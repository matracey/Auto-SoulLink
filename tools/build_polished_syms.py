"""
tools/build_polished_syms.py — P0: locked, verified canonical Polished Crystal build

Mirrors tools/build_purergb_syms.py for Polished Crystal v3.2.3 (standard
variant). Exports the pinned source commit (git archive, so the build tree is
always pristine), drives `make` via w64devkit + RGBDS, sha1-verifies the ROM
against data/polished_sources.lock.json and — only on a match — publishes
data/polished/polishedcrystal.{sym,map} (LF-normalised), build_provenance.json
and free_space.txt. On any mismatch it raises and writes nothing.

Windows notes (see the lock's make_args_note): the upstream tools/Makefile
hard-codes -flto, which w64devkit's gcc lacks, so `make CFLAGS=-O2` is passed
(space-free on purpose: CFLAGS travels through MAKEFLAGS to the tools sub-make
and a space would split it). Only the host helper tools are affected, never
the ROM. The build dir must be space-free (the repo path under Google Drive
is not), so it defaults to $SLINK_WORK_ROOT/cache/polished/build.
The export must not overlap the source repo in either direction. An existing
build directory is replaced only when it contains this tool's .slink-export marker.

Usage:
    python tools/build_polished_syms.py                      # clone (if absent) + build + publish
    python tools/build_polished_syms.py --repo-dir PATH      # reuse an existing clone
    python tools/build_polished_syms.py --build-dir PATH     # space-free export dir
    python tools/build_polished_syms.py --rgbds-bin PATH     # skip RGBDS auto-install
    python tools/build_polished_syms.py --w64devkit-bin PATH # skip w64devkit auto-install
    python tools/build_polished_syms.py --check              # build + compare, publish nothing

Output: data/polished/polishedcrystal.{sym,map}, build_provenance.json, free_space.txt
Regenerate free_space.txt alone from the published map:
    python tools/build_polished_syms.py --free-space-only
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import platform
import re
import shutil
import subprocess
import sys
import time
import zlib
from datetime import UTC, datetime

from _build_tools_bootstrap import ensure_rgbds, ensure_w64devkit, space_free_toolchain
from slink_space import work_root

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
LOCK_PATH = DATA_DIR / "polished_sources.lock.json"
OUT_DIR = DATA_DIR / "polished"
PROVENANCE_PATH = OUT_DIR / "build_provenance.json"
FREE_SPACE_PATH = OUT_DIR / "free_space.txt"

PROVENANCE_SCHEMA = "polished-build-provenance-v1"

RGBDS_BINARIES = ("rgbasm", "rgblink", "rgbfix", "rgbgfx")
DEVKIT_BINARIES = ("make", "gcc")


def load_lock() -> dict:
    return json.loads(LOCK_PATH.read_text(encoding="utf-8"))


def _write_json(path: pathlib.Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def _git(repo: pathlib.Path, *args: str, text: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=text, check=True)


def verify_source(repo_dir: pathlib.Path | None, lock: dict) -> pathlib.Path:
    """Clone (or reuse) the source repo and confirm the locked commit exists.
    The clone is never modified: the build runs on a `git archive` export."""
    src = lock["source"]
    repo = repo_dir if repo_dir is not None else work_root("cache") / "polished" / "src"
    if not repo.exists():
        repo.parent.mkdir(parents=True, exist_ok=True)
        print(f"[polished] git clone {src['url']} -> {repo}", file=sys.stderr)
        subprocess.run(["git", "clone", src["url"], str(repo)], check=True)
    commit = _git(repo, "rev-parse", f"{src['commit']}^{{commit}}").stdout.strip()
    if commit != src["commit"]:
        raise RuntimeError(f"locked commit {src['commit']} resolves to {commit}")
    tag = _git(repo, "rev-parse", f"{src['tag']}^{{commit}}").stdout.strip()
    if tag != src["commit"]:
        raise RuntimeError(f"tag {src['tag']} -> {tag} != locked commit {src['commit']}")
    return repo


EXPORT_MARKER = ".slink-export"


def remove_tree(path: pathlib.Path, attempts: int = 6, delay: float = 0.5) -> None:
    """Delete `path`, or fail closed.

    A concurrent writer (another card building the same shared cache tree) leaves a directory
    non-empty and `shutil.rmtree` raises WinError 145. Retrying rides that out when it is a
    transient handle; when it is a genuinely concurrent build this raises instead, so no caller
    ever continues onto a HALF-DELETED tree -- the failure mode that produced bogus
    "No such file or directory" errors deep inside make.
    """
    last: Exception | None = None
    for attempt in range(attempts):
        if not path.exists():
            return
        try:
            shutil.rmtree(path)
        except OSError as err:          # includes PermissionError and WinError 145
            last = err
        if not path.exists():
            return
        last = last or RuntimeError(f"{path} still exists after rmtree")
        time.sleep(delay * (attempt + 1))
    raise RuntimeError(
        f"could not remove {path} after {attempts} attempts: {last}. Another build is probably "
        f"using the same cache tree -- give this run its own SLINK_WORK_ROOT.")


def export_source(repo: pathlib.Path, commit: str, build_dir: pathlib.Path) -> None:
    """Export outside the source tree, replacing only a previously marked SLink export."""
    if " " in str(build_dir):
        raise RuntimeError(f"build dir {build_dir} contains a space; make cannot cope. Use --build-dir")
    source = repo.resolve()
    destination = build_dir.resolve()
    if destination.is_relative_to(source) or source.is_relative_to(destination):
        raise RuntimeError(f"build dir {build_dir} overlaps the source repo {repo}; use a separate --build-dir")
    if build_dir.exists() and (not build_dir.is_dir() or not (build_dir / EXPORT_MARKER).is_file()):
        raise RuntimeError(f"build dir {build_dir} is not an owned SLink export; choose a fresh --build-dir")
    remove_tree(build_dir)
    build_dir.mkdir(parents=True)
    # A stale tree that survived a delete would still carry the previous run's marker.
    (build_dir / EXPORT_MARKER).write_text(f"{commit}\n", encoding="utf-8")
    archive = subprocess.run(
        ["git", "-C", str(repo), "archive", commit], capture_output=True, check=True
    ).stdout
    subprocess.run(["tar", "-x", "-C", str(build_dir)], input=archive, check=True)


def _binary_name(name: str) -> str:
    return f"{name}.exe" if platform.system() == "Windows" else name


def _sha256(path: pathlib.Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def _lf(data: bytes) -> bytes:
    """rgbds on Windows writes CRLF; the repo pins .sym/.map as LF (.gitattributes)."""
    return data.replace(b"\r\n", b"\n")


def _toolchain_record(rgbds_bin: pathlib.Path, devkit_bin: pathlib.Path, lock: dict) -> dict:
    return {
        "rgbds": {
            "version": lock["rgbds_version"],
            "binaries": {n: _sha256(rgbds_bin / _binary_name(n)) for n in RGBDS_BINARIES},
        },
        "w64devkit": {
            "version": lock["w64devkit_version"],
            "binaries": {n: _sha256(devkit_bin / _binary_name(n)) for n in DEVKIT_BINARIES},
        },
    }


def _rom_facts(data: bytes, sha1: str) -> dict:
    return {
        "sha1": sha1,
        "md5": hashlib.md5(data).hexdigest(),
        "header_crc": data[0x14E:0x150].hex().upper(),
        "crc32": format(zlib.crc32(data), "08X"),
        "title": data[0x134:0x143].split(b"\x00")[0].decode("ascii", "replace"),
    }


# ---- free space (from the rgblink .map) -------------------------------------------

_BANK_RE = re.compile(r"^(ROM0|ROMX|VRAM|SRAM|WRAM0|WRAMX|OAM|HRAM) bank #(\d+):$")
_TOTAL_RE = re.compile(r"^\s*TOTAL EMPTY: \$([0-9a-f]+) bytes?$")
_BANK_SIZE = {"ROM0": 0x4000, "ROMX": 0x4000, "VRAM": 0x2000, "SRAM": 0x2000,
              "WRAM0": 0x1000, "WRAMX": 0x1000, "OAM": 0xA0, "HRAM": 0x7F}


def parse_map_free(map_text: str) -> dict[str, dict[int, int]]:
    """{kind: {bank: free bytes}} from rgblink's `-m` map ("TOTAL EMPTY" per bank)."""
    out: dict[str, dict[int, int]] = {}
    cur: tuple[str, int] | None = None
    for line in map_text.replace("\r\n", "\n").split("\n"):
        m = _BANK_RE.match(line)
        if m:
            cur = (m.group(1), int(m.group(2)))
            out.setdefault(cur[0], {})
            continue
        t = _TOTAL_RE.match(line)
        if t and cur:
            out[cur[0]][cur[1]] = int(t.group(1), 16)
            cur = None
    return out


