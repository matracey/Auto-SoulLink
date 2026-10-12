#!/usr/bin/env python3
"""Read-only Polished rival gate callback-PC probe; authoring does not authorize a live run.

    python tools/polished_live/rival_gate_probe.py --setup played --fixture SAVE --route route.json
    python tools/polished_live/rival_gate_probe.py --setup synth --disclosure D.json --fixture SAVE --route route.json         [--expect rival|wild]

--setup played: the fixture is native SaveRAM and the generic callback-PC oracle applies, unchanged.
--setup synth (O-33): the fixture is a tool-built SYNTH derivative (tools/polished_live/derive_rival_save.py) and
--disclosure is that builder's disclosure JSON; its SHA-256 must name the fixture (as its output = the staged scene
save, or as its input = the control run on the declared source) and its file hash is written into the trace
and checked by the oracle. Synth refuses to run without it. Only the setup is synthetic: the battle runs natively.
  --expect rival (default): the stronger rival-card oracle (evaluate_rival).
  --expect wild: the wild-battle control (evaluate_wild_control).

The fixture is native SaveRAM. The route starts at emulator boot and must include any
CONTINUE/menu input needed to reach a real trainer battle. No route means bounded idle.
Route JSON: {"steps": [{"frames": 120, "buttons": []}, {"frames": 2, "buttons": ["A"]}]}.
Each step holds exactly those buttons for its frame count; remaining frames are idle.
No SLink client/server, forced battle, WRAM setup, warp, or CPU manipulation is used.
A fresh private lane is staged per invocation; only harness.launch's own PID is killed.
The Lua RESULT certifies recording completion, not the PC finding. The oracle owns that verdict.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
WORK = Path(os.environ.get("SLINK_WORK_ROOT", "F:/slink-work/lanes/pol-rival-probe")) / "out"
RELEASE = Path(os.environ.get("POL_RELEASE_ROM", "F:/slink-work/cache/polished/release/polishedcrystal-3.2.3.gbc"))
BANK, GATE, NEXT, LAST = 0x0F, 0x47DD, 0x47E0, 0x480D
PINNED_BYTES = bytes.fromhex("218BD2")
PINNED_SITE_HEX = "218bd2fa0cd1"     # all six release bytes at 0f:47DD (flat 0x3C7DD), compared in full by the rival oracle
# trainerclass RIVAL0, RIVAL1, RIVAL2, LYRA1, LYRA2 (src/constants/trainer_constants.asm:95-127). Trainer IDs are NOT
# class constants and are never tested against this set.
RIVAL_CLASSES = frozenset({0x1B, 0x1C, 0x1D, 0x1E, 0x1F})
# docs/polished/RIVAL_STAGING.md section 4: Cherrygrove RIVAL0 trainer 3 fields RATTATA + TOTODILE.
FIXTURE_EXPECT = {"trainer_class": 0x1B, "trainer_id": 3, "enemy_party": 2}
SETUPS = ("played", "synth")
EXPECTATIONS = ("rival", "wild")
BUTTONS = frozenset(("A", "B", "Start", "Select", "Up", "Down", "Left", "Right"))
EXTRA_SYMBOLS = (
    "SendInUserPkmn", "SendInUserPkmn.got_partymon", "wOtherTrainerClass",
    "wOtherTrainerID", "wCurOTMon", "wCurPartyMon", "wOTPartyCount",
)
HOOKS = {"gate": GATE, "next": NEXT, "last": LAST}


def _scan(trace, *, operation=False):
    """Recording-integrity and per-row checks shared by every oracle: (reasons, gates).

    `gates` is None for a malformed trace, else a list of (row, clean) for every bank-and-PC-qualified gate row, where
    `clean` means that row produced no reason of its own. The caller decides what zero or many gates mean.
    """
    why: list[str] = []
    if not isinstance(trace, list) or any(not isinstance(e, dict) for e in trace):
        return ["INVALID_TRACE: expected a list of event objects"], None
    finals = [e for e in trace if e.get("kind") == "final"]
    if len(finals) != 1 or trace[-1] is not finals[0] or finals[0].get("completed") is not True:
        why.append("INCOMPLETE_TRACE: missing unique completed final event at end")
    elif finals[0].get("guest_writes") != 0 or finals[0].get("cpu_changes") != 0:
        why.append("PROBE_MUTATION: final write/register-change counters are not zero")
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    from tools.polished_live.faint_probe import validate_hook_counts

    final = finals[0] if len(finals) == 1 else {}
    raw_trace = copy.deepcopy(trace)
    if final.get("qualification_schema") == "rival-operation/1":
        for row in raw_trace:
            if row.get("kind") in HOOKS:
                row["qualified"] = row.get("raw_qualified")
    why.extend(validate_hook_counts(raw_trace, final, {
        name: {"bank": BANK, "addr": addr} for name, addr in HOOKS.items()
    }))
    counts = final.get("hook_counts")
    if isinstance(counts, dict) and set(counts) == set(HOOKS):
        totals = [v.get("total") if isinstance(v, dict) else None for v in counts.values()]
        if (type(final.get("hook_hits")) is not int or any(type(v) is not int for v in totals)
                or final["hook_hits"] != sum(totals)):
            why.append("MALFORMED_TRACE: hook_hits must equal all callback totals")
    if final.get("driver_errors", 0) != 0:
        why.append("DRIVER_ERROR: final driver error counter is not zero")
    qualified_gates = []
    for e in trace:
        before = len(why)
        kind = e.get("kind")
        if kind in ("guest_write", "cpu_change", "overflow", "driver_error"):
            code = {"guest_write": "GUEST_WRITE", "cpu_change": "CPU_CHANGE",
                    "overflow": "TRACE_OVERFLOW", "driver_error": "DRIVER_ERROR"}[kind]
            why.append(f"{code}: {e}")
        if kind not in HOOKS:
            continue
        addr = HOOKS[kind]
        if e.get("hook_addr") != addr:
            why.append(f"HOOK_ADDRESS: {kind} hook_addr={e.get('hook_addr')!r} != {addr:#06x}")
        matched, bank = e.get("matched"), e.get("bank")
        if type(matched) is not bool or type(bank) is not int or matched != (bank == BANK):
            why.append(f"WRONG_BANK_HIT: {kind} matched={matched!r}, hROMBank={bank!r}")
        if matched is not True or bank != BANK:
            continue
        if kind == "gate":
            if e.get("pc") != GATE:
                code = "PC_IS_NEXT_INSTRUCTION" if e.get("pc") == NEXT else "PC_OTHER"
                pc = e.get("pc")
                observed = f"{pc:#06x}" if type(pc) is int else repr(pc)
                why.append(f"{code}: 0f:47DD callback observed PC={observed}")
            idx, party_idx, count = (e.get(k) for k in ("cur_ot_mon", "cur_party_mon", "ot_party_count"))
            if (not operation and (any(type(v) is not int for v in (idx, party_idx, count))
                    or not 1 <= count <= 6 or not 0 <= idx < count or idx != party_idx)):
                why.append(f"BAD_SELECTED_INDEX: wCurOTMon={idx!r}, wCurPartyMon={party_idx!r}, wOTPartyCount={count!r}")
            if not operation and e.get("mode") != 2:
                why.append(f"NOT_TRAINER_BATTLE: wBattleMode={e.get('mode')!r} != 2")
        elif kind == "next" and e.get("pc") != NEXT:
            why.append(f"NEXT_PC_MISMATCH: 0f:47E0 callback observed PC={e.get('pc')!r}")
        elif kind == "last" and e.get("pc") != LAST:
            why.append(f"LAST_PC_MISMATCH: 0f:480D callback observed PC={e.get('pc')!r}")
        site = e.get("site_bytes")
        try:
            observed_bytes = bytes.fromhex(site) if isinstance(site, str) else b""
        except ValueError:
            observed_bytes = b""
        if len(observed_bytes) != 6 or observed_bytes[:3] != PINNED_BYTES:
            why.append(f"SITE_BYTES_MISMATCH: gate ROM bytes={site!r}, expected six bytes beginning 218bd2")
        if kind == "gate" and e.get("pc") == GATE:
            qualified_gates.append((e, len(why) == before))
    return why, qualified_gates


def evaluate(trace) -> tuple[bool, list[str]]:
    """PASS requires a complete, uncensored recording and at least one qualified gate hit.

    Wrong-bank callbacks retain only their first 16 rows per site; mandatory totals
    account for every callback without consuming protected trace capacity.
    Wrong-PC rows retain full diagnostics and never count as qualified hits.
    """
    why, gates = _scan(trace)
    if gates is None:
        return False, why
    if not gates:
        why.append("NO_GATE_HIT: OPEN; no bank-and-PC-qualified 0f:47DD callback (not a PASS)")
    return not why, why


def _is_hex64(value) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def _is_site_hex(value) -> bool:
    return isinstance(value, str) and len(value) == 12 and all(c in "0123456789abcdef" for c in value)


def _binding_reasons(trace, disclosure_sha256) -> list[str]:
    """The trace must itself say it was recorded as a SYNTH run bound to exactly this disclosure file."""
    final = next((e for e in trace if e.get("kind") == "final"), {})
    if not _is_hex64(disclosure_sha256):
        return [f"SETUP_BINDING: no valid disclosure sha256 supplied ({disclosure_sha256!r})"]
    if final.get("setup") != "synth" or final.get("disclosure_sha256") != disclosure_sha256:
        return [f"SETUP_BINDING: trace final says setup={final.get('setup')!r} "
                f"disclosure_sha256={final.get('disclosure_sha256')!r}, this run is synth/{disclosure_sha256}"]
    return []


def _rival_row_reasons(e, expected, site_hex) -> list[str]:
    """Rival-card requirements for one matched row (gate, next or last), beyond the generic row checks."""
    kind, out = e.get("kind"), []
    site = e.get("site_bytes")
    if site != site_hex and isinstance(site, str) and len(site) == 12:
        out.append(f"SITE_BYTES_NOT_PINNED: {kind} ROM bytes={site!r} != staged {site_hex!r} (all six bytes compared)")
    if kind != "gate":
        return out
    klass, tid = e.get("trainer_class"), e.get("trainer_id")
    if type(klass) is not int or klass not in RIVAL_CLASSES:
        out.append(f"NOT_RIVAL_CLASS: wOtherTrainerClass={klass!r} not in {{1B,1C,1D,1E,1F}}")
    if type(tid) is not int or not 0 <= tid <= 255:
        out.append(f"BAD_TRAINER_ID: wOtherTrainerID={tid!r} is not a byte")
    if expected is not None and type(klass) is int and klass in RIVAL_CLASSES and type(tid) is int and 0 <= tid <= 255:
        got = (klass, tid, e.get("ot_party_count"))
        want = (expected["trainer_class"], expected["trainer_id"], expected["enemy_party"])
        if got != want:
            out.append(f"FIXTURE_IDENTITY_MISMATCH: class {got[0]:02X} id {got[1]:02X} enemy party {got[2]!r} != "
                       f"the staged fixture class {want[0]:02X} id {want[1]:02X} enemy party {want[2]}")
    return out


def _witness_reasons(trace, gate, site_hex, expected=FIXTURE_EXPECT) -> list[str]:
    """gate -> next -> last for the initial send-out: later, same battle identity, same selected indices."""
    start = next(i for i, e in enumerate(trace) if e is gate)
    identity = ("trainer_class", "trainer_id", "ot_party_count", "cur_ot_mon", "cur_party_mon")
    out, position = [], start
    for kind in ("next", "last"):
        found = None
        for i in range(position + 1, len(trace)):
            e = trace[i]
            if e.get("kind") == "gate" and e.get("bank") == BANK and e.get("pc") == GATE:
                break  # a later send-in cannot complete this gate
            if (e.get("kind") == kind and e.get("matched") is True and e.get("pc") == HOOKS[kind]
                    and e.get("bank") == BANK):
                found, position = e, i
                break
        if found is None:
            out.append(f"WITNESS_MISSING: no bank-and-PC-qualified {kind} callback after the initial 0f:47DD gate "
                       f"(ordered gate -> next -> last required)")
            return out
        differs = [k for k in identity if found.get(k) != gate.get(k)]
        if not operation_qualified(found, expected):
            differs.append("operation predicate")
        out += _rival_row_reasons(found, expected, site_hex)
        if differs:
            out.append(f"WITNESS_MISMATCH: {kind} callback differs from the gate in {differs} "
                       f"(identity and selected indices must be unchanged through the send-out)")
    return out


def operation_qualified(row, expected=FIXTURE_EXPECT):
    """Only measured enemy-side rival callbacks qualify; absent side is never inferred."""
    return (row.get("kind") in HOOKS
            and type(row.get("bank")) is int and row["bank"] == BANK
            and type(row.get("pc")) is int and row["pc"] == HOOKS[row["kind"]]
            and type(row.get("battle_turn")) is int and row["battle_turn"] == 1
            and type(row.get("mode")) is int and row["mode"] == 2
            and type(row.get("trainer_class")) is int and row["trainer_class"] in RIVAL_CLASSES
            and type(row.get("trainer_id")) is int and expected is not None
            and row["trainer_id"] == expected["trainer_id"])


def _operation_scan(trace, expected=FIXTURE_EXPECT):
    why, raw_gates = _scan(trace, operation=True)
    if raw_gates is None:
        return why, None
    final = trace[-1] if trace else {}
    current = final.get("qualification_schema") == "rival-operation/1"
    if "qualification_schema" in final and not current:
        why.append("QUALIFICATION_SCHEMA: unknown qualification schema")
    for row in trace:
        if row.get("kind") not in HOOKS:
            continue
        if current and (type(row.get("qualified")) is not bool
                        or row["qualified"] != operation_qualified(row, expected)):
            why.append("QUALIFICATION_MISMATCH: recorded flag differs from measured operation predicate")
        # A missing side prevents qualification only where all the other guards permit it.
        candidate = dict(row, battle_turn=1)
        if operation_qualified(candidate, expected) and type(row.get("battle_turn")) is not int:
            why.append("MISSING_SIDE: OPEN; rival candidate lacks a measured integer hBattleTurn")
    return why, [(r, clean) for r, clean in raw_gates if operation_qualified(r, expected)]


def evaluate_rival(trace, *, disclosure_sha256, expected=FIXTURE_EXPECT, site_hex=PINNED_SITE_HEX):
    """Raw callbacks remain evidence; only operation-qualified callbacks require a rival witness chain."""
    if not _is_site_hex(site_hex):
        raise ValueError(f"site_hex must be six lowercase hex bytes, got {site_hex!r}")
    why, gates = _operation_scan(trace, expected)
    if gates is None:
        return False, why
    why += _binding_reasons(trace, disclosure_sha256)
    if not gates:
        for row in trace:
            if row.get("kind") == "gate" and row.get("bank") == BANK:
                why += _rival_row_reasons(row, expected, site_hex)
                if row.get("mode") != 2:
                    why.append("NOT_TRAINER_BATTLE: excluded raw gate is not a trainer battle")
        why.append("NO_RIVAL_GATE: OPEN; no operation-qualified rival enemy send-in")
        why.append("NO_GATE_HIT: OPEN; no operation-qualified gate (raw hits may exist)")
        return False, why
    for row, _ in gates:
        why += _rival_row_reasons(row, expected, site_hex)
        idx, party_idx, count = (row.get(k) for k in ("cur_ot_mon", "cur_party_mon", "ot_party_count"))
        if (any(type(v) is not int for v in (idx, party_idx, count))
                or not 1 <= count <= 6 or not 0 <= idx < count or idx != party_idx):
            why.append("BAD_SELECTED_INDEX: invalid selected enemy party index")
    # The first next/last callbacks must belong to this same send-in, not a later battle.
    why += _witness_reasons(trace, gates[0][0], site_hex, expected)
    return not why, why


def evaluate_wild_control(trace, *, disclosure_sha256):
    """PASS-as-control requires observed wild mode and zero rival-operation-qualified gates."""
    why, gates = _operation_scan(trace)
    if gates is None:
        return False, why
    why += _binding_reasons(trace, disclosure_sha256)
    modes = [e.get("mode") for e in trace if e.get("kind") == "mode"]
    if gates:
        why.append(f"WILD_QUALIFIED_HIT: {len(gates)} rival-operation gate(s) in wild control")
    if 2 in [m for m in modes if type(m) is int]:
        why.append("CONTROL_TRAINER_BATTLE: the control run entered a trainer battle (wBattleMode==2)")
    if not any(type(m) is int and m == 1 for m in modes):
        why.append("WILD_NOT_OBSERVED: OPEN; no wBattleMode==1 transition row")
    return not why, why


def require_pinned_site(site: str) -> str:
    """The rival card compares all six site bytes; a staged overlay that differs from the release bytes is refused."""
    if site != PINNED_SITE_HEX:
        raise ValueError(f"staged 0f:47DD ROM bytes {site!r} != pinned six-byte site {PINNED_SITE_HEX}")
    return site


def verdict(reasons: list[str]) -> str:
    """Keep PC findings and zero-hit OPEN distinguishable from generic failures."""
    for code in ("PC_IS_NEXT_INSTRUCTION", "PC_OTHER", "MISSING_SIDE", "NO_GATE_HIT", "WILD_NOT_OBSERVED"):
        if any(r.startswith(code + ":") for r in reasons):
            return code
    return "FAIL" if reasons else "PASS"


def parse_route(path: Path | None, frame_cap: int) -> list[dict]:
    """Validate timed native button holds; reject hidden/synthetic setup fields."""
    if path is None:
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or set(data) != {"steps"} or not isinstance(data["steps"], list):
        raise ValueError("route must be an object containing only a steps list")
    steps, total = [], 0
    for i, step in enumerate(data["steps"]):
        if not isinstance(step, dict) or set(step) != {"frames", "buttons"}:
            raise ValueError(f"route step {i} must contain only frames and buttons")
        frames, buttons = step["frames"], step["buttons"]
        if type(frames) is not int or frames <= 0:
            raise ValueError(f"route step {i} frames must be a positive integer")
        if (not isinstance(buttons, list) or any(not isinstance(b, str) or b not in BUTTONS for b in buttons)
                or len(set(buttons)) != len(buttons)):
            raise ValueError(f"route step {i} buttons must be unique native button names")
        total += frames
        steps.append({"frames": frames, "buttons": buttons})
    if total > frame_cap:
        raise ValueError(f"route needs {total} frames, exceeds --frames {frame_cap}")
    return steps


def load_disclosure(path: Path, fixture_bytes: bytes) -> dict:
    """Read a derive_rival_save disclosure and bind it to this fixture, or raise ValueError.

    role "derivative": the fixture is the disclosure output; role "source": it is the declared input (the control run).
    The returned sha256 is of the disclosure FILE, and is what the Lua trace repeats and the oracle checks.
    """
    try:
        raw = Path(path).read_bytes()
        doc = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"disclosure {path}: unreadable or not JSON ({exc})") from exc
    if not isinstance(doc, dict):
        raise ValueError(f"disclosure {path}: expected a JSON object")
    if doc.get("synth") is not True:
        raise ValueError(f"disclosure {path}: synth is not true")
    statement = doc.get("statement")
    if not isinstance(statement, str) or "SYNTH" not in statement:
        raise ValueError(f"disclosure {path}: statement must declare SYNTH")
    out_sha, in_sha = doc.get("output_sha256"), doc.get("input_sha256")
    if not _is_hex64(out_sha) or not _is_hex64(in_sha):
        raise ValueError(f"disclosure {path}: input_sha256 and output_sha256 must be lowercase sha256 hex")
    fixture_sha = hashlib.sha256(fixture_bytes).hexdigest()
    if fixture_sha == out_sha:
        role = "derivative"
    elif fixture_sha == in_sha:
        role = "source"
    else:
        raise ValueError(f"disclosure {path} names neither output {out_sha} nor input {in_sha} for fixture {fixture_sha}")
    return {"role": role, "sha256": hashlib.sha256(raw).hexdigest(), "fixture_sha256": fixture_sha, "document": doc}


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--rejudge", type=Path, help="offline trace.json; never starts an emulator or rewrites evidence")
    ap.add_argument("--setup", choices=SETUPS, required=True)
    ap.add_argument("--disclosure", type=Path, help="synth only: derive_rival_save disclosure JSON naming --fixture")
    ap.add_argument("--expect", choices=EXPECTATIONS, help="synth only: rival (default) or wild control oracle")
    ap.add_argument("--fixture", type=Path, required=True, help="SaveRAM, copied unchanged")
    ap.add_argument("--calibrate", action="store_true", help="SYNTH only: source-derived native route with bounded read-only feedback")
    ap.add_argument("--route", type=Path, help="timed native input from emulator boot; optional for idle observation")
    ap.add_argument("--frames", type=int, default=12000, help="total observation frames, including route")
    ap.add_argument("--trace-cap", type=int, default=4096, help="maximum bank-matched hook rows; overflow fails the oracle")
    ap.add_argument("--timeout", type=int, default=600, help="wall-clock deadline in seconds")
    args = ap.parse_args(argv)
    for name in ("frames", "trace_cap", "timeout"):
        if getattr(args, name) <= 0:
            ap.error(f"--{name.replace('_', '-')} must be positive")
    if not args.fixture.is_file():
        ap.error(f"fixture does not exist: {args.fixture}")
    if args.rejudge is not None and (args.calibrate or args.route is not None or args.setup != "synth"):
        ap.error("--rejudge requires synth, no route and no calibration")
    if args.calibrate and (args.setup != "synth" or args.route is not None):
        ap.error("--calibrate requires --setup synth and no --route")
    args.disclosure_info = args.disclosure_sha256 = None
    if args.setup == "played":
        if args.disclosure is not None or args.expect is not None:
            ap.error("--disclosure and --expect belong to --setup synth; played runs the generic oracle")
    else:
        if args.disclosure is None:
            ap.error("--setup synth requires --disclosure (a SYNTH run must be bound to its disclosure)")
        args.expect = args.expect or "rival"
        try:
            args.disclosure_info = load_disclosure(args.disclosure, args.fixture.read_bytes())
        except ValueError as exc:
            ap.error(str(exc))
        args.disclosure_sha256 = args.disclosure_info["sha256"]
    try:
        args.steps = parse_route(args.route, args.frames)
    except (OSError, ValueError) as exc:
        ap.error(str(exc))
    return args


def check_site_bytes(rom: bytes) -> str:
    """Pure prelaunch check of the pinned bank's instruction, plus six bytes for evidence."""
    offset = BANK * 0x4000 + GATE - 0x4000
    site = rom[offset:offset + 6]
    if len(site) != 6 or site[:3] != PINNED_BYTES:
        raise ValueError(f"staged 0f:47DD ROM bytes {site.hex()} != pinned prefix 218bd2 (six bytes required)")
    return site.hex()


