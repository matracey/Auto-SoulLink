"""PHYSICAL lane for card gen2-p4-w6: the live mailbox write-watch tripwire on the patched Gen 2 overlays.

    SLINK_LIVE=1 pytest tests/live/test_gen2_w6_gate.py -q -p no:randomly -k crystal   (or gold, silver)

O-27 D1: the SLink mailbox span (tools/gen2_mailbox_census.MAILBOX_SPANS) is protected by writer exclusion,
proven from source by the census; this is the live tripwire over the scripted corpus, never a proof. Per title
it runs the existing scripted gates on the <title>_overlay cartridge (tools/run_gb_gate.py stages the published
UPS over the clean build and checks the provenance sha1), each wrapped by lua/tests/gen2_w6_gate.lua:

    panel   lua/tests/gen2_panel_gate.lua     menus, the client panel, fallback, fade stress, a wild battle
    sfx     lua/tests/gen2_sfx_gate.lua       every sound context, a battle, a soft reset
    u1      lua/tests/gen2_frame_align.lua    Route 29 play, wild battle + Poke Ball capture + native save,
                                              the overworld poison leg and the in-battle faint

Every inner gate must still PASS its own independent verify() (the corpus really ran), and every CPU write
into the span must come from a SLink code range of the pinned overlay .sym (w6_facts), Init's WRAM0 clear at
boot/reset, or the shipped Lua client. Both known-positive controls (a native writer on wVBlankOccurred, a
non-client Lua store into the span) must be caught in every leg. On PASS writes

    tests/fixtures/gen2/receipts/<title>_overlay.w6_gate.json

Skipped without EmuHawk, the pinned build, a fixture or its qualification receipt.
"""
from __future__ import annotations

import hashlib
import importlib.util
import inspect
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tools"))
sys.path.insert(0, str(REPO / "patch/tools"))

from tests.live import (  # noqa: E402
    test_gen2_new_gates as live,
    test_gen2_panel_gate as panel,
    test_gen2_sfx_gate as sfx,
)
from tests.live.test_gen2_new_gates import emuhawk  # noqa: E402,F401 - pytest fixture
from tools import gen2_fixtures, gen2_source_data, gen2_synth_fixtures  # noqa: E402
from tools.gen2_mailbox_census import MAILBOX_SPANS  # noqa: E402

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.environ.get("SLINK_LIVE") != "1",
                       reason="live Gen 2 gates only run with SLINK_LIVE=1 (spawns EmuHawk)"),
]

GATE = "lua/tests/gen2_w6_gate.lua"
SCHEMA = "gen2-w6-gate-v1"
TITLES = ("crystal", "gold", "silver")
LEGS = ("panel", "sfx", "u1")
STAGGER = 60          # seconds between this lane's EmuHawk launches (other lanes share the machine)
# The U1 leg runs a FROZEN U1 driver chain, {repo path: ref}, recorded in the receipt (main 2026-09-23).
#   a7bf1773  U1e: catch, save, poison, battle faint, whiteout; the faint driver at c60c45c3 (its 0-PP fix; live:
#             the a7bf1773 driver loops on TAIL WHIP at 0 PP)
#   a882a763  U1f (PHYSICAL on all three titles): U1e + the Cherrygrove re-heal (5c2ce468) + Bill's PC leg
#   76d715e6  U1f + the ledge-aware walker (0ce58e0d, its sibling gen2_walk.lua frozen too) + the poison leg's
#             flake fixes; before EVO-U1 (ebfa3dcd)
# Every title runs U1f (main 2026-09-24); Gold moved first: its U1e Route 31 hunt loses a party mon to attrition
# before the poison (frame 46514). On the 9805ac1c overlays Silver's poison hunt stalls in battle on Route 26:1
# (8,49) on EVERY chain (a882a763 twice at 72662, 76d715e6 at 75099, a7bf1773 at 71761), 0 overlay violations
# each time (likely cause, unproven: the dispatcher's added reads shift rDIV-driven encounters) and the hunt never
# meets its foe. Silver keeps its last PHYSICAL pin (a7bf1773, on d09e76c1) until the hunt is made RNG-robust.
_U1 = ("lua/tests/gen2_frame_align.lua", "lua/tests/gen2_poison_inputs.lua", "lua/tests/duo/gen2_faint_inputs.lua",
       "tests/live/test_gen2_frame_align.py")
