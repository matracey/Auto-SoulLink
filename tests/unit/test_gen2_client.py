"""Gen 2 client rules (P3b): lupa on the real Entry.build_candidate graph with emulator stubs.

The graph is SOURCE/MODEL (signals.new_model, an injected checkpoint); nothing here is a
PHYSICAL receipt. Every falsifier runs twice: on the shipped sources, where the rule holds,
and on a deliberately broken variant (a textual mutation of one module, swapped in through
Lua's dofile), where the same assertion must fail. A mutation whose anchor text is missing
fails loudly, so a variant cannot silently stop testing anything.
"""

import json
import sys
from pathlib import Path

import pytest
from lupa.lua54 import LuaRuntime

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from server.adapters import gen2_codec  # noqa: E402
from server.adapters.gen2_gsc import Gen2GSCAdapter  # noqa: E402

EMULATOR = r"""
return function(rom, shadow_addr)
    local mem = {["System Bus"] = {}, CartRAM = {}}
    local emu = {bank = 1, shadow = 1, wram_bank = 1, frame = 1, callbacks = {}, writes = {},
                 -- BizHawk 2.11.1 Gambatte emu.getregister: singles and bank names, no pairs (PLAN A15)
                 regs = {PC = 0, SP = 0xC020, A = 0, B = 0, C = 0, D = 0, E = 0, F = 0, H = 0, L = 0,
                         ["ROM0 BANK"] = 0, ["ROMX BANK"] = 1, ["VRAM BANK"] = 0, ["SRAM BANK"] = 0,
                         ["WRAM BANK"] = 1}}
    local io = {model_only = true, cart_ram_linear = true}
    function io.read_u8(a, d)
        d = d or "System Bus"
        if d == "ROM" then return rom:byte(a + 1) or 0 end
        if d == "System Bus" and a < 0x8000 then
            local flat = a < 0x4000 and a or emu.bank * 0x4000 + a - 0x4000
            return rom:byte(flat + 1) or 0
        end
        if d == "System Bus" and a == shadow_addr then return emu.shadow end
        return mem[d][a] or 0
    end
    function io.read_range(a, n, d)
        local out = {}
        for i = 1, n do out[i] = io.read_u8(a + i - 1, d) end
        return out
    end
    function io.write_u8(a, v, d)
        emu.writes[#emu.writes + 1] = {a, v, d}
        mem[d or "System Bus"][a] = v
    end
    function io.bank_valid(bank, a, n)
        if a < 0x4000 then return bank == 0 and a + n <= 0x4000 end
        if a < 0x8000 then return bank == emu.bank and a + n <= 0x8000 end
        if a >= 0xC000 and a < 0xD000 then return bank == 0 and a + n <= 0xD000 end
        if a >= 0xD000 and a < 0xE000 then return bank == emu.wram_bank and a + n <= 0xE000 end
        return bank == 0 and a >= 0xFF80 and a + n <= 0xFFFF
    end
    function io.stack_valid(sp, n) return sp >= 0xC000 and sp + n <= 0xD000 end
    function io.domain_size(d)
        if d == "ROM" then return #rom end
        return d == "CartRAM" and 0x8000 or 0x10000
    end
    function io.register(name)
        local value = emu.regs[name]
        if value == nil then error("BizHawk 2.11.1 emu.getregister has no " .. tostring(name) .. " (singles only)") end
        return value
    end
    function io.framecount() return emu.frame end
    function io.on_bus_exec(fn, addr, name) emu.callbacks[name] = {fn = fn, addr = addr}; return name end
    function io.unregister(name) emu.callbacks[name] = nil; return true end
    function emu.poke(d, a, bytes) for i = 1, #bytes do mem[d][a + i - 1] = bytes[i] end end
    function emu.fire(bank, addr)
        emu.regs.PC, emu.bank, emu.shadow = addr, bank, bank
        local hit = 0
        for _, cb in pairs(emu.callbacks) do
            if cb.addr == addr then hit = hit + 1; cb.fn() end
        end
        return hit
    end
    local net = {sent = {}, inbox = {}, up = true}
    function net.send(line) net.sent[#net.sent + 1] = line end
    function net.receive() return table.remove(net.inbox, 1) end
    function net.pump() end
    function net.connected() return net.up end
    local hud = {shown = {}}
    local function rec(kind) return function(text) hud.shown[#hud.shown + 1] = kind .. ":" .. tostring(text) end end
    hud.show, hud.prompt, hud.nuzlocke_start = rec("show"), rec("prompt"), rec("nuzlocke")
    hud.set_rebuilding, hud.set_game_over, hud.clear_rebuilding = rec("rebuild"), rec("game_over"), rec("rebuild_done")
    hud.sanitize = function(s) return s end
    local logs = {}
    return emu, io, net, hud, logs, function(t) logs[#logs + 1] = t end
end
"""

MUTATE = r"""
return function(swaps)
    local real = dofile
    dofile = function(path)
        for suffix, source in pairs(swaps) do
            if path:sub(-#suffix) == suffix then return assert(load(source, "@" .. path))() end
        end
        return real(path)
    end
end
"""

POLICY = r"""
return function(owner)
    return {authorize = function(op, req) return owner(op, req.slot) end,
            pointer_stable = function() return true end,
            lifetime = {capture = function() return 1 end, valid = function() return true end},
            provenance = function() return {site = "test_gen2_client"} end}
end
"""


def mon(species=25, ot=0x1234, dvs=0x2AAA, nickname=0x81, hp=30, egg=False):
    return {"species": species, "ot": ot, "dvs": dvs, "nickname": nickname, "hp": hp, "egg": egg}


def collection(mons, capacity, stride):
    record_start = capacity + 2
    ot_start = record_start + capacity * stride
    nick_start = ot_start + capacity * 11
    raw = bytearray(nick_start + capacity * 11)
    raw[0] = len(mons)
    raw[len(mons) + 1] = 255
    for i, m in enumerate(mons):
        raw[i + 1] = 253 if m["egg"] else m["species"]
        start = record_start + i * stride
        raw[start] = m["species"]
        raw[start + 6:start + 8] = m["ot"].to_bytes(2, "big")
        raw[start + 21:start + 23] = m["dvs"].to_bytes(2, "big")
        raw[start + 31] = 20
        if stride == 48:
            raw[start + 34:start + 36] = m["hp"].to_bytes(2, "big")
            raw[start + 36:start + 38] = (50).to_bytes(2, "big")
        raw[ot_start + i * 11:ot_start + i * 11 + 2] = bytes([0x80, 0x50])
        raw[nick_start + i * 11:nick_start + i * 11 + 2] = bytes([m["nickname"], 0x50])
    return raw


def codec_key(m):
    return gen2_codec.key({"dv_word": m["dvs"], "ot_id": m["ot"], "species_id": m["species"]})


OPEN = r"""
return function(files)
    local real = io.open
    io.open = function(path, mode)
        for suffix, text in pairs(files) do
            if path:sub(-#suffix) == suffix then
                if text == false then return nil, "missing test input" end
                return {read = function() return text end, close = function() end}
            end
        end
        return real(path, mode)
    end
end
"""


CHECKPOINT = {t: json.loads((ROOT / f"data/games/gen2_{t}/write_checkpoint.json").read_text())["titles"][t]
              for t in ("crystal", "gold", "silver")}


class Refused(Exception):
    """Entry.build refused the production graph (the Lua reason is the message)."""



# Patch-first (owner 2026-10-02): the SLink companion is REQUIRED for every Gen 2 title, so
# lua/gen2/entry.lua's `eligible` refuses a CLEAN row ("needs the SLink companion patch") and the
# production graph composes only an ACTIVATED overlay row. Until tools/gen_gen2_admission.py
# --promote-overlays lands such a row there is NO legal production cartridge for Gen 2 at all, so
# these tests read the shipped catalog row itself instead of pinning the CLEAN fixture the owner
# ruled out. The skip is a dependency signal, never a green: it names itself and clears itself.
def overlay_row(title):
    rows = json.loads((ROOT / f"data/games/gen2_{title}/admission.json").read_text(encoding="utf-8"))["artifacts"]
    return next(r for r in rows if r["kind"] == "overlay")


def overlay_admitted(title):
    row = overlay_row(title)
    return (row.get("status") == "ADMITTED" and row.get("selection") == "SELECTED"
            and (row.get("runtime_gate") or {}).get("state") == "ADMITTED")


NEEDS_OVERLAY = pytest.mark.skipif(
    not all(overlay_admitted(t) for t in ("crystal", "gold", "silver")),
    reason="no ADMITTED Gen 2 overlay row yet: the launcher refuses every Gen 2 cartridge "
           "(clean needs the SLink companion; the overlay row is still BUILT/FUTURE) -- "
           "production-graph coverage returns when the overlay rows are promoted")

class World:
    def __init__(self, title="crystal", swaps=None, production=False, files=None, artifact_kind=None, clean=False):
        self.title = title
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        if swaps:
            self.lua.execute(MUTATE)(self.lua.table_from(swaps))
        if files:
            self.lua.execute(OPEN)(self.lua.table_from(files))
        self.production = production
        self.profile = json.loads((ROOT / f"data/games/gen2_{title}/profile.json").read_text())["titles"][title]
        self.sites = json.loads((ROOT / f"data/games/gen2_{title}/engine_signals.json").read_text())["titles"][title]["sites"]
        self.points = {n: pt for s in self.sites.values() for n, pt in s["point_symbols"].items()}
        repo = "pokecrystal" if title == "crystal" else "pokegold"
        rom = (ROOT / f".cache/gen2-build/{repo}/{self.profile['artifact']}.gbc").read_bytes()
        if production and not clean:
            # Patch-first: the only legal production cartridge is the overlay, i.e. the clean build
            # with the shipped UPS applied (its sha1 is the ADMITTED overlay row's).
            from patch.tools.make_ups import ups_apply
            rows = json.loads((ROOT / f"data/games/gen2_{title}/admission.json").read_text())["artifacts"]
            ups = next(r for r in rows if r["kind"] == "overlay")["ups"]["file"]
            rom = ups_apply(rom, (ROOT / ups).read_bytes())
        self.emu, self.io, self.net, self.hud, self.logs, log = self.lua.execute(EMULATOR)(
            rom, self.profile["ram"]["hROMBank"])
        self.checkpoint_ok = False
        self.owned = lambda op, slot: True
        self.covered = {"party_hp", "box_deposit", "party_collection", "box_withdraw", "backing_box"}
        checkpoint = self.lua.eval("function(f, c) return {check=function() return f() end, "
                                   "covers=function(_, kind) return c(kind) end} end")(
            lambda: (self.checkpoint_ok, "stub checkpoint"), lambda kind: kind in self.covered)
        entry = self.lua.eval("dofile")((ROOT / "lua/gen2/entry.lua").as_posix())
        if production:
            # Live-shaped IO: not model_only, with the domain list the checkpoint evaluator needs.
            self.io.model_only = False
            self.io.domains = self.lua.eval('function() return {"ROM", "System Bus", "CartRAM"} end')
            args = self.lua.table(root=ROOT.as_posix(), title=title, io=self.io, net=self.net, hud=self.hud,
                                  player="a", log=log, rom_size=len(rom),
                                  read_rom_u8=self.lua.eval("function(io) return function(a) return io.read_u8(a, 'ROM') end end")(self.io))
            result = entry.build(args)
        else:
            args = self.lua.table(root=ROOT.as_posix(), title=title, io=self.io, candidate_only=True,
                                  write_policy=self.lua.execute(POLICY)(lambda op, slot: self.owned(op, slot)),
                                  net=self.net, hud=self.hud, player="a", checkpoint=checkpoint, log=log,
                                  artifact_kind=artifact_kind)
            result = entry.build_candidate(args)
        parts = result[0] if isinstance(result, tuple) else result
        if production and parts is None:
            raise Refused(result[1])
        assert parts is not None, result
        self.parts, self.client = parts, parts.client
        # A saved game standing in the overworld: one party mon, one ball, box 1 empty.
        self.field("wPlayerID", 0x1234, 2)
        self.party([mon()])
        self.box([])
        for row in self.profile["storage_boxes"]:   # EraseBoxes: every backing box empty and terminated
            self.emu.poke("CartRAM", row["flat"], self.lua.table_from([0, 255]))
        self.ram("wNumBalls", [1, 1, 5, 255])
        self.field("wMapGroup", 24)
        self.field("wMapNumber", 3)
        for name in ("wBattleMode", "wBattleType", "wBattleScriptFlags", "wCurBox", "wCurPartyMon",
                     "wCurBattleMon", "wLinkMode"):
            self.field(name, 0)
        self.client.start(self.client)

    # ── memory ──
    def ram(self, name, data):
        self.emu.poke("System Bus", self.profile["ram"][name], self.lua.table_from(list(data)))

    def field(self, name, value, width=1):
        address = self.points[name]["addr"] if name in self.points else self.profile["ram"][name]
        data = value.to_bytes(width, "big") if width == 2 and name == "wPlayerID" else \
            bytes((value >> (8 * i)) & 255 for i in range(width))
        self.emu.poke("System Bus", address, self.lua.table_from(list(data)))

    def party(self, mons):
        self.ram("wPartyCount", collection(mons, 6, 48))

    def box(self, mons):
        self.emu.poke("CartRAM", self.profile["derived"]["active_box_flat"],
                      self.lua.table_from(list(collection(mons, 20, 32))))

    def hp_of(self, slot):
        base = self.profile["ram"]["wPartyMon1"] + slot * 48
        return self.io.read_u8(base + 34) * 256 + self.io.read_u8(base + 35), self.io.read_u8(base + 32)

    # ── engine ──
    def fire(self, site_id):
        site = self.sites[site_id]
        assert self.emu.fire(site["bank"], site["addr"]) > 0, site_id

    def frames(self, n=1):
        for _ in range(n):
            self.emu.frame += 1
            self.client.frame_end(self.client)

    def reply(self, *commands):
        self.net.inbox[len(self.net.inbox) + 1] = json.dumps({"commands": list(commands)})

    # ── observations ──
    def sent(self, event=None):
        lines = [json.loads(line) for line in self.net.sent.values()]
        return [m for m in lines if event is None or m["event"] == event]

    def shown(self):
        return list(self.hud.shown.values())

    def written(self):
        return [tuple(w.values()) for w in self.emu.writes.values()]

    def hold(self, **broken):
        """Production: the CPU reaches the pack checkpoint PC in a held idle frame (every pack
        predicate, caller word and bank shadow as the U2 PHYSICAL gate accepted them); `broken`
        overrides predicate values. The PC leaves the checkpoint again before frame end."""
        primary = CHECKPOINT[self.title]["primary"]
        for condition in primary["state_predicates"]:
            value = broken.get(condition["symbol"], condition["value"])
            self.emu.poke("System Bus", condition["address"], self.lua.table_from([value]))
        sp = primary["caller_stack"]["minimum_sp"] + 16
        for word in primary["caller_stack"]["required_words"]:
            self.emu.poke("System Bus", sp + word["offset_from_sp"],
                          self.lua.table_from([word["value"] & 255, word["value"] >> 8]))
        self.emu.regs.SP = sp
        hit = self.emu.fire(primary["execution_before"]["bank"], primary["execution_before"]["pc"])
        self.emu.regs.PC = 0
        return hit

    def hello(self):
        if self.production:
            self.hold()
            self.frames(1)
        self.checkpoint_ok = True
        self.frames(2)
        hellos = self.sent("hello")
        assert len(hellos) == 1, self.logs.values()
        return hellos[0]


