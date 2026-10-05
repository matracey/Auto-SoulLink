"""Request policy tests exercise the middleware before a mutating handler."""

import socket
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer, make_mocked_request

from server.http_safety import ALLOWED_HOSTS_ENV, allow_hosts, csrf_protection, theme_cache


@pytest.fixture(autouse=True)
def slink_local_allowed(monkeypatch):
    """The origin tests below use slink.local as the LAN name; allow it as --allow-host would."""
    monkeypatch.setenv(ALLOWED_HOSTS_ENV, "slink.local")


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
@pytest.mark.parametrize("headers", [
    {"Sec-Fetch-Site": "cross-site"},
    {"Sec-Fetch-Site": "same-site"},
    {"Sec-Fetch-Site": "none"},
    {"Sec-Fetch-Site": "unknown"},
    {"Sec-Fetch-Mode": "no-cors"},
    {"Origin": "null"},
    {"Origin": "https://attacker.example"},
    {"Referer": "https://attacker.example/form"},
    {"Origin": "http://slink.local:8091"},
    {"Origin": "http://slink.local.attacker.example:8090"},
    {"Origin": "http://slink.local:8090@attacker.example"},
    {"Origin": "http://slink.local:8090/path"},
    {"Origin": "http://slink.local:8090#fragment"},
    {"Origin": "http://slink.local:99999"},
    {"Origin": "null", "Referer": "http://slink.local:8090/"},
    {"Origin": "http://slink.local:8090", "Sec-Fetch-Site": "cross-site"},
    {"Origin": "https://attacker.example", "Sec-Fetch-Site": "same-origin"},
])
async def test_browser_mutation_is_rejected_before_handler(method, headers):
    effects = []

    async def mutate(request):
        effects.append(request.method)
        return web.json_response({"ok": True})

    app = web.Application(middlewares=[csrf_protection])
    app.router.add_route("*", "/mutation", mutate)
    async with TestClient(TestServer(app)) as client:
        response = await client.request(method, "/mutation", headers={"Host": "slink.local:8090", **headers})
        assert response.status == 403
        assert (await response.json())["ok"] is False
        assert response.headers["Cache-Control"] == "no-store"
        assert effects == []


@pytest.mark.asyncio
@pytest.mark.parametrize("host,headers", [
    ("slink.local:8090", {}),
    ("slink.local:8090", {"Sec-Fetch-Site": "same-origin"}),
    ("slink.local:8090", {"Origin": "http://slink.local:8090"}),
    ("slink.local:8090", {"Referer": "http://slink.local:8090/runs?selected=one"}),
    ("192.168.1.20:8090", {"Origin": "http://192.168.1.20:8090"}),
    ("slink.local:8090", {"Origin": "http://SLINK.LOCAL:8090", "Sec-Fetch-Site": "same-site"}),
    ("slink.local:8090", {"Origin": "http://slink.local:8090", "Sec-Fetch-Site": "none"}),
    ("slink.local", {"Origin": "http://slink.local:80"}),
    ("[::1]:8090", {"Origin": "http://[::1]:8090"}),
])
async def test_same_origin_and_native_mutations_work(host, headers):
    effects = []

    async def mutate(request):
        effects.append(await request.text())
        return web.json_response({"ok": True})

    app = web.Application(middlewares=[csrf_protection])
    app.router.add_post("/mutation", mutate)
    async with TestClient(TestServer(app)) as client:
        response = await client.post("/mutation", headers={"Host": host, **headers}, data="test")
        assert response.status == 200
        assert effects == ["test"]


@pytest.mark.asyncio
async def test_tls_terminating_proxy_scheme_is_used_for_origin_check():
    status, effects = await _status(
        "slink.local",
        "POST",
        {
            "Origin": "https://slink.local",
            "X-Forwarded-Proto": "https",
            "Sec-Fetch-Site": "same-origin",
        },
    )
    assert (status, effects) == (200, ["POST"])


