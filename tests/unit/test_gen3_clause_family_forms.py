"""C3: RR's real night encounter must match an evolved linked regional form."""
import json
import os
import struct
import sys
from pathlib import Path

import pytest
from lupa import LuaRuntime

from server.adapters.gen3_frlge import Gen3Adapter
from server.state import AreaStatus, LinkEntry, LinkStatus, MonInfo, SoulLinkState

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))


def test_rr_galar_zigzagoon_chain_is_related_and_separate_from_the_base_chain():
    adapter = Gen3Adapter(is_rr=True)
    family = adapter.evo_family(1222)
    assert adapter.evo_family(1223) == family
    assert adapter.evo_family(1154) == family
    assert adapter.evo_family(288) == adapter.evo_family(289) != family


def test_rr_native_wild_species_outside_the_name_catalog_keeps_its_evolution():
    adapter = Gen3Adapter(is_rr=True)
    assert adapter.evo_family(1361) == adapter.evo_family(1362)


@pytest.mark.parametrize("side", ["a", "b"])
@pytest.mark.parametrize("battle_start", [False, True])
def test_rr_native_night_species_rerolls_against_either_evolved_link_half(
    monkeypatch, tmp_path, side, battle_start,
):
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState(adapter=Gen3Adapter(is_rr=True), species_lock=True)
    state.pokeballs_obtained = {"a": True, "b": True}
    mons = {"a": MonInfo(key="AA:11", species=289), "b": MonInfo(key="BB:22", species=1223)}
    state.links.append(LinkEntry(area_id="duo", a=mons["a"], b=mons["b"], status=LinkStatus.ALIVE))
    state._save()
    if battle_start:
        assert state.check_dupe_on_encounter(side, "route_1", 1222) is True
    cmds = state.handle_event(side, {"event": "no_catch", "area_id": "route_1", "species_id": 1222})
    assert state.area_states.get("route_1", AreaStatus.UNSEEN) != AreaStatus.DEAD_ZONE
    assert any(c.get("cmd") == "unresolve_area" and c.get("area_id") == "route_1" for c in cmds)
    assert any(c.get("cmd") == "gui_prompt" and "reroll" in c.get("text", "") for c in cmds)


def test_base_linoone_alone_does_not_qualify_a_galarian_encounter(monkeypatch, tmp_path):
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState(
        adapter=Gen3Adapter(is_rr=True), species_lock=True, retry_same_species=False)
    state.pokeballs_obtained = {"a": True, "b": True}
    state.links.append(LinkEntry(area_id="duo", a=MonInfo(key="AA:11", species=289),
                                b=MonInfo(key="BB:22", species=16), status=LinkStatus.ALIVE))
    state._save()
    assert state.check_dupe_on_encounter("a", "route_1", 1222) is False
    state.handle_event("a", {"event": "no_catch", "area_id": "route_1", "species_id": 1222})
    assert state.area_states["route_1"] == AreaStatus.DEAD_ZONE


def test_rr_family_generator_checks_every_rom_edge_and_the_committed_artifact():
    from tools import gen_rr_evolutions as gen
    if not os.environ.get("SLINK_GEN3_ROMS"):
        pytest.skip("requires the admitted own RR ROM")
    rom = (Path(os.environ["SLINK_GEN3_ROMS"]) / "Pokemon - Radical Red.gba").read_bytes()
    catalog = (ROOT / "data/games/gen3_frlge/rr_species.json").read_bytes()
    generated = gen.generate(rom, catalog)
    assert generated == json.loads(gen.OUTPUT.read_text())
    known = set(map(int, json.loads(catalog)))
    # Naming these ROM-proven species later must not change their graph coverage.
    assert generated["wild_ids_outside_catalog"] == sorted({1356, 1361, 1362, 1371} - known)
    assert generated["reachable_ids_outside_catalog"] == sorted({1356, 1361, 1362, 1363, 1369, 1371} - known)
    assert {"1361", "1362", "1363", "1369", "1371"} <= generated["families"].keys()
    adapter = Gen3Adapter(is_rr=True)
    for sid in map(int, generated["families"]):
        for slot in range(16):
            method, _, target = struct.unpack_from("<HHH", rom, 0x017CD9B0 + (sid * 16 + slot) * 8)
            if method:
                assert adapter.evo_family(sid) == adapter.evo_family(target), (sid, slot, target)
        assert adapter.evo_family(adapter.evo_family(sid)) == adapter.evo_family(sid)
    # Two independent regional chains, plus a shared-base regional branch.
    assert adapter.evo_family(1020) == adapter.evo_family(1021) != adapter.evo_family(19)
    assert adapter.evo_family(1208) == adapter.evo_family(1155) != adapter.evo_family(52)
    assert adapter.evo_family(1022) == adapter.evo_family(25)
    with pytest.raises(ValueError, match="pinned clean"):
        gen.generate(bytes([rom[0] ^ 1]) + rom[1:], catalog)


