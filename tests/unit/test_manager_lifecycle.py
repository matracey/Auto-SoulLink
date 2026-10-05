"""Run Manager lifecycle: start/stop serialize per run, a run is "running" only once its
server answers, and a pid is trusted (or killed) only while its create time still matches.
Every child here is a fake: no server.py, no emulator."""

import asyncio
import os
import stat
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest

import server.manager as manager

pytest_plugins = ["tests.unit.manager_harness"]


def _stopped_run(**extra):
    run = {"run_id": "r1", "name": "Duo", "tcp_port": 1, "http_port": 2, "status": "stopped", "pid": None}
    run.update(extra)
    manager._save_registry([run])


def _registry_run():
    return manager._find_run(manager._load_registry(), "r1")


def _blocking_spawn(monkeypatch):
    """A _spawn_run that parks until released; returns (spawn log, release event)."""
    spawns, release = [], asyncio.Event()

    async def spawn(run, host, manager_port=0):
        spawns.append(run["run_id"])
        await release.wait()
        return 4242
    monkeypatch.setattr(manager, "_spawn_run", spawn)
    monkeypatch.setattr(manager, "_create_time", lambda pid: 100.0)
    monkeypatch.setattr(manager, "_is_alive", lambda pid, created=None: pid == 4242)
    return spawns, release


@pytest.mark.asyncio
async def test_two_concurrent_starts_spawn_one_server(manager_client, monkeypatch):
    _stopped_run()
    spawns, release = _blocking_spawn(monkeypatch)
    first = asyncio.ensure_future(manager_client.post("/api/runs/r1/start"))
    second = asyncio.ensure_future(manager_client.post("/api/runs/r1/start"))
    await asyncio.sleep(0.1)
    release.set()
    bodies = [await (await r).json() for r in (first, second)]
    assert spawns == ["r1"]
    assert all(b["ok"] for b in bodies)
    assert _registry_run()["status"] == "running" and _registry_run()["pid"] == 4242


@pytest.mark.asyncio
async def test_a_stop_during_a_start_ends_stopped_and_kills_the_child(manager_client, monkeypatch):
    _stopped_run()
    _spawns, release = _blocking_spawn(monkeypatch)
    killed = []
    monkeypatch.setattr(manager, "_kill_run", lambda pid, created=None: killed.append((pid, created)) or True)
    start = asyncio.ensure_future(manager_client.post("/api/runs/r1/start"))
    await asyncio.sleep(0.05)
    stop = asyncio.ensure_future(manager_client.post("/api/runs/r1/stop"))
    await asyncio.sleep(0.05)
    release.set()
    assert (await (await start).json())["ok"]
    assert (await (await stop).json())["ok"]
    assert killed == [(4242, 100.0)]
    run = _registry_run()
    assert run["status"] == "stopped" and run["pid"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["write", "unreadable", "removed"])
@pytest.mark.parametrize("stopped", [True, False])
async def test_unpublished_ready_child_is_stopped(manager_dir, monkeypatch, failure, stopped):
    _stopped_run()
    instance = manager.RunManager("127.0.0.1")
    killed = []

    async def spawn(*args, **kwargs):
        return 4242

    def publish(*args, **kwargs):
        if failure == "removed":
            manager._save_registry([])
            return None
        if failure == "unreadable":
            raise manager.RegistryError("registry unreadable")
        raise OSError("registry write failed")

    def kill(pid, created):
        killed.append((pid, created))
        return stopped

    monkeypatch.setattr(manager, "_spawn_run", spawn)
    monkeypatch.setattr(manager, "_create_time", lambda pid: 100.0)
    monkeypatch.setattr(manager, "_update_run", publish)
    monkeypatch.setattr(manager, "_kill_run", kill)
    pid, error = await instance._start_run("r1")
    assert pid is None and error
    assert killed == [(4242, 100.0)]
    if not stopped:
        assert "could not stop" in error and "4242" in error
    run = _registry_run()
    assert run is None if failure == "removed" else run["pid"] is None
    assert not instance._port_lease_lock().locked()


