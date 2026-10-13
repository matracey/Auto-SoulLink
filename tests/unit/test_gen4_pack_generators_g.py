"""Gen 4 pack-generator controls, card G (charmap.json, profile.bag, the hge PC-function count).

Rule (tests/TESTING.md): an ABSENT input skips and names the artifact; a PRESENT-but-WRONG one fails.
The synthetic controls always run; the ones that need a pinned clone or a built ROM skip by name.

Three generator facts the rewritten client's producers consume:
  * data/games/gen4_<mode>/charmap.json -- the u16 code -> text table (tools/gen_gen4_names.py)
  * profile.bag                          -- the balls pocket of the save-array bag
                                           (tools/gen_gen4_pack.py, parsed from the pinned clone)
  * the hge "N PC functions" count       -- checked against the fork's own hooks table
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tools import gen4_pins, gen_gen4_area_map as amap, gen_gen4_names as names, gen_gen4_pack as g

ROOT = Path(__file__).resolve().parents[2]
HGSS = ROOT / "data" / "games" / "gen4_hgss"
HGE = ROOT / "data" / "games" / "gen4_hge"

# charmap.txt rows this file pins, so a rebase of the table has to be a deliberate edit here too.
# (code, text, 1-based line in pret/pokeheartgold@ad7a3afa charmap.txt)
CHARMAP_ANCHORS = [
    (0x0000, "\\x0000", 7),
    (0x0001, "　", 8),
    (0x0121, "0", 295),
    (0x012B, "A", 305),
    (0x0144, "Z", 330),
    (0x0145, "a", 331),
    (0x015E, "z", 356),
]

# include/constants/items.h:270-285, ITEM_MASTER_BALL .. ITEM_CHERISH_BALL
LOW_BALL_IDS = list(range(1, 17))


def _clone(key: str, probe: str) -> Path:
    """The pinned source checkout, or a named skip; a wrong commit or dirty tree is a FAIL."""
    path = gen4_pins.default_locations().sources[key]
    if not (path / probe).exists():
        pytest.skip(gen4_pins.hge_fork_absent(path) if key == "hg_engine_fork" else f"pinned source checkout {key} not available: {path}")
    try:
        head, clean = gen4_pins.git_identity(path)
    except Exception as exc:  # noqa: BLE001 -- any git failure means it is not the pinned checkout
        pytest.fail(f"cannot read git state of {path}: {exc}")
    if head != gen4_pins.SOURCE_COMMITS[key] or not clean:
        pytest.fail(f"{key} clone {path} is at {head} (clean={clean}); "
                    f"pin {gen4_pins.SOURCE_COMMITS[key]} requires a clean tree")
    return path


@pytest.fixture(scope="module")
def pret() -> Path:
    return _clone("pokeheartgold_citation", "charmap.txt")


@pytest.fixture(scope="module")
def hge() -> Path:
    return _clone("hg_engine_fork", "charmap.txt")


@pytest.fixture(scope="module")
def hge_hooks() -> Path:
    return _clone("hg_engine_fork", "hooks") / "hooks"


@pytest.fixture(scope="module")
def bag_inputs() -> g.Inputs:
    inputs = g.default_inputs()
    if not inputs.paths["pokeheartgold_citation"].is_dir():
        pytest.skip(f"pret/pokeheartgold not available: {inputs.paths['pokeheartgold_citation']}")
    return inputs


def _rom() -> str:
    rom = gen4_pins.default_locations().roms["heartgold"]
    if not rom.is_file():
        pytest.skip(f"pinned HeartGold ROM not available: {rom}")
    return str(rom)


# ── charmap.json: the pure function ────────────────────────────────────────────────────────────


def test_charmap_doc_sorts_glyph_keys_numerically():
    """Revert: a lexicographic sort puts 299 before 30 and silently reorders the table."""
    doc = names.charmap_doc("hgss", {0x7: "b", 0x2: "a", 0x30: "c"}, "0" * 40, "f" * 64, ["header"])
    assert list(doc["glyphs"]) == ["2", "7", "48"]
    assert doc["_schema"] == names.CHARMAP_SCHEMA
    assert doc["terminator"] == 0xFFFF
    assert doc["source"]["pret_commit"] == "0" * 40 and doc["source"]["sha256"] == "f" * 64
    assert doc["source"]["file"] == "charmap.txt" and doc["source"]["glyph_count"] == 3
    assert "fork_commit" not in doc["source"]


def test_charmap_doc_uses_the_fork_commit_key_for_hge():
    doc = names.charmap_doc("hge", {1: "x"}, "1" * 40, "0" * 64, [])
    assert doc["source"]["fork_commit"] == "1" * 40 and "pret_commit" not in doc["source"]


def test_charmap_doc_does_not_depend_on_insertion_order():
    cm = {0x12B: "A", 0x121: "0", 0x2: "a"}
    shuffled = dict(reversed(list(cm.items())))
    assert names.charmap_doc("hgss", cm, "c", "s", []) == names.charmap_doc("hgss", shuffled, "c", "s", [])


# ── charmap.json: the pinned tables ────────────────────────────────────────────────────────────


def test_pret_charmap_anchors(pret):
    cm = names.load_charmap(pret / "charmap.txt")
    for code, text, line in CHARMAP_ANCHORS:
        assert cm[code] == text, f"charmap.txt:{line} is {cm.get(code)!r}, not {text!r}"


def test_charmap_anchors_are_at_their_cited_lines(pret):
    """The citation is the contract: each anchor's code must sit on the line the table above names."""
    rows = (pret / "charmap.txt").read_text(encoding="utf-8").split("\n")
    for code, text, line in CHARMAP_ANCHORS:
        assert rows[line - 1] == f"{code:04X}={text}", f"charmap.txt:{line} moved"


