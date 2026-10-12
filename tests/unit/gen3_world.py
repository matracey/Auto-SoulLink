"""A fake GBA + fake server that drives the PRODUCTION lua/gen3/entry.lua build (P4 C4-2b).

Shared harness for the Gen 3 client suites (test_gen3_client.py, the conformance World half
C4-5, the write-ownership guard). One `World` is one cartridge + one client:

* the ROM is sparse: every engine site's pinned bytes (engine_signals.json) and every
  checkpoint anchor (write_checkpoint.json) at their ROM offsets, so the real site check and
  the real checkpoint predicate run against pack data, not a stub;
* the bus is a sparse byte dict; the save blocks, party, boxes, bag, location and battle
  state are laid out through the pack's own profile fields and records are encoded by the
  Python codec twin (server/adapters/gen3_codec.py), so reads.lua decodes real records;
* every line the client sends is parsed and checked against tests/unit/protocol_schema.py
  (a schema problem raises at send time), every command a test injects is validated too;
* every byte written reaches `self.writes` as (addr, value, frame): the write
  sink is the injected io.write_u8; writes.lua keeps its own armed-reason log (parts.writes.log).
"""
from __future__ import annotations

import json
import pathlib

import lupa

from server.adapters import gen3_codec as codec
from tests.unit import protocol_schema as ps

REPO = pathlib.Path(__file__).resolve().parents[2]
ENTRY = (REPO / "lua" / "gen3" / "entry.lua").as_posix()
PACK_DIRS = {"gen3_frlg": REPO / "data" / "games" / "gen3_frlg",
             "gen3_emerald": REPO / "data" / "games" / "gen3_emerald",
             "gen3_rr": REPO / "data" / "games" / "gen3_rr"}
ROM_BASE = 0x08000000

# Where the fake save blocks live (any word-aligned EWRAM not used by a pinned address).
SB1_ADDR = 0x02010000
SB2_ADDR = 0x02014000

# (pack, title, kind) of every admitted artifact
ARTIFACTS = [("gen3_frlg", "firered", "clean"), ("gen3_frlg", "leafgreen", "clean"),
             ("gen3_frlg", "firered", "companion"), ("gen3_frlg", "leafgreen", "companion"),
             ("gen3_rr", "radical_red", "clean"), ("gen3_rr", "radical_red", "companion")]
# Emerald has its own client fixtures (rom_type map, bag/rival layout), so its rows are separate.
EMERALD_ARTIFACTS = [("gen3_emerald", "emerald", "clean"), ("gen3_emerald", "emerald", "companion")]


def pack_json(pack: str, name: str) -> dict:
    return json.loads((PACK_DIRS[pack] / name).read_text(encoding="utf-8"))


def lua_to_py(value):
    if lupa.lua_type(value) != "table":
        return value
    items = list(value.items())
    keys = [k for k, _ in items]
    if not keys:
        return []
    if all(isinstance(k, int) for k in keys) and sorted(keys) == list(range(1, len(keys) + 1)):
        return [lua_to_py(value[i]) for i in range(1, len(keys) + 1)]
    return {k: lua_to_py(v) for k, v in items}


STATS = ("hp", "attack", "defense", "speed", "sp_attack", "sp_defense")


def mon_record(personality: int, ot_id: int, species: int = 1, level: int = 5, hp: int = 20,
               max_hp: int = 20, nickname: str = "MON", moves=(33, 45, 0, 0), is_egg: int = 0) -> dict:
    """A complete codec dict for one party mon (every field _encode_mon reads)."""
    return {
        "personality": personality, "ot_id": ot_id, "nickname": nickname, "ot_name": "RED",
        "language": 2, "is_bad_egg": 0, "has_species": 1, "is_egg_flag": is_egg, "block_box_rs": 0,
        "markings": 0, "species": species, "held_item": 0, "experience": 135, "pp_bonuses": 0,
        "friendship": 70, "moves": list(moves), "pp": [35, 30, 0, 0],
        "evs": dict.fromkeys(STATS, 0),
        "contest": [0] * 6, "pokerus": 0, "met_location": 1, "met_level": 5, "met_game": 4,
        "pokeball": 4, "ot_gender": 0,
        "ivs": dict.fromkeys(STATS, 10),
        "is_egg": is_egg, "ability_num": 0, "ribbons": 0,
        "status": 0, "level": level, "mail": 0xFF, "hp": hp,  # mail: MAIL_NONE
        "max_hp": max_hp, "attack": 11,
        "defense": 12, "speed": 13, "sp_attack": 14, "sp_defense": 15,
    }


