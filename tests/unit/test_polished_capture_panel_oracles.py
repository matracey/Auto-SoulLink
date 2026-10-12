"""Replay the capture-negative and real-host paging oracles without an emulator."""
from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def lua_oracle(filename):
    lupa = pytest.importorskip("lupa")
    lua = lupa.LuaRuntime(unpack_returned_tuples=True)
    lua.globals().POL_ORACLE_ONLY = True
    # Oracle replay must never start the emulator/client or load its memory helper.
    lua.execute('dofile = function() error("oracle replay attempted to start the live driver") end')
    return lua, lua.execute((ROOT / "tools/polished_live" / filename).read_text(encoding="utf-8"))


def lua_value(lua, value):
    if isinstance(value, dict):
        return lua.table_from({k: lua_value(lua, v) for k, v in value.items()})
    if isinstance(value, list):
        return lua.table_from([lua_value(lua, v) for v in value])
    return value


@pytest.mark.parametrize("throws,count,ok", [
    ([], 0, False),
    ([{"caught": True, "cap_hits": 1}], 0, False),
    ([{"caught": False, "cap_hits": 0}, {"caught": True, "cap_hits": 1}], 1, True),
    ([{"caught": False, "cap_hits": 0}, {"caught": False, "cap_hits": 1},
      {"caught": True, "cap_hits": 1}], 2, False),
    ([{"caught": False, "cap_hits": 1}, {"caught": False, "cap_hits": 0}], 2, False),
])
def test_capture_negative_requires_observed_clean_failed_throws(throws, count, ok):
    lua, oracle = lua_oracle("live.lua")
    result = oracle.evaluate_failed_throws(lua_value(lua, throws))
    assert result.failed_throw_count == count
    assert result.ok is ok


ROWS = ["SOUL LINK", "", "PAIRS 1/1", "BADGES 0/8", "DEAD ZONES 0", "", " A-B"]


def pages(indexes, counts=None):
    counts = counts if counts is not None else [4] * len(indexes)
    return [{"page": index, "pages": count, "box_rows": [], "staged_text": []}
            for index, count in zip(indexes, counts, strict=True)]


@pytest.mark.parametrize("indexes,counts,ok", [
    ([1, 2, 3, 4], None, True),
    ([2, 3, 4], None, False),  # omitted initial page
    ([1, 2, 3], None, False),  # omitted final page
    ([1, 2, 2, 3, 4], None, False),  # duplicate
    ([1, 3, 4], None, False),  # skipped interior page
    ([1, 2, 3, 4], [4, 3, 4, 4], False),  # unstable count
    ([1, 2, 3], [3, 3, 3], False),  # self-consistent but truncated count
    ([], None, False),
])
def test_real_host_page_oracles_require_exact_server_row_coverage(indexes, counts, ok):
    panel = importlib.import_module("tools.polished_live.panel_host_live")
    observations = pages(indexes, counts)
    checks, failures = panel.judge_pages(ROWS, observations, 2)
    assert bool(checks) and all(check["ok"] for check in checks) is ok
    assert bool(failures) is not ok
    lua, oracle = lua_oracle("panel_host_live.lua")
    result = oracle.evaluate_pages(lua_value(lua, ROWS), lua_value(lua, observations), 2)
    assert result.expected_pages == 4
    assert result.ok is ok


def test_panel_wire_parser_rejects_malformed_evidence(tmp_path):
    panel = importlib.import_module("tools.polished_live.panel_host_live")
    wire = tmp_path / "wire_a.jsonl"
    wire.write_text(json.dumps({"dir": "s2c", "msg": {"commands": []}}) + "\nnot json\n", encoding="utf-8")
    with pytest.raises(ValueError):
        panel.read_jsonl(wire)


def test_retained_real_host_oracle_replays_complete_pages():
    panel = importlib.import_module("tools.polished_live.panel_host_live")
    sha1 = "1" * 40
    ev = {"pair_rows": ROWS, "pages": pages([1, 2, 3, 4]), "lines_per_page": 2,
          "client_receipts": [{"status": "written"}], "driver_writes": [{}] * 6}
    links = {"links": [{"a": {"key": "a", "nickname": "A"},
                        "b": {"key": "b", "nickname": "B"}, "status": "alive"}], "area_states": {}}
    wire = [{"dir": "s2c", "msg": {"commands": [{"cmd": "link_panel", "rows": ROWS}]}},
            {"dir": "c2s", "msg": {"event": "hello", "panel": True, "panel_abi": 3,
                                     "companion_abi": 3, "rom_sha1": sha1}}]
    result = panel.evaluate(ev, links, {"players": {"a": {"badges": 0}}}, wire, sha1, lua_pass=True)
    assert result["verdict"] == "PASS", result
    ev["pages"] = pages([2, 3, 4])
    result = panel.evaluate(ev, links, {"players": {"a": {"badges": 0}}}, wire, sha1, lua_pass=True)
    assert result["verdict"] == "FAIL"


@pytest.mark.parametrize("label,valid", [
    ("STAGE2 an observed failed throw does not fire the capture site", True),
    ("STAGE2 failed throw does not fire the capture site", False),
])
def test_capture_verifier_requires_the_current_producer_contract(label, valid):
    from tools import verify_polished_release as verifier

    raw = f"[ok] {label}\nRESULT: PASS live (1 checks passed)\n".encode()
    assert (not verifier._producer_errors({"expect_scenario": "live/capture-party-only"}, raw)) is valid


@pytest.mark.parametrize("throws,valid", [
    ([{"caught": False, "cap_hits": 0}], True),
    ([], False),
    ([{"caught": False, "cap_hits": 1}], False),
])
def test_verifier_consumes_the_actual_lua_negative_report(throws, valid):
    from tools import verify_polished_release as verifier

    lua, oracle = lua_oracle("live.lua")
    codec = lua.execute((ROOT / "lua/json_codec.lua").read_text(encoding="utf-8"))
    lines = []
    def record(name, ok, _):
        lines.append(f"[{'ok' if ok else 'fail'}] {name}")
    lib = lua.table_from({"ROOT": ROOT.as_posix(), "json": codec,
                          "slurp": lambda path: Path(path).read_text(encoding="utf-8"), "check": record})
    result = oracle.report_failed_throws(lua_value(lua, throws), lib)
    lines.append(f"RESULT: {'PASS' if result.ok else 'FAIL'} live (1 checks passed)")
    errors = verifier._producer_errors({"expect_scenario": "live/capture-party-only"}, "\n".join(lines).encode())
    assert (not errors) is valid
