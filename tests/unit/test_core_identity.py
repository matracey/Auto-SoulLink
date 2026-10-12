"""lua/core/identity.lua (P4 C4-1): the key_change alias machinery lifted from
lua/gen1/client.lua:367-440, :526-548 and :1852-1858 (D-13), driven under lupa.

First falsifier: an aliased key whose new key has TWO records matching the evidence frozen at
the change is refused as ambiguous, and stays refused after one of them leaves (the latch).
"""
from __future__ import annotations

import pathlib

import pytest

lupa = pytest.importorskip("lupa")

REPO = pathlib.Path(__file__).resolve().parents[2]
IDENTITY = (REPO / "lua" / "core" / "identity.lua").as_posix()

OLD, NEW, OTHER = "00000001:0000ABCD", "00000002:0000ABCD", "00000003:0000ABCD"


@pytest.fixture
def lua():
    return lupa.LuaRuntime(unpack_returned_tuples=True)


@pytest.fixture
def ident(lua):
    Identity = lua.eval(f'dofile("{IDENTITY}")')
    return Identity.new(lua.table(key=lua.eval("function(m) return m.key end")))


def mon(lua, key, slot, nick=(0x80, 0x81), moves=(1, 2)):
    return lua.table_from({"key": key, "slot": slot, "nickname_bytes": lua.table_from(list(nick)),
                           "moves": lua.table_from(list(moves))})


def party(lua, *mons):
    return lua.table_from(list(mons))


def find(ident, key, p):
    r = ident.find_party_slot(ident, key, p)
    r = (r if isinstance(r, tuple) else (r,)) + (None,) * 4
    return r[0], r[3]


def test_a_plain_key_resolves_its_slot(lua, ident):
    p = party(lua, mon(lua, OTHER, 0), mon(lua, NEW, 1))
    assert find(ident, NEW, p) == (1, None)


def test_an_unreadable_party_resolves_nothing_and_gives_no_reason(lua, ident):
    assert find(ident, NEW, None) == (None, None)


def test_a_plain_duplicate_key_is_refused_as_ambiguous(lua, ident):
    p = party(lua, mon(lua, NEW, 0), mon(lua, NEW, 1))
    assert find(ident, NEW, p) == (None, "ambiguous key")


def _retire(lua, ident, changed_party):
    changed = changed_party[1]
    ident.begin_alias(ident, OLD, NEW, changed, changed_party)
    assert ident.reject(ident, OLD, changed_party) is not None
    assert ident.retired(ident, OLD) is not None


def test_falsifier_an_aliased_key_with_two_evidence_matches_is_refused_as_ambiguous(lua, ident):
    _retire(lua, ident, party(lua, mon(lua, NEW, 0)))
    twins = party(lua, mon(lua, NEW, 0), mon(lua, NEW, 3))  # same nick bytes and moves
    slot, why = find(ident, OLD, twins)
    assert slot is None and why is not None and "ambiguous" in why


def test_ambiguity_is_latched_when_one_twin_leaves(lua, ident):
    _retire(lua, ident, party(lua, mon(lua, NEW, 0)))
    find(ident, OLD, party(lua, mon(lua, NEW, 0), mon(lua, NEW, 3)))
    slot, why = find(ident, OLD, party(lua, mon(lua, NEW, 0)))
    assert slot is None and "ambiguous" in why


def test_a_twin_seen_at_the_change_latches_ambiguity_before_the_rejection(lua, ident):
    twins = party(lua, mon(lua, NEW, 0), mon(lua, NEW, 1))
    ident.begin_alias(ident, OLD, NEW, twins[1], twins)
    ident.reject(ident, OLD, party(lua, mon(lua, NEW, 0)))
    assert "ambiguous" in find(ident, OLD, party(lua, mon(lua, NEW, 0)))[1]


