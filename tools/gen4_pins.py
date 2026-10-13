#!/usr/bin/env python3
"""Read-only Gen 4 G0 pin check; explicit --candidate creates a new lock.

The lock records file identity, not runtime, save, or release qualification.
All file hashes stream in one pass. Missing G0 inputs are OPEN (exit 2);
present mismatches are FAIL (exit 1). Later-gate inputs are descriptive only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_LOCK = REPO / "data" / "gen4_sources.lock.json"

ROM_SPECS = {
    "heartgold": ("4fcded0e2713dc03929845de631d0932ea2b5a37", "IPKE", "gen4_hgss"),
    "soulsilver": ("f8dc38ea20c17541a43b58c5e6d18c1732c7e582", "IPGE", "gen4_hgss"),
    "heartgold_hge": ("cb2dc435196d09c8c9209bf037240ed834f4cea1", "IPKE", "gen4_hge"),
    "platinum": ("ce81046eda7d232513069519cb2085349896dec7", "CPUE", "gen4_pt"),
}
MAP_SPECS = {
    "heartgold_xmap": ("39397e4c16f4fe907870ab8dac450a469a8ce9ce225eb98852771ff59fe877df", 11646615, "40eab3c65e0e4f46f6edd90540709aa9950ca07e"),
    "soulsilver_xmap": ("4ce745d56b34762300025279748b3ea3626bc2e87c0540e0a649388b758d07b2", 11646721, "40eab3c65e0e4f46f6edd90540709aa9950ca07e"),
    "platinum_xmap": ("c3b3451b4815a646514bf9d456062e644d83010f996eaff17abf8d2fe667e72a", 12107496, "a2a62d3d8966ffe5953a5ffca1cd4f8a45e79e61"),
}
SOURCE_COMMITS = {
    "pokeheartgold_citation": "ad7a3afa0cfc144fe6837c410cb95b2727217f54",
    "hg_engine_fork": "fc517576498305ecb5f5e1de44681c6e3822361b",
    "pokeplatinum_citation": "c248fb3f8cc9934ded800e489567c5c0eeee92eb",
}
HOST_ASSETS = (
    "bizhawk_emu_hawk", "bizhawk_melonds_waterbox", "bizhawk_cores",
    "bizhawk_config_observed", "hge_offsets", "hge_rom_gen_ld", "hge_nm_all",
    "gen4_pins_tool",
)
PENDING = {
    "gen4_hgss_pack": {"gate": "G2", "state": "PLANNED", "reason": "generated profile/signals/checkpoint are later-gate outputs"},
    "gen4_hge_pack": {"gate": "G2", "state": "PLANNED", "reason": "per-build pack is a later-gate output"},
    "hge_fresh_build_export_association": {"gate": "G1", "state": "PLANNED", "reason": "recorded export hashes do not prove association with a newly built ROM or live function sites"},
    "platinum_profile_bind": {"gate": "G2", "state": "PLANNED", "reason": "xMAP profile generation is a later-gate source check"},
    "soulsilver_owner_save": {"gate": "G2", "state": "OPEN", "reason": "owner first save has not been supplied"},
    "platinum_populated_save": {"gate": "G2", "state": "OPEN", "reason": "local save is blank"},
    "hge_populated_save": {"gate": "G2", "state": "OPEN", "reason": "empty AP-named file is not a populated-mon fixture"},
    "hge_duo_owner_saves": {"gate": "G4", "state": "OPEN", "reason": "two distinct owner-played saves have not been staged"},
    "qualified_run_config": {"gate": "G1", "state": "PLANNED", "reason": "observed config.ini hash does not qualify RTC/JIT lane settings"},
}


@dataclass(frozen=True)
class Locations:
    roms: dict[str, Path]
    assets: dict[str, Path]
    sources: dict[str, Path]


ENV_PREFIX = "SLINK_GEN4_"
# Stable skip-reason prefix for every check that needs the hg-engine fork source at its pin; the Gen 1
# release gate allow-lists this exact text. Only an explicitly supplied checkout (SLINK_GEN4_HG_ENGINE_FORK)
# or the repo cache copy can satisfy it -- a stray clone elsewhere on the host is never picked up.
HGE_FORK_ABSENT = "GEN4_HGE_FORK_SOURCE_ABSENT"


def env_name(key: str) -> str:
    return ENV_PREFIX + key.upper()


def hge_fork_absent(path: Path) -> str:
    return (f"{HGE_FORK_ABSENT}: hg_engine_fork {SOURCE_COMMITS['hg_engine_fork']} not available at {path} "
            f"(set {env_name('hg_engine_fork')})")


def _env(paths: dict[str, Path]) -> dict[str, Path]:
    return {key: Path(os.environ.get(env_name(key)) or path) for key, path in paths.items()}


def default_locations() -> Locations:
    """Each path is SLINK_GEN4_<KEY> when set, else the default below. The hg-engine ROM, its exports
    and the fork source default to the repo's .cache/gen4/hge layout, never a personal checkout."""
    bizhawk = Path("E:/Howard/Bizhawk")
    slink_inputs = Path("E:/Google Drive/SLink")
    xmap = REPO / ".cache" / "gen4" / "xmap"
    exports = REPO / ".cache" / "gen4" / "hge"
    return Locations(
        roms=_env({
            "heartgold": bizhawk / "Pokemon - HeartGold Version (USA).nds",
            "soulsilver": slink_inputs / "Pokemon - SoulSilver Version (USA).nds",
            "heartgold_hge": exports / "build-fc5175764983" / "test.nds",
            "platinum": slink_inputs / "Pokemon - Platinum Version (USA).nds",
        }),
        assets=_env({
            "heartgold_xmap": xmap / "heartgoldus.xMAP",
            "soulsilver_xmap": xmap / "soulsilverus.xMAP",
            "platinum_xmap": xmap / "platinumus.xMAP",
            "bizhawk_emu_hawk": bizhawk / "EmuHawk.exe",
            "bizhawk_melonds_waterbox": bizhawk / "dll" / "melonDS.wbx.zst",
            "bizhawk_cores": bizhawk / "dll" / "BizHawk.Emulation.Cores.dll",
            "bizhawk_config_observed": bizhawk / "config.ini",
            "hge_offsets": exports / "offsets.ini",
            "hge_rom_gen_ld": exports / "rom_gen.ld",
            "hge_nm_all": exports / "nm_all.txt",
            "gen4_pins_tool": Path(__file__).resolve(),
        }),
        sources=_env({
            "pokeheartgold_citation": Path("E:/Howard/hgss_archipelago-master/.tooling/pokeheartgold"),
            "hg_engine_fork": exports / "src",
            "pokeplatinum_citation": REPO / ".cache" / "gen4" / "pokeplatinum",
        }),
    )


