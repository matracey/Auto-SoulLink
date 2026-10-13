# Closing C-5 — randomized Gen 2 admission: the gate plan

**Card: a plan from reading. No live run, no emulator, no sweep was executed.** Every fact below
cites `file:line`; anything I could not settle from source is marked **UNVERIFIED**.

Requirement **C-5** — *"Randomized admission (if UPR extends to Gen 2; else a recorded limit)"* —
is opened by the owner (2026-10-03). UPR now **does** extend to Gen 2 on this branch, so the
"recorded limit" branch is no longer the applicable one; the row is `conditional` and needs its
declared evidence layers satisfied.

---

## 1. What the verifier demands for C-5 today

### 1.1 The row's declared shape

`docs/gen2/gen2_requirements.md:126`:

```
| C-5 | Randomized admission (if UPR extends to Gen 2; else a recorded limit) | SERVER | · | · | · |
```

All three evidence cells are `·` — open. The oracle column reads **SERVER**.

`docs/gen2/gen2_coverage_map.md:1370-1400` holds the machine record:

| Field | Value | Line |
|---|---|---|
| `id` | `requirement:C-5` | `:1371` |
| `required_layers` | `["SOURCE", "PHYSICAL"]` | `:1372-1375` |
| `mapping.status` | `MAPPED` | `:1376` |
| `mapping.oracle` | "SERVER admission/refusal and actual hash/anchor result; signed deferred disposition is **not** randomized-behavior proof" | `:1388` |
| `mapping.receipt_marker` | `gen2.requirement.C-5` | `:1390` |
| `mapping.lane` | `release-evidence` | `:1391` |
| `evidence.SOURCE.status` | `OPEN` | `:1394-1396` |
| `evidence.PHYSICAL.status` | `OPEN` | `:1397-1399` |

So **C-5 needs SOURCE + PHYSICAL — not MODEL.** That is deliberate and matches Gen 1: the Gen 1
digest records *"C-5 randomized admission (S✓ M✓ P✓ from `admit_randomized_new`)"*
(`docs/gen2/GEN1_STANDARD_DIGEST.md:121`) — Gen 1 carries MODEL as well, but Gen 2's own row does
not declare it.

### 1.2 How a conditional row is judged

Three separate statements, and they are the ones that decide the work:

1. `tools/verify_gen2_release.py:79-85` — `CONDITIONAL_OR_DEFERRED` is **a scope description, "not a
   signed disposition"** (`:78`). Its C-5 text is *"conditional randomized admission; no admission
   without an enabling ruling"*. **It is not itself an evaluation**: grepping the file, the dict is
   defined and never read by any check. Closure is therefore decided elsewhere.
2. `docs/gen2/GEN2_BINDING_PLAN.md:373` (P6.3) — *"Closure is read per row against that row's own
   declared evidence layers, **not against a uniform S+M+P**."* So C-5 closes on SOURCE + PHYSICAL.
3. `docs/gen2/GEN2_BINDING_PLAN.md:508` — *"disabled conditional rows carry their signed disposition
   instead"*. A disabled conditional is closed by an **owner-signed disposition**, not by evidence.
   `:19` of `gen2_coverage_map.md` repeats it: *"W-3/W-4/C-5/D-11 remain default-disabled/deferred
   without an enabling ruling… OPEN cells do not fabricate signed applicability records."*

**This is the fork in the road.** C-5 today is closable two ways:

- **(A) Signed disposition** — the owner signs that randomized Gen 2 is out of scope for this
  release. Cheap, needs no evidence, but it is *false* now that the branch admits randomized carts.
- **(B) Enable and evidence** — the owner signs the enabling ruling, the row becomes an enabled
  conditional, and it must then produce SOURCE + PHYSICAL receipts.

Only (B) actually supports what the branch does. This document plans (B).

### 1.3 What makes a row "closed" vs a "recorded limit"

From `:508` and `GEN2_BINDING_PLAN.md:486` (`| C-5 | P3b.3 | **conditional** — UPR on Gen 2, else a
recorded limit |`):

- **Closed** = an enabled row whose `required_layers` are each satisfied by an accepted receipt
  carrying `mapping.receipt_marker = "gen2.requirement.C-5"`, in the `release-evidence` lane.
