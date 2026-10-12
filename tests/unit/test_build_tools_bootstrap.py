"""Build-tool validation and path recovery without downloads or real executables."""

import io
import os
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import _build_tools_bootstrap as bootstrap  # noqa: E402


@pytest.mark.parametrize("kind", ["rgbds", "devkit"], ids=["rgbds", "w64devkit"])
def test_cached_make_toolchains_with_spaces_are_copied_intact_once(tmp_path, monkeypatch, kind):
    repo = tmp_path / "Google Drive" / "SLink"
    work = tmp_path / "work"
    monkeypatch.setattr(bootstrap, "REPO_ROOT", repo)
    monkeypatch.setattr(bootstrap.platform, "system", lambda: "Windows")
    monkeypatch.delenv("SLINK_RGBDS_BIN", raising=False)
    monkeypatch.delenv("SLINK_W64DEVKIT_BIN", raising=False)
    monkeypatch.setenv("SLINK_WORK_ROOT", str(work))
    monkeypatch.setattr(bootstrap, "_verify_rgbds_version", lambda *args: True)
    monkeypatch.setattr(bootstrap, "_verify_devkit", lambda *args: True)
    monkeypatch.setattr(bootstrap, "_download_and_verify", lambda *args: pytest.fail("cached tools must not download"))
    if kind == "rgbds":
        install = repo / ".cache/build-tools/rgbds-v1.0.3"
        names = ("rgbasm", "rgblink", "rgbfix", "rgbgfx")
        def ensure():
            return bootstrap.ensure_rgbds("v1.0.3")
    else:
        install = repo / ".cache/build-tools/w64devkit-2.10.0/w64devkit"
        names = ("make", "gcc", "sh", "busybox")
        ensure = bootstrap.ensure_w64devkit
    binary = install / "bin"
    binary.mkdir(parents=True)
    for name in names:
        (binary / f"{name}.exe").write_bytes(name.encode())
    (install / "lib/gcc").mkdir(parents=True)
    (install / "lib/gcc/support.dll").write_bytes(b"support DLL")
    before = {p.relative_to(install): p.read_bytes() for p in install.rglob("*") if p.is_file()}
    result = ensure().resolve()
    assert " " not in str(result), "make's sub-processes must receive a resolved space-free path"
    assert result.is_relative_to(work / "cache/build-tools")
    assert {p.relative_to(result.parent): p.read_bytes()
            for p in result.parent.rglob("*") if p.is_file()} == before
    assert {p.relative_to(install): p.read_bytes() for p in install.rglob("*") if p.is_file()} == before
    assert ensure().resolve() == result


@pytest.mark.parametrize("builder", ["polished-clean", "polished-overlay", "pure-clean", "pure-overlay"],
                         ids=["polished-clean", "polished-overlay", "pure-clean", "pure-overlay"])
