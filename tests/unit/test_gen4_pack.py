"""Gen 4 pack generator controls (card C1-2).

Rule (tests/TESTING.md): an ABSENT input skips and names the artifact; a PRESENT-but-WRONG one
fails. The synthetic controls always run; the real-ROM/xMAP checks skip when an input is absent.
"""
from __future__ import annotations

import contextlib
import copy
import hashlib
import json
import os
import re
import struct
from pathlib import Path

import pytest

from tools import gen4_fixtures, gen4_pins, gen_gen4_area_map as amap, gen_gen4_pack as g

PACKS = {m: g.OUT[m] for m in ("hgss", "hge", "pt")}


def _need(*keys: str) -> g.Inputs:
    inputs = g.default_inputs()
    for key in keys:
        if not inputs.paths[key].is_file():
            pytest.skip(f"absent input {key}: {inputs.paths[key]}")
    return inputs


def _need_fork(mode: str) -> None:
    """The hge pack's profile.bag is read from the fork source; without it regeneration cannot match."""
    if mode == "hge":
        fork = gen4_pins.default_locations().sources["hg_engine_fork"]
        if not (fork / g.BAG_SOURCE["hge"][2]["struct"]).is_file():
            pytest.skip(gen4_pins.hge_fork_absent(fork))


def _pack(mode: str) -> dict:
    return json.loads(PACKS[mode].read_text(encoding="utf-8"))


# ---- synthetic world: tiny xMAP + tiny images --------------------------------------------------
XMAP = """\
# .main
#>02000000          SDK_STATIC_START (linker command file)
  02000000 00000020 .text   FuncArm\t(a.o)
  02000000 00000000 .text   $a\t(a.o)
  02000100 00000010 .text   FuncThumb\t(a.o)
  02000100 00000000 .text   $t\t(a.o)
  02000200 00000010 .text   FuncData\t(a.o)
  02000200 00000000 .text   $d\t(a.o)
#>02000400          SDK_STATIC_END (linker command file)

# .OVY_12
#>0000000C          SDK_OVERLAY_OVY_12_ID (linker command file)
#>02000800          SDK_OVERLAY.OVY_12.START (linker command file)
  02000820 00000010 .text   Faint\t(b.o)
  02000820 00000000 .text   $t\t(b.o)

# .OVY_57
#>00000039          SDK_OVERLAY_OVY_57_ID (linker command file)
#>02000800          SDK_OVERLAY.OVY_57.START (linker command file)
  02000820 00000010 .text   Impostor\t(c.o)
  02000820 00000000 .text   $t\t(c.o)
"""
ARM9 = bytearray(0x400)
ARM9[0x100:0x110] = bytes.fromhex("70b50d1c061c281c012107f067f9281c")
ARM9[0:4] = bytes.fromhex("70402de9")  # ARM, AL condition
OV12 = bytearray(0x100)
OV12[0x20:0x30] = bytes.fromhex("004b1847d1ed3c02012107f067f9281c")


def _images() -> g.Images:
    return g.Images(0x02000000, bytes(ARM9), {12: (0x02000800, bytes(OV12), 0), 57: (0x02000800, bytes(0x100), 0)})


def _site(sid="faint", sym="Faint", image="ov12", addr=0x02000820, mode="thumb", n=16, ovl=12, images=None):
    reg = (images or _images()).read(image, addr, n).hex()
    return {sid: {"symbol": sym, "address": addr, "image": image, "overlay_id": ovl, "extent": n,
                  "register_hex": reg, "fire_hex": g.fire_hex(reg), "mode": mode}}


def test_xmap_owner_is_the_section_not_the_address():
    xm = g.XMap(XMAP)
    assert xm.lookup("Faint").address == xm.lookup("Impostor").address == 0x02000820
    assert (xm.lookup("Faint").image, xm.lookup("Impostor").image) == ("ov12", "ov57")
    assert xm.lookup("FuncArm").image == "arm9" and xm.static_end == 0x02000400
    assert (xm.mode(xm.lookup("FuncArm")), xm.mode(xm.lookup("FuncThumb"))) == ("arm", "thumb")
    with pytest.raises(g.Fail, match="FuncData"):  # a $d (literal pool) label is not a code mode
        xm.mode(xm.lookup("FuncData"))
    with pytest.raises(g.Fail, match="Nope"):
        xm.lookup("Nope")


def test_images_read_by_declared_image_and_report_collisions():
    img = _images()
    assert img.read("ov12", 0x02000820, 4) == OV12[0x20:0x24]
    assert img.read("ov57", 0x02000820, 4) == bytes(4)  # same address, different overlay
    assert img.collisions("ov12", 0x02000820, 16) == ["ov57"]
    with pytest.raises(g.Fail, match="does not contain"):
        img.read("arm9", 0x02000820, 16)  # ARM9 static ends at 0x02000400
    with pytest.raises(g.Fail):
        img.read("ov12", 0x020008F8, 16)  # extent runs off the end of the image


def test_valid_site_set_is_green():
    sites = {**_site(), **_site("arm", "FuncArm", "arm9", 0x02000000, "arm", 4, None)}
    assert g.validate_sites(sites, _images(), "t") == []


def test_wrong_declared_image_goes_red():
    # an ov12 site resolved from ARM9: not even inside it
    bad = _site()
    bad["faint"]["image"], bad["faint"]["overlay_id"] = "arm9", None
    assert any("does not contain" in e for e in g.validate_sites(bad, _images(), "t"))
    # an ov12 site declared as the other overlay that shares the address: bytes differ
    bad = _site()
    bad["faint"]["image"], bad["faint"]["overlay_id"] = "ov57", 57
    assert any("differs from the declared image" in e for e in g.validate_sites(bad, _images(), "t"))
    # a zero-padded expanded ARM9 (the hge bug): address-first would read zeros and call it a pin
    big = g.Images(0x02000000, bytes(0x1000), {12: (0x02000800, bytes(OV12), 0)})
    assert g.validate_sites(_site("faint", images=big), big, "t") == []
    wrong = _site("faint", images=big)
    wrong["faint"]["image"], wrong["faint"]["overlay_id"] = "arm9", None
    assert any("differs from the declared image" in e for e in g.validate_sites(wrong, big, "t"))


@pytest.mark.parametrize("mutate,needle", [
    (lambda s: s.update(register_hex="ff" + s["register_hex"][2:]), "differs from the declared image"),
    (lambda s: s.update(fire_hex=s["fire_hex"][:6]), "fire_hex"),  # 3 bytes
    (lambda s: s.update(fire_hex=s["fire_hex"] + "00"), "fire_hex"),  # 5 bytes
    (lambda s: s.update(fire_hex="".join(reversed([s["fire_hex"][i:i + 2] for i in range(0, 8, 2)]))), "fire_hex"),
    (lambda s: s.update(extent=8), "extent"),
    (lambda s: s.update(mode="arm"), "ARM site"),  # Thumb bytes declared ARM
    (lambda s: s.update(mode="bogus"), "mode"),
    (lambda s: s.update(overlay_id=57), "overlay_id"),
])
def test_mutated_pin_or_fire_word_goes_red(mutate, needle):
    sites = _site()
    mutate(sites["faint"])
    errs = g.validate_sites(sites, _images(), "t")
    assert errs and any(needle in e for e in errs), errs


def test_fire_hex_is_the_little_endian_u32():
    assert g.fire_hex("70b50d1c061c281c") == "1c0db570"
    assert g.extent_for(2) == 4 and g.extent_for(0x100) == 16 and g.extent_for(8) == 8


def test_hge_redirect_decoding():
    # vanilla ov12 BtlCmd_TryFaintMon entry in the pinned hge build -> ov130 replacement (offsets.ini:91)
    r = g.decode_redirect(0x0223E22C, bytes.fromhex("004b1847d1ed3c02012107f067f9281c"))
    assert r == {"kind": "ldr_bx_trampoline", "target": 0x023CEDD0, "thumb": True}
    # 0x1C-byte stub that saves lr and BLs the replacement
    r = g.decode_redirect(0x02073CC0, bytes.fromhex("60b4044d76462e6060bc68f39df901490847") + bytes(10))
    assert r["kind"] == "bl_stub" and r["target"] == 0x023DC008
    assert g.decode_redirect(0x02000000, bytes.fromhex("70b50d1c061c281c") + bytes(8)) is None


# ---- HG vs SS ----------------------------------------------------------------------------------
def _hgss_pair():
    pack = _pack("hgss")
    return copy.deepcopy(pack["titles"]["heartgold"]), copy.deepcopy(pack["titles"]["soulsilver"])


def test_committed_hg_ss_agree_and_mismatches_go_red():
    hg, ss = _hgss_pair()
    assert g.validate_hg_ss(hg, ss) == []
    ss["symbols"]["Party_AddMon"]["address"] += 2  # a gameplay symbol moved in SS
    assert any("Party_AddMon" in e for e in g.validate_hg_ss(hg, ss))
    hg, ss = _hgss_pair()
    ss["symbols"]["BtlCmd_TryFaintMon"]["image"] = "ov13"
    assert any("BtlCmd_TryFaintMon" in e for e in g.validate_hg_ss(hg, ss))
    hg, ss = _hgss_pair()
    ss["sites"]["battle_faint_cmd"]["register_hex"] = "00" * 16
    assert any("battle_faint_cmd" in e for e in g.validate_hg_ss(hg, ss))
    hg, ss = _hgss_pair()  # the ov74 menu site may differ in bytes per title, but not in address
    ss["sites"]["menu_only_ov74"]["register_hex"] = "00" * 16
    assert g.validate_hg_ss(hg, ss) == []
    ss["sites"]["menu_only_ov74"]["address"] += 2
    assert any("menu_only_ov74" in e for e in g.validate_hg_ss(hg, ss))
    hg, ss = _hgss_pair()
    ss["overlays"]["12"]["ram"] += 0x20
    assert any("overlay 12" in e for e in g.validate_hg_ss(hg, ss))


# ---- committed packs: shape without any ROM ----------------------------------------------------
@pytest.mark.parametrize("mode", ["hgss", "hge", "pt"])
def test_committed_pack_shape(mode):
    pack = _pack(mode)
    assert pack["schema"] == "gen4-profile-v1" and pack["generator"] == "tools/gen_gen4_pack.py"
    assert re.fullmatch(r"[0-9a-f]{64}", pack["provenance"]["lock"]["sha256"])
    for title, t in pack["titles"].items():
        assert re.fullmatch(r"[0-9a-f]{40}", t["rom"]["sha1"]) and re.fullmatch(r"[0-9a-f]{32}", t["rom"]["md5"])
        assert t["overlay_table"]["entry_size"] == 8 and t["overlay_table"]["regions"] == 3
        for sid, s in t["sites"].items():
            reg = bytes.fromhex(s["register_hex"])
            assert s["extent"] == len(reg) >= 4, (title, sid)
            assert re.fullmatch(r"[0-9a-f]{8}", s["fire_hex"]) and s["fire_hex"] == g.fire_hex(s["register_hex"])
            assert s["phase"] in ("always", "battle", "pc", "field", "probe"), (title, sid)
            if s["overlay_id"] is not None:  # the declared overlay's RAM span holds the whole extent
                ov = t["overlays"][str(s["overlay_id"])]
                assert ov["ram"] <= s["address"] and s["address"] + s["extent"] <= ov["ram"] + ov["size"], (title, sid)
                assert s["image"] == f"ov{s['overlay_id']}"
        for name, sym in t["symbols"].items():
            assert sym["image"] in ("arm9", "arm9_itcm", "arm9_dtcm") or sym["image"] in {f"ov{i}" for i in t["overlays"]}, name
        for note in t["open"].values():
            assert isinstance(note, str) and note


def test_committed_hgss_required_contents():
    pack = _pack("hgss")
    for title in ("heartgold", "soulsilver"):
        t = pack["titles"][title]
        for name in ("sSaveDataPtr", "sFieldSysPtr", "sOverlayRegions", "gSystem", "HandleLoadOverlay", "UnloadOverlayByID",
                     "Main_RunOverlayManager", "OS_WaitIrq", "OS_Halt", "VBlankCB_DmaTasksFramecounter", "Task_Blackout",
                     "Encounter_GetResult", "Party_AddMon", "PCStorage_PlaceMonInBoxFirstEmptySlot",
                     "PCStorage_PlaceMonInFirstEmptySlotInAnyBox", "PCStorage_SwapMonsInBoxByIndexPair",
                     "PCStorage_DeleteBoxMonByIndexPair", "Save_WriteManFinish", "BtlCmd_TryFaintMon", "DoSoftReset",
                     "sRTCWork", "ov12_02238A68"):
            assert name in t["symbols"], name
        assert t["symbols"]["sSaveDataPtr"]["address"] == 0x021D2228
        assert t["symbols"]["BtlCmd_TryFaintMon"]["image"] == "ov12"
        assert t["sites"]["battle_faint_cmd"]["overlay_id"] == 12 and t["sites"]["menu_only_ov74"]["overlay_id"] == 74
        assert {t["sites"][k]["mode"] for k in ("per_frame_arm", "per_frame_thumb")} == {"arm", "thumb"}
        prof = t["profile"]
        assert prof["battle"]["mons_off"] == 0x2D40 and prof["battle"]["ability_off"] == 0x27
        assert (prof["boxes"], prof["mons_per_box"], prof["memorial_box"]) == (18, 30, 17)
        assert prof["save"]["array_headers_off"] == 0x23014 and prof["save"]["slot_specs_off"] == 0x232B4


def test_committed_hge_and_pt_open_fields_are_explicit():
    hge = _pack("hge")
    assert hge["artifact_status"] == "RECORDED_NOT_ADMITTED"
    t = hge["titles"]["heartgold_hge"]
    assert "hge_internal_overlay_loads" in t["open"] and "box_modified_flag_off" not in t["open"]  # measured, no longer open
    # resolved by the populated owner save: the party header is FILE-confirmed, the trainer is the vanilla layout
    po = t["profile"]["party_off"]
    assert (po["value"], po["max_off"], po["count_off"], po["mons_off"], po["array_id"]) == (0x90, 0, 4, 8, 2)
    assert "party_off" not in t["open"] and "trainer" not in t["open"] and "0xCAB4" in po["evidence"]["rejected_candidate"]
    assert t["profile"]["trainer"]["array_id"] == 1 and t["profile"]["trainer"]["identity"]["general_off_of_profile"] == 0x64
    assert (t["profile"]["boxes"], t["profile"]["memorial_box"], t["profile"]["pkm"]["exp_bits"]) == (30, 29, 21)
    assert (t["profile"]["battle"]["ability_off"], t["profile"]["battle"]["ability_width"]) == (0x7A, 2)
    assert t["sites"]["battle_faint_cmd"]["image"] == "ov130" and t["sites"]["battle_faint_cmd"]["address"] == 0x023CEDD0
    assert t["sites"]["load_overlay_entry"]["image"] == "ov129"
    assert "not a fresh build association" in hge["provenance"]["hge_build"]["note"]
    pt = _pack("pt")
    assert pt["artifact_status"] == "BIND_ONLY_NOT_ADMITTED"
    p = pt["titles"]["platinum"]
    assert p["symbols"]["sSaveDataPtr"]["address"] == 0x021C0794 and p["overlay_table"]["address"] == 0x021BF370
    assert p["profile"]["pc"]["modified_flag_off"] is None and p["profile"]["dirty"]["full_save_flag"]["off"] == 0x0C
    assert p["profile"]["memorial_box"] is None and "memorial_box" in p["open"] and p["sites"] == {}


# ---- identity: absent skips, present-but-wrong fails -------------------------------------------
def _rom_file(tmp_path, payload=b"x" * 64, header=b"IPKE"):
    path = tmp_path / "r.nds"
    data = bytearray(payload)
    data[12:16] = header
    path.write_bytes(data)
    return path, bytes(data)


def _lock_for(data: bytes, header="IPKE"):
    return {"artifacts": {"heartgold": {"sha1": hashlib.sha1(data).hexdigest(), "md5": hashlib.md5(data).hexdigest(),
                                         "sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data),
                                         "header_code": header}}}


def test_absent_input_is_a_named_skip(tmp_path):
    inputs = g.Inputs({"heartgold": tmp_path / "missing.nds"}, lock=tmp_path / "lock.json")
    with pytest.raises(g.Skip, match="heartgold"):
        g.verify_rom(inputs, {}, "heartgold")
    with pytest.raises(g.Skip, match="lock file"):
        g.load_lock(inputs)
    assert g.main(["hgss", "--path", f"heartgold={tmp_path / 'missing.nds'}", "--check"]) == g.EXIT_SKIP


