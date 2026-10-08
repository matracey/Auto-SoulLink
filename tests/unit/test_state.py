"""
Unit tests for server/state.py — SoulLinkState FSM.

No emulator, no HTTP server required.  Feed GameState-equivalent dicts directly.

Run:
    pytest tests/unit/test_state.py -v
"""

import pytest

from server.adapters.gen3_frlge import Gen3Adapter
from server.state import (
    AreaStatus,
    LinkEntry,
    LinkStatus,
    MonInfo,
    SoulLinkState,
    is_shiny,
)

# ── helpers ───────────────────────────────────────────────────────────────────

def make_state_with_link(a_key="A:1", b_key="B:2", area="route_1",
                          status=LinkStatus.ALIVE) -> SoulLinkState:
    """Return a SoulLinkState pre-loaded with one linked pair (Pokéballs already obtained)."""
    state = SoulLinkState()
    entry = LinkEntry(
        area_id=area,
        a=MonInfo(key=a_key, level=5),
        b=MonInfo(key=b_key, level=7),
        status=status,
    )
    state.links.append(entry)
    state._index_entry(entry)
    state.area_states[area] = AreaStatus.LINKED
    state.party_keys["a"].add(a_key)
    state.party_keys["b"].add(b_key)
    state.pokeballs_obtained = {"a": True, "b": True}
    state.party_size = {"a": 2, "b": 2}
    return state


def noop_only(cmds: list) -> bool:
    # `config` (overworld_presence rule), `resolved_areas` and `dead_keys` (INV-CLIENT-2) are benign
    # informational pushes the server always queues on hello — they never represent a sync action.
    return all(c.get("cmd") in ("noop", "play_sound", "resolved_areas", "config", "dead_keys") for c in cmds)


def has_cmd(cmds: list, cmd: str, key: str | None = None) -> bool:
    return any(c.get("cmd") == cmd and (key is None or c.get("key") == key) for c in cmds)


# ── faint propagation ─────────────────────────────────────────────────────────

def test_faint_queues_force_faint_for_partner(tmp_path, monkeypatch):
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = make_state_with_link()

    # A's mon faints — A gets memorialize for their own mon; B gets force_faint + memorialize
    cmds_a = state.handle_event("a", {"event": "faint", "key": "A:1"})
    assert has_cmd(cmds_a, "memorialize", "A:1"), "A should receive memorialize for their own mon"

    cmds_b = state.handle_event("b", {"event": "tick"})
    assert has_cmd(cmds_b, "force_faint", "B:2"), "B should receive force_faint for its mon"


def test_faint_marks_entry_dead(tmp_path, monkeypatch):
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = make_state_with_link()
    state.handle_event("a", {"event": "faint", "key": "A:1"})
    assert state.links[0].status == LinkStatus.DEAD


def test_wild_faint_is_ignored_when_rule_enabled(tmp_path, monkeypatch):
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = make_state_with_link()
    state.ignore_wild_faints = True

    cmds_a = state.handle_event(
        "a", {"event": "faint", "key": "A:1", "_is_wild_battle": True})
    cmds_b = state.handle_event("b", {"event": "tick"})

    assert noop_only(cmds_a) and noop_only(cmds_b)
    assert state.links[0].status == LinkStatus.ALIVE
    assert "A:1" in state.party_keys["a"]


def test_trainer_faint_still_counts_when_wild_faints_ignored(tmp_path, monkeypatch):
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = make_state_with_link()
    state.ignore_wild_faints = True

    state.handle_event(
        "a", {"event": "faint", "key": "A:1", "_is_wild_battle": False})

    assert state.links[0].status == LinkStatus.DEAD


def test_faint_of_unknown_key_is_ignored(tmp_path, monkeypatch):
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState()
    cmds = state.handle_event("a", {"event": "faint", "key": "UNKNOWN:KEY"})
    assert noop_only(cmds)


def test_faint_of_already_dead_mon_is_ignored(tmp_path, monkeypatch):
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = make_state_with_link(status=LinkStatus.DEAD)
    cmds_a = state.handle_event("a", {"event": "faint", "key": "A:1"})
    cmds_b = state.handle_event("b", {"event": "tick"})
    assert noop_only(cmds_a)
    assert noop_only(cmds_b), "Dead mon should not trigger another force_faint"


def test_partner_faint_also_propagates(tmp_path, monkeypatch):
    """Faint from B's side should also queue force_faint for A."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = make_state_with_link()
    state.handle_event("b", {"event": "faint", "key": "B:2"})
    cmds_a = state.handle_event("a", {"event": "tick"})
    assert has_cmd(cmds_a, "force_faint", "A:1")


# ── ghost_pos relay (engine-NPC peer ghost) ───────────────────────────────────

def _ghost_cmds(cmds):
    return [c for c in cmds if c.get("cmd") == "ghost_pos"]


def test_ghost_pos_relays_to_partner(tmp_path, monkeypatch):
    """A's overworld position relays to B (and only B) as a ghost_pos command."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = make_state_with_link()
    state.overworld_presence = True  # opt-in per-run feature

    cmds_a = state.handle_event("a", {"event": "ghost_pos",
                                      "mg": 3, "mn": 5, "x": 352, "y": 160, "f": 4, "gfx": 7})
    # The sender doesn't get their own ghost back.
    assert not _ghost_cmds(cmds_a)

    cmds_b = state.handle_event("b", {"event": "tick"})
    gb = _ghost_cmds(cmds_b)
    assert len(gb) == 1
    g = gb[0]
    assert (g["mg"], g["mn"], g["x"], g["y"], g["f"], g["gfx"]) == (3, 5, 352, 160, 4, 7)


def test_ghost_pos_coalesces(tmp_path, monkeypatch):
    """Many ghost_pos before the partner drains keep only the latest (queue can't grow)."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = make_state_with_link()
    state.overworld_presence = True  # opt-in per-run feature

    for x in (16, 160, 352):
        state.handle_event("a", {"event": "ghost_pos",
                                 "mg": 1, "mn": 1, "x": x, "y": 0, "f": 1, "gfx": 0})
    # Exactly one ghost_pos queued for B, carrying the most recent position.
    q = state.queued_commands["b"]
    assert len(_ghost_cmds(q)) == 1
    assert _ghost_cmds(q)[0]["x"] == 352


def test_ghost_pos_relays_mv_run_gfx(tmp_path, monkeypatch):
    """The engine-driven ghost rides on world-pixel coords + mv/run/gfx; the patch LERPs + animates."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = make_state_with_link()
    state.overworld_presence = True  # opt-in per-run feature
    state.handle_event("a", {"event": "ghost_pos", "mg": 1, "mn": 1,
                             "x": 16, "y": 9, "f": 4, "mv": 1, "run": 1, "gfx": 5})
    g = _ghost_cmds(state.handle_event("b", {"event": "tick"}))[0]
    assert (g["x"], g["y"], g["f"], g["mv"], g["run"], g["gfx"]) == (16, 9, 4, 1, 1, 5)


def test_ghost_pos_relays_avatar_and_anim(tmp_path, monkeypatch):
    """The partner's avatar (live sprite images/anims ROM ptrs + true palette) and live animNum
    must relay so the ghost renders as THEMSELVES, animating exactly as they move."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = make_state_with_link()
    state.overworld_presence = True  # opt-in per-run feature
    pcol = "".join(f"{(0x0421 * (i + 1)) & 0x7FFF:04X}" for i in range(16))
    state.handle_event("a", {"event": "ghost_pos", "mg": 1, "mn": 1,
                             "x": 32, "y": 16, "f": 2, "mv": 1, "an": 10,
                             "imgs": 0x08EB3810, "anim": 0x083A3470, "pcol": pcol})
    g = _ghost_cmds(state.handle_event("b", {"event": "tick"}))[0]
    assert g["an"] == 10
    assert g["imgs"] == 0x08EB3810 and g["anim"] == 0x083A3470
    assert g["pcol"] == pcol


def test_peer_interact_notifies_partner(tmp_path, monkeypatch):
    """Talk-to-ghost notifies the OTHER player (not the initiator)."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = make_state_with_link()
    state.overworld_presence = True  # opt-in per-run feature
    cmds_a = state.handle_event("a", {"event": "peer_interact"})
    assert not has_cmd(cmds_a, "msgbox")          # initiator gets nothing back
    cmds_b = state.handle_event("b", {"event": "tick"})
    assert has_cmd(cmds_b, "msgbox")              # partner is notified


def test_ghost_pos_not_persisted(tmp_path, monkeypatch):
    """Ephemeral: a ghost_pos must never be written to the links file."""
    links = tmp_path / "links.json"
    monkeypatch.setattr("server.state.LINKS_PATH", str(links))
    state = make_state_with_link()
    state.overworld_presence = True  # opt-in per-run feature
    state.handle_event("a", {"event": "ghost_pos",
                             "mg": 1, "mn": 1, "x": 99, "y": 0, "f": 1, "gfx": 0})
    if links.exists():
        assert "ghost_pos" not in links.read_text()


def test_overworld_presence_off_drops_relay(tmp_path, monkeypatch):
    """Overworld Presence is opt-in (default off): with the flag off, ghost_pos and
    peer_interact are dropped server-side so no ghost ever spawns for the partner."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = make_state_with_link()  # overworld_presence defaults to False
    assert state.overworld_presence is False

    state.handle_event("a", {"event": "ghost_pos",
                             "mg": 1, "mn": 1, "x": 99, "y": 0, "f": 1, "gfx": 0})
    state.handle_event("a", {"event": "peer_interact"})
    cmds_b = state.handle_event("b", {"event": "tick"})
    assert not _ghost_cmds(cmds_b)         # no ghost relayed
    assert not has_cmd(cmds_b, "msgbox")   # no say-hey notification


# ── talk-to-partner: trade linked pairs + status helper ───────────────────────

def _with_trade_blobs(state, a_slot=0, b_slot=1):
    """Populate partner_blobs for the make_state_with_link pair (A:1 / B:2) so a trade is eligible."""
    state.partner_blobs["a"] = [{"slot": a_slot, "species_id": 1, "level": 5,
                                 "key": "A:1", "blob": bytes([0xAA] * 100)}]
    state.partner_blobs["b"] = [{"slot": b_slot, "species_id": 4, "level": 7,
                                 "key": "B:2", "blob": bytes([0xBB] * 100)}]
    return state


def _menu(cmds):
    return [c for c in cmds if c.get("cmd") == "show_menu"]


def _choose(cmds):
    return [c for c in cmds if c.get("cmd") == "choose_mon"]


def _choices(cmds):
    return [c for c in cmds if c.get("cmd") == "show_choices"]


def _open_trade(state, who="a"):
    """trade_request -> action multichoice -> pick TRADE (index 0). Returns token (phase 'choosing')."""
    token = _choices(state.handle_event(who, {"event": "trade_request"}))[0]["token"]
    state.handle_event(who, {"event": "menu_result", "token": token, "choice": 0})   # 0 = Trade
    return token


def _offer_and_pick(state, slot=0, who="a"):
    """trade_request -> TRADE -> pick `slot`. Returns the token (partner now has the confirm queued)."""
    token = _open_trade(state, who)
    state.handle_event(who, {"event": "mon_chosen", "token": token, "slot": slot})
    return token


def test_trade_request_opens_action_menu(tmp_path, monkeypatch):
    """Talking to the partner opens the native action multichoice (Trade / Say hey) — not the picker yet."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = _with_trade_blobs(make_state_with_link())
    cmds_a = state.handle_event("a", {"event": "trade_request"})
    mc = _choices(cmds_a)
    assert len(mc) == 1 and mc[0]["options"] == ["Trade", "Say hey"] and not _choose(cmds_a)
    assert state.pending_trade and state.pending_trade["phase"] == "menu"
    # The talk-NPC speaks a line under the menu. Presence OFF (default) -> the PC trade NPC (Oak);
    # presence ON -> the partner's ghost (no Oak).
    assert mc[0]["text"].startswith("OAK:")
    state.pending_trade = None
    state.overworld_presence = True
    mc_on = _choices(state.handle_event("a", {"event": "trade_request"}))
    assert mc_on[0]["text"] and "OAK" not in mc_on[0]["text"]


def test_menu_say_hey_notifies_partner(tmp_path, monkeypatch):
    """Choosing SAY HEY (index 1) on the action menu just pings the partner — no trade."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = _with_trade_blobs(make_state_with_link())
    token = _choices(state.handle_event("a", {"event": "trade_request"}))[0]["token"]
    state.handle_event("a", {"event": "menu_result", "token": token, "choice": 1})   # 1 = Say hey
    assert state.pending_trade is None
    cmds_b = state.handle_event("b", {"event": "tick"})
    assert has_cmd(cmds_b, "msgbox")                                                 # partner pinged
    assert any("says hey" in c.get("text", "") for c in cmds_b if c.get("cmd") == "msgbox")


def test_menu_trade_opens_picker(tmp_path, monkeypatch):
    """Choosing TRADE (index 0) opens the native party picker."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = _with_trade_blobs(make_state_with_link())
    token = _choices(state.handle_event("a", {"event": "trade_request"}))[0]["token"]
    cmds_a = state.handle_event("a", {"event": "menu_result", "token": token, "choice": 0})
    assert _choose(cmds_a) and state.pending_trade["phase"] == "choosing"


def test_menu_cancel_aborts(tmp_path, monkeypatch):
    """B-press (cancel, 127) on the action menu aborts with no trade and no say-hey."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = _with_trade_blobs(make_state_with_link())
    token = _choices(state.handle_event("a", {"event": "trade_request"}))[0]["token"]
    state.handle_event("a", {"event": "menu_result", "token": token, "choice": 127})
    assert state.pending_trade is None


def test_pick_linked_mon_offers_partner_confirm(tmp_path, monkeypatch):
    """Picking an eligible linked mon sends ONLY the PARTNER a confirm (initiator already committed)."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = _with_trade_blobs(make_state_with_link())
    token = _open_trade(state)
    cmds_a = state.handle_event("a", {"event": "mon_chosen", "token": token, "slot": 0})
    assert not _menu(cmds_a)                                  # initiator gets no confirm menu
    cmds_b = state.handle_event("b", {"event": "tick"})
    assert _menu(cmds_b) and _menu(cmds_b)[0]["token"] == token   # partner gets the confirm
    assert state.pending_trade["phase"] == "confirming"


def test_pick_non_linked_mon_reprompts(tmp_path, monkeypatch):
    """The linked-pair invariant: picking a slot that is NOT a linked pair re-prompts, never trades."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = _with_trade_blobs(make_state_with_link())   # only slot 0 (A:1) is an eligible linked mon
    token = _open_trade(state)
    cmds_a = state.handle_event("a", {"event": "mon_chosen", "token": token, "slot": 3})  # not linked
    assert _choose(cmds_a) and not _menu(cmds_a)         # re-prompt, no confirm
    assert has_cmd(cmds_a, "msgbox")                     # "Pick a linked POKeMON!"
    assert state.pending_trade["phase"] == "choosing"


def test_partner_accept_then_done_reconciles(tmp_path, monkeypatch):
    """Partner accepts -> apply_trade(native) to each; the link is swapped ATOMICALLY only once BOTH
    sides report trade_done (never observable half-swapped), reconciling key + trade-evolution."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = _with_trade_blobs(make_state_with_link())
    token = _offer_and_pick(state, slot=0)
    assert state.pending_trade["phase"] == "confirming"
    cmds_b = state.handle_event("b", {"event": "menu_result", "token": token, "choice": 1})  # accept
    assert state.pending_trade["phase"] == "applying"
    bt = next(c for c in cmds_b if c.get("cmd") == "apply_trade")
    assert bt["slot"] == 1 and bt["blob_hex"] == "aa" * 100   # B receives A's mon (0xAA)
    at = next(c for c in state.handle_event("a", {"event": "tick"}) if c.get("cmd") == "apply_trade")
    assert at["slot"] == 0 and at["blob_hex"] == "bb" * 100   # A receives B's mon (0xBB)
    e = state.links[0]
    assert e.a.key == "A:1" and e.b.key == "B:2"             # NOT swapped yet — wait for both trade_done
    # post-scene readback: A's received mon trade-EVOLVED (species -> 5).
    state.handle_event("a", {"event": "trade_done", "token": token, "slot": 0, "new_key": "B:2", "new_species": 5})
    assert state.pending_trade is not None, "still waiting on B's trade_done"
    assert e.a.key == "A:1", "link stays UNCHANGED until BOTH report (no half-swapped state)"
    cmds_b2 = state.handle_event("b", {"event": "trade_done", "token": token, "slot": 1, "new_key": "A:1", "new_species": 1})
    assert state.pending_trade is None
    assert e.a.key == "B:2" and e.b.key == "A:1"             # atomic ownership swap on both-done
    assert e.a.species == 5
    assert has_cmd(cmds_b2, "msgbox") and has_cmd(cmds_b2, "play_sound")   # jingle + "Traded!" after scene
    assert "B:2" in state.party_keys["a"] and "A:1" in state.party_keys["b"]


def test_trade_one_side_done_leaves_link_untouched(tmp_path, monkeypatch):
    """If only ONE side reports trade_done (the other's scene hangs / fails), the link must NOT be
    half-swapped — the 'pairs break if you wait too long' corruption. Nothing changes until both report."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = _with_trade_blobs(make_state_with_link())
    token = _offer_and_pick(state, slot=0)
    state.handle_event("b", {"event": "menu_result", "token": token, "choice": 1})   # accept
    state.handle_event("a", {"event": "tick"})                                       # deliver A's apply_trade
    state.handle_event("a", {"event": "trade_done", "token": token, "slot": 0, "new_key": "B:2", "new_species": 5})
    e = state.links[0]
    assert state.pending_trade is not None and state.pending_trade["phase"] == "applying"
    assert e.a.key == "A:1" and e.b.key == "B:2", "no swap until B also reports"
    assert "A:1" in state.party_keys["a"] and "B:2" in state.party_keys["b"], "party_keys untouched"


def test_trade_watchdog_frees_abandoned_slot(tmp_path, monkeypatch):
    """An abandoned trade (a side never responds) eventually frees the single pending_trade slot so
    future trades aren't wedged — and because the swap is atomic, the link is left untouched."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = _with_trade_blobs(make_state_with_link())
    _offer_and_pick(state, slot=0)                                   # phase 'confirming'; partner never answers
    assert state.pending_trade is not None
    for _ in range(state.TRADE_WATCHDOG_EVENTS + 5):                 # unrelated activity, no trade progress
        state.handle_event("b", {"event": "tick"})
    assert state.pending_trade is None, "watchdog frees the abandoned slot"
    assert state.links[0].a.key == "A:1", "link untouched by an abandoned trade"


def test_trade_watchdog_makes_applying_uncertain_then_evidence_commits(tmp_path, monkeypatch):
    """Once apply_trade is DISPATCHED the slot must not simply be freed, but the watchdog must not
    fabricate the silent side's keys either: that side becomes UNCERTAIN and its next party
    snapshot settles it (tests/unit/test_state_trade_uncertain.py covers every branch)."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = _with_trade_blobs(make_state_with_link())
    token = _offer_and_pick(state, slot=0)
    state.handle_event("b", {"event": "menu_result", "token": token, "choice": 1})   # accept
    # A reports; B's scene hangs forever.
    state.handle_event("a", {"event": "trade_done", "token": token, "slot": 0, "new_key": "B:2", "new_species": 5})
    for _ in range(state.TRADE_WATCHDOG_EVENTS + 5):
        state.handle_event("a", {"event": "tick"})
    e = state.links[0]
    assert state.pending_trade["phase"] == "uncertain" and e.a.key == "A:1", "no fabricated commit"
    state.handle_event("b", {"event": "tick", "party": [{"key": "A:1", "species_id": 1}]})
    assert state.pending_trade is None
    assert e.a.key == "B:2" and e.b.key == "A:1"
    assert "B:2" in state.party_keys["a"] and "A:1" in state.party_keys["b"]


def test_execute_trade_revalidates_offer(tmp_path, monkeypatch):
    """The players free-roam while the partner deliberates: if the offered pair died or a half
    left the party since mon_chosen, the confirm must ABORT — not dispatch a stale pre-death
    blob (resurrecting a dead mon) and re-add DEAD keys to party_keys."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    # Pair fainted during 'confirming'.
    state = _with_trade_blobs(make_state_with_link())
    token = _offer_and_pick(state, slot=0)
    state.links[0].status = LinkStatus.DEAD
    cmds_b = state.handle_event("b", {"event": "menu_result", "token": token, "choice": 1})
    assert state.pending_trade is None
    assert not has_cmd(cmds_b, "apply_trade")
    assert any("no longer available" in c.get("text", "") for c in cmds_b if c.get("cmd") == "msgbox")
    # A half was boxed during 'confirming'.
    state2 = _with_trade_blobs(make_state_with_link())
    token2 = _offer_and_pick(state2, slot=0)
    state2.party_keys["a"].discard("A:1")
    cmds_b2 = state2.handle_event("b", {"event": "menu_result", "token": token2, "choice": 1})
    assert state2.pending_trade is None and not has_cmd(cmds_b2, "apply_trade")


def test_stale_trade_done_token_is_dropped(tmp_path, monkeypatch):
    """A trade_done carrying an OLD trade's token must not count toward the current trade
    (a replayed report from an aborted trade could otherwise commit wrong keys)."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = _with_trade_blobs(make_state_with_link())
    token = _offer_and_pick(state, slot=0)
    state.handle_event("b", {"event": "menu_result", "token": token, "choice": 1})   # accept
    state.handle_event("a", {"event": "trade_done", "slot": 0, "token": "t999",     # stale token
                             "new_key": "B:2", "new_species": 5})
    assert state.pending_trade is not None and not state.pending_trade["done"]["a"]
    # The matching token (and, for old clients, a missing one) IS counted.
    state.handle_event("a", {"event": "trade_done", "slot": 0, "token": token,
                             "new_key": "B:2", "new_species": 5})
    assert state.pending_trade["done"]["a"]


def test_reconcile_suppressed_only_while_applying(tmp_path, monkeypatch):
    """A trade wedged at the MENU stage (player walked away without answering) must not
    suspend party/box drift enforcement for both runs until the watchdog fires; only the
    'applying' phase (RAM actually mutating) suppresses the reconciler."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))

    def fresh_trigger_state():   # same reconcile trigger as the settle-window test above
        st = make_state_with_link()
        st.party_keys["a"].discard("A:1")
        st.party_keys["b"].discard("B:2")
        return st

    wedged = fresh_trigger_state()
    wedged.pending_trade = {"phase": "menu", "initiator": "a", "token": "t1", "age": 0}
    wedged.handle_event("a", {"event": "tick", "party": [{"key": "A:1"}]})
    assert has_cmd(wedged.queued_commands["b"], "party_mon", "B:2"), \
        "a menu-stage trade must not suppress drift reconciliation"

    applying = fresh_trigger_state()
    applying.pending_trade = {"phase": "applying", "initiator": "a", "token": "t1", "age": 0}
    applying.handle_event("a", {"event": "tick", "party": [{"key": "A:1"}]})
    assert not has_cmd(applying.queued_commands["b"], "party_mon"), \
        "the applying phase (RAM mutating) still suppresses the reconciler"


def test_malformed_ghost_pos_is_dropped(tmp_path, monkeypatch):
    """A null/garbage field in a ghost_pos (nil RAM read on the client) must be dropped, not
    raise out of handle_event — an uncaught error tears down the TCP loop at ~20 Hz."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = make_state_with_link()
    state.overworld_presence = True
    state.handle_event("a", {"event": "ghost_pos", "mg": 1, "mn": 1,
                             "x": None, "y": 16, "f": 2})            # x: null
    state.handle_event("a", {"event": "ghost_pos", "mg": 1, "mn": 1,
                             "x": "garbage", "y": 16, "f": 2})       # non-numeric
    assert not _ghost_cmds(state.handle_event("b", {"event": "tick"})), \
        "malformed samples are dropped, not relayed"


def test_post_trade_settle_suppresses_reconcile_unbox(tmp_path, monkeypatch):
    """Regression: after a trade completes, a still-settling party tick must NOT make the drift
    reconciler queue a spurious party_mon ('Unbox') to the partner — the user's residual 'Unbox HUD'
    after a working trade. The post-trade settle window suppresses _reconcile_party_keys until the
    clients' party snapshots settle; the window is bounded so genuine drift still self-heals."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))

    # A reconcile trigger: a live link whose halves are BOTH server-side boxed (party_keys empty), with
    # player A's tick reporting its half in-party → "ghost-boxed" → reconcile pulls the partner via party_mon.
    def fresh_trigger_state():
        st = make_state_with_link()
        st.party_keys["a"].discard("A:1")
        st.party_keys["b"].discard("B:2")
        return st

    # Baseline (no settle window): the tick DOES queue a partner party_mon (the bug path exists).
    base = fresh_trigger_state()
    base.handle_event("a", {"event": "tick", "party": [{"key": "A:1"}]})
    assert has_cmd(base.queued_commands["b"], "party_mon", "B:2"), \
        "sanity: reconcile normally pulls the partner via party_mon"

    # With the post-trade settle window armed, the SAME tick is suppressed — no spurious Unbox.
    armed = fresh_trigger_state()
    armed._trade_settle_ticks = {"a": armed.TRADE_SETTLE_TICKS, "b": armed.TRADE_SETTLE_TICKS}
    armed.handle_event("a", {"event": "tick", "party": [{"key": "A:1"}]})
    assert not has_cmd(armed.queued_commands["b"], "party_mon"), \
        "post-trade settle window must suppress the spurious partner Unbox"

    # Bounded: after the window drains, the reconciler resumes (genuine drift still heals).
    for _ in range(armed.TRADE_SETTLE_TICKS + 1):
        armed.handle_event("a", {"event": "tick", "party": [{"key": "A:1"}]})
    assert has_cmd(armed.queued_commands["b"], "party_mon", "B:2"), \
        "after the settle window drains, reconcile resumes (window is bounded, not permanent)"


def test_trade_done_arms_post_trade_settle_window(tmp_path, monkeypatch):
    """A completed trade arms the settle window on BOTH sides (the guard that prevents the post-trade
    Unbox is wired to the real trade-completion path, not just unit-pokeable)."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = _with_trade_blobs(make_state_with_link())
    token = _offer_and_pick(state, slot=0)
    state.handle_event("b", {"event": "menu_result", "token": token, "choice": 1})   # accept
    state.handle_event("a", {"event": "tick"})                                       # deliver A's apply_trade
    state.handle_event("a", {"event": "trade_done", "token": token, "slot": 0, "new_key": "B:2", "new_species": 5})
    state.handle_event("b", {"event": "trade_done", "token": token, "slot": 1, "new_key": "A:1", "new_species": 1})
    assert state.pending_trade is None
    assert state._trade_settle_ticks["a"] == state.TRADE_SETTLE_TICKS
    assert state._trade_settle_ticks["b"] == state.TRADE_SETTLE_TICKS


def test_trade_uses_the_matching_link_half(tmp_path, monkeypatch):
    """MATCHING-pair guarantee: with multiple links, picking one mon trades EXACTLY that link's
    counterpart half — the server auto-selects the partner's matching mon, never another link's."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = make_state_with_link("A:1", "B:2", "route_1")               # link L1
    e2 = LinkEntry(area_id="route_2", a=MonInfo(key="A:3", level=5),
                   b=MonInfo(key="B:4", level=5), status=LinkStatus.ALIVE)
    state.links.append(e2)
    state._index_entry(e2)
    state.party_keys["a"].add("A:3")
    state.party_keys["b"].add("B:4")
    state.partner_blobs["a"] = [{"slot": 0, "key": "A:1", "blob": bytes([0x11] * 100)},
                                {"slot": 1, "key": "A:3", "blob": bytes([0x33] * 100)}]
    state.partner_blobs["b"] = [{"slot": 0, "key": "B:2", "blob": bytes([0x22] * 100)},
                                {"slot": 1, "key": "B:4", "blob": bytes([0x44] * 100)}]
    # A picks slot 1 = A:3 (half of L2). The matching half is B:4 — NOT B:2 (a different link).
    token = _offer_and_pick(state, slot=1)
    assert state.pending_trade["link"].area_id == "route_2"             # the L2 link was selected
    cmds_b = state.handle_event("b", {"event": "menu_result", "token": token, "choice": 1})
    bt = next(c for c in cmds_b if c.get("cmd") == "apply_trade")
    at = next(c for c in state.handle_event("a", {"event": "tick"}) if c.get("cmd") == "apply_trade")
    assert at["slot"] == 1 and at["blob_hex"] == "44" * 100   # A receives B:4 (the MATCHING half), not B:2
    assert bt["slot"] == 1 and bt["blob_hex"] == "33" * 100   # B receives A:3


def test_partner_declines_cancels(tmp_path, monkeypatch):
    """The partner declining the offer cancels and applies nothing."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = _with_trade_blobs(make_state_with_link())
    token = _offer_and_pick(state, slot=0)
    cmds_b = state.handle_event("b", {"event": "menu_result", "token": token, "choice": 0})  # decline
    assert state.pending_trade is None
    assert has_cmd(cmds_b, "msgbox") and not has_cmd(cmds_b, "apply_trade")
    assert state.links[0].a.key == "A:1", "link unchanged on decline"


def test_menu_trade_no_pair(tmp_path, monkeypatch):
    """Choosing TRADE with no eligible linked pair tells the initiator, no picker opens."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = make_state_with_link()          # no partner_blobs => nothing tradeable
    token = _choices(state.handle_event("a", {"event": "trade_request"}))[0]["token"]
    cmds_a = state.handle_event("a", {"event": "menu_result", "token": token, "choice": 0})  # TRADE
    assert has_cmd(cmds_a, "msgbox") and not _choose(cmds_a)
    assert state.pending_trade is None


def test_status_badges_cached(tmp_path, monkeypatch):
    """The status event caches each player's badge count (kept for future helpers; not shown in text)."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = _with_trade_blobs(make_state_with_link())
    state.handle_event("a", {"event": "status", "badges": 5})
    state.handle_event("b", {"event": "status", "badges": 2})
    assert state.player_badges == {"a": 5, "b": 2}


# ── explode_mode (run rule) ───────────────────────────────────────────────────

def test_explode_mode_defaults_false():
    """explode_mode must default to False so existing runs stay on the legacy force_faint path."""
    state = SoulLinkState()
    assert state.explode_mode is False


def test_explode_mode_round_trips_through_save_load(tmp_path, monkeypatch):
    """Setting explode_mode=True must persist via _save() and be restored by load()."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState(data_dir=str(tmp_path), explode_mode=True)
    state._save()
    reloaded = SoulLinkState.load(data_dir=str(tmp_path))
    assert reloaded.explode_mode is True


def test_explode_mode_off_emits_force_faint(tmp_path, monkeypatch):
    """With explode_mode=False (default), partner-faint propagation sends `force_faint`."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = make_state_with_link()
    assert state.explode_mode is False
    state.handle_event("a", {"event": "faint", "key": "A:1"})
    cmds_b = state.handle_event("b", {"event": "tick"})
    assert has_cmd(cmds_b, "force_faint", "B:2")
    assert not has_cmd(cmds_b, "force_explode")


# ── overworld_presence (run rule) ─────────────────────────────────────────────

def test_overworld_presence_defaults_false():
    """overworld_presence must default to False — peer ghost is now opt-in per run."""
    state = SoulLinkState()
    assert state.overworld_presence is False


def test_overworld_presence_stays_off_through_save_load(tmp_path, monkeypatch):
    """Deferred post-RC: overworld_presence=True is ignored at construction and on reload
    (tests/unit/test_overworld_presence_deferred.py)."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState(data_dir=str(tmp_path), overworld_presence=True)
    state._save()
    reloaded = SoulLinkState.load(data_dir=str(tmp_path))
    assert reloaded.overworld_presence is False


def test_explode_mode_on_emits_force_explode(tmp_path, monkeypatch):
    """With explode_mode=True on Radical Red, partner-faint propagation sends `force_explode`.

    The adapter must say it supports the command — only RR's Lua client implements it.
    See tests/unit/test_explode_mode_gate.py for the non-RR fallback.
    """
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = make_state_with_link()
    state.adapter = Gen3Adapter(is_rr=True)
    state.explode_mode = True
    state.handle_event("a", {"event": "faint", "key": "A:1"})
    cmds_b = state.handle_event("b", {"event": "tick"})
    assert has_cmd(cmds_b, "force_explode", "B:2")
    assert not has_cmd(cmds_b, "force_faint", "B:2"), \
        "Explode Mode must NOT emit force_faint when force_explode replaces it"


def test_faint_before_nuzlocke_active_is_ignored(tmp_path, monkeypatch):
    """Faint before pokéballs obtained must not trigger Soul Link death (e.g. starter KO)."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = make_state_with_link()
    state.pokeballs_obtained["a"] = False  # simulate pre-nuzlocke state

    cmds_a = state.handle_event("a", {"event": "faint", "key": "A:1"})
    cmds_b = state.handle_event("b", {"event": "tick"})
    assert noop_only(cmds_a)
    assert noop_only(cmds_b), "No force_faint before nuzlocke is active"
    assert state.links[0].status == LinkStatus.ALIVE, "Link should remain alive"


def test_hello_reconcile_faint_before_nuzlocke_ignored(tmp_path, monkeypatch):
    """On reconnect, hp=0 mons are not treated as dead if nuzlocke wasn't active yet."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = make_state_with_link()
    state.pokeballs_obtained["a"] = False

    state.handle_event("a", {"event": "hello",
                              "has_pokeballs": False,
                              "party": [{"key": "A:1", "hp": 0, "maxHP": 50, "level": 5}]})
    cmds_b = state.handle_event("b", {"event": "tick"})
    assert noop_only(cmds_b), "No force_faint — nuzlocke wasn't active when A's mon fainted"
    assert state.links[0].status == LinkStatus.ALIVE


# ── encounter linking ─────────────────────────────────────────────────────────

def test_both_captures_create_link(tmp_path, monkeypatch):
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState()

    state.handle_event("a", {"event": "capture", "key": "A:1", "area_id": "route_1", "level": 5})
    assert state.area_states.get("route_1") == AreaStatus.PENDING_B  # waiting for B to capture
    assert len(state.links) == 0

    notice = [c["text"] for c in state.queued_commands["b"] if c.get("cmd") == "hud_show"]
    assert notice and notice[-1].startswith("Partner caught ") and " in " in notice[-1], notice

    state.handle_event("b", {"event": "capture", "key": "B:2", "area_id": "route_1", "level": 7})
    assert state.area_states.get("route_1") == AreaStatus.LINKED
    assert len(state.links) == 1
    assert state.links[0].a.key == "A:1"
    assert state.links[0].b.key == "B:2"
    assert state.links[0].status == LinkStatus.ALIVE


def test_capture_in_dead_zone_gets_force_fainted(tmp_path, monkeypatch):
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState()
    state.area_states["route_1"] = AreaStatus.DEAD_ZONE

    cmds = state.handle_event("a", {"event": "capture", "key": "A:1", "area_id": "route_1"})
    assert has_cmd(cmds, "force_faint", "A:1"), "Capture in dead zone must be force-fainted"
    assert len(state.links) == 0


def test_duplicate_capture_same_player_ignored(tmp_path, monkeypatch):
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState()

    state.handle_event("a", {"event": "capture", "key": "A:1", "area_id": "route_1", "level": 5})
    state.handle_event("a", {"event": "capture", "key": "A:1", "area_id": "route_1", "level": 5})
    assert state.pending_captures["route_1"]["a"].key == "A:1"
    assert len(state.links) == 0  # still only one capture recorded


def test_second_capture_in_pending_area_retired(tmp_path, monkeypatch):
    """A second capture (different mon) in an area where the player already
    has a pending capture must be force-fainted and memorialized."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState()
    state.pokeballs_obtained = {"a": True, "b": True}

    # First capture — goes to pending
    cmds1 = state.handle_event("a", {"event": "capture", "key": "A:1", "area_id": "route_1", "level": 5})
    assert state.pending_captures["route_1"]["a"].key == "A:1"
    assert not has_cmd(cmds1, "force_faint", "A:1"), "First capture must not be retired"

    # Second capture (different mon) — must be retired
    cmds2 = state.handle_event("a", {"event": "capture", "key": "A:99", "area_id": "route_1", "level": 7})
    assert has_cmd(cmds2, "force_faint", "A:99"), "Second capture must be force-fainted"
    assert has_cmd(cmds2, "memorialize", "A:99"), "Second capture must be memorialized"
    # Original pending capture is preserved
    assert state.pending_captures["route_1"]["a"].key == "A:1"


def test_extra_capture_in_linked_area_ignored(tmp_path, monkeypatch):
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = make_state_with_link()
    original_count = len(state.links)
    state.handle_event("a", {"event": "capture", "key": "A:99", "area_id": "route_1"})
    assert len(state.links) == original_count, "Should not create a second link in an already-linked area"


# ── box captures (in_box flag) ───────────────────────────────────────────────

def test_box_capture_not_added_to_party_keys(tmp_path, monkeypatch):
    """A capture with in_box=True must NOT be added to party_keys."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState()
    state.pokeballs_obtained = {"a": True, "b": True}
    state.handle_event("a", {"event": "capture", "key": "A:1", "area_id": "route_1",
                              "level": 5, "in_box": True})
    assert "A:1" not in state.party_keys["a"], "Box-captured mon must not be in party_keys"
    assert state.pending_captures["route_1"]["a"].key == "A:1"