def free_space_report(map_text: str, rom: bytes, command: str) -> str:
    free = parse_map_free(map_text)
    total_banks = 2 << rom[0x148]          # header ROM-size code: 32 KiB << n
    romx = dict(free.get("ROMX", {}))
    unallocated = [b for b in range(1, total_banks) if b not in romx]
    for b in unallocated:                   # banks the linker never placed anything in
        romx[b] = 0x4000
    lines = [
        "Polished Crystal free space -- GENERATED, do not edit.",
        f"Command: {command}",
        f"ROM: {total_banks} banks ({total_banks * 0x4000} bytes), header size code {rom[0x148]:#04x}; "
        f"{len(unallocated)} ROMX bank(s) never allocated by the linker (fully free): {unallocated}",
        "",
    ]

    def section(title: str, banks: dict[int, int], size: int) -> None:
        total = sum(banks.values())
        lines.append(f"{title}: {total} bytes free across {len(banks)} bank(s)")
        for b in sorted(banks):
            if banks[b]:
                lines.append(f"  bank {b:>3}: {banks[b]:>6} free (${banks[b]:04x}) of {size}")
        lines.append("")

    section("ROM0", free.get("ROM0", {}), _BANK_SIZE["ROM0"])
    top = [kv for kv in sorted(romx.items(), key=lambda kv: (-kv[1], kv[0]))[:20] if kv[1]]
    lines.append(f"ROMX: {sum(romx.values())} bytes free across {len(romx)} bank(s) (incl. unallocated)")
    lines.append("  top banks by free bytes (max 20, nonzero only):")
    lines += [f"  bank {b:>3}: {v:>6} free (${v:04x})" for b, v in top]
    lines.append(f"  banks with any free space: {sum(1 for v in romx.values() if v)}")
    lines.append("")
    section("WRAM0", free.get("WRAM0", {}), _BANK_SIZE["WRAM0"])
    section("WRAMX", free.get("WRAMX", {}), _BANK_SIZE["WRAMX"])
    section("SRAM", free.get("SRAM", {}), _BANK_SIZE["SRAM"])
    section("HRAM", free.get("HRAM", {}), _BANK_SIZE["HRAM"])
    lines.append("(per-bank lists show only banks with free bytes; VRAM/OAM omitted)")
    return "\n".join(lines) + "\n"