def test_rr_family_graph_handles_branches_and_cycles():
    from tools.gen_rr_evolutions import graph_families
    families = graph_families({1, 2, 3, 4, 5, 6}, [(3, 1), (3, 2), (4, 5), (5, 4)])
    assert families == {1: 3, 2: 3, 3: 3, 4: 4, 5: 4, 6: 6}


def test_generic_family_table_remains_separate_from_rr():
    from server.pokemon_data import EVO_FAMILY, base_form
    for species in range(1, 1372):
        assert base_form(species, False) == EVO_FAMILY.get(species, species)


def test_generic_module_regeneration_keeps_the_rr_family_source():
    from tools.gen_pokemon_data import generate_module
    source = generate_module({1222: "Zigzagoon-Galar", 1223: "Linoone-Galar"}, {}, {1223: 1223})
    namespace = {"__file__": str(ROOT / "server/pokemon_data.py")}
    exec(compile(source, "generated_pokemon_data.py", "exec"), namespace)
    assert namespace["base_form"](1223, True) == namespace["base_form"](1222, True)
    assert namespace["base_form"](1223, False) == 1223


@pytest.mark.parametrize("foe,owner,evolved", [(288, "a", 289), (1222, "b", 1223)])
def test_family_driver_binds_the_actual_native_foe_to_the_matching_link_half(foe, owner, evolved):
    lua = LuaRuntime(unpack_returned_tuples=True)
    scenario = lua.execute("return dofile('lua/tests/duo/scenario_gen3_species_family.lua')")
    ctx = lua.execute('''
      local c={player='a',D={clause_facts={['288']={family=288},['289']={family=288},
               ['1222']={family=1154},['1223']={family=1154}}}, lines={},messages={}}
      c.wait_go=function() return true end
      c.linked=function() return 'A' end
      c.go_value=function(tag) assert(tag=='FAMILY_LINKS');return {
        {player='a',key='A',species=289},{player='b',key='B',species=1223}} end
      c.find=function() return {key='A',species=289,slot=0} end
      c.rx_count=function() return #c.messages end
      c.rx_after=function(at,p) for i=at+1,#c.messages do if p(c.messages[i]) then return c.messages[i] end end end
      c.hunt=function() c.messages[#c.messages+1]={cmd='gui_prompt',text='Dupes clause: Zigzagoon -- reroll!'};return true end
      c.wild_ready=function() return true end
      c.run_away=function() return true end
      c.G={pos=function() return 13,38 end};c.play={map=function() return 787 end}
      c.wait_until=function(p) return p() end
      c.jlog=function(tag,value) c.lines[tag]=value end
      c.log=function() end;c.save=function() return true end
      return c
    ''')
    ctx.enemy_species = lambda: foe
    assert scenario(ctx) is True
    receipt = ctx.lines.FAMILY_ENCOUNTER
    assert (receipt.related, receipt.player, receipt.owned, receipt.key) == (True, owner, evolved, owner.upper())
    assert receipt.map == 3 * 256 + 19
    assert ctx.lines.CLAUSE_REROLL.species == foe


