#!/usr/bin/env python3
"""Read-only cold reload identity/census probe; no live trade or writer authorization.

Live: --fixture SAVE --expected EXPECTED --route ROUTE [--dry-run].
Offline: --offline --fixture SAVE --expected EXPECTED [--dry-run].
EXPECTED JSON (slots zero-based, uppercase canonical keys; OT = 8 name + 3 Extra):
 {"schema":"polished-reload-expected-v1", "party_count":1,
  "party":[{"slot":0,"key":"123456:ABCD:001:00","ot_hex":"8081535353535353010203"}],
  "absent_key":"654321:DCBA:002:00"}
Optional player={id_hex:4 hex,name_hex:16 hex}, box_counts=20 integers0..20,
overlay_version_hex=40 hex. ROUTE={steps:[{phase:setup|idle,frames:N,buttons:[]}]};
include released idle. Buttons are native timed holds from cold boot, never writes.

Verdicts (corruption/completion outrank findings): FAIL malformed/mutation/overflow/
driver error; INCOMPLETE missing completion/load/checkpoint/version evidence;
CENSUS_MISMATCH inconsistent or unreadable referenced storage; STILL_PRESENT_GIVEN;
PARTY_COUNT_MISMATCH; MISSING_RECEIVED; OT_MISMATCH; PASS. NO_HIT means a completed
recording lacked a qualified cold-load stable snapshot, and is NEVER PASS.
PASS compares expected slot keys/OT and given absence, not every stat/dex/mail field,
not two-player atomicity or backing-device durability. Offline checks BOTH native
copy checksums/markers before load; live observes the engine's actual accepted copy.
Cold Continue repairs copies/storage: pre-load battery and post-load SRAM differ.
Unreferenced PokeDB garbage is deliberately ignored. The overlay version lives in
ROM, NOT SaveRAM: offline reports it unavailable; requiring it makes INCOMPLETE.
Both 32768-byte batteries and batteries with the exact 22-byte RTC footer are read
verbatim. Dry-run reads and validates provenance before ANY side effect; no verdict.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import time
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from tools.polished_live import (  # noqa: E402
    derive_save as ds,
    dispatch_probe as dispatch,
)
from tools.polished_live.faint_probe import read_symbols, validate_hook_counts  # noqa: E402

pc = ds.pc
WORK = Path("F:/slink-work/lanes/pol-reload-probe")
RELEASE = Path("F:/slink-work/cache/polished/release/polishedcrystal-3.2.3.gbc")
SCHEMA = "polished-reload-contract-v1"
EXPECTED_SCHEMA = "polished-reload-expected-v1"
KEY = re.compile(r"[0-9A-F]{6}:[0-9A-F]{4}:[0-9A-F]{3}:[0-9A-F]{2}\Z")
SITES = {"dispatch": "SlinkTradeDispatch", "load_begin": "TryLoadSaveFile",
         "primary_fail": "VerifyChecksum.fail", "backup_begin": "TryLoadSaveFile.backup",
         "backup_fail": "VerifyBackupChecksum.fail", "primary_loaded": "LoadPokemonData",
         "backup_loaded": "LoadBackupPokemonData", "corrupt": "TryLoadSaveFile.corrupt"}
RAM = {"player_id": "wPlayerID", "player_name": "wPlayerName", "party_count": "wPartyCount",
       "party_mon": "wPartyMon1", "party_ot": "wPartyMonOTs", "party_nick": "wPartyMonNicknames",
       "saved": "wSavedAtLeastOnce", "poke_used_1": "wPokeDB1UsedEntries",
       "poke_used_2": "wPokeDB2UsedEntries"}
# NEWBOX.md: six discontiguous PokeDB sections, NOT boxes of contiguous records.
SECTIONS = [{"db_bank": b, "first": first, "last": last, "offset": at}
            for b, first, last, at in ((1, 1, 167, 0x4000), (1, 168, 195, 0x0000),
                                      (1, 196, 207, 0x360C), (2, 1, 167, 0x6000),
                                      (2, 168, 195, 0x0BF1), (2, 196, 207, 0x3858))]
PRIORITY = ("FAIL", "INCOMPLETE", "CENSUS_MISMATCH", "STILL_PRESENT_GIVEN",
            "PARTY_COUNT_MISMATCH", "MISSING_RECEIVED", "OT_MISMATCH", "NO_HIT")


def integer(value, low, high):
    return type(value) is int and low <= value <= high


def digest(data, algorithm="sha256"):
    return hashlib.new(algorithm, data).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def unhex(value, size, label):
    if not isinstance(value, str) or len(value) != size * 2 or re.fullmatch(r"[0-9a-fA-F]*", value) is None:
        raise ValueError(f"{label}: expected {size} exact hex bytes")
    return bytes.fromhex(value)


def canonical_key(value):
    if not isinstance(value, str) or KEY.fullmatch(value) is None or not 1 <= int(value[12:15], 16) <= 511:
        raise ValueError("expected canonical DDDDDD:OOOO:SSS:TT key")
    return value


def parse_expected(value):
    required = {"schema", "party_count", "party", "absent_key"}
    if not isinstance(value, dict) or not required <= value.keys() or value.keys() - required - {"player", "box_counts", "overlay_version_hex"}:
        raise ValueError("expected manifest fields/schema are malformed")
    if value["schema"] != EXPECTED_SCHEMA or not integer(value["party_count"], 1, 6):
        raise ValueError("expected manifest schema or party count")
    party = value["party"]
    if not isinstance(party, list) or len(party) != value["party_count"]:
        raise ValueError("manifest must describe every expected party slot")
    out = {"schema": EXPECTED_SCHEMA, "party_count": value["party_count"], "party": [],
           "absent_key": canonical_key(value["absent_key"])}
    for slot, row in enumerate(party):
        if not isinstance(row, dict) or set(row) != {"slot", "key", "ot_hex"} or type(row["slot"]) is not int or row["slot"] != slot:
            raise ValueError("expected party slots must be complete, unique, ordered and zero-based")
        out["party"].append({"slot": slot, "key": canonical_key(row["key"]),
                             "ot_hex": unhex(row["ot_hex"], 11, "expected OT").hex()})
    if any(row["key"] == out["absent_key"] for row in out["party"]):
        raise ValueError("given key cannot also be an expected party key")
    if "player" in value:
        player = value["player"]
        if not isinstance(player, dict) or set(player) != {"id_hex", "name_hex"}:
            raise ValueError("expected player requires id_hex and name_hex")
        out["player"] = {"id_hex": unhex(player["id_hex"], 2, "player ID").hex(),
                         "name_hex": unhex(player["name_hex"], 8, "player name").hex()}
    if "box_counts" in value:
        counts = value["box_counts"]
        if not isinstance(counts, list) or len(counts) != 20 or any(not integer(n, 0, 20) for n in counts):
            raise ValueError("box_counts requires twenty integers 0..20")
        out["box_counts"] = counts.copy()
    if "overlay_version_hex" in value:
        out["overlay_version_hex"] = unhex(value["overlay_version_hex"], 20, "overlay version").hex()
    return out


def db_offset(bank, entry):
    for section in SECTIONS:
        if section["db_bank"] == bank and section["first"] <= entry <= section["last"]:
            return section["offset"] + (entry - section["first"]) * pc.SAVEMON_SIZE
    raise ValueError(f"PokeDB pointer {bank}:{entry} outside 1..207")


def party_row(slot, record, ot, nickname):
    return {"slot": slot, "record_hex": record.hex(), "ot_hex": ot.hex(), "nickname_hex": nickname.hex(),
            "species_id": record[0] | ((record[21] & 0x20) << 3), "form_byte": record[21],
            "personality_byte": record[20], "level": record[31], "hp": int.from_bytes(record[34:36], "big"),
            "ot_id_hex": record[6:8].hex(), "held_item": record[1]}


def extract_save(data):
    """Pure physical-image extraction; no repairs, builder empty-box restrictions or RTC truncation."""
    if not isinstance(data, (bytes, bytearray)) or len(data) not in (ds.CART_SIZE, ds.SAVE_SIZE):
        raise ValueError("SaveRAM must be exactly 32768 or 32790 bytes")
    data = bytes(data)
    storage = {}
    # Allocation flags are WRAM-only. Offline pointers don't invent engine-observed flags.
    for copy in ds.ALL_COPIES:
        metadata = data[copy.newbox_at:copy.newbox_at + ds.NEWBOX_COUNT * ds.NEWBOX_SIZE]
        records = []
        for box in range(20):
            meta = metadata[box * 33:(box + 1) * 33]
            for slot, entry in enumerate(meta[:20]):
                if entry:
                    bank = 1 + ((meta[20 + slot // 8] >> (slot % 8)) & 1)
                    # Invalid pointers remain evidence; comparison refuses the census.
                    raw = data[db_offset(bank, entry):db_offset(bank, entry) + 49] if entry <= 207 else b""
                    records.append({"box": box, "slot": slot, "db_bank": bank, "entry": entry, "record_hex": raw.hex()})
        storage[copy.name] = {"metadata_hex": metadata.hex(), "records": records}
    storage["used_flags"] = None
    copies, integrity = {}, {}
    for copy in ds.ALL_COPIES:
        markers = data[copy.low_marker_at] == ds.LOW_MARKER and data[copy.high_marker_at] == ds.HIGH_MARKER
        computed, stored = ds.region_sum(data, copy), ds.stored_sum(data, copy)
        integrity[copy.name] = {"stored": stored, "computed": computed, "valid": stored == computed, "markers_valid": markers}
        count = data[copy.at(ds.COUNT_OFF)]
        if not integer(count, 0, 6):
            raise ValueError(f"{copy.name}: party count outside capacity")
        party = [party_row(slot, data[copy.mon_at(slot):copy.mon_at(slot) + 48],
                           data[copy.ot_at(slot):copy.ot_at(slot) + 11], data[copy.nick_at(slot):copy.nick_at(slot) + 11])
                 for slot in range(count)]
        copies[copy.name] = {"kind": "snapshot", "player_id_hex": data[copy.at(ds.ID_OFF):copy.at(ds.ID_OFF) + 2].hex(),
                             "player_name_hex": data[copy.at(ds.NAME_OFF):copy.at(ds.NAME_OFF) + 8].hex(),
                             "party_count": count, "party": party, "storage": storage,
                             "saved_at_least_once": data[copy.at(0x2F)],
                             "save_version_hex": data[ds.VERSION_AT:ds.VERSION_AT + 2].hex(),
                             "save_phase": data[ds.PHASE_AT], "overlay_version_hex": None}
    return {"copies": copies, "integrity": integrity, "save_version_hex": data[ds.VERSION_AT:ds.VERSION_AT + 2].hex(),
            "save_phase": data[ds.PHASE_AT], "fixture_sha256": digest(data), "fixture_size": len(data),
            "overlay_version": {"observable": False, "source": "not stored in SaveRAM", "hex": None}}


def decode_snapshot(snapshot):
    """Decode all occupied records through the existing codec; malformed dump != corrupt census."""
    if not isinstance(snapshot, dict) or not integer(snapshot.get("party_count"), 0, 6):
        raise ValueError("snapshot party_count outside capacity")
    party = snapshot.get("party")
    if not isinstance(party, list) or len(party) != snapshot["party_count"]:
        raise ValueError("snapshot party count/rows disagree")
    out = {"player_id_hex": unhex(snapshot.get("player_id_hex"), 2, "player ID").hex(),
           "player_name_hex": unhex(snapshot.get("player_name_hex"), 8, "player name").hex(), "party": []}
    for slot, row in enumerate(party):
        if not isinstance(row, dict) or type(row.get("slot")) is not int or row["slot"] != slot:
            raise ValueError("snapshot party slots missing/reordered")
        record = unhex(row.get("record_hex"), 48, "party record")
        ot, nickname = unhex(row.get("ot_hex"), 11, "OT"), unhex(row.get("nickname_hex"), 11, "nickname")
        for name, actual in party_row(slot, record, ot, nickname).items():
            claimed = row.get(name)
            if (type(actual) is int and type(claimed) is not int) or claimed != actual:
                raise ValueError(f"party {slot}: derived {name} differs from bytes")
        mon = pc.decode_party_mon(record, ot=ot, nickname=nickname)
        out["party"].append({**row, **mon, "key": pc.key(mon), "ot_extra_hex": ot[8:].hex()})
    storage = snapshot.get("storage")
    if not isinstance(storage, dict) or set(storage) != {"main", "backup", "used_flags"}:
        raise ValueError("snapshot needs both twenty-box metadata copies and allocation evidence")
    flags = storage["used_flags"]
    if flags is not None:
        if not isinstance(flags, list) or len(flags) != 2:
            raise ValueError("allocation flags need two 26-byte bitmaps")
        flags = [unhex(v, 26, "allocation flags") for v in flags]
    census_bad, boxes, signatures = [], {}, {}
    for name in ("main", "backup"):
        copy = storage[name]
        if not isinstance(copy, dict) or set(copy) != {"metadata_hex", "records"} or not isinstance(copy["records"], list):
            raise ValueError("storage copy needs metadata and records")
        metadata = unhex(copy["metadata_hex"], 20 * 33, "box metadata")
        pointers, seen = {}, set()
        counts, occupied = [0] * 20, []
        for box in range(20):
            meta = metadata[33 * box:33 * (box + 1)]
            for slot, entry in enumerate(meta[:20]):
                if not entry:
                    continue
                bank = 1 + ((meta[20 + slot // 8] >> (slot % 8)) & 1)
                pointers[(box, slot)] = (bank, entry)
                counts[box] += 1
                if entry > 207 or (bank, entry) in seen:
                    census_bad.append(f"{name}: invalid/aliased PokeDB pointer at {box}:{slot}")
                seen.add((bank, entry))
                if flags is not None and entry <= 207 and not flags[bank - 1][(entry - 1) // 8] & (1 << ((entry - 1) % 8)):
                    census_bad.append(f"{name}: unallocated PokeDB reference {bank}:{entry}")
        reported = set()
        for row in copy["records"]:
            if not isinstance(row, dict) or set(row) != {"box", "slot", "db_bank", "entry", "record_hex"} or any(
                not integer(row.get(field), lo, hi) for field, lo, hi in (("box", 0, 19), ("slot", 0, 19), ("db_bank", 1, 2), ("entry", 1, 255))
            ):
                raise ValueError("malformed box record coordinates")
            pos = (row["box"], row["slot"])
            if pos in reported or pointers.get(pos) != (row["db_bank"], row["entry"]):
                census_bad.append(f"{name}: duplicate/extra/mismatched record at {pos}")
            reported.add(pos)
            if row["entry"] > 207:
                continue
            raw = unhex(row["record_hex"], 49, "savemon record")
            try:
                mon = pc.decode_savemon(raw)
                occupied.append({**row, **mon, "key": pc.key(mon)})
            except ValueError as exc:
                census_bad.append(f"{name}: unreadable referenced entry at {pos}: {exc}")
        if reported != set(pointers):
            census_bad.append(f"{name}: census omitted or added occupied slots")
        boxes[name] = {"counts": counts, "mons": occupied}
        signatures[name] = pointers
    if signatures["main"] != signatures["backup"]:
        census_bad.append("gameplay and backup box pointer censuses differ")
    out.update(boxes=boxes, census_problems=census_bad)
    return out


def finding(reasons):
    return next((name for name in PRIORITY if any(r.startswith(name + ":") for r in reasons)), "PASS")


def compare_manifest(snapshot, expected):
    """The same identity/OT/absence/census comparison is used live and on BOTH battery copies."""
    try:
        expected = parse_expected(expected)
        observed = decode_snapshot(snapshot)
        if not integer(snapshot.get("saved_at_least_once"), 0, 255) or not integer(snapshot.get("save_phase"), 0, 255):
            raise ValueError("saved flag/phase missing or malformed")
        version = unhex(snapshot.get("save_version_hex"), 2, "native save version")
        overlay = snapshot.get("overlay_version_hex")
        if overlay is not None:
            overlay = unhex(overlay, 20, "ROM overlay version").hex()
    except (ValueError, KeyError, TypeError) as exc:
        return "FAIL", [f"FAIL: {exc}"]
    why = [f"CENSUS_MISMATCH: {s}" for s in observed["census_problems"]]
    if version != ds.SAVE_VERSION:
        why.append("FAIL: native save version differs from pinned layout")
    if not snapshot["saved_at_least_once"] or snapshot["save_phase"]:
        why.append("INCOMPLETE: native saved checkpoint/phase is not settled")
    if "overlay_version_hex" in expected:
        if overlay is None:
            why.append("INCOMPLETE: ROM-only overlay version unavailable from battery")
        elif overlay != expected["overlay_version_hex"]:
            why.append("FAIL: expected overlay version differs")
    all_keys = [mon["key"] for mon in observed["party"]]
    for name, boxes in observed["boxes"].items():
        all_keys.extend(mon["key"] for mon in boxes["mons"])
        if "box_counts" in expected and boxes["counts"] != expected["box_counts"]:
            why.append(f"CENSUS_MISMATCH: {name} expected box occupancies differ")
    if expected["absent_key"] in all_keys:
        why.append("STILL_PRESENT_GIVEN: given key remains in party or referenced gameplay/backup box")
    if snapshot["party_count"] != expected["party_count"]:
        why.append("PARTY_COUNT_MISMATCH: actual and expected party counts differ")
    for wanted in expected["party"]:
        slot = wanted["slot"]
        if slot >= len(observed["party"]):
            why.append(f"MISSING_RECEIVED: expected slot {slot} absent")
            continue
        actual = observed["party"][slot]
        if actual["key"] != wanted["key"]:
            a, w = actual["key"].split(":"), wanted["key"].split(":")
            if (a[0], a[2], a[3]) == (w[0], w[2], w[3]):
                why.append(f"OT_MISMATCH: slot {slot} OT ID differs")
            else:
                why.append(f"MISSING_RECEIVED: expected key at slot {slot} missing/different")
        if actual["ot_hex"] != wanted["ot_hex"]:
            why.append(f"OT_MISMATCH: slot {slot} name or three Extra bytes differ")
    if "player" in expected and any(observed["player_" + field] != value for field, value in expected["player"].items()):
        why.append("OT_MISMATCH: player ID/name differs from expected private save")
    return finding(why), why


def evaluate_offline(data, expected):
    try:
        evidence = extract_save(data)
        expected = parse_expected(expected)
    except (ValueError, KeyError, TypeError) as exc:
        return "FAIL", [f"FAIL: {exc}"]
    why = []
    for name, integrity in evidence["integrity"].items():
        if not integrity["valid"] or not integrity["markers_valid"]:
            why.append(f"FAIL: {name} native checksum/markers invalid ({integrity})")
        _, reasons = compare_manifest(evidence["copies"][name], expected)
        why.extend(reason.split(":", 1)[0] + f": {name}:" + reason.split(":", 1)[1] for reason in reasons)
    return finding(why), why


def checkpoint_ok(row, contract):
    guards = row.get("guards")
    if not isinstance(guards, dict) or set(guards) != set(dispatch.FIELDS) or any(not integer(v, 0, 255) for v in guards.values()) or not integer(row.get("svbk"), 0, 255):
        raise ValueError("checkpoint guard bytes missing/malformed")
    stack = unhex(row.get("stack_hex"), 28, "checkpoint stack")
    clean = dispatch.engine_clean({**guards, "svbk": row["svbk"]})
    matches = dispatch.stack_matches(stack, contract["pins"])
    return clean, matches


def evaluate(trace, expected, config):
    """Replay native load witnesses and stable-frame gate; never trust a snapshot's success flags."""
    try:
        expected = parse_expected(expected)
        if not isinstance(trace, list) or any(not isinstance(r, dict) for r in trace):
            raise ValueError("trace requires object rows")
        if len(trace) < 2:
            return "INCOMPLETE", ["INCOMPLETE: begin/rows/final recording unavailable"]
        contract, final = config["contract"], trace[-1]
        if final.get("kind") != "final":
            if any(r.get("kind") in ("driver_error", "overflow", "guest_write", "cpu_change") for r in trace):
                return "FAIL", ["FAIL: recorder failed before final envelope"]
            return "INCOMPLETE", ["INCOMPLETE: final recording unavailable"]
        why = []
        begin = trace[0]
        if begin.get("kind") != "begin" or begin.get("cold_boot") is not True or any(begin.get(k) != config[k] for k in ("run_id", "provenance")):
            why.append("FAIL: cold-boot provenance envelope differs")
        if final.get("kind") != "final" or final.get("completed") is not True:
            why.append("INCOMPLETE: unique completed final event required")
        why.extend("FAIL: " + s for s in validate_hook_counts(trace, final, contract["sites"]))
        for field in ("guest_writes", "cpu_changes", "overflows", "driver_errors"):
            if not integer(final.get(field), 0, 2**53 - 1) or final[field]:
                why.append(f"FAIL: final {field} nonzero/malformed")
        if not integer(final.get("elapsed"), 0, config["frames"]):
            why.append("INCOMPLETE: elapsed frame budget malformed")
        state = {"load_started": False, "primary": "unobserved", "backup": "unobserved", "selected": "unobserved", "corrupt": False}
        backup_begin, streak, last_stable, snapshots, protected, last_frame = False, 0, None, [], 0, -1
        ready = None
        for ordinal, row in enumerate(trace, 1):
            if type(row.get("ord")) is not int or row["ord"] != ordinal or not integer(row.get("frame"), 0, 2**53 - 1) or row["frame"] < last_frame:
                why.append("FAIL: trace ordinal/frame missing, backwards or reordered")
            if integer(row.get("frame"), 0, 2**53 - 1):
                last_frame = row["frame"]
            kind = row.get("kind")
            if kind in ("begin", "final"):
                if (kind == "begin" and ordinal != 1) or (kind == "final" and ordinal != len(trace)):
                    why.append("INCOMPLETE: duplicated/misplaced envelope")
                continue
            if kind == "snapshot":
                snapshots.append(row)
                if ready is None or ready["frame"] != row["frame"] or streak != contract["stable_frames"]:
                    why.append("INCOMPLETE: snapshot lacks first three consecutive stable native frames")
                elif row.get("checkpoint") != {k: ready[k] for k in ("guards", "svbk", "stack_hex")}:
                    why.append("FAIL: snapshot checkpoint differs from actual dispatch")
                if row.get("engine_integrity") != state or state["selected"] == "unobserved" or state["corrupt"]:
                    why.append("INCOMPLETE: snapshot lacks accepted native cold load")
                if type(row.get("stable_frame_count")) is not int or row["stable_frame_count"] != contract["stable_frames"]:
                    why.append("INCOMPLETE: stable frame count differs")
                if row.get("storage", {}).get("used_flags") is None:
                    why.append("INCOMPLETE: live allocation flags missing")
                if row.get("overlay_version_hex") != contract["version"]["hex"]:
                    why.append("FAIL: live ROM overlay version differs from staged artifact")
                continue
            if kind not in contract["sites"]:
                why.append(f"FAIL: recorder event {kind!r}")
                continue
            site = contract["sites"][kind]
            if row.get("hook_bytes") != site["bytes"] or not integer(row.get("sp"), 0, 65535):
                why.append(f"FAIL: {kind} instruction bytes/SP differ")
            if row.get("matched") is True:
                protected += 1
                if row.get("qualified") is not True:
                    why.append(f"FAIL: {kind} bank-matched wrong PC")
            if row.get("qualified") is not True:
                continue
            if snapshots:
                why.append("FAIL: qualified hooks after first stable snapshot")
            if kind == "load_begin":
                if state["load_started"]:
                    why.append("FAIL: multiple cold loads in one recording")
                state["load_started"] = True
            elif kind in ("primary_fail", "backup_begin", "backup_fail", "primary_loaded", "backup_loaded", "corrupt"):
                if not state["load_started"]:
                    why.append("FAIL: checksum/copy witness precedes TryLoadSaveFile")
                if kind in ("primary_fail", "backup_fail"):
                    flags = row.get("checksum_flags")
                    if not integer(flags, 0, 255):
                        raise ValueError("native checksum epilogue lacks CPU flags")
                    valid = bool(flags & 0x80)
                    if row.get("checksum_zero") is not valid:
                        why.append("FAIL: native checksum Z claim differs from CPU flags")
                    copy = "primary" if kind == "primary_fail" else "backup"
                    if state["selected"] != "unobserved" or (copy == "backup") != backup_begin or state[copy] != "unobserved":
                        why.append("FAIL: duplicate checksum epilogue/outside native load branch")
                    state[copy] = "valid" if valid else "invalid"
                elif kind == "backup_begin":
                    if state["primary"] != "invalid" or state["selected"] != "unobserved" or backup_begin:
                        why.append("FAIL: contradictory backup branch")
                    backup_begin = True
                elif kind == "primary_loaded":
                    if state["primary"] != "valid" or backup_begin or state["selected"] != "unobserved":
                        why.append("FAIL: contradictory/unverified primary accepted-copy path")
                    state["selected"] = "main"
                elif kind == "backup_loaded":
                    if not backup_begin or state["primary"] != "invalid" or state["backup"] != "valid" or state["selected"] != "unobserved":
                        why.append("FAIL: contradictory/unverified backup accepted-copy path")
                    state["selected"] = "backup"
                else:
                    state["corrupt"] = True
                    why.append("FAIL: native engine reports corrupted/unloadable save")
            else:
                clean, matches = checkpoint_ok(row, contract)
                if row.get("engine_clean") is not clean or row.get("stack_match") is not matches:
                    why.append("FAIL: dispatch booleans disagree with raw gate")
                eligible = clean and matches and state["load_started"] and state["selected"] != "unobserved" and not state["corrupt"]
                if eligible:
                    if row["frame"] != last_stable:
                        streak = streak + 1 if last_stable is not None and row["frame"] == last_stable + 1 else 1
                        last_stable = row["frame"]
                    if streak >= contract["stable_frames"]:
                        if ready is None:
                            ready = row
                        elif row["frame"] != ready["frame"]:
                            why.append("INCOMPLETE: recorder skipped first stable frame")
                else:
                    streak, last_stable = 0, None
        if protected > config["trace_cap"]:
            why.append("FAIL: protected hook capacity exceeded")
        if type(final.get("snapshot_count")) is not int or final["snapshot_count"] != len(snapshots) or len(snapshots) > 1:
            why.append("FAIL: snapshot count/enumeration differs")
        elapsed = final.get("elapsed")
        if integer(elapsed, 0, config["frames"]) and final.get("frame") != begin.get("frame", 0) + elapsed:
            why.append("INCOMPLETE: final frame/elapsed differ")
        if not snapshots:
            why.append("NO_HIT: no qualified cold-load stable snapshot")
            if elapsed != config["frames"]:
                why.append("INCOMPLETE: no-hit run did not complete its frame budget")
        else:
            _, reasons = compare_manifest(snapshots[0], expected)
            why.extend(reasons)
        return finding(why), why
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        return "FAIL", [f"FAIL: malformed reload evidence: {exc}"]