_U1F = _U1 + ("lua/tests/gen2_pc_inputs.lua",)
U1_CHAINS = {"a7bf1773": {**dict.fromkeys(_U1, "a7bf1773"), "lua/tests/duo/gen2_faint_inputs.lua": "c60c45c3"},
             "a882a763": dict.fromkeys(_U1F, "a882a763"),
             "76d715e6": dict.fromkeys(_U1F + ("lua/tests/gen2_walk.lua",), "76d715e6"),
             # the CURRENT drivers, pinned to the checkout's own commit: Gold's U1 leg needs the disclosed SYNTH poisoned
             # lead (start_phase "tick", O-33), which the 09-24 chains predate. Its Wade-route natural poison setup is a
             # deterministic loss (sweep ffd54b44: 'a party mon fainted in battle before the poison', both attempts).
             "head": dict.fromkeys(_U1F + ("lua/tests/gen2_walk.lua",), "HEAD")}
U1_CHAIN_FOR = {"crystal": "a882a763", "gold": "head", "silver": "a7bf1773"}
SYNTH_PSN_U1 = {"gold": "gold_synth_psn"}   # titles whose U1 leg boots the disclosed SYNTH poisoned lead
# O-33 clock setup for the U1 leg (tools/gen2_synth_fixtures.day_clock, disclosed in the leg as clock_setup): the
# fixture RTC runs on with the host clock (game = the save's wStart time + RTC), and Silver Route 30 holds Weedle only by morning/day
# (pokegold data/wild/johto_grass.asm ROUTE_30, _SILVER nite: Hoothoot/Rattata), so a night launch never meets a
# POISON_STING foe (W6 Silver RED on every chain, 2026-09-24). Crystal (night Spinarak) hunts at any hour. Gold boots
# the SYNTH poisoned lead (SYNTH_PSN_U1), so it needs no hunter, but it is still pinned to 11:00: the RTC otherwise runs
# on from the host clock (EVO-U1), so the pin keeps the route a pure function of the committed fixture + the disclosed
# clock_setup, which the verifier re-derives. Only the emulator RTC trailer changes; the CartRAM is the fixture's.
U1_CLOCK = {"silver": 11, "gold": 11}
IN_PLACE_CODE = ("SlinkStartMenuEntry",)   # patch/gen2/src/panel_start.asm, bank 4
INIT_LOOP = bytes.fromhex("3600230b78b120f8")   # Init.ByteFill: ld [hl],0 / inc hl / dec bc / ld a,b / or c / jr nz


def _symbols(title: str, repo: Path = REPO) -> tuple[dict, list]:
    """The pinned overlay .sym (sha256-bound by the provenance, via panel.panel_facts): name -> (bank, addr)."""
    text = (repo / "data/gen2" / f"{title}_slink.sym").read_text(encoding="utf-8")
    rows = [(int(b, 16), int(a, 16), n) for b, a, n in re.findall(r"^([0-9a-f]{2}):([0-9a-f]{4}) (\S+)$", text, re.M)]
    return {n: (b, a) for b, a, n in rows}, rows


def w6_facts(title: str, *, repo: Path = REPO) -> dict:
    """Span, allowlist, Init clear and the control byte, all from the pinned overlay .sym."""
    base = panel.panel_facts(title, repo=repo, sites=("Init.ByteFill",), ram=("hROMBank", "wVBlankOccurred"))
    assert base["sites"]["Init.ByteFill"]["hex"] == INIT_LOOP[:3].hex(), "Init's WRAM0 clear moved"
    symbols, rows = _symbols(title, repo)
    service_bank = symbols["SlinkService"][0]
    allow = [{"name": "SLink service bank", "bank": service_bank, "lo": 0x4000, "hi": 0x8000}]
    # Outside the service bank: the ROM0 Slink* bridges and the in-place CODE hunks named in IN_PLACE_CODE, each
    # running to the next symbol in its bank. In-place data/script labels (SlinkMenuString,
    # SlinkSpecialPhoneCallList, SlinkTradeReceptionistScript) are never CPU code: a writer PC there is a fault.
    # A new in-place code hunk fails closed until it is named here.
    for bank, addr, name in rows:
        if (not name.startswith("Slink") or "." in name or name.endswith("End") or addr >= 0x8000
                or not (bank == 0 or name in IN_PLACE_CODE)):
            continue
        nxt = min((a for b, a, _ in rows if b == bank and a > addr), default=0x4000 if bank == 0 else 0x8000)
        allow.append({"name": name, "bank": bank, "lo": addr, "hi": nxt})
    init_bank, init_lo = symbols["Init.ByteFill"]
    assert init_bank == 0
    lo, hi = MAILBOX_SPANS[title]
    # SLink-owned native regions (main 2026-09-23): the P4.5b phone service writes the WORD wSpecialPhoneCallID
    # (+0, +1) from the service bank only; any other SLink range writing it is a violation.
    phone_bank, phone = symbols["wSpecialPhoneCallID"]
    assert phone_bank == 1 and 0xD000 <= phone < 0xE000, "wSpecialPhoneCallID left WRAMX bank 1"
    regions = [{"name": "wSpecialPhoneCallID", "lo": phone, "hi": phone + 2, "wram_bank": 1,
                "slink_allow": ["SLink service bank"]}]
    return {"overlay_sha1": base["overlay_sha1"], "span": [lo, hi], "allow": allow, "regions": regions,
            "init": {"name": "Init WRAM0 clear", "entry": symbols["Init"][1], "lo": init_lo, "hi": init_lo + len(INIT_LOOP)},
            "hrombank": symbols["hROMBank"][1], "control": symbols["wVBlankOccurred"][1]}


