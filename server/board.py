"""The pair board: the status payload joined back into the thing a player thinks about.

A soul link's unit is the PAIR — two mons bound across two cartridges, both of which die
if either faints — and `/api/status` hands the two halves back separately: the bond in
`links`, the live HP in each player's `party_details`, the shelf in `pc_boxes`. This
module joins them into one row per bond and sorts the rows into zones, so the template
only has to draw.

Pure functions over the status dict. Nothing here reads server state, which is what makes
`tests/unit/test_board.py` a file of dicts rather than a server harness, and what lets the
Manager render the same board for a stopped run from what it persisted.

The board's founding rule: it cannot know which player is looking at it, so "you" and
"ours" are meaningless — column position is the only ownership signal. Battle is therefore
a PLAYER state drawn on a half, never a pair state drawn on a row.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime

from server.adapters.base import companion_required_reason

_COMPANION_REFUSAL = re.compile(re.escape(companion_required_reason("{title}")).replace(re.escape("{title}"), ".+"))

SECTION_LABELS = {
    "limbo": "Limbo",
    "party": "In party",
    "pending": "Pending link",
    "unpaired": "Unpaired",
    "split": "Split — one half boxed",
    "boxed": "Boxed",
    "linked": "Linked",        # a stopped run: the bond is known, where each half sits is not
    "fallen": "Fallen",
}
# Unresolved missing mons precede the team, shelf and graveyard.
SECTION_ORDER = ("limbo", "party", "pending", "unpaired", "split", "boxed", "linked", "fallen")

PIDS = ("a", "b")


def hp_pct(mon: dict) -> int:
    if mon.get("hp") is None:
        return 0
    return max(0, min(100, round(mon["hp"] / max(mon.get("maxHP") or 1, 1) * 100)))


def hp_class(mon: dict) -> str:
    p = hp_pct(mon)
    return "" if p > 50 else "mid" if p > 20 else "low"


def area_name(area_id: str) -> str:
    """`viridian_forest` -> `Viridian Forest`, `route9` -> `Route 9`. A fallback for ids
    the adapter has no display name for."""
    s = re.sub(r"(\d+)", r" \1", (area_id or "").replace("_", " "))
    return " ".join(w[:1].upper() + w[1:] for w in s.split())


def bond_glyph(status: str) -> str:
    return "<>" if status == "alive" else "<·" if status == "pending" else "·" if status == "unpaired" else "><"


def badge_count(mask: int) -> int:
    return bin(mask or 0).count("1")


def locate(player: dict, key: str) -> dict | None:
    """Where a mon is right now, with its live numbers if it is somewhere live."""
    in_party = (player.get("party_details") or {}).get(key)
    if in_party:
        return {**in_party, "key": key, "where": "party"}
    for b in player.get("pc_boxes") or []:
        if b.get("key") == key:
            return {**b, "key": key, "where": "box"}
    return None


def active_key(player: dict) -> str | None:
    """The mon this player has out, when they are in battle."""
    if not (player.get("battle_state") or {}).get("in_battle"):
        return None
    for key in player.get("party_keys") or []:
        det = (player.get("party_details") or {}).get(key)
        if det and det.get("active"):
            return key
    return None


def _half(status: dict, link: dict, pid: str) -> dict | None:
    key = link.get(f"{pid}_key")
    if not key:
        return None
    player = status["players"][pid]
    live = locate(player, key)
    in_party = bool(live and live["where"] == "party")
    return {
        "key": key,
        "nickname": link.get(f"{pid}_nickname") or "",
        "species_name": link.get(f"{pid}_species_name") or "",
        "level": (live and live.get("level")) or link.get(f"{pid}_level") or 0,
        "sprite": link.get(f"{pid}_sprite_html") or "",
        "shiny": bool(link.get(f"{pid}_shiny")),
        "hp": live.get("hp") if in_party else None,
        "maxHP": live.get("maxHP") if in_party else None,
        "where": live["where"] if live else None,
        "ability": (live or {}).get("ability_name") or "",
        "item": (live or {}).get("item_name") or "",
        "active": active_key(player) == key,
    }


def pairs(status: dict) -> list[dict]:
    """One row per bond. Each half carries what the link recorded plus, if the run is live
    and the mon is in a party, its current HP."""
    killfeed = {k.get("area_id"): k for k in status.get("killfeed") or []}
    area_states = status.get("area_states") or {}
    rows = []
    for link in status.get("links") or []:
        a, b = _half(status, link, "a"), _half(status, link, "b")
        dead = link.get("status") in ("dead", "memorial")
        dead_zone = not a and not b and area_states.get(link.get("area_id")) == "dead_zone"
        if dead or dead_zone:
            section = "fallen"
        elif not a or not b:
            section = "pending"
        elif a["where"] == "party" and b["where"] == "party":
            section = "party"
        elif a["where"] == "box" and b["where"] == "box":
            section = "boxed"
        elif a["where"] and b["where"]:
            section = "split"
        else:
            section = "linked"   # nothing is located: the run is not live
        rows.append({
            "area": link.get("area_id") or "",
            "area_name": link.get("area_display") or area_name(link.get("area_id")),
            "status": "dead_zone" if dead_zone else link.get("status"),
            "section": section, "a": a, "b": b,
            "death": killfeed.get(link.get("area_id")),
        })
    return rows


def pending_rows(status: dict) -> list[dict]:
    """Captures waiting on a partner and first-species locks still being retried."""
    out = []
    pending = status.get("pending_captures") or {}
    locks = status.get("encounter_locks") or {}
    for area in sorted(set(pending) | set(locks)):
        sides = pending.get(area) or {}
        area_locks = locks.get(area) or {}
        halves = {}
        for pid in PIDS:
            mon = sides.get(pid)
            lock = area_locks.get(pid)
            if mon:
                halves[pid] = {
                    **mon, "sprite": mon.get("sprite_html") or "", "hp": None,
                    "where": None, "active": False, "ability": "", "item": "",
                    "seeking": False,
                }
            elif lock:
                halves[pid] = {
                    **lock, "nickname": "", "sprite": lock.get("sprite_html") or "",
                    "hp": None, "where": None, "active": False, "ability": "", "item": "",
                    "seeking": True,
                }
            else:
                halves[pid] = None
        retrying = any(half and half["seeking"] for half in halves.values())
        out.append({"area": area, "area_name": area_name(area), "section": "pending",
                    "status": "retry" if retrying else "pending",
                    "a": halves["a"], "b": halves["b"],
                    "death": None})
    return out


def unpaired_rows(status: dict) -> list[dict]:
    """Surviving halves explicitly detached from a lost partner."""
    out = []
    for area, sides in sorted((status.get("unpaired_mons") or {}).items()):
        halves = {}
        for pid in PIDS:
            mon = sides.get(pid)
            if not mon:
                halves[pid] = None
                continue
            live = locate(status["players"][pid], mon["key"])
            in_party = bool(live and live["where"] == "party")
            halves[pid] = {
                **mon,
                "sprite": mon.get("sprite_html") or "",
                "hp": live.get("hp") if in_party else None,
                "maxHP": live.get("maxHP") if in_party else None,
                "where": live["where"] if live else None,
                "active": active_key(status["players"][pid]) == mon["key"],
                "ability": (live or {}).get("ability_name") or "",
                "item": (live or {}).get("item_name") or "",
            }
        out.append({
            "area": area,
            "area_name": area_name(area),
            "section": "unpaired",
            "status": "unpaired",
            "a": halves["a"],
            "b": halves["b"],
            "death": None,
        })
    return out


def sections(status: dict, rows: list[dict] | None = None) -> list[tuple[str, list[dict]]]:
    if rows is None:
        rows = pending_rows(status) + unpaired_rows(status) + pairs(status)
    annotated = []
    represented = set()
    for row in rows:
        row = dict(row)
        for pid in PIDS:
            half = row.get(pid)
            incident = ((status.get("limbo") or {}).get(pid) or {}).get((half or {}).get("key"))
            if half and half.get("key"):
                represented.add((pid, half["key"]))
            if half and incident:
                row[pid] = {**half, "limbo": incident, "where": None, "hp": None, "active": False}
                row["section"] = "limbo"
        annotated.append(row)
    for pid in PIDS:
        for key, incident in ((status.get("limbo") or {}).get(pid) or {}).items():
            if (pid, key) in represented:
                continue
            annotated.append({
                "area": incident["area_id"], "area_name": area_name(incident["area_id"]),
                "status": "unpaired", "section": "limbo", "death": None,
                "a": None, "b": None,
                pid: {**incident, "key": key, "limbo": incident, "hp": None,
                      "where": None, "active": False, "sprite": "", "ability": "", "item": ""},
            })
    out = []
    for key in SECTION_ORDER:
        group = [r for r in annotated if r["section"] == key]
        if group:
            out.append((key, group))
    return out


def build_board(status: dict) -> dict:
    """Everything `dashboard.html` draws, derived once."""
    players = status.get("players") or {}
    rows = pairs(status)   # built once: sections() would otherwise build every pair again
    return {
        "sections": sections(status, pending_rows(status) + unpaired_rows(status) + rows),
        "section_labels": SECTION_LABELS,
        "counts": {
            "alive": sum(1 for r in rows if r["section"] != "fallen"),
            "fallen": sum(1 for r in rows if r["section"] == "fallen"),
        },
        "badges": {pid: {"count": badge_count(players.get(pid, {}).get("badges", 0)),
                         "slots": len((players.get(pid, {}).get("capabilities") or {}).get("badges") or []) or 8}
                   for pid in PIDS},
        "active": {pid: active_key(players.get(pid, {})) for pid in PIDS},
        # "?" is what a player who never said hello reports as rom_type.
        "has_data": {pid: bool((players.get(pid, {}).get("rom_type") or "?") != "?"
                               or players.get(pid, {}).get("party_keys"))
                     for pid in PIDS},
        "dead_zones": sorted(k for k, v in (status.get("area_states") or {}).items() if v == "dead_zone"),
    }


RULE_BADGES = (
    ("retry_same_species", "retry", "Retry First Species"),
    ("species_lock", "dna", "Species Clause"),
    ("gender_lock", "gender", "Gender Clause"),
    ("type_lock", "type", "Type Clause"),
    ("ignore_wild_faints", "wild-safe", "Ignore Wild Faints"),
    ("explode_mode", "explode", "Explode Mode"),
    ("rival_team_swap", "rival-swap", "Rival Team Swap"),
    ("overworld_presence", "presence", "Overworld Presence"),
)


def phase(status: dict) -> tuple[str, str]:
    """Run lifecycle from the payload alone: nuzlocke_active is pokeballs_obtained."""
    if status.get("run_over"):
        return "game_over", "Game over"
    players = status.get("players") or {}
    if all((players.get(pid) or {}).get("nuzlocke_active") for pid in PIDS):
        return "running", "Run in progress"
    if not any((players.get(pid) or {}).get("connected") for pid in PIDS):
        return "pre", "Waiting for players"
    return "pre", "Waiting for Pokéballs"


# server.py's identity_error texts that mean "this is not this run's game" rather than
# "this is not this slot's save" (test_player_pack pins them to server.py).
_WRONG_GAME_ERRORS = ("Mixed games", "Mixed artifact kinds", "Unknown rom_type")


def connection_state(p: dict, live: bool) -> dict:
    """One player's state from the status payload alone: {slug, label, line}. Errors come
    before connectivity: a rejected player stays rejected after they disconnect."""
    def state(slug, label, line):
        return {"slug": slug, "label": label, "line": line}
    if not live:
        return state("stopped", "Not started", "The run is stopped. Start it, then load the launcher in BizHawk.")
    err = p.get("identity_error") or ""
    if _COMPANION_REFUSAL.fullmatch(err):
        return state("companion", "Companion patch required",
                     "Patch this cartridge using the run's cartridge download or /patcher.")
    if err.startswith("Trade recovery extension unavailable"):
        return state("wrong_game", "Unsupported recovery",
                     f"{err}. Use a client/cartridge build with supported trade recovery.")
    if err.startswith(_WRONG_GAME_ERRORS):
        return state("wrong_game", "Wrong game", f"{err}. Load the game this run is for, then reload the launcher.")
    if p.get("admission", "admitted") != "admitted":
        return state("wrong_game", "Wrong cartridge", f"Not admitted: {p.get('admission_reason') or 'unknown reason'}. "
                     "Load the cartridge this run made for this player, then reload the launcher.")
    if err:
        return state("identity", "Wrong save", f"{err}. Load this slot's own save: SLink will not "
                     "adopt a different trainer.")
    seen = p.get("last_seen_age") is not None
    if p.get("connected") and p.get("stale"):
        return state("disconnected", "No data", f"Connected, but silent since {p.get('last_seen_label')}: "
                     "BizHawk may be paused, closed or frozen.")
    if p.get("connected") and (p.get("rom_type") or "?") != "?":
        return state("ready", "Connected", "Connected and recording.")
    if p.get("connected"):
        return state("waiting", "Connecting", "BizHawk connected; waiting for the game to say hello.")
    if seen:
        return state("disconnected", "Disconnected", f"Last heard {p.get('last_seen_label')}. Reload the "
                     "launcher in BizHawk's Lua Console to reconnect.")
    return state("waiting", "Waiting for BizHawk", "Load the game in BizHawk, then this player's launcher "
                 "in Tools → Lua Console.")


def board_context(status: dict, *, run_name: str = "", poll_url: str = "/", live: bool = True,
                  launcher_url: str = "/launcher/{player}", rom_url: str = "",
                  roms_pinned: bool = False, rom_ext: dict | None = None) -> dict:
    """Everything `_board.html` needs, from the payload alone -- so the run server and the
    Manager (which has only the payload, live or persisted) render the same board.

    `poll_url` is what `#content` fetches every 2 s: `/` on a run server, the run's board
    route on the Manager. It has to be passed down because a root-relative URL baked into
    the fragment would poll the wrong page once the fragment is served under a prefix."""
    from server.adapters import variant_label

    players = status.get("players") or {}
    rom_types = [str((players.get(pid) or {}).get("rom_type") or "") for pid in PIDS]
    rom_type = next((rt for rt in rom_types if rt and rt != "?"), "")
    rules = status.get("rules") or {}
    slug, label = phase(status) if live else ("stopped", "Run stopped")
    title = " — ".join(x for x in (variant_label(rom_type) if rom_type else "", run_name) if x)
    return {
        "status": status,
        "board": build_board(status),
        "rules": [(icon, text) for key, icon, text in RULE_BADGES if rules.get(key)],
        "phase_slug": slug,
        "phase_label": label,
        # Where each player's BizHawk launcher downloads from; the empty board points at it.
        "launchers": {pid: launcher_url.format(player=pid) for pid in PIDS},
        # A run that made its players' cartridges: where each downloads from, so the empty
        # board's first step is the cartridge, not the launcher. `roms_pinned`: randomized,
        # so the run admits no other.
        "roms": {pid: rom_url.format(player=pid) for pid in PIDS} if rom_url else {},
        "roms_pinned": bool(rom_url) and roms_pinned,
        # the download labels name the file as handed out (.gbc for Yellow and pureRGB)
        "rom_ext": rom_ext or dict.fromkeys(PIDS, ".gb"),
        "concise_title": title or "Soul Link",
        "poll_url": poll_url,
        # False for a stopped run on the Manager: the board is what it persisted, and
        # "waiting for hello" would be a lie about a server that is not listening.
        "live": live,
        # each player's status line on their Now card (connection_state)
        "conn": {pid: connection_state(players.get(pid) or {}, live) for pid in PIDS},
        "rom_label": variant_label,
        "hp_pct": hp_pct,
        "hp_class": hp_class,
        "bond_glyph": bond_glyph,
    }

# ── the run's story (/timeline) ──────────────────────────────────────────────────────────
# Built from what the run already keeps; nothing new is recorded for it. links.json holds
# no link time, only `killed_at` (UTC) on a death or dead zone. Link and memorial times come
# from the event log (events.json, local time), which keeps the last 200 events, so an
# older link or burial has no time and the page says so instead of inventing one.

_EPOCH = datetime.min.replace(tzinfo=UTC)
_CAUSES = {"battle": "fainted in battle", "whiteout": "whiteout", "dead_zone": "dead zone",
           "identity_lost": "identity lost", "npc_trade_clause": "NPC trade clause violation"}


def _when(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.astimezone()   # naive (the event log) is local time; aware is converted to it


def _mon(src: dict, pid: str) -> dict | None:
    if not src.get(f"{pid}_key"):
        return None
    return {"name": src.get(f"{pid}_nickname") or src.get(f"{pid}_species_name") or "?",
            "species": src.get(f"{pid}_species_name") or "",
            "level": src.get(f"{pid}_level") or 0,
            "sprite": src.get(f"{pid}_sprite_html") or ""}


def timeline(status: dict) -> dict:
    """The run in the order it happened: pairs formed, deaths, dead zones, burials, then
    the areas still open. Each entry's `ts` is None when the run never recorded one."""
    events = status.get("recent_events") or []

    def first_event(kind: str, area: str) -> datetime | None:
        times = [t for e in events if e.get("type") == kind and e.get("area_id") == area
                 and (t := _when(e.get("ts")))]
        return min(times) if times else None

    killfeed = {k.get("area_id"): k for k in status.get("killfeed") or []}
    items = []   # (sort time, order, entry)
    for i, link in enumerate(status.get("links") or []):
        area = link.get("area_id") or ""
        name = link.get("area_display") or area_name(area)
        a, b = _mon(link, "a"), _mon(link, "b")
        death = killfeed.get(area)
        if a and b and (death or {}).get("cause") != "dead_zone":
            ts = first_event("linked", area)
            # A link the event log no longer holds is older than everything it does hold.
            items.append((ts or _EPOCH, i, {"kind": "linked", "ts": ts, "area": name, "a": a, "b": b}))
        if death:
            ts = _when(death.get("killed_at"))
            killer = death.get("killer") or {}
            entry = {"kind": "dead_zone" if death.get("cause") == "dead_zone" else "death",
                     "ts": ts, "area": name, "a": _mon(death, "a"), "b": _mon(death, "b"),
                     "cause": _CAUSES.get(death.get("cause"), (death.get("cause") or "").replace("_", " ")),
                     "killer": killer.get("trainer_name") or killer.get("species_name") or "",
                     "by": (death.get("initiating_player") or "").upper(),
                     "met": {pid: (death.get(f"{pid}_enc_species_name"), death.get(f"{pid}_enc_level"))
                             for pid in PIDS if death.get(f"{pid}_enc_species_name")}}
            items.append((ts or _EPOCH, i, entry))
            if link.get("status") == "memorial":
                mts = first_event("memorialize", area)
                # unrecorded: placed just after the death it follows
                items.append((mts or ts or _EPOCH, i + 0.5,
                              {"kind": "memorial", "ts": mts, "area": name, "a": a, "b": b}))
    items.sort(key=lambda t: (t[0], t[1]))
    for _, _, e in items:
        e["when"] = e["ts"].strftime("%Y-%m-%d %H:%M") if e["ts"] else ""

    resolved = {link.get("area_id") for link in status.get("links") or []}
    pending = status.get("pending_captures") or {}
    locks = status.get("encounter_locks") or {}
    open_ids = sorted({a for a, s in (status.get("area_states") or {}).items()
                       if s not in ("linked", "dead_zone") and a not in resolved}
                      | set(pending) | set(locks))
    open_areas = []
    for area in open_ids:
        entry = {
            "area": area_name(area),
            "caught": {
                pid: mon.get("nickname") or mon.get("species_name") or "?"
                for pid, mon in (pending.get(area) or {}).items()
            },
        }
        seeking = {
            pid: lock.get("species_name") or "?"
            for pid, lock in (locks.get(area) or {}).items()
            if pid not in (pending.get(area) or {})
        }
        if seeking:
            entry["seeking"] = seeking
        open_areas.append(entry)
    entries = [e for _, _, e in items]
    return {"entries": entries, "open_areas": open_areas,
            "untimed": sum(1 for e in entries if not e["ts"])}
