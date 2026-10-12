"""F5: shipped UPS panel through the real native PlaceString (SM83 MODEL).

Only fallback UI, frame/input timing and letter delay are trapped. The renderer,
RST dispatch and farcall execute native ROM bytes; this is not a timing proof.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.unit import polished_sm83 as S
from tools.build_gen2_companion import _symbols
from tools.build_polished_companion import ups_apply

ROOT = Path(__file__).resolve().parents[2]
RELEASE = Path(os.environ.get("SLINK_WORK_ROOT", "F:/slink-work")) / "cache/polished/release/polishedcrystal-3.2.3.gbc"


@pytest.fixture(scope="module")
def panel_rom():
    if not RELEASE.is_file():
        pytest.skip(f"release ROM missing: {RELEASE}")
    return (ups_apply(RELEASE.read_bytes(), (ROOT / "patch/dist/SLink-Polished.ups").read_bytes()),
            _symbols(ROOT / "data/polished/polished_slink.sym"))


def render(panel_rom, pages, turn=False):
    rom, sym = panel_rom
    def addr(n):
        return sym[n][1]

    bank, entry = sym["SlinkPanel"]
    m = S.SM83(rom, bank, mbc="mbc3", farcall="native", hrombank=addr("hROMBank"))
    m.poke(addr("hROMBank"), bank)
    m.poke(addr("hInMenu"), 0xA4)
    tilemap = addr("wTilemap")
    m.poke(tilemap, bytes([0xEE]) * 360)
    mailbox, text = addr("wSlinkMailbox"), addr("wSlinkPanelText")
    m.poke(mailbox, bytes([0xCC]) * 69)
    m.poke(mailbox + 11, len(pages))
    m.poke(addr("hJoyDown"), 1)  # opening A must not advance the page
    images, payloads, phase = [], [], [0]

    def trap(name, fn):
        b, a = sym[name]
        m.trap(a, fn, bank=b if b else None)

    def frame(mm):
        if mm.peek(mailbox + 9)[0] == 1:
            page = mm.peek(mailbox + 10)[0]
            assert page == len(images), "page advanced without a fresh input edge"
            mm.poke(text, pages[page])
            mm.poke(mailbox + 9, 2)
            phase[0] = 0
        elif mm.peek(mailbox + 9)[0] == 2 and phase[0] == 0:
            images.append(mm.peek(tilemap, 360))
            payloads.append(mm.peek(text, 34))
            phase[0] = 1

    def joy(mm):
        # release first; only the following frame supplies a fresh A/B edge
        pressed = 0 if phase[0] == 1 else (1 if turn and len(images) < len(pages) else 2)
        mm.poke(addr("hJoyDown"), pressed)
        mm.poke(addr("hJoyPressed"), pressed)
        phase[0] += 1

    trap("PrintText", lambda mm: None)
    trap("PrintLetterDelay", lambda mm: None)
    trap("DelayFrame", frame)
    trap("JoyTextDelay", joy)
    result = m.call_routine(entry, bank=bank, sp=0xC0C0)
    assert result.sp_delta == 0
    assert len(images) == len(pages)
    assert m.peek(addr("hInMenu")) == b"\xA4"
    assert m.peek(mailbox + 9, 2) == b"\0\0"
    assert sym["wSlinkMailboxEnd"][1] - mailbox == 69
    allowed = [(0xC000, 0xC0C0), (mailbox, mailbox + 69),
               (addr("hROMBank"), addr("hROMBank") + 1),
               (addr("hInMenu"), addr("hInMenu") + 1)]
    # Native PlaceString may write text-engine scratch in HRAM/WRAM; with all
    # controls removed, only stack, panel protocol and the two tile rows are needed.
    allowed += [(tilemap + 14 * 20 + 1, tilemap + 14 * 20 + 17),
                (tilemap + 15 * 20 + 1, tilemap + 15 * 20 + 17)]
    for pc, a, value in result.writes:
        assert any(lo <= a < hi for lo, hi in allowed), f"WRAM escape {pc:04x}:{a:04x}={value:02x}"
    for raw, image, payload in zip(pages, images, payloads, strict=True):
        expected = bytearray([0xEE] * 360)
        for line in range(2):
            row = bytes(v if v >= 0x5F else 0x7F for v in raw[line * 17:line * 17 + 16])
            expected[(14 + line) * 20 + 1:(14 + line) * 20 + 17] = row
            assert payload[line * 17:line * 17 + 16] == row
            assert payload[line * 17 + 16] == 0x53
        assert image == expected, "native PlaceString escaped its two 16-glyph rows"
    return images


@pytest.mark.parametrize("start", range(0, 0x5F, 16))
def test_panel_replaces_every_native_control_and_ngram_before_place_string(panel_rom, start):
    bad = bytes(range(start, min(start + 16, 0x5F))).ljust(16, b"\x56")
    # CTXT and NEXT are especially dangerous: they decompress or move the cursor.
    render(panel_rom, [bad + b"\x5D" + bytes([0x80, 0x56, 0x5D, 0x53]) * 4 + b"\x56"])


@pytest.mark.parametrize("start", range(0x5F, 0x100, 16))
def test_panel_preserves_native_literal_boundary_and_bounds_missing_terminators(panel_rom, start):
    legal = bytes(range(start, min(start + 16, 0x100))).ljust(16, b"\x7F")
    render(panel_rom, [legal + b"\x80" + legal[::-1] + b"\xFF"])


def test_panel_sanitizes_each_page_without_changing_fresh_edge_handshake(panel_rom):
    render(panel_rom, [bytes([0x56]) * 34, bytes([0x5D]) * 34], turn=True)