def symbolize(title: str, key: str, repo: Path = REPO) -> str:
    """'75:4012' -> '75:4012 SlinkService.sample+0x3' (nearest symbol at or below, same bank)."""
    bank, addr = (int(x, 16) for x in key.split(":"))
    _, rows = _symbols(title, repo)
    # An `...End` label shares its address with the next routine (SlinkServiceEnd == SlinkPanel): skip it.
    best = max(((a, n) for b, a, n in rows if b == bank and a <= addr and a < 0x8000 and not n.endswith("End")),
               default=None)
    return f"{key} {best[1]}+0x{addr - best[0]:x}" if best else key


def frozen_u1(chain: str):
    """Each file of U1_CHAINS[chain] at its ref under .cache/gen2-w6-frozen/<chain> (the Lua wrapper serves them in
    place of the worktree files), {path: {ref, sha256}}, and the frozen test module (u1_facts, verify, U1_FIXTURE)."""
    base = REPO / ".cache/gen2-w6-frozen" / chain
    files = {}
    # the receipt names the exact commit, never the moving ref: resolved ONCE, before the file loop
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, check=True,
                          text=True).stdout.strip()
    for rel, ref in U1_CHAINS[chain].items():
        if ref == "HEAD":
            ref = head
        data = subprocess.run(["git", "show", f"{ref}:{rel}"], cwd=REPO, capture_output=True, check=True).stdout
        (base / rel).parent.mkdir(parents=True, exist_ok=True)
        (base / rel).write_bytes(data)
        files[rel] = {"ref": ref, "sha256": hashlib.sha256(data).hexdigest()}
    spec = importlib.util.spec_from_file_location("w6_frozen_u1_" + chain, base / "tests/live/test_gen2_frame_align.py")
    module = importlib.util.module_from_spec(spec)
    path = list(sys.path)
    spec.loader.exec_module(module)
    sys.path[:] = path          # its REPO-relative sys.path inserts point into the cache
    module.REPO = REPO
    return base, files, module


def verify_u1(u1, text: str, pack: dict, title: str):
    """The frozen 09-24 chains verify against the clean pack (no `identity`); the current-drivers chain verifies the
    overlay run against its own overlay identity, exactly as test_gen2_frame_align does (sweep wave3: the head module's
    verify() without an identity took the clean-ROM branch and failed a PASSING overlay run on rom_sha1)."""
    if "identity" in inspect.signature(u1.verify).parameters:
        return u1.verify(text, pack, title, live.identity(title, "overlay"))
    return u1.verify(text, pack, title)


