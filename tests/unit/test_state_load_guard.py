import builtins

import pytest

from server.state import SoulLinkState


def test_missing_state_starts_a_writable_run(tmp_path):
    state = SoulLinkState.load(data_dir=str(tmp_path))
    assert not state.load_failed
    state._save()
    assert (tmp_path / "links.json").is_file()


def test_transient_read_failure_cannot_overwrite_saved_run(tmp_path, monkeypatch):
    state = SoulLinkState(data_dir=str(tmp_path))
    state.trainer_names["a"] = "RED"
    state._save()
    path = tmp_path / "links.json"
    original = path.read_bytes()
    real_open = builtins.open
    failed = False

    def unavailable_once(file, *args, **kwargs):
        nonlocal failed
        if str(file) == str(path) and not failed:
            failed = True
            raise PermissionError("temporary share outage")
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", unavailable_once)
    loaded = SoulLinkState.load(data_dir=str(tmp_path))
    assert loaded.load_failed
    assert loaded.handle_event("a", {"event": "hello", "party": []}) == [
        {"cmd": "noop", "refused": "load_failed"}]
    with pytest.raises(RuntimeError, match="Refusing to overwrite"):
        loaded._save()
    assert path.read_bytes() == original
    assert SoulLinkState.load(data_dir=str(tmp_path)).trainer_names["a"] == "RED"


@pytest.mark.parametrize("content", ["{broken", "[]"])
def test_malformed_state_stays_read_only(tmp_path, content):
    path = tmp_path / "links.json"
    path.write_text(content)
    state = SoulLinkState.load(data_dir=str(tmp_path))
    assert state.load_failed
    with pytest.raises(RuntimeError, match="Refusing to overwrite"):
        state._save()
    assert path.read_text() == content
