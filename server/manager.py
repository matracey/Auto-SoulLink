"""
server/manager.py — SLink Run Manager

Standalone aiohttp process (default port 8090) that manages named Soul Link
runs, each running as its own server.py subprocess with an isolated data dir.

Usage:
    python -m server.manager
    python -m server.manager --host 127.0.0.1 --port 8090

Registry:  data/runs/registry.json
Run dirs:  data/runs/<run_id>/links.json
           data/runs/<run_id>/memorial.json
"""

import argparse
import asyncio
import contextlib
import hashlib
import html
import json
import logging
import os
import pathlib
import re
import secrets
import shutil
import signal
import stat
import sys
import tempfile
import time
from datetime import UTC, datetime
from urllib.parse import quote

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False

try:
    import aiohttp
    from aiohttp import web
    AIOHTTP_AVAILABLE = True
except ImportError:
    AIOHTTP_AVAILABLE = False
    print("ERROR: aiohttp required. Run: pip install aiohttp", file=sys.stderr)
    sys.exit(1)

import aiohttp_jinja2

from server import calc_files
from server.admin_auth import ADMIN_KEY_ENV, setup_admin_auth
from server.health import writable_directory
from server.http_safety import allow_hosts, csrf_protection, theme_cache
from server.json_files import atomic_write_json
from server.lua_literals import lua_comment, lua_string
from server.overlay_catalog import build_index_context as _build_stream_index_context
from server.status_payload import empty_status_payload
from server.templating import resolve_theme, setup_templating
from tools import make_release

# ── Game families ─────────────────────────────────────────────────────────────
# The unit of link compatibility is the FAMILY, not the cartridge: Red, Blue and Yellow
# share an adapter and an area map, so any two of them can link; the same holds for
# FireRed/LeafGreen, Gold/Silver/Crystal, HeartGold/SoulSilver, Black/White. What
# separates families is a different map (Radical Red from vanilla FireRed, Emerald,
# Platinum from HGSS, B2W2 from BW). The Archipelago builds are not listed: no SLink client
# supports them yet (Gen 1 has no AP profile, the Gen 3 launcher refuses a header-only build).
# Every new run names its family up front, so the Manager knows which cartridge (and which
# companion) to hand out. Runs created before 2026-10-01 may still carry "" (detected).
GAMES = [
    ("gen1", "Red · Blue · Yellow", ["red", "blue", "yellow"]),
    ("gen1_purergb", "PureRed · PureBlue · PureGreen", ["purered", "pureblue", "puregreen"]),
    ("gen2", "Gold · Silver · Crystal", ["gold", "silver", "crystal"]),
    ("gen2_polished", "Polished Crystal", ["polished_crystal"]),
    ("gen3", "FireRed · LeafGreen", ["firered", "leafgreen"]),
    ("gen3_rr", "Radical Red", ["firered_rr"]),
    ("gen3_e", "Emerald", ["emerald"]),
    ("gen3_exp", "Emerald Expansion", ["emerald_expansion_28877d73"]),
]
# Listed but not admitted by the client: shown in the Manager with a "not admitted" label, never created. Empty
# since the Archipelago games left the list (owner 2026-10-02), the seam stays for the next one.
UNADMITTED_GAMES: frozenset[str] = frozenset()
GAME_LABELS = {key: label for key, label, _ in GAMES}
GAME_MEMBERS = {key: members for key, _, members in GAMES}
# The randomizer contract a run's game names (upr_settings.FAMILY_*): a pure run takes pure
# cartridges only, a vanilla run vanilla ones, a FireRed / LeafGreen run FR/LG ones, a Gen 2 run
# Gen 2 ones (companion, randomized or not) -- no two families can link. "gen3_rr"
# (Radical Red) is a DIFFERENT game key from "gen3" and is deliberately absent here: RR is not
# randomizable by this pipeline (its map/data no longer matches the vanilla FR/LG tables R2
# verifies against), so a run named "gen3_rr" never lands in randomizer_games and never offers
# the randomizer -- see test_manager_names_the_frlg_family / the RR refusal test in
# test_upr_pipeline_gen3.py.
GAME_FAMILY = {"gen1": "gen1_rby", "gen1_purergb": "gen1_purergb",
               "gen2": "gen2_gsc", "gen2_polished": "gen2_polished", "gen3": "gen3_frlg", "gen3_e": "gen3_emerald"}
FAMILY_WORDS = {"gen1_rby": "vanilla Red / Blue / Yellow", "gen1_purergb": "pureRGB",
                "gen2_gsc": "Gold / Silver / Crystal", "gen2_polished": "Polished Crystal",
                "gen3_frlg": "FireRed / LeafGreen", "gen3_emerald": "Emerald",
                "gen3_exp": "Emerald Expansion"}
# Owner ruling 37 (2026-09-27): a Radical Red run cannot be randomized at all. It is absent
# from GAME_FAMILY above, which the UI honours (randomizer_games), but _game_family() then
# answers None and `handle_cartridges` SKIPPED its "this run is X; these are Y cartridges"
# refusal -- so a Radical Red run accepted a randomization request, ran Java, and recorded
# randomized cartridges. The table makes the refusal explicit and by name instead of relying
# on the absence that caused it. Randomized FireRed / LeafGreen / Emerald are untouched.
NON_RANDOMIZABLE_GAMES = {
    "gen3_rr": ("Randomized Radical Red is not supported in this release; "
                "the randomizer supports FireRed / LeafGreen / Emerald"),
    # Also absent from GAME_FAMILY on purpose: the creator feeds GAME_FAMILY[game] to the
    # randomizer form as its family, and "gen3_exp" is not one of upr_settings.FAMILIES.
    "gen3_exp": ("The Emerald Expansion has no randomizer and no companion patch -- it is a prebuilt "
                 "reference ROM; the randomizer supports FireRed / LeafGreen / Emerald"),
}


def _game_family(game: str | None) -> str | None:
    return GAME_FAMILY.get(game or "")


def _rom_ext(run: dict) -> dict:
    """Each player's cartridge extension as handed out (.gb until one is made): the download
    labels name the file the player will load, and BizHawk picks the system by it."""
    players = (run.get("cartridges") or {}).get("players") or {}
    return {p: (os.path.splitext(players.get(p, {}).get("output", ""))[1].lower() or ".gb") for p in ("a", "b")}


def _content_disposition(filename: str) -> str:
    """A Content-Disposition header value that survives a non-ASCII run name.

    `safe_name` sanitizes with `\\w`, which is Unicode by default -- a name like "日本語"
    keeps its letters and would otherwise be written straight into a header and either
    crash the encoder or arrive mangled. Add the RFC 8187 UTF-8 form only when the plain
    ASCII form is not already exact, so an ordinary name's header is unchanged.
    """
    try:
        filename.encode("ascii")
        return f'attachment; filename="{filename}"'
    except UnicodeEncodeError:
        ascii_name = filename.encode("ascii", "replace").decode("ascii").replace("?", "_")
        return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename, safe='')}"


def _legacy_cartridges(run: dict) -> dict | None:
    """A run randomized before the Cartridges step recorded only `randomizer`: the same
    shape for the page, from what it has (the randomizer's outputs are the cartridges)."""
    rnd = run.get("randomizer")
    if not rnd:
        return None
    return {"family": "", "companion": False, "randomizer": None, "legacy": True,
            "players": {p: {"source": "", "source_title": "randomized cartridge",
                            "output": v.get("output", ""), "rom_sha1": v.get("rom_sha1", ""),
                            "fingerprint": "", "kind": "rand"}
                        for p, v in (rnd.get("players") or {}).items()}}



# Run options: what each does, in the form's own words, and which cartridges can honour
# it. Reasons are shown on the option that is greyed, so "off" and "impossible" look
# different. Keyed by game_id, with a "_rr" suffix for the Radical Red build of Gen 3.
OPTION_GROUPS = [
    ("Encounter", ["retry_same_species"]),
    ("Link clauses", ["species_lock", "gender_lock", "type_lock"]),
    ("Battle", ["ignore_wild_faints", "explode_mode", "rival_team_swap", "overworld_presence"]),
    ("Native UI", ["native_messages", "native_sounds", "phone_calls", "battle_calc", "pc_trade_npc"]),
]
OPTIONS = {
    "retry_same_species": ("Retry First Species", "A failed first encounter stays open, but only that species may be caught."),
    "species_lock": ("Species Clause", "Reject links where both mons are in the same evolution family."),
    "gender_lock": ("Gender Clause", "Reject links where both mons share a gender."),
    "type_lock": ("Type Clause", "Reject links where both mons share any type."),
    "ignore_wild_faints": ("Ignore Wild Faints", "Wild-battle faints and whiteouts do not retire linked pairs."),
    "explode_mode": ("Explode Mode", "On a partner's death, force the linked mon to auto-Explode."),
    "rival_team_swap": ("Rival Swap", "Rival battles load your partner's exact team instead of the canned one."),
    "overworld_presence": ("Overworld Presence", "See your partner walking in your overworld as a live peer ghost."),
    "native_messages": ("Native Messages", "Notifications as native in-game text boxes instead of the Lua HUD overlay."),
    "native_sounds": ("Native Sounds", "Notification sounds through the game's own audio engine (needs the companion patch or pureRGB overlay on that cartridge — inert on an unpatched one)."),
    "phone_calls": ("Phone Calls", "Your partner rings your Pokégear when a pair links, an area dies or a mon falls (Gen 2 companion; the HUD pop-up always shows too)."),
    "battle_calc": ("Battle Calc", "The bundled in-battle damage and type-effectiveness calculator."),
    "pc_trade_npc": ("PC Trade NPC", "A Pokémon-Center trade NPC, when Overworld Presence is off."),
}
OPTION_SUPPORT = {
    "retry_same_species": {"all": True},
    "species_lock": {"all": True},
    "gender_lock": {"all": True,
                    "gen1_rby": {"ok": False, "why": "Gen 1 has no gender mechanic, so the clause can never fire."},
                    "gen1_purergb": {"ok": False, "why": "pureRGB has no gender mechanic, so the clause can never fire."}},
    "type_lock": {"all": True},
    "ignore_wild_faints": {"all": True},
    "explode_mode": {"all": False, "why": "Explode Mode is not supported for this cartridge.",
                     "rom_types": {title: {"ok": True} for title in ("firered", "leafgreen", "emerald")},
                     "gen1_rby": {"ok": True, "why": "No patch needed — Explosion is move 153 and the choice is a plain RAM write."},
                     "gen1_purergb": {"ok": True, "why": "No patch needed — Explosion is a plain RAM write, same as vanilla Gen 1."},
                     "gen2_gsc": {"ok": True},
                     "gen2_polished": {"ok": True, "why": "Needs the companion patch on both players' games; includes the plain-faint fallback."},
                     "gen3_frlge_rr": {"ok": True},
                     "gen3_exp": {"ok": False, "why": "Explode Mode is not supported on the Emerald Expansion."}},
    "rival_team_swap": {"all": False, "why": "Needs the companion patch — gEnemyParty is encrypted.",
                        # Gen3Adapter.rival_trainer_ids() lists Radical Red's rivals only, so the swap never fires here
                        "rom_types": {title: {"ok": False, "why": "Not available on FireRed, LeafGreen or Emerald yet."}
                                      for title in ("firered", "leafgreen", "emerald")},
                        "gen1_rby": {"ok": True, "why": "No patch needed — the Gen 1 enemy party is plaintext."},
                        "gen1_purergb": {"ok": True, "why": "No patch needed — pureRGB's enemy party is plaintext, same as vanilla Gen 1."},
                        "gen2_gsc": {"ok": True},
                        "gen2_polished": {"ok": True, "why": "Needs the companion patch on both players' games; RIVAL0, RIVAL1 and RIVAL2 only."},
                        "gen3_frlge_rr": {"ok": True},
                        "gen3_exp": {"ok": False, "why": "Needs the companion patch (gEnemyParty is encrypted), and the Emerald Expansion has none."}},
    "overworld_presence": {"all": False, "why": "Not available yet."},   # deferred post-RC, docs/gen3/TODO.md
    "native_messages": {"all": False, "why": "Not available yet."},   # deferred post-RC, docs/gen3/TODO.md
    "native_sounds": {"all": False, "why": "Needs a companion patch with a native sound path (Radical Red, Gen 1 Red/Blue, pureRGB, Gen 2 Gold/Silver/Crystal, FireRed/LeafGreen/Emerald).",
                      "rom_types": {title:{"ok":True} for title in ("firered","leafgreen","emerald")},
                      "gen1_rby": {"ok": True},
                      "gen1_purergb": {"ok": True},
                      "gen2_gsc": {"ok": True},
                      "gen2_polished": {"ok": True},   # POL-SOUNDS: the overlay's own sound service (patch/polished/src/slink_sfx.asm) plays the shared semantic codes through Polished's PlaySFX
                      "gen3_frlge_rr": {"ok": True},
                      "gen3_exp": {"ok": False, "why": "Needs a companion patch with a native sound path, and the Emerald Expansion has none."}},
    "phone_calls": {"all": False, "why": "Only Gen 2 has a Pokégear phone for the companion to ring.",
                    "gen2_gsc": {"ok": True},
                    "gen2_polished": {"ok": False, "why": "Polished Crystal has no companion call transport yet; its Pokégear Phone entry opens the SoulLink panel instead of ringing."}},
    "battle_calc": {"all": False, "why": "Radical Red only.",
                    "gen1_rby": {"ok": False, "why": "The calculator is pinned to modern mechanics and would misreport Gen 1 damage."},
                    "gen1_purergb": {"ok": False, "why": "The calculator is pinned to modern mechanics and would misreport pureRGB's retyped/rebalanced damage."},
                    "gen2_gsc": {"ok": False, "why": "The calculator is pinned to modern mechanics and would misreport Gen 2 damage."},
                    # Flipped 2026-10-04 with calc/src/calc/data/polished.js (tools/gen_polished_calc.py):
                    # gen 3 mechanics on Polished's own dex. Known limits are enumerated in
                    # docs/polished/CALC.md §4 rather than hidden here.
                    "gen2_polished": {"ok": True, "why": "Gen 3 mechanics on Polished's own dex (species, moves, Fairy-era type chart). Abilities, held-item boosts and natures are not modelled - docs/polished/CALC.md."},
                    "gen3_frlge_rr": {"ok": True},
                    "gen3_exp": {"ok": True}},
    # `always`: the cartridge trades this way whether or not the switch is on -- the form
    # shows the row greyed AND checked, so it does not read as "no trade NPC here".
    "pc_trade_npc": {"all": False, "why": "This switch turns off Radical Red's Pokémon-Center trade NPC — other games have no NPC it could turn off.",
                     "rom_types": {title:{"ok":True} for title in ("firered","leafgreen","emerald")},
                     "gen1_rby": {"ok": False, "always": True, "why": "Gen 1 trades at the Pokémon Center's Cable Club receptionist, which the companion patch makes the cartridge's own counter (a cartridge without it has no trade). Always on, nothing to switch off."},
                     "gen1_purergb": {"ok": False, "always": True, "why": "pureRGB trades at the Pokémon Center's Cable Club receptionist, which the companion overlay makes the cartridge's own counter (a cartridge without it has no trade). Always on, nothing to switch off."},
                     "gen2_gsc": {"ok": False, "always": True, "why": "Gen 2 trades at the Pokémon Center's Cable Club receptionist, which the companion patch makes the cartridge's own counter (a cartridge without it has no trade). Always on, nothing to switch off."},
                     "gen2_polished": {"ok": False, "always": True, "why": "Polished Crystal trades at the Pokémon Center's Cable Club receptionist, which the companion patch makes the cartridge's own counter (a cartridge without it has no trade). Always on, nothing to switch off."},
                     "gen3_frlge_rr": {"ok": True},
                     "gen3_exp": {"ok": False, "why": "The Emerald Expansion has no companion patch, so no Pokémon-Center trade NPC to switch off."}},
}