def flat_sram(symbol):
    bank, addr = symbol
    if not 0 <= bank <= 3 or not 0xA000 <= addr <= 0xC000:
        raise ValueError("unsupported CartRAM coordinate")
    return bank * 0x2000 + addr - 0xA000


def derive_contract(symbols, rom, provenance):
    gate = dispatch.derive_contract(symbols)
    sites = {}
    for kind, label in SITES.items():
        bank, addr = symbols[label]
        if not 1 <= bank <= 127 or not 0x4000 <= addr <= 0x7FFC or (kind != "dispatch" and bank != 5):
            raise ValueError(f"{label}: unexpected native ROM mapping")
        offset = bank * 0x4000 + addr - 0x4000
        raw = rom[offset:offset + 4]
        if len(raw) != 4:
            raise ValueError("ROM anchor outside artifact")
        sites[kind] = {"bank": bank, "addr": addr, "rom_offset": offset, "bytes": raw.hex()}
    def ram_coord(label):
        bank, addr = symbols[label]
        if bank == 0 and 0xC000 <= addr < 0xD000:
            return {"domain": "WRAM", "offset": addr - 0xC000}
        if bank in (1, 2) and 0xD000 <= addr < 0xE000:
            return {"domain": "WRAM", "offset": bank * 0x1000 + addr - 0xD000}
        raise ValueError(f"{label}: unsupported RAM mapping")
    ram = {name: ram_coord(label) for name, label in RAM.items()}
    guards = {}
    for field, label in dispatch.FIELDS.items():
        bank, addr = symbols[label]
        if not (bank == 0 and (0xC000 <= addr < 0xD000 or 0xFF80 <= addr < 0xFFFF) or bank == 1 and 0xD000 <= addr < 0xE000):
            raise ValueError(f"{label}: dispatcher bus guard mapping")
        guards[field] = {"domain": "System Bus", "offset": addr}
    for a, b, size in (("wPartyMon1", "wPartyMon2", 48), ("wPartyMon1OT", "wPartyMon2OT", 11),
                       ("wPartyMon1Nickname", "wPartyMon2Nickname", 11)):
        if symbols[b][0] != symbols[a][0] or symbols[b][1] - symbols[a][1] != size:
            raise ValueError("native party/name stride differs")
    base_bank, base_addr = symbols["wPlayerID"]
    for label, delta in (("wPlayerName", ds.NAME_OFF), ("wPartyCount", ds.COUNT_OFF), ("wPartyMon1", ds.MON_OFF),
                         ("wPartyMonOTs", ds.OT_OFF), ("wPartyMonNicknames", ds.NICK_OFF), ("wSavedAtLeastOnce", 0x2F)):
        if symbols[label] != (base_bank, base_addr + delta):
            raise ValueError(f"{label}: saved/live layout differs")
    for label, value in (("sGameData", ds.MAIN.start), ("sGameDataEnd", ds.MAIN.end),
                         ("sBackupGameData", ds.BACKUP.start), ("sBackupGameDataEnd", ds.BACKUP.end),
                         ("sChecksum", ds.MAIN.checksum_at), ("sBackupChecksum", ds.BACKUP.checksum_at),
                         ("sNewBox1", ds.MAIN.newbox_at), ("sBackupNewBox1", ds.BACKUP.newbox_at),
                         ("sSaveVersion", ds.VERSION_AT), ("sWritingBackup", ds.PHASE_AT)):
        if flat_sram(symbols[label]) != value:
            raise ValueError(f"{label}: derive_save geometry drift")
    for label, section in zip(("sBoxMons1A", "sBoxMons1B", "sBoxMons1C", "sBoxMons2A", "sBoxMons2B", "sBoxMons2C"), SECTIONS, strict=True):
        if flat_sram(symbols[label]) != section["offset"]:
            raise ValueError(f"{label}: PokeDB geometry drift")
    slot = provenance["output"]["version_slot"]
    bank, addr = symbols["SlinkVersionText"]
    version_at = bank * 0x4000 + addr - 0x4000
    if slot != {"offset": version_at, "length": 20} or len(rom[version_at:version_at + 20]) != 20:
        raise ValueError("ROM overlay version field/symbols differ")
    return {"schema": SCHEMA, "sites": sites, "rombank_addr": gate["rombank_addr"], "svbk_addr": gate["svbk_addr"],
            "pins": gate["pins"], "guards": guards, "ram": ram,
            "storage": {"main": ds.MAIN.newbox_at, "backup": ds.BACKUP.newbox_at, "boxes": 20, "slots": 20, "metadata_size": 33, "sections": SECTIONS},
            "save": {"phase_offset": ds.PHASE_AT, "version_offset": ds.VERSION_AT},
            "version": {"offset": version_at, "length": 20, "hex": rom[version_at:version_at + 20].hex()}, "stable_frames": 3}