def _fake_child(monkeypatch, *, exits_after: float | None, log_line: str = ""):
    """A child that never answers HTTP; it exits (code 1) after `exits_after` s, or never."""
    killed = []

    async def create(*_cmd, **kwargs):
        if log_line:
            kwargs["stderr"].write(log_line.encode())
        child = SimpleNamespace(pid=4242, returncode=None, kill=lambda: killed.append(4242))

        async def wait():
            if exits_after is None:
                await asyncio.Event().wait()
            await asyncio.sleep(exits_after)
            child.returncode = 1
            return 1
        child.wait = wait
        return child

    async def silent(*_args):
        return False
    monkeypatch.setattr(manager.asyncio, "create_subprocess_exec", create)
    monkeypatch.setattr(manager, "_http_ready", silent, raising=False)
    monkeypatch.setattr(manager, "SPAWN_POLL_S", 0.02, raising=False)
    return killed


@pytest.mark.asyncio
async def test_a_child_that_dies_after_the_old_grace_is_an_error_not_running(manager_client, monkeypatch):
    """Alive at 1.5 s is not ready: a server that dies later (a slow import, then a taken
    HTTP port) must not leave the registry saying running."""
    _stopped_run()
    _fake_child(monkeypatch, exits_after=1.7, log_line="Cannot listen on HTTP 127.0.0.1:2 -- in use\n")
    response = await manager_client.post("/api/runs/r1/start")
    body = await response.json()
    assert response.status == 500 and not body["ok"]
    assert "exited on startup" in body["error"] and "Cannot listen on HTTP" in body["error"]
    assert _registry_run()["status"] == "stopped" and _registry_run()["pid"] is None


@pytest.mark.asyncio
async def test_a_child_that_never_answers_is_killed_and_reported(manager_dir, monkeypatch):
    killed = _fake_child(monkeypatch, exits_after=None)
    monkeypatch.setattr(manager, "SPAWN_READY_S", 0.1)
    with pytest.raises(RuntimeError, match="did not answer on HTTP 2"):
        await manager._spawn_run({"run_id": "r1", "tcp_port": 1, "http_port": 2}, "0.0.0.0")
    assert killed == [4242]


def _fake_psutil(monkeypatch, *, started: float, terminate_fails: bool = False):
    """psutil with one live process, pid 4242, started at `started`."""
    terminated = []

    class Error(Exception):
        pass

    class AccessDenied(Error):
        pass

    class Process:
        def __init__(self, pid):
            if pid != 4242:
                raise Error(pid)

        def create_time(self):
            return started

        def terminate(self):
            if terminate_fails:
                raise AccessDenied("denied")
            terminated.append(4242)

        kill = terminate

        def wait(self, timeout=None):
            pass

    monkeypatch.setattr(manager, "psutil", SimpleNamespace(
        Process=Process, Error=Error, TimeoutExpired=Error, pid_exists=lambda pid: pid == 4242), raising=False)
    monkeypatch.setattr(manager, "PSUTIL_AVAILABLE", True)
    return terminated


@pytest.mark.asyncio
async def test_a_reused_pid_is_not_killed(manager_client, monkeypatch):
    """The recorded pid now belongs to a process started at another time: not ours."""
    _stopped_run(status="running", pid=4242, pid_created=100.0)
    terminated = _fake_psutil(monkeypatch, started=200.0)
    response = await manager_client.post("/api/runs/r1/stop")
    assert (await response.json())["ok"]
    assert terminated == []
    assert _registry_run()["status"] == "stopped" and _registry_run()["pid"] is None


@pytest.mark.asyncio
async def test_a_kill_that_fails_reports_an_error_and_keeps_the_pid(manager_client, monkeypatch):
    _stopped_run(status="running", pid=4242, pid_created=100.0)
    _fake_psutil(monkeypatch, started=100.0, terminate_fails=True)
    response = await manager_client.post("/api/runs/r1/stop")
    body = await response.json()
    assert response.status == 500 and not body["ok"] and "4242" in body["error"]
    assert _registry_run()["status"] == "running" and _registry_run()["pid"] == 4242


