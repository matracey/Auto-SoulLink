"""The Manager offers the Emerald Expansion (game key gen3_exp) and refuses what it cannot do.

The expansion is a prebuilt 32 MiB reference ROM (tools/build_expansion.py; no patch/UPS exists):
no randomizer, no companion, a real battle calc. The pre-change tables of every other game are
pinned in tests/fixtures/manager_tables_pre_gen3_exp.json so adding the row cannot move theirs.
"""
from __future__ import annotations

import asyncio
import json
import os

import pytest

pytest.importorskip("aiohttp", reason="the manager is an aiohttp app")

from server import adapters  # noqa: E402
from server import manager as mgr  # noqa: E402
from server import upr_pipeline  # noqa: E402

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
ROM = os.path.join(ROOT, "patch", "build", "gen3_pokeemerald.gba")
GOLDEN = os.path.join(ROOT, "tests", "fixtures", "manager_tables_pre_gen3_exp.json")
ROM_TYPE = "emerald_expansion_28877d73"
KEY = "gen3_exp"


@pytest.fixture
def routed():
    """The expansion rom_type must route to its adapter through the production route."""
    assert adapters.game_id_for_rom_type(ROM_TYPE) == KEY
    yield


@pytest.fixture
def manager_dir(tmp_path, monkeypatch):
    d = tmp_path / "runs"
    d.mkdir()
    monkeypatch.setattr(mgr, "MANAGER_DIR", str(d))
    monkeypatch.setattr(mgr, "REGISTRY_PATH", str(d / "registry.json"))
    mgr._save_registry([])
    return d


class _Request:
    def __init__(self, body, run_id="run_x"):
        self.match_info = {"run_id": run_id}
        self._body = body

    async def json(self):
        return self._body


def _manager():
    m = mgr.RunManager.__new__(mgr.RunManager)
    m.bind_host, m.manager_port, m._run_locks = "127.0.0.1", 0, {}
    m._run_locks = {}
    return m


def _add_run(game):
    mgr._save_registry([{"run_id": "run_x", "name": "x", "tcp_port": 1, "http_port": 2,
                         "status": "stopped", "pid": None, "game": game}])


def _need_rom():
    if not os.path.isfile(ROM):
        pytest.skip("patch/build/gen3_pokeemerald.gba is not built here")


def _cartridges(body):
    resp = asyncio.run(_manager().handle_cartridges(_Request(body)))
    return resp.status, json.loads(resp.text)


# -- the game row -------------------------------------------------------------------------
def test_the_expansion_is_an_admitted_game_with_its_one_cartridge():
    rows = {k: (label, members) for k, label, members in mgr.GAMES}
    assert rows[KEY][1] == [ROM_TYPE]
    assert "Emerald Expansion" in rows[KEY][0]
    assert KEY not in mgr.UNADMITTED_GAMES
    assert mgr.GAME_LABELS[KEY] == rows[KEY][0]
    assert mgr.GAME_MEMBERS[KEY] == [ROM_TYPE]


def test_the_expansion_never_offers_the_randomizer_or_the_cartridges_step():
    form = mgr.new_run_form()
    assert KEY not in form["randomizer_games"]
    assert KEY not in form["gen1_games"]
    assert KEY in mgr.NON_RANDOMIZABLE_GAMES
    assert "Emerald Expansion" in mgr.NON_RANDOMIZABLE_GAMES[KEY]


def test_the_randomizer_exclusion_holds_even_if_the_game_gains_a_family(monkeypatch):
    """randomizer_games is GAME_FAMILY minus NON_RANDOMIZABLE_GAMES: adding gen3_exp to
    GAME_FAMILY later must not offer it the randomizer."""
    monkeypatch.setitem(mgr.GAME_FAMILY, KEY, "gen3_exp")
    assert KEY not in mgr.new_run_form()["randomizer_games"]