@pytest.mark.asyncio
@pytest.mark.parametrize("headers", [
    {
        "Origin": "https://attacker.example",
        "X-Forwarded-Proto": "https",
        "Sec-Fetch-Site": "same-origin",
    },
    {
        "Origin": "https://slink.local",
        "X-Forwarded-Proto": "javascript",
        "Sec-Fetch-Site": "same-origin",
    },
])
async def test_forwarded_scheme_cannot_override_origin_host_or_protocol(headers):
    assert await _status("slink.local", "POST", headers) == (403, [])


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS"])
async def test_read_only_requests_remain_compatible(method):
    async def read(request):
        return web.Response(text="read-only")

    app = web.Application(middlewares=[csrf_protection])
    app.router.add_route("*", "/read", read)
    async with TestClient(TestServer(app)) as client:
        response = await client.request(method, "/read", headers={"Sec-Fetch-Site": "cross-site"})
        assert response.status == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("existing,expected", [
    ({}, {"Cache-Control": "no-cache", "Vary": "Cookie"}),
    ({"Vary": "Accept-Encoding"}, {"Cache-Control": "no-cache", "Vary": "Accept-Encoding, Cookie"}),
    ({"Vary": "cookie, Accept-Encoding", "Cache-Control": "private, no-cache"},
     {"Vary": "cookie, Accept-Encoding", "Cache-Control": "private, no-cache"}),
    ({"Vary": "*", "Cache-Control": "no-store"}, {"Vary": "*", "Cache-Control": "no-store, no-cache"}),
])
async def test_html_revalidates_without_losing_existing_cache_headers(existing, expected):
    async def page(request):
        return web.Response(text=request.cookies.get("slink-theme", "default"),
                            content_type="text/html", headers=existing)

    app = web.Application(middlewares=[theme_cache])
    app.router.add_get("/page", page)
    async with TestClient(TestServer(app)) as client:
        response = await client.get("/page", headers={"Cookie": "slink-theme=light"})
        assert await response.text() == "light"
        for name, value in expected.items():
            assert response.headers[name] == value


@pytest.mark.asyncio
async def test_non_html_cache_policy_is_unchanged():
    async def data(request):
        return web.json_response({"ok": True}, headers={"Cache-Control": "no-store", "Vary": "Accept"})

    app = web.Application(middlewares=[theme_cache])
    app.router.add_get("/data", data)
    async with TestClient(TestServer(app)) as client:
        response = await client.get("/data")
        assert response.headers["Cache-Control"] == "no-store"
        assert response.headers["Vary"] == "Accept"


@pytest.mark.asyncio
async def test_repeated_header_fields_are_preserved():
    async def page(request):
        response = web.Response(text="themed", content_type="text/html")
        response.headers.add("Vary", "Accept-Encoding")
        response.headers.add("Vary", "Accept-Language")
        response.headers.add("Cache-Control", "private")
        response.headers.add("Cache-Control", "no-store")
        return response

    app = web.Application(middlewares=[theme_cache])
    app.router.add_get("/page", page)
    async with TestClient(TestServer(app)) as client:
        response = await client.get("/page")
        assert response.headers["Vary"] == "Accept-Encoding, Accept-Language, Cookie"
        assert response.headers["Cache-Control"] == "private, no-store, no-cache"


# -- DNS rebinding: the Host header must name this machine ------------------------------

async def _status(host, method="GET", headers=None):
    effects = []

    async def handler(request):
        effects.append(request.method)
        return web.Response(text="ok")

    app = web.Application(middlewares=[csrf_protection])
    app.router.add_route("*", "/x", handler)
    async with TestClient(TestServer(app)) as client:
        response = await client.request(method, "/x", headers={"Host": host, **(headers or {})})
        return response.status, effects


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize("host", [
    "evil.example:8090", "evil.example", "localhost.evil.example:8090",
    "127.0.0.1.nip.io:8090", "slink.local.evil.example:8090",
])
async def test_rebinding_host_is_rejected_for_every_method(host, method):
    status, effects = await _status(host, method, {"Origin": f"http://{host}"})
    assert status == 403
    assert effects == []


@pytest.mark.asyncio
@pytest.mark.parametrize("host", [
    "localhost:8090", "LOCALHOST", "127.0.0.1:8090", "127.5.5.5", "[::1]:8090",
    "192.168.1.20:8090", "10.0.0.5", "172.16.3.4:80", "169.254.1.1:8090", "[fe80::1]:8090",
    "slink.local:8090",
    # Any IP literal: rebinding needs a DNS name (Tailscale 100.x, port-forwarded public IPs).
    "100.100.1.1:8090", "8.8.8.8:8090",
])
async def test_local_and_lan_hosts_pass(host):
    assert await _status(host, "POST", {"Origin": f"http://{host}"}) == (200, ["POST"])


@pytest.mark.asyncio
async def test_own_hostname_passes_with_or_without_a_domain():
    name = socket.gethostname().split(".")[0]
    for host in (name, f"{name.upper()}:8090", f"{name}.lan:8090", f"{name}.home.arpa"):
        assert (await _status(host))[0] == 200, host


@pytest.mark.asyncio
async def test_own_hostname_under_a_public_domain_is_rejected():
    """<machine>.evil.com rebinds to 127.0.0.1 just as well; only unregistrable suffixes pass."""
    name = socket.gethostname().split(".")[0]
    for host in (f"{name}.evil.com", f"{name.upper()}.attacker.net:8090"):
        assert (await _status(host, "POST", {"Origin": f"http://{host}"}))[0] == 403, host


