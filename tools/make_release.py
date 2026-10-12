#!/usr/bin/env python3
"""
tools/make_release.py — Build a player-facing SLink release package.

Creates dist/SLink-player-<version>.zip containing only the files a
non-hosting player needs to run SLink in BizHawk:
  - lua/   (clients, shared modules, area tables — no tests)
  - data/games/<gen>/  (area/location tables and the Gen 1 client's JSON data,
    loaded via _proj_root path)
  - PLAYER_SETUP.md
  - LICENSE, NOTICE.md

The server (Python), test suite, code-generation tools, and server-only
JSON data files are intentionally excluded.

Optionally (RR players) it also bundles, under companion/:
  - patch/dist/SLink-RR.ups   (the native code-injection patch)   [--with-patch]
  - the patch guide
  - a pre-patched ROM                                              [--rom <path>]

Every companion shows "SoulLink <version>" on its game's main menu, so a release must carry its own version:
run `python tools/stamp_release.py --version vX.Y.Z` first (it rebuilds and re-pins every companion; commit the result, then
tag). Bundling companions that patch/dist/companion_version.json does not vouch for under this --version is refused
(--allow-unstamped-companions overrides).

Usage:
    python tools/make_release.py
    python tools/make_release.py --version 1.2.3
    python tools/make_release.py --version 1.2.3 --out dist/
    python tools/make_release.py --host 192.168.1.10 --port 54321 --player b
    python tools/make_release.py --with-patch
    python tools/make_release.py --rom patch/build/slink_RR.gba   # patch + ROM

The optional --host/--port/--player flags bake connection settings directly
into the launcher scripts (slink_gen*.lua) as a convenience. The recommended
flow is for players to download a pre-configured launcher from the host's
status page instead.
"""

import argparse
import json
import re
import subprocess
import sys
import zipfile
from pathlib import Path

# ── Root of the SLink project ──────────────────────────────────────────────────
REPO_ROOT = Path(__file__).resolve().parent.parent

# ── Generators to run before packaging ────────────────────────────────────────
# These produce the area/location .lua files under lua/ and data/games/. Those files ARE
# git-tracked (an earlier version of this comment claimed otherwise), so running the
# generators before a release is a check that the committed tables match their sources, not
# the step that creates them.
GENERATORS: list[tuple[str, str]] = [
    ("tools/gen_gen2_area_map.py", "Gen 2 Crystal area tables"),
    ("tools/gen_area_map.py",      "Gen 3 FRLGE area tables"),
    # Note: gen_area_map.py --game emerald regenerates the separate gen3_emerald area tables;
    # it is not run by this generator loop (Emerald's tables ship as static manifest entries).
    # Writes data/games/gen4_hgss/{area_map,locations}.json -- the files lua/gen4/inputs.lua
    # opens for area ids and location names. gen4_hge gets NOTHING from it, by proof (the fork
    # declares no MAPSEC_*), so there is no hge row here; same shape as the Emerald note above.
    ("tools/gen_gen4_area_map.py", "Gen 4 HGSS area/location tables (data/games/gen4_hgss)"),
    ("tools/gen_gen5_area_map.py", "Gen 5 BW/BW2 area tables"),
]

# ── File manifests ────────────────────────────────────────────────────────────
# All entries are required; build fails if any are missing.

# lua/ root level
_LUA_ROOT = [
    "slink.lua",
    "slink_gen1.lua",
    "slink_gen3.lua",
    "slink_gen5.lua",
    "connector.lua",
    "game_detect.lua",
    "hud.lua",
    "memory_nds.lua",
    # lua/gen2/panel.lua dofiles ../sfx_arbiter.lua (one sound cue per frame).
    "sfx_arbiter.lua",
    "socket.lua",
    "json_codec.lua",
    # The Gen 1 client's closure: entry.lua dofiles these shared modules off the repo root.
    "gen1_write_safety.lua",
    # The Gen 2 client's own write-safety module (lua/gen2/entry.lua dofiles it off the
    # repo root, same idiom as gen1_write_safety.lua above).
    "gen2_write_safety.lua",
    "write_permit.lua",
    "gb_checkpoint.lua",
    # lua/gen1/panel.lua dofiles its sibling ../gb_panel.lua (the shared GB panel, P4.1d).
    "gb_panel.lua",
    # lua/gen1/trade_overlay.lua dofiles ../gb_trade_lease.lua (the shared GB trade lease, P4.3c).
    "gb_trade_lease.lua",
    "token_scanner.lua",
    "admission.lua",
    "hook_registry.lua",
    "gb_hook_binding.lua",
    "hello_session.lua",
    "reply_dispatch.lua",
    # the GB clients' owed trade reports (lua/gen1/entry.lua, lua/gen2/entry.lua; review MAJOR-1)
    "owed_reports.lua",
    # Gen 3/4/5 area tables live in data/games/<gen>/ (loaded via _proj_root)
]

# lua/gen1/ — the Gen 1 client. run.lua is what the launchers load; everything else is
# pulled in by entry.lua's composition root, so the whole directory ships or none of it does.
_LUA_GEN1 = [
    "run.lua",
    "entry.lua",
    "client.lua",
    "reads.lua",
    "signals.lua",
    "writes.lua",
    "boxes.lua",
    "rom.lua",
    "trade_overlay.lua",
    "panel.lua",
]

