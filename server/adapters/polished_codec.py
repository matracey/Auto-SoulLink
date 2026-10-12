"""Polished Crystal v3.2.3 byte codec: party_struct (48 B + 11-B OT + 11-B nickname) and the
PokeDB savemon_struct (49 B, name-MSB checksum).

SOURCE: polishedcrystal@3fa43192 (tag v3.2.3, ROM sha1 6930b48a), docs/polished/RAM.md §2.1/§3.1 and
docs/polished/NEWBOX.md §1.3/§3. Offsets are the built ROM's (data/polished/polishedcrystal.sym
wPartyMon1* :67525-67570 and sBoxMons1AMon1* :50812-50849; tests/unit/test_polished_codec.py re-reads
both), NOT Polished's MON_* rsreset block, which disagrees with the ROM (RAM.md §2.1).
Note RAM.md §2.1 lists CaughtLocation at +29: the sym puts CaughtLevel at +29 and CaughtLocation at +30.

Differences from gen2_codec that matter: 6 one-byte EVs (no stat exp), 3 DV bytes (high nibble = the
first-named stat, judge_machine.asm:496-546), a personality byte (+20: shiny bit 7, ability slot bits
5-6, nature bits 0-4) and a form byte (+21: gender bit 7 = female, is-egg bit 6, species bit 8 in bit 5,
form bits 0-4). The species is 9-bit and an egg keeps its real species (no species-list marker).
Form identity comes from the required game pack; the adapter validates species/move/item ids.
"""
from __future__ import annotations

import json
import re
from functools import cache
from pathlib import Path
from types import MappingProxyType

ROOT = Path(__file__).resolve().parents[2]
CHARMAP = ROOT / "data" / "games" / "polished_crystal" / "charmap.lua"

STAT_NAMES = ("hp", "attack", "defense", "speed", "special_attack", "special_defense")
# party_struct, keyed by the sym's own field suffix (wPartyMon1<Suffix>)
PARTY = {"Species": 0, "Item": 1, "Moves": 2, "ID": 6, "Exp": 8, "EVs": 11, "DVs": 17,
         "Personality": 20, "Form": 21, "PP": 22, "Happiness": 26, "PokerusStatus": 27,
         "CaughtData": 28, "CaughtLevel": 29, "CaughtLocation": 30, "Level": 31, "Status": 32,
         "Unused": 33, "HP": 34, "MaxHP": 36, "Attack": 38, "Defense": 40, "Speed": 42,
         "SpAtk": 44, "SpDef": 46, "End": 48}
# savemon_struct (sBoxMons1AMon1<Suffix>): bytes 0-21 equal the party's; PP collapses to one PPUps byte.
SAVEMON = {"Species": 0, "Item": 1, "Moves": 2, "ID": 6, "Exp": 8, "EVs": 11, "DVs": 17,
           "Personality": 20, "Form": 21, "PPUps": 22, "Happiness": 23, "PokerusStatus": 24,
           "CaughtData": 25, "CaughtLevel": 26, "CaughtLocation": 27, "Level": 28, "Extra": 29,
           "Nickname": 32, "OT": 42, "End": 49}
PARTY_SIZE, SAVEMON_SIZE = 48, 49
NAME_SIZE = NICKNAME_SIZE = 11         # wPartyMonOTs stride (8-byte OT + 3 Extra) / wPartyMonNicknames stride
PLAYER_NAME_LENGTH = 8
BLOB_SIZE = PARTY_SIZE + NAME_SIZE + NICKNAME_SIZE

FORMS_INDEX = ROOT / "data" / "games" / "polished_crystal" / "forms_index.json"


def load_forms_index() -> MappingProxyType:
    """Load the required, validated identity facts shared by the codec and adapter.

    Stat before the cached read so a removed pack refuses a new adapter construction;
    replaced files get a new cache key. Never downgrade missing variant facts to plain species.
    """
    path = FORMS_INDEX
    try:
        stamp = path.stat()
    except OSError as exc:
        raise ValueError(f"required Polished forms index unavailable: {path}") from exc
    return _read_forms_index(path, stamp.st_mtime_ns, stamp.st_size)


@cache
def _read_forms_index(path, modified_ns, size) -> MappingProxyType:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data["schema"] != "polished-forms-index-v1":
            raise ValueError("unsupported schema")
        rows = data["variant_forms"]
        counts = data["counts"]
        if (not isinstance(rows, list) or len(rows) != 46
                or counts != {"records": 337, "species": 289, "variant_records": 46}):
            raise ValueError("invalid record census")
        records, forms, validated = set(), set(), []
        for row in rows:
            species, form, record = row["species_id"], row["form_id"], row["record_index"]
            if (type(species) is not int or not 1 <= species <= 291 or species in (255, 256)
                    or type(form) is not int or not 1 <= form <= 31
                    or type(record) is not int or not 292 <= record <= 337
                    or row["kind"] != "variant" or not isinstance(row["form_constant"], str)):
                raise ValueError("invalid variant identity")
            if record in records or (species, form) in forms:
                raise ValueError("duplicate variant identity")
            types = row["types"]
            if (not isinstance(types, list) or len(types) != 2
                    or not all(type(t) is int and 0 <= t <= 17 for t in types)):
                raise ValueError("invalid variant types")
            edges = row["evolves_to"]
            if not isinstance(edges, list):
                raise ValueError("invalid variant evolution list")
            for edge in edges:
                target = edge["effective_species_id"]
                if target is not None and (type(target) is not int or not 1 <= target <= 337
                                            or target in (255, 256)):
                    raise ValueError("invalid variant evolution target")
            records.add(record)
            forms.add((species, form))
            validated.append(MappingProxyType(dict(row, types=tuple(types),
                             evolves_to=tuple(MappingProxyType(edge) for edge in edges))))
        record_map = MappingProxyType({(row["species_id"], row["form_id"]): row["record_index"]
                                       for row in validated})
        return MappingProxyType(dict(data, variant_forms=tuple(validated), variant_records=record_map))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ValueError(f"invalid Polished forms index {path}: {exc}") from exc


