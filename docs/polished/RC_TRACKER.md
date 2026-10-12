# Polished Crystal RC tracker

## Current freeze: 5f4732 (2026-10-08)

Product cut `e0dc443140db7511f4d7f611930444ecf23f458d`; driver-only consent fix
`d51f3e9c356b1fc60d9f4f8c9a60be7c7ac71716`. Shipped overlay
`fe8c57e1034059331ccf25d232ff757d675363b2`, code digest
`5f4732503c4ae4820b253fef3b7fc0870a3edf11b62be70868dffba874aa95f1`.
The manifest and `RC_STATUS.md` are the current gate record. The chronology below is historical;
its old capability flags, OPEN counts and overlays do not override this section.

| Feature | Frozen implementation and evidence |
|---|---|
| Native trade | Shipped commit enabled (`7e:573B=1`); normal launcher composes and advertises trade. `LIVE-SHIPPED-TRADE` binds shipped-002: both native commits, server re-key, both cold CONTINUE parties and main/backup save integrity PASS. |
| Trade binder | Cancel is wired before APPLY; publication uses the fresh-generation boundary. Native responder-entry observation gates the driver's consent input. shipped-001 FAIL remains retained; driver-only fix produced shipped-002 PASS. |
| Rival swap | Manager option enabled; configured RIVAL0/1/2 classes. `LIVE-RIVAL-SWAP` proves one native 0F:47DD enemy send-in for RIVAL0/id3 with a scripted second identity, not all rivals. |
| Explosion and plain faints | Manager Explode option enabled. Three required DEV receipts cover native Explosion, active faint and bench hold/overworld faint, exact writes and commanded echo suppression. TEST HOST command delivery is disclosed. |
| Box operations and memorialize | Party sync and box writes are always composed; `box_mon` is always queued. Memorialize is implemented for party and box origins, box20/index19. RC contract is legacy parity best-effort; three-outcome owed-burial persistence is post-RC. Fresh a/b/d/e/f `LIVE-WRITES-OVERWORLD` closes `OPEN-MEMORIALIZE`; exact diffs and controls PASS. |
| Natural faint / whiteout | `LIVE-DUO-NATURAL-FAINT` (S2n) closes `OPEN-WRITE-PATH`; `LIVE-DUO-NATURAL-WHITEOUT` (S4n) is also required. S4n's accepted bar is event sent plus partner deaths via per-mon faint; it does not prove the whiteout handler caused those deaths. |

No self-promotion from DEV to PHYSICAL or release approval is implied. Remaining receipt/source
results and the final partial-verifier census are recorded in `RC_STATUS.md`.

## Owner decisions (relayed by the coordinator; no owner-signed tree receipt)

Recorded 2026-10-08 from coordinator card `g2p-decisions`. These are relayed owner decisions, not implementation or live-qualification receipts.

**2026-10-06 — DECIDED**
* Explode/death enforcement: parity with the other generations **plus the plain-faint writer**, not universal visible Explosion.
* Three-outcome box-write contract: **GO, Gen 3 first**. Implementation/integration and qualification are separate gates.
* Full in-game trade plan: C5 commit + C6 responder, second-identity duo and cold reload. Commit **enablement for SYNTH duo testing needs a later, separate yes**; approval of the plan does not enable commit.
* Rival swap: **SYNTH staging authorised, disclosed**. This does not settle the rival class set or qualify the write.

**2026-10-07 — DECIDED**
* Incoming-mon rules: the **current validator only**; no extra admission policy.
* The dev build **never advertises trade in hello**.
* Uncertainty before APPLY: close the lease, log once, report **NOT-PERFORMED**. **UNCERTAIN** is reserved for after a commit starts; this decision is not a claim that every current implementation path complies.
* Vanilla Gen 2 APPLY hazards (discarded final APPLY frame; same-frame OFFER-answer + APPLY arm): fix in the **NEXT Gen 2 window**, batched as an inclusive **3600-frame ROM wait** plus the **host frame-gap guard**. Not a completed-fix claim for this Polished cut.
* Version stamp: **0.1.0 until the first live receipts**.
* Landing: **keep local; no master merge yet**. One reviewed landing after the box-contract merge, trade pump, commit step and a full verifier run; those prerequisites do not themselves authorise landing.

