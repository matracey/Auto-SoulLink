"""lua/core/session.lua (P4 C4-1): the generation-neutral client shell lifted from
lua/gen1/client.lua (send/seq :171-182, validate/pause :317-358, ack_cancel :361-365, generic
commands :480-596, hello gate :1804-1826, signal drain :1828-1845, tick/safe :1859-1863, receive
loop :1864-1876), driven under lupa over a fake game driver, the real identity/deferred modules
and the real json codec. Every line the session sends is checked against protocol_schema.

First falsifier: a driver whose read_party goes nil for MAX_INVALID validations pauses writes,
the deferred queue survives intact, and ONE live validation re-enables writes.

Owner ruling 2026-09-23: in-battle force_faint/force_explode are held in a core-owned,
alias-aware pending-battle-write set flushed every frame through game.battle_write
("done" | "hold" | nil); on battle end a final attempt, then the deferred checkpoint queue.
"""
from __future__ import annotations

import json
import pathlib
import re

import pytest

from tests.unit import protocol_schema as ps

lupa = pytest.importorskip("lupa")

REPO = pathlib.Path(__file__).resolve().parents[2]
CORE = REPO / "lua" / "core"
SESSION = (CORE / "session.lua").as_posix()
IDENTITY = (CORE / "identity.lua").as_posix()
DEFERRED = (CORE / "deferred.lua").as_posix()
JSON = (REPO / "lua" / "json_codec.lua").as_posix()

A, B, C = "0000000A:12345678", "0000000B:12345678", "0000000C:12345678"
OLD, NEW = "000000EE:12345678", "000000FF:12345678"

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
        sink("live")
        if not d.read_party() then return false, "party unreadable" end
        return true
    end
    function d.save_cleared() return st.cleared end
    function d.hello_ready()                      -- the contract: live AND (battle OR checkpoint)
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
    function d.on_signal(sig)
        sink("signal", sig.kind)
        if sig.kind == "boom" then error("reducer blew up") end
    end
    function d.on_reset() sink("reset") end
    function d.play_sound(id) sink("sound", id) end
    d.frame_hooks = { function() sink("hook") end }
    function d.party_borrowed() return st.borrowed == true end
    if st.battle_path then
        function d.battle_write(entry, slot, mon, ending)
            sink("battle_write", entry.cmd, entry.key, slot, ending)
            return st.battle_result, st.battle_why
        end
    end
    return d