def variant_records() -> MappingProxyType:
    return load_forms_index()["variant_records"]


def is_variant_form(species_id: int, form: int) -> bool:
    """True when (species, form) names a regional/species variant form.

    Owner ruling 2026-10-04: regional/variant forms are DIFFERENT mons from the standard counterpart;
    cosmetic forms (Unown letters, Magikarp letters/patterns, Spinda dots) are the SAME mon.
    """
    return (int(species_id), int(form)) in variant_records()


def effective_species(species_id: int, form: int) -> int:
    """The species id the shared state should judge this mon as.

    A variant form maps to its own BaseData record index (292..337), so species_types /
    evo_family / duplicate handling treat it as a different mon with NO signature change to
    server/state.py. Plain and cosmetic forms keep their species id.
    """
    return variant_records().get((int(species_id), int(form)), int(species_id))


def key_form(species_id: int, form: int) -> int:
    """Form bits carried in the identity key: variant forms stay, cosmetic forms normalise to 0.
    Owner ruling: cosmetic forms are ONE mon, so two Unown letters must produce one key."""
    return int(form) if is_variant_form(species_id, form) else 0
TERMINATOR, SPACE, START = 0x53, 0x7F, 0x00   # charmap.asm:43 '@', :93 ' ', <START>

GENDER_MASK, IS_EGG_MASK, EXTSPECIES_MASK, FORM_MASK = 0x80, 0x40, 0x20, 0x1F  # pokemon_data_constants.asm:241-245
SHINY_MASK, ABILITY_MASK, NATURE_MASK = 0x80, 0x60, 0x1F                       # :235-237


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _integer(value, low, high, label):
    _require(type(value) is int and low <= value <= high, f"{label}: expected integer {low}..{high}")
    return value


def _raw(raw, length, label):
    _require(isinstance(raw, bytes) and len(raw) == length, f"{label}: expected exactly {length} bytes")
    return raw


# ── charmap ──────────────────────────────────────────────────────────────────

@cache
def glyphs() -> MappingProxyType:
    """byte -> text from the generated pack's `glyphs` block (no_ngrams charmap)."""
    try:
        text = CHARMAP.read_text(encoding="utf-8")
    except OSError as err:
        raise ValueError(f"charmap unreadable: {err}") from err
    block = re.search(r'\["glyphs"\] = \{\n(.*?)\n  \},', text, re.S)
    _require(block is not None, "charmap.lua has no glyphs block")
    # tools/gen_polished_pack.py render_lua writes every string with json.dumps(ensure_ascii=False)
    table = {int(n): json.loads(s) for n, s in re.findall(r'\[(\d+)\] = ("(?:[^"\\]|\\.)*")', block[1])}
    _require(table.get(TERMINATOR) == "@" and set(table) == set(range(256)), "charmap glyphs incomplete or '@' is not $53")
    return MappingProxyType(table)


def decode_text(raw: bytes) -> str:
    """Charmap text up to the '@' terminator."""
    table = glyphs()
    end = raw.find(bytes([TERMINATOR]))
    return "".join(table[b] for b in (raw if end < 0 else raw[:end]))


@cache
def _reverse() -> dict[str, int]:
    reverse = {}
    for byte, glyph in sorted(glyphs().items(), reverse=True):   # lowest byte wins for a duplicated glyph
        if len(glyph) == 1:
            reverse[glyph] = byte
    return reverse


def encode_text(text: str, length: int) -> bytes:
    """Single-glyph characters only, '@'-padded to `length` (a full-length name has no terminator)."""
    reverse = _reverse()
    _require(len(text) <= length and all(ch in reverse and ch != "@" for ch in text), f"unencodable name: {text!r}")
    return bytes(reverse[ch] for ch in text) + bytes([TERMINATOR]) * (length - len(text))


# ── field helpers ────────────────────────────────────────────────────────────

def decode_dvs(raw3: bytes) -> dict:
    hp_atk, def_spe, sat_sdf = _raw(raw3, 3, "DVs")
    return {"hp": hp_atk >> 4, "attack": hp_atk & 15, "defense": def_spe >> 4, "speed": def_spe & 15,
            "special_attack": sat_sdf >> 4, "special_defense": sat_sdf & 15}


def encode_dvs(dvs: dict) -> bytes:
    v = {name: _integer(dvs[name], 0, 15, name + " DV") for name in STAT_NAMES}
    return bytes((v["hp"] << 4 | v["attack"], v["defense"] << 4 | v["speed"],
                  v["special_attack"] << 4 | v["special_defense"]))