def _legs(title: str, u1) -> dict:
    """leg -> (inner gate, its result file, fixture name, env builder, verify, timeout); u1 = the frozen U1 module."""
    def panel_env(spec, qual):
        return {"SLINK_GEN2_PANEL_FACTS": json.dumps(panel.panel_facts(title))}

    def sfx_env(spec, qual):
        return {"SLINK_GEN2_SFX_FACTS": json.dumps(sfx.sfx_facts(title))}

    def u1_env(spec, qual):
        # the leg boots the overlay: its facts resolve on the overlay symbols/bytes (D4), never the clean ones
        ctx = gen2_fixtures.exec_context(spec.title, "overlay", REPO)
        facts = gen2_fixtures.spec_route_facts(spec, REPO, kind="overlay")
        if title in SYNTH_PSN_U1:   # the leg boots the SYNTH poisoned lead: skip the hunt (start_phase "tick")
            return {"SLINK_GEN2_U1_FACTS": json.dumps(u1.u1_facts(ctx, facts, qual, synth_psn=True))}
        return {"SLINK_GEN2_U1_FACTS": json.dumps(u1.u1_facts(ctx, facts, qual))}

    pack = json.loads((REPO / f"data/games/gen2_{title}/engine_signals.json").read_text(encoding="utf-8"))
    return {
        "panel": (panel.GATE, "patch/build/gen2_panel_gate_result.txt", f"{title}_battle", panel_env,
                  lambda text, staged: panel.verify(text, panel.panel_facts(title), title, staged), 1800),
        "sfx": (sfx.GATE, "patch/build/gen2_sfx_gate_result.txt", f"{title}_battle", sfx_env,
                lambda text, staged: sfx.verify(text, sfx.sfx_facts(title), title, staged), 1800),
        "u1": (u1.GATE, "patch/build/gen2_frame_align_result.txt", u1.U1_FIXTURE[title], u1_env,
               lambda text, staged: verify_u1(u1, text, pack, title), 3600),   # the U1f gate's own bound
    }


def verify_leg(record: dict, leg: str, facts: dict) -> None:
    """The W6 record of one leg: completed, writers only allowed, both controls caught."""
    assert record["schema"] == "gen2-w6-leg-v1" and record["leg"] == leg and record["evidence_level"] == "PHYSICAL"
    assert record["overlay_sha1"] == facts["overlay_sha1"] and record["span"] == facts["span"], record
    assert record["inner_completed"] is True and record["corpus_frames"] > 0, record
    assert record["violation_count"] == 0 and record["violations"] == [], record["violations"]
    assert record["allowed_writes"] == sum(record["writers"].values()) > 0, record["writers"]
    assert not record["lua_writes"]["harness"], record["lua_writes"]
    control = record["control"]
    assert control["native_caught"] is True and control["native_flagged"] > 0, control
    assert control["lua_caught"] is True and control["lua_kind"] == "harness", control
    assert set(record["regions"]) == {r["name"] for r in facts["regions"]}, record["regions"]


