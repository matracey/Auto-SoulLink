"""KEY-SCOPE-5 (card KC-RETRY): Gen 1/2 parity for a RETRYABLE key_change refusal, ported to the
shared core (lua/core/session.lua + lua/core/identity.lua) so Gen 3 stops permanently losing a
retryable refusal (lua/gen3/client.lua now stamps `st.box_generation` / `st.boxes_ok`).

lua/gen1/client.lua Client.RETRYABLE_REJECTIONS (~141), resend_refused_change() (~376) and the
key_change_rejected branches (~687-705) are the reference: "box census unavailable" and
"ambiguous key (trade clash)" retire nothing -- the alias stays, and the SAME key_change goes
out again once a complete box census NEWER than the refusal has gone out. Every other reason is
terminal (the collision path, identity:reject). A retryable refusal must not show the red HUD
(HUD is player-facing only, owner ruling 2026-09-25): console log only.

Falsifiers 1-4 drive lua/core/session.lua directly under lupa (mirrors tests/unit/test_core_session.py's
World, with the KC-RETRY driver hooks `box_generation()` and `rescan_boxes()` added). Falsifier 5
is a cross-wire test through the real server (server/state.py / server/server.py), reusing the
Gen 3 census helpers from tests/unit/test_state_key_scope.py.

The round-2 falsifiers (review cx-876c8b77) cover the CLIENT half of the same window: a force_faint
the server sends under the OLD key while the refusal is in flight still lands, an incomplete
census is re-requested by the tick, the resend rides only a tick that actually went out, a refusal
with no stamped message sends nothing, and the real Gen 3 driver publishes ONE two-value
box_generation for the core (through tests/unit/gen3_world.py's World, the harness
tests/unit/test_gen3_client.py builds).

Round 3 (review cx-9ffbb054) covers the rescan's blast radius and the window's edges: the tick
re-requests an incomplete census only while a refusal is armed for one, stale evidence refuses
the retirement instead of guessing, a second key_change ends the first alias's authority, and
the ACK's own ordering decides what a command still naming the old key does.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from tests.unit import protocol_schema as ps

lupa = pytest.importorskip("lupa")

REPO = pathlib.Path(__file__).resolve().parents[2]
CORE = REPO / "lua" / "core"
SESSION = (CORE / "session.lua").as_posix()
IDENTITY = (CORE / "identity.lua").as_posix()
DEFERRED = (CORE / "deferred.lua").as_posix()
JSON = (REPO / "lua" / "json_codec.lua").as_posix()

A, B = "0000000A:12345678", "0000000B:12345678"
OLD, NEW = "000000EE:12345678", "000000FF:12345678"
OLD2, NEW2 = "000000C1:12345678", "000000C2:12345678"   # a SECOND change, mid-window

# KC-RETRY: the DRIVER from test_core_session.py plus the two optional retry hooks
# (box_generation -> gen, complete; rescan_boxes()), driven from `st.box_gen` / `st.box_ok`.
DRIVER = r"""
return function(st, sink)
    local d = { commands = {} }
    function d.frame() return st.frame end
    function d.read_party()
        sink("read_party")
        if st.party_ok then return st.party end
        return nil, "party unreadable"
    end
    function d.game_is_live()
        if not d.read_party() then return false, "party unreadable" end
        return true
    end
    function d.save_cleared() return st.cleared end
    function d.hello_ready()
        if not st.party_ok then return false, "party unreadable" end
        return st.hello_ready, "not live yet"
    end
    function d.hello_fields()
        return { rom_type = "firered", foundation = "gen3_frlg", party = st.json.array({}) }
    end
    function d.tick_fields() if st.tick_ok then return { in_battle = st.in_battle } end end
    function d.in_battle() return st.in_battle end
    function d.checkpoint_ok() return st.checkpoint, st.gate_why or "not at the overworld checkpoint" end
    function d.start() sink("start") return st.signals end
    function d.on_signal(sig) sink("signal", sig.kind) end
    function d.on_reset() sink("reset") end
    d.frame_hooks = {}
    function d.party_borrowed() return st.borrowed == true end
    -- KC-RETRY hooks: st.box_gen only advances on a successful rescan (mirrors gen1's own
    -- self.box_generation, and lua/gen3/client.lua's st.box_generation).
    function d.box_generation() return st.box_gen, st.box_ok end
    function d.rescan_boxes()
        sink("rescan_boxes")
        if st.scan_fails then st.box_ok = false
        else st.box_ok = true; st.box_gen = st.box_gen + 1 end
    end
    return d
end
"""


def _py(v):
    return str(v) if isinstance(v, (str, bytes)) or type(v).__name__ == "_LuaTable" else v


class World:
    def __init__(self):
        self.lua = L = lupa.LuaRuntime(unpack_returned_tuples=True)
        self.sent: list[dict] = []
        self.replies: list[str] = []
        self.hud: list[tuple] = []
        self.logs: list[str] = []
        self.connected = True
        self.json = L.eval(f'dofile("{JSON}")')
        net = L.table(connected=lambda: self.connected, pump=lambda: None,
                      send=self._send, receive=self._receive)
        self.net = net
        hud = L.table(
            show=lambda *a: self.hud.append(("show",) + tuple(_py(x) for x in a)),
            prompt=lambda *a: self.hud.append(("prompt",) + tuple(_py(x) for x in a)),
            set_game_over=lambda: None, set_rebuilding=lambda t: None, clear_rebuilding=lambda: None,
        )
        self.st = st = L.table(frame=0, party_ok=True, cleared=False, hello_ready=True, tick_ok=True,
                               in_battle=False, checkpoint=True, box_gen=0, box_ok=False,
                               scan_fails=False, json=self.json)
        self.set_party((A, 0), (B, 1))
        self.signals = L.table(drain=lambda s: L.table_from([]), close=lambda s: None)
        st.signals = self.signals
        self.calls: dict[str, int] = {}     # driver-hook call counts (rescan_boxes, read_party, ...)
        def sink(kind, *_rest):
            self.calls[kind] = self.calls.get(kind, 0) + 1
        self.driver = L.execute(DRIVER)(st, sink)
        Identity = L.eval(f'dofile("{IDENTITY}")')
        self.identity = Identity.new(L.table(key=L.eval("function(m) return m.key end")))
        exec_ = L.table(arm=lambda: None, disarm=lambda: None,
                        faint_slot=lambda s, c: None, deposit=lambda k, h: (True, None),
                        withdraw=lambda k, s, n: (True, None), memorialize=lambda k, h: (True, None))
        Deferred = L.eval(f'dofile("{DEFERRED}")')
        self.q = Deferred.new(L.table(exec=exec_, memorial_box=13))
        Session = L.eval(f'dofile("{SESSION}")')
        self.S = Session
        self.s = Session.new(L.table(net=net, json=self.json, hud=hud, log=lambda t: self.logs.append(str(t)),
                                     tag="[T]", player="a", game=self.driver, identity=self.identity,
                                     deferred=self.q))
        self.s.start(self.s)

    def _send(self, line):
        msg = json.loads(str(line))
        assert ps.validate_event(msg) == [], (msg, ps.validate_event(msg))
        self.sent.append(msg)

    def _receive(self):
        return self.replies.pop(0) if self.replies else None

    def mon(self, key, slot):
        return self.mon_ev(key, slot, [0x80], [1, 2])

    def mon_ev(self, key, slot, nick, moves):
        """A decoded record. `nick`/`moves` are the evidence lua/core/identity.lua freezes at a
        key_change (evidence_of): the two fields a key rename never touches, so a record that
        stops matching them is a DIFFERENT mon wearing the same key."""
        L = self.lua
        return L.table_from({"key": key, "slot": slot, "nickname": "MON", "level": 5, "max_hp": 20,
                             "nickname_bytes": L.table_from(nick), "moves": L.table_from(moves)})

    def set_party(self, *entries):
        self.set_party_mons(*[self.mon(*e) for e in entries])

    def set_party_mons(self, *mons):
        self.st.party = self.lua.table_from(list(mons))

    def command(self, **cmd):
        self.reply(cmd)

    def reply(self, *cmds):
        """One reply line carrying SEVERAL commands, in the order the server queued them."""
        for c in cmds:
            assert ps.validate_command(c) == [], ps.validate_command(c)
        self.replies.append(json.dumps({"commands": list(cmds)}))

    def step(self, n=1):
        for _ in range(n):
            self.st.frame = self.st.frame + 1
            self.s.frame_end(self.s)

    def step_to(self, frame):
        while self.st.frame < frame:
            self.step()

    def events(self, name):
        return [m for m in self.sent if m["event"] == name]

    def begin_alias(self, old_key, new_key, reason="npc_trade"):
        """Mirrors lua/gen3/client.lua settle_trade: begin_alias, then stamp `.msg` for a resend."""
        p = self.st.party
        self.identity.begin_alias(self.identity, old_key, new_key, p[1], p)
        msg = {"old_key": old_key, "new_key": new_key, "reason": reason, "new_species": 25}
        self.identity.pending.msg = self.lua.table_from(msg)
        self.s.send("key_change", self.identity.pending.msg)   # the original send (settle_trade)
        return msg


# -- falsifier 1: a retryable refusal keeps the alias and resends once, after a newer census ---

def test_a_retryable_refusal_keeps_the_alias_and_resends_once_after_a_newer_census():
    w = World()
    w.step_to(60)
    w.st.scan_fails = True                    # the rescan the refusal triggers won't land yet
    msg = w.begin_alias(OLD, NEW)
    w.command(cmd="key_change_rejected", old_key=OLD, new_key=NEW, reason="box census unavailable")
    w.step()
    assert w.identity.pending is not None and w.identity.retired(w.identity, OLD) is None
    assert not any(h[0] == "show" and "REFUSED" in str(h[1]) for h in w.hud)
    assert any("will retry" in line for line in w.logs)
    assert w.st.box_ok is False, "the requested rescan failed: still no complete census"
    # the census is not complete yet: no resend for many ticks
    w.step_to(300)
    assert [{k: m[k] for k in msg} for m in w.events("key_change")] == [msg]
    # a complete census strictly newer than the refusal's: resent exactly once, unchanged
    w.st.scan_fails = False
    w.driver.rescan_boxes(w.driver)           # a later, unrelated complete scan lands (gen 1)
    w.step_to(330)
    assert [{k: m[k] for k in msg} for m in w.events("key_change")] == [msg, msg]
    w.step_to(600)
    assert len(w.events("key_change")) == 2, "resent exactly once, never again"


# -- falsifier 2: no resend on an incomplete census or the same generation ---------------------

def test_no_resend_on_an_incomplete_census_or_the_same_generation():
    w = World()
    w.step_to(60)
    w.st.box_ok, w.st.box_gen = True, 1       # already at a complete generation 1
    w.st.scan_fails = True                    # the rescan the refusal triggers won't land yet
    w.begin_alias(OLD, NEW)
    w.command(cmd="key_change_rejected", old_key=OLD, new_key=NEW, reason="box census unavailable")
    w.step_to(300)
    assert len(w.events("key_change")) == 1, "retry_gen == 1: no NEWER complete census yet"
    assert w.st.box_ok is False, "the requested rescan failed"
    w.st.box_gen = 2                          # a counter bump alone, still incomplete
    w.step_to(360)
    assert len(w.events("key_change")) == 1, "incomplete: never resent, even if the counter moved"
    w.st.box_ok = True                        # now strictly newer AND complete
    w.step_to(390)
    assert len(w.events("key_change")) == 2


# -- falsifier 3: a census refusal requests a rescan; a trade-clash refusal does not -----------

def test_a_census_refusal_requests_a_rescan_a_trade_clash_refusal_does_not():
    w = World()
    w.step_to(60)
    w.st.box_ok, w.st.box_gen = True, 5    # a complete census already out, so no per-tick rescan
    w.begin_alias(OLD, NEW)
    w.command(cmd="key_change_rejected", old_key=OLD, new_key=NEW, reason="box census unavailable")
    w.step()
    assert w.st.box_gen == 6 and w.st.box_ok is True           # the refusal's rescan ran once

    w = World()
    w.step_to(60)
    w.st.box_ok, w.st.box_gen = True, 5
    w.begin_alias(OLD, NEW)
    w.command(cmd="key_change_rejected", old_key=OLD, new_key=NEW, reason="ambiguous key (trade clash)")
    w.step()
    assert w.st.box_gen == 5, "not a census reason: no rescan requested"
    assert w.identity.pending is not None


# -- falsifier 4: a non-retryable refusal still retires the alias, as today --------------------

def test_a_non_retryable_refusal_still_retires_the_alias():
    w = World()
    w.step_to(60)
    w.begin_alias(OLD, NEW)
    w.command(cmd="key_change_rejected", old_key=OLD, new_key=NEW, reason="key collision: party_keys")
    w.step()
    assert w.identity.pending is None
    assert w.identity.retired(w.identity, OLD) is not None
    assert any(h[0] == "show" and "REFUSED" in str(h[1]) for h in w.hud)
    w.step_to(600)
    assert len(w.events("key_change")) == 1, "terminal: never resent past the original"


# -- falsifier 5: cross-wire through the real server -------------------------------------------

def test_cross_wire_npc_trade_census_refusal_then_resend_reaches_partner_faint(tmp_path):
    """SERVER-SIDE REGRESSION GUARD, not a client test: the half of KEY-SCOPE-5 that
    lua/gen3/client.lua's retry relies on (mirrors tests/unit/test_state_key_scope.py's own
    KEY-SCOPE-5 census tests). An npc_trade key_change refused for "box census unavailable"
    retires nothing, and the identical key_change is accepted once a fresh complete census has
    gone out; a later faint under the new key still reaches the partner's link."""
    from server.state import LinkStatus
    from tests.unit.test_state_key_scope import (
        B1,
        B2,
        ONIX,
        _change5,
        _named,
        _snap5,
        _srv_two_links,
        _tick5,
    )

    srv, (l1, l2) = _srv_two_links(tmp_path)
    _tick5(srv, "b", _snap5(B1, B2))                                  # a complete (empty) census, gen 1
    srv._dispatch("b", {"event": "tick", "party": _snap5(ONIX, B2)})  # census omitted: stale
    cmds = _change5(srv, "b", B1)
    rej = _named(cmds, "key_change_rejected")
    assert rej and rej[0]["reason"] == "box census unavailable" and not _named(cmds, "key_change_ack")
    assert l1.status == LinkStatus.ALIVE and srv.state.entry_for("b", B1) is l1

    _tick5(srv, "b", _snap5(ONIX, B2), gen=2)                         # a fresh, newer complete census
    cmds2 = _change5(srv, "b", B1)                                    # the SAME key_change, resent
    assert _named(cmds2, "key_change_ack") and not _named(cmds2, "key_change_rejected")
    assert srv.state.entry_for("b", ONIX) is l1 and srv.state.entry_for("b", B1) is None

    srv.state.handle_event("b", {"event": "faint", "key": ONIX})       # under the NEW key
    a_cmds = srv.state.handle_event("a", {"event": "noop"})
    assert [c["key"] for c in _named(a_cmds, "force_faint")] == [l1.a.key]
    assert l1.status == LinkStatus.DEAD


