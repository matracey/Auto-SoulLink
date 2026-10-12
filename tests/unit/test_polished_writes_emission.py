"""Consumer-visible RAW receipts from the actual Polished live-write driver.

The engine seam is deliberately simulated; writes.lua, its recording guard, the JSON
codec and polished_boxes' public encoding/checksum primitives are the real modules.
No Lua source is extracted, transformed or inspected by these tests.
"""
import json
from pathlib import Path

import pytest
from lupa.lua55 import LuaRuntime

ROOT = Path(__file__).resolve().parents[2]
SB, COUNT, OTS, NICKS = 0xDCD6, 0xDCCE, 0xDDF6, 0xDE38
BOX, MEMORIAL, ENTRY, FLAG = 0x30E4, 0x3357, 0x4000, 0x28B7

# Independently worked NEWBOX examples: the first 22 bytes are the species id,
# PPUps is zero, level is 50, OT Extra contains three raw 0x80+id bytes.
# Name checksum weights 34..43 sum to 385; OT weights 44..50 sum to 329.
# id=3: 127+3*253+3*130+50*29+131*93+19*385+3*329 = 23211 (0x5AAB).
# id=4: 127+4*253+4*130+50*29+132*93+20*385+4*329 = 24401 (0x5F51).
THREE_ENTRY = bytes(
    [3] * 22 + [0] + [3] * 5 + [50] + [0x83] * 3
    + [0x13, 0x93, 0x13, 0x93, 0x93, 0x13, 0x93, 0x13, 0x93, 0x13,
       0x83, 0x03, 0x83, 0x03, 0x83, 0x83, 0x03]
)
FOUR_ENTRY = bytes(
    [4] * 22 + [0] + [4] * 5 + [50] + [0x84] * 3
    + [0x14, 0x94, 0x14, 0x94, 0x94, 0x94, 0x94, 0x94, 0x14, 0x94,
       0x04, 0x84, 0x04, 0x04, 0x04, 0x84, 0x04]
)
REFUSAL = 'box census incomplete: native "SRAM" read unavailable\nretry after save'