**Historical undecided list at that card:** memorial NOT_COMPLETE semantics; box-20 retrieval policy + quarantine exception; last-healthy parity; rival class set; later stamping policy; commit enablement; landing/push/release. Later owner rulings enabled shipped commit, selected RIVAL0/1/2 and accepted RC memorial parity best-effort; landing/push/release still require separate authority.

**2026-10-09 — DECIDED (card `g2p-rcrules`)**
* Retire `LIVE-NO-BOX-MON` and its receipt: `supports_box_mon=True` supersedes the boxless-client obligation; `LIVE-WRITES-OVERWORLD` proves box moves.
* In-battle write negatives are unit/model-covered for the RC. The only remaining `OPEN-WRITE-PATH` live requirement is a **PHYSICAL in-battle natural faint** (duo S2n), not another commanded-kill run.
* Whiteout's accepted S4n obligation is **whiteout event sent + all partners die via per-mon faint**. It does not claim that `_handle_whiteout` caused the deaths live; that handler stays unit-covered.
* Rival swap, commanded active-faint and bench-faint PASS live in `F:/slink-work/lanes/pol-explode2`. `OPEN-EXPLODE-RIVAL` now waits for the client explode echo fix (in flight) and the capability/Manager flip at freeze. No new receipts or PHYSICAL promotion in this update; rebind at the frozen cut.

2026-10-11: supports_box_mon retired by the review fixes; box_mon is always queued.

**2026-10-09 — RC 1 memorial ruling and later lane evidence (card `g2p-rcmemo`)**
* Memorialize is **in RC 1**: both mons of a dead linked pair must move to **memorial box 20, index 19**. `OPEN-MEMORIALIZE` is OPEN/blocking: client refuses at `polished_overworld.lua:902/:1038`; executor per `MEMORIALIZE.md` is in flight (`pol-memorial`), then a live `writes_run` memorialize leg receipt is required at freeze.
* Duo **S2n PASS**: `F:/slink-work/lanes/pol-duo3/s2n3/play-faint-natural/evidence.json`, `server_path=faint_event`. Duo **S4n PASS**: `F:/slink-work/lanes/pol-duo3/s4n2/play-whiteout-natural/evidence.json`, `whiteout_handler_proved=false`; accepted proof remains whiteout event sent + all partners die via per-mon faint.
* **Explode, active-faint and bench-faint PASS after echo fix `ea90b9fd3`**: `F:/slink-work/lanes/pol-explodefix/{explode-3ced0b5ae425,active-faint-eed2072c21ae,bench-faint-23649423f2f7}/probe/oracle.json`, all `ok=true`, no reasons.
* These are lane records, not receipts, restamps or PHYSICAL promotions. Duo runs disclose SYNTH fixture/second identity, HP=1 conditioning and lead rearrangement. `OPEN-WRITE-PATH` now waits only for the S2n receipt at freeze; `OPEN-EXPLODE-RIVAL` waits for capability/Manager flip and freeze binding, not the landed echo fix.

## Vocabulary

| level | meaning |
|---|---|
| IMPLEMENTED | code is in the tree; it may have no test, or no live run |
| MODEL-TESTED | a unit test on disk exercises it (Python model, or Lua under lupa where the row says so); it never ran on a cartridge |
| LIVE-RUN | ran on an emulated cartridge in an older run, on the overlay sha1 named in the row. A sha1 is not a commit. These are DEV runs from `LIVE_RESULTS.md` or a lane result file, not PHYSICAL receipts |
| QUALIFIED ON THIS CUT | the verifier's live receipts for the CURRENT code and overlay are green. This describes evidence binding only, not owner release approval |

**2026-10-08 later, card `g2p-rcclose` at `499d1cab9`: then-current overlay `688945795e2656019247f5aaceb7b1d8791e900a`, UPS 4166 B (title wordmark commit `1d750de2e`). Five DEV receipts bound to it and to the computed code digest `f8966ea4…` (`--print-code-digest`) PASS in the verifier: `LIVE-TITLE-SPLASH`, `LIVE-WRITES-OVERWORLD`, `LIVE-PANEL-PAGES-ROM`, `LIVE-PANEL-HELLO`, `LIVE-PANEL-HOST-PAGING`. `OPEN-TITLE-SPLASH` is CLOSED by `LIVE-TITLE-SPLASH`; `OPEN-WRITE-PATH` and `OPEN-PANEL-PAGES` stay OPEN on the points named in `tests/polished_release_requirements.json`. Rows below marked "on `68894579`" cite those receipts; everything else keeps its historical overlay.**

