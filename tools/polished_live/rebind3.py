"""Stage reviewed freeze-3 DEV receipts; install only after every replay passes.

The JSON map is a reviewed transcription plan, not producer evidence. Each item
names primary/secondary descriptors, exact manifest checks and raw assertions,
launch expectations, original target hash, roles for replay inputs, executed ROM
SHA1 and SYNTH disclosures. Assertions support JSON pointers or counted regex
matches on *bound* bytes. Completion flags alone cannot discharge a check.

Use --map PLAN --stage NEW_DIRECTORY --dry-run (or explicit --install). A
diagnostic_subset lists exactly the selected IDs; omit it to require all LIVE IDs.
Installation is a rollback transaction with atomic per-file replacement, not a
claim of multi-file power-loss atomicity. Evidence producers must be stopped.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools import verify_polished_release as verifier
from tools.polished_live.runtime import loads_json, parse_symbols

ROOT = Path(__file__).resolve().parents[2]
WITNESS = {"path", "sha256", "scope", "source_head", "code_digest"}
DOCUMENT = {"evidence_root", "source_head", "code_digest", "overlay_sha1"}


def require(condition, why):
    if not condition:
        raise ValueError(why)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def exact_path(value):
    require(
        isinstance(value, str) and value and not any(c in value for c in "*?[]"),
        "literal absolute path required",
    )
    p = Path(value)
    require(p.is_absolute(), f"relative path: {value}")
    return p.resolve()


def strict(raw, path):
    if path.suffix.lower() == ".jsonl":
        rows = [loads_json(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
        require(all(isinstance(r, dict) for r in rows), f"non-object JSONL: {path}")
        return rows
    if path.suffix.lower() == ".json":
        return loads_json(raw.decode("utf-8"))
    return raw.decode("utf-8", errors="strict") if path.suffix.lower() in {".txt", ".log"} else raw


class Snapshot:
    """Hash/parse once, then detect judge rereads racing changed evidence."""

    def __init__(self):
        self.files = {}
        self.trees = {}

    def read(self, path, expected=None):
        path = Path(path).resolve()
        if path not in self.files:
            stat = path.stat()
            raw = path.read_bytes()
            stamp = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
            self.files[path] = (raw, strict(raw, path), stamp)
        raw, parsed, _ = self.files[path]
        if expected is not None:
            require(
                re.fullmatch("[0-9a-f]{64}", expected or "") and digest(raw) == expected,
                f"hash mismatch: {path}",
            )
        return parsed

    def raw(self, path):
        self.read(path)
        return self.files[Path(path).resolve()][0]

    def tree(self, path):
        path = Path(path).resolve()
        self.trees.setdefault(path, self.inventory(path))

    @staticmethod
    def inventory(path):
        return frozenset(p.resolve() for p in path.rglob("*") if p.is_file())

    def recheck(self):
        for path, (raw, _, stamp) in self.files.items():
            stat = path.stat()
            now = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
            require(
                now == stamp and path.read_bytes() == raw,
                f"evidence changed during judgment: {path}",
            )
        for path, before in self.trees.items():
            require(self.inventory(path) == before, f"evidence closure added/removed files: {path}")


def git(root, *args):
    return (
        subprocess.check_output(["git", "-C", str(root), *args], stderr=subprocess.PIPE)
        .decode()
        .strip()
    )


def source(root, plan, snap):
    cut = plan["source_head"]
    require(re.fullmatch("[0-9a-f]{40}", cut or ""), "full execution source_head required")
    require(git(root, "rev-parse", cut + "^{commit}") == cut, "execution commit absent")
    # Metadata-only commits do not force another execution. All runtime and
    # producer bytes, including files outside the runtime digest, stay frozen.
    paths = git(
        root, "ls-tree", "-r", "--name-only", cut, "lua", "server", "data", "patch", "tools"
    ).splitlines()
    changed = git(
        root, "diff", "--name-only", cut, "--", "lua", "server", "data", "patch", "tools"
    ).splitlines()
    require(not (set(changed) - {"tools/polished_live/rebind3.py"}), f"source drift: {changed}")
    for name in paths:
        if name == "tools/polished_live/rebind3.py":
            continue  # installer cannot affect an already captured run
        snap.raw(root / name)
    current_paths = git(
        root,
        "ls-files",
        "--cached",
        "--others",
        "--exclude-standard",
        "lua",
        "server",
        "data",
        "patch",
        "tools",
    ).splitlines()
    extras = set(current_paths) - set(paths) - {"tools/polished_live/rebind3.py"}
    require(not extras, f"new production/producer files since execution: {sorted(extras)}")
    manifest = snap.read(root / verifier.MANIFEST, plan["manifest_sha256"])
    value = verifier.compute_code_digest(
        root, manifest["code_digest_files"], excludes=manifest.get("code_digest_exclude", [])
    )
    require(value == plan["code_digest"], "current runtime digest differs from execution")
    prov = snap.read(root / verifier.PROVENANCE)
    require(
        prov["output"]["sha1"] == plan["overlay_sha1"], "current overlay differs from execution"
    )
    return manifest


def bind(record, plan, snap):
    require(
        isinstance(record, dict) and set(record) == {"path", "sha256", "run_witness"},
        "artifact exact fields required",
    )
    path = exact_path(record["path"])
    snap.read(path, record["sha256"])
    w = record["run_witness"]
    require(
        isinstance(w, dict) and set(w) == WITNESS, "run_witness exact fields required (no aliases)"
    )
    wp, scope = exact_path(w["path"]), exact_path(w["scope"])
    doc = snap.read(wp, w["sha256"])
    require(
        isinstance(doc, dict) and set(doc) in (DOCUMENT, DOCUMENT | {"head"}),
        "witness document exact fields required",
    )
    require(
        exact_path(doc["evidence_root"]) == scope and path.is_relative_to(scope),
        "scope != evidence_root or artifact outside scope",
    )
    require(
        w["source_head"]
        == doc["source_head"]
        == plan["source_head"]
        == doc.get("head", plan["source_head"]),
        "witness cut mismatch",
    )
    require(
        w["code_digest"] == doc["code_digest"] == plan["code_digest"], "witness digest mismatch"
    )
    require(doc["overlay_sha1"] == plan["overlay_sha1"], "witness overlay mismatch")
    snap.tree(scope)
    return path


def pointer(value, path):
    require(isinstance(path, str) and path.startswith("/"), "nonempty JSON pointer required")
    for part in path[1:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        value = value[int(part)] if isinstance(value, list) else value[part]
    return value


SUMMARY_KEYS = {
    "ok",
    "verdict",
    "result",
    "status",
    "pass",
    "checks",
    "passed",
    "success",
    "done",
    "all_pass",
    "outcome",
    "summary",
    "complete",
    "failures",
}

# Reviewed per-criterion coverage, not scenario-wide permission. In particular,
# judge_pages does NOT prove the manifest's compound rendering/glyph criterion.
COVERAGE = {
    ("LIVE-R3-REFUSALS", "r3/refusals"): {
        "r3_aggregate": {
            "a clean overlay against an overlay contract is admitted": "rebind3.r3_aggregate: ROM/contract + hello/config",
            "a swapped contract is refused by the server": "rebind3.r3_aggregate: mismatch + named rejection",
            "every reply after the refusal is noop": "rebind3.r3_aggregate: all post-refusal commands",
            "a flipped byte $1F8028 is refused by the client": "rebind3.r3_aggregate: generated beacon + raw refusal",
        }
    },
    ("LIVE-DUO-NATURAL-FAINT", "duo/play-faint-natural"): {
        "verifier_replay": {
            "the loss setup is a disclosed HP 1 on the linked lead only, never HP 0": "duo.py:566-621",
            "a native battle loss produced exactly one faint event for the linked mon and no whiteout": "duo.py:638-670",
            "the engine copyback witness shows the linked mon at HP 0 before the client report": "duo.py:672-717",
            "the server sent exactly one force_faint for the linked partner to B and nothing else lethal": "duo.py:753-792",
            "B's cartridge shows the partner at HP 0 and no other B mon changed": "duo.py:793-841",
            "the server marks the link dead after the kill": "duo.py:842-858",
            "no reconnect or hello re-report after the stage mark": "duo.py:627-636",
        }
    },
    ("LIVE-DUO-NATURAL-WHITEOUT", "duo/play-whiteout-natural"): {
        "verifier_replay": {
            "the loss setup is a disclosed HP 1 on every non-egg record, never HP 0": "duo.py:566-621",
            "a native battle loss without Run produced a whiteout event after every party mon fainted": "duo.py:658-703",
            "the whiteout is reported with the entire party at HP 0 and before any heal": "duo.py:678-704",
            "the server sent exactly one force_faint for the linked partner to B via the per-mon faint of the linked mon": "duo.py:693-696,753-792",
            "B's cartridge shows the partner at HP 0 and no other B mon changed": "duo.py:793-841",
            "the server marks the link dead after the kill": "duo.py:842-858",
            "no reconnect or hello re-report after the stage mark": "duo.py:627-636",
        }
    },
    ("LIVE-SHIPPED-TRADE", "trade/shipped-native-commit-cold-continue"): {
        "verifier_replay": {
            "both native commits and one trade_done each": "trade_duo_live.py:158-183,191-210",
            "server re-keyed without false faint": "trade_duo_live.py:205-206,224-233",
            "cold CONTINUE preserves both received mons and identities": "trade_duo_live.py:142-155,210-219",
            "both native save copies valid and exchanged": "trade_duo_live.py:219-223,259",
        }
    },
}
VERIFIER_REPLAYS = {
    "duo/play-faint-natural",
    "duo/play-whiteout-natural",
    "panel/real-host-paging",
    "trade/shipped-native-commit-cold-continue",
}

# Necessary completion signals only; none may stand in for sibling criteria.
# R2 labels are deliberately tied to separate, explicitly bound leg outputs.
PRODUCER_COMPLETION = {
    "run verdict": (None, None, None),
    "R2a run verdict": (
        "LIVE-R2-RANDOMIZED-BOOT",
        "r2/randomized-boot-adopt-form-key",
        "producer_r2a",
    ),
    "R2b run verdict": (
        "LIVE-R2-RANDOMIZED-BOOT",
        "r2/randomized-boot-adopt-form-key",
        "producer_r2b",
    ),
    "overlay run verdict": ("LIVE-TITLE-SPLASH", "title/wordmark-vs-clean-control", None),
}


# HYBRID owner ruling: fixed exclusions, not a map-supplied classification.
# GAPS.json at ddfb3e52d: all 23 WRITE rows, plus conservative manifest audit.
WRITE_CHECKS = frozenset(
    {
        "0 Lua-originated writes and no panel write attempt",
        "B closes to the Phone list with no call placed and PANEL_STATE cleared",
        "B's cartridge shows the partner at HP 0 and no other B mon changed",
        "C4 B closes back to the Phone list with no call placed and PANEL_STATE cleared",
        "C5 every panel write is inside the mailbox span and the audit is non-vacuous",
        "a native full-party catch reached a box; the six-mon fixture was prepared by native catch and SAVE without a client or RAM party edits, hashed, and reloaded",
        "both native commits and one trade_done each",
        "both native save copies valid and exchanged",
        "box_mon: exact diff subset over declared ranges, and its control fails",
        "box_mon: party 5 -> 4 and the key leaves the party",
        "box_mon: the mon is in a gameplay box and its pokedb entry checksum verifies",
        "cold CONTINUE preserves both received mons and identities",
        "columns 8-9 of rows 0-1 are drawn by every native card",
        "force_faint: every write is inside the target's +32/+34/+35 bytes",
        "force_faint: exact diff subset, and its control fails",
        "force_faint: the bench mon's HP is 0, status 0, party count unchanged",
        "full-memory diff confined to expected write set",
        "independent recompute of the force_faint, box_mon, party_mon and both memorialize diffs equals the driver's",
        "memorialize (box origin): exact diff subset, and its control fails",
        "memorialize (box origin): memorialize_done exactly once, no memorialize_failed, from a boxed key",
        "memorialize (box origin): the census reads the key only in box 20 at the same (bank, entry), and the source membership is removed",
        "memorialize (party origin): a fresh pokedb entry equal to the party mon's own encoding, and it verifies",
        "memorialize (party origin): exact diff subset, and its control fails",
        "memorialize (party origin): memorialize_done exactly once, no memorialize_failed, and the key leaves the party",
        "memorialize (party origin): the census reads the key only in box 20 at the first empty slot",
        "native party grew by exactly one at the capture hook, before client quarantine",
        "native party grew by exactly one at the variant capture hook, before client quarantine",
        "no Lua-originated memory write",
        "observation-only leg has zero Lua writes; variant-catch quarantine writes are retained separately and do not count as write-path qualification",
        "one bound command and exact write plan",
        "one reply and exact confined partner party write",
        "party_mon: exact diff subset, and its control fails",
        "party_mon: party 4 -> 5, the key at the last slot, healed",
        "party_mon: the box slot is cleared and the pokedb entry is never freed",
        "s3 native delete still works and leaves one SLink row",
        "the client's own permit wrote the mailbox, all writes inside its span",
        "the loss setup is a disclosed HP 1 on every non-egg record, never HP 0",
        "the loss setup is a disclosed HP 1 on the linked lead only, never HP 0",
        "the write domain is recorded",
    }
)


def producer_completion(item, row, bound, snap):
    requested = [c["name"] for c in row["checks"] if c.get("covered_by") == "producer_completion"]
    if not requested:
        return set()
    require(
        any(c["name"] not in PRODUCER_COMPLETION for c in row["checks"]),
        "producer completion alone cannot support a receipt",
    )

    def passed(path):
        require(path in bound, "unbound producer completion artifact")
        raw = snap.raw(path)
        errors = verifier._producer_errors(item, raw)
        require(not errors, f"producer completion refused: {errors}")
        failures = re.findall(
            r"\[\s*FAIL\s*\]|^\s*(?:FAIL\b|RESULT\s*:\s*FAIL\b)", raw.decode("utf-8"), re.I | re.M
        )
        require(not failures, "producer completion contains FAIL check lines")

    passed(exact_path(row["primary"]["path"]))
    proven = set()
    for name in requested:
        require(name in PRODUCER_COMPLETION, "non-verdict check cannot use producer_completion")
        rid, scenario, role = PRODUCER_COMPLETION[name]
        require(
            rid is None or (item["id"], item["expect_scenario"]) == (rid, scenario),
            "producer completion label belongs to a different scenario",
        )
        if role:
            a, b = (exact_path(row["roles"][key]) for key in ("producer_r2a", "producer_r2b"))
            require(a != b, "R2 producer completions require distinct leg outputs")
            passed(exact_path(row["roles"][role]))
        proven.add(("producer_completion", name))
    return proven


def producer_checks(row, records, root, snap, used_labels):
    """HYBRID: exact observational L.check echoes from a launch-bound source.

    A primary and its tee console may each contain the same single check. They
    are alternative recordings, not two independent checks. Duplicate echoes
    within either matching recording refuse; labels cannot be reused per receipt.
    """
    proofs, labels, fingerprints = {}, set(), set()
    for check in row["checks"]:
        if check.get("covered_by") != "producer_check":
            continue
        require(
            set(check) == {"name", "covered_by", "producer_check"},
            "producer_check exact fields required",
        )
        name, label = check["name"], check["producer_check"]
        require(name not in WRITE_CHECKS, "WRITE criterion cannot use producer_check")
        require(
            name not in PRODUCER_COMPLETION and label not in PRODUCER_COMPLETION,
            "generic verdict belongs to producer_completion",
        )
        require(
            isinstance(label, str) and label.strip() and "\n" not in label and "\r" not in label,
            "producer_check requires one literal label",
        )
        require(label not in labels, "producer_check label reused for different checks")
        require(
            label not in used_labels or used_labels[label] == name,
            "producer_check label reused for different checks across receipts",
        )
        prefix = "  [ok] " + label
        matches = []
        primary = exact_path(row["primary"]["path"])
        for specification in row["launches"]:
            launch_path = exact_path(specification["path"])
            launch = snap.read(launch_path)
            witness = exact_path(records[launch_path]["run_witness"]["path"])
            outputs = {exact_path(launch["console"])}
            if exact_path(records[primary]["run_witness"]["path"]) == witness:
                outputs.add(primary)
            counts = {}
            lines_by_path = {}
            for output in sorted(outputs):
                require(output in records, "unbound producer output")
                lines = snap.raw(output).decode("utf-8").splitlines()
                lines_by_path[output] = lines
                counts[output] = sum(
                    line == prefix or line.startswith(prefix + "  -- ") for line in lines
                )
            if not any(counts.values()):
                continue
            require(
                all(n <= 1 for n in counts.values()), "producer_check label appears more than once"
            )
            require(
                not any("  [FAIL] " in line for lines in lines_by_path.values() for line in lines),
                "producer_check output contains a FAIL check",
            )
            checkout = exact_path(launch["source_checkout"])
            sources = []
            for driver in launch["driver_files"]:
                relative = exact_path(driver["path"]).relative_to(checkout)
                source_path = root / relative
                raw = source_path.read_bytes()  # fresh rehash, not only the cached source read
                require(
                    digest(raw) == driver["sha256"], "producer_check driver changed after launch"
                )
                require(
                    raw == snap.raw(source_path),
                    "producer_check driver differs from checked source",
                )
                if label.encode("utf-8") in raw:
                    sources.append((driver["sha256"], str(source_path)))
            require(sources, "producer_check literal absent from launch-hashed driver source")
            source_hash, source_path = sorted(sources)[0]
            output = (
                primary if counts.get(primary) else next(p for p in sorted(outputs) if counts[p])
            )
            matches.append(
                {
                    "driver_sha256": source_hash,
                    "driver": source_path,
                    "label": label,
                    "output": str(output),
                    "output_sha256": digest(snap.raw(output)),
                    "launch": str(launch_path),
                }
            )
        require(len(matches) == 1, "producer_check missing or ambiguous across launches")
        proof = matches[0]
        fingerprint = (proof["driver_sha256"], label)
        require(fingerprint not in fingerprints, "producer_check blanket observation")
        fingerprints.add(fingerprint)
        labels.add(label)
        used_labels[label] = name
        proofs[name] = proof
    return proofs


def has_summary(value, *, in_container=False):
    if isinstance(value, bool):
        return in_container
    if isinstance(value, str):
        return summary_text(value)
    if isinstance(value, dict):
        return any(
            str(k).casefold() in SUMMARY_KEYS or has_summary(v, in_container=True)
            for k, v in value.items()
        )
    return isinstance(value, list) and (
        not value or any(has_summary(v, in_container=True) for v in value)
    )


def summary_text(text):
    return (
        re.search(
            r"\bRESULT\s*:|\b(?:PASS|FAIL|verdict)\b|\[\s*(?:ok|FAIL)\s*\]"
            r"|^\s*(?:RESULT|VERDICT|STATUS)\b"
            r"|(?:^|[:=\-\s])(?:pass(?:ed)?|ok|success(?:ful)?|succeeded|done|complete(?:d)?|good|all\s+good|✓|✔|true)\s*$",
            text,
            re.I | re.M,
        )
        is not None
    )


def assertions(checks, expected, bound, snap, *, item, proven, producer_observations):
    require(
        isinstance(checks, list) and [c["name"] for c in checks] == expected,
        "exact ordered manifest checks required",
    )
    result, fingerprints = [], set()
    for check in checks:
        if "covered_by" in check:
            engine = check["covered_by"]
            if engine == "producer_check":
                proof = producer_observations.get(check["name"])
                require(
                    proof is not None and proof["label"] == check["producer_check"],
                    "producer_check not proved",
                )
                result.append(
                    {
                        "name": check["name"],
                        "result": "PASS",
                        "observed": {"covered_by": engine, **proof},
                    }
                )
                continue
            if engine == "producer_completion":
                approved = dict.fromkeys(
                    PRODUCER_COMPLETION,
                    "verifier._producer_errors + zero FAIL checks; final check_live required",
                )
                passed = (engine, check["name"]) in proven
            else:
                approved = COVERAGE.get((item["id"], item["expect_scenario"]), {}).get(engine, {})
                passed = engine in proven
            require(
                set(check) == {"name", "covered_by"} and check["name"] in approved and passed,
                "covered_by check not approved or replay did not pass",
            )
            result.append(
                {
                    "name": check["name"],
                    "result": "PASS",
                    "observed": {"covered_by": engine, "source": approved[check["name"]]},
                }
            )
            continue
        require(
            set(check) == {"name", "assertions"} and check["assertions"],
            "reviewed raw assertions required",
        )
        facts, observations, positive = [], set(), False
        for a in check["assertions"]:
            path = exact_path(a["artifact"])
            require(path in bound, f"unbound raw observation: {path}")
            op = a["op"]
            if op == "eq":
                require(
                    set(a) == {"artifact", "pointer", "op", "expected"}, "unknown assertion fields"
                )
                segments = [
                    s.replace("~1", "/").replace("~0", "~").casefold()
                    for s in a["pointer"].split("/")
                ]
                require(
                    not (set(segments) & SUMMARY_KEYS),
                    "generic completion is not raw proof",
                )
                observed = pointer(snap.read(path), a["pointer"])
                require(
                    not has_summary(observed) and not has_summary(a["expected"]),
                    "completion/container summary is not raw proof",
                )
                require(
                    type(observed) is type(a["expected"]) and observed == a["expected"],
                    f"raw check mismatch: {check['name']} {a['pointer']}",
                )
                observations.add(
                    (
                        str(path),
                        "value",
                        digest(
                            json.dumps(observed, sort_keys=True, separators=(",", ":")).encode()
                        ),
                    )
                )
                positive = True
            elif op == "regex_count":
                fields = {"artifact", "pattern", "op", "expected"}
                absence = a["expected"] == 0
                require(
                    set(a) == (fields | {"absence"} if absence else fields)
                    and (not absence or a["absence"] is True),
                    "zero regex count needs explicit absence:true",
                )
                require(
                    type(a["expected"]) is int and a["expected"] >= 0 and len(a["pattern"]) > 3,
                    "explicit count/pattern required",
                )
                require(
                    not any(token in a["pattern"] for token in ("(?=", "(?!", "(?<=", "(?<!")),
                    "lookaround and its backreferences cannot be raw proof",
                )
                text = snap.raw(path).decode()
                matches = list(re.finditer(a["pattern"], text, re.MULTILINE))
                for match in matches:
                    start = text.rfind("\n", 0, match.start()) + 1
                    end = text.find("\n", match.end())
                    lines = text[start : end if end >= 0 else len(text)]
                    require(
                        not summary_text(match.group()) and not summary_text(lines),
                        "matched producer completion/summary is not raw proof",
                    )
                spans = sorted({m.span() for m in matches})
                observations.add(
                    (str(path), "absence", a["pattern"])
                    if absence
                    else (str(path), "spans", json.dumps(spans))
                )
                observed = len(matches)
                require(observed == a["expected"], f"raw count mismatch: {check['name']}")
                positive = positive or observed > 0
            else:
                raise ValueError(f"unknown raw assertion operation: {op}")
            facts.append(
                {
                    "artifact": str(path),
                    "sha256": digest(snap.raw(path)),
                    "assertion": a,
                    "observed": observed,
                }
            )
        require(positive, "each raw check requires a positive observation")
        fingerprint = tuple(sorted(observations))
        require(fingerprint not in fingerprints, "blanket check replacement forbidden")
        fingerprints.add(fingerprint)
        result.append(
            {"name": check["name"], "result": "PASS", "observed": json.dumps(facts, sort_keys=True)}
        )
    return result


def launches(row, bound, records, plan, root, snap):
    specs = row.get("launches")
    require(isinstance(specs, list) and specs, "reviewed prelaunch metadata required")
    covered = set()
    for spec in specs:
        p = exact_path(spec["path"])
        require(p in bound and p.name.endswith(".LAUNCH.json"), "unbound launch")
        launch = snap.read(p)
        require(launch["schema"] == "slink-freeze3-launch-v1", "launch schema")
        for key in ("source_head", "code_digest", "overlay_sha1"):
            require(launch[key] == plan[key], f"launch {key} mismatch")
        require(
            launch["head"] == plan["source_head"] and launch["dirty_production"] == [],
            "dirty/wrong launch cut",
        )
        from tools.gen2_code_digest import head_digest

        stamp = launch["gen2_code_digest"]
        require(
            stamp["schema"] == "gen2-code-digest-v1"
            and stamp["commit"] == plan["source_head"]
            and stamp["dirty"] == []
            and stamp["digest"] == head_digest(root, plan["source_head"]),
            "invalid Gen2 launch stamp",
        )
        scope = exact_path(launch["evidence_root"])
        require(scope == exact_path(records[p]["run_witness"]["scope"]), "launch scope mismatch")
        require(
            launch["argv"] == spec["argv"] and launch["environment"] == spec["environment"],
            "unreviewed launch argv/environment",
        )
        require(launch["argv"] and isinstance(launch["environment"], dict), "missing launch config")
        for field, name in (
            ("overlay_provenance_sha256", verifier.PROVENANCE),
            ("ups_sha256", "patch/dist/SLink-Polished.ups"),
            ("requirement_manifest_sha256", verifier.MANIFEST),
            ("verifier_sha256", "tools/verify_polished_release.py"),
        ):
            require(launch[field] == digest(snap.raw(root / name)), f"launch {field} mismatch")
        for dep in launch["inputs"]:
            snap.read(exact_path(dep["path"]), dep["sha256"])
        checkout = exact_path(launch["source_checkout"])
        census = {}
        for dep in launch["driver_files"]:
            rel = exact_path(dep["path"]).relative_to(checkout)
            require(rel not in census, "duplicate driver path")
            census[rel] = dep["sha256"]
            snap.read(root / rel, dep["sha256"])
        required = {
            p.relative_to(root)
            for p in (root / "tools/polished_live").rglob("*")
            if p.is_file() and p.suffix in {".py", ".lua", ".json"} and p.name != "rebind3.py"
        }
        require(
            set(census) - {Path("tools/polished_live/rebind3.py")} == required,
            "incomplete frozen driver census",
        )
        exit_path = p.with_name(p.name.removesuffix(".LAUNCH.json") + ".exit.json")
        require(
            exit_path in bound and exact_path(launch["console"]) in bound,
            "missing bound exit/console",
        )
        end = snap.read(exit_path)
        w = records[p]["run_witness"]
        require(
            end["argv"] == launch["argv"]
            and end["launch_sha256"] == digest(snap.raw(p))
            and exact_path(end["launch"]) == p,
            "exit/launch mismatch",
        )
        require(
            end["witness_sha256"] == w["sha256"]
            and exact_path(end["witness"]) == exact_path(w["path"]),
            "exit/witness mismatch",
        )
        require(
            type(end["exit_code"]) is int
            and end["exit_code"] in spec.get("allowed_exit_codes", [0]),
            "unaccepted process exit",
        )
        require(
            launch["created_utc"] <= end["started_utc"] <= end["ended_utc"],
            "launch/exit time order",
        )
        covered.add(exact_path(w["path"]))
    require(
        covered == {exact_path(r["run_witness"]["path"]) for r in records.values()},
        "artifact job lacks its own launch",
    )


def r3_aggregate(row, bound, root, snap):
    """Positive overlay control plus three distinct named refusal executions.

    Paths point to the raw producer wire/log/write audit, never a fabricated
    accepted/refused summary. The reviewed reason is matched literally in raw
    output, and all post-rejection server replies must remain noop.
    """
    cases = row["r3_cases"]
    require(
        set(cases) == {"overlay", "swap", "flip14e", "flipbeacon"},
        "R3 needs control and all three mutants",
    )
    scopes = set()
    descriptors = {exact_path(r["path"]): r for r in [row["primary"], *row["secondary_evidence"]]}
    for name, case in cases.items():
        paths = {
            k: exact_path(case[k]) for k in ("wire", "client_log", "writes", "rom", "contract")
        }
        require(set(paths.values()) <= bound, f"R3 {name}: unbound wire/log/write audit")
        scope = exact_path(descriptors[paths["client_log"]]["run_witness"]["scope"])
        require(scope not in scopes, "R3 cases must be fresh distinct job allocations")
        scopes.add(scope)
        audit = snap.read(paths["writes"])
        require(
            isinstance(audit, list) or isinstance(audit, dict) and not audit,
            f"R3 {name}: invalid Lua write audit (only an array or empty object)",
        )
        require(len(audit) == 0, f"R3 {name}: Lua writes not zero")
        wire = snap.read(paths["wire"])
        require(isinstance(wire, list), "R3 raw wire must be JSONL")
        messages = [r["msg"] for r in wire if r.get("dir") == "s2c"]
        commands = [c for msg in messages for c in msg.get("commands", [])]
        hello = [
            r["msg"]
            for r in wire
            if r.get("dir") == "c2s" and r.get("msg", {}).get("event") == "hello"
        ]
        rom_sha1 = hashlib.sha1(snap.raw(paths["rom"])).hexdigest()
        pinned = snap.read(paths["contract"])["players"][case["player"]]["rom_sha1"]
        if name != "flipbeacon":
            require(
                hello and all(h.get("rom_sha1") == rom_sha1 for h in hello),
                f"R3 {name}: executed ROM not witnessed",
            )
        accepted = any(c.get("cmd") == "config" for c in commands)
        rejected = [c for c in commands if c.get("cmd") == "identity_error" or c.get("refused")]
        if name == "overlay":
            require(
                rom_sha1 == pinned == verifier.overlay_sha1(root) == row["rom_sha1_executed"],
                "R3 overlay/contract binding",
            )
            require(
                len(hello) == 1 and accepted and not rejected, "R3 overlay control not admitted"
            )
        else:
            if name != "flipbeacon":
                require(
                    rom_sha1 != pinned, "R3 server control did not exercise a contract mismatch"
                )
            require(not accepted, f"R3 {name}: mutant accepted")
            reason = case["reason"]
            require(
                isinstance(reason, str) and len(reason.strip()) > 8 and "PASS" not in reason,
                "R3 named refusal reason required",
            )
            if name == "flipbeacon":
                refusal_path = exact_path(case["refusal"])
                require(refusal_path in bound, "R3 client refusal artifact missing")
                refusal = snap.read(refusal_path)
                require(
                    refusal["schema"] == "polished-live-refusal-v1"
                    and refusal["refused_by"] == "client"
                    and isinstance(refusal["reason"], str)
                    and reason in refusal["reason"]
                    and type(refusal["frame"]) is int
                    and refusal["frame"] >= 0
                    and type(refusal["lua_writes"]) is int
                    and refusal["lua_writes"] == 0,
                    "R3 invalid client refusal/audit",
                )
                require(
                    not hello and reason in snap.raw(paths["client_log"]).decode(),
                    "R3 beacon: missing named client refusal / hello sent",
                )
            else:
                reason_path = exact_path(case["refusal_log"])
                require(reason_path in bound, "R3 unbound named refusal log")
                require(
                    hello and rejected and reason in snap.raw(reason_path).decode(),
                    f"R3 {name}: wrong/missing refusal reason",
                )
                refused_at = next(
                    i
                    for i, msg in enumerate(messages)
                    if any(c in rejected for c in msg.get("commands", []))
                )
                later = messages[refused_at + 1 :]
                require(
                    later
                    and all(
                        msg.get("commands") and all(c.get("cmd") == "noop" for c in msg["commands"])
                        for msg in later
                    ),
                    "R3 post-refusal replies not noop",
                )
    mutations = exact_path(row["roles"]["mutations"])
    require(mutations in bound, "R3 mutation provenance unbound")
    doc = snap.read(mutations)
    beacon_path = root / "data/games/polished_crystal/overlay/beacon.json"
    beacon = snap.read(beacon_path)
    require(doc["beacon_sha256"] == digest(snap.raw(beacon_path)), "R3 wrong generated beacon")
    offsets = {}
    source_path = exact_path(doc["source"])
    require(source_path in bound, "R3 unbound original cartridge")
    before = snap.raw(source_path)
    require(hashlib.sha1(before).hexdigest() == doc["source_sha1"], "R3 source SHA1 mismatch")
    for record in doc["mutations"]:
        path = exact_path(record["path"])
        require(path in bound, "R3 unbound mutant cartridge")
        after = snap.raw(path)
        offset = record["offset"]
        require(
            len(before) == len(after)
            and [i for i, (a, z) in enumerate(zip(before, after, strict=True)) if a != z]
            == [offset]
            and before[offset] == record["old"]
            and after[offset] == record["new"]
            and hashlib.sha1(after).hexdigest() == record["sha1"],
            "R3 mutation not byte-exact",
        )
        offsets[path.stem] = offset
        require(
            exact_path(cases[path.stem]["rom"]) == path, "R3 mutation not the executed cartridge"
        )
    require(
        set(offsets) == {"flip14e", "flipbeacon"} and offsets["flip14e"] == 0x14E,
        "R3 mutation census",
    )
    spans = beacon["spans"]

    def covered(offset):
        return any(s["offset"] <= offset < s["offset"] + s["length"] for s in spans)

    require(
        covered(offsets["flipbeacon"]) and not covered(offsets["flip14e"]),
        "R3 mutation doesn't measure generated beacon boundary",
    )


def replay_write_diffs(trace, reviewed, root, snap):
    """Recompute writes.lua record_diff bytes, never trace.checks or claimed counts.

    The producer has no offline API. Its three image regions are specified by
    writes.lua snapshot()/record_diff(); addresses come from the frozen symbols.
    Allowed addresses must also be explicitly approved in the binding map.
    """
    require(trace["rom_sha1"] == verifier.overlay_sha1(root), "writes ROM binding")
    operations = trace["operations"]
    require(set(operations) == set(reviewed) == set("abdef"), "writes need all five raw operations")
    symbols = parse_symbols(
        snap.raw(root / "data/polished/polished_slink.sym").decode(), reject_duplicates=True
    )
    start = symbols["wPartyCount"][1]
    end = symbols["wPartyMonNicknames"][1] + 6 * 11
    allocation = set()
    for name in ("wPokeDB1UsedEntries", "wPokeDB2UsedEntries"):
        bank, address = symbols[name]
        allocation.update(
            range(bank * 0x1000 + address - 0xD000, bank * 0x1000 + address - 0xD000 + 26)
        )

    def image(doc):
        require(
            set(doc) == {"cart_hex", "party_start", "party_hex", "allocation"},
            "writes image schema",
        )
        cart, party = bytes.fromhex(doc["cart_hex"]), bytes.fromhex(doc["party_hex"])
        require(
            len(cart) == 32768 and len(party) == end - start and doc["party_start"] == start,
            "writes snapshot coverage differs from frozen symbols",
        )
        alloc = doc["allocation"]
        require(
            isinstance(alloc, dict)
            and set(alloc) == {str(a) for a in allocation}
            and all(type(v) is int and 0 <= v <= 255 for v in alloc.values()),
            "writes allocation coverage",
        )
        return {
            **{f"cart@{a}": v for a, v in enumerate(cart)},
            **{f"party@{start + a}": v for a, v in enumerate(party)},
            **{f"alloc@{a}": v for a, v in alloc.items()},
        }

    facts = {}
    for leg, op in operations.items():
        before, after = image(op["before"]), image(op["after"])
        observed = {key for key in before if before[key] != after[key]}
        claimed = op["changed"]
        require(
            isinstance(claimed, list)
            and all(set(c) == {"kind", "a"} and type(c["a"]) is int for c in claimed),
            "writes changed-address schema",
        )
        claimed_set = {f"{c['kind']}@{c['a']}" for c in claimed}
        allowed = reviewed[leg]
        require(
            isinstance(allowed, list)
            and allowed
            and all(isinstance(a, str) for a in allowed)
            and len(set(allowed)) == len(allowed)
            and set(allowed) <= before.keys(),
            "writes reviewed address set",
        )
        require(
            isinstance(op["expected"], list)
            and len(set(op["expected"])) == len(op["expected"])
            and set(op["expected"]) == set(allowed),
            "writes producer allowance differs from reviewed addresses",
        )
        require(
            observed
            and len(claimed_set) == len(claimed)
            and observed == claimed_set
            and observed <= set(allowed),
            f"writes {leg}: recomputed diff disagrees with claim/allowance",
        )
        if leg == "a":
            # Only operation a exposes the complete write tap in the current
            # producer. Other legs expose full images, not a fabricated tap.
            writes = op["writes"]
            require(isinstance(writes, list) and writes, "writes a: missing raw write operations")
            replayed, last_frame = before.copy(), -1
            for write in writes:
                require(
                    write["op"] == "write_u8"
                    and type(write["addr"]) is int
                    and type(write["v"]) is int
                    and 0 <= write["v"] <= 255
                    and type(write["frame"]) is int
                    and write["frame"] >= last_frame,
                    "writes a: invalid/unordered write tap",
                )
                addr, domain = write["addr"], write["domain"]
                if domain == "CartRAM":
                    key = f"cart@{addr}"
                elif domain == "System Bus":
                    key = f"party@{addr}"
                elif domain == "WRAM":
                    key = f"alloc@{addr}" if addr in allocation else f"party@{addr + 0xC000}"
                else:
                    raise ValueError("writes a: unmeasured write domain")
                require(key in allowed, "writes a: raw write outside reviewed address set")
                replayed[key] = write["v"]
                last_frame = write["frame"]
            require(replayed == after, "writes a: tap does not reproduce postimage")
        facts[leg] = {"changed_bytes": len(observed), "addresses": sorted(observed)}
    return facts


def replay_extra(item, row, bound, root, snap):
    """Reuse producer oracles absent from the verifier's dispatch; no copied labels."""

    def role(name):
        path = exact_path(row["roles"][name])
        require(path in bound, f"missing replay role: {name}")
        return snap.read(path)

    primary = snap.read(exact_path(row["primary"]["path"]))
    rid = item["id"]
    if item.get("bind") == "source" and rid != "LIVE-R3-REFUSALS":
        bindings = row["rom_bindings"]
        require(
            set(bindings) == {"a", "b"},
            "source-bound run needs both executed cartridge/contract bindings",
        )
        executed = []
        for player, binding in bindings.items():
            rom, contract, observation = (
                exact_path(binding[k]) for k in ("rom", "contract", "observation")
            )
            require(
                {rom, contract, observation} <= bound, "unbound randomized ROM/contract/observation"
            )
            actual = hashlib.sha1(snap.raw(rom)).hexdigest()
            require(
                snap.read(contract)["players"][player]["rom_sha1"] == actual,
                "randomized ROM/contract mismatch",
            )
            require(
                pointer(snap.read(observation), binding["sha1_pointer"]) == actual,
                "executed randomized SHA1 differs from observed cartridge",
            )
            executed.append(actual)
        require(
            row["rom_sha1_executed"] in executed,
            "receipt substituted source overlay for executed randomized SHA1",
        )
    if rid == "LIVE-WRITES-OVERWORLD":
        return {"write_diffs": replay_write_diffs(role("trace"), row["write_expected"], root, snap)}
    if rid == "LIVE-CAPTURE-PARTY-ONLY":
        from lupa import LuaRuntime

        contract = snap.read(root / "tools/polished_live/capture_contract.json")
        require(
            contract["failed_throw_check"] in item["expect_checks"],
            "capture contract/manifest disagreement",
        )
        lua = LuaRuntime(unpack_returned_tuples=True)
        lua.globals().POL_ORACLE_ONLY = True
        oracle = lua.execute(snap.raw(root / "tools/polished_live/live.lua").decode())
        throws = [
            {"caught": caught == "true", "cap_hits": int(hits)}
            for caught, hits in re.findall(
                r"throw \d+ at frame \d+: caught (true|false) to_pc (?:true|false) capture-site bank-3 hits (\d+)",
                primary,
            )
        ]
        actual = oracle.evaluate_failed_throws(lua.table_from(throws, recursive=True))
        negative = role("negative")
        require(
            actual.ok
            and all(negative[k] == actual[k] for k in ("failed_throw_count", "capture_hits", "ok")),
            "failed-throw raw replay refused",
        )
    elif rid.startswith("LIVE-EXPLODE-"):
        from tools.polished_live.explode_live import evaluate

        require(
            primary["config"]["case"] == item["expect_scenario"].split("/")[-1],
            "mechanics case substitution",
        )
        require(
            primary["config"]["provenance"]["rom_sha1"] == verifier.overlay_sha1(root),
            "mechanics ROM provenance differs from current overlay",
        )
        result = evaluate(role("trace"), primary["config"])
        require(result[0] is True, f"mechanics replay refused: {result}")
    elif rid == "LIVE-RIVAL-SWAP":
        from tools.polished_live.rival_swap_live import evaluate

        require(role("input") == primary["manifest"], "rival input/manifest disagreement")
        result = evaluate(role("trace"), role("wire"), role("partner"), role("input"))
        require(result[0] == "PASS", f"rival replay refused: {result}")
    elif rid == "LIVE-PANEL-HOST-PAGING":
        from tools.polished_live.panel_host_live import evaluate

        panel = role("panel")
        result = evaluate(
            panel,
            role("links"),
            role("status"),
            role("wire"),
            verifier.overlay_sha1(root),
            lua_pass="RESULT: PASS" in role("result"),
            lines_per_page=panel["lines_per_page"],
        )
        require(result["verdict"] == "PASS", f"host replay refused: {result['why']}")
        require(
            primary["server_rows"] == result["server_rows"], "host summary/actual rows disagree"
        )
    elif rid == "LIVE-TITLE-SPLASH":
        from tools.polished_live.title_check import CLEAN_SHA1, expand_version, judge_boot

        overlay, clean = role("overlay"), role("clean")
        require(
            hashlib.sha1(overlay).hexdigest() == verifier.overlay_sha1(root)
            and hashlib.sha1(clean).hexdigest() == CLEAN_SHA1,
            "title ROM binding",
        )
        art = snap.raw(root / "patch/gen2/src/title_logo_crystal.2bpp")
        require(overlay[0x1F984A:0x1F996A] == art, "title art binding")
        rows = [
            bytes(int(v, 16) for v in re.findall(r"\$([0-9A-F]{2})", line))
            for line in snap.raw(root / "patch/gen2/src/title_rows_crystal.inc")
            .decode()
            .splitlines()
            if line.strip().startswith("db ")
        ]
        field, ngrams = overlay[0x1F8170:0x1F817C], {}
        for char in set(field):
            if 0x0A <= char < 0x52:
                entry = 0x3BCE + char - 0x0A
                start = entry + overlay[entry]
                ngrams[char] = overlay[start : overlay.index(0x53, start, start + 20)]
        version = expand_version(field, ngrams)
        for kind in ("overlay", "clean"):
            boot = primary["runs"][kind]
            for name in ("entrance", "settled"):
                require(
                    exact_path(boot["snapshots"][name]["screenshot"]) in bound,
                    "unbound title screenshot",
                )
            errors = judge_boot(boot, art, rows, version)
            require(
                not errors if kind == "overlay" else any("tile IDs differ" in e for e in errors),
                f"title {kind} replay refused: {errors}",
            )
    elif rid == "LIVE-R3-REFUSALS":
        r3_aggregate(row, bound, root, snap)
        # The historical literal is covered only if it is the byte just proved.
        mutation_doc = role("mutations")
        beacon_offsets = [
            r["offset"] for r in mutation_doc["mutations"] if Path(r["path"]).stem == "flipbeacon"
        ]
        return {"r3_aggregate": True, "beacon_offset": beacon_offsets[0]}
    # Duo/host/trade are replayed by check_live, including the hashed trade closure.
    # Remaining IDs must satisfy every explicit reviewed raw assertion above.