def test_present_but_wrong_rom_fails(tmp_path):
    path, data = _rom_file(tmp_path)
    inputs = g.Inputs({"heartgold": path})
    assert g.verify_rom(inputs, _lock_for(data), "heartgold")["header_code"] == "IPKE"
    path.write_bytes(data[:30] + b"Z" + data[31:])  # one byte flipped
    os.utime(path, ns=(0, path.stat().st_mtime_ns + 1_000_000))  # the digest cache is keyed by (mtime, size): a same-tick rewrite would hit it
    with pytest.raises(g.Fail, match="sha1 mismatch"):
        g.verify_rom(inputs, _lock_for(data), "heartgold")
    path.write_bytes(data)
    os.utime(path, ns=(0, path.stat().st_mtime_ns + 2_000_000))
    with pytest.raises(g.Fail, match="header"):
        g.verify_rom(inputs, _lock_for(data, header="IPGE"), "heartgold")


def test_cli_present_wrong_rom_exits_1(tmp_path):
    wrong, _ = _rom_file(tmp_path)  # a present ROM whose hash is not the locked one (needs only the lock)
    assert g.main(["hgss", "--path", f"heartgold={wrong}"]) == g.EXIT_FAIL


# ---- real inputs: regenerate and compare --------------------------------------------------------
@pytest.mark.parametrize("mode,keys", [
    ("hgss", ("heartgold", "soulsilver", "heartgold_xmap", "soulsilver_xmap")),
    ("hge", ("heartgold", "heartgold_hge", "heartgold_xmap", "hge_offsets", "hge_rom_gen_ld", "hge_nm_all")),
    ("pt", ("platinum", "platinum_xmap")),
])
def test_regeneration_matches_committed_and_sites_resolve(mode, keys, tmp_path):
    inputs = _need(*keys)
    _need_fork(mode)
    text = g.generate(mode, inputs)
    assert text == PACKS[mode].read_text(encoding="utf-8"), f"{mode} pack drifted; run tools/gen_gen4_pack.py {mode}"
    # --check: clean copy exits 0; a mutated copy (drift) exits 1
    copy_path = tmp_path / "profile.json"
    copy_path.write_text(text, encoding="utf-8")
    assert g.main([mode, "--check", "--out", str(copy_path)]) == g.EXIT_OK
    pack = json.loads(text)
    first = next(iter(pack["titles"].values()))
    first["symbols"][next(iter(first["symbols"]))]["address"] += 4
    copy_path.write_text(g.render(pack), encoding="utf-8")
    assert g.main([mode, "--check", "--out", str(copy_path)]) == g.EXIT_FAIL
    copy_path.unlink()
    assert g.main([mode, "--check", "--out", str(copy_path)]) == g.EXIT_FAIL  # missing committed file is drift


def test_committed_sites_match_real_images():
    inputs = _need("heartgold", "soulsilver", "heartgold_hge")
    pack = _pack("hgss")
    for title in ("heartgold", "soulsilver"):
        assert g.validate_sites(pack["titles"][title]["sites"], g.load_images(inputs.paths[title]), title) == []
    hge = _pack("hge")["titles"]["heartgold_hge"]
    images = g.load_images(inputs.paths["heartgold_hge"], raw_arm9=True)
    assert g.validate_sites(hge["sites"], images, "hge") == []
    # control: the vanilla ov12 address read from hge's (expanded) ARM9 is not the declared image
    vanilla = pack["titles"]["heartgold"]["sites"]["battle_faint_cmd"]
    assert images.read("arm9", vanilla["address"], 16) != bytes.fromhex(vanilla["register_hex"])
    wrong = {"battle_faint_cmd": {**vanilla, "image": "arm9", "overlay_id": None}}
    assert g.validate_sites(wrong, images, "hge")


def test_xmap_static_end_and_overlay_starts_match_rom():
    inputs = _need("heartgold", "heartgold_xmap")
    xm, images = g.load_xmap(inputs.paths["heartgold_xmap"]), g.load_images(inputs.paths["heartgold"])
    g.check_xmap_vs_rom(xm, images, "heartgold")
    xm.overlay_start["ov12"] += 0x20  # control: a map that disagrees with the ROM table must fail
    try:
        with pytest.raises(g.Fail, match="START"):
            g.check_xmap_vs_rom(xm, images, "heartgold")
    finally:
        xm.overlay_start["ov12"] -= 0x20  # the parse is cached across tests


# ---- probe_field / system offsets for the C1-1 probe -------------------------------------------
PROBE_KEYS = {"sub", "save", "task", "live", "launched_app", "field_app", "paused", "save_driver", "save_state"}


def test_hgss_probe_field_values_types_and_evidence():
    for title in ("heartgold", "soulsilver"):
        prof = _pack("hgss")["titles"][title]["profile"]
        pf = prof["probe_field"]
        assert set(pf) >= PROBE_KEYS and all(isinstance(pf[k], int) and not isinstance(pf[k], bool) for k in PROBE_KEYS)
        assert pf == {**pf, "sub": 0x00, "save": 0x0C, "task": 0x10, "live": 0x6C, "launched_app": 0x04, "field_app": 0x00,
                      "paused": 0x08, "save_driver": 0xD8, "save_state": 0x01, "save_driver_data_off": 0x10}
        ev = prof["probe_field_evidence"]
        for key in pf:
            assert ev[key]["class"] in ("SOURCE", "ASM") and "pokeheartgold@" in ev[key]["cite"], key
        for key in ("save", "live", "save_driver", "save_state"):  # asm-corroborated
            assert ev[key]["class"] == "ASM" and "asm/overlay_01_" in ev[key]["cite"], key
        assert prof["system"]["vblank_counter_off"] == 0x2C
        off = prof["probe_wrong_write_offset"]
        assert isinstance(off, int) and 0x6D <= off <= 0x6F  # compiler padding after softResetDisabled @0x6C
        assert "padding" in prof["probe_wrong_write_offset_evidence"]


def test_hge_probe_fields_are_vanilla_projections_and_pt_stays_open():
    hge = _pack("hge")["titles"]["heartgold_hge"]
    prof = hge["profile"]
    pf, ev = prof["probe_field"], prof["probe_field_evidence"]
    assert pf == _pack("hgss")["titles"]["heartgold"]["profile"]["probe_field"]  # same offsets as vanilla, none left null
    assert prof["system"]["vblank_counter_off"] == 0x2C
    assert ev["save"]["class"] == ev["task"]["class"] == "SOURCE"  # the only two the fork header declares
    for key in PROBE_KEYS - {"save", "task"}:
        assert ev[key]["class"] == "SOURCE_PROJECTION" and "probe_field_hge_checks" in ev[key]["cite"], key
    checks = prof["probe_field_hge_checks"]
    assert checks["field_system_new"]["alloc_size"] == 0x128
    assert {"FieldSystem_LaunchApplication", "OverlayManager_Run", "ov01_021F68DC", "Battle_Run"} <= {
        f["symbol"] for f in checks["functions_byte_identical"]}
    assert "probe_field" in hge["open"]  # still a PHYSICAL cell
    pt = _pack("pt")["titles"]["platinum"]
    assert pt["profile"]["probe_field"] is None and "probe_field" in pt["open"]
    assert pt["profile"]["probe_wrong_write_offset"] is None


def test_hge_hidden_ability_is_bit_6_of_the_two_bit_field_not_bit_0():
    ha = _pack("hge")["titles"]["heartgold_hge"]["profile"]["pkm"]["hidden_ability"]
    assert (ha["block"], ha["byte_off"], ha["field_bits"], ha["bit"], ha["mask"]) == ("B", 0x19, [6, 7], 6, 0x40)
    assert ha["mask"] == 1 << ha["bit"] and "Leaf Crown" in ha["note"] and "FILE" in ha["evidence"]
    assert "hidden_ability" not in _pack("hgss")["titles"]["heartgold"]["profile"]["pkm"]


# ---- Part A data gaps: location / Pt party+trainer+footer / hge party+trainer ---------------------------
SAVES = gen4_fixtures.lane_root() / "saves"
SAVE_OF = {"hgss": ("hg_base_26310.SaveRAM", "hgss"), "hge": ("hge_a_OOO_630.SaveRAM", "hge"), "pt": ("pt_TTT_44361.SaveRAM", "pt")}


def _general(mode: str) -> bytes:
    name, variant = SAVE_OF[mode]
    if not (SAVES / name).is_file():
        pytest.skip(f"absent input save: {SAVES / name}")
    from server.adapters.gen4_codec import parse_save
    return parse_save((SAVES / name).read_bytes(), variant).general


def _profile(mode: str) -> dict:
    title = {"hgss": "heartgold", "hge": "heartgold_hge", "pt": "platinum"}[mode]
    return _pack(mode)["titles"][title]["profile"]


@pytest.mark.parametrize("mode", ["hgss", "hge", "pt"])
def test_location_offsets_match_the_20_byte_location_struct(mode):
    prof = _profile(mode)
    loc = prof["location"]
    offs = [loc[k] for k in ("map_off", "warp_off", "x_off", "y_off", "dir_off")]
    assert loc["struct_size"] == 20 == 5 * 4 and offs == [0, 4, 8, 12, 16]  # 5 x s32, packed, in struct order
    assert all(o % 4 == 0 and o + 4 <= loc["struct_size"] for o in offs) and len(set(offs)) == 5
    ids = prof["save"]["array_ids"]
    assert loc["array_id"] == ids["field_overworld_state" if mode == "pt" else "local_field_data"] == (11 if mode == "pt" else 5)
    assert loc["evidence"].startswith("SOURCE") and loc["file_cross_check"]["general_off_of_array"] > 0
    assert (loc.get("y_field") == "z") == (mode == "pt")


@pytest.mark.parametrize("mode", ["hgss", "hge", "pt"])
def test_pack_offsets_decode_the_owner_saves(mode):
    """FILE: the pack's own numbers applied to the real saves give the known party header, identity and a real Location."""
    prof, general = _profile(mode), _general(mode)
    po = prof["party_off"]
    assert struct.unpack_from("<II", general, po["value"]) == (6, 1) and (po["max_off"], po["count_off"], po["mons_off"]) == (0, 4, 8)
    tr, loc = prof["trainer"], prof["location"]
    base = tr["identity"]["general_off_of_profile"]
    assert base == tr["identity"]["id_general_off"] - tr["id_off"] == (0x68 if mode == "pt" else 0x64)
    assert struct.unpack_from("<I", general, base + tr["id_off"])[0] & 0xFFFF == {"hgss": 26310, "hge": 630, "pt": 44361}[mode]
    assert general[base + tr["version_off"]] == {"hgss": 7, "hge": 7, "pt": 12}[mode]
    map_id, warp, x, y, d = struct.unpack_from("<5i", general, loc["file_cross_check"]["general_off_of_array"])
    assert 0 < map_id < 1000 and -1 <= warp < 50 and 0 < x < 3000 and 0 < y < 3000 and -1 <= d <= 3


def test_pt_footer_formula_matches_the_real_general_block_footer():
    sv = _profile("pt")["save"]
    ft, bi = sv["footer"], sv["block_info"]
    assert (ft["size"], ft["signature"], ft["block_count"]) == (0x14, 0x20060623, 2) and bi["entry_size"] == 0xC
    assert bi["table_off"] == sv["table_off"] + sv["entry_count"] * sv["entry_size"] == 0x20284
    assert "blockInfo[b].offset + blockInfo[b].size - size" in ft["addr"] and "block_id == b" in ft["valid_when"]
    general = _general("pt")  # block 0: offset 0, size == the general length, footer in its last 0x14 bytes
    foot, f = general[len(general) - ft["size"]:], ft["fields"]
    assert struct.unpack_from("<I", foot, f["signature"])[0] == ft["signature"]
    assert struct.unpack_from("<I", foot, f["size"])[0] == len(general) and foot[f["block_id"]] == ft["blocks"]["normal"]


# ---- G1 phase cases (row n) -----------------------------------------------------------------------------
def _title(mode: str = "hgss") -> dict:
    pack = _pack(mode)
    return copy.deepcopy(next(iter(pack["titles"].values())))


def _red(title: dict, needle: str) -> None:
    errs = g.validate_phase_cases(title)
    assert any(needle in e for e in errs), errs


@pytest.mark.parametrize("mode", ["hgss", "hge"])
def test_committed_phase_cases_validate_and_cover_the_candidates(mode):
    pack = _pack(mode)
    for name, t in pack["titles"].items():
        assert g.validate_phase_cases(t) == [], name
        assert [c["name"] for c in t["phase_cases"]] == ["battle", "battle_arm", "battle_disarm", "battle_close", "reset", "pc"]
        assert [c["name"] for c in t["phase_cases_blocked"]] == ["pc_withdraw_release"]
        for phase in ("battle", "pc"):
            cases = [c for c in t["phase_cases"] + t["phase_cases_blocked"] if c["phase"] == phase]
            chosen = {s for c in cases for s in c["sites"]}
            skipped = set(t["phase_cases_excluded"][phase])
            assert chosen | skipped == set(t["phases"][phase]["candidate_sites"]) and not chosen & skipped, (name, phase)
            assert all(isinstance(why, str) and why for why in t["phase_cases_excluded"][phase].values())
        for c in t["phase_cases"]:
            value = 14 if c["phase"] == "pc" else 12
            assert c["predicate"] == {"symbol": "sFieldSysPtr", "deref": [0, 4], "offset": 0x0C, "value": value}
            assert c["predicate_file_check"]["ovy_id"] == value and c["status"] == "ROUTE_LEGS_PARTLY_NEW"
            assert c["producer_site"] in c["sites"] and len(c["sites"]) + 1 <= 4 and c["open"]  # honest: nothing is closed
            assert c["route"][0] in ("gen4_routes:battle_settled", "gen4_pc:reach_pc_terminal") and all(leg in c["route_status"] for leg in c["route"])
        pc = t["phase_cases_blocked"][0]
        assert pc["predicate"]["value"] == pc["predicate_file_check"]["ovy_id"] == 14 and pc["status"] == "BLOCKED_NO_FIXTURE"
        assert "withdraw leg is unrouted" in pc["blocked_reason"] and "6b" in pc["blocked_reason"]


def test_hge_box_modified_flag_is_measured_and_cited():
    prof = _pack("hge")["titles"]["heartgold_hge"]["profile"]
    flag = prof["pc"]["box_modified_flag_off"]
    assert flag == prof["box_modified_flag_off"] == 0x1E004 and isinstance(flag, int)
    ev = prof["pc"]["box_modified_flag_evidence"]
    assert "PHYSICAL" in ev and "route_pc_hge_leg7.log:16,25" in ev and "SOURCE projection" in ev
    assert "cleared on load" in ev and "keeps 1" in ev and "not a persistence requirement" in ev
    hg = _pack("hgss")["titles"]["heartgold"]["profile"]["pc"]
    assert hg["box_modified_flag_off"] == 0x12004 and "route_pc_leg4.log:16,25" in hg["box_modified_flag_evidence"]


@pytest.mark.parametrize("mode", ["hgss", "hge", "pt"])
def test_no_pack_carries_the_stale_pc_fixture_blocker(mode):
    text = PACKS[mode].read_text(encoding="utf-8")
    assert "0 box mons" not in text and "no route tooling" not in text and "no fixture - every owner save" not in text
    if mode != "pt":
        for t in json.loads(text)["titles"].values():
            leg = t["route_legs"]["pc_withdraw_box_mon"]
            assert leg["evidence"] == "OPEN" and "unrouted" in leg["open"] and "node 8" in leg["open"]


@pytest.mark.parametrize("mode", ["hgss", "hge", "pt"])
def test_committed_pack_is_current_and_regeneration_is_byte_identical(mode, capsys):
    _need("heartgold", "soulsilver", "heartgold_hge", "platinum")
    _need_fork(mode)
    code = g.main([mode, "--check"])
    err = capsys.readouterr().err
    if code == g.EXIT_SKIP:
        # The generator names the absent input itself (e.g. the pinned xMAP under .cache/gen4): a clean
        # clone has none of them, and absent skips while a present-but-wrong input fails (EXIT_FAIL).
        pytest.skip(err.strip() or "generator reported an absent input")
    assert code == g.EXIT_OK, err


def test_hg_and_ss_phase_cases_are_identical():
    pack = _pack("hgss")
    hg, ss = pack["titles"]["heartgold"], pack["titles"]["soulsilver"]
    for key in ("phase_cases", "phase_cases_blocked", "phase_cases_excluded"):
        assert hg[key] == ss[key], key


def test_predicate_is_tied_to_the_probe_field_chain_and_the_rom_template():
    t = _title()
    pf = t["profile"]["probe_field"]
    assert all(c["predicate"]["deref"] == [pf["sub"], pf["launched_app"]] for c in t["phase_cases"])
    assert t["symbols"]["sFieldSysPtr"]["address"] == 0x021D4158 and "sFieldSysPtr" in t["symbols"]
    t["phase_cases"][0]["predicate"]["deref"] = [0, 8]
    _red(t, "probe_field")
    t = _title()
    t["phase_cases"][0]["predicate"]["value"] = 13  # the template in the ROM says 12
    _red(t, "ROM template ovy_id")
    t = _title()
    t["phase_cases"][0]["predicate"]["nonzero"] = True  # value AND nonzero is ambiguous
    _red(t, "exactly one of value / nonzero")