def test_a_rejected_alias_resolves_the_sole_evidence_match(lua, ident):
    _retire(lua, ident, party(lua, mon(lua, NEW, 2)))
    p = party(lua, mon(lua, NEW, 0, moves=(9, 9)), mon(lua, NEW, 4))  # slot 0 is not the record
    assert find(ident, OLD, p) == (4, None)


def test_an_edited_record_latches_lost_and_never_recovers(lua, ident):
    _retire(lua, ident, party(lua, mon(lua, NEW, 0)))
    ident.observe(ident, party(lua, mon(lua, NEW, 0, moves=(5, 2))))  # a TM taught
    slot, why = find(ident, OLD, party(lua, mon(lua, NEW, 0)))       # taught back
    assert slot is None and "left the party" in why


def test_a_departure_of_the_new_key_latches_lost(lua, ident):
    _retire(lua, ident, party(lua, mon(lua, NEW, 0)))
    ident.departure(ident, NEW)
    assert find(ident, OLD, party(lua, mon(lua, NEW, 0)))[0] is None


def test_an_unrelated_departure_leaves_the_alias_alone(lua, ident):
    _retire(lua, ident, party(lua, mon(lua, NEW, 0)))
    ident.departure(ident, OTHER)
    assert find(ident, OLD, party(lua, mon(lua, NEW, 0))) == (0, None)


def test_an_unreadable_departure_is_treated_as_the_aliased_record(lua, ident):
    _retire(lua, ident, party(lua, mon(lua, NEW, 0)))
    ident.departure(ident, None)
    assert find(ident, OLD, party(lua, mon(lua, NEW, 0)))[0] is None


def test_ack_drops_the_pending_alias_only_for_its_own_old_key(lua, ident):
    p = party(lua, mon(lua, NEW, 0))
    ident.begin_alias(ident, OLD, NEW, p[1], p)
    assert ident.ack(ident, OTHER) is None and ident.active(ident)
    assert ident.ack(ident, OLD) is not None and not ident.active(ident)


def test_multiple_changes_keep_each_alias_until_its_ack(lua, ident):
    first = party(lua, mon(lua, NEW, 0))
    ident.begin_alias(ident, OLD, NEW, first[1], first)
    second = party(lua, mon(lua, OTHER, 0))
    ident.begin_alias(ident, NEW, OTHER, second[1], second)
    ident.observe(ident, second)
    assert find(ident, OLD, second) == (0, None)
    assert find(ident, NEW, second) == (0, None)
    assert ident.ack(ident, OLD).new_key == NEW
    assert find(ident, NEW, second) == (0, None)
    assert ident.ack(ident, NEW).new_key == OTHER
    assert not ident.active(ident)


def test_rejected_earlier_alias_still_resolves_the_latest_record(lua, ident):
    first = party(lua, mon(lua, NEW, 0))
    ident.begin_alias(ident, OLD, NEW, first[1], first)
    second = party(lua, mon(lua, OTHER, 0))
    ident.begin_alias(ident, NEW, OTHER, second[1], second)
    ident.reject(ident, OLD, second)
    assert find(ident, OLD, second) == (0, None)
    assert ident.pending_for(ident, NEW) is not None


def test_reject_for_another_old_key_retires_nothing(lua, ident):
    p = party(lua, mon(lua, NEW, 0))
    ident.begin_alias(ident, OLD, NEW, p[1], p)
    assert ident.reject(ident, OTHER, p) is None and ident.retired(ident, OTHER) is None


def test_forget_and_clear_keep_the_retired_table_identity(lua, ident):
    _retire(lua, ident, party(lua, mon(lua, NEW, 0)))
    table = ident.retired_alias
    ident.forget(ident, OLD)
    assert ident.retired(ident, OLD) is None and not ident.active(ident)
    _retire(lua, ident, party(lua, mon(lua, NEW, 0)))
    ident.clear(ident)
    assert lua.eval("rawequal")(ident.retired_alias, table)
    assert ident.retired(ident, OLD) is None and not ident.active(ident)
