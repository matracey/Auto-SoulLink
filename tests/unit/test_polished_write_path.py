"""Card POL-WRITES: the Polished OVERWORLD write path (lua/gen2/polished_overworld.lua, composed by
compose_polished in lua/gen2/entry.lua).

Same rig as test_polished_client.py / test_polished_boxes_census.py: the REAL overlay ROM
(patch/dist/SLink-Polished.ups over the pinned release, admitted by its own sha1), a System Bus image laid out
from data/polished/polished_slink.sym holding a party built with server/adapters/polished_codec.py, and a
CartRAM save image the census accepts (sSaveVersion + sChecksum). Assertions are on the actual WRAM/SRAM bytes
and on the io write log afterwards, never on a return value alone.

Proved here:
  * force_faint zeroes the KEYED record's HP and Status and touches no other record (the death-sync command)
  * it refuses when the hold does not hold - in battle, a script running, a native save, a backup save, a link
    cable, a map that is not in MAPSTATUS_HANDLE - and when the key is not in the party
  * force_faint does NOT refuse the last party mon: the vanilla contract refuses that only in the BOX ops
    (lua/gen2/boxes.lua "last party mon"), and this pins the difference
  * box_mon moves the keyed mon: party count -1, a compacted party, a pokedb entry whose checksum verifies and
    decodes with the same key, the WRAM allocation flag and the box pointer all set
  * box_mon refuses: the key is absent, the party would be emptied, the census is incomplete, every
    non-memorial box is full
  * not one byte is written outside the declared permit ranges (the whole io write log is audited against
    ranges derived here from the sym and NEWBOX's own geometry, independently of the module under test)
  * RED CONTROL per brake (hold gate / read-back / permit / checksum): each is applied as a source mutation
    and the corresponding property is shown to break
"""
from __future__ import annotations

import json
import random
import re
from pathlib import Path

import pytest

from server.adapters import polished_codec as pc
from tests.unit.test_polished_boxes import FLAG_FLAT, SECTION_FLAT, Image
from tests.unit.test_polished_boxes_census import CHECKSUM_AT, GAME_AT, GAME_END, VERSION_AT
from tests.unit.test_polished_client import _entry, _pair
from tests.unit.test_polished_lua import ROOT, _mon, _real  # noqa: I001 (isort: project grouping)

lupa = pytest.importorskip("lupa")


_OVERLAY = None


def overlay():
    """The real overlay ROM (the pinned release with SLink-Polished.ups applied), admitted by its own sha1."""
    global _OVERLAY
    if _OVERLAY is None:
        _OVERLAY = _real()
    return _OVERLAY

REPO = Path(ROOT)
SYM = {name: (int(bank, 16), int(addr, 16))
       for bank, addr, name in re.findall(r"^([0-9a-f]{2}):([0-9a-f]{4}) (\S+)$",
                                          (REPO / "data/polished/polished_slink.sym").read_text(encoding="utf-8"),
                                          re.M)}

PARTY_COUNT, PARTY_MONS = SYM["wPartyCount"][1], SYM["wPartyMons"][1]
PARTY_END = SYM["wPartyMonNicknamesEnd"][1]
STATUS, HP, RECORD = 32, 34, 48
NAME, MON_NAME = 11, 11
MEMORIAL_BOX, PER_BOX, SECTION_SIZE = 20, 20, 49
HOLD = {"bank": 0x25, "pc": 0x51BF, "bytes": (0xCC, 0xA8, 0x0D), "start": 0x5185, "end": 0x51D7}
SAVING_FLAT = SYM["sWritingBackup"][1] - 0xA000
MONSTERS = [[("A", 1, 167), ("B", 168, 195), ("C", 196, 207)]][0]


def entry_flat(d, e):
    for letter, low, high in MONSTERS:
        if low <= e <= high:
            return SECTION_FLAT[(d, letter)] + SECTION_SIZE * (e - low)
    raise AssertionError(f"pokedb entry {e} outside 1..207")


def box_flat(n):
    bank, addr = SYM["sNewBox1"]
    return bank * 0x2000 + addr - 0xA000 + 0x21 * (n - 1)


# ── the declared permit ranges, derived here and NOT from the module under test ───────────────────────────

def allowed():
    """{domain: [(start, end)]}: the party block, the six newbox pokedb sections, the 20 GAMEPLAY box records
    and the two allocation-flag windows. NEWBOX §1-§2 (the backup copies are not writable)."""
    cart = [(entry_flat(d, low), entry_flat(d, high))
            for d in (1, 2) for _, low, high in MONSTERS]
    cart += [(box_flat(n), box_flat(n) + 0x21) for n in range(1, MEMORIAL_BOX + 1)]
    return {"System Bus": [(PARTY_COUNT, PARTY_END)], "CartRAM": cart,
            "WRAM": [(FLAG_FLAT[1], FLAG_FLAT[1] + 26), (FLAG_FLAT[2], FLAG_FLAT[2] + 26)]}


def outside(addr, domain, span=1):
    return not any(start <= addr and addr + span <= end for start, end in allowed()[domain])


# ── the io harness ───────────────────────────────────────────────────────────────────────────────────────

HARNESS = """
return function(rom, mem, image)
    local log = {hooks={}, writes={}, sent={}, lines={}}
    local io = {frame=0, cart_ram_linear=true}
    function io.read_u8(a, d)
        if d == "ROM" then return rom:byte(a + 1) end
        if d == "CartRAM" or d == "WRAM" then return image.read(a, d) end
        return mem[a] or 0
    end
    function io.read_range(a, n, d) local out = {} for i = 1, n do out[i] = io.read_u8(a + i - 1, d) end return out end
    function io.write_u8(a, v, d)
        d = d or "System Bus"
        log.writes[#log.writes + 1] = {addr=a, domain=d}
        if d == "CartRAM" or d == "WRAM" then image.write(a, v, d) else mem[a] = v end
    end
    function io.bank_valid() return true end
    function io.domain_size(d) return d == "ROM" and #rom or 0x8000 end
    function io.framecount() return io.frame end
    function io.on_bus_exec(fn, addr, name) log.hooks[#log.hooks + 1] = name return #log.hooks end
    function io.unregister() end
    function io.register() return 0 end
    local net = {connected=function() return true end, pump=function() end, receive=function() return nil end,
                 send=function(line) log.sent[#log.sent + 1] = line return true end}
    local hud = {show=function() end, nuzlocke_start=function() end, sanitize=function(s) return s end}
    local deps = {root=ROOTDIR, title="polished", io=io, net=net, hud=hud, player="a", rom_size=#rom,
                  read_rom_u8=function(a) return rom:byte(a + 1) end,
                  log=function(t) log.lines[#log.lines + 1] = t end}
    return deps, io, log
end
"""

MODULE = (REPO / "lua/gen2/polished_overworld.lua").read_text(encoding="utf-8")
BOXES = (REPO / "lua/gen2/polished_boxes.lua").read_text(encoding="utf-8")


def seal_save(img, seed=5):
    cart = img.mem["CartRAM"]
    cart[VERSION_AT:VERSION_AT + 2] = b"\x00\x0a"
    cart[GAME_AT:GAME_END] = random.Random(seed).randbytes(GAME_END - GAME_AT)
    resum(img)
    return img


