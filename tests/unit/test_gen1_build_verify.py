import hashlib
from unittest.mock import Mock

import pytest

from tests.unit.test_gen1_injector import _load


@pytest.fixture
def builder(tmp_path, monkeypatch):
    build = _load("build")
    monkeypatch.setattr(build, "REPO", str(tmp_path))
    monkeypatch.setattr(build, "BUILD", str(tmp_path / "build"))
    monkeypatch.setattr(build, "DIST", str(tmp_path / "dist"))
    monkeypatch.setattr(build, "PAYLOAD_FILE", str(tmp_path / "dist" / "slink_bank3f.bin"))
    monkeypatch.setattr(build, "title_spans", lambda *_: [])
    image = bytearray(build.INJECT_OFFSET + build.BANK_SIZE)
    image[build.HOOK_SITE:build.HOOK_SITE + len(build.HOOK_ORIGINAL)] = build.HOOK_ORIGINAL
    for offset, original, _, _ in build.MENU_PATCHES:
        image[offset:offset + len(original)] = original
    rows = {}
    for title in ("red", "blue"):
        name = f"{title}.gb"
        (tmp_path / name).write_bytes(image)
        rows[title] = name, hashlib.sha1(image).hexdigest()
    monkeypatch.setattr(build, "ROMS", rows)
    monkeypatch.setattr(build, "assemble", Mock(return_value=b"\x01" + bytes(build.BANK_SIZE - 1)))
    return build


@pytest.mark.parametrize("rom", [None, "red"])
def test_verify_only_checks_selected_base_roms_without_building(builder, tmp_path, monkeypatch, rom):
    args = ["build.py", "--verify-only"]
    if rom:
        args += ["--rom", rom]
    monkeypatch.setattr("sys.argv", args)
    checked = Mock(wraps=builder.patch_rom)
    monkeypatch.setattr(builder, "patch_rom", checked)
    assert builder.main() == 0
    assert [call.args[0] for call in checked.call_args_list] == ([rom] if rom else ["blue", "red"])
    builder.assemble.assert_not_called()
    assert not (tmp_path / "build").exists()
    assert not (tmp_path / "dist").exists()


@pytest.mark.parametrize("fault,reason", [
    ("missing", "ROM not found"), ("sha", "wrong dump"), ("hook", "hook site"),
    ("bank", "not empty"), ("menu", "not the dump"),
])
def test_verify_only_preserves_payload_even_when_base_validation_fails(
        builder, tmp_path, monkeypatch, fault, reason):
    payload = tmp_path / "dist" / "slink_bank3f.bin"
    payload.parent.mkdir()
    payload.write_bytes(b"published payload")
    rom = tmp_path / "red.gb"
    if fault == "missing":
        rom.unlink()
    else:
        data = bytearray(rom.read_bytes())
        offset = {"sha": 0, "hook": builder.HOOK_SITE, "bank": builder.INJECT_OFFSET,
                  "menu": builder.MENU_PATCHES[0][0]}[fault]
        data[offset] ^= 0xFF
        rom.write_bytes(data)
        if fault != "sha":
            builder.ROMS["red"] = rom.name, hashlib.sha1(data).hexdigest()
    monkeypatch.setattr("sys.argv", ["build.py", "--verify-only", "--rom", "red"])
    with pytest.raises(SystemExit, match=reason):
        builder.main()
    builder.assemble.assert_not_called()
    assert payload.read_bytes() == b"published payload"
    assert not (tmp_path / "build").exists()


def test_normal_build_still_assembles_and_publishes(builder, tmp_path, monkeypatch):
    monkeypatch.setattr("sys.argv", ["build.py", "--rom", "red"])
    assert builder.main() == 0
    builder.assemble.assert_called_once()
    assert (tmp_path / "dist" / "slink_bank3f.bin").read_bytes() == b"\x01"
    assert (tmp_path / "build" / "slink_red.gb").is_file()
