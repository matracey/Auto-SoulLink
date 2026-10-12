# UPR handler for Polished Crystal 3.2.3 — design

Target: a `PolishedCrystalRomHandler` sibling of `Gen2RomHandler` inside the SLink
UPR-ZX fork (stock 4.6.1 sources + `patch/upr/0001..0015`, built by
`tools/build_upr_fork.py`).

## 1. Decision

**Ship a new handler class. Do not ship a `gen2_offsets.ini` entry.**

A new class is needed because every Gen-2 assumption the INI can carry is false
for Polished:

| Assumption | Gen2RomHandler | Polished 3.2.3 |
|---|---|---|
| base-stat record stride | `0x20` (`Gen2Constants.java:45`) | **34** (`pokemon_data_constants.asm:35`) |
| base-stat field order | HP at +1, types at +7/8 | HP at +0, types at +6/7 (`base_stats/bulbasaur.asm:1,4`) |
| type byte ids | BUG=$07, FIRE=$14, STEEL=$09 | BUG=$06, FIRE=$09, STEEL=$08 (`type_constants.asm:13,17`) |
| species byte count | 1 byte, `pokes[rom[..]]` | 2 bytes `dp` everywhere (`macros/data.asm:89-91`) |
| species count | 251 (`Gen2Constants.java:43`) | 291 ids + 61 forms |
| trainer party | fixed 2-byte mon, `$FF` terminator (`Gen2RomHandler.java:1184`) | variable length prefix + flag word (`trainers/macros.asm:306,310`) |
| wild table | one linear blob (`Gen2RomHandler.java:877`) | 10 separate symbol-backed tables |
| move record | 7 bytes hardcoded (`Gen2RomHandler.java:421`) | 8 bytes, `MOVE_LENGTH` (`battle_constants.asm:51-52`) |
| names | fixed-width raw | Huffman-compressed |

An INI entry cannot express the type table, the 2-byte species, or the trainer
grammar. The INI stays the *offset source*; the class owns the *format*.

## 2. Handler selection and the generated INI

### 2.1 Registration

`RomHandler.Factory` is an abstract class with `create(Random, PrintStream)` and
`isLoadable(String)` (`romhandlers/RomHandler.java:41,48`). Handlers are tried in
array order from exactly two registries — the CLI (`cli/CliRandomizer.java:23,25`)
and the GUI (`newgui/NewRandomizerGUI.java:354`). A third reader,
`newgui/PresetLoadDialog.java:270`, iterates the GUI's array.

**Change required:** add `new PolishedCrystalRomHandler.Factory()` to both arrays.

### 2.2 `isLoadable` and the admission tuple

`Gen2RomHandler.Factory.isLoadable` (`Gen2RomHandler.java:52`) reads the first
`0x1000` bytes and calls `detectRomInner` (`:301`), which is a size gate plus
`checkRomEntry` (`:339`). `checkRomEntry` matches `romCode` at
`GBConstants.romCodeOffset = 0x13F`, `jpFlagOffset = 0x14A`, `versionOffset =
0x14C`, `crcOffset = 0x14E` (`constants/GBConstants.java:39-40`), preferring an
exact `CRCInHeader` match over a `crcInHeader == -1` wildcard.

Measured on `release/polishedcrystal-3.2.3.gbc`:

| Field | Value | Note |
|---|---|---|
| romCode `0x13F` | `PKPC` | collides with nothing in `gen2_offsets.ini` (all `AAU*`/`AAX*`/`BXT*`/`BYT*`/`KAPB`) |
| `0x143` | `$80` | GBC flag; satisfies SLink's Gen 2 admission gate |
| `0x14A` | `$01` | UPR's `NonJapanese` |
| `0x14C` | `$32` (50) | **not** 0 — a `[Gold (U)]`-style `Version=0` entry cannot match |
| `0x14E` | `$6CA3` | `CRCInHeader=0x6CA3` |
| size | `$200000` | equals `GBConstants.maxRomSize` (`:37`), passes |
| CRC32 | `F98367E4` | `CRC32=` key, same convention as `gen2_offsets.ini` |

**No collision.** `Gen1RomHandler.detectRomInner` uses the same shape but
`gen1_offsets.ini` `Game=` values are all `POKEMON *` titles, so `PKPCRYSTAL` at
`0x134` cannot match. `Gen2RomHandler` will not claim Polished today; the factory
array order is what disambiguates, and `checkHandlers` is scanned for the first
`isLoadable` hit.

### 2.3 The INI: `tools/gen_upr_polished_ini.py`

Reuse the pureRGB generator pattern (`tools/gen_upr_gen1_ini.py:1-16`,
`data/purergb/upr_pure_entries.ini:1-3`). The new tool reads
`release/polishedcrystal-3.2.3.sym`, converts `bank:addr` to flat
(`bank*0x4000 + addr-0x4000`), and writes `data/games/polished_crystal/upr_polished_entries.ini`
plus the in-fork `src/com/dabomstew/pkrandom/config/polished_offsets.ini`, guarded
by `// ---- BEGIN/END slink polished entries ----` markers so `--check` can prove
staleness.