def resum(img):
    cart = img.mem["CartRAM"]
    cart[CHECKSUM_AT:CHECKSUM_AT + 2] = (sum(cart[GAME_AT:GAME_END]) & 0xFFFF).to_bytes(2, "little")


def live_mon(rng, species, hp=300, ot="KRIS", nick="MON"):
    """A party mon the codec can encode (and key), with a known HP so a faint is observable."""
    mon = _mon(rng, species)
    mon.update(is_egg=False, hp=hp, max_hp=hp, ot_name=ot, nickname=nick,
               ot_raw_hex=pc.encode_text(ot, 8).hex(), nickname_raw_hex=pc.encode_text(nick, 11).hex(),
               stats=dict.fromkeys(pc.STAT_NAMES[1:], 100))
    mon["dv_bytes"] = int("".join(f"{mon['dvs'][n]:X}" for n in pc.STAT_NAMES), 16)
    return mon


def party(n=3):
    return [live_mon(random.Random(77 + i), 25 + i, ot="KRIS", nick=f"MON{i}") for i in range(n)]


def key_of(mon):
    return pc.key(mon)


def entry_for(mon):
    """A sealed 49-byte newbox entry of that mon (the deposit's own product, built independently)."""
    blob = (pc.encode_party_mon(mon) + bytes.fromhex(mon["ot_raw_hex"]).ljust(NAME, b"\x00")
            + bytes.fromhex(mon["nickname_raw_hex"]))
    decoded = pc.decode_party_blob(blob)
    return pc.seal(pc.party_to_savemon(decoded))


def sysbus(mons):
    """The System Bus image of a party, laid out at the overlay .sym's own addresses (as the client's own
    helper does), with the overworld gate in MAPSTATUS_HANDLE and a live identity."""
    mem = {}

    def put(label, data, offset=0):
        base = SYM[label][1]
        for i, value in enumerate(data):
            mem[base + offset + i] = value

    put("wPartyCount", [len(mons)])
    for slot, mon in enumerate(mons):
        put("wPartyMons", pc.encode_party_mon(mon), slot * RECORD)
        put("wPartyMonOTs", pc.encode_text(mon["ot_name"], 8) + bytes(3), slot * NAME)
        put("wPartyMonNicknames", pc.encode_text(mon["nickname"], MON_NAME), slot * MON_NAME)
    put("wPlayerID", (0x30B8).to_bytes(2, "big"))
    put("wPlayerName", pc.encode_text("KRIS", 11))
    put("wNumBalls", [1, 1, 5, 0xFF])
    put("wJohtoBadges", [0x03, 0x00])
    put("wMapGroup", [24, 4, 5, 6])
    put("wMapStatus", [2])                                   # MAPSTATUS_HANDLE: the overworld loop runs
    put("hROMBank", [HOLD["bank"]])                          # the CPU sits in the overworld bank (the hold site)
    put("wSlinkMailbox", [0x53, 0x4C, 0x4E, 0x4B, 3, 0, 0, 0, 0])
    put("wSlinkMailbox", [0xA5], 31)
    return mem


def plant(img, box, slot, bank, index, raw):
    """Point (box, slot) at pokedb bank/index, set its WRAM allocation flag, write the entry bytes."""
    rec, cart = box_flat(box), img.mem["CartRAM"]
    cart[rec + slot - 1] = index
    bits = rec + 0x14 + ((slot - 1) >> 3)
    cart[bits] = (cart[bits] | (1 << ((slot - 1) & 7))) if bank == 2 else (cart[bits] & ~(1 << ((slot - 1) & 7)))
    at = entry_flat(bank, index)
    cart[at:at + SECTION_SIZE] = raw
    img.mem["WRAM"][FLAG_FLAT[bank] + ((index - 1) >> 3)] |= 1 << ((index - 1) & 7)


def fill(img, box, first_entry, count=PER_BOX):
    """Fill a box's slots with valid entries, pokedb bank 1 then bank 2 as the entry numbers run out."""
    entry = first_entry
    for slot in range(1, count + 1):
        bank, index = (1, entry) if entry <= 207 else (2, entry - 207)
        plant(img, box, slot, bank, index, entry_for(live_mon(random.Random(entry), 25 + entry % 200,
                                                               ot="KRIS", nick=f"B{entry % 100:02d}")))
        entry += 1
    return entry


class Rig:
    """The composed Polished client over a System Bus table and an accepted CartRAM/WRAM save image."""

    def __init__(self, mons, img=None, sources=None, rom=None):
        self.lua = lupa.LuaRuntime(unpack_returned_tuples=True)
        self.mem = self.lua.table_from(sysbus(mons))
        self.img = img if img is not None else seal_save(Image())
        deps, self.io, self.log = self.lua.execute(HARNESS.replace("ROOTDIR", json.dumps(ROOT)))(
            rom if rom is not None else overlay()[1], self.mem, self.img)
        if sources:
            self._patch(sources)
        self.parts, why = _pair(_entry(self.lua).build(deps))
        assert why is None, why
        self.client = self.parts.client
        self.lua.globals().SLINK_PARTS = self.parts        # a real Lua table: reads/party come back as Lua values
        self.client.start(self.client)

    def _patch(self, sources):
        """Prepend an overridden module body: the RED CONTROL route (a mutated source, same wiring)."""
        for name, source in sources.items():
            self.lua.execute(f"local REAL = dofile({json.dumps(ROOT + '/lua/gen2/' + name)})")
            self.lua.execute("local OVERRIDE = " + source)
            self.lua.execute(f"_G.SLINK_OVERRIDE_{name} = OVERRIDE")

    def frame(self, n=1):
        for _ in range(n):
            self.io.frame += 1
            self.client.frame_end(self.client)

    def send_command(self, cmd):
        """The client only runs a deferred command once its writes are enabled, which client.validate() does on
        its VALIDATE_EVERY cadence (60 frames) once the game reads live. Run to that point, then dispatch."""
        self.arm_writes()
        self.client.handle_command(self.client, self.lua.table_from(cmd))
        self.frame(2)

    def arm_writes(self, limit=200):
        for _ in range(limit):
            if self.client.writes_enabled:
                return True
            self.frame(1)
        return False

    def sent(self, event=None):
        return [json.loads(line) for line in self.log["sent"].values()
                if event is None or json.loads(line)["event"] == event]

    def writes(self):
        return [dict(w) for w in self.log["writes"].values()]

    def hp(self, slot):
        at = PARTY_MONS + slot * RECORD + HP
        return ((self.mem[at] or 0) << 8) | (self.mem[at + 1] or 0)

    def status(self, slot):
        return self.mem[PARTY_MONS + slot * RECORD + STATUS] or 0

    def count(self):
        return self.mem[PARTY_COUNT] or 0

    def overworld(self):
        return self.parts.overworld

    def census_keys(self):
        """Every box the composed census reads, as {box: [keys]} (or None when it refuses)."""
        census = self.parts.overworld.census
        out = {}
        for index in range(20):
            read, why = _pair(self.parts.client.config and None or census.read_storage_box(index))
            if not read:
                return None, why
            out[index + 1] = [mon["key"] for mon in read["mons"].values()]
        return out, None


# ── the composition ───────────────────────────────────────────────────────────────────────────────────────

