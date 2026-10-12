"""C-5 (randomized Gen 2 admission) PHYSICAL runner: provision randomized pairs, then run duo scenarios resumably.

    python tools/c5_runner.py provision                 # the three randomized pairs (cc, gs, cg) via the Manager path
    python tools/c5_runner.py prove                     # contract sha1 == file, Lua admission (rand_overlay + beacon)
    python tools/c5_runner.py list                      # the cells, READY or BLOCKED (with the exact gap)
    python tools/c5_runner.py run [--only ID ...]       # every READY cell not yet PASS at this digest, one duo at a time
    python tools/c5_runner.py status | clean | selfcheck

Everything lives under F:/slink-work/lanes/g2r-live (SLINK_C5_ROOT): short, non-Drive, BizHawk MAX_PATH safe.
A cell runs the lane's own tools/e2e_duo.py overlay scenario through `_shim`, which swaps each instance's executed
cartridge for the Manager-provisioned randomized ROM and puts the Manager's rom_contract.json + roms/ in the server's
data dir. Resumable: out/<digest12>/summary.json; a cell PASSed at the same code digest AND the same contract sha1s is
skipped. Exactly one duo (two EmuHawk) at a time; a timeout kills only this runner's own process tree.
See docs/gen2/C5_RUNBOOK.md.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
C5_ROOT = Path(os.environ.get("SLINK_C5_ROOT", "F:/slink-work/lanes/g2r-live"))
ROMS = C5_ROOT / "roms"
# The pinned pret builds (lock-verified below). This worktree has no .cache; the Gen 2 coordinator tree holds them.
GEN2_BUILD = Path(os.environ.get(
    "SLINK_C5_GEN2_BUILD", "E:/Google Drive/SLink/.claude/worktrees/mandatory-rom-patch-3fcfda/.cache/gen2-build"))
JAR = Path(os.environ.get("SLINK_UPR_JAR", "E:/Google Drive/SLink/.cache/slink-upr/PokeRandoZX.jar"))
PAIRS = {"cc": ("crystal", "crystal"), "gs": ("gold", "silver"), "cg": ("crystal", "gold"),
         "ct": ("crystal", "crystal")}   # ct: trades=given, so Kyle still asks for the fixture's Bellsprout (G-h)
PAIR_OVERRIDES = {"ct": {"trades": "given"}}
# Manager defaults for gen2_gsc (wild/starters/trainers random, fastest text) plus statics and in-game trades, so
# G-e..G-h exercise ROM-derived statics/gifts/roamers/NPC trades. No tutors: UPR drops them on G/S, which would make the
# C+G pair refuse on "same applied settings" (docs/gen2/RANDOMIZER.md R1+R2).
SPEC_OVERRIDES = {"statics": "random", "trades": "given_and_requested", "wild_min_catch_rate": 5}


def _sys_path():
    for p in (str(REPO), str(REPO / "tools")):
        if p not in sys.path:
            sys.path.insert(0, p)


GEN2_CACHE = GEN2_BUILD.parent        # gen2-build + gen2-fixtures are COPIED (run_gb_gate wants the ROM inside the repo)
MAIN_CACHE = Path(os.environ.get("SLINK_C5_MAIN_CACHE", "E:/Google Drive/SLink/.cache"))
JUNCTION = ("pret", "build-tools", "downloads")
DRIVER_MARKER = "SLINK_GEN2_RANDOMIZED"   # present in lua/tests once the C-5 driver patch is committed


def make_lane(name: str, sha: str = "HEAD") -> Path:
    """A detached worktree of this checkout's commit at C5_ROOT/<name> (the gen2_final_sweep lane recipe)."""
    import shutil
    import subprocess

    path = C5_ROOT / name
    if path.exists():
        drop_lane(path)
    subprocess.run(["git", "-C", str(REPO), "worktree", "add", "--detach", str(path), sha], check=True,
                   capture_output=True)
    (path / ".cache").mkdir(exist_ok=True)
    for sub in ("gen2-build", "gen2-fixtures"):
        shutil.copytree(GEN2_CACHE / sub, path / ".cache" / sub, symlinks=True)
    for sub in JUNCTION:
        if os.name == "nt":
            subprocess.run(["cmd", "/c", "mklink", "/J", os.path.normpath(path / ".cache" / sub),
                            os.path.normpath(MAIN_CACHE / sub)], check=True, capture_output=True)
        else:
            (path / ".cache" / sub).symlink_to((MAIN_CACHE / sub).resolve(), target_is_directory=True)
    return path


