"""Public builder tests with fake external git/compiler processes; no ROM assets needed."""

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
NAMES = ("pokecrystal", "pokecrystal11", "pokegold", "pokesilver")
MAP = (
    b'ROM0 bank #0:\n'
    b'    SECTION: $0000-$0003 ($0004 bytes) ["header"]\n'
    b'    EMPTY: $0004-$3fff ($3ffc bytes)\n'
    b'    TOTAL EMPTY: $3ffc bytes\n'
)


@pytest.fixture
def build_case(tmp_path, monkeypatch):
    lock = json.loads((ROOT / "data/gen2_sources.lock.json").read_text())
    repos = {name: tmp_path / name for name in lock["sources"]}
    bins = tmp_path / "bin"
    bins.mkdir()
    for name in ("rgbasm", "rgblink", "rgbfix", "rgbgfx", "make", "gcc", "sh"):
        binary = bins / (name + (".exe" if os.name == "nt" else ""))
        binary.write_bytes(b"fixture executable " + name.encode())
        binary.chmod(0o755)
    artifacts = {}
    for name in NAMES:
        rom = (name + " ROM fixture").encode()
        artifacts[name] = {"gbc": rom, "sym": b"00:0000 Header\n", "map": MAP}
        lock["outputs"][name].update(
            sha1=hashlib.sha1(rom).hexdigest(), state="BUILT",
            sym_sha256=hashlib.sha256(artifacts[name]["sym"]).hexdigest(),
            map_sha256=hashlib.sha256(MAP).hexdigest(),
        )
    for repo in repos.values():
        repo.mkdir()
        (repo / "Makefile").write_text("# External make is faked at subprocess boundary.\n")
    lock_path = tmp_path / "lock.json"
    lock_path.write_text(json.dumps(lock) + "\n")
    out = tmp_path / "published"
    case = SimpleNamespace(lock=lock, lock_path=lock_path, repos=repos, bins=bins, out=out,
                           artifacts=artifacts, dirty={}, heads={}, versions={}, fail_make=False,
                           missing=set(), commands=[], after_build=None,
                           enforce_recursive_shell=False)

    def external_run(command, **kwargs):
        args = [str(part) for part in command]
        case.commands.append(args)
        exe = Path(args[0]).stem
        stdout = ""
        status = 0
        if exe == "git":
            repo = Path(args[2])
            name = repo.name
            if "--show-toplevel" in args:
                stdout = str(repo)
            elif "rev-parse" in args:
                stdout = case.heads.get(name, lock["sources"][name]["commit"])
            elif "status" in args:
                stdout = case.dirty.get(name, "")
            else:
                raise AssertionError(f"unexpected source mutation: {args}")
        elif "--version" in args or "--help" in args:
            stdout = case.versions.get(exe, {
                "make": "GNU Make 4.4.1", "gcc": "gcc (GCC) 15.1.0",
                "sh": "GNU bash, version 5.2.0",
            }.get(exe, f"{exe} v1.0.3"))
        elif exe == "sh":
            stdout = "gen2-shell-ready"
        elif exe == "make":
            if case.enforce_recursive_shell:
                build_env = kwargs["env"]
                nested_shell = build_env.get("MAKESHELL") or build_env.get("SHELL", "")
                if Path(nested_shell).stem != "sh":
                    return subprocess.CompletedProcess(
                        command, 2, "make clean -C tools/\nrm -f scan_includes\n",
                        "Remove-Item: Parameter cannot be processed because parameter name 'f' is ambiguous.\n",
                    )
            if "clean" not in args:
                for name in NAMES:
                    if lock["outputs"][name]["source"] != Path(kwargs["cwd"]).name:
                        continue
                    for ext, data in artifacts[name].items():
                        if f"{name}.{ext}" not in case.missing:
                            if ext == "sym" and kwargs.get("env", {}).get("DEBUG") == "1":
                                data += b"00:0002 Header.extra_debug_symbol\n"
                            (Path(kwargs["cwd"]) / f"{name}.{ext}").write_bytes(data)
                if case.after_build is not None:
                    case.after_build(Path(kwargs["cwd"]).name)
            status = 2 if case.fail_make else 0
        else:
            raise AssertionError(f"unexpected external command: {args}")
        return subprocess.CompletedProcess(command, status, stdout + "\n", "")

    monkeypatch.setattr(subprocess, "run", external_run)
    case.argv = ["--lock", str(lock_path), "--out-dir", str(out),
                 "--crystal-repo", str(repos["pokecrystal"]),
                 "--gold-repo", str(repos["pokegold"]),
                 "--rgbds-bin", str(bins), "--w64devkit-bin", str(bins)]
    return case


def run(case, *extra):
    from tools.build_gen2_syms import main
    return main([*case.argv, *extra])


