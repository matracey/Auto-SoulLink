#!/usr/bin/env python3
"""build.py — assemble the Gen 1 companion-patch spike and inject it into Red/Blue.

    python patch/gen1/tools/build.py                 # both ROMs
    python patch/gen1/tools/build.py --rom red
    python patch/gen1/tools/build.py --verify-only   # check base ROMs without building

Mirrors patch/tools/build.py (the Radical Red pipeline) with three differences that fall
out of the platform:

  * RGBDS, not arm-none-eabi-gcc. The Game Boy has no practical C toolchain and pret is
    pure assembly, so the module is hand-written SM83 and assembled with the rgbasm the
    repo already auto-downloads for the symbol pipeline. Nothing new to install.
  * Padding is 0x00, not 0xFF — pokered links with `-p 0x00`, so an unused bank is a run
    of zero bytes and that is what we assert before overwriting it.
  * RED AND BLUE ONLY. Yellow has no free WRAM at all (pret's map: `WRAM0: TOTAL EMPTY:
    $0000`) so there is nowhere to put a mailbox.

Every write is verify-then-write: the hook bytes are checked against their expected current
values before being changed, so a ROM that is not the exact expected dump fails loudly
instead of being silently corrupted.
"""
import argparse
import hashlib
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(REPO, "tools"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _build_tools_bootstrap import ensure_rgbds  # noqa: E402

SRC = os.path.join(REPO, "patch", "gen1", "src", "slink.asm")
TRADE_SRC = os.path.join(REPO, "patch", "gen1", "src", "trade.asm")
BUILD = os.path.join(REPO, "patch", "gen1", "build")
DIST = os.path.join(REPO, "patch", "gen1", "dist")
PAYLOAD_FILE = os.path.join(DIST, "slink_bank3f.bin")

from manifest import (  # noqa: E402
    BANK_SIZE,
    HOOK_BANK,
    HOOK_ORIGINAL,
    HOOK_SITE,
    HOOK_TARGET,
    INJECT_OFFSET,
    JOYPAD_STUB_ADDR,
    JOYPAD_STUB_AFTER,
    MENU_PATCHES,
    PROTECTED_RANGE,
    ROMS,
    TRADE_BRIDGE_AFTER,
)
from title_screen import DEFAULT_VERSION, FREE_FROM, title_spans  # noqa: E402


def assemble() -> bytes:
    """Link the panel and fixed-section trade objects into one bank $3F image."""
    rgbds = ensure_rgbds()
    os.makedirs(BUILD, exist_ok=True)
    exe = ".exe" if os.name == "nt" else ""
    obj = os.path.join(BUILD, "slink.o")
    trade_obj = os.path.join(BUILD, "trade.o")
    out = os.path.join(BUILD, "slink_stub.gb")

    # trade.o is always linked below, so slink.asm advertises SLINK_CAP_TRADE.
    subprocess.run([os.path.join(rgbds, "rgbasm" + exe), "-D", "SLINK_TRADE_ENABLED",
                    "-o", obj, SRC], check=True)
    subprocess.run([os.path.join(rgbds, "rgbasm" + exe),
                    "-I", os.path.join(REPO, "patch", "gen1", "src") + os.sep,
                    "-o", trade_obj, TRADE_SRC], check=True)
    # -p 0x00 matches pokered's own RGBLINKFLAGS, so the padding we emit is the padding the
    # target bank already contains.
    subprocess.run([os.path.join(rgbds, "rgblink" + exe), "-p", "0x00",
                    "-o", out, "-n", os.path.join(BUILD, "slink.sym"),
                    obj, trade_obj], check=True)
    with open(out, "rb") as f:
        image = f.read()
    # trade_service.asm pins the 32-byte bridge in ROM0's reserved RST padding.
    # inject.py has no assembler, so its manifest embeds the bytes; the build checks
    # that this linked image still produces exactly those bytes.
    bridge = image[0x0001:0x0001 + len(TRADE_BRIDGE_AFTER)]
    if bridge != TRADE_BRIDGE_AFTER:
        raise SystemExit("linked trade bridge drifted from manifest: " + bridge.hex())
    stub = image[JOYPAD_STUB_ADDR:JOYPAD_STUB_ADDR + len(JOYPAD_STUB_AFTER)]
    if stub != JOYPAD_STUB_AFTER:
        raise SystemExit("linked Joypad stub drifted from manifest: " + stub.hex())
    start = INJECT_OFFSET
    if len(image) < start + BANK_SIZE:
        # rgblink emits only as many banks as it needs; the section is pinned to $3F, so a
        # short image means the pin did not take.
        raise SystemExit(f"linked image is {len(image)} bytes — bank {HOOK_BANK:#x} missing")
    return image[start:start + BANK_SIZE]


def code_length(bank: bytes) -> int:
    """Bytes of actual code, i.e. up to the trailing 0x00 padding."""
    end = len(bank)
    while end > 0 and bank[end - 1] == 0:
        end -= 1
    return end


def patch_rom(rom_key: str, bank: bytes, verify_only: bool = False, version: str = DEFAULT_VERSION) -> str:
    name, sha1 = ROMS[rom_key]
    src = os.path.join(REPO, name)
    if not os.path.exists(src):
        raise SystemExit(f"ROM not found: {name}")
    with open(src, "rb") as f:
        data = bytearray(f.read())

    actual = hashlib.sha1(bytes(data)).hexdigest()
    if actual != sha1:
        raise SystemExit(f"{rom_key}: expected sha1 {sha1}, got {actual} — wrong dump")

    # 1. The target bank must be untouched padding. If it is not, this is not the ROM the
    #    offsets were derived from and injecting would destroy real code.
    region = data[INJECT_OFFSET:INJECT_OFFSET + BANK_SIZE]
    if any(b != 0 for b in region):
        raise SystemExit(f"{rom_key}: bank {HOOK_BANK:#x} is not empty — refusing to inject")

    # 2. The hook site must still hold the exact instruction we expect to displace.
    site = bytes(data[HOOK_SITE:HOOK_SITE + len(HOOK_ORIGINAL)])
    if site != HOOK_ORIGINAL:
        raise SystemExit(f"{rom_key}: hook site {HOOK_SITE:#x} holds {site.hex()}, "
                         f"expected {HOOK_ORIGINAL.hex()}")
    # 3. Every menu span must hold exactly what the manifest expects, and none may touch
    #    the protected header. Checked for ALL spans before ANY is written, so a manifest
    #    that is half-applicable leaves the ROM untouched rather than half-patched.
    lo, hi = PROTECTED_RANGE
    spans = MENU_PATCHES + title_spans(bytes(data), version)
    for off, original, new, why in spans:
        if not (off + len(new) <= lo or off > hi):
            raise SystemExit(
                f"{rom_key}: patch at {off:#06x} ({why}) overlaps the protected cartridge "
                f"header {lo:#06x}-{hi:#06x}")
        found = bytes(data[off:off + len(original)])
        if found != original:
            raise SystemExit(
                f"{rom_key}: {off:#06x} holds {found.hex()}, expected {original.hex()} "
                f"({why}) — this is not the dump these offsets were derived from")

    if verify_only:
        return (f"{rom_key}: clean ROM, hook site, target bank and "
                f"{len(spans)} menu and title spans all as expected")

    data[INJECT_OFFSET:INJECT_OFFSET + BANK_SIZE] = bank
    for off, _original, new, _why in spans:
        data[off:off + len(new)] = new
    # Rewrite only the two immediates: `ld b, $3F` and `ld hl, $4000`. The
    # `call Bankswitch` after them is untouched, so control still flows the same way.
    data[HOOK_SITE + 1] = HOOK_BANK
    data[HOOK_SITE + 3] = HOOK_TARGET & 0xFF
    data[HOOK_SITE + 4] = HOOK_TARGET >> 8

    os.makedirs(BUILD, exist_ok=True)
    dst = os.path.join(BUILD, f"slink_{rom_key}.gb")
    with open(dst, "wb") as f:
        f.write(data)

    # 3. Read the result back and confirm the hook really points at our code.
    with open(dst, "rb") as f:
        check = f.read()
    assert check[HOOK_SITE + 1] == HOOK_BANK
    assert check[HOOK_SITE + 3] | (check[HOOK_SITE + 4] << 8) == HOOK_TARGET
    assert check[INJECT_OFFSET:INJECT_OFFSET + 8] == bank[:8]
    for off, _original, new, why in spans:
        assert bytes(check[off:off + len(new)]) == new, f"{why} did not land at {off:#06x}"
    with open(src, "rb") as f:
        pristine = f.read()[lo:hi + 1]
    assert check[lo:hi + 1] == pristine, "the cartridge header changed"
    return (f"{rom_key}: {os.path.relpath(dst, REPO)}  "
            f"md5={hashlib.md5(check).hexdigest()}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rom", choices=sorted(ROMS), help="only this ROM (default: both)")
    ap.add_argument("--verify-only", action="store_true",
                    help="check the base ROMs and hook site, build nothing")
    ap.add_argument("--version", default=DEFAULT_VERSION,
                    help="shown on the main menu: dev (default) or vX.Y.Z[-dev]")
    args = ap.parse_args()

    rom_keys = [args.rom] if args.rom else sorted(ROMS)
    if args.verify_only:
        for rom_key in rom_keys:
            print("[gen1-patch] " + patch_rom(rom_key, b"", True, args.version), file=sys.stderr)
        return 0

    bank = assemble()
    n = code_length(bank)
    if n > FREE_FROM - INJECT_OFFSET:
        raise SystemExit(f"payload is {n} bytes; it would run into the title band at {FREE_FROM:#x}")
    print(f"[gen1-patch] assembled {n} bytes of code into bank {HOOK_BANK:#x}", file=sys.stderr)

    # Publish the payload so the structural injector can run WITHOUT a toolchain.
    # It is our own assembled code, not ROM data, so committing it is fine -- and it has
    # to be committed, because the injector's whole job is to patch a cartridge whose
    # hash nobody can know in advance, on a machine that may have no rgbasm.
    os.makedirs(DIST, exist_ok=True)
    with open(PAYLOAD_FILE, "wb") as f:
        f.write(bank[:n])
    print(f"[gen1-patch] payload -> {os.path.relpath(PAYLOAD_FILE, REPO)} ({n} bytes)",
          file=sys.stderr)

    for rom_key in rom_keys:
        print("[gen1-patch] " + patch_rom(rom_key, bank, version=args.version), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