def test_make_builders_normalize_explicit_spaced_toolchains_before_subprocess(tmp_path, monkeypatch, builder):
    import build_polished_companion as pc
    import build_polished_syms as polished
    import build_purergb_overlay as overlay
    import build_purergb_syms as pure

    monkeypatch.setattr(bootstrap.platform, "system", lambda: "Windows")
    monkeypatch.setenv("SLINK_WORK_ROOT", str(tmp_path / "work"))
    rgbds = tmp_path / "has space/rgbds/bin"
    devkit = tmp_path / "has space/devkit/bin"
    for path, names in ((rgbds, ("rgbasm", "rgblink", "rgbfix", "rgbgfx")), (devkit, ("make", "gcc", "sh"))):
        path.mkdir(parents=True)
        for name in names:
            (path / f"{name}.exe").write_bytes(name.encode())

    def fake_make(argv, **kwargs):
        assert " " not in argv[0], "the resolved make executable must be space-free"
        assert all(" " not in part for part in kwargs["env"]["PATH"].split(os.pathsep)[:2])
        raise RuntimeError("recorded make; no executable ran")

    monkeypatch.setattr(pure.subprocess, "run", fake_make)
    monkeypatch.setattr(pure, "verify_source", lambda *args: tmp_path / "source")
    monkeypatch.setattr(polished, "verify_source", lambda *args: tmp_path / "source")
    monkeypatch.setattr(polished, "export_source", lambda *args: None)
    with pytest.raises(RuntimeError, match="recorded make"):
        if builder == "polished-clean":
            polished.build_rom_syms(repo_dir=tmp_path / "source", build_dir=tmp_path / "build",
                                    rgbds_bin=rgbds, w64devkit_bin=devkit, check=True)
        elif builder == "polished-overlay":
            def fake_clean(**kwargs):
                assert " " not in str(kwargs["rgbds_bin"])
                assert " " not in str(kwargs["w64devkit_bin"])
                raise RuntimeError("recorded make; no executable ran")
            monkeypatch.setattr(pc, "build_rom_syms", fake_clean)
            pc.build(repo_dir=tmp_path / "source", cache_dir=tmp_path / "build",
                     rgbds_bin=rgbds, w64devkit_bin=devkit, check=True)
        elif builder == "pure-clean":
            pure.build_rom_syms(repo_dir=tmp_path / "source", rgbds_bin=rgbds, w64devkit_bin=devkit, check=True)
        else:
            overlay.make(tmp_path / "source", rgbds, devkit, pure.load_lock())


def test_package_import_normalizes_spaces_without_a_top_level_tools_module(tmp_path, monkeypatch):
    from tools import _build_tools_bootstrap as packaged

    monkeypatch.setattr(sys, "path", [p for p in sys.path if Path(p).resolve() != (ROOT / "tools").resolve()])
    monkeypatch.delitem(sys.modules, "slink_space", raising=False)
    monkeypatch.setattr(packaged.platform, "system", lambda: "Windows")
    monkeypatch.setenv("SLINK_WORK_ROOT", str(tmp_path / "work"))
    binary = tmp_path / "spaced install/bin"
    binary.mkdir(parents=True)
    (binary / "rgbasm.exe").write_bytes(b"package fixture")
    result = packaged.space_free_toolchain(binary, "rgbds-v1.0.3")
    assert " " not in str(result) and (result / "rgbasm.exe").read_bytes() == b"package fixture"


def test_overlay_outer_boundary_keeps_the_actual_copied_toolchain_for_provenance(tmp_path, monkeypatch):
    import build_purergb_overlay as builder

    monkeypatch.setenv("SLINK_WORK_ROOT", str(tmp_path / "work"))
    monkeypatch.setattr(bootstrap.platform, "system", lambda: "Windows")
    rgbds = tmp_path / "spaced rgbds/bin"
    devkit = tmp_path / "spaced devkit/bin"
    for path in (rgbds, devkit):
        path.mkdir(parents=True)
        (path / "fixture.exe").write_bytes(b"actual executed bytes")
    tree = tmp_path / "build"
    (tree / builder.overlay.OVERLAY_DST).mkdir(parents=True)
    monkeypatch.setattr(builder, "fresh_copy", lambda *args: tree)
    monkeypatch.setattr(builder.overlay, "apply", lambda *args: None)

    def fake_make(checkout, selected_rgbds, selected_devkit, lock):
        assert " " not in str(selected_rgbds) and " " not in str(selected_devkit)
        raise RuntimeError("captured outer normalized paths; no executable ran")

    monkeypatch.setattr(builder, "make", fake_make)
    with pytest.raises(RuntimeError, match="captured outer normalized"):
        builder.build(repo_dir=tmp_path / "source", rgbds_bin=rgbds, w64devkit_bin=devkit, check=True)