The `loadROMInfo()` scanner (`Gen2RomHandler.java:117`) strips `//` comments
(`:140`) and splits `key=value`, so the generated file needs no parser change;
only `FileFunctions.openConfig("polished_offsets.ini")` is new.

Label keys resolving directly (verified present in the `.sym`): `BaseData`
11:4b18, `EvosAttacksPointers` 06:46d1, `EvolutionMoves` 06:4580, `MoveNames`
11:77da, `ItemNames` 0e:55e0, `PokemonNames` 36:56df, `TrainerGroups` 07:4249,
`FishGroups` 24:6213, `ContestMons` 25:58b4, `NPCTrades` 3f:53af, `TMHMMoves`
04:539f, `BadgeBaseLevels` 0c:46be, `TypeMatchups` 0d:420c,
`PokemonPicPointers` 48:4000, plus the eight wild tables `JohtoGrassWildMons`
0c:46e8 … `SwarmWaterWildMons` 0c:77c7 and their per-map
`._def_grass_wildmons_<MAP>` / `._def_water_wildmons_<MAP>` labels.

Derived constants the generator must emit as literals: `BaseStatsEntrySize=34`,
`SpeciesCount=291`, `FormCount=61`, `BaseDataRecordCount=334`, `MoveLength=8`,
`TrainerClassCount=147`, `TrainerCount=961`, `NumTMs=74`, `NumHMs=6`,
`NumTutors=31`, `TmhmBitBytes=14`, `NumFishGroups=17`,
`NPCTRADE_STRUCT_LENGTH=31`.

## 3. Per-capability mapping

`binary layout` is read from the Polished source; `rewrite` says whether the field
can be changed in place at identical size.

| # | UPR method (Gen2RomHandler.java) | Polished data (file:line) | Binary layout | Rewrite |
|---|---|---|---|---|
| 1 | `loadMoves()` `:412`; fields at `:421,426` | `data/moves/moves.asm:1-11`; `MOVE_LENGTH` `battle_constants.asm:51-52` | 8 bytes/move: anim, effect, power, type, acc, pp, chance, category | yes, per byte |
| 2 | move categories / type ids | `constants/type_constants.asm:7-23` | ids `00`..`11` + `UNKNOWN_T=$12` | n/a — needs a **new** `PolishedConstants.typeTable` + `typeToByte` |
| 3 | `loadBasicPokeStats()` `:743` | `data/pokemon/base_stats/*.asm`; `BASE_DATA_SIZE` `pokemon_data_constants.asm:35` | stride **34**: stats×6, types×2, catch, exp, items×2, gender\|hatch, abilities×3, growth, egggroups, evYields×2, tmhm×14 | yes — stats at +0..+5, types +6/+7, catch +8, exp +9, items +10/+11 |
| 4 | `readPokemonNames()` `:787` | `data/pokemon/names.asm:1-2`; `MON_NAME_LENGTH` `text_constants.asm:5` | source declares fixed 10-byte `rawchar`, **but the built ROM is not plain** — see §4.3 | **exclude** from cut 1 |
| 5 | `populateEvolutions()` `:1833`, pointer read `:1843` | `data/pokemon/evos_attacks.asm:4`, `evos_attacks_pointers.asm` | per-record `db method`, param, optional extra; `db $FF` ends the block | yes, if method set is preserved |
| 6 | `getMovesLearnt()` `:1325`, evolution skip `:1333` | same file; `learnset` macro emits `db level, move` | pairs until `$FF` | yes, same size |
| 7 | `getTMHMCompatibility()` `:1662` | `tmhm` macro `base_stats.asm:15-33` | 14 bit-bytes = 111 flags (74 TM + 6 HM + 31 tutor) | yes, bit-scoped |
| 8 | `getEncounters()` `:876`, headbutt `:933`, bug contest `:952` | `data/wild/johto_grass.asm:2-6`; `wildmon` macro `macros/asserts.asm:102-105` | grass: `map_id`, 3 rate bytes, then 3×(7 × `level, dp`) = 3+21×2; water: 3 rate + 3 × `dp` | yes — `dp` is fixed 2 bytes |
| 9 | fishing | `data/wild/fish.asm:2-3,7` | group = 2 chance bytes + 3 `dw` pointers (old/good/super); entry = chance, `dp`, level | pointers and `dp` fixed size |
| 10 | bug contest | `data/wild/bug_contest_mons.asm:2,9` | chance, `dp`, min, max; `ContestMonsEnd` 25:58f0 | yes |
| 11 | roam / swarm | `data/wild/roammon_maps.asm`, `swarm_grass.asm` | map-id keyed tables | yes (defer) |
| 12 | `getTrainers()` `:1153`, grammar `:1181,1184` | `data/trainers/macros.asm:270,306,310,323,324`; `party_pointers.asm:3-5` | per trainer: `db size`, `name@`, `db flags`, then per mon `db level` + `dp` + flag-selected item/DV/personality/EV/4 moves, then nicknames | **size-prefixed** — UPR's fixed-stride writer does not fit; a length-preserving writer is required |
| 13 | `setTrainers()` `:1227` | `TrainerGroups` = `dba` per class (`party_pointers.asm:5`), 147 classes, 961 trainers | class → 3-byte bank/addr pointer → group → variable records | yes, class-by-class |
| 14 | `getStarters()` `:798` / `setStarters` `:808` | `givepoke` macro `macros/scripts/events.asm:319-325`; `maps/ElmsLab.asm:215` | opcode byte, `dp` (2), level, item, ball [, move] — embedded in map scripts, 36 sites | yes for species (2 bytes fixed), but a script command, not a table |
| 15 | `getStaticPokemon()` `:1450` / `setStaticPokemon` `:1481` | `loadwildmon` (29 map sites, e.g. `maps/CinnabarVolcanoB2F.asm:90`), `data/wild/bug_contest_mons.asm` | `loadwildmon <species[, form]>, <level>` in script bytecode | yes for species+level; multi-site so needs a `.sym`-scanned site list, not one offset |
| 16 | `getIngameTrades()` `:2677` / `setIngameTrades` `:2716` | `data/events/npc_trades.asm:1-4`; `npc_trade_constants.asm` | fixed 31-byte struct: dialog, `dp` want, `dp` give, nickname×11, DVs×3, personality, ball, item, OT id, OT name×7 | yes — both `dp` fields are 2 bytes |
| 17 | `getTMMoves()` `:1616` / `setTMMoves` `:1636` | `constants/tmhm_constants.asm:100,116,156,158` | item-id → move-id, TMs at item ids `01..4a`, HMs `4b..50` | yes |
| 18 | `hasMoveTutors()` `:1701`, `getMoveTutorMoves` `:1706` | same table; `MoveTutor` 1b:41df | 31 MT slots share the 111-bit array | yes |
| 19 | `loadItemNames()` `:2441` | `data/items/names.asm` (`list_start`/`li`) | Huffman-compressed, variable length | **exclude** |
| 20 | field / hidden items | `bg_event … BGEVENT_ITEM` in `maps/*.asm` | per-map script bytes | defer |
| 21 | egg moves | `data/pokemon/egg_moves.asm:6-7` | `dp species, form`, moves, `$ff` | yes |

