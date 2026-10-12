"""MODEL tests of the pure parts of tools/polished_live/duo.py (card C8): the two-side evaluator, the per-side
contract/path/env builders and the server-evidence parsers. No emulator, no server: the live run is the receipt."""
import copy
import importlib.util
import sys
from pathlib import Path
from urllib.error import URLError

import pytest

REPO = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("polished_duo", REPO / "tools/polished_live/duo.py")
duo = importlib.util.module_from_spec(_spec)
sys.modules["polished_duo"] = duo
_spec.loader.exec_module(duo)

SHA = duo.INTEGRATED_SHA1
KEYS = ["EFFFFF:D1C2:0A9:00", "DFFFFF:D1C2:087:00", "CFFFFF:D1C2:037:40"]


def hello(**kw):
    h = {"seen": True, "conn": 1, "admission": "admitted", "adapter": duo.ADAPTER, "artifact_kind": duo.KIND,
         "rom_type": "polished_crystal", "rom_sha1": SHA, "party_keys": list(KEYS), "party_count": 3,
         "pc_boxes_generation": 2, "pc_boxes": ["3:AAAAAA:D1C2:019:00"], "server_party_keys": list(KEYS[::-1]),
         "server_pc_boxes_n": 1}
    h.update(kw)
    return h


def side(role, port, reconnect=False, **kw):
    s = {"player": role, "paths": {"rom": f"/l/{role}/rom.gbc", "saveram": f"/l/{role}/s.SaveRAM", "config": f"/l/{role}/c.ini"},
         "client_port": port, "hello": hello(), "reconnect": hello(conn=3) if reconnect else None,
         "continuous": None if reconnect else True, "client_writes": 0, "commands": {"noop": 5, "link_panel": 1},
         "exit_marker": True}
    s.update(kw)
    return s


def good():
    return {"a": side("a", 5001), "b": side("b", 5002, reconnect=True)}


def test_good_case_passes():
    assert duo.evaluate(good(), SHA) == (True, [])


def _edit(path_fn):
    def apply(sides):
        path_fn(sides)
        return sides
    return apply


# (id, mutator, the one reason it must produce)
DEFECTS = [
    ("hello_missing", lambda s: s["a"].update(hello={"seen": False}), "a:hello_missing"),
    ("hello_rejected", lambda s: s["b"]["hello"].update(admission="rejected"), "b:hello_rejected"),
    ("adapter_differs", lambda s: s["a"]["hello"].update(adapter="gen2_gsc"), "a:adapter_mismatch"),
    ("artifact_differs", lambda s: s["b"]["hello"].update(artifact_kind="clean"), "b:artifact_kind_mismatch"),
    ("sha_differs", lambda s: s["a"]["hello"].update(rom_sha1="0" * 40), "a:rom_sha1_mismatch"),
    ("party_empty", lambda s: s["a"]["hello"].update(party_keys=[], party_count=0), "a:party_empty"),
    ("party_count_wrong", lambda s: s["b"]["hello"].update(party_count=2), "b:party_incomplete"),
    ("party_not_on_server", lambda s: s["a"]["hello"].update(server_party_keys=KEYS[:2]), "a:party_incomplete"),
    ("box_no_generation", lambda s: s["a"]["hello"].update(pc_boxes_generation=None), "a:box_census_incomplete"),
    ("box_list_missing", lambda s: s["a"]["hello"].update(pc_boxes=None), "a:box_census_incomplete"),
    ("box_not_stored", lambda s: s["a"]["hello"].update(server_pc_boxes_n=0), "a:box_census_refused"),
    ("census_changed", lambda s: s["b"]["reconnect"].update(pc_boxes=[], server_pc_boxes_n=0), "b:census_changed_after_reconnect"),
    ("party_changed", lambda s: s["b"]["reconnect"].update(party_keys=KEYS[::-1]), "b:census_changed_after_reconnect"),
    ("reconnect_hello_rejected", lambda s: s["b"]["reconnect"].update(admission="rejected"), "b/reconnect:hello_rejected"),
    ("reconnect_hello_missing", lambda s: s["b"].update(reconnect={"seen": False}), "b/reconnect:hello_missing"),
    ("no_side_reconnected", lambda s: s["b"].update(reconnect=None, continuous=True), "reconnect_missing"),
    ("a_dropped", lambda s: s["a"].update(continuous=False), "a:disconnected_unexpectedly"),
    ("client_wrote", lambda s: s["a"].update(client_writes=1), "a:write_observed"),
    ("writes_unmeasured", lambda s: s["b"].update(client_writes=-1), "b:write_observed"),
    ("box_mon_sent", lambda s: s["b"]["commands"].update(box_mon=1), "b:command_observed:box_mon"),
    ("force_faint_sent", lambda s: s["a"]["commands"].update(force_faint=2), "a:command_observed:force_faint"),
    ("exit_marker_missing", lambda s: s["b"].update(exit_marker=False), "b:exit_marker_missing"),
    ("shared_rom", lambda s: s["b"]["paths"].update(rom="/l/a/rom.gbc"), "isolation:rom_shared"),
    ("shared_saveram", lambda s: s["b"]["paths"].update(saveram="/l/a/s.SaveRAM"), "isolation:saveram_shared"),
    ("shared_config", lambda s: s["b"]["paths"].update(config="/l/a/c.ini"), "isolation:config_shared"),
    ("shared_port", lambda s: s["b"].update(client_port=5001), "isolation:client_port_shared_or_unknown"),
    ("port_unknown", lambda s: s["a"].update(client_port=None), "isolation:client_port_shared_or_unknown"),
]


@pytest.mark.parametrize("name,mutate,reason", DEFECTS, ids=[d[0] for d in DEFECTS])
def test_single_defect_fails_with_its_own_reason(name, mutate, reason):
    sides = copy.deepcopy(good())
    mutate(sides)
    ok, reasons = duo.evaluate(sides, SHA)
    assert not ok
    assert reasons == [reason], reasons


def test_one_side_only_fails_closed():
    assert duo.evaluate({"a": good()["a"]}, SHA) == (False, ["sides_missing"])


def test_contract_binds_both_players_to_the_overlay():
    c = duo.contract_for(SHA)
    assert c["players"] == {"a": {"rom_sha1": SHA}, "b": {"rom_sha1": SHA}}


def test_paths_are_private_per_side_and_per_launch():
    a, b = duo.side_paths(Path("/lane/run"), "a"), duo.side_paths(Path("/lane/run"), "b")
    for k in ("rom", "saveram", "config", "run", "result", "sram_dir"):
        assert a[k] != b[k]
    assert a["rom"].endswith("/" + duo.ROM_NAME) and a["saveram"].endswith("/pol overlay.SaveRAM")
    assert Path(a["rom"]).stem.replace("_", " ") == Path(a["saveram"]).stem   # BizHawk's save-name rule
    again = duo.side_paths(Path("/lane/run"), "a", generation=2)
    assert (again["rom"], again["saveram"], again["config"]) == (a["rom"], a["saveram"], a["config"])
    assert again["run"] != a["run"]


def test_env_names_the_player_and_server():
    p = duo.side_paths(Path("/lane/run"), "b")
    env = duo.side_env(p, "b", "127.0.0.1", 4242)
    assert (env["SLINK_PLAYER"], env["DUO_ROLE"], env["SLINK_PORT"]) == ("b", "b", "4242")
    assert env["POL_OUT"] == p["result"] and env["POL_SYMS"].startswith(p["run"])


def _wire(*recs):
    return duo.parse_wire(list(recs))


def test_parse_wire_counts_hellos_commands_and_connections():
    w = _wire({"dir": "meta", "conn": 1, "msg": {"event": "_connect"}},
              {"dir": "c2s", "conn": 1, "msg": {"event": "hello", "party": []}},
              {"dir": "c2s", "conn": 1, "msg": {"event": "tick"}},
              {"dir": "s2c", "conn": 1, "msg": {"commands": [{"cmd": "noop"}, {"cmd": "box_mon"}]}},
              {"dir": "meta", "conn": 1, "msg": {"event": "_disconnect"}},
              {"dir": "c2s", "conn": 2, "raw": "x", "msg": None})
    assert (w["connects"], w["disconnects"], len(w["hellos"])) == (1, 1, 1)
    assert w["commands"] == {"noop": 1, "box_mon": 1}


def test_parse_log_and_hello_view():
    log = duo.parse_log("2026 [INFO] __main__: Client connected: ('127.0.0.1', 5001)\n"
                        "x [a] admission: admitted — cartridge sha1 matches the contract\n"
                        "x [a] route polished_crystal -> gen2_polished (production)\n"
                        "x Client connected: ('127.0.0.1', 5002)\n"
                        "x [b] admission: rejected — nope\n")
    assert log["ports"] == [5001, 5002] and log["admission"] == {"a": ["admitted"], "b": ["rejected"]}
    rec = {"conn": 1, "msg": {"party": [{"key": "K1"}], "pc_boxes": [{"box": 2, "key": "K9"}], "pc_boxes_generation": 3,
                              "artifact_kind": "overlay", "rom_sha1": SHA}}
    h = duo.hello_view(rec, 0, "a", log, {"party_keys": ["K1"], "pc_boxes": [{}], "admission": "admitted"})
    assert (h["admission"], h["adapter"], h["party_keys"], h["pc_boxes"], h["server_pc_boxes_n"]) == (
        "admitted", "gen2_polished", ["K1"], ["2:K9"], 1)
    assert duo.hello_view(None, 0, "a", log, {}) == {"seen": False}
    assert duo.hello_view(rec, 0, "b", log, {"admission": "rejected"})["admission"] == "rejected"
    # a reconnect logs no admission line: the status verdict carries it; an identity error overrides an "admitted"
    assert duo.hello_view(rec, 1, "a", log, {"admission": "admitted"})["admission"] == "admitted"
    assert duo.hello_view(rec, 1, "a", log, {"admission": "admitted", "identity_error": "WRONG SAVE"})["admission"] == "identity_error"
    assert duo.hello_view(rec, 1, "a", log, {})["admission"] == "none"


