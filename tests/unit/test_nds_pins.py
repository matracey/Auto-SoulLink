"""Independent input/receipt negatives; no installed ROM is needed here."""
import json
import re
import struct
import zlib
from dataclasses import replace
from pathlib import Path

import pytest

from tests.unit.test_nds_image import synthetic
from tools.nds_image import AutoloadBlock, AutoloadInfo, ChangedSpan, NdsImage, digest
from tools.nds_pins import (
    CAPABILITY_BITS,
    NATIVE_ABI_VERSION,
    CompanionPin,
    ContainerPin,
    Distribution,
    Hashes,
    NamedHash,
    NativeArena,
    NoTouchSpan,
    ParentPin,
    PatchSet,
    PinError,
    SitePin,
    SourceBuild,
    VanillaReproduction,
    _hints,
    verify_distribution,
    verify_output,
    verify_parent,
    verify_sites_before,
)

CAPS = (1 << CAPABILITY_BITS["DURABLE_TRADE"]) | (1 << CAPABILITY_BITS["INFO_PANEL"]) | (1 << CAPABILITY_BITS["NATIVE_SOUND"])


def crc16(data):
    value = 0xFFFF
    for byte in data:
        value ^= byte
        for _ in range(8):
            value = (value >> 1) ^ (0xA001 if value & 1 else 0)
    return value


def with_u32(data, offset, value):
    out = bytearray(data)
    struct.pack_into("<I", out, offset, value)
    return bytes(out)


def container_pin(image, name="arm9", *, source_provided=False, compressed=True):
    decoded = image.decoded(name, arm9_compressed=compressed)
    ov = image.overlays.get(name)
    return ContainerPin(name, digest(image.read(name)), digest(decoded), ov.file_id if ov else None,
                        ov.ram_base if ov else image.arm9_ram_base, len(decoded),
                        ov.compressed if ov else compressed, source_provided)


def noop_site(image, *, compressed=True, sid="noop"):
    before = image.decoded("arm9", arm9_compressed=compressed)[0x4400:0x4404].hex()
    return SitePin(sid, "arm9", "thumb", before, before, image.arm9_ram_base + 0x4404, "FILE", offset=0x4400)


def build(base, **over):
    fields = {"repo": "https://example.test/engine", "commit": "a" * 40, "toolchain": "pinned test compiler",
              "toolchain_binary_sha256": "2" * 64, "base_rom_sha1": digest(base.data, "sha1"),
              "patch_set": PatchSet("b" * 40, "4" * 64), "vanilla_reproduction_waived": "synthetic fixture"}
    fields.update(over)
    return SourceBuild(**fields)


def make_table(base, output, sites=(), *, source_built=False, containers=None, **over):
    fields = {
        "schema_version": 1, "title": "synthetic-title",
        "source_kind": "source_built" if source_built else "byte_patched",
        "parent": None if source_built else ParentPin.of(base),
        "source_build": build(base) if source_built else None,
        "output_arm9_compressed": True, "arm9_ram_base": output.arm9_ram_base,
        "containers": containers or (container_pin(output),), "sites": tuple(sites), "no_touch_spans": (),
        "dsi_preserved": not source_built, "output": Hashes.of(output),
        "distribution": None if source_built else Distribution(
            "ups", digest(b"patch artifact"), digest(base.data, "sha1"), digest(output.data, "sha1"), True),
        "generator_hashes": (NamedHash("fixture-generator", "1" * 64),),
        "source_hashes": (NamedHash("source", "3" * 64),), "native_abi": NATIVE_ABI_VERSION, "capabilities": CAPS}
    if source_built and not sites:
        fields["sites_reason"] = "synthetic fixture has no hook sites"
    fields.update(over)
    return CompanionPin(**fields)


def patched(cls=NdsImage):
    parent = NdsImage.load(synthetic())
    decoded = parent.decoded("arm9", arm9_compressed=True)
    before = decoded[0x4400:0x4408]
    parent.edit_arm9(parent.arm9_ram_base + 0x4400, before, b"PATCHED!", arm9_compressed=True,
                     arm9_ram_base=parent.arm9_ram_base, reason="native detour")
    output, manifest = parent.apply()
    image = cls.load(output)
    site = SitePin("boot", "arm9", "thumb", before.hex(), b"PATCHED!".hex(),
                   parent.arm9_ram_base + 0x4408, "FILE", address=parent.arm9_ram_base + 0x4400)
    return parent, image, manifest, make_table(parent, image, (site,))


def appended_overlay_case():
    """B2W2 NAMING shape: the parent's y9 lacks overlay9:1 (it is an unowned file there).

    This models a y9 table-visibility flip, NOT a real append: the payload already exists in
    the parent as file:1. Container names resolve against the PARENT extent map, so a genuinely
    new payload would have to be labelled with a synthesized gap name (padding_ff@<off> /
    unmapped@<off>) in the manifest.
    """
    out_bytes = synthetic(dsi=False)
    output = NdsImage.load(out_bytes)
    raw = bytearray(out_bytes)
    struct.pack_into("<I", raw, 0x54, 32)  # parent y9 table lists one overlay
    struct.pack_into("<H", raw, 0x15E, crc16(raw[:0x15E]))
    parent = NdsImage.load(bytes(raw))
    assert "overlay9:1" not in parent.containers and "file:1" in parent.containers
    manifest = tuple(ChangedSpan(off, n, "header", digest(parent.data, "sha1", off, n),
                                 digest(output.data, "sha1", off, n), "append overlay record")
                     for off, n in ((0x54, 4), (0x15E, 2)))
    ov = output.overlays["overlay9:1"]
    new_site = SitePin("new-ov", "overlay9:1", "thumb", None, output.decoded("overlay9:1")[:4].hex(),
                       ov.ram_base + 4, "FILE", address=ov.ram_base)
    table = make_table(parent, output, (noop_site(output), new_site),
                       containers=(container_pin(output), container_pin(output, "overlay9:1", source_provided=True)))
    return parent, output, manifest, table


