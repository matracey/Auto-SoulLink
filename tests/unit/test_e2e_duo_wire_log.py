"""`--wire-log`: the golden-transcript capture for Gen 3 (card gen3-P1-C1-3).

Two halves, and the second one is the point:

* the harness (`tools/e2e_duo.py`) only adds `--wire-log DIR` to the server's argv when the
  flag is given, and promotes the server's capture to
  `tests/fixtures/gen3/wire/<scenario>_<player>_old_client.jsonl`;
* the server (`server/server.py`) writes that capture from a `_WireTap` wrapped around the
  connection's StreamWriter, so the transcript is the bytes that actually crossed the socket.

The invariance test is the one that guards production: without the flag, `server_cmd()` must be
the literal argv the runner has always built. Drop the `if wire:` guard and it goes red.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

import pytest
import pytest_asyncio

_REPO = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, os.path.join(_REPO, "tools"))

import e2e_duo as duo  # noqa: E402

from server.server import SLinkServer  # noqa: E402

HELLO_A = {"event": "hello", "player": "a", "rom_type": "FRLG_1_0", "trainer_name": "Alice",
           "ot_id": "30B8", "has_pokeballs": True}


def _run(tmp_path, **overrides) -> duo.DuoRun:
    """A DuoRun with only the fields the argv and collection paths read."""
    run = duo.DuoRun.__new__(duo.DuoRun)
    run.scenario = "faint"
    run.cfg = duo.SCENARIOS["faint"]
    run.tcp_port = 54321
    run.http_port = 8080
    run.data_dir = str(tmp_path / "data")
    base = {"server_flags": [], "wire_log": False}
    base.update(overrides)
    run.args = argparse.Namespace(**base)
    return run


# ── the flag ──────────────────────────────────────────────────────────────────

def _parse(monkeypatch, argv):
    """The real parser, reached by driving `main()` down its `--list` exit."""
    captured = {}
    real = argparse.ArgumentParser.parse_args

    def spy(self, *a, **kw):
        captured["args"] = real(self, *a, **kw)
        return captured["args"]

    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", spy)
    monkeypatch.setattr(duo, "list_lines", lambda game: [])
    monkeypatch.setattr(sys, "argv", ["e2e_duo.py", *argv, "--list"])
    with pytest.raises(SystemExit) as exit_info:
        duo.main()
    assert exit_info.value.code == 0
    return captured["args"]


def test_the_flag_parses_and_defaults_off(monkeypatch):
    assert _parse(monkeypatch, []).wire_log is False
    assert _parse(monkeypatch, ["--wire-log"]).wire_log is True


# ── argv invariance ──────────────────────────────────────────────────────────

def test_without_the_flag_the_server_argv_is_unchanged(tmp_path):
    run = _run(tmp_path)
    run._server_run_id = "wire-log-model"
    assert run.server_cmd() == [
        sys.executable, "-m", "server.server",
        "--host", "127.0.0.1",
        "--port", "54321",
        "--http-port", "8080",
        "--data-dir", run.data_dir,
        "--run-id", "wire-log-model",
    ] + run.cfg["flags"]
    assert run._wire_dir() is None
    assert run.collect_wire_logs() == []


def test_with_the_flag_the_server_argv_gains_exactly_the_wire_log_pair(tmp_path):
    plain_run = _run(tmp_path)
    plain_run._server_run_id = "wire-log-model"
    plain = plain_run.server_cmd()
    run = _run(tmp_path, wire_log=True)
    run._server_run_id = "wire-log-model"
    assert run.server_cmd() == plain + ["--wire-log", os.path.join(run.data_dir, "wire")]


def test_extra_server_flags_still_come_before_wire_log(tmp_path):
    run = _run(tmp_path, wire_log=True, server_flags=["--verbose"])
    cmd = run.server_cmd()
    assert cmd[-2:] == ["--wire-log", run._wire_dir()]
    assert "--verbose" in cmd[:-2]


# ── output path naming ───────────────────────────────────────────────────────

def test_collection_names_files_by_scenario_and_player(tmp_path, monkeypatch):
    monkeypatch.setattr(duo, "WIRE_FIXTURES", str(tmp_path / "fixtures"))
    run = _run(tmp_path, wire_log=True)
    run.scenario = "boxsync"
    run.gcfg = duo.GAMES["gen3_rr"]   # every active Gen 3 row is the new battery client (ac448144)
    os.makedirs(run._wire_dir())
    for player in ("a", "b"):
        with open(os.path.join(run._wire_dir(), f"wire_{player}.jsonl"), "w",
                  encoding="utf-8") as handle:
            handle.write(json.dumps({"dir": "c2s", "t": 1, "msg": {"player": player}}) + "\n")
    with open(os.path.join(run._wire_dir(), "server.log"), "w", encoding="utf-8") as handle:
        handle.write("not a transcript\n")

    landed = run.collect_wire_logs()

    assert sorted(os.path.basename(p) for p in landed) == [
        "boxsync_a_gen3_new.jsonl", "boxsync_b_gen3_new.jsonl"]
    with open(landed[0], encoding="utf-8") as handle:
        assert json.loads(handle.read())["msg"]["player"] == "a"


def _older_run_goldens(fixtures, scenario, players=("a", "b")) -> None:
    """Seed the golden paths with an EARLIER, passing run's transcripts (T5's stale copy)."""
    fixtures.mkdir(parents=True, exist_ok=True)
    for player in players:
        (fixtures / f"{scenario}_{player}_gen3_new.jsonl").write_text(
            json.dumps({"dir": "c2s", "t": 1, "msg": {"event": "trade_done"}}) + "\n",
            encoding="utf-8")


def test_a_player_this_run_never_logged_loses_its_older_transcript(tmp_path, monkeypatch, capsys):
    """A failing run must not leave an earlier PASSING run's transcript readable at its scenario's
    golden path -- T5 (2026-09-27) found `native_trade_firered_b_gen3_new.jsonl` holding a
    completed trade_done while the failing run's own `wire/wire_b.jsonl` had none. B never logged
    here, so B's stale copy is removed and the print names the data dir instead."""
    fixtures = tmp_path / "fixtures"
    monkeypatch.setattr(duo, "WIRE_FIXTURES", str(fixtures))
    run = _run(tmp_path, wire_log=True)
    run.scenario = "trade_gen3"
    run.gcfg = duo.GAMES["gen3_rr"]
    os.makedirs(run._wire_dir())
    with open(os.path.join(run._wire_dir(), "wire_a.jsonl"), "w", encoding="utf-8") as handle:
        handle.write(json.dumps({"dir": "c2s", "t": 1, "msg": {"player": "a"}}) + "\n")
    _older_run_goldens(fixtures, "trade_gen3")

    landed = run.collect_wire_logs()

    a = fixtures / "trade_gen3_a_gen3_new.jsonl"
    b = fixtures / "trade_gen3_b_gen3_new.jsonl"
    assert landed == [str(a)]
    # A did log, so its copy is this run's line, not the older run's trade_done.
    assert json.loads(a.read_text(encoding="utf-8"))["msg"] == {"player": "a"}
    assert not b.exists()
    printed = capsys.readouterr().out
    assert os.path.join(run._wire_dir(), "wire_b.jsonl") in printed
    assert str(b) in printed