end
"""


def _py(v):
    return str(v) if isinstance(v, (str, bytes)) or type(v).__name__ == "_LuaTable" else v


class World:
    def __init__(self, battle_path=True, instrumentation_key=None):
        self.lua = L = lupa.LuaRuntime(unpack_returned_tuples=True)
        L.globals().SLINK_INSTRUMENTATION_KEY = instrumentation_key
        self.timeline: list[tuple] = []
        self.sent: list[dict] = []
        self.replies: list[str] = []
        self.hud: list[tuple] = []
        self.logs: list[str] = []
        self.signal_queue: list = []
        self.connected = True
        self.json = L.eval(f'dofile("{JSON}")')
        net = L.table(connected=lambda: self.connected, pump=lambda: self.timeline.append(("pump",)),
                      send=self._send, receive=self._receive)
        hud = L.table(
            show=lambda *a: self.hud.append(("show",) + tuple(_py(x) for x in a)),
            prompt=lambda *a: self.hud.append(("prompt",) + tuple(_py(x) for x in a)),
            set_game_over=lambda: self.hud.append(("game_over",)),
            set_rebuilding=lambda t: self.hud.append(("rebuilding", str(t))),
            clear_rebuilding=lambda: self.hud.append(("rebuild_done",)),
        )
        self.st = st = L.table(frame=0, party_ok=True, cleared=False, hello_ready=True, tick_ok=True,
                               in_battle=False, checkpoint=True, battle_path=battle_path,
                               battle_why="target active", json=self.json)
        self.set_party((A, 0), (B, 1))
        self.signals = L.table(drain=lambda s: L.table_from(self._drain()),
                               close=lambda s: self.timeline.append(("close",)))
        st.signals = self.signals
        self.driver = L.execute(DRIVER)(st, lambda *a: self.timeline.append(tuple(_py(x) for x in a)))
        Identity = L.eval(f'dofile("{IDENTITY}")')
        self.identity = Identity.new(L.table(key=L.eval("function(m) return m.key end")))
        exec_ = L.table(
            arm=lambda: None, disarm=lambda: None,
            faint_slot=lambda s, c: self.timeline.append(("faint_slot", s, str(c))),
            deposit=lambda k, h: self._exec("deposit", k),
            withdraw=lambda k, s, n: self._exec("withdraw", k),
            memorialize=lambda k, h: self._exec("memorialize", k),
        )
        Deferred = L.eval(f'dofile("{DEFERRED}")')
        self.q = Deferred.new(L.table(exec=exec_, memorial_box=13))
        Session = L.eval(f'dofile("{SESSION}")')
        self.S = Session
        self.s = Session.new(L.table(net=net, json=self.json, hud=hud, log=lambda t: self.logs.append(str(t)),
                                     tag="[T]", player="a", game=self.driver, identity=self.identity,
                                     deferred=self.q))
        self.s.start(self.s)

    # -- fakes --------------------------------------------------------------------------
    def _send(self, line):
        msg = json.loads(str(line))
        assert ps.validate_event(msg) == [], (msg, ps.validate_event(msg))
        self.sent.append(msg)
        self.timeline.append(("sent", msg["event"]))

    def _receive(self):
        self.timeline.append(("receive",))
        return self.replies.pop(0) if self.replies else None

    def _drain(self):
        out, self.signal_queue = self.signal_queue, []
        return out

    def _exec(self, name, key):
        self.timeline.append((name, str(key)))
        return True, None

    # -- helpers ------------------------------------------------------------------------
    def mon(self, key, slot, moves=(1, 2)):
        L = self.lua
        return L.table_from({"key": key, "slot": slot, "nickname": "MON", "level": 5, "max_hp": 20,
                             "nickname_bytes": L.table_from([0x80]), "moves": L.table_from(list(moves))})

    def set_party(self, *entries):
        self.st.party = self.lua.table_from([self.mon(*e) for e in entries])

    def command(self, **cmd):
        assert ps.validate_command(cmd) == [], ps.validate_command(cmd)
        self.replies.append(json.dumps({"commands": [cmd]}))

    def step(self, n=1):
        for _ in range(n):
            self.st.frame = self.st.frame + 1
            self.s.frame_end(self.s)

    def step_to(self, frame):
        while self.st.frame < frame:
            self.step()

    def events(self, name):
        return [m for m in self.sent if m["event"] == name]

    def called(self, name):
        return [t for t in self.timeline if t[0] == name]

    def battle_writes(self):
        return [t[1:] for t in self.called("battle_write")]


# -- falsifier 1: pause, never drop -------------------------------------------------------

def test_two_identity_acknowledgements_migrate_a_held_command_through_both_keys():
    w = World()
    w.step_to(60)
    w.st.checkpoint = False
    w.set_party((OLD, 0))
    w.command(cmd="box_mon", key=OLD)
    w.step()
    for before, after in ((OLD, NEW), (NEW, A)):
        w.set_party((after, 0))
        p = w.st.party
        w.identity.begin_alias(w.identity, before, after, p[1], p)
    w.command(cmd="key_change_ack", old_key=OLD, new_key=NEW, migrated=True)
    w.command(cmd="key_change_ack", old_key=NEW, new_key=A, migrated=True)
    w.step()
    assert w.q["items"][1].key == A
    assert not w.identity.active(w.identity)


def test_falsifier_unreadable_party_pauses_writes_keeps_the_queue_and_one_live_validation_resumes():
    w = World()
    w.step_to(60)
    assert w.s.writes_enabled is True
    w.st.checkpoint = False
    w.command(cmd="box_mon", key=A)
    w.step()
    assert w.q.size(w.q) == 1
    w.st.party_ok = False
    w.step_to(60 + 60 * (w.S.MAX_INVALID - 1))           # MAX_INVALID - 1 failed validations
    assert w.s.writes_enabled is True
    w.step_to(60 + 60 * w.S.MAX_INVALID)                 # the MAX_INVALID-th
    assert w.s.writes_enabled is False
    assert any("PAUSED" in line for line in w.logs)
    w.st.checkpoint = True
    w.step_to(60 + 60 * w.S.MAX_INVALID + 59)
    assert w.q.size(w.q) == 1 and w.called("deposit") == []  # paused, not dropped
    head = w.q["items"][1]
    assert head.cmd == "box_mon" and head.key == A
    w.st.party_ok = True
    w.step()                                             # ONE live validation
    assert w.s.writes_enabled is True
    assert w.called("deposit") == [("deposit", A)] and w.q.size(w.q) == 0


def test_a_cleared_save_resets_hello_every_alias_and_the_driver():
    w = World()
    w.step()
    assert len(w.events("hello")) == 1
    p = w.st.party
    w.identity.begin_alias(w.identity, OLD, A, p[1], p)
    w.st.party_ok, w.st.cleared = False, True
    w.step_to(60)
    assert w.s.hello_sent is False and not w.identity.active(w.identity)
    assert w.called("reset") == [("reset",)]
    w.st.party_ok, w.st.cleared = True, False
    w.step()
    assert len(w.events("hello")) == 2


# -- hello / tick / validate cadence ------------------------------------------------------

def test_hello_is_not_sent_while_the_driver_is_not_hello_ready():
    w = World()
    w.st.hello_ready = False
    w.step(100)
    assert w.events("hello") == [] and w.events("tick") == []
    w.st.hello_ready = True
    w.step(5)
    (hello,) = w.events("hello")
    assert hello["rom_type"] == "firered" and hello["party"] == [] and hello["writes_enabled"] is True


def test_hello_carries_the_launcher_instrumentation_key():
    w = World(instrumentation_key="player-a-key")

    w.step()

    (hello,) = w.events("hello")
    assert hello["instrumentation_key"] == "player-a-key"


def test_a_disconnect_drops_events_and_the_reconnect_hellos_again_with_seq_continuing():
    w = World()
    w.step()
    w.connected = False
    assert w.s.send("status", w.lua.table(badges=1)) is False
    w.step(3)
    w.connected = True
    w.step()
    hellos = w.events("hello")
    assert len(hellos) == 2 and hellos[1]["seq"] == hellos[0]["seq"] + 1


def test_frame_order_pump_then_hello_then_receive():
    w = World()
    w.command(cmd="hud_show", text="hi")
    w.step()
    order = [t[0] if t[0] != "sent" else t[1] for t in w.timeline]
    assert order.index("pump") < order.index("hello") < order.index("receive")


def test_tick_cadence_is_thirty_frames():
    w = World()
    w.step_to(120)
    assert len(w.events("tick")) == 120 // w.S.TICK_INTERVAL == 4
    w.st.tick_ok = False
    w.step_to(240)
    assert len(w.events("tick")) == 4


def test_validate_cadence_is_sixty_frames():
    w = World()
    w.step_to(180)
    assert len(w.called("live")) == 180 // w.S.VALIDATE_EVERY == 3


# -- generic commands ---------------------------------------------------------------------

@pytest.mark.parametrize("name", sorted(ps.PROMPT_CANCEL))
def test_prompts_are_answered_with_the_cancel_sentinels(name):
    w = World()
    w.step()
    fields = {"show_choices": {"options": ["A", "B"], "text": "?"}, "show_menu": {"text": "?"},
              "choose_mon": {}}[name]
    w.command(cmd=name, token="t1", **fields)
    w.step()
    event, field, value = ps.PROMPT_CANCEL[name]
    (reply,) = w.events(event)
    assert reply["token"] == "t1" and reply[field] == value


def test_a_driver_command_consumes_or_falls_through_to_the_generic_handler():
    w = World()
    w.driver.commands.show_menu = w.lua.eval("function(cmd) return cmd.slot ~= nil end")
    w.step()
    w.command(cmd="show_menu", token="t1", text="trade?", slot=2, blob_hex="00")
    w.command(cmd="show_menu", token="t2", text="?")
    w.step()
    assert [r["token"] for r in w.events("menu_result")] == ["t2"]


def test_an_apply_trade_without_a_driver_path_answers_and_writes_nothing():
    w = World()
    w.step()
    n = len(w.sent)
    w.command(cmd="apply_trade", slot=0, blob_hex="00" * 100, old_key=A, token="t")
    w.step(2)
    assert [m["event"] for m in w.sent[n:]] == [] and w.q.size(w.q) == 0
    assert any("apply_trade" in line for line in w.logs)


def test_hud_commands_game_over_rebuild_and_state_commands():
    w = World()
    w.step()
    w.command(cmd="msgbox", text="hello", r=1, g=2, b=3, frames=4)
    w.command(cmd="hud_show", text="toast", color=[9, 8, 7], duration=50)
    w.command(cmd="rebuild_start", text="REBUILD", keys=[A])
    w.command(cmd="rebuild_done")
    w.command(cmd="resolved_areas", areas=["route1", "route2"])
    w.command(cmd="unresolve_area", area_id="route1")
    w.command(cmd="config", native_sounds=True)
    w.command(cmd="play_sound", sound=25)
    w.command(cmd="game_over")
    w.command(cmd="noop")
    w.step()
    assert ("prompt", "hello", 1, 2, 3, 4) in w.hud and ("show", "toast", 9, 8, 7, 50) in w.hud
    assert ("rebuilding", "REBUILD") in w.hud and ("rebuild_done",) in w.hud and ("game_over",) in w.hud
    assert w.s.resolved_areas["route1"] is None and w.s.resolved_areas["route2"] is True and w.s.seeded
    assert w.s.config.native_sounds is True
    assert w.called("sound") == [("sound", 25), ("sound", 26)] and w.s.game_over is True


def test_an_unknown_command_is_logged_and_a_throwing_one_does_not_stop_the_reply():
    w = World()
    w.step()
    w.driver.commands.hud_show = w.lua.eval("function() error('driver bug') end")
    w.replies.append(json.dumps({"commands": [{"cmd": "hud_show", "text": "x"}, {"cmd": "zzz"},
                                              {"cmd": "show_menu", "token": "t", "text": "?"}]}))
    w.replies.append("{not json")
    w.step()
    assert any("driver bug" in line for line in w.logs) and any("zzz" in line for line in w.logs)
    assert any("unreadable reply" in line for line in w.logs) and len(w.events("menu_result")) == 1


def test_key_change_ack_and_rejection_drive_the_identity():
    w = World()
    w.step()
    p = w.st.party
    w.identity.begin_alias(w.identity, OLD, A, p[1], p)
    w.command(cmd="key_change_ack", old_key=OLD, new_key=A, migrated=True)
    w.step()
    assert not w.identity.active(w.identity)
    w.identity.begin_alias(w.identity, OLD, A, p[1], p)
    w.command(cmd="key_change_rejected", old_key=OLD, new_key=A, reason="collision")
    w.step()
    assert w.identity.retired(w.identity, OLD) is not None
    assert any(h[0] == "show" and "REFUSED" in h[1] for h in w.hud)


# -- force_faint / force_explode routing ----------------------------------------------------

def test_force_faint_with_an_unreadable_party_is_deferred():
    w = World()
    w.step()
    w.st.party_ok = False
    w.command(cmd="force_faint", key=A)
    w.step()
    assert w.q.size(w.q) == 1 and w.battle_writes() == []


def test_force_faint_for_a_key_not_in_the_party_is_dropped():
    w = World()
    w.step()
    w.command(cmd="force_faint", key=C)
    w.step(3)
    assert w.q.size(w.q) == 0 and w.called("faint_slot") == []
    assert any("key not in party" in line for line in w.logs)


def test_force_faint_for_an_ambiguous_key_is_refused():
    w = World()
    w.set_party((A, 0), (A, 1))
    w.step()
    w.command(cmd="force_faint", key=A)
    w.step(3)
    assert w.q.size(w.q) == 0 and w.called("faint_slot") == [] and w.battle_writes() == []
    assert any("ambiguous" in line for line in w.logs)


def test_force_faint_in_the_overworld_runs_at_the_checkpoint():
    w = World()
    w.step_to(60)
    w.st.checkpoint = False
    w.command(cmd="force_faint", key=B)
    w.step(3)
    assert w.called("faint_slot") == [] and w.q.size(w.q) == 1
    w.st.checkpoint = True
    w.step()
    assert w.called("faint_slot") == [("faint_slot", 1, "force_faint")] and w.battle_writes() == []


def test_force_faint_in_battle_without_a_battle_path_is_deferred_to_the_checkpoint():
    w = World(battle_path=False)
    w.step_to(60)
    w.st.in_battle, w.st.checkpoint = True, False
    w.command(cmd="force_faint", key=B)
    w.step(5)
    assert w.q.size(w.q) == 1 and w.called("faint_slot") == []
    w.st.in_battle, w.st.checkpoint = False, True
    w.step()
    assert w.called("faint_slot") == [("faint_slot", 1, "force_faint")]


def test_force_faint_in_battle_whose_battle_write_returns_nil_is_deferred():
    w = World()
    w.step_to(60)  # writes enabled at the first validation
    w.st.in_battle, w.st.checkpoint, w.st.battle_result = True, False, None
    w.command(cmd="force_faint", key=B)
    w.step()
    assert w.battle_writes() == [("force_faint", B, 1, False)]
    assert w.q.size(w.q) == 1 and w.s.battle_pending_count(w.s) == 0


def test_a_bench_faint_in_battle_lands_the_same_frame():
    w = World()
    w.step_to(60)  # writes enabled at the first validation
    w.st.in_battle, w.st.battle_result = True, "done"
    w.command(cmd="force_explode", key=B)
    w.step()
    assert w.battle_writes() == [("force_explode", B, 1, False)]
    assert w.q.size(w.q) == 0 and w.s.battle_pending_count(w.s) == 0


def test_an_active_battler_is_held_across_frames_and_reported_then_lands_on_switch_out():
    w = World()
    w.step_to(60)  # writes enabled at the first validation
    w.st.in_battle, w.st.battle_result, w.st.checkpoint = True, "hold", False
    w.command(cmd="force_faint", key=A)
    w.step(w.S.PENDING_HUD_FRAMES + 60)
    assert w.s.battle_pending_count(w.s) == 1 and w.q.size(w.q) == 0
    assert len(w.battle_writes()) > w.S.PENDING_HUD_FRAMES  # retried every frame
    held = [ln for ln in w.logs if "held: force_faint" in ln]
    assert len(held) == 1 and "target active" in held[0]              # one console line, no HUD
    assert not [h for h in w.hud if h[0] == "show" and "held" in str(h[1])]
    w.st.battle_result = "done"                             # switched out
    w.step()
    assert w.s.battle_pending_count(w.s) == 0 and w.q.size(w.q) == 0


def test_a_paused_gate_holds_battle_writes_and_never_calls_the_driver():
    w = World()
    w.step_to(60)
    w.s.writes_enabled, w.s.gate_revoked = False, True
    w.st.in_battle, w.st.battle_result = True, "done"
    w.command(cmd="force_faint", key=B)
    w.step(3)
    assert w.battle_writes() == [] and w.s.battle_pending_count(w.s) == 1
    w.s.writes_enabled = True
    w.step()
    assert w.battle_writes() == [("force_faint", B, 1, False)] and w.s.battle_pending_count(w.s) == 0


def test_a_hold_at_battle_end_gets_one_final_attempt_then_falls_back_to_the_checkpoint():
    w = World()
    w.step_to(60)
    w.st.in_battle, w.st.battle_result, w.st.checkpoint = True, "hold", False
    w.command(cmd="force_faint", key=A)
    w.step(3)
    w.st.in_battle = False
    w.step()
    assert w.battle_writes()[-1] == ("force_faint", A, 0, True)
    assert w.s.battle_pending_count(w.s) == 0 and w.q.size(w.q) == 1
    w.st.checkpoint = True
    w.step()
    assert w.called("faint_slot") == [("faint_slot", 0, "force_faint")]


def test_a_final_attempt_that_lands_at_battle_end_is_not_deferred():
    w = World()
    w.step_to(60)  # writes enabled at the first validation
    w.st.in_battle, w.st.battle_result = True, "hold"
    w.command(cmd="force_faint", key=A)
    w.step()
    w.st.in_battle, w.st.battle_result = False, "done"
    w.step()
    assert w.s.battle_pending_count(w.s) == 0 and w.q.size(w.q) == 0


def test_a_held_battle_write_follows_a_key_change_alias():
    w = World()
    w.step_to(60)  # writes enabled at the first validation
    w.st.in_battle, w.st.battle_result = True, "hold"
    w.command(cmd="force_faint", key=A)
    w.step()
    w.set_party((NEW, 0), (B, 1))                           # evolved mid-battle: A -> NEW
    p = w.st.party
    w.identity.begin_alias(w.identity, A, NEW, p[1], p)
    w.step()
    assert w.battle_writes()[-1] == ("force_faint", A, 0, False)  # still resolved via the alias
    w.command(cmd="key_change_ack", old_key=A, new_key=NEW, migrated=True)
    w.step()
    assert w.battle_writes()[-1] == ("force_faint", NEW, 0, False)  # the server now tracks NEW


def test_the_driver_can_drop_its_held_battle_writes():
    w = World()
    w.step_to(60)  # writes enabled at the first validation
    w.st.in_battle, w.st.battle_result = True, "hold"
    w.command(cmd="force_faint", key=A)
    w.step()
    w.s.drop_battle_writes(w.s, "borrowed party restored")
    w.st.in_battle = False
    w.step(2)
    assert w.s.battle_pending_count(w.s) == 0 and w.q.size(w.q) == 0
    assert any("borrowed party restored" in line for line in w.logs)


@pytest.mark.parametrize("name", ["box_mon", "party_mon", "memorialize"])
def test_keyed_storage_commands_are_always_deferred(name):
    w = World()
    w.step()
    w.st.in_battle, w.st.checkpoint = True, False
    w.command(cmd=name, key=A)
    w.step()
    assert w.q.size(w.q) == 1 and w.battle_writes() == []


def test_a_gate_hold_is_reported_with_reason_and_age_in_the_tick_hud_line():
    w = World()
    w.step()
    w.st.checkpoint = False
    w.command(cmd="memorialize", key=A)
    w.step(w.S.PENDING_HUD_FRAMES + 30)
    held = [ln for ln in w.logs if "held: memorialize" in ln]
    assert len(held) == 1 and "not at the overworld checkpoint" in held[0]
    assert not [h for h in w.hud if h[0] == "show" and "held" in str(h[1])]


def test_a_held_memorial_never_reaches_the_hud():
    # owner 2026-09-25: "SLink held: memorialize x1 58s (forbidden state: callback1)" on the HUD in
    # battle -- the player needs no pending counter (Gen 1 shows holds nowhere); console only
    w = World()
    w.step()
    w.st.checkpoint = False
    w.st.gate_why = "forbidden state: callback1"
    w.command(cmd="memorialize", key=A)
    w.step(w.S.PENDING_HUD_FRAMES * 3)
    assert not [h for h in w.hud if h[0] == "show" and "held" in str(h[1])], w.hud
    assert len([ln for ln in w.logs if "held: memorialize" in ln]) == 1


# -- signals, safe, hooks, identity observation -------------------------------------------

def test_signals_are_drained_under_a_per_signal_pcall_and_a_failure_banners_once():
    w = World()
    L = w.lua
    w.signal_queue = [L.table(kind="boom"), L.table(kind="capture")]
    w.signals.handler_error = "hook threw"
    w.step()
    assert w.called("signal") == [("signal", "boom"), ("signal", "capture")]
    assert any("reducer blew up" in line for line in w.logs) and any("hook threw" in line for line in w.logs)
    assert w.signals.handler_error is None
    w.signals.failure = "site mismatch"
    w.step(3)
    assert len([h for h in w.hud if h[0] == "show" and h[1] == "SLINK STOPPED - SEE LOG"]) == 1


@pytest.mark.parametrize("entrypoint", ["frame_end", "eligible", "validate"])
def test_terminal_signal_failure_holds_commands_before_services(entrypoint):
    w = World()
    w.step_to(60)
    w.st.checkpoint, w.st.in_battle, w.st.battle_result = False, True, "hold"
    w.command(cmd="force_faint", key=A)
    w.command(cmd="box_mon", key=B)
    w.step()
    assert w.s.battle_pending_count(w.s) == 1 and w.q.size(w.q) == 1
    w.driver.pre_pump = lambda: w.timeline.append(("native_service", w.s.eligible(w.s)))
    w.driver.after_receive = lambda: w.timeline.append(("after_receive",))
    w.st.checkpoint, w.st.battle_result = True, "done"
    w.timeline.clear()
    w.signals.failure = "site mismatch"

    w.s[entrypoint](w.s)
    w.step()

    assert w.s.writes_enabled is False and w.s.eligible(w.s) is False
    assert w.called("native_service") and all(not call[1] for call in w.called("native_service"))
    assert not w.called("deposit") and not w.battle_writes()
    assert w.s.battle_pending_count(w.s) == 1 and w.q.size(w.q) == 1
    w.signals.failure = None
    w.s.validate(w.s)
    w.s.run_deferred(w.s)
    w.step_to(120)
    assert w.s.writes_enabled is False and w.s.eligible(w.s) is False
    assert all(not call[1] for call in w.called("native_service")) and not w.battle_writes()
    assert not w.called("deposit") and w.q.size(w.q) == 1
    assert len([line for line in w.logs if "ENGINE SIGNALS STOPPED" in line]) == 1


def test_signal_failure_during_drain_revokes_remaining_frame_writes():
    w = World()
    w.step_to(60)
    w.st.checkpoint = False
    w.command(cmd="box_mon", key=A)
    w.step()
    w.st.checkpoint = True
    w.timeline.clear()

    def drain(_):
        w.signals.failure = "signal buffer overflow"
        return w.lua.table()

    w.signals.drain = drain
    w.driver.after_receive = lambda: w.timeline.append(("after_receive", w.s.eligible(w.s)))
    w.step()
    assert w.s.writes_enabled is False
    assert w.called("after_receive") == [("after_receive", False)]
    assert not w.called("deposit")
    assert w.q.size(w.q) == 1


def test_safe_is_sent_once_after_a_battle_once_out_of_battle():
    w = World()
    w.step()
    w.st.in_battle = True
    w.s.pending_safe = True
    w.step(3)
    assert w.events("safe") == []
    w.st.in_battle = False
    w.step(3)
    assert len(w.events("safe")) == 1


def test_frame_hooks_run_and_aliases_are_observed_every_readable_frame():
    w = World()
    w.step()
    p = w.st.party
    w.identity.begin_alias(w.identity, OLD, A, p[1], p)
    w.set_party((A, 0, (9, 9)), (B, 1))                     # the record was edited
    w.step()
    assert len(w.called("hook")) == 2
    w.command(cmd="key_change_rejected", old_key=OLD, new_key=A, reason="collision")
    w.set_party((A, 0), (B, 1))
    w.step()
    r = w.identity.find_party_slot(w.identity, OLD, w.st.party)
    assert r[0] is None and "left the party" in r[3]


def test_stop_closes_the_signals():
    w = World()
    w.s.stop(w.s)
    assert ("close",) in w.timeline


# -- C4-2d review findings ------------------------------------------------------------------

def _emitter(w, event, key=None):
    """A frame hook that emits `event` whenever the test sets w.st.emit (a reducer stand-in)."""
    slot = len(w.driver.frame_hooks) + 1
    w.driver.frame_hooks[slot] = w.lua.eval(
        "function(s, st, name, key) local done = false return function() if st.emit and not done then "
        "done = true; s.send(name, key and {key = key} or {}) end end end"
    )(w.s, w.st, event, key)


def test_major1_no_semantic_event_precedes_hello_it_is_held_and_flushed_after():
    w = World()
    w.st.hello_ready = False
    _emitter(w, "whiteout")
    w.step()
    w.st.emit = True
    w.step(3)
    assert w.sent == []
    w.st.hello_ready = True
    w.step()
    assert [m["event"] for m in w.sent] == ["hello", "whiteout"]
    assert [m["seq"] for m in w.sent] == [1, 2]


def test_major1_held_pre_hello_events_die_with_the_connection():
    w = World()
    w.st.hello_ready = False
    _emitter(w, "whiteout")
    w.st.emit = True
    w.step()
    w.connected = False
    w.step()
    w.connected, w.st.hello_ready = True, True
    w.step()
    assert [m["event"] for m in w.sent] == ["hello"]


def test_major3_a_new_force_faint_during_a_borrowed_party_is_held_then_lands_after_restore():
    w = World()
    w.step_to(60)
    w.st.in_battle, w.st.battle_result = True, "done"
    w.set_party((C, 0))                                   # a partner's party in RAM
    w.st.borrowed = True
    w.command(cmd="force_faint", key=A)
    w.step(5)
    assert w.s.battle_pending_count(w.s) == 1 and w.battle_writes() == []
    w.set_party((A, 0), (B, 1))                           # restored
    w.st.borrowed = False
    w.step()
    assert w.battle_writes() == [("force_faint", A, 0, False)] and w.s.battle_pending_count(w.s) == 0


def test_major3_without_a_borrowed_party_an_absent_key_is_still_dropped():
    w = World()
    w.step_to(60)
    w.st.in_battle = True
    w.command(cmd="force_faint", key=C)
    w.step()
    assert w.s.battle_pending_count(w.s) == 0 and w.q.size(w.q) == 0


def test_major6_key_change_ack_migrates_queued_checkpoint_commands():
    w = World()
    w.set_party((NEW, 0), (B, 1))
    w.step_to(60)
    w.st.checkpoint = False
    p = w.st.party
    w.identity.begin_alias(w.identity, OLD, NEW, p[1], p)       # the key_change we raised
    w.command(cmd="memorialize", key=OLD)
    w.command(cmd="force_faint", key=B)
    w.step()
    w.command(cmd="key_change_ack", old_key=OLD, new_key=NEW, migrated=True)
    w.step()
    items = w.q["items"]
    assert [str(items[i].key) for i in (1, 2)] == [NEW, B]


def test_r3_an_unrelated_migrated_false_ack_without_evidence_renames_nothing():
    """migrated:false for an old key the server never tracked (state.py:2566-2571): the
    cartridge still holds OLD and no alias was raised, so nothing local follows."""
    w = World()
    w.set_party((OLD, 0), (B, 1))
    w.step_to(60)
    w.st.checkpoint = False
    w.command(cmd="memorialize", key=OLD)
    w.step()
    w.command(cmd="key_change_ack", old_key=OLD, new_key=NEW, migrated=False)
    w.step()
    assert str(w.q["items"][1].key) == OLD


def test_r3_a_replayed_ack_with_our_alias_migrates_the_queued_command():
    """The replay ACK (migration already happened server-side, state.py:2557-2565) arrives
    with migrated:false; our own pending alias for exactly this pair is the evidence."""
    w = World()
    w.set_party((NEW, 0), (B, 1))
    w.step_to(60)
    w.st.checkpoint = False
    p = w.st.party
    w.identity.begin_alias(w.identity, OLD, NEW, p[1], p)
    w.command(cmd="force_faint", key=OLD)
    w.step()
    w.q.push(w.q, w.lua.table_from({"cmd": "memorialize", "key": OLD}))
    w.command(cmd="key_change_ack", old_key=OLD, new_key=NEW, migrated=False)
    w.step()
    # KC-RETRY MAJOR-1: the force_faint for OLD resolves through the pending alias and is queued
    # (it used to be dropped as "key not in party"); the ack migrates both queued commands
    assert [str(w.q["items"][i].key) for i in range(1, w.q.size(w.q) + 1)] == [NEW, NEW]


def test_r3_codex1_no_alias_an_unrelated_new_key_in_the_party_is_never_evidence():
    """C4-2g (Codex REV3 case 1): no pending alias; the party holds an UNRELATED record carrying
    NEW; memorialize(OLD) is queued; a migrated:false ACK OLD->NEW arrives. Key membership is not
    continuity: the command stays on OLD."""
    w = World()
    w.set_party((NEW, 0), (B, 1))
    w.step_to(60)
    w.st.checkpoint = False
    w.q.push(w.q, w.lua.table_from({"cmd": "memorialize", "key": OLD}))
    w.command(cmd="key_change_ack", old_key=OLD, new_key=NEW, migrated=False)
    w.step()
    assert str(w.q["items"][1].key) == OLD
    assert any("no valid alias" in line and "none" in line for line in w.logs)


def test_r3_codex2_a_lost_alias_keeps_its_latch_through_a_replay_ack():
    """C4-2g (Codex REV3 case 2): OLD->NEW is raised, the record departs (alias.lost latches),
    a REPLACEMENT now carries NEW; a replay ACK must not hand the command to the replacement."""
    w = World()
    w.set_party((NEW, 0), (B, 1))
    w.step_to(60)
    w.st.checkpoint = False
    p = w.st.party
    w.identity.begin_alias(w.identity, OLD, NEW, p[1], p)
    w.identity.departure(w.identity, NEW)                         # the aliased record left
    w.set_party((NEW, 0), (B, 1))                                 # a replacement carries NEW
    w.q.push(w.q, w.lua.table_from({"cmd": "memorialize", "key": OLD}))
    w.command(cmd="key_change_ack", old_key=OLD, new_key=NEW, migrated=False)
    w.step()
    assert str(w.q["items"][1].key) == OLD
    assert any("no valid alias" in line and "lost" in line for line in w.logs)


def test_r3_an_ambiguous_alias_keeps_its_latch_through_an_ack():
    w = World()
    w.set_party((NEW, 0), (NEW, 1))                               # a twin at the change
    w.step_to(60)
    w.st.checkpoint = False
    p = w.st.party
    w.identity.begin_alias(w.identity, OLD, NEW, p[1], p)
    w.set_party((NEW, 0), (B, 1))
    w.q.push(w.q, w.lua.table_from({"cmd": "memorialize", "key": OLD}))
    w.command(cmd="key_change_ack", old_key=OLD, new_key=NEW, migrated=True)
    w.step()
    assert str(w.q["items"][1].key) == OLD


def test_r3_an_ack_for_a_different_pair_does_not_use_the_alias():
    w = World()
    w.set_party((NEW, 0), (B, 1))
    w.step_to(60)
    w.st.checkpoint = False
    p = w.st.party
    w.identity.begin_alias(w.identity, OLD, NEW, p[1], p)
    w.q.push(w.q, w.lua.table_from({"cmd": "memorialize", "key": OLD}))
    w.command(cmd="key_change_ack", old_key=OLD, new_key=C, migrated=True)   # not our pair
    w.step()
    assert str(w.q["items"][1].key) == OLD
    # REV5: the wrong-pair ACK must not consume the valid alias; the exact pair still migrates
    assert w.identity.pending is not None and str(w.identity.pending.new_key) == NEW
    w.command(cmd="key_change_ack", old_key=OLD, new_key=NEW, migrated=False)
    w.step()
    assert str(w.q["items"][1].key) == NEW


def test_r3_an_ack_on_an_unreadable_party_waits_to_be_observed():
    """REV5 caveat: the mapping is decided only on an OBSERVED party. With the party unreadable
    the ACK is held (nothing migrated, alias kept); once readable it is observed, then applied."""
    w = World()
    w.set_party((NEW, 0), (B, 1))
    w.step_to(60)
    w.st.checkpoint = False
    p = w.st.party
    w.identity.begin_alias(w.identity, OLD, NEW, p[1], p)
    w.q.push(w.q, w.lua.table_from({"cmd": "memorialize", "key": OLD}))
    w.st.party_ok = False
    w.command(cmd="key_change_ack", old_key=OLD, new_key=NEW, migrated=False)
    w.step(3)
    assert str(w.q["items"][1].key) == OLD and w.identity.pending is not None
    w.st.party_ok = True
    w.step()
    assert str(w.q["items"][1].key) == NEW and w.identity.pending is None


def test_r3_a_held_ack_still_honours_a_departure_seen_while_waiting():
    w = World()
    w.set_party((NEW, 0), (B, 1))
    w.step_to(60)
    w.st.checkpoint = False
    p = w.st.party
    w.identity.begin_alias(w.identity, OLD, NEW, p[1], p)
    w.q.push(w.q, w.lua.table_from({"cmd": "memorialize", "key": OLD}))
    w.st.party_ok = False
    w.command(cmd="key_change_ack", old_key=OLD, new_key=NEW, migrated=False)
    w.step()
    w.set_party((B, 1))                                           # the record left meanwhile
    w.st.party_ok = True
    w.step()
    assert str(w.q["items"][1].key) == OLD


def test_r2_held_pre_hello_events_die_with_a_save_reset():
    """A keyed event and a whiteout held for a hello that never went out belong to the save
    that was cleared; the next save's hello is not followed by them."""
    w = World()
    w.st.hello_ready = False
    _emitter(w, "faint", key=A)
    _emitter(w, "whiteout")
    w.st.emit = True
    w.step()
    w.st.emit = False
    w.st.party_ok, w.st.cleared = False, True
    w.step_to(60)                                         # the validation sees the cleared save
    w.st.party_ok, w.st.cleared, w.st.hello_ready = True, False, True
    w.step()
    assert [m["event"] for m in w.sent] == ["hello"]


