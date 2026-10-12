import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from server.admin_auth import setup_admin_auth
from server.server import SLinkServer
from tests.unit.test_limbo_state import A, B, linked, missing


@pytest.fixture
def server(tmp_path):
    srv = SLinkServer(data_dir=str(tmp_path))
    srv.state = linked(tmp_path)
    revision = missing(srv.state)
    return srv, {"player": "a", "key": A, "revision": revision, "action": "detach"}


def app_for(srv):
    app = web.Application()
    setup_admin_auth(app, lambda request: request.path == "/api/status")
    app.router.add_post("/api/recovery", srv.handle_recovery)
    app.router.add_get("/api/status", srv.handle_status_json)
    return app


@pytest.mark.asyncio
async def test_recovery_endpoint_requires_admin_and_refuses_stale_incidents(server, monkeypatch):
    srv, body = server
    monkeypatch.setenv("SLINK_ADMIN_KEY", "limbo-test-only-key")
    auth = {"Authorization": "Bearer limbo-test-only-key"}
    async with TestClient(TestServer(app_for(srv))) as client:
        assert (await client.post("/api/recovery", json=body)).status == 401
        assert (await client.post("/api/recovery", json={**body, "revision": "stale"}, headers=auth)).status == 409
        response = await client.post("/api/recovery", json=body, headers=auth)
        assert response.status == 200 and (await response.json())["ok"]
        assert (await client.post("/api/recovery", json=body, headers=auth)).status == 409
        assert srv.state.unpaired_mons["route_1"]["b"].key == B


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [None, [], {"player": []}, {"action": "unknown"}, {"key": 7}])
async def test_malformed_resolution_is_rejected_without_changes(server, monkeypatch, body):
    monkeypatch.delenv("SLINK_ADMIN_KEY", raising=False)
    srv, _ = server
    async with TestClient(TestServer(app_for(srv))) as client:
        response = await client.post("/api/recovery", json=body)
        assert response.status == 400
        assert A in srv.state.recovery.limbo["a"]


@pytest.mark.asyncio
async def test_resolution_write_failure_is_explicit_and_retryable(server, monkeypatch):
    monkeypatch.delenv("SLINK_ADMIN_KEY", raising=False)
    srv, body = server
    save = srv.state._atomic_write_json

    def fail(*_):
        raise OSError("disk unavailable")

    monkeypatch.setattr(srv.state, "_atomic_write_json", fail)
    async with TestClient(TestServer(app_for(srv))) as client:
        response = await client.post("/api/recovery", json=body)
        assert response.status == 503 and not (await response.json())["ok"]
        assert A in srv.state.recovery.limbo["a"]
        monkeypatch.setattr(srv.state, "_atomic_write_json", save)
        assert (await client.post("/api/recovery", json=body)).status == 200
