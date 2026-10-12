# Polished Crystal client: first live results (2026-10-04)

The first live run of the Polished client (`lua/gen2/polished.lua` via `compose_polished`) on a running cartridge.
The owner authorised two runs on 2026-10-04: "Polished hello + catch + box census" and "Polished trade stack
fingerprint". **These runs are DEV evidence only. They are not PHYSICAL receipts for the gate system.** Receipt
generation is the coordinator's call.

## Setup

| item | value |
|---|---|
| ROM | overlay built by `tools/build_polished_companion.py` (cached `F:/slink-work/cache/polished/companion-overlay/polishedcrystal-3.2.3.gbc`), staged as `F:/slink-work/lanes/pol-live/rom/pol_overlay.gbc`; sha1 `29ea04c24a46d9210c899355fe752f32d2880de8` = `data/polished/overlay_provenance.json` output.sha1 (checked by the harness before every launch) |
| control ROM | release `polishedcrystal-3.2.3.gbc`, sha1 `6930b48af5844d373e3c9130f26d6dd1084cf4ed` (provenance base_sha1); map-corruption controls only, no client |
| emulator | EmuHawk 2.11.1 (`E:/Howard/Bizhawk`), per-run config copy (`gen1_playthrough.write_run_config`, rewind off, CGB mode, RTC pinned), SaveRAM in the lane dir |
| server | `python -m server.server` subprocess on free local ports, private `--data-dir`, `--wire-log`; `rom_contract.json` = `{"players": {"a": {"rom_sha1": <overlay sha1>}}}` (exercises `rom_contract_by_sha1`) |
| drivers | `tools/polished_live/harness.py` (`setup`, `control`, `live`), `pol_lib.lua`, `setup.lua`, `control.lua`, `live.lua` |
| facts | addresses from `data/polished/polished_slink.sym`; the capture site from `data/games/polished_crystal/engine_signals.json`; map/tileset/collision/menu facts from the pinned source (`F:/slink-work/cache/polished/companion-overlay`, commit 3fa43192). Screenshots only confirmed what the RAM already showed |
| evidence dir | `F:/slink-work/lanes/pol-live/` (`run3_stage14_ok/`, `run5_stage23/`, `live/` = run 6, `control_*`, `setup_*`, `bad_poke_runs/`) |

Every EmuHawk was killed by its own recorded PID (`taskkill /T /F /PID`). At most two instances ran at once.

### Entry path (finding 1)

`lua/game_detect.lua` is **never reached** for a Game Boy cartridge. The real path is:

1. the Gen 2 block in `lua/slink.lua`;
2. `lua/gen2/entry.lua` `Entry.detect_title`, where header `PKPCRYSTAL` maps to title `polished`;
3. `lua/gen2/run.lua`;
4. `Entry.build`, which routes `title == "polished"` to `Entry.admit_polished` / `compose_polished`.

The Polished header is already routed, so no change to `slink.lua` or `game_detect.lua` is needed. The live driver
dofiles the real `lua/slink.lua`. Client log line:
`[SLink-gen2] polished_crystal/polished overlay PRODUCTION (DEV_OVERLAY_SHA1) player a -> 127.0.0.1:<port> (rom 29ea04c2)`.

### SYNTH save (O-33, disclosed)

`harness.py setup` / `setup.lua` builds the fixture `fixture/polished_overlay_warp.SaveRAM` (sha256
`75c7a5dc30126f746567202cfb39fe583dfa04eb226063560541f6cbd29f36b8`).

- **NATIVE:** cold boot, title, NEW GAME, initial options left at defaults (B), Elm's speech, and the clock, gender
  and name prompts (A presses; the player is named `Aaaaaaa`, ID `$D1C2`). Then the arrival in
  `PLAYERS_HOUSE_2F` (frame 3044), and START -> SAVE -> YES (`SaveGameData` ran). The save checks out:
  `sSaveVersion $000A`, checksum `$13E4` computed = stored.
- **SYNTH (WRAM writes between those two native steps):**
  - **Party:** `wPartyCount` = 5, five party structs, OT names and nicknames. The mons are level-50 CROBAT,
    JOLTEON, GOLDUCK, NIDOKING and DODRIO: base stats from `data/pokemon/base_stats/*.asm`, DV 15 (HP DV 14-i),
    move TACKLE, OT ID/name copied from the save's own `wPlayerID`/`wPlayerName`.
  - **Bag:** `wNumBalls`/`wBalls` = 99 POKE_BALL.
  - **Position:** the engine's own warp, the same bytes `Script_warp` writes (`engine/overworld/scripting.asm:2180`).
    That is `wMapGroup` 24, `wMapNumber` 3 (ROUTE_29), `wXCoord` 48, `wYCoord` 12 (COLL_LONG_GRASS in
    `Route29.ablk`), `wDefaultSpawnpoint` = $FF, `hMapEntryMethod` = MAPSETUP_WARP ($F1) and `wMapStatus` = 1. The
    overworld loop then ran MAPSETUP_WARP natively, landing at frame 3129, before the save.
- The game rewrote the synthetic form byte (1 -> 0) before the save. Party keys end `:00`/`:40`.

### Map-corruption investigation (owner screenshot)

The first runs used a **bare** position poke (the four position bytes only, no map setup). Route 29 rendered
corrupt. Controls, each without any SLink Lua:

| control | Route 29 header after CONTINUE | encounter | `continue.png` sha256 |
|---|---|---|---|
| overlay ROM, bare poke | tileset 1, 30x9 (before the save it was still the bedroom's: tileset 11, 4x3) | none, 0 steps in 8000 frames | `5886d43b...` |
| release ROM, bare poke | same | none, 0 steps | `5886d43b...` (pixel-identical) |
| overlay ROM, engine warp | tileset 1, 30x9 | yes, 10 steps / 377 frames | `7936ded0...` |
| release ROM, engine warp | tileset 1, 30x9 | yes, 6 steps / 313 frames | `7936ded0...` (pixel-identical) |

**Cause:** the bare poke. The overlay and the client are excluded: both ROMs behave identically, and the setup and
control drivers never load the client. The poke-mode fixtures are byte-identical across the two ROMs (`0ec53ea3...`).
Every stage result below comes from the engine-warp fixture. The poke runs are archived under `bad_poke_runs/` and
count for nothing except the observations that do not depend on the map: Stage 1 admission and the Stage 4 shape,
both re-taken on the warp fixture.

**Client writes.** `live.lua` wraps every `memory.write*` function before `lua/slink.lua` loads, and the driver
itself writes nothing in the live run. Result: **0 Lua-originated writes** in runs 5 and 6. `panel_writes.log` is
empty, and the client registers exactly one hook (`SLink-gen2-polished:capture_party`).

## STAGE 1: hello admitted. PASS

```
python tools/polished_live/harness.py live   (POL_STAGES=14: run 3; POL_STAGES=23: run 5)
```

Run 3 (EmuHawk PID 42868, server PID 48120). `Entry.build` hashed the ROM; `lua/slink.lua` returned after 8.5 s CPU.

| frame | event |
|---|---|
| 208 | `TitleScreenMain` |
| 220 | `MainMenu` |
| **286** | **hello sent** |
| 336 | overworld first takes input on ROUTE_29 |

Hello fields:

```
rom_type polished_crystal  foundation gen2_polished  artifact_kind overlay
rom_sha1 29ea04c24a46d9210c899355fe752f32d2880de8  companion_abi 3  panel false
party 5 [169:EFFFFF:D1C2:0A9:00 135:DFFFFF:D1C2:087:00 55:CFFFFF:D1C2:037:40 34:BFFFFF:D1C2:022:00 85:AFFFFF:D1C2:055:40]
pc_boxes []  pc_boxes_generation 2  area route_29 / Route 29  ot_id 53698  trainer_name Aaaaaaa
```

Server log (`run3_stage14_ok/server.log`):

```
[a] admission: admitted — cartridge sha1 matches the contract
[a] hello rom=polished_crystal area='route_29' party=5
[a] Identity locked: Aaaaaaa (OT 53698)
Adapter switched to gen2_polished (rom_type=polished_crystal)
[a] route polished_crystal -> gen2_polished (production)
```

`/api/status` `players.a.party_details` decodes every slot correctly (species, level 50, HP, nickname, moves).

**Observation S1-a.** The hello went out at frame 286. That is from the CONTINUE / main-menu screen, after
`TryLoadSaveFile` filled WRAM and 50 frames before the overworld took input. `hello_unheld` counts "party readable
plus 8 stable polls" as a live game. This is the same trap as the Gen 1 note (`reference_bizhawk_gate_drivers`:
"party readable is not in the game"). The identity and party are the save's, so the hello is correct in content but
not in timing.

**Observation S1-b.** The client logs `writes ENABLED` and `nuzlocke ACTIVE (pokeballs already in bag at startup)`.
It made no write attempt (see above).

## STAGE 2: wild catch. PASS

Run 5 (EmuHawk PID 16196, server PID 48180), fixture `polished_overlay_warp.SaveRAM`. The probe is a read-only
`event.on_bus_exec` on System Bus $652B, the same address the client hooks, from the pack: bank 3, flat `0xE52B`,
expected `D7084513FA09D1`.

| battle | what happened | capture site (bank 3) |
|---|---|---|
| escape (negative control) | Pidgey; B (cursor to Run, `menu.asm .b_button`) then A; `BattleMenu_Run` ran once | **0 hits** |
| party catch | Pidgey Lv3; throw 1 failed (frame 2768), throw 2 failed (frame 3600), throw 3 caught (frame 4432) | **0, 0, 1** |
| box catch (Stage 3) | full party: `PokeBallEffect.SendToPC` ran | **0 hits** |

- Hits of $652B in any other bank: 0 for the whole run.
- The hit: **frame 5334, hROMBank $03, PC $652B, SP $C0CF**. At the hit `wPartyCount` was already 6 and the last
  slot's species byte was already 16. The `inc [hl]` and the record copy precede the site, as `signals.lua` assumes.
- **Frame alignment:** the per-frame end-of-frame poll first saw `wPartyCount` 6 after the frame that contained the
  hit. It is labelled 5335 only because the poll runs after `frameadvance`, so this is the same frame as the hit.
- The client emitted `capture` in the same frame window (labelled 5335, +1):
  `{"event":"capture","key":"AE7343:D1C2:010:00","species_id":16,"level":3,"hp":16,"maxHP":16,"area_id":"route_29","in_box":false,"nickname":"Pidgey",...}`
- Server:
  ```
  [a] capture key=AE7343:D1C2:010:00 lv=3 area='route_29'
  [PENDING] route_29 player=a action=add
  ```
  `/api/status` `pending_captures` =
  `{"route_29": {"a": {"key": "AE7343:D1C2:010:00", "species": 16, "level": 3, ...}}}`.
