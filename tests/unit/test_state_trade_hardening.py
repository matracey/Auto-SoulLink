"""TRADE-HARDEN-2: the server trade state machine fixes from review e9d5e136
(docs/gen2/reviews/REVIEW_TRADE_SERVER_2026-09-24.md). One section per review item."""

import pytest

from server.adapters.gen1_rby import Gen1Adapter
from server.state import SoulLinkState
from tests.unit.test_state_trade_uncertain import _gen1_applying, _gen1_confirming, _mon, _tick

A_GETS, B_GETS = "1234:5678:15", "ABCD:1234:26"     # _gen1_applying: A gives ABCD:1234:26, B gives 1234:5678:15


def _reload(tmp_path):
    return SoulLinkState.load(data_dir=str(tmp_path), adapter=Gen1Adapter())


# ── BLOCKER-1: a restart mid-trade restores it as uncertain; party evidence settles it ─────────────

def test_a_restart_while_applying_restores_the_trade_as_uncertain_and_evidence_settles_it(tmp_path):
    state, _entry, token = _gen1_applying(tmp_path)
    state.handle_event("a", {"event": "trade_done", "token": token, "new_key": A_GETS, "new_species": 0x15})
    back = _reload(tmp_path)
    pt = back.pending_trade
    assert pt and pt["token"] == token and pt["phase"] == "uncertain"
    assert back.trade_problem()["verdict"] == {"a": "traded", "b": "await"}, "A's report survived; B awaits"
    seen = []
    back.on_trade_outcome = seen.append              # the server wires the journal after load
    assert [r["outcome"] for r in seen] == ["uncertain"] and seen[0]["token"] == token
    back.handle_event("b", {"event": "trade_done", "token": token, "new_key": B_GETS, "new_species": 0x26})
    _tick(back, "a", _mon(A_GETS, 0x15))
    _tick(back, "b", _mon(B_GETS, 0x26))
    assert back.pending_trade is None
    entry = back.links[0]
    assert (entry.a.key, entry.b.key) == (A_GETS, B_GETS), "the link follows the mons, not the restart"
    assert back._key_index[A_GETS] is entry and back._key_index[B_GETS] is entry
    assert [r["outcome"] for r in seen] == ["uncertain", "committed"]
    assert _reload(tmp_path).pending_trade is None, "a settled trade is not restored again"


def test_a_restart_before_apply_drops_the_offer_but_never_reuses_a_token(tmp_path):
    state, _entry, token, _ = _gen1_confirming(tmp_path)
    state._save()
    back = _reload(tmp_path)
    assert back.pending_trade is None, "nothing was applied: an unanswered offer may be dropped"
    back._handle_trade_request("a", {})
    assert back.pending_trade["token"] != token, "tokens do not restart at t1"


def test_a_rolled_back_or_conflicted_trade_is_persisted_as_such(tmp_path):
    state, _entry, token = _gen1_applying(tmp_path)
    state.handle_event("a", {"event": "trade_done", "token": token, "new_key": A_GETS, "new_species": 0x15})
    state.handle_event("b", {"event": "trade_done", "token": token, "new_key": "1234:5678:15", "new_species": 0})
    assert state.pending_trade["phase"] == "conflict"
    back = _reload(tmp_path)
    assert back.pending_trade["phase"] == "conflict" and back.trade_problem()["problem"]


# ── MAJOR-2: faint/box events naming a trade key wait for the swap, then go to the real holder ─────

def _cmds(state, pid):
    return state.handle_event(pid, {"event": "noop"})


def _keys(cmds, name):
    return [c.get("key") for c in cmds if c.get("cmd") == name]


