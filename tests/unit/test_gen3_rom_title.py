import hashlib

import pytest

from server.adapters import gen3_frlge as adapter

TITLES = ("firered", "leafgreen", "emerald")


def _heads(title):
    regions = {}
    for name in adapter._CONTENT_HEADS:
        address, size = adapter._symbol(title, name)
        raw = bytearray(size)
        if name == "gWildMonHeaders":
            raw[0] = 0xFF
        regions[address] = bytes(raw)
    return regions


def _payload(regions):
    ordered = sorted(regions.items())
    return {
        "tables": [{"addr": address, "hex": raw.hex()} for address, raw in ordered],
        "fingerprint": hashlib.sha1(b"".join(raw for _, raw in ordered)).hexdigest(),
    }


@pytest.mark.parametrize("title", TITLES)
@pytest.mark.parametrize("head", adapter._CONTENT_HEADS)
@pytest.mark.parametrize("cut", ["first", "middle", "last"])
def test_adjacent_head_splits_preserve_title_and_transport_bytes(title, head, cut):
    regions = _heads(title)
    original = _payload(regions)
    assert adapter.rom_title(adapter.parse_rom_content(original)) == title
    address, size = adapter._symbol(title, head)
    raw = regions.pop(address)
    at = {"first": 1, "middle": size // 2, "last": size - 1}[cut]
    regions[address] = raw[:at]
    regions[address + at] = raw[at:]
    split = _payload(regions)
    assert split["fingerprint"] == original["fingerprint"]
    assert adapter.parse_rom_content(split) == regions
    assert adapter.rom_title(regions) == title


@pytest.mark.parametrize("title", TITLES)
@pytest.mark.parametrize("fault", ["gap", "overlap", "extra", "hash"])
def test_malformed_split_reports_still_fail_closed(title, fault):
    regions = _heads(title)
    address, size = adapter._symbol(title, "gSpeciesInfo")
    raw = regions.pop(address)
    middle = size // 2
    regions[address] = raw[:middle]
    offset = {"gap": 1, "overlap": -1}.get(fault, 0)
    regions[address + middle + offset] = raw[middle + offset:]
    if fault == "extra":
        regions[0x08000100] = b"\x00"
    payload = _payload(regions)
    if fault == "hash":
        payload["fingerprint"] = "0" * 40
    with pytest.raises(ValueError):
        adapter.parse_rom_content(payload)


def test_multiple_titles_remain_ambiguous_with_adjacent_regions():
    regions = {}
    for title in ("firered", "emerald"):
        for address, raw in _heads(title).items():
            middle = len(raw) // 2
            regions[address] = raw[:middle]
            regions[address + middle] = raw[middle:]
    with pytest.raises(ValueError, match="firered.*emerald"):
        adapter.rom_title(regions)
