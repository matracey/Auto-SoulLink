"""P4.1d: lua/gb_panel.lua, the GB panel handshake + tile renderer + SFX queue, under lupa.

Behaviour of the Gen 1 binding is pinned by test_gen1_panel.py / test_gen1_panel_tiles.py /
test_gen1_writes.py, which drive lua/gen1/panel.lua (now a binder onto this module). This file
pins the CONTRACT: every binder input is explicit and asserted, the Lua ABI constants equal
patch/gb/slink_abi.inc, and a Gen 2-shaped (CGB attrmap) stub binds and paints.
"""
from __future__ import annotations

import pathlib
import re

import lupa
import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
GB_PANEL = (REPO / "lua" / "gb_panel.lua").as_posix()
GEN1_PANEL = (REPO / "lua" / "gen1" / "panel.lua").as_posix()
ABI_INC = REPO / "patch" / "gb" / "slink_abi.inc"

REQUIRED = ("mailbox", "tilemap", "attrmap", "charmap", "se_map", "deadline")

FAKE_WRITES = """
return function(rec_arm, rec_write)
  return {
    arm = function(self, reason, allow) rec_arm(reason, allow) end,
    disarm = function(self) end,
    write_bytes = function(self, addr, bytes) rec_write(addr, bytes) end,
  }
end
"""


class Bus:
    def __init__(self, mailbox, caps=0x03):
        self.mem = bytearray(0x10000)
        self.frame = 0
        self.writes: list[tuple[int, list[int]]] = []
        self.allow = None
        self.mem[mailbox:mailbox + 4] = b"SLNK"
        self.mem[mailbox + 4] = 3
        self.mem[mailbox + 8] = caps
        self.lua = lupa.LuaRuntime(unpack_returned_tuples=True)
        self.G = self.lua.eval(f'dofile("{GB_PANEL}")')
        self.io = self.lua.table(read_u8=lambda a, d=None: self.mem[int(a)], framecount=lambda: self.frame)
        self.w = self.lua.execute(FAKE_WRITES)(self._arm, self._write)

    def _arm(self, reason, allow):
        self.allow = allow

    def _write(self, addr, t):
        data = [int(t[i]) for i in range(1, len(t) + 1)]
        self.writes.append((int(addr), data))
        self.mem[int(addr):int(addr) + len(data)] = bytes(data)

    def spec(self, **kw):
        base = {
            "mailbox": 0xCFD8, "tilemap": 0xC4A0,
            "attrmap": self.lua.table(base=0xCDD9, fill=0x07),
            "charmap": self.lua.eval("function(ch) return ch == ' ' and 0x7F or 0x80 end"),
            "se_map": self.lua.table(), "deadline": 60,
        }
        base.update(kw)
        t = self.lua.table()
        for k, v in base.items():
            if v is not None:
                t[k] = v
        return t

    def bind(self, spec):
        return self.G.new(spec, self.io, self.w, lambda s: str(s))


@pytest.mark.parametrize("missing", REQUIRED)
def test_binder_missing_any_required_input_asserts(missing):
    b = Bus(0xCFD8)
    with pytest.raises(lupa.LuaError, match=missing):
        b.bind(b.spec(**{missing: None}))


def test_attrmap_false_is_the_explicit_dmg_answer():
    b = Bus(0xCFD8)
    p = b.bind(b.spec(attrmap=False))
    assert p.allow(0xCDD9) is False


def test_gen2_stub_binder_paints_tiles_then_attrs_then_publishes_state_last():
    mb, tm, am = 0xCFD8, 0xC4A0, 0xCDD9
    b = Bus(mb)
    p = b.bind(b.spec())
    assert p.present(p) and p.sfx_present(p)
    assert p.hold(p, b.lua.table("AB", "C"))
    p.service(p)                               # CLOSED observed
    b.frame += 1
    b.mem[mb + 9] = 1                          # the patch's CLOSED->AWAIT
    p.service(p)
    assert [a for a, _ in b.writes] == [tm, am, mb + 11, mb + 9]
    tiles, attrs = b.writes[0][1], b.writes[1][1]
    assert tiles[:3] == [0x80, 0x80, 0x7F] and tiles[20] == 0x80 and len(tiles) == 360
    assert attrs == [0x07] * 360
    assert b.mem[mb + 9] == 2
    allow = b.allow
    assert allow(am, 360) and allow(tm, 360) and allow(mb + 7) and allow(mb + 9) and allow(mb + 11)
    assert not allow(am + 360) and not allow(tm - 1) and not allow(mb + 8)


