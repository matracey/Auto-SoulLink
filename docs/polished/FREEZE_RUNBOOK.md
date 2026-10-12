# Polished RC freeze re-run runbook (2026-10-09)

A plan, not a result or release authorisation. Run from the **single frozen integration checkout**, not from the old lane worktrees. This inventory covers all **15 LIVE ids** in `tests/polished_release_requirements.json` at the documentation source cut `4f7bd2fb1`, plus the pending writer/duo/trade evidence. Do not re-stamp an old receipt: re-run its scenario on the frozen code, judge the new bytes, then bind the new evidence.

Commands below are PowerShell. Every evidence destination is fresh under `$F`; retained input paths are explicit. Run each command block with `Reset-PolEnv` as shown: environment from a previous randomized/control case must not leak into an overlay run. Inputs outside the repo are prerequisites, not files shipped to players.

**Unsafe probes:** `harness.py live/explore`, `manager_r1.py`, `run_phone_ab.py`, `writes_run.py` and `run_pol_panel.py` have no safe `--help`/`--dry-run` probe. `manager_r1.py --help` starts the Manager; the other invocations may stage/delete saves or launch EmuHawk. Inspect their entry points instead; never probe these invocations with those flags.

Concurrency labels: **BUILD ALONE** means no other process may use the Polished build caches. **CLIENT SERIAL** means one client-driver job at a time in this checkout: `lua/slink.lua:19` truncates the shared root `slink_lua.log`, so private emulator/SaveRAM paths alone do not make these jobs evidence-isolated. A duo job owns two EmuHawks internally; do not split its halves. **PRIVATE** jobs may overlap one another and one CLIENT SERIAL job after the build is frozen, with distinct output lanes. Do not run tests concurrently with BUILD ALONE. Always stop only the PIDs a driver started; never kill by image name or delete through `.cache` junctions.

1. **Finish fixes, then reserve and record the cut.** No active writer may change runtime, pack, assembly or provenance files after this point. Owner rulings: retire `LIVE-NO-BOX-MON`; in-battle negatives are unit/model-covered; only PHYSICAL natural in-battle faint S2n remains for `OPEN-WRITE-PATH`; S4n accepts whiteout event sent plus all partners dying through per-mon faint (`_handle_whiteout` stays unit-covered). The legacy instrument repair and Explosion echo fix are merged at this source cut. Finish memorialize, the owner-authorized shipped trade enablement (`pol-shiptrade`), capability/Manager flips and any remaining product repairs before the final runs. Retained examples are DEV, not PHYSICAL qualifications.

   ```powershell
   $PY = 'E:/Google Drive/SLink/.venv/Scripts/python.exe'
   $env:SLINK_WORK_ROOT = 'F:/slink-work'
   $env:PYTEST_DEBUG_TEMPROOT = 'F:/slink-work/tmp'
   $env:PYTHONUTF8 = '1'
   $cut = (git rev-parse --short=12 HEAD).Trim()
   $F = "F:/slink-work/lanes/pol-freeze-$cut"
   if (Test-Path $F) { throw 'Choose a fresh freeze attempt; retain the old evidence' }
   New-Item -ItemType Directory $F | Out-Null
   function Reset-PolEnv { Get-ChildItem Env:POL_* | Remove-Item }
   $base = 'F:/slink-work/cache/polished/release/polishedcrystal-3.2.3.gbc'
   $fixture = 'F:/slink-work/lanes/g2int-live/pol/fixture/polished_overlay_warp.SaveRAM'
   $fixtureB = 'F:/slink-work/lanes/pol-ident/B.SaveRAM'
   git status --short --branch
   git rev-parse HEAD
   Get-FileHash -Algorithm SHA256 $fixture,$fixtureB
   ```

   Base release SHA1 is `6930b48af5844d373e3c9130f26d6dd1084cf4ed`; the common fixture SHA256 is `75c7a5dc30126f746567202cfb39fe583dfa04eb226063560541f6cbd29f36b8`. SYNTH ancestry: five level-50 mons, 99 balls and engine-warp position, followed by native SAVE. B is the disclosed second-trainer derivative (`derive_save.py`), not independently played. Preflight overlay (before shipped trade enablement) is `688945795e2656019247f5aaceb7b1d8791e900a`; read final provenance rather than assuming it survives a new build.

2. **BUILD ALONE: reproduce the clean/overlay build, before any emulator or unit shard.** Shared-cache concurrent private builds previously made BUILD-OVERLAY fail; do not run these alongside another build. If source/provenance must be published, complete that separately before recording the final cut. Assembly/item-table edits require a rebuild, not hand-edited hashes. Version remains `0.1.0` unless the owner changes that ruling.

   ```powershell
   & $PY tools/build_polished_syms.py --check
   & $PY tools/build_polished_companion.py --check --version 0.1.0
   ```

   The verifier's exact BUILD-CLEAN-ROM command is the manifest authority if it changes; check it before executing. Pass requires clean reproduction, overlay byte/span checks, UPS round trip and published sym/map/provenance consistency. A nonzero exit is not permission to repeat an unchanged failed run.

