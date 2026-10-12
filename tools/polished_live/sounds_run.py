#!/usr/bin/env python3
"""Run the POL-SOUNDS live proof on the COMMITTED Polished overlay (card POL-SOUNDS round 2).

    python tools/polished_live/sounds_run.py

Nothing is rebuilt. The staged ROM is the pinned release ROM with the committed
patch/dist/SLink-Polished.ups applied, and it is refused unless its sha1 equals
data/polished/overlay_provenance.json's `output.sha1` (harness.stage_rom's own gate).

The driver is tools/polished_live/sounds.lua: a SCRIPTED MAILBOX WRITER. It presses only
wSlinkMailbox + SLINK_OFS_SFX_REQUEST and reads the audio engine; it is not the SLink client,
whose sound path belongs to the integration worker.

Everything runs under F:/slink-work/lanes/pol-sounds2 (short, non-Drive). Only the EmuHawk PID
this script starts is ever killed (harness.kill_own); never an image-name kill.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))
sys.path.insert(0, str(REPO))


from tools.polished_live import harness  # noqa: E402  (tools/polished_live/harness.py)

  # noqa: E402

DEFAULT_LANE = "F:/slink-work/lanes/pol-sounds2"
PROV = json.loads((REPO / "data/polished/overlay_provenance.json").read_text(encoding="utf-8"))
RELEASE = Path("F:/slink-work/cache/polished/release/polishedcrystal-3.2.3.gbc")
UPS = REPO / "patch/dist/SLink-Polished.ups"
# the sound driver reads three audio WRAM words the client harness does not
EXTRA = ("wCurSFX", "wChannel5Flags", "wMusicFade")


def stage(config) -> str:
    """Committed patch staging through the shared pin-before-write gate."""
    if hashlib.sha1(RELEASE.read_bytes()).hexdigest() != PROV["base_sha1"]:
        raise SystemExit("cached release ROM is not the pinned base")
    return harness.stage_pinned_rom(RELEASE, config.rom, PROV["output"]["sha1"], patch=UPS)


def main() -> int:
    from dataclasses import replace
    env = dict(os.environ)
    env.setdefault("POL_LANE", DEFAULT_LANE)
    env.setdefault("POL_FIXTURE", "F:/slink-work/lanes/pol-live/fixture/polished_overlay_warp.SaveRAM")
    config = harness.launch_config(env)
    config = replace(config, rom=config.lane / "rom/pol_sounds.gbc",
                     symbols=tuple(dict.fromkeys((*config.symbols, *EXTRA))))
    sha1 = stage(config)
    print(f"[pol-sounds] staged {config.rom} sha1 {sha1}", flush=True)
    source = config.fixture
    if not source.is_file():
        raise SystemExit(f"no save fixture at {source}; the game never gets past the intro")
    fixture = config.lane / "fixture" / "pol_sounds.SaveRAM"
    fixture.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, fixture)
    print(f"[pol-sounds] fixture sha256 {hashlib.sha256(fixture.read_bytes()).hexdigest()}", flush=True)
    harness.prepare_save(config, fixture)
    run = config.lane / "sounds"
    text, pid = harness.launch(config, "tools/polished_live/sounds.lua", run, {}, 180)
    print(text)
    data = [line for line in text.splitlines() if line.startswith("RESULT-DATA ")]
    if data:
        Path(str(run / "result.json")).write_text(data[0][len("RESULT-DATA "):], encoding="utf-8")
    print(f"[pol-sounds] result.txt sha256 "
          f"{hashlib.sha256((run / 'result.txt').read_bytes()).hexdigest() if (run / 'result.txt').exists() else 'n/a'}",
          flush=True)
    ok = "RESULT: PASS" in text
    print(f"[pol-sounds] {'PASS' if ok else 'FAIL'} (EmuHawk pid {pid})", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