def test_box_capture_party_capture_link_retrieves_both(tmp_path, monkeypatch):
    """When A captures to box and B captures to party, link forms and both
    get party_mon commands to retrieve from box (quarantine un-quarantine)."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState()
    state.pokeballs_obtained = {"a": True, "b": True}
    # A captures to box (full party)
    state.handle_event("a", {"event": "capture", "key": "A:1", "area_id": "route_1",
                              "level": 5, "in_box": True})
    # B captures to party → quarantine queues box_mon for B, then link forms → party_mon for both
    cmds_b = state.handle_event("b", {"event": "capture", "key": "B:2",
                                       "area_id": "route_1", "level": 7})
    assert state.area_states["route_1"] == AreaStatus.LINKED
    # B should get party_mon (not box_mon — the quarantine box_mon should be cancelled by party_mon)
    assert has_cmd(cmds_b, "party_mon", "B:2"), \
        "B's mon should be queued for party_mon after link (un-quarantine)"
    # A should get party_mon queued too
    assert any(c["cmd"] == "party_mon" and c["key"] == "A:1" for c in state.queued_commands["a"])


def test_both_box_captures_link_retrieves_both(tmp_path, monkeypatch):
    """When both players capture to box, both get party_mon after link."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState()
    state.pokeballs_obtained = {"a": True, "b": True}
    state.handle_event("a", {"event": "capture", "key": "A:1", "area_id": "route_1",
                              "level": 5, "in_box": True})
    cmds_b = state.handle_event("b", {"event": "capture", "key": "B:2", "area_id": "route_1",
                              "level": 7, "in_box": True})
    assert state.area_states["route_1"] == AreaStatus.LINKED
    # Both get party_mon
    assert has_cmd(cmds_b, "party_mon", "B:2")
    assert any(c["cmd"] == "party_mon" and c["key"] == "A:1" for c in state.queued_commands["a"])


def test_both_party_captures_link_retrieves_both(tmp_path, monkeypatch):
    """When both players capture to party, both get quarantined then un-quarantined."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState()
    state.pokeballs_obtained = {"a": True, "b": True}
    state.party_size = {"a": 2, "b": 2}
    cmds_a = state.handle_event("a", {"event": "capture", "key": "A:1", "area_id": "route_1", "level": 5})
    # A should be quarantined (box_mon queued)
    assert has_cmd(cmds_a, "box_mon", "A:1"), "Pending capture should be quarantined"
    assert "A:1" not in state.party_keys["a"], "Pending capture should not be in party_keys"
    # B captures → link forms → both get party_mon
    cmds_b = state.handle_event("b", {"event": "capture", "key": "B:2", "area_id": "route_1", "level": 7})
    assert state.area_states["route_1"] == AreaStatus.LINKED
    # B's quarantine box_mon should be cancelled by party_mon
    assert has_cmd(cmds_b, "party_mon", "B:2")
    assert not has_cmd(cmds_b, "box_mon", "B:2"), "Quarantine box_mon should be cancelled"
    # A gets party_mon queued
    assert any(c["cmd"] == "party_mon" and c["key"] == "A:1" for c in state.queued_commands["a"])


def test_box_capture_level_preserved(tmp_path, monkeypatch):
    """Box capture should preserve the level sent from Lua."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState()
    state.pokeballs_obtained = {"a": True, "b": True}
    state.handle_event("a", {"event": "capture", "key": "A:1", "area_id": "route_1",
                              "level": 12, "in_box": True})
    state.handle_event("b", {"event": "capture", "key": "B:2", "area_id": "route_1", "level": 14})
    assert state.links[0].a.level == 12
    assert state.links[0].b.level == 14


def test_party_capture_then_box_capture_link_retrieves_both(tmp_path, monkeypatch):
    """When A captures to party first (quarantined), then B captures to box,
    both get party_mon after link."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState()
    state.pokeballs_obtained = {"a": True, "b": True}
    # A captures to party (quarantined immediately)
    state.handle_event("a", {"event": "capture", "key": "A:1", "area_id": "route_1", "level": 5})
    assert "A:1" not in state.party_keys["a"], "Pending capture should be quarantined"
    # B captures to box → link forms → both get party_mon
    state.handle_event("b", {"event": "capture", "key": "B:2", "area_id": "route_1",
                              "level": 7, "in_box": True})
    assert state.area_states["route_1"] == AreaStatus.LINKED
    # A's queued_commands should have party_mon (box_mon quarantine should be cancelled)
    cmds_a = state.handle_event("a", {"event": "tick"})
    assert has_cmd(cmds_a, "party_mon", "A:1"), \
        "A's mon should get party_mon after link un-quarantine"


# ── quarantine (unlinked encounters) ──────────────────────────────────────────

def test_quarantine_pending_capture_boxed(tmp_path, monkeypatch):
    """A party capture that's still pending should be quarantined (box_mon queued)."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState()
    state.pokeballs_obtained = {"a": True, "b": True}
    state.party_size = {"a": 2, "b": 2}
    cmds = state.handle_event("a", {"event": "capture", "key": "A:1",
                                     "area_id": "route_1", "level": 5})
    assert has_cmd(cmds, "box_mon", "A:1"), "Pending capture should get box_mon"
    assert "A:1" not in state.party_keys["a"], "Pending key should not be in party_keys"


def test_quarantine_hello_re_quarantines(tmp_path, monkeypatch):
    """On reconnect, if a pending mon is in party, re-quarantine it."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState()
    state.pokeballs_obtained = {"a": True, "b": True}
    # Create a pending capture
    state.handle_event("a", {"event": "capture", "key": "A:1",
                              "area_id": "route_1", "level": 5})
    # Simulate reconnect — mon is in party (box_mon didn't execute or was reverted)
    # Include a second alive mon so re-quarantine is safe (won't leave 0 alive mons)
    cmds = state.handle_event("a", {"event": "hello",
                                     "party": [{"key": "A:1", "hp": 30, "maxHP": 30},
                                               {"key": "STARTER:9999", "hp": 22, "maxHP": 22}],
                                     "has_pokeballs": True})
    assert "A:1" not in state.party_keys["a"], "Pending key must be removed from party_keys"
    assert has_cmd(cmds, "box_mon", "A:1"), "Re-quarantine box_mon should be queued"


def test_quarantine_box_to_party_blocked(tmp_path, monkeypatch):
    """Manual withdrawal of a quarantined mon should be re-quarantined."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState()
    state.pokeballs_obtained = {"a": True, "b": True}
    # Create pending capture (quarantined)
    state.handle_event("a", {"event": "capture", "key": "A:1",
                              "area_id": "route_1", "level": 5})
    # Simulate box_mon executed (stats_cache confirms it)
    state.handle_event("a", {"event": "stats_cache", "key": "A:1",
                              "stats": {"level": 5, "maxHP": 20}})
    # Player manually withdraws the quarantined mon
    cmds = state.handle_event("a", {"event": "box_to_party", "key": "A:1"})
    # Should be re-quarantined
    assert has_cmd(cmds, "box_mon", "A:1"), "Manual withdrawal should be blocked"
    assert has_cmd(cmds, "hud_show"), "Warning HUD should appear"
    assert "A:1" not in state.party_keys["a"], "Quarantined key must not be in party_keys"


def test_memorial_mon_reboxed_on_box_to_party(tmp_path, monkeypatch):
    """A dead/memorial mon taken from the memorial box must be sent back immediately."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = make_state_with_link(status=LinkStatus.MEMORIAL)
    state.area_states["route_1"] = AreaStatus.LINKED
    # Memorial mons are in the box, not in the party
    state.party_keys["a"].discard("A:1")
    state.party_keys["b"].discard("B:2")
    # Player removes memorial mon from box
    cmds = state.handle_event("a", {"event": "box_to_party", "key": "A:1", "nickname": "PIDGEY"})
    # Should get a memorialize command to send it back
    assert any(c["cmd"] == "memorialize" and c["key"] == "A:1" for c in cmds)
    # Should get a HUD warning
    assert any(c["cmd"] == "hud_show" and "dead" in c["text"].lower() for c in cmds)
    # Key should NOT be in party_keys
    assert "A:1" not in state.party_keys["a"]


def test_dead_mon_reboxed_on_box_to_party(tmp_path, monkeypatch):
    """A dead mon (not yet memorialized) taken from box must also be sent back."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = make_state_with_link(status=LinkStatus.DEAD)
    state.area_states["route_1"] = AreaStatus.LINKED
    state.party_keys["a"].discard("A:1")
    state.party_keys["b"].discard("B:2")
    cmds = state.handle_event("a", {"event": "box_to_party", "key": "A:1"})
    assert any(c["cmd"] == "memorialize" and c["key"] == "A:1" for c in cmds)
    assert "A:1" not in state.party_keys["a"]
    """A quarantined (pending) mon's faint should not propagate — it has no link."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState()
    state.pokeballs_obtained = {"a": True, "b": True}
    # A captures, pending (quarantined)
    state.handle_event("a", {"event": "capture", "key": "A:1",
                              "area_id": "route_1", "level": 5})
    # A's pending mon faints somehow (shouldn't happen, but let's check)
    cmds = state.handle_event("a", {"event": "faint", "key": "A:1"})
    # Should be a noop — no link exists to propagate
    assert not has_cmd(cmds, "force_faint")


# ── paired party sync ─────────────────────────────────────────────────────────

def test_post_link_no_retrieve_if_party_full(tmp_path, monkeypatch):
    """After link forms, if either party is full, both mons stay in box."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState()
    state.pokeballs_obtained = {"a": True, "b": True}
    # A has room, B is full
    state.party_size = {"a": 3, "b": 6}
    state.handle_event("a", {"event": "capture", "key": "A:1",
                              "area_id": "route_1", "level": 5})
    cmds_b = state.handle_event("b", {"event": "capture", "key": "B:2",
                                       "area_id": "route_1", "level": 7})
    assert state.area_states["route_1"] == AreaStatus.LINKED
    # Neither should get party_mon
    assert not has_cmd(cmds_b, "party_mon"), "B party full — no retrieve"
    a_cmds = state.queued_commands["a"]
    assert not any(c["cmd"] == "party_mon" for c in a_cmds), "A also stays in box (sync)"
    # Both get HUD notification
    assert has_cmd(cmds_b, "hud_show")


def test_post_link_retrieve_if_both_have_room(tmp_path, monkeypatch):
    """After link forms, if both parties have room, both get party_mon."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState()
    state.pokeballs_obtained = {"a": True, "b": True}
    state.party_size = {"a": 3, "b": 4}
    state.handle_event("a", {"event": "capture", "key": "A:1",
                              "area_id": "route_1", "level": 5})
    cmds_b = state.handle_event("b", {"event": "capture", "key": "B:2",
                                       "area_id": "route_1", "level": 7})
    assert state.area_states["route_1"] == AreaStatus.LINKED
    assert has_cmd(cmds_b, "party_mon", "B:2")
    assert any(c["cmd"] == "party_mon" and c["key"] == "A:1"
               for c in state.queued_commands["a"])


def test_box_to_party_blocked_if_partner_full(tmp_path, monkeypatch):
    """Withdrawing a linked mon is blocked if partner's party is logically full."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = make_state_with_link()
    # Deposit A's mon
    state.handle_event("a", {"event": "party_to_box", "key": "A:1",
                              "stats": {"level": 10, "maxHP": 40}})
    # B's side auto-deposited by sync
    state.handle_event("b", {"event": "tick"})  # flush box_mon to B
    # Fill B's logical party with 6 other keys (simulates truly full party)
    state.party_keys["b"] = {f"FILL:{i}" for i in range(6)}
    # A tries to withdraw — blocked because B has no room logically
    cmds = state.handle_event("a", {"event": "box_to_party", "key": "A:1"})
    assert has_cmd(cmds, "box_mon", "A:1"), "Withdrawal blocked — re-deposited"
    assert has_cmd(cmds, "hud_show"), "HUD warning shown"
    assert "A:1" not in state.party_keys["a"]


def test_box_to_party_not_blocked_by_stale_party_size_during_swap(tmp_path, monkeypatch):
    """
    When the player deposits a linked mon (A:1) and immediately withdraws a different
    linked mon (A:3) whose partner (B:4) isn't in party, the server should NOT falsely
    block withdrawal because party_size[partner] is stale (still 6 from before the queued
    box_mon for B:2 has been processed by Lua).
    """
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState()
    state.pokeballs_obtained = {"a": True, "b": True}

    # Create two linked pairs: A:1<->B:2, A:3<->B:4
    for a_k, b_k, area in [("A:1", "B:2", "route_1"), ("A:3", "B:4", "route_2")]:
        entry = LinkEntry(
            area_id=area,
            a=MonInfo(key=a_k, level=5),
            b=MonInfo(key=b_k, level=5),
            status=LinkStatus.ALIVE,
        )
        state.links.append(entry)
        state._index_entry(entry)
        state.area_states[area] = AreaStatus.LINKED

    # Both parties have 6 mons: linked ones + filler
    state.party_keys["a"] = {"A:1", "A:3", "FILL_A:1", "FILL_A:2", "FILL_A:3", "FILL_A:4"}
    state.party_keys["b"] = {"B:2", "FILL_B:1", "FILL_B:2", "FILL_B:3", "FILL_B:4", "FILL_B:5"}
    # B:4 is in the box (not in party_keys["b"])
    state.party_size = {"a": 6, "b": 6}

    # A deposits A:1 → server queues box_mon(B:2) for B, removes B:2 from party_keys["b"]
    state.handle_event("a", {"event": "party_to_box", "key": "A:1"})
    assert "B:2" not in state.party_keys["b"]
    assert any(c.get("cmd") == "box_mon" and c.get("key") == "B:2"
               for c in state.queued_commands["b"]), "box_mon(B:2) queued for B"

    # A now withdraws A:3 (B:4 partner is in box, not in party_keys["b"])
    # party_size["b"] is still 6 (stale), but there is 1 pending box_mon in queue
    # so adjusted_party_size = 6-1 = 5; logical_size = max(5, 5) = 5 < 6 → OK
    cmds = state.handle_event("a", {"event": "box_to_party", "key": "A:3"})
    assert not has_cmd(cmds, "box_mon", "A:3"), "A:3 should NOT be re-deposited"
    assert "A:3" in state.party_keys["a"]
    assert has_cmd(state.queued_commands["b"], "party_mon", "B:4"), "party_mon queued for partner B:4"


def test_box_to_party_not_blocked_after_stats_cache_clears_partner(tmp_path, monkeypatch):
    """
    Regression: partner executes a box_mon and sends stats_cache (confirming deposit), but
    party_size is still stale (6) because no tick has arrived yet.  A subsequent
    box_to_party from the other side must NOT be falsely blocked.

    Real-world scenario from the bug report:
      1. B had their linked mon in party.  Some event triggered box_mon for B's mon.
      2. B's Lua executed the deposit and sent stats_cache — party_keys["b"] lost the key
         but party_size["b"] stayed at 6 (stale).
      3. A tried to take their linked mon out of the box (box_to_party).
      4. Server checked: logical_size = max(linked_party_size=5, adjusted_party_size=6) = 6
         → falsely blocked and re-deposited A's mon ("deleted" from the player's perspective).
    """
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState()
    state.pokeballs_obtained = {"a": True, "b": True}

    # Create linked pair A:1 <-> B:2
    entry = LinkEntry(
        area_id="route_1",
        a=MonInfo(key="A:1", level=10),
        b=MonInfo(key="B:2", level=10),
        status=LinkStatus.ALIVE,
    )
    state.links.append(entry)
    state._index_entry(entry)
    state.area_states["route_1"] = AreaStatus.LINKED

    # B has 6 mons in party (B:2 + 5 fillers); A has B:2's partner (A:1) in the box
    state.party_keys["b"] = {"B:2", "FILL_B:1", "FILL_B:2", "FILL_B:3", "FILL_B:4", "FILL_B:5"}
    state.party_keys["a"] = set()  # A:1 is in the box
    state.party_size = {"a": 0, "b": 6}
    state._has_helld.add("b")

    # B's box_mon for B:2 is executed — Lua sends stats_cache (no party_to_box to avoid feedback loop)
    state.handle_event("b", {
        "event": "stats_cache", "key": "B:2",
        "stats": {"level": 10, "maxHP": 40}
    })
    # party_size["b"] must be decremented so it reflects the deposit
    assert state.party_size["b"] == 5, "stats_cache should decrement party_size"
    assert "B:2" not in state.party_keys["b"]

    # A now withdraws their linked mon from the box — should succeed (B's party has room)
    cmds = state.handle_event("a", {"event": "box_to_party", "key": "A:1"})
    assert not has_cmd(cmds, "box_mon", "A:1"), \
        "A:1 must NOT be re-deposited — B's party has room after stats_cache decrement"
    assert "A:1" in state.party_keys["a"]
    # Server should also pull B:2 back out for B
    assert has_cmd(state.queued_commands["b"], "party_mon", "B:2"), \
        "party_mon queued for B:2 so the pair reunites in party"


def test_box_to_party_not_blocked_by_stale_party_size_after_manual_deposit(tmp_path, monkeypatch):
    """
    Regression: player manually deposits a mon (party_to_box); party_size stays stale
    until the next tick.  If the partner immediately withdraws their linked mon the server
    must NOT falsely block using the stale party_size.
    """
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState()
    state.pokeballs_obtained = {"a": True, "b": True}

    # Two linked pairs: A:1<->B:2, A:3<->B:4
    for a_k, b_k, area in [("A:1", "B:2", "route_1"), ("A:3", "B:4", "route_2")]:
        e = LinkEntry(
            area_id=area,
            a=MonInfo(key=a_k, level=5),
            b=MonInfo(key=b_k, level=5),
            status=LinkStatus.ALIVE,
        )
        state.links.append(e)
        state._index_entry(e)
        state.area_states[area] = AreaStatus.LINKED

    # A has A:1 and A:3 in party plus 4 fillers (6 total); B has 6 mons including B:2 and B:4
    state.party_keys["a"] = {"A:1", "A:3", "FA:1", "FA:2", "FA:3", "FA:4"}
    state.party_keys["b"] = {"B:2", "B:4", "FB:1", "FB:2", "FB:3", "FB:4"}
    state.party_size = {"a": 6, "b": 6}
    state._has_helld.update({"a", "b"})

    # A manually deposits A:1 → server queues box_mon(B:2) for B, party_size["a"] decremented
    state.handle_event("a", {"event": "party_to_box", "key": "A:1",
                              "stats": {"level": 5, "maxHP": 20}})
    assert state.party_size["a"] == 5, "party_to_box should decrement party_size"

    # Now B tries to withdraw B:4 (linked to A:3, which is still in A's party).
    # B:4 is in the box (not in party_keys["b"]).
    # Partner (A) just deposited A:1 — party_size["a"] is now 5, so logical_size = 5 < 6 → allow.
    cmds = state.handle_event("b", {"event": "box_to_party", "key": "B:4"})
    assert not has_cmd(cmds, "box_mon", "B:4"), \
        "B:4 must NOT be re-deposited — A's party has room after manual deposit decremented party_size"
    assert "B:4" in state.party_keys["b"]


def test_sync_retrieve_failed_reboxes_partner(tmp_path, monkeypatch):
    """When one side fails to retrieve, the partner who succeeded is re-boxed."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = make_state_with_link()
    # Both in party initially. Deposit A → B auto-deposited.
    state.handle_event("a", {"event": "party_to_box", "key": "A:1",
                              "stats": {"level": 10, "maxHP": 40}})
    state.handle_event("b", {"event": "tick"})  # flush box_mon
    # A withdraws → server queues party_mon for B
    state.handle_event("a", {"event": "box_to_party", "key": "A:1"})
    assert "A:1" in state.party_keys["a"]
    # B fails to retrieve (party full)
    state.handle_event("b", {"event": "sync_retrieve_failed", "key": "B:2"})
    # A should get re-boxed to maintain sync
    cmds_a = state.queued_commands["a"]
    assert any(c["cmd"] == "box_mon" and c["key"] == "A:1" for c in cmds_a), \
        "A must be re-boxed when B's retrieve fails"
    assert "A:1" not in state.party_keys["a"]



    """Box capture with stats should cache them in mon_stats for later party_mon."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState()
    state.pokeballs_obtained = {"a": True, "b": True}
    stats = {"level": 12, "maxHP": 44, "attack": 20, "defense": 18, "speed": 22, "spAtk": 15, "spDef": 16}
    state.handle_event("a", {"event": "capture", "key": "A:1", "area_id": "route_1",
                              "level": 12, "in_box": True, "stats": stats})
    assert state.mon_stats["a"].get("A:1") == stats


def test_box_capture_no_stats_still_links(tmp_path, monkeypatch):
    """Box capture without stats (enemy data unavailable) should still form the link."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState()
    state.pokeballs_obtained = {"a": True, "b": True}
    state.handle_event("a", {"event": "capture", "key": "A:1", "area_id": "route_1",
                              "level": 5, "in_box": True})
    state.handle_event("b", {"event": "capture", "key": "B:2", "area_id": "route_1", "level": 7})
    assert state.area_states["route_1"] == AreaStatus.LINKED
    assert "A:1" not in state.mon_stats["a"]  # No stats to cache


def test_sync_retrieve_failed_removes_from_party_keys(tmp_path, monkeypatch):
    """sync_retrieve_failed should ensure the key is NOT in party_keys."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = make_state_with_link()
    state.party_keys["b"].add("B:2")  # Simulate incorrect state
    state.handle_event("b", {"event": "sync_retrieve_failed", "key": "B:2"})
    assert "B:2" not in state.party_keys["b"]


def test_box_to_party_no_premature_partner_party_keys(tmp_path, monkeypatch):
    """box_to_party should NOT add partner's key to party_keys before retrieval is confirmed."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = make_state_with_link()
    state.party_keys["a"].discard("A:1")
    state.party_keys["b"].discard("B:2")
    state.mon_stats["b"]["B:2"] = {"level": 12, "maxHP": 40}

    state.handle_event("a", {"event": "box_to_party", "key": "A:1"})
    # B:2 should NOT be in party_keys — awaiting sync_retrieve_done.
    assert "B:2" not in state.party_keys["b"]
    # But A:1 should be (A actually retrieved their mon).
    assert "A:1" in state.party_keys["a"]
    # After partner confirms retrieval:
    state.handle_event("b", {"event": "sync_retrieve_done", "key": "B:2"})
    assert "B:2" in state.party_keys["b"]


def test_pc_swap_party_to_box_before_box_to_party(tmp_path, monkeypatch):
    """
    Regression test for "Move Pokemon" swap: when the Lua client fires
    party_to_box (deposit) BEFORE box_to_party (retrieval) in the same
    frame batch, the server must allow the withdrawal even when the
    partner's party_size is stale at 6.

    Prior to the fix, box_to_party was processed first (wrong Lua ordering),
    so the server saw a full partner party and blocked the swap.
    """
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState()
    state.pokeballs_obtained = {"a": True, "b": True}

    # Three linked pairs: A:1<->B:2, A:3<->B:4, A:5<->B:6
    for a_k, b_k, area in [("A:1", "B:2", "route_1"),
                            ("A:3", "B:4", "route_2"),
                            ("A:5", "B:6", "route_3")]:
        entry = LinkEntry(
            area_id=area,
            a=MonInfo(key=a_k, level=5),
            b=MonInfo(key=b_k, level=5),
            status=LinkStatus.ALIVE,
        )
        state.links.append(entry)
        state._index_entry(entry)
        state.area_states[area] = AreaStatus.LINKED

    # Both players have full parties of 6 mons.
    # A: A:1, A:3, + 4 fillers; A:5 is in box.
    # B: B:2, B:4, + 4 fillers; B:6 is in box.
    state.party_keys["a"] = {"A:1", "A:3", "FILL_A:1", "FILL_A:2", "FILL_A:3", "FILL_A:4"}
    state.party_keys["b"] = {"B:2", "B:4", "FILL_B:1", "FILL_B:2", "FILL_B:3", "FILL_B:4"}
    state.party_size = {"a": 6, "b": 6}
    state.mon_stats["b"]["B:6"] = {"level": 5, "maxHP": 20}

    # Simulate the CORRECT event ordering (deposit first, then retrieve):
    # A deposits A:1 → server queues box_mon(B:2) for B
    state.handle_event("a", {"event": "party_to_box", "key": "A:1",
                              "stats": {"level": 5, "maxHP": 20}})
    assert "A:1" not in state.party_keys["a"]
    assert any(c.get("cmd") == "box_mon" and c.get("key") == "B:2"
               for c in state.queued_commands["b"]), "box_mon(B:2) queued"

    # A retrieves A:5 → partner B:6 needs to join B's party.
    # B's party_size is still 6 (stale), but 1 pending box_mon exists,
    # so adjusted = 6-1 = 5, logical = max(linked=5, 5) = 5 < 6 → allowed
    cmds = state.handle_event("a", {"event": "box_to_party", "key": "A:5"})
    assert not has_cmd(cmds, "box_mon", "A:5"), \
        "A:5 should NOT be re-deposited (swap should work)"
    assert "A:5" in state.party_keys["a"]


def test_pc_swap_wrong_order_box_to_party_first_fails(tmp_path, monkeypatch):
    """
    Demonstrates the bug scenario: if box_to_party arrives BEFORE party_to_box
    (the old wrong Lua ordering), the server blocks the swap because it sees
    the partner at full capacity with no pending box_mon commands.

    This test documents the behavior and confirms why event ordering matters.
    """
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState()
    state.pokeballs_obtained = {"a": True, "b": True}

    # Two linked pairs: A:1<->B:2 (in party), A:5<->B:6 (A:5 in box, B:6 in box)
    for a_k, b_k, area in [("A:1", "B:2", "route_1"), ("A:5", "B:6", "route_3")]:
        entry = LinkEntry(
            area_id=area,
            a=MonInfo(key=a_k, level=5),
            b=MonInfo(key=b_k, level=5),
            status=LinkStatus.ALIVE,
        )
        state.links.append(entry)
        state._index_entry(entry)
        state.area_states[area] = AreaStatus.LINKED

    state.party_keys["a"] = {"A:1", "FILL_A:1", "FILL_A:2", "FILL_A:3", "FILL_A:4", "FILL_A:5"}
    state.party_keys["b"] = {"B:2", "FILL_B:1", "FILL_B:2", "FILL_B:3", "FILL_B:4", "FILL_B:5"}
    state.party_size = {"a": 6, "b": 6}
    state.mon_stats["b"]["B:6"] = {"level": 5, "maxHP": 20}

    # WRONG ORDER: box_to_party arrives first (old bug)
    # Server sees B at 6/6 with 0 pending box_mons → blocks
    cmds = state.handle_event("a", {"event": "box_to_party", "key": "A:5"})
    assert has_cmd(cmds, "box_mon", "A:5"), \
        "Expected block: partner full (no prior deposit)"
    assert "A:5" not in state.party_keys["a"]


def test_no_catch_creates_dead_zone(tmp_path, monkeypatch):
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState(retry_same_species=False)
    state.handle_event("a", {"event": "area_enter", "area_id": "route_1"})  # A enters → PENDING_B
    state.handle_event("b", {"event": "area_enter", "area_id": "route_1"})  # B enters → PENDING_BOTH
    state.handle_event("b", {"event": "no_catch", "area_id": "route_1"})
    assert state.area_states["route_1"] == AreaStatus.DEAD_ZONE


def test_no_catch_dead_zone_queues_hud_banner_for_both(tmp_path, monkeypatch):
    """Dead-zone creation must queue both SE_FAILURE and a HUD banner naming the area for both players."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState(retry_same_species=False)
    cmds_b = state.handle_event("b", {"event": "no_catch", "area_id": "route_1"})
    cmds_a = state.handle_event("a", {"event": "tick"})

    def _has_dz_hud(cmds):
        # Dead-zone is a momentous overworld event -> msgbox (native box when patched,
        # else HUD overlay). Text names the area as a dead zone.
        return any(
            c.get("cmd") == "msgbox" and "dead zone" in c.get("text", "").lower()
            for c in cmds
        )
    def _has_failure_se(cmds):
        return any(c.get("cmd") == "play_sound" and c.get("sound") == 26 for c in cmds)

    assert _has_failure_se(cmds_b), "Triggering player must hear SE_FAILURE"
    assert _has_failure_se(cmds_a), "Partner must hear SE_FAILURE"
    assert _has_dz_hud(cmds_b), "Triggering player must see dead-zone HUD banner"
    assert _has_dz_hud(cmds_a), "Partner must see dead-zone HUD banner"


def test_no_catch_retires_partner_pending_capture(tmp_path, monkeypatch):
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState(retry_same_species=False)

    # A captures first → records pending capture + sets pokeballs_obtained["a"]
    state.handle_event("a", {"event": "capture", "key": "A:1", "area_id": "route_1", "level": 5})
    # B enters the area
    state.handle_event("b", {"event": "area_enter", "area_id": "route_1"})

    # B fails → A's capture must be retired
    state.handle_event("a", {"event": "tick"})  # flush before
    state.handle_event("b", {"event": "no_catch", "area_id": "route_1"})

    # force_faint should be queued for A
    cmds_a2 = state.handle_event("a", {"event": "tick"})
    assert has_cmd(cmds_a2, "force_faint", "A:1")
    assert state.area_states["route_1"] == AreaStatus.DEAD_ZONE
    assert state.links[0].status == LinkStatus.DEAD


def test_no_catch_on_already_linked_area_is_noop(tmp_path, monkeypatch):
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = make_state_with_link()
    state.handle_event("a", {"event": "no_catch", "area_id": "route_1"})
    assert state.links[0].status == LinkStatus.ALIVE
    assert state.area_states["route_1"] == AreaStatus.LINKED


def test_no_catch_on_dead_zone_is_noop(tmp_path, monkeypatch):
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState()
    state.area_states["route_1"] = AreaStatus.DEAD_ZONE
    state.handle_event("a", {"event": "no_catch", "area_id": "route_1"})
    assert state.area_states["route_1"] == AreaStatus.DEAD_ZONE  # unchanged


def test_no_catch_creates_dead_zone_link_entry_no_captures(tmp_path, monkeypatch):
    """no_catch with no pending captures on either side should still create a DEAD LinkEntry."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState(retry_same_species=False)
    state.handle_event("a", {"event": "no_catch", "area_id": "route_1"})
    assert state.area_states["route_1"] == AreaStatus.DEAD_ZONE
    assert len(state.links) == 1
    entry = state.links[0]
    assert entry.area_id == "route_1"
    assert entry.status == LinkStatus.DEAD
    assert entry.a is None  # A had no catch
    assert entry.b is None  # B had no catch (none pending)


def test_no_catch_records_encounter_species(tmp_path, monkeypatch):
    """no_catch with species_id/level should populate encounter_a/b on the LinkEntry."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState(retry_same_species=False)
    state.handle_event("a", {"event": "no_catch", "area_id": "route_1",
                              "species_id": 19, "level": 4})
    entry = state.links[0]
    assert entry.a is None
    assert entry.encounter_a is not None
    assert entry.encounter_a.species == 19
    assert entry.encounter_a.level == 4
    assert entry.encounter_a.key == ""   # sentinel: not a real catch
    assert entry.encounter_b is None     # B has no encounter data yet


def test_dead_zone_capture_not_recorded_in_link_entry(tmp_path, monkeypatch):
    """A capture in an already-dead zone should be force-fainted/memorialized but NOT written
    into the dead zone entry, so the original 'no catch' display is preserved."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState(retry_same_species=False)
    state.pokeballs_obtained = {"a": True, "b": True}
    # A has no catch → dead zone, LinkEntry(a=None, b=None)
    state.handle_event("a", {"event": "no_catch", "area_id": "route_1"})
    assert len(state.links) == 1
    assert state.links[0].a is None

    # B then catches something in the dead zone
    cmds_b = state.handle_event("b", {"event": "capture", "key": "B:2",
                                       "area_id": "route_1", "level": 5,
                                       "nickname": "PIDGEY", "species_id": 16})
    assert has_cmd(cmds_b, "force_faint", "B:2"), "Dead-zone capture must be force-fainted"
    assert has_cmd(cmds_b, "memorialize", "B:2"), "Dead-zone capture must be queued for memorial"
    # The entry must NOT be updated — the original "no catch" display should be preserved
    assert state.links[0].b is None, "Dead-zone catch must not overwrite the missed-catch record"


def test_illegal_capture_in_linked_area_memorialized(tmp_path, monkeypatch):
    """A second capture in an already-linked area must be force-fainted and memorialized."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState()
    state.pokeballs_obtained = {"a": True, "b": True}
    # Establish a normal link on route_1
    state.handle_event("a", {"event": "capture", "key": "A:1", "area_id": "route_1", "level": 5})
    state.handle_event("b", {"event": "capture", "key": "B:1", "area_id": "route_1", "level": 5})
    assert state.area_states["route_1"].value == "linked"
    # A catches a second mon in the same area — illegal
    cmds = state.handle_event("a", {"event": "capture", "key": "A:2", "area_id": "route_1", "level": 7})
    assert has_cmd(cmds, "force_faint", "A:2"), "Illegal capture must be force-fainted"
    assert has_cmd(cmds, "memorialize", "A:2"), "Illegal capture must be queued for memorial"


# ── whiteout ─────────────────────────────────────────────────────────────────

def test_whiteout_force_faints_all_party_partners(tmp_path, monkeypatch):
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState()
    state.pokeballs_obtained = {"a": True, "b": True}     # a whiteout before the first ball retires nothing

    for i in range(3):
        a_key = f"A:{i}"
        b_key = f"B:{i}"
        entry = LinkEntry("route_" + str(i), MonInfo(a_key), MonInfo(b_key), LinkStatus.ALIVE)
        state.links.append(entry)
        state._index_entry(entry)
        state.party_keys["a"].add(a_key)
        state.party_keys["b"].add(b_key)

    state.handle_event("a", {"event": "whiteout"})
    cmds_b = state.handle_event("b", {"event": "tick"})

    for i in range(3):
        assert has_cmd(cmds_b, "force_faint", f"B:{i}"), f"B:{i} should be force-fainted"
    assert all(e.status == LinkStatus.DEAD for e in state.links)


def test_whiteout_skips_boxed_mons(tmp_path, monkeypatch):
    """Mons not in party_keys should NOT have their partners force-fainted."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState()

    entry = LinkEntry("route_1", MonInfo("A:1"), MonInfo("B:1"), LinkStatus.ALIVE)
    state.links.append(entry)
    state._index_entry(entry)
    # A:1 is NOT in party_keys["a"] — it is boxed
    state.party_keys["b"].add("B:1")

    state.handle_event("a", {"event": "whiteout"})
    cmds_b = state.handle_event("b", {"event": "tick"})
    assert noop_only(cmds_b), "Boxed mon's partner should NOT be force-fainted on whiteout"
    assert state.links[0].status == LinkStatus.ALIVE


# ── whiteout auto-rebuild ─────────────────────────────────────────────────────

def _add_link(state, a_key, b_key, area="route_x", status=LinkStatus.ALIVE,
              a_in_party=False, b_in_party=False):
    """Append a LinkEntry and update party_keys. Helper for rebuild tests."""
    entry = LinkEntry(area, MonInfo(a_key, level=10), MonInfo(b_key, level=10), status)
    state.links.append(entry)
    state._index_entry(entry)
    if a_in_party:
        state.party_keys["a"].add(a_key)
    if b_in_party:
        state.party_keys["b"].add(b_key)
    return entry


def test_whiteout_rebuilds_from_alive_boxed_pairs(tmp_path, monkeypatch):
    """After a whiteout, the server queues party_mon for both halves of any
    alive linked pair whose halves were both still in the PC."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState()
    state.pokeballs_obtained = {"a": True, "b": True}
    state.party_size = {"a": 1, "b": 0}

    # A:party has its half in party; partner B:party half is in party too. This
    # pair will die on whiteout.
    _add_link(state, "A:party", "B:party", area="route_1",
              a_in_party=True, b_in_party=True)
    # Alive boxed pair — eligible for rebuild.
    _add_link(state, "A:box", "B:box", area="route_2",
              a_in_party=False, b_in_party=False)

    cmds_a = state.handle_event("a", {"event": "whiteout"})

    assert has_cmd(cmds_a, "party_mon", "A:box"), "A should get party_mon for boxed half"
    assert has_cmd(cmds_a, "rebuild_start"), "A should get rebuild_start banner"
    cmds_b = state.handle_event("b", {"event": "tick"})
    assert has_cmd(cmds_b, "party_mon", "B:box"), "B should get matching party_mon"
    rb = state.rebuild_pending["a"]
    assert rb is not None
    assert "A:box" in rb["queued_keys"]
    assert "B:box" in rb["queued_partner_keys"]