def test_both_pins_ship_the_same_charmap(pret, hge):
    """hg-engine's copy is the same table (it ships CRLF, pret LF), so the two charmap.json carry the same glyphs."""
    assert (hge / "charmap.txt").read_bytes().splitlines() == (pret / "charmap.txt").read_bytes().splitlines()


def test_charmap_header_is_the_files_own_statement(pret):
    header = names.charmap_header(pret / "charmap.txt")
    assert header
    assert header[0].startswith("Character mapping") and header[1].startswith("Version")


@pytest.mark.parametrize("pack", ["gen4_hgss", "gen4_hge"])
def test_charmap_json_is_committed_and_readable_by_the_lua_producer(pack):
    """The shape lua/gen4/inputs.lua Inputs.charmap reads: .glyphs[<decimal code>] = <string>."""
    path = ROOT / "data" / "games" / pack / "charmap.json"
    if not path.is_file():
        pytest.skip(f"{path} not generated yet")
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc["_schema"] == names.CHARMAP_SCHEMA
    assert doc["terminator"] == 0xFFFF
    glyphs = doc["glyphs"]
    assert glyphs, "an empty glyph table makes the producer refuse"
    keys = [int(k) for k in glyphs]
    assert keys == sorted(keys), "glyph keys must be in ascending code order"
    assert all(0 <= k <= 0xFFFF for k in keys)
    assert all(isinstance(v, str) for v in glyphs.values())
    source = doc["source"]
    assert source["sha256"] and source["glyph_count"] == len(glyphs)
    assert (source["pret_commit"] if pack == "gen4_hgss" else source["fork_commit"])
    for code, text, _line in CHARMAP_ANCHORS:
        assert glyphs.get(str(code)) == text