# ═══════════════════════════ --distinct-identities (two fixtures, two trainers) ═══════════════════════════
A_FIXTURE = Path("F:/slink-work/lanes/g2int-live/pol/fixture/polished_overlay_warp.SaveRAM")
B_FIXTURE = Path("F:/slink-work/lanes/pol-ident/B.SaveRAM")
B_SHA256 = "f5f19f5d2e3e26e343214722b5116e1aac0c71a1200882a4cc6f58259bc6ca8d"
A_KEYS = ["EFFFFF:D1C2:0A9:00", "DFFFFF:D1C2:087:00", "CFFFFF:D1C2:037:40", "BFFFFF:D1C2:022:00", "AFFFFF:D1C2:055:40"]
B_KEYS = [k.replace(":D1C2:", ":D1C3:") for k in A_KEYS]
FOREIGN_KEYS = [f"11111{i}:D1C2:001:00" for i in range(5)]       # a save that is nobody's fixture


def _ds():
    return duo._derive_save()


def synth_save(name="Aaaaaaa", player_id=0xD1C2, mons=((0x37, 0xCF), (0x22, 0xBF))) -> bytes:
    """A minimal VALID SaveRAM image (markers, version, both checksums, N party mons) built with derive_save's own
    geometry, so the key-derivation helper is tested without the (machine-local) real fixture."""
    ds = _ds()
    buf = bytearray(ds.SAVE_SIZE)
    buf[ds.VERSION_AT:ds.VERSION_AT + 2] = ds.SAVE_VERSION
    for c in ds.ALL_COPIES:
        buf[c.low_marker_at], buf[c.high_marker_at] = ds.LOW_MARKER, ds.HIGH_MARKER
        buf[c.at(ds.ID_OFF):c.at(ds.ID_OFF) + 2] = player_id.to_bytes(2, "big")
        buf[c.at(ds.NAME_OFF):c.at(ds.NAME_OFF) + 8] = ds.pc.encode_text(name, 8)
        buf[c.at(ds.COUNT_OFF)] = len(mons)
        for slot, (species, dv) in enumerate(mons):
            raw = bytearray(ds.pc.PARTY_SIZE)
            raw[0] = species
            raw[6:8] = player_id.to_bytes(2, "big")
            raw[17:20] = bytes([dv, 0xFF, 0xFF])
            raw[31] = 5
            at = c.mon_at(slot)
            buf[at:at + ds.pc.PARTY_SIZE] = raw
            buf[c.ot_at(slot):c.ot_at(slot) + 8] = ds.pc.encode_text(name, 8)
    ds._reseal(buf)
    return bytes(buf)


def synth_pair():
    a = synth_save()
    b, _ = _ds().derive_identity(a, name="Bbbbbbb", player_id=0xD1C3)
    return a, b


def test_fixture_identity_decodes_name_id_and_keys_from_the_bytes():
    a, b = synth_pair()
    ia, ib = duo.fixture_identity(a), duo.fixture_identity(b)
    assert (ia["name"], ia["player_id"], ia["keys"]) == ("Aaaaaaa", 0xD1C2, ["CFFFFF:D1C2:037:00", "BFFFFF:D1C2:022:00"])
    assert (ib["name"], ib["player_id"], ib["keys"]) == ("Bbbbbbb", 0xD1C3, ["CFFFFF:D1C3:037:00", "BFFFFF:D1C3:022:00"])
    assert ia["sha256"].startswith(ia["prefix"]) and len(ia["prefix"]) == 8 and ia["prefix"] != ib["prefix"]


def test_fixture_identity_refuses_what_it_cannot_read():
    a, _ = synth_pair()
    ds = _ds()
    with pytest.raises(duo.FixtureError, match="size"):
        duo.fixture_identity(a[:-1])
    bad_sum = bytearray(a)
    bad_sum[ds.MAIN.checksum_at] ^= 1
    with pytest.raises(duo.FixtureError, match="checksum"):
        duo.fixture_identity(bytes(bad_sum))
    bad_marker = bytearray(a)
    bad_marker[ds.MAIN.low_marker_at] = 0
    with pytest.raises(duo.FixtureError, match="marker"):
        duo.fixture_identity(bytes(bad_marker))
    split = bytearray(a)          # main re-owned and re-sealed, backup left alone: the two copies disagree
    split[ds.MAIN.at(ds.ID_OFF) + 1] ^= 1
    ds._reseal(split)
    with pytest.raises(duo.FixtureError, match="disagree"):
        duo.fixture_identity(bytes(split))


@pytest.mark.skipif(not A_FIXTURE.is_file(), reason="the original warp fixture is a machine-local lane artifact")
def test_original_fixture_keys_are_the_ones_the_live_run_reported():
    ident = duo.fixture_identity(A_FIXTURE.read_bytes())
    assert (ident["name"], ident["player_id"], ident["keys"]) == ("Aaaaaaa", 53698, A_KEYS)
    assert ident["prefix"] == "75c7a5dc"


@pytest.mark.skipif(not B_FIXTURE.is_file(), reason="the derived second save is a machine-local lane artifact")
def test_derived_fixture_keys_carry_the_new_ot_id():
    ident = duo.fixture_identity(B_FIXTURE.read_bytes())
    assert ident["sha256"] == B_SHA256        # present-but-wrong fails: this is the save the live run was written for
    assert (ident["name"], ident["player_id"], ident["keys"]) == ("Bbbbbbb", 53699, B_KEYS)


@pytest.mark.skipif(not (A_FIXTURE.is_file() and B_FIXTURE.is_file()), reason="needs both machine-local saves")
def test_real_fixtures_are_distinct_trainers():
    assert duo.distinct_refusals(duo.fixture_identity(A_FIXTURE.read_bytes()), duo.fixture_identity(B_FIXTURE.read_bytes())) == []


def test_resolve_fixtures_precedence():
    env = {"POL_FIXTURE": "/c/common", "POL_FIXTURE_A": "/c/a_env", "POL_FIXTURE_B": "/c/b_env"}
    assert duo.resolve_fixtures("/cli/a", None, env) == (Path("/cli/a"), Path("/c/b_env"))
    assert duo.resolve_fixtures(None, "/cli/b", env) == (Path("/c/a_env"), Path("/cli/b"))
    assert duo.resolve_fixtures(None, None, {"POL_FIXTURE": "/c/common"}) == (Path("/c/common"), Path("/c/common"))
    assert duo.resolve_fixtures(None, None, {}) == (Path(duo.DEFAULT_FIXTURE), Path(duo.DEFAULT_FIXTURE))


def test_distinct_refusals_name_every_shared_fact():
    a, b = synth_pair()
    ia, ib = duo.fixture_identity(a), duo.fixture_identity(b)
    assert duo.distinct_refusals(ia, ib) == []
    assert set(duo.distinct_refusals(ia, ia)) == {"fixtures_byte_identical", "player_id_shared", "player_name_shared",
                                                  "party_keys_overlap"}
    same_id, _ = _ds().derive_identity(a, name="Zzzzzzz", player_id=0xD1C2)     # new name, same ID: keys and ID collide
    assert set(duo.distinct_refusals(ia, duo.fixture_identity(same_id))) == {"player_id_shared", "party_keys_overlap"}
    same_name, _ = _ds().derive_identity(a, name="Aaaaaaa", player_id=0xD1C3)
    assert duo.distinct_refusals(ia, duo.fixture_identity(same_name)) == ["player_name_shared"]


def _args(distinct, fa=None, fb=None):
    return duo.parse_args((["--distinct-identities"] if distinct else [])
                          + (["--fixture-a", str(fa)] if fa else []) + (["--fixture-b", str(fb)] if fb else []))


def test_distinct_mode_refuses_to_start_on_identical_fixtures(tmp_path):
    a, b = synth_pair()
    fa, fa2, fb = tmp_path / "a.SaveRAM", tmp_path / "a_copy.SaveRAM", tmp_path / "b.SaveRAM"
    fa.write_bytes(a)
    fa2.write_bytes(a)
    fb.write_bytes(b)
    with pytest.raises(SystemExit, match="fixtures_byte_identical"):
        duo.plan_fixtures(_args(True, fa, fa2))
    with pytest.raises(SystemExit, match="fixtures_byte_identical"):
        duo.plan_fixtures(_args(True, fa, fa))
    fixtures, ident = duo.plan_fixtures(_args(True, fa, fb))
    assert (ident["a"]["name"], ident["b"]["name"]) == ("Aaaaaaa", "Bbbbbbb") and fixtures["b"] == fb
    # the default (single-fixture) mode is unchanged: identical fixtures are the smoke, recorded not refused
    _, same = duo.plan_fixtures(_args(False, fa, fa2))
    assert same["a"]["sha256"] == same["b"]["sha256"]


def test_distinct_mode_refuses_a_shared_player_id_or_name(tmp_path):
    a, _ = synth_pair()
    twin, _ = _ds().derive_identity(a, name="Bbbbbbb", player_id=0xD1C2)
    other, _ = _ds().derive_identity(a, name="Aaaaaaa", player_id=0xD1C3)
    paths = {}
    for n, data in (("a", a), ("twin", twin), ("other", other)):
        paths[n] = tmp_path / f"{n}.SaveRAM"
        paths[n].write_bytes(data)
    with pytest.raises(SystemExit, match="player_id_shared"):
        duo.plan_fixtures(_args(True, paths["a"], paths["twin"]))
    with pytest.raises(SystemExit, match="player_name_shared"):
        duo.plan_fixtures(_args(True, paths["a"], paths["other"]))


def test_unreadable_fixture_is_refused_not_guessed(tmp_path):
    bad = tmp_path / "junk.SaveRAM"
    bad.write_bytes(b"\0" * 100)
    good = tmp_path / "g.SaveRAM"
    good.write_bytes(synth_save())
    with pytest.raises(SystemExit, match="unreadable"):
        duo.plan_fixtures(_args(False, good, bad))
    with pytest.raises(SystemExit, match="unreadable"):
        duo.plan_fixtures(_args(False, good, tmp_path / "missing.SaveRAM"))


def test_each_side_stages_its_own_fixture_in_private_dirs(tmp_path):
    a, b = synth_pair()
    fa, fb = tmp_path / "a.SaveRAM", tmp_path / "b.SaveRAM"
    fa.write_bytes(a)
    fb.write_bytes(b)
    run = tmp_path / "run"
    pa, pb = duo.side_paths(run, "a"), duo.side_paths(run, "b")
    sha_a, sha_b = duo.stage_side(pa, b"ROM", fa), duo.stage_side(pb, b"ROM", fb)
    assert Path(pa["saveram"]).read_bytes() == a and Path(pb["saveram"]).read_bytes() == b
    assert sha_a == duo.fixture_identity(a)["sha256"] and sha_b == duo.fixture_identity(b)["sha256"] and sha_a != sha_b
    assert Path(pa["saveram"]).parent != Path(pb["saveram"]).parent and Path(pa["rom"]) != Path(pb["rom"])
    fa.write_bytes(b"changed after staging")      # the copy is private: the fixture is never what a side boots from
    assert Path(pa["saveram"]).read_bytes() == a