def test_byte_patch_roundtrip_parent_sites_output_distribution_identity():
    parent, output, manifest, table = patched()
    decoded = CompanionPin.from_json(table.to_json())
    assert decoded == table
    assert verify_parent(parent, decoded)
    assert verify_sites_before(parent, decoded) == (("boot",), ())
    assert verify_output(output, decoded, manifest, parent=parent)
    artifact = b"patch artifact"
    out = bytes(output.data)
    d = replace(table.distribution, artifact_sha256=digest(artifact))
    assert verify_distribution(parent, artifact, lambda base, art: out, replace(table, distribution=d))
    assert verify_output(output, table, [m.to_dict() for m in manifest], parent=parent)  # dict rows accepted


def test_mutated_parent_and_wrong_expected_site_fail():
    parent, output, manifest, table = patched()
    bad = bytearray(parent.data)
    bad[-1] ^= 1
    with pytest.raises(PinError, match="parent identity"):
        verify_parent(NdsImage.load(bytes(bad)), table)
    wrong = replace(table, sites=(replace(table.sites[0], expected_before="ff" * 8),))
    with pytest.raises(PinError, match="expected-before"):
        verify_sites_before(parent, wrong)
    with pytest.raises(PinError, match="actual parent"):
        verify_output(output, table, manifest)


def test_undeclared_difference_even_with_rebound_output_hash_fails_as_pin_error():
    parent, output, manifest, table = patched()
    bad = bytearray(output.data)
    bad[-1] ^= 1
    bad = NdsImage.load(bytes(bad))
    table = replace(table, output=Hashes.of(bad),
                    distribution=replace(table.distribution, output_sha1=digest(bad.data, "sha1")))
    with pytest.raises(PinError, match="undeclared"):
        verify_output(bad, table, manifest, parent=parent)


def test_no_touch_span_cannot_even_overlap_broad_declared_edit():
    parent, output, manifest, table = patched()
    ext = parent.extent("arm9")
    assert parent.data[ext.offset:ext.offset + 4] == output.data[ext.offset:ext.offset + 4]
    span = NoTouchSpan(ext.offset, 4, digest(parent.data, start=ext.offset, length=4))
    with pytest.raises(PinError, match="overlaps no-touch"):
        verify_output(output, replace(table, no_touch_spans=(span,)), manifest, parent=parent)


def test_after_bytes_and_container_hashes_are_verified():
    parent, output, manifest, table = patched()
    wrong = replace(table, sites=(replace(table.sites[0], after=b"PATCHED?".hex()),))
    with pytest.raises(PinError, match="site after"):
        verify_output(output, wrong, manifest, parent=parent)
    wrong = replace(table, containers=(replace(table.containers[0], raw_sha256="0" * 64),))
    with pytest.raises(PinError, match="container hash"):
        verify_output(output, wrong, manifest, parent=parent)


def test_manifest_and_missing_container_errors_are_pin_errors():
    parent, output, manifest, table = patched()
    for bad in ([{"offset": 0}], ["not a span"], [replace(manifest[0], offset=-1)],
                [replace(manifest[0], container="nonexistent")]):
        with pytest.raises(PinError):
            verify_output(output, table, bad, parent=parent)
    ghost_pin = ContainerPin("overlay9:9", "1" * 64, "1" * 64, 9, 0x02150000, 16, False)
    source = make_table(output, output, (), source_built=True, containers=(container_pin(output), ghost_pin))
    with pytest.raises(PinError, match="no container"):
        verify_output(output, source, ())
    ghost = replace(table, containers=table.containers + (ghost_pin,))
    with pytest.raises(PinError):
        verify_sites_before(parent, replace(table, sites=(replace(
            table.sites[0], container="overlay9:9", address=None, offset=0),),
            containers=ghost.containers))


def test_decoded_containers_are_memoised_within_one_verification():
    class Counting(NdsImage):
        calls = None

        def decoded(self, container, *, arm9_compressed=None):
            if self.calls is not None:
                self.calls[(container, arm9_compressed)] = self.calls.get((container, arm9_compressed), 0) + 1
            return super().decoded(container, arm9_compressed=arm9_compressed)

    parent, output, manifest, table = patched(Counting)
    output.calls = {}
    assert verify_output(output, table, manifest, parent=parent)
    assert output.calls == {("arm9", True): 1}


def test_hint_cache_is_shared():
    assert _hints(ContainerPin) is _hints(ContainerPin)


def full_source_example():
    """Synthetic hge-shaped table with EVERY optional field populated."""
    return hge_example()[2]


WHERES = ["root", "parent", "hashes", "container", "site", "input"]


@pytest.mark.parametrize("where", WHERES)
def test_unknown_keys_rejected_at_every_depth_byte_patched(where):
    *_, table = patched()
    d = json.loads(table.to_json())
    target = {"root": d, "parent": d["parent"], "hashes": d["output"], "container": d["containers"][0],
              "site": d["sites"][0], "input": d["generator_hashes"][0]}[where]
    target["silent_extension"] = True
    with pytest.raises(PinError, match="unknown keys"):
        CompanionPin.from_dict(d)


