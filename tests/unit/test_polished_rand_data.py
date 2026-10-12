"""P8 (the Polished counterpart of Gen 2's R4): a randomized Polished companion cartridge answers from its own bytes.

server/adapters/gen2_polished.scan_randomized reads the wild, fishing, tree and contest tables, the species/level at
every resolved script site (data/games/polished_crystal/script_sites.json: starters, statics, gifts) and the NPC trades of a
rand_overlay cartridge; the player's adapter adopts them (use_rom_encounters) and a randomized adapter never answers
with the shipped tables. ingest_rom_content takes only the server's bytes whose sha1 is the contract pin; anything a
client reports stays unqualified. Clean and overlay adapters are unchanged.

ROM-backed tests need the pinned v3.2.3 release (tests/unit/test_upr_polished_pipeline.py `roms`: absent skips, a
wrong sha1 fails). The "UPR" cartridge is the companion overlay with the bytes one real run of the 0018 fork jar
(sha256 805325bb..., settings wild random + starters random + statics random + trainers random + trades given and
requested; seed 254615536534199) wrote inside the decoded regions; its UPR log names the expectations below.
"""
from __future__ import annotations

import hashlib

import pytest

from server.adapters import polished_rom_scan as S
from server.adapters.gen2_polished import Gen2PolishedAdapter, scan_randomized
from server.adapters.gen2_rom_scan import RomScanError
from tests.unit.test_upr_polished_pipeline import roms  # noqa: F401 -- a fixture

# Every byte that run changed inside the decoded regions: the first JohtoGrass record (SPROUT_TOWER_2F), ContestMons,
# the script sites (species LOW byte, HIGH byte, level) and NPCTrades.
UPR_POLISHED_A = {
    0x0306EE: 0x08, 0x0306EF: 0x20, 0x0306F1: 0x47, 0x0306F4: 0x1E, 0x0306F5: 0x20, 0x0306F7: 0x08, 0x0306F8: 0x20,
    0x0306FA: 0xC6, 0x030700: 0x77, 0x030703: 0x08, 0x030704: 0x20, 0x030706: 0x47, 0x030709: 0x1E, 0x03070A: 0x20,
    0x03070C: 0x08, 0x03070D: 0x20, 0x03070F: 0xC6, 0x030715: 0x77, 0x030718: 0x08, 0x030719: 0x20, 0x03071B: 0x47,
    0x03071E: 0x1E, 0x03071F: 0x20, 0x030721: 0x08, 0x030722: 0x20, 0x030724: 0xC6, 0x03072A: 0x77, 0x042897: 0xCA,
    0x052D45: 0x3E, 0x06D044: 0xF1, 0x06D072: 0x8A, 0x06D0A0: 0xDF, 0x06DEC0: 0x7A, 0x06DED4: 0x6A, 0x06DEE8: 0x81,
    0x06E488: 0x31, 0x06E4AE: 0x2A, 0x06E4D4: 0x7B, 0x073B86: 0x02, 0x07813C: 0xED, 0x07FDF4: 0x15, 0x07FE22: 0x50,
    0x07FE50: 0x5D, 0x08F954: 0x1E, 0x0958B5: 0x55, 0x0958BA: 0x8E, 0x0958BF: 0x59, 0x0958C4: 0x12, 0x0958C5: 0x20,
    0x0958C9: 0xBD, 0x0958CF: 0x20, 0x0958D3: 0x23, 0x0958D8: 0xB0, 0x0958DD: 0x36, 0x0958E2: 0x22, 0x0958E3: 0x20,
    0x0958E7: 0x13, 0x0958E8: 0x20, 0x0958EC: 0x94, 0x09A9CB: 0x13, 0x09A9D5: 0x13, 0x09C463: 0x1C, 0x09C47C: 0x67,
    0x09C495: 0x85, 0x0A48C0: 0xB3, 0x0A4919: 0xB7, 0x0A496D: 0xAE, 0x0A6DD8: 0xCB, 0x0A8C93: 0x73, 0x0AC86A: 0x17,
    0x0AC86B: 0x21, 0x0B0892: 0x55, 0x0B08A3: 0x06, 0x0B08B4: 0x58, 0x0B3089: 0x20, 0x0B308A: 0x21, 0x0B3825: 0xD1,
    0x0B5A6E: 0x03, 0x0B5A7D: 0x13, 0x0B5A7E: 0x21, 0x0B5A8C: 0x95, 0x0BB425: 0xA8, 0x0EF0D8: 0x7F, 0x0EFA7D: 0xC4,
    0x0FD3B0: 0x31, 0x0FD3B2: 0xEB, 0x0FD3D1: 0x2E, 0x0FD3D3: 0x27, 0x0FD3F2: 0x96, 0x0FD3F4: 0x3D, 0x0FD413: 0xDB,
    0x0FD415: 0x2C, 0x0FD434: 0x36, 0x0FD436: 0x9A, 0x0FD455: 0x39, 0x0FD457: 0x1C, 0x0FD476: 0x72, 0x0FD478: 0xF5,
    0x0FD497: 0x6C, 0x0FD499: 0x74, 0x0FF87F: 0xDC, 0x0FF88F: 0xBC, 0x0FF89F: 0xB4, 0x1011B5: 0x71, 0x1127A0: 0xC8,
    0x12A244: 0xAB, 0x196D31: 0x64, 0x1AA667: 0x8C, 0x1ACBBB: 0x99,
}
# Its log: "Set starter 1..3 to Species179/183/174"; the bug contest Species085 142 089 274 189 271 035 176 054 290
# 275 148; SPROUT_TOWER_2F Grass/Cave (rate=5) Species264 Lv3, 071 Lv4, 286 Lv5, 264 Lv3, 198 Lv6, 069 Lv5, 119 Lv6.
LOG_STARTERS = [179, 183, 174]
LOG_CONTEST = [85, 142, 89, 274, 189, 271, 35, 176, 54, 290, 275, 148]
LOG_SPROUT_2F = [(264, 3), (71, 4), (286, 5), (264, 3), (198, 6), (69, 5), (119, 6)]
# "Trade Species063 -> Muscle the Species066 -> Species049 -> Muscle the Species235", ... (8 lines; Jeeves is out)
LOG_TRADES = [(49, 235), (46, 39), (150, 61), (219, 44), (54, 154), (57, 28), (114, 245), (108, 116)]


