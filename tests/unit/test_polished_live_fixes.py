"""Two defects the first live Polished run found (docs/polished/LIVE_RESULTS.md, 2026-10-04).

(1) Stage 3: the box census never refreshed. lua/gen2/client.lua rescans only on `pending_rescan` or while the
    last scan was incomplete, and the Polished composition has no event that sets `pending_rescan` (no capture_box
    site, no battle-end signal, no PC events, read_current_box_num -1). compose_polished now passes
    `rescan_every=1800`: a battle end (wBattleMode nonzero -> 0) or the periodic interval re-arms the census, checked
    once per tick. Vanilla passes nothing, so the block is never entered there.
(2) Observation S2-a originally queued box_mon before Polished had a box executor. Party sync is now ON,
    and every admitted client composes that executor. The obsolete supports_box_mon capability and its
    three shared guards are retired; the tests below pin capture/reconnect quarantine and memorial relocation.

RED CONTROLS (each mutation must turn the named test red):
  battle end       drop `(mode == 0 and (self.census_mode or 0) ~= 0) or` from client.lua  -> test_a_battle_end_refreshes_the_census
  periodic         drop the `math.abs(...) >= self.rescan_every` arm from client.lua       -> test_the_periodic_interval_refreshes_the_census
  composition      delete `rescan_every=1800,` from entry.lua compose_polished             -> both refresh tests
  obsolete guard   reintroduce a supports_box_mon call -> test_box_paths_do_not_consult_an_obsolete_capability

UNPROVEN without a live run: that a native catch-to-box leaves the SRAM census complete at the next trigger (the
census checksum covers sGameData only; the live run read the box mon only after a reboot), and the on-cartridge cost
of one census per 1800 frames.
"""
from __future__ import annotations

import json
import random

import pytest

from server.adapters.gen2_gsc import Gen2GSCAdapter
from server.adapters.gen2_polished import Gen2PolishedAdapter
from server.state import SoulLinkState
from tests.unit.test_polished_boxes import PAUSED_FLAT
from tests.unit.test_polished_boxes_census import HARNESS, codec_key, deposited, hatched
from tests.unit.test_polished_client import SYM, _memory, _mons
from tests.unit.test_polished_lua import ROOT, _pair, _real

lupa = pytest.importorskip("lupa")

BATTLE_MODE = SYM["wBattleMode"][1]


@pytest.fixture(scope="module")
def overlay_rom():
    return _real()[1]


class Live:
    """The real compose_polished client over a synthetic save image, up to and past its hello."""

    def __init__(self, rom, img):
        self.img, self.lua = img, lupa.LuaRuntime(unpack_returned_tuples=True)
        self.mem = self.lua.table_from(_memory(_mons()))
        deps, self.io, self.log = self.lua.execute(HARNESS.replace("ROOTDIR", json.dumps(ROOT)))(
            rom, self.mem, lambda a, d: img.read(a, d))
        parts, why = _pair(self.lua.eval(f'dofile("{ROOT}/lua/gen2/entry.lua")').build(deps))
        assert why is None, why
        self.client = parts.client
        self.client.start(self.client)
        self.frames(80)
        (self.hello,) = self.sent("hello")

    def frames(self, n):
        for _ in range(n):
            self.io.frame += 1
            self.client.frame_end(self.client)

    def sent(self, event):
        lines = [json.loads(line) for line in self.log.sent.values()]
        return [m for m in lines if m["event"] == event]

    def census(self):
        tick = self.sent("tick")[-1]
        return sorted(e["key"] for e in tick["pc_boxes"]), tick.get("pc_boxes_generation")

    def arrive(self):
        """A catch-to-box lands a new mon in box 2 slot 4 (the native SendToPC writes SRAM, no event)."""
        entry = hatched(random.Random(77))
        self.img.plant(2, 4, 1, 60, entry)
        return codec_key(entry)


