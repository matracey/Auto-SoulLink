import asyncio
import json

import pytest
import pytest_asyncio

from server.server import SLinkServer


async def send(reader, writer, payload):
    writer.write((json.dumps(payload) + "\n").encode())
    await writer.drain()
    return json.loads(await reader.readline())


def hello(player="a", key=None, seq=1):
    payload = {
        "event": "hello",
        "player": player,
        "seq": seq,
        "rom_type": "yellow",
        "foundation": "gen1_rby",
        "artifact_kind": "clean",
        "party": [],
    }
    if key is not None:
        payload["instrumentation_key"] = key
    return payload


@pytest_asyncio.fixture
async def keyed_server(tmp_path):
    app = SLinkServer(
        data_dir=str(tmp_path),
        instrumentation_keys={"a": "player-a-key", "b": "player-b-key"},
    )
    tcp = await asyncio.start_server(app.handle_client, "127.0.0.1", 0)
    port = tcp.sockets[0].getsockname()[1]
    try:
        yield app, port
    finally:
        tcp.close()
        await tcp.wait_closed()


@pytest.mark.asyncio
async def test_missing_and_unknown_keys_are_rejected(keyed_server):
    _, port = keyed_server
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        missing = await send(reader, writer, hello())
        unknown = await send(reader, writer, hello(key="wrong", seq=2))
    finally:
        writer.close()
        await writer.wait_closed()

    assert missing["commands"] == [{"cmd": "noop", "refused": "instrumentation_key"}]
    assert unknown["commands"] == [{"cmd": "noop", "refused": "instrumentation_key"}]


@pytest.mark.asyncio
async def test_key_must_match_claimed_player(keyed_server):
    _, port = keyed_server
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        result = await send(reader, writer, hello(player="b", key="player-a-key"))
    finally:
        writer.close()
        await writer.wait_closed()

    assert result["commands"] == [{"cmd": "noop", "refused": "instrumentation_player"}]


@pytest.mark.asyncio
async def test_bound_connection_ignores_later_player_claims(keyed_server):
    app, port = keyed_server
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        accepted = await send(reader, writer, hello(key="player-a-key"))
        tick = await send(
            reader,
            writer,
            {"event": "tick", "player": "b", "seq": 2, "party": []},
        )
    finally:
        writer.close()
        await writer.wait_closed()

    assert "commands" in accepted and "commands" in tick
    assert app.connected_players["a"]["last_event"] == "tick"
    assert "b" not in app.connected_players


@pytest.mark.asyncio
async def test_same_key_can_reconnect_on_a_new_socket(keyed_server):
    _, port = keyed_server
    for _ in range(2):
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            result = await send(reader, writer, hello(key="player-a-key"))
            assert not any(
                command.get("refused", "").startswith("instrumentation")
                for command in result["commands"]
            )
        finally:
            writer.close()
            await writer.wait_closed()


def test_duplicate_configured_keys_are_refused(tmp_path):
    with pytest.raises(ValueError, match="unique"):
        SLinkServer(
            data_dir=str(tmp_path),
            instrumentation_keys={"a": "same-key", "b": "same-key"},
        )
