"""
SLink TCP server — Soul Link Nuzlocke coordinator.

Each BizHawk instance connects via LuaSocket and sends newline-delimited JSON
events (area_enter, capture, faint, etc.).  The server responds with a
newline-delimited JSON object: {"commands": [...]}.

A separate HTTP status page is served on --http-port (default 8080) for
live monitoring in a browser.

Transport: asyncio.start_server for the game protocol (no aiohttp on that path).
Status UI:  aiohttp on a separate port — browser-only, never touched by Lua.

Run:
    python -m server.server [--host 0.0.0.0] [--port 54321] [--http-port 8080]
"""

import argparse
import asyncio
import contextlib
import copy
import functools
import hmac
import html
import json
import logging
import logging.handlers
import os
import re
import shutil
import time
from collections import deque
from datetime import datetime

from server import calc_files
from server.http_safety import allow_hosts, csrf_protection, theme_cache
from server.lua_literals import lua_comment, lua_string
from server.overlay_catalog import build_index_context as _build_stream_index_context
from server.ui_capabilities import ui_capabilities

try:
    from aiohttp import web as aiohttp_web
    AIOHTTP_AVAILABLE = True
except ImportError:
    AIOHTTP_AVAILABLE = False

try:
    from .state import DATA_DIR, LINKS_PATH, SoulLinkState
except ImportError:
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from server.state import DATA_DIR, LINKS_PATH, SoulLinkState

try:
    from .obs_controller import (
        AREA_GROUPS,
        OBSController,
        classify_area,
        obs_config_path,
    )
except ImportError:
    from server.obs_controller import (
        AREA_GROUPS,
        OBSController,
        classify_area,
        obs_config_path,
    )

try:
    from .templating import resolve_layout, resolve_theme, setup_templating
except ImportError:
    from server import calc_files
import aiohttp_jinja2

from server.templating import resolve_layout, resolve_theme, setup_templating

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

VALID_PLAYERS = {"a", "b"}
_EVENTS_MAX = 200  # max entries kept in memory and written to events.json


def _configure_logging(data_dir: str | None, verbose: bool) -> None:
    """Add a RotatingFileHandler next to links.json.

    Without ``--verbose``: both file and console stay at INFO.
    With    ``--verbose``: file and console are both lowered to DEBUG so every
    state-machine decision is captured for post-mortem analysis.
    """
    log_dir = data_dir if data_dir else DATA_DIR
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, "slink.log")

    fh = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    fh.setLevel(logging.DEBUG if verbose else logging.INFO)
    fh.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    ))
    root = logging.getLogger()
    root.addHandler(fh)

    if verbose:
        root.setLevel(logging.DEBUG)
        # Also lower the existing console StreamHandler so DEBUG appears on screen.
        for h in root.handlers:
            if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.handlers.RotatingFileHandler):
                h.setLevel(logging.DEBUG)
        log.info(f"[--verbose] DEBUG logging enabled → {log_path}")
    else:
        log.info(f"Logging to {log_path}")


# ── Damage Calculator integration ────────────────────────────────────────────

# The calc's files (dist / src resolution) live in server/calc_files.py, shared with the Manager.

def _build_mon_entry(key, detail, adapter):
    """Build a JSON-serialisable dict for one mon, suitable for /api/calc/mons."""
    sid = detail.get("species_id", 0)
    if not sid:
        return None
    # Every name leaves in the calc's spelling. calc_species (not the species_name+calc_name
    # pair directly) so an adapter whose display name and calc name diverge for the same id
    # (pureRGB's 7 alternate forms, which display in-game with their base species' name) can
    # override just the calc-facing lookup — see base.GamePresentationAdapter.calc_species.
    species = adapter.calc_species(sid)
    # Nature/ability are adapter facts: Gen 1/2 have no personality-value nature
    # (calc_nature() returns None) and no abilities at all (supports_abilities() False).
    nature = adapter.calc_nature(key)
    has_ability = adapter.supports_abilities()
    abl_name = adapter.calc_name("ability", detail.get("ability_name", "")
                                 or adapter.ability_name(detail.get("ability_id", 0), sid)
                                 ) if has_ability else ""
    item_id  = detail.get("held_item_id", 0)
    item     = adapter.calc_name("item", adapter.item_name(item_id)) if item_id else ""
    raw_moves = [m for m in (detail.get("moves") or []) if m][:4]
    moves = []
    for m in raw_moves:
        name = adapter.move_name(m) if isinstance(m, int) else m
        if isinstance(name, str) and name:
            moves.append(adapter.calc_name("move", name))
    level    = detail.get("level", 0)
    nick     = detail.get("nickname", "")
    hp       = detail.get("hp", 0)
    # maxHP is only absent for a Gen 1 enemy the client hasn't decoded yet (docs/protocol.md
    # §4.3 maxHP); null it through rather than the old max(..., 1) that forced hp_pct to 100.
    raw_maxhp = detail.get("maxHP")
    maxhp    = max(raw_maxhp, 1) if raw_maxhp else None
    hp_pct   = max(0, min(100, int(hp / maxhp * 100))) if maxhp else None
    disp     = f"{species} ({nick})" if nick and nick != species else species
    lines    = [disp + (f" @ {item}" if item else "")]
    if has_ability:
        lines += [f"Ability: {abl_name}" if abl_name else "Ability: None"]
    lines += [f"Level: {level}"]
    if nature:
        lines += [f"{nature} Nature"]
    for m in moves:
        lines.append(f"- {m}")
    return {
        "key":           key,
        "nickname":      nick,
        "species_name":  species,
        "level":         level,
        "nature":        nature,
        "ability_name":  abl_name,
        "item_name":     item,
        "moves":         moves,
        "hp_pct":        hp_pct,
        "hp":            hp,
        "maxHP":         maxhp,
        "status_cond":   detail.get("status_cond", 0),
        "stat_stages":   detail.get("stat_stages"),
        "slot":          detail.get("slot", 999),
        "active":        detail.get("active", False),
        "showdown_paste": "\n".join(lines),
        "calc_stats":    detail.get("calc_stats"),
    }


def _foe_detail(em, adapter):
    """A client enemy_party entry reshaped as the party detail _build_mon_entry reads. The one
    place both calc paths (/api/calc/mons and the board preview) build an enemy from, so the
    preview can't model a different foe than the full calc does."""
    detail = {
        "species_id":   em.get("species_id", 0),
        "level":        em.get("level", 0),
        "nickname":     "",
        "hp":           em.get("hp", 0),
        # No default: an enemy the client hasn't decoded a battle struct for
        # yet (or a generation that doesn't send one) stays maxHP=None, not a
        # fake maxHP=1 that used to force hp_pct to 100.
        "maxHP":        em.get("maxHP"),
        "held_item_id": em.get("held_item_id", 0),
        "ability_id":   em.get("ability_id", 0),
        "ability_name": "",
        "moves":        em.get("moves", []),
        "status_cond":  em.get("status_cond", 0),
        "stat_stages":  em.get("stat_stages"),
        # Gen 1 only (lua/gen1/client.lua enemy_party): dvs_raw from the live
        # wEnemyMon struct always; blob_hex (trainer battles only) additionally
        # carries stat exp. adapter.calc_stats() ignores both when absent.
        "dvs_raw":      em.get("dvs_raw"),
        "blob_hex":     em.get("blob_hex"),
        "pp":           em.get("pp"),
    }
    detail["calc_stats"] = adapter.calc_stats(detail)
    return detail


def _calc_trainer_label(brief, enemy):
    """The calc setdex trainer key for a roster trainer ("*Rival Blue Set 2" -> "Rival Blue",
    the key the bridge's trainer index uses), or "" when there is no roster calc_label or
    its party doesn't match at least half the live enemy species. On "" the bridge falls
    back to matching the party by species + level."""
    label = (brief or {}).get("calc_label") or ""
    if not label or not enemy:
        return ""
    roster = {m.get("species") for m in brief.get("party") or []}
    if 2 * sum(e["species_name"] in roster for e in enemy) < len(enemy):
        return ""
    return re.sub(r"\s+Set\s+\d+$", "", label.lstrip("*"))







# ── Raw pages ──────────────────────────────────────────────────────────────────



def _bot_load_config(data_dir: str | None) -> dict:
    """Load bot config from data/twitch_bot.json. Returns defaults if absent."""
    bot_dir = data_dir or DATA_DIR
    path = os.path.join(bot_dir, "twitch_bot.json")
    defaults = {"channel": "", "nick": "", "prefix": "!", "command_cooldown_sec": 5, "enabled": True}
    if not os.path.exists(path):
        return dict(defaults)
    try:
        with open(path) as f:
            cfg = json.load(f)
        for k, v in defaults.items():
            cfg.setdefault(k, v)
        return cfg
    except Exception:
        return dict(defaults)



def _bot_save_config(data_dir: str | None, cfg: dict):
    """Save bot config to data/twitch_bot.json. Tokens are never stored here — use env vars."""
    bot_dir = data_dir or DATA_DIR
    path = os.path.join(bot_dir, "twitch_bot.json")
    safe = dict(cfg)
    os.makedirs(bot_dir, exist_ok=True)
    with open(path, "w") as f:
        json.dump(safe, f, indent=2)


# ── per-connection handler ─────────────────────────────────────────────────────

# A client is considered stale once it has said nothing for this long. The Gen 3 client ticks
# about twice a second and the dashboard polls every 2s, so 10s is far outside normal jitter.
STALE_AFTER_SECS = 10


def _age_secs(ts):
    """Whole seconds since `ts` (an epoch float), or None if there is no timestamp."""
    if not ts:
        return None
    return max(0, int(time.time() - ts))


def _age_label(age):
    """Human 'how long ago' for the players panel. None -> em dash."""
    if age is None:
        return "—"
    if age < 60:
        return f"{age}s ago"
    if age < 3600:
        return f"{age // 60}m ago"
    return f"{age // 3600}h ago"


def _area_tag(name: str) -> str:
    """Abbreviate an area name for the info panel's 4-character label column.

    "Route 3" -> "RT03" reads better than a blind truncation to "Rout", which is why this is not
    just a slice. Purely string handling — no game data — so it is not adapter territory; the name
    itself comes from the adapter.
    """
    m = re.match(r"\s*route\s*(\d+)", name or "", re.I)
    if m:
        return f"RT{int(m.group(1)):02d}"
    return re.sub(r"[^0-9A-Za-z]", "", name or "").upper()[:4]


class _WireTap:
    """`--wire-log`: a JSONL transcript of one connection's lines (card gen3-P1-C1-3, C1-3b).

    Wraps the connection's StreamWriter, so every reply is captured where it is actually
    written and `_respond`'s seven call sites stay untouched; `c2s` is called once per
    inbound line, right after it parses. Nothing here is constructed unless --wire-log
    named a directory, so the flag's absence costs one `if` per connection and per line.

    `t` is capture order: a per-FILE monotonic counter starting at 1, shared by every
    direction and every connection that ever writes to that file (files outlive a single
    connection -- see below) -- never the protocol's own `seq`, which restarts on
    reconnect and only exists on c2s lines. A reply carries `req`, the local `t` or a
    cross-sink {sink, t} reference, or null. Every record carries a process-wide `conn` id.
    See tests/fixtures/gen3/wire/README.md.

    Only a string `msg["player"]` in VALID_PLAYERS gets its own file:
    `wire_a.jsonl` / `wire_b.jsonl`.
    Everything else -- an unrecognised id, a line that parsed as JSON but wasn't an object --
    goes to one bounded sink, `wire_rejected.jsonl`, capped at `_REJECTED_CAP` lines and then
    silently dropped. That caps this process at three open handles, ever, and they outlive
    the connection that opened them by design: a reconnect's lines land in the same file,
    which is why `_connect`/`_disconnect` meta records exist -- without them a reconnect
    would be invisible in the transcript.
    """

    _REJECTED_CAP = 200  # wire_rejected.jsonl: bounded, then dropped -- never unbounded disk use
    _RAW_CAP = 1024       # a malformed (non-dict) line is recorded truncated to this many bytes
    _next_conn = 0

    def __init__(self, writer, dirpath: str, files: dict):
        self._writer = writer
        self._dir = dirpath
        # key ("a" | "b" | "rejected") -> {"handle": file, "t": int, "lines": int}.
        # Shared by every connection in this process, so a key's handle/counter persist
        # across reconnects -- that is what makes `t` meaningful as capture order.
        self._files = files
        self.player: str | None = None  # set once a line names a valid player; sticky after that
        _WireTap._next_conn += 1
        self.conn = _WireTap._next_conn
        self._request: tuple[str, int] | None = None
        self._last_sink: str | None = None
        self._connect_written = False

    def __getattr__(self, name):  # drain / wait_closed / get_extra_info
        return getattr(self._writer, name)

    def close(self):
        try:
            if self._last_sink is not None:
                self._emit(self._last_sink, "meta", msg={"event": "_disconnect"})
        except Exception as exc:  # noqa: BLE001
            log.warning(f"[wire-log] dropped disconnect: {exc!r}")
        return self._writer.close()

    def write(self, data: bytes):
        # _respond always writes one json.dumps'd {"commands": [...]} line -- this parse is
        # not expected to fail, but the tap must never crash the reply path over it.
        try:
            msg = json.loads(data.decode("utf-8", errors="replace"))
            key = self.player or "rejected"
            req = None
            if self._request is not None:
                sink, t = self._request
                req = t if sink == key else {"sink": sink, "t": t}
            self._emit(key, "s2c", msg=msg, req=req)
        except Exception as exc:  # noqa: BLE001
            self._request = None
            log.warning(f"[wire-log] dropped reply: {exc!r}")
        self._writer.write(data)

    def c2s(self, line: str, msg) -> None:
        """One inbound line, called right after handle_client's own `json.loads(line)`.

        The wire protocol expects an object, but `json.loads` on a bare `42` or `[1, 2]` also
        succeeds and hands back something with no `.get` -- that must never raise here, so a
        non-dict `msg` is recorded as a malformed line (`raw`, truncated, `msg: null`) instead
        of read like an event.
        """
        self._request = None
        try:
            if not isinstance(msg, dict):
                self.c2s_raw(line)
                return
            player = msg.get("player")
            key = player if isinstance(player, str) and player in VALID_PLAYERS else "rejected"
            if key != "rejected":
                self.player = key
            logged = dict(msg)
            if "instrumentation_key" in logged:
                logged["instrumentation_key"] = "[redacted]"
            t = self._emit(key, "c2s", msg=logged)
            if t is not None:
                self._request = (key, t)
        except Exception as exc:  # noqa: BLE001
            log.warning(f"[wire-log] dropped request: {exc!r}")

    def c2s_raw(self, line: str) -> None:
        self._request = None
        try:
            raw = line.encode("utf-8")[:self._RAW_CAP].decode("utf-8", errors="replace")
            # A replacement for a partial final codepoint can expand beyond the byte cap.
            while len(raw.encode("utf-8")) > self._RAW_CAP:
                raw = raw[:-1]
            t = self._emit("rejected", "c2s", raw=raw)
            if t is not None:
                self._request = ("rejected", t)
        except Exception as exc:  # noqa: BLE001
            log.warning(f"[wire-log] dropped raw request: {exc!r}")

    def _emit(self, key: str, direction: str, msg=None, raw=None, req=None):
        """Write one record to `key`'s file. Returns the `t` it was given, or None if dropped.

        A transcript is evidence, never a dependency: any failure here (disk full, bad DIR,
        permissions, an unopenable file) is logged and dropped so the tap can never kill a
        client (adapter-guard finding on b0e0538) -- same for the `wire_rejected.jsonl` cap.
        """
        try:
            if not self._connect_written and direction != "meta":
                if self._emit(key, "meta", msg={"event": "_connect"}) is None:
                    return None
                self._connect_written = True
            entry = self._files.get(key)
            if entry is None:
                os.makedirs(self._dir, exist_ok=True)
                name = "wire_rejected.jsonl" if key == "rejected" else f"wire_{key}.jsonl"
                # The handle outlives this call by design: it stays open for the process.
                entry = self._files[key] = {
                    "handle": open(os.path.join(self._dir, name), "w", encoding="utf-8"),  # noqa: SIM115
                    "t": 0, "lines": 0,
                }
            if key == "rejected" and entry["lines"] >= self._REJECTED_CAP:
                return None
            entry["t"] += 1
            t = entry["t"]
            record = {"dir": direction, "t": t, "conn": self.conn}
            if direction == "s2c":
                record["req"] = req
            if raw is not None:
                record["raw"] = raw
                record["msg"] = None
            else:
                record["msg"] = msg
            entry["handle"].write(json.dumps(record) + "\n")
            entry["handle"].flush()
            entry["lines"] += 1
            self._last_sink = key
            return t
        except Exception as exc:  # noqa: BLE001
            self._request = None
            log.warning(f"[wire-log] dropped {direction} line for {key}: {exc!r}")
            return None


# `foundation` is OPTIONAL on the wire, and "absent" is not "empty": a hello that omits
# the key gets the derived foundation, a hello that sends `null`/`""`/`0` is asserting
# something and asserting it wrong. A default of None could not tell those apart (json
# null decodes to None), so the absent case gets a sentinel of its own. Module level, not
# a class attribute: `_mixed_games_error` is also called unbound on a stub self.
_FOUNDATION_ABSENT = object()

# Every artifact_kind a hello may declare (docs/protocol.md §2.2). A non-empty string outside
# this set used to be admitted at face value and committed for the run's lifetime (gen2-N16,
# carried from N15's ca0888b): _kind()/pairing_kind() would silently pass it through instead
# of catching a typo'd or made-up kind. Pinned to tests/unit/protocol_schema.py's ARTIFACT_KINDS
# by test_the_servers_known_artifact_kinds_match_the_wire_schema so the two lists cannot drift.
_KNOWN_ARTIFACT_KINDS = frozenset({"clean", "overlay", "rand", "rand_overlay", "named", "companion"})


def _companion_refusal(player_id: str, msg: dict) -> str:
    """Patch-first (owner 2026-10-02): why this hello's cartridge lacks the SLink companion patch its
    title requires, or "". The adapter CLASS owns the rule (`companion_refusal`, a class lookup like
    `pairing_kind`), so no game or title is named here. Module level: `_decide_admission` is also
    called unbound on a stub self."""
    from server.adapters import adapter_class_for_rom_type
    rom_type = msg.get("rom_type", "")
    cls = adapter_class_for_rom_type(rom_type) if isinstance(rom_type, str) and rom_type else None
    why = cls.companion_refusal(msg) if cls else None
    return f"Slot {player_id.upper()}: {why}" if why else ""


def _randomized_binding_error(rom_type, kind, committed_rom_type, committed_kind, *, candidate_class=None) -> str:
    """Both halves of the run identity require an explicit randomized-title binding."""
    from server.adapters import GameRulesAdapter, adapter_class_for_rom_type

    for title, declared, prefix, cls in (
        (rom_type, kind, "", candidate_class),
        (committed_rom_type, committed_kind, "committed run: ", None),
    ):
        if declared not in ("rand", "rand_overlay") or (prefix and not title):
            continue
        cls = cls or (adapter_class_for_rom_type(title) if isinstance(title, str) else None) or GameRulesAdapter
        supports = getattr(cls, "supports_randomized", None)
        if not callable(supports) or not supports(title):
            return (f"{prefix}randomized cartridges are not supported for {title}: "
                    "no randomized-ROM binding")
    return ""


def prompt_event_type(text: str) -> str | None:
    """The events-log row a capture reply's gui_prompt stands for: "violation", "reroll" or None.

    Rejections are state.py's clause prompts ("[x] <X> clause: ...", bonus pair "... - retry") and the
    species-clause "[x] Dup <name>"; the pre-f1d79c72 texts ended "-- catch again!".
    """
    lower = text.lower()
    if "reroll" in lower:
        return "reroll"
    if "catch again" in lower or " clause: " in lower or text.startswith("[x] Dup "):
        return "violation"
    return None


def _requires_loaded_state(handler):
    @functools.wraps(handler)
    async def guarded(self, request, *args, **kwargs):
        if self.state.load_failed:
            return aiohttp_web.json_response(
                {"ok": False, "error": "run could not be loaded; restore a backup or reset first"}, status=409)
        return await handler(self, request, *args, **kwargs)
    return guarded