- **Recorded limit** = a disabled conditional carrying a signed disposition; the evidence cells stay
  `OPEN` and the reason stays "await signed applicability/disposition". It is explicitly *not* a
  behaviour pass (`gen2_coverage_map.md:1388`).

---

## 2. What the branch already has, and the PHYSICAL obligations that remain

### 2.1 Already present (SOURCE / MODEL — unit level)

`docs/gen2/RANDOMIZER.md` records the branch's randomized work:

| Card | What it established | Row |
|---|---|---|
| R3 (`28553f18`) + fixes (`3a832591`) | `rand_overlay` admission in `lua/gen2/entry.lua`; adapter kind; server binds the hello to the contract's `rom_sha1` | C-5 SOURCE |
| R6 (`9e970de3`) | Overlay beacon: `tools/gen_gen2_beacon.py` writes `data/games/gen2_<title>/overlay/beacon.json`; the Lua gate re-hashes every overlay byte | C-5 SOURCE |
| R4 (`e977b01d`) + `c1c1c400` + `a447e287` | `lua/gen2/signals.lua` reads species/level/item from the ROM at the vanilla site; roamers read `InitRoamMons` immediates; the server decodes each provisioned ROM with `gen2_rom_scan` unpinned | C-5 SOURCE |

The unit tests that carry these are `tests/unit/test_gen2_rand_admission.py` and
`tests/unit/test_gen2_rand_data.py` (`ls tests/unit/ | grep rand`). R4's own check is stated in
`RANDOMIZER.md:358`: *"Checked against the UPR logs on real randomized ROMs: starters 4/4, statics/gifts
24/24, 24/24, 21/21, 21/21, roamers all match."*

**These are SOURCE/MODEL. None is a PHYSICAL receipt**, and `gen2_coverage_map.md:1394-1399` keeps
both `SOURCE` and `PHYSICAL` `OPEN` regardless — the SOURCE cell says *"Disabled status is not a
behavior PASS."*

> **Judgment (not source):** the SOURCE leg looks substantially satisfied already; the open work is
> dominated by PHYSICAL. I would not re-litigate SOURCE except to confirm the beacon and ROM-size
> floor are covered by a test that would fail if they were removed.

### 2.2 The PHYSICAL obligations — one per gate/scenario

Gen 1's positive control is a **duo scenario** named `admit_randomized_new`
(`tests/e2e/test_duo_gen1_new.py:97`), with receipts at
`tests/fixtures/gen1/receipts/e2e_admit_randomized_new_{a,b}_result.txt`.

Gen 2 already has the *negative* half: `gen2_admit_wrong_rom` is in
`tests/e2e/test_duo_gen2_new.py:22`'s `SCENARIOS`, in `tools/e2e_duo.py:184-186`, with oracle
`assert_gen2_admit_wrong_rom` (`tools/e2e_duo.py:5309`) and the refusal driven at
`tools/e2e_duo.py:3095` (instance `b` is refused). It is also in the release verifier's mandatory set
(`tools/verify_gen2_release.py:1829`, `DUO_PAIRS_SCENARIOS`).

So Gen 2 has **refusal** but no **admission**. The live obligations:

| # | Gate | What it proves | Where it goes | Run count |
|---|---|---|---|---|
| G-a | `admit_randomized_crystal` | a randomized Crystal companion overlay (`rand_overlay`) is admitted end-to-end on a real cartridge; `links.json`/party reflect it | new scenario in `tests/e2e/test_duo_gen2_new.py` + `tools/e2e_duo.py` (alongside `gen2_admit_wrong_rom:184`) | 1 instance (a) |
| G-b | `admit_randomized_gold`, `admit_randomized_silver` | same for the other two titles — `TITLES = ("crystal","gold","silver")` (`verify_gen2_release.py:61`) | same | 2 more instances |
| G-c | `admit_randomized_wrong_rom` | a randomized cart whose sha1 is not the contract's is refused | extend `gen2_admit_wrong_rom` with a randomized variant, or add a sibling | 1 instance (b) |
| G-d | wrong-save under a randomized contract | the player-identity lock still refuses a foreign OT ID on a randomized cart | extend `gen2_reconnect`/identity scenario | 1 |
| G-e | randomized duo link/catch on a **ROM-derived static** | the R4 path (`signals.lua` reading species from ROM) links correctly against a partner cart | new duo scenario beside `gen2_gift` | 1 (needs a second random seed so the two carts differ) |
| G-f | randomized duo gift | `givepoke` species/level/item read from ROM bytes | new, beside `gen2_gift` | 1 |
| G-g | randomized roamer | `InitRoamMons` immediate read; legend area named for the ROM's species | new | 1 |
| G-h | randomized NPC trade | key migration across a trade on a randomized cart | beside `gen2_npc_trade` | 1 |
| G-i | randomized contract binding: no contract ⇒ refuse | the `rom_contract_by_sha1` refusal is PHYSICAL, not just unit | extend G-a | 0 (same run) |

