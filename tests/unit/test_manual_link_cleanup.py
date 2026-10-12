import json
from types import SimpleNamespace

import pytest

from server.board import build_board
from server.server import SLinkServer
from server.state import AreaStatus, EncounterLock, LinkEntry, LinkStatus, MonInfo, SoulLinkState


def _slot_server(tmp_path):
    server = SLinkServer(data_dir=str(tmp_path))
    for pid, ot in (("a", "11111111"), ("b", "22222222")):
        server.party_details[pid] = {
            f"FFFFFFFF:{ot}": {"slot": 0, "species_id": 1},
            f"00000001:{ot}": {"slot": 2, "species_id": 2},
        }
        server.state.party_keys[pid] = set(server.party_details[pid])
    return server


@pytest.mark.asyncio
@pytest.mark.parametrize("slot,prefix", [(0, "FFFFFFFF"), (2, "00000001")])
async def test_slot_link_uses_physical_slots_not_sorted_keys(tmp_path, slot, prefix):
    server = _slot_server(tmp_path)

    async def body():
        return {"a_slot": slot, "b_slot": slot, "area_id": "route_1"}

    response = await server.handle_inject_link_by_slot_api(SimpleNamespace(json=body))
    assert response.status == 200 and json.loads(response.text)["ok"]
    entry, = server.state.links
    assert entry.a.key == f"{prefix}:11111111" and entry.b.key == f"{prefix}:22222222"


@pytest.mark.asyncio
@pytest.mark.parametrize("player", ["a", "b"])
@pytest.mark.parametrize("slot", [-1, 1, 6, True, 1.5, "0", None])
async def test_invalid_or_missing_physical_slot_cannot_link(tmp_path, player, slot):
    server = _slot_server(tmp_path)

    async def body():
        return {f"{player}_slot": slot}

    response = await server.handle_inject_link_by_slot_api(SimpleNamespace(json=body))
    assert response.status == 400 and not json.loads(response.text)["ok"]
    assert not server.state.links


@pytest.mark.asyncio
@pytest.mark.parametrize("problem", ["duplicate", "stale"])
async def test_uncertain_physical_slot_cannot_link(tmp_path, problem):
    server = _slot_server(tmp_path)
    if problem == "duplicate":
        server.party_details["a"]["00000001:11111111"]["slot"] = 0
    else:
        server.state.party_keys["a"].remove("FFFFFFFF:11111111")

    async def body():
        return {"a_slot": 0, "b_slot": 0}

    response = await server.handle_inject_link_by_slot_api(SimpleNamespace(json=body))
    assert response.status == 400 and not server.state.links


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
@pytest.mark.parametrize("linked_player", ["a", "b"])
@pytest.mark.parametrize("force", [False, True])
async def test_override_preserves_old_pair_until_conflicts_are_accepted(tmp_path, linked_player, force):
    server = SLinkServer(data_dir=str(tmp_path))
    state = server.state
    entry = LinkEntry(area_id="route_1", a=MonInfo(key="A:OLD"), b=MonInfo(key="B:OLD"),
                      status=LinkStatus.ALIVE)
    state.links.append(entry)
    state._index_entry(entry)
    pending_player = "b" if linked_player == "a" else "a"
    pending = MonInfo(key=f"{pending_player.upper()}:PENDING", nickname="Pending")
    state.pending_captures["route_2"] = {pending_player: pending}
    state.area_states = {
        "route_1": AreaStatus.LINKED,
        "route_2": AreaStatus.PENDING_A if pending_player == "b" else AreaStatus.PENDING_B,
    }
    state.encounter_locks["route_2"] = {pending_player: EncounterLock(species=1, level=5)}
    state._save()
    before = (tmp_path / "links.json").read_bytes()
    keys = {"a": "A:OLD", "b": "B:OLD"}
    keys[pending_player] = pending.key

    async def body():
        return {"a_key": keys["a"], "b_key": keys["b"], "area_id": "route_3",
                "override": True, "force": force}

    response = await server.handle_inject_link_api(SimpleNamespace(json=body))
    result = json.loads(response.text)
    assert response.status == 200
    if not force:
        assert result["requires_force"] and not result["ok"]
        assert state.links == [entry]
        assert state.entry_for("a", "A:OLD") is entry
        assert state.entry_for("b", "B:OLD") is entry
        state._save()
        assert (tmp_path / "links.json").read_bytes() == before
    else:
        assert result["ok"]
        assert entry not in state.links
        assert len(state.links) == 1 and state.links[0].area_id == "route_3"
        assert state.entry_for("a", keys["a"]) is state.links[0]
        assert state.entry_for("b", keys["b"]) is state.links[0]
        assert state.area_states["route_1"] == AreaStatus.UNSEEN
        assert "route_2" not in state.pending_captures


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


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [LinkStatus.DEAD, LinkStatus.MEMORIAL])
async def test_revive_cancels_retirement_before_next_delivery(tmp_path, status):
    server = SLinkServer(data_dir=str(tmp_path))
    state = server.state
    key = "AB:CD"
    entry = LinkEntry(area_id="route_1", a=MonInfo(key=key), b=MonInfo(key=key), status=status)
    state.links.append(entry)
    state._index_entry(entry)
    keep = [{"cmd": "hud_show", "key": key, "text": "Keep"},
            {"cmd": "memorialize", "key": "EF:01"}]
    for pid in ("a", "b"):
        state.queued_commands[pid] = [
            {"cmd": cmd, "key": key} for cmd in ("force_faint", "force_explode", "memorialize")
        ] + keep
        state.pending_memorials[pid] = {key, "EF:01"}

    async def body():
        return {"area_id": "route_1"}

    response = await server.handle_debug_revive(SimpleNamespace(json=body))

    assert response.status == 200
    assert entry.status == LinkStatus.ALIVE
    for pid in ("a", "b"):
        assert state.pending_memorials[pid] == {"EF:01"}
        assert state._deliver_commands(pid) == keep
    loaded = SoulLinkState.load(data_dir=str(tmp_path))
    assert loaded.links[0].status == LinkStatus.ALIVE
    assert loaded.pending_memorials == state.pending_memorials
