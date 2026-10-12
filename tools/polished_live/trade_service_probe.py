#!/usr/bin/env python3
"""Serverless single-EmuHawk held-service probe (Polished TRADE.md sections 15-17).

SYNTH: event flag + engine warp to POKECENTER_2F (5,3). TEST HOST: lease-only
QUERY/OFFER replies; APPLY stages a COPY of own mon 0 with a distinct OT name,
ONLY in OT slot 0 plus sender. No cable partner, server, SLink client or commit.
The pure oracle consumes ordered byte writes, not just final mailbox snapshots.
Exec-hook accounting retains bounded wrong-bank diagnostics without admitting them as
native milestones; final/complete carry all-callback totals and registered site specs.
Minimal serializer-failure traces are explicit failures, never gameplay evidence.
Native milestones are symbol-bound; close requires all three zero stores, and
the selected OT snapshot must be complete before the first OFFER slot store.
The runner requires one exact case-qualified completion and budgets the full
launch/flush/cleanup wall time; an overdue or contradictory PASS is not evidence.
Each real invocation allocates a new contained numbered attempt beneath its case
lane and keeps all prior evidence; plan/dry-run never allocates or removes files.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
WORK = Path("F:/slink-work/lanes/pol-service-probe")
RELEASE = Path("F:/slink-work/cache/polished/release/polishedcrystal-3.2.3.gbc")
FIXTURE = Path("F:/slink-work/lanes/g2int-live/pol/fixture/polished_overlay_warp.SaveRAM")
FIXTURE_SHA256_PREFIX = "75c7a5dc"
CASES = ("offer-reject", "cancel-menu", "no-eligible", "query-timeout", "apply-done1", "apply-invalid")
TOKEN = [0x54, 0x45, 0x53, 0x54]
MAGIC = [0x53, 0x4C, 0x54, 0x31, 1]
HOOKS = ("SlinkTradeEntry", "SlinkTradeClose", "SlinkTradeCheckHeader", "Script_endtext", "GetScriptByte",
         "SelectTradeOrDayCareMon", "YesNoBox", "NoYesBox", "Special_TryQuickSave", "SlinkTradeTimeoutGate",
         "OWPlayerInput", "SetInitialOptions.joypad_loop")
EXTRA_SYMBOLS = HOOKS + ("wPartyMon1", "wOTPartyMon1", "wOTPartyMonOTs", "wOTPartyMonNicknames", "wOTPlayerName",
                         "hScriptBank", "hScriptPos", "hVBlank", "wScriptStackSize")
SYNTH = ("SYNTH event flag wEventFlags+4 |= $02 + engine warp POKECENTER_2F (20:1) (5,3); "
         "TEST HOST copies own mon 0 to OT slot 0 with distinct OT name and sender for APPLY; "
         "no cable partner, server, SLink client, or native commit")
HOOK_KINDS = {
    "service_entry": "SlinkTradeEntry", "close": "SlinkTradeClose",
    "check_header": "SlinkTradeCheckHeader", "endtext": "Script_endtext", "gsb": "GetScriptByte",
    "party_menu": "SelectTradeOrDayCareMon", "yesno": "YesNoBox", "noyes": "NoYesBox",
    "try_quicksave": "Special_TryQuickSave", "timeout_gate": "SlinkTradeTimeoutGate",
    "OWPlayerInput": "OWPlayerInput", "SetInitialOptions.joypad_loop": "SetInitialOptions.joypad_loop",
    "service_return": "service_return",
}
ACCEPTED_KINDS = frozenset(HOOK_KINDS) | {
    "setup", "answer", "rom_write", "host_begin", "host_write", "host_stage_write", "host_end",
    "staged", "query_answered", "offer_observed", "offer_answered", "apply_published",
    "done_observed", "release_published", "wrong_bank", "final", "move_start", "move_end", "complete",
}
# Kinds the recorder emits to REPORT a failure (trade_service_probe.lua): they are not part of a healthy trace, so
# they are not in ACCEPTED_KINDS, but each must still produce its own named reason (never the generic unknown-kind one).
FAILURE_KINDS = frozenset({
    "wrong_pc", "missing_symbol", "gsb_overflow", "driver_error", "instrumentation_error", "deadline", "early_exit",
})
# Pinned native sites for standalone synthetic traces. Production supplies the
# full symbol table only after checking its digest against overlay provenance.
MILESTONE_SITES = {
    "SlinkTradeEntry": [0x7E, 0x4480],
    "SlinkTradeTimeoutGate": [0x7E, 0x4410],
    "SelectTradeOrDayCareMon": [0x14, 0x4002],
    "YesNoBox": [0, 0x18E0],
}
# ld a,[addr] (3), cp (2), jr (2), call SlinkTradeEntry (3).
# The entry is a JP trampoline: RET must resume at the gate's CALL continuation.
RETURN_DELTA = 10

# Code-only ranges, excluding padding, item tables and proposer text. Their
# endpoints come from the provenance-verified linked .sym, never byte scanning.
STORE_CODE_SPANS = (
    ("SlinkTradeCheckHeader", "SlinkTradeFrameEnd"),
    ("SlinkTradeItemAllowed", "SlinkTradeItemAllowedEnd"),
    ("SlinkTradeWaitGate", "SlinkTradeGatesCodeEnd"),
    ("SlinkTradeEntry", "SlinkTradeGatesEnd"),
    ("SlinkTradeSnapshot", "SlinkTradeSnapshotEnd"),
    ("SlinkTradeValidateRecord", "SlinkTradeValidateEnd"),
    ("SlinkTradeProposerService", "SlinkTradeConfirmText"),
)
_SM83_TWO_BYTE = frozenset(range(0x06, 0x40, 8)) | {
    0x10, 0x18, 0x20, 0x28, 0x30, 0x38, 0xCB, 0xC6, 0xCE, 0xD6,
    0xDE, 0xE0, 0xE6, 0xE8, 0xEE, 0xF0, 0xF6, 0xF8, 0xFE,
}
_SM83_THREE_BYTE = frozenset({
    0x01, 0x08, 0x11, 0x21, 0x31, 0xC2, 0xC3, 0xC4, 0xCA, 0xCC,
    0xCD, 0xD2, 0xD4, 0xDA, 0xDC, 0xEA, 0xFA,
})
_SM83_INVALID = frozenset({0xD3, 0xDB, 0xDD, 0xE3, 0xE4, 0xEB, 0xEC, 0xED, 0xF4, 0xFC, 0xFD})
_A_STORES = frozenset({0x12, 0x22, 0x32, 0x77, 0xEA})


def build_native_store_sites(rom, symbols):
    """Linear-decode staged proposer/helper code; never admit an operand byte.

    Polished macros/rst.asm farcall/farjp emit rst $10 + inline dwb (four
    bytes). The pinned FarCall ABI consumes that pointer, so it is data, not
    three more instructions. Validate every linked code label's boundary too.
    """
    if symbols.get("FarCall") != [0, 0x10]:
        raise ValueError("store-site decoder requires Polished rst $10 FarCall ABI")
    sites, regions = {}, []
    for start_name, end_name in STORE_CODE_SPANS:
        bank, start = symbols[start_name]
        end_bank, end = symbols[end_name]
        if bank != 0x7E or end_bank != bank or not 0x4000 <= start < end <= 0x8000:
            raise ValueError(f"invalid store-site code range: {start_name}")
        offset = bank * 0x4000 + start - 0x4000
        code = rom[offset:offset + end - start]
        if len(code) != end - start:
            raise ValueError(f"truncated store-site ROM range: {start_name}")
        pos, starts, count = 0, set(), 0
        while pos < len(code):
            addr, opcode = start + pos, code[pos]
            starts.add(addr)
            if opcode in _SM83_INVALID:
                raise ValueError(f"invalid SM83 opcode at {bank:02x}:{addr:04x}")
            size = 4 if opcode == 0xD7 else 3 if opcode in _SM83_THREE_BYTE else 2 if opcode in _SM83_TWO_BYTE else 1
            if pos + size > len(code):
                raise ValueError(f"instruction crosses store-site code end: {end_name}")
            if opcode in _A_STORES:
                sites[str(addr)] = {"opcode": opcode, "bytes": list(code[pos:pos + size])}
                count += 1
            pos += size
        for name, (label_bank, addr) in symbols.items():
            if label_bank == bank and start <= addr < end and addr not in starts:
                raise ValueError(f"code symbol is not an instruction start: {name}")
        regions.append({"start_symbol": start_name, "end_symbol": end_name,
                        "start": start, "end": end, "store_sites": count})
    return {"bank": 0x7E, "rom_sha1": hashlib.sha1(rom).hexdigest(), "regions": regions, "sites": sites}


def _hook_accounting(trace, final, complete, symbols, why):
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    from tools.polished_live.faint_probe import validate_hook_counts

    sites = final.get("hook_sites")
    expected_names = set(HOOKS) | {"service_return"}
    if not isinstance(sites, dict) or set(sites) != expected_names or any(
        not isinstance(site, dict) or set(site) != {"bank", "addr"} or
        type(site["bank"]) is not int or not 0 <= site["bank"] <= 255 or
        type(site["addr"]) is not int or not 0 <= site["addr"] < 0x8000
        for site in (sites.values() if isinstance(sites, dict) else ())
    ):
        why.append("malformed hook_sites: expected every static hook and service_return")
        return
    if sites["SlinkTradeEntry"] != {"bank": 0x7E, "addr": 0x4480}:
        why.append("hook_sites: service entry differs from pinned 7e:4480")
    if symbols is not None:
        for name in HOOKS:
            expected = symbols.get(name)
            if expected is None or sites[name] != {"bank": expected[0], "addr": expected[1]}:
                why.append(f"hook_sites: {name} differs from symbols")
    native = MILESTONE_SITES if symbols is None else symbols
    for name in MILESTONE_SITES:
        expected = native.get(name)
        if expected is None or sites[name] != {"bank": expected[0], "addr": expected[1]}:
            why.append(f"milestone provenance: {name} differs from native symbols")
    gate = native.get("SlinkTradeTimeoutGate")
    continuation = {"bank": gate[0], "addr": gate[1] + RETURN_DELTA} if gate else None
    if sites["service_return"] != continuation:
        why.append("milestone provenance: service return is not timeout-gate CALL continuation")
    entry = next(iter(_events(trace, "service_entry")), None)
    if not entry or type(entry.get("return_addr")) is not int or type(entry.get("return_bank")) is not int or (
        sites["service_return"] != {"bank": entry.get("return_bank"), "addr": entry.get("return_addr")}
        or entry.get("return_bank") != entry.get("bank")
    ):
        why.append("hook_sites: service_return differs from entry continuation observation")
    for e in trace:
        if e.get("kind") in HOOK_KINDS and e.get("hook_site") != HOOK_KINDS[e["kind"]]:
            why.append(f"malformed hook metadata: {e['kind']} site identity")
        if e.get("kind") == "wrong_pc":
            why.append("wrong_pc: bank-matched callback has incorrect PC")
    why.extend(validate_hook_counts(trace, final, sites))
    for terminal in (final, complete):
        if terminal is None or terminal.get("completed") is not True or type(terminal.get("driver_errors")) is not int or terminal["driver_errors"] != 0:
            why.append("driver accounting: missing completion or driver errors")
    if complete and any(complete.get(k) != final.get(k) for k in
                        ("hook_sites", "hook_counts", "wrong_bank_sample_limit", "driver_errors", "completed")):
        why.append("malformed hook accounting: complete differs from final")


def _bytes(value):
    return isinstance(value, list) and len(value) == 16 and all(type(v) is int and 0 <= v <= 255 for v in value)


def _checksum(data):
    value = 0x811C9DC5
    for byte in data:
        value = ((value ^ byte) * 0x01000193) & 0xFFFFFFFF
    return f"{value:08x}"


def _header(frame, command):
    return frame[:5] == MAGIC and frame[5] == command


def _events(trace, kind):
    return [e for e in trace if e.get("kind") == kind]


def _once(trace, kind, why):
    events = _events(trace, kind)
    if len(events) != 1:
        why.append(f"{kind}: expected exactly once, observed {len(events)}")
        return None
    return events[0]


def _check_staging(trace, why):
    staged = _once(trace, "staged", why)
    writes = _events(trace, "host_stage_write")
    expected = [(name, i) for name, length in (("wOTPartyMon1", 48), ("wOTPartyMonOTs", 11),
                                             ("wOTPartyMonNicknames", 11), ("wOTPlayerName", 11))
                for i in range(length)]
    if [(e.get("symbol"), e.get("offset")) for e in writes] != expected:
        why.append("staging escaped OT slot 0/sender or omitted bytes")
    if not staged:
        return None
    try:
        pieces = {key: bytes.fromhex(staged[key]) for key in
                  ("record", "ot", "nick", "sender", "source_record", "source_ot", "source_nick")}
    except (KeyError, ValueError, TypeError):
        why.append("staging copy evidence missing or malformed")
        return staged
    if any(len(pieces[k]) != n for k, n in (("record", 48), ("source_record", 48), ("ot", 11),
                                          ("source_ot", 11), ("nick", 11), ("source_nick", 11), ("sender", 11))):
        why.append("staging copy evidence has wrong lengths")
        return staged
    if pieces["record"] != pieces["source_record"]:
        why.append("incoming record is not a COPY of own mon 0")
    if pieces["ot"][:8] == pieces["source_ot"][:8] or pieces["ot"][8:] != pieces["source_ot"][8:]:
        why.append("staged OT name not distinct or OT metadata changed")
    if pieces["sender"][:8] != pieces["ot"][:8]:
        why.append("staged sender differs from incoming OT name")
    for field, limit in (("ot", 8), ("sender", 11), ("nick", 11)):
        data = pieces[field][:limit]
        valid = 0x53 in data and all(v >= 0x5F for v in data[:data.index(0x53)])
        if field == "nick" and staged.get("invalid"):
            if 0x53 in data:
                why.append("apply-invalid incoming name still has a terminator")
        elif not valid:
            why.append(f"staged {field} has no valid name terminator")
    if not staged.get("invalid") and pieces["nick"] != pieces["source_nick"]:
        why.append("incoming nickname is not copied from own mon 0")
    payload = pieces["record"] + pieces["ot"] + pieces["nick"]
    if _checksum(payload) != staged.get("staged_sum"):
        why.append("staged payload checksum evidence disagrees")
    expected_values = list(pieces["record"] + pieces["ot"] + pieces["nick"] + pieces["sender"])
    if [e.get("value") for e in writes] != expected_values:
        why.append("staging byte writes disagree with disclosed payload")
    return staged


def _write_audit(trace, why):
    """Check continuous before/after images and each publisher's actual store order.

    Host transaction boundaries are frame-end observations. ROM QUERY/OFFER publish
    at generation, DONE at ACK; APPLY pickup's ACK is distinct from DONE publication.
    """
    publications, host_transactions = [], []
    active, host_writes, rom_pending, previous = None, [], [], None
    last_host_frame = None
    rom_waiting_for_host = None
    for e in trace:
        kind = e["kind"]
        frame = e["lease"]
        if previous is not None and frame != previous and kind not in ("rom_write", "host_write"):
            why.append("lease changed without an ordered write observation")
        if kind == "host_begin":
            if active is not None:
                why.append("nested host transaction")
            active, host_writes = e, []
            tx = e.get("tx")
            if e.get("pump") != "onframeend":
                why.append("host transaction did not originate in frame-end pump")
            allowed = ((tx == "query" and _header(frame, 1) and frame[6] != frame[7]) or
                       (tx == "offer" and _header(frame, 2) and frame[6] != frame[7]) or
                       (tx == "apply" and _header(frame, 2) and frame[6] == frame[7] and frame[8] == 0) or
                       (tx == "release" and _header(frame, 7) and frame[6] == frame[7] and frame[8] == 1))
            if not allowed:
                why.append(f"host wrote while ROM-owned ({tx})")
            if e["frame"] == last_host_frame:
                why.append("host pumped more than once in one emulator frame")
            last_host_frame = e["frame"]
        elif kind == "host_stage_write":
            if not active or active.get("tx") != "apply" or host_writes:
                why.append("host staging outside APPLY ownership or after frame publication began")
        elif kind in ("host_write", "rom_write"):
            offset, value, before = e.get("offset"), e.get("value"), e.get("before")
            if type(offset) is not int or not 0 <= offset < 16 or type(value) is not int or not 0 <= value < 256 or not _bytes(before):
                why.append("malformed lease write evidence")
                continue
            after = list(before)
            after[offset] = value
            if after != frame or (previous is not None and before != previous):
                why.append("lease write before/after chain broken")
            if kind == "host_write":
                if not active or e.get("tx") != active.get("tx"):
                    why.append("host write outside frame-end transaction")
                host_writes.append(e)
            else:
                if active:
                    why.append("ROM write interleaved with host transaction")
                if rom_waiting_for_host and not (offset == 5 and value == 0):
                    why.append("ROM payload written after generation/ACK publication")
                rom_pending.append(e)
                cmd = frame[5]
                if offset == 6 and cmd in (1, 2) and frame[6] != frame[7]:
                    needed = {0, 1, 2, 3, 4, 5, 7, 10, 11} if cmd == 1 else {5, 7, 8, 9}
                    if not needed <= {w["offset"] for w in rom_pending[:-1]}:
                        why.append("ROM generation written before payload (publication must be last)")
                    if frame[6] != (before[6] + 1) % 256 or frame[7] != before[6]:
                        why.append("ROM publication generation/ACK mismatch")
                    if not _header(frame, cmd):
                        why.append("ROM publication has invalid lease header")
                    if cmd == 2 and (frame[9] != 0 or frame[12:16] != TOKEN or frame[8] != 255):
                        why.append("OFFER chosen slot/token/result mismatch")
                    publications.append(("query" if cmd == 1 else "offer", e))
                    rom_pending = []
                    rom_waiting_for_host = cmd
                elif offset == 7 and cmd == 7 and frame[6] == frame[7]:
                    needed = {0, 1, 2, 3, 4, 5, 6, 8, 9, 12, 13, 14, 15}
                    if not needed <= {w["offset"] for w in rom_pending[:-1]}:
                        why.append("ROM DONE ACK written before payload (publication must be last)")
                    if not _header(frame, 7) or frame[12:16] != TOKEN or frame[9] != 0:
                        why.append("DONE slot/token/header mismatch")
                    publications.append(("done", e))
                    rom_pending = []
                    rom_waiting_for_host = cmd
                elif offset == 5 and value == 0:
                    publications.append(("closed", e))
                    rom_waiting_for_host = None
        elif kind == "host_end":
            if not active or e.get("tx") != active.get("tx"):
                why.append("unmatched host transaction end")
            else:
                tx, start = active["tx"], active["lease"]
                expected = {"query": [10, 11, 12, 13, 14, 15, 7], "offer": [8, 7],
                            "apply": [*range(16), 6], "release": [5]}.get(tx)
                if [w.get("offset") for w in host_writes] != expected:
                    why.append(f"host {tx} generation/ACK written before payload or write sequence incomplete")
                if tx == "query" and (frame[10] != 1 or frame[11] not in (0, 1) or frame[12:16] != TOKEN or frame[7] != start[6]):
                    why.append("QUERY answer available/mask/token/ACK mismatch")
                if tx == "offer" and (frame[8] not in (0, 1) or frame[7] != start[6]):
                    why.append("OFFER answer result/ACK mismatch")
                if tx == "apply":
                    expected_frame = MAGIC + [5, (start[6] + 1) % 256, start[6], 255, 0, 1, 0] + TOKEN
                    if frame != expected_frame:
                        why.append("APPLY generation/ACK/slot/token/frame mismatch")
                    unpublished = MAGIC + [5, start[6], start[6], 255, 0, 1, 0] + TOKEN
                    if [w["value"] for w in host_writes[:-1]] != unpublished:
                        why.append("host APPLY exposed generation before complete payload")
                if tx == "release" and (frame[5] != 8 or frame[:5] + frame[6:] != start[:5] + start[6:]):
                    why.append("RELEASE changed fields other than command")
                host_transactions.append((tx, active, e))
                rom_waiting_for_host = None
            active, host_writes = None, []
        previous = frame
    if active:
        why.append("unfinished host transaction")
    return publications, host_transactions


def evaluate(case, trace, symbols=None) -> tuple[bool, list[str]]:
    """Fail closed on incomplete instrumentation, invalid protocol, or non-restored machine state."""
    why: list[str] = []
    if case not in CASES:
        return False, [f"unknown case {case}"]
    if not isinstance(trace, list) or not trace or any(not isinstance(e, dict) for e in trace):
        return False, ["missing/malformed ordered trace"]
    for e in trace:
        kind = e.get("kind")
        if isinstance(kind, str) and kind in FAILURE_KINDS:
            why.append(f"{kind}: observation incomplete")  # reported by name, never silently accepted
            continue
        if not isinstance(kind, str) or kind not in ACCEPTED_KINDS:
            return False, [f"unaccepted event kind {kind!r}: observation incomplete"]
    required = ("kind", "ord", "frame", "sp", "bank", "vblank", "link", "running", "stack", "count",
                "party_sum", "ot_tail_sum", "ot0_sum", "ot1_sum", "own0_sum", "lease", "sbank", "spos", "x", "y")
    if any(any(k not in e for k in required) or not _bytes(e.get("lease")) for e in trace):
        return False, ["missing snapshot fields or malformed 16-byte lease"]
    if any(type(e[k]) is not int for e in trace for k in
           ("ord", "frame", "sp", "bank", "vblank", "link", "running", "stack", "count", "sbank", "spos", "x", "y")):
        return False, ["malformed numeric snapshot fields"]
    if [e["ord"] for e in trace] != list(range(1, len(trace) + 1)) or any(a["frame"] > b["frame"] for a, b in zip(trace, trace[1:], strict=False)):
        why.append("trace order is not continuous/monotonic")
    if any(not isinstance(e[k], str) or not re.fullmatch(r"[0-9a-f]{8}", e[k]) for e in trace
           for k in ("party_sum", "ot_tail_sum", "ot0_sum", "ot1_sum", "own0_sum")):
        why.append("checksum evidence missing/malformed")
    setup, entry, close, ret, endtext, final, m0, m1, complete = (
        _once(trace, k, why) for k in ("setup", "service_entry", "close", "service_return", "endtext", "final",
                                      "move_start", "move_end", "complete"))
    if final:
        _hook_accounting(trace, final, complete, symbols, why)
    chain = [e for e in (setup, entry, close, ret, endtext, final, m0, m1, complete) if e]
    if any(a["ord"] >= b["ord"] for a, b in zip(chain, chain[1:], strict=False)):
        why.append("service/closure/endtext/movement events out of order")
    if close and ret:
        # SlinkTradeClose's complete native suffix is C5/10/11, including
        # idempotent zero stores. Command zero alone does not close eligibility.
        suffix = [(e.get("offset"), e.get("value")) for e in trace
                  if e["kind"] == "rom_write" and close["ord"] < e["ord"] < ret["ord"]]
        if suffix != [(5, 0), (10, 0), (11, 0)]:
            why.append("close suffix must be exactly zero stores to 5,10,11")
    if setup and (setup.get("case") != case or setup.get("synth") is not True or setup.get("token") != TOKEN):
        why.append("missing SYNTH/test-token setup disclosure")
    if complete and (complete.get("case") != case or complete is not trace[-1]):
        why.append("early exit: complete must be the final event for this case")
    if entry and (entry["bank"] != 0x7E or entry.get("pc") != 0x4480):
        why.append("service entry is not bank-qualified 7e:4480")
    if entry and ret and (ret.get("ret_sp") != entry["sp"] or ret["sp"] != entry["sp"] + 2):
        why.append("return SP != entry SP (RET boundary / CALL continuation unbalanced)")
    if endtext and (endtext["sbank"], endtext["spos"], endtext["bank"]) != (0x2D, 0x7596, 0x25):
        why.append("Script_endtext did not use the endtext redirect")
    redirects = [e for e in _events(trace, "gsb") if (e["sbank"], e["spos"]) == (0x2D, 0x7595)]
    if len(redirects) != 1 or not ret or not endtext or not ret["ord"] < redirects[0]["ord"] < endtext["ord"]:
        why.append("missing ordered interpreter endtext redirect read")
    if final:
        if final["lease"][5] != 0:
            why.append("lease not closed (command != 0)")
        if final["lease"][10:12] != [0, 0]:
            why.append("final available/mask must both be zero")
        for key in ("vblank", "link", "running", "stack"):
            if final[key] != 0:
                why.append(f"final {key} != 0")
    if m0 and m1 and (m0["x"], m0["y"]) == (m1["x"], m1["y"]):
        why.append("player did not move")
    if entry:
        for e in trace[entry["ord"] - 1:]:
            if (e["party_sum"], e["count"]) != (entry["party_sum"], entry["count"]):
                why.append("party/OT/nickname checksum changed (or party count changed)")
                break
        if any(e["ot_tail_sum"] != entry["ot_tail_sum"] for e in trace[entry["ord"] - 1:]):
            why.append("OT slots 2..5 checksum changed; only slot 1 snapshot and slot 0 staging allowed")
    if any(e["lease"][5] == 7 and e["lease"][8] == 0 for e in trace):
        why.append("DONE result 0 observed: commit must remain disabled")
    if case != "apply-done1" and any(e["lease"][5] == 7 for e in trace):
        why.append("unexpected DONE on rejection/cancel/invalid/timeout path")
    if case in ("cancel-menu", "no-eligible", "query-timeout") and any(e["lease"][5] == 2 for e in trace):
        why.append("unexpected OFFER on cancel/ineligible/timeout path")
    pubs, txs = _write_audit(trace, why)
    expected_pubs = {
        "offer-reject": ["query", "offer", "closed"], "cancel-menu": ["query", "closed"],
        "no-eligible": ["query", "closed"], "query-timeout": ["query", "closed"],
        "apply-done1": ["query", "offer", "done", "closed"], "apply-invalid": ["query", "offer", "closed"],
    }[case]
    if [p[0] for p in pubs] != expected_pubs:
        why.append(f"ROM publication order: expected {expected_pubs}, observed {[p[0] for p in pubs]}")
    expected_txs = {"offer-reject": ["query", "offer"], "cancel-menu": ["query"], "no-eligible": ["query"],
                    "query-timeout": [], "apply-done1": ["query", "offer", "apply", "release"],
                    "apply-invalid": ["query", "offer", "apply"]}[case]
    if [t[0] for t in txs] != expected_txs:
        why.append(f"host transaction order: expected {expected_txs}, observed {[t[0] for t in txs]}")
    for name, begin, end in txs:
        if name == "query" and end["lease"][11] != (0 if case == "no-eligible" else 1):
            why.append("QUERY eligibility mask incorrect for case")
        if name == "offer" and end["lease"][8] != (1 if case == "offer-reject" else 0):
            why.append("OFFER decision incorrect for case")
        if entry and ret and not entry["ord"] < begin["ord"] < end["ord"] < ret["ord"]:
            why.append("host transaction outside held service")
    menus = _events(trace, "party_menu")
    answers = [e for e in _events(trace, "answer") if e.get("which") == "party"]
    want_menu = case not in ("no-eligible", "query-timeout")
    if len(menus) != int(want_menu):
        why.append(f"party menu: expected {int(want_menu)}, observed {len(menus)}")
    query_end = next((t[2] for t in txs if t[0] == "query"), None)
    if want_menu:
        if menus and (not query_end or menus[0]["ord"] <= query_end["ord"]):
            why.append("party menu opened before QUERY answer")
        if len(answers) != 1 or answers[0].get("btn") != ("B" if case == "cancel-menu" else "A"):
            why.append("native party menu answer missing/incorrect")
        if answers and menus and answers[0]["ord"] <= menus[0]["ord"]:
            why.append("native party input preceded menu milestone")
        if entry and ret and (not menus or not answers or
                              not entry["ord"] < menus[0]["ord"] < answers[0]["ord"] < ret["ord"]):
            why.append("native party menu/input outside held service")
    if case in ("offer-reject", "apply-done1", "apply-invalid"):
        confirms = [e for e in _events(trace, "answer") if e.get("which") == "confirm"]
        offers = [p[1] for p in pubs if p[0] == "offer"]
        native_yesnos = [e for e in _events(trace, "yesno")
                        if entry and ret and entry["ord"] < e["ord"] < ret["ord"]]
        offer_start = next((e for e in trace if e["kind"] == "rom_write" and e.get("offset") == 9
                            and query_end and offers and query_end["ord"] < e["ord"] <= offers[0]["ord"]), None)
        if len(confirms) != 1 or confirms[0].get("btn") != "A" or not offers or confirms[0]["ord"] >= offers[0]["ord"]:
            why.append("OFFER without prior native YES confirmation")
        if (not query_end or len(menus) != 1 or len(answers) != 1 or len(native_yesnos) != 1 or
                len(confirms) != 1 or not offer_start or not offers or
                not query_end["ord"] < menus[0]["ord"] < answers[0]["ord"] < native_yesnos[0]["ord"] <
                confirms[0]["ord"] < offer_start["ord"] <= offers[0]["ord"]):
            why.append("native confirmation order must be QUERY-answer/menu/party/YesNo/confirm/OFFER")
        if entry and final and final["ot1_sum"] != entry["own0_sum"]:
            why.append("OT slot 1 snapshot does not match selected own mon 0")
        if entry and len(confirms) == 1 and offer_start:
            # Snapshot copies may have intermediate checksums only after YES
            # input and before O's first slot store, not until generation.
            if any(e["ot1_sum"] != entry["ot1_sum"] for e in trace
                   if entry["ord"] <= e["ord"] <= confirms[0]["ord"]):
                why.append("snapshot timing: OT slot 1 changed through confirmation boundary")
            if any(e["ot1_sum"] != entry["own0_sum"] for e in trace
                   if e["ord"] >= offer_start["ord"]):
                why.append("snapshot timing: first OFFER store and later rows must retain selected snapshot")
    elif entry:
        if any(e["ot1_sum"] != entry["ot1_sum"] for e in trace if e["ord"] >= entry["ord"]):
            why.append("snapshot timing: OT slot 1 changed without an OFFER snapshot")
    if case.startswith("apply-"):
        staged = _check_staging(trace, why)
        if staged and staged.get("invalid") != (case == "apply-invalid"):
            why.append("staging validity disclosure incorrect for case")
        if final and staged and final["ot0_sum"] != staged.get("staged_sum"):
            why.append("staged OT slot 0 changed after staging")
        apply_end = next((t[2] for t in txs if t[0] == "apply"), None)
        pickups = [e for e in _events(trace, "rom_write") if e.get("offset") == 7 and e["lease"][5] == 5]
        if len(pickups) != 1 or not apply_end or pickups[0]["ord"] <= apply_end["ord"] or pickups[0]["lease"][7] != apply_end["lease"][6]:
            why.append("missing matching APPLY pickup ACK")
        dones = [p[1] for p in pubs if p[0] == "done"]
        if case == "apply-done1" and (len(dones) != 1 or not apply_end or dones[0]["lease"][8] != 1 or
                                       dones[0]["lease"][6] != apply_end["lease"][6]):
            why.append("APPLY must publish matching DONE result 1, never success")
    else:
        if _events(trace, "host_stage_write") or _events(trace, "staged"):
            why.append("unexpected incoming staging")
        if entry and final and final["ot0_sum"] != entry["ot0_sum"]:
            why.append("OT slot 0 changed without host staging")
    if case == "query-timeout":
        query = next((p[1] for p in pubs if p[0] == "query"), None)
        closed = next((p[1] for p in pubs if p[0] == "closed"), None)
        if not query or not closed or not 600 <= closed["frame"] - query["frame"] <= 700:
            why.append("QUERY timeout did not wait 600 frames within 700-frame budget")
    return not why, list(dict.fromkeys(why))


def read_provenance(path):
    provenance = json.loads(Path(path).read_text(encoding="utf-8"))
    for digest in (provenance.get("base_sha1"), provenance.get("output", {}).get("sha1")):
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{40}", digest):
            raise ValueError("provenance must contain base_sha1 and output.sha1 (40 hex digits)")
    return provenance["base_sha1"].lower(), provenance["output"]["sha1"].lower()


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--case", choices=CASES, required=True)
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    return args


def plan(args, repo=REPO, work=WORK):
    """Describe the retained case lane without allocating an attempt."""
    base_sha1, overlay_sha1 = read_provenance(repo / "data/polished/overlay_provenance.json")
    return {"case": args.case, "timeout": args.timeout, "case_lane": str(work / args.case), "lane": None,
            "attempt_policy": "fresh attempt-NNNN subdirectory; retain previous evidence",
            "driver": str(repo / "tools/polished_live/trade_service_probe.lua"),
            "release": str(RELEASE), "fixture": str(FIXTURE), "base_sha1": base_sha1,
            "expected_overlay_sha1": overlay_sha1, "disclosure": SYNTH,
            "service_budget_frames": 700 if args.case == "query-timeout" else 2400,
            "cleanup": "harness.launch finally kills only its own EmuHawk PID"}


def read_symbols(path):
    return _parse_symbols(path.read_text(encoding="utf-8"))


from tools.polished_live.runtime import parse_symbols as _parse_symbols  # noqa: E402


def read_verified_symbols(repo):
    path = repo / "data/polished/polished_slink.sym"
    provenance = json.loads((repo / "data/polished/overlay_provenance.json").read_text(encoding="utf-8"))
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != provenance.get("symbols", {}).get(path.name):
        raise ValueError("symbols differ from overlay provenance")
    return _parse_symbols(raw.decode("utf-8"))


def stage(lane, run_plan):
    from tools.polished_live.runtime import stage_pinned_rom
    rom = lane / "rom/pol_overlay.gbc"
    sha1 = stage_pinned_rom(RELEASE, rom, run_plan["expected_overlay_sha1"],
                            patch=REPO / "patch/dist/SLink-Polished.ups",
                            base_sha1=run_plan["base_sha1"])
    return rom, sha1


def main(argv=None):
    args = parse_args(argv)
    run_plan = plan(args)
    if args.dry_run:
        print(json.dumps(run_plan, indent=2), flush=True)
        return 0
    root = WORK.resolve()
    case_lane = Path(run_plan["case_lane"])
    if root not in case_lane.resolve().parents:
        raise ValueError(f"refusing lane outside {WORK}: {case_lane}")
    case_lane.mkdir(parents=True, exist_ok=True)
    attempt = 1
    while True:
        lane = case_lane / f"attempt-{attempt:04d}"
        if root not in lane.resolve().parents:
            raise ValueError(f"refusing lane outside {WORK}: {lane}")
        try:
            lane.mkdir()  # Exclusive allocation; never reuse or erase evidence.
        except FileExistsError:
            attempt += 1
        else:
            break
    run_plan["lane"] = str(lane)
    print(json.dumps(run_plan, indent=2), flush=True)
    from dataclasses import replace

    from tools.polished_live import harness
    rows = read_verified_symbols(REPO)
    # Optional code hooks are omitted from syms.json when absent; Lua records missing_symbol, oracle FAILS.
    required = tuple(harness.SYMBOLS) + tuple(s for s in EXTRA_SYMBOLS if s not in HOOKS)
    missing = sorted(set(required) - rows.keys())
    if missing:
        raise ValueError(f"required data/setup symbols missing: {missing}")
    symbols = tuple(dict.fromkeys((*required, *(s for s in HOOKS if s in rows))))
    rom, sha1 = stage(lane, run_plan)
    store_sites = build_native_store_sites(rom.read_bytes(), rows)
    store_path = lane / "native_store_sites.json"
    store_path.write_text(json.dumps(store_sites, indent=2), encoding="utf-8")
    launch_config = replace(harness.launch_config(dict(os.environ, POL_LANE=str(lane), POL_KIND="overlay")),
                            rom_src=rom, rom=rom, symbols=symbols)
    digest = hashlib.sha256(FIXTURE.read_bytes()).hexdigest()
    if not digest.startswith(FIXTURE_SHA256_PREFIX):
        raise ValueError("fixture sha256 differs from qualified fixture")
    harness.prepare_save(launch_config, FIXTURE)
    run = lane / "probe"
    started = time.monotonic()
    text, pid = harness.launch(launch_config, "tools/polished_live/trade_service_probe.lua", run,
                               {"POL_CASE": args.case, "POL_STORE_SITES": store_path.as_posix()}, args.timeout)
    elapsed = time.monotonic() - started
    deadline_met = elapsed < args.timeout
    reasons = []
    markers = [line.strip() for line in text.splitlines() if "RESULT:" in line]
    expected = rf"RESULT: PASS trade-service-probe {re.escape(args.case)} \(0 checks failed\) frame [0-9]+"
    if len(markers) != 1 or not re.fullmatch(expected, markers[0]):
        reasons.append("completion marker: require exactly one zero-failure PASS for selected case, no other RESULT")
    if not deadline_met:
        reasons.append("wall-clock deadline reached before launch/cleanup completed")
    core = {"qualification": "requested, not observed", "source": "config.ini PreferredCores.GBC"}
    try:
        core["name"] = json.loads((run / "config.ini").read_text(encoding="utf-8"))["PreferredCores"]["GBC"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        core["name"] = None
        reasons.append(f"requested core unavailable from config.ini: {exc}")
    path = run / "trace.json"
    try:
        trace = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        reasons.append(f"trace unavailable: {exc}")
    else:
        _, failures = evaluate(args.case, trace, rows)
        reasons.extend(failures)
        print("\n".join(f"#{e.get('ord')} f{e.get('frame')} {e.get('kind')} lease={e.get('lease')}"
                        for e in trace if e.get("kind") != "gsb"))
    result = {**run_plan, "staged_sha1": sha1, "fixture_sha256": digest, "emuhawk_pid": pid,
              "elapsed_seconds": elapsed, "deadline_seconds": args.timeout, "deadline_met": deadline_met,
              "core": core, "native_store_site_count": len(store_sites["sites"]),
              "native_store_sites": str(store_path), "ok": not reasons, "reasons": reasons}
    (run / "oracle.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 1 if reasons else 0


if __name__ == "__main__":
    raise SystemExit(main())
