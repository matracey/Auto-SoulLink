# C5 commit and C6 responder: proposed ROM contracts

**Status update 2026-10-08:** C6 landed (overlay `57f039b6` then, including its responder fix); C5 landed (merge `7f56228b5`, overlay `877a477a`, `SlinkTradeCommitEnabled` at `7e:573B` = **0**, not enabled). See `TRADE.md:1417-1429` and `overlay_provenance.json:63`; no live qualification or commit enablement is claimed. The introduction below describes its historical design cut. Bounded rollback `33f3b08bf` (merge `8d80f1ea3`) supersedes that overlay with current `cf03f53accefbc5f3fee9062846699e30c4c987b` / UPS 3767 B; rollback is limited to last/sole-slot pre-UI removal failure, commit gate remains 0 (`trade_commit.asm:1-21`; `overlay_provenance.json:63,85-88`).

Design only. Source cut: `b082abbe69162d02617dd8e58974629ee0faac5b`; overlay v8 SHA1
`add6c9440d3485839996525052cf34d5d04963d1`. No commit, responder, production capability,
server protocol change or live run is authorized by this document. The existing proposer
publishes only NOT_PERFORMED after APPLY; the responder entry is a `ret`.
Evidence: `data/polished/overlay_provenance.json:1-27`, `patch/polished/src/trade_service.asm:186-238`,
`patch/polished/src/trade_dispatch.asm:136-143`, `docs/polished/TRADE.md:1409-1413`.

## Reading the evidence

`P/` below means the **read source** at `F:/slink-work/cache/polished/src/`, pinned to
v3.2.3 commit `3fa43192379df5c3e7b09a08e4d5d79af4f02f42` (checked with Git).
Repository paths are relative to `F:/slink-work/wt/pol-c56doc/`. All cited line ranges
refer to these cuts. **PROPOSED** means an implementation requirement, not existing
behavior; **UNVERIFIED** means source/model evidence has not established the claim.
Native routine symbols, bank/address and geometry must always come from the rebuilt sym.

**Three highest-risk questions before enabling commit:**

1. **Can every interruption recover a coherent party/mail/save image?** Polished removal
   rotates SRAM mail immediately, unlike the vanilla helper's delayed shift. Main-save,
   backup-save and mail/storage recovery phases must be exercised by cold-load cuts;
   neither a returning save routine nor a RAM comparison proves this. See §3.
2. **What exact incoming domain is authorized, especially eggs, forms, HP/stats and the
   last usable mon?** The current ROM validator deliberately does not validate moves,
   HP/status/stats or form policy. Enabling native animation/evolution broadens its consumers.
   Egg display normalization and suppression of egg evolution need explicit tests and an
   owner policy; species/level validity alone is insufficient. See §2.
3. **Does the full native call chain fit the real stack and remain non-reentrant?** The
   real stack is C000..C0FF; the existing SM83 tests default to D000 and have no interrupt
   or SRAM/WRAM banking model. Responder menus, evolution and a full save are deeper than
   the current proposer stub/service evidence. See §5.

## 1. Preserve the existing boundary

- Keep the exact 70-byte outgoing snapshot (48-byte record + 11-byte OT span + 11-byte
  nickname) in OT slot 1. Incoming data stays in scattered OT slot 0; sender name is a
  separate 11-byte span. No contiguous-pointer shortcut and no relaxed comparison mask.
  (`patch/polished/src/trade_snapshot.asm:4-20,28-93`;
  `trade_validate.asm:4-13,111-149` in the same directory.)
- Preserve v8's APPLY inspection-before-expiry: frame 3600 is eligible; frame 3601 is not.
  Keep the host's strictly-later-frame OFFER-answer/APPLY rule. Do not transplant vanilla's
  old counter-first APPLY loop. (`patch/polished/src/trade_service.asm:145-186`;
  `docs/polished/TRADE.md:1413`; `lua/gen2/polished_trade.lua:357-374`.)
- C5 exports `SlinkTradeCommit` and `SlinkTradeCommitEnd`. C6 exports
  `SlinkTradeResponderService` and `SlinkTradeResponderServiceEnd`; the fixed PromptEntry
  becomes a `jp` trampoline to that real body. The existing paired-marker profile logic
  describes component presence, not authorization; retain `production: false`.
  (`tools/gen_polished_profile.py:292-308,362-369`;
  `patch/polished/src/trade_dispatch.asm:136-143`.)
- These ROM cards alone cannot deliver a production two-player trade: the merged host
  binder refuses PROMPT, never advertises, maps unexpected DONE values to UNCERTAIN, and
  is exposed only as a dev part. A separate host integration card is a dependency, not
  an implied side effect of adding symbols. (`lua/gen2/polished_trade.lua:337-401`;
  `lua/gen2/entry.lua:950-979`.)

## 2. C5: bounded native mutation contract

### Interface and preflight (PROPOSED)