def digest_file(path: Path, algorithms: tuple[str, ...]) -> dict[str, str | int]:
    hashes = {name: hashlib.new(name) for name in algorithms}
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            size += len(chunk)
            for hasher in hashes.values():
                hasher.update(chunk)
    return {"size_bytes": size, **{name: h.hexdigest() for name, h in hashes.items()}}


def nds_header_code(path: Path) -> str:
    with path.open("rb") as stream:
        stream.seek(0x0C)
        raw = stream.read(4)
    if len(raw) != 4 or any(not 0x20 <= b <= 0x7E for b in raw):
        raise ValueError("missing or invalid NDS header gamecode")
    return raw.decode("ascii")


def git_identity(path: Path) -> tuple[str, bool]:
    result = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True, timeout=15,
    )
    head = result.stdout.strip().lower()
    if len(head) != 40 or any(c not in "0123456789abcdef" for c in head):
        raise ValueError("invalid Git HEAD")
    status = subprocess.run(
        ["git", "-C", str(path), "status", "--porcelain", "--untracked-files=no"],
        capture_output=True, text=True, check=True, timeout=15,
    )
    return head, not bool(status.stdout.strip())


def _issue(issues: list[dict[str, str]], kind: str, name: str, message: str) -> None:
    issues.append({"kind": kind, "id": name, "reason": message})