def test_a_run_that_never_opened_the_wire_dir_clears_its_older_transcripts(tmp_path, monkeypatch):
    """No per-player capture at all (the server wrote no wire dir) must leave neither golden."""
    fixtures = tmp_path / "fixtures"
    monkeypatch.setattr(duo, "WIRE_FIXTURES", str(fixtures))
    run = _run(tmp_path, wire_log=True)
    run.gcfg = duo.GAMES["gen3_rr"]
    _older_run_goldens(fixtures, "faint")

    assert not os.path.isdir(run._wire_dir())
    assert run.collect_wire_logs() == []
    assert list(fixtures.iterdir()) == []


def test_without_the_flag_no_golden_is_ever_removed(tmp_path, monkeypatch):
    """A run without --wire-log never touches the fixtures, stale or not."""
    fixtures = tmp_path / "fixtures"
    monkeypatch.setattr(duo, "WIRE_FIXTURES", str(fixtures))
    run = _run(tmp_path)
    run.gcfg = duo.GAMES["gen3_rr"]
    _older_run_goldens(fixtures, "faint")

    assert run.collect_wire_logs() == []
    assert len(list(fixtures.iterdir())) == 2


# ── the capture itself, over a real socket ───────────────────────────────────

@pytest_asyncio.fixture
async def wired(tmp_path):
    """A server with --wire-log pointed at a tmp dir, listening on an ephemeral port."""
    srv = SLinkServer(data_dir=str(tmp_path / "data"), wire_log=str(tmp_path / "wire"))
    tcp = await asyncio.start_server(srv.handle_client, "127.0.0.1", 0)
    try:
        yield tcp.sockets[0].getsockname()[1], str(tmp_path / "wire")
    finally:
        tcp.close()
        await tcp.wait_closed()