def _patched(rom: bytes, changes: dict[int, int]) -> bytes:
    out = bytearray(rom)
    for offset, value in changes.items():
        out[offset] = value
    return bytes(out)


def _upr(roms):  # noqa: F811
    return _patched(roms[1], UPR_POLISHED_A)


def _payload(rom: bytes, **over) -> dict:
    return {"rom": rom, "rom_type": "polished_crystal", "rom_sha1": hashlib.sha1(rom).hexdigest(), **over}


def _adopted(rom: bytes) -> Gen2PolishedAdapter:
    adapter = Gen2PolishedAdapter(artifact_kind="rand_overlay")
    adapter.use_rom_encounters(adapter.ingest_rom_content(_payload(rom)))
    return adapter


def _site(source: str) -> int:
    return next(site["offset"] for site in S.script_sites() if site["source"] == source)


# ── the scan ──────────────────────────────────────────────────────────────────────────────
def test_the_unrandomized_overlay_adopts_to_exactly_the_shipped_tables(roms):  # noqa: F811
    """A decoder offset, a misaligned slot or a lost LEVEL_FROM_BADGES encoding would change a presentation row."""
    shipped = Gen2PolishedAdapter(artifact_kind="overlay")
    adopted = _adopted(roms[1])
    for name in ("wild", "tree", "fishing", "contest", "roamers"):
        assert adopted._encounters[name] == shipped._encounters[name], name
    assert adopted._tables == shipped._tables


