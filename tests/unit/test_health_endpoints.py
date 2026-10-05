import os

import pytest
from aiohttp.test_utils import TestClient, TestServer

from server import manager
from server.health import writable_directory
from server.server import SLinkServer, build_app


def test_writable_directory_creates_and_removes_probe(tmp_path):
    ok, error = writable_directory(str(tmp_path / "data"))

    assert ok and not error
    assert list((tmp_path / "data").iterdir()) == []


def test_writable_directory_reports_os_error(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "makedirs", lambda *_args, **_kwargs: (_ for _ in ()).throw(
        OSError("read only")))

    assert writable_directory(str(tmp_path)) == (False, "read only")


@pytest.mark.asyncio
async def test_run_health_and_readiness(tmp_path):
    async with TestClient(TestServer(build_app(SLinkServer(data_dir=str(tmp_path))))) as client:
        health = await client.get("/healthz")
        ready = await client.get("/readyz")
        health_body = await health.json()
        ready_body = await ready.json()

    assert health.status == 200 and health_body == {"ok": True}
    assert ready.status == 200 and ready_body == {"ok": True}


@pytest.mark.asyncio
async def test_run_readiness_surfaces_persistence_failure(tmp_path):
    server = SLinkServer(data_dir=str(tmp_path))
    server.state.save_failed = "disk full"
    async with TestClient(TestServer(build_app(server))) as client:
        response = await client.get("/readyz")
        body = await response.json()

    assert response.status == 503
    assert body == {"ok": False, "error": "disk full"}


@pytest.mark.asyncio
async def test_manager_readiness_checks_registry_and_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(manager, "MANAGER_DIR", str(tmp_path))
    monkeypatch.setattr(manager, "REGISTRY_PATH", str(tmp_path / "registry.json"))
    manager.atomic_write_json(manager.REGISTRY_PATH, {"runs": []})
    instance = manager.RunManager("127.0.0.1")

    response = await instance.handle_readyz(None)

    assert response.status == 200