3. **Prepare artifacts and executable pins before the final freeze.** Re-derive beacon/profile/engine sites (the engine-sites generator also writes `write_checkpoint.json`), then commit any resulting artifact changes. A changed trade item table must precede the overlay build. After all changes, record the final HEAD and both digest/provenance hashes, then start the receipt runs. Do not patch a driver pin while receipt jobs are already running.

   Digest scope is declared by `tests/polished_release_requirements.json`: `lua/*.lua` and `server/**/*.py` conservatively cover shared runtime alongside the existing Polished client/core, adapter, assembly and runtime-pack entries. Shared admission, quarantine and memorial-relocation changes therefore stale LIVE receipts. The Polished-pack `.md`/`.txt` prose exclusions remain unchanged.

   ```powershell
   & $PY tools/gen_polished_beacon.py --check
   & $PY tools/gen_polished_profile.py --check
   & $PY tools/gen_polished_engine_sites.py --check
   & $PY tools/gen_polished_pack.py --check
   & $PY tools/gen_polished_forms.py --check
   & $PY tools/gen_polished_script_sites.py --check
   & $PY tools/gen_upr_polished_ini.py --check
   & $PY tools/polished_pin_audit.py
   & $PY tools/verify_polished_release.py --print-code-digest
   Get-FileHash -Algorithm SHA256 data/polished/overlay_provenance.json
   ```

   `pol-shiptrade` must land before this freeze: it owns the shipped enabled overlay and provenance-derived pins in its leased drivers. Verify each driver consumes the final provenance; explicitly re-pin the separately owned `writes_run.py` and `trade_port_probe.py` if still literal. The rival route also binds its overlay: recalibrate after any ROM change before freezing. Do not assume the preflight `68894579` route survives the enabled build.

4. **Unit qualification, no emulator.** If shared state/protocol changed, run Gen 3 first, then Gen 1/Gen 2 checks before the full shards. This is the checked-in six-process runner, not a hand-kept file/count recipe.

   ```powershell
   & $PY -m pytest tests/unit -k gen3 -q
   & $PY -m pytest tests/unit -k gen1 -q
   & $PY -m pytest tests/unit -k gen2 -q
   & $PY tools/run_unit_shards.py --shards 6 --out "$F/unit"
   ```

   Evidence: `$F/unit/s1.log` through `s6.log`, file lists and `failures.txt`. These may run concurrently with PRIVATE jobs only if the selected tests do not write a shared build cache; conservative schedule is to finish shards first. Skips are disclosed, never counted as live proof.

5. **CLIENT SERIAL: LIVE-HELLO-ADMITTED, LIVE-HELLO-GATE-FRAME and LIVE-PANEL-HELLO, one shared hello-only run.** The new tree-owned wrapper replaces `F:/slink-work/lanes/pol-rcproof/hello_run.py`: apply the committed UPS, then use `harness.cmd_live` with stage 1. Real server, real client, common SYNTH fixture, no scripted partner.

   ```powershell
   Reset-PolEnv
   & $PY tools/polished_live/hello_live.py --lane "$F/hello" --base $base --fixture $fixture --dry-run
   & $PY tools/polished_live/hello_live.py --lane "$F/hello" --base $base --fixture $fixture
   ```

   New primary evidence: `$F/hello/live/result.txt` for LIVE-HELLO-ADMITTED and LIVE-HELLO-GATE-FRAME; `$F/hello/live/wire/wire_a.jsonl` for LIVE-PANEL-HELLO. Retain `server.log`, `gate_transitions.log`, `sent.jsonl` and `syms.json`. Existing receipt bindings are `pol-phone/live_stageA/result.txt` (both old hello ids) and `pol-rcproof/hello/live/wire/wire_a.jsonl` (panel hello). Judge actual hello `panel=true`, ABI, ROM rehash, admitted server contract and frame ordering; stage 1's PASS alone does not assert panel capability.

6. **CLIENT SERIAL: LIVE-CAPTURE-PARTY-ONLY and LIVE-BOX-CENSUS, separate native catch runs.** `harness.py:134-144` now applies the committed UPS to the pinned release and validates provenance; a mutable companion build-cache file is not required. Keep party sync/quarantine enabled. The first catch legitimately moves from party to PC after its capture-time evidence is recorded.

   ```powershell
   Reset-PolEnv
   $env:POL_KIND='overlay'; $env:POL_LANE="$F/capture"
   $env:POL_FIXTURE=$fixture; $env:POL_STAGES='2'
   & $PY tools/polished_live/harness.py live

   # Approved preparation: private native catch + native SAVE, NO client, NO RAM party edits.
   Reset-PolEnv
   $env:POL_KIND='overlay'; $env:POL_LANE="$F/full-party-preparation"
   $env:POL_FIXTURE=$fixture; $env:POL_STAGES='2'; $env:POL_PREPARE_FULL='1'
   & $PY tools/polished_live/harness.py live
   if ($LASTEXITCODE -ne 0) { throw 'Native full-party preparation failed' }
   $fullFixture="$F/full-party-preparation/sram_overlay/pol overlay.SaveRAM"
   Get-FileHash -Algorithm SHA256 $fixture,$fullFixture

   Reset-PolEnv
   $env:POL_KIND='overlay'; $env:POL_LANE="$F/box-census"
   $env:POL_FIXTURE=$fullFixture; $env:POL_PARTY_COUNT='6'; $env:POL_STAGES='3'
   & $PY tools/polished_live/harness.py live
   ```

   Bind capture primary `$F/capture/live/result.txt` plus box-catch secondary `$F/box-census/live/result.txt` to LIVE-CAPTURE-PARTY-ONLY; bind the latter as primary for LIVE-BOX-CENSUS. Retain both wire streams, capture hook/count timing, census generations, server logs and `client_writes.json`. Quarantine writes are recorded, **not byte-qualified by this acquisition probe**; LIVE-WRITES-OVERWORLD remains the writer gate. Observation-only stages still require zero Lua writes (`live.lua:466-475`). If the capture run contains no failed throw, that negative remains NOT_RUN: obtain a separate declared native throw control before claiming the description's full matrix; do not count a conditional check that never ran.

   Preparation must PASS its six-party, native SaveGameData and no-client/no-Lua-write checks (`live.lua:368-373`); the next boot must actually load six mons. Record parent/child hashes and SYNTH ancestry in the disclosure, retain the original five-mon fixture, and never substitute a RAM party edit. Existing preflight child SHA256 `02e01dcc1c218d0d8fdea3d6c3d9a398bf7191eaf4a0894e9fcafcbc8e7bfb8f` is an example, not the hash of a future preparation.

