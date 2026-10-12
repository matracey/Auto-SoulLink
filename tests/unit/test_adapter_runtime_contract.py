"""Declared ROM-content admission policy: default inert, Gen 2 SHA1-bound."""
from server.adapters import get_adapter


def test_rom_contract_policy_is_declared_and_inert_by_default():
    assert get_adapter("gen3_frlge", rom_type="firered").rom_contract_by_sha1 is False
    assert get_adapter("gen1_rby", rom_type="red").rom_contract_by_sha1 is False


def test_gen2_rom_contracts_are_bound_by_sha1():
    assert get_adapter("gen2_gsc", rom_type="crystal").rom_contract_by_sha1 is True
    assert get_adapter("gen2_polished", rom_type="polished_crystal").rom_contract_by_sha1 is True



def test_polished_runtime_facts_live_in_the_game_pack():
    from server.adapters import polished_rom_scan as scan

    pack = scan.REPO / "data/games/polished_crystal"
    assert scan.INI.parent == pack
    assert scan.SCRIPT_SITES.parent == pack


def test_polished_companion_abi_is_read_from_the_game_pack(tmp_path, monkeypatch):
    import json

    from server.adapters import gen2_polished as polished

    pack = tmp_path / "data/games/polished_crystal"
    pack.mkdir(parents=True)
    (pack / "companion_abi.json").write_text(json.dumps({"abi_version": 7}))
    monkeypatch.setattr(polished, "_ROOT", tmp_path)
    polished._companion_abi.cache_clear()
    try:
        assert polished._companion_abi() == 7
    finally:
        polished._companion_abi.cache_clear()



def test_polished_rom_identity_is_read_from_the_game_pack(tmp_path, monkeypatch):
    import json

    from server.adapters import polished_rom_scan as scan

    pack = tmp_path / "data/games/polished_crystal"
    pack.mkdir(parents=True)
    pins = {"clean": "a" * 40, "overlay": "b" * 40}
    (pack / "companion_abi.json").write_text(json.dumps({"rom_sha1": pins}))
    monkeypatch.setattr(scan, "REPO", tmp_path)
    scan.pinned_sha1s.cache_clear()
    try:
        assert scan.pinned_sha1s() == {"a" * 40: "clean", "b" * 40: "overlay"}
    finally:
        scan.pinned_sha1s.cache_clear()



def test_polished_adapter_source_pin_uses_the_same_runtime_pack(tmp_path, monkeypatch):
    import json

    from server.adapters import gen2_polished as polished, polished_rom_scan as scan

    pack = tmp_path / "data/games/polished_crystal"
    pack.mkdir(parents=True)
    (pack / "companion_abi.json").write_text(json.dumps({"rom_sha1": {"clean": "a" * 40, "overlay": "b" * 40}}))
    monkeypatch.setattr(scan, "REPO", tmp_path)
    scan.pinned_sha1s.cache_clear()
    polished._rom_sha1.cache_clear()
    try:
        assert polished._rom_sha1() == "a" * 40
    finally:
        scan.pinned_sha1s.cache_clear()
        polished._rom_sha1.cache_clear()