def test_whiteout_skips_rebuild_when_no_alive_mons(tmp_path, monkeypatch):
    """With every linked pair in party, no rebuild is armed and no
    rebuild_start command is queued."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState()
    state.pokeballs_obtained = {"a": True, "b": True}
    _add_link(state, "A:1", "B:1", area="route_1",
              a_in_party=True, b_in_party=True)

    cmds_a = state.handle_event("a", {"event": "whiteout"})
    assert not has_cmd(cmds_a, "rebuild_start")
    assert not has_cmd(cmds_a, "party_mon")
    assert state.rebuild_pending["a"] is None


def test_whiteout_rebuild_caps_at_partner_room(tmp_path, monkeypatch):
    """If partner already has 5 mons in their party, rebuild can only restore
    one paired pick (the partner half fits in slot 6 and stops there)."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState()
    state.pokeballs_obtained = {"a": True, "b": True}
    state.party_size = {"a": 1, "b": 5}

    # Partner already has 5 alive non-bonus mons in their party.
    for i in range(5):
        _add_link(state, f"A:filler{i}", f"B:filler{i}", area=f"route_filler_{i}",
                  a_in_party=False, b_in_party=True)
    # A's dying pair.
    _add_link(state, "A:dead", "B:dead", area="route_d",
              a_in_party=True, b_in_party=False)
    # Three boxed pairs that A could pull back — but B has only 1 slot free.
    for i in range(3):
        _add_link(state, f"A:box{i}", f"B:box{i}", area=f"route_box_{i}",
                  a_in_party=False, b_in_party=False)

    state.handle_event("a", {"event": "whiteout"})

    rb = state.rebuild_pending["a"]
    assert rb is not None
    # Only one paired restore (the oldest boxed pair).
    assert rb["queued_keys"] == ["A:box0"]
    assert rb["queued_partner_keys"] == ["B:box0"]


def test_whiteout_does_not_pull_pending_captures(tmp_path, monkeypatch):
    """Pending unlinked captures must stay quarantined in the box — they are
    NOT a rebuild source."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState()
    state.pokeballs_obtained = {"a": True, "b": True}
    # A's only linked pair is in party and will die.
    _add_link(state, "A:dying", "B:dying", area="route_1",
              a_in_party=True, b_in_party=True)
    # A has a pending unlinked capture in the box.
    state.pending_captures["route_2"] = {"a": MonInfo(key="A:pending", level=8)}

    cmds_a = state.handle_event("a", {"event": "whiteout"})
    assert not has_cmd(cmds_a, "rebuild_start")
    assert not has_cmd(cmds_a, "party_mon", "A:pending")
    assert state.rebuild_pending["a"] is None
    # The pending capture is still quarantined (still in pending_captures).
    assert state.pending_captures["route_2"]["a"].key == "A:pending"


def test_sync_retrieve_done_clears_rebuild_pending(tmp_path, monkeypatch):
    """When all queued rebuild keys resolve via sync_retrieve_done, the server
    clears rebuild_pending and queues rebuild_done."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState()
    state.pokeballs_obtained = {"a": True, "b": True}
    _add_link(state, "A:dying", "B:dying", area="route_1",
              a_in_party=True, b_in_party=True)
    _add_link(state, "A:box", "B:box", area="route_2",
              a_in_party=False, b_in_party=False)

    state.handle_event("a", {"event": "whiteout"})
    assert state.rebuild_pending["a"] is not None
    # Drain the whiteout's commands from the queue.
    state.handle_event("a", {"event": "tick"})

    cmds = state.handle_event("a", {"event": "sync_retrieve_done", "key": "A:box"})
    assert has_cmd(cmds, "rebuild_done")
    assert state.rebuild_pending["a"] is None


def test_sync_retrieve_failed_during_rebuild_partial(tmp_path, monkeypatch):
    """If a sync_retrieve_failed arrives, the failed key is dropped from
    queued_keys and rebuild_done only fires when all queued keys have
    resolved (either confirmed or failed)."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState()
    state.pokeballs_obtained = {"a": True, "b": True}
    _add_link(state, "A:dying", "B:dying", area="route_d",
              a_in_party=True, b_in_party=True)
    _add_link(state, "A:box1", "B:box1", area="route_1",
              a_in_party=False, b_in_party=False)
    _add_link(state, "A:box2", "B:box2", area="route_2",
              a_in_party=False, b_in_party=False)

    state.handle_event("a", {"event": "whiteout"})
    state.handle_event("a", {"event": "tick"})  # drain initial command queue

    cmds = state.handle_event("a", {"event": "sync_retrieve_failed", "key": "A:box1"})
    assert not has_cmd(cmds, "rebuild_done"), "still waiting on A:box2"
    assert state.rebuild_pending["a"] is not None

    cmds = state.handle_event("a", {"event": "sync_retrieve_done", "key": "A:box2"})
    assert has_cmd(cmds, "rebuild_done"), "all queued keys resolved"
    assert state.rebuild_pending["a"] is None


def test_whiteout_rebuild_persists_across_reload(tmp_path, monkeypatch):
    """rebuild_pending must round-trip through _save/load, and a hello after
    reconnect should re-queue outstanding party_mon plus a fresh rebuild_start."""
    links_path = str(tmp_path / "links.json")
    monkeypatch.setattr("server.state.LINKS_PATH", links_path)
    state = SoulLinkState()
    state.pokeballs_obtained = {"a": True, "b": True}
    state.area_states["route_1"] = AreaStatus.LINKED
    state.area_states["route_2"] = AreaStatus.LINKED
    _add_link(state, "A:dying", "B:dying", area="route_1",
              a_in_party=True, b_in_party=True)
    _add_link(state, "A:box", "B:box", area="route_2",
              a_in_party=False, b_in_party=False)
    state.handle_event("a", {"event": "whiteout"})
    state._save()

    loaded = SoulLinkState.load()
    assert loaded.rebuild_pending["a"] is not None
    assert "A:box" in loaded.rebuild_pending["a"]["queued_keys"]

    # Hello with empty party (A is at the PMC fresh from whiteout); rebuild
    # should re-arm and re-queue party_mon + rebuild_start.
    cmds = loaded.handle_event("a", {
        "event": "hello", "rom_type": "firered", "party": [], "has_pokeballs": True,
    })
    assert has_cmd(cmds, "party_mon", "A:box")
    assert has_cmd(cmds, "rebuild_start")


def test_box_to_party_during_rebuild_does_not_abort(tmp_path, monkeypatch):
    """Manual withdrawal of an unrelated linked mon during an active rebuild
    must NOT abort the rebuild — rebuild_pending stays set."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState()
    state.pokeballs_obtained = {"a": True, "b": True}
    state.party_size = {"a": 0, "b": 0}
    state.area_states["route_1"] = AreaStatus.LINKED
    state.area_states["route_other"] = AreaStatus.LINKED
    state.area_states["route_box"] = AreaStatus.LINKED
    _add_link(state, "A:dying", "B:dying", area="route_1",
              a_in_party=True, b_in_party=True)
    _add_link(state, "A:box", "B:box", area="route_box",
              a_in_party=False, b_in_party=False)
    _add_link(state, "A:other", "B:other", area="route_other",
              a_in_party=False, b_in_party=False)

    state.handle_event("a", {"event": "whiteout"})
    assert state.rebuild_pending["a"] is not None

    # Withdraw a different alive linked mon manually.
    state.handle_event("a", {"event": "box_to_party", "key": "A:other"})
    assert state.rebuild_pending["a"] is not None, "rebuild not affected by unrelated withdrawal"


def test_simultaneous_whiteouts_independent_rebuilds(tmp_path, monkeypatch):
    """Both players whiting out within the same tick should each enter their
    own rebuild_pending without crosstalk."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState()
    state.pokeballs_obtained = {"a": True, "b": True}
    state.party_size = {"a": 1, "b": 1}
    # Both players have one party-side pair that dies, and each has their own
    # alive boxed pair. (In Soul Link rules, both halves of a pair are
    # co-located, so a boxed pair survives both whiteouts.)
    _add_link(state, "A:die1", "B:die1", area="route_1",
              a_in_party=True, b_in_party=True)
    _add_link(state, "A:save", "B:save", area="route_2",
              a_in_party=False, b_in_party=False)

    state.handle_event("a", {"event": "whiteout"})
    state.handle_event("b", {"event": "whiteout"})

    # Only one alive boxed pair exists shared between both — after A whites out,
    # A's plan picks it; after B whites out, B's plan also picks it (because
    # both halves are STILL boxed — A's party_mon hasn't drained yet, server
    # has only queued the command, not confirmed). Both rebuilds are armed.
    assert state.rebuild_pending["a"] is not None
    assert state.rebuild_pending["b"] is not None
    assert state.rebuild_pending["a"]["queued_keys"] == ["A:save"]
    assert state.rebuild_pending["b"]["queued_keys"] == ["B:save"]


def test_alive_pc_mons_excludes_pairs_with_partner_in_party(tmp_path, monkeypatch):
    """A pair where the partner's half is in the partner's party is NOT a
    co-located boxed pair and is therefore ineligible for rebuild."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState()
    state.pokeballs_obtained = {"a": True, "b": True}
    # A's half is in box; B's half is in B's party — NOT co-located.
    _add_link(state, "A:box", "B:in_party", area="route_split",
              a_in_party=False, b_in_party=True)
    eligible = state._alive_pc_mons("a")
    assert eligible == []


def test_whiteout_fires_game_over_when_no_rebuild_possible(tmp_path, monkeypatch):
    """If a whiteout leaves no alive boxed pairs, fire game_over right away —
    the player can't continue without a party, even if pending captures
    exist."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState()
    state.pokeballs_obtained = {"a": True, "b": True}
    _add_link(state, "A:1", "B:1", area="route_1",
              a_in_party=True, b_in_party=True)
    # A pending capture exists — would normally keep run alive, but the
    # whited-out player has no rebuild candidates, so game_over fires.
    state.pending_captures["route_2"] = {"a": MonInfo(key="A:pending")}

    cmds_a = state.handle_event("a", {"event": "whiteout"})
    assert has_cmd(cmds_a, "game_over")
    assert state.run_over is True
    cmds_b = state.handle_event("b", {"event": "tick"})
    assert has_cmd(cmds_b, "game_over")


def test_whiteout_queues_party_mon_before_memorialize(tmp_path, monkeypatch):
    """Regression: rebuild's party_mon MUST appear before memorialize in the
    player's queue so the Lua's last-party-mon block (q_count<=1) lifts
    automatically when the new mon lands. Without this ordering, memorialize
    sits at the head of pending_sync_cmds and deadlocks the queue."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState()
    state.pokeballs_obtained = {"a": True, "b": True}
    _add_link(state, "A:dying", "B:dying", area="route_1",
              a_in_party=True, b_in_party=True)
    _add_link(state, "A:box", "B:box", area="route_2",
              a_in_party=False, b_in_party=False)

    cmds_a = state.handle_event("a", {"event": "whiteout"})
    party_mon_idx = next(
        (i for i, c in enumerate(cmds_a) if c.get("cmd") == "party_mon" and c.get("key") == "A:box"),
        None,
    )
    memorialize_idx = next(
        (i for i, c in enumerate(cmds_a) if c.get("cmd") == "memorialize" and c.get("key") == "A:dying"),
        None,
    )
    assert party_mon_idx is not None and memorialize_idx is not None
    assert party_mon_idx < memorialize_idx, (
        f"party_mon must drain before memorialize "
        f"(party_mon @ {party_mon_idx}, memorialize @ {memorialize_idx})"
    )


# ── duplicate-seq and noop handling ──────────────────────────────────────────

def test_tick_and_unknown_events_return_noop(tmp_path, monkeypatch):
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState()
    assert noop_only(state.handle_event("a", {"event": "tick"}))
    assert noop_only(state.handle_event("b", {"event": "safe"}))
    assert noop_only(state.handle_event("a", {"event": "unknown_event"}))


# ── reconcile on hello ────────────────────────────────────────────────────────

def test_hello_reconcile_fainted_in_party(tmp_path, monkeypatch):
    """A linked mon found in party with hp=0 on hello should trigger force_faint for partner."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = make_state_with_link()

    state.handle_event("a", {
        "event": "hello",
        "area_id": "route_1",
        "rom_type": "firered",
        "party": [{"key": "A:1", "hp": 0, "maxHP": 45, "level": 10}],
    })
    cmds_b = state.handle_event("b", {"event": "tick"})
    assert has_cmd(cmds_b, "force_faint", "B:2")


def test_wild_whiteout_is_ignored_when_rule_enabled(tmp_path, monkeypatch):
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = make_state_with_link()
    state.ignore_wild_faints = True

    cmds_a = state.handle_event("a", {"event": "whiteout", "_is_wild_battle": True})
    cmds_b = state.handle_event("b", {"event": "tick"})

    assert noop_only(cmds_a) and noop_only(cmds_b)
    assert state.links[0].status == LinkStatus.ALIVE
    assert state.rebuild_pending["a"] is None


def test_hello_reconcile_missing_mon_is_not_treated_as_dead(tmp_path, monkeypatch):
    """A linked mon absent from the party snapshot is NOT treated as fainted (may be boxed)."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = make_state_with_link()

    state.handle_event("a", {
        "event": "hello",
        "area_id": "route_1",
        "rom_type": "firered",
        "party": [],  # party is empty — mon is boxed, not dead
    })
    cmds_b = state.handle_event("b", {"event": "tick"})
    assert noop_only(cmds_b), "Missing-from-party mon should not trigger force_faint"
    assert state.links[0].status == LinkStatus.ALIVE


# ── persistence round-trip ────────────────────────────────────────────────────

def test_save_and_load_roundtrip(tmp_path, monkeypatch):
    links_path = str(tmp_path / "links.json")
    monkeypatch.setattr("server.state.LINKS_PATH", links_path)

    state = make_state_with_link()
    state._save()

    monkeypatch.setattr("server.state.LINKS_PATH", links_path)
    loaded = SoulLinkState.load()

    assert len(loaded.links) == 1
    assert loaded.links[0].a.key == "A:1"
    assert loaded.links[0].b.key == "B:2"
    assert loaded.links[0].status == LinkStatus.ALIVE
    assert loaded.area_states.get("route_1") == AreaStatus.LINKED
    assert loaded._key_index.get("A:1") is not None
    assert loaded._key_index.get("B:2") is not None


# ── party / box sync ──────────────────────────────────────────────────────────

def test_party_to_box_queues_pending_sync_for_partner(tmp_path, monkeypatch):
    """When A boxes a linked mon, B should receive a box_mon command to auto-deposit."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = make_state_with_link()  # A:1 in party, B:2 in party

    cmds_a = state.handle_event("a", {"event": "party_to_box", "key": "A:1",
                                       "stats": {"level": 10, "maxHP": 30}})
    assert noop_only(cmds_a), "A's response should be noop"

    cmds_b = state.handle_event("b", {"event": "tick"})
    assert has_cmd(cmds_b, "box_mon"), "B should receive box_mon to auto-deposit partner"
    assert any(c["key"] == "B:2" for c in cmds_b if c.get("cmd") == "box_mon")
    assert "A:1" not in state.party_keys["a"]
    assert "B:2" not in state.party_keys["b"]


def test_party_to_box_unlinked_mon_is_noop(tmp_path, monkeypatch):
    """Boxing an unlinked mon should not produce a pending_sync."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState()
    state.party_keys["a"].add("A:99")
    cmds = state.handle_event("a", {"event": "party_to_box", "key": "A:99"})
    assert noop_only(cmds)


def test_party_to_box_partner_already_boxed_no_sync(tmp_path, monkeypatch):
    """If partner is connected (has helld) and their mon is already boxed, no sync needed."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = make_state_with_link()
    # Simulate B having helld (so _has_helld knows about them)
    state._has_helld.add("b")
    state.party_keys["b"].discard("B:2")  # B:2 already boxed

    state.handle_event("a", {"event": "party_to_box", "key": "A:1"})
    cmds_b = state.handle_event("b", {"event": "tick"})
    assert noop_only(cmds_b), "Partner connected and already boxed — no sync needed"


def test_party_to_box_partner_not_yet_connected_queues_sync(tmp_path, monkeypatch):
    """First deposit of a session: partner hasn't sent hello yet (_has_helld empty).
    box_mon must still be queued so the command is delivered when partner connects."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = make_state_with_link()
    # make_state_with_link does not add to _has_helld — B hasn't helld yet

    state.handle_event("a", {"event": "party_to_box", "key": "A:1"})
    cmds_b = state.handle_event("b", {"event": "tick"})
    assert has_cmd(cmds_b, "box_mon"), "Partner not yet connected — box_mon must be queued"
    assert any(c["key"] == "B:2" for c in cmds_b if c.get("cmd") == "box_mon")


def test_box_to_party_queues_pending_sync_for_partner(tmp_path, monkeypatch):
    """When A retrieves a linked mon from box, B should receive a party_mon command."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = make_state_with_link()
    # Both boxed; pre-populate stats cache as if B:2 was deposited earlier
    state.party_keys["a"].discard("A:1")
    state.party_keys["b"].discard("B:2")
    state.mon_stats["b"]["B:2"] = {"level": 12, "maxHP": 40, "attack": 25}

    cmds_a = state.handle_event("a", {"event": "box_to_party", "key": "A:1"})
    assert noop_only(cmds_a)

    cmds_b = state.handle_event("b", {"event": "tick"})
    assert has_cmd(cmds_b, "party_mon"), "B should receive party_mon to auto-retrieve partner"
    pm = next(c for c in cmds_b if c.get("cmd") == "party_mon")
    assert pm["key"] == "B:2"
    assert pm.get("stats", {}).get("level") == 12, "Cached stats should be echoed"
    assert "A:1" in state.party_keys["a"]
    # B:2 should NOT be in party_keys yet — only added on sync_retrieve_done confirmation.
    assert "B:2" not in state.party_keys["b"]

    # Simulate Lua confirming the retrieval succeeded.
    state.handle_event("b", {"event": "sync_retrieve_done", "key": "B:2"})
    assert "B:2" in state.party_keys["b"]


def test_box_to_party_partner_already_in_party_no_sync(tmp_path, monkeypatch):
    """If partner's mon is already in party, no pending_sync is needed."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = make_state_with_link()  # B:2 already in party
    state.party_keys["a"].discard("A:1")  # A's mon was boxed

    state.handle_event("a", {"event": "box_to_party", "key": "A:1"})
    cmds_b = state.handle_event("b", {"event": "tick"})
    assert noop_only(cmds_b), "Partner already in party — no sync needed"


# ── pokéballs gate ────────────────────────────────────────────────────────────
# The gate is now enforced in Lua (M.hasPokeballs()) — the server always processes
# events it receives and no longer has a server-side pokéballs gate.

def make_fresh_state(tmp_path, monkeypatch) -> SoulLinkState:
    """Return a SoulLinkState with no prior state (fresh run)."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState(retry_same_species=False)
    state.party_size = {"a": 2, "b": 2}
    return state


def test_server_processes_no_catch_regardless_of_pokeballs(tmp_path, monkeypatch):
    """Server has no pokéballs gate — Lua is responsible for not sending no_catch early."""
    state = make_fresh_state(tmp_path, monkeypatch)
    state.handle_event("a", {"event": "no_catch", "area_id": "route_22"})
    assert state.area_states.get("route_22") == AreaStatus.DEAD_ZONE


def test_no_catch_after_route_enter_creates_dead_zone(tmp_path, monkeypatch):
    """no_catch after entering a route should dead-zone the area."""
    state = make_fresh_state(tmp_path, monkeypatch)
    state.handle_event("a", {"event": "area_enter", "area_id": "route_1"})
    state.handle_event("a", {"event": "no_catch", "area_id": "route_1"})
    assert state.area_states.get("route_1") == AreaStatus.DEAD_ZONE


def test_dead_zone_retires_quarantined_partner_capture(tmp_path, monkeypatch):
    """When B captured (quarantined in box) and A no_catches, B's mon gets
    force_faint + memorialize, and pending_captures is cleaned up."""
    state = make_fresh_state(tmp_path, monkeypatch)
    # B captures on route_1 — quarantined (box_mon queued)
    cmds_b = state.handle_event("b", {"event": "capture", "key": "B:1",
                                       "area_id": "route_1", "level": 5})
    assert has_cmd(cmds_b, "box_mon", "B:1"), "B's capture should be quarantined"
    assert "B:1" not in state.party_keys["b"]
    # A fails to catch → dead zone
    state.handle_event("a", {"event": "no_catch", "area_id": "route_1",
                              "species_id": 16, "level": 3})
    assert state.area_states["route_1"] == AreaStatus.DEAD_ZONE
    # B's quarantined mon should get force_faint + memorialize
    b_cmds = state.queued_commands["b"]
    assert any(c["cmd"] == "force_faint" and c["key"] == "B:1" for c in b_cmds)
    assert any(c["cmd"] == "memorialize" and c["key"] == "B:1" for c in b_cmds)
    # pending_captures should be cleaned up
    assert "route_1" not in state.pending_captures


def test_area_enter_always_tracked(tmp_path, monkeypatch):
    """area_enter is tracked for encounter areas but skipped for gift areas."""
    state = make_fresh_state(tmp_path, monkeypatch)
    state.handle_event("a", {"event": "area_enter", "area_id": "route_1"})
    assert "route_1" in state.area_states
    # Gift areas should NOT create area state entries
    state.handle_event("a", {"event": "area_enter", "area_id": "oaks_lab"})
    assert "oaks_lab" not in state.area_states


def test_gift_capture_links_without_pokeballs(tmp_path, monkeypatch):
    """Starter capture in oaks_lab should create a link even before Pokéballs gate fires."""
    state = make_fresh_state(tmp_path, monkeypatch)
    state.handle_event("a", {"event": "capture", "key": "A:1", "level": 5,
                              "area_id": "oaks_lab"})
    state.handle_event("b", {"event": "capture", "key": "B:2", "level": 5,
                              "area_id": "oaks_lab"})
    assert state.area_states.get("oaks_lab") == AreaStatus.LINKED, \
        "Both starters captured in oaks_lab should be linked"
    assert len(state.links) == 1
    assert state.links[0].a.key == "A:1"
    assert state.links[0].b.key == "B:2"


def test_pokeballs_obtained_via_has_pokeballs_field(tmp_path, monkeypatch):
    """hello with has_pokeballs=True must activate the gate regardless of party contents."""
    state = make_fresh_state(tmp_path, monkeypatch)
    assert not state.pokeballs_obtained["a"]
    state.handle_event("a", {"event": "hello", "has_pokeballs": True, "party": []})
    assert state.pokeballs_obtained["a"], "has_pokeballs=True must activate gate"


def test_pokeballs_obtained_inferred_on_hello_with_party(tmp_path, monkeypatch):
    """hello with a non-empty party and no has_pokeballs field uses heuristic (old client)."""
    state = make_fresh_state(tmp_path, monkeypatch)
    assert not state.pokeballs_obtained["a"]
    state.handle_event("a", {"event": "hello",
                              "party": [{"key": "A:1", "hp": 30, "maxHP": 50, "level": 10}]})
    assert state.pokeballs_obtained["a"], "Non-empty party on hello (old client) must activate gate"


def test_pokeballs_explicit_false_not_set_by_party_heuristic(tmp_path, monkeypatch):
    """hello with has_pokeballs=False must NOT activate gate even if party is non-empty."""
    state = make_fresh_state(tmp_path, monkeypatch)
    state.handle_event("a", {"event": "hello", "has_pokeballs": False,
                              "party": [{"key": "A:1", "hp": 30, "maxHP": 50, "level": 10}]})
    assert not state.pokeballs_obtained["a"], "Explicit has_pokeballs=False must not activate gate"


def test_pokeballs_obtained_via_non_gift_capture(tmp_path, monkeypatch):
    """Capturing in a non-gift area activates pokeballs_obtained server-side."""
    state = make_fresh_state(tmp_path, monkeypatch)
    assert not state.pokeballs_obtained["a"]
    state.handle_event("a", {"event": "capture", "key": "A:1", "area_id": "route_1", "level": 5})
    assert state.pokeballs_obtained["a"]


def test_gift_capture_does_not_activate_pokeballs(tmp_path, monkeypatch):
    """Capturing in a gift area must NOT set pokeballs_obtained (no pokéballs needed)."""
    state = make_fresh_state(tmp_path, monkeypatch)
    state.handle_event("a", {"event": "capture", "key": "A:1", "area_id": "oaks_lab", "level": 5})
    assert not state.pokeballs_obtained["a"]


def test_no_catch_in_gift_area_ignored(tmp_path, monkeypatch):
    """A no_catch in a gift area (e.g. celadon_condominiums) must NOT create a dead zone.
    In AP, gift locations can have wild encounters — fleeing there must not block the gift."""
    state = make_fresh_state(tmp_path, monkeypatch)
    state.pokeballs_obtained = {"a": True, "b": True}
    state.handle_event("a", {"event": "no_catch", "area_id": "celadon_condominiums", "species_id": 133, "level": 25})
    assert state.area_states.get("celadon_condominiums", AreaStatus.UNSEEN) == AreaStatus.UNSEEN
    assert len(state.links) == 0, "Gift area no_catch must not create a dead-zone link entry"


def test_dynamic_gift_area_no_catch_ignored(tmp_path, monkeypatch):
    """Dynamic gift_* area_ids (from unmapped gift locations) must also be protected."""
    state = make_fresh_state(tmp_path, monkeypatch)
    state.pokeballs_obtained = {"a": True, "b": True}
    state.handle_event("a", {"event": "no_catch", "area_id": "gift_10_11", "species_id": 50, "level": 20})
    assert state.area_states.get("gift_10_11", AreaStatus.UNSEEN) == AreaStatus.UNSEEN
    assert len(state.links) == 0


def test_dynamic_gift_area_no_pokeball_activation(tmp_path, monkeypatch):
    """Captures in dynamic gift_* areas must NOT activate pokeballs_obtained."""
    state = make_fresh_state(tmp_path, monkeypatch)
    state.handle_event("a", {"event": "capture", "key": "A:99", "area_id": "gift_10_11", "level": 25})
    assert not state.pokeballs_obtained["a"]


def test_egg_capture_in_encounter_area_treated_as_gift(tmp_path, monkeypatch):
    """An egg captured in a normal encounter area (NPC egg-giver) is_egg=True must
    bypass the Pokéball gate and skip quarantine — same as a known gift area."""
    state = make_fresh_state(tmp_path, monkeypatch)
    # Player has an existing party member (party_size >= 1 would normally trigger quarantine).
    state.party_size = {"a": 1, "b": 0}
    cmds = state.handle_event("a", {"event": "capture", "key": "A:42", "area_id": "route_5",
                                    "level": 1, "is_egg": True})
    # No Pokéball gate flip (egg gifts don't require pokéballs).
    assert not state.pokeballs_obtained["a"], "Egg gift must not activate pokéball gate"
    # No quarantine command queued (egg gifts skip the box).
    assert not has_cmd(cmds, "box_mon", "A:42"),         "Egg gift must not be quarantined to box"


def test_egg_capture_in_daycare_not_treated_as_gift(tmp_path, monkeypatch):
    """An egg picked up at the daycare is bred, not a gift — it must follow normal
    capture rules (pokéball gate activates, quarantine applies)."""
    state = make_fresh_state(tmp_path, monkeypatch)
    state.party_size = {"a": 2, "b": 0}
    cmds = state.handle_event("a", {"event": "capture", "key": "A:43",
                                    "area_id": "four_island_pokemon_day_care",
                                    "level": 1, "is_egg": True})
    # Daycare is NOT a gift area, so pokéball gate fires (daycare requires player to be
    # active in the game with full progression, and the egg is the result of breeding).
    assert state.pokeballs_obtained["a"], "Daycare egg must activate pokéball gate"
    # Quarantine applies (egg must be linked or boxed).
    assert has_cmd(cmds, "box_mon", "A:43"),         "Daycare egg must be quarantined like a normal capture"


def test_capture_without_is_egg_field_unchanged(tmp_path, monkeypatch):
    """Old clients (no is_egg field) must continue to work — defaults to False."""
    state = make_fresh_state(tmp_path, monkeypatch)
    state.party_size = {"a": 2, "b": 0}
    cmds = state.handle_event("a", {"event": "capture", "key": "A:1",
                                    "area_id": "route_1", "level": 5})
    # Normal capture flow: pokéball gate activates, quarantine applies.
    assert state.pokeballs_obtained["a"]
    assert has_cmd(cmds, "box_mon", "A:1")


def test_dynamic_gift_areas_dont_collide(tmp_path, monkeypatch):
    """Two different gift_* areas must NOT share encounters (the old 'gift' fallback bug)."""
    state = make_fresh_state(tmp_path, monkeypatch)
    state.pokeballs_obtained = {"a": True, "b": True}
    # Both players receive gifts at different unmapped locations
    state.handle_event("a", {"event": "capture", "key": "A:1", "area_id": "gift_10_11", "level": 25})
    state.handle_event("b", {"event": "capture", "key": "B:2", "area_id": "gift_10_11", "level": 25})
    # Now a second gift at a DIFFERENT unmapped location
    state.handle_event("a", {"event": "capture", "key": "A:3", "area_id": "gift_12_5", "level": 30})
    # A:3 should be a pending capture, NOT killed as "already linked"
    assert "gift_12_5" in state.pending_captures
    assert "a" in state.pending_captures["gift_12_5"]
    assert state.pending_captures["gift_12_5"]["a"].key == "A:3"


def test_hello_sends_resolved_areas(tmp_path, monkeypatch):
    """On hello, the server should queue a resolved_areas command with linked/dead/pending areas."""
    state = make_fresh_state(tmp_path, monkeypatch)
    state.pokeballs_obtained = {"a": True, "b": True}
    # Create a linked area and a pending capture
    state.handle_event("a", {"event": "capture", "key": "A:1", "area_id": "route_1", "level": 5})
    state.handle_event("b", {"event": "capture", "key": "B:2", "area_id": "route_1", "level": 7})
    state.handle_event("a", {"event": "capture", "key": "A:3", "area_id": "route_2", "level": 4})
    # route_1 = linked, route_2 = pending (A captured)
    cmds = state.handle_event("a", {"event": "hello", "party": [
        {"key": "A:1", "hp": 20, "maxHP": 20, "level": 5},
        {"key": "A:3", "hp": 15, "maxHP": 15, "level": 4},
    ]})
    resolved_cmd = [c for c in cmds if c.get("cmd") == "resolved_areas"]
    assert len(resolved_cmd) == 1, "hello must include a resolved_areas command"
    areas = set(resolved_cmd[0]["areas"])
    assert "route_1" in areas, "linked area must be in resolved_areas"
    assert "route_2" in areas, "area with player's pending capture must be in resolved_areas"


def test_hello_sends_config_overworld_presence_false(tmp_path, monkeypatch):
    """On hello, the server must push a config command carrying overworld_presence (default False).

    The client gates the companion patch's Pokémon-Center trade NPC on this flag: OFF -> NPC is the
    trade entry point; ON -> trades go through the peer ghost.
    """
    state = make_fresh_state(tmp_path, monkeypatch)  # overworld_presence defaults to False
    cmds = state.handle_event("a", {"event": "hello", "party": []})
    config_cmd = [c for c in cmds if c.get("cmd") == "config"]
    assert len(config_cmd) == 1, "hello must include exactly one config command"
    assert config_cmd[0]["overworld_presence"] is False


def test_hello_sends_config_overworld_presence_true(tmp_path, monkeypatch):
    """When the run rule is ON, the hello config command reports overworld_presence=True."""
    state = make_fresh_state(tmp_path, monkeypatch)
    state.overworld_presence = True  # opt-in per-run feature
    cmds = state.handle_event("a", {"event": "hello", "party": []})
    config_cmd = [c for c in cmds if c.get("cmd") == "config"]
    assert len(config_cmd) == 1, "hello must include exactly one config command"
    assert config_cmd[0]["overworld_presence"] is True


def test_pokeballs_obtained_persists_through_save_load(tmp_path, monkeypatch):
    """pokeballs_obtained survives a server restart (save → load roundtrip)."""
    import server.state as st
    monkeypatch.setattr(st, "LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState()
    # Capture in a non-gift area activates pokeballs_obtained and triggers a save
    state.handle_event("a", {"event": "capture", "key": "A:1", "area_id": "route_1", "level": 5})
    assert state.pokeballs_obtained["a"]
    loaded = st.SoulLinkState.load()
    assert loaded.pokeballs_obtained["a"], "pokeballs_obtained['a'] must survive save/load"
    assert not loaded.pokeballs_obtained["b"]


def test_pokeballs_obtained_via_tick_has_pokeballs(tmp_path, monkeypatch):
    """tick with has_pokeballs=True should activate pokeballs_obtained mid-session."""
    state = make_fresh_state(tmp_path, monkeypatch)
    assert not state.pokeballs_obtained["a"]
    state.handle_event("a", {"event": "tick", "has_pokeballs": True, "ball_count": 5})
    assert state.pokeballs_obtained["a"], "has_pokeballs=True in tick must activate gate"


def test_tick_without_has_pokeballs_leaves_gate_unchanged(tmp_path, monkeypatch):
    """tick without has_pokeballs field must not change pokeballs_obtained state."""
    state = make_fresh_state(tmp_path, monkeypatch)
    state.handle_event("a", {"event": "tick", "ball_count": 0})
    assert not state.pokeballs_obtained["a"], "tick without has_pokeballs must not activate gate"



# ── memorial box ──────────────────────────────────────────────────────────────

def test_faint_queues_memorialize_both(tmp_path, monkeypatch):
    """After a faint, both players should receive a memorialize command."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = make_state_with_link()

    # A's faint response carries A's own memorialize (commands are delivered immediately)
    cmds_a = state.handle_event("a", {"event": "faint", "key": "A:1"})
    assert has_cmd(cmds_a, "memorialize", "A:1"), "A should receive memorialize for their mon"

    # B gets force_faint + memorialize on the next delivery
    cmds_b = state.handle_event("b", {"event": "tick"})
    assert has_cmd(cmds_b, "memorialize", "B:2"), "B should receive memorialize for their mon"


def test_faint_cancels_stale_party_mon(tmp_path, monkeypatch):
    """A queued party_mon for a mon that then dies should be cancelled by memorialize."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = make_state_with_link()
    # Simulate: a stale party_mon is already queued for A
    state.queued_commands["a"].append({"cmd": "party_mon", "key": "A:1"})
    # Now A's mon faints — _queue_memorialize should cancel the stale party_mon
    cmds_a = state.handle_event("a", {"event": "faint", "key": "A:1"})
    assert not has_cmd(cmds_a, "party_mon", "A:1"), "Stale party_mon must be cancelled on death"
    assert has_cmd(cmds_a, "memorialize", "A:1"), "memorialize should replace it"


def test_no_catch_queues_memorialize_partner(tmp_path, monkeypatch):
    """Partner's pending capture gets force_faint + memorialize on no_catch dead zone."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState(retry_same_species=False)
    state.pokeballs_obtained = {"a": True, "b": True}
    state.party_keys["b"].add("B:2")
    state.handle_event("b", {"event": "capture", "key": "B:2", "level": 8,
                              "area_id": "route_1"})
    state.handle_event("a", {"event": "no_catch", "area_id": "route_1"})

    cmds_b = state.handle_event("b", {"event": "tick"})
    assert has_cmd(cmds_b, "force_faint", "B:2"), "Partner gets force_faint"
    assert has_cmd(cmds_b, "memorialize", "B:2"), "Partner gets memorialize"


