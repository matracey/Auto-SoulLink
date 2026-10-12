"""tools/gen1_board_snapshot.py offline paths: it must fail cleanly (a named
BOARD_ERROR / argparse error, exit 2) when there is no server and no saved
fixture, never a raw traceback -- and its --selftest replay must actually
produce the documented BOARD_ASSERT lines on a minimal known-good fixture
(a positive control: a script that always prints "everything false" without
crashing would pass a naive smoke test too).
"""
from __future__ import annotations

import json
import subprocess
import sys
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import gen1_board_snapshot

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "gen1_board_snapshot.py"


def _run(*args, cwd=None):
    return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=cwd or ROOT,
                           capture_output=True, text=True, check=False)


def test_no_args_fails_cleanly_with_a_named_reason():
    result = _run()
    assert result.returncode == 2
    assert "Traceback" not in result.stderr
    assert "capture requires --http-port" in result.stderr


def test_http_port_with_no_server_listening_fails_cleanly(tmp_path):
    # Port 1 (TCP port assignment) is never a listening HTTP server in CI or dev --
    # no live-port guessing needed to prove the connection is refused.
    result = _run("--http-port", "1", "--out", str(tmp_path), "--label", "no-server")
    assert result.returncode == 2
    assert "Traceback" not in result.stderr
    assert "BOARD_ERROR" in result.stderr


def test_selftest_missing_files_fails_cleanly():
    result = _run("--selftest")
    assert result.returncode == 2
    assert "Traceback" not in result.stderr
    assert "--selftest requires --html FILE --status FILE" in result.stderr


@pytest.mark.parametrize("required,code", [(None, 0), ("phase_label", 0), ("paired_sprites", 1)])
def test_selftest_replay_on_minimal_fixture_produces_board_assert_lines(tmp_path, required, code):
    # Positive control (feedback_verify_the_probe_first.md): prove the replay path can
    # produce a true assertion at all, not just false-for-everything without crashing.
    html = tmp_path / "index.html"
    status = tmp_path / "status.json"
    html.write_text(
        '<div id="content" hx-get="/">'
        '<span class="phase-label">Waiting for Pokéballs</span></div>',
        encoding="utf-8",
    )
    status.write_text(json.dumps({"players": {"a": {}, "b": {}}}), encoding="utf-8")

    extra = ["--require", required] if required else []
    result = _run("--selftest", "--html", str(html), "--status", str(status), *extra)

    assert result.returncode == code, result.stdout + result.stderr
    assert "Traceback" not in result.stderr
    assert 'BOARD_ASSERT phase_label ok=true' in result.stdout
    # Everything else is legitimately absent from this minimal fixture.
    assert 'BOARD_ASSERT paired_sprites ok=false' in result.stdout


@pytest.mark.parametrize("root_ok,poll_ok,required,code", [
    (True, True, True, 0),
    (True, False, True, 1),
    (False, True, True, 1),
    (False, False, True, 1),
    (False, False, False, 0),
])
def test_capture_requires_each_response_to_pass(tmp_path, monkeypatch, capsys, root_ok, poll_ok, required, code):
    def html(ok):
        label = '<span class="phase-label">Waiting for Pokéballs</span>' if ok else ""
        return f'<div id="content" hx-get="/poll">{label}</div>'.encode()

    bodies = iter([html(root_ok), b'{"players":{"a":{},"b":{}}}', html(poll_ok)])
    requests = []

    def open_response(request, timeout):
        requests.append(request)
        return BytesIO(next(bodies))

    monkeypatch.setattr(gen1_board_snapshot, "build_opener", lambda *args: SimpleNamespace(open=open_response))
    args = [str(SCRIPT), "--http-port", "8080", "--out", str(tmp_path)]
    monkeypatch.setattr(sys, "argv", args + (["--require", "phase_label"] if required else []))

    assert gen1_board_snapshot.main() == code
    output = capsys.readouterr().out
    assert f"BOARD_ASSERT phase_label ok={str(root_ok).lower()}" in output
    assert f"BOARD_POLL_ASSERT phase_label ok={str(poll_ok).lower()}" in output
    assert len(requests) == 3
    assert requests[-1].full_url == "http://127.0.0.1:8080/poll"
    assert {key.lower(): value for key, value in requests[-1].header_items()}["hx-request"] == "true"
    receipt, = tmp_path.glob("board-*")
    assert (receipt / "index.html").read_bytes() == html(root_ok)
    assert (receipt / "poll.html").read_bytes() == html(poll_ok)
    assert "BOARD_POLL_ASSERT" in (receipt / "assertions.txt").read_text()
