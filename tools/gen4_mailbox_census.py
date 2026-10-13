#!/usr/bin/env python3
"""Gen 4 companion mailbox SOURCE/ARTIFACT census (card C1, offline half).

Proves, per artifact (heartgold, soulsilver, heartgold_hge), that a candidate mailbox span in the
ARM9 ITCM arena tail (SDK_SECTION_ARENA_ITCM_START 0x01FF8620 .. 0x02000000) is unclaimed by the
game, from the pinned pret build and the ROM's own tables (cf. tools/gen2_mailbox_census.py W1-W5):

    W1  the span is empty: no nm/xMAP symbol or section covers it, it does not overlap the .itcm
        autoload, and no ARM9 overlay load range reaches the ITCM page. HG/SS: the pinned pret ELF
        nm/xMAP AND the ROM autoload table; hge (raw ARM9, no map): the ROM autoload table.
    W2  no literal, symbol or arena allocation in the pinned pret src/asm/lib (and the hge fork)
        resolves into the span; every OS_ARENA_ITCM / HW_ITCM* / SDK_*ITCM* use is listed and must be
        a known non-allocating role; every ROM word that points into the span is UNPROVEN, never PASS.
    W3  the soft-reset path (OS_ResetSystem -> OSi_DoBoot -> crt0 _start) clears no byte of the span.

A row it cannot prove is UNPROVEN, never PASS. FAIL is a positive conflict. The census cannot see
computed-pointer writes (a NULL+offset store aliases ITCM at 0x00000000, a stray pointer); those stay the
live canary watch's job (lua/tests/probe_gen4_mailbox.lua). Exit 0 all PASS, 1 any FAIL, 2 UNPROVEN.

    python tools/gen4_mailbox_census.py [--span LO HI] [--out census.json]
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import struct
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from tools import gen4_pins  # noqa: E402

PASS, UNPROVEN, FAIL = "PASS", "UNPROVEN", "FAIL"
ARENA = (0x01FF8620, 0x02000000)  # SDK_SECTION_ARENA_ITCM_START .. HW_ITCM_ARENA_HI_DEFAULT
ITCM_PAGE = (0x01FF8000, 0x02000000)  # HW_ITCM .. HW_ITCM_END (lib/include/nitro/hw/ARM9/mmap.h:10-12)
# 1 KiB, 0x400 above the arena start and 0x400 below HW_ITCM_END, which OSi_ExceptionHandler uses as a
# crash-path stack (os_exception.c:45). End exclusive.
DEFAULT_SPAN = (0x01FFEC00, 0x01FFFC00)  # 4 KiB = the shared NDS ABI arena (abi.h SLINK_ARENA_SIZE 0x1000)
PRET_PIN = "ad7a3afa0cfc144fe6837c410cb95b2727217f54"
HGE_PIN = "fc517576498305ecb5f5e1de44681c6e3822361b"
PRET = Path(os.environ.get("SLINK_GEN4_PRET", "E:/Howard/hgss_archipelago-master/.tooling/pokeheartgold"))
HGE = Path(os.environ.get("SLINK_GEN4_HGE_SRC") or gen4_pins.default_locations().sources["hg_engine_fork"])
CACHE = Path(os.environ.get("SLINK_GEN4_PRET_CACHE", "C:/slink-cache/gen4-pret"))
TITLES = ("heartgold", "soulsilver", "heartgold_hge")
PRET_BUILD = {"heartgold": "heartgold", "soulsilver": "soulsilver"}
PROVENANCE = REPO / "data" / "gen4" / "pret_build_provenance.json"
CRT0 = (0x02000800, 0x02000BA0)  # lib/asm/crt0.s _start .. _start_ModuleParams

# Mirror-alias literals proven to be data, keyed by the exact "path:line value" the scan reports, so a moved or
# changed literal goes back to UNPROVEN (pret ad7a3afa).
_DPM = ("high word of the IEEE-754 double 0x01A56E1FC2F8F359 (~1e-300), loaded as r1 of the r0:r1 pair passed to "
        "_dmul (MSL_DPMath_s_ldexp.s:59-61); data, not an address")
DATA_ALIASES = {f"lib/MSL_C/asm/MSL_DPMath_e_pow.s:{n} 0x01A56E1F": _DPM for n in (459, 932, 939, 956, 963)}
DATA_ALIASES.update({f"lib/MSL_C/asm/MSL_DPMath_s_ldexp.s:{n} 0x01A56E1F": _DPM for n in (60, 130, 135, 158)})

# Files that may mention ITCM at all, and the role that makes each harmless (pret ad7a3afa).
KNOWN_ITCM_USES = {
    "lib/NitroSDK/src/os/os_arena.c": "arena default tables and OS_SetArena* init (os_arena.c:40-41,101,167); no allocation",
    "lib/include/nitro/hw/ARM9/mmap.h": "HW_ITCM* macro definitions (mmap.h:9-12,63)",
    "lib/include/nitro/os/common/arena_shared.h": "OSArenaId enum value (arena_shared.h:23)",
    "lib/include/nitro/card/rom.h": "CARD range-overlap predicate, read-only (rom.h:85-87)",
    "lib/NitroSDK/src/os/os_exception.c": "OSi_ExceptionHandler stack at HW_ITCM_END, crash path that traps forever (os_exception.c:45)",
    "src/poke_overlay.c": "overlay load-destination classification (poke_overlay.c:51-54); no ROM overlay targets ITCM (W1)",
    "main.lsf": "Autoload ITCM block (main.lsf:462-470)",
}
EXPECTED_ITCM_OBJECTS = (
    "lib/NitroSDK/src/os/os_irqHandler.o (.itcm)", "lib/NitroSDK/src/os/os_reset.o (.itcm)",
    "lib/NitroSDK/asm/mi_dma.o (.itcm)", "lib/NitroSDK/asm/mi_dma_gxcommand.o (.itcm)", "lib/asm/nitro.o (.itcm)",
)
ITCM_TOKEN = re.compile(
    r"\b(OS_ARENA_ITCM|HW_ITCM_IMAGE|HW_ITCM_END|HW_ITCM_SIZE|HW_ITCM_ARENA_HI_DEFAULT|HW_ITCM|"
    r"SDK_SECTION_ARENA_ITCM_START|OSi_ITCM_ARENA_LO_DEFAULT|SDK_AUTOLOAD_ITCM\w*|SDK_AUTOLOAD\.ITCM\.\w+)\b")
ALLOC_ITCM = re.compile(r"OS_(AllocFromArena(?:Lo|Hi)|AllocFromHeap|InitAlloc|CreateHeap)\s*\(\s*OS_ARENA_ITCM\b")
SETARENA_ITCM = re.compile(r"OS_SetArena(?:Lo|Hi)\s*\(\s*OS_ARENA_ITCM\b")
ARENA_CALL = re.compile(r"OS_(?:AllocFromArena(?:Lo|Hi)|AllocFromHeap|InitAlloc|CreateHeap)\s*\(\s*([^,)]+)")
ASM_ARENA_CALL = re.compile(r"^\s*bl\s+OS_(AllocFromArenaLo|AllocFromArenaHi|AllocFromHeap|InitAlloc|CreateHeap|SetArenaLo|SetArenaHi)\b")
LITERAL = re.compile(r"\b0[xX]([0-9A-Fa-f]{7,8})\b")
SCAN_DIRS = ("asm", "src", "lib", "include")
SCAN_EXT = (".c", ".h", ".s", ".inc", ".lsf", ".asm", ".ld")
ARENA_ID_WRAPPERS = ("lib/NitroSDK/src/os/os_arena.c", "lib/NitroSDK/src/os/os_alloc.c")


def _row(status, detail, cites=(), **data):
    return {"status": status, "detail": detail, "cites": list(cites), **({"data": data} if data else {})}


def worst(statuses):
    statuses = list(statuses)
    return FAIL if FAIL in statuses else UNPROVEN if UNPROVEN in statuses or not statuses else PASS


def _line(text, pos):
    return text.count("\n", 0, pos) + 1


def parse_nm(text):
    out = []
    for line in text.splitlines():
        m = re.match(r"^([0-9a-fA-F]{8})\s+(\w)\s+(\S+)\s*$", line)
        if m:
            out.append((int(m[1], 16), m[2], m[3]))
    return out


def parse_xmap(text):
    out = []
    for line in text.splitlines():
        m = re.match(r"^\s+([0-9A-F]{8}) ([0-9A-F]{8}) (\S+)\s+(\S.*?)\s*\((\S+)\)\s*$", line)
        if m:
            out.append((int(m[1], 16), int(m[2], 16), m[3], m[4], m[5]))
    return out


def span_check(span):
    lo, hi = span
    if not (ARENA[0] <= lo < hi <= ARENA[1]):
        return _row(FAIL, f"span {lo:#010x}..{hi:#010x} is not inside the ITCM arena tail {ARENA[0]:#010x}..{ARENA[1]:#010x}")
    return None


# ---------------------------------------------------------------------------------------------- W1
def w1_elf(nm, xmap, span):
    """Empty in the pinned ELF: no symbol, no sized object, not under the .itcm autoload."""
    bad = span_check(span)
    if bad:
        return bad
    lo, hi = span
    syms, ents = parse_nm(nm), parse_xmap(xmap)
    by = {n: a for a, _, n in syms}
    for need in ("SDK_AUTOLOAD_ITCM_END", "SDK_SECTION_ARENA_ITCM_START"):
        if need not in by:
            return _row(UNPROVEN, f"nm lacks {need}; cannot locate the end of the .itcm autoload")
    itcm_end = max(by["SDK_AUTOLOAD_ITCM_END"], by.get("SDK_AUTOLOAD.ITCM.END", 0))
    in_page = [e for e in ents if ITCM_PAGE[0] <= e[0] < ITCM_PAGE[1]]
    map_end = max([a + s for a, s, *_ in in_page] or [0])
    itcm_end = max(itcm_end, map_end)
    cites = ["nm_n.txt SDK_AUTOLOAD_ITCM_END", "main.elf.xMAP '# .ITCM' block"]
    if itcm_end > lo:
        return _row(FAIL, f"span overlaps the .itcm autoload (ends {itcm_end:#010x} > span start {lo:#010x})", cites)
    inside = [(f"{a:#010x}", n) for a, _, n in syms if lo <= a < hi]
    if inside:
        return _row(FAIL, f"symbol(s) inside the span: {inside[:6]}", cites)
    sized = [(f"{a:#010x}+{s:#x}", n) for a, s, _, n, _ in ents if s and a < hi and a + s > lo]
    if sized:
        return _row(FAIL, f"linker object(s) cover the span: {sized[:6]}", cites)
    if by["SDK_SECTION_ARENA_ITCM_START"] != itcm_end:
        return _row(UNPROVEN, f"arena start {by['SDK_SECTION_ARENA_ITCM_START']:#010x} != autoload end {itcm_end:#010x}", cites)
    return _row(PASS, f"no symbol or object in {lo:#010x}..{hi:#010x}; .itcm autoload ends {itcm_end:#010x} == arena start",
                cites, itcm_end=itcm_end, itcm_page_symbols=len([1 for a, _, _ in syms if ITCM_PAGE[0] <= a < ITCM_PAGE[1]]))


def w1_rom(rom, span):
    """The ROM's own ARM9 autoload table and overlay table (independent of any map)."""
    bad = span_check(span)
    if bad:
        return bad
    if not rom:
        return _row(UNPROVEN, "ROM not available: the autoload table is unread")
    lo, hi = span
    cites = ["ARM9 ModuleParams autoload list (ndspy)", "ARM9 overlay table (ndspy)"]
    itcm = [e for e in rom["autoload"] if e[0] == ITCM_PAGE[0]]
    if len(itcm) != 1:
        return _row(UNPROVEN, f"expected exactly one ITCM autoload entry, found {len(itcm)}", cites)
    end = itcm[0][0] + itcm[0][1] + itcm[0][2]
    if end > lo:
        return _row(FAIL, f"span overlaps the ROM .itcm autoload (ends {end:#010x} > {lo:#010x})", cites)
    for a, size, bss in rom["autoload"]:
        if a < hi and a + size + bss > lo:
            return _row(FAIL, f"autoload block {a:#010x}+{size + bss:#x} covers the span", cites)
    for ov_id, ram, size, bss in rom["overlays"]:
        if ram < 0x02000000 and ram + size + bss > 0x01000000:
            return _row(FAIL, f"overlay {ov_id} load range {ram:#010x}+{size + bss:#x} reaches the ITCM mirror window", cites)
    return _row(PASS, f"ROM ITCM autoload ends {end:#010x} <= span start; DTCM/other autoloads and all {len(rom['overlays'])} "
                      f"overlays (lowest ram {min([o[1] for o in rom['overlays']] or [0]):#010x}) miss the ITCM window",
                cites, itcm_end=end, overlays=len(rom["overlays"]))


