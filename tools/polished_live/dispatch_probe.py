#!/usr/bin/env python3
"""D3 read-only dispatcher measurement; --dry-run validates inputs without staging or launching.

    python tools/polished_live/dispatch_probe.py --fixture SAVE --route route.json --dry-run

Route JSON: {"steps": [{"phase": "setup", "frames": 120, "buttons": ["A"]}, ...]}.
Phases: setup (boot/CONTINUE/reposition, unclassified), idle, walking, start_menu,
npc_talk, battle (optional). Include idle, walking, start_menu and npc_talk explicitly;
phase labels assert the route's intended situation, not proof that native input reached it.
Buttons are timed holds from emulator boot. Spare --frames are unclassified setup idle.
No guest writes, CPU changes, synthetic staging, SLink client, server, or lease publication.
ACCEPT/REFUSE predictions concern stack + engine guards BEFORE the lease check; actual
SlinkTradePromptEntry entries must stay zero because this probe never publishes PROMPT.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
WORK = Path("F:/slink-work/lanes/pol-dispatch-probe")
RELEASE = Path("F:/slink-work/cache/polished/release/polishedcrystal-3.2.3.gbc")
SYMBOLS = REPO / "data/polished/polished_slink.sym"
BANK, DISPATCH, PROMPT = 0x7E, 0x4700, 0x4780
PIN_OFFSETS = (5, 12, 13, 14, 15, 24, 25, 26, 27)
POSITIVE = frozenset(("idle", "walking"))
NEGATIVE = frozenset(("start_menu", "npc_talk", "battle"))
PHASES = POSITIVE | NEGATIVE | {"setup"}
REQUIRED_PHASES = POSITIVE | {"start_menu", "npc_talk"}
BUTTONS = frozenset(("A", "B", "Start", "Select", "Up", "Down", "Left", "Right"))
FIELDS = {"script_mode": "wScriptMode", "battle_mode": "wBattleMode", "link_mode": "wLinkMode",
          "paused": "wGameLogicPaused", "in_menu": "hInMenu", "vblank": "hVBlank",
          "map_status": "wMapStatus", "step_flags": "wPlayerStepFlags", "map_event_status": "wMapEventStatus"}


def read_symbols(path: Path) -> dict:
    from tools.polished_live.runtime import read_symbols as read
    # This probe's public symbol reader has always returned immutable bank/address pairs.
    return {name: tuple(pair) for name, pair in read(path).items()}


def derive_contract(symbols: dict) -> dict:
    """Nine pinned VALUES come only from linked symbols; other stack positions are noise."""
    needed = ("SlinkTradeDispatch", "SlinkTradePromptEntry", "SlinkTradeResponderService", "NextOverworldFrame",
              "NextOverworldFrame.gfx_done", "DelayFrame", "HandleMap", "OverworldLoop.loop", "hROMBank", *FIELDS.values())
    missing = [s for s in needed if s not in symbols]
    if missing:
        raise ValueError(f"missing dispatcher symbols: {missing}")
    if symbols["SlinkTradeDispatch"] != (BANK, DISPATCH) or symbols["SlinkTradePromptEntry"] != (BANK, PROMPT):
        raise ValueError("dispatcher/prompt symbols differ from pinned 7e:4700 / 7e:4780")
    target_bank, target = symbols["SlinkTradeResponderService"]
    if target_bank != BANK or not 0x4000 <= target < 0x8000:
        raise ValueError("responder service must share the prompt trampoline ROM bank")
    saved_bank = symbols["NextOverworldFrame"][0]
    if any(symbols[s][0] != saved_bank for s in ("NextOverworldFrame.gfx_done", "HandleMap", "OverworldLoop.loop")):
        raise ValueError("overworld stack chain symbols do not share one bank")
    pins = [{"offset": 5, "value": saved_bank}]
    for offset, symbol, delta in ((12, "DelayFrame", 3), (14, "NextOverworldFrame.gfx_done", 6),
                                  (24, "HandleMap", 0x15), (26, "OverworldLoop.loop", 9)):
        addr = symbols[symbol][1] + delta
        if not 0 <= addr <= 0xFFFF:
            raise ValueError(f"stack return {symbol}+{delta} exceeds 16 bits")
        pins.extend(({"offset": offset, "value": addr & 0xFF}, {"offset": offset + 1, "value": addr >> 8}))
    return {"bank": BANK, "dispatch_addr": DISPATCH, "prompt_addr": PROMPT, "prompt_target": target,
            "rombank_addr": symbols["hROMBank"][1], "svbk_addr": 0xFF70,
            "pins": pins, "fields": FIELDS, "map_status_handle": 2,
            "step_continue_mask": 1 << 5, "map_events_on": 0}


def load_contract() -> tuple[dict, dict]:
    provenance = json.loads((REPO / "data/polished/overlay_provenance.json").read_text(encoding="utf-8"))
    if hashlib.sha256(SYMBOLS.read_bytes()).hexdigest() != provenance["symbols"]["polished_slink.sym"]:
        raise ValueError("overlay symbols differ from provenance")
    return derive_contract(read_symbols(SYMBOLS)), provenance


def stack_matches(stack: bytes, pins: list[dict]) -> bool:
    return len(stack) == 28 and all(stack[p["offset"]] == p["value"] for p in pins)


def engine_clean(e: dict) -> bool:
    """Mirror trade_dispatch.asm's pre-lease guards, including the masked SVBK/step bits."""
    return (e["svbk"] & 7 < 2 and all(e[k] == 0 for k in ("script_mode", "battle_mode", "link_mode", "paused", "in_menu", "vblank"))
            and e["map_status"] == 2 and not e["step_flags"] & 0x20 and e["map_event_status"] == 0)


