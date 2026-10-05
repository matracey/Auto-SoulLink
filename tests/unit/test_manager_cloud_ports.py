import asyncio

import pytest

from server import manager


@pytest.fixture
def cloud_ports(monkeypatch):
    monkeypatch.setenv("SLINK_TCP_PORT_POOL", "54321,54322,54323,54324,54325")
    monkeypatch.setattr(manager, "_port_free", lambda _port: True)


def test_cloud_pool_allocates_only_configured_ports(cloud_ports):
    assert manager._next_ports([]) == (54321, 8081)


def test_stopped_runs_do_not_hold_cloud_ports(cloud_ports):
    runs = [
        {"status": "stopped", "tcp_port": 54321, "http_port": 8081},
        {"status": "running", "tcp_port": 54322, "http_port": 8082},
    ]

    assert manager._next_ports(runs) == (54321, 8081)


def test_sixth_simultaneous_run_is_refused(cloud_ports):
    runs = [
        {"status": "running", "tcp_port": 54321 + index, "http_port": 8081 + index}
        for index in range(5)
    ]

    with pytest.raises(RuntimeError, match="All 5 configured game ports"):
        manager._next_ports(runs)


def test_cloud_stop_keeps_preferred_ports(cloud_ports):
    assert manager._released_ports() == {}


def test_local_mode_keeps_existing_unbounded_allocation(monkeypatch):
    monkeypatch.delenv("SLINK_TCP_PORT_POOL", raising=False)
    monkeypatch.setattr(manager, "_port_free", lambda _port: True)
    runs = [{"status": "stopped", "tcp_port": 54321, "http_port": 8081}]

    assert manager._next_ports(runs) == (54322, 8082)
    assert manager._released_ports() == {}


@pytest.mark.parametrize("value", ["54321,54321", "0", "abc", ","])
def test_invalid_pool_is_refused(monkeypatch, value):
    monkeypatch.setenv("SLINK_TCP_PORT_POOL", value)

    with pytest.raises(RuntimeError, match="SLINK_TCP_PORT_POOL"):
        manager._tcp_port_pool()


def test_desired_status_migrates_from_observed_status():
    running = {"status": "running"}
    stopped = {"status": "stopped"}

    assert manager._ensure_desired_status(running) is True
    assert manager._ensure_desired_status(stopped) is True
    assert running["desired_status"] == "running"
    assert stopped["desired_status"] == "stopped"
    assert manager._ensure_desired_status(running) is False


@pytest.mark.asyncio
async def test_desired_run_is_restored_after_stale_pid(monkeypatch, tmp_path, cloud_ports):
    monkeypatch.setattr(manager, "MANAGER_DIR", str(tmp_path))
    monkeypatch.setattr(manager, "REGISTRY_PATH", str(tmp_path / "registry.json"))
    monkeypatch.setattr(manager, "_is_alive", lambda *_args: False)
    monkeypatch.setattr(manager, "_create_time", lambda _pid: 123.0)
    run = {
        "run_id": "run_1",
        "name": "Run",
        "created_at": "2026-01-01T00:00:00+00:00",
        "status": "running",
        "desired_status": "running",
        "pid": 99,
        "pid_created": 1.0,
        "tcp_port": 54321,
        "http_port": 8081,
        "instrumentation_keys": {"a": "a-key", "b": "b-key"},
    }
    manager.atomic_write_json(manager.REGISTRY_PATH, {"runs": [run]})
    (tmp_path / "run_1").mkdir()

    async def spawn(restored, *_args, **_kwargs):
        assert restored["tcp_port"] == 54321
        assert restored["http_port"] == 8081
        return 4242

    monkeypatch.setattr(manager, "_spawn_run", spawn)
    instance = manager.RunManager("0.0.0.0")

    await instance.restore_desired_runs()

    restored = manager._load_registry()[0]
    assert restored["status"] == "running"
    assert restored["desired_status"] == "running"
    assert restored["pid"] == 4242
    assert restored["pid_created"] == 123.0


