from types import SimpleNamespace

import pytest

from server.board import build_board
from server.server import SLinkServer
from server.state import AreaStatus, EncounterLock, LinkEntry, LinkStatus, MonInfo, SoulLinkState


@pytest.mark.asyncio
async def test_manual_link_clears_stale_encounter_lock(tmp_path):
    server = SLinkServer(data_dir=str(tmp_path))
    server.party_details["a"]["A:1"] = {
        "nickname": "Alpha", "species_id": 1, "level": 5,
    }
    server.party_details["b"]["B:2"] = {
        "nickname": "Beta", "species_id": 2, "level": 5,
    }
    server.state.encounter_locks["mt_moon"] = {
        "b": EncounterLock(species=388, level=14),
    }
    server.state.retry_areas["b"].add("mt_moon")

    async def body():
        return {
            "a_key": "A:1",
            "b_key": "B:2",
            "area_id": "mt_moon",
        }

    response = await server.handle_inject_link_api(SimpleNamespace(json=body))

    assert response.status == 200
    assert "mt_moon" not in server.state.encounter_locks
    assert "mt_moon" not in server.state.retry_areas["b"]


@pytest.mark.asyncio
async def test_register_unpaired_is_display_only(tmp_path):
    server = SLinkServer(data_dir=str(tmp_path))
    server.party_details["b"]["B:ORPHAN"] = {
        "key": "B:ORPHAN", "nickname": "Tirtyboi", "species_id": 564,
        "species_name": "Tirtouga", "level": 18, "hp": 52, "maxHP": 52, "slot": 3,
    }

    async def body():
        return {
            "player": "b", "key": "B:ORPHAN", "area_id": "route_24",
            "consume_area": True,
        }

    response = await server.handle_debug_register_unpaired(SimpleNamespace(json=body))

    assert response.status == 200
    assert server.state.unpaired_mons["route_24"]["b"].nickname == "Tirtyboi"
    assert server.state.area_states["route_24"] == AreaStatus.DEAD_ZONE
    assert server.state.entry_for("b", "B:ORPHAN") is None
    assert server.state.pending_captures == {}
    assert server.state.queued_commands["a"] == []
    assert server.state.queued_commands["b"] == [
        {"cmd": "resolved_areas", "areas": ["route_24"]},
    ]

    status = server._build_status_dict()
    section = dict(build_board(status)["sections"])["unpaired"]
    assert section[0]["b"]["where"] == "party"
    assert section[0]["b"]["nickname"] == "Tirtyboi"


@pytest.mark.asyncio
async def test_unlink_can_preserve_surviving_half(tmp_path):
    server = SLinkServer(data_dir=str(tmp_path))
    entry = LinkEntry(
        area_id="route_24",
        a=MonInfo(key="A:LOST", nickname="Lost"),
        b=MonInfo(key="B:KEPT", nickname="Kept"),
        status=LinkStatus.ALIVE,
    )
    server.state.links.append(entry)
    server.state._index_entry(entry)

    async def body():
        return {"area_id": "route_24", "preserve": ["b"]}

    response = await server.handle_debug_unlink(SimpleNamespace(json=body))

    assert response.status == 200
    assert not server.state.links
    assert server.state.unpaired_mons["route_24"]["b"].key == "B:KEPT"
    assert server.state.area_states["route_24"] == AreaStatus.DEAD_ZONE
    assert server.state.queued_commands == {"a": [], "b": []}


@pytest.mark.asyncio
@pytest.mark.parametrize("preserved", ["a", "b"])
async def test_unlink_preservation_cancels_only_that_players_retirement(tmp_path, preserved):
    server = SLinkServer(data_dir=str(tmp_path))
    state = server.state
    key = "AB:CD"
    entry = LinkEntry(area_id="route_1", a=MonInfo(key=key), b=MonInfo(key=key),
                      status=LinkStatus.DEAD)
    state.links.append(entry)
    state._index_entry(entry)
    retire = [{"cmd": cmd, "key": key} for cmd in ("force_faint", "force_explode", "memorialize")]
    keep = [{"cmd": "hud_show", "key": key, "text": "Keep"},
            {"cmd": "memorialize", "key": "EF:01"}]
    for pid in ("a", "b"):
        state.queued_commands[pid] = retire + keep
        state.pending_memorials[pid] = {key, "EF:01"}

    async def body():
        return {"area_id": "route_1", "preserve": [preserved]}

    response = await server.handle_debug_unlink(SimpleNamespace(json=body))

    assert response.status == 200
    other = "b" if preserved == "a" else "a"
    assert state.queued_commands[preserved] == keep
    assert state.queued_commands[other] == retire + keep
    assert state.pending_memorials[preserved] == {"EF:01"}
    assert state.pending_memorials[other] == {key, "EF:01"}
    loaded = SoulLinkState.load(data_dir=str(tmp_path))
    assert loaded.pending_memorials == state.pending_memorials
    assert loaded.unpaired_mons["route_1"][preserved].key == key