def check_pack_facts() -> None:
    """Refuse drift between this bounded probe, checkpoint, engine sites and overlay symbols."""
    pack = REPO / "data/games/polished_crystal"
    checkpoint = json.loads((pack / "write_checkpoint.json").read_text(encoding="utf-8"))
    signals = json.loads((pack / "engine_signals.json").read_text(encoding="utf-8"))
    gate = checkpoint["titles"]["polished_crystal"]["battle_hold"]["rival_swap_gate"]
    sites = signals["titles"]["polished_crystal"]["sites"]
    signal, last = sites["rival_swap_gate"], sites["rival_swap_last_consumption"]
    offset = BANK * 0x4000 + GATE - 0x4000
    if ((gate["bank"], gate["pc"], gate["rom_offset"]) != (BANK, GATE, offset)
            or (signal["bank"], signal["addr"], signal["symbol_offset"], signal["expected_hex"].upper())
            != (BANK, GATE, 149, "218BD2")
            or (last["bank"], last["addr"], last["symbol_offset"]) != (BANK, LAST, 197)):
        raise ValueError("pinned rival gate/last-consumption pack facts differ from this probe")
    symbols = {}
    for line in (REPO / "data/polished/polished_slink.sym").read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) == 2 and ":" in parts[0]:
            symbols.setdefault(parts[1], tuple(int(v, 16) for v in parts[0].split(":")))
    if (symbols.get("SendInUserPkmn") != (BANK, GATE - 149)
            or symbols.get("SendInUserPkmn.got_partymon") != (BANK, NEXT)
            or symbols.get("hROMBank") != (0, 0xFF87)):
        raise ValueError("pinned SendInUserPkmn/next/hROMBank symbols differ from this probe")