async def _send(writer, reader, msg):
    writer.write((json.dumps(msg) + "\n").encode())
    await writer.drain()
    return json.loads(await asyncio.wait_for(reader.readline(), 3))


def _lines(wire_dir, player):
    with open(os.path.join(wire_dir, f"wire_{player}.jsonl"), encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


@pytest.mark.asyncio
async def test_every_line_lands_in_capture_order_with_req_linking_the_reply(wired):
    port, wire_dir = wired
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    await _send(writer, reader, dict(HELLO_A, seq=1))
    await _send(writer, reader, {"event": "tick", "player": "a", "seq": 2, "party": [],
                                 "area_id": "pallet_town"})
    writer.close()
    await writer.wait_closed()
    await asyncio.sleep(0.05)

    rows = _lines(wire_dir, "a")
    # t is capture order for the file, not the protocol's own seq: a _connect meta record is
    # first, then each c2s/s2c pair gets its own next number, and each reply's req points at
    # the request it answers.
    assert [(r["dir"], r["t"], r.get("req")) for r in rows] == [
        ("meta", 1, None),
        ("c2s", 2, None),
        ("s2c", 3, 2),
        ("c2s", 4, None),
        ("s2c", 5, 4),
        ("meta", 6, None),
    ]
    assert rows[0]["msg"] == {"event": "_connect"}
    assert rows[-1]["msg"] == {"event": "_disconnect"}
    assert rows[1]["msg"]["event"] == "hello"
    # Verbatim, field order included: no re-serialization beyond loads/dumps.
    assert list(rows[1]["msg"]) == list(dict(HELLO_A, seq=1))
    assert list(rows[2]["msg"]) == ["commands"]
    assert isinstance(rows[2]["msg"]["commands"], list)


@pytest.mark.asyncio
async def test_a_reconnect_shares_the_file_and_keeps_counting(wired):
    """Same player, two connections: one file, an unbroken t sequence, a _disconnect/_connect
    pair marking the boundary between them."""
    port, wire_dir = wired
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    await _send(writer, reader, dict(HELLO_A))  # no seq at all
    writer.close()
    await writer.wait_closed()
    await asyncio.sleep(0.05)

    reader2, writer2 = await asyncio.open_connection("127.0.0.1", port)
    await _send(writer2, reader2, dict(HELLO_A))
    writer2.close()
    await writer2.wait_closed()
    await asyncio.sleep(0.05)

    rows = _lines(wire_dir, "a")
    assert [(r["dir"], r["msg"].get("event") if r["dir"] == "meta" else None) for r in rows] == [
        ("meta", "_connect"), ("c2s", None), ("s2c", None), ("meta", "_disconnect"),
        ("meta", "_connect"), ("c2s", None), ("s2c", None), ("meta", "_disconnect"),
    ]
    assert [r["t"] for r in rows] == list(range(1, len(rows) + 1))  # one unbroken counter


@pytest.mark.asyncio
async def test_no_wire_log_writes_nothing(tmp_path):
    """The default server never builds a tap, so no directory appears."""
    srv = SLinkServer(data_dir=str(tmp_path / "data"))
    assert srv._wire_log is None
    tcp = await asyncio.start_server(srv.handle_client, "127.0.0.1", 0)
    try:
        port = tcp.sockets[0].getsockname()[1]
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        assert "commands" in await _send(writer, reader, dict(HELLO_A, seq=1))
        writer.close()
        await writer.wait_closed()
    finally:
        tcp.close()
        await tcp.wait_closed()
    assert not os.path.isdir(tmp_path / "wire")


def test_a_tap_io_failure_never_reaches_the_connection(tmp_path, monkeypatch):
    """Adapter-guard finding on b0e0538: disk/permission failures inside the tap are logged and
    dropped, never raised into handle_client (which would kill that client's task)."""
    from server.server import _WireTap

    class _Writer:
        def __init__(self):
            self.sent = []

        def write(self, data):
            self.sent.append(data)

    w = _Writer()
    tap = _WireTap(w, str(tmp_path / "nope"), {})

    def _boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr("builtins.open", _boom)
    tap.c2s('{"event":"hello","player":"a","seq":1}', {"event": "hello", "player": "a", "seq": 1})
    tap.write(b'{"commands":[]}\n')
    assert w.sent == [b'{"commands":[]}\n']  # the reply still went out


# ── review findings (card gen3-P1-C1-3b): unvalidated players, t semantics, malformed lines ──

def _tap(tmp_path, files=None):
    from server.server import _WireTap

    class _Writer:
        def write(self, data):
            pass

    return _WireTap(_Writer(), str(tmp_path / "wire"), files if files is not None else {})


def test_wire_log_redacts_instrumentation_key_without_mutating_message(tmp_path):
    tap = _tap(tmp_path)
    msg = {
        "event": "hello",
        "player": "a",
        "seq": 1,
        "instrumentation_key": "player-a-key",
    }

    tap.c2s(json.dumps(msg), msg)

    row = next(row for row in _lines(tmp_path / "wire", "a") if row["dir"] == "c2s")
    assert row["msg"]["instrumentation_key"] == "[redacted]"
    assert msg["instrumentation_key"] == "player-a-key"


def test_an_unrecognised_player_id_goes_to_the_bounded_rejected_sink(tmp_path):
    tap = _tap(tmp_path)
    tap.c2s('{"event":"hello","player":"c"}', {"event": "hello", "player": "c"})
    with open(tmp_path / "wire" / "wire_rejected.jsonl", encoding="utf-8") as f:
        rows = [json.loads(line) for line in f]
    assert [r["dir"] for r in rows] == ["meta", "c2s"]
    assert rows[1]["msg"] == {"event": "hello", "player": "c"}
    assert not os.path.exists(tmp_path / "wire" / "wire_c.jsonl")


def test_the_rejected_sink_is_capped_then_dropped(tmp_path):
    from server.server import _WireTap
    tap = _tap(tmp_path)
    for _ in range(_WireTap._REJECTED_CAP + 20):
        tap.c2s('{"player":"nope"}', {"player": "nope"})
    with open(tmp_path / "wire" / "wire_rejected.jsonl", encoding="utf-8") as f:
        n = sum(1 for _ in f)
    assert n == _WireTap._REJECTED_CAP


def test_a_non_dict_line_is_recorded_raw_and_does_not_raise(tmp_path):
    tap = _tap(tmp_path)
    tap.c2s("[1, 2, 3]", [1, 2, 3])  # valid JSON, not protocol -- must not raise
    with open(tmp_path / "wire" / "wire_rejected.jsonl", encoding="utf-8") as f:
        rows = [json.loads(line) for line in f]
    malformed = rows[1]
    assert malformed["dir"] == "c2s"
    assert malformed["raw"] == "[1, 2, 3]"
    assert malformed["msg"] is None
    # the following reply's req points at the malformed line's own t, not the previous one
    tap.write(b'{"commands":[]}\n')
    with open(tmp_path / "wire" / "wire_rejected.jsonl", encoding="utf-8") as f:
        rows = [json.loads(line) for line in f]
    assert rows[-1]["req"] == malformed["t"]


def test_at_most_three_handles_ever_open(tmp_path):
    tap = _tap(tmp_path)
    tap.c2s('{"player":"a"}', {"player": "a"})
    tap2 = _tap(tmp_path, files=tap._files)
    tap2.c2s('{"player":"b"}', {"player": "b"})
    tap3 = _tap(tmp_path, files=tap._files)
    tap3.c2s('{"player":"?"}', {"player": "?"})
    assert set(tap._files) == {"a", "b", "rejected"}


async def _raw_send(writer, reader, line):
    writer.write((line + "\n").encode("utf-8"))
    await writer.drain()
    return json.loads(await asyncio.wait_for(reader.readline(), 3))


async def _close_socket(writer):
    writer.close()
    await writer.wait_closed()
    await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_valid_request_invalid_json_noop_attribution(wired):
    port, directory = wired
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        await _send(writer, reader, HELLO_A)
        assert await _raw_send(writer, reader, "{broken") == {"commands": [{"cmd": "noop"}]}
        raw = _lines(directory, "rejected")[-1]
        reply = _lines(directory, "a")[-1]
        assert raw["raw"] == "{broken"
        assert reply["req"] == {"sink": "rejected", "t": raw["t"]}
        assert reply["conn"] == raw["conn"]
    finally:
        await _close_socket(writer)


@pytest.mark.asyncio
async def test_valid_a_rejected_player_reply(wired):
    port, directory = wired
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        await _send(writer, reader, HELLO_A)
        assert await _send(writer, reader, {"player": "unknown"}) == {"commands": [{"cmd": "noop"}]}
        rejected = _lines(directory, "rejected")[-1]
        reply = _lines(directory, "a")[-1]
        assert reply["req"] == {"sink": "rejected", "t": rejected["t"]}
        assert reply["conn"] == rejected["conn"]
    finally:
        await _close_socket(writer)


@pytest.mark.asyncio
async def test_rejected_first_valid_a_disconnect(wired):
    port, directory = wired
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        await _send(writer, reader, {"player": "unknown"})
        await _send(writer, reader, HELLO_A)
    finally:
        await _close_socket(writer)
    rejected, accepted = _lines(directory, "rejected"), _lines(directory, "a")
    assert rejected[0]["msg"] == {"event": "_connect"}
    assert accepted[-1]["msg"] == {"event": "_disconnect"}
    assert {r["conn"] for r in rejected + accepted} == {rejected[0]["conn"]}
    assert sum(r["dir"] == "meta" for r in rejected + accepted) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["write", "guard"])