@pytest.mark.asyncio
async def test_the_board_poll_reconciles_a_dead_pid(manager_client, monkeypatch):
    _stopped_run(status="running", pid=4242, pid_created=100.0)
    monkeypatch.setattr(manager, "_is_alive", lambda pid, created=None: False)
    assert (await manager_client.get("/runs/r1/board")).status == 200
    assert _registry_run()["status"] == "stopped" and _registry_run()["pid"] is None


@pytest.mark.asyncio
async def test_new_run_form_is_accessible(manager_client):
    body = await (await manager_client.get("/new")).text()
    games = body[body.index('id="game-label"'):]
    assert 'role="radiogroup" aria-labelledby="game-label"' in games
    assert 'role="radio" :aria-checked="draft.game === g.key"' in games
    assert '@keydown="radioKeys($event)"' in games
    assert '<span class="mk-sub" role="alert" x-text="error"' in body
    assert ":aria-describedby=\"draft.name.trim() ? null : 'run-name-hint'\"" in body
    assert 'id="run-name-hint"' in body and ':aria-invalid="tried && !draft.name.trim()"' in body
    assert ":aria-describedby=\"'opt-' + key + '-desc'" in body
    assert ":id=\"'opt-' + key + '-desc'\"" in body and ":id=\"'opt-' + key + '-why'\"" in body


# -- archive / delete: never orphan a live server, never report a partial delete as done --
@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["archive", "delete"])
async def test_a_kill_failure_leaves_the_run_untouched(manager_client, manager_dir, monkeypatch, action):
    _stopped_run(status="running", pid=4242, pid_created=100.0)
    (manager_dir / "r1").mkdir()
    (manager_dir / "r1" / "links.json").write_text("{}")
    _fake_psutil(monkeypatch, started=100.0, terminate_fails=True)
    response = await manager_client.post(f"/api/runs/r1/{action}")
    assert response.status == 500 and not (await response.json())["ok"]
    assert _registry_run()["status"] == "running" and _registry_run()["pid"] == 4242
    assert (manager_dir / "r1" / "links.json").exists()


@pytest.mark.asyncio
async def test_a_locked_file_makes_delete_fail_and_keep_the_entry(manager_client, manager_dir, monkeypatch):
    _stopped_run()
    (manager_dir / "r1").mkdir()
    (manager_dir / "r1" / "links.json").write_text("{}")
    real_unlink = os.unlink

    def locked(path, *args, **kwargs):          # another process holds links.json open
        if str(path).endswith("links.json"):
            raise PermissionError(13, "in use", str(path))
        return real_unlink(path, *args, **kwargs)
    monkeypatch.setattr(os, "unlink", locked)
    response = await manager_client.post("/api/runs/r1/delete")
    body = await response.json()
    assert response.status == 500 and not body["ok"] and "links.json" in body["error"]
    assert _registry_run() is not None


@pytest.mark.asyncio
async def test_a_clean_delete_removes_the_data_and_the_entry(manager_client, manager_dir):
    """A read-only file (Drive, git objects) is cleared and removed, not left behind."""
    _stopped_run()
    (manager_dir / "r1").mkdir()
    readonly = manager_dir / "r1" / "links.json"
    readonly.write_text("{}")
    os.chmod(readonly, stat.S_IREAD)
    response = await manager_client.post("/api/runs/r1/delete")
    assert (await response.json())["ok"]
    assert not (manager_dir / "r1").exists()
    assert manager._load_registry() == []