**Same-size rewriting holds everywhere except rows 4, 12 and 19.** Row 12 is the
only structural rewrite problem: `end_trainer` computes `_tr_size` per trainer
(`trainers/macros.asm:306`) from a flag word (`_tr_flags`, `:310`), so a trainer's
record length changes when the flags change. A first cut must therefore keep the
flag word per trainer fixed and only rewrite the species/level/item/move bytes
inside the existing span.

## 4. Species and forme model

### 4.1 Two-byte species everywhere

`dp` emits `db LOW(species), HIGH(species) << MON_EXTSPECIES_F | form`
(`macros/data.asm:89-91`; `MON_EXTSPECIES_F = 5`, `EXTSPECIES_MASK = %00100000`,
`FORM_MASK = %00011111`, `constants/pokemon_data_constants.asm:244-247`):

```
species9 = byte0 | ((byte1 & 0x20) << 3)      // 9-bit, 1..291
form    = byte1 & 0x1F                        // 0 = NO_FORM, 1 = PLAIN_FORM
gender  = byte1 & 0x80                        // set at wildmon/trade/givepoke sites
```

### 4.2 Counts and the UPR mapping

Species ids `1..291` (`NUM_SPECIES`, `constants/pokemon_constants.asm:317`);
`NUM_POKEMON = 289` (`:318`). Ids 1–251 are National Dex 1–251 (id 251 = `CELEBI`,
252 = `AZURILL` — verified by extracting the const table). Forms are a **separate**
constant space starting at `FIRST_COSMETIC_FORM_MON` (`constants/pokemon_constants.asm:348`);
the header comment states `BaseData` is indexed by *species + non-cosmetic variants*,
which is why `data/pokemon/base_stats/` holds **334** files for 291 species.

UPR model: `Pokemon.number` = species9 (1..291); `Pokemon.formeNumber` = form.
`pokemonList` must be built from `BaseDataRecordCount` = 334, with index 0 as the
dummy UPR expects (`Gen2RomHandler.java:367` allocates `pokemonCount + 1`).

### 4.3 What SLink must forbid

