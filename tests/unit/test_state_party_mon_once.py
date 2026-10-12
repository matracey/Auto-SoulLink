"""
Card SV-1 — a server-driven sync command that is already ON THE WIRE must not be
re-issued by the drift reconciler.

Live evidence (slink_duo_linked_faint_active_new_n5va0ett/server.log):

    14:41:46,202  Post-link: a:02A3:419 -> party_mon (un-quarantine)
    14:41:46,202  Post-link: b:2913:AFB -> party_mon (un-quarantine)
    14:41:46,236  [a] tick reconcile: re-added 02A3:419 to party_keys (ghost-boxed)
    14:41:46,236  [b] tick reconcile: queued party_mon 2913:AFB to restore party-sync with a

B's client executed two `party_mon` for one key and acked twice
(`party_mon=2 sync_retrieve_done=2` in the receipt); the second withdraw was a
no-op only because the cartridge side happens to be idempotent.

These are shared-runtime lifecycle tests: they assert in reconciler passes, never
in frames, seconds or client events, and reference no generation's client.

No emulator, no HTTP server.
"""

from server.state import SYNC_INFLIGHT_RECONCILES, SoulLinkState


def _party_mons(cmds: list, key: str) -> list[dict]:
    return [c for c in cmds if c.get("cmd") == "party_mon" and c.get("key") == key]


def _linked_pair_state(tmp_path, monkeypatch) -> SoulLinkState:
    """Drive a real link formation from two quarantined captures.

    Mirrors the live run: B captures first and polls (its quarantine box_mon goes
    out on the wire), then A captures, which forms the link and queues the
    un-quarantine party_mon for both halves.
    """
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState()
    state.pokeballs_obtained = {"a": True, "b": True}
    state.party_size = {"a": 1, "b": 1}
    state.party_keys = {"a": {"A:0"}, "b": {"B:0"}}

    state.handle_event("b", {"event": "capture", "key": "B:2", "species_id": 19,
                             "level": 3, "maxHP": 15, "area_id": "route_1"})
    state.handle_event("b", {"event": "tick", "party": [{"key": "B:0"}]})   # flush box_mon
    state.handle_event("a", {"event": "capture", "key": "A:1", "species_id": 19,
                             "level": 4, "maxHP": 18, "area_id": "route_1"})
    return state


def _form_link_and_drain(tmp_path, monkeypatch) -> tuple[SoulLinkState, list]:
    """As above, then let B poll once — the live ordering.

    B's un-quarantine party_mon is now ON THE WIRE (delivered, not yet acked) and
    no longer visible in queued_commands, which is precisely the window the drift
    reconciler misreads.
    """
    state = _linked_pair_state(tmp_path, monkeypatch)
    delivered_b = state.handle_event("b", {"event": "tick", "party": [{"key": "B:0"}]})
    return state, delivered_b


def _a_tick(state: SoulLinkState) -> list:
    """A's tick reporting its half in party.

    A's quarantine box_mon was cancelled when the link formed, so A's cartridge
    really does still hold A:1 — the server is the side that is behind.
    """
    return state.handle_event("a", {"event": "tick",
                                    "party": [{"key": "A:0"}, {"key": "A:1"}]})


def _b_tick(state: SoulLinkState) -> list:
    return state.handle_event("b", {"event": "tick", "party": [{"key": "B:0"}]})


def test_link_unquarantine_queues_one_party_mon_per_player(tmp_path, monkeypatch):
    """The link-formation un-quarantine is exactly one party_mon per player."""
    state, cmds_b = _form_link_and_drain(tmp_path, monkeypatch)
    cmds_a = _a_tick(state)
    assert len(_party_mons(cmds_a, "A:1")) <= 1, "A must not receive a duplicate party_mon"
    assert len(_party_mons(cmds_b, "B:2")) == 1, "B's half is un-quarantined exactly once"


def test_inflight_party_mon_is_not_reissued_by_drift_reconciler(tmp_path, monkeypatch):
    """
    A's half is physically in party while the server still waits for the party_mon
    answer.  That transient is NOT drift: re-issuing B's party_mon is the SV-1 bug.
    """
    state, delivered_b = _form_link_and_drain(tmp_path, monkeypatch)

    for _ in range(3):
        _a_tick(state)
        delivered_b += _b_tick(state)

    assert len(_party_mons(delivered_b, "B:2")) == 1, (
        "B received party_mon for B:2 "
        f"{len(_party_mons(delivered_b, 'B:2'))}x — the un-quarantine command was "
        "still in flight when the drift reconciler re-issued it"
    )


def test_reconciler_passes_cannot_expire_an_unanswered_directed_sync(tmp_path, monkeypatch):
    """An unresolved PC intent keeps its direction until execution is confirmed."""
    state, _ = _form_link_and_drain(tmp_path, monkeypatch)
    # Settle A's side so only B's in-flight party_mon is left in play, then re-open the
    # drift A's cancelled quarantine created (server behind, cartridge holding A:1).
    state.handle_event("a", {"event": "sync_retrieve_done", "key": "A:1"})
    state.party_keys["a"].discard("A:1")
    assert ("B:2", "party_mon") in state.sync_inflight["b"], "sanity: B's command is on the wire"
    intent = state.sync_versions["b"]["B:2"].copy()

    # Chatter: events with no party snapshot, so the reconciler never runs for them.
    for _ in range(SYNC_INFLIGHT_RECONCILES * 4):
        state.handle_event("b", {"event": "status", "badges": 1})
    assert ("B:2", "party_mon") in state.sync_inflight["b"], (
        "unrelated client events must not spend the in-flight window — it is measured "
        "in reconciler passes, so a chatty client cannot shorten it"
    )
    _a_tick(state)
    assert not _party_mons(_b_tick(state), "B:2"), "suppression must still hold after chatter"

    for _ in range(SYNC_INFLIGHT_RECONCILES * 4):
        assert not _party_mons(_b_tick(state), "B:2")
    assert ("B:2", "party_mon") in state.sync_inflight["b"]
    state.party_keys["a"].discard("A:1")
    _a_tick(state)
    assert not _party_mons(_b_tick(state), "B:2")
    assert state.sync_versions["b"]["B:2"] == intent
    assert len(_party_mons(state.queued_commands["b"], "B:2")) == 1


def test_reconciler_resumes_after_an_answer(tmp_path, monkeypatch):
    """An answered command frees the key immediately — genuine drift still heals."""
    state, _ = _form_link_and_drain(tmp_path, monkeypatch)
    state.handle_event("a", {"event": "sync_retrieve_done", "key": "A:1"})
    state.handle_event("b", {"event": "sync_retrieve_done", "key": "B:2"})
    assert not state.sync_inflight["a"] and not state.sync_inflight["b"]

    # A phantom deposit drops both halves from party_keys with no command going out.
    state.party_keys["a"].discard("A:1")
    state.party_keys["b"].discard("B:2")
    _a_tick(state)
    assert _party_mons(_b_tick(state), "B:2"), "real drift must still be repaired"
