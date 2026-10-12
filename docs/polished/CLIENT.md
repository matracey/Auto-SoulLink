# Polished Crystal client — what it takes to make the Lua client RUN

Work list for taking `lua/gen2/*` from *admitted but not composed* to *composes and says hello with a party*,
then *detects a catch*, on **Polished Crystal v3.2.3** (tag `v3.2.3`, commit `3fa43192…`, overlay sha1 `29ea04c2…` **at the time of writing**; the overlay is rebuilt by
every `pol-*` merge, so read the current value from `data/polished/overlay_provenance.json` output.sha1 — the
committed overlay in the tree at the time of this correction was `9c60bc8f…` and the pol-panel / pol-sounds
branches will change it again).

Sources read, all read-only:

| what | where |
|---|---|
| composition root | `lua/gen2/entry.lua` (637 lines) |
| the Polished binding that already exists | `lua/gen2/polished.lua` (549 lines, committed `e81c87fd`) |
| the Gen 2 client to be reused | `lua/gen2/client.lua` (1833), `wire.lua` (234), `reads.lua` (576), `boxes.lua` (618), `writes.lua` (312), `signals.lua` (1212), `rom.lua` (339), `panel.lua` (198), `phone.lua` (177), `trade_overlay.lua` (214), `run.lua` (110), `artifact.lua` (73) |
| shared core | `lua/core/session.lua`, `deferred.lua`, `identity.lua` |
| server side (already shipped) | `server/adapters/gen2_polished.py` (447), `polished_codec.py`, registry `server/adapters/__init__.py` |
| data pack | `data/games/polished_crystal/` — **13 files, no `area_map.json`, no `engine_signals.json`, no `write_checkpoint.json`, no `admission.json`** |
| design docs | `docs/polished/{RAM,HOOKS,NEWBOX,UPR_HANDLER}.md`, `docs/gen2/gen2_engine_sites.md` |
| pinned source | `F:/slink-work/cache/polished/src` (read-only) |

---

## 0. Executive summary

The blocker is **not** the ROM and **not** the decoder. `lua/gen2/polished.lua` already decodes the party, player, map,
badges, balls pocket, battle state and stat stages off a generated Polished profile, and the **server already has a
Polished adapter** registered (`server/adapters/__init__.py:287`). The blocker is that `lua/gen2/entry.lua:596-610`
short-circuits `polished` before `compose()` ever runs, and that `compose()` is hard-wired to the vanilla Gen 2 pack
shape: it demands **15 pack files** of which the Polished pack ships **2**, demands `matrix.foundation == "gen2_gsc"` in
an `admission.json` that does not exist, and demands **physical receipts** that are the only thing that turns on
runtime admission.

The good news for a `caps=0` first milestone: **the client is far more nil-tolerant than it looks.** `panel`, `phone`,
`trade` and every SFX path are already guarded (`client.lua:502`, `:1326-1331`, `:663`), and a missing box reader
degrades `pc_boxes` to an empty list rather than failing the hello (`client.lua:1320`, `:312-317`). So *hello + party +
link-by-key + faint* need **no new engine-site data at all** — only the composition path and a `wire` shim.

The bad news: **catch detection is not free.** The Gen 2 client resolves an acquisition by diffing a party/box baseline
that it only *updates* on a signalled frame (`client.lua:196-202`, `lua/gen2/signals.lua`), and Polished has no
`engine_signals.json`. That is the one milestone-2 item that is not optional.

---

## 1. The hard-bind table

Every row is a place the Gen 2 client binds a **pret/pokecrystal-or-Gold/Silver** fact. "Polished binding needed" is what
has to replace it. `PROVIDED` means `lua/gen2/polished.lua` already does this and the work is *wiring*, not *authoring*.

### 1.A Composition / admission (the P0 blocker)

| # | vanilla bind | file:line | Polished binding needed | state |
|---|---|---|---|---|
| A1 | `Entry.build` refuses `polished` before `compose()` | `lua/gen2/entry.lua:597-603` | route `polished` into a composition | **MISSING** |
| A2 | `local titles = {"crystal","gold","silver"}` — Polished absent from the admission catalog loop | `lua/gen2/entry.lua:185` | add `polished`, or a separate `admit_polished` catalog (already exists: `lua/gen2/entry.lua:615-619`) | **PROVIDED** (`:615`) |
| A3 | `Entry.PACK_FILES.polished_crystal` ships **only** `profile` + `charmap` | `lua/gen2/entry.lua:79-82` | 13 more pack files, or a relaxed `order` | **MISSING** |
| A4 | `load_pack` iterates a 15-key `order` and indexes `files[key]` — `nil` for 13 of them on Polished | `lua/gen2/entry.lua:186-187`, `:202-205` | a Polished `load_pack` that only requires `profile`+`charmap` | **MISSING** |
| A5 | `load_pack` asserts `matrix.foundation == "gen2_gsc"` on `admission.json` | `lua/gen2/entry.lua:235` | Polished has **no `admission.json`**; its own `P.admit` already asserts the provenance pin instead | **PROVIDED** (`lua/gen2/polished.lua:88-90`) |
| A6 | `load_pack` asserts `count == 1` title in the profile wrapper | `lua/gen2/entry.lua:210-211` | Polished's wrapper has one title (`polished`); `P.load` checks a different set | **PROVIDED** (`lua/gen2/polished.lua:67-74`) |
| A7 | `Entry.RECEIPT_FILES` has no `polished_crystal` key → `proofs()` returns nil | `lua/gen2/entry.lua:91`, `:288-290` | receipts, or an explicit dev-grade bypass that does **not** claim `PHYSICAL_RECEIPTED` | **MISSING** |
| A8 | `proofs()` is the only thing that grants runtime admission, via `S.qualified_sites` + `M.qualified` | `lua/gen2/entry.lua:295-305` | Polished ships none; `qualification="DEV_OVERLAY_SHA1"` is already honest (`lua/gen2/polished.lua:112`) | **PROVIDED** |
| A9 | `compose()` calls `Reads.new(profile, io_, decode_name)` — the **vanilla** decoder | `lua/gen2/entry.lua:419` | call `P.new(...)` and take its `r` | **PROVIDED** (`lua/gen2/polished.lua:198`) |
| A10 | `rom_type = def.rom_type` from `Entry.PACKS` | `lua/gen2/entry.lua:553` | `"polished_crystal"` — matches the server's `_ROM_TYPES` | **PROVIDED** (`lua/gen2/polished.lua:16`) |
| A11 | `client.foundation` is hard-coded `"gen2_gsc"` | `lua/gen2/client.lua:97` | unchanged and correct — the server also says `foundation="gen2_gsc"` | **CLEAN** |
| A12 | header family `PKPCRYSTAL="polished"` | `lua/gen2/entry.lua:579` | already present | **CLEAN** |

### 1.B Identity / wire shapes

| # | vanilla bind | file:line | Polished binding needed | state |
|---|---|---|---|---|
| B1 | `local mon_key = wire.mon_key` — **the client always calls `wire.*`** | `lua/gen2/client.lua:219` | a `wire`-shaped object exposing `mon_key`/`party_entry`/`foe_entry`/`box_entry` | **PROVIDED, not wired** |
| B2 | vanilla key `DDDD:OOOO:SS` (2 DV bytes) | `lua/gen2/wire.lua:73` | `DDDDDD:OOOO:SSS:TT` — 3 DV bytes, 9-bit species, trait byte | **PROVIDED** (`lua/gen2/polished.lua:131`) |
| B3 | server key regex is 4-group | — | `server/adapters/gen2_polished.py:35` | **CLEAN** |
| B4 | `wire.party_entry(m, active_slot, stages)` | `lua/gen2/client.lua:235` | `P.party_entry(mon, active_slot, stages)` — **identical arity** | **PROVIDED** (`lua/gen2/polished.lua:161`) |
| B5 | party blob is whatever `wire.party_entry` emits | `lua/gen2/client.lua:240` | exactly **140 hex chars** = 48+11+11 | **PROVIDED** (`lua/gen2/polished.lua:167-168`) |
| B6 | `wire.foe_entry(foe, stages.wire)` | `lua/gen2/client.lua:265-266` | `P.foe_entry(mon, stages)` — identical arity | **PROVIDED** (`lua/gen2/polished.lua:184`) |
| B7 | `wire.box_entry(m, box_index)` | `lua/gen2/client.lua:322` | Polished box entries come from a **pokedb enumerate**, not a party/box struct walk | **MISSING** |
| B8 | `hello_identity()` joins player/foundation/kind/sha1/ot_id with `\|` | `lua/gen2/client.lua:349-357` | unchanged — `read_player().ot_id` exists | **PROVIDED** (`lua/gen2/polished.lua:398`) |

### 1.C `reads.*` the client calls (contract already met unless marked)

| # | call site | vanilla bind | Polished binding | state |
|---|---|---|---|---|
| C1 | `lua/gen2/client.lua:226` | `reads.read_party()` | `P.new(...).read_party()` | **PROVIDED** (`lua/gen2/polished.lua:373`) |
| C2 | `:246`, `:533`, `:969`, `:1343`, `:1509`, `:1592`, `:1685`, `:1804` | `reads.read_battle()` | `read_battle()`; needs `c.WILD_BATTLE=1`, `c.TRAINER_BATTLE=2`, `c.BATTLERESULT_BITMASK=192` | **PROVIDED** (profile has all three) |
| C3 | `:249`, `:266` | `reads.read_stat_stages(side)` returning `.wire` | 7 entries, neutral 6; needs `BASE_STAT_LEVEL=7`, `MAX_STAT_LEVEL=13`, `NUM_LEVEL_STATS=8` | **PROVIDED** (`lua/gen2/polished.lua:521`; profile has all three) |
| C4 | `:265`, `:1168` | `reads.read_battle_mon("enemy")` | needs `battle_struct` = **35 B**, `SpAtk`/`SpDef` renamed | **PROVIDED** (`lua/gen2/polished.lua:489`; RAM.md §2.3) |
| C5 | `:274` | `reads.read_pocket("balls")` | `derived.pockets.balls` = `{capacity 25, count "wNumBalls", data "wBalls"}` | **PROVIDED** (profile + `lua/gen2/polished.lua:424`) |
| C6 | `:282` | `reads.read_map()` | `wMapGroup`+1/wMapNumber+2 geometry | **PROVIDED** (`lua/gen2/polished.lua:404`) |
| C7 | `:312` | `reads.read_current_box_num()` | `wCurBox`, 0-based, gate `>= c.NUM_BOXES` | **PROVIDED** (`lua/gen2/polished.lua:445`; `NUM_BOXES=20`) |
| C8 | `:343`, `:350`, `:421`, `:1312`, `:1405` | `reads.read_player()` | `wPlayerID`/`wPlayerGender`/`wPlayerName`, NAME_LENGTH 11 | **PROVIDED** (`lua/gen2/polished.lua:388`) |
| C9 | `:1313`, `:1406` | `reads.read_badges()` | needs **two** badge bytes, `NUM_JOHTO_BADGES=8`, `NUM_KANTO_BADGES=8` | **PROVIDED** (`lua/gen2/polished.lua:413`; profile has 8/8) |
| C10 | `:1509`, `:1592` | `wram_byte("wLinkMode")` | `wLinkMode` **is** in the Polished `ram` table | **PROVIDED** (verified present) |
| C11 | `:317` | `reads.read_active_box()` | **no analogue** — Polished has no `sBox`/`sBoxMons` (RAM.md §1.4 "the boxes are gone") | **MISSING** |
| C12 | `:317` | `reads.read_storage_box(box)` | same | **MISSING** |
| C13 | `:318-321` | loop `0 .. c.NUM_BOXES-1` | Polished `NUM_BOXES = 20`, boxes 1-based in SRAM, `wCurBox` 0-based | **MISSING** (newbox reader) |
| C14 | `:196-202` | `io.read_range(addr, n, "System Bus")` + `io.bank_valid(bank, addr, n)` | `P.new` uses the **identical** contract (`lua/gen2/polished.lua:341-348`) | **CLEAN** |

