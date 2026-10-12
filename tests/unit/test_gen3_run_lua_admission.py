"""lua/gen3/run.lua must enforce the SAME admission policy as the launcher (lua/slink.lua),
not just Entry.admit's raw hash/anchors/header verdict.

The duo harness (lua/tests/duo/duo_gen3_main.lua) dofiles lua/gen3/run.lua directly, bypassing
lua/slink.lua's Entry.ROUTED / admitted_by check entirely -- so before Entry.admit_routed
existed, the duo evidence never actually proved the launcher's refusal of a header-only
admission or an unrouted pack (independent review OMP cx-dbabbd62). This drives the real
lua/gen3/run.lua under lupa with BizHawk's globals stubbed (the RUN_HOST idiom from
tests/unit/test_gen2_client.py::test_run_lua_exposes_the_production_client_only_for_an_admitted_cartridge
/ tests/unit/test_gen2_release_bundle_boot.py), asserting run.lua refuses by the SAME reason
Entry.admit_routed gives, and SLINK_GEN3_CLIENT is never set.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

lupa = pytest.importorskip("lupa", reason="lupa is needed to execute run.lua")

REPO = Path(__file__).resolve().parents[2]
RUN_LUA = REPO / "lua" / "gen3" / "run.lua"

sys.path.insert(0, str(REPO / "tests" / "unit"))
from test_gen3_entry import (  # noqa: E402  (reuse the real anchor synthesis)
    artifact_of,
    seed_rom,
    sites_of,
)


def _run(
    rom: dict[int, int],
    rom_hash: str,
    drop_pack: str | None = None,
    *,
    hud_messages=None,
    boot_failure=None,
):
    """dofile the real lua/gen3/run.lua with BizHawk stubbed. `drop_pack`, when set, loads the
    REAL lua/gen3/entry.lua and then clears that one pack from its Entry.ROUTED table before
    handing it back -- a temp-root copy would also need a temp copy of every data/games/* pack
    file Entry.admission_table/artifacts read, so mutating the loaded module in place (the same
    global-dofile-interception idiom test_slink_route.py uses for its OWN real targets) gets the
    same effect without duplicating the pack tree.

    Returns (SLINK_GEN3_CLIENT, logs).
    """
    lua = lupa.LuaRuntime(unpack_returned_tuples=True)
    g = lua.globals()
    logs: list[str] = []
    real_dofile = g.dofile
    g.BOOT_FAILURE_REASON = "engine sites differ from the ROM: " + ", ".join(
        sorted(sites_of("gen3_frlg", "firered", "companion"))
    )
    fail = lua.eval("function() error(BOOT_FAILURE_REASON,0) end")

    def fake_dofile(path):
        module = real_dofile(path)
        if drop_pack and str(path).replace("\\", "/").endswith("/lua/gen3/entry.lua"):
            module.ROUTED[drop_pack] = False
        if boot_failure and str(path).replace("\\", "/").endswith("/lua/gen3/entry.lua"):
            module.build = fail if boot_failure == "build" else lambda *_: lua.table(start=fail)
        return module

    g.dofile = fake_dofile
    g.memory = lua.table_from(
        {
            "read_u8": lambda a, d=None: rom.get(int(a), 0) if d == "ROM" else 0,
            "read_u16_le": lambda a, d=None: 0,
            "read_u32_le": lambda a, d=None: 0,
            "write_u8": lambda *a: None,
        }
    )
    g.emu = lua.table_from({"framecount": lambda: 1, "getregister": lambda name: 0})
    g.event = lua.table_from(
        {
            "on_bus_exec": lambda fn, addr, name: name,
            "onframeend": lambda fn: None,
            "onexit": lambda fn: None,
            "unregisterbyid": lambda hid: True,
        }
    )
    g.console = lua.table_from({"log": lambda t: logs.append(str(t))})
    g.gameinfo = lua.table_from({"getromhash": lambda: rom_hash})
    lua.execute(
        "package.loaded.connector = {init=function()end,send=function()end,"
        "receive=function()end,pump=function()end,connected=function()return false end}"
    )
    g.package.loaded.socket = lua.table(socket=lua.table(tcp=lambda: lua.table(
        settimeout=lambda *_: None, bind=lambda *_: True,
        listen=lambda *_: True, close=lambda *_: None,
    )))
    shown = hud_messages if hud_messages is not None else []
    g.package.loaded.hud = lua.table(
        init=lambda *_: None, render=lambda *_: None, show=lambda text, *_: shown.append(str(text))
    )
    # An admitted failure must not allocate a real installation nonce on disk.
    g.os.rename = lambda *_: (None, "MODEL unavailable", 13)
    lua.execute("local n=0; os.clock=function() n=n+1; return n end")
    g.SLINK_GEN3_CLIENT = "stale"
    real_dofile(str(RUN_LUA))
    return g.SLINK_GEN3_CLIENT, logs


def test_a_header_only_admission_is_refused_naming_the_pinned_cartridge_rule():
    """Case 1: an unknown-hash BPEE build with no matching anchors admits by header alone
    (admitted_by == "header") -- Entry.admit_routed must refuse it, and run.lua must actually
    ask admit_routed, not the bare Entry.admit (which would return this same admission as a
    truthy hit and let Entry.build run on an unpinned cartridge)."""
    rom = seed_rom({}, header_code="BPEE")
    client, logs = _run(rom, rom_hash="f" * 40)
    assert client == "stale"
    assert any("not a pinned cartridge" in line for line in logs), logs


def test_a_pack_dropped_from_routed_is_refused_even_when_anchors_admit_it():
    """Case 2: a synthetic ROM whose anchors admit it as gen3_frlg/firered/companion (rand_companion) (unknown hash,
    same synthesis as test_gen3_entry.py's test_admission_by_anchors_when_the_hash_is_unknown),
    but with gen3_frlg cleared from Entry.ROUTED. Entry.admit alone would happily return this
    admission; only admit_routed's own ROUTED check refuses it."""
    rom = seed_rom(sites_of("gen3_frlg", "firered", "companion"), header_code="BPRE")
    client, logs = _run(rom, rom_hash="0" * 40, drop_pack="gen3_frlg")
    assert client == "stale"
    assert any("gen3_frlg" in line and "not yet routed" in line for line in logs), logs


def test_a_clean_companion_title_is_refused_by_the_launcher_and_never_built():
    """Patch-first (owner 2026-10-02): the pinned CLEAN FireRed is a known hash, yet run.lua refuses it
    with the companion reason, and (boot_failure would otherwise fire) never reaches build."""
    shown = []
    rom = seed_rom(sites_of("gen3_frlg", "firered", "clean"))
    digest = artifact_of("gen3_frlg", "firered", "clean")["rom_sha1"]
    client, logs = _run(rom, digest, hud_messages=shown, boot_failure="build")
    assert client == "stale"
    assert any("needs the SLink companion patch" in line and "Manager or /patcher" in line for line in logs), logs
    assert not any("engine sites differ" in line for line in logs)


@pytest.mark.parametrize("phase", ["admit", "build", "start"])
def test_boot_refusals_keep_diagnostics_on_console_and_short_text_on_hud(phase):
    shown = []
    if phase == "admit":
        rom, digest = seed_rom({}, header_code="BPEE"), "f" * 40
    else:
        # the COMPANION artifact: a clean FireRed is refused before it can boot (patch-first)
        rom = seed_rom(sites_of("gen3_frlg", "firered", "companion"))
        digest = artifact_of("gen3_frlg", "firered", "companion")["rom_sha1"]
    client, logs = _run(
        rom, digest, hud_messages=shown, boot_failure=None if phase == "admit" else phase
    )
    assert client == "stale"
    assert shown == ["SLINK COULD NOT START - SEE LOG"]
    assert all(len(line) <= 39 for line in shown)
    detail = "not a pinned cartridge" if phase == "admit" else "engine sites differ from the ROM:"
    assert any(detail in line for line in logs)
    if phase != "admit":
        assert any("battle_begin" in line and "whiteout" in line for line in logs)
