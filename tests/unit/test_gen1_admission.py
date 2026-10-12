"""A run built from randomized ROMs must refuse to record events from the wrong cartridge.

Rule state built on the wrong ROM is worse than no run at all: the links, the dead zones and
the memorial all look real and all describe a game nobody played. So a run whose ROMs the
Manager built records each player's cartridge fingerprint, and a client is admitted to
connect and say hello and to NOTHING else until it proves it is running that cartridge.

The two halves that have to agree are computed in different places from different inputs --
the Manager fingerprints a ROM FILE, the server fingerprints what a CLIENT REPORTS -- so the
first test here is that they produce the same value for the same cartridge. Everything after
that is worthless if they do not.
"""
from __future__ import annotations

import json
import os

import pytest

from server.adapters import get_adapter
from server.adapters.gen1_rom_scan import (
    RomScanError,
    content_fingerprint,
    fingerprint_rom,
    parse_client_content,
    scan_fishing,
    scan_wild,
)

_REPO = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
_ROMS = {
    "red": os.path.join(_REPO, "patch", "build", "gen1_red.gb"),
    "blue": os.path.join(_REPO, "patch", "build", "gen1_blue.gb"),
    "yellow": os.path.join(_REPO, "patch", "build", "gen1_yellow.gbc"),
}


def _rom(title):
    if not os.path.exists(_ROMS[title]):
        pytest.skip(f"{_ROMS[title]} not present")
    with open(_ROMS[title], "rb") as f:
        return f.read()


def _payload_from_rom(rom: bytes, variant: str) -> dict:
    """Exactly what the rewritten client (lua/gen1/client.lua) sends: raw hex, no interpretation."""
    wild, fish = scan_wild(rom), scan_fishing(rom)

    def rec(r):
        out = bytearray()
        for key in ("grass", "water"):
            block = r[key]
            if block is None:
                out.append(0)
                continue
            out.append(block["rate"])
            for s in block["slots"]:
                out += bytes((s["level"], s["species_index"]))
        return out.hex().upper()

    return {
        "variant": variant,
        "wild": {str(m): rec(r) for m, r in wild.items()},
        "old_rod": bytes((fish["old_rod"][0]["species_index"],
                          fish["old_rod"][0]["level"])).hex().upper(),
        "good_rod": b"".join(bytes((e["level"], e["species_index"]))
                             for e in fish["good_rod"]).hex().upper(),
        # Yellow's super rod shares no format with R/B: flat 4 x (species, level) and no
        # count byte. Encoding it the R/B way here produced 9-byte records the parser
        # rightly refused -- the Lua reader has always branched on this, my helper did not.
        "super_rod": {
            str(m): (b"".join(bytes((e["species_index"], e["level"])) for e in v).hex().upper()
                     if variant == "yellow"
                     else (bytes((len(v),)) + b"".join(
                         bytes((e["level"], e["species_index"])) for e in v)).hex().upper())
            for m, v in fish["super_rod"].items()},
    }


# ── the two fingerprints must agree ──────────────────────────────────────────────────────
@pytest.mark.parametrize("title", ["red", "blue", "yellow"])
def test_the_manager_and_the_server_fingerprint_the_same_cartridge_alike(title):
    """One is computed from a ROM file, the other from a client's report. If they ever
    disagreed, every correctly-configured run would be rejected."""
    rom = _rom(title)
    adapter = get_adapter("gen1_rby", rom_type=title.capitalize())
    assert adapter.rom_content_fingerprint(_payload_from_rom(rom, title)) == fingerprint_rom(rom)


def test_different_cartridges_fingerprint_differently():
    assert fingerprint_rom(_rom("red")) != fingerprint_rom(_rom("blue"))