7. **LIVE-R1-MANAGER-PAIR: Manager HTTP proof, no emulator.** Run its three modes sequentially; isolate the Manager's data directory. The current fork jar is `F:/slink-work/cache/polished/jar/PokeRandoZX.jar`; the old trusted-jar control is hardcoded by the driver to `E:/Google Drive/SLink/.cache/slink-upr/PokeRandoZX.jar` (must still be the older non-Polished jar, not silently replaced).

   ```powershell
   Reset-PolEnv
   $env:POL_LANE="$F/random"
   $env:POL_JAR='F:/slink-work/cache/polished/jar/PokeRandoZX.jar'
   & $PY tools/polished_live/manager_r1.py jar
   & $PY tools/polished_live/manager_r1.py nojar
   & $PY tools/polished_live/manager_r1.py oldjar
   $r1 = Get-Content "$F/random/r1_jar/summary.json" -Raw | ConvertFrom-Json
   $mgr = "$F/random/r1_jar/mgr/$($r1.run_id)"
   ```

   New primary evidence: `$F/random/r1_jar/summary.json`; controls `r1_nojar/summary.json`, `r1_oldjar/summary.json`, calls, downloaded cartridges and contract. Existing binding: `F:/slink-work/lanes/pol-rand/r1_jar/summary.json`. Driver exit 0 is not an oracle: inspect HTTP statuses, trusted/fork/Polished fields, source overlay, both downloads and contract SHA1s. Nojar mode clears the env but can still discover a jar by other search paths: verify actual refusal. Run before R2/R3. It can overlap PRIVATE emulator jobs, not a build that republishes its source artifacts.

8. **Select R2 inputs from the fresh R1 cartridges.** Reuse the declared five-mon Route 29 fixture as SYNTH setup, copying it into each private SaveRAM directory; boot and encounter tables come from the new Manager ROMs, not an old randomized fixture or target. Do not invoke the old setup commands that rewrite party/bag/map RAM. For the variant leg, inspect both cartridges' actual Route 29 slots and select a variant present in **all three** morning/day/night slices. Slots are three groups of seven (`server/adapters/polished_rom_scan.py:173-185`); searching their union can select a mon unavailable at the current time. The following read-only command prints that intersection and all slots; form 0 in the table is normal form 1 at runtime.

   ```powershell
   $inspectVariants = @'
   import json, sys
   from pathlib import Path
   from server.adapters.gen2_polished import scan_randomized
   for player in ("a", "b"):
       path = Path(sys.argv[1]) / "roms" / (player + ".gbc")
       for row in scan_randomized(path.read_bytes())["wild"]["JohtoGrass"]:
           if row["map"] == [24, 3]:
               slices = [{(slot[1], slot[2]) for slot in row["slots"][i:i+7] if slot[2] > 1}
                         for i in (0, 7, 14)]
               common = sorted(set.intersection(*slices))
               print(json.dumps({"player": player, "map": row["map"], "slots": row["slots"],
                                 "variants_all_times": common}))
   '@
   & $PY -c $inspectVariants $mgr | Tee-Object "$F/random/route29-slots.jsonl"
   ```

   Set `$variantPlayer` to the selected player (`a` or `b`) and `$variantTarget` to one printed `variants_all_times` pair as `species,form`. These are measured inputs, not defaults: the preflight chose B `110,3`, but new random seeds can remove it. If neither Route 29 table contains an all-times variant, stop this leg and declare a different native route/fixture before running; do not invent a target, change game data, or repeatedly hunt an absent mon. Keep the table output and selected inputs in the evidence disclosure.

9. **CLIENT SERIAL: LIVE-R2-RANDOMIZED-BOOT, two legs.** R2a observes A's admission/adoption and five fled encounters. R2b observes the selected cartridge and catches its measured variant; `harness.py:274` now forwards POL_PLAYER to the client as well as choosing its ROM.

   ```powershell
   Reset-PolEnv
   $env:POL_KIND='rand'; $env:POL_MGR_RUN=$mgr; $env:POL_PLAYER='a'
   $env:POL_LANE="$F/random/r2a"; $env:POL_EXPECT_KIND='rand_overlay'
   $env:POL_FIXTURE=$fixture; $env:POL_STAGES='5'; $env:POL_ENCOUNTERS='5'
   $env:POL_MAP='24,3'; $env:POL_HEADER='1,30,9'; $env:POL_WALK='46,51'
   & $PY tools/polished_live/harness.py live
   if ($variantPlayer -notin @('a','b') -or $variantTarget -notmatch '^\d+,\d+$') {
       throw 'Select and record a present variant from step 8 first'
   }
   $env:POL_PLAYER=$variantPlayer; $env:POL_TARGET=$variantTarget
   $env:POL_LANE="$F/random/r2b"; $env:POL_STAGES='56'; $env:POL_HUNT='30'
   $env:POL_BATTLE_FRAMES='40000'
   & $PY tools/polished_live/harness.py live
   ```

   `POL_BATTLE_FRAMES` raises the catch-battle guard in `live.lua` (default 9000). At freeze #2 a full-HP variant survived about eight native Poke Balls and both 9000-frame attempts aborted mid-battle with no product hold; the 40000-frame rerun passed. Set it on the first attempt.

   Primary `$F/random/r2b/live/result.txt`; secondary r2a result, both encounters.jsonl, actual ROM tables, server adoption/presentation and wire. Compare native species/form/level tuples against the executed cartridge, and compare the caught variant's capture-time bytes/key/dex id against the codec. Capture-time party growth precedes quarantine; do not read a removed slot afterward (`live.lua:37,453-456`). R2a keeps zero-write checks; R2b records expected quarantine writes without claiming writer qualification. Bind the **source overlay** and also record each executed randomized SHA1; they must not be made equal.

