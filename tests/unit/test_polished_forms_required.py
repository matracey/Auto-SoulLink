"""Missing or malformed form identity facts must refuse before Polished construction."""
import json

import pytest

from server.adapters import gen2_polished as polished, polished_codec as codec


@pytest.fixture
def isolated_index(tmp_path, monkeypatch):
    path = tmp_path / "forms_index.json"
    monkeypatch.setattr(codec, "FORMS_INDEX", path)
    yield path


@pytest.mark.parametrize("consumer", [codec.variant_records, polished.Gen2PolishedAdapter],
                         ids=["codec", "adapter"])
def test_missing_forms_index_is_a_named_refusal(isolated_index, consumer):
    with pytest.raises(ValueError, match="required Polished forms index"):
        consumer()


@pytest.mark.parametrize("damage", ["schema", "duplicate_record", "duplicate_form", "record_bool", "types"],
                         ids=["schema", "duplicate-record", "duplicate-form", "record-bool", "types"])
def test_present_invalid_index_is_refused(isolated_index, damage):
    data = json.loads((codec.ROOT / "data/games/polished_crystal/forms_index.json").read_text())
    rows = data["variant_forms"]
    if damage == "schema":
        data["schema"] = "unknown"
    elif damage == "duplicate_record":
        rows[1]["record_index"] = rows[0]["record_index"]
    elif damage == "duplicate_form":
        rows[1]["species_id"], rows[1]["form_id"] = rows[0]["species_id"], rows[0]["form_id"]
    elif damage == "record_bool":
        rows[0]["record_index"] = True
    else:
        rows[0]["types"] = [False, 0]
    isolated_index.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="invalid Polished forms index"):
        codec.variant_records()



def test_cached_identity_facts_cannot_hide_a_removed_required_index(isolated_index):
    data = (codec.ROOT / "data/games/polished_crystal/forms_index.json").read_bytes()
    isolated_index.write_bytes(data)
    assert codec.variant_records()
    isolated_index.unlink()
    with pytest.raises(ValueError, match="required Polished forms index"):
        codec.variant_records()
