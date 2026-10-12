import pytest

from tests.unit.test_gen3_trade_server import _applying, _mon


@pytest.mark.parametrize("title", ["firered", "leafgreen", "emerald", "firered_rr"])
@pytest.mark.parametrize("player", ["a", "b"])
@pytest.mark.parametrize("prepared", [False, True])
def test_changed_offered_record_is_not_sent_as_an_old_copy(tmp_path, title, player, prepared):
    srv, entry, token = _applying(tmp_path, before_confirm=True, title=title)
    commands = []
    if prepared:
        commands += srv._dispatch("b", {"event": "menu_result", "token": token, "choice": 1})
    current = _mon(player, hp=19)
    blob = bytearray.fromhex(current["blob_hex"])
    blob[0x56] = 19
    current["blob_hex"] = blob.hex()
    srv._dispatch(player, {"event": "tick", "party": [current]})
    if not prepared:
        commands += srv._dispatch("b", {"event": "menu_result", "token": token, "choice": 1})
    for pid in ("a", "b"):
        commands += srv._dispatch(pid, {"event": "apply_ready", "token": token, "ok": True})
    commands += [cmd for queued in srv.state.queued_commands.values() for cmd in queued]
    assert srv.state.pending_trade is None
    assert not any(cmd["cmd"] == "apply_trade" for cmd in commands)
    assert entry.status.value == "alive"


def test_unchanged_offered_records_still_reach_application(tmp_path):
    srv, _entry, _token = _applying(tmp_path, title="firered_rr")
    assert srv.state.pending_trade["phase"] == "applying"
