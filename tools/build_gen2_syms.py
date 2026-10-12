"""Build all four locked Gen 2 ROMs with upstream Makefiles, then publish symbols.

Normal runs require existing artifact pins. The explicit --record-artifact-hashes
bootstrap fills UNBUILT entries only, after verifying every ROM and artifact.
--check rebuilds and compares published symbols without writing output or lock.
Source checkouts and intermediate build products remain in the dedicated cache.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from itertools import groupby
from pathlib import Path

if __package__:
    from ._build_tools_bootstrap import W64DEVKIT_VERSION, ensure_rgbds, ensure_w64devkit
else:
    from _build_tools_bootstrap import W64DEVKIT_VERSION, ensure_rgbds, ensure_w64devkit

ROOT = Path(__file__).resolve().parents[1]
RGBDS_BINARIES = ("rgbasm", "rgblink", "rgbfix", "rgbgfx")
SOURCE_OUTPUTS = {
    "pokecrystal": ("pokecrystal", "pokecrystal11"),
    "pokegold": ("pokegold", "pokesilver"),
}


def _json_bytes(value: dict) -> bytes:
    return (json.dumps(value, indent=2) + "\n").encode("utf-8")


def _digest(data: bytes, algorithm: str = "sha256") -> str:
    return hashlib.new(algorithm, data).hexdigest()


def _artifact_text(data: bytes) -> bytes:
    def alias_address(line: bytes):
        match = re.match(rb"^\s+\$([0-9a-fA-F]+) = ", line)
        return match[1] if match else None

    # RGBDS's equal-address alias order differs across C runtimes.
    lines = []
    for address, group in groupby(data.replace(b"\r\n", b"\n").splitlines(keepends=True), alias_address):
        lines.extend(sorted(group) if address is not None else group)
    return b"".join(lines)


def _is_digest(value: object, length: int) -> bool:
    return isinstance(value, str) and re.fullmatch(rf"[0-9a-f]{{{length}}}", value) is not None


def _load_lock(path: Path, record: bool) -> tuple[dict, bytes]:
    raw = path.read_bytes()
    lock = json.loads(raw)
    if not isinstance(lock, dict) or lock.get("schema_version") != 1:
        raise RuntimeError("unsupported Gen 2 lock schema_version")
    if lock.get("rgbds_version") != "v1.0.3":
        raise RuntimeError("Gen 2 requires pinned RGBDS v1.0.3")
    if lock.get("w64devkit_version") != W64DEVKIT_VERSION:
        raise RuntimeError(f"unsupported w64devkit pin: {lock.get('w64devkit_version')}")
    sources, outputs = lock.get("sources"), lock.get("outputs")
    if not isinstance(sources, dict) or set(sources) != set(SOURCE_OUTPUTS):
        raise RuntimeError("lock must contain both pokecrystal and pokegold sources")
    expected = {name for names in SOURCE_OUTPUTS.values() for name in names}
    if not isinstance(outputs, dict) or set(outputs) != expected:
        raise RuntimeError("lock must contain all four Gen 2 outputs")
    for source, names in SOURCE_OUTPUTS.items():
        spec = sources[source]
        if (not isinstance(spec, dict)
                or spec.get("url") != f"https://github.com/pret/{source}"
                or not _is_digest(spec.get("commit"), 40)
                or spec.get("make_targets") != [f"{name}.gbc" for name in names]):
            raise RuntimeError(f"invalid pinned source: {source}")
        for name in names:
            output = outputs[name]
            if (not isinstance(output, dict) or output.get("source") != source
                    or output.get("filename") != f"{name}.gbc"
                    or not _is_digest(output.get("sha1"), 40)
                    or not {"sym_sha256", "map_sha256"}.issubset(output)):
                raise RuntimeError(f"invalid pinned ROM: {name}")
            hashes = [output.get(f"{ext}_sha256") for ext in ("sym", "map")]
            if output.get("state") == "UNBUILT" and hashes == [None, None]:
                if not record:
                    raise RuntimeError(f"{name} is UNBUILT; use explicit --record-artifact-hashes")
            elif output.get("state") != "BUILT" or not all(_is_digest(h, 64) for h in hashes):
                raise RuntimeError(f"{name}: state must be UNBUILT/null-null or BUILT/pinned-pinned")
    return lock, raw


def _run(command: list[str], **kwargs) -> str:
    result = subprocess.run(command, capture_output=True, text=True, **kwargs)
    if result.returncode:
        raise RuntimeError(
            f"command failed ({result.returncode}): {command}\n{result.stdout}{result.stderr}"
        )
    return (result.stdout or "") + (result.stderr or "")


def _source_check(repo: Path, expected: str) -> None:
    if not repo.is_dir() or not (repo / "Makefile").is_file():
        raise RuntimeError(f"source repository or upstream Makefile missing: {repo}")
    top = _run(["git", "-C", str(repo), "rev-parse", "--show-toplevel"]).strip()
    if Path(top).resolve() != repo.resolve():
        raise RuntimeError(f"source must be a repository root: {repo}")
    observed = _run(["git", "-C", str(repo), "rev-parse", "HEAD"]).strip()
    if observed != expected:
        raise RuntimeError(f"{repo}: HEAD {observed} != locked commit {expected}")
    status = _run(["git", "-C", str(repo), "status", "--porcelain", "--untracked-files=all"])
    if status.strip():
        raise RuntimeError(f"dirty source tree refused: {repo}\n{status}")


def _source(name: str, provided: Path | None, spec: dict) -> Path:
    repo = (provided if provided is not None else ROOT / ".cache" / "gen2-build" / name).resolve()
    if not repo.exists() and provided is None:
        repo.parent.mkdir(parents=True, exist_ok=True)
        _run(["git", "clone", "--no-checkout", spec["url"], str(repo)])
        _run(["git", "-C", str(repo), "checkout", "--detach", spec["commit"]])
    _source_check(repo, spec["commit"])
    return repo


def _binary(directory: Path, name: str) -> Path:
    path = directory / (name + (".exe" if os.name == "nt" else ""))
    if not path.is_file():
        raise RuntimeError(f"required build tool missing: {path}")
    return path


def _path_binary(directory: Path, name: str, env: dict) -> Path:
    binary = _binary(directory, name)
    selected = shutil.which(name, path=env["PATH"])
    # Compare file identities: Windows case variants and hardlinks may name the
    # same verified executable, while a byte-identical copy is still another file.
    if selected is None or not binary.samefile(selected):
        raise RuntimeError(f"{name}: PATH resolves to {selected!r}, not verified tool {binary}")
    return binary


def _toolchains(rgbds: Path, devkit: Path, lock: dict) -> tuple[dict, dict]:
    env = os.environ.copy()
    env["PATH"] = os.pathsep.join((str(rgbds), str(devkit), env.get("PATH", "")))
    # Caller make flags/overrides must not replace the verified compiler or skip work.
    for name in ("MAKEFLAGS", "GNUMAKEFLAGS", "MFLAGS", "MAKEOVERRIDES", "MAKEFILES",
                 "MAKE", "RM", "DEBUG", "RGBDS", "RGBASM", "RGBLINK", "RGBFIX", "RGBGFX",
                 "RGBASMFLAGS", "RGBLINKFLAGS", "RGBFIXFLAGS", "RGBGFXFLAGS",
                 "CC", "CFLAGS", "CPPFLAGS", "LDFLAGS"):
        env.pop(name, None)
    record = {"rgbds": {"version": lock["rgbds_version"], "binaries": {}},
              "build_tools": {"binaries": {}},
              "w64devkit_requested_version": lock["w64devkit_version"]}
    for name in (*RGBDS_BINARIES, "make", "gcc", "sh"):
        binary = _path_binary(rgbds if name in RGBDS_BINARIES else devkit, name, env)
        probe = ["-c", "printf gen2-shell-ready"] if name == "sh" else ["--version"]
        observed = _run([str(binary), *probe],
                        env=env, timeout=10).strip()
        if name in RGBDS_BINARIES:
            if not re.fullmatch(rf"{name}\s+{re.escape(lock['rgbds_version'])}", observed):
                raise RuntimeError(f"{name}: observed version {observed!r} != {lock['rgbds_version']}")
            group = "rgbds"
        else:
            if not observed or (name == "make" and not observed.startswith("GNU Make ")):
                raise RuntimeError(f"{name}: missing or unsupported tool version: {observed!r}")
            if name == "sh" and observed != "gen2-shell-ready":
                raise RuntimeError("build shell cannot execute commands")
            group = "build_tools"
        record[group]["binaries"][name] = {
            "version": observed.splitlines()[0], "sha256": _digest(binary.read_bytes()),
        }
    # GNU make can export the original environment SHELL to recursive make even
    # when the top-level command overrides SHELL. Bind it only after verification.
    env["SHELL"] = "sh"
    env["MAKESHELL"] = "sh"
    return record, env


def _parse_map(data: bytes) -> dict:
    if __package__:
        from .rgbds_map import parse_map
    else:
        from rgbds_map import parse_map
    return parse_map(data.decode("utf-8"))


def _publish(files: dict[Path, bytes]) -> None:
    """Stage every file before replacement; restore originals on a handled IO error.

    This is not a crash-atomic directory transaction. Validation failures never
    reach this function, and normal publication errors roll back replaced files.
    """
    originals = {path: path.read_bytes() if path.exists() else None for path in files}
    staged: dict[Path, Path] = {}
    replaced: list[Path] = []
    try:
        for path, data in files.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.",
                                             suffix=".tmp", delete=False) as stream:
                staged[path] = Path(stream.name)
                stream.write(data)
        for path, temporary in staged.items():
            temporary.replace(path)
            replaced.append(path)
    except OSError:
        for path in reversed(replaced):
            original = originals[path]
            if original is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(original)
        raise
    finally:
        for temporary in staged.values():
            temporary.unlink(missing_ok=True)


def _build(args: argparse.Namespace) -> int:
    lock, original_lock = _load_lock(args.lock, args.record_artifact_hashes)
    repos = {
        name: _source(name, args.crystal_repo if name == "pokecrystal" else args.gold_repo, spec)
        for name, spec in lock["sources"].items()
    }
    rgbds = (args.rgbds_bin or ensure_rgbds(lock["rgbds_version"])).resolve()
    devkit = (args.w64devkit_bin or ensure_w64devkit()).resolve()
    toolchain, env = _toolchains(rgbds, devkit, lock)
    commands = {}
    for name, repo in repos.items():
        # The upstream recursive $(MAKE) and $(RGBDS) commands are not quoted.
        # Bare names resolve in the verified PATH and work in space-bearing paths.
        common = [str(_binary(devkit, "make")), "RGBDS=", "CC=gcc", "MAKE=make", "SHELL=sh"]
        clean = [*common, "clean"]
        command = [*common, "-j4", *lock["sources"][name]["make_targets"]]
        print(f"[gen2] building {name} at {lock['sources'][name]['commit']}", file=sys.stderr)
        _run(clean, cwd=str(repo), env=env)
        _run(command, cwd=str(repo), env=env)
        commands[name] = {"clean": clean, "build": command}
        _source_check(repo, lock["sources"][name]["commit"])

    artifacts: dict[str, bytes] = {}
    roms, symbols, maps = {}, {}, {}
    for name, spec in lock["outputs"].items():
        repo = repos[spec["source"]]
        rom = (repo / spec["filename"]).read_bytes()
        observed = _digest(rom, "sha1")
        if observed != spec["sha1"]:
            raise RuntimeError(f"{name}: ROM sha1 {observed} != locked {spec['sha1']}")
        roms[name] = {"source": spec["source"], "filename": spec["filename"], "sha1": observed}
        for ext in ("sym", "map"):
            filename = f"{name}.{ext}"
            data = (repo / filename).read_bytes()
            if not data.strip():
                raise RuntimeError(f"empty build artifact: {filename}")
            observed = _digest(data)
            expected = spec[f"{ext}_sha256"]
            if expected is not None and observed != expected:
                published = args.out_dir / filename
                pinned = published.read_bytes() if published.is_file() else b""
                if _digest(pinned) != expected or _artifact_text(data) != _artifact_text(pinned):
                    raise RuntimeError(f"{filename}: sha256 {observed} != locked {expected}")
                data = pinned
                observed = expected
            if ext == "map":
                maps[name] = _parse_map(data)
            artifacts[filename], symbols[filename] = data, observed
            spec[f"{ext}_sha256"] = observed
        spec["state"] = "BUILT"

    if args.lock.read_bytes() != original_lock:
        raise RuntimeError("lock changed during build; refusing publication")
    for name, repo in repos.items():
        _source_check(repo, lock["sources"][name]["commit"])
    for group, directory in (("rgbds", rgbds), ("build_tools", devkit)):
        for name, record in toolchain[group]["binaries"].items():
            if _digest(_path_binary(directory, name, env).read_bytes()) != record["sha256"]:
                raise RuntimeError(f"build tool changed during build: {name}")
    if args.check:
        for filename, data in artifacts.items():
            published = args.out_dir / filename
            if not published.is_file() or published.read_bytes() != data:
                raise RuntimeError(f"--check: published artifact differs or missing: {published}")
        print("[gen2] --check: all four builds and published symbols verified", file=sys.stderr)
        return 0

    resulting_lock = _json_bytes(lock) if args.record_artifact_hashes else original_lock
    provenance = {
        "schema": "gen2-build-provenance-v1", "schema_version": 1,
        "evidence_level": "SOURCE", "generated": datetime.now(UTC).isoformat(),
        "lock_sha256": _digest(resulting_lock),
        "sources": {name: {**spec, "clean": True} for name, spec in lock["sources"].items()},
        "toolchain": toolchain, "commands": commands, "roms": roms, "symbols": symbols,
    }
    files = {args.out_dir / name: data for name, data in artifacts.items()}
    files[args.out_dir / "build_provenance.json"] = _json_bytes(provenance)
    files[args.out_dir / "linker_slack.json"] = _json_bytes({
        "schema": "gen2-linker-slack-v1", "schema_version": 1,
        "evidence": "SOURCE", "allocation_status": "CANDIDATE", "artifacts": maps,
    })
    if args.record_artifact_hashes:
        files[args.lock] = resulting_lock
    _publish(files)
    print(f"[gen2] verified and published eight artifacts and two reports to {args.out_dir}",
          file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", type=Path, default=ROOT / "data/gen2_sources.lock.json")
    parser.add_argument("--out-dir", type=Path, default=ROOT / "data/gen2")
    parser.add_argument("--crystal-repo", type=Path)
    parser.add_argument("--gold-repo", type=Path)
    parser.add_argument("--rgbds-bin", type=Path)
    parser.add_argument("--w64devkit-bin", type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--record-artifact-hashes", action="store_true")
    args = parser.parse_args(argv)
    args.lock, args.out_dir = args.lock.resolve(), args.out_dir.resolve()
    try:
        return _build(args)
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