def test_r2_held_events_of_a_save_that_was_not_reset_still_follow_its_hello():
    w = World()
    w.st.hello_ready = False
    _emitter(w, "faint", key=A)
    w.st.emit = True
    w.step()
    w.st.emit = False
    w.st.hello_ready = True
    w.step()
    assert [m["event"] for m in w.sent] == ["hello", "faint"]


def test_blocker_validation_runs_before_the_driver_service_so_a_pause_gates_that_frame():
    w = World()
    seen = []
    w.driver.pre_pump = w.lua.eval("function(s, rec) return function() rec(s.frame, s:eligible()) end end")(
        w.s, lambda f, e: seen.append((f, e)))
    w.step_to(60)
    w.st.party_ok = False
    w.step_to(60 + 60 * w.S.MAX_INVALID)                  # the pausing validation's frame
    assert seen[-1] == (60 + 60 * w.S.MAX_INVALID, False)
    assert seen[59] == (60, True)                         # enabled by frame 60's own validation


def test_send_accepts_the_method_form_too():
    w = World()
    w.step()
    w.lua.eval("function(s) s:send('whiteout', {}) end")(w.s)
    w.s.send("whiteout", w.lua.table())
    assert [m["event"] for m in w.sent] == ["hello", "whiteout", "whiteout"]