def test_a_received_mon_fainting_in_the_one_sided_window_kills_the_real_partner_after_the_swap(tmp_path):
    state, entry, token = _gen1_applying(tmp_path)
    state.handle_event("a", {"event": "trade_done", "token": token, "new_key": A_GETS, "new_species": 0x15})
    state.handle_event("b", {"event": "trade_done", "token": token, "uncertain": True})
    a_cmds = state.handle_event("a", {"event": "faint", "key": A_GETS})      # A holds B's old mon now
    b_cmds = _cmds(state, "b")
    assert not _keys(a_cmds + b_cmds, "force_faint") and not _keys(a_cmds + b_cmds, "memorialize"), \
        "held: the link is not swapped yet, so B:2's half would be the wrong target"
    assert state.pending_trade["held_events"]
    b_cmds = _tick(state, "b", _mon(B_GETS, 0x26))                           # B's evidence: traded
    assert state.pending_trade is None and entry.a.key == A_GETS
    b_cmds += _cmds(state, "b")
    a_cmds = _cmds(state, "a")
    assert B_GETS in _keys(b_cmds, "force_faint"), "the partner of A's received mon is B's received mon"
    assert A_GETS not in _keys(b_cmds, "force_faint") and B_GETS not in _keys(a_cmds, "force_faint")
    assert entry.status.value != "alive"


def test_an_evolved_received_mon_fainting_before_its_report_is_not_lost(tmp_path):
    state, entry, token = _gen1_applying(tmp_path)
    evolved = "ABCD:1234:95"            # B's received Kadabra (0x26) trade-evolved into Alakazam (0x95)
    state.handle_event("b", {"event": "faint", "key": evolved})
    assert state.pending_trade["held_events"], "an unindexed key with the partner's OT is the trade's"
    state.handle_event("b", {"event": "trade_done", "token": token, "new_key": evolved, "new_species": 0x95})
    a_cmds = state.handle_event("a", {"event": "trade_done", "token": token, "new_key": A_GETS, "new_species": 0x15})
    assert state.pending_trade is None and entry.b.key == evolved
    assert entry.status.value != "alive", "the faint replayed into the swapped link"
    assert A_GETS in _keys(a_cmds, "force_faint")


def test_a_deposit_of_a_received_mon_boxes_the_real_partner_after_the_swap(tmp_path):
    state, _entry, token = _gen1_applying(tmp_path)
    state.handle_event("a", {"event": "trade_done", "token": token, "new_key": A_GETS, "new_species": 0x15})
    state.handle_event("a", {"event": "party_to_box", "key": A_GETS})
    assert A_GETS not in _keys(_cmds(state, "b"), "box_mon")
    b_cmds = state.handle_event("b", {"event": "trade_done", "token": token, "new_key": B_GETS, "new_species": 0x26})
    boxed = _keys(b_cmds, "box_mon")
    assert boxed == [B_GETS]


def test_a_rolled_back_trade_replays_the_held_faint_against_the_unswapped_link(tmp_path):
    state, entry, token = _gen1_applying(tmp_path)
    state.handle_event("a", {"event": "faint", "key": B_GETS})               # A's own offered mon
    assert state.pending_trade["held_events"]
    state.handle_event("a", {"event": "trade_done", "token": token, "new_key": B_GETS, "new_species": 0})
    b_cmds = state.handle_event("b", {"event": "trade_done", "token": token, "new_key": A_GETS, "new_species": 0})
    assert state.pending_trade is None and entry.a.key == B_GETS
    assert A_GETS in _keys(b_cmds, "force_faint")
    assert entry.status.value != "alive"


def test_an_unrelated_faint_is_not_held(tmp_path):
    state, _entry, _token = _gen1_applying(tmp_path)
    state.handle_event("a", {"event": "faint", "key": "ABCD:1234:99"})       # A's own OT: not the trade's
    assert not state.pending_trade.get("held_events")


# ── MAJOR-3: an admin resolves a conflict or a stuck uncertain trade ─────────────────────────────

def _conflicted(tmp_path):
    state, entry, token = _gen1_applying(tmp_path)
    state.handle_event("a", {"event": "trade_done", "token": token, "new_key": A_GETS, "new_species": 0x15})
    state.handle_event("b", {"event": "trade_done", "token": token, "new_key": "1234:5678:15", "new_species": 0})
    assert state.trade_problem()["phase"] == "conflict"
    return state, entry, token


