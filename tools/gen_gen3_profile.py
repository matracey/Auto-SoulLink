#!/usr/bin/env python3
"""Generate the Gen 3 packs' profile.json from Lua literals and pinned pret facts.

The primary source is the literal address database in `lua/games/gen3_frlge.lua`
(`GEN3.profiles.vanilla` / `.ap` / `.radical_red` and the additive
`GEN3.profiles.emerald`).  Nothing here re-types an address: the tables are parsed
out of the Lua text by a small strict parser for the subset those tables use
(`KEY = 0xHEX | integer | "string" | true/false/nil | { ... }` plus `+`/`*`
arithmetic and comments).  The file is never `require`d under a Lua runtime.
RR-P5 additions are decoded from captured instruction/literal-pool anchors in
the admitted RR binary. The anchors are shipped for ROM-free generation and
verified against patch/build/slink_RR.gba whenever that local witness exists.

Packs (PLAN §4, §5.1):
    data/games/gen3_frlg/profile.json   titles firered, leafgreen (admitted; both read the
                                        `vanilla` table today) + the unadmitted titles
                                        firered_ap (`.ap`). Emerald lives only in its
                                        own pret-derived pack.
    data/games/gen3_rr/profile.json     title radical_red (`.radical_red`) + a `native` block
                                        for kind `companion`: the companion-patch mailbox ABI and
                                        the ghost/object-event addresses, sourced from the patch's
                                        own C (patch/src/handlers.c -- the companion is BUILT from
                                        it, so it is the ABI's actual authority, not a copy of it;
                                        C5-6 deleted the old archive/gen3-old-client:lua/mailbox.lua + archive/gen3-old-client:lua/peer_ghost_npc.lua
                                        scrape targets), each with its source file:line in the
                                        sibling `_src` map

    data/games/gen3_emerald/profile.json  title emerald (admitted at EG4), NOT Lua-sourced:
                                        addresses by name from data/gen3/pret/pokeemerald.sym,
                                        constants from pinned pret/pokeemerald (build_emerald)

    python tools/gen_gen3_profile.py            # rewrite all three profiles
    python tools/gen_gen3_profile.py --check    # exit 1 if either committed file is stale
                                                # (the P2 exit condition "profile diff = 0")
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from tools import rr_companion  # noqa: E402

SRC = "lua/games/gen3_frlge.lua"
HANDLERS_SRC = "patch/src/handlers.c"
PRET_PIN = "pret/pokefirered@c75f352304d529f6ba92d4f74b9cf8b5c3810788"
STORAGE_HEADER = f"{PRET_PIN}:include/pokemon_storage_system.h"

# Pinned C layout facts, independent of the optional local pret checkout.
# boxes follows u8 currentBox, but BoxPokemon begins with u32 personality:
# ARM alignment inserts three padding bytes (the header's 0x0001 comment is wrong).
FRLG_DERIVED = {
    "BATTLE_STRUCT_MOVE_TARGET_OFF": (0x0C, f"{PRET_PIN}:include/battle.h:373-380 "
                                     "(BattleStruct.moveTarget; u8 prefix + wrappedMove[8])"),
    "BATTLE_STRUCT_CHOSEN_MOVE_POS_OFF": (0x80, f"{PRET_PIN}:include/battle.h:373-412 "
                                         "(BattleStruct.chosenMovePositions; offsetof verified from prefix)"),
    "EXPERIENCE_TABLE_ENTRY_COUNT": (101, f"{PRET_PIN}:src/data/pokemon/experience_tables.h:18 "
                                    "(gExperienceTables[][MAX_LEVEL + 1])"),
    "MAX_LEVEL": (100, f"{PRET_PIN}:include/constants/pokemon.h:187 (MAX_LEVEL)"),
    "BOX_DATA_OFFSET": (4, f"{STORAGE_HEADER}:44-48; "
                         f"{PRET_PIN}:include/pokemon.h:105-108 (u32 alignment)"),
    "BOXES_PER_STORE": (14, f"{STORAGE_HEADER}:7 (TOTAL_BOXES_COUNT)"),
    "MONS_PER_BOX": (5 * 6, f"{STORAGE_HEADER}:8-10 (IN_BOX_ROWS * IN_BOX_COLUMNS)"),
    "PARTY_CAPACITY": (6, f"{PRET_PIN}:include/constants/global.h:78 (PARTY_SIZE)"),
    # ── P4 card C4-2a: trainer/location/badges/bag/battle facts the new client needs,
    # none of which the vanilla Lua table carries (docs/gen3/research/p4_gen1_contract_map.md
    # §2 item 3, §3.2). Every offset below is read straight off the pinned pret commit, never
    # off Radical Red (memory: RR types are non-standard).
    "SB2_OT_ID_OFFSET": (0x0A, f"{PRET_PIN}:include/global.h:332 (SaveBlock2.playerTrainerId); "
                         f"{PRET_PIN}:src/pokemon.c:1798-1801 (u32 little-endian assembly)"),
    "SB2_NAME_OFFSET": (0, f"{PRET_PIN}:include/global.h:329 (SaveBlock2.playerName)"),
    "SB1_LOCATION_MAP_GROUP_OFFSET": (0x04, f"{PRET_PIN}:include/global.h:759-762 "
                                      "(SaveBlock1.location); "
                                      f"{PRET_PIN}:include/global.h:392-398 (struct WarpData.mapGroup)"),
    "SB1_LOCATION_MAP_NUM_OFFSET": (0x05, f"{PRET_PIN}:include/global.h:759-762 "
                                    "(SaveBlock1.location); "
                                    f"{PRET_PIN}:include/global.h:392-398 (struct WarpData.mapNum)"),
    "SB1_BADGE_BYTE_OFFSET": (0x104, f"{PRET_PIN}:include/constants/flags.h:1324,1364-1371 "
                              "(SYS_FLAGS=0x800; FLAG_BADGE01_GET..FLAG_BADGE08_GET = "
                              "SYS_FLAGS+0x20..+0x27; byte = flag_id >> 3, relative to "
                              "SaveBlock1.flags[])"),
    "OUTCOME_WON": (1, f"{PRET_PIN}:include/constants/battle.h:76 (B_OUTCOME_WON)"),
    "OUTCOME_LOST": (2, f"{PRET_PIN}:include/constants/battle.h:77 (B_OUTCOME_LOST)"),
    "OUTCOME_DREW": (3, f"{PRET_PIN}:include/constants/battle.h:78 (B_OUTCOME_DREW)"),
    "OUTCOME_RAN": (4, f"{PRET_PIN}:include/constants/battle.h:79 (B_OUTCOME_RAN)"),
    "OUTCOME_CAUGHT": (7, f"{PRET_PIN}:include/constants/battle.h:82 (B_OUTCOME_CAUGHT)"),
    "BATTLE_TYPE_TRAINER_MASK": (0x08, f"{PRET_PIN}:include/constants/battle.h:50 "
                                 "(BATTLE_TYPE_TRAINER)"),
    "BATTLE_TYPE_DOUBLE_MASK": (0x01, f"{PRET_PIN}:include/constants/battle.h:47 "
                                "(BATTLE_TYPE_DOUBLE)"),
    "GMAIN_INBATTLE_OFFSET": (0x439, "data/games/gen3_frlg/write_checkpoint.json: "
                              "firered.predicates.in_battle (gMain+0x439; verified live by the "
                              "cb1_overworld census probe, docs/gen3/probes/census_fr_overworld_"
                              "2026-09-21.txt)"),
    "GMAIN_INBATTLE_MASK": (0x02, "data/games/gen3_frlg/write_checkpoint.json: "
                            "firered.predicates.in_battle"),
    "BATTLE_MOVE_ENTRY_SIZE": (12, "data/gen3/pret/pokefirered.sym:26905 gBattleMoves "
                               "(0x10A4 bytes / 355 moves = 12); "
                               f"{PRET_PIN}:include/pokemon.h:284"),
    "BATTLE_MOVE_PP_OFFSET": (4, f"{PRET_PIN}:include/pokemon.h:238-249 (struct BattleMove.pp)"),
    # boxes card (C4-3) addendum: PP restore needs the species growth rate, which lives inside
    # the existing BASESTATS entry (rom.BASESTATS_ADDR / BASESTATS_ADDR_BY_GAME_CODE) -- no new
    # ROM address, just its offset.
    "BASESTATS_GROWTH_RATE_OFFSET": (0x13, f"{PRET_PIN}:include/pokemon.h:230 "
                                     "(struct SpeciesInfo.growthRate)"),
    "SHEDINJA_SPECIES_ID": (303, f"{PRET_PIN}:include/constants/species.h:312 (SPECIES_SHEDINJA); "
                            "used by CalculateMonStats' 1-HP exception, src/pokemon.c:2124-2132"),
    # C4-ACTIVE-FAINT-P: mechanism P (docs/gen3/research/active_faint_in_battle_scope_2026-09-23.md
    # §2d) -- the Perish-counter-0 + no-op commit. Together with the ram fields below these are
    # the pack's active_faint capability; RR ships none of them (CFRU layout OPEN, G5).
    "STATUS3_PERISH_SONG": (0x20, f"{PRET_PIN}:include/constants/battle.h:138 (STATUS3_PERISH_SONG)"),
    "DISABLE_STRUCT_SIZE": (0x1C, f"{PRET_PIN}:include/battle.h:139-172 (sizeof(struct DisableStruct)); "
                            "data/gen3/pret/pokefirered.sym:126 gDisableStructs 0x70 bytes / 4"),
    "DISABLE_STRUCT_PERISH_TIMER_OFF": (0x0F, f"{PRET_PIN}:include/battle.h:153-154 "
                                        "(perishSongTimer:4 low nibble, perishSongTimerStartValue:4)"),
    "B_ACTION_NOTHING_FAINTED": (13, f"{PRET_PIN}:include/battle.h:48 (B_ACTION_NOTHING_FAINTED)"),
    # C5-6: gBattleMons[b].statStages[STAT_ATK] -- ATK..EVA are the 7 bytes from here, in the
    # wire order; statStages[STAT_HP] at +0x18 is never shown (reads.lua read_stat_stages).
    "BATTLE_MON_STAT_STAGES_OFF": (0x19, f"{PRET_PIN}:include/pokemon.h:187 (BattlePokemon."
                                   "statStages at 0x18); include/constants/pokemon.h:166-175 "
                                   "(STAT_HP=0, STAT_ATK=1 .. STAT_EVASION=7)"),
    # G5-STAGES-COHERENCE: the identity a battler's stages are attached by (reads.lua
    # battler_holds) and the link flag that withholds them (battler ids are not positions in a
    # link battle, pret src/battle_controllers.c:151-168).
    "BATTLE_MON_PERSONALITY_OFF": (0x48, f"{PRET_PIN}:include/pokemon.h:202 (BattlePokemon."
                                   "personality); data/gen3/pret/pokefirered.sym CopyPlayerMonData "
                                   "0x08030C04: str r0,[sp,#0x48] @0x08030E70 after "
                                   "GetMonData(MON_DATA_PERSONALITY)"),
    "BATTLE_MON_OT_ID_OFF": (0x54, f"{PRET_PIN}:include/pokemon.h:205 (BattlePokemon.otId); "
                             "CopyPlayerMonData: str r0,[sp,#0x54] @0x08030F16 after "
                             "GetMonData(MON_DATA_OT_ID)"),
    "BATTLE_TYPE_LINK_MASK": (0x02, f"{PRET_PIN}:include/constants/battle.h:48 (BATTLE_TYPE_LINK)"),
}

# ── more P4 card C4-2a facts: symbols read straight out of each title's own .sym file (same
# technique as the gPokemonStorage match below), so the address is self-verifying per title
# rather than a literal this file could get wrong for one of the two ROMs.
FRLG_SYM_ADDR = {
    # profile section, key -> the unique global .sym symbol
    ("ram", "TRAINER_OPPONENT_ADDR"): "gTrainerBattleOpponent_A",
    ("rom", "EXPERIENCE_TABLES_ADDR"): "gExperienceTables",
    ("rom", "BATTLE_MOVES_ADDR"): "gBattleMoves",
    ("rom", "PP_UP_GET_MASK_ADDR"): "gPPUpGetMask",
    # Shared engine action fields. Each title binds its own symbols; EXPLODE-BIND adds the
    # chosen move and BattleStruct pointer without a companion-patch dependency.
    ("ram", "STATUS3_ADDR"): "gStatuses3",
    ("ram", "DISABLE_STRUCTS_ADDR"): "gDisableStructs",
    ("ram", "CHOSEN_ACTION_ADDR"): "gChosenActionByBattler",
    ("ram", "CHOSEN_MOVE_ADDR"): "gChosenMoveByBattler",
    ("ram", "BATTLE_STRUCT_PTR_ADDR"): "gBattleStruct",
    ("ram", "BATTLE_COMM_ADDR"): "gBattleCommunication",
}

# C5-9: the same three values for the vanilla titles, taken from each title's own .sym.  They are
# stored Thumb (|1) because that is the form gBattleMainFunc holds and the write_checkpoint pack's
# `expect_symbol` already produces (syms[symbol][0] | 1).  The symbol's .sym scope letter varies
# (BeginBattleIntroDummy/BeginBattleIntro are `g`, BattleIntroGetMonsData is `l`), so the lookup
# below accepts any scope rather than the `g`-only one FRLG_SYM_ADDR uses.
FRLG_SYM_THUMB_ADDR = {
    ("rom", "BEGIN_BATTLE_INTRO_ADDR"): "BeginBattleIntro",
    ("rom", "BEGIN_BATTLE_INTRO_DUMMY_ADDR"): "BeginBattleIntroDummy",
    ("rom", "BATTLE_INTRO_GET_MONS_DATA_ADDR"): "BattleIntroGetMonsData",
}
# _thumb_keys runs inside _title(), before these facts exist, so their keys are declared here.
INTRO_THUMB_KEYS = ("BEGIN_BATTLE_INTRO_ADDR", "BEGIN_BATTLE_INTRO_DUMMY_ADDR",
                    "BATTLE_INTRO_GET_MONS_DATA_ADDR")

# ── gen3_rr facts sourced from the OLD client (archive/gen3-old-client:lua/memory_gba.lua), per the owner's 2026-09-23
# ruling relayed on card C4-2a: the old RR client is production-tested, so its RAM offsets are
# acceptable RR evidence where no ROM/pret evidence exists. Every one of these is read from
# archive/gen3-old-client:lua/memory_gba.lua GENERICALLY (no RR-only branch), i.e. the exact same code path already serves
# RR live today -- never a value the pack already derives from the ROM (that trap is
# SB1_PTR_ADDR/SB2_PTR_ADDR above, which this dict does not touch).
RR_DERIVED = {
    "SB1_LOCATION_MAP_GROUP_OFFSET": (0x04, "archive/gen3-old-client:lua/memory_gba.lua:271,1112 (old-client RR profile, "
                                      "production-tested: mapGroup read generically off SB1_PTR_ADDR)"),
    "SB1_LOCATION_MAP_NUM_OFFSET": (0x05, "archive/gen3-old-client:lua/memory_gba.lua:272,1113 (old-client RR profile, "
                                    "production-tested)"),
    "SB1_BADGE_BYTE_OFFSET": (0x104, "archive/gen3-old-client:lua/memory_gba.lua:1177,1181 (old-client RR profile, "
                              "production-tested: M.readBadges is generic over M.SB1_FLAGS_OFFSET)"),
    "BATTLE_TYPE_TRAINER_MASK": (0x08, "archive/gen3-old-client:lua/memory_gba.lua:378 (old-client RR profile, "
                                 "production-tested)"),
    "BATTLE_TYPE_DOUBLE_MASK": (0x01, "archive/gen3-old-client:lua/memory_gba.lua:385 (old-client RR profile, "
                                "production-tested)"),
    "OUTCOME_WON": (1, "archive/gen3-old-client:lua/memory_gba.lua:389 (old-client RR profile, production-tested; "
                    "unconditional, never overridden per-profile)"),
    "OUTCOME_LOST": (2, "archive/gen3-old-client:lua/memory_gba.lua:390 (old-client RR profile, production-tested)"),
    "OUTCOME_DREW": (3, "archive/gen3-old-client:lua/memory_gba.lua:391 (old-client RR profile, production-tested: "
                     "\"CFRU inserts DREW=3, shifting RAN from 3->4\")"),
    # Confirmed unrenumbered by CFRU: data/games/gen3_frlge/rr_species.json["303"] == "Shedinja",
    # the same id as pret's SPECIES_SHEDINJA (FRLG_DERIVED above).
    "SHEDINJA_SPECIES_ID": (303, 'data/games/gen3_frlge/rr_species.json:"303"="Shedinja" '
                            "(RR species table; CFRU keeps this id unrenumbered)"),
    "SB2_NAME_OFFSET": (0x00, "archive/gen3-old-client:lua/memory_gba.lua:721-725 (old-client RR profile, "
                        "production-tested: M.readTrainerName decodes playerName generically at SB2+0)"),
    "BATTLE_MON_STAT_STAGES_OFF": (0x19, "archive/gen3-old-client:lua/memory_gba.lua:402-406 (old-client RR profile, "
                                   "production-tested: M.readStatStages; CFRU puts type3 at "
                                   "+0x18, so ATK..EVA start at +0x19)"),
}

# RR-P5 binary witnesses: file offsets, NOT GBA virtual addresses. These bytes
# were read from the admitted companion build and checked against clean RR. The build is named by its CANONICAL sha1 (the ROM with
# its fixed-width version field zeroed, patch/tools/rom_identity.py; patch/dist/companion_pins.json), so a release stamp does not
# rewrite every citation. Keep complete reader bodies + literal pools, so a pointer alone is not evidence
# for which field is being accessed. No Capstone dependency in the generator.
RR_WITNESS_CANONICAL = rr_companion.canonical_sha1()
RR_ROM_ANCHORS = {
    "controller_exec_marker": (0x17248,
        "00b50006030e0848006802210840002810d0064a06499800401801680907106808431060"
        "0ee000004c2b0202c83b02025ce42508044a054998004018116800680143116001bc0047"
        "c83b02025ce42508"),
    "controller_exec_reader": (0x141DC,
        "154c1649164b1d78a800401802681001f0210906084310431102084312031043216801409846002901d0"),
    "controller_exec_reader_pool": (0x14234, "c83b0202"),
    "calculate_pp": (0x4101C,
        "10b50004000c1206120e0d4c43001b189b001b191c790b48101803780b4052001341"
        "9800c018800060436421a2f1e6ff24182406240e201c10bc02bc08470000d0211509a1de2508"),
    "box_level": (0x3E830,
        "70b5051c0b21002201f084fa041c2404240c281c1921002201f07cfa031c0122104e1149"
        "e000001b80004118c87c20256d01684304308019006898420bd80c1c0132fa2a07dc9100"
        "e07c68430918891908689842f4d9501e0006000e70bc02bc084700004c511509ec987b09"),
    "trainer_id": (0xCC1E4,
        "06480268507b0006117b09040843d17a09020843917a0843704700000c500003"),
    "pp_up_masks": (0x25DEA1, "030c30c0"),
    # Expanded CFRU code actually loads and stores the callback into gBattleMainFunc.
    "action_callback_store": (0x1070626, "2d4b2d4a1a60"),
    "action_callback_pool": (0x10706DC, "844f000341400108"),
    "action_callback_entry": (0x14040, "f0b557464e464546e0b487b00f480021"),
    # C5-9 (the rival-swap window): every site that installs gBattleMainFunc with a phase value.
    # Each is a Thumb LDR pair (the gBattleMainFunc pointer, then the value) followed by STR, so
    # the pool word alone is not evidence -- the store is.  pret: battle_controllers.c:88,113,150,
    # 175,213 assigns BeginBattleIntro / BeginBattleIntroDummy per battle type, and
    # BeginBattleIntro's own tail writes BattleIntroGetMonsData|1 (battle_main.c:2196-2200).
    "intro_store_controllers": (0xD27C,
        "80b4194919480860002219488046002318498c46184fff26184d194c4046614604318c46"
        "043901c1d11908783043087050190370101903700132032aeeddfff7b9ff104800240460"
        "65f0fcf836f078fe00480047992b04090c48047008bc9846f0bc01bc00470000844f0003"
        "bd23010811e30208e04f0003d63b0202f83f0202fc3f0202c83b0202542b0202dc3d0202"
        "30b50448006802210840002804d000f0bbf803e04c2b020200f01ef800f01efa0b480068"
        "4021084000280ed10024094801788c4209da051c2006000e00211af12dfd013428788442"
        "f6db30bc01bc00474c2b0202cc3b020210b50c4802680124131c2340002b4fd109490a48"
        "0860802040021040002817d0074a084911600848037051604470074902206fe04c2b0202"
        "844f0003c1230108"),
    "intro_store_begin": (0x123C0,
        "00b500f037f804490020487003490448086001bc00470000823e0202844f0003ad2f0108"),
    "intro_getmons_body": (0x12FAC,
        "30b5034d2878002804d0012814d02ee0823e0202074c68782070002000210022faf7eaff"
        "207804f039f92878013028701de00000c43b020208480268002a16d16878013068700649"
        "0006000e097888420cd104490448086009e00000c83b0202cc3b0202844f000321300108"
        "2a7030bc01bc0047"),
    # G4-PH (mechanism P on RR, docs/gen3/research/rr_active_faint_parity_scope_2026-09-23.md §2,
    # facts re-asserted by tools/research/rr_active_faint.py): CFRU end-turn state 34, the Perish
    # case (0x090923C8..0x0909243C) and its pool words (0x09092764..0x09092773).
    "perish_state34": (0x10923C8,
        "2027e64db30059599c46394201d1fff74ff9582002007243e14b9b181c8d002c01d1fff745f9fd23de48"
        "0370fc3b43708370c3701b337343ff26db4ad2180832d3791b071b0f03714671d84809d16346b94359"
        "51d64b1c60d64b03600068fff740f90f24d1790f332340a1430b43d371d14bf2e7"),
    "perish_state34_pool": (0x1092764, "fc3d0202e43b0202b82a02020c3e0202"),
    # HandleTurnActionSelectionState case 0, absent battler: gChosenActionByBattler[b] = 13
    # (LDR@0x0801412E, movs r1,#0xd @0x08014132), pool 0x08014164.
    "htas_absent_action": (0x14128, "0340002b26d00d4810180d21"),
    "htas_absent_action_pool": (0x14164, "7c3d0202"),
    # sTurnActionsFuncsTable[13] (table 0x08250038) = HandleAction_NothingIsFainted|1
    "turn_actions_nothing_fainted": (0x25006C, "3d6d0108"),
    # G5-STAGES-COHERENCE: CopyPlayerMonData's REQUEST_ALL_BATTLE builds a struct BattlePokemon
    # at sp+0 (pret src/battle_controller_player.c; the whole function is byte-identical to
    # FireRed's 0x08030C04..0x080313B0 in this ROM): movs r0,r4; movs r1,#field; bl GetMonData;
    # str r0,[sp,#off] -- MON_DATA_PERSONALITY (0) -> +0x48, MON_DATA_OT_ID (1) -> +0x54.
    "battlemon_personality_store": (0x30E68, "201c00210ef0bcfe1290"),
    "battlemon_otid_store": (0x30F0E, "201c01210ef069fe1590"),
    # InitBattleControllers: ldr r0,=gBattleTypeFlags; ldr r0,[r0]; movs r1,#LINK; ands r0,r1;
    # beq -> InitSinglePlayerBtlControllers, else bl InitLinkBtlControllers (pret
    # battle_controllers.c InitBattleControllers). Inside intro_store_controllers above.
}


def rr_rom_facts(rom: bytes | None = None) -> dict:
    """Decode captured RR instructions/literals; verify a supplied ROM before use.

    Embedded anchors keep ordinary profile generation reproducible without a
    copyrighted ROM. With a local binary, --check also checks every witness byte.
    A rebuild must be deliberately re-pinned if any witness changes.
    """
    anchors = [(name, off, bytes.fromhex(raw)) for name, (off, raw) in RR_ROM_ANCHORS.items()]
    if rom is not None:
        for name, off, raw in anchors:
            if rom[off:off + len(raw)] != raw:
                raise ValueError(f"RR ROM anchor mismatch: {name} at 0x{off:X}")

    def read(off: int, size: int) -> int:
        for _, start, raw in anchors:
            if start <= off and off + size <= start + len(raw):
                return int.from_bytes(raw[off - start:off - start + size], "little")
        raise ValueError(f"RR fact reads outside captured anchors: 0x{off:X}")

    def literal(off: int) -> int:
        ins = read(off, 2)
        if ins & 0xF800 != 0x4800:
            raise ValueError(f"not a Thumb LDR literal: 0x{off:X}")
        return read(((off + 4) & ~3) + (ins & 255) * 4, 4)

    # CalculatePPWithBonus: (move*2 + move)*4, then LDRB +4.
    move_stride = ((1 << ((read(0x41028, 2) >> 6) & 31)) + 1) \
        << ((read(0x4102C, 2) >> 6) & 31)
    pp_offset = (read(0x41030, 2) >> 6) & 31
    # GetLevelFromBoxMonExp: growth byte +0x13; growth row = 0x20 << 5;
    # u32 experience entries, and CMP level,#0xFA. RR is NOT vanilla 101/100.
    row_bytes = (read(0x3E85E, 2) & 255) << ((read(0x3E860, 2) >> 6) & 31)
    growth_offset = (read(0x3E85C, 2) >> 6) & 31
    # GetPlayerTrainerId assembles byte offsets D,C,B,A into u32 little endian.
    ot_offsets = [(read(off, 2) >> 6) & 31 for off in (0xCC1E8, 0xCC1EC, 0xCC1F2, 0xCC1F8)]
    if ot_offsets != list(range(ot_offsets[-1] + 3, ot_offsets[-1] - 1, -1)):
        raise ValueError("RR trainer ID reader is not four contiguous bytes")
    # The canonical SB2 pointer is ROM-derived in write_checkpoint (C3-33).
    # This witness checks that symbol; it never replaces the legacy profile value.
    if literal(0xCC1E4) != 0x0300500C:
        raise ValueError("RR trainer reader no longer uses canonical gSaveBlock2Ptr")
    if literal(0x1070626) != 0x03004F84 or read(0x107062A, 2) != 0x601A:
        raise ValueError("RR callback witness no longer stores into gBattleMainFunc")
    # C5-9: the intro window's callback installs (see RR_ROM_ANCHORS).  Each is LDR-the-pointer,
    # LDR-the-value, STR -- the same shape as the 0x1070626 witness above -- so the facts are
    # *stored*, not merely pooled.
    for ldr_ptr, ldr_val, str_site, label in (
            (0xD27E, 0xD280, 0xD282, "intro_store_controllers/BeginBattleIntroDummy"),
            (0xD374, 0xD376, 0xD378, "intro_store_controllers/BeginBattleIntro"),
            (0x123CC, 0x123CE, 0x123D0, "intro_store_begin/BattleIntroGetMonsData")):
        if literal(ldr_ptr) != 0x03004F84 or read(str_site, 2) != 0x6008:
            raise ValueError(f"RR intro callback install no longer stores into gBattleMainFunc "
                             f"({label} @0x{ldr_ptr:X})")
        if literal(ldr_val) & 1 == 0:
            raise ValueError(f"RR intro install is not a Thumb pointer ({label})")
    exec_flags = literal(0x1725A)
    if exec_flags != literal(0x1727C) or exec_flags != literal(0x141DC):
        raise ValueError("RR controller execution-flag writer/reader disagree")
    # G4-PH: mechanism P's layout, read out of CFRU's own Perish case (state 34).
    def imm8(off: int, op: int) -> int:        # the #imm8 of a Thumb `movs/adds/subs rN,#imm8`
        ins = read(off, 2)
        if ins & 0xFF00 != op:
            raise ValueError(f"RR Perish witness moved: 0x{off:X} is 0x{ins:04X}")
        return ins & 0xFF
    perish_bit = imm8(0x10923C8, 0x2700)                                  # movs r7,#0x20
    if read(0x10923D2, 2) != 0x4239 or read(0x1092418, 2) != 0x43B9:      # tst r1,r7 / bics r1,r7
        raise ValueError("RR Perish case no longer tests/clears gStatuses3 with r7")
    # movs r3,#0xfd; subs r3,#0xfc; adds r3,#0x1b; muls r3,r6 (r6 = battler)
    disable_size = imm8(0x10923EE, 0x2300) - imm8(0x10923F4, 0x3B00) + imm8(0x10923FC, 0x3300)
    if read(0x10923FE, 2) != 0x4373:
        raise ValueError("RR Perish case no longer scales the battler by the DisableStruct size")
    # adds r2,#8; ldrb r3,[r2,#7]; lsls r3,#0x1c; lsrs r3,#0x1c (the low nibble)
    ldrb = read(0x1092408, 2)
    if ldrb & 0xF83F != 0x7813 or read(0x109240A, 2) != 0x071B or read(0x109240C, 2) != 0x0F1B:
        raise ValueError("RR Perish case no longer reads the timer as a low nibble")
    timer_off = imm8(0x1092406, 0x3200) + ((ldrb >> 6) & 31)
    nothing_fainted = imm8(0x14132, 0x2100)                                # movs r1,#0xd
    if read(0x25006C, 4) != 0x08016D3D:
        raise ValueError("RR sTurnActionsFuncsTable[13] is not HandleAction_NothingIsFainted")
    perish_where = "perish_state34:0x090923C8..0x0909243C (CFRU end-turn state 34)"
    # G5-STAGES-COHERENCE: gBattleMons identity offsets and BATTLE_TYPE_LINK.
    def battlemon_store(off: int, field: int) -> int:
        if read(off, 2) != 0x1C20 or read(off + 2, 2) != 0x2100 | field:  # movs r0,r4; movs r1,#field
            raise ValueError(f"RR CopyPlayerMonData witness moved at 0x{off:X}")
        hi, lo = read(off + 4, 2), read(off + 6, 2)
        if hi & 0xF800 != 0xF000 or lo & 0xF800 != 0xF800:
            raise ValueError(f"RR CopyPlayerMonData witness lost its BL at 0x{off + 4:X}")
        rel = ((hi & 0x7FF) << 12) | ((lo & 0x7FF) << 1)
        getmon = 0x08000000 + off + 8 + (rel - (1 << 23) if rel & (1 << 22) else rel)
        store = read(off + 8, 2)
        if store & 0xFF00 != 0x9000:                                       # str r0,[sp,#imm8*4]
            raise ValueError(f"RR CopyPlayerMonData witness is not a str r0,[sp] at 0x{off + 8:X}")
        return (store & 0xFF) * 4, getmon
    personality_off, getmon_a = battlemon_store(0x30E68, 0)
    ot_id_off, getmon_b = battlemon_store(0x30F0E, 1)
    if getmon_a != getmon_b:
        raise ValueError("RR CopyPlayerMonData identity stores call different getters")
    if literal(0xD30E) != 0x02022B4C or read(0xD310, 2) != 0x6800 or read(0xD314, 2) != 0x4008:
        raise ValueError("RR InitBattleControllers no longer tests gBattleTypeFlags")
    link_mask = imm8(0xD312, 0x2100)                                       # movs r1,#LINK
    battlemon_where = ("CopyPlayerMonData REQUEST_ALL_BATTLE (byte-identical to FireRed "
                       f"0x08030C04), BL GetMonData 0x{getmon_a:08X}")
    facts = {
        ("ram", "STATUS3_ADDR"): (literal(0x10923CA),
            f"{perish_where} LDR@0x090923CA pool@0x09092764"),
        ("ram", "DISABLE_STRUCTS_ADDR"): (literal(0x1092402),
            f"{perish_where} LDR@0x09092402 pool@0x09092770"),
        ("derived", "STATUS3_PERISH_SONG"): (perish_bit,
            f"{perish_where} movs r7,#imm@0x090923C8; tst@0x090923D2, bics@0x09092418"),
        ("derived", "DISABLE_STRUCT_SIZE"): (disable_size,
            f"{perish_where} 0x090923EE..0x090923FE (movs #0xfd, subs #0xfc, adds #0x1b, muls)"),
        ("derived", "DISABLE_STRUCT_PERISH_TIMER_OFF"): (timer_off,
            f"{perish_where} adds r2,#8 @0x09092406; ldrb [r2,#7] @0x09092408; low nibble @0x0909240A..0C"),
        ("derived", "BATTLE_MON_PERSONALITY_OFF"): (personality_off,
            f"battlemon_personality_store:0x08030E68..0x08030E72 {battlemon_where}, "
            "movs r1,#0 (MON_DATA_PERSONALITY), str r0,[sp,#imm] @0x08030E70"),
        ("derived", "BATTLE_MON_OT_ID_OFF"): (ot_id_off,
            f"battlemon_otid_store:0x08030F0E..0x08030F18 {battlemon_where}, "
            "movs r1,#1 (MON_DATA_OT_ID), str r0,[sp,#imm] @0x08030F16"),
        ("derived", "BATTLE_TYPE_LINK_MASK"): (link_mask,
            "intro_store_controllers:InitBattleControllers 0x0800D30C: LDR@0x0800D30E "
            "pool@0x0800D320 (gBattleTypeFlags 0x02022B4C), movs r1,#imm@0x0800D312, ands@0x0800D314"),
        ("derived", "B_ACTION_NOTHING_FAINTED"): (nothing_fainted,
            "htas_absent_action:movs r1,#imm@0x08014132; "
            "turn_actions_nothing_fainted:0x0825006C = HandleAction_NothingIsFainted|1"),
        ("ram", "CHOSEN_ACTION_ADDR"): (literal(0x1412E),
            "htas_absent_action:LDR@0x0801412E pool@0x08014164 (HTAS case-0 absent path)"),
        ("ram", "BATTLE_CONTROLLER_EXEC_FLAGS_ADDR"): (exec_flags,
            "controller_exec_marker:LDR@0x1725A/0x1727C pools@0x17274/0x17290; "
            "OR/STR@0x17266..0x1726A,0x17284..0x1728A; "
            "controller_exec_reader:LDR@0x141DC pool@0x14234,word-test@0x141FC..0x14204"),
        ("derived", "SB2_OT_ID_OFFSET"): (ot_offsets[-1], "trainer_id:0xCC1E4 byte assembly +0xA..D"),
        ("rom", "EXPERIENCE_TABLES_ADDR"): (literal(0x3E850), "box_level:LDR@0x3E850 pool@0x3E894"),
        ("derived", "EXPERIENCE_TABLE_ENTRY_COUNT"): (row_bytes // 4, "box_level:0x3E85E..0x3E862 row stride / u32"),
        ("derived", "MAX_LEVEL"): (read(0x3E872, 2) & 255, "box_level:CMP@0x3E872"),
        ("rom", "BATTLE_MOVES_ADDR"): (literal(0x41026), "calculate_pp:LDR@0x41026 pool@0x4105C"),
        ("derived", "BATTLE_MOVE_ENTRY_SIZE"): (move_stride, "calculate_pp:0x41028..0x4102C index arithmetic"),
        ("derived", "BATTLE_MOVE_PP_OFFSET"): (pp_offset, "calculate_pp:LDRB@0x41030"),
        ("rom", "PP_UP_GET_MASK_ADDR"): (literal(0x41032), "calculate_pp:LDR@0x41032 pool@0x41060; bytes@0x25DEA1"),
        ("derived", "BASESTATS_GROWTH_RATE_OFFSET"): (growth_offset, "box_level:LDRB@0x3E85C"),
        ("rom", "HANDLE_TURN_ACTION_SELECTION_ADDR"): (literal(0x1070628),
            "action_callback_store:0x1070626..0x107062A; pool@0x10706DC; entry@0x14040 (Thumb)"),
        ("rom", "BEGIN_BATTLE_INTRO_ADDR"): (literal(0xD376),
            "intro_store_controllers:LDR@0xD374/0xD376 pools@0xD39C/0xD3A0 STR@0xD378; "
            "the BeginBattleIntro body is the intro_store_begin anchor (0x123C0, == FR)"),
        ("rom", "BEGIN_BATTLE_INTRO_DUMMY_ADDR"): (literal(0xD280),
            "intro_store_controllers:LDR@0xD27E/0xD280 pools@0xD2E4/0xD2E8 STR@0xD282"),
        ("rom", "BATTLE_INTRO_GET_MONS_DATA_ADDR"): (literal(0x123CE),
            "intro_store_begin:LDR@0x123CC/0x123CE pools@0x123DC/0x123E0 STR@0x123D0 (the tail of "
            "BeginBattleIntro); the data-request phase is the intro_getmons_body anchor (0x12FAC)"),
    }
    return {key: (value, f"rom:patch/build/slink_RR.gba canonical-sha1={RR_WITNESS_CANONICAL} {where}")
            for key, (value, where) in facts.items()}

SCHEMA = "gen3-profile-v1"

# RR 4.1 GetMonAbility (09079808), resolver (090796BC), randomizer (09078148),
# difficulty remap (09079584), trainer filter (0907797C), extended flags (090B8FB0).
RR_ABILITY_READER = {
    "mode": "rr_4_1",
    "rom_start": 0x08000000, "rom_end": 0x0A000000,
    "base_stats_pointer": 0x080001BC, "stride": 28,
    "slot_offsets": [22, 23, 26], "species_limit": 1376,
    "flags_base": 0x0203B174, "flags_start": 0x900,
    "flags": {"randomizer": 0x942, "facility": 0x930, "hardcore": 0x1034,
              "restricted": 0x103C, "temporary_team": 0x1042, "link": 0x104B,
              "scripted_battle": 0x104E},
    "normal_pool": 0x09162ED6, "restricted_pool": 0x09162E06,
    "trainer_table_pointer": 0x0800FC00, "trainer_stride": 40, "trainer_class_offset": 1,
    "fixed_trainer_classes": [1, 2, 3, 4, 5, 20, 47, 56, 83, 84, 86, 87, 89, 90],
    "hardcore_trainers": 0x0912C9B4, "hardcore_trainer_count": 153,
    "hardcore_trainer_stride": 60, "hardcore_trainer_offset": 8,
    "difficulty_remap": {
        "2": 88, "3": 102, "36": 28, "45": 95, "70": 92, "90": 115,
        "109": 86, "111": 171, "118": 192, "134": 235, "142": 29, "143": 29,
        "153": 195, "154": 229, "157": 192, "172": 192, "173": 39, "176": 30,
        "181": 226, "182": 235, "183": 226, "184": 221, "191": 51, "197": 7,
        "202": 158, "207": 192, "212": 88, "213": 92, "247": 95, "252": 18,
    },
    "difficulty_exceptions": {
        "3": {"891": 240, "1320": 3, "1321": 3},
        "45": {"248": 22, "889": 22, "579": 100},
        "134": {"1104": 22}, "182": {"1104": 22},
        "176": {"981": 176}, "191": {"308": 191},
    },
    "anchors": [
        {"rom_offset": 0x40D38, "expected_hex": "0049084709980709"},
        {"rom_offset": 0x10781A4, "expected_hex": "0004360c000c874203d8c01b02300004000c7040390088f73ff9"},
        {"rom_offset": 0x10B8FB0, "expected_hex": "0c4b0200c3180c481904090c814203d8d8100a4bc0187047"},
    ],
}
RR_ABILITY_WITNESSES = (
    (0x1079808, 0x60, "320f82eee23a37f7383fcfce4e6cd4c57dfe71ede74a67ce4b5c91ef3bdd38ad"),
    (0x10796BC, 0xE0, "66cc2ed253110b1d7140309ea74691c432cb3deb17977c1cb5d43f031b24fefe"),
    (0x1078148, 0xD0, "1a30c16feee90b1d9cc04eca1821832c63bb38ee7785e029ed0689c082162fdb"),
    (0x1079584, 0x138, "0e03dac9460565ea14361749b3792c805da53cee7361a7c0e07a4d90dc114403"),
    (0x107797C, 0x9C, "efb0f019035c4583448c23405df2dfce1af461a75df402ad1c1c6518e4f9ffcd"),
    (0x10B8FB0, 0x50, "c48b525b7e735bd36b4590bc40436097cdebdc29bc9ee09663803caab371fca6"),
)


def verify_rr_ability_witness(rom: bytes) -> None:
    for offset, size, digest in RR_ABILITY_WITNESSES:
        if hashlib.sha256(rom[offset:offset + size]).hexdigest() != digest:
            raise ValueError(f"RR ability routine changed at 0x{offset:X}")
    for anchor in RR_ABILITY_READER["anchors"]:
        raw = bytes.fromhex(anchor["expected_hex"])
        offset = anchor["rom_offset"]
        if rom[offset:offset + len(raw)] != raw:
            raise ValueError(f"RR ability anchor changed at 0x{offset:X}")


# ── pins (P0 card C0-2/C0-4, docs/gen3/research/pins_inventory.md) ───────────────
ROM_HASHES = {
    "firered": {"rom_sha1": "41cb23d8dccc8ebd7c649cd8fbb58eeace6e2fdc"},
    "leafgreen": {"rom_sha1": "574fa542ffebb14be69902d1d36f1ec0a4afd71e"},
    "radical_red": {"rom_sha1": "964f951a0fdaf209e4ea1344883ef0d557bb3a80",
                    "rom_md5": "8529f3a45d32bce4da637976fcf269d4"},
}

# pack -> [(title, profile key in GEN3.profiles, admitted)]
PACKS = {
    "gen3_frlg": [
        ("firered", "vanilla", True),
        ("leafgreen", "vanilla", True),
        ("firered_ap", "ap", False),
    ],
    "gen3_rr": [
        ("radical_red", "radical_red", True),
    ],
}

# Every key the Lua tables carry, and the section it lands in.  A key that is not
# listed fails the run: the generator stays honest about what it understands.
#   ram      EWRAM/IWRAM addresses the client reads or writes
#   rom      ROM addresses (incl. SE_SONG_HEADERS, BASESTATS_ADDR, CB2_* callbacks)
#   derived  sizes, offsets, counts, modes and flags
SECTION = {
    # ── ram ──
    "PARTY_COUNT_ADDR": "ram", "PARTY_BASE": "ram",
    "ENEMY_COUNT_ADDR": "ram", "ENEMY_BASE": "ram",
    "BATTLE_TYPE_ADDR": "ram", "BATTLE_OUTCOME_ADDR": "ram", "BATTLE_MONS_ADDR": "ram",
    "BATTLER_PARTY_INDEXES_ADDR": "ram", "BATTLERS_COUNT_ADDR": "ram",
    "BATTLE_MAIN_FUNC_ADDR": "ram", "LOCKED_MOVES_ADDR": "ram",
    "GMAIN_ADDR": "ram", "SB1_PTR_ADDR": "ram", "SB2_PTR_ADDR": "ram", "PSP_PTR_ADDR": "ram",
    "SPECIAL_VAR_BOX_ID_ADDR": "ram", "SPECIAL_VAR_BOX_POS_ADDR": "ram",
    "TASKS_BASE_ADDR": "ram", "BATTLE_RESULTS_ADDR": "ram",
    "CHOSEN_MOVE_ADDRS": "ram", "CHOSEN_ACTION_ADDR": "ram", "CHOSEN_MOVE_ADDR": "ram",
    "BATTLE_COMM_ADDR": "ram", "BATTLE_STRUCT_PTR_ADDR": "ram",
    "POKEMON_STORAGE_BASE": "ram", "CFRU_BOX_NAME_BASE": "ram",
    "BALL_POCKET_ADDR": "ram", "TRAINER_OPPONENT_ADDR": "ram",
    "REAL_PARTY_BACKUP_ADDR": "ram",
    # ── rom ──
    "RETURN_FROM_BATTLE_ADDR": "rom", "SE_SONG_HEADERS": "rom", "BASESTATS_ADDR": "rom",
    "POST_BATTLE_WRITER_TASKS": "rom", "CB2_EVOLUTION_LOAD_ADDR": "rom",
    "CB2_EVOLUTION_BEGIN_ADDR": "rom", "CB2_EVOLUTION_UPDATE_ADDR": "rom",
    "CB2_TRADE_EVOLUTION_UPDATE_ADDR": "rom", "CFRU_BASESTATS_PTR": "rom",
    # ── derived ──
    "SB2_ENC_KEY_OFFSET": "derived", "SB1_BALL_POCKET_OFFSET": "derived",
    "SB1_BALL_POCKET_COUNT": "derived", "SB1_FLAGS_OFFSET": "derived",
    "SB1_VARS_OFFSET": "derived", "OVERWORLD_MODE": "derived",
    "BASESTATS_ENTRY_SIZE": "derived", "TASK_STRUCT_SIZE": "derived",
    "BASESTATS_ADDR_BY_GAME_CODE": "derived",
    "GMAIN_CB2_OFFSET": "derived",
    "BATTLE_RESULTS_PLAYER_FAINTS_OFF": "derived", "BATTLE_RESULTS_FOE_FAINTS_OFF": "derived",
    "PARTY_IN_SB1": "derived", "SB1_PARTY_BASE_OFFSET": "derived",
    "BATTLE_STRUCT_MOVE_TARGET_OFF": "derived", "BATTLE_STRUCT_CHOSEN_MOVE_POS_OFF": "derived",
    "CFRU_COMPRESSED_BOX": "derived", "COMPRESSED_MON_SIZE": "derived",
    "BOXES_PER_STORE": "derived", "CFRU_BOX_BASES": "derived", "BOX_NAMES_OFFSET": "derived",
    "BAG_IN_EWRAM": "derived", "BALL_POCKET_ENC": "derived",
    "OUTCOME_CAUGHT": "derived", "OUTCOME_RAN": "derived",
    "SE_NUZLOCKE_START": "derived", "SE_GAME_OVER": "derived",
    "SE_NEW_LINK": "derived", "SE_LINKED_KO": "derived",
    "CFRU_NO_ENCRYPT": "derived",
}


# ── the Lua subset parser ────────────────────────────────────────────────────────
_TOK = re.compile(r"""
      (?P<ws>\s+)
    | (?P<comment>--\[\[.*?\]\]|--[^\n]*)
    | (?P<str>"[^"\n]*")
    | (?P<num>0[xX][0-9A-Fa-f]+|\d+)
    | (?P<name>[A-Za-z_]\w*)
    | (?P<punct>[{}\[\],=+*./\-])
