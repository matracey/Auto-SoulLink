#!/usr/bin/env python3
"""Card 1 live probe (docs/polished/TRADE.md s12-s13): the two repointed Polished specials, NO cable partner.

    python tools/polished_live/trade_port_probe.py --case special-entry   # the positive oracle
    python tools/polished_live/trade_port_probe.py --case battle          # control (a): room 2 -> ORIGINAL wait
    python tools/polished_live/trade_port_probe.py --case decline         # control (b): B at the must-save prompt
    python tools/polished_live/trade_port_probe.py --case clean           # control (c): release ROM, oracle must REJECT

One EmuHawk at a time, a fresh private lane per case (F:/slink-work/lanes/pol-probe/<case>), only the PID this script
starts is ever killed. The Lua driver (trade_port_probe.lua) records a trace; the verdict is decided HERE by pure
functions over that trace (evaluate_*), which tests/unit/test_polished_trade_probe_oracle.py exercises on synthetic traces.

SYNTH (disclosed in every result): wEventFlags+4 |= $02 and an engine warp to POKECENTER_2F, as explore.lua WHICH=B.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
WORK = Path("F:/slink-work/lanes/pol-probe")
RELEASE = Path("F:/slink-work/cache/polished/release/polishedcrystal-3.2.3.gbc")
UPS = REPO / "patch/dist/SLink-Polished.ups"
FIXTURE = Path("F:/slink-work/lanes/g2int-live/pol/fixture/polished_overlay_warp.SaveRAM")
FIXTURE_SHA256_PREFIX = "75c7a5dc"
sys.path.insert(0, str(REPO))
from tools.polished_live.overlay_pin import overlay_sha1  # noqa: E402

OVERLAY_SHA1 = overlay_sha1()
CASES = ("special-entry", "battle", "decline", "clean")

# Symbols the Lua driver needs beyond harness.SYMBOLS (all from data/polished/polished_slink.sym).
EXTRA_SYMBOLS = (
    "NoYesBox", "GetScriptByte", "Script_endtext", "WaitForOtherPlayerToExit", "SlinkTradeWaitGate",
    "SlinkTradeTimeoutGate", "SlinkTradeEntry", "Special_CheckLinkTimeout", "PerformLinkChecks", "hScriptVar",
    "hScriptBank", "hScriptPos", "hVBlank", "wChosenCableClubRoom", "wScriptStackSize",
)
# ROM bytes the Lua `ret` hooks assume (bank, addr, byte); the overlay-only rows are skipped on the clean ROM.
RET_BYTES = ((0x0A, 0x4E59, 0xC9, False), (0x7E, 0x440B, 0xC9, True), (0x7E, 0x4426, 0xC9, True))

WAIT_CURSOR = (0x24, 0x761B)       # cursor after `special Special_WaitForLinkedFriend` (0F 02 at 24:7619)
TIMEOUT_CURSOR = (0x24, 0x762C)    # cursor after `special Special_CheckLinkTimeout` (0F 03 at 24:762A)
REDIRECT = (0x2D, 0x7595)          # DayOfWeekSiblingsHousePokedexScript.End: a bare `endtext` (C0)
ENDTEXT_CURSOR = (0x2D, 0x7596)
DID_NOT_SAVE = (0x24, 0x7689)
ENTER_ROOM = (0x24, 0x770E)        # PokeCenter2F_EnterRoom (scall target): the link-room entry
INTERP_BANK = 0x25
RETURN_FAR_CALL_STK = "9826"       # little-endian 00:2698 _ReturnFarCall: what a special's `ret` pops (measured, sp+0..1)
ORIG_WAIT_BUDGET = 900


# ── the oracle: pure functions over a trace (list of event dicts) ──────────────────────────────────────────────────
def _ev(trace, kind):
    return [e for e in trace if e.get("kind") == kind]


def _cur(e):
    return (e.get("sbank"), e.get("spos"))


def _fmt(c):
    return "?" if c[0] is None or c[1] is None else f"{c[0]:02X}:{c[1]:04X}"


def _once(trace, kind, label, why):
    evs = _ev(trace, kind)
    if not evs:
        why.append(f"missing {label} (0 {kind} events)")
        return None
    if len(evs) != 1:
        why.append(f"{label} hit {len(evs)} times, expected exactly once")
        return None
    return evs[0]


def _zero(trace, kind, label, why):
    n = len(_ev(trace, kind))
    if n:
        why.append(f"{label} executed {n}x, expected zero")


def _complete(trace, why) -> None:
    """The absence checks (no room entry, no original routine) are only as good as the interpreter trace: a recorder
    that stopped logging at its cap must FAIL the case, never silently shorten the observation window."""
    if any(e.get("kind") == "gsb_overflow" for e in trace):
        why.append("interpreter trace overflowed the recorder cap: the absence checks are incomplete")


def evaluate_positive(trace) -> tuple[bool, list[str]]:
    """The TRADE.md s13 positive oracle for the trade receptionist. Returns (ok, failure reasons)."""
    why: list[str] = []
    chain: list[tuple[str, dict]] = []
    _complete(trace, why)

    wg = _once(trace, "wait_gate", "wait gate entry", why)
    if wg:
        chain.append(("wait gate entry", wg))
        if wg.get("bank") != 0x7E:
            why.append(f"wait gate bank {wg.get('bank')} != 0x7E")
        if wg.get("room") != 1:
            why.append(f"wait gate wChosenCableClubRoom {wg.get('room')} != 1 (wrong room)")
        if _cur(wg) != WAIT_CURSOR:
            why.append(f"wait gate script cursor {_fmt(_cur(wg))} != 24:761B")
        if not str(wg.get("stk", "")).startswith(RETURN_FAR_CALL_STK):
            why.append("wait gate return address is not _ReturnFarCall (stack top)")
    wr = _once(trace, "wait_ret", "wait gate return", why)
    if wr:
        chain.append(("wait gate return", wr))
        if wr.get("var") != 1:
            why.append(f"wait gate returned hScriptVar {wr.get('var')} != 1")
        if wr.get("room") != 1:
            why.append(f"room {wr.get('room')} != 1 at the wait gate return")
        if wg and wr.get("sp") != wg.get("sp"):
            why.append(f"wait gate return SP {wr.get('sp')} != entry SP {wg.get('sp')} (stack unbalanced)")
        nxt = next((e for e in trace if e.get("kind") == "gsb" and e["ord"] > wr["ord"]), None)
        if nxt is None or nxt.get("bank") != INTERP_BANK or _cur(nxt) != WAIT_CURSOR:
            why.append("interpreter did not continue at 24:761B in bank $25 after the wait gate return")
    qs = _once(trace, "try_quicksave", "Special_TryQuickSave entry", why)
    if qs:
        chain.append(("TryQuickSave entry", qs))
    qr = _once(trace, "quicksave_ret", "quick-save return", why)
    if qr:
        chain.append(("quick-save return", qr))
        if qr.get("var") != 1:
            why.append(f"quick-save returned hScriptVar {qr.get('var')} != 1")
        if qr.get("room") != 1:
            why.append(f"quick-save left wChosenCableClubRoom {qr.get('room')} != 1")
    tg = _once(trace, "timeout_gate", "timeout gate entry", why)
    if tg:
        chain.append(("timeout gate entry", tg))
        if tg.get("bank") != 0x7E:
            why.append(f"timeout gate bank {tg.get('bank')} != 0x7E")
        if tg.get("room") != 1:
            why.append(f"timeout gate wChosenCableClubRoom {tg.get('room')} != 1 (wrong room)")
        if _cur(tg) != TIMEOUT_CURSOR:
            why.append(f"timeout gate script cursor {_fmt(_cur(tg))} != 24:762C")
        if not str(tg.get("stk", "")).startswith(RETURN_FAR_CALL_STK):
            why.append("timeout gate return address is not _ReturnFarCall (stack top)")
    st = _once(trace, "stub", "SlinkTradeEntry stub entry", why)
    if st:
        chain.append(("stub entry", st))
        if st.get("bank") != 0x7E:
            why.append(f"stub bank {st.get('bank')} != 0x7E")
        if tg and st.get("sp") != tg.get("sp", 0) - 2:
            why.append(f"stub SP {st.get('sp')} is not the timeout gate's SP - 2 (not called from the gate)")
    tr = _once(trace, "timeout_ret", "timeout gate return", why)
    gsb2 = None
    if tr:
        chain.append(("timeout gate return", tr))
        if _cur(tr) != REDIRECT:
            why.append(f"redirect cursor {_fmt(_cur(tr))} != 2D:7595 at the timeout gate return")
        if tg and tr.get("sp") != tg.get("sp"):
            why.append(f"timeout gate return SP {tr.get('sp')} != entry SP {tg.get('sp')} (stack unbalanced)")
        gsb2 = next((e for e in trace if e.get("kind") == "gsb" and e["ord"] > tr["ord"]), None)
        if gsb2 is None or _cur(gsb2) != REDIRECT or gsb2.get("bank") != INTERP_BANK:
            got = "none" if gsb2 is None else f"{_fmt(_cur(gsb2))} bank ${gsb2.get('bank', 0):02X}"
            why.append(f"redirect cursor: the interpreter's first read after the timeout gate was {got}, "
                       "expected 2D:7595 in bank $25")
        else:
            chain.append(("redirect read", gsb2))
    else:
        why.append("redirect cursor never observed (no timeout gate return to redirect from)")
    ets = _ev(trace, "endtext")
    if not ets:
        why.append("Script_endtext never hit")
    elif len(ets) != 1:
        why.append(f"Script_endtext hit {len(ets)} times, expected exactly once")
    else:
        et = ets[0]
        chain.append(("Script_endtext", et))
        if _cur(et) != ENDTEXT_CURSOR:
            why.append(f"Script_endtext cursor {_fmt(_cur(et))} != 2D:7596")
        if et.get("bank") != INTERP_BANK:
            why.append(f"Script_endtext bank {et.get('bank')} != $25")
    fin = _once(trace, "final", "final state snapshot", why)
    if fin:
        chain.append(("final state", fin))
        for key, name in (("running", "wScriptRunning"), ("stack", "wScriptStackSize"),
                          ("vblank", "hVBlank"), ("link", "wLinkMode")):
            if fin.get(key) != 0:
                why.append(f"{name} {fin.get(key)} != 0 after the script")
    m0, m1 = _once(trace, "move_start", "move start", why), _once(trace, "move_end", "move end", why)
    if m0 and m1:
        coords = [m.get(k) for m in (m0, m1) for k in ("x", "y")]
        if any(not isinstance(c, int) for c in coords):
            why.append("player movement events carry no coordinates")
        elif (m0["x"], m0["y"]) == (m1["x"], m1["y"]):
            why.append(f"player did not move after the script (stayed at ({m0.get('x')},{m0.get('y')}))")
        # the movement is the LAST thing observed: after the final state, start before end
        chain.extend([("move start", m0), ("move end", m1)])
    _zero(trace, "orig_wait", "original Special_WaitForLinkedFriend", why)
    _zero(trace, "orig_timeout", "original Special_CheckLinkTimeout", why)
    _zero(trace, "perform_link_checks", "PerformLinkChecks", why)
    _zero(trace, "wait_exit", "WaitForOtherPlayerToExit", why)
    if any(e.get("kind") == "gsb" and _cur(e) == ENTER_ROOM for e in trace):
        why.append("link room entry (PokeCenter2F_EnterRoom 24:770E) was read")
    for (an, a), (bn, b) in zip(chain, chain[1:], strict=False):
        if a["ord"] >= b["ord"]:
            why.append(f"events out of order: {an} (ord {a['ord']}) is not before {bn} (ord {b['ord']})")
    return (not why), why


def evaluate_battle(trace) -> tuple[bool, list[str]]:
    """Control (a): the battle receptionist (room 2) reaches the ORIGINAL wait and finishes inside its budget."""
    why: list[str] = []
    _complete(trace, why)
    wg = _once(trace, "wait_gate", "wait gate entry", why)
    if wg:
        if wg.get("room") != 2:
            why.append(f"chosen room {wg.get('room')} != 2")
        if wg.get("bank") != 0x7E:
            why.append(f"wait gate bank {wg.get('bank')} != 0x7E")
    ow = _once(trace, "orig_wait", "original Special_WaitForLinkedFriend entry", why)
    od = _once(trace, "orig_wait_done", "original wait completion (.done)", why)
    if wg and ow and ow["ord"] <= wg["ord"]:
        why.append("the original wait ran before the gate (not reached through the gate tail call)")
    if ow and od:
        span = od["frame"] - ow["frame"]
        if span > ORIG_WAIT_BUDGET:
            why.append(f"original wait took {span} frames > {ORIG_WAIT_BUDGET} (timeout)")
        if span < 60:
            why.append(f"original wait took only {span} frames (suspiciously short)")
        if od["ord"] <= ow["ord"]:
            why.append("original wait completion is not after its entry")
    for kind, label in (("timeout_gate", "timeout gate"), ("stub", "SlinkTradeEntry stub"),
                        ("orig_timeout", "original Special_CheckLinkTimeout"), ("try_quicksave", "Special_TryQuickSave"),
                        ("perform_link_checks", "PerformLinkChecks")):
        _zero(trace, kind, label, why)
    fin = _once(trace, "final", "final state snapshot", why)
    if fin and fin.get("running") != 0:
        why.append(f"wScriptRunning {fin.get('running')} != 0 at the end")
    return (not why), why


def evaluate_decline(trace) -> tuple[bool, list[str]]:
    """Control (b): B at the must-save prompt -> .DidNotSave -> WaitForOtherPlayerToExit, no quick-save/timeout/stub."""
    why: list[str] = []
    _complete(trace, why)
    wg = _once(trace, "wait_gate", "wait gate entry", why)
    if wg and wg.get("room") != 1:
        why.append(f"wait gate wChosenCableClubRoom {wg.get('room')} != 1")
    wr = _once(trace, "wait_ret", "wait gate return", why)
    if wr and wr.get("var") != 1:
        why.append(f"wait gate returned hScriptVar {wr.get('var')} != 1")
    ans = [e for e in _ev(trace, "answer") if e.get("which") == 2]
    if len(ans) != 1 or ans[0].get("btn") != "B":
        why.append("the must-save prompt was not answered with B exactly once")
    dns = [e for e in trace if e.get("kind") == "gsb" and _cur(e) == DID_NOT_SAVE]
    if len(dns) != 1:
        why.append(f".DidNotSave (24:7689) read {len(dns)} times, expected once")
    elif len(ans) == 1 and ans[0]["ord"] >= dns[0]["ord"]:
        why.append("the B answer was not recorded BEFORE .DidNotSave (it cannot be the cause)")
    ex = _once(trace, "wait_exit", "WaitForOtherPlayerToExit", why)
    if ex and dns and ex["ord"] <= dns[0]["ord"]:
        why.append("WaitForOtherPlayerToExit did not follow .DidNotSave")
    for kind, label in (("try_quicksave", "Special_TryQuickSave"), ("timeout_gate", "timeout gate"),
                        ("stub", "SlinkTradeEntry stub"), ("orig_timeout", "original Special_CheckLinkTimeout"),
                        ("orig_wait", "original Special_WaitForLinkedFriend"), ("perform_link_checks", "PerformLinkChecks")):
        _zero(trace, kind, label, why)
    fin = _once(trace, "final", "final state snapshot", why)
    if fin and fin.get("running") != 0:
        why.append(f"wScriptRunning {fin.get('running')} != 0 at the end")
    return (not why), why


def evaluate_clean(trace) -> tuple[bool, list[str]]:
    """Control (c): on the clean ROM the original wait runs, and the positive oracle must REJECT for missing
    gate/stub/redirect events. Returns (ok, notes); ok means the oracle correctly rejected."""
    why: list[str] = []
    _complete(trace, why)
    ow, od = _once(trace, "orig_wait", "original wait entry", why), _once(trace, "orig_wait_done", "original wait .done", why)
    if ow and od:
        span = od["frame"] - ow["frame"]
        if span > ORIG_WAIT_BUDGET:
            why.append(f"original wait took {span} frames > {ORIG_WAIT_BUDGET}")
        if span < 60 or od["ord"] <= ow["ord"]:
            why.append(f"original wait ran {span} frames / completion not after entry (suspiciously short)")
    fin = _once(trace, "final", "final state snapshot", why)
    if fin and fin.get("running") != 0:
        why.append(f"wScriptRunning {fin.get('running')} != 0 at the end")
    for kind, label in (("wait_gate", "wait gate"), ("timeout_gate", "timeout gate"), ("stub", "stub")):
        _zero(trace, kind, label, why)
    ok_pos, reasons = evaluate_positive(trace)
    if ok_pos:
        why.append("the positive oracle ACCEPTED a clean-ROM trace (it is blind)")
    for needle in ("missing wait gate entry", "missing SlinkTradeEntry stub entry", "redirect cursor"):
        if not any(needle in r for r in reasons):
            why.append(f"the positive oracle did not reject for: {needle}")
    return (not why), (why or reasons)


EVALUATORS = {"special-entry": evaluate_positive, "battle": evaluate_battle, "decline": evaluate_decline,
              "clean": evaluate_clean}


# ── the runner ─────────────────────────────────────────────────────────────────────────────────────────────────────
def excerpt(trace) -> list[str]:
    """The ordered event excerpt: every non-gsb event, plus the interpreter reads that matter."""
    keep = {ENTER_ROOM, WAIT_CURSOR, TIMEOUT_CURSOR, REDIRECT, DID_NOT_SAVE}
    rows = []
    for e in trace:
        if e.get("kind") == "gsb" and _cur(e) not in keep:
            continue
        rows.append(f"  #{e['ord']:<3} f{e['frame']:<5} {e['kind']:<14} pc={e.get('pc', 0):04X} sp={e.get('sp', 0):04X} "
                    f"bank={e.get('bank', 0):02X} var={e.get('var')} room={e.get('room')} cur={_fmt(_cur(e))} "
                    f"vb={e.get('vblank')} link={e.get('link')} run={e.get('running')} stk={e.get('stack')} "
                    f"xy=({e.get('x')},{e.get('y')})")
    return rows


def stage(case: str, lane: Path) -> tuple[Path, str]:
    sys.path.insert(0, str(REPO))
    from tools.polished_live.runtime import pinned_rom_bytes
    base_sha1 = json.loads((REPO / "data/polished/overlay_provenance.json").read_text(encoding="utf-8"))["base_sha1"]
    kind = "clean" if case == "clean" else "overlay"
    data = pinned_rom_bytes(RELEASE, base_sha1 if kind == "clean" else OVERLAY_SHA1,
                            patch=None if kind == "clean" else UPS, base_sha1=base_sha1)
    for bank, addr, want, overlay_only in RET_BYTES:
        if overlay_only and kind == "clean":
            continue
        got = data[bank * 0x4000 + addr - 0x4000]
        if got != want:
            raise SystemExit(f"ROM byte at {bank:02X}:{addr:04X} is {got:02X}, the ret hook needs {want:02X}")
    rom = lane / "rom" / f"pol_{kind}.gbc"       # BizHawk names the save from this stem: `pol overlay.SaveRAM`
    rom.parent.mkdir(parents=True, exist_ok=True)
    rom.write_bytes(data)
    return rom, hashlib.sha1(data).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--case", choices=CASES, required=True)
    ap.add_argument("--timeout", type=int, default=600, help="wall-clock deadline for the EmuHawk run, seconds")
    args = ap.parse_args()
    case = args.case
    lane = WORK / case
    if lane.exists():
        if WORK.resolve() not in lane.resolve().parents:
            raise SystemExit(f"refusing to clear {lane}")
        shutil.rmtree(lane)
    lane.mkdir(parents=True)
    kind = "clean" if case == "clean" else "overlay"
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from tools.polished_live import harness

    rom, sha1 = stage(case, lane)
    from dataclasses import replace
    launch_config = replace(harness.launch_config(dict(os.environ, POL_LANE=str(lane), POL_KIND=kind)),
                            rom_src=rom, rom=rom, symbols=tuple(dict.fromkeys((*harness.SYMBOLS, *EXTRA_SYMBOLS))))
    digest = hashlib.sha256(FIXTURE.read_bytes()).hexdigest()
    if not digest.startswith(FIXTURE_SHA256_PREFIX):
        raise SystemExit(f"fixture sha256 {digest} does not start {FIXTURE_SHA256_PREFIX}")
    harness.prepare_save(launch_config, FIXTURE, clear=True)
    header = [f"[probe] case {case} kind {kind}", f"[probe] staged ROM {rom} sha1 {sha1}",
              f"[probe] fixture {FIXTURE} sha256 {digest}",
              "[probe] SYNTH: wEventFlags+4 |= $02 (EVENT_GAVE_MYSTERY_EGG_TO_ELM) + engine warp to POKECENTER_2F "
              "(20:1) step (5,3) [(9,3) for battle]; everything else native input; no cable partner, no SLink client"]
    print("\n".join(header), flush=True)

    run = lane / "probe"
    text, pid = harness.launch(launch_config, "tools/polished_live/trade_port_probe.lua", run, {"POL_CASE": case}, args.timeout)
    print(text[-2500:])
    result = run / "result.txt"
    reasons: list[str] = []
    trace = None
    if not any(line.startswith("RESULT:") for line in text.splitlines()):
        reasons.append("no RESULT line from the driver (premature exit, deadline or crash)")
    elif "RESULT: FAIL" in text:
        reasons.append("the Lua driver finished with failed checks")
    trace_file = run / "trace.json"
    if trace_file.is_file():
        trace = json.loads(trace_file.read_text(encoding="utf-8"))
    else:
        reasons.append("no trace.json written")
    notes: list[str] = []
    if trace is not None:
        print("[probe] ordered event excerpt:\n" + "\n".join(excerpt(trace)))
        ok, why = EVALUATORS[case](trace)
        (notes if ok else reasons).extend(why)
    verdict = not reasons
    out = list(header)
    out += [f"  [note] {n}" for n in notes] + [f"  [FAIL] {r}" for r in reasons]
    out.append(f"RESULT: {'PASS' if verdict else 'FAIL'} trade-port-probe {case} (staged sha1 {sha1}, SYNTH setup, "
               f"{len(reasons)} reasons, EmuHawk pid {pid})")
    with open(result, "a", encoding="utf-8") as fh:
        fh.write("\n" + "\n".join(out) + "\n")
    print("\n".join(out))
    print(f"[probe] result.txt sha256 {hashlib.sha256(result.read_bytes()).hexdigest()} ({result})")
    return 0 if verdict else 1


if __name__ == "__main__":
    raise SystemExit(main())