def test_memorialize_done_marks_memorial(tmp_path, monkeypatch):
    """Both sides confirming memorialize_done should mark the entry MEMORIAL."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    monkeypatch.setattr("server.state.MEMORIAL_PATH", str(tmp_path / "memorial.json"))
    state = make_state_with_link()
    state.handle_event("a", {"event": "faint", "key": "A:1"})
    assert state.links[0].status == LinkStatus.DEAD

    state.handle_event("a", {"event": "memorialize_done", "key": "A:1"})
    assert state.links[0].status == LinkStatus.DEAD, "One side done — still DEAD"

    state.handle_event("b", {"event": "memorialize_done", "key": "B:2"})
    assert state.links[0].status == LinkStatus.MEMORIAL, "Both done — should be MEMORIAL"


def test_memorialize_done_writes_memorial_json(tmp_path, monkeypatch):
    """Fully confirmed memorialization should write data/memorial.json."""
    import json as _json
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    monkeypatch.setattr("server.state.MEMORIAL_PATH", str(tmp_path / "memorial.json"))
    state = make_state_with_link()
    state.handle_event("a", {"event": "faint", "key": "A:1"})
    state.handle_event("a", {"event": "memorialize_done", "key": "A:1"})
    state.handle_event("b", {"event": "memorialize_done", "key": "B:2"})

    data = _json.loads((tmp_path / "memorial.json").read_text())
    assert len(data["retired_pairs"]) == 1
    pair = data["retired_pairs"][0]
    assert pair["area_id"] == "route_1"
    assert pair["a"]["key"] == "A:1"
    assert pair["b"]["key"] == "B:2"


def test_pending_memorials_requeued_on_reconnect(tmp_path, monkeypatch):
    """Pending memorials should be re-queued when a player reconnects (hello)."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = make_state_with_link()
    state.handle_event("a", {"event": "faint", "key": "A:1"})
    # Drain A's queue (simulates commands being delivered)
    state.handle_event("a", {"event": "tick"})
    # A's pending_memorials still has A:1 since no memorialize_done received
    assert "A:1" in state.pending_memorials["a"]

    # A reconnects — should re-queue memorialize (delivered in the hello response)
    cmds_reconnect = state.handle_event("a", {"event": "hello", "has_pokeballs": True, "party": []})
    assert has_cmd(cmds_reconnect, "memorialize", "A:1"), "memorialize must be re-queued on reconnect"

    """
    Full cross-player sync round-trip:
      1. A sends party_to_box → server queues box_mon for B.
      2. B auto-deposits and sends party_to_box with stats → server caches B's stats,
         no duplicate box_mon sent back to A.
      3. A retrieves → server sends party_mon to B WITH the cached stats.
    """
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = make_state_with_link()
    # Both players are connected (have sent hello) for this round-trip test
    state._has_helld.add("a")
    state._has_helld.add("b")
    stats_b = {"level": 15, "maxHP": 55, "attack": 30, "defense": 20,
               "speed": 18, "spAtk": 22, "spDef": 19}

    # Step 1: A deposits their mon
    cmds_a = state.handle_event("a", {"event": "party_to_box", "key": "A:1",
                                      "stats": {"level": 10, "maxHP": 35}})
    assert noop_only(cmds_a)
    cmds_b_step1 = state.handle_event("b", {"event": "tick"})
    assert has_cmd(cmds_b_step1, "box_mon"), "B should receive box_mon after A deposits"

    # Step 2: B auto-deposits and sends party_to_box with stats (Lua fix)
    cmds_b_step2 = state.handle_event("b", {"event": "party_to_box", "key": "B:2",
                                             "stats": stats_b})
    assert noop_only(cmds_b_step2), "B's stats-cache party_to_box should not re-trigger box_mon for A"
    assert state.mon_stats["b"].get("B:2") == stats_b, "B's stats must be cached on server"
    # A's mon is already boxed — no duplicate box_mon for A
    cmds_a_check = state.handle_event("a", {"event": "tick"})
    assert noop_only(cmds_a_check), "No spurious box_mon sent back to A"

    # Step 3: A retrieves → B receives party_mon with B's cached stats
    state.handle_event("a", {"event": "box_to_party", "key": "A:1"})
    cmds_b_step3 = state.handle_event("b", {"event": "tick"})
    assert has_cmd(cmds_b_step3, "party_mon"), "B should receive party_mon after A retrieves"
    pm = next(c for c in cmds_b_step3 if c.get("cmd") == "party_mon")
    assert pm["key"] == "B:2"
    assert pm.get("stats") == stats_b, "party_mon must carry B's cached stats"


# ── AP ROM type compatibility ─────────────────────────────────────────────────

def test_ap_rom_type_hello_accepted(tmp_path, monkeypatch):
    """Events with rom_type 'firered_ap' or 'leafgreen_ap' should be processed normally."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState()
    cmds = state.handle_event("a", {
        "event": "hello", "rom_type": "firered_ap",
        "has_pokeballs": True, "ball_count": 5, "party": [],
    })
    assert noop_only(cmds), "AP hello must be accepted without error"
    assert state.pokeballs_obtained["a"]


def test_ap_rom_type_capture_links_normally(tmp_path, monkeypatch):
    """Captures from AP clients should link identically to vanilla."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState()
    state.pokeballs_obtained = {"a": True, "b": True}
    state.handle_event("a", {"event": "capture", "key": "A:1", "area_id": "route_1", "level": 5})
    state.handle_event("b", {"event": "capture", "key": "B:2", "area_id": "route_1", "level": 7})
    assert state.area_states["route_1"] == AreaStatus.LINKED
    assert state.links[0].a.key == "A:1"
    assert state.links[0].b.key == "B:2"


# ── species lock ──────────────────────────────────────────────────────────────

def test_species_lock_rejects_same_species(tmp_path, monkeypatch):
    """Both capture same species → second is force-fainted, area stays pending."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState(species_lock=True)
    state.pokeballs_obtained = {"a": True, "b": True}
    # Both catch Pidgey (species 16)
    state.handle_event("a", {"event": "capture", "key": "AA000001:11111111",
                              "area_id": "route_1", "level": 5, "species_id": 16})
    cmds_b = state.handle_event("b", {"event": "capture", "key": "BB000001:22222222",
                                       "area_id": "route_1", "level": 6, "species_id": 16})
    assert has_cmd(cmds_b, "force_faint", "BB000001:22222222"), "B's capture should be rejected"
    assert len(state.links) == 0, "No link should be formed"
    assert state.area_states["route_1"] != AreaStatus.LINKED


def test_species_lock_rejects_same_evo_family(tmp_path, monkeypatch):
    """A catches Charmander, B catches Charmeleon → rejected (same evo family)."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState(species_lock=True)
    state.pokeballs_obtained = {"a": True, "b": True}
    # Charmander=4, Charmeleon=5 — both map to base_form 4
    state.handle_event("a", {"event": "capture", "key": "AA000001:11111111",
                              "area_id": "route_2", "level": 5, "species_id": 4})
    cmds_b = state.handle_event("b", {"event": "capture", "key": "BB000001:22222222",
                                       "area_id": "route_2", "level": 15, "species_id": 5})
    assert has_cmd(cmds_b, "force_faint", "BB000001:22222222")
    assert len(state.links) == 0


def test_species_lock_allows_different_family(tmp_path, monkeypatch):
    """Different evo families → link formed normally."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState(species_lock=True)
    state.pokeballs_obtained = {"a": True, "b": True}
    # Pidgey=16, Rattata=19
    state.handle_event("a", {"event": "capture", "key": "AA000001:11111111",
                              "area_id": "route_3", "level": 5, "species_id": 16})
    state.handle_event("b", {"event": "capture", "key": "BB000001:22222222",
                              "area_id": "route_3", "level": 5, "species_id": 19})
    assert state.area_states["route_3"] == AreaStatus.LINKED
    assert len(state.links) == 1


def test_species_lock_off_allows_same_species(tmp_path, monkeypatch):
    """Lock disabled → same species links fine."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState(species_lock=False)
    state.pokeballs_obtained = {"a": True, "b": True}
    state.handle_event("a", {"event": "capture", "key": "AA000001:11111111",
                              "area_id": "route_4", "level": 5, "species_id": 16})
    state.handle_event("b", {"event": "capture", "key": "BB000001:22222222",
                              "area_id": "route_4", "level": 5, "species_id": 16})
    assert state.area_states["route_4"] == AreaStatus.LINKED


def test_evo_family_eevee_variants(tmp_path, monkeypatch):
    """Eevee + Vaporeon → same family, rejected by species lock."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState(species_lock=True)
    state.pokeballs_obtained = {"a": True, "b": True}
    # Eevee=133, Vaporeon=134 — both map to base_form 133
    state.handle_event("a", {"event": "capture", "key": "AA000001:11111111",
                              "area_id": "route_5", "level": 25, "species_id": 133})
    cmds = state.handle_event("b", {"event": "capture", "key": "BB000001:22222222",
                                     "area_id": "route_5", "level": 25, "species_id": 134})
    assert has_cmd(cmds, "force_faint", "BB000001:22222222")
    assert len(state.links) == 0


def test_evo_family_single_stage(tmp_path, monkeypatch):
    """Tauros + Tauros → same base form (self), rejected by species lock."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState(species_lock=True)
    state.pokeballs_obtained = {"a": True, "b": True}
    state.handle_event("a", {"event": "capture", "key": "AA000001:11111111",
                              "area_id": "route_6", "level": 30, "species_id": 128})
    cmds = state.handle_event("b", {"event": "capture", "key": "BB000001:22222222",
                                     "area_id": "route_6", "level": 30, "species_id": 128})
    assert has_cmd(cmds, "force_faint", "BB000001:22222222")


def test_species_lock_same_save_duplicate_rejected(tmp_path, monkeypatch):
    """If A already has a Pidgey linked, A can't catch another Pidgey on a different route.
    The duplicate is rejected immediately at capture time (not deferred to link time)."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState(species_lock=True)
    state.pokeballs_obtained = {"a": True, "b": True}
    # Link Pidgey(A) ↔ Rattata(B) on route_1
    state.handle_event("a", {"event": "capture", "key": "AA000001:11111111",
                              "area_id": "route_1", "level": 5, "species_id": 16})
    state.handle_event("b", {"event": "capture", "key": "BB000001:22222222",
                              "area_id": "route_1", "level": 5, "species_id": 19})
    assert state.area_states["route_1"] == AreaStatus.LINKED
    # A catches another Pidgey on route_2 — rejected immediately
    a_cmds = state.handle_event("a", {"event": "capture", "key": "AA000002:11111111",
                                       "area_id": "route_2", "level": 7, "species_id": 16})
    assert any(c["cmd"] == "force_faint" and c["key"] == "AA000002:11111111" for c in a_cmds)
    assert any(c["cmd"] == "play_sound" and c.get("sound") == 26 for c in a_cmds)
    assert any(c["cmd"] == "unresolve_area" for c in a_cmds)
    # A's capture should NOT be in pending_captures
    assert "a" not in state.pending_captures.get("route_2", {})
    assert state.area_states.get("route_2", AreaStatus.UNSEEN) != AreaStatus.LINKED


def test_species_lock_same_save_duplicate_b_side(tmp_path, monkeypatch):
    """If B already has a Rattata linked, B can't catch another Rattata elsewhere."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState(species_lock=True)
    state.pokeballs_obtained = {"a": True, "b": True}
    # Link: A=Pidgey(16) ↔ B=Rattata(19) on route_1
    state.handle_event("a", {"event": "capture", "key": "AA000001:11111111",
                              "area_id": "route_1", "level": 5, "species_id": 16})
    state.handle_event("b", {"event": "capture", "key": "BB000001:22222222",
                              "area_id": "route_1", "level": 5, "species_id": 19})
    # Now on route_2: A=Spearow(21) captured first, then B=Rattata(19) — B rejected immediately
    state.handle_event("a", {"event": "capture", "key": "AA000003:11111111",
                              "area_id": "route_2", "level": 6, "species_id": 21})
    b_cmds = state.handle_event("b", {"event": "capture", "key": "BB000003:22222222",
                                       "area_id": "route_2", "level": 6, "species_id": 19})
    # B's capture rejected immediately (force_faint + unresolve_area)
    assert any(c["cmd"] == "force_faint" and c["key"] == "BB000003:22222222" for c in b_cmds)
    assert any(c["cmd"] == "unresolve_area" for c in b_cmds)
    # B not in pending
    assert "b" not in state.pending_captures.get("route_2", {})
    # A's pending capture still present
    assert "a" in state.pending_captures.get("route_2", {})


def test_species_lock_pending_capture_blocks_duplicate(tmp_path, monkeypatch):
    """A pending (unlinked) capture should also block a duplicate species in another area."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState(species_lock=True)
    state.pokeballs_obtained = {"a": True, "b": True}
    # A captures Pidgey on route_1 (pending — B hasn't caught yet)
    state.handle_event("a", {"event": "capture", "key": "AA000001:11111111",
                              "area_id": "route_1", "level": 5, "species_id": 16})
    # A catches another Pidgey on route_2 — should be rejected (pending dup)
    a_cmds = state.handle_event("a", {"event": "capture", "key": "AA000002:11111111",
                                       "area_id": "route_2", "level": 7, "species_id": 16})
    assert any(c["cmd"] == "force_faint" and c["key"] == "AA000002:11111111" for c in a_cmds)
    assert any(c["cmd"] == "unresolve_area" for c in a_cmds)
    # Route 1 pending capture still intact
    assert "a" in state.pending_captures.get("route_1", {})
    # Route 2 not pending for A
    assert "a" not in state.pending_captures.get("route_2", {})


def test_species_lock_dead_pair_not_counted(tmp_path, monkeypatch):
    """Dead pairs should not block new captures of the same species."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState(species_lock=True)
    state.pokeballs_obtained = {"a": True, "b": True}
    # Link and kill a Pidgey pair
    state.handle_event("a", {"event": "capture", "key": "AA000001:11111111",
                              "area_id": "route_1", "level": 5, "species_id": 16})
    state.handle_event("b", {"event": "capture", "key": "BB000001:22222222",
                              "area_id": "route_1", "level": 5, "species_id": 19})
    state.handle_event("a", {"event": "faint", "key": "AA000001:11111111"})
    # Now another Pidgey on route_2 should be allowed (dead pair doesn't count)
    state.handle_event("a", {"event": "capture", "key": "AA000002:11111111",
                              "area_id": "route_2", "level": 7, "species_id": 16})
    state.handle_event("b", {"event": "capture", "key": "BB000002:22222222",
                              "area_id": "route_2", "level": 7, "species_id": 25})
    assert state.area_states["route_2"] == AreaStatus.LINKED

def test_gender_lock_rejects_same_gender(tmp_path, monkeypatch):
    """Both male → rejection."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState(gender_lock=True)
    state.pokeballs_obtained = {"a": True, "b": True}
    # personality & 0xFF >= 127 → male (for default 50/50 species like Pidgey=16)
    # 0xFF = 255 → male, 0x00 = 0 → female
    state.handle_event("a", {"event": "capture", "key": "000000FF:11111111",
                              "area_id": "route_7", "level": 5, "species_id": 16})
    cmds = state.handle_event("b", {"event": "capture", "key": "000000FF:22222222",
                                     "area_id": "route_7", "level": 5, "species_id": 19})
    assert has_cmd(cmds, "force_faint", "000000FF:22222222")
    assert len(state.links) == 0


def test_gender_lock_allows_opposite_gender(tmp_path, monkeypatch):
    """Male + female → link formed."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState(gender_lock=True)
    state.pokeballs_obtained = {"a": True, "b": True}
    # 0xFF → male, 0x00 → female (Pidgey has default 127 threshold)
    state.handle_event("a", {"event": "capture", "key": "000000FF:11111111",
                              "area_id": "route_8", "level": 5, "species_id": 16})
    state.handle_event("b", {"event": "capture", "key": "00000100:22222222",
                              "area_id": "route_8", "level": 5, "species_id": 19})
    assert state.area_states["route_8"] == AreaStatus.LINKED
    assert len(state.links) == 1


def test_gender_lock_ignores_genderless(tmp_path, monkeypatch):
    """Genderless + genderless → no violation."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState(gender_lock=True)
    state.pokeballs_obtained = {"a": True, "b": True}
    # Magnemite=81, Voltorb=100 (both genderless)
    state.handle_event("a", {"event": "capture", "key": "000000FF:11111111",
                              "area_id": "route_9", "level": 15, "species_id": 81})
    state.handle_event("b", {"event": "capture", "key": "000000FF:22222222",
                              "area_id": "route_9", "level": 15, "species_id": 100})
    assert state.area_states["route_9"] == AreaStatus.LINKED


def test_gender_lock_ignores_mixed_genderless(tmp_path, monkeypatch):
    """Gendered + genderless → no violation."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState(gender_lock=True)
    state.pokeballs_obtained = {"a": True, "b": True}
    # Pidgey=16 (gendered), Magnemite=81 (genderless)
    state.handle_event("a", {"event": "capture", "key": "000000FF:11111111",
                              "area_id": "route_10", "level": 5, "species_id": 16})
    state.handle_event("b", {"event": "capture", "key": "000000FF:22222222",
                              "area_id": "route_10", "level": 15, "species_id": 81})
    assert state.area_states["route_10"] == AreaStatus.LINKED


@pytest.mark.parametrize("treads_player", ["a", "b"])
def test_rr_gender_lock_allows_iron_treads_with_female(treads_player, tmp_path):
    state = SoulLinkState(
        data_dir=str(tmp_path), gender_lock=True, adapter=Gen3Adapter(is_rr=True),
    )
    state.pokeballs_obtained = {"a": True, "b": True}
    for player, key in [("a", "00000000:12345678"), ("b", "00000100:87654321")]:
        cmds = state.handle_event(player, {
            "event": "capture", "key": key, "area_id": "route_1", "level": 15,
            "species_id": 1258 if player == treads_player else 113,
        })
        assert not has_cmd(cmds, "force_faint")
        assert not has_cmd(cmds, "memorialize")
    assert state.area_states["route_1"] == AreaStatus.LINKED
    assert len(state.links) == 1 and state.links[0].status == LinkStatus.ALIVE


# ── type lock ──────────────────────────────────────────────────────────────────


def test_type_lock_rejects_shared_type(tmp_path, monkeypatch):
    """Both mons share a type → rejected."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState(type_lock=True)
    state.pokeballs_obtained = {"a": True, "b": True}
    # Bulbasaur=1 (Grass/Poison), Oddish=43 (Grass/Poison) — share both types
    state.handle_event("a", {"event": "capture", "key": "AA000001:11111111",
                              "area_id": "route_1", "level": 5, "species_id": 1})
    cmds = state.handle_event("b", {"event": "capture", "key": "BB000001:22222222",
                                     "area_id": "route_1", "level": 5, "species_id": 43})
    assert has_cmd(cmds, "force_faint", "BB000001:22222222")
    assert len(state.links) == 0


def test_type_lock_rejects_single_shared_type(tmp_path, monkeypatch):
    """One shared type out of different dual types → still rejected."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState(type_lock=True)
    state.pokeballs_obtained = {"a": True, "b": True}
    # Charmander=4 (Fire/Fire), Ponyta=77 (Fire/Fire) — same monotype
    state.handle_event("a", {"event": "capture", "key": "AA000001:11111111",
                              "area_id": "route_2", "level": 5, "species_id": 4})
    cmds = state.handle_event("b", {"event": "capture", "key": "BB000001:22222222",
                                     "area_id": "route_2", "level": 5, "species_id": 77})
    assert has_cmd(cmds, "force_faint", "BB000001:22222222")


def test_type_lock_rejects_partial_overlap(tmp_path, monkeypatch):
    """Dual-type A overlaps one type with mono-type B → rejected."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState(type_lock=True)
    state.pokeballs_obtained = {"a": True, "b": True}
    # Bulbasaur=1 (Grass/Poison), Mankey=56 (Fighting) — no overlap... wait
    # Bulbasaur=1 (Grass/Poison), Bellsprout=69 (Grass/Poison) — full overlap
    # Let's use: Charizard=6 (Fire/Flying), Pidgey=16 (Normal/Flying) — share Flying
    state.handle_event("a", {"event": "capture", "key": "AA000001:11111111",
                              "area_id": "route_3", "level": 5, "species_id": 6})
    cmds = state.handle_event("b", {"event": "capture", "key": "BB000001:22222222",
                                     "area_id": "route_3", "level": 5, "species_id": 16})
    assert has_cmd(cmds, "force_faint", "BB000001:22222222")
    assert len(state.links) == 0


def test_type_lock_allows_no_overlap(tmp_path, monkeypatch):
    """No shared types → link formed normally."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState(type_lock=True)
    state.pokeballs_obtained = {"a": True, "b": True}
    # Charmander=4 (Fire), Squirtle=7 (Water) — no overlap
    state.handle_event("a", {"event": "capture", "key": "AA000001:11111111",
                              "area_id": "route_4", "level": 5, "species_id": 4})
    state.handle_event("b", {"event": "capture", "key": "BB000001:22222222",
                              "area_id": "route_4", "level": 5, "species_id": 7})
    assert state.area_states["route_4"] == AreaStatus.LINKED
    assert len(state.links) == 1


def test_type_lock_allows_dual_no_overlap(tmp_path, monkeypatch):
    """Both dual-type but no overlap → link formed."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState(type_lock=True)
    state.pokeballs_obtained = {"a": True, "b": True}
    # Bulbasaur=1 (Grass/Poison), Geodude=74 (Rock/Ground) — no overlap
    state.handle_event("a", {"event": "capture", "key": "AA000001:11111111",
                              "area_id": "route_5", "level": 5, "species_id": 1})
    state.handle_event("b", {"event": "capture", "key": "BB000001:22222222",
                              "area_id": "route_5", "level": 5, "species_id": 74})
    assert state.area_states["route_5"] == AreaStatus.LINKED


def test_type_lock_off_allows_shared_type(tmp_path, monkeypatch):
    """Type lock disabled → shared types link fine."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState(type_lock=False)
    state.pokeballs_obtained = {"a": True, "b": True}
    state.handle_event("a", {"event": "capture", "key": "AA000001:11111111",
                              "area_id": "route_6", "level": 5, "species_id": 4})
    state.handle_event("b", {"event": "capture", "key": "BB000001:22222222",
                              "area_id": "route_6", "level": 5, "species_id": 77})
    assert state.area_states["route_6"] == AreaStatus.LINKED


def test_type_lock_violation_message_contains_type_name(tmp_path, monkeypatch):
    """Violation gui_prompt includes the shared type name."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState(type_lock=True)
    state.pokeballs_obtained = {"a": True, "b": True}
    # Both Fire monotypes
    state.handle_event("a", {"event": "capture", "key": "AA000001:11111111",
                              "area_id": "route_7", "level": 5, "species_id": 4})
    cmds = state.handle_event("b", {"event": "capture", "key": "BB000001:22222222",
                                     "area_id": "route_7", "level": 5, "species_id": 77})
    prompts = [c for c in cmds if c.get("cmd") == "gui_prompt"]
    assert prompts and any("Fire" in h.get("text", "") for h in prompts)


def test_type_lock_retry_after_violation(tmp_path, monkeypatch):
    """After type violation, player can retry with a non-overlapping type."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState(type_lock=True)
    state.pokeballs_obtained = {"a": True, "b": True}
    # A catches Charmander (Fire)
    state.handle_event("a", {"event": "capture", "key": "AA000001:11111111",
                              "area_id": "route_8", "level": 5, "species_id": 4})
    # B catches Ponyta (Fire) → rejected
    state.handle_event("b", {"event": "capture", "key": "BB000001:22222222",
                              "area_id": "route_8", "level": 5, "species_id": 77})
    assert state.area_states["route_8"] == AreaStatus.PENDING_B
    # B retries with Squirtle (Water) → should link
    state.handle_event("b", {"event": "capture", "key": "CC000001:33333333",
                              "area_id": "route_8", "level": 5, "species_id": 7})
    assert state.area_states["route_8"] == AreaStatus.LINKED
    assert len(state.links) == 1


# ── combined locks ────────────────────────────────────────────────────────────

def test_both_locks_species_takes_priority(tmp_path, monkeypatch):
    """Same species AND same gender → species violation reported (species checked first)."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState(species_lock=True, gender_lock=True)
    state.pokeballs_obtained = {"a": True, "b": True}
    # Both Pidgey, both male
    state.handle_event("a", {"event": "capture", "key": "000000FF:11111111",
                              "area_id": "route_11", "level": 5, "species_id": 16})
    cmds = state.handle_event("b", {"event": "capture", "key": "000000FF:22222222",
                                     "area_id": "route_11", "level": 5, "species_id": 16})
    assert has_cmd(cmds, "force_faint", "000000FF:22222222")
    # Check that a gui_prompt mentions species
    prompts = [c for c in cmds if c.get("cmd") == "gui_prompt"]
    assert prompts and any("Species" in h.get("text", "") for h in prompts)


def test_lock_violation_preserves_first_capture(tmp_path, monkeypatch):
    """First player's pending capture survives rejection."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState(species_lock=True)
    state.pokeballs_obtained = {"a": True, "b": True}
    state.handle_event("a", {"event": "capture", "key": "AA000001:11111111",
                              "area_id": "route_12", "level": 5, "species_id": 16})
    state.handle_event("b", {"event": "capture", "key": "BB000001:22222222",
                              "area_id": "route_12", "level": 5, "species_id": 16})
    # A's capture should still be in pending
    assert "a" in state.pending_captures.get("route_12", {})
    assert state.pending_captures["route_12"]["a"].key == "AA000001:11111111"
    # B's should have been removed
    assert "b" not in state.pending_captures.get("route_12", {})


def test_lock_violation_area_stays_pending(tmp_path, monkeypatch):
    """Area state remains PENDING after rejection so violating player can retry."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState(species_lock=True)
    state.pokeballs_obtained = {"a": True, "b": True}
    state.handle_event("a", {"event": "capture", "key": "AA000001:11111111",
                              "area_id": "route_13", "level": 5, "species_id": 16})
    state.handle_event("b", {"event": "capture", "key": "BB000001:22222222",
                              "area_id": "route_13", "level": 5, "species_id": 16})
    # Area should be PENDING_B (waiting for B to try again)
    assert state.area_states["route_13"] == AreaStatus.PENDING_B
    # B retries with a different species → should link now
    state.handle_event("b", {"event": "capture", "key": "CC000001:33333333",
                              "area_id": "route_13", "level": 5, "species_id": 19})
    assert state.area_states["route_13"] == AreaStatus.LINKED
    assert len(state.links) == 1


def test_lock_violation_unresolve_prevents_dead_zone(tmp_path, monkeypatch):
    """After cross-player lock violation + no_catch, initial capture must survive."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState(species_lock=True)
    state.pokeballs_obtained = {"a": True, "b": True}
    # A captures Pidgey on route_14
    state.handle_event("a", {"event": "capture", "key": "AA000001:11111111",
                              "area_id": "route_14", "level": 5, "species_id": 16})
    # B captures Pidgey on route_14 → rejected (same species)
    cmds_b = state.handle_event("b", {"event": "capture", "key": "BB000001:22222222",
                                       "area_id": "route_14", "level": 5, "species_id": 16})
    assert has_cmd(cmds_b, "force_faint", "BB000001:22222222")
    # unresolve_area must be queued so Lua doesn't fire no_catch
    assert any(c.get("cmd") == "unresolve_area" and c.get("area_id") == "route_14"
               for c in cmds_b)
    # A's pending capture should still be there
    assert state.pending_captures["route_14"]["a"].key == "AA000001:11111111"
    assert state.area_states["route_14"] == AreaStatus.PENDING_B


def test_species_clause_retry_suppresses_no_catch(tmp_path, monkeypatch):
    """After species clause rejects a capture, no_catch on any species must be suppressed
    until the player catches a valid mon."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState(species_lock=True)
    state.pokeballs_obtained = {"a": True, "b": True}
    # A has Pidgey linked on route_1
    state.handle_event("a", {"event": "capture", "key": "AA:11", "area_id": "route_1",
                              "level": 5, "species_id": 16})
    state.handle_event("b", {"event": "capture", "key": "BB:22", "area_id": "route_1",
                              "level": 5, "species_id": 25})
    assert state.area_states["route_1"] == AreaStatus.LINKED

    # A catches duplicate Pidgey on route_2 → rejected
    state.handle_event("a", {"event": "capture", "key": "CC:33", "area_id": "route_2",
                              "level": 5, "species_id": 16})
    assert "route_2" in state.retry_areas["a"]

    # A runs from a NON-duplicate (Rattata) on route_2 → no_catch should be SUPPRESSED
    cmds = state.handle_event("a", {"event": "no_catch", "area_id": "route_2", "species_id": 19})
    assert state.area_states.get("route_2", AreaStatus.UNSEEN) != AreaStatus.DEAD_ZONE
    # Should send unresolve_area to keep retrying
    assert any(c.get("cmd") == "unresolve_area" for c in cmds)

    # A catches valid Rattata on route_2 → retry cleared, capture stored
    state.handle_event("a", {"event": "capture", "key": "DD:44", "area_id": "route_2",
                              "level": 5, "species_id": 19})
    assert "route_2" not in state.retry_areas["a"]
    assert state.pending_captures["route_2"]["a"].key == "DD:44"


def test_partner_no_catch_suppressed_during_retry(tmp_path, monkeypatch):
    """B's no_catch must be suppressed while A has a retry pending on that area."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState(species_lock=True)
    state.pokeballs_obtained = {"a": True, "b": True}
    # A has Pidgey linked
    state.handle_event("a", {"event": "capture", "key": "AA:11", "area_id": "route_1",
                              "level": 5, "species_id": 16})
    state.handle_event("b", {"event": "capture", "key": "BB:22", "area_id": "route_1",
                              "level": 5, "species_id": 25})
    # A catches duplicate Pidgey on route_3 → rejected, retry pending
    state.handle_event("a", {"event": "capture", "key": "CC:33", "area_id": "route_3",
                              "level": 5, "species_id": 16})
    assert "route_3" in state.retry_areas["a"]
    # B sends no_catch for route_3 → must be suppressed (A has retry pending)
    cmds = state.handle_event("b", {"event": "no_catch", "area_id": "route_3", "species_id": 19})
    assert state.area_states.get("route_3", AreaStatus.UNSEEN) != AreaStatus.DEAD_ZONE
    assert any(c.get("cmd") == "unresolve_area" for c in cmds)


# ── lock rules persistence ────────────────────────────────────────────────────

def test_lock_rules_persist_in_save(tmp_path, monkeypatch):
    """species_lock/gender_lock/type_lock saved and loaded from links.json."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState(species_lock=True, gender_lock=True, type_lock=True)
    state._save()
    loaded = SoulLinkState.load()
    assert loaded.species_lock is True
    assert loaded.gender_lock is True
    assert loaded.type_lock is True


def test_lock_rules_default_false(tmp_path, monkeypatch):
    """Old saves without rules key default to all False."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState()
    state._save()
    loaded = SoulLinkState.load()
    assert loaded.species_lock is False
    assert loaded.gender_lock is False
    assert loaded.type_lock is False


# ── memorial overflow / failure ──────────────────────────────────────────────


def test_memorialize_failed_clears_pending(tmp_path, monkeypatch):
    """memorialize_failed should remove the key from pending_memorials
    so the pair can still reach MEMORIAL status."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = make_state_with_link(status=LinkStatus.DEAD)
    state.pending_memorials["a"].add("A:1")
    state.pending_memorials["b"].add("B:2")

    # A's memorialization fails
    state.handle_event("a", {"event": "memorialize_failed", "key": "A:1", "reason": "all boxes full"})
    assert "A:1" not in state.pending_memorials["a"]
    # Entry still DEAD because B hasn't confirmed yet
    assert state.links[0].status == LinkStatus.DEAD

    # B's memorialization succeeds
    state.handle_event("b", {"event": "memorialize_done", "key": "B:2"})
    # Now both are done → MEMORIAL
    assert state.links[0].status == LinkStatus.MEMORIAL


def test_memorialize_both_fail_still_reaches_memorial(tmp_path, monkeypatch):
    """Even if both sides fail to memorialize, the pair should still reach MEMORIAL."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = make_state_with_link(status=LinkStatus.DEAD)
    state.pending_memorials["a"].add("A:1")
    state.pending_memorials["b"].add("B:2")

    state.handle_event("a", {"event": "memorialize_failed", "key": "A:1", "reason": "all boxes full"})
    assert state.links[0].status == LinkStatus.DEAD

    state.handle_event("b", {"event": "memorialize_failed", "key": "B:2", "reason": "all boxes full"})
    assert state.links[0].status == LinkStatus.MEMORIAL

def test_hello_re_quarantine_skips_when_only_alive_mon(tmp_path, monkeypatch):
    """Re-quarantine on hello must not box the starter if it would leave no alive mons.

    Scenario: Player A has starter (pending link in "intro") and a force-fainted
    Mon2 (HP=0) in party.  Re-quarantining the starter would leave only a fainted
    mon, effectively softlocking the player.
    """
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState()
    state.pokeballs_obtained = {"a": True, "b": False}
    # Simulate: starter captured in "intro", pending link
    starter_key = "STARTER:1111"
    state.pending_captures["intro"] = {
        "a": MonInfo(key=starter_key, level=5, nickname="Bulba", species=1)
    }
    state.area_states["intro"] = AreaStatus.PENDING_B

    # Hello with party = [starter(alive), mon2(fainted)]
    cmds = state.handle_event("a", {"event": "hello", "party": [
        {"key": starter_key, "hp": 22, "maxHP": 22},
        {"key": "MON2:2222", "hp": 0, "maxHP": 14},
    ]})
    # Starter must NOT be quarantined (only alive mon remaining)
    assert not has_cmd(cmds, "box_mon", starter_key), \
        "Re-quarantine boxed the starter even though it would leave no alive mons"


def test_hello_re_quarantine_works_when_alive_mon_remains(tmp_path, monkeypatch):
    """Re-quarantine DOES fire when the player has another alive mon."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState()
    state.pokeballs_obtained = {"a": True, "b": False}
    cap_key = "CAP:1111"
    state.pending_captures["route_1"] = {
        "a": MonInfo(key=cap_key, level=3, nickname="Pidgey", species=16)
    }
    state.area_states["route_1"] = AreaStatus.PENDING_B

    # Hello with party = [starter(alive), capture(alive)]
    cmds = state.handle_event("a", {"event": "hello", "party": [
        {"key": "STARTER:9999", "hp": 22, "maxHP": 22},
        {"key": cap_key, "hp": 14, "maxHP": 14},
    ]})
    assert has_cmd(cmds, "box_mon", cap_key), \
        "Re-quarantine should fire when another alive mon remains"


def test_capture_no_quarantine_before_hello(tmp_path, monkeypatch):
    """Capture before hello (party_size unknown) must not quarantine — prevents
    boxing the starter when party_size defaults to 0 instead of 6."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState()
    state.pokeballs_obtained = {"a": False, "b": False}
    # No hello received yet — party_size not set
    cmds = state.handle_event("a", {
        "event": "capture", "key": "STARTER:1111",
        "area_id": "intro", "level": 5,
    })
    assert not has_cmd(cmds, "box_mon", "STARTER:1111"), \
        "Starter should not be quarantined before hello sets party_size"


def test_memorialize_failed_no_pending_is_harmless(tmp_path, monkeypatch):
    """memorialize_failed for a key not in pending_memorials should still allow
    the pair to reach MEMORIAL (both sides already clear)."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = make_state_with_link(status=LinkStatus.DEAD)
    # No pending memorials — both sides already clear, so the check passes
    state.handle_event("a", {"event": "memorialize_failed", "key": "A:1", "reason": "key not found"})
    # With no pending memorials on either side, the pair finalizes
    assert state.links[0].status == LinkStatus.MEMORIAL


# ── memorial box contamination safeguards ─────────────────────────────────────

from server.server import SLinkServer  # noqa: E402 (placed near relevant tests)


class _MockAdapter:
    """Minimal adapter stub for _check_memorial_box_contamination tests."""
    memorial_box_index = 13
    mons_per_box = 30

    def species_name(self, sid):
        return f"species#{sid}"


def _make_mock_server(state) -> object:
    """Return a minimal mock object that exposes _check_memorial_box_contamination."""
    class _MS:
        adapter = _MockAdapter()
        _warned_orphan_keys: set = set()
        _warned_memorial_keys: set = set()

        def _memorial_box_indices(self_inner, player_id):
            return SLinkServer._memorial_box_indices(self_inner, player_id)

        def _check_memorial_box_contamination(self_inner, player_id, pc_boxes):
            return SLinkServer._check_memorial_box_contamination(self_inner, player_id, pc_boxes)

    m = _MS()
    m.state = state
    return m


def test_contamination_dead_mon_in_regular_box_requeues_memorialize(tmp_path, monkeypatch):
    """Dead mon found in a regular (non-memorial) box must trigger a re-queued memorialize."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = make_state_with_link(status=LinkStatus.DEAD)
    # Clear pending_memorials so the server believes the write was already done.
    state.pending_memorials["a"].discard("A:1")
    srv = _make_mock_server(state)

    # A's mon is in regular box 5, not the memorial box (index 13).
    pc_boxes = [{"box": 5, "slot": 0, "key": "A:1", "species_id": 16, "nickname": "PIDGEY"}]
    srv._check_memorial_box_contamination("a", pc_boxes)

    assert has_cmd(state.queued_commands["a"], "memorialize", "A:1"), \
        "Dead mon in regular box must be re-queued for memorialize"


