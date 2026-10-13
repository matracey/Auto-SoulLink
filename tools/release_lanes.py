#!/usr/bin/env python3
"""The generation-independent release-gate mechanism (PLAN §5.5 / §6 P2).

Extracted from `verify_gen1_release.py`, which was the first gate and is now a thin manifest
over this module. A generation's `verify_<gen>_release.py` supplies its own `LANES`,
`REQUIREMENTS`, `ALLOWED_SKIPS` and `_SLOW`, and calls `run_gate(...)` from its own `main()` --
see `verify_gen1_release.py` for the pattern.

The fail-closed rules this module enforces, unchanged from the Gen 1 gate: a lane which did not
RUN did not PASS. A skip reads exactly like a pass unless its reason is on the manifest's own
`ALLOWED_SKIPS` list; xfail, xpass, deselection and pytest-reported errors each fail a lane too.
`--quick`/`--lane` runs are explicitly NOT a release verdict -- only "every lane ran and every
lane passed" is.

Failed child output is retained in unique patch/build logs; display tails name the full log.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
import time

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PY = sys.executable


class Lane:
    def __init__(self, name, argv, env=None, why=""):
        self.name, self.argv, self.env, self.why = name, argv, env or {}, why

    @property
    def is_pytest(self) -> bool:
        return "pytest" in self.argv


def _count_outcomes(text: str) -> dict:
    """pytest's own summary line, parsed. Anything that is not a pass is a problem."""
    out = {"passed": 0, "failed": 0, "skipped": 0, "xfailed": 0, "xpassed": 0,
           "error": 0, "deselected": 0}
    for key in out:
        # Collection output may contain parameter IDs such as "[3 passed]".
        # Only a summary line supplies positive execution evidence.
        # `passed` is anchored to a summary line so a parameter ID like "[3 passed]" in
        # collection output cannot be read as execution evidence. The `(?:\d+ \w+, )*` hop is
        # load-bearing: pytest puts failures FIRST ("27 failed, 11297 passed, ..."), so without
        # it the count reads 0 exactly when a run has failures — i.e. when the numbers matter.
        pattern = (r"(?m)^[ \t=]*(?:\d+ \w+, )*(\d+) passed(?=,|[ \t]+in[ \t]|\s*$)"
                   if key == "passed" else rf"(\d+) {key}")
        m = re.search(pattern, text)
        if m:
            out[key] = int(m.group(1))
    return out


def _unexplained_skips(text: str, allowed_skips) -> list[str]:
    """Skip reasons with no entry in the manifest's ALLOWED_SKIPS.

    Read from pytest's `-rs` summary, which every pytest lane here asks for -- a skip whose
    reason was never printed is itself unexplained, and fails.
    """
    out = []
    for line in text.splitlines():
        if not line.startswith("SKIPPED"):
            continue
        if not any(frag in line for frag, _why in allowed_skips):
            out.append(line.strip())
    return out


def save_failure_output(stdout, stderr, *, repo: str, label: str) -> str:
    """Retain both complete streams, including TimeoutExpired's undecoded bytes."""
    directory = os.path.join(repo, "patch", "build")
    os.makedirs(directory, exist_ok=True)
    prefix = re.sub(r"[^A-Za-z0-9_.-]", "_", label) + "-failure-"
    fd, path = tempfile.mkstemp(prefix=prefix, suffix=".log", dir=directory)
    with os.fdopen(fd, "wb") as output:
        for stream in (stdout, stderr):
            if stream is not None:
                output.write(stream if isinstance(stream, bytes) else stream.encode("utf-8"))
    return os.path.abspath(path)