def species_of(low: int, form_byte: int) -> int:
    """9-bit species (ConvertFormToExtendedSpecies, home/pokemon.asm:408)."""
    return low | (form_byte & EXTSPECIES_MASK) << 3


def _head(raw: bytes) -> dict:
    """Bytes 0-21, identical in party_struct and savemon_struct."""
    personality, form_byte = raw[20], raw[21]
    return {
        "species_id": species_of(raw[0], form_byte), "form": form_byte & FORM_MASK,
        "gender": "female" if form_byte & GENDER_MASK else "male", "is_egg": bool(form_byte & IS_EGG_MASK),
        "shiny": bool(personality & SHINY_MASK), "ability_slot": (personality & ABILITY_MASK) >> 5,
        "nature": personality & NATURE_MASK,
        "held_item": raw[1], "moves": list(raw[2:6]), "ot_id": int.from_bytes(raw[6:8], "big"),
        "exp": int.from_bytes(raw[8:11], "big"), "evs": dict(zip(STAT_NAMES, raw[11:17], strict=True)),
        "dvs": decode_dvs(raw[17:20]), "dv_bytes": int.from_bytes(raw[17:20], "big"),
    }


def _put_head(buf: bytearray, mon: dict) -> None:
    species = _integer(mon["species_id"], 1, 0x1FF, "species")
    _require(mon["gender"] in ("male", "female"), "gender: expected male/female (the form-byte bit)")
    buf[0] = species & 0xFF
    buf[21] = ((GENDER_MASK if mon["gender"] == "female" else 0) | (IS_EGG_MASK if mon["is_egg"] else 0)
               | (EXTSPECIES_MASK if species & 0x100 else 0) | _integer(mon["form"], 0, 31, "form"))
    buf[20] = ((SHINY_MASK if mon["shiny"] else 0) | _integer(mon["ability_slot"], 0, 3, "ability slot") << 5
               | _integer(mon["nature"], 0, 31, "nature"))
    buf[1] = _integer(mon["held_item"], 0, 255, "held item")
    _require(len(mon["moves"]) == 4, "four move slots required")
    buf[2:6] = bytes(_integer(m, 0, 255, "move") for m in mon["moves"])
    buf[6:8] = _integer(mon["ot_id"], 0, 0xFFFF, "OT ID").to_bytes(2, "big")
    buf[8:11] = _integer(mon["exp"], 0, 0xFFFFFF, "exp").to_bytes(3, "big")
    buf[11:17] = bytes(_integer(mon["evs"][name], 0, 255, name + " EV") for name in STAT_NAMES)
    dvs = encode_dvs(mon["dvs"])
    _require(mon.get("dv_bytes", int.from_bytes(dvs, "big")) == int.from_bytes(dvs, "big"), "contradictory DV fields")
    buf[17:20] = dvs


# ── party_struct ─────────────────────────────────────────────────────────────

def decode_party_mon(raw, *, ot=None, nickname=None, name_decoder=decode_text):
    _raw(raw, PARTY_SIZE, "party_struct")
    mon = _head(raw)
    mon.update(
        pp=[b & 63 for b in raw[22:26]], pp_ups=[b >> 6 for b in raw[22:26]],
        happiness=raw[26], pokerus=raw[27], caught_data=raw[28], caught_level=raw[29],
        caught_location=raw[30], level=_integer(raw[31], 1, 100, "record level"), status=raw[32],
        unused=raw[33], hp=int.from_bytes(raw[34:36], "big"), max_hp=int.from_bytes(raw[36:38], "big"),
        stats={name: int.from_bytes(raw[38 + 2 * i:40 + 2 * i], "big") for i, name in enumerate(STAT_NAMES[1:])},
        raw_hex=raw.hex())
    for label, value in (("ot", ot), ("nickname", nickname)):
        if value is not None:
            _raw(value, NAME_SIZE, label)
            mon[label + "_raw_hex"] = value.hex()
            if name_decoder is not None:
                # the OT field is 8 name bytes + 3 Extra (hyper training etc.), never text
                mon["ot_name" if label == "ot" else label] = name_decoder(
                    value[:PLAYER_NAME_LENGTH] if label == "ot" else value)
    return mon


def encode_party_mon(mon) -> bytes:
    buf = bytearray(PARTY_SIZE)
    _put_head(buf, mon)
    _require(len(mon["pp"]) == len(mon["pp_ups"]) == 4, "four PP slots required")
    buf[22:26] = bytes(_integer(pp, 0, 63, "PP") | _integer(up, 0, 3, "PP Ups") << 6
                       for pp, up in zip(mon["pp"], mon["pp_ups"], strict=True))
    for offset, key in ((26, "happiness"), (27, "pokerus"), (28, "caught_data"), (29, "caught_level"),
                        (30, "caught_location"), (32, "status"), (33, "unused")):
        buf[offset] = _integer(mon[key], 0, 255, key)
    buf[31] = _integer(mon["level"], 1, 100, "level")
    buf[34:36] = _integer(mon["hp"], 0, 0xFFFF, "HP").to_bytes(2, "big")
    buf[36:38] = _integer(mon["max_hp"], 0, 0xFFFF, "max HP").to_bytes(2, "big")
    for i, name in enumerate(STAT_NAMES[1:]):
        buf[38 + 2 * i:40 + 2 * i] = _integer(mon["stats"][name], 0, 0xFFFF, name).to_bytes(2, "big")
    return bytes(buf)


