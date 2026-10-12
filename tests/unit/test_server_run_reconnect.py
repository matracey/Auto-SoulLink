"""Replacing a run disconnects live clients so their normal hello restores admission."""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from shutil import copyfile
from unittest.mock import AsyncMock

import pytest

from server.server import SLinkServer
from tests.unit.companion_evidence import companion
from tests.unit.test_mixed_foundations import _hello


@asynccontextmanager
async def _tcp(srv):
    tasks = set()
    writers = []
    accepted = asyncio.Queue()

    async def handle(reader, writer):
        task = asyncio.current_task()
        tasks.add(task)
        accepted.put_nowait(task)
        try:
            await srv.handle_client(reader, writer)
        finally:
            tasks.discard(task)

    tcp = await asyncio.start_server(handle, "127.0.0.1", 0)

    async def connect():
        reader, writer = await asyncio.open_connection(
            "127.0.0.1", tcp.sockets[0].getsockname()[1])
        writers.append(writer)
        await asyncio.wait_for(accepted.get(), 3)
        return reader, writer

    try:
        yield connect, tasks
    finally:
        for writer in writers:
            writer.close()
        await asyncio.gather(*(writer.wait_closed() for writer in writers),
                             return_exceptions=True)
        tcp.close()
        await tcp.wait_closed()
        await asyncio.wait_for(asyncio.gather(*tuple(tasks)), 3)


async def _send(connection, message):
    reader, writer = connection
    writer.write((json.dumps(message) + "\n").encode())
    await writer.drain()
    return json.loads(await asyncio.wait_for(reader.readline(), 3))


async def _replace(srv, replacement):
    if replacement == "reset":
        response = await srv.handle_reset_api(None)
    else:
        srv.state._save()
        backup = Path(srv.state._links_path).parent / "backups" / "links.backup.1.json"
        backup.parent.mkdir(exist_ok=True)
        copyfile(srv.state._links_path, backup)
        response = await srv.handle_debug_rollback(
            AsyncMock(json=AsyncMock(return_value={"slot": 1})))
    assert response.status == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("rom_type", ["red", "firered"])
@pytest.mark.parametrize("replacement", ["reset", "rollback"])
async def test_contractless_run_reconnects_and_processes_events(tmp_path, rom_type, replacement):
    srv = SLinkServer(data_dir=str(tmp_path))
    hello = _hello("a", {"rom_type": rom_type, **companion(rom_type)})
    async with _tcp(srv) as (connect, _):
        old = await connect()
        await _send(old, hello)
        assert srv._rom_contract is None
        assert srv.is_admitted("a")

        await _replace(srv, replacement)
        assert await asyncio.wait_for(old[0].read(), 1) == b"", (
            "replacement must force reconnect; connected clients do not spontaneously hello")

        fresh = await connect()
        await _send(fresh, hello)
        assert srv.is_admitted("a")
        assert "route_1" not in srv.state.area_states
        await _send(fresh, {"event": "no_catch", "player": "a", "area_id": "route_1"})
        assert srv.state.area_states["route_1"].value == "dead_zone"


@pytest.mark.asyncio
@pytest.mark.parametrize("replacement", ["reset", "rollback"])
async def test_run_replacement_closes_all_sockets_including_unidentified(tmp_path, replacement):
    srv = SLinkServer(data_dir=str(tmp_path))
    async with _tcp(srv) as (connect, _):
        sockets = [await connect() for _ in range(4)]
        for connection, player in zip(sockets, ("a", "a", "b"), strict=False):
            await _send(connection, _hello(player, {"rom_type": "red", **companion("red")}))
        # The fourth connection has not declared a player yet.
        await _replace(srv, replacement)
        for connection in sockets:
            assert await asyncio.wait_for(connection[0].read(), 1) == b""


@pytest.mark.asyncio
async def test_old_disconnect_does_not_mark_live_replacement_disconnected(tmp_path):
    srv = SLinkServer(data_dir=str(tmp_path))
    hello = _hello("a", {"rom_type": "red", **companion("red")})
    async with _tcp(srv) as (connect, tasks):
        old = await connect()
        await _send(old, hello)
        old_task = next(iter(tasks))
        fresh = await connect()
        await _send(fresh, hello)
        assert srv.connected_players["a"]["connected"]
        old[1].close()
        await old[1].wait_closed()
        await asyncio.wait_for(old_task, 3)
        assert srv.connected_players["a"]["connected"]
        await _send(fresh, {"event": "no_catch", "player": "a", "area_id": "route_1"})
        assert srv.state.area_states["route_1"].value == "dead_zone"