def test_hello_view_exposes_the_identity_the_server_holds():
    log = duo.parse_log("")
    rec = {"conn": 1, "msg": {"party": [{"key": "K1"}], "pc_boxes": [], "pc_boxes_generation": 3, "ot_id": 53699,
                              "trainer_name": "Bbbbbbb"}}
    h = duo.hello_view(rec, 0, "b", log, {"party_keys": ["K1"], "trainer_name": "Bbbbbbb", "admission": "admitted"})
    assert (h["ot_id"], h["trainer_name"], h["server_trainer_name"], h["identity_error"]) == (53699, "Bbbbbbb", "Bbbbbbb", "")
    h = duo.hello_view(rec, 0, "b", log, {"identity_error": "WRONG SAVE"})
    assert h["identity_error"] == "WRONG SAVE" and h["admission"] == "identity_error"


# ── the distinct-identities evaluator ──
EXP = {"a": {"keys": A_KEYS, "player_id": 53698, "name": "Aaaaaaa"}, "b": {"keys": B_KEYS, "player_id": 53699, "name": "Bbbbbbb"}}


def dside(role, port, reconnect=False):
    keys, ident = (A_KEYS, EXP["a"]) if role == "a" else (B_KEYS, EXP["b"])
    ids = {"party_keys": list(keys), "server_party_keys": list(keys[::-1]), "party_count": len(keys),
           "ot_id": ident["player_id"], "trainer_name": ident["name"], "server_trainer_name": ident["name"],
           "identity_error": ""}
    s = side(role, port, reconnect=reconnect, hello=hello(**ids))
    if reconnect:
        s["reconnect"] = hello(conn=3, **ids)
    else:
        s["final"] = {"trainer_name": ident["name"], "party_keys": list(keys), "identity_error": ""}
    return s


def dgood():
    return {"a": dside("a", 5001), "b": dside("b", 5002, reconnect=True)}


def test_distinct_good_case_passes():
    assert duo.evaluate_distinct(dgood(), SHA, EXP) == (True, [])


def _swap_in_a(h):      # make a hello/reconnect look like fixture a booted: keys, OT id, trainer, server trainer
    h.update(party_keys=list(A_KEYS), server_party_keys=list(A_KEYS), ot_id=53698, trainer_name="Aaaaaaa",
             server_trainer_name="Aaaaaaa")


def _b_boots_a(s):
    _swap_in_a(s["b"]["hello"])
    _swap_in_a(s["b"]["reconnect"])


def _both_b(s, **kw):
    for h in (s["b"]["hello"], s["b"]["reconnect"]):
        h.update(**kw)


def _a_shows(s, keys=None, name=None):
    """What a really shows (wire hello, server status, and the status read after b reconnected): keys and/or trainer name."""
    h, fin = s["a"]["hello"], s["a"]["final"]
    if keys is not None:
        h.update(party_keys=list(keys), server_party_keys=list(keys), party_count=len(keys))
        fin.update(party_keys=list(keys))
    if name is not None:
        h.update(trainer_name=name, server_trainer_name=name)
        fin.update(trainer_name=name)


def _reconnect_new_identity(s):
    s["b"]["reconnect"].update(ot_id=53700, trainer_name="Cccccc", server_trainer_name="Cccccc")


DDEFECTS = [
    ("b_booted_fixture_a", _b_boots_a,
     ["b/reconnect:booted_other_fixture", "b:booted_other_fixture", "identity:ot_id_shared", "identity:party_keys_identical",
      "identity:trainer_name_shared"]),
    ("same_keys_on_both_sides_only", lambda s: _both_b(s, party_keys=list(A_KEYS), server_party_keys=list(A_KEYS)),
     ["b/reconnect:booted_other_fixture", "b:booted_other_fixture", "identity:party_keys_identical"]),
    ("same_trainer_name", lambda s: _both_b(s, trainer_name="Aaaaaaa", server_trainer_name="Aaaaaaa"),
     ["b/reconnect:trainer_name_not_from_fixture", "b:trainer_name_not_from_fixture", "identity:trainer_name_shared"]),
    ("same_ot_id", lambda s: _both_b(s, ot_id=53698),
     ["b/reconnect:ot_id_not_from_fixture", "b:ot_id_not_from_fixture", "identity:ot_id_shared"]),
    ("identity_error_on_a", lambda s: s["a"]["hello"].update(identity_error="WRONG SAVE"), ["a:identity_error_present"]),
    ("identity_error_on_b_reconnect", lambda s: s["b"]["reconnect"].update(identity_error="WRONG SAVE"),
     ["b/reconnect:identity_error_present"]),
    ("a_keys_not_from_its_fixture", lambda s: _a_shows(s, keys=FOREIGN_KEYS),
     ["a:party_keys_not_from_fixture"]),
    ("a_ot_id_not_from_its_fixture", lambda s: s["a"]["hello"].update(ot_id=1), ["a:ot_id_not_from_fixture"]),
    ("a_name_not_from_its_fixture", lambda s: _a_shows(s, name="Zz"), ["a:trainer_name_not_from_fixture"]),
    ("server_name_differs_from_wire", lambda s: s["b"]["hello"].update(server_trainer_name="Other"),
     ["b:server_trainer_name_differs"]),
    ("key_sets_overlap_partially", lambda s: _a_shows(s, keys=[B_KEYS[0]] + A_KEYS[1:]),
     ["a:party_keys_not_from_fixture", "identity:party_keys_overlap"]),
    ("reconnect_changed_b_keys", lambda s: s["b"]["reconnect"].update(party_keys=B_KEYS[:4], party_count=4,
                                                                       server_party_keys=B_KEYS[:4]),
     ["b/reconnect:party_keys_not_from_fixture", "b:census_changed_after_reconnect"]),
    ("reconnect_changed_b_identity", _reconnect_new_identity,
     ["b/reconnect:ot_id_not_from_fixture", "b/reconnect:trainer_name_not_from_fixture", "b:identity_changed_after_reconnect"]),
    ("a_changed_while_b_reconnected", lambda s: s["a"]["final"].update(party_keys=A_KEYS[:3]),
     ["a:identity_changed_while_peer_reconnected"]),
    ("a_renamed_while_b_reconnected", lambda s: s["a"]["final"].update(trainer_name="Other"),
     ["a:identity_changed_while_peer_reconnected"]),
    ("a_final_identity_error", lambda s: s["a"]["final"].update(identity_error="WRONG SAVE"),
     ["a:final_identity_error_present"]),
    ("a_final_missing", lambda s: s["a"].pop("final"), ["a:final_identity_missing"]),
    ("hello_missing_stays_the_evaluators_reason", lambda s: s["a"].update(hello={"seen": False}), ["a:hello_missing"]),
    ("base_defect_still_caught", lambda s: s["b"].update(client_writes=1), ["b:write_observed"]),
]


@pytest.mark.parametrize("name,mutate,reasons", DDEFECTS, ids=[d[0] for d in DDEFECTS])
def test_distinct_single_defect_fails_with_its_own_reasons(name, mutate, reasons):
    sides = copy.deepcopy(dgood())
    mutate(sides)
    ok, got = duo.evaluate_distinct(sides, SHA, EXP)
    assert not ok
    assert sorted(got) == sorted(reasons), got


def test_distinct_expected_fixtures_must_themselves_be_distinct():
    same = {"a": EXP["a"], "b": EXP["a"]}
    ok, got = duo.evaluate_distinct(dgood(), SHA, same)
    assert not ok and "identity:expected_fixtures_not_distinct" in got
    assert duo.evaluate_identities(dgood(), {"a": EXP["a"]}) == ["identity:expected_missing"]
    assert duo.evaluate_identities({"a": dgood()["a"]}, EXP) == ["identity:expected_missing"]


def test_one_identity_on_both_sides_is_a_pass_only_in_the_single_fixture_mode():
    """The old mode is untouched: ONE identity on both sides passes `evaluate`, and exactly the distinct evaluator refuses it."""
    sides = dgood()
    _b_boots_a(sides)
    assert duo.evaluate(sides, SHA) == (True, [])
    assert duo.evaluate_distinct(sides, SHA, EXP)[0] is False


# red controls: neuter the evaluator in-process and show the defects would have PASSED, i.e. each check is load-bearing
IDENTITY_ONLY = [d for d in DDEFECTS if d[0] not in ("hello_missing_stays_the_evaluators_reason", "base_defect_still_caught",
                                                     "reconnect_changed_b_keys")]


def test_red_control_without_the_identity_layer_every_identity_defect_passes(monkeypatch):
    monkeypatch.setattr(duo, "evaluate_identities", lambda sides, expected: [])
    assert len(IDENTITY_ONLY) >= 10
    for name, mutate, _ in IDENTITY_ONLY:
        sides = copy.deepcopy(dgood())
        mutate(sides)
        assert duo.evaluate_distinct(sides, SHA, EXP) == (True, []), name


def test_red_control_without_the_per_side_check_only_the_cross_side_checks_remain(monkeypatch):
    monkeypatch.setattr(duo, "_identity_reasons", lambda tag, h, own, other: [])
    sides = copy.deepcopy(dgood())
    _b_boots_a(sides)
    ok, got = duo.evaluate_distinct(sides, SHA, EXP)
    assert not ok and not [r for r in got if "fixture" in r]          # the booted-the-wrong-file reasons are gone ...
    assert "identity:party_keys_identical" in got                    # ... but two sides showing one identity still fails
    sides = copy.deepcopy(dgood())
    _a_shows(sides, keys=FOREIGN_KEYS)                   # a booted a file that is nobody's fixture
    assert duo.evaluate_distinct(sides, SHA, EXP) == (True, [])        # ... which only the per-side check can see


# ═══════════════════════════ PLAY scenarios (`--scenario`): per-scenario oracle + red controls ═══════════════════════
# Each oracle passes on a fabricated CORRECT transcript (server wire + /api/status + the Lua side's cartridge read-backs)
# and FAILS, with its own reason, on a transcript where the rule did not fire. The live run is the receipt.
KA, KB = "AE7343:D1C2:010:00", "1B2C3D:D1C3:013:00"
A_OWN, B_OWN = ["EFFFFF:D1C2:0A9:00", "DFFFFF:D1C2:087:00"], ["EFFFFF:D1C3:0A9:00", "DFFFFF:D1C3:087:00"]
AREA = "route_29"