class InstallFailure(Exception):
    def __init__(self, cause, unrecovered, recovery_files):
        super().__init__(str(cause))
        self.unrecovered = unrecovered
        self.recovery_files = recovery_files


def install_all(candidates):
    """Sole writer: reserve every recovery copy before the first atomic replacement."""
    installed, recovery, unrecovered = [], {}, []

    def remaining():
        return [str(p) for p in recovery.values() if p.exists()]

    def cleanup():
        # A cleanup failure retains a recovery file; it never hides an install or
        # rollback result and never deletes somebody else's old recovery file.
        for path in recovery.values():
            with suppress(OSError):
                path.unlink(missing_ok=True)

    try:
        for target, staged, old, expected, judged in candidates:
            require(
                digest(target.read_bytes()) == expected, f"target changed before install: {target}"
            )
            require(digest(staged.read_bytes()) == judged, f"judged candidate changed: {staged}")
            fd, name = tempfile.mkstemp(
                prefix=target.name + ".", suffix=".rebind3-recovery", dir=target.parent
            )
            recovery[target] = Path(name)
            with os.fdopen(fd, "wb") as stream:
                stream.write(old)
                stream.flush()
                os.fsync(stream.fileno())
            require(
                digest(recovery[target].read_bytes()) == expected,
                f"recovery verification failed: {target}",
            )
        for target, staged, _old, expected, judged in candidates:
            require(
                digest(target.read_bytes()) == expected, f"target changed during install: {target}"
            )
            require(
                digest(staged.read_bytes()) == judged,
                f"judged candidate changed before replace: {staged}",
            )
            installed.append((target, expected, judged))
            os.replace(staged, target)
    except BaseException as error:
        for target, expected, judged in reversed(installed):
            try:
                require(
                    digest(target.read_bytes()) in {expected, judged},
                    f"foreign edit prevents rollback: {target}",
                )
                require(
                    digest(recovery[target].read_bytes()) == expected, f"recovery changed: {target}"
                )
                if digest(target.read_bytes()) != expected:
                    os.replace(recovery[target], target)
                require(
                    digest(target.read_bytes()) == expected,
                    f"rollback verification failed: {target}",
                )
            except BaseException as rollback_error:
                unrecovered.append(
                    {
                        "target": str(target),
                        "recovery": str(recovery[target]),
                        "reason": str(rollback_error),
                    }
                )
        if not unrecovered:
            cleanup()
        raise InstallFailure(error, unrecovered, remaining()) from error
    cleanup()
    return {"recovery_files": remaining()}


