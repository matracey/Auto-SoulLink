#!/usr/bin/env python3
"""Read-only natural-faint recording. Authoring/tests do NOT authorize a live run.

python tools/polished_live/faint_probe.py --setup played --fixture SAVE --route ROUTE --dry-run
Route: {"steps":[{"frames":120,"buttons":[]}, ...]}, native buttons from boot.
PASS certifies complete recording/order with a qualified pre-copy hit, NOT that
a faint/whiteout occurred, nor that the F1 writer is qualified. No client/server.
Wrong-bank samples are bounded to 16/site with full callback accounting; protected
rows keep their own cap. SaveRAM accepts exactly 32 KiB, optionally a 22-byte RTC
footer; all fixture bytes are preserved for provenance and staging.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import time
import uuid
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
WORK = Path("F:/slink-work/lanes/pol-faint-probe")
RELEASE = Path("F:/slink-work/cache/polished/release/polishedcrystal-3.2.3.gbc")
SCHEMA = "polished-faint-probe-v1"
SITES = {
    "resolve": ("ResolveFaints", 0, 0x44AF),
    "pre_copy": ("ResolveFaints.no_fainted_mons", 1, 0x44C8),
    "copy_call": ("ResolveFaints.no_fainted_mons", 3, 0x44CA),
    "copy_return": ("ResolveFaints.no_fainted_mons", 6, 0x44CD),
    "faint": ("FaintUserPokemon", 0, None),
    "lost": ("LostBattle", 0, 0x4FF6),
}
FIELDS = {
    "rombank": "hROMBank",
    "battle_hp": "wBattleMonHP",
    "battle_status": "wBattleMonStatus",
    "order": "wWhichMonFaintedFirst",
    "substatus2": "wPlayerSubStatus2",
    "turn": "hBattleTurn",
    "mode": "wBattleMode",
    "slot": "wCurBattleMon",
    "count": "wPartyCount",
    "party_hp": "wPartyMon1HP",
    "party_status": "wPartyMon1Status",
}


def digest(data, algorithm="sha256"):
    return hashlib.new(algorithm, data).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def read_symbols(path):
    from tools.polished_live.runtime import read_symbols as read
    return read(path, reject_duplicates=True)


def derive_contract(symbols, rom):
    """All Lua coordinates are supplied here from provenance-bound symbols/ROM."""
    sites = {}
    turn = symbols["hBattleTurn"][1] & 255

    def call_bytes(name):
        bank, address = symbols[name]
        if bank != 0:
            raise ValueError(f"{name} must be ROM0")
        return bytes((0xCD, address & 255, address >> 8))

    expected = {
        "resolve": bytes((0xF0, turn, 0xF5)),
        "pre_copy": bytes((0xE0, turn)),
        "copy_call": call_bytes("UpdateBattleMonInParty"),
        "copy_return": call_bytes("UpdateEnemyMonInParty"),
        "faint": call_bytes("HasUserFainted") + b"\xc0",
    }
    for name, (symbol, delta, pin) in SITES.items():
        bank, base = symbols[symbol]
        address = base + delta
        if bank != 0x0F or not 0x4000 <= address < 0x8000 or (pin is not None and address != pin):
            raise ValueError(f"{name}: native symbol/site drift")
        offset = bank * 0x4000 + address - 0x4000
        size = len(expected.get(name, b"\0\0\0"))
        raw = rom[offset : offset + size]
        if len(raw) != size or (name in expected and raw != expected[name]):
            raise ValueError(f"{name}: native instruction bytes differ")
        sites[name] = {"bank": bank, "addr": address, "rom_offset": offset, "bytes": raw.hex()}
    stride = symbols["wPartyMon2"][1] - symbols["wPartyMon1"][1]
    if stride != 48:
        raise ValueError("party stride differs from pinned layout")
    ram = {}
    for field, label in FIELDS.items():
        bank, address = symbols[label]
        if bank == 0 and 0xFF80 <= address < 0xFFFF:
            domain, offset = "System Bus", address
        elif bank == 0 and 0xC000 <= address < 0xD000:
            domain, offset = "WRAM", address - 0xC000
        elif bank == 1 and 0xD000 <= address < 0xE000:
            domain, offset = "WRAM", 0x1000 + address - 0xD000
        else:
            raise ValueError(f"{label}: unsupported RAM mapping")
        ram[field] = {"bank": bank, "addr": address, "domain": domain, "offset": offset}
    return {
        "schema": SCHEMA,
        "sites": sites,
        "ram": ram,
        "party_stride": stride,
        "party_capacity": 6,
        "fainted_mask": 4,
        "player_turn": 0,
    }


def prepare():
    """Read/verify only; called before even creating a lane, including dry-run."""
    prov_path = REPO / "data/polished/overlay_provenance.json"
    sym_path = REPO / "data/polished/polished_slink.sym"
    ups_path = REPO / "patch/dist/SLink-Polished.ups"
    prov_raw, sym_raw = prov_path.read_bytes(), sym_path.read_bytes()
    prov = json.loads(prov_raw)
    if digest(sym_raw) != prov["symbols"][sym_path.name]:
        raise ValueError("symbols differ from overlay provenance")
    from tools.polished_live.runtime import pinned_rom_bytes
    ups = ups_path.read_bytes()
    rom = pinned_rom_bytes(RELEASE, prov["output"]["sha1"], patch=ups_path, base_sha1=prov["base_sha1"])
    contract = derive_contract(read_symbols(sym_path), rom)
    provenance = {
        "rom_sha1": digest(rom, "sha1"),
        "symbols_sha256": digest(sym_raw),
        "provenance_sha256": digest(prov_raw),
        "ups_sha256": digest(ups),
        "contract_sha256": digest(canonical(contract)),
        "driver_sha256": digest((REPO / "tools/polished_live/faint_probe.lua").read_bytes()),
    }
    return rom, contract, provenance


def integer(value, low, high):
    return type(value) is int and low <= value <= high


def validate_hook_counts(trace, final, sites) -> list[str]:
    """Validate full callback totals against protected rows and bounded bank samples.

    Totals beyond the first 16 wrong-bank rows are aggregate claims, not an
    independently reconstructed callback log. ROM0 ignores the bank shadow.
    """
    bad = []
    limit = 16
    maximum = 2**53 - 1
    if not isinstance(final, dict) or not isinstance(trace, list):
        return ["MALFORMED: callback accounting requires trace and final objects"]
    if (
        type(final.get("wrong_bank_sample_limit")) is not int
        or final["wrong_bank_sample_limit"] != limit
    ):
        bad.append("MALFORMED: wrong_bank_sample_limit must be 16")
    counters = final.get("hook_counts")
    if not isinstance(counters, dict) or set(counters) != set(sites):
        return bad + ["MALFORMED: hook_counts must contain exactly the registered sites"]
    observed = {
        name: {"qualified": 0, "wrong_pc": 0, "wrong_banks": Counter()}
        for name in sites
    }
    for row in trace:
        if not isinstance(row, dict):
            bad.append("MALFORMED: hook row must be an object")
            continue
        name = row.get("hook_site", row.get("kind"))
        if not isinstance(name, str):
            bad.append("MALFORMED: hook site must be text")
            continue
        if name not in sites:
            if "hook_site" in row:
                bad.append(f"MALFORMED: unknown hook site {name!r}")
            continue
        site = sites[name]
        if (
            not integer(row.get("bank"), 0, 255)
            or not integer(row.get("pc"), 0, 65535)
            or type(row.get("hook_addr")) is not int
            or row["hook_addr"] != site["addr"]
        ):
            bad.append(f"MALFORMED: {name} hook coordinates")
            continue
        matched = site["addr"] < 0x4000 or row["bank"] == site["bank"]
        qualified = matched and row["pc"] == site["addr"]
        if row.get("matched") is not matched or row.get("qualified") is not qualified:
            bad.append(f"MALFORMED: {name} hook qualification")
        if qualified:
            observed[name]["qualified"] += 1
        elif matched:
            observed[name]["wrong_pc"] += 1
        else:
            observed[name]["wrong_banks"][str(row["bank"])] += 1
    for name, counter in counters.items():
        if not isinstance(counter, dict) or any(
            not integer(counter.get(field), 0, maximum)
            for field in ("total", "qualified", "wrong_pc", "wrong_bank_samples")
        ):
            bad.append(f"MALFORMED: {name} callback counters")
            continue
        banks = counter.get("wrong_banks")
        # Accept exactly empty [] from synthetic Lua codecs; nonempty arrays are malformed.
        if isinstance(banks, list) and not banks:
            banks = {}
        if not isinstance(banks, dict) or any(
            not isinstance(bank, str)
            or not 1 <= len(bank) <= 3
            or not bank.isascii()
            or not bank.isdecimal()
            or str(int(bank)) != bank
            or not 0 <= int(bank) <= 255
            or not integer(count, 1, maximum)
            for bank, count in banks.items()
        ):
            bad.append(f"MALFORMED: {name} wrong_banks")
            continue
        wrong = sum(banks.values())
        if sites[name]["addr"] < 0x4000 and wrong:
            bad.append(f"MALFORMED: {name} ROM0 cannot have wrong-bank callbacks")
        if str(sites[name]["bank"]) in banks:
            bad.append(f"MALFORMED: {name} expected bank cannot be a wrong bank")
        retained = observed[name]
        samples = sum(retained["wrong_banks"].values())
        if (
            counter["total"] != counter["qualified"] + counter["wrong_pc"] + wrong
            or counter["qualified"] != retained["qualified"]
            or counter["wrong_pc"] != retained["wrong_pc"]
            or any(count > banks.get(bank, 0) for bank, count in retained["wrong_banks"].items())
            or samples != min(limit, wrong)
            or counter["wrong_bank_samples"] != samples
        ):
            bad.append(f"MALFORMED: {name} callback accounting disagrees with retained rows")
    return bad


def evaluate(trace, config):
    """Return (named verdict, diagnostics). Incomplete/corrupt evidence outranks findings.

    `matched` means bank matched; `qualified` requires PC as well. A bank-matched
    wrong PC is retained as WRONG_PC, never discarded as an ordinary bank miss.
    No cryptographic authenticity is claimed for self-consistently fabricated traces.
    """
    bad, pcs, order_errors, missing_faint = [], [], [], []
    if not isinstance(trace, list) or len(trace) < 2 or any(not isinstance(e, dict) for e in trace):
        return "FAIL", ["MALFORMED: expected begin, rows, final"]
    contract = config["contract"]
    expected_proof = {"run_id": config["run_id"], "provenance": config["provenance"]}
    if trace[0].get("kind") != "begin" or any(
        trace[0].get(k) != v for k, v in expected_proof.items()
    ):
        bad.append("PROVENANCE: begin differs from expected run/inputs")
    final = trace[-1]
    if final.get("kind") != "final" or final.get("completed") is not True:
        bad.append("INCOMPLETE: unique completed final event required")
    bad.extend(validate_hook_counts(trace, final, contract["sites"]))
    for field in ("guest_writes", "cpu_changes", "overflows", "driver_errors"):
        if type(final.get(field)) is not int or final[field] != 0:
            bad.append(f"INCOMPLETE: final {field} not zero")
    if final.get("elapsed") != config["frames"] or type(final.get("elapsed")) is not int:
        bad.append("INCOMPLETE: frame budget not completed")
    protected, last_frame, pre_hits, active = 0, -1, 0, None
    for ordinal, row in enumerate(trace, 1):
        if type(row.get("ord")) is not int or row["ord"] != ordinal:
            bad.append(f"ORDINAL: row {ordinal} missing/reordered")
        frame = row.get("frame")
        if not integer(frame, 0, 2**53) or frame < last_frame:
            bad.append(f"FRAME: row {ordinal} invalid/backwards")
        else:
            last_frame = frame
        kind = row.get("kind")
        if not isinstance(kind, str):
            bad.append("MALFORMED: event kind is not text")
            continue
        if kind in ("begin", "final"):
            if (kind == "begin" and ordinal != 1) or (kind == "final" and ordinal != len(trace)):
                bad.append("INCOMPLETE: duplicate/misplaced envelope")
            continue
        if kind not in contract["sites"]:
            bad.append(f"RECORDING: {kind!r}")  # includes overflow, mutation and driver errors
            continue
        site = contract["sites"][kind]
        if row.get("hook_addr") != site["addr"] or type(row.get("hook_addr")) is not int:
            bad.append(f"HOOK: {kind} address mismatch")
        if row.get("hook_bytes") != site["bytes"]:
            bad.append(f"ROM: {kind} bytes differ")
        valid = True
        for key, hi in (
            ("bank", 255),
            ("pc", 65535),
            ("sp", 65535),
            ("battle_status", 255),
            ("order", 255),
            ("substatus2", 255),
            ("turn", 255),
            ("mode", 255),
            ("slot", 255),
            ("count", 255),
        ):
            if not integer(row.get(key), 0, hi):
                bad.append(f"FIELD: {kind}.{key}")
                valid = False
        bhp = row.get("battle_hp_bytes")
        if (
            not isinstance(bhp, list)
            or len(bhp) != 2
            or any(not integer(v, 0, 255) for v in bhp)
            or type(row.get("battle_hp")) is not int
            or row["battle_hp"] != bhp[0] * 256 + bhp[1]
        ):
            bad.append(f"FIELD: {kind}.battle_hp")
            valid = False
        if not valid:
            continue
        matched = site["addr"] < 0x4000 or row["bank"] == site["bank"]
        qualified = matched and row["pc"] == site["addr"]
        if matched:
            protected += 1
        if row.get("matched") is not matched or row.get("qualified") is not qualified:
            bad.append(f"QUALIFICATION: {kind} forged flag")
        slot_valid = 1 <= row["count"] <= contract["party_capacity"] and row["slot"] < row["count"]
        if row.get("party_slot_valid") is not slot_valid:
            bad.append(f"FIELD: {kind}.party_slot_valid")
        php = row.get("party_hp_bytes")
        if slot_valid:
            if (
                not isinstance(php, list)
                or len(php) != 2
                or any(not integer(v, 0, 255) for v in php)
                or type(row.get("party_hp")) is not int
                or row["party_hp"] != php[0] * 256 + php[1]
                or not integer(row.get("party_status"), 0, 255)
            ):
                bad.append(f"FIELD: {kind}.party_hp/status")
        elif (
            php is not False
            or row.get("party_hp") is not False
            or row.get("party_status") is not False
        ):
            bad.append(f"FIELD: {kind} invalid slot must not read arbitrary party memory")
        if matched and not qualified:
            pcs.append(f"WRONG_PC: {kind} hook={site['addr']:#06x} observed={row['pc']:#06x}")
        if not qualified:
            continue
        if (
            not slot_valid
            or row["mode"] not in (1, 2)
            or row["turn"] not in (0, 1)
            or row["order"] not in (0, 1, 2)
        ):
            bad.append(f"CONTEXT: {kind} invalid qualified battle image")
        if kind == "pre_copy":
            pre_hits += 1
        if kind == "resolve":
            if active and active["stage"] != "copy_return":
                order_errors.append("ORDER_VIOLATION: next ResolveFaints before prior return")
            active = {"stage": "resolve", "faints": [], "slot": row["slot"], "pre": None}
        elif kind == "faint":
            if not active or active["stage"] != "resolve":
                order_errors.append(
                    "ORDER_VIOLATION: FaintUserPokemon outside entry/pre-copy interval"
                )
            else:
                active["faints"].append(row)
        elif kind == "lost":
            if not active or active["stage"] != "copy_return":
                order_errors.append("ORDER_VIOLATION: LostBattle before copyback return")
        else:
            previous = {"pre_copy": "resolve", "copy_call": "pre_copy", "copy_return": "copy_call"}[
                kind
            ]
            if not active or active["stage"] != previous or row["slot"] != active["slot"]:
                order_errors.append(f"ORDER_VIOLATION: {kind} without matching {previous}/slot")
                continue
            active["stage"] = kind
            if kind == "pre_copy":
                active["pre"] = row
            if kind == "copy_return":
                pre = active["pre"]
                if pre["battle_hp"] == 0 and pre["order"] == 0 and not active["faints"]:
                    missing_faint.append(
                        f"FAINT_NOT_OBSERVED_AT_44CD: pass pre-copy ordinal {pre['ord']}; "
                        f"return ordinal {ordinal}, FAINTED={bool(row['substatus2'] & contract['fainted_mask'])}"
                    )
    if active and active["stage"] != "copy_return":
        order_errors.append("ORDER_VIOLATION: recording ended inside ResolveFaints pass")
    hook_counts = final.get("hook_counts")
    totals = (
        {name: counter["total"] for name, counter in hook_counts.items()}
        if isinstance(hook_counts, dict)
        and set(hook_counts) == set(contract["sites"])
        and all(isinstance(counter, dict) and integer(counter.get("total"), 0, 2**53)
                for counter in hook_counts.values())
        else {}
    )
    if (
        type(final.get("hook_hits")) is not int
        or final["hook_hits"] != sum(totals.values())
        or not isinstance(final.get("counts"), dict)
        or any(type(v) is not int for v in final.get("counts", {}).values())
        or final.get("counts") != totals
        or protected >= config["trace_cap"]
    ):
        bad.append("CENSORED: counters/protected cap disagree with callback accounting")
    if (
        integer(trace[0].get("frame"), 0, 2**53)
        and integer(final.get("frame"), 0, 2**53)
        and final["frame"] - trace[0]["frame"] != config["frames"]
    ):
        bad.append("INCOMPLETE: observed frame interval differs from requested frames")
    if bad:
        return "FAIL", bad + pcs + order_errors + missing_faint
    if pcs:
        return "WRONG_PC", pcs + order_errors
    if order_errors:
        return "ORDER_VIOLATION", order_errors
    if not pre_hits:
        return "NO_HIT", ["OPEN: no qualified 44c8 pre-copy hit"]
    if missing_faint:
        return "FAINT_NOT_OBSERVED_AT_44CD", missing_faint
    return "PASS", [
        f"complete recording: {pre_hits} qualified pre-copy pass(es); not animation qualification"
    ]


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--setup", choices=("played",), required=True)
    ap.add_argument("--fixture", type=Path, required=True)
    ap.add_argument("--route", type=Path)
    ap.add_argument("--frames", type=int, default=12000)
    ap.add_argument("--trace-cap", type=int, default=20000)
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    if (
        not 1 <= args.frames <= 10_000_000
        or not 1 <= args.trace_cap <= 100_000
        or args.timeout <= 0
    ):
        ap.error("invalid frames/trace-cap/timeout bound")
    # Reuse the existing strict native-buttons-only route schema, no synthetic fields.
    sys.path.insert(0, str(REPO))
    from tools.polished_live.rival_gate_probe import parse_route

    try:
        args.fixture_bytes = args.fixture.read_bytes()
        # Preserve native SaveRAM verbatim, including BizHawk's optional 22-byte RTC footer.
        if len(args.fixture_bytes) not in (32768, 32790):
            raise ValueError("native Polished SaveRAM must be 32768 bytes")
        args.steps = parse_route(args.route, args.frames)
    except (OSError, ValueError) as exc:
        ap.error(str(exc))
    return args


def recording_reasons(text, elapsed, timeout):
    rows = [r for r in text.splitlines() if r.startswith("RESULT:")]
    why = []
    if len(rows) != 1 or not rows[0].startswith("RESULT: PASS faint-probe recording "):
        why.append("FAIL: missing unique successful recording RESULT")
    if elapsed >= timeout:
        why.append("FAIL: recording deadline reached")
    return why


def main(argv=None):
    args = parse_args(argv)
    try:
        rom, contract, proof = prepare()
    except (OSError, ValueError, KeyError) as exc:
        print(f"RESULT: FAIL faint-probe preparation: {exc}")
        return 1
    proof.update(
        fixture_sha256=digest(args.fixture_bytes), route_sha256=digest(canonical(args.steps))
    )
    if args.dry_run:
        print(
            json.dumps({"dry_run": True, "contract": contract, "provenance": proof}, sort_keys=True)
        )
        return 0  # NO lane, environment mutation, harness import or process launch
    WORK.mkdir(parents=True, exist_ok=True)
    lane = Path(tempfile.mkdtemp(prefix="played-", dir=WORK))
    path = lane / "rom/pol overlay.gbc"
    path.parent.mkdir()
    path.write_bytes(rom)
    config = {
        "run_id": uuid.uuid4().hex,
        "contract": contract,
        "provenance": proof,
        "frames": args.frames,
        "trace_cap": args.trace_cap,
        "steps": args.steps,
    }
    run = lane / "probe"
    run.mkdir()
    config_path = run / "input.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from dataclasses import replace

    from tools.polished_live import harness
    launch_config = replace(harness.launch_config(dict(os.environ, POL_LANE=str(lane), POL_KIND="overlay")),
                            rom_src=path, rom=path, sram=lane / "sram", save_name=path.stem + ".SaveRAM")
    launch_config.sram.mkdir()
    # Copy the exact bytes verified before launch, not a possibly changed external file.
    (launch_config.sram / launch_config.save_name).write_bytes(args.fixture_bytes)
    started = time.monotonic()
    pid = None
    try:
        text, pid = harness.launch(
            launch_config,
            "tools/polished_live/faint_probe.lua",
            run,
            {"POL_PROBE_CONFIG": config_path.as_posix()},
            args.timeout,
        )
        errors = recording_reasons(text, time.monotonic() - started, args.timeout)
    except Exception as exc:
        # harness owns cleanup of its launched PID, including exceptional exits.
        errors = [f"FAIL: launch/recording {type(exc).__name__}: {exc}"]
    try:
        finding, reasons = evaluate(json.loads((run / "trace.json").read_text()), config)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        finding, reasons = "FAIL", [str(exc)]
    if errors:
        finding, reasons = "FAIL", errors + reasons
    report = f"RESULT: {finding} faint-probe (played; owned PID {pid})\n" + "\n".join(reasons)
    (run / "verdict.json").write_text(
        json.dumps({"verdict": finding, "reasons": reasons, "config": config}), encoding="utf-8"
    )
    print(report)
    return 0 if finding == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