# The run options a registry entry carries and how each reaches the spawned server: the
# registry key, the CLI flag that turns it AWAY from its default, and the default. Written
# out by hand at four sites before, and they had drifted: handle_new stored `verbose`,
# _adopt_orphans did not, so an adopted run could never be started verbose.
RUN_FLAGS = (
    ("retry_same_species", "--dead-zone-on-miss", True),
    ("species_lock", "--species-clause", False),
    ("gender_lock", "--gender-clause", False),
    ("type_lock", "--type-clause", False),
    ("ignore_wild_faints", "--ignore-wild-faints", False),
    ("explode_mode", "--explode-mode", False),
    ("rival_team_swap", "--rival-team-swap", False),
    ("overworld_presence", "--overworld-presence", False),
    ("native_messages", "--native-messages", False),
    ("native_sounds", "--native-sounds", False),
    ("battle_calc", "--no-battle-calc", True),
    ("pc_trade_npc", "--no-pc-trade-npc", True),
    ("phone_calls", "--no-phone-calls", True),
    ("verbose", "--verbose", False),
)


def run_options(source: dict) -> dict:
    """The option fields of a registry entry, read from a request body or a run_meta."""
    return {key: bool(source.get(key, default)) for key, _, default in RUN_FLAGS}


def run_flags(run: dict) -> list[str]:
    """The CLI flags for this run's options: one per option whose value is not the default."""
    return [flag for key, flag, default in RUN_FLAGS if bool(run.get(key, default)) != default]


def option_support(key: str, rom_types: list[str]) -> dict:
    """Can a run on these cartridges honour this option, and if not, why. A soul link is
    symmetric, so the more restrictive answer across the pair wins. An unknown rom_type
    means "not known yet", and nothing is known to be impossible."""
    from server.adapters import game_id_for_rom_type
    rule = OPTION_SUPPORT.get(key, {"all": True})
    ok, why = True, ""
    for rt in rom_types:
        gid = game_id_for_rom_type(rt) if rt else None
        if not gid:
            continue
        specific = (rule.get("rom_types", {}).get(rt)
                    or rule.get(gid + ("_rr" if rt.endswith("_rr") else "")) or rule.get(gid))
        decided = specific["ok"] if specific else rule["all"]
        if not decided:
            return {"ok": False, "why": (specific or {}).get("why") or rule.get("why", ""),
                    "always": bool((specific or {}).get("always"))}
        if specific and specific.get("why") and not why:
            why = specific["why"]
    return {"ok": ok, "why": why}


def _calc_profile_for_run(run: dict, status: dict) -> dict | None:
    """The web calc's profile for a run, or None when it should stay hidden.

    The Manager has no live adapter instance of its own (each run is a separate process),
    so this instantiates the same adapters `server.py` would and asks calc_profile(), the
    way `SLinkServer._calc_profile` compares the two players' adapters. Prefers each
    connected player's own rom_type (a status payload's `players[pid]['rom_type']`); a
    player who has not said hello yet falls back to the run's declared game family
    (`GAMES`/`GAME_MEMBERS` above) — a soul link only ever pairs cartridges from the same
    family, so one representative rom_type stands for both. None when nothing is known
    yet, either side's game is unverified, or their rules (gen + dex) disagree.
    """
    from server.adapters import game_id_for_rom_type, get_adapter, shared_calc_profile
    # A live server can answer anything; a bad status must hide the calc, not 500 the board.
    players = (status.get("players") if isinstance(status, dict) else None) or {}
    # Unrecognized rom_types ("" or a persisted "?") count as not-yet-known.
    rom_types = [rt for rt in ((players.get(pid) or {}).get("rom_type") or "" for pid in ("a", "b"))
                 if game_id_for_rom_type(rt)]
    if not rom_types:
        members = GAME_MEMBERS.get(run.get("game") or "", [])
        if not members:
            return None
        rom_types = [members[0]]
    profiles = []
    for rom_type in rom_types:
        gid = game_id_for_rom_type(rom_type)
        if not gid:
            return None
        try:
            # rom_type picks the title for the per-title packs (Gen 2 refuses to guess one).
            profiles.append(get_adapter(gid, is_rr=rom_type.endswith("_rr"), rom_type=rom_type).calc_profile())
        except (KeyError, ValueError):  # an unregistered family (e.g. Gen 5 import) or a refused title
            return None
    return shared_calc_profile(profiles)


def new_run_form() -> dict:
    """Everything the New-run form needs, computed here so the reasons and the greying
    come from one table: per game family, per option, (ok, why)."""
    return {
        "games": [{"key": k, "label": lbl, "members": m, "unadmitted": k in UNADMITTED_GAMES}
                  for k, lbl, m in GAMES],
        "groups": [{"label": lbl, "keys": keys} for lbl, keys in OPTION_GROUPS],
        "options": {k: {"label": lbl, "desc": d} for k, (lbl, d) in OPTIONS.items()},
        "support": {k: {opt: option_support(opt, m or [""]) for opt in OPTIONS} for k, _, m in GAMES},
        "gen1_games": [k for k, _, m in GAMES if m and all(
            rt in ("red", "blue", "yellow", "purered", "pureblue", "puregreen") for rt in m)],
        # the games the Cartridges step (companion / randomizer) serves: Gen 1, Gen 2 and FR/LG
        "randomizer_games": [k for k, _, _m in GAMES if k in GAME_FAMILY and k not in NON_RANDOMIZABLE_GAMES],
    }


def _companion_titles() -> tuple:
    from server.cartridges import COMPANION_TITLES  # lazy: provisioning pulls the patch tools
    return COMPANION_TITLES


def _json_for_script(obj) -> str:
    """json.dumps, safe to embed directly in a <script> element.

    `<`, `>` and `&` become unicode escapes: still valid JSON, still the same string
    once parsed, but no longer able to close the script element they sit inside.
    """
    return (json.dumps(obj)
            .replace("<", "\\u003c").replace(">", "\\u003e")
            .replace("&", "\\u0026"))

log = logging.getLogger("slink.manager")

# ── Paths ───────────────────────────────────────────────────────────────────
PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))
MANAGER_DIR  = os.path.join(PROJECT_ROOT, "data", "runs")
# Where the run creator looks for cartridges: the SLink folder, roms/ (where a ROM picked
# in the browser lands), patch/build/ (the companion builds) and the .cache/ folders the
# pureRGB tooling builds the pinned pure cartridges and their overlays into -- a git
# worktree has no .cache of its own, so it is looked for upward like find_upr_jar does.
# .gitignore already refuses every *.gb / *.gbc / *.gba anywhere in the repo.
ROM_UPLOAD_DIR = os.environ.get("SLINK_ROM_DIR") or os.path.join(PROJECT_ROOT, "roms")


def _cache_rom_dirs() -> list[str]:
    out, d = [], os.path.normpath(PROJECT_ROOT)
    for _ in range(6):
        out += [c for sub in ("purergb", "purergb-overlay-staged")
                if os.path.isdir(c := os.path.join(d, ".cache", sub))]
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    # Polished Crystal: the pinned release ROM tools/build_polished_companion.py caches under the work root.
    # P5: add the overlay build's output folder once the companion card lands.
    from tools.slink_space import DEFAULT_WORK_ROOT
    polished = os.path.join(os.environ.get("SLINK_WORK_ROOT") or DEFAULT_WORK_ROOT, "cache", "polished", "release")
    return out + [polished] if os.path.isdir(polished) else out


ROM_DIRS = (PROJECT_ROOT, ROM_UPLOAD_DIR, os.path.join(PROJECT_ROOT, "patch", "build"), *_cache_rom_dirs())
ROM_EXTS = (".gb", ".gbc", ".gba")
UPLOAD_MAX = 64 << 20
# path -> ((size, mtime_ns), describe_rom result): the ROM scan runs on every page load and
# /api/roms call, over Google Drive; an unchanged file is not read again.
# ponytail: never evicted -- a handful of ROM files; a deleted path just goes unreferenced.
_ROM_INFO_CACHE: dict[str, tuple[tuple[int, int], dict]] = {}
REGISTRY_PATH = os.path.join(MANAGER_DIR, "registry.json")

# A freshly spawned server counts as up only once its HTTP port answers /api/status; imports
# alone can take seconds on a slow (Google Drive) disk, so being alive is not being ready.
SPAWN_READY_S = 20.0
SPAWN_POLL_S = 0.25
# Reserved port for the manager itself
MANAGER_HTTP_PORT = 8090
# Port ranges for spawned runs
TCP_PORT_BASE  = 54321
HTTP_PORT_BASE = 8081   # 8090 reserved for manager
TCP_PORT_POOL_ENV = "SLINK_TCP_PORT_POOL"

# Schema-compatible empty /api/status returned when no run is active.
_EMPTY_STATUS: dict = empty_status_payload()


# ── Registry helpers ────────────────────────────────────────────────────────

class RegistryError(RuntimeError):
    """The registry must be repaired before the Manager can change runs."""


@web.middleware
async def registry_errors(request: web.Request, handler):
    """Surface a damaged registry without replacing it or hiding every run."""
    try:
        return await handler(request)
    except RegistryError as exc:
        message = str(exc) + ". Restore or repair registry.json, then retry."
        if request.path.startswith("/api/"):
            return web.json_response({"ok": False, "error": message}, status=503,
                                     headers={"Cache-Control": "no-store"})
        return web.Response(
            text="<!doctype html><title>Run registry unavailable</title>"
                 "<h1>Run registry unavailable</h1><p>" + html.escape(message) + "</p>",
            status=503, content_type="text/html", headers={"Cache-Control": "no-store"},
        )


def _registry_runs(document) -> list[dict]:
    if not isinstance(document, dict) or not isinstance(document.get("runs"), list):
        raise ValueError("expected an object containing a runs list")
    runs = document["runs"]
    seen = set()
    for run in runs:
        if not isinstance(run, dict) or not isinstance(run.get("run_id"), str) or not run["run_id"]:
            raise ValueError("every run must have a non-empty run_id")
        if run["run_id"] in seen:
            raise ValueError("duplicate run_id")
        seen.add(run["run_id"])
    return runs


def _load_registry() -> list[dict]:
    try:
        with open(REGISTRY_PATH, encoding="utf-8") as f:
            return _registry_runs(json.load(f))
    except FileNotFoundError:
        return []
    except (ValueError, OSError) as exc:
        message = f"Run registry could not be read; preserved {REGISTRY_PATH}: {exc}"
        log.error(message)
        raise RegistryError(message) from exc


def _save_registry(runs: list[dict]):
    # A file may have become unreadable since the caller's last load (including
    # across a subprocess await). Never replace that evidence with a fresh list.
    _load_registry()
    document = {"runs": runs}
    _registry_runs(document)
    atomic_write_json(REGISTRY_PATH, document)


# ── Randomizer presets: a named spec, kept on this Manager beside the registry ────────
def _presets_path() -> str:
    return os.path.join(MANAGER_DIR, "presets.json")


def _load_presets() -> list[dict]:
    try:
        with open(_presets_path(), encoding="utf-8") as f:
            return [p for p in json.load(f).get("presets", []) if isinstance(p, dict) and p.get("name")]
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as exc:
        raise RegistryError(f"presets.json is unreadable: {exc}") from exc


def _save_presets(presets: list[dict]) -> None:
    atomic_write_json(_presets_path(), {"presets": sorted(presets, key=lambda p: p["name"].lower())})


def _committed_artifact_kind(run_id: str) -> str:
    """The pairing kind the run's players committed it to (links.json, set at the first hello), or ''."""
    try:
        with open(os.path.join(MANAGER_DIR, run_id, "links.json"), encoding="utf-8") as fh:
            kind = json.load(fh).get("artifact_kind")
    except (OSError, ValueError, AttributeError):
        return ""
    return kind if isinstance(kind, str) else ""


def _update_run(run_id: str, **fields) -> dict | None:
    """Re-read, patch one run, save. Handlers that awaited between their read and their
    write (start: spawn; new: spawn) used to write a stale snapshot over whatever the
    2 s overlay polls had reconciled in the meantime."""
    runs = _load_registry()
    run = _find_run(runs, run_id)
    if run is not None:
        run.update(fields)
        _save_registry(runs)
    return run


def _find_run(runs: list[dict], run_id: str) -> dict | None:
    for r in runs:
        if r["run_id"] == run_id:
            return r
    return None


def _ensure_instrumentation_keys(run: dict) -> bool:
    keys = run.get("instrumentation_keys")
    if not isinstance(keys, dict):
        keys = {}
    changed = False
    for player in ("a", "b"):
        if not isinstance(keys.get(player), str) or not keys[player]:
            keys[player] = secrets.token_urlsafe(32)
            changed = True
    if changed or run.get("instrumentation_keys") is not keys:
        run["instrumentation_keys"] = keys
        changed = True
    return changed


def _ensure_desired_status(run: dict) -> bool:
    if run.get("desired_status") in {"running", "stopped", "archived"}:
        return False
    status = run.get("status")
    run["desired_status"] = status if status in {"running", "archived"} else "stopped"
    return True


def _public_run(run: dict) -> dict:
    return {key: value for key, value in run.items() if key != "instrumentation_keys"}


def _internal_admin_headers(extra: dict | None = None) -> dict:
    headers = dict(extra or {})
    key = os.environ.get(ADMIN_KEY_ENV, "")
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return headers


def _port_free(port: int) -> bool:
    """Whether this machine will let a server bind the port right now. The registry only
    knows this Manager's runs; a hand-started server, another Manager's data dir, or any
    other program can hold a port the registry thinks is free."""
    import socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False


def _tcp_port_pool() -> tuple[int, ...]:
    raw = os.environ.get(TCP_PORT_POOL_ENV, "").strip()
    if not raw:
        return ()
    try:
        ports = tuple(int(value.strip()) for value in raw.split(",") if value.strip())
    except ValueError as exc:
        raise RuntimeError(f"{TCP_PORT_POOL_ENV} must contain comma-separated ports") from exc
    if not ports or len(set(ports)) != len(ports) or any(not 1 <= port <= 65535 for port in ports):
        raise RuntimeError(f"{TCP_PORT_POOL_ENV} must contain unique ports from 1 to 65535")
    return ports