# -- round 2 (review cx-876c8b77): the client's own half of the same window ----------------------

def test_a_force_faint_under_the_old_key_lands_during_the_retry_window():
    """The server names the mon by the OLD key for the whole refusal window, so a force_faint it
    sends in that window must reach the record the cartridge actually holds (MAJOR-1: the
    resolver ignored the pending alias, so route_force dropped it as "key not in party")."""
    w = World()
    w.step_to(60)                                  # hello out, writes enabled
    w.set_party((NEW, 0))                          # the cartridge holds the NEW key, and only it
    w.begin_alias(OLD, NEW)
    w.command(cmd="key_change_rejected", old_key=OLD, new_key=NEW, reason="ambiguous key (trade clash)")
    w.step()
    fainted: list[tuple] = []
    w.q.exec.faint_slot = lambda slot, name: fainted.append((int(slot), str(name)))
    w.command(cmd="force_faint", key=OLD, nickname="MON")
    w.step()
    assert fainted == [(0, "force_faint")], "the old key must resolve to the record the cartridge holds"
    assert not any("key not in party" in line for line in w.logs), w.logs


def test_the_tick_rescans_only_for_an_armed_refusal_and_at_most_once_per_tick():
    """The per-tick rescan exists for ONE consumer: a refused key_change waiting for a newer
    COMPLETE census. Ungated, a scan that cannot land (a null storage pointer across a menu or a
    battle) is re-requested every 30 frames for the rest of the session, and the only thing it
    can change is the generation nobody is waiting on (F1)."""
    w = World()
    w.step_to(60)
    w.calls.clear()                                 # count only what the ticks under test do
    w.st.box_ok, w.st.box_gen = False, 7            # a scan that did not land ...
    w.st.scan_fails = True                          # ... and none that would
    w.step_to(600)                                  # 18 ticks, nothing armed
    assert w.calls.get("rescan_boxes", 0) == 0, "no refusal is waiting: the tick never re-requests"
    assert (int(w.st.box_gen), w.st.box_ok) == (7, False), "and the generation stands still"

    w.set_party((NEW, 0))
    w.begin_alias(OLD, NEW)
    w.command(cmd="key_change_rejected", old_key=OLD, new_key=NEW, reason="ambiguous key (trade clash)")
    w.step_to(660)                                  # two more ticks, a refusal armed
    assert w.calls["rescan_boxes"] == 2, "one per tick while the refusal waits, never more"
    assert int(w.st.box_gen) == 7, "a scan that cannot land still does not advance the generation"

    w.st.scan_fails = False                         # the PC wakes up: a newer complete census lands
    w.step_to(690)
    assert w.calls["rescan_boxes"] == 3 and int(w.st.box_gen) == 8
    assert len(w.events("key_change")) == 2, "and the refusal is re-sent off it, exactly once"
    w.step_to(900)
    assert w.calls["rescan_boxes"] == 3, "not armed any more: the census is complete, so the tick stops"
    assert len(w.events("key_change")) == 2, "never re-sent a second time"