1. **Any non-zero form byte.** UPR's pools assume one species id; a `species 201 +
   form 5` result is not what SLink's Gen 2 client decodes (that profile has no forme
   byte). Ban forms from every pool.
2. **Unown.** `const UNOWN ; c9` = 201 (`constants/pokemon_constants.asm:226`); the 28
   letters are forms `UNOWN_A_FORM`… (`:350-351`). `Gen2RomHandler` already bans it
   from the bug contest (`Gen2RomHandler.java:963-964`).
3. **Cosmetic formes** (cap/surf/red/yellow/spark Pikachu, Unown letters, Shellos
   east-west, …) — sprite/icon only, no SLink key change.
4. **Species > 251.** SLink's Gen 2 adapter is 251 sequential NatDex with 17 types;
   Polished ids 252–291 fall outside its sprite/type tables. Extend the adapter
   first, or pin the randomized pool to 1..251.
5. **Formed statics.** `loadwildmon MOLTRES, GALARIAN_FORM, 65`
   (`maps/CherrygroveBay.asm:59`) must land as species-only.

### 4.4 Exclude from cut 1

- **All Huffman text.** `data/text/compressed_text.asm:27` builds
  `TextCompressionHuffmanTree`; `home/text.asm:844` (`DecompressStringToRAM`) walks
  it. A ROM probe at `MoveNames` (11:77da) and `ItemNames` (0e:55e0) returns
  top-bit-set bytes — compressed, not what UPR's `readVariableLengthString` expects.
  Same for `PokemonNames` (36:56df) despite the source saying `rawchar`
  (`data/pokemon/names.asm:1-2`): the built bytes are `9e e0 e0 e0 9e 53 53 …`,
  not `BULBASAUR`. **Cause not established — UNVERIFIED.** So no name randomization;
  species/item/move display names must be generated host-side from the `.asm` at
  generation time.
- **Trainer names, TM descriptions, shop text** — same reason.
- **Forms** (§4.3).

## 5. First cut, effort, risks

### 5.1 Scope of cut 1

In: base stats + types, evolutions + learnsets, TM/HM/MT compatibility and the
TM/HM move list, wild (grass/water × time-of-day, fishing, bug contest, roam),
trainers (length-preserving), starters, `loadwildmon` statics, `givepoke` gifts,
NPC trades, egg moves. Deferred: field items.

Out: all text, all formes, species > 251, trainer/class-name randomization,
landmark plumbing, `miscTweaks`, `randomizeIntroPokemon`.

### 5.2 Effort

| Work | Days |
|---|---|
| `tools/gen_upr_polished_ini.py` + `--check` + symbol scanner | 1.5 |
| `PolishedConstants` (type table, sizes, effect indices) | 1 |
| handler skeleton: factory, `detectRom`, `loadedRom`, base stats, moves | 2 |
| species/forme model, `pokemonList`, bans | 1.5 |
| wild: 10 tables, fishing, contest, roam | 2 |
| trainers: variable-length parse + length-preserving write | 3 |
| statics/starter/gift script scanner (`.sym` → site list) | 2 |
| trades, egg moves, TM/MT | 1 |
| lossless byte-identity harness + fork patch series | 2 |
| **total** | **~16** |

### 5.3 Risks

1. **Trainer length prefix** (highest). `end_trainer` writes `_tr_size`
   (`data/trainers/macros.asm:306`) derived from `_tr_flags` (`:310`). Change a flag
   and the record resizes, shifting every later trainer in the bank. Mitigation:
   rewrite strictly within `[offset, offset+size)` and assert the span is unchanged;
   a size mismatch is a hard refusal, never a resize.
2. **`FAITHFUL` variance.** `data/moves/moves.asm:16-24` branches on `DEF(FAITHFUL)`
   (CUT's type/power), and `data/trainers/macros.asm:16-41` has a `FAITHFUL` DVS
   block. Cut 1 must pin `FAITHFUL` or read both variants.
3. **`NUM_POKEMON` ≠ `NUM_SPECIES`.** The INI must carry `SpeciesCount=291`;
   defaulting to 289 silently drops two ids.
4. **Losslessness.** The pureRGB entries set the bar (`LosslessMode=1`,
   `data/purergb/upr_pure_entries.ini:3`). Ship load→save byte-identity with every
   setting off before any write path is trusted.
5. **`.sym`/ROM drift.** The INI is generated from a pinned release; an entry keyed
   on `Version=50` and `CRCInHeader=0x6CA3` must refuse anything else.

### 5.4 Verification the design must satisfy

- `--check` on the generated INI fails when the `.sym` moves.
- load→save with all settings off is byte-identical (`tools/upr_lossless_check.py`).
- Every consumed `.sym` label resolves; a missing label fails the build, not the run.
- Per-category spot checks: 291 species, 334 base-stat records, 147 trainer classes,
  961 trainers, 111 TM/HM/MT bits, 17 fish groups, 9 NPC trades, ~300 wild map labels.
## 6. Machine-checkable claims

Each entry is `(absolute path, 1-indexed line, exact substring on that line)`.
All 57 were verified against the files at the time of writing.

## Update (2026-10-04, coordinator): patches 0016-0018 as built

0016 = handler skeleton (lossless), 0017 = wild encounters, 0018 = trainers, starters, statics/gifts and NPC trades (all same-size in place; formed sites, Jeeves, INVER and unresolved script sites untouched; the SLink overlay ROM is accepted via a second ini section, CRCInHeader=0x725C). Corrections to this document found while building: the NPC trade record is 33 bytes (not 31); the trainer-name terminator is $53; INVER's TrainerGroups entry points at WRAM (`wInverGroup`), not ROM. Species 252-291 are in the replacement pool unless an owner ruling limits it to 251.

## Update (2026-10-04, coordinator): patch 0019 - the SLink overlay is accepted by structure

The `CRCInHeader=0x725C` overlay section described under patches 0016-0018 is superseded: an overlay rebuild changes its own header checksum, so 0019 accepts an overlay when the moved DelayFrame lead-in is at $0070, the `call $0070` rewrite is at $0DA8 and bank $7E is not all $FF, with the ini overlay section carrying `CRCInHeader=-1`. The release is still matched by its exact checksum. `server/upr_pipeline.py` `jar_supports_polished` mirrors that rule. Proven by the worker on a synthetic overlay with a different header checksum and stamped bytes; 74/74 regression outputs identical to the 0018 jar.

```json
CLAIMS: [{"path":"E:/Google Drive/SLink/.cache/slink-upr/src/com/dabomstew/pkrandom/romhandlers/Gen2RomHandler.java","line":117,"expect":"gen2_offsets.ini"},{"path":"E:/Google Drive/SLink/.cache/slink-upr/src/com/dabomstew/pkrandom/romhandlers/Gen2RomHandler.java","line":301,"expect":"detectRomInner"},{"path":"E:/Google Drive/SLink/.cache/slink-upr/src/com/dabomstew/pkrandom/romhandlers/Gen2RomHandler.java","line":339,"expect":"checkRomEntry"},{"path":"E:/Google Drive/SLink/.cache/slink-upr/src/com/dabomstew/pkrandom/romhandlers/Gen2RomHandler.java","line":367,"expect":"pokemonCount + 1"},{"path":"E:/Google Drive/SLink/.cache/slink-upr/src/com/dabomstew/pkrandom/romhandlers/Gen2RomHandler.java","line":421,"expect":"effectIndex"},{"path":"E:/Google Drive/SLink/.cache/slink-upr/src/com/dabomstew/pkrandom/romhandlers/Gen2RomHandler.java","line":743,"expect":"loadBasicPokeStats"},{"path":"E:/Google Drive/SLink/.cache/slink-upr/src/com/dabomstew/pkrandom/romhandlers/Gen2RomHandler.java","line":787,"expect":"readPokemonNames"},{"path":"E:/Google Drive/SLink/.cache/slink-upr/src/com/dabomstew/pkrandom/romhandlers/Gen2RomHandler.java","line":798,"expect":"getStarters"},{"path":"E:/Google Drive/SLink/.cache/slink-upr/src/com/dabomstew/pkrandom/romhandlers/Gen2RomHandler.java","line":876,"expect":"getEncounters"},{"path":"E:/Google Drive/SLink/.cache/slink-upr/src/com/dabomstew/pkrandom/romhandlers/Gen2RomHandler.java","line":964,"expect":"bannedPokemon.add(pokes[Species.unown])"},{"path":"E:/Google Drive/SLink/.cache/slink-upr/src/com/dabomstew/pkrandom/romhandlers/Gen2RomHandler.java","line":1662,"expect":"getTMHMCompatibility"},{"path":"E:/Google Drive/SLink/.cache/slink-upr/src/com/dabomstew/pkrandom/romhandlers/Gen2RomHandler.java","line":1701,"expect":"hasMoveTutors"},{"path":"E:/Google Drive/SLink/.cache/slink-upr/src/com/dabomstew/pkrandom/romhandlers/Gen2RomHandler.java","line":1833,"expect":"populateEvolutions"},{"path":"E:/Google Drive/SLink/.cache/slink-upr/src/com/dabomstew/pkrandom/romhandlers/Gen2RomHandler.java","line":2441,"expect":"loadItemNames"},{"path":"E:/Google Drive/SLink/.cache/slink-upr/src/com/dabomstew/pkrandom/romhandlers/RomHandler.java","line":41,"expect":"abstract class Factory"},{"path":"E:/Google Drive/SLink/.cache/slink-upr/src/com/dabomstew/pkrandom/constants/Gen2Constants.java","line":43,"expect":"pokemonCount = 251"},{"path":"E:/Google Drive/SLink/.cache/slink-upr/src/com/dabomstew/pkrandom/constants/Gen2Constants.java","line":45,"expect":"baseStatsEntrySize = 0x20"},{"path":"E:/Google Drive/SLink/.cache/slink-upr/src/com/dabomstew/pkrandom/constants/GBConstants.java","line":40,"expect":"romCodeOffset = 0x13F"},{"path":"E:/Google Drive/SLink/.cache/slink-upr/src/com/dabomstew/pkrandom/config/gen2_offsets.ini","line":2,"expect":"Game=AAUE"},{"path":"E:/Google Drive/SLink/.cache/slink-upr/src/com/dabomstew/pkrandom/newgui/NewRandomizerGUI.java","line":354,"expect":"Gen2RomHandler.Factory()"},{"path":"E:/Google Drive/SLink/.cache/slink-upr/src/com/dabomstew/pkrandom/cli/CliRandomizer.java","line":25,"expect":"new Gen2RomHandler.Factory()"},{"path":"F:/slink-work/cache/polished/src/constants/pokemon_constants.asm","line":317,"expect":"DEF NUM_SPECIES EQU const_value - 1 ; 123"},{"path":"F:/slink-work/cache/polished/src/constants/pokemon_constants.asm","line":348,"expect":"DEF FIRST_COSMETIC_FORM_MON EQU const_value ; 124"},{"path":"F:/slink-work/cache/polished/src/constants/pokemon_data_constants.asm","line":35,"expect":"DEF BASE_DATA_SIZE EQU _RS"},{"path":"F:/slink-work/cache/polished/src/constants/pokemon_data_constants.asm","line":34,"expect":"DEF BASE_TMHM        rb (NUM_TM_HM_TUTOR + 7) / 8"},{"path":"F:/slink-work/cache/polished/src/constants/pokemon_data_constants.asm","line":244,"expect":"DEF EXTSPECIES_MASK  EQU %00100000"},{"path":"F:/slink-work/cache/polished/src/macros/data.asm","line":89,"expect":"MACRO? dp ; db species, extspecies | form"},{"path":"F:/slink-work/cache/polished/src/macros/data.asm","line":91,"expect":"db LOW(\\1), HIGH(\\1) << MON_EXTSPECIES_F | \\2"},{"path":"F:/slink-work/cache/polished/src/data/pokemon/base_stats.asm","line":36,"expect":"BaseData::"},{"path":"F:/slink-work/cache/polished/src/data/pokemon/base_stats/bulbasaur.asm","line":1,"expect":"db  45,  49,  49,  45,  65,  65"},{"path":"F:/slink-work/cache/polished/src/data/pokemon/base_stats/bulbasaur.asm","line":4,"expect":"db GRASS, POISON ; type"},{"path":"F:/slink-work/cache/polished/src/data/pokemon/base_stats/bulbasaur.asm","line":17,"expect":"tmhm CURSE, TOXIC"},{"path":"F:/slink-work/cache/polished/src/data/wild/johto_grass.asm","line":3,"expect":"def_grass_wildmons SPROUT_TOWER_2F"},{"path":"F:/slink-work/cache/polished/src/macros/asserts.asm","line":102,"expect":"MACRO? wildmon"},{"path":"F:/slink-work/cache/polished/src/data/wild/fish.asm","line":7,"expect":"FishGroups:"},{"path":"F:/slink-work/cache/polished/src/data/wild/bug_contest_mons.asm","line":2,"expect":"db \\1"},{"path":"F:/slink-work/cache/polished/src/data/trainers/macros.asm","line":270,"expect":"MACRO end_trainer"},{"path":"F:/slink-work/cache/polished/src/data/trainers/macros.asm","line":306,"expect":"db _tr_size"},{"path":"F:/slink-work/cache/polished/src/data/trainers/macros.asm","line":310,"expect":"db _tr_flags"},{"path":"F:/slink-work/cache/polished/src/data/trainers/macros.asm","line":324,"expect":"dp _tr_pk{d:p}_species, _tr_pk{d:p}_form"},{"path":"F:/slink-work/cache/polished/src/data/trainers/party_pointers.asm","line":3,"expect":"TrainerGroups:"},{"path":"F:/slink-work/cache/polished/src/data/trainers/party_pointers.asm","line":5,"expect":"table_width 3"},{"path":"F:/slink-work/cache/polished/src/data/trainers/parties.asm","line":21,"expect":"CarrieGroup:"},{"path":"F:/slink-work/cache/polished/src/data/events/npc_trades.asm","line":1,"expect":"NPCTrades:"},{"path":"F:/slink-work/cache/polished/src/constants/tmhm_constants.asm","line":158,"expect":"DEF NUM_TM_HM_TUTOR EQU NUM_TMS + NUM_HMS + NUM_TUTORS"},{"path":"F:/slink-work/cache/polished/src/constants/type_constants.asm","line":17,"expect":"const FIRE      ; 09"},{"path":"F:/slink-work/cache/polished/src/constants/battle_constants.asm","line":52,"expect":"DEF MOVE_LENGTH EQU _RS"},{"path":"F:/slink-work/cache/polished/src/data/moves/moves.asm","line":30,"expect":"if DEF(FAITHFUL)"},{"path":"F:/slink-work/cache/polished/src/macros/scripts/events.asm","line":319,"expect":"MACRO givepoke"},{"path":"F:/slink-work/cache/polished/src/macros/scripts/events.asm","line":322,"expect":"dp \\1, \\2 ; pokemon"},{"path":"F:/slink-work/cache/polished/src/maps/ElmsLab.asm","line":215,"expect":"givepoke CYNDAQUIL, PLAIN_FORM, 5, ORAN_BERRY"},{"path":"F:/slink-work/cache/polished/src/maps/CherrygroveBay.asm","line":59,"expect":"loadwildmon MOLTRES, GALARIAN_FORM, 65"},{"path":"F:/slink-work/cache/polished/src/data/pokemon/names.asm","line":2,"expect":"table_width MON_NAME_LENGTH - 1"},{"path":"F:/slink-work/cache/polished/src/data/text/compressed_text.asm","line":27,"expect":"TextCompressionHuffmanTree:"},{"path":"F:/slink-work/cache/polished/src/home/text.asm","line":844,"expect":"DecompressStringToRAM::"},{"path":"F:/slink-work/wt/polished/data/purergb/upr_pure_entries.ini","line":3,"expect":"LosslessMode=1"},{"path":"F:/slink-work/wt/polished/tools/gen_upr_gen1_ini.py","line":33,"expect":"config/gen1_offsets.ini"}]
```


---

## Coordinator note: OMP review cx-e8921a0c of patches 0016-0019 (2026-10-04)

* **F1 (map width) - already corrected.** The patches' `2 + 3 + 3*7*3` row (68 B, 2-byte map id) is right;
  the ROMTABLES errata and the real reader (0 mismatches over 4025 slots) settled it earlier.
* **F2/F3 accepted:** strides are linker-enforced (`GRASS_WILDDATA_LENGTH`, `WATER_WILDDATA_LENGTH`), and the
  9-bit species path REFUSES a write when the form byte carries more than the high bit.
* **F4 (loose `isSlinkOverlay` scan in 0019) - real, low severity, not a write hazard.** The check does
  return true on the first non-`$FF` byte in bank `$7E`. But the handler's writers only write the table
  offsets from the ini, never into bank `$7E`, ROM0 `$0070-$0088`, `$0DA8-$0DAE` or the header, and the Manager
  path runs the pipeline write-domain audit (`server/upr_polished_write_domain.py`), which refuses any write
  into a UPS hunk, SLink section or the header. Tightening the signature changes the jar sha256 (a shared pin),
  so it rides the next planned jar cut rather than its own.
* **F5 (no header/global checksum recompute) - REJECTED as a boot hazard.** Neither stock UPR `Gen2RomHandler`
  (`savingRom()` = `savePokemonStats(); saveMoves();`) nor this fork rewrites `$14D/$14E-$14F`. Game Boy
  hardware verifies only the `$14D` header check over `$0134-$014C`, which table writes never touch; the global
  `$14E-$14F` sum is not verified. SLink identifies randomized ROMs by the contract sha1, not the header CRC.
* **F6 (lossless-when-off enforced by design, not by a test):** open; `tools/upr_lossless_check.py` is the
  existing check and should be run for Polished when the jar is cut.

## Update (2026-10-04, forms worker): patches 0020-0021 - overlay signature, variant forms in the pool

* **0020** tightens `isSlinkOverlay` (review cx-e8921a0c F4): besides the 7-byte signatures at $0070 and $0DA8, bank
  $7E must start with the SLink service's own first 16 bytes `21 0b c6 3e 53 22 3e 4c 22 3e 4e 22 3e 4b 22 3e`
  (`ld hl, wSlinkMailbox` + the S/L/N/K beacon stores of `SlinkService`); the ABI byte after them is not pinned.
  Measured: the 0019 jar accepts a release with the bridge bytes, a stray byte in bank $7E and a foreign header
  checksum; the 0020 jar refuses it, and still accepts the release, the overlay and an overlay with another header
  checksum. `upr_pipeline._is_slink_polished_overlay` mirrors the rule.
* **Pipeline bug found while verifying:** `jar_supports_polished` looked for a bare `CRCInHeader=-1` line, but the
  real 0019 ini line carries a trailing `//` comment, so the pipeline refused every overlay on the real 0019 jar (its
  tests used a synthetic ini). Fixed (`-1(?![0-9])`), with a test on the real line shape.