def _arrived(live, planted):
    before_keys, before_gen = live.census()
    key = live.arrive()
    live.frames(60)  # two ticks, no trigger
    assert live.census() == (before_keys, before_gen) and key not in before_keys
    return key, before_gen


def test_the_composition_sets_the_interval(overlay_rom):
    img, _ = deposited()
    assert Live(overlay_rom, img).client.rescan_every == 1800


def test_a_battle_end_refreshes_the_census(overlay_rom):
    img, planted = deposited()
    live = Live(overlay_rom, img)
    key, gen = _arrived(live, planted)
    live.mem[BATTLE_MODE] = 1
    live.frames(60)
    assert key not in live.census()[0]  # still in battle: no trigger yet
    live.mem[BATTLE_MODE] = 0
    live.frames(31)
    keys, after = live.census()
    assert key in keys and after > gen
    assert live.io.frame < 1800  # the battle end, not the periodic arm


def test_the_periodic_interval_refreshes_the_census(overlay_rom):
    img, planted = deposited()
    live = Live(overlay_rom, img)
    key, gen = _arrived(live, planted)
    live.frames(1800 - live.io.frame - 30)
    assert key not in live.census()[0]  # one tick short of the interval
    live.frames(31)
    keys, after = live.census()
    assert key in keys and after > gen


def test_a_running_native_save_still_withholds_the_refresh(overlay_rom):
    img, planted = deposited()
    live = Live(overlay_rom, img)
    key, gen = _arrived(live, planted)
    img.mem["WRAM"][PAUSED_FLAT] = 1
    live.mem[BATTLE_MODE] = 1
    live.frames(30)
    live.mem[BATTLE_MODE] = 0
    live.frames(60)
    tick = live.sent("tick")[-1]
    assert key not in [e["key"] for e in tick["pc_boxes"]] and "pc_boxes_generation" not in tick
    img.mem["WRAM"][PAUSED_FLAT] = 0
    live.frames(31)  # the incomplete scan is retried once per tick
    keys, after = live.census()
    assert key in keys and after > gen


def test_no_interval_means_no_refresh(overlay_rom):
    """rescan_every nil is the vanilla client: neither trigger arms a rescan."""
    img, planted = deposited()
    live = Live(overlay_rom, img)
    live.client.rescan_every = None
    key, gen = _arrived(live, planted)
    live.mem[BATTLE_MODE] = 1
    live.frames(30)
    live.mem[BATTLE_MODE] = 0
    live.frames(60)
    assert live.census() == (sorted(codec_key(e) for e in planted.values()), gen)


# ── (2) box_mon for a client that cannot box ─────────────────────────────────

def _solo_catch(adapter, tmp_path, monkeypatch, key):
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState(adapter=adapter)
    state.pokeballs_obtained = {"a": True, "b": True}
    state.party_size = {"a": 5, "b": 0}
    cmds = state.handle_event("a", {"event": "capture", "key": key, "area_id": "route_29", "level": 3,
                                    "species_id": 16, "hp": 16, "maxHP": 16, "in_box": False})
    return state, cmds


# Party sync is ON for Polished (owner 2026-10-08, after the live write driver passed on overlay cf03f53a:
# docs/polished/LIVE_RESULTS.md): Polished now behaves like vanilla Gen 2 below.

POLISHED_KEY = "AE7343:D1C2:010:00"  # the live run's party Pidgey


def test_a_polished_solo_catch_is_quarantined(tmp_path, monkeypatch):
    adapter = Gen2PolishedAdapter()
    state, cmds = _solo_catch(adapter, tmp_path, monkeypatch, POLISHED_KEY)
    assert "route_29" in state.pending_captures  # still the pending (unlinked) capture
    assert any(c.get("cmd") == "box_mon" and c.get("key") == POLISHED_KEY for c in cmds), cmds