def test_the_resend_waits_for_a_tick_that_actually_goes_out():
    """The resend is justified by a NEWER COMPLETE CENSUS, so it may only ride a tick that
    published one; a tick with no party publishes nothing and the refusal stays armed (MAJOR-3)."""
    w = World()
    w.step_to(60)
    w.st.box_ok, w.st.box_gen = True, 1
    msg = w.begin_alias(OLD, NEW)
    w.command(cmd="key_change_rejected", old_key=OLD, new_key=NEW, reason="box census unavailable")
    w.step_to(66)
    assert (int(w.st.box_gen), w.st.box_ok) == (2, True)   # the refusal asked for that rescan at once
    w.st.tick_ok = False                              # tick_fields() -> nil: nothing to publish
    w.step_to(90)                                     # the census tick goes nowhere
    assert len(w.events("key_change")) == 1, "the resend rides the tick that carries the census"
    assert w.identity.pending.retry_gen is not None, "and the refusal stays armed until one goes out"
    w.st.tick_ok = True
    w.step_to(120)
    assert [{k: m[k] for k in msg} for m in w.events("key_change")] == [msg, msg]


def test_hidden_tick_cannot_consume_newer_census_retry_credit():
    w = World()
    w.step_to(60)
    w.st.box_ok, w.st.box_gen = True, 1
    w.begin_alias(OLD, NEW, reason="nature_change")
    w.command(cmd="key_change_rejected", old_key=OLD, new_key=NEW, reason="box census unavailable")
    w.step()
    w.driver.tick_fields = lambda: w.lua.table_from({"party_hidden": True})
    w.s.send_tick(w.s)
    assert w.events("tick")[-1]["party_hidden"] is True
    assert len(w.events("key_change")) == 1
    assert w.identity.pending.retry_gen is not None
    w.driver.tick_fields = lambda: w.lua.table_from({"in_battle": False})
    w.s.send_tick(w.s)
    assert len(w.events("key_change")) == 2 and w.identity.pending.retry_gen is None


