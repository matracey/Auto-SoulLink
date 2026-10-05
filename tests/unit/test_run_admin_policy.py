from types import SimpleNamespace

import pytest
from aiohttp.test_utils import TestClient, TestServer

from server.server import SLinkServer, _run_public_get, build_app


@pytest.mark.parametrize("path", [
    "/",
    "/timeline",
    "/api/status",
    "/api/events",
    "/api/calc/mons",
    "/stream",
    "/stream/party-a",
    "/calc/normal.html",
])
def test_public_run_reads(path):
    assert _run_public_get(SimpleNamespace(path=path))


@pytest.mark.parametrize("path", [
    "/debug",
    "/twitch",
    "/obs",
    "/patcher",
    "/launcher/a",
    "/api/debug/raw_state",
    "/api/obs/status",
    "/api/bot/status",
    "/companion/radical-red",
])
def test_private_run_reads(path):
    assert not _run_public_get(SimpleNamespace(path=path))


@pytest.mark.asyncio
async def test_run_app_protects_controls_but_keeps_status_public(monkeypatch, tmp_path):
    monkeypatch.setenv("SLINK_ADMIN_KEY", "admin-secret")
    app = build_app(SLinkServer(data_dir=str(tmp_path)))

    async with TestClient(TestServer(app)) as client:
        status = await client.get("/api/status")
        debug = await client.get("/debug", allow_redirects=False)
        reset = await client.post("/api/reset")
        authorized = await client.post(
            "/api/reset",
            headers={"Author" + "ization": "Bearer " + "admin-secret"},
        )

    assert status.status == 200
    assert debug.status == 302 and debug.headers["Location"].startswith("/admin/login?")
    assert reset.status == 401
    assert authorized.status == 200
