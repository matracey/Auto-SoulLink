"""CLI contract for the opt-in wild-battle faint exemption."""

import subprocess
import sys

from server.state import SoulLinkState


def test_help_mentions_ignore_wild_faints():
    result = subprocess.run(
        [sys.executable, "-m", "server.server", "--help"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert "--ignore-wild-faints" in result.stdout


def test_state_defaults_off_and_accepts_opt_in():
    assert SoulLinkState().ignore_wild_faints is False
    assert SoulLinkState(ignore_wild_faints=True).ignore_wild_faints is True


def test_rule_persists_across_reload(tmp_path):
    state = SoulLinkState(data_dir=str(tmp_path), ignore_wild_faints=True)
    state._save()

    restored = SoulLinkState.load(
        data_dir=str(tmp_path),
        ignore_wild_faints=False,
    )

    assert restored.ignore_wild_faints is True