**Estimated PHYSICAL run count: ~10 scenarios × 2 instances, run on the pairs the verifier requires.**
`tools/verify_gen2_release.py:195` requires *"every P3b.7 scenario (DUO_PAIRS_SCENARIOS…)"* on the
**C-C and G-S** matrix (`:192`), so a randomized scenario added to `DUO_PAIRS_SCENARIOS` is charged
at least twice. **Budget: ~20–24 randomized duo instances**, plus the live-gate receipts.

### 2.3 Fixture and synthetic-setup rules (O-33)

`tools/gen2_fixtures.py` supplies **played** fixtures; `tools/gen2_synth_fixtures.py` supplies
**O-33 synthetic SETUP** fixtures — its docstring (`:1`) says *"O-33 synthetic SETUP fixtures for Gen 2
(docs/gen2/REVIEW_RECORD.md O-33): a qualified played save with a few…"*, and `:28` records that
*"a synthetic faint is never handed to the game pre-fainted."*

**UNVERIFIED — I could not find O-33's text** in `docs/gen2/PLAN.md` (grep returned nothing). What
the two files imply, and must be honoured: a randomized gate **must start from a qualified played
save**, patched/edited only where O-33 permits, and must not fabricate the event under test. The
randomized cartridge itself must come from the **Manager** (`docs/gen2/RANDOMIZER.md`: only the
Manager makes randomized Gen 2 carts; the server refuses a `rand_overlay` hello in a run with no
contract), so the fixture cannot be synthesised freely — it is provisioned, with a contract sha1.
**Settling O-33's exact text is the first thing to do before writing a gate.**

`OVERLAY_QUALIFICATIONS` (`tools/verify_gen2_release.py:64-68`) pins 13 played fixtures and says
*"Pin the census independently of the manifest so deleting a row cannot waive it"* (`:63`). **A
randomized gate needs its own entry here, or its absence will not be noticed.**

---
## 3. Code changes the verifier / ledger itself needs (a list, not edits)

**None of these are written; this section is the scope the coordinator must approve.**

1. **A new fixture kind / qualification census.** `OVERLAY_QUALIFICATIONS`
   (`tools/verify_gen2_release.py:64-68`) lists 13 *played* fixtures. Randomized admission needs a
   **provisioned-randomized** fixture per title (Crystal/Gold/Silver) that is pinned the same way,
   plus its contract sha1. Deciding whether this is a new census tuple or a new `fixture_kind`
   affects `gen2_fixtures.py` / `gen2_synth_fixtures.py` and the O-33 disclosure text.
2. **New duo scenarios in three places, or one place.** A scenario is not a name: it must appear in
   `tests/e2e/test_duo_gen2_new.py`'s `SCENARIOS` (`:22`), in `tools/e2e_duo.py`'s scenario table
   (the `gen2_admit_wrong_rom` entry at `:184-186` is the template — flags, timeout, games, oracle,
   oracle_kwargs), **and** in `DUO_PAIRS_SCENARIOS` (`tools/verify_gen2_release.py:1825-1830`) to be
   *required*. Adding it to the test file without the third list makes it optional and silently
   unverified — the same "silently waived" hazard `:63` calls out.
3. **An oracle per new scenario.** `tools/e2e_duo.py` needs e.g. `assert_gen2_admit_randomized`,
   plus the witness branch (`:5086`, `:5092` show how `gen2_admit_wrong_rom` selects
   `admit_wrong_rom_oracle` / `check_admit_wrong_rom_witness` instead of the save witness) and the
   refusal branch (`:3095`).
