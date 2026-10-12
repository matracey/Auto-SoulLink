"""tools/gen_polished_engine_sites.py — the Polished Crystal 3.2.3 engine-site and
write-checkpoint packs, the Polished equivalents of `data/games/gen2_crystal/engine_signals.json`
and `data/games/gen2_crystal/write_checkpoint.json`.

Every offset comes from the pinned release build (`data/polished/polishedcrystal.sym`
+ `release/polishedcrystal-3.2.3.gbc`), and every `expected_hex` is READ OUT OF THE ROM at
generation time — never hand-typed. Sites with no verified counterpart are emitted with
`status: UNRESOLVED` and a reason; they are never guessed.

The companion overlay (`patch/dist/SLink-Polished.ups` applied to the release ROM via
`patch/tools/make_ups.ups_apply`) is diffed against the release and every resolved site is
asserted to lie outside the changed spans, so a site byte can never be a patched byte.

Schemas mirror the vanilla packs: `polished-engine-signals-v1` (site keys
signal/kind/symbol/symbol_offset/bank/addr/rom_offset/expected_hex/phase/semantics/
event_role/maturity/physical_firing/runtime_enabled/status/reason) and
`polished-write-checkpoint-v1`.

Usage:
    python tools/gen_polished_engine_sites.py            # write both packs
    python tools/gen_polished_engine_sites.py --check    # exit 1 if either is stale
    python tools/gen_polished_engine_sites.py --roms DIR # override the ROM/sym directory
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import sys

from slink_space import work_root

REPO = pathlib.Path(__file__).resolve().parent.parent
PACK = REPO / "data" / "games" / "polished_crystal"
DATA = REPO / "data" / "polished"
LOCK = REPO / "data" / "polished_sources.lock.json"
PROVENANCE = DATA / "build_provenance.json"
UPS = REPO / "patch" / "dist" / "SLink-Polished.ups"
DEFAULT_ROMS = work_root("cache") / "polished" / "release"

SIGNALS_OUT = PACK / "engine_signals.json"
CHECKPOINT_OUT = PACK / "write_checkpoint.json"

SCHEMA_SIGNALS = "polished-engine-signals-v1"
SCHEMA_CHECKPOINT = "polished-write-checkpoint-v1"
GENERATOR = "tools/gen_polished_engine_sites.py"

# Bytes read from the ROM per site. 6 matches the vanilla pack. A row pinned by `find_hex`
# reads exactly its own byte sequence, so no row needs a hand-typed length any more.
DEFAULT_HEX_LEN = 6


# CPU-instruction proof for the sites a client binds (lua/gen2/signals.lua S.new_polished demands `instructions`
# and `point_symbols` of its site, as build() does of a vanilla one). Each instruction is RE-ENCODED from the .sym and
# must equal the site's ROM bytes exactly, so the text is a decoding of the anchor, never a label. point_symbols are the
# WRAM scalars the binder reads at the site, from the same .sym.
def _le(addr: int) -> bytes:
    return addr.to_bytes(2, "little")


PROOFS = {
    "capture_party": {
        "instructions": (
            ("rst FarCall ; SetCaughtData",  # rst $10 + inline dw addr, db bank (macros/rst.asm)
             lambda s: b"\xD7" + _le(s["SetCaughtData"][1]) + bytes([s["SetCaughtData"][0]])),
            ("ld a, [wCurItem]", lambda s: b"\xFA" + _le(s["wCurItem"][1])),
        ),
        "point_symbols": ("wPartyCount", "wBattleType", "wBattleScriptFlags", "wMapGroup", "wMapNumber"),
    },
    "battle_faint_copyback_return": {
        "instructions": (("call UpdateEnemyMonInParty",
                          lambda s: b"\xCD" + _le(s["UpdateEnemyMonInParty"][1])),),
        "point_symbols": ("wBattleMode", "wCurBattleMon", "wLinkMode",
                          "wBattleMonHP", "wBattleMonStatus", "wPlayerSubStatus2"),
    },
    "whiteout_before_heal": {
        "instructions": (
            ("xor a", lambda s: b"\xAF"),
            ("ld [wSpinning], a", lambda s: b"\xEA" + _le(s["wSpinning"][1])),
            ("ld hl, wMoney", lambda s: b"\x21" + _le(s["wMoney"][1])),
        ),
        "point_symbols": ("hScriptBank", "hScriptPos", "wPartyCount", "wPartyMon1"),
    },
}

SYMPATH = DATA / "polishedcrystal.sym"
SYM_RE = re.compile(r"^([0-9a-f]{2}):([0-9a-f]{4}) (\S+)$")


def flat(bank: int, addr: int) -> int:
    """bank:addr -> flat file offset. Bank 0 is identity, ROMX banks map at 0x4000."""
    return addr if bank == 0 else bank * 0x4000 + (addr - 0x4000)


def read_sym(path: pathlib.Path) -> dict[str, tuple[int, int]]:
    out: dict[str, tuple[int, int]] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = SYM_RE.match(line)
        if m and m.group(3) not in out:
            out[m.group(3)] = (int(m.group(1), 16), int(m.group(2), 16))
    return out


def sha256_file(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sha1_file(path: pathlib.Path) -> str:
    return hashlib.sha1(path.read_bytes()).hexdigest()


def overlay_spans(rom: bytes, ups_path: pathlib.Path = UPS) -> list[tuple[int, int]]:
    """Byte ranges the companion overlay changes. Empty when the UPS is absent."""
    if not ups_path.exists():
        return []
    sys.path.insert(0, str(REPO / "patch" / "tools"))
    from make_ups import ups_apply  # noqa: PLC0415
    overlaid = ups_apply(rom, ups_path.read_bytes())
    spans: list[tuple[int, int]] = []
    for i in range(len(rom)):
        if rom[i] != overlaid[i]:
            if spans and i == spans[-1][1] + 1:
                spans[-1] = (spans[-1][0], i)
            else:
                spans.append((i, i))
    return spans


def spans_overlap(a: tuple[int, int], spans: list[tuple[int, int]]) -> bool:
    return any(a[0] <= hi and lo <= a[1] for lo, hi in spans)


def _proof_bytes(site_id: str, sym: dict) -> bytes:
    return b"".join(encode(sym) for _, encode in PROOFS[site_id]["instructions"])


def _whiteout_guards(rom: bytes, sym: dict) -> dict:
    # callasm emits db opcode, dba target (macros/scripts/events.asm:96-100).
    # Re-derive its opcode from the native word table, not a pasted script byte
    # (engine/overworld/scripting.asm:52-73), then verify the whiteout caller.
    table_bank, _, table_offset = find_in_extent(
        rom, sym, "RunScriptCommand.Jumptable", _le(sym["Script_callasm"][1]))
    if table_bank != sym["Script_callasm"][0] or table_offset % 2:
        raise SystemExit("whiteout_before_heal: callasm must have an aligned same-bank dispatch entry")
    bank, addr = sym["Script_Whiteout"]
    call = bytes([table_offset // 2, sym["LoseMoney"][0]]) + _le(sym["LoseMoney"][1])
    off = flat(bank, addr)
    if rom[off:off + len(call)] != call:
        raise SystemExit("whiteout_before_heal: Script_Whiteout must begin with callasm LoseMoney")
    return {
        "required": True, "combine": "ALL",
        "memory_equals": [
            {"symbol": "hScriptBank", "bank": sym["hScriptBank"][0],
             "addr": sym["hScriptBank"][1], "width": 1, "value": bank},
            {"symbol": "hScriptPos", "bank": sym["hScriptPos"][0],
             "addr": sym["hScriptPos"][1], "width": 2, "byte_order": "little",
             "value": addr + len(call)},
        ],
        "script_context": {
            "symbol": "Script_Whiteout", "bank": bank, "addr": addr,
            "expected_hex": call.hex().upper(),
        },
    }



def S(site_id, signal, symbol, phase, semantics, *, bank=None, addr=None, symbol_offset=0,
      anchor=None, status="RESOLVED", reason=None, notes=None, find_hex=None, guards=None):
    """One site. `bank`/`addr` override the symbol lookup (for hand-placed boundaries,
    where `anchor` names the .sym label the offset was derived from). `find_hex` instead
    pins the site by byte sequence: the generator locates it inside `symbol`'s extent and
    requires exactly one match, so the offset is derived from the ROM, never hand-typed.
    `find_hex` may be a sym -> hex factory; `guards` is a (ROM, sym) -> dict factory."""
    return {
        "signal": signal, "symbol": symbol, "symbol_offset": symbol_offset,
        "bank": bank, "addr": addr, "anchor": anchor, "phase": phase,
        "semantics": semantics, "status": status, "reason": reason, "notes": notes,
        "id": site_id, "find_hex": find_hex, "guards": guards,
    }


RESOLVED = "RESOLVED"
UNRESOLVED = "UNRESOLVED"

# The site list. Every RESOLVED row resolves to a symbol in data/polished/polishedcrystal.sym
# except the four hand-placed boundaries, which name their sym anchor in `anchor`.
# Rows carried over from data/games/gen2_crystal/engine_signals.json keep their vanilla
# `signal` and `phase`; the Polished verdict and evidence live in docs/polished/ENGINE_SITES.md.
SITES: tuple[dict, ...] = (
    # ---- battle / faint ----
    # Every boundary below is pinned by BYTE SEQUENCE (`find_hex`) inside its named .sym routine, never by a
    # hand-typed offset: find_in_extent requires exactly one match in that routine's extent and aborts
    # otherwise, and expected_hex is read out of the release ROM here. The pinned PC is the INSTRUCTION, and
    # each one is checked to be reachable only on the path it names (see each row).
    S("battle_faint", "player_faint", "ResolveFaints.no_fainted_mons", "before_party_copyback",
      "Polished's before_party_copyback boundary: `ldh [hBattleTurn], a` = `e0 d1`, the last instruction "
      "before `call UpdateBattleMonInParty` copies the battle struct into the party record. The battle "
      "struct (wBattleMonHP) is authoritative here; the party record is stale until the next two "
      "instructions run. docs/polished/BATTLE_FLOW.md 1.2. NOT a faint-only path: ResolveFaints reaches "
      ".no_fainted_mons every turn end, faint or not, so a consumer keys on the battle-struct HP it reads "
      "here, never on the site firing.",
      symbol_offset=None, anchor="ResolveFaints.no_fainted_mons", find_hex="E0D1"),
    S("battle_faint_copyback_call", "player_faint", "ResolveFaints.no_fainted_mons", "copyback_entry",
      "`cd b0 34` = call UpdateBattleMonInParty (sym 00:34b0): the instruction that consumes a write made "
      "at battle_faint, immediately followed by `cd c3 34` = call UpdateEnemyMonInParty. The enemy side's "
      "ordering is documented for rival_swap_last_consumption.",
      symbol_offset=None, anchor="ResolveFaints.no_fainted_mons", find_hex="CDB034"),
    S("battle_faint_copyback_return", "player_faint", "ResolveFaints.no_fainted_mons", "after_party_copyback",
      "The instruction immediately after call UpdateBattleMonInParty returns: call UpdateEnemyMonInParty. "
      "Player HP/status copyback has executed on the pinned native path. This is not faint-only: "
      "a bound awaiting plain attempt, native player FAINTED and keyed zero HP/status are still required. "
      "Observation only; never a write window. docs/polished/FAINT_OBSERVER.md section 2.",
      symbol_offset=None, anchor="ResolveFaints.no_fainted_mons", find_hex="CDC334"),
    S("battle_end", "battle_end_result", "ExitBattle", "before_end_processing",
      "Battle exit, before end processing: ExitBattle's first instruction. Fires on EVERY battle exit, wild "
      "and trainer alike -- it brackets the encounter, it does not classify it.",
      symbol_offset=None, find_hex="CD3054"),
    S("wild_ready", "wild_battle_start", "InitEnemy.wildmon", "after_enemy_load",
      "Enemy wild mon staged: InitEnemy branches here (`jr z, .wildmon` at 0f:7264, +$56) only when "
      "`wOtherTrainerClass` reads 0, so the row is wild-only. `3e 01 ea 33 d2` = ld a,$01 / ld [wBattleMode],a.",
      symbol_offset=None, find_hex="3E01EA33D2"),
    S("trainer_ready", "trainer_battle_start", "InitEnemy", "trainer_party_build_entry",
      "TRAINER branch of InitEnemy: `d7 00 40 07` = `farcall ReadTrainerParty` (sym 07:4000), the fall-through "
      "after the `jr z, .wildmon` above. NOT the routine head (0f:7260), which fires on the wild path too.",
      symbol_offset=None, find_hex="D7004007"),
    S("explode_hold", "turn_order", "BattleTurn", "before_turn_ordering",
      "THE Explode Mode write window: `cd 35 42` = call DetermineMoveOrder (sym 0f:4235) inside BattleTurn "
      "(sym 0f:4109, core.asm:190). Reached once per turn after ParsePlayerAction committed an action and "
      "CheckOpponentForfeit did not return early -- never from the .loop1 menu path. Priority resolves through "
      "GetBattleVar(BATTLE_VARS_MOVE) -> BattleVarPairs[18] -> BattleVarLocations[12] = wCurPlayerMove, so the "
      "vanilla explode rule transfers. docs/polished/EXPLODE_RIVAL.md 6.",
      symbol_offset=None, find_hex="CD3542"),
    S("poison_faint", "poison_faint", "DoPoisonStep.DamageMonIfPoisoned", "before_poison_damage_or_cure",
      "N/A for faint reporting: at 1 HP Polished cures poison instead of reducing HP to zero; "
      "otherwise it deals 1 HP damage (engine/events/poisonstep.asm:96-123). This retained row is "
      "the native poison-step entry, not a faint witness.", bank=0x13, addr=0x68CB),
    # ---- boot / save / map ----
    S("new_game", "new_game", "NewGame", "before_reset_wram", "New game reset.", bank=0x01, addr=0x5EF8),
    S("soft_reset", "soft_reset", "SoftReset", "reset_entry",
      "Renamed from vanilla Reset; same address.", bank=0x00, addr=0x0150),
    S("map_entry_complete", "map_load", "EnterMap.dontresetpoison", "after_map_setup_status_publish",
      "Map setup complete.", bank=0x25, addr=0x514B),
    S("save_completed", "save_completed", "UNRESOLVED", "before_success_return",
      "Not re-anchored.", status=UNRESOLVED,
      reason="vanilla _SaveGameData.ok has no Polished counterpart; SaveGameData exists "
             "without .ok and StartMenu_Save.saved is a caller-side label. The vanilla "
             "breakpoint offset was not re-anchored."),
    S("continue_confirmed", "continue_loaded", "UNRESOLVED", "loaded_confirmed_before_map_entry",
      "Not re-anchored.", status=UNRESOLVED,
      reason="vanilla Continue.Check2Pass has no Polished counterpart; only Continue "
             "(01:60ca) exists and the ld a,$8 it sat on was not verified there."),
    S("whiteout_before_heal", "whiteout", "LoseMoney", "before_heal_dispatch",
      "Native whiteout entry before money/text/pause/heal (engine/events/whiteout.asm:9-30,57-60). "
      "Script_Whiteout's callasm LoseMoney is the sole caller; hScriptBank/hScriptPos must identify "
      "that caller after its four-byte opcode has been consumed. The hook fires for every native whiteout, "
      "including trainer forfeits. The consumer snapshots the party at the hook, before HealParty, and "
      "reports a Soul Link whiteout only when every non-egg party mon has HP 0 "
      "(owner ruling 2026-10-08: a trainer forfeit is not a whiteout).",
      find_hex=lambda s: _proof_bytes("whiteout_before_heal", s).hex().upper(), guards=_whiteout_guards),
    # ---- capture / acquisition ----
    S("capture_party", "capture_party", "PokeBallEffect", "post_insert_post_nickname_copy",
      "Party catch only: `rst FarCall SetCaughtData` (engine/items/item_effects.asm ~line 534), the "
      "first instruction after the party-record, OT and nickname rst CopyBytes. Reached only when "
      "the ball catches and the party has room (the box path jumps to .SendToPC first), so it "
      "never fires on an escape. The routine head (03:63A0) fires on every ball use and is NOT "
      "this site. Pinned by byte sequence, not offset: find_hex must match exactly once inside "
      "PokeBallEffect.",
      symbol_offset=None, find_hex="D7084513FA09D1"),
    S("capture_box", "capture_box", "PokeBallEffect.SendToPC", "post_insert_pre_nickname",
      "Catch routed to a box.", bank=0x03, addr=0x6590),
    S("capture_party_finalized", "capture_party", "PokeBallEffect.return_from_capture",
      "after_nickname_requires_acquisition_latch",
      "Capture finalized; also the roamer and contest latch site, as in vanilla.",
      bank=0x03, addr=0x666D),
    S("capture_box_finalized", "capture_box", "PokeBallEffect.return_from_capture",
      "after_nickname_requires_acquisition_latch", "Alias of capture_party_finalized, as in vanilla.",
      bank=0x03, addr=0x666D),
    S("roamer_party_finalized", "roamer_capture", "PokeBallEffect.return_from_capture",
      "after_roamer_name_requires_success_and_type_guards", "Roamer latch, as in vanilla.",
      bank=0x03, addr=0x666D),
    S("roamer_box_finalized", "roamer_capture", "PokeBallEffect.return_from_capture",
      "after_roamer_name_requires_success_and_type_guards", "Roamer latch, as in vanilla.",
      bank=0x03, addr=0x666D),
    S("gift_begin", "gift_static", "GivePoke", "before_gift_insert_attempt",
      "Scripted/static gift. The staged species is a 2-byte dp pair, not one byte.",
      bank=0x03, addr=0x5DF9),
    S("gift_party_finalized", "gift_static", "GivePoke.skip_nickname",
      "after_party_gift_identity_and_name", "Party gift finalized.", bank=0x03, addr=0x5EE9),
    S("gift_box_finalized", "gift_static", "GivePoke.skip_nickname",
      "after_box_gift_identity_and_name", "Box gift finalized.", bank=0x03, addr=0x5EE9),
    S("script_wild_staged", "gift_static", "Script_loadwildmon",
      "after_script_wild_species_and_level_staging",
      "Script opcode that stages a wild static. Species byte offsets inside map scripts are "
      "not derivable from a .sym -- see tools/gen_upr_polished_ini.py.", bank=0x25, addr=0x6A90),
    S("script_givepoke_handler", "gift_static", "Script_givepoke", "script_opcode_handler",
      "Script-opcode twin of GivePoke; both must be hooked or scripted gifts are missed.",
      bank=0x25, addr=0x6FD2),
    S("contest_selected", "contest_capture", "BugContest_SetCaughtContestMon.firstcatch",
      "contest_buffer_selected_before_party_or_box_acquisition", "Bug contest pick.",
      bank=0x03, addr=0x601B),
    S("contest_party_finalized", "contest_capture", "CheckPartyFullAfterContest.Party_SkipNickname",
      "after_contest_party_name_and_metadata", "Contest mon into party.", bank=0x13, addr=0x4453),
    S("contest_box_inserted", "contest_capture", "CheckPartyFullAfterContest.TryAddToBox",
      "after_successful_contest_box_insert_before_name", "Contest mon into box.", bank=0x13, addr=0x447F),
    S("contest_box_finalized", "contest_capture", "CheckPartyFullAfterContest.BoxFull",
      "after_contest_box_name_requires_insert_latch", "Contest box name done.", bank=0x13, addr=0x44D6),
    S("hatch_species", "egg_hatch", "HatchEggs.nottogepi", "after_egg_species_marker",
      "Egg hatch species (the Togepi special case is preserved in Polished too).",
      bank=0x05, addr=0x6FB6),
    S("hatch_finalized", "egg_hatch", "HatchEggs.next",
      "after_hatch_metadata_and_name_requires_slot_latch", "Hatch finalized.", bank=0x05, addr=0x700E),
    S("npc_trade_begin", "npc_trade", "DoNPCTrade", "before_npc_trade_replacement",
      "NPC trade; the give/want species are dp pairs in a fixed 31-byte struct.",
      bank=0x3F, addr=0x51BF),
    S("npc_trade_finalized", "npc_trade", "UNRESOLVED", "after_npc_trade_identity_and_stats",
      "Not re-anchored.", status=UNRESOLVED,
      reason="vanilla DoNPCTrade.incomplete has no Polished counterpart; DoNPCTrade exists "
             "but the post-replacement offset inside it was not located."),
    S("link_trade_received", "link_trade", "LinkTrade.done_animation",
      "after_animation_before_received_party_copy", "Link trade animation done.",
      bank=0x0A, addr=0x4B4B),
    S("link_trade_saved", "link_trade", "LinkTrade.save",
      "after_received_copy_evolution_and_trade_save", "Link trade saved.", bank=0x0A, addr=0x4BB1),
    S("bag_ball_received", "bag_ball_received", "UNRESOLVED", "after_successful_ball_pocket_write",
      "Not re-anchored.", status=UNRESOLVED,
      reason="vanilla PutItemInPocket.done has no Polished counterpart; PutItemInPocket "
             "(03:537e) exists without .done and the success-write offset was not located."),
    # ---- PC / box / evolution ----
    S("pc_release_party_begin", "pc_release", "BillsPC_Release.ReallyReleaseMon",
      "confirmed_before_removal",
      "Vanilla BillsPCDepositFuncRelease is gone; Polished has three labels here, and "
      "ReallyReleaseMon/done bracket the removal. docs/polished/ENGINE_SITES.md section 5.",
      bank=0x12, addr=0x5C46),
    S("pc_release_party_complete", "pc_release", "BillsPC_Release.done",
      "after_confirmed_removal", "Party mon released.", bank=0x12, addr=0x5C14),
    S("pc_release_box_begin", "pc_release", "UNRESOLVED", "confirmed_before_removal",
      "Not re-anchored.", status=UNRESOLVED,
      reason="vanilla BillsPC_Withdraw.release has no counterpart; only BillsPC_Withdraw "
             "(12:5031) exists. The release-before-removal offset was not located."),
    S("pc_release_box_complete", "pc_release", "UNRESOLVED", "after_confirmed_removal",
      "Not re-anchored.", status=UNRESOLVED,
      reason="see pc_release_box_begin."),
    S("pc_deposit_begin", "pc_deposit", "UNRESOLVED", "before_transfer_attempt",
      "Not re-anchored.", status=UNRESOLVED,
      reason="vanilla DepositPokemon is renamed BillsPC_Deposit (12:5035) but the two "
             "vanilla sub-offsets (begin/complete) have no named counterpart. See NEWBOX.md."),
    S("pc_deposit_complete", "pc_deposit", "UNRESOLVED", "after_successful_copy_and_compaction",
      "Not re-anchored.", status=UNRESOLVED, reason="see pc_deposit_begin."),
    S("pc_withdraw_begin", "pc_withdraw", "UNRESOLVED", "before_transfer_attempt",
      "Not re-anchored.", status=UNRESOLVED,
      reason="vanilla TryWithdrawPokemon is renamed BillsPC_Withdraw (12:5031) but its "
             "begin/complete sub-offsets were not re-anchored."),
    S("pc_withdraw_complete", "pc_withdraw", "UNRESOLVED", "after_successful_copy_and_compaction",
      "Not re-anchored.", status=UNRESOLVED, reason="see pc_withdraw_begin."),
    S("change_box_begin", "change_box", "UNRESOLVED", "before_confirmation",
      "Not re-anchored.", status=UNRESOLVED,
      reason="vanilla ChangeBoxSaveGame does not exist in Polished; box switching moved "
             "into BillsPC_ChangeBox (12:5e88), which exposes ONE label for two vanilla "
             "phases, so the begin/loaded pair cannot be reconstructed."),
    S("change_box_loaded", "change_box", "UNRESOLVED", "after_old_box_save_and_new_box_load",
      "Not re-anchored.", status=UNRESOLVED, reason="see change_box_begin."),
    S("evolution_species_published", "evolution_species", "EvolveAfterBattle_MasterLoop",
      "after_party_species_list_publish",
      "Vanilla .skip_unown sub-label is gone; the routine head survives.", bank=0x06, addr=0x4020),
    # ---- Rival Team Swap window (docs/polished/EXPLODE_RIVAL.md section 10) ----
    S("rival_swap_commit", "rival_window", "SendInUserPkmn", "ot_mon_index_committed",
      "SendInUserPkmn commits wCurOTMon here: ld hl,$c4dd / ld a,[de] / dec a / ld [hl],a / ld [$d10c],a. "
      "The commit runs 65 bytes BEFORE the copy, so wCurOTMon != $FF does NOT mean the window closed.",
      symbol_offset=None, anchor="SendInUserPkmn", find_hex="21DDC41A3D77EA0CD1"),
    S("rival_swap_gate", "rival_window", "SendInUserPkmn", "enemy_party_ptr_selected",
      "THE rival-swap write gate: `21 8b d2` = ld hl, wOTPartyMon1Species. The sibling instruction 5 bytes "
      "earlier (0f:47d8, `21 d6 dc` = ld hl, wPartyMon1) is followed by `jr +3` that SKIPS this one when "
      "hBattleTurn reads 0, so the row is the ENEMY send-out only. At this instant the party pointer is not "
      "resolved and no copy has run, so a write to wOTPartyMons[0] is guaranteed consumed.",
      symbol_offset=None, anchor="SendInUserPkmn", find_hex="218BD2"),
    S("rival_swap_last_consumption", "rival_window", "SendInUserPkmn", "after_party_to_battle_copy",
      "Last instruction that consumes wOTPartyMons: `e7` = rst CopyBytes after `01 11 00` (ld bc,$11 = "
      "PARTYMON_STRUCT_LENGTH - MON_LEVEL). A write after this is lost.",
      symbol_offset=None, anchor="SendInUserPkmn", find_hex="E7D1F0D1A7212CD2"),
    S("send_in_user_pkmn", "rival_window", "SendInUserPkmn", "routine_entry",
      "The side-neutral party->battle struct copy routine. Also drives player send-out.",
      bank=0x0F, addr=0x4748),
    S("determine_move_order", "turn_order", "DetermineMoveOrder", "turn_ordering_commit",
      "Turn ordering. Priority resolves through GetBattleVar(BATTLE_VARS_MOVE) -> "
      "BattleVarPairs[18] -> BattleVarLocations[12] = wCurPlayerMove. docs/polished/EXPLODE_RIVAL.md 6.",
      bank=0x0F, addr=0x4235),
    S("lost_battle", "run_over", "LostBattle", "player_lost_decision",
      "The player-lost / whiteout decision point; one of the two battle_hold oracles.",
      bank=0x0F, addr=0x4FF6),
    S("has_player_fainted", "player_faint", "HasPlayerFainted", "side_fainted_decision",
      "Replaces vanilla HandlePlayerMonFaint; the other battle_hold oracle. Polished builds "
      "a two-bit side mask from HasPlayerFainted/HasEnemyFainted in engine/battle/endturn.asm:97-110.",
      bank=0x00, addr=0x3684),
)


# The call that starts turn ordering, found by scanning the release ROM for `cd 35 42`
# (call $4235 = DetermineMoveOrder) forward from BattleTurn. Verified at generation time.
DETERMINE_MOVE_ORDER_CALL = (0x0F, 0x416A)
DETERMINE_MOVE_ORDER_CALL_BYTES = "cd3542"

# SP window: copied from the vanilla pack (same engine family, same home/init.asm layout)
# and flagged UNVERIFIED here because the Polished caller was not re-derived.
SP_WINDOW = {"bank": 0, "exclusive_stack_end": 49407, "minimum_sp": 49152,
             "must_fit_entire_read": True, "read_domain": "System Bus", "region": "WRAM0",
             "required_read_bytes": 2, "search_for_return_address": False}

ORACLES = (
    ("LostBattle", 0x0F, 0x4FF6, "SYM", "player_lost_decision; replaces vanilla LostBattle address"),
    ("HasPlayerFainted", 0x00, 0x3684, "SYM",
     "replaces vanilla HandlePlayerMonFaint, which has no Polished counterpart"),
)


def find_in_extent(rom: bytes, sym: dict, symbol: str, seq: bytes) -> tuple[int, int, int]:
    """(bank, addr, symbol_offset) of the single occurrence of `seq` inside `symbol`'s extent
    (its address up to the next non-sub-label symbol in the same bank). Zero or several
    matches abort generation."""
    bank, start = sym[symbol]
    ends = [a for n, (b, a) in sym.items()
            if b == bank and a > start and not n.startswith(symbol + ".")]
    end = min(ends)
    base = flat(bank, start)
    window = rom[base:base + (end - start)]
    hits = [i for i in range(len(window) - len(seq) + 1) if window[i:i + len(seq)] == seq]
    if len(hits) != 1:
        raise SystemExit(f"{symbol}: byte sequence {seq.hex().upper()} matches {len(hits)} "
                         f"times in {bank:02x}:{start:04x}..{end:04x}, need exactly 1")
    return bank, start + hits[0], hits[0]


def build_site(site: dict, rom: bytes, sym: dict, spans: list[tuple[int, int]]) -> dict:
    out = {
        "id": site["id"], "signal": site["signal"], "kind": "CPU_INSTRUCTION",
        "symbol": site["symbol"], "status": site["status"],
        "phase": site["phase"], "semantics": site["semantics"],
        "event_role": "OBSERVATION", "maturity": "SOURCE_CANDIDATE",
        "physical_firing": "OPEN", "runtime_enabled": False,
        "guards": site["guards"](rom, sym) if site["guards"] else {},
    }
    if site["status"] == UNRESOLVED:
        out["reason"] = site["reason"]
        return out
    bank, addr, symoff = site["bank"], site["addr"], site["symbol_offset"]
    n = DEFAULT_HEX_LEN
    find_hex = site["find_hex"](sym) if callable(site["find_hex"]) else site["find_hex"]
    if find_hex:
        seq = bytes.fromhex(find_hex)
        bank, addr, symoff = find_in_extent(rom, sym, site["symbol"], seq)
        n = len(seq)
    elif bank is None or addr is None:
        if site["symbol"] not in sym:
            raise SystemExit(f"{site['id']}: symbol {site['symbol']} absent from the .sym")
        bank, addr = sym[site["symbol"]]
    off = flat(bank, addr)
    if site["id"] == "whiteout_before_heal" and symoff != 0:
        raise SystemExit("whiteout_before_heal: the anchor must be the native LoseMoney entry")
    if site["id"] == "battle_faint_copyback_return":
        for name in ("UpdateBattleMonInParty", "UpdateEnemyMonInParty"):
            if sym[name][0] != 0:
                raise SystemExit(f"{site['id']}: {name} must be a ROM0 call")
        call_bank, call_addr, _ = find_in_extent(
            rom, sym, site["symbol"], b"\xCD" + _le(sym["UpdateBattleMonInParty"][1]))
        if call_bank != bank or call_addr + 3 != addr or rom[off - 3:off] != b"\xCD" + _le(sym["UpdateBattleMonInParty"][1]):
            raise SystemExit(f"{site['id']}: player copyback call must immediately precede the return witness")
    span = (off, off + n - 1)
    if spans_overlap(span, spans):
        raise SystemExit(f"{site['id']}: 0x{off:X}..0x{span[1]:X} overlaps a companion-overlay span")
    out.update({
        "symbol": site["symbol"], "symbol_offset": symoff,
        "bank": bank, "addr": addr, "rom_offset": off,
        "expected_hex": rom[off:off + n].hex().upper(),
        "hex_len": n,
    })
    if find_hex:
        out["find_hex"] = find_hex
    proof = PROOFS.get(site["id"])
    if proof:
        encoded = _proof_bytes(site["id"], sym)
        if encoded != rom[off:off + n]:
            raise SystemExit(f"{site['id']}: instructions encode {encoded.hex().upper()}, "
                             f"ROM has {rom[off:off + n].hex().upper()}")
        out["instructions"] = [text for text, _ in proof["instructions"]]
        out["point_symbols"] = {name: {"bank": sym[name][0], "addr": sym[name][1]}
                                for name in proof["point_symbols"]}
    if site["anchor"]:
        out["sym_anchor"] = site["anchor"]
    if site["notes"]:
        out["notes"] = site["notes"]
    return out


def build_signals(rom: bytes, sym: dict, spans: list[tuple[int, int]], meta: dict) -> dict:
    sites = {}
    for site in SITES:
        built = build_site(site, rom, sym, spans)
        sites[site["id"]] = built
    resolved = sum(1 for s in sites.values() if s["status"] == RESOLVED)
    return {
        "schema": SCHEMA_SIGNALS, "generator": GENERATOR, "source": meta,
        "evidence_level": "SOURCE", "runtime_admission": "NOT_GRANTED", "f3_complete": False,
        "site_count": len(sites), "resolved_count": resolved,
        "unresolved_count": len(sites) - resolved,
        "companion_overlay_spans": [{"start": lo, "end": hi} for lo, hi in spans],
        "titles": {"polished_crystal": {"sites": sites}},
    }


def build_checkpoint(rom: bytes, sym: dict, spans: list[tuple[int, int]]) -> dict:
    bank, addr = DETERMINE_MOVE_ORDER_CALL
    off = flat(bank, addr)
    if rom[off:off + 3].hex() != DETERMINE_MOVE_ORDER_CALL_BYTES:
        raise SystemExit(f"call DetermineMoveOrder not at 0x{off:X}: got {rom[off:off+3].hex()}")
    oracles = {}
    for name, ob, oa, ev, note in ORACLES:
        ooff = flat(ob, oa)
        if spans_overlap((ooff, ooff + DEFAULT_HEX_LEN - 1), spans):
            raise SystemExit(f"oracle {name} overlaps a companion-overlay span")
        oracles[name] = {"address": oa, "bank": ob, "evidence": ev, "note": note,
                         "rom_offset": ooff,
                         "expected_hex": rom[ooff:ooff + DEFAULT_HEX_LEN].hex().upper()}
    battle_hold = {
        "id": "battle-turn-before-determine-move-order",
        "acceptance": "ALL_REQUIRED_SAME_HELD_EXECUTION",
        "anchors": {"battle_turn": {"bank": sym["BattleTurn"][0], "addr": sym["BattleTurn"][1],
                                    "rom_offset": flat(*sym["BattleTurn"]),
                                    "evidence": "SYM"},
                    "determine_move_order": {"bank": bank, "addr": addr, "rom_offset": off,
                                             "expected_hex": DETERMINE_MOVE_ORDER_CALL_BYTES,
                                             "evidence": "ROM"}},
        "execution_before": {"bank": bank, "pc": addr, "rom_offset": off,
                             "instruction": "call DetermineMoveOrder",
                             "instruction_len": len(DETERMINE_MOVE_ORDER_CALL_BYTES),
                             "expected_hex": DETERMINE_MOVE_ORDER_CALL_BYTES,
                             "source": {"path": "engine/battle/core.asm", "line_start": 190,
                                        "line_end": 190}},
        "caller_stack": dict(SP_WINDOW, status="UNVERIFIED",
                             reason="SP window copied from the vanilla pack; the Polished "
                                    "caller and its return address were not re-derived."),
        "oracles": oracles,
        "ownership_requirements": {"effective_wram_bank": None,
                                   "status": "UNVERIFIED",
                                   "reason": "HRAM script cursor (hScriptBank/hScriptPos) is not "
                                             "on the System Bus; the ownership rule must be "
                                             "re-derived for Polished. docs/polished/"
                                             "ENGINE_SITES.md section 6.1."},
        "state_predicates": [],
        "rival_swap_gate": {"pc": 0x47DD, "bank": 0x0F, "rom_offset": flat(0x0F, 0x47DD),
                            "id": "send-in-user-pkmn-enemy-branch",
                            "note": "PC gate for the Rival Team Swap write window; see "
                                    "docs/polished/EXPLODE_RIVAL.md section 10."},
    }
    primary = {
        "id": "ow-player-input-before-check-a-press",
        "status": "UNRESOLVED",
        "reason": "vanilla CheckAPressOW is not a Polished label. OverworldLoop (25:50d5) is "
                  "the right neighbourhood but the execution_before instruction and its pc "
                  "were not re-derived, so this checkpoint is not armed.",
        "anchors": {"overworld_loop": {"bank": sym["OverworldLoop"][0],
                                       "addr": sym["OverworldLoop"][1],
                                       "rom_offset": flat(*sym["OverworldLoop"]),
                                       "evidence": "SYM"}},
        "acceptance": None, "caller_stack": dict(SP_WINDOW, status="UNVERIFIED"),
        "execution_before": None, "ownership_requirements": {}, "state_predicates": [],
    }
    return {
        "schema": SCHEMA_CHECKPOINT, "generator": GENERATOR,
        "evidence_level": "SOURCE", "runtime_authorized": False, "maturity": "SOURCE_CANDIDATE",
        "companion_overlay_spans": [{"start": lo, "end": hi} for lo, hi in spans],
        "titles": {"polished_crystal": {"battle_hold": battle_hold, "primary": primary}},
    }


def dump(obj: dict) -> str:
    """Deterministic JSON: LF, 2-space indent, trailing newline, no CRLF."""
    return json.dumps(obj, indent=2, ensure_ascii=True, sort_keys=False) + "\n"


def load_meta(rom_path: pathlib.Path) -> dict:
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    prov = json.loads(PROVENANCE.read_text(encoding="utf-8"))
    out = {
        "artifact": "polishedcrystal", "repo": lock["source"]["url"],
        "tag": lock["source"]["tag"], "commit": lock["source"]["commit"],
        "rom_sha1": sha1_file(rom_path), "sym_sha256": sha256_file(SYMPATH),
        "lock_sha256": sha256_file(LOCK),
        "build_provenance_sha256": sha256_file(PROVENANCE),
        "rgbds_version": prov.get("toolchain", {}).get("rgbds", {}).get("version"),
        "evidence_level": "SOURCE",
    }
    return out


def build_all(rom_dir: pathlib.Path = DEFAULT_ROMS) -> tuple[str, str]:
    rom_path = rom_dir / "polishedcrystal-3.2.3.gbc"
    rom = rom_path.read_bytes()
    sym = read_sym(SYMPATH)
    spans = overlay_spans(rom)
    meta = load_meta(rom_path)
    return (dump(build_signals(rom, sym, spans, meta)),
            dump(build_checkpoint(rom, sym, spans)))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--roms", default=str(DEFAULT_ROMS))
    ap.add_argument("--check", action="store_true", help="exit 1 if either pack is stale")
    args = ap.parse_args()
    signals, checkpoint = build_all(pathlib.Path(args.roms))
    if args.check:
        stale = []
        for path, text in ((SIGNALS_OUT, signals), (CHECKPOINT_OUT, checkpoint)):
            if not path.exists() or path.read_text(encoding="utf-8") != text:
                stale.append(str(path))
        if stale:
            print("stale: " + ", ".join(stale) + "; rerun tools/gen_polished_engine_sites.py",
                  file=sys.stderr)
            return 1
        print(f"{SIGNALS_OUT} and {CHECKPOINT_OUT} are current")
        return 0
    PACK.mkdir(parents=True, exist_ok=True)
    for path, text in ((SIGNALS_OUT, signals), (CHECKPOINT_OUT, checkpoint)):
        path.write_text(text, encoding="utf-8", newline="\n")
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
