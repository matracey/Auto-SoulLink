"""POL-SOUNDS: the Polished Crystal overlay's native notification sounds.

Three layers, each proved against the artifact it claims to describe:

1. THE OVERLAY ROM (patch/dist/SLink-Polished.ups on the pinned release). The sound service is
   really in the shipped image: the semantic-code table, the SFX caps bit, the reset latch, the
   same-size SoftReset rewrite, and the fact that every changed byte lies in an intended span.
2. THE PROFILE (data/games/polished_crystal/profile.json). The semantic-code -> native-id table
   and the PlaySFX/CheckSFX/wMusicFade addresses are read out of the pinned Polished source and
   the overlay .sym, never written by hand.
3. THE HOST WRITER (lua/gen2/polished_sounds.lua). Its permit is built HERE, over the same
   lua/write_permit.lua the production writers use, against a synthetic System Bus image: one
   byte may be written (the request), every other mailbox byte and the tilemap are refused.

compose_polished still hands its panel a REFUSE-ALL writer (hardening H1) and this module does
not change that; the mailbox-only permit below is what the coordinator wires, tested here.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

lupa = pytest.importorskip("lupa")

REPO = Path(__file__).resolve().parents[2]
ROOT = str(REPO).replace("\\", "/")
SOUNDS_ASM = REPO / "patch" / "polished" / "src" / "slink_sfx.asm"
ABI = REPO / "patch" / "gb" / "slink_abi.inc"
SYMS = REPO / "data" / "polished" / "polished_slink.sym"
# the pinned Polished git checkout (read-only); override for another clone
SOURCES = Path(os.environ.get("SLINK_POLISHED_SRC", "F:/slink-work/cache/polished/src"))
PROFILE = json.loads((REPO / "data/games/polished_crystal/profile.json").read_text(encoding="utf-8"))
P = PROFILE["titles"]["polished"]
OVERLAY = P["overlay"]

MAILBOX = OVERLAY["ram"]["wSlinkMailbox"]
TILEMAP = OVERLAY["ram"]["wTilemap"]
ATTRMAP = OVERLAY["ram"]["wAttrmap"]
OFF_CAPS, OFF_SFX, OFF_STATE, OFF_PAGE, OFF_PAGES, OFF_COUNTER = 8, 7, 9, 10, 11, 5
CAP_SFX, CAP_SFX_NOTIFY = 0x01, 0x04
SUCCESS, FAILURE, BOO, NOTIFY = 1, 2, 3, 4
CAPS_SFX = CAP_SFX | CAP_SFX_NOTIFY
# What the shipped overlay stores: sound AND the Phone-card panel (integrated build); no phone, no trade bit.
CAP_PANEL = 0x02
CAPS_ROM = CAP_PANEL | CAPS_SFX


def _sym():
    rows = re.findall(r"^([0-9a-f]{2}):([0-9a-f]{4}) (\S+)$", SYMS.read_text(encoding="utf-8"), re.M)
    return {name: (int(bank, 16), int(addr, 16)) for bank, addr, name in rows}


SYM = _sym()


@pytest.fixture(scope="module")
def overlay():
    """The shipped overlay ROM: the pinned release with patch/dist/SLink-Polished.ups applied."""
    clean = (Path(os.environ.get("SLINK_WORK_ROOT", "F:/slink-work"))
             / "cache/polished/release/polishedcrystal-3.2.3.gbc")
    if not clean.is_file():
        pytest.skip("pinned Polished release ROM absent")
    from patch.tools.make_ups import ups_apply
    data = ups_apply(clean.read_bytes(), (REPO / "patch/dist/SLink-Polished.ups").read_bytes())
    assert len(data) == P["derived"]["rom_size"]
    return data


def _ltable(lua, value):
    """lupa's table_from does not recurse: a nested dict stays a Python dict, not a Lua table."""
    if isinstance(value, dict):
        return lua.table_from({k: _ltable(lua, v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return lua.table_from([_ltable(lua, v) for v in value])
    return value


def _pair(value):
    """S.new returns one value on success and (nil, why) on refusal; lupa only builds a tuple for
    the second shape, so normalize here rather than in the module under test."""
    return (value, None) if not isinstance(value, tuple) else value


# -- 1. the overlay image --------------------------------------------------------------------

def test_the_overlay_advertises_sound_and_panel_and_no_other_capability(overlay):
    """The build's caps byte IS SLINK_CAP_PANEL | SLINK_CAP_SFX | SLINK_CAP_SFX_NOTIFY: bits 0, 1 and
    2 set, bits 3/4 (phone/trade) clear. A host that reads bit 3 or 4 as licensed would speak a
    protocol this overlay does not serve.

    RED CONTROL (applied): restore `xor a` in patch/polished/src/slink.asm and the caps word
    reads $0000 -> fails.
    """
    svc = SYM["SlinkService"][1]
    bank = SYM["SlinkService"][0]
    # the service writes the beacon (4 x `ld a,imm` / `ld [hli],a`), the ABI byte, then caps:
    # `3e XX 32` + a 16-bit address is the caps store. Find it by its address, not by position.
    end = SYM["SlinkServiceEnd"][1]
    window = overlay[bank * 0x4000 + svc - 0x4000: bank * 0x4000 + end - 0x4000]
    addr = MAILBOX + OFF_CAPS
    needle = bytes([0xEA, addr & 0xFF, addr >> 8])       # ld [nn],a
    assert window.count(needle) == 1, "the caps store must be unique in the service"
    at = window.index(needle)
    assert window[at - 2:at] == bytes([0x3E, CAPS_ROM]), "caps must be the PANEL + SFX + SFX_NOTIFY immediate"
    assert window[at - 3] == 0x77, "the ABI byte is stored with `ld [hl],a` just before the caps pair"


def test_the_sound_service_is_reached_only_from_the_delay_frame_bridge(overlay):
    """SlinkService ends in `jp SlinkSfxService` ($C3 to bank $7E), and the service's own entry is
    the first byte after it: there is no second caller. The 16-byte prologue of bank $7E is the UPR
    patch-0020 signature (lua/gen2/polished.lua P.RAND_ANCHORS) and must not have moved.

    RED CONTROL (applied): `call SlinkSfxService` + `ret` -> the $C3 is gone -> fails.
    """
    def flat(label):
        bank, addr = SYM[label]
        return addr if bank == 0 else bank * 0x4000 + addr - 0x4000

    end = flat("SlinkServiceEnd")
    assert overlay[0x1F8000:0x1F8010] == bytes.fromhex("210bc63e53223e4c223e4e223e4b223e"), "patch-0020 prologue"
    assert overlay[0x0070:0x0077] == bytes.fromhex("f044e0d7afe08f"), "DelayFrame lead-in in the bridge"
    assert overlay[0x0DA8:0x0DAF] == bytes.fromhex("cd700000000000"), "DelayFrame `call $0070` + 4 nop"
    end = flat("SlinkServiceEnd")
    target = SYM["SlinkSfxService"][1]
    # the `jp` is the last three bytes the core service emits, so it ENDS at the label
    assert overlay[end - 3:end] == bytes([0xC3, target & 0xFF, target >> 8]), "service must jp the sound service"
    assert SYM["SlinkSfxService"][0] == 0x7E and flat("SlinkSfxServiceEnd") <= 0x1FC000


def test_the_soft_reset_hook_is_the_same_size_and_latches_the_hold(overlay):
    """SoftReset's `call DelayFrames` became `call SlinkResetSoundBridge`: same 3 bytes, C (the
    frame count) preserved, and the bridge stores the $FF hold, clears the request, and tail-jumps
    to DelayFrames. A request left in the mailbox from before a reset must not be played against a
    half-initialized audio engine.

    RED CONTROL (applied): drop the `call DelayFrames` rewrite from POLISHED_EDITS -> the bytes at
    SoftReset+0x1A stay `cd a1 0d` -> fails.
    """
    soft = SYM["SoftReset"][1]
    bridge = SYM["SlinkResetSoundBridge"][1]
    assert overlay[soft + 0x19:soft + 0x1C] == bytes([0xCD, bridge & 0xFF, bridge >> 8]), "hook is a call"
    assert overlay[soft + 0x17:soft + 0x19] == bytes([0x0E, 0x03]), "ld c, 3 survives (same-size edit)"
    body = overlay[bridge:SYM["SlinkResetSoundBridgeEnd"][1]]
    assert body.endswith(bytes([0xC3, SYM["DelayFrames"][1] & 0xFF, SYM["DelayFrames"][1] >> 8])), "jp DelayFrames"
    assert bytes([0x3E, 0xFF, 0xEA, MAILBOX + 12 & 0xFF, (MAILBOX + 12) >> 8]) in body, "hold = $FF"
    assert bytes([0xEA, MAILBOX + OFF_SFX & 0xFF, (MAILBOX + OFF_SFX) >> 8]) in body, "request cleared"


def test_every_overlay_byte_the_sound_card_changed_is_in_an_intended_span(overlay):
    """tools/build_polished_companion.py verify_overlay is the authority on WHERE the overlay may
    write. It is re-run here against the clean ROM and the committed .sym, so a span cannot be
    widened without this test noticing, and so the sound card's bytes are named in its report.

    RED CONTROL (applied): move SlinkResetSoundBridge to ROM0[$0100] (past "Header") -> the
    free-gap adjacency proof raises.
    """
    import sys
    sys.path.insert(0, str(REPO / "tools"))
    import build_polished_companion as B
    from build_gen2_companion import _symbols
    clean = Path(os.environ.get("SLINK_WORK_ROOT", "F:/slink-work")) / "cache/polished/companion-clean"
    if not (clean / "polishedcrystal-3.2.3.gbc").is_file():
        pytest.skip("clean pinned build not cached")
    base = (clean / "polishedcrystal-3.2.3.gbc").read_bytes()
    spans = B.verify_overlay(base, overlay, _symbols(clean / "polishedcrystal-3.2.3.sym"), dict(SYM))
    joined = "\n".join(spans)
    assert "ROM0 delay + reset bridges" in joined
    assert "service + panel + sound + version" in joined
    assert "call DelayFrames -> SlinkResetSoundBridge" in joined, spans
    with pytest.raises(RuntimeError):
        # a byte outside every intended span must be refused, not absorbed
        tampered = bytearray(base)
        tampered[0x3000] = 0x00
        B.verify_overlay(bytes(tampered), overlay, _symbols(clean / "polishedcrystal-3.2.3.sym"), dict(SYM))


# -- 2. the generated profile ------------------------------------------------------------------

def test_the_profile_carries_the_native_id_table_and_the_service_facts():
    """tools/gen_polished_profile.py reads the ids out of the pinned constants/sfx_constants.asm
    and the entry points out of the overlay .sym, so this is a re-derivation, not a copy.

    RED CONTROL (applied): hardcode a code table into the generator -> the id checks fail.
    """
    block = OVERLAY["sfx"]
    assert block["codes"] == {"success": 1, "failure": 0x19, "boo": 0x24, "notify": 0x08}
    assert block["caps"] == ["SLINK_CAP_SFX", "SLINK_CAP_SFX_NOTIFY"]
    assert block["play_sfx"] == list(SYM["PlaySFX"]) == [0, 0x39BD]
    assert block["check_sfx"] == list(SYM["CheckSFX"]) == [0, 0x3AC6]
    assert block["music_fade"] == SYM["wMusicFade"][1] == 0xCCB2
    assert block["service"] == list(SYM["SlinkSfxService"])
    assert P["constants"]["SFX_ITEM"] == 1 and P["constants"]["SFX_WRONG"] == 0x19
    assert P["constants"]["SFX_BUMP"] == 0x24 and P["constants"]["SFX_READ_TEXT_2"] == 0x08


@pytest.mark.skipif(not SOURCES.is_dir(), reason="pinned Polished source absent")
def test_the_native_ids_are_the_pinned_constants_not_guesses():
    """The four ids are re-read from the pinned source at their own lines. A renumbered constant
    is a generator error, never a wrong cue.

    RED CONTROL (applied): change the expect to SFX_DEX_FANFARE_50_79 -> fails on the source text.
    """
    text = (SOURCES / "constants" / "sfx_constants.asm").read_text(encoding="utf-8").splitlines()
    at = {}
    for index, line in enumerate(text, start=1):
        for name in ("SFX_ITEM", "SFX_WRONG", "SFX_BUMP", "SFX_READ_TEXT_2"):
            if re.match(rf"\s*const {name}\s*;\s*([0-9a-f]+)", line):
                at[name] = (index, int(re.search(r";\s*([0-9a-f]+)", line).group(1), 16))
    assert at == {"SFX_ITEM": (4, 1), "SFX_WRONG": (28, 0x19), "SFX_BUMP": (39, 0x24),
                  "SFX_READ_TEXT_2": (11, 0x08)}, at
    assert P["constants"]["SFX_ITEM"] == at["SFX_ITEM"][1]
    assert P["constants"]["SFX_READ_TEXT_2"] == at["SFX_READ_TEXT_2"][1]


def test_the_overlay_resolves_the_codes_in_the_abi_order():
    """`.sounds` is indexed by (code - 1), so its order must be the ABI's
    SUCCESS/FAILURE/BOO/NOTIFY. The semantic codes themselves are the shared ABI's, unchanged.

    RED CONTROL (applied): swap SFX_BUMP and SFX_WRONG in the table -> fails.
    """
    abi = dict(re.findall(r"DEF (SLINK_SFX_[A-Z]+) +EQU +(\d+)", ABI.read_text(encoding="utf-8")))
    assert [int(abi[f"SLINK_SFX_{n}"]) for n in ("SUCCESS", "FAILURE", "BOO", "NOTIFY")] == [1, 2, 3, 4]
    table = re.search(r"^\.sounds\s*\n\s*db\s+(.+)$", SOUNDS_ASM.read_text(encoding="utf-8"), re.M).group(1)
    assert [s.strip() for s in table.split(",")] == ["SFX_ITEM", "SFX_WRONG", "SFX_BUMP", "SFX_READ_TEXT_2"]
    # the profile's own table names the same four codes, whatever order JSON sorted them in
    assert set(OVERLAY["sfx"]["codes"]) == {"success", "failure", "boo", "notify"}
    for name in ("SFX_ITEM", "SFX_WRONG", "SFX_BUMP", "SFX_READ_TEXT_2"):
        assert name in SOUNDS_ASM.read_text(encoding="utf-8")


# -- 3. the host writer: mailbox-only permit -----------------------------------------------------

def _rig(lua=None):
    """A Lua runtime, a synthetic System Bus table and a write log. Nothing here is a mock of the
    module under test: the permit, gb_panel and the Gen 2 panel binder are the shipped Lua."""
    lua = lua or lupa.LuaRuntime(unpack_returned_tuples=True)
    # a zeroed mailbox: an unwritten byte reads 0, not nil
    mem = lua.table_from({MAILBOX + i: 0 for i in range(40)})
    log = Log()
    io = lua.table_from({"frame": 0})
    io.read_u8 = lambda a, d=None: mem[int(a)] or 0
    io.write_u8 = lambda a, v, d=None: (log.writes.append((int(a), int(v))),
                                       mem.__setitem__(int(a), int(v)))[1]
    io.framecount = lambda: io.frame
    return lua, mem, log, io


class Log:
    """The io write log. A plain Python list: lupa stores a tuple as several return values, which
    would silently flatten the (address, value) pair."""

    def __init__(self):
        self.writes = []

    def clear(self):
        self.writes.clear()


def _sound_writer(lua, mem, log, io, profile=P):
    """Build lua/gen2/polished_sounds.lua's permit HERE (the coordinator's wiring is another card)."""
    Sounds = lua.eval(f'dofile("{ROOT}/lua/gen2/polished_sounds.lua")')
    Permit = lua.eval(f'dofile("{ROOT}/lua/write_permit.lua")')
    writes = Sounds.writes(io, Permit, MAILBOX)
    charmap = lua.table_from({"encoding": lua.table_from({})})
    # the Gen 2 panel binder reads its addresses from profile.overlay.ram (wSlinkMailbox/wTilemap/
    # wAttrmap); the sound facts come from profile.overlay.sfx, which S.sfx_codes requires.
    panel, why = _pair(Sounds.new(_ltable(lua, {"overlay": profile["overlay"]}), charmap, io, writes,
                                  lambda s: s))
    assert panel is not None, why
    return Sounds, writes, panel


def test_the_permit_writes_the_request_byte_and_refuses_every_other_address():
    """The one authorized write is (MAILBOX+7, one byte). The tilemap, the attrmap and every other
    mailbox byte -- including the panel state/page bytes gb_panel's own allow() accepts -- are
    refused by the domain bounds.

    RED CONTROL (applied): widen bounds to `n == 1 and addr >= MAILBOX` -> the panel state byte
    below starts being written -> fails.
    """
    lua, mem, log, io = _rig()
    Sounds, writes, _ = _sound_writer(lua, mem, log, io)
    writes.arm(writes, "sfx")
    writes.write_bytes(writes, MAILBOX + OFF_SFX, lua.table_from([FAILURE]))
    assert log.writes == [(MAILBOX + OFF_SFX, FAILURE)]
    assert int(mem[MAILBOX + OFF_SFX]) == FAILURE
    for addr in (TILEMAP, ATTRMAP, MAILBOX + OFF_STATE, MAILBOX + OFF_PAGES, MAILBOX + 0, MAILBOX + 5):
        log.clear()
        writes.arm(writes, "sfx")
        with pytest.raises(lupa.LuaError, match="write refused: interval outside domain bounds"):
            writes.write_bytes(writes, addr, lua.table_from([1]))
        assert len(log.writes) == 0, addr
    # and a two-byte span at the request is refused too: one byte, one address
    writes.arm(writes, "sfx")
    with pytest.raises(lupa.LuaError, match="write refused: interval outside domain bounds"):
        writes.write_bytes(writes, MAILBOX + OFF_SFX, lua.table_from([1, 1]))
    assert log.writes == []


def test_the_panel_arms_the_sound_permit_under_its_own_reason():
    """gb_panel arms "panel"; the sound permit re-arms itself as "sfx" and its bounds are the
    authority, so the receipts read sfx and a panel paint can never widen the window.

    RED CONTROL (applied): pass gb_panel's allow() through to permit:arm -> the panel state byte
    becomes writable under "panel" -> the state-byte refusal above fails.
    """
    lua, mem, log, io = _rig()
    Sounds, writes, _ = _sound_writer(lua, mem, log, io)
    assert writes.address == MAILBOX + OFF_SFX
    writes.arm(writes, "panel")            # what gb_panel does
    writes.write_bytes(writes, MAILBOX + OFF_SFX, lua.table_from([NOTIFY]))
    assert log.writes == [(MAILBOX + OFF_SFX, NOTIFY)], log.writes
    assert writes.log[1]["why"] == "sfx", writes.log[1]


def test_a_request_posts_through_the_panel_only_when_the_rom_is_live_and_asked():
    """request_sfx -> service() is the whole host path: one cue per frame, the failure outranks the
    rest, a cartridge with no SFX bit posts nothing, and an unplayed request is never overwritten.

    RED CONTROL (applied): set caps to CAP_PANEL only -> sfx_present() is false and nothing posts.
    """
    lua, mem, log, io = _rig()
    Sounds, writes, panel = _sound_writer(lua, mem, log, io)
    assert panel.sfx_present is not None and panel.request_sfx is not None
    mem[MAILBOX] = 0x53
    mem[MAILBOX + 1] = 0x4C
    mem[MAILBOX + 2] = 0x4E
    mem[MAILBOX + 3] = 0x4B
    mem[MAILBOX + 4] = 3
    mem[MAILBOX + OFF_CAPS] = CAPS_SFX
    mem[MAILBOX + 31] = 0xA5                      # the service's init cookie

    # two observations: the panel only reads FRESH once the sampled counter has moved
    io.frame = 1
    panel.service(panel)
    mem[MAILBOX + OFF_COUNTER] = 1
    io.frame = 2
    panel.service(panel)
    assert panel.sfx_present(panel) is True

    assert panel.request_sfx(panel, FAILURE) is True
    assert panel.request_sfx(panel, SUCCESS) is True
    assert int(mem[MAILBOX + OFF_SFX]) == 0, "nothing posts before service()"
    panel.service(panel)
    assert int(mem[MAILBOX + OFF_SFX]) == FAILURE, "one cue per frame, failure outranks success"

    # a held request is never overwritten: the ROM still owns byte +7
    panel.service(panel)
    assert int(mem[MAILBOX + OFF_SFX]) == FAILURE
    # the losing cue is DROPPED, not deferred (lua/sfx_arbiter.lua is per-frame by design), and once
    # the ROM has consumed the byte the next request posts normally
    mem[MAILBOX + OFF_SFX] = 0
    panel.service(panel)
    assert int(mem[MAILBOX + OFF_SFX]) == 0
    assert panel.request_sfx(panel, SUCCESS) is True
    panel.service(panel)
    assert int(mem[MAILBOX + OFF_SFX]) == SUCCESS

    # no SFX bit: refused, and a fresh byte stays empty
    mem[MAILBOX + OFF_SFX] = 0
    mem[MAILBOX + OFF_CAPS] = 0x02
    io.frame = 3
    panel.service(panel)
    assert panel.sfx_present(panel) is False
    assert panel.request_sfx(panel, BOO) is False
    panel.service(panel)
    assert int(mem[MAILBOX + OFF_SFX]) == 0


def test_a_profile_without_the_sound_table_refuses_before_any_writer_exists():
    """sfx_codes() is the fail-closed gate: an overlay that advertises no sound service has no
    `overlay.sfx` block, and S.new returns the reason instead of a panel.

    RED CONTROL (applied): drop the `if not codes` check -> a table-less profile builds a panel.
    """
    lua = lupa.LuaRuntime(unpack_returned_tuples=True)
    Sounds = lua.eval(f'dofile("{ROOT}/lua/gen2/polished_sounds.lua")')
    bare = json.loads(json.dumps(OVERLAY))
    bare.pop("sfx", None)
    panel, why = _pair(Sounds.sfx_codes(_ltable(lua, {"overlay": bare})))
    assert panel is None and "sfx.codes required" in why
    ids, ids_why = _pair(Sounds.sfx_codes(_ltable(lua, {"overlay": OVERLAY})))
    assert ids_why is None
    assert ids["success"] == 1 and ids["failure"] == 0x19 and ids["boo"] == 0x24 and ids["notify"] == 0x08
    # an out-of-range native id is refused rather than posted
    bad = json.loads(json.dumps(OVERLAY))
    bad["sfx"]["codes"]["boo"] = 999
    assert _pair(Sounds.sfx_codes(_ltable(lua, {"overlay": bad})))[0] is None


def test_the_manager_preserves_native_sounds_alongside_qualified_battle_options():
    """server/manager.py OPTION_SUPPORT: native_sounds remains granted alongside the qualified
    battle options, while phone_calls remains refused. Other families
    keep their native_sounds rows.

    RED CONTROL: refuse a qualified option or grant phone_calls -> the exact set breaks.
    """
    import sys
    sys.path.insert(0, str(REPO))
    from server import manager
    rows = {}
    for option, spec in manager.OPTION_SUPPORT.items():
        entry = spec.get("gen2_polished") if isinstance(spec, dict) else None
        if isinstance(entry, dict) and "ok" in entry:
            rows[option] = entry["ok"]
    assert rows["native_sounds"] is True
    # The calculator and companion battle options coexist with the sound grant.
    assert sorted(o for o, ok in rows.items() if ok) == [
        "battle_calc", "explode_mode", "native_sounds", "rival_team_swap"], rows
    assert rows["phone_calls"] is False
    golden = json.loads((REPO / "tests/fixtures/manager_tables_pre_gen3_exp.json").read_text(encoding="utf-8"))
    assert golden["support"]["gen2_polished"]["native_sounds"] == {"ok": True, "why": ""}
    # every other family's native_sounds row is untouched
    for family in ("gen1", "gen1_purergb", "gen2", "gen3", "gen3_e", "gen3_rr"):
        assert golden["support"][family]["native_sounds"] == {"ok": True, "why": ""}, family