- `signals:status()`: registered `["capture_party"]`, refusals `{}`, drops `{}`, `physical_status` OPEN.

**Observation S2-a.** The server then quarantined the catch (`quarantine: AE7343:D → box (pending link)`) and
queued `box_mon`. The client answered `box_mon_failed reason="Gen 2 box executor not composed"`. The server logged
`unknown event 'box_mon_failed'` and then `box_mon_failed ... party model restored`. A Polished solo catch with no
partner therefore always produces this refusal round trip.

**Observation S2-b.** Battle text prompts never hit `BlinkCursor` (00:086b). The driver advanced battle text with a
90-frame idle fallback (B). Driver detail only.

## STAGE 3: box arrival and census. PARTIAL

The PC deposit UI was not driven. The box arrival used the **native** catch-to-box path instead: with a full party,
`PokeBallEffect.SendToPC` ran at the catch. Not a SYNTH box fixture.

- The catch reached box 1. The capture site did **not** fire (0 hits); correct, since a box catch must not report as
  `capture_party`.
- **FAIL: the client never rescanned the census after the box arrival.** All 269 ticks and the hello carry
  `pc_boxes []` with `pc_boxes_generation 2`. This held after the catch and after a native START -> SAVE as well.
  - Root cause (`lua/gen2/client.lua`): rescans run only on `pending_rescan` or while `box_complete` is false
    (around line 1834).
  - The Polished composition produces no event that sets `pending_rescan`. There is no `capture_box` site, no
    battle-end observation (no `pending_safe`), no PC events, and `read_current_box_num()` returns -1.
  - So after a complete first scan the census is frozen until the next hello.
- **The census itself reads the box mon correctly.** Run 6 (EmuHawk PID 35060, server PID 9748) rebooted from run
  5's flushed SaveRAM (`fixture/polished_overlay_after_run5.SaveRAM`, sha256 `11b283cd...`). Its hello carried
  `pc_boxes = [{"box":0,"slot":0,"key":"A35D6B:D1C2:010:00","species_id":16,"level":2,"nickname":"Pidgey","moves":[33,0,0,0]}]`
  with **`pc_boxes_generation 1`**, and the party Pidgey `AE7343:D1C2:010:00` in slot 6. The run's one FAIL line
  is the driver's "5-mon party" check, which does not apply to that save.
- **Save-time refusal (NEWBOX section 7):** `wGameLogicPaused` was nonzero on 62 sampled end-of-frames during the
  native save. No census scan happened in that window because of the rescan gap above. The `native save running`
  refusal was therefore **not exercised live**; no `polished box census withheld` line appears in `slink_lua.log`.
- The PC-open refusal is not observable: there is no PC-open predicate (NEWBOX section 7).

## STAGE 4: frame-wait stack fingerprint. MEASURED (read-only)

**Not at the receptionist.** The receptionist is in a Pokemon Center 2F, which this save cannot reach cheaply.
Instead: an exec probe on `SlinkDelayFrameBridge` (ROM0 $0070, called from DelayFrame's 7-byte lead-in), sampled
only on frames where `OWPlayerInput` ran (the idle overworld on ROUTE_29). 300 samples, hROMBank $25, SVBK 1,
**SP = $C0DE** every time. Raw `sp+0..sp+31` (run 3, warp fixture):

```
ab0d c251 00fe 86d6 4446 22d1 6b51 e250 1400 018a 6114 0012 0f00 5843 1400 0177   x283
ab0d c251 20fe 86d6 4446 22d1 6b51 e250 1400 018a 6114 0012 0f00 5843 1400 0177   x17
```

Run 1 (poke fixture) showed the same words, except `sp+8..9` = `3e00` instead of `4446`.

| offset | word | resolves to (sym + ROM bytes) |
|---|---|---|
| sp+0 | $0DAB | `DelayFrame+3`: return from `call SlinkDelayFrameBridge` (cd 70 00) |
| sp+2 | $51C2 (bank $25) | `NextOverworldFrame.gfx_done+6`: return from **`call z, DelayFrame`** (`cc a8 0d` at 25:51BF) |
| sp+4..11 | `00fe/20fe`, `86d6`, `3e00/4446`, `22d1` | pushed registers. **sp+4 (F) and sp+8..9 vary**: not usable in a fingerprint |
| sp+12 | $516B (bank $25) | `HandleMap+0x15`: return from `call NextOverworldFrame` (cd 85 51) |
| sp+14 | $50E2 (bank $25) | `OverworldLoop.loop+9` |
| sp+16 | $0014 | `FarCall+4` (rst FarCall frame) |

So Polished's idle-overworld chain is **OverworldLoop -> HandleMap -> NextOverworldFrame -> DelayFrame ->
bridge**. There is no `DelayFrames` link. This corrects the TRADE.md 8.3 scan, which looked only for `call DelayFrames` (`cd a1 0d`): `NextOverworldFrame`
does reach `DelayFrame` directly through a `call z`. The vanilla `sp+12/14/16` constants cannot carry over. The
bytes above are what a `SlinkTradeDispatch`-equivalent would see one call deeper, offset by its own return address.

## Commands

```
python tools/polished_live/harness.py setup                      # POL_KIND=overlay|clean, POL_POSMODE=warp|poke
python tools/polished_live/harness.py control                    # no client: header + screenshot + encounter
POL_STAGES=1234 python tools/polished_live/harness.py live       # POL_FIXTURE=<SaveRAM> to boot another save
```

---

# Run 2 (2026-10-04, later): hello gate, census rescan, receptionist stack, Pokegear strip

These are DEV results, not PHYSICAL receipts. The ROM and the emulator are the same as in run 1: the overlay sha1
`29ea04c2...` is checked before every launch. The fixture is run 1's `polished_overlay_warp.SaveRAM` (sha256
`75c7a5dc...`), copied into the new lane `F:/slink-work/lanes/pol-live2/`. The client code is this tree at
`78dddd8b`, which includes the hello gate (`read_overworld_gate`) and `c442a248` (census rescan, `supports_box_mon`).

2026-10-11: supports_box_mon retired by the review fixes; box_mon is always queued.

| run | what | EmuHawk PID | server PID | evidence |
|---|---|---|---|---|
| A1 | `POL_STAGES=123 harness.py live` | 53384 | 51724 | `runA1/`, `runA1.out` |
| A2 | the same, after a driver fix (see Stage A) | 49088 | 45044 | `live/`, `runA2.out` |
| B | `POL_EXPLORE=B harness.py explore` (no client, no server) | 50920 | none | `x/explore_B/` (`resolved.txt`), `runB1.out` |
| C | `POL_EXPLORE=C harness.py explore` (no client, no server) | 55084 | none | `x/explore_C/` (`pokegear.json`, `pokegear_card0..3.png`), `runC1.out` |

Every EmuHawk was killed by its own recorded PID, and at most two ran at once (A2 overlapped B, then C). Every
PID was checked as gone afterwards. New driver pieces:
- `live.lua`: gate-byte transition log and the Stage 3 census evidence.
- `explore.lua`: Stages B and C.
- `harness.py`: an `explore` command and `resolve()`. `resolve()` names each stack word from the sym and checks
  that the ROM bytes before it are a `call`/`rst`.

## STAGE A: hello gate and box-census rescan. PASS (A2: 0 failed checks)

**Hello timing.** Identical in both runs. `gate_transitions.log` is written only when one of `wMapStatus`,
`wScriptRunning`, `wGameLogicPaused`, `wLinkMode` or `wBattleMode` changes.

| frame | event |
|---|---|
| 2 .. 335 | `wMapStatus=0`, running=false (title, main menu, CONTINUE) |
| 208 / 220 | `TitleScreenMain` / `MainMenu` (last hit) |
| 335 | first `OWPlayerInput` exec hit |
| 336 | end of frame: `wMapStatus=2`, script 0, pause 0, link 0. `read_overworld_gate().running` first reads true |
| **343** | **hello on the wire** (gate plus the 8 stable polls of `hello_unheld`) |

The hello now leaves after the first idle overworld frame instead of at frame 286 from the CONTINUE screen
(run 1, S1-a), so S1-a is fixed. Its content matches run 1: party 5, `pc_boxes []`, generation 2, `route_29`,
OT 53698.

**Box census after a native box catch, without a reboot.** The party was full (5 SYNTH mons plus the Pidgey
caught in Stage 2), so the catch ran `PokeBallEffect.SendToPC` and hit the capture site 0 times.

| run | `wBattleMode` 1 -> 0 (end of frame) | first tick carrying the box mon | delay |
|---|---|---|---|
| A2 | 7609 | 7620: generation 7, `pc_boxes` 1 (`box 0 slot 0`, Pidgey, species 16) | +11 frames |
| A1 | 7257 | 7320: generation 7, `pc_boxes` 1 | +63 frames (`wMapStatus` was 1, the map reload, until 7293) |

- The other battle ends re-armed the census the same way:
  - A1: the escape battle cleared at 1460, census at 1500; the party catch cleared at 3274, census at 3330.
  - A2: the party catch cleared at 4282 (map reload until 4318), census at 4320.
- The 1800-frame periodic rescan also fired as designed:
  - A1: 360 -> 5130 -> 6930 -> 9120.
  - A2: 1470 -> 3270.
- The server's `/api/status` `players.a.pc_boxes` carried the box Pidgey (A1 last snapshot), so run 1's FAIL
  (census frozen until the next hello) is fixed.
- Driver note: A1 logged one FAIL. My Stage 3 check started counting census generations *after* the battle had
  already ended, so it skipped the box-carrying one (gen 7 at 7320) and measured the next periodic one (+1826).
  This was fixed in `live.lua` (the mark is now taken before the catch), and A2 passed the same check at -26
  frames from the first overworld input (+11 from the battle-mode clear).

**Server log for the solo catch.** A1 and A2 show the same thing.

```
[a] capture key=0D2D60:D1C2:010:.. lv=3 area='route_29'
[PENDING] route_29 player=a action=add ...
[a] skip quarantine: 0D2D60:D (client has no box executor)
```

There are no `box_mon` commands (`recv.jsonl`: every reply after the first config batch is `noop`), no
`box_mon_failed`, and no `unknown event`. Run 1's S2-a round trip is gone.

The rest matches run 1:
- Capture site: 1 hit in bank 3 at PC $652B, SP $C0CF; `wPartyCount` changed in the same frame; `capture` was
  emitted on hit +1.
- Escape and failed throws: 0 hits.
- The native save held `wGameLogicPaused` for 62 frames.
- 0 Lua-originated writes, 0 panel write attempts.