def decode_party_blob(raw, *, name_decoder=decode_text):
    _raw(raw, BLOB_SIZE, "party transfer blob")
    return decode_party_mon(raw[:PARTY_SIZE], ot=raw[PARTY_SIZE:PARTY_SIZE + NAME_SIZE],
                            nickname=raw[PARTY_SIZE + NAME_SIZE:], name_decoder=name_decoder)


def encode_party_blob(mon) -> bytes:
    return (encode_party_mon(mon) + _raw(bytes.fromhex(mon["ot_raw_hex"]), NAME_SIZE, "OT")
            + _raw(bytes.fromhex(mon["nickname_raw_hex"]), NICKNAME_SIZE, "nickname"))


# ── trademon_struct (docs/polished/TRADE.md §8.2) ─────────────────────────────
# 53 bytes, measured from the built sym: wPlayerTrademon 00:c51c .. wPlayerTrademonEnd 00:c551, and
# wOTTrademon 00:c551 .. wOTTrademonEnd 00:c586. Vanilla pokecrystal's trademon is 50: Polished adds a
# THIRD DV byte (SatSdfDV), a personality word and a separate ExtSpecies/Form/Gender/IsEgg byte.
# Offsets below are the macro (macros/ram.asm:252-276 `MACRO trademon`) cross-checked against that sym.
TRADEMON = {"Species": 0, "SpeciesName": 1, "Nickname": 12, "SenderName": 23, "OTName": 34,
            "HPAtkDV": 45, "DefSpeDV": 46, "SatSdfDV": 47, "Personality": 48, "Form": 49,
            "ID": 50, "CaughtData": 52, "End": 53}
TRADEMON_SIZE = 53
# The three name fields are all NAME_LENGTH/MON_NAME_LENGTH = 11, whatever the player-name length is:
# NAME_LENGTH is the field stride, not how many glyphs a trainer name holds.
TRADEMON_NAME_SIZE = 11


def decode_trademon(raw, *, name_decoder=decode_text) -> dict:
    """The 53-byte staged struct -> the same dict shape decode_party_mon returns, so a received
    trademon and a received 70-byte party blob are the same object to every consumer above.

    A trademon carries identity and appearance only -- species, three names, three DV bytes, the two
    attribute bytes, the OT id and the caught data. Level, item, moves, exp, EVs and stats are NOT in
    it: the engine derives those from the species base when it builds the party member
    (AddTempMonToParty, move_mon.asm:448), which is why this is a faithful inverse of
    encode_trademon and not of encode_party_mon.
    """
    _raw(raw, TRADEMON_SIZE, "trademon")
    personality, form_byte = raw[TRADEMON["Personality"]], raw[TRADEMON["Form"]]
    ot = raw[TRADEMON["OTName"]:TRADEMON["OTName"] + TRADEMON_NAME_SIZE]
    mon = {
        "species_id": species_of(raw[TRADEMON["Species"]], form_byte),
        "form": form_byte & FORM_MASK,
        "gender": "female" if form_byte & GENDER_MASK else "male",
        "is_egg": bool(form_byte & IS_EGG_MASK),
        "shiny": bool(personality & SHINY_MASK),
        "ability_slot": (personality & ABILITY_MASK) >> 5,
        "nature": personality & NATURE_MASK,
        "ot_id": int.from_bytes(raw[TRADEMON["ID"]:TRADEMON["ID"] + 2], "big"),
        "caught_data": raw[TRADEMON["CaughtData"]],
        "dvs": decode_dvs(raw[TRADEMON["HPAtkDV"]:TRADEMON["SatSdfDV"] + 1]),
        "dv_bytes": int.from_bytes(raw[TRADEMON["HPAtkDV"]:TRADEMON["SatSdfDV"] + 1], "big"),
        "species_name_raw_hex": raw[TRADEMON["SpeciesName"]:TRADEMON["Nickname"]].hex(),
        "sender_name_raw_hex": raw[TRADEMON["SenderName"]:TRADEMON["OTName"]].hex(),
        "ot_raw_hex": ot.hex(),
        "nickname_raw_hex": raw[TRADEMON["Nickname"]:TRADEMON["SenderName"]].hex(),
        "raw_hex": raw.hex(),
    }
    if name_decoder is not None:
        # Same rule as decode_party_mon: only the first PLAYER_NAME_LENGTH bytes of a trainer name are
        # text; the OT field's 3 trailing Extra bytes are not (RAM.md, OT = 8 name + 3 Extra).
        for label, value in (("species_name", bytes.fromhex(mon["species_name_raw_hex"])),
                             ("sender_name", bytes.fromhex(mon["sender_name_raw_hex"])),
                             ("ot_name", ot[:PLAYER_NAME_LENGTH]),
                             ("nickname", bytes.fromhex(mon["nickname_raw_hex"]))):
            mon[label] = name_decoder(value)
    return mon