def _next_ports(runs: list[dict]) -> tuple[int, int]:
    """Return the next available (tcp_port, http_port) pair: unused by this registry AND
    bindable on this machine."""
    # .get: a hand-edited or old entry without ports must not take every page down with a KeyError
    pool = _tcp_port_pool()
    active = [run for run in runs if run.get("status") in {"running", "starting"}]
    used_tcp = {r.get("tcp_port") for r in (active if pool else runs)}
    used_http = {r.get("http_port") for r in (active if pool else runs)}
    if pool:
        tcp = next((port for port in pool if port not in used_tcp and _port_free(port)), None)
        if tcp is None:
            raise RuntimeError(f"All {len(pool)} configured game ports are in use")
    else:
        tcp = TCP_PORT_BASE
        while tcp in used_tcp or not _port_free(tcp):
            tcp += 1
    http = HTTP_PORT_BASE
    while http in used_http or http == MANAGER_HTTP_PORT or not _port_free(http):
        http += 1
    return tcp, http


def _released_ports() -> dict:
    # Keep the last lease as this run's preferred address. Cloud allocation ignores
    # stopped runs, so another active run may borrow it; a later restart reuses it
    # when free and safely falls back when occupied.
    return {}


def _preferred_cloud_ports(run: dict, runs: list[dict]) -> tuple[int, int] | None:
    pool = _tcp_port_pool()
    if not pool:
        return None
    tcp, preferred_http = run.get("tcp_port"), run.get("http_port")
    if tcp not in pool:
        return None
    active_others = [
        other for other in runs
        if other.get("run_id") != run.get("run_id")
        and other.get("status") in {"running", "starting"}
    ]
    if tcp in {other.get("tcp_port") for other in active_others}:
        return None
    if not _port_free(tcp):
        return None
    used_http = {other.get("http_port") for other in active_others}
    if (isinstance(preferred_http, int) and preferred_http != MANAGER_HTTP_PORT
            and preferred_http not in used_http and _port_free(preferred_http)):
        return tcp, preferred_http
    http = HTTP_PORT_BASE
    while http in used_http or http == MANAGER_HTTP_PORT or not _port_free(http):
        http += 1
    return tcp, http


# ── Launcher script generation (dynamic — served via HTTP) ──────────────────

_LAUNCHER_TEMPLATE = """\
-- Auto-generated by SLink Run Manager - {run_name}
-- Player {player_upper}: load this script in BizHawk Lua Console
-- This file can be loaded from any location (Desktop, Downloads, etc.)
--
-- Override: set SLINK_ROOT to skip auto-detection entirely:
local SLINK_ROOT = nil  -- e.g. "C:/SLink/"

SLINK_HOST   = {host}
SLINK_PORT   = {tcp_port}
SLINK_PLAYER = "{player}"
SLINK_INSTRUMENTATION_KEY = {instrumentation_key}

-- Config file lives next to this launcher and caches the project root path.
local _launcher_dir = ((debug.getinfo(1, "S") or {{}}).source or ""):match("@(.+[\\\\/])") or ""
local _cfg_path = _launcher_dir .. "slink_path.cfg"

local function _valid_root(path)
    if not path or path == "" or path == "nil" then return false end
    local f = io.open(path .. "lua/slink.lua", "r")
    if f then f:close(); return true end
    return false
end

-- 1. Prefer the runtime shipped beside this launcher. A stale slink_path.cfg
-- must never redirect a freshly downloaded player pack to an older connector.
if not SLINK_ROOT then
    local dir = _launcher_dir
    for _, rel in ipairs({{"", "../", "../../", "../../../"}}) do
        if _valid_root(dir .. rel) then SLINK_ROOT = dir .. rel; break end
    end
end

-- 2. Fall back to a previously selected standalone project folder.
if not SLINK_ROOT then
    local f = io.open(_cfg_path, "r")
    if f then
        local cached = f:read("*l"); f:close()
        if _valid_root(cached) then SLINK_ROOT = cached end
    end
end

-- 3. Fallback: show modern folder picker (OpenFileDialog trick)
if not SLINK_ROOT then
    luanet.load_assembly("System.Windows.Forms")
    local OFD = luanet.import_type("System.Windows.Forms.OpenFileDialog")
    local Path = luanet.import_type("System.IO.Path")
    local DR = luanet.import_type("System.Windows.Forms.DialogResult")
    local dlg = OFD()
    dlg.Title = "Select the SLink project folder (contains lua/ and server/)"
    dlg.ValidateNames = false
    dlg.CheckFileExists = false
    dlg.CheckPathExists = true
    dlg.FileName = "Select This Folder"
    local result = dlg:ShowDialog()
    if result == DR.OK then
        local path = Path.GetDirectoryName(dlg.FileName)
        if path and tostring(path) ~= "" then
            SLINK_ROOT = tostring(path):gsub("\\\\", "/") .. "/"
        end
    end
end

if not SLINK_ROOT then
    error("[SLink] No project folder selected — cannot start.", 2)
end

-- Save path for next run
local f = io.open(_cfg_path, "w")
if f then f:write(SLINK_ROOT); f:close() end

dofile(SLINK_ROOT .. "lua/slink.lua")
"""


def _build_launcher(run: dict, player: str, host: str) -> str:
    """Return launcher Lua source with the given connect host."""
    return _LAUNCHER_TEMPLATE.format(
        run_name=lua_comment(str(run.get("name") or run["run_id"])),
        player_upper=player.upper(),
        host=lua_string(host),
        tcp_port=run["tcp_port"],
        player=player,
        instrumentation_key=lua_string(run.get("instrumentation_keys", {}).get(player, "")),
    )


# ── The address players connect to ──────────────────────────────────────────
# The browser's Host header is the wrong source: a page opened as localhost, or through a
# tunnel/proxy, handed player B a launcher pointing at 127.0.0.1 or at a name that only
# forwards HTTP. The game TCP port is on this machine, so the answer is this machine's.
_HOST_RE = re.compile(r"[A-Za-z0-9.:\-\[\]]{1,253}")
_LOOPBACK = ("127.", "localhost", "::1")


def _lan_address() -> str | None:
    """The address of the interface that holds the default route, or None offline. A UDP
    connect only picks a route; no packet is sent (192.0.2.1 is TEST-NET-1)."""
    import socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))
            ip = s.getsockname()[0]
    except OSError:
        return None
    return None if ip.startswith(_LOOPBACK) or ip == "0.0.0.0" else ip


def advertised_host(setting: str, bind_host: str, lan=_lan_address) -> tuple[str, str]:
    """(address, source) players connect to. In order: what the host set (even loopback:
    it is their call); the one address a Manager bound to a specific interface listens on;
    127.0.0.1 for a loopback-bound Manager (nobody else can connect); the LAN address;
    127.0.0.1 when there is no network at all."""
    if setting:
        return setting, "set"
    if bind_host.startswith(_LOOPBACK):
        return "127.0.0.1", "loopback"
    if bind_host not in ("", "0.0.0.0", "::"):
        return bind_host, "bind"
    ip = lan()
    return (ip, "lan") if ip else ("127.0.0.1", "none")


def _settings_path() -> str:
    return os.path.join(MANAGER_DIR, "settings.json")


def _load_settings() -> dict:
    try:
        with open(_settings_path(), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


# ── Subprocess management ───────────────────────────────────────────────────

def _create_time(pid: int) -> float | None:
    """The process's start time, recorded beside its pid so a reused pid is never trusted."""
    if not PSUTIL_AVAILABLE:
        return None
    try:
        return psutil.Process(pid).create_time()
    except psutil.Error:
        return None


def _is_alive(pid: int | None, created: float | None = None) -> bool:
    """Whether `pid` is still OUR process. With a recorded create time, a pid now held by
    another process counts as dead. Without psutil (or for runs recorded before create
    times were kept) the bare pid is all there is to go on."""
    if pid is None:
        return False
    if PSUTIL_AVAILABLE:
        try:
            started = psutil.Process(pid).create_time()
        except psutil.Error:
            return False
        return created is None or abs(started - created) < 0.01
    # Without psutil there is no create time to check (pid_created stays None), so the bare pid.
    if os.name == "nt":
        return _win_pid_alive(pid)
    try:
        os.kill(pid, 0)         # POSIX: signal 0 only probes
        return True
    except (ProcessLookupError, PermissionError, OSError):
        return False


def _win_pid_alive(pid: int) -> bool:
    """Windows liveness without psutil. Never os.kill(pid, 0) here: on Windows any signal but
    CTRL_C/CTRL_BREAK is TerminateProcess, so the probe would kill the run's server.
    ponytail: a process that exited with code 259 (STILL_ACTIVE) reads as alive."""
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    handle = kernel32.OpenProcess(0x1000, False, pid)       # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    try:
        code = wintypes.DWORD()
        return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
    finally:
        kernel32.CloseHandle(handle)


async def _spawn_run(run: dict, host: str, manager_port: int = 0) -> int:
    """Start a server.py subprocess for the given run. Returns the new PID."""
    # Wildcards are probed on loopback of the same family (asyncio's IPv6 listeners are V6ONLY).
    probe_host = {"": "127.0.0.1", "0.0.0.0": "127.0.0.1", "::": "::1"}.get(host, host)
    # Something already answering on the HTTP port would pass the readiness poll as this run.
    if await _http_ready(probe_host, run["http_port"]):
        raise RuntimeError(f"HTTP port {run['http_port']} is in use: another server already answers there")
    data_dir = os.path.join(MANAGER_DIR, run["run_id"])
    os.makedirs(data_dir, exist_ok=True)
    cmd = [
        sys.executable, "-m", "server.server",
        "--host",      host,
        "--port",      str(run["tcp_port"]),
        "--http-port", str(run["http_port"]),
        "--data-dir",  data_dir,
        "--run-id",    run["run_id"],
        "--run-name",  run.get("name", ""),
    ]
    if manager_port:
        cmd += ["--manager-port", str(manager_port)]
    cmd += run_flags(run)
    child_env = os.environ.copy()
    for player, env_name in (("a", "SLINK_PLAYER_KEY_A"), ("b", "SLINK_PLAYER_KEY_B")):
        key = (run.get("instrumentation_keys") or {}).get(player)
        if key:
            child_env[env_name] = key
    # Keep the child's stderr. It used to go to DEVNULL, so a run that died on startup — a taken
    # port, a bad ROM path, a stack trace — vanished without trace and the Manager just showed it
    # as stopped.
    _spawn_log = os.path.join(data_dir, "spawn.log")
    try:
        # The child inherits its own handle during process creation.
        _errf = open(_spawn_log, "ab", buffering=0)   # noqa: SIM115
    except OSError:
        _errf = asyncio.subprocess.DEVNULL
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=_errf,
            env=child_env,
            # Detach from our process group so CTRL-C on the manager doesn't kill runs
            creationflags=0x00000008 if sys.platform == "win32" else 0,  # DETACHED_PROCESS on Windows
        )
    finally:
        if _errf != asyncio.subprocess.DEVNULL:
            _errf.close()
    log.info(f"Spawned run {run['run_id']} (PID {proc.pid}) TCP={run['tcp_port']} HTTP={run['http_port']}")
    # Ready means answering: poll the child's HTTP port until it answers or the child exits
    # (a taken port, a bad ROM path, a stack trace), within SPAWN_READY_S. The answer must come
    # while OUR child is still alive -- a server that cannot bind exits, and whatever else holds
    # that port must not be read as this run. /api/status does not name its run, so alive plus
    # answering is the ownership check.
    loop = asyncio.get_running_loop()
    deadline = loop.time() + SPAWN_READY_S
    exited = asyncio.ensure_future(proc.wait())
    ready = False
    try:
        while True:
            try:
                await asyncio.wait_for(asyncio.shield(exited), timeout=SPAWN_POLL_S)
                why = f"the run's server exited on startup (code {proc.returncode})"
                break
            except TimeoutError:
                pass
            if await _http_ready(probe_host, run["http_port"]) and not exited.done():
                ready = True
                return proc.pid
            if loop.time() >= deadline:
                why = f"the run's server did not answer on HTTP {run['http_port']} within {SPAWN_READY_S:g} s"
                break
    finally:
        # Not ready (hung, or this start was cancelled): our own child must not keep the ports.
        if not ready and proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
        if not exited.done():
            exited.cancel()
    reason = ""
    try:
        with open(_spawn_log, encoding="utf-8", errors="replace") as f:
            lines = [ln.strip() for ln in f.read().splitlines() if ln.strip()]
        reason = next((ln for ln in reversed(lines) if ln.startswith("Cannot ")), lines[-1] if lines else "")
    except OSError:
        pass
    raise RuntimeError(why + (f": {reason}" if reason else "") + f" — see {_spawn_log}")


async def _http_ready(host: str, port: int) -> bool:
    netloc = f"[{host}]" if ":" in host else host          # an IPv6 literal needs brackets
    try:
        # sock_connect: a refused connect on Windows retries for ~2 s; a listener accepts at once
        async with aiohttp.ClientSession() as session, session.get(
            f"http://{netloc}:{port}/api/status", timeout=aiohttp.ClientTimeout(total=2, sock_connect=0.3)
        ) as resp:
            return resp.status == 200
    except Exception:
        return False


def _kill_run(pid: int, created: float | None = None) -> bool:
    """Kill a server.py subprocess by PID. Returns whether our process is gone. A pid whose
    create time does not match is some other process: it counts as gone and is never killed."""
    if not _is_alive(pid, created):
        return True
    try:
        if PSUTIL_AVAILABLE:
            p = psutil.Process(pid)
            p.terminate()
            try:
                p.wait(timeout=5)
            except psutil.TimeoutExpired:
                p.kill()
                p.wait(timeout=5)
        else:
            os.kill(pid, signal.SIGTERM if hasattr(signal, "SIGTERM") else signal.CTRL_C_EVENT)
            # Termination is asynchronous (TerminateProcess, SIGTERM): give it the same 5 s.
            deadline = time.monotonic() + 5
            while _is_alive(pid, created) and time.monotonic() < deadline:
                time.sleep(0.05)
    except Exception as e:
        log.warning(f"Could not kill PID {pid}: {e}")
    return not _is_alive(pid, created)


def _rmtree(path: str) -> list[str]:
    """Remove a tree, clearing read-only bits (Drive, git objects) and retrying once.
    Returns the paths that still could not be removed."""
    failed: list[str] = []

    def retry(fn, p, _exc):
        try:
            os.chmod(p, stat.S_IWRITE)
            fn(p)
        except OSError:
            failed.append(p)
    # onerror is deprecated from 3.12 (onexc); the project still targets 3.11
    shutil.rmtree(path, **({"onexc": retry} if sys.version_info >= (3, 12) else {"onerror": retry}))
    return failed


# ── Health check — reconcile registry with actual process table ─────────────

def _write_run_meta(run: dict):
    """Persist run name/timestamps to run_meta.json so orphan detection can restore them."""
    meta_path = os.path.join(MANAGER_DIR, run["run_id"], "run_meta.json")
    try:
        with open(meta_path, "w") as f:
            json.dump({
                "name":       run.get("name", ""),
                "created_at": run.get("created_at", ""),
            }, f)
    except OSError as e:
        log.warning(f"Could not write run_meta.json for {run['run_id']}: {e}")


