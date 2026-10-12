"""Exercise the production frame driver and HelloSession without cartridge fixtures."""

from pathlib import Path

import pytest
from lupa.lua54 import LuaRuntime

ROOT = Path(__file__).resolve().parents[2]

MODEL = r"""
local root = ...
local HelloSession = dofile(root .. "/lua/hello_session.lua")
local clock, messages, identity_reads = 0, {}, 0
local net = {up=true}
function net.pump()
    if net.next_up ~= nil then net.up, net.next_up = net.next_up, nil end
end
function net.connected() return net.up end
local io = {mapped=true}
function io.framecount() return clock end
function io.bank_valid() return io.mapped end
local profile = {ram_bank={wPlayerID=1}, ram={wPlayerID=0xD000}}
local Client = {VALIDATE_EVERY=1000, TICK_INTERVAL=1, BURIAL_NAG_FRAMES=1200}
local panel, phone, trade
local function idle() end
local function send(event) messages[#messages + 1] = event; return true end
local self = {
    held={}, box_complete=true, refusals_logged={},
    owed={step=idle}, replies={step=idle},
    validate=idle, settle_faints=idle, land_bench_deaths=idle,
    rival_tick=idle, run_deferred=idle, release_battle_writes=idle,
    abandon_timeline=function() error("unexpected timeline reset") end,
    send_tick=function(_, event) send(event) end,
}
self.hello_session = HelloSession.new({
    connected=net.connected,
    identity=function()
        assert(io.mapped, "identity read while WRAM bank unavailable")
        identity_reads = identity_reads + 1
        return "1234"
    end,
    ready=function() return true end,
    send=function() return send("hello") end,
    retry_delay=function() return 1 end,
    clock_rewind="keep", callback_error="raise",
    on_invalidate=function() self.hello_sent=false end,
    on_error=function(_, why) error(why) end,
})
"""


def world():
    source = (ROOT / "lua/gen2/client.lua").read_text(encoding="utf-8")
    start = source.index("    function self:frame_end()")
    end = source.index("    function self:stop()", start)
    lua = LuaRuntime(unpack_returned_tuples=True)
    return lua.execute(MODEL + source[start:end] + r"""
return {
    frame=function(mapped, next_up)
        io.mapped, net.next_up = mapped, next_up
        clock = clock + 1
        self:frame_end()
    end,
    status=function() return self.hello_session:status() end,
    messages=function() return messages end,
    reads=function() return identity_reads end,
    hold=function() self.held[#self.held + 1] = {event="capture", fields={}} end,
}
""", ROOT.as_posix())


@pytest.mark.parametrize("offbank_frames", [1, 3, 35])
def test_disconnect_and_reconnect_offbank_requires_a_new_hello(offbank_frames):
    model = world()
    model.frame(True, True)
    assert list(model.messages().values()) == ["hello", "tick"]
    reads = model.reads()
    model.frame(False, False)
    assert model.status().ready is False
    model.hold()
    for _ in range(offbank_frames):
        model.frame(False, True)
    assert model.reads() == reads
    assert list(model.messages().values()) == ["hello", "tick"]
    model.frame(True, True)
    assert model.status().ready is True
    assert list(model.messages().values()) == ["hello", "tick", "hello", "capture", "tick"]


def test_bank_switch_without_disconnect_does_not_rehello():
    model = world()
    model.frame(True, True)
    generation = model.status().generation
    reads = model.reads()
    for _ in range(5):
        model.frame(False, True)
    assert model.reads() == reads
    model.frame(True, True)
    assert model.status().generation == generation
    assert list(model.messages().values()) == ["hello", "tick", "tick"]


def test_mapped_disconnect_still_rehellos_before_ticks():
    model = world()
    model.frame(True, True)
    model.frame(True, False)
    assert model.status().ready is False
    model.frame(True, True)
    assert list(model.messages().values()) == ["hello", "tick", "hello", "tick"]