def encode_trademon(mon, *, species_name_raw=None, sender_name_raw=None) -> bytes:
    """A 53-byte staged struct. species_name_raw / sender_name_raw are the two names the party blob
    does not carry (the species name is engine-owned; the sender name is the partner's trainer name).
    Either may be given as raw bytes or as decoded text, in which case the pack charmap is used.
    """
    buf = bytearray(TRADEMON_SIZE)
    species = _integer(mon["species_id"], 1, 0x1FF, "species")
    _require(mon.get("gender") in ("male", "female"), "gender: expected male/female (the form-byte bit)")
    buf[TRADEMON["Species"]] = species & 0xFF
    buf[TRADEMON["Personality"]] = (
        (SHINY_MASK if mon["shiny"] else 0) | _integer(mon["ability_slot"], 0, 3, "ability slot") << 5
        | _integer(mon["nature"], 0, 31, "nature"))
    buf[TRADEMON["Form"]] = (
        (GENDER_MASK if mon["gender"] == "female" else 0) | (IS_EGG_MASK if mon["is_egg"] else 0)
        | (EXTSPECIES_MASK if species & 0x100 else 0) | _integer(mon["form"], 0, 31, "form"))
    buf[TRADEMON["ID"]:TRADEMON["ID"] + 2] = _integer(mon["ot_id"], 0, 0xFFFF, "OT ID").to_bytes(2, "big")
    buf[TRADEMON["CaughtData"]] = _integer(mon["caught_data"], 0, 255, "caught data")
    dvs = encode_dvs(mon["dvs"])
    _require(mon.get("dv_bytes", int.from_bytes(dvs, "big")) == int.from_bytes(dvs, "big"),
             "contradictory DV fields")
    buf[TRADEMON["HPAtkDV"]:TRADEMON["SatSdfDV"] + 1] = dvs

    def field(raw_or_text, text_key, raw_key, label):
        if raw_or_text is not None:
            return _raw(raw_or_text, TRADEMON_NAME_SIZE, label)
        if raw_key in mon:
            return _raw(bytes.fromhex(mon[raw_key]), TRADEMON_NAME_SIZE, raw_key)
        if text_key in mon:
            return encode_text(mon[text_key], TRADEMON_NAME_SIZE)
        return bytes(TRADEMON_NAME_SIZE)  # an all-TERMINATOR name is the engine's own blank

    buf[TRADEMON["SpeciesName"]:TRADEMON["Nickname"]] = field(
        species_name_raw, "species_name", "species_name_raw_hex", "species name")
    buf[TRADEMON["SenderName"]:TRADEMON["OTName"]] = field(
        sender_name_raw, "sender_name", "sender_name_raw_hex", "sender name")
    buf[TRADEMON["OTName"]:TRADEMON["OTName"] + TRADEMON_NAME_SIZE] = field(
        None, "ot_name", "ot_raw_hex", "OT name")
    buf[TRADEMON["Nickname"]:TRADEMON["SenderName"]] = field(
        None, "nickname", "nickname_raw_hex", "nickname")
    return bytes(buf)


def trademon_from_party_blob(blob, *, species_name_raw=None, sender_name_raw=None) -> bytes:
    """The 70-byte wire party blob -> the 53-byte staged struct, by re-reading the same bytes the
    engine would. This is the port's send path: the Lua client has a party blob and the overlay's
    staging wants a trademon."""
    mon = decode_party_blob(_raw(blob, BLOB_SIZE, "party transfer blob"))
    return encode_trademon(mon, species_name_raw=species_name_raw, sender_name_raw=sender_name_raw)


def key(mon) -> str:
    """DDDDDD:OOOO:SSS:TT -- 3 DV bytes, OT ID, 9-bit species, traits (shiny bit 7, gender bit 6,
    form bits 0-4 = the form when it is a VARIANT form, else 0 for cosmetic forms).

    P3: the Polished Lua client must emit exactly this key. The traits byte carries what Gen 2's DV-only
    key cannot (Polished stores shiny and gender explicitly); it drops is-egg (hatching is not a new mon),
    the ability slot and nature. Evolution changes the species part, as for vanilla.
    """
    species = _integer(mon["species_id"], 1, 0x1FF, "species")
    # Owner ruling 2026-10-04: cosmetic forms are ONE mon, so their form bits normalise to 0 in the
    # key; a regional/variant form keeps its bits and is a DIFFERENT mon from the standard counterpart.
    traits = ((0x80 if mon["shiny"] else 0) | (0x40 if mon["gender"] == "female" else 0)
              | key_form(species, _integer(mon["form"], 0, 31, "form")))
    return (f"{_integer(mon['dv_bytes'], 0, 0xFFFFFF, 'DV bytes'):06X}:"
            f"{_integer(mon['ot_id'], 0, 0xFFFF, 'OT ID'):04X}:"
            f"{species:03X}:{traits:02X}")


# ── savemon_struct (NEWBOX.md §3) ────────────────────────────────────────────

_NAME_ENC = {SPACE: 0x7A, TERMINATOR: 0x7B, START: 0x7C}        # EncodeTempMon .charmap_loop
_NAME_DEC = {0xFA: SPACE, 0xFB: TERMINATOR, 0xFC: START}       # DecodeTempMon, bills_pc.asm:906