@pytest.mark.asyncio
async def test_allow_host_names_and_globs(monkeypatch):
    monkeypatch.delenv(ALLOWED_HOSTS_ENV)
    assert (await _status("box.tail1234.ts.net"))[0] == 403
    allow_hosts(["*.TS.net", " tunnel.example "])
    assert (await _status("box.tail1234.ts.net"))[0] == 200
    assert (await _status("tunnel.example:443"))[0] == 200
    assert (await _status("other.example"))[0] == 403


@pytest.mark.asyncio
async def test_env_allow_list_is_comma_separated(monkeypatch):
    monkeypatch.setenv(ALLOWED_HOSTS_ENV, "a.example, b.example")
    assert (await _status("b.example:8090"))[0] == 200


@pytest.mark.asyncio
async def test_request_without_host_header_is_allowed():
    async def handler(request):
        return web.Response(text="ok")

    request = make_mocked_request("GET", "/x", headers={})
    assert "Host" not in request.headers
    assert (await csrf_protection(request, handler)).status == 200


# -- Anti-framing on HTML ---------------------------------------------------------------

@pytest.mark.asyncio
async def test_html_gets_frame_ancestors_and_json_does_not():
    async def page(request):
        return web.Response(text="<p>", content_type="text/html")

    async def data(request):
        return web.json_response({})

    app = web.Application(middlewares=[theme_cache])
    app.router.add_get("/page", page)
    app.router.add_get("/data", data)
    async with TestClient(TestServer(app)) as client:
        assert (await client.get("/page")).headers["Content-Security-Policy"] == "frame-ancestors 'self'"
        assert "Content-Security-Policy" not in (await client.get("/data")).headers


@pytest.mark.asyncio
async def test_run_server_html_page_cannot_be_framed(tmp_path):
    from server.server import SLinkServer, build_app
    srv = SLinkServer(data_dir=str(tmp_path / "run"))
    async with TestClient(TestServer(build_app(srv))) as client:
        response = await client.get("/")
        assert response.status == 200
        assert response.headers["Content-Security-Policy"] == "frame-ancestors 'self'"
        assert (await client.get("/", headers={"Host": "evil.example:8080"})).status == 403


# -- OBS: a saved password only goes back to the host it was saved for ----------------------

async def _obs_save(tmp_path, host, port, password=""):
    from server.server import SLinkServer
    srv = SLinkServer(data_dir=str(tmp_path / "run"))
    srv.obs._config = {"connections": {"a": {"host": "192.168.1.5", "port": 4455, "password": "hunter2"}}}
    applied = []

    async def apply(cfg):
        applied.append(cfg)

    srv.obs.apply_new_config = apply
    body = {"enabled": True, "connections": {"a": {"host": host, "port": port, "password": password}}}

    async def json_body():
        return body

    await srv.handle_obs_config(SimpleNamespace(json=json_body))
    return applied[0]["connections"]["a"]["password"]


@pytest.mark.asyncio
async def test_obs_same_host_and_port_keeps_the_saved_password(tmp_path):
    assert await _obs_save(tmp_path, "192.168.1.5", 4455) == "hunter2"


@pytest.mark.asyncio
@pytest.mark.parametrize("host,port", [("attacker.example", 4455), ("192.168.1.5", 4456)])
async def test_obs_new_target_drops_the_saved_password(tmp_path, host, port):
    assert await _obs_save(tmp_path, host, port) == ""


@pytest.mark.asyncio
async def test_obs_new_password_is_used_for_a_new_host(tmp_path):
    assert await _obs_save(tmp_path, "10.0.0.9", 4455, "newpw") == "newpw"


@pytest.mark.asyncio
async def test_obs_connect_to_a_new_host_drops_the_saved_password(tmp_path):
    """/api/obs/connect re-targets a player too; it must not replay the saved password."""
    from server.server import SLinkServer
    srv = SLinkServer(data_dir=str(tmp_path))
    srv.obs._config = {"connections": {"a": {"host": "192.168.1.5", "port": 4455, "password": "hunter2"}}}
    srv.obs.save_config = lambda: None

    async def connect(player):
        return None
    srv.obs.connect_player = connect

    async def body_new():
        return {"player": "a", "host": "attacker.example", "port": 4455}
    await srv.handle_obs_connect(SimpleNamespace(json=body_new))
    assert srv.obs._config["connections"]["a"]["password"] == ""

    srv.obs._config["connections"]["a"].update(host="192.168.1.5", password="hunter2")

    async def body_same():
        return {"player": "a", "host": "192.168.1.5", "port": 4455}
    await srv.handle_obs_connect(SimpleNamespace(json=body_same))
    assert srv.obs._config["connections"]["a"]["password"] == "hunter2"