### 1.D Struct geometry (why vanilla `reads.lua` must not be reused)

`docs/polished/RAM.md:392-398` already ranks these; the client-side consequence:

| # | vanilla bind | file:line | Polished | state |
|---|---|---|---|---|
| D1 | `party_struct == 48` asserted | `lua/gen2/writes.lua:64`, `lua/gen2/reads.lua:60` (per RAM.md §5.1) | still 48 B, **different middle** — 6 one-byte EVs at +11, 3 DV bytes at +17, personality +20, ext-species/form +21, PP +22 | **PROVIDED** (`lua/gen2/polished.lua:270-283`) |
| D2 | `battle_struct == 32`, fields built by name concatenation (`SpclAtk`/`SpclDef`) | `lua/gen2/reads.lua:491-493` (per RAM.md §5.2) | **35 B**, `SpAtk`/`SpDef` | **PROVIDED** (profile `derived.battle_struct_size=35`) |
| D3 | `NUM_POKEMON = 251`, one-byte species | `lua/gen2/reads.lua:105-106` (per RAM.md §5.5) | **9-bit species + form**, `MAX_SPECIES = 0x1FF` | **PROVIDED** (`lua/gen2/polished.lua:21`, `:244`) |
| D4 | `wPartySpecies` is a real list | `lua/gen2/reads.lua:221-227` (per RAM.md §1.2) | Polished has `ds 7 ; unused` — **byte offsets still hold, so a vanilla geometry gate passes on a meaningless range** | **PROVIDED** (`lua/gen2/polished.lua:311-313` derives from `wPartyMons`/`wPartyCount` instead) |
| D5 | bag: 3 pockets, ball ids from the items list | `lua/gen2/client.lua:271-277` | **5 pockets**, capacities all changed (items 20→75, balls 12→25) | **PROVIDED** for read; nuzlocke ball-id **set** is wrong (RAM.md §5.7) |
| D6 | trainer class ids | `lua/gen2/client.lua:223-224` | all moved; `RIVAL0/1/2` + `LYRA1/2` unmodelled | **server side only**; `class*256+id` packing unchanged |
| D7 | `wScriptRunning` safe-state | RAM.md §1.2 | still exists, `01:D437` | **NOT YET BOUND** (no `write_checkpoint.json`) |

### 1.E Writes / checkpoint (all out of scope for `caps=0`)

| # | vanilla bind | file:line | Polished binding needed | state |
|---|---|---|---|---|
| E1 | `writes.lua` `W.SYM` four literals: `wCurPlayerMove`, `wCurOTMon`, `wOTPartyCount`, `wOTPartyDataEnd` | `lua/gen2/writes.lua:37-41`, table at `docs/polished/RAM.md:37-51` | all four **moved**; profile has no `W.SYM` analogue | **MISSING** |
| E2 | `writes.lua:94` derives `ot_species = ot_block + 1` as an enemy species list | RAM.md:52 | in Polished those 7 bytes are `wMirrorHerbPendingBoosts` — writing there **clobbers Mirror Herb state** | **MISSING, and hazardous** |
| E3 | `checkpoint_pc` (the hooked PC for writes + hello readiness) | `lua/gen2/entry.lua:540`, consumed `lua/gen2/client.lua:1441-1446` | needs `view.checkpoint.primary.execution_before.pc`; Polished has no receipts | **COMPOSED (correction 2026-10-06; this row first read "STILL MISSING")**: `checkpoint_pc=Overworld.HOLD.pc` (`lua/gen2/entry.lua` ~846-850, 923-924) dispatches deferred writes at 25:51BF (the `call z, DelayFrame` inside `NextOverworldFrame`) when the live overworld predicates hold (`wMapStatus == MAPSTATUS_HANDLE`, no script, no pause, out of battle, no link, no backup save; `lua/gen2/polished_overworld.lua`). This synchronous hook is not a CPU hold and not a PHYSICAL receipt; qualification stays `DEV_OVERLAY_PREDICATE_HOLD`. The returned checkpoint is `parts.overworld.checkpoint`; the top-level `parts.checkpoint` stays nil. A PC checkpoint receipt is still not invented |
| E4 | `battle_hold` / `battle_bench` / `rival_swap` gates | `lua/gen2/entry.lua:543-549`, `lua/gen2/client.lua:1448-1450` | all three require receipts; with none, all three are `nil` and the hooks simply never arm | **SAFE BY OMISSION** |
| E5 | `contest_mask` → `contest_masked()` | `lua/gen2/entry.lua:550`, `lua/gen2/client.lua:487-488` | needs `write_checkpoint.json:contest_mask`; without it, what does it default to? **UNVERIFIED** | **UNVERIFIED** |
| E6 | `Boxes.executor` / `cart_gate` — every span re-proves a receipt kind | `lua/gen2/entry.lua:495-510` | no receipts ⇒ `covers()` false ⇒ every box write refuses **before a byte** | **COMPOSED (card POL-WRITES; corrected 2026-10-06, the first version said `box_mon` only and `party_mon` refused by name)**: `lua/gen2/polished_overworld.lua` O.writes (declared ranges, one reason each) + O.boxes + O.client_boxes (`lua/gen2/entry.lua` ~909-912). Deposit (`box_mon`) and withdraw (`party_mon`, savemon to party stat/PP reconstruction in `lua/gen2/polished_stats.lua`) are composed. `memorialize` STILL REFUSES (`polished_overworld.lua` ~854-855) pending the owner memorial-box ruling (NEWBOX §6.2); the memorial helper `move_to_memorial` exists in `lua/gen2/polished_boxes.lua`. Composition is DEV evidence, not PHYSICAL qualification |

### 1.F Engine sites / signals (the P5a catch-detection blocker)

| # | vanilla bind | file:line | Polished binding needed | state |
|---|---|---|---|---|
| F1 | `signals = Signals.new(options)` with `pack = data.sites` | `lua/gen2/entry.lua:472-474` | Polished has **no `engine_signals.json`** | **MISSING** |
| F2 | client builds `points` from `site.point_symbols` | `lua/gen2/client.lua:200-202` | empty `sites` is safe (`pairs({})`); point scalars like `wBattleScriptFlags` simply have no fallback | **DEGRADES** |
| F3 | production signals require `proof.engine` (a U1 receipt) | `lua/gen2/entry.lua:478` | no receipt ⇒ the candidate/`new_model` path, or a Polished path that registers a reduced site set | **MISSING** |
| F4 | site shape: `{rom_offset, expected_hex, prelude, point_symbols}` | `lua/gen2/entry.lua:270-271`; generator spec `docs/gen2/gen2_engine_sites.md:15-16` | a Polished engine-site pack generated the same way, or hand-listed from the pinned source | **MISSING** |

### 1.G Box census (PokeDB, not boxes)

`docs/polished/RAM.md:106` states the vanilla `sram_bank` box symbols are **gone**. `docs/polished/NEWBOX.md` is the
replacement spec and is already precise enough to implement:

| # | vanilla bind | file:line | Polished binding needed | state |
|---|---|---|---|---|
| G1 | `read_active_box()` reads `sBox` + `sBoxMons` (32 B/slot) | `lua/gen2/client.lua:317` | NEWBOX §1.2: 20 gameplay records at flat `0x30E4 + 0x21*(b-1)`, each 33 B; `Entries[20]` + `Banks[3]` + `Name[9]` + `Theme[1]` | **SPEC READY** (`docs/polished/NEWBOX.md:15-42`) |
| G2 | `read_storage_box(box)` reads the backing SRAM box | `lua/gen2/client.lua:317` | same, via the pokedb: 2 banks × 207 entries × 3 sections (A/B/C), 49-byte `savemon_struct` | **SPEC READY** (`docs/polished/NEWBOX.md:42-62`) |
| G3 | slot → mon decode is a struct read | `lua/gen2/client.lua:318-324` | NEWBOX §6.1 is a **13-step pointer walk** (entry index → bank bit → flat pokedb → checksum → species/form/egg/level/ot/dvs/nickname/ot) | **SPEC READY** (`docs/polished/NEWBOX.md:221-239`) |
| G4 | box entry wire shape | `lua/gen2/client.lua:322` `wire.box_entry(m, box)` | needs a Polished box-entry shape; `polished_codec.decode_savemon` exists server-side | **MISSING on the client** |

### 1.H Panel / phone / trade / sfx capability gates