def drop_lane(path: Path) -> None:
    _sys_path()
    import gen2_final_sweep as sweep

    sweep.drop_lane(path)   # unlinks the junctions first, rmtree with S_IWRITE, prunes the admin dir


def clean_rom(title: str) -> Path:
    _sys_path()
    profile = json.loads((REPO / f"data/games/gen2_{title}/profile.json").read_text(encoding="utf-8"))["titles"][title]
    path = GEN2_BUILD / ("pokecrystal" if title == "crystal" else "pokegold") / f"{profile['artifact']}.gbc"
    if hashlib.sha1(path.read_bytes()).hexdigest() != profile["rom_sha1"]:
        raise SystemExit(f"{path} is not the pinned {title} build")
    return path


def provision(pairs) -> None:
    _sys_path()
    from server import cartridges, upr_pipeline, upr_settings as U

    if hashlib.sha256(JAR.read_bytes()).hexdigest() not in json.loads((REPO / "data/upr_jars.json").read_text()).values():
        raise SystemExit(f"{JAR} is not a pinned jar (data/upr_jars.json)")
    if not upr_pipeline.jar_is_fork(str(JAR)):
        raise SystemExit(f"{JAR} is not the SLink fork")
    for pair in pairs:
        run = ROMS / pair
        run.mkdir(parents=True, exist_ok=True)
        spec = U.family_spec({**U.default_spec(U.FAMILY_GEN2), **SPEC_OVERRIDES, **PAIR_OVERRIDES.get(pair, {})},
                             U.FAMILY_GEN2)
        settings = run / "settings.rnqs"
        settings.write_bytes(U.build_spec(spec, family=U.FAMILY_GEN2))
        a, b = PAIRS[pair]
        res = cartridges.provision(str(run), {"a": str(clean_rom(a)), "b": str(clean_rom(b))}, companion=True,
                                   randomize={"settings_path": str(settings)}, jar=str(JAR))
        meta = {"pair": pair, "titles": {"a": a, "b": b}, "jar_sha256": hashlib.sha256(JAR.read_bytes()).hexdigest(),
                "spec": spec, "summary": res["randomizer"]["summary"],
                "players": {pid: {"rom_sha1": p["rom_sha1"], "kind": p["kind"],
                                  "seed": res["randomizer"]["players"][pid]["seed"],
                                  "overlay_sha1": res["randomizer"]["players"][pid]["source_sha1"]}
                            for pid, p in res["players"].items()}}
        (run / "c5_provision.json").write_text(json.dumps(meta, indent=1) + "\n", encoding="utf-8")
        print(f"[c5] {pair}: " + ", ".join(f"{pid}={m['rom_sha1'][:12]} seed {m['seed']}" for pid, m in meta["players"].items()))


def prove(pairs) -> list[str]:
    """Each provisioned ROM: contract sha1 == file sha1; lua/gen2/entry.lua admits it as rand_overlay (beacon + anchors)
    in lupa; the server binds a hello to the contract sha1. Returns the problems (empty = proven)."""
    _sys_path()
    from server.adapters import get_adapter
    from server.server import SLinkServer
    from tests.unit import test_gen2_rand_admission as T

    problems = []
    for pair in pairs:
        run = ROMS / pair
        contract = json.loads((run / "rom_contract.json").read_text(encoding="utf-8"))
        meta = json.loads((run / "c5_provision.json").read_text(encoding="utf-8"))
        for pid, title in meta["titles"].items():
            rom = (run / "roms" / f"{pid}.gbc").read_bytes()
            sha = hashlib.sha1(rom).hexdigest()
            want = contract["players"][pid]["rom_sha1"]
            if sha != want:
                problems.append(f"{pair}/{pid}: file sha1 {sha} != contract {want}")
            overlay = T._binding(title)["rom_sha1"]
            if sha == overlay:
                problems.append(f"{pair}/{pid}: not randomized (equals the overlay)")
            decision, why = T._admit(rom)
            kind = decision["kind"] if decision is not None else None
            if kind != "rand_overlay":
                problems.append(f"{pair}/{pid}: Lua admission {kind!r} ({why})")
            flipped = bytearray(rom)   # known-negative control: one beacon byte flipped must refuse
            span = json.loads((REPO / f"data/games/gen2_{title}/overlay/beacon.json").read_text())["spans"][0]
            flipped[span["offset"]] ^= 0xFF
            if (T._admit(bytes(flipped))[0] or {}).get("kind") is not None:
                problems.append(f"{pair}/{pid}: beacon control admitted a flipped overlay byte")
            srv = SLinkServer(data_dir=str(run))
            srv.adapter = get_adapter("gen2_gsc", rom_type=T.ROM_TYPE[title])
            ok = srv._decide_admission(pid, T._hello(player=pid, sha=sha))
            bad = srv._decide_admission(pid, T._hello(player=pid, sha=overlay, kind="overlay"))
            if ok["state"] != "admitted" or bad["state"] != "rejected":
                problems.append(f"{pair}/{pid}: server contract binding {ok} / overlay {bad}")
            print(f"[c5] {pair}/{pid} {title}: sha1 {sha[:12]} == contract, Lua {kind}, beacon control refused, "
                  f"server {ok['state']} / un-randomized overlay {bad['state']}")
    return problems