def test_contamination_dead_mon_in_regular_box_no_duplicate_queue(tmp_path, monkeypatch):
    """If memorialize is already queued, the safeguard must not add a duplicate."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = make_state_with_link(status=LinkStatus.DEAD)
    state.queued_commands["a"].append({"cmd": "memorialize", "key": "A:1"})
    srv = _make_mock_server(state)

    pc_boxes = [{"box": 5, "slot": 0, "key": "A:1", "species_id": 16, "nickname": "PIDGEY"}]
    srv._check_memorial_box_contamination("a", pc_boxes)

    mem_cmds = [c for c in state.queued_commands["a"]
                if c.get("cmd") == "memorialize" and c.get("key") == "A:1"]
    assert len(mem_cmds) == 1, "Dead mon in regular box must not double-queue memorialize"


def test_contamination_dead_mon_in_memorial_box_no_action(tmp_path, monkeypatch):
    """Dead mon already in the memorial box must produce no commands."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = make_state_with_link(status=LinkStatus.DEAD)
    srv = _make_mock_server(state)

    # A's mon is correctly in the memorial box.
    pc_boxes = [{"box": 13, "slot": 0, "key": "A:1", "species_id": 16, "nickname": "PIDGEY"}]
    before = list(state.queued_commands["a"])
    srv._check_memorial_box_contamination("a", pc_boxes)

    assert state.queued_commands["a"] == before, \
        "Dead mon in memorial box must not queue any commands"


def test_contamination_orphan_in_memorial_box_no_auto_fix(tmp_path, monkeypatch):
    """Orphan mon (key not tracked as dead) in memorial box must be warned but never moved."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = make_state_with_link(status=LinkStatus.ALIVE)
    srv = _make_mock_server(state)

    pc_boxes = [{"box": 13, "slot": 0, "key": "ORPHAN:99", "species_id": 25, "nickname": "PIKACHU"}]
    srv._check_memorial_box_contamination("a", pc_boxes)

    assert not any(c.get("key") == "ORPHAN:99" for c in state.queued_commands["a"]), \
        "Orphan in memorial box must not trigger any auto-fix commands"


def test_contamination_quarantine_in_memorial_box_relocates(tmp_path, monkeypatch):
    """A quarantined pending-capture found in the memorial box must be relocated via party_mon+box_mon."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    from server.state import AreaStatus as _AS
    state = SoulLinkState()
    state.pokeballs_obtained = {"a": True, "b": True}
    state.party_size = {"a": 1, "b": 1}
    state.pending_captures["route_1"] = {
        "a": MonInfo(key="A:99", level=5, nickname="ODDISH", species=43)
    }
    state.area_states["route_1"] = _AS.PENDING_B

    srv = _make_mock_server(state)
    pc_boxes = [{"box": 13, "slot": 0, "key": "A:99", "species_id": 43, "nickname": "ODDISH"}]
    srv._check_memorial_box_contamination("a", pc_boxes)

    assert has_cmd(state.queued_commands["a"], "party_mon", "A:99"), \
        "Quarantine in memorial box must queue party_mon to retrieve it"
    assert has_cmd(state.queued_commands["a"], "box_mon", "A:99"), \
        "Quarantine in memorial box must queue box_mon to re-deposit to normal box"

def test_killfeed_faint_records_cause_battle(tmp_path, monkeypatch):
    """After a faint event, the entry should have cause='battle' and killed_at set."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = make_state_with_link()
    state.handle_event("a", {"event": "faint", "key": "A:1"})
    entry = state.links[0]
    assert entry.cause == "battle"
    assert entry.killed_at is not None
    assert entry.initiating_player == "a"


def test_killfeed_faint_records_killer_from_message(tmp_path, monkeypatch):
    """Enriched faint msg with _killer_* fields populates entry.killer dict."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = make_state_with_link()
    state.handle_event("a", {
        "event": "faint", "key": "A:1",
        "_killer_species": 133,   # Eevee
        "_killer_level": 17,
        "_is_trainer": True,
    })
    entry = state.links[0]
    assert entry.killer is not None
    assert entry.killer["species"] == 133
    assert entry.killer["level"] == 17
    assert entry.killer["is_trainer"] is True
    assert entry.killer.get("trainer_name", "") == ""  # no _trainer_name in msg


def test_killfeed_faint_no_killer_when_species_zero(tmp_path, monkeypatch):
    """A faint msg with _killer_species=0 (unknown) should leave killer as None."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = make_state_with_link()
    state.handle_event("a", {
        "event": "faint", "key": "A:1",
        "_killer_species": 0,
        "_killer_level": 0,
        "_is_trainer": False,
    })
    assert state.links[0].killer is None


def test_killfeed_no_catch_records_dead_zone(tmp_path, monkeypatch):
    """After a no_catch event that creates a dead zone, cause should be 'dead_zone'."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState(retry_same_species=False)
    state.pokeballs_obtained = {"a": True, "b": True}
    # B captured first on route_2
    state.handle_event("b", {"event": "capture", "key": "B:20", "area_id": "route_2",
                              "level": 5, "species_id": 16})
    # A fails to catch — dead zone
    state.handle_event("a", {"event": "no_catch", "area_id": "route_2"})
    dead_entry = next((e for e in state.links if e.area_id == "route_2"), None)
    assert dead_entry is not None
    assert dead_entry.cause == "dead_zone"
    assert dead_entry.killed_at is not None
    assert dead_entry.initiating_player == "a"


def test_killfeed_whiteout_records_cause(tmp_path, monkeypatch):
    """After a whiteout, all newly killed entries should have cause='whiteout'."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = make_state_with_link()
    state.handle_event("a", {"event": "whiteout"})
    entry = state.links[0]
    assert entry.cause == "whiteout"
    assert entry.killed_at is not None
    assert entry.initiating_player == "a"


def test_killfeed_persists_through_save_load(tmp_path, monkeypatch):
    """Killfeed fields (killed_at, cause, killer, initiating_player) survive save/load."""
    links_path = str(tmp_path / "links.json")
    monkeypatch.setattr("server.state.LINKS_PATH", links_path)

    state = make_state_with_link()
    state.handle_event("a", {
        "event": "faint", "key": "A:1",
        "_killer_species": 25, "_killer_level": 10,
        "_is_trainer": False,
    })
    state._save()

    loaded = SoulLinkState.load()
    assert len(loaded.links) == 1
    entry = loaded.links[0]
    assert entry.cause == "battle"
    assert entry.killed_at is not None
    assert entry.initiating_player == "a"
    assert entry.killer is not None
    assert entry.killer["species"] == 25
    assert entry.killer["level"] == 10


# ── species clause reroll (no_catch suppression) ─────────────────────────────

def test_species_clause_reroll_suppresses_no_catch(tmp_path, monkeypatch):
    """no_catch with a species the player already has should NOT dead-zone the area."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState(species_lock=True)
    state.pokeballs_obtained = {"a": True, "b": True}
    # Player A has Taillow (species 304, FRLG internal ID) linked on route_1
    state.handle_event("a", {"event": "capture", "key": "AA:11", "area_id": "route_1",
                              "level": 5, "species_id": 304})
    state.handle_event("b", {"event": "capture", "key": "BB:22", "area_id": "route_1",
                              "level": 5, "species_id": 25})
    assert state.area_states["route_1"] == AreaStatus.LINKED

    # Player A encounters Swellow (305, same family as Taillow 304) on route_2 and fails to catch
    cmds = state.handle_event("a", {"event": "no_catch", "area_id": "route_2", "species_id": 305})
    # Area should NOT be dead-zoned — species clause reroll
    assert state.area_states.get("route_2", AreaStatus.UNSEEN) != AreaStatus.DEAD_ZONE
    # Should get a HUD message about the reroll
    assert any(c.get("cmd") == "gui_prompt" and "reroll" in c.get("text", "").lower() for c in cmds)
    # Should get an unresolve_area command
    assert any(c.get("cmd") == "unresolve_area" and c.get("area_id") == "route_2" for c in cmds)


def test_species_clause_reroll_evolution_family(tmp_path, monkeypatch):
    """Encountering an evolution of an owned mon should also trigger reroll."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState(species_lock=True)
    state.pokeballs_obtained = {"a": True, "b": True}
    # Player A has Pidgey (16) linked
    state.handle_event("a", {"event": "capture", "key": "AA:11", "area_id": "route_1",
                              "level": 5, "species_id": 16})
    state.handle_event("b", {"event": "capture", "key": "BB:22", "area_id": "route_1",
                              "level": 5, "species_id": 25})
    # Player A encounters Pidgeotto (17, same family) and fails to catch
    cmds = state.handle_event("a", {"event": "no_catch", "area_id": "route_2", "species_id": 17})
    assert state.area_states.get("route_2", AreaStatus.UNSEEN) != AreaStatus.DEAD_ZONE
    assert any(c.get("cmd") == "unresolve_area" for c in cmds)


def test_species_clause_no_reroll_for_different_family(tmp_path, monkeypatch):
    """no_catch for a species the player doesn't own should still dead-zone."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState(species_lock=True, retry_same_species=False)
    state.pokeballs_obtained = {"a": True, "b": True}
    # Player A has Pidgey (16)
    state.handle_event("a", {"event": "capture", "key": "AA:11", "area_id": "route_1",
                              "level": 5, "species_id": 16})
    state.handle_event("b", {"event": "capture", "key": "BB:22", "area_id": "route_1",
                              "level": 5, "species_id": 25})
    # Player A encounters Rattata (19, different family) and fails to catch
    state.handle_event("a", {"event": "no_catch", "area_id": "route_2", "species_id": 19})
    assert state.area_states["route_2"] == AreaStatus.DEAD_ZONE


def test_species_clause_no_reroll_when_lock_disabled(tmp_path, monkeypatch):
    """Species clause reroll only applies when species_lock is enabled."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState(species_lock=False, retry_same_species=False)
    state.pokeballs_obtained = {"a": True, "b": True}
    state.handle_event("a", {"event": "capture", "key": "AA:11", "area_id": "route_1",
                              "level": 5, "species_id": 16})
    state.handle_event("b", {"event": "capture", "key": "BB:22", "area_id": "route_1",
                              "level": 5, "species_id": 25})
    # Same family encounter but species lock OFF — should dead-zone normally
    state.handle_event("a", {"event": "no_catch", "area_id": "route_2", "species_id": 17})
    assert state.area_states["route_2"] == AreaStatus.DEAD_ZONE


def test_species_clause_dead_pair_no_reroll(tmp_path, monkeypatch):
    """Dead pairs should NOT trigger species clause — player no longer 'has' that family."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState(species_lock=True, retry_same_species=False)
    state.pokeballs_obtained = {"a": True, "b": True}
    # Link and kill a Pidgey pair
    state.handle_event("a", {"event": "capture", "key": "AA:11", "area_id": "route_1",
                              "level": 5, "species_id": 16})
    state.handle_event("b", {"event": "capture", "key": "BB:22", "area_id": "route_1",
                              "level": 5, "species_id": 25})
    state.handle_event("a", {"event": "faint", "key": "AA:11"})
    assert state.links[0].status == LinkStatus.DEAD
    # Encountering Pidgey on route_2 should NOT reroll (dead pair doesn't count)
    state.handle_event("a", {"event": "no_catch", "area_id": "route_2", "species_id": 16})
    assert state.area_states["route_2"] == AreaStatus.DEAD_ZONE


def test_species_clause_reroll_b_side(tmp_path, monkeypatch):
    """Species clause reroll works for player B too."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState(species_lock=True)
    state.pokeballs_obtained = {"a": True, "b": True}
    state.handle_event("a", {"event": "capture", "key": "AA:11", "area_id": "route_1",
                              "level": 5, "species_id": 25})
    state.handle_event("b", {"event": "capture", "key": "BB:22", "area_id": "route_1",
                              "level": 5, "species_id": 304})  # Taillow (FRLG internal ID)
    # Player B encounters Swellow (305) and fails to catch
    cmds = state.handle_event("b", {"event": "no_catch", "area_id": "route_2", "species_id": 305})
    assert state.area_states.get("route_2", AreaStatus.UNSEEN) != AreaStatus.DEAD_ZONE
    assert any(c.get("cmd") == "unresolve_area" for c in cmds)


def test_species_clause_no_species_in_no_catch(tmp_path, monkeypatch):
    """no_catch without species_id should dead-zone normally even with species lock."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState(species_lock=True, retry_same_species=False)
    state.pokeballs_obtained = {"a": True, "b": True}
    state.handle_event("a", {"event": "capture", "key": "AA:11", "area_id": "route_1",
                              "level": 5, "species_id": 16})
    state.handle_event("b", {"event": "capture", "key": "BB:22", "area_id": "route_1",
                              "level": 5, "species_id": 25})
    # no_catch with no species_id (old client or read failure)
    state.handle_event("a", {"event": "no_catch", "area_id": "route_2"})
    assert state.area_states["route_2"] == AreaStatus.DEAD_ZONE


def test_dupes_clause_partner_pending_capture(tmp_path, monkeypatch):
    """no_catch for same species as partner's pending capture should reroll, not dead-zone."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState(species_lock=True)
    state.pokeballs_obtained = {"a": True, "b": True}
    # B captures Poochyena on route_1 — area goes PENDING_A
    state.handle_event("b", {"event": "capture", "key": "BB:22", "area_id": "route_1",
                              "level": 3, "species_id": 286})
    assert state.area_states["route_1"] in (AreaStatus.PENDING_A, AreaStatus.PENDING_BOTH)
    # A encounters Poochyena (same species 286) on route_1 and fails to catch
    cmds = state.handle_event("a", {"event": "no_catch", "area_id": "route_1", "species_id": 286})
    # Should NOT dead-zone — dupes clause reroll
    assert state.area_states["route_1"] != AreaStatus.DEAD_ZONE
    assert any(c.get("cmd") == "gui_prompt" and "reroll" in c.get("text", "").lower() for c in cmds)
    assert any(c.get("cmd") == "unresolve_area" and c.get("area_id") == "route_1" for c in cmds)


def test_dupes_clause_partner_pending_evo_family(tmp_path, monkeypatch):
    """no_catch for evo family of partner's pending capture should also reroll."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState(species_lock=True)
    state.pokeballs_obtained = {"a": True, "b": True}
    # B captures Pidgey (16) on route_2
    state.handle_event("b", {"event": "capture", "key": "BB:33", "area_id": "route_2",
                              "level": 5, "species_id": 16})
    # A encounters Pidgeotto (17, same family) on route_2 and fails
    cmds = state.handle_event("a", {"event": "no_catch", "area_id": "route_2", "species_id": 17})
    assert state.area_states["route_2"] != AreaStatus.DEAD_ZONE
    assert any(c.get("cmd") == "unresolve_area" for c in cmds)


def test_dupes_clause_different_species_still_dead_zones(tmp_path, monkeypatch):
    """no_catch for a DIFFERENT species than partner's capture should still dead-zone."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState(species_lock=True, retry_same_species=False)
    state.pokeballs_obtained = {"a": True, "b": True}
    # B captures Poochyena (286) on route_1
    state.handle_event("b", {"event": "capture", "key": "BB:22", "area_id": "route_1",
                              "level": 3, "species_id": 286})
    # A encounters Rattata (19, different family) on route_1 and fails
    state.handle_event("a", {"event": "no_catch", "area_id": "route_1", "species_id": 19})
    assert state.area_states["route_1"] == AreaStatus.DEAD_ZONE


# ── Dupes clause: concurrent battle detection (Check 3) ──────────────────────

def test_dupes_clause_concurrent_same_species(tmp_path, monkeypatch):
    """B enters battle on same area as A (concurrent) with same species → reroll via Check 3."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState(species_lock=True)
    state.pokeballs_obtained = {"a": True, "b": True}
    # Neither player has captured yet — area is UNSEEN.
    # Call check_dupe_on_encounter for B with A's concurrent battle species passed in.
    fired = state.check_dupe_on_encounter("b", "route_1", 16, partner_battle_species=16)
    assert fired is True
    # Area should NOT be dead-zoned — B gets a reroll
    assert state.area_states.get("route_1") != AreaStatus.DEAD_ZONE
    assert "route_1" in state.dupe_notified_areas["b"]
    cmds = state.queued_commands["b"]
    assert any(c.get("cmd") == "gui_prompt" and "reroll" in c.get("text", "").lower() for c in cmds)
    assert any(c.get("cmd") == "unresolve_area" and c.get("area_id") == "route_1" for c in cmds)


def test_dupes_clause_concurrent_evo_family(tmp_path, monkeypatch):
    """Concurrent battle with an evolution of the same family also triggers reroll."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState(species_lock=True)
    state.pokeballs_obtained = {"a": True, "b": True}
    # A is in battle with Pidgey (16), B encounters Pidgeotto (17, same family).
    fired = state.check_dupe_on_encounter("b", "route_2", 17, partner_battle_species=16)
    assert fired is True
    assert "route_2" in state.dupe_notified_areas["b"]


def test_dupes_clause_concurrent_zero_partner(tmp_path, monkeypatch):
    """No reroll when partner_battle_species is 0 (partner not in battle)."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState(species_lock=True)
    state.pokeballs_obtained = {"a": True, "b": True}
    fired = state.check_dupe_on_encounter("b", "route_1", 16, partner_battle_species=0)
    assert fired is False
    assert "route_1" not in state.dupe_notified_areas["b"]


def test_dupes_clause_concurrent_ignored_once_partner_has_pending_here(tmp_path, monkeypatch):
    """Check 3 is for the case where NEITHER player has captured yet, as its own comment says.

    Live run_20260926_225435, route_2: A captured at 19:17:17, then at 19:17:29 B's Rattata was
    rerolled as "same family as partner's concurrent battle Rattata". A's route_2 slot was already
    filled -- a second capture in one area is retired on arrival (see the "second capture in area"
    guard) -- so whatever A was battling could never link against B's encounter. B lost a legal
    encounter to a collision that cannot happen.
    """
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState(species_lock=True)
    state.pokeballs_obtained = {"a": True, "b": True}
    # A catches Pidgey (16) on route_2: their slot for this area is now filled.
    state.handle_event("a", {"event": "capture", "key": "AA:77", "area_id": "route_2",
                             "level": 3, "species_id": 16})
    assert state.pending_captures["route_2"]["a"].species == 16
    # A is now in a wild battle with a Rattata (19) on route_2 that they cannot catch for the area,
    # and B encounters a Rattata of their own.
    fired = state.check_dupe_on_encounter("b", "route_2", 19, partner_battle_species=19)
    assert fired is False
    assert "route_2" not in state.dupe_notified_areas["b"]
    # B keeps the "partner caught" hud_show from A's capture; what must NOT appear is a reroll.
    assert not any(c.get("cmd") == "unresolve_area" for c in state.queued_commands["b"])
    assert not any("reroll" in str(c.get("text", "")).lower() for c in state.queued_commands["b"])


def test_dupes_clause_concurrent_still_fires_when_partner_pending_is_elsewhere(tmp_path, monkeypatch):
    """The guard above is scoped to THIS area: a pending capture on another route does not
    license a concurrent-battle collision here."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState(species_lock=True)
    state.pokeballs_obtained = {"a": True, "b": True}
    state.handle_event("a", {"event": "capture", "key": "AA:78", "area_id": "route_1",
                             "level": 3, "species_id": 16})
    fired = state.check_dupe_on_encounter("b", "route_2", 19, partner_battle_species=19)
    assert fired is True
    assert "route_2" in state.dupe_notified_areas["b"]


def test_dupes_clause_concurrent_no_catch_suppresses_dead_zone(tmp_path, monkeypatch):
    """After Check 3 fires, B fleeing (no_catch) should not dead-zone the area."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState(species_lock=True)
    state.pokeballs_obtained = {"a": True, "b": True}
    # Simulate B's battle start triggering Check 3 (A is in concurrent battle same species).
    state.check_dupe_on_encounter("b", "route_1", 16, partner_battle_species=16)
    assert "route_1" in state.dupe_notified_areas["b"]
    # B flees — no_catch should suppress dead zone.
    cmds = state.handle_event("b", {"event": "no_catch", "area_id": "route_1", "species_id": 16})
    assert state.area_states.get("route_1") != AreaStatus.DEAD_ZONE
    assert any(c.get("cmd") == "unresolve_area" and c.get("area_id") == "route_1" for c in cmds)


def test_dupes_clause_cross_player_alive_link_encounter(tmp_path, monkeypatch):
    """A-side Mareep in an alive link should block B's Mareep encounter elsewhere."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState(species_lock=True)
    state.pokeballs_obtained = {"a": True, "b": True}
    state.links.append(LinkEntry(
        area_id="viridian_city",
        a=MonInfo(key="AA:11", nickname="Watame", species=180),
        b=MonInfo(key="BB:11", nickname="Rat", species=19),
        status=LinkStatus.ALIVE,
    ))
    state._save()

    fired = state.check_dupe_on_encounter("b", "route_3", 180)

    assert fired is True
    assert "route_3" in state.dupe_notified_areas["b"]


def test_dupes_clause_cross_player_alive_link_capture(tmp_path, monkeypatch):
    """A-side Mareep in an alive link should reject B's Mareep capture elsewhere."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState(species_lock=True)
    state.pokeballs_obtained = {"a": True, "b": True}
    state.links.append(LinkEntry(
        area_id="viridian_city",
        a=MonInfo(key="AA:11", nickname="Watame", species=180),
        b=MonInfo(key="BB:11", nickname="Rat", species=19),
        status=LinkStatus.ALIVE,
    ))
    state._save()

    cmds = state.handle_event("b", {
        "event": "capture",
        "key": "CC:33",
        "area_id": "route_3",
        "level": 5,
        "species_id": 180,
    })

    assert any(c.get("cmd") == "force_faint" and c.get("key") == "CC:33" for c in cmds)


def test_dupes_clause_cross_player_pending_other_area(tmp_path, monkeypatch):
    """A pending Mareep in another area should block B's Mareep encounter."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState(species_lock=True)
    state.pokeballs_obtained = {"a": True, "b": True}
    state.handle_event("a", {
        "event": "capture",
        "key": "AA:11",
        "area_id": "viridian_city",
        "level": 5,
        "species_id": 180,
    })

    fired = state.check_dupe_on_encounter("b", "route_3", 180)

    assert fired is True


def test_dupes_clause_cross_player_evo_family(tmp_path, monkeypatch):
    """A-side Pikachu should block B's Pichu encounter as the same evo family."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState(species_lock=True)
    state.pokeballs_obtained = {"a": True, "b": True}
    state.links.append(LinkEntry(
        area_id="viridian_forest",
        a=MonInfo(key="AA:22", nickname="Pika", species=25),
        b=MonInfo(key="BB:22", nickname="Nido", species=32),
        status=LinkStatus.ALIVE,
    ))
    state._save()

    fired = state.check_dupe_on_encounter("b", "route_3", 172)

    assert fired is True


def test_dupes_clause_cross_player_link_violation(tmp_path, monkeypatch):
    """Cross-player roster dupes should be caught during link formation too."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState(species_lock=True)
    state.pokeballs_obtained = {"a": True, "b": True}
    state.links.append(LinkEntry(
        area_id="viridian_city",
        a=MonInfo(key="AA:11", nickname="Watame", species=180),
        b=MonInfo(key="BB:11", nickname="Rat", species=19),
        status=LinkStatus.ALIVE,
    ))

    a_mon = MonInfo(key="AA:99", nickname="Bird", species=16)
    b_mon = MonInfo(key="BB:99", nickname="Sheep", species=180)
    result = state._check_link_violation(a_mon, b_mon)

    assert result is not None
    assert result[1] == "b"


def test_dupes_clause_cross_player_alive_link_no_catch(tmp_path, monkeypatch):
    """A-side dupes should also suppress B's no_catch dead zone via reroll."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState(species_lock=True)
    state.pokeballs_obtained = {"a": True, "b": True}
    state.links.append(LinkEntry(
        area_id="viridian_city",
        a=MonInfo(key="AA:11", nickname="Watame", species=180),
        b=MonInfo(key="BB:11", nickname="Rat", species=19),
        status=LinkStatus.ALIVE,
    ))
    state._save()

    cmds = state.handle_event("b", {"event": "no_catch", "area_id": "route_3", "species_id": 180})

    assert state.area_states.get("route_3", AreaStatus.UNSEEN) != AreaStatus.DEAD_ZONE
    assert any(c.get("cmd") == "unresolve_area" and c.get("area_id") == "route_3" for c in cmds)


# ── Extended species data tests (CFRU/RR internal IDs) ───────────────────────

def test_species_names_gen1_unchanged():
    """Gen 1-2 species IDs (1-251) must match NatDex names."""
    from server.pokemon_data import SPECIES_NAMES
    assert SPECIES_NAMES[1] == "Bulbasaur"
    assert SPECIES_NAMES[25] == "Pikachu"
    assert SPECIES_NAMES[150] == "Mewtwo"
    assert SPECIES_NAMES[251] == "Celebi"


def test_species_names_gen3_cfru_ids():
    """Gen 3 species must use FRLG internal IDs, NOT NatDex."""
    from server.pokemon_data import SPECIES_NAMES
    assert SPECIES_NAMES.get(277) == "Treecko"  # FRLG internal, not NatDex 252
    assert SPECIES_NAMES.get(280) == "Torchic"  # FRLG internal, not NatDex 255
    assert SPECIES_NAMES.get(283) == "Mudkip"   # FRLG internal, not NatDex 258
    # NatDex 252-276 should NOT exist (FRLG gap)
    for i in range(252, 277):
        assert i not in SPECIES_NAMES, f"ID {i} should be a gap"


def test_species_names_gen4_plus():
    """Gen 4+ species should be present at CFRU IDs."""
    from server.pokemon_data import SPECIES_NAMES
    assert SPECIES_NAMES.get(440) == "Turtwig"
    assert SPECIES_NAMES.get(443) == "Chimchar"
    assert SPECIES_NAMES.get(446) == "Piplup"


def test_evo_family_gen3_cfru_ids():
    """Evolution families must use FRLG internal IDs for Gen 3."""
    from server.pokemon_data import base_form
    # Treecko line: 277→278→279 in FRLG internal order
    assert base_form(278) == 277  # Grovyle → Treecko
    assert base_form(279) == 277  # Sceptile → Treecko
    # Taillow/Swellow: 304/305
    assert base_form(305) == 304  # Swellow → Taillow
    # Ralts line: 392/393/394
    assert base_form(393) == 392  # Kirlia → Ralts
    assert base_form(394) == 392  # Gardevoir → Ralts


def test_evo_family_cross_gen():
    """Cross-generation evolutions must link to original base form."""
    from server.pokemon_data import SPECIES_NAMES, base_form
    # Electivire → Elekid (cross-gen: Gen4 evo of Gen1 mon)
    electivire_id = next(k for k, v in SPECIES_NAMES.items() if v == "Electivire")
    elekid_id = next(k for k, v in SPECIES_NAMES.items() if v == "Elekid")
    assert base_form(electivire_id) == elekid_id
    # All Eeveelutions share base form Eevee (133)
    eevee_evos = ["Vaporeon", "Jolteon", "Flareon", "Espeon", "Umbreon"]
    for name in eevee_evos:
        evo_id = next(k for k, v in SPECIES_NAMES.items() if v == name)
        assert base_form(evo_id) == 133, f"{name} (ID {evo_id}) should map to Eevee (133)"


def test_evo_family_gen1_gen2_unchanged():
    """Gen 1-2 evo families at NatDex IDs must still work."""
    from server.pokemon_data import base_form
    # Pidgey line (Gen 1, IDs unchanged)
    assert base_form(17) == 16   # Pidgeotto → Pidgey
    assert base_form(18) == 16   # Pidgeot → Pidgey
    # Larvitar line (Gen 2)
    assert base_form(247) == 246  # Pupitar → Larvitar
    assert base_form(248) == 246  # Tyranitar → Larvitar


def test_gender_ratio_gen3_cfru_ids():
    """Gender ratios must use FRLG internal IDs for Gen 3."""
    from server.pokemon_data import GENDER_RATIO
    # Beldum line is genderless (398-400 in CFRU)
    assert GENDER_RATIO.get(398) == 255  # Beldum
    assert GENDER_RATIO.get(399) == 255  # Metang
    assert GENDER_RATIO.get(400) == 255  # Metagross


def test_species_lock_gen4_cross_evo(tmp_path, monkeypatch):
    """Species lock should reject a Gen 4 cross-gen evolution pair."""
    from server.pokemon_data import SPECIES_NAMES
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState(species_lock=True, is_rr=True)
    state.pokeballs_obtained = {"a": True, "b": True}
    # Rhyhorn = 111 (Gen 1, CFRU = NatDex for Gen 1)
    rhyhorn_id = 111
    rhyperior_id = next(k for k, v in SPECIES_NAMES.items() if v == "Rhyperior")
    # A captures Rhyhorn, B captures Pikachu on route_1
    state.handle_event("a", {"event": "capture", "key": "AA:11", "area_id": "route_1",
                              "level": 5, "species_id": rhyhorn_id})
    state.handle_event("b", {"event": "capture", "key": "BB:22", "area_id": "route_1",
                              "level": 5, "species_id": 25})
    # A captures Rhyperior on route_2 — same evo family, should be rejected
    cmds = state.handle_event("a", {"event": "capture", "key": "CC:33", "area_id": "route_2",
                                     "level": 40, "species_id": rhyperior_id})
    # The capture should be force-fainted (violation)
    assert any(c.get("cmd") == "force_faint" and c.get("key") == "CC:33" for c in cmds)


# ── game over detection ──────────────────────────────────────────────────────

def test_game_over_no_false_positive_fresh_run(tmp_path, monkeypatch):
    """No game-over on a fresh state with pokeballs but no links."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState()
    state.pokeballs_obtained = {"a": True, "b": True}
    state._check_game_over()
    assert not state.run_over


def test_game_over_no_false_positive_dead_zone_only(tmp_path, monkeypatch):
    """No game-over when only dead-zone entries exist (no real linked pair)."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState()
    state.pokeballs_obtained = {"a": True, "b": True}
    # Simulate a dead zone: area entered by A, B didn't catch -> dead_zone
    # This creates a link entry where b is None (no real pair)
    entry = LinkEntry(area_id="route_1", a=MonInfo(key="A:1", level=5), b=None,
                      status=LinkStatus.DEAD)
    state.links.append(entry)
    state.area_states["route_1"] = AreaStatus.DEAD_ZONE
    state._check_game_over()
    assert not state.run_over


def test_game_over_triggers_when_last_alive_dies(tmp_path, monkeypatch):
    """Game over triggers when the last alive linked pair dies."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = make_state_with_link()
    # Faint A's mon — kills the only alive pair
    cmds_a = state.handle_event("a", {"event": "faint", "key": "A:1"})
    assert state.run_over, "run should be over after last alive link dies"
    # Both players should get game_over
    cmds_b = state.handle_event("b", {"event": "tick"})
    assert has_cmd(cmds_b, "game_over"), "B should receive game_over"
    assert has_cmd(cmds_a, "game_over"), "A should receive game_over"


def test_game_over_not_triggered_with_pending_captures(tmp_path, monkeypatch):
    """No game-over when pending captures remain (could still form a link)."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = make_state_with_link()
    # A captures on route_2 — pending capture exists
    state.handle_event("a", {"event": "capture", "key": "C:3", "area_id": "route_2",
                              "level": 10})
    # Kill the only alive link
    state.handle_event("a", {"event": "faint", "key": "A:1"})
    assert not state.run_over, "shouldn't be over while pending captures exist"


def test_game_over_triggers_after_no_catch_clears_last_pending(tmp_path, monkeypatch):
    """Game over triggers when no_catch clears the last pending capture."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = make_state_with_link()
    state.retry_same_species = False
    # A captures on route_2
    state.handle_event("a", {"event": "capture", "key": "C:3", "area_id": "route_2",
                              "level": 10})
    # Kill the only alive link
    state.handle_event("a", {"event": "faint", "key": "A:1"})
    assert not state.run_over
    # B no_catch on route_2 — clears the pending capture
    state.handle_event("b", {"event": "no_catch", "area_id": "route_2"})
    assert state.run_over, "should be over after no_catch clears last pending"


def test_game_over_persists_through_save_load(tmp_path, monkeypatch):
    """run_over flag survives save/load cycle."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = make_state_with_link()
    state.handle_event("a", {"event": "faint", "key": "A:1"})
    assert state.run_over
    # Reload from same directory (monkeypatched LINKS_PATH)
    state2 = SoulLinkState.load()
    assert state2.run_over


def test_game_over_resent_on_hello_reconnect(tmp_path, monkeypatch):
    """game_over command is re-sent when a player reconnects after run is over."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = make_state_with_link()
    state.handle_event("a", {"event": "faint", "key": "A:1"})
    assert state.run_over
    # Drain queued commands
    state.handle_event("a", {"event": "tick"})
    state.handle_event("b", {"event": "tick"})
    # Reconnect as player B with hello
    cmds = state.handle_event("b", {"event": "hello", "party": [
        {"key": "B:2", "hp": 0, "maxHP": 30}
    ]})
    assert has_cmd(cmds, "game_over"), "game_over should be re-sent on reconnect"


def test_game_over_not_triggered_with_alive_links(tmp_path, monkeypatch):
    """No game-over when alive links still exist."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = make_state_with_link()
    # Add a second alive pair
    entry2 = LinkEntry(area_id="route_2",
                       a=MonInfo(key="C:3", level=10),
                       b=MonInfo(key="D:4", level=12),
                       status=LinkStatus.ALIVE)
    state.links.append(entry2)
    state._index_entry(entry2)
    state.area_states["route_2"] = AreaStatus.LINKED
    state.party_keys["a"].add("C:3")
    state.party_keys["b"].add("D:4")
    # Kill one pair — still have another alive
    state.handle_event("a", {"event": "faint", "key": "A:1"})
    assert not state.run_over, "shouldn't be over while alive links remain"


def test_game_over_gated_by_nuzlocke_start(tmp_path, monkeypatch):
    """No game-over if pokeballs_obtained is false for either player."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = make_state_with_link()
    # Revert B's pokeball status
    state.pokeballs_obtained["b"] = False
    # Kill the only alive pair
    state.handle_event("a", {"event": "faint", "key": "A:1"})
    assert not state.run_over, "shouldn't trigger before both players have nuzlocke active"


# ── Identity lock tests ──────────────────────────────────────────────────────

def test_identity_lock_first_hello_sets_identity(tmp_path, monkeypatch):
    """First hello with a party locks the player's identity."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState()
    state.handle_event("a", {
        "event": "hello",
        "trainer_name": "Ash",
        "party": [{"key": "AAAA:1111", "hp": 50, "maxHP": 50, "level": 10}],
    })
    assert state.player_identity["a"]["ot_id"] == "1111"
    assert state.player_identity["a"]["trainer_name"] == "Ash"
    assert not state.identity_error


def test_identity_lock_same_ot_accepted(tmp_path, monkeypatch):
    """Reconnect with the same OT ID is accepted."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState()
    state.handle_event("a", {
        "event": "hello",
        "trainer_name": "Ash",
        "party": [{"key": "AAAA:1111", "hp": 50, "maxHP": 50, "level": 10}],
    })
    # Reconnect with same OT ID (different personality is fine)
    state.handle_event("a", {
        "event": "hello",
        "trainer_name": "Ash",
        "party": [{"key": "BBBB:1111", "hp": 45, "maxHP": 45, "level": 12}],
    })
    assert "a" not in state.identity_error


def test_identity_lock_wrong_ot_rejected(tmp_path, monkeypatch):
    """Reconnect with a different OT ID is rejected."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState()
    state.handle_event("a", {
        "event": "hello",
        "trainer_name": "Ash",
        "party": [{"key": "AAAA:1111", "hp": 50, "maxHP": 50, "level": 10}],
    })
    # Connect with wrong OT ID: a bigger party carrying blobs, so adoption would be visible
    blob = "AB" * state.adapter.party_blob_size()
    msg = {
        "event": "hello",
        "trainer_name": "Gary",
        "party": [{"key": f"CCC{i}:9999", "hp": 30, "maxHP": 30, "level": 8, "slot": i, "blob_hex": blob}
                  for i in range(3)],
    }
    size_before, blobs_before = state.party_size.get("a"), list(state.partner_blobs.get("a") or [])
    cmds = state.handle_event("a", msg)
    assert msg.get("_rejected") is True
    assert "a" in state.identity_error
    assert "9999" in state.identity_error["a"] or "mismatch" in state.identity_error["a"].lower()
    # Should have a hud_show command in the returned commands
    assert any(c["cmd"] == "hud_show" for c in cmds)
    # C-1: nothing about the wrong save is adopted, not even in memory
    assert state.party_size.get("a") == size_before == 1
    assert state.partner_blobs.get("a") == blobs_before


