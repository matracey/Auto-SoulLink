"""C4b: real client/binder and assembled service; native UI remains the Rig's MODEL traps.

The late install below is the entry.lua integration seam (its owner must add it
immediately after parts.dev_polished_trade = trade). Production hello is unchanged.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from server.adapters.gen2_polished import Gen2PolishedAdapter
from server.state import LinkEntry, LinkStatus, MonInfo, SoulLinkState
from tests.unit import (
    test_polished_trade_responder as resp,
    test_polished_trade_service as svc,
    test_polished_write_path as wp,
)
from tests.unit.test_polished_client import _entry, _pair

ROOT = Path(wp.ROOT)
CLIENT = ROOT / "lua/gen2/client.lua"
env = svc.env


class Pump(wp.Rig):
    def __init__(self, *, enabled=True, source=None, player="a", legacy_observers=False):
        self.lua = wp.lupa.LuaRuntime(unpack_returned_tuples=True)
        self.mons = wp.party()
        if player == "b":
            for mon in self.mons:
                mon["ot_id"] += 1
        self.mem = self.lua.table_from(wp.sysbus(self.mons))
        self.img = wp.seal_save(wp.Image())
        self.deps, self.io, self.log = self.lua.execute(wp.HARNESS.replace("ROOTDIR", json.dumps(wp.ROOT)))(
            wp.overlay()[1], self.mem, self.img)
        self.deps.player = player
        self.deps.polished_trade_dev = False  # explicit isolated manual-binder fixture, not shipped auto-composition
        if legacy_observers:  # historical client predates the default-on Polished faint interfaces
            self.deps.polished_faint_observer = False
            self.deps.polished_active_faint = False
        if source is not None:
            self.lua.globals().pump_source = source
            self.lua.execute(f'''local original=dofile
                dofile=function(path)
                    if path == {json.dumps(str(CLIENT).replace(chr(92), '/'))} then
                        return assert(load(pump_source, '@'..path))()
                    end
                    return original(path)
                end''')
        self.parts, why = _pair(_entry(self.lua).build(self.deps))
        assert why is None, why
        self.client = self.parts.client
        self.machine = None
        self.connected = True
        self.inbox = []
        self.arm_trace = []
        self.cancel_trace = []
        g = self.lua.globals()
        g.pump_read = lambda a: self.machine.peek(a)[0] if self.machine else (self.mem[a] or 0)
        g.pump_write = self._write
        g.pump_connected = lambda: self.connected
        g.pump_receive = lambda: self.inbox.pop(0) if self.inbox else None
        self.lua.execute('''return function(io, net)
            local read=io.read_u8
            local write=io.write_u8
            io.read_u8=function(a,d) if d == nil or d == 'System Bus' then return pump_read(a) end return read(a,d) end
            -- System Bus only, like the read side: the overworld gate looks io.write_u8 up per write (box observer),
            -- so a blanket override would strand CartRAM/WRAM deposits in mem while read-backs read the image.
            io.write_u8=function(a,v,d) if d == nil or d == 'System Bus' then return pump_write(a,v,d) end return write(a,v,d) end
            net.connected=function() return pump_connected() end
            net.receive=function() return pump_receive() end
        end''')(self.io, self.deps.net)
        pt = self.lua.eval(f'dofile("{wp.ROOT}/lua/gen2/polished_trade.lua")')
        spec = self.lua.table_from({"profile": self.parts.profile, "io": self.io, "dev": True})
        spec.read_rom = self.lua.eval('''function(bank,addr,n)
            return PUMP_IO.read_range(bank*0x4000+addr-0x4000,n,'ROM') end''')
        g.PUMP_IO = self.io
        self.binder, why = _pair(pt.compose(spec))
        assert why is None, why
        g.pump_cancel = lambda why: self.cancel_trace.append(why)
        g.pump_arm = lambda cmd, frame: self.arm_trace.append((cmd, frame))
        self.lua.execute('''return function(b,io)
            local cancel=b.cancel
            b.cancel=function(self,why) pump_cancel(why); return cancel(self,why) end
            local arm=b.arm
            b.arm=function(self,cmd,...) pump_arm(cmd,io.framecount()); return arm(self,cmd,...) end
        end''')(self.binder, self.io)
        if enabled:
            self.client.install_dev_trade_pump(self.client, self.lua.table_from(
                {"binder": self.binder, "charmap": self.parts.data.charmap}))
        self.client.start(self.client)
        self.frame(12)
        assert len(self.sent("hello")) == 1, list(self.log.lines.values())

    def _write(self, a, value, domain):
        self.log.writes[len(self.log.writes) + 1] = self.lua.table_from(
            {"addr": a, "value": value, "domain": domain, "frame": self.io.frame})
        if getattr(self, "fail_once_address", None) == a:
            self.fail_once_address = None
            raise RuntimeError("one-shot arm fault")
        if getattr(self, "fail_write", False):
            raise RuntimeError("injected write fault")
        if self.machine:
            self.machine.poke(a, value)
        else:
            self.mem[a] = value
        if (getattr(self, "fail_after_once_address", None) == a
                and value == self.fail_after_once_value):
            self.fail_after_once_address = None
            raise RuntimeError("one-shot fault AFTER publication store")

    def command(self, *commands, split=False):
        if split:
            self.inbox.extend(json.dumps({"commands": [cmd]}) for cmd in commands)
        else:
            self.inbox.append(json.dumps({"commands": commands}))

    def bind(self, machine):
        self.machine = machine
        for address, value in wp.sysbus(self.mons).items():
            if address != svc.HROMBANK:
                machine.poke(address, value)

    def put(self, offset, value):
        address = self.parts.profile.overlay.trade.lease.base + offset
        if self.machine:
            self.machine.poke(address, value)
        else:
            self.mem[address] = value

    def image(self, command, gen=42, ack=41, slot=2):
        family = self.parts.profile.overlay.trade
        for i in range(16):
            self.put(i, 0)
        for i, byte in enumerate(family.lease.magic.values()):
            self.put(i, byte)
        for offset, value in {4: 1, 5: family.commands[command], 6: gen, 7: ack, 9: slot}.items():
            self.put(offset, value)

    def offered(self):
        self.image("QUERY")
        self.frame()
        self.command({"cmd": "trade_mask", "mask": 4})
        self.frame()
        # ROM carries the query answer's token into the OFFER.
        self.put(5, 2)
        self.put(6, 43)
        self.put(7, 42)
        self.frame()
        assert self.sent("trade_offer")[-1]["slot"] == 2

    def apply_command(self, token="test"):
        return {"cmd": "apply_trade", "token": token, "slot": 2,
                "old_key": self.sent("hello")[0]["party"][2]["key"],
                "blob_hex": self.sent("hello")[0]["party"][1]["blob_hex"], "partner_name": "KRIS"}

    def accepted(self, *commands):
        self.offered()
        self.command({"cmd": "trade_offer_ack", "ok": True, "token": "test"}, *commands)
        self.frame()


class Proposer(svc.Rig):
    def __init__(self, env, pump, **kwargs):
        super().__init__(env, party=3, **kwargs)
        self.pump = pump

    def build_ram(self, machine):
        super().build_ram(machine)
        self.pump.bind(machine)


class Responder(resp.Rig):
    def __init__(self, env, pump, prompt, **kwargs):
        super().__init__(env, party=3, **kwargs)
        self.pump, self.prompt = pump, prompt

    def build_ram(self, machine):
        super().build_ram(machine)
        self.pump.bind(machine)
        machine.poke(self.lease, bytes(16))
        machine.poke(self.lease + 6, self.gen0)
        machine.poke(self.lease + 7, self.gen0)
        self.pump.command(self.prompt)
        self.pump.frame()


def state_for(tmp_path, a, b):
    state = SoulLinkState(data_dir=str(tmp_path))
    state.adapter = Gen2PolishedAdapter(artifact_kind="overlay")
    for player, pump in (("a", a), ("b", b)):
        state.handle_event(player, pump.sent("hello")[0])
    aa, bb = a.sent("hello")[0]["party"][2], b.sent("hello")[0]["party"][2]
    state.links.append(LinkEntry(area_id="route_29", a=MonInfo(key=aa["key"], species=aa["species_id"]),
                                 b=MonInfo(key=bb["key"], species=bb["species_id"]), status=LinkStatus.ALIVE))
    state._index_entry(state.links[-1])
    return state


@pytest.mark.parametrize("split", [False, True], ids=["batch", "lines"])
def test_batched_ack_apply_is_parked_not_dropped(split):
    p = Pump()
    p.offered()
    p.command({"cmd": "trade_offer_ack", "ok": True, "token": "test"}, p.apply_command(), split=split)
    p.frame()
    answered = p.io.frame
    assert p.arm_trace == []
    p.frame()
    assert p.arm_trace == [(5, answered + 1)]
    assert p.sent("trade_done") == []


@pytest.mark.parametrize("yes,prepare,wrap", [(True, False, False), (True, True, False), (False, False, False),
                                           (True, False, True)], ids=["legacy", "prepare", "no", "wrap"])
def test_real_services_real_server_keep_original_link(env, tmp_path, yes, prepare, wrap):
    a, b = Pump(), Pump(player="b")
    state = state_for(tmp_path, a, b)
    state.trade_prepare = {"a": prepare, "b": prepare}  # inject legacy barrier policy, never change the hello
    original = [(link.a.key, link.b.key) for link in state.links]
    cursor = {"a": len(a.sent()), "b": len(b.sent())}

    def route(player, pump):
        messages = pump.sent()
        for msg in messages[cursor[player]:]:
            cmds = state.handle_event(player, msg)
            if cmds:
                pump.command(*cmds)
        cursor[player] = len(messages)
        # Commands are queued for the other side until its next event (as on TCP).

    responder_run = []
    def responder_host(h):
        for _ in range(4000):
            b.frame()
            route("b", b)
            # A's server messages may advance prepare while its native CPU is held.
            if state.queued_commands["a"]:
                a.command(*state.handle_event("a", {"event": "tick"}))
            a.frame()
            route("a", a)
            yield

    def proposer_host(h):
        prompt, prompt_frame = None, None
        for _ in range(4300):
            a.frame()
            route("a", a)
            if prompt and not responder_run and a.io.frame > prompt_frame + 1:
                r = Responder(env, b, prompt, gen0=0xFE if wrap else 0x10)
                result = r.run(responder_host, yesno=yes)
                responder_run.append((r, result))
                b.frame(2)
                route("b", b)
            prompts = state.handle_event("b", {"event": "tick"})
            new_prompt = next((cmd for cmd in prompts if cmd["cmd"] == "show_menu"), None)
            if new_prompt:
                prompt, prompt_frame = new_prompt, a.io.frame
            yield

    rig = Proposer(env, a, gen0=0xFD if wrap else 0x10)
    run = rig.run(proposer_host)
    a.frame(3)
    route("a", a)
    assert responder_run, list(a.log.lines.values())
    rr, rb = responder_run[0]
    svc.common(rig, run, "QOADC" if yes else "QOC")
    resp.common(rr, rb, "ADADC" if yes else "ADC", results=[0, 1] if yes else [1])
    assert [(link.a.key, link.b.key) for link in state.links] == original
    assert state.pending_trade is None
    if yes:
        release = next(w["frame"] for w in b.writes() if w["addr"] == env.lease + 5 and w["value"] == 8)
        apply_frame = next(frame for cmd, frame in b.arm_trace if cmd == 5)
        assert apply_frame > release
        assert len(a.sent("trade_done")) == len(b.sent("trade_done")) == 1
        # Native writes never touch save/party; host staging stays in OT slot zero.
        for pump in (a, b):
            spans = pump.parts.profile.overlay.trade.staging
            allowed = [(env.lease, 16), *[(s.addr, s.size) for s in spans.values()]]
            assert all(any(lo <= w["addr"] < lo + n for lo, n in allowed) for w in pump.writes())
    for pump in (a, b):
        assert pump.sent("hello")[0]["trade_prepare"] is False
        assert all(m.get("new_key") == pump.apply_command()["old_key"] and m.get("new_species") == 0
                   for m in pump.sent("trade_done"))
        assert not pump.sent("capture") and not pump.sent("key_change")


@pytest.mark.parametrize("field,value", [("token", "foreign"), ("slot", 1), ("old_key", "bad"),
                                        ("blob_hex", "00"), ("blob_hex", "gg" * 70)],
                         ids=["token", "slot", "key", "short", "hex"])
def test_invalid_apply_never_arms(field, value):
    p = Pump()
    cmd = p.apply_command()
    cmd[field] = value
    p.accepted(cmd)
    p.frame(3)
    assert p.arm_trace == []
    if field == "token":
        assert not p.sent("trade_done")
        p.command(p.apply_command())
        p.frame(2)
        assert len(p.arm_trace) == 1
    else:
        reports = p.sent("menu_result")
        assert len(reports) == 1 and reports[0]["choice"] == 0 and reports[0]["withdraw"] is True
        assert not p.sent("trade_done")


def test_preapply_poison_is_one_cancellation_and_cannot_resurrect():
    p = Pump()
    p.offered()
    p.fail_write = True
    p.command({"cmd": "trade_offer_ack", "ok": True, "token": "test"}, p.apply_command())
    p.frame(3)
    p.fail_write = False
    before = p.writes()
    p.command(p.apply_command(), {"cmd": "trade_offer_ack", "ok": True, "token": "test"})
    p.frame(4)
    assert p.writes() == before and p.arm_trace == []
    cancel = p.sent("menu_result")
    assert len(cancel) == 1 and cancel[0]["choice"] == 0 and cancel[0]["withdraw"] is True
    assert not p.sent("trade_done")
    assert len([x for x in p.log.lines.values() if "dev trade " in x]) == 1


@pytest.mark.parametrize("result", [3, 1, 2, 255], ids=["invalid-three", "none", "two", "invalid"])
def test_apply_done_is_never_consent_or_success(result):
    p = Pump()
    p.accepted(p.apply_command())
    p.frame()
    gen = p.mem[p.parts.profile.overlay.trade.lease.base + 6]
    p.put(5, 7)
    p.put(7, gen)
    p.put(8, result)
    before = p.writes()
    p.frame(3)
    done = p.sent("trade_done")
    assert len(done) == 1
    assert not p.sent("menu_result")
    if result == 1:
        assert done[0]["new_key"] == p.apply_command()["old_key"] and done[0]["new_species"] == 0
        assert p.mem[p.parts.profile.overlay.trade.lease.base + 5] == 8
    else:
        assert done[0]["uncertain"] is True and "new_key" not in done[0]
        assert p.writes() == before


def test_apply_done_result_two_is_uncertain_after_reset_with_the_vanilla_hud_line():
    # MAJOR-5 parity: a native result 2 holds in .Hold until a reset (trade_commit.asm), so the
    # server must take evidence only from the post-reset hello, and the player sees vanilla's line.
    p = Pump()
    shown = []
    p.lua.execute("return function(hud, rec) hud.show = function(text) rec(text) end end")(p.deps.hud, shown.append)
    p.accepted(p.apply_command())
    p.frame()
    assert p.arm_trace[-1][0] == 5  # APPLY was attempted
    gen = p.mem[p.parts.profile.overlay.trade.lease.base + 6]
    p.put(5, 7)
    p.put(7, gen)
    p.put(8, 2)
    p.frame(3)
    done = p.sent("trade_done")
    assert len(done) == 1 and done[0]["uncertain"] is True and done[0].get("after_reset") is True
    assert shown.count("TRADE UNCERTAIN - CHECK PARTY") == 1


@pytest.mark.parametrize("offset", [0, 4, 5, 6, 7, 9, 12, 8],
                         ids=["magic", "version", "command", "generation", "ack", "slot", "token4", "result"])
def test_prepare_revalidates_live_lease_before_readiness(offset):
    p = Pump()
    p.accepted()
    at = p.parts.profile.overlay.trade.lease.base + offset
    p.put(offset, ((p.mem[at] or 0) + 1) % 256)
    cmd = p.apply_command()
    cmd["cmd"] = "apply_prepare"
    p.command(cmd)
    p.frame(3)
    assert p.arm_trace == []
    ready = p.sent("apply_ready")
    assert len(ready) == 1 and ready[0]["ok"] is False


@pytest.mark.parametrize("action", ["disconnect", "rewind", "withdraw", "identity", "timeout"],
                         ids=["offline", "rewind", "withdraw", "identity", "timeout"])
def test_parked_visit_is_retired_without_staging(action):
    p = Pump()
    p.accepted(p.apply_command())
    if action == "disconnect":
        p.connected = False
    elif action == "rewind":
        p.io.frame -= 3
    elif action == "withdraw":
        # Withdrawal in the same reply drain, before the next pump.
        p.client.handle_command(p.client, p.lua.table_from({"cmd": "withdraw_trade", "token": "test"}))
    elif action == "identity":
        p.mem[wp.SYM["wPlayerID"][1]] += 1
    else:
        p.io.frame += 3601
        p.client.last_frame = p.io.frame  # skipped mapped frames, NOT a rewind
    p.frame(2)
    p.connected = True
    p.command(p.apply_command())
    p.frame(12)
    assert not p.arm_trace
    assert not any(m.get("uncertain") for m in p.sent("trade_done"))


@pytest.mark.parametrize("conflict", [False, True], ids=["duplicate", "conflict"])
def test_duplicate_apply_cannot_replace_parked_payload(conflict):
    p = Pump()
    cmd = p.apply_command()
    second = dict(cmd)
    if conflict:
        second["partner_name"] = "LYRA"
    p.accepted(cmd, second)
    p.frame(3)
    assert len(p.arm_trace) == (0 if conflict else 1)
    p.command(cmd)
    p.frame(3)
    assert len(p.arm_trace) == (0 if conflict else 1)


def test_box_writes_and_trade_echoes_wait_for_proved_close():
    p = Pump()
    assert p.arm_writes()
    p.accepted()
    key = p.apply_command()["old_key"]
    before = p.count()
    p.command({"cmd": "box_mon", "key": key})
    p.frame(3)
    assert p.count() == before
    for kind in ("capture", "key_change", "party_to_box", "box_to_party"):
        p.client.on_event(p.client, p.lua.table_from({"kind": kind}))
        assert p.sent(kind) == []
    p.put(5, 0)
    p.frame(4)
    assert p.count() == before - 1


def test_offline_query_refuses_without_sending():
    p = Pump()
    p.connected = False
    p.image("QUERY")
    p.frame()
    base = p.parts.profile.overlay.trade.lease.base
    assert p.mem[base + 7] == 42 and p.mem[base + 11] == 0
    assert p.sent("trade_query") == []


def test_default_off_matches_pre_pump_client_byte_for_byte():
    import subprocess

    baseline = subprocess.check_output(
        ["git", "-C", str(ROOT), "show", "48172aa6cd6100e3463b8fa2966c68c6e5d3940b:lua/gen2/client.lua"],
        text=True, encoding="utf-8")
    old, new = Pump(enabled=False, source=baseline, legacy_observers=True), Pump(enabled=False, legacy_observers=True)
    traces = []
    for p in (old, new):
        p.command({"cmd": "trade_offer_ack", "ok": True, "token": "test"}, p.apply_command(),
                  {"cmd": "config", "native_sounds": False})
        p.frame(62)
        traces.append((p.sent(), p.writes(), list(p.log.lines.values()),
                       p.lua.eval("function(c) return #c.deferred,#c.held end")(p.client)))
    assert traces[0] == traces[1]


@pytest.mark.parametrize("before,after,case", [
    ("io.framecount() > v.gap", "io.framecount() >= v.gap", "gap"),
    ("cmd.token ~= v.server_token then return true", "false then return true", "token"),
    ("and cmd.slot == v.slot", "and true", "slot"),
    ("if v.fingerprint ~= fingerprint then", "if false then", "duplicate"),
    ("if uncertain and v.attempted then", "if false then", "uncertain"),
    ("if dev_trade and dev_trade.busy() then return end", "if false then return end", "box"),
    ('if not net.connected() and not v.terminal then D.forget("disconnected") end', '', "offline"),
], ids=["gap", "token", "slot", "duplicate", "uncertain", "box", "offline"])
def test_client_guard_mutants_are_killed(before, after, case):
    source = CLIENT.read_text(encoding="utf-8")
    assert before in source
    mutant = source.replace(before, after)
    if case == "offline":
        # Both lifecycle observers independently retire a disconnect. Disable
        # both for this red control rather than claim one redundant brake failed.
        mutant = mutant.replace('if dev_trade then dev_trade.forget("hello " .. tostring(reason)) end', "")
    p = Pump(source=mutant)
    if case == "gap":
        # Two pumps of one frame may never arm, even though the ROM binder would refuse.
        p.accepted(p.apply_command())
        p.client.last_frame = None
        p.client.frame_end(p.client)
        assert p.arm_trace == [(5, p.io.frame)]
    elif case in ("token", "slot"):
        cmd = p.apply_command()
        cmd[case] = "foreign" if case == "token" else 1
        p.accepted(cmd)
        p.frame(3)
        # The remaining independent guards can still prevent arm; the wrong-token
        # mutant is killed by its improper terminal cancellation of the current visit.
        if case == "token":
            assert len(p.sent("menu_result")) == 1
        elif case == "slot":
            assert len(p.arm_trace) == 1
    elif case == "duplicate":
        cmd = p.apply_command()
        p.accepted(cmd, dict(cmd, partner_name="LYRA"))
        p.frame(2)
        assert len(p.arm_trace) == 1
    elif case == "box":
        p.arm_writes()
        p.accepted()
        p.command({"cmd": "box_mon", "key": p.apply_command()["old_key"]})
        p.frame(3)
        assert p.count() == 2
    elif case == "uncertain":
        p.accepted(p.apply_command())
        p.frame()
        gen = p.mem[p.parts.profile.overlay.trade.lease.base + 6]
        p.put(5, 7)
        p.put(7, gen)
        p.put(8, 0)  # real binder poisons APPLY/DONE0
        p.frame(3)
        assert p.sent("trade_done")[0]["new_key"] == p.apply_command()["old_key"]
    else:
        p.accepted(p.apply_command())
        p.connected = False
        p.frame()
        assert len(p.arm_trace) == 1


@pytest.mark.parametrize("offset,expression", [
    (0, "bytes[f.magic+i] ~= l.magic[i]"),
    (12, "bytes[f.token+i] ~= v.token[i]"),
    (4, "bytes[f.version+1] == l.version"),
    (6, "bytes[f.generation+1] == v.gen"),
    (7, "bytes[f.ack+1] == v.gen"),
    (9, "bytes[f.slot+1] == v.slot"),
    (8, "bytes[f.result+1] == 0"),
    (5, 'bytes[f.command+1] == (v.role == "proposer" and family.commands.OFFER or family.commands.RELEASE)'),
], ids=["magic", "token", "version", "generation", "ack", "slot", "result", "command"])
def test_prepare_lease_guard_mutants_are_killed(offset, expression):
    source = CLIENT.read_text(encoding="utf-8")
    assert expression in source
    p = Pump(source=source.replace(expression, "false" if "~=" in expression else "true"))
    p.accepted()
    at = p.parts.profile.overlay.trade.lease.base + offset
    p.put(offset, ((p.mem[at] or 0) + 1) % 256)
    cmd = p.apply_command()
    cmd["cmd"] = "apply_prepare"
    p.command(cmd)
    p.frame(3)
    assert p.sent("apply_ready")[0]["ok"] is True  # forbidden by the unmutated oracle


@pytest.mark.parametrize("fault", ["write", "release", "close", "reset", "withdraw"],
                         ids=["staging", "release", "close", "reset", "withdraw"])
def test_apply_faults_follow_the_generation_publication_boundary(fault):
    p = Pump()
    p.accepted(p.apply_command())
    if fault == "write":
        p.fail_write = True
    p.frame()
    p.fail_write = False
    if fault == "release":
        gen = p.mem[p.parts.profile.overlay.trade.lease.base + 6]
        p.put(5, 7)
        p.put(7, gen)
        p.put(8, 1)
        p.fail_write = True
    elif fault == "close":
        p.put(5, 0)
    elif fault == "reset":
        p.client.abandon_timeline(p.client, "test rewind")
    elif fault == "withdraw":
        before = p.writes()
        p.command({"cmd": "withdraw_trade", "token": "test"})
        p.frame(2)
        assert p.writes() == before and not p.sent("trade_done")
        p.put(5, 0)
    p.frame(3)
    done = p.sent("trade_done")
    if fault == "write":
        # Staging failed before fresh generation; even the failed token close cannot start APPLY.
        assert done == []
        reports = [m for m in p.sent("menu_result") if m.get("choice") == 0]
        assert len(reports) == 1 and reports[0].get("withdraw") is True
        assert p.binder.disposition(p.binder)[0] == "NOT_PERFORMED"
    else:
        assert len(done) == 1 and done[0]["uncertain"] is True and "new_key" not in done[0]
        assert done[0].get("after_reset", False) == (fault == "reset")


@pytest.mark.parametrize("title", ["crystal", "gold", "silver"], ids=["crystal", "gold", "silver"])
def test_vanilla_nil_interface_differential(title):
    import subprocess

    from tests.unit import test_gen2_client as vanilla

    profile = json.loads((ROOT / f"data/games/gen2_{title}/profile.json").read_text())["titles"][title]
    repo = "pokecrystal" if title == "crystal" else "pokegold"
    if not (ROOT / f".cache/gen2-build/{repo}/{profile['artifact']}.gbc").exists():
        pytest.skip("vanilla ROM input absent")
    baseline = subprocess.check_output(
        ["git", "-C", str(ROOT), "show", "48172aa6cd6100e3463b8fa2966c68c6e5d3940b:lua/gen2/client.lua"],
        text=True, encoding="utf-8")
    traces = []
    for source in (baseline, CLIENT.read_text(encoding="utf-8")):
        w = vanilla.World(title, swaps={"lua/gen2/client.lua": source})
        w.checkpoint_ok = True
        w.frames(65)
        w.reply({"cmd": "config", "native_sounds": False}, {"cmd": "trade_mask", "mask": 4},
                {"cmd": "apply_prepare", "token": "same", "slot": 0, "old_key": "2AAA:1234:19"})
        w.frames(3)
        traces.append(
            (
                w.sent(),
                list(w.logs.values()),
                [list(row.values()) for row in w.emu.writes.values()],
                w.lua.eval("function(c) return #c.deferred,#c.held end")(w.client),
            )
        )
    assert traces[0] == traces[1]


@pytest.mark.parametrize(
    "action",
    ["withdraw", "disconnect", "identity", "epoch", "shutdown", "deadline", "parked", "mask"],
    ids=[
        "withdraw",
        "disconnect",
        "identity",
        "epoch",
        "shutdown",
        "deadline",
        "parked-key",
        "query-mask",
    ],
)
def test_caller_cancels_each_preapply_abandonment_once(action):
    p = Pump()
    if action == "mask":
        p.image("QUERY")
        p.frame()
        p.command({"cmd": "trade_mask", "mask": 64})
        p.frame()
    else:
        p.accepted(p.apply_command())
        if action == "withdraw":
            p.client.handle_command(
                p.client, p.lua.table_from({"cmd": "withdraw_trade", "token": "test"})
            )
        elif action == "disconnect":
            p.connected = False
        elif action == "identity":
            p.mem[wp.SYM["wPlayerID"][1]] += 1
        elif action == "epoch":
            p.client.epoch += 1
        elif action == "shutdown":
            p.client.stop(p.client)
        elif action == "deadline":
            p.io.frame += 3601
            p.client.last_frame = p.io.frame
        elif action == "parked":
            p.put(9, 1)  # bound slot no longer matches
        if action != "shutdown":
            p.frame(2)
    p.connected = True
    if action != "shutdown":
        p.frame(4)
    assert len(p.cancel_trace) == 1, p.cancel_trace
    assert not p.arm_trace and not p.sent("trade_done")
    if action != "mask":
        reports = [m for m in p.sent("menu_result") if m.get("choice") == 0]
        if action == "identity":
            assert reports == []  # existing drop_held discards the old identity's queued report
            assert any("identity_changed" in line for line in p.log.lines.values())
        else:
            assert len(reports) == 1 and reports[0].get("withdraw") is True
        assert p.binder.disposition(p.binder)[0] == "NOT_PERFORMED"
    else:
        assert not p.sent(
            "menu_result"
        )  # no server token or binder visit exists before QUERY answer


def responder_accepted(p):
    cmd = p.apply_command()
    p.command(dict(cmd, cmd="show_menu"))
    p.frame()
    gen = p.mem[p.parts.profile.overlay.trade.lease.base + 6]
    p.put(5, 7)
    p.put(7, gen)
    p.put(8, 0)
    p.frame()  # consent RELEASE
    assert p.sent("menu_result")[-1]["choice"] == 1
    p.command(cmd)


@pytest.mark.parametrize("role", ["proposer", "responder"], ids=["proposer", "responder"])
@pytest.mark.parametrize("fault", ["staging", "command", "generation"],
                         ids=["before-publication", "after-command", "after-fresh-generation"])
def test_caller_uses_binder_arm_fault_disposition(role, fault):
    p = Pump()
    if role == "proposer":
        p.accepted(p.apply_command())
    else:
        responder_accepted(p)
    family = p.parts.profile.overlay.trade
    base = family.lease.base
    held_gen = p.mem[base + 6]
    if fault == "generation":
        p.fail_after_once_address = base + 6  # store fresh generation, THEN throw
        p.fail_after_once_value = (held_gen + 1) % 256  # ignore the header's rewrite of the held generation
    else:
        p.fail_once_address = base + 6 if fault == "command" else family.staging.party.addr
    p.frame(3)
    if fault == "generation":
        assert p.mem[base + 6] == (held_gen + 1) % 256
        assert len(p.sent("trade_done")) == 1 and p.sent("trade_done")[0]["uncertain"] is True
        assert p.cancel_trace == []
        assert not [m for m in p.sent("menu_result") if m.get("choice") == 0]
        assert p.binder.disposition(p.binder)[0] == "UNCERTAIN"
    else:
        assert p.mem[base + 6] == p.mem[base + 7] == held_gen
        if fault == "command":
            assert p.mem[base + 5] == family.commands.APPLY  # command alone did land, but was not published
        assert p.sent("trade_done") == []
        reports = [m for m in p.sent("menu_result") if m.get("choice") == 0]
        assert len(reports) == 1
        assert reports[0].get("withdraw", False) == (role == "proposer")
        assert p.binder.disposition(p.binder)[0] == "NOT_PERFORMED"
        assert [p.mem[base + i] for i in range(12, 16)] == [0, 0, 0, 0]


@pytest.mark.parametrize(
    "action", ["withdraw", "disconnect", "shutdown"], ids=["withdraw", "disconnect", "shutdown"]
)
def test_caller_never_cancels_attempted_apply(action):
    p = Pump()
    p.accepted(p.apply_command())
    p.frame()
    assert p.arm_trace[-1][0] == 5
    if action == "withdraw":
        p.command({"cmd": "withdraw_trade", "token": "test"})
        p.frame()
    elif action == "disconnect":
        p.connected = False
        p.frame()
    else:
        p.client.stop(p.client)
    assert not p.cancel_trace


@pytest.mark.parametrize(
    "role", ["proposer", "responder"], ids=["offer-declined", "prompt-declined"]
)
def test_caller_does_not_cancel_terminal_decline(role):
    p = Pump()
    if role == "proposer":
        p.offered()
        p.command({"cmd": "trade_offer_ack", "ok": False})
        p.frame()
    else:
        p.command(dict(p.apply_command(), cmd="show_menu"))
        p.frame()
        gen = p.mem[p.parts.profile.overlay.trade.lease.base + 6]
        p.put(5, 7)
        p.put(7, gen)
        p.put(8, 1)
        p.frame()
    assert not p.cancel_trace


def test_cancel_token_fault_reports_not_performed_and_waits_for_closure(capfd):
    p = Pump()
    p.accepted()
    p.fail_write = True
    p.command({"cmd": "withdraw_trade", "token": "test"})
    p.frame()
    p.fail_write = False
    resets = []
    p.lua.globals().cancel_reset = lambda: resets.append(True)
    p.lua.execute("""return function(b)
        local reset=b.reset
        b.reset=function(self) cancel_reset(); return reset(self) end
    end""")(p.binder)
    before = p.writes()
    p.frame(4)
    assert len(p.cancel_trace) == 1 and p.writes() == before and resets == []
    assert p.binder.closed(p.binder) is False
    assert p.binder.disposition(p.binder)[0] == "NOT_PERFORMED"
    assert not p.sent("trade_done")
    reports = [m for m in p.sent("menu_result") if m.get("choice") == 0]
    assert len(reports) == 1 and reports[0].get("withdraw") is True
    assert capfd.readouterr().out.count("trade visit cancelled before APPLY") == 1
    p.put(5, 0)  # MODEL native token check/watchdog closes the lease, not a forged closed() answer
    p.frame(4)
    assert resets == [True] and len(p.cancel_trace) == 1 and p.writes() == before
    assert not p.sent("trade_done")
    assert [m for m in p.sent("menu_result") if m.get("choice") == 0] == reports
    assert "trade visit cancelled before APPLY" not in capfd.readouterr().out


@pytest.mark.parametrize("kind", ["cancel", "attempt"], ids=["drop-cancel", "pre-arm-attempted"])
def test_client_caller_mutants_turn_contract_red(kind):
    source = CLIENT.read_text()
    if kind == "cancel":
        old = 'local _, kind = call("cancel", why)'
        new = 'local _, kind = nil, "PENDING"'
    else:
        old = (
            "v.apply = nil\n"
            '                        local gen, state, problem = call("arm", APPLY, v.slot, v.token, data)\n'
            '                        v.attempted = gen ~= nil or state == "UNCERTAIN" -- no proof of non-publication stays held'
        )
        new = (
            "v.apply, v.attempted = nil, true\n"
            '                        local gen, state, problem = call("arm", APPLY, v.slot, v.token, data)'
        )
    assert source.count(old) == 1
    p = Pump(source=source.replace(old, new))
    if kind == "cancel":
        p.accepted()
        p.command({"cmd": "withdraw_trade", "token": "test"})
        p.frame()
        with pytest.raises(AssertionError):
            assert len(p.cancel_trace) == 1
        assert p.cancel_trace == [] and p.binder.phase(p.binder)[1] == "offer"
    else:
        p.accepted(p.apply_command())
        p.fail_once_address = p.parts.profile.overlay.trade.staging.party.addr
        p.frame(3)
        assert p.binder.disposition(p.binder)[0] == "NOT_PERFORMED"
        with pytest.raises(AssertionError):
            assert p.sent("trade_done") == []
        assert p.sent("trade_done")[0]["uncertain"] is True


def test_client_source_compiles_in_lua55():
    from lupa.lua55 import LuaRuntime

    lua = LuaRuntime(unpack_returned_tuples=True)
    assert lua.eval("function(s) return assert(load(s)) ~= nil end")(CLIENT.read_text())


def complete_received_mon(source=None):
    from server.adapters import polished_codec as pc
    p = Pump(source=source)
    cmd = p.apply_command()
    incoming = dict(p.mons[1], ot_id=p.mons[1]["ot_id"] ^ 0x1234)
    blob = pc.encode_party_mon(incoming)
    p.accepted(cmd)
    p.frame()
    # MODEL native REMOVE/compact/APPEND result: received mon is the last slot.
    for i, byte in enumerate(blob):
        p.mem[wp.SYM["wPartyMons"][1]+2*48+i] = byte
    gen = p.mem[p.parts.profile.overlay.trade.lease.base+6]
    p.put(5, 7)
    p.put(7, gen)
    p.put(8, 0)
    p.frame(3)
    done = p.sent("trade_done")
    assert len(done) == 1 and not done[0].get("uncertain")
    assert (done[0]["slot"], done[0]["new_key"], done[0]["new_species"]) == (2, pc.key(incoming), incoming["species_id"])
    assert p.mem[p.parts.profile.overlay.trade.lease.base+5] == 8
    assert not p.sent("menu_result")


def test_complete_reports_received_party_mon_via_owed_trade_done():
    complete_received_mon()


def test_complete_pump_mutant_is_red():
    source = CLIENT.read_text()
    anchor = 'if done.disposition == "COMPLETE" then'
    assert source.count(anchor) == 1
    with pytest.raises(AssertionError):
        complete_received_mon(source.replace(anchor, 'if false then'))


@pytest.mark.parametrize("role", ["proposer", "responder"])
def test_published_arm_fault_requires_reset_and_warns_once(role):
    p = Pump()
    shown = []
    p.lua.execute("return function(hud, rec) hud.show = function(text) rec(text) end end")(p.deps.hud, shown.append)
    if role == "proposer":
        p.accepted(p.apply_command())
    else:
        responder_accepted(p)
    base = p.parts.profile.overlay.trade.lease.base
    p.fail_after_once_address = base + 6
    p.fail_after_once_value = (p.mem[base + 6] + 1) % 256
    p.frame(3)
    done = p.sent("trade_done")
    assert len(done) == 1 and done[0].get("after_reset") is True
    assert done[0]["uncertain"] is True and "new_key" not in done[0]
    assert shown.count("TRADE UNCERTAIN - CHECK PARTY") == 1
    before = p.writes()
    p.put(5, 7)
    p.put(7, p.mem[base + 6])
    p.put(8, 2)
    p.frame(3)
    assert p.sent("trade_done") == done
    assert shown.count("TRADE UNCERTAIN - CHECK PARTY") == 1
    assert p.writes() == before


@pytest.mark.parametrize("phase", ["query", "offer"])
def test_poll_read_failure_is_logged_once_and_retries_without_outcome(phase):
    p = Pump()
    p.image("QUERY")
    if phase == "offer":
        p.frame()
        p.command({"cmd": "trade_mask", "mask": 4})
        p.frame()
        p.put(5, 2)
        p.put(6, 43)
        p.put(7, 42)
    method = "poll_" + phase
    original = p.binder[method]
    p.binder[method] = p.lua.eval('function() error("injected poll read failure") end')
    before = p.writes()
    p.frame(3)
    diagnostics = [s for s in p.log.lines.values() if "injected poll read failure" in s]
    assert len(diagnostics) == 1
    assert method in diagnostics[0] and "UNCERTAIN" in diagnostics[0]
    assert not p.sent("trade_done") and not p.sent("menu_result")
    assert p.writes() == before
    p.binder[method] = original
    p.frame(2)
    assert len(p.sent("trade_" + phase)) == 1