def run(root, map_path, stage_dir, *, install=False):
    report = {"ok": False, "installed": False, "partial": False, "rows": [], "errors": []}
    selected, rid = [], None
    try:
        root, stage = Path(root).resolve(), Path(stage_dir).resolve()
        require(not stage.is_relative_to(root), "stage must be outside repository/receipt targets")
        require(not stage.exists(), "staging directory already exists")
        snap = Snapshot()
        plan = snap.read(Path(map_path))
        require(
            plan["schema"] == "polished-rebind3-map-v1" and plan["reviewed_by"].strip(),
            "reviewed map required",
        )
        manifest = source(root, plan, snap)
        live = {i["id"]: i for i in manifest["items"] if i["kind"] == "LIVE"}
        selected = plan.get("diagnostic_subset", list(live))
        require(
            isinstance(selected, list) and selected and len(selected) == len(set(selected)),
            "invalid selection",
        )
        require(set(plan["items"]) == set(selected) <= set(live), "missing/unknown LIVE id")
        report["partial"] = "diagnostic_subset" in plan
        candidates, all_records, producer_labels = [], {}, {}
        stage.mkdir(parents=True)
        for rid in selected:
            item, row = live[rid], plan["items"][rid]
            target = (root / item["receipt"]).resolve()
            require(
                target.is_relative_to(root / manifest["receipts_dir"]),
                "target escapes receipts directory",
            )
            old = target.read_bytes()
            require(digest(old) == row["target_sha256"], f"target hash changed: {rid}")
            require(
                row["checks_reviewed"] is True and row.get("grade", "DEV") == "DEV",
                "DEV/reviewed checks only",
            )
            records = {}
            for record in [row["primary"], *row["secondary_evidence"]]:
                p = bind(record, plan, snap)
                require(p not in records, f"duplicate artifact: {p}")
                require(
                    p not in all_records or all_records[p] == record,
                    f"contradictory artifact binding: {p}",
                )
                records[p] = record
                all_records[p] = record
            require(
                not any(stage.is_relative_to(p) for p in snap.trees),
                "stage overlaps evidence scope",
            )
            bound = set(records)
            launches(row, bound, records, plan, root, snap)
            producer_observations = producer_checks(row, records, root, snap, producer_labels)
            replay_facts = replay_extra(item, row, bound, root, snap)
            proven = set()
            if isinstance(replay_facts, dict) and replay_facts.get("r3_aggregate") is True:
                proven.add("r3_aggregate")
                for check in row["checks"]:
                    literal = re.search(r"\$([0-9A-Fa-f]+)", check["name"])
                    if check.get("covered_by") == "r3_aggregate" and literal:
                        require(
                            replay_facts["beacon_offset"] == int(literal[1], 16),
                            "covered beacon criterion no longer matches generated byte",
                        )
            if item["expect_scenario"] in VERIFIER_REPLAYS:
                snap.recheck()
                errors = verifier._replay_errors(
                    item,
                    snap.raw(exact_path(row["primary"]["path"])),
                    exact_path(row["primary"]["path"]),
                    {p: snap.raw(p) for p in bound if p != exact_path(row["primary"]["path"])},
                )
                snap.recheck()
                require(not errors, f"verifier replay refused: {errors}")
                proven.add("verifier_replay")
            proven.update(producer_completion(item, row, bound, snap))
            checks = assertions(
                row["checks"],
                item["expect_checks"],
                bound,
                snap,
                item=item,
                proven=proven,
                producer_observations=producer_observations,
            )
            primary = row["primary"]
            doc = {
                "schema": verifier.RECEIPT_SCHEMA,
                "grade": "DEV",
                "scenario_id": item["expect_scenario"],
                "date": datetime.now(UTC).date().isoformat(),
                "lane": str(exact_path(primary["run_witness"]["scope"])),
                "evidence_file": primary["path"],
                "evidence_sha256": primary["sha256"],
                "run_witness": primary["run_witness"],
                "secondary_evidence": row["secondary_evidence"],
                "checks": checks,
                "code_digest": plan["code_digest"],
                "rom_sha1_executed": row["rom_sha1_executed"],
                "source_overlay_sha1": plan["overlay_sha1"],
                "overlay_provenance_sha256": digest(snap.raw(root / verifier.PROVENANCE)),
                "synth": row["synth"],
                "not_proven": row.get("not_proven", []),
            }
            staged = stage / (rid + ".json")
            newline = "\r\n" if b"\r\n" in old else "\n"
            staged.write_bytes((json.dumps(doc, indent=2) + "\n").replace("\n", newline).encode())
            snap.read(staged)
            snap.recheck()
            judged = verifier.check_live(
                dict(item, receipt=str(staged)), root, plan["overlay_sha1"], plan["code_digest"]
            )
            snap.recheck()
            report["rows"].append(
                {
                    "id": rid,
                    "status": judged.status,
                    "reasons": judged.reasons,
                    "candidate": str(staged),
                    "target": str(target),
                    "sha256": digest(staged.read_bytes()),
                    "checks": checks,
                    "bindings": list(records.values()),
                    "replay": replay_facts,
                }
            )
            require(judged.ok, f"{rid}: {judged.reasons}")
            candidates.append((target, staged, old, row["target_sha256"], digest(snap.raw(staged))))
        source(root, plan, Snapshot())
        snap.recheck()
        if install:
            # Same-volume atomic replaces. Reject cross-volume staging rather than
            # falling back to a non-atomic copy or writing unchecked target temps.
            require(
                all(t.stat().st_dev == s.stat().st_dev for t, s, _, _, _ in candidates),
                "stage must share target filesystem",
            )
            report.update(install_all(candidates) or {})
            report["installed"] = True
        report["ok"] = True
    except Exception as exc:  # fail closed, including malformed producer payloads
        if isinstance(exc, InstallFailure):
            report["unrecovered"] = exc.unrecovered
            report["recovery_files"] = exc.recovery_files
            report["rollback_complete"] = not exc.unrecovered
            if exc.unrecovered:
                report["partial"] = True
        report["errors"].append(f"{type(exc).__name__}: {exc}")
        recorded = {r["id"] for r in report["rows"]}
        for item_id in selected:
            if item_id not in recorded:
                report["rows"].append(
                    {
                        "id": item_id,
                        "status": "FAIL" if item_id == rid else "NOT_RUN",
                        "reasons": report["errors"]
                        if item_id == rid
                        else ["transaction refused before this candidate"],
                    }
                )
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", type=Path, default=ROOT)
    ap.add_argument("--map", type=Path, required=True)
    ap.add_argument("--stage", type=Path, required=True)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--install", action="store_true")
    args = ap.parse_args(argv)
    if args.root.resolve() != ROOT:
        ap.error(
            "run this binder from the receipt checkout; --root must match the imported frozen oracles"
        )
    report = run(args.root, args.map, args.stage, install=args.install)
    print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
