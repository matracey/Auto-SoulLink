"""RF-1: randomized cartridge proof at the real hello/admission boundary."""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import struct
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from server.server import SLinkServer
from tests.unit.companion_evidence import companion
from tests.unit.test_gen3_rom_ingest import _clean, _payload


@asynccontextmanager
async def client(server):
    listener = await asyncio.start_server(server.handle_client, "127.0.0.1", 0,
                                          limit=4 * 1024 * 1024)
    reader, writer = await asyncio.open_connection("127.0.0.1", listener.sockets[0].getsockname()[1])

    async def send(message):
        writer.write((json.dumps(message) + "\n").encode())
        await writer.drain()
        return json.loads(await asyncio.wait_for(reader.readline(), 3))

    try:
        yield send
    finally:
        writer.close()
        await writer.wait_closed()
        listener.close()
        await listener.wait_closed()
        await asyncio.sleep(0)


def hello(rom_type="firered", kind="rand", **fields):
    return {**companion(rom_type), "event": "hello", "player": "a", "rom_type": rom_type, "artifact_kind": kind,
            "trainer_name": "A", "ot_id": "30B8", "has_pokeballs": True, "party": [], **fields}


def randomized_payload(title="firered", first_species=75):
    """A valid allowed trainer-only edit, with every rule table still pin-matching."""
    from tests.unit.test_gen3_rom_content_lua import symbols

    raw = bytearray(_clean(title))
    head = symbols(title, len(raw))["gTrainers"]["address"] - 0x08000000
    party = struct.unpack_from("<I", raw, head + 414 * 40 + 36)[0] - 0x08000000
    struct.pack_into("<H", raw, party + 4, first_species)  # Default control: Graveler.
    return _payload(bytes(raw), title)


@pytest.mark.asyncio
@pytest.mark.parametrize("title", ("firered", "leafgreen"))
async def test_rand_hello_without_cartridge_proof_is_refused_by_name(tmp_path, title):
    server = SLinkServer(data_dir=str(tmp_path))
    async with client(server) as send:
        response = await send(hello(rom_type=title))
    assert response["commands"] == [{"cmd": "noop", "refused": "admission"}]
    assert server.admission["a"]["state"] == "rejected"
    assert "randomized" in server.admission["a"]["reason"].lower()
    assert "rom_content" in server.admission["a"]["reason"]


@pytest.mark.parametrize("payload", (
    None, {}, [], {"tables": []},
    {"tables": [{"addr": 0x08000000, "hex": "zz"}], "fingerprint": ""},
    {"tables": [{"addr": 0x08000000, "hex": "00"}], "fingerprint": "0" * 40},
    {"tables": [{"addr": 0x08000000, "hex": "00"}],
     "fingerprint": hashlib.sha1(b"\0").hexdigest()},  # valid hash, missing required tables
), ids=("null", "empty-object", "empty-array", "empty-tables", "invalid-hex",
        "bad-transport-hash", "undecodable-tables"))
@pytest.mark.asyncio
async def test_incomplete_or_invalid_rand_proof_is_never_admitted(tmp_path, payload):
    server = SLinkServer(data_dir=str(tmp_path))
    async with client(server) as send:
        response = await send(hello(rom_content=payload))
    assert response["commands"] == [{"cmd": "noop", "refused": "admission"}]
    assert "rom_content" in server.admission["a"]["reason"]
    assert server.adapter_for("a").trainer_brief(414) is None


@pytest.mark.parametrize("title", ("firered", "leafgreen"))
@pytest.mark.asyncio
async def test_a_valid_rand_report_is_admitted_but_its_bad_hash_is_not(tmp_path, title):
    valid = randomized_payload(title)
    for label, report in (("valid", valid), ("bad_hash", {**valid, "fingerprint": "0" * 40})):
        server = SLinkServer(data_dir=str(tmp_path / label))
        async with client(server) as send:
            response = await send(hello(rom_type=title, rom_content=report))
        if label == "valid":
            assert server.admission["a"]["state"] == "admitted"
            assert server.adapter_for("a").trainer_brief(414)["party"][0]["species"] == "Graveler"
        else:
            assert response["commands"] == [{"cmd": "noop", "refused": "admission"}]
            assert "fingerprint" in server.admission["a"]["reason"]


