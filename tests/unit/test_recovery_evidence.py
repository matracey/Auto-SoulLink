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


def test_retired_or_other_claimed_keys_cannot_be_rebound():
    recovery = Recovery()
    recovery.observe("a", 1, [{"key": "A:2"}, {"key": "A:3"}], EXPECTED, {"A:2"})
    assert [row["key"] for row in recovery.candidates["a"]] == ["A:3"]


def test_candidate_expires_when_no_new_census_arrives(monkeypatch):
    monkeypatch.setattr("server.recovery.monotonic", lambda: 100)
    recovery = Recovery()
    recovery.observe("a", 1, [{"key": "A:2"}], EXPECTED)
    monkeypatch.setattr("server.recovery.monotonic", lambda: 131)
    with pytest.raises(RecoveryConflict, match="fresh"):
        recovery.candidate("a", "A:2")


def test_pause_is_owner_scoped_and_includes_only_the_affected_pair():
    from server.state import LinkEntry, LinkStatus, MonInfo, SoulLinkState

    state = SoulLinkState()
    state.links = [
        LinkEntry("route_1", MonInfo("A:1"), MonInfo("B:1"), LinkStatus.ALIVE),
        LinkEntry("route_2", MonInfo("A:2"), MonInfo("A:1"), LinkStatus.ALIVE),
    ]
    recovery = Recovery()
    recovery.observe("a", 1, [{"key": "A:2"}], recovery.expected(state, "a"))
    assert recovery.paused(state) == {"a": {"A:1"}, "b": {"B:1"}}


def test_stale_resolution_cannot_resolve_a_new_incident():
    recovery = Recovery()
    recovery.observe("a", 1, [], EXPECTED)
    recovery.observe("a", 2, [], EXPECTED)
    old = recovery.limbo["a"]["A:1"]["revision"]
    recovery.finish("a", "A:1", "detach")
    recovery.observe("a", 3, [{"key": "A:1"}], EXPECTED)
    recovery.observe("a", 4, [], EXPECTED)
    recovery.observe("a", 5, [], EXPECTED)
    with pytest.raises(RecoveryConflict, match="changed"):
        recovery.incident("a", "A:1", old)


@pytest.mark.parametrize("saved", [[], {}, {"version": 1, "limbo": {}, "history": []}])
def test_corrupt_recovery_state_is_not_silently_reset(saved):
    with pytest.raises(ValueError, match="invalid"):
        Recovery.load(saved)