def test_admin_rollback_clears_the_conflict_and_journals_it(tmp_path):
    state, entry, token = _conflicted(tmp_path)
    seen = []
    state.on_trade_outcome = seen.append
    assert state.resolve_trade("t999", "rollback")[0] is False, "the token guards it"
    assert state.resolve_trade(token, "bogus")[0] is False
    assert state.resolve_trade(token, "rollback") == (True, "")
    assert state.pending_trade is None and state.trade_problem() is None
    # invariant review MAJOR-3: A's known "traded" stands, so A's half names the copy it holds
    assert (entry.a.key, entry.b.key) == (A_GETS, A_GETS)
    assert [(r["outcome"], r["problem"]) for r in seen] == [("split", "resolved by admin: rollback")]
    assert _reload(tmp_path).pending_trade is None
    state._handle_trade_request("a", {})
    assert state.pending_trade is not None, "the trade slot is free again"


def test_admin_commit_swaps_with_the_reported_keys_and_replays_held_events(tmp_path):
    state, entry, token = _gen1_applying(tmp_path)
    state.handle_event("a", {"event": "trade_done", "token": token, "new_key": A_GETS, "new_species": 0x15})
    state.handle_event("a", {"event": "faint", "key": A_GETS})
    for _ in range(state.TRADE_WATCHDOG_EVENTS + 1):
        state.handle_event("a", {"event": "noop"})
    assert state.trade_problem()["phase"] == "uncertain"                     # B never came back
    assert state.resolve_trade(token, "commit") == (True, "")
    assert state.pending_trade is None
    assert (entry.a.key, entry.b.key) == (A_GETS, B_GETS)                     # B's side defaults to unevolved
    assert entry.status.value != "alive", "A's held faint replayed after the swap"
    assert state.trade_last["outcome"] == "committed" and "admin" in state.trade_last["problem"]


@pytest.mark.asyncio
async def test_the_resolve_endpoint_drives_the_state(tmp_path):
    from unittest.mock import AsyncMock

    from server.server import SLinkServer
    srv = SLinkServer(data_dir=str(tmp_path))
    srv.state, _entry, token = _conflicted(tmp_path)
    bad = await srv.handle_debug_resolve_trade(AsyncMock(json=AsyncMock(return_value={"token": "t0",
                                                                                       "action": "commit"})))
    assert bad.status == 400
    ok = await srv.handle_debug_resolve_trade(AsyncMock(json=AsyncMock(return_value={"token": token,
                                                                                      "action": "rollback"})))
    assert ok.status == 200 and srv.state.trade_problem() is None


# ── MAJOR-1: a prepare round before either cartridge commits (opt-in per client) ─────────────────

def _hello(state, pid, prepare=True):
    key = {"a": B_GETS, "b": A_GETS}[pid]                                    # each side's own offered mon
    msg = {"event": "hello", "party": [{"key": key, "species_id": 1, "hp": 10, "maxHP": 10,
                                     "blob_hex": state.pending_trade[f"{pid}_blob_hex"]}]}
    if prepare:
        msg["trade_prepare"] = True
    state.handle_event(pid, msg)


def _preparing(tmp_path):
    state, entry, token, _ = _gen1_confirming(tmp_path)
    _hello(state, "a")
    _hello(state, "b")
    b_cmds = state.handle_event("b", {"event": "menu_result", "token": token, "choice": 1})
    a_cmds = _cmds(state, "a")
    return state, entry, token, a_cmds, b_cmds


def _named(cmds, name):
    return [c for c in cmds if c.get("cmd") == name]