def prepare():
    """Pinned symbols/base/UPS/output validated BEFORE directory, environment or harness changes."""
    prov_path, sym_path = REPO / "data/polished/overlay_provenance.json", REPO / "data/polished/polished_slink.sym"
    prov_raw, sym_raw = prov_path.read_bytes(), sym_path.read_bytes()
    provenance = json.loads(prov_raw)
    if digest(sym_raw) != provenance["symbols"][sym_path.name]:
        raise ValueError("symbol SHA256 differs from overlay provenance")
    clean = RELEASE.read_bytes()
    ups = (REPO / "patch/dist/SLink-Polished.ups").read_bytes()
    patch = provenance["output"]["ups"]
    if digest(ups) != patch["sha256"] or len(ups) != patch["size"]:
        raise ValueError("UPS SHA256/size differs from overlay provenance")
    from tools.polished_live.runtime import pinned_rom_bytes
    rom = pinned_rom_bytes(RELEASE, provenance["output"]["sha1"],
                           patch=REPO / "patch/dist/SLink-Polished.ups", base_sha1=provenance["base_sha1"])
    if len(rom) != provenance["output"]["size"]:
        raise ValueError("overlay ROM SHA1/size differs from provenance")
    contract = derive_contract(read_symbols(sym_path), rom, provenance)
    proof = {"base_sha1": digest(clean, "sha1"), "rom_sha1": digest(rom, "sha1"), "ups_sha256": digest(ups),
             "symbols_sha256": digest(sym_raw), "provenance_sha256": digest(prov_raw),
             "contract_sha256": digest(canonical(contract)), "driver_sha256": digest((REPO / "tools/polished_live/reload_probe.lua").read_bytes())}
    return rom, contract, proof