def shim(pair: str, duo_argv: list[str]) -> None:
    """Runs INSIDE a lane process (cwd = lane): the lane's own tools/e2e_duo.py, with the executed cartridges swapped for
    the provisioned randomized pair and the Manager contract placed in the run's data dir. Nothing else changes: the
    scenario, fixtures, server, oracles and receipts are the overlay cell's own."""
    import shutil

    lane = Path.cwd().resolve()
    sys.path[:0] = [str(lane / "tools"), str(lane)]
    import e2e_duo as duo
    import run_gb_gate

    if DRIVER_MARKER not in (lane / "lua/tests/test_gen2_scripted_gate.lua").read_text(encoding="utf-8"):
        raise SystemExit("lane lacks the C-5 lua/tests driver change (SLINK_GEN2_RANDOMIZED); see docs/gen2/C5_RUNBOOK.md")
    source = ROMS / pair
    contract = json.loads((source / "rom_contract.json").read_text(encoding="utf-8"))
    titles = json.loads((source / "c5_provision.json").read_text(encoding="utf-8"))["titles"]

    def randomize(plan, inst, title):
        """One instance's launch plan, re-pointed at its provisioned randomized ROM (fails closed on any mismatch)."""
        if title != titles[inst]:
            raise RuntimeError(f"{inst}: scenario boots {title} but pair {pair} provisioned {titles[inst]}")
        rom = (source / "roms" / f"{inst}.gbc").read_bytes()
        sha = hashlib.sha1(rom).hexdigest()
        if sha != contract["players"][inst]["rom_sha1"]:
            raise RuntimeError(f"{inst}: provisioned ROM differs from its contract")
        target = Path(duo.BUILD).resolve() / "c5" / f"g2r_{inst}.gbc"
        name = run_gb_gate.g1.save_name_for(str(target))
        plan.update(stage=rom, rom=target, launch_sha1=sha, saveram_name=name)
        plan["env"].update(SLINK_GEN2_EXEC_SHA1=sha, SLINK_GEN2_RANDOMIZED="1", SLINK_GEN2_SAVERAM_NAME=name)
        return plan

    scenario = duo_argv[duo_argv.index("--scenario") + 1]
    # C-5 refused-at-hello controls. wrong_rom: the server's contract names ANOTHER Manager cart for B (pair ct's B,
    # a real UPR output with its own roms/b.gbc), while B boots its own pair's cart. no_contract: no contract at all.
    expect = {"gen2_c5_wrong_rom": {"a": "admitted", "b": "refused"},
              "gen2_c5_no_contract": {"a": "refused", "b": "refused"}}.get(scenario)

    def swap(self):
        if scenario != "gen2_c5_no_contract":
            Path(self.data_dir, "roms").mkdir(exist_ok=True)
            served = json.loads(json.dumps(contract))
            for inst in ("a", "b"):
                rom = source / "roms" / f"{inst}.gbc"
                if scenario == "gen2_c5_wrong_rom" and inst == "b":
                    rom = ROMS / "ct" / "roms" / "b.gbc"
                    served["players"]["b"] = json.loads((ROMS / "ct" / "rom_contract.json").read_text())["players"]["b"]
                    if served["players"]["b"]["rom_sha1"] == contract["players"]["b"]["rom_sha1"]:
                        raise RuntimeError("the wrong-ROM control's contract cart equals the booted cart")
                shutil.copyfile(rom, Path(self.data_dir, "roms", f"{inst}.gbc"))
            Path(self.data_dir, "rom_contract.json").write_text(json.dumps(served, indent=2) + "\n", encoding="utf-8")
        for inst, plan in self._gen2_plans.items():
            row = self._gen2_inputs[inst]
            if row.get("expect_admission") == "refused":
                continue
            randomize(plan, inst, row["title"])
            if expect:
                plan["env"]["SLINK_C5_EXPECT"] = expect[inst]
            print(f"[c5] {inst}: executes randomized {row['title']} {plan['launch_sha1'][:12]} "
                  f"(seed {contract['players'][inst]['seed']}){' expect ' + expect[inst] if expect else ''}")
        self._c5_swapped = True

    reconnect = duo.DuoRun._stage_gen2_reconnect

    def reconnected(self, phase, source_save, key):   # the relaunched A re-plans: keep it on its randomized cart
        planner = run_gb_gate.GENS["gen2"]["plan"]
        run_gb_gate.GENS["gen2"]["plan"] = lambda *a, **k: randomize(planner(*a, **k), "a", self._gen2_inputs["a"]["title"])
        try:
            return reconnect(self, phase, source_save, key)
        finally:
            run_gb_gate.GENS["gen2"]["plan"] = planner

    check = duo.DuoRun._check_bizhawk_paths

    def checked(self):
        if not getattr(self, "_c5_swapped", False):
            swap(self)
        return check(self)

    witness = duo.DuoRun.check_gen2_save_witness

    def witnessed(self, results):
        kept, self._gen2_artifact = self._gen2_artifact, "rand_overlay"   # the CLIENT line must say rand_overlay
        try:
            return witness(self, results)
        finally:
            self._gen2_artifact = kept

    # The oracles' C-5 input (gen2_duo_oracles.RANDOMIZED): each side's contract sha1 and ROM; e2e_duo passes none.
    import gen2_duo_oracles as oracles

    if not hasattr(oracles, "randomized_from_run"):
        raise SystemExit("lane's tools/gen2_duo_oracles.py has no C-5 randomized input; commit it or pass --carry")
    oracles.RANDOMIZED = oracles.randomized_from_run(source)

    prepare = duo.DuoRun._prepare_gen2_lane

    def prepared(self):
        """The U1G gift/trade legs end when the party holds the RECEIVED species (lua/tests/gen2_u1g_inputs.lua done()):
        on a randomized cart that is the species the side's own ROM gives, read by the same oracle helpers."""
        prepare(self)
        for inst, env in self._gen2_env.items():
            facts = json.loads(env.get("SLINK_GEN2_U1_FACTS") or "{}")
            u1g = facts.get("u1g") or {}
            title = self._gen2_inputs[inst]["title"]
            if u1g.get("kind") == "bill":
                u1g["received"] = oracles._randomized_gift_species(inst, title, "BillsFamilysHouse:BillScript")
            elif u1g.get("kind") == "kyle":
                u1g["received"] = oracles._randomized_npc_trade(inst, title, oracles.BELLSPROUT, oracles.KYLE_OT)[0]
            else:
                continue
            env["SLINK_GEN2_U1_FACTS"] = json.dumps(facts)
            print(f"[c5] {inst}: U1G {u1g['kind']} receives species {u1g['received']} on this ROM")

    duo.DuoRun._check_bizhawk_paths = checked
    duo.DuoRun._prepare_gen2_lane = prepared
    duo.DuoRun.check_gen2_save_witness = witnessed
    duo.DuoRun._stage_gen2_reconnect = reconnected
    sys.argv = ["e2e_duo.py", *duo_argv]
    duo.main()


