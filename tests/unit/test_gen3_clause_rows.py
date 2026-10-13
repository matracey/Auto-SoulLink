"""CLAUSE-ROWS-G3: MODEL falsifiers. These tests never start an emulator."""
import importlib
import json
import os
import re
import struct
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests/unit"))
import e2e_duo as duo  # noqa: E402


@pytest.mark.parametrize("game", ["gen3_frlg", "gen3_lgfr", "gen3_emerald", "gen3_rr"])
@pytest.mark.parametrize("name", ["species_clause_gen3", "gender_clause_gen3", "type_clause_gen3", "release_gen3"])
def test_rule_row_dispatch_is_complete(name, game):
    assert duo.scenario_applies(name, game)
    row = duo.SCENARIOS[name]
    assert callable(getattr(duo.DuoRun, row["oracle"], None))
    assert callable(getattr(duo.DuoRun, "orchestrate_" + name, None))
    module = row.get("scenario_module", name.removesuffix("_gen3"))
    assert (ROOT / "lua/tests/duo" / f"scenario_gen3_{module}.lua").is_file()


def test_release_oracle_rejects_a_surviving_or_duplicated_released_key():
    rules = importlib.import_module("gen3_clause_rows")
    def key(m):
        return m["key"]
    fixture = ([{"key": "starter", "species": 7}, {"key": "released", "species": 16}], {})
    saved = ([{"key": "starter", "species": 7}], {})
    assert rules.release_membership(saved, fixture, "released", key) == []
    assert rules.release_membership(fixture, fixture, "released", key)
    assert rules.release_membership((saved[0], {(0, 0): fixture[0][1]}), fixture, "released", key)
    assert rules.release_membership(([], {}), fixture, "released", key)


def test_species_uses_family_not_species_equality():
    rules = importlib.import_module("gen3_clause_rows")
    facts = {"16": {"family": 16}, "17": {"family": 16}, "19": {"family": 19}}
    assert rules.same_family(facts, 16, 17)
    assert not rules.same_family(facts, 16, 19)
    with pytest.raises(ValueError, match="unproven species"):
        rules.same_family(facts, 16, 999)


def test_rule_retry_limits_preserve_rng_boundaries():
    assert duo.scenario_attempt_limit("species_clause_gen3", "gen3_rr") == 8
    assert duo.scenario_attempt_limit("gender_clause_gen3", "gen3_emerald") == 6   # observation budget (freeze #3)
    assert duo.scenario_attempt_limit("release_gen3", "gen3_frlg") == 1


@pytest.mark.parametrize("game", ["gen3_frlg", "gen3_lgfr", "gen3_emerald", "gen3_rr"])
def test_ball_gate_has_native_carrier_and_saved_oracle(game):
    assert duo.scenario_applies("ball_gate_gen3", game)
    row = duo.SCENARIOS["ball_gate_gen3"]
    assert callable(getattr(duo.DuoRun, row["oracle"], None))
    assert not row.get("cold_boot")  # disclosed setup; gate and acquisition are native


def test_zero_ball_synth_is_a_save_edit_not_activation():
    from test_e2e_duo_gen3 import PIDGEY, STARTER, _fixture
    rules = importlib.import_module("gen3_clause_rows")
    body, manifest = rules.ball_gate_seed(_fixture([STARTER, PIDGEY]), "firered")
    assert duo.gen3_ball_count(body, "firered") == 0
    party, _ = duo.gen3_decode(body)
    assert party[0]["hp"] == 1 and party[0]["status"] == 0
    assert party[1]["hp"] > 0
    assert manifest and "SYNTH" in manifest[0]


@pytest.mark.parametrize("game", ["gen3_frlg", "gen3_lgfr", "gen3_emerald", "gen3_rr"])
def test_evolution_family_is_a_separate_live_subject(game):
    row = duo.SCENARIOS["species_family_gen3"]
    assert duo.scenario_applies("species_family_gen3", game)
    assert row["flags"] == ["--species-clause"]
    assert row["explicit_only"]
    assert duo.scenario_attempt_limit("species_family_gen3", game) == (16 if game == "gen3_rr" else 8)
    assert callable(getattr(duo.DuoRun, row["oracle"], None))


def _type_case(monkeypatch, tmp_path):
    from test_e2e_duo_gen3 import PIDGEY, STARTER, _fixture, _key, _mon, _oracle_stub, _saved
    rules = importlib.import_module("gen3_clause_rows")
    fixture = _fixture([STARTER, PIDGEY])
    ca, cb = _mon(0x11223344, species=16, level=3), _mon(0x55667744, species=19, level=3)
    ka, kb = _key(ca), _key(cb)
    saved = {"a": _saved(fixture, 3, [STARTER, PIDGEY], {(0, 0): ca}, balls=3),
             "b": _saved(fixture, 3, [STARTER, PIDGEY], {(13, 0): cb}, balls=3)}
    run, notes = _oracle_stub(monkeypatch, tmp_path, "type_clause_gen3", saved, fixture, [])
    run._clause_facts = {i: {"16": {"family": 16, "types": [0, 2], "gender_ratio": 127},
                                "19": {"family": 19, "types": [0, 0], "gender_ratio": 127}} for i in ("a", "b")}
    run._status = lambda: {"pending_captures": {"route_1": {"a": {"key": ka, "species": 16}}},
                          "area_states": {"route_1": "pending_b"}}
    run._reconnect_document = lambda: {"retry_areas": {"b": ["route_1"]}}
    caps = {i: {"key": key, "species_id": species, "area_id": "route_1", "level": 3, "held_item_id": 0}
            for i, key, species in (("a", ka, 16), ("b", kb, 19))}
    run._clause_pending = {"key": ka, "species": 16}
    def tag(name, value):
        return name + " " + json.dumps(value) + "\n"
    results = {i: tag("CLAUSE_ENCOUNTER", {"species": c["species_id"], "n": 1, "dupe": False})
               + f"TX capture {c['key']} " + json.dumps(c) + "\nTHREW 1\n" for i, c in caps.items()}
    results["a"] += tag("PENDING_CAPTURE", caps["a"]) + tag("RULE_RX", {"cmd": "play_sound", "sound": 22})
    results["b"] = tag("A_PENDING", caps["a"]) + results["b"]
    for value in [{"cmd": "force_faint", "key": kb}, {"cmd": "memorialize", "key": kb},
                  {"cmd": "gui_prompt", "text": "[x] Type clause: shared Normal"},
                  {"cmd": "play_sound", "sound": 26}, {"cmd": "unresolve_area", "area_id": "route_1"}]:
        results["b"] += tag("RULE_RX", value)
    results["b"] += f"RX force_faint key={kb}\nFORCED_HP0 {kb} frame=9 in_battle=0 battler=0\nTX memorialize_done {kb} {{}}\n"
    return rules, run, results, saved, notes


