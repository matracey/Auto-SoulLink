"""Retry decisions must use fresh, complete gate evidence, not pytest's truncated diagnostic.

Fixtures are minimal verbatim excerpts of freeze #3's retained W6 Crystal failure:
vanilla-sweep-plain-001/producer/logs/gate__w6__crystal.log:54,56,58,59,86,88 and
its .cmd1.gen2_frame_align_result.txt:26,72-74. No emulator is launched here.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import gen2_final_sweep as sweep  # noqa: E402

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures/gen2/sweep_retry"


def test_real_w6_full_result_earns_retry_despite_truncated_pytest_log(tmp_path, monkeypatch):
    lane = tmp_path / "lane"
    build = lane / "patch/build"
    build.mkdir(parents=True)
    calls = []
    monkeypatch.setattr(sweep, "git", lambda *_args: "")
    inner = (FIXTURES / "w6_inner_result.txt").read_text(encoding="utf-8")

    def popen(cmd, cwd, env, stdout, stderr):
        calls.append(cmd)
        first = len(calls) == 1
        source = cwd / "patch/build/gen2_frame_align_result.txt"
        source.write_text(inner if first else "RESULT: PASS u1\n", encoding="utf-8")
        os.utime(source, ns=(len(calls) * 1_000_000_000,) * 2)
        stdout.write((FIXTURES / "w6_pytest_tail.txt").read_text(encoding="utf-8")
                     if first else "1 passed\n")
        stdout.flush()
        return type("Process", (), {"wait": lambda self, timeout: int(first)})()

    monkeypatch.setattr(sweep.subprocess, "Popen", popen)
    cell = {"id": "gate/w6/crystal", "kind": "gate", "timeout": 30,
            "commands": [["python", "tests/live/test_gen2_w6_gate.py", "-k", "crystal"]]}
    result = sweep.run_cell(cell, lane, 1, tmp_path / "out", lambda: None)
    archived = [path.read_text(encoding="utf-8") for path in (tmp_path / "out/logs").glob("*_result.txt")]
    assert inner in archived
    assert result["ok"] and result["attempts"] == 2
    assert result["retried"] == "no Poke Ball left in the pocket"
    assert "RESULT: PASS u1\n" in archived


def test_old_gate_archive_cannot_make_a_new_non_rng_failure_retry(tmp_path, monkeypatch):
    lane, out = tmp_path / "lane", tmp_path / "out"
    build, logs = lane / "patch/build", out / "logs"
    build.mkdir(parents=True)
    logs.mkdir(parents=True)
    old = (FIXTURES / "w6_inner_result.txt").read_text(encoding="utf-8")
    (build / "gen2_frame_align_result.txt").write_text(old, encoding="utf-8")
    (logs / "gate__w6__crystal.cmd1.gen2_frame_align_result.txt").write_text(old, encoding="utf-8")
    calls = []
    monkeypatch.setattr(sweep, "git", lambda *_args: "")

    def popen(cmd, cwd, env, stdout, stderr):
        calls.append(cmd)
        stdout.write("binding identity refused\n")
        stdout.flush()
        return type("Process", (), {"wait": lambda self, timeout: 1})()

    monkeypatch.setattr(sweep.subprocess, "Popen", popen)
    cell = {"id": "gate/w6/crystal", "kind": "gate", "timeout": 30,
            "commands": [["python", "tests/live/test_gen2_w6_gate.py"]]}
    result = sweep.run_cell(cell, lane, 1, out, lambda: None)
    assert not result["ok"] and result["attempts"] == 1
    assert "retried" not in result


def test_previous_attempts_stall_cannot_retry_a_new_non_rng_failure(tmp_path, monkeypatch):
    lane = tmp_path / "lane"
    build = lane / "patch/build"
    build.mkdir(parents=True)
    calls = []
    monkeypatch.setattr(sweep, "git", lambda *_args: "")

    def popen(cmd, cwd, env, stdout, stderr):
        calls.append(cmd)
        if len(calls) == 1:
            source = cwd / "patch/build/gen2_frame_align_result.txt"
            source.write_text("[FAIL] no Poke Ball left in the pocket\n", encoding="utf-8")
            stdout.write("no Poke Ball left in the pocket\n")
        else:
            stdout.write("binding identity refused\n")
        stdout.flush()
        return type("Process", (), {"wait": lambda self, timeout: 1})()

    monkeypatch.setattr(sweep.subprocess, "Popen", popen)
    cell = {"id": "gate/engine_sites/crystal", "kind": "gate", "timeout": 30,
            "commands": [["python", "tests/live/test_gen2_frame_align.py"]]}
    result = sweep.run_cell(cell, lane, 1, tmp_path / "out", lambda: None)
    assert not result["ok"] and result["attempts"] == 2


def test_opening_catch_loss_is_a_recognized_sweep_stall():
    assert sweep.RNG_STALL.search("RESULT: FAIL (link route failed: the battle ended without a catch)")


@pytest.mark.parametrize("marker", ["RESULT: FAIL", "[FAIL]"])
@pytest.mark.parametrize("separate_file", [False, True])
def test_gate_non_stall_first_failure_vetoes_later_stall(tmp_path, monkeypatch, marker, separate_file):
    lane = tmp_path / "lane"
    (lane / "patch/build").mkdir(parents=True)
    calls = []
    monkeypatch.setattr(sweep, "git", lambda *_args: "")

    def popen(cmd, cwd, env, stdout, stderr):
        calls.append(cmd)
        stall = "[FAIL] no Poke Ball left in the pocket\n"
        failure = f"{marker} binding identity refused\n"
        (cwd / "patch/build/gen2_frame_align_result.txt").write_text(
            stall if separate_file else failure + stall, encoding="utf-8")
        if separate_file:
            (cwd / "patch/build/gen2_panel_gate_result.txt").write_text(failure + stall, encoding="utf-8")
        stdout.write("gate failed\n")
        stdout.flush()
        return SimpleNamespace(wait=lambda timeout: 1)

    monkeypatch.setattr(sweep.subprocess, "Popen", popen)
    cell = {"id": "gate/w6/crystal", "kind": "gate", "timeout": 30,
            "commands": [["python", "tests/live/test_gen2_w6_gate.py"]]}
    result = sweep.run_cell(cell, lane, 1, tmp_path / "out", lambda: None)
    assert not result["ok"] and result["attempts"] == 1
    assert "retried" not in result
    assert len(calls) == 1


@pytest.mark.parametrize("mixed_failure", [False, True])
@pytest.mark.parametrize("reverse", [False, True])
def test_duo_sweep_requires_every_side_failure_to_be_a_stall(tmp_path, monkeypatch, mixed_failure, reverse):
    lane = tmp_path / "lane"
    lane.mkdir()
    calls = []
    monkeypatch.setattr(sweep, "git", lambda *_args: "")
    monkeypatch.setattr(sweep, "duo_command", lambda *a: ["python", "tools/e2e_duo.py"])
    monkeypatch.setattr(sweep, "collect_duo", lambda *a: {})

    def popen(cmd, cwd, env, stdout, stderr):
        calls.append(cmd)
        first = len(calls) == 1
        lines = ["RESULT: FAIL (the battle ended without a catch)",
                 "RESULT: FAIL (binding identity refused)" if mixed_failure
                 else "RESULT: FAIL (no Poke Ball left in the pocket)"]
        stdout.write("\n".join(lines[::-1] if reverse else lines) + "\n" if first else "passed\n")
        stdout.flush()
        return SimpleNamespace(wait=lambda timeout: int(first))

    monkeypatch.setattr(sweep.subprocess, "Popen", popen)
    cell = {"id": "duo/gen2_new/gen2_changebox", "kind": "duo", "timeout": 30}
    result = sweep.run_cell(cell, lane, 1, tmp_path / "out", lambda: None)
    assert result["ok"] is not mixed_failure
    assert result["attempts"] == len(calls) == (1 if mixed_failure else 2)
    assert ("retried" in result) is not mixed_failure


@pytest.mark.parametrize("leg", ["panel", "sfx", "u1"])
def test_w6_pre_lua_failure_cannot_echo_a_stale_inner_catch_loss(tmp_path, monkeypatch, leg):
    from tests.live import test_gen2_w6_gate as w6
    import run_gb_gate

    lane = tmp_path / "lane"
    inner = lane / "patch/build/stale_result.txt"
    inner.parent.mkdir(parents=True)
    inner.write_text("[FAIL] no Poke Ball left in the pocket\n", encoding="utf-8")
    fixture = lane / "tests/fixtures/gen2/crystal_battle.SaveRAM"
    fixture.parent.mkdir(parents=True)
    fixture.write_bytes(b"fixture")
    qualification = lane / "qualification.json"
    qualification.write_text('{"attempt_id": "current"}', encoding="utf-8")
    spec = SimpleNamespace(title="crystal", name="crystal_battle", target="battle")
    monkeypatch.setattr(w6, "REPO", lane)
    monkeypatch.setattr(w6, "w6_facts", lambda title: {"overlay_sha1": "overlay"})
    monkeypatch.setattr(w6, "frozen_u1", lambda chain: (lane, {}, None))
    monkeypatch.setattr(w6, "_legs", lambda title, u1: {
        leg: ("inner.lua", "patch/build/stale_result.txt", spec.name,
              lambda *_: {}, lambda *_: None, 30)})
    monkeypatch.setitem(w6.gen2_fixtures.BY_NAME, spec.name, spec)
    for name in ("rom_missing_reason", "fixture_missing_reason", "receipt_missing_reason"):
        monkeypatch.setattr(w6.live, name, lambda *a, **kw: None)
    monkeypatch.setattr(w6.live, "qualified_identity", lambda *a, **kw: None)
    monkeypatch.setattr(w6.live, "receipt_file", lambda *a: qualification)
    monkeypatch.setattr(w6.live, "inspect_env", lambda *a, **kw: {})
    monkeypatch.setattr(w6.gen2_source_data, "load_context", lambda *a, **kw:
                        SimpleNamespace(source_record=lambda: {"rom_sha1": "base"}))
    monkeypatch.setattr(run_gb_gate, "run_gate", lambda *a, **kw:
                        (False, "outer_result.txt", "emulator failed before Lua init"))
    monkeypatch.setattr(sweep, "git", lambda *_: "")
    calls, assertions = [], []

    def popen(cmd, cwd, env, stdout, stderr):
        calls.append(cmd)
        with pytest.raises(AssertionError) as caught:
            w6.test_mailbox_write_watch_on_the_overlay(None, "crystal")
        assertions.append(str(caught.value))
        stdout.write(str(caught.value) + "\n")
        stdout.flush()
        return SimpleNamespace(wait=lambda timeout: 1)

    monkeypatch.setattr(sweep.subprocess, "Popen", popen)
    cell = {"id": "gate/w6/crystal", "kind": "gate", "timeout": 30,
            "commands": [["python", "tests/live/test_gen2_w6_gate.py"]]}
    result = sweep.run_cell(cell, lane, 1, tmp_path / "out", lambda: None)
    assert result["attempts"] == 1 and not result["ok"]
    assert len(calls) == 1 and "retried" not in result
    assert "emulator failed before Lua init" in assertions[0]
    assert "no Poke Ball left" not in assertions[0]
    assert not inner.exists()