SYNTH in Stage A: none beyond the run 1 fixture.

## STAGE B: trade receptionist stack. MEASURED (read-only), up to the native link wait

**Facts from the source.**
- `POKECENTER_2F` is map group 20 #1 (`constants/map_constants.asm:431-432`, 8x4).
- The trade receptionist is `object_event 5, 2, ... LinkReceptionistScript_Trade` (`maps/PokeCenter2F.asm:21`).
- The player's talking square is (5,3), where `Script_LeftCableTradeCenter` walks the player.
- The script's first command is `checkevent EVENT_GAVE_MYSTERY_EGG_TO_ELM`. The ROM at 24:7601 reads
  `33 21 00 d2 ...`, so the flag is event $21 = 33, which is `wEventFlags` byte 4 bit 1.

**SYNTH (disclosed).**
- `wEventFlags+4` $00 -> $02, which sets EVENT_GAVE_MYSTERY_EGG_TO_ELM. Without it the receptionist only says
  `Script_TradeCenterClosed`.
- The engine warp to 20:1 (5,3) with the `Script_warp` bytes (`wDefaultSpawnpoint` $FF, `hMapEntryMethod` $F1,
  `wMapStatus` 1). MAPSETUP_WARP landed natively at frame 477 with tileset 14 and an 8x4 header.

Everything after that was native input: Up to face the receptionist, then A presses. A answered YES at the
trade prompt; no other button was used.

| frame | milestone |
|---|---|
| 819 | `FixPlayerEVsAndStats`: the script got past `checkevent` |
| 1003 | `YesNoBox` (trade prompt), answered YES |
| 1024 | `CheckPartyForMail` (no mail) -> `Special_SetBitsForLinkTradeRequest` -> "Please wait." |
| 1039 -> 1552 | `Special_WaitForLinkedFriend`: 513 frames of serial polling, then `.done` (timeout, no cable) |
| after | `.FriendNotReady` text, then `endtext`; overworld again. `Special_TryQuickSave` never ran (no save was written) |