def test_a_refusal_with_no_stamped_message_sends_nothing_and_stays_armed():
    """Identity:begin_alias never sets `msg` (the driver stamps the message it sent). Without one
    there is nothing to re-send, and inventing an empty key_change puts an unwireable event on
    the protocol -- so the refusal must stay armed, waiting for a real message (MINOR)."""
    w = World()
    w.step_to(60)
    w.st.box_ok, w.st.box_gen = True, 1
    p = w.st.party
    w.identity.begin_alias(w.identity, OLD, NEW, p[1], p)       # no .msg: nothing was stamped
    w.command(cmd="key_change_rejected", old_key=OLD, new_key=NEW, reason="box census unavailable")
    w.step_to(66)
    assert int(w.st.box_gen) == 2, "the refusal asked for the rescan at once"
    w.step_to(120)                                      # a newer complete census goes out
    assert w.events("key_change") == [], "no key_change to re-send, and none is invented"
    assert w.identity.pending is not None
    assert w.identity.pending.retry_gen is not None, "still armed for a stamped message"


def test_the_real_gen3_driver_publishes_the_core_two_value_box_generation():
    """lua/gen3/client.lua kept TWO box_generation accessors -- a one-value local for the wire
    field and a two-value drv hook -- which could disagree. There is one now: the core's
    (generation, last-scan-complete) pair, of which the wire field takes the nil-if-incomplete
    half (MAJOR-4). Driven through the real pack data, as tests/unit/test_gen3_client.py does."""
    from tests.unit.gen3_world import World as Gen3World

    w = Gen3World()
    drv = w.client.driver
    drv.rescan_boxes()
    gen, complete = drv.box_generation()
    gen = int(gen)
    assert complete is True and gen >= 1, "a complete scan publishes (gen, true)"
    psp = w.wc["pointers"]["gPokemonStoragePtr"]["address"]
    w.poke_int(psp, 0, 4)                             # the storage pointer is null: the scan stops
    drv.rescan_boxes()
    assert drv.box_generation() == (gen, False), "an incomplete scan publishes (gen, false)"
    w.poke_int(psp, w.ram["POKEMON_STORAGE_BASE"], 4)
    drv.rescan_boxes()
    assert drv.box_generation() == (gen + 1, True), "only a complete scan advances the generation"


