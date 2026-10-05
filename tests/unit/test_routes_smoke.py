"""Every registered GET route must render without blowing up.

Nothing in the suite rendered a page before this. `server/server.py` is 8000 lines of Jinja
context building behind ~90 routes, and template/context breakage is the regression class this
repo is most exposed to — a renamed field or a missing macro takes a page (or an OBS overlay
mid-stream) down and no test notices.

The list of routes is not written out here; it is walked off `build_app()`'s real router, so a
route added to the server is covered automatically instead of drifting away from a hand-kept copy.
"""
import pytest

aiohttp = pytest.importorskip("aiohttp")
pytest_asyncio = pytest.importorskip("pytest_asyncio")
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from server.adapters.gen1_rby import Gen1Adapter  # noqa: E402
from server.adapters.gen3_frlge import Gen3Adapter  # noqa: E402
from server.server import SLinkServer, build_app  # noqa: E402
from server.state import AreaStatus, LinkEntry, LinkStatus, MonInfo  # noqa: E402

# Routes with side effects or long-lived responses. /api/events is an SSE stream that never
# completes; the calc catch-all serves files from a vendored bundle; the launcher needs a player.
SKIP = {"/api/events"}
DYNAMIC = {
    "/calc/{path:.*}": "/calc/index.html",
    "/launcher/{player}": "/launcher/a",
    "/api/obs/scenes/{player}": "/api/obs/scenes/a",
}


def _get_routes(app):
    out = []
    for r in app.router.routes():
        if r.method != "GET":
            continue
        path = getattr(r.resource, "canonical", None) or str(r.resource)
        if path in SKIP:
            continue
        out.append(DYNAMIC.get(path, path))
    return sorted(set(out))


# EVERY GENERATION, NOT JUST RADICAL RED.
# This suite rendered one Gen 3 fixture, so every Gen 1 rendering defect the sweep found --
# the missing sprite class, the badge bitmask read as a count, the duplicated Special stat --
# went through it untouched. The key FORMAT differs per generation and adapters validate it
# (`is_valid_mon_key`), so the fixture keys come from the generation under test.
_GENERATIONS = {
    "gen3_rr": {
        "adapter": lambda: Gen3Adapter(is_rr=True),
        "keys": ("A:1", "B:2", "A:3", "B:4"),
    },
    "gen1_rby": {
        "adapter": lambda: Gen1Adapter(variant="red"),
        # DDDD:TTTT:II -- DVs, OT id, species index. A key the adapter would reject renders
        # blanks everywhere and would make this suite green on an empty page.
        "keys": ("AABB:30B8:99", "CCDD:7B0B:B1", "EEFF:30B8:15", "1122:7B0B:20"),
    },
}


@pytest.fixture(params=sorted(_GENERATIONS), ids=sorted(_GENERATIONS))
def srv(request, tmp_path):
    """A server with one linked pair and a memorialized pair, so pages have rows to render."""
    gen = _GENERATIONS[request.param]
    ka, kb, kc, kd = gen["keys"]
    s = SLinkServer(data_dir=str(tmp_path))
    st = s.state
    st.adapter = s.adapter = gen["adapter"]()
    alive = LinkEntry(area_id="route_1",
                      a=MonInfo(key=ka, level=12, species=1, nickname="BULBA"),
                      b=MonInfo(key=kb, level=13, species=4, nickname="CHAR"),
                      status=LinkStatus.ALIVE)
    dead = LinkEntry(area_id="route_2",
                     a=MonInfo(key=kc, level=9, species=10),
                     b=MonInfo(key=kd, level=9, species=13),
                     status=LinkStatus.MEMORIAL)
    for e in (alive, dead):
        st.links.append(e)
        st._index_entry(e)
    st.area_states["route_1"] = AreaStatus.LINKED
    st.area_states["route_2"] = AreaStatus.LINKED
    st.party_keys["a"].add(ka)
    st.party_keys["b"].add(kb)
    st.pokeballs_obtained = {"a": True, "b": True}
    return s


@pytest_asyncio.fixture
async def client(srv):
    c = TestClient(TestServer(build_app(srv)))
    await c.start_server()
    yield c
    await c.close()


def _route_ids():
    """Collected at import time so each route is a separately named test."""
    import types
    stub = types.SimpleNamespace()
    # build_app only reads attributes off srv to register handlers; any object with them works,
    # but the real class keeps the handler names honest.
    for name in dir(SLinkServer):
        if name.startswith("handle_") or name == "_build_sidebar_html":
            setattr(stub, name, getattr(SLinkServer, name))
    return _get_routes(build_app(stub))


@pytest.mark.asyncio
@pytest.mark.parametrize("path", _route_ids())
async def test_get_route_renders(client, path):
    resp = await client.get(path)
    ctype = resp.headers.get("Content-Type", "")
    raw = await resp.read()                     # bytes: /companion/*.ups serves a binary patch
    assert resp.status < 500, f"{path} -> {resp.status}\n{raw[:1500]!r}"
    # A 200 HTML page that came back empty means the template rendered to nothing.
    if resp.status == 200 and "text/html" in ctype:
        assert raw.decode("utf-8", "replace").strip(), f"{path} returned an empty HTML body"


@pytest.mark.asyncio
async def test_status_page_shows_the_linked_pair(client):
    body = await (await client.get("/")).text()
    assert "BULBA" in body and "CHAR" in body