def mutant(path, *pairs):
    source = (ROOT / path).read_text(encoding="utf-8")
    for old, new in pairs:
        assert source.count(old) == 1, f"mutation anchor drifted in {path}: {old!r}"
        source = source.replace(old, new)
    return {path: source}


def falsify(check, swaps):
    """The rule holds on the shipped sources and fails on the broken variant."""
    check(World())
    with pytest.raises(AssertionError):
        check(World(swaps=swaps))


# ── falsifiers ──────────────────────────────────────────────────────────────────────────
def test_no_hello_before_the_checkpoint():
    def check(world):
        world.frames(120)
        assert world.sent("hello") == []
        world.hello()

    falsify(check, mutant("lua/gen2/client.lua", ("if battle.mode == 0 and not (self.checkpoint_held or safety.check(PARTY_HP)) then",
                                                  "if false then")))


def test_no_write_outside_the_armed_gate():
    def check(world):
        world.party([mon(), mon(species=172, dvs=0x3AAA)])
        world.hello()
        world.checkpoint_ok = False
        world.reply({"cmd": "force_faint", "key": codec_key(mon(species=172, dvs=0x3AAA))})
        world.frames(130)
        assert world.written() == []
        assert not any("KO'd" in text for text in world.shown())

    falsify(check, mutant("lua/gen2/client.lua", ("local safe = safety.check(PARTY_HP)", "local safe = true")))


def test_the_permit_itself_refuses_an_unarmed_write():
    world = World()
    ok, err = world.lua.eval("function(w) return pcall(function() return w:faint_party_slot(0, {mode=0, link_mode=0}) end) end")(
        world.parts.writes)
    assert ok is False and "no armed write window" in err and world.written() == []


def test_an_unhatched_egg_never_reaches_the_wire():
    def check(world):
        first, second = mon(), mon(species=172, dvs=0x3AAA)
        world.party([first, mon(species=175, dvs=0x4AAA, egg=True), second])
        world.box([mon(species=176, dvs=0x5AAA, egg=True), mon(species=133, dvs=0x6AAA)])
        hello = world.hello()
        assert [(e["slot"], e["key"]) for e in hello["party"]] == [(0, codec_key(first)), (2, codec_key(second))]
        assert [(e["box"], e["slot"], e["species_id"]) for e in hello["pc_boxes"]] == [(0, 1, 133)]
        world.frames(30)
        tick = world.sent("tick")[-1]
        assert [e["slot"] for e in tick["party"]] == [0, 2]

    # With only the client filter gone the wire refusal fails the hello closed; with both
    # gone the egg is on the wire. Either way the rule's assertion is what goes red.
    client_only = mutant("lua/gen2/client.lua", ("            if not m.is_egg then\n                local e, why = wire.party_entry",
                                                 "            if true then\n                local e, why = wire.party_entry"))
    falsify(check, client_only)
    falsify(check, {**client_only, **mutant("lua/gen2/wire.lua", ("    if mon.is_egg then return", "    if false then return"))})


def test_no_capture_without_its_acquisition_latch():
    def check(world):
        world.hello()
        world.field("wBattleMode", 1)
        world.party([mon(), mon(species=19, dvs=0x7AAA)])
        world.fire("capture_party_finalized")  # the final-name site alone: no insertion latch
        world.frames(1)
        assert world.sent("capture") == []

    # broken binder: the party final is accepted with no insertion latch behind it
    falsify(check, mutant("lua/gen2/signals.lua",
                          ('if not found then return false,"required prior success/identity unavailable" end',
                           'if not found then return name == "capture_party_finalized" end'),
                          ('local before = assert(latches[rule.prior],"prior acquisition lost")',
                           "local before = latches[rule.prior] or receiver(STARTS[rule.prior],site)")))


def test_no_faint_without_its_signal_latch():
    def check(world):
        world.hello()
        world.field("wBattleMode", 1)
        world.party([mon(hp=0)])  # HP 0 alone, no UpdateFaintedPlayerMon
        world.frames(40)
        assert world.sent("faint") == []

    falsify(check, mutant("lua/gen2/client.lua", (
        "if #self.faint_latches == 0 then return end",
        "if #self.faint_latches == 0 then self.faint_latches = {{key = mon_key(current_party().mons[1]), frame = self.frame}} end")))


def test_lua_key_agrees_with_the_python_codec_on_the_same_record():
    def check(world):
        starter, caught = mon(species=155, ot=0x0BCD, dvs=0xFEDC), mon(species=19, ot=0x0BCD, dvs=0x1357)
        world.party([starter])
        hello = world.hello()
        assert hello["party"][0]["key"] == codec_key(starter)
        world.field("wBattleMode", 1)
        world.party([starter, caught])
        world.fire("capture_party")
        world.fire("capture_party_finalized")
        world.frames(1)
        assert [c["key"] for c in world.sent("capture")] == [codec_key(caught)]

    falsify(check, mutant("lua/gen2/wire.lua", ('string.format("%04X:%04X:%02X", mon.dv_word, mon.ot_id, mon.species_id)',
                                                'string.format("%04X:%04X:%02X", mon.ot_id, mon.dv_word, mon.species_id)')))


def test_an_active_slot_forced_faint_is_never_reported_as_success():
    def check(world):
        active, bench = mon(), mon(species=172, dvs=0x3AAA)
        in_battle(world, [active, bench])
        world.reply({"cmd": "force_faint", "key": codec_key(active), "nickname": "PIKA"})
        world.frames(130)            # no battle hold yet: nothing moves, nothing is claimed
        world.owned = lambda op, slot: False   # the write policy refuses at the hold
        battle_hold(world)
        assert world.hp_of(0) == (30, 0) and world.written() == [] and battle_hp(world) == 30
        assert not any("KO'd" in text for text in world.shown())
        assert any("battle write refused" in line for line in world.logs.values())

    falsify(check, mutant("lua/gen2/client.lua", ("            if landed then\n", "            if landed or err ~= nil then\n")))


# O-30 phase 2: a death deferred past the battle follows the evolution-stable identity (DV word +
# OT id) to the one party descendant; Gen 2 sends no key_change for evolution (U1 OPEN), so the
# exact key is gone by the checkpoint and the mon used to escape (A2).
def deferred_faint_then(world, party_after):
    bulbasaur = mon(species=1, dvs=0x3AAA)
    world.party([bulbasaur, mon()])
    world.hello()
    world.frames(60)                 # a live validation enables writes
    world.checkpoint_ok = False      # in battle: no checkpoint hold
    world.field("wBattleMode", 1)
    world.reply({"cmd": "force_faint", "key": codec_key(bulbasaur), "nickname": "BULBA"})
    world.frames(2)
    end_battle(world)                # the battle ended before a battle hold: the death is owed at the checkpoint
    world.party(party_after)         # EvolveAfterBattle rewrote the species; no key_change followed
    world.checkpoint_ok = True
    world.frames(2)
    return bulbasaur


def test_a_deferred_faint_follows_the_mon_through_its_evolution():
    world = World()
    bulbasaur = mon(species=1, dvs=0x3AAA)
    deferred_faint_then(world, [dict(bulbasaur, species=2), mon()])
    assert world.hp_of(0) == (0, 0) and world.hp_of(1) == (30, 0)
    assert any(f"force_faint matched evolved {codec_key(bulbasaur)}->{codec_key(dict(bulbasaur, species=2))}" in line
               for line in world.logs.values())


def test_an_ambiguous_evolved_match_is_refused():
    world = World()
    bulbasaur = mon(species=1, dvs=0x3AAA)
    deferred_faint_then(world, [dict(bulbasaur, species=2), dict(bulbasaur, species=3)])
    assert world.hp_of(0) == (30, 0) and world.hp_of(1) == (30, 0) and world.written() == []
    assert any("ambiguous evolved match" in line for line in world.logs.values())


def test_a_dv_ot_match_that_is_not_a_descendant_is_refused():
    world = World()
    bulbasaur = mon(species=1, dvs=0x3AAA)
    deferred_faint_then(world, [dict(bulbasaur, species=4), mon()])   # Charmander: same DV/OT, other family
    assert world.hp_of(0) == (30, 0) and world.written() == []
    assert any("not a descendant" in line for line in world.logs.values())


# ── O-30 phase 3: deaths land IN battle, at the battle hold (before `call DetermineMoveOrder`) ──────────
HOLDS = {t: json.loads((ROOT / f"data/games/gen2_{t}/write_checkpoint.json").read_text())["titles"][t]
         for t in ("crystal", "gold", "silver")}


def hold_ram(world, name):
    return HOLDS[world.title]["battle_hold"]["write"]["targets"][name]["address"]


def in_battle(world, party, active=0, battle_type=0, link=0):
    """A committed turn: party on the field, the active mon's battle struct loaded."""
    world.party(party)
    world.hello()
    world.frames(60)                 # a live validation enables writes
    world.field("wBattleMode", 1)
    world.field("wBattleType", battle_type)
    world.field("wLinkMode", link)
    world.field("wCurBattleMon", active)
    world.emu.poke("System Bus", hold_ram(world, "wBattleMonSpecies"), world.lua.table_from([party[active]["species"]]))
    world.emu.poke("System Bus", hold_ram(world, "wBattleMonHP"), world.lua.table_from([0, 30]))
    world.emu.poke("System Bus", hold_ram(world, "wBattlePlayerAction"), world.lua.table_from([0]))


def battle_hold(world):
    """The player committed the turn: the CPU is at `call DetermineMoveOrder`."""
    execution = HOLDS[world.title]["battle_hold"]["execution_before"]
    return world.emu.fire(execution["bank"], execution["pc"])


def battle_hp(world):
    address = hold_ram(world, "wBattleMonHP")
    return world.io.read_u8(address) * 256 + world.io.read_u8(address + 1)


def action(world):
    return world.io.read_u8(hold_ram(world, "wBattlePlayerAction"))


def test_an_active_battler_death_lands_at_the_battle_hold():
    world = World()
    active, bench = mon(), mon(species=172, dvs=0x3AAA)
    in_battle(world, [active, bench])
    world.reply({"cmd": "force_faint", "key": codec_key(active), "nickname": "PIKA"})
    world.frames(3)
    assert battle_hp(world) == 30 and world.hp_of(0) == (30, 0) and world.written() == []   # waits for the hold
    assert battle_hold(world) >= 1
    assert battle_hp(world) == 0 and world.hp_of(0) == (0, 0) and world.hp_of(1) == (30, 0)
    assert action(world) == 1                                        # BATTLEPLAYERACTION_USEITEM
    assert world.written()[-1][0] == hold_ram(world, "wBattlePlayerAction")   # the action byte LAST
    assert "show:!! PIKA KO'd" in world.shown()


def test_a_bench_death_lands_at_the_battle_hold_in_a_special_battle():
    world = World()
    active, bench = mon(), mon(species=172, dvs=0x3AAA)
    in_battle(world, [active, bench], battle_type=6)                 # BATTLETYPE_CONTEST
    world.reply({"cmd": "force_faint", "key": codec_key(bench), "nickname": "PICHU"})
    world.frames(2)
    battle_hold(world)
    assert world.hp_of(1) == (0, 0) and world.hp_of(0) == (30, 0)
    assert battle_hp(world) == 30 and action(world) == 0             # the active battler is untouched


def test_a_link_battle_death_never_writes_in_battle_and_lands_at_the_checkpoint():
    world = World()
    active = mon()
    in_battle(world, [active, mon(species=172, dvs=0x3AAA)], link=1)
    world.checkpoint_ok = False
    world.reply({"cmd": "force_faint", "key": codec_key(active)})
    world.frames(2)
    battle_hold(world)
    assert world.written() == [] and battle_hp(world) == 30
    world.field("wLinkMode", 0)
    end_battle(world)                                                # still owed: the checkpoint zeroes it
    world.checkpoint_ok = True
    world.frames(2)
    assert world.hp_of(0) == (0, 0)


def test_a_transformed_battler_still_dies_and_a_foreign_struct_waits():
    world = World()
    active = mon()
    in_battle(world, [active, mon(species=172, dvs=0x3AAA)])
    world.emu.poke("System Bus", hold_ram(world, "wBattleMonSpecies"), world.lua.table_from([132]))   # not ours
    world.reply({"cmd": "force_faint", "key": codec_key(active)})
    world.frames(2)
    battle_hold(world)
    assert world.written() == [] and battle_hp(world) == 30          # the struct is not the slot's: wait
    world.emu.poke("System Bus", hold_ram(world, "wPlayerSubStatus5"), world.lua.table_from([1 << 3]))  # TRANSFORMED
    battle_hold(world)
    assert battle_hp(world) == 0 and world.hp_of(0) == (0, 0)


def test_the_engine_faint_echo_of_a_commanded_death_is_not_reported():
    """A clause-rejection kill can name a key whose link is still ALIVE: its engine faint must not read as new."""
    world = World()
    active = mon()
    in_battle(world, [active, mon(species=172, dvs=0x3AAA)])
    world.reply({"cmd": "force_faint", "key": codec_key(active)})
    world.frames(2)
    battle_hold(world)
    world.fire("battle_faint")                                       # HandlePlayerMonFaint: UpdateFaintedPlayerMon
    world.frames(2)
    assert world.sent("faint") == []
    assert any("faint echo of a commanded death" in line for line in world.logs.values())


@pytest.mark.parametrize("after", ["evolved", "tower_challenge_end"])
def test_a_battle_death_is_re_zeroed_at_the_checkpoint_after_a_revive(after):
    """EvolveAfterBattle adds the max-HP gain to a fainted mon; the Battle Tower reloads and heals the party.
    Dead stays dead: every landed battle write leaves a quiet checkpoint re-zero. The tower row is the
    reload after the LAST battle only (the player walks out to a checkpoint); between tower battles there
    is no checkpoint (Script_BattleRoomLoop), and tests/unit/test_o30_faint_followups.py
    test_gen2_a_tower_revival_between_battles_is_re_zeroed_at_the_next_battle_hold covers that."""
    world = World()
    lead = mon(species=1, dvs=0x3AAA)
    in_battle(world, [lead, mon()])
    world.emu.poke("System Bus", hold_ram(world, "wBattleMonSpecies"), world.lua.table_from([1]))
    world.checkpoint_ok = False
    world.reply({"cmd": "force_faint", "key": codec_key(lead), "nickname": "BULBA"})
    world.frames(2)
    world.checkpoint_ok = True       # the stub stands for both holds; the frame below is not a checkpoint yet
    battle_hold(world)
    assert world.hp_of(0) == (0, 0)
    world.checkpoint_ok = False
    end_battle(world)
    revived = dict(lead, species=2 if after == "evolved" else 1, hp=7)
    world.party([revived, mon()])
    world.checkpoint_ok = True
    world.frames(2)
    assert world.hp_of(0) == (0, 0)
    assert world.shown().count("show:!! BULBA KO'd") == 1           # the re-zero is quiet


def test_a_contest_hidden_mon_dies_when_the_contest_returns_it():
    """Ruling (a): ContestDropOffMons masks the party to one; a death for a hidden mon is held, never dropped."""
    world = World()
    lead, hidden = mon(), mon(species=172, dvs=0x3AAA)
    world.party([lead, hidden])
    world.hello()
    world.frames(60)
    mask = HOLDS[world.title]["contest_mask"]
    world.party([lead])                                              # masked: count 1
    world.emu.poke("System Bus", mask["address"], world.lua.table_from([1 << mask["bit"]]))
    world.reply({"cmd": "force_faint", "key": codec_key(hidden), "nickname": "PICHU"})
    world.frames(5)
    assert not any("key not in party" in line for line in world.logs.values())
    world.emu.poke("System Bus", mask["address"], world.lua.table_from([0]))
    world.party([lead, hidden])                                      # ContestReturnMons
    world.frames(2)
    assert world.hp_of(1) == (0, 0)


