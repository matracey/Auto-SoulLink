# Gen 2 coverage map — concrete planned bindings

This P2 binding maps **103 of 103 obligations**: 53 requirements and 50 protocol section 9 assertions, including 38a/38b. There are **zero UNMAPPED** rows. All six future overlay/ghost target slots remain explicitly PLANNED and unhashed. Every evidence cell remains OPEN. Mapping a scenario does not claim that its implementation exists, its lane ran, its artifact is admitted, or its behavior passed.

Bindings are authored by the team from PLAN, GEN2_BINDING_PLAN section 5 and its row joins, the requirements, protocol section 9, inspected P2 tools/tests and the runner manifest. Routine mapping is not an owner hand-entry prerequisite.

## Binding interpretation

* The marked JSON block is authoritative. Each MAPPED row specifies stimulus, artifact scope, positive/refusal controls, independent oracle, lane and receipt marker.
* PLANNED BINDING is an executable-scenario specification, not an execution result. EXISTS identifies inspected source/model tools; PLANNED filenames remain phase targets without a claim of present implementation. Native flows and fixtures are unqualified.
* SOURCE-only: F-1/F-4/F-5/F-7g. MODEL-only by design: C-0/C-4/D-13. Their declared layers are unchanged. All 50 protocol assertions retain the explicit SOURCE+PHYSICAL caller policy; production-graph fake-server observations remain supplementary MODEL.
* NATURAL engine scenarios and COMMAND executor scenarios remain separate. SOURCE and MODEL stimuli do not qualify physical behavior. C-6g/D-1 explicitly include C-G link alongside C-C/G-S representatives.
* Plain clean digests are selected source candidates, not runtime admission. Crystal1.1 remains build-only. P4/P5 targets use separate descriptors; their unknown digests are null, never substituted with base-ROM hashes.
* Artifact scope is independently injected through the runner's explicit target declarations and obligation bindings. Removing or changing a target inside this map cannot grant evidence eligibility. CLOSED evidence on a PLANNED target refuses in every mode/layer.
* P4 reopens affected source/site/checkpoint/admission/natural-rule receipts on the actual overlay. Reuse requires byte/offset AND reachability-context equivalence. Complete runtime/client/profile/fixture fingerprints remain phase evidence prerequisites.

## Conditional and phase applicability

W-3/W-4/C-5/D-11 remain default-disabled/deferred without an enabling ruling. Their planned bindings specify refusal/disposition controls and enabled-branch prerequisites. N-3 remains post-RC under O-13, after G6 and authorized design. OPEN cells do not fabricate signed applicability records or behavior passes.

The raw inventory's 95 declared PHYSICAL cells span all phases plus conservative protocol policy; they are not a first-release required count. GEN2_BINDING_PLAN section 5.B identifies 41 first-G6 requirement-level physical obligations with the four conditionals disabled and N-3 post-RC. D-12 needs its own GAME transient receipt or signed recorded limit. The future P6 release-evidence binder must evaluate those signed applicability/disposition records; raw all-phase closure mode does not replace it.

## Current inputs and artifact policy

The coordinator integrated docs/gen2/gen2_requirements.md byte-for-byte from the reviewed planning tree. Requirements, protocol and source lock are now all present in this implementation tree. Their raw hashes were not changed by this task.

| Input | SHA256 |
|---|---|
| requirements | 7f29009566cf39af96650f9a1671d5d5bc11cdc98b1ba19e61536f57844c4f76 |
| protocol | a5e072c4d112c29258a91c03a4e208a6caf1a2ca0db1cd3b5dc695ad1d499c40 |
| artifact_policy | 113b41354f65a9859bf878cedb334de27e0c9e3ff0f2cc13693889dfd524a3ed |

| Current clean source candidate | SHA1 |
|---|---|
| pokecrystal | f4cd194bdee0d04ca4eac29e09b8e4e9d818c133 |
| pokegold | d8b8a3600a465308c9953dfa04f0081c05bdcb94 |
| pokesilver | 49b163f7e57702bc939d642a18f591de55d92dae |

| Planned target | Known lineage anchor | State | Target digest |
|---|---|---|---|
| crystal_overlay | pokecrystal | PLANNED | null |
| crystal_ghost | pokecrystal | PLANNED | null |
| gold_overlay | pokegold | PLANNED | null |
| gold_ghost | pokegold | PLANNED | null |
| silver_overlay | pokesilver | PLANNED | null |
| silver_ghost | pokesilver | PLANNED | null |

The base anchors an already built source lineage, not an assertion of the target's complete eventual build manifest or immediate patch parent. Actual overlay/ghost builds later supply BUILT descriptors, real target digests, full provenance and new matching receipts. Current planned-target receipts can never transfer to that new mapping.

## F-3 — per-family differential witness design

The owner contract remains **ENGINE + PYDEC/GAME (method: differential gate across titles)**. Every family below requires ENGINE occurrence at its qualified CPU site. PYDEC is selected for independently measurable party/box/identity/bag/save byte effects; GAME is selected where native engine, UI or control state is the observation. These are concrete test-design choices within the existing slash policy, not a new universal ENGINE+PYDEC+GAME requirement, a normative rewrite, or an exemption. All scenarios and receipts remain PLANNED/OPEN.

Common ENGINE control: record title, artifact, routine/site, bank, PC/caller, frame and load-verified expected bytes; reject a missing family/title, wrong-bank/caller event, or script-bytecode substitute. The family-specific positive/refusal controls and independent observations are below. Source-site presence and a callback alone do not satisfy the independent witness.

Phase/lane: P2.2/P3b.4 / live-new-gates. Planned implementation: tests/live/test_gen2_new_gates.py signal-family rows, consuming independent gen2_codec bytes or native GAME observations. Aggregate receipt marker: gen2.requirement.F-3; family submarkers append the stable family name. The actual physical gate must account for every family on every selected title.

| Family | Positive stimulus/control | Refusal control | Selected independent witness |
|---|---|---|---|
| battle_start_wild | Walk into a source-supported wild encounter on each selected title. Wild battle entry occurs once with the actual species and battle UI visible. | A trainer encounter or ordinary overworld step must not satisfy the wild-start subclaim. | GAME: Native wild-battle screen/state, compared with the routine's independently captured ENGINE occurrence. |
| battle_start_trainer | Accept a known trainer challenge on each selected title. Trainer battle entry occurs once with the native trainer identity/display. | A wild battle or canceled/unreached challenge must not satisfy trainer-start. | GAME: Native trainer introduction and battle state; no PYDEC party-mutation witness is required for this control-state family. |
| battle_end_result | Naturally win, flee and take the permitted losing/recovery route from completed battles. The battle-end occurrence agrees with the observed exit/outcome transition. | An in-battle turn or unrelated transient result-bit value must not be accepted as battle completion. | GAME: Native battle exit/result/recovery state; transient wBattleResult bits alone are not the oracle. |
| capture_party | Catch a wild mon with a free party slot through the native capture path. The routine occurrence accompanies one newly decoded final party record and count/slot change. | A failed throw or full-party box destination must not satisfy party-capture. | PYDEC: Independent same-frame party/count/name bytes before and after insertion; identity comes from the final acquired record. |
| capture_box | With party full, catch into a non-final box slot and into slot20. Each successful SendMonIntoBox occurrence accompanies the independently decoded inserted box record. | A failed throw, full-box refusal or party-space catch must not satisfy box-insertion; box-full flag alone is insufficient. | PYDEC: Independent active-box count, inserted record/name and relevant backing bytes with frame/domain recorded. |
| player_faint | Naturally faint the active player mon by a battle action. The player-faint occurrence corresponds to the same identified party record changing from HP>0 to0. | Opponent faint or an already-zero record must not satisfy natural player-faint. The discriminator against client-command HP zeroing is the absence of any SLink write-permit receipt (W-1 provenance) touching that party record's HP or wBattleMonHP inside the occurrence window; today lua/gen2/writes.lua structurally refuses to zero the active battler (faint_active_battler always refuses, and faint_party_slot refuses the active slot), so the commanded-zero case cannot currently arise. | PYDEC: Independent pre/post party HP and full record identity captured before any healing. |
| poison_faint | Walk with an eligible poisoned party mon until native overworld poison reduces its HP to0. DoPoisonStep's faint occurrence corresponds to that exact record's HP transition. | A nonterminal poison tick, a healthy mon or an already-zero record must not satisfy poison-faint. | PYDEC: Independent step-aligned party HP/status/identity bytes; no inferred death from a later healed snapshot. |
| whiteout | Naturally lose the last previously alive party mon and observe the native whiteout/recovery flow. The qualified CPU entry occurs before HealParty and corresponds to the game's whiteout/recovery state. | A surviving party member, loaded all-zero party or unrelated healing/menu path must not satisfy whiteout. | GAME: Native whiteout/recovery transition, with ENGINE establishing the required pre-HealParty occurrence; record-level faint claims remain the separate faint families. |
| evolution_publish | Finish an eligible natural evolution, and separately cancel one before publication. Completed evolution occurrence corresponds to the same mon's final species/key change. | Canceled evolution or pre-publication animation must leave the prior identity and cannot satisfy publication. | PYDEC: Independent old/final party species and identity bytes from the actual publication slot. |
| npc_trade | Complete a native NPC trade with distinguishable offered/received records. Final trade occurrence corresponds to the received record in its post-compaction/final slot. | Decline or fail prerequisites; a pre-compaction slot or a same-species substitute cannot satisfy the record observation. | PYDEC: Independent before/after party records, OT/name/identity and final slot layout. |
| link_trade | Complete the phase-qualified native link-trade path with distinguishable records on both sides. The link-trade engine occurrence agrees with the actually received record on each cartridge. | Decline/interruption before commit or a wrong received identity must not satisfy successful link-trade. | PYDEC: Independent final party identities on both sides; save durability is evaluated by save/reload families and T-4 rather than inferred from the routine return. |
| pc_deposit | Use the native PC to deposit an eligible party mon. Deposit occurrence corresponds to its removal from party and insertion into the active box. | Cancel the menu or only view a box; neither may satisfy deposit. | PYDEC: Independent party/box counts and exact moved record/name before and after the operation. |
| pc_withdraw | Use the native PC to withdraw an eligible boxed mon. Withdrawal occurrence corresponds to that record leaving the box and entering party. | Cancel or attempt withdrawal with full party; neither may satisfy successful movement. | PYDEC: Independent active-box and party records/counts, including compaction and destination slot. |
| pc_release | Confirm release of an eligible boxed mon using the native PC. Release occurrence corresponds to removal of that exact record and expected surviving-slot compaction. | Cancel the confirmation or inspect another record; unchanged storage cannot satisfy release. | PYDEC: Independent box record/name inventory before and after confirmed release; protocol handling of linked release is a separate S-6 obligation. |
| pc_changebox | Select another box through the native ChangeBox flow and return to PC. The ChangeBox occurrence agrees with the active box identified by the game's own header/selection. | Cancel ChangeBox or revisit the same view without a change; neither satisfies a new box-selection transition. | GAME: Native PC active-box header/selection state; storage persistence is separately checked where promised. |
| map_load | Walk/warp through a known source-defined map boundary on each title. Map-load occurrence corresponds to the game's new map/scene presentation. | A same-map step or unrelated fade/menu must not satisfy a new map load. | GAME: Native map/scene transition and known destination presentation, compared across title-specific source map facts. |
| ball_received | Receive Poké Balls through the native grant or purchase path with Ball-pocket capacity. The bag-received occurrence agrees with the independently decoded Ball-pocket item/count delta. | No-capacity/declined grant or a non-ball item change must not satisfy ball-received. | PYDEC: Independent Ball-pocket bytes/counts; O-10 fixture injection is not a natural ball-acquisition witness. |
| save_success | Complete an in-game SAVE and separately cancel/refuse overwrite. The success-only save occurrence agrees with the resulting independently decoded CartRAM save record/checksum layout. | Save-entry-only occurrence, canceled overwrite or unchanged stale witness must not satisfy successful save. | PYDEC: Independent success-boundary CartRAM bytes and per-title save/checksum decoding; full cold-reload GAME qualification remains F-6/T-4 where declared. |
| continue | Cold-boot a qualified existing save and choose CONTINUE. The load occurrence agrees with native CONTINUE reaching that saved game state. | Title idle, canceled selection or rejected/both-bad save recovery must not satisfy successful CONTINUE. | GAME: Native load/CONTINUE outcome; distinguish this path from New Game without treating an entry call as load success. |
| new_game | Choose New Game through its native confirmation/naming/start flow. The new-game occurrence agrees with the game's fresh initialization flow. | Decline overwrite or choose CONTINUE; the retained prior run cannot satisfy New Game. | GAME: Native new-game confirmation/start/control state, including the difference from the loaded-save path. |
| soft_reset | Perform the supported native soft-reset input and observe return to reset/title flow. The reset occurrence agrees with the game's reset/title transition. | Ordinary title/menu visits or unrelated input must not satisfy a reset; stale in-flight lifecycle state is a separate R-4/C-2 refusal. | GAME: Native transition from an in-game (overworld/menu/battle) state into the reset/title flow, observed natively with an independent frame/ENGINE occurrence; a reset/title-state snapshot alone, without that preceding in-game state, does not satisfy the witness. |
| egg_hatch | Naturally hatch an existing egg through the game's hatch sequence. Hatch publication occurrence agrees with the independently decoded hatchling record/key. | GiveEgg alone or pre-hatch steps must not satisfy hatch publication. | PYDEC: Independent egg marker to final species/identity transition at the actual party slot; gift_daycare consumption is separately observed by S-8/D-1. |

Saved-record durability remains scoped to the requirements that promise it (for example F-6 and T-4); a LinkTrade return or ENGINE occurrence is never promoted into a reload witness. O-10 fixture Ball-pocket injection does not qualify the natural ball-received family. Native link/PC/acquisition flows and all fixtures remain unqualified until their lanes run.

The JSON F-3 mapping repeats the family stimuli, controls and method selections, so its mapping digest binds these choices; the table is an index, not an unbound substitute for machine-readable policy.
## Obligation index

| Obligation | Required layers | Mapping | Phase | Lane |
|---|---|---|---|---|
| requirement:F-1 | SOURCE | MAPPED | P1.1/P2.1 | profile-generated-crystal |
| requirement:F-2 | SOURCE + PHYSICAL | MAPPED | P2.2/P3b.4/P4.4 | live-new-gates |
| requirement:F-3 | SOURCE + PHYSICAL | MAPPED | P2.2/P3b.4 | live-new-gates |
| requirement:F-4 | SOURCE | MAPPED | P2.4 | rom-layout |
| requirement:F-5 | SOURCE | MAPPED | P2.1 | species-generated |
| requirement:F-6 | SOURCE + PHYSICAL | MAPPED | P3b.1/P3b.2/P3b.7/P3b.8 | fixtures |
| requirement:F-7g | SOURCE | MAPPED | P1.3/P2.5 | admission-generated |
| requirement:R-1 | SOURCE + PHYSICAL | MAPPED | P3b.1/P3b.3/P3b.3a | live-new-gates |
| requirement:R-2 | SOURCE + PHYSICAL | MAPPED | P3b.1/P3b.3a | live-new-gates |
| requirement:R-3 | SOURCE + PHYSICAL | MAPPED | P3b.3a | live-new-gates |
| requirement:R-4 | SOURCE + PHYSICAL | MAPPED | P2.3/P3b.5/P3b.6 | live-new-gates |
| requirement:R-5g | SOURCE + PHYSICAL | MAPPED | P3b.3a | live-new-gates |
| requirement:S-1 | SOURCE + PHYSICAL | MAPPED | P3b.4 | live-new-gates |
| requirement:S-2 | SOURCE + PHYSICAL | MAPPED | P3b.4/P3b.7 | live-new-gates |
| requirement:S-3 | SOURCE + PHYSICAL | MAPPED | P3b.4/P3b.7 | live-new-gates |
| requirement:S-4 | SOURCE + PHYSICAL | MAPPED | P3b.4 | live-new-gates |
| requirement:S-5 | SOURCE + PHYSICAL | MAPPED | P3b.4/P3b.7 | live-new-gates |
| requirement:S-6 | SOURCE + PHYSICAL | MAPPED | P3b.4/P3b.7 | live-new-gates |
| requirement:S-7 | SOURCE + PHYSICAL | MAPPED | P3b.2/P3b.4/P3b.7 | live-new-gates |
| requirement:S-8 | SOURCE + PHYSICAL | MAPPED | P2.5/P3b.4/P3b.7 | live-new-gates |
| requirement:S-9g | SOURCE + PHYSICAL | MAPPED | P2.5/P3b.4/P3b.7 | duo-pairs |
| requirement:S-10g | SOURCE + PHYSICAL | MAPPED | P2.5/P3b.4/P3b.7 | duo-pairs |
| requirement:W-1 | SOURCE + PHYSICAL | MAPPED | P3b.5 | live-new-gates |
| requirement:W-2 | SOURCE + PHYSICAL | MAPPED | P3b.5/P3b.7 | live-new-gates |
| requirement:W-3 | SOURCE + PHYSICAL | MAPPED | P3b.5/P6.3 conditional | release-evidence |
| requirement:W-4 | SOURCE + PHYSICAL | MAPPED | P3b.5/P6.3 conditional | release-evidence |
| requirement:W-5 | SOURCE + PHYSICAL | MAPPED | P3b.5/P3b.7 | live-new-gates |
| requirement:W-6 | SOURCE + PHYSICAL | MAPPED | P2.3/P3b.5 | live-new-gates |
| requirement:W-7 | SOURCE + PHYSICAL | MAPPED | P3b.5/P3b.7 | duo-pairs |
| requirement:C-0 | MODEL | MAPPED | P2.6/P3a.2/P3b.6 | unit |
| requirement:C-1 | SOURCE + PHYSICAL | MAPPED | P1.3/P3b.3/P3b.7 | live-new-gates |
| requirement:C-2 | SOURCE + PHYSICAL | MAPPED | P3b.6/P3b.7 | duo-pairs |
| requirement:C-3 | SOURCE + PHYSICAL | MAPPED | P3b.6/P4.1 | live-gates |
| requirement:C-4 | MODEL | MAPPED | P2.6/P3b.6 | unit |
| requirement:C-5 | SOURCE + PHYSICAL | MAPPED | P3b.3/P6.3 conditional | release-evidence |
| requirement:C-6g | SOURCE + PHYSICAL | MAPPED | P3a.1/P3b.7 | duo-pairs |
| requirement:D-1 | SOURCE + PHYSICAL | MAPPED | P2.5/P3b.7 | duo-pairs |
| requirement:D-2 | SOURCE + PHYSICAL | MAPPED | P3b.7 | duo-pairs |
| requirement:D-3 | SOURCE + PHYSICAL | MAPPED | P3b.7 | duo-pairs |
| requirement:D-5 | SOURCE + PHYSICAL | MAPPED | P3b.7 | duo-pairs |
| requirement:D-6 | SOURCE + PHYSICAL | MAPPED | P3b.7 | duo-pairs |
| requirement:D-7 | SOURCE + PHYSICAL | MAPPED | P3b.7 | duo-pairs |
| requirement:D-11 | SOURCE + PHYSICAL | MAPPED | P3b.7/P6.3 conditional | release-evidence |
| requirement:D-12 | SOURCE + PHYSICAL | MAPPED | P3b.6/P3b.7/P6.3 | duo-pairs |
| requirement:D-13 | MODEL | MAPPED | P2.6/P3b.6 | unit |
| requirement:D-14 | SOURCE + PHYSICAL | MAPPED | P3b.7 | duo-pairs |
| requirement:T-1 | SOURCE + PHYSICAL | MAPPED | P4.3 | live-trade-gates |
| requirement:T-2 | SOURCE + PHYSICAL | MAPPED | P4.3 | live-trade-gates |
| requirement:T-3 | SOURCE + PHYSICAL | MAPPED | P4.3 | live-trade-gates |
| requirement:T-4 | SOURCE + PHYSICAL | MAPPED | P4.3 | live-trade-gates |
| requirement:N-1 | SOURCE + PHYSICAL | MAPPED | P4.1 | live-gates |
| requirement:N-2 | SOURCE + PHYSICAL | MAPPED | P4.2 | live-gates |
| requirement:N-3 | SOURCE + PHYSICAL | MAPPED | P5.1/P5.2/P5.3 after G6 | release-evidence |
| protocol:9.1 | SOURCE + PHYSICAL | MAPPED | P3a.2/P3b.3a/P3b.6/P3b.7 | live-new-gates |
| protocol:9.2 | SOURCE + PHYSICAL | MAPPED | P3a.2/P3b.3a/P3b.6/P3b.7 | duo-pairs |
| protocol:9.3 | SOURCE + PHYSICAL | MAPPED | P3a.2/P3b.3a/P3b.6/P3b.7 | live-new-gates |
| protocol:9.4 | SOURCE + PHYSICAL | MAPPED | P3a.2/P3b.3a/P3b.6/P3b.7 | duo-pairs |
| protocol:9.5 | SOURCE + PHYSICAL | MAPPED | P3a.2/P3b.3a/P3b.6/P3b.7 | live-new-gates |
| protocol:9.6 | SOURCE + PHYSICAL | MAPPED | P3a.2/P3b.3a/P3b.6/P3b.7 | live-new-gates |
| protocol:9.7 | SOURCE + PHYSICAL | MAPPED | P3a.2/P3b.3a/P3b.6/P3b.7 | live-new-gates |
| protocol:9.8 | SOURCE + PHYSICAL | MAPPED | P3a.2/P3b.3a/P3b.6/P3b.7 | live-new-gates |
| protocol:9.9 | SOURCE + PHYSICAL | MAPPED | P3a.2/P3b.3a/P3b.6/P3b.7 | live-new-gates |
| protocol:9.10 | SOURCE + PHYSICAL | MAPPED | P3a.2/P3b.3a/P3b.6/P3b.7 | live-new-gates |
| protocol:9.11 | SOURCE + PHYSICAL | MAPPED | P3a.2/P3b.3a/P3b.6/P3b.7 | live-new-gates |
| protocol:9.12 | SOURCE + PHYSICAL | MAPPED | P3a.2/P3b.3a/P3b.6/P3b.7 | live-new-gates |
| protocol:9.13 | SOURCE + PHYSICAL | MAPPED | P3a.2/P3b.3a/P3b.6/P3b.7 | live-new-gates |
| protocol:9.14 | SOURCE + PHYSICAL | MAPPED | P3a.2/P3b.3a/P3b.6/P3b.7 | live-new-gates |
| protocol:9.15 | SOURCE + PHYSICAL | MAPPED | P3a.2/P3b.3a/P3b.6/P3b.7 | live-new-gates |
| protocol:9.16 | SOURCE + PHYSICAL | MAPPED | P3a.2/P3b.3a/P3b.6/P3b.7 | live-new-gates |
| protocol:9.17 | SOURCE + PHYSICAL | MAPPED | P3a.2/P3b.3a/P3b.6/P3b.7 | live-new-gates |
| protocol:9.18 | SOURCE + PHYSICAL | MAPPED | P3a.2/P3b.3a/P3b.6/P3b.7 | live-new-gates |
| protocol:9.19 | SOURCE + PHYSICAL | MAPPED | P3a.2/P3b.3a/P3b.6/P3b.7 | live-new-gates |
| protocol:9.20 | SOURCE + PHYSICAL | MAPPED | P3b.4/P3b.6/P3b.7 | duo-pairs |
| protocol:9.21 | SOURCE + PHYSICAL | MAPPED | P3b.4/P3b.6/P3b.7 | duo-pairs |
| protocol:9.22 | SOURCE + PHYSICAL | MAPPED | P3b.4/P3b.6/P3b.7 | duo-pairs |
| protocol:9.23 | SOURCE + PHYSICAL | MAPPED | P3b.4/P3b.6/P3b.7 | duo-pairs |
| protocol:9.24 | SOURCE + PHYSICAL | MAPPED | P3b.4/P3b.6/P3b.7 | duo-pairs |
| protocol:9.25 | SOURCE + PHYSICAL | MAPPED | P3b.4/P3b.6/P3b.7 | live-new-gates |
| protocol:9.26 | SOURCE + PHYSICAL | MAPPED | P3b.4/P3b.6/P3b.7 | duo-pairs |
| protocol:9.27 | SOURCE + PHYSICAL | MAPPED | P3b.4/P3b.6/P3b.7 | duo-pairs |
| protocol:9.28 | SOURCE + PHYSICAL | MAPPED | P3b.5/P3b.6/P3b.7 | live-new-gates |
| protocol:9.29 | SOURCE + PHYSICAL | MAPPED | P3b.5/P3b.6/P3b.7 | live-new-gates |
| protocol:9.30 | SOURCE + PHYSICAL | MAPPED | P3b.5/P3b.6/P3b.7 | live-new-gates |
| protocol:9.31 | SOURCE + PHYSICAL | MAPPED | P3b.5/P3b.6/P3b.7 | live-new-gates |
| protocol:9.32 | SOURCE + PHYSICAL | MAPPED | P3b.5/P3b.6/P3b.7 | live-new-gates |
| protocol:9.33 | SOURCE + PHYSICAL | MAPPED | P3b.5/P3b.6/P3b.7 | live-new-gates |
| protocol:9.34 | SOURCE + PHYSICAL | MAPPED | P3b.5/P6.3 conditional | release-evidence |
| protocol:9.35 | SOURCE + PHYSICAL | MAPPED | P3b.5/P3b.6/P3b.7 | duo-pairs |
| protocol:9.36 | SOURCE + PHYSICAL | MAPPED | P3b.4/P3b.6/P3b.7 | duo-pairs |
| protocol:9.37 | SOURCE + PHYSICAL | MAPPED | P3b.4/P3b.6/P3b.7 | duo-pairs |
| protocol:9.38 | SOURCE + PHYSICAL | MAPPED | P3b.4/P3b.6/P3b.7 | duo-pairs |
| protocol:9.38a | SOURCE + PHYSICAL | MAPPED | P3b.4/P3b.6/P3b.7 | duo-pairs |
| protocol:9.38b | SOURCE + PHYSICAL | MAPPED | P3b.4/P3b.6/P3b.7 | duo-pairs |
| protocol:9.39 | SOURCE + PHYSICAL | MAPPED | P4.3 | live-trade-gates |
| protocol:9.40 | SOURCE + PHYSICAL | MAPPED | P4.3 | live-trade-gates |
| protocol:9.41 | SOURCE + PHYSICAL | MAPPED | P4.3 | live-trade-gates |
| protocol:9.42 | SOURCE + PHYSICAL | MAPPED | P4.3 | live-trade-gates |
| protocol:9.43 | SOURCE + PHYSICAL | MAPPED | P4.3 | live-trade-gates |
| protocol:9.44 | SOURCE + PHYSICAL | MAPPED | P3b.4/P3b.6/P3b.7 | live-new-gates |
| protocol:9.45 | SOURCE + PHYSICAL | MAPPED | P3b.5/P6.3 conditional | release-evidence |
| protocol:9.45a | SOURCE + PHYSICAL | MAPPED | P3b.5/P6.3 conditional | release-evidence |
| protocol:9.46 | SOURCE + PHYSICAL | MAPPED | P3a.2/P3b.3a/P3b.6/P3b.7 | live-new-gates |
| protocol:9.47 | SOURCE + PHYSICAL | MAPPED | P3b.6/P4.1 | live-gates |