4. **A receipt kind in the release-evidence lane.** C-5's `receipt_marker` is
   `gen2.requirement.C-5` (`gen2_coverage_map.md:1390`) and its lane is `release-evidence`
   (`:1391`). Something must emit a receipt under that marker with `evidence_level: PHYSICAL`. The
   verifier already refuses non-PHYSICAL receipts for several obligations
   (e.g. `:1987-1988` requires `evidence_level == "PHYSICAL"` and
   `receipt["test"] == "tests/live/test_gen2_new_gates.py"` for the inspect attestation) — the
   randomized receipt must satisfy whatever that check becomes.
5. **The coverage-map row must move off `OPEN`.** `docs/gen2/gen2_coverage_map.md:1394-1399` keeps
   both cells `OPEN` with the reason *"Await signed applicability/disposition at P6."* Once the owner
   enables, the row's `evidence` blocks need accepted receipts — and the row's **stimulus**
   description (`:1379-1381`) still reads *"With randomized admission disabled…"* and *"no UPR
   extension… inferred"*, which is now false and must be rewritten, not just re-scored.
6. **`docs/gen2/gen2_requirements.md:126`** must change from `· · ·` to the achieved cells.
7. **CONDITIONAL_OR_DEFERRED is inert.** `tools/verify_gen2_release.py:78-85` is a description only
   (nothing reads it). Either leave it as commentary or make it load-bearing; **as it stands, an
   owner ruling is recorded nowhere machine-checkable.** I would *not* change its semantics in this
   card — but the coordinator should know the enabling ruling has no mechanical enforcement today.
8. **The required-physical count.** `GEN2_BINDING_PLAN.md:508` says the REQUIRED-physical count is
   derived from applicability at each gate (41 at the first G6). Enabling C-5 changes that
   arithmetic, and whichever row holds the count must be re-derived.

**CODE_DIGEST scope note — UNVERIFIED.** I was asked to comment on it and could not establish it:
`grep CODE_DIGEST tools/verify_gen2_release.py` finds no such symbol in this file, and I did not
locate it in `tools/release_lanes.py`. If it exists in the shared core, the question it raises is
real — a randomized ROM is not reproducible from the clean sources plus a seed inside SLink's
digest, so any CODE_DIGEST claim that covers the *executed* cartridge's bytes would be false for
`rand_overlay`. **Settle by reading `tools/release_lanes.py` before P6.3.**

---

## 4. The minimal ordered plan, and what the owner must sign

### 4.1 Ordered plan

| Step | Action | Gate | Owner sign-off needed |
|---|---|---|---|
| 0 | Read O-33's actual text (`docs/gen2/REVIEW_RECORD.md`) and settle the randomized-fixture disclosure rule | — | no |
| 1 | **Owner signs the C-5 enabling ruling** (or a disposition — see below) | — | **YES — blocking** |
| 2 | Add one randomized duo scenario end-to-end on Crystal (`admit_randomized_crystal`): test file + `e2e_duo.py` oracle/witness + `DUO_PAIRS_SCENARIOS` | — | no |
| 3 | Generalise to Gold and Silver (G-b) | — | no |
| 4 | Add the refusal control `admit_randomized_wrong_rom` (G-c) and the wrong-save control (G-d) | — | no |
| 5 | Add the four ROM-derived behaviour scenarios (G-e…G-h) | — | no |
| 6 | Provision the randomized fixture census and pin it in `OVERLAY_QUALIFICATIONS` (3.1) | — | no |
| 7 | Emit `gen2.requirement.C-5` PHYSICAL receipts in the `release-evidence` lane (3.4) | — | no |
| 8 | Re-score the coverage map (3.5), `gen2_requirements.md:126` (3.6), and the required-physical count (3.8) | P6.3 | no |
| 9 | Settle the CODE_DIGEST scope note | P6.3 | **YES if it claims executable bytes** |

**Step 1 is blocking and nothing else can start honestly without it.** The whole rest of the plan is
mechanical once randomized Gen 2 is a supported release configuration rather than a deferred
conditional.

### 4.2 What the owner must sign (G-row text)

Two mutually exclusive rulings. Both are one sentence; only (B) supports the branch as it stands.