def build_rom_syms(
    *,
    repo_dir: pathlib.Path | None = None,
    build_dir: pathlib.Path | None = None,
    rgbds_bin: pathlib.Path | None = None,
    w64devkit_bin: pathlib.Path | None = None,
    check: bool = False,
) -> int:
    lock = load_lock()
    repo = verify_source(repo_dir, lock)
    build_dir = build_dir or work_root("cache") / "polished" / "build"
    export_source(repo, lock["source"]["commit"], build_dir)

    rgbds_bin = space_free_toolchain(rgbds_bin or ensure_rgbds(lock["rgbds_version"]),
                                    f"rgbds-{lock['rgbds_version']}")
    devkit_bin = space_free_toolchain(w64devkit_bin or ensure_w64devkit(),
                                     f"w64devkit-{lock['w64devkit_version']}")

    env = os.environ.copy()
    env["PATH"] = os.pathsep.join([str(rgbds_bin), str(devkit_bin), env.get("PATH", "")])

    cmd = [str(devkit_bin / _binary_name("make")), "-j4", *lock["make_args"], *lock["make_targets"]]
    print(f"[polished] {' '.join(cmd)}  (cwd={build_dir})", file=sys.stderr)
    result = subprocess.run(cmd, cwd=str(build_dir), env=env, capture_output=True, text=True)
    if result.returncode != 0:
        sys.stderr.write(result.stdout[-6000:])
        sys.stderr.write(result.stderr[-6000:])
        raise RuntimeError(f"make failed with exit code {result.returncode}")

    spec = lock["outputs"]["polishedcrystal"]
    rom_path = build_dir / spec["filename"]
    if not rom_path.exists():
        raise RuntimeError(f"{rom_path} was not produced by the build")
    rom = rom_path.read_bytes()
    sha1 = hashlib.sha1(rom).hexdigest()
    if sha1 != spec["sha1"]:
        raise RuntimeError(
            f"build does not match {LOCK_PATH.name}: sha1 {sha1} != locked {spec['sha1']} "
            "-- publishing nothing"
        )
    print("[polished] ROM sha1 matches the lock", file=sys.stderr)

    stem = spec["filename"].removesuffix(".gbc")
    sym = _lf((build_dir / f"{stem}.sym").read_bytes())
    mapb = _lf((build_dir / f"{stem}.map").read_bytes())

    # The release .sym (if cached next to the clone) must equal ours modulo line endings.
    release_sym = work_root("cache") / "polished" / "release" / f"{stem}.sym"
    sym_vs_release = "release .sym not present"
    if release_sym.exists():
        rel_sha1 = hashlib.sha1(release_sym.read_bytes()).hexdigest()
        want = lock["release_assets"][f"{stem}.sym"]["sha1"]
        if rel_sha1 != want:
            raise RuntimeError(f"cached release .sym sha1 {rel_sha1} != locked {want}")
        if _lf(release_sym.read_bytes()) != sym:
            raise RuntimeError("built .sym differs from the release .sym (after LF normalisation)")
        sym_vs_release = "equal (LF-normalised)"
        print("[polished] built .sym equals the release .sym", file=sys.stderr)

    published = {"polishedcrystal.sym": sym, "polishedcrystal.map": mapb}
    if check:
        drift = [n for n, b in published.items()
                 if not (OUT_DIR / n).exists() or _lf((OUT_DIR / n).read_bytes()) != b]
        if drift:
            print(f"[polished] --check: drift from committed data/polished/: {drift}", file=sys.stderr)
            return 1
        print("[polished] --check: build reproduces the committed .sym/.map byte-for-byte", file=sys.stderr)
        return 0

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    symbol_hashes = {}
    for name, data in published.items():
        (OUT_DIR / name).write_bytes(data)
        symbol_hashes[name] = hashlib.sha256(data).hexdigest()

    make_cmd = " ".join(["make", "-j4", *lock["make_args"], *lock["make_targets"]])
    FREE_SPACE_PATH.write_text(
        free_space_report(mapb.decode("utf-8"), rom, "python tools/build_polished_syms.py"),
        encoding="utf-8", newline="\n")
    _write_json(PROVENANCE_PATH, {
        "schema": PROVENANCE_SCHEMA,
        "generated": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": lock["source"],
        "toolchain": _toolchain_record(rgbds_bin, devkit_bin, lock),
        "command": make_cmd,
        "roms": {"polishedcrystal": _rom_facts(rom, sha1)},
        "symbols": symbol_hashes,
        "sym_vs_release_sym": sym_vs_release,
    })
    print(f"[polished] published sym/map + free_space + provenance -> {OUT_DIR}", file=sys.stderr)
    return 0