def _rec(direction, msg, conn=1):
    return {"dir": direction, "conn": conn, "msg": msg}


def _snapshot(keys, hp=None, box=()):
    hp = hp or {}
    rows=[]
    for i,k in enumerate(keys):
        raw=bytearray((k.encode()*48)[:48])
        raw[1]=0
        raw[34:36]=hp.get(k,120).to_bytes(2,'big')
        rows.append({'slot':i,'key':k,'hp':hp.get(k,120),'max_hp':120,'status':0,
                     'record_hex':raw.hex(),'ot_hex':(k.encode()*11)[:11].hex(),
                     'nickname_hex':(k.encode()*11)[:11].hex()})
    return {'party':rows,'party_count':len(keys),'box':[{'box':0,'slot':i,'key':k} for i,k in enumerate(box)]}


def _catch(key, own):
    return {"ok": True, "op": "catch", "capture": {"key": key, "area_id": AREA, "species_id": 16, "level": 3},
            "capture_site_hits": 1, "snapshot": _snapshot(own + [key])}


def _link_status(state="alive"):
    return {"links": [{"area_id": AREA, "a_key": KA, "b_key": KB, "status": state}], "area_states": {AREA: "linked"},
            "players": {"a": {"party_keys": A_OWN + [KA]}, "b": {"party_keys": B_OWN + [KB]}}}


def link_ev():
    wa = [_rec("meta", {"event": "_connect"}), _rec("c2s", {"event": "hello", "party": []}),
          _rec("c2s", {"event": "capture", "key": KA, "area_id": AREA}),
          _rec("s2c", {"commands": [{"cmd": "hud_show"}, {"cmd": "noop"}]})]
    wb = [_rec("meta", {"event": "_connect"}), _rec("c2s", {"event": "hello", "party": []}),
          _rec("c2s", {"event": "capture", "key": KB, "area_id": AREA}),
          _rec("s2c", {"commands": [{"cmd": "msgbox"}, {"cmd": "play_sound", "sound": 25}]})]
    return {"scenario": "link", "box_capable": False, "wire": {"a": wa, "b": wb}, "marks": {}, "log": "",
            "steps": {"a": {"catch_a": _catch(KA, A_OWN)}, "b": {"catch_b": _catch(KB, B_OWN)}},
            "status": {"linked": _link_status(), "after": _link_status()}}


def faint_ev(whole_party=False):
    ev = link_ev()
    ev["scenario"] = "whiteout" if whole_party else "faint"
    ev["marks"]["stage"] = {"a": len(ev["wire"]["a"]), "b": len(ev["wire"]["b"])}
    a_party = A_OWN + [KA]
    zeroed = a_party if whole_party else [KA]
    ev["wire"]["a"] += [_rec("meta", {"event": "_disconnect"}), _rec("meta", {"event": "_connect"}, 2),
                        _rec("c2s", {"event": "hello", "party": [{"key": k, "hp": 0 if k in zeroed else 120}
                                                                   for k in a_party]}, 2)]
    ev["wire"]["b"] += [_rec("s2c", {"commands": [{"cmd": "force_faint", "key": KB, "nickname": "Pidgey"},
                                                  {"cmd": "play_sound", "sound": 26}, {"cmd": "memorialize", "key": KB}]})]
    ev["steps"]["a"]["stage"] = {"ok": True, "op": "synth_hp0", "before": _snapshot(a_party),
                                 "synth_writes": [{"key": k, "slot": i, "wram": 7000 + i} for i, k in enumerate(zeroed)
                                                  for _ in (0, 1)],
                                 "snapshot": _snapshot(a_party, hp=dict.fromkeys(zeroed, 0))}
    ev["steps"]["b"]["pre"] = {"ok": True, "op": "snapshot", "snapshot": _snapshot(B_OWN + [KB])}
    ev["steps"]["b"]["await"] = {"ok": True, "op": "await_cmd", "received": {"cmd": "force_faint", "key": KB},
                                 "snapshot": _snapshot(B_OWN + [KB], hp={KB: 0})}
    ev["status"]["after"] = _link_status("dead")
    return ev


def box_ev():
    ev = link_ev()
    ev["scenario"], ev["box_capable"] = "boxsync", True
    ev["wire"]["a"] += [_rec("s2c", {"commands": [{"cmd": "box_mon", "key": KA}]}),
                        _rec("s2c", {"commands": [{"cmd": "party_mon", "key": KA}]})]
    ev["steps"]["a"]["boxed"] = {"ok": True, "op": "await_cmd", "snapshot": _snapshot(A_OWN, box=[KA])}
    ev["steps"]["a"]["withdrawn"] = {"ok": True, "op": "await_cmd", "snapshot": _snapshot(A_OWN + [KA])}
    ev["log"] = f"[a] quarantine: {KA[:8]} → box (pending link)\n"
    return ev


def _wire_cmds(ev, role, fn):
    for rec in ev["wire"][role]:
        if rec["dir"] == "s2c":
            rec["msg"]["commands"] = [c for c in rec["msg"]["commands"] if fn(c)]



# Natural loss red controls precede the implementation: HP1 setup alone, an event alone, or the old hello
# reconciliation path is not proof that native battle copyback / pre-heal whiteout propagation worked.
def natural_ev(whole_party=False):
    ev = link_ev()
    ev["scenario"] = "whiteout-natural" if whole_party else "faint-natural"
    ev["marks"]["stage"] = {r: len(ev["wire"][r]) for r in ("a", "b")}
    a_party, ordered = A_OWN + [KA], [KA] + A_OWN[1:] + A_OWN[:1]
    targets = a_party if whole_party else [KA]
    before, staged = _snapshot(a_party), _snapshot(ordered, hp=dict.fromkeys(targets, 1))
    before["frame"], staged["frame"] = 100, 101
    writes = []
    for mon in staged["party"]:
        if mon["key"] in targets:
            for offset, old, new in ((0, 0, 0), (1, 120, 1)):
                writes.append({"key": mon["key"], "slot": mon["slot"], "wram": 0x1CD6 + 34 + mon["slot"] * 48 + offset,
                               "old": old, "new": new})
    lead_after=_snapshot(ordered)
    from tools.polished_live.faint_probe import read_symbols
    sym=read_symbols(REPO/'data/polished/polished_slink.sym')
    lead=len(a_party)-1
    lead_writes=[]
    for label,field,size in [('wPartyMons','record_hex',48),('wPartyMonOTs','ot_hex',11),('wPartyMonNicknames','nickname_hex',11)]:
        bank,addr=sym[label]
        base=addr-0xc000 if addr<0xd000 else (bank or 1)*0x1000+addr-0xd000
        for dst,src in [(0,lead),(lead,0)]:
            old,new=bytes.fromhex(before['party'][dst][field]),bytes.fromhex(before['party'][src][field])
            lead_writes.extend({'array':label,'wram':base+dst*size+i,'slot':dst,'key':before['party'][src]['key'],'old':old[i],'new':new[i]} for i in range(size))
    ev["steps"]["a"]["stage"] = {"ok": True, "op": "synth_hp1", "before": before, "synth_writes": writes,
                                 "lead_writes": lead_writes, "lead_after":lead_after, "snapshot": staged}
    zero_party = _snapshot(ordered, hp=dict.fromkeys(targets, 0))["party"]
    event = {"event": "whiteout"} if whole_party else {"event": "faint", "key": KA}
    report = dict(event, frame=150, party=copy.deepcopy(zero_party))
    hit = {"frame": 150, "party": copy.deepcopy(zero_party), "pc": 0x4ABC, "bank": 15,
           "qualified": True, "expected_hex": "abcd", "site": "LoseMoney" if whole_party else "UpdateBattleMonInParty",
           "slot": 0, "battle_hp": 0}
    ev["steps"]["a"]["battle"] = {"ok": True, "op": "lose_native", "reports": [report],
                                  "site_hits": {"faint_copyback": [copy.deepcopy(hit)], "whiteout": [hit] if whole_party else [],
                                                "heal_party": []},
                                  "run_used": False, "fight_inputs": 1, "snapshot": _snapshot(ordered)}
    if whole_party:
        ev["wire"]["a"].append(_rec("c2s", {"event":"faint","key":KA}))
    ev["wire"]["a"].append(_rec("c2s", event))
    ev["wire"]["b"].append(_rec("s2c", {"commands": [{"cmd": "force_faint", "key": KB}]}))
    ev["steps"]["b"]["pre"] = {"ok": True, "op": "snapshot", "snapshot": _snapshot(B_OWN + [KB])}
    ev["steps"]["b"]["await"] = {"ok": True, "op": "await_cmd", "received": {"cmd": "force_faint", "key": KB},
                                 "snapshot": _snapshot(B_OWN + [KB], hp={KB: 0})}
    ev["status"]["after"] = _link_status("dead")
    return ev


def _natural_hello_fallback(ev):
    mark = ev["marks"]["stage"]["a"]
    ev["wire"]["a"][mark:] = [_rec("meta", {"event": "_disconnect"}),
                                _rec("meta", {"event": "_connect"}, 2),
                                _rec("c2s", {"event": "hello", "party": [{"key": KA, "hp": 0}]}, 2)]
    ev["steps"]["a"]["battle"]["reports"] = []


def _stage_byte(ev, offset, new):
    row = sorted((w for w in ev["steps"]["a"]["stage"]["synth_writes"] if w["key"] == KA), key=lambda w: w["wram"])[offset]
    row["new"] = new