def test_an_accept_prepares_both_sides_before_any_apply(tmp_path):
    state, _entry, token, a_cmds, b_cmds = _preparing(tmp_path)
    assert state.pending_trade["phase"] == "preparing"
    assert not _named(a_cmds + b_cmds, "apply_trade"), "nothing may commit before both are ready"
    assert _named(a_cmds, "apply_prepare") == [{"cmd": "apply_prepare", "token": token, "slot": 2,
                                                "old_key": B_GETS}]
    assert _named(b_cmds, "apply_prepare") == [{"cmd": "apply_prepare", "token": token, "slot": 4,
                                                "old_key": A_GETS}]
    a_cmds = state.handle_event("a", {"event": "apply_ready", "token": token, "ok": True})
    assert state.pending_trade["phase"] == "preparing" and not _named(a_cmds, "apply_trade")
    b_cmds = state.handle_event("b", {"event": "apply_ready", "token": token, "ok": True})
    assert state.pending_trade["phase"] == "applying"
    assert len(_named(b_cmds, "apply_trade")) == 1 and len(_named(_cmds(state, "a"), "apply_trade")) == 1


def test_a_side_that_cannot_take_the_apply_cancels_both_before_any_commit(tmp_path):
    """The dual-apply race: the proposer's cartridge left (or the responder's) between YES and APPLY."""
    state, entry, token, _a, _b = _preparing(tmp_path)
    state.handle_event("b", {"event": "apply_ready", "token": token, "ok": True})
    a_cmds = state.handle_event("a", {"event": "apply_ready", "token": token, "ok": False})
    b_cmds = _cmds(state, "b")
    assert state.pending_trade is None
    for cmds in (a_cmds, b_cmds):
        assert not _named(cmds, "apply_trade")
        assert any("did not go through" in c.get("text", "") for c in _named(cmds, "msgbox"))
    assert (entry.a.key, entry.b.key) == (B_GETS, A_GETS)


def test_an_initiator_withdrawal_while_preparing_cancels(tmp_path):
    state, _entry, token, _a, _b = _preparing(tmp_path)
    state.handle_event("a", {"event": "menu_result", "token": token, "choice": 0, "withdraw": True})
    assert state.pending_trade is None
    state.handle_event("b", {"event": "apply_ready", "token": token, "ok": True})
    assert state.pending_trade is None and not _named(_cmds(state, "b"), "apply_trade")


def test_a_client_without_the_capability_keeps_the_direct_apply(tmp_path):
    """Gen 3 (and any client that does not declare trade_prepare) is byte-for-byte unchanged."""
    state, _entry, token, _ = _gen1_confirming(tmp_path)
    _hello(state, "a")
    _hello(state, "b", prepare=False)
    b_cmds = state.handle_event("b", {"event": "menu_result", "token": token, "choice": 1})
    assert state.pending_trade["phase"] == "applying"
    assert _named(b_cmds, "apply_trade") and not _named(b_cmds, "apply_prepare")



# ── a player in the Bug-Catching Contest (tick trade_blocked) has no eligible pair, nor does the partner ──

def test_a_trade_blocked_player_makes_no_pair_eligible_on_either_side(tmp_path):
    state, _entry, _token, _ = _gen1_confirming(tmp_path)
    state.pending_trade = None
    assert state._eligible_trade_pairs("a")
    state.handle_event("b", {"event": "tick", "trade_blocked": True})
    assert not state._eligible_trade_pairs("a") and not state._eligible_trade_pairs("b")
    state._handle_trade_query("a")
    assert state.queued_commands["a"][-1] == {"cmd": "trade_mask", "mask": 0}
    state.handle_event("a", {"event": "trade_offer", "slot": 2})
    assert state.pending_trade is None
    state.handle_event("b", {"event": "tick", "trade_blocked": False})
    assert state._eligible_trade_pairs("a")



# ── MAJOR-4: the applying watchdog first asks each silent prepared side to withdraw ───────────────

def _prepared_applying(tmp_path):
    state, entry, token, _a, _b = _preparing(tmp_path)
    state.handle_event("a", {"event": "apply_ready", "token": token, "ok": True})
    state.handle_event("b", {"event": "apply_ready", "token": token, "ok": True})
    assert state.pending_trade["phase"] == "applying"
    _cmds(state, "a"), _cmds(state, "b")
    return state, entry, token


def _expire(state):
    out = {"a": [], "b": []}
    for _ in range(state.TRADE_WATCHDOG_EVENTS + 1):
        out["a"] += state.handle_event("a", {"event": "noop"})
    out["b"] += _cmds(state, "b")
    return out


