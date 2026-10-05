import json
import subprocess
import sys

from server.state import SoulLinkState


def test_help_mentions_legacy_dead_zone_switch():
    result = subprocess.run(
        [sys.executable, "-m", "server.server", "--help"],
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0
    assert "--dead-zone-on-miss" in result.stdout


def test_retry_same_species_defaults_on():
    assert SoulLinkState().retry_same_species is True
    assert SoulLinkState(retry_same_species=False).retry_same_species is False


def test_retry_same_species_round_trips(tmp_path):
    state = SoulLinkState(data_dir=str(tmp_path), retry_same_species=False)
    state._save()

    saved = json.loads((tmp_path / "links.json").read_text())
    assert saved["rules"]["retry_same_species"] is False
    assert SoulLinkState.load(data_dir=str(tmp_path)).retry_same_species is False


def test_old_save_uses_default_on(tmp_path):
    state = SoulLinkState(data_dir=str(tmp_path))
    state._save()
    path = tmp_path / "links.json"
    saved = json.loads(path.read_text())
    saved["rules"].pop("retry_same_species")
    path.write_text(json.dumps(saved))

    assert SoulLinkState.load(data_dir=str(tmp_path)).retry_same_species is True