def encode_name_bytes(raw: bytes) -> bytes:
    return bytes(_NAME_ENC.get(b, b & 0x7F) for b in raw)


def decode_name_bytes(raw: bytes) -> bytes:
    return bytes(_NAME_DEC.get(b | 0x80, b | 0x80) for b in raw)


def checksum(entry: bytes) -> int:
    """ChecksumTempMon (bills_pc.asm:823-883): 127 + sum E[i]*(i+1) for 0..31, then (E[i]&$7F)*(i+2) for
    32..48 (the skipped multiplier 33 is the source's own), mod 65536."""
    _raw(entry, SAVEMON_SIZE, "savemon")
    total = 127 + sum(entry[i] * (i + 1) for i in range(32))
    total += sum((entry[i] & 0x7F) * (i + 2) for i in range(32, SAVEMON_SIZE))
    return total & 0xFFFF


def seal(entry: bytes) -> bytes:
    """.WriteChecksum: the MSB of name byte 32+k is checksum bit 15-k; byte 48's MSB is 0."""
    total, out = checksum(entry), bytearray(entry)
    for k in range(SAVEMON_SIZE - 32):
        out[32 + k] = (out[32 + k] & 0x7F) | ((total >> (15 - k)) & 1 if k < 16 else 0) << 7
    return bytes(out)


def verify(entry: bytes) -> bool:
    """False is what the game shows as a Bad Egg."""
    return seal(entry) == entry


def decode_savemon(raw, *, name_decoder=decode_text):
    _raw(raw, SAVEMON_SIZE, "savemon")
    _require(verify(raw), "savemon checksum mismatch (the game shows a Bad Egg)")
    mon = _head(raw)
    nickname = decode_name_bytes(raw[32:42]) + bytes([TERMINATOR])
    ot = decode_name_bytes(raw[42:49]) + bytes([TERMINATOR])
    mon.update(pp_ups=[raw[22] >> 2 * i & 3 for i in range(4)], happiness=raw[23], pokerus=raw[24],
               caught_data=raw[25], caught_level=raw[26], caught_location=raw[27],
               level=_integer(raw[28], 1, 100, "record level"), extra_hex=raw[29:32].hex(),
               nickname_raw_hex=nickname.hex(), ot_raw_hex=ot.hex(), raw_hex=raw.hex())
    if name_decoder is not None:
        mon.update(nickname=name_decoder(nickname), ot_name=name_decoder(ot))
    return mon


def encode_savemon(mon) -> bytes:
    """A sealed 49-byte entry. Names are the decoded charmap bytes (nickname 11, OT 8); only their
    first 10 / 7 bytes are stored, as the game does (the trailing '@' is restored on decode)."""
    buf = bytearray(SAVEMON_SIZE)
    _put_head(buf, mon)
    _require(len(mon["pp_ups"]) == 4, "four PP Up slots required")
    buf[22] = sum(_integer(up, 0, 3, "PP Ups") << 2 * i for i, up in enumerate(mon["pp_ups"]))
    for offset, k in ((23, "happiness"), (24, "pokerus"), (25, "caught_data"), (26, "caught_level"),
                      (27, "caught_location")):
        buf[offset] = _integer(mon[k], 0, 255, k)
    buf[28] = _integer(mon["level"], 1, 100, "level")
    ot_raw = bytes.fromhex(mon["ot_raw_hex"]) if "ot_raw_hex" in mon else None
    if "extra_hex" in mon:
        extra = bytes.fromhex(mon["extra_hex"])
    else:   # a party transfer blob's OT field is 8 name bytes + 3 Extra (hyper training etc.)
        _require(ot_raw is not None and len(ot_raw) == NAME_SIZE, "extra_hex or an 11-byte party OT field required")
        extra = ot_raw[PLAYER_NAME_LENGTH:]
    buf[29:32] = _raw(extra, 3, "extra")
    nickname = _name_field(mon, "nickname", "nickname_raw_hex", 11)
    ot = _name_field(mon, "ot_name", "ot_raw_hex", PLAYER_NAME_LENGTH)
    _require(len(nickname) in (10, 11) and len(ot) >= 7, "nickname 10-11 / OT 7+ bytes required")
    buf[32:42] = encode_name_bytes(nickname[:10])
    buf[42:49] = encode_name_bytes(ot[:7])
    return seal(bytes(buf))


# ── withdraw reconstruction (docs/polished/WITHDRAW.md) ──────────────────────
# engine constants, cited from the pinned source
STAT_MIN_NORMAL, STAT_MIN_HP, MAX_STAT = 5, 10, 999          # constants/battle_constants.asm:81-82
PP_UP_MASK, PP_MASK = 0xC0, 0x3F                             # constants/pokemon_data_constants.asm:426,428
HYPER_TRAINING_MASK = 0xFC                                   # constants/pokemon_data_constants.asm:282
PP_UP_CAP = 7                                                # ComputeMaxPP caps base/5 at 7 (item_effects.asm:3195-3205)
NATURES_OPT_BIT = 0                                          # wInitialOptions bit NATURES_OPT (home/pokemon.asm:195)


