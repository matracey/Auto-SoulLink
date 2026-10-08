"""Manager regressions for filesystem preservation and browser-safe endpoints."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiohttp.test_utils import TestClient, TestServer

import server.json_files as json_files
import server.manager as manager
from server.lua_literals import lua_comment, lua_string
from server.server import SLinkServer, build_app
from server.status_payload import empty_status_payload

pytest_plugins = ["tests.unit.manager_harness"]


async def _answers(*_args):                  # the child's HTTP port answers /api/status
    return True


def test_missing_registry_is_empty_and_can_be_created(manager_dir):
    assert manager._load_registry() == []
    manager._save_registry([])
    assert json.loads((manager_dir / "registry.json").read_text()) == {"runs": []}


@pytest.mark.parametrize("contents", [
    b"{unfinished", b"\xff", b"null", b"[]", b"{}", b'{"runs":{}}',
    b'{"runs":[null]}', b'{"runs":[{}]}', b'{"runs":[{"run_id":""}]}',
    b'{"runs":[{"run_id":"same"},{"run_id":"same"}]}',
])
def test_invalid_registry_cannot_be_overwritten(manager_dir, contents):
    registry = manager_dir / "registry.json"
    registry.write_bytes(contents)
    with pytest.raises(manager.RegistryError, match="preserved"):
        manager._load_registry()
    with pytest.raises(manager.RegistryError, match="preserved"):
        manager._save_registry([])
    assert registry.read_bytes() == contents
    assert list(manager_dir.iterdir()) == [registry]


def test_unreadable_registry_is_not_treated_as_missing(manager_dir, monkeypatch):
    registry = manager_dir / "registry.json"
    registry.write_text('{"runs":[]}', encoding="utf-8")

    def denied(*args, **kwargs):
        raise PermissionError("registry is locked")

    monkeypatch.setattr(manager, "open", denied, raising=False)
    with pytest.raises(manager.RegistryError, match="locked"):
        manager._load_registry()
    with pytest.raises(manager.RegistryError, match="locked"):
        manager._save_registry([])
    assert registry.read_text() == '{"runs":[]}'


def test_atomic_registry_replacement_publishes_a_complete_sibling_file(manager_dir, monkeypatch):
    registry = manager_dir / "registry.json"
    old = b'{"runs":[{"run_id":"old"}]}'
    registry.write_bytes(old)
    replacement = {"runs": [{"run_id": "new", "name": "Pokémon"}]}
    real_replace = json_files.os.replace
    calls = []

    def replace(source, target):
        assert Path(source).parent == registry.parent
        assert Path(target) == registry
        assert registry.read_bytes() == old
        assert json.loads(Path(source).read_text(encoding="utf-8")) == replacement
        calls.append((source, target))
        real_replace(source, target)

    monkeypatch.setattr(json_files.os, "replace", replace)
    manager._save_registry(replacement["runs"])
    assert len(calls) == 1
    assert json.loads(registry.read_text()) == replacement
    assert list(manager_dir.iterdir()) == [registry]


@pytest.mark.parametrize("operation", ["fsync", "replace"])
def test_failed_registry_publication_preserves_original(manager_dir, monkeypatch, operation):
    registry = manager_dir / "registry.json"
    old = b'{"runs":[{"run_id":"old"}]}'
    registry.write_bytes(old)

    def fail(*args):
        raise OSError("disk unavailable")

    monkeypatch.setattr(json_files.os, operation, fail)
    with pytest.raises(OSError, match="disk unavailable"):
        manager._save_registry([{"run_id": "new"}])
    assert registry.read_bytes() == old
    assert list(manager_dir.iterdir()) == [registry]


def test_failed_json_serialization_preserves_original_and_removes_temporary(manager_dir):
    registry = manager_dir / "registry.json"
    old = b'{"runs":[]}'
    registry.write_bytes(old)
    with pytest.raises(TypeError):
        manager._save_registry([{"run_id": "new", "bad": object()}])
    assert registry.read_bytes() == old
    assert list(manager_dir.iterdir()) == [registry]


def _assert_same_shape(actual, expected, path="status"):
    assert type(actual) is type(expected), path
    if isinstance(expected, dict):
        assert actual.keys() == expected.keys(), path
        for key, value in expected.items():
            _assert_same_shape(actual[key], value, f"{path}.{key}")


def test_empty_status_has_the_run_serializers_complete_nested_schema(tmp_path):
    # Only the test constructs a server; the production factory is pure data.
    real = json.loads(json.dumps(SLinkServer(data_dir=str(tmp_path / "run"))._build_status_dict()))
    empty = empty_status_payload()
    _assert_same_shape(empty, real)
    real["badge_slugs"] = []  # The Manager has no selected game catalogue.
    for pid in ("a", "b"):    # ...and no cartridge to read capabilities from.
        real["players"][pid]["capabilities"] = empty["players"][pid]["capabilities"]
    assert empty == real
    assert empty == manager._EMPTY_STATUS


def test_empty_status_containers_are_independent():
    one, two = empty_status_payload(), empty_status_payload()
    one["players"]["a"]["battle_state"]["enemy_party"].append({"species_id": 1})
    one["players"]["a"]["party_details"]["key"] = {}
    one["pending_bonus"]["a"].append("key")
    assert two == empty_status_payload()
    assert one["players"]["b"] == two["players"]["b"]


@pytest.mark.parametrize("host", [
    "127.0.0.1", 'host"; PWNED=true; --', "host\\tail", "host\nPWNED=true\r\x00123",
    "hôte\u2028例.example",
])
def test_launcher_host_and_comment_cannot_execute_lua(host):
    lupa = pytest.importorskip("lupa")
    runtime = lupa.LuaRuntime(unpack_returned_tuples=True)
    evaluate = runtime.eval("""function(source)
        local env = {
            ipairs = ipairs,
            debug = {getinfo = function() return {source = '@C:/test/launcher.lua'} end},
            io = {open = function() return {
                read = function() return 'C:/SLink/' end,
                write = function() end, close = function() end
            } end},
            dofile = function() end
        }
        local fn, err = load(source, 'launcher', 't', env)
        assert(fn, err)
        fn()
        return env.SLINK_HOST, env.SLINK_PLAYER, env.PWNED
    end""")
    run = {"run_id": "r1", "name": "Run\r\nPWNED=true\n--\x00", "tcp_port": 54321}
    source = manager._build_launcher(run, "a", host)
    assert evaluate(source) == (host, "a", None)
    assert "PWNED=true" in source.splitlines()[0]
    assert "\x00" not in source


def test_shared_lua_helpers_handle_all_byte_controls():
    lupa = pytest.importorskip("lupa")
    runtime = lupa.LuaRuntime()
    value = "".join(chr(i) for i in range(256)) + "例"
    assert runtime.eval(lua_string(value)) == value
    assert len(lua_comment(value).splitlines()) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["success", "failure", "cancel"])
async def test_spawn_closes_parent_stderr_after_creation(manager_dir, monkeypatch, outcome):
    captured = []

    async def create(*args, **kwargs):
        stream = kwargs["stderr"]
        captured.append(stream)
        assert not stream.closed
        stream.write(b"child startup diagnostics\n")
        if outcome == "failure":
            raise OSError("spawn failed")
        if outcome == "cancel":
            raise asyncio.CancelledError
        async def _wait():                       # a healthy child: still running after the check
            await asyncio.sleep(10)
        monkeypatch.setattr(manager, "SPAWN_POLL_S", 0.01)
        monkeypatch.setattr(manager, "_http_ready", _answers)
        return SimpleNamespace(pid=4242, wait=_wait, returncode=None)

    monkeypatch.setattr(manager.asyncio, "create_subprocess_exec", create)
    run = {"run_id": "r1", "tcp_port": 1, "http_port": 2}
    if outcome == "success":
        assert await manager._spawn_run(run, "127.0.0.1") == 4242
    else:
        error = OSError if outcome == "failure" else asyncio.CancelledError
        with pytest.raises(error):
            await manager._spawn_run(run, "127.0.0.1")
    assert len(captured) == 1 and captured[0].closed
    assert (manager_dir / "r1" / "spawn.log").read_bytes() == b"child startup diagnostics\n"


@pytest.mark.asyncio
async def test_spawn_keeps_devnull_fallback_when_log_cannot_open(monkeypatch):
    def denied(*args, **kwargs):
        raise PermissionError("log is locked")

    async def create(*args, **kwargs):
        assert kwargs["stderr"] == asyncio.subprocess.DEVNULL
        async def _wait():                       # a healthy child: still running after the check
            await asyncio.sleep(10)
        monkeypatch.setattr(manager, "SPAWN_POLL_S", 0.01)
        monkeypatch.setattr(manager, "_http_ready", _answers)
        return SimpleNamespace(pid=4242, wait=_wait, returncode=None)

    monkeypatch.setattr(manager, "open", denied, raising=False)
    monkeypatch.setattr(manager.asyncio, "create_subprocess_exec", create)
    assert await manager._spawn_run({"run_id": "r1", "tcp_port": 1, "http_port": 2}, "127.0.0.1") == 4242


@pytest.mark.asyncio
async def test_manager_main_rejects_cross_origin_delete_before_files_change(manager_client, manager_dir):
    run = {"run_id": "r1", "tcp_port": 1, "http_port": 2, "status": "stopped", "pid": None}
    manager._save_registry([run])
    sentinel = manager_dir / "r1" / "keep.txt"
    sentinel.parent.mkdir()
    sentinel.write_text("run data")
    original = (manager_dir / "registry.json").read_bytes()
    response = await manager_client.post("/api/runs/r1/delete", headers={"Origin": "https://unrelated.invalid"})
    assert response.status == 403
    assert (await response.json())["ok"] is False
    assert sentinel.read_text() == "run data"
    assert (manager_dir / "registry.json").read_bytes() == original

    response = await manager_client.post("/api/runs/r1/delete", headers={
        "Origin": str(manager_client.make_url("/")).rstrip("/"),
    })
    assert response.status == 200
    assert not sentinel.exists()
    assert manager._load_registry() == []


@pytest.mark.asyncio
async def test_corrupt_registry_returns_actionable_errors_without_mutation(manager_client, manager_dir, monkeypatch):
    registry = manager_dir / "registry.json"
    registry.write_bytes(b"{broken")
    sentinel = manager_dir / "r1" / "keep.txt"
    sentinel.parent.mkdir()
    sentinel.write_text("run data")

    async def unexpected_spawn(*args, **kwargs):
        pytest.fail("a corrupted registry must stop before spawning a run")

    monkeypatch.setattr(manager, "_spawn_run", unexpected_spawn)
    for path, body in (("/api/runs/r1/delete", None), ("/api/runs/new", {"name": "New run", "game": "gen1"})):
        response = await manager_client.post(path, json=body)
        assert response.status == 503
        assert "repair registry.json" in (await response.json())["error"]
    response = await manager_client.get("/")
    assert response.status == 503
    assert response.content_type == "text/html"
    assert "repair registry.json" in await response.text()
    assert registry.read_bytes() == b"{broken"
    assert sentinel.read_text() == "run data"
    assert set(manager_dir.iterdir()) == {registry, sentinel.parent}


@pytest.mark.asyncio
async def test_manager_main_caches_html_by_theme_and_serves_real_empty_schema(manager_client):
    for cookie in ("slink-theme=light", "slink-theme=default"):
        response = await manager_client.get("/", headers={"Cookie": cookie})
        assert response.status == 200
        assert "no-cache" in response.headers["Cache-Control"]
        assert "cookie" in response.headers["Vary"].lower()
    response = await manager_client.get("/api/status")
    assert response.status == 200
    assert await response.json() == empty_status_payload()


@pytest.mark.asyncio
async def test_browser_attempts_update_crosses_manager_and_run_middlewares(manager_client, tmp_path, monkeypatch):
    run_dir = tmp_path / "proxied-run"
    srv = SLinkServer(data_dir=str(run_dir))
    async with TestClient(TestServer(build_app(srv))) as run_client:
        monkeypatch.setattr(manager.RunManager, "_active_stream_run", lambda self: {
            "http_port": run_client.server.port,
        })
        response = await manager_client.post("/api/attempts", json={"count": 7}, headers={
            "Origin": str(manager_client.make_url("/")).rstrip("/"),
            "Sec-Fetch-Site": "same-origin",
        })
        assert response.status == 200
        assert await response.json() == {"ok": True, "attempts_count": 7}
        assert srv.state.attempts_count == 7
        saved = (run_dir / "links.json").read_bytes()
        assert json.loads(saved)["attempts_count"] == 7

        response = await manager_client.post("/api/attempts", json={"count": 99}, headers={
            "Origin": "https://unrelated.invalid",
            "Sec-Fetch-Site": "cross-site",
        })
        assert response.status == 403
        assert (await response.json())["ok"] is False
        assert srv.state.attempts_count == 7
        assert (run_dir / "links.json").read_bytes() == saved


# ── a run must get ports it can actually bind, and a dead spawn must not read as running ──
def test_next_ports_skips_a_port_something_else_holds():
    """The registry only knows this Manager's runs. A port held by anything else on the
    machine (a hand-started server, another Manager) must be skipped, not handed out."""
    import socket
    with socket.socket() as held:
        held.bind(("127.0.0.1", 0))               # whatever the OS gives; never a live run's port
        held.listen(1)
        port = held.getsockname()[1]
        assert not manager._port_free(port)
    assert manager._port_free(port)


@pytest.mark.asyncio
async def test_a_spawn_that_exits_on_startup_raises_with_its_reason(tmp_path, monkeypatch):
    """server.py exits within a second when it cannot bind. Reporting that as a running run
    would show whatever else answers on the HTTP port — another run's board."""
    monkeypatch.setattr(manager, "MANAGER_DIR", str(tmp_path))
    run = {"run_id": "r1", "name": "r1", "tcp_port": 1, "http_port": 2}
    log = tmp_path / "r1" / "spawn.log"

    async def fake_exec(*_cmd, **_kw):
        log.write_text("noise\nCannot listen on TCP 127.0.0.1:1 — in use\n  Another SLink server is probably already running.\n", encoding="utf-8")

        async def _wait():
            return 1
        return SimpleNamespace(pid=77, wait=_wait, returncode=1)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    with pytest.raises(RuntimeError, match="Cannot listen on TCP"):
        await manager._spawn_run(run, "127.0.0.1")