def _qualified(e: dict) -> bool:
    addr = DISPATCH if e.get("kind") == "dispatch" else PROMPT
    return (e.get("matched") is True and type(e.get("bank")) is int and e["bank"] == BANK
            and type(e.get("pc")) is int and e["pc"] == addr and e.get("hook_addr") == addr)


def evaluate(trace, pins=None) -> tuple[bool, list[str]]:
    """Pure when pins are supplied; otherwise derive the expected values from linked symbols.

    Recompute Lua booleans from raw stack/guard bytes. Never trust a supplied stack_match
    alone, and never let wrong-bank rows supply the positive sample or negative finding.
    Wrong-bank callbacks retain at most 16 samples per site; final aggregate counters
    account for compressed callbacks without making them positive measurements.
    """
    if pins is None:
        pins = derive_contract(read_symbols(SYMBOLS))["pins"]
    if not isinstance(trace, list) or any(not isinstance(e, dict) for e in trace):
        return False, ["MALFORMED: expected a list of event objects"]
    why, qualified = [], []
    finals = [e for e in trace if e.get("kind") == "final"]
    if len(finals) != 1 or trace[-1] is not finals[0] or finals[0].get("completed") is not True:
        why.append("INCOMPLETE: missing unique completed final event at end")
    else:
        final = finals[0]
        if (type(final.get("guest_writes")) is not int or final["guest_writes"] != 0
                or type(final.get("cpu_changes")) is not int or final["cpu_changes"] != 0):
            why.append("PROBE_MUTATION: write/register-change counters must be integer zero")
        if any(type(final.get(k)) is not int or final[k] != 0 for k in ("driver_errors", "overflows")):
            why.append("MALFORMED: driver-error/overflow counters must be integer zero")
        if type(final.get("prompt_hits")) is not int or final["prompt_hits"] != 0:
            why.append(f"PROMPT_ENTRY: expected zero accept-path entries, got {final.get('prompt_hits')!r}")
        phase_frames = final.get("phase_frames")
        if (not isinstance(phase_frames, dict)
                or any(type(phase_frames.get(p)) is not int or phase_frames[p] <= 0 for p in REQUIRED_PHASES)):
            why.append("INCOMPLETE_PHASES: idle, walking, start_menu and npc_talk must each have played frames")
    if len(finals) == 1:
        if str(REPO) not in sys.path:
            sys.path.insert(0, str(REPO))
        from tools.polished_live.faint_probe import validate_hook_counts

        final = finals[0]
        why.extend(validate_hook_counts(trace, final, {
            "dispatch": {"bank": BANK, "addr": DISPATCH},
            "prompt": {"bank": BANK, "addr": PROMPT},
        }))
        counters = final.get("hook_counts")
        if isinstance(counters, dict):
            for kind in ("dispatch", "prompt"):
                counts = counters.get(kind)
                if isinstance(counts, dict) and (
                    type(final.get(kind + "_hits")) is not int
                    or final[kind + "_hits"] != counts.get("qualified")
                ):
                    why.append(f"MALFORMED: {kind}_hits disagrees with qualified aggregate")
    for e in trace:
        kind = e.get("kind")
        if kind in ("overflow", "guest_write", "cpu_change", "driver_error"):
            why.append(f"{kind.upper()}: {e}")
        if kind not in ("dispatch", "prompt"):
            if kind != "final" and kind not in ("overflow", "guest_write", "cpu_change", "driver_error"):
                why.append(f"MALFORMED: unknown event kind {kind!r}")
            continue
        addr = DISPATCH if kind == "dispatch" else PROMPT
        bank, matched = e.get("bank"), e.get("matched")
        if (type(bank) is not int or not 0 <= bank <= 255 or type(matched) is not bool
                or matched != (bank == BANK) or e.get("hook_addr") != addr):
            why.append(f"MALFORMED: inconsistent bank/hook qualification {e}")
        expected_qualified = _qualified(e)
        if e.get("qualified") is not expected_qualified:
            why.append("MALFORMED: qualified flag disagrees with bank/PC/address")
        if bank == BANK and matched is True and e.get("pc") != addr:
            why.append(f"CALLBACK_PC: {kind} expected {addr:#06x}, observed {e.get('pc')!r}")
        if not expected_qualified:
            continue
        if kind == "prompt":
            why.append("PROMPT_ENTRY: accept path executed without probe lease publication")
            continue
        qualified.append(e)
        phase = e.get("phase")
        try:
            stack = bytes.fromhex(e["stack"]) if isinstance(e.get("stack"), str) else b""
        except ValueError:
            stack = b""
        if (len(stack) != 28 or not isinstance(phase, str) or phase not in PHASES
                or type(e.get("sp")) is not int or not 0 <= e["sp"] <= 0xFFFF
                or any(type(e.get(k)) is not int or not 0 <= e[k] <= 255 for k in ("svbk", *FIELDS))):
            why.append(f"MALFORMED: dispatch sample lacks 28 stack bytes, phase, SP or guard bytes: {e}")
            continue
        stack_ok, clean = stack_matches(stack, pins), engine_clean(e)
        if e.get("stack_match") is not stack_ok or e.get("engine_clean") is not clean or e.get("context_accept") is not (stack_ok and clean):
            why.append("MALFORMED: derived Lua booleans disagree with raw stack/guards")
        if phase in POSITIVE and clean and not stack_ok:
            why.append(f"STACK_MISMATCH_IDLE: {phase} clean guards, observed SP={e['sp']:#06x} stack={stack.hex()}")
        if phase in NEGATIVE and clean and stack_ok:
            why.append(f"NEGATIVE_ACCEPTED: {phase} would pass pre-lease guards, SP={e['sp']:#06x} stack={stack.hex()}")
    if not qualified:
        why.append("NO_HIT: OPEN; no qualified dispatcher entry (not PASS)")
    else:
        if not any(e.get("phase") == "idle" and e.get("context_accept") is True for e in qualified):
            why.append("NO_IDLE_CANDIDATE: no idle hit passes stack and engine guards")
        if not any(e.get("phase") == "walking" for e in qualified):
            why.append("NO_WALKING_SAMPLE: walking dispatcher entry was not observed")
    return not why, why