# lua/gen3/ — the rewritten Gen 3 (FRLG/RR/Emerald) client. run.lua is what lua/slink.lua's Gen 3 route
# dofiles; everything else is pulled in by entry.lua's composition root (mirrors _LUA_GEN1).
# shadow_run.lua is P3's observer-only bootstrap (not reachable from a production launcher)
# and deliberately excluded, same as the Gen 1 manifest excludes nothing analogous to it.
_LUA_GEN3 = [
    "run.lua",
    "entry.lua",
    "client.lua",
    "reads.lua",
    "signals.lua",
    "writes.lua",
    "safety.lua",
    "boxes.lua",
    "native.lua",   # RR companion mailbox part; Entry builds it for the companion kind (C4-7)
    "trade.lua",
    "trade_journal.lua",
    "report_outbox.lua",
    "rom_content.lua",
]

# lua/core/ — the shared client core (session/identity/deferred) Gen 3 binds first (P4 C4-1);
# Gen 1 re-binds later. lua/gen1/* stays on its own copies until then.
_LUA_CORE = [
    "session.lua",
    "identity.lua",
    "deferred.lua",
]

# lua/gen2/ — the Gen 2 client (Crystal, Gold and Silver; U5 cutover). run.lua is what lua/slink.lua
# dofiles; boxes.lua is NOT part of this graph (no box executor is composed, B-10).
_LUA_GEN2 = [
    "artifact.lua",
    "run.lua",
    "entry.lua",
    "client.lua",
    "reads.lua",
    "writes.lua",
    "rom.lua",
    "signals.lua",
    "wire.lua",
    "boxes.lua",
    "panel.lua",  # P4.1f: entry.lua composes it (production); it dofiles ../gb_panel.lua
    "phone.lua",  # P4.5c: entry.lua composes it beside the panel
    "trade_overlay.lua",  # P4.3b: entry.lua composes it on a trade build; dofiles ../gb_trade_lease.lua
    # Polished Crystal (dev-grade overlay admission): entry.lua compose_polished loads these by path.
    "polished.lua",
    "polished_boxes.lua",
    "polished_overworld.lua",  # C-WRITE: entry.lua compose_polished loads the predicate-hold writer by path
    "polished_explode.lua",  # C-EXPLODE: entry.lua compose_polished loads the battle facade by path
    "polished_writes.lua",  # C-EXPLODE: polished_explode.lua dofiles the W-3/W-4 battle writers (the shared Permit is injected, not loaded)
    "polished_sounds.lua",  # POL-SOUNDS: wraps panel.lua's binder; dofiles ../write_permit.lua and ../gb_panel.lua
    "polished_stats.lua",  # POL-WDLUA: pure savemon->party reconstruction; a future composition loads it by path
    "polished_rival.lua",  # C-RIVAL: entry.lua compose_polished loads the rival-swap writer by path
    "polished_trade.lua",  # C3/C4: entry.lua loads the dev-only trade binder by path (deps.polished_trade_dev)
]

# lua/gen4/ — the rewritten Gen 4 client (HGSS + hg-engine). run.lua is what lua/slink.lua's Gen 4
# route dofiles; entry.lua's composition root pulls in the rest (mirrors _LUA_GEN3). That is the
# WHOLE directory, and deliberately so: a missing module here is a dofile error in BizHawk's
# console, not a degraded feature, so the directory ships or none of it does.
_LUA_GEN4 = [
    "run.lua",
    "entry.lua",
    "client.lua",
    "inputs.lua",
    "reads.lua",
    "pk4.lua",
    "safety.lua",
    "poll_events.lua",
    "companion.lua",  # C2 companion binder; not loaded by run.lua yet, ships with the directory
]

# lua/nds/ — the shared NDS modules the Gen 4 graph loads. entry.lua Entry.build binds the
# binding/phase pair and injects pkm45_crypto as Pk4.crypto; hook_binding.lua dofiles
# residency_contract.lua off its own directory (the one edge no static path scan can see).
# native_witness.lua is NOT here: it is the Gen 3 companion mailbox reader (docs/shared-nds-witness.md)
# and nothing in the Gen 4 graph loads it.
_LUA_NDS = [
    "hook_binding.lua",
    "mailbox.lua",  # the shared envelope reader lua/gen4/companion.lua is handed (injected, never required)
    "phase_signals.lua",
    "pkm45_crypto.lua",
    "residency_contract.lua",
]

# lua/clients/ -- the retired Gen 4 client (clients/gen4_hgsspt_client.lua) is DELETED; lua/slink.lua's
# Gen 4 route dofiles lua/gen4/run.lua instead.
_LUA_CLIENTS = [
    "gen5_bw_client.lua",
]

# lua/games/ -- likewise lua/games/gen4_hgsspt.lua, the only requirer of the gen4_hgsspt_*.lua tables.
_LUA_GAMES = [
    "gen5_bw.lua",
]

# Every lua/ subtree the release ships, in ONE table. build_release's pre-flight and its ZIP
# writer both walk this, so a row can never be pre-flighted and then forgotten at zip time (or the
# reverse). Key "" is lua/'s own root; the others are the directory under lua/.
# NOT named _LUA_*: tests/unit/test_check_release_zip.py::_every_make_release_lua_list_is_an_expected_member
# globs `_LUA_[A-Z0-9]+` and iterates each one's values as FILENAMES, which a dir->files map is not.
_MANIFEST_TREES: dict[str, list[str]] = {
    "": _LUA_ROOT,
    "gen1": _LUA_GEN1,
    "gen3": _LUA_GEN3,
    "core": _LUA_CORE,
    "gen2": _LUA_GEN2,
    "gen4": _LUA_GEN4,
    "nds": _LUA_NDS,
    "clients": _LUA_CLIENTS,
    "games": _LUA_GAMES,
}


def _lua_rel(sub: str, fname: str) -> str:
    """Repo-relative path of one manifest row: `lua/<sub>/<fname>`, or `lua/<fname>` for the root."""
    return f"lua/{sub}/{fname}" if sub else f"lua/{fname}"


