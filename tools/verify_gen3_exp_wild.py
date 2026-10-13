#!/usr/bin/env python3
"""Read compiled expansion wild headers, descriptors and slots back from pinned ROM."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from server.adapters.gen3_expansion import ROM_TYPE, Gen3ExpansionAdapter  # noqa: E402
from tools import gen_gen3_exp_wild as source_wild  # noqa: E402
from tools import gen_gen3_profile as profile  # noqa: E402
from tools.gen_gen3_exp_trainers import enum_values  # noqa: E402
from tools.gen_gen3_trainers import map_keys, read  # noqa: E402

ROM_BASE = 0x08000000
ROM_LIMIT = 0x0A000000
OUT = ROOT / "data/games/gen3_exp/28877d73/expansion_encounters_rom_receipt.json"
SOURCE_PACK = ROOT / "data/games/gen3_exp/28877d73/expansion_encounters.json"
AREA_MAP = ROOT / "data/games/gen3_exp/28877d73/area_map.json"
METHODS = {"land_mons": "Grass", "water_mons": "Surfing",
           "rock_smash_mons": "Rock Smash", "fishing_mons": "Fishing"}


def digest(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def pack_sha256(path: Path) -> str:
    """The pack's hash with LF line endings, so a CRLF working copy pins the same value."""
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def readback(rom: bytes, context: dict, src: Path) -> dict:
    """Validate geometry and every active source declaration against compiled ROM bytes.

    Called by build() only after expansion_inputs verified ROM identity. Exposed separately so
    malformed-pointer tests exercise structural refusal rather than merely SHA-1 refusal.
    """
    data = json.loads(read(src / source_wild.WILD_JSON))
    group = source_wild.map_group(data)
    active = source_wild.emerald_compiled_labels(src, data, group)
    entries = [e for e in group["encounters"] if e["base_label"] in active]
    if len(entries) != len({e["base_label"] for e in entries}):
        raise ValueError("duplicate compiled base label would change header order")
    fields = [f["type"] for f in group["fields"]]
    if fields != list(METHODS):
        raise ValueError(f"unreviewed WildEncounterTypes field order: {fields}")
    source_wild.check_rates(source_wild.habitat_plan(group["fields"]))
    species = enum_values(read(src / "include/constants/species.h"), "SPECIES_")
    keys = map_keys(src)
    positions = {k: tuple(int(x) for x in v.split(":")) for k, v in keys.items()}
    header = profile.expansion_symbol(context, "gWildMonHeaders")
    # include/wild_encounter.h: u8,u8,2 pad, WildEncounterTypes[4]; each type has five ptrs.
    # include/constants/rtc.h: TIME_MORNING/DAY/EVENING/NIGHT, COUNT=4.
    stride = 4 + 4 * 5 * 4  # this pinned source's four TimeOfDay values and five info pointers
    if header["size"] != (len(entries) + 1) * stride:
        raise ValueError("gWildMonHeaders size differs from source/count/struct geometry")

    def at(addr: int, size: int) -> int:
        if addr < ROM_BASE or addr + size > min(ROM_BASE + len(rom), ROM_LIMIT):
            raise ValueError(f"wild pointer outside ROM: {addr:#x}+{size}")
        return addr - ROM_BASE

    def u32(addr: int) -> int:
        off = at(addr, 4)
        return int.from_bytes(rom[off:off + 4], "little")

    def symbol(name: str, size: int) -> int:
        row = profile.expansion_symbol(context, name)
        if row["size"] != size:
            raise ValueError(f"wild symbol {name} size {row['size']} != {size}")
        at(row["address"], size)
        return row["address"]

    decoded = []
    counts = Counter()
    hidden_pointers = bytearray()
    for i, entry in enumerate(entries):
        base = header["address"] + i * stride
        raw = rom[at(base, stride):at(base, stride) + stride]
        expected_pos = positions.get(entry["map"])
        if expected_pos is None or tuple(raw[:2]) != expected_pos or raw[2:4] != b"\0\0":
            raise ValueError(f"wild header {i} map/padding differs: {entry['base_label']}")
        # The source JSON has no hidden_mons field, so the pinned header generator emits four
        # named infos and C zero-initializes WildEncounterTypes.hiddenMonsInfo. The fifth
        # pointer is at +20; checking only raw[24:] would silently accept a hidden table.
        hidden = u32(base + 20)
        hidden_pointers.extend(raw[20:24])
        if hidden:
            raise ValueError(f"wild header {i} unexpected hidden fifth pointer: {hidden:#x}")
        if any(raw[24:]):
            raise ValueError(f"wild header {i} inactive time pointers are nonzero")
        block = {}
        for j, field in enumerate(fields):
            ptr = u32(base + 4 + j * 4)
            table = entry.get(field)
            if table is None:
                if ptr:
                    raise ValueError(f"wild header {i} unexpected {field} pointer")
                continue
            label = entry["base_label"] + "_" + field.title().replace("_", "")
            info_address = symbol(label + "Info", 8)
            if ptr != info_address:
                raise ValueError(f"wild descriptor pointer {entry['base_label']}.{field}: {ptr:#x}")
            info = rom[at(ptr, 8):at(ptr, 8) + 8]
            if info[0] != table["encounter_rate"] or info[1:4] != b"\0\0\0":
                raise ValueError(f"wild descriptor rate/padding differs: {label}")
            mons = table["mons"]
            mon_address = symbol(label, 4 * len(mons))
            if u32(ptr + 4) != mon_address:
                raise ValueError(f"wild mon-array pointer differs: {label}")
            slots = []
            for k, mon in enumerate(mons):
                raw_mon = rom[at(mon_address + k * 4, 4):at(mon_address + k * 4, 4) + 4]
                actual = (raw_mon[0], raw_mon[1], int.from_bytes(raw_mon[2:4], "little"))
                expected = (mon.get("min_level", 2), mon.get("max_level", 100), species[mon["species"]])
                if actual != expected:
                    raise ValueError(f"wild slot differs: {label}[{k}] {actual} != {expected}")
                slots.append(actual)
            block[field] = {"rate": info[0], "slots": slots}
            counts[field] += 1
        decoded.append({"label": entry["base_label"], "map": list(expected_pos), "habitats": block})

    term_addr = header["address"] + len(entries) * stride
    terminator = rom[at(term_addr, stride):at(term_addr, stride) + stride]
    if terminator != b"\xff\xff" + bytes(stride - 2):
        raise ValueError("wild header terminator differs")
    return {"headers": decoded, "habitat_counts": dict(sorted(counts.items())),
            "terminator_count": 1, "stride": stride,
            "hidden_pointer_count": sum(hidden_pointers[i:i + 4] != b"\0\0\0\0"
                                        for i in range(0, len(hidden_pointers), 4)),
            "hidden_pointer_sha256": hashlib.sha256(hidden_pointers).hexdigest()}


