"""KEY-SCOPE: mon identity is scoped per player (coordinator ruling after DUO-WAVE-D, 727c0976).

An in-game NPC-trade mon has fixed DVs and OT (Kyle's Onix is always 9666:BF1E:5F), so when
BOTH players make that trade they hold equal keys. The server accepted B's key_change and
then rejected A's as a collision, killing the pair (identity_lost). Two players may hold
equal keys; one player may not."""

import pytest

from server.adapters.gen1_rby import Gen1Adapter
from server.state import LinkEntry, LinkStatus, MonInfo, SoulLinkState
from tests.unit.test_state_trade_uncertain import _gen1_applying, _mon, _tick

ONIX = "9666:BF1E:5F"
A1, B1, A2, B2 = "ABCD:1234:26", "1234:5678:15", "ABCD:1234:30", "1234:5678:31"


def _two_links(tmp_path):
    state = SoulLinkState(data_dir=str(tmp_path), adapter=Gen1Adapter())
    links = []
    for area, a, b in (("route_1", A1, B1), ("route_2", A2, B2)):
        e = LinkEntry(area_id=area, a=MonInfo(key=a, species=0x26, level=20),
                      b=MonInfo(key=b, species=0x15, level=20), status=LinkStatus.ALIVE)
        state.links.append(e)
        state._index_entry(e)
        state.party_keys["a"].add(a)
        state.party_keys["b"].add(b)
        links.append(e)
    state.pokeballs_obtained = {"a": True, "b": True}
    return state, links


def _npc_trade(state, pid, old):
    return state.handle_event(pid, {"event": "key_change", "old_key": old, "new_key": ONIX,
                                    "new_species": 0x5F, "reason": "npc_trade"})


def _named(cmds, name):
    return [c for c in cmds if c.get("cmd") == name]


def test_both_players_npc_trading_to_the_same_key_keep_two_live_distinct_links(tmp_path):
    state, (l1, l2) = _two_links(tmp_path)
    assert _named(_npc_trade(state, "b", B1), "key_change_ack")
    a_cmds = _npc_trade(state, "a", A2)
    assert _named(a_cmds, "key_change_ack") and not _named(a_cmds, "key_change_rejected")
    assert l1.status == l2.status == LinkStatus.ALIVE
    assert (l1.b.key, l2.a.key) == (ONIX, ONIX) and (l1.a.key, l2.b.key) == (A1, B2)
    assert state.entry_for("a", ONIX) is l2 and state.entry_for("b", ONIX) is l1
    assert ONIX in state.party_keys["a"] and ONIX in state.party_keys["b"]
    # a faint of A's Onix kills its own partner (B2), never B's Onix
    state.handle_event("a", {"event": "faint", "key": ONIX})
    b_cmds = state.handle_event("b", {"event": "noop"})
    assert [c["key"] for c in _named(b_cmds, "force_faint")] == [B2]
    assert l2.status == LinkStatus.DEAD and l1.status == LinkStatus.ALIVE


def test_the_presentation_cache_of_the_other_player_is_no_collision(tmp_path):
    from server.server import SLinkServer
    srv = SLinkServer(data_dir=str(tmp_path))
    srv.state, (l1, l2) = _two_links(tmp_path)
    srv.state.presentation_key_in_use = srv._presentation_key_in_use
    srv._ingest_box_census("a", {"event": "tick", "pc_boxes": [], "pc_boxes_generation": 1})  # Gen 3 stamps
    srv.party_details["b"][ONIX] = {"species_id": 0x5F, "level": 20}        # B already holds an Onix
    assert not _named(_npc_trade(srv.state, "a", A2), "key_change_rejected")
    srv.party_details["a"]["ABCD:1234:77"] = {"species_id": 1, "level": 5}   # A holds an unlinked twin
    rejected = srv.state.handle_event("a", {"event": "key_change", "old_key": A1,
                                            "new_key": "ABCD:1234:77", "reason": "evolution"})
    assert _named(rejected, "key_change_rejected"), "one player may not hold two equal keys"