def test_the_watchdog_asks_silent_sides_to_withdraw_before_anything_awaits(tmp_path):
    """An armed-but-unpicked Gen 1 APPLY could be picked up AFTER a watchdog rollback (the review
    probe P3): a silent side is first told to withdraw it, which gives a CERTAIN answer."""
    state, _entry, token = _prepared_applying(tmp_path)
    seen = []
    state.on_trade_outcome = seen.append
    out = _expire(state)
    for pid in ("a", "b"):
        assert {"cmd": "withdraw_trade", "token": token} in out[pid]
    assert state.pending_trade["phase"] == "applying" and seen == [], "no await, no uncertain yet"
    state.handle_event("a", {"event": "trade_done", "token": token, "new_key": B_GETS, "new_species": 0})
    _expire(state)                                                             # B stays silent
    assert state.pending_trade["phase"] == "uncertain"
    assert state.pending_trade["verdict"] == {"a": "none", "b": "await"}
    assert [r["outcome"] for r in seen] == ["uncertain"]


# ── MAJOR-5: a native result 2 is settled only by the post-reset hello, never the RAM tick ────────

def test_a_result_2_side_is_settled_by_its_post_reset_hello_not_a_tick(tmp_path):
    state, entry, token = _gen1_applying(tmp_path)
    state.handle_event("a", {"event": "trade_done", "token": token, "new_key": A_GETS, "new_species": 0x15})
    state.handle_event("b", {"event": "trade_done", "token": token, "uncertain": True, "after_reset": True})
    _tick(state, "b", _mon("0101:5678:99", 7))                   # the soft-locked RAM: neither mon
    assert state.pending_trade["phase"] == "applying" and state.pending_trade["verdict"]["b"] == "await"
    state.handle_event("b", {"event": "hello", "ot_id": "5678", "trainer_name": "Bob",
                             "party": [_mon(B_GETS, 0x26)]})                     # the reloaded save
    assert state.pending_trade is None
    assert (entry.a.key, entry.b.key) == (A_GETS, B_GETS)



# ── MINOR-1: trade_done.new_key is not taken on trust ─────────────────────────────────────────────

def test_a_reported_key_that_is_neither_the_incoming_mon_nor_its_evolution_is_a_conflict(tmp_path):
    from server.state import LinkEntry, LinkStatus, MonInfo
    state, entry, token = _gen1_applying(tmp_path)
    other = LinkEntry(area_id="route_2", a=MonInfo(key="9999:1234:05", species=5, level=9),
                      b=MonInfo(key="9999:5678:06", species=6, level=9), status=LinkStatus.ALIVE)
    state.links.append(other)
    state._index_entry(other)
    state.handle_event("a", {"event": "trade_done", "token": token, "new_key": "9999:5678:06", "new_species": 6})
    assert state.pending_trade["verdict"]["a"] not in ("traded", None, "await")
    state.handle_event("b", {"event": "trade_done", "token": token, "new_key": B_GETS, "new_species": 0x26})
    assert state.pending_trade["phase"] == "conflict"
    assert state._key_index["9999:5678:06"] is other, "another live link is never overwritten"


def test_a_reported_trade_evolution_is_accepted(tmp_path):
    state, entry, token = _gen1_applying(tmp_path)
    state.handle_event("a", {"event": "trade_done", "token": token, "new_key": A_GETS, "new_species": 0x15})
    state.handle_event("b", {"event": "trade_done", "token": token, "new_key": "ABCD:1234:95", "new_species": 0x95})
    assert state.pending_trade is None
    assert entry.b.key == "ABCD:1234:95" and entry.b.species == 0x95



# ── MINOR-6: per-key bookkeeping follows the traded mon to its new holder and key ─────────────────

