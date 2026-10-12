"""C-0 (part 1): the protocol schema tracks the server, which is the contract's authority.

Every event name the server dispatches on is in the schema and vice versa; every command
literal the server emits is in the schema and vice versa. A client conformance suite built
on this schema therefore tests the contract, not the Gen 3 client that inspired it.
"""
from __future__ import annotations

import pathlib
import re

import pytest

from tests.unit import protocol_schema as ps

REPO = pathlib.Path(__file__).resolve().parents[2]
STATE = (REPO / "server" / "state.py").read_text(encoding="utf-8")
SERVER = (REPO / "server" / "server.py").read_text(encoding="utf-8")
RECOVERY = (REPO / "server" / "recovery.py").read_text(encoding="utf-8")


def _server_events() -> set[str]:
    names = set(re.findall(r'event == "([a-z_]+)"', STATE))
    for group in re.findall(r'event in \(([^)]*)\)', STATE):
        names |= set(re.findall(r'"([a-z_]+)"', group))
    for group in re.findall(r'event_type in \(([^)]*)\)|etype in \(([^)]*)\)', SERVER):
        for g in group:
            names |= set(re.findall(r'"([a-z_]+)"', g))
    return names


def _server_commands() -> set[str]:
    names = set(re.findall(r'"cmd": *"([a-z_]+)"', STATE + SERVER + RECOVERY))
    for pair in re.findall(r'cmd_name = "([a-z_]+)" if explode else "([a-z_]+)"', STATE):
        names |= set(pair)  # the one command name built conditionally (state.py:2621)
    return names


def test_every_dispatched_event_is_in_the_schema_and_vice_versa():
    server = _server_events()
    assert "tick" in server and "hello" in server, "extraction regex lost the shared tick/hello path"
    missing = server - set(ps.EVENTS)
    stale = set(ps.EVENTS) - server
    assert not missing, f"server dispatches events the schema lacks: {sorted(missing)}"
    assert not stale, f"schema lists events the server never dispatches: {sorted(stale)}"


def test_every_emitted_command_is_in_the_schema_and_vice_versa():
    server = _server_commands()
    missing = server - set(ps.COMMANDS)
    stale = set(ps.COMMANDS) - server
    assert not missing, f"server emits commands the schema lacks: {sorted(missing)}"
    assert not stale, f"schema lists commands the server never emits: {sorted(stale)}"


def test_every_ack_and_cancel_event_exists():
    for cmd, (ack, nack) in ps.ACKS.items():
        assert cmd in ps.COMMANDS
        for ev in (ack, nack):
            assert ev is None or ev in ps.EVENTS, (cmd, ev)
    for cmd, (ev, field, _code) in ps.PROMPT_CANCEL.items():
        assert cmd in ps.COMMANDS and ev in ps.EVENTS and field in ps.EVENTS[ev][0]


@pytest.mark.parametrize("msg, ok", [
    ({"event": "faint", "player": "a", "seq": 3, "key": "ABCD:1234:99"}, True),
    ({"event": "faint", "player": "a", "seq": 3, "key": "ABCD1234:00000099"}, True),  # Gen 3 key
    ({"event": "faint", "player": "c", "seq": 3, "key": "ABCD:1234:99"}, False),
    ({"event": "faint", "player": "a", "seq": 3}, False),  # missing key
    ({"event": "capture", "player": "b", "seq": 1, "key": "0000:0000:01", "area_id": "route_1",
      "species_id": 0x99, "level": 5, "in_box": False}, True),
    ({"event": "capture", "player": "b", "seq": 1, "key": "0000:0000:01", "area_id": "route_1",
      "species_id": "153"}, False),  # species_id must be int
    ({"event": "no_catch", "player": "a", "seq": 9, "area_id": "route_1", "species_id": 1, "level": 3}, True),
    ({"event": "trainer_battle_start", "player": "a", "seq": 9, "trainer_id": True}, False),  # bool is not int
    ({"event": "mystery", "player": "a", "seq": 9}, False),
])
def test_validate_event(msg, ok):
    assert (ps.validate_event(msg) == []) is ok, ps.validate_event(msg)


@pytest.mark.parametrize("cmd, ok", [
    ({"cmd": "noop"}, True),
    ({"cmd": "force_faint", "key": "ABCD:1234:99", "nickname": "PIKA"}, True),
    ({"cmd": "force_faint"}, False),
    ({"cmd": "apply_trade", "slot": 0, "blob_hex": "AB" * 66, "old_key": "ABCD:1234:99", "token": "t1"}, True),
    ({"cmd": "apply_trade", "slot": 0, "blob_hex": "ABC", "old_key": "ABCD:1234:99", "token": "t1"}, False),
    ({"cmd": "hud_show", "text": "WRONG SAVE", "color": [255, 0, 0], "duration": 300}, True),
    ({"cmd": "show_choices", "token": "t2", "options": ["Trade", "Say hey"], "text": "?"}, True),
    ({"cmd": "bogus"}, False),
])
def test_validate_command(cmd, ok):
    assert (ps.validate_command(cmd) == []) is ok, ps.validate_command(cmd)


