"""lua/gen4/reads.lua == the Python oracle (server/adapters/gen4_codec.py) on the CAPTURED SaveData.

tests/unit/test_gen4_reads_lua.py builds a MODELLED main RAM out of a battery save; this file runs
the same production module over the one artifact that is not modelled -- the live RAM image of a
running game: ``<LANE_ROOT>/probe/runs/<run>/savedata.bin``, 0x30000 bytes read from the SaveData
struct's own base ("dumped 0x30000 bytes from 0x0227C204 to savedata.bin", hg_p2/out.txt:5).

Rebase. The dump is the SaveData struct and nothing else. Its bytes go at ONE address in a zeroed
fake main RAM and the pack's ``save_ptr.address`` cell is set to that address; the pointer is the
only thing that has to agree. The address is the one the run itself reported, parsed from the run
artifact beside the dump (``sSaveDataPtr [0x021D2228] = 0x0227C204``, hg_p2/out.txt:2; the bare
``sSaveDataPtr 0x0227C220`` form, hge_p5/out.txt:4). When that artifact is absent or unparseable the
module falls back to FALLBACK_SD and records the choice in ``World.sd_source``; ``label()`` puts the
run, the rebased address and that source in every skip and assertion message, so the choice is never
silent. ``World.ptr_cell`` is the cell address the artifact printed (None when it printed none) and
is checked against the pack's.

Because SaveData is position independent, the rebase cannot change a single compared value: it only
decides which address the pack's pointer dereferences to. That is the point -- the modelled-RAM test
can get a pointer wrong; here the pointer is forced to name the bytes that were actually dumped.

    run      game             pack/title              recorded sSaveDataPtr   party / box
    hg_p2r   HeartGold        gen4_hgss/heartgold     0x0227C204              1 / box 0 slot 0
    hge_p5r  HeartGold (hge)  gen4_hge/heartgold_hge  0x0227C220              1 / box 0 slot 0
    ss_p5    SoulSilver       gen4_hgss/soulsilver    0x0227C204              title screen only, not used

hg_p2r and hge_p5r (recaptured 2026-10-13) replace hg_p2 (no box mon) and hge_p5 (empty party and box,
no pointer cell). Each loaded a save whose party mon is the owner's and whose box mon is a SYNTH clone
(tools/gen4_synth_save.py party2) that the game itself deposited; each run dir's README.txt has the
lineage and command. The dumps are pinned by sha256 (Dump.sha256): a different capture is a FAIL.

OUT OF SCOPE -- the battle chain and the bag. The dump is the SaveData struct, NOT full main RAM,
so the battle chain's static sFieldSysPtr cell and its heap OverlayManager/BattleSystem/BattleContext
blocks, and the bag's application state, are simply not in it. They are covered where they are
reachable: the battle chain in tests/unit/test_gen4_reads_lua.py (battle section, modelled heap),
and the bag has no reader in lua/gen4/reads.lua at all. What IS in the dump is everything the save
path touches: the general chunk (party, player profile, money, badges, local field data / Location)
and the PC chunk (box mons).

Box NAMES and curBox live in the PC chunk but lua/gen4/reads.lua has no reader for either (R.box_mon
is handed an address; locating slots is the caller's job), so the oracle's box_name()/pc_meta() have
no Lua counterpart here and are not compared.

An absent dump is a NAMED SKIP, never a pass: every test is parametrised over the three runs and
skips with the resolved path in the message. A dump whose party header reads cur=0 skips the party
tests (and the falsifier, which needs a party body byte to flip) with the run, the path and the
offset quoted -- comparing zero mons would prove nothing.

The Lua side decodes strictly more than the oracle (pk4 adds box_plausible and, for hge,
hidden_ability), so the comparison is one-directional: every oracle key must be present in the Lua
record and equal. nickname/ot_name are compared through R.decode_name over the oracle's own charmap,
because the Lua record carries the raw u16 codes, not a string.
"""

from __future__ import annotations

import hashlib
import json
import re
import struct
from dataclasses import dataclass
from pathlib import Path

import pytest

from server.adapters import gen4_codec as codec
from tools import gen4_fixtures

try:  # tests/unit is a package in some checkouts and a plain directory in others
    from tests.unit.test_gen4_reads_lua import RAM_HI, RAM_LO, ROOT, Ram, make_lua, py, to_lua
except ImportError:  # pragma: no cover - the flat layout
    from test_gen4_reads_lua import RAM_HI, RAM_LO, ROOT, Ram, make_lua, py, to_lua