| # | vanilla bind | file:line | Polished binding needed | state |
|---|---|---|---|---|
| H1 | `panel` built only in production, needs `deps.hud` | `lua/gen2/entry.lua:514-517` | optional; `nil` is tolerated downstream | **OPTIONAL** |
| H2 | `phone` needs `profile.overlay.phone.stage` (`wUnusedMapBuffer`, **absent** in Polished — HOOKS.md:129) | `lua/gen2/entry.lua:518-523` | a new `$C633` staging span (HOOKS.md §5.1) | **MISSING** |
| H3 | `trade` requires `profile.overlay.trade` | `lua/gen2/entry.lua:530-533` | Polished's profile `overlay` block has `abi/artifact/base_sha1/md5/ram/rom_sha1/sym/sym_sha256` — **no `trade`, no `phone`** | **MISSING** |
| H4 | `panel:present()`/`abi()`/`sfx_present()`/`companion_abi()` drive the hello caps fields | `lua/gen2/client.lua:1325-1329` | all four are `panel and … or false/0/nil` guarded ⇒ **`nil` panel is safe** | **SAFE at caps=0** |
| H5 | `request_sfx_local` | `lua/gen2/client.lua:502-512` | with `panel == nil`, `code` is `nil`, the early return is skipped, and it logs once | **SAFE at caps=0** |
| H6 | `trade_prepare = self:trade_live()` | `lua/gen2/client.lua:1331`, `:663` | needs `trade` non-nil; **UNVERIFIED** whether `trade_live()` guards a nil `trade` | **UNVERIFIED — falsifier required** |
| H7 | `awaiting_save_field()` | `lua/gen2/client.lua:1001`, `:1418` | checkpoint-derived; with no checkpoint it must degrade, not throw | **UNVERIFIED** |

### 1.I hello / tick payload — what must survive unchanged

Payload field lines: `local payload = {` at `client.lua:1314`, `send("hello", payload)` at `:1334`,
`send(event or "tick", {` at `:1407`. The field set is fixed and the server already expects it, so **the field set must not change**. Full construction:
`lua/gen2/client.lua:1295-1334` (hello) and `:1395-1420` (tick).

Fields a Polished run must supply, with the reader that supplies them:

| field | source | Polished reader |
|---|---|---|
| `rom_type`, `foundation`, `artifact_kind`, `rom_sha1` | `client.lua:1315` | `:97`, `entry.lua:553` |
| `party` | `client.lua:1316` ← `snapshot_party()` `:242-260` | `read_party` + `P.party_entry` |
| `ot_id`, `trainer_name` | `client.lua:1316` | `read_player` |
| `has_pokeballs`, `ball_count` | `client.lua:1317` | `read_pocket("balls")` |
| `badges`, `kanto_badges` | `client.lua:1319` | `read_badges` |
| `area_id`, `loc_name` | `client.lua:1320` ← `area_of()` `:281-287` | `read_map` **+ an `area_map.json` that does not exist** |
| `pc_boxes`, `pc_boxes_generation` | `client.lua:1320` | degrades to `[]`/`nil` without a box reader |
| `writes_enabled`, `rom_sha1`, `in_battle` | `client.lua:1321`, `:1322` | fine |
| `panel`, `panel_abi`, `sfx`, `companion_abi` | `client.lua:1325-1329` | `false`/`0`/`false`/`nil` at caps=0 |
| `trade_prepare` | `client.lua:1331` | see H6 |
| tick-only: `trainer_id`, `is_trainer_battle`, `enemy_party`, `trade_blocked`, `awaiting_save` | `client.lua:1407-1419` | `read_battle` ✓; `trade_blocked` `client.lua:1417` / `awaiting_save` `client.lua:1418` see H7 |

---

## 2. What runs at `caps=0`, and what needs new data

### 2.1 Runs with the shipped code, once composition is wired

| capability | needs | blocker |
|---|---|---|
| `hello` with a full party | `read_party` + `P.party_entry` + `read_player` + `read_battle` | composition (A1, A4) + `wire` shim (B1) |
| `tick` | the same, plus `read_stat_stages` | same |
| `ball_count` / nuzlocke gate | `read_pocket("balls")` | none |
| **link-by-key** | only the key string | `P.mon_key` + server regex already agree |
| **death / faint handling** | party HP in the snapshot | none beyond the above |
| `panel`/`sfx`/`phone`/`trade` | nothing — all guarded | **already `caps=0`-clean** |
| `pc_boxes` | a box reader | degrades to `[]`; **no hello failure** |
| area naming | `area_map.json` | missing ⇒ `area_id=""`, `loc_name="map_G_N"`; links still form, **display and dead-zone naming degrade** |

### 2.2 Genuinely blocked on new engine-site / checkpoint data

| capability | needs | doc |
|---|---|---|
| **catch / acquisition detection** | an engine-site pack: the client only settles a snapshot on a signalled frame, so a new monKey appears only if a site fires | `docs/gen2/gen2_engine_sites.md:1-3` (43 candidates *per pret title*, `physical_firing OPEN`); `lua/gen2/signals.lua` |
| any battle write (`force_faint` in battle) | a `battle_faint` receipt + a hold site | RAM.md §1.2 (`wScriptRunning 01:D437` exists but is unbound) |
| bench faint, rival swap, explode | `battle_bench` receipt | `entry.lua:543-549` |
| box **deposit** | **DONE (card POL-WRITES)**: the newbox writer over the same coordinates the census reads, both halves read back (`tests/unit/test_polished_write_path.py`) | `lua/gen2/polished_overworld.lua` |
| box **withdraw** / memorialize | the savemon → `party_struct` direction (stats + PP reconstructed: `CalcPkmnStats` predef, `engine/pc/bills_pc.asm:923`) and the memorial-box ruling | `docs/polished/NEWBOX.md:239-278` |
| hello **readiness** gating | `checkpoint_pc` | `entry.lua:540` |

`docs/polished/HOOKS.md:349-352` still carries the Polished-specific unverified list (`$7E` linker acceptance, `$FF` lower
bound, `ResetWRAM` span, the phone-stage span, the trade stack fingerprint). **No `ENGINE_SITES.md` exists** in
`docs/polished/` — the engine-site spec for Polished has not been written yet, which is why F1/F4 are "MISSING" rather
than "authored".

---

*(sections 3-5 follow: the card list, the open items, and the CLAIMS block)*
---

## 3. Dependency-ordered cards

Exclusive files = the only files a card may touch. **First falsifier** = the cheapest thing that kills the card's
premise *before* any code is written. **Exit evidence** = the observable that closes it.

Order is a strict dependency chain for milestone A, then milestone B.

### Milestone A — "the client composes and says hello to the server with a party"

#### C-KEY — one key format, three places
**Exclusive files:** `lua/gen2/polished.lua`, `lua/gen2/client.lua`
**Premise:** there is exactly one place the mon key is built.
**Falsifier (already fired):** there are **three**, and only one is polymorphic.
- `lua/gen2/wire.lua:73` `M.mon_key` -> `%04X:%04X:%02X` over a **16-bit** `dv_word`
- `lua/gen2/signals.lua:55-58` a **private** `key()` -> `%04X:%04X:%02X`, gated `integer(mon.species_id,1,251)`
  and reading `mon.dv_word`; Polished exposes **`dv_bytes` (24-bit)** and species to **511**
- `lua/gen2/client.lua:470` `target:sub(1,9)` / `tonumber(target:sub(11),16)` — positional slicing of the key
  string. On `DDDDDD:OOOO:SSS:TT` that yields `DDDDDD:O` and `OO:SSS`, so **`tonumber` returns `nil`**.
**Change:** add `P.wire()` returning `{mon_key, party_entry, foe_entry, box_entry}` and inject it wherever
`wire` is injected (`entry.lua:536`); give `signals` an injected key builder instead of the module-local one;
replace the positional slice with a parsed key.
**Exit evidence:** a table-driven check over one decoded Polished record asserting all three sites emit the same
string, that string matching `server/adapters/gen2_polished.py:35`, and that the evolved-identity fallback at
`client.lua:470` returns a mon instead of `nil`.
**Risk if skipped:** silent. The client composes, links by key, and only misfires on an *evolved* death — the
rarest path in a run.

#### C-PACK — a Polished pack load that asks for 2 files, not 15
**Exclusive files:** `lua/gen2/entry.lua`
**Premise:** `compose` can share `load_pack`.
**Falsifier (already fired):** `Entry.PACK_FILES.polished_crystal` (`entry.lua:79-82`) supplies `profile` and
`charmap`; `load_pack`'s `order` (`entry.lua:186-187`) indexes 15 keys and `root .. "/" .. nil` at
`entry.lua:203`. Separately `entry.lua:235` asserts `matrix.foundation == "gen2_gsc"` on an `admission.json`
Polished does not ship, and `entry.lua:200` refuses `def.dev`.
**Change:** a `load_pack_polished` that delegates to `P.load(root, json)` (`lua/gen2/polished.lua:65`) and
returns `data = {profile, charmap}`, skipping the `admission`/`sites`/`checkpoint`/`area_map` asserts.
**Exit evidence:** composition reaches the `client.new(...)` call at `entry.lua:534` without a pcall error;
a deliberately broken `charmap.source.lock_sha256` still refuses.

#### C-SIGNALS — the binder's three vanilla literals
**Exclusive files:** `lua/gen2/signals.lua`
**Premise:** signals is title-generic.
**Falsifier (already fired):**
- `signals.lua:529` `assert(({crystal=true,gold=true,silver=true})[title])` — a hard refuse on `"polished"`
- `signals.lua:56` `integer(mon.species_id,1,251)`
- `signals.lua:1045` `need(integer(old,0,13) and integer(requested,0,13))` — Polished boxes are **0..19**
**Change:** take the title allow-list, the species ceiling and the box ceiling from the profile
(`constants.NUM_BOXES = 20`, `MAX_SPECIES = 0x1FF`), not from module literals.
**Exit evidence:** `Signals.new` with `title="polished"` and one Polished mon returns a binder instead of raising;
a box-change event with `requested=19` is accepted and one with `requested=20` is refused **by name**.

#### C-COMPOSE — a production graph under a dev-grade authority (reaches **milestone A**)
**Exclusive files:** `lua/gen2/entry.lua`
**Premise:** a Polished hello can be produced from the shipped data.
**Falsifier (already fired):** `proofs()` returns `nil, "no shipped PHYSICAL ... receipts"` at `entry.lua:290`,
because `Entry.RECEIPT_FILES` (`entry.lua:91`) has no `polished_crystal` key — and `compose` then hard-asserts
at `entry.lua:424`, so there is **no** path that skips it today.
**Change:** a Polished compose that builds `reads`/`writes`/`rom`/`boxes`/`client` with
`panel=phone=trade=nil`, a `safety` stub that refuses every kind, and `qualification="DEV_OVERLAY_SHA1"` carried
through from `lua/gen2/polished.lua:112`. Keep `production=true` semantics for `client` construction, but make
`checkpoint`/`write_policy` **optional** rather than required — every consumer (`client.lua:1441`, `:1448`) already
guards on nil.
**Exit evidence:** against a real server with a client at `player=a`, `GET /api/debug/manual_link_data` lists the
Polished `rom_type`; the server's `player_identity` locks the Polished OT id; `/api/status` shows the party rows
with sprite HTML. **This is milestone A.**
**Known trap:** `Entry.admit` refuses `clean` rows outright (`entry.lua:337-340`) and Polished's own `P.admit`
does the same (`polished.lua:99-102`) — both messages are correct; do not "fix" them.