def test_one_player_with_a_true_duplicate_is_still_a_collision(tmp_path):
    state, (l1, l2) = _two_links(tmp_path)
    cmds = state.handle_event("a", {"event": "key_change", "old_key": A1, "new_key": A2,
                                    "reason": "evolution"})
    assert _named(cmds, "key_change_rejected")
    assert l1.status == LinkStatus.DEAD and l1.cause == "identity_lost", "the U5 fail-closed rule stands"
    assert l2.status == LinkStatus.ALIVE


def test_a_capture_collides_only_with_the_same_players_keys(tmp_path):
    state, (l1, l2) = _two_links(tmp_path)
    ok = state._check_link_violation(MonInfo(key="ABCD:1234:40", species=1), MonInfo(key=A1, species=4))
    assert ok is None or "collision" not in ok[0].lower(), "B may hold a key A holds"
    bad = state._check_link_violation(MonInfo(key=A1, species=1), MonInfo(key="1234:5678:40", species=4))
    assert bad and "collision" in bad[0].lower() and bad[1] == "a"
    same = state._check_link_violation(MonInfo(key=ONIX, species=0x5F), MonInfo(key=ONIX, species=0x5F))
    assert same is None or "collision" not in same[0].lower()


def test_the_per_player_index_survives_a_reload(tmp_path):
    state, (l1, l2) = _two_links(tmp_path)
    _npc_trade(state, "b", B1)
    _npc_trade(state, "a", A2)
    back = SoulLinkState.load(data_dir=str(tmp_path), adapter=Gen1Adapter())
    assert back.entry_for("a", ONIX).area_id == "route_2"
    assert back.entry_for("b", ONIX).area_id == "route_1"


# ── a native trade moves a mon between players; the per-player index follows it ─────────────────

A_GETS, B_GETS = "1234:5678:15", "ABCD:1234:26"


def test_a_native_commit_moves_each_key_to_its_new_holders_index(tmp_path):
    state, entry, token = _gen1_applying(tmp_path)
    state.handle_event("a", {"event": "trade_done", "token": token, "new_key": A_GETS, "new_species": 0x15})
    state.handle_event("b", {"event": "trade_done", "token": token, "new_key": B_GETS, "new_species": 0x26})
    assert state.pending_trade is None
    assert state.entry_for("a", A_GETS) is entry and state.entry_for("b", B_GETS) is entry
    assert state.entry_for("a", B_GETS) is None and state.entry_for("b", A_GETS) is None


def test_a_split_indexes_the_copy_under_its_holder_only(tmp_path):
    state, entry, token = _gen1_applying(tmp_path)
    state.handle_event("a", {"event": "trade_done", "token": token, "new_key": A_GETS, "new_species": 0x15})
    state.handle_event("b", {"event": "trade_done", "token": token, "new_key": A_GETS, "new_species": 0x15})
    assert state.resolve_trade(token, "adopt") == (True, "")
    assert state.entry_for("a", A_GETS) is entry and state.entry_for("b", A_GETS) is entry
    assert state.entry_for("a", B_GETS) is None
    _tick(state, "a", _mon(A_GETS, 0x15))
    assert entry.status == LinkStatus.ALIVE


# ── KEY-SCOPE-2 (OMP cx-e13143c8) ────────────────────────────────────────────────────────────────

def test_a_key_in_the_partners_pending_shiny_bonus_is_no_collision(tmp_path):
    state, (l1, l2) = _two_links(tmp_path)
    state.pending_bonus["a"].append(ONIX)        # B caught a shiny ONIX; A's bonus encounter awaits
    cmds = _npc_trade(state, "a", A2)
    assert _named(cmds, "key_change_ack") and l2.status == LinkStatus.ALIVE
    assert list(state.pending_bonus["a"]) == [ONIX], "B's shiny key is not A's to migrate"
    state.pending_bonus["b"].append("ABCD:1234:77")   # A's own shiny, queued for B
    cmds = state.handle_event("a", {"event": "key_change", "old_key": A1, "new_key": "ABCD:1234:77"})
    assert _named(cmds, "key_change_rejected"), "A's own pending shiny still collides"