def test_the_fingerprint_moves_when_a_single_encounter_slot_changes():
    """The check has to be sensitive enough to catch a different SEED, which differs from
    the contracted ROM only in table contents."""
    rom = _rom("red")
    base = _payload_from_rom(rom, "red")
    tweaked = json.loads(json.dumps(base))
    first = sorted(tweaked["wild"])[0]
    hexs = tweaked["wild"][first]
    # Flip the species byte of slot 0 (offset 1 in the record, so hex chars 2-4).
    swapped = int(hexs[2:4], 16) ^ 0x01
    tweaked["wild"][first] = hexs[:2] + f"{swapped:02X}" + hexs[4:]

    adapter = get_adapter("gen1_rby", rom_type="Red")
    assert adapter.rom_content_fingerprint(tweaked) != adapter.rom_content_fingerprint(base)


def test_a_malformed_report_raises_rather_than_fingerprinting_partial_data():
    """"Unreadable report" and "wrong ROM" need different messages, so this must not
    quietly return some digest of half a payload."""
    adapter = get_adapter("gen1_rby", rom_type="Red")
    with pytest.raises(RomScanError):
        adapter.rom_content_fingerprint({"variant": "red", "wild": {"12": "ZZ"}})


def test_the_fingerprint_ignores_bytes_no_client_can_report():
    """It covers wild and fishing only, because that is all a client reads. A digest
    including base stats could never be matched by a client and so could never admit one."""
    rom = _rom("red")
    direct = content_fingerprint("red", scan_wild(rom), scan_fishing(rom))
    from_payload = parse_client_content(_payload_from_rom(rom, "red"))
    assert content_fingerprint(from_payload["variant"], from_payload["wild"],
                               from_payload["fishing"]) == direct


# ── the gate ─────────────────────────────────────────────────────────────────────────────
class _Server:
    """The attributes the admission path touches, with the real methods bound onto it."""

    def __init__(self, contract, adapter=None):
        from types import SimpleNamespace

        from server.server import SLinkServer
        self.adapter = adapter or get_adapter("gen1_rby", rom_type="Red")
        self.state = SimpleNamespace(rom_type="Red", artifact_kind="clean")
        self._rom_contract = contract
        self.admission = {}
        for name in ("_decide_admission", "is_admitted"):
            setattr(self, name, getattr(SLinkServer, name).__get__(self))


def _contract(fingerprints: dict) -> dict:
    return {"upr_version": "4.6.1", "categories": ["wild"],
            "players": {p: {"fingerprint": f} for p, f in fingerprints.items()}}


def test_without_a_contract_everyone_is_admitted():
    """Every vanilla run, and the entire pre-existing test suite, lives here."""
    srv = _Server(None)
    v = srv._decide_admission("a", {"event": "hello"})
    assert v["state"] == "admitted"
    assert srv.is_admitted("a")


def test_the_contracted_cartridge_is_admitted():
    rom = _rom("red")
    srv = _Server(_contract({"a": fingerprint_rom(rom)}))
    v = srv._decide_admission("a", {"rom_content": _payload_from_rom(rom, "red")})
    assert v["state"] == "admitted", v


def test_a_different_cartridge_is_rejected():
    """The case this exists for: a player launches the wrong ROM, or the other player's."""
    srv = _Server(_contract({"a": fingerprint_rom(_rom("red"))}))
    v = srv._decide_admission("a", {"rom_content": _payload_from_rom(_rom("blue"), "blue")})
    assert v["state"] == "rejected"
    assert "not the cartridge built for player a" in v["reason"]


def test_a_client_that_cannot_report_its_rom_is_rejected_on_a_contracted_run():
    srv = _Server(_contract({"a": fingerprint_rom(_rom("red"))}))
    v = srv._decide_admission("a", {"event": "hello"})
    assert v["state"] == "rejected"
    assert "did not report its cartridge" in v["reason"]


def test_an_unreadable_report_is_reported_as_such_not_as_a_mismatch():
    srv = _Server(_contract({"a": fingerprint_rom(_rom("red"))}))
    v = srv._decide_admission("a", {"rom_content": {"variant": "red", "wild": {"12": "ZZ"}}})
    assert v["state"] == "rejected"
    assert "could not be read" in v["reason"], v["reason"]