def nature_multiplier(nature: int, stat: int) -> int:
    """9 / 10 / 11 for one stat, exactly as GetNatureStatMultiplier (mon_stats.asm:878-912).

    stat is 1-6 = STAT_HP..STAT_SDEF. HP is always 10 (`cp STAT_HP / jr z,.neutral`). Otherwise the
    loop leaves d = nature//5 + 2 (the RAISED stat) and a = nature%5 + 2 (the LOWERED one); the two
    are equal for the five neutral natures, and `.penalty: cp d / jr z,.neutral` turns those neutral.
    """
    if stat == 1:
        return 10
    raised, lowered = nature // 5 + 2, nature % 5 + 2
    if raised == lowered:
        return 10
    return 9 if stat == lowered else (11 if stat == raised else 10)


def max_pp(base_pp: int, ups: int) -> int:
    """GetMaxPPOfMove + ComputeMaxPP (item_effects.asm:3269-3314, 3170-3216).

    PP = (base | ups<<6) + ups * min(base//5, 7), masked to PP_MASK: base 40 with 3 ups is 61, not 64.
    """
    return (base_pp + ups * min(base_pp // 5, PP_UP_CAP)) & PP_MASK


def _base_stats(species_id, form, variants):
    record = effective_species(species_id, form)
    if is_variant_form(species_id, form):
        _require(variants is not None and record in variants,
                 f"variant form record {record} (species {species_id}, form {form}): base stats are "
                 f"not in the generated pack; pass variant_base_stats (see docs/polished/WITHDRAW.md)")
        # the ROM-derived table IS the six base stats in STAT_NAMES order
        return tuple(_integer(v, 1, 255, name) for v, name in zip(variants[record], STAT_NAMES, strict=True))
    rows = _species_index()["species"]
    _require(str(record) in rows, f"species record {record} is not in the generated species index")
    base = rows[str(record)]["base_stats"]
    return tuple(_integer(base[name], 1, 255, name) for name in STAT_NAMES)


@cache
def _species_index() -> MappingProxyType:
    return MappingProxyType(json.loads((ROOT / "data/games/polished_crystal/species_index.json")
                                      .read_text(encoding="utf-8")))


@cache
def _move_pp() -> MappingProxyType:
    moves = json.loads((ROOT / "data/games/polished_crystal/moves.json").read_text(encoding="utf-8"))["moves"]
    return MappingProxyType({m["id"]: m["pp"] for m in moves})


def variant_base_stats_from_rom(rom: bytes, *, rom_sha1: str) -> MappingProxyType:
    """Record index -> the six base stats, from the pinned ROM at forms_index's rom_offset.

    The BaseData record opens with `db hp, atk, def, spe, sat, sdf`
    (data/pokemon/base_stats/gyarados.asm:1-2), six bytes per record; the pack carries records
    1..291 but not the 46 variant records 292..337, so the caller that holds the cartridge supplies them.
    """
    import hashlib
    _require(isinstance(rom, bytes) and hashlib.sha1(rom).hexdigest() == rom_sha1,
             "variant base stats: the ROM is not the pinned build")
    forms = load_forms_index()["variant_forms"]
    return MappingProxyType({int(r["record_index"]): tuple(rom[int(r["rom_offset"]):int(r["rom_offset"]) + 6])
                             for r in forms if r.get("kind") == "variant"})


def savemon_to_party(savemon, *, variant_base_stats, apply_evs, natures_on, perfect_ivs, ot=None,
                     nickname=None, move_pp=None) -> bytes:
    """A 48-byte party_struct for a boxed mon: the stats, HP and absolute PP the savemon cannot store.

    The engine does this in two steps (engine/pc/bills_pc.asm): DecodeTempMon (:885-) copies the
    savemon into wTempMon with the PP-Up byte in place of the four PP bytes, then SetTempPartyMonData
    (:983-1029) runs `predef CalcPkmnStats`, zeroes wTempMonStatus, sets HP to MaxHP (0 for an egg) and
    runs `farcall RestoreTempPP`. Level is the savemon's own byte (SAVEMON_LEVEL, constants/pokemon_data_
    constants.asm:226) -- the engine never re-derives it from Exp on this path.

    Four REQUIRED keyword arguments, because each is an in-game OPTION the caller must read rather
    than a constant this module may assume: `apply_evs` (CalcPkmnStatC applies EVs only when
    wInitialOptions2 & %11, constants/ram_constants.asm:125), `natures_on` (GetNature returns
    NO_NATURE -- every stat neutral -- when wInitialOptions bit NATURES_OPT is clear,
    home/pokemon.asm:195-201), `perfect_ivs` (CalcPkmnStatC uses DV 15 for EVERY stat when
    wInitialOptions bit PERFECT_IVS_OPT is set, before hyper training is considered,
    engine/pokemon/mon_stats.asm:704-707) and `variant_base_stats` (records 292..337 are not in the
    generated pack). Hyper training is NOT an option: it is the OT extra byte in the savemon, always applied.

    The write path must read these RAM option inputs verbatim from the cartridge:
      * wInitialOptions bit 0 NATURES_OPT (ram_constants.asm:101; home/pokemon.asm:195-201)
      * wInitialOptions bit 3 PERFECT_IVS_OPT (ram_constants.asm:104; mon_stats.asm:705)
      * wInitialOptions2 & EV_OPTMASK (%11) (ram_constants.asm:125; mon_stats.asm:693-695)
    The first two are the same byte.
    """
    mon = savemon if isinstance(savemon, dict) else decode_savemon(savemon)
    species_id = _integer(mon["species_id"], 1, 0x1FF, "species")
    form = _integer(mon.get("form", 0), 0, 31, "form")
    level = _integer(mon["level"], 1, 100, "level")
    base = _base_stats(species_id, form, variant_base_stats)
    extra = bytes.fromhex(mon.get("extra_hex", "000000"))
    trained = extra[0] & HYPER_TRAINING_MASK
    dvs = [mon["dvs"][name] for name in STAT_NAMES]
    evs = [mon["evs"][name] for name in STAT_NAMES]
    pp_table = _move_pp() if move_pp is None else move_pp
    stats = []
    for index, _name in enumerate(STAT_NAMES, start=1):
        dv = 15 if perfect_ivs or (trained & (0x80 >> (index - 1))) else dvs[index - 1]
        ev = (evs[index - 1] >> 2) if apply_evs else 0
        value = ((base[index - 1] + dv) * 2 + 1 + ev) * level // 100
        value += (STAT_MIN_HP + level) if index == 1 else STAT_MIN_NORMAL
        value = min(value, MAX_STAT)
        # natures off: GetNature hands back NO_NATURE and every stat is neutral (home/pokemon.asm:199)
        multiplier = 10 if not natures_on else nature_multiplier(_integer(mon["nature"], 0, 31, "nature"), index)
        stats.append(value * multiplier // 10)
    raw = bytearray(mon["raw_hex"] and bytes.fromhex(mon["raw_hex"]) or b"\x00" * SAVEMON_SIZE)
    party = bytearray(PARTY_SIZE)
    party[0:22] = raw[0:22]                      # SPECIES..PERSONALITY+FORM: identical in both structs
    for index, ups in enumerate(mon["pp_ups"]):
        move = _integer(mon["moves"][index], 0, 255, "move")
        _require(move in pp_table or move == 0, f"move {move} is not in the generated move table")
        party[22 + index] = (ups << 6) | (max_pp(pp_table[move], ups) if move else 0)
    party[26] = _integer(mon.get("happiness", 0), 0, 255, "happiness")
    party[27] = _integer(mon.get("pokerus", 0), 0, 255, "pokerus")
    party[28:31] = raw[25:28]                    # CaughtTime/Ball/Level/Location: SAVEMON 25..27
    party[31] = level
    party[32] = 0                                # SetTempPartyMonData: xor a / ld [wTempMonStatus], a
    party[33] = 0                                # the unused byte is never written by the engine
    # HP, MaxHP and the five stats are BIG-endian (CalcPkmnStats stores hMultiplicand+1 first)
    party[34:36] = bytes((0, 0)) if mon["is_egg"] else stats[0].to_bytes(2, "big")
    party[36:38] = stats[0].to_bytes(2, "big")
    for offset, value in zip(range(38, 48, 2), stats[1:], strict=True):  # five battle stats
        party[offset:offset + 2] = value.to_bytes(2, "big")
    return bytes(party)


def party_to_savemon(mon) -> bytes:
    """NEWBOX.md §6.3 step 1: a sealed box entry from a decoded party mon or party blob."""
    return encode_savemon(mon)


def _name_field(mon, text_key, raw_key, length):
    """One name's bytes: the raw hex, the text, or both -- which must agree (never two silent sources)."""
    raw = bytes.fromhex(mon[raw_key]) if raw_key in mon else None
    text = mon.get(text_key)
    if raw is not None and text is not None:
        _require(decode_text(raw[:length]) == text, f"{text_key} text and {raw_key} disagree")
    if raw is None:
        _require(text is not None, f"{text_key} or {raw_key} required")
        _require(len(text) < length, f"{text_key} too long")
        raw = encode_text(text, length)
    return raw


if __name__ == "__main__":
    # NEWBOX.md §3.3 hand vector: species 1, level 5, nickname "A"+9x'@', OT "B"+6x'@', all else 0.
    entry = bytearray(SAVEMON_SIZE)
    entry[0], entry[28] = 0x01, 0x05
    entry[32:] = encode_name_bytes(bytes([0x80] + [TERMINATOR] * 9 + [0x81] + [TERMINATOR] * 6))
    assert checksum(bytes(entry)) == 0x32D1, hex(checksum(bytes(entry)))
    sealed = seal(bytes(entry))
    assert sealed[32:].hex(" ").upper() == "00 7B FB FB 7B 7B FB 7B FB FB 01 FB 7B 7B 7B FB 7B", sealed[32:].hex(" ")
    assert verify(sealed)
    mon = decode_savemon(sealed)
    assert (mon["nickname"], mon["ot_name"], mon["species_id"], mon["level"]) == ("A", "B", 1, 5), mon
    assert encode_savemon(mon) == sealed
    for i in (1, 40):                                     # a flipped data bit / name bit is a Bad Egg
        bad = bytearray(sealed)
        bad[i] ^= 1
        assert not verify(bytes(bad))
    bad = bytearray(sealed)
    bad[48] |= 0x80                                       # the 17th MSB must be clear
    assert not verify(bytes(bad))
    print(f"checksum 0x{checksum(sealed):04X} ok; sealed names: {sealed[32:].hex(' ').upper()}")