def _boxed_twin(state, key="1234:5678:15"):
    """A live link whose A half, boxed in A's PC, has the key A is about to receive."""
    twin = LinkEntry(area_id="route_9", a=MonInfo(key=key, species=0x15, level=9),
                     b=MonInfo(key="5555:5678:44", species=0x44, level=9), status=LinkStatus.ALIVE)
    state.links.append(twin)
    state._index_entry(twin)
    return twin


def test_a_trade_offer_is_refused_when_the_recipient_already_indexes_the_incoming_key(tmp_path):
    from tests.unit.test_state_trade_uncertain import _gen1_confirming
    state, _entry, _token, _ = _gen1_confirming(tmp_path)
    state.pending_trade = None
    assert state._eligible_trade_pairs("a"), "control"
    _boxed_twin(state)
    assert not state._eligible_trade_pairs("a") and not state._eligible_trade_pairs("b")
    ack = [c for c in state.handle_event("a", {"event": "trade_offer", "slot": 2})
           if c.get("cmd") == "trade_offer_ack"]
    assert ack == [{"cmd": "trade_offer_ack", "ok": False}] and state.pending_trade is None


def _assert_fail_closed(state, entry, twin):
    assert state.entry_for("a", A_GETS) is twin, "the existing link keeps A's index row"
    assert twin.status == LinkStatus.ALIVE
    assert entry.status == LinkStatus.DEAD and entry.cause == "identity_lost"


def test_a_commit_onto_a_key_the_taker_already_holds_retires_the_traded_pair(tmp_path):
    state, entry, token = _gen1_applying(tmp_path)
    twin = _boxed_twin(state)                     # appeared after the offer (the race)
    state.handle_event("a", {"event": "trade_done", "token": token, "new_key": A_GETS, "new_species": 0x15})
    state.handle_event("b", {"event": "trade_done", "token": token, "new_key": B_GETS, "new_species": 0x26})
    assert state.pending_trade is None
    _assert_fail_closed(state, entry, twin)


def test_a_split_onto_a_key_the_taker_already_holds_retires_the_traded_pair(tmp_path):
    state, entry, token = _gen1_applying(tmp_path)
    twin = _boxed_twin(state)
    state.handle_event("a", {"event": "trade_done", "token": token, "new_key": A_GETS, "new_species": 0x15})
    state.handle_event("b", {"event": "trade_done", "token": token, "new_key": A_GETS, "new_species": 0x15})
    assert state.resolve_trade(token, "adopt") == (True, "")
    _assert_fail_closed(state, entry, twin)


def test_the_other_players_stats_never_overwrite_a_shared_key(tmp_path):
    state, (l1, l2) = _two_links(tmp_path)
    _npc_trade(state, "b", B1)
    _npc_trade(state, "a", A2)                                   # both hold ONIX now
    state.handle_event("a", {"event": "stats_cache", "key": ONIX, "stats": {"level": 14, "maxHP": 40}})
    state.handle_event("b", {"event": "stats_cache", "key": ONIX, "stats": {"level": 55, "maxHP": 150}})
    assert state.mon_stats["a"][ONIX]["level"] == 14 and state.mon_stats["b"][ONIX]["level"] == 55
    # A's partner withdraws its half: A's party_mon for ONIX carries A's stats, not B's
    state.party_keys["b"].discard(B2)
    state.handle_event("b", {"event": "box_to_party", "key": B2})
    a_cmds = state.handle_event("a", {"event": "noop"})
    pm = [c for c in a_cmds if c.get("cmd") == "party_mon" and c.get("key") == ONIX]
    assert pm and pm[0]["stats"]["level"] == 14


def test_an_old_flat_mon_stats_file_is_migrated_per_player(tmp_path):
    import json
    state, (l1, l2) = _two_links(tmp_path)
    state._save()
    path = tmp_path / "links.json"
    doc = json.loads(path.read_text())
    doc["mon_stats"] = {A1: {"level": 3}, B2: {"level": 4}, "FFFF:0000:01": {"level": 5}}
    path.write_text(json.dumps(doc))
    back = SoulLinkState.load(data_dir=str(tmp_path), adapter=Gen1Adapter())
    assert back.mon_stats["a"] == {A1: {"level": 3}, "FFFF:0000:01": {"level": 5}}
    assert back.mon_stats["b"] == {B2: {"level": 4}, "FFFF:0000:01": {"level": 5}}
    back._save()
    assert set(json.loads(path.read_text())["mon_stats"]) == {"a", "b"}