def test_type_oracle_checks_real_saves_and_actual_rule_facts(monkeypatch, tmp_path):
    rules, run, results, _, notes = _type_case(monkeypatch, tmp_path)
    rules.clause_oracle(run, results)
    assert notes and "B rejected" in notes[-1]


def test_type_nonoverlap_stops_before_run_or_capture():
    from lupa import LuaRuntime
    lua = LuaRuntime(unpack_returned_tuples=True)
    scenario = lua.execute("return dofile('lua/tests/duo/scenario_gen3_clause.lua')")
    ctx = lua.execute('''
      local c={player='b',rr=true,D={rule_kind='type',clause_facts={
        ['16']={types={0,2}},['25']={types={13,13}}}},runs=0,catches=0}
      c.wait_go=function() return true end
      c.go_value=function() return {key='A',species_id=16,area_id='route_1'} end
      c.partner_done=function() return false end
      c.wait_until=function(p) return p() end
      c.rx_count=function() return 0 end
      c.hunt=function() return true end
      c.wild_ready=function() return true end
      c.enemy_species=function() return 25 end
      c.run_away=function() c.runs=c.runs+1;return true end
      c.catch=function() c.catches=c.catches+1;return 'B' end
      c.jlog=function() end
      c.log=function() end
      c.last_sent=function() return {key='B'} end
      c.received=function() return 0 end
      c.rx_after=function() return nil end
      return c
    ''')
    ok, why = scenario(ctx)
    assert ok is False and why == "RNG: type first encounter has no overlap"
    assert (ctx["runs"], ctx["catches"]) == (0, 0)


def test_type_no_overlap_rng_retry_is_exact_and_rr_scoped():
    reason = "RNG: type first encounter has no overlap"
    b = f"RESULT: FAIL ({reason})\n"
    a = "RESULT: FAIL (no type-clause verdict (partner-gone))\n"
    assert duo.scenario_attempt_limit("type_clause_gen3", "gen3_rr") == 8
    assert duo.retryable_gen1_rng("gen3_rr", {"a": a, "b": b}, 3, 8, scenario="type_clause_gen3")
    assert not duo.retryable_gen1_rng("gen3_rr", {"a": a, "b": b.replace(reason, "type mismatch")}, 3, 8,
                                      scenario="type_clause_gen3")
    assert not duo.retryable_gen1_rng("gen3_frlg", {"a": a, "b": b}, 3, 8,
                                      scenario="type_clause_gen3")


def test_rr_type_oracle_refuses_unmatched_first_foe_and_no_catch(monkeypatch, tmp_path):
    rules, run, results, _, _ = _type_case(monkeypatch, tmp_path)
    monkeypatch.setattr(type(run), "_gen3_rr", property(lambda _: True))
    with pytest.raises(RuntimeError, match="ROM-matched first encounter"):
        rules.clause_oracle(run, results)
    results["b"] = results["b"].replace('"dupe": false}', '"dupe": false, "type_overlap": true}')
    results["b"] += "TX no_catch - {}\n"
    with pytest.raises(RuntimeError, match="no_catch"):
        rules.clause_oracle(run, results)
    results["b"] = results["b"].replace("TX no_catch - {}\n", "")
    run._reconnect_events = lambda: [{"type": "dead_zone"}]
    with pytest.raises(RuntimeError, match="dead zone"):
        rules.clause_oracle(run, results)


def test_clause_oracle_requires_the_independently_observed_pending_key(monkeypatch, tmp_path):
    rules, run, results, _, _ = _type_case(monkeypatch, tmp_path)
    run._clause_pending = {"key": "wrong", "species": 16}
    with pytest.raises(RuntimeError, match="pending"):
        rules.clause_oracle(run, results)


@pytest.mark.parametrize("old,new", [("FORCED_HP0", "NO_HP_WITNESS"),
                                    ("memorialize_done", "memorialize_failed"),
                                    ("shared Normal", "shared Fire"),
                                    ("THREW 1", "NO_THROW"),
                                    ('"area_id": "route_1"', '"area_id": "route_2"')])
def test_clause_oracle_rejects_missing_or_wrong_native_evidence(monkeypatch, tmp_path, old, new):
    rules, run, results, _, _ = _type_case(monkeypatch, tmp_path)
    results["b"] = results["b"].replace(old, new)
    with pytest.raises(RuntimeError):
        rules.clause_oracle(run, results)


def test_clause_oracle_rejects_a_live_catch_in_the_memorial_slot(monkeypatch, tmp_path):
    rules, run, results, saved, _ = _type_case(monkeypatch, tmp_path)
    saved["b"] = saved["a"]
    with pytest.raises(RuntimeError, match="memorial"):
        rules.clause_oracle(run, results)


