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
from datetime import UTC, datetime
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools import verify_polished_release as verifier
from tools.polished_live.runtime import loads_json

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


def assertions(checks, expected, bound, snap):
    require(
        isinstance(checks, list) and [c["name"] for c in checks] == expected,
        "exact ordered manifest checks required",
    )
    result, fingerprints = [], set()
    for check in checks:
        require(
            set(check) == {"name", "assertions"} and check["assertions"],
            "reviewed raw assertions required",
        )
        facts = []
        for a in check["assertions"]:
            path = exact_path(a["artifact"])
            require(path in bound, f"unbound raw observation: {path}")
            op = a["op"]
            if op == "eq":
                require(
                    set(a) == {"artifact", "pointer", "op", "expected"}, "unknown assertion fields"
                )
                key = a["pointer"].rsplit("/", 1)[-1].lower()
                require(
                    key not in {"ok", "verdict", "result", "pass", "status", "checks"},
                    "generic completion is not raw proof",
                )
                observed = pointer(snap.read(path), a["pointer"])
                require(
                    type(observed) is type(a["expected"]) and observed == a["expected"],
                    f"raw check mismatch: {check['name']} {a['pointer']}",
                )
            elif op == "regex_count":
                require(set(a) == {"artifact", "pattern", "op", "expected"}, "unknown regex fields")
                require(
                    not re.search(r"PASS|\[ok\]|verdict", a["pattern"], re.I),
                    "generic PASS regex forbidden",
                )
                require(
                    type(a["expected"]) is int and a["expected"] >= 0 and len(a["pattern"]) > 3,
                    "explicit count/pattern required",
                )
                observed = len(re.findall(a["pattern"], snap.raw(path).decode(), re.MULTILINE))
                require(observed == a["expected"], f"raw count mismatch: {check['name']}")
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
        fingerprint = json.dumps(check["assertions"], sort_keys=True)
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
    # Duo/host/trade are replayed by check_live, including the hashed trade closure.
    # Remaining IDs must satisfy every explicit reviewed raw assertion above.


def install_all(candidates):
    """All prepared first; per-target compare-and-replace, undo owned writes on error."""
    installed = []
    try:
        for target, _staged, _old, expected in candidates:
            require(
                digest(target.read_bytes()) == expected, f"target changed before install: {target}"
            )
        for target, staged, old, expected in candidates:
            require(
                digest(target.read_bytes()) == expected, f"target changed during install: {target}"
            )
            owned = digest(staged.read_bytes())
            os.replace(staged, target)
            installed.append((target, old, owned))
    except BaseException:
        for target, old, owned in reversed(installed):
            require(
                digest(target.read_bytes()) == owned, f"foreign edit prevents rollback: {target}"
            )
            # Recovery bytes are prepared in the target filesystem; no cross-volume rename.
            rollback = target.with_name(target.name + ".rebind3-rollback")
            with rollback.open("xb") as stream:
                stream.write(old)
            os.replace(rollback, target)
        raise


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
        candidates, all_records = [], {}
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
            checks = assertions(row["checks"], item["expect_checks"], bound, snap)
            replay_extra(item, row, bound, root, snap)
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
                }
            )
            require(judged.ok, f"{rid}: {judged.reasons}")
            candidates.append((target, staged, old, row["target_sha256"]))
        source(root, plan, Snapshot())
        snap.recheck()
        if install:
            # Same-volume atomic replaces. Reject cross-volume staging rather than
            # falling back to a non-atomic copy or writing unchecked target temps.
            require(
                all(t.stat().st_dev == s.stat().st_dev for t, s, _, _ in candidates),
                "stage must share target filesystem",
            )
            install_all(candidates)
            report["installed"] = True
        report["ok"] = True
    except Exception as exc:  # fail closed, including malformed producer payloads
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