Input: A = selected outgoing **zero-based slot**, B = role 0 proposer / 1 responder.
The caller owns a held, authenticated lease, the private token/generation/count/slot,
an exact snapshot, and a current successful full-save checkpoint. It has an open speech
textbox. Never infer the role from serial-clock state; SLink uses no cable exchange.
This adapts the vanilla helper interface, not its implementation
(`patch/gen2/src/trade_commit.asm:1-42`; native serial role selection is
`P/engine/link/link.asm:1603-1609`).

Return values: **1** only for a proved pre-mutation refusal; **0** only after mutation,
native evolution/postconditions and the chosen native save have returned; **2** for any
uncertain/post-mutation failure. An unexpected return value is treated as 2 by the caller.
Zero is a *cartridge completion observation*, not an independently durable two-player receipt.
This is a proposed refinement: vanilla returns 2 even for its bad-entry branch
(`patch/gen2/src/trade_commit.asm:15-42`); current Polished has no helper call at all
(`patch/polished/src/trade_service.asm:197-203`).

Before the first **party, dex or SRAM mutation**, recheck:

1. Role, WRAM mapping, normal VBlank mode, no battle/link/paused-save/contest context,
   party count 1..6, slot within count, private count unchanged, live save identity and
   the same token/slot/generation. Lease pickup ACK and stack/private context are not a
   party mutation. The existing APPLY path already checks most transport/count conditions;
   the commit entry must not be callable with guessed globals.
   (`patch/polished/src/trade_service.asm:145-200,343-378`;
   `patch/polished/src/trade_dispatch.asm:81-130`.)
2. Call `SlinkTradeValidateIncomingStaged`, `SlinkTradeCheckOwnSlot` and exact
   `SlinkTradeValidateSnapshot` immediately before the mutation boundary, with no menu,
   delay, save prompt or host-staging window between the final checks and removal.
   Validate both outgoing and incoming names before rendering them. Native getters are
   not a substitute for validation. Existing predicates accept underlying species
   1..FE or 101..123, level 1..100, legal non-mail item, nature <25 and bounded terminated
   text; they do **not** establish full form/stat policy.
   (`patch/polished/src/trade_validate.asm:15-39,43-149`;
   `docs/polished/TRADE.md:1376-1380`.)

   The current responder validates the selected local nickname with
   `SlinkTradeValidateText` (eleven-byte bound, terminator `$53`, no control bytes) before
   `GetNickname` or any native UI. Incoming staged names retain their independent validator.
3. Apply the owner-approved last-usable-mon rule to **the resulting party**, not merely
   count >1. Native `CheckAnyOtherAliveMonsForTrade` searches other local HP, then incoming
   HP; it permits replacing the sole usable mon with a usable incoming mon. Preserve this
   rule unless the owner explicitly chooses another policy. Its globals are slot indices
   at this point. (`P/engine/link/link.asm:1219-1251`.)
4. **DECIDED 2026-10-07: current validator only (species, item, nature, level, name terminators); no extra egg/form domain** (owner decision relayed by coordinator card `g2p-decisions`). Current tests may directly exercise defined cases, but no production caller may interpret the current
   permissive record validator as certification of arbitrary incoming records.
   (`docs/polished/TRADE.md:1379-1380`; `patch/polished/src/trade_validate.asm:24-26`.)
   Commit **ENABLEMENT** remains OPEN: non-atomic mutation with no rollback (`trade_commit.asm:5`), real native/ISR stack high-water, cold-load coherence; enabling it needs a separate owner yes.

5. Mail policy must include the **whole local party**, not just the selected record:
   the receptionist invokes `CheckPartyForMail` before the save/wait flow
   (`P/maps/PokeCenter2F.asm:83-101`; `P/engine/link/link.asm:2820-2837`).
   Proposed default for C6 and direct C5 entry: refuse any local mail holder and any incoming
   mail before commit. Use the explicit carry/item-table predicate; do not assume the script
   wrapper's hScriptVar is a Boolean on every no-mail path without a test (the source stores
   A on the no-mail exit). `ItemIsMail_a` is compare plus complement-carry
   (`P/home/header.asm:114-122`). Permitting unselected party mail later requires an explicit
   policy relaxation and additional accepted-mail recovery evidence.

### Exact proposed sequence and native differences