def _adopt_orphans(runs: list[dict]) -> bool:
    """
    Scan data/runs/ for subdirectories not in the registry and add them as
    stopped runs.  Returns True if any runs were adopted.

    Priority for metadata:
      1. run_meta.json  — written by the manager at creation time
      2. links.json     — written by server.py; contains rom_type / trainer_names / rules
      3. directory name / ctime fallback
    """
    if not os.path.isdir(MANAGER_DIR):
        return False
    known_ids = {r["run_id"] for r in runs}
    changed = False
    try:
        entries = sorted(os.scandir(MANAGER_DIR), key=lambda e: e.name)
    except OSError:
        return False

    for entry in entries:
        if not entry.is_dir():
            continue
        run_id = entry.name
        if run_id in known_ids:
            continue

        links_path = os.path.join(entry.path, "links.json")
        meta_path  = os.path.join(entry.path, "run_meta.json")

        # Need at least a links.json to treat this as a real run directory
        if not os.path.exists(links_path):
            continue

        # --- derive creation time ---
        try:
            ctime = datetime.fromtimestamp(entry.stat().st_ctime, tz=UTC).isoformat()
        except OSError:
            ctime = datetime.now(UTC).isoformat()

        # --- derive name ---
        name = run_id  # fallback
        if os.path.exists(meta_path):
            try:
                with open(meta_path) as f:
                    meta = json.load(f)
                name   = meta.get("name") or run_id
                ctime  = meta.get("created_at") or ctime
            except (json.JSONDecodeError, OSError):
                pass
        else:
            # Try to build a readable name from links.json
            try:
                with open(links_path) as f:
                    ldata = json.load(f)
                rom = ldata.get("rom_type", "")
                tnames = ldata.get("trainer_names") or {}
                parts = [v for v in [tnames.get("a"), tnames.get("b")] if v]
                if parts:
                    name = " & ".join(parts)
                    if rom:
                        name = f"{name} ({rom})"
                elif rom:
                    name = f"{run_id} ({rom})"
            except (json.JSONDecodeError, OSError):
                pass

        # --- derive rules ---
        rules = {}
        try:
            with open(links_path) as f:
                rules = json.load(f).get("rules", {})
        except (json.JSONDecodeError, OSError):
            pass

        if _tcp_port_pool():
            tcp_port, http_port = None, None
        else:
            tcp_port, http_port = _next_ports(runs)
        run = {
            "run_id":       run_id,
            "name":         name,
            "created_at":   ctime,
            "tcp_port":     tcp_port,
            "http_port":    http_port,
            "status":       "stopped",
            "desired_status": "stopped",
            "pid":          None,
            "instrumentation_keys": {
                "a": secrets.token_urlsafe(32),
                "b": secrets.token_urlsafe(32),
            },
            **run_options(rules),
        }
        runs.append(run)
        known_ids.add(run_id)  # avoid port collision across multiple orphans
        log.info(f"Adopted orphan run directory: {run_id} (name={name!r})")
        changed = True

    return changed


def _reconcile(runs: list[dict]) -> bool:
    """Check live processes; update status for dead ones. Adopt orphan dirs. Returns True if any changed."""
    changed = False
    for run in runs:
        if _ensure_instrumentation_keys(run):
            changed = True
        if _ensure_desired_status(run):
            changed = True
        if run["status"] in {"running", "starting"} and not _is_alive(
                run.get("pid"), run.get("pid_created")):
            run["status"] = "stopped"
            run["pid"] = None
            run["pid_created"] = None
            run.update(_released_ports())
            changed = True
    if _adopt_orphans(runs):
        changed = True
    return changed


# ── HTML UI ─────────────────────────────────────────────────────────────────

_STATUS_BADGE = {
    "running":  '<span class="badge running"><span class="badge-dot"></span> running</span>',
    "stopped":  '<span class="badge stopped"><span class="badge-dot"></span> stopped</span>',
    "archived": '<span class="badge archived"><span class="badge-dot"></span> archived</span>',
}


def _manager_public_get(request: web.Request) -> bool:
    path = request.path
    if path in {"/", "/healthz", "/readyz", "/api/runs", "/api/status", "/broadcast"}:
        return True
    if path.startswith(("/stream", "/calc/")):
        return True
    if re.fullmatch(r"/api/runs/[^/]+/live", path):
        return True
    if re.fullmatch(r"/api/runs/[^/]+/player-pack/[ab]", path):
        return True
    if re.fullmatch(r"/runs/[^/]+(?:/board|/timeline)?", path):
        return True
    if re.fullmatch(r"/runs/[^/]+/calc(?:/.*)?", path):
        return True
    return bool(re.fullmatch(r"/runs/[^/]+/api/(?:status|events|calc/mons)", path))