NATURAL_DEFECTS = [
    ("hello_fallback", _natural_hello_fallback, "a:poststage_reconnect"),
    ("native_event_missing", lambda e: e["wire"]["a"].pop(), "a:native_event_missing"),
    ("partner_command_missing", lambda e: _wire_cmds(e, "b", lambda c: c["cmd"] != "force_faint"),
     f"b:force_faint_missing:{KB}"),
    ("partner_command_before_mark", lambda e: e["marks"]["stage"].update(b=len(e["wire"]["b"])),
     f"b:force_faint_missing:{KB}"),
    ("native_event_before_mark", lambda e: e["marks"]["stage"].update(a=len(e["wire"]["a"])), "a:native_event_missing"),
    ("wrong_partner_command", lambda e: e["wire"]["b"][-1]["msg"]["commands"][0].update(key=B_OWN[0]),
     "b:force_faint_wrong_key"),
    ("explode_is_not_force_faint", lambda e: e["wire"]["b"][-1]["msg"]["commands"][0].update(cmd="force_explode"),
     "b:force_faint_wrong_command"),
    ("b_hp_survives", lambda e: e["steps"]["b"]["await"].update(snapshot=_snapshot(B_OWN + [KB])),
     f"b:cartridge_hp_not_zero:{KB}"),
    ("link_survives", lambda e: e["status"].update(after=_link_status()), f"link_still_alive:{AREA}"),
    ("link_disappeared", lambda e: e["status"]["after"].update(links=[]), f"dead_link_missing:{AREA}"),
    ("link_memorial_without_kill_stamp_is_not_dead", lambda e: e["status"].update(after=_link_status("memorial")),
     f"link_not_dead:{AREA}"),
    ("wrong_hp1_target", lambda e: e["steps"]["a"]["stage"]["synth_writes"][0].update(key=B_OWN[0]),
     "a:stage_wrong_target"),
    ("wrong_hp_address", lambda e: e["steps"]["a"]["stage"]["synth_writes"][0].update(wram=100000),
     f"a:stage_hp1_write_invalid:{KA}"),
    ("zero_injection", lambda e: _stage_byte(e, 1, 0), "a:stage_hp_zero_injection"),
    ("hp1_did_not_land", lambda e: e["steps"]["a"]["stage"].update(snapshot=_snapshot([KA] + A_OWN)),
     "a:stage_not_landed"),
    ("old_hp0_op", lambda e: e["steps"]["a"]["stage"].update(op="synth_hp0"), "a:stage_not_hp1"),
    ("stage_mark_missing", lambda e: e["marks"].clear(), "stage_mark_missing"),
    ("battle_failed", lambda e: e["steps"]["a"]["battle"].update(ok=False), "a:native_battle_failed"),
    ("report_missing", lambda e: e["steps"]["a"]["battle"].update(reports=[]), "a:native_report_missing"),
    ("report_before_setup", lambda e: e["steps"]["a"]["battle"]["reports"][0].update(frame=99),
     "a:native_report_missing"),
    ("client_received_other_key", lambda e: e["steps"]["b"]["await"]["received"].update(key=B_OWN[0]),
     "b:force_faint_not_received_by_client"),
    ("client_received_other_command", lambda e: e["steps"]["b"]["await"]["received"].update(cmd="memorialize"),
     "b:force_faint_not_received_by_client"),
]


@pytest.mark.parametrize("whole_party", [False, True], ids=["faint", "whiteout"])
@pytest.mark.parametrize("name,mutate,reason", NATURAL_DEFECTS, ids=[d[0] for d in NATURAL_DEFECTS])
def test_natural_loss_red_controls(whole_party, name, mutate, reason):
    ev = natural_ev(whole_party)
    mutate(ev)
    oracle = duo.oracle_whiteout_natural if whole_party else duo.oracle_faint_natural
    verdict, reasons, facts = oracle(ev)
    assert verdict == "FAIL" and reason in reasons, reasons
    assert "server_path" in facts


NATURAL_FAINT_DEFECTS = [
    ("wrong_faint_key", lambda e: e["wire"]["a"][-1]["msg"].update(key=A_OWN[0]), "a:native_event_missing"),
    ("copyback_hp_alive", lambda e: e["steps"]["a"]["battle"]["reports"][0]["party"][0].update(hp=1),
     "a:faint_copyback_hp_not_zero"),
    ("copyback_hook_missing", lambda e: e["steps"]["a"]["battle"]["site_hits"].update(faint_copyback=[]),
     "a:faint_copyback_hook_missing"),
    ("copyback_hook_unqualified", lambda e: e["steps"]["a"]["battle"]["site_hits"]["faint_copyback"][0].update(qualified=False),
     "a:faint_copyback_hook_missing"),
    ("copyback_hook_before_stage", lambda e: e["steps"]["a"]["battle"]["site_hits"]["faint_copyback"][0].update(frame=99),
     "a:faint_copyback_hook_missing"),
    ("copyback_hook_after_report", lambda e: e["steps"]["a"]["battle"]["site_hits"]["faint_copyback"][0].update(frame=151),
     "a:faint_copyback_hook_missing"),
    ("copyback_hook_wrong_slot", lambda e: e["steps"]["a"]["battle"]["site_hits"]["faint_copyback"][0].update(slot=1),
     "a:faint_copyback_hook_missing"),
    ("native_hp_nonzero", lambda e: e["steps"]["a"]["battle"]["site_hits"]["faint_copyback"][0].update(battle_hp=1),
     "a:faint_copyback_hook_missing"),
    ("b_collateral_loss", lambda e: e["steps"]["b"]["await"].update(snapshot=_snapshot(B_OWN + [KB], hp={KB: 0, B_OWN[0]: 0})),
     f"b:collateral_hp_change:{B_OWN[0]}"),
]


@pytest.mark.parametrize("name,mutate,reason", NATURAL_FAINT_DEFECTS, ids=[d[0] for d in NATURAL_FAINT_DEFECTS])
def test_natural_faint_copyback_red_controls(name, mutate, reason):
    ev = natural_ev()
    mutate(ev)
    verdict, reasons, _ = duo.oracle_faint_natural(ev)
    assert verdict == "FAIL" and reason in reasons, reasons


NATURAL_WHITEOUT_DEFECTS = [
    ("whiteout_hook_missing", lambda e: e["steps"]["a"]["battle"]["site_hits"].update(whiteout=[]),
     "a:whiteout_preheal_hook_missing"),
    ("whiteout_hook_unqualified", lambda e: e["steps"]["a"]["battle"]["site_hits"]["whiteout"][0].update(qualified=False),
     "a:whiteout_preheal_hook_missing"),
    ("whiteout_hook_healed_party", lambda e: e["steps"]["a"]["battle"]["site_hits"]["whiteout"][0]["party"][1].update(hp=120),
     "a:whiteout_preheal_hook_missing"),
    ("whiteout_report_healed_party", lambda e: e["steps"]["a"]["battle"]["reports"][0]["party"][1].update(hp=120),
     "a:whiteout_report_party_not_all_zero"),
    ("whiteout_hook_is_other_site", lambda e: e["steps"]["a"]["battle"]["site_hits"]["whiteout"][0].update(site="HealParty"),
     "a:whiteout_preheal_hook_missing"),
    ("only_linked_hp1", lambda e: e["steps"]["a"]["stage"].update(
        synth_writes=[w for w in e["steps"]["a"]["stage"]["synth_writes"] if w["key"] == KA]), "a:stage_wrong_target"),
    ("run_forfeit", lambda e: e["steps"]["a"]["battle"].update(run_used=True), "a:whiteout_run_used"),
    ("healed_before_send", lambda e: e["steps"]["a"]["battle"]["site_hits"].update(heal_party=[{"frame": 150}]),
     "a:whiteout_healed_before_report"),
    ("heal_observation_missing", lambda e: e["steps"]["a"]["battle"]["site_hits"].pop("heal_party"),
     "a:whiteout_heal_witness_missing"),
]


@pytest.mark.parametrize("name,mutate,reason", NATURAL_WHITEOUT_DEFECTS, ids=[d[0] for d in NATURAL_WHITEOUT_DEFECTS])
def test_natural_whiteout_preheal_red_controls(name, mutate, reason):
    ev = natural_ev(True)
    mutate(ev)
    verdict, reasons, _ = duo.oracle_whiteout_natural(ev)
    assert verdict == "FAIL" and reason in reasons, reasons


@pytest.mark.parametrize("whole_party,path", [(False, "faint_event"), (True, "whiteout_event_and_faint_propagation")])
def test_natural_loss_passes_only_the_native_server_path(whole_party, path):
    ev = natural_ev(whole_party)
    # Native loss may heal A afterwards. The report at send / guarded pre-heal hook, not the final party,
    # is the HP0 witness. A later no_catch on an already linked route is not a failed initial S1 capture.
    ev["wire"]["a"].append(_rec("c2s", {"event": "no_catch", "area_id": AREA}))
    oracle = duo.oracle_whiteout_natural if whole_party else duo.oracle_faint_natural
    verdict, reasons, facts = oracle(ev)
    assert (verdict, reasons, facts["server_path"]) == ("PASS", [], path)
    assert facts["expected_partners"] == facts["b_force_faint_keys"] == [KB]


@pytest.mark.parametrize("whole_party", [False, True], ids=["faint", "whiteout"])
def test_natural_loss_passes_when_the_server_already_memorialized_the_killed_pair(whole_party):
    # Live S2n (pol-duo3 s2n1): A ran away promptly after its faint report, both clients reached a safe overworld and
    # the server advanced dead -> memorial (stamped killed_at, cause battle) before the 'after' snapshot. That is the
    # same kill one server stage later, not a surviving link.
    ev = natural_ev(whole_party)
    ev["status"]["after"] = _link_status("memorial")
    ev["status"]["after"]["links"][0].update(killed_at="2026-10-08T23:58:18+00:00")
    oracle = duo.oracle_whiteout_natural if whole_party else duo.oracle_faint_natural
    verdict, reasons, _facts = oracle(ev)
    assert (verdict, reasons) == ("PASS", [])


@pytest.mark.parametrize("whole_party", [False, True], ids=["copyback_delay", "whiteout_queue_delay"])
def test_natural_report_may_follow_its_hook_on_a_later_frame(whole_party):
    ev = natural_ev(whole_party)
    ev["steps"]["a"]["battle"]["reports"][0]["frame"] = 160
    ev["wire"]["a"].append(_rec("meta", {"event": "_disconnect"}))
    ev["wire"]["b"].append(_rec("meta", {"event": "_disconnect"}))
    oracle = duo.oracle_whiteout_natural if whole_party else duo.oracle_faint_natural
    assert oracle(ev)[:2] == ("PASS", [])


def test_natural_whiteout_ignores_eggs_but_not_usable_mons():
    ev = natural_ev(True)
    egg = A_OWN[0]
    stage, battle = ev["steps"]["a"]["stage"], ev["steps"]["a"]["battle"]
    parties = [stage["before"]["party"], stage["snapshot"]["party"], stage["lead_after"]["party"],
               battle["reports"][0]["party"], battle["site_hits"]["whiteout"][0]["party"]]
    for party in parties:
        mon = next(m for m in party if m["key"] == egg)
        mon.update(egg=True, hp=120)
        raw=bytearray.fromhex(mon['record_hex'])
        raw[34:36]=b'\x00\x78'
        mon['record_hex']=raw.hex()
    stage["synth_writes"] = [w for w in stage["synth_writes"] if w["key"] != egg]
    assert duo.oracle_whiteout_natural(ev)[:2] == ("PASS", [])