def test_a_reset_leaves_no_stale_latch():
    def check(world):
        world.hello()
        world.field("wBattleMode", 1)
        world.fire("battle_faint")  # copy-back not landed: the latch waits for HP 0
        world.party([mon(), mon(species=19, dvs=0x7AAA)])
        world.fire("capture_party")  # insertion latch, never finalized
        world.frames(1)
        world.fire("soft_reset")
        world.frames(1)
        world.field("wBattleMode", 0)
        world.party([mon(hp=0), mon(species=19, dvs=0x7AAA)])  # the reloaded save
        world.fire("capture_party_finalized")
        world.frames(40)
        assert world.sent("faint") == [] and world.sent("capture") == []

    falsify(check, mutant("lua/gen2/client.lua", (
        "self.faint_latches, self.battle, self.pending_rescan = {}, nil, false",
        "self.battle, self.pending_rescan = nil, false")))


# ── positive controls ───────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("title", ["crystal", "gold", "silver"])
def test_one_hello_with_the_protocol_party_shape(title):
    world = World(title)
    hello = world.hello()
    world.frames(90)
    assert len(world.sent("hello")) == 1
    assert hello["rom_type"] == title.capitalize() and hello["foundation"] == "gen2_gsc"
    assert hello["ot_id"] == 0x1234 and hello["has_pokeballs"] is True and hello["ball_count"] == 5
    (entry,) = hello["party"]
    assert set(entry) >= {"key", "slot", "species_id", "level", "hp", "maxHP", "status_cond", "moves",
                          "pp", "pp_ups", "held_item_id", "active", "blob_hex"}
    assert (entry["slot"], entry["key"], entry["level"], entry["hp"], entry["maxHP"]) == (0, codec_key(mon()), 20, 30, 50)
    adapter = Gen2GSCAdapter(title)
    assert adapter.validate_party_blob(entry["blob_hex"], key=entry["key"], species_marker=entry["species_id"])
    assert "rom_content" not in hello and hello["panel"] is False and hello["sfx"] is False


def test_a_capture_carries_key_area_and_in_box():
    world = World()
    world.hello()
    world.field("wBattleMode", 1)
    caught = mon(species=19, dvs=0x7AAA)
    world.fire("wild_ready")
    world.party([mon(), caught])
    world.fire("capture_party")
    world.fire("capture_party_finalized")
    world.frames(1)
    boxed = mon(species=161, dvs=0x8AAA)
    world.box([boxed])
    world.fire("capture_box")
    world.fire("capture_box_finalized")
    world.fire("battle_end")  # same frame: the boundary must not discard the finalized capture
    world.frames(1)
    captures = world.sent("capture")
    assert [(c["key"], c["area_id"], c["in_box"], c["gift"]) for c in captures] == [
        (codec_key(caught), "route_29", False, False), (codec_key(boxed), "route_29", True, False)]
    assert world.sent("no_catch") == []


def test_a_bench_faint_is_written_through_the_permit():
    world = World()
    active, bench = mon(), mon(species=172, dvs=0x3AAA)
    world.party([active, bench])
    world.hello()
    world.frames(60)  # a live validation enables writes
    world.reply({"cmd": "force_faint", "key": codec_key(bench), "nickname": "PICHU"})
    world.frames(2)
    assert world.hp_of(1) == (0, 0) and world.hp_of(0) == (30, 0)
    receipts = [dict(r.items()) for r in world.parts.writes.log.values()]
    assert [(r["why"], r["status"], r["n"]) for r in receipts] == [("overworld", "written", 1), ("overworld", "written", 2)]
    assert "show:!! PICHU KO'd" in world.shown()


def test_a_hatched_egg_is_published_as_a_gift_daycare_capture():
    world = World()
    egg = mon(species=175, dvs=0x4AAA, egg=True)
    world.party([mon(), egg])
    assert [e["slot"] for e in world.hello()["party"]] == [0]
    world.field("wCurPartyMon", 1)
    world.party([mon(), dict(egg, egg=False)])  # the hatch publishes the species marker
    world.fire("hatch_species")
    hatched = dict(egg, egg=False, ot=0x1234, nickname=0x83)
    world.party([mon(), hatched])
    world.fire("hatch_finalized")
    world.frames(1)
    (capture,) = world.sent("capture")
    assert (capture["key"], capture["area_id"], capture["gift"], capture["in_box"]) == (
        codec_key(hatched), "gift_daycare", True, False)


def test_a_qualified_direct_gift_is_published_as_a_gift_capture_in_its_pack_area():
    """Card U1G: Bill's `givepoke EEVEE, 20` (crystal gifts.json BillsFamilysHouse row) reaches the server as ONE
    capture with gift=true in the row's area; the server trusts the flag (server/state.py capture handling)."""
    world = World()
    world.hello()
    world.field("wMapGroup", 11)
    world.field("wMapNumber", 6)
    world.field("wScriptBank", 21)
    world.field("wScriptPos", 19461 + 5, 2)   # just past the givepoke command
    world.field("wCurPartySpecies", 133)
    world.fire("gift_begin")
    eevee = mon(species=133, dvs=0x5AAA, nickname=0x85)
    world.party([mon(), eevee])
    world.field("wCurPartyMon", 1)
    world.emu.regs["B"], world.emu.regs["F"] = 0, 0x80   # GivePoke.skip_nickname party branch: B = 0, Z set
    world.fire("gift_party_finalized")
    world.frames(1)
    (capture,) = world.sent("capture")
    assert (capture["key"], capture["area_id"], capture["gift"], capture["in_box"]) == (
        codec_key(eevee), "goldenrod_city", True, False)


@pytest.mark.parametrize("kind", ["roamer", "contest"])
def test_roamer_and_contest_link_under_the_adapter_namespaces(kind):
    world = World()
    world.hello()
    adapter = Gen2GSCAdapter("crystal")
    world.field("wBattleMode", 1)
    if kind == "roamer":
        raikou = mon(species=243, dvs=0x9AAA)
        world.field("wBattleType", 5)
        world.field("wCurPartySpecies", 243)
        world.party([mon(), raikou])
        world.fire("capture_party")
        world.fire("capture_party_finalized")
        expected = adapter.gift_link_area("legend_243", acquisition="roamer", species_id=243)
    else:
        world.box([mon(species=123, dvs=0x9AAA)])
        world.fire("contest_box_inserted")
        world.box([mon(species=123, dvs=0x9AAA, nickname=0x84)])
        world.fire("contest_box_finalized")
        expected = adapter.gift_link_area("national_park_contest", acquisition="contest", species_id=123)
    world.frames(1)
    (capture,) = world.sent("capture")
    assert capture["area_id"] == expected == {"roamer": "legend_243", "contest": "national_park_contest"}[kind]


@pytest.mark.parametrize("boundary", ["battle_end", "soft_reset"])
def test_a_capture_finalized_in_the_boundary_frame_is_published(boundary):
    world = World()
    world.hello()
    world.field("wBattleMode", 1)
    caught = mon(species=19, dvs=0x7AAA)
    world.fire("wild_ready")
    world.party([mon(), caught])
    world.fire("capture_party")
    world.fire("capture_party_finalized")
    world.fire(boundary)  # nothing drained between the capture and the boundary
    world.frames(1)
    assert [c["key"] for c in world.sent("capture")] == [codec_key(caught)]
    assert world.sent("no_catch") == []


def test_a_refused_catch_is_logged_once_and_later_signals_still_flow():
    world = World()
    world.hello()
    world.field("wBattleMode", 1)
    world.field("wBattleScriptFlags", 128)  # a scripted/static battle
    world.party([mon(), mon(species=243, dvs=0x7AAA)])
    for _ in range(2):
        world.fire("capture_party")
        world.fire("capture_party_finalized")
        world.frames(1)
    assert world.sent("capture") == []
    assert len([line for line in world.logs.values() if "scripted/static" in line]) == 1
    world.field("wBattleScriptFlags", 0)
    caught = mon(species=19, dvs=0x8AAA)
    world.party([mon(), mon(species=243, dvs=0x7AAA), caught])
    world.fire("capture_party")
    world.fire("capture_party_finalized")
    world.frames(1)
    assert [c["key"] for c in world.sent("capture")] == [codec_key(caught)]
    assert not any("STOPPED" in text for text in world.shown())


def test_a_faint_follows_the_fainting_record_not_its_slot():
    world = World()
    lead, pichu = mon(), mon(species=172, dvs=0x3AAA)
    world.party([lead, pichu])
    world.hello()
    world.field("wBattleMode", 1)
    world.field("wCurBattleMon", 1)
    world.fire("battle_faint")  # copy-back not landed yet
    world.frames(1)
    assert world.sent("faint") == []
    world.party([dict(pichu, hp=0), lead])  # the record moved before its HP 0 landed
    world.frames(1)
    assert [f["key"] for f in world.sent("faint")] == [codec_key(pichu)]


def test_each_poison_faint_carries_its_own_record():
    world = World()
    first, second, survivor = mon(hp=0), mon(species=172, dvs=0x3AAA, hp=0), mon(species=19, dvs=0x7AAA)
    world.party([first, second, survivor])
    world.hello()
    for slot in (0, 1):  # one DoPoisonStep pass, one site hit per fainting mon
        world.field("wCurPartyMon", slot)
        world.fire("poison_faint")
    world.frames(1)
    assert sorted(f["key"] for f in world.sent("faint")) == sorted([codec_key(first), codec_key(second)])


def test_an_evolution_publishes_key_change_and_a_cancelled_one_nothing():
    world = World()
    pikachu, bulbasaur = mon(), mon(species=1, dvs=0x3AAA)
    world.party([pikachu, bulbasaur])
    world.hello()
    # Slot 0 was B-cancelled: CancelEvolution returns to the master loop before the
    # species-list store, so no site fires and the record is unchanged.
    world.frames(5)
    assert world.sent("key_change") == []
    ivysaur = dict(bulbasaur, species=2)
    world.party([pikachu, ivysaur])
    world.field("wCurPartyMon", 1)
    species_list = world.profile["ram"]["wPartySpecies"] + 1
    world.emu.regs["A"], world.emu.regs["H"], world.emu.regs["L"] = 2, species_list >> 8, species_list & 255
    world.fire("evolution_species_published")
    world.frames(1)
    (change,) = world.sent("key_change")
    assert (change["old_key"], change["new_key"], change["reason"], change["new_species"]) == (
        codec_key(bulbasaur), codec_key(ivysaur), "evolution", 2)


# ── N3 review findings ──────────────────────────────────────────────────────────────────
def evolve_slot_one(world):
    """Bulbasaur in slot 1 evolves; A and H/L are set as BizHawk exposes them (singles)."""
    bulbasaur = mon(species=1, dvs=0x3AAA)
    world.party([mon(), bulbasaur])
    world.hello()
    world.party([mon(), dict(bulbasaur, species=2)])
    world.field("wCurPartyMon", 1)
    species_list = world.profile["ram"]["wPartySpecies"] + 1
    world.emu.regs["A"], world.emu.regs["H"], world.emu.regs["L"] = 2, species_list >> 8, species_list & 255
    world.fire("evolution_species_published")
    world.frames(1)


def test_the_evolution_hl_guard_reads_bizhawk_single_registers():
    def check(world):
        evolve_slot_one(world)
        assert [c["new_species"] for c in world.sent("key_change")] == [2]
        assert not any("STOPPED" in text for text in world.shown())

    # the pre-N3 binder asked the emulator for the pair "HL", which BizHawk does not have
    falsify(check, mutant("lua/gen2/signals.lua", (
        "if pair then return register(pair[1])*256+register(pair[2]) end", "")))


def test_a_failed_deposit_does_not_swallow_the_next_one():
    def check(world):
        lead, pichu = mon(), mon(species=172, dvs=0x3AAA)
        world.party([lead, pichu])
        world.hello()
        world.field("wCurPartyMon", 1)
        world.fire("pc_deposit_begin")  # .BoxFull: the completion site is skipped
        world.frames(1)
        world.fire("pc_deposit_begin")  # box changed, same mon deposited again
        world.party([lead])
        world.box([pichu])
        world.fire("pc_deposit_complete")
        world.frames(1)
        assert [m["key"] for m in world.sent("party_to_box")] == [codec_key(pichu)]

    falsify(check, mutant("lua/gen2/signals.lua", ("if latches[name] and SUPERSEDES[name] then", "if false then")))


def wild_battle(world):
    world.hello()
    world.reply({"cmd": "resolved_areas", "areas": []})
    world.frames(1)
    world.field("wBattleMode", 1)
    world.fire("wild_ready")


def end_battle(world):
    world.fire("battle_end")
    world.field("wBattleMode", 0)
    world.frames(1)


def test_a_refused_capture_never_becomes_a_no_catch():
    def check(world):
        wild_battle(world)
        world.party([mon(), mon()])  # caught record identical to the lead: the binder refuses
        world.fire("capture_party")
        world.fire("capture_party_finalized")
        world.frames(1)
        end_battle(world)
        assert world.sent("capture") == [] and world.sent("no_catch") == []
        assert any("no_catch withheld, route_29 stays open" in line for line in world.logs.values())
        assert "show:CATCH NOT REPORTED - SEE LOG" in world.shown()

    falsify(check, mutant("lua/gen2/client.lua", (
        "if b.capture_refused or ev.refused_acquisitions ~= b.refused_base then", "if false then")))


def test_the_new_encounter_banner_puts_the_area_on_its_own_line():
    world = World()
    wild_battle(world)
    world.frames(1)
    banners = [s for s in world.shown() if "NEW ENCOUNTER" in s]
    assert len(banners) == 1 and banners[0].startswith("show:** NEW ENCOUNTER **\n")
    assert banners[0] != "show:** NEW ENCOUNTER **\n"


def test_a_missed_throw_still_ends_as_no_catch():
    world = World()
    wild_battle(world)
    world.fire("capture_party_finalized")  # the ball missed: same RET, no insertion
    world.frames(1)
    end_battle(world)
    assert [n["area_id"] for n in world.sent("no_catch")] == ["route_29"]


def test_no_event_reaches_the_server_before_the_hello():
    def check(world):
        world.party([mon(hp=0), mon(species=172, dvs=0x3AAA)])
        world.frames(120)
        world.field("wCurPartyMon", 0)
        world.fire("poison_faint")
        world.frames(1)
        assert world.sent() == []
        world.hello()
        assert [m["event"] for m in world.sent()][:2] == ["hello", "faint"]
        assert world.sent("faint")[0]["key"] == codec_key(mon(hp=0))

    falsify(check, mutant("lua/gen2/client.lua", (
        'if event ~= "hello" and not (self.hello_session and self.hello_session:status().ready) then',
        "if false then")))


def test_held_messages_do_not_follow_an_identity_change():
    """R3-4 A: a faint held for save A (OT 0x1234) never reaches save B's (OT 0x5678) session."""
    def check(world):
        world.party([mon(hp=0), mon(species=172, dvs=0x3AAA)])
        world.field("wCurPartyMon", 0)
        world.fire("poison_faint")
        world.frames(1)
        assert world.sent() == []
        world.field("wPlayerID", 0x5678, 2)  # another save, no frame-counter jump
        world.party([mon(ot=0x5678), mon(species=172, dvs=0x3AAA, ot=0x5678)])
        hello = world.hello()
        assert hello["ot_id"] == 0x5678 and [m["event"] for m in world.sent()] == ["hello"]

    falsify(check, mutant("lua/gen2/client.lua", ('drop_held("identity change")', "")))


