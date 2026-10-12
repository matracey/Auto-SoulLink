# Polished Crystal: `party_mon` withdraw (spec, 2026-10-04)

**Status (2026-10-06): reconstruction codec DONE (Python + Lua); the write executor is BUILT and COMPOSED (`O.boxes.withdraw`, hardened, idempotent; historical one-mon bank-1 live check (d) PASS on `6e43f8d9`); `supports_box_mon()` stays False pending the gaps in `RC_TRACKER.md` "Box-command gaps".** Nothing here is a release claim. (Sections 2-4 below are the original design; read them with that in mind.)

2026-10-11: supports_box_mon retired by the review fixes; box_mon is always queued.

Evidence tags: **[C]** read by the coordinator from the pinned source (`F:/slink-work/cache/polished/src`, v3.2.3);
**[P]** derived by an OMP peer and cross-checked only for plausibility; treat as UNVERIFIED until a test or a live run
confirms it.

## 1. What the engine does on a withdraw

`engine/pc/bills_pc.asm`: `GetStorageMon` -> `DecodeTempMon` (:887) -> `SetTempPartyMonData` (:960).

* **[C] The savemon holds no HP, status or stats.** `SetTempPartyMonData` recomputes all six stats with
  `predef CalcPkmnStats`, **zeroes status** (`xor a; ld [wTempMonStatus], a`), **sets HP = MaxHP** (0 for an egg) and
  restores PP with `RestoreTempPP`. A withdrawn mon is therefore **fully healed and cured**: the Soul Link server must be
  told (a mon boxed at 3 HP returns at full HP; this is engine behaviour, not a choice).
* **[C] Layouts.** `SAVEMON_*` is identical to the party struct up to and including personality (`+0..+21`); after that
  it is shifted: savemon `PP_UPS(22) HAPPINESS(23) PKRUS(24) CAUGHT(25..27) LEVEL(28) EXTRA(29..31)` becomes party
  `PP(22..25) HAPPINESS(26) PKRUS(27) CAUGHT(28..30) LEVEL(31) STATUS(32) skip(33) HP(34) MAXHP(36) STATS(38..47)`
  (`constants/pokemon_data_constants.asm`). The 3 `EXTRA` bytes are NOT in the 48-byte struct: they ride at the tail of the
  11-byte OT array (`wTempMonOT + PLAYER_NAME_LENGTH`), which also carries the hyper-training mask.
  HP, MaxHP and the five stats are **big-endian** in the party struct (`CalcPkmnStats` stores `hMultiplicand + 1` first).
* **[C] Stats.** `CalcPkmnStatC` reads three RAM options **at rebuild time**: `wInitialOptions` bit `NATURES_OPT`
  (clear => every stat neutral), `wInitialOptions` bit `PERFECT_IVS_OPT` (set => every stat DV 15, before hyper training),
  `wInitialOptions2 & EV_OPTMASK` (zero => EVs ignored). The write path must read the **live** values and pass them to
  `savemon_to_party` / `S.party_from_savemon` (all required arguments, no defaults); it must refuse if it cannot read them.
* **[C] PP.** `ComputeMaxPP`: `base + ups * min(base // 5, 7)`, final byte masked; base 40 with 3 ups = 61.
  `_RestoreAllPP` stops at the first empty move slot.
* **[C] Bad checksum.** `DecodeTempMon` substitutes a Bad Egg on a failed checksum; SLink must refuse instead
  (`decode_savemon` verifies the checksum).

## 2. Bytes to write (executor design, [P] from `cx-1db940ac`, order is the peer's preference)

1. The 48-byte party record (`savemon_to_party`), the 11-byte nickname and the 11-byte OT array (8 name + 3 extra).
2. `wPartyCount` **last** (the engine increments it only when appending past the count; Polished has no party species
   list). A reset before the count is written leaves the mon in both places, which is recoverable; the opposite order can
   leave a count that includes a zeroed slot.
3. Box side: the engine clears only the box slot's pointer byte (`Entries`) and its `Banks` bit. **It never frees the
   pokedb entry** (bytes, name-MSB checksum and allocation flag stay), so the executor must not clear them. A freed entry is
   reused by `NewStoragePointer` and compacted by `FlushStorageSystem` only on allocation failure.