# ── KEY-SCOPE-3 (OMP cx-4e252daa): a trade-window clash latches the key as ambiguous ─────────────

def _clashing_commit(tmp_path):
    state, entry, token = _gen1_applying(tmp_path)
    twin = _boxed_twin(state)
    state.mon_stats["a"][A_GETS] = {"level": 9}                 # the twin's stats
    state.mon_stats["b"][A_GETS] = {"level": 30}                # the traded mon's, on B
    state.handle_event("a", {"event": "trade_done", "token": token, "new_key": A_GETS, "new_species": 0x15})
    state.handle_event("b", {"event": "trade_done", "token": token, "new_key": B_GETS, "new_species": 0x26})
    assert state.pending_trade is None and entry.status == LinkStatus.DEAD
    for pid in ("a", "b"):
        state.handle_event(pid, {"event": "noop"})
    return state, entry, twin


def test_after_a_clash_a_faint_of_the_key_never_reaches_the_twins_partner(tmp_path):
    state, _entry, twin = _clashing_commit(tmp_path)
    assert A_GETS in state.ambiguous_keys["a"]
    state.handle_event("a", {"event": "faint", "key": A_GETS})       # which of the two? unknown
    b_cmds = state.handle_event("b", {"event": "noop"})
    assert twin.status == LinkStatus.ALIVE and not [c for c in b_cmds if c.get("cmd") == "force_faint"]
    assert state.ambiguous_keys["a"][A_GETS]["refused"] == 1
    for event in ("party_to_box", "box_to_party", "release"):
        state.handle_event("a", {"event": event, "key": A_GETS})
    assert twin.status == LinkStatus.ALIVE and not state.queued_commands["b"]
    state.handle_event("a", {"event": "hello", "ot_id": "1234", "trainer_name": "Alice",
                             "party": [{**_mon(A_GETS, 0x15), "hp": 0}]})
    assert twin.status == LinkStatus.ALIVE, "nor through the hello's hp-0 scan"
    back = SoulLinkState.load(data_dir=str(tmp_path), adapter=Gen1Adapter())
    assert A_GETS in back.ambiguous_keys["a"], "the latch is persisted"


def test_an_admin_resolves_the_latch_and_the_key_routes_to_the_twin_again(tmp_path):
    state, _entry, twin = _clashing_commit(tmp_path)
    assert state.resolve_ambiguous_key("a", "nope")[0] is False
    assert state.resolve_ambiguous_key("a", A_GETS) == (True, "")
    state.handle_event("a", {"event": "faint", "key": A_GETS})
    assert twin.status == LinkStatus.DEAD


def test_a_clashing_commit_leaves_the_twins_stats_alone(tmp_path):
    state, _entry, _twin = _clashing_commit(tmp_path)
    assert state.mon_stats["a"][A_GETS] == {"level": 9}


def test_a_clashing_split_leaves_the_twins_stats_alone(tmp_path):
    state, entry, token = _gen1_applying(tmp_path)
    _boxed_twin(state)
    state.mon_stats["a"][A_GETS] = {"level": 9}
    state.mon_stats["b"][A_GETS] = {"level": 30}
    state.handle_event("a", {"event": "trade_done", "token": token, "new_key": A_GETS, "new_species": 0x15})
    state.handle_event("b", {"event": "trade_done", "token": token, "new_key": A_GETS, "new_species": 0x15})
    assert state.resolve_trade(token, "adopt") == (True, "")
    assert state.mon_stats["a"][A_GETS] == {"level": 9} and A_GETS in state.ambiguous_keys["a"]