The native link-trade entry is as far as a cartridge with no partner can go: `WaitForLinkedFriend` is the
first link-dependent step, and `TryQuickSave` (TRADE.md's branch point at `:99-101`) comes after it.

**Stack at `SlinkDelayFrameBridge` entry, per phase** (at most 300 samples per phase; raw `sp+0..31` in
`x/explore_B/stacks.json`; names from `resolve()`):

1. **Idle Pokemon Center overworld** (the known-positive control, 300 samples). SP $C0DE, hROMBank $25. This is
   byte-for-byte run 1's Route 29 chain: `0DAB DelayFrame+3` / `25:51C2 NextOverworldFrame.gfx_done+6` / pushed
   registers / `25:516B HandleMap+0x15` / `25:50E2 OverworldLoop.loop+9` / `0014 FarCall+4`. Only sp+4 (F)
   varies. The probe still reads what run 1 read.
2. **Native link wait** (`Special_WaitForLinkedFriend`, 300 samples, 3 distinct). SP $C0DC, hROMBank $0A,
   SVBK 1. **Only sp+2 varies.**

   | offset | word | resolves to |
   |---|---|---|
   | sp+0 | $0DAB | `DelayFrame+3` (return from `call SlinkDelayFrameBridge`) |
   | sp+2 | $4D5F (x298) / $4D1D / $4D20 | `0A: Special_WaitForLinkedFriend.not_done+0xf` (the loop's `call DelayFrame`); the two singles are the lead-in `call DelayFrame`s |
   | sp+4 | $2698 | `_ReturnFarCall` (the script `special` reaches bank $0A through FarCall) |
   | sp+6..11 | `1403 2500 000F` | FarCall-saved registers (`2500` carries the caller bank $25) |
   | sp+12 | $62B5 (bank $25) | `ScriptEvents.loop+9`: return from the `wScriptMode` dispatch call (`cd 00 63`, the mode jumptable that reaches `RunScriptCommand`) |
   | sp+14 | $515F (bank $25) | `HandleMap+9`: return from `call MapEvents` (`cd 77 51`). `MapEvents` tail-jumps (`jmp ScriptEvents`, `events.asm:112`), so it leaves no frame |
   | sp+16 | $50E2 (bank $25) | `OverworldLoop.loop+9` |
   | sp+18 | $0014 | `FarCall+4` |

   Bytes: `ab0d 5f4d 9826 0314 0025 0f00 b562 5f51 e250 1400 018a 6114 0012 0f00 5843 1400`.
3. **YES/NO prompt** (21 samples, 6 distinct; the main one is x15). SP $C0DC, hROMBank $25:
   `0DAB` / `0DA4 SFXDelayFrames+3` / `1947 HandleYesNoMenu+0x23` / `0120` / `18EC PlaceYesNoBox+6` /
   `25:65E5 Script_yesorno+3` / `25:62B5 ScriptEvents.loop+9` / `25:515F HandleMap+9` /
   `25:50E2 OverworldLoop.loop+9` / `0014`.
4. **Talk start** (A press, 6 samples). SP $C0D6:
   `0DAB` / `0DA4 SFXDelayFrames+3` / `25:53E9 PlayTalkObject+0xe` / `D69F` / `25:540F TryObjectEvent+0x24` / ... /
   `25:53D0 CheckAPressOW+8` / `25:53B7 OWPlayerInput+0x10` / `25:5238 PlayerEvents+0x22` /
   `25:517F MapEvents+8` / `25:515F HandleMap+9` / `25:50E2` / `0014`.
5. **Text printing inside the script** ("script" and "after_wait", 249 samples). 167 distinct stacks, SP from
   $C0C2 to $C0DE across banks $01/$05/$09/$0A/$21/$24/$25. DelayFrame is reached through `PrintLetterDelay`,
   `PromptText`/`ButtonSound`, `SafeUpdateSprites`, `_SafeCopyTilemapAtOnce` and others. **Not
   fingerprintable.**

**What this means for the trade hook (input for TRADE.md 8.3).**
- Inside the receptionist script, every frame wait sits under the same bank-$25 tail:
  `ScriptEvents.loop+9 ($62B5) -> HandleMap+9 ($515F) -> OverworldLoop.loop+9 ($50E2) -> FarCall+4 ($0014)`.
- The idle overworld instead has `HandleMap+0x15 ($516B)` at the same depth.
- Above that tail, the frames depend on which special or text routine is waiting. Only the
  `WaitForLinkedFriend` loop is stable: 1 of 32 bytes varies.
- A dispatch that runs from a script `special`/`callasm` would see `_ReturnFarCall` plus the `$62B5/$515F/$50E2`
  tail at a fixed offset under its own frame. That is measurable. The idle-overworld constants
  (`$516B` at sp+12) do not hold anywhere inside the script.

## STAGE C: Pokegear icon strip. MEASURED (read-only)

**SYNTH (disclosed):** `wPokegearFlags` $00 -> $87 (POKEGEAR_OBTAINED_F plus the MAP, RADIO and PHONE card bits).
The EXPN bit 3 was left clear. Everything else was native:
- START menu items `1,2,7,3,4,5,6`, with POKEGEAR (id 7) on row 3; `PokeGear` ran at frame 523.
- Right presses moved card to card. Each card was read in its joypad state (`wJumptableIndex` 01 / 04 / 0A / 0E)
  after a 90-frame dwell.

Rows 0-1 of `wTilemap` (`00:C1A0`) for each card. Columns 0-7 are identical in all four:

```
row 0  cols 0-7:  56 57 | 50 51 | 54 55 | 52 53      (Pokegear, Map, Phone, Radio top halves)
row 1  cols 0-7:  66 67 | 60 61 | 64 65 | 62 63      (lower halves = top + $10, the .PlacePokegearCardIcon shape)
```

| card | `wJumptableIndex` | row 0 cols 8-19 | row 1 cols 8-19 | attr rows 0-1 (cols 8-19) | cursor (`wSpriteAnim1` XCOORD + XOFFSET) |
|---|---|---|---|---|---|
| 0 clock | $01 | `f7 f7 f7 f7 40 7f..7f 41` | `f7 f7 f7 f7 7f 92 b6 a8 b3 a2 a7 f0` (" Switch>") | all 0 | $10 + $00 = 16 |
| 1 map | $04 | `44 91 ae b4 b3 a4 7f e2 e9 7f 7f 7f` (landmark name) | `7f` x12 | all 0 | $10 + $10 = 32 |
| 2 phone | $0A | `06 07 07 .. 07 17` (card frame) | `16 f7 .. f7 68 69 16` (signal bars $68/$69) | pal 1 from col 8 | $10 + $20 = 48 |
| 3 radio | $0E | `06 07 07 .. 07 17` | `16 f7 f7 6c f7 6d 6e f7 6f f7 f7 16` | pal 1 from col 8 | $10 + $30 = 64 |

The arrow's XOFFSET is `card << 4` (`AnimatePokegearModeIndicatorArrow`), so a card 4 lands at 64 + 16 = 80.
**Columns 8-9 of rows 0-1 are drawn by every native card:** the clock border ($f7), the map's landmark name
(starting at column 8), and the phone and radio card frames ($06/$07/$16 with palette 1). A fifth icon at
column 8 therefore collides with each card's own tilemap. The coordinator's `ld bc,$8` note understates this:
widening the black bar alone is not enough, because the card layouts start at column 8. The screenshots
(`pokegear_card2.png`: the phone frame's top-left corner sits right after the Radio icon) only confirm this.

**Tile ids in $50-$7F.** Usage is the union of each card's full 360-byte tilemap, sampled 4 times per card.
VRAM is the bank 0 tile data at `vTiles2 + id*16`, dumped in the radio state.

| ids | in a tilemap | VRAM content |
|---|---|---|
| $50-$57 | yes (icons) | Pokegear gfx |
| **$58** | **yes** (radio card) | Pokegear gfx |
| **$59** | no | inked Pokegear gfx |
| $5A | yes | |
| $5B | no | inked Pokegear gfx |
| $5C-$75 | yes ($60-$67 icon lower halves, **$68-$6B phone signal bars**, $6C-$75 radio) | |
| **$76-$7E** | no | **not Pokegear gfx at all**: the built `gfx/pokegear/pokegear.2bpp` is 608 bytes = 38 tiles ($50-$75), because the Makefile passes `--trim-whitespace` (`Makefile:228`). These ids keep whatever the previous screen left in VRAM (non-blank, map-dependent) |
| $7F | yes (blank fill) | blank |

VRAM bank 1 tiles $50-$7F read all zero (`Pokegear_LoadGFX` calls `ClearVBank1`). No attribute in rows 0-1 sets
the bank bit.

**Verdict on $58/$59: NOT usable as an icon pair.**
- $58 is drawn by the radio card.
- `.PlacePokegearCardIcon` puts the lower half at top + $10, so a $58 icon's lower pair would be $68/$69, the
  phone signal bars (`.PlacePhoneBars`, seen live in the phone card's row 1).
- No pair of adjacent ids in $50-$75 has both itself and its +$10 pair free.

The free ids are $59 and $5B (inked but unreferenced, 2 tiles, not adjacent) and $76-$7E (9 ids, not written by
the Pokegear load, so an SLink icon would need its own 4-tile load into them). With the native placement shape,
lower halves at $86+ would fall in vTiles1 (font). So a fifth icon needs one of the following: its own tile load
plus a placement routine that is not top+$10, or the VRAM-bank-1 route (the bank is empty during the Pokegear;
the icon tiles would go in bank 1 and the attribute bank bit would be set). That is a design call for
POKEGEAR_SLOT.md. Not checked: the Kanto and Orange map views, radio tuning redraws, and bank 1 content in
states other than radio.

## Run 2 commands

```
POL_LANE=F:/slink-work/lanes/pol-live2 POL_STAGES=123 python tools/polished_live/harness.py live
POL_LANE=F:/slink-work/lanes/pol-live2/x POL_EXPLORE=B python tools/polished_live/harness.py explore   # B or C
```

---

# Run 3 (2026-10-04, evening): a Manager-randomized cartridge, end to end

These are DEV results, not PHYSICAL receipts. The owner authorised live Polished randomization runs. The tree is
`e0fd92dd` (the Manager offers Polished randomization) plus this run's driver additions. The jar is the pinned forms
jar `F:/slink-work/cache/polished/jar/PokeRandoZX.jar` (sha256 `f3a10dd7...`, `data/upr_jars.json`). Lane:
`F:/slink-work/lanes/pol-rand/`. Every EmuHawk was killed by its own recorded PID, at most two ran at once (only the
two setups overlapped), and every PID was checked as gone afterwards. Duplicate ROM copies and SaveRAM scratch dirs
were deleted after the runs. The Manager's own run directory and the fixtures were kept.

| run | what | EmuHawk PID | server / Manager PID | evidence |
|---|---|---|---|---|
| R1 | `manager_r1.py jar` (jar installed) | none | Manager 56412 | `r1_jar/` (`calls.jsonl`, `summary.json`, `mgr/run_20261004_211320/`) |
| R1 | `manager_r1.py nojar` / `oldjar` | none | Manager 49968 / 4744 | `r1_nojar/`, `r1_oldjar/` |
| setup | SYNTH fixture on ROM a, Route 29 / Route 30 | 49788 / 55244 | none | `s29/`, `s30/` |
| R2a | ROM a, Route 29: hello + 5 encounters | 15080 | 47864 | `live/r2a/` |
| R2b | ROM a, Route 30: hunt + catch the variant form | 56360 | 36624 | `live/r2b/` |
| R3 | clean overlay / swapped contract / flipped 0x14E / flipped $1F8028 | 53280 / 53708 / 51244 / 56920 | 31528 / 44856 / 54724 / 35744 | `r3/{clean,swap,flip14e,flip1f8028}/` |

New driver pieces:
- `manager_r1.py`: the real `python -m server.manager` on a private port and `--data-dir`, driven only over HTTP.
- `harness.py`: `POL_KIND=rand` (`POL_MGR_RUN`, `POL_PLAYER`, `POL_ROM`/`POL_ROM_SHA1`, `POL_SWAP`, `POL_POS`,
  `POL_RUNNAME`). The server's data dir is the Manager run's own `rom_contract.json` + `roms/a.gbc`, `roms/b.gbc`.
- `live.lua`: `POL_MAP`/`POL_HEADER`/`POL_WALK`/`POL_EXPECT_KIND`, plus stage 5 (fled encounters, read from
  `wEnemyMon*`) and stage 6 (hunt one species/form and catch it).

## STAGE R1: the Manager path. PASS

The Manager ran with `SLINK_UPR_JAR` = the pinned jar. Calls and responses, all over HTTP (`r1_jar/calls.jsonl`):

| call | result |
|---|---|
| `GET /api/roms` | lists `Polished Crystal 3.2.3 · clean dump` (`F:/slink-work/cache/polished/release`, clean true, family `gen2_polished`). The overlay is in no ROM dir, so the pick is the release with the companion on (the Manager's UX path) |
| `GET /api/randomizer/status?rom_a=&rom_b=` | `ok`, `jar_trusted`, `jar_fork`, `jar_polished` all true. `jar_entries` lists only the Gen 1 sections, no Polished one |
| `POST /api/runs/new {game: gen2_polished}` | `run_20261004_211320` |
| `GET /runs/{id}/randomizer` | 200 (`randomizer_page.html`) |
| `POST /api/runs/{id}/cartridges` | 200 in 1.24 s. Body: `companion: true, randomize: true` and spec wild random, starters random, statics random, trainers random + rival keeps starter, trades `given_and_requested` (forms are always in the pool) |
| `GET /api/runs/{id}/rom/a`, `/b` | 2 MiB each, sha1 `0d75313f20ec83ad1827b9cf66a6b7202793db86` / `21ed1c1cf86c14ec9f496ce119874ae9569b32fc` = the contract pins |

The cartridges response:
- `source_sha1 29ea04c2...` (the overlay: `cartridges.py` applied the UPS to the release first), `base_kind overlay`.
- `write_domain {changed 8629, ups_bytes 107}`.
- Seeds `4246104770686` / `178042112491888`.
- Summary: "wild encounters random, starters random, static encounters random, trainer teams random, rival keeps their
  starter, in-game trades random given and requested".

`rom_contract.json`: `upr_version 4.6.1-slink3`, `categories [starters, statics, trainers, wild]` (trades are not a
contract category; the UPR log shows the In-Game Trades section randomized), fingerprint `""`, per-player
`rom_sha1` + seed.

**Route 29 grass on ROM a** (`polished_rom_scan.Rom(..., pinned=False).wild()`, identical in morn/day/nite):
`170 L2, 208 L2, 156 L3, 252 L3, 124 L2, 204 L3, 53 form 2 L3` (Alolan Persian, effective species 305). The vanilla
overlay has `16, 161, 16, 161, 19, 187, 187` by day and `163, 19, ...` by night. Route 30 (26:1) on ROM a has the same
Alolan Persian in slot 2 (30 %).

**Without the jar** (no `SLINK_UPR_JAR`; `find_upr_jar()` finds nothing from this worktree):
- The picker is unchanged.
- The status shows `jar ""`, `jar_found false`, `ok false`.
- `POST cartridges` returns **400 `missing: jar`**.

**With an older trusted fork jar** (`.cache/slink-upr/PokeRandoZX.jar`, 0015, sha256 `db4bc65c...`):
- The status shows `jar_trusted`, `jar_fork` and **`ok` true, while `jar_polished` is false**.
- `POST cartridges` returns 400 with `POLISHED_JAR_REFUSAL` ("Polished Crystal randomization needs SLink's UPR fork
  jar with a Polished Crystal entry for this cartridge's header checksum ...").

**Status page text (`server/static/randomizer.js`):**
- Any fork jar is described as "SLink fork jar (vanilla + pureRGB + FireRed / LeafGreen + Emerald + Polished
  Crystal)" (`:117`). That includes the 0015 jar, which cannot randomize Polished.
- The randomize refusal line then says "Randomizing Polished Crystal needs the current SLink fork jar (patches
  0016-0021)." (`:377-379`, on `jar_polished`).
- Not rendered in a browser: these strings and the API fields are the evidence.

## STAGE R2: live boot of the randomized cartridge. PASS, with one presentation gap

**Entry and server.** The entry path is the real one: `lua/slink.lua` -> `gen2/entry.lua` -> `run.lua`. The server
is `python -m server.server` with `--data-dir` = a copy of the Manager run (`rom_contract.json` + `roms/a.gbc`,
`roms/b.gbc`), the shape the Manager launches. The harness checks the ROM's sha1 against the contract pin before
every launch.

**SYNTH (O-33, disclosed).** The run 1 recipe, run on ROM a itself (`harness.py setup`).
- NATIVE: cold boot, intro, START -> SAVE.
- SYNTH, as WRAM writes between those steps: the five-mon party (CROBAT 169, JOLTEON 135, GOLDUCK 55, NIDOKING 34,
  DODRIO 85, all existing species; base stats are not randomized by this spec), 99 Poke Balls, and the position by
  the engine warp bytes (`Script_warp`'s `wDefaultSpawnpoint $FF`, `hMapEntryMethod $F1`, `wMapStatus 1`).
- Fixtures:
  - `s29`: 24:3 (48,12). sha256 `94b394d1...`, checksum `$13E4`, the same as run 1's overlay fixture.
  - `s30`: 26:1 ROUTE_30 (11,48). sha256 `942c07f1...`. (11,48) is TALL_GRASS in `maps/Route30.ablk` x8..13 under
    `johto_traditional_collision.asm`, and no trainer's sight line reaches row 48 (`maps/Route30.asm`).
  - MAPSETUP_WARP landed natively in both, with tileset 1 and 30x9 / 13x27 headers.

**Admission.**
- Client log: `[SLink-gen2] polished_crystal/polished rand_overlay PRODUCTION (DEV_OVERLAY_SHA1) player a ->
  127.0.0.1:<port> (rom 0d75313f)`.
- Parts: kind `rand_overlay`, qualification `DEV_OVERLAY_SHA1`, `production_admitted false`, `runtime_rom_sha1` =
  the contract pin.
- Hello: `artifact_kind rand_overlay`, `rom_sha1 0d75313f...`, abi 3, party 5, area `route_29` (R2a) /
  `route_30` (R2b).
- Hello timing: frame 343, after the gate (first `OWPlayerInput` 335, gate running 336).
- Server: `admission: admitted — cartridge sha1 matches the contract`, `route polished_crystal -> gen2_polished
  (production)`, `using this cartridge's own encounter tables (8 areas)`.
- **0 Lua-originated writes** in both runs (`memory.write*` wrapped before `slink.lua`). One hook:
  `capture_party`.

**Adoption: Route 29, 5 encounters, all fled** (`live/r2a/encounters.jsonl`, species = `wEnemyMonSpecies` | bit 5 of
`wEnemyMonForm` << 3):

| # | species | form byte | level | ROM a slot |
|---|---|---|---|---|
| 1 | 170 | $01 | 2 | slot 1 (170 L2) |
| 2 | 170 | $01 | 2 | slot 1 |
| 3 | 170 | $81 | 2 | slot 1 |
| 4 | 170 | $81 | 2 | slot 1 |
| 5 | 156 | $01 | 3 | slot 3 (156 L3) |

None of these is a vanilla Route 29 species. The engine gives the table's NO_FORM (0) as PLAIN_FORM (1); $80 is the
gender bit. Capture-site hits: 0 for every fled battle. The server's `/api/status` `players.a.encounter_table`
(route_29) is ROM a's table, slot for slot, ending in `{"species_id": 53, "form": 2, "name": "Persian (Alolan)",
"effective_species_id": 305, "rate": 2}`.

**Variant form: Route 30** (`live/r2b`).
- The first encounter was **species 53, form byte $02, Lv3**: the Alolan Persian of ROM a's slot 2.
- One throw caught it.
- Capture site: **1 hit, bank $03, PC $652B, SP $C0CF**, `wPartyCount` already 6 at the hit.
- New party slot: personality byte 2 = $02 (the form), DVs `39068D`.
- Client `capture`: `{"key":"39068D:D1C2:035:02","species_id":53,"level":3,"area_id":"route_30","in_box":false,...}`.
  **The key carries the form** (`:02`).
- Server: `capture key=39068D:D1C2:035:02 lv=3 area='route_30'`, `[PENDING] route_30 player=a action=add ...
  species=53`, `skip quarantine (client has no box executor)`.
- `/api/status` `pending_captures.route_30.a.key` = that key.
- The server's Route 30 table reads `(97) Hypno, (53, 2) Persian (Alolan) eff 305, Heracross, Girafarig, Scizor,
  Ivysaur, Yanmega`.

**SUPERSEDED (2026-10-04) by `43ee19f0` "polished: regional forms judged as distinct mons end to end - the read
layer carries the effective species id".** The gap below is CLOSED and is kept as the historical record of it.

**GAP (product, not worked around -- CLOSED 2026-10-04 by `43ee19f0`): the caught variant is not presented as its
effective species.**
- `pending_captures` and `party_details` show `species_id 53`, `species_name "Persian"` and the Kanto Persian sprite.
- No field anywhere carries the form or the effective species 305. `effective_species` appears in no shared server
  file.
- Causes:
  - `server/server.py:2903` calls `adapter.species_name(sid)` without the `form` it reads on the line before.
    `Gen2PolishedAdapter.species_name(species_id, form=0)` would name it.
  - The client's capture and party details carry no `form` field: the form is only inside the key.
  - **CLOSED:** the read layer now resolves the variant's record index and puts it on the wire, so `species_id`
    arriving at the server IS the effective id (`lua/gen2/polished.lua:199-206, 222-223, 321, 380`), and
    `server/server.py` now threads `form` into `_get_sprite_html` (the `43ee19f0` two-line change). The residual
    `adapter.species_name(sid)` below no longer drops the form, because `sid` is already effective; it is still
    fragile and would break if a raw id ever reached it. Proving test: `tests/unit/test_polished_effective_wire.py`.
- What this means: wherever the shared state goes by `species_id`, an Alolan Persian counts as a Persian. The owner's
  ruling is that a variant form is a different mon.

**Minor.** The `(8 areas)` in `server.py`'s adoption log line counts the 8 top-level keys of `scan_randomized`
(title, wild, fishing, ...), not areas.

## STAGE R3: refusals. PASS

All four use stage 1 only; the fixture is run 1's overlay fixture (clean) or `s29` (the rest).

| case | client | server |
|---|---|---|
| clean overlay `29ea04c2`, overlay contract | `overlay PRODUCTION (DEV_OVERLAY_SHA1) ... (rom 29ea04c2)`, hello at 343 | `admission: admitted — cartridge sha1 matches the contract` |
| ROM a with the pair's pins swapped (`POL_SWAP=1`) | admits `rand_overlay` and sends its hello (the client cannot know the contract) | **`admission: rejected — this is not the ROM built for player a (sha1 0d75313f20ec, expected 21ed1c1cf86c)`**; every reply is `{"cmd": "noop", "refused": "admission"}` |
| ROM a, byte 0x14E flipped ($72 -> $73), sha1 `87f75ccd...` | **`polished cartridge refused (production admission): unknown artifact SHA-1 87f75ccd...: its Polished SLink companion overlay is modified or incomplete (overlay beacon mismatch); prepare it through the Manager or /patcher`**; no client, no connection | no hello |
| ROM a, byte $1F8028 flipped ($C6 -> $C7), sha1 `26d62ad1...` | the same refusal, `overlay beacon mismatch` | no hello |

The flipped ROMs were temp copies under `r3/` and were deleted after the runs. The Manager's `roms/a.gbc` was never
touched.

## Run 3 commands

```
python tools/polished_live/manager_r1.py [jar|nojar|oldjar]
POL_KIND=rand POL_MGR_RUN=<lane>/r1_jar/mgr/<run> POL_LANE=<lane>/s30 POL_POS=26,1,11,48 python tools/polished_live/harness.py setup
POL_KIND=rand POL_MGR_RUN=... POL_FIXTURE=<fixture> POL_RUNNAME=r2b POL_EXPECT_KIND=rand_overlay POL_STAGES=16 \
  POL_MAP=26,1 POL_HEADER=1,13,27 POL_WALK=9,12 POL_TARGET=53,2 python tools/polished_live/harness.py live
# R3: POL_SWAP=1 (swapped pins) | POL_ROM=<flipped copy> POL_ROM_SHA1=<its sha1> (client refusal)
```

## Phone card SLink contact, Stage 1 (2026-10-04, DEV evidence). PASS

Overlay `34942315bb3e62189a56dabbcb9cef6dd3e9a9f5` (`docs/polished/PHONE_SLOT.md` "Stage 1 implementation"). The harness's
`stage_rom` checks it against `overlay_provenance.json` before every launch. Lane `F:/slink-work/lanes/pol-phone`. The
fixture is run 1's `polished_overlay_warp.SaveRAM` (sha256 `75c7a5dc...36b8`, on ROUTE_29, reached by the engine's own
warp when it was built), booted with CONTINUE (native).

**Phone run** (EmuHawk PID 49748, driver `lanes/pol-phone/drv/phone.lua` through the harness's own `launch`; 75 checks,
0 failed). SYNTH, each logged in `phone/result.txt`: `wPokegearFlags` $00 -> $87, and `wPhoneList` set per scenario
while in the overworld. Everything else is scripted native input: START -> POKEGEAR -> Right to the Phone card.

| scenario | `wPhoneList` | rows read from `wTilemap` | SLink entry |
|---|---|---|---|
| s0, 0 native | `0000000000` | `SLink:` / `   Soul Link`, then the native `----------` filler | ok |
| s1, Mom | `0100000000` | Mom, SLink | ok |
| s4, Mom/Elm/Joey/Wade | `09c0000000` | 4 natives. The 5th Down scrolls natively (cursor 3, scroll 1): Elm, Joey, Wade, SLink | ok, cursor/scroll 3/1 kept |
| s3, Mom/Elm/Joey | `0940000000` | Mom, Elm, Joey, SLink | ok, then delete + native call |

Each entry check: A on the row opens `PokegearPhoneContactSubmenu` with **Call/Cancel only (no Delete)**. Call reaches
`SlinkPhone_CallGate`, and the text box reads `SLink is linked.`. `MakePhoneCallFromPokegear` is **not** entered,
`wCurCaller` is untouched, `wPokegearPhoneSelectedPerson` = 38 (the virtual id) and `wPhoneList` is unchanged. B returns
to state $0A with the native "Whom do you want to call?", the cursor/scroll are unchanged and `▶` sits on the SLink row.
s3 then deletes Joey natively: the submenu offers Delete, the YES/NO prompt appears, YES gives `wPhoneList`
`0900000000`, and the list redraws as Mom, Elm, SLink, with exactly one SLink row and `wNumSetBits` 3. A real call to
Mom still enters `MakePhoneCallFromPokegear` (`wCurCaller` 1) and hangs up back to the Phone card. Screenshots
`phone/s*_*.png`; tilemaps and hits are in `phone/phone.json`.

**Existing stages on the new overlay** (`harness.py live`, stages 1234, EmuHawk PID 52952, server PID 31112; 0 checks
failed): admitted as `DEV_OVERLAY_SHA1`. The hello leaves at frame 343, after the first idle overworld frame (335) and the
gate (336), never from the main menu. The capture site fired exactly once on the party catch (bank 3), the box catch
reached the PC without firing it, the box census followed the battle end (-24 frames), and there were no Lua-originated
memory writes. Output: `pol-phone/live_stageA/`, `pol-phone/live_stageA.out`.

Not proven live: the entry text is fixed (no host/link status exists in the core mailbox); an incoming call ringing while
the Pokegear is open was not exercised; only English text and the CGB palette were seen. The first phone run (same
build) failed one driver expectation only. It expected an SLink row on s4's first screen, but the 5th row is below the
fold until the scroll. The driver was corrected; the ROM was not changed.

## Second trainer identity derived from the duo fixture (2026-10-06, SYNTH)

`tools/polished_live/derive_save.py` builds the B side's SaveRAM from the existing duo fixture
(`lanes/g2int-live/pol/fixture/polished_overlay_warp.SaveRAM`, sha256 `75c7a5dc...36b8`, itself a disclosed SYNTH
setup fixture under O-33). **SYNTH identity derivative of an existing SYNTH setup fixture (O-33); not independently
played; native Continue/save/reload evaluated separately.** The builder is pure (bytes in, bytes out) and refuses, before
any change, a wrong size, marker, version or save phase, a party count outside 1..6, a bad checksum in either copy, a
non-empty newbox `Entries` array in either copy, and an empty, over-long (more than 7 glyphs) or unencodable name.

Changes, in the main (`0x2008`) and backup (`0x1208`) copies alike: the player ID (`53698` -> `53699` by default), the
first 8 name bytes (`Aaaaaaa` -> `Bbbbbbb`), and for every occupied party slot the OT ID and the first 8 OT-name bytes.
Then both checksums are recomputed (`13e4` -> `1414`). Nicknames, held items, the 3 EXTRA OT bytes (hyper training),
the box area, version, phase and the 22-byte RTC trailer are byte-identical. With the default arguments exactly **100**
bytes differ (per copy: ID 1, name 7, 5 slots x (OT ID 1 + OT name 7), checksum 2). The default output sha256 is
`f5f19f5d...ca8d`; `held_item_variant` (separate, disclosed, never applied by default; CLI `--item SLOT:ITEMHEX`)
changes one party held-item byte in both copies and re-seals.

`verify_derived(original, derived)` reads both images through `polished_codec` and returns problems: checksums,
markers/version/phase/trailer, only the declared offsets differ, player ID/name and every mon's OT ID/name changed as
intended and equal to the player's, the EXTRA bytes kept, species/DVs/level/nickname unchanged, and the SLink mon KEYS
(which include the OT ID) disjoint between the two images. The repo's own Lua census (`P.census`: sSaveVersion +
sChecksum) admits the derived CartRAM as a complete empty census; a derivative with one stale checksum is refused by it.
Evidence: `tests/unit/test_polished_save_identity.py` (red controls for every precondition and four builder mutations).

Not proven: that the native Continue accepts the derived save, that a reload keeps it, and any live duo behaviour with
two distinct identities. The Polished `sBackupCheckValue*` markers and `sPlayerData` fields outside the declared offsets
are carried over unchanged by construction, not independently inspected for identity-bearing data.

### Coordinator live check of the derived second identity (2026-10-06)
The derived save `F:/slink-work/lanes/pol-ident/B.SaveRAM` (sha256 `f5f19f5d2e3e26e343214722b5116e1aac0c71a1200882a4cc6f58259bc6ca8d`, from `tools/polished_live/derive_save.py`, 100 bytes different, both checksums `0x1414`) was booted through the engine's real CONTINUE by the existing writes smoke (`writes_run.py`, only its fixture-hash pin overridden from the calling script): `RESULT: PASS pol-live-writes (a)+(b) complete (0 checks failed)` on overlay `97628616` (EmuHawk pid 33788, lane `F:/slink-work/lanes/pol-ident-live`). The client's party keys carry the NEW OT id: `CFFFFF:D1C3:037:40`, `BFFFFF:D1C3:022:00` (the original fixture's are `...:D1C2:...`), so the game loaded the derived identity and the key rule separates the two saves. SYNTH derivative of a SYNTH fixture (O-33), not independently played; two-identity duo behaviour and native save/reload of the derived save remain unproven.

## D3 dispatcher measurement, first live run (2026-10-06, SYNTH fixture, DEV)

`tools/polished_live/dispatch_probe.py` (read-only: 0 guest writes, 0 register changes, 0 prompt-entry hits) on overlay `aecedbb2` (provenance output sha1), fixture `polished_overlay_warp.SaveRAM` (sha256 `75c7a5dc...36b8`, the disclosed O-33 SYNTH setup fixture), route `tools/polished_live/routes/dispatch_d3_route.json` (native buttons only, 1498 frames from boot, `--frames 1600`). Geometry came from the pinned source (`maps/Route29.asm`, `Route29.ablk`, `johto_traditional_collision.asm`), checked by one read-only exploration replay (position/script-mode log only). Route: 20 A pulses to CONTINUE; idle 300; walk Up 4 steps (48,12)->(48,8); walk Right 3 steps ->(51,8); START menu open (100 frames) then closed with B pulses; face the Route 29 sign at (51,7) and read it (two text pages). **The "npc_talk" phase is a sign text box (`bg_event` jumptext), not a person**: no NPC lies within ~20 steps of (48,12) (nearest: Fisher at (25,3), about 32 steps).

Verdict: **PASS** (0 reasons). EmuHawk pid 34768, ended. Lane `F:/slink-work/lanes/pol-dispatch-probe/played-tjeu2gx4/probe`. 1551 dispatcher entries, 0 prompt entries.

| phase | frames | entries | stack pins matched | engine guards clean | context_accept |
|---|---|---|---|---|---|
| idle | 360 | 360 | 360 / 360 | 330 | 330 |
| walking | 96 | 96 | 96 / 96 | 2 | 2 |
| start_menu | 102 | 101 | 0 | 0 | 0 |
| npc_talk (sign) | 204 | 204 | 0 | 0 | 0 |

Idle and walking: every entry carried the nine pinned bytes (SP `$C0D2`; 15 distinct 28-byte images in idle, 17 in walking; only the non-pinned bytes vary). The commonest idle image (302 of 360) is `4c408300802522d186d68000ab0dc25100fe86d6444622d16b51e250`. Engine guards, not the stack, refused most walking entries: 75 had `wMapEventStatus` != 0 with the step flag set, 7 the step flag only, 12 `wScriptMode` = 3 (the first frame of a step); the 30 unclean idle entries are the frames just after a step with the step flag still set. Negatives: START menu and sign text both ran with `wScriptMode` = 1 (so the engine guard alone refuses), and the stack differs as well: SP `$C0B8`-`$C0D2` with pinned offsets 14, 15 and 24 always mismatched (5, 25, 26, 27 in most). Not measured: battle phase, any person NPC, the accept path (lease never published). SYNTH-fixture DEV evidence, not PHYSICAL.

## F3 natural-faint probe, first live run (2026-10-06, SYNTH fixture, DEV)

`tools/polished_live/faint_probe.py` (read-only recorder; branch `claude/pol-f3` merged with `claude/gen2-integration` at `f7ceb2c04`, 78 unit tests green) on overlay `aecedbb2` (provenance output sha1), fixture `polished_overlay_warp.SaveRAM` (sha256 `75c7a5dc...36b8`, the disclosed O-33 SYNTH setup fixture, Route 29 at (48,12)). The fixture is not committed under `tests/fixtures/`; the copy used is `F:/slink-work/lanes/pol-live/fixture/`. The probe insists on exactly 32768 bytes and the fixture is 32790 (a 22-byte RTC footer), so the lane copy was truncated to its first 32768 bytes (the RTC footer is dropped; the save area is unchanged).

Route `tools/polished_live/routes/faint_f3_route.json` (1931 frames, native buttons from boot, no warp, trainer or story staging): 20 A pulses to CONTINUE, settle 150 frames, then Left/Right 56-frame holds along Route 29 row 12 (collision rows from `maps/Route29.ablk`; grass patch x44..53, y10..15, 10 percent per step) until a wild battle starts, then 70 A pulses (2 frames down, 14 up: FIGHT, first move, text) and 120 idle frames. One read-only exploration replay (own Lua, logs map/x/y/`wBattleMode` transitions only, not committed) found the wild battle: stepping onto (44,12) at route frame 541, `wBattleMode` = 1 at route frame 691 (Pidgey), so the route's walking prefix is cut at frame 691. The same replay twice more reproduced it frame for frame (deterministic).

**Stock probe run: FAIL, a probe defect, not a finding.** EmuHawk pid 30752:

```
RESULT: FAIL faint-probe (played; owned PID 30752)
FAIL: missing unique successful recording RESULT
Expecting value: line 1 column 1 (char 0)
```

`trace.json` was left empty and EmuHawk stayed open until killed (own PID, `taskkill /T /F /PID`). Cause (from a lane-local copy of `faint_probe.lua` that logs the encoder reason): `L.json.encode(trace)` returned `nil, "JSON structure exceeds bounds"` because the recording held 6668 rows (default limit 100000 items), and `f:write(nil)` then raised. Almost all rows are bank-mismatched hits: `event.on_bus_exec` on the System Bus fires for whichever ROM bank is mapped at that address (copy_return 6365 hits, only 1 in bank `$0F`). The committed `faint_probe.lua` was NOT edited.

**Measured run (lane-local copy of `faint_probe.lua`, differing only by one diagnostic log line and `encode(trace, {bytes=64 MiB, items=5e6})`; everything else, the ROM, route, contract and `evaluate()`, is the committed code).** EmuHawk pid 27868, ended rc 0, 0 guest writes, 0 register changes, 0 overflows, 0 driver errors, lane `F:/slink-work/lanes/pol-f3run/live/played-_vtye_ky`. `evaluate()` verdict **PASS**, one reason: `complete recording: 1 qualified pre-copy pass(es); not animation qualification`. (The wrapper printed `RESULT: PASS`; `main()`'s own line would read `RESULT: PASS faint-probe (played; owned PID 27868)`.)

| site | address | hits total | bank `$0F` and PC = site (qualified) |
|---|---|---|---|
| resolve (ResolveFaints) | 44af | 17 | 1 |
| pre_copy | 44c8 | 173 | 1 |
| copy_call | 44ca | 1 | 1 |
| copy_return | 44cd | 6365 | 1 |
| faint (FaintUserPokemon) | 4cd2 | 2 | 2 |
| lost (LostBattle) | 4ff6 | 108 | 0 |

In every one of the 6666 rows the callback PC equalled the hook address (no WRONG_PC); the unqualified rows are other banks executing at that address (`$3A`: copy_return 6363, pre_copy 156; `$21`: resolve 16, pre_copy 16; `$14`: lost 100; `$23`: lost 8; `$34`: copy_return 1). LostBattle never ran in bank `$0F`.

The one qualified pass (trace frames; `emu.framecount`), all rows bank `$0F`, `wBattleMode` 1, party 5, slot 0, player HP 159 (`$009F`), party HP 159, status 0, FAINTED bit clear, `wWhichMonFaintedFirst` = 2:

| ord | frame | site | PC | SP | hBattleTurn |
|---|---|---|---|---|---|
| 4422 | 1390 | resolve | 44af | c0db | 0 |
| 4423 | 1390 | faint | 4cd2 | c0d7 | 1 |
| 4756 | 1460 | faint | 4cd2 | c0d7 | 0 |
| 4757 | 1460 | pre_copy | 44c8 | c0db | 1 |
| 4758 | 1460 | copy_call | 44ca | c0db | 0 |
| 4759 | 1460 | copy_return | 44cd | c0db | 0 |

Order resolve, faint, faint, pre_copy, copy_call, copy_return: the interval the oracle requires, with no ORDER_VIOLATION. The `faint` site is the entry of FaintUserPokemon (`call HasUserFainted; ret nz`), which runs whether or not the user fainted, so a hit there is not itself a faint. Context from the exploration replay (route frames, same inputs): enemy HP 16 at frame 692 (wild Pidgey Lv3), 0 at frame 1252, battle over (`wBattleMode` 0) at frame 1533 with the player back on the overworld at (44,12). So this was the **enemy-side faint** (the first `faint` hit has `hBattleTurn` = 1, the wild mon as the user); the player's mon never fainted (pre-copy `wBattleMonHP` 159, FAINTED clear) and no whiteout, so the player-side `FaintUserPokemon` body and `FAINT_NOT_OBSERVED_AT_44CD` (which needs a pre-copy HP of 0) are not exercised. PASS certifies a complete recording with a qualified 44c8 pre-copy hit, not a faint writer qualification.

Probe defects found: (1) the 32768-byte fixture check rejects the 32790-byte native fixture; (2) the default JSON encode limit makes any run with more than about 4000 wrong-bank hits write an empty trace (on this route 6666), and the EmuHawk stays open until the deadline. SYNTH-fixture DEV evidence, not PHYSICAL.

## Rival gate measurement, first live runs (2026-10-08, SYNTH scene fixture, DEV). Oracle FAIL; P0 site finding

L4 coordinator reconciliation of card `g2p-rival-live` (Codex Polished, commit `ce6ca70f`, merged `abcb1ed17`). Overlay `877a477a7dfc70b775ca3f46461d67abebe07083` (UPS 3727 B). Raw root `F:/slink-work/lanes/pol-rival-live/out` (`disclosure.json`, `evidence-manifest.json` with per-file hashes). Every run declares SYNTH: the fixture is the tool-built scene-only derivative of the duo fixture (`derive_rival_save.py`; source sha256 `75c7a5dc...`, derivative `030c62ff...`; exactly four bytes differ: `1782` 00->01, `1F0D` E4->E5, `2582` 00->01, `2D0D` E4->E5; RTC footer preserved). Native CONTINUE accepted the derivative. Speed 300%, not 1x qualification.

| run | setup | recorder | Python oracle | why |
|---|---|---|---|---|
| `synth-xq6b4nrp` | synth/rival, first calibration | PASS recording (98 hooks, frame 6237) | FAIL | the calibration stopped on an OLD wild last-site hit mistaken for rival completion; retained as a flawed run |
| `synth-i_xfq96x` | synth/rival, corrected route (90 tiles, Route 29 -> Cherrygrove) | PASS recording (231 hooks, frame 10146) | FAIL (10 reasons, all `NOT_RIVAL_CLASS` on earlier wild hits) | the real rival triple IS present at frame 10085: gate/next/last at `0F:47DD/47E0/480D`, `wBattleMode` 2, class `$1B`, id 3, OT party count 2; five earlier wild-battle hits at the same site make the strict whole-run oracle fail |
| `synth-1agu9bbq` | synth/wild control on the ORIGINAL fixture | PASS recording (23 hooks, frame 1380) | FAIL (`NOT_TRAINER_BATTLE`, `WILD_QUALIFIED_HIT`) | one qualified hit at frame 780 with `hBattleTurn` 1, mode 1, class/id 0/0, count 1 |

Trace sha256: corrected calibration `1ce96295ed630aa7fd58a7ba56e47a5a30bf9f6cb1c79ffe157abb18e62eb41c` (route.json `c0dd9bce...`), wild control `1e53789331507f5e436d5e3e11504a7805ce4b4f0581a31c249f1ee34c67a5b9`.

**P0 finding (held by the coordinator against the Polished v3.2.3 source):** the pinned rival gate site `0F:47DD` is `SendInUserPkmn+149` (`polished_slink.sym` 10490/10497). `engine/battle/core.asm:8079-8086` (`.wildmon`) sets `WILD_BATTLE`, calls `LoadEnemyWildmon`, `SetEnemyTurn`, sets `wEnemySwitchTarget` = 1 and calls `SendInUserPkmn`; `:1240-1245` branches on `hBattleTurn` before the enemy-party pointer load. So the site runs for the enemy send-in of EVERY battle, wild included, and the RIVAL_STAGING premise "no qualified wild gate hit" was false as written. Enemy side alone does not discriminate (the wild control measured `hBattleTurn` = 1). A rival-only qualification needs bank/PC at the site AND `hBattleTurn` = 1 AND `wBattleMode` = 2 AND `wOtherTrainerClass` in the rival set AND `wOtherTrainerID` = the expected rival, with the next/last witnesses on the same identity. Proposed in `RIVAL_STAGING.md` section 4; implemented by the follow-up card `g2p-rival-pred` (offline re-judgement of these traces is OPEN missing-side because they predate the side field; a fresh live run supplies it).

**What this does establish:** the SYNTH scene fixture boots natively; the source-derived button route reaches the rival encounter; the probe records the exact gate/next/last triple with the pinned bytes `218bd2fa0cd1` at `47DD`. **What it does not:** any rival swap write, class-set coverage (only `$1B`), a fixed-route replay (timed native inputs, RNG/RTC dependent), 1x speed, or a client/server run. No receipt was re-judged into PASS.

### Rival gate, operation predicate and one fresh live run (2026-10-08, SYNTH, DEV). PASS (probe-level)

Card `g2p-rival-pred` (Codex Polished, commit `9affcc5b`). The probe now qualifies an OPERATION, not a site hit: bank/PC at the pinned site AND `hBattleTurn` = 1 AND `wBattleMode` = 2 AND `wOtherTrainerClass` in the rival set AND `wOtherTrainerID` = the configured rival (`rival_gate_probe.lua:74-79`; Python recomputes it independently and rejects forged flags). Raw hits are still recorded (`raw_qualified`). Coordinator re-checked: fresh run `synth-pdm83y_x` (owned PID 19156, overlay `877a477a`, derivative fixture `030c62ff...`, 300%) has exactly three operation-qualified rows, all at frame 9125: `0F:47DD/47E0/480D`, side 1, mode 2, class `$1B`, id 3, OT party count 2; four earlier wild callbacks are raw-qualified only. Oracle `RESULT: PASS rival-gate-probe (synth/rival, 0 reasons)`; trace sha256 `ece415ff903aa319de998955b95eaf34ad505d4c4573006544aea612474a994b`. Offline re-judgement of the older traces (`--rejudge`, no emulator): `synth-i_xfq96x` OPEN (MISSING_SIDE: its rows predate the side field; a source-conditional model assuming side 1 passes, which is not a physical PASS), `synth-xq6b4nrp` OPEN (no rival gate), `synth-1agu9bbq` PASS-as-control (zero operation-qualified gates). The three traces are committed under `tests/fixtures/polished/rival/` with pinned sha256. 365 probe/builder tests pass.

Still open: the production discriminator. `lua/gen2/polished_rival.lua:135-146` already checks trainer mode, bank/PC, class and an OPTIONAL trainer id; it needs the explicit enemy-side check and a REQUIRED bound trainer identity (symbols at the callback: `hBattleTurn` 00:FFD1, `wBattleMode` 01:D233, `wOtherTrainerClass` 01:D235, `wOtherTrainerID` 01:D237, `wOTPartyCount` 01:D283). Not done: any rival swap write, other rival classes, 1x speed, a fixed-frame replay (the route is a timed native traversal, RNG/RTC dependent).

## Held trade service, six serverless cases live on the current overlay (2026-10-08, SYNTH + TEST HOST, DEV). PASS (service-level)

Card `g2p-svclive` (OMP 21080, probe fixes `6a754640`, merged `a73ce497c`). Overlay `cf03f53accefbc5f3fee9062846699e30c4c987b` (C5 bounded rollback, commit compile-disabled, `7e:573B` = 0), applied privately and sha1-checked before every launch. Fixture `75c7a5dc...` (unchanged after all runs). Disclosure in every receipt: SYNTH event flag `wEventFlags+4 |= $02` + engine warp to POKECENTER_2F; TEST HOST copies its own mon 0 into OT slot 0 for APPLY; no cable partner, server, SLink client or native commit. Evidence root `F:/slink-work/lanes/pol-svclive` (`receipts.json`, per-attempt `probe/{result,trace,oracle}`).

| case | attempt | recorder | Python oracle |
|---|---|---|---|
| offer-reject | 1 | FAIL aborted, frame 991 | FAIL (probe defect: dot-call into the lease writer; zero-placeholder store values) |
| offer-reject | 2 | PASS, frame 1229 | PASS |
| cancel-menu | 1 | PASS, frame 1134 | PASS |
| no-eligible | 1 | PASS, frame 1085 | PASS |
| query-timeout | 1 | PASS, frame 1684 (QUERY entry to close: 600 frames) | PASS |
| apply-done1 | 1 | PASS, frame 1232 | FAIL (recorder did not yet decode `ld [de],a` token stores) |
| apply-done1 | 2 | PASS, frame 1232 | PASS: DONE result 1, generation = ack = 3, host RELEASE seen, party 5 -> 5, party checksum unchanged |
| apply-invalid | 1 | PASS, frame 1231 | PASS |

Both FAILs were instrument defects, fixed red-first in the probe (`trade_service_probe.lua`) before the single retry; no unchanged failing case was re-run. In every PASS the lease ends idle (command/available/mask 0), the script engine is restored and the native close/return order holds. `apply-done1` PASS is disabled-commit evidence (result 1 = not performed), never a trade. Not shown: a cable partner, the SLink client/server, an enabled commit.

## Cold reload identity/census probe on the current overlay (2026-10-08, SYNTH fixture, native CONTINUE, DEV). PASS (bounded)

Card `g2p-reloadlive` (OMP 21080; no code change). Overlay `cf03f53accefbc5f3fee9062846699e30c4c987b`, private ROM sha1-checked; fixture the qualified SYNTH save `75c7a5dc...` (unchanged after all runs). The EXPECTED file was decoded independently from raw fixture bytes (not through the probe's or the codec's decoders): party of five (`EFFFFF:D1C2:0A9:00`, `DFFFFF:D1C2:087:00`, `CFFFFF:D1C2:037:40`, `BFFFFF:D1C2:022:00`, `AFFFFF:D1C2:055:40`), OT `80a0a0a0a0a0a053000000` for every slot, player `d1c2`, all twenty boxes empty in both newbox metadata copies. Route: the cold-boot prefix of `tools/polished_live/routes/dispatch_d3_route.json` through its first idle (A presses, no walking). Evidence root `F:/slink-work/lanes/pol-reloadlive` (`receipts.json`).

| run | verdict | notes |
|---|---|---|
| offline | PASS | both save copies valid, region sums 5092; ROM version not observable offline (expected) |
| live cold `cold-vde_8n71` | PASS, reasons [] | native load at frame 261, main copy selected, stable snapshot at frame 338; ROM version slot `920aab8b0caa7fe09ce19ce05300000000000000` = provenance `version_slot` (coordinator recomputed from the UPS); zero guest writes |
| live cold, wrong slot-0 key (red control) `cold-ghrg2v6_` | MISSING_RECEIVED | same route/ROM/fixture; the oracle rejects it, never PASS |

Bounded: empty referenced storage, main-copy acceptance only. Not exercised: occupied boxes / banked PokeDB, backup-copy fallback, a corrupt save, a reload after a trade or a box write.

## D3 dispatcher and F3 natural-faint probes re-bound to the current overlay (2026-10-08, SYNTH fixture, DEV). PASS (probe-level)

Card `g2p-probes2` (OMP 21080; dispatch-probe admission fix `286c8068`, merged `48476717d`). Overlay `cf03f53a...`, both launched ROM copies sha1-checked; bank `$0F` byte-identical to the clean release (sha256 `a1364601...`). Fixture: the same qualified SYNTH save `75c7a5dc...` as the 2026-10-06 runs, now admitted at its full 32790 bytes (RTC footer kept), unchanged after the runs. Evidence root `F:/slink-work/lanes/pol-probes2`.

- **D3** `dispatch/played-dcrx2f5k` (trace `f6e2218c...`): `RESULT: PASS dispatch-probe (0 reasons)`. The probe's admission check still required the pre-C6 inert `ret` at `7e:4780`; it now requires the `jp` to the linked `SlinkTradeResponderService` (red test first). Same route (1498 frames), 1551 dispatcher hits, 0 prompt hits, 0 guest writes. Per-phase entries / nine-pin matches / engine-clean / accepted: idle 360/360/330/330, walking 96/96/2/2, start menu 101/0/0/0, NPC talk 204/0/0/0: identical to the 2026-10-06 table. Raw negative stack images differ slightly (16 vs 17 walking images; minimum SP `$C0B6` vs `$C0B8`), cause not established; acceptance and guard totals are unchanged.
- **F3** `faint/played-pdhky12_` (trace `051f0831...`): verdict PASS, one qualified pre-copy pass (not animation qualification). The six qualified rows (frames 1390/1460: resolve `44af`, FaintUserPokemon `4cd2` x2, pre_copy `44c8`, copy_call `44ca`, copy_return `44cd`) and every per-site total and wrong-bank count match the 2026-10-06 run exactly (6666 callbacks, 0 wrong PC).

Not shown: a player-mon faint, whiteout, animation safety, a lease/prompt publication.

## Title wordmark on the current overlay (2026-10-08, fresh CGB boot, DEV). PASS against a failing clean control

Card `pol-rcproof` (worktree at `2959ee36a`, no repo change). Command `python tools/polished_live/title_check.py --lane F:/slink-work/lanes/pol-rcproof/title`. Overlay `688945795e2656019247f5aaceb7b1d8791e900a` (= provenance) and the clean release `6930b48a...` booted by the same tool with the same inputs, no SaveRAM. Overlay: `RESULT: PASS polished-title-overlay`, `reasons []` at entrance (frame 3094, scx 48, both wordmark rows inside the interlaced shear), settled (frame 3168) and main menu (frame 3288, version slice equals the ROM field). Clean control: `RESULT: FAIL polished-title-clean` with 15 reasons on the identical judge (band tile ids/attributes, VRAM art, shear, colour indices, 283/608 pixels, version slice), so the judge discriminates. Evidence `F:/slink-work/lanes/pol-rcproof/title/evidence.json` (sha256 `f214ffe5...`). Receipt `LIVE-TITLE-SPLASH`; closes `OPEN-TITLE-SPLASH`. Not shown: DMG, any save/main-menu variant other than a fresh boot.

## Overworld write path re-qualified on the current overlay (2026-10-08, SYNTH fixture, DEV). PASS (a/b/d)

Card `g2p-writes6889` (driver docstring commit `499d1cab9`; code digest unchanged from `2959ee36a`). Command `python tools/polished_live/writes_run.py`, `POL_LANE=F:/slink-work/lanes/pol-writes6889`, fixture `75c7a5dc...`, overlay `688945795e...` sha1-checked before launch. `RESULT: PASS pol-live-writes (0 checks failed) frame 936`, 45 `[ok]`. Through `Client:handle_command`: (a) bench `force_faint` HP 152 -> 0, 3 writes all in the target's +32/+34/+35; (b) `box_mon` party 5 -> 4, bank-1 entry 1 in box 1 slot 1, B.verify true; (d) `party_mon` party 4 -> 5 at the last slot, healed, Entries `$01 -> $00`, pokedb entry never freed. Recomputed from `trace.json` images: 1/3, 110/193, 43/73 bytes changed of allowed, nothing outside, every control fails; byte-identical to the `cf03f53a` lane. Evidence `F:/slink-work/lanes/pol-writes6889/writes/result.txt` (+ `trace.json`, `receipts.json`). Receipt `LIVE-WRITES-OVERWORLD`. Not shown: (c) negatives (need a real battle), an in-battle or PHYSICAL faint, bank 2, a partner-driven command.

## Panel pages, ROM half, on the current overlay (2026-10-08, SYNTH fixture + scripted host, DEV). PASS (C0-C5)

Card `pol-rcproof`. Command `POL_LANE=F:/slink-work/lanes/pol-rcproof/panel python tools/polished_live/run_pol_panel.py`, overlay `688945795e...` staged from the committed UPS. `RESULT: PASS pol-panel (0 checks failed) frame 806`, 21 `[ok]`: C0 SLink row 0 and submenu; C1 caps `07`, 69-byte mailbox `$C60B..$C650`; C2 page 1 "SOUL LINK / PARTNER: RED" with no extra keypress; C3 page 2 "PAIRS 2/3 / BADGES 4/8"; C4 back to the list, no call, `PANEL_STATE` cleared; C5 72 panel writes, 0 outside `$60B..$64F`. The pages were published by the driver's scripted mailbox writer, not the real server/lua host; `wPokegearFlags` `$87` SYNTH. Evidence `F:/slink-work/lanes/pol-rcproof/panel/run/result.txt`. Receipt `LIVE-PANEL-PAGES-ROM`. `OPEN-PANEL-PAGES` stays open on real-host paging.

## Panel hello on the current overlay (2026-10-08, SYNTH fixture, real server, DEV). PASS

Card `pol-rcproof`. Lane-local wrapper `F:/slink-work/lanes/pol-rcproof/hello_run.py` (stages the committed UPS, then `harness.cmd_live`, `POL_STAGES=1`): `lua/slink.lua` composes the client against the real SLink server. `RESULT: PASS polished-live (0 checks failed) frame 516`. Wire `wire_a.jsonl:2` hello: `panel true`, `panel_abi 3`, `companion_abi 3`, `rom_sha1 688945795e...`, `polished_crystal/gen2_polished/overlay`; `server.log:14` admitted against the contract; 0 Lua-originated writes, 0 panel write attempts. Supersedes the `panel false` hello of the Stage-1 overlay `34942315`. Receipt `LIVE-PANEL-HELLO`. Not shown: a page published by the real host.

## Panel pages from the real host on the current overlay (2026-10-08, SYNTH fixture + scripted partner, DEV). PASS, rows truncated

Card `pol-panelhost` (driver `tools/polished_live/panel_host_live.{py,lua}`, `8edc811f2`, merged `7191b8253`). Overlay `688945795e...` staged from the committed UPS. Run 2: `RESULT: PASS pol-panel-host (0 checks failed) frame 3170`, 29 `[ok]`, Python oracle `PASS`, `why []`. A native Route 29 catch (Rattata) linked against a scripted partner B on the real server; the server's `link_panel` (`RT29|Rattata|2|13/13|38||`, `|CROBAT|50|160/1|38||`, `Pairs alive|1/1`, `Dead zones|0`, `Badges|0/8`) was held by the client, staged through its own permit (12 writes, 0 outside the mailbox) and rendered as three pages; B closes cleanly. Run 1 FAILED on an instrument defect (`run1_instrument_bug/`). **The rows are cut to the 16-glyph panel**: page 1 shows `RT29 Rattata 2 1` / `CROBAT 50 160/1` (HP and the trailing field lost), because `Gen2PolishedAdapter` inherits `info_panel_width() == 0`; the per-line check compares against the staged, already cut, row. Evidence `F:/slink-work/lanes/pol-panelhost/run/oracle.json` (+ `result.txt`, `wire/`). Receipt `LIVE-PANEL-HOST-PAGING`. `OPEN-PANEL-PAGES` stays open on readability (`pol-panelfix`).


## RC freeze 5f4732 — 2026-10-08

This section records the frozen product digest
`5f4732503c4ae4820b253fef3b7fc0870a3edf11b62be70868dffba874aa95f1`, product cut
`e0dc44314`, shipped overlay `fe8c57e1034059331ccf25d232ff757d675363b2` and
subsequent driver-only consent fix `d51f3e9c3`. All installed receipts are **DEV**.
The receipt JSON contains the full evidence path, current hashes, checks and limitations;
`RC_STATUS.md` records the final verifier result. No older PASS was restamped.

| Run | Frozen result and receipt |
|---|---|
| Rival `synth-vrpd4rcz` | PASS, frame10775; `live_rival_swap.json`. Native 0F:47DD enemy send-in, one server-requested partner-party swap, exact write/image oracle and continuation. SYNTH scene, scripted second identity; only RIVAL0/id3. |
| Explosion `explode-615a97985038` | PASS, frame3307; `live_explode_explode.json`. Native action and exact write/diff/echo checks; TEST HOST command. |
| Active faint `active-faint-e6c588c4f822` | PASS, frame2022; `live_explode_active_faint.json`. Native consumption with commanded echo suppression; TEST HOST command. |
| Bench faint `bench-faint-8b4ca079ca7d` | PASS, frame2022; `live_explode_bench_faint.json`. No bench mutation in battle, then overworld checkpoint write. |
| Shipped trade `shipped-001` | Retained FAIL before APPLY; premature driver A input after wire show_menu entered the receptionist proposer script before responder pickup. No completed trade/cold proof. |
| Shipped trade `shipped-002` | PASS; `live_shipped_trade.json`. Driver now waits for native responder entry. B entry1052, consent1248, commit1263, trade_done3822; A commit1447, trade_done4145. Both token t1. Server ALIVE link re-keyed, pending_trade null; both cold CONTINUE parties and main/backup native-save copies retain exchanged keys. |

Evidence root: `F:/slink-work/lanes/pol-freeze-5f4732/trade`. The separate
`shipped-002/cold-verdict.json` is an explicitly labeled offline check of the recorded
cold/native-save evidence; it does not replace the original combined verdict.
The shipped build uses its normal launcher with no `--test-build`. The fixture, derived
TradeB identity and seeded link are disclosed SYNTH setup; subsequent menus, commits and
saves are native. This happy path does not prove power-loss handling, general rollback,
all item/evolution cases or a human campaign.


The same freeze now binds `LIVE-WRITES-OVERWORLD` to fresh legs a/b/d/e/f and adds
`LIVE-DUO-NATURAL-FAINT` / `LIVE-DUO-NATURAL-WHITEOUT`. The writes receipt covers party-origin
and box-origin memorialize to box20/index19, source removal, one completion acknowledgement,
independently recomputed exact diffs and failing controls. It is local command-path evidence,
not save/reset durability or a persistent owed-burial contract.

S2n closes `OPEN-WRITE-PATH` at the owner's RC bar. S4n reports a whiteout and the linked
partner dies through per-mon faint propagation; `whiteout_handler_proved=false` remains
explicit. Only one linked pair was present. Both duo receipts disclose HP=1 conditioning,
identity derivation and lead permutation; native death/capture and real client/server
propagation are observed, without promotion of SYNTH-conditioned DEV to PHYSICAL.