10. **CLIENT SERIAL: LIVE-R3-REFUSALS, four cases; no standalone all-cases oracle.** Real harness plus inspected client/server/wire output. The explicit preparation below copies fresh ROM a, XORs one byte at `0x14E` or `0x1F8028`, and records changed offsets and SHA1s; never alter the Manager ROM. The two offsets exercise different layers: `0x14E` is the deliberately excluded global checksum, while `0x1F8028` must still be covered by the frozen beacon. Confirm this against the generated beacon before making mutants. The lane-local preparation is specified here because there is no checked-in R3 all-cases producer/judge.

    ```powershell
    Reset-PolEnv
    $prepareMutants = @'
    import hashlib, json, sys
    from pathlib import Path
    source, out = Path(sys.argv[1]), Path(sys.argv[2])
    data = source.read_bytes()
    out.mkdir(parents=True, exist_ok=False)
    rows = []
    for name, offset in (("flip14e", 0x14E), ("flip1f8028", 0x1F8028)):
        mutated = bytearray(data)
        mutated[offset] ^= 1
        path = out / (name + ".gbc")
        path.write_bytes(mutated)
        rows.append({"path": str(path), "offset": offset, "old": data[offset],
                     "new": mutated[offset], "sha1": hashlib.sha1(mutated).hexdigest()})
    (out / "mutants.json").write_text(json.dumps({"source": str(source),
        "source_sha1": hashlib.sha1(data).hexdigest(), "mutations": rows}, indent=2))
    '@
    & $PY -c $prepareMutants "$mgr/roms/a.gbc" "$F/random/r3/input"
    $env:POL_KIND='overlay'; $env:POL_LANE="$F/random/r3/clean"
    $env:POL_FIXTURE=$fixture; $env:POL_STAGES='1'; $env:POL_RUNNAME='live'
    & $PY tools/polished_live/harness.py live 2>&1 | Tee-Object "$F/random/r3_clean.out"
    $env:POL_KIND='rand'; $env:POL_MGR_RUN=$mgr; $env:POL_PLAYER='a'; $env:POL_EXPECT_KIND='rand_overlay'
    $env:POL_FIXTURE=$fixture
    $env:POL_LANE="$F/random/r3/swap"; $env:POL_SWAP='1'
    & $PY tools/polished_live/harness.py live 2>&1 | Tee-Object "$F/random/r3_swap.out"
    Remove-Item Env:POL_SWAP
    $env:POL_LANE="$F/random/r3/flip14e"; $env:POL_ROM="$F/random/r3/input/flip14e.gbc"
    $env:POL_ROM_SHA1=(Get-FileHash -Algorithm SHA1 $env:POL_ROM).Hash.ToLower()
    & $PY tools/polished_live/harness.py live 2>&1 | Tee-Object "$F/random/r3_flip14e.out"
    $env:POL_LANE="$F/random/r3/flip1f8028"; $env:POL_ROM="$F/random/r3/input/flip1f8028.gbc"
    $env:POL_ROM_SHA1=(Get-FileHash -Algorithm SHA1 $env:POL_ROM).Hash.ToLower()
    & $PY tools/polished_live/harness.py live 2>&1 | Tee-Object "$F/random/r3_flip1f8028.out"
    ```

    New primary evidence: `$F/random/r3_swap.out`; bind all three control transcripts, `input/mutants.json` and wire/log files as secondary evidence. Existing binding: `F:/slink-work/lanes/pol-rand/r3_swap.out`. Clean overlay must admit; swapped contract must server-refuse. The `0x14E` mutant is client-admitted as rand_overlay but must server-refuse its changed full-ROM SHA1; every reply in both server-refusal cases must be an admission-refusal noop. The `0x1F8028` mutant must client-refuse on beacon mismatch and send no hello. The checksum exclusion is intentional (`tools/gen_polished_beacon.py:62-69`), not a defect to hide or repair. Harness FAIL on an intentionally refused boot is expected recording, not automatically a failed refusal oracle or a PASS. Manual judgment of the four outcomes is still required; no tree-owned all-cases oracle currently completes this receipt by itself.

11. **PRIVATE: LIVE-PHONE-ENTRY, current no-host fallback.** The merged legacy repair honors POL_LANE, exports the submenu cursor symbol and expects the current native SOUL LINK / NO CLIENT fallback after its 90-frame lease timeout (`run_phone_ab.py:17,30`; `phone.lua:107-111`). Keep the full list/submenu/no-Delete/native-contact matrix; panel C0-C5 is not its replacement.

    ```powershell
    Reset-PolEnv
    $env:POL_KIND='overlay'; $env:POL_FIXTURE=$fixture; $env:POL_LANE="$F/phone"
    $env:POL_ROM_UPS=(Resolve-Path patch/dist/SLink-Polished.ups).Path
    $env:POL_DRIVER='tools/polished_live/phone.lua'; $env:POL_RUNNAME='phone'
    & $PY tools/polished_live/run_phone_ab.py
    ```

    Primary `$F/phone/phone/result.txt`; retain scenarios/screenshots and staged ROM hash. SYNTH Pokegear flags and phone-list setup, native buttons and native-contact control. This isolated lane no longer writes the hardcoded pol-panel2 directory.

12. **PRIVATE: LIVE-RECEPTIONIST-STACK, exploration B.** Common SYNTH fixture; SYNTH event 33 (`EVENT_GAVE_MYSTERY_EGG_TO_ELM`) plus engine warp to POKECENTER_2F (20:1) at (5,3), then native receptionist input and stack sampling. Measure the current overlay proposer service and return, not the removed trade-side native cable wait. No real trade/commit proof: this no-host exploration is not OPEN-IN-GAME-TRADE closure. Bank-qualified stack samples must include idle control, prompt and overlay host-wait phases (`explore.lua:49-50,75-109`).

    ```powershell
    Reset-PolEnv
    $env:POL_KIND='overlay'; $env:POL_LANE="$F/explore"; $env:POL_FIXTURE=$fixture; $env:POL_EXPLORE='B'
    & $PY tools/polished_live/harness.py explore
    ```

    New primary evidence: `$F/explore/explore_B/result.txt`; retain stacks.json, route, screenshots and symbol resolution. Existing binding: `F:/slink-work/lanes/pol-live2/x/explore_B/result.txt`. Uses the committed UPS over the pinned release with provenance verification.