@pytest.mark.asyncio
async def test_status_json_is_wellformed(client):
    data = await (await client.get("/api/status")).json()
    assert "links" in data
    assert len(data["links"]) == 2


# Every GET route on the run server (72 GET routes, including auth and health). Routes are walked off
# the live router above, which is the right way to cover new ones -- and exactly the wrong way to
# notice that collapsing nine pages into three quietly dropped sixty parametrized tests. Change
# this number on purpose, in the same commit that changes the router.
EXPECTED_GET_ROUTES = 72


def test_route_count_changes_are_deliberate():
    assert len(_route_ids()) == EXPECTED_GET_ROUTES, (
        f"{len(_route_ids())} GET routes registered, expected {EXPECTED_GET_ROUTES}. Adding or "
        "removing a route is fine; update the constant so the coverage change is on record.")


# ── Alpine double-init ───────────────────────────────────────────────────────
# Alpine auto-invokes a component's `init()` with NO arguments during initialisation. Pairing
# that with x-init="init(...)" runs the method TWICE — once with every parameter undefined.
# That is what pointed the stylesheet <link> at /static/themes/undefined.css (a 404 on every
# dashboard load) and registered duplicate htmx:afterSettle / storage listeners.
HTML_ROUTES = ["/", "/timeline", "/stream", "/debug", "/twitch", "/obs", "/launcher/a"]


@pytest.mark.asyncio
@pytest.mark.parametrize("path", HTML_ROUTES)
async def test_no_alpine_x_init_calls_a_method_named_init(client, path):
    body = await (await client.get(path)).text()
    assert 'x-init="init(' not in body and "x-init='init(" not in body, (
        f"{path} has x-init calling init(...) — Alpine also auto-calls init() with no "
        "arguments, so it will run twice, the first time with undefined parameters. "
        "Rename the method (e.g. setup()).")


@pytest.mark.asyncio
@pytest.mark.parametrize("path", HTML_ROUTES)
async def test_no_page_builds_an_undefined_theme_url(client, path):
    body = await (await client.get(path)).text()
    assert "themes/undefined" not in body


# ── content, not just a status code ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_dashboard_actually_renders_the_pair(client):
    """`status < 500` is what let a whole class of defect through.

    Every Gen 1 rendering bug this sweep found — the sprite missing the class every CSS
    rule selects on, the badge bitmask rendered as a count, one Special drawn as two stat
    chips — produced a perfectly valid 200 with a page that said the wrong thing. A smoke
    test that only checks for the absence of a stack trace cannot see any of them.

    This asserts the linked pair reaches the page at all, which is the weakest claim worth
    making and still strictly more than a status code.
    """
    resp = await client.get("/")
    assert resp.status == 200
    body = (await resp.read()).decode("utf-8", "replace")
    assert "BULBA" in body, "the linked pair's nickname is not on the dashboard"
    assert "CHAR" in body, "the partner half is not on the dashboard"


@pytest.mark.asyncio
async def test_sprites_carry_the_class_the_stylesheet_selects_on(client, srv):
    """The defect that shipped: four of six adapters emitted a bare <img>, so the
    enc-sprite swap silently no-opped and every rule keyed on it failed to match."""
    resp = await client.get("/")
    body = (await resp.read()).decode("utf-8", "replace")
    if not srv.adapter.sprite_html(1):
        pytest.skip("this generation renders no sprites")
    assert 'class="mon-sprite"' in body or 'class="enc-sprite"' in body, (
        "no sprite on the dashboard carries a class the stylesheet can select")


# ── a run the Manager spawned sends its pages to the Manager ─────────────────────────────
@pytest.mark.asyncio
@pytest.mark.parametrize("path,target", [
    ("/", "/runs/r1"), ("/debug", "/runs/r1/debug"), ("/timeline", "/runs/r1/timeline"),
    ("/twitch", "/broadcast/twitch"), ("/obs", "/broadcast/obs"),
    ("/calc/normal.html", "/runs/r1/calc/normal.html"), ("/patcher", "/patcher"),
    ("/stream", "/broadcast"),
])
async def test_a_managed_run_redirects_its_pages_to_the_manager(tmp_path, path, target):
    """The Manager is the UI. A run started by it (manager_port + run_id set) never shows
    its own chrome: every page it used to render redirects to the Manager's equivalent.
    Overlays, the API and the calc's files stay, because OBS and the clients use them."""
    srv = SLinkServer(data_dir=str(tmp_path / "run"), run_id="r1", manager_port=8090)
    async with TestClient(TestServer(build_app(srv))) as client:
        resp = await client.get(path, allow_redirects=False)
        assert resp.status == 302, path            # with or without a calc build (CI has none)
        assert resp.headers["Location"].endswith(f":8090{target}"), resp.headers["Location"]
        for kept in ("/api/status", "/calc/css/main.css"):
            r = await client.get(kept, allow_redirects=False)
            assert r.status in (200, 404), kept          # never a redirect
        assert (await client.get("/stream/linked-party", allow_redirects=False)).status == 200


@pytest.mark.asyncio
async def test_a_standalone_run_still_renders_its_own_pages(tmp_path):
    srv = SLinkServer(data_dir=str(tmp_path / "run"))
    async with TestClient(TestServer(build_app(srv))) as client:
        for path in ("/", "/debug", "/twitch", "/obs"):
            assert (await client.get(path, allow_redirects=False)).status == 200, path
