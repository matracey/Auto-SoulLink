"""Gen 4 companion mailbox census (card C1): every row must go red on its mutation and green on revert.

Synthetic fixtures prove the instrument; the real-input tests prove the pinned pret build and the three ROMs.
Skip only when a pinned input is absent. Present but wrong is a failure, never a skip.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from tools import gen4_mailbox_census as c

SPAN = c.DEFAULT_SPAN
LO, HI = SPAN

NM = """\
01ff8000 T OS_IrqHandler
01ff8000 T SDK_AUTOLOAD_ITCM_START
01ff8614 T SDK_AUTOLOAD.ITCM.DATA_START
01ff8620 T SDK_AUTOLOAD_ITCM_END
01ff8620 T SDK_AUTOLOAD.ITCM.END
01ff8620 D SDK_SECTION_ARENA_ITCM_START
02000000 T SDK_STATIC_START
"""
XMAP = "  01FF8000 00000058 .itcm   OS_IrqHandler\t(os_irqHandler.o)\n  01FF8058 0000015C .itcm   OS_IrqHandler_ThreadSwitch\t(os_irqHandler.o)\n"
ROM = {
    "autoload": [(0x01FF8000, 0x620, 0), (0x027E0000, 0x60, 0x20)],
    "overlays": [(0, 0x021E5900, 0x1000, 0x20), (1, 0x021E5900, 0x2000, 0)],
    "images": {"arm9": (0x02000000, b"\0" * 64), "ov0": (0x021E5900, b"\0" * 64)},
    "crt0": b"crt0", "itcm_image": b"itcm", "symbols": {},
}
LSF = "Autoload ITCM\n{\n    Address 0x01FF8000\n" + "".join(f"    Object {o}\n" for o in c.EXPECTED_ITCM_OBJECTS) + "}\n"
FILES = {
    "lib/NitroSDK/src/os/os_arena.c": "OS_SetArenaHi(OS_ARENA_ITCM, OS_GetInitArenaHi(OS_ARENA_ITCM));\ncase OS_ARENA_ITCM:\n",
    "src/heap.c": "OS_AllocFromArenaLo(OS_ARENA_MAIN, 4, 4);\nOS_AllocFromArenaHi(OS_ARENA_MAINEX, 4, 4);\n",
    "asm/ok.s": "\tmov r0, #0\n\tmov r1, #4\n\tbl OS_AllocFromHeap\n",
    "main.lsf": LSF,
}


def mutated(files, **edits):
    out = dict(files)
    out.update(edits)
    return out


def red_then_green(good, mutate, want, *, expect_good=c.PASS):
    """The mutation turns the row `want`, and reverting it returns the row to green."""
    assert good(None)["status"] == expect_good, good(None)
    assert good(mutate)["status"] == want, good(mutate)
    assert good(None)["status"] == expect_good


# ---------------------------------------------------------------------------------------------- W1
def w1(mut):
    nm, xmap, span = NM, XMAP, SPAN
    if mut:
        nm, xmap, span = mut(nm, xmap, span)
    return c.w1_elf(nm, xmap, span)


@pytest.mark.parametrize("name,mutate,want", [
    ("fake symbol inside the span", lambda n, x, s: (n + f"{LO + 0x80:08x} D fake_sym\n", x, s), c.FAIL),
    ("fake sized object over the span", lambda n, x, s: (n, x + f"  {LO - 0x10:08X} 00000100 .bss   fake_obj\t(fake.o)\n", s), c.FAIL),
    ("autoload grown over the span", lambda n, x, s: (n.replace("01ff8620 T SDK_AUTOLOAD_ITCM_END", f"{LO + 4:08x} T SDK_AUTOLOAD_ITCM_END"), x, s), c.FAIL),
    ("span overlaps the .itcm autoload", lambda n, x, s: (n, x, (0x01FF8400, 0x01FF8800)), c.FAIL),
    ("span outside the arena tail", lambda n, x, s: (n, x, (0x02000000, 0x02000100)), c.FAIL),
    ("arena start disagrees with autoload end", lambda n, x, s: (n.replace("01ff8620 D SDK_SECTION", "01ff8640 D SDK_SECTION"), x, s), c.UNPROVEN),
    ("nm lacks the autoload end", lambda n, x, s: (n.replace("SDK_AUTOLOAD_ITCM_END", "SDK_RENAMED"), x, s), c.UNPROVEN),
])
def test_w1_elf_red_on_mutation_green_on_revert(name, mutate, want):
    red_then_green(w1, mutate, want)


def rom1(mut):
    rom, span = copy.deepcopy(ROM), SPAN
    if mut:
        rom, span = mut(rom, span)
    return c.w1_rom(rom, span)


@pytest.mark.parametrize("name,mutate,want", [
    ("ITCM autoload extends into the span", lambda r, s: ({**r, "autoload": [(0x01FF8000, LO - 0x01FF8000 + 8, 0), r["autoload"][1]]}, s), c.FAIL),
    ("ITCM autoload bss extends into the span", lambda r, s: ({**r, "autoload": [(0x01FF8000, 0x620, 0x7400), r["autoload"][1]]}, s), c.FAIL),
    ("a second autoload covers the span", lambda r, s: ({**r, "autoload": r["autoload"] + [(LO - 4, 0x100, 0)]}, s), c.FAIL),
    ("an overlay loads into the ITCM window", lambda r, s: ({**r, "overlays": r["overlays"] + [(9, 0x01FFF000, 0x2000, 0)]}, s), c.FAIL),
    ("no ITCM autoload entry", lambda r, s: ({**r, "autoload": [r["autoload"][1]]}, s), c.UNPROVEN),
    ("span overlaps .itcm", lambda r, s: (r, (0x01FF8000, 0x01FF8100)), c.FAIL),
])
def test_w1_rom_autoload_and_overlay_table_red_green(name, mutate, want):
    red_then_green(rom1, mutate, want)


def test_w1_rom_absent_is_unproven_not_pass():
    assert c.w1_rom(None, SPAN)["status"] == c.UNPROVEN


# ---------------------------------------------------------------------------------------------- W2
def w2(mut):
    files, span = dict(FILES), SPAN
    if mut:
        files, span = mut(files, span)
    return c.w2_source(files, span)


@pytest.mark.parametrize("name,mutate,want", [
    ("fake OS_ARENA_ITCM allocation", lambda f, s: (mutated(f, **{"src/new.c": "void *p = OS_AllocFromArenaLo(OS_ARENA_ITCM, 0x400, 4);\n"}), s), c.FAIL),
    ("multi-line OS_ARENA_ITCM allocation", lambda f, s: (mutated(f, **{"src/new.c": "OS_AllocFromArenaHi(\n    OS_ARENA_ITCM,\n    0x400, 4);\n"}), s), c.FAIL),
    ("ITCM allocation hidden in os_arena.c", lambda f, s: (mutated(f, **{"lib/NitroSDK/src/os/os_arena.c": FILES["lib/NitroSDK/src/os/os_arena.c"] + "OS_CreateHeap(OS_ARENA_ITCM, a, b);\n"}), s), c.FAIL),
    ("arena rewrite outside os_arena.c", lambda f, s: (mutated(f, **{"src/new.c": "OS_SetArenaLo(OS_ARENA_ITCM, 0x02000000);\n"}), s), c.FAIL),
    ("asm allocation with arena id 3", lambda f, s: (mutated(f, **{"asm/bad.s": "\tmov r0, #3\n\tmov r1, #4\n\tbl OS_AllocFromArenaLo\n"}), s), c.FAIL),
    ("hard-coded literal inside the span", lambda f, s: (mutated(f, **{"asm/lit.s": "_021: .word 0x01FFF880\n"}), s), c.FAIL),
    ("new ITCM symbol use in game code", lambda f, s: (mutated(f, **{"src/new.c": "u8 *p = (u8 *)HW_ITCM_END - 0x400;\n"}), s), c.FAIL),
    ("mirror-alias literal", lambda f, s: (mutated(f, **{"asm/alias.s": "_021: .word 0x01FF7880\n"}), s), c.UNPROVEN),
    ("computed arena id", lambda f, s: (mutated(f, **{"src/new.c": "OS_AllocFromArenaLo(id, 4, 4);\n"}), s), c.UNPROVEN),
    ("asm arena id not a constant", lambda f, s: (mutated(f, **{"asm/dyn.s": "\tbl OS_AllocFromHeap\n"}), s), c.UNPROVEN),
    ("ITCM autoload membership changed", lambda f, s: (mutated(f, **{"main.lsf": LSF.replace("}\n", "    Object src/mailbox.o (.itcm)\n}\n")}), s), c.UNPROVEN),
    ("span outside the arena", lambda f, s: (f, (0x02000000, 0x02000010)), c.FAIL),
])
def test_w2_source_red_on_mutation_green_on_revert(name, mutate, want):
    red_then_green(w2, mutate, want)


def test_w2_decimal_prefix_and_comments_do_not_false_positive():
    files = mutated(FILES, **{"src/n.c": "u32 x = 0x1FFF8; // 0x01FFF880 in a comment is still a literal, see below\n"})
    assert c.w2_source(files, SPAN)["status"] == c.FAIL  # a commented literal is still reported: no silent skip


def test_w2_known_roles_are_listed_as_citations():
    row = c.w2_source(dict(FILES), SPAN)
    assert any("os_arena.c" in cite for cite in row["cites"])


def test_w2_rom_literal_scan_red_green():
    import struct

    def row(mut):
        rom = copy.deepcopy(ROM)
        if mut:
            rom["images"]["ov0"] = (0x021E5900, b"\0" * 8 + struct.pack("<I", LO + 0x10) + b"\0" * 8)
        return c.w2_rom(rom, SPAN)

    red_then_green(row, True, c.UNPROVEN)
    assert c.w2_rom(None, SPAN)["status"] == c.UNPROVEN


# ---------------------------------------------------------------------------------------------- W3 (real pret reset code)
PRET_READY = c.PRET.is_dir() and c.git_head(c.PRET) == c.PRET_PIN
needs_pret = pytest.mark.skipif(not (c.PRET / "lib/NitroSDK/src/os/os_reset.c").is_file(), reason="pinned pret checkout absent")
SYMS = {"SDK_AUTOLOAD_DTCM_START": 0x027E0000, "SDK_STATIC_BSS_START": 0x02111860, "SDK_STATIC_BSS_END": 0x021E5900}


@pytest.fixture(scope="module")
def reset_files():
    paths = ["lib/NitroSDK/src/os/os_reset.c", "lib/asm/crt0.s", "lib/include/nitro/hw/ARM9/mmap.h",
             "lib/include/nitro/hw/mmap_shared.h", "lib/include/nitro/hw/consts.h"]
    return {p: (c.PRET / p).read_text(encoding="utf-8", errors="replace") for p in paths}


def edited(files, path, old, new):
    assert old in files[path], f"mutation anchor vanished from {path}"
    return mutated(files, **{path: files[path].replace(old, new)})


@needs_pret
def test_pinned_pret_head_is_the_pin():
    assert c.git_head(c.PRET) == c.PRET_PIN, "present pret checkout is not the pinned commit"


@needs_pret
@pytest.mark.parametrize("name,path,old,new,want", [
    ("OSi_DoBoot clear retargeted into the span", "lib/include/nitro/hw/ARM9/mmap.h",
     "#define HW_COMPONENT_PARAM      (HW_MAIN_MEM + 0x007fff9c)", "#define HW_COMPONENT_PARAM      (HW_ITCM + 0x7800)", c.FAIL),
    ("crt0 clear retargeted into the span", "lib/asm/crt0.s", "; =0x05000000", "; =0x01FFF800", c.FAIL),
    ("crt0 gains a fourth clear", "lib/asm/crt0.s", "\tbl INITi_CpuClear32\n\tmov r0, #0x200", "\tbl INITi_CpuClear32\n\tmov r2, #8\n\tbl INITi_CpuClear32\n\tmov r0, #0x200", c.UNPROVEN),
    ("OSi_DoBoot loses a clear", "lib/NitroSDK/src/os/os_reset.c", "    mov r2, #0x64\n    bl OSi_CpuClear32\n", "", c.UNPROVEN),
    ("unresolvable clear target", "lib/include/nitro/hw/ARM9/mmap.h", "#define HW_COMPONENT_PARAM      (HW_MAIN_MEM + 0x007fff9c)", "#define HW_COMPONENT_PARAM      (HW_NOPE + 1)", c.UNPROVEN),
])
def test_w3_reset_red_on_mutation_green_on_revert(reset_files, name, path, old, new, want):
    def row(mut):
        files = edited(reset_files, path, old, new) if mut else reset_files
        return c.w3_reset(files, SYMS, SPAN, ROM)

    red_then_green(row, True, want)


@needs_pret
def test_w3_itcm_autoload_bss_and_rom_identity(reset_files):
    bss = {**ROM, "autoload": [(0x01FF8000, 0x620, 0x20), ROM["autoload"][1]]}
    assert c.w3_reset(reset_files, SYMS, SPAN, bss)["status"] == c.UNPROVEN
    assert c.w3_reset(reset_files, SYMS, SPAN, ROM, ref_rom=ROM)["status"] == c.PASS
    assert c.w3_reset(reset_files, SYMS, SPAN, {**ROM, "crt0": b"other"}, ref_rom=ROM)["status"] == c.UNPROVEN
    assert c.w3_reset(reset_files, SYMS, SPAN, {**ROM, "itcm_image": b"other"}, ref_rom=ROM)["status"] == c.UNPROVEN
    assert c.w3_reset({}, SYMS, SPAN, ROM)["status"] == c.UNPROVEN


# ---------------------------------------------------------------------------------------------- real inputs
REAL = {t: c.CACHE / t for t in c.PRET_BUILD}
real_maps = pytest.mark.skipif(not all((d / "nm_n.txt").is_file() and (d / "main.elf.xMAP").is_file() for d in REAL.values()),
                               reason="pinned pret build artifacts absent")


@real_maps
@pytest.mark.parametrize("title", c.PRET_BUILD)
def test_real_map_is_empty_over_the_span_and_the_instrument_sees_a_planted_symbol(title):
    nm = (REAL[title] / "nm_n.txt").read_text(encoding="utf-8", errors="replace")
    xmap = (REAL[title] / "main.elf.xMAP").read_text(encoding="utf-8", errors="replace")
    assert c.w1_elf(nm, xmap, SPAN)["status"] == c.PASS
    # known-positive control on the real map: the linker's own zero-size markers (SDK_SECTION_ARENA_ITCM_START etc.) sit at
    # 0x01FF8620, so a span starting there must be reported (the instrument sees real symbols, not just planted ones).
    marker = c.w1_elf(nm, xmap, (0x01FF8620, 0x01FF8640))
    assert marker["status"] == c.FAIL and "symbol(s) inside the span" in marker["detail"] and "0x01ff8620" in marker["detail"]
    assert c.w1_elf(nm + f"{LO + 4:08x} D planted\n", xmap, SPAN)["status"] == c.FAIL
    assert c.w1_elf(nm, xmap, (0x01FF8000, 0x01FF8100))["status"] == c.FAIL  # the real autoload itself


@needs_pret
def test_real_source_tree_scans_clean_and_a_planted_allocation_is_caught():
    files = c.read_tree(c.PRET)
    assert len(files) > 1500, "pinned pret tree scan is implausibly small"
    assert c.w2_source(files, SPAN, alias_allow=c.DATA_ALIASES)["status"] == c.PASS
    bad = mutated(files, **{"src/planted.c": "void *p = OS_AllocFromArenaLo(OS_ARENA_ITCM, 0x400, 4);\n"})
    assert c.w2_source(bad, SPAN, alias_allow=c.DATA_ALIASES)["status"] == c.FAIL
    assert c.w2_source(files, SPAN, alias_allow=c.DATA_ALIASES)["status"] == c.PASS


def _need_real(titles) -> dict:
    roms = c.gen4_pins.default_locations().roms
    if not all(Path(roms[t]).is_file() for t in titles) or not PRET_READY or not all((d / "nm_n.txt").is_file() for d in REAL.values()):
        pytest.skip("pinned pret checkout, pret build cache or a Gen 4 ROM is absent")
    return roms


@pytest.fixture(scope="module")
def real_census():
    _need_real(c.TITLES)
    if "heartgold_hge" in c.TITLES and not (c.HGE / "charmap.txt").is_file():
        pytest.skip(c.gen4_pins.hge_fork_absent(c.HGE))
    return c.census(SPAN)


def test_real_census_all_artifacts_pass(real_census):
    assert real_census["pins"]["pret"]["status"] == c.PASS
    for title, art in real_census["artifacts"].items():
        assert {k: r["status"] for k, r in art["rows"].items()} == dict.fromkeys(("W1", "W2", "W2b", "W3"), c.PASS), (title, art["rows"])
        assert art["status"] == c.PASS and art["accepted"] is None
    assert real_census["result"] == c.PASS
    json.dumps(real_census)


def test_real_census_flags_a_span_that_overlaps_the_itcm_autoload():
    # same real inputs, span moved onto the autoload: every artifact's W1 must go red.
    roms = _need_real(("heartgold",))
    report = c.census((0x01FF8000, 0x01FF8100), titles=("heartgold",), roms=roms)
    assert report["artifacts"]["heartgold"]["rows"]["W1"]["status"] == c.FAIL


def test_cli_exit_codes(monkeypatch, capsys):
    monkeypatch.setattr(c, "census", lambda span: {"result": c.UNPROVEN, "span": [], "artifacts": {}})
    assert c.main([]) == 2
    monkeypatch.setattr(c, "census", lambda span: {"result": c.FAIL, "span": [], "artifacts": {}})
    assert c.main([]) == 1
    monkeypatch.setattr(c, "census", lambda span: {"result": c.PASS, "span": [], "artifacts": {}})
    assert c.main([]) == 0
    capsys.readouterr()


def test_data_alias_allowlist_is_exact_and_red_capable():
    """A listed data literal is cleared only at its exact path:line; the same value elsewhere stays UNPROVEN."""
    import tools.gen4_mailbox_census as c
    span = (0x01FFEC00, 0x01FFFC00)
    text = "\n" * 59 + "\tldr r1, _x ; =0x01A56E1F\n"
    listed = {"lib/MSL_C/asm/MSL_DPMath_s_ldexp.s": text}
    assert c.w2_source(listed, span, alias_allow=c.DATA_ALIASES)["status"] != c.UNPROVEN
    assert c.w2_source(listed, span)["status"] == c.UNPROVEN
    moved = {"lib/MSL_C/asm/MSL_DPMath_s_ldexp.s": "\n" + text}
    assert c.w2_source(moved, span, alias_allow=c.DATA_ALIASES)["status"] == c.UNPROVEN