def key_of(personality: int, ot_id: int) -> str:
    return f"{personality:08X}:{ot_id:08X}"


def compress_box_mon(raw80: bytes) -> bytes:
    """Inverse of codec.expand_compressed_box_mon for an unencrypted (RR) 80-byte record."""
    out = bytearray(codec.COMPRESSED_MON_SIZE)
    out[0x00:0x1C] = raw80[0x00:0x1C]
    out[0x1C:0x27] = raw80[0x20:0x2B]
    packed = 0
    for i in range(4):
        move = int.from_bytes(raw80[0x2C + 2 * i:0x2E + 2 * i], "little") & 0x3FF
        packed |= move << (10 * i)
    out[0x27:0x2C] = packed.to_bytes(5, "little")
    out[0x2C:0x32] = raw80[0x38:0x3E]
    out[0x32:0x3A] = raw80[0x44:0x4C]
    return bytes(out)


def default_kind(pack: str, title: str) -> str:
    """The artifact a unit world boots by default: the companion when the title ships one (the
    companion patch is what SLink admits for production), else the clean cartridge."""
    return "companion" if "companion" in pack_json(pack, "engine_signals.json")["titles"][title]["artifacts"] else "clean"


class World:
    def __init__(self, pack="gen3_frlg", title="firered", kind=None, player="a",
                 connected=True, native=None, boxes=None, journal=None, model_companion=False, outbox=None):
        kind = default_kind(pack, title) if kind is None else kind
        self.pack, self.title, self.kind, self.player = pack, title, kind, player
        self.artifact_kind = "clean" if kind == "named" else kind
        # A CLEAN cartridge is not a production artifact (production:false, refused at admission),
        # so a clean World is a MODEL of the client's clean-vs-companion branches: the build below
        # strips the flag from its decoded copy of the pack. Companion worlds are the real thing.
        model_clean = self.artifact_kind == "clean"
        titles = pack_json(pack, "engine_signals.json")["titles"]
        self.sites = titles[title]["artifacts"][self.artifact_kind]["sites"]
        self.profile = pack_json(pack, "profile.json")["titles"][title]
        self.ram, self.d = self.profile["ram"], self.profile["derived"]
        self.rr = self.d.get("CFRU_NO_ENCRYPT") is True
        self.wc = pack_json(pack, "write_checkpoint.json")[title]
        self.rom: dict[int, int] = {}
        for site in self.sites.values():
            for i, byte in enumerate(bytes.fromhex(site["expected_hex"])):
                self.rom[site["rom_offset"] + i] = byte
        for anchor in self.wc["anchors"].values():
            for i, byte in enumerate(bytes.fromhex(anchor["expected_hex"][self.artifact_kind])):
                self.rom[anchor["rom_offset"] + i] = byte
        # gBattleMoves PP for the default moves (boxes.lua rebuilds PP from it on a deposit)
        moves_addr = self.profile.get("rom", {}).get("BATTLE_MOVES_ADDR")
        if isinstance(moves_addr, int):
            stride, pp_off = self.d["BATTLE_MOVE_ENTRY_SIZE"], self.d["BATTLE_MOVE_PP_OFFSET"]
            for move, pp in ((33, 35), (45, 40), (153, 5)):
                self.rom[moves_addr - ROM_BASE + move * stride + pp_off] = pp
        self.bus: dict[int, int] = {}
        self.frame = 0
        self.regs = {"R13": 0x03007F00, "R15": 0, "CPSR": 0}
        self.hooks: dict[str, tuple] = {}
        self.connected = connected
        self.sent: list[dict] = []
        self.lines: list[str] = []
        self.replies: list[str] = []
        self.auto_recovery_control = True
        self.hud: list[tuple] = []
        self.logs: list[str] = []
        self.writes: list[tuple[int, int, int]] = []
        self.saveram_calls = 0
        # battle_ok: does the fake bus satisfy the pack's REAL battle clause set (write_checkpoint
        # battle.clauses, judged by lua/gen3/safety.lua)? battle_checks: every non-overworld
        # reason the real policy was asked about, recorded at the policy seam.
        self._battle_ok = False
        self.in_battle_state = False
        # gEnemyPartyCount is never maintained by the engine in battle (pret writes it only from
        # CalculateEnemyPartyCount, called only from trade.c), so enter_battle leaves it at 0.
        # Set this to model the ONE non-zero writer: the RR companion patch's rival-swap staging.
        self.stale_enemy_count = 0
        self.battle_checks: list[str] = []
        self.lua = L = lupa.LuaRuntime(unpack_returned_tuples=True)
        self.io = L.table(
            read_u8=lambda a: self._read(int(a), 1), read_u16=lambda a: self._read(int(a), 2),
            read_u32=lambda a: self._read(int(a), 4), read_bytes=self._read_bytes,
            rom_read=self._rom_read, framecount=lambda: self.frame,
            register=lambda name: self.regs.get(str(name), 0),
            write_u8=self._write_u8, saveram=self._saveram,
        )
        if journal is not None:
            self.io.trade_journal = journal(L)
        if outbox is not None:
            self.io.report_outbox = outbox(L)
        self.ev = L.table(on_bus_exec=self._on_bus_exec, unregister=lambda i: None)
        net = L.table(init=lambda h, p: None, connected=lambda: self.connected, pump=lambda: None,
                      send=self._send, receive=lambda: self.replies.pop(0) if self.replies else None)

        def rec(name):
            return lambda *a: self.hud.append((name,) + tuple(str(x) if lupa.lua_type(x) == "string"
                                                              or isinstance(x, (str, bytes)) else x
                                                              for x in a))
        hud = L.table(show=rec("show"), prompt=rec("prompt"), set_game_over=rec("game_over"),
                      set_rebuilding=rec("rebuilding"), clear_rebuilding=rec("rebuild_done"),
                      nuzlocke_start=rec("nuzlocke_start"))
        self.Entry = L.eval(f'dofile("{ENTRY}")')
        deps = {"root": REPO.as_posix(), "mode": "production", "io": self.io, "ev": self.ev,
                "net": net, "hud": hud, "pack": pack, "title": title, "kind": kind, "player": player,
                "rom_sha1": "ab" * 20, "log": lambda t: self.logs.append(str(t))}
        if native is not None:          # native(lua_runtime) -> the native part's Lua table
            deps["native"] = native(L)
        if boxes is not None:           # boxes(lua_runtime) -> the mover's Lua table
            mover = boxes(L)
            deps["boxes_new"] = lambda *_a: mover
        if model_companion or model_clean:
            # Explicit MODEL-only seams over the real build, undone right after it:
            # * model_companion: bind the companion client over vanilla read/anchor geometry
            #   (no production admission/profile/READY flag is changed).
            # * model_clean: see above; only the decoded copy of the pack loses the flag.
            L.globals().MODEL_COMPANION, L.globals().MODEL_CLEAN = model_companion, model_clean
            L.execute('''
                model_real_dofile = dofile
                function dofile(path)
                    local module = model_real_dofile(path)
                    if MODEL_COMPANION and path:match("lua/gen3/client.lua$") then
                        local make = module.new
                        module.new = function(p) p.artifact_kind="companion"; return make(p) end
                    end
                    if MODEL_CLEAN and path:match("lua/json_codec.lua$") then
                        local decode = module.decode
                        module.decode = function(...)
                            local value = decode(...)
                            if type(value) == "table" and type(value.titles) == "table" then
                                for _, title in pairs(value.titles) do
                                    local clean = type(title) == "table" and title.artifacts and title.artifacts.clean
                                    if clean then clean.production = nil end
                                end
                            end
                            return value
                        end
                    end
                    return module
                end
            ''')
        self.client, self.parts = self.Entry.build(L.table(**deps))
        if model_companion or model_clean:
            L.execute('dofile = model_real_dofile; model_real_dofile = nil; MODEL_COMPANION = nil; MODEL_CLEAN = nil')
        policy = self.parts.policy
        real_check = policy.check

        def check(this, snap, reason, args=None):
            if str(reason) != "overworld":
                self.battle_checks.append(str(reason))
            return real_check(this, snap, reason, args)
        policy.check = check
        self.setup_save()
        self.overworld_safe()
        self.client.start(self.client)

    # -- BizHawk fakes -----------------------------------------------------------------
    def _byte(self, addr):
        if addr >= ROM_BASE:
            return self.rom.get(addr - ROM_BASE, 0)
        return self.bus.get(addr, 0)

    def _read(self, addr, size):
        return sum(self._byte(addr + i) << (8 * i) for i in range(size))

    def _read_bytes(self, addr, length):
        addr = int(addr)
        return self.lua.table(*[self._byte(addr + i) for i in range(int(length))])

    def _rom_read(self, offset, length):
        offset = int(offset)
        return self.lua.table(*[self.rom.get(offset + i, 0) for i in range(int(length))])

    def _write_u8(self, addr, value, _domain=None):
        addr, value = int(addr), int(value)
        self.bus[addr] = value
        self.writes.append((addr, value, self.frame))

    def _saveram(self):
        self.saveram_calls += 1

    def _on_bus_exec(self, fn, addr, name):
        self.hooks[str(name)] = (fn, int(addr))
        return f"id-{len(self.hooks):04d}"

    @property
    def battle_ok(self):
        return self._battle_ok

    @battle_ok.setter
    def battle_ok(self, value):
        self._battle_ok = bool(value)
        if self.in_battle_state:
            self.apply_battle_clauses()

    def apply_battle_clauses(self):
        """Put the bus in (battle_ok) or out of the pack's battle-safe state: the action-
        selection input wait, gBattleCommunication[0] == 1 (docs: OMP C4-B). Only the clause
        bytes are touched; a masked clause clears just its bits."""
        for c in self.wc["battle"]["clauses"]:
            addr, width = c["address"] + c.get("offset", 0), c["width"]
            if c["compare"] == "nonzero":
                if self._read(addr, width) == 0:
                    self.poke_int(addr, 1, width)
            elif "mask" in c:
                self.poke_int(addr, (self._read(addr, width) & ~c["mask"]) | c["expect"], width)
            elif c["name"] == "battle_main_func" and not self._battle_ok:
                self.poke_int(addr, 0, width)            # the engine is not in its input wait
            else:
                self.poke_int(addr, c["expect"], width)

    def _send(self, line):
        self.lines.append(str(line))
        msg = json.loads(str(line))
        problems = ps.validate_event(msg)
        assert problems == [], (msg, problems)
        self.sent.append(msg)
        if msg["event"] == "hello" and self.auto_recovery_control:
            self.command(cmd="recovery_control", paused=[], cancelled={})

    # -- memory helpers ------------------------------------------------------------------
    def poke(self, addr: int, data: bytes) -> None:
        for i, byte in enumerate(data):
            self.bus[addr + i] = byte

    def poke_int(self, addr: int, value: int, width: int) -> None:
        self.poke(addr, int(value).to_bytes(width, "little"))

    def num(self, name):
        v = self.ram.get(name)
        return v if isinstance(v, int) else None

    def party_base(self) -> int:
        if self.d.get("PARTY_IN_SB1"):
            return SB1_ADDR + self.d["SB1_PARTY_BASE_OFFSET"]
        return self.ram["PARTY_BASE"]

    def setup_save(self, ot_id=0x0000ABCD, name="RED"):
        """Pointers, trainer, location and an empty party: a live, loaded save."""
        ptrs = self.wc["pointers"]
        self.poke_int(ptrs["gSaveBlock1Ptr"]["address"], SB1_ADDR, 4)
        self.poke_int(ptrs["gSaveBlock2Ptr"]["address"], SB2_ADDR, 4)
        self.poke_int(ptrs["gPokemonStoragePtr"]["address"], self.ram["POKEMON_STORAGE_BASE"], 4)
        self.set_trainer(ot_id, name)
        self.set_location(3, 19)
        self.set_party([])

    def write_companion_mailbox(self):
        """Companion evidence: the patch's RAM mailbox signature and ABI word (profile native
        block: BASE/SIG/ABI), the two reads native.lua's present() and companion_live() judge.
        A no-op when the pack profile has no native block."""
        nat = pack_json(self.pack, "profile.json").get("native")
        if not isinstance(nat, dict) or not all(isinstance(nat.get(k), int) for k in ("BASE", "SIG", "ABI")):
            return False
        self.poke_int(nat["BASE"], nat["SIG"], 4)
        self.poke_int(nat["BASE"] + 4, nat["ABI"], 2)
        return True

    def set_trainer(self, ot_id, name="RED"):
        if "SB2_OT_ID_OFFSET" in self.d:          # each offset independently: packs pin them apart
            self.poke_int(SB2_ADDR + self.d["SB2_OT_ID_OFFSET"], ot_id, 4)
        if "SB2_NAME_OFFSET" in self.d:
            self.poke(SB2_ADDR + self.d["SB2_NAME_OFFSET"], codec.encode_name(name, codec.OT_NAME_LEN))

    def set_location(self, group, num):
        if "SB1_LOCATION_MAP_GROUP_OFFSET" in self.d:
            self.poke_int(SB1_ADDR + self.d["SB1_LOCATION_MAP_GROUP_OFFSET"], group, 1)
            self.poke_int(SB1_ADDR + self.d["SB1_LOCATION_MAP_NUM_OFFSET"], num, 1)

    def set_badges(self, mask):
        self.poke_int(SB1_ADDR + self.d["SB1_FLAGS_OFFSET"] + self.d["SB1_BADGE_BYTE_OFFSET"], mask, 1)

    def set_balls(self, count, item=4):
        if self.d.get("BAG_IN_EWRAM"):
            base, enc = self.ram["BALL_POCKET_ADDR"], 0
        else:
            base = SB1_ADDR + self.d["SB1_BALL_POCKET_OFFSET"]
            enc = self._read(SB2_ADDR + self.d["SB2_ENC_KEY_OFFSET"], 4) & 0xFFFF
        self.poke_int(base, item, 2)
        self.poke_int(base + 2, count ^ enc, 2)

    def encode(self, rec: dict) -> bytes:
        return codec.encode_party_mon(rec, rr=self.rr)

    def set_party(self, records: list[dict]):
        self.poke_int(self.ram["PARTY_COUNT_ADDR"], len(records), 1)
        base = self.party_base()
        for i in range(6):
            data = self.encode(records[i]) if i < len(records) else bytes(codec.PARTY_MON_SIZE)
            self.poke(base + i * codec.PARTY_MON_SIZE, data)

    def party_hp(self, slot: int) -> int:
        return self._read(self.party_base() + slot * codec.PARTY_MON_SIZE + 0x56, 2)

    def box_addr(self, box: int, slot: int) -> int:
        if self.d.get("CFRU_COMPRESSED_BOX"):
            return self.d["CFRU_BOX_BASES"][box] + slot * self.d["COMPRESSED_MON_SIZE"]
        return (self.ram["POKEMON_STORAGE_BASE"] + self.d["BOX_DATA_OFFSET"]
                + (box * self.d["MONS_PER_BOX"] + slot) * codec.BOX_MON_SIZE)

    def set_box(self, box: int, slot: int, rec: dict | None):
        if rec is None:
            size = self.d["COMPRESSED_MON_SIZE"] if self.d.get("CFRU_COMPRESSED_BOX") else codec.BOX_MON_SIZE
            self.poke(self.box_addr(box, slot), bytes(size))
            return
        raw = codec.encode_box_mon(rec, rr=self.rr)
        if self.d.get("CFRU_COMPRESSED_BOX"):
            raw = compress_box_mon(raw)
        self.poke(self.box_addr(box, slot), raw)

    # -- checkpoint / battle -------------------------------------------------------------
    def overworld_safe(self):
        """Every checkpoint clause holds: predicates at expect, parked CPU, no active task."""
        for p in self.wc["predicates"].values():
            self.poke_int(p["address"] + p["offset"], p["expect"], p["width"])
        cpu = self.wc["cpu"]
        self.regs["R15"] = cpu["pc_min"]
        self.regs["CPSR"] = cpu["mode"] | (cpu["thumb"] << 5)

    def break_checkpoint(self, name="field_controls_locked"):
        p = self.wc["predicates"][name]
        self.poke_int(p["address"] + p["offset"], (p["expect"] + 1) & 0xFF, 1)

    def enter_battle(self, enemy: list[dict], trainer_id=0, doubles=False, active=(0,), fire=True):
        """Battle state as read_battle sees it, the checkpoint's in_battle clause set."""
        p = self.wc["predicates"]["in_battle"]
        self.poke_int(p["address"] + p["offset"], p["mask"], 1)
        flags = (self.d.get("BATTLE_TYPE_TRAINER_MASK", 0) if trainer_id else 0) \
            | (self.d.get("BATTLE_TYPE_DOUBLE_MASK", 0) if doubles else 0)
        self.poke_int(self.ram["BATTLE_TYPE_ADDR"], flags, 4)
        self.poke_int(self.ram["TRAINER_OPPONENT_ADDR"], trainer_id, 2)
        self.poke_int(self.ram["BATTLE_OUTCOME_ADDR"], 0, 1)
        count = 4 if doubles else 2
        self.poke_int(self.ram["BATTLERS_COUNT_ADDR"], count, 1)
        order = [active[0], 0, active[1] if len(active) > 1 else 1, 1]
        for b in range(count):
            self.poke_int(self.ram["BATTLER_PARTY_INDEXES_ADDR"] + 2 * b, order[b], 2)
        self.poke_int(self.ram["ENEMY_COUNT_ADDR"], self.stale_enemy_count, 1)
        # ZeroEnemyPartyMons (pret pokemon.c:1748-1752) clears all six slots before every battle
        self.poke(self.ram["ENEMY_BASE"], bytes(6 * codec.PARTY_MON_SIZE))
        for i, rec in enumerate(enemy):
            self.poke(self.ram["ENEMY_BASE"] + i * codec.PARTY_MON_SIZE, self.encode(rec))
        # battler 0 live: the CFRU in-battle detector reads its maxHP (reads.lua read_battle);
        # its hp is live too (an active battler at a parked menu never reads 0 in the engine)
        self.poke_int(self.ram["BATTLE_MONS_ADDR"] + 0x28 + 4, 20, 2)
        self.poke_int(self.ram["BATTLE_MONS_ADDR"] + 0x28, 20, 2)
        self.in_battle_state = True
        self.apply_battle_clauses()
        if fire:
            self.fire("battle_begin")

    def set_active(self, slots):
        for b, slot in zip((0, 2), slots, strict=False):
            self.poke_int(self.ram["BATTLER_PARTY_INDEXES_ADDR"] + 2 * b, slot, 2)

    def leave_battle(self, outcome=1, fire=True):
        self.in_battle_state = False
        p = self.wc["predicates"]["in_battle"]
        self.poke_int(p["address"] + p["offset"], p["expect"], 1)
        self.poke_int(self.ram["BATTLE_OUTCOME_ADDR"], outcome, 1)
        self.poke_int(self.ram["BATTLE_MONS_ADDR"] + 0x28 + 4, 0, 2)
        if fire:
            self.fire("battle_end")

    # -- driving -------------------------------------------------------------------------
    def fire(self, kind: str):
        fn, addr = self.hooks[f"SLink-gen3-{kind}"]
        fn(addr)

    def step(self, n=1):
        for _ in range(n):
            self.frame += 1
            self.client.frame_end(self.client)

    def step_to(self, frame):
        while self.frame < frame:
            self.step()

    def command(self, **cmd):
        assert ps.validate_command(cmd) == [], ps.validate_command(cmd)
        self.replies.append(json.dumps({"commands": [cmd]}))

    def events(self, name):
        return [m for m in self.sent if m["event"] == name]

    def names(self):
        return [m["event"] for m in self.sent]