def test_the_overworld_path_is_composed():
    rig = Rig(party())
    over = rig.overworld()
    assert over.writes.armed is None
    assert over.boxes.memorial_box == MEMORIAL_BOX
    assert over.checkpoint.qualification == "DEV_OVERLAY_PREDICATE_HOLD"
    assert rig.parts.checkpoint is None                # no PC hold exists to compose
    rig.frame(5)
    assert rig.writes() == []                          # nothing without a command


def test_the_hold_refuses_a_kind_this_graph_does_not_compose():
    hold = Rig(party()).overworld().checkpoint
    assert _pair(hold.check(hold, "battle_bench"))[0] is False
    assert _pair(hold.check(hold, "party_hp"))[0] is True


# ── force_faint ───────────────────────────────────────────────────────────────────────────────────────────

def test_force_faint_zeroes_the_keyed_record_only():
    mons = party()
    key = key_of(mons[1])
    rig = Rig(mons)
    rig.send_command({"cmd": "force_faint", "key": key, "nickname": "PIKA"})
    assert rig.hp(1) == 0 and rig.status(1) == 0
    assert rig.hp(0) == 300 and rig.hp(2) == 300      # the other records are untouched
    assert rig.count() == len(mons)
    for w in rig.writes():
        assert not outside(w["addr"], w["domain"]), w
        assert PARTY_MONS <= w["addr"] <= PARTY_END


@pytest.mark.parametrize("byte,value,why", [
    ("wBattleMode", 1, "in battle"),
    ("wScriptRunning", 1, "script"),
    ("wGameLogicPaused", 1, "save"),
    ("wLinkMode", 1, "link"),
    ("wMapStatus", 1, "overworld loop"),
])
def test_a_hold_that_does_not_hold_refuses_the_faint(byte, value, why):
    mons = party()
    rig = Rig(mons)
    rig.mem[SYM[byte][1]] = value
    rig.send_command({"cmd": "force_faint", "key": key_of(mons[1]), "nickname": "PIKA"})
    assert rig.hp(1) == 300, f"the faint landed with {why} in effect"
    assert rig.writes() == []


def test_a_backup_save_refuses_the_faint():
    mons = party()
    rig = Rig(mons)
    rig.img.mem["CartRAM"][SAVING_FLAT] = 1
    rig.send_command({"cmd": "force_faint", "key": key_of(mons[1]), "nickname": "PIKA"})
    assert rig.hp(1) == 300 and rig.writes() == []


def test_an_unknown_key_is_never_written():
    rig = Rig(party())
    rig.send_command({"cmd": "force_faint", "key": "ABCDEF:1234:010:00"})
    assert rig.writes() == [] and all(rig.hp(slot) == 300 for slot in range(3))


def test_force_faint_does_not_refuse_the_last_party_mon():
    """The vanilla contract refuses an empty party only in the BOX ops (lua/gen2/boxes.lua:460): a death sync
    must still land on the last mon."""
    mons = party(1)
    rig = Rig(mons)
    rig.send_command({"cmd": "force_faint", "key": key_of(mons[0])})
    assert rig.hp(0) == 0


# ── box_mon ─────────────────────────────────────────────────────────────────────────────────────────────────

def test_box_mon_moves_the_keyed_mon_into_the_first_free_box():
    mons = party()
    key = key_of(mons[1])
    rig = Rig(mons)
    rig.send_command({"cmd": "box_mon", "key": key})
    # the party
    assert rig.count() == 2
    # the compaction the engine does (ShiftPartySlotToEnd): the old slot 2 is now slot 1, the old slot 1 is gone
    assert rig.hp(0) == 300 and rig.hp(1) == 300 and rig.hp(2) == 0
    boxes, why = rig.census_keys()
    assert boxes is not None, why
    assert key in boxes[1] and key not in boxes[2]
    # the pokedb entry the game will decode
    entry = rig.img.mem["CartRAM"][entry_flat(1, 1):entry_flat(1, 1) + SECTION_SIZE]
    assert pc.verify(bytes(entry)), "the written entry is a Bad Egg"
    decoded = pc.decode_savemon(bytes(entry))
    assert pc.key(decoded) == key and decoded["species_id"] == mons[1]["species_id"]
    assert decoded["level"] == mons[1]["level"]
    # the WRAM allocation flag and the box pointer
    assert rig.img.mem["WRAM"][FLAG_FLAT[1]] & 1 == 1
    assert rig.img.mem["CartRAM"][box_flat(1)] == 1
    assert rig.img.mem["CartRAM"][box_flat(1) + 1] == 0
    # nothing outside the declared ranges
    for w in rig.writes():
        assert not outside(w["addr"], w["domain"]), w


def test_box_mon_compaction_changes_only_the_removed_slot_and_the_ones_after_it():
    """CODEX P1 (2026-10-05): removed() started each shift one byte EARLY (a zero-based offset used as a 1-based table
    index), so depositing slot s also overwrote the LAST byte of slot s-1 (Sp.Def low byte), the last byte of its OT
    field (the Extra byte) and its nickname terminator. Compare EVERY byte of the party block with an independent
    reference compaction: count - 1; slots before the removed one byte-identical; slot k >= s takes slot k+1's bytes
    in all three arrays.

    Red control (applied): start the shift loop at `from` instead of `from + 1` in removed() and this fails on the
    byte just before the removed slot."""
    mons = party(4)
    for slot, mon in enumerate(mons):
        mon["stats"]["special_defense"] = 100 + slot   # distinct LAST record bytes: the old defect copied the removed slot's
    rig = Rig(mons)
    base = {name: SYM[name][1] for name in ("wPartyCount", "wPartyMons", "wPartyMonOTs", "wPartyMonNicknames")}

    def block():
        out = {"count": rig.mem[base["wPartyCount"]] or 0}
        for name, width in (("wPartyMons", RECORD), ("wPartyMonOTs", NAME), ("wPartyMonNicknames", MON_NAME)):
            out[name] = [bytes((rig.mem[base[name] + slot * width + i] or 0) for i in range(width))
                         for slot in range(6)]
        return out

    before = block()
    removed_slot = 1
    rig.send_command({"cmd": "box_mon", "key": key_of(mons[removed_slot])})
    after = block()
    assert after["count"] == before["count"] - 1
    for name in ("wPartyMons", "wPartyMonOTs", "wPartyMonNicknames"):
        for slot in range(after["count"]):
            expected = before[name][slot if slot < removed_slot else slot + 1]
            assert after[name][slot] == expected, (name, slot, after[name][slot].hex(), expected.hex())


def test_box_mon_never_uses_the_memorial_box():
    mons = party()
    rig = Rig(mons)
    rig.send_command({"cmd": "box_mon", "key": key_of(mons[0])})
    boxes, _ = rig.census_keys()
    assert key_of(mons[0]) in boxes[1]
    assert boxes[MEMORIAL_BOX] == []


def test_box_mon_refuses_an_absent_key():
    rig = Rig(party())
    rig.send_command({"cmd": "box_mon", "key": "ABCDEF:1234:010:00"})
    assert rig.count() == 3 and rig.writes() == []
    assert [m["reason"] for m in rig.sent("box_mon_failed")] == ["key not in party"]