ENGINE = r'''
    local real_dofile = dofile
    local J = real_dofile(driver_root .. "/lua/json_codec.lua")
    local Boxes = real_dofile(driver_root .. "/lua/gen2/polished_boxes.lua")
    os.getenv = function(name)
        if name == "SLINK_ROOT" then return driver_root end
        if name == "POL_RUN" then return run_dir end
        if name == "POL_MEMORIAL" then return "1" end
        return nil
    end
    local frame, phase, seq = 0, "before", 0
    local mem = {["System Bus"] = {}, CartRAM = {}, WRAM = {}}
    local sb, count_at, ots, nicks = 0xdcd6, 0xdcce, 0xddf6, 0xde38
    local names = {"one", "two", "three", "four", "five"}
    local keys = {"one", "two", "three", "four", "five"}
    local function read(a, domain)
        if domain == "WRAM" and a >= 0x1000 and a < 0x2000 then
            return mem["System Bus"][a + 0xc000] or 0
        end
        return mem[domain][a] or 0
    end
    memory = {read_u8 = read, write_u8 = function(a, v, d) mem[d][a] = v end}
    local function put(a, v, d) memory.write_u8(a, v, d or "System Bus") end
    local function hex(bytes)
        local out = {}
        for _, b in ipairs(bytes) do out[#out + 1] = string.format("%02X", b) end
        return table.concat(out)
    end
    local function bytes_at(at, size, domain)
        local out = {}
        for i = 0, size - 1 do out[#out + 1] = read(at + i, domain) end
        return out
    end
    local function blob_at(slot)
        local out = bytes_at(sb + slot * 48, 48, "System Bus")
        for _, at in ipairs({ots + slot * 11, nicks + slot * 11}) do
            for _, b in ipairs(bytes_at(at, 11, "System Bus")) do out[#out + 1] = b end
        end
        return out
    end
    -- Setup is outside the driver's tap. All subsequent transitions use memory.write_u8.
    mem["System Bus"][count_at] = 5
    for slot = 0, 4 do
        local id = slot + 1
        for off = 0, 47 do mem["System Bus"][sb + slot * 48 + off] = id end
        for off, value in pairs({[31]=50, [32]=7, [34]=1, [35]=44, [36]=1, [37]=144}) do
            mem["System Bus"][sb + slot * 48 + off] = value
        end
        for off = 0, 10 do
            mem["System Bus"][ots + slot * 11 + off] = 0x80 + id
            mem["System Bus"][nicks + slot * 11 + off] = 0x90 + id
        end
    end
    local coords = {sBoxMons1A={2, 0xa000}, sBoxMons1B={0, 0xa000}, sBoxMons1C={1, 0xb60c},
                    sBoxMons2A={3, 0xa000}, sBoxMons2B={0, 0xabf1}, sBoxMons2C={1, 0xb858},
                    wPokeDB1UsedEntries={2, 0xd8b7}, wPokeDB2UsedEntries={2, 0xd8d1}}
    for i = 1, 20 do coords["sNewBox" .. i] = {1, 0xb0e4 + (i - 1) * 33} end
    -- Native census return objects are reused on purpose. Driver receipts must own
    -- a deep copy, including each mon's nested moves array, before the next read.
    local native, native_mons, native_mon, native_moves = {}, {}, {}, {}
    native.mons = native_mons
    local census = {}
    function census.read_storage_box(index)
        if fault == "census_refusal" and phase == "withdrawn" and index == 7 then
            return nil, refusal_reason
        end
        for k in pairs(native_mons) do native_mons[k] = nil end
        for slot = 0, 19 do
            local rec = 0x30e4 + index * 33
            local entry = read(rec + slot, "CartRAM")
            if entry ~= 0 then
                local id = entry == 2 and 3 or 4
                local bank = 1 + ((read(rec + 20 + (slot >> 3), "CartRAM") >> (slot & 7)) & 1)
                local raw = bytes_at(0x4000 + (entry - 1) * 49, 49, "CartRAM")
                local mon, moves = {}, {}
                for move = 1, 4 do moves[move] = raw[move + 2] end
                mon.key, mon.box, mon.slot, mon.bank, mon.entry = names[id], index + 1, slot, bank, entry
                mon.species_id, mon.form, mon.gender, mon.is_egg = id, id, "male", false
                mon.shiny, mon.held_item, mon.ot_id, mon.level = false, id, id * 257, 50
                mon.dv_bytes = id * 65793
                mon.raw_hex, mon.moves = hex(raw), moves
                mon.nickname_raw_hex = string.rep(string.format("%02X", 0x90 + id), 10) .. "53"
                mon.ot_raw_hex = string.rep(string.format("%02X", 0x80 + id), 7) .. "53"
                -- The first record and its nested list are mutated between calls.
                if #native_mons == 0 then
                    for k in pairs(native_mon) do native_mon[k] = nil end
                    for k, v in pairs(mon) do native_mon[k] = v end
                    for i = 1, 4 do native_moves[i] = moves[i] end
                    native_mon.moves = native_moves
                    native_mons[1] = native_mon
                else
                    native_mons[#native_mons + 1] = mon
                end
            end
        end
        return native
    end
    local party_return, party_mons = {}, {}
    party_return.mons = party_mons
    local reads = {}
    function reads:read_party()
        for k in pairs(party_mons) do party_mons[k] = nil end
        for i, key in ipairs(keys) do
            local id
            for n, name in ipairs(names) do if name == key then id = n end end
            party_mons[i] = {key=key, species=id, level=50, slot=i-1,
                             blob_hex=hex(blob_at(i-1)), moves={id, id, id, id}}
        end
        return party_return
    end
    local C = {send = function() return true end}
    package.loaded.connector = C
    local function send(event, key)
        seq = seq + 1
        C.send(J.encode({event=event, player="a", seq=seq, frame=frame,
                         key=key or J.null, reason=J.null, session_nonce="fixture-session",
                         native_ack={opcode=event == "memorialize_done" and 6 or 0,
                                     status=1, readback={key=key or J.null, accepted=true}}}))
    end
    local function remove(slot)
        local size = #keys
        for _, array in ipairs({{sb,48}, {ots,11}, {nicks,11}}) do
            for s = slot, size - 2 do
                for off = 0, array[2] - 1 do
                    put(array[1] + s * array[2] + off,
                        read(array[1] + (s+1) * array[2] + off, "System Bus"))
                end
            end
            for off = 0, array[2] - 1 do put(array[1] + (size-1) * array[2] + off, 0) end
        end
        table.remove(keys, slot + 1)
        put(count_at, #keys)
    end
    local function slot_for(key)
        for i, k in ipairs(keys) do if k == key then return i - 1 end end
    end
    local saved_four
    SLINK_GEN2_CLIENT = {}
    function SLINK_GEN2_CLIENT:handle_command(cmd)
        if cmd.cmd == "force_faint" then
            local slot = assert(slot_for(cmd.key))
            for _, off in ipairs({32,34,35}) do put(sb + slot * 48 + off, 0) end
            send("faint", cmd.key)
            phase = "fainted"
        elseif cmd.cmd == "box_mon" then
            local slot = assert(slot_for(cmd.key))
            local blob = blob_at(slot)
            if not saved_four then saved_four = blob end
            local entry = Boxes.entry_from_party(blob)
            if fault == "checksum_corruption" and phase == "fainted" then
                entry[33] = entry[33] ~ 0x80
            end
            local num = phase == "fainted" and 1 or 3
            for i, v in ipairs(entry) do put(0x4000 + (num-1) * 49 + i - 1, v, "CartRAM") end
            put(0x28b7, read(0x28b7, "WRAM") | (1 << (num-1)), "WRAM")
            put(0x30f8, 0, "CartRAM")
            put(0x30e4, num, "CartRAM")
            remove(slot)
            send("party_to_box", cmd.key)
            phase = num == 1 and "deposited" or "source"
        elseif cmd.cmd == "party_mon" then
            local slot = #keys
            for i = 1, 48 do put(sb + slot * 48 + i - 1, saved_four[i]) end
            for i = 1, 11 do
                put(ots + slot * 11 + i - 1, saved_four[48+i])
                put(nicks + slot * 11 + i - 1, saved_four[59+i])
            end
            for _, row in ipairs({{32,0}, {34,1}, {35,144}}) do put(sb + slot * 48 + row[1], row[2]) end
            keys[#keys + 1] = cmd.key
            put(count_at, #keys)
            put(0x30e4, 0, "CartRAM")
            put(0x30f8, 0, "CartRAM")
            send("box_to_party", cmd.key)
            phase = "withdrawn"
        elseif cmd.cmd == "memorialize" and cmd.key == "three" then
            local slot = assert(slot_for(cmd.key))
            local entry = Boxes.entry_from_party(blob_at(slot))
            for i, v in ipairs(entry) do put(0x4031 + i - 1, v, "CartRAM") end
            put(0x28b7, 3, "WRAM")
            put(0x336b, 0, "CartRAM")
            put(0x3357, 2, "CartRAM")
            remove(slot)
            send("memorialize_done", cmd.key)
            phase = "party_buried"
        elseif cmd.cmd == "memorialize" and cmd.key == "four" then
            put(0x336b, 0, "CartRAM")
            put(0x3358, 3, "CartRAM")
            put(0x30e4, 0, "CartRAM")
            send("memorialize_done", cmd.key)
            phase = "box_buried"
        else error("unexpected simulated command " .. tostring(cmd.cmd), 0) end
    end
    SLINK_GEN2_PARTS = {pack="polished_crystal", artifact_kind="overlay", runtime_rom_sha1="synthetic",
                       reads=reads, overworld={writes={}, boxes={}, coords=coords, census=census}}
    L = {ROOT=driver_root, RUN=run_dir, json=J, failures=0, checks={},
         SYM={wPartyMons={1,sb}, wPartyCount={1,count_at}, wPartyMonOTs={1,ots},
              wPartyMonNicknames={1,nicks}, wPartyMonNicknamesEnd={1,0xde7a}}}
    function L.check(what, ok, detail)
        L.checks[#L.checks + 1] = {what=what, ok=not not ok, detail=detail}
        if not ok then L.failures = L.failures + 1 end
        return ok
    end
    function L.finish(tag) L.finished = tag; error("oracle-finished", 0) end
    function L.die(why) error("unexpected driver abort: " .. tostring(why), 0) end
    function L.log() end
    function L.hook() end
    function L.to_overworld() return true end
    function L.woff(name) return L.SYM[name][2] - 0xc000 end
    function L.bus(a) return read(a, "System Bus") end
    function L.rw(name)
        if name == "wPartyCount" then return read(count_at, "System Bus") end
        if name == "wMapStatus" then return 2 end
        return 0
    end
    function L.unhex(text)
        local out = {}
        for byte in text:gmatch("%x%x") do out[#out + 1] = tonumber(byte, 16) end
        return out
    end
    function L.idle(n) frame = frame + n; send("tick") end
    emu = {framecount=function() return frame end}
    event = {onframeend=function() end}
    dofile = function(path)
        if path:match("pol_lib.lua$") then return L end
        if path:match("/lua/slink.lua$") then send("hello"); return end
        return real_dofile(path)
    end
    local ok, why = pcall(real_dofile, driver_root .. "/tools/polished_live/writes.lua")
    assert(not ok and why == "oracle-finished", tostring(why))
'''