def test_verified_four_rom_build_publishes_only_symbols_and_observed_provenance(build_case):
    case = build_case
    assert run(case) == 0
    assert {path.name for path in case.out.iterdir()} == {
        *(f"{name}.{ext}" for name in NAMES for ext in ("sym", "map")),
        "build_provenance.json", "linker_slack.json",
    }
    provenance = json.loads((case.out / "build_provenance.json").read_text())
    assert provenance["schema"] == "gen2-build-provenance-v1"
    assert provenance["evidence_level"] == "SOURCE"
    assert provenance["lock_sha256"] == hashlib.sha256(case.lock_path.read_bytes()).hexdigest()
    assert all(source["clean"] for source in provenance["sources"].values())
    assert set(provenance["roms"]) == set(NAMES)
    assert provenance["toolchain"]["rgbds"]["binaries"]["rgbgfx"]["version"] == "rgbgfx v1.0.3"
    assert json.loads((case.out / "linker_slack.json").read_text())["allocation_status"] == "CANDIDATE"


def test_shell_probe_uses_portable_execution_instead_of_gnu_help(build_case):
    assert run(build_case) == 0
    commands = [cmd[1:] for cmd in build_case.commands if Path(cmd[0]).stem == "sh"]
    assert commands == [["-c", "printf gen2-shell-ready"]]


@pytest.mark.parametrize("drift", ["", "address", "bank", "missing", "unpinned"])
def test_native_artifact_formatting_requires_an_equivalent_hash_pinned_reference(build_case, drift):
    case = build_case
    pinned = MAP.replace(b"\n", b"\r\n")
    aliases = b"\t         $0000 = Header\r\n\t         $0000 = Header.alias\r\n"
    pinned = pinned.replace(b"    EMPTY:", aliases + b"    EMPTY:")
    native = pinned.replace(b"\r\n", b"\n").replace(
        aliases.replace(b"\r\n", b"\n"), b"\t         $0000 = Header.alias\n\t         $0000 = Header\n")
    if drift == "address":
        native = native.replace(b"$0000 = Header.alias", b"$0001 = Header.alias")
    elif drift == "bank":
        native = native.replace(b"ROM0 bank #0:", b"ROM0 bank #1:")
    elif drift == "missing":
        native = native.replace(b"\t         $0000 = Header.alias\n", b"")
    case.out.mkdir()
    for name in NAMES:
        case.artifacts[name]["map"] = native
        case.lock["outputs"][name]["map_sha256"] = hashlib.sha256(pinned).hexdigest()
        (case.out / f"{name}.map").write_bytes(pinned + (b"tampered" if drift == "unpinned" else b""))
        (case.out / f"{name}.sym").write_bytes(case.artifacts[name]["sym"])
    case.lock_path.write_text(json.dumps(case.lock) + "\n")
    before = {path.name: path.read_bytes() for path in case.out.iterdir()}
    assert run(case, "--check") == (1 if drift else 0)
    assert {path.name: path.read_bytes() for path in case.out.iterdir()} == before


def test_source_changed_while_other_title_builds_refuses_publication(build_case):
    case = build_case

    def drift_during_gold(source):
        if source == "pokegold":
            case.heads["pokecrystal"] = "0" * 40

    case.after_build = drift_during_gold
    before = case.lock_path.read_bytes()
    assert run(case) == 1
    assert not case.out.exists()
    assert case.lock_path.read_bytes() == before


def test_tool_changed_during_build_refuses_false_provenance(build_case):
    case = build_case

    def replace_tool(source):
        if source == "pokegold":
            for suffix in ("", ".exe"):
                (case.bins / f"rgbfix{suffix}").write_bytes(b"different executable")

    case.after_build = replace_tool
    assert run(case) == 1
    assert not case.out.exists()


def test_bootstrap_requires_explicit_null_artifact_pins(build_case):
    case = build_case
    for output in case.lock["outputs"].values():
        output["state"] = "UNBUILT"
        del output["sym_sha256"]
        del output["map_sha256"]
    case.lock_path.write_text(json.dumps(case.lock))
    before = case.lock_path.read_bytes()
    assert run(case, "--record-artifact-hashes") == 1
    assert case.lock_path.read_bytes() == before
    assert not case.out.exists()


def mark_unbuilt(case):
    for output in case.lock["outputs"].values():
        output.update(state="UNBUILT", sym_sha256=None, map_sha256=None)
    case.lock_path.write_text(json.dumps(case.lock) + "\n")


@pytest.mark.parametrize("mode", [(), ("--check",)])
def test_unbuilt_lock_requires_explicit_bootstrap_without_build_side_effects(build_case, mode):
    case = build_case
    mark_unbuilt(case)
    before = case.lock_path.read_bytes()
    assert run(case, *mode) == 1
    assert case.lock_path.read_bytes() == before
    assert not case.out.exists()
    assert not case.commands