def test_identity_lock_wrong_ot_blocks_events(tmp_path, monkeypatch):
    """After rejection, queued commands still have the hud_show error."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState()
    state.pokeballs_obtained = {"a": True, "b": True}
    state.handle_event("a", {
        "event": "hello",
        "trainer_name": "Ash",
        "party": [{"key": "AAAA:1111", "hp": 50, "maxHP": 50, "level": 10}],
    })
    # Reject wrong identity
    msg = {
        "event": "hello",
        "trainer_name": "Gary",
        "party": [{"key": "CCCC:9999", "hp": 30, "maxHP": 30, "level": 8}],
    }
    state.handle_event("a", msg)
    assert state.identity_error.get("a")


def test_identity_lock_persists_across_reload(tmp_path, monkeypatch):
    """Identity lock survives save/load cycle."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState()
    state._links_path = str(tmp_path / "links.json")
    state._data_dir = str(tmp_path)
    state.handle_event("a", {
        "event": "hello",
        "trainer_name": "Ash",
        "party": [{"key": "AAAA:1111", "hp": 50, "maxHP": 50, "level": 10}],
    })
    state._save()

    # Reload
    state2 = SoulLinkState.load(data_dir=str(tmp_path))
    assert state2.player_identity["a"]["ot_id"] == "1111"

    # Wrong OT should still be rejected after reload
    msg = {
        "event": "hello",
        "trainer_name": "Gary",
        "party": [{"key": "CCCC:9999", "hp": 30, "maxHP": 30, "level": 8}],
    }
    state2.handle_event("a", msg)
    assert msg.get("_rejected") is True


def test_identity_lock_empty_party_skips_check(tmp_path, monkeypatch):
    """Hello with empty party (no OT ID) does not lock or reject."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState()
    state.handle_event("a", {"event": "hello", "party": []})
    assert "a" not in state.player_identity
    assert not state.identity_error


def test_identity_lock_independent_per_player(tmp_path, monkeypatch):
    """Each player slot has its own identity lock."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState()
    state.handle_event("a", {
        "event": "hello", "trainer_name": "Ash",
        "party": [{"key": "AAAA:1111", "hp": 50, "maxHP": 50, "level": 10}],
    })
    state.handle_event("b", {
        "event": "hello", "trainer_name": "Misty",
        "party": [{"key": "BBBB:2222", "hp": 40, "maxHP": 40, "level": 9}],
    })
    assert state.player_identity["a"]["ot_id"] == "1111"
    assert state.player_identity["b"]["ot_id"] == "2222"
    # Swap should fail
    msg_a = {
        "event": "hello", "trainer_name": "Misty",
        "party": [{"key": "DDDD:2222", "hp": 40, "maxHP": 40, "level": 9}],
    }
    state.handle_event("a", msg_a)
    assert msg_a.get("_rejected") is True


# ── Gift Area Clause Bypass ───────────────────────────────────────────────────
# Only fixed-species gift areas (where both players are guaranteed the SAME
# predetermined species with no choice) bypass clause checks.
# Player-choice areas (starters at oaks_lab, fossils at cinnabar_lab) do NOT bypass.

def test_gift_area_bypasses_species_clause(tmp_path, monkeypatch):
    """Both players catch same species in a fixed-species gift area — link must form despite species_lock."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState(species_lock=True)
    state.pokeballs_obtained = {"a": True, "b": True}
    # silph_co_7f: always Lapras (species 131)
    state.handle_event("a", {"event": "capture", "key": "AA000001:11111111",
                              "area_id": "silph_co_7f", "level": 25, "species_id": 131})
    cmds_b = state.handle_event("b", {"event": "capture", "key": "BB000001:22222222",
                                       "area_id": "silph_co_7f", "level": 25, "species_id": 131})
    assert not has_cmd(cmds_b, "force_faint"), "Fixed-species gift must bypass species clause"
    assert len(state.links) == 1
    assert state.area_states["silph_co_7f"] == AreaStatus.LINKED


def test_gift_area_bypasses_gender_clause(tmp_path, monkeypatch):
    """Both players catch same gender in a fixed-species gift area — link must form despite gender_lock."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState(gender_lock=True)
    state.pokeballs_obtained = {"a": True, "b": True}
    # route_4_pokecenter: always Magikarp; same personality low byte → same gender
    state.handle_event("a", {"event": "capture", "key": "000000FF:11111111",
                              "area_id": "route_4_pokecenter", "level": 5, "species_id": 129})
    cmds_b = state.handle_event("b", {"event": "capture", "key": "000000FF:22222222",
                                       "area_id": "route_4_pokecenter", "level": 5, "species_id": 129})
    assert not has_cmd(cmds_b, "force_faint"), "Fixed-species gift must bypass gender clause"
    assert len(state.links) == 1
    assert state.area_states["route_4_pokecenter"] == AreaStatus.LINKED


def test_gift_area_bypasses_type_clause(tmp_path, monkeypatch):
    """Both players catch mons with shared types in a fixed-species gift area — link must form despite type_lock."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState(type_lock=True)
    state.pokeballs_obtained = {"a": True, "b": True}
    # celadon_condominiums: always Eevee (species 133, Normal)
    state.handle_event("a", {"event": "capture", "key": "AA000001:11111111",
                              "area_id": "celadon_condominiums", "level": 25, "species_id": 133})
    cmds_b = state.handle_event("b", {"event": "capture", "key": "BB000001:22222222",
                                       "area_id": "celadon_condominiums", "level": 25, "species_id": 133})
    assert not has_cmd(cmds_b, "force_faint"), "Fixed-species gift must bypass type clause"
    assert len(state.links) == 1
    assert state.area_states["celadon_condominiums"] == AreaStatus.LINKED


def test_gift_area_bypasses_all_clauses(tmp_path, monkeypatch):
    """All three clauses active — same species in fixed-species gift area still links."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState(species_lock=True, gender_lock=True, type_lock=True)
    state.pokeballs_obtained = {"a": True, "b": True}
    # silph_co_7f: always Lapras (species 131)
    state.handle_event("a", {"event": "capture", "key": "AA000001:11111111",
                              "area_id": "silph_co_7f", "level": 25, "species_id": 131})
    cmds_b = state.handle_event("b", {"event": "capture", "key": "BB000001:22222222",
                                       "area_id": "silph_co_7f", "level": 25, "species_id": 131})
    assert not has_cmd(cmds_b, "force_faint"), "Fixed-species gift must bypass all clauses"
    assert len(state.links) == 1
    assert state.area_states["silph_co_7f"] == AreaStatus.LINKED


def test_non_fixed_gift_area_still_checks_clauses(tmp_path, monkeypatch):
    """Regular routes must still enforce species clause (fixed-species bypass doesn't apply)."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState(species_lock=True)
    state.pokeballs_obtained = {"a": True, "b": True}
    # Both catch Bulbasaur (species 1) at route_1 — clause should fire (not a gift area)
    state.handle_event("a", {"event": "capture", "key": "AA000001:11111111",
                              "area_id": "route_1", "level": 5, "species_id": 1})
    cmds_b = state.handle_event("b", {"event": "capture", "key": "BB000001:22222222",
                                       "area_id": "route_1", "level": 5, "species_id": 1})
    assert has_cmd(cmds_b, "force_faint"), "Regular route must still enforce species clause"
    assert len(state.links) == 0


def test_player_choice_gift_area_still_checks_clauses_at_link_formation(tmp_path, monkeypatch):
    """oaks_lab is a gift area but NOT fixed-species — species clause must still fire at link formation."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState(species_lock=True)
    state.pokeballs_obtained = {"a": True, "b": True}
    # Both pick Charmander (species 4) — starter choice can differ, so clause still applies
    state.handle_event("a", {"event": "capture", "key": "AA000004:11111111",
                              "area_id": "oaks_lab", "level": 5, "species_id": 4})
    cmds_b = state.handle_event("b", {"event": "capture", "key": "BB000004:22222222",
                                       "area_id": "oaks_lab", "level": 5, "species_id": 4})
    assert has_cmd(cmds_b, "force_faint"), "Non-fixed gift area must still enforce species clause"
    assert len(state.links) == 0


def test_fixed_gift_early_species_check_bypassed_when_player_has_existing_link(tmp_path, monkeypatch):
    """Player already has a Magikarp link — route_4_pokecenter Magikarp must NOT be rejected by early check."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState(species_lock=True)
    state.pokeballs_obtained = {"a": True, "b": True}
    # Pre-load an alive Magikarp link (from a water route) so early check would normally fire
    entry = LinkEntry(
        area_id="route_24",
        a=MonInfo(key="EXISTING_A:11111111", species=129, level=10),
        b=MonInfo(key="EXISTING_B:22222222", species=129, level=10),
        status=LinkStatus.ALIVE,
    )
    state.links.append(entry)
    state._index_entry(entry)
    state.area_states["route_24"] = AreaStatus.LINKED
    # Player A gets Magikarp from route_4_pokecenter — early species check must be bypassed
    cmds_a = state.handle_event("a", {"event": "capture", "key": "NEW_A:33333333",
                                       "area_id": "route_4_pokecenter", "level": 5, "species_id": 129})
    assert not has_cmd(cmds_a, "force_faint"), \
        "Fixed-gift Magikarp must bypass early per-player species check"
    assert state.pending_captures.get("route_4_pokecenter", {}).get("a") is not None


# ── Shiny Clause ──────────────────────────────────────────────────────────────

# Shiny key: personality=0x00010001, otId=0x00010001  →  XOR=0 (shiny)
SHINY_KEY = "00010001:00010001"
# Non-shiny key: personality=0x00FF0001, otId=0x00010001  →  XOR=254
NON_SHINY_KEY = "00FF0001:00010001"


def test_is_shiny_helper():
    assert is_shiny(SHINY_KEY) is True
    assert is_shiny(NON_SHINY_KEY) is False
    assert is_shiny("") is False
    assert is_shiny("bad") is False
    assert is_shiny("ZZZZ:XXXX") is False


def test_shiny_capture_in_dead_zone_is_kept(tmp_path, monkeypatch):
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState()
    state.pokeballs_obtained = {"a": True, "b": True}
    state.party_size = {"a": 2, "b": 2}
    state.area_states["route_1"] = AreaStatus.DEAD_ZONE

    cmds = state.handle_event("a", {"event": "capture", "key": SHINY_KEY, "area_id": "route_1", "species_id": 25})
    # Should NOT force_faint — shiny clause keeps it
    assert not has_cmd(cmds, "force_faint", SHINY_KEY)
    assert not has_cmd(cmds, "memorialize", SHINY_KEY)
    # Capturing player gets a shiny message (msgbox; native box or prompt fallback) + sound
    assert any("Shiny" in c.get("text", "") for c in cmds if c.get("cmd") == "msgbox")
    assert has_cmd(cmds, "play_sound")


def test_non_shiny_capture_in_dead_zone_still_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState()
    state.pokeballs_obtained = {"a": True, "b": True}
    state.party_size = {"a": 2, "b": 2}
    state.area_states["route_1"] = AreaStatus.DEAD_ZONE

    cmds = state.handle_event("a", {"event": "capture", "key": NON_SHINY_KEY, "area_id": "route_1"})
    assert has_cmd(cmds, "force_faint", NON_SHINY_KEY)


def test_shiny_capture_in_already_linked_area_is_kept(tmp_path, monkeypatch):
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = make_state_with_link(area="route_1")

    cmds = state.handle_event("a", {"event": "capture", "key": SHINY_KEY, "area_id": "route_1", "species_id": 25})
    assert not has_cmd(cmds, "force_faint", SHINY_KEY)


def test_shiny_as_second_capture_same_area_is_kept(tmp_path, monkeypatch):
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState()
    state.pokeballs_obtained = {"a": True, "b": True}
    state.party_size = {"a": 2, "b": 2}
    # First normal capture
    state.handle_event("a", {"event": "capture", "key": "AAAA0001:BBBB0001", "area_id": "route_2"})
    # Second capture is shiny — should be kept
    cmds = state.handle_event("a", {"event": "capture", "key": SHINY_KEY, "area_id": "route_2", "species_id": 25})
    assert not has_cmd(cmds, "force_faint", SHINY_KEY)


def test_shiny_bypasses_species_clause(tmp_path, monkeypatch):
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState(species_lock=True)
    state.pokeballs_obtained = {"a": True, "b": True}
    state.party_size = {"a": 2, "b": 2}
    # Existing alive link with species 25 (Pikachu)
    entry = LinkEntry(area_id="route_1",
                      a=MonInfo(key="AAAA0001:BBBB0001", level=5, species=25),
                      b=MonInfo(key="CCCC0001:DDDD0001", level=5, species=16),
                      status=LinkStatus.ALIVE)
    state.links.append(entry)
    state._index_entry(entry)
    state.area_states["route_1"] = AreaStatus.LINKED

    # Shiny capture of same species family — should be kept despite species lock
    cmds = state.handle_event("a", {"event": "capture", "key": SHINY_KEY, "area_id": "route_2", "species_id": 25})
    assert not has_cmd(cmds, "force_faint", SHINY_KEY)


def test_shiny_does_not_consume_area_encounter(tmp_path, monkeypatch):
    """Shiny bonus capture should unresolve the area so the normal encounter is preserved."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState()
    state.pokeballs_obtained = {"a": True, "b": True}
    state.party_size = {"a": 2, "b": 2}

    cmds = state.handle_event("a", {"event": "capture", "key": SHINY_KEY, "area_id": "route_3", "species_id": 25})
    # Should send unresolve_area so Lua doesn't mark route_3 as done
    assert has_cmd(cmds, "unresolve_area")
    assert any(c.get("area_id") == "route_3" for c in cmds if c.get("cmd") == "unresolve_area")
    # Area should NOT have a pending capture (shiny is outside the link system)
    assert "route_3" not in state.pending_captures


def test_shiny_in_resolved_area_skips_unresolve(tmp_path, monkeypatch):
    """Shiny in an already-resolved area should NOT send unresolve_area."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = make_state_with_link(area="route_1")

    cmds = state.handle_event("a", {"event": "capture", "key": SHINY_KEY, "area_id": "route_1", "species_id": 25})
    assert not any(c.get("cmd") == "unresolve_area" for c in cmds)


def test_shiny_duplicate_event_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState()
    state.pokeballs_obtained = {"a": True, "b": True}
    state.party_size = {"a": 2, "b": 2}

    state.handle_event("a", {"event": "capture", "key": SHINY_KEY, "area_id": "route_1", "species_id": 25})
    # Second identical event — should be a silent noop
    cmds = state.handle_event("a", {"event": "capture", "key": SHINY_KEY, "area_id": "route_1", "species_id": 25})
    assert not any("Shiny" in c.get("text", "") for c in cmds if c.get("cmd") == "gui_prompt")


def test_shiny_partner_gets_sound_and_gui_prompt(tmp_path, monkeypatch):
    """Partner should receive SE_SHINY and a GUI prompt when the other player catches a shiny."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState()
    state.pokeballs_obtained = {"a": True, "b": True}
    state.party_size = {"a": 2, "b": 2}

    # Player A catches a shiny
    state.handle_event("a", {"event": "capture", "key": SHINY_KEY, "area_id": "route_5", "species_id": 25})
    # Flush partner's queued commands via tick
    cmds_b = state.handle_event("b", {"event": "tick"})
    # Partner gets shiny sound (SE_SHINY = 95)
    assert any(c.get("cmd") == "play_sound" and c.get("sound") == 95 for c in cmds_b), \
        "Partner should hear SE_SHINY"
    # Partner gets a shiny notification (msgbox; native box when patched, else center prompt)
    assert any(c.get("cmd") == "msgbox" and "shiny" in c.get("text", "").lower() for c in cmds_b), \
        "Partner should see a shiny notification"


def test_shiny_activates_pokeballs_obtained(tmp_path, monkeypatch):
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState()
    state.pokeballs_obtained = {"a": False, "b": False}
    state.party_size = {"a": 2, "b": 2}

    state.handle_event("a", {"event": "capture", "key": SHINY_KEY, "area_id": "route_1", "species_id": 25})
    assert state.pokeballs_obtained["a"] is True


def test_shiny_exemption_box_to_party_not_blocked(tmp_path, monkeypatch):
    """When Player A has a shiny in party (6 mons), Player B retrieving a linked mon
    should be BLOCKED -- the old shiny exemption is removed; strict sync applies."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = make_state_with_link(a_key="A1:1", b_key="B1:1")
    # A has 5 linked mons + 1 shiny = 6 physical
    state.party_keys["a"] = {"A1:1", "A2:2", "A3:3", "A4:4", "A5:5", SHINY_KEY}
    state.party_keys["b"] = {"B1:1", "B2:2", "B3:3", "B4:4", "B5:5"}
    state.party_size = {"a": 6, "b": 5}
    state.bonus_keys["a"].add(SHINY_KEY)
    # Add more links so the linked partner for B6 (A6) is NOT in A's party
    state.links.append(LinkEntry(area_id="route_6",
                                 a=MonInfo(key="A6:6", nickname="MON6A", species=6),
                                 b=MonInfo(key="B6:6", nickname="MON6B", species=7),
                                 status=LinkStatus.ALIVE))
    state._index_entry(state.links[-1])
    # B retrieves B6 from box -- A's party is physically full (6), no exemption anymore
    cmds = state.handle_event("b", {"event": "box_to_party", "key": "B6:6"})
    # Should be bounced back (A's logical party is full: 5 linked + 1 unlinked shiny)
    assert any(c.get("cmd") == "box_mon" and c.get("key") == "B6:6" for c in cmds), \
        "B should be blocked — A's party is physically full (no wildcard exemption)"
    # B6 should NOT be in B's party_keys
    assert "B6:6" not in state.party_keys["b"]


def test_linked_deposit_with_shiny_in_party_still_syncs_partner(tmp_path, monkeypatch):
    """Depositing a linked mon must always sync the partner, even when a shiny is in party.
    The shiny occupies an extra slot but does not exempt linked-pair deposits from sync."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = make_state_with_link(a_key="A1:1", b_key="B1:1")
    # Second linked pair
    state.links.append(LinkEntry(area_id="route_2",
                                 a=MonInfo(key="A2:2", nickname="MON2A", species=4),
                                 b=MonInfo(key="B2:2", nickname="MON2B", species=5),
                                 status=LinkStatus.ALIVE))
    state._index_entry(state.links[-1])
    # A has 2 linked mons + 1 shiny; B has 2 linked mons (correctly synced)
    state.party_keys["a"] = {"A1:1", "A2:2", SHINY_KEY}
    state.party_keys["b"] = {"B1:1", "B2:2"}
    state.bonus_keys["a"].add(SHINY_KEY)
    state.party_size = {"a": 3, "b": 2}
    state._has_helld.add("b")
    # A deposits a linked mon — B's partner MUST be boxed regardless of the shiny
    state.handle_event("a", {"event": "party_to_box", "key": "A2:2"})
    cmds_b = state.handle_event("b", {"event": "tick"})
    assert any(c.get("cmd") == "box_mon" and c.get("key") == "B2:2" for c in cmds_b), \
        "Partner's linked mon must be boxed even when depositing player has a shiny in party"
    assert "B2:2" not in state.party_keys["b"]


def test_shiny_bypasses_gender_clause(tmp_path, monkeypatch):
    """Shiny capture should be kept as bonus even when gender_lock is active."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState(gender_lock=True)
    state.pokeballs_obtained = {"a": True, "b": True}
    state.party_size = {"a": 2, "b": 2}
    # B already has a pending capture on the same route
    state.handle_event("b", {"event": "capture", "key": NON_SHINY_KEY, "area_id": "route_2", "species_id": 25})
    # A captures a shiny — shiny check fires before gender clause evaluation
    cmds = state.handle_event("a", {"event": "capture", "key": SHINY_KEY, "area_id": "route_2", "species_id": 25})
    assert not has_cmd(cmds, "force_faint", SHINY_KEY)
    assert SHINY_KEY in state.bonus_keys["a"]


def test_shiny_bypasses_type_clause(tmp_path, monkeypatch):
    """Shiny capture should be kept as bonus even when type_lock is active."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState(type_lock=True)
    state.pokeballs_obtained = {"a": True, "b": True}
    state.party_size = {"a": 2, "b": 2}
    # B already has a pending capture on the same route
    state.handle_event("b", {"event": "capture", "key": NON_SHINY_KEY, "area_id": "route_2", "species_id": 25})
    # A captures a shiny — shiny check fires before type clause evaluation
    cmds = state.handle_event("a", {"event": "capture", "key": SHINY_KEY, "area_id": "route_2", "species_id": 25})
    assert not has_cmd(cmds, "force_faint", SHINY_KEY)
    assert SHINY_KEY in state.bonus_keys["a"]


def test_shiny_faint_does_not_propagate(tmp_path, monkeypatch):
    """A bonus (shiny) mon fainting should not trigger force_faint for the partner."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState()
    state.pokeballs_obtained = {"a": True, "b": True}
    state.party_size = {"a": 3, "b": 3}
    # A catches a shiny
    state.handle_event("a", {"event": "capture", "key": SHINY_KEY, "area_id": "route_1", "species_id": 25})
    assert SHINY_KEY in state.bonus_keys["a"]
    # A's shiny faints — no linked entry, so faint should be silent
    state.handle_event("a", {"event": "faint", "key": SHINY_KEY})
    cmds = state.handle_event("b", {"event": "tick"})
    assert not has_cmd(cmds, "force_faint"), "Shiny faint must not propagate to partner"
    assert SHINY_KEY not in state.party_keys["a"]


def test_shiny_capture_added_to_party_keys(tmp_path, monkeypatch):
    """Shiny captures must be added to party_keys so party sync math is correct."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState()
    state.pokeballs_obtained = {"a": True, "b": True}
    state.party_size = {"a": 2, "b": 2}
    state.handle_event("a", {"event": "capture", "key": SHINY_KEY, "area_id": "route_1", "species_id": 25})
    assert SHINY_KEY in state.party_keys["a"]


def test_shiny_bonus_keys_persist_across_reload(tmp_path, monkeypatch):
    """bonus_keys (shiny clause) must survive a save/load cycle."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState()
    state._links_path = str(tmp_path / "links.json")
    state._data_dir = str(tmp_path)
    state.pokeballs_obtained = {"a": True, "b": True}
    state.party_size = {"a": 2, "b": 2}
    state.handle_event("a", {"event": "capture", "key": SHINY_KEY, "area_id": "route_1", "species_id": 25})
    assert SHINY_KEY in state.bonus_keys["a"]
    state._save()
    state2 = SoulLinkState.load(data_dir=str(tmp_path))
    assert SHINY_KEY in state2.bonus_keys["a"], "bonus_keys must be restored after reload"
    assert SHINY_KEY not in state2.bonus_keys["b"]


def test_key_change_migrates_bonus_keys(tmp_path, monkeypatch):
    """key_change (nature change) must migrate a shiny mon's key in bonus_keys."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState()
    state.pokeballs_obtained = {"a": True, "b": True}
    state.party_size = {"a": 2, "b": 2}
    state.handle_event("a", {"event": "capture", "key": SHINY_KEY, "area_id": "route_1", "species_id": 25})
    assert SHINY_KEY in state.bonus_keys["a"]
    new_key = "00020001:00010001"
    state.handle_event("a", {"event": "key_change", "old_key": SHINY_KEY, "new_key": new_key})
    assert new_key in state.bonus_keys["a"], "New key must appear in bonus_keys after migration"
    assert SHINY_KEY not in state.bonus_keys["a"], "Old key must be removed from bonus_keys"


def test_shiny_with_pending_capture_skips_unresolve(tmp_path, monkeypatch):
    """If the player already has a normal pending capture on the area, a subsequent
    shiny capture must NOT send unresolve_area (the slot is already consumed)."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState()
    state.pokeballs_obtained = {"a": True, "b": True}
    state.party_size = {"a": 2, "b": 2}
    # A captures a normal mon first
    state.handle_event("a", {"event": "capture", "key": NON_SHINY_KEY, "area_id": "route_4"})
    assert state.pending_captures.get("route_4", {}).get("a") is not None
    # A then encounters and catches a shiny on the same route
    cmds = state.handle_event("a", {"event": "capture", "key": SHINY_KEY, "area_id": "route_4", "species_id": 25})
    # unresolve_area must NOT be sent — the area slot is already pending
    assert not any(c.get("cmd") == "unresolve_area" for c in cmds), \
        "unresolve_area must not be sent when player already has a pending capture on this area"
    # The normal pending capture must be unchanged
    assert state.pending_captures["route_4"]["a"].key == NON_SHINY_KEY


# ── key_change (nature change) ───────────────────────────────────────────────

def test_key_change_migrates_linked_mon(tmp_path, monkeypatch):
    """key_change should update the link entry's monKey and key index."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = make_state_with_link(a_key="AAAA:1111", b_key="BBBB:2222")
    state.handle_event("a", {"event": "key_change", "old_key": "AAAA:1111", "new_key": "CCCC:1111"})
    # Link entry updated
    assert state.links[0].a.key == "CCCC:1111"
    # Key index migrated
    assert "CCCC:1111" in state._key_index
    assert "AAAA:1111" not in state._key_index
    # Party keys migrated
    assert "CCCC:1111" in state.party_keys["a"]
    assert "AAAA:1111" not in state.party_keys["a"]


def test_key_change_migrates_pending_capture(tmp_path, monkeypatch):
    """key_change should update a pending (unlinked) capture's key."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState()
    state.pokeballs_obtained = {"a": True, "b": True}
    state.party_size = {"a": 2, "b": 2}
    # A captures in route_5
    state.handle_event("a", {"event": "capture", "key": "AA:11", "area_id": "route_5", "level": 5})
    assert state.pending_captures["route_5"]["a"].key == "AA:11"
    # Nature change
    state.handle_event("a", {"event": "key_change", "old_key": "AA:11", "new_key": "DD:11"})
    assert state.pending_captures["route_5"]["a"].key == "DD:11"


def test_key_change_migrates_mon_stats(tmp_path, monkeypatch):
    """key_change should migrate cached mon stats."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = make_state_with_link(a_key="AAAA:1111", b_key="BBBB:2222")
    state.mon_stats["a"]["AAAA:1111"] = {"level": 15, "maxHP": 50}
    state.handle_event("a", {"event": "key_change", "old_key": "AAAA:1111", "new_key": "CCCC:1111"})
    assert "CCCC:1111" in state.mon_stats["a"]
    assert "AAAA:1111" not in state.mon_stats["a"]
    assert state.mon_stats["a"]["CCCC:1111"]["level"] == 15


def test_key_change_migrates_queued_commands(tmp_path, monkeypatch):
    """key_change should update key references in queued commands for the player."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = make_state_with_link(a_key="AAAA:1111", b_key="BBBB:2222")
    # Queue a command for player A referencing A's key via an external path
    # (simulate: A has a pending memorialize or box_mon queued)
    state.queued_commands["a"].append({"cmd": "box_mon", "key": "AAAA:1111"})
    # Nature change — the key_change handler migrates queued commands THEN handle_event drains them
    cmds = state.handle_event("a", {"event": "key_change", "old_key": "AAAA:1111", "new_key": "CCCC:1111"})
    # The returned commands should contain the migrated key
    box_cmds = [c for c in cmds if c.get("cmd") == "box_mon"]
    assert len(box_cmds) == 1
    assert box_cmds[0]["key"] == "CCCC:1111"


def test_key_change_migrates_pending_memorials(tmp_path, monkeypatch):
    """key_change should migrate the old key in pending_memorials to the new key."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = make_state_with_link(a_key="AAAA:1111", b_key="BBBB:2222")
    state.pending_memorials["a"].add("AAAA:1111")
    state.handle_event("a", {"event": "key_change", "old_key": "AAAA:1111", "new_key": "CCCC:1111"})
    assert "CCCC:1111" in state.pending_memorials["a"]
    assert "AAAA:1111" not in state.pending_memorials["a"]


def test_key_change_faint_uses_new_key(tmp_path, monkeypatch):
    """After key_change, a faint on the new key should propagate correctly."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = make_state_with_link(a_key="AAAA:1111", b_key="BBBB:2222")
    # Nature change for player A
    state.handle_event("a", {"event": "key_change", "old_key": "AAAA:1111", "new_key": "CCCC:1111"})
    # Faint the new key
    state.handle_event("a", {"event": "faint", "key": "CCCC:1111"})
    # Partner should get force_faint
    cmds = state.handle_event("b", {"event": "tick"})
    assert has_cmd(cmds, "force_faint", "BBBB:2222")


# ── shiny clause — wildcard linked-mon slot ──────────────────────────────────

def test_wildcard_B_can_pull_linked_mon_when_A_party_full_with_shiny(tmp_path, monkeypatch):
    """When A's party is full because of a shiny, B pulling a linked mon from box
    should be BLOCKED (no wildcard exemption) — strict sync applies."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    from server.state import LinkEntry, LinkStatus, MonInfo
    state = SoulLinkState()
    state.pokeballs_obtained = {"a": True, "b": True}

    # Build 5 linked pairs, all in party
    for i in range(5):
        ak, bk = f"AA{i:02X}:1111", f"BB{i:02X}:2222"
        entry = LinkEntry(area_id=f"area{i}", a=MonInfo(key=ak), b=MonInfo(key=bk), status=LinkStatus.ALIVE)
        state.links.append(entry)
        state._index_entry(entry)
        state.party_keys["a"].add(ak)
        state.party_keys["b"].add(bk)

    # A catches a shiny — fills A's 6th party slot
    state.party_size = {"a": 6, "b": 5}
    state.handle_event("a", {"event": "capture", "key": SHINY_KEY, "area_id": "shiny_area", "species_id": 25})
    assert SHINY_KEY in state.bonus_keys["a"]
    assert len(state.party_keys["a"]) == 6  # 5 linked + 1 shiny

    # Build a 6th linked pair where neither mon is currently in party
    extra_a, extra_b = "AAFF:1111", "BBFF:2222"
    extra_entry = LinkEntry(area_id="area6", a=MonInfo(key=extra_a), b=MonInfo(key=extra_b), status=LinkStatus.ALIVE)
    state.links.append(extra_entry)
    state._index_entry(extra_entry)
    # (neither is in party_keys yet — both in box)

    # B pulls their 6th linked mon; A's party is full (shiny takes up the 6th slot)
    state.party_size["b"] = 5
    cmds = state.handle_event("b", {"event": "box_to_party", "key": extra_b})

    # Must be bounced back — A's party is physically full (5 linked + 1 shiny = 6)
    assert has_cmd(cmds, "box_mon", extra_b), "B should be blocked — A's party physically full (no wildcard exemption)"
    assert extra_b not in state.party_keys["b"], "extra_b must NOT be in B's party_keys"


def test_wildcard_B_linked_mon_boxed_when_A_shiny_faints(tmp_path, monkeypatch):
    """When A's shiny faints, no wildcard revocation occurs — the wildcard system is removed.
    B's extra linked mon was never moved to party (B was blocked), so nothing needs boxing."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    from server.state import LinkEntry, LinkStatus, MonInfo
    state = SoulLinkState()
    state.pokeballs_obtained = {"a": True, "b": True}

    # 5 matched linked pairs in party for both
    for i in range(5):
        ak, bk = f"AA{i:02X}:1111", f"BB{i:02X}:2222"
        entry = LinkEntry(area_id=f"area{i}", a=MonInfo(key=ak), b=MonInfo(key=bk), status=LinkStatus.ALIVE)
        state.links.append(entry)
        state._index_entry(entry)
        state.party_keys["a"].add(ak)
        state.party_keys["b"].add(bk)

    # A catches shiny (fills A's 6th slot)
    state.party_size = {"a": 6, "b": 5}
    state.handle_event("a", {"event": "capture", "key": SHINY_KEY, "area_id": "shiny_area", "species_id": 25})

    # 6th linked pair — B tries to pull (should be blocked, A is full)
    extra_a, extra_b = "AAFF:1111", "BBFF:2222"
    extra_entry = LinkEntry(area_id="area6", a=MonInfo(key=extra_a), b=MonInfo(key=extra_b), status=LinkStatus.ALIVE)
    state.links.append(extra_entry)
    state._index_entry(extra_entry)
    state.party_size["b"] = 5
    pull_cmds = state.handle_event("b", {"event": "box_to_party", "key": extra_b})
    # B is blocked (A's party is full with shiny occupying slot)
    assert has_cmd(pull_cmds, "box_mon", extra_b), "B should be blocked (A party full)"
    assert extra_b not in state.party_keys["b"]

    # A's shiny faints — no wildcard revocation should occur (nothing was granted)
    state.handle_event("a", {"event": "faint", "key": SHINY_KEY})
    cmds = state.handle_event("b", {"event": "tick"})
    assert not has_cmd(cmds, "box_mon", extra_b), "No box_mon revocation — wildcard system removed"
    assert not has_cmd(cmds, "memorialize", extra_b), "extra_b should not be memorialized"

    # Also: A deposits shiny — still no box_mon for extra_b
    state.party_keys["a"].add(SHINY_KEY)  # pretend shiny is still in party for deposit test
    state.handle_event("a", {"event": "party_to_box", "key": SHINY_KEY})
    cmds2 = state.handle_event("b", {"event": "tick"})
    assert not has_cmd(cmds2, "box_mon", extra_b), "B's extra linked mon must NOT be boxed when A deposits shiny"


# ── Shiny bonus pair — new paired behavior ───────────────────────────────────

def test_shiny_capture_queues_pending_bonus_for_partner(tmp_path, monkeypatch):
    """Catching a shiny adds the shiny's key to the partner's pending_bonus queue."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState()
    state.pokeballs_obtained = {"a": True, "b": True}
    state.handle_event("a", {"event": "capture", "key": SHINY_KEY, "area_id": "route_1", "species_id": 25})
    assert SHINY_KEY in state.bonus_keys["a"]
    assert list(state.pending_bonus["b"]) == [SHINY_KEY], "Partner B should have a pending bonus"
    assert len(state.pending_bonus["a"]) == 0, "Catcher A should have no pending bonus"


def test_bonus_pair_formed_on_partner_next_catch(tmp_path, monkeypatch):
    """Partner's next catch forms a bonus linked pair with the shiny."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState()
    state.pokeballs_obtained = {"a": True, "b": True}
    state.party_size = {"a": 1, "b": 1}
    # A catches shiny
    state.handle_event("a", {"event": "capture", "key": SHINY_KEY, "area_id": "route_1", "species_id": 25})
    assert list(state.pending_bonus["b"]) == [SHINY_KEY]
    # B catches their bonus partner
    BONUS_KEY = "DEAD:BEEF"
    state.handle_event("b", {"event": "capture", "key": BONUS_KEY, "area_id": "route_2", "species_id": 16})
    # Pair should be formed
    assert len(state.links) == 1, "One link should exist"
    entry = state.links[0]
    assert entry.area_id.startswith("_bonus_"), f"Area should be synthetic bonus ID, got {entry.area_id}"
    assert entry.a.key == SHINY_KEY
    assert entry.b.key == BONUS_KEY
    assert entry.status == LinkStatus.ALIVE
    # Shiny removed from bonus_keys
    assert SHINY_KEY not in state.bonus_keys["a"]
    # Pending bonus cleared
    assert len(state.pending_bonus["b"]) == 0


def test_bonus_pair_has_synthetic_area_id(tmp_path, monkeypatch):
    """Bonus pair area_id uses _bonus_ prefix with first 8 chars of shiny key."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState()
    state.pokeballs_obtained = {"a": True, "b": True}
    state.party_size = {"a": 1, "b": 1}
    state.handle_event("a", {"event": "capture", "key": SHINY_KEY, "area_id": "route_1", "species_id": 25})
    state.handle_event("b", {"event": "capture", "key": "CAFE:BABE", "area_id": "route_2", "species_id": 16})
    expected_area = f"_bonus_{SHINY_KEY[:8]}"
    assert state.links[0].area_id == expected_area