@pytest.mark.parametrize("title", ("firered", "leafgreen"))
@pytest.mark.parametrize("proof", ("missing", "empty", "bad-hash", "valid"))
@pytest.mark.asyncio
async def test_supported_rand_overlay_requires_verified_content(tmp_path, title, proof):
    fields = {}
    if proof == "empty":
        fields["rom_content"] = {}
    elif proof in ("bad-hash", "valid"):
        fields["rom_content"] = randomized_payload(title)
        if proof == "bad-hash":
            fields["rom_content"]["fingerprint"] = "0" * 40
    server = SLinkServer(data_dir=str(tmp_path))
    async with client(server) as send:
        response = await send(hello(title, "rand_overlay", **fields))
    if proof == "valid":
        assert server.admission["a"]["state"] == "admitted"
        assert server.adapter_for("a").trainer_party(414)[0]["species"] == "Graveler"
    else:
        assert server.admission["a"]["state"] == "rejected"
        assert "rom_content" in server.admission["a"]["reason"]
        assert response["commands"] == [{"cmd": "noop", "refused": "admission"}]


# Patch-first (owner 2026-10-02): a CLEAN FR/LG/RR/Red/Blue hello is refused before this admission is asked, so
# the cartridges that reach it declare the companion (Gen 3 "companion", Red "named" + its mailbox `panel`).
@pytest.mark.parametrize("rom_type,kind", (
    ("red", "named"), ("red", "rand"), ("yellow", "clean"), ("crystal", "overlay"),
    ("firered", "companion"), ("leafgreen", "companion"), ("firered_rr", "companion"),
))
@pytest.mark.parametrize("fields", ({}, {"rom_content": {}}, {"rom_content": {"bad": True}}))
@pytest.mark.asyncio
async def test_legacy_and_clean_admission_behavior_is_unchanged(tmp_path, rom_type, kind, fields):
    server = SLinkServer(data_dir=str(tmp_path))
    async with client(server) as send:
        response = await send(hello(rom_type, kind, panel=True, **copy.deepcopy(fields)))
    assert server.admission["a"] == {
        "state": "admitted", "reason": "no randomized-ROM contract for this run"}
    assert not any(command.get("refused") == "admission" for command in response["commands"])


@pytest.mark.asyncio
async def test_unparseable_rand_report_is_refused_instead_of_only_hiding_tables(tmp_path):
    server = SLinkServer(data_dir=str(tmp_path))
    async with client(server) as send:
        response = await send(hello(rom_content="not a table report"))
    assert response["commands"] == [{"cmd": "noop", "refused": "admission"}]
    assert server.admission["a"]["state"] == "rejected"
    assert "rom_content" in server.admission["a"]["reason"]


@pytest.mark.asyncio
async def test_radical_red_cannot_use_the_no_fingerprint_fallback_for_rand(tmp_path):
    server = SLinkServer(data_dir=str(tmp_path))
    async with client(server) as send:
        response = await send(hello("firered_rr", "rand"))
    assert response["commands"][0]["cmd"] == "hud_show"  # refused before kind normalization
    assert server.admission["a"]["state"] == "rejected"
    assert "randomized" in server.admission["a"]["reason"]


@pytest.mark.asyncio
async def test_a_contract_does_not_let_rand_bypass_an_unsupported_fingerprint_hook(tmp_path):
    Path(tmp_path, "rom_contract.json").write_text(json.dumps({
        "players": {"a": {"fingerprint": "0" * 64}}}), encoding="utf-8")
    server = SLinkServer(data_dir=str(tmp_path))
    async with client(server) as send:
        response = await send(hello("firered_rr", "rand", rom_content={"unreadable": True}))
    assert response["commands"][0]["cmd"] == "hud_show"
    assert server.admission["a"]["state"] == "rejected"
    assert "randomized" in server.admission["a"]["reason"]