def test_the_resolve_ambiguous_key_endpoint(tmp_path):
    import asyncio
    from unittest.mock import AsyncMock
    from server.server import SLinkServer
    srv = SLinkServer(data_dir=str(tmp_path))
    srv.state, _entry, _twin = _clashing_commit(tmp_path)
    call = lambda body: asyncio.run(srv.handle_debug_resolve_ambiguous_key(
        AsyncMock(json=AsyncMock(return_value=body))))
    assert call(None).status == 400 and call({"player": "a", "key": "x"}).status == 400
    assert call({"player": "a", "key": A_GETS}).status == 200 and not srv.state.ambiguous_keys["a"]


# ── KEY-SCOPE-4 (DUO-WAVE-D G<->S): the changing player's own tick may win the race ─────────────

def _srv_two_links(tmp_path):
    from server.server import SLinkServer
    srv = SLinkServer(data_dir=str(tmp_path))
    srv.state, links = _two_links(tmp_path)
    srv.state.adapter = srv.adapter = Gen1Adapter(variant="red")
    srv.state.presentation_key_in_use = srv._presentation_key_in_use
    # the snapshot before the test's own: the self-report proof needs old_key in it (cx-06ec4e8e F3)
    srv.state._ingest_party_blobs("a", _snap(A1, A2))
    srv.state._ingest_party_blobs("b", _snap(B1, B2))
    return srv, links


def _snap(*keys):
    blob = "00" * Gen1Adapter().party_blob_size()
    return [{**_mon(k, 0x5F), "slot": i, "level": 20, "blob_hex": blob} for i, k in enumerate(keys)]


def test_a_tick_reporting_the_new_key_before_the_key_change_is_the_same_mon(tmp_path):
    srv, (l1, l2) = _srv_two_links(tmp_path)
    srv._dispatch("b", {"event": "tick", "party": _snap(ONIX, B2), "pc_boxes": [],
                        "pc_boxes_generation": 1})                         # the tick wins the race
    cmds = srv._dispatch("b", {"event": "key_change", "old_key": B1, "new_key": ONIX,
                               "new_species": 0x5F, "reason": "npc_trade"})
    assert _named(cmds, "key_change_ack") and not _named(cmds, "key_change_rejected")
    assert l1.status == LinkStatus.ALIVE and srv.state.entry_for("b", ONIX) is l1


def test_a_tick_first_true_duplicate_in_the_party_still_rejects(tmp_path):
    srv, (l1, l2) = _srv_two_links(tmp_path)
    srv._dispatch("b", {"event": "tick", "party": _snap(ONIX, ONIX, B2), "pc_boxes": [],
                        "pc_boxes_generation": 1})                         # B already had an Onix
    cmds = srv._dispatch("b", {"event": "key_change", "old_key": B1, "new_key": ONIX, "reason": "npc_trade"})
    assert _named(cmds, "key_change_rejected") and l1.cause == "identity_lost"


def test_a_tick_first_change_onto_a_boxed_twin_still_rejects(tmp_path):
    srv, (l1, l2) = _srv_two_links(tmp_path)
    srv._dispatch("b", {"event": "tick", "party": _snap(ONIX, B2), "pc_boxes_generation": 1,
                        "pc_boxes": [{"box": 0, "slot": 0, "key": ONIX, "species_id": 0x5F}]})
    cmds = srv._dispatch("b", {"event": "key_change", "old_key": B1, "new_key": ONIX, "reason": "npc_trade"})
    assert _named(cmds, "key_change_rejected") and l1.cause == "identity_lost"


# ── KEY-SCOPE-5 (OMP cx-ee316c45): raw party census, box-census generation, replay ledger ─────

def _snap5(*keys, no_blob=()):
    """A party snapshot; slots listed in `no_blob` carry blob_hex="" (a failed Gen 3 blob read)."""
    return [{**m, "blob_hex": ""} if i in no_blob else m for i, m in enumerate(_snap(*keys))]


def _tick5(srv, pid, party, gen=1, boxes=(), event="tick"):
    """A snapshot with a complete box census (`pc_boxes` + `pc_boxes_generation`)."""
    return srv._dispatch(pid, {"event": event, "party": party, "pc_boxes": list(boxes),
                               "pc_boxes_generation": gen})