**2026-10-08 source update at `4281d18c5`: then-current overlay `cf03f53accefbc5f3fee9062846699e30c4c987b`, UPS 3767 B (`overlay_provenance.json:63,85-88`), from bounded-rollback commit `33f3b08bf` (merge `8d80f1ea3`). `877a477a` is the historical C5-body cut (`7f56228b5`); `97628616` is older still. Pin audit: `130 pins; 0 STALE executable pins`.** The `deebb100` text below is the historical reference for the "live on" column. The integrated overlay then was `data/polished/overlay_provenance.json` `output.sha1`,
`deebb1004d4f6123667d5d9e061ad0bf867ac021` (`deebb100`, commit `da7c9bad4`). The column "live on
`deebb100`?" is the marker for which rows were run live on that overlay and which only on an
older or lane overlay. Do not copy a sha1 between trees: read the file.

Older overlay sha1s that appear below (all are overlay sha1s, not commits): `29ea04c2` (Runs 1-3),
`34942315` (Phone Stage 1, `c9f1ad14f`), `9c60bc8f` (first panel build, `5ed073821`), `ac65532c`
(sound-only build, `ef7fdd60e`), `7461c828` (a pol-panel2 lane build; not committed as the
integrated overlay).

## Historical table (superseded by the current freeze section)