LANE = gen4_fixtures.lane_root()
RUNS = LANE / "probe" / "runs"

# Used only when a run's out.txt is gone or prints no pointer. It is hg_p2's recorded address,
# which is also the address the other two runs report; SaveData is position independent, so a
# fallback cannot change what any test compares -- only what the pointer has to point at.
FALLBACK_SD = 0x0227C204

# "sSaveDataPtr [0x021D2228] = 0x0227C204" (hg_p2) and "sSaveDataPtr 0x0227C220" (hge_p5).
PTR_RE = re.compile(r"sSaveDataPtr\s*(?:\[\s*(0x[0-9A-Fa-f]+)\s*\]\s*=\s*)?(0x[0-9A-Fa-f]+)")

TEXT_FIELDS = {"nickname", "ot_name"}
LIST_FIELDS = {"nickname_raw", "ot_name_raw", "moves", "pp", "pp_ups", "evs", "ivs", "stats"}
MON_SLOTS = 30


@dataclass(frozen=True)
class Dump:
    run: str
    game: str
    pack: str
    title: str
    codec_profile: str
    sha256: str

    @property
    def dir(self) -> Path:
        return RUNS / self.run

    @property
    def path(self) -> Path:
        return self.dir / "savedata.bin"


DUMPS = {
    d.run: d
    for d in (
        Dump("hg_p2r", "HeartGold", "gen4_hgss", "heartgold", "hgss",
             "493105f0c4203e857d227795fd7a74af84ef64d95c68ba54a3f4aa77121b7df7"),
        # ss_p5 is NOT a save image: its out.txt:2 says overworld_frame nil (title screen only),
        # so no save was loaded and the general chunk footer is all zero. SS needs a real dump.
        Dump("hge_p5r", "HeartGold (hg-engine)", "gen4_hge", "heartgold_hge", "hge",
             "fd2f5186c1bb72988a64dd0cc2e64bc0929c17ed86763b2b14b5b69aa5cd97d9"),
    )
}


# -- the run's own pointer, parsed from the artifact beside the dump -------------------------
def recorded_pointer(d: Dump) -> tuple[int | None, int | None]:
    """(sSaveDataPtr, pointer-cell address) from <run>/out.txt, or (None, None) if it prints none."""
    out = d.dir / "out.txt"
    if not out.is_file():
        return None, None
    m = PTR_RE.search(out.read_text(encoding="utf-8", errors="replace"))
    if not m:
        return None, None
    return int(m.group(2), 16), (int(m.group(1), 16) if m.group(1) else None)


def chunk_spec(raw: bytes, off: int) -> tuple[int, int]:
    """(offset, size) of a slot spec {u8 id, u8 first_page, u16 num_pages, u32 offset, u32 size}."""
    _, _, _, offset, size = struct.unpack_from("<BBHII", raw, off)
    return offset, size


def array_header(raw: bytes, sv: dict, i: int) -> tuple[int, int]:
    """(offset, size) of save array `i` as the dump's own header states it, after checking its id."""
    ident, size, off, _, _ = struct.unpack_from(
        "<IIIHH", raw, sv["array_headers_off"] + i * sv["array_header_size"]
    )
    assert ident == i, f"array header {i} names id {ident}"
    return off, size


# -- one fixture -----------------------------------------------------------------------------
@dataclass
class World:
    spec: Dump
    path: Path
    raw: bytes
    sd: int
    sd_source: str
    ptr_cell: int | None
    prof: dict
    lprof: object
    ram: Ram
    mem: object
    lua: object
    reads: object
    charmap: object
    general: bytes
    pc_chunk: bytes
    arrays: dict[int, tuple[int, int]]
    pc_dynamic_off: int
    oracle: codec.Gen4Save

    @property
    def adapter(self) -> codec.Profile:
        return codec.PROFILES[self.spec.codec_profile]

    @property
    def dyn(self) -> int:
        return self.prof["save"]["dynamic_region_off"]

    def array_addr(self, i: int) -> int:
        """Absolute address of save array `i` (pack id) inside the rebased image."""
        return self.sd + self.dyn + self.arrays[i][0]

    def party_array_addr(self) -> int:
        return self.array_addr(self.prof["party_off"]["array_id"])

    def party_count(self) -> int:
        po = self.adapter.party_off
        maxc, cur = struct.unpack_from("<II", self.general, po)
        assert maxc == 6 and cur <= 6, f"party header (max={maxc}, cur={cur}) at general+{po:#x}"
        return cur