def _change5(srv, pid, old, new=ONIX):
    return srv._dispatch(pid, {"event": "key_change", "old_key": old, "new_key": new,
                               "new_species": 0x5F, "reason": "npc_trade"})


def test_duplicate_party_keys_are_counted_before_blob_filtering(tmp_path):
    srv, (l1, l2) = _srv_two_links(tmp_path)
    _tick5(srv, "b", _snap5(ONIX, ONIX, B2, no_blob=(1,)))     # the twin has no blob
    cmds = _change5(srv, "b", B1)
    assert len(_named(cmds, "key_change_rejected")) == 1 and not _named(cmds, "key_change_ack")
    assert srv.state.entry_for("b", B1) is l1 and l1.cause == "identity_lost"


def test_same_mon_tick_without_blob_is_self_reported(tmp_path):
    srv, (l1, l2) = _srv_two_links(tmp_path)
    _tick5(srv, "b", _snap5(ONIX, B2, no_blob=(0,)))
    ack = _named(_change5(srv, "b", B1), "key_change_ack")
    assert ack and ack[0]["migrated"] is True
    assert l1.b.key == ONIX and srv.state.entry_for("b", B1) is None and l1.status == LinkStatus.ALIVE


def test_box_twin_with_omitted_census_is_rejected(tmp_path):
    srv, (l1, l2) = _srv_two_links(tmp_path)
    _tick5(srv, "b", _snap5(B1, B2))                                   # a complete (empty) census
    srv._dispatch("b", {"event": "tick", "party": _snap5(ONIX, B2)})   # census omitted
    cmds = _change5(srv, "b", B1)
    rej = _named(cmds, "key_change_rejected")
    assert rej and rej[0]["reason"] == "box census unavailable" and not _named(cmds, "key_change_ack")
    assert srv.state.entry_for("b", ONIX) is None
    # uncertainty is not evidence of a collision: the pair is NOT retired (cx-8f3a6ce9 F2)
    assert l1.status == LinkStatus.ALIVE and srv.state.entry_for("b", B1) is l1
    a_cmds = srv.state.handle_event("a", {"event": "noop"})
    assert not [c for c in cmds + a_cmds if c.get("cmd") in ("force_faint", "force_explode", "memorialize")]


def test_a_client_without_a_census_generation_keeps_presence_semantics(tmp_path):
    """A foundation that implements no `pc_boxes_generation` (base default; Gen 3 stamps since
    KEY-SCOPE-5): an omitted census is not a rejection."""
    from server.adapters.gen3_frlge import Gen3Adapter
    srv, (l1, l2) = _srv_two_links(tmp_path)
    legacy = type("Legacy", (Gen3Adapter,), {"reports_box_census": lambda self: False})
    srv.state.adapter = srv.adapter = legacy()
    srv._dispatch("b", {"event": "tick", "party": _snap5(ONIX, B2)})
    assert _named(_change5(srv, "b", B1), "key_change_ack")


def test_delayed_old_tick_after_acceptance_does_not_rollback_party(tmp_path):
    state, (l1, l2) = _two_links(tmp_path)
    assert _named(_npc_trade(state, "b", B1), "key_change_ack")
    cmds = state.handle_event("b", {"event": "tick", "party": [_mon(B1, 0x15), _mon(B2, 0x15)]})
    assert state.party_keys["b"] == {ONIX, B2}
    a_cmds = state.handle_event("a", {"event": "noop"})
    assert not [c for c in cmds + a_cmds if c.get("cmd") in ("party_mon", "box_mon")]
    # an alias AND its terminal key in one snapshot are two live mons: never aliased, never latched
    state.handle_event("b", {"event": "tick", "party": [_mon(B1, 0x15), _mon(ONIX, 0x5F), _mon(B2, 0x15)]})
    assert state.party_keys["b"] == {B1, ONIX, B2} and not state.ambiguous_keys["b"]