def test_validate_reply_requires_a_non_empty_command_list():
    assert ps.validate_reply({"commands": [{"cmd": "noop"}]}) == []
    assert ps.validate_reply({"commands": []})
    assert ps.validate_reply({"commands": [{"cmd": "noop"}], "extra": 1})


def test_key_change_replies_are_one_way_and_the_reason_vocabulary_is_documented():
    """PLAN A1: the two replies are server commands the client never answers (no ACKS row),
    and every documented `reason` is what state.py/docs name."""
    for cmd in ("key_change_ack", "key_change_rejected"):
        assert cmd in ps.COMMANDS and cmd not in ps.ACKS and cmd not in ps.DEFERRED
    assert ps.validate_command({"cmd": "key_change_ack", "old_key": "ABCD:1234:99",
                                "new_key": "FFFF:1234:99", "migrated": True}) == []
    assert ps.validate_command({"cmd": "key_change_rejected", "old_key": "ABCD:1234:99",
                                "new_key": "FFFF:1234:99", "reason": "key collision: party_keys"}) == []
    assert ps.validate_command({"cmd": "key_change_ack", "old_key": "ABCD:1234:99"})
    assert set(ps.KEY_CHANGE_REASONS) == {"nature_change", "evolution", "npc_trade", "trade_undo",
                                          "transform", "apex_chip"}
    doc = (REPO / "docs" / "protocol.md").read_text(encoding="utf-8")
    for reason in ps.KEY_CHANGE_REASONS:
        assert f"`{reason}`" in doc, f"docs/protocol.md does not document reason {reason!r}"
    assert "artifact_kind" in ps.EVENTS["hello"][1] and "`artifact_kind`" in doc


# P3a.2 (C-0 schema half): the hello's pairing fields. The relation rom_type -> foundation is
# the server's own table (foundation_for_rom_type); which clients must declare is ps data.
_HELLO = {"event": "hello", "player": "a", "seq": 1, "party": []}
_GEN2_ROM_TYPES = ("Crystal", "crystal", "Gold", "gold", "Silver", "silver")


@pytest.mark.parametrize("rom_type", _GEN2_ROM_TYPES)
def test_a_gen2_hello_must_declare_foundation_and_artifact_kind(rom_type):
    ok = dict(_HELLO, rom_type=rom_type, foundation="gen2_gsc", artifact_kind="clean")
    assert ps.validate_event(ok) == [], ps.validate_event(ok)
    for field in ("foundation", "artifact_kind"):
        bare = {k: v for k, v in ok.items() if k != field}
        assert ps.validate_event(bare), f"{rom_type} hello without {field!r} accepted"
    # another generation's (or the legacy) foundation claimed on a Gen 2 cartridge
    for claim in ("gen1_rby", "gen1_purergb", "gen3_frlg", "gen3_rr", "gen2_crystal", None, "", 0, False):
        assert ps.validate_event(dict(ok, foundation=claim)), (rom_type, claim)


def test_the_declaring_foundations_are_the_servers_and_gen3_declares_nothing():
    from server.adapters import _ROM_TYPE_TO_GAME_ID, foundation_for_rom_type
    served = {foundation_for_rom_type(rt) for rt in _ROM_TYPE_TO_GAME_ID}
    assert ps.HELLO_DECLARES.issubset(served)
    # Gen 3's reference client sends neither field (§2.1): still conformant without them
    assert ps.validate_event(dict(_HELLO, rom_type="firered")) == []
    # a Gen 1 hello declares like Gen 2 (lua/gen1/client.lua builds both)
    assert ps.validate_event(dict(_HELLO, rom_type="red"))
    assert ps.validate_event(dict(_HELLO, rom_type="red", foundation="gen1_rby", artifact_kind="clean")) == []
    # crystal_ap is refused outright (O-25): a gen2_gsc claim on it is refused too
    assert ps.validate_event(dict(_HELLO, rom_type="crystal_ap", foundation="gen2_gsc"))
    assert ps.validate_event(dict(_HELLO, rom_type="not_a_game"))


@pytest.mark.parametrize("kind", [None, "", False, 0, [], {}, "bogus"])
def test_a_present_artifact_kind_must_be_a_documented_string(kind):
    """§2.2 step 1': absent is not empty. server.py:1321 (`msg.get("artifact_kind") or "clean"`)
    admits a falsy value as clean today; the contract refuses it (shared carry, R5)."""
    for rom_type, extra in (("firered", {}), ("crystal", {"foundation": "gen2_gsc"})):
        msg = dict(_HELLO, rom_type=rom_type, artifact_kind=kind, **extra)
        assert ps.validate_event(msg), (rom_type, kind)
    for kind_ok in ps.ARTIFACT_KINDS:
        assert ps.validate_event(dict(_HELLO, rom_type="firered", artifact_kind=kind_ok)) == []