| Step | Operation and postcondition | Read evidence / difference from vanilla |
|---|---|---|
| Prepare display | Before changing the outgoing slot, populate both **native 53-byte** trademon buffers: sender/OT/name fields, OT ID, **three** DV bytes, personality and full form. Set display caught-data to zero. For eggs, use display species EGG and clear EXTSPECIES_MASK in the **display form only**, retaining the egg flag; assert that the display extended species is FF, never 1FF. Never replace the underlying party species with EGG. | `P/macros/ram.asm:252-276`; `P/engine/link/link.asm:1474-1574`; `P/engine/movie/trade_animation.asm:1253-1263`; `P/engine/gfx/trademon_frontpic.asm:1-23`; risk noted in `docs/polished/TRADE.md:1379-1380`. Explicit personality initialization is required by its consumer even though the cable preparation excerpt does not fill every byte. Test the proposed egg-display normalization; do not assume the generic Lua encode helper supplies it. |
| Save controls | Preserve BC/DE/HL, role, original count/slot, and any native controls this helper changes (at least link mode, force evolution, current party/OT indices, withdraw parameter, jumptable/dialog, state flags/sprite updates; record others found by the write census). Keep the original slot separate from native variables that later become species bytes. | Native cable path overwrites `wCurTradePartyMon`/`wCurOTTradePartyMon` with species at `P/engine/link/link.asm:1576-1595`; vanilla preservation pattern `patch/gen2/src/trade_commit.asm:45-86`. Do not copy a guessed byte list without a write census. |
| Remove | Set `wCurPartyMon=outgoing slot`, `wPokemonWithdrawDepositParameter=0`; call the **Polished** `RemoveMonFromParty` predef. Require count N-1. This routes to `SetStorageBoxPointer` with B=0, C=slot+1, E=0, shifts the selected slot to the end, then decrements count. | `P/engine/pokemon/move_mon.asm:894-901`; `P/engine/pc/bills_pc.asm:539-550,585-623`. Not vanilla `RemoveMonFromPartyOrBox`. The chain swaps record, nickname, full OT span **and SRAM mail** (`bills_pc.asm:263-297`). Do not call vanilla `.ShiftMail` afterward or copy the cable routine's separate mail-exchange block. |
| Animate | Disable sprite updates; clear/load native graphics using Polished `ClearTileMap`, `LoadFontsBattleExtra`, A=`CGB_PLAIN`, `GetCGBLayout`. Select `TradeAnimation` or banked `TradeAnimationPlayer2` from the local role. No serial wait/check-byte loop. | `P/engine/link/link.asm:1597-1609`; `P/engine/movie/trade_animation.asm:1-16,57-72`. `GetSGBLayout`/SCGB conventions and a near call from another bank are wrong ports. Symbol currently places Player2 at 0A:52DE. |
| Materialize | After animation, copy incoming OT slot 0 to temp with **B=$81, C=1, HL=wOTPartyMon1Species**, `farcall CopyBetweenPartyAndTemp`; it copies record, nickname and full OT metadata. Then `farcall AddTempMonToParty`; require carry clear and count N. | `P/engine/link/link.asm:1611-1624`; `P/engine/pc/bills_pc.asm:625-670`; `P/engine/pokemon/move_mon.asm:448-512`. Copying just 48 bytes leaves stale temp names. Do not set/use a nonexistent `wOTPartySpecies` list or require host-written `wOTPartyCount=1`. |
| Append check | Newly appended slot is N-1. Compare its species **and full form**, OT 11 and nickname 11 with staged input; compare the 48-byte record with only the native non-egg happiness change allowed. Preserve caught-data, caught-level/location, level and held item here. Verify the unaffected party prefix/order in the model tests. | Append copies all 48 bytes and both name spans; for non-eggs only, it sets seen/caught and BASE_HAPPINESS (`move_mon.asm:455-509`). Egg path skips both. Nine-bit decoding is `P/home/pokemon.asm:408-416`; dex species/form dispatch is `P/home/pokedex_flags.asm:89-131`. No blanket mask and no clearing the actual party caught fields. |
| Evolve | For an owner-admitted non-egg, set `wCurPartyMon=N-1`, `wForceEvolution=EVOLVE_TRADE` (**3**, not TRUE=1), use the intended nonzero trade link mode during native evolution, then `farcall EvolvePokemon`. Check count remains N. For eggs, proposed safe rule is **skip forced evolution**, preserving the egg record; owner must explicitly approve egg support or refuse eggs before removal. | `P/constants/pokemon_data_constants.asm:312-317`; `P/engine/link/link.asm:1594-1595,1623-1626`; `P/engine/pokemon/evolve.asm:1-56,93-107,153-169`. Everstone blocks; Linking Cord rows need EVOLVE_TRADE, other trade rows require/consume the held item (`evolve.asm:298-304`). Examples: `P/data/pokemon/evos_attacks.asm:1278,1309,1505,1562,2077`. |
| Evolved check | Expect native changes to species/form, consumed item, non-custom species name, max stats/HP delta and potentially learned moves. Verify identity continuity (OT, DVs, unaffected personality/metadata), valid final record and expected native evolution path; do not compare evolved output byte-for-byte with the unevolved stage. | `P/engine/pokemon/evolve.asm:364-379,425-499`. `GetEvosAttacksPointer` does not test the egg bit; the separate `GetEvolutionData` does (`evolve.asm:807-826`, `P/home/pokemon.asm:499-505`). Egg suppression here is a deliberate proposed guard, not a claim that native EvolvePokemon guarantees it. |
| Save and restore | Use the full post-commit save contract in §3. Only after it returns successfully and the postconditions hold may the helper return 0. Restore caller controls, SRAM closure/bank contract and map speech display with `ReturnToMapWithSpeechTextbox`; leave the responder's final CloseText to its service exit. Do not return to serial/link-room machinery. | Map restoration and its own DelayFrame: `P/home/map.asm:1597-1622`; NPC control preservation: `P/engine/events/npc_trade.asm:61-72`; serial loop to omit: `P/engine/link/link.asm:1632-1666`. |

