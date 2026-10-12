"""Cartridge preparation against everything else the Manager does to a run (sweep cx-ee2e538f).

handle_cartridges read the registry, awaited a provision that can take minutes, then wrote that snapshot back: a run
started meanwhile lost its pid (an unkillable server), a run deleted meanwhile came back, and a re-prepare of a joined
run could switch its pairing kind under the set-once admission lock.
"""
from __future__ import annotations

import asyncio
import json

import pytest

pytest.importorskip("aiohttp", reason="the manager is an aiohttp app")

import os  # noqa: E402

from server import (
    cartridges,  # noqa: E402
    manager as mgr,  # noqa: E402
)
from tests.unit.test_manager_randomize import _Request  # noqa: E402


@pytest.fixture
def manager_dir(tmp_path, monkeypatch):
    d = tmp_path / "runs"
    d.mkdir()
    monkeypatch.setattr(mgr, "MANAGER_DIR", str(d))
    monkeypatch.setattr(mgr, "REGISTRY_PATH", str(d / "registry.json"))
    os.makedirs(d / "run_test", exist_ok=True)
    mgr._save_registry([{"run_id": "run_test", "name": "test", "tcp_port": 1,
                         "http_port": 2, "status": "stopped", "pid": None}])
    return d


async def _post(body, run_id="run_test"):
    m = mgr.RunManager.__new__(mgr.RunManager)
    m.bind_host, m.manager_port, m._run_locks = "127.0.0.1", 0, {}
    resp = await m.handle_cartridges(_Request(run_id, body))
    return resp.status, json.loads(resp.text)

_RESULT = {"family": "gen1_rby", "companion": True,
           "players": {p: {"source": "x", "source_title": "Red", "output": "out", "rom_sha1": "0" * 40,
                           "fingerprint": "", "kind": "companion"} for p in ("a", "b")}}
_BODY = {"rom_a": "not-a-file-a", "rom_b": "not-a-file-b", "companion": True}


@pytest.mark.asyncio
async def test_a_start_while_cartridges_are_made_keeps_its_pid(manager_dir, monkeypatch):
    def provision(*_a, **_k):
        mgr._update_run("run_test", status="running", pid=4242)     # the start (or a poll) lands mid-provision
        return dict(_RESULT)
    monkeypatch.setattr(cartridges, "provision", provision)
    status, body = await _post(_BODY)
    assert status == 200, body
    run = mgr._find_run(mgr._load_registry(), "run_test")
    assert (run["status"], run["pid"]) == ("running", 4242)
    assert run["cartridges"]["players"]["a"]["rom_sha1"] == "0" * 40


@pytest.mark.asyncio
async def test_a_run_deleted_while_cartridges_are_made_stays_deleted(manager_dir, monkeypatch):
    def provision(*_a, **_k):
        mgr._save_registry([r for r in mgr._load_registry() if r["run_id"] != "run_test"])
        return dict(_RESULT)
    monkeypatch.setattr(cartridges, "provision", provision)
    status, _body = await _post(_BODY)
    assert status == 404
    assert mgr._find_run(mgr._load_registry(), "run_test") is None


@pytest.mark.asyncio
async def test_concurrent_cartridges_keep_their_own_settings(manager_dir, monkeypatch):
    from server import upr_settings

    m = mgr.RunManager.__new__(mgr.RunManager)
    m.bind_host, m.manager_port, m._run_locks = "127.0.0.1", 0, {}
    started, second_built, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    seen = []

    def build(spec, **_):
        if spec["marker"] == "second":
            second_built.set()
        return spec["marker"].encode()

    async def worker(fn, *args, **kwargs):
        assert fn is cartridges.provision
        if not seen:
            started.set()
            await asyncio.wait_for(release.wait(), timeout=5)
        with open(kwargs["randomize"]["settings_path"], "rb") as f:
            seen.append(f.read())
        return dict(_RESULT)

    monkeypatch.setattr(upr_settings, "family_spec", lambda spec, family: spec)
    monkeypatch.setattr(upr_settings, "build_spec", build)
    monkeypatch.setattr(mgr.asyncio, "to_thread", worker)
    tasks = []
    try:
        for marker in ("first", "second"):
            body = {**_BODY, "randomize": True, "jar": "test.jar", "spec": {"marker": marker}}
            tasks.append(asyncio.create_task(m.handle_cartridges(_Request("run_test", body))))
            await asyncio.wait_for((started if marker == "first" else second_built).wait(), timeout=5)
    finally:
        release.set()
        responses = await asyncio.gather(*tasks)
    assert all(response.status == 200 for response in responses)
    assert seen == [b"first", b"second"]
    assert not m._run_locks


@pytest.mark.asyncio
async def test_a_joined_run_cannot_switch_between_randomized_and_not(manager_dir, monkeypatch):
    """Players joined a randomized pair, so links.json is committed to 'rand'; plain cartridges would be refused at
    every hello for the rest of the run. Refuse the re-prepare, by name, before anything is written."""
    (manager_dir / "run_test" / "links.json").write_text(json.dumps({"links": [], "artifact_kind": "rand"}))
    called = []
    monkeypatch.setattr(cartridges, "provision", lambda *a, **k: called.append(a) or dict(_RESULT))
    status, body = await _post(_BODY)
    assert status == 409 and "randomized" in body["error"], body
    assert not called


def test_a_registry_entry_without_ports_does_not_break_port_allocation(manager_dir):
    """_next_ports read r["tcp_port"] on every page: a hand-edited entry without one was an opaque 500 everywhere."""
    (manager_dir / "registry.json").write_text(json.dumps({"runs": [{"run_id": "r1", "name": "x"}]}))
    tcp, http = mgr._next_ports(mgr._load_registry())
    assert isinstance(tcp, int) and isinstance(http, int)