@pytest.mark.asyncio
@pytest.mark.parametrize("game", ["gen3_ap", " gen3_ap", "GEN3_AP", "gen1_ap"])
async def test_manager_refuses_to_create_an_archipelago_game(manager_client, manager_dir, monkeypatch, game):
    """The Archipelago games are no longer listed (owner 2026-10-02: no SLink client supports them
    yet), so /api/runs/new refuses their keys like any unlisted game: 400, nothing written, nothing
    spawned. Vanilla gen3 and gen3_e (Emerald, EG4) are still created."""
    async def unexpected_spawn(*args, **kwargs):
        pytest.fail("an unlisted game must not spawn a run")

    monkeypatch.setattr(manager, "_spawn_run", unexpected_spawn)
    response = await manager_client.post("/api/runs/new", json={"name": "x", "game": game})
    assert response.status == 400
    assert "choose the game" in (await response.json())["error"]
    assert manager._load_registry() == []
    assert [p.name for p in manager_dir.iterdir()] == []


@pytest.mark.asyncio
async def test_manager_creates_an_emerald_run_now_that_eg4_admits_it(manager_client, manager_dir, monkeypatch):
    """EG4 falsifier: gen3_e left UNADMITTED_GAMES, so /api/runs/new must create the run (a
    spawn failure, e.g. no BizHawk client attached, is reported in start_error, not a 400)."""
    async def failing_spawn(*args, **kwargs):
        raise RuntimeError("no client")

    monkeypatch.setattr(manager, "_spawn_run", failing_spawn)
    response = await manager_client.post("/api/runs/new", json={"name": "x", "game": "gen3_e"})
    assert response.status == 200
    body = await response.json()
    assert body["ok"] is True and body["run"]["game"] == "gen3_e"
    assert [r["game"] for r in manager._load_registry()] == ["gen3_e"]