@pytest.mark.parametrize("where", ["source_build", "patch_set", "tracked", "no_touch", "distribution",
                                   "distribution_base", "reference_build", "native_arena"])
def test_unknown_keys_rejected_at_every_depth_source_built(where):
    table = full_source_example()
    d = json.loads(table.to_json())
    target = {"source_build": d["source_build"], "patch_set": d["source_build"]["patch_set"],
              "tracked": d["source_build"]["tracked_inputs"][0], "no_touch": d["no_touch_spans"][0],
              "distribution": d["distribution"], "distribution_base": d["distribution_base"],
              "reference_build": d["reference_build"], "native_arena": d["native_arena"]}[where]
    target["silent_extension"] = True
    with pytest.raises(PinError, match="unknown keys"):
        CompanionPin.from_dict(d)
    assert CompanionPin.from_json(table.to_json()) == table


def test_old_names_are_unknown_keys():
    *_, table = patched()
    for old in ("protected_spans", "randomizer_exclusion_spans", "arm9_compressed", "patch_artifact_sha256"):
        d = json.loads(table.to_json())
        d[old] = []
        with pytest.raises(PinError, match="unknown keys"):
            CompanionPin.from_dict(d)


def test_duplicate_keys_bad_version_bool_integer_and_missing_hash_rejected():
    *_, table = patched()
    with pytest.raises(PinError, match="duplicate JSON"):
        CompanionPin.from_json('{"schema_version":1,"schema_version":1}')
    for field, value in (("schema_version", 2), ("native_abi", True), ("output_arm9_compressed", 1)):
        d = json.loads(table.to_json())
        d[field] = value
        with pytest.raises(PinError):
            CompanionPin.from_dict(d)
    d = json.loads(table.to_json())
    del d["output"]["sha256"]
    with pytest.raises(PinError, match="missing"):
        CompanionPin.from_dict(d)


def test_source_built_is_complete_without_byte_parent_or_distribution_step():
    image = NdsImage.load(synthetic(dsi=False))
    site = SitePin("new-overlay-entry", "overlay9:0", "thumb", None,
                   image.decoded("overlay9:0")[:4].hex(), 0x02150004, "SOURCE", offset=0)
    table = make_table(image, image, (site,), source_built=True,
                       containers=(container_pin(image), container_pin(image, "overlay9:0", source_provided=True)))
    assert table.parent is None and table.distribution is None
    assert CompanionPin.from_json(table.to_json()) == table
    assert verify_output(image, table, ())
    assert verify_sites_before(image, table) == ((), ("new-overlay-entry",))  # nothing verified, explicitly
    with pytest.raises(PinError, match="no byte parent"):
        verify_parent(image, table)
    for field, value in (("commit", "ad7a3afa"), ("toolchain_binary_sha256", ""),
                         ("repo", ""), ("base_rom_sha1", "")):
        d = json.loads(table.to_json())
        d["source_build"][field] = value
        with pytest.raises(PinError):
            CompanionPin.from_dict(d)


def test_source_built_can_pin_raw_arm9_without_decompression():
    image = NdsImage.load(synthetic(compressed=False, dsi=False))
    table = make_table(image, image, (), source_built=True, output_arm9_compressed=False,
                       containers=(container_pin(image, compressed=False),))
    assert verify_output(image, table, ())


def test_source_build_before_sites_use_explicit_base_compression():
    base = NdsImage.load(synthetic(compressed=True, dsi=False))
    output = NdsImage.load(synthetic(compressed=False, dsi=False))
    before = base.decoded("arm9", arm9_compressed=True)[0x4400:0x4404]
    site = SitePin("existing", "arm9", "thumb", before.hex(), before.hex(), base.arm9_ram_base + 0x4404, "FILE",
                   offset=0x4400)
    table = make_table(base, output, (site,), source_built=True, output_arm9_compressed=False,
                       parent=ParentPin.of(base), source_build=build(base, base_arm9_compressed=True),
                       containers=(container_pin(output, compressed=False),))
    assert verify_sites_before(base, table) == (("existing",), ())
    with pytest.raises(PinError, match="preimages require actual parent"):
        verify_output(output, table, ())
    with pytest.raises(PinError, match="base compression"):
        replace(table, source_build=replace(table.source_build, base_arm9_compressed=None))


def test_source_built_dsi_preservation_cannot_be_claimed_without_baseline():
    image = NdsImage.load(synthetic())
    table = make_table(image, image, source_built=True, dsi_preserved=True)
    with pytest.raises(PinError, match="DSi preservation needs"):
        verify_output(image, table, ())


def test_site_bounds_alignment_and_duplicate_ids():
    *_, table = patched()
    with pytest.raises(PinError, match="outside"):
        replace(table, sites=(replace(table.sites[0], address=0x02200000),))
    with pytest.raises(PinError, match="unaligned"):
        replace(table.sites[0], address=0x02004001)
    with pytest.raises(PinError, match="address OR offset"):
        replace(table.sites[0], offset=4)
    with pytest.raises(PinError, match="duplicate site"):
        replace(table, sites=(table.sites[0], table.sites[0]))


ARM_BX_LR = bytes.fromhex("1eff2fe1") * 2  # two ARM `bx lr`: 0xFF1E is an invalid first Thumb halfword