def test_provision_itself_refuses_a_randomizer_and_hands_out_the_build_without_a_companion(tmp_path):
    """Patch-first (5ea1881f / 2d2c37db): the companion is decided SERVER-SIDE per player (companion_admitted), so a companion=True request
    for the expansion (which has no admitted companion yet; Codex's build lands last, companion-only) resolves to companion False and the
    reference ROM is handed out as picked. Before that change provision raised for it. The randomizer is still refused by name."""
    _need_rom()
    from server import cartridges
    with pytest.raises(cartridges.CartridgeError, match="Emerald Expansion"):
        cartridges.provision(str(tmp_path / "refused"), {"a": ROM, "b": ROM}, companion=False,
                             randomize={"settings_path": "x.rnqs"})
    assert not (tmp_path / "refused").exists()
    out = cartridges.provision(str(tmp_path / "out"), {"a": ROM, "b": ROM}, companion=True, randomize=None)
    assert out["family"] == "gen3_exp" and out["companion"] is False and out["randomizer"] is None


def test_no_companion_title_is_offered_for_the_expansion():
    from server.cartridges import COMPANION_TITLES
    assert not any("expansion" in t.lower() for t in COMPANION_TITLES)


def test_the_expansion_option_rows_are_explicit(routed):
    support = mgr.new_run_form()["support"][KEY]
    ok = {k: v["ok"] for k, v in support.items()}
    assert ok == {"retry_same_species": True,
                  "species_lock": True, "gender_lock": True, "type_lock": True,
                  "explode_mode": False, "rival_team_swap": False, "overworld_presence": False,
                  "native_messages": False, "native_sounds": False, "phone_calls": False,
                  "battle_calc": True, "pc_trade_npc": False}
    for key in ("explode_mode", "rival_team_swap", "native_sounds", "pc_trade_npc"):
        assert "Emerald Expansion" in support[key]["why"], key
    assert not support["pc_trade_npc"].get("always")


def test_every_other_game_keeps_exactly_its_table(routed):
    with open(GOLDEN, encoding="utf-8") as f:
        want = json.load(f)
    form = mgr.new_run_form()

    def others(d):
        return {k: v for k, v in d.items() if k != KEY}

    assert [g for g in form["games"] if g["key"] != KEY] == want["games"]
    existing_support = {
        game: {key: value for key, value in options.items() if key != "retry_same_species"}
        for game, options in others(form["support"]).items()
    }
    assert existing_support == want["support"]
    assert [g for g in form["randomizer_games"] if g != KEY] == want["randomizer_games"]
    assert [g for g in form["gen1_games"] if g != KEY] == want["gen1_games"]
    assert others(mgr.GAME_FAMILY) == want["game_family"]
    assert others(mgr.FAMILY_WORDS) == want["family_words"]
    assert others(mgr.NON_RANDOMIZABLE_GAMES) == want["non_randomizable"]
    from server.cartridges import COMPANION_TITLES
    assert list(COMPANION_TITLES) == want["companion_titles"]
    assert sorted(mgr.UNADMITTED_GAMES) == want["unadmitted"]


# -- creating a run, and what the Cartridges step does with it ---------------------------
def test_an_expansion_run_can_be_created(manager_dir, monkeypatch):
    async def spawn(run, *_a, **_k):
        return 4242
    monkeypatch.setattr(mgr, "_spawn_run", spawn)
    monkeypatch.setattr(mgr, "_create_time", lambda _pid: 1.0)
    resp = asyncio.run(_manager().handle_new(_Request({"name": "exp run", "game": KEY})))
    out = json.loads(resp.text)
    assert resp.status == 200 and out["ok"], out
    assert out["run"]["game"] == KEY


def test_a_randomizer_request_for_an_expansion_run_is_refused_by_name(manager_dir, monkeypatch):
    """Ruling 37 shape (no ROM is read: the game alone decides): a game with no randomizer is refused explicitly, not by the accident of
    being absent from GAME_FAMILY (which skipped the family check and let the request through)."""
    _add_run(KEY)
    monkeypatch.setattr(upr_pipeline, "prepare_pair", lambda *a, **k: pytest.fail("randomizer ran"))
    status, out = _cartridges({"jar": __file__, "categories": ["wild"], "rom_a": "nope_a.gba", "rom_b": "nope_b.gba",
                               "randomize": True})
    assert status == 400 and not out["ok"]
    assert "Emerald Expansion" in out["error"]
    assert "randomiz" in out["error"].lower()
    assert "randomizer" not in (mgr._find_run(mgr._load_registry(), "run_x") or {})