def test_wrong_predicate_symbol_goes_red():
    t = _title()
    t["phase_cases"][0]["predicate"]["symbol"] = "sNotInTheXmap"
    _red(t, "predicate.symbol")
    t = _title()
    t["phase_cases"][0]["predicate"]["symbol"] = "gSystem"  # a real xMAP symbol is accepted by this check (the chain check catches it)
    assert not any("predicate.symbol" in e for e in g.validate_phase_cases(t))


def test_sites_above_the_cap_go_red():
    t = _title()
    battle = t["phases"]["battle"]
    assert battle["cap"] == 3
    t["phase_cases"][0]["sites"] = [*battle["candidate_sites"][:4]]  # 4 candidates > cap 3
    _red(t, "exceed the battle cap 3")
    t = _title()
    t["phase_cases_blocked"][0]["sites"] = t["phases"]["pc"]["candidate_sites"][:3]  # cap 2
    _red(t, "exceed the pc cap 2")


def test_producer_must_be_one_of_the_armed_sites():
    # the probe compares its always-on observer with the REGISTRY events of producer_site, so a producer outside `sites` can never match
    t = _title()
    t["phase_cases"][0]["producer_site"] = "encounter_result"
    _red(t, "must be one of sites")


def test_case_site_outside_candidate_sites_goes_red():
    t = _title()
    t["phase_cases"][0]["sites"][1] = "pc_swap_by_index_pair"  # a pc candidate in a battle case
    _red(t, "is not a battle candidate_site")
    t = _title()
    t["phase_cases"][0]["sites"][1] = "per_frame_arm"  # a probe-phase site
    _red(t, "is not a battle candidate_site")


def test_every_unexercised_caller_must_be_listed_once_with_a_reason():
    t = _title()
    t["phase_cases"][0]["open"].pop()
    _red(t, "why_open")
    t = _title()
    row = t["phase_cases"][0]["caller_matrix"]["sites"][0]["callers"][0]
    row["exercised_by_route"], row["exercised_by"] = True, ["pc_exit_app"]  # claims coverage by a leg outside the route
    _red(t, "leg that is not in the route")
    t = _title()
    t["phase_cases"][0]["route"].append("teleport")  # a leg without a route_status
    _red(t, "named legs")
    t = _title()
    t["phase_cases"][0]["caller_matrix"]["sites"].pop()
    _red(t, "caller_matrix.sites")


# ---- card C1-2c: button-recipe route legs, UI geometry, shared-address collision pairs ------------
ALL_TITLES = [(mode, name) for mode in ("hgss", "hge") for name in _pack(mode)["titles"]]


def _tt(mode: str, name: str) -> dict:
    return copy.deepcopy(_pack(mode)["titles"][name])


def _red_legs(title: dict, needle: str) -> None:
    errs = g.validate_route_legs(title)
    assert any(needle in e for e in errs), errs


@pytest.mark.parametrize("mode,name", ALL_TITLES)
def test_committed_route_legs_validate_and_cover_every_named_leg(mode, name):
    t = _tt(mode, name)
    assert g.validate_route_legs(t) == [] and g.validate_collision_pairs(t) == []
    legs = t["route_legs"]
    for case in [*t["phase_cases"], *t["phase_cases_blocked"]]:
        assert set(case["route"]) <= set(legs), case["name"]
    assert set(t["route"]) | set(t["persistence_route"]) <= set(legs)
    recipes = {n for n, leg in legs.items() if leg["route_status"] == "recipe_source"}
    assert {"fight_until_enemy_faints", "run_from_wild", "exit_battle_to_overworld", "soft_reset_in_fight_menu", "open_start_menu",
            "start_menu_cursor_to_save", "start_menu_select_save", "save_confirm_until_saved", "close_start_menu"} == recipes
    assert all(legs[n]["steps"] == [] and legs[n]["until"] is None and legs[n]["open"] for n in set(legs) - recipes)
    assert all(legs[n]["evidence"] in ("SOURCE", "FILE") for n in recipes)
    buttons = {b for n in recipes for st in legs[n]["steps"] for b in st["press"]}
    assert buttons <= set(g.NDS_BUTTONS) and {"A", "X", "Start", "Select", "L", "R", "Down", "Left"} <= buttons  # Up left with the row-o fight recipe


def test_hg_ss_hge_route_legs_and_collision_pairs_agree():
    hg, ss = _tt("hgss", "heartgold"), _tt("hgss", "soulsilver")
    for key in ("route_legs", "route", "persistence_route", "collision_pairs"):
        assert hg[key] == ss[key], key
    # the UI code is byte-identical across the two ROMs; only the compared-with label differs
    assert {n: r["sha1"] for n, r in hg["ui_geometry"]["code_identity"]["symbols"].items()} == \
        {n: r["sha1"] for n, r in ss["ui_geometry"]["code_identity"]["symbols"].items()}
    hge = _tt("hge", "heartgold_hge")
    for n, leg in hg["route_legs"].items():  # same recipes; only the hge note on the faint leg differs
        assert {k: v for k, v in hge["route_legs"][n].items() if k != "note"} == {k: v for k, v in leg.items() if k != "note"}, n
    assert hge["ui_geometry"]["code_identity"]["symbols"] == hg["ui_geometry"]["code_identity"]["symbols"]


def test_unknown_predicate_symbol_in_a_recipe_goes_red():
    t = _tt("hgss", "heartgold")
    t["route_legs"]["run_from_wild"]["until"]["symbol"] = "sNotInTheXmap"
    _red_legs(t, "until.symbol")
    t = _tt("hgss", "heartgold")
    t["route_legs"]["run_from_wild"]["until"].update(nonzero=True)  # value|nonzero|zero must be exactly one
    _red_legs(t, "exactly one of value")
    t = _tt("hgss", "heartgold")
    t["route_legs"]["run_from_wild"]["until"] = None
    _red_legs(t, "until must be a predicate object")


@pytest.mark.parametrize("bad", [None, 0, -5, g.ROUTE_MAX_FRAMES + 1, True, "600"])
def test_recipe_without_a_bounded_max_frames_goes_red(bad):
    t = _tt("hgss", "heartgold")
    t["route_legs"]["fight_until_enemy_faints"]["max_frames"] = bad
    _red_legs(t, "max_frames must be a bounded integer")
    t = _tt("hgss", "heartgold")
    del t["route_legs"]["fight_until_enemy_faints"]["max_frames"]
    _red_legs(t, "max_frames must be a bounded integer")


def test_a_cycle_longer_than_max_frames_and_bad_steps_go_red():
    t = _tt("hgss", "heartgold")
    t["route_legs"]["fight_until_enemy_faints"]["max_frames"] = 10
    _red_legs(t, "exceeds max_frames")
    t = _tt("hgss", "heartgold")
    t["route_legs"]["open_start_menu"]["steps"][0]["press"] = ["Z"]  # not a BizHawk NDS joypad name
    _red_legs(t, "BizHawk NDS buttons")
    t = _tt("hgss", "heartgold")
    t["route_legs"]["open_start_menu"]["steps"][0]["hold_frames"] = 0  # a press must be held at least one frame
    _red_legs(t, "hold_frames >= 1")
    t = _tt("hgss", "heartgold")
    t["route_legs"]["open_start_menu"]["steps"] = []
    _red_legs(t, "needs steps")


def test_every_route_and_phase_case_leg_must_exist_in_route_legs():
    t = _tt("hgss", "heartgold")
    t["phase_cases"][0]["route"].append("teleport")
    _red_legs(t, "leg 'teleport' is not in route_legs")
    t = _tt("hgss", "heartgold")
    del t["route_legs"]["fight_until_enemy_faints"]  # still named by route and by phase_cases[].route
    errs = g.validate_route_legs(t)
    assert any("route:" in e for e in errs) and any("phase_case:battle.route" in e for e in errs), errs
    t = _tt("hgss", "heartgold")
    t["persistence_route"] = []
    _red_legs(t, "persistence_route")
    t = _tt("hgss", "heartgold")
    t["route_legs"]["gen4_routes:battle_settled"]["steps"] = [{"press": ["A"], "hold_frames": 1, "then_wait_frames": 1}]
    _red_legs(t, "an open leg keeps steps []")


def test_battle_dpad_recipes_follow_the_rom_tables_from_every_cursor_cell():
    ui = _tt("hgss", "heartgold")["ui_geometry"]
    main = ui["battle_main_cursor"]["cells"]
    assert main == g.EXPECT_MAIN_CURSOR and ui["battle_fight_cursor"]["cells"] == g.EXPECT_FIGHT_CURSOR
    step = lambda x, y, k: g.main_menu_step(main, x, y, k)  # noqa: E731
    starts = [(x, y) for y in range(2) for x in range(3)]
    assert {g._run(step, c, ui["battle_paths"]["to_FIGHT"]) for c in starts} == {(0, 0)}
    assert {main[y][x] for x, y in (g._run(step, c, ui["battle_paths"]["to_RUN"]) for c in starts)} == {3}
    # the quirks the recipes route around: Up on RUN does nothing; Left on FIGHT jumps to BAG (bottom-left)
    assert g.main_menu_step(main, 1, 1, "Up") == (1, 1) and g.main_menu_step(main, 0, 0, "Left") == (0, 1)
    fight = ui["battle_fight_cursor"]["cells"]
    fstep = lambda x, y, k: g._check_key(x, y, 2, 3, fight, k)  # noqa: E731
    assert {g._run(fstep, (x, y), ui["battle_paths"]["fight_to_MOVE_1"]) for y in range(3) for x in range(2)} == {(0, 0)}


def test_start_menu_save_path_is_the_bfs_over_the_rom_neighbour_table():
    sm = _tt("hgss", "heartgold")["ui_geometry"]["start_menu"]
    nav = sm["neighbour_table"]["rows"]
    assert len(nav) == 7 and all(len(row) == 4 and all(len(c) == 3 for c in row) for row in nav)
    assert sm["layout_rows"]["rows"][0] == g.FULL_MENU_VARIANT and sm["icon_order"][5] == "SAVE"
    assert sm["cursor_to_save"] == {**sm["cursor_to_save"], "from_cell": 0, "to_cell": 5, "path": ["Down", "Left"]}
    assert g.start_menu_path(nav, 0, 5) == ["Down", "Left"] and g.start_menu_path(nav, 5, 5) == []
    # no single word reaches SAVE from every start cell, which is why the leg verifies the landing cell
    assert g.start_menu_path(nav, 3, 5) == ["Left", "Up"] != g.start_menu_path(nav, 0, 5)
    leg = _tt("hgss", "heartgold")["route_legs"]["start_menu_cursor_to_save"]
    assert [st["press"] for st in leg["steps"]] == [[d] for d in sm["cursor_to_save"]["path"]]
    assert leg["until"]["value"] == 5 and leg["until"]["offset"] == g.PANEL_CURSOR_OFF
    assert g.start_menu_path([[[0, 0, 0]] * 4] * 7, 0, 5) is None  # a table without a route is None, not a guess


def test_save_and_battle_predicates_use_the_pack_probe_field_offsets():
    t = _tt("hgss", "heartgold")
    pf, legs = t["profile"]["probe_field"], t["route_legs"]
    chain = [pf["save_driver"], pf["save_driver_data_off"], 4, pf["save_driver_data_off"]]
    assert legs["start_menu_select_save"]["until"] == {"symbol": "sFieldSysPtr", "deref": chain, "offset": 0x0C, "value": 4}
    assert legs["save_confirm_until_saved"]["until"]["value"] == 15 and legs["save_confirm_until_saved"]["until"]["deref"] == chain
    assert legs["open_start_menu"]["until"] == {"symbol": "sFieldSysPtr", "deref": [], "offset": pf["task"], "nonzero": True}
    assert legs["exit_battle_to_overworld"]["until"]["zero"] is True and legs["run_from_wild"]["until"]["deref"] == [pf["sub"]]
    b = t["profile"]["battle"]
    assert legs["fight_until_enemy_faints"]["until"] == {
        "symbol": "sFieldSysPtr", "deref": [pf["sub"], pf["launched_app"], 0x1C, b["ctx_off"]],
        "offset": b["mons_off"] + b["mon_size"] + b["hp_off"], "zero": True}
    reset = legs["soft_reset_in_fight_menu"]
    assert reset["steps"][0]["press"] == ["Start", "Select", "L", "R"] and "NULL base" in reset["note"]


def test_collision_pair_shares_one_address_in_two_images_with_different_words():
    for mode, name in ALL_TITLES:
        t = _tt(mode, name)
        (pair,) = t["collision_pairs"]
        a, b = pair["sites"].values()
        assert a["address"] == b["address"] == pair["address"] == 0x021E5900
        assert {a["image"], b["image"]} == {"ov1", "ov60"} and a["fire_hex"] != b["fire_hex"]
        assert {a["symbol"], b["symbol"]} == {"FieldMap_VBlankCallback", "TitleScreen_Init"}
        assert t["overlays"]["1"]["ram"] == t["overlays"]["60"]["ram"] == pair["address"]
        assert g.validate_collision_pairs(t) == []
    hge = _tt("hge", "heartgold_hge")["collision_pairs"][0]
    assert "all zero padding" in hge["hge_note"] and hge["open"] == []


def test_collision_sites_in_the_same_image_or_at_different_addresses_go_red():
    t = _tt("hgss", "heartgold")
    sites = t["collision_pairs"][0]["sites"]
    sites["title_init_ov60"]["image"] = sites["field_vblank_ov1"]["image"]  # same owning image: not a collision
    assert any("different images" in e for e in g.validate_collision_pairs(t))
    t = _tt("hgss", "heartgold")
    t["collision_pairs"][0]["sites"]["title_init_ov60"]["address"] += 2
    assert any("share one address" in e for e in g.validate_collision_pairs(t))
    t = _tt("hgss", "heartgold")
    sites = t["collision_pairs"][0]["sites"]
    sites["title_init_ov60"]["fire_hex"] = sites["field_vblank_ov1"]["fire_hex"]
    assert any("first words must differ" in e for e in g.validate_collision_pairs(t))
    t = _tt("hgss", "heartgold")
    del t["collision_pairs"][0]["resident_when"]["title_init_ov60"]
    assert any("resident_when" in e for e in g.validate_collision_pairs(t))
    t = _tt("hgss", "heartgold")
    t["collision_pairs"] = []
    assert g.validate_collision_pairs(t) == ["collision_pairs missing or empty"]


def test_collision_sites_match_the_real_images():
    inputs = _need("heartgold", "soulsilver", "heartgold_hge")
    for mode, name, key, raw in (("hgss", "heartgold", "heartgold", False), ("hgss", "soulsilver", "soulsilver", False),
                                 ("hge", "heartgold_hge", "heartgold_hge", True)):
        images = g.load_images(inputs.paths[key], raw_arm9=raw)
        assert g.validate_sites(_tt(mode, name)["collision_pairs"][0]["sites"], images, name) == []


def test_the_synthetic_world_rejects_a_same_image_pair(monkeypatch):
    xm = g.XMap(XMAP)
    monkeypatch.setattr(g, "COLLISION_SPECS", [{"name": "synthetic", "sites": [("a", "Faint", "x"), ("b", "Faint", "x")]}])
    with pytest.raises(g.Fail, match="one address in two images"):
        g.collision_pairs(xm, _images(), "hgss")


def test_comparison_name_and_image_attribution_counts_reconcile():
    c = _pack("hgss")["comparison"]
    several = c["differing_names_in_several_images"]
    assert sum(c["differing_names_by_image"].values()) == c["differing_name_image_pairs"]
    assert c["differing_name_image_pairs"] == c["address_differs"] + len(several)  # each such name sits in exactly two images
    assert several == sorted(several) and c["address_differs"] > len(several)
    other = g.XMap(XMAP.replace("02000100 00000010 .text   FuncThumb", "02000104 00000010 .text   FuncThumb"))
    d = g.compare_title_maps(g.XMap(XMAP), other)
    assert d["address_differs"] == 1 == d["differing_name_image_pairs"] and d["differing_names_by_image"] == {"arm9": 1}


def test_probe_field_citations_point_at_the_lines_the_coordinator_checked():
    ev = _pack("hgss")["titles"]["heartgold"]["profile"]["probe_field_evidence"]
    assert "include/field_system.h:81 (FieldSystemUnkSub0.unk4" in ev["launched_app"]["cite"]
    assert "include/field_system.h:80 (FieldSystemUnkSub0.unk0" in ev["field_app"]["cite"]
    assert ":115-117 (sub_0203DF7C" in ev["field_app"]["cite"] and "97 (non-NULL for the whole field session" not in ev["field_app"]["cite"]