# data/games/<gen>/ — data files loaded at runtime via _proj_root path.
# Mostly area/location .lua tables; the Gen 1 NEW client reads five JSONs directly
# (lua/gen1/entry.lua Entry.build) and nothing else here, and the rest of data/ stays
# server-only. The old gen1_rby_{areas,locations}.lua rows went with the old client.
_DATA_GAME_LUA: dict[str, list[str]] = {
    "gen1_rby": [
        # Read by lua/gen1/entry.lua: memory profile, engine signal sites, the write
        # checkpoint, area names and the scripted-encounter table.
        "profile.json",
        "engine_signals.json",
        "write_checkpoint.json",
        "area_map.json",
        "static_encounters.json",
    ],
    "gen1_purergb": [
        # The pureRGB pack the same client loads when the cartridge sha1 admits a pure title.
        "admission.json",
        "area_map.json",
        "charmap.lua",
        "engine_signals.json",
        "profile.json",
        "species_index.json",
        "static_encounters.json",
        "write_checkpoint.json",
        # The SLink companion overlay's own pack files (PLAN M3), selected by admission kind.
        "admission_overlay.json",
        "engine_signals_overlay.json",
        "profile_overlay.json",
        "write_checkpoint_overlay.json",
    ],
    "gen2_crystal": [
        "overlay/binding.json",
        "overlay/beacon.json",
        "receipts/overlay/crystal.engine_sites.json",
        "receipts/overlay/crystal.write_window.json",
        "receipts/overlay/crystal_battle.qualification.json",
        "receipts/overlay/crystal_town.qualification.json",
        "receipts/overlay/crystal_synth_grass.synth.json",
        "receipts/overlay/crystal_synth_kyle.synth.json",
        "receipts/overlay/crystal_synth_bill.synth.json",
        # lua/gen2/entry.lua Entry.PACK_FILES.gen2_crystal (Entry.build reads these at load,
        # for every title -- the admission catalog walks Crystal/Gold/Silver together).
        "profile.json",
        "admission.json",
        "engine_signals.json",
        "write_checkpoint.json",
        "area_map.json",
        "static_encounters.json",
        "encounter_tables.json",
        "species_index.json",
        "evolutions.json",
        "gifts.json",
        "moves.json",
        "trainers.json",
        "map_names.json",
        "items.json",
        "charmap.lua",
        # Entry.RECEIPT_FILES.gen2_crystal -- the shipped O-22 proofs Entry.build
        # re-validates before admitting Crystal. Gold/Silver ship none (G1 PENDING).
        "receipts/crystal.engine_sites.json",
        "receipts/crystal.write_window.json",
        "receipts/crystal_battle.qualification.json",
        "receipts/crystal_town.qualification.json",
        "receipts/crystal_synth_grass.synth.json",
        "receipts/crystal_synth_kyle.synth.json",
        "receipts/crystal_synth_bill.synth.json",
    ],
    "gen2_gold": [
        "overlay/binding.json",
        "overlay/beacon.json",
        "receipts/overlay/gold.engine_sites.json",
        "receipts/overlay/gold.write_window.json",
        "receipts/overlay/gold_battle.qualification.json",
        "receipts/overlay/gold_town.qualification.json",
        "receipts/overlay/gold_battle_errand.qualification.json",
        "receipts/overlay/gold_synth_grass.synth.json",
        "receipts/overlay/gold_synth_kyle.synth.json",
        "receipts/overlay/gold_synth_bill.synth.json",
        # Entry.PACK_FILES.gen2_gold -- Entry.build's admission catalog loads these for
        # every title, whether or not that title is currently ADMITTED.
        "profile.json",
        "admission.json",
        "engine_signals.json",
        "write_checkpoint.json",
        "area_map.json",
        "static_encounters.json",
        "encounter_tables.json",
        "species_index.json",
        "evolutions.json",
        "gifts.json",
        "moves.json",
        "trainers.json",
        "map_names.json",
        "items.json",
        "charmap.lua",
        # Entry.RECEIPT_FILES.gen2_gold (O-22 admission), the shipped proofs Entry.build
        # re-validates before admitting Gold.
        "receipts/gold.engine_sites.json",
        "receipts/gold.write_window.json",
        "receipts/gold_battle.qualification.json",
        "receipts/gold_battle_errand.qualification.json",
        "receipts/gold_town.qualification.json",
        "receipts/gold_synth_grass.synth.json",
        "receipts/gold_synth_kyle.synth.json",
        "receipts/gold_synth_bill.synth.json",
    ],
    "gen2_silver": [
        "overlay/binding.json",
        "overlay/beacon.json",
        "receipts/overlay/silver.engine_sites.json",
        "receipts/overlay/silver.write_window.json",
        "receipts/overlay/silver_battle.qualification.json",
        "receipts/overlay/silver_town.qualification.json",
        "receipts/overlay/silver_synth_grass.synth.json",
        "receipts/overlay/silver_synth_kyle.synth.json",
        "receipts/overlay/silver_synth_bill.synth.json",
        # Entry.PACK_FILES.gen2_silver -- same as Gold.
        "profile.json",
        "admission.json",
        "engine_signals.json",
        "write_checkpoint.json",
        "area_map.json",
        "static_encounters.json",
        "encounter_tables.json",
        "species_index.json",
        "evolutions.json",
        "gifts.json",
        "moves.json",
        "trainers.json",
        "map_names.json",
        "items.json",
        "charmap.lua",
        # Entry.RECEIPT_FILES.gen2_silver (O-22+O-23 admission) -- Silver's U2 write-window
        # IS Gold's receipt (its checkpoint rows are identical), so it ships its own
        # engine-sites + battle qualification plus Gold's write-window and the two Gold
        # qualification reports it binds to.
        "receipts/silver.engine_sites.json",
        "receipts/gold.write_window.json",
        "receipts/gold_battle.qualification.json",
        "receipts/gold_town.qualification.json",
        "receipts/silver_battle.qualification.json",
        "receipts/silver_town.qualification.json",
        "receipts/silver_synth_grass.synth.json",
        "receipts/silver_synth_kyle.synth.json",
        "receipts/silver_synth_bill.synth.json",
    ],
    "polished_crystal": [
        # lua/gen2/entry.lua Entry.PACK_FILES.polished_crystal (the dev pack: only what Entry.admit_polished and
        # compose_polished read). Additive: no existing title's rows change.
        "profile.json",
        "charmap.lua",
        "evolutions.json",
        "area_map.json",
        "items.json",  # compose_polished: mail_ids for the box_mon mail refusal (ItemIsMail set)
        "species_index.json",  # compose_polished: base stats + variant records for the party_mon stat rebuild
        "moves.json",  # compose_polished: base PP per move for the party_mon PP rebuild
        "engine_signals.json",  # Signals.new_polished (C-SITES) loads the site pack
        "overlay/beacon.json",  # P.admit's rand_overlay gate re-hashes the overlay spans (tools/gen_polished_beacon.py)
    ],
    "gen3_frlge": [
        "gen3_frlge_areas.lua",
        "gen3_frlge_locations.lua",
        # Read by lua/gen3/entry.lua Entry.PACK_FILES[*].area_map for the gen3_frlg and gen3_rr
        # packs (RR is a FireRed map hack sharing this table): "group:num" -> area id.
        # gen3_emerald does NOT use this file; it has its own area_map.json (see "gen3_emerald" below).
        "area_map.json",
    ],
    "gen3_frlg": [
        # Read by lua/gen3/entry.lua Entry.build: memory profile, engine signal sites (also
        # read by Entry.admit/admission_table for every pack, so gen3_rr's ship too even on a
        # gen3_frlg cartridge) and the write checkpoint.
        "profile.json",
        "engine_signals.json",
        "write_checkpoint.json",
    ],
    "gen3_rr": [
        "profile.json",
        "engine_signals.json",
        "write_checkpoint.json",
    ],
    "gen3_emerald": [
        # Routed since EG4 (owner ruling 24), same as gen3_frlg/gen3_rr. Every pack's
        # engine_signals.json is opened unconditionally by Entry.admission_table
        # (lua/gen3/entry.lua:121-140) regardless of routing, so a release zip without this row
        # refuses EVERY GBA cartridge at "cannot open data/games/gen3_emerald/engine_signals.json"
        # (F1, cx-7b74a808). Mirrors Entry.PACK_FILES.gen3_emerald (lua/gen3/entry.lua:97-103).
        "profile.json",
        "engine_signals.json",
        "write_checkpoint.json",
        "area_map.json",
        "gen3_emerald_locations.lua",
        # server/adapters/gen3_frlge.py _load_emerald() reads it; a missing file silently empties
        # the fixed-gift clause bypasses (Beldum/Wynaut/Castform/Mew/Deoxys) -- OMP cx-9f0eacae F1.
        "statics.json",
    ],
    "gen3_exp/28877d73": [
        # X3: registered in Entry.PACKS/PACK_FILES (not routed, not admitted), so
        # Entry.admission_table opens its engine_signals.json for every GBA cartridge -- same
        # F1 rule as gen3_emerald above. Mirrors Entry.PACK_FILES.gen3_exp.
        "profile.json",
        "engine_signals.json",
        "write_checkpoint.json",
        "area_map.json",
        "gen3_exp_locations.lua",
    ],
    "gen4_hgss": [
        # Entry.PACKS.gen4_hgss.profile (lua/gen4/entry.lua:26) -- opened by Entry.admission_table
        # for EVERY cartridge, routed or not, and by inputs.lua's has_pokeballs producer for the
        # bag array (inputs.lua:173).
        "profile.json",
        # inputs.lua area_of: `.maps[map_id]` -> area_id, `.locations[map_id].name` -> loc_name
        # (inputs.lua:80,85). Same file also carries `.gift_areas.ids` for the gift-area seam.
        "area_map.json",
        "locations.json",
        # inputs.lua charmap: the u16 code -> glyph table the client's text seam decodes names with
        # (inputs.lua:146); written by tools/gen_gen4_names.py alongside the (dev-only) names.json.
        "charmap.json",
    ],
    "gen4_hge": [
        # Same profile + charmap (Entry.PACKS.gen4_hge.profile, entry.lua:27).
        "profile.json",
        "charmap.json",
        # hge's area map is the HGSS map, proven byte-identical on the two pinned ROMs
        # (area_map.json source.hge_proof, tools/gen_gen4_area_map.py); inputs.lua opens both.
        "area_map.json",
        "locations.json",
    ],
    "gen4_pt": [
        # Bind-only (Entry.BIND_ONLY, D3): never admitted, never routed. It ships anyway for the
        # same reason gen3_emerald/gen3_exp ship above: Entry.admission_table loads EVERY registered
        # pack's profile.json before it can refuse one (entry.lua:76-80) and load_json asserts on a
        # missing file (entry.lua:37), so withholding this makes the release refuse EVERY Gen 4
        # cartridge at "cannot open data/games/gen4_pt/profile.json".
        "profile.json",
    ],
    "gen5_bw": [
        "gen5_bw_areas.lua",
        "gen5_bw_locations.lua",
    ],
}

