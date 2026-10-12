"""A `seq` counter belongs to a TCP connection, and a connection proves itself with hello.

The live `reconnect_new` run found both halves missing at once. Slot A's same-save relaunch
sent nine messages and was killed; a DIFFERENT cartridge then connected as slot A and sent
`hello` with seq 1. The old guard compared that seq against the *slot's* last seq across
connections, and its restart escape hatch only fired for `seq <= 1 and last > 10` — nine is
not more than ten, so the hello was dropped as a duplicate with no log line at all. Every
later tick carried seq > last, so it sailed through with no accepted hello behind it, and at
12:14:23 the unidentified cartridge's party reconciled as slot A and discarded the run's
linked keys.

So: the counter resets with the connection, and nothing but hello is listened to until this
connection has said hello.
"""
from __future__ import annotations

import asyncio
import json
import logging
from unittest.mock import AsyncMock, Mock

import pytest
import pytest_asyncio

from server.server import SLinkServer

HELLO_A = {"event": "hello", "player": "a", "rom_type": "red", "trainer_name": "Alice",
           "ot_id": "30B8", "has_pokeballs": True,
           "artifact_kind": "named", "panel": True}   # a patched Red: the companion is required (2026-10-02)


def _tick(seq: int) -> dict:
    return {"event": "tick", "player": "a", "seq": seq, "party": [], "area_id": "pallet_town"}


@pytest.fixture
def srv(tmp_path):
    """A server whose every _dispatch call is recorded as (player, event, seq)."""
    s = SLinkServer(data_dir=str(tmp_path))
    s.dispatched = []
    inner = s._dispatch

    def recording(player_id, msg):
        s.dispatched.append((player_id, msg.get("event"), msg.get("seq")))
        return inner(player_id, msg)

    s._dispatch = recording
    return s


class _Conn:
    """One TCP connection to `srv`, opened and closed like a real client's."""

    def __init__(self, port):
        self._port = port

    async def __aenter__(self):
        self._r, self._w = await asyncio.open_connection("127.0.0.1", self._port)
        return self

    async def __aexit__(self, *exc):
        self._w.close()
        await self._w.wait_closed()
        # The handler's finally block runs on the server's own task; let it.
        await asyncio.sleep(0.05)

    async def send(self, msg):
        self._w.write((json.dumps(msg) + "\n").encode())
        await self._w.drain()
        return json.loads(await asyncio.wait_for(self._r.readline(), 3))


@pytest_asyncio.fixture
async def port(srv):
    tcp = await asyncio.start_server(srv.handle_client, "127.0.0.1", 0)
    try:
        yield tcp.sockets[0].getsockname()[1]
    finally:
        tcp.close()
        await tcp.wait_closed()


@pytest.mark.asyncio
async def test_a_new_connection_starts_the_seq_counter_over(srv, port):
    """The exact live shape: a short session, then a second connection helloing at seq 1."""
    async with _Conn(port) as first:
        await first.send(dict(HELLO_A, seq=1))
        for n in (2, 3, 4):
            await first.send(_tick(n))

    async with _Conn(port) as second:
        await second.send(dict(HELLO_A, seq=1, ot_id="8C4C"))

    hellos = [d for d in srv.dispatched if d[1] == "hello"]
    assert len(hellos) == 2, f"the second connection's hello was swallowed: {srv.dispatched}"
    assert hellos[1] == ("a", "hello", 1)


@pytest.mark.asyncio
async def test_a_tick_before_hello_never_reaches_dispatch(srv, port, caplog):
    caplog.set_level(logging.WARNING, logger="server.server")
    async with _Conn(port) as c:
        assert (await c.send(_tick(1)))["commands"] == [{"cmd": "noop", "refused": "no_hello"}]
        await c.send(_tick(2))
        # ...and hello still works afterwards, on the same connection.
        await c.send(dict(HELLO_A, seq=3))
        await c.send(_tick(4))

    assert [d[1] for d in srv.dispatched] == ["hello", "tick"], srv.dispatched
    warnings = [r for r in caplog.records
                if r.levelno == logging.WARNING and "before hello" in r.getMessage()]
    assert len(warnings) == 1, [r.getMessage() for r in warnings]
    assert "tick" in warnings[0].getMessage()


@pytest.mark.asyncio
async def test_a_repeated_seq_on_one_connection_is_still_a_duplicate(srv, port):
    async with _Conn(port) as c:
        await c.send(dict(HELLO_A, seq=1))
        await c.send(_tick(2))
        assert (await c.send(_tick(2)))["commands"] == [{"cmd": "noop", "refused": "duplicate"}]
        assert (await c.send(_tick(1)))["commands"] == [{"cmd": "noop", "refused": "duplicate"}]

    assert [d[2] for d in srv.dispatched] == [1, 2], srv.dispatched


@pytest.mark.asyncio
@pytest.mark.parametrize("seq", ["2", None, [], {}, True, False, 2.0])
@pytest.mark.parametrize("event", ["hello", "tick"])
async def test_malformed_seq_is_refused_without_losing_the_connection(srv, port, caplog, seq, event):
    async with _Conn(port) as conn:
        await conn.send(dict(HELLO_A, seq=1))
        generation = srv._conn_gen["a"]
        msg = dict(HELLO_A, seq=seq) if event == "hello" else dict(_tick(2), seq=seq)
        assert (await conn.send(msg))["commands"] == [{"cmd": "noop", "refused": "malformed"}]
        assert srv._conn_gen["a"] == generation
        accepted = await conn.send(_tick(2))
        assert not any(cmd.get("refused") for cmd in accepted["commands"])
    assert srv.dispatched == [("a", "hello", 1), ("a", "tick", 2)]
    assert "non-integer seq" in caplog.text