@pytest.mark.skipif(not (HGSS / "charmap.json").is_file(), reason="charmap.json not generated yet")
def test_charmap_regenerates_to_the_committed_bytes(pret, capsys, tmp_path):
    args = ["hgss", "--src", str(pret), "--rom", _rom(), "--out-dir", str(tmp_path)]
    assert names.main(args) == 0, capsys.readouterr().err
    assert (tmp_path / "charmap.json").read_bytes() == (HGSS / "charmap.json").read_bytes()
    assert (tmp_path / "names.json").read_bytes() == (HGSS / "names.json").read_bytes()


@pytest.mark.skipif(not (HGSS / "charmap.json").is_file(), reason="charmap.json not generated yet")
def test_charmap_check_mode_detects_drift(pret, capsys, tmp_path):
    """Revert: writing the file unconditionally instead of diffing makes this green."""
    (tmp_path / "charmap.json").write_text('{"glyphs": {}}\n', encoding="utf-8")
    rc = names.main(["hgss", "--src", str(pret), "--rom", _rom(), "--out-dir", str(tmp_path), "--check"])
    assert rc == 1
    assert "DRIFT" in capsys.readouterr().err


# ── profile.bag: the pure parsers (always run) ───────────────────────────────────────────────

SLOT_H = """\
typedef struct ItemSlot {
    u16 id;       // from constants/items.h
    u16 quantity; // quantity of that item
} ItemSlot;
"""

COUNTS_PLAIN = """\
#define NUM_BAG_ITEMS        165
#define NUM_BAG_MEDICINE     40
#define NUM_BAG_BALLS        24
#define NUM_BAG_TMS_HMS      101
#define NUM_BAG_BERRIES      64
#define NUM_BAG_MAIL         12
#define NUM_BAG_BATTLE_ITEMS 30
#define NUM_BAG_KEY_ITEMS    50
"""

COUNTS_GATED = """\
#define NUM_MEGA_STONES (48)

#ifdef ITEM_POCKET_EXPANSION
#define NUM_BAG_ITEMS        165+32+NUM_MEGA_STONES
#define NUM_BAG_MEDICINE     40
#define NUM_BAG_BALLS         24+2
#define NUM_BAG_KEY_ITEMS     50+42
#else
#define NUM_BAG_BALLS         24
#endif
"""

BAG_H_PLAIN = """\
typedef struct Bag {
    ItemSlot items[NUM_BAG_ITEMS];
    ItemSlot keyItems[NUM_BAG_KEY_ITEMS];
    ItemSlot balls[NUM_BAG_BALLS];
} Bag;
"""

POCKET_ORDER = ["items", "keyItems", "TMsHMs", "mail", "medicine", "berries", "balls", "battleItems"]


def test_c_int_resolves_macros_and_refuses_anything_else():
    assert g._c_int("165+32+NUM_MEGA_STONES", {"NUM_MEGA_STONES": "(48)"}) == 245
    assert g._c_int("(24+2)", {}) == 26
    with pytest.raises(g.Fail):
        g._c_int("FOO", {})
    with pytest.raises(g.Fail):
        g._c_int("__import__('os')", {})


def test_item_slot_geometry_comes_from_the_struct():
    size, fields = g._item_slot(SLOT_H, "ItemSlot")
    assert size == 4 and fields == {"id": (0, 2), "quantity": (2, 2)}


def test_bag_counts_take_the_enabled_branch_not_the_else():
    """Revert: reading the #else branch silently yields vanilla 24 slots on the fork."""
    assert g._bag_counts(COUNTS_PLAIN, gated=False)["NUM_BAG_BALLS"] == 24
    assert g._bag_counts(COUNTS_GATED, gated=True) == {
        "NUM_BAG_ITEMS": 245, "NUM_BAG_MEDICINE": 40, "NUM_BAG_BALLS": 26, "NUM_BAG_KEY_ITEMS": 92}
    with pytest.raises(g.Fail):
        g._bag_counts(COUNTS_PLAIN, gated=True)