def verdict(reasons: list[str]) -> str:
    # No qualified measurement is OPEN; preserve every corruption/completion reason alongside it.
    for code in ("STACK_MISMATCH_IDLE", "NEGATIVE_ACCEPTED"):
        if any(r.startswith(code + ":") for r in reasons):
            return code
    if any(r.startswith("NO_HIT:") for r in reasons):
        return "NO_HIT"
    return "FAIL" if reasons else "PASS"


def distribution(trace) -> list[dict]:
    """Distinct raw images and counts, per phase/SP; only qualified dispatcher entries."""
    counts = Counter()
    for e in trace:
        if (not isinstance(e, dict) or e.get("kind") != "dispatch" or not _qualified(e)
                or not isinstance(e.get("phase"), str) or type(e.get("sp")) is not int
                or not isinstance(e.get("stack"), str)):
            continue
        try:
            stack = bytes.fromhex(e["stack"])
        except ValueError:
            continue
        if len(stack) == 28:
            counts[(e["phase"], e["sp"], stack.hex())] += 1
    return [{"phase": phase, "sp": sp, "stack": stack, "count": count}
            for (phase, sp, stack), count in counts.items()]


def parse_route(path: Path, frame_cap: int) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or set(data) != {"steps"} or not isinstance(data["steps"], list):
        raise ValueError("route must contain only a steps list")
    steps, total, phases = [], 0, set()
    for i, step in enumerate(data["steps"]):
        if not isinstance(step, dict) or set(step) != {"phase", "frames", "buttons"}:
            raise ValueError(f"step {i} must contain only phase, frames, buttons")
        phase, frames, buttons = step["phase"], step["frames"], step["buttons"]
        if not isinstance(phase, str) or phase not in PHASES or type(frames) is not int or frames <= 0:
            raise ValueError(f"step {i} requires a known phase and positive integer frames")
        if (not isinstance(buttons, list) or any(not isinstance(b, str) or b not in BUTTONS for b in buttons)
                or len(set(buttons)) != len(buttons)):
            raise ValueError(f"step {i} buttons must be unique native button names")
        if phase == "idle" and buttons:
            raise ValueError("idle phase must release all buttons")
        if phase == "walking" and not any(b in buttons for b in ("Up", "Down", "Left", "Right")):
            raise ValueError("walking phase must hold a direction")
        phases.add(phase)
        total += frames
        steps.append({"phase": phase, "frames": frames, "buttons": buttons})
    if not phases >= REQUIRED_PHASES:
        raise ValueError(f"route missing required phases: {sorted(REQUIRED_PHASES - phases)}")
    for phase, button in (("start_menu", "Start"), ("npc_talk", "A")):
        if not any(s["phase"] == phase and button in s["buttons"] for s in steps):
            raise ValueError(f"{phase} phase must include its native {button} press")
    if total > frame_cap:
        raise ValueError(f"route needs {total} frames, exceeds --frames {frame_cap}")
    return steps


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--fixture", type=Path, required=True, help="native SaveRAM, copied unchanged")
    ap.add_argument("--route", type=Path, required=True, help="phased native input from emulator boot")
    ap.add_argument("--frames", type=int, default=12000)
    ap.add_argument("--trace-cap", type=int, default=20000, help="all hook rows; reaching capacity fails")
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--dry-run", action="store_true", help="read/validate/print only; no lanes, staging or emulator")
    args = ap.parse_args(argv)
    if any(getattr(args, k) <= 0 for k in ("frames", "trace_cap", "timeout")):
        ap.error("frames, trace-cap and timeout must be positive")
    if not args.fixture.is_file():
        ap.error(f"fixture does not exist: {args.fixture}")
    try:
        args.steps = parse_route(args.route, args.frames)
    except (OSError, ValueError) as exc:
        ap.error(str(exc))
    return args