def test_box_mon_refuses_to_empty_the_party():
    mons = party(1)
    rig = Rig(mons)
    rig.send_command({"cmd": "box_mon", "key": key_of(mons[0])})
    assert rig.count() == 1 and rig.writes() == []
    assert [m["reason"] for m in rig.sent("box_mon_failed")] == ["last party mon"]


def test_box_mon_refuses_an_incomplete_census():
    """No save anchors: the census is not a census, so no pokedb byte may be written."""
    rig = Rig(party(), img=Image())                   # an all-zero CartRAM/WRAM image
    rig.send_command({"cmd": "box_mon", "key": pc.key(party()[0])})
    assert rig.count() == 3
    assert rig.writes() == []
    assert rig.sent("box_mon_failed")[0]["reason"].startswith("box census incomplete")


def test_box_mon_refuses_when_every_box_is_full():
    img = seal_save(Image())
    entry = 1
    for box in range(1, MEMORIAL_BOX):               # every non-memorial box full
        entry = fill(img, box, entry)
    mons = party()
    rig = Rig(mons, img=img)
    rig.send_command({"cmd": "box_mon", "key": key_of(mons[0])})
    assert rig.count() == 3 and rig.writes() == []
    assert rig.sent("box_mon_failed")[0]["reason"] == "no free box slot"


def test_party_mon_and_memorialize_refuse_an_absent_key_by_name():
    rig = Rig(party())
    # party_mon is composed (card POL-WDEXEC): with an empty box census the key is genuinely not there
    assert _pair(rig.overworld().boxes.withdraw())[0] is None
    assert "key not boxed" in _pair(rig.overworld().boxes.withdraw())[1]
    # memorialize is composed (owner ruling 2026-10-09, docs/polished/MEMORIALIZE.md): absent everywhere refuses
    assert _pair(rig.overworld().boxes.memorialize())[0] is None
    assert _pair(rig.overworld().boxes.memorialize())[1] == "key not in party or boxes"


# ── the permit ─────────────────────────────────────────────────────────────────────────────────────────────

def test_the_permit_refuses_a_byte_outside_every_declared_range():
    """Direct: an armed permit will not write one byte of wMirrorHerbPendingBoosts (01:d284) or of the backup
    box copies, even when the writer asks. RED CONTROL: bounds always true -> the byte lands (see below)."""
    rig = Rig(party())
    writes = rig.overworld().writes
    herb = SYM["wMirrorHerbPendingBoosts"][1]
    backup = SYM["sBackupNewBox1"][0] * 0x2000 + SYM["sBackupNewBox1"][1] - 0xA000
    for reason, domain, addr in (("overworld", "System Bus", herb), ("box_deposit", "CartRAM", backup),
                                 ("box_deposit", "WRAM", FLAG_FLAT[1] - 1)):
        writes.arm(writes, reason)
        with pytest.raises(Exception) as raised:
            writes.write_batch(writes, _span(rig.lua, domain, addr, 0x5A))
        writes.disarm(writes)
        assert "write refused" in str(raised.value)
    assert rig.writes() == []


# ── RED CONTROL: each brake, applied and reverted ─────────────────────────────────────────────────────────
#
# Each mutation is applied to a COPY of the module source and built over the SAME image and io as the composed
# graph, so the difference in outcome is the brake and nothing else. The composed graph is re-checked in each
# test, so a brake that stopped working would show up as the unmutated path behaving the same way.

RED = """
return function(rig, source, boxes_source)
    local parts, io = rig.parts, rig.io
    local fn = rig.lua.eval("function(src) return assert(load(src, 'mutant'))() end")
    local Overworld = fn(source)
    local Boxes = boxes_source and fn(boxes_source) or dofile(ROOTDIR .. "/lua/gen2/polished_boxes.lua")
    local P = dofile(ROOTDIR .. "/lua/gen2/polished.lua")
    assert(P.load_variants(parts.profile.derived.variant_forms))
    local Permit = dofile(ROOTDIR .. "/lua/write_permit.lua")
    local coords, census, reads = parts.overworld.coords, parts.overworld.census, parts.reads
    local reader = assert(Boxes.new(coords, io, P.mon_key, nil))
    local checkpoint = Overworld.checkpoint(parts.profile, io, nil, Overworld.HOLD)
    local policy = {
        authorize = function(op) return Overworld.KINDS[op] == true and checkpoint:check(op) == true end,
        pointer_stable = function() return true end,
        lifetime = {capture = function() return io.framecount() end, valid = function(t) return t == io.framecount() end},
        provenance = function() return {site = "red control"} end,
    }
    local writes = Overworld.writes(parts.profile, coords, io, Permit, policy, checkpoint)
    local boxes = Overworld.boxes({profile = parts.profile, reads = reads, census = census, boxes = Boxes,
                                   reader = reader, writes = writes})
    return {checkpoint = checkpoint, writes = writes, boxes = boxes}
end
"""


def _mutant(rig, source=None, boxes_source=None):
    """Build the write path from a mutated module source over the rig's own image and io."""
    loader = rig.lua.execute(RED.replace("ROOTDIR", json.dumps(ROOT)))
    return loader(rig, source or MODULE, boxes_source)


DEPOSIT = """
function(boxes, slot)
    local key = SLINK_PARTS.reads.read_party().mons[slot + 1].key
    return boxes.deposit(key)
end
"""


def _deposit(lua, boxes, slot=0):
    """boxes:deposit(<the live key of party slot `slot`>): the key is read inside Lua, so it is the client's own
    Lua string rather than a Python-side wrapper."""
    return lua.eval(DEPOSIT)(boxes, slot)


def _span(lua, domain, addr, value):
    """One write span as a Lua array of plain span tables (the permit's shape)."""
    return lua.eval("function(domain, addr, value)"
                    "  return {{domain = domain, addr = addr, bytes = {value}}}"
                    "end")(domain, addr, value)


def _faint_through(rig, path, slot):
    """Arm a (possibly mutated) writer and land one faint through it, exactly as client.run_deferred does."""
    return _pair(rig.lua.eval("""
        function(writes, slot)
            local snapshot = {mode = 0, link_mode = 0}
            writes.arm(writes, "overworld")
            local ok, result = pcall(function() return writes:faint_party_slot(slot, snapshot) end)
            writes.disarm(writes)
            return ok, result
        end
    """)(path.writes, slot))


def test_red_control_skip_the_hold_gate_writes_on_a_loading_map():
    """RED 1: the map-status gate removed. The composed graph refuses a faint while the map is still loading;
    the mutant writes HP 0 anyway."""
    mons = party()
    rig = Rig(mons)
    rig.mem[SYM["wMapStatus"][1]] = 1                  # MAPSTATUS_ENTER: the map is still loading
    # the composed path: the hold refuses
    rig.send_command({"cmd": "force_faint", "key": key_of(mons[1])})
    assert rig.hp(1) == 300 and rig.writes() == []
    # the mutant: check() accepts a map that is still loading
    rig.mem[SYM["wMapStatus"][1]] = 1                  # MAPSTATUS_ENTER: the map is still loading
    source = MODULE.replace("if self.facts.wMapStatus ~= O.MAPSTATUS_HANDLE then", "if false then")
    assert source != MODULE
    path = _mutant(rig, source)
    ok, _ = _faint_through(rig, path, 1)
    assert ok and rig.hp(1) == 0, "the hold gate is not load-bearing"


