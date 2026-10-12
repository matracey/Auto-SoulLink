"""Private-lane mechanics; feature verdicts belong to independent driver oracles.

Configuration is captured once per invocation, never in mutable module globals.
Recording readers fail closed: corrupt evidence is not an empty successful run.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class LaunchConfig:
    lane: Path
    kind: str
    mgr_run: Path
    player: str
    mode: str
    cache: Path
    rom_src: Path
    rom: Path
    sram: Path
    save_name: str
    fixture: Path
    sym: Path
    symbols: tuple[str, ...] = ()
    position: tuple[int, ...] = (24, 3, 48, 12)
    repo: Path = REPO
    emuhawk: str = "E:/Howard/Bizhawk/EmuHawk.exe"
    base_config: Path = Path("E:/Howard/Bizhawk/config.ini")
    rom_sha1: str | None = None
    stages: str = "1234"
    run_name: str = "live"
    swap: bool = False
    explore: str = "B"

    @classmethod
    def from_env(cls, env=None):
        e = os.environ if env is None else env
        lane = Path(e.get("POL_LANE", "F:/slink-work/lanes/pol-live"))
        kind, mode = e.get("POL_KIND", "overlay"), e.get("POL_POSMODE", "warp")
        mgr, player = Path(e.get("POL_MGR_RUN", "F:/slink-work/lanes/pol-rand/mgr_run")), e.get("POL_PLAYER", "a")
        cache = Path("F:/slink-work/cache/polished")
        source = cache / ("companion-overlay" if kind == "overlay" else "release") / "polishedcrystal-3.2.3.gbc"
        if kind == "rand":
            source = Path(e.get("POL_ROM") or mgr / "roms" / f"{player}.gbc")
        home = Path(e.get("SLINK_BIZHAWK_HOME", "E:/Howard/Bizhawk"))
        return cls(lane, kind, mgr, player, mode, cache, source,
                   lane / "rom" / f"pol_{kind}.gbc", lane / f"sram_{kind}", f"pol {kind}.SaveRAM",
                   Path(e.get("POL_FIXTURE") or lane / "fixture" / f"polished_{kind}_{mode}.SaveRAM"),
                   REPO / "data/polished/polished_slink.sym",
                   position=tuple(int(v) for v in e.get("POL_POS", "24,3,48,12").split(",")),
                   emuhawk=e.get("SLINK_EMUHAWK", str(home / "EmuHawk.exe")),
                   base_config=Path(e.get("SLINK_BIZHAWK_CONFIG", str(home / "config.ini"))),
                   rom_sha1=e.get("POL_ROM_SHA1"), stages=e.get("POL_STAGES", "1234"),
                   run_name=e.get("POL_RUNNAME", "live"), swap=e.get("POL_SWAP") == "1",
                   explore=e.get("POL_EXPLORE", "B"))


def parse_symbols(text, *, reject_duplicates=False):
    rows = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2 and ":" in parts[0]:
            bank, addr = parts[0].split(":")
            if reject_duplicates and parts[1] in rows:
                raise ValueError(f"duplicate symbol {parts[1]}")
            rows.setdefault(parts[1], [int(bank, 16), int(addr, 16)])
    return rows


def read_symbols(path, names=None, *, reject_duplicates=False):
    if names is not None and not names:
        return {}
    rows = parse_symbols(Path(path).read_text(encoding="utf-8"), reject_duplicates=reject_duplicates)
    if names is None:
        return rows
    missing = [name for name in names if name not in rows]
    if missing:
        raise SystemExit(f"symbols missing from {Path(path).name}: {missing}")
    return {name: rows[name] for name in names}


def _object(pairs):
    row = {}
    for key, value in pairs:
        if key in row:
            raise ValueError(f"duplicate JSON key {key}")
        row[key] = value
    return row


def _constant(value):
    raise ValueError(f"nonfinite JSON value {value}")


def loads_json(text):
    """Decode JSON without duplicate fields or nonfinite numeric extensions."""
    return json.loads(text, object_pairs_hook=_object, parse_constant=_constant)


def read_json(path):
    return loads_json(Path(path).read_text(encoding="utf-8"))


def read_jsonl(path):
    rows = []
    for number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = loads_json(line)
            if not isinstance(row, dict):
                raise ValueError("record must be an object")
        except ValueError as exc:
            raise ValueError(f"{path}:{number}: {exc}") from exc
        rows.append(row)
    return rows


def pinned_rom_bytes(source, want_sha1, *, patch=None, base_sha1=None):
    """Read/apply without writing, for dry-run and independent ROM-site checks."""
    data = Path(source).read_bytes()
    if base_sha1 is not None and hashlib.sha1(data).hexdigest() != base_sha1:
        raise ValueError("release ROM sha1 differs from provenance base_sha1")
    if patch is not None:
        from patch.tools.make_ups import ups_apply
        data = ups_apply(data, Path(patch).read_bytes())
    if hashlib.sha1(data).hexdigest() != want_sha1:
        raise ValueError(f"ROM sha1 differs from provenance {want_sha1}")
    return data


def stage_pinned_rom(source, destination, want_sha1, *, patch=None, base_sha1=None):
    try:
        data = pinned_rom_bytes(source, want_sha1, patch=patch, base_sha1=base_sha1)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    sha1 = hashlib.sha1(data).hexdigest()
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists() or destination.read_bytes() != data:
        destination.write_bytes(data)
    return sha1


def prepare_save(config, fixture=None, *, cold=False, clear=False):
    config.sram.mkdir(parents=True, exist_ok=True)
    if clear:
        for stale in config.sram.glob("*.SaveRAM*"):
            stale.unlink()
    save = config.sram / config.save_name
    if cold:
        save.unlink(missing_ok=True)
    else:
        shutil.copyfile(fixture or config.fixture, save)
    return save


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def kill_own(proc, label, *, run=subprocess.run):
    if proc.poll() is None:
        run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
        proc.wait(timeout=20)
    print(f"[pol-live] {label} pid {proc.pid} ended rc={proc.returncode}", flush=True)


def prepare_launch(config, lua, run_dir, env_extra, *, write_config=None, env=None, config_path=None):
    if write_config is None:
        from tools.gen1_playthrough import write_run_config
        write_config = write_run_config
    run_dir.mkdir(parents=True, exist_ok=True)
    out, cfg = run_dir / "result.txt", Path(config_path) if config_path else run_dir / "config.ini"
    out.unlink(missing_ok=True)
    longest = max(len(str(config.sram / config.save_name)), len(str(out)))
    if longest >= 200:
        raise SystemExit(f"lane path too long ({longest})")
    write_config(str(config.base_config), str(cfg), saveram_dir=str(config.sram), purergb=True)
    syms = run_dir / "syms.json"
    syms.write_text(json.dumps(read_symbols(config.sym, config.symbols)), encoding="utf-8")
    child_env = dict(os.environ if env is None else env)
    child_env.update(SLINK_ROOT=config.repo.as_posix(), POL_OUT=out.as_posix(), POL_SYMS=syms.as_posix(),
                     POL_RUN=run_dir.as_posix(), POL_KIND=config.kind, POL_POSMODE=config.mode)
    child_env.update(env_extra)
    command = [config.emuhawk, f"--lua={(config.repo / lua).as_posix()}", f"--config={cfg.as_posix()}", config.rom.as_posix()]
    return command, child_env, out


def start_owned(config, lua, run_dir, env_extra, *, popen=subprocess.Popen, config_path=None, write_config=None, stop=kill_own):
    """Start one prepared child; caller owns its lifetime and must call kill_own."""
    command, env, _ = prepare_launch(config, lua, run_dir, env_extra, config_path=config_path, write_config=write_config)
    proc = popen(command, cwd=str(config.repo), env=env)
    try:
        (run_dir / "emuhawk.pid").write_text(str(proc.pid))
    except BaseException:
        stop(proc, "EmuHawk")
        raise
    return proc


def launch(config, lua, run_dir, env_extra, timeout, poll=None, *, popen=subprocess.Popen,
           clock=time.monotonic, sleep=time.sleep, stop=kill_own, write_config=None):
    proc = start_owned(config, lua, run_dir, env_extra, popen=popen, write_config=write_config, stop=stop)
    out = run_dir / "result.txt"
    try:
        deadline = clock() + timeout
        while clock() < deadline and proc.poll() is None:
            sleep(2)
            if poll:
                poll()
            text = out.read_text(encoding="utf-8", errors="replace") if out.exists() else ""
            if any(line.startswith("RESULT:") for line in text.splitlines()):
                sleep(3)
                break
    finally:
        stop(proc, "EmuHawk")
    return (out.read_text(encoding="utf-8", errors="replace") if out.exists() else ""), proc.pid