**UNVERIFIED:** the complete set of native animation/evolution scratch writes, maximum
stack depth and all legal regional/cosmetic evolution outcomes. The table defines the
sequence to test, not permission to assume those postconditions. Runtime input is frozen
for the lease; the host must not restage after pickup. Native UI potentially clobbering OT
slot 0/snapshot must be detected in the oracle, not hidden by a broad write whitelist.

## 3. Saving, uncertainty and mail/storage recovery

**Proposed baseline:** retain the receptionist's pre-trade full-save checkpoint; C6 must
obtain a user-confirmed `Link_SaveGame` before its snapshot. After successful append and
evolution, use `ForceGameSave` (no new overwrite question) for a **full native save**, not
an invented party-byte flush. This is a deliberate stronger choice than native cable
`SaveAfterLinkTrade`; approve it as part of C5's commit policy.

Read facts:

- `Link_SaveGame` calls `AskOverwriteSaveFile`, then `ForceGameSave`; Force sets pause,
  calls `SavedTheGame`, clears pause, returns carry clear. `SavedTheGame` invokes
  `SaveGameData` and writes the save version (`P/engine/menus/save.asm:52-65,148-156`).
- `SaveAfterLinkTrade` saves Pokémon data, primary/backup checksums, backup Pokémon data,
  mail backup and RTC; it is **not** the full player/options/storage path
  (`save.asm:36-50,311-330`). Dex flags lie inside wPokemonData, so do not incorrectly
  claim that it omits dex: sym `wPokemonData=DCCE`, `wPokedexCaught=DE7B`,
  `wPokedexSeen=DEAD`, `wPokemonDataEnd=DFF3` (`data/polished/polished_slink.sym:67655-68175`).
- Full save records options/player/Pokémon data, sets the save phase before making the
  main checksum valid, writes backup mail/storage/game data and clears the phase
  (`save.asm:158-213`). The full-save native storage backup is expected; arbitrary
  gameplay box insertion/allocation is not part of this party-to-party transaction.
- `SaveStorageSystem` copies active box pointers into their backup; `AddStorageMon`
  instead allocates a pokedb entry and may Crash if already occupied. Never route C5
  through a box-deposit/newbox allocation path (`save.asm:224-233`;
  `P/engine/pc/bills_pc.asm:495-532,672-703`).
- Load checks primary/backup checksums and save phase, may finish `WriteBackupSave`,
  then restores party mail and storage (`save.asm:386-429`). Mail backup/restore copies
  all six party mail entries plus mailbox data (`P/engine/pokemon/mail.asm:235-258`).

**Mutation boundary:** after RemoveMonFromParty starts, there is no safe-refusal return,
no automatic retry and no rollback promise. A count/readback/evolution/save failure is
result 2, held for recovery; do not save an already-detected partial party merely to
finish the handshake. At result 0 also retain the transaction until matching RELEASE;
there is no B or timeout escape after commit entry. Result 2 ignores RELEASE. This is
the vanilla service's distinction to port deliberately, not current Polished behavior
(`patch/gen2/src/trade_service.asm:282-352` versus Polished `trade_service.asm:206-238`).

**Proposed runtime save readback:** before returning 0, compare primary and backup saved
Pokémon-data spans (count/records/OT/nickname/dex included) with the live postimage, verify
their native checksum coverage and save phase 0, and close SRAM. Derive each range from
the copy/checksum symbols, not a guessed save-file offset (`P/engine/menus/save.asm:311-330,352-384,394-401`).
Any mismatch or unclassifiable read is result 2. The backing device's persistence remains
UNVERIFIED even after this in-process comparison; it does not replace cold loading.

**Cold-reload acceptance:** two independently staged native save copies, process stopped
without savestate recovery, cold boot, then read party/count/order, identity/form/item,
OT extras, nickname, HP/stats, dex, mail association and gameplay/backup storage census.
Cut before removal, inside rotations, after append, during evolution, and at each primary/
backup/mail/storage save phase. Outcomes may be coherent old or coherent new save according
to the native phase, never an invented universal atomicity claim. Result 0 plus cold-load
new state on both sides is required for a durable trade receipt. A model trap returning
from save cannot close this obligation. Unrelated party mail must be a refusal control
under the proposed default; an owner-enabled relaxation needs accepted-mail interruption
cuts too. Mail-array bytes still rotate even when the active party holds no mail.