## Checks and explicit planned-target bindings

Schema and eligibility rules are in [shared-coverage-map.md](../shared-coverage-map.md). The registered runner now supplies these explicit flags; targets are never inferred from this map's own claims.

```text
python tools/coverage_map.py --map docs/gen2/gen2_coverage_map.md
  --requirements docs/gen2/gen2_requirements.md --protocol docs/protocol.md
  --protocol-section 9 --protocol-layers SOURCE PHYSICAL
  --artifact-policy data/gen2_sources.lock.json --artifact-collection outputs
  --artifact-digest-field sha1 --artifact-id pokecrystal --artifact-id pokegold
  --artifact-id pokesilver
  --planned-target crystal_overlay=pokecrystal
  --planned-target crystal_ghost=pokecrystal
  --planned-target gold_overlay=pokegold
  --planned-target gold_ghost=pokegold
  --planned-target silver_overlay=pokesilver
  --planned-target silver_ghost=pokesilver
  --target-binding requirement:C-3,requirement:T-1,requirement:T-2,requirement:T-3,requirement:T-4,requirement:N-1,requirement:N-2,protocol:9.39,protocol:9.40,protocol:9.41,protocol:9.42,protocol:9.43,protocol:9.47=crystal_overlay,gold_overlay,silver_overlay
  --target-binding requirement:N-3=crystal_ghost,gold_ghost,silver_ghost
  --mode mapping
```

Inventory and mapping modes validate the complete planned map. Closure mode still refuses the six unbuilt target slots and all OPEN evidence. A mapping pass establishes neither native behavior nor owner gate acceptance.

## Machine-readable coverage map

