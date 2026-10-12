# Polished Crystal 3.2.3 — SoulLink title mark and main-menu version

Owner rules (`docs/memory`, "Title screen design rules"): plain vanilla look,
`SoulLink` in each logo style, animate where the title animates, real builds not
mocks. Every patched title shows a SoulLink wordmark, and the patch version appears
on the New Game/Continue menu.

## Status (2026-10-08, card g2p-title-rebuild): wordmark BUILT, live check owed

The wordmark is in the overlay (`patch/polished/src/title.asm`; overlay sha1
`688945795e2656019247f5aaceb7b1d8791e900a`, UPS 4166 B, `data/polished/overlay_provenance.json`). ROM-bytes
evidence: `tests/unit/test_polished_title.py`. **Not yet seen on screen** -- the live title check (T7/T9 below) is
the coordinator's, after merge.

| fact | value | source |
|---|---|---|
| hook | `call EnableLCD` at `35:40eb`, flat `0xD40EB`, `cd da 24` -> `cd a1 3f` (`call SlinkTitleBridge`) | `engine/movie/title.asm:179`; the only `call EnableLCD` in `_TitleScreen` (`35:4000`..`SuicuneFrameIterator` `35:4117`) |
| bridge | ROM0 `$3FA1`-`$3FA7`: `farcall SlinkTitleBand` / `jp EnableLCD` (`d7 12 58 7e c3 da 24`) | after the main-menu bridge (`$3F92`-`$3FA0`); clean bytes `$FF` |
| band | section fixed at `7e:5800` (after the trade commit, so no floating bank-$7E section moves): rows `7e:5800`/`7e:5809`, `SlinkTitleBand` `7e:5812`, art `7e:584a`-`7e:5969` | `data/polished/polished_slink.sym` |
| art | the Crystal art unchanged: `patch/gen2/src/title_logo_crystal.2bpp` + `title_rows_crystal.inc` (18 tiles, ids `$60`-`$71`), copied by the builder (`TITLE_ART`) | `tools/gen_gen1_title.py` |
| placement | map rows 10-11, tiles 6-14 (screen rows 9-10: `hSCY` = 8, `title.asm:187-188`), via `wTilemap` (UpdateBGMap mode 1 copies tiles) | `title.asm:5` and `:103-106` clear them; nothing else writes them |
| palette | 6 (`title.asm:71-74`, the logo's lines 8-9): black / white / gold 26,21,0 / blue 2,3,30 -- the order `CRYSTAL_ROLES` paints; attribute bank bit clear | `gfx/title/title.pal` |
| entrance | `TitleScreenEntrance` (`01:6698`, `engine/menus/intro_menu.asm`) shear widened 80 -> 88 lines: `ld bc, 8 * 11` (operand flat `0x66A6`) and `ld b, 8 * 11 / 2` (operand flat `0x66AD`) | both band rows slide in with the logo, as on the vanilla Crystal overlay |

**Two corrections to the sections below, both settled from the ROM/source:**

1. **vTiles2 is NOT untouched (§2.4, §7.3, §8.5).** `TitleLogoGFX` (`logo_version.2bpp.lz`) decompresses to **154 tiles**
   from `vTiles1` (`title.asm:93-95`), so it runs on into `vTiles2` ids `$00`-`$19` (logo `$80`-`$0B`, copyright
   `$0C`-`$18` drawn at `:115-118`, FAITHFUL `$19` at `:121-124`). `TitleSuicuneGFX` (256 tiles, `:22-28`) is VRAM
   bank 1 only; `TitleCrystalGFX` (60 tiles) is OBJ tiles in `vTiles0`. Bank-0 ids `$1A`-`$7E` are free (`$7F` is the
   `' '` clear tile), so `$60`-`$71` is still collision-free -- now by measurement, and `title.asm` ASSERTs the run
   stays inside `$1A`-`$7E`. The test decompresses all three blocks out of the clean ROM with a model of
   `home/decompress.asm` (checked against the clean build's own `logo_version.2bpp`).
2. **The logo entrance DOES refill `wLYOverrides` (§7.5 is wrong).** `_TitleScreen` clears the buffer, but
   `TitleScreenEntrance` in `engine/menus/intro_menu.asm` (run every frame from `RunTitleScreen`) fills
   `8 * 10` lines with the interlaced SCX shear -- map rows 1-10, which includes the band's top row. Hence the
   88-line widening, exactly as the vanilla Crystal overlay does (`tools/build_gen2_companion.py` `TITLE_SHEAR_EDITS`).

**ROM facts carry bank:addr + flat offset + bytes** from
`F:/slink-work/cache/polished/release/polishedcrystal-3.2.3.gbc`. WRAM is not in the
ROM and is never dumped here.

Vanilla implementation mirrored: `tools/build_gen2_companion.py`,
`patch/gen2/src/title.asm`, `patch/gen2/src/version.asm`,
`tools/gen_gen1_title.py`, `data/gen2/overlay_provenance.json`.

## 1. The wordmark geometry is already generated — reuse it unchanged

`tools/gen_gen1_title.py` draws the wordmark art and emits the Gen 2 payloads:

| fact | value | source |
|---|---|---|
| Crystal wordmark | **9 × 2 tiles** (72 × 16 px) | `tools/gen_gen1_title.py:32` `COLS, ROWS = 9, 2` |
| Gold/Silver wordmark | **8 × 2 tiles** (64 × 16 px) | `gen_gen1_title.py:126` `pack(draw(8), 8, ROWS, GS_ROLES)` — narrower because G/S free tile ids are fewer |
| draw buffer | `Image.new("L", (cols * 8, ROWS * 8), WHITE)` | `gen_gen1_title.py:73` |
| outputs | `title_logo_crystal.2bpp` + `title_rows_crystal.inc`, `title_logo_gs.2bpp` + `title_rows_gs.inc` | `gen_gen1_title.py:117-132` |

The `.inc` files carry `DEF SLINK_TITLE_LOGO_TILES EQU {n}` and
`DEF SLINK_TITLE_FIRST_TILE ${first:02X}` plus per-row tile-id lists — so the tile
count is **not fixed at 18**; it is the deduplicated 8×8 cell count, and the dedup
depends on which cells the wordmark actually uses. **The overlay must allocate a tile
range sized by `SLINK_TITLE_LOGO_TILES`, not a guessed 18.** Polished has far more free
tile ids than either vanilla title (§2), so the G/S 8-wide narrowing is not needed
here; use the 9-wide Crystal art.

Palettes are already per-title (`CRYSTAL_ROLES` / `GS_ROLES` at `gen_gen1_title.py:117`
and `:126`), so "SoulLink in each logo style" is satisfied by the existing generator —
**no new art work is required for the wordmark itself.**

## 2. Polished's title screen

`engine/movie/title.asm` (369 lines). `_TitleScreen:` at `:1`.

### 2.1 What it loads, in order

| step | source | where it goes |
|---|---|---|
| `wTitleScreenTimer` cleared | `:15-16` | WRAM |
| Suicune GFX loaded | `:26-27` `ld hl, TitleSuicuneGFX` / `ld de, vTiles1` | **vTiles1** |
| BG map cleared | `:31`, `:41` | `wTilemap` / `vBGMap1` |
| text rows | `:51` row 3, `:56` row 5, `:61` row 6, `:66` row 7, `:71` row 8, `:77` row 5/row 9, `:83` row 12 | BG map |
| logo GFX loaded | `:93-94` `ld hl, TitleLogoGFX` / `ld de, vTiles1` | **vTiles1** |
| crystal GFX loaded | `:98-99` `ld hl, TitleCrystalGFX` / `ld de, vTiles0` | **vTiles0** |
| palettes | `:142`, `:147` `ld hl, TitleScreenPalettes` | BGP/OBP |

**Rows the title writes: 0, 3, 5, 6, 7, 8, 9, 12.** Rows 1, 2, 4, 10, 11 are not
written by any `hlbgcoord` in this file.

### 2.2 The two facts that change the vanilla hook

1. **The animation is a STAT-interrupt LY trick.** `:170` `set B_IE_STAT, [hl]` arms
   the STAT interrupt; `:159-165` fills `wLYOverrides` and loads its bank. The title
   redraws per scanline, so a band written once before the loop can be sheared or
   overwritten by the LY overrides.
2. **`SuicuneFrameIterator` (`:208`) and `AnimateTitleCrystal` (`:335`) run every
   frame.** Unlike pokecrystal, there is no single "setup ends here" instruction.

The only surviving vanilla anchor is `call EnableLCD` at `:179`. Proven in the ROM: `_TitleScreen` is `35:4000` (flat `0xD4000`, `.sym:32325`) and the call sits at **`35:40eb`, flat `0xD40EB`, bytes `cd da 24`** — the `3`-byte `call $24da` whose target is `EnableLCD` (`00:24da`, `.sym:725`, bytes `f0 40 cb`). That is the three-byte rewrite site.

Other anchors, from the `.sym`: `MainMenuJoypadLoop` `12:43ca` flat `0x483CA` (`.sym:12041`), `SetUpMenu` `00:19a0` flat `0x19A0` (`.sym:540`), `MainMenuItems` `12:439f` flat `0x4839F` (`.sym:12038`), `PlaceMenuStrings` `00:1a96` flat `0x1A96` (`.sym:560`). Graphics live in bank `$23` (`35`): `TitleSuicuneGFX` flat `0xD41AF`, `TitleLogoGFX` `0xD44CA`, `TitleCrystalGFX` `0xD4973`, `TitleScreenPalettes` `0xD4B50`.

The LY buffer is `wLYOverrides` `05:de00` flat `0x1DE00` to `wLYOverridesEnd` `05:de90` flat `0x1DE90` — **144 bytes** of per-scanline state (`.sym:69819-69820`).

### 2.3 Where the wordmark goes

**Recommendation: rows 10-11, tiles 6-14** — the same row pair pokecrystal uses, and
the same reasoning applies: `hlbgcoord 0, 12` (`:83`) is the last row the title writes,
so 10 and 11 are free of text, and `:83` writing row 12 happens *before* the loop starts
so there is no shear into it.

Two differences from pokecrystal that must be handled:

- **No entrance to join.** pokecrystal scrolls the BG up 8 pixels so the logo's entrance
  slides the wordmark in with it. Polished's animation is LY-driven per scanline; a band
  at rows 10-11 will be **static** while the crystal logo animates above it. That is
  acceptable under the owner rule ("animate where the title animates") — the *logo*
  animates; the wordmark is a static band, exactly as it would be in a non-animating
  vanilla title. If the owner wants the wordmark to animate too, that is a separate
  card and needs the `wLYOverrides` buffer semantics, which I have not read.
- **Palette.** `TitleScreenPalettes` (`:142`, `:147`) is loaded whole; the patch must
  use a palette index that already exists in it. pokecrystal's banner used palette 6.
  **UNVERIFIED** which Polished palette index is the logo's own. The palette source is
  `gfx/title/title.pal`, included as `TitleScreenPalettes` by
  `engine/movie/title.asm:368-369`; the logo's palette-index mapping is not established here.

### 2.4 Tile and VRAM budget

**UNVERIFIED in detail, but the headroom is not in doubt:** Polished's title loads exactly
two GFX blocks — Suicune into `vTiles1` (`:26-27`) and the logo into `vTiles1` (`:93-94`),
with the crystal into `vTiles0` (`:98-99`). Neither uses `vTiles2`. The vanilla overlay put
the wordmark in `vTiles2` at `SLINK_TITLE_FIRST_TILE` (`patch/gen2/src/title.asm`). For
Polished, `vTiles2` is the natural home **provided** `SLINK_TITLE_FIRST_TILE` does not
collide with `$60`-`$7E`-style ids the title uses — and since `vTiles2` is untouched
here, any tile id in `$80`-`$FF` is free by construction.

I have **not** enumerated the exact first free tile id or the tilemap/attribute budget.
That is the first thing the implementation card must do, and it is falsifiable (§6, F1).

## 3. The main menu

`engine/menus/main_menu.asm`. `MainMenu:` at `:1`, `MainMenuJoypadLoop:` at `:104`,
whose first instruction is `call SetUpMenu` (`:105`) — the surviving vanilla anchor.

### 3.1 What is drawn, from source

| region | source | rows |
|---|---|---|
| item strings | `MainMenuItems` `:58` — per-menu tables of item ids, max **5** (`:51-56`: CONTINUE, NEW_GAME, NEW_GAME_PLUS, OPTION, MUSIC_PLAYER) | see 3.3 |
| strings | `.Strings` `:37-42` — "Continue", "New Game", "New Game+", "Options", "Music Player" | — |
| placement | `PlaceMenuStrings::` `home/menu.asm:589-601`, `rst PlaceString` at `:600` | — |
| clock box | `hlcoord 0, 14` `main_menu.asm:149`; `hlcoord 1, 14` `:190` | **row 14** |
| clock text | `decoord 4, 16` `:179`, `PrintHour` `:182`, `':'` `:183` | **row 16** |
| per-frame redraw | `MainMenu_PrintCurrentTimeAndDay` called at `:11`, `MainMenu` loops at `:22` (`jr MainMenu`) | every frame |

The three menu variants are the `MainMenuItems` tables: NEW_GAME (`:59-64`, 3 items),
CONTINUE (`:65-71`, 4 items), NEW_GAME_PLUS (`:72+`, 5 items).

### 3.2 What is free, in every state

The task's constraint is that items occupy **rows 0-11**, the time box **rows 14-17**,
and **rows 12-17** are redrawn every frame when the clock is not set. So:

| row band | status | verdict |
|---|---|---|
| 0-11 | item rows, all three menus | **occupied** |
| 12-13 | unused by items; below the box | **conditionally free** — but `:149`/`MainMenu_PrintCurrentTimeAndDay` redraws the box region every frame |
| 14-17 | time box; redrawn every frame | **occupied** |

**Row 13 is the only candidate band that is outside the item rows and above the time
box.** I could not locate the row number the menu engine starts items at — the loop that
advances `wMenuSelection`'s row lives in the menu engine (`home/menu.asm`), not in
`main_menu.asm`. **The exact per-item rows are UNVERIFIED**; `docs/polished/HOOKS.md §4`
reports 0-11, which this document takes as given but does not independently confirm.

### 3.3 The proposal that never overlaps

Place the version string at **tile (1, 13)** — one row, above the time box, below the
items — and print it **from the same `call SetUpMenu` bridge as vanilla**, so it is
written before `SetUpMenu`'s own `ApplyTilemap`.

Why this cannot overlap:

- it is not in 0-11, so no item can collide with it;
- it is not in 14-17, so `MainMenu_PrintCurrentTimeAndDay` cannot overwrite it — and
  that routine runs **every frame** via `jr MainMenu` at `:22`, so anything placed in
  14-17 would be destroyed continuously;
- it is above the box, so the box's own border tiles are untouched.

**Risk, stated plainly:** if the menu engine does in fact place an item on row 13,
this overlaps. That is falsifiable without hardware (§6, F2) and must be checked first.

The vanilla hook shape ports directly: rewrite `MainMenuJoypadLoop`'s `call SetUpMenu`
(`:105`) — same size — to `call SlinkMainMenuBridge`, which far-calls the printer and
then `jp SetUpMenu`, exactly as `patch/gen2/src/version.asm` does.

### 3.4 `rst FarCall` takes inline data — use `farcall`

`docs/polished/HOOKS.md` records that Polished's `rst FarCall` consumes an inline
operand, so the vanilla `ld a, BANK(x)` / `ld hl, x` / `rst FarCall` idiom is wrong
here. The bridge must use `farcall SlinkPrintVersion` (which rgbds expands to the
correct inline form) rather than hand-assembling the sequence. Any port that copies
`version.asm`'s three-instruction prologue verbatim will silently call the wrong thing.

## 4. Overlay space

`data/polished/free_space.txt` is the authority and it settles this cleanly:

- **banks 126 (`$7E`) and 127 (`$7F`) are never allocated by the linker** — 16384 free
  bytes each. `$7E` is therefore a fully free service bank, matching HOOKS.md §3.1's
  proposed `SLINK_SERVICE_BANK $7E`. `$7F` is a free second bank if one is ever needed.
- ROM0 has **351 bytes free** in total — enough for a same-size `jp`-to-bridge rewrite
  (SUPERSEDED 2026-10-04: `$015f` is that COUNT, not an address; the real gaps are `$0089-$00FF` and the part of
  `$3F34-$3FFF` after the phone bridge — `docs/polished/HOOKS.md:812-813`)
  in ROM0, but not for anything larger.
- bank 125 (`$7D`) has 660 free; everything else has under 5.

The wordmark 2bpp + row tables + the version string all fit in `$7E` with enormous
headroom. **No bank-switch constraint beyond the usual one**: the bridge lives in ROM0
and `farcall`s into `$7E`; `$7E` must not be assumed to be mapped anywhere else.

## 5. `version_identity` for Polished

Vanilla: `patch/gen2/src/version.asm` prints `"SoulLink {SLINK_BUILD_VERSION}@"` and pads
to a **fixed 20-byte field** (`ds 20 - (.end - .text), 0`) because
`patch/tools/rom_identity.py` masks that field so stamping a version does not move the
build's canonical identity. Its asserts are
`ASSERT .end - .text - 1 <= SCREEN_WIDTH - 1` and `ASSERT .end - .text <= 20`.

Polished needs the same shape with these changes:

| item | vanilla | Polished |
|---|---|---|
| text | `"SoulLink {SLINK_BUILD_VERSION}@"` | same, with the `@` terminator at char `$53` in Polished's charmap — **UNVERIFIED**, the charmap value was not read this pass |
| placement | `hlcoord 1, 10` | `hlcoord 1, 13` (§3.3) |
| field width | 20 bytes, `ds` padded | 20 bytes, `ds` padded — keep, so `rom_identity.py` masks the same span |
| lives in | `SECTION "SLink Version", ROMX, BANK[SLINK_SERVICE_BANK]` | same section, `BANK[$7E]` |

`version_identity` therefore needs **one new spec** keyed on the Polished ROM sha1
(`6930b48af5844d373e3c9130f26d6dd1084cf4ed`, `data/polished_sources.lock.json:14`) with
`field = 20` and the field's offset recorded. The offset is a linker output and must be
**read from the built `.sym`**, not computed — the `.sym` line for `SlinkPrintVersion.text`
is the thing to record.

## 6. Card list, with first falsifiers

| # | card | first falsifier (no hardware needed) |
|---|---|---|
| T1 | enumerate the title screen's free tile ids and the exact first free tile for `SLINK_TITLE_FIRST_TILE` | read every tile id written by `engine/movie/title.asm` and assert the chosen base does not appear; assert `vTiles2` is untouched (it is — `:26-99` writes only `vTiles1`/`vTiles0`) |
| T2 | **prove row 13 is free of menu items** — read the menu-engine row loop in `home/menu.asm` and assert no item lands on 13 in any of the three `MainMenuItems` variants | a static read; if it fails, move the version to row 12/13 boundary and re-check |
| T3 | build the `$7E` overlay section (wordmark + version) and assert it links without moving anything | `rgblink` succeeds and the free-space report still shows `$7E` unallocated elsewhere |
| T4 | same-size ROM0 rewrite of `MainMenuJoypadLoop`'s `call SetUpMenu` (`:105`) | assert the 3 bytes at that offset changed and **nothing else** did — a whole-ROM diff |
| T5 | same-size rewrite of the `call EnableLCD` at `engine/movie/title.asm:179` | same whole-ROM diff |
| T6 | version string renders in all three menu states (NEW_GAME / CONTINUE / NEW_GAME_PLUS) | drive the menu with scripted inputs and screenshot each state |
| T7 | wordmark does not collide with Suicune's frames over the animation | **live only** — the LY trick redraws per scanline and static analysis cannot bound it |
| T8 | `version_identity` spec recorded; re-stamping a build does not move the canonical identity | build twice with different `--version`, assert only the 20-byte field differs |
| T9 | boot check on a real build in DMG and CGB | **live only** |

### 6.1 What can only be verified live

**T7** (Suicune overlap) is the one that genuinely cannot be settled statically: the
STAT-interrupt LY trick (`title.asm:170`, `wLYOverrides` at `:163-165`) means the visible
band depends on per-scanline timing, and the source does not state which scanlines are
overridden for rows 10-11. T6 and T9 also need a real build. Everything else is
falsifiable by reading the ROM, the `.sym`, or a whole-ROM diff.

## 7. Open items settled

### 7.1 (T2) The menu box is **rows 0-6**, not 0-11 — row 13 is free in every state

`engine/menus/main_menu.asm:24-29`:

```
.MenuDataHeader:
   db MENU_BACKUP_TILES
   menu_coords 0, 0, 16, 7
```

`menu_coords x, y, width, height` — so the menu box is **x 0-15, y 0-6**. Items are
printed inside it (`PlaceMenuStrings::` `home/menu.asm:589-601`, `rst PlaceString` at
`:600`), so **no item can be below row 6 in any state**, regardless of whether
Continue / New Game+ / Music Player are present. `MainMenuItems` (`:58`) has three
variants — NEW_GAME 3 items (`:60-64`), CONTINUE 4 (`:66-71`), NEW_GAME_PLUS 5 (`:72+`)
— and they all render into that same box.

Rows 7-13 are therefore **entirely outside the menu box in all three states**. The time
box is rows 14-17 (`hlcoord 0, 14` `:149`, `hlcoord 1, 14` `:190`, `decoord 4, 16` `:179`),
redrawn every frame via `MainMenu_PrintCurrentTimeAndDay` (`:127`, called at `:11`) with
`jr MainMenu` (`:22`).

**Answer: tile (1, 13) is free in every state** — continue present or absent, New Game+
present or absent, clock set or unset. **This contradicts `docs/polished/HOOKS.md §4`,
which reported items at rows 0-11**; that figure appears to have been the *whole* menu
region rather than the box. The conclusion of TITLE.md §3.3 is unchanged but the
*margin* is now proven rather than assumed: there are seven free rows (7-13), not one.

State selection is `MainMenu_GetWhichMenu` (`:86-102`): no save file ->
`MAINMENU_MENU_NEW_GAME`; save file -> `MAINMENU_MENU_NEW_GAME_PLUS`, decremented to
`MAINMENU_MENU_CONTINUE` unless `EVENT_BEAT_LEAF`. None of that moves the box.

### 7.2 (palette) Row palettes, and what the wordmark should use

`engine/movie/title.asm` writes each text row with an explicit `ld a, <palette>` after
the `hlbgcoord`: row 6 -> **4** (`:61`,`:63`), row 7 -> **5** (`:66`,`:68`), row 8 -> **6**
(`:71`,`:73`), row 9 -> **1** (`:77`,`:79`), row 12 -> **8** (`:83`,`:85`). Rows 3 (`:51`)
and 5 (`:56`) have their own immediates I did not capture.

The palette data is `gfx/title/title.pal`, `INCLUDE`d at `engine/movie/title.asm:369`
into the label `TitleScreenPalettes` (`35:4b50`, flat `0xD4B50`), loaded at `:142` and
`:147`.

**Recommendation: palette 6** for the wordmark band — it is the logo's own palette
(row 8, the Pokemon logo row, uses it), so the mark inherits the logo's gold/blue/white
exactly as pokecrystal's overlay did. Rows 10-11 currently carry **no** palette because
they are unwritten, so 6 is a free choice *and* the visually correct one.

### 7.3 (tiles) `vTiles2` is untouched — and the GFX are LZ-compressed

The title writes exactly three GFX blocks:

| data | destination | source |
|---|---|---|
| `TitleSuicuneGFX` | `vTiles1` | `:26-27` |
| `TitleLogoGFX` | `vTiles1` | `:93-94` |
| `TitleCrystalGFX` | `vTiles0` | `:98-99` |

All three are `INCBIN "gfx/title/*.2bpp.lz"` (`:360`, `:363`, `:366`) — **LZ-compressed**,
which the vanilla overlay's plain `.2bpp` payload cannot be. The wordmark must go to
**`vTiles2`**, which no line in the file writes, so any tile id there is free by
construction.

**UNVERIFIED:** the first free tile id and the dedup count. `tools/gen_gen1_title.py`
needs PIL and a draw run I did not perform, so `SLINK_TITLE_LOGO_TILES` and
`SLINK_TITLE_FIRST_TILE` are not read. The vanilla overlay *allocates* its tile range
from the generated `SLINK_TITLE_FIRST_TILE` (`patch/gen2/src/title.asm`:
`ld de, vTiles2 tile SLINK_TITLE_FIRST_TILE` + `ld bc, SLINK_TITLE_LOGO_TILES tiles`);
the Polished overlay must do the same rather than hardcode a range. **Do not assume 18
tiles** — the count is the deduplicated 8x8 cell count.

### 7.4 (charmap) `@` is `$53` — confirmed

`constants/charmap.asm:43`: `ctxtmap "@",        $53, 001011010`.

So the version text is a normal Polished string and `patch/gen2/src/version.asm`'s
`db "SoulLink {SLINK_BUILD_VERSION}@"` form carries over unchanged. The 20-byte fixed
field and the two `ASSERT`s should also carry over, unchanged.

### 7.5 (LY) `wLYOverrides` cannot displace rows 10-11 — the buffer is emptied and never refilled on this path

`engine/movie/title.asm:156-170`:

```
; LY/SCX trickery starts here
   push af
   ld a, BANK(wLYOverrides)
   ldh [rWBK], a
; Make sure the LYOverrides buffer is empty
   ld hl, wLYOverrides
   xor a
   ld bc, wLYOverridesEnd - wLYOverrides
   rst ByteFill
; Let LCD Stat know we're messing around with SCX
   ld hl, rIE
   set B_IE_STAT, [hl]
```

`xor a` + `rst ByteFill` over the whole buffer (`wLYOverrides` `05:de00` flat `0x1DE00`
to `wLYOverridesEnd` `05:de90` flat `0x1DE90`, 144 bytes) leaves every entry **zero**.
A tree-wide grep shows the only writers of `wLYOverrides` are
`engine/battle/battle_transition.asm` and `engine/sliding_intro.asm` — **neither is on
the title screen path**, and `engine/movie/title.asm` contains no write to it after the
`ByteFill`.

**Answer: rows 10-11 cannot be displaced by an LY override during the title animation.**
That converts the one item I had marked live-only (card T7) into a static result. The
remaining live-only items are T6 (three menu states rendered) and T9 (DMG + CGB boot).

**Correction to §2.2 of this document:** I described the STAT/LY trick as something the
title's animation drives. It is not — the title *enables* STAT and *clears* the override
buffer, then animates via `SuicuneFrameIterator` (`:208`) and `AnimateTitleCrystal`
(`:335`) on ordinary frames. The override buffer belongs to the intro/battle-transition
code that shares the symbol.

## Coordinator correction (2026-10-04) to §7 T2: the menu box is NOT rows 0-6, and (1,13) is not free in every state

`menu_coords` is `x1, y1, x2, y2` (`macros/coords.asm:66-69`: start and END coordinates), not width/height, and the bottom row is recomputed from the item count: `AutomaticGetMenuBottomCoord` (`home/menu.asm:454-465`) sets `bottom = top + items*2 + 1`. So the box is rows 0..7 with 3 items, 0..9 with 4, 0..11 with 5 (New Game+), which is what HOOKS.md §4 said ("rows 0-11"); that file is right and §7 F1 above is withdrawn. Rows 12-13 are free of the menu in every state. The clock area is drawn by `MainMenu_PrintCurrentTimeAndDay` only when a save exists (`wSaveFileExists`): with the RTC set it is a 4-row box at rows 14-17; with the RTC NOT set, `SpeechTextbox` (rows 12-17) is redrawn on every pass of `MainMenuJoypadLoop .loop` (`main_menu.asm:105-121`, the per-iteration `call MainMenu_PrintCurrentTimeAndDay`), wiping anything printed at row 13 once. The `call SetUpMenu` hook at `MainMenuJoypadLoop` runs ONCE before that loop, so a version string printed there at (1,13) survives with the RTC set (or no save) but is erased on the first loop pass when a save exists and the RTC is unset. Consequence for the card: print the version from a bridge on the per-iteration `call MainMenu_PrintCurrentTimeAndDay` in `.loop` (print after the redraw each pass), or accept that state shows no version; decide before writing the overlay. T2 is NOT retired. The LY finding (§7.3: the title clears wLYOverrides and never refills it; rows 10-11 cannot be displaced) is unaffected.

## 8. Version placement and tiles, round 2

This section **withdraws §7.1**. The coordinator's correction is right: `menu_coords` is
`x1, y1, x2, y2` and the bottom row is recomputed from the item count, so the menu box
does reach row 11 with five items — `docs/polished/HOOKS.md` was right and §7.1 was wrong.

### 8.1 Screen occupancy, every state

`home/menu.asm:454-467`:

```
AutomaticGetMenuBottomCoord::
   ld a, [wMenuBorderLeftCoord]  ; -> c
   ld a, [wMenuBorderRightCoord]
   sub c                          ; -> c = width
   ld a, [wMenuDataItems]
   add a
   inc a
   ld b, a                        ; b = items * 2 + 1
   ld a, [wMenuBorderTopCoord]
   add b
   ld [wMenuBorderBottomCoord], a
```

so **bottom = top + items*2 + 1**. With `menu_coords 0, 0, 16, 7` the top is 0 and
`MenuBoxCoord2Tile` uses the computed bottom, not the literal `7`.

Box bottom by item count: 3 items -> **row 7**; 4 -> **row 9**; 5 -> **row 11**.

The lower region depends on the save/RTC state:

| region | source | rows | redrawn |
|---|---|---|---|
| menu box | `AutomaticGetMenuBottomCoord` | `0 .. items*2+1` | once, by `SetUpMenu` |
| time box (RTC set) | `main_menu.asm:149-151` `hlcoord 0, 14` / `lb bc, 2, 18` / `jmp Textbox` | **14-15** | **every pass** |
| speech box (RTC unset) | `main_menu.asm:146-148` `call CheckRTCStatus` / `and $80` / `jmp nz, SpeechTextbox` | **12-17** | **every pass** |
| time text | `:190` `hlcoord 1, 14`, `:179` `decoord 4, 16` | 14, 16 | every pass |

`SpeechTextbox::` (`home/text.asm:52-55`) uses `TEXTBOX_X`/`TEXTBOX_Y`/
`TEXTBOX_INNERH`/`TEXTBOX_INNERW`, and `constants/text_constants.asm` gives
`TEXTBOX_HEIGHT EQU 6` (`:15`), `TEXTBOX_Y EQU SCREEN_HEIGHT - TEXTBOX_HEIGHT` (`:18`)
— so **rows 12-17**.

`MainMenu_PrintCurrentTimeAndDay:` (`:127-143`) is called from `MainMenuJoypadLoop`'s
`.loop` (`:107`), which `jr MainMenu` (`:22`) re-enters. So the lower region is
**repainted every frame** in every state where a save exists.

### 8.2 The full occupancy matrix

| save | RTC | items | menu box | lower region | rows free below the box |
|---|---|---|---|---|---|
| none | — | 3 | 0-7 | none (`:129-130` returns) | 8-17 |
| yes | set | 3 | 0-7 | time box 14-15, text 14/16 | 8-13, 17 |
| yes | unset | 3 | 0-7 | speech box **12-17** | 8-11 |
| yes | set | 4 | 0-9 | time box 14-15, text 14/16 | 10-13, 17 |
| yes | unset | 4 | 0-9 | speech box **12-17** | 10-11 |
| yes | set | 5 | 0-11 | time box 14-15, text 14/16 | 12-13, 17 |
| yes | unset | 5 | 0-11 | speech box **12-17** | **none** |

### 8.3 There is no row free in every state

The worst case is **save present, RTC unset, five items**: the box reaches row 11 and
the speech box takes 12-17. **Rows 12-17 are not always free**, so §3.3's "print once
from the `SetUpMenu` hook" is wrong: with the RTC unset, `SpeechTextbox` erases anything
written there on the next `.loop` pass.

**Consequence: the version string must be printed on every pass, not once.** There is no
placement that avoids this, because the region that would be free in the RTC-unset state
is exactly the region the speech box occupies. This is a design consequence of Polished's
own menu, not a limitation of the hook.

### 8.4 The per-pass hook

**Anchor.** `MainMenu_PrintCurrentTimeAndDay` is `12:43ed` (`.sym`), and the call site
inside `MainMenuJoypadLoop` (`12:43ca`, flat `0x483CA`) is at **flat `0x483CD`, bytes
`cd ed 43`** — verified in the ROM: the routine opens `cd a0 19` (`call SetUpMenu`) then
`cd ed 43` (`call $43ed`). Exactly **3 bytes**, so a same-size rewrite is available.

**Rewrite** `cd ed 43` -> `cd lo hi` (`call SlinkMainMenuLoopBridge`).

**Bridge**, in ROM0:

```
SlinkMainMenuLoopBridge::
   call MainMenu_PrintCurrentTimeAndDay
   ; the original ended with `ld a,$1 / ldh [hBGMapMode],a` (main_menu.asm:141-142),
   ; so the tilemap is already flushed. Writing after that needs a second flush.
   xor a
   ldh [hBGMapMode], a
   farcall SlinkPrintVersion        ; rst FarCall takes inline data on Polished
   ld a, 1
   ldh [hBGMapMode], a
   ret
```

**Ordering.** `MainMenu_PrintCurrentTimeAndDay` clears `hBGMapMode` to 0 at `:131-132`
before drawing and sets it to 1 at `:141-142` after, which is the flush. Anything printed
*after* it lands in `wTilemap` with `hBGMapMode == 1` and is **never copied to VRAM** —
so the `xor a` / `ld a,1` pair around the print is mandatory, not cosmetic. Printing
*before* the original would instead be erased by the very next thing the original does.

**One-time vs per-pass trade-off.** A one-time print at `SetUpMenu` is cheaper but, per
§8.3, is erased in the RTC-unset state. A per-pass print costs one bounded literal-glyph
loop per frame in the main menu — a menu that is idle and redrawn at 60 Hz. That is the
price of correctness here.

**Current renderer: `SlinkPrintVersionBounded`.** Row 10 stays the per-pass origin. The active
`wMenuBorderLeftCoord` and `wMenuBorderRightCoord` constrain the horizontal interval to
`left+1 .. right-1`; the native rectangle is columns `0 .. 16`, not the full screen width.
The ROM copies at most `right-left-1` literal glyphs and stops at `@` (`$53`), truncating
long versions without wrapping or touching either border. The 20-byte `SlinkVersionText`
field is emitted with the scoped `no_ngrams` charmap: the nine-byte `SoulLink ` prefix,
literal stamp and padding keep the version-masked identity contract and slot length stable.
The bounded routine is outside that field, so changing the stamp does not move code.

`tests/unit/test_polished_version_bounds.py` executes the native menu-coordinate calculation,
border painter and per-pass bridge against the actual UPS-applied ROM. Long and short stamps
cover New Game, Continue and NG+ rectangles, checking exact changed tile cells and untouched
borders. This is rendering-model evidence, not live video/timing evidence.

Native smoke of the rebuilt overlay also reached the no-save New Game menu in BizHawk.
A private `SoulLink v12.45.678` stamp rendered `SoulLink v12.45` inside columns `1 .. 15`
with both border/exterior intervals unchanged (`rf-rom-title-smoke`: PASS, frame 258).
The private stamp retained canonical SHA1 `1ef4effd9e33177a645c41f9b20d06fca9597c67`.
For the current engine, `hBGMapMode == 1` enables `UpdateBGMap`'s VBlank tilemap copy;
the mode reset around the print is drawing hygiene, not a requirement for visibility.

### 8.5 Tile allocation — settled

`python tools/gen_gen1_title.py` from the worktree root:

```
crystal 18 tiles, gold/silver 16 tiles
gen3 logo 72x16
pure line 37px in 6 tiles
18 tiles
```

`patch/gen2/src/title_rows_crystal.inc` then reads:

```
DEF SLINK_TITLE_LOGO_TILES EQU 18
DEF SLINK_TITLE_FIRST_TILE EQU $60
SlinkTitleLogoRow0:   db $60,$61,$62,$63,$64,$65,$66,$67,$68
SlinkTitleLogoRow1:   db $69,$6A,$6B,$6C,$6D,$6E,$6F,$70,$71
```

So the Crystal band is **18 tiles at `$60`-`$71`** (9 + 9, no dedup collisions in the
Crystal style; G/S dedups to 16 over 8 columns).

**Collision proof.** The range `$60`-`$71` goes into **`vTiles2`**. `engine/movie/title.asm`
writes only `vTiles1` (`TitleSuicuneGFX` `:26-27`, `TitleLogoGFX` `:93-94`) and `vTiles0`
(`TitleCrystalGFX` `:98-99`). **No line in the file writes `vTiles2`**, so no tile the
title places can occupy `$60`-`$71`. Because the whole allocation sits in an unwritten
block, the range is safe regardless of what the title's tilesets contain.

**Generator side effect — reported and repaired.** The run modified the tracked file
`patch/tools/gen3_title_art.py`. I restored it with
`git show HEAD:patch/tools/gen3_title_art.py > patch/tools/gen3_title_art.py`;
`git diff --numstat` on it is now empty (only git's `core.autocrlf` LF/CRLF warning
remains, which is the known checkout-dependence in this repo, not a content change).
`patch/gen2/src/title_rows_crystal.inc` and `title_rows_gs.inc` were unchanged by the
run, so the committed values already matched.

### 8.6 Palette immediates for rows 3 and 5

Read from `engine/movie/title.asm`: row 3 at `:51` is followed by `ld a, 2` (`:53`), and
row 5 at `:56` by `ld a, 3` (`:58`). Combined with §7.2: row 6 -> 4, row 7 -> 5,
row 8 -> 6, row 9 -> 1, row 12 -> 8. **Every title text row is now accounted for.**

### 8.7 What this changes in the earlier sections

- **§3.3's proposal is withdrawn.** One-time printing at `(1, 13)` does not survive the
  RTC-unset state. Replace it with the per-pass hook in §8.4 and a placement that is
  *re-painted*, not merely *placed*.
- **§7.1 is withdrawn** (§8.1 supersedes it).
- **§7.3's tile unknowns are settled**: 18 tiles at `$60`-`$71`, collision-free (§8.5).
- **§6's card T2 is withdrawn and replaced** by the per-pass hook card; T1 is done.

## Coordinator correction (2026-10-04) to §8 F3 'flush ordering'

The claim that text printed after the original returns 'lands in wTilemap with hBGMapMode == 1 and is never copied to VRAM' is WRONG. `UpdateBGMap` (`home/video.asm:148-166`) treats `hBGMapMode` 1 as 'copy BG map 0 tiles from wTilemap, in halves, each VBlank', so anything written to `wTilemap` while the mode is 1 reaches VRAM within a frame or two. Wrapping the print in `xor a / ldh [hBGMapMode],a` ... restore is still the right hygiene (it makes the string appear in the same copy as the clock text, with no one-frame tear), but it is not required for visibility; falsifier 4 as written would not fail. The rest of §8 stands and was independently re-verified: the hook anchor is `cd ed 43` at flat `0x483CD` (`MainMenuJoypadLoop` `12:43ca` reads `cd a0 19 cd ed 43`; `MainMenu_PrintCurrentTimeAndDay` is `12:43ed`), and per-pass printing is required.

```json
CLAIMS: [{"path":"F:/slink-work/cache/polished/src/home/menu.asm","line":454,"expect":"AutomaticGetMenuBottomCoord::"},{"path":"F:/slink-work/cache/polished/src/home/menu.asm","line":94,"expect":"ld a, [wMenuDataItems]"},{"path":"F:/slink-work/cache/polished/src/home/menu.asm","line":360,"expect":"ld [wMenuBorderBottomCoord], a"},{"path":"F:/slink-work/cache/polished/src/home/menu.asm","line":589,"expect":"PlaceMenuStrings::"},{"path":"F:/slink-work/cache/polished/src/engine/menus/main_menu.asm","line":104,"expect":"MainMenuJoypadLoop:"},{"path":"F:/slink-work/cache/polished/src/engine/menus/main_menu.asm","line":105,"expect":"call SetUpMenu"},{"path":"F:/slink-work/cache/polished/src/engine/menus/main_menu.asm","line":11,"expect":"call MainMenu_PrintCurrentTimeAndDay"},{"path":"F:/slink-work/cache/polished/src/engine/menus/main_menu.asm","line":127,"expect":"MainMenu_PrintCurrentTimeAndDay:"},{"path":"F:/slink-work/cache/polished/src/engine/menus/main_menu.asm","line":132,"expect":"ldh [hBGMapMode], a"},{"path":"F:/slink-work/cache/polished/src/engine/menus/main_menu.asm","line":149,"expect":"hlcoord 0, 14"},{"path":"F:/slink-work/cache/polished/src/engine/menus/main_menu.asm","line":150,"expect":"lb bc, 2, 18"},{"path":"F:/slink-work/cache/polished/src/engine/menus/main_menu.asm","line":153,"expect":".PlaceTime:"},{"path":"F:/slink-work/cache/polished/src/engine/menus/main_menu.asm","line":190,"expect":"hlcoord 1, 14"},{"path":"F:/slink-work/cache/polished/src/engine/menus/main_menu.asm","line":179,"expect":"decoord 4, 16"},{"path":"F:/slink-work/cache/polished/src/engine/menus/main_menu.asm","line":22,"expect":"jr MainMenu"},{"path":"F:/slink-work/cache/polished/src/engine/menus/main_menu.asm","line":26,"expect":"menu_coords 0, 0, 16, 7"},{"path":"F:/slink-work/cache/polished/src/engine/menus/main_menu.asm","line":86,"expect":"MainMenu_GetWhichMenu:"},{"path":"F:/slink-work/cache/polished/src/engine/menus/main_menu.asm","line":58,"expect":"MainMenuItems:"},{"path":"F:/slink-work/cache/polished/src/home/text.asm","line":52,"expect":"SpeechTextbox::"},{"path":"F:/slink-work/cache/polished/src/constants/text_constants.asm","line":14,"expect":"DEF TEXTBOX_HEIGHT EQU 6"},{"path":"F:/slink-work/cache/polished/src/constants/text_constants.asm","line":18,"expect":"DEF TEXTBOX_Y      EQU SCREEN_HEIGHT - TEXTBOX_HEIGHT"},{"path":"F:/slink-work/cache/polished/src/constants/text_constants.asm","line":11,"expect":"DEF BORDER_WIDTH   EQU 2"},{"path":"F:/slink-work/cache/polished/src/constants/text_constants.asm","line":12,"expect":"DEF TEXTBOX_WIDTH  EQU SCREEN_WIDTH"},{"path":"F:/slink-work/cache/polished/src/constants/charmap.asm","line":43,"expect":"ctxtmap \"@\",        $53, 001011010"},{"path":"F:/slink-work/cache/polished/src/engine/movie/title.asm","line":51,"expect":"hlbgcoord 0, 3"},{"path":"F:/slink-work/cache/polished/src/engine/movie/title.asm","line":56,"expect":"hlbgcoord 0, 5"},{"path":"F:/slink-work/cache/polished/src/engine/movie/title.asm","line":61,"expect":"hlbgcoord 0, 6"},{"path":"F:/slink-work/cache/polished/src/engine/movie/title.asm","line":66,"expect":"hlbgcoord 0, 7"},{"path":"F:/slink-work/cache/polished/src/engine/movie/title.asm","line":71,"expect":"hlbgcoord 0, 8"},{"path":"F:/slink-work/cache/polished/src/engine/movie/title.asm","line":77,"expect":"hlbgcoord 5, 9"},{"path":"F:/slink-work/cache/polished/src/engine/movie/title.asm","line":83,"expect":"hlbgcoord 0, 12"},{"path":"F:/slink-work/cache/polished/src/engine/movie/title.asm","line":26,"expect":"ld hl, TitleSuicuneGFX"},{"path":"F:/slink-work/cache/polished/src/engine/movie/title.asm","line":93,"expect":"ld hl, TitleLogoGFX"},{"path":"F:/slink-work/cache/polished/src/engine/movie/title.asm","line":98,"expect":"ld hl, TitleCrystalGFX"},{"path":"F:/slink-work/cache/polished/src/engine/movie/title.asm","line":179,"expect":"call EnableLCD"},{"path":"F:/slink-work/wt/polished/patch/gen2/src/title_rows_crystal.inc","line":2,"expect":"DEF SLINK_TITLE_LOGO_TILES EQU 18"},{"path":"F:/slink-work/wt/polished/patch/gen2/src/title_rows_crystal.inc","line":3,"expect":"DEF SLINK_TITLE_FIRST_TILE EQU $60"},{"path":"F:/slink-work/wt/polished/patch/gen2/src/title_rows_crystal.inc","line":4,"expect":"SlinkTitleLogoRow0:"},{"path":"F:/slink-work/wt/polished/patch/gen2/src/title_rows_crystal.inc","line":6,"expect":"SlinkTitleLogoRow1:"},{"path":"F:/slink-work/wt/polished/patch/gen2/src/version.asm","line":17,"expect":"SlinkPrintVersion:"},{"path":"F:/slink-work/wt/polished/patch/gen2/src/version.asm","line":18,"expect":"hlcoord 1, 10"},{"path":"F:/slink-work/wt/polished/patch/gen2/src/version.asm","line":22,"expect":"db \"SoulLink {SLINK_BUILD_VERSION}@\""},{"path":"F:/slink-work/wt/polished/patch/gen2/src/version.asm","line":26,"expect":"ds 20 - (.end - .text), 0"},{"path":"F:/slink-work/wt/polished/tools/gen_gen1_title.py","line":32,"expect":"COLS, ROWS = 9, 2"},{"path":"F:/slink-work/wt/polished/data/polished/polishedcrystal.sym","line":12041,"expect":"12:43ca MainMenuJoypadLoop"},{"path":"F:/slink-work/wt/polished/data/polished/polishedcrystal.sym","line":12045,"expect":"12:43ed MainMenu_PrintCurrentTimeAndDay"},{"path":"F:/slink-work/wt/polished/data/polished/polishedcrystal.sym","line":32325,"expect":"35:4000 _TitleScreen"},{"path":"F:/slink-work/wt/polished/data/polished/polishedcrystal.sym","line":725,"expect":"00:24da EnableLCD"},{"path":"F:/slink-work/wt/polished/data/polished/polishedcrystal.sym","line":32343,"expect":"35:4b50 TitleScreenPalettes"},{"path":"F:/slink-work/wt/polished/data/polished/polishedcrystal.sym","line":560,"expect":"00:1a96 PlaceMenuStrings"},{"path":"F:/slink-work/wt/polished/data/polished/polishedcrystal.sym","line":18,"expect":"00:0030 PlaceString"},{"path":"F:/slink-work/wt/polished/data/polished/free_space.txt","line":3,"expect":"2 ROMX bank(s) never allocated by the linker"},{"path":"F:/slink-work/wt/polished/data/polished/free_space.txt","line":10,"expect":"bank 126:  16384 free ($4000)"},{"path":"F:/slink-work/wt/polished/docs/polished/HOOKS.md","line":59,"expect":"MainMenuJoypadLoop"}]
```