def parse_route(path, frame_cap):
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or set(value) != {"steps"} or not isinstance(value["steps"], list) or not value["steps"]:
        raise ValueError("route must contain only a nonempty steps list")
    steps, total, idle = [], 0, False
    for row in value["steps"]:
        if not isinstance(row, dict) or set(row) != {"phase", "frames", "buttons"} or row["phase"] not in ("setup", "idle") or not integer(row["frames"], 1, frame_cap):
            raise ValueError("route requires native setup/idle phase and positive bounded frames")
        buttons = row["buttons"]
        if not isinstance(buttons, list) or any(not isinstance(b, str) or b not in dispatch.BUTTONS for b in buttons) or len(buttons) != len(set(buttons)):
            raise ValueError("route buttons must be unique native names")
        if row["phase"] == "idle":
            if buttons:
                raise ValueError("idle requires released native buttons")
            idle = True
        total += row["frames"]
        steps.append({"phase": row["phase"], "frames": row["frames"], "buttons": buttons.copy()})
    if total > frame_cap or not idle:
        raise ValueError("route exceeds frame budget or lacks released idle")
    return steps


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--fixture", required=True, type=Path)
    parser.add_argument("--expected", required=True, type=Path)
    parser.add_argument("--route", type=Path)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--frames", type=int, default=12000)
    parser.add_argument("--trace-cap", type=int, default=2000)
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args(argv)
    if not 1 <= args.frames <= 10_000_000 or not 1 <= args.trace_cap <= 2000 or not 1 <= args.timeout <= 3600:
        parser.error("frames/trace-cap/timeout outside bounded limits")
    if args.offline and args.route or not args.offline and args.route is None:
        parser.error("live needs --route; offline does not accept --route")
    try:
        args.fixture_bytes = args.fixture.read_bytes()
        if len(args.fixture_bytes) not in (ds.CART_SIZE, ds.SAVE_SIZE):
            raise ValueError("SaveRAM must be exactly 32768 or 32790 bytes")
        args.expected_bytes = args.expected.read_bytes()
        args.manifest = parse_expected(json.loads(args.expected_bytes))
        args.steps = [] if args.offline else parse_route(args.route, args.frames)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.error(str(exc))
    return args