def run_lane(lane: Lane, quiet: bool, allowed_skips) -> tuple[bool, str]:
    env = dict(os.environ)
    env.update(lane.env)
    started = time.time()
    proc = subprocess.run(lane.argv, cwd=_REPO, env=env, capture_output=True, text=True)
    text = (proc.stdout or "") + (proc.stderr or "")
    took = time.time() - started

    ok = proc.returncode == 0
    detail = f"exit {proc.returncode}  ({took:.0f}s)"

    if lane.is_pytest:
        counts = _count_outcomes(text)
        unexplained = _unexplained_skips(text, allowed_skips)
        # A `skipped` count with no SKIPPED line behind it means the reason was never printed,
        # so no ALLOWED_SKIPS entry can have excused it: unexplained by construction.
        # pytest AGGREGATES identical (location, reason) pairs as "SKIPPED [N] path: reason",
        # so the number of lines is not the number of skips: sum the bracketed counts instead.
        # Counting lines read fine while skips were few and distinct, and started failing every
        # lane the moment one generation contributed hundreds of same-reason skips.
        reason_lines = sum(int(m.group(1) or 1) for m in
                           re.finditer(r"(?m)^SKIPPED(?:[ \t]*\[(\d+)\])?", text))
        detail = (f"{counts['passed']} passed, {counts['skipped']} skipped "
                  f"({len(unexplained)} unexplained), {counts['failed']} failed, "
                  f"{counts['xfailed']} xfailed, {counts['deselected']} deselected  "
                  f"({took:.0f}s)")
        if counts["passed"] == 0:
            ok = False
            detail += "  no passing tests executed; collection or empty output is not a pass"
        if counts["skipped"] > reason_lines:
            ok = False
            detail += (f"  {counts['skipped']} skipped but only {reason_lines} SKIPPED reason "
                       f"lines printed")
        # Deselection counts too: a test filtered out by -k or a marker is a test that did
        # not run, and this gate cannot tell the difference between that and not existing.
        if (unexplained or counts["xfailed"] or counts["xpassed"]
                or counts["deselected"] or counts["error"] or counts["failed"]):
            ok = False
        if unexplained and not quiet:
            print("  skips with no entry in ALLOWED_SKIPS:")
            for line in unexplained:
                print(f"    - {line}")

    if not ok:
        output_path = save_failure_output(proc.stdout, proc.stderr, repo=_REPO, label=lane.name)
        detail += f"  full output: {output_path}"
        if not quiet:
            print(f"Full output: {output_path}")
            print(text[-4000:])
    return ok, detail


def run_gate(*, title, lanes, requirements, slow, run_lane, description="", argv=None) -> int:
    """The shared CLI: `--list`/`--lane`/`--quick`/`--quiet`, run in `title`'s manifest order.

    `run_lane` is taken as a parameter (not imported here) so a calling module's own
    `run_lane(lane, quiet)` wrapper -- the thing tests monkeypatch -- is what actually runs.
    """
    ap = argparse.ArgumentParser(description=description,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--quick", action="store_true",
                    help="stop before the lanes that need an emulator")
    ap.add_argument("--list", action="store_true", help="show the lanes and exit")
    ap.add_argument("--lane", action="append", metavar="NAME",
                    help="run only this lane (repeatable); see --list for the names")
    ap.add_argument("--quiet", action="store_true", help="do not dump failing output")
    args = ap.parse_args(argv)

    names = [lane.name for lane in lanes]
    if args.lane:
        unknown = [name for name in args.lane if name not in names]
        if unknown:
            print(f"unknown lane(s): {', '.join(unknown)}", file=sys.stderr)
            print(f"lanes: {', '.join(names)}", file=sys.stderr)
            return 2

    if args.list:
        for lane in lanes:
            mark = "slow" if lane.name in slow else "fast"
            print(f"  {lane.name:<18} [{mark}]  {lane.why}")
            print(f"    requirements: {', '.join(requirements[lane.name])}")
        return 0

    wanted = set(args.lane) if args.lane else set(names)
    selected = [x for x in lanes
                if x.name in wanted and not (args.quick and x.name in slow)]
    print(f"{title} — {len(selected)} lanes\n")
    failed = []
    for lane in selected:
        print(f"[ .. ] {lane.name}", flush=True)
        ok, detail = run_lane(lane, args.quiet)
        print(f"[{'PASS' if ok else 'FAIL'}] {lane.name:<18} {detail}")
        if not ok:
            failed.append(lane.name)

    print()
    if failed:
        print(f"GATE FAILED — {', '.join(failed)}")
        print("A skipped, xfailed or deselected test counts as a failure here: it did not "
              "run, and 'did not run' is not 'passed'.")
        return 1
    if args.quick:
        print("Fast lanes passed. The emulator lanes were NOT run, so this is not a "
              "release verdict — re-run without --quick.")
        return 0
    if args.lane:
        print("LANE(S) PASSED — not a release verdict: only the named lanes ran, so nothing "
              "here says anything about the lanes that did not.")
        return 0
    print("GATE PASSED — every lane ran and every lane passed.")
    return 0