@pytest.mark.asyncio
async def test_malformed_first_hello_does_not_claim_the_connection(srv, port):
    async with _Conn(port) as conn:
        assert (await conn.send(dict(HELLO_A, seq="1")))["commands"] == [
            {"cmd": "noop", "refused": "malformed"},
        ]
        assert srv.dispatched == []
        assert srv._conn_gen.get("a", 0) == 0
        await conn.send(dict(HELLO_A, seq=1))
        await conn.send(_tick(2))
    assert srv.dispatched == [("a", "hello", 1), ("a", "tick", 2)]


@pytest.mark.asyncio
async def test_legacy_messages_without_seq_still_bypass_the_duplicate_guard(srv, port):
    async with _Conn(port) as conn:
        await conn.send(HELLO_A)
        for _ in range(2):
            tick = _tick(0)
            del tick["seq"]
            assert not any(cmd.get("refused") for cmd in (await conn.send(tick))["commands"])
    assert srv.dispatched == [("a", "hello", None), ("a", "tick", None), ("a", "tick", None)]


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [256, 4 * 1024 * 1024])
@pytest.mark.parametrize("framing", ["buffered", "chunked", "repeated", "eof"])
async def test_oversized_frame_preserves_following_messages(srv, caplog, limit, framing):
    hello = (json.dumps(dict(HELLO_A, seq=1)) + "\n").encode()
    tick = (json.dumps(_tick(2)) + "\n").encode()
    oversized = b"x" * (limit + 1)
    assert len(hello) <= limit
    chunks = {
        "buffered": [oversized + b"\n" + hello + tick],
        "chunked": [oversized, oversized, b"tail\n" + hello + tick],
        "repeated": [oversized + b"\n" + oversized + b"\n" + hello + tick],
        "eof": [hello, oversized, oversized],
    }[framing]
    reader = asyncio.StreamReader(limit=limit)
    writer = Mock(spec=asyncio.StreamWriter)
    writer.get_extra_info.return_value = ("127.0.0.1", 12345)
    writer.is_closing.return_value = False
    writer.drain = AsyncMock()
    writer.wait_closed = AsyncMock()

    async def feed():
        for chunk in chunks:
            reader.feed_data(chunk)
            await asyncio.sleep(0)
        reader.feed_eof()

    feeder = asyncio.create_task(feed())
    try:
        await asyncio.wait_for(srv.handle_client(reader, writer), 3)
    finally:
        await feeder
    expected = [("a", "hello", 1)]
    if framing != "eof":
        expected.append(("a", "tick", 2))
    assert srv.dispatched == expected
    replies = [json.loads(call.args[0]) for call in writer.write.call_args_list]
    assert len(replies) == len(expected)
    assert not any(cmd.get("refused") for reply in replies for cmd in reply["commands"])
    assert "Oversized line" in caplog.text
    writer.close.assert_called_once()
    writer.wait_closed.assert_awaited_once()


# ── INV-SERVER x INV-CLIENT: a line the server did not process says so (`refused`) ──────────────
# The client retires an owed report only on a reply WITHOUT a `refused` command.

@pytest.mark.asyncio
async def test_every_unprocessed_line_is_answered_with_a_refused_noop(srv, port):
    async with _Conn(port) as c:
        assert (await c.send(_tick(1)))["commands"] == [{"cmd": "noop", "refused": "no_hello"}]
        accepted = await c.send({**HELLO_A, "seq": 1})
        assert not any(cmd.get("refused") for cmd in accepted["commands"])
        assert (await c.send(_tick(1)))["commands"] == [{"cmd": "noop", "refused": "duplicate"}]
        srv.state.identity_error["a"] = "wrong save"
        assert (await c.send(_tick(2)))["commands"] == [{"cmd": "noop", "refused": "identity"}]
        srv.state.identity_error.pop("a")
        srv.is_admitted = lambda pid: False
        assert (await c.send(_tick(3)))["commands"] == [{"cmd": "noop", "refused": "admission"}]

@pytest.mark.asyncio
async def test_ghost_positions_do_not_wake_sse_listeners(srv, port):
    """The RR client streams ghost_pos at 20-30 Hz; each SSE ping made the calc refetch the
    full status. A position changes nothing a page draws, so it must not ping."""
    pings = []
    srv._notify_sse = lambda: pings.append(1)
    async with _Conn(port) as c:
        await c.send(dict(HELLO_A, seq=1))
        after_hello = len(pings)
        for n in range(2, 7):
            await c.send({"event": "ghost_pos", "player": "a", "seq": n, "x": n, "y": 1, "map": 0})
        assert len(pings) == after_hello, "ghost_pos must not notify SSE"
        await c.send(_tick(7))
    assert len(pings) > after_hello, "a tick still notifies"



@pytest.mark.asyncio
async def test_a_newer_hello_supersedes_an_older_connection(srv, port):
    """KEY-SCOPE-5: a delayed tick from a superseded socket must not roll the party back."""
    async with _Conn(port) as first:
        await first.send(dict(HELLO_A, seq=1))
        async with _Conn(port) as second:
            await second.send(dict(HELLO_A, seq=1))
            assert (await first.send(_tick(2)))["commands"] == [{"cmd": "noop", "refused": "superseded"}]
            assert not (await second.send(_tick(2)))["commands"][0].get("refused")
    assert [d[1] for d in srv.dispatched] == ["hello", "hello", "tick"], srv.dispatched