## 4. C6: responder consent and held APPLY

Port the **flow** of `patch/gen2/src/trade_service.asm:135-209,210-352`, not its species-list,
save assumptions or counter-first timeout. Proposed phases:

1. Fixed `SlinkTradePromptEntry` jumps to the real responder body; preserve the dispatcher's
   DE and return depth. Recheck header, PROMPT, generation != ACK, nonzero token, live party/
   slot/domain conditions. Allocate/zero the same 10-byte stack context; copy token, own slot,
   generation/count and role=1. Publish pickup ACK=received generation **before any native UI**.
2. Before rendering incoming nickname, run bounded text/record policy checks. Own outgoing
   nickname comes from the selected local slot. OpenText, offer text, YesNoBox; NO/B declines.
   On YES, ask permission for the full save; call Link_SaveGame and treat carry/decline as
   no consent. This can save before any trade; do not describe the pre-APPLY path as SRAM-free.
3. After every menu/save returns, recheck the held token/generation/slot, count and outgoing
   legality, then capture the exact 70-byte snapshot. Publish PROMPT completion: result 0
   means **consent only**, result 1 means decline. Preserve the received generation; DONE
   publication order is result/header/token/slot/command, generation, **ACK last**.
   Reuse the Polished publisher's stack-relative contract, not copied offsets at a new depth
   (`patch/polished/src/trade_service.asm:275-320`).

   **Normalization checkpoint:** the proposer script runs `FixPlayerEVsAndStats` before
   its menu (`P/maps/PokeCenter2F.asm:83`). C6 lacks that script. Proposed parity: after the
   user's save consent, but before the full save and snapshot, perform that normalization,
   preserving/revalidating the selected slot. It can change EVs, stats and current HP, so
   this pre-save branch is not party-byte-read-only and must have its own whitelist.
   Never normalize after the snapshot. The native routine loops all party slots through
   `UpdatePkmnStats`; EV adjustment depends on the modern-EV option, and HP is adjusted
   for stat changes (`P/engine/pokemon/move_mon.asm:953-1023`;
   `P/engine/pokemon/mon_stats.asm:545-561`). If the later native overwrite dialog is
   declined, report no trade, not “no bytes changed.”

   **Current responder checkpoints:** `SlinkTradeResponderCheckConsent` verifies header,
   token, generation/ACK, PROMPT command, the private selected slot, current own-slot
   legality and unchanged party count immediately after either consent menu returns.
   The check precedes normalization, is repeated before the full save, and runs again after
   save and before snapshot. Decline also rechecks before publishing. A canceled/replaced
   visit exits before the next SLink trade write (snapshot, consent/APPLY publication or
   commit) and never publishes a stale consent. Changes already made while the preceding
   checkpoint was valid are not described as rolled back.
   **Native save boundary (2026-10-12 review):** once the checkpoint before the full save
   passes, entering `Link_SaveGame` is an irrevocable, player-confirmed native save of the
   current pre-trade state. Inside it, the native `AskOverwriteSaveFile` question (shown when
   the saved and live OT differ) is the game's own prompt; if the visit is canceled while it
   waits and the player answers YES, the native erase and save complete before the
   post-save checkpoint, which then exits without any SLink trade write. rc2 (`c98120f61`,
   overlay `fe8c57e1`) had the same path; no SLink revalidation runs inside the native save.
   `tests/unit/test_polished_responder_safety.py` executes these checks in the shipped
   responder bytes with native menus/save/normalization trapped; it is not SRAM-durability
   or live interrupt-timing proof.
4. Decline: bounded 90-frame RELEASE wait, with B/timeout allowed, then close lease and
   CloseText. Consent: keep the foreground stack, textbox, token and snapshot while waiting
   for matching RELEASE of the PROMPT generation; only after observing it accept fresh APPLY
   generation=previous+1 (including FF->00), ACK=previous, same token and own slot.
   RELEASE-before-APPLY is another observable boundary: host must not overwrite RELEASE with
   APPLY in the same unobserved frame. Before commit, B/timeout may cancel; use v8's inclusive
   3600-frame APPLY boundary and specify whether the consent RELEASE wait shares that budget
   (proposal: bounded total wait, like the vanilla flags path).
5. Fresh APPLY pickup ACK precedes the staged-input/count/snapshot checks. In a commit-disabled
   build, finish with result 1 just like the proposer. In an explicitly enabled commit build,
   call C5 once; map 0/1/2 by the mutation contract, retain no-escape holds for 0/2, and never
   redispatch a repeated generation. Exit closes the responder-owned text exactly once;
   there is no receptionist script to execute `endtext`. The proposer still returns to the
   timeout gate's redirect (`patch/polished/src/trade_gate.asm:37-50`).

