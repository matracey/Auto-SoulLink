import pytest

from server.server import SLinkServer
from tests.unit.test_party_snapshot_dispatch import legacy_hello, party
from tests.unit.test_server_run_reconnect import _send, _tcp


@pytest.mark.asyncio
@pytest.mark.parametrize("refusal", ["identity", "rom", "malformed"])
async def test_rejected_replacement_cannot_supersede_live_client(tmp_path, refusal):
    server = SLinkServer(data_dir=str(tmp_path))
    async with _tcp(server) as (connect, _):
        valid = await connect()
        await _send(valid, legacy_hello(party()))
        generation = server._conn_gen["a"]
        bad = await connect()
        message = legacy_hello(party())
        if refusal == "identity":
            message["ot_id"] = "22222222"
        elif refusal == "rom":
            message["rom_type"] = "not_a_game"
        else:
            message["party"] = [{}]
        await _send(bad, message)
        assert server._conn_gen["a"] == generation
        assert not server.state.identity_error.get("a")
        assert server.is_admitted("a")
        refused = await _send(bad, {"event": "tick", "player": "a", "party": []})
        assert refused["commands"] == [{"cmd": "noop", "refused": "no_hello"}]
        response = await _send(valid, {"event": "tick", "player": "a", "party": party()})
        assert not any(c.get("refused") for c in response["commands"])
        assert set(server.party_details["a"]) == {party()[0]["key"]}
