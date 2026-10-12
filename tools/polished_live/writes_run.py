#!/usr/bin/env python3
"""Run card POL-LIVE-WRITES on the INTEGRATED Polished overlay (tools/polished_live/writes.lua):
legs (a) force_faint, (b) box_mon, (d) party_mon, and (e)/(f) memorialize from the party and from a box
(POL_MEMORIAL=1, the default here; docs/polished/MEMORIALIZE.md).

    python tools/polished_live/writes_run.py

Nothing is rebuilt. The staged ROM is the pinned release with the committed
patch/dist/SLink-Polished.ups applied, and it is refused unless its sha1 is the integrated
overlay `68894579…` (C5 present, commit gate disabled). The save fixture is
copied into this lane and its sha256 printed, so another card rewriting its own lane cannot move
this run's inputs.

A private SLink server settles the real client's connection; commands are driven through
`Client:handle_command`, the same public entry the real reply path uses.

One EmuHawk at a time, engine warp only, short non-Drive lane. Only the PID this script starts is
ever killed (harness.kill_own) — never an image-name kill.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))
sys.path.insert(0, str(REPO))

FIXTURE_SHA256_PREFIX = "75c7a5dc"

from tools.polished_live import harness  # noqa: E402

# noqa: E402
from tools.polished_live.overlay_pin import overlay_sha1  # noqa: E402

DEFAULT_LANE = "F:/slink-work/lanes/pol-livew"
RELEASE = Path("F:/slink-work/cache/polished/release/polishedcrystal-3.2.3.gbc")
UPS = REPO / "patch/dist/SLink-Polished.ups"
# BizHawk derives the save name from the ROM FILE STEM with underscores turned into spaces
# (harness.py:48): `pol_overlay.gbc` -> `pol overlay.SaveRAM`, which IS harness.SAVE_NAME.
# Staging as `pol_writes.gbc` made it look for `pol writes.SaveRAM`, so no copy ever loaded and
# BizHawk silently created a fresh one -- that is why the boot came up as a new game twice.
# Stage under the harness's own name.
# The rebuilt C5 overlay; symbol presence does not enable its commit call sites.
INTEGRATED_SHA1 = overlay_sha1()


def stage(config) -> str:
    return harness.stage_pinned_rom(RELEASE, config.rom, INTEGRATED_SHA1, patch=UPS)


def main() -> int:
    env = dict(os.environ)
    env.setdefault("POL_LANE", DEFAULT_LANE)
    env.setdefault("POL_FIXTURE", "F:/slink-work/lanes/g2int-live/pol/fixture/polished_overlay_warp.SaveRAM")
    config = harness.launch_config(env)
    sha1 = stage(config)
    print(f"[writes] staged {config.rom} sha1 {sha1}", flush=True)
    source = config.fixture
    if not source.is_file():
        raise SystemExit(f"no save fixture at {source}")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    if not digest.startswith(FIXTURE_SHA256_PREFIX):
        raise SystemExit(f"fixture sha256 {digest} does not start {FIXTURE_SHA256_PREFIX}: {source}")
    print(f"[writes] fixture {source} sha256 {digest}", flush=True)

    save = harness.prepare_save(config, source, clear=True)
    print(f"[writes] save as {save}", flush=True)

    # The real client dials a TCP peer on start. There is no SLink server here (the card drives
    # the client), so start the harness's own server exactly as harness.cmd_live does -- otherwise
    # the connector retries forever and the boot never settles.
    run = config.lane / "writes"
    srv_dir = run / "srv"
    if srv_dir.exists():
        shutil.rmtree(srv_dir)
    srv_dir.mkdir(parents=True)
    (srv_dir / "rom_contract.json").write_text(
        json.dumps({"upr_version": "none (overlay, not randomized)", "categories": [],
                    "players": {"a": {"rom_sha1": sha1}}}), encoding="utf-8")
    port, http = harness.free_port(), harness.free_port()
    with open(run / "server.log", "w", encoding="utf-8") as srv_log:
        srv = subprocess.Popen([sys.executable, "-m", "server.server", "--host", "127.0.0.1",
                                "--port", str(port), "--http-port", str(http),
                                "--data-dir", str(srv_dir), "--wire-log", str(run / "wire")],
                               cwd=str(REPO), stdout=srv_log, stderr=subprocess.STDOUT)
        print(f"[writes] server pid {srv.pid} tcp {port}", flush=True)
        time.sleep(1.5)
        pid = None
        try:
            # POL_MEMORIAL=1 runs legs (e) party-origin and (f) box-origin memorialize after (a)/(b)/(d)
            text, pid = harness.launch(config, "tools/polished_live/writes.lua", run,
                                       {"SLINK_HOST": "127.0.0.1", "SLINK_PORT": str(port),
                                        "POL_MEMORIAL": os.environ.get("POL_MEMORIAL", "1")}, 420)
        finally:
            harness.kill_own(srv, "server")
    print(text[-4000:])
    result = run / "result.txt"
    if result.is_file():
        print(f"[writes] result.txt sha256 {hashlib.sha256(result.read_bytes()).hexdigest()}", flush=True)
    ok = "RESULT: PASS" in text
    print(f"[writes] {'PASS' if ok else 'FAIL'} (EmuHawk pid {pid})", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
