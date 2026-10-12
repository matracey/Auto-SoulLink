"""run_phone_ab: the KNOWN-GOOD Phone-card driver on this lane, against a chosen overlay ROM.

Round-3 step 4. The only variable between runs is the ROM; lane, fixture, driver, symbols and
emulator config are identical. That is what separates "the panel overlay breaks boot" from
"the driver/fixture is wrong".

    POL_ROM_UPS  path to a .ups to apply to the pinned release ROM (default: the committed one)
    POL_ROM_OUT  staged filename under $POL_LANE/rom (default pol_overlay.gbc)
    POL_FIXTURE  source SaveRAM (default: the g2int-live phone run's, which PASSED 75/75)
"""
import hashlib
import os
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tools"))
sys.path.insert(0, str(REPO / "patch" / "tools"))
sys.path.insert(0, str(REPO / "tools" / "polished_live"))

from make_ups import ups_apply  # noqa: E402

from tools.polished_live import harness as H  # noqa: E402

EXTRA_SYMBOLS = (
    "wPhoneList", "wNumSetBits", "wCurCaller", "wPokegearPhoneCursorPosition", "wPokegearPhoneScrollPosition",
    "wPokegearPhoneSelectedPerson", "wPokegearPhoneSubmenuCursor", "PokegearPhoneContactSubmenu", "PokegearPhoneContactSubmenu.Delete",
    "MakePhoneCallFromPokegear", "SlinkPhone_CallGate", "SlinkPhone_CallerName", "SlinkPhone_CanDelete",
    "SlinkPhone_CountSetBits", "wSlinkMailbox", "wSlinkMailboxEnd", "wSlinkPanelText",
    # round 4: without these, L.hook("SlinkPanel") asserted at load and three bisects died
    # one line after the boot log -- a harness defect that looked exactly like a boot failure.
    "SlinkPanel", "SlinkPanelEnd", "SlinkPanelFallback",
)

RELEASE = pathlib.Path("F:/slink-work/cache/polished/release/polishedcrystal-3.2.3.gbc")
DEFAULT_UPS = REPO / "patch/dist/SLink-Polished.ups"


def main() -> int:
    from dataclasses import replace

    from tools.polished_live.runtime import stage_pinned_rom
    env = dict(os.environ)
    env.setdefault("POL_LANE", "F:/slink-work/lanes/pol-panel2")
    env.setdefault("POL_FIXTURE", "F:/slink-work/lanes/g2int-live/pol/fixture/polished_overlay_warp.SaveRAM")
    config = H.launch_config(env)
    config = replace(config, rom=config.lane / "rom" / env.get("POL_ROM_OUT", "pol_overlay.gbc"),
                     symbols=tuple(dict.fromkeys((*config.symbols, *EXTRA_SYMBOLS))))
    ups = pathlib.Path(env.get("POL_ROM_UPS", str(DEFAULT_UPS)))
    # Deliberate A/B cartridges may differ from production; pin their exact UPS output.
    data = ups_apply(RELEASE.read_bytes(), ups.read_bytes())
    sha1 = hashlib.sha1(data).hexdigest()
    stage_pinned_rom(RELEASE, config.rom, sha1, patch=ups)
    H.prepare_save(config, clear=True)
    run = config.lane / env.get("POL_RUNNAME", "phone_ab")
    text, pid = H.launch(config, env.get("POL_DRIVER", "tools/polished_live/phone.lua"), run, {}, 900)
    print(f"sha1 {sha1} pid {pid}")
    print(text[-6000:])
    (run / "rom_sha1.txt").write_text(sha1 + "\n", encoding="utf-8")
    return 0 if "RESULT: PASS" in text else 1


if __name__ == "__main__":
    sys.exit(main())
