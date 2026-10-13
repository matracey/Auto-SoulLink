"""Compiled expansion wild-table readback, separate from source projection and live checks."""

import json

import pytest

from tools import gen_gen3_profile as profile


ROOT = profile.REPO
OUT = ROOT / "data/games/gen3_exp/28877d73/expansion_encounters_rom_receipt.json"


@pytest.fixture(scope="module")
def pinned_inputs():
    source = ROOT / ".cache/expansion-src"
    artifacts = ROOT / ".cache/expansion-output/reference"
    if not source.is_dir() or any(not (artifacts / name).is_file()
                                  for name in ("pokeemerald.gba", "pokeemerald.sym", "pokeemerald.map")):
        pytest.skip("local pinned expansion source or copyrighted build artifacts absent")
    return profile.expansion_inputs(artifacts=artifacts), source


def test_compiled_wild_table_matches_source_set_zero_and_keeps_duplicate_headers(pinned_inputs):
    from tools import verify_gen3_exp_wild as wild

    receipt = json.loads(OUT.read_text(encoding="utf-8"))
    assert wild.build() == receipt
    assert receipt["evidence"] == "COMPILED_ROM_SOURCE_READBACK"
    assert receipt["header_count"] == 124
    assert receipt["terminator_count"] == 1
    assert receipt["hidden_pointer_count"] == 0
    assert receipt["altering_cave_header_count"] == 9
    assert receipt["source_projection_set"] == 0
    assert receipt["source_projection_match"] is True
    assert receipt["live_verified"] is False


def test_source_pack_hash_does_not_depend_on_the_checkout_eol(tmp_path):
    # The receipt once pinned the CRLF working copy's hash, so it only matched a CRLF checkout.
    from tools import verify_gen3_exp_wild as wild

    lf = tmp_path / "lf.json"
    crlf = tmp_path / "crlf.json"
    lf.write_bytes(b'{"encounters": {}}\n')
    crlf.write_bytes(b'{"encounters": {}}\r\n')
    assert wild.pack_sha256(lf) == wild.pack_sha256(crlf)


def test_receipt_binds_the_committed_source_pack():
    from tools import verify_gen3_exp_wild as wild

    receipt = json.loads(OUT.read_text(encoding="utf-8"))
    assert receipt["source_pack_sha256"] == wild.pack_sha256(wild.SOURCE_PACK)


def test_compiled_wild_table_refuses_corrupt_descriptor_pointer(pinned_inputs):
    from tools import verify_gen3_exp_wild as wild

    context, source = pinned_inputs
    changed = bytearray(context["rom"])
    first_header = profile.expansion_symbol(context, "gWildMonHeaders")["address"] - 0x08000000
    changed[first_header + 4:first_header + 8] = (0x02000000).to_bytes(4, "little")
    with pytest.raises(ValueError, match="pointer|descriptor"):
        wild.readback(bytes(changed), context, source)


def test_compiled_wild_table_refuses_hidden_fifth_pointer(pinned_inputs):
    from tools import verify_gen3_exp_wild as wild

    context, source = pinned_inputs
    changed = bytearray(context["rom"])
    first_header = profile.expansion_symbol(context, "gWildMonHeaders")["address"] - 0x08000000
    changed[first_header + 20:first_header + 24] = (0x08DEA538).to_bytes(4, "little")
    with pytest.raises(ValueError, match="hidden|fifth"):
        wild.readback(bytes(changed), context, source)