def test_both_players_may_migrate_to_the_same_npc_trade_key_with_fresh_censuses(tmp_path):
    srv, (l1, l2) = _srv_two_links(tmp_path)
    _tick5(srv, "b", _snap5(ONIX, B2))
    _tick5(srv, "a", _snap5(A1, ONIX))
    assert _named(_change5(srv, "b", B1), "key_change_ack")
    assert _named(_change5(srv, "a", A2), "key_change_ack")
    assert l1.status == l2.status == LinkStatus.ALIVE
    assert srv.state.entry_for("a", ONIX) is l2 and srv.state.entry_for("b", ONIX) is l1
    srv.state.handle_event("a", {"event": "faint", "key": ONIX})
    b_cmds = srv.state.handle_event("b", {"event": "noop"})
    assert [c["key"] for c in _named(b_cmds, "force_faint")] == [B2] and l1.status == LinkStatus.ALIVE


def test_both_halves_of_one_pair_may_npc_trade_to_the_same_key(tmp_path):
    """DUO-WAVE-D's own case (727c0976): both players traded the two halves of ONE pair to Kyle,
    so the pair's halves share a key. Both changes are accepted, and a faint still crosses."""
    state, (l1, l2) = _two_links(tmp_path)
    assert _named(_npc_trade(state, "b", B1), "key_change_ack")
    a_cmds = _npc_trade(state, "a", A1)
    assert _named(a_cmds, "key_change_ack") and not _named(a_cmds, "key_change_rejected")
    assert (l1.a.key, l1.b.key) == (ONIX, ONIX) and l1.status == LinkStatus.ALIVE
    assert state.entry_for("a", ONIX) is l1 and state.entry_for("b", ONIX) is l1
    state.handle_event("a", {"event": "faint", "key": ONIX})
    b_cmds = state.handle_event("b", {"event": "noop"})
    assert [c["key"] for c in _named(b_cmds, "force_faint")] == [ONIX]
    assert l1.status == LinkStatus.DEAD and l2.status == LinkStatus.ALIVE


def test_the_protocol_doc_scopes_key_uniqueness_per_player():
    """docs/protocol.md §3.1 and checklist item 38 were written (ce39251f, 2026-09-14) when the
    server refused a pair whose halves shared a key; KEY-SCOPE (71d68454) scoped identity per
    player and dropped that refusal. The doc must not demand distinct halves."""
    from pathlib import Path
    doc = (Path(__file__).resolve().parents[2] / "docs" / "protocol.md").read_text(encoding="utf-8")
    lines = doc.splitlines()
    uniqueness = [ln for ln in lines if ln.startswith("| Uniqueness |")]
    item38 = [ln for ln in lines if ln.startswith("38. ")]
    assert len(uniqueness) == 1 and len(item38) == 1
    for row in uniqueness + item38:
        assert "distinct keys" not in row, row
        assert "per player" in row, row


@pytest.mark.parametrize("event", ["safe", "tick"])
def test_safe_and_tick_self_report_are_identical(tmp_path, event):
    srv, (l1, l2) = _srv_two_links(tmp_path)
    _tick5(srv, "b", _snap5(ONIX, B2, no_blob=(0,)), event=event)
    ack = _named(_change5(srv, "b", B1), "key_change_ack")
    assert ack and ack[0]["migrated"] is True
    assert srv.state.entry_for("b", ONIX) is l1 and srv.state.party_keys["b"] == {ONIX, B2}
    assert srv.state.party_key_census["b"][ONIX] == 1


def test_a_fresh_hello_never_aliases_a_retired_key(tmp_path):
    """cx-8f3a6ce9 F3/F4: a hello is the cartridge's own word; a new mon that happens to carry a
    retired key is that key, not the migration's terminal key."""
    state, (l1, l2) = _two_links(tmp_path)
    assert _named(_npc_trade(state, "b", B1), "key_change_ack")          # ledger {B1 -> ONIX}
    state.handle_event("b", {"event": "hello", "party": [_mon(B1, 0x15), _mon(B2, 0x15)]})
    assert state.party_keys["b"] == {B1, B2} and not state.ambiguous_keys["b"]