def test_site_after_must_decode_under_its_declared_isa():
    *_, table = patched()
    site = table.sites[0]  # thumb, 8 bytes, address-form
    with pytest.raises(PinError, match="not a thumb instruction"):
        replace(site, after=ARM_BX_LR.hex())
    assert replace(site, isa="arm", after=ARM_BX_LR.hex())  # the same word is fine under its own ISA
    assert replace(site, after=ARM_BX_LR.hex(), data=True)  # explicit data site opts out of the decode
    # An unchanged (before == after) site pins existing bytes and is not decoded.
    assert replace(site, expected_before=ARM_BX_LR.hex(), after=ARM_BX_LR.hex())
    # A new site (no preimage) is decoded; a half Thumb BL pair is not an instruction.
    with pytest.raises(PinError, match="split"):
        replace(site, expected_before=None, after="00f0")
    assert replace(site, expected_before=None, after="00f000f8")  # a complete BL pair
    with pytest.raises(PinError, match="bool"):
        replace(site, data=1)


def test_site_continuation_must_be_in_extent_and_outside_the_site():
    *_, table = patched()
    site = table.sites[0]  # address base+0x4400, 8 bytes, continuation base+0x4408
    for bad in (site.address, site.address + 4, 0x10, 0x01000000, 0x02004000 + table.containers[0].ram_size + 4):
        with pytest.raises(PinError, match="continuation"):
            replace(table, sites=(replace(site, continuation=bad),))
    assert replace(table, sites=(replace(site, continuation=site.address - 4),))  # resuming earlier is legal
    assert replace(table, sites=(replace(site, continuation=site.address + 12),))


@pytest.mark.parametrize("isa,alignment", [("arm", 4), ("thumb", 2)])
def test_continuation_uses_half_open_container_extents(isa, alignment):
    *_, table = patched()
    container = table.containers[0]
    end = container.ram_base + container.ram_size
    site = replace(table.sites[0], isa=isa,
                   after=ARM_BX_LR.hex() if isa == "arm" else table.sites[0].after)
    assert replace(table, sites=(replace(site, continuation=end - alignment),))
    with pytest.raises(PinError, match="continuation"):
        replace(table, sites=(replace(site, continuation=end),))
    adjacent = replace(container, name="overlay9:99", file_id=99, ram_base=end, ram_size=32)
    assert replace(table, containers=(container, adjacent), sites=(replace(site, continuation=end),))


def test_per_container_site_overlap_but_not_cross_container_overlap():
    image = NdsImage.load(synthetic(dsi=False))
    a = noop_site(image, sid="a")
    overlapping = replace(a, id="b", offset=0x4402, continuation=a.continuation + 2)
    with pytest.raises(PinError, match="overlapping site"):
        make_table(image, image, (a, overlapping))
    ov_before = image.decoded("overlay9:0")[0:4].hex()
    other = SitePin("ov", "overlay9:0", "thumb", ov_before, ov_before, image.overlays["overlay9:0"].ram_base + 4,
                    "FILE", offset=0)
    make_table(image, image, (a, other),
               containers=(container_pin(image), container_pin(image, "overlay9:0")))


def test_overlay_sites_use_decompressed_container_not_aliased_address_alone():
    parent = NdsImage.load(synthetic())
    ov = parent.overlays["overlay9:0"]
    before = parent.decoded(ov.name)[32:36]
    parent.edit_overlay(0, ov.ram_base + 32, before, b"\x00\xb5\x00\xbd", reason="overlay instruction")
    output, manifest = parent.apply()
    output = NdsImage.load(output)
    site = SitePin("overlay", ov.name, "thumb", before.hex(), "00b500bd", ov.ram_base + 36, "FILE",
                   address=ov.ram_base + 32)
    table = make_table(parent, output, (site,), containers=(container_pin(output), container_pin(output, ov.name)))
    assert table.containers[1].compressed is True and output.overlays["overlay9:1"].compressed is False
    assert verify_sites_before(parent, table) == (("overlay",), ())
    assert verify_output(output, table, manifest, parent=parent)


def test_two_overlays_sharing_one_load_address_are_distinct_by_name():
    image = NdsImage.load(synthetic(dsi=False))
    assert image.overlays["overlay9:0"].ram_base == image.overlays["overlay9:1"].ram_base
    sites = []
    for name in ("overlay9:0", "overlay9:1"):
        before = image.decoded(name)[16:20].hex()
        sites.append(SitePin(f"hook-{name}", name, "thumb", before, before,
                             image.overlays[name].ram_base + 20, "FILE", address=image.overlays[name].ram_base + 16))
    table = make_table(image, image, sites, source_built=True, parent=ParentPin.of(image),
                       containers=(container_pin(image), container_pin(image, "overlay9:0"),
                                   container_pin(image, "overlay9:1")))
    assert verify_output(image, table, (), parent=image)
    for bare in ("ram:0x02150010", "0x02150000-0x02150200", "overlay9", "overlay9:01"):
        with pytest.raises(PinError, match="container"):
            replace(sites[0], container=bare)
    d = json.loads(table.to_json())
    del d["sites"][0]["container"]
    with pytest.raises(PinError, match="missing"):
        CompanionPin.from_dict(d)


def test_dsi_preservation_checks_even_allowlisted_payload_mutation():
    parent = NdsImage.load(synthetic())
    parent.edit(container="dsi_arm9i", expected_before=b"9i", after=b"XX", reason="bad preservation")
    output, manifest = parent.apply()
    output = NdsImage.load(output)
    table = make_table(parent, output, (noop_site(parent),))
    with pytest.raises(PinError, match="DSi payload"):
        verify_output(output, table, manifest, parent=parent)


