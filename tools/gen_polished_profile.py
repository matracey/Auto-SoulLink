"""Generate data/games/polished_crystal/profile.json (gen2-profile-v1 family) for the Polished Lua client.

Every address and struct member offset comes from the OVERLAY build's own .sym
(data/polished/polished_slink.sym, so wSlinkMailbox is included); the Polished MON_* rsreset block is
never used (docs/polished/RAM.md §2.1: it disagrees with the built ROM). Source constants (stat-stage
bounds, masks, capacities) come from the pinned polishedcrystal checkout via tools/gen_polished_pack.py's
parser, and each is cross-checked against the .sym geometry where one exists. Run with --check to compare.
ROM-table crosschecks read data/games/polished_crystal/upr_polished_entries.ini;
data/polished retains build/provenance inputs, not the runtime offsets table.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gen_polished_pack as pack  # noqa: E402
from gen_gen2_profile import OVERLAY_RAM, render, slink_abi_version  # noqa: E402
from rgbds_symbols import parse_symbols  # noqa: E402

ROOT = pack.ROOT
OUT = ROOT / "data/games/polished_crystal/profile.json"
SYM = ROOT / "data/polished/polished_slink.sym"
PROVENANCE = ROOT / "data/polished/overlay_provenance.json"
TITLE, ARTIFACT = "polished", "polishedcrystal"

RAM = (
    "wPartyCount", "wPartyMons", "wPartyMonOTs", "wPartyMonNicknames", "wPartyMonNicknamesEnd",
    "wPlayerID", "wPlayerName", "wRivalName", "wMapGroup", "wMapNumber", "wYCoord", "wXCoord",
    "wBattleMode", "wBattleType", "wBattleResult", "wCurBattleMon", "wOtherTrainerClass", "wOtherTrainerID",
    "wJohtoBadges", "wKantoBadges", "wCurBox", "wSavedAtLeastOnce", "wScriptRunning", "wLinkMode",
    "wBattleMonNickname", "wEnemyMonNickname", "wPlayerStatLevels", "wEnemyStatLevels",
    "wOTPlayerName", "wOTPlayerID", "wOTPartyCount", "wOTPartyMons", "wOTPartyMonOTs",
    "wOTPartyMonNicknames", "wOTPartyDataEnd", "wMirrorHerbPendingBoosts",
    "wMapStatus", "wGameLogicPaused",  # hello gate (docs/polished/HELLO_GATE.md)
)
# Stage 2 (docs/polished/PANEL.md): the panel's staged page is wSlinkPanelText inside the mailbox,
# and its geometry is patch/polished/src/panel.asm's own DEF lines. The Lua client is given those
# facts rather than repeating them, so the ROM and the host cannot drift.
PANEL_SRC = ROOT / "patch" / "polished" / "src" / "panel.asm"
PANEL_RAM = ("wSlinkPanelText", "wSlinkMailboxEnd")   # patch/polished/src/slink_mailbox.asm
CHARMAP = ROOT / "data" / "games" / "polished_crystal" / "charmap.lua"
# (id+quantity) pockets: count byte, data, End label (capacity = (End - data - 1) / 2), source constant
POCKETS = {"items": ("wNumItems", "wItems", "wItemsEnd", "MAX_ITEMS"),
           "medicine": ("wNumMedicine", "wMedicine", "wMedicineEnd", "MAX_MEDICINE"),
           "balls": ("wNumBalls", "wBalls", "wBallsEnd", "MAX_BALLS"),
           "berries": ("wNumBerries", "wBerries", "wBerriesEnd", "MAX_BERRIES")}
STAGES = (("ATTACK", "Atk"), ("DEFENSE", "Def"), ("SPEED", "Spe"), ("SP_ATTACK", "SAtk"),
          ("SP_DEFENSE", "SDef"), ("ACCURACY", "Acc"), ("EVASION", "Eva"))
CONSTANTS = {
    "constants/battle_constants.asm": ("MAX_LEVEL", "NUM_MOVES", "BASE_STAT_LEVEL", "MAX_STAT_LEVEL",
                                       "NUM_LEVEL_STATS", "WILD_BATTLE", "TRAINER_BATTLE",
                                       "BATTLERESULT_BITMASK", *(name for name, _ in STAGES)),
    "constants/pokemon_data_constants.asm": ("NUM_BOXES", "MONS_PER_BOX", "PARTY_LENGTH", "SHINY_MASK",
                                             "ABILITY_MASK", "NATURE_MASK", "GENDER_MASK", "IS_EGG_MASK",
                                             "EXTSPECIES_MASK", "FORM_MASK"),
    "constants/text_constants.asm": ("NAME_LENGTH", "MON_NAME_LENGTH", "PLAYER_NAME_LENGTH"),
    "constants/ram_constants.asm": ("NUM_JOHTO_BADGES", "NUM_KANTO_BADGES"),
    "constants/item_data_constants.asm": ("MAX_ITEM_STACK", "MAX_ITEMS", "MAX_MEDICINE", "MAX_BALLS",
                                          "MAX_BERRIES"),
}
# POL-SOUNDS: the four native ids the overlay's sound service resolves the shared semantic codes
# to (patch/polished/src/slink_sfx.asm .sounds). Read out of the pinned constants file, so a
# renamed or renumbered SFX is a generator error rather than a wrong cue at runtime.
CONSTANTS["constants/sfx_constants.asm"] = ("SFX_ITEM", "SFX_WRONG", "SFX_BUMP", "SFX_READ_TEXT_2")
# semantic code (patch/gb/slink_abi.inc SLINK_SFX_*) -> the source constant that names its native id
SFX_CODES = (("success", "SFX_ITEM"), ("failure", "SFX_WRONG"), ("boo", "SFX_BUMP"),
             ("notify", "SFX_READ_TEXT_2"))
# The overlay's own sound entry points, so the Lua client can name them instead of hardcoding.
SFX_SYMBOLS = ("PlaySFX", "CheckSFX", "wMusicFade", "wCurSFX", "SlinkSfxService")
# C-ROMTABLES (docs/polished/ROMTABLES.md): the encounter-table reader lua/gen2/rom.lua.
CONSTANTS["constants/pokemon_constants.asm"] = ("NUM_SPECIES", "NUM_POKEMON")
CONSTANTS["constants/map_data_constants.asm"] = ("FISHGROUP_SHORE", "NUM_FISHGROUPS")
# rom.lua base_stats field -> BASE_* member. Polished has no BASE_DEX_NO (no species byte) and packs
# gender with egg steps in one byte (BASE_EGG_STEPS EQU BASE_GENDER), so `hatch` is left out.
BASE_FIELDS = {"hp": "BASE_HP", "attack": "BASE_ATK", "defense": "BASE_DEF", "speed": "BASE_SPE",
               "sat": "BASE_SAT", "sdf": "BASE_SDF", "type1": "BASE_TYPE_1", "type2": "BASE_TYPE_2",
               "catch_rate": "BASE_CATCH_RATE", "base_exp": "BASE_EXP", "item1": "BASE_ITEM_1",
               "item2": "BASE_ITEM_2", "gender": "BASE_GENDER", "growth": "BASE_GROWTH_RATE",
               "egg_groups": "BASE_EGG_GROUPS", "tmhm": "BASE_TMHM"}
CONSTANTS["constants/pokemon_data_constants.asm"] += (
    "NUM_GRASSMON", "NUM_WATERMON", "GRASS_WILDDATA_LENGTH", "WATER_WILDDATA_LENGTH", "FISHGROUP_DATA_LENGTH",
    "NUM_ROAMMON_MAPS", "LEVEL_FROM_BADGES", "NUM_TREEMON_SETS", "TREEMON_SET_ROCK", *BASE_FIELDS.values())
WILD_REGIONS = ("Johto", "Kanto", "Orange", "Swarm")
ROM_SYMBOLS = ("BaseData", "GrassMonProbTable", "WaterMonProbTable", "TreeMons", "TreeMonMaps", "RockMonMaps",
               "FishGroups", "RoamMaps", "InitRoamMons", "CheckEncounterRoamMon", "ContestMons", "ContestMonsEnd",
               *(f"{r}{kind}WildMons" for r in WILD_REGIONS for kind in ("Grass", "Water")))
# rom symbol -> its key in the generated UPR ini (a second, independently generated view of the clean sym)
INI_KEYS = {"BaseData": "PokemonStatsOffset", "FishGroups": "FishingWildsOffset", "ContestMons": "BCCWildsOffset",
            "TreeMons": "TreemonWildsOffset", "RoamMaps": "RoamMonsterMapsOffset",
            **{f"{r}{k}WildMons": f"{r}{k}WildMonsOffset" for r in WILD_REGIONS for k in ("Grass", "Water")}}
INI = ROOT / "data/games/polished_crystal/upr_polished_entries.ini"
PARTY_REQUIRED = ("Species", "Item", "Moves", "ID", "Exp", "EVs", "HPEV", "AtkEV", "DefEV", "SpeEV",
                  "SatEV", "SdfEV", "DVs", "Personality", "Form", "PP",
                  "Happiness", "PokerusStatus", "CaughtData", "CaughtLevel", "CaughtLocation", "Level",
                  "Status", "Unused", "HP", "MaxHP", "Attack", "Defense", "Speed", "SpAtk", "SpDef", "End")
BATTLE_REQUIRED = ("Species", "Item", "Moves", "DVs", "Personality", "Form", "PP", "Happiness", "Level",
                   "Status", "HP", "MaxHP", "Attack", "Defense", "Speed", "SpAtk", "SpDef", "Type1", "Type2",
                   "StructEnd")


def require(cond, msg):
    if not cond:
        raise ValueError(msg)


def _members(symbols, prefix, end_name):
    """{suffix: offset} of every <prefix><Suffix> label inside [prefix, prefix+end]; one bank."""
    bank, base = symbols[prefix]
    end = symbols[prefix + end_name]
    require(end.bank == bank and end.address > base, f"{prefix}{end_name} geometry")
    out = {}
    for name, (b, address) in symbols.items():
        suffix = name[len(prefix):]
        if name.startswith(prefix) and re.fullmatch(r"[A-Z]\w*", suffix) and b == bank \
                and base <= address <= end.address:
            out[suffix] = address - base
    return out


def _routine(rel, label, stop):
    """Code lines of `label` up to (not including) the first line equal to `stop`."""
    out, inside = [], False
    for _, line in pack.lines(rel):
        if line == label:
            inside = True
        elif inside and line == stop:
            return out
        elif inside:
            out.append(line)
    raise ValueError(f"{rel}: {label} .. {stop} not found")


def _ini() -> dict:
    """key=value pairs of the generated UPR ini, after checking it was cut from the pinned clean sym."""
    text = INI.read_text(encoding="utf-8")
    require(f"sym sha256 {pack.sha256(pack.SYM_PATH)}" in text, f"{INI.name}: not generated from {pack.SYM_PATH.name}")
    pairs = (line.split("//", 1)[0].strip().partition("=") for line in text.splitlines())
    return {k.strip(): v.strip() for k, sep, v in pairs if sep}


def rom_tables(symbols, constants) -> tuple[dict, dict, dict]:
    """profile.rom, the reader's derived counts and the `layout` block for lua/gen2/rom.lua."""
    clean, ini = parse_symbols(pack.SYM_PATH.read_text(encoding="utf-8")), _ini()
    rom = {}
    for name in ROM_SYMBOLS:
        require(name in symbols and symbols[name] == clean.get(name), f"{name}: overlay and clean sym disagree")
        bank, addr = symbols[name]
        require(bank >= 1 and 0x4000 <= addr < 0x8000, f"{name} outside banked ROM")
        rom[name] = {"bank": bank, "addr": addr, "flat": bank * 0x4000 + addr - 0x4000}
        if name in INI_KEYS:
            require(int(ini[INI_KEYS[name]], 16) == rom[name]["flat"], f"{name}: ini offset disagrees")
    require("TimeFishGroups" not in symbols, "TimeFishGroups present: the fishing layout changed")
    regions = sorted(WILD_REGIONS, key=lambda r: rom[f"{r}GrassWildMons"]["flat"])
    require(tuple(regions) == WILD_REGIONS, "wild region tables out of order")

    # GRASS_WILDDATA_LENGTH = 2 + (1 + NUM_GRASSMON * slot) * 3, slot = level byte + species bytes
    grass, water = constants["GRASS_WILDDATA_LENGTH"], constants["WATER_WILDDATA_LENGTH"]
    slot, rest = divmod((grass - 2) // 3 - 1, constants["NUM_GRASSMON"])
    require(rest == 0 and (grass - 2) % 3 == 0 and slot >= 2, "GRASS_WILDDATA_LENGTH shape")
    require(water == 2 + 1 + constants["NUM_WATERMON"] * slot, "WATER_WILDDATA_LENGTH disagrees with the grass slot")
    widths = {line.split()[1] for _, line in pack.lines("data/wild/probabilities.asm")
              if line.startswith("table_width")}
    require(len(widths) == 1, "probability tables disagree on table_width")
    gate = _routine("engine/events/treemons.asm", "GetTreeMons:", "add a")
    require(gate == ["cp NUM_TREEMON_SETS", "ret nc"], f"unsupported GetTreeMons set gate: {gate}")
    init = _routine("engine/overworld/wildmons.asm", "InitRoamMons:", "CheckEncounterRoamMon:")
    roamers = sorted({int(n) for line in init for n in re.findall(r"ld \[wRoamMon(\d+)Species\], a", line)})
    require(roamers and roamers == list(range(1, len(roamers) + 1)), "non-contiguous or empty roamer slots")

    tmhm_bits = sum(1 for _, line in pack.lines("constants/tmhm_constants.asm")
                    if line.split(" ", 1)[0] in ("add_tm", "add_hm", "add_mt"))
    stride = constants["BASE_TMHM"] + (tmhm_bits + 7) // 8
    require(int(ini["BaseStatsEntrySize"]) == stride, "BASE_DATA_SIZE: source and ini disagree")
    require(int(ini["SpeciesCount"]) == constants["NUM_SPECIES"], "NUM_SPECIES: source and ini disagree")
    derived = {
        # 291 is the species-id validation bound; 289 (NUM_POKEMON) is not (ROMTABLES.md 8.5)
        "species_count": constants["NUM_SPECIES"], "num_pokemon": constants["NUM_POKEMON"],
        "base_stats_stride": stride, "base_tmhm_offset": constants["BASE_TMHM"],
        "num_grassmon": constants["NUM_GRASSMON"], "num_watermon": constants["NUM_WATERMON"],
        "num_treemon_sets": constants["NUM_TREEMON_SETS"], "treemon_set_rock": constants["TREEMON_SET_ROCK"],
        "treemon_enabled_limit": constants["NUM_TREEMON_SETS"],
        "num_fishgroups": constants["NUM_FISHGROUPS"], "num_time_fishgroups": 0,
        "num_roammon_maps": constants["NUM_ROAMMON_MAPS"], "roamer_count": len(roamers),
        # battle types whose wild encounter resolves an area (ROMTABLES.md 4.1): normal, fish, tree
        "area_battle_types": [constants[f"BATTLETYPE_{n}"] for n in ("NORMAL", "FISH", "TREE")],
    }
    layout = {
        "species_bytes": slot - 1, "prob_width": int(widths.pop()),
        "wild_grass_row": grass, "wild_water_row": water, "wild_regions": list(WILD_REGIONS),
        "fish_group_header": constants["FISHGROUP_DATA_LENGTH"], "fish_group_base": constants["FISHGROUP_SHORE"],
        "tree_first_set": 0, "level_from_badges": constants["LEVEL_FROM_BADGES"],
        "roamer_inc_a": "inc a" in init,
        "species_count": int(ini["SpeciesCount"]), "base_stats_stride": int(ini["BaseStatsEntrySize"]),
        "base_fields": {field: constants[name] for field, name in BASE_FIELDS.items()},
    }
    return rom, derived, layout

def _def(source: str, name: str, seen: frozenset = frozenset()) -> int:
    """The value of `DEF <name> EQU <int | <other name> + <int>>`, or raise."""
    if name in seen:
        raise ValueError(f"panel.asm DEF {name} is circular")
    match = re.search(rf"^DEF {name} EQU (.+?)\s*(?:;.*)?$", source, re.M)
    if not match:
        raise ValueError(f"panel.asm no longer declares DEF {name}")
    expr = match.group(1)
    if expr.isdigit():
        return int(expr)
    other, _, offset = expr.partition(" + ")
    if not other or not offset.isdigit():
        raise ValueError(f"panel.asm DEF {name} = {expr!r} is neither a literal nor a named sum")
    return _def(source, other, seen | {name}) + int(offset)


def panel_block(ram: dict) -> dict:
    """Where lua/gen2/panel.lua stages a page, and how the ROM reads it back (Stage 2).

    The line count and glyph width are panel.asm's own constants and the terminator is the one the
    generated charmap pack already hands the client's own token scanner, so the ROM and the host
    cannot disagree about either."""
    source = PANEL_SRC.read_text(encoding="utf-8")
    lines, line_max = _def(source, "SLINK_PANEL_LINES"), _def(source, "SLINK_PANEL_LINE_MAX")
    stride = _def(source, "SLINK_PANEL_STRIDE")
    terminator = int(re.search(r'\["terminator"\] = (\d+)', CHARMAP.read_text(encoding="utf-8")).group(1))
    require(stride == line_max + 1, f"panel stride {stride} is not one terminator past {line_max} glyphs")
    require(1 <= lines <= 18, f"panel lines {lines} outside one text page")
    require(0 < line_max < 20, f"panel line width {line_max} outside the textbox")
    require(terminator == 0x53, f"charmap terminator {terminator} is not the game's `@`")
    require(stride * lines <= 35, f"a staged page needs {stride * lines} of the 35-byte mailbox tail")
    return {"base": ram["wSlinkPanelText"], "lines": lines, "line_max": line_max,
            "stride": stride, "terminator": terminator}


TRADE_ENTRIES = (
    "SlinkTradeWaitGate", "SlinkTradeTimeoutGate", "SlinkTradeEntry",
    "SlinkTradeProposerService", "SlinkTradeProposerServiceEnd",
    "SlinkTradeDispatch", "SlinkTradePromptEntry",
)
TRADE_STACK_PINS = (
    "NextOverworldFrame", "DelayFrame", "NextOverworldFrame.gfx_done",
    "HandleMap", "OverworldLoop.loop",
)


def trade_block(symbols: dict, provenance: dict) -> dict:
    """C2: provenance-bound facilities and owner-enabled source gate; qualification remains external.

    Numeric DEFs are not exported by this build's sym. Read only sources whose
    bytes match the build receipt; all addresses and record sizes come from sym.
    Capabilities are keyed on the paired start/End markers of each component, never on PromptEntry (a jp
    trampoline since C6). Production needs every component plus the verified commit-enable define; test builds stay unadvertised.
    `staging` is the host payload allowlist; `snapshot` is ROM-owned and must never
    be included in the host write permit. Neither grants authority outside a lease.
    """
    def source(rel):
        raw = (ROOT / rel).read_bytes()
        require(provenance["overlay"]["sources_sha256"].get(rel)
                == hashlib.sha256(raw).hexdigest(), f"trade source provenance mismatch: {rel}")
        return raw.decode("utf-8")

    def number(text, name, seen=frozenset()):
        require(name not in seen, f"trade constant cycle: {name}")
        matches = re.findall(rf"^DEF\s+{re.escape(name)}\s+EQU\s+([^;\n\r]+)", text, re.M)
        require(len(matches) == 1, f"trade constant missing or duplicated: {name}")
        expr = matches[0].strip()
        if re.fullmatch(r"\d+", expr):
            return int(expr)
        if re.fullmatch(r"\$[0-9a-fA-F]+", expr):
            return int(expr[1:], 16)
        require(re.fullmatch(r"\w+", expr), f"unsupported trade constant: {name} = {expr}")
        return number(text, expr, seen | {name})

    abi = source("patch/gb/slink_abi.inc")
    frame = source("patch/polished/src/trade_frame.asm")
    service = source("patch/polished/src/trade_service.asm")
    validator = source("patch/polished/src/trade_validate.asm")

    def location(name, rom=False):
        require(name in symbols, f"trade required symbol missing: {name}")
        bank, addr = symbols[name]
        valid = ((bank == 0 and 0 <= addr < 0x4000) or (bank > 0 and 0x4000 <= addr < 0x8000)) if rom else (
            (bank == 0 and 0xC000 <= addr < 0xD000) or (1 <= bank <= 7 and 0xD000 <= addr < 0xE000))
        require(valid, f"trade symbol outside {'ROM' if rom else 'WRAM'}: {name}")
        return {"symbol": name, "bank": bank, "addr": addr}

    def span(start, end):
        row, stop = location(start), location(end)
        require(row["bank"] == stop["bank"] and stop["addr"] > row["addr"], f"trade span: {start}/{end}")
        return {**row, "size": stop["addr"] - row["addr"]}

    entries = {name: location(name, rom=True) for name in TRADE_ENTRIES}

    def component(name):
        # Paired implementation markers: a PromptEntry stub is never sufficient.
        # Future cards export these start/End labels; presence is not live qualification.
        present = [label in symbols for label in (name, name + "End")]
        require(all(present) or not any(present), f"trade incomplete component: {name}")
        if not all(present):
            return False
        start, end = location(name, rom=True), location(name + "End", rom=True)
        require(start["bank"] == end["bank"] and end["addr"] > start["addr"], f"trade component extent: {name}")
        entries[name], entries[name + "End"] = start, end
        return True

    capabilities = {"proposer_service": component("SlinkTradeProposerService"),
                    "responder_service": component("SlinkTradeResponderService"),
                    "commit": component("SlinkTradeCommit")}
    # These two values are not exported DEFs: the build asserts NUM_NATURES,
    # and the low-species bound is an immediate in ValidateRecord. Parse only
    # executable/assertion lines in the provenance-bound source, never comments.
    code = "\n".join(line.split(";", 1)[0].strip() for line in validator.splitlines())
    nature = re.findall(r"^ASSERT\b[^\n]*\bNUM_NATURES\s*==\s*(\d+)\b", code, re.M)
    record = re.search(r"^SlinkTradeValidateRecord::\n(.*?)^\.limit$", code, re.M | re.S)
    require(len(nature) == 1 and record is not None, "trade validator facts missing: nature/record")
    low = re.findall(r"^ld d, \$([0-9a-fA-F]+)$", record[1], re.M)
    require(len(low) == 1 and "cp NUM_NATURES" in record[1], "trade validator facts missing: species/nature check")
    table = location("SlinkTradeAllowedItems", rom=True)
    table_end = location("SlinkTradeAllowedItemsEnd", rom=True)
    require(table["bank"] == table_end["bank"] and table_end["addr"] - table["addr"] == 256,
            "trade item table must span all 256 byte ids in one ROM bank")
    validation = {"glyph_floor": number(validator, "SLINK_TRADE_NAME_FLOOR"),
                  "nature_count": int(nature[0]), "species_low_max": int(low[0], 16),
                  "items": {**table, "size": table_end["addr"] - table["addr"]}}
    require(0 < validation["glyph_floor"] <= 255 and 0 < validation["nature_count"] <= 32
            and 0 < validation["species_low_max"] < 256, "trade validator facts outside byte/mask bounds")
    for name in TRADE_STACK_PINS:
        location(name, rom=True)
    staging = {
        "party": span("wOTPartyMon1", "wOTPartyMon1End"),
        "ot": span("wOTPartyMonOTs", "wOTPartyMon2OT"),
        "nickname": span("wOTPartyMonNicknames", "wOTPartyMon2Nickname"),
        "sender": span("wOTPlayerName", "wOTPlayerID"),
    }
    snapshot = {
        "party": span("wOTPartyMon2", "wOTPartyMon2End"),
        "ot": span("wOTPartyMon2OT", "wOTPartyMon3OT"),
        "nickname": span("wOTPartyMon2Nickname", "wOTPartyMon3Nickname"),
    }
    for name in snapshot:
        require(staging[name]["size"] == snapshot[name]["size"], f"trade slot sizes disagree: {name}")
    mailbox = span("wSlinkMailbox", "wSlinkMailboxEnd")
    offset, size = number(abi, "SLINK_OFS_TRADE_LEASE"), number(abi, "SLINK_TRADE_LEASE_SIZE")
    require(offset >= 0 and offset + size <= mailbox["size"], "trade lease outside mailbox")
    fields = {name.lower(): number(frame, f"SLINK_TRADE_OFS_{name}") for name in (
        "MAGIC", "VERSION", "COMMAND", "GENERATION", "ACK", "RESULT", "SLOT", "AVAILABLE", "MASK", "TOKEN")}
    # Magic/token occupy four bytes; every other field is a byte. Reject holes or aliasing.
    covered = [i for name, pos in fields.items() for i in range(pos, pos + (4 if name in ("magic", "token") else 1))]
    require(sorted(covered) == list(range(size)), "trade lease fields overlap or leave holes")
    lease = {"symbol": "wSlinkMailbox", "bank": mailbox["bank"], "base": mailbox["addr"] + offset,
             "offset": offset, "size": size, "fields": fields,
             "version": number(abi, "SLINK_TRADE_VERSION"),
             "magic": [number(abi, f"SLINK_TRADE_MAGIC_{i}") for i in range(4)]}
    spans = [*staging.values(), *snapshot.values(), {"bank": lease["bank"], "addr": lease["base"], "size": size}]
    for i, left in enumerate(spans):
        for right in spans[i + 1:]:
            require(left["bank"] != right["bank"] or left["addr"] + left["size"] <= right["addr"]
                    or right["addr"] + right["size"] <= left["addr"], "trade staging/snapshot/lease overlap")
    return {
        "schema": "polished-trade-v1",
        "production": (number(service, "SLINK_TRADE_COMMIT_ENABLE") == 1
                       and all(capabilities.values()) and not provenance.get("test_only")),
        "commit_gate": location("SlinkTradeCommitEnabled", rom=True),
        "capabilities": capabilities,
        "lease": lease, "entries": entries, "dispatcher_stack_pin_names": list(TRADE_STACK_PINS),
        "staging": staging, "snapshot": snapshot,
        "validation": validation,
        "commands": {name: number(abi, f"SLINK_TRADE_CMD_{name}") for name in (
            "QUERY", "OFFER", "PROMPT", "APPLY", "DONE", "RELEASE")},
        "timeouts": {name: number(service, f"SLINK_TRADE_{name}_FRAMES") for name in (
            "QUERY", "OFFER", "APPLY", "RELEASE")},
    }


def build() -> dict:
    pack.verify_source()
    raw_sym, raw_prov = SYM.read_bytes(), PROVENANCE.read_bytes()
    prov = json.loads(raw_prov)
    sym_sha = hashlib.sha256(raw_sym).hexdigest()
    require(prov.get("schema") == "polished-overlay-provenance-v1", "overlay provenance: unsupported schema")
    require(prov["symbols"].get(SYM.name) == sym_sha, f"{SYM.name} sha256 differs from overlay provenance")
    clean = pack.LOCK["outputs"][ARTIFACT]
    out = prov["output"]
    require(prov["base_sha1"] == clean["sha1"] and prov["source"]["commit"] == pack.LOCK["source"]["commit"],
            "overlay provenance base/commit disagrees with the lock")
    require(out["identical_to_clean"] is False and out["sha1"] != clean["sha1"], "overlay must differ from clean")
    require(out["title"].split("\u0000")[0] == clean["title"], "overlay header title disagrees with the lock")
    abi = slink_abi_version(ROOT)
    require(prov["abi_version"] == abi, "overlay provenance ABI differs from patch/gb/slink_abi.inc")
    symbols = parse_symbols(raw_sym.decode("utf-8"))

    constants = {}
    for rel, names in CONSTANTS.items():
        values = pack.parse_consts(rel)
        for name in names:
            require(type(values.get(name)) is int, f"{rel}: {name} unresolved")
            constants[name] = values[name]
    constants.update({k: v for k, v in pack.parse_consts("constants/battle_constants.asm").items()
                      if k.startswith("BATTLETYPE_")})

    constants.update({k: v for k, v in pack.parse_consts("constants/sfx_constants.asm").items()
                      if k in {c for _n, c in SFX_CODES}})
    names = set(RAM) | {fields[i] for fields in POCKETS.values() for i in range(3)}
    names |= {f"w{side}{suffix}Level" for side in ("Player", "Enemy") for _, suffix in STAGES}
    patterns = (r"wPartyMon[1-6]\w*", r"wBattleMon\w*", r"wEnemyMon\w*", r"wRoamMon\d\w*", r"h\w+")
    names |= {n for n in symbols if any(re.fullmatch(p, n) for p in patterns)}
    names |= set(OVERLAY_RAM) | set(PANEL_RAM)
    ram, ram_bank, hram = {}, {}, {}
    for name in sorted(names):
        require(name in symbols, f"{SYM.name}: required symbol {name} missing")
        bank, address = symbols[name]
        if name.startswith("h"):
            require(bank == 0 and 0xFF80 <= address <= 0xFFFF, f"{name} outside HRAM")
            hram[name] = address
        else:
            require((bank == 0 and 0xC000 <= address < 0xD000) or (1 <= bank <= 7 and 0xD000 <= address < 0xE000),
                    f"{name} outside WRAM0/WRAMX")
        ram[name], ram_bank[name] = address, bank

    party = _members(symbols, "wPartyMon1", "End")
    battle = _members(symbols, "wBattleMon", "StructEnd")
    enemy = _members(symbols, "wEnemyMon", "StructEnd")
    require(all(k in party for k in PARTY_REQUIRED), f"party_struct members missing: {set(PARTY_REQUIRED) - set(party)}")
    require(all(k in battle for k in BATTLE_REQUIRED), f"battle_struct members missing: {set(BATTLE_REQUIRED) - set(battle)}")
    require({k: v for k, v in enemy.items() if k in battle} == {k: battle[k] for k in enemy if k in battle}
            and set(BATTLE_REQUIRED) <= set(enemy), "wEnemyMon/wBattleMon struct offsets differ")

    def diff(a, b):
        require(symbols[a].bank == symbols[b].bank, f"{a}/{b} in different banks")
        return symbols[a].address - symbols[b].address

    size, cap = party["End"], constants["PARTY_LENGTH"]
    checks = {
        "wPartyMon2 - wPartyMon1": (diff("wPartyMon2", "wPartyMon1"), size),
        "wPartyMonOTs - wPartyMons": (diff("wPartyMonOTs", "wPartyMons"), cap * size),
        "NAME_LENGTH": (diff("wPartyMon2OT", "wPartyMon1OT"), constants["NAME_LENGTH"]),
        "PLAYER_NAME_LENGTH": (diff("wPartyMon1Extra", "wPartyMon1OT"), constants["PLAYER_NAME_LENGTH"]),
        "MON_NAME_LENGTH": (diff("wPartyMon2Nickname", "wPartyMon1Nickname"), constants["MON_NAME_LENGTH"]),
        "wPartyMonNicknames - wPartyMonOTs": (diff("wPartyMonNicknames", "wPartyMonOTs"), cap * constants["NAME_LENGTH"]),
        "wPartyMonNicknamesEnd": (diff("wPartyMonNicknamesEnd", "wPartyMonNicknames"), cap * constants["MON_NAME_LENGTH"]),
        "wPlayerName length": (diff("wRivalName", "wPlayerName"), constants["NAME_LENGTH"]),
        "wPlayerStatLevels": (diff("wEnemyStatLevels", "wPlayerStatLevels"), constants["NUM_LEVEL_STATS"]),
        "wOTPartyMons stride": (diff("wOTPartyMonOTs", "wOTPartyMons"), cap * size),
        "wOTPartyDataEnd": (diff("wOTPartyDataEnd", "wOTPartyMonNicknames"), cap * constants["MON_NAME_LENGTH"]),
    }
    for side in ("Player", "Enemy"):
        for index, suffix in STAGES:
            checks[f"w{side}{suffix}Level"] = (diff(f"w{side}{suffix}Level", f"w{side}StatLevels"), constants[index])
    pockets = {}
    for pocket, (count, data, end, constant) in POCKETS.items():
        capacity, rest = divmod(diff(end, data) - 1, 2)
        checks[f"{pocket} capacity"] = (capacity, constants[constant])
        checks[f"{pocket} terminator byte"] = (rest, 0)
        checks[f"{pocket} count precedes data"] = (diff(data, count), 1)
        pockets[pocket] = {"count": count, "data": data, "capacity": capacity}
    for label, (measured, expected) in checks.items():
        require(measured == expected, f"{label}: sym geometry {measured} != source {expected}")
    require(constants.get("SFX_ITEM") == 1 and constants.get("SFX_WRONG") == 0x19
            and constants.get("SFX_BUMP") == 0x24 and constants.get("SFX_READ_TEXT_2") == 0x08,
            f"sfx_constants.asm ids moved: {constants}")

    overlay = {"artifact": "polished_overlay", "base_sha1": clean["sha1"], "rom_sha1": out["sha1"],
               "md5": out["md5"], "sym": SYM.name, "sym_sha256": sym_sha, "abi": abi,
               "ram": {name: ram[name] for name in OVERLAY_RAM + PANEL_RAM},
               "panel": panel_block(ram), "trade": trade_block(symbols, prov)}
    for name in OVERLAY_RAM + PANEL_RAM:
        require(ram_bank[name] == 0, f"{name} outside WRAM0")
    sfx = {name: constants[const] for name, const in SFX_CODES}
    for name in SFX_SYMBOLS:
        require(name in symbols, f"{SYM.name}: sound service symbol {name} missing")
    require(symbols["PlaySFX"][0] == 0 and symbols["CheckSFX"][0] == 0, "PlaySFX/CheckSFX must be ROM0")
    overlay["sfx"] = {"codes": sfx, "caps": ["SLINK_CAP_SFX", "SLINK_CAP_SFX_NOTIFY"],
                     "entry": "jp SlinkSfxService (patch/polished/src/slink.asm, via SlinkDelayFrameBridge)",
                     "play_sfx": list(symbols["PlaySFX"]), "check_sfx": list(symbols["CheckSFX"]),
                     "music_fade": symbols["wMusicFade"][1], "cur_sfx": symbols["wCurSFX"][1],
                     "service": list(symbols["SlinkSfxService"])}
    rom, rom_derived, layout = rom_tables(symbols, constants)
    source = pack.source_block()
    source.update({"artifact": ARTIFACT, "overlay_sha1": out["sha1"], "overlay_sym_sha256": sym_sha,
                   "overlay_provenance_sha256": hashlib.sha256(raw_prov).hexdigest(),
                   "generator": "tools/gen_polished_profile.py"})
    selected = {
        "title": TITLE, "variant": TITLE, "artifact": ARTIFACT, "repo": pack.LOCK["source"]["url"],
        "sym": SYM.name, "rom_sha1": clean["sha1"], "header_title": clean["title"],
        "ram": ram, "ram_bank": ram_bank, "hram": hram, "constants": constants, "rom": rom, "layout": layout,
        "structs": {"party": party, "battle": battle},
        "derived": {"party_struct_size": size, "battle_struct_size": battle["StructEnd"], "party_capacity": cap,
                    "name_length": constants["NAME_LENGTH"], "mon_name_length": constants["MON_NAME_LENGTH"],
                    "player_name_length": constants["PLAYER_NAME_LENGTH"], "num_boxes": constants["NUM_BOXES"],
                    "rom_size": out["size"], "pockets": pockets, **rom_derived,
                    # (species_id, form_id, record_index) of every REGIONAL/variant form = a different mon (owner
                    # 2026-10-04) whose effective species id is its BaseData record index (polished_codec.effective_species);
                    # every other form is cosmetic and keys as form 0 (polished_codec.key_form). From forms_index.json.
                    "variant_forms": sorted([r["species_id"], r["form_id"], r["record_index"]] for r in json.loads(
                        (OUT.parent / "forms_index.json").read_text(encoding="utf-8"))["variant_forms"])},
        "overlay": overlay,
    }
    return {"schema": "gen2-profile-v1", "generator": "tools/gen_polished_profile.py", "source": source,
            "write_authority": "NONE", "titles": {TITLE: selected}}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    try:
        data = render(build()).encode()
        if args.check:
            require(OUT.is_file() and OUT.read_bytes() == data, f"profile missing or stale: {OUT}")
            print("Polished profile is current (SOURCE only)")
        else:
            OUT.write_bytes(data)
            print(f"wrote {OUT.relative_to(ROOT)} (SOURCE only)")
        return 0
    except (ValueError, OSError, KeyError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