def test_a_stale_tick_aliases_only_where_the_terminal_key_sat(tmp_path):
    state, (l1, l2) = _two_links(tmp_path)
    state.handle_event("b", {"event": "tick", "party": [{**_mon(B1, 0x15), "slot": 0}, {**_mon(B2, 0x15), "slot": 1}]})
    assert _named(_npc_trade(state, "b", B1), "key_change_ack")
    state.handle_event("b", {"event": "tick", "party": [{**_mon(ONIX, 0x5F), "slot": 0}, {**_mon(B2, 0x15), "slot": 1}]})
    # B1 back in ONIX's slot, ONIX gone: the stale case, aliased
    state.handle_event("b", {"event": "tick", "party": [{**_mon(B1, 0x15), "slot": 0}, {**_mon(B2, 0x15), "slot": 1}]})
    assert state.party_keys["b"] == {ONIX, B2}
    # B1 in ANOTHER slot is a live mon of its own
    state.handle_event("b", {"event": "tick", "party": [{**_mon(ONIX, 0x5F), "slot": 0}, {**_mon(B2, 0x15), "slot": 1}]})
    state.handle_event("b", {"event": "tick", "party": [{**_mon(B2, 0x15), "slot": 0}, {**_mon(B1, 0x15), "slot": 1}]})
    assert B1 in state.party_keys["b"] and ONIX not in state.party_keys["b"]


def test_a_deep_ledger_keeps_a_snapshot_cheap(tmp_path):
    """cx-8f3a6ce9 F7: a 256-deep ledger, a 6-key party, one hello and one tick well under 5 ms."""
    import time
    state, _ = _two_links(tmp_path)
    keys = [f"{i:04X}:5678:15" for i in range(257)]
    state.key_migration_ledger["b"].extend({"old_key": o, "new_key": n} for o, n in zip(keys, keys[1:]))
    party = [_mon(keys[0], 0x15)] + [_mon(f"{i:04X}:9999:15", 0x15) for i in range(5)]
    state._save = lambda: None
    t0 = time.perf_counter()
    state.handle_event("b", {"event": "hello", "party": party})
    state.handle_event("b", {"event": "tick", "party": party})
    assert time.perf_counter() - t0 < 0.005



# ── KEY-SCOPE-5 final round (OMP cx-06ec4e8e) ─────────────────────────────────────────────

def test_a_census_foundation_without_a_generation_is_census_unavailable(tmp_path):
    """F1: Gen 1/2 implement the generation, so a snapshot without one is no census at all --
    never the legacy presence check, and never a death."""
    srv, (l1, l2) = _srv_two_links(tmp_path)
    srv._dispatch("b", {"event": "tick", "party": _snap5(ONIX, B2),
                        "pc_boxes": [{"box": 0, "slot": 0, "key": ONIX, "species_id": 0x5F}]})
    cmds = _change5(srv, "b", B1)
    assert [c["reason"] for c in _named(cmds, "key_change_rejected")] == ["box census unavailable"]
    assert l1.status == LinkStatus.ALIVE and l1.b.key == B1


def test_a_box_mutation_makes_the_census_stale(tmp_path):
    """F2: a deposit after the census may have boxed the twin: refuse until a newer generation."""
    srv, (l1, l2) = _srv_two_links(tmp_path)
    _tick5(srv, "b", _snap5(B1, B2, ONIX, no_blob=(2,)))
    srv._dispatch("b", {"event": "party_to_box", "key": ONIX})
    cmds = _change5(srv, "b", B1)
    assert [c["reason"] for c in _named(cmds, "key_change_rejected")] == ["box census unavailable"]
    assert l1.status == LinkStatus.ALIVE


def test_self_report_needs_old_key_in_the_previous_snapshot(tmp_path):
    """F3: a new key already in the PREVIOUS snapshot is another mon, not the changing one."""
    srv, (l1, l2) = _srv_two_links(tmp_path)
    _tick5(srv, "b", _snap5(B1, ONIX, B2))
    _tick5(srv, "b", _snap5(ONIX, B2))
    rej = _named(_change5(srv, "b", B1), "key_change_rejected")
    assert rej and rej[0]["reason"].startswith("key collision") and l1.cause == "identity_lost"
