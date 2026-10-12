#!/usr/bin/env python3
"""Polished Crystal (gen2_polished) release-candidate verifier — the SOLO lane.

`tools/verify_gen2_release.py` is the Gen 2 release manifest and must stay exactly as it is:
`tests/unit/test_polished_release_guards.py::test_release_verifier_has_no_polished_title`
pins its `TITLES` to ("crystal", "gold", "silver") and
`::test_release_verifier_duo_pairs_need_no_polished_partner` pins DUO_PAIRS to vanilla pairs.
That is correct: Polished Crystal is a SOLO title. It has no vanilla pairing, so there is no
duo matrix, no second fixture and no per-title live-gate set to extend. Appending it to TITLES
would put a nonexistent partner in every pairing.

So this module is the separate lane: a manifest (`tests/polished_release_requirements.json`)
plus a runner that reads or executes every item and prints one row each. It never edits the Gen
2 manifest and it never claims a duo result.

    python tools/verify_polished_release.py            # run every item, print the table
    python tools/verify_polished_release.py --list     # ids, kinds and one line each; not a verdict
    python tools/verify_polished_release.py --only LIVE-PHONE-ENTRY   # PARTIAL: exit 3 when green
    python tools/verify_polished_release.py --print-code-digest       # the digest receipts must carry
    python tools/verify_polished_release.py --json     # machine-readable rows
    python tools/verify_polished_release.py --manifest PATH --root DIR

Fail-closed rules, all of them load-bearing:

  * A SOURCE/BUILD item is a command. It passes only on exit 0. A timeout, a traceback and a
    "stale or missing" line are all failures, and the tool's own last words are quoted so a
    Windows/toolchain error is never mistaken for byte drift.
  * A MODEL item is one pytest subprocess PER FILE. A file passes only when pytest exits 0 AND
    reports at least one pass AND reports no skip, xfail, xpass, deselection or error. A test
    that did not run is not a test that passed. (release_lanes._count_outcomes does the parsing,
    so the counting is the shared one, not a second convention.)
  * A LIVE item is a committed receipt, never a live re-run: this machine has no emulator. The
    receipt must exist, satisfy the schema, record DEV grade (a receipt may not promote itself
    to PHYSICAL — that is an owner act), carry the item's `expect_scenario`, show every name in
    the item's `expect_checks` with result PASS (an INFO-only receipt is red), record the sha256
    of the committed overlay provenance, carry a `code_digest` equal to the one this verifier
    COMPUTES from the manifest's `code_digest_files` (LF-normalised), and bind a ROM sha1. If that sha1 is not the overlay
    sha1 the CURRENT `data/polished/overlay_provenance.json` publishes, the item is STALE and
    fails, printing both hashes. The phone-card commit moved the overlay from 29ea04c2 to
    34942315, so every earlier receipt is stale until it is re-run: that is the gate working.
  * Every primary/secondary artifact and every run witness is re-hashed. Literal absolute
    paths and exact binding fields are required; a witness's optional head must equal its
    source_head. Supporting artifacts must have distinct canonical paths; the primary cannot
    match a supporting artifact or witness by content hash. Independent empty audits may
    match by content and run-witness references may repeat. Missing, corrupt or contradictory
    supporting evidence fails; a receipt cannot vouch for itself. Capture evidence must
    be text with the failed-throw label and its bound raw throws must pass the oracle;
    title/R1 controls must be JSON. Other supported oracles replay retained observations.
    This never launches an emulator. The digest includes the entire Gen 2 Lua tree, shared
    panel/core, ROM scan/encounter presentation and runtime pack tree. Only pack *.md/*.txt
    prose may be excluded. A missing declared input is red; widening scope requires a fresh
    execution, never a receipt restamp.
  * A MANAGER item is an import and an assertion against the live server modules. The jar pin is
    compared both to `data/upr_jars.json` and, when the jar file is reachable, to its own bytes.
  * A RELEASE item builds with companion patches and must pass ZIP hygiene. Its Polished UPS
    must be present and hash to the SHA256 published in the current overlay provenance.
  * An OPEN item fails while it is open, always (`blocking_rc` is not a bypass: anything but
    true is a manifest error). Closing one requires `closed_by` to name a LIVE item that exists
    and PASSes in the same run.
  * The manifest's `required_ids` is the obligation census: every listed id must exist, and an id
    that is not listed is refused until it is added there on purpose.
  * A partial run (--only, --no-release) prints PARTIAL RUN: NOT A RELEASE VERDICT and exits 3
    when green (1 when red); --json says `complete: false`.
  * An OPEN item that names a Manager option must still be refused by that option. If someone
    flips the Manager row to `ok` while the item reads OPEN, the two disagree and the item fails.

Exit code 0 means every item ran and every item passed, in a COMPLETE run. 3 is a green partial
run, which is never a verdict. Anything else is 1 (or 2 for an unreadable manifest under --list).
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib
import io
import json
import re
import subprocess
import sys
import tempfile
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
_PY = sys.executable

MANIFEST = "tests/polished_release_requirements.json"
PROVENANCE = "data/polished/overlay_provenance.json"
RECEIPT_SCHEMA = "polished-live-receipt-v1"
MANIFEST_SCHEMA = "polished-release-requirements-v1"
# The only grade a receipt in this schema may carry. PHYsical promotion is the owner's act and
# is recorded elsewhere; a lane author cannot mark their own DEV run as a release receipt.
ALLOWED_GRADES = frozenset({"DEV"})
KINDS = ("SOURCE", "BUILD", "MODEL", "LIVE", "MANAGER", "RELEASE", "OPEN")

# The pinned forms jar. Not reachable on a host is a FAIL: the byte pin cannot be verified there.
# Patches 0016-0018 declare these classes; 0020/0021 harden the same handler.
_POLISHED_JAR_ENTRIES = (
    "com/dabomstew/pkrandom/config/polished_offsets.ini",
    "com/dabomstew/pkrandom/constants/PolishedConstants.class",
    "com/dabomstew/pkrandom/romhandlers/PolishedCrystalRomHandler.class",
    "com/dabomstew/pkrandom/romhandlers/PolishedCrystalRomHandler$Factory.class",
    "com/dabomstew/pkrandom/romhandlers/PolishedCrystalRomHandler$RomEntry.class",
    "com/dabomstew/pkrandom/romhandlers/PolishedCrystalRomHandler$Slot.class",
    "com/dabomstew/pkrandom/romhandlers/PolishedCrystalRomHandler$TrainerRecord.class",
)

_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")

# Each SOURCE/BUILD command's own budget. A clean ROM rebuild is a full rgbds build; 1800 s is
# the ceiling a Windows toolchain needs here, and exceeding it is a failure, never a pass.
DEFAULT_TIMEOUT = 1800
MODEL_TIMEOUT = 900
RELEASE_TIMEOUT = 900
# Detail cells quote this many trailing non-empty output lines: enough to name the drift, short
# enough that the table stays readable.
_TAIL_LINES = 2


# ────────────────────────────────────────────────────────────── rows


@dataclass
class Row:
    """One manifest item's verdict. `status` is PASS or FAIL; FAIL always carries `reasons`."""

    id: str
    kind: str
    status: str
    detail: str = ""
    reasons: list[str] = field(default_factory=list)

    def fail(self, reason: str) -> Row:
        self.status = "FAIL"
        self.reasons.append(reason)
        return self

    @property
    def ok(self) -> bool:
        return self.status == "PASS"

    def as_dict(self) -> dict:
        return {"id": self.id, "kind": self.kind, "status": self.status,
                "detail": self.detail, "reasons": self.reasons}