def test_bag_offsets_accumulate_the_declared_pocket_order():
    """A pocket reordered in the struct must move the balls offset, not leave it pinned."""
    fields = re.findall(r"^\s*(?:ItemSlot|ITEM_SLOT)\s+(\w+)\[([A-Z0-9_]+)\]\s*;", BAG_H_PLAIN, re.M)
    counts = {"NUM_BAG_ITEMS": 10, "NUM_BAG_KEY_ITEMS": 5, "NUM_BAG_BALLS": 4}
    assert [name for name, _ in fields] == ["items", "keyItems", "balls"]
    offsets, off = {}, 0
    for index, (_name, const) in enumerate(fields):
        offsets[fields[index][0]] = off
        off += counts[const] * 4
    assert offsets == {"items": 0, "keyItems": 40, "balls": 60}
    assert off == (10 + 5 + 4) * 4


def test_pocket_members_come_from_the_own_pocket_table():
    csv_text = ("item,price,fieldPocket,battlePocket\n"
                "ITEM_POKE_BALL,200,POCKET_BALLS,1\n"
                "ITEM_POTION,300,POCKET_MEDICINE,4\n"
                "ITEM_GREAT_BALL,600,POCKET_BALLS,1\n")
    assert g._csv_pocket_items(csv_text, "POCKET_BALLS") == ["ITEM_POKE_BALL", "ITEM_GREAT_BALL"]
    c_text = ("[ITEM_POKE_BALL] =\n{\n    .fieldPocket = POCKET_BALLS,\n};\n\n"
              "[ITEM_POTION] =\n{\n    .fieldPocket = POCKET_MEDICINE,\n};\n")
    assert g._c_pocket_items(c_text, "POCKET_BALLS") == ["ITEM_POKE_BALL"]
    with pytest.raises(g.Fail):
        g._csv_pocket_items("item,price\nITEM_X,1\n", "POCKET_BALLS")


def test_absent_source_clone_is_a_named_gap_not_a_guess(tmp_path):
    """Revert: fabricating a bag (or raising Skip) instead of returning a named gap makes this green."""
    inputs = g.Inputs({**g.default_inputs().paths, "pokeheartgold_citation": tmp_path / "nope"})
    bag, why = g.bag_profile("hgss", inputs, 3)
    assert bag is None
    assert "pokeheartgold_citation" in why and "nope" in why and "has_pokeballs" in why


def test_unpinned_source_clone_fails_not_skips(monkeypatch, tmp_path):
    """Control: a checkout at another commit must FAIL, not become a silent gap (revert: drop the head check)."""
    monkeypatch.setattr(gen4_pins, "git_identity", lambda _path: ("0" * 40, True))
    inputs = g.Inputs({**g.default_inputs().paths, "pokeheartgold_citation": tmp_path})
    with pytest.raises(g.Fail):
        g.bag_profile("hgss", inputs, 3)


# ── profile.bag: the real packs ───────────────────────────────────────────────────────────────


def test_hgss_bag_geometry(bag_inputs):
    """pret include/item.h:13-16 (two u16) x include/bag_types_def.h:36-44 (pocket order) x
    include/constants/items.h:34-41 (165/50/101/12/40/64/24 slots)."""
    bag, why = g.bag_profile("hgss", bag_inputs, 3)
    assert bag is not None, why
    assert bag["ball_slot_size"] == 4
    assert bag["ball_slot_count"] == 24
    assert bag["balls_pocket_off"] == (165 + 50 + 101 + 12 + 40 + 64) * 4 == 0x6C0
    assert bag["balls_pocket_off_hex"] == "0x06c0"
    assert bag["array_id"] == 3 and bag["base"] == "bag_array"
    assert bag["slot_fields"] == {"id": {"off": 0, "size": 2}, "quantity": {"off": 2, "size": 2}}
    assert bag["source"]["commit"] == gen4_pins.SOURCE_COMMITS["pokeheartgold_citation"]