def run_emission(run_dir, fault="none"):
    lua = LuaRuntime(unpack_returned_tuples=True)
    lua.globals().driver_root = ROOT.as_posix()
    lua.globals().run_dir = run_dir.as_posix()
    lua.globals().fault = fault
    lua.globals().refusal_reason = REFUSAL
    lua.execute(ENGINE)
    # Parse the actual persisted consumer artifact, not L.check or an internal table.
    return json.loads((run_dir / "trace.json").read_text(encoding="utf-8"))


@pytest.fixture
def emission(tmp_path):
    return run_emission(tmp_path)


def receipt(addr, value, frame, domain="System Bus"):
    return {"op": "write_u8", "addr": addr, "v": value, "domain": domain, "frame": frame}


def party_record(identifier, *, fainted=False, healed=False):
    data = [identifier] * 48
    data[31], data[32] = 50, 0 if fainted or healed else 7
    data[34:38] = [0, 0, 1, 144] if fainted else [1, 144 if healed else 44, 1, 144]
    return data


def removal_receipts(frame, identifiers, slot):
    """Literal simulated engine order: compact each parallel array, zero tail, count last."""
    out = []
    for base, size in ((SB, 48), (OTS, 11), (NICKS, 11)):
        for target in range(slot, len(identifiers) - 1):
            identifier = identifiers[target + 1]
            values = (party_record(identifier, healed=identifier == 4)
                      if size == 48 else [0x80 + identifier if base == OTS else 0x90 + identifier] * 11)
            out.extend(receipt(base + target * size + off, value, frame)
                       for off, value in enumerate(values))
        out.extend(receipt(base + (len(identifiers) - 1) * size + off, 0, frame)
                   for off in range(size))
    out.append(receipt(COUNT, len(identifiers) - 1, frame))
    return out