def test_a_real_upr_cartridge_decodes_to_its_log(roms):  # noqa: F811
    scan, vanilla = scan_randomized(_upr(roms)), scan_randomized(roms[1])
    assert [mon["species"] for mon in scan["starters"]] == LOG_STARTERS
    statics = {mon["source"]: mon for mon in scan["statics"]}
    assert statics["maps/CeladonGameCornerPrizeRoom.asm:148"]["species"] == 21              # Mr. Mime => Species021
    assert statics["maps/EcruteakPokeCenter1F.asm:71"]["species"] == 279                    # Eevee => 279: species bit 8
    assert statics["maps/DragonShrine.asm:187"]["species"] == statics["maps/DragonShrine.asm:190"]["species"] == 19
    assert statics["maps/UnionCaveB2F.asm:44"] == {"source": "maps/UnionCaveB2F.asm:44", "kind": "loadwildmon",
                                                   "species": 127, "form": 1, "level": 25}   # Lapras => 127, level kept
    old = {mon["source"]: mon for mon in vanilla["statics"]}
    for mon in scan["statics"]:
        if mon["form"] > 1:                         # formed sites (Galarian birds, Alolan Exeggutor ...) are outside
            assert mon == old[mon["source"]]        # the handler's model: never written
    assert [(t["requested"], t["offered"]) for t in scan["trades"][:8]] == LOG_TRADES
    assert scan["trades"][8] == vanilla["trades"][8]                                        # Jeeves stays whole
    keep = [{k: v for k, v in t.items() if k not in ("requested", "offered")} for t in scan["trades"]]
    assert keep == [{k: v for k, v in t.items() if k not in ("requested", "offered")} for t in vanilla["trades"]]
    assert [row[1] for row in scan["contest"]] == LOG_CONTEST
    sprout = next(row for row in scan["wild"]["JohtoGrass"] if row["map"] == [3, 2])
    assert [(species, level) for level, species, _form in sprout["slots"][:7]] == LOG_SPROUT_2F


@pytest.mark.parametrize("delta, what", [(-1, "opcode"), (1, "gender/form byte"), (2, "level")])
def test_a_moved_or_rewritten_script_command_refuses(roms, delta, what):  # noqa: F811
    at = _site("maps/UnionCaveB2F.asm:44") + delta
    rom = _upr(roms)
    bad = {-1: rom[at] ^ 0x01, 1: rom[at] ^ 0x40, 2: 0}[delta]       # level 0 is no plain level
    with pytest.raises(RomScanError, match="UnionCaveB2F"):
        scan_randomized(_patched(rom, {at: bad}))


def test_species_bit_8_may_change_but_never_past_the_dex(roms):  # noqa: F811
    """UPR sets bit 8 for species 256+ (Ecruteak's 279 above); 127 + 256 is no species, so it refuses."""
    at = _site("maps/UnionCaveB2F.asm:44")
    rom = _upr(roms)
    with pytest.raises(RomScanError, match="species 383"):
        scan_randomized(_patched(rom, {at + 1: rom[at + 1] | S.EXT_SPECIES}))


def test_the_cartridge_must_be_a_2_mib_polished_crystal(roms):  # noqa: F811
    with pytest.raises(RomScanError, match="PKPCRYSTAL"):
        scan_randomized(_patched(_upr(roms), {0x134: 0x00}))
    with pytest.raises(RomScanError):
        scan_randomized(_upr(roms)[:0x100000])