@pytest.mark.parametrize("whole_party", [False, True])
def test_natural_loss_never_passes_hello_reconciliation(whole_party):
    ev = natural_ev(whole_party)
    _natural_hello_fallback(ev)
    oracle = duo.oracle_whiteout_natural if whole_party else duo.oracle_faint_natural
    verdict, reasons, facts = oracle(ev)
    assert verdict == "FAIL" and "a:native_event_missing" in reasons
    assert facts["server_path"] == "hello_reconcile"


def test_natural_link_gate_still_rejects_no_catch_before_stage():
    ev = natural_ev()
    ev["wire"]["a"].insert(3, _rec("c2s", {"event": "no_catch", "area_id": AREA}))
    ev["marks"]["stage"]["a"] += 1
    verdict, reasons, _ = duo.oracle_faint_natural(ev)
    assert verdict == "FAIL" and "link:a:no_catch_sent" in reasons


def test_natural_whiteout_requires_every_linked_partner_and_no_unlinked_death():
    ev = natural_ev(True)
    ka2, kb2 = A_OWN[0], B_OWN[0]
    for state, status in (("alive", ev["status"]["linked"]), ("dead", ev["status"]["after"])):
        status["links"].append({"area_id": "route_30", "a_key": ka2, "b_key": kb2, "status": state})
    verdict, reasons, _ = duo.oracle_whiteout_natural(ev)
    assert verdict == "FAIL" and f"b:force_faint_missing:{kb2}" in reasons
    ev["wire"]["b"][-1]["msg"]["commands"].append({"cmd": "force_faint", "key": kb2})
    ev["steps"]["b"]["await"]["received"] = [{"cmd": "force_faint", "key": KB}, {"cmd": "force_faint", "key": kb2}]
    ev["steps"]["b"]["await"]["snapshot"] = _snapshot(B_OWN + [KB], hp={KB: 0, kb2: 0})
    assert duo.oracle_whiteout_natural(ev)[:2] == ("PASS", [])


def retired_partner_ev():
    ev = natural_ev()
    step = ev["steps"]["b"]["await"]
    step["received"].update(frame=200, seq=21)
    step["force_faint_readback"] = {"key": KB, "hp": 0, "slot": 2, "frame": 201, "command_frame": 200, "seq": 21,
                                    "party": copy.deepcopy(step["snapshot"]["party"])}
    step["snapshot"] = _snapshot(B_OWN, box=[KB])
    return ev


RETIRED_PARTNER_DEFECTS = [
    ("hp_never_zero", lambda e: e["steps"]["b"]["await"]["force_faint_readback"].update(hp=1)),
    ("wrong_key", lambda e: e["steps"]["b"]["await"]["force_faint_readback"].update(key=B_OWN[0])),
    ("wrong_slot", lambda e: e["steps"]["b"]["await"]["force_faint_readback"].update(slot=0)),
    ("party_hp_alive", lambda e: e["steps"]["b"]["await"]["force_faint_readback"]["party"][-1].update(hp=1)),
    ("read_before_command", lambda e: e["steps"]["b"]["await"]["force_faint_readback"].update(frame=199)),
    ("other_command_frame", lambda e: e["steps"]["b"]["await"]["force_faint_readback"].update(command_frame=199)),
    ("other_command_sequence", lambda e: e["steps"]["b"]["await"]["force_faint_readback"].update(seq=22)),
]


@pytest.mark.parametrize("name,mutate", RETIRED_PARTNER_DEFECTS, ids=[d[0] for d in RETIRED_PARTNER_DEFECTS])
def test_natural_retirement_requires_exact_command_bound_hp0_readback(name, mutate):
    ev = retired_partner_ev()
    mutate(ev)
    verdict, reasons, _ = duo.oracle_faint_natural(ev)
    assert verdict == "FAIL" and f"b:force_faint_readback_invalid:{KB}" in reasons, reasons


def test_natural_partner_may_memorialize_after_its_real_cartridge_hp0_witness():
    verdict, reasons, facts = duo.oracle_faint_natural(retired_partner_ev())
    assert (verdict, reasons) == ("PASS", [])
    assert facts["b_cartridge_witness"] == {KB: "force_faint_readback"}


def test_natural_surviving_partner_is_not_hidden_by_an_earlier_zero_readback():
    ev = retired_partner_ev()
    ev["steps"]["b"]["await"]["snapshot"] = _snapshot(B_OWN + [KB])
    verdict, reasons, _ = duo.oracle_faint_natural(ev)
    assert verdict == "FAIL" and f"b:cartridge_hp_not_zero:{KB}" in reasons


def test_natural_loss_rejects_the_original_hp0_staged_transcripts():
    for whole_party, oracle in ((False, duo.oracle_faint_natural), (True, duo.oracle_whiteout_natural)):
        verdict, reasons, _ = oracle(faint_ev(whole_party))
        assert verdict == "FAIL" and "a:stage_not_hp1" in reasons


def test_play_oracles_pass_on_a_correct_transcript():
    assert duo.oracle_link(link_ev())[:2] == ("PASS", [])
    verdict, reasons, facts = duo.oracle_faint(faint_ev())
    assert (verdict, reasons) == ("PASS", []) and facts["server_path"] == "hello_reconcile"
    assert facts["expected_partners"] == [KB] and facts["b_force_faint_keys"] == [KB]
    verdict, reasons, facts = duo.oracle_whiteout(faint_ev(whole_party=True))
    assert (verdict, reasons) == ("PASS", []) and facts["expected_partners"] == [KB]
    assert duo.oracle_boxsync(box_ev())[:2] == ("PASS", [])


LINK_DEFECTS = [
    ("a_catch_failed", lambda e: e["steps"]["a"]["catch_a"].update(ok=False), "a:catch_failed"),
    ("b_capture_site_silent", lambda e: e["steps"]["b"]["catch_b"].update(capture_site_hits=0), "b:capture_site_not_fired"),
    ("b_capture_not_on_server_wire", lambda e: e["wire"]["b"].pop(2), "b:capture_not_on_wire"),
    ("a_caught_mon_not_on_cartridge", lambda e: e["steps"]["a"]["catch_a"].update(snapshot=_snapshot(A_OWN)),
     "a:caught_mon_not_on_cartridge"),
    ("areas_differ", lambda e: e["wire"]["b"][2]["msg"].update(area_id="route_30"), "capture_areas_differ"),
    ("no_catch_sent", lambda e: e["wire"]["b"].append(_rec("c2s", {"event": "no_catch", "area_id": AREA})),
     "b:no_catch_sent"),
    ("link_missing", lambda e: e["status"]["linked"].update(links=[]), "link_missing"),
    ("link_keys_mismatch", lambda e: e["status"]["linked"]["links"][0].update(b_key=B_OWN[0]), "link_keys_mismatch"),
    ("link_dead", lambda e: e["status"]["linked"]["links"][0].update(status="dead"), "link_not_alive"),
    ("area_dead_zoned", lambda e: e["status"]["linked"]["area_states"].update({AREA: "dead"}), "area_dead_zoned"),
]


@pytest.mark.parametrize("name,mutate,reason", LINK_DEFECTS, ids=[d[0] for d in LINK_DEFECTS])
def test_link_oracle_red_controls(name, mutate, reason):
    ev = link_ev()
    mutate(ev)
    verdict, reasons, _ = duo.oracle_link(ev)
    assert verdict == "FAIL" and reason in reasons, reasons


FAINT_DEFECTS = [
    ("no_force_faint_to_b", lambda e: _wire_cmds(e, "b", lambda c: c["cmd"] != "force_faint"), f"b:force_faint_missing:{KB}"),
    # a force_faint that was already on B's wire BEFORE A's HP-0 is not this rule firing
    ("force_faint_before_the_stage", lambda e: e["marks"]["stage"].update(b=len(e["wire"]["b"])),
     f"b:force_faint_missing:{KB}"),
    ("force_faint_wrong_key", lambda e: e["wire"]["b"][-1]["msg"]["commands"][0].update(key=B_OWN[0]),
     "b:force_faint_wrong_key"),
    ("b_client_never_received", lambda e: e["steps"]["b"]["await"].update(ok=False), "b:force_faint_not_received_by_client"),
    ("b_cartridge_hp_not_zero", lambda e: e["steps"]["b"]["await"].update(snapshot=_snapshot(B_OWN + [KB])),
     f"b:cartridge_hp_not_zero:{KB}"),
    ("b_partner_left_party", lambda e: e["steps"]["b"]["await"].update(snapshot=_snapshot(B_OWN)),
     f"b:partner_missing_from_party:{KB}"),
    ("b_collateral_faint", lambda e: e["steps"]["b"]["await"].update(snapshot=_snapshot(B_OWN + [KB], hp={KB: 0, B_OWN[0]: 0})),
     f"b:collateral_hp_change:{B_OWN[0]}"),
    ("b_partner_already_dead", lambda e: e["steps"]["b"]["pre"].update(snapshot=_snapshot(B_OWN + [KB], hp={KB: 0})),
     f"b:partner_not_alive_before:{KB}"),
    ("a_hp0_never_reported", lambda e: e["wire"]["a"][-1]["msg"].update(party=[{"key": KA, "hp": 120}]), "a:hp0_not_reported"),
    ("a_stage_failed", lambda e: e["steps"]["a"]["stage"].update(ok=False), "a:stage_failed"),
    ("a_stage_hit_another_mon", lambda e: e["steps"]["a"]["stage"].update(synth_writes=[{"key": A_OWN[0]}]),
     "a:stage_wrong_target"),
    ("a_stage_did_not_land", lambda e: e["steps"]["a"]["stage"].update(snapshot=_snapshot(A_OWN + [KA])), "a:stage_not_landed"),
    ("link_still_alive_after", lambda e: e["status"].update(after=_link_status("alive")), f"link_still_alive:{AREA}"),
    ("never_linked", lambda e: e["status"]["linked"].update(links=[]), "link:link_missing"),
]


