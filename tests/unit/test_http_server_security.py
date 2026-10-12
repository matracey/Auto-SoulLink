"""HTTP boundaries reject external paths and render client-controlled text safely."""

import html
import os
from html.parser import HTMLParser
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import quote

import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from yarl import URL

from server.adapters.gen3_frlge import Gen3Adapter
from server.server import SLinkServer, build_app
from server.state import AreaStatus


@pytest.fixture
def srv(tmp_path):
    instance = SLinkServer(data_dir=str(tmp_path / "run"))
    instance.state.adapter = instance.adapter = Gen3Adapter(is_rr=True)
    return instance


@pytest.fixture
def calc_dirs(tmp_path, monkeypatch):
    src, dist = tmp_path / "src", tmp_path / "dist"
    src.mkdir()
    dist.mkdir()
    from server import calc_files
    monkeypatch.setattr(calc_files, "SRC_DIR", str(src))
    monkeypatch.setattr(calc_files, "DIST_DIR", str(dist))
    (tmp_path / "outside.txt").write_text("PRIVATE FILE", encoding="utf-8")
    return src, dist


@pytest_asyncio.fixture
async def client(srv, calc_dirs):
    async with TestClient(TestServer(build_app(srv))) as test_client:
        yield test_client


@pytest.mark.asyncio
@pytest.mark.parametrize("path", [
    "../outside.txt", "assets/../../outside.txt", "/outside.txt",
    "C:/Windows/win.ini", "C:outside.txt", "//server/share/file",
    r"C:\Windows\win.ini", r"..\outside.txt", r"assets\..\outside.txt",
    r"\\server\share\file", r"\\?\C:\Windows\win.ini", "null\x00.bin",
])
async def test_calc_rejects_non_relative_paths(srv, calc_dirs, path):
    request = SimpleNamespace(match_info={"path": path})
    with pytest.raises(web.HTTPForbidden):
        await srv.handle_calc_files(request)


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["../outside.txt", "C:/Windows/win.ini", r"..\outside.txt"])
async def test_calc_rejects_url_encoded_paths(client, path):
    response = await client.get(URL("/calc/" + quote(path, safe=""), encoded=True))
    assert response.status == 403
    assert "PRIVATE FILE" not in await response.text()


@pytest.mark.asyncio
async def test_calc_preserves_source_precedence_and_conditional_downloads(client, calc_dirs):
    src, dist = calc_dirs
    (src / "asset.css").write_text("body { color: red; }", encoding="utf-8")
    (dist / "asset.css").write_text("DIST COPY", encoding="utf-8")
    (dist / "fallback.bin").write_bytes(b"\x00\x01fallback")
    (src / "version..txt").write_text("legitimate filename", encoding="utf-8")

    response = await client.get("/calc/asset.css")
    assert response.status == 200
    assert response.content_type == "text/css"
    assert await response.text() == "body { color: red; }"
    etag = response.headers["ETag"]
    cached = await client.get("/calc/asset.css", headers={"If-None-Match": etag})
    assert cached.status == 304
    assert await cached.read() == b""
    head = await client.head("/calc/asset.css")
    assert head.status == 200
    assert head.headers["Content-Length"] == response.headers["Content-Length"]
    assert await head.read() == b""
    fallback = await client.get("/calc/fallback.bin")
    assert await fallback.read() == b"\x00\x01fallback"
    assert (await client.get("/calc/missing.bin")).status == 404
    assert (await client.get("/calc/")).status == 404
    assert await (await client.get("/calc/version..txt")).text() == "legitimate filename"


@pytest.mark.asyncio
async def test_calc_html_keeps_template_wrapping_and_theme(client, calc_dirs):
    _, dist = calc_dirs
    (dist / "hardcore.html").write_text(
        "<html><head><title>ORIGINAL HEAD</title></head>"
        '<body><section id="calc-test">Calculator body</section></body></html>',
        encoding="utf-8",
    )
    response = await client.get("/calc/hardcore.html?theme=light")
    body = await response.text()
    assert response.status == 200
    assert response.content_type == "text/html"
    assert 'id="calc-test"' in body
    assert "Hardcore Mode" in body
    assert "themes/light.css" in body
    assert "ORIGINAL HEAD" not in body


@pytest.mark.asyncio
@pytest.mark.parametrize("root_index", [0, 1])
async def test_calc_rejects_symlink_escape(srv, calc_dirs, tmp_path, root_index):
    link = calc_dirs[root_index] / "escaped.txt"
    try:
        link.symlink_to(tmp_path / "outside.txt")
    except OSError as exc:
        pytest.skip(f"Creating symlinks is unavailable: {exc}")
    with pytest.raises(web.HTTPForbidden):
        await srv.handle_calc_files(SimpleNamespace(match_info={"path": "escaped.txt"}))


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "nt", reason="Windows junction regression")
@pytest.mark.parametrize("root_index", [0, 1])
async def test_calc_rejects_windows_junction_escape(srv, calc_dirs, tmp_path, root_index):
    # Junctions do not require the symlink privilege on Windows. Creation uses
    # a fixed command with argv quoting, and pytest owns both temporary paths.
    import subprocess

    outside = tmp_path / "outside-dir"
    outside.mkdir()
    (outside / "private.txt").write_text("PRIVATE FILE", encoding="utf-8")
    junction = calc_dirs[root_index] / "junction"
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(outside)],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    try:
        with pytest.raises(web.HTTPForbidden):
            await srv.handle_calc_files(SimpleNamespace(match_info={"path": "junction/private.txt"}))
    finally:
        # Remove only the junction itself, never recursively traverse its target.
        junction.rmdir()


