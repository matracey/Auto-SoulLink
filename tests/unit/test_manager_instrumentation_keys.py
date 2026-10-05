from server import manager


def test_instrumentation_keys_are_generated_once():
    run = {"run_id": "test"}

    assert manager._ensure_instrumentation_keys(run) is True
    original = dict(run["instrumentation_keys"])
    assert set(original) == {"a", "b"}
    assert all(len(value) >= 32 for value in original.values())

    assert manager._ensure_instrumentation_keys(run) is False
    assert run["instrumentation_keys"] == original


def test_missing_player_key_is_backfilled_without_rotating_other_player():
    run = {"run_id": "test", "instrumentation_keys": {"a": "keep-a"}}

    assert manager._ensure_instrumentation_keys(run) is True
    assert run["instrumentation_keys"]["a"] == "keep-a"
    assert len(run["instrumentation_keys"]["b"]) >= 32


def test_public_run_never_exposes_instrumentation_keys():
    run = {
        "run_id": "test",
        "name": "Test",
        "instrumentation_keys": {"a": "a-key", "b": "b-key"},
    }

    public = manager._public_run(run)

    assert public == {"run_id": "test", "name": "Test"}
    assert run["instrumentation_keys"]["a"] == "a-key"


def test_reconcile_backfills_old_registry_entry(monkeypatch, tmp_path):
    monkeypatch.setattr(manager, "MANAGER_DIR", str(tmp_path))
    run = {"run_id": "old", "status": "stopped", "pid": None}

    assert manager._reconcile([run]) is True
    assert set(run["instrumentation_keys"]) == {"a", "b"}