def test_a_player_the_contract_does_not_name_is_rejected():
    srv = _Server(_contract({"a": fingerprint_rom(_rom("red"))}))
    v = srv._decide_admission("b", {"rom_content": _payload_from_rom(_rom("red"), "red")})
    assert v["state"] == "rejected"
    assert "names no cartridge" in v["reason"]


def test_an_unreadable_contract_fails_closed():
    """A contract we cannot parse is NOT the same as no contract: the run was built from
    randomized ROMs and we have lost the record of which, so admitting anyone would defeat
    the check entirely."""
    srv = _Server({"unreadable": True, "players": {}})
    assert srv._decide_admission("a", {})["state"] == "rejected"


def test_a_generation_that_cannot_fingerprint_is_admitted():
    """ingest/fingerprint return None off the base adapter, and a run whose adapter cannot
    answer has nothing to check -- it must not be locked out."""
    class _Mute:
        game_id = "mute"
        rom_contract_by_sha1 = False

        def rom_content_fingerprint(self, payload):
            return None

    srv = _Server(_contract({"a": "whatever"}), adapter=_Mute())
    assert srv._decide_admission("a", {"rom_content": {"x": 1}})["state"] == "admitted"


def test_admission_is_re_decided_so_a_swapped_rom_is_caught_on_reconnect():
    """Every hello re-runs the decision, which is what makes reconnect a new epoch."""
    srv = _Server(_contract({"a": fingerprint_rom(_rom("red"))}))
    srv.admission["a"] = srv._decide_admission(
        "a", {"rom_content": _payload_from_rom(_rom("red"), "red")})
    assert srv.is_admitted("a")
    srv.admission["a"] = srv._decide_admission(
        "a", {"rom_content": _payload_from_rom(_rom("blue"), "blue")})
    assert not srv.is_admitted("a"), "a swapped ROM stayed admitted from the previous hello"


# ── the gate actually blocks ─────────────────────────────────────────────────────────────
def test_the_dispatch_gate_blocks_everything_but_hello():
    """Reading the source rather than driving a whole server: what matters is that the
    guard sits in _dispatch, exempts only hello, and returns before any handler runs.

    `tick` must NOT be exempt -- it carries the party snapshot diff_party turns into
    captures, which is precisely a semantic event.
    """
    with open(os.path.join(_REPO, "server", "server.py"), encoding="utf-8") as f:
        src = f.read()
    guard = 'if event != "hello" and not self.is_admitted(player_id):'
    assert guard in src, "the admission guard is gone from _dispatch"
    body = src[src.index(guard):src.index(guard) + 200]
    assert 'return [{"cmd": "noop", "refused": "admission"}]' in body
    # It has to run before the event handlers, not after them.
    assert src.index(guard) < src.index('elif event == "capture":')
    assert src.index(guard) < src.index('elif event == "tick":')


# ── the gate has to hold before the hello, and stop the hello it refuses ──────────