# Repo-relative files outside data/games/<gen>/ that a launcher loads by path; shipped at the same path.
# lua/gen2/polished.lua P.PROVENANCE (Entry.admit_polished pins the overlay sha1 to it).
_DATA_EXTRA = ["data/polished/overlay_provenance.json"]

# lua/x64/ — the DLL is optional to the ZIP (a player may supply it from Archipelago) but it IS
# tracked in git (blob 896b3cba at master, lua/x64/README.md alongside it), so a checkout has it;
# only a hand-stripped tree would miss it. Absent -> warn, do not fail.
_LUA_X64_OPTIONAL = ["socket-windows-5-4.dll"]
# Shipped at the zip root: the MIT licence and the third-party notices (LuaSocket's MIT
# notice covers the DLL above). NOTICE.md names what those licences require on redistribution.
_LICENSE_FILES = ["LICENSE", "NOTICE.md"]

# ── Companion patch (Radical Red native code-injection) — optional add-on ──────
# RR-only. Bundled under companion/ when --with-patch or --rom is given. The UPS
# is the distributable; a pre-patched ROM may be included with --rom for a
# playgroup that already owns the base ROM.
_COMPANION_UPS = "patch/dist/SLink-RR.ups"
_COMPANION_README = "patch/README.md"
# Declared Game Boy companion UPS files; gb_companion_ups selects what --with-patch ships.
# Gen 1 vanilla Red/Blue and pureRGB are always shipped. Polished uses its runtime admission
# binding below, not a vanilla Gen 2 catalog. No Yellow (no free WRAM).
_GB_COMPANION_UPS = ("SLink-RB-Red.ups", "SLink-RB-Blue.ups",
                     "SLink-PureRed.ups", "SLink-PureBlue.ups", "SLink-PureGreen.ups",
                     "SLink-Polished.ups")