def test_source_citations_are_re_read_from_the_pinned_clone():
    clone = g.gen4_pins.default_locations().sources["pokeheartgold_citation"]
    if not (clone / "src" / "start_menu.c").is_file():
        pytest.skip(f"absent input pokeheartgold clone: {clone}")
    assert len(g.CITE_NEEDLES) > 40
    for path, line, needle in g.CITE_NEEDLES:
        text = (clone / path).read_text(encoding="utf-8", errors="replace").splitlines()
        assert line <= len(text) and needle in text[line - 1], f"{path}:{line} does not contain {needle!r}: {text[line - 1]!r}"
    # control: the pre-fix line (field_system.h:80 for unk4) does not carry unk4
    header = (clone / "include/field_system.h").read_text(encoding="utf-8").splitlines()
    assert "unk4" not in header[79] and "unk4" in header[80]


# ---- card C1-2d: profile.battle, battle_enums, admission anchors, system.address, diagnostic sites -------------
LOCS = g.gen4_pins.default_locations()


def _clone(key: str, probe: str) -> Path:
    root = LOCS.sources[key]
    if not (root / probe).is_file():
        pytest.skip(gen4_pins.hge_fork_absent(root) if key == "hg_engine_fork" else f"absent input {key} clone: {root}")
    if key == "hg_engine_fork":
        head, clean = gen4_pins.git_identity(root)
        assert head == gen4_pins.SOURCE_COMMITS[key] and clean, f"{key} clone {root} is at {head} (clean={clean})"
    return root


def _defines(text: str, prefix: str, literals_only: bool = False) -> dict[str, int]:
    """`#define NAME expr` for NAMEs starting with `prefix`: literals, `(1 << n)` and ORs of already-known names, to a fixpoint."""
    raw = {m.group(1): m.group(2).strip() for m in re.finditer(rf"^#define\s+({prefix}\w*)[ \t]+(.+?)[ \t]*(?://.*)?$", text, re.M)}
    if literals_only:  # composite constants (A | B) are not single battle types
        raw = {k: v for k, v in raw.items() if not re.search(r"[A-Za-z_]", re.sub(r"0[xX][0-9A-Fa-f]+", "", v))}
    out: dict[str, int] = {}
    for _ in range(6):
        for name, expr in raw.items():
            sub = re.sub(r"[A-Za-z_]\w*", lambda m: str(out[m.group(0)]) if m.group(0) in out else m.group(0), expr)
            if re.fullmatch(r"[\d\sxXa-fA-F|()<>]+", sub):
                with contextlib.suppress(SyntaxError):  # not yet resolvable: a name defined later in the fixpoint
                    out[name] = int(eval(sub, {"__builtins__": {}}))  # a tiny closed grammar: digits, | ( ) <<
    return out


_SIZES = {"u8": 1, "s8": 1, "u16": 2, "s16": 2, "u32": 4, "s32": 4, "int": 4, "BOOL": 4}
_CONSTS = {"MAX_MON_MOVES": 4, "NUM_BATTLE_STATS": 8, "POKEMON_NAME_LENGTH": 10, "PLAYER_NAME_LENGTH": 7}  # include/constants/{pokemon,global}.h


def _up(x: int, n: int) -> int:
    return (x + n - 1) // n * n