# ---------------------------------------------------------------------------------------------- W2
def _hex_hits(text, span, path):
    lo, hi = span
    prim, alias, low = [], [], []
    for m in LITERAL.finditer(text):
        v = int(m[1], 16)
        spot = f"{path}:{_line(text, m.start())} {m[0]}"
        if lo <= v < hi:
            prim.append(spot)
        elif 0x01000000 <= v < 0x02000000 and (lo & 0x7FFF) <= (v & 0x7FFF) < (hi & 0x7FFF or 0x8000):
            alias.append(spot)
        elif len(m[1]) == 8 and v < 0x8000 and (lo & 0x7FFF) <= v < (hi & 0x7FFF or 0x8000):
            low.append(spot)
    return prim, alias, low


def w2_source(files, span, *, alias_allow=()):
    """files: {relative path: text}. Literal, symbol and arena-allocation scan of the pinned source."""
    bad = span_check(span)
    if bad:
        return bad
    if not files:
        return _row(UNPROVEN, "no source files")
    fails, unproven, uses, lows, calls = [], [], {}, [], 0
    for path, text in sorted(files.items()):
        for m in ALLOC_ITCM.finditer(text):
            fails.append(f"ITCM allocation {path}:{_line(text, m.start())}: {m.group(0)[:60]}")
        if path != "lib/NitroSDK/src/os/os_arena.c":
            for m in SETARENA_ITCM.finditer(text):
                fails.append(f"ITCM arena rewrite outside os_arena.c {path}:{_line(text, m.start())}")
        for m in ITCM_TOKEN.finditer(text):
            uses.setdefault(path, []).append(f"{_line(text, m.start())}:{m[0]}")
        prim, alias, low = _hex_hits(text, span, path)
        fails += [f"literal in span {p}" for p in prim]
        unproven += [f"mirror-alias literal {a} (unclassified)" for a in alias if a not in alias_allow]
        lows += low
        if path.endswith((".c", ".h")):
            for m in ARENA_CALL.finditer(text):
                calls += 1
                arg = m.group(1).strip()
                if arg.startswith("OS_ARENA_") and arg != "OS_ARENA_ITCM":
                    continue
                if arg != "OS_ARENA_ITCM" and path in ARENA_ID_WRAPPERS or path.startswith("lib/include/nitro/os/"):
                    continue
                if arg != "OS_ARENA_ITCM":
                    unproven.append(f"arena id not a literal {path}:{_line(text, m.start())}: {arg}")
        elif path.endswith((".s", ".inc")):
            lines = text.splitlines()
            for i, ln in enumerate(lines):
                m = ASM_ARENA_CALL.match(ln)
                if not m:
                    continue
                calls += 1
                const = None
                for back in lines[max(0, i - 12):i][::-1]:
                    if re.match(r"^\s*(bl|blx|b|bx)\b", back) or re.match(r"^\S+:", back):
                        break
                    mv = re.match(r"^\s*mov\s+r0,\s*#(\d+)\b", back)
                    if mv:
                        const = int(mv[1])
                        break
                if const == 3:
                    fails.append(f"ITCM arena id (3) passed to OS_{m[1]} {path}:{i + 1}")
                elif const is None:
                    unproven.append(f"arena id not a constant {path}:{i + 1} OS_{m[1]}")
        if "NNS_FndInitAllocatorForSDKHeap" in text and "bl NNS_FndInitAllocatorForSDKHeap" in text:
            unproven.append(f"SDK-heap allocator instantiated {path}: arena id is object-held")
    for path in uses:
        if path not in KNOWN_ITCM_USES:
            fails.append(f"new ITCM-token use in {path}: {uses[path][:4]}")
    lsf = files.get("main.lsf")
    if lsf is not None:
        block = re.search(r"Autoload ITCM\s*\{(.*?)\}", lsf, re.S)
        objs = tuple(re.findall(r"^\s*Object\s+(.+?)\s*$", block.group(1), re.M)) if block else ()
        if objs != EXPECTED_ITCM_OBJECTS:
            unproven.append(f"main.lsf ITCM autoload membership changed: {objs}")
    cites = [f"{p} ({KNOWN_ITCM_USES.get(p, 'UNKNOWN')}): {' '.join(v[:6])}" for p, v in sorted(uses.items())]
    if fails:
        return _row(FAIL, "; ".join(fails[:6]), cites)
    if unproven:
        return _row(UNPROVEN, "; ".join(unproven[:6]), cites)
    return _row(PASS, f"{len(files)} files: no literal in span, no ITCM allocation, {calls} arena calls all literal MAIN/MAINEX or "
                      f"id-wrapper definitions; every ITCM-token use is a known role",
                cites, itcm_token_files=sorted(uses), null_alias_candidates=len(lows),
                residual="computed-pointer / NULL+offset (ITCM mirrors at 0x0..0x7FFF) writes are not statically visible")