# -- the run header's actions: single-flight, failures in place, a gone run is not a 404 page --
@pytest.mark.asyncio
async def test_run_actions_are_single_flight_and_report_in_place(manager_client):
    _stopped_run()
    body = await (await manager_client.get("/runs/r1")).text()
    act = body[body.index("async act(what, confirmText)"):body.index("async pin()")]
    assert "if (this.busy) return;" in act
    assert "AbortSignal.timeout(30000)" in act
    assert "await res.text()" in act and "JSON.parse(text)" in act and "res.ok && j.ok" in act
    assert "if (!navigating) this.busy = false;" in act
    assert "?error=" not in act, "a failure must not redirect to a run page that may 404"
    assert "new CustomEvent('run-error'" in act
    assert '<div role="alert" x-data="runNotice()" @run-error.window=' in body


@pytest.mark.asyncio
async def test_deleting_a_missing_run_is_a_404_json_the_page_handles(manager_client):
    response = await manager_client.post("/api/runs/nope/delete")
    assert response.status == 404
    assert await response.json() == {"ok": False, "error": "Run not found"}


# -- without psutil: liveness must not kill (on Windows os.kill(pid, 0) is TerminateProcess) --
def _no_psutil(monkeypatch):
    monkeypatch.delattr(manager, "psutil", raising=False)
    monkeypatch.setattr(manager, "PSUTIL_AVAILABLE", False)
    if os.name == "nt":
        def never(*_args):
            raise AssertionError("os.kill on Windows terminates the process")
        monkeypatch.setattr(manager.os, "kill", never)


def test_liveness_without_psutil_probes_without_killing(monkeypatch):
    finished = subprocess.Popen([sys.executable, "-c", "pass"])
    finished.wait()
    _no_psutil(monkeypatch)
    assert manager._is_alive(os.getpid()) is True
    assert manager._is_alive(os.getpid(), None) is True, "pid_created is None on this path"
    assert manager._is_alive(finished.pid) is False
    assert manager._create_time(os.getpid()) is None


def test_stop_without_psutil_kills_and_waits_for_the_child(monkeypatch):
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    # POSIX: os.kill(pid, 0) succeeds on an unreaped zombie. The manager's real children are reaped
    # by asyncio, so reap this one the same way (a waiter) instead of weakening _is_alive.
    threading.Thread(target=child.wait, daemon=True).start()
    try:
        monkeypatch.delattr(manager, "psutil", raising=False)
        monkeypatch.setattr(manager, "PSUTIL_AVAILABLE", False)
        assert manager._is_alive(child.pid)
        assert manager._kill_run(child.pid) is True
        assert child.wait(timeout=5) is not None
    finally:
        if child.poll() is None:
            child.kill()


# -- review round: probe host, a foreign server, lock growth, async kills, cancellation --
def _run_manager(client):
    return next(r.handler.__self__ for r in client.server.app.router.routes()
                if isinstance(getattr(r.handler, "__self__", None), manager.RunManager))


def _live_child(monkeypatch, answers):
    """A child that stays up; `answers(host)` decides what its HTTP port says."""
    spawned, probed = [], []

    async def create(*_cmd, **_kw):
        spawned.append(4242)

        async def wait():
            await asyncio.Event().wait()
        return SimpleNamespace(pid=4242, returncode=None, wait=wait, kill=lambda: None)

    async def ready(host, _port):
        probed.append(host)
        return answers(bool(spawned))
    monkeypatch.setattr(manager.asyncio, "create_subprocess_exec", create)
    monkeypatch.setattr(manager, "_http_ready", ready)
    monkeypatch.setattr(manager, "SPAWN_POLL_S", 0.01)
    return spawned, probed


@pytest.mark.asyncio
async def test_an_ipv6_wildcard_is_probed_on_ipv6_loopback(manager_dir, monkeypatch):
    _spawned, probed = _live_child(monkeypatch, lambda up: up)
    assert await manager._spawn_run({"run_id": "r1", "tcp_port": 1, "http_port": 2}, "::") == 4242
    assert probed and set(probed) == {"::1"}