def expected_leg_writes():
    a = [receipt(SB + 2 * 48 + off, 0, 120) for off in (32, 34, 35)]
    b = [receipt(ENTRY + i, value, 240, "CartRAM") for i, value in enumerate(FOUR_ENTRY)]
    b += [receipt(FLAG, 1, 240, "WRAM"), receipt(BOX + 20, 0, 240, "CartRAM"),
          receipt(BOX, 1, 240, "CartRAM")]
    # The untouched fifth mon, not the withdrawn fourth mon, shifts into slot 3.
    b += removal_receipts(240, [1, 2, 3, 4, 5], 3)
    d = [receipt(SB + 4 * 48 + i, value, 390) for i, value in enumerate(party_record(4))]
    for i in range(11):
        d += [receipt(OTS + 4 * 11 + i, 0x84, 390), receipt(NICKS + 4 * 11 + i, 0x94, 390)]
    d += [receipt(SB + 4 * 48 + off, value, 390) for off, value in ((32, 0), (34, 1), (35, 144))]
    d += [receipt(COUNT, 5, 390), receipt(BOX, 0, 390, "CartRAM"), receipt(BOX + 20, 0, 390, "CartRAM")]
    e = [receipt(ENTRY + 49 + i, value, 540, "CartRAM") for i, value in enumerate(THREE_ENTRY)]
    e += [receipt(FLAG, 3, 540, "WRAM"), receipt(MEMORIAL + 20, 0, 540, "CartRAM"),
          receipt(MEMORIAL, 2, 540, "CartRAM")]
    e += removal_receipts(540, [1, 2, 3, 5, 4], 2)
    f = [receipt(MEMORIAL + 20, 0, 840, "CartRAM"), receipt(MEMORIAL + 1, 3, 840, "CartRAM"),
         receipt(BOX, 0, 840, "CartRAM")]
    return {"a": a, "b": b, "d": d, "e": e, "f": f}