def test_hgss_ball_ids_are_the_balls_pocket(bag_inputs):
    """item_data.csv rows with fieldPocket == POCKET_BALLS, resolved through include/constants/items.h."""
    bag, why = g.bag_profile("hgss", bag_inputs, 3)
    assert bag is not None, why
    ids = bag["ball_ids"]
    assert ids == sorted(set(ids))
    assert set(LOW_BALL_IDS) <= set(ids)  # MASTER .. CHERISH
    assert 17 not in ids  # ITEM_POTION is medicine
    assert 268 not in ids  # ITEM_BLK_APRICORN is POCKET_ITEMS
    assert len(ids) >= len(LOW_BALL_IDS)


def test_bag_pocket_layout_is_contiguous_and_the_balls_pocket_fits(bag_inputs):
    bag, why = g.bag_profile("hgss", bag_inputs, 3)
    assert bag is not None, why
    layout = bag["pocket_layout"]
    assert list(layout) == POCKET_ORDER
    off = 0
    for name in POCKET_ORDER:
        assert layout[name]["off"] == off, name
        off += layout[name]["count"] * bag["ball_slot_size"]
    extent = bag["ball_slot_count"] * bag["ball_slot_size"]
    assert extent == layout["balls"]["count"] * bag["ball_slot_size"]
    assert bag["balls_pocket_off"] + extent <= off


@pytest.mark.skipif(not (HGSS / "profile.json").is_file(), reason="profile.json not built yet")
def test_committed_hgss_profile_carries_a_consistent_bag():
    title = json.loads((HGSS / "profile.json").read_text(encoding="utf-8"))["titles"]["heartgold"]
    profile = title["profile"]
    assert "bag" in profile, "profile.bag must exist as a key even when it is null"
    bag = profile["bag"]
    if bag is None:  # the clone was absent on the machine that built it; the reason must be named
        assert "pokeheartgold_citation" in title["open"]["bag"]
        pytest.skip("profile.bag is null in the committed pack (source clone absent at build time)")
    assert {"balls_pocket_off", "ball_slot_size", "ball_slot_count", "ball_ids"} <= set(bag)
    assert bag["array_id"] == profile["save"]["array_ids"]["bag"]
    extent = bag["ball_slot_count"] * bag["ball_slot_size"]
    assert extent == bag["pocket_layout"]["balls"]["count"] * bag["ball_slot_size"]
    total = sum(pocket["count"] for pocket in bag["pocket_layout"].values()) * bag["ball_slot_size"]
    assert bag["balls_pocket_off"] + extent <= total
    assert bag["ball_ids"] and bag["ball_ids"] == sorted(set(bag["ball_ids"]))


@pytest.mark.skipif(not (HGE / "profile.json").is_file(), reason="profile.json not built yet")
def test_committed_hge_profile_bag_is_present_or_named_open():
    title = json.loads((HGE / "profile.json").read_text(encoding="utf-8"))["titles"]["heartgold_hge"]
    profile = title["profile"]
    assert "bag" in profile, "profile.bag must exist as a key even when it is null"
    if profile["bag"] is None:
        assert "hg_engine_fork" in title["open"]["bag"]
        pytest.skip("profile.bag is null in the committed hge pack (source clone absent at build time)")
    assert profile["bag"]["array_id"] == profile["save"]["array_ids"]["bag"]
    assert profile["bag"]["ball_slot_count"] > 0 and profile["bag"]["ball_ids"]
    assert profile["bag"]["balls_pocket_off"] % profile["bag"]["ball_slot_size"] == 0