def test_eligible_needs_writes_hello_and_a_connection():
    w = World()
    w.step()
    assert w.s.eligible(w.s) is False                     # writes not enabled yet
    w.step_to(60)
    assert w.s.eligible(w.s) is True
    w.connected = False
    assert w.s.eligible(w.s) is False


# -- static contract ---------------------------------------------------------------------

BIZHAWK = re.compile(r"\b(memory|mainmemory|event|gui|console|emu|client|joypad|comm|gameinfo|bizstring|forms)\s*\.")
WRITES = re.compile(r"\bwrite_(u8|u16|u32|bytes|range)\b|\bwritebyte\b")


@pytest.mark.parametrize("name", ["session.lua", "identity.lua", "deferred.lua"])
def test_core_names_no_bizhawk_global_and_writes_no_memory(name):
    code = "\n".join(line.split("--", 1)[0] for line in (CORE / name).read_text(encoding="utf-8").splitlines())
    assert not BIZHAWK.search(code), BIZHAWK.search(code)
    assert not WRITES.search(code), WRITES.search(code)


def test_a_battle_held_force_faint_keeps_its_place_ahead_of_the_later_memorialize():
    """Live deadzone_gen3 r3 (123c6c45), B: the capture's force_faint arrived IN BATTLE (held),
    its memorialize arrived next (straight to the deferred queue); at battle end the held
    force_faint was appended BEHIND it, the memorialize moved the record out, and the
    force_faint was dropped ("key not in party"). Server order is force_faint -> memorialize
    (state.py _propagate_faint); Gen 1's deadzone_new receipt is FAINTED then RETIRED."""
    w = World()
    w.step_to(60)
    w.st.in_battle, w.st.battle_result, w.st.checkpoint = True, "hold", False
    w.command(cmd="force_faint", key=B)
    w.command(cmd="memorialize", key=B)
    w.step(3)
    w.st.in_battle = False                 # the battle ends; the final attempt declines (nil)
    w.st.battle_result = None
    w.step()
    order = [str(q.cmd) for q in w.q["items"].values()]
    assert order == ["force_faint", "memorialize"], order
    w.st.checkpoint = True
    w.step()
    assert w.called("faint_slot") == [("faint_slot", 1, "force_faint")]