def as_list(value):
    # Existing codec intentionally encodes an untyped empty Lua table as {}.
    assert isinstance(value, list) or value == {}
    return [] if value == {} else value


def test_all_legs_persist_complete_ordered_write_receipts_without_preparation_contamination(emission):
    assert emission["tag"] == "pol-live-writes"
    assert emission["failures"] == 0
    assert set(emission["operations"]) == {"a", "b", "d", "e", "f"}
    for leg, writes in expected_leg_writes().items():
        assert emission["operations"][leg]["writes"] == writes, leg
    # f's 690-frame preparation deposit is intentionally outside its 840-frame tap.
    assert {row["frame"] for row in emission["operations"]["f"]["writes"]} == {840}


@pytest.mark.parametrize("leg,end", [("a", 240), ("b", 390), ("d", 540), ("e", 690), ("f", 990)])
def test_diff_controls_persist_wrong_allowances_rejection_and_untouched_full_images(emission, leg, end):
    op = emission["operations"][leg]
    control = op["control"]
    wrong = {
        "a": sorted(f"party@{SB + 3 * 48 + off}" for off in (32, 34, 35)),
        "b": [f"party@{COUNT}"],
        "d": sorted(f"party@{SB + 5 * 48 + off}" for off in range(48)),
        "e": [f"party@{COUNT}"],
        "f": sorted([f"cart@{MEMORIAL + 1}", f"cart@{MEMORIAL + 20}"]),
    }[leg]
    assert control["kind"] == "diff_allowance"
    assert control["expected"] == wrong
    outside = sorted(f'{row["kind"]}@{row["a"]}' for row in op["changed"]
                     if f'{row["kind"]}@{row["a"]}' not in wrong)
    assert outside  # No vacuous rejected empty diff.
    assert control["unexpected"] == outside
    assert control["reason"] == "diff_outside_control_allowance"
    assert control["write_count"] == 0
    assert as_list(control["writes"]) == []
    assert control["frames"] == {"start": end, "end": end}
    assert control["before"] == control["after"] == op["after"]
    assert set(control["before"]) == {"cart_hex", "party_start", "party_hex", "allocation"}
    assert len(control["before"]["cart_hex"]) == 0x8000 * 2
    assert control["before"]["party_start"] == COUNT
    assert len(control["before"]["party_hex"]) == (0xDE7A - COUNT) * 2
    assert len(control["before"]["allocation"]) == 52
    # Independently fixed controls also make the measured address reporting concrete.
    if leg == "a":
        assert outside == sorted(f"party@{SB + 2 * 48 + off}" for off in (32, 34, 35))
    elif leg == "f":
        assert outside == [f"cart@{BOX}"]


