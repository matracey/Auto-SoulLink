"""Gen 4 pinned-input lookup: SLINK_GEN4_<KEY> first, else the repo's .cache/gen4 layout.

The hg-engine ROM, exports and fork source never default to a personal checkout: a stray clone at
another commit on the host must not be found implicitly (it would turn every clean run red). An
unset variable with no cache copy is the named skip GEN4_HGE_FORK_SOURCE_ABSENT; only an explicitly
configured fork at the wrong commit is a FAIL.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import gen4_pins  # noqa: E402
import gen_gen4_names as names  # noqa: E402

HGE_KEYS = ("heartgold_hge", "hge_offsets", "hge_rom_gen_ld", "hge_nm_all", "hg_engine_fork")


def _all(loc: gen4_pins.Locations) -> dict[str, Path]:
    return {**loc.roms, **loc.assets, **loc.sources}


@pytest.fixture
def no_env(monkeypatch):
    for key in _all(gen4_pins.default_locations()):
        monkeypatch.delenv(gen4_pins.env_name(key), raising=False)


def test_hge_inputs_default_to_the_repo_cache_layout(no_env):
    paths = _all(gen4_pins.default_locations())
    cache = gen4_pins.REPO / ".cache" / "gen4" / "hge"
    for key in HGE_KEYS:
        assert paths[key].is_relative_to(cache), (key, paths[key])
    assert paths["heartgold_hge"] == cache / "build-fc5175764983" / "test.nds"
    # the stray 54c1e0a clone and the old fork checkout are never defaults
    for path in paths.values():
        text = path.as_posix().lower()
        assert "hgengine_romhack" not in text and ".tooling/hg-engine" not in text, path


def test_every_key_takes_its_env_override(no_env, monkeypatch, tmp_path):
    keys = list(_all(gen4_pins.default_locations()))
    for key in keys:
        monkeypatch.setenv(gen4_pins.env_name(key), str(tmp_path / key))
    paths = _all(gen4_pins.default_locations())
    assert {k: paths[k] for k in keys} == {k: tmp_path / k for k in keys}


def test_unset_env_and_no_cache_copy_is_the_named_fork_skip(no_env, monkeypatch, tmp_path):
    monkeypatch.setattr(names.gen4_pins, "REPO", tmp_path)
    with pytest.raises(names.Absent) as exc:
        names.locate_src("hge", None)
    assert str(exc.value).startswith("GEN4_HGE_FORK_SOURCE_ABSENT: hg_engine_fork fc517576")
    assert str(tmp_path / ".cache" / "gen4" / "hge" / "src") in str(exc.value)


def test_a_configured_fork_at_the_wrong_commit_fails_not_skips(no_env, monkeypatch, tmp_path):
    clone = tmp_path / "wrong-fork"
    clone.mkdir()
    (clone / "charmap.txt").write_text("0001=A\n", encoding="utf-8")
    git = ["git", "-C", str(clone), "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "add", "charmap.txt"], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "x"], check=True)
    monkeypatch.setenv(gen4_pins.env_name("hg_engine_fork"), str(clone))
    with pytest.raises(names.Mismatch, match="pinned fc517576"):
        names.locate_src("hge", None)
