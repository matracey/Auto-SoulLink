"""The Windows connector must not treat a zero-byte send as connect completion."""

import os

import pytest

lupa = pytest.importorskip("lupa", reason="lupa is needed to execute connector.lua")

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_CONNECTOR = os.path.join(_REPO, "lua", "connector.lua")

_HARNESS = r"""
local M = { sends = 0, sent = {}, closed = false }
local sock = {}
sock.__index = sock

function sock:settimeout(_) end
function sock:connect(_, _) return nil, "timeout" end
function sock:close() M.closed = true end
function sock:send(data, _)
    M.sends = M.sends + 1
    table.insert(M.sent, data)
    if M.sends == 1 then return 0 end
    if M.sends == 2 then return nil, "Socket is not connected", 0 end
    if M.sends == 3 then return nil, "timeout", 0 end
    return #data
end
function sock:receive(_) return nil, "timeout", "" end

M.new = function() return setmetatable({}, sock) end
return M
"""


def test_pending_connect_waits_until_probe_byte_is_writable():
    rt = lupa.LuaRuntime(unpack_returned_tuples=True)
    mock = rt.execute(_HARNESS)
    logs = []
    rt.globals().console = rt.table_from({"log": lambda value: logs.append(str(value))})
    fake_socket = rt.table_from({"socket": rt.table_from({"tcp4": lambda: mock.new()})})
    rt.execute(
        'local fake = ...; package.preload["socket"] = function() return fake end',
        fake_socket,
    )

    with open(_CONNECTOR, encoding="utf-8") as source:
        connector = rt.execute(source.read())

    connector.init("soullink.example", 54321)
    assert not connector.connected()

    connector.pump()
    connector.pump()
    connector.pump()
    assert not connector.connected()
    assert mock.sends == 3

    connector.pump()
    assert connector.connected()
    assert mock.sends == 4
    assert all(mock.sent[index] == "\n" for index in range(1, 5))
    assert any("TCP connected" in line for line in logs)
