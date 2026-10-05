"""P4.5c: the server's optional "phone" tag (O-29 Soul Link phone calls), and its inertness.

docs/gen2/reviews/P4_5_PHONE_CALLS_PLAN_2026-09-23.md §3.1: no new command. The partner's battle
force_faint from _propagate_faint carries "phone": "fallen" (never the identity_lost retirement),
both dead-zone msgboxes carry "dead_zone", and both "linked!" msgboxes of the run's FIRST two-sided
link carry "first_link". The key is generation-neutral: every client receives it and only the Gen 2
binder (lua/gen2/phone.lua) acts on it, so Gen 1 and Gen 3 must behave exactly as without it.
"""
from __future__ import annotations

import pathlib
import random
import re

import pytest

from server.state import SoulLinkState
from tests.unit import protocol_schema as ps
from tests.unit.test_state import make_state_with_link

REPO = pathlib.Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _links(tmp_path, monkeypatch):
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))


def tagged(state, player, replied=()):
    """The tags queued for `player`, plus those handed back in `replied` (handle_event drains
    the calling player's queue into its return value)."""
    return [(c["cmd"], c["phone"]) for c in [*replied, *state.queued_commands[player]] if "phone" in c]


# -- fallen -------------------------------------------------------------------------------------

def test_a_battle_death_tags_only_the_partners_force_faint():
    state = make_state_with_link()
    state.handle_event("a", {"event": "faint", "key": "A:1"})
    assert tagged(state, "b") == [("force_faint", "fallen")]
    assert tagged(state, "a") == []


def test_the_identity_loss_retirement_carries_no_tag():
    state = make_state_with_link()
    state._propagate_faint("a", state.links[0], cause="identity_lost")
    assert any(c["cmd"] == "force_faint" for c in state.queued_commands["b"])
    assert tagged(state, "a") == tagged(state, "b") == []


# -- dead_zone ----------------------------------------------------------------------------------

def test_both_dead_zone_msgboxes_are_tagged():
    state = SoulLinkState(retry_same_species=False)
    replied = state.handle_event("b", {"event": "no_catch", "area_id": "route_1"})
    assert tagged(state, "a") == tagged(state, "b", replied) == [("msgbox", "dead_zone")]


# -- first_link ---------------------------------------------------------------------------------

def link(state, area, a_key, b_key):
    state.handle_event("a", {"event": "capture", "key": a_key, "area_id": area, "level": 5})
    return state.handle_event("b", {"event": "capture", "key": b_key, "area_id": area, "level": 5})


def test_only_the_runs_first_two_sided_link_is_tagged():
    state = SoulLinkState(retry_same_species=False)
    state.pokeballs_obtained = {"a": True, "b": True}
    state.handle_event("b", {"event": "no_catch", "area_id": "route_9"})   # a one-sided entry first
    for pid in "ab":
        state.queued_commands[pid].clear()
    replied = link(state, "route_1", "A:1", "B:1")
    assert tagged(state, "a") == tagged(state, "b", replied) == [("msgbox", "first_link")]
    for pid in "ab":
        state.queued_commands[pid].clear()
    replied = link(state, "route_2", "A:2", "B:2")
    assert tagged(state, "a") == tagged(state, "b", replied) == []


def test_a_run_that_already_has_a_link_never_tags_first_link():
    state = make_state_with_link()          # a restored run: links.json already holds one pair
    replied = link(state, "route_2", "A:2", "B:2")
    assert tagged(state, "a") == tagged(state, "b", replied) == []


# -- the tag is inert outside Gen 2 -------------------------------------------------------------

@pytest.mark.parametrize("cmd", [
    {"cmd": "force_faint", "key": "ABCD:1234:99", "phone": "fallen"},
    {"cmd": "force_explode", "key": "ABCD:1234:99", "phone": "fallen"},
    {"cmd": "msgbox", "text": "Route 1 is a dead zone!", "phone": "dead_zone"},
])
def test_the_schema_knows_the_tag(cmd):
    assert ps.validate_command(cmd, strict=True) == []


CLIENTS = sorted({*(REPO / "lua" / "clients").glob("*.lua"), REPO / "lua" / "gen1" / "client.lua"})


@pytest.mark.parametrize("path", CLIENTS, ids=lambda p: p.name)
def test_other_clients_dispatch_by_name_and_never_read_the_tag(path):
    """Gen 1/3/4/5 read named fields off a command; none walks its keys or reads `phone`."""
    src = path.read_text(encoding="utf-8")
    assert not re.search(r"\.phone\b|\[\s*[\"']phone[\"']\s*\]", src)
    assert not re.search(r"pairs\(\s*(c|cmd)\s*\)", src)


def test_a_gen1_client_behaves_identically_with_the_tag():
    from server.adapters import gen1_codec as codec
    from tests.unit.test_gen1_client import World, _mon

    def run(tag):
        w = World("red")
        rng = random.Random(1)
        w.seed_party([_mon(rng, 0x99, nick="BULBA"), _mon(rng, 0xB1, nick="PIDGEY")])
        w.set_map(0x0C)
        w.connect()
        w.step(60)
        extra = {"phone": "fallen"} if tag else {}
        w.reply({"cmd": "force_faint", "key": codec.key(w.party()[1]), **extra},
                {"cmd": "msgbox", "text": "Route 1 is a dead zone!", **({"phone": "dead_zone"} if tag else {})})
        w.step(3)
        w.overworld_safe()
        w.step(3)
        return w.writes, w.sent, w.hud, w.logs, w.party()[1]["hp"]

    plain, with_tag = run(False), run(True)
    assert plain[-1] == 0 and with_tag == plain