* **0021: forms are always in the pool, with no setting** (owner ruling 2026-10-04). `getPokemon()` returns every
  BaseData record, the 46 variant forms (292..337) included, so wild, trainer, starter, static and trade pools draw
  them with their own stats and types; UPR's alt-forme options stay off and `upr_settings` is unchanged. A site
  reads as the record its (species, form) resolves to (variant, else the species: plain and cosmetic forms alike),
  so formed wild slots, trainer mons, the five resolved formed script sites (StaticSite44..48) and Jeeves's trade
  are now in the model. One writer: a site still holding the same record keeps every byte (a cosmetic form stays);
  otherwise LOW(species), then gender/egg bits | HIGH << 5 | the variant's form, or for a species the site's own
  NO_FORM/PLAIN_FORM, else the context's plain spelling (NO_FORM wild/trainer/trade, PLAIN_FORM scripts). The
  handler never writes a party/box record, so the FORMS.md H5 gender/egg hazard does not arise here. EGG, $100 and
  Unown stay banned. Known ceiling: record 292 (Gyarados-Red) shares UPR's `Species.shedinja` number, so its
  power-level BST drops HP (534 instead of 540).
* SLink side: `polished_rom_scan.placed()` lists every randomizable dp with its effective species (now including
  trainer mons) and `_check_content_polished` refuses an output whose changed sites hold anything but a plain species
  or a variant form; `gen2_polished._adopt_slots` adopts a moved form that exists for the species and refuses any
  other pair, and presented slots carry `effective_species_id`.