def expected_native_mon(identifier, box, slot, entry, raw):
    return {
        "key": "three" if identifier == 3 else "four", "box": box, "slot": slot,
        "bank": 1, "entry": entry, "species_id": identifier, "form": identifier,
        "gender": "male", "is_egg": False, "shiny": False, "held_item": identifier,
        "ot_id": identifier * 257, "level": 50, "dv_bytes": identifier * 65793,
        "raw_hex": raw.hex().upper(), "moves": [identifier] * 4,
        "nickname_raw_hex": f"{0x90 + identifier:02X}" * 10 + "53",
        "ot_raw_hex": f"{0x80 + identifier:02X}" * 7 + "53",
    }


def test_census_and_party_read_receipts_retain_full_native_payloads_without_shared_aliases(emission):
    expected_phases = {"b": {"after"}, "d": {"after"}, "e": {"after"},
                       "f": {"source", "after"}}
    frames = {("b", "after"): 390, ("d", "after"): 540,
              ("e", "after"): 690, ("f", "source"): 840, ("f", "after"): 990}
    seen = {}
    for leg, phases in expected_phases.items():
        rows = as_list(emission["operations"][leg]["census"])
        assert {row["phase"] for row in rows} == phases
        for phase in phases:
            matching = [row for row in rows if row["phase"] == phase]
            assert {row["box_index"] for row in matching} == set(range(20))
            for row in matching:
                assert row["frame"] == frames[leg, phase]
                assert row["reason"] is None
                assert set(row["returned"]) == {"mons"}
                seen[leg, phase, row["box_index"]] = as_list(row["returned"]["mons"])
    b = expected_native_mon(4, 1, 0, 1, FOUR_ENTRY)
    e = expected_native_mon(3, 20, 0, 2, THREE_ENTRY)
    f_source = expected_native_mon(4, 1, 0, 3, FOUR_ENTRY)
    f_buried = expected_native_mon(4, 20, 1, 3, FOUR_ENTRY)
    assert seen["b", "after", 0] == [b]
    assert seen["d", "after", 0] == []
    assert seen["e", "after", 19] == [e]
    assert seen["f", "source", 0] == [f_source]
    assert seen["f", "source", 19] == [e]
    assert seen["f", "after", 0] == []
    assert seen["f", "after", 19] == [e, f_buried]
    party_expectations = {
        "b": {"after": ["one", "two", "three", "five"]},
        "d": {"after": ["one", "two", "three", "five", "four"]},
        "e": {"before": ["one", "two", "three", "five", "four"],
              "after": ["one", "two", "five", "four"]},
        "f": {"before": ["one", "two", "five", "four"]},
    }
    for leg, phases in party_expectations.items():
        rows = as_list(emission["operations"][leg]["party_reads"])
        for phase, keys in phases.items():
            matching = [row for row in rows if row["phase"] == phase]
            assert matching, (leg, phase)
            for row in matching:
                assert row["reason"] is None
                mons = row["returned"]["mons"]
                assert [mon["key"] for mon in mons] == keys
                assert [mon["slot"] for mon in mons] == list(range(len(keys)))
                assert all(len(mon["blob_hex"]) == 140 and len(mon["moves"]) == 4 for mon in mons)


def assert_checksum(payload, raw, computed, stored):
    assert payload["input_hex"].upper() == raw.hex().upper()
    assert payload["computed"] == computed
    assert payload["stored"] == stored
    assert payload["tail_msb"] == 0
    assert payload.get("reason") is None