# -- PHONE-NAMES: phone_data, relative to each receiver ---------------------------------------

def phone_data(state, player, replied=()):
    return [c.get("phone_data") for c in [*replied, *state.queued_commands[player]] if "phone" in c]


def named(state):
    state.trainer_names = {"a": "ALICE", "b": "BOB"}
    return state


def test_fallen_names_the_caller_and_both_mons_for_the_receiver():
    state = named(make_state_with_link())
    state.links[0].a.species, state.links[0].a.nickname = 16, "PIDGE"
    state.links[0].b.species, state.links[0].b.nickname = 19, "RATTY"
    state.handle_event("a", {"event": "faint", "key": "A:1"})
    [cmd] = [c for c in state.queued_commands["b"] if "phone" in c]
    assert cmd["phone_data"] == {"trainer_name": "ALICE",
                                 "caller_mon": {"species_id": 16, "nickname": "PIDGE"},
                                 "receiver_mon": {"species_id": 19, "nickname": "RATTY"}}
    assert ps.validate_command({**cmd, "key": "ABCD:1234:99"}, strict=True) == []


def test_first_link_gives_each_receiver_its_own_object():
    state = named(SoulLinkState())
    state.pokeballs_obtained = {"a": True, "b": True}
    state.handle_event("a", {"event": "capture", "key": "A:1", "area_id": "route_1", "level": 5,
                             "species_id": 16, "nickname": "PIDGE"})
    replied = state.handle_event("b", {"event": "capture", "key": "B:1", "area_id": "route_1", "level": 5,
                                       "species_id": 19, "nickname": "RATTY"})
    [to_a], [to_b] = phone_data(state, "a"), phone_data(state, "b", replied)
    assert to_a == {"trainer_name": "BOB", "caller_mon": {"species_id": 19, "nickname": "RATTY"},
                    "receiver_mon": {"species_id": 16, "nickname": "PIDGE"}}
    assert to_b["trainer_name"] == "ALICE" and to_b["caller_mon"]["species_id"] == 16
    assert to_a is not to_b


def test_dead_zone_names_only_the_other_trainer():
    state = named(SoulLinkState(retry_same_species=False))
    replied = state.handle_event("b", {"event": "no_catch", "area_id": "route_1"})
    assert phone_data(state, "a") == [{"trainer_name": "BOB"}]
    assert phone_data(state, "b", replied) == [{"trainer_name": "ALICE"}]


def test_no_trainer_name_keeps_the_tag_and_omits_phone_data():
    state = make_state_with_link()
    state.player_identity = {"a": {"ot_id": "1", "trainer_name": "A", "trainer_name_placeholder": True}}
    state.handle_event("a", {"event": "faint", "key": "A:1"})
    assert phone_data(state, "b") == [None] and tagged(state, "b") == [("force_faint", "fallen")]


def test_a_real_trainer_name_equal_to_the_slot_letter_is_still_a_name():
    """F8: the placeholder is an explicit flag, never inferred from name == slot letter."""
    state = make_state_with_link()
    state.player_identity = {"a": {"ot_id": "1", "trainer_name": "A"}}
    state.handle_event("a", {"event": "faint", "key": "A:1"})
    assert phone_data(state, "b") == [{"trainer_name": "A"}]


def test_the_identity_lock_flags_its_synthesized_name_and_a_real_name_clears_it():
    state = SoulLinkState()
    party = [{"key": "ABCD:1234:99", "species_id": 1, "level": 5}]
    state.handle_event("a", {"event": "hello", "rom_type": "gen3_frlge", "party": party, "ot_id": 4660})
    assert state.player_identity["a"]["trainer_name_placeholder"] is True
    assert state._phone_data("a", None, None) is None
    state.handle_event("a", {"event": "hello", "rom_type": "gen3_frlge", "party": party, "ot_id": 4660,
                             "trainer_name": "ALICE"})
    assert "trainer_name_placeholder" not in state.player_identity["a"]
    assert state._phone_data("a", None, None) == {"trainer_name": "ALICE"}


@pytest.mark.parametrize("data", [
    {"caller_mon": {"species_id": 1}},                               # no trainer_name
    {"trainer_name": "BOB", "caller_mon": {"nickname": "X"}},       # a mon without species
    {"trainer_name": "BOB", "extra": 1},
    {"trainer_name": "BOB", "receiver_mon": {"species_id": "1"}},
])
def test_the_schema_validates_phone_data_strictly(data):
    assert ps.validate_command({"cmd": "msgbox", "text": "x", "phone": "dead_zone", "phone_data": data},
                               strict=True)


def test_phone_data_without_a_tag_is_rejected():
    assert ps.validate_command({"cmd": "msgbox", "text": "x", "phone_data": {"trainer_name": "BOB"}})