def test_red_control_skip_the_read_back_hides_a_failed_write():
    """RED 2: the read-back removed AND the write aimed one record off. The composed writer proves the byte it
    wrote; the mutant reports success while the keyed record still has HP."""
    mons = party()
    rig = Rig(mons)
    writes = rig.overworld().writes
    class _W:
        writes = None
    holder = _W()
    holder.writes = writes
    _faint_through(rig, holder, 1)
    assert rig.hp(1) == 0                       # the real read-back: the bytes are there
    rig2 = Rig(party())
    source = MODULE.replace("local at = base + slot * s.End", "local at = base + (slot + 1) * s.End")
    assert source != MODULE
    path = _mutant(rig2, source)
    ok, _ = _faint_through(rig2, path, 1)
    assert ok, "the mutant write itself should succeed"
    assert rig2.hp(1) == 300, "the read-back would have caught this"


def test_red_control_an_open_permit_writes_outside_every_declared_range():
    """RED 3: the permit bounds opened. The composed permit refuses a byte in wMirrorHerbPendingBoosts; the
    mutant writes it."""
    rig = Rig(party())
    writes = rig.overworld().writes
    herb = SYM["wMirrorHerbPendingBoosts"][1]
    writes.arm(writes, "overworld")
    with pytest.raises(Exception) as raised:
        writes.write_batch(writes, _span(rig.lua, "System Bus", herb, 0x5A))
    writes.disarm(writes)
    assert "write refused" in str(raised.value)
    source = MODULE.replace(
        "bounds = function(addr, n, reason) return permitted(reason, addr, n) == true end,",
        "bounds = function(addr, n, reason) return true end,").replace(
        "                mapped = function(addr, n, reason)\n"
        "                    local r = declared(reason, addr, n)\n"
        "                    return r ~= nil and io.bank_valid(r.bank, addr, n) == true\n"
        "                end,",
        "                mapped = function(addr, n, reason) return true end,")
    assert source != MODULE
    path = _mutant(rig, source)
    open_writes = path.writes
    open_writes.arm(writes, "overworld")
    open_writes.write_batch(open_writes, _span(rig.lua, "System Bus", herb, 0x5A))
    open_writes.disarm(writes)
    assert rig.mem[herb] == 0x5A, "the permit bounds are not load-bearing"
    assert outside(herb, "System Bus")


def test_red_control_an_unsealed_entry_is_refused_by_the_read_back():
    """RED 4: the box entry is written without its checksum. The deposit's census read-back refuses (a Bad Egg
    makes the census incomplete), so the party half never runs; with the seal it lands."""
    mons = party()
    unsealed = BOXES.replace("    return B.seal(e)\nend", "    return e\nend")
    assert unsealed != BOXES
    rig = Rig(mons)
    path = _mutant(rig, None, unsealed)
    rig.lua.globals().SLINK_PARTS = rig.parts
    done, why = _pair(_deposit(rig.lua, path.boxes, 0))
    assert done is None and "read-back" in why
    # the composed path seals it and the same deposit lands
    rig2 = Rig(party())                              # a second, identical party over a second image
    boxes = rig2.overworld().boxes
    done, note = _pair(_deposit(rig2.lua, boxes, 0))
    assert done is True and note["durability"] == "VOLATILE_UNTIL_NATIVE_SAVE"


# ── the hold SITE: a PC hold, pinned to NextOverworldFrame's idle wait ──────────────────────────────────

def test_the_hold_site_is_the_idle_overworld_call_inside_the_named_routine():
    """25:51BF is `call z, DelayFrame` inside NextOverworldFrame (engine/overworld/events.asm:114). The routine's
    own bounds come from the .sym, the bytes from the EXECUTED overlay ROM, and the byte triple must occur exactly
    once in the routine - a second `call DelayFrame` would make the site ambiguous."""
    sym = SYM["NextOverworldFrame"], SYM["DelayFrame"], SYM["OverworldLoop"]
    assert (sym[0][0], sym[0][1]) == (HOLD["bank"], HOLD["start"])
    assert sym[1][1] == 0x0DA8
    assert (HOLD["start"], HOLD["end"]) == (0x5185, SYM["HandleMapObjects"][1])
    rom = overlay()[1]
    at = HOLD["bank"] * 0x4000 + (HOLD["pc"] - 0x4000)
    assert rom[at:at + 3] == bytes(HOLD["bytes"])
    assert HOLD["pc"] < SYM["HandleMapObjects"][1]          # the site precedes the next routine in the bank
    span = rom[HOLD["bank"] * 0x4000 + (HOLD["start"] - 0x4000):HOLD["bank"] * 0x4000 + (HOLD["end"] - 0x4000)]
    assert span.count(bytes(HOLD["bytes"])) == 1, "the hold instruction is not unique in NextOverworldFrame"


def test_the_client_hooks_the_hold_site():
    rig = Rig(party())
    rig.client.start(rig.client)
    assert rig.client.checkpoint_hook is not None
    assert "SLink-gen2-checkpoint" in rig.log["hooks"].values()


def test_the_hold_refuses_a_bank_that_is_not_the_overworld():
    rig = Rig(party())
    rig.mem[SYM["hROMBank"][1]] = 0x21
    hold = rig.overworld().checkpoint
    assert _pair(hold.check(hold, "party_hp"))[0] is False
    assert "NextOverworldFrame" in _pair(hold.check(hold, "party_hp"))[1]


def test_the_hold_refuses_patched_bytes():
    """The instruction bytes are re-read from the EXECUTED ROM at check time, so a cartridge whose NextOverworldFrame
    no longer holds `call z, DelayFrame` never takes a write."""
    patched = bytearray(overlay()[1])
    patched[HOLD["bank"] * 0x4000 + (HOLD["pc"] - 0x4000)] = 0x00
    rig = Rig(party(), rom=bytes(patched))
    hold = rig.overworld().checkpoint
    assert _pair(hold.check(hold, "party_hp"))[0] is False
    assert "hold site bytes differ" in _pair(hold.check(hold, "party_hp"))[1]


def test_arm_rechecks_the_hold():
    """Round-2 item 5: the box path writes through the permit, so arm() itself must re-prove the hold."""
    rig = Rig(party())
    writes = rig.overworld().writes
    writes.arm(writes, "box_deposit")
    writes.disarm(writes)
    rig.mem[SYM["wBattleMode"][1]] = 1
    with pytest.raises(Exception) as raised:
        writes.arm(writes, "box_deposit")
    assert "write refused at arm" in str(raised.value)


# ── mail: sPartyMon1Mail is never shifted (engine/pc/bills_pc.asm:289-297) ───────────────────────────────

def test_a_party_holding_mail_cannot_be_deposited():
    mons = party()
    mons[2]["held_item"] = 0xF5                      # FIRST_MAIL: the lowest id of items.json mail_ids
    rig = Rig(mons)
    rig.send_command({"cmd": "box_mon", "key": key_of(mons[1])})
    assert rig.count() == 3 and rig.writes() == []
    assert "sPartyMail is never shifted" in rig.sent("box_mon_failed")[0]["reason"]