Shared waits/exit must be reached with the original context-base SP (tail `jp`, not an
extra `call` frame). The publisher's own return address already accounts for its +2
offsets. An extra push across a shared wait silently retargets token/count/result/flags;
test the stack image at each transition, not only the final balanced SP.
(`patch/polished/src/trade_service.asm:234-248,275-320`.)

**Re-entry:** dispatcher guards include the nine-byte return-chain fingerprint, bank mapping,
script/battle/link/pause/menu/VBlank/map/step/event state and fresh PROMPT generation
(`patch/polished/src/trade_dispatch.asm:50-130`). Native menu/save/nested service DelayFrames
are not the original idle chain; pickup also makes gen==ACK. Keep both protections. Do not
set wLinkMode merely to fake admission or rely only on hInMenu. The bridge's plain dispatcher
call must stay at the same depth (`patch/polished/src/slink.asm:113-119`). A forged fresh
PROMPT during the held service must still fail its stack/engine guards.

**Host compatibility:** C3's `advertised=false`, proposer-only arm and DONE=1-only release
remain correct for the current build (`lua/gen2/polished_trade.lua:337-401`). Introduce a
separately gated, role/phase-aware host path: PROMPT/DONE0 => CONSENT (not COMPLETE),
APPLY/DONE1 => NOT_PERFORMED, APPLY/DONE2 or unknown => UNCERTAIN; APPLY/DONE0 => a new
completion disposition only with the enabled-commit provenance and independently observed
postconditions. Preserve all old negative tests on the commit-disabled cut. Do not blindly
change `result == 1` to accept 0 or infer enablement from helper symbol presence. Server
event/recovery semantics need their own reviewed host card; C4 intentionally supplies no
client pump (`lua/gen2/entry.lua:950-979`; `lua/gen2/client.lua:691-695,1508-1518,1904-1907`).

## 5. Space, stack and verification budget

The v8 map has dispatcher 4700..4779, PromptEntry 4780, proposer 4A00..4CEF, then free
4CF0..7FFF in bank 7E (`data/polished/polished_slink.map:3865-3889`). Reserve proposer
growth below its existing 5000 limit; place new C5/C6 bodies at/above 5000 with linker
ASSERTs and measured End symbols. These are proposed placement constraints, not permanent
literal addresses. Extend the builder's allowed extent and required-symbol census: it
currently stops at DispatchEnd/ProposerServiceEnd (`tools/build_polished_companion.py:219-234`).

No new WRAM/mailbox scratch: 69 mailbox bytes are already reserved, and OT slot 1 holds
the snapshot. Private service context remains 10 bytes. Bridge push AF/BC/HL plus saved-bank
AF is **8 bytes**, despite the stale four-byte comment (`patch/polished/src/slink.asm:47-64`).
The dispatcher adds its return/preserved-DE/call frames; a jump trampoline adds no new
return. Budget C5 locals explicitly (vanilla saves 6 register bytes +10 control bytes +2
role/count bytes, before native call depth), then measure the Polished-specific additions.
(`patch/gen2/src/trade_commit.asm:17-86`; `patch/polished/src/trade_dispatch.asm:125-130`.)

Proposed C5 explicit-local ceiling: **24 bytes**, excluding its return/native call frames:
6 saved-register bytes, 10 native-control bytes, 2 role/count, 2 private slot/alignment,
2 saved SVBK/VBlank, 2 reserve. This is a design budget, not measured safety. Restore
SVBK/VBlank to the qualified entry values and the caller's ROM bank; close SRAM. Any
larger layout requires a reviewed budget change rather than borrowing mailbox bytes.

Real stack endpoints are C000/C0FF (`data/polished/polished_slink.sym:63638-63640`). Run the
SM83 calls with an explicit realistic SP as well as sentinel-depth checks; its default
D000 is not stack-headroom evidence. Interrupt and SRAM/WRAM banking behavior are absent
from the model (`tests/unit/polished_sm83.py:13-23,39,738`). **UNVERIFIED** maximum native
UI/save/evolution depth and ISR margin: live high-water measurement on both roles is a
hard enablement prerequisite, especially during nested DelayFrame calls.

## 6. Cards, ownership and acceptance

Each row is a proposed exclusive lease of at most three files; no worker may overlap the
service, builder or generated-output owner. Test file names below are proposals, not existing
evidence. C5/C6 authoring may run in parallel only for their separate new asm/test pairs.