class _AttackTags(HTMLParser):
    def __init__(self):
        super().__init__()
        self.markers = []

    def handle_starttag(self, tag, attrs):
        self.markers.extend(value for key, value in attrs if key == "data-attack")


@pytest.mark.asyncio
async def test_dashboard_escapes_client_text_on_the_board(client, srv):
    """Everything a client can name -- its rom_type, its last event, its area -- reaches the
    board as text. Jinja autoescapes, but a `|safe` in the wrong place would undo that."""
    def attack(label):
        return f'<img data-attack="{label}" src=x onerror="alert(1)">'

    for player in ("a", "b"):
        srv.connected_players[player] = {
            "connected": True, "rom_type": attack("rom-" + player),
            "last_event": attack("event-" + player),
        }
        srv.player_area_id[player] = attack("area-" + player)
    encounter_area = attack("encounter")
    srv.state.area_states[encounter_area] = AreaStatus.DEAD_ZONE
    resp = await client.get("/")
    assert resp.status == 200
    rendered = await resp.text()
    parser = _AttackTags()
    parser.feed(rendered)
    assert parser.markers == []
    # Jinja escapes through markupsafe (`"` -> &#34;), not html.escape (&quot;).
    from markupsafe import escape
    for player in ("a", "b"):
        assert str(escape(attack("event-" + player))) in rendered
        assert str(escape(srv.adapter.area_display_name(attack("area-" + player)))) in rendered


@pytest.mark.asyncio
async def test_dashboard_says_unknown_for_an_unnamed_area(client, srv):
    srv.connected_players["a"] = {"connected": True, "rom_type": "firered_rr", "last_event": "hello"}
    assert "unknown" in await (await client.get("/")).text()


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [None, 42, True])
async def test_escaping_preserves_rendering_of_non_text_legacy_metadata(client, srv, value):
    # The legacy TCP handler records metadata before its dispatch validation.
    # Output escaping must not turn formerly printable JSON scalars into 500s.
    srv.connected_players["a"] = {"rom_type": value, "last_event": value}
    resp = await client.get("/")
    assert resp.status == 200
    if value is not None:   # None is "no data yet" and the card says so instead
        assert html.escape(str(value)) in await resp.text()
    assert srv._build_status_dict()["players"]["a"]["last_event"] is value


@pytest.mark.asyncio
@pytest.mark.parametrize("player", ["other", "", None, 3, [], {}])
async def test_invalid_pokeball_player_has_no_side_effects(client, srv, monkeypatch, player):
    before = dict(srv.state.pokeballs_obtained)
    save, notify = Mock(), Mock()
    monkeypatch.setattr(srv.state, "_save", save)
    monkeypatch.setattr(srv, "_notify_sse", notify)
    response = await client.post("/api/debug/set_pokeballs", json={"player": player})
    assert response.status == 400
    assert srv.state.pokeballs_obtained == before
    save.assert_not_called()
    notify.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("body,player,value", [
    ({}, "a", True), ({"player": "a", "value": False}, "a", False),
    ({"player": "b", "value": True}, "b", True),
])
async def test_valid_pokeball_player_still_persists(client, srv, monkeypatch, body, player, value):
    save, notify = Mock(), Mock()
    monkeypatch.setattr(srv.state, "_save", save)
    monkeypatch.setattr(srv, "_notify_sse", notify)
    response = await client.post("/api/debug/set_pokeballs", json=body)
    assert response.status == 200
    assert srv.state.pokeballs_obtained[player] is value
    save.assert_called_once_with()
    notify.assert_called_once_with()


@pytest.mark.asyncio
async def test_run_app_blocks_cross_origin_mutation(client, srv, monkeypatch):
    save = Mock()
    monkeypatch.setattr(srv.state, "_save", save)
    response = await client.post(
        "/api/debug/set_pokeballs", json={"player": "a"},
        headers={"Sec-Fetch-Site": "cross-site", "Origin": "https://untrusted.example"},
    )
    assert response.status == 403
    save.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("separator", ["\n", "\r", "\r\n", "\x00", "\u2028", "\u2029"])