#### C-AREA — area naming (non-blocking for A)
**Exclusive files:** `tools/gen_polished_pack.py`, `data/games/polished_crystal/area_map.json`
**Premise:** `area_of()` at `client.lua:281-287` needs `area_map[group*256+number]`.
**Falsifier:** the Polished pack has **no `area_map.json`**. Links still form (the server keys on `area_id`, and
`""` is a valid key), but the status page shows `map_G_N` and dead-zone/gift naming loses its labels.
**Exit evidence:** `area_of()` returns a real `area_id` for New Bark Town.

### Milestone B — "detects a catch"

#### C-SITES — a Polished engine-site pack (reaches **milestone B**)
**Exclusive files:** `docs/polished/ENGINE_SITES.md` (new), the Polished site spec generator, `data/games/polished_crystal/engine_signals.json`
**Premise:** the client notices a new mon without a signal.
**Falsifier (already fired):** it cannot. The client only settles a snapshot on a signalled frame
(`client.lua:196-202` builds `points`; the dispatch is `on_observation` `client.lua:1165-1226` and `on_event`
`client.lua:1229-1273`), and `capture` is emitted from an `ev.acquisition` at `client.lua:1154-1158`. With no
sites there is no `ev`. `docs/gen2/gen2_engine_sites.md:6` is explicit that even the pret packs are
`physical_firing OPEN` / `runtime_enabled false` — Polished has not even been enumerated.
**Exit evidence:** one live acquisition observed on a real cartridge: the client emits `capture`
(`client.lua:1156`) with the Polished key, and the server's `pending_captures` gains a row. **This is milestone B.**
**Scope note:** the minimum viable site set is *one* acquisition signal (capture/gift/egg) plus whatever gates
"we are in an overworld frame"; it does **not** need the full 25-family census.

#### C-BOX — the newbox census (independent of B; unlocks `pc_boxes` and every box write)
**Exclusive files:** `lua/gen2/polished.lua` (add `read_boxes`), `lua/gen2/boxes.lua` (only if a writer is added)
**Premise:** a box is a slot array of 32-byte records.
**Falsifier (already fired):** `docs/polished/RAM.md:106` — the vanilla `sram_bank` box symbols are *gone*.
Polished PC storage is a 20-record metadata table (`docs/polished/NEWBOX.md:15`) over a 2x207-entry pokedb of
49-byte `savemon_struct` records (`docs/polished/NEWBOX.md:42`), reachable only by the pointer walk in
`docs/polished/NEWBOX.md:221`.
**Change:** implement NEWBOX 6.1 verbatim as `read_boxes()`; return `box_complete=true` only when every one of
the 20 records and every referenced entry decoded (the client already treats a partial scan as "not a census",
`client.lua:318-322`).
**Exit evidence:** one real deposited mon enumerates with a matching key and level, and `pc_boxes` appears in the
hello payload with a non-null `pc_boxes_generation`.

### Deferred — explicitly out of scope until A and B land

| item | why deferred | doc |
|---|---|---|
| `battle_faint` / `battle_bench` / rival swap | need receipts that do not exist | `entry.lua:543-549` |
| any box **write** (deposit/withdraw/memorialize) | needs NEWBOX 6.2/6.3 **and** a box receipt | `docs/polished/NEWBOX.md:239`, `:257` |
| phone | `wUnusedMapBuffer` is absent; needs a `$C633` staging span | `HOOKS.md:129`, `entry.lua:518-523` |
| trade | `profile.overlay` has no `trade` block | `entry.lua:530-533` |
| SFX / panel | needs a later overlay milestone (caps != 0) | `entry.lua:514-517` |
| `writes.lua` `W.SYM` four literals | all four moved; one clobbers Mirror Herb state | `docs/polished/RAM.md:37-51`, `:52` |

---

## 4. Open items and UNVERIFIED

| # | question | what would settle it |
|---|---|---|
| U1 | `trade_live()` (`client.lua:663`) called with `trade == nil` (`client.lua:1331`) — raises or returns false? | read `client.lua:663-700`; one line either way. **This is the only P0 unknown.** |
| U2 | `contest_masked()` (`client.lua:487-488`) with no `p.contest_mask` — nil comparison or error? | read `client.lua:485-500` |
| U3 | `awaiting_save_field()` (`client.lua:1001`) with no checkpoint | read `client.lua:1000-1010` |
| U4 | Does `AREA_BATTLE_TYPES = {0,4,8}` (`client.lua:48`) match Polished's `wBattleType` enum? | read `engine/battle/core.asm` in the pinned source; RAM.md does not cover it |
| U5 | Is `signals.lua`'s `ev.acquisition` vocabulary (`egg_hatch`/`gift`) reachable at all on Polished without the full site set? | the C-SITES design |
| U6 | Polished's `wLinkMode` values | RAM.md 1.2 does not list it; confirmed present in the profile only |
| U7 | Nuzlocke ball-id set (RAM.md 5.7 — five pockets, capacities and ids differ) | a Ball id table in the pinned source |
| U8 | BizHawk `WRAM`-domain offset for pokedb bank 2 | `docs/polished/NEWBOX.md` 7 lists this as open; System Bus + `SVBK` is the stated fallback |

**Two architectural facts worth stating plainly, because they change how the cards are sized:**

1. **`lua/core/{session,deferred,identity}.lua` are not bound by Gen 2 today.** The Gen 2 client is a
   self-contained state machine with its own `send`, its own pre-hello hold and its own deferred queue; the core
   modules are the generation-neutral *target* the work list converges on, not the current binding. **No card in
   section 3 should try to route Gen 2 through `lua/core` first** — that is a separate migration with its own risk.
2. **`qualification` must stay honest.** The vanilla graph claims `"PHYSICAL_RECEIPTED"`
   (`entry.lua:567`). Polished can only ever claim `"DEV_OVERLAY_SHA1"` (`lua/gen2/polished.lua:112`). Never let a
   refactor let Polished reach the former — it would assert a proof that was never made.

---

## 4b. Open items settled

Closed by reading the source and, where the question is a Lua behaviour, by **executing the real module under lupa
(Lua 5.5)** with the stubs the composition would supply. Method for U1-U3: `dofile` the shipped
`lua/gen2/client.lua`, `lua/gen2/wire.lua`, `lua/hello_session.lua`, `lua/reply_dispatch.lua`,
`lua/owed_reports.lua`, construct with `trade=nil`, `contest_mask=nil`, `checkpoint_pc=nil`, `battle_hold=nil`,
and a stub `reads`/`io`/`net`; then call the real methods. Printed results verbatim below.

### U1 — `trade_live()` with `trade == nil`: **returns `false`, does not raise**. SETTLED.
Source: `lua/gen2/client.lua:663-664`
```lua
function self:trade_live()
    return trade ~= nil and self.artifact_kind == "overlay" and trade:advertised()
```
`trade ~= nil` short-circuits, so `trade:advertised()` is never evaluated.
Execution:
```
construct=ok  trade=nil contest_mask=nil checkpoint_pc=nil battle_hold=nil
U1  pcall=true  trade_live()=false  type=boolean
```
Confidence: **certain** (read + executed).

### U2 — `contest_masked()` with no `contest_mask`: **returns `false`**, never indexes `io`. SETTLED.
Source: `lua/gen2/client.lua:487-490`
```lua
local contest = p.contest_mask
local function contest_masked()
    if not contest or io.bank_valid(contest.bank, contest.address, 1) ~= true then return false end
```
`not contest` is true when `p.contest_mask` is nil, so `io.bank_valid` is never reached.
**Risk removed:** the C-COMPOSE card can leave `contest_mask=nil` with no guard.

### U3 — `awaiting_save_field()` with no checkpoint: **returns `nil` (field absent)**. SETTLED.
Source: `lua/gen2/client.lua:1001` calls `burial_waiting()` (`lua/gen2/client.lua:994`), which only iterates
`self.settle` — a client-local table that exists regardless of checkpoint. So `awaiting_save` is simply **omitted
from the tick**, which is the documented idle-tick behaviour.

U2 and U3 together, one execution (`send_tick` on the real client):
```
U2U3 send_tick pcall=true  (NO RAISE)
U2U3 messages=0
```
`messages=0` is correct and is not a failure: `send_tick` builds the payload table (so `contest_masked()` and
`awaiting_save_field()` both **run** — they are table-constructor arguments at `lua/gen2/client.lua:1417-1418`,
evaluated before `send`), and only then hits the pre-hello hold, because a fresh `HelloSession` is not `ready`.
That is the pre-hello gate doing its job, not a swallow.

### C-WRITE — the overworld write path (card POL-WRITES, 2026-10-04)

`compose_polished` now composes `lua/gen2/polished_overworld.lua`: the hold, the armed writer and the `box_mon`
executor. **No checkpoint PC is invented** — `write_checkpoint.json.titles.polished_crystal.primary.execution_before`
is still null, so the hold is the live predicate set (HELLO_GATE §1), and `parts.checkpoint` stays nil while
`parts.overworld` carries `{checkpoint, writes, boxes, census, coords}`.

* `force_faint` writes the keyed record's Status (+32) to 0 and HP (+34..35) to 0 — byte for byte what the vanilla
  writer does (`lua/gen2/writes.lua` `faint_party_slot`; a Gen 2 mon is fainted by HP 0) — and reads both bytes back.
* `box_mon` runs the engine's own two halves in its own order (`UpdateStorageBoxMonFromTemp` then
  `RemoveMonFromParty`, `engine/pc/bills_pc.asm:495-531,539-623`): the newbox entry first (so a reset between the
  halves duplicates the mon and never loses it), then the party compaction, then a census read-back.
* Every byte is inside a declared permit range: the party block, that record's Status/HP, the six pokedb sections,
  the 20 **gameplay** box records and the two allocation-flag windows. `wMirrorHerbPendingBoosts` (01:d284) is
  asserted disjoint and never appears in a range.