@pytest.mark.parametrize("jump", [-60, 600])
def test_held_messages_do_not_replay_after_a_savestate_load(jump):
    """R3-4 B: a savestate load (the frame counter steps other than +1, back or forward) drops them."""
    def check(world):
        world.frames(100)  # the counter must stay nonnegative after the rewind
        world.party([mon(hp=0), mon(species=172, dvs=0x3AAA)])
        world.field("wCurPartyMon", 0)
        world.fire("poison_faint")
        world.frames(1)
        world.emu.frame += jump
        world.party([mon(), mon(species=172, dvs=0x3AAA)])  # the loaded state has no faint
        world.hello()
        assert [m["event"] for m in world.sent()] == ["hello"]

    falsify(check, mutant("lua/gen2/client.lua", ('self:abandon_timeline("savestate load")', "")))


def test_a_full_pre_hello_queue_is_shown_on_the_hud_once():
    """R3-7: the 65th held message is lost; the player sees it, once per filled queue."""
    def check(world):
        for n in range(66):
            world.client.send("faint", world.lua.table(key=f"k{n}"))
        assert world.sent() == []
        assert sum("SLINK EVENTS LOST" in text for text in world.shown()) == 1

    falsify(check, mutant("lua/gen2/client.lua", ('hud.show("SLINK EVENTS LOST - SEE LOG", 255, 64, 64, 600)', "")))


def test_a_reset_drops_messages_held_before_the_hello():
    world = World()
    world.party([mon(hp=0), mon(species=172, dvs=0x3AAA)])
    world.fire("poison_faint")
    world.frames(1)
    world.fire("soft_reset")
    world.frames(1)
    world.party([mon(), mon(species=172, dvs=0x3AAA)])  # the reloaded save
    world.hello()
    assert [m["event"] for m in world.sent()] == ["hello"]


# ── R4 S2: a savestate load/rewind abandons the timeline; a natural boundary does not ────────────
def _pump_fails_once(world):
    calls = {"n": 0}

    def pump():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("socket pump failed")
    world.net.pump = pump


@pytest.mark.parametrize("jump", [-60, 600])
def test_a_capture_queued_before_a_rewind_is_never_published(jump):
    """A pump error leaves a finalized capture queued in the binder; the player then loads a state."""
    def check(world):
        world.hello()
        world.frames(100)
        world.field("wBattleMode", 1)
        world.fire("wild_ready")
        world.party([mon(), mon(species=19, dvs=0x7AAA)])
        world.fire("capture_party")
        world.fire("capture_party_finalized")
        _pump_fails_once(world)
        with pytest.raises(Exception, match="socket pump failed"):
            world.frames(1)
        world.emu.frame += jump
        world.field("wBattleMode", 0)
        world.party([mon()])  # the loaded state never caught anything
        world.frames(3)
        assert world.sent("capture") == [] and world.sent("no_catch") == []

    falsify(check, mutant("lua/gen2/signals.lua", ("        count = count+#service:drain()\n",
                                                   "        for _,b in ipairs(service:drain()) do carried[#carried+1] = b end\n")))


def test_pending_faint_and_acquisition_latches_do_not_survive_a_rewind():
    def check(world):
        world.hello()
        world.frames(100)
        world.field("wBattleMode", 1)
        world.fire("battle_faint")  # copy-back not landed: the faint latch waits for HP 0
        world.party([mon(), mon(species=19, dvs=0x7AAA)])
        world.fire("capture_party")  # insertion latch, never finalized on this timeline
        world.frames(1)
        world.emu.frame -= 60
        world.frames(1)
        world.party([mon(hp=0), mon(species=19, dvs=0x7AAA)])  # the resumed timeline
        world.fire("capture_party_finalized")
        world.frames(40)
        assert world.sent("faint") == [] and world.sent("capture") == []

    falsify(check, mutant("lua/gen2/client.lua", (
        "        if self.signals then self.signals:abandon(why) end\n"
        "        self.faint_latches, self.deferred = {}, {}\n"
        "        self.battle, self.pending_safe, self.pending_rescan = nil, false, true\n", "")))


def test_rewind_cancels_a_deferred_faint_from_the_abandoned_timeline():
    world = World()
    lead, bench = mon(), mon(species=172, dvs=0x3AAA)
    world.party([lead, bench])
    world.hello()
    world.frames(60)  # a live validation arms the model writer
    world.checkpoint_ok = False
    world.reply({"cmd": "force_faint", "key": codec_key(bench), "nickname": "PICHU"})
    world.frames(1)
    assert world.hp_of(1) == (30, 0) and world.written() == []

    world.emu.frame -= 60  # load an earlier frame with the same party key
    world.checkpoint_ok = True
    world.frames(1)
    assert world.hp_of(1) == (30, 0) and world.written() == []


def test_a_write_refused_after_the_gate_is_logged_and_consumed():
    """When the checkpoint gate (safety.check) passes but the write itself refuses,
    the refusal is logged once, no bytes are written, and the deferred command is
    consumed (not re-queued). This pins current behavior—benign in production because
    the gate and the write re-check run in the same frame (OMP review O13)."""
    world = World()
    lead, bench = mon(), mon(species=172, dvs=0x3AAA)
    world.party([lead, bench])
    world.hello()
    world.frames(60)  # a live validation arms the model writer
    world.checkpoint_ok = True  # gate passes
    world.owned = lambda op, slot: False  # but the write refuses
    world.reply({"cmd": "force_faint", "key": codec_key(bench), "nickname": "PICHU"})
    world.frames(1)  # run_deferred is called; the gate passes but arm() refuses
    # The command is consumed, even though the write was refused.
    assert len(world.client.deferred) == 0
    assert world.written() == []
    # The refusal is logged.
    assert any("refused by the write gate" in line for line in world.logs.values())
    # The HUD shows the refusal message.
    assert any("KO refused" in text for text in world.shown())


def test_rewind_cancels_a_safe_reply_from_the_abandoned_battle():
    world = World()
    world.hello()
    world.frames(100)
    world.field("wBattleMode", 1)
    world.fire("battle_end")
    world.frames(1)  # battle_end queued a safe reply, but the old battle still reads as active
    assert world.sent("safe") == []

    world.emu.frame -= 60
    world.field("wBattleMode", 0)  # the loaded state is back in the overworld
    world.frames(1)
    assert world.sent("safe") == []


def test_rewind_discards_old_key_alias_before_a_delayed_faint_command():
    def check(world):
        old = mon(species=1, dvs=0x3AAA)
        new = dict(old, species=2)
        evolve_slot_one(world)
        world.frames(60)
        world.reply({"cmd": "key_change_rejected", "old_key": codec_key(old),
                     "new_key": codec_key(new), "reason": "collision"})
        world.frames(1)
        world.emu.frame -= 60
        world.frames(1)  # the loaded state retains the evolved mon but not the old key's alias
        world.reply({"cmd": "force_faint", "key": codec_key(old)})
        world.frames(1)
        # O-30 (A2): the death still lands, but through the live-party evolved-identity proof
        # (unique DV/OT, descendant species), never through the stale alias
        assert world.hp_of(1) == (0, 0)
        assert any("force_faint matched evolved" in line for line in world.logs.values())

    falsify(check, mutant("lua/gen2/client.lua", (
        "        -- A delayed retirement may be lost after rewinding a key change; retaining its alias could faint another record.\n"
        "        self.key_alias, self.retired_alias = nil, {}\n",
        "        -- A delayed retirement may be lost after rewinding a key change; retaining its alias could faint another record.\n")))


def test_rewind_refreshes_box_snapshot_before_the_next_tick():
    def check(world):
        old, new = mon(species=133, dvs=0x4AAA), mon(species=172, dvs=0x5AAA)
        world.box([old])
        assert [e["key"] for e in world.hello()["pc_boxes"]] == [codec_key(old)]
        world.frames(100)
        world.emu.frame -= 60
        world.box([new])  # the restored cartridge has a different active-box record
        world.frames(31)
        assert [e["key"] for e in world.sent("tick")[-1]["pc_boxes"]] == [codec_key(new)]

    falsify(check, mutant("lua/gen2/client.lua", (
        "        self.battle, self.pending_safe, self.pending_rescan = nil, false, true\n",
        "        self.battle, self.pending_safe, self.pending_rescan = nil, false, false\n")))


# ── N12b: a scripted static finalizes at the wild capture sites; the static pack names it ─────
def static_battle(world, group, number, battle_type, flags=128):
    world.hello()
    world.field("wBattleMode", 1)
    world.field("wMapGroup", group)
    world.field("wMapNumber", number)
    world.field("wBattleType", battle_type)
    world.field("wBattleScriptFlags", flags)  # Script_loadwildmon writes bit 7


def static_party_catch(world, group, number, species, battle_type, flags=128):
    static_battle(world, group, number, battle_type, flags)
    world.party([mon(), mon(species=species, dvs=0x7AAA)])
    world.fire("capture_party")
    world.fire("capture_party_finalized")
    world.frames(1)
    return [(c["key"], c["area_id"], c["gift"]) for c in world.sent("capture")]


@pytest.mark.parametrize("title,group,number,species,battle_type,flags,area", [
    ("crystal", 3, 4, 245, 12, 128, "legend_245"),  # Tin Tower Suicune, SUICUNE (O-21)
    ("crystal", 9, 6, 130, 7, 128, "static_lake_of_rage_130"),  # red Gyarados, FORCESHINY
    ("crystal", 10, 3, 185, 0, 128, "static_route_36_185"),  # Sudowoodo, NORMAL
    ("crystal", 3, 49, 100, 9, 128, "static_team_rocket_base_b1f_100"),  # Rocket B1F Voltorb trap, TRAP
    ("crystal", 12, 3, 143, 10, 128, "static_vermilion_city_143"),  # Snorlax, FORCEITEM
    ("crystal", 3, 52, 251, 11, 128, "static_ilex_forest_251"),  # Celebi, CelebiEvent_SetBattleType
    ("crystal", 3, 39, 131, 0, 128, "static_union_cave_b2f_131"),  # Lapras, NORMAL
    ("gold", 15, 12, 250, 10, 128, "static_tin_tower_roof_250"),  # Gold's Ho-Oh beside its unselected Silver twin
    ("crystal", 24, 3, 19, 0, 0, "route_29"),  # control: a natural wild Rattata is unchanged
])
def test_a_scripted_static_capture_is_published_under_its_pack_area(title, group, number, species,
                                                                    battle_type, flags, area):
    world = World(title)
    assert static_party_catch(world, group, number, species, battle_type, flags) == [
        (codec_key(mon(species=species, dvs=0x7AAA)), area, False)]
    # Gen 1 canon (O-3), pack-owned since gen2-static-canon: a static is its own gift area
    # (static_<lowercase map constant>_<species>, or its O-21 legend namespace), never the
    # route's ordinary area -- and keyed by the map CONSTANT, so the same static in Crystal,
    # Gold and Silver is one area for a pair (O-16)
    adapter = Gen2GSCAdapter(title)
    assert adapter.is_gift_area(area) == bool(flags) and (not flags or adapter.gift_link_area(area) == area)


@pytest.mark.parametrize("title,group,number,species,battle_type", [
    ("crystal", 24, 3, 19, 3),  # Route 29 catching tutorial
    ("crystal", 9, 6, 245, 12),  # no static row for this map/species
    ("crystal", 9, 6, 130, 0),  # the red Gyarados row is FORCESHINY, not NORMAL
    ("gold", 3, 14, 244, 0),  # Gold's source_unused Burned Tower Entei
])
def test_an_unqualified_scripted_static_stays_refused(title, group, number, species, battle_type):
    world = World(title)
    assert static_party_catch(world, group, number, species, battle_type) == []
    assert any("scripted/static" in line for line in world.logs.values())


def test_an_unselected_version_row_alone_never_publishes():
    world = World("gold")
    for row in world.parts.data.statics.encounters.values():
        if row.script == "TinTowerHoOh":  # leave only the selected:false Silver twin
            row.applicability.selected = False
    assert static_party_catch(world, 15, 12, 250, 10) == []


@pytest.mark.parametrize("prior", [20, 19])
def test_a_box_full_static_never_claims_the_mon_already_first_in_the_box(prior):
    world = World()
    static_battle(world, 3, 50, 0)  # a B2F Electrode
    world.party([mon(dvs=0x1000 + n) for n in range(6)])  # party full: .SendToPC
    old = [mon(species=101, dvs=0x2000 + n) for n in range(prior)]
    caught = mon(species=101, dvs=0x7AAA)
    world.ram("wEnemyMonDVs", [0x7A, 0xAA])
    # SendMonIntoBox .full leaves the box untouched; otherwise the new record is first
    world.box(old if prior == 20 else [caught, *old])
    world.fire("capture_box")
    world.fire("capture_box_finalized")
    world.frames(1)
    captures = [(c["key"], c["area_id"], c["in_box"]) for c in world.sent("capture")]
    assert captures == ([] if prior == 20 else [(codec_key(caught), "static_team_rocket_base_b2f_101", True)])


@pytest.mark.parametrize("title", ["crystal", "gold", "silver"])
def test_the_adapter_accepts_every_static_area_the_binder_can_publish(title):
    """The zone rule of lua/gen2/signals.lua final_event over every publishable pack row:
    the binder publishes the row's own static_area_id (gen2-static-canon), and the adapter's
    membership set must accept exactly those -- no id is recomputed on either side."""
    adapter = Gen2GSCAdapter(title)
    rows = json.loads((ROOT / f"data/games/gen2_{title}/static_encounters.json").read_text())["encounters"]
    published = {row["static_area_id"] for row in rows
                 if row["applicability"]["selected"] and not row["source_unused"]
                 and row["kind"] != "tutorial"}
    assert published, "the pack must publish at least one static area"
    for zone in published:
        assert adapter.is_gift_area(zone) and adapter.gift_link_area(zone) == zone, (title, zone)
    # The retired group*256+number shape is refused outright, gift-shaped or not.
    assert not adapter.is_gift_area(f"static_{rows[0]['map_group'] * 256 + rows[0]['map_number']}_{rows[0]['species']}")


# ── production graph (card U3): Entry.build over the admitted Crystal and its PHYSICAL receipts ─────
RECEIPTS = ROOT / "tests/fixtures/gen2/receipts"


def production(title="crystal", swaps=None, files=None):
    return World(title, swaps=swaps, production=True, files=files)


def falsify_production(check, swaps):
    """The rule holds on the shipped production graph and fails on the broken variant."""
    check(production())
    with pytest.raises(AssertionError):
        check(production(swaps=swaps))