# ────────────────────────────────────────────────────────────── small helpers


def _tail(text: str, lines: int = _TAIL_LINES) -> str:
    kept = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    return " | ".join(kept[-lines:]) if kept else ""


def overlay_sha1(root: Path) -> str | None:
    """The overlay sha1 the CURRENT provenance publishes. This is what a receipt must name."""
    try:
        doc = json.loads((root / PROVENANCE).read_text(encoding="utf-8"))
        sha1 = doc["output"]["sha1"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return sha1 if isinstance(sha1, str) and _HEX40.match(sha1) else None


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def compute_code_digest(root: Path, patterns: list[str], *, excludes: list[str] | tuple[str, ...] = ()) -> str:
    """sha256 over the LF-normalised bytes of every file the patterns match, in sorted path order.

    Each file contributes `<posix path>\0<bytes with CRLF -> LF>\0`. A pattern that matches no
    file raises ValueError: a digest over fewer files than declared is not the digest.
    Explicit exclusion patterns remove prose only; an empty exclusion match is harmless.
    """
    found: set[str] = set()
    for pat in patterns:
        hits = [p for p in root.glob(pat) if p.is_file()]
        if not hits:
            raise ValueError(f"code_digest_files pattern {pat!r} matches no file under {root}")
        found.update(p.relative_to(root).as_posix() for p in hits)
    for pattern in excludes:
        found.difference_update(p.relative_to(root).as_posix() for p in root.glob(pattern) if p.is_file())
    digest = hashlib.sha256()
    for rel in sorted(found):
        digest.update(rel.encode("utf-8") + b"\0")
        digest.update((root / rel).read_bytes().replace(b"\r\n", b"\n") + b"\0")
    return digest.hexdigest()


def _run(argv: list[str], cwd: Path, timeout: int) -> tuple[int | None, str, bool]:
    """(exit code or None on a spawn/timeout failure, combined output, timed out)."""
    try:
        proc = subprocess.run([str(a) for a in argv], cwd=str(cwd), capture_output=True,
                              text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        parts = [p.decode("utf-8", errors="replace") if isinstance(p, bytes) else (p or "")
                 for p in (exc.stdout, exc.stderr)]
        return None, "".join(parts), True
    except OSError as exc:
        return None, str(exc), False
    return proc.returncode, (proc.stdout or "") + (proc.stderr or ""), False


# ────────────────────────────────────────────────────────────── SOURCE / BUILD


def check_command(item: dict, root: Path) -> Row:
    """One manifest command, executed. Exit 0 is the only pass; the tool's last words are quoted."""
    row = Row(item["id"], item["kind"], "PASS")
    argv = item.get("command") or []
    if not argv:
        return row.fail("no command declared")
    if argv[0] == "$PY":                       # the manifest stays interpreter-agnostic
        argv = [_PY] + list(argv[1:])
    script = argv[1] if len(argv) > 1 and str(argv[1]).endswith(".py") else None
    if script and not (root / script).is_file():
        return row.fail(f"missing tool: {script}")
    started = time.time()
    code, out, timed_out = _run(argv, root, int(item.get("timeout", DEFAULT_TIMEOUT)))
    took = time.time() - started
    tail = _tail(out)
    row.detail = f"exit {code} ({took:.0f}s)" + (f"  {tail}" if tail else "")
    if timed_out:
        row.fail(f"timed out after {item.get('timeout', DEFAULT_TIMEOUT)}s — did not run is not a pass")
    elif code != 0:
        row.fail(f"exit {code}" + (f": {tail}" if tail else " (no output)"))
    return row


# ────────────────────────────────────────────────────────────── MODEL


def check_model(item: dict, root: Path) -> Row:
    """One pytest subprocess per named file. Zero passes, or anything that did not run, fails."""
    import release_lanes  # the shared outcome parser, so counting has one convention

    row = Row(item["id"], "MODEL", "PASS")
    files = item.get("files") or []
    if not files:
        return row.fail("no test files declared")
    failed: list[str] = []
    passed_files = 0
    for rel in files:
        path = root / rel
        if not path.is_file():
            failed.append(f"{rel}: MISSING")
            continue
        code, out, timed_out = _run(
            [_PY, "-m", "pytest", rel, "-q", "-p", "no:randomly", "-rs"],
            root, int(item.get("timeout", MODEL_TIMEOUT)))
        if timed_out:
            failed.append(f"{rel}: TIMEOUT")
            continue
        if code != 0:
            failed.append(f"{rel}: exit {code} {_tail(out, 1)}")
            continue
        counts = release_lanes._count_outcomes(out)
        unexplained = release_lanes._unexplained_skips(out, ())
        did_not_run = (counts["skipped"] or counts["xfailed"] or counts["xpassed"]
                       or counts["deselected"] or counts["error"] or unexplained)
        if counts["passed"] == 0:
            failed.append(f"{rel}: 0 passed"
                          + (f", {counts['skipped']} skipped" if counts["skipped"] else "")
                          + " — collection, empty output or an all-skipped file is not a pass")
            continue
        if did_not_run:
            failed.append(f"{rel}: {counts['skipped']} skipped/{len(unexplained)} unexplained, "
                          f"{counts['xfailed']} xfailed, {counts['deselected']} deselected, "
                          f"{counts['error']} error — did not run is not passed")
            continue
        passed_files += 1
    row.detail = f"{passed_files}/{len(files)} files green"
    for line in failed:
        row.fail(line)
    return row


# ────────────────────────────────────────────────────────────── LIVE


_RECEIPT_FIELDS = {
    "scenario_id": str, "date": str, "lane": str, "evidence_file": str,
    "evidence_sha256": str, "rom_sha1_executed": str, "code_digest": str,
    "synth": list, "checks": list, "secondary_evidence": list, "run_witness": dict,
}


def receipt_schema_errors(doc: object) -> list[str]:
    """Everything wrong with a receipt's shape, in the words of what is missing."""
    if not isinstance(doc, dict):
        return [f"receipt is {type(doc).__name__}, not an object"]
    errs: list[str] = []
    if doc.get("schema") != RECEIPT_SCHEMA:
        errs.append(f"schema is {doc.get('schema')!r}, expected {RECEIPT_SCHEMA!r}")
    grade = doc.get("grade")
    if grade not in ALLOWED_GRADES:
        errs.append(f"grade is {grade!r}; this schema admits only {sorted(ALLOWED_GRADES)} "
                    f"(a lane author cannot promote their own run)")
    for key, kind in _RECEIPT_FIELDS.items():
        if key not in doc:
            errs.append(f"missing field {key!r}")
        elif not isinstance(doc[key], kind) or (kind is str and not doc[key]):
            errs.append(f"field {key!r} is {type(doc[key]).__name__}, expected {kind.__name__}")
    sha = doc.get("evidence_sha256")
    if isinstance(sha, str) and not _HEX64.match(sha):
        errs.append(f"evidence_sha256 {sha!r} is not 64 lowercase hex chars")
    rom = doc.get("rom_sha1_executed")
    if isinstance(rom, str) and not _HEX40.match(rom):
        errs.append(f"rom_sha1_executed {rom!r} is not a sha1")
    checks = doc.get("checks")
    if isinstance(checks, list):
        for i, one in enumerate(checks):
            if not isinstance(one, dict) or "name" not in one or "result" not in one:
                errs.append(f"checks[{i}] needs 'name' and 'result'")
            elif one["result"] not in ("PASS", "FAIL", "INFO"):
                errs.append(f"checks[{i}] result {one['result']!r} is not PASS/FAIL/INFO")
        if not checks:
            errs.append("checks is empty: a receipt with no checks proves nothing")
    return errs


_WITNESS_FIELDS = {"path", "sha256", "scope", "source_head", "code_digest"}
_ARTIFACT_FIELDS = {"path", "sha256", "run_witness"}
_WITNESS_DOCUMENT_FIELDS = {"evidence_root", "source_head", "code_digest", "overlay_sha1"}


def literal_evidence_path(value: object) -> Path:
    """Shared binder boundary: a nonempty literal absolute path, never implicit CWD."""
    if not isinstance(value, str) or not value or any(char in value for char in "*?[]"):
        raise ValueError("literal absolute evidence path required")
    path = Path(value)
    if not path.is_absolute():
        raise ValueError(f"relative evidence path: {value}")
    return path.resolve()


def validate_evidence_descriptor(record: object) -> None:
    """Shared artifact descriptor contract, excluding witness reads and snapshot mechanics."""
    if not isinstance(record, dict) or set(record) != _ARTIFACT_FIELDS:
        raise ValueError("artifact descriptor exact fields required")
    literal_evidence_path(record["path"])
    if not isinstance(record["sha256"], str) or not _HEX64.fullmatch(record["sha256"]):
        raise ValueError("artifact needs lowercase sha256")


def validate_run_witness(record: object, witness: object, artifact: Path, *,
                         code_digest: str, overlay_sha1: str | None,
                         source_head: str | None = None) -> None:
    """Validate already-hashed witness metadata; reusable by rebind3 without its Snapshot."""
    if not isinstance(record, dict) or set(record) != _WITNESS_FIELDS:
        raise ValueError("run_witness exact fields required (no aliases)")
    literal_evidence_path(record["path"])
    if not isinstance(record["sha256"], str) or not _HEX64.fullmatch(record["sha256"]):
        raise ValueError("witness needs lowercase sha256")
    base = literal_evidence_path(record["scope"])
    if not isinstance(witness, dict) or set(witness) not in (
            _WITNESS_DOCUMENT_FIELDS, _WITNESS_DOCUMENT_FIELDS | {"head"}):
        raise ValueError("witness document exact fields required")
    if not artifact.resolve().is_relative_to(base):
        raise ValueError(f"artifact outside witness scope: {artifact}")
    if literal_evidence_path(witness["evidence_root"]) != base:
        raise ValueError("evidence_root does not equal declared scope")
    source = record["source_head"]
    if not isinstance(source, str) or not _HEX40.fullmatch(source):
        raise ValueError("missing source_head execution cut")
    if (witness["source_head"] != source or witness.get("head", source) != source
            or (source_head is not None and source != source_head)):
        raise ValueError("source_head disagrees with recorded execution cut")
    if record["code_digest"] != code_digest or witness["code_digest"] != code_digest:
        raise ValueError("code_digest disagrees with receipt execution binding")
    if witness["overlay_sha1"] != overlay_sha1:
        raise ValueError("overlay_sha1 disagrees with current provenance")


def _evidence_bytes(record: object, label: str, row: Row) -> bytes | None:
    """Read once: the bytes hashed are also the bytes interpreted."""
    if not isinstance(record, dict):
        row.fail(f"{label}: missing artifact binding")
        return None
    path, expected = record.get("path"), record.get("sha256")
    if not isinstance(path, str) or not path or not isinstance(expected, str) or not _HEX64.fullmatch(expected):
        row.fail(f"{label}: needs path and lowercase sha256")
        return None
    try:
        raw = literal_evidence_path(path).read_bytes()
    except (OSError, ValueError) as exc:
        row.fail(f"{label}: evidence file not reachable or invalid path: {path}: {exc}")
        return None
    if hashlib.sha256(raw).hexdigest() != expected:
        row.fail(f"{label}: evidence sha256 mismatch: {path}")
        return None
    return raw


def _check_witness(record: object, artifact: Path, doc: dict, current_rom: str | None,
                   label: str, row: Row) -> None:
    from tools.polished_live.runtime import loads_json

    raw = _evidence_bytes(record, label, row)
    if raw is None:
        return
    try:
        witness = loads_json(raw.decode("utf-8"))
        validate_run_witness(record, witness, artifact, code_digest=doc["code_digest"],
                             overlay_sha1=current_rom)
    except (UnicodeError, ValueError, TypeError, OSError) as exc:
        row.fail(f"{label}: invalid witness binding: {exc}")


def _check_evidence_aliases(doc: dict, row: Row) -> None:
    """Distinct supporting paths, primary content isolation, and reusable run witnesses."""
    records = [{"path": doc["evidence_file"], "sha256": doc["evidence_sha256"]},
               *doc["secondary_evidence"]]
    paths = set()
    primary_sha = doc["evidence_sha256"]
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            continue
        try:
            path = literal_evidence_path(record.get("path"))
        except ValueError:
            continue  # the byte/binding checks report the invalid record
        sha = record.get("sha256")
        if not isinstance(sha, str) or not _HEX64.fullmatch(sha):
            continue
        if path in paths or (index > 0 and sha == primary_sha):
            row.fail(f"supporting evidence aliases another artifact: {path}")
        paths.add(path)
    witnesses = [doc["run_witness"], *(record.get("run_witness") for record in doc["secondary_evidence"]
                                      if isinstance(record, dict))]
    for record in witnesses:
        if not isinstance(record, dict):
            continue
        try:
            path = literal_evidence_path(record.get("path"))
        except ValueError:
            continue
        sha = record.get("sha256")
        if not isinstance(sha, str) or not _HEX64.fullmatch(sha):
            continue
        if path in paths or sha == primary_sha:
            row.fail(f"run witness aliases evidence artifact: {path}")


def _producer_errors(item: dict, raw: bytes) -> list[str]:
    """Interpret the producer, not the receipt's handwritten PASS transcription."""
    from tools.polished_live.runtime import loads_json

    try:
        text = raw.decode("utf-8")
        scenario = item["expect_scenario"]
        json_primary = text.lstrip().startswith("{")
        if scenario == "live/capture-party-only":
            try:
                loads_json(text)
            except ValueError:
                pass
            else:
                return ["capture primary must be text, not a JSON verdict"]
            contract = loads_json((ROOT / "tools/polished_live/capture_contract.json").read_text(encoding="utf-8"))
            label = "STAGE2 " + contract["failed_throw_check"]
            if not re.search(r"^\s*\[ok\] " + re.escape(label) + r"\b", text, re.MULTILINE):
                return ["producer missing observed failed-throw negative"]
        if scenario in ("title/wordmark-vs-clean-control", "r1/manager-randomized-pair") and not json_primary:
            return ["scenario primary must be JSON with its native control observations"]
        if scenario == "live/hello-panel-true":
            records = [loads_json(line) for line in text.splitlines() if line.strip()]
            hellos = [r["msg"] for r in records if r.get("dir") == "c2s"
                      and isinstance(r.get("msg"), dict) and r["msg"].get("event") == "hello"]
            if not hellos or any(h.get("panel") is not True or h.get("panel_abi") != 3 for h in hellos):
                return ["producer has no valid panel hello"]
            return []
        if json_primary:
            doc = loads_json(text)
            if not isinstance(doc, dict):
                return ["producer is not an object"]
            if scenario == "title/wordmark-vs-clean-control":
                runs = doc.get("runs", {})
                overlay, clean = runs.get("overlay", {}), runs.get("clean", {})
                valid = (overlay.get("result") == "PASS" and overlay.get("reasons") == []
                         and clean.get("result") == "FAIL"
                         and any("tile IDs differ" in r for r in clean.get("reasons", [])))
                return [] if valid else ["title producer lacks overlay PASS / failing identical clean control"]
            if scenario == "r1/manager-randomized-pair":
                valid = (doc.get("mode") == "jar" and doc.get("cartridges_status") == 200
                         and doc.get("contract_matches_downloads") is True
                         and bool(doc.get("picker_polished")) and bool(doc.get("run_id"))
                         and (doc.get("status") or {}).get("jar_found") is True)
                return [] if valid else ["Manager producer provisioning/pin verdict failed"]
            if "verdict" in doc:
                good = doc["verdict"] == "PASS"
            elif "ok" in doc:
                good = doc["ok"] is True
            else:
                return ["producer verdict missing"]
            if not good or doc.get("reasons") or doc.get("why"):
                return ["producer verdict is not an unqualified PASS"]
            return []
        verdicts = re.findall(r"^RESULT:\s+(PASS|FAIL)\b.*$", text, re.MULTILINE)
        if verdicts != ["PASS"] or re.search(r"^\s*\[(?:fail|FAIL)\]", text, re.MULTILINE):
            return ["producer needs one PASS verdict and no failed checks"]
        return []
    except (OSError, UnicodeError, ValueError, TypeError, KeyError, AttributeError) as exc:
        return [f"producer evidence malformed: {exc}"]


def _replay_errors(item: dict, primary: bytes, evidence: Path,
                   artifacts: dict[Path, bytes]) -> list[str]:
    """Re-judge supported observations; absent raw inputs cannot inherit an old PASS."""
    from tools.polished_live.runtime import loads_json

    scenario = item["expect_scenario"]
    try:
        if scenario == "live/capture-party-only":
            negatives = [raw for path, raw in artifacts.items() if path.name == "capture_negative.json"]
            if len(negatives) != 1:
                return ["capture replay needs one bound capture_negative.json"]
            recorded = loads_json(negatives[0].decode("utf-8"))
            throws = recorded.get("throws") if isinstance(recorded, dict) else None
            if not isinstance(throws, list):
                return ["capture replay missing throw observations"]
            # Mirror live.lua Oracle.evaluate_failed_throws: successful captures
            # do not count; at least one failed throw and zero failed-throw hits.
            failed, hits = 0, 0
            for throw in throws:
                if (not isinstance(throw, dict) or type(throw.get("caught")) is not bool
                        or type(throw.get("cap_hits")) is not int or throw["cap_hits"] < 0):
                    return ["capture replay malformed throw observation"]
                if not throw["caught"]:
                    failed += 1
                    hits += throw["cap_hits"]
            if failed == 0 or hits != 0:
                return [f"capture replay failed throws {failed}; capture hits during failed throws {hits}"]
            return []
        if scenario in ("duo/play-faint-natural", "duo/play-whiteout-natural"):
            from tools.polished_live import duo

            recorded = loads_json(primary.decode("utf-8"))
            observations = recorded.get("evidence")
            if not isinstance(observations, dict):
                return ["replay missing raw natural-loss observations"]
            oracle = duo.oracle_faint_natural if scenario.endswith("faint-natural") else duo.oracle_whiteout_natural
            verdict, reasons, _ = oracle(observations)
            return [] if verdict == "PASS" else ["natural-loss replay: " + str(r) for r in reasons]
        if scenario == "panel/real-host-paging":
            from tools.polished_live.panel_host_live import judge_pages

            recorded = loads_json(primary.decode("utf-8"))
            traces = [raw for path, raw in artifacts.items() if path.name == "panel_host.json"]
            if len(traces) != 1:
                return ["replay needs one hashed panel_host.json with runtime page counts"]
            observations = loads_json(traces[0].decode("utf-8"))
            _, reasons = judge_pages(recorded.get("server_rows"), observations.get("pages"),
                                     observations.get("lines_per_page", 2))
            return ["panel replay: " + str(r) for r in reasons]
        if scenario == "trade/shipped-native-commit-cold-continue":
            from tools.polished_live.trade_duo_live import replay_saved

            verdict, reasons = replay_saved(evidence.parent, bound_artifacts=frozenset(artifacts))
            return [] if verdict == "PASS" else ["trade replay: " + str(r) for r in reasons]
        return []
    except (UnicodeError, ValueError, KeyError, TypeError, AttributeError, OSError) as exc:
        return [f"replay observations missing or malformed: {exc}"]


def check_live(item: dict, root: Path, current_rom: str | None,
               code_digest: str | None = None) -> Row:
    """A committed DEV receipt, schema- and byte-checked, bound to the overlay published NOW."""
    row = Row(item["id"], "LIVE", "PASS")
    rel = item.get("receipt")
    if not rel:
        return row.fail("no receipt declared")
    path = root / rel
    if not path.is_file():
        return row.fail(f"missing receipt: {rel}")
    from tools.polished_live.runtime import loads_json

    try:
        doc = loads_json(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        return row.fail(f"receipt is not valid JSON: {exc}")
    errs = receipt_schema_errors(doc)
    if errs:
        for err in errs:
            row.fail(f"bad schema: {err}")
        row.detail = Path(rel).name
        return row

    evidence = Path(doc["evidence_file"])
    bind = item.get("bind", "executed")
    if bind == "source":
        bound = doc.get("source_overlay_sha1")
        what = "overlay the cartridge was derived from"
    else:
        bound = doc["rom_sha1_executed"]
        what = "ROM booted"
    if bind == "source" and not isinstance(bound, str):
        row.fail("bind=source needs a source_overlay_sha1 field (the overlay this cartridge "
                 "was randomized from); a randomized cartridge's own sha1 can never equal the "
                 "overlay sha1")
        bound = None
    row.detail = (f"{doc['scenario_id']} {doc['date']} grade={doc['grade']} "
                  f"rom={doc['rom_sha1_executed'][:8]} src={(bound or '?')[:8]} "
                  f"digest={doc['code_digest'][:12]}")

    # 1. the executed ROM must be the one published now
    if current_rom is None:
        row.fail(f"cannot read {PROVENANCE}: the ROM binding cannot be checked")
    elif bound != current_rom:
        row.fail(f"STALE: {what} {bound} but {PROVENANCE} publishes {current_rom} — "
                 f"re-run this scenario on the current overlay")

    # 2. the receipt's own claim about the provenance it was written against: REQUIRED
    claimed = doc.get("overlay_provenance_sha256")
    if not isinstance(claimed, str) or not _HEX64.match(claimed):
        row.fail(f"overlay_provenance_sha256 is {claimed!r}: a receipt must record the sha256 "
                 f"of the provenance it was written against")
    else:
        try:
            live = _sha256_file(root / PROVENANCE)
        except OSError as exc:
            row.fail(f"cannot hash {PROVENANCE}: {exc}")
            live = None
        if live and claimed != live:
            row.fail(f"overlay_provenance_sha256 {claimed[:12]} is not "
                     f"the committed provenance's {live[:12]}")

    # 2b. the code the run exercised: the digest the verifier computes now, not a free string
    if code_digest is None:
        row.fail("cannot compute the code digest: the code binding cannot be checked")
    elif doc["code_digest"] != code_digest:
        row.fail(f"STALE: code_digest {doc['code_digest'][:12]} is not the {code_digest[:12]} "
                 f"computed from the current code_digest_files — re-run this scenario")

    # 2c. the receipt must be for THIS scenario and carry the checks this item demands
    if doc["scenario_id"] != item.get("expect_scenario"):
        row.fail(f"scenario_id {doc['scenario_id']!r} is not the expected "
                 f"{item.get('expect_scenario')!r}")
    passed = {c["name"] for c in doc["checks"] if c["result"] == "PASS"}
    if not passed:
        row.fail("receipt has no PASS check (INFO-only proves nothing)")
    for name in item.get("expect_checks") or []:
        if name not in passed:
            row.fail(f"expected check {name!r} is not recorded as PASS")
    if not item.get("expect_checks"):
        row.fail("item declares no expect_checks")

    # Re-hash every declared artifact and witness before consuming any producer result.
    primary = _evidence_bytes({"path": str(evidence), "sha256": doc["evidence_sha256"]}, "primary", row)
    _check_witness(doc["run_witness"], evidence, doc, current_rom, "primary witness", row)
    secondary = doc["secondary_evidence"]
    if not secondary:
        row.fail("secondary_evidence is empty: supporting evidence is required")
    artifacts: dict[Path, bytes] = {}
    for index, record in enumerate(secondary):
        label = f"secondary_evidence[{index}]"
        try:
            validate_evidence_descriptor(record)
        except ValueError as exc:
            row.fail(f"{label}: invalid artifact binding: {exc}")
        raw = _evidence_bytes(record, label, row)
        if raw is not None:
            artifacts[Path(record["path"]).resolve()] = raw
        if isinstance(record, dict) and isinstance(record.get("path"), str):
            _check_witness(record.get("run_witness"), Path(record["path"]), doc, current_rom,
                           label + " witness", row)
    _check_evidence_aliases(doc, row)
    if primary is not None:
        for error in _producer_errors(item, primary):
            row.fail(error)
        if row.ok:
            for error in _replay_errors(item, primary, evidence, artifacts):
                row.fail(error)

    for note in doc.get("checks", []):
        if note.get("result") == "FAIL":
            row.fail(f"receipt records a FAIL check: {note['name']}")
    return row


# ────────────────────────────────────────────────────────────── MANAGER


def check_manager(item: dict, root: Path) -> Row:
    """Import the live server modules and assert the gate the item names."""
    row = Row(item["id"], "MANAGER", "PASS")
    check = item.get("check")
    if check not in _MANAGER_CHECKS:
        return row.fail(f"unknown manager check {check!r}")
    where = str(root)
    if where not in sys.path:
        sys.path.insert(0, where)
    try:
        ok, detail = _MANAGER_CHECKS[check](root)
    except Exception as exc:                      # a missing module is a failure, never a pass
        return row.fail(f"{type(exc).__name__}: {exc}")
    row.detail = detail
    if not ok:
        row.fail(detail or f"{check} did not hold")
    return row


def _jar_pin(root: Path) -> tuple[bool, str]:
    """The Manager's resolved jar must be allowlisted and carry the Polished handler/resources."""
    import server.upr_pipeline as upr

    found = upr.find_upr_jar()
    if not found:
        return False, "jar not reachable: the Manager resolver found no jar"
    jar = Path(found)
    try:
        raw = jar.read_bytes()
        pins = json.loads((root / "data/upr_jars.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return False, f"jar not reachable or allowlist unreadable: {exc}"
    if not isinstance(pins, dict):
        return False, "data/upr_jars.json is not a label-to-sha256 object"
    actual = hashlib.sha256(raw).hexdigest()
    labels = [label for label, sha in pins.items() if isinstance(label, str) and sha == actual]
    if not labels:
        return False, f"Manager jar {jar} sha256 {actual} is not pinned in data/upr_jars.json"
    # the same pin the runtime consults: a jar nobody may run is not a shipped jar
    if not upr.jar_is_trusted(str(jar)):
        return False, "the pin is in data/upr_jars.json but jar_is_trusted() refuses the file"
    try:
        # Inspect the bytes just hashed, so a replacement cannot mix pin and contents.
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            missing = sorted(set(_POLISHED_JAR_ENTRIES) - set(archive.namelist()))
    except zipfile.BadZipFile as exc:
        return False, f"Manager jar is not a readable ZIP: {exc}"
    if missing:
        return False, "missing Polished jar entries: " + ", ".join(missing)
    return True, f"jar {actual} pinned, byte-exact and Polished-capable ({labels[-1]}; {jar})"


def _randomizer_flag(root: Path) -> tuple[bool, str]:
    import server.upr_pipeline as upr

    if upr.POLISHED_RANDOMIZER_ENABLED is not True:
        return False, "upr_pipeline.POLISHED_RANDOMIZER_ENABLED is not True"
    return True, "POLISHED_RANDOMIZER_ENABLED is True"


def _picker_gate(root: Path) -> tuple[bool, str]:
    """The Manager's ROM picker must offer Polished and the run form must list the family."""
    import server.manager as manager
    import server.upr_pipeline as upr

    family = getattr(upr, "FAMILY_POLISHED", "gen2_polished")
    if family not in manager.GAME_FAMILY.values():
        return False, f"manager.GAME_FAMILY has no {family!r} family"
    if family not in manager.new_run_form()["randomizer_games"]:
        return False, f"new_run_form() does not offer {family!r} for Randomize"
    return True, f"picker lists {family!r} and the run form offers it"


def _companion_gate(root: Path) -> tuple[bool, str]:
    """The patcher must serve the Polished companion, md5-identical to the published overlay."""
    import server.patcher as patcher

    target = patcher.TARGETS.get("polished-crystal")
    if not target:
        return False, "patcher.TARGETS has no 'polished-crystal' target"
    published = json.loads((root / PROVENANCE).read_text(encoding="utf-8"))["output"]["md5"]
    if target["patched_md5"] != published:
        return False, (f"patcher serves md5 {target['patched_md5']}, the published overlay is "
                       f"{published}")
    return True, f"polished-crystal target serves overlay md5 {published}"


_MANAGER_CHECKS = {
    "randomizer_flag": _randomizer_flag,
    "picker_gate": _picker_gate,
    "companion_gate": _companion_gate,
    "jar_pin": _jar_pin,
}


# ────────────────────────────────────────────────────────────── RELEASE


_RELEASE_STAMP = Path("patch") / "dist" / "companion_version.json"
_STAMPED_RELEASE = re.compile(r"v\d+\.\d+\.\d+")


def _stamped_release(root: Path) -> tuple[str | None, str]:
    """The release version the companions were stamped with (tools/stamp_release.py), or (None, why not).

    Only one clean `vX.Y.Z` counts: a `mixed` or `dev` record vouches for no release, and the player ZIP is
    never built with allow_unstamped."""
    hint = "run tools/stamp_release.py --version vX.Y.Z"
    path = root / _RELEASE_STAMP
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        version = doc["version"]
    except OSError as exc:
        return None, f"companions not stamped for a release: cannot read {_RELEASE_STAMP.as_posix()} ({type(exc).__name__}); {hint}"
    except (ValueError, KeyError, TypeError) as exc:
        return None, f"companions not stamped for a release: {_RELEASE_STAMP.as_posix()} is unusable ({type(exc).__name__}: {exc}); {hint}"
    if not isinstance(version, str) or not _STAMPED_RELEASE.fullmatch(version):
        return None, f"companions not stamped for a release: {_RELEASE_STAMP.as_posix()} says {version!r}; {hint}"
    import stamp_release
    if doc.get("schema") not in (stamp_release.SCHEMA, stamp_release.SCHEMA_V2):
        return None, f"companions not stamped for a release: unknown certificate schema; {hint}"
    if doc["schema"] == stamp_release.SCHEMA_V2:
        errors = stamp_release.certificate_errors(version, root, path.parent, polished=True, doc=doc)
        if errors:
            return None, "companion certificate refused: " + "; ".join(errors)
    return version, ""


def check_release(item: dict, root: Path) -> Row:
    """Check patch-inclusive ZIP hygiene and bind its required Polished UPS to provenance."""
    row = Row(item["id"], "RELEASE", "PASS")
    tools = root / "tools"
    if not (tools / "check_release_zip.py").is_file():
        return row.fail("tools/check_release_zip.py is missing")
    version, why = _stamped_release(root)
    if version is None:
        return row.fail(why)
    out_dir = Path(tempfile.mkdtemp(prefix="polished-rel-"))
    zip_path = out_dir / f"SLink-player-{version}.zip"
    captured = io.StringIO()
    try:
        if str(tools) not in sys.path:
            sys.path.insert(0, str(tools))
        make_release = importlib.import_module("make_release")
        # build_release refuses with sys.exit(1) after printing why to stderr; SystemExit is not an Exception.
        with contextlib.redirect_stderr(captured):
            made = make_release.build_release(version=version, out_dir=out_dir,
                                              skip_generators=True, quiet=True, with_patch=True)
    except SystemExit as exc:
        reason = _tail(captured.getvalue(), 6) or "no reason printed"
        return row.fail(f"make_release.build_release refused (exit {exc.code}): {reason}")
    except Exception as exc:
        return row.fail(f"make_release.build_release failed: {type(exc).__name__}: {exc}")
    if not made or not Path(made).is_file():
        return row.fail(f"make_release returned no zip ({made})")
    zip_path = Path(made)
    code, out, timed_out = _run(
        [_PY, str(Path("tools") / "check_release_zip.py"), str(zip_path)], root,
        int(item.get("timeout", RELEASE_TIMEOUT)))
    row.detail = f"{zip_path.name} ({zip_path.stat().st_size} B) " + _tail(out, 1)
    if timed_out:
        row.fail("check_release_zip timed out")
    elif code != 0:
        row.fail(f"check_release_zip exit {code}: {_tail(out, 2)}")
    companion = "companion/SLink-Polished.ups"
    try:
        provenance = json.loads((root / PROVENANCE).read_text(encoding="utf-8"))
        expected_sha = provenance["output"]["ups"]["sha256"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return row.fail(f"cannot read {companion} sha256 from {PROVENANCE}: {type(exc).__name__}: {exc}")
    try:
        with zipfile.ZipFile(zip_path) as archive:
            actual_sha = hashlib.sha256(archive.read(f"SLink-player-{version}/{companion}")).hexdigest()
    except KeyError:
        return row.fail(f"missing required {companion} in player ZIP")
    except (OSError, zipfile.BadZipFile) as exc:
        return row.fail(f"cannot read {companion} from player ZIP: {type(exc).__name__}: {exc}")
    if actual_sha != expected_sha:
        row.fail(f"{companion} sha256 mismatch: expected {expected_sha}, got {actual_sha}")
    else:
        row.detail += f"; Polished UPS sha256={actual_sha}"
    return row


# ────────────────────────────────────────────────────────────── OPEN


def check_open(item: dict, root: Path, live_passed: frozenset[str] = frozenset()) -> Row:
    """An obligation other cards are still building. Open is ALWAYS red; closing needs a receipt.

    `live_passed` is the set of LIVE item ids that PASSed in this same run.
    """
    row = Row(item["id"], "OPEN", "PASS")
    status = item.get("status")
    row.detail = f"status={status}"
    if item.get("blocking_rc", True) is not True:
        row.fail(f"blocking_rc is {item.get('blocking_rc')!r}: an OPEN obligation always blocks the RC")
    if status == "OPEN":
        row.detail += "  " + (item.get("description") or "")[:60]
        return row.fail("OPEN: " + (item.get("blocker") or "work not landed"))
    if status != "CLOSED":
        return row.fail(f"status is {status!r}; an OPEN item reads OPEN or CLOSED only")
    closer = item.get("closed_by")
    if not closer:
        return row.fail("CLOSED without a closed_by: name the LIVE item whose receipt closes it")
    row.detail = f"status=CLOSED by {closer}"
    if closer not in live_passed:
        row.fail(f"closed_by {closer!r} is not a LIVE item that PASSes in this run: closing "
                 f"needs a real receipt")
    return row


def check_open_consistency(open_items: list[dict], root: Path) -> list[Row]:
    """Cross-check Manager availability against qualified Polished capabilities.

    Explode and Rival Swap are enabled before receipt rebind; check_open still keeps
    their OPEN obligation red until its closing LIVE evidence passes in this run.
    Only an item that NAMES an option is bound. Several open obligations (the write sink, the
    title splash, the panel pages) have no Manager row at all, and inventing one would make this
    check assert something the Manager never claimed.
    """
    bound = [it for it in open_items if it.get("manager_options")]
    if not bound:
        return []
    where = str(root)
    if where not in sys.path:
        sys.path.insert(0, where)
    enabled = frozenset({"explode_mode", "rival_team_swap"})
    rows = []
    for item in bound:
        options = item["manager_options"]
        # a distinct id: this row is a CROSS-CHECK on the OPEN row, not the OPEN row again
        row = Row(f"{item['id']}/manager", "OPEN", "PASS",
                  detail=f"{item['id']}: qualified availability for {', '.join(options)}")
        try:
            import server.manager as manager
        except Exception as exc:
            row.fail(f"cannot import server.manager: {type(exc).__name__}: {exc}")
            rows.append(row)
            continue
        for option in options:
            rule = manager.OPTION_SUPPORT.get(option)
            if rule is None:
                row.fail(f"manager.OPTION_SUPPORT has no {option!r} row")
                continue
            cell = rule.get("gen2_polished")
            if cell is None:
                row.fail(f"OPTION_SUPPORT[{option!r}] has no gen2_polished row")
            elif cell.get("ok") is not (option in enabled):
                row.fail(f"OPTION_SUPPORT[{option!r}]['gen2_polished'] expected ok={option in enabled}, "
                         f"got {cell.get('ok')!r}")
            elif not cell.get("why"):
                row.fail(f"a bound option must carry its companion requirement or refusal reason: {option!r}")
        rows.append(row)
    return rows


# ────────────────────────────────────────────────────────────── manifest


def load_manifest(path: Path) -> dict:
    doc = json.loads(path.read_text(encoding="utf-8"))
    if doc.get("schema") != MANIFEST_SCHEMA:
        raise ValueError(f"manifest schema is {doc.get('schema')!r}, expected {MANIFEST_SCHEMA!r}")
    return doc


def manifest_errors(doc: object) -> list[str]:
    """Declaration checks only — no execution, no evidence qualification."""
    if not isinstance(doc, dict):
        return [f"manifest is {type(doc).__name__}, not an object"]
    errs: list[str] = []
    items = doc.get("items")
    if not isinstance(items, list) or not items:
        return ["manifest has no items"]
    if doc.get("open") is not None and not isinstance(doc.get("open"), list):
        errs.append("'open' is not a list")
    live_ids = {i.get("id") for i in items if isinstance(i, dict) and i.get("kind") == "LIVE"}
    digest_files = doc.get("code_digest_files")
    if (not isinstance(digest_files, list) or not digest_files
            or not all(isinstance(p, str) and p for p in digest_files)):
        errs.append("code_digest_files must be a non-empty list of path patterns")
    excludes = doc.get("code_digest_exclude", [])
    prose = {"data/games/polished_crystal/**/*.md", "data/games/polished_crystal/**/*.txt"}
    if not isinstance(excludes, list) or any(not isinstance(p, str) or p not in prose for p in excludes):
        errs.append("code_digest_exclude may contain only Polished pack *.md/*.txt prose patterns")
    seen: set[str] = set()
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            errs.append(f"items[{i}] is not an object")
            continue
        for key in ("id", "kind", "description"):
            if not item.get(key):
                errs.append(f"items[{i}] is missing {key!r}")
        rid, kind = item.get("id"), item.get("kind")
        if rid in seen:
            errs.append(f"duplicate id {rid!r}")
        seen.add(rid)
        if kind not in KINDS:
            errs.append(f"{rid}: kind {kind!r} is not one of {KINDS}")
        if kind == "OPEN":
            errs.append(f"{rid}: OPEN items belong in the 'open' list, not 'items'")
        if kind in ("SOURCE", "BUILD") and not item.get("command"):
            errs.append(f"{rid}: {kind} needs a command")
        if kind == "MODEL" and not item.get("files"):
            errs.append(f"{rid}: MODEL needs files")
        if kind == "LIVE" and not item.get("receipt"):
            errs.append(f"{rid}: LIVE needs a receipt")
        if kind == "LIVE":
            if not isinstance(item.get("expect_scenario"), str) or not item.get("expect_scenario"):
                errs.append(f"{rid}: LIVE needs an expect_scenario")
            ec = item.get("expect_checks")
            if not isinstance(ec, list) or not ec or not all(isinstance(n, str) and n for n in ec):
                errs.append(f"{rid}: LIVE needs a non-empty expect_checks list of check names")
        if kind == "MANAGER" and item.get("check") not in _MANAGER_CHECKS:
            errs.append(f"{rid}: unknown manager check {item.get('check')!r}")
    open_list = doc.get("open") if isinstance(doc.get("open"), list) else []
    for i, item in enumerate(open_list):
        if not isinstance(item, dict):
            errs.append(f"open[{i}] is not an object")
            continue
        for key in ("id", "kind", "description", "status"):
            if not item.get(key):
                errs.append(f"open[{i}] is missing {key!r}")
        if item.get("kind") != "OPEN":
            errs.append(f"open[{i}] kind is {item.get('kind')!r}, expected OPEN")
        if item.get("status") not in ("OPEN", "CLOSED"):
            errs.append(f"open[{i}] status is {item.get('status')!r}; OPEN or CLOSED only")
        if item.get("blocking_rc", True) is not True:
            errs.append(f"{item.get('id')}: blocking_rc must be true or absent; an OPEN "
                        f"obligation always blocks the RC")
        if item.get("status") == "CLOSED" and item.get("closed_by") not in live_ids:
            errs.append(f"{item.get('id')}: closed_by {item.get('closed_by')!r} must name a LIVE "
                        f"item id of this manifest")
        if item.get("id") in seen:
            errs.append(f"duplicate id {item.get('id')!r}")
        seen.add(item.get("id"))
    required = doc.get("required_ids")
    if (not isinstance(required, list) or not required
            or not all(isinstance(r, str) and r for r in required)):
        errs.append("required_ids must be a non-empty list of ids (the obligation census)")
    else:
        for rid in sorted(set(required) - seen):
            errs.append(f"required id {rid!r} is absent from the manifest")
        for rid in sorted(x for x in seen - set(required) if isinstance(x, str)):
            errs.append(f"id {rid!r} is not in required_ids: add it there on purpose")
    return errs


# ────────────────────────────────────────────────────────────── driver


def verify(root: Path | None = None, manifest_path: Path | None = None,
           only: frozenset[str] | None = None, *, run_release: bool = True) -> tuple[list[Row], list[str]]:
    """Every item, one Row each. Returns (rows, manifest-level errors)."""
    root = ROOT if root is None else Path(root)
    manifest_path = Path(manifest_path) if manifest_path else root / MANIFEST
    try:
        doc = load_manifest(manifest_path)
    except (OSError, ValueError) as exc:
        return [], [f"cannot read the manifest: {exc}"]
    errs = manifest_errors(doc)
    if errs:
        return [], errs

    try:
        digest = compute_code_digest(root, doc["code_digest_files"], excludes=doc.get("code_digest_exclude", ()))
    except (OSError, ValueError) as exc:
        return [], [f"cannot compute the code digest: {exc}"]
    items = list(doc["items"]) + list(doc.get("open") or [])
    if only:
        items = [it for it in items if it["id"] in only]
        missing = only - {it["id"] for it in items}
        if missing:
            errs.append("unknown id(s): " + ", ".join(sorted(missing)))
    rows: list[Row] = []
    current_rom = overlay_sha1(root)
    for item in items:
        kind = item["kind"]
        if kind == "OPEN":
            continue                      # judged below, once every LIVE verdict is in
        if kind in ("SOURCE", "BUILD"):
            rows.append(check_command(item, root))
        elif kind == "MODEL":
            rows.append(check_model(item, root))
        elif kind == "LIVE":
            rows.append(check_live(item, root, current_rom, digest))
        elif kind == "MANAGER":
            rows.append(check_manager(item, root))
        elif kind == "RELEASE":
            rows.append(check_release(item, root) if run_release
                        else Row(item["id"], kind, "PASS", detail="not executed (run_release=False)"))
    live_passed = frozenset(r.id for r in rows if r.kind == "LIVE" and r.ok)
    rows.extend(check_open(it, root, live_passed) for it in items if it["kind"] == "OPEN")
    rows.extend(check_open_consistency(list(doc.get("open") or []), root))
    rows.sort(key=lambda r: r.id)
    return rows, errs


def _print_table(rows: list[Row]) -> None:
    width = max((len(r.id) for r in rows), default=4)
    kindw = max((len(r.kind) for r in rows), default=4)
    print(f"{'ID'.ljust(width)}  {'KIND'.ljust(kindw)}  VERDICT  DETAIL")
    print(f"{'-' * width}  {'-' * kindw}  -------  ------")
    for r in rows:
        print(f"{r.id.ljust(width)}  {r.kind.ljust(kindw)}  {r.status:<7}  {r.detail}")
    for r in rows:
        for reason in r.reasons:
            print(f"  RED  {r.id}: {reason}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=str(ROOT), help="repository root to judge")
    ap.add_argument("--manifest", default=None, help=f"manifest path (default {MANIFEST})")
    ap.add_argument("--list", action="store_true", help="list the items and exit; runs nothing")
    ap.add_argument("--only", action="append", metavar="ID", default=[],
                    help="judge only this id (repeatable); not a release verdict")
    ap.add_argument("--json", action="store_true", help="emit rows as JSON")
    ap.add_argument("--print-code-digest", action="store_true",
                    help="print the code_digest receipts must carry, and exit")
    ap.add_argument("--no-release", action="store_true",
                    help="skip the RELEASE item (it builds a ZIP); not a release verdict")
    args = ap.parse_args(argv)

    root = Path(args.root).resolve()
    manifest_path = Path(args.manifest).resolve() if args.manifest else root / MANIFEST
    if args.print_code_digest:
        try:
            doc = load_manifest(manifest_path)
            print(compute_code_digest(root, doc["code_digest_files"], excludes=doc.get("code_digest_exclude", ())))
        except (OSError, ValueError, KeyError) as exc:
            print(f"cannot compute the code digest: {exc}", file=sys.stderr)
            return 2
        return 0
    if args.list:
        try:
            doc = load_manifest(manifest_path)
        except (OSError, ValueError) as exc:
            print(f"cannot read the manifest: {exc}", file=sys.stderr)
            return 2
        print("LIST ONLY: not a verdict - nothing below was run or judged.")
        for item in list(doc["items"]) + list(doc.get("open") or []):
            extra = ""
            if item["kind"] in ("SOURCE", "BUILD"):
                extra = "  " + " ".join(str(a) for a in item.get("command", ()))
            elif item["kind"] == "MODEL":
                extra = f"  {len(item.get('files', ()))} files"
            elif item["kind"] == "LIVE":
                extra = "  " + item.get("receipt", "")
            elif item["kind"] == "OPEN":
                extra = f"  status={item.get('status')}"
            print(f"  {item['id']:<24} {item['kind']:<8} {item['description']}{extra}")
        return 0

    only = frozenset(args.only) if args.only else None
    rows, errs = verify(root, manifest_path, only, run_release=not args.no_release)
    partial = bool(only) or args.no_release
    red = bool(errs) or any(not r.ok for r in rows)
    if args.json:
        print(json.dumps({"complete": not partial, "manifest_errors": errs,
                          "rows": [r.as_dict() for r in rows]}, indent=2))
        if partial:
            print("PARTIAL RUN: NOT A RELEASE VERDICT", file=sys.stderr)
    else:
        if errs:
            for err in errs:
                print(f"RED  manifest: {err}", file=sys.stderr)
        print(f"Polished Crystal release check — {len(rows)} items "
              f"(overlay {overlay_sha1(root) or 'unreadable'})\n")
        _print_table(rows)
        failed = [r for r in rows if not r.ok]
        print()
        if errs or failed:
            print(f"GATE FAILED — {len(errs)} manifest error(s), {len(failed)} item(s): "
                  f"{', '.join(r.id for r in failed) or '-'}")
            print("A missing, stale, unverifiable or open item is a failure here: an RC decision "
                  "cannot be made on a lane that did not run.")
            return 1
        if partial:
            print("PARTIAL RUN: NOT A RELEASE VERDICT — "
                  + ("only the named items ran." if only else "the RELEASE item did not run."))
            return 3
        print("GATE PASSED — every item ran and every item passed.")
    if red:
        return 1
    return 3 if partial else 0


if __name__ == "__main__":
    sys.exit(main())