def test_mail_after_the_removed_slot_refuses_too():
    """The engine's swap walks every later slot, so mail in a LATER slot is refused as well (vanilla no_mail_from)."""
    mons = party()
    mons[2]["held_item"] = 0xFA
    rig = Rig(mons)
    rig.send_command({"cmd": "box_mon", "key": key_of(mons[0])})
    assert rig.count() == 3 and rig.writes() == []
    assert "slot 2" in rig.sent("box_mon_failed")[0]["reason"]


def test_red_control_without_the_mail_table_the_deposit_lands():
    """RED: the mail predicate removed -> the deposit of a party whose slot 2 holds Mail goes through, and that
    mon's mail record is left bound to the wrong slot (the defect the refusal exists for)."""
    mons = party()
    mons[2]["held_item"] = 0xF5
    rig = Rig(mons)
    source = MODULE.replace("if later.slot >= slot and mail[later.held_item] then", "if false then")
    assert source != MODULE
    path = _mutant(rig, source)
    rig.lua.globals().SLINK_PARTS = rig.parts
    done, _ = _pair(_deposit(rig.lua, path.boxes, 1))
    assert done is True, "the mail refusal is not load-bearing"


# ── deposit hardening (Codex review 2026-10-05): read-back before the party is touched, count written last ───

def _party_bytes(rig):
    return [rig.mem[a] or 0 for a in range(PARTY_COUNT, PARTY_END)]


def _drop_one_entry_byte(rig):
    """Make the CartRAM writer silently DROP one byte of the first pokedb entry (bank 1, entry 1) - the box half
    lands everywhere else. Returns a function that restores the real writer."""
    at = entry_flat(1, 1) + 10
    real = rig.img.write

    def write(offset, value, domain):
        if domain == "CartRAM" and offset == at:
            assert rig.img.mem["CartRAM"][at] != value, "the dropped byte must differ from what is there"
            return
        real(offset, value, domain)

    rig.img.write = write

    def restore():
        rig.img.write = real

    return restore


def _published(rig):
    """(allocation flag set, Entries pointer, Banks byte) of box 1 slot 1 / pokedb bank 1 entry 1."""
    return (rig.img.mem["WRAM"][FLAG_FLAT[1]] & 1, rig.img.mem["CartRAM"][box_flat(1)],
            rig.img.mem["CartRAM"][box_flat(1) + 0x14])


def test_a_dropped_entry_byte_refuses_the_deposit_and_publishes_nothing():
    """CODEX P1+P2: the entry is written and read back BEFORE the flag/pointer/Banks bit are published, so a dropped
    entry byte leaves no referenced Bad Egg: the census stays complete and the next deposit goes through."""
    mons = party()
    rig = Rig(mons)
    before, published_before = _party_bytes(rig), _published(rig)
    restore = _drop_one_entry_byte(rig)
    rig.send_command({"cmd": "box_mon", "key": key_of(mons[1])})
    reasons = [m["reason"] for m in rig.sent("box_mon_failed")]
    assert len(reasons) == 1 and "read-back" in reasons[0] and "nothing published" in reasons[0], reasons
    assert _party_bytes(rig) == before and rig.count() == 3
    assert not [w for w in rig.writes() if w["domain"] == "System Bus"], "the party was written"
    assert _published(rig) == published_before == (0, 0, 0), "the flag, pointer or Banks bit was published"
    boxes, why = rig.census_keys()
    assert boxes is not None, why                              # the census still completes: no Bad Egg is referenced
    restore()
    rig.send_command({"cmd": "box_mon", "key": key_of(mons[2])})
    assert [m["reason"] for m in rig.sent("box_mon_failed")] == reasons       # no new failure
    assert rig.count() == 2
    boxes, why = rig.census_keys()
    assert boxes is not None and key_of(mons[2]) in boxes[1], why


def test_red_control_publishing_before_the_entry_is_verified_leaves_a_referenced_bad_egg():
    """RED: step 1's refusal removed -> the dropped byte is published anyway (flag + pointer set over a bad entry);
    the later census read-back still refuses, but the Bad Egg stays referenced."""
    mons = party()
    rig = Rig(mons)
    _drop_one_entry_byte(rig)
    source = MODULE.replace("if bad1 then", "if false then")
    assert source != MODULE
    path = _mutant(rig, source)
    rig.lua.globals().SLINK_PARTS = rig.parts
    done, why = _pair(_deposit(rig.lua, path.boxes, 1))
    assert done is None and "read-back" in why
    assert _published(rig)[:2] == (1, 1), "step 1 is not load-bearing"


def test_red_control_without_the_deposit_read_back_the_party_loses_the_mon():
    """RED: both read-back refusals removed -> the same dropped byte lets the party half run (the later census
    read-back catches it, but only after the original is gone from the party)."""
    mons = party()
    rig = Rig(mons)
    before = _party_bytes(rig)
    _drop_one_entry_byte(rig)
    source = MODULE.replace("if bad1 then", "if false then").replace(
        'if bad then refuse("deposit read-back refused: " .. bad .. PUBLISHED) end', "")
    assert source != MODULE and source.count("if false then") == MODULE.count("if false then") + 1
    path = _mutant(rig, source)
    rig.lua.globals().SLINK_PARTS = rig.parts
    _deposit(rig.lua, path.boxes, 1)
    assert _party_bytes(rig) != before, "the deposit read-back is not load-bearing"


def test_a_deposit_keeps_the_neighbouring_banks_bits():
    """The Banks byte holds 8 slots' bank bits. Slot 2's bit is pre-set (an empty slot's junk bit, or a pokedb-2
    mon); publishing slot 1 must change ONLY slot 1's bit."""
    mons = party()
    rig = Rig(mons)
    rig.img.mem["CartRAM"][box_flat(1) + 0x14] = 0b10101110
    rig.send_command({"cmd": "box_mon", "key": key_of(mons[1])})
    assert rig.count() == 2 and not rig.sent("box_mon_failed")
    assert rig.img.mem["CartRAM"][box_flat(1) + 0x14] == 0b10101110      # slot 1 is bank 1 (bit 0 clear), rest intact


def test_red_control_writing_the_whole_banks_byte_blindly_clobbers_the_neighbours():
    """RED: bank_span writes a fresh byte holding only the target bit -> the neighbours are lost (and the deposit's
    own neighbour check refuses it)."""
    mons = party()
    rig = Rig(mons)
    rig.img.mem["CartRAM"][box_flat(1) + 0x14] = 0b10101110
    unsafe = BOXES.replace("bytes = {d == 2 and (old | bit) or (old & ~bit & 0xFF)}", "bytes = {d == 2 and bit or 0}")
    assert unsafe != BOXES
    path = _mutant(rig, None, unsafe)
    rig.lua.globals().SLINK_PARTS = rig.parts
    done, why = _pair(_deposit(rig.lua, path.boxes, 1))
    assert rig.img.mem["CartRAM"][box_flat(1) + 0x14] != 0b10101110, "the blind write is not red"
    assert done is None and "neighbouring Banks bits" in why


def test_the_party_count_is_the_last_write_of_a_deposit():
    mons = party()
    rig = Rig(mons)
    rig.send_command({"cmd": "box_mon", "key": key_of(mons[1])})
    assert rig.count() == 2
    writes = rig.writes()
    assert writes[-1] == {"addr": PARTY_COUNT, "domain": "System Bus"}
    assert [w["addr"] for w in writes].count(PARTY_COUNT) == 1
    party_writes = [w for w in writes if w["domain"] == "System Bus"]
    assert party_writes[-1]["addr"] == PARTY_COUNT and len(party_writes) == PARTY_END - PARTY_COUNT