* **The hold is a PC hold.** The site is `call z, DelayFrame` at **25:51BF**, inside `NextOverworldFrame`
  (`engine/overworld/events.asm:114`, called from `:99`): the idle-overworld frame wait, reached once per frame after
  `MapEvents` and `HandleMapObjects` and before the next frame's events. `compose_polished` hands the client that PC
  (`checkpoint_pc`), the client hooks it with `io.on_bus_exec` and runs one deferred write **synchronously inside the
  exec**, exactly as the vanilla graph does at `OWPlayerInput`; the predicate set must still hold at that instant, and
  `hROMBank == $25` plus the executed ROM bytes `CC A8 0D` are re-read at check time. The instruction is unique in the
  routine (verified over the executed overlay ROM in `tests/unit/test_polished_write_path.py`, which also re-derives
  every address from the pinned `.sym`). Residual: a *script* runs inside `HandleMapObjects`/`MapEvents` before this
  call in the same frame, so a script that starts and finishes within one frame is still not observable by
  `wScriptRunning` — the hold narrows the window, it does not remove it.
* **Mail is refused, not shifted.** `SwapPartyMons` also swaps `sPartyMon1Mail` (`DoMailSwap`,
  `engine/pc/bills_pc.asm:289-297`, `MAIL_STRUCT_LENGTH = $2f` bytes per slot) and SLink never rewrites that SRAM
  block, so a deposit refuses while the removed mon **or any later party slot** holds Mail (`ItemIsMail`,
  `home/header.asm:114`: item >= FIRST_MAIL; the ids come from `items.json` `mail_ids`) — the vanilla rule
  (`lua/gen2/boxes.lua` `no_mail_from`).
* **The Bug Catching Contest is guarded.** With no `contest_mask` in the pack, a KO during the contest found no party
  mon and was dropped (`client.lua` run_deferred) = a lost Soul Link death. `compose_polished` passes
  `contest_mask = {wStatusFlags2, bit 2}` (`STATUSFLAGS2_BUG_CONTEST_TIMER_F`, `constants/ram_constants.asm:255`,
  `data/events/engine_flags.asm:37` `engine_flag` -> `1 << (2 % 8)` = `$04`), so the command is **held** until the
  contest returns, per ruling (a).
* **The permit re-proves the hold on `arm`.** The box path writes through the permit directly, so
  `writes:arm("box_deposit")` itself re-runs the predicate set instead of trusting the frame-count lifetime token.
* **The server capability stays FALSE.** `Gen2PolishedAdapter.supports_box_mon()` returns False: the executor is
  composed and proven, but advertising it quarantines a solo catch (`state.py:2786`) and the un-quarantine that follows
  a link (`state.py:2896-2912`) is a `party_mon` this client refuses — `sync_retrieve_failed` re-boxes only a partner
  whose key is still in `party_keys` (`state.py:617-643`), so both mons would sit boxed with no retry. The flag flips
  when `withdraw()` is composed.

2026-10-11: supports_box_mon retired by the review fixes; box_mon is always queued.

### U4 — `AREA_BATTLE_TYPES = {0,4,8}` does **NOT** match Polished. **Real bug.** SETTLED.
`BATTLETYPE_*` are bare `const`s, so their values are their ordinal within the `const_def` block.
**Vanilla pokecrystal** (`E:/Google Drive/SLink/.cache/pret/pokecrystal/constants/battle_constants.asm:90-102`):
`NORMAL=0 CANLOSE=1 DEBUG=2 TUTORIAL=3 FISH=4 ROAMING=5 CONTEST=6 FORCESHINY=7 TREE=8 TRAP=9 ...`
⇒ `{0,4,8}` = **{NORMAL, FISH, TREE}**. Correct.

**Polished** (`F:/slink-work/cache/polished/src/constants/battle_constants.asm:106-123`):
`NORMAL=0 CANLOSE=1 TUTORIAL=2 FISH=3 TREE=4 ROAMING=5 CONTEST=6 SAFARI=7 GHOST=8 GROTTO=9 INVERSE=10 TRAP=11 ...`
⇒ `{0,4,8}` on Polished = **{NORMAL, TREE, GROTTO}**.

So on Polished the set is wrong in **both directions**: a **FISH (3)** battle does not resolve its area, and a
**GROTTO (8)** battle falsely resolves one. The single consumer is
`lua/gen2/client.lua:1177` `resolves = battle ~= nil and AREA_BATTLE_TYPES[battle.battle_type] == true`.
A false resolve consumes a real encounter slot — a **dead-zone defect**, not cosmetic.

**Amplifier:** `data/games/polished_crystal/profile.json`'s `constants` carries `WILD_BATTLE=1` and
`TRAINER_BATTLE=2` but **no `BATTLETYPE_*` at all** (verified: the filtered dict is empty), so the fix cannot
read them from the profile today. `tools/gen_polished_profile.py` must emit them, then `AREA_BATTLE_TYPES` moves
into the profile like `NUM_BOXES` already did in the C-SIGNALS card.
Confidence: **certain** (ordinals read from both pinned sources).

### U5 — field sets: the **only** missing decoded-mon field is `dv_word`. SETTLED.
Mechanically extracted by regex over the two modules:
- `wire.lua` reads on a decoded mon: `dv_word, held_item, hp, is_egg, level, max_hp, moves, nickname, ot_id, pp, pp_ups, slot, species_id, status`
- `client.lua` reads on a decoded mon: `held_item, hp, is_egg, key, level, max_hp, nickname, slot, species_id`

`lua/gen2/polished.lua`'s `decode_record` (`lua/gen2/polished.lua:268-301`) plus `traits`/`dvs_of`/`pp_of`/`decode_party_block`
supplies **all thirteen** of `wire.lua`'s reads **except `dv_word`** — it supplies `dv_bytes` (24-bit,
`lua/gen2/polished.lua:249`) instead. All nine of `client.lua`'s reads are present.

The **wire entry shapes are identical**: `polished.lua`'s `common()` emits
`species_id, level, hp, maxHP, status_cond, held_item_id, moves, pp, pp_ups` — exactly the set
`wire.foe_entry` emits — and `P.party_entry` adds the same `key, slot, blob_hex, active, nickname, stat_stages`
that `wire.party_entry` adds.

**Conclusion: the field-diff risk is not "many missing fields", it is exactly one — `dv_word` — and it is the key
input.** That is the whole of C-KEY. Confidence: **certain**.

### U6 — the three key sites, as a proposal (**not applied**)

The unifying rule: **one key builder, injected; the stable prefix is parsed, not sliced.**

**Site 1 — `lua/gen2/wire.lua:73-79`.** `M.mon_key` keeps today's body as the default and delegates when the
record is Polished. Minimal shape: add an optional builder parameter (`M.mon_key(mon, builder)`) or a
`M.set_key_builder(fn)`; `P.wire()` supplies the Polished builder.
```lua
-- PROPOSED, not applied
function M.mon_key(mon, builder)
    builder = builder or M.mon_key            -- recursion guard: default path is today's body verbatim
    ...
```
Executed proof — the proposed dispatcher on the three vanilla records, against the **real** `wire.lua` output:
```
== BEFORE (real lua/gen2/wire.lua M.mon_key) ==
  sample1 -> 1234:ABCD:19
  sample2 -> 0000:0000:01
  sample3 -> FFFF:0001:FB
== AFTER (proposed_key) on the same 3 vanilla records ==
  sample1 -> 1234:ABCD:19   byte-identical=true
  sample2 -> 0000:0000:01   byte-identical=true
  sample3 -> FFFF:0001:FB   byte-identical=true
  VANILLA OUTPUT PRESERVED = true
```

**Site 2 — `lua/gen2/signals.lua:55-58`.** Delete the module-private `key()` and take `options.key_builder`.
Proposed output on three Polished records (3 DV bytes, 9-bit species, trait byte):
```
  polished1 -> 123456:ABCD:019:80
  polished2 -> 000000:0000:001:00
  polished3 -> FFFFFF:0001:1FF:C1
```
Each matches the 4-group shape of `server/adapters/gen2_polished.py:35`.

**Site 3 — `lua/gen2/client.lua:470-472`.** Replace `sub(1,9)` / `tonumber(sub(11),16)` with a parse. The **old**
code on a Polished key, executed:
```
  123456:ABCD:019:80   -> sub(1,9)=123456:AB  tonumber(sub(11),16)=nil
```
— `stable` is wrong **and** `old` is `nil`, so `if not (old and descends(old, mon.species_id))` can never pass and
the evolved-death match silently never fires. The **proposed** parse:
```
  1234:ABCD:19         -> stable=1234:ABCD     species=25   fields=3
  123456:ABCD:019:80   -> stable=123456:ABCD   species=25   fields=4
  FFFF:0001:FF         -> stable=FFFF:0001     species=255  fields=3
  FFFFFF:0001:1FF:C1   -> stable=FFFFFF:0001   species=511  fields=4
```
Both shapes parse; the 3-group case is byte-for-byte what `sub(1,9)` produced.
Confidence: **certain** (executed).

**Rollback:** all three are single-function edits inside files with existing callers; reverting restores today's
behaviour exactly, and the vanilla-output proof above is the regression net.

---

## 5. CLAIMS

All 89 verified by reading the cited line of the cited file.

## Coordinator correction (2026-10-04) to U4

Counting the Polished `const_def` block (`constants/battle_constants.asm:106-123`): NORMAL 0, CANLOSE 1, TUTORIAL 2, FISH 3, TREE 4, ROAMING 5, CONTEST 6, SAFARI 7, GHOST 8, GROTTO 9, INVERSE 10. So on Polished `{0,4,8}` = {NORMAL, TREE, GHOST}; the helper's text calling 8 "GROTTO" is a miscount. The defect stands (FISH 3 is missed, GHOST 8 would falsely resolve); the area-resolving set to emit from the profile is {NORMAL 0, FISH 3, TREE 4}, with GROTTO 9 an owner/design question.

## Amendment (2026-10-04, from omp 01a1072c via the overlord; coordinator spot-checked reads.lua:60-70, :244, :292-294, entry.lua:459-467): decode layer is under-covered