# ── the cells ─────────────────────────────────────────────────────────────────────────────────────────────────────
GAMES = {"cc": "gen2_new", "gs": "gen2_gold_silver", "cg": "gen2_crystal_gold", "ct": "gen2_new"}
# (pair, e2e_duo scenario, RANDOMIZER_GATES.md §2.2 rows covered, gap or None). A gap is the exact missing piece; a
# BLOCKED cell never runs and keeps `run` red until its gap closes and the row is edited here.
CELLS = [
    ("cc", "link", "G-a", None),
    ("gs", "link", "G-b", None),
    ("cg", "link", "G-b", None),
    ("cc", "gen2_reconnect", "G-d", None),
    ("gs", "gen2_reconnect", "G-d", None),
    # G-e/G-f: Bill's givepoke species is read from each side's ROM (gen2_duo_oracles._randomized_gift_species)
    ("cc", "gen2_gift", "G-e G-f", None),
    ("gs", "gen2_gift", "G-e G-f", None),
    # G-h: ct is provisioned with trades=given, so Kyle still asks for Bellsprout; the given species comes from the ROM
    ("ct", "gen2_npc_trade", "G-h", None),
    # G-c: B boots cc's B cart while the served contract names ct's B (another real Manager cart): server refuses B
    ("cc", "gen2_c5_wrong_rom", "G-c", None),
    # G-i: no rom_contract.json in the run: the server refuses both randomized hellos (server.py:862-867)
    ("cc", "gen2_c5_no_contract", "G-i", None),
    ("cc", "c5_roamer", "G-g",
     "the overlay client never registers roamer_party_finalized / roamer_box_finalized: they are absent from the "
     "PHYSICAL engine_sites receipt (tests/fixtures/gen2/receipts/overlay/<title>.engine_sites.json proves 27 sites, "
     "no roamer_*), so lua/gen2/signals.lua:953-965 can never classify a roamer catch on ANY overlay cart (randomized "
     "or not). Needs a U1G 'roamer' leg (tests/live/test_gen2_u1g.py KINDS, lua/tests/gen2_u1g_inputs.lua) to prove "
     "them first; only then an O-33 roamer setup (wRoamMon<i> on Route 29 + repel + Master Balls) can link"),
]
RNG_STALL = None   # filled from gen2_final_sweep (the same retry-once classes)


