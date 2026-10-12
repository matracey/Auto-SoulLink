"""Qualify the pinned Polished title on an actual fresh CGB boot, without SaveRAM.

Run: python tools/polished_live/title_check.py --lane F:/slink-work/lanes/pol-titlelive
The overlay must PASS; the clean-ROM control must FAIL the identical band check.
Private Lua reads VRAM/WRAM and presses Start; it never writes emulator memory.
Plain client.screenshot captures the entrance, settled title and fresh main menu.
Pixel checks compare opaque shipped 2bpp geometry with the actual screenshot.
Colour zero permits the native crystal sprites behind the BG; nonzero colours
match bijectively, without assuming a particular emulator RGB correction.
Raw palette 6, attributes, tile IDs and tile bytes are checked independently.
This is a CGB title qualification, not DMG or every main-menu/save-state variant.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path

from PIL import Image

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from tools.polished_live.overlay_pin import overlay_sha1  # noqa: E402

OVERLAY_SHA1 = overlay_sha1()
CLEAN_SHA1 = "6930b48af5844d373e3c9130f26d6dd1084cf4ed"
BAND_X, BAND_Y, WIDTH = 6, 10, 9
FIRST = 0x60
PALETTE = bytes.fromhex("0000ff7fba026278")
SYM = REPO / "data/polished/polished_slink.sym"


def sym_offset(name: str, sym: Path = SYM) -> int:
    """Flat ROM offset of an overlay label from the committed .sym (a rebuilt overlay moves labels)."""
    for line in sym.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1] == name:
            bank, addr = (int(v, 16) for v in parts[0].split(":"))
            return addr if bank == 0 else bank * 0x4000 + addr - 0x4000
    raise ValueError(f"{name} not in {sym}")


def tile_pixels(tile: bytes) -> list[list[int]]:
    if len(tile) != 16:
        raise ValueError("2bpp tile must contain 16 bytes")
    return [[((tile[y * 2] >> (7 - x)) & 1) | (((tile[y * 2 + 1] >> (7 - x)) & 1) << 1)
             for x in range(8)] for y in range(8)]


def check_band(vram: bytes, art: bytes, rows: list[bytes]) -> list[str]:
    if len(vram) != 0x4000 or len(art) != 18 * 16 or [len(r) for r in rows] != [9, 9]:
        return ["invalid VRAM/art/row dimensions"]
    errors = []
    for y, row in enumerate(rows, BAND_Y):
        pos = 0x1800 + y * 32 + BAND_X
        if vram[pos:pos + WIDTH] != row:
            errors.append(f"band row {y}: tile IDs differ")
        if vram[pos + 0x2000:pos + 0x2000 + WIDTH] != bytes([6] * WIDTH):
            errors.append(f"band row {y}: attributes differ from palette 6/bank 0/unflipped")
    if vram[0x1000 + FIRST * 16:0x1000 + FIRST * 16 + len(art)] != art:
        errors.append("VRAM tile data differs from shipped Crystal art")
    return errors


def check_pixels(image: Image.Image, art: bytes, rows: list[bytes], shifts: bytes,
                 scy: int = 8) -> list[str]:
    """Match visible opaque BG pixels; colour zero permits behind-BG sprites."""
    if image.size != (160, 144) or len(shifts) != 144 or scy != 8:
        return ["invalid screenshot/scanline geometry"]
    if len(art) != 18 * 16 or [len(r) for r in rows] != [9, 9]:
        return ["invalid art/row dimensions"]
    image = image.convert("RGB")
    colours: dict[int, tuple[int, int, int]] = {}
    mismatches = visible = 0
    for ry, row in enumerate(rows):
        for rx, tile_id in enumerate(row):
            if not FIRST <= tile_id < FIRST + 18:
                return ["tile ID outside shipped art"]
            tile = tile_pixels(art[(tile_id - FIRST) * 16:(tile_id - FIRST + 1) * 16])
            for ty, pixels in enumerate(tile):
                y = (BAND_Y + ry) * 8 + ty - scy
                for tx, index in enumerate(pixels):
                    x = ((BAND_X + rx) * 8 + tx - shifts[y]) & 255
                    if x >= 160 or index == 0:
                        continue
                    visible += 1
                    colour = image.getpixel((x, y))
                    colours.setdefault(index, colour)
                    mismatches += colour != colours[index]
    errors = []
    if visible < 300:
        errors.append(f"wordmark insufficiently visible: {visible} pixels")
    if len(colours) < 3 or len(set(colours.values())) != len(colours):
        errors.append("wordmark colour indices are not distinct")
    if mismatches:
        errors.append(f"wordmark screenshot shape differs at {mismatches}/{visible} pixels")
    return errors


def check_version(vram: bytes, text: bytes) -> list[str]:
    if len(vram) != 0x4000 or not 1 <= len(text) <= 19:
        return ["invalid menu snapshot/version field"]
    pos = 0x1800 + 10 * 32 + 1  # patch/polished/src/version.asm:38
    return [] if vram[pos:pos + len(text)] == text else ["main-menu version slice differs from ROM field"]


def expand_version(text: bytes, ngrams: dict[int, bytes]) -> bytes:
    """PlaceString expands ROM ngrams before writing literal font tile IDs."""
    glyphs = bytearray()
    for char in text:
        part = bytes([char]) if char >= 0x5F else ngrams.get(char)
        if not part or any(c < 0x5F for c in part):
            raise ValueError("unsupported version text command or ngram")
        glyphs.extend(part)
    if not 1 <= len(glyphs) <= 19:
        raise ValueError("expanded version exceeds the menu slice")
    return bytes(glyphs)


LUA = r'''
local root, run = os.getenv("SLINK_ROOT"), os.getenv("TITLE_RUN")
local J = dofile(root .. "/lua/json_codec.lua")
local function hex(addr, count, domain)
    local out = {}
    for i = 0, count - 1 do out[#out + 1] = string.format("%02x", memory.read_u8(addr + i, domain)) end
    return table.concat(out)
end
local function bus(addr) return memory.read_u8(addr, "System Bus") end
local hits = {entrance=0, menu=0}
event.on_bus_exec(function() if bus(0xff87) == 1 then hits.entrance = hits.entrance + 1 end end,
                  0x6698, "title_entrance", "System Bus")
event.on_bus_exec(function() if bus(0xff87) == 0x12 then hits.menu = hits.menu + 1 end end,
                  0x43cd, "title_menu", "System Bus")
local function capture(name)
    local snap = {name=name, frame=emu.framecount(), rom_sha1=gameinfo.getromhash(),
                  system=emu.getsystemid(),
                  domains={VRAM=memory.getmemorydomainsize("VRAM"),WRAM=memory.getmemorydomainsize("WRAM")},
                  scx=bus(0xffb9), scy=bus(0xffba), phase=memory.read_u8(0xe47,"WRAM"),
                  vram=hex(0,0x4000,"VRAM"), overrides=hex(0x5e00,144,"WRAM"),
                  palette6=hex(0x5db0,8,"WRAM"), entrance_hits=hits.entrance, menu_hits=hits.menu,
                  screenshot=run .. "/" .. name .. ".png"}
    client.screenshot(snap.screenshot)
    local encoded, why = J.encode(snap)
    assert(encoded, why)
    local f = assert(io.open(run .. "/" .. name .. ".json", "w"))
    f:write(encoded) f:close()
end
client.speedmode(400)
local mid, settled, settle_frame, menu_start = false, false, nil, nil
for n = 1, 6000 do
    joypad.set(menu_start and n % 16 < 2 and {Start=true} or {})
    emu.frameadvance()
    local sx, phase = bus(0xffb9), memory.read_u8(0xe47,"WRAM")
    if not mid and hits.entrance > 0 and phase == 0 and sx >= 32 and sx <= 48 then
        capture("entrance") mid = true
    end
    if mid and not settled and phase == 2 and sx == 0 then
        settle_frame = settle_frame or n
        if n - settle_frame >= 60 then capture("settled") settled = true menu_start = n end
    end
    if settled and hits.menu > 0 and n - menu_start >= 120 then
        capture("menu")
        local f=assert(io.open(run .. "/done.txt","w")) f:write("native boot complete") f:close()
        client.exit() return
    end
end
local f=assert(io.open(run .. "/error.txt","w"))
f:write("native boot bound exceeded: " .. J.encode(hits)) f:close()
client.exit()
'''


def run_boot(kind: str, rom: Path, lane: Path) -> dict:
    from dataclasses import replace

    from tools.gen1_playthrough import write_run_config
    from tools.polished_live.runtime import LaunchConfig, kill_own, start_owned

    run = lane / kind
    run.mkdir(exist_ok=False)
    config = replace(LaunchConfig.from_env(), rom=rom, sram=run / "sram", symbols=())
    def title_config(base, target, **kwargs):
        write_run_config(base, target, **kwargs)
        settings = json.loads(Path(target).read_text())
        settings["ScreenshotCaptureOsd"] = False
        Path(target).write_text(json.dumps(settings, indent=2))
    script = run / "boot.lua"
    script.write_text(LUA, encoding="utf-8")
    proc = start_owned(config, script, run, {"TITLE_RUN": run.as_posix()}, write_config=title_config)
    print(f"[title] {kind} owned EmuHawk PID {proc.pid}", flush=True)
    try:
        deadline = time.monotonic() + 180
        while proc.poll() is None and time.monotonic() < deadline and not (run / "done.txt").exists():
            time.sleep(0.25)
        if (run / "done.txt").exists():
            with suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=10)
    finally:
        kill_own(proc, "title " + kind)
    print(f"[title] {kind} owned PID {proc.pid} ended rc={proc.returncode}", flush=True)
    if not (run / "done.txt").exists():
        raise RuntimeError(f"{kind} native boot incomplete; inspect {run}")
    return {"pid": proc.pid, "exit_code": proc.returncode,
            "snapshots": {name: json.loads((run / f"{name}.json").read_text())
                          for name in ("entrance", "settled", "menu")}}


def judge_boot(boot: dict, art: bytes, rows: list[bytes], version: bytes) -> list[str]:
    errors = []
    for name in ("entrance", "settled"):
        snap = boot["snapshots"][name]
        errors += [f"{name}: {e}" for e in check_band(bytes.fromhex(snap["vram"]), art, rows)]
        if bytes.fromhex(snap["palette6"]) != PALETTE:
            errors.append(f"{name}: palette 6 differs from logo palette")
        shifts = bytes.fromhex(snap["overrides"])
        if name == "entrance":
            if not 0 < snap["scx"] < 112 or shifts[72:88] != bytes(
                    snap["scx"] if y % 2 == 0 else (-snap["scx"] & 255) for y in range(72, 88)):
                errors.append("entrance: both wordmark rows did not join the interlaced logo shear")
            # LCDGeneric applies the HBlank override to the following scanline.
            shifts = bytes([0]) + shifts[:-1]
        else:
            if snap["scx"] != 0 or any(shifts[:88]):
                errors.append("settled: logo shear remains active")
            shifts = bytes(144)
        with Image.open(snap["screenshot"]) as image:
            errors += [f"{name}: {e}" for e in check_pixels(image, art, rows, shifts, snap["scy"])]
    errors += check_version(bytes.fromhex(boot["snapshots"]["menu"]["vram"]), version)
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lane", type=Path, required=True)
    parser.add_argument("--base", type=Path, default=Path(os.environ.get("SLINK_WORK_ROOT", "F:/slink-work")) /
                        "cache/polished/release/polishedcrystal-3.2.3.gbc")
    args = parser.parse_args()
    sys.path.insert(0, str(REPO))
    from tools.polished_live.runtime import pinned_rom_bytes
    base = pinned_rom_bytes(args.base, CLEAN_SHA1)
    overlay = pinned_rom_bytes(args.base, OVERLAY_SHA1, patch=REPO / "patch/dist/SLink-Polished.ups",
                               base_sha1=CLEAN_SHA1)
    art = (REPO / "patch/gen2/src/title_logo_crystal.2bpp").read_bytes()
    assert overlay[0x1F984A:0x1F996A] == art, "overlay art differs from shipped Crystal art"
    rows = [bytes(int(v, 16) for v in re.findall(r"\$([0-9A-F]{2})", line))
            for line in (REPO / "patch/gen2/src/title_rows_crystal.inc").read_text().splitlines()
            if line.strip().startswith("db ")]
    start, end = sym_offset("SlinkVersionText.text"), sym_offset("SlinkVersionText.end")
    field = overlay[start:end - 1]  # excludes Polished's $53 terminator
    if overlay[end - 1] != 0x53:
        raise ValueError("version field does not end in the $53 terminator")
    ngrams = {}
    for char in set(field):
        if 0x0A <= char < 0x52:
            entry = 0x3BCE + char - 0x0A  # ROM0 NgramStrings; home/text.asm:197-217
            start = entry + overlay[entry]
            ngrams[char] = overlay[start:overlay.index(0x53, start, start + 20)]
    version = expand_version(field, ngrams)
    args.lane.mkdir(parents=True, exist_ok=True)
    evidence = {"overlay_sha1": OVERLAY_SHA1, "clean_sha1": CLEAN_SHA1,
                "art_sha256": hashlib.sha256(art).hexdigest(), "version_hex": version.hex(),
                "version_field_hex": field.hex(), "runs": {}}
    for kind, data in (("overlay", overlay), ("clean", base)):
        rom = args.lane / f"{kind}.gbc"
        rom.write_bytes(data)
        boot = run_boot(kind, rom, args.lane)
        errors = judge_boot(boot, art, rows, version)
        evidence["runs"][kind] = dict(boot, reasons=errors, result="FAIL" if errors else "PASS")
        print(f"RESULT: {'FAIL' if errors else 'PASS'} polished-title-{kind}", flush=True)
        for error in errors:
            print(f"  {error}", flush=True)
    (args.lane / "evidence.json").write_text(json.dumps(evidence, indent=2))
    return 0 if not evidence["runs"]["overlay"]["reasons"] and any(
        "tile IDs differ" in r for r in evidence["runs"]["clean"]["reasons"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
