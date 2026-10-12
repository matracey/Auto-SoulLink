"""tools/build_polished_syms.py: the export tree delete must be retrying and fail-closed.

Round-1 root cause (card cx-52f61773): `export_source` called `shutil.rmtree` on a shared cache
tree. A concurrent build left a directory non-empty, rmtree raised WinError 145, and the build
continued onto a HALF-DELETED tree -- which surfaced much later as make failing on files that
demonstrably existed. These tests pin the two properties that fix it: transient failures are
retried, and a tree that will not go away stops the build instead of corrupting it.
"""
from __future__ import annotations

import pathlib
import shutil
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import build_polished_syms as bps  # noqa: E402


def test_a_missing_tree_is_a_no_op(tmp_path):
    bps.remove_tree(tmp_path / "absent")


def test_an_existing_tree_is_removed(tmp_path):
    tree = tmp_path / "tree"
    (tree / "deep" / "deeper").mkdir(parents=True)
    (tree / "deep" / "deeper" / "f.bin").write_bytes(b"x")
    bps.remove_tree(tree)
    assert not tree.exists()


def test_a_transient_failure_is_retried_and_then_succeeds(tmp_path, monkeypatch):
    """One WinError-145-shaped failure must not end the delete; the retry rides it out."""
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "f.bin").write_bytes(b"x")
    real = shutil.rmtree
    calls = {"n": 0}

    def flaky(path, *a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError(145, "The directory is not empty")
        return real(path, *a, **kw)

    monkeypatch.setattr(bps.shutil, "rmtree", flaky)
    bps.remove_tree(tree, attempts=4, delay=0)
    assert calls["n"] == 2
    assert not tree.exists()


def test_a_persistent_failure_fails_closed(tmp_path, monkeypatch):
    """A tree that never goes away must RAISE -- never return and let the build continue."""
    tree = tmp_path / "tree"
    tree.mkdir()
    monkeypatch.setattr(bps.shutil, "rmtree", lambda *a, **k: (_ for _ in ()).throw(OSError(145, "busy")))
    with pytest.raises(RuntimeError, match="Another build is probably using the same cache tree"):
        bps.remove_tree(tree, attempts=3, delay=0)
    assert tree.exists(), "the failing tree must be left alone, not half-deleted"


def test_a_race_that_recreates_the_tree_still_fails_closed(tmp_path, monkeypatch):
    """A concurrent build that keeps recreating the tree is the real case; retrying must not pass."""
    tree = tmp_path / "tree"
    tree.mkdir()
    real = shutil.rmtree

    def recreating(path, *a, **kw):
        real(path)
        path.mkdir(exist_ok=True)          # the other build writes again immediately

    monkeypatch.setattr(bps.shutil, "rmtree", recreating)
    with pytest.raises(RuntimeError, match="Another build is probably using the same cache tree"):
        bps.remove_tree(tree, attempts=3, delay=0)


def test_export_source_refuses_a_space_in_the_path(tmp_path):
    with pytest.raises(RuntimeError, match="contains a space"):
        bps.export_source(tmp_path, "deadbeef", tmp_path / "has space")


def test_export_source_stamps_the_commit_into_a_fresh_tree(tmp_path, monkeypatch):
    """The marker distinguishes a fresh export from a stale tree that survived a delete."""
    repo = tmp_path / "repo"
    repo.mkdir()
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "leftover.bin").write_bytes(b"stale")
    (tree / bps.EXPORT_MARKER).write_text("previous-commit\n", encoding="utf-8")
    monkeypatch.setattr(bps.subprocess, "run", lambda *a, **k: subprocess_result(b""))
    bps.export_source(repo, "cafe1234", tree)
    assert (tree / bps.EXPORT_MARKER).read_text(encoding="utf-8").strip() == "cafe1234"
    assert not (tree / "leftover.bin").exists()


@pytest.mark.parametrize("location", ["same", "inside", "ancestor"])
def test_export_source_refuses_any_tree_overlapping_the_source(tmp_path, monkeypatch, location):
    repo = tmp_path / "parent" / "repo"
    repo.mkdir(parents=True)
    source = repo / "source.asm"
    source.write_bytes(b"source must survive")
    tree = {"same": repo, "inside": repo / "build", "ancestor": repo.parent}[location]
    tree.mkdir(exist_ok=True)
    (tree / bps.EXPORT_MARKER).write_text("previous-commit\n", encoding="utf-8")
    monkeypatch.setattr(bps.subprocess, "run", lambda *a, **k: subprocess_result(b""))

    with pytest.raises(RuntimeError, match="overlaps the source"):
        bps.export_source(repo, "cafe1234", tree)

    assert source.read_bytes() == b"source must survive"
    assert (tree / bps.EXPORT_MARKER).read_text(encoding="utf-8") == "previous-commit\n"


def test_export_source_preserves_an_existing_unowned_directory(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    tree = tmp_path / "unowned"
    tree.mkdir()
    unrelated = tree / "user-data.bin"
    unrelated.write_bytes(b"do not delete")
    monkeypatch.setattr(bps.subprocess, "run", lambda *a, **k: subprocess_result(b""))

    with pytest.raises(RuntimeError, match="not an owned SLink export"):
        bps.export_source(repo, "cafe1234", tree)

    assert unrelated.read_bytes() == b"do not delete"
    assert not (tree / bps.EXPORT_MARKER).exists()


def subprocess_result(stdout: bytes):
    import subprocess

    return subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr=b"")