| Order/card | Exclusive files (repository-relative) | Acceptance / first falsifier |
|---|---|---|
| C5-A: helper, unwired | `patch/polished/src/trade_commit.asm`; `tests/unit/test_polished_trade_commit.py` | `python -m pytest -q tests/unit/test_polished_trade_commit.py` once linked; wrong count or temp-name source must fail before a success result. Export real start/End markers; no enabled call site. |
| C6-A: responder, commit-disabled | `patch/polished/src/trade_responder.asm`; `tests/unit/test_polished_trade_responder.py` | `python -m pytest -q tests/unit/test_polished_trade_responder.py` once linked; repeated PROMPT must open one menu only; consent may not append or publish APPLY success. |
| I1: common ROM wiring owner | `patch/polished/src/slink.asm`; `patch/polished/src/trade_dispatch.asm`; `tools/build_polished_companion.py` | `python tools/build_polished_companion.py --version 0.1.0` in the isolated build lane; linker overlap or an unexpected changed ROM span fails. Include new bodies, route the fixed trampoline, extend symbol/span verification. |
| I2: service phases and gated call site | `patch/polished/src/trade_service.asm`; `patch/polished/src/trade_commit.asm`; `tests/unit/test_polished_trade_service.py` | `python -m pytest -q tests/unit/test_polished_trade_service.py tests/unit/test_polished_trade_commit.py tests/unit/test_polished_trade_responder.py`; old commit-disabled scenarios must still never produce result0. Serial after C5-A; owner GO required before an enabled commit cut. |
| H1: phase-aware dev host | `lua/gen2/polished_trade.lua`; `tests/unit/test_polished_trade_binder.py`; `tests/unit/test_polished_trade_composition.py` | `python -m pytest -q tests/unit/test_polished_trade_binder.py tests/unit/test_polished_trade_composition.py`; a PROMPT consent misclassified as COMPLETE is the first falsifier. No production hello flip. |
| H2: separately authorized pump seam | `lua/gen2/client.lua`; `lua/gen2/entry.lua`; `tests/unit/test_polished_trade_pump.py` | `python -m pytest -q tests/unit/test_polished_trade_pump.py`; absent dev capability must leave vanilla/default graphs and hello unchanged, and result2 must not emit completion or release. Owner GO for shared-client work; serial after H1, before a server-driven duo. Any server/protocol change needs another exclusive lease, not a fourth file here. |
| P1: serverless evidence driver | `tools/polished_live/trade_service_probe.lua`; `tools/polished_live/trade_service_probe.py`; `tests/unit/test_polished_trade_service_probe.py` | `python -m pytest -q tests/unit/test_polished_trade_service_probe.py`; forged/missing ACK, wrong phase or missing final event must not pass. Live run is separately lane-authorized. |

C5-A/C6-A can report **source-ready**, but their MODEL acceptance is pending until I1
produces the actual linked cut. A present stale ROM missing the new symbols must fail,
not skip or satisfy a fabricated routine trap. Keep the integration cut unqualified
until all three suites execute its rebuilt bytes.

Generated artifacts are **not silently outside these leases**. After each ROM source cut,
the coordinator freezes it and assigns serial regeneration batches of at most three files:
UPS/sym/map; overlay provenance/beacon/profile; engine sites/checkpoint and their pins;
then remaining verifier/probe SHA pins found by an exact old-SHA census. Re-run generators'
`--check` and the existing companion/profile/service tests. The builder writes more than
three outputs, so its live invocation requires an explicitly enumerated build-output lease;
the I1 source author cannot run it under a source-only three-file lease. Current provenance
enumerates the input/source hashes (`tools/build_polished_companion.py:395-420`), and profile
generation derives the present trade facts (`tools/gen_polished_profile.py:248-371`).

### SM83 oracle requirements

- Execute rebuilt helper/service bytes, not a Python replacement of their branch decisions.
  Trap native UI/animation/save only when necessary, and label the trapped claim MODEL.
  Exercise real removal/temp-copy/append and evolution subroutines where the machine permits;
  banking-dependent mail/save paths require a banking-capable harness or explicit OPEN/skip.
  Existing service rig already distinguishes native traps and CPU/host writes
  (`tests/unit/test_polished_trade_service.py:1-18`; `tests/unit/polished_sm83.py:13-23`).
- Per-branch write whitelist: early refusal = stack/lease plus declared UI/snapshot only;
  consent normalization = exactly the native EV/stat/HP changes; pre-save consent = native
  save domains, no party replacement; commit = controlled party/name/dex,
  native mail rotation, animation/temp/evolution scratch and the separately enumerated native
  save domains. Never whitelist all SRAM/WRAM to make a failing test pass. Distinguish native
  full-save storage backup writes from forbidden box-entry insertion/allocation.
- Test first/middle/last slots and counts 1/6; incoming/outgoing mail refusal; unrelated mail
  alignment; three DVs, extended and regional forms; OT metadata; nicknamed/unnicknamed;
  legal eggs only under the chosen policy; Everstone, Linking Cord and held-item evolution;
  final count/order/dex/item/name/HP changes and all 70 snapshot-byte flips.