def lane_prefix(setup: str) -> str:
    return f"{setup}-"


def setup_line(args, fixture_digest: str) -> str:
    """The header line that tells a reader exactly how the fixture came to be. Never calls a SYNTH run played."""
    if args.setup == "played":
        return f"[probe] setup played; no synthetic guest setup; fixture {args.fixture} sha256 {fixture_digest}"
    info = args.disclosure_info
    return (f"[probe] setup SYNTH (O-33), fixture is the {info['role']} of the disclosed Cherrygrove-scene derivation; "
            f"disclosure {args.disclosure} sha256 {args.disclosure_sha256}; fixture {args.fixture} sha256 {fixture_digest}; "
            f"the battle under test runs natively; the probe itself performs no guest or CPU writes; oracle {args.expect}")


def run_oracle(args, trace, site: str):
    """played -> the generic oracle (unchanged); synth -> the rival-card oracle or the wild control, bound to the disclosure."""
    if args.setup == "played":
        return evaluate(trace)
    if args.expect == "wild":
        return evaluate_wild_control(trace, disclosure_sha256=args.disclosure_sha256)
    return evaluate_rival(trace, disclosure_sha256=args.disclosure_sha256, site_hex=site)


def stage(lane: Path, full_site: bool = False) -> tuple[Path, str, str]:
    check_pack_facts()
    provenance = json.loads((REPO / "data/polished/overlay_provenance.json").read_text(encoding="utf-8"))
    from tools.polished_live.runtime import pinned_rom_bytes
    data = pinned_rom_bytes(RELEASE, provenance["output"]["sha1"],
                            patch=REPO / "patch/dist/SLink-Polished.ups",
                            base_sha1=provenance["base_sha1"])
    sha1 = hashlib.sha1(data).hexdigest()
    site = check_site_bytes(data)
    if full_site:
        require_pinned_site(site)
    rom = lane / "rom/pol_overlay.gbc"  # harness SAVE_NAME is pol overlay.SaveRAM
    rom.parent.mkdir(parents=True)
    rom.write_bytes(data)
    return rom, sha1, site