4. Do not touch the dex flags or `sPartyMon1Mail` (a withdraw appends without swapping).
5. Reuse: `polished_boxes.lua` `gated`, `pointer/bank_byte/flag_byte`, `read_boxes`; `polished_overworld.lua` `O.checkpoint`,
   `write_party_block`, the mail refusal. (Historical: the old `withdraw` stub that refused by name has been replaced by the executor.)

## 3. Acceptance tests (to write with the executor; red control per line)

1. record equals the engine formula for hand vectors (status 0, HP == MaxHP, PP restored with ups);
2. flip each of the three options -> stats change as the source says;
3. `wPartyCount` unchanged below the count, +1 above, written after the record (assert the write log order);
4. box pointer byte 0 and Banks bit clear while the pokedb entry bytes and allocation flag are byte-identical;
5. refusals: party full, incomplete census, bad checksum, unknown key, hold not satisfied, options unreadable;
6. round trip withdraw -> deposit gives a byte-identical pokedb entry modulo slot choice;
7. mutants: skip the status/HP write; copy stats from the savemon; read saved options instead of live; clear the pokedb
   allocation flag; write `wPartyCount` first; drop the census read-back; drop the party-full refusal.

## 4. Open

* RESOLVED (Codex fact-check cx-be6a1e40, 2026-10-05): cosmetic forms (Unown letters, Magikarp patterns, Pikachu/Pichu variants, Arbok markings; Arbok Johto is the default form 1) are only in the cosmetic table, so `GetBaseData` falls back to the plain species record (home/pokemon.asm:151-167, 466-527); only the 46 variant rows (records 292..337) select a different BaseData record. The repo's `variant_record[species*32+form]` rule matches the engine for all 289 defined species x 32 forms x gender/egg bits (36,992 source-model cases, 0 differences). Exceptions are only invalid species ids (0, 256, 511), which the codec refuses. TODO: add an engine-table-derived lookup test (the current oracle reuses `pc.effective_species`).
* SUPERSEDED: the executor is composed and has one historical live pass; `supports_box_mon` stays False until the box-command gaps in `RC_TRACKER.md` close (it must never flip before them).
* Known limit (documented, not fixed): reconciliation of a both-places state matches by identity key plus byte equality of the rebuilt party record, so two byte-identical, independently caught mons (one boxed, one in the party) are indistinguishable and collapse into one when a reconcile removes the box copy.

2026-10-11: supports_box_mon retired by the review fixes; box_mon is always queued.

## Idempotent withdraw (2026-10-06; review cx-4beeb281, cx-082b6600)
`party_mon` for a key that is in NO box and is exactly one NON-EGG party mon returns done with no write (`{already_in_party = true}`): the client keeps no settle for Polished, so a duplicate or re-sent command must be a re-ack, because a `key not boxed` refusal makes the server drop the key and re-box the partner. Limits, stated plainly: (1) it acknowledges a CURRENT key-level postcondition, not that this command moved this individual (the key excludes nickname, moves, experience): an unrelated same-key party mon satisfies it, a broader form of the byte-identical collision above; (2) a mon at HP 0 refuses `dead in the party (hp 0)` (a dead mon must not be re-announced as retrieved). CHARACTERISED with the real `SoulLinkState` (`tests/unit/test_polished_state_roundtrip.py`): once the server has processed the death (pair DEAD) the refusal re-boxes nobody and never re-adds the key; but in the narrow window where the mon is already at HP 0 in the cartridge and the pair is still ALIVE on the server (the faint event not yet processed), the refusal is read as a failed retrieval: the rebuild key is dropped, the key is discarded and `box_mon` is queued for the partner. The proper fix is the three-outcome contract or a server guard (`BOX_WRITE_CONTRACT.md`); the code comment at `polished_overworld.lua:~681` that says it re-boxes nobody is only true after the death is processed (comment fix queued for the freeze cut); (3) a retry after a torn withdraw (Entries cleared, Banks bit not) reaches this branch and acknowledges logical membership without finishing the Banks cleanup: no mon is lost, the stale selector bit sits on an empty slot; (4) a full party containing the unique match still succeeds (the branch precedes the capacity check). The server round-trip is exercised by `tests/unit/test_polished_state_roundtrip.py` (the real `SoulLinkState` driven by the composed client, per-event snapshots) with these LIMITS: it runs inside a SEEDED, permanently open rebuild (which also suppresses the server's party reconcile), feeds a bare tick (no party snapshot) and ignores returned command lists, so it proves duplicate safety during that seeded state, not a natural session; a no-rebuild case and a production-shaped rebuild are queued.