@pytest.mark.parametrize("name,mutate,reason", FAINT_DEFECTS, ids=[d[0] for d in FAINT_DEFECTS])
def test_faint_oracle_red_controls(name, mutate, reason):
    ev = faint_ev()
    mutate(ev)
    verdict, reasons, _ = duo.oracle_faint(ev)
    assert verdict == "FAIL" and reason in reasons, reasons


def test_faint_oracle_names_the_server_path_that_fired():
    ev = faint_ev()
    ev["wire"]["a"][-1]["msg"]["party"] = [{"key": KA, "hp": 120}]          # the hello no longer carries the HP 0 ...
    ev["wire"]["a"].append(_rec("c2s", {"event": "faint", "key": KA}, 2))  # ... a faint event does
    verdict, reasons, facts = duo.oracle_faint(ev)
    assert (verdict, reasons, facts["server_path"]) == ("PASS", [], "faint_event")


WHITEOUT_DEFECTS = [
    ("only_the_linked_mon_zeroed", lambda e: e["steps"]["a"]["stage"].update(synth_writes=[{"key": KA}]), "a:stage_wrong_target"),
    ("wipe_incomplete_on_a", lambda e: e["steps"]["a"]["stage"].update(snapshot=_snapshot(A_OWN + [KA], hp={KA: 0})),
     "a:stage_not_landed"),
    ("unlinked_b_mon_also_killed", lambda e: e["wire"]["b"][-1]["msg"]["commands"].append({"cmd": "force_faint", "key": B_OWN[1]}),
     "b:force_faint_wrong_key"),
    ("partner_spared", lambda e: _wire_cmds(e, "b", lambda c: c["cmd"] != "force_faint"), f"b:force_faint_missing:{KB}"),
    ("b_cartridge_hp_not_zero", lambda e: e["steps"]["b"]["await"].update(snapshot=_snapshot(B_OWN + [KB])),
     f"b:cartridge_hp_not_zero:{KB}"),
    ("b_collateral_faint", lambda e: e["steps"]["b"]["await"].update(snapshot=_snapshot(B_OWN + [KB], hp={KB: 0, B_OWN[1]: 0})),
     f"b:collateral_hp_change:{B_OWN[1]}"),
    ("no_partner_to_kill", lambda e: e["status"]["linked"]["links"][0].update(a_key="0" * 18), "no_linked_partner"),
]


@pytest.mark.parametrize("name,mutate,reason", WHITEOUT_DEFECTS, ids=[d[0] for d in WHITEOUT_DEFECTS])
def test_whiteout_oracle_red_controls(name, mutate, reason):
    ev = faint_ev(whole_party=True)
    mutate(ev)
    verdict, reasons, _ = duo.oracle_whiteout(ev)
    assert verdict == "FAIL" and reason in reasons, reasons


def test_whiteout_oracle_reports_a_real_whiteout_event_when_one_is_sent():
    ev = faint_ev(whole_party=True)
    ev["wire"]["a"].append(_rec("c2s", {"event": "whiteout"}, 2))
    assert duo.oracle_whiteout(ev)[2]["server_path"] == "whiteout_event"


def test_boxsync_is_skipped_never_passed_while_the_capability_is_off():
    ev = link_ev()
    verdict, reasons, _ = duo.oracle_boxsync(ev)
    assert verdict == "SKIPPED" and reasons == [duo.BOX_CAPABILITY_OFF]
    assert duo.oracle_boxsync({"box_capable": False, "wire": {}})[0] == "SKIPPED"   # the not-launched path
    ev = box_ev()
    ev["box_capable"] = False                         # capability off but the server sent box commands anyway
    assert duo.oracle_boxsync(ev)[:2] == ("FAIL", ["box_command_sent_while_capability_false"])


BOX_DEFECTS = [
    ("quarantine_skipped", lambda e: e.update(log=f"[a] skip quarantine: {KA[:8]} (client has no box executor)\n"),
     "a:quarantine_skipped"),
    ("no_box_mon", lambda e: _wire_cmds(e, "a", lambda c: c["cmd"] != "box_mon"), "a:box_mon_missing"),
    ("box_mon_failed", lambda e: e["wire"]["a"].append(_rec("c2s", {"event": "box_mon_failed", "key": KA})), "a:box_mon_failed"),
    ("box_mon_not_received", lambda e: e["steps"]["a"]["boxed"].update(ok=False), "a:box_mon_not_received_by_client"),
    ("deposit_not_executed", lambda e: e["steps"]["a"]["boxed"].update(snapshot=_snapshot(A_OWN + [KA])), "a:box_mon_not_executed"),
    ("deposit_lost_the_mon", lambda e: e["steps"]["a"]["boxed"].update(snapshot=_snapshot(A_OWN)), "a:boxed_mon_not_in_census"),
    ("deposit_took_two", lambda e: e["steps"]["a"]["boxed"].update(snapshot=_snapshot(A_OWN[:1], box=[KA])),
     "a:party_count_not_decremented"),
    ("no_party_mon", lambda e: _wire_cmds(e, "a", lambda c: c["cmd"] != "party_mon"), "a:party_mon_missing"),
    ("withdraw_not_received", lambda e: e["steps"]["a"]["withdrawn"].update(ok=False), "a:party_mon_not_received_by_client"),
    ("withdraw_not_executed", lambda e: e["steps"]["a"]["withdrawn"].update(snapshot=_snapshot(A_OWN, box=[KA])),
     "a:party_mon_not_executed"),
    ("withdraw_duplicated", lambda e: e["steps"]["a"]["withdrawn"].update(snapshot=_snapshot(A_OWN + [KA], box=[KA])),
     "a:withdrawn_mon_still_in_box"),
    ("server_model_lost_it", lambda e: e["status"]["after"]["players"]["a"].update(party_keys=A_OWN),
     "a:server_party_model_missing_key"),
]


@pytest.mark.parametrize("name,mutate,reason", BOX_DEFECTS, ids=[d[0] for d in BOX_DEFECTS])
def test_boxsync_oracle_red_controls(name, mutate, reason):
    ev = box_ev()
    mutate(ev)
    verdict, reasons, _ = duo.oracle_boxsync(ev)
    assert verdict == "FAIL" and reason in reasons, reasons


def test_play_names_steps_and_step_lines():
    assert duo.play_names("all") == ["link", "boxsync", "faint", "whiteout", "faint-natural", "whiteout-natural"]
    assert duo.play_names("faint") == ["faint"]
    with pytest.raises(ValueError):
        duo.play_names("trade")
    assert duo.parse_args(["--scenario", "whiteout"]).scenario == "whiteout"
    assert duo.play_steps("link", False) == [("a", "catch_a"), ("b", "catch_b")]
    assert duo.play_steps("faint", True) == [("a", "catch_a"), ("a", "boxed"), ("b", "catch_b"), ("a", "withdrawn"),
                                             ("b", "pre"), ("a", "stage"), ("b", "await")]
    for name in ("faint-natural", "whiteout-natural"):
        assert duo.parse_args(["--scenario", name]).scenario == name
        assert duo.play_names(name) == [name]
        assert duo.play_steps(name, True) == [("a", "catch_a"), ("a", "boxed"), ("b", "catch_b"), ("a", "withdrawn"),
                                              ("b", "pre"), ("a", "stage"), ("a", "battle"), ("b", "await")]
    text = ('x\nPLAY_STEP {"k":1,"op":"catch","label":"catch_a","ok":true,"battles":{}}\nPLAY_STEP not json\n'
            'PLAY_STEP {"k":2,"op":"idle","ok":true}\n')
    got = duo.play_step_lines(text)
    assert got["catch_a"]["ok"] is True and got["idle#2"]["op"] == "idle"
    assert duo._as_list({}) == []                                           # Lua's empty array