@pytest.mark.parametrize("title", TITLES)
def test_mailbox_write_watch_on_the_overlay(emuhawk, title):  # noqa: F811
    from run_gb_gate import run_gate

    facts = w6_facts(title)
    chain = U1_CHAIN_FOR[title]
    frozen_dir, frozen_files, u1 = frozen_u1(chain)
    legs, launched = {}, False
    for leg, (gate, inner_result, fixture_name, extra_env, check, timeout) in _legs(title, u1).items():
        spec = gen2_fixtures.BY_NAME[fixture_name]
        reason = (live.rom_missing_reason(spec.title) or live.fixture_missing_reason(spec.name)
                  or live.receipt_missing_reason(spec.name, kind="overlay"))
        if reason:
            pytest.skip(reason)
        fixture = REPO / "tests/fixtures/gen2" / f"{spec.name}.SaveRAM"
        staged = fixture.read_bytes()
        live.qualified_identity(spec.name, staged, kind="overlay")
        qual = json.loads(live.receipt_file(f"{spec.name}.qualification.json", "overlay").read_text(encoding="utf-8"))
        env = live.inspect_env(spec, staged, kind="overlay")
        env.update(extra_env(spec, qual["attempt_id"]))
        env["SLINK_GEN2_QUALIFICATION_ATTEMPT"] = qual["attempt_id"]
        source = gen2_source_data.load_context(title, root=REPO).source_record()
        env["SLINK_GEN2_W6"] = json.dumps({"leg": leg, "gate": gate, "inner_result": inner_result, "facts": facts,
                                           "overlay_sha1": facts["overlay_sha1"], "base_sha1": source["rom_sha1"],
                                           "lua_control_offset": 20,
                                           "frozen": {rel: str(frozen_dir / rel) for rel in U1_CHAINS[chain]
                                                      if rel.endswith(".lua")} if leg == "u1" else None})
        if launched:
            time.sleep(STAGGER)
        launched = True
        source_path, clock, poison = fixture, None, None
        boot = staged
        if leg == "u1" and title in SYNTH_PSN_U1:   # O-33: the disclosed SYNTH poisoned lead (setup, not the subject)
            boot, poison = gen2_synth_fixtures.build_named(SYNTH_PSN_U1[title], root=REPO)
        if leg == "u1" and title in U1_CLOCK:   # set right before the launch: the RTC runs on from here
            raw, clock = gen2_synth_fixtures.day_clock(boot, hour=U1_CLOCK[title], now=int(time.time()),
                                                      title=title)
            source_path = REPO / ".cache/gen2-fixtures/w6" / f"{title}-{leg}-clock.SaveRAM"
            source_path.parent.mkdir(parents=True, exist_ok=True)
            source_path.write_bytes(raw)
        # A launch that fails before Lua init must not report an earlier leg's inner failure.
        (REPO / inner_result).unlink(missing_ok=True)
        passed, path, text = run_gate(GATE, rom_key=f"{title}_overlay", target=spec.target, timeout=timeout,
                                      saveram_dir=str(REPO / ".cache/gen2-fixtures/w6" / f"{title}-{leg}"),
                                      fixture_path=str(source_path), speed_percent=300, env_overrides=env)
        inner_text = (REPO / inner_result).read_text(encoding="utf-8", errors="replace") \
            if (REPO / inner_result).is_file() else ""
        assert passed, (f"{leg}: W6 gate FAILED; {path}: {text[-3000:]}\ninner failures:\n"
                        + "\n".join(line for line in inner_text.splitlines() if "[FAIL]" in line)
                        + f"\ninner tail: {inner_text[-2000:]}")
        assert fixture.read_bytes() == staged, "fixture changed while the gate ran"
        check(inner_text, staged)   # the corpus leg really ran and passed its own independent re-check
        record = live.tag_json(text, "W6")
        verify_leg(record, leg, facts)
        legs[leg] = {**record, "fixture": spec.name, "fixture_sha256": hashlib.sha256(staged).hexdigest(),
                     "qualification_attempt_id": qual["attempt_id"], "clock_setup": clock,
                     **({"poison_setup": poison, "synth_psn": True,
                         "poison_start_phase": json.loads(env["SLINK_GEN2_U1_FACTS"])["poison"]["start_phase"]}
                        if poison is not None else {}),
                     "driver": {"ref": chain, "files": frozen_files} if leg == "u1" else None,
                     "writers": {symbolize(title, k): v for k, v in sorted(record["writers"].items())},
                     "boot_clear": {symbolize(title, k): v for k, v in sorted(record["boot_clear"].items())},
                     "regions": {name: {**r, "native": {symbolize(title, k): v for k, v in sorted(r["native"].items())},
                                        "slink": {symbolize(title, k): v for k, v in sorted(r["slink"].items())}}
                                 for name, r in record["regions"].items()},
                     "control": {**record["control"], "native_writers": {
                         symbolize(title, k): v for k, v in sorted(record["control"]["native_writers"].items())}}}

    writers: dict[str, int] = {}
    for record in legs.values():
        for key, count in record["writers"].items():
            writers[key] = writers.get(key, 0) + count
    first = legs["panel"]
    # B2: the executed identity, OBSERVED in the emulator (gameinfo.getromhash) in EVERY leg and
    # cross-checked against the independently hashed staged bytes.
    identity = live.identity(title, "overlay")
    for leg, record in legs.items():
        assert record["observed_rom_sha1"] == identity["rom_sha1"], \
            f"{leg}: the gate ran other bytes than the staged overlay"
    receipt = {"schema": SCHEMA, "title": title, "evidence_level": "PHYSICAL", "result": "PASS",
               "overlay_sha1": facts["overlay_sha1"], "observed_rom_sha1": first["observed_rom_sha1"],
               "artifact_kind": identity["kind"], "rom_sha1": identity["rom_sha1"],
               "base_sha1": identity["base_sha1"], "binding_sha256": identity["binding_sha256"],
               "fixture": first["fixture"], "fixture_sha256": first["fixture_sha256"],
               "facts": facts, "legs": legs, "corpus_frames": sum(r["corpus_frames"] for r in legs.values()),
               "writers": dict(sorted(writers.items())), "violation_count": 0,
               "input_mode": "normal_buttons", "harness_write_scopes": []}
    # B1: overlay evidence lands in the overlay namespace, never beside the clean receipts.
    live.receipt_file(f"{title}.w6_gate.json", "overlay").write_text(
        json.dumps(live.stamped(receipt), indent=1, sort_keys=True) + "\n", encoding="utf-8")

