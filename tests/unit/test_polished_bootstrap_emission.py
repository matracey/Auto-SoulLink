"""Raw bootstrap/composition receipts emitted by the complete live Lua drivers."""
import json

import pytest
from lupa.lua55 import LuaError, LuaRuntime

from tests.unit.test_polished_live_emission import run_driver
from tests.unit.test_polished_writes_emission import ROOT


@pytest.mark.parametrize("outcome", ["present", "refused", "refused-with-parts"])
def test_bootstrap_records_raw_parts_before_continuing_or_refusing(tmp_path, outcome):
    run_driver(tmp_path, prepare=False, setup=r'''
        local clock_calls = 0
        os.clock = function()
            clock_calls = clock_calls + 1
            return clock_calls == 1 and 20 or 22.5
        end
        function bootstrap()
            frame = 143
            SLINK_GEN2_PARTS.production_admitted = false
            SLINK_GEN2_PARTS.runtime_rom_sha1 = string.rep("b", 40)
            SLINK_GEN2_PARTS.admitted_by = "raw-admission"
            SLINK_GEN2_PARTS.overlay_sha1 = string.rep("c", 40)
            if OUTCOME ~= "present" then
                SLINK_GEN2_CLIENT = nil
                if OUTCOME == "refused" then SLINK_GEN2_PARTS = nil end
            end
        end
        L.die = function(why)
            assert(why == "the Gen 2 route did not start a client (see slink_lua.log)")
            -- The existing driver's finish sentinel also bounds the refusal path.
            L.finish("polished-live")
        end
    '''.replace("OUTCOME", json.dumps(outcome)))
    parts = {
        "pack": "polished_crystal", "title": "polished", "artifact_kind": "overlay",
        "qualification": "DEV_OVERLAY_SHA1", "production_admitted": False,
        "runtime_rom_sha1": "b" * 40, "admitted_by": "raw-admission", "overlay_sha1": "c" * 40,
    }
    if outcome == "refused":
        parts = dict.fromkeys(parts)
    assert json.loads((tmp_path / "bootstrap.json").read_text(encoding="utf-8")) == {
        "schema": "polished-live-bootstrap-v1", "client_present": outcome == "present",
        "frame": 143, "cpu_seconds": 2.5, "parts": parts,
    }
    if outcome != "present":
        assert json.loads((tmp_path / "client_writes.json").read_text(encoding="utf-8")) == []
        assert json.loads((tmp_path / "refusal.json").read_text(encoding="utf-8"))["refused_by"] == "client"


def test_bootstrap_keeps_missing_fields_as_null_without_inventing_admission(tmp_path):
    run_driver(tmp_path, prepare=False)
    parts = json.loads((tmp_path / "bootstrap.json").read_text(encoding="utf-8"))["parts"]
    assert parts == {
        "pack": "polished_crystal", "title": "polished", "artifact_kind": "overlay",
        "qualification": "DEV_OVERLAY_SHA1", "production_admitted": None,
        "runtime_rom_sha1": None, "admitted_by": None, "overlay_sha1": None,
    }


@pytest.mark.parametrize(
    ("overworld", "writer_present", "boxes_present", "metadata"),
    [
        ("{writes={name='observed-writer', version=3}, boxes={name='observed-boxes', version='v2'}}",
         True, True, ("observed-writer", 3, "observed-boxes", "v2")),
        ("{writes={}, boxes={}}", True, True, (None, None, None, None)),
        ("{boxes={}}", False, True, (None, None, None, None)),
        ("{writes={}}", True, False, (None, None, None, None)),
        ("nil", False, False, (None, None, None, None)),
    ],
)
def test_writes_trace_records_raw_composition_even_when_a_component_is_missing(
    tmp_path, overworld, writer_present, boxes_present, metadata,
):
    lua = LuaRuntime(unpack_returned_tuples=True)
    lua.globals().driver_root = ROOT.as_posix()
    lua.globals().run_dir = tmp_path.as_posix()
    lua.execute(r'''
        local real_dofile = dofile
        os.getenv = function(name)
            if name == "SLINK_ROOT" then return driver_root end
            if name == "POL_RUN" then return run_dir end
            return nil
        end
        frame = 100
        emu = {framecount=function() return frame end}
        event = {onframeend=function() end}
        memory = {}
        L = {ROOT=driver_root, RUN=run_dir, failures=0,
             json=real_dofile(driver_root .. "/lua/json_codec.lua")}
        function L.log() end
        function L.check(_, ok) return ok end
        function L.finish() error("composition-observed", 0) end
        function L.die(why) error("unexpected abort:" .. why, 0) end
        function L.hook() end
        -- Stop through the real trace-persisting finish boundary, before write phases.
        function L.to_overworld() L.finish("composition-observed") end
        dofile = function(path)
            if path:match("pol_lib.lua$") then return L end
            if path:match("/lua/slink.lua$") then
                frame = 143
                SLINK_GEN2_CLIENT = {}
                SLINK_GEN2_PARTS = {pack="polished_crystal", artifact_kind="overlay",
                    runtime_rom_sha1="raw-rom", overworld=OVERWORLD}
                return
            end
            return real_dofile(path)
        end
    '''.replace("OVERWORLD", overworld))
    with pytest.raises(LuaError, match="composition-observed"):
        lua.execute('dofile(driver_root .. "/tools/polished_live/writes.lua")')
    trace = json.loads((tmp_path / "trace.json").read_text(encoding="utf-8"))
    assert trace["composition"] == {
        "writer_present": writer_present, "boxes_present": boxes_present, "frame": 143,
        "writer_name": metadata[0], "writer_version": metadata[1],
        "boxes_name": metadata[2], "boxes_version": metadata[3],
    }