@pytest.mark.parametrize("stdout,stderr,returncode,expected", [
    ("rgbasm v1.0.1\n", "", 0, True),
    ("", "rgbasm v1.0.1\r\n", 0, True),
    ("rgbasm v1.0.1\n", "warning\n", 0, True),
    ("rgbasm v1.0.10\n", "", 0, False),
    ("rgbasm v1.0.1-12-gabcdef\n", "", 0, False),
    ("rgbasm v1.0.1-dirty\n", "", 0, False),
    ("rgbasm v1.0.1+local\n", "", 0, False),
    ("rgbasm v1.0.3\n", "", 0, False),
    ("rgbasm v1.0.1\n", "", 1, False),
    ("", "expected v1.0.1, unavailable\n", 0, False),
    ("rgbasm v1.0.", "1\n", 0, False),
    ("", "", 0, False),
])
def test_rgbds_version_requires_an_exact_successful_banner(monkeypatch, stdout, stderr, returncode, expected):
    def run(args, **kwargs):
        assert args == ["rgbasm", "--version"]
        assert kwargs == {"capture_output": True, "text": True, "timeout": 5}
        return subprocess.CompletedProcess(args, returncode, stdout, stderr)

    monkeypatch.setattr(bootstrap.subprocess, "run", run)
    assert bootstrap._verify_rgbds_version(Path("rgbasm"), "v1.0.1") is expected


@pytest.mark.parametrize("error", [FileNotFoundError(), OSError(), subprocess.TimeoutExpired("rgbasm", 5)])
def test_rgbds_version_execution_errors_are_rejected(monkeypatch, error):
    def run(*args, **kwargs):
        raise error

    monkeypatch.setattr(bootstrap.subprocess, "run", run)
    assert not bootstrap._verify_rgbds_version(Path("rgbasm"), "v1.0.1")


def test_explicit_rgbds_override_still_trusts_the_requested_directory(tmp_path, monkeypatch):
    for name in bootstrap.REQUIRED_BINARIES:
        (tmp_path / bootstrap._binary_name(name)).touch()
    monkeypatch.setenv("SLINK_RGBDS_BIN", str(tmp_path))

    def unexpected_version_check(*args):
        pytest.fail("explicit override must not trigger version checks")

    monkeypatch.setattr(bootstrap, "_verify_rgbds_version", unexpected_version_check)
    assert bootstrap.ensure_rgbds() == tmp_path


def test_linux_rgbds_install_uses_the_verified_pin_and_extracts_only_tools(tmp_path, monkeypatch):
    archive = tmp_path / "rgbds.tar.xz"
    names = (*bootstrap.REQUIRED_BINARIES, "rgbgfx")
    with tarfile.open(archive, "w:xz") as tf:
        for name in (*names, "../outside"):
            data = name.encode()
            member = tarfile.TarInfo(name)
            member.size = len(data)
            tf.addfile(member, io.BytesIO(data))
    monkeypatch.setattr(bootstrap, "REPO_ROOT", tmp_path / "repo")
    monkeypatch.setattr(bootstrap.platform, "system", lambda: "Linux")
    monkeypatch.setattr(bootstrap.platform, "machine", lambda: "x86_64")
    monkeypatch.delenv("SLINK_RGBDS_BIN", raising=False)
    monkeypatch.setattr(bootstrap, "_check_existing_path", lambda name: None)
    monkeypatch.setattr(bootstrap, "_verify_rgbds_version", lambda *args: True)
    calls = []

    def download(*args):
        calls.append(args)
        return archive

    monkeypatch.setattr(bootstrap, "_download_and_verify", download)
    result = bootstrap.ensure_rgbds("v1.0.3")
    pin = bootstrap.RGBDS_PINS["v1.0.3"]["linux_x86_64"]
    assert calls == [(pin["url"], pin["sha256"], pin["size"])]
    assert sorted(p.name for p in result.iterdir()) == sorted(names)
    assert all((result / name).read_bytes() == name.encode() for name in names)
    assert all((result / name).stat().st_mode & 0o111 for name in names)
    assert not (result.parent / "outside").exists()
    assert bootstrap.ensure_rgbds("v1.0.3") == result
    assert len(calls) == 1
