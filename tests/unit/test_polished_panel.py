"""POL-PANEL Stage 2: the SLink panel behind the Phone card (docs/polished/PANEL.md).

Two layers, both against the REAL overlay artifacts:

* native rendering, sanitization, mailbox bounds and input waits execute the shipped UPS
  in test_polished_panel_rom.py;
* the host side runs the same in-process rig as test_polished_client.py over a WRAM image laid out
  from that .sym and drives the shared AWAIT/STAGED handshake exactly as the cartridge does.

The rig detail that matters: gb_panel only stages on an **observed** CLOSED -> AWAIT transition, and
the Gen 2 binder only calls the panel present once it has seen the sampled frame counter MOVE. So a
real open is three frames, not two -- `_open()` does all three. Two frames silently shows the
fallback and never paints, which is the protocol working, not a bug.

Every test names its red control. Four of them are applied controls, not assertions of absence.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tests.unit.test_polished_client import (  # noqa: F401
    HARNESS,
    ROOT,
    SYM,
    _entry,
    _memory,
    _mons,
    _pair,
    _run,
    roms,
)

lupa = pytest.importorskip("lupa")


ROOTP = Path(ROOT)
MAILBOX = SYM["wSlinkMailbox"][1]
MAILBOX_END = SYM["wSlinkMailboxEnd"][1]
PANEL_TEXT = SYM["wSlinkPanelText"][1]
TILEMAP = SYM["wTilemap"][1]
ATTRMAP = SYM["wAttrmap"][1]
OFF_CAPS, OFF_STATE, OFF_PAGE, OFF_PAGES, OFF_COUNTER = 8, 9, 10, 11, 5
CLOSED, AWAIT, STAGED = 0, 1, 2
CAP_PANEL = 0x02
CAPS_PANEL_SOUND = 0x07   # SLINK_CAP_PANEL | SLINK_CAP_SFX | SLINK_CAP_SFX_NOTIFY: what the overlay stores
BLANK = 0x7F
PROFILE = json.loads((ROOTP / "data/games/polished_crystal/profile.json").read_text(encoding="utf-8"))
PANEL = PROFILE["titles"]["polished"]["overlay"]["panel"]
LINES, LINE_MAX, STRIDE, TERM = PANEL["lines"], PANEL["line_max"], PANEL["stride"], PANEL["terminator"]

_CODES: dict[str, int] | None = None


def _codes(text: str) -> list[int]:
    """ASCII -> the game's own charmap codes, from the generated pack (the client's tile_for)."""
    global _CODES
    if _CODES is None:
        raw = (ROOTP / "data/games/polished_crystal/charmap.lua").read_text(encoding="utf-8")
        _CODES = {k: int(v) for k, v in re.findall(r'\["([^"]+)"\] = (\d+)', raw)}
    return [_CODES[ch] for ch in text]


def _rows(lua, values):
    """A plain Lua array in the CLIENT'S runtime (a foreign runtime's table is a different type)."""
    return lua.table_from(values)


def _fresh(caps: int = CAP_PANEL) -> dict:
    """A mailbox the overlay service has just written: beacon, ABI 3, one cap byte, live counter."""
    return {
        MAILBOX: 0x53, MAILBOX + 1: 0x4C, MAILBOX + 2: 0x4E, MAILBOX + 3: 0x4B,
        MAILBOX + 4: 3, MAILBOX + OFF_CAPS: caps, MAILBOX + OFF_STATE: CLOSED,
        # the service's own init cookie (+31, patch/polished/src/slink.asm SLINK_SAMPLE_COOKIE):
        # without it the binder deliberately reads the mailbox as a previous session's
        MAILBOX + 31: 0xA5,
        MAILBOX + OFF_COUNTER: 1,
    }


def _build(roms, extra=None):  # noqa: F811
    lua = lupa.LuaRuntime(unpack_returned_tuples=True)
    memory = _memory(_mons())
    memory.update(extra or {})
    mem = lua.table_from(memory)
    deps, io, log = lua.execute(HARNESS.replace("ROOTDIR", json.dumps(ROOT)))(roms[1], mem)
    parts, why = _pair(_entry(lua).build(deps))
    assert why is None, why
    parts.client.start(parts.client)
    return lua, parts, io, log, mem


def _frame(parts, io, mem, counter, state):
    """One mapped frame: publish `state` and advance the sampled counter."""
    mem[MAILBOX + OFF_COUNTER] = counter
    mem[MAILBOX + OFF_STATE] = state
    _run(io, parts.client, 1)


def _open(lua, parts, io, mem, rows, counter: int = 3):
    """Hold `rows` and take the panel through a REAL open: counter moves (panel goes present),
    CLOSED is then OBSERVED, then AWAIT arrives -- the only transition gb_panel stages on."""
    assert parts.panel.hold(parts.panel, _rows(lua, rows)) is True
    _frame(parts, io, mem, counter, CLOSED)        # counter moves: the binder sees the service live
    _frame(parts, io, mem, counter + 1, CLOSED)    # now present, so CLOSED is observed
    _frame(parts, io, mem, counter + 2, AWAIT)     # CLOSED -> AWAIT: the paint arm


def _staged(log) -> list[int]:
    """Every address the cartridge was asked to take, in order (the harness io write log).

    Indexed, not iterated: lupa's table iteration yields keys/values depending on the runtime's
    tuple mode, and an explicit index is the one form that is the same in every build."""
    return [int(log.writes[i]["addr"]) for i in range(1, len(log.writes) + 1)]


def _written(log) -> dict[int, int]:
    """addr -> value for everything the panel asked the cartridge to take."""
    return {int(log.writes[i]["addr"]): int(log.writes[i]["value"]) for i in range(1, len(log.writes) + 1)}


def _line(mem, index: int) -> list[int]:
    """Line `index` of the staged page, read back out of the WRAM the panel actually wrote."""
    return [int(mem[PANEL_TEXT + index * STRIDE + i]) for i in range(STRIDE)]


def _window(mem, base: int, n: int = 64) -> list[int]:
    return [mem[base + i] for i in range(n)]




def test_the_overlay_advertises_panel_and_sound_and_nothing_else(roms):  # noqa: F811
    """caps is SLINK_CAP_PANEL | SLINK_CAP_SFX | SLINK_CAP_SFX_NOTIFY ($07): a host that speaks the
    phone or trade ABI is told no (the shipped ROM's own caps immediate is pinned in
    test_polished_sounds.py). Red control (applied): OR in CAP_PHONE in the service and the
    `caps_has` assertion below, fed the same byte, flips to true."""
    lua, parts, io, _log, mem = _build(roms, _fresh(caps=CAPS_PANEL_SOUND))
    _frame(parts, io, mem, 2, CLOSED)          # first observation: nothing has moved yet
    _frame(parts, io, mem, 3, CLOSED)          # the counter moved: the service is live
    panel = parts.panel
    assert panel.present(panel) is True
    assert panel.caps_has(panel, 0x01) is True       # SFX bit
    assert panel.caps_has(panel, 0x04) is True       # SFX_NOTIFY bit
    assert panel.caps_has(panel, 0x08) is False      # no PHONE bit: the card is not an ABI surface
    assert panel.caps_has(panel, 0x10) is False      # no TRADE bit
    assert mem[MAILBOX + OFF_CAPS] == CAPS_PANEL_SOUND


# ── the host's half ───────────────────────────────────────────────────────────────────────

def test_a_staged_page_lands_only_in_the_mailbox_and_publishes_staged_last(roms):  # noqa: F811
    """The Stage-2 contract in one open: the page's glyph codes and terminators appear inside the
    mailbox, the page count is published, STAGED goes last, and NOT ONE byte outside the mailbox
    moves -- in particular wTilemap and wAttrmap are never painted, which is exactly what makes
    this a native text box instead of the graphics lease Polished cannot take.

    Red control (applied): drop the `narrow` region in compose_polished and the tilemap assertion
    is the first thing to fail."""
    lua, parts, io, log, mem = _build(roms, _fresh())
    tiles, attrs = _window(mem, TILEMAP), _window(mem, ATTRMAP)
    _open(lua, parts, io, mem, ["SOUL LINK", "PARTNER: RED"])
    staged = _written(log)
    assert mem[MAILBOX + OFF_STATE] == STAGED, "STAGED was not published"
    assert staged[MAILBOX + OFF_PAGES] == 1, "two rows are one page"
    assert staged[MAILBOX + OFF_STATE] == STAGED
    first = _line(mem, 0)
    second = _line(mem, 1)
    assert first[:9] == _codes("SOUL LINK"), first
    assert second[:12] == _codes("PARTNER: RED"), second
    assert first[LINE_MAX] == TERM and second[LINE_MAX] == TERM
    assert first[9:LINE_MAX] == [BLANK] * (LINE_MAX - 9), "the rest of the line is space-padded"
    assert _window(mem, TILEMAP) == tiles and _window(mem, ATTRMAP) == attrs
    assert all(MAILBOX <= a < MAILBOX_END for a in _staged(log)), sorted(_staged(log))


def test_a_page_turn_stages_the_next_page_and_a_persisting_await_does_not(roms):  # noqa: F811
    """A advances: the ROM writes PAGE, drops to AWAIT, and THAT transition stages again. An AWAIT
    that merely persists stages nothing, so a late client cannot repaint a page the player is
    already reading.

    Red control (applied): treat a persistent AWAIT as a page turn in gb_panel's tick and the
    "still page 1" assertion below fails."""
    lua, parts, io, log, mem = _build(roms, _fresh())
    _open(lua, parts, io, mem, ["ONE", "TWO", "THREE", "FOUR", "FIVE"])
    assert _written(log)[MAILBOX + OFF_PAGES] == 3, "5 rows at 2 a page is 3 pages"
    assert mem[MAILBOX + OFF_STATE] == STAGED
    assert _line(mem, 0)[:3] == _codes("ONE")

    _frame(parts, io, mem, 9, AWAIT)                      # persists: no transition, no repaint
    assert _line(mem, 0)[:3] == _codes("ONE")

    mem[MAILBOX + OFF_PAGE] = 1                          # the ROM advanced the page and asked again
    _frame(parts, io, mem, 10, STAGED)
    _frame(parts, io, mem, 11, AWAIT)                    # STAGED -> AWAIT: a real page turn
    assert mem[MAILBOX + OFF_STATE] == STAGED
    assert _line(mem, 0)[:5] == _codes("THREE")
    assert _line(mem, 1)[:4] == _codes("FOUR")
    assert _line(mem, 0)[LINE_MAX] == TERM


def test_a_write_outside_the_mailbox_is_refused_by_the_permit(roms):  # noqa: F811
    """Hardening H-1, restated: the panel writes inside the overlay's own 69-byte span and nothing
    else, however the caller arms it. The tilemap is the obvious thing to want and is refused; so
    is a span that starts inside the mailbox and runs out of it.

    Red control (applied): drop the `narrow` argument to Panel.writes and the tilemap case writes."""
    lua, parts, io, log, mem = _build(roms, _fresh())
    before = _window(mem, TILEMAP)
    w = parts.panel_writes
    w.arm(w, "panel", lambda addr, n: True)             # a lying allow(): the permit's bound still bites
    for addr, n in ((TILEMAP, 4), (MAILBOX_END - 4, 8), (PANEL_TEXT + LINES * STRIDE, 1)):
        with pytest.raises(Exception, match="refused"):
            w.write_bytes(w, addr, lua.table_from([1] * n))
    assert _window(mem, TILEMAP) == before
    w.disarm()
    w.arm(w, "panel", lambda addr, n: addr >= PANEL_TEXT and addr + n <= MAILBOX_END)
    w.write_bytes(w, PANEL_TEXT, lua.table_from([BLANK]))
    w.disarm()
    assert _written(log)[PANEL_TEXT] == BLANK


def test_a_caps_less_mailbox_is_left_alone(roms):  # noqa: F811
    """present() asks the capability bits, not the ABI number: a mailbox byte that happens to read
    AWAIT on a cartridge with no panel is ordinary WRAM and stages nothing.

    Red control (applied): drop the caps test from present() and the no-caps cartridge paints."""
    lua, parts, io, log, mem = _build(roms, _fresh(caps=0))
    _open(lua, parts, io, mem, ["SOUL LINK", "PARTNER: RED"])
    assert parts.panel.present(parts.panel) is False
    assert mem[MAILBOX + OFF_STATE] == AWAIT, "a cartridge with no panel cap must be left alone"
    assert _staged(log) == [], "and nothing of the page may be staged"


def test_rows_are_cut_to_the_line_padded_to_it_and_bad_payloads_refused(roms):  # noqa: F811
    """A long row is cut to the line width and a short one is space-padded, so a page always covers
    the fallback the ROM printed first; a payload that is not a list of strings is refused before
    anything is held.

    Red control (applied): drop the `c` clamp / BLANK padding in hold() and the padding assertion
    fails -- a short line would leave the fallback's glyphs showing under the page."""
    lua, parts, io, log, mem = _build(roms, _fresh())
    panel = parts.panel
    assert panel.hold(panel, _rows(lua, [1, 2])) is not None, "non-string rows must be refused"
    _open(lua, parts, io, mem, ["A" * 40, "B"])
    first, second = _line(mem, 0), _line(mem, 1)
    assert first[:LINE_MAX] == _codes("A" * LINE_MAX), first
    assert first[LINE_MAX] == TERM
    assert second[:2] == _codes("B ")
    assert second[2:LINE_MAX] == [BLANK] * (LINE_MAX - 2), "a short line is padded, not left short"
    assert second[LINE_MAX] == TERM