@pytest.mark.asyncio
@pytest.mark.parametrize("rom_type", ["firered", "unknown"])
async def test_rejected_sockets_clear_presence_without_owning_gameplay(tmp_path, rom_type):
    srv = SLinkServer(data_dir=str(tmp_path))
    hello = _hello("a", {"rom_type": "firered", **companion("firered"), "ot_id": 0x1234})
    async with _tcp(srv) as (connect, tasks):
        original = await connect()
        original_task = next(iter(tasks))
        await _send(original, hello)
        original[1].close()
        await original[1].wait_closed()
        await asyncio.wait_for(original_task, 3)
        assert not srv.connected_players["a"]["connected"]
        rejected = []
        for _ in range(2):
            previous = set(tasks)
            connection = await connect()
            handler = next(iter(tasks - previous))
            await _send(connection, {**hello, "rom_type": rom_type, "ot_id": 0x5678})
            rejected.append((connection, handler))
        assert srv.connected_players["a"]["connected"]
        assert not srv._client_writers.get("a")
        for index, (connection, handler) in enumerate(rejected):
            connection[1].close()
            await connection[1].wait_closed()
            await asyncio.wait_for(handler, 3)
            assert srv.connected_players["a"]["connected"] == (index == 0)
        assert not srv._client_writers.get("a")


@pytest.mark.asyncio
async def test_a_hello_from_a_does_not_admit_b_on_the_same_socket(tmp_path, monkeypatch):
    srv = SLinkServer(data_dir=str(tmp_path))
    dispatched = []
    dispatch = srv._dispatch
    def record(player, message):
        dispatched.append((player, message.get("event")))
        return dispatch(player, message)
    monkeypatch.setattr(srv, "_dispatch", record)
    async with _tcp(srv) as (connect, _):
        socket = await connect()
        await _send(socket, _hello("a", {"rom_type": "red"}))
        tick = {"event": "tick", "player": "b", "party": []}
        refused = await _send(socket, tick)
        assert any(c.get("refused") == "no_hello" for c in refused["commands"])
        assert not srv.connected_players.get("b", {}).get("connected")
        assert ("b", "tick") not in dispatched
        await _send(socket, _hello("b", {"rom_type": "red"}))
        await _send(socket, tick)
        assert srv.is_admitted("b") and ("b", "tick") in dispatched


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_area", [False, True])
async def test_non_hello_adapter_error_refuses_and_keeps_the_socket(tmp_path, monkeypatch, caplog, bad_area):
    srv = SLinkServer(data_dir=str(tmp_path))
    async with _tcp(srv) as (connect, _):
        socket = await connect()
        await _send(socket, _hello("a", {"rom_type": "red"}))
        adapter = srv.state._adapter_for("a")
        def broken(_area):
            raise RuntimeError("pack decode failed")
        monkeypatch.setattr(adapter, "is_gift_area", broken)
        reply = await _send(socket, {"event": "capture", "player": "a", "area_id": ["route_1"] if bad_area else "route_1",
                                     "key": "A:1", "species_id": 19, "level": 5})
        assert any(c.get("refused") == "error" for c in reply["commands"])
        assert "capture" in caplog.text
        assert ("unhashable" if bad_area else "pack decode failed") in caplog.text
        tick = await _send(socket, {"event": "tick", "player": "a", "party": []})
        assert not any(c.get("refused") for c in tick["commands"])
        assert srv.connected_players["a"]["connected"] and srv.connected_players["a"]["last_event"] == "tick"


def test_hello_dispatch_error_still_rolls_back_and_raises(tmp_path, monkeypatch):
    srv = SLinkServer(data_dir=str(tmp_path))
    adapter = srv.adapter
    def broken(*_args):
        raise RuntimeError("hello decode failed")
    monkeypatch.setattr(srv.state, "handle_event", broken)
    with pytest.raises(RuntimeError, match="hello decode failed"):
        srv._dispatch("a", _hello("a", {"rom_type": "red"}))
    assert srv.adapter is adapter and srv.state.adapter is adapter