def test_a_vanilla_solo_catch_still_quarantines(tmp_path, monkeypatch):
    adapter = Gen2GSCAdapter("crystal")
    _, cmds = _solo_catch(adapter, tmp_path, monkeypatch, "4E73:D1C2:10")
    assert any(c.get("cmd") == "box_mon" and c.get("key") == "4E73:D1C2:10" for c in cmds), cmds


def _rehello(state, key, other):
    """A reconnect with the pending capture still in the party (the live run 6 reboot shape)."""
    return state.handle_event("a", {"event": "hello", "has_pokeballs": True,
                                    "party": [{"key": key, "hp": 16, "maxHP": 16},
                                              {"key": other, "hp": 30, "maxHP": 30}]})


def test_a_polished_reconnect_re_quarantines(tmp_path, monkeypatch):
    state, _ = _solo_catch(Gen2PolishedAdapter(), tmp_path, monkeypatch, POLISHED_KEY)
    cmds = _rehello(state, POLISHED_KEY, "4A9D3C:D1C2:0A9:00")
    assert any(c.get("cmd") == "box_mon" and c.get("key") == POLISHED_KEY for c in cmds), cmds


def test_a_vanilla_reconnect_still_re_quarantines(tmp_path, monkeypatch):
    state, _ = _solo_catch(Gen2GSCAdapter("crystal"), tmp_path, monkeypatch, "4E73:D1C2:10")
    cmds = _rehello(state, "4E73:D1C2:10", "5A9D:D1C2:A9")
    assert any(c.get("cmd") == "box_mon" and c.get("key") == "4E73:D1C2:10" for c in cmds), cmds


def _memorial_relocation(adapter, tmp_path, key):
    """server.py _check_memorial_box_contamination Check 1: a quarantined capture found in the memorial box."""
    from server.server import SLinkServer
    from server.state import MonInfo
    srv = SLinkServer(data_dir=str(tmp_path))
    srv.adapter = srv.state.adapter = adapter
    srv.state.pending_captures["route_29"] = {"a": MonInfo(key=key, level=3, species=16)}
    entry = {"box": adapter.memorial_box_index, "slot": 0, "key": key, "species_id": 16, "nickname": "Pidgey"}
    srv._check_memorial_box_contamination("a", [entry])
    srv._check_memorial_box_contamination("a", [entry])
    return [c["cmd"] for c in srv.state.queued_commands["a"]]


def test_a_polished_memorial_relocation_queues_like_vanilla(tmp_path, caplog):
    with caplog.at_level("WARNING"):
        assert _memorial_relocation(Gen2PolishedAdapter(), tmp_path, POLISHED_KEY)[:2] == ["party_mon", "box_mon"]
    assert not any("cannot execute box_mon" in r.getMessage() for r in caplog.records)


def test_a_vanilla_memorial_relocation_still_queues(tmp_path):
    assert _memorial_relocation(Gen2GSCAdapter("crystal"), tmp_path, "4E73:D1C2:10")[:2] == ["party_mon", "box_mon"]


@pytest.mark.parametrize("path", ["capture", "reconnect", "memorial"],
                         ids=["capture", "reconnect", "memorial"])
def test_box_paths_do_not_consult_an_obsolete_capability(tmp_path, monkeypatch, path):
    adapter = Gen2PolishedAdapter()

    def obsolete():
        pytest.fail("obsolete supports_box_mon consulted")

    if path != "reconnect":
        monkeypatch.setattr(adapter, "supports_box_mon", obsolete, raising=False)
    if path == "memorial":
        assert _memorial_relocation(adapter, tmp_path, POLISHED_KEY)[:2] == ["party_mon", "box_mon"]
    else:
        state, cmds = _solo_catch(adapter, tmp_path, monkeypatch, POLISHED_KEY)
        if path == "reconnect":
            monkeypatch.setattr(adapter, "supports_box_mon", obsolete, raising=False)
            cmds = _rehello(state, POLISHED_KEY, "4A9D3C:D1C2:0A9:00")
        assert any(c.get("cmd") == "box_mon" and c.get("key") == POLISHED_KEY for c in cmds)
