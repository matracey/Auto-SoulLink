from pathlib import Path

import pytest

lupa = pytest.importorskip("lupa", reason="lupa is needed to execute the Gen 3 guard")

ROOT = Path(__file__).resolve().parents[2]
RUN_LUA = ROOT / "lua" / "gen3" / "run.lua"


def _guard(lua):
    source = RUN_LUA.read_text(encoding="utf-8")
    start = source.index("-- >>> singleton guard")
    start = source.index("\n", start) + 1
    end = source.index("-- <<< singleton guard <<<")
    return lua.execute(source[start:end] + "\nreturn acquire_instance_guard")


def test_singleton_guard_holds_an_exclusive_listener():
    lua = lupa.LuaRuntime(unpack_returned_tuples=True)
    calls = []
    listener = lua.table(
        settimeout=lambda _self, timeout: calls.append(("timeout", timeout)),
        bind=lambda _self, host, port: (calls.append(("bind", host, port)) or True),
        listen=lambda _self, backlog: (calls.append(("listen", backlog)) or True),
        close=lambda _self: calls.append(("close",)),
    )
    socket_module = lua.table(socket=lua.table(tcp=lambda: listener))

    result = _guard(lua)(socket_module, 63642)

    assert result["close"] is not None
    assert calls == [
        ("timeout", 0),
        ("bind", "127.0.0.1", 63642),
        ("listen", 1),
    ]


def test_singleton_guard_refuses_a_second_open():
    lua = lupa.LuaRuntime(unpack_returned_tuples=True)
    closed = []
    listener = lua.table(
        settimeout=lambda _self, _timeout: None,
        bind=lambda _self, _host, _port: (None, "address already in use"),
        close=lambda _self: closed.append(True),
    )
    socket_module = lua.table(socket=lua.table(tcp=lambda: listener))

    result, reason = _guard(lua)(socket_module, 63642)

    assert result is None
    assert reason == "address already in use"
    assert closed == [True]