def test_hge_area_map_is_the_hgss_map_and_says_what_proves_it():
    """hge no longer has no area map. It has the HGSS one, and the file must be able to say WHY that is
    valid for the fork -- the two spans measured on the two pinned ROMs, with the pins named."""
    for name in ("area_map.json", "locations.json"):
        path = HGE / name
        assert path.is_file(), f"gen4_hge {name} is not generated; run tools/gen_gen4_area_map.py"
        hge_doc = json.loads(path.read_text(encoding="utf-8"))
        hgss_doc = json.loads((HGSS / name).read_text(encoding="utf-8"))
        # every field but `source`/`_note` is the HGSS file, verbatim: no second derivation to drift
        # (gift_areas is derived from each pack's OWN acquisition inventory: ids/sites/on_wild_area must match,
        # the inventory pointer and the out-of-scope record list legitimately differ)
        skip = ("source", "_note", "gift_areas")
        assert {k: v for k, v in hge_doc.items() if k not in skip} \
            == {k: v for k, v in hgss_doc.items() if k not in skip}, name
        if name == "area_map.json":
            for key in ("ids", "sites", "on_wild_area"):
                assert hge_doc["gift_areas"][key] == hgss_doc["gift_areas"][key], key
        # the hge note keeps the hgss one whole and names the proof beside it
        assert hge_doc["_note"].startswith(amap.NOTE) and hgss_doc["_note"].startswith(amap.NOTE), name
        assert hge_doc["_note"].endswith(hgss_doc["_note"][len(amap.NOTE):]), "the same tail, proof sentence in front"
        assert "hge_proof" in hge_doc["_note"] and "hge_proof" not in hgss_doc["_note"], name
        # ... and the source still names pret as the derivation, never the fork as an oracle
        assert (hge_doc["source"]["repo"], hge_doc["source"]["commit"]) \
            == ("pret/pokeheartgold", gen4_pins.SOURCE_COMMITS["pokeheartgold_citation"]), name
        assert "hge_proof" in hge_doc["source"] and "hge_proof" not in hgss_doc["source"], name

    proof = json.loads((HGE / "area_map.json").read_text(encoding="utf-8"))["source"]["hge_proof"]
    heads, names = proof["map_headers"]["sha256"], proof["area_names"]["sha256"]
    assert proof["identical"] is True and "byte-identical" in proof["claim"]
    assert set(heads) == set(names) == {"heartgold", "heartgold_hge"}
    assert len(set(heads.values())) == 1 and len(set(names.values())) == 1, "the two ROMs must agree"
    assert all(len(d) == 64 for d in (*heads.values(), *names.values())), "full sha256, never a prefix"
    assert (proof["map_headers"]["symbol"], proof["map_headers"]["address"], proof["map_headers"]["size"],
            proof["map_headers"]["count"]) == (amap.MAP_HEADERS_SYMBOL, f"{amap.MAP_HEADERS_ADDR:#010x}",
                                              amap.MAP_HEADERS_SIZE, amap.MAP_HEADER_COUNT)
    assert proof["map_headers"]["cite"] == amap.MAP_HEADERS_CITE
    assert (proof["area_names"]["narc"], proof["area_names"]["member"]) == (amap.AREA_NAMES_NARC, amap.AREA_NAMES_MSG)
    for key in ("heartgold", "heartgold_hge"):
        assert proof["roms"][key]["sha1"] == gen4_pins.ROM_SPECS[key][0], key


# ── the hge PC-function count ────────────────────────────────────────────────────────────────


def test_hge_pcstorage_redirect_count_is_the_hooks_table(hge_hooks):
    """The number the pack's pc phase `open` text and hg_engine.md:102 both assert."""
    lines = hge_hooks.read_text(encoding="utf-8").split("\n")
    start = next(i for i, ln in enumerate(lines) if ln.strip() == "#pc box expansion")
    block = []
    for ln in lines[start + 1:]:
        if ln.strip().startswith("#"):
            break
        if ln.strip():
            block.append(ln.strip())
    named = [ln for ln in block if re.match(r"^\S+\s+PCStorage_\w+\s", ln)]
    assert len(named) == 29, block
    assert named[0].split()[1] == "PCStorage_sizeof"
    assert named[-1].split()[1] == "PCStorage_GetBoxModifiedFlags"
    # the one unnamed row in the same block, deliberately not counted as a PCStorage_* function
    assert any("sub_02074128" in ln for ln in block)