def test_expansion_cartridges_in_a_detect_run_refuse_the_randomizer(manager_dir, monkeypatch):
    """A game-less (Detect) run picking the build: no randomizer exists for it."""
    _need_rom()
    _add_run("")
    monkeypatch.setattr(upr_pipeline, "prepare_pair", lambda *a, **k: pytest.fail("randomizer ran"))
    status, out = _cartridges({"rom_a": ROM, "rom_b": ROM, "randomize": True, "categories": ["wild"], "jar": "x"})
    assert status == 400 and not out["ok"], out
    assert "Emerald Expansion" in out["error"] and "randomiz" in out["error"].lower()
    assert not (manager_dir / "run_x" / "roms").exists()


def test_a_companion_request_for_the_expansion_is_handed_out_without_one(manager_dir):
    """Patch-first (5ea1881f / 2d2c37db): the Manager always asks for the companion and the server decides per player; the expansion has none
    admitted yet, so the pick comes back unchanged and the status says companion False (it lands last, companion-only)."""
    _need_rom()
    _add_run("")
    status, out = _cartridges({"rom_a": ROM, "rom_b": ROM, "companion": True})
    assert status == 200 and out["ok"], out
    assert out["cartridges"]["companion"] is False and out["cartridges"]["family"] == "gen3_exp"


def test_a_plain_expansion_pair_is_handed_out_unchanged(manager_dir):
    _need_rom()
    _add_run("")
    status, out = _cartridges({"rom_a": ROM, "rom_b": ROM})
    assert status == 200 and out["ok"], out
    assert out["cartridges"]["family"] == "gen3_exp"
    assert out["randomizer"] is None
    for pid in ("a", "b"):
        player = out["cartridges"]["players"][pid]
        assert player["kind"] == "clean" and player["output"].endswith(f"{pid}.gba")
        assert player["fingerprint"] == ""
        assert open(player["output"], "rb").read() == open(ROM, "rb").read()


def test_an_emerald_run_refuses_expansion_cartridges_naming_both(manager_dir):
    _need_rom()
    _add_run("gen3_e")
    status, out = _cartridges({"rom_a": ROM, "rom_b": ROM})
    assert status == 400 and not out["ok"]
    assert "Emerald Expansion" in out["error"] and "Emerald dumps" in out["error"]


def test_a_mixed_expansion_and_emerald_pair_is_refused_in_a_detect_run(manager_dir, tmp_path):
    _need_rom()
    _add_run("")
    emerald = tmp_path / "emerald.gba"
    rom = bytearray(16 << 20)
    rom[0xAC:0xB0] = b"BPEE"
    emerald.write_bytes(bytes(rom))
    status, out = _cartridges({"rom_a": ROM, "rom_b": str(emerald)})
    assert status == 400 and "different families" in out["error"]


# -- the ROM picker -----------------------------------------------------------------------
def test_the_picker_lists_the_build_as_a_clean_expansion_cartridge(monkeypatch):
    _need_rom()
    monkeypatch.setattr(mgr, "ROM_DIRS", (os.path.dirname(ROM),))
    monkeypatch.setattr(mgr, "_ROM_INFO_CACHE", {})
    rows = [r for r in mgr.RunManager._scan_roms("") if r["name"] == "gen3_pokeemerald.gba"]
    assert len(rows) == 1
    assert rows[0]["clean"] is True and rows[0]["family"] == "gen3_exp"
    assert rows[0]["variant"] == "Emerald Expansion"


# -- the Cartridges page script (a game-less run can still pick the build) ---------------
def test_the_picker_script_groups_the_build_and_greys_randomize_and_companion(tmp_path):
    from tests.unit.test_randomizer_js import _run_node
    out = _run_node(tmp_path, """
const rf = mod.randomizerFields(form);
rf.roms = [{ path: 'x.gba', clean: true, family: 'gen3_exp', variant: 'Emerald Expansion', title: 'x' }];
rf.rdraft.rom_a = rf.rdraft.rom_b = 'x.gba';
const groups = rf.romGroups().map(g => g.label);
const plain = rf.cartsWhy();
rf.rdraft.randomize = true;
console.log(JSON.stringify({ groups, plain, rnd: rf.cartsWhy(), companion: rf.companionOk(),
  label: rf.familyLabel('gen3_exp') }));
""")
    assert out["groups"] == ["Emerald Expansion"] and out["label"] == "Emerald Expansion"
    assert out["plain"] == "" and "no randomizer" in out["rnd"]
    assert out["companion"]["ok"] is False and "Emerald Expansion" in out["companion"]["why"]
