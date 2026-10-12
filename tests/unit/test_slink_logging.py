"""Execute the launcher's real console tee without starting an emulator."""

from pathlib import Path

import pytest

lupa = pytest.importorskip("lupa.lua54")
SOURCE = (Path(__file__).resolve().parents[2] / "lua/slink.lua").read_text()
TEE = SOURCE[:SOURCE.index("-- ── Gen 1 route")]


@pytest.fixture
def lua():
    runtime = lupa.LuaRuntime(unpack_returned_tuples=True)
    runtime.execute("""
        files, original_calls = {}, {}
        console = {log = function(...)
            original_calls[#original_calls + 1] = {count = select("#", ...), ...}
        end}
        io.open = function()
            if refuse_open then return nil end
            local fh = {entries = {}, flushes = 0, closed = false}
            function fh:write(text)
                assert(not self.closed and not self.fail_write, "write failed")
                self.entries[#self.entries + 1] = text
            end
            function fh:flush() self.flushes = self.flushes + 1 end
            function fh:close() self.closed = true end
            files[#files + 1] = fh
            return fh
        end
    """)
    return runtime


def test_reloads_write_and_flush_each_message_once(lua):
    g = lua.globals()
    for load in range(1, 6):
        lua.execute(TEE)
        current = g.files[load]
        assert len(current.entries) == 2
        before = len(g.original_calls)
        g.console.log("message", None, 3)
        assert len(current.entries) == 3
        assert current.entries[3] == "message\tnil\t3\n"
        assert current.flushes == 3
        assert len(g.original_calls) == before + 1
        assert g.original_calls[before + 1]["count"] == 3
        if load > 1:
            assert g.files[load - 1].closed


def test_failed_open_preserves_console_and_later_reload_has_one_tee(lua):
    g = lua.globals()
    lua.execute(TEE)
    g.refuse_open = True
    lua.execute(TEE)
    g.console.log("console only")
    assert g.files[1].closed
    assert g.original_calls[len(g.original_calls)][1] == "console only"
    assert g.__slink_log_fh is None
    g.refuse_open = False
    lua.execute(TEE)
    g.console.log("recovered")
    assert list(g.files[2].entries.values())[1:] == [
        "[SLink] Console tee -> ../slink_lua.log\n", "recovered\n",
    ]


def test_write_failure_closes_the_handle_without_losing_console_output(lua):
    g = lua.globals()
    lua.execute(TEE)
    g.files[1].fail_write = True
    g.console.log("write failure")
    assert g.files[1].closed
    assert g.__slink_log_fh is None
    assert g.original_calls[len(g.original_calls)][1] == "write failure"
    lua.execute(TEE)
    g.console.log("recovered")
    assert len(g.files[2].entries) == 3
    assert g.files[2].entries[3] == "recovered\n"
