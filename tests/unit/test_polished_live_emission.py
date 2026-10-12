"""Raw artifacts from the complete live driver, not reconstructed check prose."""
import json
from pathlib import Path

import pytest
from lupa.lua55 import LuaError, LuaRuntime

ROOT = Path(__file__).resolve().parents[2]


def run_driver(run_dir, *, prepare=True, stages="", setup=""):
    lua = LuaRuntime(unpack_returned_tuples=True)
    lua.globals().driver_root = ROOT.as_posix()
    lua.globals().run_dir = run_dir.as_posix()
    lua.globals().prepare = prepare
    lua.globals().stages = stages
    lua.execute(r'''
        local real_dofile = dofile
        os.getenv = function(name)
            local env = {SLINK_ROOT=driver_root, POL_STAGES=stages,
                         POL_PREPARE_FULL=prepare and "1" or nil}
            return env[name]
        end
        frame, rom_bank, wram_bank = 100, 3, 1
        emu = {framecount=function() return frame end,
               getregister=function(name) return name == "PC" and 0x652b or 0xff80 end}
        client = {speedmode=function() end, screenshot=function() end}
        gameinfo = {getromhash=function() return string.rep("a", 40) end}
        ram = {WRAM={}, CartRAM={}, ["System Bus"]={}}
        memory = {
            read_u8=function(addr, domain)
                if domain == "System Bus" and addr == 0xff70 then return wram_bank end
                return ram[domain or "System Bus"][addr] or 0
            end,
            write_u8=function(addr, value, domain)
                ram[domain or "System Bus"][addr] = value
                return "written", 17
            end,
        }
        console = {log=function() end}
        hooks, checks = {}, {}
        L = {ROOT=driver_root, RUN=run_dir, SYM={}, hit={}, hits={},
             json=real_dofile(driver_root .. "/lua/json_codec.lua")}
        for _, name in ipairs({"PokeBallEffect", "PokeBallEffect.caught", "PokeBallEffect.SendToPC"}) do
            L.SYM[name] = {3, 0x4000}
        end
        function L.slurp(path)
            local f = assert(io.open(path, "r"))
            local s = f:read("*a"); f:close(); return s
        end
        function L.log() end
        function L.check(name, ok) checks[#checks+1] = {name=name, ok=ok}; return ok end
        function L.finish(tag) error("finished:" .. tag, 0) end
        function L.die(why) error("unexpected abort:" .. why, 0) end
        function L.hook_at(name, bank, addr, callback)
            hooks[name] = callback; L.hits[name] = 0
        end
        function L.hook(name, callback) hooks[name] = callback; L.hits[name] = 0 end
        function L.rombank() return rom_bank end
        function L.rw(name, plus)
            local values = {wPartyCount=6, wMapStatus=2, wScriptRunning=0,
                wGameLogicPaused=0, wLinkMode=0, wBattleMode=0, wMapTileset=1,
                wMapWidth=30, wMapHeight=9, wXCoord=46, wYCoord=4,
                wMenuItemsList=1, wMenuCursorY=1}
            if name == "wMenuItemsList" and plus == 1 then return 4 end
            return values[name] or 0
        end
        function L.wbytes(_, _, n) local t={} for i=1,n do t[i]=0 end return t end
        function L.hex(t) local out={} for _,v in ipairs(t) do out[#out+1]=string.format("%02x",v) end return table.concat(out) end
        function L.map() return 24, 3 end
        function L.after(name, mark) return (L.hit[name] or 0) > mark end
        function L.recent() return false end
        function L.frame()
            frame = frame + 1
            if L.on_frame then L.on_frame() end
        end
        function L.idle(n) for _=1,n do L.frame() end end
        function L.pulse()
            L.frame()
            L.hit.StartMenu, L.hit.SaveGameData = frame, frame
        end
        function L.to_overworld() return true end
        package.loaded.connector = {send=function() end, receive=function() end}
        dofile = function(path)
            if path:match("pol_lib.lua$") then return L end
            if path:match("/lua/slink.lua$") then
                SLINK_GEN2_CLIENT = {hello_sent=true}
                SLINK_GEN2_PARTS = {pack="polished_crystal", title="polished",
                    artifact_kind="overlay", qualification="DEV_OVERLAY_SHA1"}
                if bootstrap then bootstrap() end
                return
            end
            return real_dofile(path)
        end
    ''')
    lua.execute(setup)
    with pytest.raises(LuaError) as finished:
        lua.execute('dofile(driver_root .. "/tools/polished_live/live.lua")')
    expected = "native-full-party-preparation" if prepare else "polished-live"
    assert str(finished.value).splitlines()[0] == f"finished:{expected}"
    return lua