def test_se_map_is_the_binders():
    b = Bus(0xCFD8)
    p = b.bind(b.spec(se_map=b.lua.eval("{[7] = 2}")))
    assert p.sfx_code_for(p, 7) == 2
    assert p.sfx_code_for(p, 25) is None


# -- Lua constants == patch/gb/slink_abi.inc --------------------------------------------

def _abi():
    if not ABI_INC.exists():
        pytest.skip("patch/gb/slink_abi.inc not landed yet (P4.1c, Codex)")
    out = {}
    for name, val in re.findall(r"^DEF\s+(\w+)\s+EQU\s+([^;\n]+)", ABI_INC.read_text(), re.M):
        v = val.strip()
        m = re.fullmatch(r"1\s*<<\s*(\d+)", v)
        if m:
            out[name] = 1 << int(m.group(1))
        elif v.startswith("$"):
            out[name] = int(v[1:], 16)
        elif v.isdigit():
            out[name] = int(v)
    return out


PAIRS = [
    ("OFF_ABI", "SLINK_OFS_VERSION"), ("OFF_SFX", "SLINK_OFS_SFX_REQUEST"),
    ("OFF_CAPS", "SLINK_OFS_CAPS"), ("OFF_STATE", "SLINK_OFS_PANEL_STATE"),
    ("OFF_PAGE", "SLINK_OFS_PANEL_PAGE"), ("OFF_PAGES", "SLINK_OFS_PANEL_PAGES"),
    ("CAP_SFX", "SLINK_CAP_SFX"), ("CAP_PANEL", "SLINK_CAP_PANEL"),
    ("CAP_SFX_NOTIFY", "SLINK_CAP_SFX_NOTIFY"),
    ("CLOSED", "SLINK_PANEL_CLOSED"), ("AWAIT", "SLINK_PANEL_AWAIT"), ("STAGED", "SLINK_PANEL_STAGED"),
    ("SFX_SUCCESS", "SLINK_SFX_SUCCESS"), ("SFX_FAILURE", "SLINK_SFX_FAILURE"),
    ("SFX_BOO", "SLINK_SFX_BOO"), ("SFX_NOTIFY", "SLINK_SFX_NOTIFY"),
]


def _mismatches(M, abi, mailbox=None):
    """Names whose Lua value != the .inc. `mailbox` given: M's OFF_* are absolute addresses."""
    bad = [f"BEACON_{i}" for i in range(4) if M.BEACON[i + 1] != abi[f"SLINK_BEACON_{i}"]]
    for lua_name, abi_name in PAIRS:
        v = M[lua_name]
        if mailbox is not None and lua_name.startswith("OFF_"):
            v = M[lua_name[4:]] - mailbox if M[lua_name[4:]] is not None else None
        if v != abi[abi_name]:
            bad.append(f"{lua_name} != {abi_name}")
    return bad


def test_gb_panel_constants_equal_slink_abi_inc():
    abi = _abi()
    G = lupa.LuaRuntime().eval(f'dofile("{GB_PANEL}")')
    assert _mismatches(G, abi) == []


def test_gen1_panel_constants_equal_slink_abi_inc():
    """The Gen 1 binder re-exports ABSOLUTE addresses (P.CAPS, P.STATE, ...) off its mailbox."""
    abi = _abi()
    P = lupa.LuaRuntime().eval(f'dofile("{GEN1_PANEL}")')
    assert _mismatches(P, abi, mailbox=P.MAILBOX) == []


def test_abi_equality_probe_catches_a_drift():
    """Known-positive control: one shifted .inc value must be reported."""
    abi = dict(_abi(), SLINK_OFS_CAPS=9)
    G = lupa.LuaRuntime().eval(f'dofile("{GB_PANEL}")')
    assert _mismatches(G, abi) == ["OFF_CAPS != SLINK_OFS_CAPS"]


def test_text_rows_are_terminated_before_the_write_window_opens():
    b = Bus(0xCFD8)
    b.lua.globals().panel_is_armed = lambda: b.allow is not None
    text = b.lua.eval("""setmetatable({base=0xC600,stride=17,lines=2,line_max=16}, {
        __index=function(_,k)
            if k == 'terminator' then
                assert(not panel_is_armed(), 'text payload constructed inside armed window')
                return 0x53
            end
        end})""")
    p = b.bind(b.spec(text=text, rows_per_page=2))
    assert p.hold(p, b.lua.table("AB", "C"))
    b.w.arm(b.w, "panel", p.allow)
    assert p.stage(p) == 0
    assert b.writes == [
        (0xC600, [0x80, 0x80] + [0x7F] * 14 + [0x53]),
        (0xC611, [0x80] + [0x7F] * 15 + [0x53]),
        (0xCFE3, [1]),
        (0xCFE1, [2]),
    ]
