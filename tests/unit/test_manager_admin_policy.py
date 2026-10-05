from types import SimpleNamespace

import pytest

from server.manager import _manager_public_get


@pytest.mark.parametrize("path", [
    "/",
    "/api/runs",
    "/api/status",
    "/broadcast",
    "/stream/party-a",
    "/runs/run_1",
    "/runs/run_1/board",
    "/runs/run_1/timeline",
    "/runs/run_1/calc/normal.html",
    "/runs/run_1/api/status",
    "/runs/run_1/api/events",
    "/runs/run_1/api/calc/mons",
    "/api/runs/run_1/live",
    "/api/runs/run_1/player-pack/a",
    "/api/runs/run_1/player-pack/b",
])
def test_public_manager_reads(path):
    assert _manager_public_get(SimpleNamespace(path=path))


@pytest.mark.parametrize("path", [
    "/new",
    "/tools",
    "/broadcast/obs",
    "/broadcast/twitch",
    "/runs/run_1/debug",
    "/runs/run_1/cartridges",
    "/api/runs/run_1/launcher/a",
    "/api/runs/run_1/rom/a",
    "/api/debug/raw_state",
    "/patcher",
])
def test_private_manager_reads(path):
    assert not _manager_public_get(SimpleNamespace(path=path))