13. **PRIVATE: LIVE-POKEGEAR-MEASUREMENT, exploration C.** Common SYNTH fixture and disclosed Pokegear setup. The same lane is safe only sequentially; choose separate `$F/explore-c` for concurrency.

    ```powershell
    Reset-PolEnv
    $env:POL_KIND='overlay'; $env:POL_LANE="$F/explore-c"; $env:POL_FIXTURE=$fixture; $env:POL_EXPLORE='C'
    & $PY tools/polished_live/harness.py explore
    ```

    New primary evidence: `$F/explore-c/explore_C/result.txt`; retain icon tile/OAM/stack evidence. Existing binding: `F:/slink-work/lanes/pol-live2/x/explore_C/result.txt`. Native UI measurement, not real-host panel paging.

14. **PRIVATE: LIVE-TITLE-SPLASH.** Fresh boot with no SaveRAM; tool owns separate overlay and clean-control EmuHawks sequentially. Both use the same judge; clean control must fail the wordmark oracle while overlay passes.

    ```powershell
    Reset-PolEnv
    & $PY tools/polished_live/title_check.py --lane "$F/title" --base $base
    ```

    New primary evidence: `$F/title/evidence.json`; existing binding `F:/slink-work/lanes/pol-rcproof/title/evidence.json`. CGB entrance/settled/menu snapshots only, not DMG or alternate save/menu states. Closes OPEN-TITLE-SPLASH only when this LIVE row passes in the same verifier run.

15. **CLIENT SERIAL: LIVE-WRITES-OVERWORLD.** Common SYNTH fixture; driver invokes real Client:handle_command (TEST HOST), not partner-driven kills. Unset scope reducers. In-battle (c) negatives remain explicitly NOT RUN live and owner-accepted as unit/model-covered.

    ```powershell
    Reset-PolEnv
    $env:POL_KIND='overlay'; $env:POL_LANE="$F/writes"; $env:POL_FIXTURE=$fixture
    & $PY tools/polished_live/writes_run.py
    ```

    New primary evidence: `$F/writes/writes/result.txt`; secondary `$F/writes/writes/trace.json`, exact recomputed party/CartRAM/allocation differences and permit bounds for (a) bench force_faint, (b) box_mon, (d) party_mon, (e) party-origin memorialize and (f) box-origin memorialize. Retained a/b/d baseline: `F:/slink-work/lanes/pol-writes6889/writes/result.txt`; retained e/f recording: `F:/slink-work/lanes/pol-memorial/writes/result.txt`. These local command legs are not persistence or natural in-battle faint proof.

    `writes_run.py` defaults `POL_MEMORIAL=1`, so the command above runs all five legs a/b/d/e/f after `Reset-PolEnv` clears overrides. Re-run them on the shipped frozen overlay; retain independent e/f diffs and failing controls, complete key/census reads, source membership removal, destination box 20 (index 19), and exactly one `memorialize_done` with no `memorialize_failed` per leg. Leg e buries the party target from (a); leg f buries the boxed (b)/(d) key. This TEST HOST recording does not establish both players' server obligations or save/reset durability. Owner ruling #7 accepts parity best-effort for RC; the stricter owed-burial/three-outcome contract is post-RC (`docs/polished/MEMORIALIZE.md`). Do not close OPEN-MEMORIALIZE using the old a/b/d-only recording.

16. **PRIVATE: LIVE-PANEL-PAGES-ROM, ROM half against scripted host.** Stages the committed UPS, not a mutable cached overlay. Common SYNTH fixture plus Pokegear flags/phone-list setup; C0-C5 protocol/paging/close and bounded mailbox spans. Leave `POL_FIXTURE` unset: the driver reads `$fixture` via its retained default, then copies it to `$F/panel-rom/fixture/polished_overlay_warp.SaveRAM`. Setting it makes both `FIXTURE_SRC` (`run_pol_panel.py:39-40`) and `H.FIXTURE` (`harness.py:51`) the same path.

    ```powershell
    Reset-PolEnv
    $env:POL_KIND='overlay'; $env:POL_LANE="$F/panel-rom"
    & $PY tools/polished_live/run_pol_panel.py
    ```

    New primary evidence `$F/panel-rom/run/result.txt`; secondary panel.json/screenshots. Existing binding `F:/slink-work/lanes/pol-rcproof/panel/run/result.txt`. No real server; does not substitute for host paging.

17. **CLIENT SERIAL: LIVE-PANEL-HOST-PAGING.** Common SYNTH fixture; Pokegear flags/list writes; scripted TCP second identity derived from the save, normal hello/tick/capture events; one real native Route 29 catch by A; real server link_panel -> client hold/stage -> ROM pages. Include the compact/readable 16-glyph row oracle, not only equality with already-truncated text. Leave `POL_FIXTURE` unset here too: `--lane` selects the private destination `$F/panel-host/fixture/polished_overlay_warp.SaveRAM`; `panel_host_live.py:154` copies the same retained source as step 16.

    ```powershell
    Reset-PolEnv
    & $PY tools/polished_live/panel_host_live.py --lane "$F/panel-host" --timeout 2400
    ```

    New primary evidence `$F/panel-host/run/oracle.json`; retain result, wire, persisted links/status, page text and permits. Existing binding `F:/slink-work/lanes/pol-panelhost/run/oracle.json`; readability follow-up is `F:/slink-work/lanes/pol-panelfix/run/oracle.json`, not yet a replacement receipt by editing its hash.