@pytest.mark.parametrize("title,expected", [("firered", 17), ("leafgreen", 17), ("emerald", 287), ("radical_red", 289)])
def test_family_builder_uses_its_own_rom_and_preserves_the_key(title, expected):
    import gen3_fixtures as fx

    from server.adapters import gen3_codec as c
    rules = importlib.import_module("gen3_clause_rows")
    if not os.environ.get("SLINK_GEN3_ROMS"):
        pytest.skip("own-title ROM source checks require SLINK_GEN3_ROMS")
    rr, layout = title == "radical_red", "emerald" if title == "emerald" else "frlg"
    stem = {"firered": "firered_party_battle", "leafgreen": "leafgreen_party_battle",
            "emerald": "emerald_battle", "radical_red": "rr_battle2"}[title]
    before = (ROOT / f"tests/fixtures/gen3/{stem}.sav").read_bytes()
    body, manifest = rules.family_seed(before, title)
    old, new = c.party_from_save(before, rr=rr, title=layout), c.party_from_save(body, rr=rr, title=layout)
    assert fx.qualify_one(body, rr=rr, title=layout)["ok"]
    assert (new[0]["species"], new[0]["level"], new[0]["status"]) == (expected, 25, 0)
    assert duo.gen3_key(old[0]) == duo.gen3_key(new[0])
    assert new[0]["hp"] == new[0]["max_hp"] > 0
    assert old[1:] == new[1:] and manifest[0].startswith("SYNTH")
    facts = rules.species_facts(rules.source_rom(title), title)
    lower = {"firered": 16, "leafgreen": 16, "emerald": 286, "radical_red": 288}[title]
    assert rules.same_family(facts, expected, lower)
    assert rules.gender({"1": {"gender_ratio": 255}}, 1, "00000000:00000001") == "genderless"


@pytest.mark.parametrize("title", ["firered", "leafgreen", "emerald"])
def test_native_pickup_script_and_walk_tiles_come_from_that_titles_rom(title, monkeypatch):
    import gba_map
    import rr_ingame_trades as parser
    rules = importlib.import_module("gen3_clause_rows")
    if not os.environ.get("SLINK_GEN3_ROMS"):
        pytest.skip("own-title ROM source checks require SLINK_GEN3_ROMS")
    rom = rules.source_rom(title)
    groups = gba_map.resolve_groups_addr(ROOT / f"data/gen3/pret/poke{title}.sym")
    monkeypatch.setattr(parser, "GMAPGROUPS", groups)
    g, n, x, y, flag, face = rules.BALL_PICKUPS[title]
    objects, _ = parser.parse_map_object_events(rom, g, n)
    obj, = [o for o in objects if o["flag_id"] == flag]
    step = {"Up": (0, -1), "Right": (1, 0)}[face]
    assert (obj["x"], obj["y"]) == (x + step[0], y + step[1])
    at = obj["script"] - 0x08000000
    assert rom[at:at + 13].hex() == "1a008004001a01800100090102"  # finditem POKE_BALL,1; end
    geom = gba_map.Rom(rom, groups, "emerald" if title == "emerald" else "fr").map(g, n)
    other = (x + 1, y) if title == "emerald" else (x - 1, y)
    assert geom.collision[y][x] == geom.collision[other[1]][other[0]] == 0
    assert geom.bfs((x, y), other) == ["Right" if title == "emerald" else "Left"]
    if title != "emerald":
        assert geom.behaviour[y][x] == geom.behaviour[other[1]][other[0]] == 2
    else:
        assert rules.wild_facts("emerald", "rusturf_tunnel")


def test_rr_fact_binding_rejects_changed_pointer_and_never_uses_vanilla():
    rules = importlib.import_module("gen3_clause_rows")
    if not os.environ.get("SLINK_GEN3_ROMS"):
        pytest.skip("own-title ROM source checks require SLINK_GEN3_ROMS")
    rr = bytearray(rules.source_rom("radical_red"))
    facts = rules.species_facts(rr, "radical_red")
    assert facts["453"]["types"] == [0, 11]  # RR Bibarel: Normal/Water, absent from vanilla IDs
    rr[0x42F6C] ^= 1
    with pytest.raises(ValueError, match="evolution pointer"):
        rules.species_facts(rr, "radical_red")
    with pytest.raises(ValueError, match="anchor"):
        rules.species_facts(rules.source_rom("firered"), "radical_red")


def test_emerald_rule_seeds_are_disclosed_and_not_native_resave_evidence():
    import gen3_fixtures as fx

    from server.adapters import gen3_codec as c
    if not os.environ.get("SLINK_GEN3_ROMS"):
        pytest.skip("own-title ROM source checks require SLINK_GEN3_ROMS")
    flags = fx.emerald_new_game_flags(fx.pret_emerald())
    for kind in fx.EMERALD_RULE_KINDS:
        body = fx.build_emerald_seed(kind, flags)
        assert fx.qualify_one(body, rr=False, title="emerald")["ok"]
        assert any("not a game re-save" in p for p in fx.emerald_fixture_problems(body, kind))
        parsed = c.parse_flash(body, title="emerald")
        if kind == "ball_gate":
            assert duo.gen3_ball_count(body, "emerald") == 0
            assert struct.unpack_from("<bbbxhh", parsed["sb1"], 0xC) == (24, 4, -1, 3, 2)


def test_species_carrier_runs_from_the_encounter_it_observed():
    from lupa import LuaRuntime
    lua = LuaRuntime(unpack_returned_tuples=True)
    scenario = lua.execute("return dofile('lua/tests/duo/scenario_gen3_clause.lua')")
    ctx = lua.execute('''
      local c={player='b',D={rule_kind='species',clause_facts={['16']={family=16},['19']={family=19}}},n=0,
               lines={}, messages={}, catches=0, runs=0}
      c.wait_go=function() return true end
      c.go_value=function() return {key='A',species_id=16,area_id='route_1'} end
      c.partner_done=function() return false end
      c.wait_until=function(p) return p() end
      c.rx_count=function() return #c.messages end
      c.rx_after=function(at,p) for i=at+1,#c.messages do if p(c.messages[i]) then return c.messages[i] end end end
      c.hunt=function() c.n=c.n+1;return true end
      c.wild_ready=function() return true end
      c.enemy_species=function() return c.n==1 and 16 or 19 end
      c.run_away=function() c.runs=c.runs+1;c.messages[#c.messages+1]={cmd='gui_prompt',text='Dupes clause: Pidgey -- reroll!'};return true end
      c.catch=function(label,ready) assert(ready==true,'second hunt would discard the observed foe');c.catches=c.catches+1;c.messages[#c.messages+1]={cmd='msgbox',text='A and B linked!'};return 'B' end
      c.last_sent=function() return {key='B',species_id=19,area_id='route_1'} end
      c.received=function() return 0 end
      c.observe_returned=function() return true end
      c.jlog=function(tag,value) c.lines[#c.lines+1]={tag=tag,value=value} end
      c.log=function() end
      c.save=function() return true end
      return c
    ''')
    assert scenario(ctx) is True
    assert (ctx["n"], ctx["runs"], ctx["catches"]) == (2, 1, 1)
    assert ctx["lines"][1]["tag"] == "B_PRE_ENCOUNTER"
    assert [ctx["lines"][i]["tag"] for i in range(1, len(ctx["lines"]) + 1)].count("CLAUSE_REROLL") == 1