- Fault/mutant matrix: count decrement/append failure; stale temp OT/nick; dropped form/DV;
  TRUE instead of EVOLVE_TRADE; double mail shift; ignored snapshot/token/slot; missing full
  save; result0 before save; B/timeout/RELEASE escaping result2; unconsumed consent RELEASE;
  fresh PROMPT during every nested native wait; stack push/pop imbalance. A wrong-state trap
  must not return a manufactured success. Preserve v8 final-frame and host frame-gap controls.

### Live progression and owner gates

1. Freeze the rebuilt overlay. Serverless, one-instance native proposer and responder
   prompts first: reject/decline/save-decline/timeouts, both real UI return paths, caller
   SP/bank and hVBlank/link/pause restoration, no duplicate pickup. Then explicitly authorized
   commit-enabled dev probes on private saves: animation, append, evolution and save. A
   lease response alone is not proof of any native mutation or durability.
2. Two distinct trainer identities and independently provisioned saves, real host/server,
   hello+census before a trade. Record the same token/roles, each local frozen preimage,
   consent RELEASE consumption, both APPLY pickups, native return/save and resulting
   identities. One-sided completion/disconnect stays UNCERTAIN; no blind resend of a
   committed request. The C4 dev-only graph is not yet this harness/pump.
3. Cold reload both sides and the interruption matrix in §3, including unrelated mail and
   nonempty boxes. Keep failed/partial receipts and exact ROM/source/fixture hashes. Do not
   upgrade previous stub/v6 dispatcher evidence to this changed call chain
   (`docs/polished/TRADE.md:1383-1385,1413`).

Owner decisions: enablement of any commit-capable build/live mutation; egg/form/last-usable
mon policy and full post-save choice; the phase-aware host recovery/completion contract;
production capability/hello admission and final landing. Shared server/protocol edits, if
required by H1's follow-on pump/recovery work, get their own owner GO and Gen-3-first then
other-generation regression pass (`docs/polished/BOX_WRITE_CONTRACT.md:68-69`). No push,
master merge or release claim follows from
these source/model cards. The three questions at the top remain OPEN until their named
source/model/live/cold-load evidence and owner decisions are recorded.

## Native-source fact check (2026-10-07, headless Codex magi-89038145; coordinator spot-checked #1 and #5)
Source: pinned Polished tree 3fa43192. 43 claim groups: 39 verified, 2 misleading, 2 unverifiable, 0 false. Corrections and additions the C5 card MUST absorb:
1. **"Usable" is not "nonzero HP".** `CheckAnyOtherAliveMonsForTrade` (`engine/link/link.asm:1219-1251`) only ORs the HP words of the other local slots and the incoming OT slot: no egg, species or stat validation. An incoming egg with forged HP satisfies it. The incoming-domain policy (eggs, stats, forms) stays an OPEN owner/validator gate.
2. **Save verification boundary.** The Pokemon-data checksums do not cover options, save phase, mail or box metadata (`ram/sram.asm:15-34,44-61`; `engine/menus/save.asm:320-330,374-384`). A valid-primary load ALWAYS rewrites the backup (`WriteBackupSave`) and the backup fallback calls `SaveGameData` (`ram/sram.asm:86-111,138-147`; `save.asm:403-429`): the interruption matrix must include those recovery writes. Readback of the Pokemon span proves nothing about mail/storage integrity.
3. **Evolution write set** also includes learned-move PP, evolved dex flags and dex-cache invalidation (`wDexCacheValid`), and can open the move-replacement UI (`engine/pokemon/evolve.asm:489-499,575-636,642-695`; `learn.asm:14-37,75-100`; `home/pokedex_flags.asm:89-131`).
4. **Full-save scope** also writes current-map data, clears `sBattleTowerChallengeState`, sets validity markers/version, stages RTC and temporarily changes `hVBlank` (`save.asm:148-213,275-308,332-363`). The storage backup copies box entries, bank flags, names and themes, NOT a second copy of the Pokemon database records (`save.asm:224-234`; `ram/sram.asm:138-157`).
5. **Restore map music.** Trade animation starts evolution music; evolution stops it and its link-mode return skips `RestartMapMusic`; `ReturnToMapWithSpeechTextbox` does not restart it; the native NPC trade calls `RestartMapMusic` explicitly (`engine/events/npc_trade.asm:47-54`; `trade_animation.asm:130-144`; `evolve.asm:58-71,440-443`; `home/map.asm:1597-1622`). C5's exit must restore it.
6. **NPC trading is a different construction path** (`DoNPCTrade` before the animation, `TryAddMonToParty`, caught-data, stat/HP recompute, no `AddTempMonToParty`, no trade evolution, no save): never transplant its steps into a staged-record exchange (`npc_trade.asm:41-72,153-263`).
7. Selecting the LAST party slot performs zero mail swaps in removal (`bills_pc.asm:617-619`); the removal's SRAM mail rotation already exists, so C5 must not add a second shift/exchange (`bills_pc.asm:263-297,615-623`).
8. `0A:52DE` (Player2) holds only for the current symbol file; re-derive from the rebuilt candidate's sym.