18. **CLIENT SERIAL: rival swap, pending OPEN-EXPLODE-RIVAL evidence (not yet a LIVE id).** Use a route calibrated on the final overlay, named explicitly as `$rivalRoute`; SYNTH scene-only fixture plus scripted second trainer; native feedback navigation, real client/server trainer event and swap consumer. Do not use `--fixed-route` for the normal qualified replay.

    ```powershell
    Reset-PolEnv
    if (-not $rivalRoute -or -not (Test-Path $rivalRoute)) { throw 'Declare the final-overlay calibrated route first' }
    $routeDir = Split-Path $rivalRoute
    if (-not (Test-Path "$routeDir/calibration.json") -or -not (Test-Path "$routeDir/result.txt")) {
        throw 'Retain the route calibration and its overlay-hash transcript'
    }
    & $PY tools/polished_live/rival_swap_live.py --fixture 'F:/slink-work/lanes/pol-rival-live/fixture/rival.SaveRAM' --disclosure 'F:/slink-work/lanes/pol-rival-live/out/disclosure.json' --route $rivalRoute --out "$F/rival" --frames 18000 --timeout 600 --dry-run
    if ($LASTEXITCODE -ne 0) { throw 'Rival route preflight refused; recalibrate, do not launch' }
    & $PY tools/polished_live/rival_swap_live.py --fixture 'F:/slink-work/lanes/pol-rival-live/fixture/rival.SaveRAM' --disclosure 'F:/slink-work/lanes/pol-rival-live/out/disclosure.json' --route $rivalRoute --out "$F/rival" --frames 18000 --timeout 600
    ```

    Evidence is `$F/rival/synth-<allocated>/receipt.json`, trace/result/wire and partner/input declarations; allocation is printed by the driver. Current retained PASS `F:/slink-work/lanes/pol-explode2/rival/synth-90mk5ijy/receipt.json`. The dry-run verifies the calibration transcript SHA against the driver's frozen overlay pin (`rival_swap_live.py:44-61`); absent calibration files are refused by the explicit runbook guard, even though the driver supports uncalibrated hand-authored routes. The historical `calib-68894579/synth-o562jtrj/probe/route.json` is not the new shipped-build input. A mismatched calibration requires a new route before the freeze.

19. **PRIVATE: explode_live's three cases, pending writer evidence (not yet LIVE ids).** Each invocation allocates a new owned lane and uses direct Entry.build rather than the shared launcher log. SYNTH fixture/logical link; TEST HOST handle_command; native wild route/action/faint. Not server capability/partner qualification.

    ```powershell
    Reset-PolEnv
    & $PY tools/polished_live/explode_live.py --case explode --fixture $fixture --route tools/polished_live/routes/faint_f3_route.json --lane "$F/explode" --timeout 600 --run
    & $PY tools/polished_live/explode_live.py --case active-faint --fixture $fixture --route tools/polished_live/routes/faint_f3_route.json --lane "$F/explode" --timeout 600 --run
    & $PY tools/polished_live/explode_live.py --case bench-faint --fixture $fixture --route tools/polished_live/routes/faint_f3_route.json --lane "$F/explode" --timeout 600 --run
    ```

    Evidence per case: `$F/explode/<case>-<allocated>/probe/oracle.json`, result.txt, input.json and trace.json. They may run concurrently with unique allocations after fixes/build freeze; the block is serial for clarity. Post-fix preflight PASS examples are `F:/slink-work/lanes/pol-explodefix/{explode-3ced0b5ae425,active-faint-eed2072c21ae,bench-faint-23649423f2f7}/probe/oracle.json`; re-run all three at the shipped frozen cut. An explode recording PASS is not enough: inspect Python oracle and absence of commanded-death echo. The echo fix is merged; require exactly one commanded-death outcome and suppression of its later native echo, not merely the Lua RESULT line.

20. **CLIENT SERIAL: duo S2n and S4n, separate jobs with two own EmuHawks each.** Native catches/paired link; SYNTH HP=1 conditioning and weak-action setup disclosed by the driver, then native battle faint/copyback/whiteout. Do not replace these with the HP=0/reconnect controls or call SYNTH-conditioned deaths unqualified PHYSICAL evidence.

    ```powershell
    Reset-PolEnv
    $env:POL_KIND='overlay'; $env:POL_DUO_LANE="$F/duo-s2n"; $env:POL_DUO_DEADLINE='900'
    & $PY tools/polished_live/duo.py --scenario faint-natural --distinct-identities --fixture-a $fixture --fixture-b $fixtureB
    $env:POL_DUO_LANE="$F/duo-s4n"
    & $PY tools/polished_live/duo.py --scenario whiteout-natural --distinct-identities --fixture-a $fixture --fixture-b $fixtureB
    ```

    Evidence: `$F/duo-s2n/play-faint-natural/evidence.json` and `$F/duo-s4n/play-whiteout-natural/evidence.json`, both sides' result/trace, wire, server log and SYNTH disclosures. Prior launches used `F:/slink-work/lanes/pol-live/fixture/polished_overlay_warp.SaveRAM` (same disclosed fixture) and `pol-ident/B.SaveRAM`; verify bytes instead of trusting aliases. S2n needs owner PHYSICAL qualification separately. S4n bar is whiteout event sent plus all linked partners dying via per-mon faint; it does not prove `_handle_whiteout` caused the deaths.