def test_red_control_count_first_is_not_the_last_party_write():
    """RED: the party block written in one span from wPartyCount (the old order) puts the count FIRST."""
    mons = party()
    rig = Rig(mons)
    old = ('gate:write_batch({{domain = "System Bus", addr = block + 1, bytes = rest},\n'
           '                              {domain = "System Bus", addr = block, bytes = {bytes[1]}}})')
    source = MODULE.replace(old, 'gate:write_batch({{domain = "System Bus", addr = block, bytes = bytes}})')
    assert source != MODULE
    path = _mutant(rig, source)
    rig.lua.globals().SLINK_PARTS = rig.parts
    done, _ = _pair(_deposit(rig.lua, path.boxes, 1))
    assert done is True and rig.writes()[-1]["addr"] != PARTY_COUNT, "the count-last order is not load-bearing"


# ── the Bug Catching Contest: a hidden mon's death must not be dropped ────────────────────────────────────

CONTEST_AT = SYM["wStatusFlags2"][1]       # wStatusFlags2 (the profile does not carry it; the pinned .sym does)


def poke(rig, addr, mask, value):
    """read-modify-write one System Bus byte through the Lua table (a missing key reads nil)."""
    rig.mem[addr] = ((rig.mem[addr] or 0) & mask) | value


def test_the_composition_wires_the_contest_mask():
    source = Path(ROOT, "lua/gen2/entry.lua").read_text(encoding="utf-8")
    assert "contest_mask={bank=1, address=0xD7E4, bit=2}" in source      # wStatusFlags2 STATUSFLAGS2_BUG_CONTEST_TIMER_F


def _legacy_death_rig(mons):
    """The SAME explicit-OFF composition for contest positives and their drop control."""
    from unittest.mock import patch

    old = 'local deps = {root=ROOTDIR, title="polished",'
    with patch(__name__ + ".HARNESS", HARNESS.replace(old, old + " polished_active_faint=false, polished_faint_observer=false,")):
        return Rig(mons)


def test_a_ko_during_the_contest_is_held_not_dropped():
    mons = party()
    rig = _legacy_death_rig(mons)
    # the contest's ContestDropOffMons: the party reads empty while the timer flag is set, so the keyed mon is
    # NOT found - exactly the state in which the vanilla client used to drop the death
    rig.mem[PARTY_COUNT] = 0
    poke(rig, CONTEST_AT, 0xFF, 0x04)                     # STATUSFLAGS2_BUG_CONTEST_TIMER_F
    rig.send_command({"cmd": "force_faint", "key": key_of(mons[0])})
    dropped = [line for line in rig.log["lines"].values() if "dropped at the checkpoint" in line]
    assert dropped == [], "the death was dropped instead of held for the contest to return"
    assert all(rig.hp(slot) == 300 for slot in range(3))


def test_the_same_ko_lands_once_the_contest_returns():
    mons = party()
    rig = _legacy_death_rig(mons)
    rig.mem[PARTY_COUNT] = 0
    poke(rig, CONTEST_AT, 0xFF, 0x04)
    rig.send_command({"cmd": "force_faint", "key": key_of(mons[0])})
    assert rig.hp(0) == 300
    poke(rig, CONTEST_AT, 0xFB, 0x00)                   # the flag clears (ContestReturnMons)
    rig.mem[PARTY_COUNT] = len(mons)                    # the party is back
    rig.frame(3)
    assert rig.hp(0) == 0


def test_red_control_without_the_contest_flag_the_explicitly_legacy_death_is_dropped():
    """RED: the SAME state (the party reads empty) with the contest flag CLEAR is dropped as 'key not in party' -
    so the hold in the two tests above is the contest_mask branch and not an accident of the image."""
    mons = party()
    rig = _legacy_death_rig(mons)
    rig.mem[PARTY_COUNT] = 0
    rig.send_command({"cmd": "force_faint", "key": key_of(mons[0])})
    dropped = [line for line in rig.log["lines"].values() if "dropped at the checkpoint" in line]
    assert dropped and "key not in party" in dropped[0]
    assert rig.hp(0) == 300


# ── memorialize (owner ruling 2026-10-09; design of record docs/polished/MEMORIALIZE.md) ───────────────────
#
# Driven through the real client command path (client.lua run_box): a success must reach memorialize_done exactly once
# with no settle queued (a truthy second return would raise or queue one), a refusal memorialize_failed, and
# `last party mon` the client's deferred retry. Every byte is judged on the image, never on a return value.

def _party_arrays(rig):
    out = {"count": rig.mem[PARTY_COUNT] or 0}
    for name, width in (("wPartyMons", RECORD), ("wPartyMonOTs", NAME), ("wPartyMonNicknames", MON_NAME)):
        base = SYM[name][1]
        out[name] = [bytes((rig.mem[base + slot * width + i] or 0) for i in range(width)) for slot in range(6)]
    return out


def _burials(rig):
    return [m["key"] for m in rig.sent("memorialize_done")], [m["reason"] for m in rig.sent("memorialize_failed")]


def _changed(before, after):
    return {i for i in range(len(before)) if before[i] != after[i]}


def test_memorialize_party_origin_buries_into_the_first_hole_of_box_20_with_a_fresh_entry():
    img = seal_save(Image())
    plant(img, MEMORIAL_BOX, 1, 1, 1, entry_for(live_mon(random.Random(501), 60, nick="OLD1")))
    plant(img, MEMORIAL_BOX, 3, 1, 2, entry_for(live_mon(random.Random(503), 61, nick="OLD3")))
    mons = party(4)
    key = key_of(mons[1])
    rig = Rig(mons, img=img)
    before, cart0, wram0 = _party_arrays(rig), bytes(rig.img.mem["CartRAM"]), bytes(rig.img.mem["WRAM"])
    rig.send_command({"cmd": "memorialize", "key": key, "nickname": "MON1"})
    assert _burials(rig) == ([key], [])
    assert len(rig.client.settle) == 0, "a bare true never queues a settle"
    # the party: RemoveMonFromParty's compaction, exactly
    after = _party_arrays(rig)
    assert after["count"] == 3
    for name in ("wPartyMons", "wPartyMonOTs", "wPartyMonNicknames"):
        for slot in range(3):
            assert after[name][slot] == before[name][slot if slot < 1 else slot + 1], (name, slot)
    # box 20: the FIRST EMPTY slot (the hole at 2), a FRESH entry (1 and 2 are taken: bank 1 entry 3)
    mem = box_flat(MEMORIAL_BOX)
    cart = rig.img.mem["CartRAM"]
    assert cart[mem + 1] == 3 and cart[mem + 0x14] & 0b10 == 0
    assert bytes(cart[entry_flat(1, 3):entry_flat(1, 3) + SECTION_SIZE]) == entry_for(mons[1])
    assert rig.img.mem["WRAM"][FLAG_FLAT[1]] & 0b100
    # nothing else in CartRAM/WRAM moved: the slot pointer, its Banks byte, the entry, its flag byte
    assert _changed(cart0, cart) <= {mem + 1, mem + 0x14} | set(range(entry_flat(1, 3), entry_flat(1, 3) + 49))
    assert _changed(wram0, rig.img.mem["WRAM"]) == {FLAG_FLAT[1]}
    boxes, why = rig.census_keys()
    assert boxes is not None, why
    assert boxes[MEMORIAL_BOX].count(key) == 1 and all(key not in boxes[b] for b in range(1, MEMORIAL_BOX))
    for w in rig.writes():
        assert not outside(w["addr"], w["domain"]), w