# -- round 3 (review cx-9ffbb054): the edges of the refusal window -------------------------------

def test_stale_evidence_under_a_pending_alias_refuses_the_force_faint_as_lost():
    """The alias resolves the old key on EVIDENCE FROZEN at the change (nickname bytes + moves),
    never on the key alone. A record that has stopped matching it is a different mon wearing the
    same key, so the retirement is refused rather than guessed: a force_faint faints a partner for
    good, and guessing here faints the wrong one."""
    w = World()
    w.step_to(60)
    w.set_party((NEW, 0), (B, 1))                    # p[1] freezes the changed record's evidence
    w.begin_alias(OLD, NEW)
    w.set_party_mons(w.mon_ev(NEW, 0, [0x81], [1, 2]))   # same key, the evidence is gone
    fainted: list[tuple] = []
    w.q.exec.faint_slot = lambda slot, name: fainted.append((int(slot), str(name)))
    w.step()
    w.command(cmd="force_faint", key=OLD, nickname="MON")
    w.step()
    assert fainted == [], "a record that stopped matching the alias is never the one to faint"
    assert any("retired record left the party" in line for line in w.logs), w.logs
    assert w.identity.pending is not None, "a refused command consumes nothing"


def test_a_second_unrelated_key_change_cannot_redirect_the_first_olds_authority():
    """Retained aliases must not redirect a command to a different mon's new identity."""
    w = World()
    w.step_to(60)
    w.set_party((NEW, 0))
    w.begin_alias(OLD, NEW)
    w.set_party((NEW2, 0))
    w.begin_alias(OLD2, NEW2)                        # the second change lands before the first is answered
    w.step()
    fainted: list[tuple] = []
    w.q.exec.faint_slot = lambda slot, name: fainted.append((int(slot), str(name)))
    w.command(cmd="force_faint", key=OLD, nickname="MON")
    w.step()
    assert fainted == [], "the first alias names a record that is no longer in the party"
    assert any("retired record left the party" in line for line in w.logs), w.logs
    w.command(cmd="force_faint", key=OLD2, nickname="MON")
    w.step()
    assert fainted == [(0, "force_faint")], "the alias that IS pending still resolves"