def test_a_commit_moves_the_shiny_dedup_and_the_stats_cache_with_the_mon(tmp_path):
    state, entry, token = _gen1_applying(tmp_path)
    state.bonus_keys["a"].add(B_GETS)                       # A caught its offered mon as a shiny
    state.mon_stats["a"][B_GETS] = {"atk": 9}
    state.handle_event("a", {"event": "trade_done", "token": token, "new_key": A_GETS, "new_species": 0x15})
    state.handle_event("b", {"event": "trade_done", "token": token, "new_key": "ABCD:1234:95", "new_species": 0x95})
    assert state.pending_trade is None
    assert B_GETS not in state.bonus_keys["a"] and "ABCD:1234:95" in state.bonus_keys["b"]
    assert state.mon_stats["b"].get("ABCD:1234:95") == {"atk": 9} and B_GETS not in state.mon_stats["a"]



# ── NIT: a trade_done without the dispatched token counts toward nothing ─────────────────────────

def test_a_tokenless_trade_done_is_ignored(tmp_path):
    state, _entry, _token = _gen1_applying(tmp_path)
    state.handle_event("a", {"event": "trade_done", "new_key": A_GETS, "new_species": 0x15})
    state.handle_event("a", {"event": "trade_done", "token": "", "new_key": A_GETS, "new_species": 0x15})
    assert state.pending_trade["verdict"] == {"a": None, "b": None}



# ── asm review 1b33bc31 MINOR-4: vanilla trade sanity is a host-owned invariant ──────────────────

@pytest.mark.parametrize("broken, why", [
    ({"b": {"hp": 0}}, "A would keep no living mon: its only mon goes, a fainted one comes"),
    ({"b": {"species_id": 0xFF}}, "an invalid incoming species (CheckAnyOtherAliveMonsForTrade/ValidateOTTrademon)"),
    ({"b": {"level": 0}}, "an abnormal incoming level"),
    ({"a": {"level": 101}}, "the partner would receive an abnormal mon"),
])
def test_an_abnormal_or_last_living_trade_is_never_eligible(tmp_path, broken, why):
    state, _entry, _token, _ = _gen1_confirming(tmp_path)
    state.pending_trade = None
    for pid in ("a", "b"):
        state.partner_blobs[pid][0].update(level=30, hp=40)
    assert state._eligible_trade_pairs("a") and state._eligible_trade_pairs("b"), "control: a sane pair trades"
    for pid, fields in broken.items():
        state.partner_blobs[pid][0].update(fields)
    assert not state._eligible_trade_pairs("a"), why
    assert not state._eligible_trade_pairs("b"), why


def test_a_fainted_incoming_mon_is_fine_while_another_own_mon_lives(tmp_path):
    state, _entry, _token, _ = _gen1_confirming(tmp_path)
    state.pending_trade = None
    state.partner_blobs["a"][0].update(level=30, hp=40)
    state.partner_blobs["b"][0].update(level=30, hp=0)
    state.partner_blobs["a"].append({"slot": 0, "key": "ABCD:1234:99", "blob": bytes(66),
                                     "species_id": 0x99, "level": 5, "hp": 12})
    assert state._eligible_trade_pairs("a")


# ── TRADE-DRIVER live G<->S evolve: a hello back-fill must not rewrite the other half's species ──

def test_a_hello_holding_the_partners_key_mid_trade_does_not_break_the_evolution_check(tmp_path):
    """Live gs2_evolve: after the native swap each party holds the PARTNER's key; the hello's
    display back-fill wrote that species onto the player's OWN half, so B's trade-evolved report
    (Kadabra -> Alakazam) failed the family check against the wrong species."""
    state, entry, token = _gen1_applying(tmp_path)
    state.handle_event("a", {"event": "hello", "ot_id": "1234", "trainer_name": "Alice",
                             "party": [_mon(A_GETS, 0x15)]})            # A now holds B's mon
    assert entry.a.species == 0x26, "A's half is still the Kadabra A gave"
    state.handle_event("a", {"event": "trade_done", "token": token, "new_key": A_GETS, "new_species": 0x15})
    state.handle_event("b", {"event": "trade_done", "token": token, "new_key": "ABCD:1234:95", "new_species": 0x95})
    assert state.pending_trade is None, state.pending_trade and state.pending_trade.get("problem")
    assert entry.b.key == "ABCD:1234:95" and entry.b.species == 0x95