def test_native_preparation_emits_empty_audit_before_its_early_finish(tmp_path):
    run_driver(tmp_path)
    assert json.loads((tmp_path / "client_writes.json").read_text()) == []
    assert json.loads((tmp_path / "prepare_audit.json").read_text()) == {
        "schema": "polished-live-preparation-audit-v1",
        "no_client": True,
        "lua_writes": [],
        "lua_write_count": 0,
        "frame": 1332,
    }


CAPTURE_SETUP = r'''
    local read_word, after, save_pulse = L.rw, L.after, L.pulse
    local party_count, battle_number, battle_start, walking = 5, 0, nil, false
    for _, name in ipairs({"StartBattle", "OWPlayerInput", "LoadBattleMenu",
            "BattleMenu_Run", "PokeBallEffect", "PokeBallEffect.caught",
            "PokeBallEffect.SendToPC"}) do L.SYM[name] = {3, 0x4000} end
    L.rw = function(name, plus)
        if name == "wPartyCount" then return party_count end
        return read_word(name, plus)
    end
    L.after = function(name, mark)
        if name == "StartBattle" and not battle_start then walking = true end
        return after(name, mark)
    end
    local function hit(name, count)
        L.hit[name] = frame; L.hits[name] = (L.hits[name] or 0) + (count or 1)
    end
    local function wrong_bank()
        if disable_wrong_banks then return end
        rom_bank = 7; hooks.capture_site(false); rom_bank = 3
    end
    L.frame = function()
        frame = frame + 1
        if walking then
            walking = false; battle_start = frame; battle_number = battle_number + 1
            hit("StartBattle")
        end
        if battle_start then
            local elapsed = frame - battle_start
            if elapsed == 3 then hit("LoadBattleMenu") end
            if elapsed == 10 then wrong_bank() end
            if battle_number == 1 and elapsed == 12 then hit("BattleMenu_Run", 2) end
            if battle_number == 2 then
                if elapsed == 12 or elapsed == 16 then hit("PokeBallEffect") end
                if elapsed == 13 then wrong_bank() end
                if elapsed == 18 then
                    party_count = 6; hit("PokeBallEffect.caught"); hooks.capture_site(true)
                end
            end
            if elapsed == 20 then hit("OWPlayerInput"); battle_start = nil end
        end
        if L.on_frame then L.on_frame() end
    end
    L.pulse = function(button)
        if battle_start then L.frame() else save_pulse(button) end
    end
'''


def test_capture_emits_executed_run_count_and_wrong_bank_windows(tmp_path):
    run_driver(tmp_path, stages="2", setup=CAPTURE_SETUP)
    report = json.loads((tmp_path / "capture_negative.json").read_text())
    assert report["run_executed"] == 2
    assert report["failed_throw_count"] == 1 and report["capture_hits"] == 0
    assert report["wrong_bank_events"] == [
        {"frame": 111, "bank": 7, "pc": 0x652B, "sp": 0xFF80},
        {"frame": 132, "bank": 7, "pc": 0x652B, "sp": 0xFF80},
        {"frame": 135, "bank": 7, "pc": 0x652B, "sp": 0xFF80},
    ]
    windows = report["battle_windows"]
    assert [(w["label"], w["frame_start"], w["frame_end"]) for w in windows] == [
        ("STAGE2-run", 103, 121), ("STAGE2-catch1", 124, 142),
    ]
    assert [(w["wrong_bank_first"], w["wrong_bank_count"]) for w in windows] == [(1, 1), (2, 2)]
    assert [(t["frame"], t["frame_end_exclusive"], t["wrong_bank_hits"]) for t in report["throws"]] == [
        (134, 138, 1), (138, 143, 0),
    ]


