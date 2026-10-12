"""Item IDs are bound to the admitted cartridge's generated catalog."""

import json
import logging
from pathlib import Path

import pytest

from server.adapters.gen4_hgsspt import Gen4Adapter
from server.data.items import gen4

DATA = Path(__file__).resolve().parents[2] / "data/games"


@pytest.mark.parametrize("title,foundation", [
    ("heartgold", "gen4_hgss"), ("soulsilver", "gen4_hgss"),
    ("heartgold_hge", "gen4_hge"),
])
def test_every_item_uses_its_cartridge_catalog(title, foundation):
    items = json.loads((DATA / foundation / "names.json").read_text())["items"]
    adapter = Gen4Adapter(rom_type=title)
    for item_id, name in items.items():
        expected = (name if name.strip() else f"Item #{item_id}") if int(item_id) else ""
        assert adapter.item_name(int(item_id)) == expected
    assert adapter.item_name(999999) == "Item #999999"


@pytest.mark.parametrize("item_id,name", [
    (43, "Berry Juice"), (44, "Sacred Ash"), (54, "Old Gateau"),
    (55, "Guard Spec."), (234, "Leftovers"), (270, "Life Orb"),
])
def test_hgss_item_id_anchors(item_id, name):
    assert Gen4Adapter().item_name(item_id) == name


@pytest.mark.parametrize("items", [None, [], {}, {"wrong": "Potion"}, {"-1": "Potion"},
                                  {"01": "Potion"}, {"1": None},
                                  {"1" * 5000: "Potion"}, {"65536": "Potion"},
                                  {"١": "Potion"}])
def test_malformed_catalog_is_explicitly_refused(tmp_path, monkeypatch, caplog, items):
    path = tmp_path / "gen4_hgss"
    path.mkdir()
    (path / "names.json").write_text(json.dumps({"items": items}))
    monkeypatch.setattr(gen4, "_DATA", tmp_path)
    with caplog.at_level(logging.ERROR):
        assert gen4._load_item_names("gen4_hgss") == {}
    assert str(path / "names.json") in caplog.text


def test_blank_reserved_slots_do_not_invalidate_named_items(tmp_path, monkeypatch):
    path = tmp_path / "gen4_hge"
    path.mkdir()
    (path / "names.json").write_text(json.dumps({"items": {"1": "Master Ball", "1823": ""}}))
    monkeypatch.setattr(gen4, "_DATA", tmp_path)
    assert gen4._load_item_names("gen4_hge") == {1: "Master Ball"}


def test_missing_catalog_does_not_fall_back_to_another_title(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(gen4, "_DATA", tmp_path)
    with caplog.at_level(logging.ERROR):
        assert gen4._load_item_names("gen4_hge") == {}
    assert "gen4_hge" in caplog.text