def cell_id(pair, scenario):
    return f"c5/{pair}/{scenario}"


def lf_sha256(path: Path) -> str:
    raw = path.read_bytes()
    return hashlib.sha256(raw.replace(b"\r\n", b"\n") if path.suffix in (".txt", ".json") else raw).hexdigest()


def _git(cwd, *args):
    import subprocess

    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True).stdout


# The C-5 harness files that may be carried UNCOMMITTED from this checkout into the lane (--carry): outside the Gen 2
# CODE_DIGEST (lua/tests, tools), so a carried run still stamps a clean digest. New files are listed too.
CARRY = ("lua/tests/test_gen2_scripted_gate.lua", "lua/tests/duo/duo_gen2_main.lua", "tools/gen2_duo_oracles.py",
         "tools/e2e_duo.py", "tools/gen2_synth_fixtures.py", "lua/tests/duo/gen2_c5_refused.lua",
         "lua/tests/duo/scenario_gen2_c5_wrong_rom.lua", "lua/tests/duo/scenario_gen2_c5_no_contract.lua",
         "lua/tests/duo/scenario_gen2_c5_roamer.lua")


def ensure_lane(sha: str, carry: bool) -> Path:
    import shutil

    lane = C5_ROOT / "r1"
    if lane.exists() and _git(lane, "rev-parse", "HEAD").strip() != sha:
        drop_lane(lane)
    if not lane.exists():
        make_lane("r1", sha)
    if carry:   # this checkout's working copy of each C-5 harness file, byte for byte
        for rel in CARRY:
            if (REPO / rel).is_file():
                (lane / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(REPO / rel, lane / rel)
    if DRIVER_MARKER not in (lane / "lua/tests/test_gen2_scripted_gate.lua").read_text(encoding="utf-8"):
        raise SystemExit(f"{sha[:8]} lacks the C-5 lua/tests driver change; commit it or pass --carry")
    dirty = [line[3:] for line in _git(lane, "status", "--porcelain", "--untracked-files=no").splitlines()]
    if set(dirty) - (set(CARRY) if carry else set()):
        raise SystemExit(f"lane {lane} has tracked changes beyond the carried C-5 files: {sorted(set(dirty) - set(CARRY))}")
    return lane


def contract_of(pair):
    return json.loads((ROMS / pair / "rom_contract.json").read_text(encoding="utf-8"))


def c5_receipt_errors(receipts: dict[str, list[str]], contract: dict, digest: str) -> list[str]:
    """What makes a PASS a C-5 PASS: each side's production client admitted rand_overlay at its contract sha1, and the
    run's code stamp is clean and at this digest."""
    errors = []
    for side in ("a", "b"):
        lines = receipts.get(side)
        if lines is None:
            errors.append(f"{side}: no receipt")
            continue
        want = contract["players"][side]["rom_sha1"]
        clients = [json.loads(line.split(" ", 1)[1]) for line in lines if line.startswith("CLIENT ")]
        heads = [json.loads(line.split(" ", 1)[1]) for line in lines if line.startswith("DUO_GEN2 ")]
        if not clients or any(c.get("artifact_kind") != "rand_overlay" or c.get("rom_sha1") != want for c in clients):
            errors.append(f"{side}: CLIENT is not rand_overlay at the contract sha1 {want[:12]}")
        if not heads or any(h.get("rom_sha1") != want for h in heads):
            errors.append(f"{side}: DUO_GEN2 header does not name the randomized sha1")
    for phase in ("a_initial", "a_same_save", "a_wrong_save"):
        clients = [json.loads(line.split(" ", 1)[1]) for line in receipts.get(phase, []) if line.startswith("CLIENT ")]
        if phase in receipts and (not clients or any(c.get("artifact_kind") != "rand_overlay"
                                                     or c.get("rom_sha1") != contract["players"]["a"]["rom_sha1"]
                                                     for c in clients)):
            errors.append(f"{phase}: CLIENT is not rand_overlay at A's contract sha1")
    for side in ("a", "b"):
        lines = receipts.get(side) or []
        verdicts = [line for line in lines if line.startswith("RESULT:")]
        if not verdicts or any(not v.startswith("RESULT: PASS") for v in verdicts):
            errors.append(f"{side}: RESULT is not PASS")
    stamps = [json.loads(line.split(" ", 1)[1]) for line in receipts.get("pydec", []) if line.startswith("CODE_DIGEST ")]
    if len(stamps) != 1 or stamps[0].get("digest") != digest or stamps[0].get("dirty") != []:
        errors.append(f"pydec: CODE_DIGEST is not one clean stamp at {digest[:12]}")
    if not any(line.startswith("PYDEC: PASS") for line in receipts.get("pydec", [])):
        errors.append("pydec: no PYDEC: PASS")
    return errors


def selfcheck() -> int:
    """c5_receipt_errors on a known-good receipt set and three one-field mutants (each must go red)."""
    sha = {"a": "1" * 40, "b": "2" * 40}
    contract = {"players": {p: {"rom_sha1": s} for p, s in sha.items()}}

    def side(p, kind="rand_overlay", head=None, verdict="RESULT: PASS (ok)"):
        return [f'DUO_GEN2 {{"rom_sha1": "{head or sha[p]}"}}', f'CLIENT {{"artifact_kind": "{kind}", "rom_sha1": "{sha[p]}"}}',
                verdict]

    pydec = ['CODE_DIGEST {"digest": "d", "dirty": []}', "PYDEC: PASS a=x"]
    good = {"a": side("a"), "b": side("b"), "pydec": pydec}
    assert c5_receipt_errors(good, contract, "d") == []
    assert c5_receipt_errors({**good, "a": side("a", kind="overlay")}, contract, "d")
    assert c5_receipt_errors({**good, "b": side("b", head="3" * 40)}, contract, "d")
    assert c5_receipt_errors({**good, "a": side("a", verdict="RESULT: FAIL")}, contract, "d")
    assert c5_receipt_errors(good, contract, "other")
    print("[c5] selfcheck ok")
    return 0


def run_cell(pair, scenario, covers, lane, out, digest, sha, attempt_base):
    import shutil
    import subprocess
    import time

    import e2e_duo

    contract = contract_of(pair)
    cid = cell_id(pair, scenario)
    log = out / "logs" / (cid.replace("/", "__") + ".log")
    log.parent.mkdir(parents=True, exist_ok=True)
    result = {"id": cid, "pair": pair, "scenario": scenario, "covers": covers, "ok": False, "attempts": 0,
              "contract_sha1": {p: contract["players"][p]["rom_sha1"] for p in ("a", "b")}}
    started = time.time()
    for attempt in (1, 2):
        result["attempts"] = attempt
        lane_id = f"c5{attempt_base}{attempt}"
        cmd = [sys.executable, str(Path(__file__).resolve()), "_shim", pair, "--", "--game", GAMES[pair],
               "--scenario", scenario, "--lane", lane_id, "--keep-data", "--gen2-artifact", "overlay"]
        env = dict(os.environ, SLINK_LIVE="1", PYTHONUNBUFFERED="1", SLINK_GEN2_ARTIFACT="overlay")
        with open(log, "a", encoding="utf-8") as handle:
            handle.write(f"$ {' '.join(cmd)}\n")
            handle.flush()
            proc = subprocess.Popen(cmd, cwd=lane, env=env, stdout=handle, stderr=subprocess.STDOUT,
                                    start_new_session=os.name != "nt")
            try:
                code = proc.wait(timeout=e2e_duo.SCENARIOS[scenario]["timeout"] * 2 + 600)
            except subprocess.TimeoutExpired:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
                else:
                    os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
                code = "timeout"
        receipts, texts = {}, {}
        for src in sorted((lane / "patch/build").glob(f"e2e_{scenario}_{lane_id}_*")):
            suffix = src.name[len(f"e2e_{scenario}_{lane_id}_"):]
            if "attempt" in suffix or suffix.endswith(("_exit.SaveRAM", "_link_save.SaveRAM", "manifest.json")):
                continue
            rel = f"receipts/c5/duo_{scenario.removeprefix('gen2_')}_{pair}_{suffix}"
            dest = out / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest)
            receipts[rel] = lf_sha256(dest)
            side = {"a_result.txt": "a", "b_result.txt": "b", "pydec_result.txt": "pydec"}.get(suffix)
            if side:
                texts[side] = dest.read_text(encoding="utf-8", errors="replace").splitlines()
        for phase in ("initial", "same_save", "wrong_save"):   # gen2_reconnect: every A launch, CLIENT identity only
            extra = out / f"receipts/c5/duo_{scenario.removeprefix('gen2_')}_{pair}_a_{phase}_result.txt"
            if scenario == "gen2_reconnect" and extra.is_file():
                texts[f"a_{phase}"] = extra.read_text(encoding="utf-8", errors="replace").splitlines()
        result["receipts"] = receipts
        result["exit"] = code
        problems = [] if code == 0 else [f"e2e_duo exit {code}"]
        problems += c5_receipt_errors(texts, contract, digest)
        result["problems"] = problems
        result["ok"] = not problems
        text = log.read_text(encoding="utf-8", errors="replace").split(f"$ {' '.join(cmd)}")[-1]
        result["data_dirs"] = sorted(set(result.get("data_dirs", [])) | {
            line.split("data dir kept: ", 1)[1].strip() for line in text.splitlines() if "data dir kept: " in line})
        if result["ok"] or not RNG_STALL.search(text):
            break
        result["retried"] = RNG_STALL.search(text).group(0)
    result["seconds"] = round(time.time() - started)
    if result["ok"]:
        meta = json.loads((ROMS / pair / "c5_provision.json").read_text(encoding="utf-8"))
        synth = sorted({json.loads(line.split(" ", 1)[1]).get("synth") or "" for side in ("a", "b")
                        for line in texts.get(side, []) if line.startswith("DUO_GEN2 ")} - {""})
        manifest = {"schema": "gen2-c5-duo-cell-v1", "receipt_marker": "gen2.requirement.C-5",
                    "evidence_level": "PHYSICAL", "cell": cid, "covers": covers.split(), "pair": pair,
                    "game": GAMES[pair], "scenario": scenario, "sha": sha, "code_digest": digest,
                    "contract": contract, "provision": meta, "receipts": receipts,
                    "disclosures": {
                        "SYNTH": ["fixture RTC trailer set by tools/gen2_synth_fixtures.day_clock (e2e_duo clock_setup)"]
                                 + [f"O-33 synthetic setup fixture {name}" for name in synth],
                        "HARNESS": ["tools/c5_runner.py _shim: each instance executes the Manager-provisioned randomized "
                                    "ROM (server.cartridges.provision, pinned jar) in place of the overlay stage; the "
                                    "Manager's rom_contract.json + roms/ sit in the server data dir",
                                    "lua/tests C-5 driver change (SLINK_GEN2_RANDOMIZED): the scripted gate keeps the "
                                    "overlay row's facts/view; duo_gen2_main accepts production artifact_kind rand_overlay"],
                        "NATIVE": "the randomized cartridge, the production client admission and the server contract "
                                  "binding are the behaviour under test and run natively"}}
        path = out / f"receipts/c5/{cid.replace('/', '__')}.manifest.json"
        path.write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8", newline="\n")
        result["receipts"][str(path.relative_to(out)).replace("\\", "/")] = lf_sha256(path)
    return result