@pytest.mark.parametrize("title", ["crystal", "gold", "silver"])
@NEEDS_OVERLAY
def test_production_registers_exactly_the_u1_proven_sites_and_the_u2_kinds(title):
    world = production(title)
    parts = world.parts
    assert parts.production_admitted is True and parts.qualification == "PHYSICAL_RECEIPTED"
    assert parts.title == title and parts.data.admission.gate.state == "ADMITTED"
    receipt = json.loads((RECEIPTS / f"{title}.engine_sites.json").read_text())
    # card U1G: a v2 receipt proves the union of its runs
    proven = sorted({name for run in receipt.get("runs", [receipt]) for name in run["proven"]})
    status = world.client.signals.status(world.client.signals)
    assert status.evidence_level == "PHYSICAL" and status.runtime_authorized is True
    assert sorted(status.registered_sites.values()) == sorted(proven)
    assert sorted(parts.write_scope.kinds.keys()) == ["backing_box", "battle_bench", "battle_faint", "box_deposit",
                                                      "box_withdraw", "party_collection", "party_hp"]
    pc = CHECKPOINT[title]["primary"]["execution_before"]["pc"]
    assert world.emu.callbacks["SLink-gen2-checkpoint"].addr == pc
    assert len(world.hello()["party"]) == 1  # the title's own checkpoint hold arms the hello
    world.client.stop(world.client)
    assert world.emu.callbacks["SLink-gen2-checkpoint"] is None


@NEEDS_OVERLAY
def test_production_no_hello_before_an_accepted_checkpoint_hold():
    def check(world):
        world.frames(120)
        assert world.sent("hello") == []
        assert world.hold(wScriptRunning=1) == 1  # a script frame at the PC: the predicate refuses
        world.frames(2)
        assert world.sent("hello") == []
        world.hold()
        world.frames(1)
        assert len(world.sent("hello")) == 1

    falsify_production(check, mutant("lua/gen2/client.lua", (
        "if battle.mode == 0 and not (self.checkpoint_held or safety.check(PARTY_HP)) then", "if false then")))


@NEEDS_OVERLAY
def test_production_refuses_an_unarmed_or_unheld_write():
    def check(world):
        attempt = world.lua.eval("""function(w, arm) return pcall(function()
            if arm then w:arm('overworld') end
            return w:faint_party_slot(0, {mode=0, link_mode=0}) end) end""")
        ok, err = attempt(world.parts.writes, False)
        assert ok is False and "no armed write window" in err
        result = attempt(world.parts.writes, True)  # armed, but the CPU is not held at the checkpoint
        world.parts.writes.disarm(world.parts.writes)
        assert isinstance(result, tuple) and result[0] is False and "ownership refused" in result[1]
        assert world.written() == []

    falsify_production(check, mutant("lua/gen2/entry.lua", (
        "return kind ~= nil and checkpoint:check(kind) == true", "return true")))


@NEEDS_OVERLAY
def test_production_bench_faint_lands_only_inside_the_checkpoint_hold():
    world = production()
    bench = mon(species=172, dvs=0x3AAA)
    world.party([mon(), bench])
    world.hello()
    world.frames(60)  # a live validation enables writes
    world.reply({"cmd": "force_faint", "key": codec_key(bench), "nickname": "PICHU"})
    world.frames(130)
    assert world.written() == [] and world.hp_of(1) == (30, 0)
    world.hold()
    assert world.hp_of(1) == (0, 0) and world.hp_of(0) == (30, 0)
    receipts = [dict(r.items()) for r in world.parts.writes.log.values()]
    assert {r["site"] for r in receipts} == {"lua/gen2/entry.lua production"}
    assert "show:!! PICHU KO'd" in world.shown()


def production_battle_hold(world, caller=True):
    """Production: the CPU at the pack battle hold (before `call DetermineMoveOrder`) on StartBattle's stack."""
    hold = HOLDS[world.title]["battle_hold"]
    stack = hold["caller_stack"]
    sp = stack["minimum_sp"] + 16
    word = stack["required_words"][0]["value"] + (0 if caller else 1)
    world.emu.poke("System Bus", sp, world.lua.table_from([word & 255, word >> 8]))
    world.emu.regs.SP = sp
    hit = world.emu.fire(hold["execution_before"]["bank"], hold["execution_before"]["pc"])
    world.emu.regs.PC = 0
    return hit


@NEEDS_OVERLAY
def test_production_active_death_lands_only_inside_the_battle_hold():
    """O-30: behind the PHYSICAL battle_faint receipt the production graph hooks the battle hold; the write lands
    only when the held evaluation accepts it (here: the StartBattle caller word), never elsewhere in battle."""
    world = production()
    active = mon()
    world.party([active, mon(species=172, dvs=0x3AAA)])
    world.hello()
    world.frames(60)
    world.field("wBattleMode", 1)
    world.field("wCurBattleMon", 0)
    world.emu.poke("System Bus", hold_ram(world, "wBattleMonSpecies"), world.lua.table_from([active["species"]]))
    world.emu.poke("System Bus", hold_ram(world, "wBattleMonHP"), world.lua.table_from([0, 30]))
    world.reply({"cmd": "force_faint", "key": codec_key(active), "nickname": "PIKA"})
    world.frames(3)
    assert production_battle_hold(world, caller=False) == 1          # another caller: refused, nothing moves
    assert world.written() == [] and battle_hp(world) == 30
    production_battle_hold(world)
    assert battle_hp(world) == 0 and world.hp_of(0) == (0, 0) and action(world) == 1
    assert {r["site"] for r in world.parts.writes.log.values()} == {"lua/gen2/entry.lua production"}
    assert "show:!! PIKA KO'd" in world.shown()


@NEEDS_OVERLAY
def test_production_box_mon_lands_only_inside_the_checkpoint_hold():
    world = production()
    lead, pichu = mon(), mon(species=172, dvs=0x3AAA)
    world.party([lead, pichu])
    world.hello()
    world.frames(60)
    world.reply({"cmd": "box_mon", "key": codec_key(pichu)})
    world.frames(130)
    assert world.written() == [] and world.sent("box_mon_failed") == []
    world.hold()
    assert world.sent("box_mon_failed") == [] and [m["key"] for m in world.sent("stats_cache")] == [codec_key(pichu)]
    flat = world.profile["derived"]["active_box_flat"]
    assert [world.io.read_u8(flat + i, "CartRAM") for i in range(3)] == [1, 172, 255]
    assert world.io.read_u8(world.profile["ram"]["wPartyCount"]) == 1
    receipts = {r["site"] for r in world.parts.writes.log.values()}
    assert receipts == {"lua/gen2/entry.lua production"}


@NEEDS_OVERLAY
def test_production_refuses_what_the_receipts_do_not_cover():
    """A receipt without its box runs (the pre-BOX schema) proves only party_hp + box_deposit: every box
    command NACKs at the hold with the missing kind, and no byte moves."""
    receipt = json.loads((ROOT / "data/games/gen2_crystal/receipts/overlay/crystal.write_window.json").read_text())
    for mode in ("boxes", "boxes_reset", "boxes_reload", "battle_faint", "battle_bench"):
        del receipt["runs"][mode]
    world = production(files={"/receipts/overlay/crystal.write_window.json": json.dumps(receipt)})
    active = mon()
    world.party([active, mon(species=172, dvs=0x3AAA)])
    world.hello()
    world.frames(60)
    world.reply({"cmd": "box_mon", "key": codec_key(active)})
    world.field("wBattleMode", 1)
    world.reply({"cmd": "force_faint", "key": codec_key(active), "nickname": "PIKA"})
    world.frames(2)
    assert world.sent("box_mon_failed") == []            # box commands wait for the checkpoint hold
    world.field("wBattleMode", 0)
    world.hold()
    (nack,) = world.sent("box_mon_failed")
    assert "unproven write kind party_collection" in nack["reason"]
    assert any(text.startswith("show:KO held") for text in world.shown()) and world.written() == []


@NEEDS_OVERLAY
def test_production_key_agrees_with_the_python_codec_on_the_same_record():
    def check(world):
        starter, caught = mon(species=155, ot=0x0BCD, dvs=0xFEDC), mon(species=19, ot=0x0BCD, dvs=0x1357)
        world.party([starter])
        assert world.hello()["party"][0]["key"] == codec_key(starter)
        world.field("wBattleMode", 1)
        world.fire("wild_ready")
        world.party([starter, caught])
        world.fire("capture_party")
        world.fire("capture_party_finalized")
        world.frames(1)
        assert [c["key"] for c in world.sent("capture")] == [codec_key(caught)]

    falsify_production(check, mutant("lua/gen2/wire.lua", (
        'string.format("%04X:%04X:%02X", mon.dv_word, mon.ot_id, mon.species_id)',
        'string.format("%04X:%04X:%02X", mon.ot_id, mon.dv_word, mon.species_id)')))


@pytest.mark.parametrize("title,name,other", [
    ("silver", "silver.engine_sites.json", "gold.engine_sites.json"),  # U1 is per title
    ("gold", "gold.engine_sites.json", "silver.engine_sites.json"),
    ("crystal", "crystal.write_window.json", "gold.write_window.json"),  # only Silver follows Gold's U2
])
@NEEDS_OVERLAY
def test_each_title_admits_only_with_its_own_receipts(title, name, other):
    with pytest.raises(Refused, match="PHYSICAL proof refused"):
        production(title, files={f"/receipts/overlay/{name}": (RECEIPTS / "overlay" / other).read_text()})


@pytest.mark.parametrize("name,path,value", [
    ("crystal.engine_sites.json", ("runs", 0, "evidence_level"), "MODEL"),
    ("crystal.engine_sites.json", ("runs", 1, "synth", "sha256"), "0" * 64),   # card U1G: a forged disclosure
    ("crystal.write_window.json", ("runs", "town", "evidence_level"), "MODEL"),
    ("crystal_battle.qualification.json", ("fixtures", 0, "artifacts", "fixture", "sha256"), "0" * 64),
])
@NEEDS_OVERLAY
def test_a_forged_or_model_receipt_refuses_production(name, path, value):
    receipt = json.loads((ROOT / "data/games/gen2_crystal/receipts/overlay" / name).read_text())
    node = receipt
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    with pytest.raises(Refused, match="PHYSICAL proof refused"):
        production(files={f"/receipts/overlay/{name}": json.dumps(receipt)})


@pytest.mark.parametrize("title", ["crystal", "gold", "silver"])
def test_a_clean_gen2_cartridge_composes_no_client_and_says_why_it_needs_the_companion(title):
    """Patch-first (owner 2026-10-02): the companion overlay is REQUIRED for every Gen 2 title,
    so lua/gen2/entry.lua refuses the shipped clean build BEFORE it composes anything. This is the
    replacement for the production tests above, which used to run on exactly this cartridge: the
    rule they can no longer exercise is stated here, and they wait (NEEDS_OVERLAY) for a legal
    overlay row rather than pinning the clean one."""
    with pytest.raises(Refused) as caught:
        World(title, production=True, clean=True)
    reason = str(caught.value)
    assert f"this {title} cartridge needs the SLink companion patch" in reason
    assert "Manager or /patcher" in reason


RUN_HOST = r"""
return function(rom, root, pc_log)
    local frames, callbacks = {}, {}
    memory = {
        read_u8 = function(a, d) if d == "ROM" then return rom:byte(a + 1) or 0 end return 0 end,
        write_u8 = function() end,
        getmemorydomainsize = function(d) return d == "ROM" and #rom or 0x10000 end,
        getmemorydomainlist = function() return {"ROM", "System Bus", "CartRAM"} end,
    }
    emu = {framecount = function() return 1 end, getregister = function() return 0 end}
    event = {
        onframeend = function(fn) frames[#frames + 1] = fn end,
        onexit = function() end,
        on_bus_exec = function(fn, addr, name) callbacks[name] = addr; return name end,
        unregisterbyid = function() return true end,
    }
    console = {log = function(t) pc_log[#pc_log + 1] = t end}
    package.loaded.connector = {init = function() end, send = function() end, receive = function() end,
                                pump = function() end, connected = function() return false end}
    package.loaded.hud = {init = function() end, render = function() end, show = function() end}
    SLINK_GEN2_CLIENT, SLINK_GEN2_PARTS = "stale", "stale"
    dofile(root .. "/lua/gen2/run.lua")
    return frames, callbacks
end
"""


@pytest.mark.parametrize("title,artifact", [("crystal", None), ("gold", None), ("silver", None),
                                            ("crystal", "pokecrystal11")])
def test_run_lua_exposes_the_production_client_only_for_an_admitted_cartridge(title, artifact):
    """The H1 duo driver's contract: SLINK_GEN2_CLIENT / SLINK_GEN2_PARTS (production_admitted),
    the client's own onframeend tick; nil for a refused cartridge. Patch-first (owner 2026-10-02)
    moved BOTH refusals onto the launcher: a clean cartridge (the release build, `artifact` None)
    now needs the SLink companion, and Crystal 1.1 stays refused as BUILD_ONLY."""
    profile = json.loads((ROOT / f"data/games/gen2_{title}/profile.json").read_text())["titles"][title]
    repo = "pokecrystal" if title == "crystal" else "pokegold"
    rom = (ROOT / f".cache/gen2-build/{repo}/{artifact or profile['artifact']}.gbc").read_bytes()
    lua = LuaRuntime(unpack_returned_tuples=True)
    logs = lua.table()
    frames, callbacks = lua.execute(RUN_HOST)(rom, ROOT.as_posix(), logs)
    g = lua.globals()
    assert g.SLINK_GEN2_CLIENT is None and g.SLINK_GEN2_PARTS is None and len(frames) == 0
    refusals = [line for line in logs.values() if "refused" in line]
    assert refusals, dict(logs)
    if artifact is None:
        # the clean release build: refused for want of the companion, by name
        assert f"this {title} cartridge" in refusals[0] and "needs the SLink companion patch" in refusals[0]
    else:
        assert "BUILD_ONLY" in refusals[0]


# ── card BOX: box_mon / party_mon / memorialize through the composed executor (MODEL candidate) ───────
BOX_KINDS = {"party_hp", "box_deposit", "party_collection", "box_withdraw", "backing_box"}


def box_world(party, box=(), covered=BOX_KINDS):
    world = World()
    world.covered = set(covered)
    world.field("wSavedAtLeastOnce", 1)
    world.party(list(party))
    world.box(list(box))
    world.hello()
    world.frames(60)   # a live validation enables writes
    world.checkpoint_ok = False
    return world


def storage(world, index):
    flat = world.profile["storage_boxes"][index]["flat"]
    return [world.io.read_u8(flat + i, "CartRAM") for i in range(1102)]


def active(world):
    flat = world.profile["derived"]["active_box_flat"]
    return [world.io.read_u8(flat + i, "CartRAM") for i in range(1102)]


def party_count(world):
    return world.io.read_u8(world.profile["ram"]["wPartyCount"])


def test_box_mon_deposits_at_the_checkpoint_after_stats_cache():
    lead, pichu = mon(), mon(species=172, dvs=0x3AAA)
    world = box_world([lead, pichu])
    world.reply({"cmd": "box_mon", "key": codec_key(pichu)})
    world.frames(1)
    assert party_count(world) == 2 and world.sent("stats_cache") == []   # nothing outside the checkpoint
    world.checkpoint_ok = True
    world.frames(1)
    (cache,) = world.sent("stats_cache")
    assert cache["key"] == codec_key(pichu) and world.sent("box_mon_failed") == []
    assert party_count(world) == 1 and active(world)[0:3] == [1, 172, 255]
    world.frames(30)
    assert [e["key"] for e in world.sent("tick")[-1]["pc_boxes"]] == [codec_key(pichu)]


def test_party_mon_withdraws_and_acks_sync_retrieve_done():
    lead, boxed = mon(), mon(species=19, dvs=0x7AAA)
    world = box_world([lead], [boxed])
    world.checkpoint_ok = True
    world.reply({"cmd": "party_mon", "key": codec_key(boxed)})
    world.frames(2)
    assert [m["key"] for m in world.sent("sync_retrieve_done")] == [codec_key(boxed)]
    assert party_count(world) == 2 and active(world)[0] == 0