def _layout(text: str, name: str, stop: str | None = None) -> dict:
    """{field: byte offset, '_size': n} of a pret C struct: scalars, arrays, pointers, GCC-ARM bitfield packing, nested structs."""
    m = re.search(rf"(?:typedef\s+)?struct\s+{name}\s*\{{(.*?)\n\}}", text, re.S)
    assert m, name
    bits, align, fields = 0, 1, {}
    for stmt in filter(None, (" ".join(x.split()) for x in re.sub(r"//.*", "", m.group(1)).split(";"))):
        bf = re.fullmatch(r"(\w+) (\w+) : (\d+)", stmt)
        if bf:
            t, fname, w = bf.group(1), bf.group(2), int(bf.group(3))
            unit = _SIZES[t] * 8
            if bits // unit != (bits + w - 1) // unit:
                bits = (bits // unit + 1) * unit
            fields[fname] = bits // 8
            bits, align = bits + w, max(align, _SIZES[t])
            continue
        t, ptr, fname, arr = re.fullmatch(r"(\w+) (\*?)(\w+)(?:\[([\w +]+)\])?", stmt).groups()
        if stop is not None and fname == stop:  # fields past here may use types from other headers
            fields[fname] = _up((bits + 7) // 8, 4)
            break
        if ptr:
            size = al = 4
        elif t in _SIZES:
            size = al = _SIZES[t]
        else:
            sub = _layout(text, t)
            size, al = sub["_size"], sub["_align"]
        n = 1 if arr is None else int(eval(arr, {"__builtins__": {}}, _CONSTS))
        at = _up((bits + 7) // 8, al)
        fields[fname] = at
        bits, align = (at + size * n) * 8, max(align, al)
    fields["_size"] = _up((bits + 7) // 8, align)
    fields["_align"] = align
    return fields


def test_hgss_battle_block_equals_the_pret_struct_layout():
    clone = _clone("pokeheartgold_citation", "include/battle/battle.h")
    text = (clone / "include/battle/battle.h").read_text(encoding="utf-8", errors="replace")
    mon, bsys = _layout(text, "BattleMon"), _layout(text, "BattleSystem", stop="ctx")
    for title in ("heartgold", "soulsilver"):
        b = _pack("hgss")["titles"][title]["profile"]["battle"]
        assert (mon["species"], mon["level"], mon["hp"], mon["maxHp"], mon["personality"], mon["otid"], mon["ability"], mon["_size"]) == (
            b["species_off"], b["level_off"], b["hp_off"], b["max_hp_off"], b["personality_off"], b["otid_off"], b["ability_off"], b["mon_size"])
        assert (bsys["battleType"], bsys["ctx"]) == (b["type_off"], b["ctx_off"])
    # control: the parser is not vacuous (it sees the real offsets, so a wrong expectation is a different number)
    assert mon["hp"] != 0x4E and bsys["ctx"] != 0x34
    for title in ("heartgold", "soulsilver"):
        b = _pack("hgss")["titles"][title]["profile"]["battle"]
        assert (mon["moves"], mon["movePPCur"]) == (b["moves_off"], b["pp_off"]) == (0x0C, 0x2C)


def test_hge_battle_block_equals_the_fork_offset_comments():
    clone = _clone("hg_engine_fork", "include/battle.h")
    text = (clone / "include/battle.h").read_text(encoding="utf-8", errors="replace")

    def at(field: str) -> int:
        return int(re.search(rf"/\*\s*(0x[0-9A-Fa-f]+)\s*\*/[^\n;]*\b{field}(?!\w)", text).group(1), 16)

    b = _pack("hge")["titles"]["heartgold_hge"]["profile"]["battle"]
    got = {"species_off": at("species"), "level_off": at("level"), "hp_off": at("hp"), "max_hp_off": at("maxhp"),
           "personality_off": at("personal_rnd"), "otid_off": at("id_no"), "ability_off": at("ability"), "type_off": at("battleType"),
           "moves_off": at(r"move\[4\]"), "pp_off": at(r"pp\[4\]"),
           "ctx_off": at("sp"), "mons_off": at(r"battlemon\[CLIENT_MAX\]"), "selected_off": at(r"sel_mons_no\[CLIENT_MAX\]"),
           "fainted_flag_off": at("server_status_flag")}
    assert got == {k: b[k] for k in got}
    assert re.search(r"battlemon\[CLIENT_MAX\];\s*// 0xc0", text) and b["mon_size"] == 0xC0
    assert b["ability_off"] == 0x7A and b["ability_width"] == 2


@pytest.mark.parametrize("mode,title", [("hgss", "heartgold"), ("hgss", "soulsilver"), ("hge", "heartgold_hge")])
def test_battle_block_shape_and_evidence(mode, title):
    prof = _pack(mode)["titles"][title]["profile"]
    b, ev = prof["battle"], prof["battle_evidence"]
    need = {"man_data_off": 0x1C, "ctx_off": 0x30, "mons_off": 0x2D40, "mon_size": 0xC0, "max_battlers": 4, "selected_off": 0x219C,
            "type_off": 0x2C, "outcome_off": 0x2420, "outcome_mask": 0x3F, "species_off": 0, "level_off": 0x34, "hp_off": 0x4C,
            "hp_width": 4, "hp_signed": True, "max_hp_off": 0x50, "personality_off": 0x68, "otid_off": 0x74, "template_off": 0x0C,
            "template_id": 12, "fainted_flag_off": 0x213C, "fainted_flag_shift": 24, "fainted_flag_mask": 0x0F000000}
    assert {k: b[k] for k in need} == need
    assert set(ev) == set(b), set(ev) ^ set(b)  # every value carries evidence, nothing extra
    for key, e in ev.items():
        assert e["class"] in ("SOURCE", "ASM", "FILE", "SOURCE_PROJECTION") and e["cite"], key
    for key in ("type_off", "ctx_off", "outcome_off", "template_off", "template_id"):
        assert ev[key]["class"] == "FILE", key  # ROM bytes, not just a header
    assert (b["ability_off"], b["ability_width"]) == ((0x7A, 2) if mode == "hge" else (0x27, 1))
    assert b["fainted_flag_mask"] == 0xF << b["fainted_flag_shift"]  # battler b faints = bit shift+b
    assert b["fainted_flag_off"] < b["selected_off"] < b["mons_off"]
    assert ("hg-engine fork@" in ev["species_off"]["cite"]) == (mode == "hge")


def test_battle_physical_corroboration_is_recorded_per_title():
    hg = _pack("hgss")["titles"]["heartgold"]["profile"]["battle_physical"]
    hge = _pack("hge")["titles"]["heartgold_hge"]["profile"]["battle_physical"]
    assert [(r["commit"], r.get("ctx")) for r in hg] == [("89b957d1", 0x022C32D8), ("a15b7d74", None)]
    assert [(r["commit"], r.get("ctx")) for r in hge] == [("89b957d1", 0x022D38A4), ("0f75c938", None)]
    assert _pack("hgss")["titles"]["soulsilver"]["profile"]["battle_physical"] == []  # SS: SOURCE/FILE only (D4)


def test_battle_rom_accessors_pin_type_ctx_and_outcome_offsets():
    checks = _pack("hgss")["titles"]["heartgold"]["profile"]["battle_file_checks"]
    assert {c["field"]: c["value"] for c in checks.values()} == {"type_off": 0x2C, "ctx_off": 0x30, "outcome_off": 0x2420}
    for mode, title in (("hgss", "heartgold"), ("hgss", "soulsilver"), ("hge", "heartgold_hge")):
        assert _pack(mode)["titles"][title]["profile"]["battle_file_checks"] == checks  # identical bytes in all three ROMs
    hge = _pack("hge")["titles"]["heartgold_hge"]["profile"]["battle_hge_checks"]
    assert all(hge["accessors_identical"].values()) and hge["outcome_store"]["identical_in_hge"]
    assert hge["outcome_store"]["bytes"] == g.OUTCOME_STORE_SEQ.hex()


def test_battle_rom_accessor_revert_with_a_wrong_offset(monkeypatch):
    monkeypatch.setattr(g, "BATTLE_ACCESSORS", g.BATTLE_ACCESSORS[:1])  # BattleSystem_GetBattleType only
    xm = g.XMap(XMAP.replace("02000820 00000010 .text   Faint", "02000820 00000004 .text   BattleSystem_GetBattleType"))

    def images(body: str) -> g.Images:
        ov = bytearray(0x100)
        ov[0x20:0x24] = bytes.fromhex(body)
        return g.Images(0x02000000, bytes(ARM9), {12: (0x02000800, bytes(ov), 0)})

    assert g.battle_file_checks(xm, images("c06a7047"))["BattleSystem_GetBattleType"]["value"] == 0x2C  # control: the real body passes
    with pytest.raises(g.Fail, match="no longer ROM-proven"):
        g.battle_file_checks(xm, images("c06b7047"))  # ldr r0,[r0,#0x3c]: not the pinned 0x2c


def test_battle_rom_checks_hold_in_the_pinned_roms():
    for key in ("heartgold", "soulsilver"):
        inputs = _need(key, "heartgold_xmap")
        xm, img = g.load_xmap(inputs.paths["heartgold_xmap"]), g.load_images(inputs.paths[key])
        assert g.battle_file_checks(xm, img) == _pack("hgss")["titles"][key]["profile"]["battle_file_checks"]
        tmpl = g.template_check(xm, img, "gOverlayTemplate_Battle", 12)
        b = _pack("hgss")["titles"][key]["profile"]["battle"]
        assert (b["template_off"], b["template_id"]) == (tmpl["ovy_id_off"], tmpl["ovy_id"]) == (0x0C, 12)


# ---- enums: outcomes / trainer_mask / exempt_mask ---------------------------------------------------------------------
ENUM_HEADERS = {  # pack -> (clone key, probe file, header files)
    "hgss": ("pokeheartgold_citation", "include/constants/battle.h", ["include/constants/battle.h"]),
    "hge": ("hg_engine_fork", "include/battle.h", ["include/battle.h", "include/battle_controller_player.h"]),
    "pt": ("pokeplatinum_citation", "include/constants/battle.h", ["include/constants/battle.h"]),
}
OUTCOME_NAMES = r"(?:BATTLE_OUTCOME_|BATTLE_RESULT_|BATTLE_IN_PROGRESS)"


def _enum_errors(enums: dict, types: dict[str, int], outcomes: dict[str, int]) -> list[str]:
    errs = []
    for const, val in enums["type_bits"].items():
        if types.get(const) != val:
            errs.append(f"{const}: pack {val:#x} vs header {types.get(const)}")
    singles = {c for c, v in types.items() if v and v & (v - 1) == 0}
    errs += [f"header single-bit {c} is not classified" for c in sorted(singles - set(enums["type_bits"]))]
    if set(enums["exempt_roles"].values()) | set(enums["not_exempt"]) != set(enums["type_bits"]):
        errs.append("exempt + not_exempt does not partition the constant table")
    mask = 0
    for const in enums["exempt_roles"].values():
        mask |= types[const]
    if enums["exempt_mask"] != mask or enums["no_catch_mask"] != mask:
        errs.append(f"exempt_mask {enums['exempt_mask']:#x} != OR of the exempt constants {mask:#x}")
    if enums["trainer_mask"] != types[enums["trainer_const"]] or enums["trainer_mask"] & mask:
        errs.append("trainer_mask is not the lone trainer bit outside the exempt mask")
    for name, code in enums["outcomes"].items():
        if outcomes.get(enums["outcome_consts"][name]) != code:
            errs.append(f"outcome {name}: pack {code} vs header {outcomes.get(enums['outcome_consts'][name])}")
    return errs


def _enum_header(mode: str) -> tuple[dict, dict]:
    key, probe, files = ENUM_HEADERS[mode]
    root = _clone(key, probe)
    text = "\n".join((root / f).read_text(encoding="utf-8", errors="replace") for f in files)
    return _defines(text, "BATTLE_TYPE_", literals_only=True), _defines(text, OUTCOME_NAMES)


@pytest.mark.parametrize("mode,title", [("hgss", "heartgold"), ("hgss", "soulsilver"), ("hge", "heartgold_hge"), ("pt", "platinum")])
def test_battle_enum_constants_exist_in_the_pinned_sources(mode, title):
    enums = _pack(mode)["titles"][title]["profile"]["battle_enums"]
    types, outcomes = _enum_header(mode)
    assert _enum_errors(enums, types, outcomes) == []
    assert enums["outcomes"] == {"none": 0, "win": 1, "lose": 2, "draw": 3, "caught": 4, "player_fled": 5, "foe_fled": 6}
    assert enums["trainer_mask"] == 1
    assert enums["exempt_mask"] & 0x1C == 0x1C  # link | multi | tag (the faint probe's refuse mask) is inside the exempt mask
    assert enums["exempt_mask"] == enums["no_catch_mask"] == {"hgss": 0x800016BC, "hge": 0x16BC, "pt": 0x800006BC}[mode]
    assert bool(enums["exempt_mask"] & 0x1000) == (mode != "pt")  # the bug-catching contest exists in HGSS/hge only


def test_battle_enum_revert_a_wrong_bit_a_missing_class_and_a_wrong_outcome_go_red():
    enums = _pack("hgss")["titles"]["heartgold"]["profile"]["battle_enums"]
    types, outcomes = _enum_header("hgss")
    bad = copy.deepcopy(enums)
    bad["type_bits"]["BATTLE_TYPE_LINK"] = 0x8  # wrong bit
    assert any("BATTLE_TYPE_LINK" in e for e in _enum_errors(bad, types, outcomes))
    bad = copy.deepcopy(enums)
    bad["exempt_mask"] &= ~0x1000  # a mask that forgot the bug contest
    assert any("exempt_mask" in e for e in _enum_errors(bad, types, outcomes))
    bad = copy.deepcopy(enums)
    del bad["not_exempt"]["BATTLE_TYPE_ROAMER"]  # a constant nobody classified
    assert any("partition" in e for e in _enum_errors(bad, types, outcomes))
    bad = copy.deepcopy(enums)
    bad["outcomes"]["lose"] = 3
    assert any("outcome lose" in e for e in _enum_errors(bad, types, outcomes))
    # a header constant the pack does not know is also red (a new battle type bit upstream)
    assert any("BATTLE_TYPE_NEW" in e for e in _enum_errors(enums, {**types, "BATTLE_TYPE_NEW": 1 << 20}, outcomes))


def test_generator_refuses_an_unclassified_battle_type(monkeypatch):
    t = copy.deepcopy(g.BATTLE_TYPE_TABLES)
    t["hgss"]["bits"]["BATTLE_TYPE_NEW"] = 1 << 20
    monkeypatch.setattr(g, "BATTLE_TYPE_TABLES", t)
    with pytest.raises(g.Fail, match="unclassified"):
        g.battle_enums("hgss")


# ---- admission: md5 + sha1 pinned, anchors, hge differs ----------------------------------------------------------
def test_every_title_pins_both_md5_and_sha1_and_they_match_the_lock():
    lock = json.loads(g.LOCK_PATH.read_text(encoding="utf-8"))["artifacts"]
    seen = {}
    for mode in ("hgss", "hge", "pt"):
        for title, t in _pack(mode)["titles"].items():
            rom = t["rom"]
            assert re.fullmatch(r"[0-9a-f]{40}", rom["sha1"]) and re.fullmatch(r"[0-9a-f]{32}", rom["md5"]), title
            pin = lock[title]
            assert (rom["sha1"], rom["md5"], rom["header_code"]) == (pin["sha1"], pin["md5"], pin["header_code"]), title
            for digest in (rom["sha1"], rom["md5"]):
                assert digest not in seen, f"{digest} shared by {seen.get(digest)} and {title}"  # entry.lua hard-errors on a repeat
                seen[digest] = title
    assert len(seen) == 8


def test_hge_admission_is_hash_only_and_explained():
    hge = _pack("hge")
    t = hge["titles"]["heartgold_hge"]
    assert t["admission"] == "HASH_ONLY" and "admission_anchors" not in t  # entry.lua: an hge hit is admitted by hash only
    assert hge["artifact_status"] == "RECORDED_NOT_ADMITTED"  # the G0 ledger status is a different fact
    note = hge["schema_notes"]["admission"]
    assert "HASH_ONLY" in note and "artifact_status" in note and "G0" in note
    assert _pack("hgss")["titles"]["heartgold"]["admission"] != "HASH_ONLY"


def test_admission_anchor_rows_are_well_formed():
    for title in ("heartgold", "soulsilver"):
        rows = _pack("hgss")["titles"][title]["admission_anchors"]
        assert [r["name"] for r in rows] == ["nitromain_hge_hook_site", "nitromain_entry", "nitromain_after_hook"]
        assert rows[0]["address"] == 0x02000CD0 and rows[0]["hge_differs"] is True and rows[0]["length"] == 16
        main = _pack("hgss")["titles"][title]["symbols"]["NitroMain"]
        for r in rows:
            assert r["image"] == "arm9" and len(bytes.fromhex(r["hex"])) == r["length"] >= 16 and r["address"] % 2 == 0
            assert main["address"] <= r["address"] and r["address"] + r["length"] <= main["address"] + main["size"]
    hge = _pack("hge")["titles"]["heartgold_hge"]["admission_check"]
    vanilla = _pack("hgss")["titles"]["heartgold"]["admission_anchors"][0]
    assert hge["vanilla_hex"] == vanilla["hex"] and hge["hge_hex"] != vanilla["hex"] and hge["differs"] is True
    assert hge["hge_hook"]["target"] == 0x02110334 and hge["other_anchors_same_in_hge"] == {"nitromain_entry": True, "nitromain_after_hook": True}


def test_admission_anchor_bytes_match_the_pinned_roms_and_hge_differs():
    inputs = _need("heartgold", "soulsilver", "heartgold_hge", "heartgold_xmap")
    imgs = {k: g.load_images(inputs.paths[k]) for k in ("heartgold", "soulsilver")}
    hge_img = g.load_images(inputs.paths["heartgold_hge"], raw_arm9=True)
    for title, img in imgs.items():
        for r in _pack("hgss")["titles"][title]["admission_anchors"]:
            assert img.read("arm9", r["address"], r["length"]).hex() == r["hex"], (title, r["name"])
    hook = _pack("hgss")["titles"]["heartgold"]["admission_anchors"][0]
    assert hge_img.read("arm9", hook["address"], hook["length"]).hex() != hook["hex"]  # the hge ROM is not vanilla here
    # the unmodified vanilla image passed off as hge: the generator FAILS (revert of the hook)
    xm = g.load_xmap(inputs.paths["heartgold_xmap"])
    with pytest.raises(g.Fail, match="cannot tell hge from vanilla"):
        g.hge_admission_check(imgs["heartgold"], imgs["heartgold"], g.admission_anchors(xm, imgs["heartgold"]), 0x02110334)


def _arm9(patches: dict[int, str]) -> g.Images:
    raw = bytearray(0x2000)
    for addr, hexs in patches.items():
        raw[addr - 0x02000000:addr - 0x02000000 + len(hexs) // 2] = bytes.fromhex(hexs)
    return g.Images(0x02000000, bytes(raw), {})


def test_hge_admission_check_synthetic_controls():
    vanilla = _arm9({0x02000CD0: "0020032102f00af80120032102f006f8"})
    anchors = [{"name": "hook", "address": 0x02000CD0, "address_hex": "0x02000cd0", "length": 16, "hex": "0020032102f00af80120032102f006f8", "hge_differs": True},
               {"name": "entry", "address": 0x02000CA4, "length": 16, "hex": "00" * 16, "hge_differs": False}]
    good = _arm9({0x02000CD0: "0ff130fb02f00af80120032102f006f8"})  # bl 0x02110334
    out = g.hge_admission_check(vanilla, good, anchors, 0x02110334)
    assert out["differs"] and out["hge_hook"]["target"] == 0x02110334 and out["other_anchors_same_in_hge"] == {"entry": True}
    with pytest.raises(g.Fail, match="cannot tell hge from vanilla"):
        g.hge_admission_check(vanilla, vanilla, anchors, 0x02110334)  # identical bytes: the anchor proves nothing
    with pytest.raises(g.Fail, match="not a call to load_arm9_expansion"):
        g.hge_admission_check(vanilla, _arm9({0x02000CD0: "ffffffff02f00af80120032102f006f8"}), anchors, 0x02110334)
    with pytest.raises(g.Fail, match="not a call to load_arm9_expansion"):
        g.hge_admission_check(vanilla, good, anchors, 0x02110338)  # a different target is a different mod


# ---- system.address (the gSystem address the checkpoint uses) ----------------------------------------------------------
@pytest.mark.parametrize("mode,title", [("hgss", "heartgold"), ("hgss", "soulsilver"), ("hge", "heartgold_hge"), ("pt", "platinum")])
def test_system_address_is_the_gsystem_symbol_address(mode, title):
    t = _pack(mode)["titles"][title]
    sysb = t["profile"]["system"]
    assert sysb["symbol"] == "gSystem" and sysb["address"] == t["symbols"]["gSystem"]["address"] > 0x02000000
    assert (sysb["vblank_counter_off"], sysb["frame_counter_off"]) == (0x2C, 0x30)
    assert sysb["address_evidence"] and sysb["evidence"]


def test_platinum_system_layout_is_size_checked_against_the_xmap():
    pt = _pack("pt")["titles"]["platinum"]
    assert pt["profile"]["system"]["vblank_counter_off"] == 0x2C and "vblank_counter_off" not in " ".join(pt["open"])
    assert pt["symbols"]["gSystem"]["size"] == 0x74
    assert g.pt_system({"address": 1, "size": 0x74})["vblank_counter_off"] == 0x2C
    with pytest.raises(g.Fail, match="vblankCounter offset is unproven"):
        g.pt_system({"address": 1, "size": 0x78})  # the HGSS size: a wrong struct is never emitted
    clone = _clone("pokeplatinum_citation", "include/system.h")
    text = (clone / "include/system.h").read_text(encoding="utf-8")
    body = re.search(r"typedef struct System \{(.*?)\} System;", text, re.S).group(1)
    names = [m.group(1) for m in re.finditer(r"^\s+\w[\w\s\*]*?\b(\w+);", body, re.M)]
    assert tuple(names[:len(g.PT_SYSTEM_HEAD)]) == g.PT_SYSTEM_HEAD  # the declaration order the offsets are counted from


def test_platinum_battle_and_probe_field_stay_open_with_reasons():
    pt = _pack("pt")["titles"]["platinum"]
    assert pt["profile"]["battle"] is None and pt["profile"]["probe_field"] is None
    assert "battle_enums" in pt["open"]["battle"] and "FieldSystemUnkSub0" in pt["open"]["probe_field"]


# ---- diagnostic sites ----------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("mode,title", [("hgss", "heartgold"), ("hgss", "soulsilver"), ("hge", "heartgold_hge")])
def test_diagnostic_sites_are_hot_probe_only_rows_never_production(mode, title):
    t = _pack(mode)["titles"][title]
    diag = t["diagnostic_sites"]
    assert set(diag) == {"hot_disable_interrupts", "hot_idle_halt"}
    assert diag["hot_disable_interrupts"]["symbol"] == "OS_DisableInterrupts" and diag["hot_idle_halt"]["symbol"] == "OS_Halt"
    assert diag["hot_idle_halt"]["address"] + 0xC == diag["hot_idle_halt"]["sampled_pc"] == 0x020D3F64
    production = {s["address"] for s in t["sites"].values() if s["phase"] in ("always", "battle", "pc", "field")}
    for sid, r in diag.items():
        reg = bytes.fromhex(r["register_hex"])
        assert r["image"] == "arm9" and r["mode"] == "arm" and r["phase"] == "diagnostic" and r["overlay_id"] is None
        assert r["extent"] == len(reg) >= 4 and r["fire_hex"] == g.fire_hex(r["register_hex"]) and r["address"] % 4 == 0
        assert t["symbols"][r["symbol"]]["address"] == r["address"]
        assert sid not in t["sites"] and all(sid not in ph.get("candidate_sites", []) for ph in t["phases"].values())
        for case in [*t["phase_cases"], *t["phase_cases_blocked"]]:
            assert sid not in case["sites"] and case["producer_site"] != sid
        assert r["address"] not in production  # the hot sites are not a production site under another id
        if mode == "hge":
            assert r["hge_status"] == "KEPT"
    assert "ONLY" in _pack(mode)["schema_notes"]["diagnostic_sites"]
    assert _pack("hgss")["titles"]["heartgold"]["diagnostic_sites"] == _pack("hgss")["titles"]["soulsilver"]["diagnostic_sites"]


def test_diagnostic_site_bytes_match_the_pinned_roms():
    inputs = _need("heartgold", "soulsilver", "heartgold_hge")
    for key, mode, raw in (("heartgold", "hgss", False), ("soulsilver", "hgss", False), ("heartgold_hge", "hge", True)):
        title = key
        img = g.load_images(inputs.paths[key], raw_arm9=raw)
        assert g.validate_diagnostic_sites(_pack(mode)["titles"][title], img) == []


def _images_for_title(t: dict) -> g.Images:
    """A synthetic arm9 that carries exactly this title's diagnostic bytes (validate_sites re-reads them)."""
    raw = bytearray(0x02200000 - 0x02000000)
    for r in t["diagnostic_sites"].values():
        raw[r["address"] - 0x02000000:r["address"] - 0x02000000 + r["extent"]] = bytes.fromhex(r["register_hex"])
    return g.Images(0x02000000, bytes(raw), {})


def test_diagnostic_site_in_a_production_place_goes_red():
    t = copy.deepcopy(_pack("hgss")["titles"]["heartgold"])
    img = _images_for_title(t)
    assert g.validate_diagnostic_sites(t, img) == []
    bad = copy.deepcopy(t)
    bad["sites"]["hot_idle_halt"] = bad["diagnostic_sites"]["hot_idle_halt"]
    assert any("also a production site id" in e for e in g.validate_diagnostic_sites(bad, img))
    bad = copy.deepcopy(t)
    bad["phases"]["battle"]["candidate_sites"].append("hot_disable_interrupts")
    assert any("battle candidate_site" in e for e in g.validate_diagnostic_sites(bad, img))
    bad = copy.deepcopy(t)
    bad["phase_cases"][0]["sites"].append("hot_idle_halt")
    assert any("used by phase case" in e for e in g.validate_diagnostic_sites(bad, img))
    bad = copy.deepcopy(t)
    bad["diagnostic_sites"]["hot_idle_halt"]["phase"] = "probe"
    assert any("phase must be 'diagnostic'" in e for e in g.validate_diagnostic_sites(bad, img))
    bad = copy.deepcopy(t)
    bad["diagnostic_sites"]["hot_idle_halt"]["register_hex"] = "00" * 12
    assert any("differs from the declared image" in e for e in g.validate_diagnostic_sites(bad, img))


def test_new_cite_needles_are_re_read_from_the_pinned_clones():
    for key, probe, needles in (("hg_engine_fork", "include/battle.h", g.CITE_NEEDLES_HGE),
                                ("pokeplatinum_citation", "include/system.h", g.CITE_NEEDLES_PT)):
        clone = _clone(key, probe)
        assert needles
        for path, line, needle in needles:
            text = (clone / path).read_text(encoding="utf-8", errors="replace").splitlines()
            assert line <= len(text) and needle in text[line - 1], f"{key} {path}:{line} does not contain {needle!r}: {text[line - 1]!r}"


# ---- card C1-2e: profile.rtc (the cached RTC work struct the pinned-RTC determinism probe writes) ---------------------
_SIZES.update({"RTCResult": 4, "RTCWeek": 4})  # NitroSDK enums are 4 bytes (the work-struct size equals the xMAP symbol size)
RTC_KEYS = ("date_off", "date_size", "time_off", "time_size", "async_date_off", "async_time_off")


def _rtc_source_layout(mode: str) -> dict:
    """{date_off, date_size, time_off, time_size, ...} re-derived from the pinned C struct, not from the generator."""
    hg = _clone("pokeheartgold_citation", "src/gf_rtc.c")
    api = (hg / "lib/include/nitro/rtc/ARM9/api.h").read_text(encoding="utf-8", errors="replace")
    if mode == "pt":
        pt = _clone("pokeplatinum_citation", "src/rtc.c")
        text = (pt / "src/rtc.c").read_text(encoding="utf-8", errors="replace").replace("typedef struct {", "struct RTCState {", 1)
        work = _layout(api + text, "RTCState")
    else:
        text = (hg / "src/gf_rtc.c").read_text(encoding="utf-8", errors="replace")
        work = _layout(api + text, "GFRtcWork")
    date, time = _layout(api, "RTCDate"), _layout(api, "RTCTime")
    first, second, third, fourth = ("valid", "readInProgress", "framesSinceRead", "status") if mode == "pt" else \
        ("getDateTimeSuccess", "getDateTimeLock", "getDateTimeSleep", "getDateTimeErrorCode")
    assert (work[first], work[second], work[third], work[fourth]) == (0, 4, 8, 12)
    d, t, da, ta = ("date", "time", "tempDate", "tempTime") if mode == "pt" else ("date", "time", "date_async", "time_async")
    return {"date_off": work[d], "date_size": date["_size"], "time_off": work[t], "time_size": time["_size"],
            "async_date_off": work[da], "async_time_off": work[ta], "work_size": work["_size"]}


@pytest.mark.parametrize("mode,title,symbol", [("hgss", "heartgold", "sRTCWork"), ("hgss", "soulsilver", "sRTCWork"),
                                               ("hge", "heartgold_hge", "sRTCWork"), ("pt", "platinum", "sRTCState")])
def test_rtc_profile_equals_the_source_struct_layout(mode, title, symbol):
    rtc = _pack(mode)["titles"][title]["profile"]["rtc"]
    src = _rtc_source_layout("pt" if mode == "pt" else "hgss")  # hge keeps the vanilla struct (FILE-checked); the fork defines none
    assert (rtc["symbol"], rtc["date_off"], rtc["date_size"], rtc["time_off"], rtc["time_size"]) == (symbol, 0x10, 16, 0x20, 12)
    assert {k: rtc[k] for k in src} == src
    assert rtc["address"] == _pack(mode)["titles"][title]["symbols"][symbol]["address"]
    assert rtc["work_size"] == _pack(mode)["titles"][title]["symbols"][symbol]["size"] == (72 if mode == "pt" else 88)
    assert rtc["source"] and rtc["evidence"] == "SOURCE + FILE"
    # revert-test: a wrong committed offset is a different number from what the struct parser reports
    for key in RTC_KEYS:
        assert {**rtc, key: rtc[key] + 4}[key] != src[key]
    assert src["date_off"] != 0x14 and src["time_off"] != 0x1C


def test_rtc_hgss_frozen_time_tail_and_hge_struct_declaration():
    hg = _pack("hgss")["titles"]["heartgold"]["profile"]["rtc"]
    assert (hg["frozen_state_off"], hg["frozen_time_off"]) == (0x48, 0x4C) and "frozen_state_off" not in _pack("pt")["titles"]["platinum"]["profile"]["rtc"]
    fork = _clone("hg_engine_fork", "include/rtc.h")
    text = (fork / "include/rtc.h").read_text(encoding="utf-8", errors="replace")
    assert re.search(r"struct RTCDate \{\s*u32 year;\s*u32 month;\s*u32 day;\s*enum RTCWeek week;\s*\};", text)
    assert re.search(r"struct RTCTime \{\s*u32 hour;\s*u32 minute;\s*u32 second;\s*\};", text)
    assert "GFRtcWork" not in text  # the fork defines no work struct of its own: the layout is the vanilla one (hge_checks)
    assert _pack("hge")["titles"]["heartgold_hge"]["profile"]["rtc"]["hge_checks"]["identical_to_vanilla"]


class _FakeXMap:
    def __init__(self, **syms): self.syms = syms
    def lookup(self, name): return self.syms[name]


def _rtc_world(date_lit: int, time_lit: int):
    base = 0x02000400
    arm9 = bytearray(0x40)
    arm9[0:4], arm9[4:8] = struct.pack("<I", base + date_lit), struct.pack("<I", base + time_lit)  # two literal-pool words
    imgs = g.Images(0x02000000, bytes(0x400) + bytes(arm9), {})
    mk = lambda n, a: g.Sym(n, a, 4, ".text", "x.o", "arm9")  # noqa: E731
    return imgs, _FakeXMap(GF_RTC_CopyDate=mk("GF_RTC_CopyDate", 0x02000400), GF_RTC_CopyTime=mk("GF_RTC_CopyTime", 0x02000404)), base


def test_rtc_file_check_reads_the_reader_literal_pools_and_a_wrong_offset_goes_red():
    imgs, xm, base = _rtc_world(0x10, 0x20)
    out = g.rtc_file_checks(xm, imgs, "hgss", base, 0x10, 0x20, 88)
    assert out["GF_RTC_CopyDate"]["pool_offsets"] == [0x10] and out["GF_RTC_CopyTime"]["pool_offsets"] == [0x20]
    for wrong in ((0x14, 0x20), (0x10, 0x1C)):
        with pytest.raises(g.Fail, match="not ROM-proven"):
            g.rtc_file_checks(xm, imgs, "hgss", base, *wrong, 88)
    imgs, xm, base = _rtc_world(0x20, 0x10)  # readers swapped in the ROM
    with pytest.raises(g.Fail, match="not ROM-proven"):
        g.rtc_file_checks(xm, imgs, "hgss", base, 0x10, 0x20, 88)


def test_rtc_layout_size_mismatch_goes_red(monkeypatch):
    inputs = _need("heartgold", "heartgold_xmap")
    xm, img = g.load_xmap(inputs.paths["heartgold_xmap"]), g.load_images(inputs.paths["heartgold"])
    assert g.rtc_profile(xm, img, "hgss") == _pack("hgss")["titles"]["heartgold"]["profile"]["rtc"]
    monkeypatch.setattr(g, "HG_RTC_FIELDS", (("a", 4), *g.HG_RTC_FIELDS))  # an extra word shifts everything and breaks the size
    with pytest.raises(g.Fail, match="unproven"):
        g.rtc_profile(xm, img, "hgss")
    monkeypatch.setattr(g, "HG_RTC_FIELDS", (*g.HG_RTC_FIELDS[:4], ("date", 20), *g.HG_RTC_FIELDS[5:]))  # a wrong RTCDate size
    with pytest.raises(g.Fail, match="unproven"):
        g.rtc_profile(xm, img, "hgss")


def test_rtc_file_checks_hold_in_the_pinned_roms_incl_hge_identity():
    inputs = _need("heartgold", "heartgold_hge", "heartgold_xmap", "platinum", "platinum_xmap")
    xm, hg = g.load_xmap(inputs.paths["heartgold_xmap"]), g.load_images(inputs.paths["heartgold"])
    hge = g.load_images(inputs.paths["heartgold_hge"], raw_arm9=True)
    assert g.rtc_profile(xm, hge, "hge", hg) == _pack("hge")["titles"]["heartgold_hge"]["profile"]["rtc"]
    pxm, pt = g.load_xmap(inputs.paths["platinum_xmap"]), g.load_images(inputs.paths["platinum"])
    assert g.rtc_profile(pxm, pt, "pt") == _pack("pt")["titles"]["platinum"]["profile"]["rtc"]
    # control: the vanilla image checked with a time offset one word off is not proven
    row = g.symbol_row(xm, "sRTCWork")
    with pytest.raises(g.Fail, match="not ROM-proven"):
        g.rtc_file_checks(xm, hg, "hgss", row["address"], 0x10, 0x24, 88)


# ---- card gen4-C1-2f: rtc wording (Platinum has its own caveat), the hge identity compare, battle field owners ----------
def test_platinum_rtc_has_its_own_caveat_with_no_frozen_pair():
    pt = _pack("pt")["titles"]["platinum"]["profile"]["rtc"]
    hg = _pack("hgss")["titles"]["heartgold"]["profile"]["rtc"]
    assert "frozen" not in json.dumps(pt).lower()  # Platinum's RTCState has no frozen-time pair anywhere in its rtc block
    assert "frozen" in hg["caveat"]  # control: the HGSS caveat does describe it
    for word in ("readInProgress", "framesSinceRead", "tempDate", "tempTime", "11 frames"):
        assert word in pt["caveat"], word
    assert "date_async" not in json.dumps(pt) and "getDateTime" not in json.dumps(pt)
    assert pt["async_names"] == {"date": "tempDate", "time": "tempTime"} and hg["async_names"] == {"date": "date_async", "time": "time_async"}
    assert (pt["async_date_off"], pt["async_time_off"]) == (hg["async_date_off"], hg["async_time_off"]) == (0x2C, 0x3C)
    assert pt["refresh_gate"]["lock_field"] == "readInProgress" and hg["refresh_gate"]["lock_field"] == "getDateTimeLock"
    assert pt["refresh_gate"]["counter_threshold"] == hg["refresh_gate"]["counter_threshold"] == 10
    assert "every 11 frames when no read is in flight" in hg["caveat"]


def test_rtc_bytes_are_labelled_decompressed_hg_vs_raw_hge():
    for mode, title in (("hgss", "heartgold"), ("hgss", "soulsilver"), ("hge", "heartgold_hge"), ("pt", "platinum")):
        rtc = _pack(mode)["titles"][title]["profile"]["rtc"]
        for chk in rtc["file_checks"].values():
            assert "loadArm9().sections[0]" in chk["bytes_source"] and "raw uncompressed rom.arm9" in chk["bytes_source"]
        assert "DECOMPRESSED" in rtc["bytes_source"]
    hge = _pack("hge")["titles"]["heartgold_hge"]["profile"]["rtc"]["hge_checks"]
    assert set(hge["compared"]) == set(hge["identical_to_vanilla"]) and len(hge["compared"]) == 4
    assert all(row["identical"] and len(row["sha256"]) == 64 for row in hge["compared"].values())
    assert "DECOMPRESSED HG ARM9" in hge["method"] and "RAW rom.arm9" in hge["method"]


def test_rtc_hge_identity_compare_is_real_a_mutated_byte_goes_red():
    base = 0x02000000
    mk = lambda n, a: g.Sym(n, a, 8, ".text", "x.o", "arm9")  # noqa: E731
    xm = _FakeXMap(**{n: mk(n, base + 8 * i) for i, n in enumerate(g.RTC_HGE_IDENTICAL)})
    blob = bytes(range(32))
    hg, hge = g.Images(base, blob, {}), g.Images(base, blob, {})
    out = g.rtc_hge_identity(xm, hge, hg)
    assert list(out) == list(g.RTC_HGE_IDENTICAL) and all(r["identical"] for r in out.values())
    for i in range(len(blob)):  # a flipped byte anywhere inside any of the four function bodies is caught
        bad = bytearray(blob)
        bad[i] ^= 0xFF
        with pytest.raises(g.Fail, match="hge changed"):
            g.rtc_hge_identity(xm, g.Images(base, bytes(bad), {}), hg)


@pytest.mark.parametrize("mode,title", [("hgss", "heartgold"), ("hge", "heartgold_hge")])
def test_battle_evidence_records_the_owning_struct_per_field(mode, title):
    prof = _pack(mode)["titles"][title]["profile"]
    ev, vals = prof["battle_evidence"], prof["battle"]
    assert set(ev) == set(vals) and all(ev[k]["owner"]["struct"] and ev[k]["owner"]["cite"] for k in ev)
    owner = {k: ev[k]["owner"]["struct"] for k in ev}
    assert (owner["type_off"], owner["ctx_off"], owner["outcome_off"]) == ("BattleSystem",) * 3
    assert (owner["mons_off"], owner["selected_off"], owner["fainted_flag_off"]) == ("BattleContext",) * 3
    assert (owner["hp_off"], owner["hp_width"], owner["mon_size"]) == ("BattleMon",) * 3
    assert "battle.h:527" in ev["type_off"]["owner"]["cite"] and "battle.h:394" in ev["mons_off"]["owner"]["cite"]
    assert "battle.h:248" in ev["hp_off"]["owner"]["cite"] and "accessor literal" in ev["outcome_off"]["owner"]["cite"]


# ---- card gen4-G3-pack-d7: profile.battle.d7 (the in-battle linked-faint seam lua/gen4/client.lua reads) ----------------
D7_TITLES = [("hgss", "heartgold"), ("hgss", "soulsilver"), ("hge", "heartgold_hge")]
D7_FIELDS = ("seam.cmd", "seam.overlay_id", "ctx_cmd_off", "bs_party_off", "party_hp_off", "repl_flag_off")


@pytest.mark.parametrize("mode,title", D7_TITLES)
def test_d7_block_has_the_client_field_names_and_the_proven_values(mode, title):
    from tests.unit.gen4_world import D7_MODEL
    d7 = _pack(mode)["titles"][title]["profile"]["battle"]["d7"]
    model = D7_MODEL["heartgold_hge" if mode == "hge" else "heartgold"]
    assert d7 == model  # the pack block and the client's MODEL fallback agree key for key
    assert set(d7) == {"seam", "ctx_cmd_off", "bs_party_off", "party_hp_off", "repl_flag_off"}
    seam = d7["seam"]
    assert set(seam) == ({"cmd", "overlay_id", "table", "addr", "pin_hex"} if mode == "hge" else {"cmd", "overlay_id", "addr", "pin_hex"})
    assert (seam["cmd"], seam["overlay_id"]) == ((9, 12) if mode == "hge" else (11, 12))


@pytest.mark.parametrize("mode,title", D7_TITLES)
def test_every_d7_field_carries_evidence_and_a_physical_reference(mode, title):
    prof = _pack(mode)["titles"][title]["profile"]
    ev = prof["battle_evidence"]["d7"]
    assert set(ev["fields"]) == set(D7_FIELDS)
    for key, e in ev["fields"].items():
        assert e["class"] in ("FILE", "SOURCE") and e["cite"] and e["owner"]["struct"] and e["owner"]["cite"], key
        assert e["physical"].startswith("PHYSICAL:") or title == "soulsilver", key  # HG/hge: a receipt; SS: stated absence
        assert ("pret/pokeheartgold@ad7a3afa" in e["owner"]["cite"]) == (mode == "hgss"), key
        assert ("hg-engine fork@" in e["owner"]["cite"]) == (mode == "hge"), key
    assert ev["fields"]["seam.cmd"]["class"] == ev["fields"]["repl_flag_off"]["class"] == "FILE"
    if title == "soulsilver":
        assert all(e["physical"].startswith("none: no SoulSilver run") for e in ev["fields"].values())
    else:
        assert all("battle_faint_seam.md" in e["physical"] for e in ev["fields"].values())


def test_d7_is_absent_for_platinum_with_an_explicit_open():
    pt = _pack("pt")["titles"]["platinum"]
    assert pt["profile"]["battle"] is None and "battle.d7" in pt["open"]["battle"] and "OPEN" in pt["open"]["battle"]


def _flip(images: g.Images, image: str, addr: int) -> g.Images:
    """A copy of `images` with one byte of `image` inverted at `addr`."""
    ovs = {i: (ram, bytearray(data), bss) for i, (ram, data, bss) in images.overlays.items()}
    arm9 = images.arm9
    if image == "arm9":
        arm9 = bytearray(arm9)
        arm9[addr - images.arm9_base] ^= 0xFF
    else:
        ram, data, _ = ovs[int(image[2:])]
        data[addr - ram] ^= 0xFF
    return g.Images(images.arm9_base, bytes(arm9), ovs)


def _d7_inputs():
    inputs = _need("heartgold", "soulsilver", "heartgold_hge", "heartgold_xmap")
    return (g.load_xmap(inputs.paths["heartgold_xmap"]), g.load_images(inputs.paths["heartgold"]),
            g.load_images(inputs.paths["soulsilver"]), g.load_images(inputs.paths["heartgold_hge"], raw_arm9=True))


def test_d7_pin_hex_and_seam_match_the_pinned_rom_bytes_in_all_three_roms():
    xm, hg, ss, hge = _d7_inputs()
    addr = _pack("hgss")["titles"]["heartgold"]["profile"]["battle"]["d7"]["seam"]["addr"]
    pin = _pack("hgss")["titles"]["heartgold"]["profile"]["battle"]["d7"]["seam"]["pin_hex"]
    for img in (hg, ss):  # ov12 bytes at the seam address: the pin the client registers on
        assert img.read("ov12", addr, 4).hex() == pin == g.D7_SEAM_PIN_HEX
        assert struct.unpack("<I", img.read("ov12", 0x0226CA90 + 4 * 11, 4))[0] == addr | 1  # dispatch entry 11 names it
    assert g.d7_file_checks(xm, hg, "hgss") == _pack("hgss")["titles"]["heartgold"]["profile"]["battle_d7_file_checks"]
    assert g.d7_file_checks(xm, ss, "hgss") == _pack("hgss")["titles"]["soulsilver"]["profile"]["battle_d7_file_checks"]
    checks = g.d7_file_checks(xm, hge, "hge", hg)
    assert checks == _pack("hge")["titles"]["heartgold_hge"]["profile"]["battle_d7_file_checks"]
    assert checks["table"]["entry_word"] == 0x022494DD and checks["table"]["identical_in_vanilla"] is True
    table = _pack("hge")["titles"]["heartgold_hge"]["profile"]["battle"]["d7"]["seam"]["table"]
    assert struct.unpack("<I", hge.read("ov12", table + 4 * 9, 4))[0] == 0x022494DD  # what the client reads at first use


def test_d7_revert_a_corrupted_pin_byte_a_wrong_table_entry_or_a_wrong_repl_flag_fail_the_generator(monkeypatch):
    xm, hg, ss, hge = _d7_inputs()
    assert g.d7_file_checks(xm, hg, "hgss")["pin_bytes"]["bytes"] == "f8b582b0"  # control: the real ROM passes
    for i in range(4):  # a flipped byte anywhere in the 4-byte pin is caught (HG and SS)
        for img in (hg, ss):
            with pytest.raises(g.Fail, match="PHYSICAL-proven seam pin"):
                g.d7_file_checks(xm, _flip(img, "ov12", 0x0224A70C + i), "hgss")
    with pytest.raises(g.Fail, match="not BattleControllerPlayer_UpdateFieldConditionExtra"):  # dispatch entry 11 retargeted
        g.d7_file_checks(xm, _flip(hg, "ov12", 0x0226CA90 + 4 * 11 + 1), "hgss")
    with pytest.raises(g.Fail, match="differs from the vanilla"):  # hge entry 9 no longer the shared command
        g.d7_file_checks(xm, _flip(hge, "ov12", 0x0226CA90 + 4 * 9 + 1), "hge", hg)
    with pytest.raises(g.Fail, match="differs from the vanilla"):  # no vanilla baseline: never accepted
        g.d7_file_checks(xm, hge, "hge", None)
    for img in (hg, hge):  # the replacement-flag offset: either halfword of the proven movs/lsls pair
        for off in (0x0224D540 + 0x4A, 0x0224D540 + 0x4E):
            with pytest.raises(g.Fail, match="replacement-flag halfwords"):
                g.d7_file_checks(xm, _flip(img, "ov12", off), "hge" if img is hge else "hgss", hg)
    monkeypatch.setattr(g, "D7_SEAM_PIN_HEX", "f8b582b1")  # a wrong typed constant is red too
    with pytest.raises(g.Fail, match="PHYSICAL-proven seam pin"):
        g.d7_file_checks(xm, hg, "hgss")


# ---- card gen4-G2-synth-place: profile.field_save (flags / vars / map objects / avatar state for the SYNTH place kind) ----
FIELD_SAVE_EXPECT = {  # independent of the generator: FILE-measured or summed offsets, see tests/unit/test_gen4_synth_save.py
    "hgss": {"vars": 0xDE4, "flags": 0xDE4 + 0x2E0, "map_objects": 0x2348, "location": 0x1234},
    "hge": {"vars": 0xFD4, "flags": 0xFD4 + 0x2E0, "map_objects": 0x2CC0, "location": 0x1424},
}


@pytest.mark.parametrize("mode", ["hgss", "hge"])
def test_field_save_offsets_match_the_independent_constants_and_close_on_location(mode):
    prof = _profile(mode)
    fs, want = prof["field_save"], FIELD_SAVE_EXPECT[mode]
    assert fs["vars"]["general_off"] == want["vars"] and fs["flags"]["general_off"] == want["flags"]
    assert fs["map_objects"]["general_off"] == want["map_objects"]
    assert fs["vars"]["count"] == 0x170 and fs["flags"]["count"] == 2912 and fs["flags"]["bytes"] == 364
    # SaveVarsFlags is vars[0x170] u16 + flags[364] u8 = 0x44C bytes, then its 4-byte CRC slot, then LocalFieldData
    assert want["vars"] + 0x44C + 4 == prof["location"]["file_cross_check"]["general_off_of_array"] == want["location"]
    assert fs["map_objects"]["count"] == 64 and fs["map_objects"]["stride"] == 0x50 and fs["map_objects"]["active_mask"] == 1
    f = fs["map_objects"]["fields"]
    assert (f["flags"], f["movement"], f["currentFacing"], f["mapId"], f["currentX"], f["currentY"], f["currentZ"]) == (
        0, 9, 0xD, 0x10, 0x26, 0x28, 0x2A)
    ps = fs["player_state"]
    assert (ps["player_off_in_local_field"], ps["state_off_in_local_field"], ps["state_width"]) == (0x6C, 0x70, 4)


@pytest.mark.parametrize("mode", ["hgss", "hge"])
def test_field_save_offsets_decode_the_owner_saves(mode):
    """FILE: the pack numbers applied to the real saves land on the player object, the known vars and the walking state."""
    prof, general = _profile(mode), _general(mode)
    fs, loc = prof["field_save"], prof["location"]["file_cross_check"]["general_off_of_array"]
    _, _, x, y, _ = struct.unpack_from("<5i", general, loc)
    mo, F = fs["map_objects"], fs["map_objects"]["fields"]
    entry = [mo["general_off"] + i * mo["stride"] for i in range(mo["count"])]
    active = [i for i, o in enumerate(entry) if struct.unpack_from("<I", general, o + F["flags"])[0] & mo["active_mask"]]
    players = [i for i in active if general[entry[i] + F["movement"]] == 1]
    assert players == [0]
    assert (struct.unpack_from("<h", general, entry[0] + F["currentX"])[0], struct.unpack_from("<h", general, entry[0] + F["currentZ"])[0]) == (x, y)
    assert struct.unpack_from("<H", general, fs["vars"]["general_off"] + 2 * 0x30)[0] == 155      # VAR 0x4030, shared by HG, SS and hge
    assert struct.unpack_from("<H", general, fs["vars"]["general_off"] + 2 * 0x35)[0] == 56150    # VAR 0x4035
    assert general[fs["flags"]["general_off"] + 13] == 0x04                                      # the same flag byte in both owner saves
    assert struct.unpack_from("<i", general, loc + fs["player_state"]["state_off_in_local_field"])[0] == 0
    assert all(not any(general[o : o + mo["stride"]]) for o in entry[8:])                         # the list is compact: the tail is zero


def test_field_save_evidence_is_graded_and_honest_about_hge_and_pt():
    hg, hge = _profile("hgss")["field_save"], _profile("hge")["field_save"]
    assert [hg[k]["evidence_class"] for k in ("vars", "flags", "map_objects")] == ["SOURCE+FILE", "SOURCE+FILE", "SOURCE+FILE"]
    assert [hge[k]["evidence_class"] for k in ("vars", "flags", "map_objects")] == ["DERIVED+FILE", "DERIVED+FILE", "DERIVED+FILE"]
    for fs in (hg, hge):
        for key in ("vars", "flags", "map_objects", "player_state"):
            assert fs[key]["evidence"] and fs[key]["evidence_class"]
        assert "src/save.c:733-768" in fs["vars"]["evidence"] and "include/map_object.h:6-33" in fs["map_objects"]["evidence"]
    assert "DERIVED" in hge["vars"]["file_cross_check"] and "Not read from a documented hge array table" in hge["vars"]["file_cross_check"]
    assert "field_save" not in _profile("pt")                                                   # Platinum: OPEN / absent
    for mode in ("hgss", "hge"):
        assert "tools/gen4_routes.py docstring" not in _profile(mode)["location"]["file_cross_check"]["evidence"]


# ---- card gen4-G3-pack-d7 review fixes (OMP cx-85b55e38): probe binding, whole-load pin, single repo id, hge trampoline ----
PROBE = Path(__file__).resolve().parents[2] / "lua" / "tests" / "probe_gen4_battle_faint.lua"


def _probe_ufce(text: str) -> dict:
    """The seam the PHYSICAL probe registered its exec hook on: M.SEAMS.ufce = {addr, pin (u32 of the first 4 bytes), cmd}."""
    m = re.search(r"ufce\s*=\s*\{addr\s*=\s*(0x[0-9A-Fa-f]+),\s*pin\s*=\s*(0x[0-9A-Fa-f]+),\s*cmd\s*=\s*(\d+)", text)
    assert m, "the probe SEAMS.ufce row moved"
    return {"addr": int(m[1], 16), "pin_hex": struct.pack("<I", int(m[2], 16)).hex(), "cmd": int(m[3])}


def _probe_hge_cmds(text: str) -> set:
    """The commands the probe frame poll treats as the faint seam; hge folds 9-11 into command 9."""
    m = re.search(r"if cmd == (\d+) or cmd == (\d+) or cmd == (\d+) then", text)
    assert m, "the probe command-seen row moved"
    return {int(x) for x in m.groups()}


def _assert_d7_matches_probe(text: str) -> None:
    probe = _probe_ufce(text)
    for title in ("heartgold", "soulsilver"):
        seam = _pack("hgss")["titles"][title]["profile"]["battle"]["d7"]["seam"]
        assert (seam["addr"], seam["pin_hex"], seam["cmd"]) == (probe["addr"], probe["pin_hex"], probe["cmd"]), title
    hge = _pack("hge")["titles"]["heartgold_hge"]["profile"]["battle"]["d7"]["seam"]
    hg_checks = _pack("hgss")["titles"]["heartgold"]["profile"]["battle_d7_file_checks"]
    assert hge["cmd"] in _probe_hge_cmds(text) and hge["cmd"] == 9          # hge folds commands 9-11 into command 9
    assert hge["table"] == hg_checks["table"]["address"]                      # the shared ov12 sPlayerBattleCommands
    assert _pack("hge")["titles"]["heartgold_hge"]["profile"]["battle_d7_file_checks"]["table"]["entry_word"] == 0x022494DD


def test_d7_seam_constants_equal_the_probe_that_physically_proved_them():
    text = PROBE.read_text(encoding="utf-8")
    _assert_d7_matches_probe(text)
    assert _probe_ufce(text) == {"addr": 0x0224A70C, "pin_hex": "f8b582b0", "cmd": 11}   # the typed PHYSICAL receipt values


@pytest.mark.parametrize("old,new", [("addr = 0x0224A70C", "addr = 0x0224A710"), ("pin = 0xB082B5F8", "pin = 0xB082B5F9"),
                                     ("pin = 0xB082B5F8, cmd = 11", "pin = 0xB082B5F8, cmd = 12")])
def test_mutating_the_probe_seam_row_goes_red(old, new):
    text = PROBE.read_text(encoding="utf-8")
    assert old in text
    with pytest.raises(AssertionError):
        _assert_d7_matches_probe(text.replace(old, new, 1))


@pytest.mark.parametrize("mode,title", D7_TITLES)
def test_every_owner_cite_names_the_repository_exactly_once(mode, title):
    ev = _pack(mode)["titles"][title]["profile"]["battle_evidence"]["d7"]
    repo = "hg-engine fork@" if mode == "hge" else "pret/pokeheartgold@"
    for key, e in ev["fields"].items():
        assert e["owner"]["cite"].count(repo) == 1, (key, e["owner"]["cite"])
    assert ev["owner"]["cite"].count("pret/pokeheartgold@") == 1


def test_d7_repl_flag_pin_covers_the_indexed_load_and_the_hge_trampoline_is_typed(monkeypatch):
    xm, hg, ss, hge = _d7_inputs()
    assert [o for o, _ in g.D7_REPL_PIN][:4] == [0x4A, 0x4C, 0x4E, 0x50]      # the literal AND the load that consumes it
    for img, build in ((hg, "hgss"), (ss, "hgss"), (hge, "hge")):
        assert g.d7_file_checks(xm, img, build, hg)["repl_flag"]["halfwords"][:4] == ["0x214f", "0x9807", "0x0089", "0x5842"]
    for off in (0x4C, 0x50):                                                  # the load halfwords were not covered before
        for img, build in ((hg, "hgss"), (hge, "hge")):
            with pytest.raises(g.Fail, match="replacement-flag halfwords"):
                g.d7_file_checks(xm, _flip(img, "ov12", 0x0224D540 + off), build, hg)
    hge_pin = _pack("hge")["titles"]["heartgold_hge"]["profile"]["battle_d7_file_checks"]["pin_bytes"]
    assert hge_pin["bytes"] == g.D7_HGE_TRAMPOLINE_HEX == "004a1047"          # ldr r2,[pc]; bx r2
    for i in range(4):                                                        # a flipped trampoline byte is red
        with pytest.raises(g.Fail, match="trampoline"):
            g.d7_file_checks(xm, _flip(hge, "ov12", 0x022494DC + i), "hge", hg)
    monkeypatch.setattr(g, "D7_HGE_TRAMPOLINE_HEX", "004a1048")               # and so is a wrong typed constant
    with pytest.raises(g.Fail, match="trampoline"):
        g.d7_file_checks(xm, hge, "hge", hg)


# ---- hge probe_field review fixes (OMP cx-62760365): preserved-prefix invariant, wording, cite, state semantics, 54AC ----
def test_hge_probe_field_offsets_are_a_checked_invariant_below_the_preserved_prefix(monkeypatch):
    xm, hg, ss, hge = _d7_inputs()
    checks = g.hge_field_checks(xm, hg, hge)
    pre = checks["preserved_prefix"]
    assert (pre["end"], pre["max_probe_field_offset"]) == (0xE4, 0xD8) and pre["max_probe_field_offset"] < pre["end"]
    assert g.FS_SIZE == 0x128 and pre["end"] < g.FS_SIZE
    assert checks == _pack("hge")["titles"]["heartgold_hge"]["profile"]["probe_field_hge_checks"]
    for bad in (0xE4, 0xE8, 0x108):  # a FieldSystem-level offset in the extended / follower part is red
        monkeypatch.setitem(g.PROBE_FIELD, "save_driver", (bad, "ASM", "x"))
        with pytest.raises(g.Fail, match="preserved"):
            g.hge_field_checks(xm, hg, hge)
    monkeypatch.setitem(g.PROBE_FIELD, "save_driver", (0xD8, "ASM", "x"))
    monkeypatch.setitem(g.PROBE_FIELD, "live", (0xE4, "ASM", "x"))
    with pytest.raises(g.Fail, match="preserved"):
        g.hge_field_checks(xm, hg, hge)


def test_hge_projection_wording_says_prefix_preserved_not_struct_unchanged():
    ev = _pack("hge")["titles"]["heartgold_hge"]["profile"]["probe_field_evidence"]
    for key in PROBE_KEYS - {"save", "task"}:
        cite = ev[key]["cite"]
        assert "vanilla prefix preserved; hge extends FieldSystem to 0x128" in cite, key
        assert "StoreFieldSysPtr hook" in cite and "below the preserved prefix 0xE4" in cite, key
        assert "struct unchanged" not in cite and "projected onto hge" not in cite, key


def test_the_save_driver_cite_covers_both_asm_lines_and_the_state_semantics_sit_on_the_entry():
    for mode, title in (("hgss", "heartgold"), ("hgss", "soulsilver"), ("hge", "heartgold_hge")):
        ev = _pack(mode)["titles"][title]["profile"]["probe_field_evidence"]
        assert "overlay_01_021F6830.s:91-92 (add r4,#0xd8; str r0,[r4]" in ev["save_driver"]["cite"], title
        sem = ev["save_state"]["semantics"]
        assert (sem["width"], sem["type"]) == (1, "u8") and set(sem["values"]) == {"0", "1", "2"}
        assert sem["values"]["0"].startswith("init") and sem["values"]["1"].startswith("idle") and "only value that accepts" in sem["values"]["1"]
        assert sem["values"]["2"].startswith("requested")
        assert all(ref in sem["evidence"] for ref in (":124-137", ":365-374", ":431-436"))
        assert ":365-374" in ev["save_state"]["cite"]


def test_the_running_field_map_writer_is_byte_identical_in_hge():
    xm, hg, ss, hge = _d7_inputs()
    assert "ov01_021F54AC" in g.HGE_IDENTICAL_FUNCS
    s = xm.lookup("ov01_021F54AC")
    assert hg.read(s.image, s.address, s.size) == hge.read(s.image, s.address, s.size)
    names = {f["symbol"] for f in _pack("hge")["titles"]["heartgold_hge"]["profile"]["probe_field_hge_checks"]["functions_byte_identical"]}
    assert "ov01_021F54AC" in names
    # revert: a changed byte in the writer fails the generator
    with pytest.raises(g.Fail, match="ov01_021F54AC"):
        g.hge_field_checks(xm, hg, _flip(hge, s.image, s.address + 2))


# ---- card gen4-hge-area-map: the map table and the area names are the SAME bytes in both ROMs -----
def _hge_area_inputs():
    """HG's DECOMPRESSED static image and hge's RAW arm9 -- the two sources the generator reads."""
    inputs = _need("heartgold", "heartgold_hge")
    return (g.load_images(inputs.paths["heartgold"]),
            g.load_images(inputs.paths["heartgold_hge"], raw_arm9=True), inputs)


def test_the_map_header_table_and_area_names_are_byte_identical_in_hge():
    hg, hge, inputs = _hge_area_inputs()
    assert (amap.MAP_HEADERS_SYMBOL, amap.MAP_HEADERS_IMAGE) == ("sMapHeaders", "arm9")
    assert (amap.MAP_HEADERS_ADDR, amap.MAP_HEADERS_SIZE, amap.MAP_HEADER_SIZE, amap.MAP_HEADER_COUNT) \
        == (0x020F6BE0, 0x32A0, 24, 540)
    assert amap.MAP_HEADER_COUNT * amap.MAP_HEADER_SIZE == amap.MAP_HEADERS_SIZE
    assert "map_header.c:15" in amap.MAP_HEADERS_CITE and "heartgoldus.xMAP:49069-49070" in amap.MAP_HEADERS_CITE

    # the whole proof, end to end, on the pinned ROMs: both spans, and the digests it records
    proof = amap.hge_proof(inputs)
    assert proof["identical"] is True and "byte-identical" in proof["claim"]
    heads, names = proof["map_headers"]["sha256"], proof["area_names"]["sha256"]
    assert set(heads) == set(names) == {"heartgold", "heartgold_hge"}
    assert len(set(heads.values())) == 1 and len(set(names.values())) == 1
    assert all(len(d) == 64 for d in (*heads.values(), *names.values())), "full sha256, never a prefix"
    assert (proof["map_headers"]["address"], proof["map_headers"]["size"], proof["map_headers"]["count"]) \
        == (f"{amap.MAP_HEADERS_ADDR:#010x}", amap.MAP_HEADERS_SIZE, amap.MAP_HEADER_COUNT)
    assert proof["map_headers"]["cite"] == amap.MAP_HEADERS_CITE
    assert (proof["area_names"]["narc"], proof["area_names"]["member"]) == ("a/0/2/7", 279), proof["area_names"]
    # the ROM identities are the pins themselves, taken from gen4_pins -- not retyped here
    assert proof["roms"]["heartgold"]["sha1"] == gen4_pins.ROM_SPECS["heartgold"][0]
    assert proof["roms"]["heartgold_hge"]["sha1"] == gen4_pins.ROM_SPECS["heartgold_hge"][0]

    # the spans really are the same bytes, read out of the two images
    spans = amap.map_header_spans({"heartgold": hg, "heartgold_hge": hge})
    amap.require_identical(spans, amap.MAP_HEADERS_SYMBOL)
    assert spans["heartgold"] == hg.read("arm9", amap.MAP_HEADERS_ADDR, amap.MAP_HEADERS_SIZE)
    # revert: one flipped byte anywhere in the hge table is red (the arm9 image, not an overlay)
    with pytest.raises(amap.HgeMapDrift, match=amap.MAP_HEADERS_SYMBOL):
        amap.require_identical(amap.map_header_spans({"heartgold": hg, "heartgold_hge": _flip(hge, "arm9", amap.MAP_HEADERS_ADDR + 7)}),
                               amap.MAP_HEADERS_SYMBOL)


def test_the_area_name_bank_is_byte_identical_in_hge_and_a_flipped_byte_is_red():
    inputs = _need("heartgold", "heartgold_hge")
    names = {key: amap.area_name_member(inputs.paths[key], amap.AREA_NAMES_MSG)
             for key in ("heartgold", "heartgold_hge")}
    what = f"{amap.AREA_NAMES_NARC} member {amap.AREA_NAMES_MSG}"
    amap.require_identical(names, what)
    blob = names["heartgold_hge"]
    broken = dict(names, heartgold_hge=blob[:4] + bytes([blob[4] ^ 0x01]) + blob[5:])
    with pytest.raises(amap.HgeMapDrift, match="member 279"):
        amap.require_identical(broken, what)
    # and a bank the fork shortened is a named FAIL, not a silent short read
    with pytest.raises(amap.HgeMapDrift, match="holds"):
        amap.area_name_member(inputs.paths["heartgold"], 10 ** 6)


# ---- place-pack fixes (OMP cx-3bab37d5 on 3f5f447d): F1 vecY, F3 derived key set, F4 bics/str pin, F5 :374, F6 summable offset ----
def test_map_objects_carry_vecy_and_the_height_relation_the_game_uses():
    for mode in ("hgss", "hge"):
        mo = _profile(mode)["field_save"]["map_objects"]
        assert mo["fields"]["vecY"] == 0x2C and mo["fields"]["currentY"] == 0x28 and mo["height_to_vecY_shift"] == 15
        assert "map_object.c:639-641" in mo["height_evidence"] and ":494-496" in mo["height_evidence"]
        assert "single point" in mo["height_evidence"]            # honest: one height in the owner saves


@pytest.mark.parametrize("mode", ["hgss", "hge"])
def test_every_active_owner_object_obeys_vecy_equals_currenty_shift(mode):
    prof, general = _profile(mode), _general(mode)
    mo = prof["field_save"]["map_objects"]
    F, sh = mo["fields"], mo["height_to_vecY_shift"]
    rows = []
    for i in range(mo["count"]):
        o = mo["general_off"] + i * mo["stride"]
        if struct.unpack_from("<I", general, o + F["flags"])[0] & mo["active_mask"]:
            rows.append((struct.unpack_from("<h", general, o + F["currentY"])[0], struct.unpack_from("<i", general, o + F["vecY"])[0]))
    assert rows and all(vec == cy << sh for cy, vec in rows)


def test_the_hgss_map_object_offset_is_summed_from_source_and_hge_keeps_an_honest_gap():
    hg, hge = _profile("hgss")["field_save"]["map_objects"], _profile("hge")["field_save"]["map_objects"]
    assert 0x1234 + 0x84 + 0x344 + 0x1E4 + 0x884 + 0x2E4 == 0x2348 == hg["general_off"]
    assert hg["evidence_class"] == "SOURCE+FILE" and "0x1234 + 0x84 (LocalFieldData 0x80 + CRC) + 0x344 (Pokedex)" in hg["evidence"]
    assert "0x2348" in hg["evidence"] and "cannot be summed" not in hg["evidence"] + hg["file_cross_check"]
    assert "not explained here" in hge["evidence"] or "NOT explained here" in hge["evidence"]
    assert hge["evidence_class"] == "DERIVED+FILE" and hge["general_off"] == 0x2CC0 == 0x2348 - 0x1234 + 0x1424 + 0x788
    assert "cannot be summed" not in hge["evidence"] + hge["file_cross_check"]


def test_hge_probe_field_key_set_is_the_complement_of_the_declared_non_fieldsystem_keys(monkeypatch):
    xm, hg, ss, hge = _d7_inputs()
    assert set(g.NON_FS_PROBE_KEYS) == {"launched_app", "field_app", "paused", "save_state", "save_driver_data_off"}
    assert set(g.NON_FS_PROBE_KEYS) <= {*g.PROBE_FIELD, *g.PROBE_FIELD_EXTRA}
    pre = g.hge_field_checks(xm, hg, hge)["preserved_prefix"]
    assert pre["probe_field_keys"] == sorted({*g.PROBE_FIELD, *g.PROBE_FIELD_EXTRA} - set(g.NON_FS_PROBE_KEYS))
    assert pre["max_probe_field_offset"] == 0xD8 and "sub" in pre["probe_field_keys"] and "paused" not in pre["probe_field_keys"]
    monkeypatch.setitem(g.PROBE_FIELD, "brand_new_key", (0x108, "SOURCE", "x"))   # a NEW FieldSystem-level key past the prefix
    with pytest.raises(g.Fail, match="preserved"):
        g.hge_field_checks(xm, hg, hge)
    monkeypatch.setitem(g.PROBE_FIELD, "brand_new_key", (0x40, "SOURCE", "x"))    # inside the prefix is fine and is now covered
    assert g.hge_field_checks(xm, hg, hge)["preserved_prefix"]["probe_field_keys"].count("brand_new_key") == 1


def test_repl_flag_pin_covers_the_bics_and_the_store_that_clear_bit_zero():
    xm, hg, ss, hge = _d7_inputs()
    assert [o for o, _ in g.D7_REPL_PIN] == [0x4A, 0x4C, 0x4E, 0x50, 0x54, 0x58]
    assert [h for _, h in g.D7_REPL_PIN][4:] == [0x4382, 0x5042]               # bics r2,r0 ; str r2,[r0,r1] (ctx->unk_13C[b] &= ~1)
    for img, build in ((hg, "hgss"), (ss, "hgss"), (hge, "hge")):
        assert g.d7_file_checks(xm, img, build, hg)["repl_flag"]["halfwords"] == [
            "0x214f", "0x9807", "0x0089", "0x5842", "0x4382", "0x5042"]
    for off in (0x54, 0x58):
        for img, build in ((hg, "hgss"), (ss, "hgss"), (hge, "hge")):
            with pytest.raises(g.Fail, match="replacement-flag halfwords"):
                g.d7_file_checks(xm, _flip(img, "ov12", 0x0224D540 + off), build, hg)
    ev = _pack("hgss")["titles"]["heartgold"]["profile"]["battle_evidence"]["d7"]["fields"]["repl_flag_off"]["cite"]
    assert "bics r2,r0" in ev and "unk_13C[b] &= ~1" in ev


def test_the_save_state_cite_runs_to_the_strb_at_line_374():
    for mode, title in (("hgss", "heartgold"), ("hgss", "soulsilver"), ("hge", "heartgold_hge")):
        ev = _pack(mode)["titles"][title]["profile"]["probe_field_evidence"]["save_state"]
        assert ":365-374" in ev["cite"] and ":365-374" in ev["semantics"]["evidence"], title
        assert ":365-373" not in ev["cite"] and ":365-373" not in ev["semantics"]["evidence"], title


@pytest.mark.parametrize("mode", ["hgss", "hge"])
def test_every_site_symbol_resolves_for_the_probe_validator(mode):
    """probe_gen4_hooks.lua M.validate_site needs title.symbols[site.symbol] with the site's address/image/mode
    (REPLACED sites resolve through hge_replacements instead); a site without one fails closed live (hge census c)."""
    for title in _pack(mode)["titles"].values():
        for site_id, site in title["sites"].items():
            if site.get("hge_status") == "REPLACED":
                continue
            sym = title["symbols"].get(site["symbol"])
            assert sym is not None, f"{site_id}: symbol {site['symbol']} absent from title.symbols"
            assert (sym["address"], sym["image"], sym["mode"]) == (site["address"], site["image"], site["mode"]), site_id


@pytest.mark.parametrize("mode", ["hgss", "hge"])
def test_d7_seam_carries_its_file_pin_on_every_build(mode):
    """The client arms the D7 seam on table-active AND this pin; a pin it had to read from RAM would be minted
    from the very bytes an in-flight async ov12 load has not replaced yet (the residency race)."""
    for title in _pack(mode)["titles"].values():
        seam = title["profile"]["battle"]["d7"]["seam"]
        assert isinstance(seam.get("addr"), int) and seam["addr"] % 2 == 0, seam
        assert isinstance(seam.get("pin_hex"), str) and len(seam["pin_hex"]) == 8, seam


@pytest.mark.parametrize("mode", ("hgss", "hge"))
def test_generated_battle_close_uses_template_exit_full_pin(mode):
    for title in _pack(mode)["titles"].values():
        case = next(c for c in title["phase_cases"] if c["name"] == "battle_close")
        site = title["sites"][case["producer_site"]]
        assert site["symbol"] == "Battle_Exit" and site["image"] == "arm9"
        assert site["address"] == case["predicate_file_check"]["exit"] & ~1
        assert bytes.fromhex(site["register_hex"])[:4] == bytes.fromhex("01207047")
        assert site["extent"] == len(bytes.fromhex(site["register_hex"])) >= 4
        assert "src/launch_application.c:174-176" in site["source"]
        assert case["predicate"]["value"] == 12
        assert case["route"] == [g.BS, g.RUN, g.EXIT_LEG]
        assert case["close_boundary"] is True


@pytest.mark.parametrize("mode", ("hgss", "hge"))
def test_pc_deposit_case_keeps_unexercised_delete_withdraw_release_open(mode):
    for title in _pack(mode)["titles"].values():
        pc = next(c for c in title["phase_cases"] if c["name"] == "pc")
        assert pc["fixture_role"] == "pc_case" and pc["predicate"]["value"] == 14
        assert pc["route"] == [
            "gen4_pc:reach_pc_terminal",
            "pc_open_storage",
            "pc_deposit_first_party_mon",
            "pc_exit_app",
        ]
        assert title["sites"][pc["producer_site"]]["image"] == "arm9"
        blocked = title["phase_cases_blocked"][0]
        assert (
            blocked["name"] == "pc_withdraw_release"
            and "pc_delete_by_index_pair" in blocked["sites"]
        )
        assert "pc_withdraw_box_mon" in blocked["route"] and "RELEASE" in blocked["blocked_reason"]
        if mode == "hge":
            assert pc["producer_site"] == "pc_place_arm9_entry" and len(pc["sites"]) == 2
            replacement = title["sites"]["pc_place_first_in_box"]
            static = title["sites"][pc["producer_site"]]
            assert static["address"] == replacement["replaces"]["vanilla_address"]
            assert static["register_hex"] == replacement["replaces"]["vanilla_entry_hex"]
            assert static["register_hex"] != replacement["replaces"]["vanilla_register_hex"]
            assert replacement["image"] == "ov129"
            good = copy.deepcopy(title)
            pc["producer_site"] = "pc_place_first_in_box"
            assert any("static_pc" in e for e in g.validate_phase_cases(title))
            assert not g.validate_phase_cases(
                good
            )  # revert: the genuine ARM9 trampoline remains required


@pytest.mark.parametrize("title", ("heartgold", "soulsilver"))
def test_pc_deposit_observes_the_indexed_native_transfer(title):
    # ad7a3afa overlay_14.s:7582-7596 FindFirstEmptySlot -> explicit destination;
    # :2641-2649 work+E4/E8; :1404-1428 -> ov14_021E61BC; :1152 -> ByIndexPair.
    # PHYSICAL rows69 HG phase-5 deposited/saved correctly while the old
    # FirstEmptySlot oracle AND registry both counted zero.
    t = _pack("hgss")["titles"][title]
    case = next(c for c in t["phase_cases"] if c["name"] == "pc")
    assert case["producer_site"] == "pc_place_by_index_pair"
    assert case["sites"] == ["pc_place_by_index_pair"]
    assert t["sites"][case["producer_site"]]["symbol"] == "PCStorage_PlaceMonInBoxByIndexPair"
    rows = case["caller_matrix"]["sites"]
    assert [row["site"] for row in rows] == ["pc_place_by_index_pair"]
    covered = [c for c in rows[0]["callers"] if c["exercised_by_route"]]
    assert len(covered) == 1 and "ov14_021E61BC" in covered[0]["caller"]
    assert covered[0]["exercised_by"] == ["pc_deposit_first_party_mon"]
    assert "pc_place_by_index_pair" not in t["phase_cases_excluded"]["pc"]
    blocked = t["phase_cases_blocked"][0]
    old_site = next(r for r in blocked["caller_matrix"]["sites"] if r["site"] == "pc_place_first_in_box")
    assert all(not c["exercised_by_route"] for c in old_site["callers"])
    assert all(c["why_open"] for c in old_site["callers"])


@pytest.mark.parametrize("title", ("heartgold", "soulsilver"))
def test_indexed_deposit_monitor_red_and_revert(title):
    """Replay the zero-oracle PHYSICAL failure through the actual registry/monitor.

    This is a SOURCE-shaped native event, not a claim about live callback timing.
    overlay_14.s:7582-7596 resolves a slot <30 (bit0x80 clear); :1428,1152 uses
    ByIndexPair. Selecting FirstEmptySlot must still FAIL the unchanged validator.
    """
    from lupa import LuaRuntime

    rt = LuaRuntime(unpack_returned_tuples=True)
    root = Path(__file__).resolve().parents[2]
    rt.globals().SLINK_GEN4_PROBE_TEST = True
    rt.globals().M = rt.execute((root / "lua/tests/probe_gen4_hooks.lua").read_text())
    rt.globals().Registry = rt.execute((root / "lua/hook_registry.lua").read_text())

    def lua(value):
        if isinstance(value, dict):
            return rt.table_from({key: lua(v) for key, v in value.items()})
        if isinstance(value, list):
            return rt.table_from([lua(v) for v in value])
        return value

    t = _pack("hgss")["titles"][title]
    rt.globals().title = lua(t)
    replay = rt.eval("""function(selected)
      local callbacks, serial = {}, 0
      local frame, active = 100, true
      local producer = title.sites[selected]; producer.id = selected
      local settle = {current={}, samples={}}
      local oracle = {}
      local function bytes(address)
        for _,s in pairs(title.sites) do if s.address == address then return s.register_hex end end
        error("unknown site")
      end
      local function register(s,cb)
        serial=serial+1; local id="model-"..serial
        callbacks[id]={address=s.address,cb=cb}; return id
      end
      local function remove(id) callbacks[id]=nil; return true end
      register(producer,function(a,v)
        if active and bytes(a)==producer.register_hex then
          assert(a==producer.address and v==tonumber(producer.fire_hex,16))
          oracle[#oracle+1]=frame
        end
      end)
      local comp=M.composite(Registry,{
        validate=function(s) M.validate_site(title,s,bytes,function() return true end); return s end,
        register=register, unregister=remove,
        capture=function(s,a,v,flags)
          local e=M.capture(s,a,v,flags,a+4,function() return true end)
          if e then e.frame=frame; e.step_id=1 end; return e
        end})
      local mon,state=M.phase_monitor(comp,"pc",{producer},selected,function() return active end,
        function(want) return M.phase_sites_ready({producer},bytes,function() return true end,
          frame,settle,{max_frames=16},{},want) end,function() return frame end)
      mon.before()
      local native=title.sites.pc_place_by_index_pair
      for _,h in pairs(callbacks) do if h.address==native.address then
        h.cb(native.address,tonumber(native.fire_hex,16),0)
      end end
      mon.after(); active=false; frame=frame+1; mon.after(); mon.before(); mon.finish()
      local physical={phase="pc",first_expected=#oracle,last_expected=#oracle,
        first_seen=#state.seen,last_seen=#state.seen,peak=comp.peak+1,
        live_after_close=comp:live_handles(),second_drain=state.second_drain,
        max_cost=0,baseline_fps=1,restored_fps=1,close_boundary_required=false}
      local status,why=M.evaluate_n_case(physical)
      return status,why,physical.first_expected,physical.first_seen
    end""")
    selection = next(c for c in t["phase_cases"] if c["name"] == "pc")["producer_site"]
    assert replay(selection) == ("PASS", None, 1, 1)
    status, why, expected, seen = replay("pc_place_first_in_box")
    assert status == "FAIL" and "first/last producer lost" in why and expected == seen == 0
    assert replay(selection) == ("PASS", None, 1, 1)  # revert restores the real selection


def test_blocked_pc_delete_callers_are_each_explicitly_open():
    for mode in ("hgss", "hge"):
        for title in _pack(mode)["titles"].values():
            blocked = title["phase_cases_blocked"][0]
            row = next(s for s in blocked["caller_matrix"]["sites"] if s["site"] == "pc_delete_by_index_pair")
            assert row["callers"] and all(not c["exercised_by_route"] and c["why_open"] for c in row["callers"])
            assert all(any(c["caller"] in reason for reason in blocked["open"]) for c in row["callers"])