def test_encoding_receipts_expose_independently_worked_party_bytes_and_stored_checksum(emission):
    assert_checksum(emission["operations"]["b"]["checksum"], FOUR_ENTRY, 24401, 24401)
    encoded = emission["operations"]["e"]["encoding"]
    blob = bytes(party_record(3, fainted=True) + [0x83] * 11 + [0x93] * 11)
    assert encoded["party_blob_hex"].upper() == blob.hex().upper()
    assert encoded["computed_entry_hex"].upper() == THREE_ENTRY.hex().upper()
    assert encoded["stored_entry_hex"].upper() == THREE_ENTRY.hex().upper()
    assert encoded["entry_at"] == ENTRY + 49
    assert_checksum(encoded["checksum"], THREE_ENTRY, 23211, 23211)


def expected_event(event, key, seq, frame):
    return {"event": event, "player": "a", "seq": seq, "frame": frame, "key": key,
            "reason": None, "session_nonce": "fixture-session",
            "native_ack": {"opcode": 6 if event == "memorialize_done" else 0, "status": 1,
                           "readback": {"key": key, "accepted": True}}}


def test_memorial_event_intervals_preserve_raw_ack_payloads_and_full_wire_sequence(emission):
    # Hello is seq=1 but predates the post-build event tap. All other frames survive.
    expected = [expected_event(event, key, seq, frame) for event, key, seq, frame in [
        ("tick", None, 2, 120), ("faint", "three", 3, 120), ("tick", None, 4, 240),
        ("party_to_box", "four", 5, 240), ("tick", None, 6, 390),
        ("box_to_party", "four", 7, 390), ("tick", None, 8, 540),
        ("memorialize_done", "three", 9, 540), ("tick", None, 10, 690),
        ("party_to_box", "four", 11, 690), ("tick", None, 12, 840),
        ("memorialize_done", "four", 13, 840), ("tick", None, 14, 990),
    ]]
    assert emission["sent_events"] == expected
    for leg, start, end in (("e", 7, 9), ("f", 11, 13)):
        op = emission["operations"][leg]
        assert op["events_start"] == start
        assert op["events_end"] == end
        assert op["events"] == expected[start:end]
        assert op["events"] == emission["sent_events"][start:end]


def test_corrupted_stored_checksum_reports_actual_numeric_mismatch_not_invented_validity(tmp_path):
    trace = run_emission(tmp_path, "checksum_corruption")
    corrupt = bytearray(FOUR_ENTRY)
    corrupt[32] ^= 0x80
    assert_checksum(trace["operations"]["b"]["checksum"], bytes(corrupt), 24401, 57169)
    checks = [row for row in trace["checks"] if row["what"].startswith("(b3)")]
    assert len(checks) == 1 and checks[0]["ok"] is False
    assert trace["operations"]["b"]["writes"][32] == receipt(ENTRY + 32, 0x94, 240, "CartRAM")
    assert_checksum(trace["operations"]["e"]["encoding"]["checksum"], THREE_ENTRY, 23211, 23211)


def test_refused_native_census_persists_json_null_and_exact_raw_reason(tmp_path):
    trace = run_emission(tmp_path, "census_refusal")
    rows = [row for row in trace["operations"]["d"]["census"] if row["box_index"] == 7]
    assert rows == [{"phase": "after", "box_index": 7, "frame": 540,
                     "returned": None, "reason": REFUSAL}]
    successful = [row for row in trace["operations"]["d"]["census"] if row["box_index"] != 7]
    assert len(successful) == 19 and all(row["reason"] is None for row in successful)
    checks = [row for row in trace["checks"] if row["what"].startswith("(d3d)")]
    assert len(checks) == 1 and checks[0]["ok"] is False
    assert trace["operations"]["d"]["writes"] == expected_leg_writes()["d"]
