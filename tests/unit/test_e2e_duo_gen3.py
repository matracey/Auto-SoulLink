"""The gen3_frlg duo row of tools/e2e_duo.py, without an emulator (card C4-6a).

Synthetic flash images are built with server/adapters/gen3_codec.py exactly the way the game lays
a save out (14 sections per slot, slot = counter % 2, sectors 28-31 outside both slots), so every
rule of check_save_witness_gen3 has a positive and a named failure, and every saved-state oracle
helper is exercised on decoded saves. The runner's per-game dispatch (ROM, battery seed, GBA
config, stub fields) is checked against a recorded Popen, and the driver files are checked for
the pret symbols, paths and helper exports they name.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def test_actual_tick_collector_keeps_latest_packet_and_count_without_verbose_history():
    """Replay retained f1da wire tick through the actual collector and ctx accessors."""
    from lupa import LuaRuntime
    source=(REPO/'lua/tests/duo/duo_gen3_main.lua').read_text()
    declaration=next(line for line in source.splitlines() if line.startswith('local seen_tx,'))
    collector=source[source.index('local C = require("connector")'):source.index('-- ── the production client:')]
    sent=source[source.index('function ctx.sent('):source.index('function ctx.received(')]
    last=source[source.index('function ctx.last_sent('):source.index('--- pred() each frame, pressing')]
    lua=LuaRuntime(unpack_returned_tuples=True)
    lua.globals().ROOT=REPO.as_posix()
    harness=lua.execute('''
        local JSON=dofile(ROOT..'/lua/json_codec.lua')
        local ctx,logs,forwarded={},{},{}
        local native_candidate=nil
        local fmt=string.format
        local function log(s) logs[#logs+1]=s end
        package.preload.connector=function() return {send=function(line)
            forwarded[#forwarded+1]=line;return 77 end} end
    '''+declaration+'\n'+collector+'\n'+sent+'\n'+last+'''
        return {send=C.send,sent=ctx.sent,last=ctx.last_sent,
                retained=function() return #tx end,logs=function() return #logs end,
                wire=function(i) return forwarded[i] end}
    ''')
    packet=dict(area_id='route_1',badges=0,ball_count=9,enemy_party=[],event='tick',
                has_pokeballs=True,in_battle=False,is_doubles=False,is_trainer_battle=False,
                loc_name='route1',party_hidden=True,player='a',seq=19,trainer_name='B')
    line=json.dumps(packet)
    assert harness.send(line)==77 and harness.wire(1)==line
    assert harness.sent('tick',None)==1
    assert harness.last('tick')['party_hidden'] is True
    for _ in range(200): harness.send(line)
    assert harness.sent('tick',None)==201
    packet['party_hidden']=False
    harness.send(json.dumps(packet))
    assert harness.last('tick')['party_hidden'] is False
    assert harness.retained()==0 and harness.logs()==0
    ordinary=json.dumps(dict(event='faint',key='K'))
    assert harness.send(ordinary)==77
    assert harness.sent('faint','K')==1 and harness.last('faint')['key']=='K'
    assert harness.retained()==1 and harness.logs()==1


@pytest.mark.parametrize("fault", ["none", "escape_error", "walk_error"])
def test_release_transit_escapes_before_complete_pc_sequence(fault):
    """Actual release scenario and shared route policy: transit must preserve the reserve."""
    from lupa import LuaRuntime

    lua = LuaRuntime(unpack_returned_tuples=True)
    lua.globals().ROOT = REPO.as_posix()
    lua.globals().FAULT = fault
    result = lua.execute(r'''
        local Routes = dofile(ROOT .. '/lua/tests/gen3_routes.lua')
        local hp, escaped, ops = 22, 0, {}
        local c = {player='a', session={signals={drain=function()
            return {{kind='pc_release', release_key='K1', release_source={where='box'}}}
        end}}}
        c.wait_go=function() return true end
        c.linked=function() return 'K1' end
        c.find=function() return {slot=1} end
        c.log=function() end; c.jlog=function() end
        c.play={fight_through=function() hp=0; return true end}
        local original=c.play.fight_through
        c.run_away=function()
            escaped=escaped+1
            return FAULT~='escape_error', 'retained flee refusal'
        end
        c.flee_incidentals=function(label,fn)
            return Routes.with_incidental_escape(c,label,fn)
        end
        c.walk_to_pc=function()
            c.play.fight_through()
            if FAULT=='walk_error' then error('retained walk failure') end
        end
        c.pc_deposit=function()
            assert(hp>0, 'retained leadKO before first deposit')
            ops[#ops+1]='deposit'; return 'K1'
        end
        c.pc_withdraw=function() ops[#ops+1]='withdraw'; return 'K1' end
        c.pc_release=function()
            ops[#ops+1]='release'; c.session.signals:drain(); return true
        end
        c.observe_boxed=function() return true end
        c.observe_returned=function() return true end
        c.wait_sent=function() return true end
        c.wait_until=function(fn) return fn() end
        c.sent=function() return 2 end
        c.G={tap=function() end}
        c.save=function() ops[#ops+1]='save'; return true end
        local scenario=dofile(ROOT .. '/lua/tests/duo/scenario_gen3_release.lua')
        local ok,pass,why=pcall(scenario,c)
        return ok,pass,tostring(why),hp,escaped,table.concat(ops,','),
               c.play.fight_through==original
    ''')
    ok, passed, why, hp, escaped, operations, restored = result
    assert ok, (passed, why)
    assert restored and hp == 22 and escaped == 1
    if fault == "none":
        assert passed is True and operations == "deposit,withdraw,deposit,release,save"
    else:
        assert passed is False and operations == "" and "release transit:" in why
sys.path.insert(0, str(REPO / "tools"))
sys.path.insert(0, str(REPO / "tests" / "unit"))

import e2e_duo as duo  # noqa: E402
import gen3_fixtures  # noqa: E402

# Reuse the RR compressed-mon builder (server/adapters/gen3_codec round-trip already proven
# there) rather than re-deriving the CompressedPokemon layout a second time.
from test_gen3_rr_save_layout import _compressed  # noqa: E402

# the scripted-play helper's own BizHawk stub, reused for the bag/dump falsifiers (C4-6h)
from test_gen3_scripted_play import _in_battle_cp, bag_stubbed  # noqa: E402,F401

from server.adapters import gen3_codec as codec  # noqa: E402
from tools import rr_companion  # noqa: E402

GEN3 = ("faint_cmd_gen3", "linked_faint_active_gen3", "boxsync_gen3", "whiteout_gen3",
        "link_gen3", "deadzone_gen3", "reconnect_gen3")
# P5 (card C5-5): RR-only, added on top of GEN3 above (which now also runs on gen3_rr).
GEN3_RR_ONLY = ("rival_swap_gen3", "clean_rr_refused_gen3")
OT_A = 0x99DE0D8A


@pytest.mark.parametrize("name,case,target,slot,attempts", [
    ("trainer_bench_gen3", "trainer_bench", "trainer", 1, 2),
])
def test_battle_window_registration(name, case, target, slot, attempts):
    row = duo.SCENARIOS[name]
    assert row["battle_window_case"] == case and row["scenario_module"] == "battle_window"
    assert row["target"] == {"a": target, "b": "town"} and row["battle_window_slot"] == slot
    assert row["no_save"] == ("b",)
    for game in ("gen3_frlg", "gen3_lgfr"):
        assert duo.scenario_applies(name, game)
        assert duo.scenario_attempt_limit(name, game) == attempts
    assert not duo.scenario_applies(name, "gen3_rr")
    assert callable(getattr(duo.DuoRun, "orchestrate_" + name))
    assert callable(getattr(duo.DuoRun, row["oracle"]))


@pytest.mark.parametrize("name", ["trainer_bench_gen3"])
def test_battle_window_queues_only_after_its_keyed_ready(name, monkeypatch):
    run = duo.DuoRun.__new__(duo.DuoRun)
    run.cfg = duo.SCENARIOS[name]
    run.http_port = 1234
    run._pydec_note = lambda text: None
    setup = []
    monkeypatch.setattr(duo, "api", lambda *args: setup.append(args) or {"ok": True})
    calls = []
    run._gen3_prelude = lambda: ({0: "A0", 1: "A1"}, {0: "B0", 1: "B1"})
    run.go = lambda lines: calls.append(("go", lines))
    run._gen3_mark = lambda *args: calls.append(("ready", args))
    run.queue_command = lambda *args: calls.append(("queue", args))
    getattr(run, "orchestrate_" + name)()
    assert [row[0] for row in calls] == ["go", "ready", "queue"]
    key = "A" + str(run.cfg["battle_window_slot"])
    assert key in calls[1][1][1] and run.cfg["battle_window_case"] in calls[1][1][1]
    assert calls[2][1] == ("a", {"cmd": "force_faint", "key": key})
    assert [row[3]["area_id"] for row in setup] == ["viridian_city", "route_1", "route_2", "viridian_forest"]
    assert all(row[2] == "/api/debug/set_area_state" and row[3]["state"] == "linked" for row in setup)


def test_t2_retry_restarts_whole_attempt_once_only(monkeypatch):
    from tests.unit.test_gen3_battle_window_oracle import loss_receipts

    instances = []
    class Run:
        def __init__(self, name, args, attempt):
            instances.append(self)
            self.attempt = attempt
        def run(self):
            return False
    monkeypatch.setattr(duo, "DuoRun", Run)
    receipts = loss_receipts()
    monkeypatch.setattr(duo, "read_result", lambda name, side: receipts[side])
    monkeypatch.setattr(duo, "_archive_attempt", lambda *args: None)
    args = argparse.Namespace(game="gen3_frlg", idle_jitter=0)
    assert duo.run_scenario_with_rng_retry("trainer_bench_gen3", args) == (False, 2)
    assert [r.attempt for r in instances] == [1, 2]
    instances.clear()
    receipts["a"] = "RESULT: FAIL (PREPARATION lead fainted)"
    assert duo.run_scenario_with_rng_retry("trainer_bench_gen3", args) == (False, 1)
    assert len(instances) == 1


def test_battle_window_oracle_binding_runs_after_fresh_witness(monkeypatch, tmp_path):
    import gen3_battle_window_oracle as oracle

    run = _oracle_run("trainer_bench_gen3")
    run.gcfg = duo.GAMES["gen3_frlg"]
    run.attempt, run._link_keys = 1, {"a": "K"}
    calls = []
    hook = tmp_path / "hook.bin"
    hook.write_bytes(b"hook")
    run._witness_path = lambda side: str(hook)
    run._gen3_fixture_bytes = lambda side: b"fixture" + side.encode()
    run._gen3_flushed = lambda side: b"flushed" + side.encode()
    run.check_save_witness_gen3 = lambda results: calls.append("fresh witness")
    run._pydec_note = lambda text: calls.append(text)
    def verify(**kw):
        assert calls == ["fresh witness"]
        assert kw["helpers"] is duo and kw["witness"] == b"hook"
        assert kw["peer_flushed"] == b"flushedb" and kw["case"] == "trainer_bench"
        calls.append("oracle")
        return {"case": kw["case"], "key": "K", "slot": 1, "observed_samples": 2}
    monkeypatch.setattr(oracle, "verify", verify)
    run._run_oracle({"a": "receipt", "b": "idle"})
    assert calls[:2] == ["fresh witness", "oracle"]


# ── synthetic saves ─────────────────────────────────────────────────────────────────────────
def _mon(personality, *, party=True, ot_id=OT_A, level=5, species=7):
    mon = {
        "personality": personality, "ot_id": ot_id, "nickname": "MON", "language": 2,
        "is_bad_egg": 0, "has_species": 1, "is_egg_flag": 0, "block_box_rs": 0,
        "flags_unused": 0, "ot_name": "JONN", "markings": 0, "unknown": 0,
        "species": species, "held_item": 0, "experience": 135, "pp_bonuses": 0,
        "friendship": 70, "growth_filler": 0, "moves": [33, 39, 0, 0], "pp": [35, 30, 0, 0],
        "evs": dict.fromkeys(("hp", "attack", "defense", "speed", "sp_attack", "sp_defense"), 0),
        "contest": [0] * 6, "pokerus": 0, "met_location": 88, "met_level": 5, "met_game": 4,
        "pokeball": 4, "ot_gender": 0,
        "ivs": dict.fromkeys(("hp", "attack", "defense", "speed", "sp_attack", "sp_defense"), 7),
        "is_egg": 0, "ability_num": 0, "ribbons": 0,
    }
    if party:
        mon.update({"status": 0, "level": level, "mail": 0xFF, "hp": 20, "max_hp": 20,
                    "attack": 10, "defense": 11, "speed": 9, "sp_attack": 10, "sp_defense": 12})
    return mon


def _key(mon):
    return f"{mon['personality']:08X}:{mon['ot_id']:08X}"


def _blocks(party, boxes, balls):
    sb2 = bytearray(codec.SAVEBLOCK2_SIZE)
    sb2[0xA:0xE] = OT_A.to_bytes(4, "little")
    sb2[0xF20:0xF24] = (0x0000BEEF).to_bytes(4, "little")          # encryptionKey
    sb1 = bytearray(codec.SAVEBLOCK1_SIZE)
    sb1[codec.SB1_PARTY_COUNT_OFFSET] = len(party)
    for i, mon in enumerate(party):
        at = codec.SB1_PARTY_OFFSET + i * codec.PARTY_MON_SIZE
        raw = bytearray(codec.encode_party_mon(mon))
        if mon.get("flip_checksum"):
            raw[0x1C] ^= 0x01            # one bit of the stored secure checksum
        sb1[at:at + codec.PARTY_MON_SIZE] = raw
    if balls:
        sb1[0x430:0x432] = (4).to_bytes(2, "little")                 # ITEM_POKE_BALL
        sb1[0x432:0x434] = (balls ^ 0xBEEF).to_bytes(2, "little")
    storage = bytearray(codec.STORAGE_SIZE)
    for (box, slot), mon in boxes.items():
        at = codec.BOX_DATA_OFFSET + (box * codec.MONS_PER_BOX + slot) * codec.BOX_MON_SIZE
        storage[at:at + codec.BOX_MON_SIZE] = codec.encode_box_mon(mon)
    return {"sb1": sb1, "sb2": sb2, "storage": storage}


def _write_slot(image, counter, party, boxes=None, balls=4):
    blocks = _blocks(party, boxes or {}, balls)
    layout = codec.slot_layout()
    half = codec.NUM_SECTORS_PER_SLOT * (counter % codec.NUM_SAVE_SLOTS)
    for entry in layout:
        chunk = bytes(blocks[entry["object"]])[entry["offset"]:entry["offset"] + entry["size"]]
        at = (half + entry["id"]) * codec.SECTOR_SIZE
        image[at:at + codec.SECTOR_SIZE] = codec.write_sector(chunk, entry["id"], counter, layout)


def _fixture(party, boxes=None, balls=4):
    """Counter 2 in slot 0, the older counter 1 in slot 1, patterned Hall of Fame sectors."""
    image = bytearray(codec.FLASH_SIZE)
    _write_slot(image, 1, party[:1], balls=balls)
    _write_slot(image, 2, party, boxes, balls=balls)
    hof = 2 * codec.NUM_SECTORS_PER_SLOT * codec.SECTOR_SIZE
    image[hof:] = bytes((i * 13 + 5) & 0xFF for i in range(codec.FLASH_SIZE - hof))
    return bytes(image)


def _saved(fixture, counter, party, boxes=None, balls=4):
    image = bytearray(fixture)
    _write_slot(image, counter, party, boxes, balls=balls)
    return bytes(image)


STARTER, PIDGEY, CATCH = _mon(0x2D356A90), _mon(0x263620B6, species=16), _mon(0x11112222, species=19)


@pytest.fixture
def pair():
    fixture = _fixture([STARTER, PIDGEY])
    return fixture, _saved(fixture, 3, [STARTER, PIDGEY])


# ── check_gen3_witness: the byte rules ──────────────────────────────────────────────────────
def test_witness_positive_and_rtc_suffix_is_normalized(pair):
    fixture, saved = pair
    facts = duo.check_gen3_witness(saved, saved, fixture, saves=1)
    assert facts["counter"] == (2, 3) and facts["site"] == facts["file"] and not facts["rtc"]
    facts = duo.check_gen3_witness(saved, saved + b"\x01" * 16, fixture, saves=1)
    assert facts["rtc"] is True


def test_witness_two_saves_skip_the_other_slot_rule(pair):
    fixture, _ = pair
    twice = _saved(_saved(fixture, 3, [STARTER, PIDGEY]), 4, [STARTER])
    assert duo.check_gen3_witness(twice, twice, fixture, saves=2)["counter"] == (2, 4)


@pytest.mark.parametrize("mutate, flushed_from, saves, message", [
    ("short", None, 1, "expected 131072"),
    ("torn", None, 1, "not a complete save"),
    ("unchanged", None, 1, "did not advance"),
    (None, None, 2, "witnessed 2 save"),
    ("hof", None, 1, "sectors 28-31 differ"),
    ("old_slot", None, 1, "the unwritten slot 0 differ"),
    (None, "other_party", 1, "decoded party differs"),
    (None, "other_boxes", 1, "decoded boxes differ"),
    (None, "footer", 1, "does not match the flushed battery"),
    (None, "bad_length", 1, "unsupported length"),
])
def test_witness_each_failure_mode_is_named(pair, mutate, flushed_from, saves, message):
    fixture, saved = pair
    witness = bytearray(saved)
    if mutate == "short":
        witness = witness[:-1]
    elif mutate == "torn":
        # slot 1 (counter 3), section id 5: a data byte flipped breaks its checksum
        witness[(14 + 5) * codec.SECTOR_SIZE + 3] ^= 0xFF
    elif mutate == "unchanged":
        witness = bytearray(fixture)
    elif mutate == "hof":
        witness[30 * codec.SECTOR_SIZE] ^= 0xFF
    elif mutate == "old_slot":
        _write_slot(witness, 2, [STARTER])        # slot 0 rewritten: still valid, not the fixture's
    witness = bytes(witness)
    flushed = witness
    if flushed_from == "other_party":
        flushed = _saved(fixture, 3, [STARTER, PIDGEY, CATCH])
    elif flushed_from == "other_boxes":
        flushed = _saved(fixture, 3, [STARTER, PIDGEY], {(0, 0): _mon(0x33334444, party=False)})
    elif flushed_from == "footer":
        # an unchecksummed byte between the section data and the footer: decodes the same
        raw = bytearray(witness)
        raw[14 * codec.SECTOR_SIZE + 0xFA0] ^= 0xFF
        flushed = bytes(raw)
    elif flushed_from == "bad_length":
        flushed = witness + b"\x00" * 3
    with pytest.raises(RuntimeError, match=re.escape(message)):
        duo.check_gen3_witness(witness, flushed, fixture, saves=saves)


# ── check_gen3_witness(rr=True): RR's own layout and its narrower "untouched" range ────────
RR_MON = (277, 0xEBEF11DA, 0x2BDDC8BF)   # species, personality, ot_id -- test_gen3_rr_save_layout's own Treecko


def _rr_mon_bytes(species, personality, ot_id, level=5):
    """A plaintext (RR is CFRU_NO_ENCRYPT) party record: expand_compressed_box_mon + the
    party-only tail, same technique test_gen3_rr_save_layout's own synthetic test uses."""
    raw = bytearray(codec.expand_compressed_box_mon(_compressed(species, personality, ot_id)))
    raw += bytes(codec.PARTY_MON_SIZE - codec.BOX_MON_SIZE)
    raw[0x54] = level
    return bytes(raw)


def _rr_write_slot(image, counter, party=RR_MON, ext_byte=None, rotation=0):
    """Mutate `image` in place: one RR slot the way the game writes it (sibling of `_write_slot`
    above, RR-chunked via `_build_rr_image`'s own placement rule). `ext_byte`, when given, also
    rewrites the unrotated extension (sectors 30-31) -- omitted, it is left alone, the same way a
    real single-sector save leaves untouched chunks alone."""
    layout = codec.rr_slot_layout()
    sb1 = bytearray(codec.SAVEBLOCK1_SIZE)
    sb1[codec.SB1_PARTY_COUNT_OFFSET] = 1
    sb1[codec.SB1_PARTY_OFFSET:codec.SB1_PARTY_OFFSET + codec.PARTY_MON_SIZE] = _rr_mon_bytes(*party)
    source = {"sb2": bytes(codec.SAVEBLOCK2_SIZE), "sb1": bytes(sb1), "storage": bytes(codec.STORAGE_SIZE)}
    for sid, entry in enumerate(layout):
        chunk = source[entry["object"]][entry["offset"]:entry["offset"] + entry["size"]]
        index = ((rotation + sid) % codec.NUM_SECTORS_PER_SLOT) + codec.NUM_SECTORS_PER_SLOT * (counter % 2)
        image[index * codec.SECTOR_SIZE:(index + 1) * codec.SECTOR_SIZE] = \
            codec.write_sector(chunk, sid, counter, layout)
    if ext_byte is not None:
        ext = bytes([ext_byte]) * codec.RR_EXT_SIZE
        for n, index in enumerate(codec.RR_EXT_SECTORS):
            image[index * codec.SECTOR_SIZE:(index + 1) * codec.SECTOR_SIZE] = \
                ext[n * codec.CHUNK_SIZE_CFRU:(n + 1) * codec.CHUNK_SIZE_CFRU].ljust(codec.SECTOR_SIZE, b"\0")


def _rr_fixture(party=RR_MON, ext_byte=0x00):
    """Counter 2 in slot 0, the older counter 1 in slot 1 -- the RR sibling of `_fixture` above,
    so a one-save witness has a real (not erased) "other slot" to compare against."""
    image = bytearray(b"\xff" * codec.FLASH_SIZE)
    _rr_write_slot(image, 1, party, ext_byte=ext_byte)
    _rr_write_slot(image, 2, party, ext_byte=ext_byte)
    return bytes(image)


def _rr_saved(fixture, counter, party=RR_MON, ext_byte=None):
    image = bytearray(fixture)
    _rr_write_slot(image, counter, party, ext_byte=ext_byte)
    return bytes(image)


def test_rr_witness_positive():
    fixture = _rr_fixture()
    saved = _rr_saved(fixture, 3)
    facts = duo.check_gen3_witness(saved, saved, fixture, saves=1, rr=True)
    assert facts["counter"] == (2, 3)


def test_rr_witness_extension_boxes_may_legitimately_change():
    """docs/gen3/research/flash_save.md:136: sectors 30-31 (RR boxes 20-22) carry no checksum or
    generation counter of their own, and a normal save CAN rewrite them -- unlike vanilla's Hall
    of Fame, asserting byte-equality against the fixture there would be wrong, not stricter."""
    fixture = _rr_fixture(ext_byte=0x00)
    saved = _rr_saved(fixture, 3, ext_byte=0x11)
    facts = duo.check_gen3_witness(saved, saved, fixture, saves=1, rr=True)
    assert facts["counter"] == (2, 3)


def test_rr_witness_still_refuses_a_changed_hall_of_fame_sector():
    """Sectors 28-29 stay real Hall of Fame/Trainer Tower territory even under rr=True (only
    30-31 are RR's own extension) -- narrowing the range must not widen it into a no-op."""
    fixture = _rr_fixture()
    saved = bytearray(_rr_saved(fixture, 3))
    saved[28 * codec.SECTOR_SIZE] ^= 0xFF
    with pytest.raises(RuntimeError, match="sectors 28-29"):
        duo.check_gen3_witness(bytes(saved), bytes(saved), fixture, saves=1, rr=True)


def test_rr_witness_decoded_party_mismatch_is_named():
    """witness vs FLUSHED, not vs the fixture (check_gen3_witness compares those two): a
    from-scratch `saved` with a different party than the dumped witness must be caught."""
    fixture = _rr_fixture()
    witness = _rr_saved(fixture, 3, party=RR_MON)
    flushed = _rr_saved(fixture, 3, party=(1, 0xAAAABBBB, 0x2BDDC8BF))
    with pytest.raises(RuntimeError, match="decoded party differs"):
        duo.check_gen3_witness(witness, flushed, fixture, saves=1, rr=True)


# ── check_save_witness_gen3: the receipt and file rules ────────────────────────────────────
def _witness_run(tmp_path, monkeypatch, pair, scenario="faint_cmd_gen3"):
    fixture, saved = pair
    build = tmp_path / "build"
    build.mkdir(exist_ok=True)
    run = duo.DuoRun.__new__(duo.DuoRun)
    run.scenario, run.attempt, run.game = scenario, 1, "gen3_frlg"
    run.emus, run._started = [], 0
    run.cfg, run.gcfg = dict(duo.SCENARIOS[scenario]), dict(duo.GAMES["gen3_frlg"])
    notes = []
    run._pydec_note = notes.append
    monkeypatch.setattr(duo, "BUILD", str(build))
    monkeypatch.setattr(duo, "REPO", str(tmp_path))
    monkeypatch.setattr(run, "_gen3_flushed", lambda inst: saved)
    monkeypatch.setattr(run, "_gen3_fixture_bytes", lambda inst: fixture)
    receipts = {}
    for inst in ("a", "b"):
        path = build / f"e2e_{scenario}_{inst}_1_witness.bin"
        path.write_bytes(saved)
        rel = os.path.relpath(path, tmp_path).replace("\\", "/")
        receipts[inst] = (f"SAVE_WITNESS faint_cmd counter=2->3\n"
                          f"SAVE_WITNESS_DUMP path={rel} bytes={len(saved)} saves=1 frame=10 counter=3\n")
    return run, receipts, notes, build


def test_witness_method_positive(tmp_path, monkeypatch, pair):
    run, receipts, notes, _ = _witness_run(tmp_path, monkeypatch, pair)
    run.check_save_witness_gen3(receipts)
    assert len(notes) == 2 and all("match=true saves=1 counter=2->3" in n for n in notes)


def test_witness_method_refuses_a_half_that_never_saved(tmp_path, monkeypatch, pair):
    run, receipts, _, _ = _witness_run(tmp_path, monkeypatch, pair)
    receipts["b"] = "SAVE_WITNESS_DUMP_SKIPPED why=r0-255-r5-0\n"
    with pytest.raises(RuntimeError, match="b: the receipt carries no SAVE_WITNESS_DUMP.*r0-255"):
        run.check_save_witness_gen3(receipts)


def test_witness_method_honours_no_save_and_refuses_a_dump_there(tmp_path, monkeypatch, pair):
    run, receipts, notes, _ = _witness_run(tmp_path, monkeypatch, pair, "reconnect_gen3")
    good_a = receipts["a"]
    receipts["a"] = "WRITES 0\n"
    run.check_save_witness_gen3(receipts)
    assert "saves=0 skipped (no_save)" in notes[0]
    receipts["a"] = good_a
    with pytest.raises(RuntimeError, match="declared no_save"):
        run.check_save_witness_gen3(receipts)


def test_witness_method_refuses_a_stale_file(tmp_path, monkeypatch, pair):
    run, receipts, _, build = _witness_run(tmp_path, monkeypatch, pair)
    run._started = os.path.getmtime(build / "e2e_faint_cmd_gen3_a_1_witness.bin") + 60
    with pytest.raises(RuntimeError, match="predates this attempt"):
        run.check_save_witness_gen3(receipts)


@pytest.mark.parametrize("extra, message", [
    ("SAVE_WITNESS_DUMP_FAIL boom\n", "the last dump attempt"),
    ("SAVE_WITNESS_DUMP path=x bytes=1 saves=3 frame=1 counter=4\n", "the dump ordinals"),
])
def test_witness_method_reads_dump_outcomes_in_order(tmp_path, monkeypatch, pair, extra, message):
    run, receipts, _, _ = _witness_run(tmp_path, monkeypatch, pair)
    receipts["a"] += extra
    with pytest.raises(RuntimeError, match=message):
        run.check_save_witness_gen3(receipts)


def test_witness_method_refuses_a_path_the_body_did_not_log(tmp_path, monkeypatch, pair):
    run, receipts, _, _ = _witness_run(tmp_path, monkeypatch, pair)
    receipts["a"] = receipts["a"].replace("_a_1_witness", "_a_2_witness")
    with pytest.raises(RuntimeError, match="landed at"):
        run.check_save_witness_gen3(receipts)


# ── _run_oracle: required oracle, witness first ────────────────────────────────────────────
def _oracle_run(scenario, game="gen3_frlg", cfg=None):
    run = duo.DuoRun.__new__(duo.DuoRun)
    run.scenario, run.game = scenario, game
    run.cfg = cfg if cfg is not None else dict(duo.SCENARIOS[scenario])
    return run


def test_a_gen3_frlg_scenario_without_an_oracle_fails():
    run = _oracle_run("faint_cmd_gen3", cfg={"flags": [], "timeout": 1})
    with pytest.raises(RuntimeError, match="faint_cmd_gen3 declares no post-result oracle"):
        run._run_oracle({"a": "", "b": ""})


def test_the_gen3_witness_runs_before_the_oracle(monkeypatch):
    run = _oracle_run("boxsync_gen3")
    calls = []
    monkeypatch.setattr(run, "check_save_witness_gen3", lambda results: calls.append("witness"))
    monkeypatch.setattr(run, "check_save_witness", lambda results: calls.append("gen1 witness"))
    monkeypatch.setattr(run, "assert_boxsync_gen3_saved", lambda results: calls.append("oracle"))
    run._run_oracle({"a": "", "b": ""})
    assert calls == ["witness", "oracle"]


def test_rows_without_the_flag_keep_their_verdict_paths():
    """A game-less (`legacy`) stub row with no oracle still returns quietly; the (renamed,
    new-client) gen3_rr row and gen1_new refuse a verdict without a saved-state readback
    (FAMILY_EVIDENCE require_oracle, merged from master's evidence pipeline)."""
    assert _oracle_run("faint", game="legacy")._run_oracle({}) is None
    with pytest.raises(RuntimeError, match="faint declares no post-result oracle"):
        _oracle_run("faint", game="gen3_rr")._run_oracle({})
    with pytest.raises(RuntimeError, match="link_new declares no post-result oracle"):
        _oracle_run("link_new", game="gen1_new", cfg={"flags": []})._run_oracle({})


# ── saved-state oracle helpers ─────────────────────────────────────────────────────────────
def _rom_with_pp(table, title="firered"):
    """A ROM image holding only the move table's PP bytes the synthetic records use, at the
    title's own rom.BATTLE_MOVES_ADDR -- so gen3_limits' real ROM read is what these tests run."""
    with open(duo.gen3_profile_path(title), encoding="utf-8") as handle:
        prof = json.load(handle)["titles"][title]
    base = prof["rom"]["BATTLE_MOVES_ADDR"] - 0x08000000
    stride, off = prof["derived"]["BATTLE_MOVE_ENTRY_SIZE"], prof["derived"]["BATTLE_MOVE_PP_OFFSET"]
    rom = bytearray(base + 1024 * stride)
    for move, pp in table.items():
        rom[base + move * stride + off] = pp
    return bytes(rom)


# Tackle 35 PP, Tail Whip 30 PP (pret src/data/battle_moves.h), as the synthetic mons carry them
LIMITS = duo.gen3_limits("firered", _rom_with_pp({33: 35, 39: 30}))


def _rt(*args, **kw):
    return duo.gen3_round_trip_problems(*args, **{"limits": LIMITS, **kw})


def _mem(*args, **kw):
    return duo.gen3_memorial_problems(*args, **{"limits": LIMITS, **kw})


def _cap(*args, **kw):
    return duo.gen3_capture_problems(*args, **{"limits": LIMITS, **kw})


def _decoded(image):
    return duo.gen3_decode(image)


def test_memorial_problems_positive_and_negatives(pair):
    fixture, _ = pair
    good = _saved(fixture, 3, [STARTER], {(13, 0): _mon(PIDGEY["personality"], party=False, species=16)})
    key = _key(PIDGEY)
    assert _mem("b", _decoded(good), _decoded(fixture), key, 13) == []
    kept = _saved(fixture, 3, [STARTER, PIDGEY])
    assert any("still in the saved party" in p for p in
               _mem("b", _decoded(kept), _decoded(fixture), key, 13))
    wrong_box = _saved(fixture, 3, [STARTER], {(0, 0): _mon(PIDGEY["personality"], party=False)})
    assert any("not exactly once in the memorial" in p for p in
               _mem("b", _decoded(wrong_box), _decoded(fixture), key, 13))


def test_round_trip_problems_positive_and_negatives(pair):
    fixture, saved = pair
    key = _key(PIDGEY)
    assert _rt("a", _decoded(saved), _decoded(fixture), key) == []
    moved = dict(PIDGEY, level=6, max_hp=22)
    changed = _saved(fixture, 3, [STARTER, moved])
    assert any("differs from the fixture" in p for p in
               _rt("a", _decoded(changed), _decoded(fixture), key))
    boxed_too = _saved(fixture, 3, [STARTER, PIDGEY], {(0, 0): _mon(PIDGEY["personality"], party=False)})
    assert any("still (or also) in a saved box" in p for p in
               _rt("a", _decoded(boxed_too), _decoded(fixture), key))


def test_capture_problems_and_ball_count(pair):
    fixture, _ = pair
    caught = _saved(fixture, 3, [STARTER, PIDGEY, CATCH], balls=3)
    key = _key(CATCH)
    sent = {"species_id": 19, "level": 5, "held_item_id": 0, "nickname": "MON"}
    assert _cap("a", _decoded(caught), _decoded(fixture), key, sent) == []
    assert any("species" in p for p in _cap(
        "a", _decoded(caught), _decoded(fixture), key, dict(sent, species_id=16)))
    assert any("level" in p for p in _cap(
        "a", _decoded(caught), _decoded(fixture), key, dict(sent, level=7)))
    assert duo.gen3_ball_count(fixture) == 4 and duo.gen3_ball_count(caught) == 3


def test_receipt_problems_order_and_forbidden():
    text = "RX box_mon key=K\nTX stats_cache K {}\n"
    assert duo.gen3_receipt_problems("b", text, required=[duo.gen3_rx("box_mon", "K")],
                                     ordered=[(duo.gen3_rx("box_mon", "K"),
                                               duo.gen3_tx("stats_cache", "K"))]) == []
    assert duo.gen3_receipt_problems("b", text, ordered=[(duo.gen3_tx("stats_cache", "K"),
                                                          duo.gen3_rx("box_mon", "K"))])
    assert duo.gen3_receipt_problems("b", text, forbidden=[duo.gen3_rx("box_mon", "K")])
    assert re.search(duo.gen3_tx("whiteout", "-"), "TX whiteout - {}")
    assert not re.search(duo.gen3_rx("box_mon", "K"), "RX box_mon key=KX")


# ── oracle methods on stubbed runs ─────────────────────────────────────────────────────────
def _oracle_stub(monkeypatch, tmp_path, scenario, saved, fixture, links):
    run = _oracle_run(scenario)
    run.gcfg, run.emus, run.data_dir = dict(duo.GAMES["gen3_frlg"]), [], str(tmp_path)
    notes = []
    run._pydec_note = notes.append
    (tmp_path / "links.json").write_text(json.dumps({"links": links}), encoding="utf-8")
    monkeypatch.setattr(run, "_gen3_flushed", lambda inst: saved[inst])
    monkeypatch.setattr(run, "_gen3_fixture_bytes", lambda inst: fixture)
    monkeypatch.setattr(run, "_gen3_limits", lambda inst: LIMITS)
    run._link_keys = {"a": _key(PIDGEY), "b": _key(PIDGEY_B)}
    return run, notes


PIDGEY_B = _mon(0x263620B6, ot_id=0x6621F275, species=16)


def _faint_cmd_receipts(ka, kb):
    return {"a": f"RX memorialize key={ka}\nTX memorialize_done {ka} {{}}\n",
            "b": (f"RX force_faint key={kb}\n[client] [SLink-gen3] write overworld 0x02024282 +2 frame 9\n"
                  f"FORCED_HP0 {kb} frame=9 in_battle=0 battler=0\nRX memorialize key={kb}\n"
                  f"TX memorialize_done {kb} {{}}\n")}


def test_faint_cmd_oracle_positive_and_negative(monkeypatch, tmp_path):
    fix_a = _fixture([STARTER, PIDGEY])
    saved_a = _saved(fix_a, 3, [STARTER], {(13, 0): _mon(PIDGEY["personality"], party=False, species=16)})
    ka = _key(PIDGEY)
    # one synthetic save serves both halves here, so both halves carry A's key
    link = [{"a": {"key": ka}, "b": {"key": ka}, "status": "memorial"}]
    run, notes = _oracle_stub(monkeypatch, tmp_path, "faint_cmd_gen3", {"a": saved_a, "b": saved_a},
                              fix_a, link)
    run._link_keys = {"a": ka, "b": ka}
    receipts = _faint_cmd_receipts(ka, ka)
    run.assert_faint_cmd_gen3_saved(receipts)
    assert notes and "faint_cmd" in notes[-1]
    receipts["b"] = receipts["b"].replace("in_battle=0", "in_battle=1")
    with pytest.raises(RuntimeError, match="FORCED_HP0"):
        run.assert_faint_cmd_gen3_saved(receipts)


def test_boxsync_oracle_reads_the_keyed_ack_order(monkeypatch, tmp_path):
    fixture = _fixture([STARTER, PIDGEY])
    saved = _saved(fixture, 3, [STARTER, PIDGEY])
    k = _key(PIDGEY)
    run, _ = _oracle_stub(monkeypatch, tmp_path, "boxsync_gen3", {"a": saved, "b": saved}, fixture,
                          [{"a": {"key": k}, "b": {"key": "B"}, "status": "alive"}])
    run._link_keys = {"a": k, "b": "B"}
    monkeypatch.setattr(run, "_gen3_flushed", lambda inst: saved)
    receipts = {"a": (f"TX party_to_box {k} {{}}\nBOXED_OBSERVED {k} box=0:0\n"
                      f"TX box_to_party {k} {{}}\nRETURNED_OBSERVED {k} slot=1\n"),
                "b": ("RX box_mon key=B\nTX stats_cache B {}\nBOXED_OBSERVED B box=0:0\n"
                      "RX party_mon key=B\nTX sync_retrieve_done B {}\nRETURNED_OBSERVED B slot=1\n")}
    # B's key is not in the synthetic save: the round-trip helper must say so
    with pytest.raises(RuntimeError, match="b: B appears 0x"):
        run.assert_boxsync_gen3_saved(receipts)
    run._link_keys = {"a": k, "b": k}
    (tmp_path / "links.json").write_text(json.dumps({"links": [
        {"a": {"key": k}, "b": {"key": k}, "status": "alive"}]}), encoding="utf-8")
    receipts["b"] = receipts["b"].replace(" B", f" {k}").replace("=B", f"={k}")
    run.assert_boxsync_gen3_saved(receipts)
    receipts["b"] = receipts["b"].replace(f"TX stats_cache {k}", "TX nothing")
    with pytest.raises(RuntimeError, match="stats_cache"):
        run.assert_boxsync_gen3_saved(receipts)


def test_link_oracle_counts_the_thrown_balls(monkeypatch, tmp_path):
    fixture = _fixture([STARTER, PIDGEY])
    caught = _saved(fixture, 3, [STARTER, PIDGEY, CATCH], balls=2)
    k = _key(CATCH)
    run, notes = _oracle_stub(monkeypatch, tmp_path, "link_gen3", {"a": caught, "b": caught}, fixture,
                              [{"a": {"key": k}, "b": {"key": k}, "status": "alive", "area_id": "route_1"}])
    run._link_keys = {"a": k, "b": k}
    line = f'TX capture {k} {{"area_id":"route_1","event":"capture","key":"{k}","species_id":19}}\n'
    receipts = dict.fromkeys(("a", "b"), line + "THREW 1\nTHREW 2\n")
    run.assert_link_gen3_saved(receipts)
    assert any("BAG_BALLS baseline=4 final=2 throws=2" in n for n in notes)
    receipts["a"] = line + "THREW 1\n"
    with pytest.raises(RuntimeError, match="saved 2 Poke Balls"):
        run.assert_link_gen3_saved(receipts)


# ── RR-only oracles (P5, card C5-5) ─────────────────────────────────────────────────────────
REFUSAL = "this radical_red cartridge needs the SLink companion patch; prepare it through the Manager or /patcher"


def _clean_rr_refused_receipts():
    a = ("MYKEY 1 KA\nTX hello - {}\nPROBE_SETTLED writes=0 rx=0\nWRITES 0\n")
    b = (f"[client] [SLink-gen3] refused: {REFUSAL}\n"
         f"REFUSED_AT_LAUNCH [SLink-gen3] refused: {REFUSAL}\nWRITES 0\n")
    return {"a": a, "b": b}


def _clean_rr_refused_run(connected=False, links=()):
    run = _oracle_run("clean_rr_refused_gen3", game="gen3_rr")
    run.notes = []
    run._pydec_note = run.notes.append
    run._status = lambda: {"players": {"a": {"connected": True}, **({"b": {"connected": True}} if connected else {})}}
    run._links_json = lambda: list(links)
    return run


def test_clean_rr_refused_is_the_clean_rr_refusal_proof():
    """Owner policy 2026-10-02 (companion REQUIRED): the clean RR is refused at launch with the
    companion verdict, links nothing and writes nothing; the companion side A boots alone and stays
    quiet. Neither side saves."""
    row = duo.SCENARIOS["clean_rr_refused_gen3"]
    assert row["no_save"] == ("a", "b") and row["expect_refused"] == ("b",)
    assert row["rom_kind"] == {"a": "companion", "b": "clean"}
    run = _clean_rr_refused_run()
    receipts = _clean_rr_refused_receipts()
    run.assert_clean_rr_refused_gen3_saved(receipts)
    assert run.notes and "refused" in run.notes[-1]


@pytest.mark.parametrize("side, mutate, problem", [
    # B: the refusal line is the evidence; every way the cartridge could have worked is red
    ("b", lambda t: t.replace("needs the SLink companion patch", "is not an admitted cartridge"), "missing"),
    ("b", lambda t: t.replace("REFUSED_AT_LAUNCH", "NOTHING"), "REFUSED_AT_LAUNCH"),
    ("b", lambda t: t.replace("WRITES 0", "WRITES 1"), "WRITES 0"),
    ("b", lambda t: t + "MYKEY 1 KB\n", "forbidden"),
    ("b", lambda t: t + "TX hello - {}\n", "forbidden"),
    ("b", lambda t: t + "RX apply_prepare key=KB\n", "forbidden"),
    ("b", lambda t: t + "[client] [SLink-gen3] write native 0x0203F806 +2 frame 9\n", "forbidden"),
    ("b", lambda t: t + "[client] [SLink-gen3] gen3_rr/radical_red (clean by hash) player b -> 127.0.0.1:1 (rom AB)\n",
     "forbidden"),
    ("b", lambda t: t + "SAVE_WITNESS_DUMP path=p bytes=1 saves=1 frame=1 counter=5\n", "forbidden"),
    # A: the companion booted and connected, and nothing B-shaped reached it
    ("a", lambda t: t.replace("TX hello - {}\n", ""), "hello"),
    ("a", lambda t: t.replace("PROBE_SETTLED writes=0 rx=0\n", ""), "PROBE_SETTLED"),
    ("a", lambda t: t.replace("writes=0 rx=0", "writes=2 rx=0"), "PROBE_SETTLED"),
    ("a", lambda t: t.replace("writes=0 rx=0", "writes=0 rx=1"), "PROBE_SETTLED"),
    ("a", lambda t: t + "RX apply_prepare key=KA\n", "forbidden"),
    ("a", lambda t: t + "RX force_faint key=KA\n", "forbidden"),
])
def test_clean_rr_refused_oracle_is_red_on_every_way_the_clean_side_could_work(side, mutate, problem):
    run = _clean_rr_refused_run()
    receipts = _clean_rr_refused_receipts()
    receipts[side] = mutate(receipts[side])
    with pytest.raises(RuntimeError, match=problem):
        run.assert_clean_rr_refused_gen3_saved(receipts)


def test_clean_rr_refused_oracle_reads_the_server_for_no_partner_and_no_link():
    receipts = _clean_rr_refused_receipts()
    with pytest.raises(RuntimeError, match="b connected"):
        _clean_rr_refused_run(connected=True).assert_clean_rr_refused_gen3_saved(receipts)
    with pytest.raises(RuntimeError, match="persisted link"):
        _clean_rr_refused_run(links=[{"a": {"key": "KA"}, "b": {"key": "KB"}, "status": "alive"}]
                           ).assert_clean_rr_refused_gen3_saved(receipts)
    gone = _clean_rr_refused_run()
    gone._status = lambda: None
    with pytest.raises(RuntimeError, match="status"):
        gone.assert_clean_rr_refused_gen3_saved(receipts)


def test_clean_rr_refused_orchestration_waits_for_a_alone_then_b_refusal_then_releases_a():
    run = _clean_rr_refused_run()
    calls, receipts = [], {"a": "", "b": ""}
    run._read_receipt = lambda inst: receipts[inst]
    run.wait_for = lambda desc, pred, timeout: calls.append(desc) or pred() or calls.append("PRED_FALSE")
    run.go = lambda *args: calls.append("go")
    receipts.update(a="MYKEY 1 KA\n", b=_clean_rr_refused_receipts()["b"])
    run._status = lambda: {"players": {"a": {"connected": True}}}
    run.orchestrate_clean_rr_refused_gen3()
    assert calls == ["a: MYKEY line", "a: hello accepted", "b: refused at launch", "go"], calls
    # B never produces keys or a hello: the orchestration must not wait on either
    receipts["b"] = ""
    calls.clear()
    run.orchestrate_clean_rr_refused_gen3()
    assert "PRED_FALSE" in calls


def test_a_refused_side_is_launched_with_expect_refused():
    run = _oracle_run("clean_rr_refused_gen3", game="gen3_rr")
    assert run._gen3_expect_refused("b") is True and run._gen3_expect_refused("a") is False
    assert _oracle_run("faint_cmd_gen3", game="gen3_rr")._gen3_expect_refused("b") is False


def _launch_verdict():
    """The driver's REAL launch_verdict (lua/tests/duo/duo_gen3_main.lua), lifted by name."""
    from lupa import LuaRuntime

    runtime = LuaRuntime(unpack_returned_tuples=True)
    text = DRIVER.read_text(encoding="utf-8")
    found = re.search(_LUA_DEF.format("launch_verdict"), text, re.M | re.S)
    assert found, "duo_gen3_main.lua has no launch_verdict"
    return runtime.execute(found.group(0) + "\nreturn launch_verdict")


def test_the_driver_passes_a_refused_launch_only_when_refusal_was_expected():
    verdict = _launch_verdict()
    says = f"[SLink-gen3] refused: {REFUSAL}"
    # expected refusal with the companion verdict: done, pass
    done, ok, msg = verdict(None, says, True)
    assert (done, ok) == (True, True) and "companion" in msg
    # expected refusal but a different refusal (a bad hash, not the companion rule): done, FAIL
    done, ok, msg = verdict(None, "[SLink-gen3] refused: header BPEE is not an admitted Gen 3 cartridge", True)
    assert (done, ok) == (True, False)
    # expected refusal but no refusal at all, and no client: FAIL
    assert verdict(None, None, True)[:2] == (True, False)
    # expected refusal but the cartridge was ADMITTED and a client built: FAIL, never carry on
    done, ok, msg = verdict({}, None, True)
    assert (done, ok) == (True, False) and "admitted" in msg
    # ordinary rows: a built client carries on; a refusal with none expected is the old failure
    assert verdict({}, None, False) is False   # a built client: nothing to decide, carry on
    done, ok, msg = verdict(None, says, False)
    assert (done, ok) == (True, False) and "built no client" in msg


def test_rival_swap_is_only_a_negative_characterization():
    """Finding 6: rival_swap is a BLOCKED NEGATIVE CONTROL (a dummy team refused), labelled so in
    the registry, the oracle's PYDEC line and the run summary -- never a qualification pass."""
    control = duo.SCENARIOS["rival_swap_gen3"].get("control", "")
    assert "BLOCKED" in control and "negative" in control
    assert "CONTROL, not a qualification pass" in duo.summary_lines(
        {"rival_swap_gen3": (True, 1)}, "gen3_rr")[0]
    assert "CONTROL" not in duo.summary_lines({"faint_cmd_gen3": (True, 1)}, "gen3_rr")[0]
    run = _oracle_run("rival_swap_gen3", game="gen3_rr")
    notes = []
    run._pydec_note = notes.append
    a_untouched = ([STARTER], {})
    run._gen3_saved = lambda inst: a_untouched
    run._gen3_fixture_saved = lambda inst: a_untouched
    b_receipt = ("READY_IN_BATTLE\nRX replace_rival_team\n"
                 'TX rival_team_replaced - {"error":"stale_battle_id","species_ids":[],"trainer_id":0}\n')
    run.assert_rival_swap_gen3_saved({"a": "", "b": b_receipt})
    assert notes and "NEGATIVE CONTROL (not qualification)" in notes[-1]
    # the C5-10 identity gate answers first (live 3fa789da); window_closed would mean an
    # identity-less command got past it
    for other in ("window_closed", "ok"):
        with pytest.raises(RuntimeError, match="expected 'stale_battle_id'"):
            run.assert_rival_swap_gen3_saved({"a": "", "b": b_receipt.replace("stale_battle_id", other)})
    with pytest.raises(RuntimeError, match="READY_IN_BATTLE"):
        run.assert_rival_swap_gen3_saved({"a": "", "b": b_receipt.replace("READY_IN_BATTLE\n", "")})


# ── per-game dispatch: ROM, battery, config, stub ──────────────────────────────────────────
def test_the_row_resolves_titles_fixtures_and_one_line_leafgreen():
    row = duo.GAMES["gen3_frlg"]
    run = duo.DuoRun.__new__(duo.DuoRun)
    run.gcfg, run.cfg = dict(row), dict(duo.SCENARIOS["boxsync_gen3"])
    # The G4 pairing: A FireRed, B LeafGreen (fixtures 0978a5be).
    assert run.is_gen3_battery and run._gen3_title("a") == "firered"
    assert run._gen3_title("b") == "leafgreen"
    assert run._gen3_fixture_path("a").endswith(os.path.join("gen3", "firered_party_battle.sav"))
    assert run._gen3_fixture_path("b").endswith(os.path.join("gen3", "leafgreen_party_town.sav"))
    # FR/FR stays one line away (the _b fixtures remain committed).
    run.gcfg = dict(row, sides=dict(row["sides"], b=("firered", "firered_party_{target}_b")))
    assert run._gen3_title("b") == "firered"
    assert run._gen3_fixture_path("b").endswith("firered_party_town_b.sav")
    # P5: radical_red joined (GAMES["gen3_rr"]) alongside firered/leafgreen; E4: emerald;
    # X3: the expansion reference build (GAMES["gen3_exp"], test_e2e_duo_gen3_exp.py).
    assert set(duo.GEN3_TITLES) == {"firered", "leafgreen", "radical_red", "emerald",
                                    "emerald_expansion_28877d73"}
    for inst in ("a", "b"):
        assert row["sides"][inst][0] in duo.GEN3_TITLES


def _gba_config(tmp_path):
    path = tmp_path / "config.ini"
    path.write_text(json.dumps({"MainWindowPosition": "1200, -1300", "PathEntries": {"Paths": [
        {"System": "GBA", "Type": "Save RAM", "Path": ""}]}}), encoding="utf-8")
    return path


@pytest.mark.parametrize("scenario", ["faint_cmd_gen3", "trainer_bench_gen3", "active_end_gen3"])
def test_launch_seeds_the_flash_body_and_writes_a_gba_config(monkeypatch, tmp_path, scenario):
    fixture = _fixture([STARTER, PIDGEY])
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    (fixtures / "firered_party_town.sav").write_bytes(fixture + b"\x07" * 16)   # RTC suffix dropped
    (fixtures / "firered_party_town_b.sav").write_bytes(fixture)
    (fixtures / "firered_party_battle.sav").write_bytes(fixture)
    (fixtures / "firered_party_trainer.sav").write_bytes(fixture + b"\x07" * 16)
    monkeypatch.setattr(duo, "GEN3_FIXTURES", str(fixtures))
    monkeypatch.setattr(duo, "BUILD", str(tmp_path))
    monkeypatch.setattr(duo, "BIZHAWK_CONFIG", str(_gba_config(tmp_path)))
    monkeypatch.setattr(duo, "_LANE_ORDINAL", {})
    launched = []
    monkeypatch.setattr(duo.subprocess, "Popen", lambda argv, **kw: launched.append(argv))
    monkeypatch.setattr(gen3_fixtures, "stage_rom", lambda src: "patch/build/gen3_fr.gba")
    args = argparse.Namespace(game="gen3_frlg", lane="t", scenario=scenario, idle_jitter=0)
    run = duo.DuoRun(scenario, args, attempt=1)
    monkeypatch.setattr(run, "_gen3_rom", lambda inst: f"patch/build/gen3_{inst}.gba")
    # a clean dump (the fake ROM path is no file): the cartridge-kind decision is tested in test_e2e_duo_gen3_companion_battery.py
    monkeypatch.setattr(run, "_gen3_companion_cart", lambda inst, title: False)
    run.launch_instance("a")
    battery = Path(run._saveram_dir("a")) / "Pokemon - FireRed Version (USA).SaveRAM"
    assert battery.read_bytes() == fixture
    cfg = json.loads(Path(run.cfg_path("a")).read_text(encoding="utf-8-sig"))
    gba = [e for e in cfg["PathEntries"]["Paths"] if e["System"] == "GBA"]
    assert gba[0]["Path"] == run._saveram_dir("a").replace("\\", "/")
    stub = Path(run.stub_path("a")).read_text()
    assert 'title = "firered"' in stub and 'scenario_prefix = "gen3_"' in stub
    assert 'game = "gen3_frlg"' in stub and "duo_gen3_main.lua" in stub
    assert launched[0][-1] == "patch/build/gen3_a.gba"
    if scenario == "trainer_bench_gen3":
        assert 'scenario_module = "battle_window"' in stub
        assert f'battle_window_case = "{duo.SCENARIOS[scenario]["battle_window_case"]}"' in stub
    if scenario == "active_end_gen3":
        assert 'scenario_module = "linked_faint_active"' in stub
        assert 'active_faint_case = "command"' in stub and "battle_window_case" not in stub


def test_gen3_rom_prefers_the_dump_then_the_staged_copy(monkeypatch, tmp_path):
    # Hermetic: _gen3_rom walks up EVERY parent of REPO for the dump, so a real FireRed dump anywhere above the pytest
    # temp root (a checkout root, F:/slink-work, ...) would satisfy the search the test expects to fail. Only the dump this
    # test creates under tmp_path may exist.
    import pathlib
    real_is_file = pathlib.Path.is_file
    monkeypatch.setattr(pathlib.Path, "is_file", lambda self: real_is_file(self) and not (
        self.name == "Pokemon - FireRed Version (USA).gba" and tmp_path not in self.parents))
    run = duo.DuoRun.__new__(duo.DuoRun)
    run.gcfg, run.cfg = dict(duo.GAMES["gen3_frlg"]), dict(duo.SCENARIOS["link_gen3"])
    root = tmp_path / "main" / "wt"
    root.mkdir(parents=True)
    monkeypatch.setattr(duo, "REPO", str(root))
    (tmp_path / "main" / "Pokemon - FireRed Version (USA).gba").write_bytes(b"rom")
    staged = []
    monkeypatch.setattr(gen3_fixtures, "stage_rom", lambda src: staged.append(src) or "staged.gba")
    assert run._gen3_rom("a") == "staged.gba" and staged[0].endswith("FireRed Version (USA).gba")
    (tmp_path / "main" / "Pokemon - FireRed Version (USA).gba").unlink()
    with pytest.raises(FileNotFoundError, match="FireRed"):
        run._gen3_rom("a")


# ── RR ROM/battery staging: staged companion build vs the raw clean dump (P5, C5-5) ────────
def test_gen3_rr_rom_companion_uses_the_staged_build_directly(monkeypatch, tmp_path):
    """No dump search at all for the ordinary (default) kind: `staged` (ROM_REL) short-circuits
    it, so a companion-kind run never depends on a raw RR dump being reachable."""
    run = duo.DuoRun.__new__(duo.DuoRun)
    run.gcfg, run.cfg = dict(duo.GAMES["gen3_rr"]), dict(duo.SCENARIOS["faint_cmd_gen3"])
    root = tmp_path / "wt"
    (root / "patch" / "build").mkdir(parents=True)
    (root / duo.ROM_REL).write_bytes(b"rom")
    monkeypatch.setattr(duo, "REPO", str(root))
    assert run._gen3_rom("a") == duo.ROM_REL


def test_gen3_rr_rom_companion_missing_build_refuses(monkeypatch, tmp_path):
    run = duo.DuoRun.__new__(duo.DuoRun)
    run.gcfg, run.cfg = dict(duo.GAMES["gen3_rr"]), dict(duo.SCENARIOS["faint_cmd_gen3"])
    (tmp_path / "empty").mkdir()
    monkeypatch.setattr(duo, "REPO", str(tmp_path / "empty"))
    with pytest.raises(FileNotFoundError, match="slink_RR"):
        run._gen3_rom("a")


def test_gen3_rr_rom_clean_kind_searches_the_raw_dump(monkeypatch, tmp_path):
    """clean_rr_refused_gen3's `rom_kind: {"b": "clean"}` bypasses `staged` and searches for the
    raw dump (patch/tools/build.py:91 DEFAULT_RR / patch/README.md:18), same rule as firered."""
    run = duo.DuoRun.__new__(duo.DuoRun)
    run.gcfg, run.cfg = dict(duo.GAMES["gen3_rr"]), dict(duo.SCENARIOS["clean_rr_refused_gen3"])
    root = tmp_path / "main" / "wt"
    root.mkdir(parents=True)
    monkeypatch.setattr(duo, "REPO", str(root))
    (tmp_path / "main" / duo.GEN3_CLEAN_RR_ROM).write_bytes(b"rom")
    staged = []
    monkeypatch.setattr(gen3_fixtures, "stage_rom", lambda src: staged.append(src) or "staged_clean.gba")
    assert run._gen3_rom("b") == "staged_clean.gba"
    assert staged[0].endswith(duo.GEN3_CLEAN_RR_ROM)


def test_gen3_rr_battery_path_companion_kind_uses_the_pinned_saveram_name(tmp_path):
    run = duo.DuoRun.__new__(duo.DuoRun)
    run.gcfg, run.cfg = dict(duo.GAMES["gen3_rr"]), dict(duo.SCENARIOS["faint_cmd_gen3"])
    run._saveram_dir = lambda inst: str(tmp_path)
    assert os.path.basename(run._gen3_battery_path("a")) == "slink RR.SaveRAM"


def test_gen3_rr_battery_path_clean_kind_computes_the_saveram_name(monkeypatch, tmp_path):
    """No hand-transcribed saveram name for the clean side: it is derived from whatever
    `_gen3_rom` actually staged (gen3_fixtures.saveram_name), avoiding a transcription error."""
    run = duo.DuoRun.__new__(duo.DuoRun)
    run.gcfg, run.cfg = dict(duo.GAMES["gen3_rr"]), dict(duo.SCENARIOS["clean_rr_refused_gen3"])
    run._saveram_dir = lambda inst: str(tmp_path)
    monkeypatch.setattr(run, "_gen3_rom", lambda inst: "patch/build/gen3_Pokemon_-_Radical_Red.gba")
    path = run._gen3_battery_path("b")
    assert os.path.basename(path) == "gen3 Pokemon - Radical Red.SaveRAM"


# ── the driver files: symbols, paths and helpers they name exist ──────────────────────────
DRIVER = REPO / "lua" / "tests" / "duo" / "duo_gen3_main.lua"
SCRIPTED = REPO / "lua" / "tests" / "gen3_scripted_play.lua"


def test_every_scenario_has_its_module_and_runner_half():
    row = duo.GAMES["gen3_frlg"]
    for name in GEN3:
        base = name[:-len("_gen3")]
        assert (REPO / "lua" / "tests" / "duo" / f"scenario_{row['scenario_prefix']}{base}.lua").is_file(), name
        assert callable(getattr(duo.DuoRun, f"orchestrate_{name}", None)), name


def test_every_rr_only_scenario_has_its_module_oracle_and_runner_half():
    """The remaining RR-only carriers have real modules and oracles."""
    row = duo.GAMES["gen3_rr"]
    for name in GEN3_RR_ONLY:
        base = duo.SCENARIOS[name].get("scenario_module") or name[:-len("_gen3")]
        assert (REPO / "lua" / "tests" / "duo" / f"scenario_{row['scenario_prefix']}{base}.lua").is_file(), name
        assert callable(getattr(duo.DuoRun, f"orchestrate_{name}", None)), name
        oracle = duo.SCENARIOS[name]["oracle"]
        assert oracle == f"assert_{name}_saved" and callable(getattr(duo.DuoRun, oracle, None)), name
        assert duo.SCENARIOS[name]["games"] == ("gen3_rr",), name


# ── load-time pcall wrapping (card C4-LG2) ─────────────────────────────────────────────────
# A live RR duo (gen3_rr faint_cmd_gen3) died silently: SLINK_GEN3_TITLE="radical_red" made
# gen3_scripted_play.lua's dofile raise, the driver's first log line was its last, no RESULT was
# ever written, and e2e_duo.py's harness just waited out its 120s MYKEY timeout. Every load-time
# dofile in the driver's load section must now go through load_or_die, which pcalls and reports
# "load: <label>: <err>" through finish() (a real RESULT line) instead of letting the raise reach
# the top level uncaught.
_LOAD_OR_DIE_FN = re.compile(r"local function load_or_die\(.*?\nend\n", re.S)


def test_load_section_wraps_every_helper_dofile_in_load_or_die():
    text = DRIVER.read_text(encoding="utf-8")
    fn = _LOAD_OR_DIE_FN.search(text)
    assert fn, "no local function load_or_die(...) in the driver"
    assert "pcall(dofile" in fn.group(0) and 'finish(false, "load: "' in fn.group(0), fn.group(0)
    for rel, label in [("/lua/json_codec.lua", "json_codec.lua"),
                        ("/lua/tests/gen3_boot_check.lua", "gen3_boot_check.lua"),
                        ("/lua/tests/gen3_scripted_play.lua", "gen3_scripted_play.lua"),
                        ("/lua/gen3/reads.lua", "reads.lua")]:
        assert re.search(rf'load_or_die\("{re.escape(rel)}",\s*"{re.escape(label)}"\)', text), (
            f"{rel} is not loaded through load_or_die")
        # and NOT also reachable as a bare, unprotected dofile of the same path in that section.
        load_section = text[text.index("-- ── receipt, finish, console tee"):text.index("local play = SP.play")]
        assert f'dofile(ROOT .. "{rel}")' not in load_section.replace("load_or_die(", ""), rel


def test_load_or_die_reports_a_failure_through_finish_and_returns_a_success():
    """Runs the real load_or_die body under lupa with a stub dofile/finish -- not just a text
    match -- so the actual pcall/finish wiring is exercised, not merely its shape."""
    from lupa import LuaRuntime

    fn_src = _LOAD_OR_DIE_FN.search(DRIVER.read_text(encoding="utf-8"))
    assert fn_src

    lua = LuaRuntime(unpack_returned_tuples=True)
    load_or_die, calls = lua.execute(r"""
        local ROOT = "/repo"
        local calls = {}
        local function finish(ok, msg) calls[#calls + 1] = {ok, msg}; error("FINISHED_STUB", 0) end
        local function dofile(path)
            if path:find("bad", 1, true) then error("boom: " .. path, 0) end
            return { ok = true, path = path }
        end
    """ + fn_src.group(0) + "\n        return load_or_die, calls")

    # success: no finish() call, the loaded module comes back.
    ok_mod = load_or_die("/lua/good.lua", "good.lua")
    assert ok_mod["path"] == "/repo/lua/good.lua"
    assert len(calls) == 0

    # failure: finish(false, "load: <label>: <err>") is called, and the raise past it is caught
    # here exactly like duo_gen3_main.lua's own outer pcall(scenario, ctx) would catch it.
    ok, err = lua.globals().pcall(load_or_die, "/lua/bad.lua", "bad.lua")
    assert not ok
    assert len(calls) == 1
    passed, msg = calls[1][1], calls[1][2]
    assert passed is False
    assert msg.startswith("load: bad.lua: ") and "boom: /repo/lua/bad.lua" in msg


# ── load-time guarding beyond dofiles (card C4-GUARD) ──────────────────────────────────────
# C4-LG2 pcall-wrapped the load section's dofiles; the REST of that section could still raise out
# of the main chunk and hang the same way it fixed (no RESULT, the harness left waiting on MYKEY):
# an `assert` on a missing write_checkpoint.json / profile.json title, the .sym scan and its
# per-symbol asserts. The live witness is the save_then_write_gen3 red receipt (C4-STW-DIAG): both
# instances silent after connect and one EmuHawk left holding a .NET exception dialog. Every
# load-time read and symbol assert now runs inside guard(), which reports "load: <label>: <err>"
# through finish() -- the same discipline, and the same "load: " prefix, as load_or_die.
_LOAD_SECTION = re.compile(r"local function load_or_die\(.*?(?=\n-- ── seams teed)", re.S)
_GUARD_FN = re.compile(r"local function guard\(.*?\nend\n", re.S)
# The revision this card's falsifier is pinned to (never HEAD~n): the load section as it was when
# a missing checkpoint title / symbol / .sym died silently.
PRE_GUARD_REV = "8e9e7ba4"


def _driver_syms():
    text = DRIVER.read_text(encoding="utf-8")
    return re.findall(r'"(\w+)"', re.search(r"local SYMS = \{(.*?)\}", text, re.S).group(1))


def _lua_long(text):
    assert "]]" not in text, "the fake cannot carry a long bracket"
    return f"[[{text}]]"


def _sym_text(names):
    return "".join(f"{0x02000000 + 4 * i:08x} g {4 * i:08x} {n}\r\n" for i, n in enumerate(names))


def _run_load_section(title="firered", *, sym_names=None, sym_readable=True, checkpoint_title=True,
                      section_override=None):
    """Run the driver's REAL load section under lupa with fakes for everything it touches.

    Returns (messages, completed): the finish() messages the section produced -- empty when it ran
    to the end -- and whether it got there. The fakes are deliberately dumb (one module per dofile
    path, one blob per read path) because what is under test is the guard wiring, not the decoding.
    """
    from lupa import LuaRuntime

    section = section_override or _LOAD_SECTION.search(DRIVER.read_text(encoding="utf-8"))
    assert section, "no load section (load_or_die .. seams) in the driver"
    names = _driver_syms() if sym_names is None else sym_names
    sym_path = f"/repo/data/gen3/pret/poke{title}.sym"
    files = {
        "/repo/data/games/gen3_frlg/write_checkpoint.json": "<checkpoint>",
        "/repo/data/games/gen3_frlg/profile.json": "<profile>",
    }
    if sym_readable:
        files[sym_path] = _sym_text(names)
    entries = ",\n".join(f'  ["{p}"] = {_lua_long(t)}' for p, t in files.items())
    checkpoint_doc = f"{{ {title} = {{ pointers = {{}} }} }}" if checkpoint_title else "{}"
    prelude = f"""
local ROOT = "/repo"
captured = {{}}  -- global: the test reads it back after the pcall
local function finish(ok, msg)
    captured[#captured + 1] = tostring(msg)
    error("FINISHED_STUB", 0)
end
local function make_handle(text)
    local h = {{}}
    function h:read(_) return text end
    function h:close() end
    function h:lines()
        local pos = 1
        return function()
            if pos > #text then return nil end
            local nl = text:find("\\n", pos, true)
            local line
            if nl then line = text:sub(pos, nl - 1); pos = nl + 1
            else line = text:sub(pos); pos = #text + 1 end
            return (line:gsub("\\r$", ""))
        end
    end
    return h
end
local BAD_OPEN = {_lua_long("" if sym_readable else sym_path)}
local FILES = {{
{entries}
}}
local DECODED = {{
  ["<checkpoint>"] = {checkpoint_doc},
  ["<profile>"] = {{ titles = {{ {title} = {{}} }} }},
}}
local MODULES = {{
  ["/repo/lua/json_codec.lua"] = {{ decode = function(t) return DECODED[t] or {{}} end }},
  ["/repo/lua/tests/gen3_boot_check.lua"] = {{ title = "", budget = 0 }},
  ["/repo/lua/tests/gen3_scripted_play.lua"] = {{ play = {{}}, PROFILE_PACK_BY_TITLE = {{
      firered = "gen3_frlg", leafgreen = "gen3_frlg", radical_red = "gen3_rr", emerald = "gen3_emerald" }} }},
  ["/repo/lua/gen3/reads.lua"] = {{ new = function() return {{}} end }},
  ["/repo/lua/tests/gen3_title_syms.lua"] = {{ entries = {{}}, for_title = function() return {{}} end,
      emerald_engine = function(t) return t == "emerald" end,
      sym_path = function(root, t) return root .. "/data/gen3/pret/poke" .. t .. ".sym" end }},
}}
local function dofile(path)
    local m = MODULES[path]
    if not m then error("no fake module for " .. path, 0) end
    return m
end
local io = {{
    open = function(path, _)
        if path == BAD_OPEN or not FILES[path] then return nil, "No such file or directory" end
        return make_handle(FILES[path])
    end,
    lines = function(path)
        if path == BAD_OPEN or not FILES[path] then error("cannot open " .. path, 0) end
        return make_handle(FILES[path]):lines()
    end,
}}
local D = {{ title = {_lua_long(title)} }}
local memory = {{ read_u8 = function() return 0 end, read_u16_le = function() return 0 end,
                  read_u32_le = function() return 0 end }}
"""
    # The section is not a file: it is the middle of the driver, so it expects the chunk's own
    # locals (ROOT, D, io, memory, finish) to be in scope. Wrap prelude + section in ONE function
    # body, exactly like the real chunk, and keep `captured` global so the test can read it.
    section_src = section.group(0) if hasattr(section, "group") else section
    body = prelude + "\n" + section_src + "\nreturn true"
    lua = LuaRuntime(unpack_returned_tuples=True)
    runner = lua.eval("function()\n" + body + "\nend")
    ok, _ = lua.globals().pcall(runner)
    captured = lua.globals().captured
    return [captured[i] for i in range(1, len(captured) + 1)], bool(ok)


def test_load_section_guards_the_json_reads_the_sym_scan_and_the_symbol_asserts():
    text = DRIVER.read_text(encoding="utf-8")
    fn = _GUARD_FN.search(text)
    assert fn, "no local function guard(...) in the driver"
    assert "pcall(fn)" in fn.group(0) and 'finish(false, "load: "' in fn.group(0), fn.group(0)
    section = _LOAD_SECTION.search(text).group(0)
    assert re.search(r'guard\(\s*"checkpoint', section), "the checkpoint read is not guarded"
    assert re.search(r'guard\(\s*"profile', section), "the profile read is not guarded"
    assert re.search(r'guard\(\s*"pret symbols in ', section), "the .sym scan is not guarded"
    # the two shapes C4-STW-DIAG named as silently lethal are gone from the section.
    assert "io.lines(" not in section and "assert(S[" not in section, section
    # and the pre-MYKEY steps past the load section are guarded too.
    assert 'guard("boot to field' in text and 'guard("party read after boot' in text


def test_guard_reports_a_missing_checkpoint_title():
    msgs, done = _run_load_section(checkpoint_title=False)
    assert not done, "a missing checkpoint title must not let the section continue"
    assert len(msgs) == 1, msgs
    assert msgs[0].startswith("load: "), msgs
    assert "write_checkpoint.json" in msgs[0] and "firered" in msgs[0], msgs


def test_guard_reports_a_missing_pret_symbol():
    msgs, done = _run_load_section(sym_names=[n for n in _driver_syms() if n != "sSaveDialogCB"])
    assert not done, "a missing symbol must not let the section continue"
    assert len(msgs) == 1, msgs
    assert msgs[0].startswith("load: "), msgs
    assert "sSaveDialogCB" in msgs[0] and "pokefirered.sym" in msgs[0], msgs


def test_guard_reports_an_unreadable_sym():
    msgs, done = _run_load_section(sym_readable=False)
    assert not done, "an unreadable .sym must not let the section continue"
    assert len(msgs) == 1, msgs
    assert msgs[0].startswith("load: "), msgs
    assert "cannot read" in msgs[0] and "pokefirered.sym" in msgs[0], msgs


def test_a_clean_load_section_completes_without_a_finish():
    msgs, done = _run_load_section()
    assert done and msgs == [], msgs


def test_the_guard_is_what_ends_the_silent_hang():
    """Falsifier, pinned by sha to the revision before this card.

    The same fakes that make the guarded section report a named RESULT make the UNGUARDED section
    report nothing at all -- which is the live symptom (no RESULT, the harness left waiting on
    MYKEY, the driver's first log line its last) that the guard exists to end.
    """
    import subprocess

    proc = subprocess.run(["git", "show", f"{PRE_GUARD_REV}:lua/tests/duo/duo_gen3_main.lua"],
                          cwd=REPO, capture_output=True, text=True, encoding="utf-8",
                          errors="replace")
    if proc.returncode != 0 or "local function guard(" in proc.stdout:
        pytest.skip(f"{PRE_GUARD_REV}:duo_gen3_main.lua unavailable or already guarded")
    old = _LOAD_SECTION.search(proc.stdout)
    assert old, "the pre-card driver has no load section"

    msgs, done = _run_load_section(section_override=old.group(0), checkpoint_title=False)
    assert not done and msgs == [], f"the unguarded section reported something: {msgs}"
    msgs, done = _run_load_section(checkpoint_title=False)
    assert not done and len(msgs) == 1 and msgs[0].startswith("load: "), msgs


@pytest.mark.parametrize("title", ["firered", "leafgreen"])
def test_every_pret_symbol_the_driver_reads_exists(title):
    text = DRIVER.read_text(encoding="utf-8")
    names = re.findall(r'"(\w+)"', re.search(r"local SYMS = \{(.*?)\}", text, re.S).group(1))
    syms = {line.split()[-1] for line in (REPO / "data" / "gen3" / "pret" / f"poke{title}.sym")
            .read_text(encoding="utf-8").splitlines() if len(line.split()) == 4}
    assert names and not [n for n in names if n not in syms]


def test_the_scripted_play_exports_and_paths_the_driver_uses_exist():
    text = DRIVER.read_text(encoding="utf-8") + "".join(
        p.read_text(encoding="utf-8") for p in (REPO / "lua" / "tests" / "duo").glob("scenario_gen3_*.lua"))
    scripted = SCRIPTED.read_text(encoding="utf-8")
    exports = scripted[scripted.rindex("\nreturn {"):]
    for name in set(re.findall(r"\bSP\.(\w+)", text)):
        assert re.search(rf"\b{name}\s*=", exports), f"gen3_scripted_play does not export {name}"
    paths = scripted[scripted.index("local PATHS = {"):scripted.index("local H = {")]
    used = set(re.findall(r'(?:follow\(cp, |reversed\()"(\w+)"', text)) - set(
        re.findall(r'reversed\("\w+", "(\w+)"\)', text))   # built by the driver's reversed()
    for name in used:
        # E4: the Emerald paths are assigned in the Emerald block (PATHS.em_... = {)
        assert (re.search(rf"^\s+{name} = \{{", paths, re.M)
                or re.search(rf"^PATHS\.{name} = \{{", scripted, re.M)), f"no PATHS entry {name}"
    for dest in set(re.findall(r"SP\.DEST\.(\w+)", text)):
        assert re.search(rf"\b{dest} = \{{", scripted), f"no DEST {dest}"


# ── the scenario modules' control flow, under lupa with a fake ctx ─────────────────────────
# Not the game: every ctx primitive answers "it worked". What this catches is a Lua slip (a nil
# call, a misspelt ctx field, a branch that never returns) and the markers each half logs, which
# are exactly what the runner and the oracles read.
_FAKE_CTX = r"""
function FAKE(scenario, player, phase, spec)
    spec = spec or {}
    local logs = {}
    local party = { { slot = 0, key = "K0", hp = 5, max_hp = 20, species = 7, level = 9 },
                    { slot = 1, key = "K1", hp = 5, max_hp = 17, species = 16, level = 4 },
                    { slot = 2, key = "K9", hp = 9, max_hp = 9, species = 19, level = 3 } }
    local ctx = { player = player, phase = phase, fmt = string.format, cp = {}, finished = "done",
                  D = { wt = WT }, hp0_tag = "FORCED_HP0", title = spec.emerald and "emerald" or "firered",
                  emerald_engine = spec.emerald and true or false,
                  rr = spec.rr and true or false }
    -- E4c-2: an Emerald whiteout heals in C before the landing (overworld.c:357-366), so the
    -- Emerald party reads full; spec.unhealed leaves the lead short (the landing must refuse it)
    if spec.emerald then
        for _, m in ipairs(party) do m.hp = m.max_hp end
        if spec.unhealed then party[1].hp = party[1].max_hp - 1 end
    end
    -- C4-SAVE-ROWS: the pret statics a scenario reads through ctx.peek (SYMS addresses; the
    -- callbacks compare with the Thumb bit, as the real S table does)
    ctx.sym = { SaveDialogCB_AskSaveHandleInput = 0x0806F7F8, SaveDialogCB_ReturnSuccess = 0x0806F9E0,
                SaveDialogCB_AskOverwriteOrReplacePreviousFileHandleInput = 0x0806F8DC,
                LinkCB_RequestPlayerDataExchange = 0x0800A720 }
    -- the START menu after an in-game save (save_then_write): closed, the cursor left on SAVE
    -- (row 4 of the identity order), sSaveDialogCB stale on SaveDialogCB_ReturnSuccess
    local menu = { open = false, cb = "input", cursor = 4, dialog = 0x0806F9E1 }
    -- the flash save counter (G.save_counter): 4 + saves; spec.leg_saved models a menu-leg save
    -- that logged no DUMP line (SAVE_WITNESS_DUMP_SKIPPED/_FAIL)
    local flash, flaky = 4, 0
    local menu_tap
    -- released: a talk's script closed (mash_until); save_then_write starts on an idle field
    local probes, released = 0, scenario == "save_then_write"
    -- G5-RR-NURSE-2: the RR START menu model (whiteout's nurse_control RR branch) -- rr_menu_open
    -- is sStartMenuWindowId's witness (peek_u8 0x0203ABE0 ~= 0xFF); spec.swallow_start taps are
    -- dropped before it opens (default 0: the first tap opens it); spec.other_lock models a
    -- field lock from something else entirely, window id still 0xFF, once Start has been tapped
    local rr_menu_open, start_taps = false, 0
    ctx.log = function(s)
        logs[#logs + 1] = tostring(s)
        local live = tostring(s):match("^CONTROL_LIVE (%S+)")
        if live then
            probes = probes + 1
            local probe = ({ cable_welcome_message = "box_mon", nurse = "box_mon",
                             union_room_attendant = "party_mon", start_menu = "box_mon" })[live]
            if probe then logs[#logs + 1] = "RX " .. probe .. " key=" .. (spec.linked or "K1") end
        end
        if live == "dialog_witness" then logs[#logs + 1] = "RX party_mon key=" .. (spec.linked or "K1") end
        if tostring(s):find("^WRITE_PROBE_READY") then
            probes = probes + 1
            logs[#logs + 1] = "RX box_mon key=" .. (spec.linked or "K1")
        end
    end
    ctx.frames = function() end
    local watchers, write_armed, teala = {}, nil, spec.teala or 1
    local DIR = { Down = 1, Up = 2, Left = 3, Right = 4 }
    local facing, walking = 2, false
    ctx.facing = function() return facing end
    ctx.player_idle = function() if walking then walking = false; return false end return true end
    local function run_watchers()
        for i = #watchers, 1, -1 do if watchers[i]() then table.remove(watchers, i) end end
    end
    ctx.watch = function(fn) watchers[#watchers + 1] = fn end
    ctx.wait_until = function(pred) run_watchers(); return pred() end
    ctx.mash_until = function(pred)
        local v = pred()
        if v then return v end
        released = true                        -- the mashed A closes the open script
        if scenario == "center_controls" and teala == 1 then
            teala = 2                          -- CableClub_EventScript_Tutorial walked (2,6)->(2,4)
            here_at(5, 5, 2, 4)
        end
        return pred()
    end
    -- wait_go(marker, secs): a non-string marker is a caller bug (wait_go(300) waited for a
    -- line reading "300"), so the fake refuses it instead of answering true (finding 7).
    ctx.wait_go = function(marker, secs)
        if marker ~= nil and type(marker) ~= "string" then
            error("wait_go(marker, secs): marker must be a string or nil, got " .. type(marker), 0)
        end
        if secs ~= nil and type(secs) ~= "number" then
            error("wait_go(marker, secs): secs must be a number or nil", 0)
        end
        return true
    end
    ctx.go_has = function() return true end
    ctx.linked = function() return spec.linked or "K1" end
    ctx.partner_done = function() return spec.partner_done ~= false end
    ctx.partner_result = function() return spec.partner end
    ctx.party = function() return party end
    -- gone: a deposit leaves the party until the withdraw; boxed: where the fake PC holds it;
    -- used: gBattleResults.lastUsedMovePlayer; writes: the armed-sink write count
    local gone, boxed, used, writes = {}, {}, 0, spec.writes or 0
    if spec.gone then gone[spec.gone] = true end     -- a record the server moved out (quarantine)
    ctx.find = function(k) for _, m in ipairs(party) do if m.key == k and not gone[k] then return m end end end
    -- clean_rr_refused's A: spec.drift_writes / spec.drift_rx land inside the 600-frame settle window
    local rx_extra, base_frames = 0, ctx.frames
    ctx.frames = function(n)
        if n == 600 then writes = writes + (spec.drift_writes or 0); rx_extra = rx_extra + (spec.drift_rx or 0) end
        return base_frames(n)
    end
    ctx.rx_count = function() return rx_extra end
    ctx.sent = function(event)
        if event == "box_mon_failed" or event == "sync_retrieve_failed" then
            return spec.failed == event and 1 or 0
        end
        if spec.unsent and spec.unsent:find(event, 1, true) then return 0 end
        return spec.sent or 1
    end
    ctx.received = function(cmd)
        if cmd == "force_faint" and player == "a" then return 0 end
        if player == "a" and cmd == "box_mon" then return 1 + probes end
        if player == "a" and cmd == "party_mon" then return 1 + probes end
        return 1
    end
    ctx.wait_sent = function(event, key)
        if event == "stats_cache" and not spec.noop_deposit then gone[key] = true; boxed[key] = true end
        if event == "sync_retrieve_done" then gone[key] = nil; boxed[key] = nil end
        -- live r9: after the cancelled cable link the released probe never landed
        if spec.stale and event == "stats_cache" then return false end
        -- today's pack: the probe stays held on save_dialog_cb after the save
        if spec.held and event == "stats_cache" then return false end
        -- row 1's mirror: the released party_mon never lands once the field is free
        if spec.never_lands and event == "sync_retrieve_done" then return false end
        if scenario == "save_then_write" and event == "sync_retrieve_done" and write_armed then
            local w = write_armed; write_armed = nil; w()          -- the landing frame's write
        end
        logs[#logs + 1] = "TX " .. event .. " " .. tostring(key) .. " {}"
        return true
    end
    ctx.wait_received = function(cmd)
        if cmd == "force_explode" and spec.executes ~= false then used = 153 end
        -- the companion's native stage writes; the clean side writes only what spec.writes says
        if cmd == "apply_prepare" then writes = writes + (player == "a" and 3 or (spec.writes or 0)) end
        return spec.received ~= false
    end
    ctx.last_sent = function(event)
        if event == "rival_team_replaced" then return spec.rival_reply or { error = "stale_battle_id" } end
        if event == "apply_ready" then
            if spec.ready_ok ~= nil then return { ok = spec.ready_ok } end
            return { ok = player == "a" }
        end
        return { area_id = "route_1", species_id = 16 }
    end
    ctx.await_turn = function() return spec.turn or "action" end
    -- boxes: a key the client deposited is boxed unless spec.noop_deposit models a deposit that
    -- ACKed without moving the record (Codex C4-6b finding 2's falsifier)
    ctx.boxed = function(k) return boxed[k] and "0:0" or nil end
    ctx.observe_boxed = function(k)
        if ctx.find(k) or not boxed[k] then return nil end
        logs[#logs + 1] = "BOXED_OBSERVED " .. k .. " box=0:0"
        return "0:0"
    end
    ctx.observe_returned = function(k)
        local m = ctx.find(k)
        if not m or boxed[k] then return nil end
        logs[#logs + 1] = "RETURNED_OBSERVED " .. k .. " slot=" .. m.slot
        return m
    end
    ctx.last_used_move_player = function() return used end
    ctx.battle_outcome = function() return 1 end
    ctx.trade_phase = function() return spec.trade_phase or "scene" end
    ctx.hp0 = function() return spec.hp0 end
    ctx.battle_hold = function() return { why = "active battler" } end
    local saves = 0
    ctx.save = function()
        saves = saves + 1
        menu.open, menu.cb, menu.cursor, menu.dialog = false, "input", 4, 0x0806F9E1
        flash = 4 + saves
        logs[#logs + 1] = string.format("SAVE_WITNESS_DUMP path=p bytes=131072 saves=%d frame=1 counter=%d",
                                        saves, 4 + saves)
        return true
    end
    ctx.catch = function()
        if spec.catch_why then return nil, spec.catch_why end
        return "K9"
    end
    ctx.hunt = function() return true end
    ctx.run_away = function() return true end
    ctx.lose_active = function() return true end
    ctx.switch_to = function() return true end
    ctx.in_battle = function() return false end
    ctx.battler_slot = function() return 0 end
    ctx.walk_to_pc = function() end
    ctx.walk_pc_to_grass = function() end
    ctx.pc_deposit = function() local k = spec.linked or "K1"; gone[k] = true; boxed[k] = true; return k end
    ctx.pc_withdraw = function() local k = spec.linked or "K1"; gone[k] = nil; boxed[k] = nil; return k end
    ctx.try = function(fn, ...)
        local r = table.pack(pcall(fn, ...))
        if scenario == "whiteout" and player == "a" then
            run_watchers()                     -- the landing, while the heal script still runs
            if write_armed then local w = write_armed; write_armed = nil; w() end  -- the rebuild
        end
        return table.unpack(r, 1, r.n)
    end
    ctx.writes = function() return writes end
    ctx.wrong_save_hud = function() return true end
    -- E4c-2: Emerald lands on lastHealLocation Oldale 0.10 (6,17), outdoors (the model's name
    -- stays CENTER: it is the whiteout destination every branch below compares against)
    local CENTER = spec.emerald and { group = 0, num = 10, x = 6, y = 17 } or { group = 5, num = 4, x = 7, y = 4 }
    local function snap(at, bad)
        return { group = at.group, num = at.num, x = at.x, y = at.y, frame = 7, bad = bad or {},
                 ptrs = { gSaveBlock1Ptr = 0x02025000 } }
    end
    local here = snap(CENTER)
    function here_at(g, n, x, y) here = snap({ group = g, num = n, x = x, y = y }) end
    ctx.SP = { verify_fight_cursor = function() return "fight" end,
               whiteout_destination = function() return CENTER end, warp_to = function() end,
               -- Emerald's START-menu witness (EMH.menu_ready), the same open flag as RR's
               EMH = { menu_ready = function() return rr_menu_open end } }
    -- a script is live from a talk (A tap) until A mashes it closed (mash_until)
    ctx.press = ctx.press or function() end
    ctx.peek_u8 = function(addr)
        if addr == 0x0203ABE0 then return rr_menu_open and 1 or 0xFF end
        if addr == 0x0203FE98 then return spec.producer_phase or 2 end    -- RR TRADE_BASE + 0x48
        if addr >= 0x03005390 and addr < 0x03005394 then return addr == 0x03005390 and 4 or 0 end
        error("fake ctx.peek_u8: no address " .. tostring(addr))
    end
    ctx.G = { map = function() return here.group, here.num end, pos = function() return here.x, here.y end,
              pred_ok = function(_, name)
                  if (ctx.rr or ctx.title == "emerald") and name == "field_controls_locked" then
                      return not (rr_menu_open or (spec.other_lock and start_taps > 0))
                  end
                  if name == "field_controls_locked" and (menu.open or menu.stuck) then return false end
                  return released
              end,
              flash_domain = function() return "Flash" end,
              save_counter = function() return flash end,
              start_menu_witness = function()
                  return function() return menu.open and menu.cb == "input" end,
                         function()
                             -- spec.old_witness: the pre-10e4a702 shape, the stale pointer alone
                             if spec.old_witness then return menu.dialog ~= 0 end
                             return menu.open and menu.cb == "save"
                         end
              end,
              tap = function(btn)
                  if scenario == "save_then_write" then return menu_tap(btn) end
                  if btn == "Start" and (ctx.rr or ctx.title == "emerald") then
                      if rr_menu_open then error("test: Start tapped again after the START menu already opened", 0) end
                      start_taps = start_taps + 1
                      if start_taps > (spec.swallow_start or 0) then rr_menu_open = true end
                      return
                  end
                  if btn == "A" then
                      -- an A that faces no attendant talks to nobody (center_controls' counters)
                      if scenario == "center_controls" and facing ~= 2 then return end
                      released = false
                  elseif DIR[btn] then
                      if walking then walking = false else facing = DIR[btn] end  -- dropped mid-step
                  end
              end }
    ctx.center_state = function()
        local missing = spec.ur_missing and { spec.ur_missing } or {}
        return string.format("map=%d.%d at=(%d,%d) frame=7 tasks=[] preds=[]", here.group, here.num,
                             here.x, here.y), missing, here
    end
    -- the write fires once, at its moment (inside ctx.try for whiteout A); a hook armed after
    -- it never sees it -- HEAD's order, which the live gen3_lgfr r6 run exposed
    ctx.on_write = function(_, fn)
        if scenario == "save_then_write" then
            local line = "[SLink-gen3] write overworld 0x020242E8 +100 frame 5626"
            if spec.write_locked then fn(line) else write_armed = function() fn(line) end end
            return
        end
        local function fire()
            if spec.write_at == "outside" then here = snap({ group = 3, num = 1, x = 26, y = 27 }) end
            if spec.off_checkpoint then here = snap(CENTER, { "cpu" }) end
            fn()
            here = snap(CENTER)
        end
        if scenario == "whiteout" and player == "a" then write_armed = fire else fire() end
    end
    ctx.game_var = function() return teala end
    ctx.write_hook_errors = function() return {} end
    ctx.party_base = function() return 0x02024284 end
    -- the rebuild's keyed record: K1 reads back in slot 1 -> party_base + 100
    ctx.write_lines = function()
        if spec.unkeyed then return { { reason = "overworld", address = 0x02030CFC, len = 80, frame = 7 } } end
        return { { reason = "overworld", address = 0x02024284 + 100, len = 100, frame = 7 } }
    end
    ctx.locate = function() return { party = 1, box = false } end
    ctx.on_field = function() return true end
    local saved = false
    ctx.task_live = function(name)
        if name == "Task_MultichoiceMenu_HandleInput" then return false end   -- still at the \p
        if name == "Task_StartMenuHandleInput" then return menu.open end
        if name == "task50_save_game" and spec.no_save_task then return false end
        if name == "Task_LinkupAwaitConnection" and not saved then
            saved = true                       -- the Cable Club save precedes the link wait
            logs[#logs + 1] = "SAVE_WITNESS_DUMP path=p bytes=1 saves=1 frame=1 counter=5"
        end
        return true
    end
    ctx.script_at = function(label)
        if spec.no_witness then return nil end
        -- live r8: without A the Direct Corner script waits at the welcome text's \p, so it
        -- is never inside SelectCableClubRoom; the Union Room msgbox caller is on the stack
        if label == "CableClub_EventScript_WelcomeToCableClub" then return "scriptPtr" end
        if label == "CableClub_EventScript_UnionRoomAdapterNotConnected" then return "stack[0]" end
        return nil
    end
    ctx.special_result = function() return spec.var_result or 0 end
    ctx.stale_predicates = function()
        if scenario == "save_then_write" and not spec.no_stale then
            return { "save_dialog_cb=0x0806F9E1:SaveDialogCB_ReturnSuccess(no save dialog task)" }
        end
        if spec.stale then
            return { "link_callback=0x0800A721:LinkCB_RequestPlayerDataExchange(sLinkOpen=0)",
                     "save_dialog_cb=0x0806F9E1:SaveDialogCB_ReturnSuccess(no save dialog task)" }
        end
        return {}
    end
    -- the deferred queue the REAL ctx.queued/ctx.hold_probe read (HOLD_SRC, from the driver);
    -- spec.queue = "wrong" is Codex's case: the right reason, but only an unrelated party_mon
    local key = spec.linked or "K1"
    local items = spec.queue == "wrong" and { { cmd = "party_mon", key = "K7" } }
                  or { { cmd = "box_mon", key = key }, { cmd = "party_mon", key = key } }
    local why = spec.why or "...nk/.claude/worktrees/gen3-lane-clean/lua/gen3/safety.lua:73: "
                            .. "forbidden state: field_controls_locked"
    local attempted = 0
    local session = { deferred = { items = items, exec = { write_count = function() return attempted end } } }
    -- the START menu model (pret start_menu.c): Start opens it on StartCB_HandleInput; Up/Down move
    -- the cursor; A on SAVE runs the dialog to the save yes/no, A there to the overwrite yes/no;
    -- B at a yes/no cancels back to the menu (:589-594), B at the menu closes it (:436-441)
    local SAVE_ASK, OVERWRITE = 0x0806F7F9, 0x0806F8DD
    menu_tap = function(btn)
        if btn == "Start" and not menu.open then menu.open, menu.cb = true, "input"
        elseif not menu.open then return
        elseif menu.cb == "input" and (btn == "Up" or btn == "Down") then
            menu.cursor = menu.cursor + (btn == "Up" and -1 or 1)
        elseif menu.cb == "input" and btn == "A" and menu.cursor == 4 then menu.cb, menu.dialog = "save", SAVE_ASK
        elseif menu.cb == "save" and btn == "A" and menu.dialog == SAVE_ASK then
            menu.dialog = OVERWRITE
            -- spec.prompt_admits: the checkpoint admits at the prompt, so the queued probe executes
            if spec.prompt_admits then
                for i = #items, 1, -1 do if items[i].cmd == "party_mon" then table.remove(items, i) end end
            end
        elseif menu.cb == "save" and btn == "B" and menu.dialog == OVERWRITE then
            menu.cb = "input"
            if spec.leg_saved then flash = flash + 1 end
        elseif menu.cb == "input" and btn == "B" then menu.open, menu.stuck = false, spec.stuck_locked
        end
    end
    ctx.peek = function(name, _, offset)
        if name == "sSaveDialogDelay" then return spec.delay or 23 end
        if name == "sSaveDialogCB" then return menu.dialog end
        if name == "sStartMenuCursorPos" then return menu.cursor end
        if name == "sNumStartMenuItems" then return 7 end
        if name == "sStartMenuOrder" then return offset or 0 end
        if name == "gDifferentSaveFile" then return 0 end
        -- the no-partner link wait (link.c OpenLink :390-395): open, callback set, never run
        if name == "sLinkOpen" then
            if spec.link_flaky then flaky = flaky + 1; return flaky % 2 end   -- open on every other frame
            return spec.link_closed and 0 or 1
        end
        if name == "gLinkCallback" then return spec.null_cb and 0 or spec.odd_cb and 0x12345679 or 0x0800A721 end
        error("fake ctx.peek: no " .. tostring(name))
    end
    function session.deferred:pending() return #self.items, why, 0, self.items[1] and self.items[1].cmd end
    ctx.deferred_hold = function()                      -- HEAD d199da32's accessor (why, head)
        local _, w, _, head = session.deferred:pending()
        if #items > 0 then return w, head end
    end
    local calls = 0
    ctx.mutable_bytes = function()
        calls = calls + 1
        if spec.bytes_change and calls > 1 then return "P2", "B" end
        if spec.attempted_change then attempted = attempted + 1 end
        return "P", "B"
    end
    if HOLD_SRC then
        local env = setmetatable({ ctx = ctx, fmt = string.format, G = ctx.G, cp = ctx.cp, session = session,
                                   emu = { framecount = function() return 0 end } }, { __index = _G })
        assert(load(HOLD_SRC, "hold_probe", "t", env))()
    end
    ctx.play = { fight_through = function() return true end, wait_scene_settled = function() return true end,
                 where = function() return "here" end,
                 follow = function(_, name)
                     logs[#logs + 1] = "FOLLOW " .. name
                     local last = ({ center2f_to_direct_corner = "Right", center2f_counter_to_direct_corner = "Right",
                                     center2f_direct_corner_to_union_room = "Left" })[name]
                     if last then facing, walking = DIR[last], true end   -- the last step still walking
                 end }
    local fn = dofile(SCENARIO_DIR .. "/scenario_gen3_" .. scenario .. ".lua")
    local ok, pass, msg = pcall(fn, ctx)
    return ok, pass, tostring(ok and msg or pass), table.concat(logs, "\n")
end
"""


@pytest.fixture(scope="module")
def lua():
    from lupa import LuaRuntime

    runtime = LuaRuntime(unpack_returned_tuples=True)
    runtime.globals().SCENARIO_DIR = str(REPO / "lua" / "tests" / "duo").replace("\\", "/")
    # the driver's REAL keyed-probe check, when this cut has one (HEAD d199da32 does not)
    text = DRIVER.read_text(encoding="utf-8")
    defs = [re.search(_LUA_DEF.format(re.escape(name)), text, re.M | re.S)
            for name in ("queued_entry", "ctx.queued", "ctx.hold_probe")]
    defs.append(re.search(r"^function ctx\.attempted\(\) .* end$", text, re.M))
    # the REAL turn-and-prove (C4-6p), when this cut has one; the fake supplies facing/idle
    face = [re.search(r"^local FACING = .*$", text, re.M),
            re.search(_LUA_DEF.format(re.escape("ctx.face")), text, re.M | re.S)]
    if all(face):
        defs += face
    if all(defs):
        runtime.globals().HOLD_SRC = "\n".join(d.group(0) for d in defs)
    runtime.globals().WT = str(REPO).replace(chr(92), "/")
    runtime.execute(_FAKE_CTX)
    return runtime


def _run_module(lua, scenario, player, phase, spec):
    """spec values: str -> Lua string, "lua:<expr>" -> that Lua expression, int -> number."""
    fields = []
    for key, value in spec.items():
        if isinstance(value, str) and value.startswith("lua:"):
            fields.append(f"{key} = {value[4:]}")
        elif isinstance(value, str):
            fields.append(f'{key} = "{value}"')
        else:
            fields.append(f"{key} = {value}")
    return lua.globals().FAKE(scenario, player, phase, lua.eval("{" + ", ".join(fields) + "}"))


@pytest.mark.parametrize("scenario, player, phase, spec, markers", [
    ("faint_cmd", "a", "initial", {}, ["READY K1"]),
    ("faint_cmd", "b", "initial", {"hp0": "lua:{frame=1,in_battle=false}"}, ["READY K1"]),
    ("linked_faint_active", "a", "initial", {"linked": "K0"}, ["LINKED_FAINTED K0"]),
    # linked_faint_active's B (the P+H subject) runs on its own engine model: _PH_MODEL below
    ("boxsync", "a", "initial", {}, ["BOXED_OBSERVED K1", "DEPOSITED K1", "RETURNED_OBSERVED K1",
                                     "WITHDRAWN K1"]),
    ("boxsync", "b", "initial", {}, ["BOXED_OBSERVED K1", "MIRROR_DEPOSITED K1",
                                     "RETURNED_OBSERVED K1", "MIRROR_WITHDRAWN K1"]),
    ("whiteout", "a", "initial", {}, ["BOXED_OBSERVED K1", "DEPOSITED_FOR_REBUILD K1",
                                      "CENTER_STATE map=5.4 at=(7,4)", "WHITED_OUT at here",
                                      "WRITE_IN_CENTER map=5.4 at=(7,4)",
                                      "CONTROL_LIVE nurse K1 map=5.4",
                                      "CONTROL_REFUSED nurse box_mon K1 clause=field_controls_locked"]),
    # G5-RR-NURSE-2 (OMP cx-6c92f636 F1/F2): RR's nurse never refuses, so the control is the
    # START menu, named start_menu; the menu opens on the first tap by default (swallow_start=0)
    ("whiteout", "a", "initial", {"rr": "lua:true"},
     ["CONTROL_LIVE start_menu K1 map=5.4",
      "CONTROL_REFUSED start_menu box_mon K1 clause=field_controls_locked"]),
    # E4c-2: Emerald lands healed outdoors at Oldale 0.10 (6,17): LANDING_STATE / WRITE_AT_LANDING
    # and the START-menu control with Emerald's own menu witness
    ("whiteout", "a", "initial", {"emerald": "lua:true"},
     ["CONTROL_LIVE start_menu K1 map=0.10", "LANDING_STATE map=0.10 at=(6,17)",
      "healed=K0=20/20,K1=17/17,K9=9/9", "WRITE_AT_LANDING map=0.10 at=(6,17)",
      "CONTROL_REFUSED start_menu box_mon K1 clause=field_controls_locked"]),
    # the first two Start taps swallowed (the field only just freed), the menu opens on the third
    ("whiteout", "a", "initial", {"rr": "lua:true", "swallow_start": 2},
     ["CONTROL_LIVE start_menu K1 map=5.4",
      "CONTROL_REFUSED start_menu box_mon K1 clause=field_controls_locked"]),
    ("center_controls", "a", "initial", {}, ["CONTROL_LIVE cable_welcome_message K1 ",
                                             "CONTROL_REFUSED cable_welcome_message box_mon K1 clause=",
                                             "CONTROL_REFUSED cable_link box_mon K1 clause=",
                                             "CONTROL_RELEASED cable_link box_mon K1",
                                             "CONTROL_REFUSED union_room_attendant party_mon K1 clause=",
                                             "CONTROL_RELEASED union_room_attendant party_mon K1"]),
    ("center_controls", "b", "initial", {}, []),
    ("whiteout", "b", "initial", {}, ["BOXED_OBSERVED K1", "DEPOSITED_FOR_REBUILD K1",
                                      "RETURNED_OBSERVED K1", "MIRROR_WITHDRAWN K1"]),
    ("link", "a", "initial", {}, ["CAUGHT K9", "RETURNED_OBSERVED K9 slot=2"]),
    ("deadzone", "a", "initial", {}, ["NO_CATCH area=route_1 species=16"]),
    ("deadzone", "b", "initial", {"hp0": "lua:{frame=1,in_battle=false}"}, ["CAUGHT K9", "RETIRED K9"]),
    ("reconnect", "b", "initial", {}, ["RECONNECT_READY b"]),
    ("reconnect", "a", "same_save", {}, ["RECONNECT_HELLO same_save count=1"]),
    ("reconnect", "a", "wrong_save", {}, ["RECONNECT_HELLO wrong_save count=1",
                                          "WRONG_SAVE_ZERO attempted=0 writes=0 party=unchanged "
                                          "box=unchanged"]),
    # explode runs on the P+H model (_PH_MODEL, case "explode")
    ("rival_swap", "b", "initial", {}, ["READY_IN_BATTLE"]),
    ("rival_swap", "a", "initial", {}, []),
    ("clean_rr_refused", "a", "initial", {}, ["PROBE_SETTLED writes=0 rx=0"]),
])
def test_scenario_modules_run_their_happy_path(lua, scenario, player, phase, spec, markers):
    ok, passed, msg, logs = _run_module(lua, scenario, player, phase, spec)
    assert ok, msg
    assert passed is True, msg
    for marker in markers:
        assert marker in logs, (marker, logs)


@pytest.mark.parametrize("scenario, player, phase, spec, reason", [
    ("faint_cmd", "b", "initial", {}, "never took K1 to HP 0"),
    ("reconnect", "a", "wrong_save", {"writes": 2}, "wrote 2 time(s)"),
    # C4-6n (Codex receipt audit): zero ATTEMPTED bytes and unchanged live RAM, not only zero
    # completed write-log lines
    ("reconnect", "a", "wrong_save", {"attempted_change": "lua:true"}, "ATTEMPTED 1 byte(s)"),
    ("reconnect", "a", "wrong_save", {"bytes_change": "lua:true"}, "live party/PC RAM changed"),
    ("reconnect", "a", "initial", {}, "the runner never killed A"),
    ("rival_swap", "b", "initial", {"turn": "party"}, "never reached the action menu"),
    ("rival_swap", "b", "initial", {"rival_reply": "lua:{error='window_closed'}"}, "expected error=stale_battle_id"),
    ("clean_rr_refused", "a", "initial", {"drift_writes": 2}, "wrote 2 time(s)"),
    ("clean_rr_refused", "a", "initial", {"drift_rx": 1}, "received 1 command(s)"),
    ("clean_rr_refused", "b", "initial", {}, "refusal-proof side"),
    # finding 2's falsifier: the mirrored deposit ACKed (stats_cache) but moved nothing
    ("whiteout", "b", "initial", {"noop_deposit": "lua:true"}, "was never read back boxed"),
    ("boxsync", "b", "initial", {"noop_deposit": "lua:true"}, "was never read back boxed"),
    # G5-RR-NURSE-2 F1's falsifier: a field lock from something else (window id stays 0xFF) must
    # not be mistaken for the START menu opening -- pre-fix (locked() alone) this wrongly passed
    ("whiteout", "a", "initial",
     {"rr": "lua:true", "other_lock": "lua:true", "swallow_start": 99},
     "the START menu never opened"),
])
def test_scenario_modules_fail_with_a_named_reason(lua, scenario, player, phase, spec, reason):
    ok, passed, msg, _ = _run_module(lua, scenario, player, phase, spec)
    assert ok and passed is False and reason in msg, msg

# ── Codex C4-6b: the falsifiers, one block per finding ────────────────────────────────────
# finding 1: RR saves decode as RR everywhere an oracle reads them
def _rr_run(scenario="faint_cmd_gen3"):
    run = duo.DuoRun.__new__(duo.DuoRun)
    run.scenario, run.game = scenario, "gen3_rr"
    run.cfg, run.gcfg = dict(duo.SCENARIOS[scenario]), dict(duo.GAMES["gen3_rr"])
    return run


def test_rr_rows_decode_saves_with_the_rr_layout(monkeypatch):
    image = _rr_saved(_rr_fixture(), 3)
    run = _rr_run()
    monkeypatch.setattr(run, "_gen3_flushed", lambda inst: image)
    monkeypatch.setattr(run, "_gen3_fixture_bytes", lambda inst: image)
    party, _ = run._gen3_saved("a")
    assert [duo.gen3_key(m) for m in party] == [f"{RR_MON[1]:08X}:{RR_MON[2]:08X}"]
    assert party[0]["species"] == RR_MON[0] and party[0]["checksum_ok"] is None
    assert run._gen3_fixture_saved("a")[0][0]["checksum_ok"] is None


def test_record_validity_is_the_cartridges_own():
    """Vanilla demands the secure checksum; RR (CFRU, no checksum) must not be failed for the
    None it decodes with, nor pass a record decoded with a vanilla verdict. Level and species
    are bounded by the title (Codex C4-6c finding 5): RR's MAX_LEVEL is 250, FR's 100; RR's
    species are rr_species.json's ids, FR's 1..411."""
    rr_limits = duo.gen3_limits("radical_red", _rom_with_pp({}, "radical_red"))
    rr_mon = {"checksum_ok": None, "has_species": 1, "is_bad_egg": 0, "species": 277,
              "level": 5, "moves": [0, 0, 0, 0], "pp": [0, 0, 0, 0], "pp_bonuses": 0}
    assert duo.gen3_record_problems("x", rr_mon, rr=True, limits=rr_limits) == []
    assert duo.gen3_record_problems("x", rr_mon, rr=False, limits=LIMITS)   # the old "is not True" rule
    assert duo.gen3_record_problems("x", dict(rr_mon, checksum_ok=True), rr=True, limits=rr_limits)
    assert duo.gen3_record_problems("x", dict(rr_mon, has_species=0), rr=True, limits=rr_limits)
    assert duo.gen3_record_problems("x", dict(rr_mon, level=0), rr=True, limits=rr_limits)
    # RR's own level cap, not vanilla's
    assert duo.gen3_record_problems("x", dict(rr_mon, level=200), rr=True, limits=rr_limits) == []
    assert duo.gen3_record_problems("x", dict(rr_mon, level=251), rr=True, limits=rr_limits)
    fr = dict(rr_mon, checksum_ok=True, species=16)
    assert duo.gen3_record_problems("x", dict(fr, level=101), limits=LIMITS)
    # species: a real RR id beyond vanilla's range passes on RR, an id RR never defines does not
    with open(duo.GEN3_RR_SPECIES, encoding="utf-8") as handle:
        rr_ids = sorted(int(k) for k in json.load(handle))
    missing = next(i for i in range(1, rr_ids[-1]) if i not in set(rr_ids))
    assert duo.gen3_record_problems("x", dict(rr_mon, species=rr_ids[-1]), rr=True, limits=rr_limits) == []
    assert duo.gen3_record_problems("x", dict(rr_mon, species=missing), rr=True, limits=rr_limits)
    assert duo.gen3_record_problems("x", dict(fr, species=412), limits=LIMITS)
    assert duo.gen3_record_problems("x", dict(fr, species=411), limits=LIMITS) == []
    # no move table: the PP bound cannot be checked, which is a problem, not a pass
    assert any("no move table" in p for p in duo.gen3_record_problems("x", fr))


def _rr_with_balls(count):
    """An RR image whose EWRAM bag (ram.BALL_POCKET_ADDR, inside the sectors 30-31 extension)
    holds `count` Poke Balls in plaintext."""
    image = bytearray(_rr_saved(_rr_fixture(), 3))
    with open(duo.GEN3_RR_PROFILE, encoding="utf-8") as handle:
        addr = json.load(handle)["titles"]["radical_red"]["ram"]["BALL_POCKET_ADDR"]
    off = addr - codec.RR_EXT_ADDR
    sector, within = codec.RR_EXT_SECTORS[off // codec.CHUNK_SIZE_CFRU], off % codec.CHUNK_SIZE_CFRU
    at = sector * codec.SECTOR_SIZE + within
    image[at:at + 4] = (4).to_bytes(2, "little") + count.to_bytes(2, "little")
    return bytes(image)


def test_rr_ball_count_reads_the_plaintext_ewram_bag():
    assert duo.gen3_ball_count(_rr_with_balls(7), "radical_red") == 7
    assert duo.gen3_ball_count(_rr_with_balls(0), "radical_red") == 0


def test_rr_wrong_save_is_parsed_with_the_rr_layout(monkeypatch, tmp_path):
    image = _rr_saved(_rr_fixture(), 3)
    path = tmp_path / "wrong.sav"
    path.write_bytes(image)
    run = _rr_run("reconnect_gen3")
    monkeypatch.setattr(run, "_gen3_fixture_bytes", lambda inst: image)
    # the same trainer id: refused by the TRAINER rule, not by a vanilla qualify of an RR image
    with pytest.raises(RuntimeError, match="carries A's own trainer id"):
        run._gen3_wrong_save(str(path))


# finding 3: record integrity -- checksum and every invariant field, an explicit whitelist
def test_round_trip_refuses_a_flipped_checksum_bit(pair):
    fixture, _ = pair
    flipped = _saved(fixture, 3, [STARTER, dict(PIDGEY, flip_checksum=True)])
    problems = _rt("a", _decoded(flipped), _decoded(fixture), _key(PIDGEY))
    assert any("secure checksum fails" in p for p in problems), problems


@pytest.mark.parametrize("field, value", [
    ("moves", [33, 0, 0, 0]),
    ("ivs", dict(PIDGEY["ivs"], speed=31)),
    ("held_item", 13),
    ("nickname", "OTHER"),
    ("ot_name", "BLUE"),
    ("ability_num", 1),
    ("evs", dict(PIDGEY["evs"], attack=4)),
    ("met_location", 1),
])
def test_round_trip_names_every_changed_invariant(pair, field, value):
    fixture, _ = pair
    changed = _saved(fixture, 3, [STARTER, dict(PIDGEY, **{field: value})])
    problems = _rt("a", _decoded(changed), _decoded(fixture), _key(PIDGEY))
    assert any(f"('{field}'," in p for p in problems), problems


@pytest.mark.parametrize("field, value, message", [
    ("hp", 65535, "hp 65535/20"),                      # Codex's repro, one field at a time
    ("pp", [255, 255, 255, 255], "holds 255 PP"),
    ("status", 0xFFFFFFFF, "not a valid status1"),
    ("mail", 3, "mail 0x03 without a held mail item"),
])
def test_record_validity_bounds_every_mutable_field(field, value, message):
    """Codex C4-6c finding 1: hp=65535/20, PP 255 in every slot and status 0xFFFFFFFF all passed
    the old whitelist. Each is a named problem now, whatever the scenario."""
    mon = duo.gen3_decode(_saved(_fixture([STARTER, PIDGEY]), 3, [STARTER, dict(PIDGEY, **{field: value})]))[0][1]
    problems = duo.gen3_record_problems("x", mon, limits=LIMITS)
    assert any(message in p for p in problems), problems


def test_round_trip_refuses_codex_repro(pair):
    fixture, _ = pair
    broken = dict(PIDGEY, hp=65535, pp=[255, 255, 255, 255], status=0xFFFFFFFF)
    saved = _saved(fixture, 3, [STARTER, broken])
    problems = _rt("a", _decoded(saved), _decoded(fixture), _key(PIDGEY), walked=True)
    for message in ("hp 65535/20", "holds 255 PP", "not a valid status1", "not healed",
                    "not cured", "not restored"):
        assert any(message in p for p in problems), (message, problems)


def test_a_withdrawal_is_healed_cured_and_pp_restored(pair):
    """Each is inside its bound, yet not what a mon fresh out of a box looks like."""
    fixture, saved = pair
    assert _rt("a", _decoded(saved), _decoded(fixture), _key(PIDGEY)) == []
    for change, message in (({"hp": 3}, "not healed"), ({"status": 8}, "not cured"),
                            ({"pp": [20, 30, 0, 0]}, "not restored")):
        hurt = _saved(fixture, 3, [STARTER, dict(PIDGEY, **change)])
        problems = _rt("a", _decoded(hurt), _decoded(fixture), _key(PIDGEY))
        assert any(message in p for p in problems), (change, problems)


def _rr_withdrawn(limits, moves=(33, 0, 0, 0)):
    pp = [limits["max_pp"](m, 0, i) for i, m in enumerate(moves)]
    return {"checksum_ok": None, "has_species": 1, "is_bad_egg": 0, "species": 277, "level": 5,
            "hp": 20, "max_hp": 20, "status": 0, "mail": 0xFF, "held_item": 0,
            "moves": list(moves), "pp": pp, "pp_bonuses": 0}


def test_rr_withdrawal_keeps_the_move0_pp_cfru_computes():
    """Codex C4-6d #2: CFRU's expansion computes PP for all four slots, move 0 included, so an RR
    mon with moves [33,0,0,0] comes out of a box with move 0's table PP in the empty slots. Only
    vanilla's empty slot is 0."""
    limits = duo.gen3_limits("radical_red", _rom_with_pp({0: 35, 33: 35}, "radical_red"))
    mon = _rr_withdrawn(limits)
    assert mon["pp"] == [35, 35, 35, 35]
    assert duo.gen3_record_problems("x", mon, rr=True, limits=limits) == []
    assert duo.gen3_withdrawn_problems("x", mon, limits, rr=True) == []
    assert any("not restored" in p for p in duo.gen3_withdrawn_problems("x", mon, limits, rr=False))


def test_rr_withdrawal_with_the_real_rom_move0_pp():
    """The same, with move 0's PP read from the staged companion ROM's own move table."""
    rom = REPO / duo.GEN3_TITLES["radical_red"]["staged"]
    if not rom.is_file():
        pytest.skip(f"{rom} is not built (the RR companion ROM is a local artifact)")
    limits = duo.gen3_limits("radical_red", rom.read_bytes())
    mon = _rr_withdrawn(limits)
    assert mon["pp"][1] == limits["max_pp"](0, 0, 1)
    assert duo.gen3_record_problems("x", mon, rr=True, limits=limits) == []
    assert duo.gen3_withdrawn_problems("x", mon, limits, rr=True) == []


def test_friendship_may_move_only_when_the_scenario_walked_the_mon(pair):
    fixture, _ = pair
    walked = _saved(fixture, 3, [STARTER, dict(PIDGEY, friendship=90)])
    assert _rt("a", _decoded(walked), _decoded(fixture), _key(PIDGEY), walked=True) == []
    idle = _rt("b", _decoded(walked), _decoded(fixture), _key(PIDGEY), walked=False)
    assert any("('friendship'," in p for p in idle), idle
    assert frozenset({"hp", "status", "mail", "pp", "checksum"}) == duo.GEN3_RECORD_MUTABLE


def test_memorial_friendship_moves_only_after_a_battle(pair):
    fixture, _ = pair
    changed = _saved(fixture, 3, [STARTER],
                     {(13, 0): dict(_mon(PIDGEY["personality"], party=False, species=16), friendship=60)})
    key = _key(PIDGEY)
    assert _mem("b", _decoded(changed), _decoded(fixture), key, 13, battled=True) == []
    assert any("('friendship'," in p for p in _mem("b", _decoded(changed), _decoded(fixture), key, 13))


def test_memorial_record_keeps_the_fixture_invariants(pair):
    fixture, _ = pair
    item = _saved(fixture, 3, [STARTER],
                  {(13, 0): dict(_mon(PIDGEY["personality"], party=False, species=16), held_item=13)})
    problems = _mem("b", _decoded(item), _decoded(fixture), _key(PIDGEY), 13)
    assert any("('held_item'," in p for p in problems), problems


def test_capture_record_must_be_valid_for_the_cartridge(pair):
    fixture, _ = pair
    caught = _saved(fixture, 3, [STARTER, PIDGEY, dict(CATCH, flip_checksum=True)], balls=3)
    problems = _cap("a", _decoded(caught), _decoded(fixture), _key(CATCH),
                                         {"species_id": 19})
    assert any("secure checksum fails" in p for p in problems), problems


def test_rr_box_trip_ignores_only_what_cfru_compression_drops():
    was = {"species": 277, "contest": [1, 2, 3, 4, 5, 6], "unknown": 7, "ribbons": 0x10, "moves": [1]}
    now = {"species": 277, "contest": [0] * 6, "unknown": 0, "ribbons": 0x80000010, "moves": [1]}
    assert duo.gen3_record_diff(was, now, rr=True) == []
    assert duo.gen3_record_diff(was, now, rr=False)                  # vanilla keeps them
    assert duo.gen3_record_diff(was, dict(now, ribbons=0x80000011), rr=True)


# finding 2: whiteout/boxsync need the cartridge read back, not just the ACK
def _whiteout_receipts(k):
    a = (f"BOXED_OBSERVED {k} box=0:0\nTX whiteout - {{}}\nCENTER_STATE map=5.4 at=(7,4)\n"
         "WHITED_OUT at here\nRX rebuild_start text=REBUILDING\n"
         f"RX party_mon key={k}\nTX sync_retrieve_done {k} {{}}\nWRITE_IN_CENTER map=5.4 at=(7,4)\n"
         f"RX rebuild_done\nRETURNED_OBSERVED {k} slot=1\nCONTROL_LIVE nurse {k} map=5.4\n"
         f"RX box_mon key={k}\nCONTROL_REFUSED nurse box_mon {k} clause=field_controls_locked held\n")
    b = (f"RX box_mon key={k}\nTX stats_cache {k} {{}}\nBOXED_OBSERVED {k} box=0:0\n"
         f"RX party_mon key={k}\nTX sync_retrieve_done {k} {{}}\nRETURNED_OBSERVED {k} slot=1\n")
    return {"a": a, "b": b}


def test_whiteout_oracle_refuses_a_noop_deposit(monkeypatch, tmp_path):
    fixture = _fixture([STARTER, PIDGEY])
    saved = _saved(fixture, 3, [STARTER, PIDGEY])
    k = _key(PIDGEY)
    run, notes = _oracle_stub(monkeypatch, tmp_path, "whiteout_gen3", {"a": saved, "b": saved},
                              fixture, [{"a": {"key": k}, "b": {"key": k}, "status": "alive"}])
    run._link_keys = {"a": k, "b": k}
    receipts = _whiteout_receipts(k)
    run.assert_whiteout_gen3_saved(receipts)
    assert notes and "whiteout" in notes[-1]
    # the ACKs and the final saved party are all there; only the physical read-back is missing
    noop = dict(receipts, b=receipts["b"].replace(f"BOXED_OBSERVED {k} box=0:0\n", ""))
    with pytest.raises(RuntimeError, match="BOXED_OBSERVED"):
        run.assert_whiteout_gen3_saved(noop)
    never_back = dict(receipts, b=receipts["b"].replace(f"RETURNED_OBSERVED {k} slot=1\n", ""))
    with pytest.raises(RuntimeError, match="RETURNED_OBSERVED"):
        run.assert_whiteout_gen3_saved(never_back)


# finding 4: RR extension freshness is proven against live RAM or stated OPEN
def test_rr_extension_is_open_unless_the_live_ram_copy_matches():
    fixture = _rr_fixture(ext_byte=0x00)
    saved = _rr_saved(fixture, 3, ext_byte=0x11)
    assert duo.check_gen3_witness(saved, saved, fixture, saves=1, rr=True)["extension"] == "OPEN"
    live = bytes([0x11]) * codec.RR_EXT_SIZE
    facts = duo.check_gen3_witness(saved, saved, fixture, saves=1, rr=True, ext_ram=live)
    assert facts["extension"] == "LIVE_RAM_MATCH"
    # a stale extension: flash still holds the previous state while RAM moved on
    stale = bytes([0x22]) * codec.RR_EXT_SIZE
    with pytest.raises(RuntimeError, match="extension sector 30 is not the live EWRAM"):
        duo.check_gen3_witness(saved, saved, fixture, saves=1, rr=True, ext_ram=stale)
    with pytest.raises(RuntimeError, match="live extension RAM copy is 4 bytes"):
        duo.check_gen3_witness(saved, saved, fixture, saves=1, rr=True, ext_ram=b"\0" * 4)
    assert duo.check_gen3_witness(*_pair_vanilla(), saves=1)["extension"] is None


def _pair_vanilla():
    fixture = _fixture([STARTER, PIDGEY])
    saved = _saved(fixture, 3, [STARTER, PIDGEY])
    return saved, saved, fixture


def _ext_line(scenario, inst, saves, size=None, attempt=1):
    return (f"SAVE_WITNESS_EXT path=patch/build/e2e_{scenario}_{inst}_{attempt}_witness_ext.bin "
            f"bytes={codec.RR_EXT_SIZE if size is None else size} saves={saves}\n")


def _rr_ext_run(tmp_path, monkeypatch, saves=1):
    fixture = _rr_fixture(ext_byte=0x00)
    saved = _rr_saved(fixture, 3, ext_byte=0x11)
    if saves == 2:
        saved = _rr_saved(saved, 4, ext_byte=0x11)
    run, receipts, notes, build = _witness_run(tmp_path, monkeypatch, (fixture, saved))
    run.game, run.gcfg = "gen3_rr", dict(duo.GAMES["gen3_rr"])
    (tmp_path / "patch" / "build").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(duo, "BUILD", str(tmp_path / "patch" / "build"))
    for inst in ("a", "b"):
        (tmp_path / "patch" / "build" / f"e2e_faint_cmd_gen3_{inst}_1_witness.bin").write_bytes(saved)
        (tmp_path / "patch" / "build" / f"e2e_faint_cmd_gen3_{inst}_1_witness_ext.bin").write_bytes(
            bytes([0x11]) * codec.RR_EXT_SIZE)
    return run, notes


_EMITTER = re.compile(r"local function witness_receipt_lines\(.*?\nend\n", re.S)
_EMIT = {}


def _driver_emit(rel, saves, erel=None):
    """The receipt lines duo_gen3_main.lua's OWN witness_receipt_lines emits for one save, in its
    emission order (the body is extracted from the driver and run under lupa), so producer and
    consumer are tested together rather than against a hand-written order (Codex C4-6d #1)."""
    if "fn" not in _EMIT:
        from lupa import LuaRuntime

        body = _EMITTER.search(DRIVER.read_text(encoding="utf-8"))
        assert body, "duo_gen3_main.lua must define witness_receipt_lines"
        _EMIT["fn"] = LuaRuntime(unpack_returned_tuples=True).execute(
            body.group(0) + "\nreturn witness_receipt_lines")
    lines = _EMIT["fn"](rel, codec.FLASH_SIZE, saves, saves, 2 + saves, erel, codec.RR_EXT_SIZE)
    return [lines[i] for i in range(1, len(lines) + 1)]


def _rr_receipt(inst, saves, ext_saves):
    out = "SAVE_WITNESS faint_cmd counter=2->3\n"
    rel = f"patch/build/e2e_faint_cmd_gen3_{inst}_1_witness.bin"
    for n in range(1, saves + 1):
        erel = rel.replace(".bin", "_ext.bin") if n in ext_saves else None
        out += "".join(line + "\n" for line in _driver_emit(rel, n, erel))
    return out


def test_driver_receipts_bind_the_extension_as_the_consumer_reads_them(tmp_path, monkeypatch):
    """Codex C4-6d #1: the driver logged EXT before DUMP and the consumer demanded EXT after it,
    so every genuine RR copy read as OPEN. Fed the driver's ACTUAL lines, the consumer binds it."""
    rel = "patch/build/e2e_faint_cmd_gen3_a_1_witness.bin"
    lines = _driver_emit(rel, 1, rel.replace(".bin", "_ext.bin"))
    assert [line.split()[0] for line in lines] == ["SAVE_WITNESS_DUMP", "SAVE_WITNESS_EXT"]
    assert duo.SAVE_WITNESS_DUMP_RE.search(lines[0]) and duo.GEN3_EXT_RE.match(lines[1])
    run, notes = _rr_ext_run(tmp_path, monkeypatch)
    blob, why = run._gen3_final_ext("a", "\n".join(lines) + "\n", 1, 0)
    assert why == "" and blob == bytes([0x11]) * codec.RR_EXT_SIZE


def test_rr_witness_method_binds_the_extension_to_the_final_save(tmp_path, monkeypatch):
    run, notes = _rr_ext_run(tmp_path, monkeypatch)
    run.check_save_witness_gen3({i: _rr_receipt(i, 1, {1}) for i in ("a", "b")})
    assert all("extension_30_31=LIVE_RAM_MATCH" in n for n in notes), notes
    notes.clear()
    # the copy is on disk, but no receipt binds it to any save: OPEN, not a match
    run.check_save_witness_gen3({i: _rr_receipt(i, 1, set()) for i in ("a", "b")})
    assert all("extension_30_31=OPEN" in n and "no SAVE_WITNESS_EXT receipt" in n for n in notes), notes


def test_rr_extension_copy_from_an_earlier_save_is_open(tmp_path, monkeypatch):
    """Codex's repro: counter 2->4, dumps 1 and 2, the EXT receipt only at ordinal 1 -- the file
    on disk is save 1's copy, so the final save's extension is OPEN, never LIVE_RAM_MATCH."""
    run, notes = _rr_ext_run(tmp_path, monkeypatch, saves=2)
    run.check_save_witness_gen3({i: _rr_receipt(i, 2, {1}) for i in ("a", "b")})
    assert all("extension_30_31=OPEN" in n and "save 1's, not the final save 2's" in n
               for n in notes), notes


@pytest.mark.parametrize("line, message", [
    (_ext_line("faint_cmd_gen3", "{inst}", 1, attempt=2), "extension copy landed at"),
    (_ext_line("faint_cmd_gen3", "{inst}", 1, size=16), "receipt says 16 bytes"),
])
def test_rr_extension_receipt_that_names_another_file_fails(tmp_path, monkeypatch, line, message):
    run, _ = _rr_ext_run(tmp_path, monkeypatch)
    receipts = {i: _rr_receipt(i, 1, set()) + line.format(inst=i) for i in ("a", "b")}
    with pytest.raises(RuntimeError, match=message):
        run.check_save_witness_gen3(receipts)


def test_explode_runs_on_the_p_h_carrier_as_a_qualification_row():
    """Owner ruling 19: RR force_explode ends in the P+H hand-off, so explode_gen3 runs on the
    P+H carrier's explode case. The chain needs the attacker's own faint site after the 153
    stamp -- the downstream witness the old control label asked for -- so it qualifies
    (G5-RR-ORACLES) and the summary carries no CONTROL tag."""
    row = duo.SCENARIOS["explode_gen3"]
    assert row["scenario_module"] == "linked_faint_active" and row["active_faint_case"] == "explode"
    assert "control" not in row
    assert duo.summary_lines({"explode_gen3": (True, 1)}, "gen3_rr") == [
        "  explode_gen3: PASS (attempt 1 of 1)"]
    required, _, _ = duo.active_faint_chain("K0", "explode")
    assert any("ACTIVE_FAINT_SITE" in r for r in required) and any("last_move=153" in r for r in required)
    assert not (REPO / "lua" / "tests" / "duo" / "scenario_gen3_explode.lua").exists()


@pytest.mark.parametrize("game", ("gen3_frlg", "gen3_lgfr", "gen3_emerald"))
def test_explode_binds_the_vanilla_duo_orientation(game):
    assert duo.scenario_applies("explode_gen3", game)
    row = duo.SCENARIOS["explode_gen3"]
    assert row["flags"] == ["--explode-mode"] and row["active_faint_case"] == "explode"
    assert row["oracle"] == "assert_explode_gen3_saved" and "control" not in row


def test_explode_carrier_requires_an_actual_pp_drop(ph):
    _, passed, reason, _ = ph("explode", "b", "no_explosion_pp_drop")
    assert passed is False and "PP" in reason


def test_explode_receipt_cannot_omit_or_forge_the_pp_witness(ph):
    _, passed, _, log = ph("explode", "b")
    assert passed and "EXPLOSION_PP K0 committed=5/5/5/5 ko=4/5/5/5" in log
    required, ordered, forbidden = duo.active_faint_chain("K0", "explode")
    for bad in (re.sub(r"(?m)^EXPLOSION_PP.*\n", "", log),
                log.replace("ko=4/5/5/5", "ko=5/5/5/5")):
        assert duo.gen3_receipt_problems("b", bad, required=required, ordered=ordered, forbidden=forbidden)


def test_no_driver_pokes_game_memory():
    """Scripted normal inputs only: no scenario module or the driver writes the cartridge."""
    files = [DRIVER] + list((REPO / "lua" / "tests" / "duo").glob("scenario_gen3_*.lua"))
    for f in files:
        text = f.read_text(encoding="utf-8")
        if f.name == "scenario_gen3_static_wild.lua":
            # The ONE disclosed SYNTH write (XG3 "runtime SFC32 Rock state"): rock_rng_prep stages the
            # 16-byte expansion RNG so the Rock Smash roll is deterministic. It must stay exactly that.
            staged = 'memory.write_u8(f.rock_rng_address+i,tonumber(f.rock_rng_state_hex:sub(i*2+1,i*2+2),16),"System Bus")'
            assert text.count("memory.write") == 1 and text.count(staged) == 1
            assert text.index("local function rock_rng_prep") < text.index(staged) < text.index("return before")
        else:
            assert not re.search(r"memory\.write", text), f.name
        assert "zero_hp" not in text


# finding 7: wait_go's first argument is the MARKER
def test_no_scenario_passes_seconds_as_the_wait_go_marker():
    for f in (REPO / "lua" / "tests" / "duo").glob("scenario_gen3_*.lua"):
        assert not re.search(r"wait_go\(\s*\d", f.read_text(encoding="utf-8")), f.name


# the wire-log label: the gen3 battery rows run the NEW client
def test_wire_logs_are_labelled_by_client(monkeypatch, tmp_path):
    for game, label in (("gen3_frlg", "gen3_new"), ("gen3_rr", "gen3_new")):
        run = duo.DuoRun.__new__(duo.DuoRun)
        run.scenario, run.game, run.gcfg = "faint_cmd_gen3", game, dict(duo.GAMES[game])
        run.args = argparse.Namespace(wire_log=True)
        run.data_dir = str(tmp_path / game)
        wire = Path(run.data_dir) / "wire"
        wire.mkdir(parents=True)
        (wire / "wire_a.jsonl").write_text("{}\n", encoding="utf-8")
        out = tmp_path / f"out_{game}"
        monkeypatch.setattr(duo, "WIRE_FIXTURES", str(out))
        landed = run.collect_wire_logs()
        assert [Path(x).name for x in landed] == [f"faint_cmd_gen3_a_{label}.jsonl"], game


# finding 2: the driver's location logic, run as written (lupa over the extracted bodies)
_LOCATE_FNS = re.compile(r"local function (locate_key|observed)\(.*?\nend\n", re.S)


@pytest.fixture(scope="module")
def locate():
    """`locate(party, boxes, want)`: party is "K,X" or "unreadable"; boxes is "X,K|unreadable|"
    (one entry per box). Returns (observed, unknown, why) from the driver's own functions."""
    from lupa import LuaRuntime

    text = DRIVER.read_text(encoding="utf-8")
    bodies = [m.group(0) for m in _LOCATE_FNS.finditer(text)]
    assert len(bodies) == 2, "duo_gen3_main.lua must define locate_key and observed"
    lua = LuaRuntime(unpack_returned_tuples=True)
    return lua.execute("\n".join(bodies) + """
        local function split(s, sep)
            local out = {}
            for part in (s .. sep):gmatch("(.-)" .. sep:gsub("%p", "%%%0")) do out[#out + 1] = part end
            return out
        end
        local function mons(csv)
            local t = {}
            for i, k in ipairs(csv == "" and {} or split(csv, ",")) do
                t[i] = { slot = i - 1, key = k, has_species = 1 }
            end
            return t
        end
        return function(party, boxes, want)
            local rows = split(boxes, "|")
            local read_party = function()
                if party == "unreadable" then return nil, "no pointer" end
                return mons(party)
            end
            local read_box = function(b)
                if rows[b + 1] == "unreadable" then return nil, "mid-relocation" end
                return mons(rows[b + 1])
            end
            local at, why = locate_key("K", read_party, read_box, #rows, function(m) return m.key end)
            return observed(at, want), at == nil, why
        end""")


def test_location_positives_need_every_read(locate):
    boxed, unknown, _ = locate("", "|X,K", "boxed")
    assert boxed == "1:1" and not unknown
    returned, unknown, _ = locate("X,K", "|", "returned")
    assert returned and not unknown
    assert locate("K", "|", "boxed")[0] is None          # in the party: not boxed
    assert locate("", "K|", "returned")[0] is None       # boxed: not returned


def test_box_read_failure_is_never_returned_observed(locate):
    """Codex: party read OK (K in the party) but a box read failed -> K may be boxed as well;
    no RETURNED_OBSERVED."""
    got, unknown, why = locate("K", "|unreadable", "returned")
    assert got is None and unknown and "box 1 unreadable" in why


def test_party_read_failure_is_never_boxed_observed(locate):
    """Codex: the party is unreadable but K sits in a box -> K may be in the party as well;
    no BOXED_OBSERVED."""
    got, unknown, why = locate("unreadable", "K", "boxed")
    assert got is None and unknown and "party unreadable" in why


def test_a_key_in_two_party_slots_is_ambiguous(locate):
    """Codex C4-6d #3: party K,K with every box empty used to read RETURNED_OBSERVED."""
    got, unknown, why = locate("K,K", "|", "returned")
    assert got is None and unknown and "two party slots" in why


def test_a_key_in_two_box_slots_is_ambiguous(locate):
    got, unknown, why = locate("", "K|K", "boxed")
    assert got is None and unknown and "two box slots" in why


# ── C4-6e: the first live link_gen3 (FR A / LG B) ─────────────────────────────────────────
def test_link_consequence_is_named_when_the_partner_ends_before_the_link():
    """B's capture was quarantined (box_mon) and correctly boxed; A had FAILED, so the link
    never formed. The old body read that as "not in the party before the SAVE"; it is a
    consequence of the partner's failure and says so."""
    from lupa import LuaRuntime

    lua = LuaRuntime(unpack_returned_tuples=True)
    lua.globals().SCENARIO_DIR = str(REPO / "lua" / "tests" / "duo").replace("\\", "/")
    lua.execute(_FAKE_CTX)
    ok, passed, msg, _ = _run_module(lua, "link", "b", "initial",
                                     {"unsent": "sync_retrieve_done", "gone": "K9", "partner": A_FAILED})
    assert ok and passed is False and msg.startswith(
        "CONSEQUENCE: the partner failed before its catch, so the link never formed"), msg


_BAG_READY = re.compile(r"local function bag_input_ready\(.*?\nend\n", re.S)


@pytest.fixture(scope="module")
def bag_ready():
    """`bag_ready(cb2_ok, fade_active, tasks)`: the driver's own bag_input_ready over a fake bus;
    tasks is a comma list of active task names ("input", "animate")."""
    from lupa import LuaRuntime

    body = _BAG_READY.search(DRIVER.read_text(encoding="utf-8"))
    assert body, "duo_gen3_main.lua must define bag_input_ready"
    lua = LuaRuntime(unpack_returned_tuples=True)
    return lua.execute(body.group(0) + """
        local s = { gMain = 0x100, CB2_BagMenuRun = 0x08107EE0, gPaletteFade = 0x200, gTasks = 0x300,
                    Task_BagMenu_HandleInput = 0x08108F0C, Task_AnimateWin0v = 0x08108CFC }
        return function(cb2_ok, fade_active, tasks)
            local mem32, mem8 = {}, {}
            mem32[s.gMain + 4] = cb2_ok and (s.CB2_BagMenuRun | 1) or 0x080565B5
            mem8[s.gPaletteFade + 7] = fade_active and 0x80 or 0x00
            local i = 0
            for name in tasks:gmatch("[^,]+") do
                local base = s.gTasks + i * 40
                mem8[base + 4] = 1
                mem32[base] = (name == "input" and s.Task_BagMenu_HandleInput or s.Task_AnimateWin0v) | 1
                i = i + 1
            end
            return bag_input_ready(function(a) return mem32[a] or 0 end,
                                   function(a) return mem8[a] or 0 end, s)
        end""")


@pytest.mark.parametrize("cb2_ok, fade_active, tasks, ready", [
    (True, False, "input", True),
    (False, False, "input", False),                 # not the bag yet
    (True, True, "input", False),                   # the open fade: every press is dropped
    (True, False, "input,animate", False),          # the window animation: same
    (True, False, "", False),                       # no input task at all
])
def test_bag_input_ready_follows_task_bagmenu_handleinput(bag_ready, cb2_ok, fade_active, tasks, ready):
    """pret item_menu.c:1044-1049 (the gates) and :501-502 (CB2_BagMenuRun installed before the
    fade ends): the live FR failure pressed A in exactly the fade_active=True row."""
    assert bag_ready(cb2_ok, fade_active, tasks) is ready


def test_catch_waits_for_bag_input_before_the_throw():
    text = DRIVER.read_text(encoding="utf-8")
    catch = text[text.index("function ctx.catch("):text.index("function ctx.lose_active(")]
    bag, wait, throw = (catch.index("ctx.choose_action(ACTION_BAG)"),
                        catch.index("ctx.wait_until(ctx.bag_input_ready"),
                        catch.index("SP.throw_pokeball_from_bag("))
    assert bag < wait < throw


# ── C4-6g: the ball-RNG retry, generalized from the Gen 1 standard ────────────────────────
OUT_OF_BALLS = "RESULT: FAIL (hunt ended out-of-balls)"
A_CONSEQUENCE = ("RESULT: FAIL (CONSEQUENCE: the partner failed before its catch, so the link never "
                 "formed; 21EDCA07:1C600D89 stays quarantined in the PC)")


def test_the_driver_returns_a_bare_out_of_balls_reason():
    """The live LG half logged "hunt ended hunt ended out-of-balls": ctx.catch prefixed the phrase
    and so did its callers, and the doubled phrase matches no cause -- the retry never fired."""
    text = DRIVER.read_text(encoding="utf-8")
    catch = text[text.index("function ctx.catch("):text.index("function ctx.lose_active(")]
    assert 'return nil, "out-of-balls"' in catch
    assert not re.search(r'return nil, "hunt ended', text)     # no caller-side prefix twice
    assert duo.classify_gen1_result("RESULT: FAIL (hunt ended hunt ended out-of-balls)") == "FINAL"


@pytest.mark.parametrize("scenario", ["link", "deadzone"])
def test_a_ball_miss_reads_as_the_gen1_cause(lua, scenario):
    """The scenario's own RESULT over the driver's bare reason is the standard's exact phrase."""
    ok, passed, msg, _ = _run_module(lua, scenario, "b", "initial", {"catch_why": "out-of-balls"})
    assert ok and passed is False and msg == "hunt ended out-of-balls", msg
    assert duo.classify_gen1_result(f"RESULT: FAIL ({msg})") == "CAUSE_RNG"


@pytest.mark.parametrize("game", ["gen3_frlg", "gen3_rr"])
def test_gen3_ball_hunts_get_the_gen1_retry_budget(game):
    assert duo.rng_retry_family(game)
    assert duo.scenario_attempt_limit("link_gen3", game) == 3
    assert duo.scenario_attempt_limit("deadzone_gen3", game) == 3
    for name in ("faint_cmd_gen3", "linked_faint_active_gen3", "boxsync_gen3", "whiteout_gen3",
                 "reconnect_gen3"):
        assert duo.scenario_attempt_limit(name, game) == 1, name     # no ball, nothing to retry
    # the Gen 1 standard itself is unchanged, cold boot included
    assert duo.scenario_attempt_limit("ball_gate_new", "gen1_new") == 1
    assert duo.scenario_attempt_limit("link_new", "gen1_new") == 3
    assert duo.scenario_attempt_limit("species_clause_new", "gen1_new") == 8


def test_gen3_pair_retries_only_on_the_out_of_balls_cause():
    """Live link_gen3 run 2: B missed both balls; A named the CONSEQUENCE. That pair retries."""
    assert duo.classify_gen1_result(A_CONSEQUENCE) == "CONSEQUENCE"
    assert duo.retryable_gen1_rng("gen3_frlg", {"a": A_CONSEQUENCE, "b": OUT_OF_BALLS}, 1, 3)
    assert duo.retryable_gen1_rng("gen3_frlg", {"a": "RESULT: PASS (ran)", "b": OUT_OF_BALLS}, 2, 3)
    assert not duo.retryable_gen1_rng("gen3_frlg", {"a": A_CONSEQUENCE, "b": OUT_OF_BALLS}, 3, 3)


@pytest.mark.parametrize("b", [
    None,                                                      # B made no claim at all
    "RESULT: FAIL (hunt ended no wild encounter)",             # any other cause is FINAL
    "RESULT: FAIL (hunt ended hunt ended out-of-balls)",       # the old doubled phrase
    A_CONSEQUENCE,                                             # two consequences, no cause
])
def test_a_consequence_never_retries_on_its_own(b):
    assert not duo.retryable_gen1_rng("gen3_frlg", {"a": A_CONSEQUENCE, "b": b}, 1, 3)


def test_the_gen3_driver_echoes_the_idle_jitter():
    """A retry is only a new roll if its timing differs: FRLG's VBlank advances the RNG every
    frame (pret src/main.c:412), and the driver must echo the harness's count in the Gen 1
    format that jitter_problems checks on every double PASS of a retry family."""
    text = DRIVER.read_text(encoding="utf-8")
    assert 'log(fmt("JITTER requested=%d applied=%d attempt=%d"' in text
    assert duo.jitter_problems("JITTER requested=37 applied=37 attempt=2\n", 37) == []


# ── C4-6h: the timeout task dump and the helper's bag wait. The whiteout walk-out was DROPPED
# (owner ruling: SLink writes must land inside Pokemon Centers; the allow-list is its own card). ─────────────
def test_a_timeout_line_carries_the_task_dump():
    """The whiteout hold timed out naming nothing; the line now carries every active task."""
    text = DRIVER.read_text(encoding="utf-8")
    body = text[text.index("function ctx.wait_until("):text.index("local function go_lines(")]
    assert "pcall(SP.PC.dump)" in body and '"TIMEOUT waiting for "' in body
    assert re.search(r"^PC\.dump = pc_state_dump", SCRIPTED.read_text(encoding="utf-8"), re.M)


def test_the_exported_dump_names_the_active_tasks(bag_stubbed):  # noqa: F811 (imported fixture)
    runtime, module, store, logged, exits = bag_stubbed
    store[module.TASKS_BASE] = 0x0815F9A5                        # one active task
    store[module.TASKS_BASE + 4] = 1
    dump = module.PC.dump()
    assert "tasks=[0815F9A5]" in dump, dump


def _bag_at_pokeball(runtime, module, store):
    cp = _in_battle_cp(runtime, store)
    store[module.GMAIN_CALLBACK2_ADDR] = module.CB2_BAG_MENU_RUN
    store[module.BAG_MENU_STATE_ADDR + module.BAG_POCKET_OFF] = module.BAG_POCKET_POKEBALLS
    ptr_addr, sb1_addr = 0x03005008, 0x02020000
    store[ptr_addr] = sb1_addr
    store[sb1_addr + module.SB1_POKEBALLS_POCKET_OFFSET] = module.ITEM_POKE_BALL
    store[module.SPECIAL_VAR_ITEM_ID_ADDR] = module.ITEM_POKE_BALL
    fade = 0x02037AB8
    return runtime.table(
        predicates=runtime.table(
            in_battle=cp.predicates["in_battle"],
            palette_fade_active=runtime.table(address=fade, offset=7, mask=0x80, expect=0, width=1)),
        pointers=runtime.table(gSaveBlock1Ptr=runtime.table(address=ptr_addr))), fade


def test_the_bag_throw_waits_for_the_input_task(bag_stubbed):  # noqa: F811 (imported fixture)
    """Live FR throw 2: the bag was up, the pocket already POKEBALLS, and the selecting A fell in
    the open fade. The helper must wait for the input task, and name the gate when it never
    opens -- not press A into the fade and report a zero gSpecialVar_ItemId."""
    runtime, module, store, logged, exits = bag_stubbed
    cp, fade = _bag_at_pokeball(runtime, module, store)
    store[fade + 7] = 0x80                                      # the fade never ends
    module.throw_pokeball_from_bag(cp, "test")
    fail = [line for line in logged if "RESULT: FAIL" in line]
    assert exits and fail and "the bag never took input" in fail[-1], logged


def test_the_bag_throw_proceeds_once_the_input_task_reads(bag_stubbed):  # noqa: F811 (imported fixture)
    runtime, module, store, logged, exits = bag_stubbed
    cp, fade = _bag_at_pokeball(runtime, module, store)
    store[module.TASKS_BASE] = module.TASK_BAG_MENU_HANDLE_INPUT
    store[module.TASKS_BASE + 4] = 1
    module.throw_pokeball_from_bag(cp, "test")
    assert not exits, logged
    # ... and the window animation, when it runs, holds the press the same way
    logged.clear()
    store[module.TASKS_BASE + 40] = module.TASK_ANIMATE_WIN0V
    store[module.TASKS_BASE + 44] = 1
    module.throw_pokeball_from_bag(cp, "test")
    assert exits and any("the bag never took input" in line for line in logged), logged


def test_the_bag_task_symbols_match_pret():
    for title, want in (("firered", (0x08108F0D, 0x08108CFD)), ("leafgreen", (0x08108EE5, 0x08108CD5))):
        syms = {}
        for line in (REPO / "data" / "gen3" / "pret" / f"poke{title}.sym").read_text(
                encoding="utf-8").splitlines():
            parts = line.split()
            if len(parts) == 4 and parts[3] in ("Task_BagMenu_HandleInput", "Task_AnimateWin0v"):
                syms.setdefault(parts[3], int(parts[0], 16) | 1)
        assert (syms["Task_BagMenu_HandleInput"], syms["Task_AnimateWin0v"]) == want, title


# ── lose_active under a key-edge model of the battle controller (card C4-6i) ────────────────
# Live linked_faint_active_gen3 FR 324aea87: "LOSE ... status_move_slot=nil" then "TIMEOUT
# waiting for the move menu". The driver's REAL steer/choose_action/status_move_slot/use_move/
# lose_active, the helper's REAL wait_for_action_menu/verify_fight_cursor and gen3_boot_check's
# REAL tap/idle run over a model with two pret facts: the battle controllers act only on JOY_NEW
# (an edge of the held keys, main.c:299), and gBattleMons is empty until the intro copies it in
# (battle_main.c:2576-2578), after the encounter step the hunt returns on.
_LUA_DEF = r"^(?:local function {0}\(|function {0}\(|{0} = function\().*?^end$"


def _lua_defs(path, names):
    text = path.read_text(encoding="utf-8")
    out = []
    for name in names:
        match = re.search(_LUA_DEF.format(re.escape(name)), text, re.M | re.S)
        assert match, f"{path.name} must define {name}"
        out.append(match.group(0))
    return "\n".join(out)


_BATTLE_MODEL = r"""
S = { gBattlerControllerFuncs = 0x100, HandleInputChooseAction = 0x1000, HandleInputChooseMove = 0x2000,
      gActionSelectionCursor = 0x200, gMoveSelectionCursor = 0x201, gBattleMons = 0x300, gBattleMoves = 0x8000 }
local ACT, MOVE = S.HandleInputChooseAction | 1, S.HandleInputChooseMove | 1
POWER = { [33] = 40, [39] = 0, [145] = 20, [2] = 90 }       -- Tackle, Tail Whip, Bubble, (a recoil move)
EFFECT = { [33] = 0, [39] = 0, [145] = 0, [2] = 48 }        -- 0 = EFFECT_HIT, 48 = EFFECT_RECOIL (CFRU)
M = { ctrl = 0, pending = {}, prev = {}, cursor = 0, lead_hp = 27, over = false, filled = false,
      moves = { 33, 39, 145, 0 }, pp = { 35, 30, 30, 0 }, after = nil, used = {},
      frame = 0, read_every = 1, held = 0, outcome = nil }
GMAIN = 0x03003000
BITS = { A = 1, Right = 0x10, Left = 0x20, Up = 0x40, Down = 0x80 }
-- the PACK's move table (G5-RR-MOVEPICK); with M.rr the pret-FR gBattleMoves address holds
-- unrelated bytes (every "power" reads 99), as FR's table address does in a CFRU ROM
RR_MOVES = 0x9000
profile = { rom = { BATTLE_MOVES_ADDR = S.gBattleMoves }, derived = { BATTLE_MOVE_ENTRY_SIZE = 12 } }
M.foe_hits, M.foe_hp, M.hunts, M.rr = true, 12, 0, false
-- the expansion's 64-byte MoveInfo table (X3): effect = u16 at +8, power = 9 bits at +10 >> 7
EXP_MOVES = 0xA000
EXP_EFFECT = {}
LOGS = {}
function start(intro)                    -- the action menu comes up `intro` frames into the battle
    M.after = { n = intro, to = ACT, fill = true }
end
joypad = { set = function(t) M.pending = t or {} end }
memory = {
    read_u32_le = function(a) if a == S.gBattlerControllerFuncs then return M.ctrl end return 0 end,
    read_u16_le = function(a)
        if a == GMAIN + 0x2C then return M.held end          -- gMain.heldKeys, the game's own read
        if a == GMAIN + 0x2E then return 0 end
        if a == S.gBattleMons + 0x28 then return M.lead_hp end
        if a == S.gBattleMons + 0x58 + 0x28 then return M.foe_hp end
        if a >= EXP_MOVES and a < EXP_MOVES + 0x4000 then
            local off, id = (a - EXP_MOVES) % 64, (a - EXP_MOVES) // 64
            if off == 8 then return EXP_EFFECT[id] or 0 end
            return off == 10 and (POWER[id] or 0) << 7 or 0
        end
        return M.filled and M.moves[(a - S.gBattleMons - 0x0C) // 2 + 1] or 0
    end,
    read_u8 = function(a)
        if a == S.gActionSelectionCursor then return 0 end
        if a == S.gMoveSelectionCursor then return M.cursor end
        -- entry+0 = effect (SELF_DAMAGE_EFFECTS' own read), entry+1 = power (status_move_slot's)
        if a >= RR_MOVES then
            local off = (a - RR_MOVES) % 12
            return off == 0 and (EFFECT[(a - RR_MOVES - off) // 12] or 0) or (POWER[(a - RR_MOVES - 1) // 12] or 0)
        end
        if a >= S.gBattleMoves then
            if M.rr then return 99 end
            local off = (a - S.gBattleMoves) % 12
            return off == 0 and (EFFECT[(a - S.gBattleMoves - off) // 12] or 0)
                             or (POWER[(a - S.gBattleMoves - 1) // 12] or 0)
        end
        return M.filled and M.pp[a - S.gBattleMons - 0x24 + 1] or 0
    end,
}
emu = { frameadvance = function()                           -- one frame: ReadKeys, then the controller
    -- a main-loop pass that overruns (read_every > 1) reads the pad on fewer frames than the
    -- emulator runs; a frame's input the game never read is simply gone (RR's CFRU battle frames)
    M.frame = M.frame + 1
    local new = {}
    if M.frame % M.read_every == 0 then
        M.held = 0
        for _, k in ipairs({ "A", "Up", "Down", "Left", "Right" }) do
            local held = M.pending[k] == true
            new[k] = held and not M.prev[k]
            M.prev[k] = held
            if held then M.held = M.held | BITS[k] end
        end
    end
    M.pending = {}
    if M.ctrl == ACT and new.A then M.ctrl = 0; M.after = { n = 3, to = MOVE }
    elseif M.ctrl == MOVE and new.A then
        local move = M.moves[M.cursor + 1]
        M.used[#M.used + 1] = move
        M.ctrl = 0
        M.pp[M.cursor + 1] = M.pp[M.cursor + 1] - 1
        if POWER[move] > 0 then M.foe_hp = M.foe_hp - POWER[move] end
        if POWER[move] > 0 and M.foe_hp <= 0 then
            M.over, M.outcome = true, 1                    -- B_OUTCOME_WON: the foe goes down first, no counter
        elseif M.foe_hits then
            M.lead_hp = math.max(0, M.lead_hp - 9)
            if M.lead_hp <= 0 then
                M.over = true
                M.outcome = M.outcome_on_ko or 2            -- B_OUTCOME_LOST by default; a test may force DREW
                -- a test-only race: the whiteout heal can land before the watcher's hp0 record
                -- does, so a genuinely fainted lead can read back alive (G5-RR-CLEAN-2)
                if M.heal_on_faint then M.lead_hp = M.revive_hp or 27 end
            else
                M.after = { n = 41, to = ACT }
            end
        else
            M.after = { n = 41, to = ACT }
        end
    elseif M.ctrl == MOVE then                              -- HandleInputChooseMove's bit toggles
        if new.Right then M.cursor = M.cursor | 1 elseif new.Left then M.cursor = M.cursor & 2
        elseif new.Down then M.cursor = M.cursor | 2 elseif new.Up then M.cursor = M.cursor & 1 end
    elseif M.after then
        M.after.n = M.after.n - 1
        if M.after.n <= 0 then
            if M.after.fill then M.filled = true end
            M.ctrl = M.after.to; M.after = nil
        end
    end
end }
emu.framecount = function() return M.frame end
G = { spent = 0, budget = 1e9, shot = function() end,
      finish = function(_, why) error("G.finish: " .. tostring(why), 0) end }
function G.advance() emu.frameadvance() end
play = { in_battle = function() return not M.over end, at = function() return "here" end,
         wait_scene_settled = function() end }
function action_menu_up() return M.ctrl == ACT end
function party_menu_up() return false end
function action_cursor() return 0 end
ACTION_FIGHT = 0
log, fmt, cp = function(s) LOGS[#LOGS + 1] = s end, string.format, {}
ctx = { find = function() return { hp = M.lead_hp } end, hp0 = function() return nil end,
        battle_outcome = function() return M.outcome end }
function ctx.run_away() M.ctrl = 0; M.escaped = (M.escaped or 0) + 1; return true end
function ctx.hunt()                          -- a fresh foe that does attack, full HP, back in battle
    M.hunts, M.foe_hits, M.foe_hp, M.over = M.hunts + 1, true, 100, false
    M.after = { n = 40, to = S.HandleInputChooseAction | 1 }
    return true
end
function ctx.wait_until(pred, _, what)
    for _ = 1, 600 do local v = pred(); if v then return v end; emu.frameadvance() end
    LOGS[#LOGS + 1] = "TIMEOUT waiting for " .. tostring(what)
end
"""


@pytest.fixture
def battle_model():
    from lupa import LuaRuntime

    runtime = LuaRuntime(unpack_returned_tuples=True)
    runtime.execute(_BATTLE_MODEL)
    runtime.execute("local M = G\n" + _lua_defs(REPO / "lua" / "tests" / "gen3_boot_check.lua",
                                                ["M.idle", "M.tap"]))
    runtime.execute(_lua_defs(REPO / "lua" / "tests" / "gen3_scripted_play.lua",
                              ["wait_for_action_menu", "verify_fight_cursor"])
                    + "\nSP = { verify_fight_cursor = verify_fight_cursor }")
    text = DRIVER.read_text(encoding="utf-8")
    consts = re.search(r"^local ACTION_FIGHT, ACTION_BAG, ACTION_SWITCH, ACTION_RUN = .*$", text, re.M)
    outcome = re.search(r"^local B_OUTCOME_WON = .*$", text, re.M)
    self_damage = re.search(r"^local SELF_DAMAGE_EFFECTS = \{.*?^\}$", text, re.M | re.S)
    # X3: the vanilla gBattleMons/move-table geometry (the driver's BM table) and its power read
    geometry = re.search(r"^local BM = \{.*?effect_off = 0 \}$", text, re.M | re.S)
    assert geometry, "duo_gen3_main.lua must define the vanilla BM geometry"
    exp_consts = []
    for n in ("EXP_EFFECT_HIT", "EXP_SELF_KO_HITS"):
        found = re.search(rf"^local {n} = .*$", text, re.M)
        assert found, f"duo_gen3_main.lua must define a one-line `local {n} = ...` (battle_model extracts it)"
        exp_consts.append(found.group(0))
    menus = re.findall(r"^local function (?:ctrl0|action_menu_up|move_menu_up)\(\).*$", text, re.M)
    assert consts and outcome and self_damage and len(menus) == 3
    runtime.execute("\n".join([consts.group(0), outcome.group(0), *menus,
                               _lua_defs(DRIVER, ["game_press"]),
                               "local function press(btn, gap) return game_press(btn, joypad.set, G.advance,"
                               " function() return memory.read_u16_le(GMAIN + 0x2C) end, gap, 30) end",
                               self_damage.group(0), *exp_consts, geometry.group(0),
                               # swap the vanilla BM for the expansion's: same battler geometry, the
                               # MoveInfo power/effect bitfields (offsets/shifts from facts.json)
                               "function USE_EXP_BM(pw, ef) local b = {} for k, v in pairs(BM)"
                               " do b[k] = v end b.effect_off = nil"
                               " b.power = { off = pw[1], width = pw[2], shift = pw[3], mask = (1 << pw[4]) - 1 }"
                               " b.effect = { off = ef[1], width = ef[2], shift = ef[3], mask = (1 << ef[4]) - 1 }"
                               " BM = b end",
                               _lua_defs(DRIVER, ["move_power"]),
                               _lua_defs(DRIVER, ["move_effect", "steer", "ctx.choose_action",
                                                  "ctx.status_move_slot", "any_move_slot", "ctx.use_move",
                                                  "ctx.lose_active"]),
                               "GAME_PRESS = game_press",
                               "function LOSE() local ok, why = ctx.lose_active('K0', 'test')"
                               " return ok, tostring(why) end"]))
    return runtime


@pytest.mark.parametrize("intro", [40, 41])          # both parities of the helper's A mash
def test_lose_active_selects_fight_off_the_mash_and_reads_moves_after_the_intro(battle_model, intro):
    lua = battle_model
    lua.globals().start(intro)                       # gBattleMons empty at the encounter step
    ok, why = lua.globals().LOSE()
    m, logs = lua.globals().M, list(lua.globals().LOGS.values())
    assert ok is True, (why, logs)
    assert set(m.used.values()) == {39}, "only Tail Whip, never the damaging Tackle fallback"
    assert "LOSE K0 status_move_slot=1" in logs, logs


@pytest.mark.parametrize("every", [3, 4, 5])
def test_lose_active_on_an_overrunning_main_loop_uses_game_read_presses(battle_model, every):
    """G5-RR-CARRIER-FIX (receipt c12211c1): on RR the carrier's blind 3-frame A on FIGHT was
    never taken ("move menu never opened (action menu still up: true)"). A game that reads the
    pad only every `every` frames still gets every press, because each one waits for the game's
    own gMain.heldKeys to read released -> pressed -> released."""
    lua = battle_model
    lua.globals().M.read_every = every
    lua.globals().start(40)
    ok, why = lua.globals().LOSE()
    m, logs = lua.globals().M, list(lua.globals().LOGS.values())
    assert ok is True, (why, logs)
    assert set(m.used.values()) == {39}


def test_a_blind_tap_misses_an_overrunning_read_that_game_press_gets(battle_model):
    """The known-negative control for the probe above: the old G.tap hold (3 frames, then
    release) lands between two reads of a pass that reads every 5th frame."""
    lua = battle_model
    lua.execute("M.read_every = 5; M.frame = 1; M.ctrl = S.HandleInputChooseAction | 1")
    lua.execute("G.tap('A', 3, 13)")          # frames 2,3,4 held: the reads fall on 5 and 10
    assert lua.globals().M.ctrl == 0x1001, "the blind tap should have been missed"
    ok, n = lua.eval("GAME_PRESS('A', joypad.set, G.advance, function() return memory.read_u16_le(GMAIN + 0x2C) end, 0, 30)")
    assert ok is True and lua.globals().M.ctrl != 0x1001, n


def test_game_press_names_a_press_the_game_never_reads(battle_model):
    lua = battle_model
    lua.execute("M.read_every = 1000")
    ok, why = lua.eval("GAME_PRESS('A', joypad.set, G.advance, function() return 0 end, 0, 30)")
    assert ok is False and "never read A pressed in 30 frames" in why


def test_lose_active_reads_the_packs_move_table_not_pret_frs(battle_model):
    """G5-RR-MOVEPICK: on RR, pret FR's gBattleMoves address is not the move table (CFRU keeps
    its own at the pack's rom.BATTLE_MOVES_ADDR). The known-negative control: pointed at the FR
    address, every move reads 99 power and no status move is found."""
    lua = battle_model
    lua.execute("M.rr = true; profile.rom.BATTLE_MOVES_ADDR = S.gBattleMoves; start(40)")
    ok, why = lua.globals().LOSE()
    assert ok is False and "no no-damage move with PP" in why, why
    lua.execute("M.ctrl, M.after, M.lead_hp, M.used = 0, nil, 27, {}")
    lua.execute("profile.rom.BATTLE_MOVES_ADDR = RR_MOVES; start(40)")
    ok, why = lua.globals().LOSE()
    assert ok is True and set(lua.globals().M.used.values()) == {39}, why


def test_lose_active_rehunts_a_foe_that_never_hurts(battle_model):
    """Live RR R4 at 97672e6d: one foe that never hurt the lead ate all 30 of Leer's PP. After
    six turns with no HP lost the carrier RUNs and hunts a fresh foe."""
    lua = battle_model
    lua.execute("M.foe_hits = false; M.pp[2] = 10; start(40)")
    ok, why = lua.globals().LOSE()
    m, logs = lua.globals().M, list(lua.globals().LOGS.values())
    assert ok is True, (why, logs)
    assert m.hunts == 1 and m.escaped == 1 and any(line.startswith("LOSE_REHUNT K0 turn=") for line in logs), logs
    assert m.pp[2] > 0, "the stall was cut before the PP ran out"


def test_lose_active_refuses_a_lead_without_a_no_damage_move(battle_model):
    lua = battle_model
    lua.execute("M.moves = { 33, 145, 0, 0 }; M.pp = { 35, 30, 0, 0 }")
    lua.globals().start(40)
    ok, why = lua.globals().LOSE()
    assert ok is False and "no no-damage move" in why, why
    assert len(lua.globals().M.used) == 0, "never pressed a damaging move"


def test_lose_active_falls_back_to_a_damaging_move_once_status_pp_is_spent(battle_model):
    """G5-RR-CLEAN, live RR clean_gen3 at 870e5e5d: Leer's 30 PP spent at turn 31 (lead hp 1, the
    foe still up), so status_move_slot goes permanently nil. Model: Tail Whip's PP is almost gone
    (2), the foe hits for 9/turn, and the foe has plenty of HP left (100) so Tackle (the weakest
    damaging move with PP, slot 0) never risks a win -- it must only buy the lead its natural
    faint, never the "no no-damage move with PP" refusal."""
    lua = battle_model
    lua.execute("M.pp[2] = 2; M.foe_hp = 100")
    lua.globals().start(40)
    ok, why = lua.globals().LOSE()
    m, logs = lua.globals().M, list(lua.globals().LOGS.values())
    assert ok is True, (why, logs)
    assert list(m.used.values()) == [39, 39, 33], (m.used, logs)
    assert any(line.startswith("LOSE_FALLBACK K0 turn=3 slot=0") for line in logs), logs
    assert m.lead_hp == 0 and m.hunts == 0


def test_lose_active_rehunts_when_the_fallback_wins_the_battle(battle_model):
    """The fallback of the test above can also defeat a weak foe outright before the lead faints
    (live RR clean_gen3: a fresh wild encounter may be far weaker than the one that emptied the
    status move's PP). That is a bad matchup, not a failure: re-hunt, same as the STALL_TURNS
    path, and keep using the fallback on the fresh foe until the lead naturally faints."""
    lua = battle_model
    lua.execute("M.pp[2] = 1; M.foe_hp = 30")               # Tackle's 40 "damage" KOs a 30-hp foe
    lua.globals().start(40)
    ok, why = lua.globals().LOSE()
    m, logs = lua.globals().M, list(lua.globals().LOGS.values())
    assert ok is True, (why, logs)
    assert m.hunts == 1, "exactly one re-hunt, off the fallback's win"
    assert any(line.startswith("LOSE_REHUNT_WIN K0 turn=") for line in logs), logs
    assert m.lead_hp == 0
    assert list(m.used.values())[:2] == [39, 33], "Tail Whip once, then the fallback wins the battle"


# ── G5-RR-CLEAN-2: self-KO exclusion, WON-gated re-hunt, and the shared budget's failure ──────
def test_lose_active_refuses_a_self_damaging_fallback(battle_model):
    """any_move_slot must never hand the fallback a move that can faint the lead itself (recoil,
    Explosion, ...). Model: Tail Whip's 1 PP is spent turn 1, and the only move left with PP is a
    recoil move (id 2, effect 48 = EFFECT_RECOIL) that would otherwise win the fight for free --
    lose_active must refuse it outright, never press it hoping it "happens" not to self-KO."""
    lua = battle_model
    lua.execute("M.moves = { 39, 2, 0, 0 }; M.pp = { 1, 30, 0, 0 }")
    lua.globals().start(40)
    ok, why = lua.globals().LOSE()
    m = lua.globals().M
    assert ok is False and "no no-damage move with PP" in why, why
    assert 2 not in set(m.used.values()), "the recoil move must never be pressed"


@pytest.mark.parametrize("outcome_on_ko, label", [(2, "LOST"), (3, "DREW")])
def test_lose_active_does_not_rehunt_a_lost_or_drawn_battle(battle_model, outcome_on_ko, label):
    """turn==nil looks the same whether the fallback just won (re-hunt wanted) or the lead itself
    just fainted for a LOST/DREW (the point of this function, already achieved) -- and fainted()
    can read false right then anyway, if the whiteout heal lands before the watcher's hp0 record
    does. Only ctx.battle_outcome() == WON may start a re-hunt; LOST/DREW must never re-hunt, even
    while fainted() is masked this way."""
    lua = battle_model
    lua.execute("M.pp[2] = 1; M.foe_hp = 999")                  # the fallback alone never wins here
    lua.execute(f"M.heal_on_faint = true; M.outcome_on_ko = {outcome_on_ko}")
    lua.globals().start(40)
    ok, why = lua.globals().LOSE()
    m, logs = lua.globals().M, list(lua.globals().LOGS.values())
    assert ok is False, (label, why, logs)
    assert m.hunts == 0, f"a {label} battle must never re-hunt"
    assert not any(line.startswith("LOSE_REHUNT_WIN") for line in logs), (label, logs)


def test_lose_active_fails_by_name_when_the_fallback_keeps_winning(battle_model):
    """The WON re-hunt shares its 6-hunt budget with the STALL re-hunt above; running it out must
    fail with an explicit name, not the generic "battle left the action menu" a bare turn==nil
    would otherwise print. Model: a lead with effectively unlimited HP (foe counters never matter)
    and a foe that dies to 3 Tackles every single re-hunt, so the fallback wins six times running."""
    lua = battle_model
    lua.execute("M.pp[2] = 1; M.foe_hp = 100; M.lead_hp = 100000")
    lua.globals().start(40)
    ok, why = lua.globals().LOSE()
    m, logs = lua.globals().M, list(lua.globals().LOGS.values())
    assert ok is False, (why, logs)
    assert "the fallback keeps winning" in why, why
    assert m.hunts == 5, "5 re-hunts spent the budget; the 6th win must not spend a 6th"
    assert sum(1 for line in logs if line.startswith("LOSE_REHUNT_WIN")) == 5, logs


# ── C4-6j: Codex review of 43b9ccb4 / ad9669b1 ──────────────────────────────────────────────
A_FAILED = 'lua:"duo instance a\\nRESULT: FAIL (hunt ended out-of-balls)\\n"'
A_CAUGHT_THEN_FAILED = 'lua:"duo instance a\\nCAUGHT K7\\nRESULT: FAIL (save failed)\\n"'


@pytest.mark.parametrize("spec", [
    # Codex's executed case: this side's sync failed, the partner is still running
    {"failed": "sync_retrieve_failed", "unsent": "sync_retrieve_done", "partner_done": "lua:false"},
    {"failed": "box_mon_failed", "unsent": "sync_retrieve_done", "partner": A_FAILED},
    # no own failure, but the partner's receipt does not explain the missing link
    {"unsent": "sync_retrieve_done", "partner_done": "lua:false"},
    {"unsent": "sync_retrieve_done", "partner": A_CAUGHT_THEN_FAILED},
    {"unsent": "sync_retrieve_done", "partner": 'lua:"duo instance a\\nRESULT: PASS (caught K7)\\n"'},
])
def test_link_claims_a_consequence_only_when_the_partner_explains_it(lua, spec):
    ok, passed, msg, _ = _run_module(lua, "link", "b", "initial", {"gone": "K9", **spec})
    assert ok and passed is False, msg
    assert duo.classify_gen1_result(f"RESULT: FAIL ({msg})") == "FINAL", msg


def test_an_own_sync_failure_never_retries_with_a_partner_ball_miss(lua):
    """Codex item 3: B's independent sync failure beside A's ball miss is not a retry."""
    _, _, msg, _ = _run_module(lua, "link", "b", "initial",
                               {"failed": "sync_retrieve_failed", "unsent": "sync_retrieve_done",
                                "gone": "K9", "partner": A_FAILED})
    b = f"RESULT: FAIL ({msg})"
    assert not duo.retryable_gen1_rng("gen3_frlg", {"a": OUT_OF_BALLS, "b": b}, 1, 3), b
    # the positive control: the same pair with B's link genuinely missing IS a retry
    _, _, msg, _ = _run_module(lua, "link", "b", "initial",
                               {"unsent": "sync_retrieve_done", "gone": "K9", "partner": A_FAILED})
    assert duo.retryable_gen1_rng("gen3_frlg", {"a": OUT_OF_BALLS, "b": f"RESULT: FAIL ({msg})"}, 1, 3)


_CATCH_MODEL = r"""
BALLS, THROWS, RAN = nil, 0, { true }
S = { gBattleOutcome = 0x10, gActionSelectionCursor = 0x11 }
ctrl0 = function() return 0x12345679 end
fmt = string.format
B_OUTCOME_CAUGHT, ACTION_BAG = 7, 1
memory = { read_u8 = function() return 1 end }
reader = { read_balls = function() if BALLS then return { ball_count = BALLS } end end }
log = function() end
boot_keys = {}
-- in_battle defaults true: the realistic budget-exhaustion case (R4-DRIVER-2, OMP cx-d84db30c)
-- is the LAST ball missing and the wild Pokemon still up, not the battle already having ended.
play = { wait_scene_settled = function() return true end, in_battle = function() return true end }
SP = { verify_fight_cursor = function() return "fight" end,
       throw_pokeball_from_bag = function() THROWS = THROWS + 1; BALLS = BALLS - 1 end }
ctx = { hunt = function() return true end, choose_action = function() return true end,
        wait_until = function(pred) return true end, bag_input_ready = function() return true end,
        await_turn = function() return "action" end,                  -- every ball misses
        run_away = function() return RAN[1], RAN[2] end,
        last_sent = function() end, sent = function() return 0 end, party = function() return {} end }
function CATCH() local key, why = ctx.catch("t"); return key, tostring(why) end
"""


@pytest.fixture
def catch_model():
    from lupa import LuaRuntime

    runtime = LuaRuntime(unpack_returned_tuples=True)
    runtime.execute(_CATCH_MODEL)
    runtime.execute(_lua_defs(DRIVER, ["ctx.balls", "ctx.catch"]))
    return runtime


@pytest.mark.parametrize("balls, ran, want", [
    ("nil", "{ true }", "FINAL"),                           # Codex: read_balls() nil before any throw
    ("0", "{ true }", "FINAL"),                             # a fixture that starts empty
    ("2", "{ false, 'no escape' }", "FINAL"),               # exhausted, then the escape failed
    ("2", "{ true }", "CAUSE_RNG"),                         # the real RNG: two thrown, both missed
    # R4-DRIVER-2 (OMP cx-d84db30c): the 20th throw is also the LAST ball. The old exit branch
    # reported the unconditional "capture throw budget exhausted (20)" before ever checking the
    # pocket, and e2e_duo.py's classify_gen1_result only retries "hunt ended out-of-balls" --
    # every other reason is FINAL, so this run was never retried though it is pure ball RNG.
    ("20", "{ true }", "CAUSE_RNG"),                        # 20 stocked, 20 thrown, pocket empty
    ("25", "{ true }", "FINAL"),                            # budget hit with balls still left
])
def test_only_an_observed_ball_exhaustion_is_the_rng(catch_model, balls, ran, want):
    catch_model.execute(f"BALLS = {balls}; RAN = {ran}")
    key, why = catch_model.globals().CATCH()
    text = f"RESULT: FAIL (hunt ended {why})"
    assert key is None and duo.classify_gen1_result(text) == want, text
    assert duo.retryable_gen1_rng("gen3_frlg", {"a": text, "b": "RESULT: PASS (x)"}, 1, 3) == (
        want == "CAUSE_RNG")


def test_the_loop_exiting_normally_still_reaches_the_settle_and_outcome_check(catch_model):
    """R4-DRIVER-2 item 2: the model previously had no `play.in_battle`, so no case ever reached
    the post-loop code a normal ("over") exit takes -- only the mid-loop early returns were
    covered. A catch attempt that resolves before the budget must still settle and read the
    battle outcome, not report a ball-budget/RNG reason."""
    catch_model.execute("""
        BALLS = 2
        AWAIT_N = 0
        ctx.await_turn = function() AWAIT_N = AWAIT_N + 1; if AWAIT_N == 1 then return "over" end
                                     return "action" end
        play.in_battle = function() return false end
    """)
    key, why = catch_model.globals().CATCH()
    assert key is None and why == "the battle ended with outcome 1 (ctrl0=0x12345679 action_cursor=1)", why


def test_the_attempt_jitter_lands_after_go(tmp_path):
    """Codex item 4: idled before the scenario, a GO that comes later absorbs the jitter and the
    retry replays the same roll. The driver's own wait_go and scenario tail, over a fake clock:
    the first input after GO must come `requested` frames after it."""
    from lupa import LuaRuntime

    text = DRIVER.read_text(encoding="utf-8")
    head = text[text.index("function ctx.wait_until("):text.index("function ctx.linked()")]
    tail = text[text.index("local ok, pass, msg = pcall(scenario, ctx)"):]
    go = (tmp_path / "go.txt").as_posix()
    lua = LuaRuntime(unpack_returned_tuples=True)
    lua.execute(f"""
        FRAME, LOGS, FIRST_INPUT = 0, {{}}, nil
        D = {{ idle_jitter = 37, attempt = 2, go_file = "{go}" }}
        emu = {{ frameadvance = function()
            FRAME = FRAME + 1
            if FRAME == 50 then local f = io.open(D.go_file, "w"); f:write("GO\\n"); f:close() end
        end }}
        log, fmt, writes, FINISHED = function(s) LOGS[#LOGS + 1] = s end, string.format, 0, "F"
        SP = {{ PC = {{ dump = function() return "" end }} }}
        finish = function() end
        ctx = {{}}
    """)
    lua.execute(head + "\nscenario = function(c) c.wait_go(); FIRST_INPUT = FRAME; return true, 'ok' end\n"
                + tail)
    g = lua.globals()
    assert g.FIRST_INPUT == 50 + 37, g.FIRST_INPUT
    assert "JITTER requested=37 applied=37 attempt=2" in list(g.LOGS.values())
    assert duo.jitter_problems("\n".join(g.LOGS.values()), 37) == []


# ── C4-6k: G4 item 2a, the write inside a Pokemon Center (whiteout_gen3 A) ─────────────────
@pytest.mark.parametrize("spec, why", [
    ({"ur_missing": "Task_UnionRoomListen"}, "Union Room background set is absent"),
    ({"write_at": "outside"}, "landed outside the Center landing tile"),
    # Codex's case (review of d199da32): the right hold reason, but the queue holds only an
    # unrelated party_mon -- the nurse's box_mon probe for K1 is not the thing being held
    ({"queue": "wrong", "why": "forbidden state: script_context_status"},
     "control: nurse: box_mon K1 is not queued"),
    ({"attempted_change": "lua:true"}, "the sink attempted a write"),
    ({"bytes_change": "lua:true"}, "the party bytes changed while held"),
    ({"unkeyed": "lua:true"}, "no write of K1's party record in the write frame"),
    ({"off_checkpoint": "lua:true"}, "the write landed off the checkpoint: cpu"),
    # E4c-2: the Emerald landing refuses an unhealed party and a write off the heal tile
    ({"emerald": "lua:true", "unhealed": "lua:true"},
     "never saw the healed party at lastHealLocation 0.10 (6,17)"),
    ({"emerald": "lua:true", "write_at": "outside"}, "landed outside the heal-location tile"),
    ({"emerald": "lua:true", "off_checkpoint": "lua:true"}, "the write landed off the checkpoint: cpu"),
])
def test_whiteout_a_proves_the_center_write_or_fails_by_name(lua, spec, why):
    ok, passed, msg, _ = _run_module(lua, "whiteout", "a", "initial", spec)
    assert ok and passed is False and why in msg, msg


def test_the_center_predicates_read_the_pack_union_room_set():
    """The driver's own center_predicates over the FR pack and a fake bus: all three Union Room
    tasks (FR 0x081199FC / 0x08119D34 / 0x080F8B34, Thumb |1) active -> none missing; drop one
    -> it is named. Every pack predicate is printed, "!" off its expected value."""
    from lupa import LuaRuntime

    cp = json.loads((REPO / "data" / "games" / "gen3_frlg" / "write_checkpoint.json").read_text(
        encoding="utf-8"))["firered"]
    lua = LuaRuntime(unpack_returned_tuples=True)
    lua.execute(_lua_defs(DRIVER, ["center_predicates"]) + "\nCENTER_PREDICATES = center_predicates")
    tasks = cp["tasks"]["address"]
    mem = {}
    for i, fn in enumerate((0x081199FC, 0x08119D34, 0x080F8B34)):
        mem[tasks + i * 40] = fn | 1
        mem[tasks + i * 40 + 4] = 1
    sle = cp["predicates"]["script_context_status"]
    mem[sle["address"]] = 2
    cp_lua = lua.table_from(cp, recursive=True)
    read = lua.eval("function(m) return function(a) return m[a] or 0 end end")(lua.table_from(mem))
    cpu = cp["cpu"]
    parked = lua.table_from({"R15": cpu["pc_min"], "CPSR": cpu["mode"] | (cpu["thumb"] << 5)})
    preds, missing, bad, _ = lua.globals().CENTER_PREDICATES(cp_lua, read, parked, True)
    assert list(missing.values()) == []
    assert "script_context_status=0x2," in preds and "link_players_received=0x0," in preds
    assert "callback1=0x0!" in preds                   # off CB1_Overworld on this bare bus
    assert f"cpu=[R15=0x{cpu['pc_min']:08X},CPSR=" in preds
    assert "callback1" in list(bad.values()) and "pointer:gSaveBlock1Ptr" in list(bad.values())
    # the write-time state: every predicate at its expectation, sane pointers, a parked CPU
    for pred in cp["predicates"].values():
        mem[pred["address"] + pred.get("offset", 0)] = pred["expect"]
    for i, name in enumerate(sorted(cp["pointers"])):
        mem[cp["pointers"][name]["address"]] = 0x02025000 + 0x100 * i
    def ok():
        return lua.eval("function(m) return function(a, w) return m[a] or 0 end end")(lua.table_from(mem))
    _, _, bad, ptrs = lua.globals().CENTER_PREDICATES(cp_lua, ok(), parked, True)
    assert list(bad.values()) == [], list(bad.values())
    assert ptrs["gSaveBlock1Ptr"] == mem[cp["pointers"]["gSaveBlock1Ptr"]["address"]]
    off = lua.table_from({"R15": cpu["pc_max"] + 2, "CPSR": cpu["mode"] | (cpu["thumb"] << 5)})
    _, _, bad, _ = lua.globals().CENTER_PREDICATES(cp_lua, ok(), off, False)
    assert list(bad.values()) == ["cpu"]
    _, _, bad, _ = lua.globals().CENTER_PREDICATES(cp_lua, ok(), parked, None)   # no verdict: bad
    assert list(bad.values()) == ["cpu"]
    mem[tasks + 2 * 40 + 4] = 0                         # Task_UnionRoomListen no longer active
    _, missing, _, _ = lua.globals().CENTER_PREDICATES(cp_lua, ok(), parked, True)
    assert list(missing.values()) == ["Task_UnionRoomListen"]


def test_the_center_predicates_read_the_rr_pack_pointers_literally_and_by_reference():
    """Finding 5 (G5-RR-FOLLOW-HARDEN): mirrors test_the_center_predicates_read_the_pack_
    union_room_set, over the REAL Radical Red pack. RR's write_checkpoint.json adds
    pokemon_storage_base = 0x02029314, the storage address itself (profile.ram.
    POKEMON_STORAGE_BASE) -- not a pointer TO the storage like gPokemonStoragePtr/
    gSaveBlock1Ptr/gSaveBlock2Ptr are. center_predicates must take it literally (the same rule
    lua/gen3/safety.lua's own pointers() applies) while still dereferencing every other pointer
    through the bus."""
    from lupa import LuaRuntime

    cp = json.loads((REPO / "data" / "games" / "gen3_rr" / "write_checkpoint.json").read_text(
        encoding="utf-8"))["radical_red"]
    lua = LuaRuntime(unpack_returned_tuples=True)
    lua.execute(_lua_defs(DRIVER, ["center_predicates"]) + "\nCENTER_PREDICATES = center_predicates")
    tasks = cp["tasks"]["address"]
    live_tasks = cp["tasks"]["allowed_overworld_tasks"]
    mem = {}
    for i, name in enumerate(("Task_InitUnionRoom", "Task_SearchForChildOrParent", "Task_UnionRoomListen")):
        mem[tasks + i * 40] = live_tasks[name] | 1
        mem[tasks + i * 40 + 4] = 1
    for pred in cp["predicates"].values():
        mem[pred["address"] + pred.get("offset", 0)] = pred["expect"]
    storage_addr = cp["pointers"]["pokemon_storage_base"]["address"]
    assert storage_addr == 0x02029314
    for i, name in enumerate(sorted(cp["pointers"])):
        # A DECOY at pokemon_storage_base's own "address" slot: if center_predicates ever read
        # the bus there instead of taking the address literally, this decoy surfaces in
        # ptrs["pokemon_storage_base"] below and the assertion catches it.
        mem[cp["pointers"][name]["address"]] = (
            0x02039DE0 + i if name == "pokemon_storage_base" else 0x02025000 + 0x100 * i)
    cp_lua = lua.table_from(cp, recursive=True)
    read = lua.eval("function(m) return function(a) return m[a] or 0 end end")(lua.table_from(mem))
    cpu = cp["cpu"]
    parked = lua.table_from({"R15": cpu["pc_min"], "CPSR": cpu["mode"] | (cpu["thumb"] << 5)})
    _, missing, bad, ptrs = lua.globals().CENTER_PREDICATES(cp_lua, read, parked, True)
    assert list(missing.values()) == []
    assert list(bad.values()) == [], list(bad.values())
    # taken literally: the address itself, never the decoy sitting at that address on the bus
    assert ptrs["pokemon_storage_base"] == storage_addr
    for name in ("gPokemonStoragePtr", "gSaveBlock1Ptr", "gSaveBlock2Ptr"):
        assert ptrs[name] == mem[cp["pointers"][name]["address"]]
        assert ptrs[name] != cp["pointers"][name]["address"]


def test_whiteout_oracle_requires_the_center_receipt(monkeypatch, tmp_path):
    fixture = _fixture([STARTER, PIDGEY])
    saved = _saved(fixture, 3, [STARTER, PIDGEY])
    k = _key(PIDGEY)
    run, _ = _oracle_stub(monkeypatch, tmp_path, "whiteout_gen3", {"a": saved, "b": saved},
                          fixture, [{"a": {"key": k}, "b": {"key": k}, "status": "alive"}])
    run._link_keys = {"a": k, "b": k}
    receipts = _whiteout_receipts(k)
    for marker in ("CENTER_STATE", "WRITE_IN_CENTER", "CONTROL_REFUSED"):
        cut = "\n".join(line for line in receipts["a"].splitlines() if not line.startswith(marker))
        with pytest.raises(RuntimeError, match=marker):
            run.assert_whiteout_gen3_saved(dict(receipts, a=cut))
    # the held box_mon landing (an ACK) is a failed control, not a pass
    landed = dict(receipts, a=receipts["a"] + f"TX stats_cache {k} {{}}\n")
    with pytest.raises(RuntimeError, match="stats_cache"):
        run.assert_whiteout_gen3_saved(landed)


def _emerald_whiteout_receipts(k, tile="map=0.10 at=(6,17)"):
    a = (f"BOXED_OBSERVED {k} box=0:2\nTX whiteout - {{}}\n"
         f"LANDING_STATE {tile} frame=9440 tasks=[] preds=[] healed=A=20/20 overworld_writes_before=0\n"
         "WHITED_OUT at here\nRX rebuild_start text=REBUILDING\n"
         f"RX party_mon key={k}\nTX sync_retrieve_done {k} {{}}\nWRITE_AT_LANDING {tile} frame=9464 | keyed\n"
         f"RX rebuild_done\nRETURNED_OBSERVED {k} slot=1\nCONTROL_LIVE start_menu {k} map=0.10\n"
         f"RX box_mon key={k}\nCONTROL_REFUSED start_menu box_mon {k} clause=field_controls_locked held\n")
    b = (f"RX box_mon key={k}\nTX stats_cache {k} {{}}\nBOXED_OBSERVED {k} box=0:2\n"
         f"RX party_mon key={k}\nTX sync_retrieve_done {k} {{}}\nRETURNED_OBSERVED {k} slot=1\n")
    return {"a": a, "b": b}


def test_emerald_whiteout_oracle_requires_the_landing_receipt(monkeypatch, tmp_path):
    """E4c-2: assert_whiteout_gen3_saved on the gen3_emerald row, over the real Emerald pc fixture
    (saved == fixture: the round trip put everything back). LANDING_STATE / WRITE_AT_LANDING must
    name A's fixture lastHealLocation (0.10 (6,17)); each Emerald marker is required by name, the
    FR Center markers are not accepted in their place, and the held probe landing still fails."""
    image = (REPO / "tests/fixtures/gen3/emerald_pc.sav").read_bytes()
    k = duo.gen3_key(duo.gen3_decode(image, title="emerald")[0][1])
    run, notes = _oracle_stub(monkeypatch, tmp_path, "whiteout_gen3", {"a": image, "b": image},
                              image, [{"a": {"key": k}, "b": {"key": k}, "status": "alive"}])
    run.gcfg = dict(duo.GAMES["gen3_emerald"])
    run._link_keys = {"a": k, "b": k}
    assert run._gen3_fixture_heal_tile("a") == r"map=0\.10 at=\(6,17\)"
    receipts = _emerald_whiteout_receipts(k)
    run.assert_whiteout_gen3_saved(receipts)
    assert notes and "whiteout" in notes[-1]
    for marker in ("LANDING_STATE", "WRITE_AT_LANDING", "CONTROL_REFUSED"):
        cut = "\n".join(line for line in receipts["a"].splitlines() if not line.startswith(marker))
        with pytest.raises(RuntimeError, match=marker):
            run.assert_whiteout_gen3_saved(dict(receipts, a=cut))
    # a write anywhere but the fixture's heal tile is not the Emerald receipt
    elsewhere = dict(receipts, a=receipts["a"].replace("WRITE_AT_LANDING map=0.10 at=(6,17)",
                                                       "WRITE_AT_LANDING map=0.10 at=(6,16)"))
    with pytest.raises(RuntimeError, match="WRITE_AT_LANDING"):
        run.assert_whiteout_gen3_saved(elsewhere)
    unhealed = dict(receipts, a=receipts["a"].replace(" healed=A=20/20", ""))
    with pytest.raises(RuntimeError, match="LANDING_STATE"):
        run.assert_whiteout_gen3_saved(unhealed)
    # FR's Center receipt is not accepted on the Emerald row
    with pytest.raises(RuntimeError, match="LANDING_STATE"):
        run.assert_whiteout_gen3_saved(_whiteout_receipts(k))
    landed = dict(receipts, a=receipts["a"] + f"TX stats_cache {k} {{}}\n")
    with pytest.raises(RuntimeError, match="stats_cache"):
        run.assert_whiteout_gen3_saved(landed)


def test_the_runner_queues_the_control_write_only_after_a_parks():
    body = REPO / "tools" / "e2e_duo.py"
    text = body.read_text(encoding="utf-8")
    orch = text[text.index("def orchestrate_whiteout_gen3"):text.index("def orchestrate_link_gen3")]
    assert orch.index("^CONTROL_LIVE ") < orch.index('"cmd": "box_mon"')


# ── C4-6l: live whiteout_gen3 r3 (123c6c45) ─────────────────────────────────────────────────
def test_whiteout_oracle_takes_the_servers_party_mon_first_order(monkeypatch, tmp_path):
    """The server's rebuild reply is [party_mon..., rebuild_start] (state.py
    _queue_rebuild_commands); the live A logged RX party_mon before RX rebuild_start, and the
    oracle refused it. rebuild_start still has to answer the whiteout and precede rebuild_done."""
    fixture = _fixture([STARTER, PIDGEY])
    saved = _saved(fixture, 3, [STARTER, PIDGEY])
    k = _key(PIDGEY)
    run, _ = _oracle_stub(monkeypatch, tmp_path, "whiteout_gen3", {"a": saved, "b": saved},
                          fixture, [{"a": {"key": k}, "b": {"key": k}, "status": "alive"}])
    run._link_keys = {"a": k, "b": k}
    receipts = _whiteout_receipts(k)
    start = "RX rebuild_start text=REBUILDING\n"
    party = f"RX party_mon key={k}\n"
    live = dict(receipts, a=receipts["a"].replace(start + party, party + start))
    assert live["a"] != receipts["a"]
    run.assert_whiteout_gen3_saved(live)
    late = dict(receipts, a=receipts["a"].replace(start, "").replace(
        "RX rebuild_done\n", "RX rebuild_done\n" + start))
    with pytest.raises(RuntimeError, match="rebuild_start"):
        run.assert_whiteout_gen3_saved(late)



# ── C4-6m: Codex review of d199da32 + the r4 addendum ──────────────────────────────────────
def test_whiteout_oracle_binds_the_control_to_the_linked_key(monkeypatch, tmp_path):
    fixture = _fixture([STARTER, PIDGEY])
    saved = _saved(fixture, 3, [STARTER, PIDGEY])
    k = _key(PIDGEY)
    run, _ = _oracle_stub(monkeypatch, tmp_path, "whiteout_gen3", {"a": saved, "b": saved},
                          fixture, [{"a": {"key": k}, "b": {"key": k}, "status": "alive"}])
    run._link_keys = {"a": k, "b": k}
    receipts = _whiteout_receipts(k)
    other = dict(receipts, a=receipts["a"].replace(f"CONTROL_REFUSED nurse box_mon {k} ",
                                                   "CONTROL_REFUSED nurse box_mon 00000000:00000000 "))
    with pytest.raises(RuntimeError, match="CONTROL_REFUSED"):
        run.assert_whiteout_gen3_saved(other)


def test_deadzone_oracle_takes_one_dead_zone_row_per_player(monkeypatch, tmp_path):
    """server.py:2217-2220 logs dead_zone for the player AND the partner; live deadzone_gen3 r4
    (684bbb7a) passed both halves and PYDEC refused its two rows."""
    run = _oracle_run("deadzone_gen3")
    run._deadzone_b_key = "KB"
    run._gen3_flush_boundary = lambda: None
    run._status = lambda: {"area_states": {"route_1": "dead_zone"}}
    run._links_json = lambda: []
    run._gen3_saved = lambda inst: ([], {})
    run._gen3_fixture_saved = lambda inst: ([], {})
    run._gen3_memorial_box = lambda: 13
    run._gen3_limits = lambda inst: LIMITS
    monkeypatch.setattr(duo, "gen3_memorial_problems", lambda *a, **k: [])
    notes = []
    run._pydec_note = notes.append
    rows = [{"type": "dead_zone", "player": "a", "area_id": "route_1"},
            {"type": "dead_zone", "player": "b", "area_id": "route_1"}]
    run._reconnect_events = lambda: rows
    receipts = {"a": 'TX no_catch - {"area_id":"route_1","species_id":16}\n',
                "b": "FAINTED KB frame=1\nRX memorialize key=KB\nTX memorialize_done KB {}\n"}
    run.assert_deadzone_gen3_saved(receipts)
    assert notes and "deadzone" in notes[-1]
    run._reconnect_events = lambda: rows[:1]
    with pytest.raises(RuntimeError, match="dead_zone rows per player"):
        run.assert_deadzone_gen3_saved(receipts)


def _center_controls_receipt(k):
    return ("WITNESS cable_welcome_message script=CableClub_EventScript_WelcomeToCableClub at=scriptPtr "
            "var_result=0 adapter_connected=false(observed: IsWirelessAdapterConnected's VAR_RESULT)\n"
            f"CONTROL_LIVE cable_welcome_message {k} map=5.5\nRX box_mon key={k}\n"
            f"CONTROL_REFUSED cable_welcome_message box_mon {k} clause=script_context_status held\n"
            f"CONTROL_LIVE cable_save {k} map=5.5\n"
            f"CONTROL_REFUSED cable_save box_mon {k} clause=task held\n"
            "SAVE_WITNESS_DUMP path=p bytes=1 saves=1 frame=1 counter=5\n"
            f"CONTROL_LIVE cable_link {k} map=5.5\n"
            f"CONTROL_REFUSED cable_link box_mon {k} clause=task held\n"
            "CABLE_CALLBACK_NULL limit=no-cable-partner held_frames=601 open_frames=601 null_frames=0 "
            "callback=0x0800A721:LinkCB_RequestPlayerDataExchange\n"
            f"CONTROL_RELEASED cable_link box_mon {k}\nTX stats_cache {k} {{}}\n"
            f"BOXED_OBSERVED {k} box=0:0\nCONTROL_SETTLED cable_link box_mon {k}\n"
            "WITNESS union_room_attendant script=CableClub_EventScript_UnionRoomAdapterNotConnected "
            "at=stack[0] var_result=0 adapter_connected=false(observed: IsWirelessAdapterConnected's "
            "VAR_RESULT)\n"
            f"CONTROL_LIVE union_room_attendant {k} map=5.5\nRX party_mon key={k}\n"
            f"CONTROL_REFUSED union_room_attendant party_mon {k} clause=field_controls_locked held\n"
            f"CONTROL_RELEASED union_room_attendant party_mon {k}\nTX sync_retrieve_done {k} {{}}\n"
            f"RETURNED_OBSERVED {k} slot=1\nCONTROL_SETTLED union_room_attendant party_mon {k}\n")


def test_center_controls_oracle_positive_and_negatives(monkeypatch, tmp_path):
    fixture = _fixture([STARTER, PIDGEY])
    saved = _saved(fixture, 3, [STARTER, PIDGEY])
    k = _key(PIDGEY)
    run, notes = _oracle_stub(monkeypatch, tmp_path, "center_controls_gen3", {"a": saved, "b": saved},
                              fixture, [{"a": {"key": k}, "b": {"key": k}, "status": "alive"}])
    run._link_keys = {"a": k, "b": k}
    good = {"a": _center_controls_receipt(k), "b": ""}
    run.assert_center_controls_gen3_saved(good)
    assert notes and "center_controls" in notes[-1]
    # the probe arriving BEFORE the state it is supposed to test proves nothing
    early = f"RX box_mon key={k}\n" + good["a"].replace(f"RX box_mon key={k}\n", "")
    with pytest.raises(RuntimeError, match="CONTROL_LIVE cable_welcome_message"):
        run.assert_center_controls_gen3_saved(dict(good, a=early))
    with pytest.raises(RuntimeError, match="CONTROL_RELEASED union_room_attendant"):
        run.assert_center_controls_gen3_saved(dict(good, a=good["a"].replace(
            f"CONTROL_RELEASED union_room_attendant party_mon {k}\n", "")))
    # Codex REV-center-receipt-2: the release boundary logged AFTER the ACK is refused
    late = good["a"].replace(f"CONTROL_RELEASED cable_link box_mon {k}\n", "").replace(
        f"BOXED_OBSERVED {k} box=0:0\n", f"BOXED_OBSERVED {k} box=0:0\nCONTROL_RELEASED cable_link box_mon {k}\n")
    with pytest.raises(RuntimeError, match="CONTROL_RELEASED cable_link"):
        run.assert_center_controls_gen3_saved(dict(good, a=late))
    # rows 3/6 (C4-SAVE-ROWS): the Cable Club save is its own refusal, before the save lands
    with pytest.raises(RuntimeError, match="cable_save"):
        run.assert_center_controls_gen3_saved(dict(good, a=good["a"].replace(
            f"CONTROL_REFUSED cable_save box_mon {k} clause=task held\n", "")))
    save_first = good["a"].replace("SAVE_WITNESS_DUMP path=p bytes=1 saves=1 frame=1 counter=5\n", "").replace(
        f"CONTROL_LIVE cable_save {k}", f"SAVE_WITNESS_DUMP path=p bytes=1 saves=1 frame=1 counter=5\nCONTROL_LIVE cable_save {k}")
    with pytest.raises(RuntimeError, match="SAVE_WITNESS_DUMP"):
        run.assert_center_controls_gen3_saved(dict(good, a=save_first))
    # row 6's line is judged field by field (REV-PROBE2-SAVEROWS B-2): see test_row6_oracle_*
    witnessed = good["a"].replace(
        "CABLE_CALLBACK_NULL limit=no-cable-partner held_frames=601 open_frames=601 null_frames=0 "
        "callback=0x0800A721:LinkCB_RequestPlayerDataExchange",
        "CABLE_CALLBACK_NULL sLinkOpen=1 gLinkCallback=0 held_frames=601 open_frames=601 null_frames=4")
    run.assert_center_controls_gen3_saved(dict(good, a=witnessed))


def test_the_lgfr_row_is_the_frlg_family_with_leafgreen_as_a():
    row = duo.GAMES["gen3_lgfr"]
    assert row["game"] == "gen3_frlg" and row["main"] == duo.GAMES["gen3_frlg"]["main"]
    assert row["sides"]["a"][0] == "leafgreen" and row["sides"]["b"][0] == "firered"
    assert duo.scenario_applies("whiteout_gen3", "gen3_lgfr")
    assert duo.scenario_applies("center_controls_gen3", "gen3_lgfr")
    for _title, stem in row["sides"].values():
        for target in ("battle", "town"):
            assert (REPO / "tests" / "fixtures" / "gen3" / (stem.format(target=target) + ".sav")).is_file()


def test_the_runner_records_rom_pack_source_and_fixture_identity(monkeypatch, tmp_path):
    run = _oracle_run("whiteout_gen3")
    run.gcfg = dict(duo.GAMES["gen3_frlg"])
    rom, fix = tmp_path / "rom.gba", tmp_path / "fix.sav"
    rom.write_bytes(b"rom")
    fix.write_bytes(b"fix")
    monkeypatch.setattr(run, "_gen3_rom", lambda inst: str(rom))
    monkeypatch.setattr(run, "_gen3_fixture_path", lambda inst: str(fix))
    line = run._gen3_identity()
    assert re.match(r"IDENTITY a=firered:rom=[0-9a-f]{64}:fixture=[0-9a-f]{64} "
                    r"b=leafgreen:rom=[0-9a-f]{64}:fixture=[0-9a-f]{64} "
                    r"write_checkpoint\.json=[0-9a-f]{64} profile\.json=[0-9a-f]{64} "
                    r"source=[0-9a-f]{40}(\+dirty dirty=\[.+\])?$", line), line
    # C4-6n: every Gen 3 scenario's receipt carries it -- run() writes it for any battery row
    body = (REPO / "tools" / "e2e_duo.py").read_text(encoding="utf-8")
    run_body = body[body.index("    def run(self):"):]
    run_body = run_body[:run_body.index("\n    def ", 10) if "\n    def " in run_body[10:] else None]
    assert "if self.is_gen3_battery:\n                # every Gen 3 receipt names its own cut" in run_body
    assert "self._pydec_note(self._gen3_identity())" in run_body


def test_the_center_controls_runner_queues_each_probe_after_its_marker():
    body = (REPO / "tools" / "e2e_duo.py").read_text(encoding="utf-8")
    orch = body[body.index("def orchestrate_center_controls_gen3"):body.index("def orchestrate_whiteout_gen3")]
    assert orch.index("CONTROL_LIVE cable_welcome_message") < orch.index('"cmd": "box_mon"')
    assert orch.index("CONTROL_LIVE union_room_attendant") < orch.index('"cmd": "party_mon"')
    assert "stats_cache" in orch


# ── C4-6n: self-proving receipts (Codex receipt audit 2026-09-23) ────────────────────────────
def test_the_oracle_writes_the_markers_it_consumed_into_the_receipt(monkeypatch, tmp_path):
    fixture = _fixture([STARTER, PIDGEY])
    saved = _saved(fixture, 3, [STARTER, PIDGEY])
    k = _key(PIDGEY)
    run, notes = _oracle_stub(monkeypatch, tmp_path, "whiteout_gen3", {"a": saved, "b": saved},
                              fixture, [{"a": {"key": k}, "b": {"key": k}, "status": "alive"}])
    run._link_keys = {"a": k, "b": k}
    run.cfg = dict(duo.SCENARIOS["whiteout_gen3"])
    run.game = "gen3_frlg"
    monkeypatch.setattr(run, "check_save_witness_gen3", lambda results: None)
    run._run_oracle(_whiteout_receipts(k))
    markers = [n for n in notes if n.startswith("MARKER ")]
    assert f"MARKER a: CONTROL_REFUSED nurse box_mon {k} clause=field_controls_locked held" in markers
    assert f"MARKER b: RX box_mon key={k}" in markers
    assert "MARKER a: CENTER_STATE map=5.4 at=(7,4)" in markers
    assert duo._CONSUMED_MARKERS is None


def test_each_sides_raw_result_line_is_kept(monkeypatch):
    run = _oracle_run("link_gen3")
    run.gcfg = dict(duo.GAMES["gen3_frlg"])
    notes = []
    run._pydec_note = notes.append
    run._note_result_lines({"a": "X\nRESULT: PASS (caught K9)\n", "b": "no result here\n"})
    assert notes == ["RESULT_LINE a: RESULT: PASS (caught K9)", "RESULT_LINE b: (none)"]
    body = (REPO / "tools" / "e2e_duo.py").read_text(encoding="utf-8")
    run_body = body[body.index("    def run(self):"):body.index("\ndef list_lines(")]
    # both exits keep them: the double-RESULT path and the early-finish path
    assert run_body.count("self._note_result_lines(") == 2


def test_reconnect_oracle_requires_the_attempted_zero_witness(monkeypatch, tmp_path):
    codec = duo.gen3_codec()
    image = _fixture([STARTER, PIDGEY])
    body = codec.split_rtc(image)[0]
    run = _oracle_run("reconnect_gen3")
    run.gcfg = dict(duo.GAMES["gen3_frlg"])
    run._live_complete = {"reconnect_gen3": True}
    run._gen3_flush_boundary = lambda: None
    run._gen3_one_link = lambda status, cause=None: {}
    same = tmp_path / "same.sav"
    same.write_bytes(image)
    run._same_save_artifact = str(same)
    run._artifact = lambda path, what: Path(path)
    run._gen3_before_kill = image
    run._gen3_wrong_body = body
    run._gen3_flushed = lambda inst: image
    run._gen3_saved = lambda inst: ([PIDGEY_B], {})
    run._link_keys = {"a": _key(PIDGEY), "b": _key(PIDGEY_B)}
    notes = []
    run._pydec_note = notes.append
    zero = "WRITES 0\nWRONG_SAVE_ZERO attempted=0 writes=0 party=unchanged box=unchanged\n"
    run.assert_reconnect_gen3_saved({"a": zero, "b": ""})
    assert notes and "reconnect" in notes[-1]
    with pytest.raises(RuntimeError, match="WRONG_SAVE_ZERO"):
        run.assert_reconnect_gen3_saved({"a": "WRITES 0\n", "b": ""})


# ── C4-6n addendum: Codex REV-center-receipt-2 ──────────────────────────────────────────────
def test_the_center_controls_emitter_satisfies_its_own_oracle_chain(lua):
    """Producer -> consumer: the REAL scenario's emitted receipt (under the fake ctx, which
    echoes the driver tee's RX/TX/DUMP lines) against the oracle's own chain."""
    ok, passed, msg, logs = _run_module(lua, "center_controls", "a", "initial", {})
    assert ok and passed is True, msg
    chain = duo.center_controls_chain("K1")
    problems = duo.gen3_receipt_problems("a", logs, required=chain,
                                         ordered=list(zip(chain, chain[1:], strict=False)))
    assert problems == [], (problems, logs)


def test_center_controls_needs_its_source_pinned_witness(lua):
    ok, passed, msg, _ = _run_module(lua, "center_controls", "a", "initial", {"no_witness": "lua:true"})
    assert ok and passed is False and "(the no-adapter branch)" in msg, msg


_HOLD_MODEL = r"""
FRAME, FLIP, DURING_RX, ATT, RX, LIVE = 0, nil, false, 0, 0, true
ITEMS = { { cmd = "box_mon", key = "K" } }
session = { deferred = { items = ITEMS, exec = { write_count = function() return ATT end } } }
function session.deferred:pending() return #self.items, "x.lua:73: forbidden state: script_context_status" end
emu = { framecount = function() return FRAME end }
G, cp, fmt = { pred_ok = function() return false end }, {}, string.format
ctx = { log = function() end, writes = function() return 0 end, center_state = function() return "s" end,
        mutable_bytes = function() return "P", "B" end, received = function() return RX end,
        wait_until = function(pred)
            if DURING_RX then ATT = ATT + 1 end        -- a byte attempted while the probe is in flight
            RX = RX + 1
            return pred()
        end,
        frames = function(n) FRAME = FRAME + n; if FRAME == 600 and FLIP then FLIP() end end }
function PROBE() local c, why = ctx.hold_probe("t", "box_mon", "K", function() return LIVE end, 600)
    return c, tostring(why) end
"""


@pytest.fixture
def hold_model():
    from lupa import LuaRuntime

    text = DRIVER.read_text(encoding="utf-8")
    runtime = LuaRuntime(unpack_returned_tuples=True)
    runtime.execute(_HOLD_MODEL)
    defs = [re.search(_LUA_DEF.format(re.escape(n)), text, re.M | re.S).group(0)
            for n in ("queued_entry", "ctx.queued", "ctx.hold_probe")]
    defs.append(re.search(r"^function ctx\.attempted\(\) .* end$", text, re.M).group(0))
    runtime.execute("\n".join(defs))
    return runtime


@pytest.mark.parametrize("flip, why", [
    ("function() ATT = ATT + 1 end", "the sink attempted a write"),
    ("function() table.remove(ITEMS, 1) end", "box_mon K is not queued (frame 600)"),
    ("function() LIVE = false end", "the refusing state ended by frame 600"),
])
def test_hold_probe_samples_the_final_frame(hold_model, flip, why):
    """Codex executed each of these ON frame 600; the old before-advance checks returned success."""
    hold_model.execute(f"FLIP = {flip}")
    clause, msg = hold_model.globals().PROBE()
    assert clause is None and why in msg, msg


def test_hold_probe_counts_attempts_made_while_the_probe_is_delivered(hold_model):
    hold_model.execute("DURING_RX = true")
    clause, msg = hold_model.globals().PROBE()
    assert clause is None and "the sink attempted a write" in msg, msg


def test_hold_probe_positive_control(hold_model):
    clause, _ = hold_model.globals().PROBE()
    assert clause == "script_context_status"


def _identity_run(monkeypatch, tmp_path, status="", fail=None):
    run = _oracle_run("whiteout_gen3")
    run.gcfg = dict(duo.GAMES["gen3_frlg"])
    rom = tmp_path / "rom.gba"
    rom.write_bytes(b"rom")
    monkeypatch.setattr(run, "_gen3_rom", lambda inst: str(rom))
    monkeypatch.setattr(run, "_gen3_fixture_path", lambda inst: str(rom))

    class Proc:
        def __init__(self, out, code=0):
            self.stdout, self.stderr, self.returncode = out, "boom", code

    def fake_run(cmd, **_kwargs):
        if fail and fail in cmd:
            return Proc("", 128)
        return Proc("a" * 40 + "\n" if "rev-parse" in cmd else status)
    monkeypatch.setattr(duo.subprocess, "run", fake_run)
    return run


def test_identity_marks_any_tracked_change_dirty_and_the_oracle_rejects_it(monkeypatch, tmp_path):
    run = _identity_run(monkeypatch, tmp_path, status=" M server/state.py\0 M tests/fixtures/gen3/wire/x.jsonl\0")
    line = run._gen3_identity()
    assert line.endswith("source=" + "a" * 40 + "+dirty dirty=[server/state.py]"), line
    run.cfg = dict(duo.SCENARIOS["whiteout_gen3"])
    run.game = "gen3_frlg"
    monkeypatch.setattr(run, "check_save_witness_gen3", lambda results: None)
    with pytest.raises(RuntimeError, match=r"\+dirty \(server/state.py\)"):
        run._run_oracle({"a": "", "b": ""})
    clean = _identity_run(monkeypatch, tmp_path, status=" M tests/fixtures/gen3/wire/x.jsonl\0")
    assert clean._gen3_identity().endswith("source=" + "a" * 40)
    assert clean._gen3_source_dirty == []


@pytest.mark.parametrize("step", ["rev-parse", "status"])
def test_identity_fails_closed_on_a_git_error(monkeypatch, tmp_path, step):
    run = _identity_run(monkeypatch, tmp_path, fail=step)
    with pytest.raises(RuntimeError, match="IDENTITY: git .* failed"):
        run._gen3_identity()


# ── C4-6o: live r6 at 059da756 ──────────────────────────────────────────────────────────────
def test_the_whiteout_hooks_catch_a_rebuild_that_lands_inside_the_walk(lua):
    """gen3_lgfr r6: the lone starter fainted in an incidental battle ON the walk; playlib settled
    the whiteout and the heal script (the rebuild wrote at frame 16729) before it raised at
    16793, and the write hook -- armed after -- saw nothing. Armed before the walk, both the
    landing sample and the write frame are caught."""
    ok, passed, msg, logs = _run_module(lua, "whiteout", "a", "initial", {})
    assert ok and passed is True, msg
    assert "CENTER_STATE map=5.4 at=(7,4)" in logs and "overworld_writes_before=0" in logs
    assert "WRITE_IN_CENTER map=5.4 at=(7,4)" in logs and "record=0x020242E8+100@7" in logs


@pytest.mark.parametrize("teala, path, marker", [
    (1, "FOLLOW center2f_counter_to_direct_corner", "TEALA_TUTORIAL var=1->2 at=(2,4)"),
    (2, "FOLLOW center2f_to_direct_corner", "TEALA_TUTORIAL var=2 skipped"),
])
def test_center_controls_waits_out_the_2f_tutorial(lua, teala, path, marker):
    """FR r6 stalled on "step Up" at (2,6): VAR_MAP_SCENE_POKEMON_CENTER_TEALA (0x407C) is 1
    after the Pokedex, so CableClub_OnFrame runs the tutorial (lockall, walk_up x2 to (2,4))."""
    ok, passed, msg, logs = _run_module(lua, "center_controls", "a", "initial", {"teala": teala})
    assert ok and passed is True, msg
    assert path in logs and marker in logs, logs


def test_the_tutorial_var_and_its_path_are_pret_facts():
    from gba_map import load

    rom = REPO / "patch" / "build" / "gen3_Pokemon_-_FireRed_Version_(USA).gba"
    if not rom.is_file():
        pytest.skip("no staged FR ROM")
    import hashlib

    import gen3_final_cut
    want = gen3_final_cut.rom_pins(str(REPO))["firered"]   # absent skips; present-but-wrong fails
    assert hashlib.sha1(rom.read_bytes()).hexdigest() == want, f"{rom} is not the pinned FR {want[:8]}"
    m = load(str(rom), sym_path=str(REPO / "data" / "gen3" / "pret" / "pokefirered.sym")).map(5, 5)
    assert m.bfs((2, 4), (10, 4)) == ["Right"] * 8
    text = (REPO / "lua" / "tests" / "duo" / "scenario_gen3_center_controls.lua").read_text(encoding="utf-8")
    assert "VAR_MAP_SCENE_POKEMON_CENTER_TEALA = 0x407C" in text


# ── C4-6o addendum: Codex on 86245d1e ───────────────────────────────────────────────────────
def test_a_long_marker_survives_whole_into_the_receipt(monkeypatch, tmp_path):
    fixture = _fixture([STARTER, PIDGEY])
    saved = _saved(fixture, 3, [STARTER, PIDGEY])
    k = _key(PIDGEY)
    run, notes = _oracle_stub(monkeypatch, tmp_path, "whiteout_gen3", {"a": saved, "b": saved},
                              fixture, [{"a": {"key": k}, "b": {"key": k}, "status": "alive"}])
    run._link_keys = {"a": k, "b": k}
    run.cfg = dict(duo.SCENARIOS["whiteout_gen3"])
    run.game = "gen3_frlg"
    monkeypatch.setattr(run, "check_save_witness_gen3", lambda results: None)
    preds = ",".join(f"pred{i}=0x{i:X}" for i in range(40)) + ",soft_reset_disabled=0x0"
    keyed = f" | keyed {k} slot=1 record=0x020242E8+100@7 | ack map=5.4 at=(7,4) frame=8"
    receipts = _whiteout_receipts(k)
    long_write = f"WRITE_IN_CENTER map=5.4 at=(7,4) frame=7 preds=[{preds}]{keyed}"
    assert len(long_write) > 400
    receipts["a"] = receipts["a"].replace("WRITE_IN_CENTER map=5.4 at=(7,4)\n", long_write + "\n")
    run._run_oracle(receipts)
    assert f"MARKER a: {long_write}" in notes, [n for n in notes if "WRITE_IN_CENTER" in n]


@pytest.mark.parametrize("status, dirty", [
    # a move OUT of the excluded wire dir into server/: both ends reported, dirty
    ("R  server/new.py\0tests/fixtures/gen3/wire/old.jsonl\0", ["server/new.py", "tests/fixtures/gen3/wire/old.jsonl"]),
    # a move INTO the wire dir from server/: dirty too
    ("R  tests/fixtures/gen3/wire/new.jsonl\0server/old.py\0", ["tests/fixtures/gen3/wire/new.jsonl", "server/old.py"]),
    # both ends inside the wire dir: excluded
    ("R  tests/fixtures/gen3/wire/b.jsonl\0tests/fixtures/gen3/wire/a.jsonl\0", []),
    # -z never quotes: a name with a space is one path
    ("?? lua/tests/duo/my scenario.lua\0 M tools/e2e_duo.py\0", ["lua/tests/duo/my scenario.lua", "tools/e2e_duo.py"]),
    ("", []),
])
def test_gen3_dirty_paths_parses_porcelain_z_renames_both_ends(status, dirty):
    assert duo.gen3_dirty_paths(status) == dirty


def test_identity_asks_git_for_z_records_and_submodules(monkeypatch, tmp_path):
    seen = []
    run = _identity_run(monkeypatch, tmp_path)
    real = duo.subprocess.run

    def spy(cmd, **kw):
        seen.append(cmd)
        return real(cmd, **kw)
    monkeypatch.setattr(duo.subprocess, "run", spy)
    run._gen3_identity()
    status = [c for c in seen if "status" in c][0]
    assert "-z" in status and "--porcelain=v1" in status and "--ignore-submodules=none" in status


# ── C4-6p: live r7 at fb255a05, FR and LG ───────────────────────────────────────────────────
def test_center_controls_proves_it_faces_the_counter_before_talking(lua):
    """r7: the follow returns at the START of its last Right step; the old single Up tap landed
    inside that walk, was dropped, and A met the empty tile to the east -- "cable_menu: the
    attendant's script never started" on both titles. ctx.face waits the step out and reads the
    facing nibble back before A."""
    ok, passed, msg, logs = _run_module(lua, "center_controls", "a", "initial", {})
    assert ok and passed is True, msg
    assert "TALK cable_welcome_message at=" in logs and "facing=2 idle=true" in logs, logs
    assert "TALK union_room_attendant" in logs


_FACE_MODEL = r"""
FLAGS, BYTE18, TAPS = 0x41, 0x44, {}        -- mid-step (heldMovementActive, not finished), facing right
S = { gObjectEvents = 0x100 }
memory = { read_u8 = function(a) if a == 0x100 then return FLAGS end return BYTE18 end }
G = { tap = function(btn) TAPS[#TAPS + 1] = btn; if FLAGS == 0xC1 and btn == "Up" then BYTE18 = 0x22 end end }
ctx = { frames = function() FLAGS = 0xC1 end }
"""


def test_face_reads_the_low_nibble_and_waits_out_the_step():
    from lupa import LuaRuntime

    text = DRIVER.read_text(encoding="utf-8")
    lua = LuaRuntime(unpack_returned_tuples=True)
    lua.execute(_FACE_MODEL)
    body = text[text.index("local FACING = {"):text.index("--- Is the pret function `name`")]
    lua.execute(body)
    assert lua.eval("ctx.facing()") == 4                  # low nibble of 0x44: right
    assert lua.eval("ctx.face('Up')") is True
    assert list(lua.globals().TAPS.values()) == ["Up"]     # one tap, AFTER the step ended
    lua.execute("BYTE18 = 0x42")                          # movementDirection 4, facingDirection 2
    assert lua.eval("ctx.facing()") == 2


# ── C4-6q: live r8 at f5bdbdfc, FR and LG ───────────────────────────────────────────────────
def test_cable_menu_parks_at_the_welcome_paragraph_and_observes_the_adapter(lua):
    """r8: 240 frames after the talk the Direct Corner script sat at the \\p of the welcome text
    (waitmessage waits for A), never inside SelectCableClubRoom -- "not parked at the Cable Club
    service multichoice" on both titles. The witness is now that wait itself, and VAR_RESULT
    there (IsWirelessAdapterConnected's return) is read, not inferred."""
    ok, passed, msg, logs = _run_module(lua, "center_controls", "a", "initial", {})
    assert ok and passed is True, msg
    assert ("WITNESS cable_welcome_message script=CableClub_EventScript_WelcomeToCableClub at=scriptPtr var_result=0 "
            "adapter_connected=false") in logs, logs
    assert "var_result=0 adapter_connected=false" in logs.split("WITNESS union_room_attendant")[1]


def test_an_adapter_that_reports_connected_is_a_named_failure(lua):
    ok, passed, msg, _ = _run_module(lua, "center_controls", "a", "initial", {"var_result": 1})
    assert ok and passed is False and "IsWirelessAdapterConnected returned 1" in msg, msg


def test_the_welcome_block_is_where_pret_puts_the_wait():
    """cable_club.inc: WelcomeToCableClub = message(5) waitmessage(1) delay(3) goto(5) end(1), and
    the next label is UnusedWelcomeToCableClub -- 15 bytes on both titles."""
    for title in ("firered", "leafgreen"):
        syms = {}
        for line in (REPO / "data" / "gen3" / "pret" / f"poke{title}.sym").read_text(encoding="utf-8").splitlines():
            parts = line.split()
            if len(parts) == 4:
                syms.setdefault(parts[3], int(parts[0], 16))
        span = (syms["CableClub_EventScript_UnusedWelcomeToCableClub"]
                - syms["CableClub_EventScript_WelcomeToCableClub"])
        assert span == 15, (title, span)


# ── C4-6r: live r9 at f926a8b4, FR and LG ───────────────────────────────────────────────────
def test_a_stale_checkpoint_pointer_after_release_is_a_named_product_finding(lua):
    """r9: after B cancelled the Cable Club link and the attendant's script ended (script status
    2, only the background tasks), the released box_mon stayed held on link_callback (FR) /
    save_dialog_cb (LG): LinkCB_RequestPlayerDataExchange and SaveDialogCB_ReturnSuccess, which
    pret never clears. The harness must say so, not time out as if the probe were lost."""
    ok, passed, msg, logs = _run_module(lua, "center_controls", "a", "initial", {"stale": "lua:true"})
    assert ok and passed is False and msg.startswith("PRODUCT FINDING: cable_link released to an idle field"), msg
    assert "FINDING stale_predicate cable_link box_mon link_callback=0x0800A721" in logs, logs


_STALE_MODEL = r"""
MEM = {}
S = { gLinkCallback = 0x10, sLinkOpen = 0x20, sSaveDialogCB = 0x30, task50_save_game = 0x40,
      Task_StartMenuHandleInput = 0x50, LinkCB_RequestPlayerDataExchange = 0x0800A720,
      SaveDialogCB_ReturnSuccess = 0x0806F9E0 }
LIVE = {}
function STALE() return stale_predicates(function(a) return MEM[a] or 0 end, function(a) return MEM[a] or 0 end,
                                         S, function(fn) return LIVE[fn] == true end) end
"""


@pytest.fixture
def stale_model():
    from lupa import LuaRuntime

    lua = LuaRuntime(unpack_returned_tuples=True)
    lua.execute(_lua_defs(DRIVER, ["stale_predicates"]) + "\n" + _STALE_MODEL)
    return lua


def test_stale_predicates_names_only_pointers_whose_state_is_over(stale_model):
    lua = stale_model
    assert list(lua.globals().STALE().values()) == []
    lua.execute("MEM[0x10] = 0x0800A721; MEM[0x30] = 0x0806F9E1")        # r9: both left behind
    got = list(lua.globals().STALE().values())
    assert got == ["link_callback=0x0800A721:LinkCB_RequestPlayerDataExchange(sLinkOpen=0)",
                   "save_dialog_cb=0x0806F9E1:SaveDialogCB_ReturnSuccess(no save dialog task)"], got
    lua.execute("MEM[0x20] = 1; LIVE[0x40] = true")                     # link open, save running
    assert list(lua.globals().STALE().values()) == []


# ── C4-6s: save_then_write_gen3, the live regression for the stale sSaveDialogCB ─────────────
_HELD_ON_SAVE = 'lua:"...lua/gen3/safety.lua:73: forbidden state: save_dialog_cb"'


def test_save_then_write_emits_its_own_oracle_chain(lua):
    ok, passed, msg, logs = _run_module(lua, "save_then_write", "a", "initial", {})
    assert ok and passed is True, msg
    required, ordered = duo.save_then_write_order("K1")
    assert duo.gen3_receipt_problems("a", logs, required=required, ordered=ordered) == [], logs


def test_save_then_write_fails_by_name_while_the_pack_holds_on_save_dialog_cb(lua):
    """Today's pack: the probe stays held on save_dialog_cb; the run must say exactly that."""
    ok, passed, msg, _ = _run_module(lua, "save_then_write", "a", "initial",
                                     {"held": "lua:true", "queue": "box", "why": _HELD_ON_SAVE})
    assert ok and passed is False and "stayed HELD" in msg and "(clause save_dialog_cb:" in msg, msg


def test_save_then_write_refuses_a_run_that_does_not_exercise_the_defect(lua):
    ok, passed, msg, _ = _run_module(lua, "save_then_write", "a", "initial", {"no_stale": "lua:true"})
    assert ok and passed is False and "does not exercise the defect" in msg, msg


def _save_then_write_receipt(k):
    return ("SAVE_WITNESS_DUMP path=p bytes=1 saves=1 frame=1 counter=5\n"
            "SAVE_DISMISSAL save_then_write_1 by=a_press delay=40\n"
            "STALE_SAVE_DIALOG save_dialog_cb=0x0806F9E1:SaveDialogCB_ReturnSuccess(no save dialog task)\n"
            f"WRITE_PROBE_READY {k} map=3.1\nRX box_mon key={k}\nTX stats_cache {k} {{}}\n"
            f"BOXED_OBSERVED {k} box=0:0\nWRITE_LANDED box_mon {k} map=3.1\n"
            "SAVE_WITNESS_DUMP path=p bytes=1 saves=2 frame=2 counter=6\n"
            "SAVE_DISMISSAL save_then_write_2 by=timeout delay=0\n"
            "DIALOG_WITNESS_FALSE cursor=3 action=3 save_row=4 menu=open stale=0x0806F9E1\n"
            f"CONTROL_LIVE dialog_witness {k} map=3.1\nRX party_mon key={k}\n"
            f"CONTROL_REFUSED dialog_witness party_mon {k} clause=field_controls_locked held\n"
            f"SAVE_CANCEL_PROMPT {k} row=save prompt=overwrite\n"
            f"CONTROL_LIVE save_prompt {k} map=3.1\n"
            f"CONTROL_REFUSED save_prompt party_mon {k} clause=task held\n"
            f"SAVE_CANCEL_MENU_REDRAWN {k} cursor=4 sSaveDialogCB=0x0806F8DD\n"
            f"CONTROL_LIVE save_cancel_menu {k} map=3.1\n"
            f"CONTROL_REFUSED save_cancel_menu party_mon {k} clause=field_controls_locked held\n"
            f"CONTROL_RELEASED save_cancel party_mon {k}\n"
            "[client] [SLink-gen3] write overworld 0x020242E8 +100 frame 5626\n"
            f"TX sync_retrieve_done {k} {{}}\nSAVE_CANCEL_FIELD_FREE {k}\nRETURNED_OBSERVED {k} slot=1\n"
            f"SAVE_CANCEL_WRITE_FRAME {k} frame=5626 field_free=true start_menu_task=false\n"
            f"CONTROL_SETTLED save_cancel party_mon {k}\nSAVE_COUNTER_UNCHANGED before=6 after=6\n")


def test_save_then_write_oracle_positive_and_negatives(monkeypatch, tmp_path):
    fixture = _fixture([STARTER, PIDGEY])
    k = _key(PIDGEY)
    boxed = _saved(fixture, 5, [STARTER], {(0, 0): _mon(PIDGEY["personality"], party=False, species=16)})
    run, notes = _oracle_stub(monkeypatch, tmp_path, "save_then_write_gen3", {"a": boxed, "b": boxed},
                              fixture, [{"a": {"key": k}, "b": {"key": k}, "status": "alive"}])
    run._link_keys = {"a": k, "b": k}
    receipt = _save_then_write_receipt(k)
    run.assert_save_then_write_gen3_saved({"a": receipt, "b": ""})
    assert notes and "save_then_write" in notes[-1]
    # rows 1/9 (C4-SAVE-ROWS): the leg is required, the old clause is refused by name, and the
    # cancelled dialog must not have saved
    for cut, match in ((f"CONTROL_REFUSED save_prompt party_mon {k} clause=task held\n", "save_prompt"),
                       ("DIALOG_WITNESS_FALSE", "DIALOG_WITNESS_FALSE"),
                       (f"SAVE_CANCEL_FIELD_FREE {k}\n", "SAVE_CANCEL_FIELD_FREE"),
                       ("SAVE_DISMISSAL save_then_write_2", "SAVE_DISMISSAL")):
        with pytest.raises(RuntimeError, match=match):
            run.assert_save_then_write_gen3_saved({"a": receipt.replace(cut, "X"), "b": ""})
    with pytest.raises(RuntimeError, match="save_dialog_cb"):
        run.assert_save_then_write_gen3_saved({"a": receipt.replace(
            f"dialog_witness party_mon {k} clause=field_controls_locked",
            f"dialog_witness party_mon {k} clause=field_controls_locked save_dialog_cb=0x0806F9E1!"), "b": ""})
    with pytest.raises(RuntimeError, match="saves=3"):
        run.assert_save_then_write_gen3_saved({"a": receipt + "SAVE_WITNESS_DUMP path=p bytes=1 saves=3 frame=3 counter=7\n",
                                                "b": ""})
    with pytest.raises(RuntimeError, match="STALE_SAVE_DIALOG"):
        run.assert_save_then_write_gen3_saved({"a": receipt.replace("STALE_SAVE_DIALOG", "STALE_X"), "b": ""})
    with pytest.raises(RuntimeError, match="box_mon_failed"):
        run.assert_save_then_write_gen3_saved({"a": receipt + f"TX box_mon_failed {k} {{}}\n", "b": ""})
    in_party = _saved(fixture, 5, [STARTER, PIDGEY])
    run, _ = _oracle_stub(monkeypatch, tmp_path, "save_then_write_gen3", {"a": in_party, "b": in_party},
                          fixture, [{"a": {"key": k}, "b": {"key": k}, "status": "alive"}])
    run._link_keys = {"a": k, "b": k}
    with pytest.raises(RuntimeError, match="still holds"):
        run.assert_save_then_write_gen3_saved({"a": receipt, "b": ""})


def test_the_save_then_write_runner_queues_only_after_the_stale_witness():
    body = (REPO / "tools" / "e2e_duo.py").read_text(encoding="utf-8")
    orch = body[body.index("def orchestrate_save_then_write_gen3"):body.index("def orchestrate_whiteout_gen3")]
    assert orch.index("WRITE_PROBE_READY") < orch.index('"cmd": "box_mon"')
    assert duo.SCENARIOS["save_then_write_gen3"]["no_save"] == ("b",)
    assert duo.scenario_applies("save_then_write_gen3", "gen3_frlg")
    assert duo.scenario_applies("save_then_write_gen3", "gen3_lgfr")


# ── C4-SAVE-ROWS: G4 draft §3.2 rows 1/3/6/9 (docs/gen3/research/c4_save_rows_design_2026-09-23.md) ──
# Each row's falsifier runs the REAL scenario under the fake ctx; every one is red on beeea4ff,
# where neither scenario had the leg (it passed without naming the row).

def test_row1_the_success_dismissal_is_read_not_assumed(lua):
    """start_menu.c:668/:673-687: sSaveDialogDelay starts at 60 and drops once per ReturnSuccess
    call; a held A ends it early (> 0), the timeout at 0; 60 means ReturnSuccess never ran."""
    for delay, marker in ((23, "by=a_press delay=23"), (0, "by=timeout delay=0")):
        ok, passed, msg, logs = _run_module(lua, "save_then_write", "a", "initial", {"delay": delay})
        assert ok and passed is True, msg
        assert f"SAVE_DISMISSAL save_then_write_1 {marker}" in logs, logs
        assert f"SAVE_DISMISSAL save_then_write_2 {marker}" in logs, logs
    ok, passed, msg, _ = _run_module(lua, "save_then_write", "a", "initial", {"delay": 60})
    assert ok and passed is False and "SaveDialogCB_ReturnSuccess never ran" in msg, msg


def test_row1_a_prompt_that_admits_fails_naming_the_prompt(lua):
    """The leg cannot pass on a prompt that never refused: the queued party_mon executed there."""
    ok, passed, msg, logs = _run_module(lua, "save_then_write", "a", "initial", {"prompt_admits": "lua:true"})
    assert ok and passed is False and msg.startswith("save_prompt: party_mon K1 is not queued"), msg
    assert "SAVE_CANCEL_PROMPT K1 row=save prompt=overwrite" in logs, logs


@pytest.mark.parametrize("spec, why", [
    ({"never_lands": "lua:true"}, "SAVE_CANCEL_FIELD_FREE: the released party_mon never landed"),
    ({"stuck_locked": "lua:true"}, "SAVE_CANCEL_FIELD_FREE: the field never freed"),
])
def test_row1_a_cancel_that_stays_refused_fails_at_field_free(lua, spec, why):
    ok, passed, msg, _ = _run_module(lua, "save_then_write", "a", "initial", spec)
    assert ok and passed is False and msg.startswith(why), msg


def test_row1_the_cancel_leg_emits_prompt_refusals_and_the_landing(lua):
    ok, passed, msg, logs = _run_module(lua, "save_then_write", "a", "initial", {})
    assert ok and passed is True, msg
    order = ["SAVE_CANCEL_PROMPT K1 row=save prompt=overwrite",
             "CONTROL_REFUSED save_prompt party_mon K1 clause=field_controls_locked",
             "SAVE_CANCEL_MENU_REDRAWN K1 cursor=4 sSaveDialogCB=0x0806F8DD",
             "CONTROL_REFUSED save_cancel_menu party_mon K1 clause=field_controls_locked",
             "CONTROL_RELEASED save_cancel party_mon K1", "SAVE_CANCEL_FIELD_FREE K1",
             "TX sync_retrieve_done K1", "RETURNED_OBSERVED K1",
             "SAVE_CANCEL_WRITE_FRAME K1 frame=5626 field_free=true start_menu_task=false",
             "CONTROL_SETTLED save_cancel party_mon K1"]
    at = [logs.find(m) for m in order]
    assert -1 not in at and at == sorted(at), list(zip(order, at, strict=True))


def test_row1_a_write_that_lands_with_the_field_locked_is_red(lua):
    """The write-frame witness is read inside the write: one landing under the START menu fails."""
    ok, passed, msg, _ = _run_module(lua, "save_then_write", "a", "initial", {"write_locked": "lua:true"})
    assert ok and passed is False and msg.startswith(
        "SAVE_CANCEL_WRITE_FRAME: the party_mon wrote at frame 5626 with field_free=false"), msg


def test_row1_oracle_takes_the_same_frame_landing_order(monkeypatch, tmp_path):
    """Live 03ab26e7 (FR and LG): the client wrote and ACKed in the first free frame, inside its
    onframeend pump, so SAVE_CANCEL_FIELD_FREE logged AFTER TX sync_retrieve_done. Red on
    c9e2b695 (its chain demanded FIELD_FREE before TX); TX before the release still fails."""
    fixture = _fixture([STARTER, PIDGEY])
    k = _key(PIDGEY)
    boxed = _saved(fixture, 5, [STARTER], {(0, 0): _mon(PIDGEY["personality"], party=False, species=16)})
    run, _ = _oracle_stub(monkeypatch, tmp_path, "save_then_write_gen3", {"a": boxed, "b": boxed},
                          fixture, [{"a": {"key": k}, "b": {"key": k}, "status": "alive"}])
    run._link_keys = {"a": k, "b": k}
    receipt = _save_then_write_receipt(k)
    assert receipt.index(f"TX sync_retrieve_done {k}") < receipt.index(f"SAVE_CANCEL_FIELD_FREE {k}")
    run.assert_save_then_write_gen3_saved({"a": receipt, "b": ""})
    released = f"CONTROL_RELEASED save_cancel party_mon {k}\n"
    early_tx = receipt.replace(f"TX sync_retrieve_done {k} {{}}\n", "").replace(
        released, f"TX sync_retrieve_done {k} {{}}\n" + released)
    with pytest.raises(RuntimeError, match="must precede"):
        run.assert_save_then_write_gen3_saved({"a": early_tx, "b": ""})
    early_free = receipt.replace(f"SAVE_CANCEL_FIELD_FREE {k}\n", "").replace(
        released, f"SAVE_CANCEL_FIELD_FREE {k}\n" + released)
    with pytest.raises(RuntimeError, match="must precede"):
        run.assert_save_then_write_gen3_saved({"a": early_free, "b": ""})
    for cut in (f"SAVE_CANCEL_WRITE_FRAME {k} frame=5626 field_free=true start_menu_task=false\n",):
        with pytest.raises(RuntimeError, match="SAVE_CANCEL_WRITE_FRAME"):
            run.assert_save_then_write_gen3_saved({"a": receipt.replace(cut, ""), "b": ""})
    with pytest.raises(RuntimeError, match="SAVE_CANCEL_WRITE_FRAME"):
        run.assert_save_then_write_gen3_saved({"a": receipt.replace("field_free=true", "field_free=false"), "b": ""})


def test_row9_the_old_witness_shape_is_red(lua):
    """cx-3e10776a: the stale pointer alone reads TRUE on another row; the row must refuse it."""
    ok, passed, msg, _ = _run_module(lua, "save_then_write", "a", "initial", {"old_witness": "lua:true"})
    assert ok and passed is False and "dialog_witness: the save-dialog witness reads TRUE" in msg, msg


def test_row9_the_witness_stays_false_off_the_save_row(lua):
    ok, passed, msg, logs = _run_module(lua, "save_then_write", "a", "initial", {})
    assert ok and passed is True, msg
    assert "DIALOG_WITNESS_FALSE cursor=3 action=3 save_row=4 menu=open stale=0x0806F9E1" in logs, logs
    assert "CONTROL_REFUSED dialog_witness party_mon K1 clause=field_controls_locked" in logs, logs


def test_row3_the_cable_save_needs_its_task_witness(lua):
    ok, passed, msg, _ = _run_module(lua, "center_controls", "a", "initial", {"no_save_task": "lua:true"})
    assert ok and passed is False and msg.startswith("cable_save: task50_save_game never started"), msg


def test_row3_the_cable_save_is_its_own_refusal_before_the_save_lands(lua):
    ok, passed, msg, logs = _run_module(lua, "center_controls", "a", "initial", {})
    assert ok and passed is True, msg
    refused = logs.find("CONTROL_REFUSED cable_save box_mon K1 clause=")
    assert refused != -1 and refused < logs.find("SAVE_WITNESS_DUMP") < logs.find("CONTROL_LIVE cable_link K1"), logs


@pytest.mark.parametrize("spec, marker", [
    ({}, "CABLE_CALLBACK_NULL limit=no-cable-partner held_frames=601 open_frames=601 null_frames=0 "
         "callback=0x0800A721:LinkCB_RequestPlayerDataExchange"),
    ({"null_cb": "lua:true"},
     "CABLE_CALLBACK_NULL sLinkOpen=1 gLinkCallback=0 held_frames=601 open_frames=601 null_frames=601"),
])
def test_row6_the_link_wait_is_sampled_for_a_null_callback(lua, spec, marker):
    ok, passed, msg, logs = _run_module(lua, "center_controls", "a", "initial", spec)
    assert ok and passed is True, msg
    assert marker in logs, logs
    assert logs.find("CONTROL_REFUSED cable_link") < logs.find(marker) < logs.find("CONTROL_RELEASED cable_link")


def test_row6_a_link_that_never_opened_samples_nothing(lua):
    ok, passed, msg, _ = _run_module(lua, "center_controls", "a", "initial", {"link_closed": "lua:true"})
    assert ok and passed is False and "sLinkOpen never read 1" in msg, msg


# ── REV-PROBE2-SAVEROWS (ACCEPT-WITH-FIXES): B-2, B-3 and the lows; each red on 0feb9383 ──
@pytest.mark.parametrize("spec, why", [
    ({"odd_cb": "lua:true"}, "gLinkCallback read 0x12345679, not LinkCB_RequestPlayerDataExchange"),
    ({"link_flaky": "lua:true"}, "the link was open on 301 of 601 held frames"),
])
def test_row6_the_limit_needs_every_frame_open_on_the_named_callback(lua, spec, why):
    ok, passed, msg, _ = _run_module(lua, "center_controls", "a", "initial", spec)
    assert ok and passed is False and why in msg, msg


def test_row6_oracle_judges_the_line_field_by_field(monkeypatch, tmp_path):
    """The reviewer's fuzz (open_frames=1, callback=0x12345678, samples=9/3) passed 0feb9383's
    regex; the address is resolved from pokefirered.sym (A is FireRed here)."""
    fixture = _fixture([STARTER, PIDGEY])
    saved = _saved(fixture, 3, [STARTER, PIDGEY])
    k = _key(PIDGEY)
    run, _ = _oracle_stub(monkeypatch, tmp_path, "center_controls_gen3", {"a": saved, "b": saved},
                          fixture, [{"a": {"key": k}, "b": {"key": k}, "status": "alive"}])
    run._link_keys = {"a": k, "b": k}
    good = _center_controls_receipt(k)
    run.assert_center_controls_gen3_saved({"a": good, "b": ""})
    line = ("CABLE_CALLBACK_NULL limit=no-cable-partner held_frames=601 open_frames=601 null_frames=0 "
            "callback=0x0800A721:LinkCB_RequestPlayerDataExchange")
    assert line in good
    for bad in (
            # the reviewer's three, as 0feb9383 accepted them
            "CABLE_CALLBACK_NULL limit=no-cable-partner open_frames=1 null_frames=0 callback=0x0800A721",
            "CABLE_CALLBACK_NULL limit=no-cable-partner open_frames=601 null_frames=0 callback=0x12345678",
            "CABLE_CALLBACK_NULL sLinkOpen=1 gLinkCallback=0 samples=9/3",
            # and in the new shape
            line.replace("open_frames=601", "open_frames=1"),
            line.replace("held_frames=601 open_frames=601", "held_frames=60 open_frames=60"),
            line.replace("0x0800A721", "0x12345679"),
            line.replace(":LinkCB_RequestPlayerDataExchange", ":LinkCB_Other"),
            line.replace(":LinkCB_RequestPlayerDataExchange", ""),
            "CABLE_CALLBACK_NULL sLinkOpen=1 gLinkCallback=0 held_frames=601 open_frames=3 null_frames=9",
            "CABLE_CALLBACK_NULL sLinkOpen=1 gLinkCallback=0 held_frames=601 open_frames=700 null_frames=9"):
        with pytest.raises(RuntimeError, match="CABLE_CALLBACK_NULL"):
            run.assert_center_controls_gen3_saved({"a": good.replace(line, bad), "b": ""})


def test_row1_a_leg_that_moved_the_flash_counter_is_red(lua):
    """A save the dialog wrote with no DUMP line (SAVE_WITNESS_DUMP_SKIPPED/_FAIL) is caught by the
    flash counter itself."""
    ok, passed, msg, _ = _run_module(lua, "save_then_write", "a", "initial", {"leg_saved": "lua:true"})
    assert ok and passed is False and msg.startswith("SAVE_COUNTER: the menu leg moved the flash save counter 6 -> 7"), msg
    ok, passed, msg, logs = _run_module(lua, "save_then_write", "a", "initial", {})
    assert ok and passed is True and "SAVE_COUNTER_UNCHANGED before=6 after=6" in logs, (msg, logs)


def test_save_then_write_oracle_lows(monkeypatch, tmp_path):
    fixture = _fixture([STARTER, PIDGEY])
    k = _key(PIDGEY)
    boxed = _saved(fixture, 5, [STARTER], {(0, 0): _mon(PIDGEY["personality"], party=False, species=16)})
    run, _ = _oracle_stub(monkeypatch, tmp_path, "save_then_write_gen3", {"a": boxed, "b": boxed},
                          fixture, [{"a": {"key": k}, "b": {"key": k}, "status": "alive"}])
    run._link_keys = {"a": k, "b": k}
    receipt = _save_then_write_receipt(k)
    run.assert_save_then_write_gen3_saved({"a": receipt, "b": ""})
    for bad, match in (
            (receipt.replace(f"CONTROL_LIVE save_prompt {k} map=3.1\n", ""), "CONTROL_LIVE save_prompt"),
            (receipt.replace(f"CONTROL_LIVE save_cancel_menu {k} map=3.1\n", ""), "CONTROL_LIVE save_cancel_menu"),
            (receipt.replace("by=a_press delay=40", "by=a_press delay=60"), "SAVE_DISMISSAL"),
            (receipt.replace("by=a_press delay=40", "by=a_press delay=200"), "SAVE_DISMISSAL"),
            (receipt.replace("SAVE_COUNTER_UNCHANGED before=6 after=6\n", ""), "SAVE_COUNTER_UNCHANGED"),
            (receipt.replace("before=6 after=6", "before=6 after=7"), "SAVE_COUNTER_UNCHANGED")):
        with pytest.raises(RuntimeError, match=match):
            run.assert_save_then_write_gen3_saved({"a": bad, "b": ""})
    # the CONTROL_LIVE of each held control precedes its refusal
    swapped = receipt.replace(f"CONTROL_LIVE save_prompt {k} map=3.1\n", "").replace(
        f"CONTROL_REFUSED save_prompt party_mon {k} clause=task held\n",
        f"CONTROL_REFUSED save_prompt party_mon {k} clause=task held\nCONTROL_LIVE save_prompt {k} map=3.1\n")
    with pytest.raises(RuntimeError, match="must precede"):
        run.assert_save_then_write_gen3_saved({"a": swapped, "b": ""})


def test_the_save_then_write_runner_queues_the_withdraw_after_the_dialog_witness():
    body = (REPO / "tools" / "e2e_duo.py").read_text(encoding="utf-8")
    orch = body[body.index("def orchestrate_save_then_write_gen3"):body.index("def orchestrate_whiteout_gen3")]
    assert orch.index("CONTROL_LIVE dialog_witness") < orch.index('"cmd": "party_mon"')


# pret pokefirered c75f3523 (start_menu.c:63-72, new_game.c:37): address and size in BOTH .sym
# files -- the widths the scenario peeks must fit, and the START menu trio must be the same words
# gen3_boot_check.lua reads (START_MENU_*_ADDR).
_SAVE_ROWS_SYMS = {"sSaveDialogDelay": (0x03000FA8, 1), "gDifferentSaveFile": (0x02031DB0, 1),
                   "SaveDialogCB_AskSaveHandleInput": (0x0806F7F8, 0x72),
                   "SaveDialogCB_AskOverwriteOrReplacePreviousFileHandleInput": (0x0806F8DC, 0x46),
                   "sStartMenuCursorPos": (0x020370F4, 1), "sNumStartMenuItems": (0x020370F5, 1),
                   "sStartMenuOrder": (0x020370F6, 9)}


def test_the_new_driver_symbols_are_pret_statics():
    assert set(_SAVE_ROWS_SYMS) <= set(_driver_syms())
    for title in ("firered", "leafgreen"):
        got = {}
        for line in (REPO / "data" / "gen3" / "pret" / f"poke{title}.sym").read_text(encoding="utf-8").splitlines():
            parts = line.split()
            if len(parts) == 4 and parts[3] in _SAVE_ROWS_SYMS:
                got.setdefault(parts[3], (int(parts[0], 16), int(parts[2], 16)))
        assert got == _SAVE_ROWS_SYMS, (title, got)
    boot = (REPO / "lua" / "tests" / "gen3_boot_check.lua").read_text(encoding="utf-8")
    for const, name in (("START_MENU_CURSOR_ADDR", "sStartMenuCursorPos"),
                        ("START_MENU_COUNT_ADDR", "sNumStartMenuItems"), ("START_MENU_ORDER_ADDR", "sStartMenuOrder")):
        assert int(re.search(rf"local {const}\s*=\s*0x([0-9A-Fa-f]+)", boot).group(1), 16) == _SAVE_ROWS_SYMS[name][0]
    scenario = (REPO / "lua" / "tests" / "duo" / "scenario_gen3_save_then_write.lua").read_text(encoding="utf-8")
    for name, width in re.findall(r'ctx\.peek\("(\w+)", (\d)', scenario):
        if name in _SAVE_ROWS_SYMS:
            assert int(width) <= _SAVE_ROWS_SYMS[name][1], (name, width)



# ── C4-6t: BizHawk rewind off in every generated run config (duo run 61569 crash) ─────────────
_REWIND = {"UseCompression": False, "UseDelta": False, "Enabled": True, "BufferSize": 512}


def _config_with_rewind(tmp_path):
    path = tmp_path / "src.ini"
    path.write_text(json.dumps({"Rewind": dict(_REWIND), "PathEntries": {"Paths": [
        {"System": "GBA", "Type": "Save RAM", "Path": ""}]}}), encoding="utf-8")
    return path


def _rewind_of(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))["Rewind"]


def test_every_run_config_writer_turns_rewind_off(tmp_path):
    """BizHawk's rewind capture (MainForm.CaptureRewind -> ZwinderBuffer.Capture ->
    MGBAHawk.SaveStateBinary) crashed both duo instances with an AccessViolationException."""
    import gen1_playthrough as g1
    import gen3_fixtures
    import run_gate

    src = _config_with_rewind(tmp_path)
    for name, write in (
            ("gb", lambda dst: g1.write_run_config(str(src), str(dst))),
            ("gba", lambda dst: gen3_fixtures.write_gba_run_config(str(src), str(dst), str(tmp_path / "sr"))),
            ("gate", lambda dst: run_gate.write_gate_config(str(src), str(dst)))):
        dst = tmp_path / f"{name}.ini"
        write(dst)
        rewind = _rewind_of(dst)
        assert rewind["Enabled"] is False, name
        assert rewind["BufferSize"] == 512, name              # every other field kept
    # an unparseable source is a loud error in every writer -- never a raw copy with rewind on
    broken = tmp_path / "broken.ini"
    broken.write_text("{not json", encoding="utf-8")
    for name, write in (
            ("gb", lambda dst: g1.write_run_config(str(broken), str(dst))),
            ("gba", lambda dst: gen3_fixtures.write_gba_run_config(str(broken), str(dst), str(tmp_path / "sr3"))),
            ("gate", lambda dst: run_gate.write_gate_config(str(broken), str(dst)))):
        dst = tmp_path / f"broken_{name}.ini"
        with pytest.raises(RuntimeError, match="rewind"):
            write(dst)
        assert not dst.exists(), name
    # a config with no Rewind block at all gets one, disabled
    bare = tmp_path / "bare.ini"
    bare.write_text(json.dumps({}), encoding="utf-8")
    run_gate.write_gate_config(str(bare), str(tmp_path / "bare_out.ini"))
    assert _rewind_of(tmp_path / "bare_out.ini") == {"Enabled": False}


def test_no_tool_copies_the_bizhawk_config_raw():
    """Every per-run config goes through a writer that disables rewind -- no plain copy left."""
    offenders = [p.name for p in (REPO / "tools").glob("*.py")
                 if re.search(r"copyfile\(\s*BIZHAWK_CONFIG", p.read_text(encoding="utf-8"))]
    assert offenders == [], offenders


# ── mechanism P+H: the active-faint carrier (scenario_gen3_linked_faint_active.lua) ───────────
# A frame-stepped model of the engine after a force_faint: the client's 5-line commit, the
# controller hand-off, the Perish KO, the faint site, then the case's aftermath. Faults turn each
# engine oracle red. The happy logs are fed to the runner's own active_faint_chain, so the Lua
# producer and the Python consumer cannot drift apart.
_PH_MODEL = r"""
function PH(case, player, fault)
    fault = fault or ""
    local explode = case == "explode"
    local FROM, TO, SLOTADDR = 0x0802E33D, 0x0802E3B5, 0x03004FE0
    local PARTY_HP, BATTLE_HP = 0x02024284 + 0x56, 0x02023BE4 + 0x28
    local logs, lines, watchers, sites = {}, {}, {}, {}
    local frame, rx, entry, sent = 100, 0, nil, { faint = 0, whiteout = 0, memorialize_done = 0 }
    local party = { { slot = 0, key = "K0", hp = 20, max_hp = 20, level = 9, experience = 400 },
                    { slot = 1, key = "K1", hp = 17, max_hp = 17, level = 4 } }
    if fault == "lone" then party[2] = nil end           -- RR rr_battle.sav: one mon
    local e = { in_battle = false, ctrl0 = 0x08030001, exec = 1, keys = 0, battler0_slot = 0,
                battle_hp = 20, pp = { 35, 30, 0, 0 }, status3 = 0, counter = 0, last_move = 0,
                outcome = 0 }
    local presses, balls, commit_at, ko_at = 0, 5, nil, nil
    local ctx = { D = { active_faint_case = case, timeout_secs = 60 }, player = player, cp = {},
                  rr = case == "lhammer", handoff = { slot_addr = SLOTADDR, from = FROM, to = { TO } } }
    ctx.log = function(s) logs[#logs + 1] = tostring(s) end
    local function tick()
        frame = frame + 1
        if rx == 1 and not commit_at and e.in_battle then          -- the client commits P+H
            commit_at = frame
            local n = explode and 14 or (fault == "four_writes" and 4 or 5)
            for i = 1, n do
                local last = i == n
                lines[#lines + 1] = { reason = "battle_commit", address = last and SLOTADDR or 0x02023DFC + i,
                                      len = last and 4 or 1, frame = frame }
            end
            e.ctrl0 = FROM
            if explode then
                logs[#logs + 1] = "[client] [SLink-gen3] force_explode: menu skip committed slot=0 battler=0 handoff=1"
                entry = { key = "K0", explode = { battler = 0 }, why = "explosion committed" }
                e.pp = { 5, 5, 5, 5 }
            else
                logs[#logs + 1] = "[client] [SLink-gen3] force_faint: Perish commit battler=0 handoff=1 K0"
                e.status3 = e.status3 | 0x20
                entry = fault == "old_hold" and { key = "K0", perish = true, why = "active faint committed (press A)" }
                        or { key = "K0", perish = true, handoff = true, why = "active faint committed" }
            end
        elseif commit_at and frame == commit_at + 1 and fault ~= "no_handoff" then
            e.ctrl0, e.exec = TO, fault == "exec_set" and 1 or 0
        elseif commit_at and frame == commit_at + 3 then
            if fault == "press" then presses = presses + 1 end
            if fault == "keys" then e.keys = 0x100 end
            if fault == "pp_drop" then e.pp[1] = 34 end
            if fault == "acted" then e.last_move = 33 end
            if fault == "hp_write" then lines[#lines + 1] = { reason = "battle_faint", address = PARTY_HP, len = 2, frame = frame } end
            if fault == "lost_ball" then balls = balls - 1 end
        elseif commit_at and frame == commit_at + 5 and explode and fault ~= "no_boom" then
            e.last_move, e.pp[1] = 153, fault == "no_explosion_pp_drop" and 5 or 4
        elseif commit_at and frame == commit_at + 6 then        -- the engine KO
            e.keys, e.battle_hp = 0, 0
            if fault ~= "flag_kept" then e.status3 = e.status3 & ~0x20 end
            ko_at = frame
        elseif ko_at and fault == "site_needs_a" and presses == 0 then
            -- RR explode at 97672e6d: battle text after the KO waits for a press before the site
        elseif ko_at and frame >= ko_at + 1 and #sites == 0 then -- the faint site, party HP follows
            party[1].hp = 0
            sites[#sites + 1] = { frame = frame, active = 0, battler0_slot = 0, battle_hp = 0, party_hp = 0,
                                  counter = fault == "counter" and e.counter or e.counter + 1 }
            logs[#logs + 1] = string.format("FORCED_HP0 K0 frame=%d in_battle=1 battler=1", frame)
            entry = nil
        end
        for i = #watchers, 1, -1 do if watchers[i]() then table.remove(watchers, i) end end
    end
    ctx.frames = function(n) for _ = 1, n do tick() end end
    ctx.watch = function(fn) watchers[#watchers + 1] = fn end
    ctx.wait_until = function(pred)
        for _ = 1, 400 do local v = pred(); if v then return v end; tick() end
    end
    ctx.mash_until = function(pred)
        for _ = 1, 400 do local v = pred(); if v then return v end; presses = presses + 1; tick() end
    end
    ctx.wait_go = function() return true end
    ctx.linked = function() return "K0" end
    ctx.party = function() return party end
    ctx.find = function(k) for _, m in ipairs(party) do if m.key == k then return m end end end
    ctx.hunt = function() e.in_battle, e.ctrl0 = true, 0x08030001; return true end
    ctx.SP = { verify_fight_cursor = function()
        if not e.in_battle then return nil end
        if ko_at then return fault == "no_sendout" and "fight" or "party" end
        return "fight"
    end }
    ctx.battler_slot = function() return e.battler0_slot end
    ctx.in_battle = function() return e.in_battle end
    ctx.write_lines = function() return lines end
    ctx.attempted = function() return #lines end
    ctx.inputs = function() return presses end
    ctx.press = function() presses = presses + 1 end
    ctx.faint_sites = function() return sites end
    ctx.hp_addrs = function() return { [PARTY_HP] = true, [BATTLE_HP] = true } end
    ctx.engine_sample = function()
        local s = {}
        for k, v in pairs(e) do s[k] = v end
        s.pp = { e.pp[1], e.pp[2], e.pp[3], e.pp[4] }
        s.frame, s.party_hp = frame, party[1].hp
        return s
    end
    ctx.battle_hold = function(k) if entry and entry.key == k then return entry end end
    ctx.received = function(cmd)
        if cmd == (explode and "force_explode" or "force_faint") then return rx end
        return (cmd == "game_over" or cmd == "memorialize") and 1 or 0
    end
    -- the whiteout row: game_over latched, the last-mon memorialize dropped at the checkpoint
    local settled = false
    ctx.queued = function()
        if fault == "mem_stuck" then return { index = 1 } end
        if not settled then
            settled = true
            logs[#logs + 1] = "RX memorialize key=K0"
            logs[#logs + 1] = "RX game_over"
            logs[#logs + 1] = "[client] [SLink-gen3] memorialize dropped: last mon after game over K0"
        end
    end
    ctx.wait_received = function()
        if fault == "no_rx" then return nil end
        rx = 1
        logs[#logs + 1] = (explode and "RX force_explode" or "RX force_faint") .. " key=K0"
        return true
    end
    ctx.balls = function() return balls end
    ctx.peek_u8 = function() return 4 end
    ctx.try = function(fn, ...) return pcall(fn, ...) end
    ctx.send_out = function(slot)
        e.battler0_slot = slot
        ctx.frames(1)
        logs[#logs + 1] = "SENT_OUT slot=1 battler_slot=1"
        return true
    end
    local function battle_ends(outcome)
        e.outcome = outcome
        ctx.frames(1)
        e.in_battle = false
        ctx.frames(1)
    end
    ctx.run_away = function() battle_ends(4); return true end
    ctx.play = {
        fight_through = function()
            if case == "whiteout" then
                battle_ends(2)
                sent.whiteout = 1
                logs[#logs + 1] = "TX whiteout - {}"
                error({ whiteout = true }, 0)
            end
            battle_ends(1)
            return true
        end,
        wait_scene_settled = function() ctx.frames(1); return true end }
    ctx.sent = function(ev) return sent[ev] + (ev == "faint" and fault == "echo" and 1 or 0) end
    ctx.wait_sent = function(ev, k)
        sent[ev] = 1
        logs[#logs + 1] = "TX " .. ev .. " " .. k .. " {}"
        return true
    end
    ctx.save = function()
        logs[#logs + 1] = "SAVE_WITNESS_DUMP path=p bytes=131072 saves=1 frame=1 counter=5"
        return true
    end
    ctx.walk_to_pc = function() end
    ctx.walk_pc_to_grass = function()
        if fault == "walk_whiteout" then error({ whiteout = true, map = 1284 }, 0) end
    end
    local fled = false
    ctx.flee_incidentals = function(_, fn) fled = true; return pcall(fn) end
    ctx.pc_deposit = function()
        assert(fled, "the one-mon walks must run under flee_incidentals")
        table.remove(party, 2)
        return "K1"
    end
    ctx.observe_boxed = function() return "0:0" end
    ctx.preparation_budget = function() return 1800000 end
    ctx.enter_trainer = function(_, id, prep)
        assert(id == 102 and prep.target_key == "K1" and prep.target_slot == 1)
        party[1].level = 13
        return ctx.hunt()
    end
    ctx.partner_result = function() return "x\nRESULT: PASS (subject)\n" end
    ctx.lose_active = function() party[1].hp = 0; e.in_battle = false; sent.faint = 1; return true end
    local fn = dofile(SCENARIO_DIR .. "/scenario_gen3_linked_faint_active.lua")
    local ok, pass, msg = pcall(fn, ctx)
    return ok, pass, tostring(ok and msg or pass), table.concat(logs, "\n") .. "\n"
end
"""


@pytest.fixture(scope="module")
def ph():
    from lupa import LuaRuntime

    runtime = LuaRuntime(unpack_returned_tuples=True)
    runtime.globals().SCENARIO_DIR = str(REPO / "lua" / "tests" / "duo").replace("\\", "/")
    runtime.execute(_PH_MODEL)
    return runtime.globals().PH


@pytest.mark.parametrize("case,player", [("wild", "b"), ("whiteout", "b"), ("trainer", "b"),
                                         ("command", "a"), ("lhammer", "b"), ("explode", "b")])
def test_p_h_carrier_happy_path_satisfies_the_runner_chain(ph, case, player):
    ok, passed, msg, log = ph(case, player)
    assert ok and passed is True, (msg, log)
    required, ordered, forbidden = duo.active_faint_chain("K0", case)
    if case == "whiteout":
        required.append(r"(?m)^LAST_MON_KEPT K0$")
        forbidden.append(duo.gen3_tx("memorialize_done", "K0"))
    elif case != "command":
        required.append(duo.gen3_tx("memorialize_done", "K0"))
    assert duo.gen3_receipt_problems(player, log, required=required, ordered=ordered,
                                     forbidden=forbidden) == [], log
    assert "HANDOFF K0 from=0x0802E33D to=0x0802E3B5 frames=1 exec_bit0=0" in log
    assert re.search(r"ACTIVE_FAINT_SITE K0 .* counter=0->1$", log, re.M)
    if case == "whiteout":
        assert "ONE_MON_PARTY K0 deposited=K1" in log and "ACTIVE_OUTCOME K0 outcome=2 " in log
    if case == "explode":
        assert "last_move=153 inputs=0 keys=0x0 hp_writes=0 attempted=14" in log
    if case == "trainer":
        assert "PREP_LEVEL before=9 after=13 floor=13" in log


@pytest.mark.parametrize("fault,reason", [
    # the red checks the card names: a press (harness or engine keys) between commit and KO
    ("press", "input between the commit and the KO"),
    ("keys", "input between the commit and the KO"),
    # the old hold, or a commit without the hand-off
    ("old_hold", "not a handed-off Perish commit"),
    ("no_handoff", "no hand-off successor within 2 frames"),
    ("exec_set", "exec bit 0 still set"),
    ("four_writes", "commit wrote 4 lines"),
    ("hp_write", "SLink wrote an HP word"),
    ("pp_drop", "PP dropped"),
    ("acted", "lastUsedMovePlayer moved"),
    ("flag_kept", "Perish flag is still set"),
    ("counter", "playerFaintCounter 0 -> 0"),
    ("echo", "echoed faint"),
    ("no_sendout", "no send-out party screen"),
    ("no_rx", "no force_faint"),
])
def test_p_h_carrier_fails_each_engine_oracle_by_name(ph, fault, reason):
    ok, passed, msg, log = ph("wild", "b", fault)
    assert ok and passed is False and reason in msg, (fault, msg, log)
    assert "SAVE_WITNESS_DUMP" not in log


def test_p_h_whiteout_walks_flee_and_name_a_whiteout_before_ready(ph):
    """W3's live FR-as-A failure at b0483efe: the lone lead fought an incidental encounter on the
    walk back, whited out, and the raw table escaped as "scenario error: table: 0x...". The walks
    now run under flee_incidentals (the model asserts it), and a whiteout there is named."""
    ok, passed, msg, log = ph("whiteout", "b", "walk_whiteout")
    assert ok and passed is False and msg == "one-mon party walk: whited out", msg
    assert "READY_ACTIVE" not in log


def test_p_h_whiteout_row_needs_the_last_mon_memorialize_settled(ph):
    ok, passed, msg, _ = ph("whiteout", "b", "mem_stuck")
    assert ok and passed is False and "last-mon memorialize was never settled" in msg


def test_p_h_faint_site_behind_post_ko_text_is_pressed_through(ph):
    """explode_gen3 on RR at 97672e6d: the KO came hands-off (last_move 153, inputs 0), but the
    faint site sat behind battle text for 600 s while the carrier pressed nothing ("no in-battle
    Perish KO witnessed"). After the KO -- where presses are allowed -- the carrier now presses A
    until the site fires; the KO line still records zero inputs in the window."""
    for case in ("explode", "wild"):
        ok, passed, msg, log = ph(case, "b", "site_needs_a")
        assert ok and passed is True, (case, msg, log)
        assert re.search(r"^ACTIVE_KO K0 .* inputs=0 keys=0x0 hp_writes=0 ", log, re.M), log
        assert re.search(r"^ACTIVE_FAINT_SITE K0 ", log, re.M), log


def test_p_h_explode_case_names_a_ko_without_the_explosion(ph):
    ok, passed, msg, log = ph("explode", "b", "no_boom")
    assert ok and passed is False and "without the Explosion action" in msg, (msg, log)


def test_p_h_one_mon_fixture_needs_no_deposit_and_keeps_its_last_mon(ph):
    """RR's rr_battle.sav holds one mon: R4 builds no party (no PC trip), and after game_over the
    last-mon memorialize is dropped (LAST_MON_KEPT) -- on the subject and on A's natural side."""
    ok, passed, msg, log = ph("whiteout", "b", "lone")
    assert ok and passed is True, (msg, log)
    assert "ONE_MON_PARTY K0 deposited=-" in log and "LAST_MON_KEPT K0" in log
    ok, passed, msg, log = ph("wild", "a", "lone")
    assert ok and passed is True and "LAST_MON_KEPT K0" in log, (msg, log)
    assert "TX memorialize_done" not in log


def test_p_h_carrier_rr_l_hammer_names_a_lost_ball(ph):
    ok, passed, msg, _ = ph("lhammer", "b", "lost_ball")
    assert ok and passed is False and "L hammer lost a ball" in msg


def test_p_h_carrier_mega_row_is_a_signed_limit_by_name(ph):
    ok, passed, msg, _ = ph("mega", "b")
    assert ok and passed is False and msg.startswith("SIGNED LIMIT R5 (owner ruling 20)")


def test_p_h_command_case_idles_b_without_saving(ph):
    ok, passed, msg, log = ph("command", "b")
    assert ok and passed is True, msg
    assert "SAVE_WITNESS_DUMP" not in log and "READY_ACTIVE" not in log


@pytest.mark.parametrize("mutate,problem", [
    (lambda t: t.replace("ACTIVE_COMMIT K0", "ACTIVE_HOLD K0 why=active battler\nACTIVE_COMMIT K0"), "forbidden"),
    (lambda t: t.replace("inputs=0 ", "inputs=1 "), "missing"),
    (lambda t: t.replace("frames=1 exec_bit0=0", "frames=3 exec_bit0=0"), "missing"),
    # R1 L3: the measured values, each nonzero on its own
    (lambda t: t.replace("keys=0x0 ", "keys=0x100 "), "missing"),
    (lambda t: t.replace("hp_writes=0 ", "hp_writes=1 "), "missing"),
    (lambda t: t.replace("exec_bit0=0", "exec_bit0=1"), "missing"),
    (lambda t: t.replace("counter=0->1", "counter=0->0"), "missing"),
    (lambda t: t + "TX faint K0 {}\n", "forbidden"),
    (lambda t: t.replace("in_battle=1 battler=1", "in_battle=0 battler=0"), "forbidden"),
])
def test_active_faint_chain_is_red_on_the_old_hold_and_a_press(ph, mutate, problem):
    """The Python consumer, red on its own: a receipt with an ACTIVE_HOLD, a press counted in
    the window, a slow hand-off, a flat faint counter, a faint echo, or an overworld HP 0."""
    _, _, _, log = ph("wild", "b")
    required, ordered, forbidden = duo.active_faint_chain("K0", "wild")
    problems = duo.gen3_receipt_problems("b", mutate(log), required=required, ordered=ordered,
                                         forbidden=forbidden)
    assert problems and any(problem in p for p in problems), problems


def test_p_h_rows_are_registered_with_their_cases():
    cases = {"linked_faint_active_gen3": ("wild", ("gen3_frlg", "gen3_rr", "gen3_emerald", "gen3_exp")),
             "linked_faint_active_whiteout_gen3": ("whiteout", ("gen3_frlg", "gen3_rr")),
             "linked_faint_active_trainer_gen3": ("trainer", ("gen3_frlg",)),
             "active_end_gen3": ("command", ("gen3_frlg",)),
             "linked_faint_active_lhammer_gen3": ("lhammer", ("gen3_rr",)),
             "linked_faint_active_mega_gen3": ("mega", ("gen3_rr",)),
             "explode_gen3": ("explode", ("gen3_frlg", "gen3_rr", "gen3_emerald"))}
    for name, (case, games) in cases.items():
        row = duo.SCENARIOS[name]
        assert row.get("active_faint_case", "wild") == case and row["games"] == games, name
        assert row.get("scenario_module", "linked_faint_active") == "linked_faint_active", name
        assert "battle_window_case" not in row, name
        assert callable(getattr(duo.DuoRun, "orchestrate_" + name)) and callable(getattr(duo.DuoRun, row["oracle"]))
        for game in games:
            assert duo.scenario_applies(name, game)
    # RR fixtures: R1/R2/R3 need rr_battle2 (a second mon, balls); R4 and explode run on rr_battle
    rr = {"linked_faint_active_gen3": "battle2",
          "linked_faint_active_lhammer_gen3": "battle2", "linked_faint_active_whiteout_gen3": "battle",
          "explode_gen3": "battle"}
    for name, target in rr.items():
        assert duo.scenario_target(duo.SCENARIOS[name], "gen3_rr") == target, name
    assert duo.scenario_target(duo.SCENARIOS["linked_faint_active_gen3"], "gen3_frlg") == "battle"
    # patch-first (2026-10-02): the clean-RR link rows are retired -- a clean RR is refused at launch
    for gone in ("linked_faint_active_clean_gen3", "faint_cmd_clean_gen3"):
        assert gone not in duo.SCENARIOS, gone
        assert not hasattr(duo.DuoRun, "orchestrate_" + gone), gone
    # G4-SYNTH-TRAINER (6e85ddfc): the cached-native trainer fixture replaces the T2 walk
    trainer = duo.SCENARIOS["linked_faint_active_trainer_gen3"]
    assert trainer["target"] == {"a": "battle", "b": "trainer"}
    assert (trainer["timeout"], trainer["frames"]) == (1800, 2500000)
    assert duo.SCENARIOS["active_end_gen3"]["no_save"] == ("b",)


def test_active_end_queues_force_faint_only_after_ready_active(monkeypatch):
    run = duo.DuoRun.__new__(duo.DuoRun)
    run.cfg, run.http_port = duo.SCENARIOS["active_end_gen3"], 1234
    run._pydec_note = lambda text: None
    monkeypatch.setattr(duo, "api", lambda *args: {"ok": True})
    calls = []
    run._gen3_prelude = lambda: ({0: "A0", 1: "A1"}, {0: "B0", 1: "B1"})
    run.go = lambda lines: calls.append(("go", lines))
    run._gen3_mark = lambda *args: calls.append(("ready", args))
    run.queue_command = lambda *args: calls.append(("queue", args))
    run.orchestrate_active_end_gen3()
    assert [row[0] for row in calls] == ["go", "ready", "queue"]
    assert calls[0][1] == {"a": ["LINKED A0"], "b": ["LINKED B0"]}
    assert re.search(calls[1][1][1], "READY_ACTIVE A0 case=command")
    assert calls[2][1] == ("a", {"cmd": "force_faint", "key": "A0"})


def test_rr_rows_skip_until_their_battle_fixtures_exist(monkeypatch, tmp_path):
    monkeypatch.setattr(duo, "GEN3_FIXTURES", str(tmp_path))
    why, allowed = duo.skip_reason("linked_faint_active_gen3", "gen3_rr")
    assert "rr_battle2.sav" in why and "rr_battle2_b.sav" in why and "not built yet" in why and not allowed
    assert duo.skip_reason("linked_faint_active_gen3", "gen3_frlg") is None     # FR never skips
    (tmp_path / "rr_battle.sav").write_bytes(b"")
    (tmp_path / "rr_battle_b.sav").write_bytes(b"")
    assert duo.skip_reason("linked_faint_active_whiteout_gen3", "gen3_rr") is None   # R4: one-mon
    assert duo.skip_reason("explode_gen3", "gen3_rr") is None
    assert "rr_battle2" in duo.skip_reason("linked_faint_active_lhammer_gen3", "gen3_rr")[0]
    (tmp_path / "rr_battle2.sav").write_bytes(b"")
    (tmp_path / "rr_battle2_b.sav").write_bytes(b"")
    for name in ("linked_faint_active_gen3", "linked_faint_active_lhammer_gen3"):
        assert duo.skip_reason(name, "gen3_rr") is None, name
    why, allowed = duo.skip_reason("linked_faint_active_mega_gen3", "gen3_rr")
    assert why.startswith("SIGNED LIMIT: owner ruling 20") and allowed is True


def test_a_skip_is_never_a_pass_but_a_signed_limit_is_allowed():
    assert duo.exit_code({"x": (True, 1), "y": (None, 0, "fixture missing", False)}) == 3
    assert duo.exit_code({"x": (False, 1), "y": (None, 0, "why", False)}) == 1
    assert duo.exit_code({"x": (True, 1)}) == 0
    assert duo.exit_code({"x": (True, 1), "r5": (None, 0, "ruling 20", True)}) == 0
    assert duo.exit_code({"r5": (None, 0, "ruling 20", True), "y": (None, 0, "missing", False)}) == 3
    assert duo.summary_lines({"y": (None, 0, "fixture missing", False)}, "gen3_rr") == [
        "  y: SKIP — fixture missing"]
    assert duo.summary_lines({"r5": (None, 0, "ruling 20", True)}, "gen3_rr") == [
        "  r5: SKIP (allowed: signed limit) — ruling 20"]


def test_game_help_names_the_new_rows():
    import inspect

    src = inspect.getsource(duo.main)
    help_text = src[src.index('ap.add_argument("--game"'):src.index('ap.add_argument("--scenario"')]
    for row in ("gen3_frlg", "gen3_lgfr", "gen3_rr", "gen1_new", "gen2"):
        assert row in help_text, row


def _memorial_saved(**overrides):
    """(saved, fixture) for gen3_memorial_problems with STARTER cut from the party into the
    memorial box: party [PIDGEY], box 13 slot 0 holding the key, nothing else moved. `overrides`
    patch the MEMORIAL RECORD only. The party tail (level, max_hp, the six stats) is injected
    on purpose: gen3_codec._PARTY_TAIL is party-only, so a decoded BoxPokemon can never carry
    those fields and this pins the mask as declared rather than as one save shape happens to
    reach it."""
    fixture = _fixture([STARTER, PIDGEY])
    saved = _saved(fixture, 3, [PIDGEY], {(13, 0): _mon(STARTER["personality"], party=False)})
    flushed, cut = _decoded(saved), _decoded(fixture)
    flushed[1][(13, 0)] = dict(flushed[1][(13, 0)], **overrides)
    return flushed, cut


# every field GEN3_TRAINED_MUTABLE names, at a value the cartridge's own record bounds accept
_TRAINED_GROWTH = [
    ("experience", 1261), ("level", 9), ("max_hp", 24), ("attack", 13), ("defense", 14),
    ("speed", 12), ("sp_attack", 13), ("sp_defense", 15), ("moves", [33, 33, 0, 0]),
    ("evs", {"hp": 4, "attack": 4, "defense": 0, "speed": 0, "sp_attack": 0, "sp_defense": 0}),
]


@pytest.mark.parametrize("field,value", _TRAINED_GROWTH)
def test_memorial_trained_masks_the_growth_field_and_nothing_else(field, value):
    """PYDEC FAIL on the RR final cut (d9a928d7): "a: the memorial ... differs from the fixture
    record in [('experience', 228, 267)]". A fights naturally before it goes down, so its
    memorial record may legitimately carry growth the fixture's does not. Each field the mask
    names is hidden when the side was trained -- and is still a diff when it was not, so a
    trained side cannot launder a change the game had no business making."""
    assert field in duo.GEN3_TRAINED_MUTABLE
    key = _key(STARTER)
    clean, fixture = _memorial_saved()
    assert _mem("a", clean, fixture, key, 13) == []       # the untouched record reports nothing
    grown, _ = _memorial_saved(**{field: value})
    assert _mem("a", grown, fixture, key, 13, trained=True) == []
    assert any(f"('{field}'," in p for p in _mem("a", grown, fixture, key, 13))


@pytest.mark.parametrize("field,value", [("species", 16), ("personality", 0x11112222),
                                         ("ot_id", 0x33445566)])
def test_memorial_trained_never_masks_identity_or_species(field, value):
    """`trained` is a growth mask, not a blank cheque. A changed species is a different mon filed
    under the key and stays a diff; a changed personality or OT id makes the key lookup miss, so
    the record is reported as not sitting in the memorial box at all. A wrong-save deposit is
    exactly what a mask that grew to cover identity would hide."""
    key = _key(STARTER)
    saved, fixture = _memorial_saved(**{field: value})
    problems = _mem("a", saved, fixture, key, 13, trained=True)
    assert problems
    if field == "species":
        assert any("('species'," in p for p in problems), problems
    else:
        assert any("not exactly once in the memorial box" in p for p in problems), problems


def test_last_mon_problems_positive_and_negatives(pair):
    """The whiteout row's B: the linked lead kept alone in the party (memorialize dropped after
    game_over), the hand-deposited slot-1 mon once in a non-memorial box."""
    fixture, _ = pair
    key, bench = _key(STARTER), _key(PIDGEY)
    boxed = {(0, 0): _mon(PIDGEY["personality"], party=False, species=16)}

    def last(saved, deposited=(bench,)):
        return duo.gen3_last_mon_problems("b", _decoded(saved), _decoded(fixture), key, list(deposited), 13,
                                          limits=LIMITS)

    assert last(_saved(fixture, 3, [STARTER], boxed)) == []
    assert any("expected [" in p for p in last(_saved(fixture, 3, [STARTER, PIDGEY])))
    assert any("no hand deposit" in p for p in last(_saved(fixture, 3, [STARTER], boxed), deposited=()))
    assert any("hand-deposited" in p for p in
               last(_saved(fixture, 3, [STARTER], {(13, 0): boxed[(0, 0)]})))
    assert any("boxed copy" in p for p in
               last(_saved(fixture, 3, [STARTER], {**boxed, (0, 1): _mon(STARTER["personality"], party=False)})))


def test_active_end_oracle_reads_the_engine_written_hp0(ph, monkeypatch, tmp_path):
    """A2's saved-state half: the P+H chain on A, A's saved slot 0 at HP 0 with the fixture's
    membership, B idle with an unchanged battery. Red on a healed save and on a B write."""
    fixture = _fixture([STARTER, PIDGEY])
    key = _key(STARTER)
    saved = _saved(fixture, 3, [dict(STARTER, hp=0), PIDGEY])
    run, notes = _oracle_stub(monkeypatch, tmp_path, "active_end_gen3", {"a": saved, "b": fixture}, fixture, [])
    run._link_keys = {"a": key, "b": "B0"}
    _, _, _, log = ph("command", "a")
    receipts = {"a": log.replace("K0", key), "b": "WRITES 0\nRESULT: PASS (idle)\n"}
    run.assert_active_end_gen3_saved(receipts)
    assert notes and "active_end" in notes[-1]
    # the companion's own config / link_panel writes (reason native, inside its arena) are NOT game-state
    # writes: the live b receipt ends `WRITES 11` on every companion run (timing decides how many)
    with open(duo.gen3_profile_path("leafgreen"), encoding="utf-8") as handle:
        base = json.load(handle)["native"]["BASE"]
    native = [f"[client] [SLink-gen3] write native 0x{base + 0x800:08X} +4 frame 5",
              f"[client] [SLink-gen3] write native 0x{base + 0x808:08X} +1 frame 5",
              f"[client] [SLink-gen3] write native 0x{base + 0x6E0:08X} +32 frame 90",
              f"[client] [SLink-gen3] write native 0x{base:08X} +2 frame 90"]

    def idle(*lines):
        n = sum("] write " in line for line in lines)
        return "\n".join([*lines, f"WRITES {n}", "RESULT: PASS (idle)"]) + "\n"

    run.assert_active_end_gen3_saved(dict(receipts, b=idle(*native, "RX link_panel key=-")))
    # red: a game-state write on the idle side (a non-native reason, or native outside the arena)
    for bad in ("[client] [SLink-gen3] write battle_faint 0x02024282 +2 frame 9",
                "[client] [SLink-gen3] write overworld 0x02024284 +2 frame 9",
                f"[client] [SLink-gen3] write native 0x{base + duo.GEN3_NATIVE_ARENA_SIZE:08X} +1 frame 9",
                "[client] [SLink-gen3] write native 0x02024284 +2 frame 9"):
        with pytest.raises(RuntimeError, match="idle peer wrote"):
            run.assert_active_end_gen3_saved(dict(receipts, b=idle(*native, bad)))
    # red: B was handed a state command (the partner's death, a party/box/memorial order)
    # (apply_prepare/apply_trade drive the native trade and show_menu/show_choices/choose_mon start a native field
    #  script or the party chooser: in-arena reason-native writes, yet they change game script state)
    for cmd in ("force_faint", "force_explode", "box_mon", "party_mon", "memorialize", "apply_prepare", "apply_trade",
                "show_menu", "show_choices", "choose_mon"):
        with pytest.raises(RuntimeError, match="forbidden"):
            run.assert_active_end_gen3_saved(dict(receipts, b=idle(*native, f"RX {cmd} key=B0")))
    # red: the driver's WRITES total disagrees with the lines it logged, or is missing
    with pytest.raises(RuntimeError, match=r"WRITES 1 but 0 parsed"):
        run.assert_active_end_gen3_saved(dict(receipts, b="WRITES 1\n"))
    with pytest.raises(RuntimeError, match="missing"):
        run.assert_active_end_gen3_saved(dict(receipts, b="RESULT: PASS (idle)\n"))
    healed = _saved(fixture, 3, [STARTER, PIDGEY])
    monkeypatch.setattr(run, "_gen3_flushed", lambda inst: healed if inst == "a" else fixture)
    with pytest.raises(RuntimeError, match="not 0"):
        run.assert_active_end_gen3_saved(receipts)


@pytest.mark.parametrize("case", ["wild", "whiteout", "explode"])
def test_linked_faint_active_oracle_on_p_h_receipts(ph, monkeypatch, tmp_path, case):
    """A1's runner oracle over the model's receipts: wild ends in both memorials; whiteout keeps
    B's last mon (dropped memorialize after game_over) with the slot-1 mon hand-deposited."""
    fixture = _fixture([STARTER, PIDGEY])
    k = _key(STARTER)
    memorial = _saved(fixture, 3, [PIDGEY], {(13, 0): _mon(STARTER["personality"], party=False)})
    kept = _saved(fixture, 3, [STARTER], {(0, 0): _mon(PIDGEY["personality"], party=False, species=16)})
    name = {"wild": "linked_faint_active_gen3", "whiteout": "linked_faint_active_whiteout_gen3",
            "explode": "explode_gen3"}[case]
    status = "dead" if case == "whiteout" else "memorial"
    run, notes = _oracle_stub(monkeypatch, tmp_path, name,
                              {"a": memorial, "b": kept if case == "whiteout" else memorial}, fixture,
                              [{"a": {"key": k}, "b": {"key": k}, "status": status, "cause": "battle"}])
    run._link_keys = {"a": k, "b": k}
    cmd = "force_explode" if case == "explode" else "force_faint"
    (tmp_path / "slink.log").write_text(f"[a] faint → {cmd} b:{k}\n"
                                        + ("" if case == "whiteout" else "fully memorialized\n"), encoding="utf-8")
    _, _, _, log = ph(case, "b")
    receipts = {"a": f"ENGINE_FAINT_SITE frame=1\nTX faint {k} {{}}\nTX memorialize_done {k} {{}}\n"
                     "SAVE_WITNESS_DUMP path=p\n",
                "b": log.replace("K0", k).replace("K1", _key(PIDGEY))}
    oracle = getattr(run, duo.SCENARIOS[name]["oracle"])
    oracle(receipts)
    assert notes and f"linked_faint_active[{case}]" in notes[-1]
    with pytest.raises(RuntimeError, match="ACTIVE_HOLD"):
        oracle(
            dict(receipts, b=receipts["b"].replace("ACTIVE_COMMIT", f"ACTIVE_HOLD {k} why=active battler\nACTIVE_COMMIT")))


def test_p_h_receipt_values_are_measured_not_literal(ph):
    """R1 L3: the counts on HANDOFF/ACTIVE_KO come from the observer's own reads. R3 presses L,
    and its KO line carries the real press count and held keys; a set exec bit is logged as 1
    (before the named FAIL), and the happy wild line reads all zeros."""
    _, passed, _, log = ph("wild", "b")
    assert passed is True
    assert re.search(r"^ACTIVE_KO K0 .* inputs=0 keys=0x0 hp_writes=0 attempted=5 case=wild$", log, re.M), log
    _, passed, _, log = ph("lhammer", "b")
    presses = int(re.search(r"^ACTIVE_KO K0 .* inputs=(\d+) keys=", log, re.M).group(1))
    assert passed is True and presses >= 5, log            # one L per frame, commit to KO
    required, _, _ = duo.active_faint_chain("K0", "lhammer")
    assert duo.gen3_receipt_problems("b", log, required=required) == []
    _, passed, msg, log = ph("wild", "b", "exec_set")
    assert passed is False and "exec_bit0=1" in log and "exec bit 0 still set" in msg


def test_a_throwing_watcher_is_reported_by_name_before_it_is_dropped():
    """R1 L4: the driver's own run_watchers body, under lupa."""
    from lupa import LuaRuntime

    text = DRIVER.read_text(encoding="utf-8")
    body = re.search(r"^local function run_watchers\(.*?^end$", text, re.M | re.S).group(0)
    lua = LuaRuntime(unpack_returned_tuples=True)
    run = lua.execute(body + "\nreturn run_watchers")
    reports = []
    watchers = lua.table(lua.eval("function() error('boom', 0) end"),
                         lua.eval("function() return true end"),
                         lua.eval("function() return nil end"))
    names = lua.table("observer", "done", "keeps")
    run(watchers, names, lambda name, err: reports.append((name, err)))
    assert reports == [("observer", "boom")]
    assert len(watchers) == 1 and names[1] == "keeps"
    assert "WATCHER_ERROR scenario=%s watcher=%s" in text


_LFA_SCENARIO = {"wild": "linked_faint_active_gen3", "whiteout": "linked_faint_active_whiteout_gen3",
                 "trainer": "linked_faint_active_trainer_gen3", "lhammer": "linked_faint_active_lhammer_gen3",
                 "explode": "explode_gen3"}

# (side, trained) each linked_faint_active case hands gen3_memorial_problems, in call order.
# A is the side whose engine faint site fires: it fights NATURALLY in every case
# (wild/trainer/lhammer/...), so it can legitimately gain EXP before it goes down. B's KO is
# engine-forced with no input (active_faint_chain), so its growth only moves in the trainer
# case, where PREP_LEVEL levels it up before the hand-off. Whiteout never reaches the check on
# B: its linked mon is B's only mon, so game_over drops the memorialize and the last mon is kept
# (ruling 21) -- B is checked by gen3_last_mon_problems instead.
_LFA_TRAINED_MASK = {
    "wild": (("a", True), ("b", False)),
    "whiteout": (("a", True),),
    "trainer": (("a", True), ("b", True)),
    "lhammer": (("a", True), ("b", False)),
    "explode": (("a", True), ("b", False)),
}


@pytest.mark.parametrize("case", sorted(_LFA_TRAINED_MASK))
def test_linked_faint_active_masks_growth_for_the_natural_side_only(ph, monkeypatch, tmp_path, case):
    """PYDEC FAIL on the RR final cut (d9a928d7): "a: the memorial ... differs from the fixture
    record in [('experience', 228, 267)]". The call site masks growth with
    `trained=inst == "a" or case == "trainer"`, so the table below is the whole rule: A always,
    B only for the trainer case. It is a table rather than one wild case because B is not
    uniformly unmasked -- the trainer case levels B up first (PREP_LEVEL) and the whiteout case
    never memorializes B at all."""
    fixture = _fixture([STARTER, PIDGEY])
    k = _key(STARTER)
    memorial = _saved(fixture, 3, [PIDGEY], {(13, 0): _mon(STARTER["personality"], party=False)})
    kept = _saved(fixture, 3, [STARTER], {(0, 0): _mon(PIDGEY["personality"], party=False, species=16)})
    scenario = _LFA_SCENARIO[case]
    saved = {"a": memorial, "b": kept if case == "whiteout" else memorial}
    links = [{"a": {"key": k}, "b": {"key": k},
              "status": "dead" if case == "whiteout" else "memorial", "cause": "battle"}]
    run, _ = _oracle_stub(monkeypatch, tmp_path, scenario, saved, fixture, links)
    run._link_keys = {"a": k, "b": k}
    calls = []
    monkeypatch.setattr(duo, "gen3_memorial_problems",
                        lambda inst, *a, **kw: calls.append((inst, kw.get("trained"))) or [])
    cmd = "force_explode" if case == "explode" else "force_faint"
    (tmp_path / "slink.log").write_text(
        f"[a] faint → {cmd} b:{k}\n" + ("" if case == "whiteout" else "fully memorialized\n"),
        encoding="utf-8")
    _, _, _, log = ph(case, "b")
    receipts = {"a": (f"ENGINE_FAINT_SITE frame=1\nTX faint {k} {{}}\nTX memorialize_done {k} {{}}\n"
                      f"SAVE_WITNESS_DUMP path=p\n"),
                "b": log.replace("K0", k).replace("K1", _key(PIDGEY))}
    # the receipt verdict itself is test_linked_faint_active_oracle_on_p_h_receipts' job; the
    # mask is decided in the saved-state loop, before the oracle looks at a receipt at all
    getattr(run, duo.SCENARIOS[scenario]["oracle"])(receipts)
    assert calls == list(_LFA_TRAINED_MASK[case])


def test_the_explode_case_gives_b_no_turn_to_win_on(ph, monkeypatch, tmp_path):
    r"""EXPLODE-B-MASK: `active_faint_chain`'s explode branch pins a bare `outcome=\d+`
    (e2e_duo.py:1818-1819 -- the foe usually falls with the user, so the aftermath may be a win, a
    send-out, a whiteout or a draw), which reads like a window where B's linked mon could win a
    battle and bank EXP before its forced KO. It cannot, so `trained` stays narrow:
      * the explode case falls through `enter` to the SAME `ctx.hunt` as wild
        (scenario_gen3_linked_faint_active.lua:280), and hunt_encounter walks with enc=false
        (gen3_scripted_play.lua:1182-1187) -- the first encounter IS the parked battle, never an
        incidental fight run to a win;
      * the chain REQUIRES the PARTY HP word at 0 in the faint site (e2e_duo.py:1812-1813) with
        inputs=0 keys=0x0 across commit->KO (:1810-1811): the mon is fainted IN THE PARTY before
        the battle ends, and the exp path reads that word -- "Our dead mon gets no further exp",
        PROVEN against `src/battle_script_commands.c:3282` and the level-up refresh's
        non-zero-HP guard (`docs/gen3/research/active_faint_in_battle_scope_2026-09-23.md:63,75,250`);
      * the Explosion plan writes the BATTLE mon's moves/PP (lua/gen3/client.lua:731-735), never
        the party record, so `moves` is the fixture's here too. The Explosion can hand EXP to a
        BENCH mon sent out after the KO (the same doc's Explode costs, :163-164), and the oracle
        only diffs the memorial record of `key`, so that is not B's linked mon.
    Red on both edges: a receipt whose party HP word is nonzero is refused, and a B memorial record
    with grown experience is refused -- widening `trained` to the explode case would pass it."""
    ok, passed, msg, log = ph("explode", "b")
    assert ok and passed is True, (msg, log)
    assert re.search(r"^ACTIVE_KO K0 .* last_move=153 inputs=0 keys=0x0 hp_writes=0 attempted=\d+ case=explode$",
                     log, re.M), log
    assert re.search(r"^ACTIVE_FAINT_SITE K0 .* battle_hp=0 party_hp=0 counter=0->1$", log, re.M), log
    required, ordered, forbidden = duo.active_faint_chain("K0", "explode")
    assert duo.gen3_receipt_problems("b", log, required=required, ordered=ordered, forbidden=forbidden) == [], log
    assert duo.gen3_receipt_problems("b", log.replace("party_hp=0", "party_hp=128"), required=required,
                                     ordered=ordered, forbidden=forbidden)

    fixture = _fixture([STARTER, PIDGEY])
    k = _key(STARTER)
    grown = _mon(STARTER["personality"], party=False)
    grown["experience"] = 1261
    link = [{"a": {"key": k}, "b": {"key": k}, "status": "memorial", "cause": "battle"}]

    def run_for(record):
        saved = _saved(fixture, 3, [PIDGEY], {(13, 0): record})
        run, _ = _oracle_stub(monkeypatch, tmp_path, "explode_gen3", {"a": saved, "b": saved}, fixture, link)
        run._link_keys = {"a": k, "b": k}
        (tmp_path / "slink.log").write_text(f"[a] faint → force_explode b:{k}\nfully memorialized\n",
                                           encoding="utf-8")
        return run

    _, _, _, log_b = ph("explode", "b")
    receipts = {
        "a": f"ENGINE_FAINT_SITE frame=1\nTX faint {k} {{}}\nTX memorialize_done {k} {{}}\n"
             f"SAVE_WITNESS_DUMP path=p\n",
        "b": log_b.replace("K0", k) + f"TX memorialize_done {k} {{}}\n",
    }
    real = duo.gen3_memorial_problems
    calls = []
    monkeypatch.setattr(duo, "gen3_memorial_problems",
                        lambda inst, *a, **kw: calls.append((inst, kw.get("trained"))) or [])
    run_for(_mon(STARTER["personality"], party=False)).assert_explode_gen3_saved(receipts)
    assert ("a", True) in calls     # A fights naturally in every case, explode included
    assert ("b", False) in calls    # B's mon is engine-fainted before any EXP award

    monkeypatch.setattr(duo, "gen3_memorial_problems", real)
    with pytest.raises(RuntimeError, match="differs from the fixture"):
        run_for(grown).assert_explode_gen3_saved(receipts)


def test_linked_faint_active_oracle_keeps_the_last_mon_on_both_sides(ph, monkeypatch, tmp_path):
    """R4 on RR's one-mon rr_battle.sav: A's natural faint and B's Perish KO both leave the lone
    linked mon in its party after game_over (ruling 21); no deposit, link DEAD, no memorial."""
    fixture = _fixture([STARTER])
    k = _key(STARTER)
    kept = _saved(fixture, 3, [STARTER])
    run, notes = _oracle_stub(monkeypatch, tmp_path, "linked_faint_active_whiteout_gen3", {"a": kept, "b": kept},
                              fixture, [{"a": {"key": k}, "b": {"key": k}, "status": "dead", "cause": "battle"}])
    run._link_keys = {"a": k, "b": k}
    (tmp_path / "slink.log").write_text(f"[a] faint → force_faint b:{k}\n", encoding="utf-8")
    _, _, _, log_a = ph("wild", "a", "lone")
    _, _, _, log_b = ph("whiteout", "b", "lone")
    receipts = {"a": f"ENGINE_FAINT_SITE frame=1\nTX faint {k} {{}}\n" + log_a.replace("K0", k)
                + "SAVE_WITNESS_DUMP path=p\n", "b": log_b.replace("K0", k)}
    run.assert_linked_faint_active_whiteout_gen3_saved(receipts)
    assert "last mon kept: a,b" in notes[-1]
    with pytest.raises(RuntimeError, match="memorialize dropped"):
        run.assert_linked_faint_active_whiteout_gen3_saved(
            dict(receipts, a=receipts["a"].replace("memorialize dropped", "memorialize kept")))


@pytest.mark.parametrize("kind", ["clean", "companion"])
def test_the_carrier_cpu_verdict_is_the_product_safety_over_the_pack(kind):
    """G5-RR-CARRIER-FIX item 2: the carrier no longer copies the CPU shape (its copy flagged
    "cpu" on every RR frame once frames ended on the BIOS IRQ entry, owner ruling 23). Its
    cpu_parked asks lua/gen3/safety.lua itself, over the committed RR pack's irq_entry."""
    from test_gen3_safety import HALT_LR, IRQ_CPSR, World, irq_world

    def verdict(world):
        body = _lua_defs(DRIVER, ["cpu_parked"])
        return world.lua.execute(body + "\nreturn cpu_parked")(world.safety)

    assert verdict(irq_world("radical_red", kind, HALT_LR)) is True            # the halt's IRQ entry
    assert verdict(World("radical_red", kind)) is True                         # the System halt
    assert verdict(irq_world("radical_red", kind, 0x0800_0A1C)) is False       # IRQ from game code
    broken = World("radical_red", kind)
    broken.lua.execute("for k in pairs(rom) do rom[k] = (rom[k] + 1) % 256 end")   # anchors differ
    assert verdict(broken) is None                                             # no clause ran
    assert verdict(irq_world("firered", "clean", HALT_LR, cpsr=IRQ_CPSR)) is False   # FR admits none


def test_only_a_battle_berry_eaten_in_battle_is_not_a_record_change(pair):
    """G5-RR-ORACLES-2 (OMP review of 410d9578): the consumed-item rule is narrowed to a
    GEN3_BATTLE_BERRIES id going to NONE on a mon that battled. Falsifiers: another item cleared
    (13 -> 0) fails for the last-mon and the memorial checks; 139 -> 0 without a battle fails; a
    berry swapped (139 -> 13) fails; 139 -> 0 in battle passes."""
    k = _key(STARTER)
    fixture = _fixture([dict(STARTER, held_item=139), PIDGEY])
    plain = _fixture([dict(STARTER, held_item=13), PIDGEY])

    def last(fix, item, battled):
        saved = _saved(fix, 3, [dict(STARTER, held_item=item)])
        return duo.gen3_last_mon_problems("a", _decoded(saved), _decoded(fix), k, [PIDGEY and _key(PIDGEY)], 13,
                                          limits=LIMITS, battled=battled)

    def memorial(fix, item, battled):
        saved = _saved(fix, 3, [PIDGEY], {(13, 0): dict(_mon(STARTER["personality"], party=False), held_item=item)})
        return _mem("a", _decoded(saved), _decoded(fix), k, 13, battled=battled)

    held = lambda problems: any("held_item" in p for p in problems)  # noqa: E731
    assert not held(last(fixture, 0, True)) and not held(memorial(fixture, 0, True))
    assert held(last(plain, 0, True)) and held(memorial(plain, 0, True))           # 13 -> 0
    assert held(last(fixture, 0, False)) and held(memorial(fixture, 0, False))     # no battle
    assert held(last(fixture, 13, True)) and held(memorial(fixture, 13, True))     # swapped
    # F2 (OMP cx-ba4598d7): the whole set, pinned -- and, when the pret cache is present, equal
    # to the berries src/data/items.json gives a battle holdEffect
    assert frozenset(range(133, 148)) | frozenset(range(168, 175)) == duo.GEN3_BATTLE_BERRIES
    assert len(duo.GEN3_BATTLE_BERRIES) == 22


def _rom_dump(name):
    """A dump in the repo root or any parent (the _gen3_rom search), else None."""
    for base in (REPO, *REPO.parents):
        if (base / name).is_file():
            return base / name
    return None


RR_DUMP = _rom_dump("Pokemon - Radical Red.gba")
FR_DUMP = _rom_dump("Pokemon - FireRed Version (USA).gba")


RR_ARTIFACTS = {  # sha1 -> path: the clean 4.1 dump and the companion build SLink ships
    "964f951a0fdaf209e4ea1344883ef0d557bb3a80": RR_DUMP,
    rr_companion.rom_sha1(): REPO / "patch" / "build" / "slink_RR.gba",     # patch/dist/companion_pins.json, written by the build
}


# (the companion no longer carries a Task_HandleChooseMonInput 0x0811FB29 literal: its party chooser
# calls RR's ChoosePartyMonByMenuType, which owns that reference)
# 0x0802EA11's companion referrer moved 0x08379D84 -> 0x08379D88 when the title/menu-version payload (companion
# sha1 e87a6a7e, patch/dist/companion_pins.json) grew the payload by 4 bytes ahead of it: a pure relocation (the
# count 8 + 1 is unchanged and every other pinned referrer is where it was)
COMPANION_EXTRA_REFS = {0x02023FFC: [0x08378F44, 0x09360318], 0x0802EA11: [0x08379D88]}


def _pret_battle_berries():
    import json as _json

    for base in (REPO, *REPO.parents):
        items = base / ".cache" / "pret" / "pokefirered" / "src" / "data" / "items.json"
        if items.is_file():
            ids = {}
            for line in (items.parent.parent.parent / "include" / "constants" / "items.h").read_text().splitlines():
                m = re.match(r"#define (ITEM_\w+) (\d+)$", line)
                if m:
                    ids[m.group(1)] = int(m.group(2))
            doc = _json.loads(items.read_text(encoding="utf-8"))
            return {ids[i["itemId"]] for i in (doc["items"] if isinstance(doc, dict) else doc)
                    if "_BERRY" in i["itemId"] and i["itemId"] != "ITEM_BERRY_JUICE"
                    and i.get("holdEffect", "HOLD_EFFECT_NONE") != "HOLD_EFFECT_NONE"}
    return None


def test_the_battle_berry_set_is_pret_s():
    """F2: GEN3_BATTLE_BERRIES is exactly pret's battle-holdEffect berries (skip only without
    the pret cache)."""
    pret = _pret_battle_berries()
    if pret is None:
        pytest.skip("no pret pokefirered cache in the repo root or a parent")
    assert frozenset(pret) == duo.GEN3_BATTLE_BERRIES


def _rr_rom(sha):
    import hashlib

    path = RR_ARTIFACTS[sha]
    if not (path and path.is_file() and FR_DUMP):
        return None
    rom = path.read_bytes()
    assert hashlib.sha1(rom).hexdigest() == sha, f"{path} is not the pinned artifact {sha}"
    return rom


@pytest.mark.parametrize("sha", sorted(RR_ARTIFACTS))
def test_rr_party_menu_words_hold_in_the_rr_rom(sha):
    """The party-menu and battle/bag notes in lua/tests/gen3_title_syms.lua, re-read from each
    RR artifact (G5-RR-ORACLES-2/-3 F6: the clean dump AND the shipped companion), sha1 first.
    Pinned exactly: identical bodies and referrers; Task_HandleSelectionMenuInput's 188-byte
    prologue; the CFRU detour stubs; exact literal counts; the +9 slotId reads in both the
    FR-identical region and CFRU; the battle-controller and bag words."""
    import hashlib
    import struct

    rr = _rr_rom(sha)
    if rr is None:
        pytest.skip(f"RR artifact {sha[:8]} or the FR dump is not present")
    fr = FR_DUMP.read_bytes()
    assert hashlib.sha1(fr).hexdigest() == "41cb23d8dccc8ebd7c649cd8fbb58eeace6e2fdc"

    def body(rom, addr, n):
        return rom[addr - 0x08000000:addr - 0x08000000 + n]

    def word(rom, addr):
        return struct.unpack_from("<I", rom, addr - 0x08000000)[0]

    def refs(rom, value):
        lit, out, i = struct.pack("<I", value), [], rom.find(struct.pack("<I", value))
        while i >= 0:
            out.append(0x08000000 + i)
            i = rom.find(lit, i + 1)
        return out

    def field_reads(rom, lo, hi, value, offset):
        """ldr rd,[pc,#] of `value`, then a later ldrb rX,[rd,#offset] before rd is reused."""
        hits = []
        for off in range(lo - 0x08000000, hi - 0x08000000, 2):
            hw = struct.unpack_from("<H", rom, off)[0]
            if hw & 0xF800 != 0x4800:
                continue
            rd, pc = (hw >> 8) & 7, 0x08000000 + off
            pool = ((pc + 4) & ~3) + (hw & 0xFF) * 4
            if struct.unpack_from("<I", rom, pool - 0x08000000)[0] != value:
                continue
            for k in range(1, 12):
                nxt = struct.unpack_from("<H", rom, off + 2 * k)[0]
                if nxt & 0xF800 == 0x7800 and (nxt >> 3) & 7 == rd and (nxt >> 6) & 0x1F == offset:
                    hits.append(pc)
                    break
                if nxt & 7 == rd and nxt & 0xF800 not in (0x7000, 0x6000, 0x8000):   # rd rewritten
                    break
        return hits

    # party menu (G5-RR-ORACLES)
    assert body(rr, 0x0811EBA0, 0x1A) == body(fr, 0x0811EBA0, 0x1A)
    assert refs(rr, 0x0811EBA1) == refs(fr, 0x0811EBA1) == [0x0811EE28, 0x0811EE70]
    assert body(rr, 0x081203B8, 0x68) == body(fr, 0x081203B8, 0x68)
    assert body(rr, 0x08122C5C, 188) == body(fr, 0x08122C5C, 188)
    assert body(rr, 0x0811FB28, 4) == bytes.fromhex("00490847")                       # ldr r1,[pc]; bx r1
    # the companion's own code (0x0837xxxx / 0x0936xxxx) adds these referrers, nothing else
    extra = COMPANION_EXTRA_REFS if RR_ARTIFACTS[sha].name == "slink_RR.gba" else {}

    def exact(value, base):
        got = refs(rr, value)
        assert len(got) == base + len(extra.get(value, [])) and set(extra.get(value, [])) <= set(got), hex(value)

    exact(0x0811FB29, 29)
    exact(0x08122C5D, 4)
    exact(0x0203B0A0, 179)
    assert len(refs(fr, 0x0203B0A0)) == 153
    fr_reads = field_reads(fr, 0x0811E000, 0x08126000, 0x0203B0A0, 9)
    assert field_reads(rr, 0x0811E000, 0x08126000, 0x0203B0A0, 9) == fr_reads and len(fr_reads) >= 20
    assert 0x090B3360 in field_reads(rr, 0x090B0000, 0x090B7000, 0x0203B0A0, 9)       # CFRU reads +9 too
    # battle controller / bag (G5-RR-BATTERY)
    assert word(rr, 0x090AA178) == 0x02023BC4                     # CFRU action menu: ldr r4,=gActiveBattler
    assert refs(rr, 0x0802E3B5) == [0x0802E334, 0x090445FC]       # PlayerBufferRunCommand
    assert refs(rr, 0x0802EA11)[:4] == refs(fr, 0x0802EA11) == [0x0802E79C, 0x0802F398, 0x0802F3FC, 0x08032C8C]
    assert body(rr, 0x0802EA10, 4) == bytes.fromhex("00480047") and word(rr, 0x0802EA14) == 0x090AB8B9
    exact(0x02023FFC, 51)                                          # gMoveSelectionCursor
    exact(0x0802EA11, 8)
    assert body(rr, 0x08107EE0, 0x1A) == body(fr, 0x08107EE0, 0x1A)
    assert body(rr, 0x08108CFC, 0x64) == body(fr, 0x08108CFC, 0x64)
    for task in (0x08107EE1, 0x08108F0D, 0x08108CFD):
        assert refs(rr, task) == refs(fr, task), hex(task)
    lua_syms = (REPO / "lua" / "tests" / "gen3_title_syms.lua").read_text(encoding="utf-8")
    for w in ("0x0811EBA1", "0x0811FB29", "0x081203B9", "0x08122C5D", "0x0203B0A0", "0x02023BC4",
              "0x0802E3B5", "0x0802EA11", "0x02023FFC", "0x08107EE1", "0x08108F0D", "0x08108CFD"):
        assert f"radical_red = {w}" in lua_syms, w


def test_the_rr_carrier_symbols_are_proven_only_and_fail_closed():
    """F4 (OMP cx-ba4598d7): on radical_red the carrier's symbol table comes ONLY from
    gen3_title_syms, the RR profile and the RR pack -- never pokefirered.sym -- and a symbol no
    source proves raises by name at its use. Sentinel: every symbol the RR rows read is present;
    an FR-only one (the Cable Club script) raises."""
    from lupa import LuaError, LuaRuntime

    text = DRIVER.read_text(encoding="utf-8")
    body = re.search(r"^local function rr_symbols\(.*?^end$", text, re.M | re.S).group(0)
    lua = LuaRuntime(unpack_returned_tuples=True)
    build = lua.execute(body + "\nreturn rr_symbols")
    syms = re.findall(r'"(\w+)"', text[text.index("local SYMS = {"):text.index("--- radical_red's symbol table")])
    titles = lua.execute(f'return dofile("{(REPO / "lua/tests/gen3_title_syms.lua").as_posix()}")')
    ram = json.loads((REPO / "data/games/gen3_rr/profile.json").read_text(encoding="utf-8"))["titles"]["radical_red"]["ram"]
    pack = json.loads((REPO / "data/games/gen3_rr/write_checkpoint.json").read_text(encoding="utf-8"))["radical_red"]
    s = build(lua.table_from(dict.fromkeys(syms, True)), titles.for_title("radical_red"), titles.entries,
              lua.table_from(ram, recursive=True), lua.table_from(pack, recursive=True))
    rr_rows_read = {"gBattlerControllerFuncs": 0x03004FE0, "HandleInputChooseAction": 0x0802E438,
                    "HandleInputChooseMove": 0x0802EA10, "gActionSelectionCursor": 0x02023FF8,
                    "gMoveSelectionCursor": 0x02023FFC, "gBattleMons": 0x02023BE4,
                    "gBattlerPartyIndexes": 0x02023BCE, "gBattleControllerExecFlags": 0x02023BC8,
                    "gBattleOutcome": 0x02023E8A, "gMain": 0x030030F0, "gTasks": 0x03005090,
                    "gPartyMenu": 0x0203B0A0, "CB2_UpdatePartyMenu": 0x0811EBA0,
                    "Task_HandleChooseMonInput": 0x0811FB28, "Task_HandleSelectionMenuInput": 0x08122C5C,
                    "Task_ReturnToChooseMonAfterText": 0x081203B8, "gActiveBattler": 0x02023BC4,
                    "gBattleResults": 0x03004F90, "gStatuses3": 0x02023DFC,
                    "PlayerBufferExecCompleted": 0x0802E33C, "PlayerBufferRunCommand": 0x0802E3B4,
                    "CB2_BagMenuRun": 0x08107EE0, "Task_BagMenu_HandleInput": 0x08108F0C,
                    "Task_AnimateWin0v": 0x08108CFC, "gPaletteFade": 0x02037AB8,
                    "Task_DepositMenu": 0x0808DD88, "Task_WithdrawMon": 0x0808DC9C,
                    "gSaveBlock1Ptr": 0x03005008}
    for name, want in rr_rows_read.items():
        assert s[name] == want, (name, hex(s[name]))
    with pytest.raises(LuaError, match="CableClub_EventScript_WelcomeToCableClub is unproven for radical_red"):
        lua.eval("function(s) return s.CableClub_EventScript_WelcomeToCableClub end")(s)
    rr_branch = text[text.index('if title == "radical_red" then\n    local want'):text.index("else\n    local want")]
    assert ".sym" not in rr_branch and "pokefirered" not in rr_branch


def test_the_forced_send_out_walks_back_from_confirm_or_cancel():
    """R1-R3 at f4ef3f5a: forced_party_cursor_invalid -- the cursor read a row below the mons
    (6 CONFIRM / 7 CANCEL). cursor_step walks Up from there; only a value that is no row fails."""
    from lupa import LuaRuntime

    text = SCRIPTED.read_text(encoding="utf-8")
    body = re.search(r"^local function cursor_step\(.*?^end$", text, re.M | re.S).group(0)
    step = LuaRuntime(unpack_returned_tuples=True).execute(body + "\nreturn cursor_step")
    assert step(1, 1, 2)[0] == "done" if isinstance(step(1, 1, 2), tuple) else step(1, 1, 2) == "done"
    first = lambda r: r[0] if isinstance(r, tuple) else r  # noqa: E731
    assert first(step(0, 1, 2)) == "Down" and first(step(2, 1, 3)) == "Up"
    assert first(step(7, 1, 2)) == "Up" and first(step(6, 1, 2)) == "Up"
    ok, why = step(9, 1, 2)
    assert ok is None and "no party-menu row" in why
    assert "cursor_step(slot, target, count)" in text


def test_the_carrier_takes_title_syms_values_first():
    """G5-RR-ORACLES-2: duo_gen3_main.lua overlays every symbol gen3_title_syms proves for the
    title on its .sym read (one source of truth with the helpers). On FR/LG the two agree."""
    text = DRIVER.read_text(encoding="utf-8")
    assert 'Titles.for_title(title)' in text and "S[e.symbol] = v - (e.offset or 0) - (e.thumb and 1 or 0)" in text


def test_the_send_out_accepts_an_rr_record_without_a_checksum():
    """G5-RR-R1R3 (R1/R2/R3 on rr_battle2 at 410d9578: forced_party_no_healthy_mon). The fixture is
    sound (Treecko 22/22 + the Route 1 catch 18/18, gen3_codec rr=True); RR's reader leaves
    checksum_ok nil (CFRU has no secure checksum) and the send-out demanded true. record_ok takes
    nil on radical_red only; the send-out uses it."""
    from lupa import LuaRuntime

    text = SCRIPTED.read_text(encoding="utf-8")
    body = re.search(r"^local function record_ok\(.*?^end$", text, re.M | re.S).group(0)
    lua = LuaRuntime()
    ok = lua.execute(body + "\nreturn record_ok")
    def rec(v):
        return lua.table_from({"checksum_ok": v} if v is not None else {})
    assert ok(rec(None), "radical_red") is True and ok(rec(True), "radical_red") is True
    assert ok(rec(False), "radical_red") is False
    assert ok(rec(None), "firered") is False and ok(rec(True), "leafgreen") is True
    start = text.index("send_out_healthy_mon = function")
    send = text[start:text.index("forced_party_no_healthy_mon", start)]
    assert "record_ok(mon, TITLE)" in send and "mon.checksum_ok" not in send
    fixture = duo.gen3_decode((REPO / "tests/fixtures/gen3/rr_battle2.sav").read_bytes(), rr=True)[0]
    assert [(m["hp"], m["checksum_ok"]) for m in fixture] == [(22, None), (18, None)]


def test_a_caught_keys_memorial_is_compared_against_its_capture_event(pair):
    """F3 (OMP cx-ba4598d7): a key the fixture never carried (deadzone's refused catch) had its
    memorial record compared against nothing. It is now checked against the client's own capture
    event (species, held item), and a missing event is a problem, never a skip."""
    fixture, _ = pair
    catch = _mon(CATCH["personality"], party=False, species=19)
    catch["held_item"] = 0
    saved = _saved(fixture, 3, [STARTER, PIDGEY], {(13, 0): catch})
    k = _key(CATCH)
    sent = {"species_id": 19, "held_item_id": 0}
    assert _mem("b", _decoded(saved), _decoded(fixture), k, 13, battled=True, captured=sent) == []
    assert any("capture event" in p for p in _mem("b", _decoded(saved), _decoded(fixture), k, 13, battled=True))
    assert any("held_item" in p for p in
               _mem("b", _decoded(saved), _decoded(fixture), k, 13, battled=True, captured=dict(sent, held_item_id=139)))
    assert any("species" in p for p in
               _mem("b", _decoded(saved), _decoded(fixture), k, 13, battled=True, captured=dict(sent, species_id=16)))


def test_rr_rows_that_link_or_throw_boot_rr_battle2():
    """G5-RR-BATTERY (live 3fa789da: KeyError 1 on slot-1 links over the one-mon rr_town, no
    balls on rr_battle): every RR row that links/trades slot 1 or throws a ball boots rr_battle2."""
    for name in ("faint_cmd_gen3", "boxsync_gen3", "whiteout_gen3", "link_gen3", "deadzone_gen3",
                 "reconnect_gen3", "clean_rr_refused_gen3", "linked_faint_active_gen3"):
        assert duo.scenario_target(duo.SCENARIOS[name], "gen3_rr") == "battle2", name
    fr = {"faint_cmd_gen3": "town", "link_gen3": "catch_synth", "boxsync_gen3": {"a": "battle", "b": "town"}}
    for name, want in fr.items():
        assert duo.scenario_target(duo.SCENARIOS[name], "gen3_frlg") == want, name
    # link_gen3 hunts 20-ball SYNTH saves on FR/LG (the LG battle save's 2 balls ran out 3/3)
    assert duo.scenario_target(duo.SCENARIOS["link_gen3"], "gen3_lgfr") == "catch_synth"
    for title in ("firered", "leafgreen"):
        sav = (REPO / f"tests/fixtures/gen3/{title}_party_catch_synth.sav").read_bytes()
        assert duo.gen3_ball_count(sav, title) >= 20, title
    fixture = duo.gen3_decode((REPO / "tests/fixtures/gen3/rr_battle2.sav").read_bytes(), rr=True)[0]
    assert len(fixture) == 2 and duo.gen3_ball_count((REPO / "tests/fixtures/gen3/rr_battle2.sav").read_bytes(),
                                                     "radical_red") == 9


def test_rr_storage_spans_are_the_cfru_box_regions():
    """G5-RR-BATTERY-2 (reconnect_gen3 on RR: 'arithmetic on a nil value (field MONS_PER_BOX)'):
    the carrier's PC byte compare covers each CFRU box region from the RR pack (30 * 58 bytes at
    every CFRU_BOX_BASES entry, as reads.lua read_box decodes them); FR/LG keep the one
    PokemonStorage span (0x83D0)."""
    from lupa import LuaRuntime

    text = DRIVER.read_text(encoding="utf-8")
    body = re.search(r"^local function storage_spans\(.*?^end$", text, re.M | re.S).group(0)
    lua = LuaRuntime(unpack_returned_tuples=True)
    spans = lua.execute(body + "\nreturn storage_spans")
    rr = json.loads((REPO / "data/games/gen3_rr/profile.json").read_text(encoding="utf-8"))["titles"]["radical_red"]
    got = spans(lua.table_from(rr["derived"], recursive=True), 0x02029314, 30)
    assert [(got[i][1], got[i][2]) for i in range(1, len(got) + 1)] == [
        (base, 30 * 58) for base in rr["derived"]["CFRU_BOX_BASES"]]
    fr = json.loads((REPO / "data/games/gen3_frlg/profile.json").read_text(encoding="utf-8"))["titles"]["firered"]
    one = spans(lua.table_from(fr["derived"], recursive=True), 0x02029314, 30)
    assert len(one) == 1 and (one[1][1], one[1][2]) == (0x02029314, 0x83D0)


def test_rr_bag_pocket_is_read_from_ewram():
    """G5-RR-BATTERY-2: the throw helper's POKe BALLS pocket read on RR is the pack's EWRAM pocket
    (BAG_IN_EWRAM, ram.BALL_POCKET_ADDR, 50 slots), not SaveBlock1 +0x430."""
    from lupa import LuaRuntime

    text = SCRIPTED.read_text(encoding="utf-8")
    body = re.search(r"^local function bag_pokeballs_item_id\(.*?^end$", text, re.M | re.S).group(0)
    lua = LuaRuntime(unpack_returned_tuples=True)
    rr = json.loads((REPO / "data/games/gen3_rr/profile.json").read_text(encoding="utf-8"))["titles"]["radical_red"]
    lua.globals().profile = lua.table_from(rr, recursive=True)
    lua.execute("reads = {}; memory = {read_u16_le = function(a) reads[#reads + 1] = a; return 4 end}; "
                "SB1_POKEBALLS_POCKET_OFFSET = 0x430; function sb1_ptr() error('SaveBlock1 read on RR') end")
    fn = lua.execute(body + "\nreturn bag_pokeballs_item_id")
    assert fn(None, 3) == 4 and lua.globals().reads[1] == rr["ram"]["BALL_POCKET_ADDR"] + 12
    assert "profile.derived.SB1_BALL_POCKET_COUNT or 13" in text


def test_every_harness_note_entry_keeps_its_fr_referrers():
    """G5-RR-BATTERY-2: the rule in docs/gen3/research/rr_harness_syms_2026-09-24.md, run through
    its own derivation tool on every artifact present -- each gen3_title_syms entry citing the note
    keeps ALL FR literal-pool referrers at the same ROM address, and its RR value is FR's."""
    sys.path.insert(0, str(REPO / "tools" / "research"))
    import rr_harness_syms as tool

    fr = tool.load("Pokemon - FireRed Version (USA).gba", tool.FR_SHA1)
    if fr is None:
        pytest.skip("the FR dump is not in the repo root or a parent")
    cited = tool.cited_entries()
    assert len(cited) >= 25 and not any(name.startswith("PLAYER_BUFFER") for name, *_ in cited)
    sizes, checked = tool.sym_sizes(), 0
    for sha, rel in tool.RR_ARTIFACTS.items():
        rr = tool.load(rel, sha)
        if rr is None:
            continue
        for name, _symbol, value, thumb, rr_value in cited:
            ev = tool.evidence(fr, rr, value, thumb, sizes)
            assert ev["fr_refs"] > 0 and ev["kept"] and rr_value == value, (sha[:8], name, ev)
        checked += 1
    if not checked:
        pytest.skip("no RR artifact present")


def test_the_pc_owner_list_exit_on_rr_rests_on_the_field_terminal():
    """G5-RR-PC (live boxsync/whiteout at e99c3760: pc_exit_owner_not_canceled with result=0 and
    only field tasks live). FR/LG still need SCR_MENU_CANCEL (127) seen; RR accepts the list
    closing with no PC task taking over, and leave_storage's field-terminal wait follows."""
    from lupa import LuaRuntime

    text = SCRIPTED.read_text(encoding="utf-8")
    body = re.search(r"^local function owner_list_closed\(.*?^end$", text, re.M | re.S).group(0)
    closed = LuaRuntime().execute(body + "\nreturn owner_list_closed")
    assert closed(True, True, "radical_red", False) is False          # still open
    assert closed(False, True, "firered", False) is True               # FR: the 127 was seen
    assert closed(False, False, "firered", False) is False             # FR: never without it
    assert closed(False, False, "radical_red", False) is True          # RR: closed, nothing took over
    assert closed(False, False, "radical_red", True) is False          # RR: a row reopened the PC
    exit_wait = text[text.index('"exit_owner_not_canceled"'):text.index('"exit_not_field"')]
    assert "owner_list_closed(" in exit_wait and "saw_cancel" in exit_wait


def test_rr_way_out_of_viridian_is_the_proven_way_in_reversed():
    """G5-RR-LAST (whiteout_gen3 on RR at 6131930f: 'pokecenter_door_to_route1_edge: step Down
    stalled at (26,28)'). On radical_red the helper walks the live-proven inbound path reversed,
    and every step of it is walkable on the RR ROM's own map 3.1 collision (tools/gba_map.py)."""
    from lupa import LuaRuntime

    os.environ.setdefault("SLINK_ROOT", str(REPO).replace("\\", "/"))
    lua = LuaRuntime(unpack_returned_tuples=True)
    lua.globals().SLINK_GEN3_TITLE = "radical_red"
    rr = lua.execute(f'return dofile("{SCRIPTED.as_posix()}")')
    out, inn = rr.PATHS["pokecenter_door_to_route1_edge"], rr.PATHS["route1_edge_to_pokecenter_door"]
    dirs = [out.dirs[i] for i in range(1, len(out.dirs) + 1)]
    flip = {"Up": "Down", "Down": "Up", "Left": "Right", "Right": "Left"}
    assert dirs == [flip[inn.dirs[i]] for i in range(len(inn.dirs), 0, -1)]
    assert (out["from"][1], out["from"][2], out["to"][1], out["to"][2]) == (26, 27, 24, 39)
    fr = LuaRuntime(unpack_returned_tuples=True)
    fr.globals().SLINK_GEN3_TITLE = "firered"
    frp = fr.execute(f'return dofile("{SCRIPTED.as_posix()}")').PATHS["pokecenter_door_to_route1_edge"]
    assert [frp.dirs[i] for i in range(1, 3)] == ["Down", "Down"]          # FR keeps its own path
    rom = _rom_dump("Pokemon - Radical Red.gba")
    if rom is None:
        pytest.skip("the RR dump is not in the repo root or a parent")
    sys.path.insert(0, str(REPO / "tools"))
    import gba_map

    m = gba_map.load(str(rom), sym_path=str(REPO / "data/gen3/pret/pokefirered.sym")).map(3, 1)
    step = {"Up": (0, -1), "Down": (0, 1), "Left": (-1, 0), "Right": (1, 0)}
    x, y = 26, 27
    for d in dirs:
        x, y = x + step[d][0], y + step[d][1]
        assert m.collision[y][x] == 0, (d, x, y)
        assert (x, y) not in {(o.x, o.y) for o in m.objects}, (x, y)
    assert (x, y) == (24, 39)


def test_traced_follow_is_rr_only_and_never_reads_a_nil_obj_events_addr():
    """G5-RR-WHITEOUT (whiteout_gen3 on RR at c23a8f46: 'pokecenter_door_to_route1_edge: step
    Left stalled at (25,27)'). gen3_title_syms.lua's OBJ_EVENTS_ADDR carries no radical_red
    value ("no RR citation found"), so it is nil on RR -- run 2 crashed inside the (then
    diagnostic-only) trace on exactly that nil arithmetic. rr_trace_log must fall back to the
    independently-confirmed RR address (reference_rr_object_events.md) instead of dereferencing
    OBJ_EVENTS_ADDR directly. (The behavioural half of this -- FR/LG never taking the RR loop,
    the loop itself -- is covered executably below, not by grepping source text.)
    """
    text = SCRIPTED.read_text(encoding="utf-8")
    trace_start = text.index("local function rr_trace_log(")
    trace_fn = text[trace_start:text.index("\nend", trace_start)]
    assert "local base = OBJ_EVENTS_ADDR or RR_OBJ_EVENTS_ADDR" in trace_fn
    assert "RR_OBJ_EVENTS_ADDR = 0x02036E38" in text
    # exported for duo_gen3_main.lua, and actually wired into the real stall site (not left
    # dangling on the dead recover_to_pallet_town call path the first cut of this fix used).
    assert "traced_follow = traced_follow," in text
    duo_text = (REPO / "lua" / "tests" / "duo" / "duo_gen3_main.lua").read_text(encoding="utf-8")
    assert 'SP.traced_follow(cp, "pokecenter_door_to_route1_edge", label)' in duo_text


# ── G5-RR-FOLLOW-HARDEN: traced_follow's RR recovery, executable over a fake play/H/G (OMP
# cx-84088887 -- the old version of this test only grepped traced_follow's source text, which
# cannot tell a witnessed recovery from a blind one). rr_trace_log is spliced in alongside
# traced_follow in the SAME lua.execute() chunk so the "local function" it is written as stays a
# real Lua upvalue traced_follow can call, exactly as the two sit in gen3_scripted_play.lua.
_TF_WORLD = r"""
W = { map = 100, x = 5, y = 5, in_battle = false, on_field = true, quiet = true,
      step_calls = 0, follow_calls = 0, wait_at_calls = 0, dialogue_calls = 0,
      battle_calls = 0, enc_seen = {}, round_effects = {}, step_queue = {}, log = {} }

local function pop_effect()
    local fx = table.remove(W.round_effects, 1)
    if fx then fx() end
end

G = {
    shot = function() end,
    finish = function(ok, msg)
        W.log[#W.log + 1] = "RESULT: " .. (ok and "PASS" or "FAIL") .. " " .. tostring(msg)
        error("FINISH:" .. tostring(msg), 0)
    end,
}
H = {
    pos = function() return W.x, W.y end,
    scene_quiet = function() return W.quiet end,
}
console = { log = function(s) W.log[#W.log + 1] = s end }
PATHS = { test_path = { from = { 1, 1 }, dirs = { "Right" } } }
-- rr_trace_log's fallback (nil OBJ_EVENTS_ADDR on RR) and its bus reads, stubbed inert: every
-- test here runs with RR_TRACE off, so this only needs to exist, never to answer usefully.
RR_OBJ_EVENTS_ADDR = 0x02036E38
OBJ_EVENTS_ADDR = nil
memory = { read_u8 = function() return 0 end, read_s16_le = function() return 0 end }

play = { opts = { max_encounters = 12 } }
function play.follow(cp, path_name, label) W.follow_calls = W.follow_calls + 1 end
function play.map(cp) return W.map end
function play.at(cp) return string.format("(%d,%d)", W.x, W.y) end
function play.wait_at(cp, x, y, budget) W.wait_at_calls = W.wait_at_calls + 1; return true end
function play.in_battle(cp) return W.in_battle end
function play.on_field(cp) return W.on_field end
--- Stands in for playlib's real P.clear_dialogue: a mashed A. The per-test `round_effects`
--- queue is how a test scripts what that mashing eventually achieves (the lock clearing, an
--- unwanted warp, ...), one effect consumed per recovery round.
function play.clear_dialogue(cp)
    W.dialogue_calls = W.dialogue_calls + 1
    pop_effect()
end
--- Stands in for playlib's real P.handle_encounter: bumps the SAME `enc` budget object
--- traced_follow owns (the accounting finding 2 requires it route through), and records every
--- value enc.n took so a test can see the budget was actually charged.
function play.handle_encounter(cp, enc, dir, before)
    enc.n = enc.n + 1
    W.battle_calls = W.battle_calls + 1
    W.enc_seen[#W.enc_seen + 1] = enc.n
    pop_effect()
end
function play.step(cp, dir, start_map, want, enc)
    W.step_calls = W.step_calls + 1
    local r = W.step_queue[W.step_calls]
    if r == nil then r = W.step_queue[#W.step_queue] end
    return r
end
"""


def _traced_follow_env(title, rr_trace=False):
    from lupa import LuaRuntime

    lua = LuaRuntime(unpack_returned_tuples=True)
    lua.execute(_TF_WORLD)
    lua.globals().TITLE = title
    lua.globals().RR_TRACE = rr_trace
    lua.execute(_lua_defs(SCRIPTED, ["rr_trace_log", "traced_follow"])
                + "\nTRACED_FOLLOW = traced_follow\n"
                  "function W.run(cp, path_name, label)\n"
                  "    local ok, err = pcall(TRACED_FOLLOW, cp, path_name, label)\n"
                  "    return ok, err\n"
                  "end\n")
    return lua


def test_traced_follow_fr_lg_calls_play_follow_once_and_skips_rr_recovery():
    """Finding: the title guard is traced_follow's very first line -- FR/LG must never touch
    play.step/play.handle_encounter/play.clear_dialogue, only play.follow, exactly once."""
    lua = _traced_follow_env("firered")
    w = lua.globals().W
    ok, err = w.run(None, "test_path", "lbl")
    assert ok is True, err
    assert w.follow_calls == 1
    assert w.step_calls == 0 and w.battle_calls == 0 and w.dialogue_calls == 0


def test_traced_follow_rr_recovers_when_the_lock_clears():
    """A lock (message box / battle intro) that clears on the first recovery round: one
    clear_dialogue, then the retried step lands -- the walk finishes without error."""
    lua = _traced_follow_env("radical_red")
    w = lua.globals().W
    w.quiet, w.on_field = False, False
    lua.execute("W.step_queue = { false, true }")
    lua.execute("W.round_effects = { function() W.quiet = true; W.on_field = true end }")
    ok, err = w.run(None, "test_path", "lbl")
    assert ok is True, err
    assert w.step_calls == 2
    assert w.dialogue_calls == 1
    assert w.battle_calls == 0


def test_traced_follow_rr_a_persistent_lock_fails_naming_locked_and_the_round_count():
    """Finding 6: the stalled failure must say the field stayed locked and how many recovery
    rounds ran, not just "stalled at (x,y)" -- a lock that never clears runs all 4 rounds."""
    lua = _traced_follow_env("radical_red")
    w = lua.globals().W
    w.quiet, w.on_field = False, False
    lua.execute("W.step_queue = { false }")          # play.step never succeeds
    ok, err = w.run(None, "test_path", "lbl")
    assert ok is False
    assert "locked" in err and "4" in err, err


def test_traced_follow_rr_a_warp_during_recovery_fails_not_succeeds():
    """Finding 1: the old code accepted ANY map change during recovery as success. A warp
    caused by the recovery mash itself (a wrong dialogue choice, not the lock clearing) must
    fail loud, and must never let the retried play.step wave it through as a legitimate warp."""
    lua = _traced_follow_env("radical_red")
    w = lua.globals().W
    w.quiet, w.on_field = False, False
    lua.execute("W.step_queue = { false }")
    lua.execute("W.round_effects = { function() W.map = 999 end }")
    ok, err = w.run(None, "test_path", "lbl")
    assert ok is False
    assert "warp" in err.lower() and "999" in err, err
    assert w.step_calls == 1                          # never retried the step past the warp


def test_traced_follow_rr_a_battle_during_recovery_uses_the_shared_encounter_budget():
    """Finding 2: a battle hit during recovery must route through the same P.handle_encounter
    accounting play.follow uses (the enc.n budget it owns), not a bare play.fight_through that
    skips it."""
    lua = _traced_follow_env("radical_red")
    w = lua.globals().W
    w.in_battle, w.quiet, w.on_field = True, False, False
    lua.execute("W.step_queue = { false, true }")
    lua.execute("W.round_effects = { function()"
                " W.in_battle = false; W.quiet = true; W.on_field = true end }")
    ok, err = w.run(None, "test_path", "lbl")
    assert ok is True, err
    assert w.battle_calls == 1
    assert list(w.enc_seen.values()) == [1]
    assert w.step_calls == 2


def test_traced_follow_rr_battles_false_refuses_to_auto_fight():
    """Finding 3: play.follow refuses to absorb an encounter when p.battles == false; traced_follow
    must refuse the same way instead of fighting it through RR recovery regardless."""
    lua = _traced_follow_env("radical_red")
    w = lua.globals().W
    w.in_battle = True
    lua.execute("PATHS.test_path.battles = false")
    lua.execute("W.step_queue = { false }")
    ok, err = w.run(None, "test_path", "lbl")
    assert ok is False
    assert "battles=false" in err or "refuses battles" in err, err
    assert w.battle_calls == 0                         # never routed through handle_encounter


def test_traced_follow_rr_trace_is_silent_without_the_env_flag():
    """RR_TRACE off (the SLINK_GEN3_RR_TRACE-gated default) must never touch console.log, even
    across a full recovery round -- one console table shared by both rr_trace_log call sites and
    the per-round trace line."""
    lua = _traced_follow_env("radical_red", rr_trace=False)
    w = lua.globals().W
    w.quiet, w.on_field = False, False
    lua.execute("W.step_queue = { false, true }")
    lua.execute("W.round_effects = { function() W.quiet = true; W.on_field = true end }")
    ok, err = w.run(None, "test_path", "lbl")
    assert ok is True, err
    assert list(w.log.values()) == []



# ── E4: the gen3_emerald row (E<->E) ─────────────────────────────────────────────────────
def test_emerald_admission_is_production_only():
    """EG4: Emerald is admitted by production code alone -- the duo driver's pre-EG4 TEST-ONLY
    seam (test_admission_codec, which faked titles.emerald.admitted for the duo row only) is
    gone now that it would be a no-op."""
    profile = json.loads((REPO / "data/games/gen3_emerald/profile.json").read_text(encoding="utf-8"))
    assert profile["titles"]["emerald"]["admitted"] is True
    entry = (REPO / "lua/gen3/entry.lua").read_text(encoding="utf-8")
    from lupa import LuaRuntime
    routes = dict(LuaRuntime().execute(entry).ROUTED.items())
    assert set(routes) == {"gen3_frlg", "gen3_rr", "gen3_emerald", "gen3_exp"}
    assert all(value is True for value in routes.values())
    assert "test_admission_codec" not in entry
    assert "test_admission_codec" not in DRIVER.read_text(encoding="utf-8")


def test_ball_hunt_scenarios_resolve_emerald_fixture_with_enough_balls():
    """RC risk: deadzone_gen3 on Emerald burned all three RNG retries because B ran out of the
    plain "battle" fixture's 5 Poke Balls twice. Every ball_hunt scenario that lists gen3_emerald
    must hunt on a fixture carrying >= 20 balls (the "catch" kind), and GAMES["gen3_emerald"]
    ["hunt_area"] must name that target."""
    row = duo.GAMES["gen3_emerald"]
    ball_hunts = [(name, entry) for name, entry in duo.SCENARIOS.items()
                  if entry.get("ball_hunt") and "gen3_emerald" in entry.get("games", ())]
    assert ball_hunts, "no ball_hunt scenario lists gen3_emerald -- test is vacuous"
    for name, entry in ball_hunts:
        target = duo.scenario_target(entry, "gen3_emerald")
        if name == "ball_gate_gen3":
            # This row must START with zero balls: acquiring the first native
            # reward is itself under test. Its full faint/reward/catch oracle
            # checks the actual debit; every ordinary hunt keeps the >=20 rule.
            assert target == "ball_gate"
            assert entry["hunt_area_by_game"]["gen3_emerald"] == "rusturf_tunnel"
            assert entry["oracle"] == "assert_ball_gate_gen3_saved"
            continue
        assert target in row["hunt_area"], f"{name}: hunt_area names no {target!r} target"
        body = (REPO / f"tests/fixtures/gen3/emerald_{target}.sav").read_bytes()
        balls = sum(qty for item, qty in gen3_fixtures.emerald_ball_pocket(body)
                    if item == gen3_fixtures.ITEM_POKE_BALL)
        assert balls >= 20, f"{name}: emerald_{target}.sav only carries {balls} balls"


def test_emerald_row_resolves_pack_fixtures_and_layout():
    row = duo.GAMES["gen3_emerald"]
    assert row["game"] == "gen3_emerald" and duo.scenario_family("gen3_emerald") == "gen3_emerald"
    assert "gen3_emerald" in duo.OPT_IN_GAMES and duo.rng_retry_family("gen3_emerald")
    assert duo.gen3_profile_path("emerald").endswith(os.path.join("gen3_emerald", "profile.json"))
    assert duo.gen3_profile_path("firered").endswith(os.path.join("gen3_frlg", "profile.json"))
    assert duo.gen3_profile_path("radical_red") == duo.GEN3_RR_PROFILE
    assert duo.gen3_codec_title("radical_red") == "frlg" and duo.gen3_codec_title("emerald") == "emerald"
    for name in ("faint_cmd_gen3", "reconnect_gen3", "deadzone_gen3", "link_gen3", "boxsync_gen3",
                 "linked_faint_active_gen3", "whiteout_gen3"):
        assert duo.scenario_applies(name, "gen3_emerald"), name
        run = duo.DuoRun.__new__(duo.DuoRun)
        run.gcfg, run.cfg, run.game = dict(row), dict(duo.SCENARIOS[name]), "gen3_emerald"
        assert run._hunt_area == \
            {"battle": "route_102", "pc": "route_103", "catch": "route_102"}[run._target_for("a")], name
        for inst in ("a", "b"):
            assert run._gen3_title(inst) == "emerald"
            assert os.path.isfile(run._gen3_fixture_path(inst)), run._gen3_fixture_path(inst)
    frlg = duo.DuoRun.__new__(duo.DuoRun)
    frlg.gcfg = dict(duo.GAMES["gen3_frlg"])
    assert frlg._hunt_area == "route_1"
    # the Emerald layout, not FR's: emerald_pc.sav's two-mon party and two boxed mons
    image = (REPO / "tests/fixtures/gen3/emerald_pc.sav").read_bytes()
    party, boxes = duo.gen3_decode(image, title="emerald")
    assert [m["species"] for m in party] == [283, 286] and len(boxes) == 2
    assert duo.gen3_ball_count(image, "emerald") == 5
    # the raw party record comes from Emerald's own SaveBlock1 offset, not FR's
    pc = duo.DuoRun.__new__(duo.DuoRun)
    pc.gcfg, pc.cfg, pc.game = dict(row), dict(duo.SCENARIOS["faint_cmd_gen3"]), "gen3_emerald"
    assert codec.decode_party_mon(bytes.fromhex(pc._gen3_party_record_hex("a", 1))) == party[1]


def test_hunt_area_fails_loud_off_kanto():
    run = duo.DuoRun.__new__(duo.DuoRun)
    run.gcfg = {"game": "gen3_emerald"}                   # an Emerald-family row without the key
    with pytest.raises(RuntimeError, match="names no hunt_area"):
        _ = run._hunt_area
    for game in ("gen3_frlg", "gen3_lgfr", "gen3_rr", "gen1_new"):
        run.gcfg = dict(duo.GAMES[game])
        assert run._hunt_area == "route_1", game


# ── E4-DUO-2: whiteout_destination on Emerald (raw lastHealLocation) vs FR (projection) ───
def _scripted_machine(title):
    from lupa import LuaRuntime
    from test_gen3_fr_story_oracles import HARNESS

    lua = LuaRuntime(unpack_returned_tuples=True)
    lua.globals().SLINK_ROOT = REPO.as_posix()
    lua.globals().SLINK_GEN3_TITLE = title
    lua.execute(HARNESS)
    return lua, lua.execute(f'return dofile("{SCRIPTED.as_posix()}")')


def test_sb1_pointer_bound_follows_the_title_saveblock1_size():
    """sb1_ptr's upper bound is 0x02040000 - sizeof(SaveBlock1): FR/LG 0x3D68, Emerald 0x3D88
    (gen3_codec SAVEBLOCK1_SIZE_EMERALD). 0x0203C290 fits FR's block but not Emerald's."""
    from lupa import LuaError

    assert codec.SAVEBLOCK1_SIZE_EMERALD == 0x3D88
    at = 0x0203C290
    assert 0x02040000 - 0x3D88 < at <= 0x02040000 - 0x3D68
    lua, mod = _scripted_machine("emerald")
    lua.globals().F.w32(0x03005008, at)
    with pytest.raises(LuaError, match="whiteout_heal_pointer"):
        mod.whiteout_destination(lua.globals().F.cp)
    lua, mod = _scripted_machine("firered")
    lua.globals().F.w32(0x03005008, at)
    with pytest.raises(LuaError, match="whiteout_heal_unsupported"):   # the pointer passed
        mod.whiteout_destination(lua.globals().F.cp)


def test_emerald_whiteout_lands_on_the_raw_heal_tile_and_fr_still_projects():
    from lupa import LuaError

    lua, mod = _scripted_machine("emerald")
    oldale = lua.eval("{group=0, num=10, warp=255, x=6, y=17}")
    dest = mod.whiteout_destination(lua.globals().F.cp, oldale)
    assert (dest.group, dest.num, dest.x, dest.y) == (0, 10, 6, 17)
    lua, mod = _scripted_machine("firered")
    cp = lua.globals().F.cp
    dest = mod.whiteout_destination(cp, lua.eval("{group=3, num=1, warp=255, x=26, y=27}"))
    assert (dest.group, dest.num, dest.x, dest.y) == (5, 4, 7, 4)     # Viridian Center, unchanged
    with pytest.raises(LuaError, match="whiteout_heal_unsupported"):
        mod.whiteout_destination(cp, lua.eval("{group=0, num=10, warp=255, x=6, y=17}"))  # FR: no raw tile


# ── X3-WHITEOUT: the expansion's damaging fallback (EXP-WHITEOUT-HARNESS) ─────────────────────
# Live gen3_exp whiteout_gen3: "battler 0 has no no-damage move with PP (turn 43, hp 2, hunts 3)" --
# Growl's PP spent, and the expansion BM has no effect_off, so any_move_slot always returned nil.
# The expansion reads MoveInfo.effect (facts.json bitfield) and takes only a plain EFFECT_HIT with
# power. Ids are the pinned source's (moves.h): Tackle 33, Double-Edge 38, Growl 45, Self-Destruct
# 120, Explosion 153, Struggle 165; effects (battle_move_effects.h): HIT 1, STAT_CHANGE 2,
# STRUGGLE 227, RECOIL 271.
_EXP_FACTS = json.loads((REPO / "data/games/gen3_exp/28877d73/facts.json").read_text(encoding="utf-8"))["structs"]["MoveInfo"]["bitfields"]


def _exp_lead(lua, moves, pp):
    """The model, switched to the expansion title: 64-byte MoveInfo table, power/effect bitfields."""
    lua.execute("POWER[45], POWER[38], POWER[120], POWER[153], POWER[165] = 0, 120, 200, 250, 50")
    lua.execute("EXP_EFFECT[33], EXP_EFFECT[45], EXP_EFFECT[38], EXP_EFFECT[120], EXP_EFFECT[153],"
                " EXP_EFFECT[165] = 1, 2, 271, 1, 1, 227")
    lua.execute("profile.rom.BATTLE_MOVES_ADDR, profile.derived.BATTLE_MOVE_ENTRY_SIZE = EXP_MOVES, 64")
    p, e = _EXP_FACTS["power"], _EXP_FACTS["effect"]
    # the same facts.json fields the driver reads (offset/width/shift/bits), mask = (1 << bits) - 1
    lua.execute("USE_EXP_BM({%s}, {%s})" % tuple(
        ", ".join(str(f[k]) for k in ("offset", "width", "shift", "bits")) for f in (p, e)))
    lua.execute(f"M.moves = {{ {', '.join(map(str, moves))} }}; M.pp = {{ {', '.join(map(str, pp))} }}")
    lua.execute("M.foe_hp = 999")                       # the fallback alone never wins here
    lua.globals().start(40)


def test_exp_lose_active_falls_back_to_a_plain_hit_once_growl_pp_is_spent(battle_model):
    """Growl (45) has 1 PP; slot 1 is Double-Edge (EFFECT_RECOIL 271, power 120, PP left) and slot 2
    Tackle (33): turn 1 Growl, then the fallback must pick Tackle (slot 2) until the lead faints --
    not the "no no-damage move with PP" refusal, and not the recoil move the CFRU-set guard would
    let through (reverting any_move_slot's BM.effect branch picks slot 1; reverting move_effect's
    BM.effect branch finds no readable effect and refuses)."""
    lua = battle_model
    _exp_lead(lua, [45, 38, 33, 0], [1, 5, 35, 0])
    ok, why = lua.globals().LOSE()
    m, logs = lua.globals().M, list(lua.globals().LOGS.values())
    assert ok is True, (why, logs)
    assert list(m.used.values())[:2] == [45, 33], (m.used, logs)
    assert 38 not in set(m.used.values()), (m.used, logs)
    assert m.lead_hp == 0 and any(line.startswith("LOSE_FALLBACK K0 turn=2 slot=2") for line in logs), logs


@pytest.mark.parametrize("move, label", [(38, "Double-Edge (EFFECT_RECOIL)"), (120, "Self-Destruct"),
                                         (153, "Explosion"), (165, "Struggle (EFFECT_STRUGGLE)")])
def test_exp_lose_active_never_picks_a_self_damaging_fallback(battle_model, move, label):
    """The only move with PP left after Growl is one that can KO or hurt the lead itself: refuse
    it by name. Explosion/Self-Destruct are EFFECT_HIT in the expansion (moves_info.h:3268,4182),
    so the effect id alone is not enough -- they are excluded by id."""
    lua = battle_model
    _exp_lead(lua, [45, move, 0, 0], [1, 5, 0, 0])
    ok, why = lua.globals().LOSE()
    assert ok is False and "no no-damage move with PP" in why, (label, why)
    assert move not in set(lua.globals().M.used.values()), label


def test_non_expansion_bm_still_refuses_an_unreadable_fallback(battle_model):
    """The default (CFRU/pret) BM is untouched: same model without USE_EXP_BM, an effect-0 Tackle
    with PP left is picked via the original effect_off path (the recoil refusal stays covered by
    test_lose_active_refuses_a_self_damaging_fallback)."""
    lua = battle_model
    lua.execute("M.moves = { 39, 33, 0, 0 }; M.pp = { 1, 35, 0, 0 }; M.foe_hp = 999")
    lua.globals().start(40)
    ok, why = lua.globals().LOSE()
    assert ok is True and list(lua.globals().M.used.values())[:2] == [39, 33], why


# ── frlgc 2026-10-03: reconnect_gen3's same-save window with a companion cartridge ─────────────────────
# Evidence (F:/slink-work/tmp/duo_2jsg2y18, the Emerald same_save result file): one client session sends THREE `TX hello` lines in the
# first ~550 frames -- the connect hello, then capability refreshes after `resolved_areas/config/dead_keys` and the native link-panel
# write -- all with the same artifact_kind/foundation/companion_abi/badges/ball_count and the same party; only area_id may differ (the
# map settles after CONTINUE). The phase driver logs ONE `RECONNECT_HELLO same_save count=N` line (N = hellos sent so far).
def _tx_hello(area="oldale_town", kind="companion", abi=2, badges=0, balls=5, foundation="gen3_emerald"):
    return ('TX hello - {"area_id":"%s","artifact_kind":"%s","badges":%d,"ball_count":%d,"battle_identity":true,'
            '"companion_abi":%d,"event":"hello","foundation":"%s","has_pokeballs":true,"in_battle":fal\n'
            % (area, kind, badges, balls, abi, foundation))


def _same_save_window(count=3, hellos=3, **kw):
    return ("booted\n" + "".join(_tx_hello(**kw) for _ in range(hellos))
            + f"RECONNECT_HELLO same_save count={count}\n")


@pytest.mark.parametrize("count, hellos", [(1, 1), (2, 2), (3, 3), (3, 1)])
def test_same_save_hello_wait_accepts_the_companions_extra_hellos(count, hellos):
    window = _same_save_window(count, hellos)
    assert duo.same_save_hello_seen(window) is True and duo.same_save_hello_problems(window) == []


def test_same_save_hello_wait_is_not_satisfied_by_nothing_or_count_zero():
    for text in ("", "booted\n", "RECONNECT_HELLO same_save count=0\n"):
        assert duo.same_save_hello_seen(text) is False and duo.same_save_hello_problems(text) == []


def test_a_refresh_may_change_the_area_but_not_the_identity_fields():
    areas = ("booted\n" + _tx_hello(area="viridian_city") + _tx_hello(area="viridian_city_mart")
             + "RECONNECT_HELLO same_save count=2\n")
    assert duo.same_save_hello_problems(areas) == []
    for field, bad in (("kind", "clean"), ("abi", 1), ("badges", 3), ("balls", 4), ("foundation", "gen3_frlg")):
        window = "booted\n" + _tx_hello() + _tx_hello(**{field: bad}) + "RECONNECT_HELLO same_save count=2\n"
        problems = duo.same_save_hello_problems(window)
        assert problems and "hello #2 differs from hello #1" in problems[0], (field, problems)
        with pytest.raises(RuntimeError, match="not one save"):
            duo.same_save_hello_seen(window)


def test_a_mixed_same_and_different_save_window_fails_and_never_waits_out_the_timeout():
    """RED case: the window holds a same_save hello AND a hello for another save (or a line nobody can parse)."""
    mixed = _same_save_window(2, 2) + "RECONNECT_HELLO wrong_save count=1\n"
    assert any("another save" in p and "wrong_save" in p for p in duo.same_save_hello_problems(mixed))
    with pytest.raises(RuntimeError, match="wrong_save"):
        duo.same_save_hello_seen(mixed)
    only_other = "RECONNECT_HELLO wrong_save count=1\n"                   # a wrong_save-only window is no same-save hello
    with pytest.raises(RuntimeError, match="another save"):
        duo.same_save_hello_seen(only_other)
    for odd in ("RECONNECT_HELLO other_save count=1\n", "RECONNECT_HELLO same_save\n", "RECONNECT_HELLO same_save count=x\n"):
        problems = duo.same_save_hello_problems(_same_save_window(1, 1) + odd)
        assert problems, odd
        with pytest.raises(RuntimeError):
            duo.same_save_hello_seen(_same_save_window(1, 1) + odd)


def test_the_orchestration_waits_with_the_checked_predicate_and_re_checks_at_the_oracle():
    import inspect

    src = inspect.getsource(duo.DuoRun.orchestrate_reconnect_gen3)
    assert "same_save_hello_seen(text(same_path))" in src            # the wait predicate (raises on a violation)
    assert "same_save_hello_problems(text(same_path))" in src        # and the final problems list
    assert "accepted_reconnect_hellos(baseline" in src and "RECONNECT_HELLO same_save count=1" not in src


def test_the_driver_logs_per_hello_what_the_tx_line_truncates():
    """HELLO_FACTS (duo_gen3_main.lua hello_facts): the party a hello carried and whether it was withheld -- the two facts that decide
    whether a '0 mons' hello is safe (server/state.py:1958-1959 vs :2087)."""
    from lupa import LuaRuntime

    runtime = LuaRuntime(unpack_returned_tuples=True)
    source = DRIVER.read_text(encoding="utf-8")
    found = re.search(_LUA_DEF.format("hello_facts"), source, re.M | re.S)
    assert found, "duo_gen3_main.lua has no hello_facts"
    facts = runtime.execute(found.group(0) + "\nreturn hello_facts")
    assert facts(runtime.eval("{party = {}, party_hidden = true}")) == "HELLO_FACTS party=0 party_hidden=true"
    assert facts(runtime.eval("{party = {1, 2}}")) == "HELLO_FACTS party=2 party_hidden=false"
    assert facts(runtime.eval("{party = {}}")) == "HELLO_FACTS party=0 party_hidden=false"
    assert facts(runtime.eval("{}")) == "HELLO_FACTS party=absent party_hidden=false"
    assert facts(runtime.eval("{party_hidden = false}")) == "HELLO_FACTS party=absent party_hidden=false"
    assert facts("not a table") == "HELLO_FACTS party=absent party_hidden=false"
    assert 'if name == "hello" then log(hello_facts(msg)) end' in source          # and every sent hello is logged with it


# ── probe_protected_span_flip_gen3: a live OBSERVATION row, in no plan ───────────────────────────────────────────────
PROBE = "probe_protected_span_flip_gen3"
_PROBE_SLOT = b"\xcd\xe3\xe9\xe0\xc6\xdd\xe2\xdf\x00\xd8\xd9\xea\xff" + b"\x00" * 7        # "dev" text, 0xFF terminator, zero padding


def _probe_rom_and_row(slot=_PROBE_SLOT):
    rom = bytearray(b"\x55" * 0x400)
    rom[0x100 + 0x20:0x100 + 0x20 + len(slot)] = slot
    row = {"protected_spans": [{"offset": 0x100, "size": 0x100}, {"offset": 0x300, "size": 8}],
           "payload_version_slot": {"offset": 0x20, "length": len(slot)}}
    return bytes(rom), row


def test_the_probe_flips_the_last_padding_byte_of_the_version_slot_by_a_fixed_rule():
    rom, row = _probe_rom_and_row()
    choice = duo.gen3_probe_flip_choice(rom, row, [(0x10, 8)])
    assert choice == {"offset": 0x100 + 0x20 + 19, "before": 0, "after": 1, "span": 0, "rule": "version-slot padding byte",
                      "span_offset": 0x100, "span_size": 0x100}
    assert duo.gen3_probe_flip_choice(rom, row, [(0x10, 8)]) == choice                  # deterministic
    flipped = rom[:choice["offset"]] + bytes([choice["after"]]) + rom[choice["offset"] + 1:]
    assert [i for i in range(len(rom)) if rom[i] != flipped[i]] == [choice["offset"]]   # exactly one byte differs


def test_the_probe_falls_back_when_the_slot_is_not_a_terminated_padded_string():
    rom, row = _probe_rom_and_row(slot=b"\xcd" * 20)                                    # no 0xFF terminator, no padding
    choice = duo.gen3_probe_flip_choice(rom, row, [])
    assert choice["rule"] == "first non-slot payload byte" and choice["offset"] == 0x100
    assert choice["offset"] not in range(0x100 + 0x20, 0x100 + 0x20 + 20)


def test_the_probe_byte_must_be_in_a_protected_span_and_off_every_anchor():
    rom, row = _probe_rom_and_row()
    with pytest.raises(ValueError, match="on an anchor"):
        duo.gen3_probe_flip_choice(rom, row, [(0x100 + 0x20 + 18, 4)])                  # an anchor covering the chosen byte
    duo.gen3_probe_flip_choice(rom, row, [(0x100 + 0x20 + 19, 0)])                      # a zero-length anchor covers nothing
    outside = {**row, "protected_spans": [{"offset": 0x100, "size": 0x10}]}                # the slot sits past the span
    with pytest.raises(ValueError, match="not inside a protected span"):
        duo.gen3_probe_flip_choice(rom, outside, [])


@pytest.mark.parametrize("title", ["firered", "leafgreen", "emerald"])
def test_the_probe_byte_of_the_real_builds_is_padding_inside_a_span_off_the_anchors(title):
    """Needs the real, uncommitted clean dump: a named skip when it is absent (tests/TESTING.md)."""
    from tools.gen3_companions import published
    from tools.gen3_final_cut import ROOT_DUMPS, main_checkout

    root = os.environ.get("SLINK_GEN3_ROMS") or main_checkout()
    dump = os.path.join(root, ROOT_DUMPS[title])
    if not os.path.isfile(dump):
        pytest.skip(f"clean {title} dump absent: {dump}")
    patched, row = published(title, open(dump, "rb").read())
    pack = {"firered": "gen3_frlg", "leafgreen": "gen3_frlg", "emerald": "gen3_emerald"}[title]
    anchors = json.loads((REPO / "data" / "games" / pack / "write_checkpoint.json").read_text(encoding="utf-8"))[title]["anchors"]
    spans = [(a["rom_offset"], len(a["expected_hex"]["companion"]) // 2) for a in anchors.values()]
    choice = duo.gen3_probe_flip_choice(patched, row, spans)
    assert choice["rule"] == "version-slot padding byte" and (choice["before"], choice["after"]) == (0, 1)
    assert any(s["offset"] <= choice["offset"] < s["offset"] + s["size"] for s in row["protected_spans"])
    assert not any(a <= choice["offset"] < a + n for a, n in spans)
    slot = row["payload_version_slot"]
    base = row["protected_spans"][0]["offset"] + slot["offset"]
    from patch.tools.gen3_title import menu_field
    field = menu_field(row["menu_version"])                                                # the stamped string, terminator, zero padding
    assert bytes(patched[base:base + slot["length"]]) == field and field[-1] == 0
    assert choice["offset"] == base + slot["length"] - 1                                    # behind the string's terminator
    assert duo.gen3_probe_flip_choice(patched, row, spans) == choice


def test_the_probe_is_opt_in_and_in_no_plan(monkeypatch):
    from tools import gen3_final_cut as fc

    row = duo.SCENARIOS[PROBE]
    assert row["explicit_only"] is True and row["gen3_probe_flip"] is True and row["no_save"] == ("a", "b")
    assert set(row["games"]) == {"gen3_frlg", "gen3_lgfr", "gen3_emerald"}
    for game in ("gen3_frlg", "gen3_lgfr", "gen3_emerald"):
        assert PROBE not in duo.scenarios_for(game) and duo.scenario_applies(PROBE, game)
    # applicable to exactly the three per-title games and to NO other pairing row
    assert sorted(g for g in duo.GAMES if duo.scenario_applies(PROBE, g)) == ["gen3_emerald", "gen3_frlg", "gen3_lgfr"]
    cut = "c" * 40
    if not os.environ.get("SLINK_GEN3_RAND_ROMS"):
        monkeypatch.setenv("SLINK_GEN3_RAND_ROMS", "R:/rand")   # restored after the test; a bare setdefault leaked into later tests
    for plan in (fc.build_plan, fc.build_plan_rr, fc.build_plan_emerald, fc.build_plan_exp, fc.build_plan_frlgc, fc.build_plan_frlgc_rand):
        assert not any("probe_protected_span_flip" in " ".join(r.argv) or "probe_flip" in r.id for r in plan(cut, "L:/lane", "L:/m")), plan
    assert len(fc.build_plan_frlgc(cut, "L:/lane", "L:/m")) == 65 and len(fc.build_plan_frlgc_rand(cut, "L:/lane", "L:/m")) == 13
    assert callable(duo.DuoRun.orchestrate_probe_protected_span_flip_gen3) and callable(duo.DuoRun.assert_probe_protected_span_flip_gen3_saved)


def _probe_run(receipt_b, status_b=None):
    run = object.__new__(duo.DuoRun)
    run._probe_flip = {"title": "firered", "offset": 0xEB2E27, "before": 0, "after": 1, "span": 0, "span_offset": 0xEB2000,
                       "span_size": 0x4000, "anchors_checked": 11}
    notes, went = [], []
    run._pydec_note = notes.append
    run._read_receipt = lambda side: receipt_b
    run._status = lambda: {"players": {"b": status_b or {}}}
    run.wait_for = lambda desc, pred, timeout: pred() or (_ for _ in ()).throw(TimeoutError(desc))
    run.go = lambda *a, **k: went.append(True)
    return run, notes, went


def test_the_probe_records_a_launch_refusal_without_judging_it():
    run, notes, went = _probe_run("PROBE_ADMISSION client=refused_at_launch reason=[SLink-gen3] refused: this firered cartridge ...\n")
    run.orchestrate_probe_protected_span_flip_gen3()
    assert run._probe_observed["client"] == "refused_at_launch" and went == [True]
    assert any("PROBE_FLIP_OBSERVED" in n and "client=refused_at_launch" in n for n in notes)
    run.assert_probe_protected_span_flip_gen3_saved({"a": "", "b": "PROBE_ADMISSION client=refused_at_launch reason=x\n"})


@pytest.mark.parametrize("player, expect", [({"admission": "admitted", "admission_reason": "ok"}, "server=admitted"),
                                            ({"admission": "rejected", "admission_reason": "companion_abi differs"}, "server=rejected"),
                                            ({"identity_error": "Identity mismatch"}, "server=identity_error")])
def test_the_probe_records_an_admitted_client_and_the_servers_verdict_either_way(player, expect):
    run, notes, went = _probe_run("TX hello - {}\nHELLO_FACTS party=2 party_hidden=false\nPROBE_CLIENT admitted kind=companion\n", player)
    run.orchestrate_probe_protected_span_flip_gen3()
    assert went == [True] and any(expect in n and "client=admitted" in n for n in notes)
    run.assert_probe_protected_span_flip_gen3_saved(
        {"a": "", "b": "HELLO_FACTS party=2 party_hidden=false\nPROBE_CLIENT admitted kind=companion\n"})


def test_the_probe_oracle_names_what_was_observed_never_a_bare_pass():
    run, notes, _ = _probe_run("PROBE_ADMISSION client=refused_at_launch reason=x\n")
    run.orchestrate_probe_protected_span_flip_gen3()
    notes.clear()
    run.assert_probe_protected_span_flip_gen3_saved({"a": "", "b": "PROBE_ADMISSION client=refused_at_launch reason=x\n"})
    assert len(notes) == 1 and notes[0].startswith("OBSERVED refused_at_launch (an observation, not a verdict)")
    run, notes, _ = _probe_run("HELLO_FACTS party=2 party_hidden=false\nPROBE_CLIENT admitted kind=companion\n", {"admission": "admitted"})
    run.orchestrate_probe_protected_span_flip_gen3()
    notes.clear()
    run.assert_probe_protected_span_flip_gen3_saved(
        {"a": "", "b": "HELLO_FACTS party=2 party_hidden=false\nPROBE_CLIENT admitted kind=companion\n"})
    assert len(notes) == 1 and notes[0].startswith("OBSERVED admitted (an observation, not a verdict)")


def test_the_probe_fails_only_when_the_harness_itself_broke():
    run, _notes, went = _probe_run("")                                       # B did nothing at all: no observation possible
    with pytest.raises(TimeoutError, match="launch outcome"):
        run.orchestrate_probe_protected_span_flip_gen3()
    assert went == []
    with pytest.raises(RuntimeError, match="no admission observation"):
        run.assert_probe_protected_span_flip_gen3_saved({"a": "", "b": ""})
    run._probe_observed = {"client": "admitted", "reason": "", "server": "admitted", "server_reason": ""}
    with pytest.raises(RuntimeError, match="not in its own receipt"):          # the claim must be in the side's own receipt
        run.assert_probe_protected_span_flip_gen3_saved({"a": "", "b": "HELLO_FACTS party=2 party_hidden=false\n"})


def test_the_driver_lets_a_probe_record_a_refusal_but_nothing_else_changes():
    from lupa import LuaRuntime

    source = DRIVER.read_text(encoding="utf-8")
    found = re.search(_LUA_DEF.format("launch_verdict"), source, re.M | re.S)
    verdict = LuaRuntime(unpack_returned_tuples=True).execute(found.group(0) + "\nreturn launch_verdict")
    refused = "[SLink-gen3] refused: this firered cartridge needs the SLink companion patch; prepare it through the Manager or /patcher"
    done, ok, msg = verdict(None, refused, False, True)
    assert (done, ok) == (True, True) and msg.startswith("OBSERVED refused: ") and "probe observed: refused at launch" in msg
    assert verdict(None, "x", False, False)[:2] == (True, False)                    # an ordinary row: still a failure
    assert verdict(None, "x", False)[:2] == (True, False)                           # the old three-argument call
    assert verdict({}, None, False, True) is False                                  # a built client carries on, probe or not
    assert verdict({}, None, True, True)[:2] == (True, False)                       # expect_refused still wins over a probe
    assert 'PROBE_ADMISSION client=refused_at_launch reason=' in source and "D.probe_admission" in source


def test_the_probe_scenario_module_records_what_the_client_announced_and_writes_nothing():
    from lupa import LuaRuntime

    runtime = LuaRuntime(unpack_returned_tuples=True)
    runtime.execute("""
        logs = {}
        ctx = { last_sent = function() return { artifact_kind = "companion", companion_abi = 2, rom_sha1 = "ab" } end,
                log = function(s) logs[#logs + 1] = s end, wait_go = function() return true end, frames = function() end,
                writes = function() return 0 end }
        module = dofile(WT .. "/lua/tests/duo/scenario_gen3_probe_flip.lua")
    """.replace("WT", repr(str(REPO).replace(chr(92), "/"))))
    ok, msg = runtime.eval("module(ctx)")
    assert ok is True and msg.startswith("OBSERVED admitted: ")
    logs = list(runtime.eval("logs").values())
    assert logs[0] == "PROBE_CLIENT admitted artifact_kind=companion companion_abi=2 rom_sha1=ab" and logs[-1] == "PROBE_PASSIVE writes=0"


# ── link_gen3_rand: attempt budget 6, and nothing else's budget moved ───────────────────────────────────────────────────
# Every scenario/game pair whose scenario_attempt_limit was not 1 at d5a26da9 (snapshot of 1417 pairs; link_gen3_rand excluded -- it is
# the one row that changed). Pairs not listed were 1.
ATTEMPT_LIMITS_AT_D5A26DA9 = {
    "active_end_gen3": {"gen1_new": 3, "gen1_pure": 3, "gen1_pure_green": 3},
    "admit_randomized_emerald": {"gen1_new": 3, "gen1_pure": 3, "gen1_pure_green": 3},
    "admit_randomized_frlg": {"gen1_new": 3, "gen1_pure": 3, "gen1_pure_green": 3},
    "admit_randomized_new": {"gen1_new": 3, "gen1_pure": 3, "gen1_pure_green": 3},
    "ball_gate_gen3": {"gen1_new": 8, "gen1_pure": 8, "gen1_pure_green": 8, "gen3_emerald": 8, "gen3_exp": 8, "gen3_frlg": 8, "gen3_lgfr": 8, "gen3_rr": 8},
    "borrowed_party_battle_gen3": {"gen1_new": 3, "gen1_pure": 3, "gen1_pure_green": 3},
    "borrowed_party_menu_gen3": {"gen1_new": 3, "gen1_pure": 3, "gen1_pure_green": 3},
    "borrowed_party_opponent_gen3": {"gen1_new": 3, "gen1_pure": 3, "gen1_pure_green": 3},
    "boxsync": {"gen1_new": 3, "gen1_pure": 3, "gen1_pure_green": 3},
    "boxsync_gen3": {"gen1_new": 3, "gen1_pure": 3, "gen1_pure_green": 3},
    "center_controls_gen3": {"gen1_new": 3, "gen1_pure": 3, "gen1_pure_green": 3},
    "changebox_new": {"gen1_new": 3, "gen1_pure": 3, "gen1_pure_green": 3},
    "choice_gift_gen3": {"gen1_new": 3, "gen1_pure": 3, "gen1_pure_green": 3},
    "clean_rr_refused_gen3": {"gen1_new": 3, "gen1_pure": 3, "gen1_pure_green": 3},
    "deadzone_gen3": {"gen1_new": 3, "gen1_pure": 3, "gen1_pure_green": 3, "gen3_emerald": 3, "gen3_exp": 3, "gen3_frlg": 3, "gen3_lgfr": 3, "gen3_rr": 3},
    "deadzone_new": {"gen1_new": 3, "gen1_pure": 3, "gen1_pure_green": 3},
    "egg_hatch_gen3": {"gen1_new": 3, "gen1_pure": 3, "gen1_pure_green": 3},
    "egg_receive_gen3": {"gen1_new": 3, "gen1_pure": 3, "gen1_pure_green": 3},
    "evolve_gen3": {"gen1_new": 3, "gen1_pure": 3, "gen1_pure_green": 3},
    "exp_static_altering0_gen3": {"gen1_new": 3, "gen1_pure": 3, "gen1_pure_green": 3},
    "exp_static_altering1_gen3": {"gen1_new": 3, "gen1_pure": 3, "gen1_pure_green": 3},
    "exp_static_fish_gen3": {"gen1_new": 3, "gen1_pure": 3, "gen1_pure_green": 3},
    "exp_static_grass_gen3": {"gen1_new": 3, "gen1_pure": 3, "gen1_pure_green": 3},
    "exp_static_rock_gen3": {"gen1_new": 3, "gen1_pure": 3, "gen1_pure_green": 3},
    "exp_static_static_gen3": {"gen1_new": 3, "gen1_pure": 3, "gen1_pure_green": 3},
    "exp_static_static_run_gen3": {"gen1_new": 3, "gen1_pure": 3, "gen1_pure_green": 3},
    "exp_static_surf_gen3": {"gen1_new": 3, "gen1_pure": 3, "gen1_pure_green": 3},
    "explode_bench_battle_new": {"gen1_new": 3, "gen1_pure": 3, "gen1_pure_green": 3},
    "explode_gen3": {"gen1_new": 3, "gen1_pure": 3, "gen1_pure_green": 3},
    "explode_new": {"gen1_new": 4, "gen1_pure": 4, "gen1_pure_green": 4},
    "faint": {"gen1_new": 3, "gen1_pure": 3, "gen1_pure_green": 3},
    "faint_cmd_gen3": {"gen1_new": 3, "gen1_pure": 3, "gen1_pure_green": 3},
    "gen2_admit_wrong_rom": {"gen1_new": 3, "gen1_pure": 3, "gen1_pure_green": 3},
    "gen2_ball_gate": {"gen1_new": 3, "gen1_pure": 3, "gen1_pure_green": 3, "gen2_crystal_gold": 2, "gen2_gold_silver": 2, "gen2_new": 2},
    "gen2_boxed_capture": {"gen1_new": 3, "gen1_pure": 3, "gen1_pure_green": 3},
    "gen2_changebox": {"gen1_new": 3, "gen1_pure": 3, "gen1_pure_green": 3},
    "gen2_egg_hatch": {"gen1_new": 3, "gen1_pure": 3, "gen1_pure_green": 3},
    "gen2_evolution": {"gen1_new": 3, "gen1_pure": 3, "gen1_pure_green": 3},
    "gen2_faint": {"gen1_new": 3, "gen1_pure": 3, "gen1_pure_green": 3},
    "gen2_faint_active": {"gen1_new": 3, "gen1_pure": 3, "gen1_pure_green": 3},
    "gen2_faint_active_trainer": {"gen1_new": 3, "gen1_pure": 3, "gen1_pure_green": 3},
    "gen2_gender_clause": {"gen1_new": 3, "gen1_pure": 3, "gen1_pure_green": 3, "gen2_crystal_gold": 3, "gen2_gold_silver": 3, "gen2_new": 3},
    "gen2_gift": {"gen1_new": 3, "gen1_pure": 3, "gen1_pure_green": 3},
    "gen2_npc_trade": {"gen1_new": 3, "gen1_pure": 3, "gen1_pure_green": 3},
    "gen2_pc_ops": {"gen1_new": 3, "gen1_pure": 3, "gen1_pure_green": 3},
    "gen2_poison": {"gen1_new": 3, "gen1_pure": 3, "gen1_pure_green": 3},
    "gen2_reconnect": {"gen1_new": 3, "gen1_pure": 3, "gen1_pure_green": 3},
    "gen2_soft_reset": {"gen1_new": 3, "gen1_pure": 3, "gen1_pure_green": 3},
    "gen2_species_clause": {"gen1_new": 3, "gen1_pure": 3, "gen1_pure_green": 3, "gen2_crystal_gold": 3, "gen2_gold_silver": 3, "gen2_new": 3},
    "gen2_trade_decline_new": {"gen1_new": 3, "gen1_pure": 3, "gen1_pure_green": 3},
    "gen2_trade_evolve": {"gen1_new": 3, "gen1_pure": 3, "gen1_pure_green": 3},
    "gen2_trade_new": {"gen1_new": 3, "gen1_pure": 3, "gen1_pure_green": 3},
    "gen2_trade_refuse_item": {"gen1_new": 3, "gen1_pure": 3, "gen1_pure_green": 3},
    "gen2_trade_reset_commit": {"gen1_new": 3, "gen1_pure": 3, "gen1_pure_green": 3},
    "gen2_trade_reset_wait": {"gen1_new": 3, "gen1_pure": 3, "gen1_pure_green": 3},
    "gen2_trade_timeout": {"gen1_new": 3, "gen1_pure": 3, "gen1_pure_green": 3},
    "gen2_type_clause": {"gen1_new": 3, "gen1_pure": 3, "gen1_pure_green": 3, "gen2_crystal_gold": 3, "gen2_gold_silver": 3, "gen2_new": 3},
    "gen2_whiteout": {"gen1_new": 3, "gen1_pure": 3, "gen1_pure_green": 3},
    "gen2_whiteout_rebuild": {"gen1_new": 3, "gen1_pure": 3, "gen1_pure_green": 3},
    "gender_clause_gen3": {"gen1_new": 3, "gen1_pure": 3, "gen1_pure_green": 3, "gen3_emerald": 3, "gen3_exp": 3, "gen3_frlg": 3, "gen3_lgfr": 3, "gen3_rr": 8},
    "gift_box_gen3": {"gen1_new": 3, "gen1_pure": 3, "gen1_pure_green": 3},
    "gift_gen3": {"gen1_new": 3, "gen1_pure": 3, "gen1_pure_green": 3},
    "infopanel_dex_gen3": {"gen1_new": 3, "gen1_pure": 3, "gen1_pure_green": 3},
    "infopanel_gen3": {"gen1_new": 3, "gen1_pure": 3, "gen1_pure_green": 3},
    "link": {"gen1_new": 3, "gen1_pure": 3, "gen1_pure_green": 3},
    "link_gen3": {"gen1_new": 3, "gen1_pure": 3, "gen1_pure_green": 3, "gen3_emerald": 3, "gen3_exp": 3, "gen3_frlg": 3, "gen3_lgfr": 3, "gen3_rr": 3},
    "link_new": {"gen1_new": 3, "gen1_pure": 3, "gen1_pure_green": 3},
    "linked_faint_active_gen3": {"gen1_new": 3, "gen1_pure": 3, "gen1_pure_green": 3},
    "linked_faint_active_lhammer_gen3": {"gen1_new": 3, "gen1_pure": 3, "gen1_pure_green": 3},
    "linked_faint_active_mega_gen3": {"gen1_new": 3, "gen1_pure": 3, "gen1_pure_green": 3},
    "linked_faint_active_new": {"gen1_new": 3, "gen1_pure": 3, "gen1_pure_green": 3},
    "linked_faint_active_trainer_gen3": {"gen1_new": 3, "gen1_pure": 3, "gen1_pure_green": 3},
    "linked_faint_active_whiteout_gen3": {"gen1_new": 3, "gen1_pure": 3, "gen1_pure_green": 3},
    "linked_faint_bench_battle_new": {"gen1_new": 3, "gen1_pure": 3, "gen1_pure_green": 3},
    "linked_faint_bench_new": {"gen1_new": 3, "gen1_pure": 3, "gen1_pure_green": 3},
    "native_trade_decline_firered": {"gen1_new": 3, "gen1_pure": 3, "gen1_pure_green": 3},
    "native_trade_firered": {"gen1_new": 3, "gen1_pure": 3, "gen1_pure_green": 3},
    "nature_change_gen3": {"gen1_new": 3, "gen1_pure": 3, "gen1_pure_green": 3},
    "npc_trade_gen3": {"gen1_new": 3, "gen1_pure": 3, "gen1_pure_green": 3},
    "pc_ops_new": {"gen1_new": 3, "gen1_pure": 3, "gen1_pure_green": 3},
    "poison_faint_gen3": {"gen1_new": 3, "gen1_pure": 3, "gen1_pure_green": 3},
    "poison_new": {"gen1_new": 4, "gen1_pure": 4, "gen1_pure_green": 4},
    "reconnect_gen3": {"gen1_new": 3, "gen1_pure": 3, "gen1_pure_green": 3},
    "reconnect_new": {"gen1_new": 3, "gen1_pure": 3, "gen1_pure_green": 3},
    "release_gen3": {"gen1_new": 3, "gen1_pure": 3, "gen1_pure_green": 3},
    "rival_swap_gen3": {"gen1_new": 3, "gen1_pure": 3, "gen1_pure_green": 3},
    "rival_swap_new": {"gen1_new": 3, "gen1_pure": 3, "gen1_pure_green": 3},
    "rival_swap_real_gen3": {"gen1_new": 3, "gen1_pure": 3, "gen1_pure_green": 3},
    "save_then_write_gen3": {"gen1_new": 3, "gen1_pure": 3, "gen1_pure_green": 3},
    "shiny_bonus_gen3": {"gen1_new": 8, "gen1_pure": 8, "gen1_pure_green": 8, "gen3_emerald": 8, "gen3_exp": 8, "gen3_frlg": 8, "gen3_lgfr": 8, "gen3_rr": 8},
    "soft_reset_new": {"gen1_new": 3, "gen1_pure": 3, "gen1_pure_green": 3},
    "species_clause_gen3": {"gen1_new": 8, "gen1_pure": 8, "gen1_pure_green": 8, "gen2_crystal_gold": 8, "gen2_gold_silver": 8, "gen2_new": 8, "gen3_emerald": 8, "gen3_exp": 8, "gen3_fr_trade": 8, "gen3_frlg": 8, "gen3_lg_trade": 8, "gen3_lgfr": 8, "gen3_rr": 8},
    "species_clause_new": {"gen1_new": 8, "gen1_pure": 8, "gen1_pure_green": 8},
    "species_family_gen3": {"gen1_new": 8, "gen1_pure": 8, "gen1_pure_green": 8, "gen2_crystal_gold": 8, "gen2_gold_silver": 8, "gen2_new": 8, "gen3_emerald": 8, "gen3_exp": 8, "gen3_fr_trade": 8, "gen3_frlg": 8, "gen3_lg_trade": 8, "gen3_lgfr": 8, "gen3_rr": 16},
    "trade_decline_gen3": {"gen1_new": 3, "gen1_pure": 3, "gen1_pure_green": 3},
    "trade_decline_new": {"gen1_new": 3, "gen1_pure": 3, "gen1_pure_green": 3},
    "trade_gen3": {"gen1_new": 3, "gen1_pure": 3, "gen1_pure_green": 3},
    "trade_lock_probe_gen3": {"gen1_new": 3, "gen1_pure": 3, "gen1_pure_green": 3},
    "trade_new": {"gen1_new": 3, "gen1_pure": 3, "gen1_pure_green": 3},
    "trade_reset_commit_gen3": {"gen1_new": 3, "gen1_pure": 3, "gen1_pure_green": 3},
    "trade_reset_success_gen3": {"gen1_new": 3, "gen1_pure": 3, "gen1_pure_green": 3},
    "trainer_bench_gen3": {"gen1_new": 2, "gen1_pure": 2, "gen1_pure_green": 2, "gen2_crystal_gold": 2, "gen2_gold_silver": 2, "gen2_new": 2, "gen3_emerald": 2, "gen3_exp": 2, "gen3_fr_trade": 2, "gen3_frlg": 2, "gen3_lg_trade": 2, "gen3_lgfr": 2, "gen3_rr": 2},
    "trainer_panel_gen3_rand": {"gen1_new": 3, "gen1_pure": 3, "gen1_pure_green": 3},
    "type_clause_gen3": {"gen1_new": 3, "gen1_pure": 3, "gen1_pure_green": 3, "gen3_emerald": 3, "gen3_exp": 3, "gen3_frlg": 3, "gen3_lgfr": 3, "gen3_rr": 8},
    "type_clause_new": {"gen1_new": 3, "gen1_pure": 3, "gen1_pure_green": 3},
    "whiteout_gen3": {"gen1_new": 3, "gen1_pure": 3, "gen1_pure_green": 3},
    "whiteout_new": {"gen1_new": 3, "gen1_pure": 3, "gen1_pure_green": 3}
}
RAND_WHITEOUT = "RESULT: FAIL (hunt ended whiteout)"


# The snapshot above is d5a26da9's. Only explicitly named sets may drift:
#   1. link_gen3_rand: "rng_attempts": 6 (this task's own change; asserted separately as exactly 6 on FR/LG and Emerald).
#   2. Gen 2 96b65c48 ("a Gen 2 trade scenario retries once ... when its route battle is lost"): every `GEN2_TRADE_SCENARIOS` scenario on a
#      `scenario_family(game) == "gen2_new"` pairing (gen2_new, gen2_gold_silver, gen2_crystal_gold) may be 2. The set comes from the module's own
#      GEN2_TRADE_SCENARIOS (absent on a tree without the change: then those rows must still equal the snapshot).
#   3. Gen 1 O-8 (codex/o8-gen1; 2aa11f88 "bind synth catches ...", 2dec7f9e): the gen1_synth rows carry `"rng_attempts"` in their SCENARIOS
#      entry instead of being keyed by name AND game inside scenario_attempt_limit (the old `if name == "explode_new": return 4` is gone). The
#      entry value applies on every rng-retry family, so the same rows drift on the Gen 3 pairings (e.g. linked_faint_active_new on gen3_frlg,
#      1 -> 3): those pairs never run (the rows are `games: gen1_new`) and the drift is accepted EXPLICITLY, with the exact values pinned below.
#   4. GEN2_ROUTE_LOSS_SCENARIOS: the named opening-catch rows retry once on their Gen 2 pairings.
#   5. gender_clause_gen3: six observation attempts on FR/LG/Emerald only (3 -> 6); RR keeps its prior 8.
#      Species/type and non-applicable game budgets retain their snapshot values.
O8_GEN1_SYNTH_LIMITS = {"linked_faint_active_new": 3, "explode_bench_battle_new": 3, "explode_new": 4}
_GEN2_BALL_GATE_RETRY = "        return 2   # one retry, only when a side ran out of the aide's five natural Balls (GEN2_OUT_OF_BALLS)\n"
_GEN2_TRADE_RETRY = "        return 2   # one retry, only when the route's own wild battle was lost (GEN2_ROUTE_BATTLE_LOST)\n"


def _allowed_attempt_limits(snapshot, name, family, trade_scenarios, rng_family=False, o8_rows=(), route_scenarios=()):
    """Historical limits plus named Gen 2, Gen 1 O-8 and Gen 3 gender observation budgets."""
    allowed = {snapshot}
    if (name in trade_scenarios or name in route_scenarios) and family == "gen2_new":
        allowed.add(2)
    if name in o8_rows and rng_family:
        allowed.add(O8_GEN1_SYNTH_LIMITS[name])
    if name == "gender_clause_gen3" and family in ("gen3_frlg", "gen3_emerald"):
        allowed.add(6)
    return allowed


def _o8_rows(module):
    """The module's gen1_synth rows that carry `rng_attempts` (derived, not listed); only the pinned names are allowed a new value."""
    return {n for n, row in module.SCENARIOS.items() if row.get("gen1_synth") and row.get("rng_attempts") and n in O8_GEN1_SYNTH_LIMITS}


def _attempt_budget_problems(module):
    """Every scenario/game pair whose scenario_attempt_limit left its allowed set, except link_gen3_rand (6) and the probe row (new, 1).
    [(name, game, snapshot, now)]"""
    trade = getattr(module, "GEN2_TRADE_SCENARIOS", ())
    route = getattr(module, "GEN2_ROUTE_LOSS_SCENARIOS", ())
    o8 = _o8_rows(module)
    problems = []
    for game in module.GAMES:
        for name in module.SCENARIOS:
            if name in ("link_gen3_rand", "probe_protected_span_flip_gen3"):      # (the probe row did not exist at d5a26da9)
                continue
            was = ATTEMPT_LIMITS_AT_D5A26DA9.get(name, {}).get(game, 1)
            now = module.scenario_attempt_limit(name, game)
            if now not in _allowed_attempt_limits(was, name, module.scenario_family(game), trade, module.rng_retry_family(game), o8, route):
                problems.append((name, game, was, now))
    return problems


def test_the_attempt_limit_expectation_builder_in_both_modes():
    # Gen 2 trade rows
    assert _allowed_attempt_limits(1, "gen2_trade_x", "gen2_new", ()) == {1}                       # symbol absent: snapshot unchanged
    assert _allowed_attempt_limits(1, "gen2_trade_x", "gen2_new", ("gen2_trade_x",)) == {1, 2}      # present: the trade rows may be 2
    assert _allowed_attempt_limits(1, "gen2_trade_x", "gen3_frlg", ("gen2_trade_x",)) == {1}        # ... only on the gen2_new pairings
    assert _allowed_attempt_limits(3, "link_gen3", "gen2_new", ("gen2_trade_x",)) == {3}            # ... and only for those scenarios
    # Gen 1 O-8 rows: the pinned value, only for a named row that carries rng_attempts, only on an rng-retry family
    assert _allowed_attempt_limits(1, "explode_new", "gen3_frlg", (), True, {"explode_new"}) == {1, 4}
    assert _allowed_attempt_limits(1, "explode_new", "gen2_new", (), False, {"explode_new"}) == {1}
    assert _allowed_attempt_limits(1, "explode_new", "gen3_frlg", (), True, set()) == {1}
    assert _allowed_attempt_limits(1, "link_gen3", "gen3_frlg", (), True, {"explode_new"}) == {1}
    # Only gender observation on its applicable Gen 3 families gets the six-attempt exception.
    assert _allowed_attempt_limits(3, "gender_clause_gen3", "gen3_frlg", ()) == {3, 6}
    assert _allowed_attempt_limits(3, "gender_clause_gen3", "gen3_emerald", ()) == {3, 6}
    assert _allowed_attempt_limits(8, "gender_clause_gen3", "gen3_rr", ()) == {8}
    assert _allowed_attempt_limits(3, "gender_clause_gen3", "gen3_exp", ()) == {3}
    assert _allowed_attempt_limits(3, "gender_clause_gen3", "gen1_new", ()) == {3}
    assert _allowed_attempt_limits(8, "species_clause_gen3", "gen3_frlg", ()) == {8}
    assert _allowed_attempt_limits(3, "type_clause_gen3", "gen3_frlg", ()) == {3}
    assert sorted(g for g in duo.GAMES if duo.scenario_family(g) == "gen2_new") == ["gen2_crystal_gold", "gen2_gold_silver", "gen2_new"]


def _fake_module(trade_rows_at=None, with_symbol=False, o8=True):
    """A module-shaped object around the real tables, whose scenario_attempt_limit is the SNAPSHOT value everywhere except what the caller
    says (the Gen 2 trade rows at `trade_rows_at`, the O-8 rows at their pinned values)."""
    import types

    def limit(name, game):
        was = ATTEMPT_LIMITS_AT_D5A26DA9.get(name, {}).get(game, 1)
        if name in ("link_gen3_rand", "probe_protected_span_flip_gen3"):
            return duo.scenario_attempt_limit(name, game)
        if trade_rows_at is not None and name in duo.GEN2_TRADE_SCENARIOS and duo.scenario_family(game) == "gen2_new":
            return trade_rows_at
        if o8 and name in O8_GEN1_SYNTH_LIMITS and duo.rng_retry_family(game):
            return O8_GEN1_SYNTH_LIMITS[name]
        return was

    module = types.SimpleNamespace(GAMES=duo.GAMES, SCENARIOS=duo.SCENARIOS, scenario_family=duo.scenario_family,
                                   rng_retry_family=duo.rng_retry_family, scenario_attempt_limit=limit)
    if with_symbol:
        module.GEN2_TRADE_SCENARIOS = duo.GEN2_TRADE_SCENARIOS
    return module


def test_the_snapshot_check_works_on_a_module_without_the_symbol():
    """STRICT no-symbol mode: a module with no GEN2_TRADE_SCENARIOS is held to the plain snapshot (+ link6 + O-8), so a Gen 2 trade row at 2
    is a problem there -- and is the ONLY problem. With the symbol the same limits are fine."""
    trade_pairs = sorted((n, g) for g in duo.GAMES for n in duo.GEN2_TRADE_SCENARIOS if duo.scenario_family(g) == "gen2_new")
    assert len(trade_pairs) == 21
    bare = _fake_module(trade_rows_at=2)
    assert not hasattr(bare, "GEN2_TRADE_SCENARIOS")
    assert sorted((n, g) for n, g, _was, _now in _attempt_budget_problems(bare)) == trade_pairs
    assert _attempt_budget_problems(_fake_module(trade_rows_at=2, with_symbol=True)) == []
    assert _attempt_budget_problems(_fake_module()) == []                          # no symbol, snapshot values: clean
    assert sorted((n, g) for n, g, _was, _now in _attempt_budget_problems(_fake_module(trade_rows_at=3, with_symbol=True))) == trade_pairs
    # O-8: without its rows' `rng_attempts` the drifted pairs are NOT excused
    stripped = _fake_module()
    stripped.SCENARIOS = {n: {k: v for k, v in row.items() if k != "rng_attempts"} if n in O8_GEN1_SYNTH_LIMITS else row
                          for n, row in duo.SCENARIOS.items()}
    drifted = {n for n, _g, _was, _now in _attempt_budget_problems(stripped)}
    assert drifted == set(O8_GEN1_SYNTH_LIMITS)


def test_link_gen3_rand_and_gender_clause_gen3_have_six_attempts_without_other_drift():
    assert _attempt_budget_problems(duo) == []
    assert [duo.scenario_attempt_limit("link_gen3_rand", g) for g in ("gen3_frlg", "gen3_emerald")] == [6, 6]    # the Emerald twin too
    assert [duo.scenario_attempt_limit("gender_clause_gen3", g)
            for g in ("gen3_frlg", "gen3_lgfr", "gen3_emerald", "gen3_rr")] == [6, 6, 6, 8]
    assert [duo.scenario_attempt_limit("probe_protected_span_flip_gen3", g) for g in ("gen3_frlg", "gen3_emerald")] == [1, 1]
    assert [n for n, row in duo.SCENARIOS.items() if row.get("rng_attempts") == 6] == ["link_gen3_rand"]
    assert not any("rehunt" in k for row in duo.SCENARIOS.values() for k in row)


def test_the_gen2_gen1_and_gen3_gender_changed_sets_are_exactly_the_named_ones():
    """Name every drift the snapshot excuses, so a new one cannot hide inside the allowed sets."""
    drifted = {}
    for game in duo.GAMES:
        for name in duo.SCENARIOS:
            if name in ("link_gen3_rand", "probe_protected_span_flip_gen3"):
                continue
            was = ATTEMPT_LIMITS_AT_D5A26DA9.get(name, {}).get(game, 1)
            now = duo.scenario_attempt_limit(name, game)
            if now != was:
                drifted.setdefault(name, set()).add((game, was, now))
    gen2 = {n: v for n, v in drifted.items() if n in duo.GEN2_TRADE_SCENARIOS}
    assert set(gen2) <= set(duo.GEN2_TRADE_SCENARIOS)
    for name, rows in gen2.items():
        assert {game for game, _w, now in rows} <= {"gen2_new", "gen2_gold_silver", "gen2_crystal_gold"} and {now for _g, _w, now in rows} == {2}, name
    route = {n: v for n, v in drifted.items() if n in duo.GEN2_ROUTE_LOSS_SCENARIOS}
    assert set(route) == {
        "gen2_faint", "gen2_whiteout", "gen2_pc_ops", "gen2_changebox", "gen2_poison", "gen2_whiteout_rebuild",
    }
    for name, rows in route.items():
        assert {game for game, _w, now in rows} == {"gen2_new", "gen2_gold_silver", "gen2_crystal_gold"}
        assert {now for _g, _w, now in rows} == {2}, name
    o8 = {n: v for n, v in drifted.items() if n in O8_GEN1_SYNTH_LIMITS}
    for name, rows in o8.items():
        assert {now for _g, _w, now in rows} == {O8_GEN1_SYNTH_LIMITS[name]}, name
        assert all(duo.rng_retry_family(game) and not duo.scenario_applies(name, game) for game, _w, _n in rows), name    # pairs that never run
    gender = {n: v for n, v in drifted.items() if n == "gender_clause_gen3"}
    assert gender == {"gender_clause_gen3": {
        ("gen3_frlg", 3, 6), ("gen3_lgfr", 3, 6), ("gen3_emerald", 3, 6),
    }}
    assert set(drifted) == set(gen2) | set(route) | set(o8) | set(gender), sorted(set(drifted) - set(gen2) - set(route) - set(o8) - set(gender))
    assert {n for n, row in duo.SCENARIOS.items() if row.get("gen1_synth") and row.get("rng_attempts")} >= set(O8_GEN1_SYNTH_LIMITS)
    assert all(duo.SCENARIOS[n]["rng_attempts"] == v for n, v in O8_GEN1_SYNTH_LIMITS.items())


def _module_from_source(source, tmp_path):
    import importlib.util

    # a fresh name per variant: a same-size edit written within the same second would otherwise load the stale __pycache__ entry
    path = tmp_path / f"e2e_duo_variant_{len(list(tmp_path.glob('e2e_duo_variant_*.py')))}.py"
    path.write_text(source, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("e2e_duo_variant", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["e2e_duo_variant"] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop("e2e_duo_variant", None)
    return module


def test_the_snapshot_catches_any_other_drift_in_the_real_source(tmp_path):
    """Mutate THIS tree's e2e_duo.py source and run the same check on the variant: every kind of other drift is a problem."""
    source = (REPO / "tools" / "e2e_duo.py").read_text(encoding="utf-8").replace("\r\n", "\n")
    assert _attempt_budget_problems(_module_from_source(source, tmp_path)) == []                    # the unmutated source is clean

    def variant(old, new):
        assert source.count(old) == 1, old
        return _attempt_budget_problems(_module_from_source(source.replace(old, new), tmp_path))
    assert source.count(_GEN2_TRADE_RETRY) == 1 and source.count(_GEN2_BALL_GATE_RETRY) == 1
    third = variant(_GEN2_TRADE_RETRY, _GEN2_TRADE_RETRY.replace("return 2", "return 3"))             # a third attempt on a trade row
    assert {n for n, *_ in third} == set(duo.GEN2_TRADE_SCENARIOS) | set(duo.GEN2_ROUTE_LOSS_SCENARIOS)
    moved_gate = variant(_GEN2_BALL_GATE_RETRY, _GEN2_BALL_GATE_RETRY.replace("return 2", "return 3"))    # ball_gate moved
    assert {n for n, *_ in moved_gate} == {"gen2_ball_gate"}
    anchor = '    entry = SCENARIOS.get(name, {})\n    if entry.get("rule_kind") == "family"'
    gen3 = variant(anchor, '    if name == "link_gen3" and scenario_family(game) == "gen3_frlg":\n        return 2\n' + anchor)
    assert sorted((n, g, now) for n, g, _w, now in gen3) == [("link_gen3", "gen3_frlg", 2), ("link_gen3", "gen3_lgfr", 2)]   # (lgfr = the family)
    gen1 = variant('"gen1_synth": "explode", "rng_attempts": 4, "no_setup"', '"gen1_synth": "explode", "rng_attempts": 5, "no_setup"')
    assert {n for n, *_ in gen1} == {"explode_new"} and {now for *_x, now in gen1} == {5}            # a gen1 row beyond its new value
    no_entry = variant('"gen1_synth": "explode", "rng_attempts": 4, "no_setup"', '"gen1_synth": "explode", "no_setup"')
    assert {n for n, *_ in no_entry} <= {"explode_new"}                                              # (explode_new is back to the default)
    gender_budget = '    if entry.get("rule_kind") == "gender" and scenario_family(game) in ("gen3_frlg", "gen3_emerald"):\n'
    wrong_clause = variant(gender_budget, gender_budget.replace('== "gender"', '== "type"'))
    assert {n for n, *_ in wrong_clause} == {"type_clause_gen3"}
    wrong_game = variant(gender_budget, gender_budget.replace('"gen3_emerald")', '"gen3_emerald", "gen3_exp")'))
    assert wrong_game == [("gender_clause_gen3", "gen3_exp", 3, 6)]


@pytest.mark.parametrize("game", ["gen3_frlg", "gen3_emerald"])
def test_attempts_past_two_retry_a_link_gen3_rand_whiteout_and_nothing_else(game):
    receipts = {"a": RAND_WHITEOUT, "b": None}
    for attempt in (1, 2, 3, 4, 5):
        assert duo.retryable_gen1_rng(game, receipts, attempt, 6, scenario="link_gen3_rand"), attempt
    assert not duo.retryable_gen1_rng(game, receipts, 6, 6, scenario="link_gen3_rand")        # the budget ends at 6
    partner = "RESULT: FAIL (runner never released B (A_PENDING))"
    assert duo.retryable_gen1_rng(game, {"a": RAND_WHITEOUT, "b": partner}, 4, 6, scenario="link_gen3_rand")
    # an out-of-balls miss keeps the ordinary two retries; past them it is final (the late clause is for a whiteout only)
    assert duo.retryable_gen1_rng(game, {"a": duo.RNG_OUT_OF_BALLS, "b": None}, 2, 6, scenario="link_gen3_rand")
    assert not duo.retryable_gen1_rng(game, {"a": duo.RNG_OUT_OF_BALLS, "b": None}, 3, 6, scenario="link_gen3_rand")
    assert not duo.retryable_gen1_rng(game, {"a": RAND_WHITEOUT, "b": duo.RNG_OUT_OF_BALLS}, 3, 6, scenario="link_gen3_rand")


@pytest.mark.parametrize("scenario", ["link_gen3", "trainer_panel_gen3_rand", "admit_randomized_frlg", "ball_gate_gen3", None])
def test_the_late_whiteout_retry_does_not_fire_for_other_rows(scenario):
    receipts = {"a": RAND_WHITEOUT, "b": None}
    assert not duo.retryable_gen1_rng("gen3_frlg", receipts, 3, 6, scenario=scenario)
    assert not duo.retryable_gen1_rng("gen3_frlg", receipts, 3, 2, scenario="link_gen3_rand")      # only at its own declared budget


def test_only_the_flipped_instance_is_launched_as_a_probe():
    source = (REPO / "tools" / "e2e_duo.py").read_text(encoding="utf-8")
    assert 'duo["probe_admission"] = bool(self.cfg.get("gen3_probe_flip")) and inst == "b"' in source


@pytest.mark.parametrize("game, flipped", [("gen3_frlg", "leafgreen"), ("gen3_lgfr", "firered"), ("gen3_emerald", "emerald")])
def test_each_probe_game_flips_one_named_title_on_the_b_cart(game, flipped):
    """gen3_frlg is FireRed(A)/LeafGreen(B), gen3_lgfr is LeafGreen(A)/FireRed(B), gen3_emerald is Emerald/Emerald: B is the flipped cart,
    so the three games observe LeafGreen, FireRed and Emerald. Their fixtures are the ordinary party_town battery pair."""
    sides = duo.GAMES[game]["sides"]
    assert sides["b"][0] == flipped and duo.GAMES[game]["scenario_prefix"] == "gen3_"
    assert duo.scenario_target(duo.SCENARIOS[PROBE], game) == "town"
    assert duo.scenario_applies(PROBE, game)


def test_the_observed_line_names_which_title_was_flipped_and_where():
    run, notes, _ = _probe_run("PROBE_ADMISSION client=refused_at_launch reason=x\n")
    run.orchestrate_probe_protected_span_flip_gen3()
    observed = next(n for n in notes if n.startswith("PROBE_FLIP_OBSERVED"))
    for fact in ("flipped_title=firered", "cart=b", "offset=0xeb2e27", "inside_protected_span=0xeb2000+0x4000", "on_anchor=false",
                 "checked 11 anchors", "client=refused_at_launch"):
        assert fact in observed, fact
    notes.clear()
    run.assert_probe_protected_span_flip_gen3_saved({"a": "", "b": "PROBE_ADMISSION client=refused_at_launch reason=x\n"})
    assert "flipped_title=firered" in notes[0] and "inside_protected_span=0xeb2000+0x4000" in notes[0]


# ── ctx.catch is strict about the client's capture event (the party scan only for named exemptions, of which there are none) ──────────
NO_CAPTURE_MESSAGE = "caught, but the client never sent a capture event"
CATCH_CALLER_MODULES = {"clause", "deadzone", "link", "shiny_bonus", "static_wild"}     # (+ rand_link and ball_gate, which dofile() link)


def _caught_key_world(scenario="link_gen3", exempt=(), party="{}", sent="", on_frame="nil"):
    from lupa import LuaRuntime

    source = DRIVER.read_text(encoding="utf-8")
    found = re.search(_LUA_DEF.format(re.escape("ctx.caught_key")), source, re.M | re.S)
    assert found, "duo_gen3_main.lua has no ctx.caught_key"
    window = int(re.search(r"CAPTURE_TX_WINDOW_FRAMES = (\d+)", source)[1])
    runtime = LuaRuntime(unpack_returned_tuples=True)
    runtime.execute(f"""
        D = {{ scenario = {scenario!r} }}
        boot_keys = {{ BOOT = true }}
        CAPTURE_TX_WINDOW_FRAMES = {window}
        tx = {{ {sent} }}
        frames_run = 0
        on_frame = {on_frame}
        ctx = {{ catch_without_capture_event = {{ {"".join(f"[{name!r}] = true," for name in exempt)} }} }}
        function ctx.last_sent(ev) for i = #tx, 1, -1 do if tx[i].event == ev then return tx[i].msg end end end
        function ctx.sent(ev) local n = 0 for _, e in ipairs(tx) do if e.event == ev then n = n + 1 end end return n end
        function ctx.frames(n) frames_run = frames_run + n; if on_frame then on_frame(frames_run) end end
        function ctx.party() return {party} end
        {found.group(0)}
    """)
    return runtime, window


def _call(runtime, captures_before):
    """ctx.caught_key's (key, why) with a Lua nil as None (a Lua table would drop it)."""
    returned = runtime.eval("ctx.caught_key")(captures_before)
    returned = list(returned) if isinstance(returned, tuple) else [returned]
    return tuple(returned + [None] * (2 - len(returned)))


def _capture(key):
    return "{ event = 'capture', msg = { key = '%s' } }" % key


def test_a_capture_tx_already_sent_names_the_key_without_waiting():
    runtime, _ = _caught_key_world(sent=_capture("K1"))
    assert _call(runtime, 0) == ("K1", None) and runtime.eval("frames_run") == 0


def test_a_capture_tx_that_arrives_late_within_the_frame_window_is_used():
    runtime, window = _caught_key_world(on_frame="function(n) if n == %d then tx[#tx + 1] = %s end end" % (window_minus_one(), _capture("K2")))
    assert _call(runtime, 0) == ("K2", None)
    assert runtime.eval("frames_run") == window_minus_one() and window_minus_one() < window


def window_minus_one():
    return int(re.search(r"CAPTURE_TX_WINDOW_FRAMES = (\d+)", DRIVER.read_text(encoding="utf-8"))[1]) - 1


def test_no_capture_tx_inside_the_window_fails_by_name_even_with_a_new_mon_in_the_party():
    runtime, window = _caught_key_world(party="{ { key = 'NEW' } }")
    got = _call(runtime, 0)
    assert got[0] is None and got[1] == NO_CAPTURE_MESSAGE                         # strict: the party scan does NOT rescue it
    assert runtime.eval("frames_run") == window == 600                             # a FRAME window, not wall clock
    assert "wait_until" not in re.search(_LUA_DEF.format(re.escape("ctx.caught_key")),
                                          DRIVER.read_text(encoding="utf-8"), re.M | re.S)[0]


def test_an_earlier_mons_capture_or_a_boot_key_is_not_this_catchs_event():
    stale, _ = _caught_key_world(sent=_capture("OLD"))
    assert _call(stale, 1)[1] == NO_CAPTURE_MESSAGE      # one capture was already sent on entry
    boot, _ = _caught_key_world(sent=_capture("BOOT"))
    assert _call(boot, 0)[1] == NO_CAPTURE_MESSAGE


def test_a_named_exemption_still_scans_the_party_and_nothing_else_does():
    runtime, _ = _caught_key_world(scenario="exempt_row", exempt=("exempt_row",), party="{ { key = 'BOOT' }, { key = 'NEW' } }")
    got = _call(runtime, 0)
    assert got[0] == "NEW" and runtime.eval("frames_run") == 0                      # no wait for an event nobody sends
    empty, _ = _caught_key_world(scenario="exempt_row", exempt=("exempt_row",), party="{ { key = 'BOOT' } }")
    assert _call(empty, 0)[1] == "caught, but no new key in the party"
    other, _ = _caught_key_world(scenario="not_exempt", exempt=("exempt_row",), party="{ { key = 'NEW' } }")
    assert _call(other, 0)[1] == NO_CAPTURE_MESSAGE


def test_every_ctx_catch_caller_gets_the_strict_behaviour_because_the_exemption_list_is_empty():
    source = DRIVER.read_text(encoding="utf-8")
    assert "ctx.catch_without_capture_event = {}" in source                       # EMPTY, in the driver
    callers = {path.stem.removeprefix("scenario_gen3_") for path in (REPO / "lua" / "tests" / "duo").glob("scenario_gen3_*.lua")
               if "ctx.catch(" in path.read_text(encoding="utf-8")}
    assert callers == CATCH_CALLER_MODULES, callers        # a NEW caller must be looked at: does it expect a capture event?
    for name in duo.SCENARIOS:                              # no scenario of any row is exempt
        runtime, _ = _caught_key_world(scenario=name, party="{ { key = 'NEW' } }")
        assert _call(runtime, 0)[1] == NO_CAPTURE_MESSAGE, name
    assert "return ctx.caught_key(captures_before)" in source and "local captures_before = ctx.sent(\"capture\")" in source


def test_the_missing_capture_event_failure_is_never_retried_for_any_scenario():
    for reason in (NO_CAPTURE_MESSAGE, "link_new prerequisite failed: hunt ended " + NO_CAPTURE_MESSAGE):
        for prefix in ("hunt ended ", "native catch failed: ", ""):
            text = f"RESULT: FAIL ({prefix}{reason})"
            assert duo.classify_gen1_result(text) == "FINAL", text                          # classification=real, not CAUSE_RNG
            for name in duo.SCENARIOS:
                for game in duo.GAMES:
                    limit = duo.scenario_attempt_limit(name, game)
                    for attempt in range(1, limit + 1):
                        assert not duo.retryable_gen1_rng(game, {"a": text, "b": None}, attempt, limit, scenario=name), (name, game, attempt)


def test_native_arena_size_is_the_abi_header_extent():
    """The idle-peer oracle's arena scope is the companion ABI's reserved extent, not a copied guess."""
    import re
    abi = (Path(duo.REPO) / "patch" / "src" / "trade_targets" / "abi.h").read_text(encoding="utf-8")
    assert int(re.search(r"#define SLINK_ARENA_SIZE (0x[0-9A-Fa-f]+)u", abi).group(1), 16) == duo.GEN3_NATIVE_ARENA_SIZE