def check_rom(rom: bytes, contract: dict) -> None:
    offset = BANK * 0x4000 + DISPATCH - 0x4000
    bank_pin = next(p["value"] for p in contract["pins"] if p["offset"] == 5)
    if rom[offset:offset + 5] != bytes((0xF8, 5, 0x7E, 0xFE, bank_pin)):
        raise ValueError("staged dispatcher entry does not contain ld hl,sp+5 / ld a,[hl] / cp saved bank")
    prompt_offset = BANK * 0x4000 + PROMPT - 0x4000
    # C6 replaced the inert RET with a frame-neutral JP into the held responder.
    expected = b"\xc3" + contract["prompt_target"].to_bytes(2, "little")
    if rom[prompt_offset:prompt_offset + 3] != expected:
        raise ValueError("staged prompt trampoline does not jump to the linked responder service")


def stage(lane: Path, contract: dict, provenance: dict) -> tuple[Path, str]:
    from tools.polished_live.runtime import pinned_rom_bytes
    rom_bytes = pinned_rom_bytes(RELEASE, provenance["output"]["sha1"],
                                 patch=REPO / provenance["output"]["ups"]["file"],
                                 base_sha1=provenance["base_sha1"])
    sha1 = hashlib.sha1(rom_bytes).hexdigest()
    check_rom(rom_bytes, contract)
    rom = lane / "rom/pol_overlay.gbc"
    rom.parent.mkdir(parents=True)
    rom.write_bytes(rom_bytes)
    return rom, sha1