# ---- F1: appended (parent-absent) overlay under byte_patched -------------------------------

def test_byte_patched_may_pin_sites_in_a_parent_absent_overlay():
    parent, output, manifest, table = appended_overlay_case()
    assert CompanionPin.from_json(table.to_json()) == table
    assert verify_sites_before(parent, table) == (("noop",), ("new-ov",))
    assert verify_output(output, table, manifest, parent=parent)


def test_source_provided_rules_follow_the_parent_extent_map():
    parent, output, manifest, table = appended_overlay_case()
    # The parent really has the container: source_provided must be refused.
    with_parent = replace(table, parent=ParentPin.of(output),
                          distribution=replace(table.distribution, input_sha1=digest(output.data, "sha1")))
    with pytest.raises(PinError, match="exists in the parent"):
        verify_output(output, with_parent, (), parent=output)
    # A parent-absent container not flagged source_provided is refused.
    plain = (table.containers[0], replace(table.containers[1], source_provided=False))
    with pytest.raises(PinError, match="must be source_provided"):
        verify_output(output, replace(table, containers=plain, sites=(table.sites[0],)), manifest, parent=parent)
    # Schema: preimage-less sites need a source-provided container; byte_patched
    # sites inside one carry no preimage; ARM9 can never be source-provided there.
    with pytest.raises(PinError, match="only sites in source-provided"):
        replace(table, containers=plain)
    with pytest.raises(PinError, match="no preimage"):
        replace(table, sites=(table.sites[0], replace(table.sites[1], expected_before=table.sites[1].after)))
    with pytest.raises(PinError, match="cannot be source-provided"):
        replace(table, containers=(replace(table.containers[0], source_provided=True), table.containers[1]))


# ---- F2: address-form sites and RAM bases ---------------------------------------------------

def test_parent_and_output_must_agree_on_ram_bases():
    parent, output, manifest, table = patched()
    bad = NdsImage.load(with_u32(parent.data, 0x28, parent.arm9_ram_base + 0x1000))
    t = replace(table, parent=ParentPin.of(bad),
                distribution=replace(table.distribution, input_sha1=digest(bad.data, "sha1")))
    with pytest.raises(PinError, match="ARM9 base"):
        verify_parent(bad, t)
    with pytest.raises(PinError, match="RAM base differs"):
        verify_sites_before(bad, t)
    # source_built with an optional parent is held to the same rule.
    image = NdsImage.load(synthetic(dsi=False))
    shifted = NdsImage.load(with_u32(image.data, 0x28, image.arm9_ram_base + 0x1000))
    table = make_table(image, image, (), source_built=True, parent=ParentPin.of(shifted),
                       source_build=build(shifted))
    with pytest.raises(PinError, match="ARM9 base"):
        verify_output(image, table, (), parent=shifted)


def test_overlay_address_sites_require_matching_overlay_bases():
    parent = NdsImage.load(synthetic())
    ov = parent.overlays["overlay9:0"]
    y9 = struct.unpack_from("<I", parent.data, 0x50)[0]
    bad = NdsImage.load(with_u32(parent.data, y9 + 4, ov.ram_base + 0x1000))
    before = parent.decoded(ov.name)[32:36].hex()
    site = SitePin("overlay", ov.name, "thumb", before, before, ov.ram_base + 36, "FILE", address=ov.ram_base + 32)
    table = make_table(parent, parent, (site,), containers=(container_pin(parent), container_pin(parent, ov.name)))
    assert verify_sites_before(parent, table) == (("overlay",), ())
    with pytest.raises(PinError, match="RAM base differs"):
        verify_sites_before(bad, table)


# ---- F3: DSi opt-out needs a reason ---------------------------------------------------------

def test_dsi_preservation_opt_out_needs_a_reason_on_a_dsi_parent():
    parent, output, manifest, table = patched()
    assert parent.unit_code & 2
    with pytest.raises(PinError, match="dsi_preserved_reason"):
        replace(table, dsi_preserved=False)
    with pytest.raises(PinError, match="nonempty"):
        replace(table, dsi_preserved=False, dsi_preserved_reason="  ")
    assert replace(table, dsi_preserved=False, dsi_preserved_reason="signature re-signed by tool X")
    plain = NdsImage.load(synthetic(dsi=False))
    assert make_table(plain, plain, (noop_site(plain),), dsi_preserved=False)  # non-DSi parent: no reason


# ---- F4: evidence references ----------------------------------------------------------------

def test_physical_evidence_requires_a_receipt_ref():
    *_, table = patched()
    site = table.sites[0]
    with pytest.raises(PinError, match="receipt_ref"):
        replace(site, evidence_class="PHYSICAL")
    assert replace(site, evidence_class="PHYSICAL", receipt_ref="receipts/e2e-2026-10-02.json")
    assert replace(site, evidence_class="FILE", evidence_ref="docs/gen4/site-notes.md")
    with pytest.raises(PinError):
        replace(site, receipt_ref="")
    with pytest.raises(PinError, match="unknown ISA"):
        replace(site, evidence_class="VIBES")


# ---- F5: zero sites -------------------------------------------------------------------------

