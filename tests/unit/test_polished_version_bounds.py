"""MODEL rendering: real UPS, native menu borders and SM83 version printer.

No video/timing claim: each changed tile occupies its corresponding 8x8 pixel cell.
The clock is a trap; menu rectangle computation, border paint, glyph paint and
native FarCall execute from the cartridge. Symbols come from committed build data.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from patch.tools.make_ups import ups_apply
from tests.unit.polished_sm83 import SM83

ROOT = Path(__file__).resolve().parents[2]
RELEASE = Path(os.environ.get("SLINK_WORK_ROOT", "F:/slink-work")) / "cache/polished/release/polishedcrystal-3.2.3.gbc"


@pytest.fixture(scope="module")
def version_rom():
    if not RELEASE.is_file():
        pytest.skip(f"pinned release unavailable: {RELEASE}")
    rom = ups_apply(RELEASE.read_bytes(), (ROOT / "patch/dist/SLink-Polished.ups").read_bytes())
    symbols = {name: (int(bank, 16), int(address, 16))
               for bank, address, name in re.findall(
                   r"^([0-9a-f]{2}):([0-9a-f]{4}) (\S+)$",
                   (ROOT / "data/polished/polished_slink.sym").read_text(), re.M)}
    return bytes(rom), symbols


@pytest.mark.parametrize("items", [3, 4, 5], ids=["new-game", "continue", "new-game-plus"])
@pytest.mark.parametrize("left,right", [(0, 16), (2, 14)], ids=["native-rectangle", "inset-rectangle"])
@pytest.mark.parametrize("line", [None, "SoulLink v12.45.678", "SoulLink dev"],
                         ids=["actual-stamped-field", "19-glyphs", "short"])
def test_version_glyph_cells_stay_inside_active_menu_columns(version_rom, items, left, right, line):
    rom, sym = version_rom
    def addr(name):
        return sym[name][1]

    bank = sym["MainMenu_PrintCurrentTimeAndDay"][0]
    m = SM83(rom, bank=bank, mbc="mbc3", farcall="native", hrombank=addr("hROMBank"))
    m.poke(addr("hROMBank"), bank)
    tilemap = addr("wTilemap")
    m.poke(tilemap, bytes([0x7F]) * 360)
    for name, value in (("wMenuBorderLeftCoord", left), ("wMenuBorderRightCoord", right),
                        ("wMenuBorderTopCoord", 0), ("wMenuDataItems", items)):
        m.poke(addr(name), value)
    m.call_routine(addr("AutomaticGetMenuBottomCoord"), sp=addr("wStackTop"))
    assert m.peek(addr("wMenuBorderBottomCoord")) == bytes([items * 2 + 1])
    m.call_routine(addr("MenuBox"), sp=addr("wStackTop"))
    # Actual native borders are the sentinel, including NG+'s row-10 right edge.
    before = m.peek(tilemap, 360)
    assert before[(items * 2 + 1) * 20 + right] != 0x7F
    glyphs = {" ": 0x7F, ".": 0x9C}
    glyphs.update({chr(65 + i): 0x80 + i for i in range(26)})
    glyphs.update({chr(97 + i): 0xA0 + i for i in range(26)})
    glyphs.update({str(i): 0xE0 + i for i in range(10)})
    field_bank, field_addr = sym["SlinkVersionText"]
    assert sym["SlinkVersionFieldEnd"][1] - field_addr == 20
    if line is None:
        # Decode the actual committed field through the native printer. The
        # default Polished charmap can encode 'ou'/'in' as ngrams, not tiles.
        reference = SM83(rom, bank=field_bank)
        reference.trap(addr("PrintLetterDelay"), lambda cpu: None)
        reference.call_routine(addr("_PlaceString"),
                               {"hl": tilemap, "de": field_addr}, sp=addr("wStackTop"))
        encoded = bytes(value for _, address, value in reference.writes
                        if tilemap <= address < tilemap + 360)
        assert encoded[:9] == bytes(glyphs[c] for c in "SoulLink ")
        assert 9 < len(encoded) <= 19
    else:
        encoded = bytes(glyphs[c] for c in line)
        assert len(encoded) <= 19
        m.mem.poke_rom(field_addr, (encoded + b"\x53").ljust(20, b"\0"), bank=field_bank)
    clocks = []
    m.trap(addr("MainMenu_PrintCurrentTimeAndDay"), lambda cpu: clocks.append(cpu.bank), bank=bank)
    # Old PlaceString uses the real glyph loop, but letter timing is not modelled.
    m.trap(addr("PrintLetterDelay"), lambda cpu: None)
    for _ in range(2):
        first_write = len(m.writes)
        result = m.call_routine(addr("SlinkMainMenuLoopBridge"), sp=addr("wStackTop"))
        assert result.returned and result.sp_delta == 0
        assert m.bank == bank and m.peek(addr("hROMBank")) == bytes([bank])
        assert m.peek(addr("hBGMapMode")) == b"\x01"
        writes = [(a - tilemap, value) for _, a, value in m.writes[first_write:]
                  if tilemap <= a < tilemap + 360]
        expected = encoded[:right - left - 1]
        assert writes == [(10 * 20 + left + 1 + i, value) for i, value in enumerate(expected)]
        after = m.peek(tilemap, 360)
        allowed = set(range(10 * 20 + left + 1, 10 * 20 + right))
        assert all(after[i] == before[i] for i in range(360) if i not in allowed)
        assert after[10 * 20 + right] == before[10 * 20 + right], "right border overwritten"
        # Exact occupied pixel cells: x=(left+1)*8 .. right*8-1, y=80..87.
        assert all((left + 1) * 8 <= (i % 20) * 8 and (i % 20) * 8 + 7 < right * 8
                   and (i // 20) * 8 == 80 for i, _ in writes)
    assert clocks == [bank, bank]
