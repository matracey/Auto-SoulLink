"""Consumer-visible corruption controls for the live Polished title oracle."""
import pytest
from PIL import Image

from tools.polished_live.title_check import (
    check_band,
    check_pixels,
    check_version,
    expand_version,
    tile_pixels,
)

ROWS = [bytes(range(0x60, 0x69)), bytes(range(0x69, 0x72))]
ART = bytes([0xAA, 0x0F] * 8 * 18)
COLOURS = [(0, 0, 0), (255, 255, 255), (220, 170, 0), (10, 20, 245)]


def snapshot():
    vram = bytearray(0x4000)
    vram[0x1600:0x1720] = ART
    for y, row in enumerate(ROWS, 10):
        pos = 0x1800 + y * 32 + 6
        vram[pos:pos + 9] = row
        vram[pos + 0x2000:pos + 0x2000 + 9] = bytes([6] * 9)
    return vram


def picture(shifts=bytes(144)):
    image = Image.new("RGB", (160, 144), (120, 120, 120))
    # Independent fixture raster: the two bitplanes make repeating 0,1,2,3 pixels.
    indices = [1, 0, 1, 0, 3, 2, 3, 2]
    for y in range(72, 88):
        for x in range(48, 120):
            screen_x = (x - shifts[y]) & 255
            if screen_x < 160:
                image.putpixel((screen_x, y), COLOURS[indices[(x - 48) % 8]])
    return image


def test_tile_bitplane_order_and_most_significant_pixel_first():
    assert tile_pixels(bytes([0x80, 0x01] + [0, 0] * 7))[0] == [1, 0, 0, 0, 0, 0, 0, 2]
    with pytest.raises(ValueError, match="16 bytes"):
        tile_pixels(bytes(15))


def test_correct_live_band_and_settled_pixels():
    assert check_band(snapshot(), ART, ROWS) == []
    assert check_pixels(picture(), ART, ROWS, bytes(144)) == []


@pytest.mark.parametrize("offset,reason", [
    (0x1946, "band row 10: tile IDs differ"),
    (0x196E, "band row 11: tile IDs differ"),
    (0x3946, "band row 10: attributes"),
    (0x396E, "band row 11: attributes"),
    (0x171F, "VRAM tile data"),
])
def test_corrupt_live_byte_is_rejected(offset, reason):
    vram = snapshot()
    vram[offset] ^= 1
    assert any(reason in error for error in check_band(vram, ART, ROWS))


def test_clean_blank_tilemap_cannot_pass_band_check():
    assert "band row 10: tile IDs differ" in check_band(bytes(0x4000), ART, ROWS)


def test_palette_bank_or_flip_attribute_is_not_accepted():
    for attribute in (5, 14, 38, 70):
        vram = snapshot()
        vram[0x3946] = attribute
        assert any("attributes differ" in e for e in check_band(vram, ART, ROWS))


def test_native_interlaced_entrance_requires_both_rows_to_move():
    shifts = bytes(40 if y % 2 == 0 else 216 for y in range(144))
    assert check_pixels(picture(shifts), ART, ROWS, shifts) == []
    stopped_bottom_row = bytearray(shifts)
    stopped_bottom_row[80:88] = bytes(8)
    assert check_pixels(picture(stopped_bottom_row), ART, ROWS, shifts)


def test_blank_and_single_corrupt_pixel_cannot_pass_visual_check():
    assert check_pixels(Image.new("RGB", (160, 144), "white"), ART, ROWS, bytes(144))
    image = picture()
    image.putpixel((80, 80), (200, 0, 0))
    assert any("shape differs" in e for e in check_pixels(image, ART, ROWS, bytes(144)))


def test_behind_bg_crystal_can_show_through_colour_zero_without_obscuring_letters():
    image = picture()
    for y in range(72, 88):
        for x in range(48, 120):
            if (x - 48) % 8 in (1, 3):
                image.putpixel((x, y), (172, 81, 218))
    assert check_pixels(image, ART, ROWS, bytes(144)) == []


def test_scaled_or_wrong_scroll_screenshot_is_rejected():
    assert check_pixels(picture().resize((320, 288)), ART, ROWS, bytes(144))
    assert check_pixels(picture(), ART, ROWS, bytes(144), scy=0)


def test_main_menu_version_must_match_entire_rom_field():
    vram = snapshot()
    text = b"SoulLink dev"
    pos = 0x1800 + 10 * 32 + 1
    vram[pos:pos + len(text)] = text
    assert check_version(vram, text) == []
    vram[pos + len(text) - 1] ^= 1
    assert check_version(vram, text) == ["main-menu version slice differs from ROM field"]


def test_truncated_memory_and_art_are_not_valid_evidence():
    assert check_band(snapshot()[:-1], ART, ROWS)
    assert check_band(snapshot(), ART[:-1], ROWS)
    assert check_version(bytes(0x2000), b"SoulLink dev")


def test_polished_ngram_version_expands_to_font_tiles():
    field = bytes.fromhex("920aab8b0caa7fe09ce19ce0")
    assert expand_version(field, {0x0A: bytes.fromhex("aeb4"), 0x0C: bytes.fromhex("a8ad")}) == bytes.fromhex(
        "92aeb4ab8ba8adaa7fe09ce19ce0")


def test_version_commands_missing_ngrams_and_expanded_overflow_are_rejected():
    for field, ngrams in ((b"\x53", {}), (b"\x0a", {}), (b"\x0a", {10: b"\x53"}),
                           (bytes([0x80]) * 20, {})):
        with pytest.raises(ValueError):
            expand_version(field, ngrams)
    assert expand_version(bytes([0x80]) * 19, {}) == bytes([0x80]) * 19


def test_version_field_offset_comes_from_the_overlay_sym():
    # A rebuilt overlay moves SlinkVersionText; a hardcoded ROM offset read code bytes instead
    # (freeze #3 title FAIL: "unsupported version text command or ngram").
    from tools.polished_live import title_check

    start = title_check.sym_offset("SlinkVersionText.text")
    end = title_check.sym_offset("SlinkVersionText.end")
    assert 0 < end - start <= 20
    assert start == 0x7E * 0x4000 + 0x4183 - 0x4000