def run(only, carry, sha_arg, ready_only) -> int:
    import time

    global RNG_STALL
    _sys_path()
    import gen2_code_digest
    import gen2_final_sweep

    RNG_STALL = gen2_final_sweep.RNG_STALL
    sha = _git(REPO, "rev-parse", sha_arg).strip()
    digest = gen2_code_digest.head_digest(REPO, sha)
    out = C5_ROOT / "out" / digest[:12]
    out.mkdir(parents=True, exist_ok=True)
    summary_path = out / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.is_file() else {"cells": {}}
    summary.update(sha=sha, code_digest=digest)
    todo = [c for c in CELLS if not only or cell_id(c[0], c[1]) in only]
    if only and len(todo) != len(set(only)):
        raise SystemExit(f"unknown cell(s): {sorted(set(only) - {cell_id(c[0], c[1]) for c in CELLS})}")
    jar = hashlib.sha256(JAR.read_bytes()).hexdigest()
    lane, red, blocked = None, [], []
    for pair, scenario, covers, gap in todo:
        cid = cell_id(pair, scenario)
        if gap:
            blocked.append(cid)
            print(f"[c5] BLOCKED {cid}: {gap}")
            continue
        meta = json.loads((ROMS / pair / "c5_provision.json").read_text(encoding="utf-8"))
        if meta["jar_sha256"] != jar:
            raise SystemExit(f"{pair}: provisioned with jar {meta['jar_sha256'][:12]}, pinned jar is {jar[:12]}: re-provision")
        want = {p: contract_of(pair)["players"][p]["rom_sha1"] for p in ("a", "b")}
        done = summary["cells"].get(cid)
        if done and done.get("ok") and done.get("contract_sha1") == want:
            print(f"[c5] SKIP {cid}: PASS at {digest[:12]} (receipted)")
            continue
        lane = lane or ensure_lane(sha, carry)
        print(f"[c5] RUN  {cid} ({covers})", flush=True)
        result = run_cell(pair, scenario, covers, lane, out, digest, sha, int(time.time()) % 100000)
        summary["cells"][cid] = result
        tmp = summary_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(summary, indent=1) + "\n", encoding="utf-8", newline="\n")
        tmp.replace(summary_path)
        print(f"[c5] {'PASS' if result['ok'] else 'FAIL'} {cid} {result['seconds']}s attempts={result['attempts']} "
              f"{'; '.join(result['problems'])}", flush=True)
        if not result["ok"]:
            red.append(cid)
            break   # a red cell stops the run: diagnose before anything else spends emulator time
    for cid in red:
        print(f"RED  {cid}: {'; '.join(summary['cells'][cid]['problems'])}")
    print(f"[c5] out: {out}")
    if red:
        return 1
    return 0 if ready_only or not blocked else 2