async def test_failed_c2s_write_successful_reply_has_req_null(wired, monkeypatch, failure):
    from server.server import _WireTap

    port, directory = wired
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        await _send(writer, reader, HELLO_A)
        original = _WireTap._emit

        class BrokenHandle:
            def write(self, data):
                raise OSError("injected c2s write failure")

        def fail_request(self, key, direction, **kwargs):
            if direction == "c2s":
                if failure == "guard":
                    raise OSError("injected c2s failure")
                entry = self._files[key]
                handle = entry["handle"]
                entry["handle"] = BrokenHandle()
                try:
                    return original(self, key, direction, **kwargs)
                finally:
                    entry["handle"] = handle
            return original(self, key, direction, **kwargs)

        monkeypatch.setattr(_WireTap, "_emit", fail_request)
        assert "commands" in await _send(writer, reader, HELLO_A)
        assert _lines(directory, "a")[-1]["req"] is None
    finally:
        await _close_socket(writer)


@pytest.mark.asyncio
@pytest.mark.parametrize("msg", [{"player": []}, {"player": {}}, {}, {"player": None}, [], 42, None])
async def test_list_dict_players_and_non_object_json_through_handle_client(wired, msg):
    port, directory = wired
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        reply = await _send(writer, reader, msg)
        if not isinstance(msg, dict) or not isinstance(msg.get("player", ""), str):
            assert any(c.get("refused") == "malformed" for c in reply["commands"])
        rows = _lines(directory, "rejected")
        assert rows[1]["dir"] == "c2s"
        assert rows[1]["msg"] == (msg if isinstance(msg, dict) else None)
        if not isinstance(msg, dict):
            assert rows[1]["raw"] == json.dumps(msg)
        assert rows[2]["req"] == rows[1]["t"]
        assert "commands" in await _send(writer, reader, HELLO_A)
    finally:
        await _close_socket(writer)