def test_a_saved_run_with_an_unknown_game_key_still_labels():
    """A registry run saved under a game key no longer in GAMES must not raise KeyError."""
    assert manager.GAME_LABELS.get("gen3_gone", "gen3_gone") == "gen3_gone"


@pytest.mark.parametrize("locks", [1, 4, 5])
def test_atomic_json_retries_only_target_permission_locks(tmp_path, monkeypatch, locks):
    import time
    path = tmp_path / "locked.json"
    path.write_text('{"old":true}')
    replace = json_files.os.replace
    calls, delays = [], []
    def locked(source, target):
        calls.append((source, target))
        if len(calls) <= locks:
            raise PermissionError("Drive lock")
        return replace(source, target)
    monkeypatch.setattr(json_files.os, "replace", locked)
    monkeypatch.setattr(time, "sleep", delays.append)
    if locks == 5:
        with pytest.raises(PermissionError, match="Drive lock"):
            json_files.atomic_write_json(path, {"new": True})
        assert json.loads(path.read_text()) == {"old": True}
    else:
        json_files.atomic_write_json(path, {"new": True})
        assert json.loads(path.read_text()) == {"new": True}
    assert len(calls) == min(locks + 1, 5)
    assert delays == pytest.approx([0.2 * (n + 1) for n in range(min(locks, 4))])
    assert not list(tmp_path.glob("*.tmp"))


def test_atomic_json_does_not_retry_non_permission_error(tmp_path, monkeypatch):
    calls = []
    def broken(*args):
        calls.append(args)
        raise OSError("disk full")
    monkeypatch.setattr(json_files.os, "replace", broken)
    with pytest.raises(OSError, match="disk full"):
        json_files.atomic_write_json(tmp_path / "out.json", {})
    assert len(calls) == 1 and not (tmp_path / "out.json").exists()
    assert not list(tmp_path.glob("*.tmp"))


def test_obs_config_is_published_atomically(tmp_path, monkeypatch):
    from server.obs_controller import OBSController
    obs = OBSController.__new__(OBSController)
    path = tmp_path / "obs.json"
    path.write_text('{"old":true}')
    obs._config_path, obs._config = str(path), {"players": {}}
    replace = json_files.os.replace
    seen = []
    def publish(source, target):
        assert json.loads(path.read_text()) == {"old": True}
        seen.append(json.loads(Path(source).read_text()))
        return replace(source, target)
    monkeypatch.setattr(json_files.os, "replace", publish)
    obs.save_config()
    assert seen == [{"players": {}}] and json.loads(path.read_text()) == obs._config
    assert not list(tmp_path.glob("*.tmp"))