class SLinkServer:
    def __init__(self, data_dir: str = None, run_id: str = None,
                 run_name: str = "", tcp_port: int = 0,
                 manager_port: int = 0,
                 species_lock: bool = False, gender_lock: bool = False,
                 type_lock: bool = False, explode_mode: bool = False,
                 rival_team_swap: bool = False, overworld_presence: bool = False,
                 native_messages: bool = False, native_sounds: bool = False,
                 battle_calc: bool = True, pc_trade_npc: bool = True, phone_calls: bool = True,
                 retry_same_species: bool = True,
                 ignore_wild_faints: bool = False,
                 instrumentation_keys: dict[str, str] | None = None,
                 wire_log: str = None):
        # --wire-log DIR: capture every line of every connection (card gen3-P1-C1-3).
        # None (the default) means no _WireTap is ever built and nothing changes.
        self._wire_log = wire_log
        self._wire_files: dict = {}
        self.dispatch_errors = {"a": 0, "b": 0}
        self._data_dir = data_dir  # None → use global DATA_DIR (backward compat)
        self._run_id   = run_id
        self._run_name = run_name
        self._tcp_port = tcp_port
        self._manager_port = manager_port
        configured_keys = instrumentation_keys if instrumentation_keys is not None else {
            "a": os.environ.get("SLINK_PLAYER_KEY_A", ""),
            "b": os.environ.get("SLINK_PLAYER_KEY_B", ""),
        }
        self._instrumentation_keys = {
            player: key for player, key in configured_keys.items()
            if player in VALID_PLAYERS and isinstance(key, str) and key
        }
        if len(set(self._instrumentation_keys.values())) != len(self._instrumentation_keys):
            raise ValueError("instrumentation keys must be unique per player")
        self.state = SoulLinkState.load(data_dir=data_dir,
                                        species_lock=species_lock,
                                        gender_lock=gender_lock,
                                        type_lock=type_lock,
                                        explode_mode=explode_mode,
                                        rival_team_swap=rival_team_swap,
                                        overworld_presence=overworld_presence,
                                        native_messages=native_messages,
                                        native_sounds=native_sounds,
                                        battle_calc=battle_calc,
                                        pc_trade_npc=pc_trade_npc,
                                        phone_calls=phone_calls,
                                        retry_same_species=retry_same_species,
                                        ignore_wild_faints=ignore_wild_faints)
        self.state.presentation_key_in_use = self._presentation_key_in_use
        self.state.on_trade_outcome = self._journal_trade
        # Game adapter — shared with state machine for consistent behavior.
        # Provides both rules and presentation methods.
        self.adapter = self.state.adapter
        # A restored run already committed its artifact kind; the adapter is rebuilt without it.
        if self.state.artifact_kind:
            self.adapter.set_artifact_kind(self.state.artifact_kind)
        # A run may be played on ROMs randomized per player -- same settings, different
        # seeds -- so "what does this route hold" has a different answer for each of them.
        # get_adapter() is not a singleton, so one adapter per player is cheap; absent an
        # entry here a player simply uses the run-global adapter and the shipped tables.
        self._player_adapters: dict[str, object] = {}
        self.state.player_adapter_for = self.adapter_for

        # ── Admission ─────────────────────────────────────────────────────────
        # A run built from randomized ROMs records, per player, the fingerprint of the
        # cartridge the Manager made for them. Until a client proves it is running that
        # cartridge it is ADMITTED to connect and to say hello, and to nothing else --
        # because every other event mutates rule state, and rule state built on the wrong
        # ROM is worse than no run at all.
        #
        # A run with no contract has nothing to check and admits everyone, which is every
        # vanilla run and the entire existing test suite.
        self._rom_contract_mtime: float | None = None
        self._rom_contract = self._load_rom_contract()
        self.admission: dict[str, dict] = {}
        # Players whose last hello named a rom_type nothing routes (see handle_client).
        self._rom_type_rejected: set[str] = set()
        # Track live connections: player_id → {rom_type, last_event, connected}
        self.connected_players: dict[str, dict] = {}
        self._client_connections: set[asyncio.StreamWriter] = set()
        self._client_writers: dict[str, set[asyncio.StreamWriter]] = {}
        # Refused hellos still count as transport connections, not gameplay owners.
        self._presence_writers: dict[str, set[asyncio.StreamWriter]] = {}
        # Per-player display data (updated from events, used only for status page)
        self.player_area: dict[str, str] = {"a": "", "b": ""}
        self.player_area_id: dict[str, str] = {"a": "", "b": ""}  # raw area_id for state lookups
        self.player_ball_count: dict[str, int] = {"a": 0, "b": 0}
        self.player_badges: dict[str, int] = {"a": 0, "b": 0}
        self.player_gender: dict[str, int | None] = {"a": None, "b": None}
        self.player_kanto_badges: dict[str, int] = {"a": 0, "b": 0}
        self.trainer_name: dict[str, str] = {
            "a": self.state.trainer_names.get("a", ""),
            "b": self.state.trainer_names.get("b", ""),
        }
        self.pc_boxes: dict[str, list] = {"a": [], "b": []}
        # KEY-SCOPE-5: the key_change collision check's box census, per player. `capable`: this
        # client session advertises `pc_boxes_generation`; `boxes`: the entries of its newest complete
        # census (None = missing or stale, which fails closed). Presence-based for other clients.
        self.box_census: dict[str, dict] = {pid: {"capable": False, "gen": -1, "boxes": None}
                                            for pid in ("a", "b")}
        # KEY-SCOPE-5: server-owned connection generation per player; the newest hello wins.
        self._conn_gen: dict[str, int] = {}
        # key → {level, hp, maxHP, nickname, species_id, gender} — best-effort party snapshot
        self.party_details: dict[str, dict[str, dict]] = {"a": {}, "b": {}}
        # Persistent per-monKey cache of display info (species_id, nickname, level, gender).
        # Survives party↔box transitions so sprites don't go blank between ticks.
        self._mon_cache: dict[str, dict] = {}
        # Orphan keys already warned about — suppress repeat warnings on every tick.
        self._warned_orphan_keys: set[str] = set()
        self._warned_memorial_keys: set[tuple[str, str]] = set()
        # Last `link_panel` payload sent to each player, so it is re-sent only on change.
        self._last_panel_sig: dict[str, str] = {"a": None, "b": None}
        # Battle state: in_battle flag + enemy team snapshot
        self.battle_state: dict[str, dict] = {
            "a": {"in_battle": False, "is_trainer_battle": False, "enemy_party": [],
                  "trainer_id": 0, "opponent_name": "", "opponent_class": "",
                  "is_doubles": False},
            "b": {"in_battle": False, "is_trainer_battle": False, "enemy_party": [],
                  "trainer_id": 0, "opponent_name": "", "opponent_class": "",
                  "is_doubles": False},
        }
        # Ring buffer of recent events for the stream overlay event feed.
        self._recent_events: deque[dict] = deque(maxlen=_EVENTS_MAX)
        self._events_path = (
            os.path.join(data_dir, "events.json") if data_dir
            else os.path.join(DATA_DIR, "events.json")
        )
        self._load_events()
        # SSE: set of asyncio.Queue (one per connected browser).
        # Each queue holds at most 1 item (coalescing — latest snapshot wins).
        self._sse_clients: set[asyncio.Queue] = set()
        self._sse_heartbeat_task: asyncio.Task | None = None
        # Rolling backup: copy links.json every 5 min when both players connected.
        self._backup_task: asyncio.Task | None = None
        self._backup_interval = 300  # seconds
        self._backup_max = 6
        self._bot_activity = []   # ring buffer, max 50 entries [{ts, text}]
        self._bot_last_error: str = ""
        self._bot_task = None
        self._bot_instance = None
        # OBS WebSocket integration
        _obs_cfg = obs_config_path(data_dir)
        self.obs = OBSController(_obs_cfg)

    def _get_sprite_html(self, species_id: int, form: int = 0, player_id: str = "") -> str:
        """Get sprite HTML by delegating to the game adapter -- `player_id`'s own when given
        (Gen 2 titles draw from their own pack), else the run's.

        Optional `form` byte (default 0) is the alt-form discriminator from Block B
        (Gen 4+). Adapters that ignore it still produce correct base-form sprites.
        """
        return (self.adapter_for(player_id) if player_id else self.adapter).sprite_html(species_id, form)

    _METHOD_ICON: dict[str, str] = {
        "Day":        "☀",
        "Night":      "🌙",
        "Surfing":    "🌊",
        "Rock Smash": "🪨",
        "Old Rod":    "🎣 Old",
        "Good Rod":   "🎣 Good",
        "Super Rod":  "🎣 Super",
    }

    def _ingest_rom_content(self, player_id: str, payload: dict) -> None:
        """Adopt a player's own cartridge tables, or leave them without any.

        Failure is NOT silent and NOT a fallback: a player whose payload we cannot read
        keeps no ROM tables and the UI says the data is unavailable for them. Falling back
        to the shipped tables would print retail species beside a randomized cartridge,
        which is the exact misinformation this exists to remove.
        """
        from server.adapters import game_id_for_rom_type, get_adapter
        failed = False
        try:
            tables = self.adapter.ingest_rom_content(payload)
        except Exception as exc:                      # noqa: BLE001 - report, never adopt
            log.warning("[%s] rom_content rejected — encounter data will be shown as "
                        "unavailable for this player: %s", player_id, exc)
            tables, failed = {}, True
        if tables is None:
            return                                    # this generation cannot read its ROM

        rom_type = self.connected_players.get(player_id, {}).get("rom_type", "")
        game_id = game_id_for_rom_type(rom_type) or self.adapter.game_id
        # the run's committed kind (tests stub the state without one: default clean)
        adapter = get_adapter(game_id, is_rr=self.state.is_rr, rom_type=rom_type,
                              artifact_kind=getattr(self.state, "artifact_kind", "") or "clean")
        adopt = getattr(adapter, "use_rom_encounters", None)
        if adopt is None:
            return
        # THREE STATES, and the middle one is the reason this is not a plain boolean:
        #   no entry here  -> nobody reported a ROM; the shipped tables are all we have
        #   {}             -> a client TRIED and we could not read it; show nothing
        #   populated      -> this cartridge's own tables
        # An unreadable payload must NOT fall back to the shipped tables: the client only
        # sends this when it can see its ROM, so a failure is evidence something is unusual
        # about that ROM, and retail species printed beside a randomized cartridge is the
        # exact misinformation this whole path exists to remove.
        try:
            adopt(tables)
        except Exception as exc:                      # noqa: BLE001 - e.g. tables of another title: show nothing
            log.warning("[%s] rom tables could not be adopted — encounter data will be shown as "
                        "unavailable for this player: %s", player_id, exc)
            tables, failed = {}, True
            adopt(tables)
        self._player_adapters[player_id] = adapter
        if failed:
            log.info("[%s] encounter data marked unavailable", player_id)
        else:
            log.info("[%s] using this cartridge's own encounter tables (%d top-level keys)",
                     player_id, len(tables))

    def _load_rom_contract(self) -> dict | None:
        """The randomized-ROM contract for this run, if the Manager wrote one.

        Re-read rather than cached for the run's lifetime. The Manager writes this file from
        `handle_randomize` while the server is already up, so a snapshot taken in __init__
        made the gate depend on the ORDER the two were started in: a run whose server came
        up first kept `_rom_contract = None` and admitted everyone, forever, with no way to
        notice. The mtime check keeps it to one stat() per hello.
        """
        base = self._data_dir or DATA_DIR
        path = os.path.join(base, "rom_contract.json")
        if not os.path.exists(path):
            self._rom_contract_mtime = None
            return None
        try:
            with open(path) as f:
                contract = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            # Fail CLOSED and say so. A contract we cannot read is not the same as no
            # contract: the run was built from randomized ROMs and we have lost the only
            # record of which ones, so admitting anyone would defeat the check entirely.
            log.error("rom_contract.json is unreadable (%s) — every player will be "
                      "rejected until it is fixed or removed", exc)
            return {"unreadable": True, "players": {}}
        log.info("run is bound to randomized ROMs (UPR %s, categories %s)",
                 contract.get("upr_version", "?"), contract.get("categories", []))
        return contract

    def _refresh_rom_contract(self) -> None:
        """Pick up a contract the Manager wrote after this server started."""
        base = self._data_dir or DATA_DIR
        path = os.path.join(base, "rom_contract.json")
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            mtime = None
        if mtime != self._rom_contract_mtime:
            self._rom_contract = self._load_rom_contract()
            self._rom_contract_mtime = mtime

    def _contracted_rom(self, player_id: str, msg: dict) -> dict | None:
        """The ROM-content payload for a randomized cartridge bound by the contract sha1 (`rom_contract_by_sha1`),
        whose client sends no rom_content: the Manager's own file for this player (server/cartridges.py writes
        roms/<pid>.<ext> beside rom_contract.json), as bytes, with the pinned sha1 the adapter re-checks. A missing
        file still yields a payload, so ingest fails and the player shows NO tables rather than the shipped ones."""
        if (msg.get("artifact_kind") not in ("rand", "rand_overlay")
                or self.adapter.rom_contract_by_sha1 is not True):
            return None
        want = (((self._rom_contract or {}).get("players") or {}).get(player_id) or {}).get("rom_sha1")
        roms, rom = os.path.join(self._data_dir or DATA_DIR, "roms"), None
        for name in (f"{player_id}.gbc", f"{player_id}.gb"):
            with contextlib.suppress(OSError):
                with open(os.path.join(roms, name), "rb") as f:
                    rom = f.read()
                break
        return {"rom": rom, "rom_type": msg.get("rom_type", ""), "rom_sha1": want}

    def _mixed_games_error(self, player_id: str, rom_type: str, artifact_kind: str,
                           foundation: object = _FOUNDATION_ABSENT, rom_content: object = None) -> str:
        """Why this hello cannot join the committed run, or "" when it can.

        The run is locked to one FOUNDATION and to one pairing kind. A foundation is a
        pack, not a game_id: Radical Red and vanilla FireRed share `Gen3Adapter`, so a
        game_id comparison paired a clean FR with a clean RR (docs/gen3/PLAN.md §5.1).
        Variants of one foundation -- Red beside Blue, FireRed beside LeafGreen -- pair.

        The foundation is DERIVED from the rom_type; the hello's own `foundation` is
        optional and may only agree (absent ⇒ derived, so an old client still connects).
        Both kind normalizations are pure CLASS lookups on the two foundations' adapters --
        no candidate adapter is installed to answer a hello that may be refused, and
        nothing here mutates the run.

        FAIL-CLOSED on every input, for the socket caller and for any direct one: the
        derivation IS the lock, so a rom_type that is missing, empty or not a routable
        string is refused rather than skipped. Skipping it let a hello with no rom_type
        past the lock entirely and on into `state.handle_event` (Codex cx-a66ab55a F1).
        """
        from server.adapters import (
            GameRulesAdapter,
            adapter_class_for_rom_type,
            foundation_for_rom_type,
            unrouted_rom_type_reason,
        )
        if not isinstance(rom_type, str) or not rom_type:
            return (f"Missing rom_type for slot {player_id.upper()}: a hello must name the "
                    f"cartridge it is running (got {rom_type!r})")
        got = foundation_for_rom_type(rom_type)
        if not got:
            return (f"Unknown rom_type {rom_type!r} for slot {player_id.upper()}: "
                    f"{unrouted_rom_type_reason(rom_type)}")
        if foundation is not _FOUNDATION_ABSENT and foundation != got:
            return (f"Foundation mismatch: slot {player_id.upper()} declares "
                    f"{foundation!r}, but its ROM type {rom_type!r} is {got!r}")
        if not isinstance(artifact_kind, str) or not artifact_kind:
            return (f"Bad artifact_kind for slot {player_id.upper()}: expected a string "
                    f"(got {artifact_kind!r})")
        if artifact_kind not in _KNOWN_ARTIFACT_KINDS:
            return (f"Bad artifact_kind for slot {player_id.upper()}: {artifact_kind!r} is not "
                    f"a known kind (expected one of {sorted(_KNOWN_ARTIFACT_KINDS)})")
        if refused := _randomized_binding_error(
                rom_type, artifact_kind, self.state.rom_type, self.state.artifact_kind):
            return refused
        want = foundation_for_rom_type(self.state.rom_type) if self.state.rom_type else ""
        if want and got != want:
            return (f"Mixed games: slot {player_id.upper()} runs {got}, "
                    f"this run is committed to {want}")

        # A vanilla cartridge the launcher admitted by header ("named": the optional companion
        # patch, a randomized vanilla dump) is a clean-layout artifact -- the patch is per
        # cartridge and announced per player (`panel`), so a clean Red beside a patched Blue is
        # the ordinary vanilla pairing, not a mixed one (review: Fable 2026-09-20 #1).
        def _kind(rt: str, kind: str, content: object = None) -> str:
            cls = adapter_class_for_rom_type(rt) if rt else None
            title_aware = getattr(cls, "pairing_kind_for_title", None)
            if title_aware is not None:
                return title_aware(rt, kind, content)
            return (cls or GameRulesAdapter).pairing_kind_for(kind, content)

        got_kind = _kind(rom_type, artifact_kind, rom_content)
        want_kind = _kind(self.state.rom_type or "", self.state.artifact_kind or "")
        if want_kind and got_kind != want_kind:
            return (f"Mixed artifact kinds: slot {player_id.upper()} runs a "
                    f"{artifact_kind!r} ROM, this run is committed to {self.state.artifact_kind!r}")
        return ""

    def _decide_admission(self, player_id: str, msg: dict) -> dict:
        """Re-run on EVERY hello, so a reconnect or a swapped ROM is re-checked.

        Returns the admission record; never raises. A player who cannot be admitted is not
        an error condition, it is a run that has not started for them yet.
        """
        # The same refusal as the wire seam in handle_client (HUD line there), so a hello that
        # reaches `_dispatch` another way is held to it too.
        needs_companion = _companion_refusal(player_id, msg)
        if needs_companion:
            return {"state": "rejected", "reason": needs_companion}
        payload, kind = msg.get("rom_content"), msg.get("artifact_kind", "clean")
        refused = _randomized_binding_error(
            msg.get("rom_type", self.state.rom_type), kind, self.state.rom_type, self.state.artifact_kind,
            candidate_class=type(self.adapter))
        if not refused and (kind in ("rand", "rand_overlay") or (not self._rom_contract and payload)):
            refused = self.adapter.refused_rom_content(payload, artifact_kind=kind)
        if refused:
            if kind in ("rand", "rand_overlay"):
                # Unverified reconnects must not display a previous cartridge's tables.
                # The caller still rolls back the staged identity/rule state on refusal.
                getattr(self, "_player_adapters", {}).pop(player_id, None)
            return {"state": "rejected", "reason": refused}
        if not self._rom_contract:
            if kind == "rand_overlay" and self.adapter.rom_contract_by_sha1 is True:
                # Bound only by the contract sha1: with no contract nothing vouches for the cartridge.
                return {"state": "rejected",
                        "reason": "randomized cartridges for this game must be made by the Manager "
                                  "(this run has no randomized-ROM contract)"}
            return {"state": "admitted", "reason": "no randomized-ROM contract for this run"}
        if self._rom_contract.get("unreadable"):
            return {"state": "rejected", "reason": "this run's rom_contract.json could not be read"}

        expected = (self._rom_contract.get("players") or {}).get(player_id) or {}
        want = expected.get("fingerprint")
        if self.adapter.rom_contract_by_sha1 is True:
            # A foundation that binds its cartridge by the full-ROM sha1 its client rehashes: the contract's
            # pin for this player, exactly. It applies even when the contract also carries a fingerprint
            # (then the fingerprint checks below follow); it is never the weaker fallback.
            want_sha1 = expected.get("rom_sha1")
            got_sha1 = msg.get("rom_sha1")
            if not (isinstance(want_sha1, str) and len(want_sha1) == 40):
                return {"state": "rejected",
                        "reason": f"the contract names no cartridge for player {player_id}"}
            if not isinstance(got_sha1, str) or not got_sha1:
                return {"state": "rejected",
                        "reason": "this run is bound to randomized ROMs, but the client did not "
                                  "report its cartridge sha1"}
            if got_sha1.lower() != want_sha1.lower():
                return {"state": "rejected",
                        "reason": (f"this is not the ROM built for player {player_id} "
                                   f"(sha1 {got_sha1.lower()[:12]}, expected {want_sha1.lower()[:12]})")}
            if not want:
                return {"state": "admitted", "reason": "cartridge sha1 matches the contract"}
        if not want:
            return {"state": "rejected",
                    "reason": f"the contract names no cartridge for player {player_id}"}
        payload = msg.get("rom_content")
        if not payload:
            return {"state": "rejected",
                    "reason": "this run is bound to randomized ROMs, but the client did not "
                              "report its cartridge — an older client, or a BizHawk build "
                              "with no flat ROM domain"}
        try:
            got = self.adapter.rom_content_fingerprint(payload)
        except Exception as exc:                      # noqa: BLE001
            # Distinct from a mismatch on purpose: "unreadable report" and "wrong ROM" send
            # whoever is debugging to different places.
            return {"state": "rejected",
                    "reason": f"the client's cartridge report could not be read: {exc}"}
        if got is None:
            return {"state": "admitted",
                    "reason": "this generation cannot fingerprint its ROM; nothing to check"}
        if got != want:
            return {"state": "rejected",
                    "reason": (f"this is not the cartridge built for player {player_id} "
                               f"(reported {got[:12]}, expected {want[:12]})")}
        # The full-ROM sha1 the preparation bound (PLAN A3): the tables can agree while the
        # build differs (an overlay or a re-randomized artifact), so it is checked AFTER the
        # fingerprint, when the client reports one; the wrong-cartridge verdict above keeps
        # its wording for clients and oracles that predate the sha1.
        want_sha1 = str(expected.get("rom_sha1") or "").lower()
        got_sha1 = str(msg.get("rom_sha1") or "").lower()
        if want_sha1 and got_sha1 and got_sha1 != want_sha1:
            return {"state": "rejected",
                    "reason": (f"this is not the ROM built for player {player_id} "
                               f"(tables match; sha1 {got_sha1[:12]}, expected {want_sha1[:12]})")}
        return {"state": "admitted", "reason": "cartridge matches the contract"}

    def is_admitted(self, player_id: str) -> bool:
        """A player with no verdict yet is `contract_pending`, not admitted.

        The default used to be "admitted" so that an uncontracted run behaved exactly as it
        always had -- correct for that case, and wrong for the contracted one. Nothing
        requires a hello before `_dispatch`: `handle_client` takes the player id from the
        message itself, so a client whose first line is a `capture` (a reconnect after a
        crash that lost the hello, or any client that reorders) found no record, took the
        default, and mutated rule state on a cartridge nobody had checked.

        The distinction is only meaningful when there IS something to check, so an
        uncontracted run keeps the old behaviour exactly.
        """
        state = (self.admission.get(player_id) or {}).get("state")
        if state is None:
            # getattr for the same reason adapter_for uses it: some tests build an
            # SLinkServer without running __init__.
            return not self._rom_contract
        return state == "admitted"

    def _player_has_panel(self, player_id: str) -> bool:
        """Does THIS player's cartridge have the native panel?

        Per player, not per adapter. On Gen 1 the panel comes from the companion ROM patch,
        so a patched and an unpatched cartridge can sit in the same run and the generation
        alone cannot answer. A client that can render it says so at hello; one that says
        nothing gets no panel payloads rather than a stream of unknown-command warnings.

        The adapter still has a veto: a generation with no panel at all never sends one,
        whatever a client claims.
        """
        if not self.adapter.supports_info_panel():
            return False
        reported = (self.connected_players.get(player_id) or {}).get("panel")
        if reported is None:
            # Generations whose clients predate the capability report keep working: they
            # were already receiving the panel on the adapter's say-so.
            return self.adapter.info_panel_width() == 0
        return bool(reported)

    def _bind_player_adapter(self, player_id: str, rom_type: str) -> None:
        """Give a player their own adapter when the run's adapter declares one.

        A run's adapter may declare that the cartridges it admits bind DIFFERENT
        per-player data: Gen 2's three titles share one pairing foundation but not one
        pack, so the adapter locked to whichever title said hello first cannot answer for
        a partner on another title. The declaration is ADAPTER data (`per_player_key` /
        `per_player_bound_key`, resolved with getattr) -- never a game_id, title or
        rom_type branch in this file. An adapter that declares neither -- every generation
        but Gen 2 today -- keeps the one run-level adapter for both players, exactly as
        before.

        Called on every accepted hello: a reconnect re-declares its cartridge,
        and a player whose key matches the run's again drops the adapter it had.
        """
        from server.adapters import game_id_for_rom_type, get_adapter
        key_for = getattr(self.state.adapter, "per_player_key", None)
        own_key = getattr(self.state.adapter, "per_player_bound_key", None)
        if not (rom_type and callable(key_for) and callable(own_key)):
            return
        player_key = key_for(rom_type)
        if player_key is None or player_key == own_key():
            self._player_adapters.pop(player_id, None)
            return
        # F5 (Codex cx-2985fe38): pass the run's COMMITTED artifact kind, not a bare
        # constructor default -- an overlay-committed run must build its partner's adapter
        # with overlay capabilities too, not the "clean" a bare get_adapter() call defaults
        # to. Called only after the artifact_kind commit point in `_dispatch`, so on the
        # very first hello that commits it, this already sees the just-committed value.
        self._player_adapters[player_id] = get_adapter(
            game_id_for_rom_type(rom_type), is_rr=rom_type.endswith("_rr"), rom_type=rom_type,
            artifact_kind=self.state.artifact_kind or "clean")
        log.info(f"[{player_id}] per-player adapter bound to {player_key!r} "
                 f"(run adapter is {own_key()!r})")

    def adapter_for(self, player_id: str):
        """The adapter that describes THIS player's cartridge.

        Content and title-sensitive acquisition sites may differ per player. Species,
        evolution, gender/type and shiny rules retain the run adapter: admitted titles
        and supported randomizer settings share those semantics.
        """
        # getattr, not a plain attribute read: some tests build an SLinkServer without
        # running __init__, and a lookup helper should answer for those rather than raise.
        # A class-level dict would be shared across instances, which is worse.
        return self._player_adapters.get(player_id) or self.adapter

    def _calc_profile(self) -> dict | None:
        """The damage-calc profile shared by both players' adapters, or None if either
        game has no verified calc numbers yet, or the two games' calc rules disagree."""
        from server.adapters import shared_calc_profile
        return shared_calc_profile(self.adapter_for(pid).calc_profile() for pid in ("a", "b"))

    def _trainer_panel_html(self, area_id: str, player_id: str = "",
                            key_prefix: str = "",
                            highest_party_level: int = 0) -> str:
        """Return collapsible Upcoming Trainers widget HTML for an area, or ''.

        Each area row expands to show the trainer's full curated party
        (species, level, ability, item, moves).

        ``key_prefix`` is prepended to the data-details-key/id so the same
        widget can be rendered in both the split AND combined views without
        DOM-id collisions (both views live in the DOM simultaneously and are
        toggled via body.lp-view). Pass "lp:" from combined-view callers.

        ``highest_party_level`` is the level of the player's strongest
        owned mon at the moment of rendering. The spreadsheet expresses
        many trainer mon levels as "Max Level - N" / "Highest Lv -N",
        which the game resolves at fight time as
        ``max(1, highest_party_level + level_offset)`` (offset <= 0). Pass
        0 to fall back to the static `level` value baked into the JSON.

        Mirrors `_encounter_html`'s structure exactly so idiomorph morph
        behaviour, `data-details-key` localStorage persistence, and the
        existing CSS variable palette all work without new infrastructure.
        """
        if not area_id:
            return ""
        # THIS PLAYER'S adapter, not the run-global one. Trainer parties and levels are an
        # ALLOWED randomization category, so on a randomized pair the two cartridges can
        # disagree about what a trainer fields -- and a widget built from whichever adapter
        # the first hello happened to install would show one player the other's game.
        # (Today only Gen 3 populates these, and Gen 1 returns [] so the widget renders
        # nothing at all. The global lookup was still the wrong source to read from.)
        adapter = self.adapter_for(player_id) if player_id else self.adapter
        try:
            trainer_ids = adapter.trainers_for_area(area_id)
        except Exception:
            trainer_ids = []
        if not trainer_ids:
            return ""

        # Resolve briefs.
        briefs: list[tuple[int, dict]] = []
        player_gender = getattr(self, "player_gender", {}).get(player_id)
        for rt_id in trainer_ids:
            brief = adapter.trainer_brief(rt_id)
            if brief:
                required = brief.get("required_player_gender")
                if (type(player_gender) is int and player_gender in (0, 1)
                        and type(required) is int and required in (0, 1) and required != player_gender):
                    continue
                briefs.append((rt_id, brief))

        # Bucket by (name, cap_bucket) — candidates for grouping. Then
        # decide row-vs-variant based on whether fight_labels are DISTINCT.
        # Distinct labels = true starter-variants ("If Rival Has X/Y/Z"
        # for one rival encounter) → render as ONE row with sub-blocks.
        # Identical or missing labels = separate encounters (Left/Right
        # Rocket Guards both labeled "Rocket Hide.") → render as
        # individual rows. Sequential same-name fights at different caps
        # land in different cap buckets and get progress-filtered.
        _CAP_BUCKET = 10
        groups: list[list[tuple[int, dict]]] = []
        candidates: dict[tuple[str, int], list[tuple[int, dict]]] = {}
        order: list[tuple[str, int]] = []
        for rt_id, brief in briefs:
            nm_key = brief.get("name", "").title() or f"#{rt_id}"
            cap = None
            fl = brief.get("fight_label") or ""
            if hasattr(adapter, "milestone_cap_for_fight_label"):
                cap = adapter.milestone_cap_for_fight_label(fl)
            if cap is None:
                cap = brief.get("level_cap") or 0
            cap_bucket = (cap or 0) // _CAP_BUCKET
            key = (nm_key, cap_bucket)
            if key not in candidates:
                candidates[key] = []
                order.append(key)
            candidates[key].append((rt_id, brief))
        # Promote each candidate bucket into one or more groups: a single
        # group when fight_labels are all distinct (true variants), one
        # group per entry otherwise.
        for key in order:
            bucket = candidates[key]
            labels = [b.get("fight_label") or "" for _, b in bucket]
            distinct_nonempty = (
                len(bucket) > 1
                and all(labels)
                and len(set(labels)) == len(labels)
            )
            if distinct_nonempty:
                groups.append(bucket)
            else:
                for entry in bucket:
                    groups.append([entry])

        # Sequential-fight filter: when the SAME trainer name has multiple
        # cap buckets at the same area (i.e. they're different fights at
        # different points in the story), hide groups not currently relevant
        # to the player's progress. For each name with >1 bucket:
        #   • Skip "past" buckets (cap + window < highest)
        #   • Keep the FIRST upcoming bucket only (lowest cap above highest)
        #   • If the player is past all buckets, keep the latest bucket only.
        def _bucket_cap(group_list: list[tuple[int, dict]]) -> int:
            # All entries in a bucket share approximately the same cap.
            _, b = group_list[0]
            fl = b.get("fight_label") or ""
            c = None
            if hasattr(adapter, "milestone_cap_for_fight_label"):
                c = adapter.milestone_cap_for_fight_label(fl)
            return c if c is not None else (b.get("level_cap") or 0)

        if highest_party_level > 0:
            # Re-bucket groups by trainer name so we can pick the most
            # progress-relevant fight per trainer. `groups` was flattened
            # above; we need name-level visibility for the filter.
            by_name: dict[str, list[list[tuple[int, dict]]]] = {}
            for g in groups:
                if not g:
                    continue
                nm = (g[0][1].get("name") or "").title() or "?"
                by_name.setdefault(nm, []).append(g)
            for _nm, buckets in by_name.items():
                if len(buckets) < 2:
                    continue
                ordered = sorted(buckets, key=_bucket_cap)
                # Sequential filtering only applies when the buckets cover
                # genuinely different progress milestones (e.g. Pre-Surge vs
                # Post-Surge Bugsy). When all buckets share roughly the
                # same cap (Left Guard + Right Guard at the same hideout),
                # they're simultaneous encounters in the same playthrough
                # and ALL should remain visible.
                caps = [_bucket_cap(g) for g in ordered]
                if (max(caps) - min(caps)) < _CAP_BUCKET:
                    continue
                keep = None
                for g in ordered:
                    if _bucket_cap(g) >= highest_party_level - _CAP_BUCKET:
                        keep = g
                        break
                if keep is None:
                    keep = ordered[-1]
                for g in ordered:
                    if g is not keep:
                        for entry in g:
                            entry[1]["_filtered_out"] = True

        # Helper: classify a fight against the player's current progress.
        # Returns ("past" | "current" | "future" | "unknown", css_class).
        # "Pre X" milestone cap → fight happens BEFORE that cap; "Post X" →
        # fight happens AFTER. A fight with cap=C against highest_party_level
        # h is:
        #   • past    when h is well past C (we've moved on to later content)
        #   • future  when h is far below C (we haven't earned the right to be
        #             there yet — adjacent fights show up in the panel anyway,
        #             but visually de-emphasised so the *current* one pops)
        #   • current otherwise (within +/-CURRENT_WINDOW of C)
        _CURRENT_WINDOW = 10
        def _classify_fight(fight_brief: dict) -> tuple[str, int | None]:
            fl = fight_brief.get("fight_label") or ""
            cap = adapter.milestone_cap_for_fight_label(fl) \
                  if hasattr(adapter, "milestone_cap_for_fight_label") else None
            if cap is None or highest_party_level <= 0:
                return ("unknown", cap)
            if highest_party_level >= cap + _CURRENT_WINDOW:
                return ("past", cap)
            if highest_party_level <= cap - _CURRENT_WINDOW:
                return ("future", cap)
            return ("current", cap)

        rows_html = ""
        for group in groups:
            variants = group  # list[(rt_id, brief)]
            # Skip groups the sequential-fight filter marked as not relevant
            # to the player's current progress (handled above).
            if any(b.get("_filtered_out") for _, b in variants):
                continue
            primary_id, primary_brief = variants[0]
            # The first variant's brief drives the summary; remaining variants
            # are surfaced as selectable tabs inside the expanded body.
            rt_id = primary_id
            brief = primary_brief
            cls_raw  = brief.get("class", "") or ""
            name_raw = brief.get("name", "") or f"Trainer #{rt_id}"
            # Collapse "Team Rocket Grunt" + name "Grunt" → just the class;
            # avoids "TEAM ROCKET GRUNT Grunt" duplication.
            if name_raw.strip().lower() in cls_raw.strip().lower().split():
                name_raw = ""
            cls   = html.escape(cls_raw)
            name  = html.escape(name_raw)
            party = brief.get("party") or []
            party_count = len(party)
            n_variants = len(variants)
            state, _ms_cap = _classify_fight(brief)
            def _render_party(mons: list[dict]) -> str:
                """Render a single variant's party as a stack of mon rows.

                Levels are resolved at render time: when a mon has a
                level_offset (relative "Max Level - N" entry from the
                spreadsheet) AND the caller passed a non-zero
                highest_party_level, we recompute the level dynamically.
                Otherwise the static fallback baked into the JSON is used.
                A small "rel" hint appears next to dynamically-computed
                levels so the user knows the number reflects their team.
                """
                out = ""
                for mon in mons:
                    species = html.escape(str(mon.get("species") or "?"))
                    level    = mon.get("level") or 0
                    offset   = mon.get("level_offset")
                    dynamic  = False
                    if (isinstance(offset, int) and highest_party_level > 0):
                        level = max(1, highest_party_level + offset)
                        dynamic = True
                    ability = html.escape(str(mon.get("ability") or ""))
                    item    = html.escape(str(mon.get("item") or ""))
                    nature  = html.escape(str(mon.get("nature") or ""))
                    moves   = mon.get("moves") or []
                    moves_html = ""
                    if moves:
                        move_chips = "".join(
                            f'<span class="tr-move">{html.escape(str(m))}</span>'
                            for m in moves if m and m != "-"
                        )
                        moves_html = f'<div class="tr-moves">{move_chips}</div>'
                    if isinstance(level, int) and level > 0:
                        lv_text = f"Lv{level}"
                        if dynamic:
                            # Show the rule that produced this number so the
                            # player understands why it scales with their team.
                            if offset == 0:
                                lv_text += ' <span class="tr-lv-rule">(Max Lv)</span>'
                            else:
                                lv_text += (f' <span class="tr-lv-rule">'
                                            f'(Max Lv {offset:+d})</span>')
                    else:
                        lv_text = "Lv?"
                    meta_bits = []
                    # Column-aligned table of optional meta chips — kept on one line each.
                    if ability: meta_bits.append(f'<span class="tr-ability">{ability}</span>')   # noqa: E701
                    if item:    meta_bits.append(f'<span class="tr-item">@ {item}</span>')       # noqa: E701
                    if nature:  meta_bits.append(f'<span class="tr-nature dim">{nature}</span>') # noqa: E701
                    meta_html = (' · '.join(meta_bits)) if meta_bits else ''
                    # Built outside the f-string: a backslash inside an f-string expression
                    # is Python 3.12+ syntax and would not parse on 3.11.
                    meta_row = f'<div class="tr-mon-meta">{meta_html}</div>' if meta_html else ''
                    out += (
                        f'<div class="tr-party-row">'
                        f'<div class="tr-mon-hdr">'
                        f'<span class="tr-species">{species}</span>'
                        f'<span class="tr-lv">{lv_text}</span>'
                        f'</div>'
                        f'{meta_row}'
                        f'{moves_html}'
                        f'</div>'
                    )
                return out

            if n_variants == 1:
                mons_html = _render_party(party)
            else:
                # Render each variant in a sub-block. Header text comes from
                # the variant's own fight_label ("If Rival Has Squirtle",
                # "Pre Lt. Surge", "Team Two", …) when present — this is the
                # spreadsheet author's stated trigger condition. Falls back
                # to "Variant N" when no label is available. Each variant
                # also gets its own Calc button so the user can load the
                # specific matchup into the Prep tab.
                blocks = []
                for vi, (v_rt_id, v_brief) in enumerate(variants, start=1):
                    v_party = v_brief.get("party") or []
                    lead_species = v_party[0].get("species", "?") if v_party else "?"
                    v_label = v_brief.get("fight_label") or f"Variant {vi}"
                    v_calc_label = v_brief.get("calc_label", "") or ""
                    blocks.append(
                        f'<div class="tr-variant">'
                        f'<div class="tr-variant-hdr">'
                        f'<span class="tr-variant-label">{html.escape(v_label)}</span>'
                        f'<span class="tr-variant-lead dim">lead: '
                        f'{html.escape(str(lead_species))}</span>'
                        f'<button type="button" class="tr-calc-btn tr-variant-calc" '
                        f'data-pid="{html.escape(player_id, quote=True)}" '
                        f'data-tid="{v_rt_id}" '
                        f'data-calc-label="{html.escape(v_calc_label, quote=True)}" '
                        f'title="Open this variant in the Calc Prep tab">'
                        f'&#9876;</button>'
                        f'</div>'
                        f'{_render_party(v_party)}'
                        f'</div>'
                    )
                mons_html = '<div class="tr-variants">' + ''.join(blocks) + '</div>'
            pid_seg = f"{player_id}:" if player_id else ""
            row_key = f"{key_prefix}tr:{pid_seg}{html.escape(area_id, quote=True)}:{rt_id}"
            # Summary metadata: party size + level cap + fight condition +
            # variant count. The fight_label is the "WHEN you fight him"
            # condition pulled from the xlsx ("If Lv ≥ 27", "If Lv ≥ 44", …).
            meta_chunks = [f'<span class="tr-size dim">· {party_count} mons</span>']
            lc = brief.get("level_cap")
            if isinstance(lc, int) and lc > 0:
                meta_chunks.append(
                    f'<span class="tr-cap dim">· Lv≤{lc}</span>')
            fight_label = brief.get("fight_label") or ""
            if fight_label:
                meta_chunks.append(
                    f'<span class="tr-fight-label">· '
                    f'{html.escape(fight_label)}</span>')
            if n_variants > 1:
                meta_chunks.append(
                    f'<span class="tr-variant-count">{n_variants} variants</span>')
            meta_html = "".join(meta_chunks)
            sprite_url = brief.get("sprite_url") or ""
            sprite_html = ""
            if sprite_url:
                sprite_html = (
                    f'<img class="tr-sprite" loading="lazy" '
                    f'src="{html.escape(sprite_url, quote=True)}" '
                    f'alt="" referrerpolicy="no-referrer" '
                    f'onerror="this.style.display=\'none\'">'
                )
            # The widget header already declares these are "Upcoming
            # Trainers"; a per-row "upcoming"/"current"/"past" chip would be
            # redundant noise. The progress state class still drives subtle
            # styling (faded past fights, accented border for current) and
            # the sequential-fight filter above hides genuinely-past variants
            # so only the relevant ones reach this loop.
            rows_html += (
                f'<details class="trainer-row tr-state-{state}" id="d-{row_key}" '
                f'data-details-key="{row_key}">'
                f'<summary>'
                f'{sprite_html}'
                f'<span class="tr-class">{cls}</span> '
                f'<span class="tr-name">{name}</span>'
                f'{meta_html}'
                f'<button type="button" class="tr-calc-btn" '
                f'data-pid="{html.escape(player_id, quote=True)}" '
                f'data-tid="{rt_id}" '
                f'data-calc-label="{html.escape(brief.get("calc_label", "") or "", quote=True)}" '
                f'title="Open in the Calc Prep tab (reuses calc tab if open)">&#9876;</button>'
                f'</summary>'
                f'<div class="tr-party">{mons_html}</div>'
                f'</details>'
            )
        if not rows_html:
            return ""
        pid_seg = f"{player_id}:" if player_id else ""
        key = f"{key_prefix}tr:{pid_seg}{html.escape(area_id, quote=True)}"
        return (
            f'<details class="trainer-widget" id="d-{key}" data-details-key="{key}">'
            f'<summary><svg class="inline-ico" aria-hidden="true">'
            f'<use href="#i-swords"/></svg> Upcoming Key Trainers</summary>'
            f'<div class="trainer-list">{rows_html}</div>'
            f'</details>'
        )

    def _calc_preview(self, pid: str, enemy_party: list[dict]) -> dict | None:
        """What calc-preview.js needs to run the damage calculator for this player's
        current matchup, or None when this player's game has no verified calc numbers
        yet (calc_profile() is None)."""
        adapter = self.adapter_for(pid)
        profile = adapter.calc_profile()
        if not profile:
            return None
        details = self.party_details.get(pid, {})
        atk_key = next((k for k, d in details.items() if d.get("active") and d.get("hp", 0) > 0),
                       next((k for k, d in details.items() if d.get("hp", 0) > 0), None))
        dfn = next((em for em in enemy_party if em.get("active")),
                   next((em for em in enemy_party if em.get("hp", 0) > 0), None))
        # The same names /api/calc/mons hands the full calc: the engine looks moves,
        # abilities and items up by name, never by the cartridge's numeric id.
        atk = _build_mon_entry(atk_key, details[atk_key], adapter) if atk_key else None
        if not atk or not dfn:
            return None
        bs = self.battle_state.get(pid, {})
        is_trainer = bool(bs.get("is_trainer_battle") and bs.get("opponent_class") and bs.get("opponent_name"))

        def status(mon):
            # The adapter decodes its own generation's bitfield; SLP/PSN/... lowercase is
            # the engine's StatusName.
            return adapter.status_token(int(mon.get("status_cond", 0) or 0)).lower()

        def boosts(stages):
            # Raw battle stages (6 = neutral) in ATK, DEF, SPE, SPA, SPD order, as the full
            # bridge reads them (slink_bridge.js _applyBattleState).
            if not isinstance(stages, list):
                return {}
            out = {}
            for stat, raw in zip(("atk", "def", "spe", "spa", "spd"), stages, strict=False):
                if isinstance(raw, int) and 0 <= raw <= 12 and raw != 6:
                    out[stat] = raw - 6
            return out

        # The enemy exactly as /api/calc/mons builds it (_foe_detail): calc spelling, held
        # item, ability and calc_stats. Nature stays None -- no client sends an enemy's
        # personality, so neither calc path can know it.
        foe = _build_mon_entry("foe", _foe_detail(dfn, adapter), adapter)
        if not foe:
            return None
        return {
            "gen": profile.get("gen"),
            "dex": profile.get("dex"),
            "trainer_key": f"{bs['opponent_class']} {bs['opponent_name']}" if is_trainer else "",
            "is_trainer": is_trainer,
            "is_doubles": bool(bs.get("is_doubles")),
            "player_species": atk["species_name"],
            "player_level": atk["level"],
            "player_nature": atk["nature"],
            "player_ability": atk["ability_name"],
            "player_item": atk["item_name"],
            "player_moves": atk["moves"],
            "player_hp_pct": atk["hp_pct"],
            "player_status": status(atk),
            "player_boosts": boosts(atk["stat_stages"]),
            "player_calc_stats": atk["calc_stats"],
            "enemy_species": foe["species_name"],
            "enemy_level": foe["level"],
            "enemy_nature": foe["nature"],
            "enemy_ability": foe["ability_name"],
            "enemy_item": foe["item_name"],
            "enemy_hp_pct": (max(0, min(100, int(dfn.get("hp", 0) / dfn["maxHP"] * 100)))
                              if dfn.get("maxHP") else None),
            "enemy_status": status(dfn),
            "enemy_boosts": boosts(dfn.get("stat_stages")),
            "enemy_calc_stats": foe["calc_stats"],
        }

    def _enc_table_for_status(self, area_id: str, player_id: str = "") -> dict | None:
        """The encounter table for the payload, or None when no encounter data exists
        (non-RR or unmapped area).

        ``player_id`` selects whose cartridge to describe; without it the run-global
        adapter answers, which is right only while both players hold the same content.
        """
        return self.adapter_for(player_id).encounter_table(area_id) or None

    # rom_type → game_id and rom_type → variant-label maps live in
    # server.adapters (single source of truth alongside the registry).
    # Access via game_id_for_rom_type() and variant_label() helpers.

    def _page_title(self) -> str:
        """Build dynamic page title: Pokémon Soul Link Tracker — <variant> — <run name>.

        Game variant is committed once on first hello and persisted in links.json,
        so it survives server restarts and client disconnects.
        """
        parts = ["Pokémon Soul Link Tracker"]
        if self.state.rom_type:
            from server.adapters import variant_label
            parts.append(variant_label(self.state.rom_type))
        if self._run_name:
            parts.append(html.escape(self._run_name))
        return " — ".join(parts)

    def _rail_ctx(self, *, page: str = "run", panel: str = "", tab: str = "") -> dict:
        """What _rail.html needs from a standalone run server: this one run, its own pages."""
        return {
            "standalone": True, "base": "", "tcp_port": self._tcp_port,
            "runs": [self._run_entry()], "run": self._run_entry(),
            "page": page, "panel": panel, "tab": tab, "pinned_run_id": None,
            "show_calc": bool(self._calc_profile()),
        }

    def _rom_label(self) -> str:
        from server.adapters import variant_label
        return variant_label(self.state.rom_type) if self.state.rom_type else ""

    def _run_entry(self) -> dict:
        return {"run_id": self._run_id or "run", "name": self._run_name or self._run_id or "SLink",
                "status": "running", "game_label": self._rom_label()}

    def _build_sidebar_html(self, request, active: str, **rail) -> str:
        """The rail, rendered to a string for the pages that carry it as `sidebar_html`."""
        env = aiohttp_jinja2.get_env(request.app)
        return env.get_template("_rail.html").render(self._rail_ctx(page=active, **rail))

    def _panel_ctx(self, request, *, panel: str, label: str, title: str | None = None,
                   meta: str | None = None, tabs=None) -> dict:
        """panel_page.html on a standalone run server."""
        run = self._run_entry()
        if tabs is None:
            tabs = [("Board", "/", False)]
            if self._calc_profile():
                tabs.append(("Calc", "/calc/normal.html", panel == "calc"))
            tabs.append(("Debug", "/debug", panel == "debug"))
            tabs.append(("Timeline", "/timeline", panel == "timeline"))
        run_panel = panel in ("calc", "debug", "timeline")
        ctx = self._rail_ctx(page="run" if run_panel else "broadcast",
                             panel=panel if run_panel else "",
                             tab=panel if panel in ("twitch", "obs") else "")
        ctx.update({
            "page_title": self._page_title(), "theme": resolve_theme(request),
            "is_stream": False, "hide_chrome": False,
            "body_class": "board mgr" + (" dark-theme calc-host" if panel == "calc" else ""),
            "title": title or run["name"], "meta": meta or label, "tabs": tabs,
            "panel": panel, "panel_label": label, "api_base": "",
        })
        return ctx

    def _notify_sse(self):
        """Push an update notification to all connected SSE clients.

        Uses coalescing: each queue holds at most 1 item.  If the queue is
        full (client hasn't consumed the previous update yet), the old item
        is replaced with a fresh sentinel so the client always gets the
        latest state when it reads.
        """
        if not self._sse_clients:
            return
        for q in self._sse_clients:
            # Drain any unconsumed item, then put the new sentinel.
            with contextlib.suppress(asyncio.QueueEmpty):
                q.get_nowait()
            # QueueFull should not happen after the drain, but be safe.
            with contextlib.suppress(asyncio.QueueFull):
                q.put_nowait(True)

    # ── Rolling backups ───────────────────────────────────────────────────────

    def _both_connected(self) -> bool:
        return (self.connected_players.get("a", {}).get("connected", False)
                and self.connected_players.get("b", {}).get("connected", False))

    def start_backup_task(self):
        """Spawn the rolling-backup task. Idempotent if already running."""
        if self._backup_task is None or self._backup_task.done():
            from server.backup import backup_loop
            self._backup_task = asyncio.ensure_future(backup_loop(
                links_path=self.state._links_path,
                events_path=self._events_path,
                max_slots=self._backup_max,
                interval_s=self._backup_interval,
                is_active=self._both_connected,
            ))

    async def handle_sse(self, request):
        """GET /api/events — Server-Sent Events stream.

        Emits two named event types:
          - ``event: status``  — full JSON status dict (for stream overlays)
          - ``event: ping``    — empty data (triggers fetch+morph on main page)

        Clients that only need the ping can ignore ``status`` events and
        vice-versa, keeping the architecture flexible with a single endpoint.
        """
        resp = aiohttp_web.StreamResponse()
        resp.content_type = "text/event-stream"
        resp.headers["Cache-Control"] = "no-cache"
        resp.headers["X-Accel-Buffering"] = "no"  # disable nginx buffering
        resp.force_close()  # disable HTTP keep-alive; SSE connections must not be reused
        await resp.prepare(request)

        async def _write(data: bytes) -> bool:
            """Write with a 10-second timeout. Returns False on failure/timeout."""
            try:
                await asyncio.wait_for(resp.write(data), timeout=10.0)
                return True
            except (TimeoutError, ConnectionResetError, ConnectionAbortedError, asyncio.CancelledError):
                return False
            except Exception as e:
                log.debug(f"SSE write error: {e}")
                return False

        q: asyncio.Queue = asyncio.Queue(maxsize=1)
        self._sse_clients.add(q)
        try:
            # Send retry hint and an immediate ping so clients fetch the initial state
            if not await _write(b"retry: 3000\n\n"):
                return resp
            if not await _write(b"event: ping\ndata: \n\n"):
                return resp

            while True:
                # Short heartbeat interval (3s instead of the previous 15s) so
                # dead client connections are detected and cleaned up quickly.
                # The earlier 15s window caused F5-spam to stack up to 6
                # zombie SSE connections inside Chrome's per-origin pool,
                # which then blocked any new fetches on the dashboard until
                # the heartbeats finally noticed the disconnects.
                try:
                    await asyncio.wait_for(q.get(), timeout=3.0)
                except TimeoutError:
                    # Proactive disconnect detection: if the transport has
                    # already closed, exit immediately without attempting a
                    # write. Saves up to one heartbeat cycle of latency.
                    if request.transport is None or request.transport.is_closing():
                        break
                    if not await _write(b": heartbeat\n\n"):
                        break
                    continue
                except asyncio.CancelledError:
                    break

                # Real update — send a tiny ping; clients fetch /api/status themselves
                if not await _write(b"event: ping\ndata: \n\n"):
                    break
        except Exception as e:
            log.debug(f"SSE handler exited: {e}")
        finally:
            self._sse_clients.discard(q)
        return resp

    def _disconnect_clients(self):
        """Make state replacement visible to clients through their reconnect/hello path."""
        for writer in tuple(self._client_connections):
            writer.close()

    async def handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        peer = writer.get_extra_info("peername")
        log.info(f"Client connected: {peer}")
        if self._wire_log:
            writer = _WireTap(writer, self._wire_log, self._wire_files)
        self._client_connections.add(writer)
        # Per-CONNECTION session state. The seq counter used to be per-slot
        # (`self._last_seq`), which outlived the socket it described — see the guards below.
        last_seq = -1
        hello_players = set()
        conn_gen: dict[str, int] = {}   # player -> the generation this connection's hello was given
        bound_player: str | None = None
        no_hello_warned = False
        dropping_oversized_line = False
        try:
            while not writer.is_closing():
                try:
                    raw = await reader.readuntil(b"\n")
                except asyncio.IncompleteReadError:
                    break  # peer closed cleanly
                except asyncio.LimitOverrunError as e:
                    if not dropping_oversized_line:
                        log.warning("Oversized line from %s (%s bytes before limit) - dropping", peer, e.consumed)
                    # Leave the newline and any later frames for readuntil.
                    await reader.readexactly(e.consumed)
                    dropping_oversized_line = True
                    continue
                if dropping_oversized_line:
                    dropping_oversized_line = False
                    continue
                # A reset may close this socket while readuntil is suspended. Buffered
                # events (including hello) must not enter the replacement run.
                if writer.is_closing():
                    break
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError as e:
                    log.warning(f"Bad JSON from {peer}: {e}")
                    if self._wire_log:
                        writer.c2s_raw(line)
                    await self._respond(writer, [{"cmd": "noop"}])
                    continue
                if self._wire_log:
                    writer.c2s(line, msg)
                if not isinstance(msg, dict) or not isinstance(msg.get("player", ""), str):
                    await self._respond(writer, [{"cmd": "noop", "refused": "malformed"}])
                    continue
                if "seq" in msg and type(msg["seq"]) is not int:
                    log.warning("Rejected non-integer seq from %s", peer)
                    await self._respond(writer, [{"cmd": "noop", "refused": "malformed"}])
                    continue

                if self._instrumentation_keys and msg.get("event") == "hello":
                    supplied = msg.get("instrumentation_key")
                    matched = next((
                        player for player, expected in self._instrumentation_keys.items()
                        if isinstance(supplied, str) and hmac.compare_digest(supplied, expected)
                    ), None)
                    if matched is None:
                        log.warning("Rejected hello with missing or unknown instrumentation key from %s", peer)
                        await self._respond(
                            writer, [{"cmd": "noop", "refused": "instrumentation_key"}])
                        continue
                    if bound_player is not None and bound_player != matched:
                        log.warning("Rejected instrumentation slot change on one connection from %s", peer)
                        await self._respond(
                            writer, [{"cmd": "noop", "refused": "instrumentation_slot"}])
                        continue
                    if msg.get("player") != matched:
                        log.warning("Rejected hello whose player does not match its instrumentation key from %s", peer)
                        await self._respond(
                            writer, [{"cmd": "noop", "refused": "instrumentation_player"}])
                        continue
                    bound_player = matched
                elif self._instrumentation_keys and bound_player is not None:
                    msg["player"] = bound_player

                player_id = msg.get("player", "")
                if player_id not in VALID_PLAYERS:
                    log.warning(f"Rejected unknown player_id: {repr(player_id)} from {peer}")
                    await self._respond(writer, [{"cmd": "noop"}])
                    continue

                # Hello-first. A connection has proved nothing until it has said hello, so
                # nothing else on it is listened to. Without this a cartridge whose hello was
                # lost still had its ticks reconciled into whichever slot it named, and a
                # wrong save's party discarded the run's linked keys (reconnect_new,
                # 2026-09-17). The per-slot identity gate in _dispatch is the second line.
                if msg.get("event") != "hello" and player_id not in hello_players:
                    if not no_hello_warned:
                        no_hello_warned = True
                        log.warning(f"[{player_id}] {msg.get('event', '?')!r} before hello from "
                                    f"{peer} — dropping this connection's events until it says hello")
                    await self._respond(writer, [{"cmd": "noop", "refused": "no_hello"}])
                    continue

                if (msg.get("event") != "hello"
                        and conn_gen.get(player_id) != self._conn_gen.get(player_id)):
                    await self._respond(writer, [{"cmd": "noop", "refused": "superseded"}])
                    continue

                prev_conn = dict(self.connected_players.get(player_id, {}))
                prev_error = self.state.identity_error.get(player_id)
                prev_admission = dict(self.admission.get(player_id, {}))
                prev_player_adapter = self._player_adapters.get(player_id)
                prev_rom_rejected = player_id in self._rom_type_rejected
                other_active = bool(self._client_writers.get(player_id, set()) - {writer})

                def restore_active_connection(player_id=player_id, prev_conn=prev_conn,
                                              prev_error=prev_error, prev_admission=prev_admission,
                                              prev_player_adapter=prev_player_adapter,
                                              prev_rom_rejected=prev_rom_rejected,
                                              other_active=other_active):
                    if not other_active:
                        return
                    self.connected_players[player_id] = prev_conn
                    self.admission[player_id] = prev_admission
                    if prev_player_adapter is None:
                        self._player_adapters.pop(player_id, None)
                    else:
                        self._player_adapters[player_id] = prev_player_adapter
                    if prev_error is None:
                        self.state.identity_error.pop(player_id, None)
                    else:
                        self.state.identity_error[player_id] = prev_error
                    if prev_rom_rejected:
                        self._rom_type_rejected.add(player_id)
                    else:
                        self._rom_type_rejected.discard(player_id)

                # Update connection info
                self._presence_writers.setdefault(player_id, set()).add(writer)
                self.connected_players[player_id] = {
                    "connected":  True,
                    "last_event": msg.get("event", "?"),
                    "last_seen":  datetime.now().strftime("%H:%M:%S"),
                    # `connected` only clears in the reader's finally block, so a crashed emulator
                    # or a slept laptop leaves the badge green forever. An age does not lie.
                    "last_seen_ts": time.time(),
                    "rom_type":   prev_conn.get("rom_type", "?"),
                }
                # CARRY THE CARTRIDGE FACTS FORWARD. This dict is rebuilt from scratch on
                # EVERY inbound message, but `panel`/`panel_abi` are written only on hello
                # -- so the first tick after connecting erased them, `_player_has_panel`
                # fell back to the adapter default, and Gen 1 (width 20, not 0) answered
                # False. The panel payload therefore went out exactly once, at hello, and
                # the cartridge showed the run frozen at connect time for the rest of the
                # session. Gen 3 never noticed: it leaves info_panel_width at 0, so its
                # fallback answered True.
                for carried in ("panel", "panel_abi", "sfx"):
                    if carried in prev_conn:
                        self.connected_players[player_id][carried] = prev_conn[carried]
                if msg.get("event") == "hello":
                    hello_players.discard(player_id)
                    # A rom_type the router does not know used to leave the server on
                    # whichever adapter it already had -- silently -- and the run carried on
                    # under the wrong generation (Gen 3 genders and abilities on Red/Blue
                    # mons; adapters/__init__.py records the same for Gen 2). Refuse the
                    # hello the way an identity mismatch is refused: the client sees a HUD
                    # line, the dashboard sees identity_error, and nothing else gets through
                    # until a hello with a rom_type we route.
                    # A MISSING rom_type is refused for the same reason (Codex cx-a66ab55a
                    # F1): the run's foundation lock is DERIVED from it, so a hello with no
                    # rom_type used to skip the lock entirely and be dispatched into the
                    # committed run. Every shipped client sends one; a hello without one is
                    # not an old client, it is a malformed one.
                    from server.adapters import game_id_for_rom_type, unrouted_rom_type_reason
                    _rt = msg.get("rom_type", "")
                    if not isinstance(_rt, str) or not _rt or not game_id_for_rom_type(_rt):
                        err = (f"Unknown rom_type {_rt!r} for slot {player_id.upper()}: "
                               f"{unrouted_rom_type_reason(_rt)}")
                        log.warning(f"[{player_id}] REJECTED: {err}")
                        self.state.identity_error[player_id] = err
                        self._rom_type_rejected.add(player_id)
                        self._record_hello_refusal(player_id, err, msg)
                        restore_active_connection()
                        await self._respond(writer, [{
                            "cmd": "hud_show", "text": f"[x] UNKNOWN ROM: {_rt}",
                            "r": 255, "g": 0, "b": 0, "frames": 600,
                        }])
                        self._notify_sse()
                        continue
                    # A recognised hello for ANOTHER foundation, or another artifact kind of
                    # the same foundation, is refused rather than ignored (PLAN A3 / §4 row
                    # 31): the two cartridges would share one rules run with different
                    # species, type and area domains -- or, for mixed kinds, a run-level
                    # native capability that only one side has.
                    #
                    # THIS RUNS FIRST (docs/gen3/PLAN.md §5.1). It used to run last, after the
                    # per-cartridge capability updates below and after the adapter reselection:
                    # a hello that was about to be refused had already rewritten the run's
                    # capabilities and, on an uncommitted run, swapped the adapter under it.
                    # A refused hello must leave the run exactly as it found it, so the only
                    # thing that precedes this check is the check that the rom_type routes.
                    # `foundation` absent ⇒ derived; PRESENT ⇒ it must agree, and `null`/""/0
                    # are present (Codex cx-a66ab55a F2), so the key's presence is what is
                    # passed through, never a falsey-coerced value.
                    _mixed = self._mixed_games_error(
                        player_id, _rt, msg.get("artifact_kind", "clean"),
                        msg.get("foundation", _FOUNDATION_ABSENT), msg.get("rom_content"))
                    if _mixed:
                        log.warning(f"[{player_id}] REJECTED: {_mixed}")
                        self.state.identity_error[player_id] = _mixed
                        self._rom_type_rejected.add(player_id)
                        self._record_hello_refusal(player_id, _mixed, msg)
                        restore_active_connection()
                        await self._respond(writer, [{
                            "cmd": "hud_show", "text": "[x] MIXED GAMES",
                            "color": [255, 0, 0], "duration": 600,
                        }])
                        self._notify_sse()
                        continue
                    # A hello that routes and pairs still has not been ADMITTED (ROM contract)
                    # or ACCEPTED (save identity) — both are decided inside `_dispatch`, on the
                    # committed run state, not on a candidate ROM report. Only the routing-level
                    # rejection marker is ours to clear here (Codex cx-2985fe38 F2): the
                    # rom_type/panel/panel_abi/sfx facts and the adapter are STAGED by
                    # `_dispatch`'s "hello" branch and committed only on acceptance, so a
                    # contract- or identity-rejected hello leaves connected_players and the
                    # adapter exactly as they were.
                    # Patch-first (owner 2026-10-02): a title that requires the SLink companion
                    # patch is refused when the hello shows none -- the cartridge bypassed the
                    # Manager. Same refusal shape as the checks above; the adapter owns the rule.
                    _needs = _companion_refusal(player_id, msg)
                    if _needs:
                        log.warning(f"[{player_id}] REJECTED: {_needs}")
                        self.state.identity_error[player_id] = _needs
                        self._rom_type_rejected.add(player_id)
                        self._record_hello_refusal(player_id, _needs, msg)
                        restore_active_connection()
                        await self._respond(writer, [{
                            "cmd": "hud_show", "text": "[x] NEEDS COMPANION PATCH",
                            "color": [255, 0, 0], "duration": 600,
                        }])
                        self._notify_sse()
                        continue
                    if player_id in self._rom_type_rejected:
                        # The identity gate in state.py only clears its own errors; this one
                        # is ours to clear, and only a routable hello gets this far.
                        self._rom_type_rejected.discard(player_id)
                        self.state.identity_error.pop(player_id, None)
                # Duplicate-event guard, per CONNECTION: a seq counter belongs to a socket,
                # and every client sends hello on (re)connect and counts up from there. So a
                # seq is only ever a duplicate of one seen on THIS connection — comparing
                # across connections dropped the first message of the next one.
                seq = msg.get("seq", -1)
                if seq != -1:
                    if seq <= last_seq:
                        log.debug(f"[{player_id}] duplicate seq {seq}, skipping")
                        await self._respond(writer, [{"cmd": "noop", "refused": "duplicate"}])
                        continue
                    log.debug(f"[TCP] player={player_id}  seq={seq}  last={last_seq}  outcome=accepted  event={msg.get('event','?')}")
                    last_seq = seq
                commands = self._dispatch(player_id, msg)
                if msg.get("event") == "hello":
                    if (msg.get("_rejected") or not self.is_admitted(player_id)
                            or any(c.get("refused") for c in commands)):
                        restore_active_connection()
                    else:
                        hello_players.add(player_id)
                        self._conn_gen[player_id] = conn_gen[player_id] = self._conn_gen.get(player_id, 0) + 1
                        self._client_writers.setdefault(player_id, set()).add(writer)
                await self._respond(writer, commands)
                # Log non-trivial responses at DEBUG for post-mortem tracing.
                _real_cmds = [c for c in commands if c.get("cmd") not in ("noop", "resolved_areas")]
                if _real_cmds:
                    _summary = ", ".join(
                        c["cmd"] + (":" + c["key"][:8] if "key" in c else "")
                        for c in _real_cmds
                    )
                    log.debug(f"[CMD FLUSH] player={player_id}  {len(_real_cmds)} cmd(s): {_summary}")
                # Notify SSE clients after TCP response (no game-client latency impact).
                # Not for ghost_pos: the RR client sends it 20-30x a second and it changes
                # nothing a page draws, but every ping makes the calc refetch full status.
                if msg.get("event") != "ghost_pos":
                    self._notify_sse()

        except (asyncio.IncompleteReadError, ConnectionResetError):
            pass
        finally:
            log.info(f"Client disconnected: {peer}")
            self._client_connections.discard(writer)
            for player_id, writers in list(self._client_writers.items()):
                if writer not in writers:
                    continue
                writers.discard(writer)
                if not writers:
                    self._client_writers.pop(player_id)
            for player_id, writers in list(self._presence_writers.items()):
                if writer not in writers:
                    continue
                writers.discard(writer)
                if not writers:
                    self._presence_writers.pop(player_id)
                    info = self.connected_players.get(player_id)
                    if info is not None:
                        info["connected"] = False
                        self._notify_sse()
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    def _record_hello_refusal(self, player_id: str, reason: str, msg: dict) -> None:
        """A hello refused before admission (unknown rom_type, mixed games or kinds) is an
        admission verdict like any other: /api/status and the board show it, and the event
        journal keeps a durable "REJECTED —" line (ADMISSION-MIXED-KINDS-VERDICT)."""
        self.admission[player_id] = {"state": "rejected", "reason": reason}
        self._log_event(player_id, "hello", f"REJECTED — {reason}",
                        msg.get("loc_name", "") or msg.get("area_id", ""))

    @staticmethod
    async def _respond(writer: asyncio.StreamWriter, commands: list):
        data = json.dumps({"commands": commands}) + "\n"
        writer.write(data.encode("utf-8"))
        await writer.drain()

    # Mirrors BAR_W in patch/src/handlers.c: the info panel's HP bar is 38 px of track. The scaling
    # is done here because the ROM blob has no libgcc and therefore cannot divide at runtime.
    INFO_BAR_W = 38

    def _build_link_panel(self, player_id: str) -> dict:
        """Rows for the native in-game SOULLINK panel, for ONE player.

        Built per recipient because the panel is framed as "yours" and "your partner's" — the same
        run reads differently from each side, and telling the halves of a pair apart is the entire
        point of the screen.

        Rows are emitted PRE-FORMATTED and flat, `field|field|...`, because the Lua client has no
        JSON decoder — `parse_command_list` is a pattern scraper, so a nested payload simply never
        reaches it. Flat strings of a shape it can scrape keep the formatting here, in Python, where
        it is unit-testable.

        Field order matches MB.info_mon: label|name|level|hp|barpx|state. A row with an EMPTY label
        continues the pair above it (that is what draws the tie bracket). Two fields = label/value,
        one = full-width text. Pairs occupy 2 rows each and the client pages 6 rows at a time, so a
        pair can never straddle a page boundary.
        """
        from server.state import AreaStatus, LinkStatus
        partner = "b" if player_id == "a" else "a"
        s = self.state

        def half(mon, owner, label):
            det = self.party_details.get(owner, {}).get(mon.key) or {}
            cached = self._mon_cache.get(mon.key, {})
            species = det.get("species_id") or cached.get("species_id") or mon.species
            name = (det.get("nickname") or cached.get("nickname")
                    or (self.adapter.species_name(species) if species else "")
                    or mon.nickname or "?")
            level = int(det.get("level") or cached.get("level") or mon.level or 0)
            if not det:
                # Boxed: alive but out of the party, so there is no live HP. It gets its own state
                # rather than an empty bar, which the panel would colour like a dead mon.
                return f"{label}|{name[:10]}|{level}|BOX|0|B|"
            hp, mx = int(det.get("hp", 0) or 0), int(det.get("maxHP", 0) or 0)
            if hp <= 0:
                return f"{label}|{name[:10]}|{level}|FNT|0||"
            px = max(1, min(self.INFO_BAR_W, round(hp * self.INFO_BAR_W / mx))) if mx > 0 else 0
            # 7th field: status. A poisoned or paralysed linked mon is exactly the kind of thing a
            # player opens this screen to find out, and it is invisible from the HP bar alone.
            tok = self.adapter.status_token(int(det.get("status_cond", 0) or 0))
            return f"{label}|{name[:10]}|{level}|{hp}/{mx}|{px}||{tok}"

        rows = []
        npairs = 0
        for e in s.links:
            if not (e.a and e.a.key and e.b and e.b.key):
                continue                      # half-formed: one side hasn't caught yet
            mine_mon   = e.a if player_id == "a" else e.b
            theirs_mon = e.b if player_id == "a" else e.a
            rows.append(half(mine_mon, player_id, _area_tag(self.adapter.area_display_name(e.area_id))))
            rows.append(half(theirs_mon, partner, ""))   # empty label = continues the pair above
            npairs += 1

        if not rows:
            rows.append("No linked pairs yet")
        dead_zones = sorted(a for a, st in s.area_states.items() if st == AreaStatus.DEAD_ZONE)
        alive = sum(1 for e in s.links if e.status == LinkStatus.ALIVE)
        rows.append(f"Pairs alive|{alive}/{npairs}")
        rows.append(f"Dead zones|{len(dead_zones)}")
        # popcount the BITMASK hello/tick deliver (SLinkServer.player_badges). SoulLinkState's
        # player_badges was a count only the deleted old Gen 3 client's `status` event set, so
        # the row read 0/8 on every live client (UI-lane old-client survey, 2026-09-25).
        try:
            badges = bin(int(self.player_badges.get(player_id, 0) or 0)).count("1")
        except (TypeError, ValueError):
            badges = 0
        rows.append(f"Badges|{badges}/8")
        # NAME the dead zones. A count tells a player a number; the names tell them where they can
        # no longer catch, which is the part they can act on. Pagination carries the overflow.
        for area_id in dead_zones:
            rows.append("- " + self.adapter.area_display_name(area_id)[:28])

        # A NARROW SCREEN GETS ITS OWN ROWS, not these truncated.
        # The rows above are `label|field|field|...` for a client that lays them out in
        # columns on a 30-wide screen. A Game Boy has 20, which is not enough for that
        # layout at all, so chopping them would produce something that fits and says
        # nothing. Compact rows are plain single-line text the client prints as-is.
        width = self.adapter.info_panel_width()
        if width and width <= 20:
            npairs_alive = f"{alive}/{npairs}"
            compact = [
                "SOUL LINK",
                "",
                f"PAIRS {npairs_alive}",
                f"BADGES {badges}/8",
                f"DEAD ZONES {len(dead_zones)}",
            ]
            # THE PAIRS THEMSELVES, which is what the screen is for. A count tells the
            # player a number; the names and levels tell them which of their mons is tied
            # to which of their partner's, and whether it is still alive -- the thing they
            # opened the menu to find out.
            live = [e for e in s.links if e.a and e.a.key and e.b and e.b.key]
            if live:
                compact.append("")
                for e in live:
                    mine = e.a if player_id == "a" else e.b
                    theirs = e.b if player_id == "a" else e.a
                    tag = "X" if e.status != LinkStatus.ALIVE else " "
                    compact.append(f"{tag}{self._panel_name(mine)}-{self._panel_name(theirs)}"[:width])
            # NAME the dead zones, all of them. They used to be capped at eight because a
            # single screen holds eighteen rows and there was nowhere to put the rest; the
            # panel pages now, so the cap only hid information the player can act on.
            if dead_zones:
                compact.append("")
                for area_id in dead_zones:
                    compact.append(("-" + self.adapter.area_display_name(area_id))[:width])
            return {"cmd": "link_panel", "rows": [r[:width] for r in compact]}

        return {"cmd": "link_panel", "rows": rows}

    def _panel_name(self, mon) -> str:
        """A mon's short name for the 20-column native panel.

        Nickname when there is one, species otherwise, and never more than eight
        characters: two of these plus a separator has to fit in twenty columns.
        """
        if mon is None:
            return "?"
        name = (mon.nickname or "").strip() or self.adapter.species_name(mon.species) or "?"
        return name[:8]

    def _cache_mon_info(self, key: str, detail: dict, player_id: str | None = None):
        """Update the persistent per-monKey display cache from a detail dict.

        Also backfills level=0 and stale nicknames in any LinkEntry MonInfo
        for this key, so data gets corrected once the mon connects with
        live party data.
        """
        entry = self._mon_cache.get(key, {})
        for field in ("species_id", "nickname", "level", "gender", "held_item_id"):
            val = detail.get(field)
            if val:  # only overwrite with non-empty / non-zero
                entry[field] = val
        self._mon_cache[key] = entry
        # Backfill mon_stats for PC box level display (covers shiny/bonus mons)
        lv = detail.get("level", 0)
        maxhp = detail.get("maxHP", 0)
        stats = self.state.mon_stats[player_id] if player_id else None   # KEY-SCOPE-2: per player
        if stats is None:
            pass
        elif lv and key not in stats:
            stats[key] = {"level": lv}
            if maxhp:
                stats[key]["maxHP"] = maxhp
        elif lv and not stats.get(key, {}).get("level"):
            stats[key]["level"] = lv
        # Backfill level and nickname into link entries
        nick = detail.get("nickname", "")
        species_id = detail.get("species_id", 0)
        link_entry = (self.state.entry_for(player_id, key) if player_id
                      else self.state._key_index.get(key))
        if (lv or nick or species_id) and link_entry:
            dirty = False
            halves = ((link_entry.a if player_id == "a" else link_entry.b,) if player_id
                      else (link_entry.a, link_entry.b))
            for mi in halves:
                if mi and mi.key == key:
                    if lv and not mi.level:
                        mi.level = lv
                        dirty = True
                    if nick and nick != mi.nickname:
                        mi.nickname = nick
                        dirty = True
                    if species_id and species_id != mi.species:
                        mi.species = species_id
                        dirty = True
            if dirty:
                self.state._save()

    def _mon_display_name(self, player_id: str, key: str) -> str:
        if not key:
            return "?"
        detail = self.party_details.get(player_id, {}).get(key, {})
        if detail.get("nickname"):
            return detail["nickname"]
        cached = self._mon_cache.get(key, {})
        if cached.get("nickname"):
            return cached["nickname"]
        species_id = detail.get("species_id", 0) or cached.get("species_id", 0)
        if species_id:
            return self.adapter.species_name(species_id)
        link_entry = self.state.entry_for(player_id, key)
        mon = link_entry and (link_entry.a if player_id == "a" else link_entry.b)
        if mon and mon.key == key:
            return mon.nickname or self.adapter.species_name(mon.species) or key[:8]
        return key[:8]

    def _load_events(self):
        """Load persisted recent events from events.json on startup."""
        try:
            with open(self._events_path, encoding="utf-8") as f:
                events = json.load(f)
            self._recent_events = deque(events[:_EVENTS_MAX], maxlen=_EVENTS_MAX)
        except (FileNotFoundError, json.JSONDecodeError):
            pass

    def _save_events(self):
        """Persist the recent events ring buffer to events.json."""
        try:
            os.makedirs(os.path.dirname(self._events_path), exist_ok=True)
            with open(self._events_path, "w", encoding="utf-8") as f:
                # Belt-and-suspenders: never write more than _EVENTS_MAX entries.
                json.dump(list(self._recent_events)[:_EVENTS_MAX], f)
        except Exception as e:
            log.warning(f"Failed to save events.json: {e}")

    def _log_event(self, player_id: str, event_type: str, text: str,
                   area_id: str = "", key: str = ""):
        """Append a timestamped entry to the recent events ring buffer."""
        self._recent_events.appendleft({
            "ts": datetime.now().isoformat(timespec="seconds"),
            "player": player_id,
            "type": event_type,
            "text": text,
            "area_id": area_id,
            "key": key,
        })
        self._save_events()

    def _party_snapshot(self, player_id: str, party: list) -> dict:
        """Legacy display fields shared by admitted HELLO and tick observations.

        Cartridge/rule validation and cache publication remain at their existing
        dispatch boundaries. This helper only constructs the display snapshot.
        """
        adapter = self.adapter_for(player_id)
        return {
            mon["key"]: {
                "level": mon.get("level", 0),
                "hp": mon.get("hp", 1),
                "maxHP": mon.get("maxHP", 1),
                "nickname": mon.get("nickname", ""),
                "species_id": mon.get("species_id", 0),
                "held_item_id": mon.get("held_item_id", mon.get("held_item", 0)),
                "ability_id": mon.get("ability_id", mon.get("ability", 0)),
                "gender": adapter.gender_from_key(mon["key"], mon.get("species_id", 0)),
                "moves": mon.get("moves", []),
                "pp": mon.get("pp", []),
                "slot": mon.get("slot", index),
                "active": mon.get("active", False),
                "status_cond": mon.get("status_cond", 0),
                "stat_stages": mon.get("stat_stages"),
                # Small decoded dict (DVs/IVs/EVs/stat exp/computed stats), NOT the blob
                # itself -- party_details goes out on every status push. None when the
                # adapter can't decode it (box/linked mons, missing/bad blob_hex).
                "calc_stats": adapter.calc_stats(mon),
            }
            for index, mon in enumerate(party) if mon.get("key")
        }

    def _with_run_identity(self, commands: list) -> list:
        """Bind recovery clients to the existing run; an unmanaged run has no fabricated ID."""
        if self.adapter.supports_trade_recovery():
            for command in commands:
                if command.get("cmd") == "config":
                    command["run_id"] = self._run_id or ""
            if any(c.get("cmd") == "config" for c in commands):
                # A fresh client must learn the run binding before retiring journal records.
                commands = [c for c in commands if c.get("cmd") != "trade_final"] + [
                    c for c in commands if c.get("cmd") == "trade_final"]
        return commands

    def _dispatch(self, player_id: str, msg: dict) -> list:
        try:
            return self._dispatch_event(player_id, msg)
        except Exception:
            if msg.get("event") == "hello":
                raise  # the hello transaction owns rollback and retains its fatal boundary
            self.dispatch_errors[player_id] = self.dispatch_errors.get(player_id, 0) + 1
            if self.dispatch_errors[player_id] == 1:
                log.error("[%s] failed to dispatch %s", player_id, msg.get("event", "unknown"), exc_info=True)
            return [{"cmd": "noop", "refused": "error"}]

    def _dispatch_event(self, player_id: str, msg: dict) -> list:
        if self.state.load_failed:
            return [{"cmd": "noop", "refused": "load_failed"}]
        malformed = self.state.normalize_party_snapshot(player_id, msg)
        if malformed is not None:
            return malformed
        event = msg.get("event", "unknown")
        # enemy_party is client JSON read by every battle view: keep only a list of objects.
        if "enemy_party" in msg:
            ep = msg["enemy_party"]
            msg["enemy_party"] = [e for e in ep if isinstance(e, dict)] if isinstance(ep, list) else []
        # Snapshot area state before the event so we can detect outcome transitions.
        _pre_area_state = self.state.area_states.get(msg.get("area_id", ""))
        _pre_battle = self.battle_state[player_id]["in_battle"]
        _pre_run_over = self.state.run_over
        _pre_memorial_status = None
        if event == "memorialize_done":
            _pre_memorial_link = self.state.entry_for(player_id, msg.get("key", ""))
            if _pre_memorial_link:
                _pre_memorial_status = _pre_memorial_link.status.value

        # Block all events from a player whose identity was rejected.
        # Only a hello with correct identity can clear the error.
        # `refused` (INV-CLIENT contract): the line was NOT processed, so the client must not
        # retire an owed report on this reply. Older clients read a plain noop.
        if event != "hello" and self.state.identity_error.get(player_id):
            return [{"cmd": "noop", "refused": "identity"}]

        # Block everything but hello until the player's cartridge is admitted. Same shape as
        # the identity gate above and for the same reason: only a hello can change the
        # verdict, so letting anything else through would mutate rule state on evidence we
        # have already decided not to trust. `tick` is NOT exempt -- it carries the party
        # snapshot that diff_party turns into captures, which is exactly a semantic event.
        if event != "hello" and not self.is_admitted(player_id):
            return [{"cmd": "noop", "refused": "admission"}]

        refused = self.state.refuse_hidden_event(player_id, msg)
        if refused is not None:
            return refused  # before presentation enrichment or command/census mutation

        if event == "hello":
            # ── Transactional hello (Codex cx-2985fe38 F1/F2) ──────────────────────────
            # Stage the candidate rom_type/panel/panel_abi/sfx facts and the adapter they
            # imply; commit them only once admission (_decide_admission) AND save-identity
            # (state.handle_event) accept this hello. A rejected hello must leave
            # connected_players and the adapter EXACTLY as they were (docs/protocol.md
            # §2.2 steps 1/1': "rejected ⇒ state untouched") -- this used to be true only up
            # to the point mixed_games_error passed, after which the capability fields and
            # a same-generation-variant adapter swap were written unconditionally, before
            # admission or identity had a say.
            prev_conn = dict(self.connected_players.get(player_id, {}))
            prev_adapter = self.state.adapter
            prev_is_rr = self.state.is_rr

            rom = msg.get("rom_type", "") or "unknown"
            staged_conn = dict(prev_conn)
            staged_conn["rom_type"] = msg.get("rom_type", "")
            # Panel capability is per CARTRIDGE: on Gen 1 it comes from the companion ROM
            # patch, so a patched and an unpatched cartridge can sit in one run and the
            # generation alone cannot answer.
            if "panel" in msg:
                staged_conn["panel"] = bool(msg.get("panel"))
                staged_conn["panel_abi"] = msg.get("panel_abi", 0)
            # Native SFX likewise: the caps bit of the same companion mailbox.
            if "sfx" in msg:
                staged_conn["sfx"] = bool(msg.get("sfx"))

            # Resolve the adapter from the FULL rom_type, not only game_id/is_rr (F1): before
            # the run commits a rom_type, every hello rebuilds a fresh adapter from its own
            # cartridge string, so a same-generation variant swap (Red -> reset -> Yellow, or
            # the reverse) gets ITS OWN adapter instead of silently keeping the last one,
            # whose panel/trade/encounter-table capabilities are bound to the wrong variant.
            staged_adapter, staged_is_rr = prev_adapter, prev_is_rr
            if not self.state.rom_type:
                from server.adapters import game_id_for_rom_type, get_adapter
                _rt = msg.get("rom_type", "")
                new_game_id = game_id_for_rom_type(_rt)
                if new_game_id:
                    staged_is_rr = _rt.endswith("_rr")
                    staged_adapter = get_adapter(new_game_id, is_rr=staged_is_rr, rom_type=_rt)
            elif rom != "unknown" and rom != self.state.rom_type:
                log.warning(f"[{player_id}] hello rom_type={rom!r} ignored — "
                            f"run already locked to {self.state.rom_type!r}")

            def _rollback_hello():
                self.connected_players[player_id] = prev_conn
                self.state.adapter = prev_adapter
                self.adapter = prev_adapter
                self.state.is_rr = prev_is_rr

            # Admission and identity are decided against the CANDIDATE adapter (a randomized
            # cartridge's fingerprint check and an OT parse are both generation-specific), so
            # the staged values are applied now and rolled back on any refusal below.
            self.connected_players[player_id] = staged_conn
            self.state.adapter = staged_adapter
            self.adapter = staged_adapter
            self.state.is_rr = staged_is_rr

            # Exception-safe (review of 8418c931, P3): anything raising between staging and the
            # accept/reject decision must not leave the candidate cartridge applied.
            try:
                # Pick up a contract written after this server started, before deciding.
                self._refresh_rom_contract()
                verdict = self._decide_admission(player_id, msg)
                if self.admission.get(player_id, {}).get("state") != verdict["state"]:
                    (log.info if verdict["state"] == "admitted" else log.warning)(
                        "[%s] admission: %s — %s", player_id, verdict["state"], verdict["reason"])
                self.admission[player_id] = verdict
                if verdict["state"] != "admitted":
                    # STOP HERE. Recording the verdict was not enough: control used to fall
                    # straight into state.handle_event, which permanently locks
                    # player_identity, saves it, rebuilds party_keys from the rejected
                    # cartridge and QUEUES box_mon write commands back to the very client we
                    # just refused -- and then _ingest_rom_content adopted its encounter
                    # tables. Booting the wrong ROM once therefore did not merely fail to
                    # connect, it corrupted the run and wrote into the wrong save file.
                    _rollback_hello()
                    self._log_event(player_id, "hello",
                                    f"REJECTED — {verdict['reason']}",
                                    msg.get("loc_name", "") or msg.get("area_id", ""))
                    return [{"cmd": "noop", "refused": "admission"}]

                area    = msg.get("area_id", "")
                loc     = msg.get("loc_name", "")
                party_n = len(msg.get("party", []))
                log.info(f"[{player_id}] hello rom={rom} area='{area or loc}' party={party_n}")
                defer_trade_census = self.adapter.supports_trade_recovery() or (
                    ("party_hidden" in msg or "trade_outstanding" in msg) and self.adapter.refused_trade_recovery())
                if not defer_trade_census:
                    self._ingest_box_census(player_id, msg)

                # Run state machine first (handles identity lock check).
                cmds = self.state.handle_event(player_id, msg)
            except Exception:
                _rollback_hello()
                raise

            if msg.get("_rejected"):
                # Identity mismatch — log it, surface error, but don't update display data,
                # and roll back the staged capabilities/adapter: a wrong-save hello must not
                # leave the run's connected_players or adapter describing the wrong cartridge.
                _rollback_hello()
                self._log_event(player_id, "hello",
                                "REJECTED — wrong save/slot", area or loc)
                return self._with_run_identity(cmds)

            if defer_trade_census:
                self._ingest_box_census(player_id, msg)

            # ACCEPTED. The staged rom_type/panel/panel_abi/sfx/adapter are already applied
            # above; only the presentation follow-ups (panel re-send, adapter-switch log)
            # remain.
            self._last_panel_sig[player_id] = None
            if staged_adapter is not prev_adapter:
                log.info(f"Adapter switched to {staged_adapter.game_id} (rom_type={rom})")

            self._log_event(player_id, "hello",
                            f"Connected ({rom}, {party_n} mons)", loc or area)
            log.info("[%s] route %s -> %s (production)", player_id, rom, staged_adapter.game_id)
            self.player_area[player_id] = loc or area
            self.player_area_id[player_id] = area
            # Commit ROM type once — static for the run's lifetime.
            _dirty = False
            if rom and rom != "unknown" and not self.state.rom_type:
                self.state.rom_type = rom
                _dirty = True
                log.info(f"Committed ROM type '{rom}' for this run")
            if not self.state.artifact_kind:
                # "named" (a vanilla cartridge admitted by header) is a clean-layout artifact; a
                # kind the cartridge's content pairs differently (pairing_kind_for) commits that
                # effective kind, so the partner is paired against what the ROM holds.
                from server.adapters import GameRulesAdapter, adapter_class_for_rom_type
                _declared = msg.get("artifact_kind", "clean")
                _cls = adapter_class_for_rom_type(rom) or GameRulesAdapter
                # same title-aware rule as _mixed_games_error._kind: a rand hello is never
                # normalised to clean by another title's proof (gen3_frlge.pairing_kind_for_title)
                _title_aware = getattr(_cls, "pairing_kind_for_title", None)
                _by_content = (_title_aware(rom, _declared, msg.get("rom_content")) if _title_aware
                               else _cls.pairing_kind_for(_declared, msg.get("rom_content")))
                self.state.artifact_kind = (_by_content if _by_content != _cls.pairing_kind(_declared)
                                            else {"named": "clean"}.get(_declared, _declared))
                _dirty = True
                # Per-run capability: the adapter's native_trade_ui()/supports_info_panel()
                # follow the committed kind from here on (base adapter: no-op). Every
                # per-player adapter already bound (F5: an earlier hello from the OTHER
                # player, on the same still-uncommitted run) is stale at the default kind
                # until now, so it is refreshed here too rather than only on its own next
                # hello.
                self.state.adapter.set_artifact_kind(self.state.artifact_kind)
                for _pa in self._player_adapters.values():
                    _pa.set_artifact_kind(self.state.artifact_kind)

            # Publish only after admission and save identity accepted this hello. A refused
            # cartridge must not replace the last accepted player's acquisition/display data.
            # Bound AFTER the artifact_kind commit above (F5): the committed kind, not the
            # per-player adapter's constructor default, is what a Gen 2 partner's own
            # adapter must carry, including on the very first hello that commits it.
            self._bind_player_adapter(player_id, msg.get("rom_type", ""))
            gender = msg.get("player_gender")
            self.player_gender[player_id] = gender if type(gender) is int and gender in (0, 1) else None
            if "ball_count" in msg:
                self.player_ball_count[player_id] = msg["ball_count"]
            if "badges" in msg:
                self.player_badges[player_id] = msg["badges"]
            if "kanto_badges" in msg:
                self.player_kanto_badges[player_id] = msg["kanto_badges"]
            if "trainer_name" in msg:
                tname = msg["trainer_name"]
                self.trainer_name[player_id] = tname
                # Commit trainer name once per player — static for the run.
                if tname and not self.state.trainer_names.get(player_id):
                    self.state.trainer_names[player_id] = tname
                    _dirty = True
                    log.info(f"Committed trainer name '{tname}' for player {player_id}")
            # Both _cache_mon_info call sites below (pc_boxes and party) can write
            # self.state.mon_stats. Snapshot before either runs so the single save at
            # the end of the handler catches every path, instead of saving too early
            # (before either loop) and losing whichever one runs after it.
            stats_before = copy.deepcopy(self.state.mon_stats)
            if rom_payload := (msg.get("rom_content") or self._contracted_rom(player_id, msg)):
                self._ingest_rom_content(player_id, rom_payload)
            if "pc_boxes" in msg and not self.state.party_snapshot_withheld(player_id, msg):
                self.pc_boxes[player_id] = msg["pc_boxes"]
                for bentry in msg["pc_boxes"]:
                    bk = bentry.get("key", "")
                    if bk:
                        self._cache_mon_info(bk, bentry, player_id)
                self._check_memorial_box_contamination(player_id, msg["pc_boxes"])
            # A withheld party preserves the last display, without refreshing its evidence.
            if not self.state.party_snapshot_withheld(player_id, msg):
                self.party_details[player_id] = self._party_snapshot(player_id, msg.get("party", []))
                for k, det in self.party_details[player_id].items():
                    self._cache_mon_info(k, det, player_id)
            if _dirty or self.state.mon_stats != stats_before:
                self.state._save()
            # Seed battle state from hello (so page reflects battle immediately)
            if "in_battle" in msg:
                self.battle_state[player_id]["in_battle"] = bool(msg["in_battle"])
            if "is_trainer_battle" in msg:
                self.battle_state[player_id]["is_trainer_battle"] = bool(msg["is_trainer_battle"])
            if "enemy_party" in msg:
                self.battle_state[player_id]["enemy_party"] = msg["enemy_party"]
            return self._with_run_identity(cmds)
        elif event == "area_enter":
            area = msg.get("area_id", "")
            loc  = msg.get("loc_name", "")
            disp = loc or area  # prefer specific location name for display
            log.info(f"[{player_id}] area_enter → '{disp}'")
            if disp:
                self.player_area[player_id] = disp
                # Use area_id for display name resolution (has underscores for proper formatting)
                disp_name = self.adapter_for(player_id).area_display_name(area or disp)
                self._log_event(player_id, "area_enter",
                                f"Entered {disp_name}", disp)
            if area:
                self.player_area_id[player_id] = area
        elif event == "capture":
            key = msg.get("key", "")
            log.info(f"[{player_id}] capture key={key} lv={msg.get('level','?')} area='{msg.get('area_id','')}'")
            if key:
                sid = msg.get("species_id", 0)
                sp_name = self.adapter_for(player_id).species_name(sid) if sid else "?"
                self._log_event(player_id, "capture",
                                f"Caught {sp_name} Lv{msg.get('level','?')}",
                                msg.get("area_id", ""), key)
                detail = {
                    "level":        msg.get("level", 0),
                    "hp":           msg.get("hp", 1),
                    "maxHP":        msg.get("maxHP", 1),
                    "nickname":     msg.get("nickname", ""),
                    "species_id":   sid,
                    "held_item_id": msg.get("held_item_id", msg.get("held_item", 0)),
                    "ability_id":   msg.get("ability_id", msg.get("ability", 0)),
                    "gender":       self.adapter.gender_from_key(key, sid),
                }
                self.party_details[player_id][key] = detail
                self._cache_mon_info(key, detail, player_id)
        elif event == "faint":
            key = msg.get("key", "")
            log.info(f"[{player_id}] faint key={key} area='{msg.get('area_id','')}'")
            if key and key in self.party_details[player_id]:
                self.party_details[player_id][key]["hp"] = 0
                nick = self.party_details[player_id][key].get("nickname", "")
                self._log_event(player_id, "faint",
                                f"{nick or key[:8]} fainted!",
                                msg.get("area_id", ""), key)
            # Enrich msg with cached battle-state killer info for state machine killfeed tracking.
            bs = self.battle_state.get(player_id, {})
            enemy = bs.get("enemy_party") or []
            active_foe = next((e for e in enemy if e.get("hp", 1) > 0), enemy[0] if enemy else None)
            if active_foe:
                msg["_killer_species"] = active_foe.get("species_id", 0)
                msg["_killer_level"]   = active_foe.get("level", 0)
            msg["_is_trainer"] = bool(bs.get("is_trainer_battle", False))
            msg["_is_wild_battle"] = bool(bs.get("in_battle")) and not msg["_is_trainer"]
            if msg["_is_trainer"]:
                msg["_trainer_name"] = bs.get("opponent_name", "")
                msg["_trainer_class"] = bs.get("opponent_class", "")
            # Inject current level from party_details so memorial shows death-time level.
            pd = self.party_details[player_id].get(key, {})
            if pd.get("level"):
                msg["_level"] = pd["level"]
        elif event == "no_catch":
            log.info(f"[{player_id}] no_catch area='{msg.get('area_id','')}'")
            self._log_event(player_id, "no_catch",
                            f"Missed catch at {self.adapter_for(player_id).area_display_name(msg.get('area_id',''))}",
                            msg.get("area_id", ""))
        elif event == "whiteout":
            log.info(f"[{player_id}] whiteout")
            bs = self.battle_state.get(player_id, {})
            msg["_is_wild_battle"] = (
                bool(bs.get("in_battle")) and not bool(bs.get("is_trainer_battle", False)))
            for key in self.party_details[player_id]:
                self.party_details[player_id][key]["hp"] = 0
            self._log_event(player_id, "whiteout", "WHITED OUT! All party mons fainted")
        elif event == "party_to_box":
            key = msg.get("key", "")
            log.info(f"[{player_id}] party_to_box key={key}")
            self.party_details[player_id].pop(key, None)
        elif event == "stats_cache":
            key = msg.get("key", "")
            log.debug(f"[{player_id}] stats_cache key={key}")
            self.party_details[player_id].pop(key, None)
        elif event == "box_to_party":
            key = msg.get("key", "")
            log.info(f"[{player_id}] box_to_party key={key}")
            if key and key not in self.party_details[player_id]:
                # Populate from persistent cache so sprites don't go blank until next tick.
                cached = self._mon_cache.get(key, {})
                self.party_details[player_id][key] = {
                    "level":        cached.get("level", 0),
                    "hp":           1,
                    "maxHP":        1,
                    "nickname":     cached.get("nickname", ""),
                    "species_id":   cached.get("species_id", 0),
                    "held_item_id": cached.get("held_item_id", 0),
                    "gender":       cached.get("gender", ""),
                }
        elif event == "memorialize_done":
            key = msg.get("key", "")
            log.info(f"[{player_id}] memorialize_done key={key}")
            self.party_details[player_id].pop(key, None)
        elif event == "key_change":
            # Presentation caches migrate AFTER state.handle_event accepts it (below).
            log.info(f"[{player_id}] key_change {msg.get('old_key', '')[:8]} → {msg.get('new_key', '')[:8]}")
        elif event == "safe":
            log.debug(f"[{player_id}] safe state")
        elif event == "tick":
            gender = msg.get("player_gender")
            self.player_gender[player_id] = gender if type(gender) is int and gender in (0, 1) else None
            if "ball_count" in msg:
                self.player_ball_count[player_id] = msg["ball_count"]
            if "badges" in msg:
                self.player_badges[player_id] = msg["badges"]
            if "kanto_badges" in msg:
                self.player_kanto_badges[player_id] = msg["kanto_badges"]
            if "trainer_name" in msg:
                self.trainer_name[player_id] = msg["trainer_name"]
            if "pc_boxes" in msg and not self.state.party_snapshot_withheld(player_id, msg):
                self.pc_boxes[player_id] = msg["pc_boxes"]
                for bentry in msg["pc_boxes"]:
                    bk = bentry.get("key", "")
                    if bk:
                        self._cache_mon_info(bk, bentry, player_id)
                self._check_memorial_box_contamination(player_id, msg["pc_boxes"])
            if "in_battle" in msg:
                was_in_battle = self.battle_state[player_id]["in_battle"]
                now_in_battle = bool(msg["in_battle"])
                self.battle_state[player_id]["in_battle"] = now_in_battle
                if was_in_battle and not now_in_battle:
                    self.battle_state[player_id]["trainer_id"] = 0
                    self.battle_state[player_id]["opponent_name"] = ""
                    self.battle_state[player_id]["opponent_class"] = ""
                    self.battle_state[player_id]["is_trainer_battle"] = False
                    self.battle_state[player_id]["enemy_party"] = []
                    self.battle_state[player_id]["is_doubles"] = False
            if "is_trainer_battle" in msg:
                self.battle_state[player_id]["is_trainer_battle"] = bool(msg["is_trainer_battle"])
            if "trainer_id" in msg:
                tid = msg["trainer_id"]
                self.battle_state[player_id]["trainer_id"] = tid
                # Resolve trainer name/class via adapter
                tr_name, tr_class = self.adapter_for(player_id).trainer_info(tid)
                if tr_class:
                    self.battle_state[player_id]["opponent_name"] = tr_name
                    self.battle_state[player_id]["opponent_class"] = tr_class
                elif "opponent_name" in msg or "opponent_class" in msg:
                    # Non-RR (e.g. Gen 1/2 client emits class+name directly because
                    # trainer_id alone is ambiguous without class context). Accept
                    # whatever the client provided.
                    if msg.get("opponent_name"):
                        self.battle_state[player_id]["opponent_name"] = msg["opponent_name"]
                    if msg.get("opponent_class"):
                        self.battle_state[player_id]["opponent_class"] = msg["opponent_class"]
            if "enemy_party" in msg:
                self.battle_state[player_id]["enemy_party"] = msg["enemy_party"]
            if "is_doubles" in msg:
                self.battle_state[player_id]["is_doubles"] = bool(msg["is_doubles"])
            elif "enemy_party" in msg:
                # Passive inference: >1 active enemy implies doubles (benefits Gen 4/5 automatically).
                active_count = sum(1 for e in msg["enemy_party"] if e.get("active"))
                if active_count > 1:
                    self.battle_state[player_id]["is_doubles"] = True
            # Dupes clause: notify at wild battle start (before handle_event flushes the queue).
            if "in_battle" in msg and not was_in_battle and now_in_battle \
                    and not self.battle_state[player_id].get("is_trainer_battle"):
                _battle_area_id = msg.get("area_id", "")
                _ep = self.battle_state[player_id].get("enemy_party", [])
                _enc_species = _ep[0].get("species_id", 0) if _ep else 0
                if _battle_area_id and _enc_species:
                    # Check if partner is also in a concurrent wild battle on the same area.
                    _partner_id = "b" if player_id == "a" else "a"
                    _partner_bs = self.battle_state[_partner_id]
                    _partner_battle_species = 0
                    if (_partner_bs.get("in_battle") and not _partner_bs.get("is_trainer_battle")
                            and self.player_area_id.get(_partner_id) == _battle_area_id):
                        _partner_ep = _partner_bs.get("enemy_party", [])
                        _partner_battle_species = _partner_ep[0].get("species_id", 0) if _partner_ep else 0
                    if self.state.check_dupe_on_encounter(player_id, _battle_area_id, _enc_species,
                                                          partner_battle_species=_partner_battle_species):
                        _sp_name = self.adapter.species_name(_enc_species)
                        self._log_event(player_id, "reroll",
                                        f"🔁 Dupes clause: {_sp_name} -- reroll!", _battle_area_id)
            # Update location from tick in case area_enter was missed (e.g. on reconnect).
            tick_area = msg.get("loc_name", "") or msg.get("area_id", "")
            if tick_area:
                self.player_area[player_id] = tick_area
            tick_area_id = msg.get("area_id", "")
            if tick_area_id:
                self.player_area_id[player_id] = tick_area_id
            log.debug(f"[{player_id}] tick")
        elif event == "ghost_pos":
            # High-frequency (~20Hz) overworld peer-position relay; handled in
            # state.handle_event (_handle_ghost_pos). No server-side enrichment or
            # per-frame logging (would flood at this rate).
            pass
        elif event == "peer_interact":
            # Talk-to-ghost: handled in state.handle_event (_handle_peer_interact),
            # which notifies the partner. Low-frequency; no enrichment needed here.
            pass
        elif event in ("trade_request", "mon_chosen", "menu_result", "trade_done", "apply_ready", "status"):
            # Talk-to-partner trade flow (request → pick → confirm → trade scene → done) + periodic
            # badge status: handled in state.handle_event. User-paced (or ~0.2 Hz for status); no
            # server-side enrichment or per-event logging needed.
            pass
        else:
            log.debug(f"[{player_id}] unknown event '{event}' seq={msg.get('seq','?')}")

        # On tick, replace party_details entirely from the authoritative party snapshot.
        # This prevents captures that went straight to the PC box (full-party captures)
        # from appearing as phantom party mons between ticks.
        if ("party" in msg and event == "tick"
                and not self.state.party_snapshot_withheld(player_id, msg)):
            self.party_details[player_id] = self._party_snapshot(player_id, msg["party"])
            for k, det in self.party_details[player_id].items():
                self._cache_mon_info(k, det, player_id)

        self._ingest_box_census(player_id, msg)
        cmds = self.state.handle_event(player_id, msg)

        if event == "key_change" and msg.get("_key_change_status") == "migrated":
            self._migrate_presentation_key(player_id, msg.get("old_key", ""), msg.get("new_key", ""))

        # Refresh the native in-game panel, but only when its content actually changed — this runs
        # on every tick, and the panel is a few hundred bytes. The client keeps the last one staged
        # in EWRAM so opening the menu needs no round-trip at all.
        if self._player_has_panel(player_id):
            _panel = self._build_link_panel(player_id)
            _sig = json.dumps(_panel, sort_keys=True)
            if _sig != self._last_panel_sig.get(player_id):
                self._last_panel_sig[player_id] = _sig
                cmds = list(cmds) + [_panel]

        # Post-dispatch: log outcome events that require state-machine results.
        _partner = "b" if player_id == "a" else "a"
        _area_id = msg.get("area_id", "")
        if event in ("capture", "no_catch") and _area_id:
            _new_state = self.state.area_states.get(_area_id)
            if _new_state is not None and _new_state != _pre_area_state:
                _area_disp = self.adapter.area_display_name(_area_id)
                _sv = _new_state.value
                if _sv == "linked":
                    _cap_key = msg.get("key", "")
                    _link = self.state.entry_for(player_id, _cap_key)
                    if _link:
                        _ma = _link.a if player_id == "a" else _link.b
                        _mb = _link.b if player_id == "a" else _link.a
                        _ma_nick = (_ma.nickname or self.adapter.species_name(_ma.species)) if _ma else "?"
                        _mb_nick = (_mb.nickname or self.adapter.species_name(_mb.species)) if _mb else "?"
                        self._log_event(player_id, "linked",
                                        f"✓ Linked {_ma_nick} × {_mb_nick} on {_area_disp}", _area_id)
                        self._log_event(_partner, "linked",
                                        f"✓ Linked {_mb_nick} × {_ma_nick} on {_area_disp}", _area_id)
                elif _sv == "dead_zone":
                    self._log_event(player_id, "dead_zone",
                                    f"☠ Dead zone: {_area_disp}", _area_id)
                    self._log_event(_partner, "dead_zone",
                                    f"☠ Dead zone: {_area_disp}", _area_id)

        if event == "faint" and msg.get("key"):
            _faint_key = msg["key"]
            _link = self.state.entry_for(player_id, _faint_key)
            if _link:
                _p_mon = _link.b if player_id == "a" else _link.a
                _death = (self.state.queued_death_cmd(_partner, _p_mon.key)
                          if _p_mon else None)
                if _death:
                    _cached = self._mon_cache.get(_p_mon.key, {})
                    _p_nick = (
                        _cached.get("nickname") or
                        self.adapter.species_name(_cached.get("species_id", 0)) or
                        _p_mon.key[:8]
                    )
                    _verb = "exploded" if _death == "force_explode" else "force fainted"
                    self._log_event(_partner, _death,
                                    f"⚡ {_p_nick} {_verb}!",
                                    _area_id, _p_mon.key)

        if event == "capture":
            _cap_key = msg.get("key", "")
            if _cap_key and _cap_key in self.state.bonus_keys.get(player_id, set()):
                _nick = self._mon_display_name(player_id, _cap_key)
                self._log_event(player_id, "shiny",
                                f"✨ Shiny {_nick}!", _area_id, _cap_key)

        if event == "party_to_box":
            _box_key = msg.get("key", "")
            if _box_key:
                _nick = self._mon_display_name(player_id, _box_key)
                self._log_event(player_id, "party_to_box",
                                f"📦 {_nick} deposited", "", _box_key)

        if event == "box_to_party":
            _box_key = msg.get("key", "")
            if _box_key:
                _nick = self._mon_display_name(player_id, _box_key)
                self._log_event(player_id, "box_to_party",
                                f"↑ {_nick} retrieved", "", _box_key)

        if event == "memorialize_done":
            _mem_key = msg.get("key", "")
            _link = self.state.entry_for(player_id, _mem_key)
            _post_status = getattr(getattr(_link, "status", None), "value", getattr(_link, "status", None))
            if _link and _post_status == "memorial" and _pre_memorial_status != "memorial":
                _a_name = (_link.a.nickname or self.adapter.species_name(_link.a.species)) if _link.a else "?"
                _b_name = (_link.b.nickname or self.adapter.species_name(_link.b.species)) if _link.b else "?"
                self._log_event(player_id, "memorialize",
                                f"⚰ {_a_name} × {_b_name} laid to rest", _link.area_id, _mem_key)

        if event == "key_change":
            _new_key = msg.get("new_key", "")
            _migrated = self.party_details[player_id].get(_new_key, {})
            _nick = _migrated.get("nickname", "") or msg.get("old_key", "")[:8]
            _reason = msg.get("reason", "")
            if msg.get("_rejected"):
                _what = "key change REJECTED (identity lost)"
            elif msg.get("_key_change_status") == "rejected":
                _what = "key change refused"
            elif msg.get("new_species") is not None:
                _what = "evolved"
            elif _reason == "trade_undo":
                _what = "trade reverted"
            else:
                _what = "nature/ability changed"
            self._log_event(player_id, "key_change",
                            f"🔄 {_nick or _new_key[:8]} {_what}", "", _new_key)

        # Log clause violations (capture rejected) and dupes rerolls (no_catch suppressed).
        # gui_prompt commands are only queued for these scenarios, so they're a reliable signal.
        if event in ("capture", "no_catch"):
            for c in cmds:
                if c.get("cmd") == "gui_prompt":
                    _prompt_text = c.get("text", "")
                    _kind = prompt_event_type(_prompt_text)
                    if _kind:
                        self._log_event(player_id, _kind,
                                        f"{'⚠' if _kind == 'violation' else '🔁'} {_prompt_text}", _area_id)

        self._emit_obs_triggers(player_id, msg, cmds, _pre_area_state, _pre_battle, _pre_run_over)
        return self._with_run_identity(cmds)


    def _emit_obs_triggers(self, player_id: str, msg: dict, cmds: list,
                           pre_area_state, pre_battle: bool, pre_run_over: bool):
        """Collect all game events that fired this dispatch cycle and submit them
        to OBSController.submit_fired() for priority-ordered scene resolution.

        Rules are evaluated in list order — the first matching rule per target player
        wins regardless of how many events fire simultaneously.
        """
        event = msg.get("event", "")
        _area_id = msg.get("area_id", "")
        _partner = "b" if player_id == "a" else "a"

        # fired: list of (trigger_name, src_player, metadata)
        fired = []

        # battle_start / battle_end — in_battle transition from tick
        if event == "tick" and "in_battle" in msg:
            now_battle = self.battle_state[player_id]["in_battle"]
            if not pre_battle and now_battle:
                fired.append(("battle_start", player_id, {}))
                if self.battle_state[player_id].get("is_trainer_battle"):
                    fired.append(("trainer_battle_start", player_id, {}))
                else:
                    fired.append(("wild_battle_start", player_id, {}))
                _cur_area = self.player_area_id.get(player_id, "")
                if self._area_has_open_encounter(_cur_area):
                    fired.append(("battle_start_new", player_id, {}))
            elif pre_battle and not now_battle:
                fired.append(("battle_end", player_id, {}))

        if event == "faint":
            fired.append(("faint", player_id, {}))
            # link_death — partner receives a death command (force_faint or force_explode)
            _faint_key = msg.get("key", "")
            if _faint_key:
                _link = self.state.entry_for(player_id, _faint_key)
                if _link:
                    _p_mon = _link.b if player_id == "a" else _link.a
                    if _p_mon and self.state.queued_death_cmd(_partner, _p_mon.key):
                        fired.append(("link_death", _partner, {}))

        if event == "whiteout":
            fired.append(("whiteout", player_id, {}))

        if event == "capture":
            fired.append(("capture", player_id, {}))
            _cap_key = msg.get("key", "")
            if _cap_key and _cap_key in self.state.bonus_keys.get(player_id, set()):
                fired.append(("shiny", player_id, {}))

        if event == "area_enter":
            fired.append(("area_enter", player_id, {"area_id": _area_id}))
            if self._area_has_open_encounter(_area_id):
                fired.append(("area_enter_new", player_id, {"area_id": _area_id}))

        if event == "party_to_box":
            fired.append(("party_to_box", player_id, {}))

        if event == "box_to_party":
            fired.append(("box_to_party", player_id, {}))

        if event == "memorialize_done":
            fired.append(("memorialize_done", player_id, {}))

        # linked / dead_zone — area state transition post-dispatch
        if event in ("capture", "no_catch") and _area_id:
            _new_state = self.state.area_states.get(_area_id)
            if _new_state is not None and _new_state != pre_area_state:
                _sv = _new_state.value
                if _sv == "linked":
                    fired.append(("linked", player_id, {}))
                    fired.append(("linked", _partner, {}))
                elif _sv == "dead_zone":
                    fired.append(("dead_zone", player_id, {}))
                    fired.append(("dead_zone", _partner, {}))

        if self.state.run_over and not pre_run_over:
            fired.append(("run_over", player_id, {}))

        if fired:
            self.obs.submit_fired(fired)

    def _area_display(self, area_id: str) -> str:
        """Return a human-readable display name for area_id, handling bonus pair synthetic IDs."""
        if area_id.startswith("_bonus_"):
            return "✦ Bonus Pair"
        return self.adapter.area_display_name(area_id)

    def _area_has_open_encounter(self, area_id: str) -> bool:
        """True if area_id is an active nuzlocke area that hasn't been fully resolved.

        Returns False if: area not tracked, already linked, or dead zone.
        """
        if not area_id:
            return False
        state = self.state.area_states.get(area_id)
        if state is None:
            return False
        sv = state.value
        return sv not in ("linked", "dead_zone")

    def _build_status_dict(self) -> dict:
        """Serialize current server state to a JSON-safe dict."""
        s = self.state

        def _enrich_killer(killer, initiating_player=""):
            """Add species_name to a killer dict for the memorial/killfeed.

            The killer belongs to the INITIATING player's cartridge (Codex cx-2985fe38 F4):
            on a randomized/Gen 2 pairing the run adapter may not be the side that fainted
            this link's mon.
            """
            if not killer:
                return killer
            k = dict(killer)
            sp = k.get("species", 0)
            if sp:
                k["species_name"] = self.adapter_for(initiating_player).species_name(sp)
            return k

        def _move_details(mon: dict, *, boxed: bool = False, adapter=None) -> list[dict]:
            """Resolve a mon's raw move ids into full move dicts with PP applied.

            Gen 3 sends pp_bonuses as a packed bitfield (2 bits per move); Gen 4 sends
            pp_ups as a list[4]. Box mons carry no current PP, so they show the maximum.

            `adapter` is the OWNING player's adapter (Codex cx-2985fe38 F4): move data is
            cross-title content on a randomized/Gen 2 pairing, so a partner's move names
            must come from their own cartridge, not the run's. Defaults to the run adapter
            for the few call sites (none left) that have no player to ask.
            """
            adapter = adapter or self.adapter
            raw_pp = mon.get("pp", [])
            pp_bonuses = mon.get("pp_bonuses", 0)
            pp_ups_list = mon.get("pp_ups") or []
            out = []
            for idx, mid in enumerate(mon.get("moves", [])):
                if not mid or mid <= 0:
                    continue
                md = adapter.move_data(mid)
                if not md:
                    continue
                md = dict(md)
                if boxed:
                    md["current_pp"] = md.get("pp", 0)
                else:
                    base_pp = md.get("pp", 0)
                    pp_ups = (pp_ups_list[idx] if idx < len(pp_ups_list)
                              else (pp_bonuses >> (idx * 2)) & 0x3)
                    if base_pp:
                        md["pp"] = base_pp + (base_pp * pp_ups) // 5
                    md["current_pp"] = raw_pp[idx] if idx < len(raw_pp) else md["pp"]
                out.append(md)
            return out

        def _enrich_party(pid):
            """Add key, species_name, ability_name, item_name, sprite_html and move_details
            to each party detail entry."""
            adapter = self.adapter_for(pid)
            enriched = {}
            for key, det in self.party_details.get(pid, {}).items():
                d = dict(det)
                # The key is the dict key and was not repeated in the value, so a consumer
                # iterating values (a partner lookup, a template loop) had no way back to it.
                d["key"] = key
                sid = d.get("species_id", 0)
                form = d.get("form", 0)
                d["species_name"] = adapter.species_name(sid) if sid else ""
                d["sprite_html"] = self._get_sprite_html(sid, form, pid) if sid else ""
                aid = d.get("ability_id", 0)
                d["ability_name"] = adapter.ability_name(aid, sid) if aid else ""
                iid = d.get("held_item_id", 0)
                d["item_name"] = adapter.item_name(iid) if iid else ""
                d["move_details"] = _move_details(d, adapter=adapter)
                enriched[key] = d
            return enriched

        def _enrich_box(pid):
            """Add item_name and move_details to PC box entries."""
            adapter = self.adapter_for(pid)
            enriched = []
            for bentry in self.pc_boxes.get(pid, []):
                b = dict(bentry)
                iid = b.get("held_item_id", 0)
                b["item_name"] = adapter.item_name(iid) if iid else ""
                b["move_details"] = _move_details(b, boxed=True, adapter=adapter)
                enriched.append(b)
            return enriched

        def _enrich_battle_state(pid):
            """Add sprite_html, species_name, and move_details to each enemy_party entry."""
            adapter = self.adapter_for(pid)
            bs = dict(self.battle_state.get(pid, {"in_battle": False, "enemy_party": []}))
            enriched = []
            for em in bs.get("enemy_party") or []:
                em2 = dict(em)
                sid = em2.get("species_id", 0)
                form = em2.get("form", 0)
                # Rendered |safe, so it is always ours: a client-sent sprite_html is dropped.
                em2.pop("sprite_html", None)
                if sid:
                    em2["sprite_html"] = self._get_sprite_html(sid, form, pid)
                if sid and not em2.get("species_name"):
                    em2["species_name"] = adapter.species_name(sid)
                em2["move_details"] = _move_details(em2, adapter=adapter)
                enriched.append(em2)
            bs["enemy_party"] = enriched
            bs["calc_preview"] = self._calc_preview(pid, enriched) if bs.get("in_battle") else None
            return bs

        return {
            # "" when the last save succeeded; the error text when it did not.
            "save_failed": s.save_failed,
            "load_failed": s.load_failed,
            "dispatch_errors": dict(getattr(self, "dispatch_errors", {"a": 0, "b": 0})),
            "players": {
                pid: {
                    "connected":      self.connected_players.get(pid, {}).get("connected", False),
                    "rom_type":       self.connected_players.get(pid, {}).get("rom_type", "?"),
                    "last_event":     self.connected_players.get(pid, {}).get("last_event", "—"),
                    "last_seen":      self.connected_players.get(pid, {}).get("last_seen", "—"),
                    # Seconds since this player last said anything, or None if never seen.
                    "last_seen_age":  _age_secs(self.connected_players.get(pid, {}).get("last_seen_ts")),
                    "last_seen_label": _age_label(_age_secs(self.connected_players.get(pid, {}).get("last_seen_ts"))),
                    # `connected` stays True on a silent socket; only the age reveals it.
                    "stale":          (_age_secs(self.connected_players.get(pid, {}).get("last_seen_ts")) or 0)
                                      >= STALE_AFTER_SECS,
                    "nuzlocke_active": s.pokeballs_obtained.get(pid, False),
                    "current_area":   self.player_area.get(pid, ""),
                    "current_area_id": self.player_area_id.get(pid, ""),
                    "current_area_display": self.adapter_for(pid).area_display_name(
                        self.player_area_id.get(pid, "") or self.player_area.get(pid, "")
                    ),
                    "ball_count":     self.player_ball_count.get(pid, 0),
                    "badges":         self.player_badges.get(pid, 0),
                    "kanto_badges":   self.player_kanto_badges.get(pid, 0),
                    "trainer_name":   self.trainer_name.get(pid, ""),
                    "pc_boxes":       _enrich_box(pid),
                    "party_keys":     self._get_party_ordered(pid),
                    "party_details":  _enrich_party(pid),
                    "queued":         len(s.queued_commands.get(pid, [])),
                    "battle_state":   _enrich_battle_state(pid),
                    "identity_error": s.identity_error.get(pid, ""),
                    "awaiting_save": s.awaiting_save.get(pid, False),  # BURIAL-VISIBLE
                    **({"trade_recovery": {"hidden": s.party_hidden[pid],
                                          "pending": s.trade_recovery_pending[pid],
                                          "problem": s.trade_recovery_errors[pid]}}
                       if self.adapter.supports_trade_recovery() else {}),
                    # Surfaced rather than only logged: a player whose events are being
                    # dropped needs to be told which cartridge the run expects, otherwise
                    # the game simply appears not to be recording anything.
                    "admission": self.admission.get(pid, {}).get(
                        "state", "admitted"),
                    "admission_reason": self.admission.get(pid, {}).get(
                        "reason", ""),
                    # What THIS cartridge can do, so a template asks `caps.abilities`
                    # rather than "is this RR". Per rom_type: firered and firered_rr
                    # differ on explode_mode and info_panel through one adapter class.
                    "capabilities": ui_capabilities(
                        self.adapter_for(pid),
                        str(self.connected_players.get(pid, {}).get("rom_type") or "")),
                    # What THIS cartridge reported at hello (companion mailbox caps): the
                    # START-menu panel and the native sound path. None until it says.
                    "companion": {k: self.connected_players.get(pid, {}).get(k)
                                  for k in ("panel", "sfx")},
                    "encounter_table": self._enc_table_for_status(
                        self.player_area_id.get(pid, "") or self.player_area.get(pid, ""),
                        pid,
                    ),
                    # Pre-rendered: the Upcoming Key Trainers widget (Radical Red) owns
                    # its own <details> contracts and stays a Python builder for now.
                    "trainer_panel_html": self._trainer_panel_html(
                        self.player_area_id.get(pid, "") or self.player_area.get(pid, ""), pid),
                }
                for pid in ["a", "b"]
            },
            "links": [
                {
                    "area_id":    e.area_id,
                    "area_display": self._area_display(e.area_id),
                    "a_key":      e.a.key if e.a else None,
                    "a_nickname": e.a.nickname if e.a else "",
                    "a_species":  e.a.species if e.a else 0,
                    "a_species_name": self.adapter_for("a").species_name(e.a.species) if e.a and e.a.species else "",
                    "a_sprite_html": self._get_sprite_html(e.a.species, 0, "a") if e.a and e.a.species else "",
                    "a_level":    self._resolve_level("a", e.a),
                    "a_shiny":    e.a.is_shiny if e.a else False,
                    "b_key":      e.b.key if e.b else None,
                    "b_nickname": e.b.nickname if e.b else "",
                    "b_species":  e.b.species if e.b else 0,
                    "b_species_name": self.adapter_for("b").species_name(e.b.species) if e.b and e.b.species else "",
                    "b_sprite_html": self._get_sprite_html(e.b.species, 0, "b") if e.b and e.b.species else "",
                    "b_level":    self._resolve_level("b", e.b),
                    "b_shiny":    e.b.is_shiny if e.b else False,
                    "a_enc_species": e.encounter_a.species if e.encounter_a else 0,
                    "a_enc_level":   e.encounter_a.level   if e.encounter_a else 0,
                    "b_enc_species": e.encounter_b.species if e.encounter_b else 0,
                    "b_enc_level":   e.encounter_b.level   if e.encounter_b else 0,
                    "status":     e.status.value,
                    "killed_at":  e.killed_at,
                }
                for e in s.links
            ],
            "area_states": {k: v.value for k, v in s.area_states.items()},
            "pending_captures": {
                area: {
                    pid: {
                        "key": mon.key, "nickname": mon.nickname,
                        "species": mon.species, "level": mon.level,
                        "species_name": self.adapter_for(pid).species_name(mon.species) if mon.species else "",
                        "sprite_html": self._get_sprite_html(mon.species, 0, pid) if mon.species else "",
                    }
                    for pid, mon in players.items()
                }
                for area, players in s.pending_captures.items()
            },
            "encounter_locks": {
                area: {
                    pid: {
                        "species": lock.species,
                        "level": lock.level,
                        "species_name": self.adapter_for(pid).species_name(lock.species),
                        "sprite_html": self._get_sprite_html(lock.species, 0, pid),
                    }
                    for pid, lock in players.items()
                }
                for area, players in s.encounter_locks.items()
            },
            "rules": {
                "species_lock": s.species_lock,
                "gender_lock": s.gender_lock,
                "type_lock": s.type_lock,
                "retry_same_species": s.retry_same_species,
                "ignore_wild_faints": s.ignore_wild_faints,
                "explode_mode": s.explode_mode,
                "rival_team_swap": s.rival_team_swap,
                "overworld_presence": s.overworld_presence,
                "native_messages": s.native_messages,
                "native_sounds": s.native_sounds,
                "battle_calc": s.battle_calc,
                "pc_trade_npc": s.pc_trade_npc,
                "phone_calls": s.phone_calls,
            },
            "recent_events": list(self._recent_events),
            "killfeed": sorted(
                [
                    {
                        "killed_at":        e.killed_at,
                        "area_id":          e.area_id,
                        "area_display":     self._area_display(e.area_id),
                        "cause":            e.cause,
                        "killer":           _enrich_killer(e.killer, e.initiating_player),
                        "initiating_player": e.initiating_player,
                        "a_key":      e.a.key      if e.a else None,
                        "a_nickname": e.a.nickname if e.a else "",
                        "a_species":  e.a.species  if e.a else 0,
                        "a_species_name": self.adapter_for("a").species_name(e.a.species) if e.a and e.a.species else "",
                        "a_sprite_html": self._get_sprite_html(e.a.species, 0, "a") if e.a and e.a.species else "",
                        "a_level":    self._resolve_level("a", e.a),
                        "b_key":      e.b.key      if e.b else None,
                        "b_nickname": e.b.nickname if e.b else "",
                        "b_species":  e.b.species  if e.b else 0,
                        "b_species_name": self.adapter_for("b").species_name(e.b.species) if e.b and e.b.species else "",
                        "b_sprite_html": self._get_sprite_html(e.b.species, 0, "b") if e.b and e.b.species else "",
                        "b_level":    self._resolve_level("b", e.b),
                        # The wild mon a side met but did not catch (dead zones only).
                        "a_enc_species_name": self.adapter_for("a").species_name(e.encounter_a.species)
                                              if e.encounter_a and e.encounter_a.species else "",
                        "a_enc_level": e.encounter_a.level if e.encounter_a else 0,
                        "b_enc_species_name": self.adapter_for("b").species_name(e.encounter_b.species)
                                              if e.encounter_b and e.encounter_b.species else "",
                        "b_enc_level": e.encounter_b.level if e.encounter_b else 0,
                        "status":     e.status.value,
                    }
                    for e in s.links if e.killed_at
                ],
                key=lambda x: x["killed_at"],
                reverse=True,
            ),
            "run_over": s.run_over,
            "attempts_count": s.attempts_count,
            "bonus_keys": {
                pid: sorted(s.bonus_keys.get(pid, set()))
                for pid in ["a", "b"]
            },
            "pending_bonus": {
                pid: list(s.pending_bonus.get(pid, []))
                for pid in ["a", "b"]
            },
            # O-24: dead keys still alive in party whose force_faint repair budget is spent.
            "faint_repair_stalled": {
                pid: sorted(s.faint_repair_stalled.get(pid, {}))
                for pid in ["a", "b"]
            },
            # KEY-SCOPE-3: keys whose events are refused until an admin resolves the clash.
            "ambiguous_keys": {
                pid: dict(s.ambiguous_keys.get(pid, {}))
                for pid in ["a", "b"]
            },
            # An uncertain / conflicted native trade awaiting party evidence or a human (None = none).
            "trade_problem": s.trade_problem(),
            "trade_held": s.trade_held(),
            "trade_last": s.trade_last,
            "badge_slugs": self.adapter.gym_badge_slugs(s.rom_type or ""),
        }

    def _manager_url(self, request, path: str) -> str | None:
        """Where the Manager shows this page, when a Manager spawned this run. The Manager
        is THE UI; a run's own pages exist for the standalone `python -m server.server`
        case and are otherwise redirected there so nobody lands in the old chrome."""
        if not (self._manager_port and self._run_id):
            return None
        host = (request.host or "127.0.0.1").split(":")[0] or "127.0.0.1"
        return f"{request.scheme}://{host}:{self._manager_port}{path.replace('{id}', self._run_id)}"

    def _to_manager(self, request, path: str) -> None:
        url = self._manager_url(request, path)
        if url:
            raise aiohttp_web.HTTPFound(url)

    async def handle_status_html(self, request):
        self._to_manager(request, "/runs/{id}")
        return await self._handle_dashboard_template(request)

    async def _handle_dashboard_template(self, request):
        """The pair board. `server/board.py` joins the status payload into pair rows and
        zones and computes everything display-shaped; the template draws it."""
        from server.board import board_context

        ctx = board_context(self._build_status_dict(), run_name=self._run_name or "", poll_url="/")
        ctx.update({
            "page_title":   self._page_title(),
            "theme":        resolve_theme(request),
            "is_stream":    False,
            "hide_chrome":  False,
            "body_class":   "board mgr",
            "sidebar_html": self._build_sidebar_html(request, "run"),
            "tcp_port":     self._tcp_port,
            "game_label":   self._rom_label(),
            "show_calc":    bool(self._calc_profile()),
        })
        return aiohttp_jinja2.render_template("dashboard.html", request, ctx)

    async def handle_status_json(self, request):
        return aiohttp_web.json_response(self._build_status_dict())

    # ── RR Damage Calculator handlers ───────────────────────────────────────

    async def handle_calc_redirect(self, request):
        raise aiohttp_web.HTTPFound('/calc/normal.html')

    async def handle_calc_files(self, request):
        """Serve the calc: entry points wrapped in ``calc.html`` for the chrome, everything
        else verbatim (resolution in ``server.calc_files``, shared with the Manager)."""
        path = request.match_info.get('path', '')
        if path.endswith('.html'):
            # A managed run never renders the page itself: send it on before looking for
            # the build, so an unbuilt calc redirects like a built one.
            self._to_manager(request, "/runs/{id}/calc/" + path)
        abs_path = calc_files.resolve(path)
        if not path.endswith('.html'):
            return calc_files.file_response(abs_path)
        ctx = self._panel_ctx(request, panel="calc", label="Calc")
        ctx.update({
            "calc_body_html":  calc_files.page_body(abs_path),
            "calc_mode_label": calc_files.mode_label(path),
            "status_href":     "/",
        })
        resp = aiohttp_jinja2.render_template("panel_page.html", request, ctx)
        # The rendered theme depends on the `slink-theme` cookie; revalidate so a theme
        # change on another page is not masked by a heuristic-cached copy.
        resp.headers["Cache-Control"] = "no-cache"
        return resp

    async def handle_calc_mons(self, request):
        """Return live party + linked mons for both players as Showdown pastes."""
        d = self._build_status_dict()
        s = self.state
        result = {"calc": self._calc_profile()}
        for pid in ("a", "b"):
            adapter = self.adapter_for(pid)
            p = d["players"][pid]
            adapter = self.adapter_for(pid)
            party, linked = [], []
            # Party mons
            for key in p.get("party_keys", []):
                detail = p["party_details"].get(key, {})
                entry = _build_mon_entry(key, detail, adapter)
                if entry:
                    entry["loc"] = "party"
                    entry["hp_pct"] = (
                        max(0, min(100, int(detail.get("hp", 0)
                                           / max(detail.get("maxHP", 1), 1) * 100))))
                    party.append(entry)
            # Linked alive mons not already in party
            party_key_set = set(p.get("party_keys", []))
            for lnk in s.links:
                if lnk.status.value != "alive":
                    continue
                mi = lnk.a if pid == "a" else lnk.b
                if not mi or not mi.key or mi.key in party_key_set:
                    continue
                stats = s.mon_stats[pid].get(mi.key, {})
                detail = {
                    "species_id":   mi.species,
                    "level":        mi.level or stats.get("level", 0),
                    "nickname":     mi.nickname or "",
                    "hp":           0,
                    "maxHP":        0,
                    "held_item_id": 0,
                    "ability_id":   0,
                    "ability_name": "",
                    "moves":        stats.get("moves", []),
                }
                entry = _build_mon_entry(mi.key, detail, adapter)
                if entry:
                    entry["loc"] = "box"
                    linked.append(entry)
            bs = p.get("battle_state", {})
            enemy = []
            if bs.get("in_battle"):
                is_trainer = bs.get("is_trainer_battle", False)
                opp_name  = bs.get("opponent_name", "")
                opp_class = bs.get("opponent_class", "")
                trainer_label = " ".join(filter(None, [opp_class, opp_name])) if is_trainer else "Wild"
                tid = bs.get("trainer_id") or 0
                for ei, em in enumerate(bs.get("enemy_party", [])):
                    if not em.get("species_id", 0):
                        continue
                    entry = _build_mon_entry(f"foe-{ei}", _foe_detail(em, adapter), adapter)
                    if entry:
                        entry["loc"]    = "enemy"
                        entry["active"] = em.get("active", False)
                        entry["hp_pct"] = (
                            max(0, min(100, int(em.get("hp", 0) / em["maxHP"] * 100)))
                            if em.get("maxHP") else None)
                        entry["trainer_label"] = trainer_label
                        enemy.append(entry)
                calc_label = _calc_trainer_label(
                    adapter.trainer_brief(tid) if (is_trainer and tid) else None, enemy)
                for entry in enemy if calc_label else ():
                    entry["trainer_label"] = calc_label
            result[pid] = {
                "trainer_name": p.get("trainer_name", pid.upper()),
                "party":  party,
                "linked": linked,
                "enemy":  enemy,
            }
        return aiohttp_web.json_response(result)

    async def handle_timeline_html(self, request):
        """GET /timeline — the run's story in order (route name: docs/public_ui/DECISIONS.md
        #1). Built from the status payload by server.board.timeline; a Manager run's lives at
        /runs/{id}/timeline."""
        from server.board import timeline
        self._to_manager(request, "/runs/{id}/timeline")
        d = self._build_status_dict()
        ctx = self._panel_ctx(request, panel="timeline", label="Timeline")
        ctx.update({"story": timeline(d), "players": d.get("players") or {}})
        return aiohttp_jinja2.render_template("panel_page.html", request, ctx)

    # ── Stream overlay handlers ──────────────────────────────────────────────

    async def handle_stream_index(self, request):
        self._to_manager(request, "/broadcast")
        ctx = _build_stream_index_context(request)
        ctx.update({
            "sidebar_html": self._build_sidebar_html(request, "broadcast"),
            "body_class": "board mgr", "mgr": True,
            "active_run_name": self._run_name or "",
            "tabs": [("Overlays", "/stream", True), ("Twitch", "/twitch", False), ("OBS", "/obs", False)],
        })
        return aiohttp_jinja2.render_template("stream_index.html", request, ctx)

    # ── Templated party overlay ────────────────────────────────

    def _build_party_overlay_context(self, player_id: str) -> dict:
        """Slice and reshape `_build_status_dict()` into the template context
        the party overlay expects. One full status-dict build per HTMX poll
        (every 2 s) — acceptable at SLink's localhost-only scale.
        """
        d = self._build_status_dict()
        p = d["players"].get(player_id, {})
        keys = p.get("party_keys", [])
        details = p.get("party_details", {})

        mons = []
        for key in keys:
            det = details.get(key) or {}
            hp = det.get("hp", 0) or 0
            max_hp = det.get("maxHP", 0) or 0
            fainted = (hp == 0)
            if fainted or max_hp <= 0:
                tone = "fnt" if fainted else "bl"
            else:
                pct = (hp / max_hp) * 100
                if pct > 50:
                    tone = "bh"
                elif pct > 20:
                    tone = "bm"
                else:
                    tone = "bl"
            mons.append({
                "key":          key,
                "species_id":   det.get("species_id", 0),
                "species_name": det.get("species_name", ""),
                "nickname":     det.get("nickname", "") or det.get("species_name", "") or key[:8],
                "level":        det.get("level", 0),
                "hp":           hp,
                "maxHP":        max_hp,
                "sprite_html":  det.get("sprite_html", ""),
                "status_tone":  tone,
                "fainted":      fainted,
                "status_cond":  det.get("status_cond", 0),
                "stat_stages":  det.get("stat_stages", []) if det.get("active") else None,
                "active":       det.get("active", False),
            })

        return {
            "player_id":    player_id,
            "trainer_name": p.get("trainer_name", ""),
            "mons":         mons,
            # A dropped client keeps its last party on screen, tagged, not passed off as live.
            "connected":    bool(p.get("connected")),
        }

    # ── Battle overlay dispatch ──────────────────────────

    @staticmethod
    def _apply_battle_body_class(ctx: dict, template_base: str, request) -> None:
        """Focus + enemy-focus need `body.ov-focus` for their shell scaling
        rules to apply. The CSS extras are stitched onto `layout_class` so
        the existing _render_stream_overlay plumbing stays untouched."""
        if template_base not in ("focus", "enemy_focus"):
            return
        existing = resolve_layout(request)
        ctx["layout_class"] = (existing + " ov-focus").strip()

    def _build_battle_overlay_context(self, player_id: str, template_base: str) -> dict:
        """Unified context builder for enemy_focus / enemy_trainer / focus.
        Dispatches on template_base to assemble the right slice."""
        d = self._build_status_dict()
        p  = d.get("players", {}).get(player_id, {}) or {}
        connected = bool(p.get("connected"))
        if not connected:
            p = {}  # a dropped client's last battle is not live: hide the panels
        bs = p.get("battle_state", {}) or {}
        ctx = {"player_id": player_id, "connected": connected, "in_battle": bool(bs.get("in_battle"))}

        if template_base == "enemy_focus":
            return {**ctx, **self._enemy_focus_ctx(bs)}
        if template_base == "enemy_trainer":
            return {**ctx, **self._enemy_trainer_ctx(bs)}
        if template_base == "focus":
            return {**ctx, **self._focus_ctx(p, bs)}
        return ctx

    def _enemy_focus_ctx(self, bs: dict) -> dict:
        if not bs.get("in_battle"):
            return {"active_mons": [], "is_doubles": False, "is_trainer": False, "title": ""}
        enemy_party = bs.get("enemy_party", []) or []
        active = [m for m in enemy_party if m.get("active")]
        if not active and enemy_party:
            active = [enemy_party[0]]
        is_doubles = bool(bs.get("is_doubles")) or len(active) > 1
        is_trainer = bool(bs.get("is_trainer_battle"))
        if is_trainer:
            title = (bs.get("opponent_class") or "TRAINER")
            if bs.get("opponent_name"):
                title += " " + bs["opponent_name"]
        else:
            title = "WILD ENCOUNTER"
        return {
            "active_mons": [self._battle_slot(m) for m in active],
            "is_doubles": is_doubles,
            "is_trainer": is_trainer,
            "title": title,
        }

    def _enemy_trainer_ctx(self, bs: dict) -> dict:
        # is_trainer gates the template: a wild battle keeps in_battle True, and without this
        # the overlay sat on "TRAINER / Loading..." for the whole encounter.
        if not bs.get("in_battle") or not bs.get("is_trainer_battle"):
            return {"mons": [], "trainer_label": "", "is_trainer": False}
        team = bs.get("enemy_party", []) or []
        label = (bs.get("opponent_class") or "Trainer")
        if bs.get("opponent_name"):
            label += " " + bs["opponent_name"]
        return {
            "mons": [self._battle_mon_card(m) for m in team],
            "trainer_label": label,
            "is_trainer": True,
        }

    def _focus_ctx(self, p: dict, bs: dict) -> dict:
        keys = p.get("party_keys", []) or []
        details = p.get("party_details", {}) or {}
        active_slots = []
        for k in keys:
            det = details.get(k) or {}
            if not det.get("active"):
                continue
            active_slots.append(self._battle_slot(det))
        is_doubles = bool(bs.get("is_doubles")) or len(active_slots) > 1
        return {
            "active_mons": active_slots,
            "is_doubles":  is_doubles,
        }

    def _battle_slot(self, det: dict) -> dict:
        """Compose a {mon, moves} dict the focus/enemy-focus templates expect."""
        return {
            "mon":   self._battle_mon_card(det),
            "moves": [self._battle_move(md) for md in det.get("move_details", []) or []],
        }

    def _battle_mon_card(self, det: dict) -> dict:
        """Project a party_details / enemy_party entry into the shape mon_card expects."""
        hp     = det.get("hp", 0) or 0
        max_hp = det.get("maxHP", 0) or 0
        fnt    = hp == 0
        pct    = self._hp_pct(hp, max_hp)
        if fnt:
            tone = "fnt"
        elif pct > 50:
            tone = "bh"
        elif pct > 20:
            tone = "bm"
        else:
            tone = "bl"
        return {
            "species_id":   det.get("species_id", 0),
            "species_name": det.get("species_name", ""),
            "nickname":     det.get("nickname") or det.get("species_name") or "???",
            "level":        det.get("level", 0),
            "hp":           hp,
            "maxHP":        max_hp,
            "sprite_html":  det.get("sprite_html", ""),
            "status_tone":  tone,
            "fainted":      fnt,
            "status_cond":  det.get("status_cond", 0),
            "stat_stages":  det.get("stat_stages", []) if det.get("active") else None,
            "active":       bool(det.get("active")),
        }

    def _battle_move(self, md: dict) -> dict:
        """Project a move detail entry into the shape the moves-grid expects."""
        max_pp = md.get("pp", 0) or 0
        cur_pp = md.get("current_pp", 0) or 0
        pct    = max(0, min(100, round(cur_pp / max_pp * 100))) if max_pp else 0
        if pct > 50:
            cls = "pp-h"
        elif pct > 25:
            cls = "pp-m"
        else:
            cls = "pp-l"
        return {
            "name":       md.get("name") or "?",
            "type_name":  md.get("type_name") or "",
            "current_pp": cur_pp,
            "pp_max":     max_pp,
            "pp_cls":     cls,
        }

    # ── Links overlay context builders ───────────────────

    @staticmethod
    def _hp_class(pct: int) -> str:
        return "hp-h" if pct > 50 else ("hp-m" if pct > 20 else "hp-l")

    @staticmethod
    def _hp_pct(hp: int, max_hp: int) -> int:
        if max_hp <= 0 or hp <= 0:
            return 0
        return max(0, min(100, round(hp / max_hp * 100)))

    def _build_links_overlay_context(self) -> dict:
        """Alive + dead link cards for /stream/links."""
        d = self._build_status_dict()
        alive, dead = [], []
        for lnk in d.get("links", []):
            item = {
                "area_display": lnk.get("area_display") or "",
                "a": {
                    "nickname":     lnk.get("a_nickname") or "",
                    "species_name": lnk.get("a_species_name") or "",
                    "sprite_html":  lnk.get("a_sprite_html") or "",
                },
                "b": {
                    "nickname":     lnk.get("b_nickname") or "",
                    "species_name": lnk.get("b_species_name") or "",
                    "sprite_html":  lnk.get("b_sprite_html") or "",
                },
                "killed_at": lnk.get("killed_at") or "",
            }
            (alive if lnk.get("status") == "alive" else dead).append(item)
        # .lk-list is overflow:hidden and OBS cannot scroll, so a long dead list clipped
        # silently. Show the newest few and say how many more there are.
        dead_shown = sorted(dead, key=lambda x: x["killed_at"], reverse=True)[:self._LINKS_DEAD_SHOWN]
        return {"alive": alive, "dead": dead, "dead_shown": dead_shown,
                "dead_more": len(dead) - len(dead_shown)}

    _LINKS_DEAD_SHOWN = 5  # rows that fit under the alive cards at the catalog's sizes

    def _build_linked_party_overlay_context(self) -> dict:
        """Linked pairs where BOTH mons are currently in party — full
        sprite + HP card layout for /stream/linked-party."""
        d = self._build_status_dict()
        pa = d.get("players", {}).get("a", {}) or {}
        pb = d.get("players", {}).get("b", {}) or {}
        a_keys = set(pa.get("party_keys", []))
        b_keys = set(pb.get("party_keys", []))
        a_det  = pa.get("party_details", {}) or {}
        b_det  = pb.get("party_details", {}) or {}

        pairs = []
        for lnk in d.get("links", []):
            if lnk.get("status") != "alive":
                continue
            a_key = lnk.get("a_key")
            b_key = lnk.get("b_key")
            if a_key not in a_keys or b_key not in b_keys:
                continue
            ad = a_det.get(a_key) or {}
            bd = b_det.get(b_key) or {}
            a_hp, a_mx = ad.get("hp", 0) or 0, ad.get("maxHP", 0) or 0
            b_hp, b_mx = bd.get("hp", 0) or 0, bd.get("maxHP", 0) or 0
            a_pct = self._hp_pct(a_hp, a_mx)
            b_pct = self._hp_pct(b_hp, b_mx)
            a_fnt = a_hp == 0
            b_fnt = b_hp == 0
            pairs.append({
                "area_display": lnk.get("area_display") or "",
                "both_fainted": a_fnt and b_fnt,
                "a": {
                    "nickname":     ad.get("nickname") or lnk.get("a_nickname") or "",
                    "species_name": lnk.get("a_species_name") or "",
                    "sprite_html":  ad.get("sprite_html") or lnk.get("a_sprite_html") or "",
                    "level":        ad.get("level") or lnk.get("a_level") or 0,
                    "hp": a_hp, "max_hp": a_mx, "pct": a_pct,
                    "hp_cls": self._hp_class(a_pct), "fainted": a_fnt,
                },
                "b": {
                    "nickname":     bd.get("nickname") or lnk.get("b_nickname") or "",
                    "species_name": lnk.get("b_species_name") or "",
                    "sprite_html":  bd.get("sprite_html") or lnk.get("b_sprite_html") or "",
                    "level":        bd.get("level") or lnk.get("b_level") or 0,
                    "hp": b_hp, "max_hp": b_mx, "pct": b_pct,
                    "hp_cls": self._hp_class(b_pct), "fainted": b_fnt,
                },
            })
        return {"pairs": pairs}

    def _build_boxed_links_overlay_context(self) -> dict:
        """Alive pairs where at least one mon is in the box (not party)."""
        d = self._build_status_dict()
        pa = d.get("players", {}).get("a", {}) or {}
        pb = d.get("players", {}).get("b", {}) or {}
        a_keys = set(pa.get("party_keys", []))
        b_keys = set(pb.get("party_keys", []))

        pairs = []
        for lnk in d.get("links", []):
            if lnk.get("status") != "alive":
                continue
            a_in = lnk.get("a_key") in a_keys
            b_in = lnk.get("b_key") in b_keys
            if a_in and b_in:
                continue  # both in party → belongs to linked-party overlay
            pairs.append({
                "area_display": lnk.get("area_display") or "",
                "a": {
                    "nickname":     lnk.get("a_nickname") or "",
                    "species_name": lnk.get("a_species_name") or "",
                    "sprite_html":  lnk.get("a_sprite_html") or "",
                    "level":        lnk.get("a_level") or 0,
                    "in_party":     a_in,
                },
                "b": {
                    "nickname":     lnk.get("b_nickname") or "",
                    "species_name": lnk.get("b_species_name") or "",
                    "sprite_html":  lnk.get("b_sprite_html") or "",
                    "level":        lnk.get("b_level") or 0,
                    "in_party":     b_in,
                },
            })
        return {"pairs": pairs}

    # ── Stream overlays: one spec per slug, one handler ─────────────
    # (template base, context factory). The factory gets (self, request) and returns the
    # template context; the battle overlays also stitch their body class on. Titles come from
    # overlay_catalog so the launcher and the page agree. Routes are registered per slug
    # (not as /stream/{slug}) so the router lists every overlay and test_routes_smoke walks
    # all 48 of them.
    STREAM_OVERLAYS: dict[str, tuple[str, object]] = {
        "party-a":         ("party",          lambda self, r: self._build_party_overlay_context("a")),
        "party-b":         ("party",          lambda self, r: self._build_party_overlay_context("b")),
        "enemy-focus-a":   ("enemy_focus",    lambda self, r: self._battle_ctx(r, "enemy_focus", "a")),
        "enemy-focus-b":   ("enemy_focus",    lambda self, r: self._battle_ctx(r, "enemy_focus", "b")),
        "enemy-trainer-a": ("enemy_trainer",  lambda self, r: self._battle_ctx(r, "enemy_trainer", "a")),
        "enemy-trainer-b": ("enemy_trainer",  lambda self, r: self._battle_ctx(r, "enemy_trainer", "b")),
        "focus-a":         ("focus",          lambda self, r: self._battle_ctx(r, "focus", "a")),
        "focus-b":         ("focus",          lambda self, r: self._battle_ctx(r, "focus", "b")),
        "links":           ("links",          lambda self, r: self._build_links_overlay_context()),
        "linked-party":    ("linked_party",   lambda self, r: self._build_linked_party_overlay_context()),
        "boxed-links":     ("boxed_links",    lambda self, r: self._build_boxed_links_overlay_context()),
        "deaths":          ("deaths",         lambda self, r: self._build_deaths_overlay_context()),
        "attempts":        ("attempts",       lambda self, r: self._build_attempts_overlay_context()),
        "areas":           ("areas",          lambda self, r: self._build_areas_overlay_context()),
        "events":          ("events",         lambda self, r: self._build_events_overlay_context(r)),
        "badges-a":        ("badges",         lambda self, r: self._build_badges_overlay_context("a")),
        "badges-b":        ("badges",         lambda self, r: self._build_badges_overlay_context("b")),
        "encounters":      ("encounters",     lambda self, r: self._build_encounters_overlay_context()),
        "stream-memorial": ("stream_memorial", lambda self, r: self._build_memorial_scroll_context()),
        "ticker":          ("ticker",         lambda self, r: self._build_ticker_overlay_context(r)),
        "area-encounter":  ("area_encounter", lambda self, r: self._build_area_encounter_overlay_context()),
        "enc-table-a":     ("enc_table",      lambda self, r: self._build_enc_table_overlay_context("a")),
        "enc-table-b":     ("enc_table",      lambda self, r: self._build_enc_table_overlay_context("b")),
    }

    def _battle_ctx(self, request, template_base: str, player_id: str) -> dict:
        ctx = self._build_battle_overlay_context(player_id, template_base)
        self._apply_battle_body_class(ctx, template_base, request)
        return ctx

    async def handle_stream_overlay(self, request, slug: str, fragment: bool = False):
        """GET /stream/{slug} (the page) and /stream/{slug}/fragment (the #root subtree the
        page polls every 2 s and swaps in place by idiomorph)."""
        from server.overlay_catalog import OVERLAYS
        template_base, factory = self.STREAM_OVERLAYS[slug]
        ctx = factory(self, request)
        title = next((o["title"] for o in OVERLAYS if o.get("slug") == slug), None)
        if title:
            ctx.setdefault("overlay_title", title)
        return await self._render_stream_overlay(request, slug, ctx, template_base=template_base,
                                                 fragment=fragment)

    # ── Shared template plumbing for stream overlays ─────────────

    async def _render_stream_overlay(self, request, slug: str, ctx: dict, *,
                                     fragment: bool = False, template_base: str | None = None):
        """Render `stream/{base}.html` (or `stream/_{base}_root.html` for the
        HTMX fragment endpoint). Shared by every stream overlay so the
        theme + layout + fragment-url plumbing lives in exactly one place.

        ``template_base`` lets two-player overlays (e.g. /stream/focus-a +
        /stream/focus-b) share one template — pass ``template_base='focus'``
        for both and the per-side context tells the template which player.
        """
        ctx = dict(ctx)
        ctx.setdefault("overlay_title", slug.replace("-", " ").title())
        ctx["theme"]        = resolve_theme(request)
        # `setdefault` (not `=`) so handlers that already stitched extras onto
        # layout_class — e.g. `_apply_battle_body_class` adding `ov-focus` to
        # focus / enemy-focus — survive this plumbing. Otherwise body.ov-focus
        # is silently dropped and the doubles flex-row split layout never
        # activates, leaving both columns stacked vertically.
        ctx.setdefault("layout_class", resolve_layout(request))
        query = request.url.query_string
        ctx["fragment_url"] = f"/stream/{slug}/fragment" + (f"?{query}" if query else "")
        base = (template_base or slug).replace("-", "_")
        template = f"stream/_{base}_root.html" if fragment else f"stream/{base}.html"
        return aiohttp_jinja2.render_template(template, request, ctx)

    def _build_deaths_overlay_context(self) -> dict:
        """Alive vs dead link counts for the SOUL LINK overlay."""
        alive = dead = 0
        for lnk in self.state.links:
            status = lnk.status.value
            if status == "alive":
                alive += 1
            elif status in ("dead", "memorial"):
                dead += 1
        return {"alive_count": alive, "dead_count": dead}

    def _build_attempts_overlay_context(self) -> dict:
        return {"attempts_count": self.state.attempts_count}

    @_requires_loaded_state
    async def handle_api_attempts(self, request):
        """POST /api/attempts — set the manual attempts counter."""
        try:
            body = await request.json()
        except Exception:
            return aiohttp_web.json_response({"ok": False, "error": "Invalid JSON"}, status=400)
        count = body.get("count")
        if count is None or not isinstance(count, int) or count < 0:
            return aiohttp_web.json_response(
                {"ok": False, "error": "count must be a non-negative integer"}, status=400)
        self.state.attempts_count = count
        self.state._save()
        self._notify_sse()
        return aiohttp_web.json_response({"ok": True, "attempts_count": count})

    # ── Context builders for the remaining overlays ──

    def _build_areas_overlay_context(self) -> dict:
        linked = dead = pending = 0
        for st in self.state.area_states.values():
            sv = st.value
            if sv == "linked":
                linked += 1
            elif sv == "dead_zone":
                dead += 1
            elif sv.startswith("pending"):
                pending += 1
        return {"linked": linked, "dead": dead, "pending": pending}

    def _build_badges_overlay_context(self, player_id: str) -> dict:
        d = self._build_status_dict()
        p = d.get("players", {}).get(player_id, {}) or {}
        slugs = d.get("badge_slugs", []) or []
        primary = p.get("badges", 0) or 0
        kanto = p.get("kanto_badges", 0) or 0
        trainer_name = p.get("trainer_name") or f"Player {player_id.upper()}"
        badges = []
        for i, pair in enumerate(slugs):
            earned = bool((primary >> i) & 1) if i < 8 else bool((kanto >> (i - 8)) & 1)
            badges.append({
                "slug": pair[0] if isinstance(pair, (list, tuple)) else str(pair),
                "name": pair[1] if isinstance(pair, (list, tuple)) and len(pair) > 1 else "",
                "earned": earned,
            })
        return {
            "player_id":    player_id,
            "trainer_name": trainer_name,
            "badges":       badges,
            "connected":    bool(p.get("connected")),
        }

    def _build_encounters_overlay_context(self) -> dict:
        d = self._build_status_dict()
        links = d.get("links", []) or []
        linked = sum(1 for lk in links if lk.get("status") == "alive")
        dead   = sum(1 for lk in links if lk.get("status") != "alive")
        shinies = sum(1 for lk in links if lk.get("a_shiny") or lk.get("b_shiny"))
        bonus = d.get("bonus_keys", {}) or {}
        shinies += len(bonus.get("a", []) or []) + len(bonus.get("b", []) or [])
        last = links[-1] if links else None
        last_ctx = None
        if last:
            last_ctx = {
                "area_display": last.get("area_display") or "",
                "a": {
                    "nickname":     last.get("a_nickname") or "",
                    "species_name": last.get("a_species_name") or "",
                    "level":        last.get("a_level") or 0,
                    "sprite_html":  last.get("a_sprite_html") or "",
                    "shiny":        bool(last.get("a_shiny")),
                },
                "b": {
                    "nickname":     last.get("b_nickname") or "",
                    "species_name": last.get("b_species_name") or "",
                    "level":        last.get("b_level") or 0,
                    "sprite_html":  last.get("b_sprite_html") or "",
                    "shiny":        bool(last.get("b_shiny")),
                },
            }
        return {
            "linked":  linked + dead,
            "dead":    dead,
            "shinies": shinies,
            "last":    last_ctx,
        }

    def _build_memorial_scroll_context(self) -> dict:
        """Killfeed for the streaming memorial scroll, oldest first."""
        d = self._build_status_dict()
        kf = sorted(d.get("killfeed", []) or [], key=lambda x: x.get("killed_at") or "")
        entries = [{
            "area_display": k.get("area_display") or "",
            "a": {
                "nickname":     k.get("a_nickname") or "",
                "species_name": k.get("a_species_name") or "",
                "sprite_html":  k.get("a_sprite_html") or "",
            },
            "b": {
                "nickname":     k.get("b_nickname") or "",
                "species_name": k.get("b_species_name") or "",
                "sprite_html":  k.get("b_sprite_html") or "",
            },
        } for k in kf]
        return {"entries": entries}

    _EVENT_TYPE_CLASSES = {
        "capture":      "ec", "faint":        "ef", "whiteout":   "ew",
        "no_catch":     "en", "area_enter":   "ea", "linked":     "el",
        "dead_zone":    "ed", "violation":    "ev", "key_change": "ek",
        "force_faint":  "ef", "hello":        "eh", "shiny":      "es",
        "force_explode": "ef",
        "memorialize":  "em", "party_to_box": "ep", "box_to_party": "ep",
        "reroll":       "er",
    }

    @staticmethod
    def _format_event_ts(raw) -> str:
        if not raw:
            return ""
        try:
            dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00")).astimezone()
        except (ValueError, TypeError):
            s = str(raw)
            return s[11:16] if len(s) >= 16 else s
        h12 = dt.hour % 12 or 12
        return f"{h12}:{dt.minute:02d}{'p' if dt.hour >= 12 else 'a'}"

    def _build_events_overlay_context(self, request) -> dict:
        return self._build_event_feed_context(request, top_n=16, list_mode="events")

    def _build_ticker_overlay_context(self, request) -> dict:
        return self._build_event_feed_context(request, top_n=16, list_mode="ticker")

    def _build_event_feed_context(self, request, top_n: int, list_mode: str) -> dict:
        d = self._build_status_dict()
        events = (d.get("recent_events", []) or [])[:top_n]
        # ?filter=type1,type2 picks the types; ?filter=all shows everything. No param means
        # the catalog's default-on set, so party_to_box / box_to_party stay off a bare URL.
        from server.overlay_catalog import EVENT_FILTERS_DEFAULT_ON
        flt = request.query.get("filter", "").strip()
        if flt != "all":
            allow = {t for t in flt.split(",") if t} if flt else set(EVENT_FILTERS_DEFAULT_ON)
            events = [e for e in events if e.get("type") in allow]
        pa = d.get("players", {}).get("a", {}) or {}
        pb = d.get("players", {}).get("b", {}) or {}
        name_a = pa.get("trainer_name") or "A"
        name_b = pb.get("trainer_name") or "B"
        rows = []
        for ev in events:
            rows.append({
                "ts":   self._format_event_ts(ev.get("ts")),
                "who":  name_a if ev.get("player") == "a" else name_b,
                "type": ev.get("type") or "",
                "text": ev.get("text") or ev.get("type") or "",
                "cls":  self._EVENT_TYPE_CLASSES.get(ev.get("type"), ""),
            })
        key = "pills" if list_mode == "ticker" else "events"
        return {key: rows}

    def _build_area_encounter_overlay_context(self) -> dict:
        """Current most-active area: linked / dead-zone / pending."""
        d = self._build_status_dict()
        # Pick the area both players are in, or the first pending area.
        area_a = self.player_area_id.get("a") or self.player_area.get("a") or ""
        area_b = self.player_area_id.get("b") or self.player_area.get("b") or ""
        focus  = area_a if area_a == area_b else (area_a or area_b)
        if not focus:
            return {
                "area_display": "", "status_tone": "dim", "status_label": "",
                "side_a": {"sprite_html": "", "name": "", "level": 0, "tag": "", "tag_cls": ""},
                "side_b": {"sprite_html": "", "name": "", "level": 0, "tag": "", "tag_cls": ""},
            }
        state = d.get("area_states", {}).get(focus, "unseen")
        # Find a link for this area
        linked_entry = None
        for lk in d.get("links", []) or []:
            if lk.get("area_id") == focus:
                linked_entry = lk
                break
        pending = d.get("pending_captures", {}).get(focus, {}) or {}

        def side(slot_id):
            if linked_entry:
                tag_cls = ("ae-tag-dead" if linked_entry.get("status") in ("dead", "memorial")
                           else "ae-tag-caught")
                tag = "DEAD" if linked_entry.get("status") in ("dead", "memorial") else "CAUGHT"
                return {
                    "sprite_html": linked_entry.get(f"{slot_id}_sprite_html") or "",
                    "name":        linked_entry.get(f"{slot_id}_nickname")
                                   or linked_entry.get(f"{slot_id}_species_name") or "",
                    "level":       linked_entry.get(f"{slot_id}_level") or 0,
                    "tag":         tag,
                    "tag_cls":     tag_cls,
                }
            p = pending.get(slot_id) or {}
            return {
                "sprite_html": "",
                "name":        p.get("nickname") or p.get("species_name") or "",
                "level":       p.get("level") or 0,
                "tag":         "WAITING" if p else "",
                "tag_cls":     "ae-tag-waiting",
            }

        if state == "linked":
            tone, label = "alive", "LINKED"
        elif state == "dead_zone":
            tone, label = "dead", "DEAD ZONE"
        elif state.startswith("pending"):
            tone, label = "pend", "PENDING"
        else:
            tone, label = "dim", "OPEN"
        return {
            "area_display": self._area_display(focus),
            "status_tone":  tone,
            "status_label": label,
            "side_a":       side("a"),
            "side_b":       side("b"),
        }

    def _build_enc_table_overlay_context(self, player_id: str) -> dict:
        """Wild encounter rates for the player's current area, sourced
        via _enc_table_for_status.

        Source shape from `_enc_table_for_status` is `{method: [entry, ...]}`
        directly (no `"methods"` wrapper); each entry carries `name`,
        `species_id`, `rate`, `min_level`, `max_level`. The `level_range`
        display string is derived here ("lvX" when min == max, else "lvX–Y")
        to match the pre-HTMX renderer's output. """
        area_id = self.player_area_id.get(player_id) or self.player_area.get(player_id) or ""
        raw = self._enc_table_for_status(area_id, player_id) or {}
        methods = []
        for method_name, entries in raw.items():
            mlist = []
            for e in entries:
                sid = e.get("species_id", 0)
                lo, hi = e.get("min_level", 0), e.get("max_level", 0)
                lv_range = f"lv{lo}" if lo == hi else f"lv{lo}–{hi}"
                mlist.append({
                    "species_id":   sid,
                    "species_name": e.get("name") or e.get("species_name") or "?",
                    "rate":         e.get("rate", 0),
                    "level_range":  lv_range if lo or hi else "",
                    "sprite_html":  self._get_sprite_html(sid, 0, player_id) if sid else "",
                })
            if mlist:
                methods.append({"name": method_name, "entries": mlist})
        return {
            "player_id":    player_id,
            "area_display": self._area_display(area_id) if area_id else "",
            "methods":      methods,
        }

    # ── Launcher script download ─────────────────────────────────────────────

    _LAUNCHER_TEMPLATE = (
        '-- Auto-generated by SLink - {run_name}\n'
        '-- Player {player_upper}: load this script in BizHawk Lua Console\n'
        '-- This file can be loaded from any location (Desktop, Downloads, etc.)\n'
        '--\n'
        '-- Override: set SLINK_ROOT to skip auto-detection entirely:\n'
        'local SLINK_ROOT = nil  -- e.g. "C:/SLink/"\n'
        '\n'
        'SLINK_HOST   = {host}\n'
        'SLINK_PORT   = {tcp_port}\n'
        'SLINK_PLAYER = {player}\n'
        '\n'
        '-- Config file lives next to this launcher and caches the project root path.\n'
        'local _launcher_dir = ((debug.getinfo(1, "S") or {{}}).source or ""):match("@(.+[\\\\/])") or ""\n'
        'local _cfg_path = _launcher_dir .. "slink_path.cfg"\n'
        '\n'
        'local function _valid_root(path)\n'
        '    if not path or path == "" or path == "nil" then return false end\n'
        '    local f = io.open(path .. "lua/slink.lua", "r")\n'
        '    if f then f:close(); return true end\n'
        '    return false\n'
        'end\n'
        '\n'
        '-- 1. Load cached path from config file\n'
        'if not SLINK_ROOT then\n'
        '    local f = io.open(_cfg_path, "r")\n'
        '    if f then\n'
        '        local cached = f:read("*l"); f:close()\n'
        '        if _valid_root(cached) then SLINK_ROOT = cached end\n'
        '    end\n'
        'end\n'
        '\n'
        '-- 2. Auto-detect: search from this script\'s directory upward for lua/slink.lua\n'
        'if not SLINK_ROOT then\n'
        '    local dir = _launcher_dir\n'
        '    for _, rel in ipairs({{"", "../", "../../", "../../../"}}) do\n'
        '        if _valid_root(dir .. rel) then SLINK_ROOT = dir .. rel; break end\n'
        '    end\n'
        'end\n'
        '\n'
        '-- 3. Fallback: show folder picker\n'
        'if not SLINK_ROOT then\n'
        '    luanet.load_assembly("System.Windows.Forms")\n'
        '    local FBD = luanet.import_type("System.Windows.Forms.FolderBrowserDialog")\n'
        '    local DR = luanet.import_type("System.Windows.Forms.DialogResult")\n'
        '    local dlg = FBD()\n'
        '    dlg.Description = "Select the SLink project folder (the folder that contains lua/ and server/)"\n'
        '    dlg.ShowNewFolderButton = false\n'
        '    local result = dlg:ShowDialog()\n'
        '    if result == DR.OK then\n'
        '        local path = tostring(dlg.SelectedPath)\n'
        '        if path and path ~= "" then\n'
        '            SLINK_ROOT = path:gsub("\\\\", "/") .. "/"\n'
        '        end\n'
        '    end\n'
        'end\n'
        '\n'
        'if not SLINK_ROOT then\n'
        '    error("[SLink] No project folder selected — cannot start.", 2)\n'
        'end\n'
        '\n'
        '-- Save path for next run\n'
        'local f = io.open(_cfg_path, "w")\n'
        'if f then f:write(SLINK_ROOT); f:close() end\n'
        '\n'
        'dofile(SLINK_ROOT .. "lua/slink.lua")\n'
    )

    async def handle_launcher(self, request):
        """GET /launcher/{player} — serve a per-player launcher .lua file."""
        player = request.match_info["player"]
        if player not in ("a", "b"):
            return aiohttp_web.Response(text="player must be 'a' or 'b'", status=400)
        host_header = request.host or "127.0.0.1"
        connect_host = host_header.split(":")[0] or "127.0.0.1"
        run_name = self._run_name or self._run_id or "SLink"
        content = self._LAUNCHER_TEMPLATE.format(
            run_name=lua_comment(run_name),
            player_upper=player.upper(),
            host=lua_string(connect_host),
            tcp_port=self._tcp_port,
            player=lua_string(player),
        )
        safe_name = re.sub(r'[^\w-]', '_', run_name).strip('_') or "SLink"
        filename = f"slink_{safe_name}_{player}.lua"
        return aiohttp_web.Response(
            text=content,
            content_type="application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    # ── Twitch bot management page ───────────────────────────────────────────


    async def handle_twitch_page(self, request):
        return self._panel_page(request, "twitch")

    def _panel_page(self, request, name: str):
        """/twitch, /obs and /debug: the shared panel (templates/_{name}_panel.html) in this
        run's own rail. The Manager renders the same panels at /broadcast/{name} and
        /runs/{id}/debug, and a Manager-spawned run sends the browser there."""
        self._to_manager(request, {"twitch": "/broadcast/twitch", "obs": "/broadcast/obs",
                                   "debug": "/runs/{id}/debug"}[name])
        if name == "debug":
            ctx = self._panel_ctx(request, panel="debug", label="Debug")
        else:
            ctx = self._panel_ctx(
                request, panel=name, label={"twitch": "Twitch bot", "obs": "OBS triggers"}[name],
                title="Broadcast",
                tabs=[("Overlays", "/stream", False), ("Twitch", "/twitch", name == "twitch"),
                      ("OBS", "/obs", name == "obs")])
        return aiohttp_jinja2.render_template("panel_page.html", request, ctx)

    # ── OBS integration page & API ────────────────────────────────────────────


    async def handle_obs_page(self, request):
        return self._panel_page(request, "obs")

    async def handle_obs_status(self, request):
        """GET /api/obs/status — connection status + config (passwords omitted)."""
        status = self.obs.get_status()
        # Include triggers (no passwords in triggers)
        status["triggers"] = self.obs._config.get("triggers", [])
        return aiohttp_web.json_response(status)

    async def handle_obs_config(self, request):
        """POST /api/obs/config — save config and hot-reload connections."""
        try:
            body = await request.json()
        except Exception:
            return aiohttp_web.json_response({"ok": False, "error": "Invalid JSON"}, status=400)
        new_cfg = {
            "enabled": bool(body.get("enabled", False)),
            "connections": {},
            "triggers": body.get("triggers", []),
        }
        for pid in ("a", "b"):
            conn_in = body.get("connections", {}).get(pid, {})
            existing = self.obs._config.get("connections", {}).get(pid, {})
            host = str(conn_in.get("host", ""))
            port = int(conn_in.get("port", 4455))
            # Empty password = keep the saved one, but only for the same host:port;
            # otherwise a page could relay the saved password to a host of its choosing.
            same_target = (host == str(existing.get("host", ""))
                           and port == int(existing.get("port", 4455)))
            new_cfg["connections"][pid] = {
                "host": host,
                "port": port,
                "password": conn_in.get("password") or (existing.get("password", "") if same_target else ""),
            }
        # Ensure each trigger has an id
        import uuid as _uuid
        for t in new_cfg["triggers"]:
            if not t.get("id"):
                t["id"] = _uuid.uuid4().hex[:8]
        await self.obs.apply_new_config(new_cfg)
        return aiohttp_web.json_response({"ok": True})

    async def handle_obs_triggers(self, request):
        """POST /api/obs/triggers — save only the triggers list (auto-save, no creds)."""
        try:
            body = await request.json()
        except Exception:
            return aiohttp_web.json_response({"ok": False, "error": "Invalid JSON"}, status=400)
        import uuid as _uuid
        triggers = body.get("triggers", [])
        for t in triggers:
            if not t.get("id"):
                t["id"] = _uuid.uuid4().hex[:8]
        # Update triggers in-place — don't restart OBS connections (would briefly disconnect both players)
        self.obs._config["triggers"] = triggers
        self.obs.save_config()
        return aiohttp_web.json_response({"ok": True})

    async def handle_obs_scenes(self, request):
        """GET /api/obs/scenes/{player} — list available scene names from OBS."""
        player = request.match_info.get("player", "a")
        if player not in ("a", "b"):
            return aiohttp_web.json_response({"ok": False, "scenes": []}, status=400)
        scenes = await self.obs.list_scenes(player)
        return aiohttp_web.json_response({"ok": True, "scenes": scenes})

    async def handle_obs_test(self, request):
        """POST /api/obs/test — fire a test scene change immediately."""
        try:
            body = await request.json()
        except Exception:
            return aiohttp_web.json_response({"ok": False, "error": "Invalid JSON"}, status=400)
        player = body.get("player", "a")
        scene  = body.get("scene", "")
        if player not in ("a", "b"):
            return aiohttp_web.json_response({"ok": False, "error": "Invalid player"}, status=400)
        if not scene:
            return aiohttp_web.json_response({"ok": False, "error": "scene required"}, status=400)
        result = await self.obs.test_scene(player, scene)
        return aiohttp_web.json_response(result)

    async def handle_obs_connect(self, request):
        """POST /api/obs/connect — save connection settings for this player and (re)connect."""
        try:
            body = await request.json()
        except Exception:
            body = {}
        player = body.get("player", "a")
        if player not in ("a", "b"):
            return aiohttp_web.json_response({"ok": False, "error": "Invalid player"}, status=400)
        # Persist any connection settings provided — preserves the other player's settings
        host = str(body.get("host", "")).strip()
        port_raw = body.get("port")
        port = int(port_raw) if port_raw else None
        password = body.get("password")  # None = not provided; "" = explicitly cleared
        if host or port is not None or password:
            conns = {k: dict(v) for k, v in self.obs._config.get("connections", {}).items()}
            old = dict(conns.get(player, {}))
            conn = dict(old)
            if host:
                conn["host"] = host
            if port is not None:
                conn["port"] = port
            if password:  # only update if non-empty; blank = keep existing
                conn["password"] = password
            elif (str(conn.get("host", "")), str(conn.get("port", ""))) !=                     (str(old.get("host", "")), str(old.get("port", ""))):
                conn["password"] = ""   # never replay a saved password to a new host
            conns[player] = conn
            self.obs._config = {**self.obs._config, "connections": conns}
            self.obs.save_config()
        await self.obs.connect_player(player)
        return aiohttp_web.json_response({"ok": True})

    async def handle_obs_disconnect(self, request):
        """POST /api/obs/disconnect — disconnect a player's OBS."""
        try:
            body = await request.json()
        except Exception:
            body = {}
        player = body.get("player", "a")
        if player not in ("a", "b"):
            return aiohttp_web.json_response({"ok": False, "error": "Invalid player"}, status=400)
        await self.obs.disconnect_player(player)
        return aiohttp_web.json_response({"ok": True})

    def _load_known_area_ids(self) -> list[str]:
        """Return every area_id known to the active game's area maps, sorted.

        Reads all area_map*.json files in data/games/<game_id>/, handling the
        four formats currently in use (gen1/2 object-of-objects, gen3 flat
        bank:map → slug, gen4 slug → {display,maps}, gen5 list of zone records).
        Falls back to gen3_frlge if the adapter's game dir doesn't exist.
        """
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        game_dir = os.path.join(base_dir, "data", "games", self._area_pack())
        if not os.path.isdir(game_dir):
            game_dir = os.path.join(base_dir, "data", "games", "gen3_frlge")
        area_ids: set[str] = set()
        try:
            entries = os.listdir(game_dir)
        except OSError:
            return []
        for fname in entries:
            if not (fname.startswith("area_map") and fname.endswith(".json")):
                continue
            try:
                with open(os.path.join(game_dir, fname)) as f:
                    data = json.load(f)
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(data, dict):
                for k, v in data.items():
                    if k.startswith("_"):  # _comment etc
                        continue
                    if isinstance(v, dict) and "area_id" in v:
                        # gen1/gen2: {id_str: {area_id, name}}
                        if v["area_id"]:
                            area_ids.add(v["area_id"])
                    elif isinstance(v, str) and v:
                        # gen3: {bank:map: area_slug}
                        area_ids.add(v)
                    elif isinstance(v, dict) and "display" in v:
                        # gen4: {area_slug: {display, maps}}
                        area_ids.add(k)
            elif isinstance(data, list):
                # gen5: [{zone_id, area_id, name}, ...]
                for entry in data:
                    if isinstance(entry, dict) and entry.get("area_id"):
                        area_ids.add(entry["area_id"])
        return sorted(area_ids)

    def _area_pack(self) -> str:
        """The data/games/<dir> of the active game's area maps: the adapter's `area_pack` when
        it names one (titles sharing an adapter can have their own maps), else its game_id."""
        if not self.adapter:
            return "gen3_frlge"
        return getattr(self.adapter, "area_pack", None) or self.adapter.game_id

    async def handle_obs_areas(self, request):
        """GET /api/obs/areas — grouped area list for the active game.

        Response shape:
            {
              "game_id": "gen3_frlge",
              "groups":  [{"id": "route", "label": "Routes", "count": 25}, ...],
              "areas":   [{"id": "route_1", "name": "Route 1", "group": "route"}, ...],
            }
        """
        area_ids = self._load_known_area_ids()
        # Bucket by group, preserving the AREA_GROUPS display order
        grouped: dict[str, list[dict]] = {g["id"]: [] for g in AREA_GROUPS}
        for aid in area_ids:
            gid = classify_area(aid)
            if gid not in grouped:
                gid = "other"
            name = self.adapter.area_display_name(aid) if self.adapter else aid
            grouped[gid].append({"id": aid, "name": name, "group": gid})
        groups_out: list[dict] = []
        areas_out: list[dict] = []
        for g in AREA_GROUPS:
            items = grouped.get(g["id"], [])
            if not items:
                continue
            items.sort(key=lambda a: a["name"].lower())
            groups_out.append({"id": g["id"], "label": g["label"], "count": len(items)})
            areas_out.extend(items)
        return aiohttp_web.json_response({
            "game_id": self.adapter.game_id if self.adapter else "",
            "groups":  groups_out,
            "areas":   areas_out,
        })

    async def handle_bot_status(self, request):
        """GET /api/bot/status — returns current bot status + config + recent activity."""
        cfg = _bot_load_config(self._data_dir)
        access_token = os.environ.get("TWITCH_ACCESS_TOKEN", "")
        connected = (self._bot_instance is not None
                     and self._bot_task is not None
                     and not self._bot_task.done()
                     and bool(access_token))
        status = "disabled" if not cfg.get("enabled", True) else ("connected" if connected else "disconnected")
        return aiohttp_web.json_response({
            "ok": True,
            "status": status,
            "access_token_set": bool(access_token),
            "client_id_set": bool(cfg.get("client_id", "")),
            "last_error": self._bot_last_error,
            "channel": cfg.get("channel", ""),
            "config": {k: v for k, v in cfg.items() if k != "token"},
            "activity": list(self._bot_activity[-50:]),
        })

    async def handle_bot_config(self, request):
        """POST /api/bot/config — save non-sensitive config to data/twitch_bot.json."""
        try:
            body = await request.json()
        except Exception:
            return aiohttp_web.json_response({"ok": False, "error": "Invalid JSON"}, status=400)
        cfg = _bot_load_config(self._data_dir)
        allowed = {"channel", "nick", "prefix", "command_cooldown_sec", "enabled", "client_id"}
        for k in allowed:
            if k in body:
                cfg[k] = body[k]
        _bot_save_config(self._data_dir, cfg)
        return aiohttp_web.json_response({"ok": True})

    async def handle_bot_reload(self, request):
        """POST /api/bot/reload — cancel + restart the bot task."""
        await self._restart_bot()
        return aiohttp_web.json_response({"ok": True})

    async def handle_bot_enable(self, request):
        """POST /api/bot/enable — mark enabled in config and restart."""
        cfg = _bot_load_config(self._data_dir)
        cfg["enabled"] = True
        _bot_save_config(self._data_dir, cfg)
        await self._restart_bot()
        return aiohttp_web.json_response({"ok": True})

    async def handle_bot_disable(self, request):
        """POST /api/bot/disable — mark disabled and cancel task."""
        cfg = _bot_load_config(self._data_dir)
        cfg["enabled"] = False
        _bot_save_config(self._data_dir, cfg)
        if self._bot_task and not self._bot_task.done():
            self._bot_task.cancel()
            try:
                await self._bot_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
        self._bot_task = None
        self._bot_instance = None
        return aiohttp_web.json_response({"ok": True})

    async def handle_bot_preview(self, request):
        """POST /api/bot/preview — return what a command would reply without sending."""
        try:
            body = await request.json()
        except Exception:
            return aiohttp_web.json_response({"ok": False, "error": "Invalid JSON"}, status=400)
        cmd = (body.get("command") or "").lower().strip()
        arg = (body.get("arg") or "").strip()
        if self._bot_instance:
            reply = await self._bot_instance.build_reply(cmd, arg)
        else:
            try:
                from server.twitch_bot import build_reply_standalone
                reply = await build_reply_standalone(cmd, arg, self, self._data_dir or DATA_DIR)
            except Exception as e:
                reply = f"(Bot not running — {e})"
        return aiohttp_web.json_response({"ok": True, "reply": reply})

    async def _restart_bot(self):
        """Cancel the existing bot task (if any) and start a fresh one."""
        if self._bot_task and not self._bot_task.done():
            self._bot_task.cancel()
            try:
                await self._bot_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
        self._bot_task = None
        self._bot_instance = None
        cfg = _bot_load_config(self._data_dir)
        if not cfg.get("enabled", True):
            return
        access_token = os.environ.get("TWITCH_ACCESS_TOKEN", "")
        if not access_token:
            self._bot_last_error = (
                "TWITCH_ACCESS_TOKEN environment variable is not set. "
                "Set it before starting the server (see /twitch for instructions)."
            )
            log.warning("Twitch bot: TWITCH_ACCESS_TOKEN not set — bot disabled")
            return
        refresh_token = os.environ.get("TWITCH_REFRESH_TOKEN", "")
        client_secret = os.environ.get("TWITCH_CLIENT_SECRET", "")
        if not client_secret:
            self._bot_last_error = (
                "TWITCH_CLIENT_SECRET environment variable is not set. "
                "Generate a Client Secret at dev.twitch.tv/console → your app → New Secret."
            )
            log.warning("Twitch bot: TWITCH_CLIENT_SECRET not set — bot disabled")
            return
        client_id = cfg.get("client_id", "")
        if not client_id:
            self._bot_last_error = (
                "Client ID is not configured. Register an app at dev.twitch.tv/console, "
                "copy the Client ID, and save it in the config form."
            )
            log.warning("Twitch bot: client_id not set — bot disabled")
            return
        if not cfg.get("channel"):
            self._bot_last_error = "Channel is not configured. Enter a channel name and save."
            log.warning("Twitch bot: channel not set — bot disabled")
            return
        self._bot_last_error = ""
        try:
            from server.twitch_bot import SLinkChatBot
            bot = SLinkChatBot(
                self, self._data_dir or DATA_DIR, cfg,
                access_token=access_token,
                refresh_token=refresh_token,
                client_id=client_id,
                client_secret=client_secret,
            )
            self._bot_instance = bot
            self._bot_task = asyncio.create_task(bot.start())
            self._bot_task.add_done_callback(self._on_bot_done)
            log.info(f"Twitch bot started for channel #{cfg.get('channel','?')}")
        except Exception as e:
            self._bot_last_error = f"Startup failed: {e}"
            log.error(f"Twitch bot startup failed: {e}")

    def _on_bot_done(self, task):
        if task.cancelled():
            pass
        elif task.exception():
            err = str(task.exception())
            self._bot_last_error = f"Connection failed: {err}"
            log.error(f"Twitch bot task failed: {err}")
            entry = {"ts": datetime.utcnow().isoformat(), "text": f"⚠ Error: {err}"}
            self._bot_activity.append(entry)
            if len(self._bot_activity) > 50:
                self._bot_activity = self._bot_activity[-50:]
        self._bot_task = None
        self._bot_instance = None

    # ── Debug page & API ─────────────────────────────────────────────────────

    async def handle_debug_html(self, request):
        return self._panel_page(request, "debug")

    async def handle_debug_manual_link_data(self, request):
        """GET /api/debug/manual_link_data — return mon options + area data for manual linking."""
        s = self.state
        # Build reverse map: monKey → pending area
        pending_key_to_area: dict[tuple, str] = {}
        for pc_area, players in s.pending_captures.items():
            for pid_pc, mon_pc in players.items():
                pending_key_to_area[(pid_pc, mon_pc.key)] = pc_area

        result: dict = {}
        for pid in ["a", "b"]:
            opts = []
            seen_keys = set()
            # Party mons
            for key in self._get_party_ordered(pid):
                det = self.party_details[pid][key]
                nick = det.get("nickname", "")
                sid = det.get("species_id", 0)
                sp_name = self.adapter.species_name(sid) if sid else "?"
                lv = det.get("level", 0)
                linked = s.entry_for(pid, key) is not None
                label = f"{nick or sp_name} Lv{lv} [{key[:8]}]"
                pend = pending_key_to_area.get((pid, key), "")
                opts.append({"key": key, "label": label, "linked": linked,
                             "pending_area": pend, "loc": "party"})
                seen_keys.add(key)
            # Box mons
            for bentry in self.pc_boxes.get(pid, []):
                key = bentry.get("key", "")
                if not key:
                    continue
                nick = bentry.get("nickname", "")
                sid = bentry.get("species_id", 0)
                sp_name = self.adapter.species_name(sid) if sid else "?"
                box_num = bentry.get("box", 0) + 1
                linked = s.entry_for(pid, key) is not None
                label = f"{nick or sp_name} [Box{box_num}] [{key[:8]}]"
                pend = pending_key_to_area.get((pid, key), "")
                opts.append({"key": key, "label": label, "linked": linked,
                             "pending_area": pend, "loc": "box"})
                seen_keys.add(key)
            # Linked/dead/memorial mons from link entries (not already shown)
            for entry in s.links:
                mon = entry.a if pid == "a" else entry.b
                if not mon or mon.key in seen_keys:
                    continue
                nick = mon.nickname or ""
                sp_name = self.adapter.species_name(mon.species) if mon.species else "?"
                status_tag = entry.status.value.upper()
                label = f"{nick or sp_name} Lv{mon.level} [{mon.key[:8]}] ({status_tag})"
                opts.append({"key": mon.key, "label": label, "linked": True,
                             "pending_area": "", "loc": status_tag.lower()})
                seen_keys.add(mon.key)
            # Pending captures not yet in party/box display (e.g. quarantined)
            for pc_area, players in s.pending_captures.items():
                cap = players.get(pid)
                if cap and cap.key not in seen_keys:
                    sp_name = self.adapter.species_name(cap.species) if cap.species else "?"
                    label = f"{cap.nickname or sp_name} Lv{cap.level} [{cap.key[:8]}] (PENDING)"
                    opts.append({"key": cap.key, "label": label, "linked": False,
                                 "pending_area": pc_area, "loc": "pending"})
                    seen_keys.add(cap.key)
            result[f"{pid}_options"] = opts

        # Build area list: the active game's own area maps, via the shared loader.
        # It has no cross-game fallback -- a game that ships no map (gen3_exp) gets []
        # and then only the areas its own run has entered, never another game's catalog.
        _all_area_ids = self._load_known_area_ids()
        all_area_set = set(_all_area_ids)
        for extra_src in [s.area_states.keys(), s.pending_captures.keys()]:
            for extra in extra_src:
                if extra and extra not in all_area_set:
                    _all_area_ids.append(extra)
                    all_area_set.add(extra)
        if "gift" not in all_area_set:
            _all_area_ids.append("gift")
        _all_area_ids.sort()

        area_data: dict[str, dict] = {}
        for aid in _all_area_ids:
            disp = self.adapter.area_display_name(aid)
            st = s.area_states.get(aid)
            st_str = st.value if st else "unseen"
            pend_who = ""
            if aid in s.pending_captures:
                pend_who = ",".join(sorted(s.pending_captures[aid].keys()))
            area_data[aid] = {"d": disp, "s": st_str, "p": pend_who}

        result["areas"] = area_data
        result["area_ids"] = _all_area_ids
        result["name_a"] = self.trainer_name.get("a") or "Player A"
        result["name_b"] = self.trainer_name.get("b") or "Player B"
        return aiohttp_web.json_response(result)

    async def handle_debug_raw_state(self, request):
        """GET /api/debug/raw_state — return raw links.json content + live state."""
        s = self.state
        raw = {}
        if os.path.exists(s._links_path):
            with open(s._links_path) as f:
                raw = json.load(f)
        raw["_live"] = {
            "queued_commands": {p: list(cmds) for p, cmds in s.queued_commands.items()},
            "connected_players": {p: dict(d) for p, d in self.connected_players.items()},
            "player_area": dict(self.player_area),
            "player_ball_count": dict(self.player_ball_count),
            "party_keys": {p: sorted(keys) for p, keys in s.party_keys.items()},
            "bonus_keys": {p: sorted(keys) for p, keys in s.bonus_keys.items()},
            "pending_bonus": {p: list(q) for p, q in s.pending_bonus.items()},
            "party_size": dict(s.party_size),
            "identity_errors": dict(s.identity_error),
            "battle_state": {p: {"in_battle": bs["in_battle"],
                                  "is_trainer": bs["is_trainer_battle"]}
                             for p, bs in self.battle_state.items()},
            "recent_events": list(self._recent_events),
        }
        # Memorial data for debug panel
        mem_box_idx = self.adapter.memorial_box_index if self.adapter else -1
        memorial_log = []
        mem_path = s._memorial_path
        if os.path.exists(mem_path):
            try:
                with open(mem_path) as mf:
                    mem_doc = json.load(mf)
                # memorial.json is {"retired_pairs": [...]}; the panel wants the list.
                memorial_log = mem_doc.get("retired_pairs", []) if isinstance(mem_doc, dict) else []
            except Exception:
                pass
        # Build memorial box contents from pc_boxes (mons in the memorial box)
        memorial_box_contents: dict[str, list] = {}
        if mem_box_idx >= 0:
            # Collect all dead/memorial keys from link entries for cross-referencing
            dead_keys: set[str] = set()
            pending_mem_keys: set[str] = set()
            pending_cap_keys: set[str] = set()
            for entry in s.links:
                if entry.status.value in ("dead", "memorial"):
                    if entry.a:
                        dead_keys.add(entry.a.key)
                    if entry.b:
                        dead_keys.add(entry.b.key)
            for pid in ("a", "b"):
                pending_mem_keys.update(s.pending_memorials.get(pid, set()))
                for _area, players in s.pending_captures.items():
                    cap = players.get(pid)
                    if cap:
                        pending_cap_keys.add(cap.key)
            for pid in ("a", "b"):
                entries = []
                for bentry in self.pc_boxes.get(pid, []):
                    if bentry.get("box") == mem_box_idx:
                        key = bentry.get("key", "")
                        status = "dead" if key in dead_keys else "unknown"
                        if key in pending_mem_keys and status == "unknown":
                            status = "pending_memorial"
                        if key in pending_cap_keys:
                            status = "quarantined"  # should NOT be here
                        entries.append({
                            "slot": bentry.get("slot", 0),
                            "key": key,
                            "species_id": bentry.get("species_id", 0),
                            "nickname": bentry.get("nickname", ""),
                            "species_name": self.adapter.species_name(bentry.get("species_id", 0)) if self.adapter else "",
                            "status": status,
                        })
                entries.sort(key=lambda e: e["slot"])
                memorial_box_contents[pid] = entries
        raw["_memorial"] = {
            "memorial_box_index": mem_box_idx,
            "memorial_log": memorial_log,
            "memorial_box_contents": memorial_box_contents,
            "pending_memorials": {
                pid: [
                    {
                        "key": k,
                        "species_name": self._memorial_key_species(k),
                    }
                    for k in keys
                ]
                for pid, keys in s.pending_memorials.items()
            },
        }
        return aiohttp_web.json_response(raw)

    @_requires_loaded_state
    async def handle_debug_inject_event(self, request):
        """POST /api/debug/inject_event — send a synthetic event through the state machine."""
        try:
            body = await request.json()
        except Exception:
            return aiohttp_web.json_response({"ok": False, "error": "Invalid JSON"}, status=400)
        player = body.pop("player", "a")
        if player not in ("a", "b"):
            return aiohttp_web.json_response({"ok": False, "error": "player must be 'a' or 'b'"}, status=400)
        if "event" not in body:
            return aiohttp_web.json_response({"ok": False, "error": "event field required"}, status=400)
        try:
            self._ingest_box_census(player, body)          # KEY-SCOPE-5: as a real snapshot would
            cmds = self.state.handle_event(player, body)
            self._notify_sse()
            return aiohttp_web.json_response({
                "ok": True,
                "player": player,
                "event": body.get("event"),
                "commands_returned": cmds,
            })
        except Exception as e:
            log.exception(f"Debug inject_event error: {e}")
            return aiohttp_web.json_response({"ok": False, "error": str(e)}, status=500)

    @_requires_loaded_state
    async def handle_debug_queue_command(self, request):
        """POST /api/debug/queue_command — manually queue a command for a player."""
        try:
            body = await request.json()
        except Exception:
            return aiohttp_web.json_response({"ok": False, "error": "Invalid JSON"}, status=400)
        player = body.pop("player", "a")
        if player not in ("a", "b"):
            return aiohttp_web.json_response({"ok": False, "error": "player must be 'a' or 'b'"}, status=400)
        cmd_type = body.get("cmd", "noop")
        cmd = {"cmd": cmd_type}
        for k, v in body.items():
            if k not in ("player",):
                cmd[k] = v
        self.state.queued_commands.setdefault(player, []).append(cmd)
        self._notify_sse()
        return aiohttp_web.json_response({
            "ok": True,
            "player": player,
            "command": cmd,
            "queue_length": len(self.state.queued_commands[player]),
        })

    @_requires_loaded_state
    async def handle_debug_set_pokeballs(self, request):
        """POST /api/debug/set_pokeballs — toggle pokeballs_obtained."""
        try:
            body = await request.json()
        except Exception:
            return aiohttp_web.json_response({"ok": False, "error": "Invalid JSON"}, status=400)
        player = body.get("player", "a")
        if player not in ("a", "b"):
            return aiohttp_web.json_response({"ok": False, "error": "player must be 'a' or 'b'"}, status=400)
        value = bool(body.get("value", True))
        self.state.pokeballs_obtained[player] = value
        self.state._save()
        self._notify_sse()
        return aiohttp_web.json_response({
            "ok": True, "player": player,
            "pokeballs_obtained": value,
        })

    @_requires_loaded_state
    async def handle_debug_set_area_state(self, request):
        """POST /api/debug/set_area_state — manually set an area's state."""
        try:
            body = await request.json()
        except Exception:
            return aiohttp_web.json_response({"ok": False, "error": "Invalid JSON"}, status=400)
        area_id = body.get("area_id", "").strip()
        new_state = body.get("state", "").strip()
        if not area_id:
            return aiohttp_web.json_response({"ok": False, "error": "area_id required"}, status=400)
        from server.state import AreaStatus
        valid = {s.value: s for s in AreaStatus}
        if new_state not in valid:
            return aiohttp_web.json_response({"ok": False, "error": f"Invalid state. Valid: {list(valid.keys())}"}, status=400)
        if new_state == "unseen":
            self.state.area_states.pop(area_id, None)
        else:
            self.state.area_states[area_id] = valid[new_state]
        self.state._save()
        self._notify_sse()
        return aiohttp_web.json_response({
            "ok": True, "area_id": area_id, "state": new_state,
        })

    @_requires_loaded_state
    async def handle_debug_clear_pending(self, request):
        """POST /api/debug/clear_pending — clear pending captures (all or by area)."""
        try:
            body = await request.json()
        except Exception:
            body = {}
        area_id = body.get("area_id", "").strip()
        if area_id:
            removed = area_id in self.state.pending_captures
            self.state.pending_captures.pop(area_id, None)
            msg = f"Cleared pending for {area_id}" if removed else f"No pending for {area_id}"
        else:
            count = len(self.state.pending_captures)
            self.state.pending_captures.clear()
            msg = f"Cleared all pending captures ({count} areas)"
        self.state._save()
        self._notify_sse()
        return aiohttp_web.json_response({"ok": True, "message": msg})

    @_requires_loaded_state
    async def handle_debug_unlink(self, request):
        """POST /api/debug/unlink — remove a link entry.

        Body: {"area_id": "route_1", "index": 0}
        Uses index as tiebreaker if multiple links share an area (shouldn't happen
        but be safe). Removes the link entry, cleans up _key_index and area_states.
        """
        try:
            body = await request.json()
        except Exception:
            return aiohttp_web.json_response({"ok": False, "error": "invalid JSON"}, status=400)
        area_id = body.get("area_id", "").strip()
        idx = body.get("index", -1)
        if not area_id:
            return aiohttp_web.json_response({"ok": False, "error": "area_id required"}, status=400)

        from server.state import AreaStatus

        s = self.state
        # Find the entry — match by area_id and list index for safety
        entry = None
        if isinstance(idx, int) and 0 <= idx < len(s.links):
            candidate = s.links[idx]
            if candidate.area_id == area_id:
                entry = candidate
        # Fallback: search by area_id
        if entry is None:
            for e in s.links:
                if e.area_id == area_id:
                    entry = e
                    break
        if entry is None:
            return aiohttp_web.json_response(
                {"ok": False, "error": f"No link found for area {area_id}"}, status=404)
        if s.link_in_trade(entry):
            return aiohttp_web.json_response(
                {"ok": False, "error": "This pair is in a trade. Settle the trade first."}, status=409)

        s.unindex_entry(entry)

        # Remove from links list
        s.links.remove(entry)

        # Reset area state to unseen (unless there are pending captures)
        if area_id in s.pending_captures:
            remaining = s.pending_captures[area_id]
            if "a" in remaining and "b" not in remaining:
                s.area_states[area_id] = AreaStatus.PENDING_B
            elif "b" in remaining and "a" not in remaining:
                s.area_states[area_id] = AreaStatus.PENDING_A
            elif "a" in remaining and "b" in remaining:
                s.area_states[area_id] = AreaStatus.PENDING_BOTH
        else:
            s.area_states[area_id] = AreaStatus.UNSEEN

        a_name = entry.a.nickname if entry.a else "?"
        b_name = entry.b.nickname if entry.b else "?"

        s._save()
        log.info(f"[unlink] Removed link: {a_name} <-> {b_name} on {area_id}")
        self._notify_sse()
        return aiohttp_web.json_response({
            "ok": True,
            "message": f"Unlinked {a_name} <-> {b_name} on {self.adapter.area_display_name(area_id)}. Area reset.",
        })

    @_requires_loaded_state
    async def handle_debug_resolve_trade(self, request):
        """POST /api/debug/resolve_trade {"token": "t7", "action": "commit"|"rollback"|"adopt",
        "sides"?: {"a"|"b": "traded"|"none"}} —
        settle a conflicted or stuck native trade by hand (the board's trade banner names this);
        see SoulLinkState.resolve_trade for how a side's known outcome binds."""
        try:
            body = await request.json()
        except Exception:
            return aiohttp_web.json_response({"ok": False, "error": "invalid JSON"}, status=400)
        if (not isinstance(body, dict) or not isinstance(body.get("token"), str)
                or not isinstance(body.get("action"), str)
                or not isinstance(body.get("sides") or {}, dict)):
            return aiohttp_web.json_response(
                {"ok": False, "error": 'body must be {"token": str, "action": str, "sides"?: object}'}, status=400)
        ok, error = self.state.resolve_trade(body["token"], body["action"], body.get("sides"))
        if not ok:
            return aiohttp_web.json_response({"ok": False, "error": error}, status=400)
        self._notify_sse()
        return aiohttp_web.json_response({"ok": True})

    @_requires_loaded_state
    async def handle_debug_resolve_ambiguous_key(self, request):
        """POST /api/debug/resolve_ambiguous_key {"player": "a"|"b", "key": str} — after checking
        the cartridge, clear a KEY-SCOPE-3 latch (the key names one mon again)."""
        try:
            body = await request.json()
        except Exception:
            body = None
        if not isinstance(body, dict) or not isinstance(body.get("player"), str) \
                or not isinstance(body.get("key"), str):
            return aiohttp_web.json_response(
                {"ok": False, "error": 'body must be {"player": "a"|"b", "key": str}'}, status=400)
        ok, error = self.state.resolve_ambiguous_key(body["player"], body["key"])
        if not ok:
            return aiohttp_web.json_response({"ok": False, "error": error}, status=400)
        self._notify_sse()
        return aiohttp_web.json_response({"ok": True})

    @_requires_loaded_state
    async def handle_debug_revive(self, request):
        """POST /api/debug/revive — revive a dead/memorial link back to alive.

        Body: {"area_id": "route_22", "index": 0}
        Sets status back to alive, clears death metadata, removes from pending_memorials,
        and re-adds keys to party_keys. User must manually restore mons in-game.
        """
        try:
            body = await request.json()
        except Exception:
            return aiohttp_web.json_response({"ok": False, "error": "invalid JSON"}, status=400)
        area_id = body.get("area_id", "").strip()
        idx = body.get("index", -1)
        if not area_id:
            return aiohttp_web.json_response({"ok": False, "error": "area_id required"}, status=400)

        from server.state import LinkStatus

        s = self.state
        # Find the entry
        entry = None
        if isinstance(idx, int) and 0 <= idx < len(s.links):
            candidate = s.links[idx]
            if candidate.area_id == area_id:
                entry = candidate
        if entry is None:
            for e in s.links:
                if e.area_id == area_id and e.status in (LinkStatus.DEAD, LinkStatus.MEMORIAL):
                    entry = e
                    break
        if entry is None:
            return aiohttp_web.json_response(
                {"ok": False, "error": f"No dead/memorial link found for area {area_id}"}, status=404)
        if entry.status == LinkStatus.ALIVE:
            return aiohttp_web.json_response(
                {"ok": False, "error": "Link is already alive"}, status=400)

        # Revive: set status to alive, clear death metadata
        entry.status = LinkStatus.ALIVE
        entry.killed_at = None
        entry.cause = None
        entry.killer = None
        entry.initiating_player = None

        # Remove from pending_memorials and re-add to party_keys
        if entry.a:
            s.pending_memorials["a"].discard(entry.a.key)
            s.party_keys["a"].add(entry.a.key)
        if entry.b:
            s.pending_memorials["b"].discard(entry.b.key)
            s.party_keys["b"].add(entry.b.key)

        a_name = entry.a.nickname if entry.a else "?"
        b_name = entry.b.nickname if entry.b else "?"

        s.run_over = False
        s._check_game_over()
        s._save()
        log.info(f"[revive] Revived link: {a_name} <-> {b_name} on {area_id}")
        self._notify_sse()
        return aiohttp_web.json_response({
            "ok": True,
            "message": f"Revived {a_name} <-> {b_name} on {self.adapter.area_display_name(area_id)}. Restore mons from memorial box manually.",
        })

    async def handle_debug_list_backups(self, request):
        """GET /api/debug/backups — list available rolling backups with summary."""
        backup_dir = os.path.join(os.path.dirname(self.state._links_path), "backups")
        backups = []
        if os.path.isdir(backup_dir):
            for i in range(1, self._backup_max + 1):
                fp = os.path.join(backup_dir, f"links.backup.{i}.json")
                if os.path.exists(fp):
                    stat = os.stat(fp)
                    entry: dict = {
                        "slot": i,
                        "file": f"links.backup.{i}.json",
                        "size": stat.st_size,
                        "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                    }
                    # Read backup JSON for summary stats
                    try:
                        with open(fp) as fh:
                            data = json.loads(fh.read())
                        links = data.get("links", [])
                        alive = sum(1 for lnk in links if lnk.get("status") == "alive")
                        dead = sum(1 for lnk in links if lnk.get("status") in ("dead", "memorial"))
                        areas = data.get("area_states", {})
                        pending = sum(1 for v in areas.values() if v.startswith("pending"))
                        dead_zones = sum(1 for v in areas.values() if v == "dead_zone")
                        entry["summary"] = {
                            "links_alive": alive,
                            "links_dead": dead,
                            "areas_pending": pending,
                            "areas_dead_zone": dead_zones,
                        }
                    except Exception:
                        entry["summary"] = None
                    backups.append(entry)
        return aiohttp_web.json_response({"ok": True, "backups": backups})

    def _reset_connection_and_display_state(self) -> None:
        """Clear connected_players and every derived party/box/battle/display cache.

        Shared by `/api/reset` and `/api/debug/rollback` (Codex cx-2985fe38 F3): both
        replace `self.state` with a run the connected clients have not said hello to yet,
        so both must leave the SAME blank slate behind -- a stale `connected_players` entry
        (rom_type, panel/sfx caps) or a stale party/box/battle snapshot from the run just
        replaced must not survive into the new one and be shown until the next hello.
        """
        self.connected_players.clear()
        self.player_area = {"a": "", "b": ""}
        self.player_area_id = {"a": "", "b": ""}
        self.player_ball_count = {"a": 0, "b": 0}
        self.player_badges = {"a": 0, "b": 0}
        self.player_gender = {"a": None, "b": None}
        self.player_kanto_badges = {"a": 0, "b": 0}
        self.trainer_name = {"a": "", "b": ""}
        self.pc_boxes = {"a": [], "b": []}
        self.box_census = {pid: {"capable": False, "gen": -1, "boxes": None} for pid in ("a", "b")}
        self.party_details = {"a": {}, "b": {}}
        self._mon_cache.clear()
        self.battle_state = {
            p: {"in_battle": False, "is_trainer_battle": False, "enemy_party": [],
                "trainer_id": 0, "opponent_name": "", "opponent_class": "",
                "is_doubles": False}
            for p in ("a", "b")
        }
        self._last_panel_sig = {"a": None, "b": None}

    async def handle_debug_rollback(self, request):
        """POST /api/debug/rollback — restore links.json (and events.json) from a backup slot."""
        try:
            body = await request.json()
        except Exception:
            return aiohttp_web.json_response({"ok": False, "error": "Invalid JSON"}, status=400)
        slot = body.get("slot")
        if not isinstance(slot, int) or slot < 1 or slot > self._backup_max:
            return aiohttp_web.json_response(
                {"ok": False, "error": f"Invalid slot (1-{self._backup_max})"}, status=400)
        backup_dir = os.path.join(os.path.dirname(self.state._links_path), "backups")
        backup_links  = os.path.join(backup_dir, f"links.backup.{slot}.json")
        backup_events = os.path.join(backup_dir, f"events.backup.{slot}.json")
        if not os.path.exists(backup_links):
            return aiohttp_web.json_response(
                {"ok": False, "error": f"Backup slot {slot} not found"}, status=404)
        os.makedirs(backup_dir, exist_ok=True)
        # Save current state as pre-rollback snapshots
        took_pre = os.path.exists(self.state._links_path)
        if took_pre:
            shutil.copy2(self.state._links_path,
                         os.path.join(backup_dir, "links.pre_rollback.json"))
        if os.path.exists(self._events_path):
            shutil.copy2(self._events_path,
                         os.path.join(backup_dir, "events.pre_rollback.json"))
        # Restore links.json and reload state. The slot is loaded BEFORE anything else changes: a
        # truncated or unloadable backup must leave links.json, the live state and the clients as they were.
        pre_links = os.path.join(backup_dir, "links.pre_rollback.json")
        shutil.copy2(backup_links, self.state._links_path)
        try:
            restored = SoulLinkState.load(
                data_dir=self._data_dir,
                species_lock=self.state.species_lock,
                gender_lock=self.state.gender_lock,
                type_lock=self.state.type_lock,
                explode_mode=self.state.explode_mode,
                rival_team_swap=self.state.rival_team_swap,
                overworld_presence=self.state.overworld_presence,
                native_messages=self.state.native_messages,
                native_sounds=self.state.native_sounds,
                battle_calc=self.state.battle_calc,
                pc_trade_npc=self.state.pc_trade_npc,
                phone_calls=self.state.phone_calls,
                retry_same_species=self.state.retry_same_species,
                ignore_wild_faints=self.state.ignore_wild_faints)
            problem = restored.load_failed
        except Exception as exc:                        # e.g. UnsafeGameMigration from an old slot
            restored, problem = None, f"{type(exc).__name__}: {exc}"
        if problem:
            if took_pre:                                 # never an older rollback's leftover
                shutil.copy2(pre_links, self.state._links_path)
            else:
                os.remove(self.state._links_path)
            log.error(f"rollback to slot {slot} refused: {problem}")
            return aiohttp_web.json_response(
                {"ok": False, "error": f"Backup slot {slot} could not be loaded ({problem}). Nothing was changed."},
                status=409)
        self._disconnect_clients()
        self.state = restored
        self.state.presentation_key_in_use = self._presentation_key_in_use
        self.state.on_trade_outcome = self._journal_trade
        self.adapter = self.state.adapter
        self._player_adapters = {}
        self.state.player_adapter_for = self.adapter_for
        # Closed clients reconnect and re-hello; no open socket can silently lose events
        # behind this pending admission gate.
        self.admission = {pid: {"state": "contract_pending", "reason": "run restored; awaiting hello"}
                          for pid in VALID_PLAYERS}
        # F3: rollback must leave the same blank connection/display slate as /api/reset —
        # a patched client's stale panel/sfx caps, party/box snapshot or battle state must
        # not survive into the restored run.
        self._reset_connection_and_display_state()
        if self.state.artifact_kind:
            self.adapter.set_artifact_kind(self.state.artifact_kind)
        # Restore events.json and reload ring buffer
        if os.path.exists(backup_events):
            shutil.copy2(backup_events, self._events_path)
        else:
            # No events backup for this slot — clear the ring buffer so it stays in sync.
            with open(self._events_path, "w") as _ef:
                _ef.write("[]")
        self._load_events()
        log.warning(f"⚠  Rolled back to backup slot {slot}")
        self._notify_sse()
        return aiohttp_web.json_response({
            "ok": True,
            "message": f"Rolled back to backup slot {slot}. Pre-rollback state saved."
        })

    async def handle_reset_api(self, request):
        """POST /api/reset — wipe all Soul Link state and start a fresh run."""
        self._disconnect_clients()
        links_path = self.state._links_path
        if os.path.exists(links_path):
            os.remove(links_path)
        # Reset = a new run. Every socket was just closed, so each client reconnects and
        # re-hellos; its cartridge facts (rom_type, panel/panel_abi/sfx) come back with that
        # hello and nothing of the old cartridge carries over. The adapter is kept only so the
        # page renders the right generation until the first hello re-resolves it.
        self.connected_players.clear()
        self.state = SoulLinkState(data_dir=self._data_dir,
                                   adapter=self.state.adapter,
                                   is_rr=self.state.is_rr,
                                   species_lock=self.state.species_lock,
                                   gender_lock=self.state.gender_lock,
                                   type_lock=self.state.type_lock,
                                   explode_mode=self.state.explode_mode,
                                   rival_team_swap=self.state.rival_team_swap,
                                   overworld_presence=self.state.overworld_presence,
                                   native_messages=self.state.native_messages,
                                   native_sounds=self.state.native_sounds,
                                   battle_calc=self.state.battle_calc,
                                   pc_trade_npc=self.state.pc_trade_npc,
                                   phone_calls=self.state.phone_calls,
                                   retry_same_species=self.state.retry_same_species,
                                   ignore_wild_faints=self.state.ignore_wild_faints)
        self.state.presentation_key_in_use = self._presentation_key_in_use
        self.state.on_trade_outcome = self._journal_trade
        self.adapter = self.state.adapter
        self._player_adapters = {}
        self.state.player_adapter_for = self.adapter_for
        self.admission = {pid: {"state": "contract_pending", "reason": "run reset; awaiting hello"}
                          for pid in VALID_PLAYERS}
        # Clear derived display caches so SSE doesn't broadcast stale data. Shared with
        # /api/debug/rollback (F3) so both leave the same blank slate.
        self._reset_connection_and_display_state()
        self._recent_events.clear()
        self._save_events()
        log.warning("⚠  State reset via API — all links, area states, and captures cleared.")
        self._notify_sse()
        return aiohttp_web.json_response({"ok": True, "message": "State reset. Starting fresh run."})

    def _memorial_key_species(self, key: str) -> str:
        """Look up a species name for a pending memorial key."""
        s = self.state
        entry = s._key_index.get(key)
        if entry:
            mon = entry.a if (entry.a and entry.a.key == key) else entry.b
            if mon:
                name = mon.nickname or (self.adapter.species_name(mon.species) if mon.species else "")
                if name:
                    return name
        return key[:8] if key else "?"

    def _memorial_box_indices(self, player_id: str | None = None) -> set[int]:
        """Return set of box indices used for memorial storage (primary + overflow).

        Each player's memorial storage is its own: its buried halves (DEAD/MEMORIAL links) and
        its pending memorials, as one key set so a pending burial of a dead link counts once.
        The primary box holds `mons_per_box`; each further mon spills into the box below it.
        With `player_id`, that player's boxes only; otherwise the union of both players'.
        """
        from server.state import LinkStatus
        mem_idx = self.adapter.memorial_box_index if self.adapter else -1
        if mem_idx < 0:
            return set()
        indices = {mem_idx}
        mons_per_box = self.adapter.mons_per_box if self.adapter else 30
        for pid in ((player_id,) if player_id else ("a", "b")):
            buried = {getattr(e, pid).key for e in self.state.links
                      if e.status in (LinkStatus.DEAD, LinkStatus.MEMORIAL) and getattr(e, pid) is not None}
            overflow_boxes = max(0, (len(buried | self.state.pending_memorials[pid]) - 1) // mons_per_box)
            indices.update(mem_idx - i for i in range(1, overflow_boxes + 1) if mem_idx - i >= 0)
        return indices

    def _ingest_box_census(self, player_id: str, msg: dict) -> None:
        """KEY-SCOPE-5: keep the collision check's box census. A client advertises a complete
        census by sending `pc_boxes_generation` (monotonic per client session, bumped after each
        successful full box scan). A snapshot carrying a newer generation with `pc_boxes`
        replaces the census; a snapshot without both (or with an older generation) makes it
        stale until a newer generation arrives. A client that never sends a generation is
        legacy: its (possibly empty) `pc_boxes` stays presence-based, as before -- unless its
        foundation implements the generation (`reports_box_census`), where a missing one is no
        census. Any box mutation makes the census stale until the next complete scan."""
        event, bc = msg.get("event"), self.box_census[player_id]
        if self.state.party_snapshot_withheld(player_id, msg):
            bc["boxes"] = None  # retained display is not collision evidence
            return
        if event == "hello":
            bc.update(capable="pc_boxes_generation" in msg, gen=-1, boxes=None)
        elif "pc_boxes_generation" in msg:
            bc["capable"] = True
        if event in ("party_to_box", "box_to_party", "stats_cache", "release", "memorialize_done"):
            bc["boxes"] = None                    # a box mutation: stale until a newer generation
            return
        if event not in ("hello", "tick", "safe") or not self._census_capable(player_id):
            return
        gen, boxes = msg.get("pc_boxes_generation"), msg.get("pc_boxes")
        complete = type(gen) is int and gen >= 0 and isinstance(boxes, list)
        if complete and gen > bc["gen"]:
            bc.update(gen=gen, boxes=[dict(e) for e in boxes if isinstance(e, dict)])
        elif complete and gen == bc["gen"] and bc["boxes"] is not None:
            pass                                  # the census we hold, resent
        elif "party" in msg or event == "safe":   # a snapshot (or a post-battle safe) without one
            bc["boxes"] = None

    def _census_capable(self, player_id: str) -> bool:
        """KEY-SCOPE-5: this player's census is generation-stamped -- by foundation (the client
        implements it), or because this client session sent a generation."""
        return self.box_census[player_id]["capable"] or self.adapter_for(player_id).reports_box_census()

    def _journal_trade(self, rec: dict) -> None:
        """events.json: one trade_<outcome> entry per native-trade outcome; key = the trade token."""
        names = []
        for pid, partner in (("a", "b"), ("b", "a")):
            # Commit is published after the link swap and trade evolution, before the
            # next party tick. Resolve the received identity from the updated link;
            # the display caches can still describe its pre-trade species/holder.
            key = ((rec.get(f"{pid}_new") or rec[f"{partner}_key"])
                   if rec["outcome"] == "committed" else rec[f"{pid}_key"])
            entry = self.state.entry_for(pid, key)
            mon = entry and getattr(entry, pid)
            name = ((mon.nickname or self.adapter_for(pid).species_name(mon.species))
                    if mon and mon.key == key else "")
            names.append(name or self._mon_display_name(pid, key))
        details = " ".join(part for part in (
            str(rec["verdict"]) if rec.get("verdict") else "", rec.get("problem") or "") if part)
        self._log_event("", f"trade_{rec['outcome']}",
                        f"{names[0]} <-> {names[1]}" + (f": {details}" if details else ""),
                        key=rec["token"])

    def _presentation_key_in_use(self, key: str, player_id: str | None = None,
                                 party: bool = True) -> bool | None:
        """Is `key` a live mon in the presentation caches?  The state's key_change collision
        preflight asks this (PLAN A1): a party mon or a mon in a non-memorial box of either
        player is load-bearing; the memorial boxes (primary and overflow) hold buried keys,
        which are reusable, and `_mon_cache` is never pruned, so neither counts. KEY-SCOPE: with
        a player, only that player's caches count (the partner may hold an equal key).
        `party=False` skips the party cache (the state already proved that party hit is the
        key-changing mon itself). KEY-SCOPE-5: a client that advertises a box census is judged
        on its newest complete census only; None = that census is missing or stale."""
        pids = (player_id,) if player_id else ("a", "b")
        if party and any(key in self.party_details.get(pid, {}) for pid in pids):
            return True
        unavailable = False
        for pid in pids:
            boxes = self.box_census[pid]["boxes"] if self._census_capable(pid) else self.pc_boxes.get(pid, [])
            if boxes is None:
                unavailable = True
                continue
            memorial = self._memorial_box_indices(pid)
            if any(b.get("key") == key and b.get("box") not in memorial for b in boxes):
                return True
        return None if unavailable else False

    def _migrate_presentation_key(self, player_id: str, old_key: str, new_key: str):
        """Move the presentation caches from old_key to new_key AFTER state accepted the
        key_change (a rejected one leaves them alone, so the dashboard keeps showing the
        identity the rules layer still holds)."""
        old_detail = self.party_details[player_id].pop(old_key, None)
        if old_detail:
            self.party_details[player_id][new_key] = old_detail
        old_mon = self._mon_cache.pop(old_key, None)
        if old_mon:
            self._mon_cache[new_key] = old_mon
        for bentry in self.pc_boxes.get(player_id, []):
            if bentry.get("key") == old_key:
                bentry["key"] = new_key

    def _check_memorial_box_contamination(self, player_id: str, pc_boxes: list):
        """Scan pc_boxes for memorial box integrity violations and take corrective action.

        Three checks (all require a dedicated memorial box, i.e. mem_idx >= 0):

        1. Non-dead mon in memorial box — quarantine relocation (existing behaviour).
           A pending-capture that somehow ended up in the memorial box is pulled out
           and re-deposited to a normal box via party_mon + box_mon.

        2. Dead/memorial mon found in a regular (non-memorial) box — re-memorialize.
           Handles the case where a player manually retrieves a dead mon from the
           memorial box via the PC menu, or a write glitch placed it in the wrong box.

        3. Orphan in memorial box — log-only warning.
           A mon in the memorial box whose key is not tracked as dead/memorial and is
           not in pending_memorials.  We don't auto-fix this because it may be a mon
           the player placed there manually; it is surfaced as "unknown" in the debug
           panel so a human can investigate.
        """
        mem_idx = self.adapter.memorial_box_index if self.adapter else -1
        if mem_idx < 0:
            return
        memorial_boxes = self._memorial_box_indices(player_id)
        s = self.state

        # Collect keys that belong in the memorial box (dead/memorial link entries).
        all_dead_keys: set[str] = set()
        for entry in s.links:
            if entry.status.value in ("dead", "memorial"):
                mon = entry.a if player_id == "a" else entry.b
                if mon:
                    all_dead_keys.add(mon.key)

        # Also treat pending_memorials for *this* player as "expected in memorial box"
        # so they are not flagged as orphans while still in transit.
        pending_for_player: set[str] = set(s.pending_memorials.get(player_id, set()))
        expected_in_memorial = all_dead_keys | pending_for_player

        for bentry in pc_boxes:
            box = bentry.get("box")
            key = bentry.get("key", "")
            if not key:
                continue
            nick = bentry.get("nickname", "") or (
                self.adapter.species_name(bentry.get("species_id", 0)) if self.adapter else key[:8]
            )

            if box in memorial_boxes:
                # ── Check 1: non-dead mon in memorial box ──────────────────────
                if key not in expected_in_memorial:
                    # Once per key, like Check 3 twenty lines below. Unguarded, this
                    # re-logged on every event carrying pc_boxes -- roughly twice a
                    # second, forever -- and the trigger is ordinary: the Gen 1
                    # memorial box IS Box 12 (SRAM 0x75EA = sBox12), so anything the
                    # player ever stored there, or any mon revived from the dashboard,
                    # is permanently "unexpected".
                    if (player_id, key) not in self._warned_memorial_keys:
                        self._warned_memorial_keys.add((player_id, key))
                        log.warning(
                            f"[{player_id}] ⚠ NON-DEAD mon in memorial box: {nick} "
                            f"[{key[:8]}] (box {box} slot {bentry.get('slot', '?')})"
                        )
                    # Relocate if it is a quarantined pending capture.
                    for _area, players in s.pending_captures.items():
                        cap = players.get(player_id)
                        if cap and cap.key == key:
                            log.warning(
                                f"[{player_id}] Quarantined mon {key[:8]} found in memorial box! "
                                f"Queueing party_mon + box_mon to relocate."
                            )
                            stats = s.mon_stats[player_id].get(key, {})
                            s.queued_commands[player_id].append({"cmd": "party_mon", "key": key, "stats": stats})
                            s.queued_commands[player_id].append({"cmd": "box_mon", "key": key})
                            break

                    # ── Check 3: orphan in memorial box (log-only, once per key) ────
                    # Only log if not a quarantine case (already logged above).
                    else:
                        if key not in self._warned_orphan_keys:
                            self._warned_orphan_keys.add(key)
                            log.warning(
                                f"[{player_id}] ⚠ Orphan mon in memorial box: {nick} [{key[:8]}] "
                                f"(box {box} slot {bentry.get('slot', '?')}) — "
                                f"not tracked as dead/memorial; investigate manually"
                            )

            elif key in all_dead_keys:
                # ── Check 2: dead/memorial mon found in a regular box ───────────
                # This happens if the player moved a dead mon out of the memorial box
                # via the PC, or if a previous memorialize write went to the wrong box.
                already_queued = any(
                    c.get("cmd") == "memorialize" and c.get("key") == key
                    for c in s.queued_commands.get(player_id, [])
                )
                if not already_queued:
                    log.warning(
                        f"[{player_id}] ⚠ DEAD mon in regular box {box}: {nick} [{key[:8]}] "
                        f"(slot {bentry.get('slot', '?')}) — re-queuing memorialize"
                    )
                    s._queue_memorialize(player_id, key)

    def _get_party_ordered(self, pid: str) -> list:
        """Return monKeys for player `pid` sorted by party slot order.

        Uses the ``slot`` field stored in party_details (populated from the Lua
        snapshot's ``slot=i`` field).  Falls back to 999 for old clients that
        don't send slot info, which puts them at the end in original order.
        """
        pd = self.party_details.get(pid, {})
        return sorted(pd.keys(), key=lambda k: pd[k].get("slot", 999))

    def _resolve_level(self, player_id: str, mi) -> int:
        """Return the best available level for a MonInfo.

        Falls back through mon_stats cache and party_details when the stored
        level is 0 (e.g. manual links that didn't capture level at creation).
        """
        if not mi:
            return 0
        if mi.level:
            return mi.level
        # Try mon_stats cache (set when mon was deposited to box)
        cached = self.state.mon_stats[player_id].get(mi.key)
        if cached and cached.get("level"):
            return cached["level"]
        # Try live party_details from either player
        for pid in ("a", "b"):
            det = self.party_details.get(pid, {}).get(mi.key)
            if det and det.get("level"):
                return det["level"]
        return 0

    def _lookup_mon_detail(self, player_id: str, key: str) -> dict:
        """Look up mon details from party_details first, then pc_boxes.

        Enriches with level from mon_stats cache or the link entry when the
        primary source (e.g. pc_boxes) doesn't carry it.
        """
        det = self.party_details.get(player_id, {}).get(key)
        if det:
            return det
        result: dict = {}
        for bentry in self.pc_boxes.get(player_id, []):
            if bentry.get("key") == key:
                result = dict(bentry)
                break
        if not result:
            # Check pending_captures as last resort
            for _area, players in self.state.pending_captures.items():
                mon = players.get(player_id)
                if mon and mon.key == key:
                    return {"nickname": mon.nickname, "species_id": mon.species, "level": mon.level}
        # Enrich with level from mon_stats cache or existing link entry
        if result and not result.get("level"):
            cached = self.state.mon_stats[player_id].get(key)
            if cached and cached.get("level"):
                result["level"] = cached["level"]
            else:
                link_entry = self.state._key_index.get(key)
                if link_entry:
                    mi = link_entry.a if link_entry.a and link_entry.a.key == key else link_entry.b
                    if mi and mi.level:
                        result["level"] = mi.level
        # Also check the OTHER player's party_details (for solo-testing with same OT)
        if result and not result.get("level"):
            other = "b" if player_id == "a" else "a"
            other_det = self.party_details.get(other, {}).get(key)
            if other_det and other_det.get("level"):
                result["level"] = other_det["level"]
        return result

    def _find_pending_area_for_key(self, player_id: str, key: str) -> str | None:
        """Return the area_id where this key has a pending capture, or None."""
        for area, players in self.state.pending_captures.items():
            mon = players.get(player_id)
            if mon and mon.key == key:
                return area
        return None

    @_requires_loaded_state
    async def handle_inject_link_api(self, request):
        """POST /api/inject_link — manually create a linked pair.

        Body (JSON): {"a_key": "...", "b_key": "...", "area_id": "route_1",
                      "force": false}

        Looks up mon info from party, box, AND pending_captures.
        If either mon has a pending capture on a different area than specified
        and force is not set, returns a warning with requires_force=true.
        On success, cleans up pending_captures and updates area_states.
        """
        try:
            body = await request.json()
        except Exception:
            return aiohttp_web.json_response({"ok": False, "error": "invalid JSON"}, status=400)
        a_key  = body.get("a_key", "").strip()
        b_key  = body.get("b_key", "").strip()
        area   = body.get("area_id", "manual").strip()
        force  = body.get("force", False)
        override = body.get("override", False)
        if not a_key or not b_key:
            return aiohttp_web.json_response(
                {"ok": False, "error": "a_key and b_key are required"}, status=400)

        from server.state import AreaStatus, LinkEntry, LinkStatus, MonInfo

        s = self.state

        # If override requested, unlink existing entries for these keys
        if override:
            if any(s.link_in_trade(s.entry_for(pid, k)) for pid, k in (("a", a_key), ("b", b_key))):
                return aiohttp_web.json_response(
                {"ok": False, "error": "This pair is in a trade. Settle the trade first."}, status=409)
            for pid, key_to_free in (("a", a_key), ("b", b_key)):
                old_entry = s.entry_for(pid, key_to_free)
                if old_entry:
                    s.unindex_entry(old_entry)
                    if old_entry in s.links:
                        s.links.remove(old_entry)
                    old_area = old_entry.area_id
                    if old_area and old_area != area and old_area not in s.pending_captures:
                        s.area_states[old_area] = AreaStatus.UNSEEN
                    log.info(f"[inject_link] override: removed old link on {old_area}")

        # Reject already-linked keys (unless override already cleared them)
        if s.entry_for("a", a_key) is not None:
            return aiohttp_web.json_response(
                {"ok": False, "error": f"Player A mon {a_key[:8]} is already linked."}, status=400)
        if s.entry_for("b", b_key) is not None:
            return aiohttp_web.json_response(
                {"ok": False, "error": f"Player B mon {b_key[:8]} is already linked."}, status=400)

        # Detect pending-area conflicts
        a_pend_area = self._find_pending_area_for_key("a", a_key)
        b_pend_area = self._find_pending_area_for_key("b", b_key)
        conflicts = []
        if a_pend_area and a_pend_area != area:
            a_det = self._lookup_mon_detail("a", a_key)
            a_name = a_det.get("nickname") or f"#{a_det.get('species_id', '?')}"
            conflicts.append(f"Player A's {a_name} is pending on {self.adapter.area_display_name(a_pend_area)}")
        if b_pend_area and b_pend_area != area:
            b_det = self._lookup_mon_detail("b", b_key)
            b_name = b_det.get("nickname") or f"#{b_det.get('species_id', '?')}"
            conflicts.append(f"Player B's {b_name} is pending on {self.adapter.area_display_name(b_pend_area)}")
        if conflicts and not force:
            return aiohttp_web.json_response({
                "ok": False, "requires_force": True,
                "conflicts": conflicts,
                "error": " | ".join(conflicts) + " — use Link anyway to override.",
            })

        # Pull mon info from party, box, or pending_captures
        a_det = self._lookup_mon_detail("a", a_key)
        b_det = self._lookup_mon_detail("b", b_key)
        entry = LinkEntry(
            area_id=area,
            a=MonInfo(key=a_key,
                      nickname=a_det.get("nickname", ""),
                      species=a_det.get("species_id", 0),
                      level=a_det.get("level", 0)),
            b=MonInfo(key=b_key,
                      nickname=b_det.get("nickname", ""),
                      species=b_det.get("species_id", 0),
                      level=b_det.get("level", 0)),
            status=LinkStatus.ALIVE,
        )
        s.links.append(entry)
        s._index_entry(entry)
        s.area_states[area] = AreaStatus.LINKED
        # A manual link resolves the area just like normal pair formation. Leaving
        # its first-species lock behind renders a contradictory "Seeking ..." row
        # on an already-linked area.
        s.encounter_locks.pop(area, None)
        s.retry_areas["a"].discard(area)
        s.retry_areas["b"].discard(area)

        # Clean up pending_captures for both mons
        for pid, _key, pend_area in [("a", a_key, a_pend_area), ("b", b_key, b_pend_area)]:
            if pend_area and pend_area in s.pending_captures:
                s.pending_captures[pend_area].pop(pid, None)
                if not s.pending_captures[pend_area]:
                    del s.pending_captures[pend_area]
                # Recompute area_state for the old pending area (if different from link area)
                if pend_area != area:
                    remaining = s.pending_captures.get(pend_area, {})
                    if not remaining:
                        # No one pending here anymore — revert to unseen
                        s.area_states[pend_area] = AreaStatus.UNSEEN
                    elif "a" in remaining and "b" not in remaining:
                        s.area_states[pend_area] = AreaStatus.PENDING_B
                    elif "b" in remaining and "a" not in remaining:
                        s.area_states[pend_area] = AreaStatus.PENDING_A
            # Also clean pending on the link area itself
            if area in s.pending_captures:
                s.pending_captures[area].pop(pid, None)
                if not s.pending_captures[area]:
                    del s.pending_captures[area]

        # Only add to party_keys if the mon is actually in the party
        if a_key in self.party_details.get("a", {}):
            s.party_keys["a"].add(a_key)
        if b_key in self.party_details.get("b", {}):
            s.party_keys["b"].add(b_key)

        for pid in ("a", "b"):
            s._queue_resolved_areas(pid)
        s._save()
        a_name = a_det.get("nickname") or a_key[:8]
        b_name = b_det.get("nickname") or b_key[:8]
        log.info(f"[inject_link] A:{a_name}({a_key[:8]}) <-> B:{b_name}({b_key[:8]}) area={area}")
        self._notify_sse()
        return aiohttp_web.json_response({
            "ok": True,
            "a_key": a_key, "b_key": b_key, "area_id": area,
            "message": f"Linked {a_name} <-> {b_name} on {self.adapter.area_display_name(area)}.",
        })

    @_requires_loaded_state
    async def handle_inject_link_by_slot_api(self, request):
        """POST /api/inject_link_by_slot — link party slots without knowing the keys.

        Body (JSON): {"a_slot": 0, "b_slot": 0, "area_id": "test"}
        Resolves slot indices to keys, then delegates to handle_inject_link_api.
        """
        try:
            body = await request.json()
        except Exception:
            return aiohttp_web.json_response({"ok": False, "error": "invalid JSON"}, status=400)

        a_slot = int(body.get("a_slot", 0))
        b_slot = int(body.get("b_slot", 0))

        s = self.state
        a_keys = sorted(s.party_keys.get("a", set()))
        b_keys = sorted(s.party_keys.get("b", set()))

        if not a_keys:
            return aiohttp_web.json_response(
                {"ok": False, "error": "No party keys known for player A."}, status=400)
        if not b_keys:
            return aiohttp_web.json_response(
                {"ok": False, "error": "No party keys known for player B."}, status=400)
        if a_slot >= len(a_keys):
            return aiohttp_web.json_response(
                {"ok": False, "error": f"a_slot {a_slot} out of range (A has {len(a_keys)} party mons)"}, status=400)
        if b_slot >= len(b_keys):
            return aiohttp_web.json_response(
                {"ok": False, "error": f"b_slot {b_slot} out of range (B has {len(b_keys)} party mons)"}, status=400)

        # Build a fake request that delegates to inject_link
        resolved = {
            "a_key": a_keys[a_slot], "b_key": b_keys[b_slot],
            "area_id": body.get("area_id", "manual"),
            "force": body.get("force", False),
        }
        class _Req:
            async def json(self_): return resolved
        return await self.handle_inject_link_api(_Req())


# ── entrypoint ─────────────────────────────────────────────────────────────────


def build_app(srv):
    """Build the aiohttp app with every route registered.

    Extracted from main() so tests can construct the SAME app and walk the SAME route
    table — a test that re-declared the routes would drift the moment one was added here.
    """
    app = aiohttp_web.Application(middlewares=[csrf_protection, theme_cache])
    setup_templating(app)
    app.router.add_get("/",            srv.handle_status_html)
    app.router.add_get("/timeline",    srv.handle_timeline_html)
    app.router.add_get("/api/status",  srv.handle_status_json)
    app.router.add_get("/api/events",  srv.handle_sse)
    app.router.add_post("/api/reset",              srv.handle_reset_api)
    app.router.add_post("/api/inject_link",        srv.handle_inject_link_api)
    app.router.add_post("/api/inject_link_by_slot", srv.handle_inject_link_by_slot_api)
    # Stream overlay routes
    app.router.add_get("/stream",          srv.handle_stream_index)
    app.router.add_get("/stream/",         srv.handle_stream_index)
    # Every overlay page and its fragment, from one table. Registered per slug so the
    # router (and the route smoke test that walks it) still lists each one.
    for _slug in SLinkServer.STREAM_OVERLAYS:
        app.router.add_get(f"/stream/{_slug}",
                           functools.partial(srv.handle_stream_overlay, slug=_slug))
        app.router.add_get(f"/stream/{_slug}/fragment",
                           functools.partial(srv.handle_stream_overlay, slug=_slug, fragment=True))
    app.router.add_post("/api/attempts",   srv.handle_api_attempts)
    app.router.add_get("/launcher/{player}", srv.handle_launcher)
    # Twitch bot routes
    app.router.add_get("/twitch",               srv.handle_twitch_page)
    app.router.add_get("/api/bot/status",       srv.handle_bot_status)
    app.router.add_post("/api/bot/config",      srv.handle_bot_config)
    app.router.add_post("/api/bot/reload",      srv.handle_bot_reload)
    app.router.add_post("/api/bot/enable",      srv.handle_bot_enable)
    app.router.add_post("/api/bot/disable",     srv.handle_bot_disable)
    app.router.add_post("/api/bot/preview",     srv.handle_bot_preview)
    # OBS scene trigger routes
    app.router.add_get("/obs",                  srv.handle_obs_page)
    app.router.add_get("/api/obs/status",       srv.handle_obs_status)
    app.router.add_post("/api/obs/config",      srv.handle_obs_config)
    app.router.add_post("/api/obs/triggers",    srv.handle_obs_triggers)
    app.router.add_get("/api/obs/scenes/{player}", srv.handle_obs_scenes)
    app.router.add_get("/api/obs/areas",        srv.handle_obs_areas)
    app.router.add_post("/api/obs/test",        srv.handle_obs_test)
    app.router.add_post("/api/obs/connect",     srv.handle_obs_connect)
    app.router.add_post("/api/obs/disconnect",  srv.handle_obs_disconnect)
    # Debug routes
    app.router.add_get("/debug",                       srv.handle_debug_html)
    app.router.add_get("/api/debug/raw_state",         srv.handle_debug_raw_state)
    app.router.add_get("/api/debug/manual_link_data",  srv.handle_debug_manual_link_data)
    app.router.add_post("/api/debug/inject_event",     srv.handle_debug_inject_event)
    app.router.add_post("/api/debug/queue_command",    srv.handle_debug_queue_command)
    app.router.add_post("/api/debug/set_pokeballs",    srv.handle_debug_set_pokeballs)
    app.router.add_post("/api/debug/set_area_state",   srv.handle_debug_set_area_state)
    app.router.add_post("/api/debug/clear_pending",    srv.handle_debug_clear_pending)
    app.router.add_post("/api/debug/unlink",            srv.handle_debug_unlink)
    app.router.add_post("/api/debug/revive",            srv.handle_debug_revive)
    app.router.add_post("/api/debug/resolve_trade",     srv.handle_debug_resolve_trade)
    app.router.add_post("/api/debug/resolve_ambiguous_key", srv.handle_debug_resolve_ambiguous_key)
    app.router.add_get("/api/debug/backups",            srv.handle_debug_list_backups)
    app.router.add_post("/api/debug/rollback",          srv.handle_debug_rollback)
    # RR Damage Calculator routes
    app.router.add_get("/calc",           srv.handle_calc_redirect)
    app.router.add_get("/calc/",          srv.handle_calc_redirect)
    app.router.add_get("/calc/{path:.*}", srv.handle_calc_files)
    app.router.add_get("/api/calc/mons",  srv.handle_calc_mons)

    from server.patcher import setup_patcher_routes
    def _patcher_chrome(request):
        srv._to_manager(request, "/patcher")
        return {"sidebar_html": srv._build_sidebar_html(request, "tools"), "body_class": "board mgr",
                "mgr": True, "is_stream": False, "hide_chrome": False}
    setup_patcher_routes(app, _patcher_chrome)
    return app


async def main(host: str, port: int, http_port: int, reset: bool = False,
               data_dir: str = None, run_id: str = None, run_name: str = "",
               species_lock: bool = False, gender_lock: bool = False,
               type_lock: bool = False, explode_mode: bool = False,
               rival_team_swap: bool = False, overworld_presence: bool = False,
               native_messages: bool = False, native_sounds: bool = False,
               battle_calc: bool = True, pc_trade_npc: bool = True, phone_calls: bool = True,
               retry_same_species: bool = True,
               ignore_wild_faints: bool = False,
               manager_port: int = 0, verbose: bool = False,
               wire_log: str = None):
    _configure_logging(data_dir, verbose)
    if reset:
        links_path = os.path.join(data_dir, "links.json") if data_dir else LINKS_PATH
        if os.path.exists(links_path):
            os.remove(links_path)
            log.warning("⚠  --reset: links.json deleted. Starting a fresh run.")
        else:
            log.info("--reset: no existing state found, starting fresh.")
    srv = SLinkServer(data_dir=data_dir, run_id=run_id, run_name=run_name,
                      tcp_port=port, manager_port=manager_port,
                      species_lock=species_lock, gender_lock=gender_lock,
                      type_lock=type_lock, explode_mode=explode_mode,
                      rival_team_swap=rival_team_swap,
                      overworld_presence=overworld_presence,
                      native_messages=native_messages,
                      native_sounds=native_sounds,
                      battle_calc=battle_calc,
                      pc_trade_npc=pc_trade_npc,
                      phone_calls=phone_calls,
                      retry_same_species=retry_same_species,
                      ignore_wild_faints=ignore_wild_faints,
                      wire_log=wire_log)

    # TCP game server.
    # limit=4 MiB lifts asyncio's default 64 KiB readline buffer so Gen 5's
    # full party+box hello payload (24 boxes × 30 slots × ~150 bytes JSON ≈ 110 KiB)
    # doesn't trip LimitOverrunError and force the connection closed each tick.
    # Gen 4 (18 boxes) and Gen 1-3 (smaller PCs) fit under the old limit but get
    # the bigger headroom for free.
    try:
        tcp_server = await asyncio.start_server(srv.handle_client, host, port,
                                                limit=4 * 1024 * 1024)
    except OSError as e:
        # A bare traceback here reads as "SLink is broken" when it almost always means the port
        # is taken — usually a server the user forgot they left running.
        raise SystemExit(
            f"\nCannot listen on TCP {host}:{port} — {e}\n"
            "  Another SLink server is probably already running.\n"
            f"  Use a different port:  python -m server.server --port {port + 10}\n"
        ) from None
    addrs = ", ".join(str(s.getsockname()) for s in tcp_server.sockets)
    run_label = f" [{run_id}]" if run_id else ""
    log.info(f"SLink{run_label} TCP server listening on {addrs}")

    # Start rolling backup task
    srv.start_backup_task()
    # Start OBS worker tasks
    srv.obs.start_workers()

    # HTTP status page
    if AIOHTTP_AVAILABLE:
        app = build_app(srv)
        runner = aiohttp_web.AppRunner(app)
        await runner.setup()
        http_site = aiohttp_web.TCPSite(runner, host, http_port)
        try:
            await http_site.start()
        except OSError as e:
            await runner.cleanup()
            tcp_server.close()
            raise SystemExit(
                f"\nCannot listen on HTTP {host}:{http_port} — {e}\n"
                "  The dashboard port is in use (the Run Manager uses 8090, and the runs\n"
                "  it spawns start at 8081).\n"
                f"  Use a different port:  python -m server.server --http-port {http_port + 10}\n"
            ) from None
        # Start Twitch bot if configured
        await srv._restart_bot()
        log.info(f"SLink{run_label} status page at http://{host if host != '0.0.0.0' else 'localhost'}:{http_port}/")
    else:
        log.warning("aiohttp not installed — HTTP status page disabled. Run: pip install aiohttp")
        runner = None

    async with tcp_server:
        await tcp_server.serve_forever()

    if runner:
        await runner.cleanup()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SLink Soul Link server")
    parser.add_argument("--host",      default="0.0.0.0",  help="Bind address (default: 0.0.0.0)")
    parser.add_argument("--port",      type=int, default=54321, help="TCP port (default: 54321)")
    parser.add_argument("--http-port", type=int, default=8080,  help="HTTP status port (default: 8080)")
    parser.add_argument("--reset",     action="store_true",     help="Clear all saved state and start a fresh run")
    parser.add_argument("--data-dir",  default=None,            help="Data directory for links/memorial JSON (default: data/)")
    parser.add_argument("--run-id",    default=None,            help="Optional run label (used in log output)")
    parser.add_argument("--run-name",  default="",              help="Human-readable run name (shown in page title)")
    parser.add_argument("--species-clause", action="store_true", dest="species_lock", help="Reject links where both mons share the same evolution family")
    parser.add_argument("--gender-clause",  action="store_true", dest="gender_lock",  help="Reject links where both mons share the same gender")
    parser.add_argument("--type-clause",    action="store_true", dest="type_lock",    help="Reject links where both mons share any type")
    parser.add_argument("--dead-zone-on-miss", action="store_false", dest="retry_same_species",
        help="Close an area after a failed encounter instead of retrying its first species")
    parser.add_argument("--ignore-wild-faints", action="store_true", dest="ignore_wild_faints",
        help="Do not retire linked pairs for faints or whiteouts during wild battles")
    parser.add_argument("--explode-mode",   action="store_true", dest="explode_mode", help="On partner death, force the linked mon to auto-Explode (every game except the Emerald Expansion)")
    parser.add_argument("--rival-team-swap", action="store_true", dest="rival_team_swap",
        help="On rival battles, replace the rival's team with the partner's current party (Gen 1, pureRGB, Gen 2, Radical Red)")
    parser.add_argument("--overworld-presence", action="store_true", dest="overworld_presence",
        help="Peer ghost: render your partner walking your overworld as a live NPC (RR + companion patch required; opt-in per-run rule)")
    parser.add_argument("--native-messages", action="store_true", dest="native_messages",
        help="Show SLink notifications via the companion patch's native message box / in-battle text instead of the Lua HUD overlay (RR + patch; default off)")
    parser.add_argument("--native-sounds", action="store_true", dest="native_sounds",
        help="Play SLink notification sounds via the companion patch's native PlaySE (RR + patch; default off)")
    parser.add_argument("--no-battle-calc", action="store_false", dest="battle_calc",
        help="Hide the bundled Battle Calc damage display (RR + patch; shown by default)")
    parser.add_argument("--no-pc-trade-npc", action="store_false", dest="pc_trade_npc",
        help="Disable the Pokémon-Center trade NPC (RR + patch; on by default, only active while overworld presence is off)")
    parser.add_argument("--no-phone-calls", action="store_false", dest="phone_calls",
        help="Gen 2 + companion: do not ring a Pokégear call for first link / dead zone / fallen (on by default)")
    parser.add_argument("--manager-port", type=int, default=0,   help="Manager HTTP port (enables 'Run Manager' link on status page)")
    parser.add_argument("--verbose",      action="store_true",   help="Enable DEBUG-level logging to file and console (default: INFO only)")
    parser.add_argument("--wire-log",     default=None, metavar="DIR",
                        help="Capture every TCP line to DIR/wire_<player>.jsonl (debug/characterization)")
    parser.add_argument("--allow-host",   action="append", default=[], metavar="NAME",
                        help="Extra Host name the web UI answers to, e.g. a tunnel name or '*.<tailnet>.ts.net' (repeatable; also SLINK_ALLOWED_HOSTS)")
    args = parser.parse_args()
    allow_hosts(args.allow_host)
    asyncio.run(main(args.host, args.port, args.http_port, args.reset, args.data_dir, args.run_id,
                     run_name=args.run_name,
                     species_lock=args.species_lock, gender_lock=args.gender_lock,
                     type_lock=args.type_lock,
                     explode_mode=args.explode_mode,
                     rival_team_swap=args.rival_team_swap,
                     overworld_presence=args.overworld_presence,
                     native_messages=args.native_messages,
                     native_sounds=args.native_sounds,
                     battle_calc=args.battle_calc,
                     pc_trade_npc=args.pc_trade_npc,
                     phone_calls=args.phone_calls,
                     retry_same_species=args.retry_same_species,
                     ignore_wild_faints=args.ignore_wild_faints,
                     manager_port=args.manager_port,
                     verbose=args.verbose,
                     wire_log=args.wire_log))