`lua/gen2/rom.lua` is a full ROM encounter-table reader (BaseData, Grass/WaterMonProbTable, TreeMons/TreeMonMaps/RockMonMaps, FishGroups/TimeFishGroups, RoamMaps) built in `Entry.compose` from `profile.rom`; Polished's generated profile has no `rom`, `sram_bank`, `storage_boxes` or `constant_sources` (checked). `reads.lua` cannot be satisfied by that profile as it stands: its dimensions assert (`:60-70`) demands NUM_POKEMON=251, EGG=253, PARTYMON/BOXMON_STRUCT_LENGTH=48/32, BOX_LENGTH=1104, NUM_BOXES=14; `storage_boxes` (`:292`), the `NUM_BOXES/2` bank split (`:294`) and the literal "0..13" (`:244`) are vanilla-only. Accepted card: **C-ROMTABLES** between C-PACK and C-SITES: emit the Polished `rom` table coordinates (and `BATTLETYPE_*`, folding in U4 with the corrected set {0,3,4}) from the overlay sym in `gen_polished_profile.py`, and make `compose` take the Polished reads (`polished.lua`) instead of `reads.lua`, so no vanilla assert is ever asked to hold for Polished. UNVERIFIED: whether Polished's table layouts match vanilla's `rom.lua` readers.

```json
[
 {
  "path": "F:/slink-work/wt/polished/lua/gen2/entry.lua",
  "line": 80,
  "expect": "profile=\"data/games/polished_crystal/profile.json\","
 },
 {
  "path": "F:/slink-work/wt/polished/lua/gen2/entry.lua",
  "line": 81,
  "expect": "charmap=\"data/games/polished_crystal/charmap.lua\","
 },
 {
  "path": "F:/slink-work/wt/polished/lua/gen2/entry.lua",
  "line": 91,
  "expect": "Entry.RECEIPT_FILES = {"
 },
 {
  "path": "F:/slink-work/wt/polished/lua/gen2/entry.lua",
  "line": 185,
  "expect": "local titles = {\"crystal\", \"gold\", \"silver\"}"
 },
 {
  "path": "F:/slink-work/wt/polished/lua/gen2/entry.lua",
  "line": 186,
  "expect": "local order = {\"profile\", \"admission\", \"sites\", \"checkpoint\", \"area_map\", \"statics\", \"encounters\","
 },
 {
  "path": "F:/slink-work/wt/polished/lua/gen2/entry.lua",
  "line": 200,
  "expect": "assert(not def.dev, \"a dev title has no Gen 2 production pack\")"
 },
 {
  "path": "F:/slink-work/wt/polished/lua/gen2/entry.lua",
  "line": 203,
  "expect": "local path = root .. \"/\" .. files[key]"
 },
 {
  "path": "F:/slink-work/wt/polished/lua/gen2/entry.lua",
  "line": 235,
  "expect": "matrix.foundation == \"gen2_gsc\""
 },
 {
  "path": "F:/slink-work/wt/polished/lua/gen2/entry.lua",
  "line": 288,
  "expect": "local group = Entry.RECEIPT_FILES[pack]"
 },
 {
  "path": "F:/slink-work/wt/polished/lua/gen2/entry.lua",
  "line": 290,
  "expect": "\"no shipped PHYSICAL \""
 },
 {
  "path": "F:/slink-work/wt/polished/lua/gen2/entry.lua",
  "line": 337,
  "expect": "if row.kind == \"clean\" then"
 },
 {
  "path": "F:/slink-work/wt/polished/lua/gen2/entry.lua",
  "line": 419,
  "expect": "local reads, why = Reads.new(profile, io_, decode_name)"
 },
 {
  "path": "F:/slink-work/wt/polished/lua/gen2/entry.lua",
  "line": 424,
  "expect": "proof = assert(proofs(root, json, data, title, def.pack, view))"
 },
 {
  "path": "F:/slink-work/wt/polished/lua/gen2/entry.lua",
  "line": 472,
  "expect": "local options = {title=title, profile=data.profile, pack=data.sites, io=io_,"
 },
 {
  "path": "F:/slink-work/wt/polished/lua/gen2/entry.lua",
  "line": 495,
  "expect": "local Boxes, wire = load(\"lua/gen2/boxes.lua\"), load(\"lua/gen2/wire.lua\")"
 },
 {
  "path": "F:/slink-work/wt/polished/lua/gen2/entry.lua",
  "line": 505,
  "expect": "local boxes = Boxes.executor({profile=profile, reads=reads, key=wire.mon_key, writes=writes,"
 },
 {
  "path": "F:/slink-work/wt/polished/lua/gen2/entry.lua",
  "line": 516,
  "expect": "panel = Panel.new(profile, data.charmap, io_, Panel.writes(io_, Permit),"
 },
 {
  "path": "F:/slink-work/wt/polished/lua/gen2/entry.lua",
  "line": 520,
  "expect": "local ph = profile.overlay and profile.overlay.phone"
 },
 {
  "path": "F:/slink-work/wt/polished/lua/gen2/entry.lua",
  "line": 530,
  "expect": "if profile.overlay and profile.overlay.trade then"
 },
 {
  "path": "F:/slink-work/wt/polished/lua/gen2/entry.lua",
  "line": 534,
  "expect": "client = load(\"lua/gen2/client.lua\").new({"
 },
 {
  "path": "F:/slink-work/wt/polished/lua/gen2/entry.lua",
  "line": 536,
  "expect": "reads=reads, wire=wire, writes=writes, rom=rom, boxes=boxes, panel=panel, phone=phone,"
 },
 {
  "path": "F:/slink-work/wt/polished/lua/gen2/entry.lua",
  "line": 540,
  "expect": "checkpoint_pc=production and view.checkpoint.primary.execution_before.pc or nil,"
 },
 {
  "path": "F:/slink-work/wt/polished/lua/gen2/entry.lua",
  "line": 543,
  "expect": "battle_hold=(not production or checkpoint:covers(\"battle_faint\"))"
 },
 {
  "path": "F:/slink-work/wt/polished/lua/gen2/entry.lua",
  "line": 550,
  "expect": "contest_mask=hold_facts.contest_mask,"
 },
 {
  "path": "F:/slink-work/wt/polished/lua/gen2/entry.lua",
  "line": 553,
  "expect": "rom_type=def.rom_type,"
 },
 {
  "path": "F:/slink-work/wt/polished/lua/gen2/entry.lua",
  "line": 567,
  "expect": "qualification=production and \"PHYSICAL_RECEIPTED\" or \"SOURCE_MODEL_CANDIDATE\""
 },
 {
  "path": "F:/slink-work/wt/polished/lua/gen2/entry.lua",
  "line": 579,
  "expect": "PKPCRYSTAL=\"polished\"}"
 },
 {
  "path": "F:/slink-work/wt/polished/lua/gen2/entry.lua",
  "line": 597,
  "expect": "if deps.title == \"polished\" then"
 },
 {
  "path": "F:/slink-work/wt/polished/lua/gen2/entry.lua",
  "line": 601,
  "expect": "Polished Crystal overlay admitted (dev,"
 },
 {
  "path": "F:/slink-work/wt/polished/lua/gen2/entry.lua",
  "line": 615,
  "expect": "function Entry.admit_polished(deps)"
 },
 {
  "path": "F:/slink-work/wt/polished/lua/gen2/polished.lua",
  "line": 16,
  "expect": "P.TITLE, P.ROM_TYPE = \"polished\", \"polished_crystal\""
 },
 {
  "path": "F:/slink-work/wt/polished/lua/gen2/polished.lua",
  "line": 21,
  "expect": "local MAX_SPECIES = 0x1FF"
 },
 {
  "path": "F:/slink-work/wt/polished/lua/gen2/polished.lua",
  "line": 65,
  "expect": "function P.load(root, json)"
 },
 {
  "path": "F:/slink-work/wt/polished/lua/gen2/polished.lua",
  "line": 99,
  "expect": "if candidate.kind == \"clean\" then"
 },
 {
  "path": "F:/slink-work/wt/polished/lua/gen2/polished.lua",
  "line": 112,
  "expect": "qualification=\"DEV_OVERLAY_SHA1\"}"
 },
 {
  "path": "F:/slink-work/wt/polished/lua/gen2/polished.lua",
  "line": 131,
  "expect": "string.format(\"%06X:%04X:%03X:%02X\", mon.dv_bytes, mon.ot_id, mon.species_id, traits)"
 },
 {
  "path": "F:/slink-work/wt/polished/lua/gen2/polished.lua",
  "line": 161,
  "expect": "function P.party_entry(mon, active_slot, stages)"
 },
 {
  "path": "F:/slink-work/wt/polished/lua/gen2/polished.lua",
  "line": 168,
  "expect": "if #blob ~= 140 or not blob:match(\"^%x+$\") then return nil, \"missing/invalid party blob hex\" end"
 },
 {
  "path": "F:/slink-work/wt/polished/lua/gen2/polished.lua",
  "line": 184,
  "expect": "function P.foe_entry(mon, stages)"
 },
 {
  "path": "F:/slink-work/wt/polished/lua/gen2/polished.lua",
  "line": 373,
  "expect": "function r.read_party()"
 },
 {
  "path": "F:/slink-work/wt/polished/lua/gen2/polished.lua",
  "line": 424,
  "expect": "function r.read_pocket(pocket)"
 },
 {
  "path": "F:/slink-work/wt/polished/lua/gen2/polished.lua",
  "line": 445,
  "expect": "function r.read_current_box_num()"
 },
 {
  "path": "F:/slink-work/wt/polished/lua/gen2/client.lua",
  "line": 48,
  "expect": "local AREA_BATTLE_TYPES = { [0] = true, [4] = true, [8] = true }"
 },
 {
  "path": "F:/slink-work/wt/polished/lua/gen2/client.lua",
  "line": 97,
  "expect": "foundation = \"gen2_gsc\", artifact_kind = p.artifact_kind or \"clean\","
 },
 {
  "path": "F:/slink-work/wt/polished/lua/gen2/client.lua",
  "line": 219,
  "expect": "local mon_key = wire.mon_key"
 },
 {
  "path": "F:/slink-work/wt/polished/lua/gen2/client.lua",
  "line": 235,
  "expect": "local e, why = wire.party_entry(m, active, stages)"
 },
 {
  "path": "F:/slink-work/wt/polished/lua/gen2/client.lua",
  "line": 317,
  "expect": "if box == cur then mons = reads.read_active_box() else mons = reads.read_storage_box(box) end"
 },
 {
  "path": "F:/slink-work/wt/polished/lua/gen2/client.lua",
  "line": 320,
  "expect": "local e = (not m.is_egg) and wire.box_entry(m, box) or nil"
 },
 {
  "path": "F:/slink-work/wt/polished/lua/gen2/client.lua",
  "line": 470,
  "expect": "local stable, old = target:sub(1, 9), tonumber(target:sub(11), 16)"
 },
 {
  "path": "F:/slink-work/wt/polished/lua/gen2/client.lua",
  "line": 502,
  "expect": "function self:request_sfx_local(gen3_id)"
 },
 {
  "path": "F:/slink-work/wt/polished/lua/gen2/client.lua",
  "line": 663,
  "expect": "function self:trade_live()"
 },
 {
  "path": "F:/slink-work/wt/polished/lua/gen2/client.lua",
  "line": 1001,
  "expect": "local function awaiting_save_field()"
 },
 {
  "path": "F:/slink-work/wt/polished/lua/gen2/client.lua",
  "line": 1156,
  "expect": "send(\"capture\", { key = key, area_id = ev.area_id, species_id = m.species_id, level = m.level,"
 },
 {
  "path": "F:/slink-work/wt/polished/lua/gen2/client.lua",
  "line": 1314,
  "expect": "local payload = {"
 },
 {
  "path": "F:/slink-work/wt/polished/lua/gen2/client.lua",
  "line": 1320,
  "expect": "area_id = area_id, loc_name = loc, pc_boxes = pc_boxes_wire(), pc_boxes_generation = box_generation(),"
 },
 {
  "path": "F:/slink-work/wt/polished/lua/gen2/client.lua",
  "line": 1325,
  "expect": "panel = panel and panel:present() or false, panel_abi = panel and panel:abi() or 0,"
 },
 {
  "path": "F:/slink-work/wt/polished/lua/gen2/client.lua",
  "line": 1331,
  "expect": "trade_prepare = self:trade_live(),"
 },
 {
  "path": "F:/slink-work/wt/polished/lua/gen2/client.lua",
  "line": 1407,
  "expect": "send(event or \"tick\", {"
 },
 {
  "path": "F:/slink-work/wt/polished/lua/gen2/client.lua",
  "line": 1417,
  "expect": "trade_blocked = contest_masked(),"
 },
 {
  "path": "F:/slink-work/wt/polished/lua/gen2/wire.lua",
  "line": 73,
  "expect": "function M.mon_key(mon)"
 },
 {
  "path": "F:/slink-work/wt/polished/lua/gen2/wire.lua",
  "line": 203,
  "expect": "function M.box_entry(mon, box_index)"
 },
 {
  "path": "F:/slink-work/wt/polished/lua/gen2/signals.lua",
  "line": 56,
  "expect": "integer(mon.species_id,1,251) and integer(mon.ot_id,0,65535)"
 },
 {
  "path": "F:/slink-work/wt/polished/lua/gen2/signals.lua",
  "line": 58,
  "expect": "return string.format(\"%04X:%04X:%02X\",mon.dv_word,mon.ot_id,mon.species_id)"
 },
 {
  "path": "F:/slink-work/wt/polished/lua/gen2/signals.lua",
  "line": 529,
  "expect": "assert(({crystal=true,gold=true,silver=true})[title], \"selected Gen 2 title required\")"
 },
 {
  "path": "F:/slink-work/wt/polished/lua/gen2/signals.lua",
  "line": 1045,
  "expect": "need(integer(old,0,13) and integer(requested,0,13),\"box-change context unavailable: \""
 },
 {
  "path": "F:/slink-work/wt/polished/server/adapters/gen2_polished.py",
  "line": 35,
  "expect": "_KEY = re.compile(r\"([0-9A-F]{6}):([0-9A-F]{4}):([0-9A-F]{3}):([0-9A-F]{2})\")"
 },
 {
  "path": "F:/slink-work/wt/polished/docs/polished/RAM.md",
  "line": 106,
  "expect": "### 1.4 `sram_bank` (365 keys) — the boxes are gone"
 },
 {
  "path": "F:/slink-work/wt/polished/docs/polished/RAM.md",
  "line": 392,
  "expect": "## 5. What breaks, ranked"
 },
 {
  "path": "F:/slink-work/wt/polished/docs/polished/NEWBOX.md",
  "line": 15,
  "expect": "### 1.1 Box metadata: 20 records, two copies"
 },
 {
  "path": "F:/slink-work/wt/polished/docs/polished/NEWBOX.md",
  "line": 42,
  "expect": "### 1.2 PokeDB: 2 banks × 207 entries, 6 sections"
 },
 {
  "path": "F:/slink-work/wt/polished/docs/polished/NEWBOX.md",
  "line": 221,
  "expect": "### 6.1 Enumerate boxed mons (read-only)"
 },
 {
  "path": "F:/slink-work/wt/polished/docs/polished/HOOKS.md",
  "line": 349,
  "expect": "the `$FF` scan is a **lower bound**; real data can be `$FF`"
 },
 {
  "path": "F:/slink-work/wt/polished/docs/gen2/gen2_engine_sites.md",
  "line": 6,
  "expect": "It does **not** complete F3, arm hooks, grant runtime"
 },
 {
  "path": "F:/slink-work/wt/polished/lua/gen2/client.lua",
  "line": 88,
  "expect": "local arr = json.array"
 },
 {
  "path": "F:/slink-work/wt/polished/lua/gen2/client.lua",
  "line": 664,
  "expect": "return trade ~= nil and self.artifact_kind == \"overlay\" and trade:advertised()"
 },
 {
  "path": "F:/slink-work/wt/polished/lua/gen2/client.lua",
  "line": 487,
  "expect": "local contest = p.contest_mask"
 },
 {
  "path": "F:/slink-work/wt/polished/lua/gen2/client.lua",
  "line": 488,
  "expect": "local function contest_masked()"
 },
 {
  "path": "F:/slink-work/wt/polished/lua/gen2/client.lua",
  "line": 489,
  "expect": "if not contest or io.bank_valid(contest.bank, contest.address, 1) ~= true then return false end"
 },
 {
  "path": "F:/slink-work/wt/polished/lua/gen2/client.lua",
  "line": 994,
  "expect": "local function burial_waiting()"
 },
 {
  "path": "F:/slink-work/wt/polished/lua/gen2/client.lua",
  "line": 1177,
  "expect": "resolves = battle ~= nil and AREA_BATTLE_TYPES[battle.battle_type] == true"
 },
 {
  "path": "F:/slink-work/wt/polished/lua/gen2/polished.lua",
  "line": 249,
  "expect": "mon.dv_bytes = hp_atk * 65536 + def_spe * 256 + sat_sdf"
 },
 {
  "path": "F:/slink-work/cache/polished/src/constants/battle_constants.asm",
  "line": 106,
  "expect": "const_def"
 },
 {
  "path": "F:/slink-work/cache/polished/src/constants/battle_constants.asm",
  "line": 107,
  "expect": "const BATTLETYPE_NORMAL"
 },
 {
  "path": "F:/slink-work/cache/polished/src/constants/battle_constants.asm",
  "line": 110,
  "expect": "const BATTLETYPE_FISH"
 },
 {
  "path": "F:/slink-work/cache/polished/src/constants/battle_constants.asm",
  "line": 111,
  "expect": "const BATTLETYPE_TREE"
 },
 {
  "path": "F:/slink-work/cache/polished/src/constants/battle_constants.asm",
  "line": 116,
  "expect": "const BATTLETYPE_GROTTO"
 },
 {
  "path": "E:/Google Drive/SLink/.cache/pret/pokecrystal/constants/battle_constants.asm",
  "line": 90,
  "expect": "const_def"
 },
 {
  "path": "E:/Google Drive/SLink/.cache/pret/pokecrystal/constants/battle_constants.asm",
  "line": 95,
  "expect": "const BATTLETYPE_FISH"
 },
 {
  "path": "E:/Google Drive/SLink/.cache/pret/pokecrystal/constants/battle_constants.asm",
  "line": 99,
  "expect": "const BATTLETYPE_TREE"
 }
]
```


