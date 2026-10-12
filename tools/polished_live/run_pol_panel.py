"""Panel driver on a private lane, staged from the committed provenance-pinned UPS."""
import json
import os
import shutil
import sys
from dataclasses import replace
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(REPO), str(REPO / "tools/polished_live")]
from tools.polished_live import harness as H  # noqa: E402

EXTRA_SYMBOLS = (
    "SlinkPanel", "SlinkPanel.page", "SlinkPanel.ready", "SlinkPanel.close", "SlinkPanel.WaitForStage",
    "SlinkPanelFallback", "SlinkPanelEnd", "wSlinkPanelText", "wSlinkMailboxEnd",
    "wPhoneList", "wNumSetBits", "wCurCaller", "wPokegearPhoneCursorPosition", "wPokegearPhoneScrollPosition",
    "wPokegearPhoneSelectedPerson", "PokegearPhoneContactSubmenu", "MakePhoneCallFromPokegear", "SlinkPhone_CallGate",
)
RELEASE = Path("F:/slink-work/cache/polished/release/polishedcrystal-3.2.3.gbc")
UPS = REPO / "patch/dist/SLink-Polished.ups"
PROV = REPO / "data/polished/overlay_provenance.json"
DEFAULT_FIXTURE = "F:/slink-work/lanes/g2int-live/pol/fixture/polished_overlay_warp.SaveRAM"


def panel_config(env=None):
    values = dict(os.environ if env is None else env)
    values.setdefault("POL_LANE", "F:/slink-work/lanes/pol-panel2")
    config = H.launch_config(values)
    return replace(config, symbols=tuple(dict.fromkeys((*config.symbols, *EXTRA_SYMBOLS))))


def stage_from_committed_ups(config) -> str:
    want = json.loads(PROV.read_text(encoding="utf-8"))["output"]["sha1"]
    return H.stage_pinned_rom(RELEASE, config.rom, want, patch=UPS)


def main() -> int:
    config = panel_config()
    sha1 = stage_from_committed_ups(config)
    source = Path(os.environ.get("POL_FIXTURE", DEFAULT_FIXTURE))
    config.fixture.parent.mkdir(parents=True, exist_ok=True)
    if source.resolve() != config.fixture.resolve():
        shutil.copyfile(source, config.fixture)
    H.prepare_save(config, clear=True)
    text, pid = H.launch(config, "tools/polished_live/pol_panel.lua", config.lane / "run", {}, 900)
    print(f"rom {sha1} pid {pid}")
    print(text[-8000:])
    return 0 if "RESULT: PASS" in text else 1


if __name__ == "__main__":
    sys.exit(main())