@pytest.mark.asyncio
async def test_restart_falls_back_only_when_preferred_port_is_busy(
        monkeypatch, tmp_path, cloud_ports):
    monkeypatch.setattr(manager, "MANAGER_DIR", str(tmp_path))
    monkeypatch.setattr(manager, "REGISTRY_PATH", str(tmp_path / "registry.json"))
    monkeypatch.setattr(manager, "_create_time", lambda _pid: 123.0)
    monkeypatch.setattr(manager, "_is_alive", lambda *_args: False)
    runs = [
        {
            "run_id": "preferred",
            "status": "stopped",
            "desired_status": "stopped",
            "pid": None,
            "tcp_port": 54321,
            "http_port": 8081,
        },
        {
            "run_id": "active",
            "status": "running",
            "desired_status": "running",
            "pid": 88,
            "tcp_port": 54321,
            "http_port": 8081,
        },
    ]
    manager.atomic_write_json(manager.REGISTRY_PATH, {"runs": runs})
    (tmp_path / "preferred").mkdir()
    assigned = []

    async def spawn(run, *_args, **_kwargs):
        assigned.append((run["tcp_port"], run["http_port"]))
        return 4242

    monkeypatch.setattr(manager, "_spawn_run", spawn)
    instance = manager.RunManager("0.0.0.0")

    pid, error = await instance._start_run("preferred")

    assert (pid, error) == (4242, "")
    assert assigned == [(54322, 8082)]


@pytest.mark.asyncio
async def test_busy_internal_http_port_does_not_change_preferred_tcp(
        monkeypatch, tmp_path, cloud_ports):
    monkeypatch.setattr(manager, "MANAGER_DIR", str(tmp_path))
    monkeypatch.setattr(manager, "REGISTRY_PATH", str(tmp_path / "registry.json"))
    monkeypatch.setattr(manager, "_create_time", lambda _pid: 123.0)
    monkeypatch.setattr(manager, "_is_alive", lambda *_args: False)
    monkeypatch.setattr(manager, "_port_free", lambda port: port != 8082)
    run = {
        "run_id": "preferred",
        "status": "stopped",
        "desired_status": "stopped",
        "pid": None,
        "tcp_port": 54322,
        "http_port": 8082,
    }
    manager.atomic_write_json(manager.REGISTRY_PATH, {"runs": [run]})
    (tmp_path / "preferred").mkdir()
    assigned = []

    async def spawn(started, *_args, **_kwargs):
        assigned.append((started["tcp_port"], started["http_port"]))
        return 4242

    monkeypatch.setattr(manager, "_spawn_run", spawn)
    instance = manager.RunManager("0.0.0.0")

    pid, error = await instance._start_run("preferred")

    assert (pid, error) == (4242, "")
    assert assigned == [(54322, 8081)]


@pytest.mark.asyncio
async def test_concurrent_restoration_assigns_unique_ports(monkeypatch, tmp_path, cloud_ports):
    monkeypatch.setattr(manager, "MANAGER_DIR", str(tmp_path))
    monkeypatch.setattr(manager, "REGISTRY_PATH", str(tmp_path / "registry.json"))
    monkeypatch.setattr(manager, "_create_time", lambda pid: float(pid))
    runs = [
        {
            "run_id": f"run_{index}",
            "name": f"Run {index}",
            "created_at": f"2026-01-0{index + 1}T00:00:00+00:00",
            "status": "stopped",
            "desired_status": "running",
            "pid": None,
            "tcp_port": None,
            "http_port": None,
            "instrumentation_keys": {"a": f"a-{index}", "b": f"b-{index}"},
        }
        for index in range(2)
    ]
    manager.atomic_write_json(manager.REGISTRY_PATH, {"runs": runs})
    for run in runs:
        (tmp_path / run["run_id"]).mkdir()
    assigned = []

    async def spawn(run, *_args, **_kwargs):
        assigned.append((run["tcp_port"], run["http_port"]))
        await asyncio.sleep(0)
        return 5000 + len(assigned)

    monkeypatch.setattr(manager, "_spawn_run", spawn)
    instance = manager.RunManager("0.0.0.0")

    await instance.restore_desired_runs()

    assert len(set(assigned)) == 2
    restored = manager._load_registry()
    assert {run["status"] for run in restored} == {"running"}