def test_zero_sites_need_source_built_and_an_explicit_reason():
    image = NdsImage.load(synthetic(dsi=False))
    with pytest.raises(PinError, match="zero sites"):
        make_table(image, image, (), source_built=True, sites_reason=None)
    with pytest.raises(PinError, match="zero sites"):
        make_table(image, image, (), dsi_preserved=False, sites_reason="why not")  # byte_patched
    with pytest.raises(PinError, match="nonempty"):
        make_table(image, image, (), source_built=True, sites_reason=" ")
    assert make_table(image, image, (), source_built=True, sites_reason="engine appends only")
    with pytest.raises(PinError, match="only for tables without"):
        make_table(image, image, (noop_site(image),), source_built=True, sites_reason="oops")


# ---- schema negatives (F9) ------------------------------------------------------------------

def test_container_names_file_ids_and_title_configuration():
    image = NdsImage.load(synthetic(dsi=False))
    pin = container_pin(image)
    for bad in ("overlay9:01", "Overlay9:1", "overlay8:1", "ram:02150000", "overlay9", "arm7", "overlay9:-1"):
        with pytest.raises(PinError, match="container must be"):
            replace(pin, name=bad, file_id=1)
    with pytest.raises(PinError, match="file_id"):
        replace(pin, file_id=0)  # ARM9 omits it
    with pytest.raises(PinError, match="file_id"):
        replace(pin, name="itcm", file_id=0)
    with pytest.raises(PinError, match="file_id"):
        replace(pin, name="overlay9:0", file_id=None)
    *_, table = patched()
    with pytest.raises(PinError, match="ARM9 container disagrees"):
        replace(table, output_arm9_compressed=False)
    with pytest.raises(PinError, match="ARM9 container disagrees"):
        replace(table, arm9_ram_base=table.arm9_ram_base + 4)


def test_named_hash_sets_and_no_touch_spans_are_strict():
    *_, table = patched()
    for name in ("generator_hashes", "source_hashes"):
        with pytest.raises(PinError, match="nonempty unique"):
            replace(table, **{name: ()})
        dup = (NamedHash("x", "1" * 64), NamedHash("x", "2" * 64))
        with pytest.raises(PinError, match="unique"):
            replace(table, **{name: dup})
    span = NoTouchSpan(0, 8, "5" * 64)
    with pytest.raises(PinError, match="overlapping no-touch"):
        replace(table, no_touch_spans=(span, span))
    with pytest.raises(PinError, match="overlapping no-touch"):
        replace(table, no_touch_spans=(span, NoTouchSpan(4, 8, "5" * 64)))
    with pytest.raises(PinError, match="empty no-touch"):
        NoTouchSpan(0, 0, "5" * 64)


def test_overlay_geometry_and_compression_mismatches_are_detected():
    image = NdsImage.load(synthetic(dsi=False))
    ov = image.overlays["overlay9:0"]
    y9 = struct.unpack_from("<I", image.data, 0x50)[0]
    shifted = NdsImage.load(with_u32(image.data, y9 + 4, ov.ram_base + 0x1000))
    pins = (container_pin(image), container_pin(image, "overlay9:0", source_provided=True))
    table = make_table(image, shifted, (), source_built=True, containers=pins)
    with pytest.raises(PinError, match="overlay geometry"):
        verify_output(shifted, table, ())
    flipped = (pins[0], replace(pins[1], compressed=False))
    with pytest.raises(PinError, match="overlay geometry"):
        verify_output(image, make_table(image, image, (), source_built=True, containers=flipped), ())
    assert verify_output(image, make_table(image, image, (), source_built=True, containers=pins), ())


# ---- A/B/G: source build provenance ----------------------------------------------------------

def test_vanilla_reproduction_is_required_unless_waived():
    base = NdsImage.load(synthetic(dsi=False))
    sha = digest(base.data, "sha1")
    good = VanillaReproduction(sha, sha, "mwccarm 2.0/sp2p2", "6" * 64, "data/gen4/pret_build_provenance.json")
    assert build(base, vanilla_reproduction=good, vanilla_reproduction_waived=None)
    with pytest.raises(PinError, match="not byte-identical"):
        replace(good, rebuilt_sha1="9" * 40)
    with pytest.raises(PinError, match="exactly one"):
        build(base, vanilla_reproduction_waived=None)
    with pytest.raises(PinError, match="exactly one"):
        build(base, vanilla_reproduction=good)
    with pytest.raises(PinError, match="disagrees"):
        build(base, vanilla_reproduction=replace(good, base_sha1="9" * 40, rebuilt_sha1="9" * 40),
              vanilla_reproduction_waived=None)
    with pytest.raises(PinError, match="nonempty"):
        build(base, vanilla_reproduction_waived="")


def test_patch_set_identity_and_dirty_tree_inputs():
    base = NdsImage.load(synthetic(dsi=False))
    with pytest.raises(PinError, match="commit"):
        PatchSet("abc", "4" * 64)
    with pytest.raises(PinError, match="patch_set"):
        build(base, patch_set=None)
    with pytest.raises(PinError, match="tracked_inputs"):
        build(base, dirty_tree=True)
    with pytest.raises(PinError, match="unique"):
        build(base, tracked_inputs=(NamedHash("a", "1" * 64), NamedHash("a", "2" * 64)))
    assert build(base, dirty_tree=True, tracked_inputs=(NamedHash("hooks", "1" * 64),))
    assert build(base)  # clean tree needs none


# ---- E: autoload blocks ---------------------------------------------------------------------