def collect(
    locations: Locations,
    *,
    rom_specs: dict[str, tuple[str, str, str]] = ROM_SPECS,
    map_specs: dict[str, tuple[str, int, str]] = MAP_SPECS,
    source_commits: dict[str, str] = SOURCE_COMMITS,
    host_assets: tuple[str, ...] = HOST_ASSETS,
    source_reader: Callable[[Path], tuple[str, bool]] = git_identity,
) -> dict:
    """Observe all G0 inputs; reference mismatches fail even for a new candidate."""
    issues: list[dict[str, str]] = []
    artifacts: dict[str, dict] = {}
    assets: dict[str, dict] = {}
    sources: dict[str, dict] = {}
    for name, (expected_sha1, expected_header, foundation) in rom_specs.items():
        path = locations.roms[name]
        row = {"role": "rom", "state": "OPEN", "foundation": foundation}
        if name == "heartgold_hge":
            row["admission"] = "RECORDED_NOT_ADMITTED"
        elif name == "platinum":
            row["admission"] = "BIND_ONLY_NOT_ADMITTED"
        else:
            row["admission"] = "G0_IDENTITY_ONLY"
        try:
            row.update(digest_file(path, ("sha1", "md5", "sha256")))
            row["header_code"] = nds_header_code(path)
        except FileNotFoundError:
            _issue(issues, "OPEN", name, "required G0 ROM missing")
        except (OSError, ValueError) as exc:
            _issue(issues, "FAIL", name, f"ROM unreadable or invalid: {exc}")
        else:
            if row["sha1"] != expected_sha1.lower() or row["header_code"] != expected_header:
                _issue(issues, "FAIL", name, "present ROM differs from published SHA1/header pin")
            else:
                row["state"] = "PINNED"
        artifacts[name] = row
    for name in (*map_specs, *host_assets):
        path = locations.assets.get(name)
        role = "xmap" if name in map_specs else "tool" if name == "gen4_pins_tool" else "observed_host_config" if name == "bizhawk_config_observed" else "build_symbol_export" if name.startswith("hge_") else "emulator_file"
        row: dict = {"role": role, "gate": "G0", "state": "OPEN"}
        if role == "observed_host_config":
            row["qualification"] = "OBSERVED_ONLY_NOT_RUN_CONFIG"
        if role == "build_symbol_export":
            row["qualification"] = "RECORDED_EXPORT_FILE_ONLY_NOT_FRESH_BUILD_ASSOCIATION"
        if name in map_specs:
            row["source_commit"] = map_specs[name][2]
        try:
            if path is None:
                raise FileNotFoundError(name)
            row.update(digest_file(path, ("sha256",)))
        except FileNotFoundError:
            _issue(issues, "OPEN", name, "required G0 asset missing")
        except OSError as exc:
            _issue(issues, "FAIL", name, f"asset unreadable: {exc}")
        else:
            if name in map_specs and (row["sha256"] != map_specs[name][0].lower() or row["size_bytes"] != map_specs[name][1]):
                _issue(issues, "FAIL", name, "present xMAP differs from published SHA256/size pin")
            else:
                row["state"] = "PINNED"
        assets[name] = row
    for name, expected in source_commits.items():
        row = {"role": "source_checkout", "gate": "G0", "state": "OPEN"}
        try:
            if not locations.sources[name].is_dir():
                raise FileNotFoundError(locations.sources[name])
            head, clean = source_reader(locations.sources[name])
            row["commit"] = head.lower()
            row["tracked_clean"] = clean
        except FileNotFoundError:
            _issue(issues, "OPEN", name, "required source checkout missing")
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            _issue(issues, "FAIL", name, f"source HEAD unavailable: {exc}")
        else:
            if row["commit"] != expected.lower() or not row["tracked_clean"]:
                _issue(issues, "FAIL", name, "present source HEAD differs from published commit or tracked checkout is dirty")
            else:
                row["state"] = "PINNED"
        sources[name] = row
    sources["pokeheartgold_xmap"] = {"role": "published_xmap_provenance", "gate": "G0", "state": "PINNED", "commit": map_specs["heartgold_xmap"][2], "verification": "map SHA256 and size only; no matching checkout"}
    sources["pokeplatinum_xmap"] = {"role": "published_xmap_provenance", "gate": "G0", "state": "PINNED", "commit": map_specs["platinum_xmap"][2], "verification": "map SHA256 and size only; no matching checkout"}
    return {"schema_version": 1, "artifacts": artifacts, "assets": assets, "sources": sources, "pending": PENDING, "issues": issues}