def test_bonus_pair_faint_propagation_shiny_faints(tmp_path, monkeypatch):
    """When the shiny half of a bonus pair faints, partner's bonus mon is force-fainted."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState()
    state.pokeballs_obtained = {"a": True, "b": True}
    state.party_size = {"a": 1, "b": 1}
    BONUS_KEY = "CAFE:BABE"
    state.handle_event("a", {"event": "capture", "key": SHINY_KEY, "area_id": "route_1", "species_id": 25})
    state.handle_event("b", {"event": "capture", "key": BONUS_KEY, "area_id": "route_2", "species_id": 16})
    # A's shiny faints
    state.handle_event("a", {"event": "faint", "key": SHINY_KEY})
    cmds_b = state.handle_event("b", {"event": "tick"})
    assert has_cmd(cmds_b, "force_faint", BONUS_KEY), "B's bonus mon should be force-fainted"


def test_bonus_pair_faint_propagation_bonus_mon_faints(tmp_path, monkeypatch):
    """When B's bonus mon faints, A's shiny is force-fainted."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState()
    state.pokeballs_obtained = {"a": True, "b": True}
    state.party_size = {"a": 1, "b": 1}
    BONUS_KEY = "CAFE:BABE"
    state.handle_event("a", {"event": "capture", "key": SHINY_KEY, "area_id": "route_1", "species_id": 25})
    state.handle_event("b", {"event": "capture", "key": BONUS_KEY, "area_id": "route_2", "species_id": 16})
    # B's bonus mon faints
    state.handle_event("b", {"event": "faint", "key": BONUS_KEY})
    cmds_a = state.handle_event("a", {"event": "tick"})
    assert has_cmd(cmds_a, "force_faint", SHINY_KEY), "A's shiny should be force-fainted"


def test_bonus_pair_party_sync_shiny_deposited(tmp_path, monkeypatch):
    """When A deposits the shiny, B's bonus mon is boxed."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState()
    state.pokeballs_obtained = {"a": True, "b": True}
    state.party_size = {"a": 2, "b": 2}
    BONUS_KEY = "CAFE:BABE"
    state.handle_event("a", {"event": "capture", "key": SHINY_KEY, "area_id": "route_1", "species_id": 25})
    state.handle_event("b", {"event": "capture", "key": BONUS_KEY, "area_id": "route_2", "species_id": 16})
    # A deposits shiny
    state.party_keys["a"].add(SHINY_KEY)
    state.party_keys["b"].add(BONUS_KEY)
    cmds = state.handle_event("a", {"event": "party_to_box", "key": SHINY_KEY})
    cmds_b = state.handle_event("b", {"event": "tick"})
    all_cmds = cmds + cmds_b
    assert has_cmd(all_cmds, "box_mon", BONUS_KEY), "B's bonus mon should be boxed when shiny is deposited"


def test_multiple_pending_bonuses_fifo(tmp_path, monkeypatch):
    """Two shinies caught by A create two pending bonus slots for B (FIFO)."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState()
    state.pokeballs_obtained = {"a": True, "b": True}
    state.party_size = {"a": 1, "b": 1}
    SHINY_KEY2 = "00020002:00020002"  # personality=0x00020002, otId=0x00020002 -> XOR=0 < 8
    # A catches two shinies
    state.handle_event("a", {"event": "capture", "key": SHINY_KEY, "area_id": "route_1", "species_id": 25})
    state.handle_event("a", {"event": "capture", "key": SHINY_KEY2, "area_id": "route_2", "species_id": 37})
    assert list(state.pending_bonus["b"]) == [SHINY_KEY, SHINY_KEY2], "FIFO order must be preserved"
    # B catches first bonus
    state.handle_event("b", {"event": "capture", "key": "CAFE:BABE", "area_id": "route_3", "species_id": 16})
    assert list(state.pending_bonus["b"]) == [SHINY_KEY2], "First pending consumed; second remains"
    # B catches second bonus
    state.handle_event("b", {"event": "capture", "key": "DEAD:BEEF", "area_id": "route_4", "species_id": 19})
    assert len(state.pending_bonus["b"]) == 0, "Both pending bonuses consumed"
    assert len(state.links) == 2, "Two bonus pairs should be formed"


def test_bonus_catch_violates_species_clause_retries(tmp_path, monkeypatch):
    """When B's bonus catch violates species clause, it is rejected and pending_bonus is preserved."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState(species_lock=True)
    state.pokeballs_obtained = {"a": True, "b": True}
    state.party_size = {"a": 1, "b": 1}
    # A catches shiny (Pikachu, species 25)
    state.handle_event("a", {"event": "capture", "key": SHINY_KEY, "area_id": "route_1", "species_id": 25})
    # B tries to catch Raichu (species 26, same evo family as Pikachu-25)
    # force_faint is queued during capture and returned immediately in that event's response
    cmds_b = state.handle_event("b", {"event": "capture", "key": "CAFE:BABE", "area_id": "route_2", "species_id": 26})
    # Should be rejected — force_faint is in the capture response, pending_bonus preserved
    assert has_cmd(cmds_b, "force_faint", "CAFE:BABE"), "Violating catch should be force-fainted"
    assert len(state.pending_bonus["b"]) == 1, "Pending bonus should be preserved after violation"
    assert len(state.links) == 0, "No link should be formed"


def test_bonus_catch_unresolves_area(tmp_path, monkeypatch):
    """After bonus pair forms, the area is unresolved so normal encounter is still available."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState()
    state.pokeballs_obtained = {"a": True, "b": True}
    state.party_size = {"a": 1, "b": 1}
    state.handle_event("a", {"event": "capture", "key": SHINY_KEY, "area_id": "route_1", "species_id": 25})
    # B enters route_2 (Lua side will mark it resolved)
    state.handle_event("b", {"event": "area_enter", "area_id": "route_2"})
    cmds = state.handle_event("b", {"event": "capture", "key": "CAFE:BABE", "area_id": "route_2", "species_id": 16})
    # Must contain unresolve_area for route_2
    assert any(c.get("cmd") == "unresolve_area" and c.get("area_id") == "route_2" for c in cmds), \
        "Bonus pair formation should send unresolve_area"
    # route_2 should still be in normal pending state (PENDING_A since B entered)
    assert state.area_states.get("route_2") in (AreaStatus.PENDING_A, AreaStatus.UNSEEN, AreaStatus.PENDING_BOTH), \
        "Area state should not be consumed by bonus pair"


def test_pending_bonus_persists_across_reload(tmp_path, monkeypatch):
    """pending_bonus queue survives a save/load cycle."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState()
    state.pokeballs_obtained = {"a": True, "b": True}
    state.handle_event("a", {"event": "capture", "key": SHINY_KEY, "area_id": "route_1", "species_id": 25})
    assert list(state.pending_bonus["b"]) == [SHINY_KEY]
    # Reload
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state2 = SoulLinkState.load()
    assert list(state2.pending_bonus["b"]) == [SHINY_KEY], "pending_bonus must survive reload"
    assert len(state2.pending_bonus["a"]) == 0


def test_key_change_migrates_pending_bonus(tmp_path, monkeypatch):
    """key_change on a shiny must update its key in the partner's pending_bonus queue."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState()
    state.pokeballs_obtained = {"a": True, "b": True}
    state.handle_event("a", {"event": "capture", "key": SHINY_KEY, "area_id": "route_1", "species_id": 25})
    assert list(state.pending_bonus["b"]) == [SHINY_KEY]
    NEW_SHINY = "00030003:00030003"
    state.handle_event("a", {"event": "key_change", "old_key": SHINY_KEY, "new_key": NEW_SHINY})
    assert list(state.pending_bonus["b"]) == [NEW_SHINY], "New key must appear in pending_bonus"
    assert SHINY_KEY not in state.pending_bonus["b"], "Old key must be removed"


def test_no_wildcard_slot_during_pending_window(tmp_path, monkeypatch):
    """While shiny is unlinked (pending), normal strict party sync applies (no free slot for partner)."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState()
    state.pokeballs_obtained = {"a": True, "b": True}
    # Build 5 linked pairs, all in party
    for i in range(5):
        ak, bk = f"AA{i:02X}:1111", f"BB{i:02X}:2222"
        entry = LinkEntry(area_id=f"area{i}", a=MonInfo(key=ak), b=MonInfo(key=bk), status=LinkStatus.ALIVE)
        state.links.append(entry)
        state._index_entry(entry)
        state.party_keys["a"].add(ak)
        state.party_keys["b"].add(bk)
    # A catches shiny — A's party is now physically full (5 linked + 1 shiny = 6)
    state.party_size = {"a": 6, "b": 5}
    state.handle_event("a", {"event": "capture", "key": SHINY_KEY, "area_id": "shiny_area", "species_id": 25})
    assert SHINY_KEY in state.bonus_keys["a"]
    # Build a 6th linked pair in box
    extra_a, extra_b = "AAFF:1111", "BBFF:2222"
    extra_entry = LinkEntry(area_id="area6", a=MonInfo(key=extra_a), b=MonInfo(key=extra_b), status=LinkStatus.ALIVE)
    state.links.append(extra_entry)
    state._index_entry(extra_entry)
    # B tries to pull their 6th linked mon — should be BLOCKED (A's party is physically full)
    state.party_size["b"] = 5
    cmds = state.handle_event("b", {"event": "box_to_party", "key": extra_b})
    assert has_cmd(cmds, "box_mon", extra_b), "B should be blocked — A's party physically full (no wildcard exemption)"


def test_shiny_faint_no_wildcard_revocation(tmp_path, monkeypatch):
    """When A's unlinked shiny faints, no wildcard revocation occurs (no box_mon for B)."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState()
    state.pokeballs_obtained = {"a": True, "b": True}
    state.handle_event("a", {"event": "capture", "key": SHINY_KEY, "area_id": "route_1", "species_id": 25})
    # A's shiny faints — should be silent (no propagation, no wildcard revocation)
    state.handle_event("a", {"event": "faint", "key": SHINY_KEY})
    cmds_b = state.handle_event("b", {"event": "tick"})
    assert not any(c.get("cmd") == "box_mon" for c in cmds_b), "No box_mon should be queued for B"


def test_shiny_deposit_no_wildcard_revocation(tmp_path, monkeypatch):
    """When A deposits their unlinked shiny, no wildcard revocation occurs (no box_mon for B)."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState()
    state.pokeballs_obtained = {"a": True, "b": True}
    state.party_size = {"a": 1, "b": 1}
    state.handle_event("a", {"event": "capture", "key": SHINY_KEY, "area_id": "route_1", "species_id": 25})
    state.party_keys["a"].add(SHINY_KEY)
    cmds = state.handle_event("a", {"event": "party_to_box", "key": SHINY_KEY})
    cmds_b = state.handle_event("b", {"event": "tick"})
    all_cmds = cmds + cmds_b
    assert not any(c.get("cmd") == "box_mon" for c in all_cmds), "No box_mon should be queued — wildcard revocation removed"


def test_bonus_pair_sync_shiny_in_box_at_formation(tmp_path, monkeypatch):
    """If A's shiny is already in the box when B catches their bonus partner, B's catch should be boxed too."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState()
    state.pokeballs_obtained = {"a": True, "b": True}
    state.party_size = {"a": 1, "b": 1}
    state.handle_event("a", {"event": "capture", "key": SHINY_KEY, "area_id": "route_1", "species_id": 25})
    # A deposits shiny
    state.party_keys["a"].discard(SHINY_KEY)  # shiny is now in A's box
    # B catches their bonus — shiny is NOT in A's party
    BONUS_KEY = "CAFE:BABE"
    cmds = state.handle_event("b", {"event": "capture", "key": BONUS_KEY, "area_id": "route_2", "species_id": 16})
    # B's bonus catch should be boxed to match shiny's box state
    all_cmds = cmds + state.handle_event("b", {"event": "tick"})
    assert has_cmd(all_cmds, "box_mon", BONUS_KEY), "B's bonus catch should be boxed (shiny is in A's box)"


# ── Category 1: PC Movement Race Conditions ──────────────────────────────────


def test_triple_swap_all_pairs_sync(tmp_path, monkeypatch):
    """Deposit pair1, withdraw pair2, deposit pair3 in sequence — all partners sync correctly."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState()
    state.pokeballs_obtained = {"a": True, "b": True}

    # Create 3 linked pairs
    pairs = [("A:1", "B:1", "route_1"), ("A:2", "B:2", "route_2"), ("A:3", "B:3", "route_3")]
    for a_k, b_k, area in pairs:
        entry = LinkEntry(area_id=area, a=MonInfo(key=a_k, level=5),
                          b=MonInfo(key=b_k, level=5), status=LinkStatus.ALIVE)
        state.links.append(entry)
        state._index_entry(entry)
        state.area_states[area] = AreaStatus.LINKED

    # A:1 and A:3 in party; A:2 in box. B:1 and B:3 in party; B:2 in box.
    state.party_keys["a"] = {"A:1", "A:3", "FILL_A:1", "FILL_A:2"}
    state.party_keys["b"] = {"B:1", "B:3", "FILL_B:1", "FILL_B:2"}
    state.party_size = {"a": 4, "b": 4}
    state.mon_stats["b"]["B:2"] = {"level": 5, "maxHP": 20}

    # A deposits pair1
    state.handle_event("a", {"event": "party_to_box", "key": "A:1", "stats": {"level": 5, "maxHP": 20}})
    # A withdraws pair2
    state.handle_event("a", {"event": "box_to_party", "key": "A:2"})
    # A deposits pair3
    state.handle_event("a", {"event": "party_to_box", "key": "A:3", "stats": {"level": 5, "maxHP": 20}})

    # Flush B's commands
    cmds_b = state.handle_event("b", {"event": "tick"})
    # Verify B gets box_mon for B:1, party_mon for B:2, box_mon for B:3
    assert has_cmd(cmds_b, "box_mon", "B:1"), "B should get box_mon for B:1"
    assert has_cmd(cmds_b, "party_mon", "B:2"), "B should get party_mon for B:2"
    assert has_cmd(cmds_b, "box_mon", "B:3"), "B should get box_mon for B:3"


def test_box_to_party_with_stale_party_size(tmp_path, monkeypatch):
    """B's party_size is stale (5), but B has a pending box_mon that would free a slot. Verify retrieval allowed."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState()
    state.pokeballs_obtained = {"a": True, "b": True}

    # Create 2 linked pairs: A:1<->B:1, A:2<->B:2
    for a_k, b_k, area in [("A:1", "B:1", "route_1"), ("A:2", "B:2", "route_2")]:
        entry = LinkEntry(area_id=area, a=MonInfo(key=a_k, level=5),
                          b=MonInfo(key=b_k, level=5), status=LinkStatus.ALIVE)
        state.links.append(entry)
        state._index_entry(entry)
        state.area_states[area] = AreaStatus.LINKED

    state.party_keys["a"] = {"A:1", "FILL_A:1", "FILL_A:2", "FILL_A:3", "FILL_A:4"}
    state.party_keys["b"] = {"B:1", "FILL_B:1", "FILL_B:2", "FILL_B:3", "FILL_B:4"}
    state.party_size = {"a": 5, "b": 5}
    state.mon_stats["b"]["B:2"] = {"level": 5, "maxHP": 20}

    # A deposits pair1 → box_mon for B:1 queued (B adjusted size: 5-1=4)
    state.handle_event("a", {"event": "party_to_box", "key": "A:1", "stats": {"level": 5, "maxHP": 20}})
    assert any(c.get("cmd") == "box_mon" and c.get("key") == "B:1"
               for c in state.queued_commands["b"])

    # A withdraws pair2 → B's stale party_size=5, but 1 pending box_mon → adjusted=4 → allow
    cmds = state.handle_event("a", {"event": "box_to_party", "key": "A:2"})
    assert not has_cmd(cmds, "box_mon", "A:2"), "A:2 should NOT be re-deposited"
    assert "A:2" in state.party_keys["a"]


def test_simultaneous_deposits_both_partners_boxed(tmp_path, monkeypatch):
    """A and B each deposit a linked mon in the same area on same tick — both partners get box_mon."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = make_state_with_link()
    state._has_helld = {"a", "b"}

    # A deposits A:1 → queues box_mon for B:2
    state.handle_event("a", {"event": "party_to_box", "key": "A:1", "stats": {"level": 5, "maxHP": 20}})
    # B:2 is already removed from party_keys by the server, so B's deposit of B:2 won't re-queue box_mon for A
    state.handle_event("b", {"event": "party_to_box", "key": "B:2", "stats": {"level": 7, "maxHP": 25}})

    # A's partner mon B:2 should be in B's queued_commands OR already handled
    # B's partner mon A:1 is already boxed by A themselves — no duplicate box_mon for A
    cmds_a = state.handle_event("a", {"event": "tick"})
    state.handle_event("b", {"event": "tick"})
    assert not has_cmd(cmds_a, "box_mon", "A:1"), "A boxed A:1 themselves — no duplicate box_mon"

    # Both should be out of party_keys
    assert "A:1" not in state.party_keys["a"]
    assert "B:2" not in state.party_keys["b"]


def test_many_pending_commands_all_execute(tmp_path, monkeypatch):
    """Queue 4+ box_mon/party_mon commands — all delivered on next tick."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState()
    state.pokeballs_obtained = {"a": True, "b": True}

    # Create 4 linked pairs
    for i in range(4):
        a_k, b_k = f"A:{i}", f"B:{i}"
        entry = LinkEntry(area_id=f"route_{i}", a=MonInfo(key=a_k, level=5),
                          b=MonInfo(key=b_k, level=5), status=LinkStatus.ALIVE)
        state.links.append(entry)
        state._index_entry(entry)
        state.area_states[f"route_{i}"] = AreaStatus.LINKED
        state.party_keys["a"].add(a_k)
        state.party_keys["b"].add(b_k)

    state.party_size = {"a": 4, "b": 4}

    # A deposits all 4
    for i in range(4):
        state.handle_event("a", {"event": "party_to_box", "key": f"A:{i}",
                                  "stats": {"level": 5, "maxHP": 20}})

    # B's next tick should deliver all 4 box_mon commands
    cmds_b = state.handle_event("b", {"event": "tick"})
    for i in range(4):
        assert has_cmd(cmds_b, "box_mon", f"B:{i}"), f"B:{i} should get box_mon"


# ── Category 2: Reconnect Half-Complete Sync Tests ───────────────────────────


def test_reconnect_after_deposit_before_partner_ack(tmp_path, monkeypatch):
    """A deposits, server queues box_mon for B. A disconnects and reconnects. Verify box_mon still queued for B."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = make_state_with_link()

    # A deposits → box_mon queued for B
    state.handle_event("a", {"event": "party_to_box", "key": "A:1",
                              "stats": {"level": 10, "maxHP": 40}})
    assert any(c.get("cmd") == "box_mon" and c.get("key") == "B:2"
               for c in state.queued_commands["b"]), "box_mon should be queued for B"

    # A reconnects
    state.handle_event("a", {"event": "hello", "has_pokeballs": True,
                              "party": [{"key": "STARTER:9999", "hp": 30, "maxHP": 30}]})

    # B's queue should still have box_mon
    cmds_b = state.handle_event("b", {"event": "tick"})
    assert has_cmd(cmds_b, "box_mon", "B:2"), "B's box_mon should survive A's reconnect"


def test_reconnect_requeues_pending_memorials(tmp_path, monkeypatch):
    """Both players have pending memorials when A reconnects — memorials re-queued for A."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    monkeypatch.setattr("server.state.MEMORIAL_PATH", str(tmp_path / "memorial.json"))
    state = make_state_with_link()

    # Cause faint → both get memorials
    state.handle_event("a", {"event": "faint", "key": "A:1"})
    # Drain A's queue (delivered on the faint response itself)
    state.handle_event("a", {"event": "tick"})
    # A's pending_memorials still has A:1
    assert "A:1" in state.pending_memorials["a"]

    # A reconnects → should re-queue memorialize
    cmds = state.handle_event("a", {"event": "hello", "has_pokeballs": True, "party": []})
    assert has_cmd(cmds, "memorialize", "A:1"), "memorialize must be re-queued on reconnect"


def test_reconnect_with_mon_in_party_not_in_party_keys(tmp_path, monkeypatch):
    """Hello party snapshot includes a linked mon not in party_keys — party_keys reconciled from snapshot."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = make_state_with_link()

    # Artificially remove A:1 from party_keys (simulate stale state)
    state.party_keys["a"].discard("A:1")

    # A reconnects with A:1 in party
    state.handle_event("a", {"event": "hello", "has_pokeballs": True,
                              "party": [{"key": "A:1", "hp": 45, "maxHP": 50, "level": 5}]})

    # After hello, party_keys["a"] should contain A:1
    assert "A:1" in state.party_keys["a"], "party_keys should be rebuilt from hello snapshot"


def test_reconnect_reconciles_hp_zero_as_faint(tmp_path, monkeypatch):
    """Hello party contains linked mon with hp=0 — treated as faint, propagates to partner."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    monkeypatch.setattr("server.state.MEMORIAL_PATH", str(tmp_path / "memorial.json"))
    state = make_state_with_link()

    # A reconnects with A:1 hp=0
    state.handle_event("a", {"event": "hello", "has_pokeballs": True,
                              "party": [{"key": "A:1", "hp": 0, "maxHP": 50, "level": 5}]})

    # Entry should be DEAD
    assert state.links[0].status == LinkStatus.DEAD

    # B should get force_faint for B:2
    cmds_b = state.handle_event("b", {"event": "tick"})
    assert has_cmd(cmds_b, "force_faint", "B:2"), "B should get force_faint on A's reconcile faint"


# ── Category 3: Faint Timing Conflict Tests ──────────────────────────────────


def test_faint_cancels_pending_party_mon(tmp_path, monkeypatch):
    """A's mon faints while party_mon for B's partner is queued — party_mon should be replaced by memorialize."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    monkeypatch.setattr("server.state.MEMORIAL_PATH", str(tmp_path / "memorial.json"))
    state = make_state_with_link()

    # Simulate: a stale party_mon is already queued for B
    state.queued_commands["b"].append({"cmd": "party_mon", "key": "B:2"})

    # A:1 faints → _propagate_faint → _queue_memorialize cancels stale party_mon for B:2
    state.handle_event("a", {"event": "faint", "key": "A:1"})
    cmds_b = state.handle_event("b", {"event": "tick"})

    assert not has_cmd(cmds_b, "party_mon", "B:2"), "Stale party_mon must be cancelled on death"
    assert has_cmd(cmds_b, "memorialize", "B:2"), "memorialize should replace it"
    assert has_cmd(cmds_b, "force_faint", "B:2"), "force_faint for B:2 should also be present"


def test_simultaneous_faint_no_double_processing(tmp_path, monkeypatch):
    """A reports faint for A:1, then B reports faint for B:2 (same linked pair). No double-kill."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    monkeypatch.setattr("server.state.MEMORIAL_PATH", str(tmp_path / "memorial.json"))
    state = make_state_with_link()

    # A:1 faints → entry DEAD, force_faint queued for B:2
    state.handle_event("a", {"event": "faint", "key": "A:1"})
    assert state.links[0].status == LinkStatus.DEAD

    # B:2 faint arrives → entry already DEAD → ignored
    state.handle_event("b", {"event": "faint", "key": "B:2"})
    # No duplicate force_faint for A should be queued
    cmds_a = state.handle_event("a", {"event": "tick"})
    assert not has_cmd(cmds_a, "force_faint", "A:1"), "No duplicate force_faint for already-dead mon"


def test_faint_after_sync_retrieve_done(tmp_path, monkeypatch):
    """B successfully retrieves (sync_retrieve_done), then B:2 immediately faints. Faint should propagate."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    monkeypatch.setattr("server.state.MEMORIAL_PATH", str(tmp_path / "memorial.json"))
    state = make_state_with_link()

    # Both boxed initially
    state.party_keys["a"].discard("A:1")
    state.party_keys["b"].discard("B:2")
    state.mon_stats["b"]["B:2"] = {"level": 7, "maxHP": 30}

    # A retrieves → party_mon queued for B
    state.handle_event("a", {"event": "box_to_party", "key": "A:1"})
    # B confirms retrieval
    state.handle_event("b", {"event": "sync_retrieve_done", "key": "B:2"})
    assert "B:2" in state.party_keys["b"]

    # B:2 faints → should propagate force_faint to A:1
    state.handle_event("b", {"event": "faint", "key": "B:2"})
    assert state.links[0].status == LinkStatus.DEAD
    cmds_a = state.handle_event("a", {"event": "tick"})
    assert has_cmd(cmds_a, "force_faint", "A:1"), "A:1 should get force_faint after B:2 faints"


def test_faint_during_pending_box_mon(tmp_path, monkeypatch):
    """A's mon faints while box_mon for B's partner is pending. Memorialize replaces box_mon."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    monkeypatch.setattr("server.state.MEMORIAL_PATH", str(tmp_path / "memorial.json"))
    state = make_state_with_link()

    # A deposited → box_mon queued for B:2
    state.queued_commands["b"].append({"cmd": "box_mon", "key": "B:2"})

    # A:1 faints → _queue_memorialize cancels stale box_mon for B:2
    state.handle_event("a", {"event": "faint", "key": "A:1"})
    cmds_b = state.handle_event("b", {"event": "tick"})

    assert not has_cmd(cmds_b, "box_mon", "B:2"), "Stale box_mon must be cancelled on death"
    assert has_cmd(cmds_b, "memorialize", "B:2"), "memorialize should replace box_mon"


# ── Category 4: Party Size Accounting ────────────────────────────────────────


def test_adjusted_party_size_with_three_pending_box_mons(tmp_path, monkeypatch):
    """Partner has party_size=6 but 3 pending box_mons → adjusted=3, retrieval allowed."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState()
    state.pokeballs_obtained = {"a": True, "b": True}

    # Create 4 linked pairs
    for i in range(4):
        a_k, b_k = f"A:{i}", f"B:{i}"
        entry = LinkEntry(area_id=f"route_{i}", a=MonInfo(key=a_k, level=5),
                          b=MonInfo(key=b_k, level=5), status=LinkStatus.ALIVE)
        state.links.append(entry)
        state._index_entry(entry)
        state.area_states[f"route_{i}"] = AreaStatus.LINKED

    # A party: A:0 in party, A:1..3 in box (deposit them)
    state.party_keys["a"] = {"A:0", "FILL_A:1", "FILL_A:2"}
    state.party_keys["b"] = {"B:0", "FILL_B:1", "FILL_B:2", "FILL_B:3", "FILL_B:4", "FILL_B:5"}
    state.party_size = {"a": 3, "b": 6}

    # Queue 3 box_mon commands for B
    for i in range(1, 4):
        state.queued_commands["b"].append({"cmd": "box_mon", "key": f"B:{i}"})

    state.mon_stats["b"]["B:0"] = {"level": 5, "maxHP": 20}

    # A:0 is in party, B:0 is in party. A deposits A:0 → server queues box_mon for B:0
    state.handle_event("a", {"event": "party_to_box", "key": "A:0", "stats": {"level": 5, "maxHP": 20}})
    # B now has 4 pending box_mons, party_size=6 → adjusted=2

    # Now A retrieves some linked mon (A:1) — B's partner (B:1) needs to come to party
    # B:1 not in party_keys, B has box_mon queued for B:1 but let's test with A:2
    # Actually let's just verify the adjusted size is computed: try withdrawing A:1
    state.mon_stats["b"]["B:1"] = {"level": 5, "maxHP": 20}
    cmds = state.handle_event("a", {"event": "box_to_party", "key": "A:1"})
    # B:1's partner needs party_mon; B has adjusted_party_size = 6-4 = 2, logical_size = max(linked=2, 2) = 2 < 6
    assert not has_cmd(cmds, "box_mon", "A:1"), "A:1 should NOT be re-deposited (B has room after adjustments)"
    assert "A:1" in state.party_keys["a"]


def test_party_size_updated_from_tick(tmp_path, monkeypatch):
    """Tick with party snapshot updates party_size."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState()
    state.party_size = {"a": 0, "b": 0}

    state.handle_event("a", {"event": "tick", "party": [
        {"key": "M1:1", "hp": 30, "maxHP": 30},
        {"key": "M2:2", "hp": 25, "maxHP": 25},
        {"key": "M3:3", "hp": 20, "maxHP": 20},
        {"key": "M4:4", "hp": 15, "maxHP": 15},
    ]})
    assert state.party_size["a"] == 4, "party_size should be 4 after tick with 4 mons"


def test_box_to_party_blocked_when_partner_truly_full(tmp_path, monkeypatch):
    """Partner has party_size=6, no pending box_mons → retrieval blocked, mon re-deposited."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = make_state_with_link()

    # Deposit A:1 first
    state.handle_event("a", {"event": "party_to_box", "key": "A:1",
                              "stats": {"level": 10, "maxHP": 40}})
    # Flush B's box_mon
    state.handle_event("b", {"event": "tick"})

    # Fill B's party with 6 other keys (simulates truly full party)
    state.party_keys["b"] = {f"FILL:{i}" for i in range(6)}
    state.party_size["b"] = 6

    # A tries to withdraw — blocked because B has no room
    cmds = state.handle_event("a", {"event": "box_to_party", "key": "A:1"})
    assert has_cmd(cmds, "box_mon", "A:1"), "Withdrawal blocked — re-deposited"
    assert has_cmd(cmds, "hud_show"), "HUD warning shown"
    assert "A:1" not in state.party_keys["a"]


# ── Category 5: Memorial Completion/Failure Tests ────────────────────────────


def test_memorialize_done_transitions_to_memorial(tmp_path, monkeypatch):
    """Both players confirm memorialize_done → entry transitions from DEAD to MEMORIAL."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    monkeypatch.setattr("server.state.MEMORIAL_PATH", str(tmp_path / "memorial.json"))
    state = make_state_with_link(status=LinkStatus.DEAD)
    state.pending_memorials["a"].add("A:1")
    state.pending_memorials["b"].add("B:2")

    # A sends memorialize_done → still DEAD (B pending)
    state.handle_event("a", {"event": "memorialize_done", "key": "A:1"})
    assert state.links[0].status == LinkStatus.DEAD, "One side done — still DEAD"
    assert "A:1" not in state.pending_memorials["a"]

    # B sends memorialize_done → now MEMORIAL
    state.handle_event("b", {"event": "memorialize_done", "key": "B:2"})
    assert state.links[0].status == LinkStatus.MEMORIAL, "Both done — should be MEMORIAL"


def test_memorialize_failed_still_transitions(tmp_path, monkeypatch):
    """memorialize_failed is treated as done — pair can still reach MEMORIAL status."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    monkeypatch.setattr("server.state.MEMORIAL_PATH", str(tmp_path / "memorial.json"))
    state = make_state_with_link(status=LinkStatus.DEAD)
    state.pending_memorials["a"].add("A:1")
    state.pending_memorials["b"].add("B:2")

    # A sends memorialize_done
    state.handle_event("a", {"event": "memorialize_done", "key": "A:1"})
    assert state.links[0].status == LinkStatus.DEAD

    # B sends memorialize_failed → should still transition to MEMORIAL
    state.handle_event("b", {"event": "memorialize_failed", "key": "B:2", "reason": "boxes full"})
    assert state.links[0].status == LinkStatus.MEMORIAL, "Failed memorialization should still reach MEMORIAL"


def test_save_load_round_trip(tmp_path, monkeypatch):
    """State saved to links.json and loaded back preserves all fields."""
    links_path = str(tmp_path / "links.json")
    monkeypatch.setattr("server.state.LINKS_PATH", links_path)
    monkeypatch.setattr("server.state.MEMORIAL_PATH", str(tmp_path / "memorial.json"))

    state = SoulLinkState(species_lock=True, gender_lock=True, type_lock=True)
    state.pokeballs_obtained = {"a": True, "b": False}

    # Add a link
    entry = LinkEntry(area_id="route_5", a=MonInfo(key="AA:11", level=10, species=25, nickname="PIKA"),
                      b=MonInfo(key="BB:22", level=12, species=16, nickname="PIDGY"),
                      status=LinkStatus.ALIVE)
    state.links.append(entry)
    state._index_entry(entry)
    state.area_states["route_5"] = AreaStatus.LINKED

    # Add pending captures
    state.pending_captures["route_6"] = {
        "a": MonInfo(key="CC:33", level=5, species=19, nickname="RAT")
    }
    state.area_states["route_6"] = AreaStatus.PENDING_B

    # Add mon_stats
    state.mon_stats["a"]["AA:11"] = {"level": 10, "maxHP": 35}

    # Add bonus_keys and pending_bonus
    state.bonus_keys["a"].add("SHINY:1111")
    state.pending_bonus["b"].append("SHINY:1111")

    # Add pending memorials
    state.pending_memorials["a"].add("DEAD:KEY")

    state._save()

    # Load it back
    loaded = SoulLinkState.load()
    assert len(loaded.links) == 1
    assert loaded.links[0].a.key == "AA:11"
    assert loaded.links[0].a.nickname == "PIKA"
    assert loaded.links[0].b.key == "BB:22"
    assert loaded.links[0].status == LinkStatus.ALIVE
    assert loaded.area_states["route_5"] == AreaStatus.LINKED
    assert loaded.area_states["route_6"] == AreaStatus.PENDING_B
    assert loaded.pokeballs_obtained["a"] is True
    assert loaded.pokeballs_obtained["b"] is False
    assert loaded.species_lock is True
    assert loaded.gender_lock is True
    assert loaded.type_lock is True
    assert loaded.mon_stats["a"].get("AA:11", {}).get("level") == 10
    assert "SHINY:1111" in loaded.bonus_keys["a"]
    assert list(loaded.pending_bonus["b"]) == ["SHINY:1111"]
    assert loaded.pending_captures["route_6"]["a"].key == "CC:33"
    assert "DEAD:KEY" in loaded.pending_memorials["a"]


# ── Category 6: Bonus Pair Edge Cases ────────────────────────────────────────


def test_shiny_faint_before_bonus_pair_forms(tmp_path, monkeypatch):
    """A catches shiny, shiny faints before B catches bonus partner. Bonus_keys/pending_bonus should remain."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    monkeypatch.setattr("server.state.MEMORIAL_PATH", str(tmp_path / "memorial.json"))
    state = SoulLinkState()
    state.pokeballs_obtained = {"a": True, "b": True}
    state.party_size = {"a": 2, "b": 2}

    # A catches a shiny
    state.handle_event("a", {"event": "capture", "key": SHINY_KEY, "area_id": "route_1", "species_id": 25})
    assert SHINY_KEY in state.bonus_keys["a"]
    assert list(state.pending_bonus["b"]) == [SHINY_KEY]

    # Shiny faints — it has no link entry, so faint is a no-op on links
    state.handle_event("a", {"event": "faint", "key": SHINY_KEY})
    # Bonus_keys and pending_bonus should remain (shiny death doesn't revoke bonus opportunity)
    assert SHINY_KEY in state.bonus_keys["a"], "bonus_keys should persist after shiny faint"
    assert list(state.pending_bonus["b"]) == [SHINY_KEY], "pending_bonus should persist after shiny faint"
    # No crash, state is consistent
    assert SHINY_KEY not in state.party_keys["a"], "Dead shiny should be removed from party_keys"


def test_pending_bonus_fifo_three_entries(tmp_path, monkeypatch):
    """A catches 3 shinies → B's pending_bonus has 3 entries in FIFO order. B's captures pop in order."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState()
    state.pokeballs_obtained = {"a": True, "b": True}
    state.party_size = {"a": 1, "b": 1}

    SHINY_KEY2 = "00020002:00020002"
    SHINY_KEY3 = "00030003:00030003"

    # A catches 3 shinies
    state.handle_event("a", {"event": "capture", "key": SHINY_KEY, "area_id": "route_1", "species_id": 25})
    state.handle_event("a", {"event": "capture", "key": SHINY_KEY2, "area_id": "route_2", "species_id": 37})
    state.handle_event("a", {"event": "capture", "key": SHINY_KEY3, "area_id": "route_3", "species_id": 43})
    assert list(state.pending_bonus["b"]) == [SHINY_KEY, SHINY_KEY2, SHINY_KEY3]

    # B catches mon1 → pairs with shiny1
    state.handle_event("b", {"event": "capture", "key": "CAFE:0001", "area_id": "route_4", "species_id": 16})
    assert list(state.pending_bonus["b"]) == [SHINY_KEY2, SHINY_KEY3]
    assert state.links[-1].a.key == SHINY_KEY

    # B catches mon2 → pairs with shiny2
    state.handle_event("b", {"event": "capture", "key": "CAFE:0002", "area_id": "route_5", "species_id": 19})
    assert list(state.pending_bonus["b"]) == [SHINY_KEY3]
    assert state.links[-1].a.key == SHINY_KEY2

    # B catches mon3 → pairs with shiny3
    state.handle_event("b", {"event": "capture", "key": "CAFE:0003", "area_id": "route_6", "species_id": 7})
    assert len(state.pending_bonus["b"]) == 0
    assert state.links[-1].a.key == SHINY_KEY3
    assert len(state.links) == 3


def test_bonus_clause_violation_retry(tmp_path, monkeypatch):
    """B's bonus catch violates species clause → rejected, pending_bonus preserved for retry."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState(species_lock=True)
    state.pokeballs_obtained = {"a": True, "b": True}
    state.party_size = {"a": 1, "b": 1}

    # A catches shiny Pikachu (species 25)
    state.handle_event("a", {"event": "capture", "key": SHINY_KEY, "area_id": "route_1", "species_id": 25})

    # B catches Raichu (species 26, same evo family as Pikachu 25) as bonus → violation
    cmds_b = state.handle_event("b", {"event": "capture", "key": "CAFE:BABE",
                                       "area_id": "route_2", "species_id": 26})
    assert has_cmd(cmds_b, "force_faint", "CAFE:BABE"), "Violating bonus catch should be force-fainted"
    assert len(state.pending_bonus["b"]) == 1, "pending_bonus preserved for retry"
    assert len(state.links) == 0, "No link should be formed"