def build_world(spec: Dump, lua, reads) -> World:
    if not spec.path.is_file():
        pytest.skip(f"{spec.game} probe dump not found: {spec.path}")
    raw = spec.path.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == spec.sha256, f"{spec.path} is not the pinned {spec.run} capture"

    prof = json.loads((ROOT / f"data/games/{spec.pack}/profile.json").read_text(encoding="utf-8"))
    prof = prof["titles"][spec.title]["profile"]
    sv = prof["save"]

    sd, cell = recorded_pointer(spec)
    if sd is None:
        sd, sd_source = FALLBACK_SD, "fallback"
    else:
        sd_source = "out.txt"
    assert sd >= RAM_LO and sd + len(raw) - 1 <= RAM_HI, (
        f"{spec.run}: the dump does not fit in main RAM at {sd:#x} ({len(raw):#x} bytes, "
        f"RAM {RAM_LO:#x}-{RAM_HI:#x})"
    )

    # the PC chunk's geometry comes from the dump's OWN slot specs, so the oracle and the Lua
    # module are both reading the image the emulator actually produced.
    gen_off, gen_size = chunk_spec(raw, sv["slot_specs_off"] + sv["slots"]["general"] * sv["slot_spec_size"])
    pc_off, pc_size = chunk_spec(raw, sv["slot_specs_off"] + sv["slots"]["pc"] * sv["slot_spec_size"])
    dyn = sv["dynamic_region_off"]
    for slot, (off, size) in (("general", (gen_off, gen_size)), ("pc", (pc_off, pc_size))):
        assert off + size <= sv["dynamic_region_size"], f"{spec.run}: {slot} chunk overruns the dynamic region"
    general = raw[dyn + gen_off : dyn + gen_off + gen_size]
    pc_chunk = raw[dyn + pc_off : dyn + pc_off + pc_size]

    # the RAM footer rule lua/gen4/reads.lua signature() applies, checked in Python first so a
    # geometry mismatch is reported as a pack/dump disagreement and not as a Lua refusal.
    cf = sv["chunk_footer"]
    for slot, (off, size) in (("general", (gen_off, gen_size)), ("pc", (pc_off, pc_size))):
        foot = dyn + off + size - cf["size"]
        magic = struct.unpack_from("<I", raw, foot + cf["fields"]["magic"])[0]
        fsize = struct.unpack_from("<I", raw, foot + cf["fields"]["size"])[0]
        fslot = struct.unpack_from("<H", raw, foot + cf["fields"]["slot"])[0]
        assert magic == cf["magic"] and fsize == size and fslot == sv["slots"][slot], (
            f"{spec.run}: the {slot} chunk footer does not validate (magic={magic:#x} size={fsize:#x} "
            f"slot={fslot}) against spec ({size:#x}, {sv['slots'][slot]})"
        )

    arrays = {i: array_header(raw, sv, i) for i in sv["array_ids"].values()}
    for off, size in arrays.values():
        assert off + size <= sv["dynamic_region_size"], f"{spec.run}: array overruns the dynamic region"

    oracle = codec.Gen4Save(
        profile=codec.PROFILES[spec.codec_profile],
        bank=0,
        counter=struct.unpack_from("<I", raw, sv["save_counter_off"])[0],
        general=general,
        pc=pc_chunk,
        blocks={},
    )

    ram = Ram()
    ram.put(sd, raw)
    ram.u32_cell(prof["save_ptr"]["address"], sd)

    return World(
        spec=spec, path=spec.path, raw=raw, sd=sd, sd_source=sd_source, ptr_cell=cell,
        prof=prof, lprof=to_lua(lua, prof), ram=ram, mem=ram.adapter(lua), lua=lua, reads=reads,
        charmap=lua.table_from(codec._CHARMAP), general=general, pc_chunk=pc_chunk,
        pc_dynamic_off=pc_off,
        arrays=arrays, oracle=oracle,
    )


@pytest.fixture(scope="module")
def lua_reads():
    return make_lua()


@pytest.fixture(scope="module", params=sorted(DUMPS))
def world(request, lua_reads):
    lua, reads = lua_reads
    return build_world(DUMPS[request.param], lua, reads)


# -- comparison helpers ----------------------------------------------------------------------
def ok(res, what: str):
    """A refusal comes back as (nil, reason); anything else is the value."""
    assert not isinstance(res, tuple), f"{what}: refused with {res[1]!r}"
    return res


def need_party(w: World) -> int:
    cur = w.party_count()
    if not cur:
        pytest.skip(
            f"{label(w)} has no party mon in the dump (party header cur=0 at "
            f"general+{w.adapter.party_off + 4:#x}); a party comparison would prove nothing: {w.path}"
        )
    return cur