# Gen 2: the companion overlay per title (tools/build_gen2_companion.py, data/gen2/overlay_provenance.json).
# NOT unconditional: lua/gen2/entry.lua admits ONLY an activated overlay row (a clean cartridge is refused,
# owner 2026-10-02), so until the overlay row is promoted a release that shipped these UPS would hand the
# user a patch that bricks their cartridge while (see data_game_files) withholding the binding sidecar and
# proofs that explain why. Gated by overlay_state.
_GEN2_OVERLAY_UPS = {"crystal": "SLink-Crystal.ups", "gold": "SLink-Gold.ups", "silver": "SLink-Silver.ups"}


def overlay_state(pack: str, root: Path | None = None) -> str:
    """`ADMITTED` or `BUILT` for one Gen 2 pack's overlay row -- the ONE source of truth for whether that
    title's execution binding, proofs AND companion UPS ship.

    Read once per call from the pack's own catalog; a missing or malformed catalog is an error, never an
    inactive artifact. Deliberately not memoised: the catalogs are rewritten by --promote-overlays inside
    the same process that later builds a release.
    """
    root = REPO_ROOT if root is None else root
    catalog = json.loads((Path(root) / f"data/games/{pack}/admission.json").read_text(encoding="utf-8"))
    rows = [row for row in catalog["artifacts"] if row.get("kind") == "overlay"]
    if len(rows) != 1 or rows[0].get("status") not in ("BUILT", "ADMITTED"):
        raise ValueError(f"{pack}: overlay catalog missing or malformed")
    return rows[0]["status"]


def _polished_overlay_admitted(root: Path) -> bool:
    """Mirror P.admit's published profile/provenance binding (lua/gen2/polished.lua).

    Polished has no BUILT/ADMITTED catalog: its exact published overlay is admitted
    when these generated pins agree. Missing or stale inputs must not ship its UPS.
    """
    try:
        provenance = json.loads((root / "data/polished/overlay_provenance.json").read_text(encoding="utf-8"))
        profile = json.loads((root / "data/games/polished_crystal/profile.json").read_text(encoding="utf-8"))
        title = profile["titles"]["polished"]
        overlay = title["overlay"]
        return (provenance["schema"] == "polished-overlay-provenance-v1"
                and provenance["output"]["sha1"] == overlay["rom_sha1"]
                and provenance["base_sha1"] == overlay["base_sha1"] == title["rom_sha1"])
    except (OSError, ValueError, KeyError, TypeError):
        return False


def gb_companion_ups(root: Path | None = None) -> tuple[str, ...]:
    """Ship admitted companions: Gen 1/pureRGB, catalog-activated Gen 2, and
    Polished only while its generated runtime admission binding agrees."""
    root = REPO_ROOT if root is None else root
    return tuple(name for name in _GB_COMPANION_UPS
                 if name != "SLink-Polished.ups" or _polished_overlay_admitted(root)) + tuple(
        name for title, name in _GEN2_OVERLAY_UPS.items()
        if overlay_state(f"gen2_{title}", root) == "ADMITTED")

_COMPANION_ROM_ARCNAME = "Pokemon - Radical Red (SLink companion).gba"
_GEN3_COMPANION_FILES = ("SLink-FireRed.ups", "SLink-LeafGreen.ups", "SLink-Emerald.ups", "gen3_companions.json",
                         "companion_version.json")   # the stamp record companion_stamp_errors() vouches with

# Launcher scripts (relative to lua/) whose SLINK_* lines get patched
_LAUNCHER_SCRIPTS: set[str] = {
    "slink_gen1.lua",
    "slink_gen3.lua",
    "slink_gen5.lua",
}

# ── Player setup guide ────────────────────────────────────────────────────────