# ── Category 7: Command Queue Ordering ───────────────────────────────────────


def test_box_mon_then_party_mon_same_pair(tmp_path, monkeypatch):
    """A deposits then immediately withdraws same mon — box_mon cancelled, party_mon sent."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = make_state_with_link()
    state.mon_stats["b"]["B:2"] = {"level": 7, "maxHP": 30}

    # A deposits A:1 → box_mon queued for B:2
    state.handle_event("a", {"event": "party_to_box", "key": "A:1",
                              "stats": {"level": 5, "maxHP": 20}})
    assert any(c.get("cmd") == "box_mon" and c.get("key") == "B:2"
               for c in state.queued_commands["b"])

    # A withdraws A:1 → box_mon for B:2 cancelled, party_mon for B:2 queued
    state.handle_event("a", {"event": "box_to_party", "key": "A:1"})

    # B's queue should have only party_mon, not box_mon
    cmds_b = state.handle_event("b", {"event": "tick"})
    assert has_cmd(cmds_b, "party_mon", "B:2"), "B should get party_mon"
    assert not has_cmd(cmds_b, "box_mon", "B:2"), "box_mon for B:2 should be cancelled"


def test_party_mon_then_box_mon_same_pair(tmp_path, monkeypatch):
    """A withdraws, B confirms retrieval (sync_retrieve_done), then A deposits.
    Party_mon cancelled, box_mon queued because B:2 is now confirmed in party."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = make_state_with_link()
    state._has_helld = {"a", "b"}

    # Both boxed initially
    state.party_keys["a"].discard("A:1")
    state.party_keys["b"].discard("B:2")
    state.mon_stats["b"]["B:2"] = {"level": 7, "maxHP": 30}

    # A withdraws A:1 → party_mon queued for B:2
    state.handle_event("a", {"event": "box_to_party", "key": "A:1"})
    assert any(c.get("cmd") == "party_mon" and c.get("key") == "B:2"
               for c in state.queued_commands["b"])

    # B confirms the retrieval — B:2 now in party_keys
    state.handle_event("b", {"event": "sync_retrieve_done", "key": "B:2"})
    assert "B:2" in state.party_keys["b"]

    # A deposits A:1 → partner_in_party is True now, so party_mon cancelled and box_mon queued
    state.handle_event("a", {"event": "party_to_box", "key": "A:1",
                              "stats": {"level": 5, "maxHP": 20}})

    # B's next tick: only box_mon, no stale party_mon
    cmds_b = state.handle_event("b", {"event": "tick"})
    assert has_cmd(cmds_b, "box_mon", "B:2"), "B:2 should get box_mon"
    assert not has_cmd(cmds_b, "party_mon", "B:2"), "stale party_mon for B:2 should be cancelled"


def test_mixed_sync_and_hud_commands_all_delivered(tmp_path, monkeypatch):
    """Box_mon, hud_show, and party_mon for different pairs — all delivered in one tick."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState()
    state.pokeballs_obtained = {"a": True, "b": True}

    # Create 2 linked pairs
    for a_k, b_k, area in [("A:1", "B:1", "route_1"), ("A:2", "B:2", "route_2")]:
        entry = LinkEntry(area_id=area, a=MonInfo(key=a_k, level=5),
                          b=MonInfo(key=b_k, level=5), status=LinkStatus.ALIVE)
        state.links.append(entry)
        state._index_entry(entry)
        state.area_states[area] = AreaStatus.LINKED

    state.party_keys["a"] = {"A:1"}
    state.party_keys["b"] = {"B:1"}
    state.party_size = {"a": 3, "b": 3}
    state.mon_stats["b"]["B:2"] = {"level": 5, "maxHP": 20}

    # Queue a mix of commands for B
    state.queued_commands["b"].append({"cmd": "hud_show", "text": "Hello!", "r": 0, "g": 255, "b": 0})
    # A deposits pair1 → box_mon for B:1
    state.handle_event("a", {"event": "party_to_box", "key": "A:1",
                              "stats": {"level": 5, "maxHP": 20}})
    # A retrieves pair2 → party_mon for B:2
    state.handle_event("a", {"event": "box_to_party", "key": "A:2"})

    # B's tick should deliver all commands
    cmds_b = state.handle_event("b", {"event": "tick"})
    assert has_cmd(cmds_b, "hud_show"), "HUD command should be delivered"
    assert has_cmd(cmds_b, "box_mon", "B:1"), "box_mon for B:1 should be delivered"
    assert has_cmd(cmds_b, "party_mon", "B:2"), "party_mon for B:2 should be delivered"


# ── atomic persistence ───────────────────────────────────────────────────────────────────────

def test_save_writes_valid_json_and_leaves_no_tmp(tmp_path):
    """_save() produces a parseable links.json and removes its .tmp scratch file."""
    import json as _json
    state = SoulLinkState(data_dir=str(tmp_path))
    state.pokeballs_obtained = {"a": True, "b": True}

    state.handle_event("a", {"event": "capture", "key": "A:1", "area_id": "route_1", "level": 5})
    state.handle_event("b", {"event": "capture", "key": "B:2", "area_id": "route_1", "level": 7})

    links_path = tmp_path / "links.json"
    assert links_path.exists(), "links.json should exist after captures trigger _save()"
    with open(links_path) as f:
        payload = _json.load(f)
    assert any(e.get("area_id") == "route_1" for e in payload["links"])

    assert not list(tmp_path.glob("*.tmp")), ".tmp scratch must not leak"


def test_save_crash_mid_write_preserves_previous_links_json(tmp_path, monkeypatch):
    """If json.dump raises mid-write, links.json must remain at its prior content."""
    import json as _json
    state = SoulLinkState(data_dir=str(tmp_path))
    state.pokeballs_obtained = {"a": True, "b": True}

    state.handle_event("a", {"event": "capture", "key": "A:1", "area_id": "route_1", "level": 5})
    state.handle_event("b", {"event": "capture", "key": "B:2", "area_id": "route_1", "level": 7})
    links_path = tmp_path / "links.json"
    with open(links_path) as f:
        good_payload = _json.load(f)

    # Inject a crash inside json.dump. Because _atomic_write_json writes to
    # links.json.tmp first and only os.replaces on success, the original file
    # must survive untouched.
    real_dump = _json.dump
    def exploding_dump(*args, **kwargs):
        real_dump(*args, **kwargs)
        raise OSError("simulated disk full")
    monkeypatch.setattr("server.state.json.dump", exploding_dump)

    # The save failure is swallowed (logged as warning), so no exception propagates.
    # The important invariant is that links.json still holds the prior good payload.
    state.handle_event("a", {"event": "faint", "key": "A:1"})

    with open(links_path) as f:
        survived = _json.load(f)
    assert survived == good_payload, "links.json must be unchanged after crashed write"


# ── key_change undo (trade_undo) round-trip ───────────────────────────────────

def test_key_change_undo_restores_link_entry(tmp_path, monkeypatch):
    """NPC trade forward then trade_undo backward fully restores link entry and key index."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    ORIG_KEY   = "AAAA:1111"
    TRADED_KEY = "CCCC:3333"

    state = make_state_with_link(a_key=ORIG_KEY, b_key="BBBB:2222")
    state.mon_stats["a"][ORIG_KEY] = {"level": 20, "maxHP": 60}

    # Forward: NPC trade — A's mon gets a new key
    state.handle_event("a", {"event": "key_change", "old_key": ORIG_KEY, "new_key": TRADED_KEY})
    assert state.links[0].a.key == TRADED_KEY
    assert TRADED_KEY in state._key_index
    assert ORIG_KEY not in state._key_index
    assert TRADED_KEY in state.party_keys["a"]
    assert TRADED_KEY in state.mon_stats["a"]

    # Undo: freeze fires within 5 frames; Lua sends trade_undo B→A
    state.handle_event("a", {"event": "key_change", "old_key": TRADED_KEY, "new_key": ORIG_KEY,
                             "reason": "trade_undo"})
    assert state.links[0].a.key == ORIG_KEY
    assert ORIG_KEY in state._key_index
    assert TRADED_KEY not in state._key_index
    assert ORIG_KEY in state.party_keys["a"]
    assert TRADED_KEY not in state.party_keys["a"]
    assert ORIG_KEY in state.mon_stats["a"]
    assert TRADED_KEY not in state.mon_stats["a"]
    assert state.mon_stats["a"][ORIG_KEY]["level"] == 20


def test_key_change_undo_restores_pending_capture(tmp_path, monkeypatch):
    """trade_undo key_change migrates the key back in pending_captures."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState()
    state.pokeballs_obtained = {"a": True, "b": True}
    state.party_size = {"a": 2, "b": 2}

    state.handle_event("a", {"event": "capture", "key": "AA:11", "area_id": "route_5", "level": 5})
    assert state.pending_captures["route_5"]["a"].key == "AA:11"

    # Trade key forward
    state.handle_event("a", {"event": "key_change", "old_key": "AA:11", "new_key": "BB:22"})
    assert state.pending_captures["route_5"]["a"].key == "BB:22"

    # Undo
    state.handle_event("a", {"event": "key_change", "old_key": "BB:22", "new_key": "AA:11",
                             "reason": "trade_undo"})
    assert state.pending_captures["route_5"]["a"].key == "AA:11"


def test_key_change_undo_faint_propagates_with_restored_key(tmp_path, monkeypatch):
    """After a trade_undo, a faint on the restored original key still propagates to partner."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    ORIG_KEY   = "AAAA:1111"
    TRADED_KEY = "CCCC:3333"

    state = make_state_with_link(a_key=ORIG_KEY, b_key="BBBB:2222")

    state.handle_event("a", {"event": "key_change", "old_key": ORIG_KEY, "new_key": TRADED_KEY})
    state.handle_event("a", {"event": "key_change", "old_key": TRADED_KEY, "new_key": ORIG_KEY,
                             "reason": "trade_undo"})

    # Faint the restored key — partner should still receive force_faint
    state.handle_event("a", {"event": "faint", "key": ORIG_KEY})
    cmds = state.handle_event("b", {"event": "tick"})
    assert has_cmd(cmds, "force_faint", "BBBB:2222")


# ── tick reconciliation of party_keys vs. Lua snapshot ───────────────────────

def test_tick_reconcile_consistent_party_is_noop(tmp_path, monkeypatch):
    """Tick where Lua's party matches party_keys leaves state unchanged and queues nothing."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = make_state_with_link()
    cmds = state.handle_event("a", {"event": "tick",
                                    "party": [{"key": "A:1"}]})
    assert state.party_keys["a"] == {"A:1"}
    assert noop_only(cmds)


def test_tick_reconcile_ghost_boxed_mon_readds_and_pulls_partner(tmp_path, monkeypatch):
    """
    Phantom party_to_box scenario: Lua diff falsely emitted a deposit for A's mon,
    so server discarded it from party_keys[a] and queued box_mon to B which physically
    deposited B's partner.  When A's next tick reports the mon still in party, the
    reconciler should re-add it AND queue party_mon for B to restore party-sync.
    """
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = make_state_with_link()
    # Simulate the phantom deposit aftermath: server believes A's mon is boxed,
    # and B's mon got physically boxed in response.
    state.party_keys["a"].discard("A:1")
    state.party_keys["b"].discard("B:2")
    state.mon_stats["b"]["B:2"] = {"level": 7, "maxHP": 22, "attack": 10, "defense": 9,
                              "speed": 12, "spAtk": 8, "spDef": 8}

    state.handle_event("a", {"event": "tick", "party": [{"key": "A:1"}]})
    assert "A:1" in state.party_keys["a"]
    # Partner pull is queued on B's side — fetched on B's next round-trip.
    cmds_b = state.handle_event("b", {"event": "tick", "party": []})
    assert has_cmd(cmds_b, "party_mon", "B:2")
    # Stats from the cache should ride along so Lua can retrieve cleanly.
    party_mon_cmd = next(c for c in cmds_b if c.get("cmd") == "party_mon" and c.get("key") == "B:2")
    assert party_mon_cmd.get("stats", {}).get("level") == 7


def test_tick_reconcile_ghost_party_discards(tmp_path, monkeypatch):
    """
    Inverse drift: server thinks key is in party_keys but Lua's tick doesn't see it.
    Reconciler should discard the stale entry.
    """
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = make_state_with_link()
    # Lua reports an empty party (mon really got deposited but party_to_box was lost).
    state.handle_event("a", {"event": "tick", "party": []})
    assert "A:1" not in state.party_keys["a"]


def test_tick_reconcile_skips_keys_with_pending_box_mon(tmp_path, monkeypatch):
    """
    Don't reconcile a key with a pending box_mon — Lua hasn't executed yet but will,
    and reconciling now would race with the in-flight command.
    """
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = make_state_with_link()
    # Server has queued a real box_mon for A's mon but Lua hasn't drained it yet.
    state.queued_commands["a"].append({"cmd": "box_mon", "key": "A:1"})
    # During that window Lua's tick still reports the mon in party — don't discard.
    state.party_keys["a"].discard("A:1")  # already discarded as part of party_to_box
    state.handle_event("a", {"event": "tick", "party": [{"key": "A:1"}]})
    assert "A:1" not in state.party_keys["a"], (
        "reconciler must not re-add a key while box_mon is in-flight"
    )
    # No party_mon should leak to B either.
    cmds_b = state.handle_event("b", {"event": "tick", "party": [{"key": "B:2"}]})
    assert not has_cmd(cmds_b, "party_mon", "B:2")


def test_tick_reconcile_skips_memorial_mons(tmp_path, monkeypatch):
    """A memorial mon Lua transiently sees in party must not be re-added to party_keys."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = make_state_with_link(status=LinkStatus.MEMORIAL)
    state.party_keys["a"].discard("A:1")
    state.handle_event("a", {"event": "tick", "party": [{"key": "A:1"}]})
    assert "A:1" not in state.party_keys["a"]


def test_tick_reconcile_skips_quarantined_mons(tmp_path, monkeypatch):
    """A quarantined (pending-link) capture must stay boxed even if Lua reports party presence."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState()
    state.pokeballs_obtained = {"a": True, "b": True}
    state.pending_captures["route_5"] = {"a": MonInfo(key="Q:99", level=4)}
    state.handle_event("a", {"event": "tick", "party": [{"key": "Q:99"}]})
    assert "Q:99" not in state.party_keys["a"]


def test_tick_reconcile_skips_during_rebuild(tmp_path, monkeypatch):
    """rebuild_pending owns its own party_keys bookkeeping; tick must defer to it."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = make_state_with_link()
    state.party_keys["a"].discard("A:1")
    state.rebuild_pending["a"] = {"queued_keys": ["A:1"], "restored_keys": set()}
    state.handle_event("a", {"event": "tick", "party": [{"key": "A:1"}]})
    # Reconciler should NOT touch party_keys while a rebuild is in flight.
    assert "A:1" not in state.party_keys["a"]


def test_tick_reconcile_untracked_key_is_added_without_partner_pull(tmp_path, monkeypatch):
    """Lua reports a key the server has no link entry for — just sync, don't try to pull a partner."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState()
    state.pokeballs_obtained = {"a": True, "b": True}
    state.handle_event("a", {"event": "tick", "party": [{"key": "ORPHAN:42"}]})
    assert "ORPHAN:42" in state.party_keys["a"]
    # No partner pull because there's no link entry.
    cmds_b = state.handle_event("b", {"event": "tick", "party": []})
    assert noop_only(cmds_b)


def test_tick_reconcile_doesnt_double_pull_partner_already_in_party(tmp_path, monkeypatch):
    """If the partner is already in their party_keys, don't queue a redundant party_mon."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = make_state_with_link()
    # Only A's side is ghost-boxed; B's side is intact.
    state.party_keys["a"].discard("A:1")
    state.handle_event("a", {"event": "tick", "party": [{"key": "A:1"}]})
    assert "A:1" in state.party_keys["a"]
    cmds_b = state.handle_event("b", {"event": "tick", "party": [{"key": "B:2"}]})
    assert not has_cmd(cmds_b, "party_mon", "B:2"), (
        "partner already in their party_keys — no redundant party_mon should be queued"
    )


# ══════════════════════════════════════════════════════════════════════════
# Gift / egg received in a real encounter area must NOT lock that area's wild
# slot. Regression for the Vermilion City egg + Amaura/Anorith free-mon bugs:
# the gift is filed under a synthetic gift_<area> namespace instead.
# ══════════════════════════════════════════════════════════════════════════

# Four non-shiny Gen 3 keys (PID:OTID). The shiny test XORs the four 16-bit
# halves, which is order-independent, so all four are non-shiny. _EGG_A is the
# exact key test_gen3_adapter asserts non-shiny.
_EGG_A,  _EGG_B  = "12345678:87654321", "2468ACE0:1234ABCD"
_WILD_A, _WILD_B = "12345679:87654321", "2468ACE1:1234ABCD"


def _rr_state(tmp_path, monkeypatch):
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState(adapter=Gen3Adapter(is_rr=True))
    state.pokeballs_obtained = {"a": True, "b": True}
    state.party_size = {"a": 1, "b": 1}
    return state


def test_gift_link_area_remaps_real_area_only():
    """gift_link_area maps real areas into the gift namespace, leaves gift/daycare alone."""
    adapter = Gen3Adapter(is_rr=True)
    assert adapter.gift_link_area("vermilion_city") == "gift_vermilion_city"
    assert adapter.gift_link_area("oaks_lab") == "oaks_lab"          # already a gift area
    assert adapter.gift_link_area("gift_7_2") == "gift_7_2"          # already gift-namespaced
    assert adapter.gift_link_area("route5_pokemon_day_care") == "route5_pokemon_day_care"  # daycare


def test_area_display_name_synthetic_gift():
    """Synthetic gift_<area> renders as 'Gift – <host area>'; numeric gift ids unaffected."""
    adapter = Gen3Adapter(is_rr=True)
    host = adapter.area_display_name("vermilion_city")
    assert adapter.area_display_name("gift_vermilion_city") == f"Gift – {host}"
    assert adapter.area_display_name("gift_99_99").startswith("Gift")


def test_egg_in_real_area_does_not_lock_area(tmp_path, monkeypatch):
    """An egg caught by both players in Vermilion City forms a gift link and leaves
    the real area open for its actual wild encounter."""
    state = _rr_state(tmp_path, monkeypatch)

    state.handle_event("a", {"event": "capture", "key": _EGG_A,
                             "area_id": "vermilion_city", "species_id": 313,
                             "level": 1, "is_egg": True})
    state.handle_event("b", {"event": "capture", "key": _EGG_B,
                             "area_id": "vermilion_city", "species_id": 213,
                             "level": 1, "is_egg": True})

    # The egg did NOT lock vermilion_city ...
    assert state.area_states.get("vermilion_city") != AreaStatus.LINKED
    # ... and formed a standalone gift link under the gift namespace.
    gift_links = [ln for ln in state.links
                  if ln.area_id == "gift_vermilion_city" and ln.status == LinkStatus.ALIVE]
    assert len(gift_links) == 1

    # The real Vermilion wild encounter still links normally afterwards.
    state.handle_event("a", {"event": "area_enter", "area_id": "vermilion_city"})
    state.handle_event("b", {"event": "area_enter", "area_id": "vermilion_city"})
    state.handle_event("a", {"event": "capture", "key": _WILD_A,
                             "area_id": "vermilion_city", "species_id": 645, "level": 28})
    state.handle_event("b", {"event": "capture", "key": _WILD_B,
                             "area_id": "vermilion_city", "species_id": 984, "level": 28})
    assert state.area_states.get("vermilion_city") == AreaStatus.LINKED
    wild = next((ln for ln in state.links if ln.area_id == "vermilion_city"), None)
    assert wild is not None and wild.status == LinkStatus.ALIVE


def test_gift_flag_in_real_area_does_not_lock_area(tmp_path, monkeypatch):
    """Amaura/Anorith case: a non-egg gift (Lua gift=true — same shape the freeze-
    recovery path emits) in a real area forms a gift link, not the area link."""
    state = _rr_state(tmp_path, monkeypatch)

    state.handle_event("a", {"event": "capture", "key": _EGG_A,
                             "area_id": "vermilion_city", "species_id": 806,  # Amaura
                             "level": 25, "gift": True})
    cmds_b = state.handle_event("b", {"event": "capture", "key": _EGG_B,
                                      "area_id": "vermilion_city", "species_id": 390,  # Anorith
                                      "level": 25, "gift": True})

    assert state.area_states.get("vermilion_city") != AreaStatus.LINKED
    assert any(ln.area_id == "gift_vermilion_city" and ln.status == LinkStatus.ALIVE
               for ln in state.links)
    # Gift is not quarantined to the box.
    assert not has_cmd(cmds_b, "box_mon", _EGG_B)


def test_gift_flag_does_not_flip_pokeball_gate(tmp_path, monkeypatch):
    """A gift=true catch must not satisfy the Pokéball gate (it isn't a wild catch)."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = SoulLinkState(adapter=Gen3Adapter(is_rr=True))
    state.pokeballs_obtained = {"a": False, "b": False}
    state.party_size = {"a": 1, "b": 0}

    cmds = state.handle_event("a", {"event": "capture", "key": _EGG_A,
                                    "area_id": "vermilion_city", "species_id": 806,
                                    "level": 25, "gift": True})
    assert state.pokeballs_obtained["a"] is False
    assert not has_cmd(cmds, "box_mon", _EGG_A)


def test_gift_links_even_when_real_area_already_linked(tmp_path, monkeypatch):
    """A gift received in an area already LINKED by a wild encounter must still link
    (under the gift namespace), not be retired as an extra/already-linked capture."""
    state = _rr_state(tmp_path, monkeypatch)
    state.party_size = {"a": 2, "b": 2}

    # Wild encounter links vermilion_city first.
    state.handle_event("a", {"event": "area_enter", "area_id": "vermilion_city"})
    state.handle_event("b", {"event": "area_enter", "area_id": "vermilion_city"})
    state.handle_event("a", {"event": "capture", "key": _WILD_A,
                             "area_id": "vermilion_city", "species_id": 645, "level": 28})
    state.handle_event("b", {"event": "capture", "key": _WILD_B,
                             "area_id": "vermilion_city", "species_id": 984, "level": 28})
    assert state.area_states.get("vermilion_city") == AreaStatus.LINKED

    # Gift arrives in the now-linked area — must NOT be force-fainted.
    cmds_a = state.handle_event("a", {"event": "capture", "key": _EGG_A,
                                      "area_id": "vermilion_city", "species_id": 806,
                                      "level": 25, "gift": True})
    state.handle_event("b", {"event": "capture", "key": _EGG_B,
                             "area_id": "vermilion_city", "species_id": 390,
                             "level": 25, "gift": True})
    assert not has_cmd(cmds_a, "force_faint", _EGG_A)
    gift_link = next((ln for ln in state.links if ln.area_id == "gift_vermilion_city"), None)
    assert gift_link is not None and gift_link.status == LinkStatus.ALIVE


def test_multiple_gifts_in_same_area_each_link(tmp_path, monkeypatch):
    """Two separate gifts received in the same real area (e.g. an egg then a free
    mon in Vermilion City) must BOTH link under the gift namespace — a gift area is
    not a one-shot wild slot, and a later gift is never retired as 'already linked'."""
    state = _rr_state(tmp_path, monkeypatch)

    # Gift #1 (egg) — both players.
    state.handle_event("a", {"event": "capture", "key": _EGG_A,
                             "area_id": "vermilion_city", "species_id": 313, "level": 1, "is_egg": True})
    state.handle_event("b", {"event": "capture", "key": _EGG_B,
                             "area_id": "vermilion_city", "species_id": 213, "level": 1, "is_egg": True})
    # Gift #2 (free mon, gift flag) — both players, SAME real area.
    cmds_a = state.handle_event("a", {"event": "capture", "key": _WILD_A,
                                      "area_id": "vermilion_city", "species_id": 806, "level": 25, "gift": True})
    state.handle_event("b", {"event": "capture", "key": _WILD_B,
                             "area_id": "vermilion_city", "species_id": 390, "level": 25, "gift": True})

    # The second gift must NOT be retired/force-fainted.
    assert not has_cmd(cmds_a, "force_faint", _WILD_A)
    # Both gift pairs link under the gift namespace.
    gift_links = [ln for ln in state.links
                  if ln.area_id == "gift_vermilion_city" and ln.status == LinkStatus.ALIVE]
    assert len(gift_links) == 2
    # The real area was never locked by either gift.
    assert state.area_states.get("vermilion_city") != AreaStatus.LINKED


def test_key_change_on_a_buried_link_owes_the_faint_even_when_a_memorial_is_already_queued(tmp_path, monkeypatch):
    """Review cx-6aacc4f1 #2: the two obligations are deduplicated separately -- a queued
    memorialize(old) must not suppress the re-queued force_faint(new), and vice versa."""
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    state = make_state_with_link(a_key="AAAA:1111", b_key="BBBB:2222", status=LinkStatus.DEAD)
    state._queue_memorialize("a", "AAAA:1111")
    cmds = state.handle_event("a", {"event": "key_change", "old_key": "AAAA:1111", "new_key": "CCCC:1111"})
    cmds += state.handle_event("a", {"event": "tick"})
    assert has_cmd(cmds, "force_faint", "CCCC:1111"), "the faint is owed under the new key"
    assert sum(1 for c in cmds if c.get("cmd") == "memorialize" and c.get("key") == "CCCC:1111") == 1

    state2 = make_state_with_link(a_key="AAAA:1111", b_key="BBBB:2222", status=LinkStatus.DEAD)
    state2.queued_commands["a"].append({"cmd": "force_faint", "key": "AAAA:1111"})
    cmds2 = state2.handle_event("a", {"event": "key_change", "old_key": "AAAA:1111", "new_key": "CCCC:1111"})
    cmds2 += state2.handle_event("a", {"event": "tick"})
    assert sum(1 for c in cmds2 if c.get("cmd") == "force_faint" and c.get("key") == "CCCC:1111") == 1
    assert has_cmd(cmds2, "memorialize", "CCCC:1111"), "the memorial is owed even with a faint queued"



@pytest.mark.parametrize("raw", [[{"maxHP": 50}], [1], 5])
def test_a_malformed_party_snapshot_is_coerced_not_raised(raw):
    state = SoulLinkState()
    state.handle_event("a", {"event": "hello", "party": raw, "ot_id": "1234", "trainer_name": "A"})
    assert state.party_keys["a"] == set()
    state.handle_event("a", {"event": "hello", "ot_id": "1234", "trainer_name": "A",
                              "party": [{"key": "1234:1", "hp": 10, "maxHP": 50}]})
    assert state.party_keys["a"] == {"1234:1"} and state.party_size["a"] == 1

def test_leaving_retry_area_allows_later_no_catch_to_dead_zone():
    state = make_state_with_link()
    state.retry_same_species = False
    state.retry_areas["a"].add("route_2")
    state.handle_event("a", {"event": "area_enter", "area_id": "route_3"})
    state.handle_event("a", {"event": "no_catch", "area_id": "route_2"})
    assert state.area_states["route_2"] == AreaStatus.DEAD_ZONE


def test_stale_retry_with_pending_capture_does_not_wedge_game_over():
    state = make_state_with_link(status=LinkStatus.DEAD)
    state.retry_same_species = False
    state.retry_areas["a"].add("route_2")
    state.pending_captures["route_2"] = {"b": MonInfo(key="B:pending", level=5)}
    state._check_game_over()
    assert not state.run_over
    state.handle_event("a", {"event": "area_enter", "area_id": "oaks_lab"})
    commands = state.handle_event("a", {"event": "no_catch", "area_id": "route_2"})
    assert "route_2" not in state.pending_captures and state.run_over
    assert has_cmd(commands, "game_over")


def test_same_area_no_catch_without_area_enter_still_suppresses_retry():
    state = make_state_with_link()
    state.retry_areas["a"].add("route_2")
    commands = state.handle_event("a", {"event": "no_catch", "area_id": "route_2"})
    assert has_cmd(commands, "unresolve_area")
    assert state.area_states.get("route_2") != AreaStatus.DEAD_ZONE



def test_state_save_uses_shared_atomic_helper_and_survives_drive_lock(tmp_path, monkeypatch):
    import json
    import time

    from server import json_files
    state = SoulLinkState(data_dir=str(tmp_path))
    helper, replace = json_files.atomic_write_json, json_files.os.replace
    delegated, attempts = [], []
    def publish(path, value):
        delegated.append(path)
        return helper(path, value)
    def locked(source, target):
        attempts.append(target)
        if len(attempts) < 3:
            raise PermissionError("Drive lock")
        return replace(source, target)
    monkeypatch.setattr(json_files, "atomic_write_json", publish)
    monkeypatch.setattr(json_files.os, "replace", locked)
    monkeypatch.setattr(time, "sleep", lambda _: None)
    state._save()
    assert state._links_path in delegated and len(attempts) >= 3
    assert not state.save_failed and json.loads((tmp_path / "links.json").read_text())["links"] == []


@pytest.mark.parametrize("error", [ValueError("bad stats"), OSError("decode IO"), RuntimeError("decoder broke")])
def test_decode_failure_latches_and_refuses_events(tmp_path, monkeypatch, error):
    path = tmp_path / "links.json"
    path.write_text('{"links":[]}')
    def broken(*_args):
        raise error
    monkeypatch.setattr(SoulLinkState, "_load_mon_stats", broken)
    state = SoulLinkState.load(data_dir=str(tmp_path))
    assert state.load_failed == f"{type(error).__name__}: {error}"
    for event in ("hello", "tick", "capture"):
        commands = state.handle_event("a", {"event": event, "key": "A:1", "area_id": "route_1", "party": []})
        assert commands == [{"cmd": "noop", "refused": "load_failed"}]
    assert path.read_text() == '{"links":[]}' and not state.party_keys["a"]


@pytest.mark.parametrize("contents", ["{broken", "null", '{"area_states":{"route_1":"nonsense"}}'])
def test_load_distinguishes_unreadable_json_from_decode_errors(tmp_path, contents):
    (tmp_path / "links.json").write_text(contents)
    state = SoulLinkState.load(data_dir=str(tmp_path))
    assert bool(state.load_failed) == (contents != "{broken")


def test_open_error_remains_tolerant_but_unsafe_migration_raises(tmp_path, monkeypatch):
    import builtins

    from server.state import UnsafeGameMigration
    path = tmp_path / "links.json"
    path.write_text('{}')
    original = builtins.open
    def locked(name, *args, **kwargs):
        if str(name) == str(path):
            raise PermissionError("locked")
        return original(name, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(builtins, "open", locked)
        assert SoulLinkState.load(data_dir=str(tmp_path)).load_failed == ""
    def unsafe(*_args):
        raise UnsafeGameMigration("migration refused")
    monkeypatch.setattr(SoulLinkState, "_load_mon_stats", unsafe)
    with pytest.raises(UnsafeGameMigration, match="migration refused"):
        SoulLinkState.load(data_dir=str(tmp_path))


@pytest.mark.parametrize("uncertain", [False, True])
def test_prompt_terminal_records_uncertainty_without_claiming_a_decline(tmp_path, uncertain):
    import json
    from pathlib import Path

    from server.server import SLinkServer
    server = SLinkServer(data_dir=str(tmp_path / "server"))
    state = _with_trade_blobs(make_state_with_link())
    server.state, server.adapter = state, state.adapter
    state.on_trade_outcome = server._journal_trade
    token = _offer_and_pick(state)
    state.queued_commands = {"a": [], "b": []}
    b = state.handle_event("b", {"event": "menu_result", "token": token, "choice": 0, "uncertain": uncertain})
    a = state.handle_event("a", {"event": "tick"})
    assert state.pending_trade is None and state.links[0].status == LinkStatus.ALIVE
    if uncertain:
        assert [c["text"] for c in a if c["cmd"] == "msgbox"] == ["Trade canceled - no response."]
        assert [c["text"] for c in b if c["cmd"] == "msgbox"] == ["Trade canceled - no response."]
        assert state.trade_last["outcome"] == "uncertain" and state.trade_last["token"] == token
        assert server._build_status_dict()["trade_last"] == state.trade_last
        rows = json.loads(Path(server._events_path).read_text())
        assert len([r for r in rows if r["type"] == "trade_uncertain" and r["key"] == token]) == 1
        assert all("{}" not in r["text"] for r in rows if r["type"] == "trade_uncertain")
    else:
        assert any(c.get("text") == "Your partner declined the trade." for c in a)
        assert any(c.get("text") == "Trade declined." for c in b)
        assert state.trade_last is None



def test_a_valid_old_format_links_json_still_loads(tmp_path):
    import json
    payload = {"links": [{"area_id": "route_1", "status": "alive", "a": {"key": "A:1", "level": 5},
                          "b": {"key": "B:2", "level": 7}}],
               "area_states": {"route_1": "linked"}, "pokeballs_obtained": {"a": True, "b": False}}
    (tmp_path / "links.json").write_text(json.dumps(payload))
    loaded = SoulLinkState.load(data_dir=str(tmp_path))
    assert loaded.load_failed == "" and loaded.links[0].a.key == "A:1"
    assert loaded.links[0].b.key == "B:2" and loaded.links[0].status == LinkStatus.ALIVE
    assert loaded.area_states == {"route_1": AreaStatus.LINKED}
    assert loaded.pokeballs_obtained == {"a": True, "b": False}


def test_unknown_saved_area_state_is_a_strict_load_failure(tmp_path):
    (tmp_path / "links.json").write_text('{"area_states":{"route_1":"unknown"}}')
    loaded = SoulLinkState.load(data_dir=str(tmp_path))
    assert loaded.load_failed.startswith("ValueError:")
    assert loaded.handle_event("a", {"event": "tick"})[0]["refused"] == "load_failed"


@pytest.mark.parametrize("event", ["tick", "safe"])
def test_partial_party_snapshot_drops_bad_entries_with_warning(event, caplog):
    import logging
    state = make_state_with_link()
    state.handle_event("a", {"event": event, "party": [1, {"maxHP": 10}, {"key": "A:1", "hp": 5, "maxHP": 10}]})
    assert state.party_size["a"] == 1 and state.party_keys["a"] == {"A:1"}
    assert any(r.levelno == logging.WARNING and "party" in r.message for r in caplog.records)


@pytest.mark.parametrize("event", ["hello", "tick", "safe"])
@pytest.mark.parametrize("party", [[{"maxHP": 50}], [1], 5])
def test_nonempty_unkeyed_party_is_refused_without_zeroing(event, party):
    state = make_state_with_link()
    before = set(state.party_keys["a"]), state.party_size["a"]
    reply = state.handle_event("a", {"event": event, "party": party})
    assert any(c.get("refused") == "party_snapshot" for c in reply)
    assert (state.party_keys["a"], state.party_size["a"]) == before


def test_unkeyed_lead_cannot_bypass_the_identity_lock(caplog):
    state = SoulLinkState()
    state.handle_event("a", {"event": "hello", "trainer_name": "A", "party": [{"key": "AAAA:1111", "hp": 10, "maxHP": 10}]})
    message = {"event": "hello", "trainer_name": "Other", "party": [{"maxHP": 10}, {"key": "BBBB:2222", "hp": 10, "maxHP": 10}]}
    state.handle_event("a", message)
    assert message.get("_rejected") and "2222" in state.identity_error["a"]
    assert state.party_keys["a"] == {"AAAA:1111"}



def test_gift_area_retry_clear_is_logged_and_saved(tmp_path, caplog):
    import logging
    caplog.set_level(logging.INFO)
    state = SoulLinkState(data_dir=str(tmp_path))
    state.retry_areas["a"].add("route_2")
    state._save()
    state.handle_event("a", {"event": "area_enter", "area_id": "oaks_lab"})
    restored = SoulLinkState.load(data_dir=str(tmp_path), adapter=state.adapter)
    assert restored.retry_areas["a"] == set()
    assert "cleared retry areas" in caplog.text
