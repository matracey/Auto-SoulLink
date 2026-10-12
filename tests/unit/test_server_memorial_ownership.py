from server.server import SLinkServer
from server.state import LinkEntry, LinkStatus, MonInfo


def test_memorial_census_never_uses_other_players_dead_keys(tmp_path):
    server = SLinkServer(data_dir=str(tmp_path))
    collision = "11111111:12345678"
    dead = LinkEntry(area_id="route_1", a=MonInfo(key=collision), b=None, status=LinkStatus.DEAD)
    live = LinkEntry(area_id="route_2", a=None, b=MonInfo(key=collision), status=LinkStatus.ALIVE)
    for entry in (dead, live):
        server.state.links.append(entry)
        server.state._index_entry(entry)
    server._check_memorial_box_contamination("b", [{"key": collision, "box": 0, "slot": 0}])
    assert not server.state.pending_memorials["b"]
    assert not server.state.queued_commands["b"]
    server._check_memorial_box_contamination("a", [{"key": collision, "box": 0, "slot": 0}])
    assert collision in server.state.pending_memorials["a"]
