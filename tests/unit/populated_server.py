"""A server populated the way `tools/inject_full_mocks.py` populates one — in-process.

The route smoke test renders one linked pair and one memorial, which is enough to prove a
template compiles and not enough to prove it says anything. The mock injector is the
complete cast (six pairs, a pending capture, a dead zone, a boxed pair, a battle, PC boxes,
held items, badges), and it is what the payload fixtures under `tests/fixtures/ui/`
were captured from. Driving it through the REAL TCP handler — not `_dispatch`
directly — is what makes the adapter switch on hello, `connected_players` and the seq guard
all run, so the page under test is the page a live run shows.

The socket stays open until the fixture is torn down. The server marks a player disconnected
when its socket closes, and a dashboard rendered for two offline players is a different
page (see the injector's HOLD note).
"""
from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import json
import os

import pytest
import pytest_asyncio
from aiohttp.test_utils import TestClient, TestServer

from server.server import SLinkServer, build_app
from tests.unit.companion_evidence import patched

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_INJECTOR = os.path.join(_REPO, "tools", "inject_full_mocks.py")

GAMES = ("gen3", "gen1")


def _load_injector():
    # tools/ is not a package; load the script as a module so its cast and helpers are ours.
    spec = importlib.util.spec_from_file_location("inject_full_mocks", _INJECTOR)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def populate(srv: SLinkServer, game: str):
    """Wait for every injected event's TCP reply, then return the socket cleanup callback.

    A missed reply must fail setup: otherwise later reads consume earlier replies and
    populate can return before a final tick overwrites the caller's board-test changes.
    """
    tcp = await asyncio.start_server(srv.handle_client, "127.0.0.1", 0, limit=4 * 1024 * 1024)
    port = tcp.sockets[0].getsockname()[1]
    mod = _load_injector()
    mod.GAME = game
    mod.TCP_HOST, mod.TCP_PORT, mod.HOLD = "127.0.0.1", port, 0
    held: list[asyncio.StreamWriter] = []

    async def send_tcp(events):
        if not held:
            r, w = await asyncio.open_connection("127.0.0.1", port)
            held.append(w)
            held.append(r)
        w, r = held[0], held[1]
        for m in events:
            if m.get("event") == "hello":
                m = patched(m)
            w.write((json.dumps(m) + "\n").encode())
            await w.drain()
            # Shards share CPU/disk, so allow a slow setup reply, but never silently
            # skip one: the reply is the witness that this event has been dispatched.
            reply = await asyncio.wait_for(r.readline(), timeout=30)
            if not reply:
                raise EOFError(f"injector TCP closed before replying to {m['event']}")

    def http_post(path, body):
        if path == "/api/attempts":
            srv.state.attempts_count = int(body.get("count", 0))
            srv.state._save()
        return {"ok": True}

    async def close():
        if held:
            held[0].close()
            with contextlib.suppress(ConnectionError):
                await held[0].wait_closed()
        tcp.close()
        await tcp.wait_closed()

    mod.send_tcp, mod.http_post = send_tcp, http_post
    mod.print = lambda *a, **k: None
    try:
        await mod.main()
    except BaseException:
        await close()
        raise
    return close


@pytest_asyncio.fixture(params=GAMES, ids=GAMES)
async def populated(request, tmp_path):
    """(srv, client) for a fully populated run of the parametrized generation."""
    # The fixture intentionally includes a legacy dead-zone row; keep that option explicit
    # now that new runs default to same-species retries.
    srv = SLinkServer(data_dir=str(tmp_path), retry_same_species=False)
    close = await populate(srv, request.param)
    client = TestClient(TestServer(build_app(srv)))
    await client.start_server()
    try:
        yield srv, client
    finally:
        await client.close()
        await close()


@pytest.fixture
def game(request):
    return request.node.callspec.params.get("populated", "gen3")