# The oldest BizHawk each game family runs on -- the one place setup text (this guide, the
# Manager's run page) takes it from. Gen 1's is enforced at load by lua/slink.lua, which
# refuses anything older (tests/unit/test_player_pack.py pins the two together), and so is
# Gen 3's (the same lua/slink.lua guard on the Gen 3 route); Gen 2's is the Lua 5.4 LuaSocket
# floor (lua/connector.lua). Gen 4/5 stay unlisted (experimental).
BIZHAWK_MIN = {"Gen 1": "2.11", "Gen 2": "2.9", "Gen 3": "2.11"}


def bizhawk_requirement() -> str:
    """'2.11+ for Gen 1, 2.9+ for Gen 2, 2.11+ for Gen 3'"""
    return ", ".join(f"{v}+ for {k}" for k, v in BIZHAWK_MIN.items())


_GUIDE_HEAD = """\
# SLink player setup

This package contains everything you need to play a Soul Link Nuzlocke with
SLink in BizHawk. You don't need Python: the host runs the server.

---

## What you need

| Requirement | Detail |
|---|---|
| BizHawk | BIZHAWK_REQ (older versions refuse to start). https://github.com/TASEmulators/BizHawk/releases |
| A writable folder | Unzip somewhere you can write (not Program Files): Gen 3 keeps a small session file next to `lua/`. |
| Your cartridge | The one your host prepared in the SLink Manager (download it from the run's page). Red/Blue, pureRGB, Gold/Silver/Crystal, FireRed/LeafGreen/Emerald and Radical Red must carry the SLink patch; the Manager adds it. Yellow is played clean. |
| LuaSocket DLL | Already in `lua/x64/`. If missing, see the note below. |
"""

# A pack the Manager built already holds the player's launcher.
_GUIDE_STEP1_PACKED = """\
| Launcher script | `LAUNCHER`, already in this folder. |

---

## Step 1: your launcher is already here

`LAUNCHER` sits next to `lua/` and `data/` in this folder. It connects to
`CONNECT` as Player PLAYER. Keep it in this folder: it finds the rest of
SLink next to itself. If your host's address changes, download a fresh pack.

---

"""

_GUIDE_STEP1_DOWNLOAD = """\
| Launcher script | Download from your host's SLink Manager (see Step 1). |

---

## Step 1: download your launcher from the host

Your host will share their **SLink Manager** page, which looks like:

```
http://<host-ip>:8090/
```

On the run's page, open **Launchers** and download the `.lua` for your
player slot (Player A or Player B). It is pre-configured with the address,
game TCP port and slot the run expects. The **setup .zip** in the same menu
is this whole package with the launcher already inside.

**Save that file into this folder**, the same folder that contains `lua/`
and `data/`. For example:

```
SLink-player-<version>/
├── slink_MyRun_a.lua   ← place the downloaded launcher here
├── lua/
└── data/
```

---

"""

_GUIDE_REST = """\
## Step 2: load in BizHawk

1. Open BizHawk and load your save file.
2. Open **Tools → Lua Console**.
3. Choose **Script → Open Script…** and select your launcher `.lua` (Step 1).
4. The console will print:

   ```
   [SLink] TCP connected to <host>:<port>
   ```

   If it prints `TCP connecting… (non-blocking)` briefly first, that is
   normal. It connects within a second or two.

> **Important:** Load your save file *before* opening the Lua script.
> The script validates save data at startup. If the save isn't loaded yet,
> writes are disabled until validation passes (it retries automatically).

---

## If the LuaSocket DLL is missing

The file `lua/x64/socket-windows-5-4.dll` is required and ships in this
package. If it is absent, download the setup ZIP from your host again.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `BizHawk … is too old for Gen 1` | Install BizHawk GEN1_MIN or newer. |
| `module 'socket' not found` | `socket-windows-5-4.dll` is missing from `lua/x64/`. See above. |
| `TCP connect failed` / retrying | Server is not running, or IP/port is wrong. Ask host to verify. |
| Connected but nothing happens | Check with host that your player slot (A or B) is not already taken. |
| Writes disabled / validation failed | Load your save file **before** the Lua script. |
| `… needs the SLink companion patch` | This cartridge isn't patched, or was patched by an earlier SLink. Use the cartridge your host prepared, or patch it at `/patcher` on the host's Manager. |
| `Wrong save!` on screen | You loaded a different save file than the one registered for your slot. |
| Folder picker appears | Put the launcher `.lua` file inside the extracted `SLink-player-*` folder, next to `lua/`. |

---

## What SLink does automatically

- Your first catch on a route is permanently
  paired with your partner's first catch on the same route.
- When your linked partner faints, so does yours.
- If either player fails to catch on a route, the route is closed
  for both of you.
- Fallen pairs are moved to a memorial box once both games are
  somewhere safe.

You play normally. SLink enforces the rules for you.
"""

# ── Helpers ────────────────────────────────────────────────────────────────────

def run_generators() -> None:
    """Run area-map generators to ensure data/games/ lua files are up to date."""
    print("Running area-map generators...")
    for rel_script, desc in GENERATORS:
        script = REPO_ROOT / rel_script
        print(f"  {desc}  ({rel_script})")
        result = subprocess.run(
            [sys.executable, "-X", "utf8", str(script)],
            cwd=REPO_ROOT,
        )
        if result.returncode != 0:
            print(f"    ERROR: generator failed (exit {result.returncode})", file=sys.stderr)
            sys.exit(1)
    print()


