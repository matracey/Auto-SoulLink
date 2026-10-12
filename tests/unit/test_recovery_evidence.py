import pytest

from server.recovery import Recovery, RecoveryConflict

EXPECTED = {"A:1": {"area_id": "route_1", "nickname": "Before"}}


def test_absence_requires_two_new_complete_generations():
    recovery = Recovery()
    assert recovery.observe("a", 1, [], EXPECTED) == []
    assert recovery.observe("a", 1, [], EXPECTED) == []
    changes = recovery.observe("a", 2, [], EXPECTED)
    assert changes[0]["action"] == "missing"
    assert recovery.limbo["b"] == {}


def test_reappearance_records_history_without_losing_the_encounter():
    recovery = Recovery()
    recovery.observe("a", 1, [], EXPECTED)
    recovery.observe("a", 2, [], EXPECTED)
    incident = recovery.limbo["a"]["A:1"]
    assert recovery.observe("a", 3, [{"key": "A:1"}], EXPECTED)[0]["action"] == "reappeared"
    assert recovery.limbo["a"] == {}
    assert recovery.history[0]["revision"] == incident["revision"]
    assert recovery.history[0]["area_id"] == "route_1"


def test_restart_keeps_limbo_but_never_reuses_old_presence_or_suspicion():
    recovery = Recovery()
    recovery.observe("a", 1, [{"key": "A:2"}], EXPECTED)
    recovery.observe("a", 2, [{"key": "A:2"}], EXPECTED)
    loaded = Recovery.load(recovery.dump())
    assert loaded.limbo == recovery.limbo
    with pytest.raises(RecoveryConflict, match="fresh"):
        loaded.candidate("a", "A:2")
    recovery.disconnect("a")
    assert recovery.candidates["a"] == []
    assert recovery.limbo["a"] == loaded.limbo["a"]


def test_duplicate_keys_are_neither_reappearance_nor_rebind_candidates():
    recovery = Recovery()
    recovery.observe("a", 1, [], EXPECTED)
    recovery.observe("a", 2, [], EXPECTED)
    assert recovery.observe("a", 3, [{"key": "A:1"}, {"key": "A:1"}], EXPECTED) == []
    assert "A:1" in recovery.limbo["a"]
    assert recovery.candidates["a"] == []


def test_incomplete_interval_breaks_absence_streak_and_invalidates_candidates():
    recovery = Recovery()
    recovery.observe("a", 1, [{"key": "A:2"}], EXPECTED)
    recovery.invalidate("a")
    assert recovery.candidates["a"] == []
    assert recovery.observe("a", 2, [], EXPECTED) == []
    assert recovery.observe("a", 3, [], EXPECTED)[0]["action"] == "missing"