def test_write_audit_preserves_operation_order_and_actual_byte_pre_post_images(tmp_path):
    run_driver(tmp_path, prepare=False, setup=r'''
        function bootstrap()
            ram["System Bus"][0xdccf], ram.CartRAM[0x2010], ram.WRAM[0x1234] = 0xaa, 0x12, 0xf0
            local status, witness = memory.write_u8(0xdccf, 1, "System Bus")
            assert(status == "written" and witness == 17)
            memory.write_u8(0x2010, 0x56, "CartRAM")
            memory.write_u8(0x1234, 0xf1, "WRAM")
            memory.write_u8(0xdccf, 2, "System Bus")
        end
    ''')
    audit = json.loads((tmp_path / "write_audit.json").read_text())
    assert audit["schema"] == "polished-live-write-audit-v1"
    assert [(op["sequence"], op["domain"], op["addr"], op["value"], op["status"])
            for op in audit["write_ops"]] == [
        (1, "System Bus", 0xDCCF, 1, "returned"),
        (2, "CartRAM", 0x2010, 0x56, "returned"),
        (3, "WRAM", 0x1234, 0xF1, "returned"),
        (4, "System Bus", 0xDCCF, 2, "returned"),
    ]
    assert [(s["sequence"], s["bank"], s["n"], s["pre_hex"], s["post_hex"])
            for s in audit["touched_spans"]] == [
        (1, 1, 1, "aa", "01"), (2, 1, 1, "12", "56"),
        (3, 1, 1, "f0", "f1"), (4, 1, 1, "01", "02"),
    ]


def test_write_audit_does_not_report_requested_value_as_the_observed_post_image(tmp_path):
    run_driver(tmp_path, prepare=False, setup=r'''
        memory.write_u8 = function() return false end
        function bootstrap()
            ram["System Bus"][0xdccf] = 0xaa
            assert(memory.write_u8(0xdccf, 1, "System Bus") == false)
        end
    ''')
    audit = json.loads((tmp_path / "write_audit.json").read_text())
    assert audit["write_ops"][0]["value"] == 1
    assert audit["touched_spans"][0]["pre_hex"] == "aa"
    assert audit["touched_spans"][0]["post_hex"] == "aa"


@pytest.mark.parametrize("partial", [False, True], ids=["no-mutation", "partial-mutation"])
def test_raised_write_preserves_the_attempt_and_observed_partial_state(tmp_path, partial):
    lua_partial = "true" if partial else "false"
    run_driver(tmp_path, prepare=False, setup=r'''
        local partial = PARTIAL
        memory.write_u8 = function(addr, _, domain)
            if partial then ram[domain][addr] = 0x55 end
            error("poke failed", 0)
        end
        function bootstrap()
            ram["System Bus"][0xdccf] = 0xaa
            local ok, why = pcall(memory.write_u8, 0xdccf, 1, "System Bus")
            assert(not ok and why == "poke failed")
        end
    '''.replace("PARTIAL", lua_partial))
    audit = json.loads((tmp_path / "write_audit.json").read_text())
    assert audit["write_ops"][0]["status"] == "raised"
    assert audit["write_ops"][0]["error"] == "poke failed"
    assert audit["touched_spans"][0]["pre_hex"] == "aa"
    assert audit["touched_spans"][0]["post_hex"] == ("55" if partial else "aa")