def itcm_table(image, block):
    itcm = ContainerPin("itcm", digest(block), digest(block), None, 0x01FF8000, len(block), False, True)
    site = SitePin("mailbox-init", "itcm", "arm", None, block[:4].hex(), 0x01FF8004, "SOURCE", address=0x01FF8000)
    return make_table(image, image, (site,), source_built=True,
                      containers=(container_pin(image), itcm))


def test_autoload_blocks_need_an_image_accessor():
    image = NdsImage.load(synthetic(dsi=False))
    block = bytes([0xE5, 0x9F, 0, 0]) + bytes(60)
    table = itcm_table(image, block)
    assert CompanionPin.from_json(table.to_json()) == table

    class NoAccessor(NdsImage):
        autoload_block = None

    with pytest.raises(PinError, match="autoload blocks unsupported by this image object"):
        verify_output(NoAccessor.load(bytes(image.data)), table, ())
    with pytest.raises(PinError):  # the real accessor refuses the synthetic image (no valid autoload table)
        verify_output(image, table, ())

    def stub(returned):
        class WithAutoload(NdsImage):
            def autoload_block(self, kind, *, arm9_compressed=None):
                assert kind == "itcm" and arm9_compressed is True
                return returned
        return WithAutoload.load(bytes(image.data))

    def info(ram):
        return AutoloadInfo(ram, len(block), 0, 0)

    good = AutoloadBlock(block, info(0x01FF8000), "itcm", 0)
    assert verify_output(stub(good), table, ())
    # Plain bytes cannot bind the RAM base the container/site asserts: refused by name.
    with pytest.raises(PinError, match="must return an AutoloadBlock"):
        verify_output(stub(block), table, ())
    # An AutoloadBlock at the wrong base is refused too (container base, then the address-form site).
    wrong_base = AutoloadBlock(block, info(0x01FF8100), "itcm", 0)
    with pytest.raises(PinError, match="autoload RAM base mismatch"):
        verify_output(stub(wrong_base), table, ())
    preimage = replace(table.sites[0], expected_before=block[:4].hex(), after=block[:4].hex())
    # F3: an ITCM preimage lives in the decoded ARM9 too, so base compression must be explicit.
    with pytest.raises(PinError, match="explicit base compression"):
        replace(table, sites=(preimage,))
    explicit = replace(table, sites=(preimage,), source_build=replace(table.source_build, base_arm9_compressed=True))
    assert verify_sites_before(stub(good), explicit) == (("mailbox-init",), ())
    with pytest.raises(PinError, match="image RAM base differs"):
        verify_sites_before(stub(wrong_base), explicit)

    class Absent(NdsImage):
        def autoload_block(self, kind, *, arm9_compressed=None):
            return None

    with pytest.raises(PinError, match="no itcm autoload"):
        verify_output(Absent.load(bytes(image.data)), table, ())
    with pytest.raises(PinError, match="raw"):
        ContainerPin("dtcm", "1" * 64, "1" * 64, None, 0x027E0000, 16, True)


# ---- F: distribution ------------------------------------------------------------------------

def toy_case():
    base = NdsImage.load(synthetic(compressed=True, dsi=False))
    output = NdsImage.load(synthetic(compressed=False, dsi=False))
    artifact = zlib.compress(bytes(output.data))  # toy "format": whole-output blob
    dist = Distribution("custom", digest(artifact), digest(base.data, "sha1"), digest(output.data, "sha1"), True)
    table = make_table(base, output, source_built=True, distribution=dist, output_arm9_compressed=False,
                       containers=(container_pin(output, compressed=False),))
    return base, artifact, table


def toy_apply(base_data, artifact):
    return zlib.decompress(artifact)


def test_verify_distribution_applies_via_injected_callable():
    base, artifact, table = toy_case()
    assert verify_distribution(base, artifact, toy_apply, table)
    assert verify_distribution(bytes(base.data), artifact, toy_apply, table)
    with pytest.raises(PinError, match="artifact hash"):
        verify_distribution(base, artifact + b"x", toy_apply, table)
    with pytest.raises(PinError, match="declared input"):
        verify_distribution(b"\0" * 0x400, artifact, toy_apply, table)
    with pytest.raises(PinError, match="output hash"):
        verify_distribution(base, artifact, lambda b, a: b"wrong", table)
    with pytest.raises(PinError, match="bytes"):
        verify_distribution(base, artifact, lambda b, a: None, table)
    with pytest.raises(PinError, match="no distribution"):
        verify_distribution(base, artifact, toy_apply, replace(table, distribution=None))


def test_verify_distribution_hashes_the_output_once(monkeypatch):
    import tools.nds_pins as pins
    base, artifact, table = toy_case()
    calls = []
    real = pins.digest
    monkeypatch.setattr(pins, "digest", lambda data, *a, **kw: calls.append((len(data), a)) or real(data, *a, **kw))
    assert verify_distribution(base, artifact, toy_apply, table)
    assert len(calls) == 3  # base sha1, artifact sha256, ONE output sha1
    assert sum(1 for n, a in calls if n == len(base.data) and a == ("sha1",)) >= 1
    calls.clear()
    pins.Hashes.of(base)
    assert len(calls) == 3  # Hashes.of is three chunked passes per image (the documented cost)


def test_distribution_schema():
    base, artifact, table = toy_case()
    d = table.distribution
    with pytest.raises(PinError, match="format"):
        replace(d, format="zip")
    with pytest.raises(PinError):
        replace(d, roundtrip_verified=1)
    with pytest.raises(PinError, match="disagrees with pinned output"):
        replace(table, distribution=replace(d, output_sha1="9" * 40))
    with pytest.raises(PinError, match="source base ROM"):
        replace(table, distribution=replace(d, input_sha1="9" * 40))
    with pytest.raises(PinError, match="requires parent and distribution"):
        replace(patched()[3], distribution=None)