def main(argv=None):
    args = parse_args(argv)
    if args.offline:
        # Offline intentionally has no ROM dependency or harness import. Battery is read, never repaired.
        if args.dry_run:
            print(json.dumps({"dry_run": True, "mode": "offline", "measurement_verdict": None,
                              "fixture_size": len(args.fixture_bytes), "fixture_sha256": digest(args.fixture_bytes),
                              "expected_sha256": digest(args.expected_bytes)}, sort_keys=True))
            return 0
        verdict, reasons = evaluate_offline(args.fixture_bytes, args.manifest)
        try:
            evidence = extract_save(args.fixture_bytes)
            for snapshot in evidence["copies"].values():
                snapshot["decoded"] = decode_snapshot(snapshot)
        except (ValueError, KeyError, TypeError) as exc:
            evidence = {"extraction_error": str(exc)}
        print(json.dumps({"mode": "offline", "verdict": verdict, "reasons": reasons, "evidence": evidence}, sort_keys=True))
        return 0 if verdict == "PASS" else 1
    try:
        rom, contract, proof = prepare()
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"RESULT: FAIL reload-probe preparation: {exc}")
        return 1
    proof.update(fixture_sha256=digest(args.fixture_bytes), expected_sha256=digest(args.expected_bytes),
                 route_sha256=digest(canonical(args.steps)))
    if args.dry_run:
        print(json.dumps({"dry_run": True, "mode": "live", "measurement_verdict": None, "contract": contract, "provenance": proof}, sort_keys=True))
        return 0
    WORK.mkdir(parents=True, exist_ok=True)
    lane = Path(tempfile.mkdtemp(prefix="cold-", dir=WORK))
    rom_path = lane / "rom/pol overlay.gbc"
    rom_path.parent.mkdir()
    rom_path.write_bytes(rom)
    run = lane / "probe"
    run.mkdir()
    config = {"run_id": uuid.uuid4().hex, "contract": contract, "provenance": proof, "frames": args.frames,
              "trace_cap": args.trace_cap, "steps": args.steps}
    config_path = run / "input.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    pid, errors = None, []
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from dataclasses import replace

        from tools.polished_live import harness
        launch_config = replace(harness.launch_config(dict(os.environ, POL_LANE=str(lane), POL_KIND="overlay")),
                                rom_src=rom_path, rom=rom_path, sram=lane / "sram",
                                save_name=rom_path.stem + ".SaveRAM")
        launch_config.sram.mkdir()
        (launch_config.sram / launch_config.save_name).write_bytes(args.fixture_bytes)
        started = time.monotonic()
        # Existing harness owns exactly the process it launches, including exception/timeout cleanup.
        text, pid = harness.launch(launch_config, "tools/polished_live/reload_probe.lua", run,
                                   {"POL_PROBE_CONFIG": config_path.as_posix()}, args.timeout)
        results = [row for row in text.splitlines() if row.startswith("RESULT:")]
        if len(results) != 1 or not results[0].startswith("RESULT: PASS reload-probe recording ") or time.monotonic() - started >= args.timeout:
            errors.append("FAIL: missing successful recording RESULT or deadline reached")
    except Exception as exc:
        errors.append(f"FAIL: launch/recording {type(exc).__name__}: {exc}")
    try:
        trace = json.loads((run / "trace.json").read_text(encoding="utf-8"))
        verdict, reasons = evaluate(trace, args.manifest, config)
        snapshots = [row for row in trace if isinstance(row, dict) and row.get("kind") == "snapshot"]
        decoded = decode_snapshot(snapshots[0]) if len(snapshots) == 1 else None
    except (OSError, ValueError, KeyError, TypeError) as exc:
        verdict, reasons, decoded = "FAIL", [f"FAIL: trace {exc}"], None
    if errors:
        verdict, reasons = "FAIL", errors + reasons
    report = {"verdict": verdict, "reasons": reasons, "owned_pid": pid, "config": config, "decoded": decoded}
    (run / "verdict.json").write_text(json.dumps(report, sort_keys=True), encoding="utf-8")
    print(f"RESULT: {verdict} reload-probe (cold; owned PID {pid})\n" + "\n".join(reasons))
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