def test_rr_pair_seed_preserves_b_identity_and_has_its_own_evolved_night_family():
    from server.adapters import gen3_codec as c
    from tools import e2e_duo as h, gen3_clause_rows as rules, gen3_fixtures as fx
    if not os.environ.get("SLINK_GEN3_ROMS"):
        pytest.skip("requires the admitted own RR ROM")
    assert h.scenario_target(h.SCENARIOS["species_family_gen3"], "gen3_rr") == {
        "a": "family_synth", "b": "family_galar_synth"}
    seed = (ROOT / "tests/fixtures/gen3/rr_battle2_b.sav").read_bytes()
    body, manifest = fx.build_rr_synth(seed, "family_galar")
    before, after = c.party_from_save(seed, rr=True), c.party_from_save(body, rr=True)
    assert h.gen3_key(before[0]) == h.gen3_key(after[0])
    assert after[0]["species"] == 1223 and after[0]["level"] == 25
    assert before[1:] == after[1:] and fx.qualify_one(body, rr=True)["ok"]
    facts = rules.species_facts(rules.source_rom("radical_red"), "radical_red")
    assert rules.same_family(facts, 1223, 1222) and not rules.same_family(facts, 1223, 288)
    assert any("SYNTH" in line and "1222" in line for line in manifest)
    assert body == (ROOT / "tests/fixtures/gen3/rr_family_galar_synth_b.sav").read_bytes()


def _family_oracle_case(monkeypatch, tmp_path, foe):
    from tests.unit.test_e2e_duo_lane_isolation import _args
    from tools import e2e_duo as h, gen3_clause_rows as rules
    if not os.environ.get("SLINK_GEN3_ROMS"):
        pytest.skip("requires the admitted own RR ROM")
    run = h.DuoRun("species_family_gen3", _args(game="gen3_rr", scenario="species_family_gen3", lane="family-model"))
    run.emus = []
    run._gen3_flushed = run._gen3_fixture_bytes
    run._gen3_rom = lambda _: str(Path(os.environ["SLINK_GEN3_ROMS"]) / "Pokemon - Radical Red.gba")
    run._link_keys = {i: h.gen3_key(run._gen3_fixture_saved(i)[0][0]) for i in ("a", "b")}
    halves = {i: {"key": run._link_keys[i], "species": run._gen3_fixture_saved(i)[0][0]["species"]} for i in ("a", "b")}
    run._links_json = lambda: [{**halves, "status": "alive"}]
    run._reconnect_events = lambda: [{"type": "reroll"}]
    notes = []
    run._pydec_note = notes.append
    inst = "b" if foe == 1222 else "a"
    marker = {"key": halves[inst]["key"], "owned": halves[inst]["species"], "player": inst,
              "species": foe, "related": foe in (288, 1222), "map": 3 * 256 + 19, "x": 13, "y": 38}
    prompt = "Dupes clause: native species -- reroll!"
    def receipts(**updates):
        return {"a": "FAMILY_ENCOUNTER " + json.dumps({**marker, **updates}) + "\n"
                + "RULE_RX " + json.dumps({"cmd": "gui_prompt", "text": prompt}) + "\n"
                + "CLAUSE_REROLL " + json.dumps({"species": foe, "prompt": prompt}) + "\n", "b": ""}
    return rules, run, halves, notes, receipts


@pytest.mark.parametrize("foe", [288, 1222])
def test_family_oracle_accepts_rom_proven_day_or_night_against_actual_link_half(monkeypatch, tmp_path, foe):
    rules, run, halves, notes, receipts = _family_oracle_case(monkeypatch, tmp_path, foe)
    rules.family_oracle(run, receipts())
    assert any("rerolled" in line for line in notes)
    with pytest.raises(RuntimeError, match="staged cartridge record"):
        rules.family_oracle(run, receipts(key="OTHER:KEY"))
    halves["b"]["species"] = 16
    with pytest.raises(RuntimeError, match="saved link species"):
        rules.family_oracle(run, receipts())


def test_family_oracle_rejects_wrong_map_and_retains_unobserved_species(monkeypatch, tmp_path):
    rules, run, _, notes, receipts = _family_oracle_case(monkeypatch, tmp_path, 951)
    with pytest.raises(RuntimeError, match="Route 1"):
        rules.family_oracle(run, receipts(map=3 * 256 + 1))
    with pytest.raises(rules.ClauseUnobserved):
        rules.family_oracle(run, receipts())
    assert any('"species": 951' in line for line in notes)
