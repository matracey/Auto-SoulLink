"""Companion manifest publication across independent builder processes."""

import json
import multiprocessing
import os
import stat

import pytest

from patch.tools import companion_pins as pins


@pytest.fixture
def manifest(tmp_path):
    directory = tmp_path / "publication"
    directory.mkdir()
    path = directory / "pins.json"
    path.write_text(json.dumps({
        "schema": pins.SCHEMA,
        "pins": {"rr": {"existing": 1}, "rb-red": {"stable": 2}},
    }))
    return path


def _update_worker(path, slug, fields, started, loaded, release):
    original_load = pins.load

    def paused_load(path):
        doc = original_load(path)
        loaded.set()
        if release is not None and not release.wait(10):
            raise TimeoutError("test did not release the manifest reader")
        return doc

    pins.load = paused_load
    started.set()
    pins.update(slug, fields, path)


@pytest.mark.parametrize("second_slug", ["rr", "rb-red"])
def test_competing_builders_preserve_both_updates(manifest, second_slug):
    ctx = multiprocessing.get_context("spawn")
    first_started, first_loaded, release = ctx.Event(), ctx.Event(), ctx.Event()
    second_started, second_loaded = ctx.Event(), ctx.Event()
    first = ctx.Process(target=_update_worker, args=(
        manifest, "rr", {"first": 10}, first_started, first_loaded, release,
    ))
    second = ctx.Process(target=_update_worker, args=(
        manifest, second_slug, {"second": 20}, second_started, second_loaded, None,
    ))
    started = []
    try:
        first.start()
        started.append(first)
        assert first_loaded.wait(10)
        second.start()
        started.append(second)
        assert second_started.wait(10)
        entered_while_locked = second_loaded.wait(0.5)
    finally:
        release.set()
        for process in started:
            process.join(10)
            if process.is_alive():
                process.terminate()
                process.join(10)
    assert all(process.exitcode == 0 for process in started)
    assert not entered_while_locked
    entries = pins.load(manifest)["pins"]
    assert entries["rr"] == {"existing": 1, "first": 10, **(
        {"second": 20} if second_slug == "rr" else {}
    )}
    assert entries["rb-red"] == {"stable": 2, **(
        {"second": 20} if second_slug == "rb-red" else {}
    )}


def test_failed_publication_preserves_manifest_and_releases_lock(manifest, monkeypatch):
    before = manifest.read_bytes()

    def refuse_replace(source, target):
        raise OSError("publication refused")

    with monkeypatch.context() as patch:
        patch.setattr(pins.os, "replace", refuse_replace)
        with pytest.raises(OSError, match="publication refused"):
            pins.update("rr", {"first": 10}, manifest)
    assert manifest.read_bytes() == before
    assert set(manifest.parent.iterdir()) <= {manifest, manifest.with_suffix(".json.lock")}
    assert pins.update("rr", {"second": 20}, manifest) == {"existing": 1, "second": 20}


def test_publication_preserves_file_mode(manifest):
    if os.name != "nt":
        manifest.chmod(0o640)
    before = stat.S_IMODE(manifest.stat().st_mode)
    assert pins.update("new", {"version": "dev"}, manifest) == {"version": "dev"}
    assert stat.S_IMODE(manifest.stat().st_mode) == before


def test_staging_file_is_owned_by_one_publication(manifest):
    other = manifest.with_suffix(".json.tmp")
    other.write_bytes(b"another builder's staging data")
    pins.update("rr", {"version": "dev"}, manifest)
    assert other.read_bytes() == b"another builder's staging data"


@pytest.mark.parametrize("failure", ["load", "serialization"])
def test_transaction_error_releases_lock_and_preserves_manifest(manifest, monkeypatch, failure):
    before = manifest.read_bytes()

    def refuse_load(path):
        raise ValueError("bad manifest")

    with monkeypatch.context() as patch:
        if failure == "load":
            patch.setattr(pins, "load", refuse_load)
        with pytest.raises(ValueError if failure == "load" else TypeError):
            pins.update("rr", {"invalid": object()}, manifest)
    assert manifest.read_bytes() == before
    assert set(manifest.parent.iterdir()) <= {manifest, manifest.with_suffix(".json.lock")}
    assert pins.update("rr", {"version": "dev"}, manifest) == {"existing": 1, "version": "dev"}
