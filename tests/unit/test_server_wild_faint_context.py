"""Server battle-state enrichment for the wild-faint exemption."""

from server.server import SLinkServer
from server.state import AreaStatus, LinkEntry, LinkStatus, MonInfo


def test_server_marks_faint_as_wild_before_state_dispatch(tmp_path):
    server = SLinkServer(data_dir=str(tmp_path), ignore_wild_faints=True)
    entry = LinkEntry(
        area_id="route_1",
        a=MonInfo(key="A:1", level=5),
        b=MonInfo(key="B:2", level=5),
        status=LinkStatus.ALIVE,
    )
    server.state.links.append(entry)
    server.state._index_entry(entry)
    server.state.area_states["route_1"] = AreaStatus.LINKED
    server.state.party_keys = {"a": {"A:1"}, "b": {"B:2"}}
    server.state.pokeballs_obtained = {"a": True, "b": True}
    server.party_details["a"]["A:1"] = {"level": 5, "hp": 0, "nickname": "A"}
    server.battle_state["a"].update(in_battle=True, is_trainer_battle=False)

    message = {"event": "faint", "key": "A:1", "area_id": "route_1"}
    server._dispatch("a", message)

    assert message["_is_wild_battle"] is True
    assert entry.status == LinkStatus.ALIVE
