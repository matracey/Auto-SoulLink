"""Retry decisions must use fresh, complete gate evidence, not pytest's truncated diagnostic.

Fixtures are minimal verbatim excerpts of freeze #3's retained W6 Crystal failure:
vanilla-sweep-plain-001/producer/logs/gate__w6__crystal.log:54,56,58,59,86,88 and
its .cmd1.gen2_frame_align_result.txt:26,72-74. No emulator is launched here.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import gen2_final_sweep as sweep  # noqa: E402

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures/gen2/sweep_retry"


def test_real_w6_full_result_earns_retry_despite_truncated_pytest_log(tmp_path, monkeypatch):
    lane = tmp_path / "lane"
    build = lane / "patch/build"
    build.mkdir(parents=True)
    calls = []
    monkeypatch.setattr(sweep, "git", lambda *_args: "")

    def popen(cmd, cwd, env, stdout, stderr):
        calls.append(cmd)
        first = len(calls) == 1
        source = cwd / "patch/build/gen2_frame_align_result.txt"
        source.write_text((FIXTURES / "w6_inner_result.txt").read_text(encoding="utf-8")
                          if first else "RESULT: PASS u1\n", encoding="utf-8")
        os.utime(source, ns=(len(calls) * 1_000_000_000,) * 2)
        stdout.write((FIXTURES / "w6_pytest_tail.txt").read_text(encoding="utf-8")
                     if first else "1 passed\n")
        stdout.flush()
        return type("Process", (), {"wait": lambda self, timeout: int(first)})()

    monkeypatch.setattr(sweep.subprocess, "Popen", popen)
    cell = {"id": "gate/w6/crystal", "kind": "gate", "timeout": 30,
            "commands": [["python", "tests/live/test_gen2_w6_gate.py", "-k", "crystal"]]}
    result = sweep.run_cell(cell, lane, 1, tmp_path / "out", lambda: None)
    assert result["ok"] and result["attempts"] == 2
    assert result["retried"] == "no Poke Ball left in the pocket"
    archived = [path.read_text(encoding="utf-8") for path in (tmp_path / "out/logs").glob("*_result.txt")]
    assert (FIXTURES / "w6_inner_result.txt").read_text(encoding="utf-8") in archived
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
            source.write_text((FIXTURES / "w6_inner_result.txt").read_text(encoding="utf-8"), encoding="utf-8")
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