---

## 5. Status after milestone A + C-BOX + C-AREA (2026-10-04)

Appended, not substituted: the rows above stay as written and are corrected here. Nothing in this
section rewrites an earlier line. Source of truth is the committed code at `cd89c370`
(`cd89c370` C-BOX, `c3d10da9` C-AREA, `d8d75628` milestone A).

**Correction note (2026-10-06).** This section began as a historical snapshot at `cd89c370` (C-BOX
`cd89c370`, C-AREA `c3d10da9`, milestone A `d8d75628`). The inline corrections below distinguish that
snapshot from the current implementation (`lua/gen2/entry.lua` at the integration tip) and DEV evidence
from PHYSICAL qualification. The "Commit" column names the commit that first landed a card; it does not
date later composed features (capture site, overworld hold, deposit, withdraw, explode, rival facade).

### 5.1 Card status

| Card | Status | Commit | Proved by | UNPROVEN without a cartridge |
|---|---|---|---|---|
| **C-PACK** | **DONE-tested** | `d8d75628` | `tests/unit/test_polished_client.py` (`test_the_hello_carries_the_party_and_the_companion_evidence`) | That the three pack files agree with the *live* overlay on a real save; proven only against the built overlay image and a synthetic WRAM image |
| **C-SIGNALS** | **DONE-tested** | `d8d75628` | `test_polished_client.py::test_the_production_signals_gate_refuses_polished`, plus the re-labelled binder cases `test_the_polished_title_binds_only_with_its_key_builder`, `test_the_box_change_bound_is_the_profile_num_boxes`, `test_the_area_battle_types_are_the_profile_set` | Snapshot text, `cd89c370` (superseded, kept for the record): "no site is registered, so nothing has ever been observed". **Correction 2026-10-06:** the production signals gate still refuses Polished (`test_the_production_signals_gate_refuses_polished`), while the separate DEV-qualified `Signals.new_polished` binder is composed (`lua/gen2/entry.lua` ~828-830). The capture site has since been observed on a DEV run (2026-10-04, `LIVE_RESULTS.md` Stage 2, see C-SITES). That evidence does not close PHYSICAL qualification |
| **C-COMPOSE** | **IMPLEMENTED and DEV-qualified**, not production-admitted (this row first read "DONE-tested (DEV-GRADE only)", `d8d75628`) | `d8d75628` (first landing) | `test_polished_client.py` (compose, hello capture, `test_the_real_server_admits_the_captured_hello`, `test_no_hook_and_no_write_on_the_whole_path`, `test_the_clean_release_and_a_random_rom_get_no_client`), `test_polished_write_path.py`, `test_polished_withdraw_path.py` | **Correction 2026-10-06** (snapshot text "Everything live ... hello admitted against a synthetic WRAM image" is superseded). The client now composes `force_faint`, deposit, withdraw, explode and a rival facade (`lua/gen2/entry.lua` ~836-868, 909-949). Rival writes remain gated by an explicit class set (`deps.rival_classes`) and a required CPU site. `production_admitted=false` and qualification `DEV_OVERLAY_SHA1` remain; no `PHYSICAL_RECEIPTED` exists. Hello admission was observed live on 2026-10-04 on the SYNTH save fixture and overlay `29ea04c24a46d9210c899355fe752f32d2880de8` (`LIVE_RESULTS.md` Stage 1): DEV evidence, not a current PHYSICAL receipt |
| **C-AREA** | **DONE-tested** | `c3d10da9` | `tests/unit/test_polished_area_map.py` | That Polished's map-group/number values and landmark constants match on a running cartridge |
| **C-BOX** | **DONE-tested** (read-only) | `cd89c370` | `tests/unit/test_polished_boxes_census.py` | That the CartRAM→WRAM flag mapping is right on a real save. **A wrong flag mapping reads every slot as empty — a complete but wrong census**, and no synthetic image can catch that |
| **C-SITES** | **MODEL-TESTED, HISTORICALLY LIVE-RUN; PHYSICAL qualification OPEN** (snapshot `cd89c370` said OPEN, nothing registered; corrected 2026-10-06, see 5.4) | — | `test_polished_sites.py`; `LIVE_RESULTS.md` Stage 2 | The `capture_party` site at 03:652B (flat 0xE52B) is composed. PHYSICAL qualification: the binder reports `physical_status` OPEN and `runtime_authorized` false, and `engine_signals.json` is not a receipted pack |
| **C-KEY** | **DONE-tested** | `d8d75628` | `test_polished_client.py` (9-bit species `291` planted, `pc.key` round trip against the server's `decode_party_blob`) | Nothing structural — but see 5.2(a): the *key format* was correct while the *foundation string* sent alongside it was not |
| **C-ROMTABLES** | **DONE-unproven-live** | `d8d75628` | `tests/unit/test_gen2_polished_adapter.py`, `test_gen2_rand_data.py` | The R4 scan decodes a provisioned ROM's tables, but no wildcard encounter, gift or static has been resolved from a **running** cart |
| **battle_faint` / `battle_bench` / rival swap** | **OPEN** | — | — | Need receipts that do not exist |
| **any box write** (deposit/withdraw/memorialize) | **OPEN** at the snapshot; **corrected 2026-10-06: deposit and withdraw COMPOSED (DEV), memorialize still refuses** | — | `test_polished_write_path.py`, `test_polished_withdraw_path.py` | Deposit (`box_mon`) and withdraw (`party_mon`) are composed through `Overworld.writes` / `Overworld.boxes` / `Overworld.client_boxes` (`lua/gen2/entry.lua` ~909-912). Memorialize refuses pending the owner memorial-box ruling (NEWBOX 6.2; helper `move_to_memorial` exists in `polished_boxes.lua`). No PHYSICAL box write receipt exists |
| **phone** | **OPEN** | — | — | `wUnusedMapBuffer` absent; needs a `$C633` staging span |
| **trade** | **OPEN** | — | — | `profile.overlay` has no `trade` block |
| **SFX / panel** | **OPEN** | — | — | Needs a later overlay milestone (caps != 0). `P.writes` is composed and live; the only brake is the ROM-advertised caps byte |

**Correction (2026-10-06): the `battle_faint` / `battle_bench` / rival swap row above is a historical OPEN snapshot.**
The current graph is **COMPOSED (DEV-qualified)** for `force_faint`, explode and a rival facade, as the
C-COMPOSE correction records (`lua/gen2/entry.lua:836-868,913-949`), with distinct limits:

- `force_faint` uses the overworld writer through the facade (`lua/gen2/polished_explode.lua:183`).
  An active-battler plain faint in battle is still **not composed** and refuses by name (`:184-186`).
- Explode is **Explosion-move/PP replacement only** (`lua/gen2/polished_writes.lua:166-185`), not proof
  that Explosion executes or a guarantee of death/plain-faint fallback; see `docs/polished/EXPLODE_SCOPE.md:6-16`.
- Rival writes require the owner-supplied `deps.rival_classes` set and the CPU at **0f:47DD**
  (`lua/gen2/entry.lua:913-918`; `lua/gen2/polished_overworld.lua:893-894`;
  `lua/gen2/polished_rival.lua:129-141`). The client exec hook for that rival site is **not built**:
  the composed battle hook enters the explode hold, while `rival_tick` runs at frame end
  (`lua/gen2/client.lua:1497-1506,1911`). Composing the facade does not make the rival write reachable.
- `battle_bench` remains **not composed** (`lua/gen2/entry.lua:866,919-940`);
  receipt-time bench handling returns without it (`lua/gen2/client.lua:1653-1654`).

These are composition facts, not PHYSICAL qualification: the battle writer reports
`DEV_OVERLAY_PREDICATE_HOLD` (`lua/gen2/polished_overworld.lua:889`), and the graph still returns
`production_admitted=false`, qualification `DEV_OVERLAY_SHA1` (`lua/gen2/entry.lua:948-949`).

### 5.2 Corrected claims

**(a) A11 — `client.foundation` was hard-coded `gen2_gsc`; the server refused the hello. SUPERSEDED.**
Row A11 says *"unchanged and correct — the server also says `foundation=\"gen2_gsc\"`"* and marks it
**CLEAN**. It was not. The server maps `polished_crystal` to `gen2_polished`
(`server/adapters/__init__.py:82`), so a client announcing `gen2_gsc` on a Polished cart was
mismatched. Fixed by three edits: `lua/gen2/polished.lua:18` `P.FOUNDATION = "gen2_polished"`, used at
`:113` in the admission description; `lua/gen2/client.lua:114`
`foundation = p.foundation or "gen2_gsc"` (default preserved, so vanilla is unchanged). A11's
*CLEAN* verdict is withdrawn; the row's premise (the client hard-coded it) was right.

**(b) C-BOX — "add `read_boxes`; `wire.box_entry` need not change" was wrong on both counts.
SUPERSEDED.**
* The card's exclusive-file line (`CLIENT.md:304`) says *"add `read_boxes`"*, and the change section
  implies the vanilla wire entry is reusable. It is not: `lua/gen2/wire.lua:209` refuses any
  `box_index` outside `0..13`, and Polished has **20** boxes. A Polished box 14–19 would have been
  dropped from `pc_boxes` silently. Polished needed its own `P.box_entry` (`lua/gen2/polished.lua:202`),
  which widens the bound to `integer(box_index, 0, 19)` at `:207` and the slot likewise.
* What was actually built is **`P.census`** (`lua/gen2/polished.lua:609`), not a method named
  `read_boxes`, and its `read_current_box_num` returns **`-1`** (`:656`), not a box number. That `-1`
  was the latent blocker: `client.lua`'s rescan walks boxes `0..19` and the vanilla path reads a
  *current box number*; composing a census that reports `-1` would have to be refused or special-cased
  at every consumer. **UNVERIFIED (snapshot):** I did not trace whether the composed client special-cases `-1`
  or simply never asks, because no box writer is composed.
  **Superseded 2026-10-06:** the current composition supplies `Overworld.client_boxes(Overworld.boxes(...))`
  and periodic census rescans (`rescan_every=1800`; `lua/gen2/entry.lua` ~909-912, 932-945). That does
  not establish how every `-1` consumer behaves.
* Consequence for the doc: the C-BOX premise *"a box is a slot array of 32-byte records"* is already
  marked falsified in the card; what was NOT anticipated is the **wire-shape** change.

**(c) The C-BOX exclusive-files line lists `lua/gen2/boxes.lua`, which is not needed. SUPERSEDED.**
`CLIENT.md:304` reads *"`lua/gen2/polished.lua` (add `read_boxes`), `lua/gen2/boxes.lua` (only if a
writer is added)"*. The census is read-only, so no `boxes.lua` change was made; the real exclusive set
was `lua/gen2/polished.lua` plus `lua/gen2/polished_boxes.lua` (**UNVERIFIED** — I did not diff the
`cd89c370` file list to confirm whether `polished_boxes.lua` was added by that commit or already
existed).