@pytest.mark.asyncio
async def test_overlapping_same_player_interleaved_requests_reversed_close(wired):
    port, directory = wired
    reader1, writer1 = await asyncio.open_connection("127.0.0.1", port)
    reader2, writer2 = await asyncio.open_connection("127.0.0.1", port)
    try:
        for reader, writer in [(reader1, writer1), (reader2, writer2),
                               (reader1, writer1), (reader2, writer2)]:
            await _send(writer, reader, HELLO_A)
    finally:
        await _close_socket(writer2)
        await _close_socket(writer1)
    rows = _lines(directory, "a")
    connects = [r["conn"] for r in rows if r["msg"] == {"event": "_connect"}]
    disconnects = [r["conn"] for r in rows if r["msg"] == {"event": "_disconnect"}]
    assert len(set(connects)) == 2
    assert disconnects == connects[::-1]
    assert [r["conn"] for r in rows if r["dir"] == "c2s"] == connects * 2
    assert [r["t"] for r in rows] == list(range(1, len(rows) + 1))
    for row in rows:
        if row["dir"] == "s2c":
            request = rows[row["req"] - 1]
            assert request["dir"] == "c2s"
            assert request["conn"] == row["conn"]


@pytest.mark.asyncio
async def test_disconnect_write_failure_still_closes_writer(wired, monkeypatch):
    from server.server import _WireTap

    original = _WireTap._emit
    closed = asyncio.Event()

    def fail_disconnect(self, key, direction, **kwargs):
        if kwargs.get("msg") == {"event": "_disconnect"}:
            # Observe the real server-side transport after close() returns.
            asyncio.get_running_loop().call_soon(check_closed, self._writer)
            raise OSError("injected disconnect failure")
        return original(self, key, direction, **kwargs)

    def check_closed(writer):
        if writer.is_closing():
            closed.set()

    monkeypatch.setattr(_WireTap, "_emit", fail_disconnect)
    port, _ = wired
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        await _send(writer, reader, HELLO_A)
    finally:
        await _close_socket(writer)
    await asyncio.wait_for(closed.wait(), 3)


