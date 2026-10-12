"""The panel capability is a fact about a CARTRIDGE and must survive every message.

`connected_players[pid]` is rebuilt from scratch on every inbound line, and `panel` /
`panel_abi` are written only on the hello branch. So the capability lived for exactly one
message: the first tick after connecting erased it, `_player_has_panel` fell through to its
"client predates the capability report" default, and that default asks
`info_panel_width() == 0`. Gen 1 answers 20, so it returned False.

The player-visible result was a panel that showed the run as it stood at connect and never
updated again -- and it looked like a Lua bug, because the client really was painting
faithfully whatever it last received. Gen 3 was immune by accident: it never overrides
`info_panel_width`, so its default is 0 and the fallback answered True.

These tests drive the capability through the same rebuild the message loop performs, rather
than setting `connected_players` and asking the getter -- which is what every existing panel
test does, and why none of them could see this.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from server.adapters.gen1_rby import Gen1Adapter
from server.adapters.gen3_frlge import Gen3Adapter
from server.server import SLinkServer
from server.ui_capabilities import ui_capabilities


@pytest.mark.parametrize("title,variant,supported", [
    ("red_ap", "red", False), ("blue_ap", "blue", False),
    ("red", "red", True), ("blue", "blue", True),
    ("Red", "red", True), ("Blue", "blue", True),
    ("yellow", "yellow", False), ("Yellow", "yellow", False),
])
def test_companion_capabilities_belong_to_the_cartridge(title, variant, supported):
    adapter = Gen1Adapter(rom_type=title)
    assert adapter._enc_variant == variant
    assert adapter.supports_info_panel() is supported
    assert adapter.native_trade_ui() is supported
    assert adapter.info_panel_width() == (20 if supported else 0)
    caps = ui_capabilities(adapter, title)
    assert caps["info_panel"] is supported
    assert caps["info_panel_width"] == (20 if supported else 0)


@pytest.mark.parametrize("title", ["red_ap", "blue_ap"])
def test_generated_ap_capabilities_match_the_adapter(title):
    path = Path(__file__).resolve().parents[1] / "fixtures/ui/capabilities.json"
    saved = json.loads(path.read_text(encoding="utf-8"))[title]
    actual = ui_capabilities(Gen1Adapter(rom_type=title), title)
    assert saved == actual
    assert saved["info_panel"] is False


@pytest.fixture
def srv(tmp_path):
    s = SLinkServer(data_dir=str(tmp_path))
    s.state.adapter = s.adapter = Gen1Adapter(variant="red")
    return s


def _hello(srv, pid, panel=True, abi=3):
    """What the loop does on hello, in the same order."""
    _rebuild(srv, pid, event="hello")
    srv.connected_players[pid]["panel"] = panel
    srv.connected_players[pid]["panel_abi"] = abi


def _rebuild(srv, pid, event="tick"):
    """The connection-info rebuild from SLinkServer.handle_client, reproduced.

    Kept deliberately literal: if the real loop starts carrying another field, this helper
    stops matching it and the test that matters here still measures the right thing.
    """
    prev = srv.connected_players.get(pid, {})
    srv.connected_players[pid] = {
        "connected": True, "last_event": event, "last_seen": "00:00:00",
        "last_seen_ts": 0.0, "rom_type": prev.get("rom_type", "?"),
    }
    for carried in ("panel", "panel_abi"):
        if carried in prev:
            srv.connected_players[pid][carried] = prev[carried]


def test_capability_survives_the_next_message(srv):
    """The regression itself: hello, then one ordinary tick."""
    _hello(srv, "a")
    assert srv._player_has_panel("a") is True
    _rebuild(srv, "a")
    assert srv._player_has_panel("a") is True, (
        "the panel capability was erased by the first non-hello message; the cartridge "
        "would never receive another link_panel payload")


def test_capability_survives_a_long_session(srv):
    _hello(srv, "a")
    for _ in range(50):
        _rebuild(srv, "a")
    assert srv._player_has_panel("a") is True
    assert srv.connected_players["a"]["panel_abi"] == 3, "the ABI was dropped"


def test_an_unpatched_cartridge_stays_unpatched(srv):
    """The load-bearing control. Carrying the field forward must not turn into
    'everyone has a panel' -- a client that says panel=false keeps saying it."""
    _hello(srv, "b", panel=False, abi=0)
    _rebuild(srv, "b")
    assert srv._player_has_panel("b") is False


def test_a_client_that_never_reports_gets_no_panel_on_gen1(srv):
    """Silence is not consent on a generation whose panel comes from a ROM patch:
    an unpatched Gen 1 cartridge has no mailbox to paint into."""
    _rebuild(srv, "c")
    assert srv._player_has_panel("c") is False


def test_gen3_clients_that_never_report_still_get_the_panel(tmp_path):
    """Gen 3's clients predate the capability report and were already receiving the
    panel on the adapter's say-so; the fallback exists for them and must keep working."""
    s = SLinkServer(data_dir=str(tmp_path))
    s.state.adapter = s.adapter = Gen3Adapter(is_rr=True)
    _rebuild(s, "a")
    assert s._player_has_panel("a") is True