def test_species_a_holds_first_battle_until_runner_matches_family():
    from lupa import LuaRuntime
    lua = LuaRuntime(unpack_returned_tuples=True)
    scenario = lua.execute("return dofile('lua/tests/duo/scenario_gen3_clause.lua')")
    ctx = lua.execute('''
      local c={player='a',D={rule_kind='species',clause_facts={['16']={family=16}}},hunts=0,catches=0,runs=0}
      c.wait_go=function(marker) return marker~='PREFLIGHT_MATCH' end
      c.go_value=function() return {species=16,family=16} end
      c.partner_done=function() return false end
      c.wait_until=function(p) return p() end
      c.hunt=function() c.hunts=c.hunts+1;return true end
      c.wild_ready=function() return true end
      c.enemy_species=function() return 16 end
      c.jlog=function() end
      c.log=function() end
      c.catch=function() c.catches=c.catches+1;return 'A' end
      c.run_away=function() c.runs=c.runs+1;return true end
      return c
    ''')
    ok, why = scenario(ctx)
    assert ok is False and why == "species preflight match not released"
    assert (ctx["hunts"], ctx["runs"], ctx["catches"]) == (1, 0, 0)


def test_rule_rng_failures_keep_final_failures_final():
    receipts = {"a": "RESULT: FAIL (no gender-clause verdict (partner-gone))\n",
                "b": "RESULT: FAIL (hunt ended out-of-balls)\n"}
    assert duo.retryable_gen1_rng("gen3_frlg", receipts, 1, 3)
    receipts["a"] = "RESULT: FAIL (rejection not read back boxed)\n"
    assert not duo.retryable_gen1_rng("gen3_frlg", receipts, 1, 3)


def _release_case(monkeypatch, tmp_path):
    from test_e2e_duo_gen3 import PIDGEY, PIDGEY_B, STARTER, _fixture, _key, _oracle_stub, _saved
    rules = importlib.import_module("gen3_clause_rows")
    fa, fb = _fixture([STARTER, PIDGEY]), _fixture([STARTER, PIDGEY_B])
    ka, kb = _key(PIDGEY), _key(PIDGEY_B)
    saved = {"a": _saved(fa, 3, [STARTER]), "b": _saved(fb, 3, [STARTER], {(13, 0): PIDGEY_B})}
    run, notes = _oracle_stub(monkeypatch, tmp_path, "release_gen3", saved, fa,
        [{"a": {"key": ka}, "b": {"key": kb}, "status": "memorial", "cause": "release"}])
    run._gen3_fixture_bytes = lambda i: fa if i == "a" else fb
    results = {"a": f"TX party_to_box {ka} {{}}\nTX box_to_party {ka} {{}}\nTX party_to_box {ka} {{}}\nSECOND_DEPOSITED {ka}\nTX release {ka} {{}}\nRELEASED {ka}\n",
               "b": f"MIRROR_DEPOSITED {kb}\nMIRROR_WITHDRAWN {kb}\nMIRROR_SECOND_DEPOSITED {kb}\nRX force_faint key={kb}\nRX memorialize key={kb}\nTX memorialize_done {kb} {{}}\n"}
    preimage = "RELEASE_PREIMAGE " + json.dumps({"key": ka, "source": {"where": "box", "box": 0, "slot": 0}}) + "\n"
    results["a"] = results["a"].replace(f"TX release {ka}", preimage + f"TX release {ka}")
    return rules, run, results, notes


def test_release_oracle_accepts_native_boxed_partner_memorial_without_party_hp(monkeypatch, tmp_path):
    rules, run, results, notes = _release_case(monkeypatch, tmp_path)
    rules.release_oracle(run, results)
    assert "boxed partner" in notes[-1]


def test_release_oracle_requires_boxed_native_preimage(monkeypatch, tmp_path):
    rules, run, results, _ = _release_case(monkeypatch, tmp_path)
    results["a"] = results["a"].replace('"where": "box"', '"where": "party"')
    with pytest.raises(RuntimeError, match="boxed pre-removal"):
        rules.release_oracle(run, results)


def test_release_oracle_requires_the_second_native_deposit_tx(monkeypatch, tmp_path):
    rules, run, results, _ = _release_case(monkeypatch, tmp_path)
    line = results["a"].splitlines()[2] + "\n"
    at = results["a"].rfind(line)
    results["a"] = results["a"][:at] + results["a"][at + len(line):]
    with pytest.raises(RuntimeError, match="deposit"):
        rules.release_oracle(run, results)