def test_memorialize_moves_the_mon_into_box_14_and_acks_with_the_box():
    lead, dead = mon(), mon(species=19, dvs=0x7AAA, hp=0)
    world = box_world([lead, dead])
    world.checkpoint_ok = True
    world.reply({"cmd": "memorialize", "key": codec_key(dead)})
    world.frames(2)
    (done,) = world.sent("memorialize_done")
    assert done["key"] == codec_key(dead) and done["box"] == 13
    assert storage(world, 13)[0:3] == [1, 19, 255] and party_count(world) == 1


def test_a_repeated_memorialize_while_the_burial_waits_for_the_save_never_acks_early():
    """Final sweep ffd54b44 (gen2_pc_ops, both pairs): the dead partner sat in the ACTIVE box, so its removal is
    volatile until a native save (BOX-MEMORIAL-2) and the first memorialize parks a settle. The server RE-SENT the
    command while the player had not saved yet; the duplicate found the memorial copy and no source, which the box
    executor reports as a plain done, so the client acked memorialize_done three times BEFORE the save. The ack must
    wait for the save and be sent exactly once."""
    lead, dead = mon(), mon(species=19, dvs=0x7AAA)
    world = box_world([lead], [dead])                          # the dead mon is in the active (current) box
    world.checkpoint_ok = True
    for _ in range(4):                                         # the server's retries before the player saves
        world.reply({"cmd": "memorialize", "key": codec_key(dead)})
        world.frames(3)
    assert world.sent("memorialize_done") == [], "acked before the native save made the removal durable"
    assert world.sent("memorialize_failed") == []
    world.fire("save_completed")
    world.frames(6)
    (done,) = world.sent("memorialize_done")
    assert done["key"] == codec_key(dead) and done["box"] == 13
    world.reply({"cmd": "memorialize", "key": codec_key(dead)})  # a retry AFTER the settle is harmless and idempotent
    world.frames(3)
    assert len(world.sent("memorialize_done")) == 2   # re-acked on request (the lone durable copy is done)


def test_a_repeated_memorialize_also_matches_its_waiting_burial_by_the_servers_own_key():
    """Review M1 of c8db5a92: `phys` is re-derived per command (retired_alias), the waiting settle is parked under the
    physical key. If the alias is reset while the burial waits (a boundary resets it), the duplicate resolves to the
    server's OLD key, misses a phys-only guard, and the box executor refuses it ('key not in party or boxes'):
    memorialize_failed, which the server treats as done, so the same non-durable finalization returns through the
    failure branch."""
    lead, dead = mon(), mon(species=19, dvs=0x7AAA)
    old = "BEEF:0001:07"                                           # the key the server tracks (stale)
    world = box_world([lead], [dead])
    world.checkpoint_ok = True
    world.client.retired_alias[old] = codec_key(dead)
    world.reply({"cmd": "memorialize", "key": old})
    world.frames(3)
    world.client.retired_alias[old] = None                         # the alias is gone, the settle still waits
    world.reply({"cmd": "memorialize", "key": old})
    world.frames(3)
    assert world.sent("memorialize_failed") == [] and world.sent("memorialize_done") == []
    world.fire("save_completed")
    world.frames(6)
    (done,) = world.sent("memorialize_done")
    assert done["key"] == old and done["box"] == 13


def test_a_repeated_party_mon_inside_the_deferred_backing_window_never_deletes_the_durable_box_copy():
    """Review M2 of c8db5a92: a duplicate party_mon while the withdraw's backing removal waits for the save passed the
    box executor's same() check and committed plan_withdraw, deleting the DURABLE box copy before the save persisted the
    party; a reset in that window loses the mon. Every command is still acked (the mon IS in the party)."""
    lead, boxed = mon(), mon(species=19, dvs=0x7AAA)
    world = box_world([lead])
    flat = world.profile["storage_boxes"][4]["flat"]
    world.emu.poke("CartRAM", flat, world.lua.table_from(list(collection([boxed], 20, 32))))
    world.checkpoint_ok = True
    for _ in range(3):
        world.reply({"cmd": "party_mon", "key": codec_key(boxed)})
        world.frames(3)
    assert storage(world, 4)[0] == 1, "the durable box copy must stay until the native save"
    assert party_count(world) == 2
    assert [m["key"] for m in world.sent("sync_retrieve_done")] == [codec_key(boxed)] * 3
    world.fire("save_completed")
    world.frames(3)
    assert storage(world, 4)[0] == 0 and party_count(world) == 2


@pytest.mark.parametrize("state", ["present", "reset", "reload", "full", "unreadable", "ambiguous"])
def test_backing_withdrawal_retry_requires_a_current_party_copy(state):
    source = (ROOT / "lua/gen2/client.lua").read_text(encoding="utf-8")

    def method(signature):
        start = source.index("    " + signature)
        end = source.index("\n    end\n", start) + len("\n    end\n")
        return source[start:end]

    lua = LuaRuntime(unpack_returned_tuples=True)
    model = lua.execute(r"""
        local key = "7AAA:1234:13"
        local party = {mons={{slot=0, key="2AAA:1234:19"}}}
        local probe = {messages={}, calls=0, durable=true}
        local self = {
            epoch=0, settle={}, retired_alias={}, deferred={}, held={}, pending_battle_writes={},
            rival_close=function() end, trade_forget=function() end,
            hello_session={invalidate=function() end},
        }
        local function current_party() return party end
        local function mon_key(mon) return mon.key end
        local function nick_label(k) return k end
        local function log() end
        local function drop_held() end
        local function send(event, fields)
            probe.messages[#probe.messages + 1] = {event=event, key=fields.key, reason=fields.reason}
        end
        local hud = {show=function() end}
        local boxes = {}
        function boxes.withdraw(k, opts)
            assert(k == key and opts.defer_backing == true)
            probe.calls = probe.calls + 1
            for _, mon in ipairs(party.mons) do
                if mon.key == key then
                    probe.durable = false
                    return true
                end
            end
            if #party.mons >= 6 then return false, "party full" end
            party.mons[#party.mons + 1] = {slot=#party.mons, key=key}
            return true, "backing removal deferred to the save witness"
        end
    """ + method("function self:boundary(kind, why)") +
        method("local function find_party_slot(key, death)") +
        method("function self:run_box(cmd)") + r"""
        function probe.run()
            self:run_box({cmd="party_mon", key=key, full_budget=0})
        end
        function probe.restore(state)
            if state == "present" then return end
            self:boundary(state == "reload" and "reload" or "reset")
            if state == "unreadable" then party = nil
            elseif state == "ambiguous" then
                party = {mons={{slot=0, key=key}, {slot=1, key=key}}}
            elseif state == "full" then
                party = {mons={}}
                for i = 1, 6 do party.mons[i] = {slot=i-1, key="other-" .. i} end
            else party = {mons={{slot=0, key="2AAA:1234:19"}}} end
        end
        probe.client = self
        return probe
    """)
    model.run()
    assert model.calls == 1 and model.durable is True
    assert len(model.client.settle) == 1
    model.restore(state)
    model.run()
    messages = list(model.messages.values())
    assert model.durable is True
    assert model.calls == (2 if state in {"reset", "reload", "full"} else 1)
    assert len(model.client.settle) == (0 if state == "full" else 1)
    expected = "sync_retrieve_failed" if state in {"full", "unreadable", "ambiguous"} else "sync_retrieve_done"
    assert [message.event for message in messages] == ["sync_retrieve_done", expected]
    if expected == "sync_retrieve_failed":
        assert messages[-1].reason


def test_a_parked_burial_with_no_save_stays_visible_and_never_acks_however_long_it_waits():
    """Review m2: the policy is pinned: MAX_PENDING_FRAMES bounds faint latches, not a burial; it waits for the save."""
    lead, dead = mon(), mon(species=19, dvs=0x7AAA)
    world = box_world([lead], [dead])
    world.checkpoint_ok = True
    world.reply({"cmd": "memorialize", "key": codec_key(dead)})
    world.frames(3)
    world.frames(1300)                                             # more than 2 x Client.MAX_PENDING_FRAMES (600)
    assert world.sent("memorialize_done") == [] and world.sent("memorialize_failed") == []
    assert world.sent("tick")[-1].get("awaiting_save") is True


def test_a_repeated_memorialize_with_the_memorial_box_active_also_acks_exactly_once_after_the_save():
    """Review m3: the other half of BOX-MEMORIAL-2: the memorial copy lands in the ACTIVE box 14 (current box 13), so
    its durable source waits for the save; duplicates before the save must stay silent and one ack follows."""
    lead, dead = mon(), mon(species=19, dvs=0x7AAA)
    world = box_world([lead])
    flat = world.profile["storage_boxes"][4]["flat"]
    world.emu.poke("CartRAM", flat, world.lua.table_from(list(collection([dead], 20, 32))))
    world.field("wCurBox", 13)
    world.checkpoint_ok = True
    for _ in range(4):
        world.reply({"cmd": "memorialize", "key": codec_key(dead)})
        world.frames(3)
    assert world.sent("memorialize_done") == [] and world.sent("memorialize_failed") == []
    world.fire("save_completed")
    world.frames(6)
    if not world.sent("memorialize_done"):                         # a second witness if the removal also waited
        world.fire("save_completed")
        world.frames(6)
    (done,) = world.sent("memorialize_done")
    assert done["key"] == codec_key(dead) and done["box"] == 13


def test_memorialize_follows_the_dead_mon_through_its_evolution():
    """O-30: the linked mon evolved after its death (no Gen 2 key_change): the memorial still buries it
    and acks under the key the server tracks."""
    lead, dead = mon(), mon(species=1, dvs=0x7AAA, hp=0)
    world = box_world([lead, dict(dead, species=2)])
    world.checkpoint_ok = True
    world.reply({"cmd": "memorialize", "key": codec_key(dead)})
    world.frames(2)
    (done,) = world.sent("memorialize_done")
    assert done["key"] == codec_key(dead) and world.sent("memorialize_failed") == []
    assert storage(world, 13)[0:3] == [1, 2, 255] and party_count(world) == 1
    assert any("memorialize matched evolved" in line for line in world.logs.values())


def test_a_whiteout_rebuild_party_mon_waits_behind_the_memorial_that_frees_a_slot():
    """Gen 1 A5/rebuild rule: party full -> tail requeue (bounded); the memorialize queued after it frees
    the slot, then the retry lands. sync_retrieve_failed would be final at the server (state.py)."""
    six = [mon(species=20 + i, dvs=0x1000 * (i + 1)) for i in range(6)]
    boxed = mon(species=19, dvs=0x7AAA)
    world = box_world(six, [boxed])
    world.checkpoint_ok = True
    world.reply({"cmd": "party_mon", "key": codec_key(boxed)}, {"cmd": "memorialize", "key": codec_key(six[5])})
    world.frames(4)
    assert world.sent("sync_retrieve_failed") == []
    events = [m["event"] for m in world.sent() if m["event"] in ("memorialize_done", "sync_retrieve_done")]
    assert events == ["memorialize_done", "sync_retrieve_done"]


def test_a_genuinely_full_party_still_fails_the_retrieve():
    six = [mon(species=20 + i, dvs=0x1000 * (i + 1)) for i in range(6)]
    boxed = mon(species=19, dvs=0x7AAA)
    world = box_world(six, [boxed])
    world.checkpoint_ok = True
    world.reply({"cmd": "party_mon", "key": codec_key(boxed)})
    world.frames(5)
    (nack,) = world.sent("sync_retrieve_failed")
    assert nack["key"] == codec_key(boxed) and "party full" in nack["reason"]


def test_the_last_party_mon_memorial_waits_for_the_rebuild_or_drops_after_game_over():
    lead, boxed = mon(hp=0), mon(species=19, dvs=0x7AAA)
    world = box_world([lead], [boxed])
    world.checkpoint_ok = True
    world.reply({"cmd": "memorialize", "key": codec_key(lead)}, {"cmd": "party_mon", "key": codec_key(boxed)})
    world.frames(4)
    events = [m["event"] for m in world.sent() if m["event"] in ("memorialize_done", "sync_retrieve_done",
                                                                  "memorialize_failed")]
    assert events == ["sync_retrieve_done", "memorialize_done"]
    world = box_world([lead])
    world.checkpoint_ok = True
    world.reply({"cmd": "game_over"}, {"cmd": "memorialize", "key": codec_key(lead)})
    world.frames(4)
    assert world.sent("memorialize_done") == [] and world.sent("memorialize_failed") == []
    assert party_count(world) == 1


@pytest.mark.parametrize("cmd,nack", [("box_mon", "box_mon_failed"), ("party_mon", "sync_retrieve_failed"),
                                      ("memorialize", "memorialize_failed")])
def test_an_unproven_write_kind_is_nacked_with_the_kind_and_writes_nothing(cmd, nack):
    lead, other = mon(), mon(species=19, dvs=0x7AAA)
    world = box_world([lead] if cmd == "party_mon" else [lead, other], [other] if cmd == "party_mon" else [],
                      covered={"party_hp", "box_deposit"})
    world.checkpoint_ok = True
    world.reply({"cmd": cmd, "key": codec_key(other)})
    world.frames(2)
    (refusal,) = world.sent(nack)
    assert refusal["key"] == codec_key(other) and "party_collection" in refusal["reason"]
    assert world.written() == []


def test_one_hello_across_a_battle_whose_animations_switch_the_wram_bank():
    """gen2-hello-flap (LIVE2 C<->C reconnect: HELLO_AGAIN every 30-80 frames in a wild battle). Battle
    animations select SVBK = BANK(wBGPals1) = 5 (C engine/battle_anims/anim_commands.asm:1413,
    bg_effects.asm:2562,2589); a frame-end read then finds WRAMX unmapped. That is no identity change."""
    def check(world):
        world.hello()
        generation = world.client.hello_session.status(world.client.hello_session).generation
        world.field("wBattleMode", 1)
        for _ in range(4):
            world.emu.wram_bank = 5
            world.frames(1)
            world.emu.wram_bank = 1
            world.frames(3)
        assert len(world.sent("hello")) == 1
        assert world.client.hello_session.status(world.client.hello_session).generation == generation
        world.field("wPlayerID", 0x4321, 2)   # a real identity change still re-hellos
        world.frames(2)
        assert len(world.sent("hello")) == 2

    falsify(check, mutant("lua/gen2/client.lua", (
        "if io.bank_valid(profile.ram_bank.wPlayerID, profile.ram.wPlayerID, 1) ~= true then return end", "")))


def test_a_backing_box_withdraw_settles_its_box_copy_only_after_the_native_save():
    """gen2-box-durability F1: the backing slot is durable SRAM, the party is not until SAVE. The client acks
    the withdraw at once (the mon IS in the party) and removes the box copy only after save_completed."""
    lead, boxed = mon(), mon(species=19, dvs=0x7AAA)
    world = box_world([lead])
    flat = world.profile["storage_boxes"][4]["flat"]
    world.emu.poke("CartRAM", flat, world.lua.table_from(list(collection([boxed], 20, 32))))
    world.checkpoint_ok = True
    world.reply({"cmd": "party_mon", "key": codec_key(boxed)})
    world.frames(3)
    assert [m["key"] for m in world.sent("sync_retrieve_done")] == [codec_key(boxed)]
    assert party_count(world) == 2 and storage(world, 4)[0] == 1     # a duplicate until the save
    world.fire("save_completed")
    world.frames(3)
    assert storage(world, 4)[0] == 0 and party_count(world) == 2