def rom_literal_hits(images, span):
    """images: {name: (ram_base, bytes)}. Aligned words that point into the span."""
    lo, hi = span
    hits = []
    for name, (base, data) in sorted(images.items()):
        for k, (v,) in enumerate(struct.iter_unpack("<I", data[:len(data) // 4 * 4])):
            if lo <= v < hi:
                hits.append((name, base + 4 * k, v))
    return hits


def w2_rom(rom, span):
    if not rom:
        return _row(UNPROVEN, "ROM not available: literal pools unread")
    hits = rom_literal_hits(rom["images"], span)
    cites = ["aligned-word scan of ARM9 + all overlays (ndspy)"]
    if hits:
        return _row(UNPROVEN, f"{len(hits)} ROM word(s) point into the span, e.g. {[(h[0], hex(h[1]), hex(h[2])) for h in hits[:4]]}", cites)
    return _row(PASS, f"no aligned ROM word in ARM9 + {len(rom['images']) - 1} overlays points into the span "
                      f"(instruction-encoded and mirror addresses are not covered; the canary watch is the tripwire)", cites)


# ---------------------------------------------------------------------------------------------- W3
def _defines(files):
    out = {}
    for path in ("lib/include/nitro/hw/ARM9/mmap.h", "lib/include/nitro/hw/mmap_shared.h", "lib/include/nitro/hw/consts.h"):
        for m in re.finditer(r"^#define[ \t]+(\w+)[ \t]+(.+?)[ \t]*(?://.*)?$", files.get(path, ""), re.M):
            out[m[1]] = m[2]
    return out


def _eval(expr, defs, depth=0):
    expr = expr.strip()
    if depth > 8:
        return None
    toks = re.findall(r"0[xX][0-9A-Fa-f]+|\d+|\w+|[()+\-*]", expr)
    py = []
    for t in toks:
        if re.fullmatch(r"0[xX][0-9A-Fa-f]+|\d+|[()+\-*]", t):
            py.append(t)
        elif t in defs:
            v = _eval(defs[t], defs, depth + 1)
            if v is None:
                return None
            py.append(str(v))
        elif t == "u32":
            continue
        else:
            return None
    try:
        return eval("".join(py), {"__builtins__": {}}) & 0xFFFFFFFF  # noqa: S307 - digits/operators only
    except Exception:
        return None


def w3_reset(files, symbols, span, rom=None, ref_rom=None):
    """OS_ResetSystem / crt0 never clear the span. rom/ref_rom (hge only) prove byte-identity to the reference."""
    bad = span_check(span)
    if bad:
        return bad
    lo, hi = span
    reset = files.get("lib/NitroSDK/src/os/os_reset.c")
    crt0 = files.get("lib/asm/crt0.s")
    if reset is None or crt0 is None:
        return _row(UNPROVEN, "os_reset.c or crt0.s absent")
    cites = ["lib/NitroSDK/src/os/os_reset.c OSi_DoBoot", "lib/asm/crt0.s _start / do_autoload"]
    unproven, spans = [], []
    arm9 = reset.split("#include <nitro/itcm_end.h>", 1)[0]  # the ARM9 branch ends where the ITCM section does
    body = re.search(r"asm void OSi_DoBoot\(void\)\s*\{(.*?)\n\}\s*\n\s*asm u32 OSi_CpuClear32", arm9, re.S)
    if not body:
        return _row(UNPROVEN, "cannot locate the ARM9 OSi_DoBoot body", cites)
    defs = _defines(files)
    for m in re.finditer(r"ldr r1,\s*=(\w+)\s*\n\s*mov r2,\s*#(\w+)\s*\n\s*bl OSi_CpuClear32", body.group(1)):
        start, size = _eval(m[1], defs), _eval(m[2], defs)
        if start is None or size is None:
            unproven.append(f"OSi_DoBoot clear target {m[1]}+{m[2]} unresolved")
        else:
            spans.append((f"OSi_DoBoot {m[1]}", start, start + size))
    if len(spans) != body.group(1).count("bl OSi_CpuClear32") and not unproven:
        unproven.append(f"OSi_DoBoot has {body.group(1).count('bl OSi_CpuClear32')} OSi_CpuClear32 calls, only {len(spans)} parsed")
    if len(spans) != 3 and not unproven:
        unproven.append(f"expected 3 OSi_CpuClear32 targets in OSi_DoBoot, parsed {len(spans)}")
    start_fn = crt0.split("arm_func_start _start\n", 1)[-1].split("arm_func_end _start\n", 1)[0]
    clears = re.findall(r"ldr r1, \w+ ; =(\w+)\s*\n\s*mov r2, #(\w+)\s*\n\s*bl INITi_CpuClear32", start_fn)
    by = symbols or {}
    for sym, size in clears:
        a = by.get(sym) if not sym.startswith("0x") else int(sym, 16)
        n = _eval(size, {})
        if a is None or n is None:
            unproven.append(f"crt0 clear {sym}+{size} unresolved (need the nm symbol)")
        else:
            spans.append((f"crt0 {sym}", a, a + n))
    if len(clears) != start_fn.count("bl INITi_CpuClear32") or len(clears) != 3:
        unproven.append(f"crt0 _start has {start_fn.count('bl INITi_CpuClear32')} INITi_CpuClear32 calls, parsed {len(clears)} (expected 3)")
    for sym in ("SDK_STATIC_BSS_START", "SDK_STATIC_BSS_END"):
        if sym not in by:
            unproven.append(f"no address for {sym}")
    if "SDK_STATIC_BSS_START" in by and "SDK_STATIC_BSS_END" in by:
        spans.append(("crt0 static bss", by["SDK_STATIC_BSS_START"], by["SDK_STATIC_BSS_END"]))
    hit = [f"{n} {a:#010x}..{b:#010x}" for n, a, b in spans if a < hi and b > lo]
    if hit:
        return _row(FAIL, f"reset/boot clear covers the span: {hit}", cites)
    itcm_bss = [e for e in (rom or {}).get("autoload", []) if e[0] == ITCM_PAGE[0]]
    if itcm_bss and itcm_bss[0][2]:
        unproven.append(f"ITCM autoload has {itcm_bss[0][2]:#x} bytes of bss cleared at every boot")
    if ref_rom is not None and (not rom or rom.get("crt0") != ref_rom.get("crt0") or rom.get("itcm_image") != ref_rom.get("itcm_image")):
        unproven.append("crt0 or the ITCM autoload image differs from the pinned HG ARM9 (reset path not proven identical)")
    if unproven:
        return _row(UNPROVEN, "; ".join(unproven[:5]), cites)
    return _row(PASS, f"OSi_DoBoot clears {len([s for s in spans if s[0].startswith('OSi')])} main-RAM blocks; crt0 clears DTCM, palette, OAM, "
                      f"static bss and the (empty) autoload bss; none reaches {lo:#010x}..{hi:#010x}. Only a hard reset zeroes ITCM",
                cites, cleared=[(n, f"{a:#010x}", f"{b:#010x}") for n, a, b in spans])


# ---------------------------------------------------------------------------------------------- input loading
def git_head(path):
    try:
        return subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=15, check=True).stdout.strip()
    except Exception:
        return None


def read_tree(root, dirs=SCAN_DIRS, ext=SCAN_EXT, extra=()):
    files = {}
    for d in dirs:
        for dp, _, names in os.walk(root / d):
            for n in names:
                if n.endswith(ext):
                    p = Path(dp) / n
                    files[p.relative_to(root).as_posix()] = p.read_text(encoding="utf-8", errors="replace")
    for n in extra:
        if (root / n).is_file():
            files[n] = (root / n).read_text(encoding="utf-8", errors="replace")
    return files


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_rom(path, span):
    """Plain-data facts from a ROM: autoload table, overlay table, ARM9/overlay images."""
    import ndspy.codeCompression as cc
    import ndspy.rom
    rom = ndspy.rom.NintendoDSRom.fromFile(str(path))
    raw = bytes(rom.arm9)
    try:
        img, compressed = bytes(cc.decompress(raw)), True
    except Exception:
        img, compressed = raw, False
    i = img.find(struct.pack("<I", 0x2106C0DE))
    if i < 32:
        return None
    base = rom.arm9RamAddress
    ls, le, auto, bss0, bss1 = struct.unpack_from("<5I", img, i - 32)
    table = [struct.unpack_from("<3I", img, ls - base + 12 * k) for k in range((le - ls) // 12)]
    itcm = next((e for e in table if e[0] == ITCM_PAGE[0]), None)
    images = {"arm9": (base, img)}
    overlays = []
    for oid, ov in sorted(rom.loadArm9Overlays().items()):
        data = bytes(ov.data)
        if ov.compressed:
            with contextlib.suppress(Exception):  # a raw overlay stays raw
                data = bytes(cc.decompress(data))
        images[f"ov{oid}"] = (ov.ramAddress, data)
        overlays.append((oid, ov.ramAddress, ov.ramSize, ov.bssSize))
    dtcm = next((e[0] for e in table if e[0] >= 0x02700000), None)
    syms = {"SDK_STATIC_BSS_START": bss0, "SDK_STATIC_BSS_END": bss1, **({"SDK_AUTOLOAD_DTCM_START": dtcm} if dtcm else {})}
    return {"autoload": table, "overlays": overlays, "symbols": syms, "images": images, "arm9_compressed": compressed,
            "crt0": img[CRT0[0] - base:CRT0[1] - base],
            "itcm_image": img[auto - base:auto - base + itcm[1]] if itcm else b""}


def _read(path):
    p = Path(path)
    return p.read_text(encoding="utf-8", errors="replace") if p.is_file() else None


def census(span=DEFAULT_SPAN, *, pret=PRET, cache=CACHE, hge=HGE, roms=None, titles=TITLES, pret_files=None, hge_files=None):
    """Whole census. Present-but-wrong pins are FAIL rows; absent inputs are UNPROVEN rows, never PASS."""
    from tools import gen4_pins
    roms = roms or {t: Path(os.environ.get("SLINK_GEN4_" + t.upper(), p)) for t, p in gen4_pins.default_locations().roms.items()}
    pins = {}
    head = git_head(pret) if pret_files is None else PRET_PIN
    pins["pret"] = _row(PASS if head == PRET_PIN else UNPROVEN if head is None else FAIL, f"pret HEAD {head} vs pin {PRET_PIN}")
    files = pret_files if pret_files is not None else read_tree(pret)
    provenance = json.loads(PROVENANCE.read_text(encoding="utf-8")) if PROVENANCE.is_file() else {}
    rom_facts, rom_bad, out = {}, {}, {}
    for title in titles:
        rom_path = Path(roms[title])
        want = gen4_pins.ROM_SPECS[title][0]
        facts = None
        if rom_path.is_file():
            got = hashlib.sha1(rom_path.read_bytes()).hexdigest()
            if got != want:
                rom_bad[title] = _row(FAIL, f"{title} ROM sha1 {got} != pinned {want}")
            else:
                facts = load_rom(rom_path, span)
        rom_facts[title] = facts
    ref = rom_facts.get("heartgold")
    for title in titles:
        rows, nm, xmap = {}, None, None
        facts = rom_facts[title]
        absent = _row(UNPROVEN, f"{title} ROM absent or unreadable: {roms[title]}")
        if title in PRET_BUILD:
            d = cache / PRET_BUILD[title]
            nm, xmap = _read(d / "nm_n.txt"), _read(d / "main.elf.xMAP")
            built = provenance.get("builds", {}).get(PRET_BUILD[title].upper(), {})
            elf = d / "main.elf"
            if nm is None or xmap is None:
                rows["W1"] = _row(UNPROVEN, f"pinned pret build artifacts absent under {d}")
            elif elf.is_file() and built.get("main_elf_sha256") and sha256(elf) != built["main_elf_sha256"]:
                rows["W1"] = _row(FAIL, f"{elf} sha256 differs from data/gen4/pret_build_provenance.json")
            else:
                a, b = w1_elf(nm, xmap, span), w1_rom(facts, span)
                rows["W1"] = _row(worst([a["status"], b["status"]]), f"ELF: {a['detail']} | ROM: {b['detail']}", a["cites"] + b["cites"])
        else:
            rows["W1"] = w1_rom(facts, span) if facts else absent
        src = dict(files)
        if title == "heartgold_hge":
            fork = hge_files if hge_files is not None else read_tree(hge, dirs=("src", "include", "asm", "armips", "hooks", "armhooks"), extra=("rom.ld", "Makefile"))
            fhead = git_head(hge) if hge_files is None else HGE_PIN
            a = w2_source(src, span, alias_allow=DATA_ALIASES)
            b = w2_source({"hge:" + k: v for k, v in fork.items()}, span) if fork else _row(UNPROVEN, "hge fork source absent")
            # fork paths are not in KNOWN_ITCM_USES: any ITCM token there is a new use, which is what the scan reports.
            note = "" if fhead == HGE_PIN else f" (fork HEAD {fhead} != pin {HGE_PIN})"
            rows["W2"] = _row(worst([a["status"], b["status"]] + ([UNPROVEN] if note else [])),
                              f"pret base: {a['detail']} | hge fork: {b['detail']}{note}", a["cites"] + b["cites"])
        else:
            rows["W2"] = w2_source(src, span, alias_allow=DATA_ALIASES)
        rows["W2b"] = w2_rom(facts, span) if facts else absent
        if title in PRET_BUILD:
            rows["W3"] = w3_reset(src, {n: a for a, _, n in parse_nm(nm or "")}, span, facts)
        else:
            rows["W3"] = w3_reset(src, facts["symbols"], span, facts, ref_rom=ref) if facts else absent
        if title in rom_bad:
            rows = dict.fromkeys(rows, rom_bad[title])
        out[title] = {"rows": rows, "status": worst(r["status"] for r in rows.values()),
                      "candidate": {"lo": f"{span[0]:#010x}", "hi_exclusive": f"{span[1]:#010x}", "domain": "Instruction TCM",
                                    "domain_offset": f"{span[0] & 0x7FFF:#06x}"},
                      "accepted": None, "accepted_note": "needs the live canary receipts (tests/live/test_gen4_mailbox.py) on this artifact"}
    statuses = [pins["pret"]["status"]] + [v["status"] for v in out.values()]
    return {"schema": "gen4-mailbox-census-v1", "span": [f"{span[0]:#010x}", f"{span[1]:#010x}"], "arena": [f"{ARENA[0]:#010x}", f"{ARENA[1]:#010x}"],
            "pins": pins, "artifacts": out, "result": worst(statuses)}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--span", nargs=2, type=lambda s: int(s, 0), metavar=("LO", "HI"), default=list(DEFAULT_SPAN))
    ap.add_argument("--out", type=Path)
    args = ap.parse_args(argv)
    report = census(tuple(args.span))
    text = json.dumps(report, indent=2, sort_keys=True)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return {PASS: 0, FAIL: 1, UNPROVEN: 2}[report["result"]]


if __name__ == "__main__":
    sys.exit(main())