def recording_reasons(text: str, elapsed: float = 0, timeout: float = float("inf")) -> list[str]:
    """Missing RESULT, early exit/crash/deadline, and driver failure are never evidence of success."""
    reasons = []
    results = [line for line in text.splitlines() if line.startswith("RESULT:")]
    if len(results) != 1 or not results[0].startswith("RESULT: PASS rival-gate-probe recording "):
        reasons.append("INCOMPLETE_RECORDING: missing unique successful driver RESULT (early exit, deadline or crash)")
    if elapsed >= timeout:
        reasons.append(f"DEADLINE: launch/recording took {elapsed:.3f}s >= {timeout}s; late RESULT is not accepted")
    return reasons


def rejudge(args):
    """A new labelled analysis, never overwrite the recording or its original result."""
    raw = args.rejudge.read_bytes()
    trace = json.loads(raw)
    _, reasons = run_oracle(args, trace, PINNED_SITE_HEX)
    hooks = [r for r in trace if isinstance(r, dict) and r.get("kind") in HOOKS]
    missing = sum("battle_turn" not in r for r in hooks if r.get("bank") == BANK)
    qualified = [r for r in hooks if operation_qualified(r)]
    result = {"analysis": "OFFLINE REJUDGEMENT; original recording unchanged", "setup": "SYNTH",
              "trace_sha256": hashlib.sha256(raw).hexdigest(), "oracle": args.expect,
              "verdict": verdict(reasons), "reasons": reasons,
              "status": "PASS" if not reasons else ("OPEN" if verdict(reasons) in
                  ("MISSING_SIDE", "NO_GATE_HIT", "WILD_NOT_OBSERVED") else "FAIL"),
              "missing_side_rows": missing, "qualified_gate_frames": [r["frame"] for r in qualified if r["kind"] == "gate"]}
    if missing:
        conditional = copy.deepcopy(trace)
        for row in conditional:
            if row.get("kind") in HOOKS and "battle_turn" not in row:
                row["battle_turn"] = 1
        _, conditional_reasons = run_oracle(args, conditional, PINNED_SITE_HEX)
        result["conditional_source_model"] = {
            "assumption": "UNMEASURED side=1, based on source enemy-pointer branch; NOT PHYSICAL PASS",
            "verdict": verdict(conditional_reasons), "reasons": conditional_reasons}
    print(json.dumps(result, indent=2))
    return 0 if not reasons else 1


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.rejudge is not None:
        return rejudge(args)
    WORK.mkdir(parents=True, exist_ok=True)
    lane = Path(tempfile.mkdtemp(prefix=lane_prefix(args.setup), dir=WORK))
    rom, sha1, site = stage(lane, full_site=args.setup == "synth")
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from dataclasses import replace

    from tools.polished_live import harness
    launch_config = replace(harness.launch_config(dict(os.environ, POL_LANE=str(lane), POL_KIND="overlay")),
                            rom_src=rom, rom=rom,
                            symbols=tuple(dict.fromkeys((*harness.SYMBOLS, *EXTRA_SYMBOLS, "hBattleTurn"))))
    harness.prepare_save(launch_config, args.fixture)
    fixture_digest = hashlib.sha256(args.fixture.read_bytes()).hexdigest()
    run = lane / "probe"
    run.mkdir()
    config = run / "input.json"
    settings = {"steps": args.steps, "frames": args.frames, "trace_cap": args.trace_cap, "setup": args.setup,
                "calibrate": args.calibrate, "expect": args.expect, "expected": FIXTURE_EXPECT}
    if args.setup == "synth":
        settings["disclosure_sha256"] = args.disclosure_sha256
    config.write_text(json.dumps(settings), encoding="utf-8")
    header = [setup_line(args, fixture_digest),
              f"[probe] overlay sha1 {sha1}; gate 0f:47DD ROM bytes {site}",
              f"[probe] native route {args.route}; canonical input {config}; frames {args.frames}; cap {args.trace_cap}"]
    print("\n".join(header), flush=True)
    started = time.monotonic()
    text, pid = harness.launch(launch_config, "tools/polished_live/rival_gate_probe.lua", run,
                               {"POL_PROBE_CONFIG": config.as_posix()}, args.timeout)
    # Conservative: include harness shutdown/flush time rather than accepting a RESULT seen after its deadline.
    reasons = recording_reasons(text, time.monotonic() - started, args.timeout)
    try:
        trace = json.loads((run / "trace.json").read_text(encoding="utf-8"))
        _, oracle_reasons = run_oracle(args, trace, site)
        reasons.extend(oracle_reasons)
    except (OSError, ValueError) as exc:
        reasons.append(f"INVALID_TRACE: {exc}")
    finding = verdict(reasons)
    rows = header + [f"  [FAIL] {r}" for r in reasons]
    label = args.setup if args.setup == "played" else f"synth/{args.expect}"
    rows.append(f"RESULT: {finding} rival-gate-probe ({label}, EmuHawk pid {pid}, {len(reasons)} reasons)")
    with (run / "result.txt").open("a", encoding="utf-8") as fh:
        fh.write("\n" + "\n".join(rows) + "\n")
    print("\n".join(rows))
    print(f"[probe] evidence {run / 'trace.json'}; result {run / 'result.txt'}")
    return 0 if not reasons else 1


if __name__ == "__main__":
    raise SystemExit(main())