# ── P4.3b: the native SLINK TRADE lease (lua/gen2/trade_overlay.lua + the client trade phases) ──────
# The cartridge side is a Python stand-in for patch/gen2/src/trade_service.asm (SOURCE at bd6c68b1):
# it publishes QUERY/OFFER, picks up at the two hook labels BEFORE writing the ACK, and publishes DONE.
# The trade block is built by tools/gen_gen2_profile.trade_block over the pinned overlay .sym plus the
# P4.3a labels (the published sym predates them), so the addresses are generator output, never literals.
import re  # noqa: E402

sys.path.insert(0, str(ROOT / "tools"))
import gen_gen2_profile  # noqa: E402
from rgbds_symbols import parse_symbols  # noqa: E402

CAP_TRADE = 0x10
TRADE_LABELS = {"crystal": (0x75, 0x4154, 0x4237, 0x4413), "gold": (0x13, 0x4154, 0x4237, 0x4413),
                "silver": (0x13, 0x4154, 0x4237, 0x4413)}
MAIL = 158  # FLOWER_MAIL: items.json mail_ids, all three packs


def base_sym(name):
    """The pinned overlay .sym without any SlinkTrade* row: a publication that already carries the P4.3a
    family (or not) gives the same fixture, and the labels below are appended exactly once."""
    text = (ROOT / "data/gen2" / name).read_text()
    return "".join(line for line in text.splitlines(keepends=True) if " SlinkTrade" not in line)


def trade_profile_text(title):
    wrapper = json.loads((ROOT / f"data/games/gen2_{title}/profile.json").read_text())
    ov = wrapper["titles"][title]["overlay"]
    bank, prompt, pickup, commit = TRADE_LABELS[title]
    sym = base_sym(ov["sym"]) + (
        f"{bank:02x}:{prompt:04x} SlinkTradePromptEntry\n{bank:02x}:{pickup:04x} SlinkTradeApplyPickup\n"
        f"{bank:02x}:{commit:04x} SlinkTradeCommit\n")
    ov["trade"] = gen_gen2_profile.trade_block(parse_symbols(sym))
    return json.dumps(wrapper)


def blob70(m, item=0):
    raw = collection([m], 6, 48)
    rec = raw[8:8 + 48]
    rec[1] = item
    ot = bytes([0x80, 0x50]) + bytes([0x50] * 9)
    nick = bytes([m["nickname"], 0x50]) + bytes([0x50] * 9)
    return bytes(rec) + ot + nick


class TradeCart:
    """World + the trade-capable overlay: caps has CAP_TRADE, the lease is mailbox+14."""

    def __init__(self, title="crystal", caps=CAP_TRADE, artifact_kind="overlay"):
        text = trade_profile_text(title)
        self.w = World(title, files={f"gen2_{title}/profile.json": text}, artifact_kind=artifact_kind)
        ov = json.loads(text)["titles"][title]["overlay"]
        self.trade = ov["trade"]
        self.mb = ov["ram"]["wSlinkMailbox"]
        self.lease = self.mb + 14
        self.poke(self.mb, [0x53, 0x4C, 0x4E, 0x4B, 3, 0, 0, 0, caps])
        self.w.hello()
        self.token = None                          # the cartridge's private visit token

    def poke(self, addr, data):
        self.w.emu.poke("System Bus", addr, self.w.lua.table_from(list(data)))

    def frame(self):
        return [self.w.io.read_u8(self.lease + i) for i in range(16)]

    def publish(self, cmd, **fields):
        f = self.frame()
        f[0:5] = [0x53, 0x4C, 0x54, 0x31, 1]
        f[5] = cmd
        for k, v in fields.items():
            f[{"result": 8, "slot": 9, "avail": 10, "mask": 11}[k]] = v
        f[7] = f[6]                                 # SlinkTradeNextGeneration: ack = old gen,
        f[6] = (f[6] + 1) & 255                     # gen = old + 1, published last
        self.poke(self.lease, f)

    def query(self, mask, frames=3):
        self.publish(1, avail=0, mask=0)
        self.w.frames(1)
        assert self.w.sent("trade_query"), self.w.logs.values()
        self.w.reply({"cmd": "trade_mask", "mask": mask})
        self.w.frames(frames)
        f = self.frame()
        assert f[7] == f[6], "host must acknowledge the query"
        self.token = f[12:16]

    def offer(self, slot, ok=True):
        self.publish(2, slot=slot, result=0xFF)
        self.w.frames(1)
        assert self.w.sent("trade_offer")[-1]["slot"] == slot
        self.w.reply({"cmd": "trade_offer_ack", "ok": ok})
        self.w.frames(2)

    def pick_up(self, label):
        """The asm reaches the hook label with the armed frame untouched, then writes the ACK."""
        bank, addr = self.trade["rom"][label]["bank"], self.trade["rom"][label]["addr"]
        assert self.w.emu.fire(bank, addr) == 1, label
        self.w.emu.regs.PC = 0
        f = self.frame()
        if self.token is None:
            self.token = f[12:16]                   # the responder captures the PROMPT token
        if label != "SlinkTradeCommit":
            f[7] = f[6]
        self.poke(self.lease, f)

    def done(self, result, token=None):
        """SlinkTradePublishDone: header, private token, own slot, cmd DONE, gen == ack."""
        f = self.frame()
        f[0:5] = [0x53, 0x4C, 0x54, 0x31, 1]
        f[8], f[5] = result, 7
        f[12:16] = token if token is not None else self.token
        f[7] = f[6]
        self.poke(self.lease, f)

    def close(self):
        """SlinkTradeClose: command/available/mask zeroed, evidence kept."""
        f = self.frame()
        f[5] = f[10] = f[11] = 0
        self.poke(self.lease, f)

    def ot_slot1_spans(self):
        ram = self.trade["ram"]
        return [(ram["wOTPartyMon1"]["addr"] + 48, 48), (ram["wOTPartyMonOTs"]["addr"] + 11, 11),
                (ram["wOTPartyMonNicknames"]["addr"] + 11, 11)]

    def writes_in(self, spans):
        return [x for x in self.w.written() if any(a <= x[0] < a + n for a, n in spans)]


LEAD, PARTNER = mon(), mon(species=19, dvs=0x7AAA, ot=0x4321, nickname=0x82)


def apply_cmd(item=0, old=LEAD):
    return {"cmd": "apply_trade", "slot": 0, "blob_hex": blob70(PARTNER, item).hex(),
            "old_key": codec_key(old), "token": "t1", "partner_name": "BOB"}


def nothing_changed(world):
    return [(d["new_key"], d["new_species"]) for d in world.sent("trade_done")] == [(codec_key(LEAD), 0)]


def proposer_ready(cart):
    cart.query(mask=1)
    cart.offer(0)
    return cart.token


@pytest.mark.parametrize("title", ["crystal", "gold", "silver"])
def test_trade_proposer_apply_keeps_the_offer_token_and_reports_the_received_mon(title):
    cart = TradeCart(title)
    token = proposer_ready(cart)
    cart.w.reply(apply_cmd())
    cart.w.frames(1)
    f = cart.frame()
    assert f[5] == 5 and f[12:16] == token, "APPLY must carry the accepted OFFER token (the asm refuses another)"
    assert f[9] == 0 and f[6] == (f[7] + 1) & 255
    cart.pick_up("SlinkTradeApplyPickup")
    cart.w.party([PARTNER])                        # REMOVE+compact then APPEND (the native commit)
    cart.done(0)
    cart.w.frames(2)
    assert cart.frame()[5] == 8, "a matching DONE is released"
    done = cart.w.sent("trade_done")
    assert [(d["new_key"], d["new_species"]) for d in done] == [(codec_key(PARTNER), 19)]


def test_trade_a_stale_token_never_completes():
    cart = TradeCart()
    token = proposer_ready(cart)
    cart.w.reply(apply_cmd())
    cart.w.frames(1)
    cart.pick_up("SlinkTradeApplyPickup")
    cart.done(0, token=[(b % 255) + 1 for b in token])
    cart.w.frames(5)
    assert not cart.w.sent("trade_done") and cart.frame()[5] == 7, "a foreign DONE is neither released nor reported"
    cart.close()                                    # the cartridge ends the visit without our DONE
    cart.w.frames(2)
    assert nothing_changed(cart.w)


@pytest.mark.parametrize("item", [MAIL, 0xFF, 7])   # mail, the $FF sentinel, an unholdable placeholder
def test_trade_a_mail_or_unholdable_item_is_refused_before_any_write(item):
    cart = TradeCart()
    proposer_ready(cart)
    before = len(cart.w.written())
    cart.w.reply(apply_cmd(item=item))
    cart.w.frames(2)
    assert cart.w.written()[before:] == [], "D3: refused before staging or publishing"
    assert nothing_changed(cart.w)
    # the partner side too: a PROMPT carrying mail is declined, nothing written
    resp = TradeCart()
    before = len(resp.w.written())
    resp.w.reply({"cmd": "show_menu", "token": "t2", "slot": 0, "blob_hex": blob70(PARTNER, item).hex()})
    resp.w.frames(2)
    assert resp.w.written()[before:] == []
    assert [m["choice"] for m in resp.w.sent("menu_result")] == [0]


def test_trade_ot_slot_1_is_never_written_and_the_permit_refuses_it():
    cart = TradeCart()
    proposer_ready(cart)
    cart.w.reply(apply_cmd())
    cart.w.frames(1)
    ram = cart.trade["ram"]
    staged = cart.writes_in([(ram["wOTPartyMon1"]["addr"], 48), (ram["wOTPartyCount"]["addr"], 1),
                             (ram["wOTPartySpecies"]["addr"], 2)])
    assert staged, "incoming slot 0 is staged"
    assert cart.w.io.read_u8(ram["wOTPartySpecies"]["addr"] + 1) == 0xFF
    assert cart.writes_in(cart.ot_slot1_spans()) == []
    trade = cart.w.client.trade
    probe = cart.w.lua.eval("function(t, a) return pcall(function() t.writes:arm() "
                            "t.writes:write_bytes(a, {1}) end) end")
    for addr, _ in cart.ot_slot1_spans():
        ok = probe(trade, addr)
        trade.writes.disarm(trade.writes)
        assert (ok[0] if isinstance(ok, tuple) else ok) is False, hex(addr)


def test_trade_role_comes_from_the_first_command():
    resp = TradeCart()
    resp.w.reply({"cmd": "show_menu", "token": "t2", "slot": 0, "blob_hex": blob70(PARTNER).hex()})
    resp.w.frames(1)
    assert resp.w.client.trade_visit.role == "responder"
    prompt_token = resp.frame()[12:16]
    resp.pick_up("SlinkTradePromptEntry")
    resp.done(0)
    resp.w.frames(2)
    assert [m["choice"] for m in resp.w.sent("menu_result")] == [1] and resp.frame()[5] == 8
    resp.w.reply(apply_cmd())
    resp.w.frames(1)
    assert resp.frame()[5] == 5 and resp.frame()[12:16] == prompt_token, "APPLY reuses the PROMPT token"

    prop = TradeCart()
    prop.query(mask=1)
    assert prop.w.client.trade_visit.role == "proposer"

    stray = TradeCart()                             # no visit: an apply is refused, nothing armed
    before = len(stray.w.written())
    stray.w.reply(apply_cmd())
    stray.w.frames(2)
    assert stray.w.written()[before:] == []
    assert nothing_changed(stray.w)


def test_trade_a_timeout_commits_nothing():
    # the cartridge's own APPLY wait expires (SLINK_TRADE_APPLY_FRAMES): it closes, we report nothing
    cart = TradeCart()
    proposer_ready(cart)
    cart.w.reply(apply_cmd())
    cart.w.frames(1)
    cart.close()
    cart.w.frames(2)
    assert nothing_changed(cart.w)
    assert cart.frame()[5] == 0, "no RELEASE after a timeout"
    # a PROMPT the player never picks up is withdrawn after the host deadline and declined
    resp = TradeCart()
    resp.w.reply({"cmd": "show_menu", "token": "t2", "slot": 0, "blob_hex": blob70(PARTNER).hex()})
    resp.w.frames(1)
    assert resp.frame()[5] == 3
    frames = resp.w.lua.eval("dofile")((ROOT / "lua/gen2/client.lua").as_posix()).TRADE_PICKUP_FRAMES
    resp.w.frames(frames + 2)
    assert resp.frame()[5] != 3, "withdrawn: the dispatcher can never pick it up"
    assert [m["choice"] for m in resp.w.sent("menu_result")] == [0]
    assert resp.w.client.trade_visit is None


@pytest.mark.parametrize("caps,kind", [(0x02, "overlay"), (CAP_TRADE, None)])
def test_trade_is_off_without_the_cap_bit_or_the_overlay_kind(caps, kind):
    cart = TradeCart(caps=caps, artifact_kind=kind)
    before = len(cart.w.written())
    cart.w.reply({"cmd": "show_menu", "token": "t2", "slot": 0, "blob_hex": blob70(PARTNER).hex()})
    cart.w.reply(apply_cmd())
    cart.w.frames(2)
    assert [m["choice"] for m in cart.w.sent("menu_result")] == [0]
    assert cart.w.written()[before:] == []
    assert [d["new_key"] for d in cart.w.sent("trade_done")] == [codec_key(LEAD)]   # the pre-P4 reply


@pytest.mark.parametrize("title", ["crystal", "gold", "silver"])
def test_trade_holdable_set_equals_the_asm_allowed_items_table(title):
    asm = (ROOT / "patch/gen2/src/trade_items.asm").read_text()
    rows = re.findall(r"^\s*db ([01,]+) ; \$", asm, re.M)
    table = [int(x) for row in rows for x in row.split(",")]
    assert len(table) == 256
    lua = LuaRuntime(unpack_returned_tuples=True)
    T = lua.eval("dofile")((ROOT / "lua/gen2/trade_overlay.lua").as_posix())
    items = lua.table_from(json.loads((ROOT / f"data/games/gen2_{title}/items.json").read_text()), recursive=True)
    allowed = T.holdable(items)
    assert [1 if allowed[i] else 0 for i in range(256)] == table


def test_trade_block_is_all_or_none():
    sym = base_sym("crystal_slink.sym")
    assert gen_gen2_profile.trade_block(parse_symbols(sym)) is None       # a build without the family
    with pytest.raises(ValueError, match="partial trade family"):
        gen_gen2_profile.trade_block(parse_symbols(sym + "75:4154 SlinkTradePromptEntry\n"))


@pytest.mark.parametrize("partner", ["BOB", None])
def test_trade_ot_player_name_is_the_partner_trainer_not_the_mon_ot(partner):
    """wOTPlayerName is the SENDER (trade_commit.asm copies it to wOTTrademonSenderName): the server's
    partner_name through the pack charmap; the incoming OT only when the server sent no name."""
    cart = TradeCart()
    proposer_ready(cart)
    cmd = apply_cmd()
    if partner is None:
        del cmd["partner_name"]
    cart.w.reply(cmd)
    cart.w.frames(1)
    addr = cart.trade["ram"]["wOTPlayerName"]["addr"]
    got = [cart.w.io.read_u8(addr + i) for i in range(11)]
    enc = cart.w.lua.eval("dofile")((ROOT / "data/games/gen2_crystal/charmap.lua").as_posix()).encoding
    if partner is None:
        assert got == list(blob70(PARTNER)[48:59])               # the incoming OT ("A")
    else:
        assert got == [enc[ch] for ch in partner] + [0x50] * (11 - len(partner))
        assert got != list(blob70(PARTNER)[48:59])