def free_space_only() -> int:
    """Regenerate free_space.txt from the published map + the cached/built ROM."""
    lock = load_lock()
    rom_path = work_root("cache") / "polished" / "build" / lock["outputs"]["polishedcrystal"]["filename"]
    rom = rom_path.read_bytes()
    FREE_SPACE_PATH.write_text(
        free_space_report((OUT_DIR / "polishedcrystal.map").read_text(encoding="utf-8"), rom,
                          "python tools/build_polished_syms.py --free-space-only"),
        encoding="utf-8", newline="\n")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--repo-dir", type=pathlib.Path, default=None,
                   help="reuse an existing clone (default $SLINK_WORK_ROOT/cache/polished/src)")
    p.add_argument("--build-dir", type=pathlib.Path, default=None,
                   help="space-free export/build dir (default $SLINK_WORK_ROOT/cache/polished/build)")
    p.add_argument("--rgbds-bin", type=pathlib.Path, default=None)
    p.add_argument("--w64devkit-bin", type=pathlib.Path, default=None)
    p.add_argument("--check", action="store_true",
                   help="build and compare against the lock + committed data/polished/, write nothing")
    p.add_argument("--free-space-only", action="store_true")
    args = p.parse_args()
    try:
        if args.free_space_only:
            return free_space_only()
        return build_rom_syms(repo_dir=args.repo_dir, build_dir=args.build_dir,
                              rgbds_bin=args.rgbds_bin, w64devkit_bin=args.w64devkit_bin,
                              check=args.check)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