@pytest.mark.asyncio
async def test_the_probe_brackets_an_ipv6_literal():
    from aiohttp import web
    from aiohttp.test_utils import TestServer
    async def status(_request):
        return web.json_response({})
    app = web.Application()
    app.router.add_get("/api/status", status)
    server = TestServer(app, host="::1")
    try:
        await server.start_server()
    except OSError:
        pytest.skip("no IPv6 loopback here")
    try:
        assert await manager._http_ready("::1", server.port) is True
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_a_foreign_server_on_the_http_port_refuses_the_start(manager_dir, monkeypatch):
    spawned, _probed = _live_child(monkeypatch, lambda up: True)
    with pytest.raises(RuntimeError, match="HTTP port 2 is in use"):
        await manager._spawn_run({"run_id": "r1", "tcp_port": 1, "http_port": 2}, "127.0.0.1")
    assert spawned == []


@pytest.mark.asyncio
async def test_run_locks_do_not_accumulate(manager_client, monkeypatch):
    _stopped_run()
    monkeypatch.setattr(manager, "_kill_run", lambda pid, created=None: True)
    for path in ("/api/runs/nope/start", "/api/runs/nope/stop", "/api/runs/nope/delete",
                 "/api/runs/r1/stop", "/api/runs/r1/delete"):
        await manager_client.post(path)
    assert _run_manager(manager_client)._run_locks == {}


@pytest.mark.asyncio
async def test_a_waiter_keeps_the_lock_alive_until_it_is_done(manager_client, monkeypatch):
    _stopped_run()
    spawns, release = _blocking_spawn(monkeypatch)
    first = asyncio.ensure_future(manager_client.post("/api/runs/r1/start"))
    second = asyncio.ensure_future(manager_client.post("/api/runs/r1/start"))
    await asyncio.sleep(0.1)
    assert _run_manager(manager_client)._run_locks["r1"][1] == 2
    release.set()
    await first
    await second
    assert spawns == ["r1"] and _run_manager(manager_client)._run_locks == {}


def test_a_kill_without_psutil_waits_for_the_process_to_go(monkeypatch):
    """SIGTERM (and TerminateProcess) return before the process is gone."""
    monkeypatch.setattr(manager, "PSUTIL_AVAILABLE", False)
    signalled, checks = [], iter([True, True, True, True, False])
    monkeypatch.setattr(manager.os, "kill", lambda pid, sig: signalled.append(pid))
    monkeypatch.setattr(manager, "_is_alive", lambda pid, created=None: next(checks, False))
    assert manager._kill_run(4242) is True
    assert signalled == [4242]


@pytest.mark.asyncio
async def test_a_cancelled_start_kills_its_child(manager_dir, monkeypatch):
    killed = _fake_child(monkeypatch, exits_after=None)
    start = asyncio.ensure_future(manager._spawn_run({"run_id": "r1", "tcp_port": 1, "http_port": 2}, "127.0.0.1"))
    await asyncio.sleep(0.1)
    start.cancel()
    with pytest.raises(asyncio.CancelledError):
        await start
    assert killed == [4242]


@pytest.mark.asyncio
async def test_stop_kills_off_the_event_loop_and_keeps_a_concurrent_reconcile(manager_client, monkeypatch):
    manager._save_registry([
        {"run_id": "r1", "name": "Duo", "tcp_port": 1, "http_port": 2, "status": "running", "pid": 4242},
        {"run_id": "r2", "name": "Other", "tcp_port": 3, "http_port": 4, "status": "running", "pid": 99},
    ])
    released, blocked_loop = threading.Event(), []

    def slow_kill(pid, created=None):
        blocked_loop.append(not released.wait(timeout=2))   # the loop must be free to release us
        manager._update_run("r2", status="stopped", pid=None)  # a board poll reconciled r2 meanwhile
        return True
    monkeypatch.setattr(manager, "_kill_run", slow_kill)
    stop = asyncio.ensure_future(manager_client.post("/api/runs/r1/stop"))
    await asyncio.sleep(0.1)
    released.set()
    assert (await (await stop).json())["ok"]
    assert blocked_loop == [False]
    runs = {r["run_id"]: r for r in manager._load_registry()}
    assert runs["r1"]["status"] == "stopped" and runs["r2"]["status"] == "stopped"