@pytest.mark.asyncio
@pytest.mark.parametrize("line", ["é" * 800, "€" * 800, "😀" * 800])
async def test_multibyte_raw_truncation_at_most_1024_bytes(wired, line):
    port, directory = wired
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        assert await _raw_send(writer, reader, line) == {"commands": [{"cmd": "noop"}]}
        raw = _lines(directory, "rejected")[1]["raw"]
        assert 1021 <= len(raw.encode("utf-8")) <= 1024
        assert line.startswith(raw)
    finally:
        await _close_socket(writer)


@pytest.mark.asyncio
@pytest.mark.parametrize("message", [42, None, "a", [1], {"event": "tick", "player": 42}])
async def test_non_object_json_through_handle_client_without_the_wire_tap(tmp_path, message):
    from tests.unit.test_mixed_foundations import _hello
    from tests.unit.test_server_run_reconnect import _send as send, _tcp
    srv = SLinkServer(data_dir=str(tmp_path), retry_same_species=False)
    assert srv._wire_log is None
    async with _tcp(srv) as (connect, _):
        socket = await connect()
        malformed = await send(socket, message)
        assert any(c.get("refused") == "malformed" for c in malformed["commands"])
        reply = await send(socket, _hello("a", {"rom_type": "red"}))
        assert "commands" in reply and srv.is_admitted("a")
        await send(socket, {"event": "no_catch", "player": "a", "area_id": "route_1"})
        assert srv.state.area_states["route_1"].value == "dead_zone"