def recording_reasons(text: str, elapsed: float, timeout: float) -> list[str]:
    results = [line for line in text.splitlines() if line.startswith("RESULT:")]
    reasons = []
    if len(results) != 1 or not results[0].startswith("RESULT: PASS dispatch-probe recording "):
        reasons.append("INCOMPLETE: missing unique successful driver RESULT")
    if elapsed >= timeout:
        reasons.append("DEADLINE: wall-clock budget expired, including harness shutdown/flush")
    return reasons


def main(argv=None) -> int:
    args = parse_args(argv)
    contract, provenance = load_contract()
    plan = {"dry_run": args.dry_run, "measurement_verdict": None, "setup": "played",
            "fixture": str(args.fixture), "route": str(args.route), "steps": args.steps,
            "frames": args.frames, "trace_cap": args.trace_cap, "contract": contract,
            "overlay_sha1": provenance["output"]["sha1"]}
    if args.dry_run:
        print(json.dumps(plan, indent=2))
        return 0  # validation only, never a measurement PASS
    WORK.mkdir(parents=True, exist_ok=True)
    lane = Path(tempfile.mkdtemp(prefix="played-", dir=WORK))
    rom, sha1 = stage(lane, contract, provenance)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from tools.polished_live import harness

    extra = ("SlinkTradeDispatch", "SlinkTradePromptEntry", *FIELDS.values())
    from dataclasses import replace
    launch_config = replace(harness.launch_config(dict(os.environ, POL_LANE=str(lane), POL_KIND="overlay")),
                            rom_src=rom, rom=rom, symbols=tuple(dict.fromkeys((*harness.SYMBOLS, *extra))))
    harness.prepare_save(launch_config, args.fixture)
    plan["fixture_sha256"] = hashlib.sha256(args.fixture.read_bytes()).hexdigest()
    run = lane / "probe"
    run.mkdir()
    config = run / "input.json"
    config.write_text(json.dumps({"contract": contract, "steps": args.steps, "frames": args.frames,
                                 "trace_cap": args.trace_cap}), encoding="utf-8")
    started = time.monotonic()
    text, pid = harness.launch(launch_config, "tools/polished_live/dispatch_probe.lua", run,
                               {"POL_PROBE_CONFIG": config.as_posix()}, args.timeout)
    reasons = recording_reasons(text, time.monotonic() - started, args.timeout)
    trace = []
    try:
        trace = json.loads((run / "trace.json").read_text(encoding="utf-8"))
        _, findings = evaluate(trace, contract["pins"])
        reasons.extend(findings)
    except (OSError, ValueError) as exc:
        reasons.append(f"MALFORMED: {exc}")
    finding = verdict(reasons)
    result = {**plan, "measurement_verdict": finding, "reasons": reasons, "overlay_sha1": sha1,
              "emuhawk_pid": pid, "stack_distribution": distribution(trace) if isinstance(trace, list) else []}
    (run / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    with (run / "result.txt").open("a", encoding="utf-8") as fh:
        fh.write(f"\nRESULT: {finding} dispatch-probe ({len(reasons)} reasons)\n")
    print(json.dumps(result, indent=2))
    print(f"[probe] evidence {run}")
    return 0 if not reasons else 1


if __name__ == "__main__":
    raise SystemExit(main())
