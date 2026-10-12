from unittest.mock import AsyncMock

import pytest

from server.server import SLinkServer
from tests.unit.test_obs_priority import make_controller
from tests.unit.test_party_snapshot_dispatch import legacy_hello, party
from tests.unit.test_state import _add_link


def admit_pair(server):
    for player in ("a", "b"):
        message = legacy_hello(party())
        message.update(player=player, has_pokeballs=True)
        assert any(command["cmd"] == "config" for command in server._dispatch(player, message))
    key = party()[0]["key"]
    _add_link(server.state, key, key, area="route_1", a_in_party=True, b_in_party=True)


def controller():
    return make_controller([
        {"id": "end", "event": "run_over", "player_filter": "any",
         "target": "both", "scene": "GAME OVER"},
        {"id": "walk", "event": "area_enter", "player_filter": "any",
         "target": "own", "scene": "WALK"},
    ])


def end_run(server, player):
    commands = server._dispatch(player, {"event": "whiteout"})
    assert any(command["cmd"] == "game_over" for command in commands)
    assert server.state.run_over
    for queue in server.obs._queues.values():
        assert queue.get_nowait() == "GAME OVER"
    for side in ("a", "b", "a", "b"):
        server._dispatch(side, {"event": "tick"})
    assert all(queue.empty() for queue in server.obs._queues.values())
    server._dispatch(player, {"event": "area_enter", "area_id": "route_2"})
    assert server.obs._queues[player].get_nowait() == "WALK"


@pytest.mark.asyncio
@pytest.mark.parametrize("player", ["a", "b"])
@pytest.mark.parametrize("recovery", ["revive", "reset"])
async def test_run_over_scene_fires_once_and_rearms_after_recovery(tmp_path, player, recovery):
    server = SLinkServer(data_dir=str(tmp_path))
    admit_pair(server)
    server.obs = controller()
    end_run(server, player)
    if recovery == "revive":
        request = AsyncMock(json=AsyncMock(return_value={"area_id": "route_1"}))
        assert (await server.handle_debug_revive(request)).status == 200
    else:
        assert (await server.handle_reset_api(None)).status == 200
        admit_pair(server)
    assert not server.state.run_over
    end_run(server, player)


def test_ended_run_does_not_retrigger_scene_after_server_restart(tmp_path):
    server = SLinkServer(data_dir=str(tmp_path))
    admit_pair(server)
    server._dispatch("a", {"event": "whiteout"})
    assert server.state.run_over
    restarted = SLinkServer(data_dir=str(tmp_path))
    assert restarted.state.run_over
    restarted.obs = controller()
    message = legacy_hello([])
    message["has_pokeballs"] = True
    restarted._dispatch("a", message)
    restarted._dispatch("a", {"event": "tick"})
    assert all(queue.empty() for queue in restarted.obs._queues.values())