def test_explicit_bootstrap_records_all_artifacts_and_resulting_lock_hash(build_case):
    case = build_case
    mark_unbuilt(case)
    assert run(case, "--record-artifact-hashes") == 0
    recorded = json.loads(case.lock_path.read_text())
    for name, output in recorded["outputs"].items():
        assert output["state"] == "BUILT"
        for ext in ("sym", "map"):
            assert output[f"{ext}_sha256"] == hashlib.sha256(case.artifacts[name][ext]).hexdigest()
    provenance = json.loads((case.out / "build_provenance.json").read_text())
    assert provenance["lock_sha256"] == hashlib.sha256(case.lock_path.read_bytes()).hexdigest()
    before = {path.name: path.read_bytes() for path in case.out.iterdir()}
    assert run(case, "--check") == 0
    assert {path.name: path.read_bytes() for path in case.out.iterdir()} == before


def test_record_mode_cannot_replace_a_preexisting_different_hash(build_case):
    case = build_case
    assert run(case) == 0
    before = {path.name: path.read_bytes() for path in case.out.iterdir()}
    case.lock["outputs"]["pokesilver"]["map_sha256"] = "0" * 64
    case.lock_path.write_text(json.dumps(case.lock))
    lock_before = case.lock_path.read_bytes()
    assert run(case, "--record-artifact-hashes") == 1
    assert case.lock_path.read_bytes() == lock_before
    assert {path.name: path.read_bytes() for path in case.out.iterdir()} == before


def test_invalid_last_map_does_not_record_partial_bootstrap(build_case):
    case = build_case
    mark_unbuilt(case)
    case.artifacts["pokesilver"]["map"] = b"invalid linker map\n"
    before = case.lock_path.read_bytes()
    assert run(case, "--record-artifact-hashes") == 1
    assert case.lock_path.read_bytes() == before
    assert not case.out.exists()


def test_make_failure_does_not_replace_published_artifacts(build_case):
    case = build_case
    assert run(case) == 0
    before = {path.name: path.read_bytes() for path in case.out.iterdir()}
    case.fail_make = True
    assert run(case) == 1
    assert {path.name: path.read_bytes() for path in case.out.iterdir()} == before


def test_caller_debug_build_flag_does_not_change_locked_artifacts(build_case, monkeypatch):
    monkeypatch.setenv("DEBUG", "1")
    assert run(build_case) == 0


@pytest.mark.parametrize("host_variables", [("SHELL",), ("MAKESHELL",), ("SHELL", "MAKESHELL")])
def test_recursive_make_uses_verified_shell_despite_host_powershell(
    build_case, monkeypatch, host_variables,
):
    for variable in ("SHELL", "MAKESHELL"):
        monkeypatch.delenv(variable, raising=False)
    for variable in host_variables:
        monkeypatch.setenv(variable, r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe")
    build_case.enforce_recursive_shell = True
    assert run(build_case) == 0
    assert (build_case.out / "build_provenance.json").is_file()


def separate_rgbds_directory(case):
    rgbds = case.bins.parent / "rgbds override"
    rgbds.mkdir()
    for name in ("rgbasm", "rgblink", "rgbfix", "rgbgfx"):
        filename = name + (".exe" if os.name == "nt" else "")
        shutil.copy2(case.bins / filename, rgbds / filename)
    case.argv[case.argv.index("--rgbds-bin") + 1] = str(rgbds)
    return rgbds


@pytest.mark.parametrize("name", ["make", "gcc", "sh"])
def test_earlier_tool_directory_cannot_shadow_recorded_build_tool(build_case, name):
    case = build_case
    rgbds = separate_rgbds_directory(case)
    assert run(case) == 0
    before = {path.name: path.read_bytes() for path in case.out.iterdir()}
    # Case differences do not make Windows executable names distinct.
    filename = f"{name.upper()}.EXE" if os.name == "nt" else name
    shadow = rgbds / filename
    shadow.write_bytes(b"unrecorded executable")
    shadow.chmod(0o755)
    case.commands.clear()
    assert run(case) == 1
    assert {path.name: path.read_bytes() for path in case.out.iterdir()} == before
    assert not any(Path(args[0]).stem == "make" and "--version" not in args
                   for args in case.commands)


def test_an_earlier_hardlink_to_the_verified_tool_is_the_same_binary(build_case):
    case = build_case
    rgbds = separate_rgbds_directory(case)
    filename = "gcc.exe" if os.name == "nt" else "gcc"
    (rgbds / filename).hardlink_to(case.bins / filename)
    assert run(case) == 0