| feature | level | live on `deebb100`? | source and commits | tests and live records |
|---|---|---|---|---|
| hello + admission (overlay) | MODEL-TESTED, LIVE-RUN (`29ea04c2`) | YES: Stage A `PASS polished-live` frame 8590, lane `F:/slink-work/lanes/g2int-ov/stageA.out` (not recorded in a committed doc) | `lua/gen2/polished.lua` `P.admit`; `lua/gen2/entry.lua` `admit_polished` | `tests/unit/test_polished_client.py`, `tests/unit/test_polished_companion.py`; `LIVE_RESULTS.md` Run 1 Stage 1, Run 2 Stage A |
| hello + admission (randomized cartridge) | MODEL-TESTED, LIVE-RUN (`29ea04c2` lineage) | NO | `lua/gen2/polished.lua` `P.admit`, `P.RAND_ANCHORS` | `tests/unit/test_polished_rand_admission.py`; `LIVE_RESULTS.md` Run 3 Stages R1-R3 |
| title / version stamping | IMPLEMENTED, INTEGRATED, LIVE-RUN on `68894579` (DEV) | YES on `68894579`: `LIVE-TITLE-SPLASH` (overlay PASS, clean-ROM control FAIL on the same judge; CGB fresh boot only) | `patch/polished/src/title.asm` + `version.asm`; wordmark commit `1d750de2e`; earlier source `e5f66d89c` on `claude/pol-title2` | `tests/unit/test_polished_title.py`; receipt `tests/fixtures/polished/receipts/live_title_splash.json` (lane `F:/slink-work/lanes/pol-rcproof/title/evidence.json`). The old `claude/pol-title` branch is superseded |
| wild-catch detection | MODEL-TESTED, LIVE-RUN (`29ea04c2`) | YES: Stage A capture-site bank 3 hits 1, `g2int-ov/stageA.out` | `lua/gen2/signals.lua` `S.new_polished` (site `capture_party` `03:652b`) | `tests/unit/test_polished_sites.py`, `tests/unit/test_gen_polished_engine_sites.py`; `LIVE_RESULTS.md` Run 1 Stage 2 |
| forms identity (key + effective wire) | MODEL-TESTED | NO: never run live; the Alolan Persian GAP in `LIVE_RESULTS.md` was closed in code by `43ee19f0` | commit `43ee19f0`; `server/adapters/polished_codec.py` `key_form` | `tests/unit/test_polished_effective_wire.py`, `tests/unit/test_gen_polished_forms.py` |
| variant evolution families | MODEL-TESTED | NO | `server/adapters/gen2_polished.py` `evo_family` (lowest effective id of the evolution component, over variants too) | `tests/unit/test_polished_forms_adapter.py` (18), `tests/unit/test_polished_upr_forms.py` |
| box census (read-only) | MODEL-TESTED, LIVE-RUN (`29ea04c2`) | YES: Stage A census follows the battle end (`g2int-ov/stageA.out`) | `lua/gen2/polished_boxes.lua` | `tests/unit/test_polished_boxes_census.py`, `tests/unit/test_polished_boxes.py`; `LIVE_RESULTS.md` Run 1 Stage 3, Run 2 Stage A |
| memorial / insert writes | IMPLEMENTED helpers; memorial executor refuses. **In RC 1**, owner 2026-10-09 | NO memorial leg receipt | `polished_overworld.lua:902/:1038`; memorial box 20/index 19; executor in flight (`pol-memorial`) | `MEMORIALIZE.md`; `OPEN-MEMORIALIZE` requires live `writes_run` memorialize leg receipt at freeze |
| box_mon deposit | MODEL-TESTED, LIVE-RUN on `68894579` (DEV). `supports_box_mon` is **True** since `8c1b841e2` | YES on `68894579`: `LIVE-WRITES-OVERWORLD` box_mon (b) and party_mon (d) exact diffs PASS, byte-identical to the `cf03f53a` lane | `lua/gen2/polished_overworld.lua` `O.boxes`; `server/adapters/gen2_polished.py:760` `supports_box_mon` (True, `8c1b841e2`). Commits `9f5783869`, `dfeec7555` | `tests/unit/test_polished_write_path.py`, `tests/unit/test_polished_writes.py`; `tools/polished_live/writes.lua`, `writes_run.py`; receipt `live_writes_overworld.json` (lane `F:/slink-work/lanes/pol-writes6889`). One bank-1 deposit only |
| party_mon withdraw | MODEL-TESTED (executor composed), LIVE-RUN on `68894579` (DEV): check (d) PASS in `LIVE-WRITES-OVERWORLD` (bank 1, one mon; exact diff, pokedb entry never freed). Earlier historical (d) PASS on `6e43f8d9` (`LIVE_WRITES.md`). Idempotent and dead-mon guarded (`WITHDRAW.md`). Capability `supports_box_mon` True since `8c1b841e2` | YES on `68894579` for (d) | `lua/gen2/polished_overworld.lua` withdraw branch (`:590-651`): party half first, count last, verified append, narrowed permits, reconciliation, ambiguity refusal. Commit `290d47b63`; savemon to party reconstruction in `lua/gen2/polished_stats.lua` and `polished_codec.savemon_to_party` (`805f65b56`, `6a0c13211`) | `tests/unit/test_polished_withdraw_path.py` (42), `tests/unit/test_polished_stats.py` (47), `tests/unit/test_polished_stats_lua.py`; spec `WITHDRAW.md`; receipt `live_writes_overworld.json`. HP comes back full and status cleared (engine behaviour) |
| force_faint / natural faint propagation | MODEL-TESTED; bench (a) PASS in `LIVE-WRITES-OVERWORLD`; S2n PASS lane evidence | YES: `pol-duo3/s2n3/play-faint-natural/evidence.json`, `server_path=faint_event`; not a freeze receipt or PHYSICAL promotion | `lua/gen2/polished_overworld.lua`; `duo.py --scenario faint-natural`; in-battle negatives unit/model-covered per owner | `test_polished_write_path.py`; `OPEN-WRITE-PATH`: S2n PASS as lane evidence; receipt at freeze |
| Bug Catching Contest death hold | MODEL-TESTED | NO | `lua/gen2/entry.lua:919-924` `contest_mask={bank=1, address=0xD7E4, bit=2}` (a KO during the contest is held, not dropped) | `tests/unit/test_polished_write_path.py:902-916` |
| explode mode | MODEL-TESTED; explode/active-faint/bench-faint all PASS lane evidence after echo fix `ea90b9fd3` | YES in `pol-explodefix`; not freeze-qualified or receipt-bound | `lua/gen2/polished_explode.lua`; capability/Manager flip deferred to freeze | `test_polished_explode_path.py`; `pol-explodefix/{explode-3ced0b5ae425,active-faint-eed2072c21ae,bench-faint-23649423f2f7}/probe/oracle.json`: `ok=true`, no reasons |
| rival team swap | COMPOSED/model-tested; SYNTH swap PASS live (DEV) in `pol-explode2`, superseding the historical probe-only limitation | YES in `pol-explode2`; not freeze-qualified or receipt-bound | `lua/gen2/polished_rival.lua`; configured class policy remains separate; capability/Manager flip deferred to freeze | `test_polished_rival_path.py`; `pol-explode2/rival/synth-90mk5ijy/receipt.json`: PASS, no reasons; `rival_final.out`: EmuHawk rc=0 |
| battle-sites binder (opt-in) | MODEL-TESTED | NO | `lua/gen2/polished_battle.lua`; commit `6e7a1b53` | `tests/unit/test_polished_battle_sites.py` |
| whiteout (S4n RC bar) | OWNER-ACCEPTED 2026-10-09: whiteout event sent + all partners die via per-mon faint; S4n PASS lane evidence | YES: `pol-duo3/s4n2/play-whiteout-natural/evidence.json`; no freeze receipt or handler-caused death proof | `_handle_whiteout` stays unit-covered; `whiteout_handler_proved=false` in lane evidence | S4n SYNTH HP=1 conditioning disclosed; frozen-cut receipt binding remains separate |
| runtime party / battle / player decoding, party and savemon codecs | MODEL-TESTED (Lua under lupa and Python) | NO beyond what Stage A exercised | `lua/gen2/polished.lua`, `server/adapters/polished_codec.py` | `tests/unit/test_polished_lua.py` (24), `tests/unit/test_polished_codec.py` (17), `tests/unit/test_polished_stats.py` |
| trainer / rival identification, badge ordering | MODEL-TESTED | NO | `server/adapters/gen2_polished.py` (moved trainer classes RIVAL0/1/2 + LYRA1/2; wild `level_from_badges` handling) | `tests/unit/test_gen2_polished_adapter.py` (18) |
| server adoption of randomized encounter tables | MODEL-TESTED, LIVE-RUN (Run 3 R2, older overlay) | NO | `server/adapters/gen2_polished.py:411-433` `ingest_rom_content` and `use_rom_encounters` (the server's own read of the Manager file, sha1 pinned; unreadable cartridge shows no encounters) | `tests/unit/test_polished_rand_data.py` (12); `LIVE_RESULTS.md` Run 3 |
| randomizer + UPR jar | MODEL-TESTED, LIVE-RUN (Run 3, older overlay) | NO | `server/upr_pipeline.py` (`POLISHED_VARIANT`), `tools/gen_upr_polished_ini.py` | `tests/unit/test_upr_polished_pipeline.py`, `tests/unit/test_gen_upr_polished_ini.py`; `LIVE_RESULTS.md` Run 3 Stages R1-R3 |
| Phone-card SLink contact | MODEL-TESTED, LIVE-RUN | YES: `PASS pol-phone` frame 3103 on `deebb100`, lane `g2int-ov/phone2.out` (not in a committed doc). The earlier Stage 1 run (`34942315`) is in `LIVE_RESULTS.md` | `patch/polished/src/slink.asm` (`SlinkPhone_*`); `lua/gen2/phone.lua`; commit `c9f1ad14f` | `tests/unit/test_polished_phone_entry.py`, `tests/unit/test_polished_companion.py`. "Call" on the contact now opens `SlinkPanel` (`slink.asm:179-191`); the old "CallGate prints a stub" wording is obsolete |
| phone calls (real in-game calls) | NOT STARTED: no call transport | NO | `SlinkPhone_CallGate` opens the panel and never rings | `tests/unit/test_polished_phone_entry.py` |
| panel pages | MODEL-TESTED, LIVE-RUN on `68894579` (DEV); rows truncated | YES on `68894579`: C0-C5 against a scripted mailbox host (`LIVE-PANEL-PAGES-ROM`), hello `panel=true`, `panel_abi 3` (`LIVE-PANEL-HELLO`), real-host paging server `link_panel` -> lua `panel:hold` -> ROM (`LIVE-PANEL-HOST-PAGING`). NO for readability: wide `label|field` rows are cut to the 16-glyph panel (`info_panel_width()==0` inherited), fix in flight (`pol-panelfix`) | `patch/polished/src/panel.asm`; commit `5ed073821`; spec `PANEL.md`; driver `panel_host_live.{py,lua}` (`8edc811f2`) | `tests/unit/test_polished_panel.py`; receipts `live_panel_pages_rom.json`, `live_panel_hello.json`, `live_panel_host_paging.json` (lanes `F:/slink-work/lanes/pol-rcproof/{panel,hello}`, `pol-panelhost`). Historical: `7461c828` lane PASS, `9c60bc8f` FAIL (`LIVE_PANEL.md`) |
| sounds | MODEL-TESTED, LIVE-RUN. PASS on `ac65532c` and on `deebb100` | YES: `PASS pol-sounds` frame 633, caps `$07`, four codes post to ack to play, busy-channel hold 163 frames (under 240), every host write at `$C612` | `lua/gen2/polished_sounds.lua`; commits `ef7fdd60e`, `983693620` | `tests/unit/test_polished_sounds.py`, `tests/unit/test_polished_sounds_wired.py`; `LIVE_SOUNDS.md`. Not exercised: reset-latch path, music-fade hold. The Manager `native_sounds` row was never run through a real client/server pair |
| native trade (receptionist) | IMPLEMENTED, DEV-only; C5 bounded rollback built but commit disabled, gate `7e:573B=0` | NO | `trade_commit.asm:1-21`: last/sole-slot pre-UI removal failure rollback only; first durable store is point of no return; non-last partial rotations hold result 2. Owner yes and enabled evidence still required | `LIVE_RESULTS.md:770-797`: six held-service cases PASS on `cf03f53a` with SYNTH + TEST HOST and disabled commit; cold reload PASS only for empty referenced boxes, not post-commit durability |
| trade dispatch fingerprint | MODEL-TESTED. Engine-state blocks are INFERRED, not measured | NO | `server/adapters/polished_trade_fingerprint.py` and Lua twin `lua/gen2/polished_trade_fp.lua` | `tests/unit/test_polished_trade_fp.py` (38; see its docstring on INFERRED values) |
| trade lease / mailbox binder | IMPLEMENTED, DEV-only; advertised trade stays false; cancel API landed, pump wiring queued | NO | `polished_trade.lua:393,481-512,614-641`: `cancel(reason)` closes pre-APPLY visit; arm fault is NOT_PERFORMED only after readback proves no APPLY publication and cancellation succeeds, otherwise UNCERTAIN. `client.lua` does not call cancel yet; see `TRADE_PUMP.md:101-111` | `test_polished_trade_binder.py`, `test_polished_trade_composition.py`; test presence, not re-run here. `entry.lua:958-986` retains explicit dev composition |
| calculator | **PARTIAL**: baseline works (`calculateRBYGSC` over Polished data). Crit: the Polished scaling (x1.5, x2.25 with Sniper) is implemented (`CALC.md` s4.1), `effect_commands.asm:4333-4343` (`CALC.md` section 4.1). Abilities unsupported: `supports_abilities()` is False (`gen2_polished.py:630`) | NO | `tools/gen_polished_calc.py`; commit `c32791fe` | `tests/unit/test_gen_polished_calc.py` (19) |
| overlay build + byte guard | MODEL-TESTED; integrated C5 body is built but disabled, not live-qualified | n/a (build artefact) | `tools/build_polished_companion.py`; current overlay sha1 `688945795e2656019247f5aaceb7b1d8791e900a` (`data/polished/overlay_provenance.json` `output.sha1`, UPS 4166 B; title wordmark `1d750de2e`). `SlinkTradeCommitEnabled` at `7e:573B` is 0 (`TRADE.md:1421-1429`); symbol presence is not enablement | `tests/unit/test_polished_companion.py`, `tests/unit/test_polished_release_guards.py`. A full rgbds rebuild remains a BUILD-OVERLAY verifier item; no rebuild or current-cut qualification is claimed by this update |
| ROM tables / ram doc / forms pack | MODEL-TESTED (source only) | n/a | `tools/gen_polished_*.py` | `tests/unit/test_polished_rom_tables.py` (one skip when pokecrystal is not cloned), `test_polished_ram_doc.py`, `test_polished_area_map.py` |
| player ZIP carries the composed Polished dependencies | MODEL-TESTED; package manifest source checked, no ZIP built in this update | n/a | `tools/make_release.py` `_LUA_GEN2` and the `polished_crystal` pack rows. The dev-only `polished_trade.lua` binder **is listed** (`make_release.py:175`); `polished_trade_fp.lua` is not listed. Dev composition is opt-in (`entry.lua:958-986`), not a production capability | `tests/unit/test_polished_release_manifest.py`; test presence only, not re-run here |
| Polished release verifier | IMPLEMENTED and MODEL-TESTED. Its verdict is red | n/a | `tools/verify_polished_release.py`, manifest `tests/polished_release_requirements.json`, report `RC_STATUS.md`; false-green paths closed in `6fb8ea629` | `tests/unit/test_verify_polished_release.py` (90). Known red: see the header |
| C-5 randomized duo gate | PARTIAL. The gate is real release evidence: `c5_gate_errors` (`tools/verify_gen2_release.py:2462`) requires an installed, sound packet and a PHYSICAL PASS per cell at the current digest. The installer `tools/c5_install_receipts.py` has a link-safe destination and an unpinned-file gate (`verify_gen2_release.py:2458`). **Receipts are NOT installed** (`tests/fixtures/gen2/receipts/c5/` does not exist) | NO | `tools/c5_runner.py` (needs EmuHawk, about 20 min, run at freeze) | `tests/unit/test_verify_gen2_c5.py`, `tests/unit/test_c5_install.py` |

2026-10-11: supports_box_mon retired by the review fixes; box_mon is always queued.

**Note W (box_mon / force_faint).** HISTORICAL (the committed `docs/polished/LIVE_WRITES.md` was updated 2026-10-06 and now records (a), (b), (d) PASS on `6e43f8d9`, bank 1, one mon; only (c) is NOT RUN). The earlier state of that document: it said
check (a), (b) and (c) are NOT RUN (the save fixture did not load). The later driver commits
`9fd4619a5` (a) and `dfeec7555` (b) fixed the harness, and the lane result file
`F:/slink-work/lanes/pol-livew/writes/result.txt` (2026-10-05) shows (a1)-(a4b) and (b0)-(b4) all `[ok]`
on the integrated overlay `deebb100`. (The document has since been updated; this tracker still cites the
result file and does not claim more than it shows.) Check (c) did not finish.

## How to re-derive this table

```bash
git log --oneline --all -- <the feature's main source file>
git branch --contains <sha>            # which branch carries it
git merge-base --is-ancestor <sha> HEAD && echo in-HEAD
python -m pytest tests/unit -k polished --collect-only -q | tail -1
python -c "import json;print(json.load(open('data/polished/overlay_provenance.json'))['output']['sha1'])"
```

## Known-stale text elsewhere in `docs/polished/`

Marked in place, not rewritten (the original wording is kept as history):

* `LIVE_WRITES.md` now records (a), (b), (d) PASS (historical, `6e43f8d9`) and (c) NOT RUN; see note W.
* `LIVE_RESULTS.md` Phone Stage 1 describes the entry text "SLink is linked." That was the Stage 1
  overlay (`34942315`); the contact now opens the panel.
* `FORMS.md`, owner rulings: said `polished_codec.key` "today keeps form 0-4 as traits". `key_form`
  normalises cosmetic forms now (`server/adapters/polished_codec.py:82-85`).
* `LIVE_RESULTS.md`, the Alolan Persian GAP: closed in code by `43ee19f0` and the effective-species wire.
* `PHONE.md`, `PHONE_SLOT.md`, `POKEGEAR_SLOT.md`, `TITLE.md`: said ROM0 has "351 bytes free at `$015f`".
  `$015f` is that **count**, not an address; the real gaps are `$0089-$00FF` and the part of `$3F34-$3FFF`
  after the phone bridge. `HOOKS.md:812-813` carries the correction.
* `CLIENT.md` line 4 quotes overlay sha1 `29ea04c2` as current. That is right for the runs it describes;
  the current overlay is in `data/polished/overlay_provenance.json`.

## Unverified in this table

* Every LIVE-RUN statement is a claim about a historic DEV run recorded in `LIVE_RESULTS.md`,
  `LIVE_SOUNDS.md` or a lane result file. None was re-run here and none is a PHYSICAL receipt. The
  lane files under `F:/slink-work/lanes/` are outside the repository.
* The count "18 failing items" is the coordinator's figure; this rewrite started the verifier and
  stopped it before it finished.
* Test counts are collected, not passed.

## Historical box-command gaps before capability flips (2026-10-06; headless Codex cx-532590c0 + Polished peer cx-c0a19d75; code re-read by the coordinator where marked)

The original ruling kept both flags **False**. `supports_box_mon` became True in `8c1b841e2`; the owner retired the boxless-client RC obligation on 2026-10-09. The gap inventory below is historical, not an additional live-negative RC gate. Live (a)(b)(d) PASS proves memory effects of one mon in pokedb bank 1 only; it does not exercise the client ack, the server reaction or partial failure.
1. **Withdraw never acked (CONFIRMED; CLOSED IN CODE/MODEL by `4bda8793d`, current-cut live qualification outstanding):** executor returns `true, <table>`, client concatenates it as the settle-note string before `sync_retrieve_done`. Fix = `O.client_boxes` adapter in `polished_overworld.lua`, wrapped in `entry.lua` (claude/pol-boxfix).
2. **Full party never retries (CONFIRMED; CLOSED with gap 1):** `party full (6/6)` vs the client's exact `party full`.
3. **Post-mutation failures are reported as ordinary refusals (OPEN design):** deposit (stage -> flag/Banks/Entries -> compaction -> count last) and withdraw (record/OT/nick -> count last -> Entries erase -> Banks clear) can all fail AFTER bytes changed; `run_box` then sends `box_mon_failed` / `sync_retrieve_failed`, and `server/state.py` 617-660 assumes the move did NOT happen (restores the party model / drops the rebuild key / re-boxes the partner). There is no `box_mon_done` event. Needs a third outcome (proved complete / proved not complete / uncertain) honoured by both ends, classified from a fresh census plus the exact party block and the permit receipts; a census key list cannot prove an intact party after compaction. No mailbox byte is proposed.
4. **Memorialize is not composed** (`polished_overworld.lua` refuses by name); owner 2026-10-09 puts box 20/index 19 burial in RC 1. Executor in flight (`pol-memorial`); `OPEN-MEMORIALIZE` requires a live `writes_run` leg receipt at freeze. Box-20 retrieval policy remains separate.
5. **Last-healthy-mon guard:** the Polished executor guards only `party.count <= 1`, as the vanilla Gen 2 executor does (`lua/gen2/boxes.lua:460,581`); native storage also refuses to leave no healthy mon. Parity decision, not a regression.
6. **Allocation rollover and bank 2:** allocation rollover MODEL tests are integrated (`test_polished_allocation_rollover.py`: entries 167/168, 195/196, 207, bank-1-full -> bank 2, both banks full); NOT proven live: flags are retained after withdraw (native `NewStoragePointer` rebuilds them); one live bank-2 deposit/withdraw + native save/reload still owed.
7. **Egg/mail:** CLOSED IN CODE/MODEL (`647ffe59f`): withdraw refuses a boxed egg and a mail holder, deposit refusals are pinned in `test_polished_executor_refusals.py`.
8. **Retry identity:** a completed withdraw retried without its settle is now an idempotent done (WITHDRAW.md 'Idempotent withdraw'; key-level limits there); two byte-identical independent mons collapse on reconcile (documented in WITHDRAW.md).
9. **Reset durability:** the 73-cut withdraw sweep is in-memory; there is no deposit byte-cut sweep and no emulator reset/native-save scenario.
10. **Explode (historical gap, current evidence 2026-10-09):** explode, active-faint and bench-faint PASS after echo fix `ea90b9fd3` in `pol-explodefix`; in-battle negatives remain unit/model-covered for the RC. `OPEN-EXPLODE-RIVAL` waits for capability/Manager flip and receipt binding at freeze.
11. **Evidence is for a past overlay:** the live PASS names `6e43f8d9`; the current overlay differs. Re-run on the frozen cut.

2026-10-11: supports_box_mon retired by the review fixes; box_mon is always queued.

### Status of the box-command gaps (source update 2026-10-08 at `cb477bc1`)
Closed in code: gaps 1 and 2 (withdraw ack and `party full` retry, `O.client_boxes`, commit `4bda8793d`, composed at `lua/gen2/entry.lua:910-913`); gap 7 egg/mail executor guards (`647ffe59f`). Gap 3's three-outcome contract is **GO, Gen 3 first (owner 2026-10-06, relayed above)**, not waiting for an owner ruling; its Polished implementation/integration and failure qualification remain open. Rival source writer is composed at `entry.lua:917-923`, REFUSING by default; SYNTH staging is authorised with disclosure, but the rival hook at `0f:47DD`, live PC semantics and class set remain open. The plain-faint path has opt-in settlement composition at `entry.lua:924-927`; parity + plain-faint scope is DECIDED, not qualification. Gaps 4 (memorialize), 5 (last-healthy parity), 6 (live bank-2/native-reload evidence), 8-11 retain the qualifications/limits above. `supports_box_mon()` and `supports_explode_mode()` remain **False** (`server/adapters/gen2_polished.py:751-763`); approved decisions do not flip capabilities.

**2026-10-09 supersession:** the capability-False and probe-only statements in the dated 2026-10-08 paragraph above are historical. `supports_box_mon=True` since `8c1b841e2`; rival swap and active/bench faint now have `pol-explode2` live passes. The current RC requirements are the four owner rulings recorded above, not the old live-negative gate.

2026-10-11: supports_box_mon retired by the review fixes; box_mon is always queued.