def status() -> int:
    for path in sorted((C5_ROOT / "out").glob("*/summary.json")):
        summary = json.loads(path.read_text(encoding="utf-8"))
        print(f"{path.parent.name}  sha {summary.get('sha', '?')[:8]}")
        for cid, cell in sorted(summary["cells"].items()):
            print(f"  {'PASS' if cell['ok'] else 'FAIL'}  {cid}  {cell.get('seconds')}s  attempts={cell.get('attempts')}")
    return 0


def clean() -> int:
    """Drop the lane and the server data dirs this runner's cells kept (receipts in out/ stay)."""
    import shutil

    _sys_path()
    import gen2_final_sweep

    lane = C5_ROOT / "r1"
    if lane.exists():
        drop_lane(lane)
    for path in (C5_ROOT / "out").glob("*/summary.json"):
        for cell in json.loads(path.read_text(encoding="utf-8"))["cells"].values():
            for data in cell.get("data_dirs", []):
                if Path(data).name.startswith("duo_") and Path(data).exists():
                    shutil.rmtree(data, onerror=gen2_final_sweep._writable)
    print("[c5] lane and kept data dirs removed; out/ and roms/ kept")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("provision", "prove"):
        p = sub.add_parser(name)
        p.add_argument("--pairs", nargs="*", default=list(PAIRS), choices=list(PAIRS))
    p = sub.add_parser("run")
    p.add_argument("--only", nargs="*", help="cell ids (exact), default every cell")
    p.add_argument("--carry", action="store_true", help="copy this checkout's uncommitted C-5 harness files (CARRY) into the lane")
    p.add_argument("--sha", default="HEAD")
    p.add_argument("--ready-only", action="store_true", help="exit 0 with BLOCKED cells left (READY cells all PASS)")
    sub.add_parser("list")
    sub.add_parser("status")
    sub.add_parser("clean")
    sub.add_parser("selfcheck")
    p = sub.add_parser("_shim", help="internal: run e2e_duo in the cwd lane on a randomized pair")
    p.add_argument("pair", choices=list(PAIRS))
    p.add_argument("duo_argv", nargs=argparse.REMAINDER)
    args = ap.parse_args(argv)
    if args.cmd == "_shim":
        shim(args.pair, [a for a in args.duo_argv if a != "--"])
        return 0
    if args.cmd == "list":
        for pair, scenario, covers, gap in CELLS:
            print(f"{'BLOCKED' if gap else 'READY  '} {cell_id(pair, scenario):24} {covers:8} {gap or ''}")
        return 0
    if args.cmd == "run":
        _sys_path()
        return run(args.only, args.carry, args.sha, args.ready_only)
    if args.cmd == "status":
        return status()
    if args.cmd == "clean":
        return clean()
    if args.cmd == "selfcheck":
        return selfcheck()
    if args.cmd == "provision":
        provision(args.pairs)
        return 0
    problems = prove(args.pairs)
    for problem in problems:
        print(f"RED  {problem}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