def _ball_case(monkeypatch, tmp_path):
    from test_e2e_duo_gen3 import PIDGEY, STARTER, _fixture, _key, _mon, _oracle_stub, _saved

    from server.adapters import gen3_codec as c
    rules = importlib.import_module("gen3_clause_rows")
    starter = dict(STARTER, hp=1)
    fixture = _fixture([starter, PIDGEY], balls=0)
    catches = {"a": _mon(0x11223344, species=10, level=3), "b": _mon(0x55667788, species=13, level=3)}
    keys, saved = {i: _key(m) for i, m in catches.items()}, {}
    for i, mon in catches.items():
        image = _saved(fixture, 3, [dict(starter, hp=0), PIDGEY, mon], balls=0)
        parsed = c.parse_flash(image)
        sb1 = bytearray(parsed["sb1"])
        sb1[0xEE0 + 342 // 8] |= 1 << (342 % 8)
        entry = next(e for e in c.slot_layout() if e["object"] == "sb1" and e["offset"] == 0)
        sector = next(s for s in parsed["sectors"][14:28] if s["id"] == entry["id"])
        base = sector["index"] * c.SECTOR_SIZE
        modified = bytearray(image)
        modified[base:base + c.SECTOR_SIZE] = c.write_sector(sb1[:entry["size"]], entry["id"], 3, c.slot_layout())
        saved[i] = bytes(modified)
    run, notes = _oracle_stub(monkeypatch, tmp_path, "ball_gate_gen3", saved, fixture,
        [{"a": {"key": keys["a"]}, "b": {"key": keys["b"]}, "status": "alive", "area_id": "viridian_forest"}])
    run._link_keys = keys
    run._ball_pre_status = {"players": {i: {"nuzlocke_active": False, "ball_count": 0} for i in ("a", "b")}}
    run.cfg = dict(run.cfg, post_flip_stock=False)  # this seam exercises the single-phase oracle controls
    run._status = lambda: {"players": {i: {"nuzlocke_active": True, "ball_count": 1} for i in ("a", "b")}}
    run._reconnect_events = lambda: []
    run._reconnect_document = lambda: {"pokeballs_obtained": {"a": True, "b": True}}
    lead = _key(starter)
    (tmp_path / "slink.log").write_text(f"[a] faint key={lead}\n[b] faint key={lead}\n", encoding="utf-8")
    receipt = (f'TX faint {lead} {{}}\nBALL_NATIVE_HP0 {lead} frame=9 in_battle=1 battler=0\n'
               'BALL_PRE_ENCOUNTER species=10 outcome=4\n'
               + 'BALL_FAINT ' + json.dumps({"key": lead, "hp": 0, "sent": 1,
                    "site": {"battler0_slot": 0, "party_hp": 0, "counter": 1}}) + '\n'
               'BALL_PRE {"balls":0,"active":false,"attempted":0}\n'
               'BALL_PICKUP {"flag":342,"before":false,"after":true}\n'
               'BALL_FLIP {"balls":1,"active":true}\n')
    results = {}
    for i, mon in catches.items():
        cap = {"key": keys[i], "species_id": mon["species"], "level": 3, "area_id": "viridian_forest"}
        results[i] = receipt + f"THREW 1\nTX capture {keys[i]} " + json.dumps(cap) + "\nBALL_POST_CATCH " + json.dumps({"key": keys[i]}) + "\n"
    return rules, run, results, notes


def test_ball_gate_requires_the_native_reward_flag_and_saved_ball_count(monkeypatch, tmp_path):
    rules, run, results, notes = _ball_case(monkeypatch, tmp_path)
    rules.ball_gate_oracle(run, results)
    assert "post-ball real catch/link saved" in notes[-1]


def test_ball_gate_cannot_pass_a_server_that_was_already_active(monkeypatch, tmp_path):
    rules, run, results, _ = _ball_case(monkeypatch, tmp_path)
    run._ball_pre_status["players"]["a"]["nuzlocke_active"] = True
    with pytest.raises(RuntimeError, match="pre-ball server"):
        rules.ball_gate_oracle(run, results)


@pytest.mark.parametrize("old,new", [('"before":false', '"before":true'),
                                    ('"balls":1', '"balls":2'), ('outcome=4', 'outcome=1'),
                                    ('BALL_PRE_ENCOUNTER', 'NO_PRE_ENCOUNTER')])
def test_ball_gate_rejects_weak_acquisition_and_preball_receipts(monkeypatch, tmp_path, old, new):
    rules, run, results, _ = _ball_case(monkeypatch, tmp_path)
    results["a"] = results["a"].replace(old, new)
    with pytest.raises(RuntimeError):
        rules.ball_gate_oracle(run, results)


def test_gender_clause_uses_the_pid_and_this_titles_ratio(monkeypatch, tmp_path):
    rules, run, results, _, _ = _type_case(monkeypatch, tmp_path)
    run.cfg["rule_kind"] = "gender"
    # Both PIDs end in 0x44 (<127). The actual server prompt must say female.
    results["b"] = results["b"].replace("Type clause: shared Normal", "Gender clause: both are " + "\\u2640")
    rules.clause_oracle(run, results)
    run._clause_facts["b"]["19"]["gender_ratio"] = 0  # this title now says male
    with pytest.raises(RuntimeError, match="do not justify rejection"):
        rules.clause_oracle(run, results)


def test_family_oracle_needs_distinct_related_species_and_a_native_reroll(monkeypatch, tmp_path):
    from test_e2e_duo_gen3 import PIDGEY, STARTER, _fixture, _key, _oracle_stub, _saved
    rules = importlib.import_module("gen3_clause_rows")
    evolved = dict(STARTER, species=17)
    key = _key(evolved)
    fixture = _fixture([evolved, PIDGEY])
    saved = _saved(fixture, 3, [evolved, PIDGEY])
    run, _ = _oracle_stub(monkeypatch, tmp_path, "species_family_gen3", {"a": saved, "b": saved}, fixture,
        [{"a": {"key": key, "species": 17}, "b": {"key": key, "species": 17}, "status": "alive"}])
    run._link_keys = {"a": key, "b": key}
    run._clause_facts = {"a": {"16": {"family": 16}, "17": {"family": 16}, "19": {"family": 19}}}
    run._reconnect_events = lambda: [{"type": "reroll"}]
    receipt = ("FAMILY_ENCOUNTER " + json.dumps({"key": key, "owned": 17, "player": "a", "species": 16, "related": True}) + "\n"
               'RULE_RX {"cmd":"gui_prompt","text":"Dupes clause: Pidgey -- reroll!"}\n'
               'CLAUSE_REROLL {"species":16,"prompt":"Dupes clause: Pidgey -- reroll!"}\n')
    rules.family_oracle(run, {"a": receipt, "b": ""})
    with pytest.raises(RuntimeError, match="CLAUSE_REROLL"):
        rules.family_oracle(run, {"a": receipt.replace("CLAUSE_REROLL", "NOT_A_REROLL"), "b": ""})
    with pytest.raises(rules.ClauseUnobserved):
        rules.family_oracle(run, {"a": receipt.replace('"species": 16', '"species": 19').replace('"related": true', '"related": false'), "b": ""})


def test_exhausted_unobserved_clause_never_becomes_a_pass(monkeypatch):
    from types import SimpleNamespace
    rules = importlib.import_module("gen3_clause_rows")
    calls = []
    class Lane:
        def __init__(self, name, args, attempt):
            calls.append(attempt)
        def run(self):
            raise rules.ClauseUnobserved("species reroll unobserved")
    monkeypatch.setattr(duo, "DuoRun", Lane)
    monkeypatch.setattr(duo, "_archive_attempt", lambda *a: None)
    monkeypatch.setattr(duo, "read_result", lambda *a: "RESULT: PASS\n")
    result = duo.run_scenario_with_rng_retry("species_clause_gen3", SimpleNamespace(game="gen3_frlg", idle_jitter=0))
    assert result[:2] == (False, 8)
    assert calls == list(range(1, 9))


def test_species_preflight_orders_b_then_a_and_aborts_mismatch_before_capture():
    from types import SimpleNamespace
    rules = importlib.import_module("gen3_clause_rows")
    order = []
    def mark(side, pattern, what, timeout=None):
        order.append(("mark", side, what))
        if "B preflight" in what:
            return re.match(r"(.*)", json.dumps({"species": 16, "family": 16}))
        if "A preflight" in what:
            return re.match(r"(.*)", json.dumps({"species": 19, "family": 19}))
        raise AssertionError("capture was released on a mismatch")
    run = SimpleNamespace(cfg={"rule_kind": "species"}, _hunt_area="route_1",
        _gen3_prelude=lambda: order.append(("prelude",)), go=lambda: order.append(("go",)),
        _gen3_mark=mark, _append_reconnect_marker=lambda side, line: order.append(("release", side, line)),
        _read_receipt=lambda side: "", _gen3_title=lambda side: "firered",
        _clause_facts={"a": {"16": {"family": 16}, "19": {"family": 19}},
                       "b": {"16": {"family": 16}, "19": {"family": 19}}})
    with pytest.raises(rules.ClauseUnobserved, match="preflight"):
        rules._orchestrate_clause(run)
    assert order[:3] == [("prelude",), ("go",), ("mark", "b", "B preflight")]
    assert any(item[:2] == ("release", "a") for item in order)
    assert not any(item[0] == "release" and item[1] == "b" for item in order)


def test_held_b_battle_a_real_capture_then_b_no_catch_gets_server_reroll(tmp_path, monkeypatch):
    from server.state import AreaStatus, SoulLinkState

    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState(species_lock=True)
    state.pokeballs_obtained = {"a": True, "b": True}
    assert state.check_dupe_on_encounter("b", "route_1", 16) is False
    # A's concurrent-battle prompt is informational; it does not block the native catch.
    assert state.check_dupe_on_encounter("a", "route_1", 16, partner_battle_species=16)
    state.handle_event("a", {"event": "capture", "key": "A:16", "area_id": "route_1",
                             "level": 3, "species_id": 16})
    assert state.pending_captures["route_1"]["a"].key == "A:16"
    cmds = state.handle_event("b", {"event": "no_catch", "area_id": "route_1", "species_id": 16})
    assert state.area_states["route_1"] != AreaStatus.DEAD_ZONE
    assert any(c.get("cmd") == "gui_prompt" and "reroll!" in c.get("text", "") for c in cmds)


def test_species_match_releases_a_catch_before_b_pending_and_save():
    from types import SimpleNamespace
    rules = importlib.import_module("gen3_clause_rows")
    order = []
    cap = {"key": "A:16", "species_id": 16, "area_id": "route_1"}
    def mark(side, pattern, what, timeout=None):
        order.append(("mark", side, what))
        payload = ({"species": 16, "family": 16} if "preflight" in what else cap)
        return re.match(r"(.*)", json.dumps(payload))
    run = SimpleNamespace(cfg={"rule_kind": "species"}, _hunt_area="route_1",
        _gen3_prelude=lambda: None, go=lambda: order.append(("go",)), _gen3_mark=mark,
        _append_reconnect_marker=lambda side, line: order.append(("release", side, line)),
        _read_receipt=lambda side: "", _gen3_title=lambda side: "firered",
        _clause_facts={i: {"16": {"family": 16}} for i in "ab"},
        _status=lambda: {"pending_captures": {"route_1": {"a": {"key": "A:16", "species": 16}}}},
        wait_for=lambda name, pred, timeout: pred())
    rules._orchestrate_clause(run)
    releases = [(i, item[1], item[2]) for i, item in enumerate(order) if item[0] == "release"]
    match = next(i for i, side, line in releases if side == "a" and line == "PREFLIGHT_MATCH")
    pending = next(i for i, side, line in releases if side == "b" and line.startswith("A_PENDING "))
    assert match < pending
    assert order[-2:] == [("release", "a", "SAVE"), ("release", "b", "SAVE")]


def test_species_preflight_oracle_requires_native_order_and_matching_rom_families():
    rules = importlib.import_module("gen3_clause_rows")
    facts = {i: {"16": {"family": 16}, "17": {"family": 16},
                 "19": {"family": 19}} for i in "ab"}
    tag = lambda name, row: name + " " + json.dumps(row) + "\n"
    receipts = {
        "a": tag("A_PRE_ENCOUNTER", {"species": 16, "family": 16}) +
             "PREFLIGHT_MATCH\n" + tag("CLAUSE_ENCOUNTER", {"n": 1, "species": 16}) +
             "TX capture A:16 {}\n" + tag("PENDING_CAPTURE", {"species_id": 16}),
        "b": tag("B_PRE_ENCOUNTER", {"species": 16, "family": 16}) +
             tag("A_PENDING", {"species_id": 16}) + tag("CLAUSE_ENCOUNTER", {"n": 1, "species": 16}) +
             "TX no_catch - {}\n" + tag("CLAUSE_REROLL", {"n": 1, "species": 16}),
    }
    assert rules.species_preflight_problems(receipts, facts) == []
    wrong = dict(receipts, b=receipts["b"].replace('"family": 16', '"family": 19', 1))
    assert rules.species_preflight_problems(wrong, facts)
    missing = dict(receipts, b=receipts["b"].replace("TX no_catch - {}\n", ""))
    assert rules.species_preflight_problems(missing, facts)
    same_family_other_foe = dict(receipts, b=receipts["b"].replace(
        '"n": 1, "species": 16', '"n": 1, "species": 17', 1))
    assert any("B preflight species" in p for p in rules.species_preflight_problems(same_family_other_foe, facts))
    same_family_other_a_catch = dict(receipts, a=receipts["a"].replace(
        '"species_id": 16', '"species_id": 17', 1))
    assert any("A preflight species" in p for p in rules.species_preflight_problems(same_family_other_a_catch, facts))
    same_family_other_a_foe = dict(receipts, a=receipts["a"].replace(
        '"n": 1, "species": 16', '"n": 1, "species": 17', 1))
    assert any("A preflight species" in p for p in rules.species_preflight_problems(same_family_other_a_foe, facts))


def test_unknown_preflight_rom_fact_is_hard_not_rng_retry():
    from types import SimpleNamespace
    rules = importlib.import_module("gen3_clause_rows")
    run = SimpleNamespace(cfg={"rule_kind": "species"}, _hunt_area="route_1",
        _gen3_prelude=lambda: None, go=lambda: None,
        _gen3_mark=lambda *args: re.match(r"(.*)", json.dumps({"species": 999999, "family": 1})),
        _read_receipt=lambda side: "", _gen3_title=lambda side: "firered",
        _clause_facts={i: {"16": {"family": 16}} for i in "ab"})
    with pytest.raises(RuntimeError, match="booted-ROM facts") as caught:
        rules._orchestrate_clause(run)
    assert not isinstance(caught.value, rules.ClauseUnobserved)


def test_script_entrypoint_uses_its_own_rng_exception_classes():
    from types import SimpleNamespace
    rules = importlib.import_module("gen3_clause_rows")
    # `python tools/e2e_duo.py` defines these in __main__, which is a different
    # module/class identity from a later `import e2e_duo` in an oracle helper.
    class RunningHostEarly(RuntimeError):
        pass
    class RunningHostRng(RuntimeError):
        pass
    def early():
        raise RunningHostEarly("partner ended")
    run = SimpleNamespace(_gen3_prelude=early, _read_receipt=lambda i: "RESULT: FAIL (hunt ended out-of-balls)\n")
    host = SimpleNamespace(ClientFinishedEarly=RunningHostEarly, GameRngMiss=RunningHostRng,
                           _has_exact_rng_miss=duo._has_exact_rng_miss)
    with pytest.raises(RunningHostRng):
        rules.orchestrate_clause(run, helpers=host)
    run._read_receipt = lambda i: "RESULT: FAIL (memorial readback missing)\n"
    with pytest.raises(RunningHostEarly):
        rules.orchestrate_clause(run, helpers=host)


def test_ball_gate_requires_native_preball_faint_and_postball_link(monkeypatch, tmp_path):
    rules, run, results, _ = _ball_case(monkeypatch, tmp_path)
    # The earlier encounter/activation-only receipt is no longer a complete row.
    results["a"] = results["a"].replace("BALL_FAINT ", "MISSING_FAINT ")
    with pytest.raises(RuntimeError, match="BALL_FAINT|capture"):
        rules.ball_gate_oracle(run, results)


@pytest.mark.parametrize("old,new", [("BALL_NATIVE_HP0", "NO_NATIVE_HP0"),
                                    ('"counter": 1', '"counter": 0'),
                                    ("TX faint", "NO_FAINT_TX"),
                                    ("BALL_POST_CATCH", "NO_POST_CATCH"),
                                    ("TX capture", "NO_CAPTURE_TX")])
def test_complete_gate_refuses_missing_native_faint_or_post_activation_catch(monkeypatch, tmp_path, old, new):
    rules, run, results, _ = _ball_case(monkeypatch, tmp_path)
    results["a"] = results["a"].replace(old, new)
    with pytest.raises(RuntimeError):
        rules.ball_gate_oracle(run, results)


def test_complete_gate_rejects_a_soul_link_death_for_the_preball_faint(monkeypatch, tmp_path):
    rules, run, results, _ = _ball_case(monkeypatch, tmp_path)
    with (tmp_path / "slink.log").open("a", encoding="utf-8") as f:
        f.write("faint → force_faint\n")
    with pytest.raises(RuntimeError, match="suppress"):
        rules.ball_gate_oracle(run, results)


@pytest.mark.parametrize("title", ["firered", "leafgreen", "emerald", "radical_red"])
def test_complete_gate_setup_has_zero_balls_hp1_lead_and_fast_healthy_reserve(title):
    import gen3_fixtures as fx

    from server.adapters import gen3_codec as c
    if not os.environ.get("SLINK_GEN3_ROMS"):
        pytest.skip("own-title ROM source checks require SLINK_GEN3_ROMS")
    if title == "emerald":
        body = fx.build_emerald_seed("ball_gate", fx.emerald_new_game_flags(fx.pret_emerald()))
    elif title == "radical_red":
        seed = (ROOT / "tests/fixtures/gen3/rr_battle.sav").read_bytes()
        body, manifest = fx.build_rr_synth(seed, "ball_gate")
        before, after = c.parse_flash(seed, cfru=True), c.parse_flash(body, cfru=True)
        assert before["sb1"][0xEE0:0x1100] == after["sb1"][0xEE0:0x1100]  # story flags/vars
        assert before["sb2"] == after["sb2"] and before["storage"] == after["storage"]
        assert manifest and "pre-parcel story/pocket untouched" in manifest[0]
    else:
        seed = (ROOT / f"tests/fixtures/gen3/{title}_party_battle.sav").read_bytes()
        body, _ = fx.build_frlg_synth(seed, "ball_gate", title=title)
    party, _ = duo.gen3_decode(body, rr=title == "radical_red", title=duo.gen3_codec_title(title))
    assert len(party) == 2 and (party[0]["hp"], party[0]["status"]) == (1, 0)
    assert party[1]["level"] == 25 and party[1]["hp"] == party[1]["max_hp"] > 0
    assert duo.gen3_ball_count(body, title) == 0
    assert fx.qualify_one(body, rr=title == "radical_red", title=duo.gen3_codec_title(title))["ok"]


@pytest.mark.parametrize("title,stock_phase,phase", [
    (t, False, "initial") for t in ("firered", "leafgreen", "emerald", "radical_red")
] + [(t, True, p) for t in ("firered", "leafgreen", "emerald") for p in ("initial", "post_flip")])
def test_ball_carrier_requires_native_faint_before_reward_and_catches_after_activation(title, stock_phase, phase):
    from lupa import LuaRuntime
    lua = LuaRuntime(unpack_returned_tuples=True)
    scenario = lua.execute("return dofile('lua/tests/duo/scenario_gen3_ball_gate.lua')")
    ctx = lua.execute('''
      local c={player='a',D={wt='.'},cp={},session={state={has_pokeballs=false}},
               stock=0,inside=false,dead=false,caught=false,flag=false,lines={},catch_calls=0}
      c.wait_go=function(mark)
        if mark=='CAPTURE' or c.D.phase=='post_flip' then c.released=true end
        if mark=='SAVE_GATE' then c.save_gate=true end
        return true
      end
      c.balls=function() return c.stock end
      c.game_flag=function() return c.flag end
      c.attempted=function() return 0 end
      c.party=function()
        local p={{key='LEAD',slot=0,hp=c.dead and 0 or 1,species=7},{key='RESERVE',slot=1,hp=40,species=16}}
        if c.caught then p[3]={key='CAUGHT',slot=2,hp=10,species=10} end
        return p
      end
      c.hunt=function() c.inside=true;return true end
      c.in_battle=function() return c.inside end
      c.wild_ready=function() return true end
      c.enemy_species=function() return 10 end
      c.lose_active=function(key) assert(c.stock==0 and key=='LEAD');c.dead=true;return true end
      c.faint_sites=function() return {{battler0_slot=0,party_hp=0,counter=1}} end
      c.hp0=function() return {in_battle=true} end
      c.wait_sent=function() return true end
      c.sent=function(event) return event=='faint' and c.dead and 1 or 0 end
      c.received=function() return 0 end
      c.await_turn=function() return 'party' end
      c.send_out=function(slot) assert(c.dead and slot==1);return true end
      c.run_away=function() c.inside=false;return true end
      c.battle_outcome=function() return 4 end
      c.log=function(s) c.lines[#c.lines+1]=s end
      c.jlog=function(tag,v) c.lines[#c.lines+1]=tag;c[tag]=v end
      c.fmt=string.format
      c.frames=function() end
      c.wait_until=function(p) return p() end
      c.partner_done=function() return true end
      c.catch=function(label,ready)
        assert(c.dead and c.released and c.session.state.has_pokeballs,'catch before activation')
        if c.title=='radical_red' then assert(ready==true,'must catch the return-route encounter') end
        c.catch_calls=c.catch_calls+1;c.caught=true;return 'CAUGHT'
      end
      c.find=function(key) if key=='CAUGHT' and c.caught then return {key=key,slot=2} end end
      c.save=function()
        if c.D.ball_stock_phase and c.D.phase=='initial' then
          assert(c.save_gate and c.stock==1 and not c.caught);c.native_phase_saved=true
        else assert(c.caught) end
        return true,'saved'
      end
      c.fail=function(why) error(why) end
      c.G={pos=function() return c.title=='emerald' and 3 or 4,2 end,
           tap=function() error('unverified facing tap after an unfinished step') end}
      c.face=function(direction) c.faced=direction;return true end
      c.play={step=function() c.inside=true;return true end,wait_scene_settled=function() return true end}
      c.flee_incidentals=function(_,fn)
        c.escaping=true;local ok,why=pcall(fn);c.escaping=false;return ok,why
      end
      c.SP={return_to_grass_origin=function()
        assert(c.escaping,'dead-lead return to grass must not use incidental FIGHT')
      end}
      c.mash_until=function(p) c.stock=1;c.flag=true;c.session.state.has_pokeballs=true;return p() end
      local real=dofile
      dofile=function(path)
        if path:find('gen3_rr_battle_fixture.lua',1,true) then
          return {native_ball_gift=function(cp,reward,battle,flee)
            assert(type(flee)=='function','duo parcel walk must use the proved RUN driver before its reward')
            c.inside=true;assert(flee());assert(not c.inside and c.stock==0)
            c.stock=10;c.flag=true;c.session.state.has_pokeballs=true
            reward();assert(c.released);battle();battle();return true
          end}
        end
        return real(path)
      end
      return c
    ''')
    ctx["title"] = title
    ctx.D.ball_stock_phase, ctx.D.phase = stock_phase, phase
    if phase == "post_flip":
        ctx.stock, ctx.dead, ctx.flag, ctx.session.state.has_pokeballs = 20, True, True, True
    assert scenario(ctx)[0] is True
    if stock_phase and phase == "initial":
        assert ctx.native_phase_saved is True and ctx.catch_calls == 0
        return
    if phase == "post_flip":
        assert ctx.BALL_STOCK_READY.balls == 20 and ctx.catch_calls == 1
        return
    assert ctx["catch_calls"] == 1 and ctx["BALL_FAINT"]["key"] == "LEAD"
    tags = [ctx["lines"][i] for i in range(1, len(ctx["lines"]) + 1)]
    assert tags.index("BALL_FAINT") < tags.index("BALL_PRE") < tags.index("BALL_FLIP") < tags.index("BALL_POST_CATCH")


def test_rr_duo_reuses_the_admitted_checkpoint_instead_of_reloading_environment():
    from lupa import LuaRuntime
    source = (ROOT / "lua/tests/gen3_rr_battle_fixture.lua").read_text(encoding="utf-8")
    start = source.index("local function run_route2(")
    prefix = source[start:source.index("    local domain, seen", start)]
    lua = LuaRuntime(unpack_returned_tuples=True)
    lua.execute('''G={open=function() error('duo must not reopen the standalone receipt') end,
                         phase=function() end, checkpoint=function() error('stale environment checkpoint') end}
                   client={speedmode=function() end}''')
    call = lua.execute(prefix + "return cp, title, G.title, duo_route_battle end; return run_route2")
    cp = lua.table_from({"proof": "already admitted by the production driver"})
    flee = lua.eval("function() return 'native RUN delegate' end")
    returned, title, helper_title, bound = call(cp, lua.table_from({"flee": flee}))
    assert returned["proof"] == cp["proof"] and title == helper_title == "radical_red"
    assert bound() == "native RUN delegate"