@pytest.mark.parametrize("bank", [1, 2], ids=["bank1", "bank2"])
def test_memorialize_box_origin_reuses_the_same_entry_and_moves_only_pointers(bank):
    """BOX-origin: the same (bank, entry) is published in box 20, THEN the source pointer is cleared. No allocation,
    no re-encode, no flag change (a fresh allocation, the party-origin path, would move a flag byte here)."""
    target = live_mon(random.Random(611), 70, nick="BOXD")
    img = seal_save(Image())
    plant(img, 3, 5, bank, 9, entry_for(target))
    rig = Rig(party(3), img=img)
    cart0, wram0 = bytes(rig.img.mem["CartRAM"]), bytes(rig.img.mem["WRAM"])
    rig.send_command({"cmd": "memorialize", "key": key_of(target)})
    assert _burials(rig) == ([key_of(target)], [])
    cart, mem, src = rig.img.mem["CartRAM"], box_flat(MEMORIAL_BOX), box_flat(3) + 4
    assert cart[mem] == 9 and (cart[mem + 0x14] & 1) == bank - 1
    assert cart[src] == 0
    assert _changed(cart0, cart) <= {mem, mem + 0x14, src} and {mem, src} <= _changed(cart0, cart)
    assert bytes(rig.img.mem["WRAM"]) == wram0, "a box-origin burial never touches an allocation flag"
    assert rig.count() == 3
    boxes, _ = rig.census_keys()
    assert boxes[MEMORIAL_BOX] == [key_of(target)] and key_of(target) not in boxes[3]


def test_memorialize_box_origin_never_clears_the_source_when_the_destination_did_not_land():
    """Publication safety: a silently dropped destination Entries write is caught by its read-back BEFORE the source is
    cleared (move_to_memorial does both in one batch; the executor must not)."""
    from tests.unit.test_polished_withdraw_path import build, flaky_io
    target = live_mon(random.Random(611), 70, nick="BOXD")
    img = seal_save(Image())
    plant(img, 3, 5, 2, 9, entry_for(target))
    rig = Rig(party(3), img=img)
    rig.arm_writes()
    io = flaky_io(rig)
    io.flaky.swallow = box_flat(MEMORIAL_BOX)
    raw = build(rig, io=io)
    done, why = _pair(rig.lua.eval("function(b, k) return b.memorialize(k) end")(raw, key_of(target)))
    assert done is None and "read-back" in why
    assert rig.img.mem["CartRAM"][box_flat(3) + 4] == 9, "the source was cleared without a published destination"


def test_memorialize_refuses_the_last_party_mon_before_any_write_and_retries():
    mons = party(1)
    rig = Rig(mons)
    rig.send_command({"cmd": "memorialize", "key": key_of(mons[0])})
    assert rig.writes() == [] and rig.count() == 1
    assert _burials(rig) == ([], []), "last party mon is the client's deferred retry, never a memorialize_failed"
    assert any(c["cmd"] == "memorialize" for c in rig.client.deferred.values())


def test_memorialize_refuses_mail_in_the_removed_or_a_later_slot():
    mons = party()
    mons[2]["held_item"] = 0xF5                       # FIRST_MAIL
    rig = Rig(mons)
    rig.send_command({"cmd": "memorialize", "key": key_of(mons[1])})
    assert rig.writes() == [] and rig.count() == 3
    done, failed = _burials(rig)
    assert done == [] and len(failed) == 1 and "sPartyMail is never shifted" in failed[0]


def test_memorialize_refuses_an_ambiguous_key_in_the_party_and_a_box():
    mons = party()
    img = seal_save(Image())
    plant(img, 2, 1, 1, 1, entry_for(mons[1]))
    rig = Rig(mons, img=img)
    rig.send_command({"cmd": "memorialize", "key": key_of(mons[1])})
    assert rig.writes() == [] and rig.count() == 3
    done, failed = _burials(rig)
    assert done == [] and len(failed) == 1 and "ambiguous" in failed[0]


def test_memorialize_reconciles_an_interrupted_burial_by_exact_content():
    """A published memorial copy byte-identical to the party mon (an attempt cut after its box half): only the party is
    compacted; no second entry is allocated."""
    mons = party()
    img = seal_save(Image())
    plant(img, MEMORIAL_BOX, 1, 1, 1, entry_for(mons[1]))
    rig = Rig(mons, img=img)
    cart0, wram0 = bytes(rig.img.mem["CartRAM"]), bytes(rig.img.mem["WRAM"])
    rig.send_command({"cmd": "memorialize", "key": key_of(mons[1])})
    assert _burials(rig) == ([key_of(mons[1])], [])
    assert rig.count() == 2
    assert bytes(rig.img.mem["CartRAM"]) == cart0 and bytes(rig.img.mem["WRAM"]) == wram0
    boxes, _ = rig.census_keys()
    assert boxes[MEMORIAL_BOX] == [key_of(mons[1])]


def test_red_control_a_key_only_memorial_match_is_not_reconciled():
    """RED: the same KEY with different bytes (another nickname) in box 20 is not this mon's interrupted burial."""
    mons = party()
    twin = dict(mons[1], nickname="OTHER", nickname_raw_hex=pc.encode_text("OTHER", 11).hex())
    assert key_of(twin) == key_of(mons[1]) and entry_for(twin) != entry_for(mons[1])
    img = seal_save(Image())
    plant(img, MEMORIAL_BOX, 1, 1, 1, entry_for(twin))
    rig = Rig(mons, img=img)
    rig.send_command({"cmd": "memorialize", "key": key_of(mons[1])})
    assert rig.writes() == [] and rig.count() == 3
    done, failed = _burials(rig)
    assert done == [] and len(failed) == 1 and "differs" in failed[0]


def test_memorialize_of_a_mon_already_only_in_box_20_is_a_verified_no_op():
    """Already buried: a bare done with no write, even with box 20 full."""
    target = live_mon(random.Random(611), 70, nick="BOXD")
    img = seal_save(Image())
    entry = fill(img, MEMORIAL_BOX, 1, count=19)
    plant(img, MEMORIAL_BOX, 20, 1, entry, entry_for(target))
    rig = Rig(party(), img=img)
    rig.send_command({"cmd": "memorialize", "key": key_of(target)})
    assert _burials(rig) == ([key_of(target)], []) and rig.writes() == []


def test_deposit_refuses_ambiguous_boxed_key_without_any_write():
    mons = party()
    img = seal_save(Image())
    for slot in (1, 2):
        plant(img, 1, slot, 1, slot, entry_for(mons[1]))
    rig = Rig(mons, img=img)
    before = rig.img.snap()
    rig.send_command({"cmd": "box_mon", "key": key_of(mons[1])})
    assert rig.writes() == []
    assert rig.count() == len(mons) and rig.img.snap() == before
    assert "ambiguous duplicate key" in rig.sent("box_mon_failed")[0]["reason"]