def committing(cart):
    """ApplyPickup took the APPLY, then the native commit was entered (SlinkTradeCommit)."""
    proposer_ready(cart)
    cart.w.reply(apply_cmd())
    cart.w.frames(1)
    cart.pick_up("SlinkTradeApplyPickup")
    cart.pick_up("SlinkTradeCommit")


def uncertain_done(world):
    return [(d["token"], d.get("uncertain"), "new_key" in d) for d in world.sent("trade_done")]


@pytest.mark.parametrize("how", ["init_clears_the_lease", "reset_boundary"])
def test_trade_a_reset_after_the_commit_entry_is_uncertain_never_a_refusal(how):
    cart = TradeCart()
    committing(cart)
    before = len(cart.w.written())
    if how == "init_clears_the_lease":
        cart.poke(cart.lease, [0] * 16)             # Init after a hardware reset: no DONE ever comes
    else:
        cart.w.checkpoint_ok = False                # the post-reset hello waits for its checkpoint
        cart.w.client.boundary(cart.w.client, "reset", "save_reset")
        cart.w.frames(5)
        assert cart.w.sent("trade_done") == [], "nothing but the hello goes before the post-reset hello"
        cart.w.checkpoint_ok = True
    cart.w.frames(2)
    # an entered commit may have saved: never claim nothing changed -- declare it uncertain, once
    assert uncertain_done(cart.w) == [("t1", True, False)]
    if how == "reset_boundary":
        names = [m["event"] for m in cart.w.sent()]
        assert names.index("trade_done") > len(names) - 1 - names[::-1].index("hello"), "after the new hello"
    cart.w.frames(3)
    assert len(cart.w.sent("trade_done")) == 1
    assert cart.w.written()[before:] == [], "no RELEASE and no restage"
    assert any("UNCERTAIN" in s for s in cart.w.shown())
    assert cart.w.client.trade_state is None and cart.w.client.trade_visit is None


def test_trade_a_native_result_2_is_declared_uncertain_once_and_never_released():
    cart = TradeCart()
    committing(cart)
    cart.done(2)
    cart.w.frames(4)
    assert uncertain_done(cart.w) == [("t1", True, False)]
    assert [d.get("after_reset") for d in cart.w.sent("trade_done")] == [True], \
        "MAJOR-5: the RAM party is no evidence; only the reloaded save (post-reset hello) is"
    assert cart.frame()[5] == 7, "result 2 is never released"
    cart.w.client.boundary(cart.w.client, "reset", "save_reset")         # the reset the hold requires
    cart.w.frames(4)
    assert len(cart.w.sent("trade_done")) == 1, "declared once"


@pytest.mark.parametrize("how", ["apply_wait_timeout", "late_ack"])
def test_trade_the_proposer_leaving_before_apply_withdraws_the_offer(how):
    """Trade-driver finding: B's late YES used to reach a server that still thought A was waiting;
    A's cartridge had left, so B committed alone. A withdraws under its ack's token instead."""
    cart = TradeCart()
    cart.query(mask=1)
    cart.publish(2, slot=0, result=0xFF)
    cart.w.frames(1)
    if how == "late_ack":
        cart.close()                                # the cartridge's offer wait expired first
        cart.w.reply({"cmd": "trade_offer_ack", "ok": True, "token": "t1"})
        cart.w.frames(2)
    else:
        cart.w.reply({"cmd": "trade_offer_ack", "ok": True, "token": "t1"})
        cart.w.frames(2)
        assert cart.w.sent("menu_result") == [] and cart.w.client.trade_visit.accepted
        cart.close()                                # SLINK_TRADE_APPLY_FRAMES ran out: SlinkTradeExit
        cart.w.frames(2)
    assert [(m["token"], m["choice"], m.get("withdraw")) for m in cart.w.sent("menu_result")] == [("t1", 0, True)]
    assert cart.w.client.trade_visit is None
    cart.w.frames(3)
    assert len(cart.w.sent("menu_result")) == 1


def test_trade_a_refused_offer_or_a_tokenless_ack_withdraws_nothing():
    for ack in ({"cmd": "trade_offer_ack", "ok": False}, {"cmd": "trade_offer_ack", "ok": True}):
        cart = TradeCart()
        cart.query(mask=1)
        cart.publish(2, slot=0, result=0xFF)
        cart.w.frames(1)
        cart.w.reply(ack)
        cart.w.frames(2)
        cart.close()
        cart.w.frames(2)
        assert cart.w.sent("menu_result") == []


def test_trade_a_validation_close_after_pickup_before_the_commit_is_a_refusal():
    cart = TradeCart()
    proposer_ready(cart)
    cart.w.reply(apply_cmd())
    cart.w.frames(1)
    cart.pick_up("SlinkTradeApplyPickup")          # SlinkTradeCheckIncoming etc. refuse -> SlinkTradeExit
    cart.close()
    cart.w.frames(2)
    assert nothing_changed(cart.w)
    assert not any("UNCERTAIN" in s for s in cart.w.shown())


def test_trade_block_requires_the_commit_label():
    sym = base_sym("crystal_slink.sym")
    with pytest.raises(ValueError, match="SlinkTradeCommit"):
        gen_gen2_profile.trade_block(parse_symbols(
            sym + "75:4154 SlinkTradePromptEntry\n75:4237 SlinkTradeApplyPickup\n"))


# ── MAJOR-1 (review e9d5e136): the prepare round ─────────────────────────────────────────────────

def prepare_cmd(old=LEAD):
    return {"cmd": "apply_prepare", "token": "t1", "slot": 0, "old_key": codec_key(old)}


def test_trade_hello_declares_the_prepare_round_only_on_a_live_trade_build():
    assert TradeCart().w.sent("hello")[0].get("trade_prepare") is True
    assert TradeCart(caps=0x02).w.sent("hello")[0].get("trade_prepare") is False


def test_trade_prepare_is_ready_only_while_the_cartridge_still_waits_for_the_apply():
    cart = TradeCart()
    proposer_ready(cart)
    before = len(cart.w.written())
    cart.w.reply(prepare_cmd())
    cart.w.frames(1)
    assert [(m["token"], m["ok"]) for m in cart.w.sent("apply_ready")] == [("t1", True)]
    assert cart.w.written()[before:] == [], "a prepare stages nothing"
    assert cart.w.client.trade_visit.accepted, "the visit still takes the apply"

    left = TradeCart()
    proposer_ready(left)
    left.close()                                   # the APPLY wait ran out before the prepare
    left.w.frames(2)
    left.w.reply(prepare_cmd())
    left.w.frames(1)
    assert [m["ok"] for m in left.w.sent("apply_ready")] == [False]

    stray = TradeCart()                            # no accepted visit, or another mon
    stray.w.reply(prepare_cmd())
    stray.w.frames(1)
    assert [m["ok"] for m in stray.w.sent("apply_ready")] == [False]


# ── the Bug-Catching Contest refuses a trade (9805ac1c SlinkTradeCheckParty): the host never arms one ──

def in_contest(cart, on=True):
    mask = HOLDS[cart.w.title]["contest_mask"]
    cart.poke(mask["address"], [(1 << mask["bit"]) if on else 0])


def test_trade_in_the_contest_declines_a_prompt_without_arming_it_and_answers_a_zero_mask():
    resp = TradeCart()
    in_contest(resp)
    before = len(resp.w.written())
    resp.w.reply({"cmd": "show_menu", "token": "t2", "slot": 0, "blob_hex": blob70(PARTNER).hex()})
    resp.w.frames(2)
    assert [m["choice"] for m in resp.w.sent("menu_result")] == [0]
    assert resp.w.written()[before:] == [], "no PROMPT armed during the contest"
    assert resp.w.client.trade_visit is None

    prop = TradeCart()
    prop.publish(1, avail=0, mask=0)
    prop.w.frames(1)
    in_contest(prop)
    prop.w.reply({"cmd": "trade_mask", "mask": 1})
    prop.w.frames(2)
    f = prop.frame()
    assert f[7] == f[6] and f[11] == 0 and f[10] == 0, "the query is answered, with nothing eligible"
    assert prop.w.client.trade_visit is None


def test_trade_blocked_rides_the_tick_while_the_contest_masks_the_party():
    cart = TradeCart()
    in_contest(cart)
    cart.w.frames(60)
    assert cart.w.sent("tick")[-1].get("trade_blocked") is True
    in_contest(cart, on=False)
    cart.w.frames(60)
    assert cart.w.sent("tick")[-1].get("trade_blocked") is False


# ── MAJOR-4 (review e9d5e136): the server asks a silent side to withdraw its APPLY ───────────────

def test_trade_withdraw_pulls_an_unpicked_apply_and_reports_nothing_changed():
    cart = TradeCart()
    proposer_ready(cart)
    cart.w.reply(apply_cmd())
    cart.w.frames(1)
    assert cart.frame()[5] == 5
    cart.w.reply({"cmd": "withdraw_trade", "token": "t1"})
    cart.w.frames(2)
    assert cart.frame()[5] != 5, "the dispatcher can never pick it up now"
    assert nothing_changed(cart.w)


def test_trade_withdraw_after_the_commit_entry_declares_uncertain():
    cart = TradeCart()
    committing(cart)
    cart.w.reply({"cmd": "withdraw_trade", "token": "t1"})
    cart.w.frames(3)
    assert uncertain_done(cart.w) == [("t1", True, False)]
    assert [d.get("after_reset") for d in cart.w.sent("trade_done")] == [None]


# ── review m2: a reset before the commit entry reports a certain nothing-changed after the hello ──

def test_trade_a_reset_before_the_commit_entry_reports_nothing_changed_after_the_hello():
    cart = TradeCart()
    proposer_ready(cart)
    cart.w.reply(apply_cmd())
    cart.w.frames(1)
    cart.pick_up("SlinkTradeApplyPickup")          # picked up, not committing
    cart.w.checkpoint_ok = False
    cart.w.client.boundary(cart.w.client, "reset", "save_reset")
    cart.w.frames(3)
    assert cart.w.sent("trade_done") == []
    cart.w.checkpoint_ok = True
    cart.w.frames(3)
    assert nothing_changed(cart.w)
    assert not any("UNCERTAIN" in s for s in cart.w.shown())


def test_trade_a_held_report_dropped_at_a_boundary_becomes_an_uncertain_declaration():
    cart = TradeCart()
    proposer_ready(cart)
    cart.w.checkpoint_ok = False
    cart.w.client.hello_session.invalidate(cart.w.client.hello_session, "test: hello pending")
    cart.w.client.send("trade_done", cart.w.lua.table_from({"token": "t1", "new_key": codec_key(PARTNER),
                                                              "new_species": 19}))
    assert cart.w.sent("trade_done") == [], "held until the hello"
    cart.w.client.boundary(cart.w.client, "reset", "save_reset")
    cart.w.checkpoint_ok = True
    cart.w.frames(3)
    assert uncertain_done(cart.w) == [("t1", True, False)]


def test_a_complete_box_scan_is_stamped_with_a_generation():
    """KEY-SCOPE-5: `pc_boxes` is a complete census only with `pc_boxes_generation`, bumped after
    each successful full rescan and never on a failed one (the server fails a key_change closed
    on a missing or stale census)."""
    def check(world):
        assert world.hello().get("pc_boxes_generation") == 1
        world.frames(30)
        assert world.sent("tick")[-1].get("pc_boxes_generation") == 1
        world.field("wCurBox", 99)                    # read_current_box_num refuses: the scan fails
        world.client.pending_rescan = True
        world.frames(30)
        assert world.sent("tick")[-1].get("pc_boxes_generation") is None
        world.field("wCurBox", 0)
        world.client.pending_rescan = True
        world.frames(30)
        assert world.sent("tick")[-1].get("pc_boxes_generation") == 2
        row = world.profile["storage_boxes"][3]
        world.emu.poke("CartRAM", row["flat"], world.lua.table_from([0, 0]))   # one box read fails
        world.client.pending_rescan = True
        world.frames(30)
        assert world.sent("tick")[-1].get("pc_boxes_generation") is None
        world.emu.poke("CartRAM", row["flat"], world.lua.table_from([0, 255]))
        world.client.pending_rescan = True
        world.frames(30)
        assert world.sent("tick")[-1].get("pc_boxes_generation") == 3

    falsify(check, mutant("lua/gen2/client.lua", ("            complete = complete and mons ~= nil -- any failed box read: not a census\n",
                                                  "")))


def test_a_refused_change_is_resent_after_a_newer_census_and_a_failed_scan_retries():
    """KEY-SCOPE-5 (cx-06ec4e8e F5/F6): "box census unavailable" and the ambiguity latch retire
    nothing, so the alias stays and the change goes out again after a complete census newer than
    the refusal; a collision is terminal. A failed scan retries on the next tick by itself."""
    def check(world):
        world.hello()
        world.frames(30)
        msg = {"old_key": "0001:0002:03", "new_key": "0004:0005:06", "new_species": 25, "reason": "evolution"}
        world.client.key_alias = world.lua.table_from({"old_key": msg["old_key"], "new_key": msg["new_key"],
                                                       "msg": world.lua.table_from(msg)})
        world.reply({"cmd": "key_change_rejected", "old_key": msg["old_key"], "new_key": msg["new_key"],
                     "reason": "box census unavailable"})
        world.frames(30)
        assert world.client.key_alias is not None and world.client.retired_alias[msg["old_key"]] is None
        resent = world.sent("key_change")
        assert [{k: m[k] for k in msg} for m in resent] == [msg], "re-sent once, after the fresh census"
        lines = world.sent()
        i = max(j for j, m in enumerate(lines) if m["event"] == "key_change")
        assert lines[i - 1]["event"] == "tick" and lines[i - 1].get("pc_boxes_generation") == 2
        world.reply({"cmd": "key_change_ack", "old_key": msg["old_key"], "new_key": msg["new_key"], "migrated": True})
        world.frames(30)
        assert world.client.key_alias is None
        # a failed scan is retried by itself on the next tick
        world.field("wCurBox", 99)
        world.client.pending_rescan = True
        world.frames(30)
        assert world.sent("tick")[-1].get("pc_boxes_generation") is None
        world.field("wCurBox", 0)
        world.frames(30)
        assert world.sent("tick")[-1].get("pc_boxes_generation") == 3

    falsify(check, mutant("lua/gen2/client.lua", ("        resend_refused_change()\n", "")))
    falsify(check, mutant("lua/gen2/client.lua", (
        "        if self.pending_rescan or (not self.box_complete and self.frame % Client.TICK_INTERVAL == 0) then\n",
        "        if self.pending_rescan then\n")))


@pytest.mark.parametrize("title", ["crystal", "gold", "silver"])
def test_real_gen2_binder_refusal_and_clobber_keep_pre_diff_result(title):
    cart = TradeCart(title)
    proposer_ready(cart)
    cart.w.reply(apply_cmd())
    cart.w.frames(1)
    trade = cart.w.client.trade
    assert trade.phase == "armed"
    # Refusal before the native pickup label: no callback/ACK for these polls.
    cart.w.frames(3)
    assert trade.phase == "armed" and trade.poll_done(trade) is None
    frame = cart.frame()
    frame[0] = 0
    cart.poke(cart.lease, frame)
    assert trade.clobbered(trade) is True and trade.pickup_error is None
    cart.w.frames(1)
    assert nothing_changed(cart.w)
    assert cart.w.client.trade_state is None
