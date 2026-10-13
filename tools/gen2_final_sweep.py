"""Gen 2 FINAL EVIDENCE SWEEP: 86 overlay-only release-required PHYSICAL cells, N lanes at a time, at one frozen sha.

    python tools/gen2_final_sweep.py --list                      # the cells and their exact commands
    python tools/gen2_final_sweep.py --lanes 4                   # everything (run only after "freeze")
    python tools/gen2_final_sweep.py --lanes 2 --only overlay/gen2_new/link overlay/gen2_new/gen2_admit_wrong_rom   # a dry run
    python tools/gen2_final_sweep.py --pin <out>                 # after review: install the PASS receipts + repin

Cells: 64 overlay duos (C-C/G-S gameplay including wrong-ROM refusal, C-G link,
plus seven native trades on each pair) and 22 overlay gates (U1/U2 and five feature gates per title,
plus inspect). Historical clean cells and receipts are retained in the ledgers but never rerun.
The 13 overlay qualification receipts bind fixture bytes, not code, and are captured before the sweep.
A scenario newly registered for C-C/G-S in tools/e2e_duo.py becomes an overlay cell automatically.

Each lane is a detached worktree C:/Users/howar/AppData/Local/Temp/fs<k> at --sha, short enough for BizHawk's MAX_PATH
(e2e_duo.BIZHAWK_PATH_LIMIT). .cache/gen2-build and .cache/gen2-fixtures are COPIED in, because the ROM must resolve
inside the repo (run_gb_gate._gen2_plan). .cache/{pret,build-tools,downloads} are junctioned. Before every cell the
lane must show no tracked change: a gate rewrites tracked receipts, and those are copied out and then reset.
A cell is retried ONCE, and only on an RNG stall (RNG_STALL), except U1 engine-sites cells: up to three disclosed
clock attempts (1/2/3), recorded in the summary. On a timeout only the cell's own process tree is killed
(taskkill /T on its PID).

Output in --out: receipts/<repo-relative path> uses the committed names (duo_<scenario>_<cc|gs|cg>_*, the gates' own
files). summary.json/summary.txt hold the sha, the CODE_DIGEST and per cell: ok, attempts, seconds, reason and every
receipt with its LF sha256 (the pin value). Nothing is committed here. --pin <out> copies the receipts of the PASS
cells into this checkout and rewrites their sha256 in tests/gen2_release_requirements.json and
tests/gen2_live_gate_requirements.json in place (text edit, formatting kept). It also adds the single inspect_run row
when it is missing and lists every receipt no row names. Then commit the exact paths.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import queue
import re
import shutil
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO / "tools"), str(REPO)]
import e2e_duo  # noqa: E402

# Lanes + evidence live on the work drive, never C: (owner 2026-10-02); tools/slink_space.work_root() replaces this.
WORK_ROOT = Path(os.environ.get("SLINK_WORK_ROOT", "F:/slink-work"))
LANE_ROOT = WORK_ROOT / "lanes" / "gen2"   # fs1..N stay short: BizHawk's 240-char SaveRAM path budget
COPY = ("gen2-build", "gen2-fixtures")
JUNCTION = ("pret", "build-tools", "downloads")
PAIRS = {"gen2_new": "cc", "gen2_gold_silver": "gs", "gen2_crystal_gold": "cg"}
TITLES = ("crystal", "gold", "silver")
# ponytail: the stall phrases seen in lane logs; widen here if a new RNG stall class shows up
# The trainer poison leg (Bug Catcher Wade, G-S poison and Gold's U1 chain) is capped near 83% a fight by the
# 15-HP target (coordinator, re-run pass): a fight that ends unpoisoned retries once like out-of-balls.
RNG_STALL = re.compile(r"out-of-balls|no Poke Ball left in the pocket|survived \d+ battles|duplicates-only hunt|clause unobserved"
                       r"|the trainer battle ended without a poisoned party mon|poison_faint did not fire"
                       r"|the battle ended without a catch")
GATE_TIMEOUT = 3600
PIN_FILES = ("tests/gen2_release_requirements.json", "tests/gen2_live_gate_requirements.json")
ATTESTATION = "tests/fixtures/gen2/receipts/live_new_gates.inspect_run.json"   # tests/live/conftest.py
INSPECT_ROW = """    {
      "id": "new-gates.inspect-run",
      "stage": "physical-live",
      "description": "R-1/R-2/R-3/R-4/R-5g live-new-gates run attestation (gen2-live-new-gates-attestation-v1): SLINK_LIVE=1 pytest tests/live/test_gen2_new_gates.py over every fixture, every title PASS, passes only (re-run: tools/gen2_final_sweep.py cell gate/inspect_run)",
      "axes": {"kind": "inspect_run", "requirement_ids": ["R-1", "R-2", "R-3", "R-4", "R-5g"]},
      "proofs": [{"receipts": {"receipt": {
        "path": "%s",
        "sha256": "%s"
      }}}]
    }"""


def live(test, title):
    return [sys.executable, "-m", "pytest", f"tests/live/{test}", "-q", "-p", "no:randomly", "-k", title]


def cells():
    out = []
    for game, pair in PAIRS.items():
        for scenario in e2e_duo.scenarios_for(game):
            if scenario in e2e_duo.GEN2_TRADE_SCENARIOS:
                out.append({"id": f"{game}/{scenario}", "kind": "duo", "game": game, "scenario": scenario, "pair": pair,
                            "artifact_kind": "overlay", "timeout": e2e_duo.SCENARIOS[scenario]["timeout"] + 600})
            if scenario not in e2e_duo.GEN2_TRADE_SCENARIOS and (pair != "cg" or scenario == "link"):
                out.append({"id": f"overlay/{game}/{scenario}", "kind": "duo", "game": game,
                            "scenario": scenario, "pair": pair, "artifact_kind": "overlay",
                            "timeout": e2e_duo.SCENARIOS[scenario]["timeout"] + 600})
    for title in TITLES:
        for kind in ("panel", "sfx", "w6", "phone", "sp_lowwater"):
            out.append({"id": f"gate/{kind}/{title}", "kind": "gate", "timeout": GATE_TIMEOUT,
                        "artifact_kind": "overlay",
                        "commands": [live(f"test_gen2_{kind}_gate.py", title)]})
        out.append({"id": f"overlay/gate/engine_sites/{title}", "kind": "gate", "artifact_kind": "overlay",
                    "timeout": 2 * GATE_TIMEOUT,
                    "commands": [live("test_gen2_frame_align.py", title), live("test_gen2_u1g.py", title)]})
        out.append({"id": f"overlay/gate/write_window/{title}", "kind": "gate", "artifact_kind": "overlay",
                    "timeout": GATE_TIMEOUT, "commands": [live("test_gen2_write_windows.py", title)]})
    out.append({"id": "overlay/gate/inspect_run", "kind": "gate", "artifact_kind": "overlay", "timeout": 13 * 900,
                "commands": [[sys.executable, "-m", "pytest", "tests/live/test_gen2_new_gates.py", "-q", "-p", "no:randomly"]]})
    return sorted(out, key=lambda c: -c["timeout"])   # longest first


def duo_command(cell, lane):
    return [sys.executable, "tools/e2e_duo.py", "--game", cell["game"], "--scenario", cell["scenario"],
            "--lane", lane, "--keep-data", "--gen2-artifact", cell.get("artifact_kind", "clean")]


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True).stdout


def _writable(func, path, _exc):
    os.chmod(path, stat.S_IWRITE)
    func(path)


def drop_lane(path):
    for name in JUNCTION:   # unlink the junctions FIRST: never recurse through one
        junction = path / ".cache" / name
        if junction.exists() or os.path.lexists(junction):
            os.rmdir(junction)
    if path.exists():
        shutil.rmtree(path, onerror=_writable)
    git(REPO, "worktree", "prune")
    # a Drive checkout leaves the admin dir read-only, so prune cannot delete it (the 14 orphans)
    admin = Path(git(REPO, "rev-parse", "--path-format=absolute", "--git-common-dir").strip()) / "worktrees" / path.name
    if admin.exists():
        shutil.rmtree(admin, onerror=_writable)


def make_lane(index, sha):
    path = LANE_ROOT / f"fs{index}"
    if path.exists():
        drop_lane(path)
    LANE_ROOT.mkdir(parents=True, exist_ok=True)
    git(REPO, "worktree", "add", "--detach", str(path), sha)
    (path / ".cache").mkdir(exist_ok=True)
    for name in COPY:
        shutil.copytree(REPO / ".cache" / name, path / ".cache" / name, symlinks=True)
    for name in JUNCTION:
        subprocess.run(["cmd", "/c", "mklink", "/J", str(path / ".cache" / name), str(REPO / ".cache" / name)],
                       check=True, capture_output=True)
    return path


def tracked_changes(lane):
    return [line[3:] for line in git(lane, "status", "--porcelain", "--untracked-files=no").splitlines()]


def lf_sha256(path):
    raw = path.read_bytes()
    if path.suffix in (".txt", ".json"):
        raw = raw.replace(b"\r\n", b"\n")
    return hashlib.sha256(raw).hexdigest()


def run(cmd, cwd, timeout, log, *, artifact_kind=None, u1_attempt=None):
    env = dict(os.environ, SLINK_LIVE="1", PYTHONUNBUFFERED="1")
    env.pop("SLINK_GEN2_U1_ATTEMPT", None)  # caller's shell cannot silently select a sweep clock
    if u1_attempt is not None:
        env["SLINK_GEN2_U1_ATTEMPT"] = str(u1_attempt)
    if artifact_kind is not None:
        env["SLINK_GEN2_ARTIFACT"] = artifact_kind
    env.pop("SLINK_GEN2_NO_ATTEST", None)   # the sweep's gate/inspect_run cell is the one that attests
    with open(log, "a", encoding="utf-8") as handle:
        if u1_attempt is not None:
            handle.write(f"[sweep] SLINK_GEN2_U1_ATTEMPT={u1_attempt}\n")
        handle.write(f"$ {' '.join(cmd)}\n")
        handle.flush()
        proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=handle, stderr=subprocess.STDOUT)
        try:
            return proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)   # our tree only
            proc.wait()
            return "timeout"


def collect_duo(cell, lane_id, lane, out):
    artifact = f"{cell['scenario']}_{lane_id}"
    stem = f"duo_{cell['scenario'].removeprefix('gen2_')}_{cell['pair']}_"
    got = {}
    namespace = "/overlay" if cell.get("artifact_kind") == "overlay" else ""
    for src in sorted((lane / "patch/build").glob(f"e2e_{artifact}_*")):
        suffix = src.name[len(f"e2e_{artifact}_"):]
        if "attempt" in suffix or suffix.endswith(("_exit.SaveRAM", "_link_save.SaveRAM", "manifest.json")):
            continue
        dest = out / ("receipts/tests/fixtures/gen2/receipts" + namespace) / (stem + suffix)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
        got[f"tests/fixtures/gen2/receipts{namespace}/{stem + suffix}"] = lf_sha256(dest)
    return got


def collect_gate(lane, out):
    """Every receipt the gate wrote (changed or new, the attestation included), copied out; the lane is then reset."""
    got = {}
    rows = git(lane, "status", "--porcelain", "--untracked-files=all", "--", "tests/fixtures/gen2/receipts", "data/games")
    for line in rows.splitlines():
        rel = line[3:].strip('"')
        dest = out / "receipts" / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(lane / rel, dest)
        got[rel] = lf_sha256(dest)
        if line.startswith("??"):
            (lane / rel).unlink()
    git(lane, "checkout", "--", ".")
    return got


def pin(out, root=REPO):
    """Install the PASS cells' receipts from a sweep's `out` and repin them; returns the receipts no row names."""
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    pins = {rel: sha for cell in summary["cells"] if cell["ok"] for rel, sha in cell.get("receipts", {}).items()}
    # gen2_reconnect: the runner's final <a>_result.txt is the last relaunch; the proof's "a" is the initial phase
    # (e2e_duo _archive_gen2_witnesses keeps it as a_initial), so that file takes the committed "a" name
    sources = {rel: rel for rel in pins}
    for rel in [r for r in pins if r.endswith("_a_initial_result.txt")]:
        target = rel.replace("_a_initial_result.txt", "_a_result.txt")
        sources[target], pins[target] = rel, pins.pop(rel)
        del sources[rel]
    for rel, src in sources.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(out / "receipts" / src, root / rel)
    named = set()
    for name in PIN_FILES:
        path = root / name
        text = path.read_text(encoding="utf-8")
        for rel, sha in pins.items():
            pattern = re.compile(r'("path": "' + re.escape(rel) + r'",\s*"sha256": ")[0-9a-f]{64}"')
            text, hits = pattern.subn(lambda m, sha=sha: m.group(1) + sha + '"', text)
            if hits:
                named.add(rel)
        if name.endswith("live_gate_requirements.json") and ATTESTATION in pins and '"kind": "inspect_run"' not in text:
            at = text.rindex("\n  ]")
            text = text[:at] + ",\n" + INSPECT_ROW % (ATTESTATION, pins[ATTESTATION]) + text[at:]
            named.add(ATTESTATION)
        doc = json.loads(text)
        changed = False
        for row in doc.get("requirements", []):
            axes = row.get("axes") or {}
            artifact = axes.get("artifact_kind", "clean")
            namespace = "tests/fixtures/gen2/receipts/" + ("overlay/" if artifact == "overlay" else "")
            if row.get("stage") == "live-duos":
                pair = {("crystal", "crystal"): "cc", ("gold", "silver"): "gs", ("crystal", "gold"): "cg"}.get(
                    (axes.get("initiator"), axes.get("partner")))
                for scenario in axes.get("scenarios", []):
                    stem = namespace + "duo_" + scenario.removeprefix("gen2_") + "_" + str(pair) + "_"
                    paths = {side: stem + side + "_result.txt" for side in ("a", "b", "pydec")}
                    seeds = {}
                    if scenario == "gen2_reconnect":
                        paths.update({"a_" + phase: stem + "a_" + phase + "_result.txt"
                                      for phase in ("same_save", "wrong_save")})
                        seeds = {phase: stem + "a_" + phase + "_seed.SaveRAM"
                                 for phase in ("same_save", "wrong_save")}
                    if all(rel in pins for rel in [*paths.values(), *seeds.values()]):
                        proof = {"scenario": scenario, "receipts": {side: {"path": rel, "sha256": pins[rel]}
                                                                  for side, rel in paths.items()}}
                        if seeds:
                            proof["staged_saves"] = {phase: {"path": rel, "sha256": pins[rel]}
                                                     for phase, rel in seeds.items()}
                            proof["staged_saves"]["wrong_save"]["case"] = axes["initiator"] + "_battle_ot2"
                            snapshot = stem + "a_witness.SaveRAM"
                            if snapshot in pins:
                                proof["witness_snapshots"] = {"a": {"path": snapshot, "sha256": pins[snapshot]}}
                                named.add(snapshot)
                        row["proofs"] = [old for old in row.get("proofs", []) if old.get("scenario") != scenario] + [proof]
                        named.update(paths.values())
                        named.update(seeds.values())
                        changed = True
            else:
                kind, title = axes.get("kind"), axes.get("title")
                if kind in ("engine_sites", "write_window"):
                    owner = "gold" if artifact == "clean" and title == "silver" and kind == "write_window" else title
                    rel = namespace + str(owner) + "." + kind + ".json"
                elif kind == "qualification":
                    rel = namespace + str(axes.get("fixture")) + ".qualification.json"
                elif kind == "inspect_run":
                    rel = namespace + "live_new_gates.inspect_run.json"
                elif kind in ("panel_gate", "sfx_gate", "w6_gate", "phone_gate", "sp_lowwater_gate"):
                    rel = namespace + str(title) + "." + kind + ".json"
                else:
                    continue
                if rel in pins:
                    row["proofs"] = [{"receipts": {"receipt": {"path": rel, "sha256": pins[rel]}}}]
                    named.add(rel)
                    changed = True
        if changed:
            text = json.dumps(doc, ensure_ascii=False, indent=2) + "\n"
        path.write_text(text, encoding="utf-8", newline="\n")
    return sorted(rel for rel in pins if rel not in named and rel.endswith((".txt", ".json")))


def is_u1_command(command):
    return any(str(arg).replace("\\", "/").rsplit("/", 1)[-1] == "test_gen2_frame_align.py" for arg in command)


def run_commands(commands, lane, timeout, log, *, artifact_kind=None, u1_attempt=None, attempt=None, gate_results=None):
    """Stop at the first failing command and archive every gate result it wrote.

    Optional gate_results receives only this invocation's new/changed archives for retry classification.
    Attempts keep distinct archives so later passes cannot erase the original failure.
    """
    def stamps():   # compared by value: Windows file times tick coarser than time.time()
        return {p: p.stat().st_mtime_ns for p in (lane / "patch/build").glob("*_result.txt")
                if not p.name.startswith("e2e_")}

    codes = []
    for index, cmd in enumerate(commands, 1):
        before = stamps()
        options = {"artifact_kind": artifact_kind} if artifact_kind is not None else {}
        if u1_attempt is not None and is_u1_command(cmd):
            options["u1_attempt"] = u1_attempt
        codes.append(run(cmd, lane, timeout, log, **options))
        for result, stamp in stamps().items():
            if before.get(result) != stamp:
                attempt_suffix = (f".u1-attempt{u1_attempt}" if u1_attempt is not None
                                  else f".attempt{attempt}" if attempt is not None else "")
                archive = log.with_name(f"{log.stem}{attempt_suffix}.cmd{index}.{result.name}")
                shutil.copyfile(result, archive)
                if gate_results is not None:
                    gate_results.append(archive)
        if codes[-1] != 0:
            break
    return codes


def run_cell(cell, lane, n, out, stagger):
    lane_id = f"s{n}"   # unique per cell: e2e_duo refuses to overwrite a lane's archived witnesses
    log = out / "logs" / (cell["id"].replace("/", "__") + ".log")
    log.parent.mkdir(parents=True, exist_ok=True)
    result = {"id": cell["id"], "lane": lane.name, "attempts": 0, "ok": False}
    u1 = cell["kind"] == "gate" and any(is_u1_command(cmd) for cmd in cell["commands"])
    if u1:
        result["u1_attempts"] = []
    last_attempt = 3 if u1 else 2
    started = time.time()
    for attempt in range(1, last_attempt + 1):
        dirty = tracked_changes(lane)
        if dirty:
            result["reason"] = f"lane not clean: {dirty[:3]}"
            break
        stagger()
        result["attempts"] = attempt
        commands = [duo_command(cell, f"{lane_id}{attempt}")] if cell["kind"] == "duo" else cell["commands"]
        options = {"artifact_kind": cell.get("artifact_kind", "clean")}
        if u1:
            result["u1_attempts"].append(attempt)
            options["u1_attempt"] = attempt
        log_start = log.stat().st_size if log.exists() else 0
        gate_results = []
        codes = run_commands(commands, lane, cell["timeout"], log, attempt=attempt, gate_results=gate_results, **options)
        result["ok"] = all(code == 0 for code in codes)
        if cell["kind"] == "duo":
            result["receipts"] = collect_duo(cell, f"{lane_id}{attempt}", lane, out)
        else:
            result["receipts"] = collect_gate(lane, out)
        with log.open("rb") as handle:
            handle.seek(log_start)
            text = handle.read().decode("utf-8", errors="replace")
        evidence = [path.read_text(encoding="utf-8", errors="replace") for path in gate_results]
        if cell["kind"] == "duo":
            failures = [line for line in text.splitlines() if "RESULT: FAIL" in line]
        else:
            # Later checks can cascade from a lost catch. Classify each fresh file's root failure;
            # use the log only when no fresh gate file exists (pytest may truncate its diagnostics).
            failures = [next((line for line in source.splitlines()
                              if "[FAIL]" in line or "RESULT: FAIL" in line), "")
                        for source in evidence or [text]]
        stall = RNG_STALL.search(text + "".join("\n" + source for source in evidence))
        independent_failure = any(line and not RNG_STALL.search(line) for line in failures)
        result["reason"] = "PASS" if result["ok"] else f"exit {codes}"
        if result["ok"] or attempt == last_attempt or stall is None or independent_failure:
            break
        result["retried"] = stall.group(0)
    result["seconds"] = round(time.time() - started)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--list", action="store_true", help="print the cells and their commands, run nothing")
    parser.add_argument("--lanes", type=int, default=4)
    parser.add_argument("--sha", default="HEAD")
    parser.add_argument("--only", nargs="*", help="cell ids (exact) to run instead of all")
    parser.add_argument("--stagger", type=float, default=10.0, help="seconds between cell starts (preflight ~9 s)")
    parser.add_argument("--out", type=Path, default=WORK_ROOT / "evidence" / f"gen2-fsw-{time.strftime('%m%d-%H%M')}")
    parser.add_argument("--pin", type=Path, metavar="OUT", help="install and repin a finished sweep's PASS receipts")
    args = parser.parse_args(argv)
    if args.pin:
        loose = pin(args.pin)
        print("[sweep] pinned; receipts no requirement row names:" if loose else "[sweep] pinned; every receipt named")
        print("\n".join(loose))
        return 0
    todo = [c for c in cells() if not args.only or c["id"] in args.only]
    if args.only and len(todo) != len(set(args.only)):
        parser.error(f"unknown cell(s): {sorted(set(args.only) - {c['id'] for c in todo})}")
    if args.list:
        for c in todo:
            cmds = [duo_command(c, "<lane>")] if c["kind"] == "duo" else c["commands"]
            print(c["id"], " && ".join(" ".join(cmd[1:] if cmd[0] == sys.executable else cmd) for cmd in cmds))
        print(f"{len(todo)} cells")
        return 0
    sha = git(REPO, "rev-parse", args.sha).strip()
    from tools import gen2_code_digest
    digest = gen2_code_digest.head_digest(REPO, sha)
    args.out.mkdir(parents=True, exist_ok=True)
    lanes = queue.Queue()
    for index in range(1, min(args.lanes, len(todo)) + 1):
        lanes.put(make_lane(index, sha))
    last, lock = [0.0], threading.Lock()

    def stagger():
        with lock:
            wait = last[0] + args.stagger - time.time()
            if wait > 0:
                time.sleep(wait)
            last[0] = time.time()

    def work(n, cell):
        lane = lanes.get()
        try:
            result = run_cell(cell, lane, n, args.out, stagger)
        finally:
            lanes.put(lane)
        print(f"[sweep] {'PASS' if result['ok'] else 'FAIL'} {cell['id']} {result.get('seconds')}s "
              f"attempts={result['attempts']} {result.get('reason')}", flush=True)
        return result

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=lanes.qsize()) as pool:
            results = list(pool.map(work, range(len(todo)), todo))
    finally:
        while not lanes.empty():
            drop_lane(lanes.get())
    summary = {"sha": sha, "code_digest": digest, "lanes": args.lanes, "cells": results}
    (args.out / "summary.json").write_text(json.dumps(summary, indent=1) + "\n", encoding="utf-8", newline="\n")
    lines = [f"sha {sha}  code_digest {digest}  lanes {args.lanes}"]
    lines += [f"{'PASS' if r['ok'] else 'FAIL'}  {r['id']}  {r.get('seconds')}s  attempts={r['attempts']}  {r.get('reason')}"
              for r in results]
    (args.out / "summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print("\n".join(lines))
    print(f"[sweep] out: {args.out}")
    return 0 if all(r["ok"] for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