# ---- cross-card F4: ABI/capability vocabulary -----------------------------------------------

def test_capability_vocabulary_matches_the_abi_header():
    header = Path(__file__).resolve().parents[2] / "patch/src/nds/common/abi.h"
    if not header.exists():
        pytest.skip("NDS_ABI_HEADER_ABSENT")
    text = header.read_text()
    caps = {m.group(1): int(m.group(2)) for m in re.finditer(r"SLINK_CAP_(\w+)\s*=\s*1u\s*<<\s*(\d+)", text)}
    assert caps == CAPABILITY_BITS
    assert int(re.search(r"#define SLINK_ABI_VERSION (\d+)u", text).group(1)) == NATIVE_ABI_VERSION
    *_, table = patched()
    assert table.native_abi == NATIVE_ABI_VERSION and table.capabilities == CAPS


# ---- F12: worked pin examples (synthetic, labelled) -------------------------------------------

def hgss_example():
    """HG/SS shape: source_built, compressed ARM9 and overlays, shared load address."""
    image = NdsImage.load(synthetic(compressed=True, dsi=False))
    sha = digest(image.data, "sha1")
    repro = VanillaReproduction(sha, sha, "mwccarm 2.0/sp2p2 under wine", "6" * 64, "synthetic provenance note")
    sites = []
    for name in ("overlay9:0", "overlay9:1"):
        before = image.decoded(name)[16:20].hex()
        sites.append(SitePin(f"hook-{name}", name, "thumb", before, before,
                             image.overlays[name].ram_base + 20, "FILE", address=image.overlays[name].ram_base + 16))
    table = make_table(
        image, image, sites, source_built=True, parent=ParentPin.of(image),
        source_build=build(image, base_arm9_compressed=True, vanilla_reproduction=repro,
                           vanilla_reproduction_waived=None),
        native_arena=NativeArena("itcm", 0x01FF8620, 0x79E0),
        containers=(container_pin(image), container_pin(image, "overlay9:0"), container_pin(image, "overlay9:1")))
    return image, table


def hge_example():
    """hge shape: in-fork source_built, raw ARM9, two named parents, dirty tree."""
    base = NdsImage.load(synthetic(compressed=True, dsi=False))
    reference = NdsImage.load(synthetic(compressed=False, dsi=False, overlay_slack=64))
    output = NdsImage.load(synthetic(compressed=False, dsi=False))
    ov = output.overlays["overlay9:1"]
    site = SitePin("new-hook", "overlay9:1", "thumb", None, output.decoded("overlay9:1")[:4].hex(),
                   ov.ram_base + 4, "SOURCE", offset=0)
    artifact = b"hge artifact"
    table = make_table(
        base, output, (site,), source_built=True,
        source_build=build(base, repo="https://example.test/hge-fork", base_arm9_compressed=True, dirty_tree=True,
                           vanilla_reproduction_waived="fork commit already contains the SLink changes",
                           tracked_inputs=(NamedHash("bytereplacement", "6" * 64), NamedHash("hooks", "7" * 64))),
        output_arm9_compressed=False, distribution_base=ParentPin.of(base), reference_build=ParentPin.of(reference),
        distribution=Distribution("xdelta", digest(artifact), digest(base.data, "sha1"),
                                  digest(output.data, "sha1"), True),
        no_touch_spans=(NoTouchSpan(0, 12, digest(output.data, start=0, length=12)),),
        native_arena=NativeArena("itcm", 0x01FF8620, 0x79E0),
        containers=(container_pin(output, compressed=False), container_pin(output, "overlay9:1", source_provided=True)))
    assert ov.ram_base == table.containers[1].ram_base
    return output, reference, table


def test_worked_example_hgss():
    image, table = hgss_example()
    assert CompanionPin.from_json(table.to_json()) == table
    assert verify_output(image, table, (), parent=image)
    assert verify_sites_before(image, table)[0] == ("hook-overlay9:0", "hook-overlay9:1")


def test_worked_example_hge_two_named_parents():
    output, reference, table = hge_example()
    assert table.parent is None  # no byte-patched parent
    assert verify_output(output, table, (), reference=reference)
    with pytest.raises(PinError, match="reference_build identity"):
        verify_output(output, table, (), reference=NdsImage.load(synthetic(dsi=False)))
    with pytest.raises(PinError, match="no reference_build"):
        verify_output(output, replace(table, reference_build=None), (), reference=reference)
    with pytest.raises(PinError, match="reference_build needs actual reference"):
        verify_output(output, table, ())  # declared reference_build, no reference bytes supplied
    # No reference and no parent: a no-touch span would be self-checked against the output.
    with pytest.raises(PinError, match="comparison base"):
        verify_output(output, replace(table, reference_build=None), ())
    tampered = bytearray(reference.data)
    tampered[:12] = b"XXXXXXXXXXXX"
    with pytest.raises(PinError, match="reference_build identity"):
        verify_output(output, table, (), reference=NdsImage.load(bytes(tampered)))


def test_worked_example_b2w2_appended_overlay():
    parent, output, manifest, table = appended_overlay_case()
    assert table.source_kind == "byte_patched" and table.containers[1].source_provided
    assert verify_output(output, table, manifest, parent=parent)