@pytest.mark.parametrize("startup_error", [ConnectionRefusedError("starting"), URLError("starting")])
def test_play_waits_for_server_readiness_before_starting_both_sides(tmp_path, monkeypatch, startup_error):
    """A real startup-boundary failure must not abort the run before its emulator launches."""
    monkeypatch.setattr(duo, "LANE", tmp_path / "lane")
    source = tmp_path / "release"
    patch = tmp_path / "patch"
    source.write_bytes(b"release")
    patch.write_bytes(b"patch")
    monkeypatch.setattr(duo, "RELEASE", source)
    monkeypatch.setattr(duo, "UPS", patch)
    rom = b"staged-test-rom"
    monkeypatch.setattr(duo, "INTEGRATED_SHA1", duo.hashlib.sha1(rom).hexdigest())
    from patch.tools import make_ups
    monkeypatch.setattr(duo.sys, "path", list(sys.path))

    monkeypatch.setattr(make_ups, "ups_apply", lambda base, patch: rom)
    from tools.polished_live import harness, runtime
    monkeypatch.setattr(harness, "free_port", lambda: 54321)
    fixtures = {r: tmp_path / f"{r}.SaveRAM" for r in ("a", "b")}
    saves = synth_pair()
    for role, data in zip(("a", "b"), saves, strict=True):
        fixtures[role].write_bytes(data)
    identities = {r: duo.fixture_identity(fixtures[r].read_bytes()) for r in fixtures}
    ready = False
    requests = 0
    processes = []

    def status(port, path):
        nonlocal requests, ready
        requests += 1
        if requests == 1:
            raise startup_error
        ready = True
        return {"links": _link_status()["links"], "players": {"a": {}, "b": {}}, "area_states": {AREA: "linked"}}

    class Process:
        def __init__(self, command, **kwargs):
            self.pid, self.returncode = 100 + len(processes), None
            self.server = "-m" in command
            if not self.server:
                assert ready, "emulator launched before the HTTP server became ready"
                role = "a" if "/a/config.ini" in command[-2] else "b"
                result = Path(duo.side_paths(duo.LANE / "play-link", role)["result"])
                result.write_text("PLAY_READY\n", encoding="utf-8")
            processes.append(self)

        def poll(self):
            return None if self.server else 0

    def wire(path):
        return link_ev()["wire"]["a" if path.name == "wire_a.jsonl" else "b"]

    monkeypatch.setattr(duo, "http_json", status)
    monkeypatch.setattr(duo.subprocess, "Popen", Process)
    def start_owned(config, lua, run, env, *, config_path=None):
        run.mkdir(parents=True, exist_ok=True)
        return Process([config.emuhawk, f"--lua={lua}", f"--config={config_path}", config.rom.as_posix()])
    monkeypatch.setattr(runtime, "start_owned", start_owned)
    monkeypatch.setattr(duo, "kill_pid", lambda proc, label: setattr(proc, "returncode", 0))
    monkeypatch.setattr(duo.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(duo, "read_jsonl", wire)
    monkeypatch.setattr(duo, "play_step_lines", lambda text: {
        "catch_a": _catch(KA, A_OWN), "catch_b": _catch(KB, B_OWN),
        "stop_a": {"ok": True}, "stop_b": {"ok": True},
    })
    verdict, reasons, _ = duo.play_run(_args(True), "link", fixtures, identities, False)
    assert (verdict, reasons) == ("PASS", [])
    assert len(processes) == 3 and all(p.returncode == 0 for p in processes)


@pytest.mark.parametrize("state", ["occupied", "empty", "invalid"])
def test_play_snapshot_reads_the_production_box_census(state):
    """The live recorder must observe deposited keys, not mistake {mons=...} for an empty array."""
    from tests.unit.test_polished_boxes import Image, codec_key
    from tests.unit.test_polished_boxes_census import Rig, deposited, seal_save

    if state == "occupied":
        image, planted = deposited()
    else:
        image, planted = Image(), {}
        if state == "empty":
            seal_save(image)
    rig = Rig(image)
    before = image.snap()
    reads = rig.lua.table_from({
        "read_party": rig.lua.eval("function() return {mons={}} end"),
        "read_storage_box": rig.census.read_storage_box,
    })
    rig.lua.globals().emu = rig.lua.table_from({"framecount": rig.lua.eval("function() return 123 end")})
    driver = (REPO / "tools/polished_live/duo_play.lua").read_text(encoding="utf-8")
    # Execute the driver's actual read-back functions with the real production census over sealed SRAM.
    chunk = driver[driver.index("local function pbyte("):driver.index("local function slot_of(")]
    snapshot = rig.lua.execute("local P,L,PM = ...\n" + chunk + "\nreturn snapshot()",
                              rig.lua.table_from({"reads": reads}),
                              rig.lua.table_from({"rw": rig.lua.eval("function() return 0 end")}), rig.P)
    got = sorted((mon["box"], mon["slot"], mon["key"]) for mon in snapshot["box"].values())
    assert got == sorted((box - 1, slot - 1, codec_key(entry)) for (box, slot), entry in planted.items())
    assert snapshot["box_why"] is None if state != "invalid" else "sSaveVersion 0000" in snapshot["box_why"]
    assert image.snap() == before


def test_natural_driver_advances_intro_and_runs_through_real_menu_inputs():
    """An intro waiting for input before UI hooks must reach FIGHT, not idle until the stall bound."""
    lupa = pytest.importorskip("lupa")
    lua = lupa.LuaRuntime(unpack_returned_tuples=True)
    engine = """
local frame, mode, hp, engine, x, y = 0, 0, 1, "intro", 1, 1
local native_ui, native_ui_frame = nil, 0
local reports, witnesses, hook_errors = {}, {}, {}
local staged = {key="K", all=false, frame=0}
local fmt=string.format
local emu={framecount=function() return frame end}
local L={hits={BattleMenu_Run=0,BattleMenu_Fight=0,BattleTurn=0}}
local function party_evidence()
    return {{key="K",slot=0,hp=hp,level=2,egg=false},{key="S",slot=1,hp=100,level=50,egg=false}}
end
local function snapshot() return {frame=frame,party=party_evidence()} end
local function pbyte(slot,off) return off==2 and 33 or 0 end
local function be16(slot,off) return slot==0 and hp or 100 end
local function walk_for_battle() mode=1 return true end
function L.rw(name)
    if name=="wBattleMode" then return mode
    elseif name=="wCurBattleMon" or name=="wBattleType" then return 0
    elseif name=="wMenuCursorY" then return y
    elseif name=="wMenuCursorX" then return x end
    return 2
end
function L.wbytes() return {33,0,0,0} end
function L.ow_idle() return mode==0 end
function L.recent() return false end
function L.pulse(button)
    local pressed=button and frame%16<2
    if pressed and engine=="intro" and button=="A" then
        engine="fight";native_ui="root";native_ui_frame=frame
    elseif pressed and engine=="fight" and button=="A" then
        engine="fainted";hp=0
        reports[#reports+1]={event="faint",key="K",frame=frame};L.hits.BattleMenu_Fight=1
    elseif pressed and engine=="fainted" then
        if button=="Down" then y=2 elseif button=="Right" then x=2
        elseif button=="A" and x==2 and y==2 then engine="run_text";native_ui="post_run";y=3;L.hits.BattleMenu_Run=1 end
    elseif pressed and engine=="run_text" and button=="A" then
        engine="overworld";mode=0
    end
    frame=frame+1
end
"""
    source = (REPO / "tools/polished_live/duo_play.lua").read_text(encoding="utf-8")
    loss = source[source.index("local function op_lose_native("):source.index("-- ── boot + step loop")]
    result = lua.execute(engine + loss + '\nreturn op_lose_native({key="K",all=false,frames=5000})')
    assert result["ok"] and result["fight_inputs"] == 1 and result["run_used"]


def test_natural_whiteout_accepts_the_real_empty_wire_payload():
    ev = natural_ev(True)
    ev['wire']['a'][-1]['msg'].pop('party', None)
    assert duo.oracle_whiteout_natural(ev)[:2] == ('PASS', [])


def test_natural_whiteout_cannot_claim_handler_effects_from_event_presence():
    ev = natural_ev(True)
    facts = duo.oracle_whiteout_natural(ev)[2]
    assert facts['whiteout_handler_proved'] is False
    assert facts['server_path'] == 'whiteout_event_and_faint_propagation'


def test_natural_setup_rejects_a_lead_write_that_is_not_a_permutation():
    ev = natural_ev()
    ev['steps']['a']['stage']['lead_writes'] = [{'array':'wPartyMons','wram':0,'slot':0,'key':KA,'old':7,'new':8}]
    assert duo.oracle_faint_natural(ev)[0] == 'FAIL'


def test_emu_health_reports_json_and_never_raises():
    class Out:
        returncode, stderr = 0, ""
        stdout = '{"responding":true,"cpu_delta":0.0,"threads":22}\n'

    assert duo.emu_health(123, run=lambda *a, **k: Out()) == {"responding": True, "cpu_delta": 0.0, "threads": 22}

    def boom(*a, **k):
        raise OSError("no powershell")

    assert "OSError" in duo.emu_health(123, run=boom)["error"]


def test_duo_play_step_loop_records_a_lua_error_and_keeps_the_client_running():
    lupa = pytest.importorskip("lupa")
    source = (REPO / "tools/polished_live/duo_play.lua").read_text(encoding="utf-8")
    tail = source[source.index("local k, held, stopped = 0, 0, false"):source.index('L.check("stop step seen')]
    lua = lupa.LuaRuntime(unpack_returned_tuples=True)
    out = lua.execute("""
        local tail = ...
        local logs, frames = {}, 0
        local L = {RUN = ".", log = function(s) logs[#logs + 1] = s end}
        L.frame = function() frames = frames + 1 if frames > 40 then error("pol-live-finished", 0) end end
        local fake = {read = function() return '{"op":"catch"}' end, close = function() end, write = function() end}
        local env = setmetatable({L = L, fmt = string.format, emu = {framecount = function() return frames end},
            ROLE = "t", HOLD_CAP = 400000, client_writes = {},
            J = {decode = function() return {op = "catch"} end, encode = function() return "{}" end},
            op_catch = function() error("synthetic step error") end,
            io = {open = function() return fake end}}, {__index = _G})
        local ok, err = pcall(assert(load(tail, "tail", "t", env)))
        return ok, tostring(err), table.concat(logs, "|"), frames
    """, tail)
    ok, err, logs, frames = out
    assert ok is False and "pol-live-finished" in err             # the finish marker passes through ...
    assert "PLAY_LUA_ERROR" in logs and "synthetic step error" in logs and frames > 0   # ... after the error was logged


@pytest.mark.parametrize("whole_party", [False, True], ids=["faint", "whiteout"])
@pytest.mark.parametrize("duplicate", ["faint", "force_faint"])
def test_f5_natural_loss_rejects_duplicate_poststage_reports_and_kills(whole_party, duplicate):
    ev = natural_ev(whole_party)
    if duplicate == "faint":
        ev["wire"]["a"].append(_rec("c2s", {"event": "faint", "key": KA}))
    else:
        ev["wire"]["b"].append(_rec("s2c", {"commands": [{"cmd": "force_faint", "key": KB}]}))
    oracle = duo.oracle_whiteout_natural if whole_party else duo.oracle_faint_natural
    assert oracle(ev)[0] == "FAIL"


def test_f6_copyback_requires_its_own_keyed_party_hp_zero():
    ev = natural_ev()
    hit = ev["steps"]["a"]["battle"]["site_hits"]["faint_copyback"][0]
    next(m for m in hit["party"] if m["key"] == KA)["hp"] = 1
    assert duo.oracle_faint_natural(ev)[0] == "FAIL"


@pytest.mark.parametrize("field,offset", [
    ("record_hex", 32), ("record_hex", 2), ("record_hex", 23), ("record_hex", 1),
    ("record_hex", 38), ("ot_hex", 0), ("nickname_hex", 0),
], ids=["status", "moves", "pp", "item", "stats", "ot", "nickname"])
def test_f7_unaffected_partner_full_record_is_preserved(field, offset):
    ev = natural_ev()
    mon = ev["steps"]["b"]["await"]["snapshot"]["party"][0]
    raw = bytearray.fromhex(mon[field])
    raw[offset] ^= 1
    mon[field] = raw.hex()
    assert duo.oracle_faint_natural(ev)[0] == "FAIL"


@pytest.mark.parametrize("missing", ["record_hex", "ot_hex", "nickname_hex"])
def test_f7_unaffected_partner_missing_raw_evidence_fails_closed(missing):
    ev = natural_ev()
    for label in ("pre", "await"):
        ev["steps"]["b"][label]["snapshot"]["party"][0].pop(missing)
    assert duo.oracle_faint_natural(ev)[0] == "FAIL"


def test_f7_unaffected_native_slot_movement_is_allowed():
    ev = natural_ev()
    ev["steps"]["b"]["await"]["snapshot"]["party"].reverse()
    for slot, mon in enumerate(ev["steps"]["b"]["await"]["snapshot"]["party"]):
        mon["slot"] = slot
        mon["address"] = 0xD000 + slot * 48
    assert duo.oracle_faint_natural(ev)[:2] == ("PASS", [])