@pytest.mark.parametrize("fields", ({}, {"rom_content": {}}, {"rom_content": None}),
                         ids=("omitted", "empty", "null"))
@pytest.mark.parametrize("contracted", (False, True), ids=("no-contract", "contract"))
@pytest.mark.asyncio
async def test_refused_rand_reconnect_drops_stale_tables_but_keeps_the_partner(tmp_path, fields, contracted):
    from server.adapters.gen3_frlge import Gen3Adapter

    a_report, b_report = randomized_payload(), randomized_payload("leafgreen", first_species=76)
    if contracted:
        fingerprint = Gen3Adapter(rom_type="firered").rom_content_fingerprint
        Path(tmp_path, "rom_contract.json").write_text(json.dumps({"players": {
            "a": {"fingerprint": fingerprint(a_report)},
            "b": {"fingerprint": fingerprint(b_report)},
        }}), encoding="utf-8")
    server = SLinkServer(data_dir=str(tmp_path))
    async with client(server) as send:
        await send(hello(rom_content=a_report))
        await send(hello("leafgreen", rom_content=b_report, player="b", trainer_name="B", ot_id="7B0B"))
        assert server.adapter_for("a").trainer_party(414)[0]["species"] == "Graveler"
        partner = copy.deepcopy(server.adapter_for("b").trainer_brief(414))
        assert partner["party"][0]["species"] == "Golem"
        response = await send(hello(**copy.deepcopy(fields)))
        assert response["commands"] == [{"cmd": "noop", "refused": "admission"}]
        assert server.admission["a"]["state"] == "rejected"
        assert server.adapter_for("a").trainer_brief(414) is None
        assert server.adapter_for("a").encounter_table("route_1") is None
        assert server.adapter_for("b").trainer_brief(414) == partner


@pytest.mark.asyncio
@pytest.mark.parametrize("same_socket", [False, True])
@pytest.mark.parametrize("bad_content", [None, {}, {"tables": [{"addr": 0x08000000, "hex": "zz"}]}])
async def test_refused_rand_hello_preserves_only_a_surviving_connection_adapter(
        tmp_path, monkeypatch, same_socket, bad_content):
    from server.adapters.gen3_frlge import Gen3Adapter

    # ROM decoding is modeled; admission, socket ownership and rollback are real.
    proof = {"model_valid": True}
    tables = {"route_1": {"Grass": [{"species": 75, "min_level": 5, "max_level": 5}]}}
    refuse = Gen3Adapter.refused_rom_content
    monkeypatch.setattr(Gen3Adapter, "refused_rom_content",
                        lambda self, payload, **kw: "" if payload == proof else refuse(self, payload, **kw))
    monkeypatch.setattr(Gen3Adapter, "ingest_rom_content", lambda self, payload: tables)
    server = SLinkServer(data_dir=str(tmp_path))
    async with client(server) as active, client(server) as competing:
        accepted = await active(hello(rom_content=proof))
        assert not any(command.get("refused") for command in accepted["commands"])
        adapter = server._player_adapters["a"]
        assert adapter.encounter_table("route_1") == tables["route_1"]
        generation = server._conn_gen["a"]
        refused = await (active if same_socket else competing)(hello(rom_content=bad_content))
        assert refused["commands"] == [{"cmd": "noop", "refused": "admission"}]
        assert server._conn_gen["a"] == generation
        if same_socket:
            assert "a" not in server._player_adapters
            assert not server.is_admitted("a")
        else:
            assert server.adapter_for("a") is adapter
            assert adapter.encounter_table("route_1") == tables["route_1"]
            assert server.is_admitted("a")
            tick = await active({"event": "tick", "player": "a", "party": []})
            assert not any(command.get("refused") for command in tick["commands"])