def project_set_zero(headers: list[dict]) -> dict:
    """First header per map, then first method per area, preserving slot order/rates."""
    area_map = json.loads(read(AREA_MAP))
    name = Gen3ExpansionAdapter(rom_type=ROM_TYPE).species_name
    first_map = {}
    for row in headers:
        first_map.setdefault(tuple(row["map"]), row)
    result = {}
    plan = source_wild.habitat_plan(source_wild.map_group(json.loads(read(
        ROOT / ".cache/expansion-src" / source_wild.WILD_JSON)))["fields"])
    rates = {habitat: methods for habitat, methods in plan}
    for pos, row in sorted(first_map.items()):
        area = area_map.get(f"{pos[0]}:{pos[1]}")
        if not area:
            continue
        block = result.setdefault(area, {})
        for habitat, payload in row["habitats"].items():
            slot_offset = 0
            for method, weights in rates[habitat]:
                if method in block:
                    slot_offset += len(weights)
                    continue
                agg = {}
                for rate, (minimum, maximum, sid) in zip(weights,
                        payload["slots"][slot_offset:slot_offset + len(weights)], strict=True):
                    value = agg.setdefault(sid, {"species_id": sid, "name": name(sid),
                                                 "rate": 0, "min_level": minimum,
                                                 "max_level": maximum})
                    value["rate"] += rate
                    value["min_level"] = min(value["min_level"], minimum)
                    value["max_level"] = max(value["max_level"], maximum)
                block[method] = sorted(agg.values(), key=lambda e: (-e["rate"], e["species_id"]))
                slot_offset += len(weights)
    return {k: v for k, v in sorted(result.items()) if v}


def build() -> dict:
    src = ROOT / ".cache/expansion-src"
    if source_wild.git_head(src) != source_wild.pinned_commit() or source_wild.tracked_changes(src):
        raise ValueError("expansion source pin/cleanliness mismatch")
    context = profile.expansion_inputs(artifacts=ROOT / ".cache/expansion-output/reference")
    decoded = readback(context["rom"], context, src)
    projection = project_set_zero(decoded["headers"])
    candidate = json.loads(read(SOURCE_PACK))
    if projection != candidate["encounters"]:
        raise ValueError("compiled ROM set-0 area projection differs from source candidate")
    headers = decoded["headers"]
    cave = [r for r in headers if r["label"].startswith("gAlteringCave")]
    return {"schema": "gen3-expansion-wild-rom-readback-v1", "build": "28877d73",
            "evidence": "COMPILED_ROM_SOURCE_READBACK", "live_verified": False,
            "rom_sha1": context["source"]["rom_sha1"],
            "symbols_sha256": context["source"]["symbols_sha256"],
            "source_commit": context["source"]["source_commit"],
            "header_count": len(headers), "terminator_count": decoded["terminator_count"],
            "header_stride": decoded["stride"], "habitat_counts": decoded["habitat_counts"],
            "hidden_pointer_count": decoded["hidden_pointer_count"],
            "hidden_pointer_sha256": decoded["hidden_pointer_sha256"],
            "altering_cave_header_count": len(cave),
            "source_projection_set": 0, "source_projection_match": True,
            "header_order_sha256": digest([[r["label"], r["map"]] for r in headers]),
            "decoded_slots_sha256": digest(headers),
            "area_projection_sha256": digest(projection),
            "source_pack_sha256": pack_sha256(SOURCE_PACK),
            "limits": ["Static compiled ROM/source readback, not live encounter behavior",
                       "Area projection selects first header per map; later Altering Cave sets remain unprojected",
                       "Runtime overrides, scripts, randomized ROMs and time-dependent builds remain outside scope"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    text = json.dumps(build(), indent=2, sort_keys=True) + "\n"
    if args.check:
        if not OUT.is_file() or read(OUT) != text:
            raise SystemExit(f"stale or absent: {OUT}")
        print(f"current: {OUT}")
    else:
        OUT.write_text(text, encoding="utf-8", newline="\n")
        print(f"wrote: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