class TestTheGateItself:
    """Three holes that made the gate inert or destructive, none of them in the
    fingerprint comparison the rest of this file tests.

    The comparison was right the whole time. What was wrong was everything around it:
    when the verdict is consulted, what happens when there is no verdict yet, and what
    happens after a verdict of `rejected`.
    """

    CONTRACT = {
        "upr_version": "4.6.1",
        "settings_sha256": "0" * 64,
        "categories": ["wild"],
        "players": {"a": {"fingerprint": "f" * 64, "seed": "1", "rom_sha1": "a" * 40},
                    "b": {"fingerprint": "e" * 64, "seed": "2", "rom_sha1": "b" * 40}},
    }

    def _srv(self, tmp_path, contract=True):
        from server.adapters.gen1_rby import Gen1Adapter
        from server.server import SLinkServer
        if contract:
            with open(os.path.join(str(tmp_path), "rom_contract.json"), "w") as f:
                json.dump(self.CONTRACT, f)
        s = SLinkServer(data_dir=str(tmp_path))
        s.state.adapter = s.adapter = Gen1Adapter(variant="red")
        return s

    def test_a_player_with_no_verdict_is_not_admitted_under_a_contract(self, tmp_path):
        """Nothing requires a hello before _dispatch -- handle_client takes the player id
        from the message itself. A client whose first line is a `capture` (a reconnect
        after a crash that lost the hello) used to find no record, take the "admitted"
        default and mutate rule state on an unchecked cartridge."""
        s = self._srv(tmp_path)
        assert s.is_admitted("a") is False

    def test_an_uncontracted_run_is_completely_unchanged(self, tmp_path):
        """The load-bearing control. Most runs have no contract and must behave exactly
        as they always did -- a gate that fails closed on them would break every
        vanilla run instead."""
        s = self._srv(tmp_path, contract=False)
        assert s.is_admitted("a") is True

    def test_a_non_hello_event_from_an_unadmitted_player_is_dropped(self, tmp_path):
        """Asserted on the rule state, not the return value: a capture that IS processed
        also returns a noop when the partner has not caught yet, so the reply alone
        cannot tell the two apart."""
        s = self._srv(tmp_path)
        s._dispatch("a", {"event": "capture", "key": "DEAD:BEEF:01",
                          "area_id": "route_1", "species_id": 1, "level": 5})
        assert not s.state.pending_captures.get("route_1"),             "the capture was recorded despite the player never being admitted"
        assert not s.state.links

    def test_a_rejected_hello_mutates_nothing_and_sends_no_write(self, tmp_path):
        """THE severe one. Recording the verdict was not enough: control fell straight
        into state.handle_event, which permanently locks player_identity, saves it,
        rebuilds party_keys from the rejected cartridge and queues box_mon WRITE commands
        back to the client we just refused -- and then _ingest_rom_content adopted its
        encounter tables. Booting the wrong ROM once corrupted the run and wrote into
        the wrong save file."""
        s = self._srv(tmp_path)
        cmds = s._dispatch("a", {
            "event": "hello", "rom_type": "gen1_rby", "trainer_name": "RED",
            "party": [{"key": "DEAD:BEEF:01", "species_id": 1, "level": 5}],
            "rom_content": {"variant": "red", "wild": {}},
        })
        assert cmds == [{"cmd": "noop", "refused": "admission"}], "a rejected hello must return nothing to run"
        assert not any(c.get("cmd") == "box_mon" for c in cmds)
        assert s.state.player_identity.get("a") in (None, ""), \
            "identity was locked to a cartridge we refused"
        assert not s.state.trainer_names.get("a"), "trainer name was committed anyway"
        assert not s.state.rom_type, "rom_type was committed from a refused cartridge"
        assert s.admission["a"]["state"] == "rejected"

    def test_an_admitted_hello_still_works(self, tmp_path):
        """The other control: the early return must not swallow good helloes."""
        s = self._srv(tmp_path, contract=False)
        s._dispatch("a", {"event": "hello", "rom_type": "gen1_rby",
                          "trainer_name": "RED", "party": []})
        assert s.state.trainer_names.get("a") == "RED"

    def test_a_contract_written_after_the_server_started_is_picked_up(self, tmp_path):
        """_rom_contract was read once in __init__ and never again, while the Manager
        writes it from handle_randomize with the server already up. Whether the gate
        existed at all came down to which process started first."""
        s = self._srv(tmp_path, contract=False)
        assert s.is_admitted("a") is True          # no contract yet
        with open(os.path.join(str(tmp_path), "rom_contract.json"), "w") as f:
            json.dump(self.CONTRACT, f)
        s._dispatch("a", {"event": "hello", "rom_type": "gen1_rby", "party": []})
        assert s.admission["a"]["state"] == "rejected", \
            "the contract written after startup was never noticed"