21. **TODO — `pol-shiptrade`: shipped-launcher trade plus native-save/cold CONTINUE.** The owner authorized shipping commit-enabled trade on 2026-10-09; land `pol-shiptrade` before freezing. At this document's source cut, `trade_duo_live.py:266-268` still exposes only the historical fixture/name/timeout interface and its private enabled test build. Do not run that dev-wrapper recipe as shipped qualification, and do not guess its replacement CLI.

    After `pol-shiptrade` lands, replace this TODO with its exact normal-launcher invocation, private lane inputs, artifact paths and judge. Verify production composition/hello advertisement with no dev opt-in override; both cartridges must be the shipped enabled overlay (or their declared contracted derivatives). Require native proposer/responder consent, one COMPLETE per side, server reconciliation, native SAVE, then cold CONTINUE with both received identities intact. Retain actual ROM hashes, command/lease wire, before/after party records, verdict.json and cold verdict. Missing cold evidence is NOT_RUN. Keep separately built commit-disabled controls as negative fixtures only; never make a private enabled-test hash equal the shipped hash in a receipt.

    This is a blocking TODO owned by `pol-shiptrade`, not a new enablement decision. Its landing may add/change LIVE IDs and closing requirements: refresh the manifest inventory before rebind. No release claim follows from the earlier private trade PASS.

22. **Rebind only judged new evidence, after all jobs finish.** Receipt writer records final HEAD/source hashes, `--print-code-digest`, provenance SHA256, executed ROM SHA1 (or source-overlay binding for R1/R2/R3), the new primary path/size/SHA256, secondary evidence and exact observed PASS checks. Preserve grade DEV; PHYSICAL is an owner act. Keep failed attempts; never overwrite their evidence or promote a recording PASS over a red oracle. Apply the exact criterion cutovers below, bind fresh memorialize e/f evidence, and resolve the named shiptrade TODO; do not silently omit rows or reuse obsolete checks. Any later digest-scoped edit revokes the cut and requires re-running affected receipts. Manifest/receipt/doc changes themselves are outside its code_digest_files.

23. **BUILD ALONE: final verifier, including BUILD-OVERLAY, then owner decision.** All emulators/private builds must have finished so the full gate cannot race their caches. First list confirms the LIVE census and closure ids; targeted runs are diagnostic only. Full verifier consumes every receipt and re-hashes its evidence; CLOSED needs its closing LIVE item to PASS in the same run.

    ```powershell
    Reset-PolEnv
    & $PY tools/verify_polished_release.py --list
    & $PY tools/verify_polished_release.py --json | Tee-Object "$F/verifier.json"
    ```

    `--only` and `--no-release` are not release verdicts; red is honest. Keep the six-shard logs, build logs and final verifier JSON with this cut. Vanilla Gen 2/C-5 qualification remains a separate gate (`docs/gen2/C5_RUNBOOK.md`), not implied by Polished solo/duo receipts. Owner ruling remains keep local: no master merge, push, release or lane deletion is authorised by a green runbook invocation.


## Criterion updates to apply at the rebind

This is an edit specification, **not a manifest or receipt update**. The exact old strings and line numbers below are from `tests/polished_release_requirements.json` at **4f7bd2fb1** (15 LIVE items). Re-read the manifest after `pol-shiptrade` merges; compare by item ID and exact value, not shifted line numbers, and reconcile any concurrent changes with its owner. Apply the replacements to the manifest and to the matching newly judged receipt check names together. Never satisfy a replacement by renaming an old PASS.

There are **18 replacement strings across 9 LIVE IDs**. The memorialize producer has landed; the two LIVE-WRITES-OVERWORLD strings still require fresh e/f evidence at the freeze (step 15), not re-stamping its retained recording. All other replacements describe the merged current behavior. Source references below are pinned to the stated source cut unless updated explicitly; the frozen source remains authoritative.

### LIVE-HELLO-ADMITTED

| Manifest line and exact current text | Exact replacement text | Source evidence |
|---|---|---|
| `225`: "exactly one hook registered" | "registered acquisition and battle sites match the frozen default composition; names and pin refusals are recorded" | `lua/gen2/entry.lua:828-833; lua/gen2/signals.lua:1396-1405,1735` |

At this cut the signal list is `capture_party`, `battle_faint`, `whiteout_before_heal`, `battle_faint_copyback_return`, `rival_swap_gate`; this is not a claim that only five emulator hooks exist. Separate client holds and future shipped trade hooks must be inventoried independently. Keep the hello-only zero-write requirement.

### LIVE-CAPTURE-PARTY-ONLY

| Manifest line and exact current text | Exact replacement text | Source evidence |
|---|---|---|
| `259`: "party grew by exactly one" | "native party grew by exactly one at the capture hook, before client quarantine" | `tools/polished_live/live.lua:31-37,355-361` |
| `263`: "no hits in any other bank" | "wrong-bank callbacks are excluded from qualified capture hits and emit no capture event" | `tools/polished_live/live.lua:30-32; lua/gen2/signals.lua:1668` |

There is no zero-write string in this manifest item to replace: that stale check was in the old driver/receipt narrative. Keep the existing escape, failed-throw and box-catch obligations; bind step 6's two runs and an actually observed failed-throw negative, not just a party-catch PASS. Record expected quarantine separately; exact write correctness is still the writes gate.

### LIVE-BOX-CENSUS

| Manifest line and exact current text | Exact replacement text | Source evidence |
|---|---|---|
| `269`: "a tick carries the box mon in pc_boxes without a reboot" | "after a native full-party catch from a declared six-mon fixture, a tick carries the caught box mon in pc_boxes without a reboot" | `tools/polished_live/live.lua:368-406` |
| `274`: "a wild mon reached a box" | "a native full-party catch reached a box; the six-mon fixture was prepared by native catch and SAVE without a client or RAM party edits, hashed, and reloaded" | `tools/polished_live/live.lua:368-393; tools/polished_live/harness.py:221-225` |

The original refresh/without-reboot criteria remain. Only the preparation changes: native party-to-PC quarantine is not a substitute for this native full-party catch. The fixture preparation and subsequent reload are separate from the same-session catch-to-census interval.

### LIVE-R2-RANDOMIZED-BOOT

