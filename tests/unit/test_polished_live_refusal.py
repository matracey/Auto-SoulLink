"""Persist the live producer's refusal evidence before its fatal exit."""
import json
from pathlib import Path

import pytest
from lupa.lua55 import LuaError, LuaRuntime

ROOT = Path(__file__).resolve().parents[2]
ABORT = "the Gen 2 route did not start a client (see slink_lua.log)"
BEACON_REFUSAL = (
    "[SLink-gen2] polished cartridge refused (production admission): "
    "unknown artifact SHA-1 abc: its Polished SLink companion overlay is modified "
    "or incomplete (overlay beacon mismatch); prepare it through the Manager or /patcher"
)


def run_refusal(run_dir, logs=(), writes=0):
    lua = LuaRuntime(unpack_returned_tuples=True)
    lua.globals().driver_root = ROOT.as_posix()
    lua.globals().run_dir = run_dir.as_posix()
    lua.globals().startup_logs = lua.table_from(logs)
    lua.globals().startup_writes = writes
    lua.execute(r'''
        local real_dofile = dofile
        os.getenv = function(name)
            if name == "SLINK_ROOT" then return driver_root end
            return nil
        end
        local frame = 4123
        emu = {framecount = function() return frame end}
        client = {speedmode = function() end}
        gameinfo = {getromhash = function() return string.rep("a", 40) end}
        memory = {write_u8 = function() end}
        console_lines = {}
        console = {log = function(...)
            console_lines[#console_lines + 1] = {...}
        end}
        L = {ROOT = driver_root, RUN = run_dir, SYM = {},
             json = real_dofile(driver_root .. "/lua/json_codec.lua")}
        function L.slurp(path)
            local f = assert(io.open(path, "r"))
            local text = f:read("*a"); f:close(); return text
        end
        function L.log() end
        function L.hook_at() end
        function L.die(why) error(why, 0) end
        dofile = function(path)
            if path:match("pol_lib.lua$") then return L end
            if path:match("/lua/slink.lua$") then
                -- The launcher's console tee captures the logger present at entry.
                local previous = console.log
                console.log = function(...) return previous(...) end
                for i = 1, startup_writes do
                    memory.write_u8(0xc610 + i - 1, i, "System Bus")
                end
                for _, text in ipairs(startup_logs) do console.log(text) end
                return
            end
            return real_dofile(path)
        end
    ''')
    with pytest.raises(LuaError) as aborted:
        lua.execute('dofile(driver_root .. "/tools/polished_live/live.lua")')
    assert str(aborted.value).splitlines()[0] == ABORT
    return lua


def test_beacon_refusal_persists_zero_write_audit_and_named_reason_before_abort(tmp_path):
    run_refusal(tmp_path, logs=[BEACON_REFUSAL])
    assert json.loads((tmp_path / "client_writes.json").read_text()) == []
    assert json.loads((tmp_path / "refusal.json").read_text()) == {
        "schema": "polished-live-refusal-v1",
        "refused_by": "client",
        "reason": BEACON_REFUSAL,
        "frame": 4123,
        "lua_writes": 0,
    }


@pytest.mark.parametrize("logs", [
    [],
    ["[SLink-gen2] frame error: unrelated failure"],
    ["unrelated cartridge refused (production admission): not a client reason"],
    [17],
], ids=["absent", "frame-error", "unrelated-prefix", "non-string"])
def test_unavailable_client_reason_is_explicit_json_null(tmp_path, logs):
    run_refusal(tmp_path, logs=logs)
    report = json.loads((tmp_path / "refusal.json").read_text())
    assert "reason" in report and report["reason"] is None
    assert json.loads((tmp_path / "client_writes.json").read_text()) == []


def test_refused_start_preserves_the_full_console_message_and_tee(tmp_path):
    message = '[SLink-gen2] refused to start: bad "site"\nprepare through /patcher'
    lua = run_refusal(tmp_path, logs=[message])
    assert json.loads((tmp_path / "refusal.json").read_text())["reason"] == message
    assert lua.globals().console_lines[1][1] == message


def test_writes_before_client_refusal_are_recorded_not_disguised_as_zero(tmp_path):
    run_refusal(tmp_path, logs=[BEACON_REFUSAL], writes=2)
    report = json.loads((tmp_path / "refusal.json").read_text())
    audit = json.loads((tmp_path / "client_writes.json").read_text())
    assert report["lua_writes"] == 2
    assert isinstance(audit, list) and len(audit) == report["lua_writes"]