> **G-1 (enable).** "Randomized Gen 2 companion cartridges (`rand_overlay`, provisioned by the
> Manager) are a supported release configuration for Polished-era Gen 2. C-5 is enabled and must
> close on SOURCE + PHYSICAL evidence (`docs/gen2/gen2_coverage_map.md:1372-1375`); a `rand_overlay`
> hello without a Manager contract is refused, and a cart whose sha1 is not the contract's is
> refused."

> **G-2 (disposition, alternative).** "Randomized Gen 2 is out of scope for this release. C-5 closes
> as a recorded limit by this signed disposition, its evidence cells stay `OPEN`, and the
> `rand_overlay` route must be refused at admission until a later enabling ruling."
> **Consequence:** with (G-2), R3/R4/R6 and the beacon are shipped dark — the branch would have to
> *withdraw* `rand_overlay` admission rather than merely not qualify it. That is a real cost and is
> why I plan (G-1).

### 4.3 The one risk worth naming

The cheapest-looking path to a green row is a **signed disposition** (G-2). It requires no evidence
and would close C-5 on paper while the branch actually admits randomized carts. The coverage map
already forbids the degenerate version of this — *"OPEN cells do not fabricate signed applicability
records"* (`gen2_coverage_map.md:19`) — but a signed disposition is exactly the mechanism that makes
a disabled conditional close, so the ruling's *wording* is the whole safety margin here. I'd have the
ruling state which configuration SLink ships, not merely whether randomized admission is "supported".

## 5. What I could not settle

- **O-33's exact text** (not found in `docs/gen2/PLAN.md`); §2.3 infers the rule from the two
  `gen2_fixtures*.py` docstrings.
- **CODE_DIGEST** — no such symbol in `tools/verify_gen2_release.py`; not read in
  `tools/release_lanes.py`.
- Whether the `release-evidence` lane has a generic receipt emitter that C-5 can reuse, or whether
  C-5 needs its own (§3.4). I did not trace that lane's writers.