class RunManager:
    def __init__(self, bind_host: str, manager_port: int = MANAGER_HTTP_PORT, public_host: str = ""):
        self.bind_host = bind_host
        self.manager_port = manager_port
        # The address players connect to, when the host names one: --public-host, else what
        # was last set on a run page. "" = work it out (advertised_host).
        self.public_host = public_host or str(_load_settings().get("public_host") or "")
        self._stream_pin_id: str | None = None  # run_id pinned for stream overlays
        # One lock per run: a start (spawn + readiness wait) and a stop serialize, so two
        # clicks cannot spawn twice and a stop mid-start is not overwritten by the start.
        # ponytail: in-process only. Two Managers sharing one registry are not locked
        # against each other (documented limitation: run one Manager per data dir).
        self._run_locks: dict[str, list] = {}      # run_id -> [lock, holders + waiters]
        self._lease_lock = asyncio.Lock()

    @contextlib.asynccontextmanager
    async def _run_lock(self, run_id: str):
        """The run's lock, dropped once nobody holds or waits on it, so ids that 404 or were
        deleted do not accumulate. Counted by hand: after a release, lock.locked() is False
        while a woken waiter has yet to take it."""
        entry = self._run_locks.setdefault(run_id, [asyncio.Lock(), 0])
        entry[1] += 1
        try:
            async with entry[0]:
                yield
        finally:
            entry[1] -= 1
            if not entry[1]:
                del self._run_locks[run_id]

    def _get(self) -> list[dict]:
        runs = _load_registry()
        if _reconcile(runs):
            _save_registry(runs)
        return runs

    def _port_lease_lock(self) -> asyncio.Lock:
        lock = getattr(self, "_lease_lock", None)
        if lock is None:
            lock = self._lease_lock = asyncio.Lock()
        return lock

    async def _start_run(self, run_id: str) -> tuple[int | None, str]:
        async with self._port_lease_lock():
            runs = _load_registry()
            run = _find_run(runs, run_id)
            if run is None:
                return None, "Run not found"
            if run.get("status") == "archived":
                return None, "Archived runs cannot be started"
            if run.get("status") == "running" and _is_alive(
                    run.get("pid"), run.get("pid_created")):
                return run.get("pid"), ""
            try:
                if _tcp_port_pool():
                    preferred = _preferred_cloud_ports(run, runs)
                    if preferred is None:
                        others = [other for other in runs if other.get("run_id") != run_id]
                        preferred = _next_ports(others)
                    run["tcp_port"], run["http_port"] = preferred
                elif not run.get("tcp_port") or not run.get("http_port"):
                    run["tcp_port"], run["http_port"] = _next_ports(runs)
                pid = await _spawn_run(
                    run, self.bind_host, manager_port=self.manager_port)
            except Exception as exc:
                return None, str(exc)
            created = _create_time(pid)
            try:
                registered = _update_run(
                    run_id,
                    status="running",
                    desired_status="running",
                    pid=pid,
                    pid_created=created,
                    tcp_port=run["tcp_port"],
                    http_port=run["http_port"],
                )
                if registered is None:
                    raise RegistryError("Run disappeared before its server could be registered")
            except (RegistryError, OSError) as exc:
                error = f"Could not register started run {run_id}: {exc}"
                if not await asyncio.to_thread(_kill_run, pid, created):
                    error += f"; could not stop spawned PID {pid}"
                log.error(error)
                return None, error
            return pid, ""

    async def restore_desired_runs(self) -> None:
        runs = self._get()
        run_ids = [
            run["run_id"] for run in runs
            if run.get("desired_status") == "running" and run.get("status") != "archived"
        ]

        async def restore(run_id: str) -> None:
            async with self._run_lock(run_id):
                current = _find_run(_load_registry(), run_id)
                if current is None or current.get("desired_status") != "running":
                    return
                _pid, error = await self._start_run(run_id)
                if error:
                    log.error("Could not restore desired run %s: %s", run_id, error)

        await asyncio.gather(*(restore(run_id) for run_id in run_ids))

    async def stop_child_processes(self) -> None:
        try:
            runs = _load_registry()
        except RegistryError as exc:
            log.error("Could not stop child processes from unreadable registry: %s", exc)
            return
        await asyncio.gather(*(
            asyncio.to_thread(_kill_run, run.get("pid"), run.get("pid_created"))
            for run in runs
            if run.get("status") == "running" and run.get("pid")
        ))

    def _active_stream_run(self) -> dict | None:
        """Return the run that stream overlays should proxy to.

        Priority:
        1. Explicitly pinned run (if still running and alive).
        2. Most recently started running run (latest created_at).
        Returns None if no run is running.
        """
        # A plain read. _get() reconciles and can rewrite registry.json, and this is
        # called on every 2 s overlay poll from every browser source.
        runs = _load_registry()
        running = [r for r in runs if r.get("status") == "running" and _is_alive(r.get("pid"), r.get("pid_created"))]
        if not running:
            return None
        if self._stream_pin_id:
            for r in running:
                if r["run_id"] == self._stream_pin_id:
                    return r
            # Pinned run stopped — clear pin, fall through to auto
            self._stream_pin_id = None
        return max(running, key=lambda r: r.get("created_at", ""))

    async def handle_index(self, request: web.Request) -> web.Response:
        """GET / — the home page: what Soul Link is, the runs, and the way in."""
        runs = self._get()
        return await self._render_shell(request, runs, None, page="home",
                                        extra={"home_runs": await self._home_runs(request, runs)})

    async def _home_runs(self, request: web.Request, runs: list[dict]) -> list[dict]:
        """Unarchived runs, running first then newest; alive/fallen only where a server answers."""
        from server.board import build_board
        shown = sorted((r for r in runs if r.get("status") != "archived"),
                       key=lambda r: r.get("created_at") or "", reverse=True)
        shown.sort(key=lambda r: r.get("status") != "running")

        async def one(run: dict) -> dict:
            r = self._augment_for_template(run)
            live = (await self._fetch_live(request, run)
                    if run.get("status") == "running" and run.get("http_port") else None)
            r["counts"] = build_board(live)["counts"] if live else None
            return r
        return list(await asyncio.gather(*(one(r) for r in shown)))

    async def handle_run_page(self, request: web.Request) -> web.Response:
        """GET /runs/{run_id} — the shell with that run's board."""
        runs = self._get()
        run = _find_run(runs, request.match_info["run_id"])
        if run is None:
            raise web.HTTPNotFound(text="Run not found")
        return await self._render_shell(request, runs, run, page="run")

    async def handle_new_page(self, request: web.Request) -> web.Response:
        """GET /new — the shell with the New-run form."""
        return await self._render_shell(request, self._get(), None, page="new")

    async def _render_shell(self, request, runs, run, *, page, extra=None):
        status = await self._run_status(request, run) if run else None
        show_calc = bool(_calc_profile_for_run(run, status)) if run else False
        ctx = {
            "page_title":   "Soul Link",
            "theme":        resolve_theme(request),
            "is_stream":    False,
            "hide_chrome":  False,
            "body_class":   "board mgr",
            "page":         page,
            "runs":         [self._augment_for_template(r) for r in runs],
            "run":          self._augment_for_template(run) if run else None,
            "show_calc":    show_calc,
            "pinned_run_id": self._stream_pin_id,
            "form_json":    _json_for_script(new_run_form()),
            # The creator randomizes as part of creating a Gen 1 run; it needs the same
            # categories / labels / jar the standalone page does, with no current pair.
            # Off the event loop: the form scans (and may hash) every ROM file.
            "randomizer_json": _json_for_script(await asyncio.to_thread(self._randomizer_form, None)),
            "next_ports":   self._next_ports_preview(runs),
            "manager_port": self.manager_port,
            # Links to a run's own port (calc, debug) use the host the browser used for us.
            "host":         (request.host or "127.0.0.1").split(":")[0] or "127.0.0.1",
            "is_admin":     request.get("is_admin", True),
        }
        if run:
            ctx.update(self._board_context(run, status))
        ctx.update(extra or {})
        return aiohttp_jinja2.render_template("manager.html", request, ctx)

    def _connect_host(self) -> tuple[str, str]:
        return advertised_host(self.public_host, self.bind_host)

    @staticmethod
    def _next_ports_preview(runs: list[dict]) -> tuple[int | None, int | None]:
        try:
            return _next_ports(runs)
        except RuntimeError:
            return None, None

    def _board_context(self, run: dict, status: dict) -> dict:
        """board_context for a Manager run: it polls its own board route, its launchers and
        (once it has made them) its cartridges download from the Manager. On top of the
        shared board: each player's setup ZIP, the address players connect to, the BizHawk
        minimum."""
        from server.board import PIDS, board_context
        rid = run["run_id"]
        live = run.get("status") == "running"
        ctx = board_context(status, run_name=run.get("name", ""), poll_url=f"/runs/{rid}/board",
                            live=live,
                            launcher_url=f"/api/runs/{rid}/launcher/{{player}}",
                            rom_url=f"/api/runs/{rid}/rom/{{player}}" if run.get("cartridges") or run.get("randomizer") else "",
                            roms_pinned=bool(run.get("randomizer")),
                            rom_ext=_rom_ext(run))
        host, source = self._connect_host()
        ctx.update({
            "packs": {pid: f"/api/runs/{rid}/player-pack/{pid}" for pid in PIDS},
            "connect": {"host": host, "port": run["tcp_port"], "source": source},
            "bizhawk_min": make_release.bizhawk_requirement(),
        })
        return ctx

    async def handle_run_board(self, request: web.Request) -> web.Response:
        """GET /runs/{run_id}/board — the `#content` fragment the shell polls."""
        # _get reconciles: a server that died reads as stopped on the next poll, not "running".
        run = _find_run(self._get(), request.match_info["run_id"])
        if run is None:
            raise web.HTTPNotFound(text="Run not found")
        ctx = self._board_context(run, await self._run_status(request, run))
        ctx["is_admin"] = request.get("is_admin", True)
        return aiohttp_jinja2.render_template("_board.html", request, ctx)

    async def _run_status(self, request: web.Request, run: dict) -> dict:
        """The run's status payload: live from its server when it is running, otherwise
        rebuilt from what it persisted. A stopped run's own loader reads its own files,
        so the board for it is the board it had -- links, memorial, events -- with no
        player connected."""
        if run.get("status") == "running" and run.get("http_port"):
            live = await self._fetch_live(request, run)
            if live is not None:
                return live
        run_dir = os.path.join(MANAGER_DIR, run["run_id"])
        if not os.path.isdir(run_dir):
            return empty_status_payload()
        try:
            from server.server import SLinkServer
            opts = {k: v for k, v in run_options(run).items() if k != "verbose"}
            return SLinkServer(data_dir=run_dir, run_id=run["run_id"], run_name=run.get("name", ""),
                               **opts)._build_status_dict()
        except Exception as e:
            log.warning(f"could not rebuild status for {run['run_id']}: {e}")
            return empty_status_payload()

    def _randomizer_form(self, run: dict | None) -> dict | None:
        """The randomized-pair builder's state for a Gen 1 run: the categories the pipeline
        supports (from the same table the allowlist is computed from), what the run has,
        and where to start looking for the jar."""
        if run is not None and run.get("game") not in new_run_form()["randomizer_games"]:
            return None
        from server.upr_pipeline import find_upr_jar, jar_is_fork, jar_is_trusted
        from server.upr_settings import option_form
        jar = find_upr_jar() or ""
        return {
            "options": option_form(every_family=True),
            "jar": jar,
            # only a jar whose sha256 is in data/upr_jars.json is ever run (upr_pipeline)
            "jar_trusted": bool(jar) and jar_is_trusted(jar),
            # The pure family randomizes only on the SLink fork jar (upr_pipeline); the
            # page says which jar it found so a greyed pure ROM is explained.
            "jar_fork": bool(jar) and jar_is_fork(jar),
            "roms": self._scan_roms(jar),
            "roms_dir": PROJECT_ROOT,
            # Which cartridges this run can take: the family its game names (the creator
            # follows the game chip through game_family instead).
            "family": _game_family(run.get("game")) if run else None,
            "game_family": GAME_FAMILY,
            "presets": _load_presets(),
            "current": run.get("randomizer") if run else None,
            "cartridges": (run.get("cartridges") or _legacy_cartridges(run)) if run else None,
            # The titles that get the SLink companion (server/cartridges.py decides per player);
            # the form only explains why a pick outside them is handed out as picked.
            "companion_titles": _companion_titles(),
        }

    @staticmethod
    def _scan_roms(jar: str) -> list[dict]:
        """Every .gb/.gbc in ROM_DIRS with the scanner's verdict (describe_rom). A zero-byte
        file (an interrupted download, a placeholder) is not a cartridge and is skipped. Two
        paths with identical bytes are the same cartridge picked up twice (a copy in roms/ of
        one already in the repo root, say) and are deduped, first ROM_DIRS folder wins."""
        from server.upr_pipeline import describe_rom
        roms, seen_paths, seen_content = [], set(), set()
        for d in ROM_DIRS:
            if not os.path.isdir(d):
                continue
            for name in sorted(os.listdir(d), key=str.lower):
                path = os.path.join(d, name)
                if not (name.lower().endswith(ROM_EXTS) and os.path.isfile(path) and path not in seen_paths):
                    continue
                seen_paths.add(path)
                try:
                    st = os.stat(path)
                except OSError:
                    continue
                if st.st_size == 0:
                    continue
                # ponytail: size+mtime+ctime, not a re-hash. A cartridge swapped in place that
                # keeps ALL THREE stamps still returns the cached sha1, so the picker would
                # label it as the old ROM; ctime catches the replace-the-file case (the common
                # one: a restore or a Drive sync writes a new file), mtime the ordinary
                # overwrite. Re-hash every scan if a stale label ever actually bites.
                key = (st.st_size, st.st_mtime_ns, st.st_ctime_ns)
                hit = _ROM_INFO_CACHE.get(path)
                if hit and hit[0] == key:
                    info = hit[1]
                else:
                    info = describe_rom(path, True)
                    if info.get("sha1"):        # a failed read is retried next scan, not kept
                        _ROM_INFO_CACHE[path] = (key, info)
                digest = info.get("sha1") or path
                if digest in seen_content:
                    continue
                seen_content.add(digest)
                roms.append({"name": name, **info})
        return roms

    def _augment_for_template(self, run: dict) -> dict:
        """Display strings for the rail: a short date, a filesystem-safe name, the game."""
        rid = run["run_id"]
        r = dict(run)
        r["created_short"] = (run.get("created_at") or "")[:16].replace("T", " ")
        r["safe_name"] = re.sub(r"[^\w-]", "_", run.get("name") or rid).strip("_") or rid
        r["game_label"] = GAME_LABELS.get(run.get("game") or "", "")
        r["gen1"] = (run.get("game") or "") in new_run_form()["randomizer_games"]
        r["rom_ext"] = _rom_ext(run)
        return r

    async def handle_list(self, request: web.Request) -> web.Response:
        return web.json_response({"runs": [_public_run(run) for run in self._get()]})

    async def handle_healthz(self, request: web.Request) -> web.Response:
        return web.json_response({"ok": True})

    async def handle_readyz(self, request: web.Request) -> web.Response:
        try:
            _load_registry()
        except RegistryError as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=503)
        writable, error = writable_directory(MANAGER_DIR)
        return web.json_response(
            {"ok": writable, **({"error": error} if error else {})},
            status=200 if writable else 503,
        )

    async def handle_new(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "Invalid JSON"}, status=400)
        name = str(body.get("name", "")).strip()
        if not name:
            return web.json_response({"ok": False, "error": "name is required"}, status=400)
        if len(name) > 80:
            return web.json_response({"ok": False, "error": "name is too long (80 characters max)"}, status=400)
        # Only a listed game can be created (an unlisted one, e.g. an Archipelago key from an old
        # page, is refused here instead of at hello).
        game = str(body.get("game", "") or "").strip().lower()  # one normalized key: check, store, message
        if game not in GAME_MEMBERS:
            return web.json_response({"ok": False, "error": "choose the game this run plays"}, status=400)
        if game in UNADMITTED_GAMES:
            return web.json_response({"ok": False, "error": f"{GAME_LABELS[game]}: cannot create a run"},
                                     status=400)

        runs = _load_registry()
        run_id = "run_" + datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        # Handle collision (unlikely but possible)
        existing_ids = {r["run_id"] for r in runs}
        suffix = 0
        base_id = run_id
        while run_id in existing_ids:
            suffix += 1
            run_id = f"{base_id}_{suffix}"

        try:
            tcp_port, http_port = _next_ports(runs)
            start_error = ""
        except RuntimeError as exc:
            tcp_port, http_port = None, None
            start_error = str(exc)
        run = {
            "run_id":     run_id,
            "name":       name,
            "created_at": datetime.now(UTC).isoformat(),
            "tcp_port":   tcp_port,
            "http_port":  http_port,
            "status":     "stopped",
            "desired_status": "running" if tcp_port is not None else "stopped",
            "pid":        None,
            "instrumentation_keys": {
                "a": secrets.token_urlsafe(32),
                "b": secrets.token_urlsafe(32),
            },
            **run_options(body),
            "game": game,  # the game FAMILY
        }
        # Create data directory immediately
        os.makedirs(os.path.join(MANAGER_DIR, run_id), exist_ok=True)
        _write_run_meta(run)
        runs.append(run)
        _save_registry(runs)

        # Auto-start. The run exists either way; a start that fails is reported with its
        # reason so the creator can show it, rather than landing on a "running" run.
        async with self._run_lock(run_id):
            if tcp_port is None:
                return web.json_response(
                    {"ok": True, "run": _public_run(run), "start_error": start_error})
            _pid, start_error = await self._start_run(run_id)
            run = _find_run(_load_registry(), run_id) or run
            if start_error:
                log.error("Failed to auto-start run %s: %s", run_id, start_error)

        return web.json_response({"ok": True, "run": _public_run(run), "start_error": start_error})

    async def handle_start(self, request: web.Request) -> web.Response:
        run_id = request.match_info["run_id"]
        async with self._run_lock(run_id):
            pid, error = await self._start_run(run_id)
            if error == "Run not found":
                return web.json_response({"ok": False, "error": error}, status=404)
            if error == "Archived runs cannot be started":
                return web.json_response({"ok": False, "error": error}, status=400)
            if error:
                status = 409 if error.startswith("All ") and "configured game ports" in error else 500
                return web.json_response({"ok": False, "error": error}, status=status)
            return web.json_response({"ok": True, "pid": pid})

    async def handle_stop(self, request: web.Request) -> web.Response:
        run_id = request.match_info["run_id"]
        async with self._run_lock(run_id):
            runs = _load_registry()
            run = _find_run(runs, run_id)
            if run is None:
                return web.json_response({"ok": False, "error": "Run not found"}, status=404)
            pid = run.get("pid")
            if pid and not await asyncio.to_thread(_kill_run, pid, run.get("pid_created")):
                # Keep the pid: the server is still up, and a later stop must be able to retry.
                return web.json_response({"ok": False, "error": f"Could not stop the run's server (PID {pid})"},
                                         status=500)
            _update_run(run_id, status="stopped", desired_status="stopped",
                        pid=None, pid_created=None,
                        **_released_ports())
            return web.json_response({"ok": True})

    async def handle_archive(self, request: web.Request) -> web.Response:
        run_id = request.match_info["run_id"]
        async with self._run_lock(run_id):
            runs = _load_registry()
            run = _find_run(runs, run_id)
            if run is None:
                return web.json_response({"ok": False, "error": "Run not found"}, status=404)
            pid = run.get("pid")
            if pid and not await asyncio.to_thread(_kill_run, pid, run.get("pid_created")):
                return web.json_response({"ok": False, "error": f"Could not stop the run's server (PID {pid})"},
                                         status=500)
            _update_run(run_id, status="archived", desired_status="archived",
                        pid=None, pid_created=None,
                        **_released_ports())
            return web.json_response({"ok": True})

    async def handle_delete(self, request: web.Request) -> web.Response:
        run_id = request.match_info["run_id"]
        async with self._run_lock(run_id):
            runs = _load_registry()
            run = _find_run(runs, run_id)
            if run is None:
                return web.json_response({"ok": False, "error": "Run not found"}, status=404)
            # Stop the process if running; never delete the data out from under a live server
            pid = run.get("pid")
            if pid and not await asyncio.to_thread(_kill_run, pid, run.get("pid_created")):
                return web.json_response({"ok": False, "error": f"Could not stop the run's server (PID {pid})"},
                                         status=500)
            # Remove the data directory. A partial delete keeps the registry entry and says what
            # is left: a leftover links.json would otherwise be re-adopted as a stopped run.
            data_dir = os.path.join(MANAGER_DIR, run_id)
            if os.path.isdir(data_dir):
                failed = await asyncio.to_thread(_rmtree, data_dir)
                if os.path.exists(data_dir):
                    left = failed[0] if failed else data_dir
                    return web.json_response({"ok": False, "error": f"Could not delete the run's data: {left} is still there"},
                                             status=500)
                log.info(f"Deleted data directory for run {run_id}")
            # re-read: a board poll may have reconciled other runs during the awaits
            runs = [r for r in _load_registry() if r["run_id"] != run_id]
            try:
                _save_registry(runs)
            except OSError as e:
                return web.json_response({"ok": False, "error": f"Run data deleted, but the registry could not be saved: {e}"},
                                         status=500)
            log.info(f"Deleted run {run_id}")
            return web.json_response({"ok": True})

    async def handle_launcher(self, request: web.Request) -> web.Response:
        """Serve a launcher .lua file with the connect host derived from the request."""
        run_id = request.match_info["run_id"]
        player = request.match_info["player"]
        if player not in ("a", "b"):
            return web.json_response({"ok": False, "error": "player must be 'a' or 'b'"}, status=400)
        runs = self._get()
        run = _find_run(runs, run_id)
        if run is None:
            return web.json_response({"ok": False, "error": "Run not found"}, status=404)
        content = _build_launcher(run, player, self._connect_host()[0])
        safe_name = self._augment_for_template(run)["safe_name"]
        filename = f"slink_{safe_name}_{player}.lua"
        return web.Response(
            text=content,
            content_type="application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    async def handle_player_pack(self, request: web.Request) -> web.Response:
        """GET /api/runs/{run_id}/player-pack/{player} — the whole player package (the
        release ZIP from tools/make_release.py) with this run's launcher at its root and the
        run's host, game TCP port and slot baked into every launcher inside."""
        player = request.match_info["player"]
        if player not in ("a", "b"):
            return web.json_response({"ok": False, "error": "player must be 'a' or 'b'"}, status=400)
        run = _find_run(self._get(), request.match_info["run_id"])
        if run is None:
            return web.json_response({"ok": False, "error": "Run not found"}, status=404)
        host = self._connect_host()[0]
        name = f"{self._augment_for_template(run)['safe_name']}_{player}"
        launcher = f"slink_{name}.lua"
        guide = make_release.player_setup_md(launcher, f"{host}:{run['tcp_port']}", player)

        # ponytail: built per request (~60 ms, ~0.5 MB) and held in memory; cache the base
        # package if a slow disk ever makes this noticeable.
        def build() -> bytes:
            with tempfile.TemporaryDirectory() as out:
                path = make_release.build_release(
                    version=name, out_dir=pathlib.Path(out), host=host, port=run["tcp_port"],
                    player=player, skip_generators=True, quiet=True,
                    launcher=(launcher, _build_launcher(run, player, host)), guide=guide)
                return path.read_bytes()
        try:
            data = await asyncio.to_thread(build)
        except (SystemExit, OSError) as e:          # make_release exits on a missing file
            log.error(f"player pack for {run['run_id']}/{player} failed: {e!r}")
            return web.json_response({"ok": False, "error": "could not build the player package; "
                                      "see the Manager log"}, status=500)
        return web.Response(body=data, content_type="application/zip",
                            headers={"Content-Disposition": _content_disposition(f"slink_{name}.zip")})

    async def handle_public_host(self, request: web.Request) -> web.Response:
        """POST /api/settings/public-host {"host": "..."} — the address players connect to.
        "" goes back to working it out. Persisted beside the registry."""
        try:
            host = str((await request.json()).get("host") or "").strip()
        except Exception:
            return web.json_response({"ok": False, "error": "Invalid JSON"}, status=400)
        if host and not _HOST_RE.fullmatch(host):
            return web.json_response({"ok": False, "error": "not a host name or IP address"}, status=400)
        settings = _load_settings()
        settings["public_host"] = host
        try:
            atomic_write_json(_settings_path(), settings)
        except OSError as e:
            return web.json_response({"ok": False, "error": f"could not save: {e}"}, status=500)
        self.public_host = host
        resolved, source = self._connect_host()
        return web.json_response({"ok": True, "host": resolved, "source": source})

    # ── Cartridges: what each player plays ────────────────────────────────────

    async def handle_cartridges(self, request: web.Request, *, implied_randomize: bool = False) -> web.Response:
        """POST /api/runs/{run_id}/cartridges — produce each player's cartridge from what
        was picked: the SLink companion on it when asked, randomized when asked
        (server/cartridges.py decides the order per family). Also answers the older
        /api/runs/{run_id}/randomize, where randomizing is implied.

        Body: {rom_a, rom_b, companion?: bool, randomize?: bool, jar?, and for randomizing
        one of spec (upr_settings.OPTIONS) | categories | settings (a .rnqs path)}.

        Everything the players need to trust a randomized pair is recorded on the run: the
        settings file's hash, both seeds, both final ROM hashes and the scanned content
        hashes. None of it is recoverable from the ROMs afterwards -- UPR's CLI has no seed
        flag and writes the seed only to its log -- so if this step does not capture it,
        nothing can. Runs in a thread: the pipeline shells out to java twice.
        """
        run_id = request.match_info["run_id"]
        runs = _load_registry()
        run = _find_run(runs, run_id)
        if run is None:
            return web.json_response({"ok": False, "error": "Run not found"}, status=404)
        # A run whose game cannot be randomized is refused by name, before any ROM is read
        # or any jar is resolved -- the request itself is what ruling 37 removes.
        if refused := NON_RANDOMIZABLE_GAMES.get(run.get("game") or ""):
            return web.json_response({"ok": False, "error": refused}, status=400)
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "Invalid JSON"}, status=400)

        from server import cartridges
        from server.upr_pipeline import (
            EXPANSION_REFUSAL,
            FAMILY_GEN3_EXP,
            FAMILY_POLISHED,
            POLISHED_RANDOMIZER_ENABLED,
            POLISHED_RANDOMIZER_REFUSAL,
            family_of,
            find_upr_jar,
        )
        from server.upr_settings import (
            FAMILY_PURE,
            FAMILY_VANILLA,
            UprSettingsError,
            build_categories,
            build_spec,
            family_spec,
        )

        jar = str(body.get("jar", "")).strip() or find_upr_jar() or ""
        settings = str(body.get("settings", "")).strip()
        rom_a = str(body.get("rom_a", "")).strip()
        rom_b = str(body.get("rom_b", "")).strip()
        spec, categories = body.get("spec"), body.get("categories")
        randomize = bool(body.get("randomize", implied_randomize or spec is not None
                                      or categories is not None or bool(settings)))
        companion = bool(body.get("companion", True))  # patch-first; per player in cartridges.py
        missing = [n for n, v in (("rom_a", rom_a), ("rom_b", rom_b)) if not v]
        if randomize:
            missing += [n for n, v in (("jar", jar),) if not v]
            if spec is None and categories is None and not settings:
                missing.append("settings")
        if missing:
            return web.json_response(
                {"ok": False, "error": f"missing: {', '.join(missing)}"}, status=400)
        # Players who joined committed the run to one pairing kind (set-once, links.json); cartridges of
        # the other kind would be refused at every hello for the rest of the run.
        committed = _committed_artifact_kind(run_id)
        if committed and committed.startswith("rand") != randomize:
            was = "randomized" if committed.startswith("rand") else "unrandomized"
            now = "randomized" if randomize else "unrandomized"
            return web.json_response({"ok": False, "error": (
                f"players already joined this run with {was} cartridges; {now} ones would be refused at "
                f"every connect. Start a new run for a {now} pair.")}, status=409)

        # The family (vanilla / pureRGB) comes from the ROMs. A run named up front admits
        # one family; a pair from the other would be refused at the first hello, so refuse
        # it here, where it can be fixed.
        family = FAMILY_VANILLA
        if os.path.isfile(rom_a) and os.path.isfile(rom_b):
            try:
                family = family_of({"a": rom_a, "b": rom_b})
            except Exception as exc:                      # noqa: BLE001
                return web.json_response({"ok": False, "error": str(exc)}, status=400)
            wanted = _game_family(run.get("game"))
            if wanted and wanted != family:
                return web.json_response({"ok": False, "error": (
                    f"this run is {GAME_LABELS.get(run['game'], run['game'])}; these are "
                    f"{FAMILY_WORDS.get(family, family)} cartridges -- pick "
                    f"{FAMILY_WORDS.get(wanted, wanted)} dumps")}, status=400)
        if family == FAMILY_GEN3_EXP and randomize:
            return web.json_response({"ok": False, "error": EXPANSION_REFUSAL}, status=400)
        if randomize and family == FAMILY_POLISHED and not POLISHED_RANDOMIZER_ENABLED:
            return web.json_response({"ok": False, "error": POLISHED_RANDOMIZER_REFUSAL}, status=400)
        # Either a settings file the user built in UPR's GUI, the form's spec (every option
        # in upr_settings.OPTIONS), or the six categories older callers speak in -- the last
        # two go through the SAME builder the allowlist is computed from, so a file made here
        # is by construction one the pipeline admits. A pure pair gets every code-patching
        # tweak turned off (the fork offers only lower-case names, a data write).
        blob = None
        if randomize and not settings:
            try:
                if spec is not None:
                    if not isinstance(spec, dict):
                        raise UprSettingsError("spec must be an object")
                    blob = build_spec(family_spec(spec, family), family=family)
                else:
                    fastest = bool(body.get("fastest_text", True)) and family != FAMILY_PURE
                    blob = build_categories(set(map(str, categories)), fastest_text=fastest, family=family)
            except UprSettingsError as exc:
                return web.json_response({"ok": False, "error": str(exc)}, status=400)

        run_dir = os.path.join(MANAGER_DIR, run_id)
        # Under the run's lock (start/stop/archive/delete take it too), and the registry is re-read after
        # the provision: it can take minutes, and writing back the snapshot from before it lost a pid
        # started meanwhile (an unkillable server) or brought back a run deleted meanwhile.
        async with self._run_lock(run_id):
            try:
                if blob is not None:
                    settings = os.path.join(run_dir, "settings.rnqs")
                    os.makedirs(run_dir, exist_ok=True)
                    with open(settings, "wb") as f:
                        f.write(blob)
                result = await asyncio.to_thread(
                    cartridges.provision, run_dir, {"a": rom_a, "b": rom_b}, companion=companion,
                    randomize={"settings_path": settings} if randomize else None, jar=jar)
            except cartridges.CartridgeError as exc:
                # A refusal is the feature, not a crash: say exactly what was wrong so the user
                # can fix the settings or the ROMs rather than guessing.
                log.warning("cartridges %s refused: %s", run_id, exc)
                return web.json_response({"ok": False, "error": str(exc)}, status=400)
            except Exception as exc:                      # noqa: BLE001
                log.exception("cartridges %s failed", run_id)
                return web.json_response({"ok": False, "error": f"unexpected: {exc}"}, status=500)

            runs = _load_registry()
            run = _find_run(runs, run_id)
            if run is None:
                return web.json_response({"ok": False, "error": "This run was deleted while its cartridges were made."},
                                         status=404)
            now = datetime.now(UTC).isoformat()
            run["cartridges"] = {**result, "created_at": now}
            rnd = result.get("randomizer")
            if rnd:
                # The pair as the run records it (the shape the randomizer page and the older
                # callers read): the FINAL cartridge's path and sha1 per player.
                run["randomizer"] = {
                    "upr_version": rnd["upr_version"],
                    "settings_sha256": rnd["settings_sha256"],
                    "categories": rnd["categories"],
                    "spec": rnd["spec"],
                    "summary": rnd["summary"],
                    "created_at": now,
                    "players": {
                        p: {"seed": str(v["seed"]),      # 48-bit; a string so no JS float rounds it
                            "rom_sha1": result["players"][p]["rom_sha1"],
                            "source_sha1": v["source_sha1"],
                            "content_hash": v["content_hash"],
                            "output": result["players"][p]["output"]}
                        for p, v in rnd["players"].items()
                    },
                }
            else:
                run.pop("randomizer", None)
            try:
                _save_registry(runs)
            except OSError as exc:
                log.exception("cartridges %s: registry save failed", run_id)
                return web.json_response({"ok": False, "error": (
                    f"the cartridges were made but the run list could not be saved ({exc}); prepare them again")},
                    status=500)
            _write_run_meta(run)
        return web.json_response({"ok": True, "cartridges": run["cartridges"],
                                  "randomizer": run.get("randomizer")})

    async def handle_randomize(self, request: web.Request) -> web.Response:
        """POST /api/runs/{run_id}/randomize — the older name: randomizing is what it did,
        and what it still implies."""
        return await self.handle_cartridges(request, implied_randomize=True)

    async def handle_settings_export(self, request: web.Request) -> web.Response:
        """POST /api/randomizer/settings/export {spec, name?} — the form's settings as a UPR
        .rnqs: the file UPR's own GUI opens, the same bytes handle_randomize would write
        for this spec (upr_settings.build_spec)."""
        from server.upr_settings import (
            FAMILIES,
            FAMILY_VANILLA,
            UprSettingsError,
            build_spec,
            family_spec,
        )
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "Invalid JSON"}, status=400)
        spec = body.get("spec")
        if not isinstance(spec, dict):
            return web.json_response({"ok": False, "error": "spec is required"}, status=400)
        family = body.get("family") or FAMILY_VANILLA
        if family not in FAMILIES:
            return web.json_response({"ok": False, "error": f"unknown family: {family!r}"}, status=400)
        try:
            blob = build_spec(family_spec(spec, family), family=family)
        except UprSettingsError as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=400)
        name = re.sub(r"[^\w-]+", "_", str(body.get("name") or "slink")).strip("_") or "slink"
        return web.Response(body=blob, headers={
            "Content-Type": "application/octet-stream",
            "Content-Disposition": f'attachment; filename="{name}.rnqs"',
        })

    async def handle_settings_import(self, request: web.Request) -> web.Response:
        """POST /api/randomizer/settings/import (multipart `file`, optional `family`) — a
        .rnqs from UPR's GUI or from another run, admitted by the pipeline's own gates
        (version, the named dangers, the allowlist) and read back as the form's spec. A
        refusal names what the file changes, because spec_from_parsed alone would drop it
        silently. `family` picks which allowlist applies (pureRGB's is stricter); the form
        knows its own family and sends it, defaulting to vanilla when it does not (a run
        not yet tied to a family, or an older caller)."""
        from server.upr_pipeline import UprPipelineError, admit_settings
        from server.upr_settings import FAMILIES, FAMILY_VANILLA, spec_from_parsed, summarize
        if request.content_type != "multipart/form-data":
            return web.json_response({"ok": False, "error": "multipart/form-data expected"}, status=400)
        reader = await request.multipart()
        raw, family_raw = None, ""
        field = await reader.next()
        while field is not None:
            if field.name == "file":
                raw = await field.read(decode=False)
            elif field.name == "family":
                family_raw = (await field.read()).decode("utf-8", "replace").strip()
            field = await reader.next()
        if raw is None:
            return web.json_response({"ok": False, "error": "send the .rnqs as `file`"}, status=400)
        if len(raw) > 64 << 10:
            return web.json_response({"ok": False, "error": "not a settings file (too large)"}, status=400)
        family = family_raw or FAMILY_VANILLA
        if family not in FAMILIES:
            return web.json_response({"ok": False, "error": f"unknown family: {family_raw!r}"}, status=400)
        try:
            parsed = admit_settings(raw, family)
        except UprPipelineError as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=400)
        spec = spec_from_parsed(parsed, family)
        return web.json_response({"ok": True, "spec": spec, "summary": summarize(spec),
                                  "rom_name": parsed.get("rom_name", "")})

    async def handle_run_settings(self, request: web.Request) -> web.Response:
        """GET /api/runs/{run_id}/settings.rnqs — the settings file the pair was built
        with, as UPR's GUI would open it."""
        run_id = request.match_info["run_id"]
        run = _find_run(_load_registry(), run_id)
        path = os.path.join(MANAGER_DIR, run_id, "settings.rnqs")
        if run is None or not run.get("randomizer") or not os.path.isfile(path):
            return web.json_response({"ok": False, "error": "no randomized pair for this run"}, status=404)
        safe_name = re.sub(r"[^\w-]", "_", run.get("name") or run_id).strip("_") or run_id
        return web.FileResponse(path, headers={
            "Content-Type": "application/octet-stream",
            "Content-Disposition": f'attachment; filename="slink_{safe_name}.rnqs"',
        })

    async def handle_presets(self, request: web.Request) -> web.Response:
        """GET /api/presets — every saved randomizer preset: {name, spec, updated_at}."""
        return web.json_response({"ok": True, "presets": _load_presets()})

    async def handle_preset_save(self, request: web.Request) -> web.Response:
        """POST /api/presets {name, spec, overwrite?} — save a new preset, or replace an
        existing one only with `overwrite: true` (409 otherwise, so the page can ask first
        rather than silently clobbering someone's saved spec). The spec goes through the
        same builder the randomizer uses, so a saved preset is one it will accept."""
        from server.upr_settings import FAMILIES, UprSettingsError, build_spec, family_spec
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "Invalid JSON"}, status=400)
        name = body.get("name")
        if not isinstance(name, str):
            return web.json_response({"ok": False, "error": "name must be a string"}, status=400)
        name = name.strip()
        if not name or len(name) > 60:
            return web.json_response({"ok": False, "error": "name must be 1-60 characters"}, status=400)
        spec = body.get("spec")
        if not isinstance(spec, dict):
            return web.json_response({"ok": False, "error": "spec is required"}, status=400)
        errors = []
        for family in FAMILIES:          # a preset is saveable when some family builds it
            try:
                build_spec(family_spec(spec, family), family=family)
                break
            except UprSettingsError as exc:
                errors.append(str(exc))
        else:
            return web.json_response({"ok": False, "error": errors[0]}, status=400)
        presets = _load_presets()
        existing = next((p for p in presets if p["name"].lower() == name.lower()), None)
        if existing is not None and not body.get("overwrite"):
            return web.json_response(
                {"ok": False, "error": f'a preset named "{existing["name"]}" already exists',
                 "conflict": True}, status=409)
        preset = {"name": name, "spec": spec, "updated_at": datetime.now(UTC).isoformat()}
        kept = [p for p in presets if p["name"].lower() != name.lower()]
        try:
            _save_presets(kept + [preset])
        except OSError as exc:
            return web.json_response({"ok": False, "error": f"could not save preset: {exc}"}, status=500)
        return web.json_response({"ok": True, "preset": preset})

    async def handle_preset_delete(self, request: web.Request) -> web.Response:
        """POST /api/presets/delete {name}."""
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "Invalid JSON"}, status=400)
        name = str(body.get("name", "")).strip()
        presets = _load_presets()
        kept = [p for p in presets if p["name"].lower() != name.lower()]
        if len(kept) == len(presets):
            return web.json_response({"ok": False, "error": "no such preset"}, status=404)
        try:
            _save_presets(kept)
        except OSError as exc:
            return web.json_response({"ok": False, "error": f"could not delete preset: {exc}"}, status=500)
        return web.json_response({"ok": True})

    async def handle_randomizer_status(self, request: web.Request) -> web.Response:
        """GET /api/randomizer/status?jar=&rom_a=&rom_b= — the checks that cost
        milliseconds, before the ones that cost minutes."""
        from server.upr_pipeline import find_upr_jar, preflight
        q = request.query
        jar = q.get("jar", "").strip() or find_upr_jar() or ""
        sources = {p: q.get(f"rom_{p}", "").strip() for p in ("a", "b")}
        return web.json_response(preflight(jar, sources))

    async def handle_roms(self, request: web.Request) -> web.Response:
        """GET /api/roms — every .gb/.gbc in the SLink folder (and its roms/), each with the
        scanner's verdict, so the run creator can offer them instead of asking for paths.
        Nothing is uploaded to anywhere: the Manager and the ROMs share a machine."""
        from server.upr_pipeline import find_upr_jar
        jar = request.query.get("jar", "").strip() or find_upr_jar() or ""
        roms = await asyncio.to_thread(self._scan_roms, jar)
        return web.json_response({"ok": True, "roms": roms, "dir": PROJECT_ROOT})

    async def handle_rom_upload(self, request: web.Request) -> web.Response:
        """POST /api/roms (multipart `file`) — a ROM chosen with the browser's own file
        dialog lands in <repo>/roms/ (a jar lands as <repo>/PokeRandoZX.jar, where
        find_upr_jar looks first) and the answer describes it like handle_roms would. A
        same-named file that differs is kept: the upload gets a numbered name. A jar whose
        sha256 is not in data/upr_jars.json is refused and never lands: dropped where
        find_upr_jar looks first, it would shadow the pinned fork."""
        from server.upr_pipeline import (
            _sha1,
            describe_rom,
            jar_is_fork,
            jar_is_trusted,
            trusted_jars,
        )
        if request.content_type != "multipart/form-data":
            return web.json_response({"ok": False, "error": "multipart/form-data expected"}, status=400)
        reader = await request.multipart()
        field = await reader.next()
        while field is not None and field.name != "file":
            field = await reader.next()
        name = os.path.basename(field.filename or "") if field is not None else ""
        ext = os.path.splitext(name)[1].lower()
        if not name or ext not in ROM_EXTS + (".jar",):
            return web.json_response({"ok": False, "error": "send a .gb, .gbc, .gba or .jar as `file`"}, status=400)
        name = re.sub(r"[^\w .()\[\]'&+,-]", "_", name)
        if ext == ".jar":
            dest_dir, name = PROJECT_ROOT, "PokeRandoZX.jar"
        else:
            dest_dir = ROM_UPLOAD_DIR
        os.makedirs(dest_dir, exist_ok=True)
        # A unique name per REQUEST, not per process: os.getpid() is the same for every
        # upload this Manager handles, so two concurrent uploads wrote the same .part file
        # and one clobbered the other's bytes (or its os.replace lost the race).
        fd, tmp = tempfile.mkstemp(dir=dest_dir, prefix=".upload-", suffix=".part")
        size, h, h256, too_large = 0, hashlib.sha1(), hashlib.sha256(), False
        try:
            with os.fdopen(fd, "wb") as f:
                while chunk := await field.read_chunk(1 << 20):
                    size += len(chunk)
                    if size > UPLOAD_MAX:
                        too_large = True
                        break
                    h.update(chunk)
                    h256.update(chunk)
                    f.write(chunk)
            if too_large:
                # A plain web.HTTPRequestEntityTooLarge answers 413 with a text body; the
                # page's fetch always parses JSON, so the user saw a parse error instead of
                # the limit.
                return web.json_response({"ok": False, "error": (
                    f"too large: the limit is {UPLOAD_MAX // (1 << 20)} MB")}, status=413)
            if ext == ".jar" and h256.hexdigest() not in trusted_jars():
                return web.json_response({"ok": False, "error": (
                    f"unknown randomizer build (sha256 {h256.hexdigest()}): only the SLink UPR "
                    f"jars in data/upr_jars.json are accepted. Build the fork with "
                    f"`python tools/build_upr_fork.py --pin`.")}, status=400)
            if ext != ".jar":
                # Bytes already in a ROM folder: answer with THAT file. A kept copy in roms/
                # is deduped out of the next scan, and the picker's selection with it.
                roms = await asyncio.to_thread(self._scan_roms, "")
                same = next((r for r in roms if r.get("sha1") == h.hexdigest()), None)
                if same is not None:
                    return web.json_response({"ok": True, "path": same["path"], "kind": "rom",
                                              "rom": same, "existing": True})
            stem, n = os.path.splitext(name)[0], 1
            dest = os.path.join(dest_dir, name)
            while os.path.exists(dest) and _sha1(dest) != h.hexdigest():
                n += 1
                dest = os.path.join(dest_dir, f"{stem} ({n}){ext}")
            os.replace(tmp, dest)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        if ext == ".jar":
            return web.json_response({"ok": True, "path": dest, "kind": "jar",
                                      "jar_trusted": jar_is_trusted(dest), "jar_fork": jar_is_fork(dest)})
        return web.json_response({"ok": True, "path": dest, "kind": "rom",
                                  "rom": {"name": os.path.basename(dest), **describe_rom(dest, True)}})

    async def handle_rom_download(self, request: web.Request) -> web.Response:
        """GET /api/runs/{run_id}/rom/{player} — this player's cartridge as the run made
        it (companion / randomized / both), named slink_<run>_<player>.gb: .gb so BizHawk
        picks the DMG core and finds the battery save."""
        run_id, player = request.match_info["run_id"], request.match_info["player"]
        if player not in ("a", "b"):
            return web.json_response({"ok": False, "error": "player must be 'a' or 'b'"}, status=400)
        run = _find_run(_load_registry(), run_id)
        if run is None:
            return web.json_response({"ok": False, "error": "no cartridges for this run"}, status=404)
        # Strictly THIS player's own recorded output: cartridges (today's shape), then
        # randomizer.players (runs from before the Cartridges step recorded only that). A
        # run-level `randomizer` being truthy says nothing about whether THIS player is in
        # it -- it used to be read as a green light to guess a path for any player.
        path = ((run.get("cartridges") or {}).get("players", {}).get(player, {}).get("output")
                or (run.get("randomizer") or {}).get("players", {}).get(player, {}).get("output"))
        if not path:
            return web.json_response({"ok": False, "error": "no cartridge recorded for this player"}, status=404)
        if not os.path.isfile(path):
            return web.json_response({"ok": False, "error": "ROM file is missing on disk"}, status=404)
        safe_name = re.sub(r"[^\w-]", "_", run.get("name") or run_id).strip("_") or run_id
        # The cartridge keeps its own extension: BizHawk picks the system by it for a ROM
        # its database does not know, and a pure cartridge named .gb runs in mono.
        ext = os.path.splitext(path)[1].lower() or ".gb"
        return web.FileResponse(path, headers={
            "Content-Type": "application/octet-stream",
            "Content-Disposition": _content_disposition(f"slink_{safe_name}_{player}{ext}"),
        })

    # ── Stream pin ─────────────────────────────────────────────────────────────

    async def handle_stream_pin(self, request: web.Request) -> web.Response:
        """POST /api/stream/pin — pin a run as the stream overlay target."""
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "Invalid JSON"}, status=400)
        run_id = body.get("run_id") or None
        if run_id is not None:
            runs = _load_registry()
            if not any(r["run_id"] == run_id for r in runs):
                return web.json_response({"ok": False, "error": "Run not found"}, status=404)
        self._stream_pin_id = run_id
        log.info(f"Stream overlay pin set to: {run_id!r}")
        active = self._active_stream_run()
        return web.json_response({
            "ok": True,
            "pinned": run_id,
            "active_run_id": active["run_id"] if active else None,
        })

    async def handle_stream_pin_status(self, request: web.Request) -> web.Response:
        """GET /api/stream/pin — return current pin and active run."""
        active = self._active_stream_run()
        return web.json_response({
            "pinned": self._stream_pin_id,
            "active_run_id": active["run_id"] if active else None,
            "active_run_name": active.get("name") if active else None,
        })

    # ── Stream overlay pages (served at fixed manager port 8090) ───────────────

    def _rail_ctx(self, request: web.Request, runs: list[dict], *, page: str) -> dict:
        """What _rail.html needs, for the pages that are not the run shell."""
        return {
            "runs": [self._augment_for_template(r) for r in runs],
            "is_admin": request.get("is_admin", True),
            "run": None,
            "page": page, "standalone": False,
            "pinned_run_id": self._stream_pin_id,
            "manager_port": self.manager_port,
            "host": (request.host or "127.0.0.1").split(":")[0] or "127.0.0.1",
        }

    async def handle_stream_index(self, request: web.Request) -> web.Response:
        """GET /broadcast (and /stream for the URL that is pasted into OBS): the overlay
        gallery, wearing the Manager's rail. The gallery lays itself out as the second
        column of whatever grid it sits in; here that is the Manager's .mk shell."""
        ctx = _build_stream_index_context(request)
        rail_ctx = self._rail_ctx(request, self._get(), page="broadcast")
        env = aiohttp_jinja2.get_env(request.app)
        ctx["sidebar_html"] = env.get_template("_rail.html").render(rail_ctx)
        ctx["body_class"] = "board mgr"
        ctx["mgr"] = True
        ctx["active_run_name"] = (self._active_stream_run() or {}).get("name", "")
        ctx["tabs"] = [("Overlays", "/broadcast", True), ("Twitch", "/broadcast/twitch", False),
                       ("OBS", "/broadcast/obs", False)]
        return aiohttp_jinja2.render_template("stream_index.html", request, ctx)

    async def handle_cartridges_page(self, request: web.Request) -> web.Response:
        """GET /runs/{run_id}/cartridges (and the older /randomizer) — a Gen 1 run's
        cartridges: what each player plays, the companion, the randomizer, the downloads."""
        runs = self._get()
        run = _find_run(runs, request.match_info["run_id"])
        if run is None:
            raise web.HTTPNotFound(text="Run not found")
        form = await asyncio.to_thread(self._randomizer_form, run)
        if form is None:
            raise web.HTTPNotFound(text="Cartridges are prepared for Gen 1 runs only")
        ctx = self._rail_ctx(request, runs, page="run")
        ctx.update({
            "page_title": f"Cartridges — {run.get('name', '')}",
            "theme": resolve_theme(request),
            "is_stream": False, "hide_chrome": False,
            "body_class": "board mgr",
            "run": self._augment_for_template(run),
            "randomizer_json": _json_for_script(form),
        })
        return aiohttp_jinja2.render_template("cartridges.html", request, ctx)

    def _run_or_404(self, request: web.Request) -> tuple[list[dict], dict]:
        runs = self._get()
        run = _find_run(runs, request.match_info["run_id"])
        if run is None:
            raise web.HTTPNotFound(text="Run not found")
        return runs, run

    async def _run_panel_ctx(self, request: web.Request, runs, run, *, panel: str, label: str) -> dict:
        ctx = self._rail_ctx(request, runs, page="run")
        base = f"/runs/{run['run_id']}"
        running = run.get("status") == "running"
        show_calc = bool(_calc_profile_for_run(run, await self._run_status(request, run)))
        tabs = [("Board", base, False)]
        if show_calc:
            tabs.append(("Calc", f"{base}/calc/normal.html", panel == "calc"))
        tabs.append(("Debug", f"{base}/debug", panel == "debug"))
        tabs.append(("Timeline", f"{base}/timeline", panel == "timeline"))
        ctx.update({
            "page_title": f"{label} — {run.get('name', '')}",
            "theme": resolve_theme(request),
            "is_stream": False, "hide_chrome": False,
            "body_class": "board mgr" + (" dark-theme calc-host" if panel == "calc" else ""),
            "run": self._augment_for_template(run),
            "panel": panel, "panel_label": label, "base": base, "api_base": base,
            "title": run.get("name", ""), "meta": label,
            "tabs": tabs,
            "show_calc": show_calc,
            "available": running,
            "unavailable_html": (f"This run is not running; {label.lower()} needs its server. "
                                 f"<a href=\"{base}\">Start it from the board</a>."),
        })
        return ctx

    async def handle_run_debug(self, request: web.Request) -> web.Response:
        """GET /runs/{run_id}/debug — the run's debug tools in the Manager's chrome. The
        panel is the run server's own (templates/_debug_panel.html); its calls go through
        handle_run_api, SSE included."""
        runs, run = self._run_or_404(request)
        ctx = await self._run_panel_ctx(request, runs, run, panel="debug", label="Debug")
        return aiohttp_jinja2.render_template("panel_page.html", request, ctx)

    async def handle_run_timeline(self, request: web.Request) -> web.Response:
        """GET /runs/{run_id}/timeline — the run's story, from its status payload: live when
        it is running, what it persisted when it is not (so a stopped run still has one)."""
        from server.board import timeline
        runs, run = self._run_or_404(request)
        ctx = await self._run_panel_ctx(request, runs, run, panel="timeline", label="Timeline")
        status = await self._run_status(request, run)
        ctx.update({"available": True, "story": timeline(status), "players": status.get("players") or {}})
        return aiohttp_jinja2.render_template("panel_page.html", request, ctx)

    async def handle_run_calc(self, request: web.Request) -> web.Response:
        """GET /runs/{run_id}/calc/{path} — the damage calculator for one run, in the
        Manager's chrome. Entry points are wrapped (run_panel.html + _calc_panel.html);
        the calc's own files are served verbatim. The bridge inside the page reads
        SLINK_API_BASE (= /runs/{id}) and so talks to that run through handle_run_api."""
        path = request.match_info.get("path", "") or "normal.html"
        if not path.endswith(".html"):
            return calc_files.file_response(calc_files.resolve(path))
        runs, run = self._run_or_404(request)
        ctx = await self._run_panel_ctx(request, runs, run, panel="calc", label="Calc")
        try:
            abs_path = calc_files.resolve(path)
        except web.HTTPNotFound:
            # The entry points live in calc/dist, a build product: the page still wears
            # the chrome and says what to run, rather than 404ing the whole run page.
            abs_path = None
            note = ("The calculator is not built on this machine: run <code>cd calc &amp;&amp; npm install "
                    "&amp;&amp; npm run build</code> (docs/REFERENCE.md, Damage calculator) and reload.")
            # a stopped run's reason comes first; the build note follows it
            ctx["unavailable_html"] = note if ctx["available"] else ctx["unavailable_html"] + " " + note
            ctx["available"] = False
        ctx.update({
            "calc_body_html": calc_files.page_body(abs_path) if abs_path else "",
            "calc_mode_label": calc_files.mode_label(path),
            "status_href": f"/runs/{run['run_id']}",
        })
        resp = aiohttp_jinja2.render_template("panel_page.html", request, ctx)
        resp.headers["Cache-Control"] = "no-cache"
        return resp

    async def handle_calc_asset(self, request: web.Request) -> web.Response:
        """GET /calc/{path} — the calc's absolute-path assets (its stylesheets link to
        /calc/css/…), for the page served under /runs/{id}/calc/."""
        return calc_files.file_response(calc_files.resolve(request.match_info.get("path", "")))

    async def handle_run_api(self, request: web.Request) -> web.StreamResponse:
        """/runs/{run_id}/api/{tail} (GET or POST) — relayed verbatim to THAT run, so a
        run's own panels (debug, calc bridge) work from the Manager's origin. The SSE
        stream (/api/events) is piped through chunk by chunk."""
        run = _find_run(_load_registry(), request.match_info["run_id"])
        if run is None or run.get("status") != "running" or not run.get("http_port"):
            return web.json_response({"ok": False, "error": "run not running"}, status=404)
        qs = request.url.query_string
        url = (f"http://127.0.0.1:{run['http_port']}/api/{request.match_info['tail']}"
               + (f"?{qs}" if qs else ""))
        session = request.app["proxy_session"]
        try:
            up = await session.request(
                request.method, url, data=await request.read(),
                headers=_internal_admin_headers({
                    "Content-Type": request.headers.get("Content-Type", "application/json"),
                    "Accept": request.headers.get("Accept", "*/*"),
                }),
                timeout=aiohttp.ClientTimeout(total=None, sock_read=None))
        except Exception as e:
            log.debug(f"Proxy {request.path} failed: {e}")
            return web.json_response({"ok": False, "error": "proxy_failed"}, status=503)
        ct = up.headers.get("Content-Type", "application/json")
        try:
            if ct.startswith("text/event-stream"):
                resp = web.StreamResponse(status=up.status, headers={
                    "Content-Type": ct, "Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
                await resp.prepare(request)
                async for chunk in up.content.iter_any():
                    await resp.write(chunk)
                return resp
            body = await up.read()
            return web.Response(body=body, status=up.status, content_type=ct.split(";")[0])
        except (ConnectionResetError, asyncio.CancelledError):
            raise
        finally:
            up.close()

    async def handle_broadcast_panel(self, request: web.Request) -> web.Response:
        """GET /broadcast/twitch and /broadcast/obs — the active run's Twitch bot and OBS
        scene triggers in the Manager's chrome. The panel partials are the run server's own
        (templates/_{tab}_panel.html); their JS calls /api/bot/* and /api/obs/* on this
        origin, which handle_proxy_api relays to that run."""
        tab = request.match_info["tab"]
        runs = self._get()
        ctx = self._rail_ctx(request, runs, page="broadcast")
        active = self._active_stream_run()
        label = {"twitch": "Twitch bot", "obs": "OBS triggers"}[tab]
        ctx.update({
            "page_title": "Broadcast — Soul Link",
            "theme": resolve_theme(request),
            "is_stream": False, "hide_chrome": False,
            "body_class": "board mgr",
            "tab": tab, "panel": tab, "panel_label": label, "api_base": "",
            "title": "Broadcast", "meta": label + (f" · {active['name']}" if active else ""),
            "tabs": [("Overlays", "/broadcast", False), ("Twitch", "/broadcast/twitch", tab == "twitch"),
                     ("OBS", "/broadcast/obs", tab == "obs")],
            "available": active is not None,
            "unavailable_html": ("No running run to broadcast. <a href=\"/\">Start one</a> — "
                                 "the pinned run's bot and triggers appear here."),
        })
        return aiohttp_jinja2.render_template("panel_page.html", request, ctx)

    async def handle_proxy_api(self, request: web.Request) -> web.Response:
        """/api/bot/* and /api/obs/* (GET or POST) — relayed verbatim to the active run,
        so the Twitch and OBS panels work unchanged from the Manager's origin."""
        active = self._active_stream_run()
        if active is None:
            return web.json_response({"ok": False, "error": "No active run"}, status=503)
        qs = request.url.query_string
        url = f"http://127.0.0.1:{active['http_port']}{request.path}" + (f"?{qs}" if qs else "")
        try:
            async with request.app["proxy_session"].request(
                request.method, url, data=await request.read(),
                headers=_internal_admin_headers({
                    "Content-Type": request.headers.get("Content-Type", "application/json"),
                }),
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                body = await resp.read()
                ct = resp.headers.get("Content-Type", "application/json")
                return web.Response(body=body, status=resp.status, content_type=ct.split(";")[0])
        except Exception as e:
            log.debug(f"Proxy {request.path} failed: {e}")
            return web.json_response({"ok": False, "error": "proxy_failed"}, status=503)

    async def handle_tools_page(self, request: web.Request) -> web.Response:
        """GET /tools — the patcher and the randomized-pair builder."""
        runs = self._get()
        ctx = self._rail_ctx(request, runs, page="tools")
        ctx.update({
            "page_title": "Tools — Soul Link",
            "theme": resolve_theme(request),
            "is_stream": False, "hide_chrome": False,
            "body_class": "board mgr",
            "gen1_runs": [self._augment_for_template(r) for r in runs
                          if r.get("game") in new_run_form()["randomizer_games"] and r.get("status") != "archived"],
        })
        return aiohttp_jinja2.render_template("tools.html", request, ctx)

    async def handle_stream_overlay_proxy(self, request: web.Request) -> web.Response:
        """GET /stream/{name} (and /stream/{name}/fragment) on the manager —
        proxy through to the active run's overlay so the gallery preview iframe
        and OBS browser sources can target a stable manager URL regardless of
        which run is currently pinned. The fragment variant is required because
        every overlay's `_base.html` polls `fragment_url = /stream/{slug}/fragment`
        via HTMX every 2 s — without the fragment route, the initial paint shows
        but the page never updates.

        If no run is active, return a friendly 404 instead of 500.
        """
        name = request.match_info["name"]
        # The fragment route reuses this handler; aiohttp's match_info exposes
        # the suffix path (empty for the page route, "/fragment" for the poll).
        suffix = "/fragment" if request.match_info.get("suffix") else ""
        active = self._active_stream_run()
        if active is None:
            return web.Response(
                status=404,
                content_type="text/html",
                text=(
                    "<!DOCTYPE html><html><body style=\"font-family:'Pixelify Sans',monospace;"
                    "background:#070910;color:#e6e6e6;padding:2em;text-align:center\">"
                    "<h2 style=\"color:#f8a020\">No active run</h2>"
                    "<p>Pin a running run on <a href=\"/\" style=\"color:#6af\">the manager</a> "
                    "to serve overlays here.</p></body></html>"
                ),
            )
        # Preserve query string (?theme=…, ?layout=…, etc.) when proxying.
        qs = request.url.query_string
        target = (f"http://127.0.0.1:{active['http_port']}/stream/{name}{suffix}"
                  + (f"?{qs}" if qs else ""))
        try:
            async with request.app["proxy_session"].get(
                target, timeout=aiohttp.ClientTimeout(total=5)
            ) as resp:
                body = await resp.read()
                ct = resp.headers.get("Content-Type", "text/html")
                return web.Response(body=body, status=resp.status, content_type=ct.split(";")[0])
        except Exception as e:
            log.debug(f"Proxy /stream/{name}{suffix} → run {active['run_id']} failed: {e}")
            return web.Response(status=502, text=f"Upstream run {active['run_id']} unreachable")

    # ── API proxy endpoints (relay to active run) ──────────────────────────────

    async def handle_proxy_status(self, request: web.Request) -> web.Response:
        """GET /api/status — proxy to the active run or return empty status."""
        active = self._active_stream_run()
        if active is None:
            return web.json_response(empty_status_payload())
        return web.json_response(await self._fetch_live(request, active) or empty_status_payload())

    async def _fetch_live(self, request: web.Request, run: dict) -> dict | None:
        """One run's /api/status, or None if it did not answer in time."""
        url = f"http://127.0.0.1:{run['http_port']}/api/status"
        try:
            async with request.app["proxy_session"].get(
                url, timeout=aiohttp.ClientTimeout(total=3)
            ) as resp:
                return await resp.json(content_type=None)
        except Exception as e:
            log.debug(f"live status for {run['run_id']} failed: {e}")
            return None

    async def handle_run_live(self, request: web.Request) -> web.Response:
        """GET /api/runs/{run_id}/live — same-origin proxy to a specific run's /api/status
        JSON. 404 if the run is unknown or stopped, 502 if it did not answer."""
        run = _find_run(_load_registry(), request.match_info.get("run_id", ""))
        if run is None or run.get("status") != "running" or not run.get("http_port"):
            return web.json_response({"error": "run not running"}, status=404)
        data = await self._fetch_live(request, run)
        if data is None:
            return web.json_response({"error": "run unreachable"}, status=502)
        return web.json_response(data)

    async def handle_proxy_attempts(self, request: web.Request) -> web.Response:
        """POST /api/attempts — proxy to the active run."""
        active = self._active_stream_run()
        if active is None:
            return web.json_response({"ok": False, "error": "No active run"}, status=503)
        url = f"http://127.0.0.1:{active['http_port']}/api/attempts"
        try:
            body = await request.read()
            async with request.app["proxy_session"].post(
                url,
                data=body,
                headers=_internal_admin_headers({"Content-Type": "application/json"}),
                timeout=aiohttp.ClientTimeout(total=3),
            ) as resp:
                data = await resp.json(content_type=None)
                return web.json_response(data, status=resp.status)
        except Exception as e:
            log.debug(f"Proxy /api/attempts failed: {e}")
            return web.json_response({"ok": False, "error": "proxy_failed"}, status=503)


# ── Entry point ─────────────────────────────────────────────────────────────

async def main(host: str, port: int, public_host: str = ""):
    manager = RunManager(bind_host=host, manager_port=port, public_host=public_host)
    app = web.Application(middlewares=[csrf_protection, theme_cache, registry_errors])
    setup_templating(app)
    setup_admin_auth(app, _manager_public_get)

    # Run-management routes
    app.router.add_get("/",                           manager.handle_index)
    app.router.add_get("/healthz",                    manager.handle_healthz)
    app.router.add_get("/readyz",                     manager.handle_readyz)
    app.router.add_get("/new",                        manager.handle_new_page)
    app.router.add_get("/runs/{run_id}",              manager.handle_run_page)
    app.router.add_get("/runs/{run_id}/board",        manager.handle_run_board)
    app.router.add_get("/runs/{run_id}/cartridges",   manager.handle_cartridges_page)
    app.router.add_get("/runs/{run_id}/randomizer",   manager.handle_cartridges_page)
    app.router.add_get("/api/runs",                   manager.handle_list)
    app.router.add_post("/api/runs/new",              manager.handle_new)
    app.router.add_post("/api/runs/{run_id}/start",   manager.handle_start)
    app.router.add_post("/api/runs/{run_id}/stop",    manager.handle_stop)
    app.router.add_post("/api/runs/{run_id}/archive", manager.handle_archive)
    app.router.add_post("/api/runs/{run_id}/delete",  manager.handle_delete)
    app.router.add_get("/api/runs/{run_id}/launcher/{player}", manager.handle_launcher)
    app.router.add_get("/api/runs/{run_id}/player-pack/{player}", manager.handle_player_pack)
    app.router.add_post("/api/settings/public-host", manager.handle_public_host)
    app.router.add_post("/api/runs/{run_id}/cartridges", manager.handle_cartridges)
    app.router.add_post("/api/runs/{run_id}/randomize", manager.handle_randomize)
    app.router.add_get("/api/runs/{run_id}/rom/{player}", manager.handle_rom_download)
    app.router.add_get("/api/randomizer/status",      manager.handle_randomizer_status)
    app.router.add_post("/api/randomizer/settings/export", manager.handle_settings_export)
    app.router.add_post("/api/randomizer/settings/import", manager.handle_settings_import)
    app.router.add_get("/api/runs/{run_id}/settings.rnqs", manager.handle_run_settings)
    app.router.add_get("/api/presets",                manager.handle_presets)
    app.router.add_post("/api/presets",               manager.handle_preset_save)
    app.router.add_post("/api/presets/delete",        manager.handle_preset_delete)
    app.router.add_get("/api/roms",                   manager.handle_roms)
    app.router.add_post("/api/roms",                  manager.handle_rom_upload)
    app.router.add_get("/api/runs/{run_id}/live",     manager.handle_run_live)

    # Stream pin API
    app.router.add_get("/api/stream/pin",  manager.handle_stream_pin_status)
    app.router.add_post("/api/stream/pin", manager.handle_stream_pin)

    # Stream overlay gallery — fixed at manager port 8090. The /stream/{name}
    # proxy relays to the active run's HTTP port so OBS browser sources can
    # bookmark a stable URL even if the pinned run changes.
    app.router.add_get("/broadcast",                       manager.handle_stream_index)
    app.router.add_get("/tools",                           manager.handle_tools_page)
    app.router.add_get("/stream",                          manager.handle_stream_index)
    app.router.add_get("/stream/",                         manager.handle_stream_index)
    app.router.add_get("/stream/{name}",                   manager.handle_stream_overlay_proxy)
    # Fragment route — the per-run overlays' HTMX bodies poll this every 2 s
    # via `fragment_url = /stream/{slug}/fragment`. Without it the manager
    # returns 404 on every poll, and the overlay never updates after first paint.
    app.router.add_get("/stream/{name}/{suffix:fragment}", manager.handle_stream_overlay_proxy)

    # API proxy — relays to the active (pinned or latest) run
    app.router.add_get("/api/status",         manager.handle_proxy_status)
    app.router.add_post("/api/attempts",      manager.handle_proxy_attempts)
    # A run's secondary pages in the Manager's chrome, and the per-run relay their JS uses.
    app.router.add_get("/runs/{run_id}/debug",            manager.handle_run_debug)
    app.router.add_get("/runs/{run_id}/timeline",         manager.handle_run_timeline)
    app.router.add_get("/runs/{run_id}/calc",             manager.handle_run_calc)
    app.router.add_get("/runs/{run_id}/calc/{path:.*}",   manager.handle_run_calc)
    app.router.add_get("/calc/{path:.*}",                 manager.handle_calc_asset)
    app.router.add_get("/runs/{run_id}/api/{tail:.*}",    manager.handle_run_api)
    app.router.add_post("/runs/{run_id}/api/{tail:.*}",   manager.handle_run_api)
    # The Twitch and OBS panels under /broadcast/* keep their own JS; their calls land here.
    app.router.add_get("/broadcast/{tab:twitch|obs}", manager.handle_broadcast_panel)
    for prefix in ("/api/bot/{tail:.*}", "/api/obs/{tail:.*}"):
        app.router.add_get(prefix,  manager.handle_proxy_api)
        app.router.add_post(prefix, manager.handle_proxy_api)

    # Companion ROM patcher — global setup tool, reachable from the manager too.
    # The manager hosts the page itself, so manager_port=None (Manager nav item
    # would dead-link to self) and tcp_port=None (not tied to a run).
    from server.patcher import setup_patcher_routes

    def _patcher_chrome(request: web.Request) -> dict:
        env = aiohttp_jinja2.get_env(request.app)
        rail = env.get_template("_rail.html").render(
            manager._rail_ctx(request, manager._get(), page="tools"))
        return {"sidebar_html": rail, "body_class": "board mgr",
                "mgr": True, "is_stream": False, "hide_chrome": False}
    setup_patcher_routes(app, _patcher_chrome)

    # Lifecycle: shared aiohttp ClientSession for proxy requests
    async def _startup(app: web.Application) -> None:
        app["proxy_session"] = aiohttp.ClientSession()
        await manager.restore_desired_runs()

    async def _cleanup(app: web.Application) -> None:
        await manager.stop_child_processes()
        session = app.get("proxy_session")
        if session and not session.closed:
            await session.close()

    app.on_startup.append(_startup)
    app.on_cleanup.append(_cleanup)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    display = "localhost" if host in ("0.0.0.0", "127.0.0.1") else host
    log.info(f"SLink Manager running at http://{display}:{port}/")
    log.info(f"Stream overlays at http://{display}:{port}/stream (fixed port — safe for OBS)")

    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )
    parser = argparse.ArgumentParser(description="SLink Run Manager")
    parser.add_argument("--host", default="0.0.0.0", help="Bind address (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=MANAGER_HTTP_PORT,
                        help=f"Manager HTTP port (default: {MANAGER_HTTP_PORT})")
    parser.add_argument("--data-dir", default=os.environ.get("SLINK_DATA_DIR"),
                        help="Where runs live (default: data/runs). A fresh directory is a fresh Manager")
    parser.add_argument("--allow-host", action="append", default=[], metavar="NAME",
                        help="Extra Host name the web UI answers to, e.g. a tunnel name or '*.<tailnet>.ts.net' "
                             "(repeatable; also SLINK_ALLOWED_HOSTS, comma-separated). Runs inherit it")
    parser.add_argument("--public-host", default=os.environ.get("SLINK_PUBLIC_HOST", ""), metavar="ADDRESS",
                        help="Address players' launchers connect to (default: this machine's LAN address; "
                             "also settable on a run page)")
    args = parser.parse_args()
    if args.public_host and not _HOST_RE.fullmatch(args.public_host):
        parser.error(f"--public-host {args.public_host!r} is not a host name or IP address")
    allow_hosts(args.allow_host)
    if args.data_dir:
        MANAGER_DIR = os.path.abspath(args.data_dir)
        REGISTRY_PATH = os.path.join(MANAGER_DIR, "registry.json")
        os.makedirs(MANAGER_DIR, exist_ok=True)
    asyncio.run(main(args.host, args.port, args.public_host))