def compare_mon(w: World, dec: dict, want: dict, where: str) -> None:
    for k, v in want.items():
        if k in TEXT_FIELDS:
            continue
        if k in LIST_FIELDS:
            v = list(v)
        assert dec[k] == v, f"{where}: {k}: lua {dec[k]!r} != python {v!r}"
    for k in ("nickname", "ot_name"):
        raw = list(want[f"{k}_raw"])
        got = w.reads.decode_name(w.lua.table_from(raw), w.charmap)
        assert got == want[k], f"{where}: {k}: lua {got!r} != python {want[k]!r} (raw {raw})"


# -- the rebase ------------------------------------------------------------------------------
def label(w: World) -> str:
    """Every assertion about this image names the run, the rebased address and where it came from."""
    return f"{w.spec.game} ({w.spec.run}, SaveData@{w.sd:#x} from {w.sd_source})"


def array_at(w: World, i):
    """(address, size) of save array `i`. R.array returns TWO values on success, so it needs its
    own guard rather than ok(): a refusal is (None, reason), a hit is (addr, size)."""
    res = w.reads.array(w.mem, w.lprof, w.sd, i)
    assert isinstance(res, tuple), f"{label(w)}: array {i}: {res!r}"
    if res[0] is None:
        pytest.fail(f"{label(w)}: array {i} refused with {res[1]!r}")
    return res[0], res[1]


def test_the_dump_rebases_to_the_pointer_the_run_reported(world):
    w = world
    assert w.reads.save_data(w.mem, w.lprof) == w.sd, f"{label(w)}: save_data did not resolve"
    assert w.ram.oob_calls == 0
    # every array the pack names resolves to the offset/size in the dump's own header
    for name, i in w.prof["save"]["array_ids"].items():
        assert array_at(w, i) == (w.array_addr(i), w.arrays[i][1]), f"{label(w)}: array {name} (id {i})"
    # and by pack name, not just by id
    assert array_at(w, "party") == (w.party_array_addr(), w.arrays[w.prof["party_off"]["array_id"]][1])
    assert w.ram.oob_calls == 0


def test_the_pointer_cell_the_run_artifact_prints_is_the_one_the_pack_names(world):
    w = world
    if w.ptr_cell is None:
        pytest.skip(f"{w.spec.run}: out.txt prints no sSaveDataPtr cell address: {w.spec.dir / 'out.txt'}")
    assert w.ptr_cell == w.prof["save_ptr"]["address"], (
        f"{w.spec.run}: the run printed pointer cell {w.ptr_cell:#x}, the pack names "
        f"{w.prof['save_ptr']['address']:#x}"
    )


# -- party -----------------------------------------------------------------------------------
def test_party_on_the_real_dump_matches_the_oracle(world):
    w = world
    cur = need_party(w)
    got = py(ok(w.reads.party(w.mem, w.lprof), f"{label(w)}: party"))
    want = w.oracle.party()
    assert len(want) == cur
    assert len(got) == cur, f"{w.spec.run}: lua read {len(got)} party mons, the dump header says {cur}"
    for i, (g, x) in enumerate(zip(got, want, strict=True)):
        assert g["slot"] == i and g["key"] == x["key"]
        assert (g["species"], g["level"], g["hp"], g["max_hp"]) == (x["species"], x["level"], x["hp"], x["max_hp"])
        assert g["moves"] == list(x["moves"])
        assert g["nickname_bytes"] == list(x["nickname_raw"])
        compare_mon(w, g["decoded"], x, f"{w.spec.run} party slot {i}")
    # the pre-resolved SaveData gives the same answer as the pointer walk
    assert py(ok(w.reads.party(w.mem, w.lprof, w.sd), f"{label(w)}: party(sd)")) == got
    assert w.ram.oob_calls == 0