def test_an_ack_before_the_force_faint_in_one_reply_drops_the_stale_old_key_command():
    """Reply order is the server's queue order. The ACK commits the rename and CONSUMES the alias
    (apply_ack -> identity:ack), so a command that still names the old key after it, in the SAME
    reply, is stale by construction: it is dropped by name, not resolved to anything. Only a
    command already HELD when the ACK landed follows the rename -- the next test."""
    w = World()
    w.step_to(60)
    w.set_party((NEW, 0))
    w.begin_alias(OLD, NEW)
    fainted: list[tuple] = []
    w.q.exec.faint_slot = lambda slot, name: fainted.append((int(slot), str(name)))
    w.reply({"cmd": "key_change_ack", "old_key": OLD, "new_key": NEW, "migrated": True},
            {"cmd": "force_faint", "key": OLD, "nickname": "MON"})
    w.step()
    assert w.identity.pending is None, "the ACK consumed the alias"
    assert fainted == [], "nothing resolves the old key once the ACK has landed"
    assert any("key not in party" in line for line in w.logs), w.logs


@pytest.mark.parametrize("initial_hidden", [False, True])
def test_real_gen3_server_and_core_hidden_window_finishes_same_pair_once(tmp_path, initial_hidden):
    from server.state import LinkEntry, LinkStatus, MonInfo
    from tests.unit.test_gen3_trade_server import KEYS, _mon, _server

    srv = _server(tmp_path, "firered_rr")
    old, new = KEYS["a"], "00000003:00000011"
    link = LinkEntry(
        area_id="route_1",
        status=LinkStatus.ALIVE,
        a=MonInfo(key=old, species=1, level=12),
        b=MonInfo(key=KEYS["b"], species=4, level=12),
    )
    srv.state.links.append(link)
    srv.state._index_entry(link)
    srv._dispatch(
        "a", {"event": "tick", "party": [_mon("a")], "pc_boxes": [], "pc_boxes_generation": 4}
    )
    if initial_hidden:
        srv._dispatch("a", {"event": "tick", "party_hidden": True})
    else:
        srv._dispatch(
            "a", {"event": "tick", "party": [_mon("a", key=new)]}
        )  # stale census, old party key reconciled away
    w = World()
    w.step_to(60)
    w.st.box_ok, w.st.box_gen = True, 4
    w.set_party((old, 0))
    w.s.seq = 152
    responses = []

    def wire(line):
        w._send(line)
        msg = json.loads(str(line))
        if msg["event"] in ("key_change", "tick"):
            commands = srv._dispatch("a", msg)
            responses.extend(commands)
            w.reply(*commands)

    w.net.send = wire
    w.begin_alias(old, new, reason="nature_change")
    w.set_party((new, 0))
    w.step()
    assert responses[-1]["reason"] == (
        "party hidden" if initial_hidden else "box census unavailable"
    )

    def hidden():
        return w.lua.table_from({"party_hidden": True})

    w.driver.tick_fields = hidden
    w.s.send_tick(w.s)
    w.step()
    assert len(w.events("key_change")) == 1 and w.identity.pending.retry_gen is not None

    def visible():
        return w.lua.table_from(
            {
                "party": w.json.array(w.lua.table_from([w.lua.table_from(_mon("a", key=new))])),
                "pc_boxes": w.json.array(w.lua.table()),
                "pc_boxes_generation": int(w.st.box_gen),
                "in_battle": False,
            }
        )

    w.driver.tick_fields = visible
    w.s.send_tick(w.s)
    w.step()
    assert len(w.events("key_change")) == 2
    assert (
        w.identity.pending is None
    )  # migrated ACK ends the alias; retirement is for nonmigrated ACK
    assert srv.state.entry_for("a", new) is link and srv.state.entry_for("a", old) is None
    assert link.status == LinkStatus.ALIVE
    assert (
        len([c for c in responses if c["cmd"] == "key_change_ack" and c.get("migrated") is True])
        == 1
    )
    w.s.send_tick(w.s)
    w.step()
    assert len(w.events("key_change")) == 2


@pytest.mark.parametrize(
    "hidden,connected,tick_ok", [(True, True, True), (False, False, True), (False, True, False)]
)
def test_retry_credit_survives_unpublished_or_hidden_tick(hidden, connected, tick_ok):
    w = World()
    w.step_to(60)
    w.st.box_ok, w.st.box_gen = True, 1
    w.begin_alias(OLD, NEW)
    w.command(cmd="key_change_rejected", old_key=OLD, new_key=NEW, reason="party hidden")
    w.step()
    w.connected = connected
    w.driver.tick_fields = lambda: w.lua.table_from({"party_hidden": hidden}) if tick_ok else None
    w.s.send_tick(w.s)
    assert w.identity.pending.retry_gen is not None and len(w.events("key_change")) == 1


def test_hidden_rejection_for_another_pair_cannot_arm_current_alias():
    w = World()
    w.step_to(60)
    w.st.box_ok, w.st.box_gen = True, 1
    w.begin_alias(OLD, NEW)
    scans = w.calls.get("rescan_boxes", 0)
    w.command(cmd="key_change_rejected", old_key=OLD, new_key=NEW2, reason="party hidden")
    w.step()
    assert w.identity.pending.retry_gen is None and w.calls.get("rescan_boxes", 0) == scans