@pytest.mark.parametrize("player", ["a", "b"])
async def test_standalone_launcher_keeps_untrusted_values_out_of_lua_code(srv, separator, player):
    lupa = pytest.importorskip("lupa")
    srv._run_name = f"Test{separator}SLINK_ATTACKED = true{separator}--"
    srv._tcp_port = 54321
    host = 'host"; SLINK_ATTACKED = true; --'
    response = await srv.handle_launcher(SimpleNamespace(match_info={"player": player}, host=host))
    assert response.status == 200
    assert response.headers["Content-Disposition"].endswith(f'_{player}.lua"')

    runtime = lupa.LuaRuntime(unpack_returned_tuples=True)
    # Execute the real launcher with all filesystem and client-loading effects
    # replaced, so a syntactically valid injected statement would be detected.
    runtime.execute("""
        io = {open = function()
            return {read = function() return 'safe/' end,
                    close = function() end, write = function() end}
        end}
        dofile = function(path) SLINK_LOADED_PATH = path end
    """)
    runtime.execute(response.text)
    assert runtime.globals().SLINK_ATTACKED is None
    assert host == runtime.globals().SLINK_HOST
    assert player == runtime.globals().SLINK_PLAYER
    assert runtime.globals().SLINK_PORT == 54321
    assert runtime.globals().SLINK_LOADED_PATH == "safe/lua/slink.lua"


@pytest.mark.asyncio
async def test_debug_revive_clears_game_over_and_allows_it_to_be_requeued(srv, client):
    from server.state import LinkEntry, LinkStatus, MonInfo
    state = srv.state
    first = LinkEntry(area_id="route_1", a=MonInfo(key="A:1"), b=MonInfo(key="B:1"), status=LinkStatus.DEAD)
    second = LinkEntry(area_id="route_2", a=MonInfo(key="A:2"), b=MonInfo(key="B:2"), status=LinkStatus.DEAD)
    state.links = [first, second]
    for entry in state.links:
        state._index_entry(entry)
    state.pokeballs_obtained = {"a": True, "b": True}
    state._check_game_over()
    assert state.run_over
    state.queued_commands = {"a": [], "b": []}
    response = await client.post("/api/debug/revive", json={"area_id": "route_1", "index": 0})
    assert response.status == 200 and (await response.json())["ok"]
    assert first.status == LinkStatus.ALIVE and not state.run_over
    first.status = LinkStatus.DEAD
    state._check_game_over()
    assert state.run_over and all(any(c["cmd"] == "game_over" for c in state.queued_commands[p]) for p in ("a", "b"))


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/api/attempts", "/api/debug/inject_event", "/api/debug/queue_command",
    "/api/debug/set_pokeballs", "/api/debug/set_area_state", "/api/debug/clear_pending", "/api/debug/unlink",
    "/api/debug/register_unpaired",
    "/api/debug/resolve_trade", "/api/debug/resolve_ambiguous_key", "/api/debug/revive", "/api/inject_link", "/api/inject_link_by_slot"])
async def test_load_failure_refuses_http_state_writers_without_touching_disk(srv, client, path):
    from pathlib import Path

    from server.state import LinkEntry, LinkStatus, MonInfo
    saved = Path(srv.state._links_path)
    saved.parent.mkdir(parents=True, exist_ok=True)
    saved.write_bytes(b'{"bad":"preserve me"}')
    srv.state.load_failed = "ValueError: broken saved state"
    srv.state.links = [LinkEntry(area_id="route_1", a=MonInfo(key="A:1"), b=MonInfo(key="B:1"), status=LinkStatus.DEAD)]
    response = await client.post(path, json={"player": "a", "area_id": "route_1", "index": 0, "key": "A:1",
        "a_key": "A:1", "b_key": "B:1", "count": 7, "event": "tick", "cmd": "hud_show", "token": "t1", "action": "rollback"})
    assert response.status == 409
    assert await response.json() == {"ok": False, "error": "run could not be loaded; restore a backup or reset first"}
    assert saved.read_bytes() == b'{"bad":"preserve me"}'


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["reset", "rollback"])
async def test_load_failure_still_allows_reset_and_rollback(srv, client, action):
    from pathlib import Path
    srv.state.load_failed = "ValueError: broken saved state"
    if action == "rollback":
        backup = Path(srv.state._links_path).parent / "backups" / "links.backup.1.json"
        backup.parent.mkdir(parents=True)
        backup.write_text('{"links":[]}')
    response = await client.post("/api/reset" if action == "reset" else "/api/debug/rollback", json={"slot": 1})
    assert response.status == 200 and (await response.json())["ok"]
    assert srv.state.load_failed == ""


@pytest.mark.asyncio
async def test_a_rollback_to_an_unreadable_slot_changes_nothing(srv, client):
    """A truncated backup slot used to be copied over links.json and reported as restored, leaving a run that refuses
    every event; the live state was replaced too. Now the slot is checked first and nothing changes (sweep cx-06955fa9)."""
    from pathlib import Path
    srv.state._save()
    links = Path(srv.state._links_path)
    before, live = links.read_bytes(), srv.state
    backup = links.parent / "backups" / "links.backup.1.json"
    backup.parent.mkdir(parents=True)
    backup.write_text('{"links": [{"area_id": "route_1", "status": "alive", "a": {"bogus_field": 1}}]}')
    response = await client.post("/api/debug/rollback", json={"slot": 1})
    assert response.status == 409 and not (await response.json())["ok"]
    assert links.read_bytes() == before
    assert srv.state is live