""", re.VERBOSE | re.DOTALL)

NIL = object()


class _Lex:
    """Cursor-based lexer: only the table being parsed is ever lexed."""

    def __init__(self, text: str, pos: int) -> None:
        self.text, self.pos, self._peeked = text, pos, None

    def _lex(self) -> tuple[str, str]:
        while True:
            m = _TOK.match(self.text, self.pos)
            if not m:
                sys.exit(f"gen_gen3_profile: cannot lex {SRC} at {self.text[self.pos:self.pos + 40]!r}")
            self.pos = m.end()
            if m.lastgroup not in ("ws", "comment"):
                return m.lastgroup, m.group()

    def peek(self) -> tuple[str, str]:
        if self._peeked is None:
            self._peeked = self._lex()
        return self._peeked

    def take(self) -> tuple[str, str]:
        tok, self._peeked = self.peek(), None
        return tok

    def expect(self, want: str) -> None:
        kind, val = self.take()
        if val != want:
            sys.exit(f"gen_gen3_profile: expected {want!r}, got {val!r} ({kind})")

    # value ::= table | string | true|false|nil | expr
    def value(self):
        kind, val = self.peek()
        if val == "{":
            return self.table()
        if kind == "str":
            self.take()
            return val[1:-1]
        if kind == "name" and val in ("true", "false", "nil"):
            self.take()
            return {"true": True, "false": False, "nil": NIL}[val]
        return self.expr()

    # expr ::= term ('+' term)*   |   term ::= num ('*' num)*
    def expr(self) -> int:
        total = self.term()
        while self.peek()[1] == "+":
            self.take()
            total += self.term()
        return total

    def term(self) -> int:
        val = self.number()
        while self.peek()[1] == "*":
            self.take()
            val *= self.number()
        return val

    def number(self) -> int:
        kind, val = self.take()
        if kind != "num":
            sys.exit(f"gen_gen3_profile: expected a number, got {val!r}")
        return int(val, 16) if val[:2].lower() == "0x" else int(val)

    def table(self):
        """A Lua table literal.  Positional entries, or keys 1..n, come back as a list;
        anything else as a dict keyed by the literal key (stringified for JSON)."""
        self.expect("{")
        items: list[tuple[object, object]] = []
        while True:
            kind, val = self.peek()
            if val == "}":
                self.take()
                break
            if val == "[":                      # [16] = ...
                self.take()
                key = self.expr()
                self.expect("]")
                self.expect("=")
                items.append((key, self.value()))
            elif kind == "name" and val not in ("true", "false", "nil"):
                self.take()
                self.expect("=")
                items.append((val, self.value()))
            else:                               # positional entry
                items.append((None, self.value()))
            if self.peek()[1] == ",":
                self.take()
        keys = [k for k, _ in items]
        if not items:
            return {}
        if all(k is None for k in keys) or keys == list(range(1, len(items) + 1)):
            return [v for _, v in items]
        return {str(k): v for k, v in items}


def _table_at(text: str, assignment: str):
    """Parse the table literal assigned by `assignment`.  Anchored at line start so the
    prose copy of `GEN3.profiles.emerald = {...}` in a comment is not mistaken for it."""
    m = re.search(r"^" + re.escape(assignment) + r"\s*=\s*\{", text, re.M)
    if not m:
        sys.exit(f"gen_gen3_profile: {SRC} has no `{assignment} = {{`")
    return _Lex(text, m.end() - 1).table()


def parse_profiles(text: str) -> dict:
    """{profile key: table} from GEN3.profiles = {...} plus the additive .emerald."""
    out = {k: v for k, v in _table_at(text, "GEN3.profiles").items() if v is not NIL}
    out["emerald"] = _table_at(text, "GEN3.profiles.emerald")
    return out


# ── the companion-patch native ABI (gen3_rr only) ────────────────────────────────
# C5-6 deleted archive/gen3-old-client:lua/mailbox.lua and archive/gen3-old-client:lua/peer_ghost_npc.lua (the old Lua client's copies of the
# ABI). The companion patch is BUILT from patch/src/handlers.c, so that C source -- not a Lua
# mirror of it -- is the actual authority; every name below is read out of it, never re-typed.
# Two naming conventions collide here on purpose: every MB.OP_* opcode already shares its exact
# name with handlers.c's opcode enum (both say "OP_PING"), so those are looked up by name alone;
# everything else predates this generator and keeps its old Lua-side name via an explicit map to
# whatever handlers.c calls the same literal (e.g. MB.BASE is handlers.c's MAILBOX_ADDR).
MAILBOX_C_SYMBOL = {
    "BASE": "MAILBOX_ADDR", "SIG": "SLNK_SIG", "ABI": "ABI_VER",
    "BLOB_BUF": "SLINK_BLOB_BUF", "TEXT_BUF": "SLINK_TEXT_BUF", "MENU_BUF": "SLINK_MENU_BUF",
    "BATTLE_NOTIF": "BN", "TN_ENABLE": "TN", "CALC_OFF": "SLINK_CALC_OFF", "INFO": "SI",
    "INFO_MAXLINES": "INFO_ROWS", "INFO_PAGESLOT": "INFO_PAGE_SLOT", "INFO_BAR_W": "BAR_W",
    "EVR": "EV", "GH": "GH", "GHOST_PAL_BUF": "GHOST_PAL_BUF", "GPLAYER_AVATAR": "gPlayerAvatar",
    "SW": "SW", "TRADE_BASE": "RT_BASE", "EV_PLAYER_FAINT": "EV_PLAYER_FAINT", "EV_FOE_FAINT": "EV_FOE_FAINT",
    "EV_OUTCOME": "EV_OUTCOME", "EV_PARTY_ADD": "EV_PARTY_ADD", "EV_EVOLVE": "EV_EVOLVE",
}
# ghost/object-event addresses (post-RC feature; kept for the same byte-identical reason as
# everything else above -- native.lua doesn't read these today, but the profile shape must not
# change out from under a future consumer). Every one is ALSO a plain handlers.c #define.
GHOST_C_SYMBOL = {
    "OBJECT_EVENTS_BASE": "gObjectEvents", "CB2_OVERWORLD": "CB2_OVERWORLD",
    "SPRITES_BASE": "gSprites", "OBJ_PALETTE_BUF": "gPlttBufferUnfaded_OBJ",
    "CAMERA_Y_ADDR": "gSpriteCoordOffsetY",
}
# every MB.OP_* name is spelled identically in handlers.c's opcode enum
MAILBOX_OPCODES = (
    "OP_PING", "OP_FORCE_FAINT", "OP_FORCE_MOVE", "OP_CREATE_MON", "OP_FORCE_MOVE_SLOT",
    "OP_SPAWN_PEER_NPC", "OP_DESPAWN_PEER_NPC", "OP_SHOW_MESSAGE", "OP_PLAY_FANFARE",
    "OP_ARM_PEER_INTERACT", "OP_GHOST_SPAWN", "OP_GHOST_CLEAR", "OP_SET_ENEMY_PARTY",
    "OP_SHOW_MENU", "OP_SET_PARTY_MON", "OP_PLAY_SE", "OP_CHOOSE_PARTY_MON", "OP_TRADE_SCENE",
    "OP_SHOW_CHOICES", "OP_SHOW_BATTLE_MESSAGE", "OP_DEPOSIT_MON", "OP_WITHDRAW_MON",
    "OP_MEMORIALIZE", "OP_SHOW_INFO", "OP_RIVAL_SWAP",
    "OP_TRADE_PREPARE", "OP_TRADE_WITHDRAW", "OP_TRADE_STATUS",   # RR-DURABLE producer opcodes
)

# `#define NAME <literal>` or the pointer-cast form `#define NAME ((Type *)<literal>)`.
_CDEFINE_RE = (r"^#define\s+{name}\s+(?:\(\(\s*[A-Za-z_ ]+\*\)\s*)?"
              r"(0[xX][0-9A-Fa-f]+|\d+)u?\)?\s*(?://.*|/\*.*)?$")


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _c_define(text: str, cname: str) -> tuple[int, int]:
    """(value, match offset) of `#define cname ...` in handlers.c (plain or pointer-cast)."""
    m = re.search(_CDEFINE_RE.format(name=re.escape(cname)), text, re.M)
    if not m:
        sys.exit(f"gen_gen3_profile: {HANDLERS_SRC} no longer defines {cname}")
    lit = m.group(1)
    return (int(lit, 16) if lit[:2].lower() == "0x" else int(lit)), m.start()


ABI_SRC = "patch/src/trade_targets/abi.h"
V2_TARGETS = ("firered", "leafgreen", "emerald", "radical_red")


def _abi_number(expression: str, constants: dict[str, int]) -> int:
    """Evaluate only the integer-expression subset used by the canonical C ABI."""
    expression = re.sub(r"\b(0x[0-9a-fA-F]+|\d+)[uUlL]+\b", r"\1", expression)
    tree = ast.parse(expression.strip(), mode="eval")

    def number(node):
        if isinstance(node, ast.Constant) and type(node.value) is int:
            return node.value
        if isinstance(node, ast.Name) and node.id in constants:
            return constants[node.id]
        if isinstance(node, ast.BinOp):
            left, right = number(node.left), number(node.right)
            operations = {ast.Add: lambda: left + right, ast.Sub: lambda: left - right,
                          ast.Mult: lambda: left * right, ast.LShift: lambda: left << right,
                          ast.BitOr: lambda: left | right, ast.BitAnd: lambda: left & right}
            if type(node.op) in operations:
                return operations[type(node.op)]()
        raise ValueError(f"unsupported ABI expression: {expression}")

    return number(tree.body)


def native_abi() -> dict:
    """Read v2 constants and naturally aligned fixed-width structs from abi.h.

    This is layout evidence only, never a title binding or a READY assertion.
    Unknown declarations fail instead of silently moving subsequent fields.
    """
    source = (REPO / ABI_SRC).read_text(encoding="utf-8")
    text = re.sub(r"/\*.*?\*/|//[^\n]*",
                  lambda m: "\n" * m[0].count("\n"), source, flags=re.S)
    constants, structs, citations = {}, {}, {}
    declarations = [(m.start(), m[1], m[2]) for m in re.finditer(
        r"^[ \t]*#define[ \t]+(SLINK_[A-Z0-9_]+)[ \t]+([^\n]+)", text, re.M)]
    for enum in re.finditer(r"\benum\s+\w+\s*\{([^}]+)\}", text):
        for row in re.finditer(r"[^,]+", enum[1]):
            declaration = row[0].strip()
            if not declaration:
                continue
            parsed = re.fullmatch(r"(SLINK_[A-Z0-9_]+)\s*=\s*(.+)", declaration, re.S)
            if not parsed:
                raise ValueError(f"unsupported ABI enum declaration: {declaration}")
            offset = enum.start(1) + row.start() + len(row[0]) - len(row[0].lstrip())
            declarations.append((offset, parsed[1], parsed[2]))
    for offset, name, expression in sorted(declarations):
        constants[name] = _abi_number(expression, constants)
        citations[name] = f"{ABI_SRC}:{_line_of(text, offset)} ({name})"
    for struct in re.finditer(r"typedef\s+struct\s*\{([^}]+)\}\s*(\w+)\s*;", text):
        fields, offset, alignment = {}, 0, 1
        for declaration in struct[1].split(";"):
            declaration = declaration.strip()
            if not declaration:
                continue
            match = re.fullmatch(r"uint(8|16|32)_t\s+(.+)", declaration, re.S)
            if not match:
                raise ValueError(f"unsupported ABI declaration: {declaration}")
            width = int(match[1]) // 8
            alignment = max(alignment, width)
            for field in match[2].split(","):
                field = field.strip()
                parsed = re.fullmatch(r"(\w+)((?:\s*\[[^\]]+\])*)", field)
                if not parsed:
                    raise ValueError(f"unsupported ABI declaration: {declaration}")
                count = 1
                for dimension in re.findall(r"\[([^\]]+)\]", parsed[2]):
                    size = _abi_number(dimension, constants)
                    if size <= 0:
                        raise ValueError(f"unsupported ABI array size: {dimension}")
                    count *= size
                offset = (offset + width - 1) // width * width
                if parsed[1] in fields:
                    raise ValueError(f"duplicate ABI field: {parsed[1]}")
                fields[parsed[1]] = {"offset": offset, "width": width, "count": count}
                offset += width * count
        structs[struct[2]] = {"size": (offset + alignment - 1) // alignment * alignment,
                             "fields": fields}
        citations[struct[2]] = f"{ABI_SRC}:{_line_of(text, struct.start())} ({struct[2]})"
    if constants.get("SLINK_ABI_VERSION") != 2 or "SlinkMailboxV2" not in structs:
        raise ValueError("unsupported companion ABI")
    return {"constants": constants, "structs": structs, "_src": citations,
            # LF-normalized: hashing raw bytes differs between a Windows (CRLF) checkout and CI
            "source_sha256": hashlib.sha256((REPO / ABI_SRC).read_bytes().replace(b"\r\n", b"\n")).hexdigest()}


def native_block(title: str | None = None) -> dict | None:
    if title is not None:
        if title not in V2_TARGETS:
            raise ValueError(f"unknown companion target: {title}")
        abi = native_abi()
        path = f"patch/src/trade_targets/{title}.h"
        text = (REPO / path).read_text(encoding="utf-8")
        ready = re.search(r"^#define SLINK_TARGET_READY\s+(\w+)", text, re.M)
        if not ready:
            raise ValueError(f"missing READY gate: {path}")
        if _abi_number(ready[1], {}) == 0:
            return None
        constants = abi["constants"]
        base, base_off = _c_define(text, "SLINK_TARGET_ARENA_BASE")
        native = {"BASE":base,"SIG":constants["SLINK_SIGNATURE"],"ABI":2,"abi_v2":abi}
        native.update({name.removeprefix("SLINK_"):value for name,value in constants.items()
                       if name.startswith("SLINK_OP_")})
        for name, offset in (("BLOB_BUF","SLINK_BLOB_OFFSET"),("TEXT_BUF","SLINK_TEXT_OFFSET"),
                             ("MENU_BUF","SLINK_MENU_OFFSET"),("INFO","SLINK_INFO_OFFSET")):
            native[name]=base+constants[offset]
        native.update(INFO_LINEW=32,INFO_MAXLINES=constants["SLINK_INFO_MAX_LINES"],
                      INFO_PAGESLOT=constants["SLINK_INFO_PAGE_SLOT"],PI_COUNT=base+0x804,TN_ENABLE=base+0x808)
        native["_src"]={name:f"{ABI_SRC} (canonical ABI2 layout); {path}:{_line_of(text,base_off)} (reserved arena)"
                        for name in native if name!="abi_v2"}
        return native
    # The published RR UPS is still v1. Never replace its values/citations with
    # the unqualified v2 header merely because its source is now available.
    values: dict[str, int] = {}
    src: dict[str, str] = {}
    text = (REPO / HANDLERS_SRC).read_text(encoding="utf-8", errors="replace")

    def put(name: str, cname: str) -> int:
        value, off = _c_define(text, cname)
        values[name] = value
        src[name] = f"{HANDLERS_SRC}:{_line_of(text, off)} ({cname})"
        return value

    for name, cname in MAILBOX_C_SYMBOL.items():
        put(name, cname)
    for name, cname in GHOST_C_SYMBOL.items():
        put(name, cname)
    for op in MAILBOX_OPCODES:
        m = re.search(rf"\b{op}\s*=\s*(\d+)", text)
        if not m:
            sys.exit(f"gen_gen3_profile: {HANDLERS_SRC} no longer defines {op}")
        values[op] = int(m.group(1))
        src[op] = f"{HANDLERS_SRC}:{_line_of(text, m.start())} (opcode enum)"
    # GMAIN_CB2_PTR = gMain + 4 (gMain.callback2 -- archive/gen3-old-client:lua/peer_ghost_npc.lua used to read this directly)
    gmain, gmain_off = _c_define(text, "gMain")
    values["GMAIN_CB2_PTR"] = gmain + 4
    src["GMAIN_CB2_PTR"] = f"{HANDLERS_SRC}:{_line_of(text, gmain_off)} (gMain + 4, callback2)"
    # PI_COUNT = SlinkState.pi_count -- SS + 3 (struct field order: _rsvd0, pi_armed, pi_oe, pi_count)
    ss, ss_off = _c_define(text, "SS")
    values["PI_COUNT"] = ss + 3
    src["PI_COUNT"] = f"{HANDLERS_SRC}:{_line_of(text, ss_off)} (SlinkState.pi_count, SS + 3)"
    # EVR_PRIM = EvRing.prim -- EV + 6 (struct field order: wr,rd,overflow,inb,pfc,ofc,prim)
    values["EVR_PRIM"] = values["EVR"] + 6
    src["EVR_PRIM"] = src["EVR"] + " (EvRing.prim, EV + 6)"
    # INFO_LINEW: SlinkInfo.line[8][N] row width is a struct array dimension, not a #define.
    m = re.search(r"volatile u8 line\[8\]\[(\d+)\]", text)
    if not m:
        sys.exit(f"gen_gen3_profile: {HANDLERS_SRC} no longer carries SlinkInfo.line[8][N]")
    values["INFO_LINEW"] = int(m.group(1))
    src["INFO_LINEW"] = f"{HANDLERS_SRC}:{_line_of(text, m.start())} (SlinkInfo.line[8][N])"
    # LOCALID: the ghost's sentinel localId (0xF0), checked inline rather than named -- its
    # documented neighbor TN_LOCALID (0xF1) calls it out: "exclusive sentinel (ghost uses 0xF0)".
    m = re.search(r"R8\(oo \+ 0x08\) == (0x[0-9A-Fa-f]+)u", text)
    if not m:
        sys.exit(f"gen_gen3_profile: {HANDLERS_SRC} no longer checks the ghost sentinel localId")
    values["LOCALID"] = int(m.group(1), 16)
    src["LOCALID"] = f"{HANDLERS_SRC}:{_line_of(text, m.start())} (ghost sentinel localId check)"

    values["_src"] = src  # type: ignore[assignment]
    return values


# ── assembly ─────────────────────────────────────────────────────────────────────
def _thumb_keys(rom: dict) -> list[str]:
    """ROM keys whose literal already carries the Thumb bit (bit 0 set).  The values are
    kept verbatim -- gTasks[].func / gMain.callback2 are read from RAM with the +1, so
    re-stripping here would make the profile disagree with what the client compares."""
    out = []
    for key, val in rom.items():
        vals = val if isinstance(val, list) else [val]
        nums = [v for v in vals if isinstance(v, int)]
        if nums and all(v & 1 for v in nums):
            out.append(key)
    return sorted(out)


def _title(profile: dict, title: str, key: str, admitted: bool) -> dict:
    sections: dict[str, dict] = {"ram": {}, "rom": {}, "derived": {}}
    for name, val in profile.items():
        section = SECTION.get(name)
        if section is None:
            sys.exit(f"gen_gen3_profile: {key}.{name} is not classified in SECTION")
        sections[section][name] = None if val is NIL else val
    out = {
        "variant": key,
        "admitted": admitted,
        "rom_thumb": _thumb_keys(sections["rom"]),
        **sections,
    }
    out.update(ROM_HASHES.get(title, {}))
    return out


# ── C4-LGSE: LeafGreen ROM addresses the shared `vanilla` table gets wrong ─────────
# Same bug class as the BASESTATS_ADDR override above: the Lua `vanilla` profile is
# one table shared by both titles, so it can only carry one literal per key -- and it
# carries FireRed's for SE_SONG_HEADERS and the evolution-scene CB2s.  Translate by
# symbol NAME (FR address -> FR symbol -> LG address for that symbol), never by a
# fixed byte offset, and hard-fail on any ambiguity rather than guess.
_SYM_LINE = re.compile(r"^([0-9a-fA-F]{8})\s+[a-zA-Z]\s+[0-9a-fA-F]+\s+(\S+)$", re.M)


def _sym_index(text: str) -> tuple[dict[int, list[str]], dict[str, list[int]]]:
    """{address: [symbol names]} and {name: [addresses]} for a pret .sym file's text.
    `.gcc2_compiled.` pseudo-symbols are excluded from the name index -- never a real
    translation target -- but kept in the address index so callers can still see (and
    reject) an address that names more than one thing."""
    by_addr: dict[int, list[str]] = {}
    by_name: dict[str, list[int]] = {}
    for m in _SYM_LINE.finditer(text):
        addr, name = int(m[1], 16), m[2]
        by_addr.setdefault(addr, []).append(name)
        if not name.startswith("."):
            by_name.setdefault(name, []).append(addr)
    return by_addr, by_name


def _real_names_at(by_addr: dict, addr: int) -> list[str]:
    return [n for n in by_addr.get(addr, []) if not n.startswith(".")]


def _resolve_symbol(by_addr: dict, val: int, is_thumb: bool) -> tuple[list[str], int]:
    """Resolve `val` to (real symbol names there, the address they're at). Looks up the EXACT
    address first -- a data symbol can legitimately be odd (gPPUpGetMask sits at 0x0825DEA1 in
    FireRed; that is not a Thumb pointer) -- and only strips bit 0 when the field is a known
    Thumb pointer (`is_thumb`, from `rom_thumb` membership) AND the exact odd address names
    nothing on its own."""
    names = _real_names_at(by_addr, val)
    if names or not is_thumb or not (val & 1):
        return names, val
    stripped = val & ~1
    return _real_names_at(by_addr, stripped), stripped


def _unique_global_name(by_addr: dict, by_name: dict, val: int, is_thumb: bool,
                        sym_file: str, label: str) -> tuple[str, int]:
    """The one real symbol naming `val`, hard-failing unless it is unique both AT that address
    (no aliases sharing the address) and GLOBALLY (no duplicate statics elsewhere in the file)
    -- a name that recurs elsewhere can't be trusted to mean the same thing at every occurrence,
    so picking a counterpart by name alone could silently pick the wrong one."""
    names, addr = _resolve_symbol(by_addr, val, is_thumb)
    if len(names) != 1:
        sys.exit(f"gen_gen3_profile: {label}: {sym_file} names {names or 'no'} real symbols "
                 f"at 0x{addr:08X} (need exactly one)")
    name = names[0]
    if len(by_name.get(name, [])) != 1:
        sys.exit(f"gen_gen3_profile: {label}: {sym_file} has {len(by_name[name])} addresses "
                 f"named {name} (need a globally-unique symbol, not just one unique at "
                 f"0x{addr:08X})")
    return name, addr


def _translate_fr_default(fr_by_addr: dict, fr_by_name: dict, lg_by_name: dict, fr_val: int,
                          is_thumb: bool, label: str) -> tuple[int, str]:
    """FR ROM address -> LG's address for the same, globally-unique symbol, keeping the Thumb
    bit `is_thumb` fields carry."""
    name, _ = _unique_global_name(fr_by_addr, fr_by_name, fr_val, is_thumb,
                                  "pokefirered.sym", label)
    addrs = lg_by_name.get(name, [])
    if len(addrs) != 1:
        sys.exit(f"gen_gen3_profile: {label}: pokeleafgreen.sym has {len(addrs)} addresses "
                 f"for {name} (need exactly one)")
    return addrs[0] | (fr_val & 1 if is_thumb else 0), name


# Paths the guard below must never flag: known cases where a leafgreen value legitimately
# equals FireRed's without being a copy-paste bug -- each verified directly (never by name
# lookup alone, which is exactly what the stricter guard now refuses to trust).
_GUARD_EXCLUDE_PATHS = {
    # Keyed by FireRed's own game code -- FR's value IS the right value here.
    "derived.BASESTATS_ADDR_BY_GAME_CODE.BPRE",
    # Task_LaunchLvlUpAnim is a `static` helper repeated 3x across translation units in BOTH
    # .sym files -- not globally unique by name, so the stricter guard correctly refuses to
    # resolve it by name alone. Direct address comparison (not name lookup) confirms the
    # specific instance this task writer targets did not move between builds:
    #   pokefirered.sym:2013/9168/12833  -> 0x08030238 / 0x080e8190 / 0x08156c68
    #   pokeleafgreen.sym:2013/9170/12835 -> 0x08030238 / 0x080e8168 / 0x08156c44
    # Both name 0x08030238, and it is the one POST_BATTLE_WRITER_TASKS[0] carries.
    "rom.POST_BATTLE_WRITER_TASKS[0]",
}


def _walk_rom_addrs(lg_val, fr_val, path: str):
    """Yield (path, lg value, fr value) for every int in `lg_val` that sits in the ROM
    address range, recursing into dicts/lists (e.g. SE_SONG_HEADERS, POST_BATTLE_WRITER_TASKS).
    `fr_val` is the value at the same path in FireRed's entry, or None where it has none."""
    if isinstance(lg_val, dict):
        for k, v in lg_val.items():
            yield from _walk_rom_addrs(v, fr_val.get(k) if isinstance(fr_val, dict) else None,
                                       f"{path}.{k}")
    elif isinstance(lg_val, list):
        for i, v in enumerate(lg_val):
            fv = fr_val[i] if isinstance(fr_val, list) and i < len(fr_val) else None
            yield from _walk_rom_addrs(v, fv, f"{path}[{i}]")
    elif isinstance(lg_val, int) and 0x08000000 <= lg_val <= 0x09FFFFFF:
        yield path, lg_val, fr_val


def _guard_leafgreen_not_copied(lg_entry: dict, fr_entry: dict, fr_by_addr: dict,
                                fr_by_name: dict, lg_by_name: dict) -> None:
    """C4-LGSE: fail the build -- don't ship -- the next time a leafgreen ROM address is left at
    FireRed's default. Fail-CLOSED: any ambiguity (an FR address naming more than one symbol, an
    FR or LG symbol name that recurs elsewhere) is treated as unresolved evidence of a bug, not
    silently accepted. An earlier version of this guard resolved a name at an address without
    checking global uniqueness on either side, and unconditionally stripped bit 0 before looking
    an address up; that let an aliased FR address, a duplicate LG static, and a genuinely-odd data
    address (gPPUpGetMask) all slip a copied value past the guard. A value only trips this if it
    (a) still equals FireRed's value at the same path, (b) FireRed's address names exactly one
    real, globally-unique symbol, and (c) that same symbol is NOT at the same address in
    pokeleafgreen.sym -- positive, unambiguous evidence LG disagrees, not just two ROMs
    coincidentally sharing an address (e.g. BeginBattleIntro) or a name that recurs and so
    proves nothing (e.g. a `static` helper repeated across translation units)."""
    thumb_keys = set(lg_entry.get("rom_thumb", ()))
    for section in ("ram", "rom", "derived"):
        for key, lg_val in lg_entry[section].items():
            fr_val = fr_entry[section].get(key)
            is_thumb = section == "rom" and key in thumb_keys
            for path, val, fr_leaf in _walk_rom_addrs(lg_val, fr_val, f"{section}.{key}"):
                if path in _GUARD_EXCLUDE_PATHS or val != fr_leaf:
                    continue
                names, addr = _resolve_symbol(fr_by_addr, val, is_thumb)
                if not names:
                    continue  # nothing to resolve -- no evidence either way
                if len(names) > 1:
                    sys.exit(f"gen_gen3_profile: leafgreen.{path} = 0x{val:08X}: pokefirered.sym "
                             f"names {names} at 0x{addr:08X} -- ambiguous (aliases); translate "
                             f"explicitly, or add a _GUARD_EXCLUDE_PATHS entry with the reason, "
                             f"instead of leaving this copied")
                name = names[0]
                if len(fr_by_name.get(name, [])) != 1:
                    sys.exit(f"gen_gen3_profile: leafgreen.{path} = 0x{val:08X}: pokefirered.sym "
                             f"has {len(fr_by_name[name])} addresses named {name} -- not "
                             f"globally unique; translate explicitly instead of leaving this "
                             f"copied")
                lg_addrs = lg_by_name.get(name, [])
                if len(lg_addrs) != 1:
                    sys.exit(f"gen_gen3_profile: leafgreen.{path} = 0x{val:08X}: "
                             f"pokeleafgreen.sym has {len(lg_addrs)} addresses named {name} -- a "
                             f"duplicate static means an address match at 0x{addr:08X} proves "
                             f"nothing; translate explicitly instead of leaving this copied")
                if lg_addrs[0] != addr:
                    sys.exit(f"gen_gen3_profile: leafgreen.{path} = 0x{val:08X} still equals "
                             f"FireRed's value, and FireRed's {name} sits at a different address "
                             f"(0x{lg_addrs[0]:08X}) in pokeleafgreen.sym -- translate it "
                             f"(see C4-LGSE)")


def rom_tables(title: str, text: str) -> tuple[dict, dict]:
    """R0: cartridge table heads, independent of native/companion support.

    Strides are the pinned pret layouts (Gen 3 randomized design R0); counts
    include gWildMonHeaders' terminating sentinel and come from symbol sizes.
    """
    if title not in ("firered", "leafgreen", "emerald"):
        raise ValueError("rom_tables is bound only for FireRed/LeafGreen/Emerald")
    rows, provenance = {}, {}
    strides = {"gTrainers": 40, "gWildMonHeaders": 20, "gEvolutionTable": 40,
               "gSpeciesInfo": 28, "gTrainerClassNames": 13}
    for name, stride in strides.items():
        matches = list(re.finditer(rf"^([0-9a-fA-F]{{8}})\s+[lg]\s+([0-9a-fA-F]{{8}})\s+{name}$", text, re.M))
        if len(matches) != 1:
            raise ValueError(f"rom_tables: need exactly one {name} in poke{title}.sym")
        match = matches[0]
        address, size = int(match[1], 16), int(match[2], 16)
        if not 0x08000000 <= address < 0x0A000000 or size <= 0 or size % stride:
            raise ValueError(f"rom_tables: {name} has invalid address/size/stride")
        if title == "emerald":
            count = {"gTrainers": 855, "gWildMonHeaders": 125, "gEvolutionTable": 412,
                     "gSpeciesInfo": 412, "gTrainerClassNames": 66}[name]
            if size != count * stride:
                raise ValueError(f"rom_tables: Emerald {name} size is not {count} * {stride}")
        rows[name] = {"address": address, "size": size, "stride": stride, "count": size // stride}
        pin = EMERALD_PIN if title == "emerald" else "pret/pokefirered@c75f3523"
        provenance[name] = f"{pin} {name}; data/gen3/pret/poke{title}.sym:{_line_of(text, match.start())}"
    return rows, provenance


def build(pack: str, profiles: dict, source: dict) -> dict:
    out = {
        "schema": SCHEMA,
        "generator": "tools/gen_gen3_profile.py",
        "pack": pack,
        "source": source,
        "titles": {t: _title(profiles[k], t, k, adm) for t, k, adm in PACKS[pack]},
    }
    if pack == "gen3_frlg":
        # C4-LGSE: FireRed's own index, used below to translate leafgreen's copied ROM
        # addresses and to guard against the next one nobody translates.
        fr_by_addr, fr_by_name = _sym_index(
            (REPO / "data/gen3/pret/pokefirered.sym").read_text(encoding="utf-8"))
        lg_by_name: dict[str, list[int]] = {}
        for title in ("firered", "leafgreen"):
            entry = out["titles"][title]
            path = f"data/gen3/pret/poke{title}.sym"
            text = (REPO / path).read_text(encoding="utf-8")
            matches = list(re.finditer(
                r"^([0-9a-fA-F]{8})\s+g\s+[0-9a-fA-F]+\s+gPokemonStorage$", text, re.M))
            if len(matches) != 1:
                sys.exit(f"gen_gen3_profile: {path} must name exactly one gPokemonStorage")
            match = matches[0]
            entry["ram"]["POKEMON_STORAGE_BASE"] = int(match[1], 16)
            entry["_src"] = {
                "ram.POKEMON_STORAGE_BASE":
                    f"{path}:{_line_of(text, match.start())} (gPokemonStorage; {PRET_PIN})",
            }
            for name, (value, where) in FRLG_DERIVED.items():
                entry["derived"][name] = value
                entry["_src"][f"derived.{name}"] = where
            for (section, name), symbol in FRLG_SYM_ADDR.items():
                sym_matches = list(re.finditer(
                    rf"^([0-9a-fA-F]{{8}})\s+g\s+[0-9a-fA-F]+\s+{re.escape(symbol)}$", text, re.M))
                if len(sym_matches) != 1:
                    sys.exit(f"gen_gen3_profile: {path} must name exactly one {symbol}")
                sym_match = sym_matches[0]
                entry[section][name] = int(sym_match[1], 16)
                entry["_src"][f"{section}.{name}"] = (
                    f"{path}:{_line_of(text, sym_match.start())} ({symbol}; {PRET_PIN})")
            for (section, name), symbol in FRLG_SYM_THUMB_ADDR.items():
                sym_matches = list(re.finditer(
                    rf"^([0-9a-fA-F]{{8}})\s+[glt]\s+[0-9a-fA-F]+\s+{re.escape(symbol)}$", text, re.M))
                if len(sym_matches) != 1:
                    sys.exit(f"gen_gen3_profile: {path} must name exactly one {symbol}")
                sym_match = sym_matches[0]
                entry[section][name] = int(sym_match[1], 16) | 1
                entry["_src"][f"{section}.{name}"] = (
                    f"{path}:{_line_of(text, sym_match.start())} "
                    f"({symbol}|1, the Thumb form gBattleMainFunc stores; {PRET_PIN})")
                if name in INTRO_THUMB_KEYS:
                    entry["rom_thumb"].append(name)
                    entry["rom_thumb"].sort()
            # C4-3c separately requested correction: the shared legacy vanilla
            # table carries FR's default; LG must publish its own table address.
            if title == "leafgreen":
                match = re.search(r"^([0-9a-fA-F]{8})\s+g\s+[0-9a-fA-F]+\s+gSpeciesInfo$", text, re.M)
                if not match:
                    raise ValueError("LeafGreen gSpeciesInfo symbol missing")
                entry["rom"]["BASESTATS_ADDR"] = int(match[1], 16)
                entry["_src"]["rom.BASESTATS_ADDR"] = (
                    f"{path}:{_line_of(text, match.start())} (gSpeciesInfo; {PRET_PIN})")
                # C4-LGSE: the same "shared vanilla table carries FR's default" bug, for
                # SE_SONG_HEADERS and the evolution CB2s. Translate each by symbol name.
                _, lg_by_name = _sym_index(text)
                new_headers = {}
                for sid, fr_val in entry["rom"]["SE_SONG_HEADERS"].items():
                    # even addresses, never Thumb-tagged (SongHeader table entries, not funcs)
                    new_val, name = _translate_fr_default(
                        fr_by_addr, fr_by_name, lg_by_name, fr_val, False,
                        f"rom.SE_SONG_HEADERS[{sid}]")
                    new_headers[sid] = new_val
                    entry["_src"][f"rom.SE_SONG_HEADERS.{sid}"] = (
                        f"{path} {name} translated by symbol name from pokefirered.sym "
                        f"0x{fr_val:08X} ({PRET_PIN})")
                entry["rom"]["SE_SONG_HEADERS"] = new_headers  # a fresh dict: FR's entry
                # still points at the original, un-translated dict object -- never mutate it.
                for cb2_key in ("CB2_EVOLUTION_LOAD_ADDR", "CB2_EVOLUTION_BEGIN_ADDR",
                                "CB2_EVOLUTION_UPDATE_ADDR", "CB2_TRADE_EVOLUTION_UPDATE_ADDR"):
                    fr_val = entry["rom"][cb2_key]
                    new_val, name = _translate_fr_default(
                        fr_by_addr, fr_by_name, lg_by_name, fr_val, True, f"rom.{cb2_key}")
                    entry["rom"][cb2_key] = new_val
                    entry["_src"][f"rom.{cb2_key}"] = (
                        f"{path} {name} translated by symbol name from pokefirered.sym "
                        f"0x{fr_val:08X} ({PRET_PIN})")
            entry["rom_tables"], entry["rom_tables_provenance"] = rom_tables(title, text)
        # C4-LGSE guard: the next FR-default value nobody translated must fail the build.
        _guard_leafgreen_not_copied(out["titles"]["leafgreen"], out["titles"]["firered"],
                                    fr_by_addr, fr_by_name, lg_by_name)
    if pack == "gen3_rr":
        # The existing RR detector explicitly rejects party counts above this limit.
        text = (REPO / SRC).read_text(encoding="utf-8")
        detector = re.search(r"^local function _detectRR\(\)(.*?)^end", text, re.M | re.S)
        match = re.search(r"^    if partyCount > (\d+) then return false end$",
                          detector[1] if detector else "", re.M)
        if not match:
            sys.exit(f"gen_gen3_profile: {SRC} no longer carries the RR party capacity check")
        entry = out["titles"]["radical_red"]
        entry["derived"]["PARTY_CAPACITY"] = int(match[1])
        entry["_src"] = {
            "derived.PARTY_CAPACITY":
                f"{SRC}:{_line_of(text, detector.start(1) + match.start())} (_detectRR partyCount limit)",
        }
        # The old client's radical_red table still ships 0x03003840 / 0x03003838 for
        # SB1_PTR_ADDR / SB2_PTR_ADDR.  Those are not the pointers: both are literal-pool
        # constants inside IntrMain_Buffer (the DMA'd intr_main blob), correct on today's RR only
        # because its save-block offset is fixed at 0.  The values stay, because every Lua
        # literal has to survive at its key, and the NEW client reads the ROM-derived
        # write_checkpoint.pointers.gSaveBlock1Ptr instead (card C3-33) -- so the legacy address
        # is recorded here as the documented limit it is rather than silently inherited.
        legacy = entry["ram"].get("SB1_PTR_ADDR")
        hits = [m.start() for m in
                re.finditer(rf"^\s*SB1_PTR_ADDR\s*=\s*0x{legacy:08X}\s*,", text, re.M)
                ] if isinstance(legacy, int) else []
        if len(hits) != 1:
            sys.exit(f"gen_gen3_profile: {SRC} must spell the RR SB1_PTR_ADDR "
                     f"0x{legacy:X} exactly once (found {len(hits)})")
        entry["_src"]["ram.SB1_PTR_ADDR"] = (
            f"{SRC}:{_line_of(text, hits[0])} (the old client's address, kept for parity: it is "
            "a literal-pool constant inside IntrMain_Buffer, not the pointer -- the new client "
            "reads write_checkpoint.pointers.gSaveBlock1Ptr, which is ROM-derived)")
        for name, (value, where) in RR_DERIVED.items():
            entry["derived"][name] = value
            entry["_src"][f"derived.{name}"] = where
        for (section, name), (value, where) in rr_rom_facts().items():
            if name in entry[section] and entry[section][name] != value:
                raise ValueError(f"RR binary fact would replace existing {section}.{name}")
            entry[section][name] = value
            entry["_src"][f"{section}.{name}"] = where
            if section == "rom" and name in INTRO_THUMB_KEYS:
                entry["rom_thumb"].append(name)
        entry["rom_thumb"].append("HANDLE_TURN_ACTION_SELECTION_ADDR")
        entry["rom_thumb"].sort()
        entry["_rom_anchors"] = {
            name: {"rom_offset": off, "expected_hex": raw.upper()}
            for name, (off, raw) in RR_ROM_ANCHORS.items()
        }
        entry["abilities"] = RR_ABILITY_READER
        entry["_src"]["abilities"] = "RR 4.1 ROM routines pinned by RR_ABILITY_WITNESSES"
        out["native"] = native_block()
    elif pack == "gen3_frlg":
        out["native"] = native_block("firered")
    return out


# ── E1-PACK: the vanilla Emerald pack (data/games/gen3_emerald/profile.json) ──────────────
# Not Lua-sourced: every address is read out of pret's published pokeemerald.sym by symbol NAME,
# and every derived constant is re-derived from the pinned pret source (file:line + the identifier
# that line must carry; tests/unit/test_gen3_emerald_pack.py re-reads each cited line).  The old
# GEN3.profiles.emerald stub is never copied -- the test only uses it as an independent cross-check.
EMERALD_SYM = "data/gen3/pret/pokeemerald.sym"
EMERALD_PIN = "pret/pokeemerald@c65e93f20a5275ab03b07d6f6411096a82a60ffd"
EMERALD_ROM_SHA1 = "f3ae088181bf583e55daf962a92bb46f4f1d07b7"

# (section, key) -> (unique .sym symbol, stored Thumb (|1))
EMERALD_SYM_ADDR = {
    ("ram", "BATTLERS_COUNT_ADDR"): ("gBattlersCount", False),
    ("ram", "BATTLER_PARTY_INDEXES_ADDR"): ("gBattlerPartyIndexes", False),
    ("ram", "BATTLE_COMM_ADDR"): ("gBattleCommunication", False),
    ("ram", "BATTLE_MAIN_FUNC_ADDR"): ("gBattleMainFunc", False),
    ("ram", "BATTLE_MONS_ADDR"): ("gBattleMons", False),
    ("ram", "BATTLE_OUTCOME_ADDR"): ("gBattleOutcome", False),
    ("ram", "BATTLE_RESULTS_ADDR"): ("gBattleResults", False),
    ("ram", "BATTLE_TYPE_ADDR"): ("gBattleTypeFlags", False),
    ("ram", "CHOSEN_ACTION_ADDR"): ("gChosenActionByBattler", False),
    ("ram", "CHOSEN_MOVE_ADDR"): ("gChosenMoveByBattler", False),
    ("ram", "BATTLE_STRUCT_PTR_ADDR"): ("gBattleStruct", False),
    ("ram", "DISABLE_STRUCTS_ADDR"): ("gDisableStructs", False),
    ("ram", "ENEMY_BASE"): ("gEnemyParty", False),
    ("ram", "ENEMY_COUNT_ADDR"): ("gEnemyPartyCount", False),
    ("ram", "GMAIN_ADDR"): ("gMain", False),
    ("ram", "LOCKED_MOVES_ADDR"): ("gLockedMoves", False),
    ("ram", "PARTY_BASE"): ("gPlayerParty", False),
    ("ram", "PARTY_COUNT_ADDR"): ("gPlayerPartyCount", False),
    # the ASLR window base (struct PokemonStorageASLR, include/load_save.h:23-30); the live
    # address is gPokemonStoragePtr, exactly as for the FR/LG gPokemonStorage
    ("ram", "POKEMON_STORAGE_BASE"): ("gPokemonStorage", False),
    ("ram", "PSP_PTR_ADDR"): ("gPokemonStoragePtr", False),
    ("ram", "SB1_PTR_ADDR"): ("gSaveBlock1Ptr", False),
    ("ram", "SB2_PTR_ADDR"): ("gSaveBlock2Ptr", False),
    ("ram", "SPECIAL_VAR_BOX_ID_ADDR"): ("gSpecialVar_MonBoxId", False),
    ("ram", "SPECIAL_VAR_BOX_POS_ADDR"): ("gSpecialVar_MonBoxPos", False),
    ("ram", "STATUS3_ADDR"): ("gStatuses3", False),
    ("ram", "TASKS_BASE_ADDR"): ("gTasks", False),
    ("ram", "TRAINER_OPPONENT_ADDR"): ("gTrainerBattleOpponent_A", False),
    ("rom", "BASESTATS_ADDR"): ("gSpeciesInfo", False),
    ("rom", "BATTLE_MOVES_ADDR"): ("gBattleMoves", False),
    ("rom", "EXPERIENCE_TABLES_ADDR"): ("gExperienceTables", False),
    ("rom", "PP_UP_GET_MASK_ADDR"): ("gPPUpGetMask", False),
    ("rom", "RETURN_FROM_BATTLE_ADDR"): ("ReturnFromBattleToOverworld", True),
    ("rom", "BEGIN_BATTLE_INTRO_ADDR"): ("BeginBattleIntro", True),
    ("rom", "BEGIN_BATTLE_INTRO_DUMMY_ADDR"): ("BeginBattleIntroDummy", True),
    ("rom", "BATTLE_INTRO_GET_MONS_DATA_ADDR"): ("BattleIntroGetMonsData", True),
    ("rom", "CB2_EVOLUTION_BEGIN_ADDR"): ("CB2_BeginEvolutionScene", True),
    ("rom", "CB2_EVOLUTION_LOAD_ADDR"): ("CB2_EvolutionSceneLoadGraphics", True),
    ("rom", "CB2_EVOLUTION_UPDATE_ADDR"): ("CB2_EvolutionSceneUpdate", True),
    ("rom", "CB2_TRADE_EVOLUTION_UPDATE_ADDR"): ("CB2_TradeEvolutionSceneUpdate", True),
}
# pokeemerald has two static Task_LaunchLvlUpAnim (battle_controller_player.c:1271 and
# battle_controller_player_partner.c:429); the FR POST_BATTLE_WRITER_TASKS entry is the player one,
# so take the occurrence inside the battle_controller_player.o link span [SetControllerToPlayer,
# SetControllerToOpponent) -- the same bracket names 0x08030238 in pokefirered.sym.
EMERALD_LVLUP_TASK = ("Task_LaunchLvlUpAnim", "SetControllerToPlayer", "SetControllerToOpponent")
# SE song id (the pokeemerald numbering, include/constants/songs.h line) -> song header symbol.
# SUCCESS/FAILURE/SHINY are 31/32/102 here, not the FR 25/26/95.
EMERALD_SE_SONGS = {16: ("se_faint", 22, "SE_FAINT"), 17: ("se_flee", 23, "SE_FLEE"),
                    22: ("se_boo", 28, "SE_BOO"), 31: ("se_success", 37, "SE_SUCCESS"),
                    32: ("se_failure", 38, "SE_FAILURE"), 102: ("se_shiny", 108, "SE_SHINY")}
# key -> (value, pret path:lines, identifier the cited lines carry, note)
EMERALD_DERIVED = {
    "BATTLE_STRUCT_MOVE_TARGET_OFF": (0x0C, "include/battle.h:354-361", "moveTarget",
                                     "BattleStruct u8 prefix + wrappedMove[8]"),
    "BATTLE_STRUCT_CHOSEN_MOVE_POS_OFF": (0x80, "include/battle.h:354-392", "chosenMovePositions",
                                         "offsetof verified from BattleStruct prefix"),
    "BADGE_FIRST_FLAG": (0x867, "include/constants/flags.h:1359", "FLAG_BADGE01_GET",
                         "SYSTEM_FLAGS 0x860 + 7 (flags.h:1348); the E2-ENTRY+BADGE derived "
                         "flag id lua/gen3/reads.lua and tools/gen3_reads_pydec.py derive "
                         "per-bit from, since the badges straddle a byte (see "
                         "SB1_BADGE_BYTE_OFFSET below)"),
    "BASESTATS_GROWTH_RATE_OFFSET": (0x13, "include/pokemon.h:319", "growthRate", "struct SpeciesInfo"),
    "BATTLE_MON_OT_ID_OFF": (0x54, "include/pokemon.h:294", "otId", "struct BattlePokemon"),
    "BATTLE_MON_PERSONALITY_OFF": (0x48, "include/pokemon.h:291", "personality", "struct BattlePokemon"),
    "BATTLE_MON_STAT_STAGES_OFF": (0x19, "include/pokemon.h:277", "statStages",
                                   "statStages at 0x18 + STAT_ATK 1, include/constants/pokemon.h:75-76"),
    "BATTLE_MOVE_PP_OFFSET": (4, "include/pokemon.h:327-333", "pp",
                              "struct BattleMove: effect, power, type, accuracy, then pp"),
    "BATTLE_RESULTS_FOE_FAINTS_OFF": (1, "include/battle.h:237", "opponentFaintCounter", ""),
    "BATTLE_RESULTS_PLAYER_FAINTS_OFF": (0, "include/battle.h:236", "playerFaintCounter", ""),
    "BATTLE_TYPE_DOUBLE_MASK": (0x01, "include/constants/battle.h:59", "BATTLE_TYPE_DOUBLE", ""),
    "BATTLE_TYPE_LINK_MASK": (0x02, "include/constants/battle.h:60", "BATTLE_TYPE_LINK", ""),
    "BATTLE_TYPE_TRAINER_MASK": (0x08, "include/constants/battle.h:62", "BATTLE_TYPE_TRAINER", ""),
    "BOXES_PER_STORE": (14, "include/pokemon_storage_system.h:4", "TOTAL_BOXES_COUNT", ""),
    "BOX_DATA_OFFSET": (4, "include/pokemon_storage_system.h:21-23", "boxNames",
                        "boxes follows u8 currentBox but BoxPokemon opens with u32 personality "
                        "(include/pokemon.h:196-198): 3 pad bytes; boxNames at 0x8344 = 4 + 14*30*80 "
                        "confirms it (the 0x0001 comment is wrong)"),
    "B_ACTION_NOTHING_FAINTED": (13, "include/battle.h:41", "B_ACTION_NOTHING_FAINTED", ""),
    "DISABLE_STRUCT_PERISH_TIMER_OFF": (0x0F, "include/battle.h:70-85", "perishSongTimer",
                                        "u32, u16, u16, then 7 u8 fields put the perishSongTimer:4 "
                                        "byte at 0x0F (low nibble)"),
    "EXPERIENCE_TABLE_ENTRY_COUNT": (101, "src/data/pokemon/experience_tables.h:18", "MAX_LEVEL + 1",
                                     "gExperienceTables[][MAX_LEVEL + 1]"),
    "GMAIN_CB2_OFFSET": (0x04, "include/main.h:11", "callback2", "struct Main"),
    "GMAIN_INBATTLE_MASK": (0x02, "include/main.h:38-39", "inBattle",
                            "second bitfield bit of the 0x439 byte, after oamLoadDisabled:1"),
    "GMAIN_INBATTLE_OFFSET": (0x439, "include/main.h:39", "inBattle", "struct Main"),
    "MAX_LEVEL": (100, "include/constants/pokemon.h:146", "MAX_LEVEL", ""),
    "MONS_PER_BOX": (5 * 6, "include/pokemon_storage_system.h:5-7", "IN_BOX_COUNT",
                     "IN_BOX_ROWS * IN_BOX_COLUMNS"),
    "OUTCOME_WON": (1, "include/constants/battle.h:100", "B_OUTCOME_WON", ""),
    "OUTCOME_LOST": (2, "include/constants/battle.h:101", "B_OUTCOME_LOST", ""),
    "OUTCOME_DREW": (3, "include/constants/battle.h:102", "B_OUTCOME_DREW", ""),
    "OUTCOME_RAN": (4, "include/constants/battle.h:103", "B_OUTCOME_RAN", ""),
    "OUTCOME_CAUGHT": (7, "include/constants/battle.h:106", "B_OUTCOME_CAUGHT", ""),
    "OVERWORLD_MODE": ("gmain_flags", "include/main.h:39", "inBattle",
                       "client mode name: the in-battle test is this gMain flag bit, as on FR/LG"),
    "PARTY_CAPACITY": (6, "include/constants/global.h:33", "PARTY_SIZE", ""),
    # Not a number: the FR badges are flags 0x820..0x827 = SaveBlock1.flags byte 0x104 bits
    # 0-7, but the Emerald ones are 0x867..0x86E = byte 0x10C bit 7 + byte 0x10D bits 0-6.  One u8
    # whose bit i is badge i+1 (lua/gen3/reads.lua:462-468) cannot describe that, so the key stays
    # null; BADGE_FIRST_FLAG above (E2-ENTRY+BADGE) carries the per-bit path instead.
    "SB1_BADGE_BYTE_OFFSET": (None, "include/constants/flags.h:1348-1366", "FLAG_BADGE01_GET",
                              "resolved via BADGE_FIRST_FLAG: SYSTEM_FLAGS 0x860, "
                              "FLAG_BADGE01_GET = +0x7 = 0x867 .. FLAG_BADGE08_GET = 0x86E -- "
                              "flags byte 0x10C bit 7 .. byte 0x10D bit 6, which a single badge "
                              "byte cannot express"),
    "SB1_BALL_POCKET_COUNT": (16, "include/constants/global.h:53", "BAG_POKEBALLS_COUNT", ""),
    "SB1_BALL_POCKET_OFFSET": (0x650, "include/global.h:1008", "bagPocket_PokeBalls", "struct SaveBlock1"),
    "SB1_FLAGS_OFFSET": (0x1270, "include/global.h:1020", "flags", "struct SaveBlock1"),
    "SB1_LOCATION_MAP_GROUP_OFFSET": (0x04, "include/global.h:987", "location",
                                      "struct WarpData.mapGroup at +0, include/global.h:581-584"),
    "SB1_LOCATION_MAP_NUM_OFFSET": (0x05, "include/global.h:987", "location",
                                    "struct WarpData.mapNum at +1, include/global.h:581-584"),
    "SB1_VARS_OFFSET": (0x139C, "include/global.h:1021", "vars", "struct SaveBlock1"),
    "SB2_ENC_KEY_OFFSET": (0xAC, "include/global.h:532", "encryptionKey", "struct SaveBlock2"),
    "SB2_NAME_OFFSET": (0, "include/global.h:510", "playerName", "struct SaveBlock2"),
    "SB2_PLAYER_GENDER_OFFSET": (0x08, "include/global.h:511", "playerGender", "struct SaveBlock2; MALE=0/FEMALE=1, include/constants/global.h:113-114"),
    "SB2_OT_ID_OFFSET": (0x0A, "include/global.h:513", "playerTrainerId", "struct SaveBlock2"),
    "SHEDINJA_SPECIES_ID": (303, "include/constants/species.h:309", "SPECIES_SHEDINJA", ""),
    "STATUS3_PERISH_SONG": (0x20, "include/constants/battle.h:162", "STATUS3_PERISH_SONG", ""),
}
# key -> (.sym array symbol, element count, pret path:line of the count, identifier): value =
# symbol size / count, and the division must be exact.
EMERALD_SYM_SIZED = {
    "BASESTATS_ENTRY_SIZE": ("gSpeciesInfo", 412, "include/constants/species.h:418-420", "NUM_SPECIES"),
    "BATTLE_MOVE_ENTRY_SIZE": ("gBattleMoves", 355, "include/constants/moves.h:360", "MOVES_COUNT"),
    "DISABLE_STRUCT_SIZE": ("gDisableStructs", 4, "include/constants/battle.h:41", "MAX_BATTLERS_COUNT"),
    "TASK_STRUCT_SIZE": ("gTasks", 16, "include/task.h:8", "NUM_TASKS"),
}


def build_emerald() -> dict:
    """The gen3_emerald pack: every address by name from pokeemerald.sym, every constant cited."""
    text = (REPO / EMERALD_SYM).read_text(encoding="utf-8")
    rows: dict[str, list[tuple[int, int, int]]] = {}
    for line_no, line in enumerate(text.splitlines(), 1):
        m = re.fullmatch(r"([0-9a-f]{8}) [lg] ([0-9a-f]{8}) (\S+)", line)
        if m:
            rows.setdefault(m[3], []).append((int(m[1], 16), int(m[2], 16), line_no))

    def one(symbol: str, lo: int = 0, hi: int = 1 << 32) -> tuple[int, int, int]:
        hits = [r for r in rows.get(symbol, []) if lo <= r[0] < hi]
        if len(hits) != 1:
            # raise, not sys.exit: build_emerald() must be catchable by main() so a failure here
            # cannot leave the FR/RR profile.json files it already rendered half-written.
            raise ValueError(f"gen_gen3_profile: {EMERALD_SYM} names {len(hits)} {symbol} (need exactly one)")
        return hits[0]

    sections: dict[str, dict] = {"ram": {}, "rom": {}, "derived": {}}
    src: dict[str, str] = {}
    for (section, key), (symbol, thumb) in EMERALD_SYM_ADDR.items():
        addr, _, line = one(symbol)
        sections[section][key] = addr | 1 if thumb else addr
        src[f"{section}.{key}"] = (f"{EMERALD_SYM}:{line} ({symbol}{'|1, Thumb' if thumb else ''}; "
                                   f"{EMERALD_PIN})")
    task, lo_sym, hi_sym = EMERALD_LVLUP_TASK
    addr, _, line = one(task, one(lo_sym)[0], one(hi_sym)[0])
    sections["rom"]["POST_BATTLE_WRITER_TASKS"] = [addr | 1]
    src["rom.POST_BATTLE_WRITER_TASKS"] = (
        f"{EMERALD_SYM}:{line} ({task}|1, the one inside [{lo_sym}, {hi_sym}) = "
        f"battle_controller_player.o); {EMERALD_PIN}:src/battle_controller_player.c:1271 ({task})")
    headers = {}
    for sid, (symbol, songs_line, const) in EMERALD_SE_SONGS.items():
        addr, _, line = one(symbol)
        headers[str(sid)] = addr
        src[f"rom.SE_SONG_HEADERS.{sid}"] = (f"{EMERALD_SYM}:{line} ({symbol}); "
                                             f"{EMERALD_PIN}:include/constants/songs.h:{songs_line} "
                                             f"({const}; SE id {sid})")
    sections["rom"]["SE_SONG_HEADERS"] = headers
    derived = sections["derived"]
    for key, (value, where, ident, note) in EMERALD_DERIVED.items():
        derived[key] = value
        src[f"derived.{key}"] = f"{EMERALD_PIN}:{where} ({ident}{'; ' + note if note else ''})"
    for key, (symbol, count, where, ident) in EMERALD_SYM_SIZED.items():
        _, size, line = one(symbol)
        if size % count:
            raise ValueError(f"gen_gen3_profile: {symbol} size 0x{size:X} is not {count} equal entries")
        derived[key] = size // count
        src[f"derived.{key}"] = (f"{EMERALD_SYM}:{line} {symbol} 0x{size:X} bytes / {count}; "
                                 f"{EMERALD_PIN}:{where} ({ident})")
    derived["BASESTATS_ADDR_BY_GAME_CODE"] = {"BPEE": sections["rom"]["BASESTATS_ADDR"]}
    src["derived.BASESTATS_ADDR_BY_GAME_CODE"] = "rom.BASESTATS_ADDR under game code BPEE"
    head = subprocess.run(["git", "log", "-1", "--format=%H", "--", EMERALD_SYM],
                          cwd=REPO, capture_output=True, text=True).stdout.strip()
    tables, table_sources = rom_tables("emerald", text)
    return {
        "schema": SCHEMA,
        "generator": "tools/gen_gen3_profile.py",
        "pack": "gen3_emerald",
        "native": native_block("emerald"),
        "source": {"file": EMERALD_SYM, "git_head": head or "unknown",
                   "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()},
        "titles": {"emerald": {
            "_src": src,
            "admitted": True,  # ruling 24: admitted at EG4
            "variant": "emerald",
            "rom_sha1": EMERALD_ROM_SHA1,
            "rom_thumb": _thumb_keys(sections["rom"]),
            "rom_tables": tables,
            "rom_tables_provenance": table_sources,
            **sections,
        }},
    }


def source_block(text: str) -> dict:
    head = subprocess.run(["git", "log", "-1", "--format=%H", "--", SRC],
                          cwd=REPO, capture_output=True, text=True).stdout.strip()
    return {
        "file": SRC,
        # the last commit that touched the source table, not the branch tip: a profile that
        # nothing changed must stay byte-identical so --check means "stale", not "rebased"
        "git_head": head or "unknown",
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }


def render(profile: dict) -> str:
    return json.dumps(profile, indent=2, sort_keys=True) + "\n"


# X1-PACK is opt-in: default generation/checks of existing packs are unchanged.
EXPANSION_BUILD = "28877d73"
EXPANSION_TITLE = "emerald_expansion_28877d73"
EXPANSION_SHA1 = "28877d733492299599f2b8fff50493109d72653c"


def expansion_inputs(build=EXPANSION_BUILD, artifacts=None):
    if build != EXPANSION_BUILD:
        raise ValueError(f"unregistered expansion build: {build}")
    directory = REPO / "data/games/gen3_exp" / build
    facts = json.loads((directory / "facts.json").read_text(encoding="utf-8"))
    artifacts = pathlib.Path(artifacts or os.environ.get("SLINK_EXPANSION_ARTIFACTS", REPO / ".cache/expansion-output/reference"))
    files = {}
    for name in ("pokeemerald.gba", "pokeemerald.sym", "pokeemerald.map"):
        raw = (artifacts / name).read_bytes()
        expected = facts["provenance"]["artifacts"][name]
        if len(raw) != expected["size"] or hashlib.sha256(raw).hexdigest() != expected["sha256"]:
            raise ValueError(f"expansion artifact identity mismatch: {name}")
        files[name] = raw
    if hashlib.sha1(files["pokeemerald.gba"]).hexdigest() != EXPANSION_SHA1:
        raise ValueError("expansion ROM identity mismatch")
    if facts["provenance"]["rom_sha1"] != EXPANSION_SHA1:
        raise ValueError("expansion facts are bound to another ROM")
    rows = {}
    for line, raw in enumerate(files["pokeemerald.sym"].decode().splitlines(), 1):
        m = re.fullmatch(r"([0-9a-f]{8}) [lg] ([0-9a-f]{8}) (\S+)", raw)
        if m:
            rows.setdefault(m[3], []).append({"address": int(m[1], 16), "size": int(m[2], 16), "line": line})
    return {"build": build, "directory": directory, "facts": facts, "symbols": rows,
            "map": files["pokeemerald.map"].decode(), "rom": files["pokeemerald.gba"],
            "source": {"rom_sha1": EXPANSION_SHA1, "source_commit": facts["provenance"]["source_commit"],
                       "symbols_sha256": hashlib.sha256(files["pokeemerald.sym"]).hexdigest(),
                       "facts_sha256": hashlib.sha256(render(facts).encode()).hexdigest()}}


def expansion_symbol(context, name, obj=None):
    hits = context["symbols"].get(name, [])
    if obj:
        spans = re.findall(r"^ \.text\s+(0x[0-9a-f]+)\s+(0x[0-9a-f]+)\s+" + re.escape(obj) + r"$", context["map"], re.M)
        if len(spans) != 1:
            raise ValueError(f"expansion .map has no unique .text span for {obj}")
        lo, size = (int(v, 16) for v in spans[0])
        hits = [r for r in hits if lo <= r["address"] < lo + size]
    if len(hits) != 1:
        raise ValueError(f"expansion .sym has {len(hits)} occurrences of {name} ({obj or 'unscoped'})")
    return hits[0]


def expansion_write(context, filename, value, check=False):
    path = context["directory"] / filename
    text = render(value)
    if check:
        if not path.exists() or path.read_text(encoding="utf-8") != text:
            raise ValueError(f"stale expansion output: {path}")
        print(f"{path.relative_to(REPO)} is current")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
        print(f"wrote {path.relative_to(REPO)}")


def build_expansion(context):
    facts = context["facts"]
    types, const = facts["structs"], facts["constants"]
    sections = {"ram": {}, "rom": {}, "derived": {}}
    src, dropped = {}, {}
    removed = {"gDisableStructs", "gStatuses3", "gTrainerBattleOpponent_A", "BattleIntroGetMonsData"}
    party = {"PARTY_BASE": ("gParties", "B_TRAINER_PLAYER", const["PARTY_SIZE"] * types["Pokemon"]["size"]),
             "ENEMY_BASE": ("gParties", "B_TRAINER_OPPONENT_A", const["PARTY_SIZE"] * types["Pokemon"]["size"]),
             "PARTY_COUNT_ADDR": ("gPartiesCount", "B_TRAINER_PLAYER", 1),
             "ENEMY_COUNT_ADDR": ("gPartiesCount", "B_TRAINER_OPPONENT_A", 1)}
    for (section, key), (symbol, thumb) in EMERALD_SYM_ADDR.items():
        if symbol in removed:
            dropped[key] = f"{symbol} has no expansion equivalent in this build"
            continue
        symbol = "gMovesInfo" if symbol == "gBattleMoves" else symbol
        offset = 0
        if key in party:
            symbol, index, stride = party[key]
            offset = const[index] * stride
        row = expansion_symbol(context, symbol)
        sections[section][key] = (row["address"] + offset) | int(thumb)
        src[f"{section}.{key}"] = f"build:pokemon.sym:{row['line']} {symbol}+{offset}; Thumb={thumb}"
    for name in ("gBattleControllerExecFlags", "gBattlerControllerFuncs"):
        row = expansion_symbol(context, name)
        key = {"gBattleControllerExecFlags": "BATTLE_CONTROLLER_EXEC_FLAGS_ADDR", "gBattlerControllerFuncs": "BATTLER_CONTROLLER_FUNCS_ADDR"}[name]
        sections["ram"][key] = row["address"]
        src[f"ram.{key}"] = f"build:pokemon.sym:{row['line']} {name}"
    row = expansion_symbol(context, "Task_LaunchLvlUpAnim", "src/battle_controller_player.o")
    sections["rom"]["POST_BATTLE_WRITER_TASKS"] = [row["address"] | 1]
    src["rom.POST_BATTLE_WRITER_TASKS"] = f"build:pokemon.sym:{row['line']} Task_LaunchLvlUpAnim in .map player-controller span, Thumb"
    headers = {}
    for _, (symbol, _, constant) in EMERALD_SE_SONGS.items():
        row = expansion_symbol(context, symbol)
        headers[str(const[constant])] = row["address"]
        src[f"rom.SE_SONG_HEADERS.{const[constant]}"] = f"build:pokemon.sym:{row['line']} {symbol}; facts.constants.{constant}"
    sections["rom"]["SE_SONG_HEADERS"] = headers
    derived = sections["derived"]

    def put(key, value, where):
        derived[key] = value
        src["derived." + key] = "facts.json:" + where

    for key, constant in {
        "BADGE_FIRST_FLAG": "FLAG_BADGE01_GET", "BATTLE_TYPE_DOUBLE_MASK": "BATTLE_TYPE_DOUBLE",
        "BATTLE_TYPE_LINK_MASK": "BATTLE_TYPE_LINK", "BATTLE_TYPE_TRAINER_MASK": "BATTLE_TYPE_TRAINER",
        "BOXES_PER_STORE": "TOTAL_BOXES_COUNT", "MONS_PER_BOX": "IN_BOX_COUNT", "PARTY_CAPACITY": "PARTY_SIZE",
        "MAX_LEVEL": "MAX_LEVEL", "B_ACTION_NOTHING_FAINTED": "B_ACTION_NOTHING_FAINTED", "SHEDINJA_SPECIES_ID": "SPECIES_SHEDINJA",
        "SB1_BALL_POCKET_COUNT": "BAG_POKEBALLS_COUNT", "NICKNAME_LEN": "POKEMON_NAME_LENGTH",
        **{f"OUTCOME_{k}": f"B_OUTCOME_{k}" for k in ("WON", "LOST", "DREW", "RAN", "CAUGHT")},
    }.items():
        put(key, const[constant], "constants." + constant)
    for key, type_name in {"BASESTATS_ENTRY_SIZE": "SpeciesInfo", "BATTLE_MOVE_ENTRY_SIZE": "MoveInfo",
                           "TASK_STRUCT_SIZE": "Task", "BATTLE_MON_SIZE": "BattlePokemon",
                           "PARTY_MON_SIZE": "Pokemon", "BOX_MON_SIZE": "BoxPokemon"}.items():
        put(key, types[type_name]["size"], f"structs.{type_name}.size")
    for key, type_name, member in (
        ("BASESTATS_GROWTH_RATE_OFFSET", "SpeciesInfo", "growthRate"), ("BATTLE_MOVE_PP_OFFSET", "MoveInfo", "pp"),
        ("BATTLE_MON_OT_ID_OFF", "BattlePokemon", "otId"), ("BATTLE_MON_PERSONALITY_OFF", "BattlePokemon", "personality"),
        ("BATTLE_MON_HP_OFF", "BattlePokemon", "hp"),
        ("BATTLE_MON_MOVES_OFF", "BattlePokemon", "moves"),
        ("BATTLE_MON_PP_OFF", "BattlePokemon", "pp"),
        ("BATTLE_RESULTS_PLAYER_FAINTS_OFF", "BattleResults", "playerFaintCounter"),
        ("BATTLE_RESULTS_FOE_FAINTS_OFF", "BattleResults", "opponentFaintCounter"),
        ("BOX_DATA_OFFSET", "PokemonStorage", "boxes"), ("GMAIN_CB2_OFFSET", "Main", "callback2"),
        ("SB1_FLAGS_OFFSET", "SaveBlock1", "flags"), ("SB1_VARS_OFFSET", "SaveBlock1", "vars"),
        ("SB1_LOCATION_MAP_GROUP_OFFSET", "SaveBlock1", "location_mapGroup"),
        ("SB1_LOCATION_MAP_NUM_OFFSET", "SaveBlock1", "location_mapNum"),
        ("SB2_ENC_KEY_OFFSET", "SaveBlock2", "encryptionKey"), ("SB2_NAME_OFFSET", "SaveBlock2", "playerName"),
        ("SB2_OT_ID_OFFSET", "SaveBlock2", "playerTrainerId"),
    ):
        put(key, types[type_name]["fields"][member]["offset"], f"structs.{type_name}.fields.{member}.offset")
    # playerGender is not in facts.json's probe (tools/expansion_offsets.c); the harness probe
    # compiles it (tools/expansion_harness_offsets.c F(SaveBlock2, playerGender), checked by
    # gen_expansion_harness_facts.check). reads.lua publishes player_gender only when this key
    # exists, which is what makes the server's rival-by-gender panel filter live. ROM witness:
    # ScrCmd_checkplayergender 0x081f9dc0 `ldrb r2, [r2, #8]` off gSaveBlock2Ptr (test_gen3_exp_player_gender.py).
    harness = json.loads((context["directory"] / "harness_facts.json").read_text(encoding="utf-8"))
    gender = harness["structs"]["SaveBlock2"]["fields"]["playerGender"]
    if gender["size"] != 1:
        raise ValueError("expansion SaveBlock2.playerGender is no longer a u8")
    derived["SB2_PLAYER_GENDER_OFFSET"] = gender["offset"]
    src["derived.SB2_PLAYER_GENDER_OFFSET"] = ("harness_facts.json:structs.SaveBlock2.fields.playerGender.offset; "
        "pin e8bd1cd7:include/global.h:591 playerGender, MALE=0/FEMALE=1 include/constants/global.h:178-179; "
        "ROM: ScrCmd_checkplayergender 0x081f9dc0 ldrb r2,[r2,#8] off gSaveBlock2Ptr")
    put("SB1_BALL_POCKET_OFFSET", types["SaveBlock1"]["fields"]["bag"]["offset"] + types["Bag"]["fields"]["pokeBalls"]["offset"], "SaveBlock1.bag + Bag.pokeBalls")
    put("BATTLE_MON_STAT_STAGES_OFF", types["BattlePokemon"]["fields"]["statStages"]["offset"] + const["STAT_ATK"], "BattlePokemon.statStages + constants.STAT_ATK")
    volatiles_off = types["BattlePokemon"]["fields"]["volatiles"]["offset"]
    perish = types["Volatiles"]["bitfields"]["perishSong"]
    timer = types["Volatiles"]["bitfields"]["perishSongTimer"]
    if (perish["width"], perish["bits"], timer["width"], timer["bits"]) != (1, 1, 1, 2):
        raise ValueError("expansion Perish compiler lanes changed")
    put("BATTLE_MON_PERISH_FLAG_OFF", volatiles_off + perish["offset"],
        "BattlePokemon.volatiles + Volatiles.perishSong compiler bitfield")
    put("BATTLE_MON_PERISH_FLAG_MASK", int(perish["mask"], 16), "Volatiles.perishSong compiler mask")
    put("BATTLE_MON_PERISH_TIMER_OFF", volatiles_off + timer["offset"],
        "BattlePokemon.volatiles + Volatiles.perishSongTimer compiler bitfield")
    put("BATTLE_MON_PERISH_TIMER_KEEP", 0xFF ^ int(timer["mask"], 16),
        "complement of Volatiles.perishSongTimer compiler mask in its u8 lane")
    put("EXPERIENCE_TABLE_ENTRY_COUNT", const["MAX_LEVEL"] + 1, "constants.MAX_LEVEL + 1")
    flag = types["Main"]["bitfields"]["inBattle"]
    put("GMAIN_INBATTLE_OFFSET", flag["offset"], "structs.Main.bitfields.inBattle.offset")
    put("GMAIN_INBATTLE_MASK", int(flag["mask"], 16), "structs.Main.bitfields.inBattle.mask")
    put("OVERWORLD_MODE", "gmain_flags", "Main.inBattle interpretation; client mode name")
    for key, type_name, member in (("MON_SPECIES_MASK", "PokemonSubstruct0", "species"),
                                   ("MON_ITEM_MASK", "PokemonSubstruct0", "heldItem"),
                                   ("MON_MOVE_MASK", "PokemonSubstruct1", "move1")):
        value = types[type_name]["bitfields"][member]
        put(key, int(value["mask"], 16), f"structs.{type_name}.bitfields.{member}.mask")
    # X2: the record-layout keys lua/gen3/reads.lua and gen3_codec.decode_*_masked consume
    # ({word_off, word_size, shift, width} in the substruct), converted from the compiler's
    # {offset, width (bytes), shift, bits} facts -- one representation, no hand copy.
    def field(type_name, member):
        f = types[type_name]["bitfields"][member]
        return {"word_off": f["offset"], "word_size": f["width"], "shift": f["shift"], "width": f["bits"]}

    for key, type_name, member in (("EXPERIENCE_MASK", "PokemonSubstruct0", "experience"),
                                   ("PP_MASK", "PokemonSubstruct1", "pp1"),
                                   ("MARKINGS_MASK", "BoxPokemon", "markings")):
        put(key, int(types[type_name]["bitfields"][member]["mask"], 16), f"structs.{type_name}.bitfields.{member}.mask")
    put("NICKNAME_EXTRA", {"chars": [field("PokemonSubstruct0", m) for m in ("nickname11", "nickname12")]},
        "structs.PokemonSubstruct0.bitfields.nickname11/nickname12")
    put("POKEBALL_FIELD", field("PokemonSubstruct0", "pokeball"), "structs.PokemonSubstruct0.bitfields.pokeball")
    put("ABILITY_NUM_FIELD", field("PokemonSubstruct3", "abilityNum"), "structs.PokemonSubstruct3.bitfields.abilityNum")
    # reads.lua reads BoxPokemon.unknown as the u16 at 0x1E: the hpLost:14 lane shinyModifier shares
    lane, shiny = types["BoxPokemon"]["bitfields"]["hpLost"], types["BoxPokemon"]["bitfields"]["shinyModifier"]
    if lane["offset"] != 0x1E or lane["width"] != 2 or not 0 <= shiny["offset"] - lane["offset"] < 2:
        raise ValueError("BoxPokemon.shinyModifier is not in the u16 lane at 0x1E")
    put("SHINY_MODIFIER_FIELD", {"shift": (shiny["offset"] - lane["offset"]) * 8 + shiny["shift"],
                                 "width": shiny["bits"]},
        "structs.BoxPokemon.bitfields.shinyModifier relative to hpLost's u16 lane")
    put("BASESTATS_ADDR_BY_GAME_CODE", {"BPEE": sections["rom"]["BASESTATS_ADDR"]}, "rom.BASESTATS_ADDR, exact ROM only")
    return {"schema": SCHEMA, "generator": "tools/gen_gen3_profile.py", "pack": "gen3_exp", "build": context["build"],
            "source": context["source"], "titles": {EXPANSION_TITLE: {"admitted": True, "variant": EXPANSION_TITLE,
            "rom_sha1": EXPANSION_SHA1, "rom_thumb": _thumb_keys(sections["rom"]), "_src": src, **sections,
            "unavailable": dropped, "open": []}}}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true",
                    help="exit 1 if a committed profile differs from a fresh generation")
    ap.add_argument("--expansion", choices=[EXPANSION_BUILD], help="generate only this admitted reference expansion build")
    ap.add_argument("--artifacts", type=pathlib.Path)
    args = ap.parse_args()
    try:
        return _generate_profiles(args)
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


def _generate_profiles(args: argparse.Namespace) -> int:
    if args.expansion:
        context = expansion_inputs(args.expansion, args.artifacts)
        expansion_write(context, "profile.json", build_expansion(context), args.check)
        return 0

    rr_witness = REPO / "patch/build/slink_RR.gba"
    if rr_witness.exists():
        rr_rom_facts(rr_witness.read_bytes())
        verify_rr_ability_witness(rr_witness.read_bytes())

    text = (REPO / SRC).read_text(encoding="utf-8", errors="replace")
    profiles = parse_profiles(text)
    source = source_block(text)
    makers = [(pack, lambda pack=pack: build(pack, profiles, source)) for pack in PACKS]
    makers.append(("gen3_emerald", build_emerald))

    # Build every pack fully before writing any of them. A maker that fails (raises, e.g.
    # build_emerald() on a bad .sym) must not leave a partial set of profile.json files on disk;
    # nothing below this point writes until every maker above has already succeeded.
    for title in V2_TARGETS:
        native_block(title)
    rendered = {pack: render(make()) for pack, make in makers}

    stale = []
    for pack, text_out in rendered.items():
        out = REPO / "data" / "games" / pack / "profile.json"
        if args.check:
            current = out.read_text(encoding="utf-8") if out.exists() else ""
            if current != text_out:
                stale.append(str(out.relative_to(REPO)).replace("\\", "/"))
            continue
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text_out, encoding="utf-8", newline="\n")
        titles = json.loads(text_out)["titles"]
        print(f"wrote data/games/{pack}/profile.json: " + ", ".join(
            f"{t} ram={len(v['ram'])} rom={len(v['rom'])} derived={len(v['derived'])}"
            for t, v in titles.items()))
    if stale:
        print("stale (run tools/gen_gen3_profile.py): " + ", ".join(stale), file=sys.stderr)
        return 1
    if args.check:
        print("data/games/gen3_{frlg,rr,emerald}/profile.json are current")
    return 0


if __name__ == "__main__":
    sys.exit(main())