def player_setup_md(launcher: str | None = None, connect: str = "", player: str = "") -> str:
    """PLAYER_SETUP.md. With `launcher` (a pack the Manager built), Step 1 names the launcher
    already in the folder and the host:port it connects to, instead of sending the player
    off to download one."""
    step1 = (_GUIDE_STEP1_PACKED.replace("LAUNCHER", launcher).replace("CONNECT", connect)
             .replace("PLAYER", player.upper()) if launcher else _GUIDE_STEP1_DOWNLOAD)
    return ((_GUIDE_HEAD + step1 + _GUIDE_REST)
            .replace("BIZHAWK_REQ", bizhawk_requirement())
            .replace("GEN1_MIN", BIZHAWK_MIN["Gen 1"]))


def patch_launcher(content: str, host: str | None, port: int | None, player: str | None) -> str:
    """Rewrite SLINK_HOST / SLINK_PORT / SLINK_PLAYER assignments in a launcher."""
    if host is not None:
        content = re.sub(r'^(SLINK_HOST\s*=\s*)"[^"]*"', rf'\1"{host}"',
                         content, flags=re.MULTILINE)
    if port is not None:
        content = re.sub(r'^(SLINK_PORT\s*=\s*)\d+', rf'\g<1>{port}',
                         content, flags=re.MULTILINE)
    if player is not None:
        content = re.sub(r'^(SLINK_PLAYER\s*=\s*)"[^"]*"', rf'\1"{player}"',
                         content, flags=re.MULTILINE)
    return content


def data_game_files(root: Path | None = None) -> dict[str, list[str]]:
    """Only activated Gen 2 overlays require their execution binding and own proofs.

    The declared manifest is a superset. FUTURE/BUILT overlays need no optional
    files; ADMITTED overlays require every named file in the usual preflight.
    A missing or malformed catalog is an error, never an inactive artifact. The ADMITTED test is
    overlay_state -- the same one gb_companion_ups uses for the companion UPS, so the two halves of a
    release can never disagree about which titles are activated.
    """
    root = REPO_ROOT if root is None else root
    manifest = {game: list(names) for game, names in _DATA_GAME_LUA.items()}
    for title in ("crystal", "gold", "silver"):
        pack = f"gen2_{title}"
        if pack not in manifest:
            continue
        if overlay_state(pack, root) != "ADMITTED":
            manifest[pack] = [name for name in manifest[pack]
                              if name not in ("overlay/binding.json", "overlay/beacon.json")
                              and not name.startswith("receipts/overlay/")]
    return manifest