### 5.3 What a cartridge would still have to prove

1. **C-BOX flag mapping** — the only finding that could make a "tested" row *silently wrong*: a bad
   CartRAM/WRAM flag decode yields a **complete but empty** census, and no synthetic image can catch it.
2. **C-SITES** — snapshot text: "nothing has ever fired on a cart". **Corrected 2026-10-06:** the capture
   site fired on a DEV run (2026-10-04, `LIVE_RESULTS.md` Stage 2); PHYSICAL qualification stays OPEN.
3. **C-AREA** — 605 maps/146 areas are generated from static data; no live area transition was observed.
4. **The two hardening findings** from the `d8d75628` review remain open and are *not* closed by any of
   the three commits above: the composed `P.writes` panel writer (`lua/gen2/panel.lua:66`, whose
   `pointer_stable` and `lifetime.valid` are unconditionally `true`) and `hello_unheld`
   (`lua/gen2/client.lua:1364`) skipping the `battle.mode` gate at `:1366`.


### 5.4 C-SITES update (coordinator, after commit of Signals.new_polished)

C-SITES was **DONE-unproven-live** at that point: the composed client registers one hook, `capture_party` at 03:652B,
and a replayed wild party catch emits `capture` that the real server takes into `pending_captures`
(`tests/unit/test_polished_sites.py`, 10 tests with red controls). Unproven without a cartridge: that the
exec hook fires at 0xE52B as an instruction start with hROMBank 3, and the frame alignment of the party
write (the capture RAM-effect stays OPEN by design). Box catches, roamers, scripted/grotto and contest catches
emit nothing (fail closed). The milestone-A "no hook anywhere" assertions now allow exactly this one hook.

**Correction (2026-10-06): the status is MODEL-TESTED, HISTORICALLY LIVE-RUN, PHYSICAL qualification OPEN.**
The "unproven without a cartridge" sentence above is historical. `capture_party` at 03:652B (flat 0xE52B) is
composed, and `LIVE_RESULTS.md` Stage 2 records a DEV run on 2026-10-04 with overlay
`29ea04c24a46d9210c899355fe752f32d2880de8`: the bank 3 / PC 652B hit at frame 5334, with `wPartyCount` already 6
and the copied species already present, then the client `capture` and the server `pending_captures` entry.
The end-of-frame poll labelled 5335 is the same frame window (the poll runs after `frameadvance`); the same
numbers appear in `RC_TRACKER.md`. The binder still reports `physical_status` OPEN and `runtime_authorized`
false. Unsupported acquisition paths (box catches, roamers, scripted/grotto, contest) stay fail-closed. The
composed-client no-command test now permits three hooks (`capture_party`, the overworld checkpoint and the
battle hold) instead of exactly one.