def test_unreadable_span_images_are_explicit_unknowns_not_zero_bytes(tmp_path):
    run_driver(tmp_path, prepare=False, setup=r'''
        memory.read_u8 = function() error("RAM unreadable", 0) end
        function bootstrap() memory.write_u8(0xdccf, 1, "System Bus") end
    ''')
    span = json.loads((tmp_path / "write_audit.json").read_text())["touched_spans"][0]
    assert span["pre_hex"] is None and span["post_hex"] is None
    assert span["pre_read_error"] == "RAM unreadable" and span["post_read_error"] == "RAM unreadable"
    assert span["bank"] is None


def test_preparation_does_not_invent_no_client_for_a_stale_export(tmp_path):
    run_driver(tmp_path, setup="SLINK_GEN2_CLIENT = {hello_sent=true}")
    report = json.loads((tmp_path / "prepare_audit.json").read_text())
    assert report["no_client"] is False
    assert report["lua_write_count"] == 0


def test_panel_hello_emits_an_explicit_empty_attempt_array(tmp_path):
    run_driver(tmp_path, prepare=False)
    assert json.loads((tmp_path / "panel_attempts.json").read_text()) == []


@pytest.mark.parametrize("fails", [False, True], ids=["written", "partial-error"])
def test_panel_hello_preserves_real_permit_attempts_including_partial_errors(tmp_path, fails):
    run_driver(tmp_path, prepare=False, setup=r'''
        local original = memory.write_u8
        memory.write_u8 = function(addr, ...)
            if FAILS and addr == 0xc611 then error("panel byte failed", 0) end
            return original(addr, ...)
        end
        function bootstrap()
            local Panel = dofile(driver_root .. "/lua/gen2/panel.lua")
            local Permit = dofile(driver_root .. "/lua/write_permit.lua")
            local writes = Panel.writes({
                write_u8=function(...) return memory.write_u8(...) end,
                framecount=function() return frame end,
            }, Permit, {base=0xc610, size=16})
            SLINK_GEN2_PARTS.panel_writes = writes
            writes:arm("panel")
            local ok = pcall(function() writes:write_bytes(0xc610, {0x11, 0x22}) end)
            writes:disarm()
            assert(ok ~= FAILS)
        end
    '''.replace("FAILS", "true" if fails else "false"))
    rows = json.loads((tmp_path / "panel_attempts.json").read_text())
    expected = {
        "addr": 0xC610, "n": 2, "why": "panel", "frame": 100,
        "domain": "System Bus", "status": "error" if fails else "written",
        "completed": 1 if fails else 2, "attempted": 2,
    }
    if fails:
        expected["error"] = "panel byte failed"
    assert [{key: row[key] for key in expected} for row in rows] == [expected]


def test_capture_with_no_wrong_bank_callbacks_emits_empty_arrays_and_zero_windows(tmp_path):
    run_driver(tmp_path, stages="2", setup="disable_wrong_banks = true\n" + CAPTURE_SETUP)
    report = json.loads((tmp_path / "capture_negative.json").read_text())
    assert report["wrong_bank_events"] == []
    assert [w["wrong_bank_count"] for w in report["battle_windows"]] == [0, 0]
    assert [t["wrong_bank_hits"] for t in report["throws"]] == [0, 0]


def test_preparation_write_audit_does_not_hide_a_lua_mutation_without_a_client(tmp_path):
    run_driver(tmp_path, setup=r'''
        local entered = false
        L.to_overworld = function()
            if not entered then
                entered = true; memory.write_u8(0xc610, 0x77, "System Bus")
            end
            return true
        end
    ''')
    report = json.loads((tmp_path / "prepare_audit.json").read_text())
    assert report["no_client"] is True and report["lua_write_count"] == 1
    assert report["lua_writes"] == json.loads((tmp_path / "client_writes.json").read_text())
    span = json.loads((tmp_path / "write_audit.json").read_text())["touched_spans"][0]
    assert (span["pre_hex"], span["post_hex"]) == ("00", "77")