# ── the adapter ───────────────────────────────────────────────────────────────────────────
def test_a_randomized_adapter_answers_only_from_its_own_cartridge(roms):  # noqa: F811
    shipped = Gen2PolishedAdapter(artifact_kind="overlay")
    randomized = Gen2PolishedAdapter(artifact_kind="rand_overlay")
    assert randomized.encounter_table("national_park_contest") is None      # never the shipped tables beside it
    adapter = _adopted(_upr(roms))
    contest = adapter.encounter_table("national_park_contest")["Contest"]
    assert [slot["species_id"] for slot in contest] == LOG_CONTEST
    assert contest != shipped.encounter_table("national_park_contest")["Contest"]
    morn = next(rows for label, rows in adapter.encounter_table("sprout_tower").items()
                if label.startswith("Morn") and rows[0]["map_number"] == 2)
    assert [(slot["species_id"], slot["min_level"]) for slot in morn] == LOG_SPROUT_2F
    assert adapter.encounter_table("route_47") is not None                  # badge-relative rows still present
    unreadable = Gen2PolishedAdapter(artifact_kind="rand_overlay")
    unreadable.use_rom_encounters({})                                       # an unreadable cartridge: nothing
    assert unreadable.encounter_table("route_29") is None
    other = Gen2PolishedAdapter(artifact_kind="rand_overlay")
    with pytest.raises(ValueError, match="another title"):
        other.use_rom_encounters(dict(scan_randomized(_upr(roms)), title="crystal"))


def test_ingest_accepts_the_contract_checked_bytes_of_a_real_cartridge(roms):  # noqa: F811
    rom = _upr(roms)
    adapter = Gen2PolishedAdapter(artifact_kind="rand_overlay")
    tables = adapter.ingest_rom_content(_payload(rom, rom_sha1=hashlib.sha1(rom).hexdigest().upper()))
    assert tables["title"] == "polished_crystal" and [m["species"] for m in tables["starters"]] == LOG_STARTERS
    with pytest.raises(ValueError, match="contract sha1"):
        adapter.ingest_rom_content(_payload(rom, rom_sha1=hashlib.sha1(roms[1]).hexdigest()))


def _effective_ids(node) -> set[int]:
    if isinstance(node, dict):
        return ({node["effective_species_id"]} if "effective_species_id" in node else set()).union(
            *(_effective_ids(v) for v in node.values()))
    return set().union(*(_effective_ids(v) for v in node)) if isinstance(node, list) else set()


@pytest.mark.parametrize("species, form, effective", [(19, 2, 295),     # Alolan Rattata: a variant, its own record
                                                       (129, 5, 129)])  # a Magikarp pattern: cosmetic, the species
def test_adoption_takes_a_moved_form_that_exists_for_the_species(roms, species, form, effective):  # noqa: F811
    """Variant forms are in the randomizer pool (owner ruling 2026-10-04): a slot whose form moved to a form that
    exists for its species is adopted, and presented with its effective species (a variant = record 292..337).
    RED CONTROL: drop the `row["form"] = form` line in gen2_polished._adopt_mon -> the form assert fails."""
    tables = scan_randomized(roms[1])
    level, _species, _form = tables["wild"]["JohtoGrass"][0]["slots"][0]
    tables["wild"]["JohtoGrass"][0]["slots"][0] = [level, species, form]
    adapter = Gen2PolishedAdapter(artifact_kind="rand_overlay")
    adapter.use_rom_encounters(tables)
    head = tables["wild"]["JohtoGrass"][0]["map"]
    row = next(r for r in adapter._encounters["wild"]["grass"] if r["table"] == "JohtoGrassWildMons"
               and [r["map_group"], r["map_number"]] == head and r["time"] == "morning")
    assert (row["slots"][0]["species"], row["slots"][0]["form"]) == (species, form)
    assert effective in _effective_ids(adapter._tables)


def test_adoption_refuses_a_form_the_species_does_not_have(roms):  # noqa: F811
    """A decoded (species, form) pair that names no form of that species means a misaligned decode, refused.
    RED CONTROL: make gen2_polished._adopt_mon's form check always true -> this test fails."""
    tables = scan_randomized(roms[1])
    level, _species, _form = tables["wild"]["JohtoGrass"][0]["slots"][0]
    tables["wild"]["JohtoGrass"][0]["slots"][0] = [level, 19, 9]          # Rattata has no form 9
    with pytest.raises(ValueError, match="has no form 9"):
        Gen2PolishedAdapter(artifact_kind="rand_overlay").use_rom_encounters(tables)
