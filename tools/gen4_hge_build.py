#!/usr/bin/env python3
"""Fresh hg-engine build on the owner's Linux box (`hgbox`), pulled into the cache.

Replicates the fork's build-remote.sh (tar-over-ssh sync, `make -j8`) but never
writes inside the fork: outputs go only under
`.cache/gen4/hge/build-<commit12>/`. Default mode is --plan (touches nothing);
--build runs for real. Uses key-based ssh only (BatchMode); no credential files.

Exit codes: 0 PASS (fresh test.nds sha1 == pin), 1 FAIL/refused, 2 hgbox unreachable (skip).
A sha1 mismatch is a finding for the owner: reproducibility is unproven
(unpinned devkitARM/armips); this tool never repins.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from tools import gen4_pins  # noqa: E402

LOCK = REPO / "data" / "gen4_sources.lock.json"
CACHE = REPO / ".cache" / "gen4" / "hge"
FORK = gen4_pins.default_locations().sources["hg_engine_fork"]  # SLINK_GEN4_HG_ENGINE_FORK
HOST, RDIR, JOBS = "hgbox", "git/hg-engine", 8
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15"]
SCP = ["scp", "-q", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15"]
# Same excludes as build-remote.sh.
TAR = ["tar", "--exclude-vcs", "--exclude=*.nds", "--exclude=.venv",
       "--exclude=./build_output", "--exclude=./docs", "-cf", "-", "."]
NM_CMD = (f"cd ~/{RDIR} && for f in build/linked.o build/*_linked.o; do "
          'echo "## $f"; arm-none-eabi-nm -n "$f"; done')
PULL = ("test.nds", "offsets.ini", "rom_gen.ld")  # rom_gen.ld lives in build/ on the box
REMOTE_PATH = {"test.nds": "test.nds", "offsets.ini": "offsets.ini", "rom_gen.ld": "build/rom_gen.ld"}
ABS_SYM = re.compile(rb"[0-9a-f]{8} [aA] ")
EXPORTS = ("offsets.ini", "rom_gen.ld", "nm_all.txt")  # compared with the cached copies


class Skip(Exception):
    """hgbox unreachable."""


class Refused(Exception):
    pass


def _hashes(path: Path) -> dict:
    data = path.read_bytes()
    return {"size_bytes": len(data), **{a: hashlib.new(a, data).hexdigest() for a in ("sha1", "md5", "sha256")}}


def _git(fork: Path, *args: str) -> str:
    return subprocess.run(["git", "--no-optional-locks", "-C", str(fork), *args], capture_output=True, text=True, check=True).stdout


def plan_lines(fork: Path, out: Path) -> list[str]:
    return [
        f"git -C {fork} status --porcelain --untracked-files=no   (must be empty)",
        f"git -C {fork} rev-parse HEAD",
        " ".join(SSH + [HOST, "true"]) + "   (reachability; unreachable => skip, exit 2)",
        f"(cd {fork} && {' '.join(TAR)}) | {' '.join(SSH)} {HOST} 'mkdir -p ~/{RDIR} && tar -C ~/{RDIR} -xf -'",
        f"{' '.join(SSH)} {HOST} 'cd ~/{RDIR} && make -j{JOBS}'",
        f"(cd {out} && {' '.join(SCP)} {' '.join(f'{HOST}:~/{RDIR}/{REMOTE_PATH[n]}' for n in PULL)} .)",
        f"{' '.join(SSH)} {HOST} '{NM_CMD}' > {out / 'nm_all.txt'}",
        f"write {out / 'manifest.json'}; compare test.nds sha1 with the pin in {LOCK.name}",
    ]


def build(fork: Path | None = None, cache: Path | None = None, lock: Path | None = None, log=print) -> int:
    fork, cache, lock = (fork or FORK).resolve(), (cache or CACHE).resolve(), lock or LOCK
    if _git(fork, "status", "--porcelain", "--untracked-files=no").strip():
        raise Refused("fork has tracked changes; commit/discard them first (build-remote tars the dirty tree)")
    head = _git(fork, "rev-parse", "HEAD").strip()
    out = (cache / f"build-{head[:12]}").resolve()
    # Never write inside the fork, and only under the cache root.
    if fork in out.parents or cache not in out.parents:
        raise Refused(f"output dir {out} must be under {cache} and outside {fork}")
    pin = json.loads(lock.read_text(encoding="utf-8"))["artifacts"]["heartgold_hge"]["sha1"]

    if subprocess.run(SSH + [HOST, "true"], capture_output=True).returncode != 0:
        raise Skip(f"{HOST} unreachable")
    started = datetime.now(UTC).isoformat()
    out.mkdir(parents=True, exist_ok=True)

    log(f">> sync {fork} -> {HOST}:~/{RDIR}")
    tar = subprocess.Popen(TAR, cwd=fork, stdout=subprocess.PIPE)
    rc = subprocess.run(SSH + [HOST, f"mkdir -p ~/{RDIR} && tar -C ~/{RDIR} -xf -"], stdin=tar.stdout).returncode
    tar.stdout.close()
    if tar.wait() or rc:
        raise RuntimeError(f"sync failed (tar/ssh rc {tar.returncode}/{rc})")

    make_cmd = f"cd ~/{RDIR} && make -j{JOBS}"
    log(f">> {HOST}: {make_cmd}  (log: {out / 'make.log'})")
    with open(out / "make.log", "wb") as lf:
        rc = subprocess.run(SSH + [HOST, make_cmd], stdout=lf, stderr=subprocess.STDOUT).returncode
    if rc:
        raise RuntimeError(f"make failed rc={rc}; see {out / 'make.log'}")

    log(">> pull test.nds, offsets.ini, rom_gen.ld, nm_all.txt")
    srcs = [f"{HOST}:~/{RDIR}/{REMOTE_PATH[n]}" for n in PULL]
    # cwd + "." dest: a Windows drive path like E:\... would be parsed by scp as host "E".
    if subprocess.run(SCP + srcs + ["."], cwd=out).returncode:
        raise RuntimeError("scp pull failed")
    nm = subprocess.run(SSH + [HOST, NM_CMD], capture_output=True)
    if nm.returncode:
        raise RuntimeError("remote nm failed")
    (out / "nm_all.txt").write_bytes(nm.stdout)

    files = {n: _hashes(out / n) for n in (*PULL, "nm_all.txt")}
    fresh = files["test.nds"]["sha1"]
    exports = {}
    for n in EXPORTS:
        old = cache / n
        new = (out / n).read_bytes()
        if n == "nm_all.txt":  # the cached copy predates this tool and omits absolute (a/A) symbols
            new = b"".join(ln for ln in new.splitlines(True) if not ABS_SYM.match(ln))
        exports[n] = "no_cached_copy" if not old.is_file() else ("equal" if old.read_bytes() == new else "different")
    manifest = {
        "fork": str(fork), "fork_commit": head,
        "fork_clean_after": not _git(fork, "status", "--porcelain", "--untracked-files=no").strip(),
        "box_build_command": make_cmd, "host": HOST,
        "started_utc": started, "finished_utc": datetime.now(UTC).isoformat(),
        "pinned_sha1": pin, "fresh_sha1": fresh,
        "result": "PASS" if fresh == pin else "FAIL",
        "exports_vs_cache": exports, "files": files,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    log(f"fork commit {head}\nfresh sha1  {fresh}\npinned sha1 {pin}\nexports vs cache: {exports}\nmanifest: {out / 'manifest.json'}")
    if fresh != pin:
        log("FAIL: fresh test.nds does not match the pin (reproducibility unproven; NOT repinned)")
        return 1
    log("PASS: fresh build matches the pinned hge sha1")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--build", action="store_true", help="run the real build (default: --plan, touch nothing)")
    ap.add_argument("--plan", action="store_true", help="print what would run (default)")
    ap.add_argument("--fork", type=Path, default=FORK)
    args = ap.parse_args(argv)
    if not args.build:
        print("\n".join(plan_lines(args.fork, CACHE / "build-<commit12>")))
        return 0
    try:
        return build(args.fork)
    except Skip as e:
        print(f"SKIP: {e}")
        return 2
    except (Refused, RuntimeError, subprocess.CalledProcessError) as e:
        print(f"FAIL: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
