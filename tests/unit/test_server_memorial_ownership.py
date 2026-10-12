import pytest

from server.adapters import get_adapter
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


def add_burials(server, player, count, status=LinkStatus.MEMORIAL):
    keys = []
    for i in range(count):
        key = f"{i + 1:08X}:12345678"
        entry = LinkEntry(area_id=f"{player}_route_{i}", status=status,
                          a=MonInfo(key=key) if player == "a" else None,
                          b=MonInfo(key=key) if player == "b" else None)
        server.state.links.append(entry)
        server.state._index_entry(entry)
        keys.append(key)
    return keys


@pytest.mark.parametrize("player", ["a", "b"])
@pytest.mark.parametrize("status", [LinkStatus.DEAD, LinkStatus.MEMORIAL])
def test_valid_gen5_overflow_burials_are_not_requeued(tmp_path, player, status):
    server = SLinkServer(data_dir=str(tmp_path))
    server.adapter = get_adapter("gen5_bw")
    capacity, primary = server.adapter.mons_per_box, server.adapter.memorial_box_index
    keys = add_burials(server, player, 2 * capacity + 1, status)
    boxes = [{"key": key, "box": primary - i // capacity, "slot": i % capacity}
             for i, key in enumerate(keys)]
    for _ in range(3):
        server._check_memorial_box_contamination(player, boxes)
    assert not server.state.queued_commands[player]
    assert not server.state.pending_memorials[player]
    server._check_memorial_box_contamination(player, [{"key": keys[-1], "box": primary - 3}])
    assert any(command["cmd"] == "memorialize" and command["key"] == keys[-1]
               for command in server.state.queued_commands[player])


@pytest.mark.parametrize("player", ["a", "b"])
def test_partner_overflow_does_not_make_own_regular_box_a_memorial(tmp_path, player):
    server = SLinkServer(data_dir=str(tmp_path))
    server.adapter = get_adapter("gen5_bw")
    add_burials(server, "b" if player == "a" else "a", server.adapter.mons_per_box + 1)
    key = add_burials(server, player, 1)[0]
    server._check_memorial_box_contamination(
        player, [{"key": key, "box": server.adapter.memorial_box_index - 1}])
    assert key in server.state.pending_memorials[player]
