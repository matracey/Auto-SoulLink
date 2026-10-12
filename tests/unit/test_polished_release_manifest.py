"""Polished Crystal ships in the player ZIP, additively: no existing path is added to, dropped or altered.

BASELINE is the commit before the Polished rows landed; the pre-change tools/make_release.py is read
from it and builds a ZIP from the SAME tree, so the comparison is exactly "what the Polished edit changed".
"""
from __future__ import annotations

import hashlib
import importlib.util
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "tools"))

import make_release  # noqa: E402

BASELINE = "69ca9ed8"
POLISHED = {
    "data/games/polished_crystal/profile.json",
    "data/games/polished_crystal/charmap.lua",
    "data/games/polished_crystal/evolutions.json",
    "data/games/polished_crystal/area_map.json",
    "data/games/polished_crystal/items.json",
    "data/games/polished_crystal/species_index.json",  # compose_polished: party_mon stat rebuild tables
    "data/games/polished_crystal/moves.json",  # compose_polished: party_mon base PP table
    "data/games/polished_crystal/engine_signals.json",
    "data/games/polished_crystal/overlay/beacon.json",
    "data/polished/overlay_provenance.json",
    "lua/gen2/polished.lua",
    "lua/gen2/polished_boxes.lua",
    "lua/gen2/polished_overworld.lua",
    "lua/gen2/polished_explode.lua",
    "lua/gen2/polished_writes.lua",  # polished_explode.lua dofiles it
    "lua/gen2/polished_sounds.lua",
    "lua/gen2/polished_stats.lua",
    "lua/gen2/polished_rival.lua",  # compose_polished loads the rival-swap writer by path
    "lua/gen2/polished_trade.lua",  # dev-only trade binder (deps.polished_trade_dev)
}


# The Gen 2 randomizer lane (merged into the same integration) adds each title's overlay beacon beside its binding
# (lua/gen2/entry.lua Entry.BEACON_FILES, R6). Gen 3 recovery also adds its durable journal.
GEN2_BEACONS = {f"data/games/gen2_{t}/overlay/beacon.json" for t in ("crystal", "gold", "silver")}
GEN3_RECOVERY = {"lua/gen3/report_outbox.lua"}


def _build(module, out: Path) -> dict[str, str]:
    path = module.build_release(version="t", out_dir=out, skip_generators=True, quiet=True)
    with zipfile.ZipFile(path) as zf:
        return {n.split("/", 1)[1]: hashlib.sha256(zf.read(n)).hexdigest()
                for n in zf.namelist() if n.split("/", 1)[1]}


def _baseline_module(tmp_path):
    proc = subprocess.run(["git", "show", f"{BASELINE}:tools/make_release.py"], cwd=_REPO, capture_output=True)
    if proc.returncode:
        pytest.skip(f"baseline commit {BASELINE} not in this clone")
    src = tmp_path / "make_release_baseline.py"
    src.write_bytes(proc.stdout)
    spec = importlib.util.spec_from_file_location("make_release_baseline", src)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.REPO_ROOT = _REPO
    return mod


def test_polished_files_ship_and_nothing_else_changes(tmp_path):
    before = _build(_baseline_module(tmp_path), tmp_path / "before")
    after = _build(make_release, tmp_path / "after")
    assert not (POLISHED & set(before)), "baseline already shipped Polished: the baseline is stale"
    additions = POLISHED | GEN2_BEACONS | GEN3_RECOVERY
    assert additions <= set(after)
    assert {k: v for k, v in after.items() if k not in additions} == before


def test_the_polished_manifest_rows_are_not_gated_by_a_gen2_overlay_catalog():
    # Dev-grade admission (Entry.admit_polished) reads these unconditionally; overlay_state gates Crystal/Gold/Silver only.
    names = make_release.data_game_files()["polished_crystal"]
    prefix = "data/games/polished_crystal/"
    assert set(names) == {p.removeprefix(prefix) for p in POLISHED if p.startswith(prefix)}


def test_red_control_without_the_polished_rows_the_files_are_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(make_release, "_DATA_GAME_LUA",
                        {k: v for k, v in make_release._DATA_GAME_LUA.items() if k != "polished_crystal"})
    monkeypatch.setattr(make_release, "_DATA_EXTRA", [])
    monkeypatch.setattr(make_release, "_LUA_GEN2", [f for f in make_release._LUA_GEN2 if not f.startswith("polished")])
    # master builds from the ONE tree table, which holds its own reference to the gen2 list
    monkeypatch.setitem(make_release._MANIFEST_TREES, "gen2", [f for f in make_release._LUA_GEN2 if not f.startswith("polished")])
    assert POLISHED.isdisjoint(_build(make_release, tmp_path))
