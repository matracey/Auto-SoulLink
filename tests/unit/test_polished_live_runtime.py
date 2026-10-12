"""Isolated contracts for private Polished runtime mechanics (no emulator)."""
import hashlib
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from tools.polished_live import runtime


def test_launch_configuration_is_immutable_and_isolated(tmp_path):
    first = runtime.LaunchConfig.from_env({"POL_LANE": str(tmp_path / "one"), "POL_KIND": "clean", "POL_POS": "1,2,3,4"})
    second = runtime.LaunchConfig.from_env({"POL_LANE": str(tmp_path / "two")})
    assert first.rom == tmp_path / "one/rom/pol_clean.gbc"
    assert first.position == (1, 2, 3, 4)
    assert second.kind == "overlay"
    assert second.position == (24, 3, 48, 12)
    with pytest.raises(FrozenInstanceError):
        first.kind = "overlay"


def test_pinned_staging_refuses_before_writing(tmp_path):
    source, destination = tmp_path / "source.gbc", tmp_path / "private/rom.gbc"
    source.write_bytes(b"cartridge")
    with pytest.raises(SystemExit, match="sha1"):
        runtime.stage_pinned_rom(source, destination, "0" * 40)
    assert not destination.exists()
    expected = hashlib.sha1(b"cartridge").hexdigest()
    assert runtime.stage_pinned_rom(source, destination, expected) == expected
    assert destination.read_bytes() == b"cartridge"


def test_jsonl_recording_rejects_corruption_and_non_objects(tmp_path):
    path = tmp_path / "wire.jsonl"
    path.write_text('{"event":"hello"}\n\n{"event":"tick"}\n', encoding="utf-8")
    assert runtime.read_jsonl(path) == [{"event": "hello"}, {"event": "tick"}]
    for invalid in ('{"event":\n', '[]\n', '{"value": NaN}\n', '{"a":1,"a":2}\n'):
        path.write_text(invalid, encoding="utf-8")
        with pytest.raises(ValueError):
            runtime.read_jsonl(path)


def test_symbol_selection_refuses_missing_names(tmp_path):
    path = tmp_path / "rom.sym"
    path.write_text('01:4567 Entry\n00:c123 Counter\n', encoding="utf-8")
    assert runtime.read_symbols(path, ("Counter", "Entry")) == {"Counter": [0, 0xc123], "Entry": [1, 0x4567]}
    with pytest.raises(SystemExit, match="Missing"):
        runtime.read_symbols(path, ("Missing",))

def test_recording_guard_blocks_mutation_but_preserves_reads():
    from lupa import LuaRuntime

    lua = LuaRuntime(unpack_returned_tuples=True)
    helper = Path(__file__).resolve().parents[2] / "tools/polished_live/recording_guard.lua"
    guard = lua.execute(helper.read_text(encoding="utf-8"))
    lua.globals().guard = guard
    assert lua.execute("""
        local calls, denied = 0, {}
        local memory = {read_u8=function() return 42 end,
                        write_u8=function() calls=calls+1 end}
        local emu = {framecount=function() return 9 end,
                     setregister=function() calls=calls+1 end}
        local m,e = guard.readonly(memory, emu, function(kind,api)
            denied[#denied+1] = kind .. ":" .. api
        end)
        assert(m.read_u8() == 42 and e.framecount() == 9)
        assert(not pcall(m.write_u8, 1, 2))
        assert(not pcall(e.setregister, "PC", 0))
        assert(calls == 0)
        assert(denied[1] == "guest_write:memory.write_u8")
        assert(denied[2] == "cpu_change:emu.setregister")
        return true
    """)


def test_launch_uses_private_configuration_and_cleans_only_owned_process(tmp_path):
    from dataclasses import replace

    symbols = tmp_path / "rom.sym"
    symbols.write_text("00:c123 Counter\n", encoding="utf-8")
    config = replace(runtime.LaunchConfig.from_env({"POL_LANE": str(tmp_path)}),
                     sym=symbols, symbols=("Counter",), emuhawk="private-emulator")
    seen, stopped = [], []
    run = tmp_path / "run"

    def write_config(base, target, **kwargs):
        assert kwargs["saveram_dir"] == str(config.sram)
        Path(target).write_text("private-config", encoding="utf-8")

    class Process:
        pid = 4321
        def poll(self):
            return None

    def popen(command, **kwargs):
        seen.append((command, kwargs))
        return Process()

    def sleep(_):
        (run / "result.txt").write_text("RESULT: PASS recording\n", encoding="utf-8")

    text, pid = runtime.launch(config, "driver.lua", run, {"SLINK_PLAYER": "b"}, 10,
                               popen=popen, clock=lambda: 0, sleep=sleep,
                               stop=lambda proc, label: stopped.append(proc.pid), write_config=write_config)
    assert pid == 4321 and text == "RESULT: PASS recording\n"
    assert stopped == [4321]
    command, kwargs = seen[0]
    assert command == ["private-emulator", f"--lua={(config.repo / 'driver.lua').as_posix()}",
                       f"--config={(run / 'config.ini').as_posix()}", config.rom.as_posix()]
    assert kwargs["env"]["SLINK_PLAYER"] == "b"
    assert kwargs["env"]["POL_RUN"] == run.as_posix()