* Verified: 48 runs (24 overlay seeds through `prepare_pair`, 24 release seeds through `randomize` + the same
  checks), all checks pass, all 46 variant records placed, all-off byte-identical on both ROMs. The jar is UNPINNED.

## Update (2026-10-04, jar cut worker): the jar is cut and PINNED once (patches 0001-0021)

* **Jar:** `PokeRandoZX.jar` sha256 `f3a10dd744ff0ecb5834a0559c4e165c5eab9802bf8eda6521ce66c6c71f08ab`, built by
  `tools/build_upr_fork.py --bootstrap` (v4.6.1 + `patch/upr/0001..0021`, JDK 17 `--release 8`) and pinned in
  `data/upr_jars.json` as "SLink fork, built 2026-10-04 (patches 0001-0021; PokeRandoZX.jar)" (one added entry,
  additive: every older jar stays pinned). Kept at `F:/slink-work/cache/polished/jar/PokeRandoZX.jar`.
* **Reproducibility:** the sha256 is NOT byte-reproducible (the build tool's own docstring: `jar` stamps file times), but
  the content is: all 392 entries of the official build have the same names and CRC-32s as the scratch
  `PokeRandoZX-forms-final.jar` (sha256 `0e2fe51d...`), and of a rebuild from the series with the stray jar removed
  (below). Pin the jar that was tested, never a rebuild.
* **Delta against the previous pin (0015, `db4bc65c...`):** added `polished_offsets.ini` and the seven
  `PolishedCrystalRomHandler`/`PolishedConstants` classes; changed only `CliRandomizer.class` and
  `NewRandomizerGUI*.class` (the handler registered between Gen 2 and Gen 3); every other class and EVERY non-class
  resource (`gen1_offsets.ini`, `gen2_offsets.ini`, `gen3_offsets.ini`, the `.ips` files) is byte-identical.
* **Unchanged families, measured:** the same seeded driver (CLI handler chain, fixed seeds) over the old and the new jar,
  13 ROMs x {all-off, random-all x 2 seeds} = 39 runs: Red, Blue, Yellow, pureRed/Blue/Green, pureRed overlay,
  FireRed, LeafGreen, Emerald, Crystal, Gold, Silver. 39/39 output ROMs byte-identical (sha1) and the 36 logs equal
  but for the `Time elapsed` line; the all-off pureRGB runs are `out == in`. `isLoadable` also leaves every one of
  those to its old handler (the Polished factory claims only the release and the overlay). Through
  `upr_pipeline.prepare_pair` with the new jar: pureRGB overlay, FireRed+LeafGreen, Emerald and Polished overlay all
  pass their content checks and write-domain audits.
* **Gen 3 write-domain models are pinned to ONE jar sha** (`jar_sha256` in `data/games/gen3_{frlg,emerald}/upr_write_domains.json`,
  checked by `upr_gen3_write_domain.check_output`): installing this jar as `.cache/slink-upr/PokeRandoZX.jar` needs both
  models regenerated, as at every earlier cut. Regenerated from the new jar, each model equals the committed one with only
  `jar_sha256` swapped (0015 reproduces the committed models byte-for-byte), so the re-pin is that one line per file.
* **Defect found in the committed series:** `0021-...patch` also adds a stray 1.1 MB binary `PokeRandoZX-forms.jar` at the
  fork root (outside `src/`, so it is not in the jar). A cleaned 0021 (3 files, 30 KB) builds a CRC-identical jar.
* **Held:** `POLISHED_RANDOMIZER_ENABLED` stays False; the flip is prepared as patch files, not applied.