<!-- COVERAGE_MAP_START -->
```json
{
  "schema_version": 1,
  "input_sha256": {
    "requirements": "7f29009566cf39af96650f9a1671d5d5bc11cdc98b1ba19e61536f57844c4f76",
    "protocol": "a5e072c4d112c29258a91c03a4e208a6caf1a2ca0db1cd3b5dc695ad1d499c40",
    "artifact_policy": "113b41354f65a9859bf878cedb334de27e0c9e3ff0f2cc13693889dfd524a3ed"
  },
  "rows": [
    {
      "id": "requirement:F-1",
      "required_layers": [
        "SOURCE"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "SOURCE",
          "description": "PLANNED BINDING [P1.1/P2.1]. Regenerate each selected title's profile from its locked .sym/.map and compare every generated RAM/HRAM/SRAM/ROM field, retaining ROM and CGB bank metadata. EXISTS: tools/gen_gen2_profile.py; tests/unit/test_gen2_profile.py. Repeat the registered profile-generated-gold and profile-generated-silver lanes."
        },
        "artifacts": {
          "pokecrystal": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133",
          "pokegold": "d8b8a3600a465308c9953dfa04f0081c05bdcb94",
          "pokesilver": "49b163f7e57702bc939d642a18f591de55d92dae"
        },
        "positive_control": "Crystal, Gold and Silver --check outputs match their own symbols; Gold/Silver WRAM equality is independently derived.",
        "refusal_control": "Alter one symbol address, bank, or profile value; the relevant --check must refuse without rewriting outputs.",
        "oracle": "Pinned pret symbols and independently addressed ROM bytes; no running-cartridge claim.",
        "receipt_marker": "gen2.requirement.F-1",
        "lane": "profile-generated-crystal"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "No coverage receipt matching this planned binding, current input/artifact pins and declared layer has been accepted."
        }
      }
    },
    {
      "id": "requirement:F-2",
      "required_layers": [
        "SOURCE",
        "PHYSICAL"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "NATURAL",
          "description": "PLANNED BINDING [P2.2/P3b.4/P4.4]. On each selected running cartridge, load all hook anchors and naturally enter each qualified CPU routine; record bank/PC/frame and observed instruction bytes before callbacks. EXISTS: tools/gen_gen2_engine_signals.py; tests/unit/test_gen2_engine_sites.py; tools/verify_gen2_rom_layout.py. PLANNED: tests/live/test_gen2_new_gates.py."
        },
        "artifacts": {
          "pokecrystal": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133",
          "pokegold": "d8b8a3600a465308c9953dfa04f0081c05bdcb94",
          "pokesilver": "49b163f7e57702bc939d642a18f591de55d92dae"
        },
        "positive_control": "Every declared clean-ROM anchor matches its current pack and fires only in its intended bank/context.",
        "refusal_control": "Corrupt one expected_hex byte or offer a script-bytecode label; entry/hook registration must refuse before events or writes.",
        "oracle": "ENGINE: independent bus-exec and cartridge-byte receipt; SOURCE pack verification is separate.",
        "receipt_marker": "gen2.requirement.F-2",
        "lane": "live-new-gates"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "No coverage receipt matching this planned binding, current input/artifact pins and declared layer has been accepted."
        },
        "PHYSICAL": {
          "status": "OPEN",
          "reason": "PLANNED cartridge scenario has not run or qualified; no physical receipt is claimed."
        }
      }
    },
    {
      "id": "requirement:F-3",
      "required_layers": [
        "SOURCE",
        "PHYSICAL"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "NATURAL",
          "description": "PLANNED BINDING [P2.2/P3b.4; tests/live/test_gen2_new_gates.py]. On each selected title, execute the following 22 natural signal families through qualified scripted play. ENGINE occurrence is captured for every family at its source-qualified CPU site with expected bytes, bank/PC/caller and frame. Family stimuli: battle_start_wild: Walk into a source-supported wild encounter on each selected title.; battle_start_trainer: Accept a known trainer challenge on each selected title.; battle_end_result: Naturally win, flee and take the permitted losing/recovery route from completed battles.; capture_party: Catch a wild mon with a free party slot through the native capture path.; capture_box: With party full, catch into a non-final box slot and into slot20.; player_faint: Naturally faint the active player mon by a battle action.; poison_faint: Walk with an eligible poisoned party mon until native overworld poison reduces its HP to0.; whiteout: Naturally lose the last previously alive party mon and observe the native whiteout/recovery flow.; evolution_publish: Finish an eligible natural evolution, and separately cancel one before publication.; npc_trade: Complete a native NPC trade with distinguishable offered/received records.; link_trade: Complete the phase-qualified native link-trade path with distinguishable records on both sides.; pc_deposit: Use the native PC to deposit an eligible party mon.; pc_withdraw: Use the native PC to withdraw an eligible boxed mon.; pc_release: Confirm release of an eligible boxed mon using the native PC.; pc_changebox: Select another box through the native ChangeBox flow and return to PC.; map_load: Walk/warp through a known source-defined map boundary on each title.; ball_received: Receive Poké Balls through the native grant or purchase path with Ball-pocket capacity.; save_success: Complete an in-game SAVE and separately cancel/refuse overwrite.; continue: Cold-boot a qualified existing save and choose CONTINUE.; new_game: Choose New Game through its native confirmation/naming/start flow.; soft_reset: Perform the supported native soft-reset input and observe return to reset/title flow.; egg_hatch: Naturally hatch an existing egg through the game's hatch sequence."
        },
        "artifacts": {
          "pokecrystal": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133",
          "pokegold": "d8b8a3600a465308c9953dfa04f0081c05bdcb94",
          "pokesilver": "49b163f7e57702bc939d642a18f591de55d92dae"
        },
        "positive_control": "Every family needs its qualified ENGINE occurrence plus the selected independent PYDEC/GAME observation across all three titles; the selections below implement the existing alternative by signal family, not a new universal conjunction. battle_start_wild: Wild battle entry occurs once with the actual species and battle UI visible.; battle_start_trainer: Trainer battle entry occurs once with the native trainer identity/display.; battle_end_result: The battle-end occurrence agrees with the observed exit/outcome transition.; capture_party: The routine occurrence accompanies one newly decoded final party record and count/slot change.; capture_box: Each successful SendMonIntoBox occurrence accompanies the independently decoded inserted box record.; player_faint: The player-faint occurrence corresponds to the same identified party record changing from HP>0 to0.; poison_faint: DoPoisonStep's faint occurrence corresponds to that exact record's HP transition.; whiteout: The qualified CPU entry occurs before HealParty and corresponds to the game's whiteout/recovery state.; evolution_publish: Completed evolution occurrence corresponds to the same mon's final species/key change.; npc_trade: Final trade occurrence corresponds to the received record in its post-compaction/final slot.; link_trade: The link-trade engine occurrence agrees with the actually received record on each cartridge.; pc_deposit: Deposit occurrence corresponds to its removal from party and insertion into the active box.; pc_withdraw: Withdrawal occurrence corresponds to that record leaving the box and entering party.; pc_release: Release occurrence corresponds to removal of that exact record and expected surviving-slot compaction.; pc_changebox: The ChangeBox occurrence agrees with the active box identified by the game's own header/selection.; map_load: Map-load occurrence corresponds to the game's new map/scene presentation.; ball_received: The bag-received occurrence agrees with the independently decoded Ball-pocket item/count delta.; save_success: The success-only save occurrence agrees with the resulting independently decoded CartRAM save record/checksum layout.; continue: The load occurrence agrees with native CONTINUE reaching that saved game state.; new_game: The new-game occurrence agrees with the game's fresh initialization flow.; soft_reset: The reset occurrence agrees with the game's reset/title transition.; egg_hatch: Hatch publication occurrence agrees with the independently decoded hatchling record/key.",
        "refusal_control": "For every family, an absent required observation, missing family/title, wrong bank/caller or script-bytecode substitute fails the differential gate. battle_start_wild: A trainer encounter or ordinary overworld step must not satisfy the wild-start subclaim.; battle_start_trainer: A wild battle or canceled/unreached challenge must not satisfy trainer-start.; battle_end_result: An in-battle turn or unrelated transient result-bit value must not be accepted as battle completion.; capture_party: A failed throw or full-party box destination must not satisfy party-capture.; capture_box: A failed throw, full-box refusal or party-space catch must not satisfy box-insertion; box-full flag alone is insufficient.; player_faint: Opponent faint or an already-zero record must not satisfy natural player-faint. The discriminator against client-command HP zeroing is the absence of any SLink write-permit receipt (W-1 provenance) touching that party record's HP or wBattleMonHP inside the occurrence window; today lua/gen2/writes.lua structurally refuses to zero the active battler (faint_active_battler always refuses, and faint_party_slot refuses the active slot), so the commanded-zero case cannot currently arise.; poison_faint: A nonterminal poison tick, a healthy mon or an already-zero record must not satisfy poison-faint.; whiteout: A surviving party member, loaded all-zero party or unrelated healing/menu path must not satisfy whiteout.; evolution_publish: Canceled evolution or pre-publication animation must leave the prior identity and cannot satisfy publication.; npc_trade: Decline or fail prerequisites; a pre-compaction slot or a same-species substitute cannot satisfy the record observation.; link_trade: Decline/interruption before commit or a wrong received identity must not satisfy successful link-trade.; pc_deposit: Cancel the menu or only view a box; neither may satisfy deposit.; pc_withdraw: Cancel or attempt withdrawal with full party; neither may satisfy successful movement.; pc_release: Cancel the confirmation or inspect another record; unchanged storage cannot satisfy release.; pc_changebox: Cancel ChangeBox or revisit the same view without a change; neither satisfies a new box-selection transition.; map_load: A same-map step or unrelated fade/menu must not satisfy a new map load.; ball_received: No-capacity/declined grant or a non-ball item change must not satisfy ball-received.; save_success: Save-entry-only occurrence, canceled overwrite or unchanged stale witness must not satisfy successful save.; continue: Title idle, canceled selection or rejected/both-bad save recovery must not satisfy successful CONTINUE.; new_game: Decline overwrite or choose CONTINUE; the retained prior run cannot satisfy New Game.; soft_reset: Ordinary title/menu visits or unrelated input must not satisfy a reset; stale in-flight lifecycle state is a separate R-4/C-2 refusal.; egg_hatch: GiveEgg alone or pre-hatch steps must not satisfy hatch publication.",
        "oracle": "ENGINE + PYDEC/GAME (method: differential gate across titles). ENGINE occurrence is required for every family; select the independent method for its measured effect: battle_start_wild => GAME: Native wild-battle screen/state, compared with the routine's independently captured ENGINE occurrence.; battle_start_trainer => GAME: Native trainer introduction and battle state; no PYDEC party-mutation witness is required for this control-state family.; battle_end_result => GAME: Native battle exit/result/recovery state; transient wBattleResult bits alone are not the oracle.; capture_party => PYDEC: Independent same-frame party/count/name bytes before and after insertion; identity comes from the final acquired record.; capture_box => PYDEC: Independent active-box count, inserted record/name and relevant backing bytes with frame/domain recorded.; player_faint => PYDEC: Independent pre/post party HP and full record identity captured before any healing.; poison_faint => PYDEC: Independent step-aligned party HP/status/identity bytes; no inferred death from a later healed snapshot.; whiteout => GAME: Native whiteout/recovery transition, with ENGINE establishing the required pre-HealParty occurrence; record-level faint claims remain the separate faint families.; evolution_publish => PYDEC: Independent old/final party species and identity bytes from the actual publication slot.; npc_trade => PYDEC: Independent before/after party records, OT/name/identity and final slot layout.; link_trade => PYDEC: Independent final party identities on both sides; save durability is evaluated by save/reload families and T-4 rather than inferred from the routine return.; pc_deposit => PYDEC: Independent party/box counts and exact moved record/name before and after the operation.; pc_withdraw => PYDEC: Independent active-box and party records/counts, including compaction and destination slot.; pc_release => PYDEC: Independent box record/name inventory before and after confirmed release; protocol handling of linked release is a separate S-6 obligation.; pc_changebox => GAME: Native PC active-box header/selection state; storage persistence is separately checked where promised.; map_load => GAME: Native map/scene transition and known destination presentation, compared across title-specific source map facts.; ball_received => PYDEC: Independent Ball-pocket bytes/counts; O-10 fixture injection is not a natural ball-acquisition witness.; save_success => PYDEC: Independent success-boundary CartRAM bytes and per-title save/checksum decoding; full cold-reload GAME qualification remains F-6/T-4 where declared.; continue => GAME: Native load/CONTINUE outcome; distinguish this path from New Game without treating an entry call as load success.; new_game => GAME: Native new-game confirmation/start/control state, including the difference from the loaded-save path.; soft_reset => GAME: Native transition from an in-game (overworld/menu/battle) state into the reset/title flow, observed natively with an independent frame/ENGINE occurrence; a reset/title-state snapshot alone, without that preceding in-game state, does not satisfy the witness.; egg_hatch => PYDEC: Independent egg marker to final species/identity transition at the actual party slot; gift_daycare consumption is separately observed by S-8/D-1.",
        "receipt_marker": "gen2.requirement.F-3",
        "lane": "live-new-gates"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "No coverage receipt matching this planned binding, current input/artifact pins and declared layer has been accepted."
        },
        "PHYSICAL": {
          "status": "OPEN",
          "reason": "PLANNED cartridge scenario has not run or qualified; no physical receipt is claimed."
        }
      }
    },
    {
      "id": "requirement:F-4",
      "required_layers": [
        "SOURCE"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "SOURCE",
          "description": "PLANNED BINDING [P2.4]. Read base stats and wild morning/day/night, water, fishing/time-reference, headbutt, rock-smash and roamer tables from all three locked ROMs through independent Lua and Python readers. EXISTS: lua/gen2/rom.lua; server/adapters/gen2_rom_scan.py; tests/unit/test_gen2_rom_tables.py; tests/unit/test_gen2_rom_reader.py; tools/verify_gen2_rom_layout.py."
        },
        "artifacts": {
          "pokecrystal": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133",
          "pokegold": "d8b8a3600a465308c9953dfa04f0081c05bdcb94",
          "pokesilver": "49b163f7e57702bc939d642a18f591de55d92dae"
        },
        "positive_control": "Both readers agree byte-for-byte with independently source-derived known vectors, including Gold/Silver differences and all time/method branches.",
        "refusal_control": "Flip one table-format byte or pointer, truncate a table, or omit a family/time group; readers/checks must refuse or disagree visibly.",
        "oracle": "Independent Lua ROM reader, Python gen2_rom_scan, and pinned ASM known vectors; fake ROM IO remains SOURCE/MODEL, not PHYSICAL.",
        "receipt_marker": "gen2.requirement.F-4",
        "lane": "rom-layout"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "No coverage receipt matching this planned binding, current input/artifact pins and declared layer has been accepted."
        }
      }
    },
    {
      "id": "requirement:F-5",
      "required_layers": [
        "SOURCE"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "SOURCE",
          "description": "PLANNED BINDING [P2.1]. Regenerate species, item and evolution facts for all selected titles; enumerate species through NUM_POKEMON, keep EGG outside dex IDs, and preserve every evolution branch parameter. EXISTS: gen_gen2_species.py, gen_gen2_evos.py, gen_gen2_items.py and corresponding unit tests; also run evos-generated and items-generated."
        },
        "artifacts": {
          "pokecrystal": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133",
          "pokegold": "d8b8a3600a465308c9953dfa04f0081c05bdcb94",
          "pokesilver": "49b163f7e57702bc939d642a18f591de55d92dae"
        },
        "positive_control": "National order 1..251, baby-family links, Eevee/Tyrogue branches and trade-item/held-item controls match pinned source and ROM tables.",
        "refusal_control": "Reorder a species constant, include EGG as species252, remove an evolution branch, or corrupt a ROM operand; regeneration/check refuses.",
        "oracle": "Pinned constants and evolution ASM plus independent ROM record/pointer controls.",
        "receipt_marker": "gen2.requirement.F-5",
        "lane": "species-generated"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "No coverage receipt matching this planned binding, current input/artifact pins and declared layer has been accepted."
        }
      }
    },
    {
      "id": "requirement:F-6",
      "required_layers": [
        "SOURCE",
        "PHYSICAL"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "NATURAL",
          "description": "PLANNED BINDING [P3b.1/P3b.2/P3b.7/P3b.8]. Play New Game through naming/Mom/Elm/starter to town and Route29 battle saves for Crystal/Gold/Silver plus Crystal second-OT town/battle; cold-boot, CONTINUE, re-save and reload every fixture. PLANNED: tools/gen2_fixtures.py; tests/unit/test_gen2_fixtures.py; independent server/adapters/gen2_codec.py; fixture receipts and post-cutover requalification."
        },
        "artifacts": {
          "pokecrystal": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133",
          "pokegold": "d8b8a3600a465308c9953dfa04f0081c05bdcb94",
          "pokesilver": "49b163f7e57702bc939d642a18f591de55d92dae"
        },
        "positive_control": "Eight isolated saves satisfy both title-specific checksum/recovery layouts and PYDEC agrees with party-menu readback after reload.",
        "refusal_control": "Bad primary/good backup, good primary/bad backup, both bad, wrong OT, or a different save/frame must be identified; both-bad cannot qualify.",
        "oracle": "GAME load/CONTINUE/menu and independent PYDEC. O-10 permits Ball-pocket injection only for battle fixtures; it is recorded and does not qualify ball acquisition.",
        "receipt_marker": "gen2.requirement.F-6",
        "lane": "fixtures"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "No coverage receipt matching this planned binding, current input/artifact pins and declared layer has been accepted."
        },
        "PHYSICAL": {
          "status": "OPEN",
          "reason": "PLANNED cartridge scenario has not run or qualified; no physical receipt is claimed."
        }
      }
    },
    {
      "id": "requirement:F-7g",
      "required_layers": [
        "SOURCE"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "SOURCE",
          "description": "PLANNED BINDING [P1.3/P2.5]. Regenerate separate Gold and Silver admission/encounter/area catalogs from their own locked ROM/source identity, while independently comparing shared WRAM layout facts. EXISTS: gen_gen2_admission.py, gen_gen2_encounters.py, gen_gen2_area_map.py and admission/encounter/area-map unit controls; use encounters-generated too."
        },
        "artifacts": {
          "pokecrystal": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133",
          "pokegold": "d8b8a3600a465308c9953dfa04f0081c05bdcb94",
          "pokesilver": "49b163f7e57702bc939d642a18f591de55d92dae"
        },
        "positive_control": "The two clean hashes remain distinct; a documented Gold/Silver wild-table counterexample differs without inventing different ordinary area identity.",
        "refusal_control": "Copy Gold's artifact or encounter data into Silver, remove Silver's row, or claim an unselected Crystal/AP artifact; validation refuses.",
        "oracle": "Pinned separate ROM SHA1s and Johto wild records, not title-name recognition.",
        "receipt_marker": "gen2.requirement.F-7g",
        "lane": "admission-generated"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "No coverage receipt matching this planned binding, current input/artifact pins and declared layer has been accepted."
        }
      }
    },
    {
      "id": "requirement:R-1",
      "required_layers": [
        "SOURCE",
        "PHYSICAL"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "NATURAL",
          "description": "PLANNED BINDING [P3b.1/P3b.3/P3b.3a]. At a recorded frame on each running title, dump the same party, active/backing boxes and names from explicit WRAM/SRAM domains; compare production Lua decode with independent Python decode. PLANNED: lua/gen2/reads.lua; server/adapters/gen2_codec.py; tests/unit/test_gen2_reads.py; tests/live/test_gen2_new_gates.py."
        },
        "artifacts": {
          "pokecrystal": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133",
          "pokegold": "d8b8a3600a465308c9953dfa04f0081c05bdcb94",
          "pokesilver": "49b163f7e57702bc939d642a18f591de55d92dae"
        },
        "positive_control": "48-byte party/32-byte box records, 20 slots per box and 14 boxes compare byte-for-byte with names and held items.",
        "refusal_control": "Use a different frame/domain, corrupt one record/name byte, or omit a title; the differential must fail.",
        "oracle": "PYDEC over the exact same-frame captured bytes; fixture-only agreement does not satisfy PHYSICAL.",
        "receipt_marker": "gen2.requirement.R-1",
        "lane": "live-new-gates"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "No coverage receipt matching this planned binding, current input/artifact pins and declared layer has been accepted."
        },
        "PHYSICAL": {
          "status": "OPEN",
          "reason": "PLANNED cartridge scenario has not run or qualified; no physical receipt is claimed."
        }
      }
    },
    {
      "id": "requirement:R-2",
      "required_layers": [
        "SOURCE",
        "PHYSICAL"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "NATURAL",
          "description": "PLANNED BINDING [P3b.1/P3b.3a]. Read mons with varied DVs, level and stat-exp on each running title; recompute CalcMonStats independently, including shared Special stat-exp feeding Sp.Atk and Sp.Def. PLANNED: independent gen2_codec stat calculation; test_gen2_codec.py; live inspect rows."
        },
        "artifacts": {
          "pokecrystal": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133",
          "pokegold": "d8b8a3600a465308c9953dfa04f0081c05bdcb94",
          "pokesilver": "49b163f7e57702bc939d642a18f591de55d92dae"
        },
        "positive_control": "Both independent calculations agree with game-displayed stats for multiple known-positive vectors and rounding boundaries.",
        "refusal_control": "Use separate Sp.Def stat-exp or a wrong DV/rounding term; CONTROL rejects the derived stats.",
        "oracle": "CONTROL: independently derived CalcMonStats plus GAME status values, not production-reader self-comparison.",
        "receipt_marker": "gen2.requirement.R-2",
        "lane": "live-new-gates"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "No coverage receipt matching this planned binding, current input/artifact pins and declared layer has been accepted."
        },
        "PHYSICAL": {
          "status": "OPEN",
          "reason": "PLANNED cartridge scenario has not run or qualified; no physical receipt is claimed."
        }
      }
    },
    {
      "id": "requirement:R-3",
      "required_layers": [
        "SOURCE",
        "PHYSICAL"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "NATURAL",
          "description": "PLANNED BINDING [P3b.3a]. Enter known trainer battles, inspect Johto and Kanto badge screens, a held-item party entry and several active PC boxes on every selected title. PLANNED: test_gen2_new_gates.py inspect cases; P2 trainers/maps/profile facts are source inputs only."
        },
        "artifacts": {
          "pokecrystal": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133",
          "pokegold": "d8b8a3600a465308c9953dfa04f0081c05bdcb94",
          "pokesilver": "49b163f7e57702bc939d642a18f591de55d92dae"
        },
        "positive_control": "Each decoded trainer class/id, badge bit, held-item byte and active-box index matches the corresponding native display.",
        "refusal_control": "Swap trainer class/id, count versus bitmask, item ID, or 0/1-based box interpretation; independent display comparison fails.",
        "oracle": "GAME: trainer display, Trainer Card, party item line and PC box header captured with the read frame.",
        "receipt_marker": "gen2.requirement.R-3",
        "lane": "live-new-gates"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "No coverage receipt matching this planned binding, current input/artifact pins and declared layer has been accepted."
        },
        "PHYSICAL": {
          "status": "OPEN",
          "reason": "PLANNED cartridge scenario has not run or qualified; no physical receipt is claimed."
        }
      }
    },
    {
      "id": "requirement:R-4",
      "required_layers": [
        "SOURCE",
        "PHYSICAL"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "NATURAL",
          "description": "PLANNED BINDING [P2.3/P3b.5/P3b.6]. Boot title screen, load/continue, soft-reset, enter textbox/START/battle/warp/Elm-scene states, and return to ordinary idle overworld; observe admission, hello, checkpoint and write permits. EXISTS: gen_gen2_write_checkpoint.py and test_gen2_write_checkpoint.py. PLANNED: live-new-gates checkpoint/entry cases."
        },
        "artifacts": {
          "pokecrystal": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133",
          "pokegold": "d8b8a3600a465308c9953dfa04f0081c05bdcb94",
          "pokesilver": "49b163f7e57702bc939d642a18f591de55d92dae"
        },
        "positive_control": "Hello starts only after a valid overworld checkpoint or running battle; ordinary idle play eventually grants a valid window.",
        "refusal_control": "Title/unloaded state, scene/menu/warp, wrong caller/bank or reset invalidates writes; no premature hello or held permit survives.",
        "oracle": "GAME context plus ENGINE checkpoint/write-site observations and wire capture; candidate source predicates are not liveness proof.",
        "receipt_marker": "gen2.requirement.R-4",
        "lane": "live-new-gates"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "No coverage receipt matching this planned binding, current input/artifact pins and declared layer has been accepted."
        },
        "PHYSICAL": {
          "status": "OPEN",
          "reason": "PLANNED cartridge scenario has not run or qualified; no physical receipt is claimed."
        }
      }
    },
    {
      "id": "requirement:R-5g",
      "required_layers": [
        "SOURCE",
        "PHYSICAL"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "NATURAL",
          "description": "PLANNED BINDING [P3b.3a]. Inspect source-derived DV cases giving each applicable gender outcome and shiny/non-shiny outcomes on each selected title. PLANNED: test_gen2_new_gates.py gender/shiny inspect cases and independent codec controls."
        },
        "artifacts": {
          "pokecrystal": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133",
          "pokegold": "d8b8a3600a465308c9953dfa04f0081c05bdcb94",
          "pokesilver": "49b163f7e57702bc939d642a18f591de55d92dae"
        },
        "positive_control": "Derived gender matches the native status symbol and shininess matches the game palette/animation.",
        "refusal_control": "Change one decisive DV or invert the threshold/sentinel handling; displayed outcome comparison rejects it.",
        "oracle": "GAME gender symbol and shiny palette/animation with independent DV decode.",
        "receipt_marker": "gen2.requirement.R-5g",
        "lane": "live-new-gates"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "No coverage receipt matching this planned binding, current input/artifact pins and declared layer has been accepted."
        },
        "PHYSICAL": {
          "status": "OPEN",
          "reason": "PLANNED cartridge scenario has not run or qualified; no physical receipt is claimed."
        }
      }
    },
    {
      "id": "requirement:S-1",
      "required_layers": [
        "SOURCE",
        "PHYSICAL"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "NATURAL",
          "description": "PLANNED BINDING [P3b.4]. After the GBC frame-alignment probe, drive each declared natural site on Crystal/Gold/Silver and record routine, bank, PC, caller and armed frame. PLANNED: lua/gen2/signals.lua; test_gen2_signals.py; test_gen2_new_gates.py site rows."
        },
        "artifacts": {
          "pokecrystal": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133",
          "pokegold": "d8b8a3600a465308c9953dfa04f0081c05bdcb94",
          "pokesilver": "49b163f7e57702bc939d642a18f591de55d92dae"
        },
        "positive_control": "Exactly the expected site fires at the qualified routine/context in each title.",
        "refusal_control": "Visit the same banked address in another bank or an unrelated caller; no accepted event is produced.",
        "oracle": "ENGINE independent bus-exec trace with load-time expected-byte pins.",
        "receipt_marker": "gen2.requirement.S-1",
        "lane": "live-new-gates"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "No coverage receipt matching this planned binding, current input/artifact pins and declared layer has been accepted."
        },
        "PHYSICAL": {
          "status": "OPEN",
          "reason": "PLANNED cartridge scenario has not run or qualified; no physical receipt is claimed."
        }
      }
    },
    {
      "id": "requirement:S-2",
      "required_layers": [
        "SOURCE",
        "PHYSICAL"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "NATURAL",
          "description": "PLANNED BINDING [P3b.4/P3b.7]. Throw a ball in unresolved wild battles with party space and full party; observe TryAddMonToParty versus SendMonIntoBox and the Ball pocket around the throw. PLANNED: natural capture rows and duo ball_gate/boxed_capture; source input is P2 engine_signals."
        },
        "artifacts": {
          "pokecrystal": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133",
          "pokegold": "d8b8a3600a465308c9953dfa04f0081c05bdcb94",
          "pokesilver": "49b163f7e57702bc939d642a18f591de55d92dae"
        },
        "positive_control": "Correct branch, acquired record destination, and one consumed ball are observed on every title.",
        "refusal_control": "Failed throw or non-ball action cannot emit a successful capture/destination; wrong-pocket decrement fails the comparison.",
        "oracle": "ENGINE branch receipt plus PYDEC pre/post bag and acquired-record bytes; SERVER wire is supplemental.",
        "receipt_marker": "gen2.requirement.S-2",
        "lane": "live-new-gates"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "No coverage receipt matching this planned binding, current input/artifact pins and declared layer has been accepted."
        },
        "PHYSICAL": {
          "status": "OPEN",
          "reason": "PLANNED cartridge scenario has not run or qualified; no physical receipt is claimed."
        }
      }
    },
    {
      "id": "requirement:S-3",
      "required_layers": [
        "SOURCE",
        "PHYSICAL"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "NATURAL",
          "description": "PLANNED BINDING [P3b.4/P3b.7]. With party full, catch into both a non-final active-box slot and the twentieth slot on each title; observe actual SendMonIntoBox insertion. PLANNED: test_gen2_new_gates.py box insertion and duo boxed_capture."
        },
        "artifacts": {
          "pokecrystal": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133",
          "pokegold": "d8b8a3600a465308c9953dfa04f0081c05bdcb94",
          "pokesilver": "49b163f7e57702bc939d642a18f591de55d92dae"
        },
        "positive_control": "Insertion and correct new record occur in both controls, regardless of BATTLERESULT_BOX_FULL flag behavior.",
        "refusal_control": "A full box or failed catch cannot be accepted as insertion; a model that relies only on the box-full flag fails non-final-slot control.",
        "oracle": "ENGINE insertion path with independently recorded box count/slot; PYDEC/SERVER confirm downstream behavior separately.",
        "receipt_marker": "gen2.requirement.S-3",
        "lane": "live-new-gates"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "No coverage receipt matching this planned binding, current input/artifact pins and declared layer has been accepted."
        },
        "PHYSICAL": {
          "status": "OPEN",
          "reason": "PLANNED cartridge scenario has not run or qualified; no physical receipt is claimed."
        }
      }
    },
    {
      "id": "requirement:S-4",
      "required_layers": [
        "SOURCE",
        "PHYSICAL"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "NATURAL",
          "description": "PLANNED BINDING [P3b.4]. Cause player battle faint and overworld poison faint, including a terminal party faint; latch party bytes at the qualified sites before any HealParty. PLANNED: natural faint/poison/whiteout site rows; not command-executor force_faint tests."
        },
        "artifacts": {
          "pokecrystal": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133",
          "pokegold": "d8b8a3600a465308c9953dfa04f0081c05bdcb94",
          "pokesilver": "49b163f7e57702bc939d642a18f591de55d92dae"
        },
        "positive_control": "UpdateFaintedPlayerMon and DoPoisonStep receipts preserve faint-time HP/key data and the pre-heal ordering.",
        "refusal_control": "Opponent faint, client-forced zeroing, or reading only after healing cannot satisfy the player-faint receipt.",
        "oracle": "ENGINE source-pinned routine/order trace and raw faint-time bytes.",
        "receipt_marker": "gen2.requirement.S-4",
        "lane": "live-new-gates"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "No coverage receipt matching this planned binding, current input/artifact pins and declared layer has been accepted."
        },
        "PHYSICAL": {
          "status": "OPEN",
          "reason": "PLANNED cartridge scenario has not run or qualified; no physical receipt is claimed."
        }
      }
    },
    {
      "id": "requirement:S-5",
      "required_layers": [
        "SOURCE",
        "PHYSICAL"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "NATURAL",
          "description": "PLANNED BINDING [P3b.4/P3b.7]. Complete a natural evolution and an NPC trade on each title; observe final species publication and the acquisition slot after any party compaction. PLANNED: evolution/npc_trade natural gates and duo scenarios; source engine anchors alone do not close timing."
        },
        "artifacts": {
          "pokecrystal": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133",
          "pokegold": "d8b8a3600a465308c9953dfa04f0081c05bdcb94",
          "pokesilver": "49b163f7e57702bc939d642a18f591de55d92dae"
        },
        "positive_control": "Exactly one key_change carries the old/new identity and new species; canceled evolution leaves identity unchanged.",
        "refusal_control": "Hooking before final publication or choosing a pre-compaction NPC slot must fail; no capture/box events for the same transition.",
        "oracle": "ENGINE publication-site trace plus SERVER key_change/ack wire and identity cache readback.",
        "receipt_marker": "gen2.requirement.S-5",
        "lane": "live-new-gates"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "No coverage receipt matching this planned binding, current input/artifact pins and declared layer has been accepted."
        },
        "PHYSICAL": {
          "status": "OPEN",
          "reason": "PLANNED cartridge scenario has not run or qualified; no physical receipt is claimed."
        }
      }
    },
    {
      "id": "requirement:S-6",
      "required_layers": [
        "SOURCE",
        "PHYSICAL"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "NATURAL",
          "description": "PLANNED BINDING [P3b.4/P3b.7]. Use the native PC to deposit, withdraw, release a linked boxed mon and change boxes, preserving selected key identity. PLANNED: pc_ops/changebox gates and duo scenarios; release is logged without inventing a new wire handler."
        },
        "artifacts": {
          "pokecrystal": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133",
          "pokegold": "d8b8a3600a465308c9953dfa04f0081c05bdcb94",
          "pokesilver": "49b163f7e57702bc939d642a18f591de55d92dae"
        },
        "positive_control": "Each actual operation emits its corresponding observation; boxed release is explicitly logged as the known shared-protocol gap.",
        "refusal_control": "Canceled menus, a box-view change without movement, or server-command movement cannot duplicate natural PC events.",
        "oracle": "ENGINE PC operation sites; raw record/key changes and SERVER event log provide independent post-operation checks.",
        "receipt_marker": "gen2.requirement.S-6",
        "lane": "live-new-gates"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "No coverage receipt matching this planned binding, current input/artifact pins and declared layer has been accepted."
        },
        "PHYSICAL": {
          "status": "OPEN",
          "reason": "PLANNED cartridge scenario has not run or qualified; no physical receipt is claimed."
        }
      }
    },
    {
      "id": "requirement:S-7",
      "required_layers": [
        "SOURCE",
        "PHYSICAL"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "NATURAL",
          "description": "PLANNED BINDING [P3b.2/P3b.4/P3b.7]. Perform a successful in-game SAVE, declined overwrite/save attempt, CONTINUE and New Game; take CartRAM witness only after _SaveGameData success. PLANNED: save-witness gate, recovery controls and fixture qualifier; per-title save layouts stay explicit."
        },
        "artifacts": {
          "pokecrystal": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133",
          "pokegold": "d8b8a3600a465308c9953dfa04f0081c05bdcb94",
          "pokesilver": "49b163f7e57702bc939d642a18f591de55d92dae"
        },
        "positive_control": "A successful save produces the correct witness and survives host cold reload with the same save identity.",
        "refusal_control": "Entry-only save hook, canceled overwrite, failed save or an old witness cannot count; New Game and CONTINUE must differ.",
        "oracle": "ENGINE success-only boundary plus SERVER witness association and independent reload/PYDEC.",
        "receipt_marker": "gen2.requirement.S-7",
        "lane": "live-new-gates"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "No coverage receipt matching this planned binding, current input/artifact pins and declared layer has been accepted."
        },
        "PHYSICAL": {
          "status": "OPEN",
          "reason": "PLANNED cartridge scenario has not run or qualified; no physical receipt is claimed."
        }
      }
    },
    {
      "id": "requirement:S-8",
      "required_layers": [
        "SOURCE",
        "PHYSICAL"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "NATURAL",
          "description": "PLANNED BINDING [P2.5/P3b.4/P3b.7]. Acquire a static, a direct gift and a hatched egg through their natural paths; give an egg without hatching as the negative boundary. PLANNED: natural gift/egg_hatch and duo gift/egg_hatch; EXISTS area-map policy inputs; static source generator remains a planned dependency if absent."
        },
        "artifacts": {
          "pokecrystal": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133",
          "pokegold": "d8b8a3600a465308c9953dfa04f0081c05bdcb94",
          "pokesilver": "49b163f7e57702bc939d642a18f591de55d92dae"
        },
        "positive_control": "Static/gift records publish once; hatch publishes gift capture under gift_daycare using the hatchling key.",
        "refusal_control": "GiveEgg alone must not publish the hatch capture, and an aborted acquisition must not consume a gift/area.",
        "oracle": "ENGINE acquisition/hatch site plus SERVER capture area/key and independent record decode.",
        "receipt_marker": "gen2.requirement.S-8",
        "lane": "live-new-gates"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "No coverage receipt matching this planned binding, current input/artifact pins and declared layer has been accepted."
        },
        "PHYSICAL": {
          "status": "OPEN",
          "reason": "PLANNED cartridge scenario has not run or qualified; no physical receipt is claimed."
        }
      }
    },
    {
      "id": "requirement:S-9g",
      "required_layers": [
        "SOURCE",
        "PHYSICAL"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "NATURAL",
          "description": "PLANNED BINDING [P2.5/P3b.4/P3b.7]. Catch each source-supported roaming legendary naturally in a map with an independently tracked ordinary encounter; inspect both ledgers. PLANNED: roamer extra-catch duo scenario; EXISTS encounter/area-map generators provide SOURCE policy only."
        },
        "artifacts": {
          "pokecrystal": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133",
          "pokegold": "d8b8a3600a465308c9953dfa04f0081c05bdcb94",
          "pokesilver": "49b163f7e57702bc939d642a18f591de55d92dae"
        },
        "positive_control": "The catch creates legend_<species> extra pairing while ordinary map consumption/lock remains unchanged.",
        "refusal_control": "Treat the roamer as the map's first catch or consume/lock that map; the non-consumption oracle fails.",
        "oracle": "ENGINE roamer/capture observation plus SERVER separate legend and ordinary-area states.",
        "receipt_marker": "gen2.requirement.S-9g",
        "lane": "duo-pairs"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "No coverage receipt matching this planned binding, current input/artifact pins and declared layer has been accepted."
        },
        "PHYSICAL": {
          "status": "OPEN",
          "reason": "PLANNED cartridge scenario has not run or qualified; no physical receipt is claimed."
        }
      }
    },
    {
      "id": "requirement:S-10g",
      "required_layers": [
        "SOURCE",
        "PHYSICAL"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "NATURAL",
          "description": "PLANNED BINDING [P2.5/P3b.4/P3b.7]. Enter the Bug-Catching Contest, keep a contest capture, and compare the ordinary National Park encounter ledger. PLANNED: contest duo control; EXISTS area-map/encounter SOURCE policy."
        },
        "artifacts": {
          "pokecrystal": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133",
          "pokegold": "d8b8a3600a465308c9953dfa04f0081c05bdcb94",
          "pokesilver": "49b163f7e57702bc939d642a18f591de55d92dae"
        },
        "positive_control": "Contest capture pairs under national_park_contest with the kept record identity.",
        "refusal_control": "A rejected/replaced contest specimen or ordinary park catch must not create the wrong contest/ordinary-zone link.",
        "oracle": "ENGINE contest acquisition site plus SERVER zone/key ledger and independent kept-record readback.",
        "receipt_marker": "gen2.requirement.S-10g",
        "lane": "duo-pairs"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "No coverage receipt matching this planned binding, current input/artifact pins and declared layer has been accepted."
        },
        "PHYSICAL": {
          "status": "OPEN",
          "reason": "PLANNED cartridge scenario has not run or qualified; no physical receipt is claimed."
        }
      }
    },
    {
      "id": "requirement:W-1",
      "required_layers": [
        "SOURCE",
        "PHYSICAL"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "COMMAND",
          "description": "PLANNED BINDING [P3b.5]. Issue a valid write command through the production armed gate and attempt unarmed, expired, wrong-domain and out-of-bounds writes; capture every actual write sink. PLANNED: test_gen2_writes.py MODEL and test_gen2_new_gates.py PHYSICAL write-provenance controls."
        },
        "artifacts": {
          "pokecrystal": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133",
          "pokegold": "d8b8a3600a465308c9953dfa04f0081c05bdcb94",
          "pokesilver": "49b163f7e57702bc939d642a18f591de55d92dae"
        },
        "positive_control": "All bytes of the valid operation carry a live permit and prevalidated payload; idle liveness still allows it.",
        "refusal_control": "No byte reaches a write sink for any invalid permit/payload, including a later-invalid span in a multi-span request.",
        "oracle": "ENGINE write-site/sink provenance plus PYDEC over those records; final-state equality alone is insufficient.",
        "receipt_marker": "gen2.requirement.W-1",
        "lane": "live-new-gates"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "No coverage receipt matching this planned binding, current input/artifact pins and declared layer has been accepted."
        },
        "PHYSICAL": {
          "status": "OPEN",
          "reason": "PLANNED cartridge scenario has not run or qualified; no physical receipt is claimed."
        }
      }
    },
    {
      "id": "requirement:W-2",
      "required_layers": [
        "SOURCE",
        "PHYSICAL"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "COMMAND",
          "description": "PLANNED BINDING [P3b.5/P3b.7]. Send force_faint for the active battler while running battle code and for party-full/last-mon transition cases; observe battle-loop-head and permitted tail retries. PLANNED: armed-write gate and linked_faint_active duo; no natural-faint stimulus substitution."
        },
        "artifacts": {
          "pokecrystal": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133",
          "pokegold": "d8b8a3600a465308c9953dfa04f0081c05bdcb94",
          "pokesilver": "49b163f7e57702bc939d642a18f591de55d92dae"
        },
        "positive_control": "Write occurs at the qualified head; only the declared party-full/last-mon cases retry at tail, with native battle outcome/readback.",
        "refusal_control": "Wrong-loop/caller window or arbitrary retry-at-tail writes must refuse without a partial payload.",
        "oracle": "ENGINE checkpoint/write trace plus GAME battle HP/transition, independent same-frame record decode.",
        "receipt_marker": "gen2.requirement.W-2",
        "lane": "live-new-gates"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "No coverage receipt matching this planned binding, current input/artifact pins and declared layer has been accepted."
        },
        "PHYSICAL": {
          "status": "OPEN",
          "reason": "PLANNED cartridge scenario has not run or qualified; no physical receipt is claimed."
        }
      }
    },
    {
      "id": "requirement:W-3",
      "required_layers": [
        "SOURCE",
        "PHYSICAL"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "COMMAND",
          "description": "PLANNED BINDING [P3b.5/P6.3 conditional]. Default-disabled Explode Mode: inspect capability/config and attempt delivery without enablement. If separately enabled, bind source-derived explosion semantics and qualified battle write cases first. PLANNED: conditional test_gen2_writes.py/live-new-gates controls and P6 applicability evaluation. Default no is preserved."
        },
        "artifacts": {
          "pokecrystal": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133",
          "pokegold": "d8b8a3600a465308c9953dfa04f0081c05bdcb94",
          "pokesilver": "49b163f7e57702bc939d642a18f591de55d92dae"
        },
        "positive_control": "Disabled disposition yields no dispatch/unauthorized write; enabled qualification must observe the declared game effect and retained invariants.",
        "refusal_control": "An unapproved enablement, unsupported command, wrong battle window or success claimed from disabled status must fail.",
        "oracle": "ENGINE for enabled behavior; explicit signed applicability/disposition for disabled status, never a behavior PASS.",
        "receipt_marker": "gen2.requirement.W-3",
        "lane": "release-evidence"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "Default-disabled/deferred conditional. Await signed applicability/disposition at P6; an enabled branch requires its declared layers. Disabled status is not a behavior PASS."
        },
        "PHYSICAL": {
          "status": "OPEN",
          "reason": "Default-disabled/deferred conditional. Await signed applicability/disposition at P6; an enabled branch requires its declared layers. Disabled status is not a behavior PASS."
        }
      }
    },
    {
      "id": "requirement:W-4",
      "required_layers": [
        "SOURCE",
        "PHYSICAL"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "COMMAND",
          "description": "PLANNED BINDING [P3b.5/P6.3 conditional]. Default-disabled rival swap: verify absent capability/dispatch. Only after enablement and B-25 source resolution, submit source-valid team payloads in the qualified rival battle. PLANNED: conditional rival-swap command gates; B-25 remains a prerequisite, not guessed record geometry."
        },
        "artifacts": {
          "pokecrystal": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133",
          "pokegold": "d8b8a3600a465308c9953dfa04f0081c05bdcb94",
          "pokesilver": "49b163f7e57702bc939d642a18f591de55d92dae"
        },
        "positive_control": "Disabled mode performs no swap; enabled mode must produce independently read native team records.",
        "refusal_control": "Unresolved format, wrong trainer/context, malformed species/team or unapproved enablement must refuse before writes.",
        "oracle": "GAME native opponent team plus independent record decode when enabled; signed disabled disposition otherwise.",
        "receipt_marker": "gen2.requirement.W-4",
        "lane": "release-evidence"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "Default-disabled/deferred conditional. Await signed applicability/disposition at P6; an enabled branch requires its declared layers. Disabled status is not a behavior PASS."
        },
        "PHYSICAL": {
          "status": "OPEN",
          "reason": "Default-disabled/deferred conditional. Await signed applicability/disposition at P6; an enabled branch requires its declared layers. Disabled status is not a behavior PASS."
        }
      }
    },
    {
      "id": "requirement:W-5",
      "required_layers": [
        "SOURCE",
        "PHYSICAL"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "COMMAND",
          "description": "PLANNED BINDING [P3b.5/P3b.7]. Memorialize with Box14 inactive; separately perform ordinary deposit/withdraw in the current box, then select Box14 and SAVE before reload. PLANNED: Box14 hazard/current-box positive controls, memorial-across-SAVE duo and native save reload."
        },
        "artifacts": {
          "pokecrystal": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133",
          "pokegold": "d8b8a3600a465308c9953dfa04f0081c05bdcb94",
          "pokesilver": "49b163f7e57702bc939d642a18f591de55d92dae"
        },
        "positive_control": "Inactive-target memorial write and ordinary current-box traffic both work under their distinct ownership contracts; saved records survive reload.",
        "refusal_control": "Memorial backing write while wCurBox==13 refuses with no byte changed; a gate that blocks all current-box traffic also fails.",
        "oracle": "GAME PC/save reload plus independent PYDEC CartRAM/active-shadow/backing-store comparison; live CartRAM mapping verifies target offset.",
        "receipt_marker": "gen2.requirement.W-5",
        "lane": "live-new-gates"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "No coverage receipt matching this planned binding, current input/artifact pins and declared layer has been accepted."
        },
        "PHYSICAL": {
          "status": "OPEN",
          "reason": "PLANNED cartridge scenario has not run or qualified; no physical receipt is claimed."
        }
      }
    },
    {
      "id": "requirement:W-6",
      "required_layers": [
        "SOURCE",
        "PHYSICAL"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "COMMAND",
          "description": "PLANNED BINDING [P2.3/P3b.5]. Attempt box writes before the first SAVE, during New Game erase/overwrite prompts, and after a successful save at an ordinary idle checkpoint. EXISTS: write-checkpoint source generator/negative tests. PLANNED: live checkpoint lifecycle and liveness controls."
        },
        "artifacts": {
          "pokecrystal": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133",
          "pokegold": "d8b8a3600a465308c9953dfa04f0081c05bdcb94",
          "pokesilver": "49b163f7e57702bc939d642a18f591de55d92dae"
        },
        "positive_control": "A properly saved idle state permits its valid operation without suppressing normal gameplay.",
        "refusal_control": "Unsaved/New Game/overwrite/menu/warp ownership states refuse all writes, including stale permits carried across transitions.",
        "oracle": "GAME state/first-save and reload observations plus ENGINE checkpoint/write provenance.",
        "receipt_marker": "gen2.requirement.W-6",
        "lane": "live-new-gates"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "No coverage receipt matching this planned binding, current input/artifact pins and declared layer has been accepted."
        },
        "PHYSICAL": {
          "status": "OPEN",
          "reason": "PLANNED cartridge scenario has not run or qualified; no physical receipt is claimed."
        }
      }
    },
    {
      "id": "requirement:W-7",
      "required_layers": [
        "SOURCE",
        "PHYSICAL"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "NATURAL",
          "description": "PLANNED BINDING [P3b.5/P3b.7]. Drive a linked-party whiteout naturally; capture faint state before HealParty and let the shared server rebuild flow command the surviving records. PLANNED: whiteout duo with independent witness/oracle pipeline, not client RESULT alone."
        },
        "artifacts": {
          "pokecrystal": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133",
          "pokegold": "d8b8a3600a465308c9953dfa04f0081c05bdcb94",
          "pokesilver": "49b163f7e57702bc939d642a18f591de55d92dae"
        },
        "positive_control": "Shared rebuild yields the prescribed links/party state while honoring native heal/save ordering.",
        "refusal_control": "Post-heal-only sampling, wrong-side identity, premature rebuild writes or duplicate whiteout must fail independent readback.",
        "oracle": "SERVER event/state timeline plus PYDEC pre-heal and final/reloaded party records.",
        "receipt_marker": "gen2.requirement.W-7",
        "lane": "duo-pairs"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "No coverage receipt matching this planned binding, current input/artifact pins and declared layer has been accepted."
        },
        "PHYSICAL": {
          "status": "OPEN",
          "reason": "PLANNED cartridge scenario has not run or qualified; no physical receipt is claimed."
        }
      }
    },
    {
      "id": "requirement:C-0",
      "required_layers": [
        "MODEL"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "MODEL",
          "description": "PLANNED BINDING [P2.6/P3a.2/P3b.6]. Load the production Entry.build graph under lupa with fake socket/IO and drive every protocol §9 assertion and the Gen2 schema fields. EXISTS: tests/unit/test_protocol_schema.py and protocol_schema.py; PLANNED: Gen2 production client conformance cases in test_gen2_client.py/test_protocol_conformance.py."
        },
        "artifacts": {
          "pokecrystal": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133",
          "pokegold": "d8b8a3600a465308c9953dfa04f0081c05bdcb94",
          "pokesilver": "49b163f7e57702bc939d642a18f591de55d92dae"
        },
        "positive_control": "Each assertion has an independently inspected wire/write result on the actual production composition.",
        "refusal_control": "Malformed fields, missing ack, unsafe write, borrowed/unloaded party or missing assertion must fail; a harness-only graph is not accepted.",
        "oracle": "MODEL fake-server wire/write oracle with production graph identity; no PHYSICAL claim.",
        "receipt_marker": "gen2.requirement.C-0",
        "lane": "unit"
      },
      "evidence": {
        "MODEL": {
          "status": "OPEN",
          "reason": "No coverage receipt matching this planned binding, current input/artifact pins and declared layer has been accepted."
        }
      }
    },
    {
      "id": "requirement:C-1",
      "required_layers": [
        "SOURCE",
        "PHYSICAL"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "NATURAL",
          "description": "PLANNED BINDING [P1.3/P3b.3/P3b.7]. Boot each selected ROM with a valid save, capture hello identity/foundation/kind, then try unknown hash, altered anchor, unselected Crystal1.1 and AP/randomized inputs. EXISTS admission SOURCE generator/tests. PLANNED entry admission and admit_wrong_rom runtime scenarios; BUILT is not ADMITTED."
        },
        "artifacts": {
          "pokecrystal": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133",
          "pokegold": "d8b8a3600a465308c9953dfa04f0081c05bdcb94",
          "pokesilver": "49b163f7e57702bc939d642a18f591de55d92dae"
        },
        "positive_control": "Exactly one permitted pack/title/kind is selected and hello carries actual rom_sha1 and OT identity.",
        "refusal_control": "Unknown/ambiguous hash, contradictory title/anchor or unsupported artifact refuses before runtime side effects.",
        "oracle": "SERVER hello/refusal and immutable admission result plus independent ROM hash/anchor readback.",
        "receipt_marker": "gen2.requirement.C-1",
        "lane": "live-new-gates"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "No coverage receipt matching this planned binding, current input/artifact pins and declared layer has been accepted."
        },
        "PHYSICAL": {
          "status": "OPEN",
          "reason": "PLANNED cartridge scenario has not run or qualified; no physical receipt is claimed."
        }
      }
    },
    {
      "id": "requirement:C-2",
      "required_layers": [
        "SOURCE",
        "PHYSICAL"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "NATURAL",
          "description": "PLANNED BINDING [P3b.6/P3b.7]. Disconnect/reconnect with the same save, switch to wrong OT/save, clear WRAM, then soft-reset and reload using isolated instance save directories. PLANNED reconnect and soft_reset duos; production client MODEL lifecycle tests supplement them."
        },
        "artifacts": {
          "pokecrystal": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133",
          "pokegold": "d8b8a3600a465308c9953dfa04f0081c05bdcb94",
          "pokesilver": "49b163f7e57702bc939d642a18f591de55d92dae"
        },
        "positive_control": "Same-save reconnect retains links; the valid reload reestablishes identity and appropriate observations.",
        "refusal_control": "Wrong-save, unloaded/cleared WRAM or stale queued state cannot mutate the prior run or issue writes; warnings are observed.",
        "oracle": "SERVER links/status/wire plus independent save/OT/PYDEC readback.",
        "receipt_marker": "gen2.requirement.C-2",
        "lane": "duo-pairs"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "No coverage receipt matching this planned binding, current input/artifact pins and declared layer has been accepted."
        },
        "PHYSICAL": {
          "status": "OPEN",
          "reason": "PLANNED cartridge scenario has not run or qualified; no physical receipt is claimed."
        }
      }
    },
    {
      "id": "requirement:C-3",
      "required_layers": [
        "SOURCE",
        "PHYSICAL"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "COMMAND",
          "description": "PLANNED BINDING [P3b.6/P4.1]. Send HUD/msgbox/GUI text containing unsupported characters and populate native SLINK panel rows on qualified companion overlays for each title. PLANNED test_gen2_client.py and P4 panel rows in test_gen2_trade_gates.py; overlay artifact pins required."
        },
        "artifacts": {
          "crystal_overlay": {
            "state": "PLANNED",
            "digest": null,
            "base_artifact": "pokecrystal",
            "base_digest": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133"
          },
          "gold_overlay": {
            "state": "PLANNED",
            "digest": null,
            "base_artifact": "pokegold",
            "base_digest": "d8b8a3600a465308c9953dfa04f0081c05bdcb94"
          },
          "silver_overlay": {
            "state": "PLANNED",
            "digest": null,
            "base_artifact": "pokesilver",
            "base_digest": "49b163f7e57702bc939d642a18f591de55d92dae"
          }
        },
        "positive_control": "Every displayed string is sanitized and the game's panel shows all expected rows and values.",
        "refusal_control": "Unsupported bytes, truncated/overflowing rows or unqualified mailbox/panel capability must not corrupt display/game state.",
        "oracle": "GAME transient HUD and native panel tilemap/row observations; MODEL sanitizer tests are supplementary.",
        "receipt_marker": "gen2.requirement.C-3",
        "lane": "live-gates"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "No coverage receipt matching this planned binding, current input/artifact pins and declared layer has been accepted."
        },
        "PHYSICAL": {
          "status": "OPEN",
          "reason": "PLANNED cartridge scenario has not run or qualified; no physical receipt is claimed."
        }
      }
    },
    {
      "id": "requirement:C-4",
      "required_layers": [
        "MODEL"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "MODEL",
          "description": "PLANNED BINDING [P2.6/P3b.6]. Inject failures at construction, callback, queue, socket and validated-write stages of the production Entry.build composition. PLANNED production test_gen2_client.py/test_gen2_entry.py fault controls; validator unit tests do not substitute for client tests."
        },
        "artifacts": {
          "pokecrystal": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133",
          "pokegold": "d8b8a3600a465308c9953dfa04f0081c05bdcb94",
          "pokesilver": "49b163f7e57702bc939d642a18f591de55d92dae"
        },
        "positive_control": "Successful control completes with ordered cleanup; each injected failure latches refusal and releases owned resources/permits.",
        "refusal_control": "Leaked hook/permit, partial write before validation or reuse of poisoned state after an exception fails.",
        "oracle": "MODEL fault-injection oracle over calls, writes and resource ownership, by construction.",
        "receipt_marker": "gen2.requirement.C-4",
        "lane": "unit"
      },
      "evidence": {
        "MODEL": {
          "status": "OPEN",
          "reason": "No coverage receipt matching this planned binding, current input/artifact pins and declared layer has been accepted."
        }
      }
    },
    {
      "id": "requirement:C-5",
      "required_layers": [
        "SOURCE",
        "PHYSICAL"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "COMMAND",
          "description": "PLANNED BINDING [P3b.3/P6.3 conditional]. With randomized admission disabled, offer unknown/randomized/AP artifacts to Entry.admit. Only a separate enabling ruling may introduce a pinned producer/qualification policy. PLANNED entry negative controls and P6 applicability disposition; no UPR extension or AP runtime admission inferred."
        },
        "artifacts": {
          "pokecrystal": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133",
          "pokegold": "d8b8a3600a465308c9953dfa04f0081c05bdcb94",
          "pokesilver": "49b163f7e57702bc939d642a18f591de55d92dae"
        },
        "positive_control": "Disabled inputs refuse without changing admitted catalog/run state; a later enabled policy must qualify exact artifacts.",
        "refusal_control": "A recognizable title, shared layout, or clean base hash cannot silently admit a randomized/unknown artifact.",
        "oracle": "SERVER admission/refusal and actual hash/anchor result; signed deferred disposition is not randomized-behavior proof.",
        "receipt_marker": "gen2.requirement.C-5",
        "lane": "release-evidence"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "Default-disabled/deferred conditional. Await signed applicability/disposition at P6; an enabled branch requires its declared layers. Disabled status is not a behavior PASS."
        },
        "PHYSICAL": {
          "status": "OPEN",
          "reason": "Default-disabled/deferred conditional. Await signed applicability/disposition at P6; an enabled branch requires its declared layers. Disabled status is not a behavior PASS."
        }
      }
    },
    {
      "id": "requirement:C-6g",
      "required_layers": [
        "SOURCE",
        "PHYSICAL"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "NATURAL",
          "description": "PLANNED BINDING [P3a.1/P3b.7]. Exercise every title/alias pairing and arrival order at hello; run representative C-C and G-S duos plus a physical C-G link, reconnect and persisted-run reload. PLANNED test_gen2_pairing_matrix.py and duo link/reconnect cases; shared foundation mechanism is reused without game_id branches."
        },
        "artifacts": {
          "pokecrystal": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133",
          "pokegold": "d8b8a3600a465308c9953dfa04f0081c05bdcb94",
          "pokesilver": "49b163f7e57702bc939d642a18f591de55d92dae"
        },
        "positive_control": "All Gen2 titles derive foundation gen2_gsc with permitted clean kind; each positive pair links distinct identities.",
        "refusal_control": "Gen1/Gen3 cross-family, unknown/contradictory hello and kind mismatch refuse with state/cache/disk byte-identical.",
        "oracle": "SERVER connection/run persistence and independent duo save identities/PYDEC, including C-G link receipt.",
        "receipt_marker": "gen2.requirement.C-6g",
        "lane": "duo-pairs"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "No coverage receipt matching this planned binding, current input/artifact pins and declared layer has been accepted."
        },
        "PHYSICAL": {
          "status": "OPEN",
          "reason": "PLANNED cartridge scenario has not run or qualified; no physical receipt is claimed."
        }
      }
    },
    {
      "id": "requirement:D-1",
      "required_layers": [
        "SOURCE",
        "PHYSICAL"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "NATURAL",
          "description": "PLANNED BINDING [P2.5/P3b.7]. Catch each side's first eligible encounter in the same permitted area on C-C and G-S; also execute the required C-G link scenario. PLANNED link/boxed_capture scenarios; source area/encounter packs are inputs, not duo proof."
        },
        "artifacts": {
          "pokecrystal": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133",
          "pokegold": "d8b8a3600a465308c9953dfa04f0081c05bdcb94",
          "pokesilver": "49b163f7e57702bc939d642a18f591de55d92dae"
        },
        "positive_control": "Both independently decoded acquired records match one SERVER link with correct area/keys.",
        "refusal_control": "Second ordinary encounter, mismatched area, failed acquisition or identical-full-key pair cannot become a new valid link.",
        "oracle": "SERVER links.json/status plus PYDEC exact party/box records and independent post-result witness.",
        "receipt_marker": "gen2.requirement.D-1",
        "lane": "duo-pairs"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "No coverage receipt matching this planned binding, current input/artifact pins and declared layer has been accepted."
        },
        "PHYSICAL": {
          "status": "OPEN",
          "reason": "PLANNED cartridge scenario has not run or qualified; no physical receipt is claimed."
        }
      }
    },
    {
      "id": "requirement:D-2",
      "required_layers": [
        "SOURCE",
        "PHYSICAL"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "NATURAL",
          "description": "PLANNED BINDING [P3b.7]. Traverse eligible areas and begin encounters before and after Poké Balls are present in the Gen2 Ball pocket. PLANNED ball_gate duo and natural ball-acquisition observation outside the injected-fixture control."
        },
        "artifacts": {
          "pokecrystal": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133",
          "pokegold": "d8b8a3600a465308c9953dfa04f0081c05bdcb94",
          "pokesilver": "49b163f7e57702bc939d642a18f591de55d92dae"
        },
        "positive_control": "The game/ledger enables the catch rule only when the Ball-pocket witness establishes balls.",
        "refusal_control": "Items-pocket lookalikes, zero count or stale has_pokeballs after consumption must not open the gate.",
        "oracle": "SERVER area/ball gate plus independent Ball-pocket bytes; O-10 fixture injection is identified as a fixture exception.",
        "receipt_marker": "gen2.requirement.D-2",
        "lane": "duo-pairs"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "No coverage receipt matching this planned binding, current input/artifact pins and declared layer has been accepted."
        },
        "PHYSICAL": {
          "status": "OPEN",
          "reason": "PLANNED cartridge scenario has not run or qualified; no physical receipt is claimed."
        }
      }
    },
    {
      "id": "requirement:D-3",
      "required_layers": [
        "SOURCE",
        "PHYSICAL"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "NATURAL",
          "description": "PLANNED BINDING [P3b.7]. Deposit, withdraw, release and ChangeBox from native PCs on both linked instances; let peer sync complete through its command gate. PLANNED pc_ops/changebox duos with separate natural-operation and commanded-sync receipts."
        },
        "artifacts": {
          "pokecrystal": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133",
          "pokegold": "d8b8a3600a465308c9953dfa04f0081c05bdcb94",
          "pokesilver": "49b163f7e57702bc939d642a18f591de55d92dae"
        },
        "positive_control": "Keys and records remain linked and appear exactly once in the expected party/active/backing box positions.",
        "refusal_control": "Canceled PC operations, unknown keys, active-Box14 memorial hazard or a missing sync ack must not silently succeed.",
        "oracle": "PYDEC independent before/after and saved readback across both cartridges, with SERVER sync events.",
        "receipt_marker": "gen2.requirement.D-3",
        "lane": "duo-pairs"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "No coverage receipt matching this planned binding, current input/artifact pins and declared layer has been accepted."
        },
        "PHYSICAL": {
          "status": "OPEN",
          "reason": "PLANNED cartridge scenario has not run or qualified; no physical receipt is claimed."
        }
      }
    },
    {
      "id": "requirement:D-5",
      "required_layers": [
        "SOURCE",
        "PHYSICAL"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "NATURAL",
          "description": "PLANNED BINDING [P3b.7]. Drive species, gender and type clause collisions/non-collisions and shiny bonus acquisitions using each title's source-derived facts. PLANNED species_clause/gender_clause/type_clause/shiny_bonus duos; no collision-rate claim."
        },
        "artifacts": {
          "pokecrystal": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133",
          "pokegold": "d8b8a3600a465308c9953dfa04f0081c05bdcb94",
          "pokesilver": "49b163f7e57702bc939d642a18f591de55d92dae"
        },
        "positive_control": "Allowed controls pair normally; each applicable clause blocks only its declared conflict; shiny bonus uses actual DV result.",
        "refusal_control": "Wrong evolution family/type/gender, false shiny flag or unsupported clause inference must fail the link decision comparison.",
        "oracle": "SERVER clause/link outcomes plus independent species/type/DV derivation and acquired-record identities.",
        "receipt_marker": "gen2.requirement.D-5",
        "lane": "duo-pairs"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "No coverage receipt matching this planned binding, current input/artifact pins and declared layer has been accepted."
        },
        "PHYSICAL": {
          "status": "OPEN",
          "reason": "PLANNED cartridge scenario has not run or qualified; no physical receipt is claimed."
        }
      }
    },
    {
      "id": "requirement:D-6",
      "required_layers": [
        "SOURCE",
        "PHYSICAL"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "NATURAL",
          "description": "PLANNED BINDING [P3b.7]. Naturally faint a linked mon while its partner is benched, then while its partner is active in battle; include poison-driven source faint. PLANNED linked_faint_bench/linked_faint_active/poison duos."
        },
        "artifacts": {
          "pokecrystal": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133",
          "pokegold": "d8b8a3600a465308c9953dfa04f0081c05bdcb94",
          "pokesilver": "49b163f7e57702bc939d642a18f591de55d92dae"
        },
        "positive_control": "Peer death reaches the correct identity at the appropriate qualified write window in both cases.",
        "refusal_control": "Opponent/unlinked faint, self-forced echo or wrong-key propagation cannot kill another record or create a feedback loop.",
        "oracle": "PYDEC independent HP/identity snapshots on both cartridges, ENGINE source event and SERVER command timeline.",
        "receipt_marker": "gen2.requirement.D-6",
        "lane": "duo-pairs"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "No coverage receipt matching this planned binding, current input/artifact pins and declared layer has been accepted."
        },
        "PHYSICAL": {
          "status": "OPEN",
          "reason": "PLANNED cartridge scenario has not run or qualified; no physical receipt is claimed."
        }
      }
    },
    {
      "id": "requirement:D-7",
      "required_layers": [
        "SOURCE",
        "PHYSICAL"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "NATURAL",
          "description": "PLANNED BINDING [P3b.7]. Naturally wipe one side's previously alive linked party, then observe shared whiteout/rebuild on both sides through healing/save transitions. PLANNED whiteout duo, including bad timing/refusal controls."
        },
        "artifacts": {
          "pokecrystal": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133",
          "pokegold": "d8b8a3600a465308c9953dfa04f0081c05bdcb94",
          "pokesilver": "49b163f7e57702bc939d642a18f591de55d92dae"
        },
        "positive_control": "Exactly one whiteout produces the prescribed rebuilt state and preserved linkage/identity constraints.",
        "refusal_control": "A loaded all-zero/borrowed party, duplicate callback or after-heal-only observation must not pass as a real whiteout.",
        "oracle": "PYDEC pre-heal, post-rebuild and durable save records on both sides with SERVER state.",
        "receipt_marker": "gen2.requirement.D-7",
        "lane": "duo-pairs"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "No coverage receipt matching this planned binding, current input/artifact pins and declared layer has been accepted."
        },
        "PHYSICAL": {
          "status": "OPEN",
          "reason": "PLANNED cartridge scenario has not run or qualified; no physical receipt is claimed."
        }
      }
    },
    {
      "id": "requirement:D-11",
      "required_layers": [
        "SOURCE",
        "PHYSICAL"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "COMMAND",
          "description": "PLANNED BINDING [P3b.7/P6.3 conditional]. Keep rival swap/Explode disabled unless separately enabled; verify neither is dispatched during representative duos. On enablement, bind each game's qualified command and post-result oracle first. PLANNED conditional duo scenarios under P3b.7; no enabled behavior PASS is inferred from refusal."
        },
        "artifacts": {
          "pokecrystal": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133",
          "pokegold": "d8b8a3600a465308c9953dfa04f0081c05bdcb94",
          "pokesilver": "49b163f7e57702bc939d642a18f591de55d92dae"
        },
        "positive_control": "Disabled disposition leaves ordinary duo behavior unchanged; enabled scenarios must prove exact cross-side outcomes.",
        "refusal_control": "Unsupported capability, B-25-unresolved rival payload or an inferred enabled state must refuse without corruption.",
        "oracle": "GAME native effects and independent duo records when enabled; signed disabled/deferred applicability otherwise.",
        "receipt_marker": "gen2.requirement.D-11",
        "lane": "release-evidence"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "Default-disabled/deferred conditional. Await signed applicability/disposition at P6; an enabled branch requires its declared layers. Disabled status is not a behavior PASS."
        },
        "PHYSICAL": {
          "status": "OPEN",
          "reason": "Default-disabled/deferred conditional. Await signed applicability/disposition at P6; an enabled branch requires its declared layers. Disabled status is not a behavior PASS."
        }
      }
    },
    {
      "id": "requirement:D-12",
      "required_layers": [
        "SOURCE",
        "PHYSICAL"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "COMMAND",
          "description": "PLANNED BINDING [P3b.6/P3b.7/P6.3]. Cause game_over and measure its transient HUD state while ticks continue, across reset/reconnect boundaries defined by the client contract. PLANNED game-over client MODEL cases and physical duo overlay measurement; no inherited limit."
        },
        "artifacts": {
          "pokecrystal": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133",
          "pokegold": "d8b8a3600a465308c9953dfa04f0081c05bdcb94",
          "pokesilver": "49b163f7e57702bc939d642a18f591de55d92dae"
        },
        "positive_control": "The actual displayed game-over state is captured at the required frames while periodic communication continues.",
        "refusal_control": "A missing/expired overlay, stopped ticks or reuse of a Gen1 MODEL limitation cannot close the Gen2 GAME claim.",
        "oracle": "GAME transient overlay capture plus wire timeline; an explicit owner-signed recorded limit is a separate non-behavior disposition.",
        "receipt_marker": "gen2.requirement.D-12",
        "lane": "duo-pairs"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "No coverage receipt matching this planned binding, current input/artifact pins and declared layer has been accepted."
        },
        "PHYSICAL": {
          "status": "OPEN",
          "reason": "PLANNED cartridge scenario has not run or qualified; no physical receipt is claimed."
        }
      }
    },
    {
      "id": "requirement:D-13",
      "required_layers": [
        "MODEL"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "MODEL",
          "description": "PLANNED BINDING [P2.6/P3b.6]. Construct two records with the identical full Gen2 mon key and exercise both resolver lookup paths and capture linking on the production graph. PLANNED test_gen2_client.py identity/collision cases; fixed MODEL-only-by-design obligation."
        },
        "artifacts": {
          "pokecrystal": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133",
          "pokegold": "d8b8a3600a465308c9953dfa04f0081c05bdcb94",
          "pokesilver": "49b163f7e57702bc939d642a18f591de55d92dae"
        },
        "positive_control": "A distinct-key control resolves uniquely and accepts the valid capture.",
        "refusal_control": "Ambiguous lookup must write nothing on either path and colliding capture must be refused without cache/state mutation.",
        "oracle": "MODEL refusal/zero-write snapshots of both resolver paths and server state; no cartridge collision-frequency assertion.",
        "receipt_marker": "gen2.requirement.D-13",
        "lane": "unit"
      },
      "evidence": {
        "MODEL": {
          "status": "OPEN",
          "reason": "No coverage receipt matching this planned binding, current input/artifact pins and declared layer has been accepted."
        }
      }
    },
    {
      "id": "requirement:D-14",
      "required_layers": [
        "SOURCE",
        "PHYSICAL"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "NATURAL",
          "description": "PLANNED BINDING [P3b.7]. Create real links, disconnect either side, reconnect in both orders and reload the persisted run with the same saves. PLANNED reconnect duo with same-save/wrong-save/WRAM-clear controls."
        },
        "artifacts": {
          "pokecrystal": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133",
          "pokegold": "d8b8a3600a465308c9953dfa04f0081c05bdcb94",
          "pokesilver": "49b163f7e57702bc939d642a18f591de55d92dae"
        },
        "positive_control": "Link identities, status and acquired records remain consistent after reconnect/reload.",
        "refusal_control": "Wrong save/OT, cleared WRAM or a stale alias cannot attach to the existing link or mutate its persisted state.",
        "oracle": "SERVER persisted links/status plus PYDEC/OT of the actual per-instance saves.",
        "receipt_marker": "gen2.requirement.D-14",
        "lane": "duo-pairs"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "No coverage receipt matching this planned binding, current input/artifact pins and declared layer has been accepted."
        },
        "PHYSICAL": {
          "status": "OPEN",
          "reason": "PLANNED cartridge scenario has not run or qualified; no physical receipt is claimed."
        }
      }
    },
    {
      "id": "requirement:T-1",
      "required_layers": [
        "SOURCE",
        "PHYSICAL"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "NATURAL",
          "description": "PLANNED BINDING [P4.3]. On each qualified companion overlay, approach every supported Center receptionist and enter the native Trade Center route with the partner available. PLANNED patch/gen2/src/trade_*.asm, lua/gen2/trade_overlay.lua and test_gen2_trade_gates.py; overlay pins required."
        },
        "artifacts": {
          "crystal_overlay": {
            "state": "PLANNED",
            "digest": null,
            "base_artifact": "pokecrystal",
            "base_digest": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133"
          },
          "gold_overlay": {
            "state": "PLANNED",
            "digest": null,
            "base_artifact": "pokegold",
            "base_digest": "d8b8a3600a465308c9953dfa04f0081c05bdcb94"
          },
          "silver_overlay": {
            "state": "PLANNED",
            "digest": null,
            "base_artifact": "pokesilver",
            "base_digest": "49b163f7e57702bc939d642a18f591de55d92dae"
          }
        },
        "positive_control": "Cartridge-native receptionist/menu flow opens the correct shared trade session at every enumerated Center.",
        "refusal_control": "Wrong receptionist/context, missed mailbox window or unavailable partner cannot create a partial native/shared trade.",
        "oracle": "GAME native text/menu/entry plus SERVER session timeline and qualified mailbox receipt.",
        "receipt_marker": "gen2.requirement.T-1",
        "lane": "live-trade-gates"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "No coverage receipt matching this planned binding, current input/artifact pins and declared layer has been accepted."
        },
        "PHYSICAL": {
          "status": "OPEN",
          "reason": "PLANNED cartridge scenario has not run or qualified; no physical receipt is claimed."
        }
      }
    },
    {
      "id": "requirement:T-2",
      "required_layers": [
        "SOURCE",
        "PHYSICAL"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "NATURAL",
          "description": "PLANNED BINDING [P4.3]. Use the cartridge's own YES/NO prompt on both sides, including partner decline, cancellation, timeout and reset before commit. PLANNED native trade_new/trade_decline_new/timeout/reset controls on qualified overlay pairings."
        },
        "artifacts": {
          "crystal_overlay": {
            "state": "PLANNED",
            "digest": null,
            "base_artifact": "pokecrystal",
            "base_digest": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133"
          },
          "gold_overlay": {
            "state": "PLANNED",
            "digest": null,
            "base_artifact": "pokegold",
            "base_digest": "d8b8a3600a465308c9953dfa04f0081c05bdcb94"
          },
          "silver_overlay": {
            "state": "PLANNED",
            "digest": null,
            "base_artifact": "pokesilver",
            "base_digest": "49b163f7e57702bc939d642a18f591de55d92dae"
          }
        },
        "positive_control": "YES completes the agreed phase; decline/cancel/timeout releases both sides to a consistent idle state.",
        "refusal_control": "Any negative path must leave both saved records unchanged and must not acknowledge commit.",
        "oracle": "GAME native prompt state plus PYDEC/hash of both saves before/after and SERVER trade token state.",
        "receipt_marker": "gen2.requirement.T-2",
        "lane": "live-trade-gates"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "No coverage receipt matching this planned binding, current input/artifact pins and declared layer has been accepted."
        },
        "PHYSICAL": {
          "status": "OPEN",
          "reason": "PLANNED cartridge scenario has not run or qualified; no physical receipt is claimed."
        }
      }
    },
    {
      "id": "requirement:T-3",
      "required_layers": [
        "SOURCE",
        "PHYSICAL"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "COMMAND",
          "description": "PLANNED BINDING [P4.3]. Commit a native shared trade with distinct records and held items, including applicable trade evolution; deliver an invalid held-item payload as the refusal control. PLANNED apply_trade native overlay path, held-item validation and C-C/G-S trade duos."
        },
        "artifacts": {
          "crystal_overlay": {
            "state": "PLANNED",
            "digest": null,
            "base_artifact": "pokecrystal",
            "base_digest": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133"
          },
          "gold_overlay": {
            "state": "PLANNED",
            "digest": null,
            "base_artifact": "pokegold",
            "base_digest": "d8b8a3600a465308c9953dfa04f0081c05bdcb94"
          },
          "silver_overlay": {
            "state": "PLANNED",
            "digest": null,
            "base_artifact": "pokesilver",
            "base_digest": "49b163f7e57702bc939d642a18f591de55d92dae"
          }
        },
        "positive_control": "Both sides receive the exact intended record, valid held item and source-correct evolution result.",
        "refusal_control": "Invalid item, stale token/key/slot or malformed blob refuses before either commit; no partial one-sided success.",
        "oracle": "PYDEC independent exact-record and held-item readback on both sides, with native trade evolution result.",
        "receipt_marker": "gen2.requirement.T-3",
        "lane": "live-trade-gates"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "No coverage receipt matching this planned binding, current input/artifact pins and declared layer has been accepted."
        },
        "PHYSICAL": {
          "status": "OPEN",
          "reason": "PLANNED cartridge scenario has not run or qualified; no physical receipt is claimed."
        }
      }
    },
    {
      "id": "requirement:T-4",
      "required_layers": [
        "SOURCE",
        "PHYSICAL"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "NATURAL",
          "description": "PLANNED BINDING [P4.3]. After successful native trade, capture SaveAfterLinkTrade plus the scenario save, cold-reload both hosts and decode the resulting saves. PLANNED trade persistence witness and independent host reload; routine return alone is not durability."
        },
        "artifacts": {
          "crystal_overlay": {
            "state": "PLANNED",
            "digest": null,
            "base_artifact": "pokecrystal",
            "base_digest": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133"
          },
          "gold_overlay": {
            "state": "PLANNED",
            "digest": null,
            "base_artifact": "pokegold",
            "base_digest": "d8b8a3600a465308c9953dfa04f0081c05bdcb94"
          },
          "silver_overlay": {
            "state": "PLANNED",
            "digest": null,
            "base_artifact": "pokesilver",
            "base_digest": "49b163f7e57702bc939d642a18f591de55d92dae"
          }
        },
        "positive_control": "Reloaded OT/DVs/identity, species/evolution, nickname and held item match the transferred records, not just species.",
        "refusal_control": "A same-species substitute, missing save witness, stale pretrade save or wrong host SaveRAM directory must fail.",
        "oracle": "GAME CONTINUE/load success plus PYDEC exact transferred-record persistence on both saves.",
        "receipt_marker": "gen2.requirement.T-4",
        "lane": "live-trade-gates"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "No coverage receipt matching this planned binding, current input/artifact pins and declared layer has been accepted."
        },
        "PHYSICAL": {
          "status": "OPEN",
          "reason": "PLANNED cartridge scenario has not run or qualified; no physical receipt is claimed."
        }
      }
    },
    {
      "id": "requirement:N-1",
      "required_layers": [
        "SOURCE",
        "PHYSICAL"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "NATURAL",
          "description": "PLANNED BINDING [P4.1]. Open START-menu SLINK on each qualified overlay and navigate every specified panel row while partner/run state changes. PLANNED panel patch, lua/gen2/panel.lua and panel rows in test_gen2_trade_gates.py; overlay pins required."
        },
        "artifacts": {
          "crystal_overlay": {
            "state": "PLANNED",
            "digest": null,
            "base_artifact": "pokecrystal",
            "base_digest": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133"
          },
          "gold_overlay": {
            "state": "PLANNED",
            "digest": null,
            "base_artifact": "pokegold",
            "base_digest": "d8b8a3600a465308c9953dfa04f0081c05bdcb94"
          },
          "silver_overlay": {
            "state": "PLANNED",
            "digest": null,
            "base_artifact": "pokesilver",
            "base_digest": "49b163f7e57702bc939d642a18f591de55d92dae"
          }
        },
        "positive_control": "Native tilemap shows the exact sanitized row values and refreshes within the qualified mailbox lifecycle.",
        "refusal_control": "Unsafe/occupied mailbox, overlong text, invalid row or transition/reset cannot corrupt saved regions or leave stale ownership.",
        "oracle": "GAME panel tilemap/row observations plus transient mailbox and save-region exclusion receipts.",
        "receipt_marker": "gen2.requirement.N-1",
        "lane": "live-gates"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "No coverage receipt matching this planned binding, current input/artifact pins and declared layer has been accepted."
        },
        "PHYSICAL": {
          "status": "OPEN",
          "reason": "PLANNED cartridge scenario has not run or qualified; no physical receipt is claimed."
        }
      }
    },
    {
      "id": "requirement:N-2",
      "required_layers": [
        "SOURCE",
        "PHYSICAL"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "COMMAND",
          "description": "PLANNED BINDING [P4.2]. Request each Gen2-bound sound at the independently qualified main-thread service sites during movement, idle START, text, battle and transitions, then reset with a pending request. PLANNED ticket-16 patch sites and test_gen2_trade_gates.py sound rows; no IRQ execution assumed."
        },
        "artifacts": {
          "crystal_overlay": {
            "state": "PLANNED",
            "digest": null,
            "base_artifact": "pokecrystal",
            "base_digest": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133"
          },
          "gold_overlay": {
            "state": "PLANNED",
            "digest": null,
            "base_artifact": "pokegold",
            "base_digest": "d8b8a3600a465308c9953dfa04f0081c05bdcb94"
          },
          "silver_overlay": {
            "state": "PLANNED",
            "digest": null,
            "base_artifact": "pokesilver",
            "base_digest": "49b163f7e57702bc939d642a18f591de55d92dae"
          }
        },
        "positive_control": "Every valid request is consumed and audible/observable by its bounded deadline with correct busy semantics.",
        "refusal_control": "Wrong context/bank, already-busy channel, duplicate request or reset-pending request must not execute stale/unsafe sound code.",
        "oracle": "GAME native sound/consumption timing plus caller/context ABI, bank/register preservation and reset receipts.",
        "receipt_marker": "gen2.requirement.N-2",
        "lane": "live-gates"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "No coverage receipt matching this planned binding, current input/artifact pins and declared layer has been accepted."
        },
        "PHYSICAL": {
          "status": "OPEN",
          "reason": "PLANNED cartridge scenario has not run or qualified; no physical receipt is claimed."
        }
      }
    },
    {
      "id": "requirement:N-3",
      "required_layers": [
        "SOURCE",
        "PHYSICAL"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "NATURAL",
          "description": "PLANNED BINDING [P5.1/P5.2/P5.3 after G6]. After shipped G6 and the separately authorized ghost design, move the partner through tiles/maps, enter battle and save/reload while ghost lifecycle transitions. PLANNED test_gen2_ghost_gates.py and ghost overlay artifacts; its future live lane must be registered for G5."
        },
        "artifacts": {
          "crystal_ghost": {
            "state": "PLANNED",
            "digest": null,
            "base_artifact": "pokecrystal",
            "base_digest": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133"
          },
          "gold_ghost": {
            "state": "PLANNED",
            "digest": null,
            "base_artifact": "pokegold",
            "base_digest": "d8b8a3600a465308c9953dfa04f0081c05bdcb94"
          },
          "silver_ghost": {
            "state": "PLANNED",
            "digest": null,
            "base_artifact": "pokesilver",
            "base_digest": "49b163f7e57702bc939d642a18f591de55d92dae"
          }
        },
        "positive_control": "Ghost tracks partner coordinates/sprite/palette and suspends in battle without persistent-object leakage.",
        "refusal_control": "No collision/wild-encounter participation or saved ghost object may survive transitions/reload; infeasible/unapproved design cannot run.",
        "oracle": "GAME transient sprite/position receipts and independent object/save-region exclusion; first-RC disposition remains post-RC, not behavior PASS.",
        "receipt_marker": "gen2.requirement.N-3",
        "lane": "release-evidence"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "Post-RC O-13: later ghost/G5 obligation after G6 and authorized design. No ghost behavior receipt or signed disposition is fabricated."
        },
        "PHYSICAL": {
          "status": "OPEN",
          "reason": "Post-RC O-13: later ghost/G5 obligation after G6 and authorized design. No ghost behavior receipt or signed disposition is fabricated."
        }
      }
    },
    {
      "id": "protocol:9.1",
      "required_layers": [
        "SOURCE",
        "PHYSICAL"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "COMMAND",
          "description": "PLANNED BINDING [P3a.2/P3b.3a/P3b.6/P3b.7]. Trigger hello, tick and representative events on a running client and capture the exact TCP byte stream, with parallel production-graph fake-socket conformance. PLANNED: production Entry.build conformance in tests/unit/test_gen2_client.py/test_protocol_conformance.py; tests/live/test_gen2_new_gates.py and tests/e2e/test_duo_gen2_new.py cases as the selected lane requires."
        },
        "artifacts": {
          "pokecrystal": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133",
          "pokegold": "d8b8a3600a465308c9953dfa04f0081c05bdcb94",
          "pokesilver": "49b163f7e57702bc939d642a18f591de55d92dae"
        },
        "positive_control": "Every event is one JSON object followed by exactly one LF, with event:string, player:a|b and integer seq.",
        "refusal_control": "Malformed type, embedded extra line, missing LF or double LF fails the independent framing/parser oracle.",
        "oracle": "Independent wire-byte decoder and SERVER capture; fake socket remains MODEL.",
        "receipt_marker": "gen2.protocol.9.1",
        "lane": "live-new-gates"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "No coverage receipt matching this planned binding, current input/artifact pins and declared layer has been accepted."
        },
        "PHYSICAL": {
          "status": "OPEN",
          "reason": "PLANNED cartridge scenario has not run or qualified; no physical receipt is claimed."
        }
      }
    },
    {
      "id": "protocol:9.2",
      "required_layers": [
        "SOURCE",
        "PHYSICAL"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "NATURAL",
          "description": "PLANNED BINDING [P3a.2/P3b.3a/P3b.6/P3b.7]. Start a fresh production client, emit several events, disconnect/reconnect TCP without reloading the script, then reload the script separately. PLANNED: production Entry.build conformance in tests/unit/test_gen2_client.py/test_protocol_conformance.py; tests/live/test_gen2_new_gates.py and tests/e2e/test_duo_gen2_new.py cases as the selected lane requires."
        },
        "artifacts": {
          "pokecrystal": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133",
          "pokegold": "d8b8a3600a465308c9953dfa04f0081c05bdcb94",
          "pokesilver": "49b163f7e57702bc939d642a18f591de55d92dae"
        },
        "positive_control": "Seq begins at1 only on script load and increments by exactly1 for every sent event across reconnect.",
        "refusal_control": "Resetting seq on TCP reconnect, gaps, repeats or a noninteger fails the wire sequence comparison.",
        "oracle": "Independent per-script/per-connection wire timeline; script reload and reconnect are distinct stimuli.",
        "receipt_marker": "gen2.protocol.9.2",
        "lane": "duo-pairs"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "No coverage receipt matching this planned binding, current input/artifact pins and declared layer has been accepted."
        },
        "PHYSICAL": {
          "status": "OPEN",
          "reason": "PLANNED cartridge scenario has not run or qualified; no physical receipt is claimed."
        }
      }
    },
    {
      "id": "protocol:9.3",
      "required_layers": [
        "SOURCE",
        "PHYSICAL"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "COMMAND",
          "description": "PLANNED BINDING [P3a.2/P3b.3a/P3b.6/P3b.7]. Keep the production connector disconnected while normal event sources fire; then establish TCP and inspect first and subsequent writes. PLANNED: production Entry.build conformance in tests/unit/test_gen2_client.py/test_protocol_conformance.py; tests/live/test_gen2_new_gates.py and tests/e2e/test_duo_gen2_new.py cases as the selected lane requires."
        },
        "artifacts": {
          "pokecrystal": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133",
          "pokegold": "d8b8a3600a465308c9953dfa04f0081c05bdcb94",
          "pokesilver": "49b163f7e57702bc939d642a18f591de55d92dae"
        },
        "positive_control": "Disconnected phase writes zero bytes; connect begins with current hello rather than a backlog.",
        "refusal_control": "Any buffered stale event replayed on connect or a send while C.connected()==false fails.",
        "oracle": "Independent socket write log tied to connector state, supplemented by production-graph MODEL calls.",
        "receipt_marker": "gen2.protocol.9.3",
        "lane": "live-new-gates"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "No coverage receipt matching this planned binding, current input/artifact pins and declared layer has been accepted."
        },
        "PHYSICAL": {
          "status": "OPEN",
          "reason": "PLANNED cartridge scenario has not run or qualified; no physical receipt is claimed."
        }
      }
    },
    {
      "id": "protocol:9.4",
      "required_layers": [
        "SOURCE",
        "PHYSICAL"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "NATURAL",
          "description": "PLANNED BINDING [P3a.2/P3b.3a/P3b.6/P3b.7]. Connect and reconnect each side in both arrival orders during a valid run. PLANNED: production Entry.build conformance in tests/unit/test_gen2_client.py/test_protocol_conformance.py; tests/live/test_gen2_new_gates.py and tests/e2e/test_duo_gen2_new.py cases as the selected lane requires."
        },
        "artifacts": {
          "pokecrystal": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133",
          "pokegold": "d8b8a3600a465308c9953dfa04f0081c05bdcb94",
          "pokesilver": "49b163f7e57702bc939d642a18f591de55d92dae"
        },
        "positive_control": "The first outbound line on every connection is hello before tick/area/faint events.",
        "refusal_control": "Force an event source during connection setup; any non-hello first line fails.",
        "oracle": "SERVER receive order and raw wire capture with connection boundaries.",
        "receipt_marker": "gen2.protocol.9.4",
        "lane": "duo-pairs"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "No coverage receipt matching this planned binding, current input/artifact pins and declared layer has been accepted."
        },
        "PHYSICAL": {
          "status": "OPEN",
          "reason": "PLANNED cartridge scenario has not run or qualified; no physical receipt is claimed."
        }
      }
    },
    {
      "id": "protocol:9.5",
      "required_layers": [
        "SOURCE",
        "PHYSICAL"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "COMMAND",
          "description": "PLANNED BINDING [P3a.2/P3b.3a/P3b.6/P3b.7]. Reply to each representative event with commands:[noop] and with an unknown command name while the running client continues ticking. PLANNED: production Entry.build conformance in tests/unit/test_gen2_client.py/test_protocol_conformance.py; tests/live/test_gen2_new_gates.py and tests/e2e/test_duo_gen2_new.py cases as the selected lane requires."
        },
        "artifacts": {
          "pokecrystal": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133",
          "pokegold": "d8b8a3600a465308c9953dfa04f0081c05bdcb94",
          "pokesilver": "49b163f7e57702bc939d642a18f591de55d92dae"
        },
        "positive_control": "Known noop and unknown-command replies are tolerated; expected diagnostic occurs and later valid commands still work.",
        "refusal_control": "Malformed dispatch path must not crash the client or stop periodic ticks; unknown command must not write game state.",
        "oracle": "Wire liveness and independent write log/diagnostic capture; positive subsequent command control.",
        "receipt_marker": "gen2.protocol.9.5",
        "lane": "live-new-gates"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "No coverage receipt matching this planned binding, current input/artifact pins and declared layer has been accepted."
        },
        "PHYSICAL": {
          "status": "OPEN",
          "reason": "PLANNED cartridge scenario has not run or qualified; no physical receipt is claimed."
        }
      }
    },
    {
      "id": "protocol:9.6",
      "required_layers": [
        "SOURCE",
        "PHYSICAL"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "COMMAND",
          "description": "PLANNED BINDING [P3a.2/P3b.3a/P3b.6/P3b.7]. Feed a server reply line just above4MiB followed by a valid bounded line; also exercise a line at the accepted limit. PLANNED: production Entry.build conformance in tests/unit/test_gen2_client.py/test_protocol_conformance.py; tests/live/test_gen2_new_gates.py and tests/e2e/test_duo_gen2_new.py cases as the selected lane requires."
        },
        "artifacts": {
          "pokecrystal": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133",
          "pokegold": "d8b8a3600a465308c9953dfa04f0081c05bdcb94",
          "pokesilver": "49b163f7e57702bc939d642a18f591de55d92dae"
        },
        "positive_control": "Oversize line is discarded and the following valid line is parsed and acted on exactly once.",
        "refusal_control": "A stuck parser, partial oversize-command execution, or lost following line fails.",
        "oracle": "Independent server byte stream, client command effect/write log and continuing tick trace.",
        "receipt_marker": "gen2.protocol.9.6",
        "lane": "live-new-gates"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "No coverage receipt matching this planned binding, current input/artifact pins and declared layer has been accepted."
        },
        "PHYSICAL": {
          "status": "OPEN",
          "reason": "PLANNED cartridge scenario has not run or qualified; no physical receipt is claimed."
        }
      }
    },
    {
      "id": "protocol:9.7",
      "required_layers": [
        "SOURCE",
        "PHYSICAL"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "COMMAND",
          "description": "PLANNED BINDING [P3a.2/P3b.3a/P3b.6/P3b.7]. Send semantically identical replies with reordered JSON fields and reordered object keys, preserving command-array order. PLANNED: production Entry.build conformance in tests/unit/test_gen2_client.py/test_protocol_conformance.py; tests/live/test_gen2_new_gates.py and tests/e2e/test_duo_gen2_new.py cases as the selected lane requires."
        },
        "artifacts": {
          "pokecrystal": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133",
          "pokegold": "d8b8a3600a465308c9953dfa04f0081c05bdcb94",
          "pokesilver": "49b163f7e57702bc939d642a18f591de55d92dae"
        },
        "positive_control": "Both field-order variants produce identical intended command outcomes.",
        "refusal_control": "A parser dependent on field order or one that reorders command-array execution fails.",
        "oracle": "Independent resulting state/write sequence and wire acknowledgements; array order is not treated as object-field order.",
        "receipt_marker": "gen2.protocol.9.7",
        "lane": "live-new-gates"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "No coverage receipt matching this planned binding, current input/artifact pins and declared layer has been accepted."
        },
        "PHYSICAL": {
          "status": "OPEN",
          "reason": "PLANNED cartridge scenario has not run or qualified; no physical receipt is claimed."
        }
      }
    },
    {
      "id": "protocol:9.8",
      "required_layers": [
        "SOURCE",
        "PHYSICAL"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "NATURAL",
          "description": "PLANNED BINDING [P3a.2/P3b.3a/P3b.6/P3b.7]. Boot every selected title/save and inspect hello on initial connect and reconnect. PLANNED: production Entry.build conformance in tests/unit/test_gen2_client.py/test_protocol_conformance.py; tests/live/test_gen2_new_gates.py and tests/e2e/test_duo_gen2_new.py cases as the selected lane requires."
        },
        "artifacts": {
          "pokecrystal": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133",
          "pokegold": "d8b8a3600a465308c9953dfa04f0081c05bdcb94",
          "pokesilver": "49b163f7e57702bc939d642a18f591de55d92dae"
        },
        "positive_control": "rom_type resolves through the declared registry; party/list, balls/bool, trainer/string and badge bitmask have correct types and values.",
        "refusal_control": "Unknown alias, count substituted for badge bitmask, or a missing/mistyped field fails.",
        "oracle": "SERVER hello decoder plus independent save/name/bag/badge readback; all Gen2 aliases are covered by pairing tests.",
        "receipt_marker": "gen2.protocol.9.8",
        "lane": "live-new-gates"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "No coverage receipt matching this planned binding, current input/artifact pins and declared layer has been accepted."
        },
        "PHYSICAL": {
          "status": "OPEN",
          "reason": "PLANNED cartridge scenario has not run or qualified; no physical receipt is claimed."
        }
      }
    },
    {
      "id": "protocol:9.9",
      "required_layers": [
        "SOURCE",
        "PHYSICAL"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "NATURAL",
          "description": "PLANNED BINDING [P3a.2/P3b.3a/P3b.6/P3b.7]. Hello with empty, partial and full valid parties across titles; inspect every emitted mon entry and encoded blob. PLANNED: production Entry.build conformance in tests/unit/test_gen2_client.py/test_protocol_conformance.py; tests/live/test_gen2_new_gates.py and tests/e2e/test_duo_gen2_new.py cases as the selected lane requires."
        },
        "artifacts": {
          "pokecrystal": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133",
          "pokegold": "d8b8a3600a465308c9953dfa04f0081c05bdcb94",
          "pokesilver": "49b163f7e57702bc939d642a18f591de55d92dae"
        },
        "positive_control": "Key, HP/maxHP, level, slot, species_id, nickname and blob_hex correspond to the exact record; hex length is2*adapter.party_blob_size().",
        "refusal_control": "Truncated/oversized blob, wrong slot or stale key/value must fail schema/independent decode.",
        "oracle": "Independent PYDEC and wire schema using the current adapter's declared blob size, never a copied Gen3 length.",
        "receipt_marker": "gen2.protocol.9.9",
        "lane": "live-new-gates"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "No coverage receipt matching this planned binding, current input/artifact pins and declared layer has been accepted."
        },
        "PHYSICAL": {
          "status": "OPEN",
          "reason": "PLANNED cartridge scenario has not run or qualified; no physical receipt is claimed."
        }
      }
    },
    {
      "id": "protocol:9.10",
      "required_layers": [
        "SOURCE",
        "PHYSICAL"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "NATURAL",
          "description": "PLANNED BINDING [P3a.2/P3b.3a/P3b.6/P3b.7]. Connect known-OT saves and inspect hello.ot_id; separately exercise the protocol's legacy fallback in a MODEL control. PLANNED: production Entry.build conformance in tests/unit/test_gen2_client.py/test_protocol_conformance.py; tests/live/test_gen2_new_gates.py and tests/e2e/test_duo_gen2_new.py cases as the selected lane requires."
        },
        "artifacts": {
          "pokecrystal": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133",
          "pokegold": "d8b8a3600a465308c9953dfa04f0081c05bdcb94",
          "pokesilver": "49b163f7e57702bc939d642a18f591de55d92dae"
        },
        "positive_control": "Gen2 sends the actual OT id; any deliberately tested omission must derive the same id from the full first-party key.",
        "refusal_control": "Wrong OT or ambiguous/empty-key fallback cannot bind the save to an existing run.",
        "oracle": "Independent saved trainer-id decode plus SERVER identity check; Gen2's explicit OT path is the planned production behavior.",
        "receipt_marker": "gen2.protocol.9.10",
        "lane": "live-new-gates"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "No coverage receipt matching this planned binding, current input/artifact pins and declared layer has been accepted."
        },
        "PHYSICAL": {
          "status": "OPEN",
          "reason": "PLANNED cartridge scenario has not run or qualified; no physical receipt is claimed."
        }
      }
    },
    {
      "id": "protocol:9.11",
      "required_layers": [
        "SOURCE",
        "PHYSICAL"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "NATURAL",
          "description": "PLANNED BINDING [P3a.2/P3b.3a/P3b.6/P3b.7]. Observe hello at title/unloaded state and during a source-confirmed borrowed-party context, then return to a valid owned party. PLANNED: production Entry.build conformance in tests/unit/test_gen2_client.py/test_protocol_conformance.py; tests/live/test_gen2_new_gates.py and tests/e2e/test_duo_gen2_new.py cases as the selected lane requires."
        },
        "artifacts": {
          "pokecrystal": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133",
          "pokegold": "d8b8a3600a465308c9953dfa04f0081c05bdcb94",
          "pokesilver": "49b163f7e57702bc939d642a18f591de55d92dae"
        },
        "positive_control": "Unloaded/borrowed contexts serialize party:[]; valid owned party later reappears.",
        "refusal_control": "Stale owned-party entries or borrowed records transmitted as the save's party fail.",
        "oracle": "GAME load/borrowed context and independent raw record ownership compared to wire output.",
        "receipt_marker": "gen2.protocol.9.11",
        "lane": "live-new-gates"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "No coverage receipt matching this planned binding, current input/artifact pins and declared layer has been accepted."
        },
        "PHYSICAL": {
          "status": "OPEN",
          "reason": "PLANNED cartridge scenario has not run or qualified; no physical receipt is claimed."
        }
      }
    },
    {
      "id": "protocol:9.12",
      "required_layers": [
        "SOURCE",
        "PHYSICAL"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "COMMAND",
          "description": "PLANNED BINDING [P3a.2/P3b.3a/P3b.6/P3b.7]. Send hud_show with text beginning [x] WRONG SAVE using color/duration fields, then r,g,b,frames fields. PLANNED: production Entry.build conformance in tests/unit/test_gen2_client.py/test_protocol_conformance.py; tests/live/test_gen2_new_gates.py and tests/e2e/test_duo_gen2_new.py cases as the selected lane requires."
        },
        "artifacts": {
          "pokecrystal": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133",
          "pokegold": "d8b8a3600a465308c9953dfa04f0081c05bdcb94",
          "pokesilver": "49b163f7e57702bc939d642a18f591de55d92dae"
        },
        "positive_control": "Both forms render the warning without crashing and use the specified duration/color interpretation.",
        "refusal_control": "Missing alternative-field handling, unsafe characters or a warning that stops ticks fails.",
        "oracle": "GAME transient HUD capture and wire liveness; unsupported text passes through sanitizer.",
        "receipt_marker": "gen2.protocol.9.12",
        "lane": "live-new-gates"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "No coverage receipt matching this planned binding, current input/artifact pins and declared layer has been accepted."
        },
        "PHYSICAL": {
          "status": "OPEN",
          "reason": "PLANNED cartridge scenario has not run or qualified; no physical receipt is claimed."
        }
      }
    },
    {
      "id": "protocol:9.13",
      "required_layers": [
        "SOURCE",
        "PHYSICAL"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "COMMAND",
          "description": "PLANNED BINDING [P3a.2/P3b.3a/P3b.6/P3b.7]. Send hello replies with resolved_areas populated, then empty, and change each config boolean. PLANNED: production Entry.build conformance in tests/unit/test_gen2_client.py/test_protocol_conformance.py; tests/live/test_gen2_new_gates.py and tests/e2e/test_duo_gen2_new.py cases as the selected lane requires."
        },
        "artifacts": {
          "pokecrystal": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133",
          "pokegold": "d8b8a3600a465308c9953dfa04f0081c05bdcb94",
          "pokesilver": "49b163f7e57702bc939d642a18f591de55d92dae"
        },
        "positive_control": "Both area lists replace current resolved state and set seeded state; booleans follow the reply.",
        "refusal_control": "Retaining stale areas on an empty list or coercing false to true fails.",
        "oracle": "Independent client-state MODEL inspection plus resulting SERVER/area behavior and wire observations on cartridge.",
        "receipt_marker": "gen2.protocol.9.13",
        "lane": "live-new-gates"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "No coverage receipt matching this planned binding, current input/artifact pins and declared layer has been accepted."
        },
        "PHYSICAL": {
          "status": "OPEN",
          "reason": "PLANNED cartridge scenario has not run or qualified; no physical receipt is claimed."
        }
      }
    },
    {
      "id": "protocol:9.14",
      "required_layers": [
        "SOURCE",
        "PHYSICAL"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "NATURAL",
          "description": "PLANNED BINDING [P3a.2/P3b.3a/P3b.6/P3b.7]. Run overworld, wild/trainer battle, borrowed/unloaded and trustworthy PC-diff contexts through multiple tick periods. PLANNED: production Entry.build conformance in tests/unit/test_gen2_client.py/test_protocol_conformance.py; tests/live/test_gen2_new_gates.py and tests/e2e/test_duo_gen2_new.py cases as the selected lane requires."
        },
        "artifacts": {
          "pokecrystal": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133",
          "pokegold": "d8b8a3600a465308c9953dfa04f0081c05bdcb94",
          "pokesilver": "49b163f7e57702bc939d642a18f591de55d92dae"
        },
        "positive_control": "Periodic ticks carry the declared context fields; party ownership, enemy_party outside battle and pc_boxes eligibility are correct.",
        "refusal_control": "Stale battle enemies, borrowed party, unsafe box diff, missing periodic tick or wrong boolean/count fields fail.",
        "oracle": "Independent frame/wire timeline and same-frame PYDEC snapshots; Gen2 cadence follows its explicit bound profile.",
        "receipt_marker": "gen2.protocol.9.14",
        "lane": "live-new-gates"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "No coverage receipt matching this planned binding, current input/artifact pins and declared layer has been accepted."
        },
        "PHYSICAL": {
          "status": "OPEN",
          "reason": "PLANNED cartridge scenario has not run or qualified; no physical receipt is claimed."
        }
      }
    },
    {
      "id": "protocol:9.15",
      "required_layers": [
        "SOURCE",
        "PHYSICAL"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "NATURAL",
          "description": "PLANNED BINDING [P3a.2/P3b.3a/P3b.6/P3b.7]. Enter a natural wild battle from a known area on every title and capture the first battle tick. PLANNED: production Entry.build conformance in tests/unit/test_gen2_client.py/test_protocol_conformance.py; tests/live/test_gen2_new_gates.py and tests/e2e/test_duo_gen2_new.py cases as the selected lane requires."
        },
        "artifacts": {
          "pokecrystal": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133",
          "pokegold": "d8b8a3600a465308c9953dfa04f0081c05bdcb94",
          "pokesilver": "49b163f7e57702bc939d642a18f591de55d92dae"
        },
        "positive_control": "First battle tick has in_battle true, is_trainer_battle false, nonempty area_id and enemy species_id>0.",
        "refusal_control": "Delayed/stale trainer state, empty area or zero/wrong enemy species fails.",
        "oracle": "GAME battle entry plus independent enemy record/area decode and first-tick wire ordering.",
        "receipt_marker": "gen2.protocol.9.15",
        "lane": "live-new-gates"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "No coverage receipt matching this planned binding, current input/artifact pins and declared layer has been accepted."
        },
        "PHYSICAL": {
          "status": "OPEN",
          "reason": "PLANNED cartridge scenario has not run or qualified; no physical receipt is claimed."
        }
      }
    },
    {
      "id": "protocol:9.16",
      "required_layers": [
        "SOURCE",
        "PHYSICAL"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "NATURAL",
          "description": "PLANNED BINDING [P3a.2/P3b.3a/P3b.6/P3b.7]. Start trainer battles with known class/id/name across titles and inspect first and later battle ticks. PLANNED: production Entry.build conformance in tests/unit/test_gen2_client.py/test_protocol_conformance.py; tests/live/test_gen2_new_gates.py and tests/e2e/test_duo_gen2_new.py cases as the selected lane requires."
        },
        "artifacts": {
          "pokecrystal": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133",
          "pokegold": "d8b8a3600a465308c9953dfa04f0081c05bdcb94",
          "pokesilver": "49b163f7e57702bc939d642a18f591de55d92dae"
        },
        "positive_control": "is_trainer_battle is true and trainer_id>0 or the declared opponent name/class fallback is correct.",
        "refusal_control": "Wild/borrowed battle misclassification or a trainer id from the prior battle fails.",
        "oracle": "GAME trainer display and independent source trainer table versus wire fields.",
        "receipt_marker": "gen2.protocol.9.16",
        "lane": "live-new-gates"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "No coverage receipt matching this planned binding, current input/artifact pins and declared layer has been accepted."
        },
        "PHYSICAL": {
          "status": "OPEN",
          "reason": "PLANNED cartridge scenario has not run or qualified; no physical receipt is claimed."
        }
      }
    },
    {
      "id": "protocol:9.17",
      "required_layers": [
        "SOURCE",
        "PHYSICAL"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "NATURAL",
          "description": "PLANNED BINDING [P3a.2/P3b.3a/P3b.6/P3b.7]. Inspect active and benched mons with status conditions, stat-stage changes and PP-Up-bearing moves, including battle exit. PLANNED: production Entry.build conformance in tests/unit/test_gen2_client.py/test_protocol_conformance.py; tests/live/test_gen2_new_gates.py and tests/e2e/test_duo_gen2_new.py cases as the selected lane requires."
        },
        "artifacts": {
          "pokecrystal": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133",
          "pokegold": "d8b8a3600a465308c9953dfa04f0081c05bdcb94",
          "pokesilver": "49b163f7e57702bc939d642a18f591de55d92dae"
        },
        "positive_control": "Status bits match protocol encoding; only active mons carry seven neutral6-based stages; PP-Up data accompanies moves.",
        "refusal_control": "Gen2-native stage numbering copied unreencoded, benched stages, packed PP mistaken for PP count or missing PP-Up data fails.",
        "oracle": "Independent raw-byte/stage/PP decode plus captured wire schema and GAME status controls.",
        "receipt_marker": "gen2.protocol.9.17",
        "lane": "live-new-gates"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "No coverage receipt matching this planned binding, current input/artifact pins and declared layer has been accepted."
        },
        "PHYSICAL": {
          "status": "OPEN",
          "reason": "PLANNED cartridge scenario has not run or qualified; no physical receipt is claimed."
        }
      }
    },
    {
      "id": "protocol:9.18",
      "required_layers": [
        "SOURCE",
        "PHYSICAL"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "NATURAL",
          "description": "PLANNED BINDING [P3a.2/P3b.3a/P3b.6/P3b.7]. Move known records through several box/slot positions and inspect pc_boxes when snapshots are trustworthy. PLANNED: production Entry.build conformance in tests/unit/test_gen2_client.py/test_protocol_conformance.py; tests/live/test_gen2_new_gates.py and tests/e2e/test_duo_gen2_new.py cases as the selected lane requires."
        },
        "artifacts": {
          "pokecrystal": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133",
          "pokegold": "d8b8a3600a465308c9953dfa04f0081c05bdcb94",
          "pokesilver": "49b163f7e57702bc939d642a18f591de55d92dae"
        },
        "positive_control": "Box/slot are zero-based and key/species/nickname match records; client memorial index equals adapter policy.",
        "refusal_control": "Off-by-one indexes, wrong memorial box or stale key/name entries fail.",
        "oracle": "PYDEC raw PC slots, GAME box header and adapter-policy comparison to wire payload.",
        "receipt_marker": "gen2.protocol.9.18",
        "lane": "live-new-gates"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "No coverage receipt matching this planned binding, current input/artifact pins and declared layer has been accepted."
        },
        "PHYSICAL": {
          "status": "OPEN",
          "reason": "PLANNED cartridge scenario has not run or qualified; no physical receipt is claimed."
        }
      }
    },
    {
      "id": "protocol:9.19",
      "required_layers": [
        "SOURCE",
        "PHYSICAL"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "NATURAL",
          "description": "PLANNED BINDING [P3a.2/P3b.3a/P3b.6/P3b.7]. Finish wild/trainer battles by win, escape and permitted loss/transition paths; identify the first qualified overworld frame. PLANNED: production Entry.build conformance in tests/unit/test_gen2_client.py/test_protocol_conformance.py; tests/live/test_gen2_new_gates.py and tests/e2e/test_duo_gen2_new.py cases as the selected lane requires."
        },
        "artifacts": {
          "pokecrystal": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133",
          "pokegold": "d8b8a3600a465308c9953dfa04f0081c05bdcb94",
          "pokesilver": "49b163f7e57702bc939d642a18f591de55d92dae"
        },
        "positive_control": "Exactly the intended safe event occurs on that first overworld frame after battle.",
        "refusal_control": "Safe sent during a battle/menu/transition or delayed past the first qualifying frame fails.",
        "oracle": "Independent frame/context trace and raw safe-event wire timing.",
        "receipt_marker": "gen2.protocol.9.19",
        "lane": "live-new-gates"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "No coverage receipt matching this planned binding, current input/artifact pins and declared layer has been accepted."
        },
        "PHYSICAL": {
          "status": "OPEN",
          "reason": "PLANNED cartridge scenario has not run or qualified; no physical receipt is claimed."
        }
      }
    },
    {
      "id": "protocol:9.20",
      "required_layers": [
        "SOURCE",
        "PHYSICAL"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "NATURAL",
          "description": "PLANNED BINDING [P3b.4/P3b.6/P3b.7]. Walk between known maps, reconnect without a map change, and enter a source-unsupported map control. PLANNED: production Entry.build conformance in tests/unit/test_gen2_client.py/test_protocol_conformance.py; tests/live/test_gen2_new_gates.py and tests/e2e/test_duo_gen2_new.py cases as the selected lane requires."
        },
        "artifacts": {
          "pokecrystal": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133",
          "pokegold": "d8b8a3600a465308c9953dfa04f0081c05bdcb94",
          "pokesilver": "49b163f7e57702bc939d642a18f591de55d92dae"
        },
        "positive_control": "One area_enter accompanies a true map change with the known adapter area/loc_name; unsupported area is empty.",
        "refusal_control": "Time-of-day/method changes alone cannot invent a new ordinary area; duplicate same-map event fails.",
        "oracle": "GAME map transition and source area-map lookup versus SERVER wire/ledger.",
        "receipt_marker": "gen2.protocol.9.20",
        "lane": "duo-pairs"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "No coverage receipt matching this planned binding, current input/artifact pins and declared layer has been accepted."
        },
        "PHYSICAL": {
          "status": "OPEN",
          "reason": "PLANNED cartridge scenario has not run or qualified; no physical receipt is claimed."
        }
      }
    },
    {
      "id": "protocol:9.21",
      "required_layers": [
        "SOURCE",
        "PHYSICAL"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "NATURAL",
          "description": "PLANNED BINDING [P3b.4/P3b.6/P3b.7]. Naturally acquire mons into party and box, receive a direct gift, and hatch an egg using the declared acquisition paths. PLANNED: production Entry.build conformance in tests/unit/test_gen2_client.py/test_protocol_conformance.py; tests/live/test_gen2_new_gates.py and tests/e2e/test_duo_gen2_new.py cases as the selected lane requires."
        },
        "artifacts": {
          "pokecrystal": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133",
          "pokegold": "d8b8a3600a465308c9953dfa04f0081c05bdcb94",
          "pokesilver": "49b163f7e57702bc939d642a18f591de55d92dae"
        },
        "positive_control": "Each capture has correct nonempty key/area, positive species/level, destination in_box, out-of-battle gift flag and is_egg field.",
        "refusal_control": "Failed/canceled acquisition, GiveEgg-before-hatch, stale destination or duplicate capture cannot pass.",
        "oracle": "ENGINE acquisition sites plus independent PYDEC final record and SERVER capture payload.",
        "receipt_marker": "gen2.protocol.9.21",
        "lane": "duo-pairs"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "No coverage receipt matching this planned binding, current input/artifact pins and declared layer has been accepted."
        },
        "PHYSICAL": {
          "status": "OPEN",
          "reason": "PLANNED cartridge scenario has not run or qualified; no physical receipt is claimed."
        }
      }
    },
    {
      "id": "protocol:9.22",
      "required_layers": [
        "SOURCE",
        "PHYSICAL"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "NATURAL",
          "description": "PLANNED BINDING [P3b.4/P3b.6/P3b.7]. Receive the starter or another allowed out-of-battle acquisition before the Ball pocket enables ordinary catches, then compare a later gift. PLANNED: production Entry.build conformance in tests/unit/test_gen2_client.py/test_protocol_conformance.py; tests/live/test_gen2_new_gates.py and tests/e2e/test_duo_gen2_new.py cases as the selected lane requires."
        },
        "artifacts": {
          "pokecrystal": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133",
          "pokegold": "d8b8a3600a465308c9953dfa04f0081c05bdcb94",
          "pokesilver": "49b163f7e57702bc939d642a18f591de55d92dae"
        },
        "positive_control": "Pre-ball out-of-battle capture uses intro; later policy uses its declared area.",
        "refusal_control": "Stale has_pokeballs, an ordinary wild capture labeled intro, or consumed map state for the intro gift fails.",
        "oracle": "Independent bag/record witness and SERVER capture area/consumption state; O-10 fixture balls do not prove this natural path.",
        "receipt_marker": "gen2.protocol.9.22",
        "lane": "duo-pairs"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "No coverage receipt matching this planned binding, current input/artifact pins and declared layer has been accepted."
        },
        "PHYSICAL": {
          "status": "OPEN",
          "reason": "PLANNED cartridge scenario has not run or qualified; no physical receipt is claimed."
        }
      }
    },
    {
      "id": "protocol:9.23",
      "required_layers": [
        "SOURCE",
        "PHYSICAL"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "NATURAL",
          "description": "PLANNED BINDING [P3b.4/P3b.6/P3b.7]. Resolve a wild battle without a catch, repeat an already resolved area, receive a gift, and complete a successful catch control. PLANNED: production Entry.build conformance in tests/unit/test_gen2_client.py/test_protocol_conformance.py; tests/live/test_gen2_new_gates.py and tests/e2e/test_duo_gen2_new.py cases as the selected lane requires."
        },
        "artifacts": {
          "pokecrystal": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133",
          "pokegold": "d8b8a3600a465308c9953dfa04f0081c05bdcb94",
          "pokesilver": "49b163f7e57702bc939d642a18f591de55d92dae"
        },
        "positive_control": "Exactly one no_catch belongs to an unresolved unsuccessful wild battle.",
        "refusal_control": "No no_catch for gifts, already resolved areas, duplicate battle callbacks or after capture in the same battle.",
        "oracle": "ENGINE battle outcome sequence plus SERVER per-area events and link/consumption ledger.",
        "receipt_marker": "gen2.protocol.9.23",
        "lane": "duo-pairs"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "No coverage receipt matching this planned binding, current input/artifact pins and declared layer has been accepted."
        },
        "PHYSICAL": {
          "status": "OPEN",
          "reason": "PLANNED cartridge scenario has not run or qualified; no physical receipt is claimed."
        }
      }
    },
    {
      "id": "protocol:9.24",
      "required_layers": [
        "SOURCE",
        "PHYSICAL"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "COMMAND",
          "description": "PLANNED BINDING [P3b.4/P3b.6/P3b.7]. Send unresolve_area for a resolved known area, then naturally revisit and fail a new eligible encounter. PLANNED: production Entry.build conformance in tests/unit/test_gen2_client.py/test_protocol_conformance.py; tests/live/test_gen2_new_gates.py and tests/e2e/test_duo_gen2_new.py cases as the selected lane requires."
        },
        "artifacts": {
          "pokecrystal": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133",
          "pokegold": "d8b8a3600a465308c9953dfa04f0081c05bdcb94",
          "pokesilver": "49b163f7e57702bc939d642a18f591de55d92dae"
        },
        "positive_control": "The area can again produce the appropriate encounter HUD/no_catch once.",
        "refusal_control": "Unknown area, repeated unresolve or stale battle state cannot produce duplicates or corrupt other area records.",
        "oracle": "SERVER area ledger/event timeline plus GAME transient encounter display.",
        "receipt_marker": "gen2.protocol.9.24",
        "lane": "duo-pairs"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "No coverage receipt matching this planned binding, current input/artifact pins and declared layer has been accepted."
        },
        "PHYSICAL": {
          "status": "OPEN",
          "reason": "PLANNED cartridge scenario has not run or qualified; no physical receipt is claimed."
        }
      }
    },
    {
      "id": "protocol:9.25",
      "required_layers": [
        "SOURCE",
        "PHYSICAL"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "NATURAL",
          "description": "PLANNED BINDING [P3b.4/P3b.6/P3b.7]. Observe genuine HP>0 to0 party faints, separately send force_faint/force_explode controls and leave an already-zero record unchanged. PLANNED: production Entry.build conformance in tests/unit/test_gen2_client.py/test_protocol_conformance.py; tests/live/test_gen2_new_gates.py and tests/e2e/test_duo_gen2_new.py cases as the selected lane requires."
        },
        "artifacts": {
          "pokecrystal": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133",
          "pokegold": "d8b8a3600a465308c9953dfa04f0081c05bdcb94",
          "pokesilver": "49b163f7e57702bc939d642a18f591de55d92dae"
        },
        "positive_control": "One faint is emitted for each genuine eligible transition.",
        "refusal_control": "Client-written zeroes, enemy faints, repeated samples or already-zero records must not emit a natural faint.",
        "oracle": "Independent HP/identity snapshots, ENGINE natural site and command write-origin log versus wire events.",
        "receipt_marker": "gen2.protocol.9.25",
        "lane": "live-new-gates"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "No coverage receipt matching this planned binding, current input/artifact pins and declared layer has been accepted."
        },
        "PHYSICAL": {
          "status": "OPEN",
          "reason": "PLANNED cartridge scenario has not run or qualified; no physical receipt is claimed."
        }
      }
    },
    {
      "id": "protocol:9.26",
      "required_layers": [
        "SOURCE",
        "PHYSICAL"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "NATURAL",
          "description": "PLANNED BINDING [P3b.4/P3b.6/P3b.7]. Naturally faint every previously alive party member, preserving the pre-heal window, then test a loaded all-zero or borrowed-party state. PLANNED: production Entry.build conformance in tests/unit/test_gen2_client.py/test_protocol_conformance.py; tests/live/test_gen2_new_gates.py and tests/e2e/test_duo_gen2_new.py cases as the selected lane requires."
        },
        "artifacts": {
          "pokecrystal": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133",
          "pokegold": "d8b8a3600a465308c9953dfa04f0081c05bdcb94",
          "pokesilver": "49b163f7e57702bc939d642a18f591de55d92dae"
        },
        "positive_control": "Exactly one whiteout follows a real terminal party faint.",
        "refusal_control": "No whiteout from loaded-zero/borrowed state, duplicate callbacks or a client-forced echo.",
        "oracle": "ENGINE pre-HealParty ordering and independent full-party HP timeline plus SERVER event count.",
        "receipt_marker": "gen2.protocol.9.26",
        "lane": "duo-pairs"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "No coverage receipt matching this planned binding, current input/artifact pins and declared layer has been accepted."
        },
        "PHYSICAL": {
          "status": "OPEN",
          "reason": "PLANNED cartridge scenario has not run or qualified; no physical receipt is claimed."
        }
      }
    },
    {
      "id": "protocol:9.27",
      "required_layers": [
        "SOURCE",
        "PHYSICAL"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "NATURAL",
          "description": "PLANNED BINDING [P3b.4/P3b.6/P3b.7]. Use native PC deposit/withdraw outside battle with an alive known key, then perform a server-commanded movement control. PLANNED: production Entry.build conformance in tests/unit/test_gen2_client.py/test_protocol_conformance.py; tests/live/test_gen2_new_gates.py and tests/e2e/test_duo_gen2_new.py cases as the selected lane requires."
        },
        "artifacts": {
          "pokecrystal": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133",
          "pokegold": "d8b8a3600a465308c9953dfa04f0081c05bdcb94",
          "pokesilver": "49b163f7e57702bc939d642a18f591de55d92dae"
        },
        "positive_control": "Natural alive party departure emits party_to_box with stats and known return emits box_to_party.",
        "refusal_control": "Battle departure, faint/deletion, or client-command movement cannot masquerade as a natural PC event.",
        "oracle": "Independent party/box key snapshots and write-origin classification versus SERVER wire.",
        "receipt_marker": "gen2.protocol.9.27",
        "lane": "duo-pairs"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "No coverage receipt matching this planned binding, current input/artifact pins and declared layer has been accepted."
        },
        "PHYSICAL": {
          "status": "OPEN",
          "reason": "PLANNED cartridge scenario has not run or qualified; no physical receipt is claimed."
        }
      }
    },
    {
      "id": "protocol:9.28",
      "required_layers": [
        "SOURCE",
        "PHYSICAL"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "COMMAND",
          "description": "PLANNED BINDING [P3b.5/P3b.6/P3b.7]. Send box_mon for a valid non-last party key in a qualified window; repeat for last-mon, unknown-key and forced-deposit-failure cases. PLANNED: production Entry.build conformance in tests/unit/test_gen2_client.py/test_protocol_conformance.py; tests/live/test_gen2_new_gates.py and tests/e2e/test_duo_gen2_new.py cases as the selected lane requires."
        },
        "artifacts": {
          "pokecrystal": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133",
          "pokegold": "d8b8a3600a465308c9953dfa04f0081c05bdcb94",
          "pokesilver": "49b163f7e57702bc939d642a18f591de55d92dae"
        },
        "positive_control": "stats_cache precedes successful deposit and final record lands correctly.",
        "refusal_control": "Last-party-mon refusal or failed deposit must not mutate illegally and must emit the required box_mon_failed reason.",
        "oracle": "PYDEC before/after slots plus wire ordering and native PC/saved readback; command execution is separate from natural PC events.",
        "receipt_marker": "gen2.protocol.9.28",
        "lane": "live-new-gates"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "No coverage receipt matching this planned binding, current input/artifact pins and declared layer has been accepted."
        },
        "PHYSICAL": {
          "status": "OPEN",
          "reason": "PLANNED cartridge scenario has not run or qualified; no physical receipt is claimed."
        }
      }
    },
    {
      "id": "protocol:9.29",
      "required_layers": [
        "SOURCE",
        "PHYSICAL"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "COMMAND",
          "description": "PLANNED BINDING [P3b.5/P3b.6/P3b.7]. Send party_mon for a boxed known key with optional stats, an already-present key and an impossible/unsafe withdrawal case. PLANNED: production Entry.build conformance in tests/unit/test_gen2_client.py/test_protocol_conformance.py; tests/live/test_gen2_new_gates.py and tests/e2e/test_duo_gen2_new.py cases as the selected lane requires."
        },
        "artifacts": {
          "pokecrystal": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133",
          "pokegold": "d8b8a3600a465308c9953dfa04f0081c05bdcb94",
          "pokesilver": "49b163f7e57702bc939d642a18f591de55d92dae"
        },
        "positive_control": "Exactly one sync_retrieve_done follows successful/already-present state with the right record.",
        "refusal_control": "Failure emits exactly one sync_retrieve_failed; no double ack, partial withdrawal or unsafe write.",
        "oracle": "Independent party/box record comparison and SERVER command/ack token/key timeline.",
        "receipt_marker": "gen2.protocol.9.29",
        "lane": "live-new-gates"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "No coverage receipt matching this planned binding, current input/artifact pins and declared layer has been accepted."
        },
        "PHYSICAL": {
          "status": "OPEN",
          "reason": "PLANNED cartridge scenario has not run or qualified; no physical receipt is claimed."
        }
      }
    },
    {
      "id": "protocol:9.30",
      "required_layers": [
        "SOURCE",
        "PHYSICAL"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "COMMAND",
          "description": "PLANNED BINDING [P3b.5/P3b.6/P3b.7]. Send memorialize for eligible linked/dead records with inactive memorial backing, full-target and last-party controls; separately exercise game_over authorization. PLANNED: production Entry.build conformance in tests/unit/test_gen2_client.py/test_protocol_conformance.py; tests/live/test_gen2_new_gates.py and tests/e2e/test_duo_gen2_new.py cases as the selected lane requires."
        },
        "artifacts": {
          "pokecrystal": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133",
          "pokegold": "d8b8a3600a465308c9953dfa04f0081c05bdcb94",
          "pokesilver": "49b163f7e57702bc939d642a18f591de55d92dae"
        },
        "positive_control": "Exactly one done/failed response reports the actual operation/box; successful record placement is independently read back.",
        "refusal_control": "No party-emptying without game_over, no active-Box14 backing write and no success ack for failed placement.",
        "oracle": "PYDEC saved/active-box records plus GAME PC/reload and SERVER ack count.",
        "receipt_marker": "gen2.protocol.9.30",
        "lane": "live-new-gates"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "No coverage receipt matching this planned binding, current input/artifact pins and declared layer has been accepted."
        },
        "PHYSICAL": {
          "status": "OPEN",
          "reason": "PLANNED cartridge scenario has not run or qualified; no physical receipt is claimed."
        }
      }
    },
    {
      "id": "protocol:9.31",
      "required_layers": [
        "SOURCE",
        "PHYSICAL"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "COMMAND",
          "description": "PLANNED BINDING [P3b.5/P3b.6/P3b.7]. Queue several deferred operations, opposing box_mon/party_mon on one key and duplicate memorialize, then cross unsafe/safe frame boundaries. PLANNED: production Entry.build conformance in tests/unit/test_gen2_client.py/test_protocol_conformance.py; tests/live/test_gen2_new_gates.py and tests/e2e/test_duo_gen2_new.py cases as the selected lane requires."
        },
        "artifacts": {
          "pokecrystal": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133",
          "pokegold": "d8b8a3600a465308c9953dfa04f0081c05bdcb94",
          "pokesilver": "49b163f7e57702bc939d642a18f591de55d92dae"
        },
        "positive_control": "FIFO executes at most one eligible command per frame; opposing pair cancels and memorialize deduplicates.",
        "refusal_control": "Unsafe-frame execution, reordering, more than one command/frame or duplicate writes fails.",
        "oracle": "Independent frame-tagged write-sink/queue timeline and final records; MODEL queue controls supplement physical execution.",
        "receipt_marker": "gen2.protocol.9.31",
        "lane": "live-new-gates"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "No coverage receipt matching this planned binding, current input/artifact pins and declared layer has been accepted."
        },
        "PHYSICAL": {
          "status": "OPEN",
          "reason": "PLANNED cartridge scenario has not run or qualified; no physical receipt is claimed."
        }
      }
    },
    {
      "id": "protocol:9.32",
      "required_layers": [
        "SOURCE",
        "PHYSICAL"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "COMMAND",
          "description": "PLANNED BINDING [P3b.5/P3b.6/P3b.7]. Send each key-addressed command with a nonexistent key while normal ticks and a subsequent valid command continue. PLANNED: production Entry.build conformance in tests/unit/test_gen2_client.py/test_protocol_conformance.py; tests/live/test_gen2_new_gates.py and tests/e2e/test_duo_gen2_new.py cases as the selected lane requires."
        },
        "artifacts": {
          "pokecrystal": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133",
          "pokegold": "d8b8a3600a465308c9953dfa04f0081c05bdcb94",
          "pokesilver": "49b163f7e57702bc939d642a18f591de55d92dae"
        },
        "positive_control": "Unknown-key command is a noncrashing no-op; following valid control still operates.",
        "refusal_control": "Any write to another key, fabricated success with mutation, queue poison or crash fails.",
        "oracle": "Independent zero-write/state snapshot for unknown key and wire liveness.",
        "receipt_marker": "gen2.protocol.9.32",
        "lane": "live-new-gates"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "No coverage receipt matching this planned binding, current input/artifact pins and declared layer has been accepted."
        },
        "PHYSICAL": {
          "status": "OPEN",
          "reason": "PLANNED cartridge scenario has not run or qualified; no physical receipt is claimed."
        }
      }
    },
    {
      "id": "protocol:9.33",
      "required_layers": [
        "SOURCE",
        "PHYSICAL"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "COMMAND",
          "description": "PLANNED BINDING [P3b.5/P3b.6/P3b.7]. Send force_faint to a benched/out-of-battle mon and to the active battler, then switch/end battle. PLANNED: production Entry.build conformance in tests/unit/test_gen2_client.py/test_protocol_conformance.py; tests/live/test_gen2_new_gates.py and tests/e2e/test_duo_gen2_new.py cases as the selected lane requires."
        },
        "artifacts": {
          "pokecrystal": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133",
          "pokegold": "d8b8a3600a465308c9953dfa04f0081c05bdcb94",
          "pokesilver": "49b163f7e57702bc939d642a18f591de55d92dae"
        },
        "positive_control": "Benched/out-of-battle HP reaches0 in the same frame; active case lands at qualified switch-out/end handling.",
        "refusal_control": "Unsafe immediate active write, missed eventual write or emitted faint echo for either client-origin path fails.",
        "oracle": "Frame-tagged write provenance, GAME/independent HP readback and wire faint suppression.",
        "receipt_marker": "gen2.protocol.9.33",
        "lane": "live-new-gates"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "No coverage receipt matching this planned binding, current input/artifact pins and declared layer has been accepted."
        },
        "PHYSICAL": {
          "status": "OPEN",
          "reason": "PLANNED cartridge scenario has not run or qualified; no physical receipt is claimed."
        }
      }
    },
    {
      "id": "protocol:9.34",
      "required_layers": [
        "SOURCE",
        "PHYSICAL"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "COMMAND",
          "description": "PLANNED BINDING [P3b.5/P6.3 conditional]. With supports_explode_mode false, confirm no server force_explode dispatch; inject an unsupported-command control. If enabled separately, compare force_explode with force_faint guarantees. PLANNED: production Entry.build conformance in tests/unit/test_gen2_client.py/test_protocol_conformance.py; tests/live/test_gen2_new_gates.py and tests/e2e/test_duo_gen2_new.py cases as the selected lane requires."
        },
        "artifacts": {
          "pokecrystal": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133",
          "pokegold": "d8b8a3600a465308c9953dfa04f0081c05bdcb94",
          "pokesilver": "49b163f7e57702bc939d642a18f591de55d92dae"
        },
        "positive_control": "Disabled capability suppresses dispatch; any supported handler preserves at least the required faint behavior.",
        "refusal_control": "Unknown/disabled command cannot bypass the write gate or produce a false natural faint.",
        "oracle": "SERVER capability/dispatch plus independent write/HP oracle if enabled; disabled disposition is not enabled-behavior evidence.",
        "receipt_marker": "gen2.protocol.9.34",
        "lane": "release-evidence"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "No coverage receipt matching this planned binding, current input/artifact pins and declared layer has been accepted."
        },
        "PHYSICAL": {
          "status": "OPEN",
          "reason": "PLANNED cartridge scenario has not run or qualified; no physical receipt is claimed."
        }
      }
    },
    {
      "id": "protocol:9.35",
      "required_layers": [
        "SOURCE",
        "PHYSICAL"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "COMMAND",
          "description": "PLANNED BINDING [P3b.5/P3b.6/P3b.7]. Send game_over and continue running through several tick periods and permitted UI transitions. PLANNED: production Entry.build conformance in tests/unit/test_gen2_client.py/test_protocol_conformance.py; tests/live/test_gen2_new_gates.py and tests/e2e/test_duo_gen2_new.py cases as the selected lane requires."
        },
        "artifacts": {
          "pokecrystal": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133",
          "pokegold": "d8b8a3600a465308c9953dfa04f0081c05bdcb94",
          "pokesilver": "49b163f7e57702bc939d642a18f591de55d92dae"
        },
        "positive_control": "Game-over HUD state persists while periodic ticks continue.",
        "refusal_control": "Stopping the client/ticks or clearing the HUD without a declared lifecycle transition fails.",
        "oracle": "GAME transient/persistent HUD observation and independent wire cadence.",
        "receipt_marker": "gen2.protocol.9.35",
        "lane": "duo-pairs"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "No coverage receipt matching this planned binding, current input/artifact pins and declared layer has been accepted."
        },
        "PHYSICAL": {
          "status": "OPEN",
          "reason": "PLANNED cartridge scenario has not run or qualified; no physical receipt is claimed."
        }
      }
    },
    {
      "id": "protocol:9.36",
      "required_layers": [
        "SOURCE",
        "PHYSICAL"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "NATURAL",
          "description": "PLANNED BINDING [P3b.4/P3b.6/P3b.7]. Move one mon box-to-party/back, reconnect, rename it and change held item through native paths. PLANNED: production Entry.build conformance in tests/unit/test_gen2_client.py/test_protocol_conformance.py; tests/live/test_gen2_new_gates.py and tests/e2e/test_duo_gen2_new.py cases as the selected lane requires."
        },
        "artifacts": {
          "pokecrystal": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133",
          "pokegold": "d8b8a3600a465308c9953dfa04f0081c05bdcb94",
          "pokesilver": "49b163f7e57702bc939d642a18f591de55d92dae"
        },
        "positive_control": "Its full key remains stable for every operation that does not change the declared identity fields.",
        "refusal_control": "Slot-based identity, nickname/item-dependent key drift or alias-induced duplicate linking fails.",
        "oracle": "Independent OT/DV/species identity decode across records plus SERVER key/link history.",
        "receipt_marker": "gen2.protocol.9.36",
        "lane": "duo-pairs"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "No coverage receipt matching this planned binding, current input/artifact pins and declared layer has been accepted."
        },
        "PHYSICAL": {
          "status": "OPEN",
          "reason": "PLANNED cartridge scenario has not run or qualified; no physical receipt is claimed."
        }
      }
    },
    {
      "id": "protocol:9.37",
      "required_layers": [
        "SOURCE",
        "PHYSICAL"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "NATURAL",
          "description": "PLANNED BINDING [P3b.4/P3b.6/P3b.7]. Complete a same-mon evolution that changes the key; include canceled evolution and an NPC identity migration control. PLANNED: production Entry.build conformance in tests/unit/test_gen2_client.py/test_protocol_conformance.py; tests/live/test_gen2_new_gates.py and tests/e2e/test_duo_gen2_new.py cases as the selected lane requires."
        },
        "artifacts": {
          "pokecrystal": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133",
          "pokegold": "d8b8a3600a465308c9953dfa04f0081c05bdcb94",
          "pokesilver": "49b163f7e57702bc939d642a18f591de55d92dae"
        },
        "positive_control": "One key_change carries old/new keys and required new species/name/reason without capture/PC movement events for either key.",
        "refusal_control": "Canceled evolution, early publication, duplicate capture or spurious party_to_box/box_to_party fails.",
        "oracle": "ENGINE final publication and independent record identity plus SERVER ordered event history.",
        "receipt_marker": "gen2.protocol.9.37",
        "lane": "duo-pairs"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "No coverage receipt matching this planned binding, current input/artifact pins and declared layer has been accepted."
        },
        "PHYSICAL": {
          "status": "OPEN",
          "reason": "PLANNED cartridge scenario has not run or qualified; no physical receipt is claimed."
        }
      }
    },
    {
      "id": "protocol:9.38",
      "required_layers": [
        "SOURCE",
        "PHYSICAL"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "NATURAL",
          "description": "PLANNED BINDING [P3b.4/P3b.6/P3b.7]. Create links from different-OT C-C fixtures, G-S and C-G, and attempt an identical-full-key pair. PLANNED: production Entry.build conformance in tests/unit/test_gen2_client.py/test_protocol_conformance.py; tests/live/test_gen2_new_gates.py and tests/e2e/test_duo_gen2_new.py cases as the selected lane requires."
        },
        "artifacts": {
          "pokecrystal": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133",
          "pokegold": "d8b8a3600a465308c9953dfa04f0081c05bdcb94",
          "pokesilver": "49b163f7e57702bc939d642a18f591de55d92dae"
        },
        "positive_control": "Every accepted link has distinct full keys on its two halves.",
        "refusal_control": "An identical-key or ambiguous record pair must refuse and preserve state/records.",
        "oracle": "SERVER accepted-link set plus independent key derivation; MODEL collision refusal remains D-13's separate by-design obligation.",
        "receipt_marker": "gen2.protocol.9.38",
        "lane": "duo-pairs"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "No coverage receipt matching this planned binding, current input/artifact pins and declared layer has been accepted."
        },
        "PHYSICAL": {
          "status": "OPEN",
          "reason": "PLANNED cartridge scenario has not run or qualified; no physical receipt is claimed."
        }
      }
    },
    {
      "id": "protocol:9.38a",
      "required_layers": [
        "SOURCE",
        "PHYSICAL"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "COMMAND",
          "description": "PLANNED BINDING [P3b.4/P3b.6/P3b.7]. Send accepted and rejected key_change requests while the client retains old-to-new aliases awaiting the same reply. PLANNED: production Entry.build conformance in tests/unit/test_gen2_client.py/test_protocol_conformance.py; tests/live/test_gen2_new_gates.py and tests/e2e/test_duo_gen2_new.py cases as the selected lane requires."
        },
        "artifacts": {
          "pokecrystal": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133",
          "pokegold": "d8b8a3600a465308c9953dfa04f0081c05bdcb94",
          "pokesilver": "49b163f7e57702bc939d642a18f591de55d92dae"
        },
        "positive_control": "Exactly one matching ack or rejected command answers each request; either response resolves the pending alias correctly.",
        "refusal_control": "Missing/double/mismatched reply or alias left live after rejection fails.",
        "oracle": "Independent SERVER reply capture plus client alias/record resolution controls and no-write rejection snapshot.",
        "receipt_marker": "gen2.protocol.9.38a",
        "lane": "duo-pairs"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "No coverage receipt matching this planned binding, current input/artifact pins and declared layer has been accepted."
        },
        "PHYSICAL": {
          "status": "OPEN",
          "reason": "PLANNED cartridge scenario has not run or qualified; no physical receipt is claimed."
        }
      }
    },
    {
      "id": "protocol:9.38b",
      "required_layers": [
        "SOURCE",
        "PHYSICAL"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "COMMAND",
          "description": "PLANNED BINDING [P3b.4/P3b.6/P3b.7]. Accept a key_change, disconnect before observation, resend it after reconnect; separately reject a colliding request. PLANNED: production Entry.build conformance in tests/unit/test_gen2_client.py/test_protocol_conformance.py; tests/live/test_gen2_new_gates.py and tests/e2e/test_duo_gen2_new.py cases as the selected lane requires."
        },
        "artifacts": {
          "pokecrystal": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133",
          "pokegold": "d8b8a3600a465308c9953dfa04f0081c05bdcb94",
          "pokesilver": "49b163f7e57702bc939d642a18f591de55d92dae"
        },
        "positive_control": "Resend is idempotent with migrated:false and no repeated mutation; rejection leaves server/presentation caches unchanged.",
        "refusal_control": "Repeated migration, cache mutation on rejection or stale alias drift fails.",
        "oracle": "SERVER state/cache/disk snapshots and independent reply/event trace across connection boundaries.",
        "receipt_marker": "gen2.protocol.9.38b",
        "lane": "duo-pairs"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "No coverage receipt matching this planned binding, current input/artifact pins and declared layer has been accepted."
        },
        "PHYSICAL": {
          "status": "OPEN",
          "reason": "PLANNED cartridge scenario has not run or qualified; no physical receipt is claimed."
        }
      }
    },
    {
      "id": "protocol:9.39",
      "required_layers": [
        "SOURCE",
        "PHYSICAL"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "COMMAND",
          "description": "PLANNED BINDING [P4.3]. On qualified overlays drive show_choices, show_menu and choose_mon with select, cancel and prompt-unavailable contexts. PLANNED: production Entry.build conformance in tests/unit/test_gen2_client.py/test_protocol_conformance.py; tests/live/test_gen2_trade_gates.py native prompt/trade cases."
        },
        "artifacts": {
          "crystal_overlay": {
            "state": "PLANNED",
            "digest": null,
            "base_artifact": "pokecrystal",
            "base_digest": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133"
          },
          "gold_overlay": {
            "state": "PLANNED",
            "digest": null,
            "base_artifact": "pokegold",
            "base_digest": "d8b8a3600a465308c9953dfa04f0081c05bdcb94"
          },
          "silver_overlay": {
            "state": "PLANNED",
            "digest": null,
            "base_artifact": "pokesilver",
            "base_digest": "49b163f7e57702bc939d642a18f591de55d92dae"
          }
        },
        "positive_control": "One result echoes the token and exact index/cancel conventions: choices0-based/127, menu1yes0no, mon0..5/7; unavailable prompt cancels immediately.",
        "refusal_control": "Double response, changed token, wrong cancel encoding or blocking unavailable prompt fails.",
        "oracle": "GAME native prompt/menu state plus independent wire token/choice count and timing.",
        "receipt_marker": "gen2.protocol.9.39",
        "lane": "live-trade-gates"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "No coverage receipt matching this planned binding, current input/artifact pins and declared layer has been accepted."
        },
        "PHYSICAL": {
          "status": "OPEN",
          "reason": "PLANNED cartridge scenario has not run or qualified; no physical receipt is claimed."
        }
      }
    },
    {
      "id": "protocol:9.40",
      "required_layers": [
        "SOURCE",
        "PHYSICAL"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "COMMAND",
          "description": "PLANNED BINDING [P4.3]. Start a prompt/trade, then request another prompt and a second trade while the first remains pending. PLANNED: production Entry.build conformance in tests/unit/test_gen2_client.py/test_protocol_conformance.py; tests/live/test_gen2_trade_gates.py native prompt/trade cases."
        },
        "artifacts": {
          "crystal_overlay": {
            "state": "PLANNED",
            "digest": null,
            "base_artifact": "pokecrystal",
            "base_digest": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133"
          },
          "gold_overlay": {
            "state": "PLANNED",
            "digest": null,
            "base_artifact": "pokegold",
            "base_digest": "d8b8a3600a465308c9953dfa04f0081c05bdcb94"
          },
          "silver_overlay": {
            "state": "PLANNED",
            "digest": null,
            "base_artifact": "pokesilver",
            "base_digest": "49b163f7e57702bc939d642a18f591de55d92dae"
          }
        },
        "positive_control": "At most one prompt/trade interaction owns the native UI; completion releases ownership.",
        "refusal_control": "A second trade_request, overlapping prompt or leaked ownership after cancel/timeout/reset fails.",
        "oracle": "GAME native UI ownership and SERVER/wire session-token timeline.",
        "receipt_marker": "gen2.protocol.9.40",
        "lane": "live-trade-gates"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "No coverage receipt matching this planned binding, current input/artifact pins and declared layer has been accepted."
        },
        "PHYSICAL": {
          "status": "OPEN",
          "reason": "PLANNED cartridge scenario has not run or qualified; no physical receipt is claimed."
        }
      }
    },
    {
      "id": "protocol:9.41",
      "required_layers": [
        "SOURCE",
        "PHYSICAL"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "COMMAND",
          "description": "PLANNED BINDING [P4.3]. Apply a valid trade to the slot holding old_key, including a slot-resolution control after allowed party changes. PLANNED: production Entry.build conformance in tests/unit/test_gen2_client.py/test_protocol_conformance.py; tests/live/test_gen2_trade_gates.py native prompt/trade cases."
        },
        "artifacts": {
          "crystal_overlay": {
            "state": "PLANNED",
            "digest": null,
            "base_artifact": "pokecrystal",
            "base_digest": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133"
          },
          "gold_overlay": {
            "state": "PLANNED",
            "digest": null,
            "base_artifact": "pokegold",
            "base_digest": "d8b8a3600a465308c9953dfa04f0081c05bdcb94"
          },
          "silver_overlay": {
            "state": "PLANNED",
            "digest": null,
            "base_artifact": "pokesilver",
            "base_digest": "49b163f7e57702bc939d642a18f591de55d92dae"
          }
        },
        "positive_control": "Exactly one trade_done echoes token/slot and reads new_key/species back from the actual target slot.",
        "refusal_control": "Stale key/slot/token, invalid blob or ack using intended rather than read-back identity fails before partial commit.",
        "oracle": "PYDEC actual target record and GAME/native trade result plus independent ack trace.",
        "receipt_marker": "gen2.protocol.9.41",
        "lane": "live-trade-gates"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "No coverage receipt matching this planned binding, current input/artifact pins and declared layer has been accepted."
        },
        "PHYSICAL": {
          "status": "OPEN",
          "reason": "PLANNED cartridge scenario has not run or qualified; no physical receipt is claimed."
        }
      }
    },
    {
      "id": "protocol:9.42",
      "required_layers": [
        "SOURCE",
        "PHYSICAL"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "COMMAND",
          "description": "PLANNED BINDING [P4.3]. Trade two distinct records while sync commands referencing their old identities are queued, then run subsequent ticks. PLANNED: production Entry.build conformance in tests/unit/test_gen2_client.py/test_protocol_conformance.py; tests/live/test_gen2_trade_gates.py native prompt/trade cases."
        },
        "artifacts": {
          "crystal_overlay": {
            "state": "PLANNED",
            "digest": null,
            "base_artifact": "pokecrystal",
            "base_digest": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133"
          },
          "gold_overlay": {
            "state": "PLANNED",
            "digest": null,
            "base_artifact": "pokegold",
            "base_digest": "d8b8a3600a465308c9953dfa04f0081c05bdcb94"
          },
          "silver_overlay": {
            "state": "PLANNED",
            "digest": null,
            "base_artifact": "pokesilver",
            "base_digest": "49b163f7e57702bc939d642a18f591de55d92dae"
          }
        },
        "positive_control": "No key_change/capture/party_to_box for traded keys; stale queued sync operations for them are discarded.",
        "refusal_control": "Any acquisition/migration echo or delayed sync write using either old key fails.",
        "oracle": "Independent SERVER event/queue timeline and PYDEC records around native trade.",
        "receipt_marker": "gen2.protocol.9.42",
        "lane": "live-trade-gates"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "No coverage receipt matching this planned binding, current input/artifact pins and declared layer has been accepted."
        },
        "PHYSICAL": {
          "status": "OPEN",
          "reason": "PLANNED cartridge scenario has not run or qualified; no physical receipt is claimed."
        }
      }
    },
    {
      "id": "protocol:9.43",
      "required_layers": [
        "SOURCE",
        "PHYSICAL"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "COMMAND",
          "description": "PLANNED BINDING [P4.3]. While apply_trade owns the operation, inject box_mon, party_mon and memorialize targeting relevant and unrelated keys. PLANNED: production Entry.build conformance in tests/unit/test_gen2_client.py/test_protocol_conformance.py; tests/live/test_gen2_trade_gates.py native prompt/trade cases."
        },
        "artifacts": {
          "crystal_overlay": {
            "state": "PLANNED",
            "digest": null,
            "base_artifact": "pokecrystal",
            "base_digest": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133"
          },
          "gold_overlay": {
            "state": "PLANNED",
            "digest": null,
            "base_artifact": "pokegold",
            "base_digest": "d8b8a3600a465308c9953dfa04f0081c05bdcb94"
          },
          "silver_overlay": {
            "state": "PLANNED",
            "digest": null,
            "base_artifact": "pokesilver",
            "base_digest": "49b163f7e57702bc939d642a18f591de55d92dae"
          }
        },
        "positive_control": "No conflicting sync write executes during the trade critical interval; later handling follows declared queue policy.",
        "refusal_control": "A single overlapping box/party/memorial write or lost trade ownership fails.",
        "oracle": "Independent frame-tagged write provenance and native/shared trade state timeline.",
        "receipt_marker": "gen2.protocol.9.43",
        "lane": "live-trade-gates"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "No coverage receipt matching this planned binding, current input/artifact pins and declared layer has been accepted."
        },
        "PHYSICAL": {
          "status": "OPEN",
          "reason": "PLANNED cartridge scenario has not run or qualified; no physical receipt is claimed."
        }
      }
    },
    {
      "id": "protocol:9.44",
      "required_layers": [
        "SOURCE",
        "PHYSICAL"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "NATURAL",
          "description": "PLANNED BINDING [P3b.4/P3b.6/P3b.7]. Enter known trainer battles, wild battles and borrowed-party contexts on each selected title. PLANNED: production Entry.build conformance in tests/unit/test_gen2_client.py/test_protocol_conformance.py; tests/live/test_gen2_new_gates.py and tests/e2e/test_duo_gen2_new.py cases as the selected lane requires."
        },
        "artifacts": {
          "pokecrystal": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133",
          "pokegold": "d8b8a3600a465308c9953dfa04f0081c05bdcb94",
          "pokesilver": "49b163f7e57702bc939d642a18f591de55d92dae"
        },
        "positive_control": "Exactly one trainer_battle_start with trainer_id>0 appears for each eligible trainer battle.",
        "refusal_control": "Wild/borrowed entry, repeated callbacks or prior-battle trainer ID cannot emit another trainer-start.",
        "oracle": "ENGINE battle-entry class plus GAME trainer display and independent wire count.",
        "receipt_marker": "gen2.protocol.9.44",
        "lane": "live-new-gates"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "No coverage receipt matching this planned binding, current input/artifact pins and declared layer has been accepted."
        },
        "PHYSICAL": {
          "status": "OPEN",
          "reason": "PLANNED cartridge scenario has not run or qualified; no physical receipt is claimed."
        }
      }
    },
    {
      "id": "protocol:9.45",
      "required_layers": [
        "SOURCE",
        "PHYSICAL"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "COMMAND",
          "description": "PLANNED BINDING [P3b.5/P6.3 conditional]. Preserve default-disabled rival swap. Exercise production dispatch's replacement reply contract in MODEL; only after enablement/B-25 resolution run native rival replacement. PLANNED: production Entry.build conformance in tests/unit/test_gen2_client.py/test_protocol_conformance.py; tests/live/test_gen2_new_gates.py and tests/e2e/test_duo_gen2_new.py cases as the selected lane requires."
        },
        "artifacts": {
          "pokecrystal": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133",
          "pokegold": "d8b8a3600a465308c9953dfa04f0081c05bdcb94",
          "pokesilver": "49b163f7e57702bc939d642a18f591de55d92dae"
        },
        "positive_control": "Every handled replace_rival_team request has one rival_team_replaced response carrying trainer/species/error as appropriate.",
        "refusal_control": "Missing/double response, malformed/unresolved team accepted or disabled capability causing writes fails.",
        "oracle": "SERVER reply schema/count; GAME and independent team decode only for separately enabled runtime behavior.",
        "receipt_marker": "gen2.protocol.9.45",
        "lane": "release-evidence"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "No coverage receipt matching this planned binding, current input/artifact pins and declared layer has been accepted."
        },
        "PHYSICAL": {
          "status": "OPEN",
          "reason": "PLANNED cartridge scenario has not run or qualified; no physical receipt is claimed."
        }
      }
    },
    {
      "id": "protocol:9.45a",
      "required_layers": [
        "SOURCE",
        "PHYSICAL"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "COMMAND",
          "description": "PLANNED BINDING [P3b.5/P6.3 conditional]. Preserve default-disabled rival swap. Exercise in MODEL that replace_rival_team echoes the session nonce and battle_id of its battle when the client declared battle_identity, and that a Gen 2 client which never declares it keeps the pre-card behaviour (a command with no identity fields). PLANNED: production Entry.build conformance in tests/unit/test_gen2_client.py/test_protocol_conformance.py; tests/live/test_gen2_new_gates.py and tests/e2e/test_duo_gen2_new.py cases as the selected lane requires."
        },
        "artifacts": {
          "pokecrystal": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133",
          "pokegold": "d8b8a3600a465308c9953dfa04f0081c05bdcb94",
          "pokesilver": "49b163f7e57702bc939d642a18f591de55d92dae"
        },
        "positive_control": "A replace_rival_team for a client that declared battle_identity echoes that battle session nonce and battle_id; one that never declared it gets a command with no identity fields.",
        "refusal_control": "Missing, mismatched or other-session identity accepted for a declaring client, identity fields sent to a non-declaring client, or disabled capability causing writes fails.",
        "oracle": "SERVER command schema (identity fields present only for declaring clients); GAME only for separately enabled runtime behavior.",
        "receipt_marker": "gen2.protocol.9.45a",
        "lane": "release-evidence"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "No coverage receipt matching this planned binding, current input/artifact pins and declared layer has been accepted."
        },
        "PHYSICAL": {
          "status": "OPEN",
          "reason": "PLANNED cartridge scenario has not run or qualified; no physical receipt is claimed."
        }
      }
    },
    {
      "id": "protocol:9.46",
      "required_layers": [
        "SOURCE",
        "PHYSICAL"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "NATURAL",
          "description": "PLANNED BINDING [P3a.2/P3b.3a/P3b.6/P3b.7]. Earn/inspect Johto badges from0 through8 and compare hello/tick bitmask with status events; separately retain Kanto read tests under R-3. PLANNED: production Entry.build conformance in tests/unit/test_gen2_client.py/test_protocol_conformance.py; tests/live/test_gen2_new_gates.py and tests/e2e/test_duo_gen2_new.py cases as the selected lane requires."
        },
        "artifacts": {
          "pokecrystal": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133",
          "pokegold": "d8b8a3600a465308c9953dfa04f0081c05bdcb94",
          "pokesilver": "49b163f7e57702bc939d642a18f591de55d92dae"
        },
        "positive_control": "status.badges is a count0..8 for the protocol field, not the raw hello/tick bitmask.",
        "refusal_control": "A mask such as128 sent as count, stale count after earning a badge or Kanto bits silently expanding the wire contract fails.",
        "oracle": "GAME Trainer Card plus independent bit-popcount policy and captured distinct message schemas.",
        "receipt_marker": "gen2.protocol.9.46",
        "lane": "live-new-gates"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "No coverage receipt matching this planned binding, current input/artifact pins and declared layer has been accepted."
        },
        "PHYSICAL": {
          "status": "OPEN",
          "reason": "PLANNED cartridge scenario has not run or qualified; no physical receipt is claimed."
        }
      }
    },
    {
      "id": "protocol:9.47",
      "required_layers": [
        "SOURCE",
        "PHYSICAL"
      ],
      "mapping": {
        "status": "MAPPED",
        "stimulus": {
          "kind": "COMMAND",
          "description": "PLANNED BINDING [P3b.6/P4.1]. Send msgbox, gui_prompt and hud_show strings containing non-ASCII, unsupported tokens, length boundaries and control-like characters; include each native panel/prompt route. PLANNED: production Entry.build conformance in tests/unit/test_gen2_client.py/test_protocol_conformance.py; tests/live/test_gen2_trade_gates.py native prompt/trade cases."
        },
        "artifacts": {
          "crystal_overlay": {
            "state": "PLANNED",
            "digest": null,
            "base_artifact": "pokecrystal",
            "base_digest": "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133"
          },
          "gold_overlay": {
            "state": "PLANNED",
            "digest": null,
            "base_artifact": "pokegold",
            "base_digest": "d8b8a3600a465308c9953dfa04f0081c05bdcb94"
          },
          "silver_overlay": {
            "state": "PLANNED",
            "digest": null,
            "base_artifact": "pokesilver",
            "base_digest": "49b163f7e57702bc939d642a18f591de55d92dae"
          }
        },
        "positive_control": "Every rendered string passes the shared sanitizer and appears with the declared safe glyph/length behavior.",
        "refusal_control": "Any unsanitized server bytes, overflow or inconsistent alternate rendering path fails.",
        "oracle": "GAME rendered HUD/native text and independent sanitizer input/output controls; overlay prompt/panel artifacts must be pinned.",
        "receipt_marker": "gen2.protocol.9.47",
        "lane": "live-gates"
      },
      "evidence": {
        "SOURCE": {
          "status": "OPEN",
          "reason": "No coverage receipt matching this planned binding, current input/artifact pins and declared layer has been accepted."
        },
        "PHYSICAL": {
          "status": "OPEN",
          "reason": "PLANNED cartridge scenario has not run or qualified; no physical receipt is claimed."
        }
      }
    }
  ]
}
```
<!-- COVERAGE_MAP_END -->