# -- trainer, money, badges -----------------------------------------------------------------
def test_trainer_money_badges_on_the_real_dump_match_the_oracle(world):
    w = world
    want = w.oracle.player()
    tr = py(ok(w.reads.trainer(w.mem, w.lprof), f"{label(w)}: trainer"))
    assert tr["name_raw"] == list(want["name_raw"])
    assert (tr["otid"], tr["tid"], tr["sid"]) == (want["id"], want["tid"], want["sid"])
    assert (tr["gender"], tr["language"], tr["version"]) == (want["gender"], want["language"], want["version"])
    name = w.reads.decode_name(w.lua.table_from(list(want["name_raw"])), w.charmap)
    assert name == want["name"], f"{w.spec.run}: OT name {name!r} != {want['name']!r}"
    assert name, f"{w.spec.run}: the dump's OT name is empty; the comparison would prove nothing"
    assert ok(w.reads.money(w.mem, w.lprof), "money") == want["money"]
    b = py(ok(w.reads.badges(w.mem, w.lprof), f"{label(w)}: badges"))
    assert (b["johto"], b["kanto"]) == (want["johto_badges"], want["kanto_badges"])
    assert b["mask"] == want["johto_badges"] | (want["kanto_badges"] << 8)
    assert b["count"] == bin(b["mask"]).count("1")
    assert w.ram.oob_calls == 0


# -- location --------------------------------------------------------------------------------
def test_location_on_the_real_dump_matches_the_bytes(world):
    w = world
    loc = w.prof["location"]
    off = w.arrays[loc["array_id"]][0] + loc["map_off"]
    want = struct.unpack_from("<iiiii", w.general, off)
    got = py(ok(w.reads.location(w.mem, w.lprof), f"{label(w)}: location"))
    assert (got["map_id"], got["warp_id"], got["x"], got["y"], got["dir"]) == want, (
        f"{w.spec.run}: Location at general+{off:#x}"
    )
    assert (want[0], want[2], want[3]) != (0, 0, 0), (
        f"{w.spec.run}: Location is degenerate at general+{off:#x}; the comparison would prove nothing"
    )
    assert w.ram.oob_calls == 0


# -- boxes -----------------------------------------------------------------------------------
def test_box_mons_on_the_real_dump_match_the_oracle(world):
    w = world
    p = w.adapter
    assert p.box_stride == w.prof["pc"]["box_stride"] and w.prof["mons_per_box"] == MON_SLOTS
    pc_base = w.sd + w.dyn + w.pc_dynamic_off
    assert p.boxes_off + p.box_count * p.box_stride <= len(w.pc_chunk)

    compared = 0
    for i in range(p.box_count):
        for j in range(MON_SLOTS):
            off = p.boxes_off + i * p.box_stride + j * codec.BOX_MON_SIZE
            raw = w.pc_chunk[off : off + codec.BOX_MON_SIZE]
            if len(raw) != codec.BOX_MON_SIZE or codec.is_empty_slot(raw):
                continue
            where = f"{w.spec.run} box {i} slot {j}"
            want = codec.decode_box_mon(raw, p)
            got = py(ok(w.reads.box_mon(w.mem, w.lprof, pc_base + off), where))
            assert got["key"] == want["key"] and got["species"] == want["species"]
            compare_mon(w, got, want, where)
            compared += 1
    if not compared:
        pytest.skip(f"{label(w)} has no occupied box slot in the dump: {w.path}")
    assert w.ram.oob_calls == 0


# -- the falsifier: one flipped party body byte ----------------------------------------------
def test_control_one_flipped_party_body_byte_is_caught_and_restoring_it_passes(world):
    w = world
    need_party(w)
    base = w.party_array_addr() + w.prof["party_off"]["mons_off"]
    flip = base + 0x10  # inside the stored 0x80 encrypted block; the order-invariant checksum covers it

    ram = Ram(w.ram.mem)  # a private copy: the shared fixture image is never mutated
    mem = ram.adapter(w.lua)
    party_before = py(ok(w.reads.party(mem, w.lprof), "party before"))

    ram.mem[flip - RAM_LO] ^= 0x01
    res = w.reads.party(mem, w.lprof)
    assert isinstance(res, tuple) and res[1] == "party_mon0:checksum", (
        f"{w.spec.run}: a flipped byte at {flip:#x} was not caught: {res!r}"
    )
    raw = bytes(ram.mem[base - RAM_LO : base - RAM_LO + codec.PARTY_MON_SIZE])
    with pytest.raises(codec.Gen4CodecError) as ei:
        codec.decode_party_mon(raw, w.adapter)
    assert ei.value.reason == "checksum", f"the oracle accepted a corrupt record: {ei.value}"

    ram.mem[flip - RAM_LO] ^= 0x01  # restore
    party_after = py(ok(w.reads.party(mem, w.lprof), "party after restore"))
    assert party_after == party_before
    assert len(party_after) == w.party_count()
    for i, x in enumerate(w.oracle.party()):
        compare_mon(w, party_after[i]["decoded"], x, f"{w.spec.run} party slot {i}")
    assert w.ram.oob_calls == 0