- The exact G6 required-physical arithmetic that enabling C-5 changes (`GEN2_BINDING_PLAN.md:508`
  states the rule; I did not read the count's implementation).
- Whether `Gen 2` randomized admission is expected for **all three titles** or only where UPR
  applies; `:61` gives `TITLES` for the verifier, but no per-title applicability list for C-5.

## 6. Machine-checkable citations

Each entry names an absolute path, a line and an exact substring on that line.

```json CLAIMS
[
 {
  "path": "F:/slink-work/wt/g2-rand/tools/verify_gen2_release.py",
  "line": 61,
  "expect": "TITLES = (\"crystal\", \"gold\", \"silver\")"
 },
 {
  "path": "F:/slink-work/wt/g2-rand/tools/verify_gen2_release.py",
  "line": 63,
  "expect": "# Pin the census independently of the manifest so deleting a row cannot waive it."
 },
 {
  "path": "F:/slink-work/wt/g2-rand/tools/verify_gen2_release.py",
  "line": 82,
  "expect": "\"C-5\": \"conditional randomized admission; no admission without an enabling ruling\","
 },
 {
  "path": "F:/slink-work/wt/g2-rand/tools/verify_gen2_release.py",
  "line": 1829,
  "expect": "\"gen2_admit_wrong_rom\","
 },
 {
  "path": "F:/slink-work/wt/g2-rand/tools/verify_gen2_release.py",
  "line": 196,
  "expect": "every P3b.7 scenario (DUO_PAIRS_SCENARIOS; shiny_bonus is the O-26 recorded limit) plus"
 },
 {
  "path": "F:/slink-work/wt/g2-rand/tools/verify_gen2_release.py",
  "line": 192,
  "expect": "PHYSICAL receipts: the C-C, G-S and C-G link matrix of tests/gen2_release_requirements.json;"
 },
 {
  "path": "F:/slink-work/wt/g2-rand/tools/verify_gen2_release.py",
  "line": 1987,
  "expect": "receipt.get(\"test\") != \"tests/live/test_gen2_new_gates.py\""
 },
 {
  "path": "F:/slink-work/wt/g2-rand/tools/verify_gen2_release.py",
  "line": 228,
  "expect": "\"release-evidence\": list(REQUIREMENT_IDS),"
 },
 {
  "path": "F:/slink-work/wt/g2-rand/docs/gen2/gen2_requirements.md",
  "line": 126,
  "expect": "| C-5 | Randomized admission (if UPR extends to Gen 2; else a recorded limit) | SERVER |"
 },
 {
  "path": "F:/slink-work/wt/g2-rand/docs/gen2/gen2_coverage_map.md",
  "line": 19,
  "expect": "W-3/W-4/C-5/D-11 remain default-disabled/deferred without an enabling ruling."
 },
 {
  "path": "F:/slink-work/wt/g2-rand/docs/gen2/gen2_coverage_map.md",
  "line": 124,
  "expect": "| requirement:C-5 | SOURCE + PHYSICAL | MAPPED | P3b.3/P6.3 conditional | release-evidence |"
 },
 {
  "path": "F:/slink-work/wt/g2-rand/docs/gen2/gen2_coverage_map.md",
  "line": 1371,
  "expect": "\"id\": \"requirement:C-5\","
 },
 {
  "path": "F:/slink-work/wt/g2-rand/docs/gen2/gen2_coverage_map.md",
  "line": 1390,
  "expect": "\"receipt_marker\": \"gen2.requirement.C-5\","
 },
 {
  "path": "F:/slink-work/wt/g2-rand/docs/gen2/gen2_coverage_map.md",
  "line": 1046,
  "expect": "\"lane\": \"release-evidence\""
 },
 {
  "path": "F:/slink-work/wt/g2-rand/docs/gen2/gen2_coverage_map.md",
  "line": 1389,
  "expect": "signed deferred disposition is not randomized-behavior proof."
 },
 {
  "path": "F:/slink-work/wt/g2-rand/docs/gen2/gen2_coverage_map.md",
  "line": 1051,
  "expect": "Disabled status is not a behavior PASS."
 },
 {
  "path": "F:/slink-work/wt/g2-rand/docs/gen2/GEN2_BINDING_PLAN.md",
  "line": 373,
  "expect": "Closure is read per row against that row's own declared evidence layers"
 },
 {
  "path": "F:/slink-work/wt/g2-rand/docs/gen2/GEN2_BINDING_PLAN.md",
  "line": 508,
  "expect": "disabled conditional rows carry their signed disposition instead"
 },
 {
  "path": "F:/slink-work/wt/g2-rand/docs/gen2/GEN2_BINDING_PLAN.md",
  "line": 486,
  "expect": "| C-5 | P3b.3 | **conditional** \u2014 UPR on Gen 2, else a recorded limit |"
 },
 {
  "path": "F:/slink-work/wt/g2-rand/docs/gen2/GEN2_BINDING_PLAN.md",
  "line": 508,
  "expect": "41 at the first G6"
 },
 {
  "path": "F:/slink-work/wt/g2-rand/docs/gen2/GEN1_STANDARD_DIGEST.md",
  "line": 122,
  "expect": "C-5 randomized admission (S\u2713 M\u2713 P\u2713 from `admit_randomized_new`)"
 },
 {
  "path": "F:/slink-work/wt/g2-rand/tests/e2e/test_duo_gen1_new.py",
  "line": 97,
  "expect": "\"admit_randomized_new\", \"soft_reset_new\", \"trade_decline_new\", \"explode_new\","
 },
 {
  "path": "F:/slink-work/wt/g2-rand/tests/e2e/test_duo_gen2_new.py",
  "line": 22,
  "expect": "\"gen2_egg_hatch\", \"gen2_npc_trade\", \"gen2_evolution\", \"gen2_ball_gate\", \"gen2_faint_active\", \"gen2_faint_active_trainer\", \"gen2_admit_wrong_rom\","
 },
 {
  "path": "F:/slink-work/wt/g2-rand/tools/e2e_duo.py",
  "line": 184,
  "expect": "\"gen2_admit_wrong_rom\": {\"flags\": [], \"timeout\": 1200, \"games\": (\"gen2_new\",),"
 },
 {
  "path": "F:/slink-work/wt/g2-rand/tools/e2e_duo.py",
  "line": 5309,
  "expect": "def assert_gen2_admit_wrong_rom(self, results, **kwargs):"
 },
 {
  "path": "F:/slink-work/wt/g2-rand/tools/e2e_duo.py",
  "line": 3095,
  "expect": "refused = scenario == \"gen2_admit_wrong_rom\" and inst == \"b\""
 },
 {
  "path": "F:/slink-work/wt/g2-rand/tools/e2e_duo.py",
  "line": 5092,
  "expect": "witness = \"check_admit_wrong_rom_witness\" if self.scenario == \"gen2_admit_wrong_rom\""
 },
 {
  "path": "F:/slink-work/wt/g2-rand/tools/gen2_synth_fixtures.py",
  "line": 1,
  "expect": "O-33 synthetic SETUP fixtures for Gen 2"
 },
 {
  "path": "F:/slink-work/wt/g2-rand/tools/gen2_synth_fixtures.py",
  "line": 28,
  "expect": "never handed to the game pre-fainted"
 },
 {
  "path": "F:/slink-work/wt/g2-rand/docs/gen2/RANDOMIZER.md",
  "line": 355,
  "expect": "## ROM-derived data (R4, 2026-10-04)"
 },
 {
  "path": "F:/slink-work/wt/g2-rand/docs/gen2/RANDOMIZER.md",
  "line": 359,
  "expect": "starters 4/4, statics/gifts"
 }
]
```


---

## C-5 evidence status (2026-10-04, coordinator)

Run by `tools/c5_runner.py` on real BizHawk, randomized overlay ROMs from the real Manager pipeline (pinned jar db4bc65c),
digest `e5b0c5c6` at lane commit `e2620f64` + `3c2f504f`. **10 of the 11 planned cells PASS, each on its first attempt**
(three reds along the way were oracle/runner assumptions, each diagnosed and fixed before any rerun):

| cell | gate | result |
|---|---|---|
| c5/cc/link, c5/gs/link, c5/cg/link | G-a, G-b | PASS (rand_overlay at the contract sha1 on both sides) |
| c5/cc/gen2_reconnect, c5/gs/gen2_reconnect | G-d | PASS (same_save and wrong_save legs) |
| c5/cc/gen2_gift, c5/gs/gen2_gift | G-e, G-f | PASS (Bill's species read from the contracted ROM) |
| c5/ct/gen2_npc_trade | G-h | PASS (Kyle's request/OT read from the ROM; pair provisioned trades=given) |
| c5/cc/gen2_c5_wrong_rom | G-c | PASS (server refused B: 'this is not the ROM built for player b') |
| c5/cc/gen2_c5_no_contract | G-i | PASS (both sides refused: no randomized-ROM contract) |

**G-g (randomized roamer): owner ruling 2026-10-04 = NAMED LIMIT, fail-safe.** The overlay client registers no
`roamer_party_finalized` / `roamer_box_finalized` site on ANY cart (vanilla included; they are not among the 27 sites proven by
the PHYSICAL overlay receipts), so `lua/gen2/signals.lua` cannot classify a roamer catch on an overlay cart, randomized or not.
Same-species roamer slots already fail safe. C-5 closes on the 10 passing cells.

Open: (1) the evidence is at a digest that moves when the Polished lane merges (`lua/gen2/**`, `server/**` edits); re-run
`python tools/c5_runner.py run` after the merge (resumable, about 20 minutes); (2) `tools/verify_gen2_release.py` **now has a `--c5` reader** (`c5_errors()`, `:2368`) for the
`gen2-c5-duo-cell-v1` manifests -- **SUPERSEDED 2026-10-04**. Still open: `--c5` is not one of the `LANES` (`:126`)
and is not called from `release_evidence_errors` (`:2090`), and the receipts are not installed under
`tests/fixtures/gen2/receipts/c5/`. The duo checks also still pin the PUBLISHED overlay sha1 (`:1113`), so these
receipts still cannot live in `tests/gen2_release_requirements.json`; (3) the receipts are PHYSICAL with SYNTH/HARNESS/NATIVE disclosures per cell.

### Admission optimization equivalence

The C-5 verifier can reuse physical receipts only across the exact `lua/admission.lua` source-blob pair pinned in `tools/gen2_code_digest.py`. All other production rows remain bound, and reuse requires a clean production tree. The differential Lua test supplies SOURCE/MODEL equivalence, not a fresh physical run. Receipts and raw runtime digests remain unchanged.
