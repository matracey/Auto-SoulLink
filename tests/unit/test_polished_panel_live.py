"""The panel live runner must not mutate a historical lane when a private lane is leased."""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "tools/polished_live/run_pol_panel.py"


# Exercise main's actual filesystem writes; stop at the emulator boundary.
# Tiny stage inputs isolate lane selection from UPS format/game content.
PRIVATE_RUN = r'''
import hashlib, json, runpy, sys
from pathlib import Path
lane = Path(sys.argv[1]).resolve()
inputs = lane / "inputs"
inputs.mkdir(parents=True)
(inputs / "base").write_bytes(b"base")
(inputs / "patch").write_bytes(b"patch")
(inputs / "fixture").write_bytes(b"synthetic save")
(inputs / "provenance").write_text(json.dumps({"output":{"sha1":hashlib.sha1(b"staged ROM").hexdigest()}}))
namespace = runpy.run_path(sys.argv[2], run_name="panel_live_unit")
driver = namespace["main"].__globals__
driver.update(RELEASE=inputs / "base", UPS=inputs / "patch", PROV=inputs / "provenance")
os = __import__("os")
os.environ["POL_FIXTURE"] = str(inputs / "fixture")
from patch.tools import make_ups
make_ups.ups_apply = lambda base, patch: b"staged ROM"
original_mkdir = Path.mkdir
def guarded_mkdir(path, *args, **kwargs):
    assert path.resolve().is_relative_to(lane), f"write escaped leased panel lane: {path}"
    return original_mkdir(path, *args, **kwargs)
Path.mkdir = guarded_mkdir
class EmulatorBoundaryReached(Exception): pass
def launch(config, lua, run, extra, timeout):
    run.mkdir(parents=True, exist_ok=True)
    raise EmulatorBoundaryReached
# No fake PASS result: reaching the native backend ends this isolated test.
driver["H"].launch = launch
try:
    driver["main"]()
except EmulatorBoundaryReached:
    sys.exit(0)
raise AssertionError("main did not reach its emulator backend")
'''


def test_private_panel_run_cannot_write_outside_its_leased_lane(tmp_path):
    lane = tmp_path / "leased-panel-lane"
    env = dict(os.environ, POL_LANE=str(lane), POL_KIND="overlay", POL_POSMODE="warp")
    env.pop("POL_FIXTURE", None)
    result = subprocess.run([sys.executable, "-c", PRIVATE_RUN, str(lane), str(RUNNER)],
                            cwd=ROOT, env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