def companion_stamp_errors(version: str, dist: Path | None = None) -> list[str]:
    """Validate every shipped companion's stamped version or explicit as-built provenance policy."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import stamp_release

    dist = dist or REPO_ROOT / "patch" / "dist"
    return stamp_release.certificate_errors(version, REPO_ROOT, dist,
                                            polished=_polished_overlay_admitted(REPO_ROOT))


def build_release(
    version: str,
    out_dir: Path,
    host: str | None = None,
    port: int | None = None,
    player: str | None = None,
    skip_generators: bool = False,
    with_patch: bool = False,
    rom: Path | None = None,
    launcher: tuple[str, str] | None = None,
    guide: str | None = None,
    quiet: bool = False,
    allow_unstamped: bool = False,
) -> Path:
    """`launcher` is (filename, source) for a launcher placed at the package root, beside
    lua/ -- the Manager's player pack. `guide` replaces the generic PLAYER_SETUP.md.
    `quiet` drops the per-file progress lines (errors still go to stderr)."""
    say = (lambda *a, **k: None) if quiet else print
    zip_name = f"SLink-player-{version}.zip"
    zip_path = out_dir / zip_name
    prefix   = f"SLink-player-{version}/"
    do_patch = bool(host or port or player)

    # ── Generate area tables ───────────────────────────────────────────────────
    if not skip_generators:
        run_generators()

    # ── Pre-flight: verify all required files exist ───────────────────────────
    game_data = data_game_files(REPO_ROOT)
    required: list[Path] = (
        [REPO_ROOT / _lua_rel(sub, f) for sub, files in _MANIFEST_TREES.items() for f in files]
        + [
            REPO_ROOT / "data" / "games" / gen / f
            for gen, files in game_data.items()
            for f in files
        ]
        + [REPO_ROOT / f for f in _DATA_EXTRA]
    )
    missing = [str(p) for p in required if not p.exists()]
    if missing:
        print("ERROR — missing required source files:", file=sys.stderr)
        for m in missing:
            print(f"  {m}", file=sys.stderr)
        sys.exit(1)

    # ── Companion patch (optional) pre-flight ─────────────────────────────────
    # A --rom implies bundling the patch too (the ROM only makes sense with it).
    include_companion = with_patch or rom is not None
    if include_companion and not allow_unstamped:
        stamp_errors = companion_stamp_errors(version)
        if stamp_errors:
            print("ERROR — the companions are not stamped for this release:", file=sys.stderr)
            for e in stamp_errors:
                print(f"  {e}", file=sys.stderr)
            print("  (--allow-unstamped-companions ships them as built; the game menus then show their own version)", file=sys.stderr)
            sys.exit(1)
    if include_companion:
        ups = REPO_ROOT / _COMPANION_UPS
        if not ups.exists():
            print(f"ERROR — companion requested but missing: {ups}\n"
                  f"  build it first: python patch/tools/build.py", file=sys.stderr)
            sys.exit(1)
        if rom is not None and not rom.exists():
            print(f"ERROR — --rom not found: {rom}", file=sys.stderr)
            sys.exit(1)

    # ── Optional: DLL ─────────────────────────────────────────────────────────
    dll_warnings: list[str] = []
    dll_files: list[Path] = []
    for fname in _LUA_X64_OPTIONAL:
        p = REPO_ROOT / "lua" / "x64" / fname
        if p.exists():
            dll_files.append(p)
        else:
            dll_warnings.append(
                f"  lua/x64/{fname} — not found; the player package will lack it"
            )

    # ── Build zip ─────────────────────────────────────────────────────────────
    out_dir.mkdir(parents=True, exist_ok=True)
    say(f"Building {zip_path} ...")

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(prefix + "PLAYER_SETUP.md", guide or player_setup_md())
        for fname in _LICENSE_FILES:
            zf.write(REPO_ROOT / fname, prefix + fname)
            say(f"  [added]   {prefix}{fname}")
        if launcher:
            zf.writestr(prefix + launcher[0], launcher[1])
            say(f"  [added]   {prefix}{launcher[0]}")

        for sub, files in _MANIFEST_TREES.items():
            for fname in files:
                src = REPO_ROOT / _lua_rel(sub, fname)
                arc = prefix + _lua_rel(sub, fname)
                # Only the lua/ ROOT carries launchers, so only it can be one.
                if do_patch and not sub and fname in _LAUNCHER_SCRIPTS:
                    zf.writestr(arc, patch_launcher(src.read_text("utf-8"), host, port, player))
                    say(f"  [patched] {arc}")
                else:
                    zf.write(src, arc)
                    say(f"  [added]   {arc}")

        for p in dll_files:
            zf.write(p, prefix + f"lua/x64/{p.name}")
            say(f"  [added]   {prefix}lua/x64/{p.name}")

        for gen, files in game_data.items():
            for fname in files:
                zf.write(
                    REPO_ROOT / "data" / "games" / gen / fname,
                    prefix + f"data/games/{gen}/{fname}",
                )
                say(f"  [added]   {prefix}data/games/{gen}/{fname}")

        for rel in _DATA_EXTRA:
            zf.write(REPO_ROOT / rel, prefix + rel)
            say(f"  [added]   {prefix}{rel}")

        # ── Companion patches — optional ──────────────────────────────────────
        if include_companion:
            zf.write(REPO_ROOT / _COMPANION_UPS, prefix + "companion/SLink-RR.ups")
            say(f"  [added]   {prefix}companion/SLink-RR.ups")
            # The Game Boy pair. Red and Blue each get their own, because a UPS embeds
            # the CRC32 of the exact dump it was diffed against. There is deliberately NO
            # Yellow patch: its WRAM has no free bytes for the mailbox, so no build
            # exists, and shipping one would advertise a capability that cannot be there.
            # pureRGB (PLAN M3): the companion source overlay over each pinned pure build,
            # one UPS per title (PureGreen included -- it is a full pure build of its own).
            # Gen 2's per-title UPS comes from gb_companion_ups ONLY for an ADMITTED overlay, so the ZIP
            # never offers a Gen 2 patch the launcher would refuse (its sidecar/proofs are gated the same way).
            for gb_ups in gb_companion_ups(REPO_ROOT) + _GEN3_COMPANION_FILES:
                src = REPO_ROOT / "patch" / "dist" / gb_ups
                if src.exists():
                    zf.write(src, prefix + f"companion/{gb_ups}")
                    say(f"  [added]   {prefix}companion/{gb_ups}")
                else:
                    say(f"  [SKIP]    {gb_ups} not built — run patch/tools/make_ups.py")
            readme = REPO_ROOT / _COMPANION_README
            if readme.exists():
                zf.write(readme, prefix + "companion/COMPANION_PATCH.md")
                say(f"  [added]   {prefix}companion/COMPANION_PATCH.md")
            if rom is not None:
                rom_mb = rom.stat().st_size // (1024 * 1024)
                zf.write(rom, prefix + f"companion/{_COMPANION_ROM_ARCNAME}")
                say(f"  [added]   {prefix}companion/{_COMPANION_ROM_ARCNAME}  ({rom_mb} MB)")

    size_kb = zip_path.stat().st_size // 1024
    say(f"\nDone — {zip_path.name}  ({size_kb} KB)")

    if dll_warnings:
        say("\nWarnings (non-fatal):")
        for w in dll_warnings:
            say(w)

    return zip_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Build SLink player release package.")
    parser.add_argument("--version", default="dev", help="Version string (default: dev)")
    parser.add_argument("--out", default=str(REPO_ROOT / "dist"),
                        help="Output directory (default: dist/)")
    parser.add_argument("--host",   metavar="HOST",
                        help="Server IP to bake into launcher scripts")
    parser.add_argument("--port",   metavar="PORT", type=int,
                        help="Server TCP port to bake into launcher scripts")
    parser.add_argument("--player", metavar="PLAYER", choices=["a", "b"],
                        help='Player slot to bake into launcher scripts: "a" or "b"')
    parser.add_argument("--skip-generators", action="store_true",
                        help="Skip running area-map generators")
    parser.add_argument("--with-patch", action="store_true",
                        help="Bundle the RR companion patch (patch/dist/SLink-RR.ups) + guide "
                             "under companion/")
    parser.add_argument("--rom", metavar="PATH",
                        help="Bundle a pre-patched ROM under companion/ (implies --with-patch); "
                             "e.g. patch/build/slink_RR.gba")
    parser.add_argument("--allow-unstamped-companions", action="store_true",
                        help="Bundle companions even when patch/dist/companion_version.json does not match --version")
    args = parser.parse_args()

    build_release(
        version=args.version,
        out_dir=Path(args.out),
        host=args.host,
        port=args.port,
        player=args.player,
        skip_generators=args.skip_generators,
        with_patch=args.with_patch,
        rom=Path(args.rom) if args.rom else None,
        allow_unstamped=args.allow_unstamped_companions,
    )


if __name__ == "__main__":
    main()
