"""Failure diagnostics survive the Gen 1 wrapper and release-lane display tails."""
from __future__ import annotations

import importlib
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
WRAPPERS = (
    ("test_duo_gen1_new", "test_gen1_new_duo", ("link_new",)),
    ("test_duo_gen1_pure", "test_gen1_pure_duo", ("gen1_pure", "link_new")),
    ("test_duo_gen1_pure", "test_gen1_pure_duo", ("gen1_pure_green", "link_new")),
)


def wrapper_ready(monkeypatch, tmp_path, module_name):
    monkeypatch.syspath_prepend(str(REPO / "tools"))
    monkeypatch.syspath_prepend(str(REPO / "tests" / "e2e"))
    module = importlib.import_module(module_name)
    monkeypatch.setattr(module, "REPO", str(tmp_path))
    emulator = tmp_path / "EmuHawk.exe"
    emulator.touch()
    monkeypatch.setattr(module.play, "EMUHAWK", str(emulator))
    monkeypatch.setattr(module, "companion_problems", lambda game: [])
    if module_name == "test_duo_gen1_new":
        monkeypatch.setattr(module, "missing_fixtures", lambda scenario: [])
    else:
        monkeypatch.setattr(module, "required_fixtures", lambda game, scenario: [])
    return module


def saved_output(tmp_path, visible, expected):
    files = list((tmp_path / "patch" / "build").glob("*.log"))
    assert files, "failure must save full child output next to build receipts"
    matching = [path for path in files if path.read_bytes() == expected]
    assert matching, "saved output must contain complete stdout followed by complete stderr"
    assert any(str(path.resolve()) in visible for path in matching), (
        "truncated failure must name the absolute full-output path"
    )
    return files


@pytest.mark.parametrize("module_name,function,args", WRAPPERS)
def test_wrapper_failure_saves_full_streams_without_overwriting(
    monkeypatch, tmp_path, module_name, function, args,
):
    module = wrapper_ready(monkeypatch, tmp_path, module_name)
    stdout = "Traceback: original early child failure\n" + "stdout noise\n" * 800
    stderr = "Original stderr diagnosis\n" + "stderr noise\n" * 800
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(
        a[0], 1, stdout, stderr,
    ))
    for _ in range(2):
        with pytest.raises(AssertionError) as failure:
            getattr(module, function)(*args)
        visible = str(failure.value)
        assert "original early child failure" not in visible
        files = saved_output(tmp_path, visible, (stdout + stderr).encode("utf-8"))
    assert len(files) == 2, "repeated failures must not overwrite earlier diagnostics"


@pytest.mark.parametrize("module_name,function,args", WRAPPERS)
@pytest.mark.parametrize("as_bytes", [False, True])
def test_wrapper_timeout_saves_both_partial_streams(
    monkeypatch, tmp_path, module_name, function, args, as_bytes,
):
    module = wrapper_ready(monkeypatch, tmp_path, module_name)
    stdout = "Traceback: early timeout context\n" + "partial stdout\n" * 800
    stderr = "Timeout stderr context\n" + "partial stderr\n" * 800
    if as_bytes:
        stdout, stderr = stdout.encode("utf-8") + b"\xff", stderr.encode("utf-8") + b"\xfe"

    def timeout(*a, **kw):
        raise subprocess.TimeoutExpired(a[0], kw["timeout"], output=stdout, stderr=stderr)

    monkeypatch.setattr(subprocess, "run", timeout)
    with pytest.raises(pytest.fail.Exception) as failure:
        getattr(module, function)(*args)
    visible = str(failure.value)
    assert "early timeout context" not in visible
    expected = stdout + stderr
    if not as_bytes:
        expected = expected.encode("utf-8")
    saved_output(tmp_path, visible, expected)


@pytest.mark.parametrize("quiet", [False, True])
@pytest.mark.parametrize("exit_code", [0, 1])
def test_release_lane_saves_full_failure_even_when_quiet(
    monkeypatch, tmp_path, capsys, quiet, exit_code,
):
    monkeypatch.syspath_prepend(str(REPO / "tools"))
    release = importlib.import_module("release_lanes")
    monkeypatch.setattr(release, "_REPO", str(tmp_path))
    stdout = "Traceback: original release failure\n" + "release noise\n" * 800
    # Exit zero still fails the gate when pytest reports failing tests.
    stderr = "stderr original release diagnosis\n1 failed, 1 passed in 1.0s\n"
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(
        a[0], exit_code, stdout, stderr,
    ))
    for _ in range(2):
        ok, detail = release.run_lane(release.Lane("duo", ["python", "-m", "pytest"]), quiet, [])
        assert not ok
        visible = capsys.readouterr().out
        assert "original release failure" not in visible
        if not quiet:
            assert "1 failed, 1 passed" in visible
        files = saved_output(tmp_path, visible + detail, (stdout + stderr).encode("utf-8"))
    assert len(files) == 2