| Manifest line and exact current text | Exact replacement text | Source evidence |
|---|---|---|
| `315`: "the server presents ROM a's own table" | "the server presents the executed player's own cartridge table" | `tools/polished_live/harness.py:233-244,274; server/adapters/gen2_polished.py:435-457` |
| `319`: "party grew by exactly one" | "native party grew by exactly one at the variant capture hook, before client quarantine" | `tools/polished_live/live.lua:437-456` |
| `320`: "0 Lua-originated writes in both legs" | "observation-only leg has zero Lua writes; variant-catch quarantine writes are retained separately and do not count as write-path qualification" | `tools/polished_live/live.lua:466-475` |

### LIVE-R3-REFUSALS

| Manifest line and exact current text | Exact replacement text | Source evidence |
|---|---|---|
| `328`: "a swapped contract and two flipped bytes are refused, by the server and by the client respectively" | "a swapped contract and a global-checksum mutation are refused by the server; an overlay-beacon mutation is refused by the client" | `tools/gen_polished_beacon.py:62-69; lua/gen2/polished.lua:101-120; tools/polished_live/harness.py:233-238` |
| `335`: "a flipped byte 0x14E is refused by the client" | "a flipped byte 0x14E is admitted as rand_overlay by the client and refused by the server against the original full-ROM contract SHA1" | `tools/gen_polished_beacon.py:62-69; server/server.py:874-893` |

Retain the unchanged `$1F8028` client refusal and no-hello requirement, provided that offset remains beacon-covered after rebuild. Preserve original Manager files and both refused-run logs. A harness RESULT FAIL caused by intentional client refusal is expected; it must be judged from the named refusal plus absent hello, not relabeled by exit code alone.

### LIVE-PHONE-ENTRY

| Manifest line and exact current text | Exact replacement text | Source evidence |
|---|---|---|
| `357`: "Call reaches SlinkPhone_CallGate and reads 'SLink is linked.'" | "Call reaches SlinkPhone_CallGate and SlinkPanel and renders 'SOUL LINK' / 'NO CLIENT' on the no-host path" | `tools/polished_live/phone.lua:105-112; patch/polished/src/panel.asm:149-152` |

### LIVE-RECEPTIONIST-STACK

| Manifest line and exact current text | Exact replacement text | Source evidence |
|---|---|---|
| `369`: "the trade receptionist's frame-wait stack, measured to the native link wait" | "the trade receptionist's bank-qualified frame-wait stack, measured through the overlay service and its return" | `tools/polished_live/explore.lua:49-50,75-109; patch/polished/src/trade_gate.asm:28-43` |
| `375`: "the native link wait ran and timed out (no cable)" | "the overlay proposer service entered and returned without a host; the trade gate skipped the native cable wait" | `tools/polished_live/explore.lua:97-98` |
| `378`: "native link wait chain" | "overlay host-wait stack samples decoded against the frozen symbols with bank and phase recorded" | `tools/polished_live/explore.lua:49-62,99-109; tools/polished_live/harness.py:299-312` |
| `379`: "only sp+2 varies in the link wait" | "stack variation is recorded per overlay phase; no historical native-link-wait offset invariant is assumed" | `tools/polished_live/explore.lua:58-62,99-109` |

Retain idle-overworld control and its decoded chain. This replaces the obsolete trade-side native wait assertion only; it does not claim a cable connection, actual commit, or a native battle-receptionist control.

### LIVE-WRITES-OVERWORLD

| Manifest line and exact current text | Exact replacement text | Source evidence |
|---|---|---|
| `417`: "force_faint (bench), box_mon and party_mon through the client's own command path, each an exact-diff subset with a failing control" | "force_faint (bench), box_mon, party_mon and both party-origin and box-origin memorialize through the client's own command path, each an independently checked exact-diff subset with a failing control" | `tests/polished_release_requirements.json:529-537; docs/polished/MEMORIALIZE.md; tools/polished_live/writes.lua` legs e/f (step 15) |
| `432`: "independent recompute of the three diffs equals the driver's" | "independent recompute of the force_faint, box_mon, party_mon and both memorialize diffs equals the driver's" | `tests/polished_release_requirements.json:529-537; tools/polished_live/writes.lua` legs a/b/d/e/f (step 15) |

**Pending freeze evidence; do not apply as fulfilled yet.** The landed e/f producer judges destination box 20, source removal, non-collateral bytes, failing diff controls and the client's wire acknowledgements. Preserve those individual checks and the existing a/b/d checks when binding the fresh recording. Resolve the closing LIVE ID with the manifest owner; OPEN-MEMORIALIZE remains open until its frozen receipt is accepted. The RC bar is owner ruling #7's parity best-effort, not the post-RC owed-burial/three-outcome contract.

### LIVE-PANEL-HOST-PAGING

| Manifest line and exact current text | Exact replacement text | Source evidence |
|---|---|---|
| `480`: "three pages rendered, each line equal to the row the client staged" | "all advertised pages rendered, each line equal to the staged row and every server row fits the 16-glyph panel without unsupported separators" | `tools/polished_live/panel_host_live.lua:243-275,298-309; tools/polished_live/panel_host_live.py:76-86,225-226` |

The existing Python `>= 2` page check is a lower bound, not proof of the replacement's full coverage. At rebind, verify recorded page indexes cover `1..pages`, per-page staged/rendered equality, and readable row widths from the trace; missing advertised pages cannot PASS. Do not convert the historical fixed count of three into a claim about a newly generated page count.


The remaining six LIVE IDs have no criterion text replacement identified at this cut: LIVE-HELLO-GATE-FRAME, LIVE-R1-MANAGER-PAIR, LIVE-POKEGEAR-MEASUREMENT, LIVE-TITLE-SPLASH, LIVE-PANEL-PAGES-ROM and LIVE-PANEL-HELLO. They still need fresh execution and exact evidence checks. Title and both panel halves remain separate steps 5/14/16/17; S2n/S4n, all three explode cases, memorialize and shipped trade remain steps 15/19/20/21, even where no new LIVE ID has yet been assigned. Reconcile new IDs from the owning cards before the final `--list` census; this table does not waive them.