def check(observed: dict, lock: dict) -> dict:
    """Compare every required G0 identity exactly; do not let lock omissions pass."""
    issues = list(observed["issues"])
    if not isinstance(lock, dict):
        _issue(issues, "FAIL", "lock", "lock root must be a JSON object")
        return {"status": "FAIL", "issues": issues}
    if type(lock.get("schema_version")) is not int or lock["schema_version"] != 1:
        _issue(issues, "FAIL", "lock", "schema_version must be 1")
    for group in ("artifacts", "assets", "sources"):
        actual = observed[group]
        saved = lock.get(group)
        if not isinstance(saved, dict) or set(saved) != set(actual):
            _issue(issues, "FAIL", group, "lock IDs differ from required inventory")
            continue
        for name, row in actual.items():
            if not isinstance(saved[name], dict):
                _issue(issues, "FAIL", name, "lock row is not an object")
                continue
            if saved[name].get("state") != "PINNED":
                _issue(issues, "FAIL", name, "required G0 lock row is not PINNED")
            if row.get("state") == "OPEN":
                # The observed OPEN reason is already in issues; missing inputs
                # must remain OPEN (nonzero), not turn into a mismatch FAIL.
                continue
            if row != saved[name]:
                _issue(issues, "FAIL", name, "observed G0 identity differs from lock")
    if lock.get("pending") != observed["pending"]:
        _issue(issues, "FAIL", "pending", "later-gate ledger differs from lock")
    return {"status": "FAIL" if any(i["kind"] == "FAIL" for i in issues) else "OPEN" if issues else "PASS", "issues": issues}


def _override(values: list[str], base: dict[str, Path]) -> dict[str, Path]:
    out = dict(base)
    for value in values:
        key, sep, raw = value.partition("=")
        if not sep or key not in out or not raw:
            raise ValueError(f"override must be known ID=PATH: {value}")
        out[key] = Path(raw)
    return out


def _unique_pairs(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
    parser.add_argument("--candidate", type=Path, help="write a NEW candidate lock only after all G0 reference checks pass")
    parser.add_argument("--rom", action="append", default=[], metavar="ID=PATH")
    parser.add_argument("--asset", action="append", default=[], metavar="ID=PATH")
    parser.add_argument("--source", action="append", default=[], metavar="ID=PATH")
    parser.add_argument("--json", action="store_true", help="print structured observed inventory and verdict")
    args = parser.parse_args(argv)
    defaults = default_locations()
    try:
        locations = Locations(_override(args.rom, defaults.roms), _override(args.asset, defaults.assets), _override(args.source, defaults.sources))
    except ValueError as exc:
        parser.error(str(exc))
    observed = collect(locations)
    if args.candidate:
        issues = observed["issues"]
        verdict = {"status": "FAIL" if any(i["kind"] == "FAIL" for i in issues) else "OPEN" if issues else "PASS", "issues": issues}
        if verdict["status"] == "PASS":
            candidate = {key: observed[key] for key in ("schema_version", "artifacts", "assets", "sources", "pending")}
            try:
                with args.candidate.open("x", encoding="utf-8", newline="\n") as output:
                    json.dump(candidate, output, indent=2, sort_keys=True)
                    output.write("\n")
            except OSError as exc:
                _issue(verdict["issues"], "FAIL", "candidate", f"could not create candidate: {exc}")
                verdict["status"] = "FAIL"
    else:
        try:
            lock = json.loads(args.lock.read_text(encoding="utf-8"), object_pairs_hook=_unique_pairs)
        except FileNotFoundError:
            _issue(observed["issues"], "OPEN", "lock", "committed G0 lock missing")
            verdict = {"status": "FAIL" if any(i["kind"] == "FAIL" for i in observed["issues"]) else "OPEN", "issues": observed["issues"]}
        except (OSError, ValueError) as exc:
            verdict = {"status": "FAIL", "issues": [{"kind": "FAIL", "id": "lock", "reason": f"lock unreadable: {exc}"}]}
        else:
            verdict = check(observed, lock)
    report = {"status": verdict["status"], "issues": verdict["issues"], **{key: observed[key] for key in ("schema_version", "artifacts", "assets", "sources", "pending")}}
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(report["status"])
        for issue in report["issues"]:
            print(f"{issue['kind']} {issue['id']}: {issue['reason']}")
    return {"PASS": 0, "FAIL": 1, "OPEN": 2}[verdict["status"]]


if __name__ == "__main__":
    sys.exit(main())
