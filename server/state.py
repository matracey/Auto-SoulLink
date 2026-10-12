"""
server/state.py — SoulLinkState: Soul Link Nuzlocke finite state machine.

Processes events from both BizHawk instances, updates shared Soul Link state,
and emits commands back to each game.

Commands for the requesting player are returned immediately.
Commands for the partner are queued and delivered on the partner's next event.

Persistence:
    data/links.json   — link table + area states (written on every state change)
    data/memorial.json — Phase 4 (retired pair log)

When `data_dir` is passed to SoulLinkState() / SoulLinkState.load(), those files
are written inside `data_dir` instead of the global DATA_DIR.  The manager uses
this to give each run its own isolated data directory.
"""

import json
import logging
import os
import time
from collections import Counter, deque
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from enum import Enum

from server import json_files
from server.adapters.base import GameRulesAdapter, humanize_area_id
from server.pokemon_data import _parse_pid_otid_key, pid_otid_shiny

log = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
LINKS_PATH    = os.path.join(DATA_DIR, "links.json")
MEMORIAL_PATH = os.path.join(DATA_DIR, "memorial.json")

# The commands that mean "your linked partner just died".  Explode Mode swaps one for
# the other (_propagate_faint), so everything downstream — event log, OBS triggers,
# overlay filters — must accept BOTH.  Matching the bare string "force_faint" is how
# Explode Mode kills came to be invisible in the dashboard and to OBS.
DEATH_COMMANDS = ("force_faint", "force_explode")


class UnsafeGameMigration(RuntimeError):
    """A persisted run's adapter cannot be safely carried over after a rom_type cutover.

    Raised by SoulLinkState.load(); never caught by its own broad except-and-log so it
    reaches the operator starting the server, not a connecting client.
    """

# The party/box sync commands whose effect the server cannot observe until the client
# answers.  Tracked from delivery to answer in SoulLinkState.sync_inflight so the drift
# reconciler never acts on a key whose command is still on the wire.
SYNC_COMMANDS = ("party_mon", "box_mon", "memorialize")
# Persisted while undelivered (memorialize has its own durable record, pending_memorials).
DURABLE_SYNC_COMMANDS = ("party_mon", "box_mon")

# KEY-SCOPE-5: accepted key migrations remembered per player (replay idempotence and stale-snapshot
# canonicalization). The count, not a clock, is the guarantee window.
KEY_MIGRATION_LEDGER_LIMIT = 256
# The Nth reconciler pass after delivery is the first that may re-issue a command; passes 1..N-1
# suppress it (adapter-guard review: the count is a re-issue window, not an ack deadline).
# The unit is deliberately the reconciler's own passes, not wall-clock and not client
# events: the reconciler is the only reader of sync_inflight, so counting its passes
# makes the window mean "give the client this many chances to answer before deciding
# it drifted", identical on every client whatever its event cadence or tick rate.
#
# This bounds RE-ISSUE SUPPRESSION, not ack arrival.  An answer may legitimately come
# long after the window closes — a client is free to defer a command until the game is
# in a state where it can run it (Gen 1 executes party_mon only in the overworld) — in
# which case the reconciler may re-issue and the client's already-in-party path absorbs
# the duplicate.  The window ages out rather than latching so that a genuinely dropped
# answer cannot disable drift repair for that key for the rest of the run.
SYNC_INFLIGHT_RECONCILES = 6
# Owner ruling O-24: DEATH_COMMANDS have no ack, so a lost one is repaired from the party
# snapshot instead (_repair_lost_faints).  Unit: out-of-battle reconciler passes, one per
# tick; every Gen 1/2/3 client ticks every 30 frames (~0.5 s).
#
# FAINT_REPAIR_RECONCILES is the re-issue window after a death command is delivered.  It is
# long (~60 s) on purpose: Gen 1 and Gen 2 hold a delivered force_faint OUT of battle until
# their overworld checkpoint passes, which a player in the PC, a menu or a script can keep
# closed for a long time.  A short window would spend the budget on a client that is merely
# waiting.  ~60 s is above any ordinary hold; a longer one costs at most one idempotent
# duplicate per window, never a wrong kill.
FAINT_REPAIR_RECONCILES = 120
# Re-issues per incident before the repair stalls (logged as an error, and listed on the
# status payload as faint_repair_stalled) ...
FAINT_REPAIR_BUDGET = 3
# ... and the stall is a pause, not a verdict: after this cool-down (~5 min) the budget is
# refilled, so a genuine loss later on (a client that wiped its deferred queue) is still
# repaired.  A key is never permanently disabled.
FAINT_REPAIR_COOLDOWN = 600


# Not StrEnum: these values are interpolated into JSON, templates and log lines,
# where StrEnum's str()/format() differ from the (str, Enum) behaviour relied on here.
class AreaStatus(str, Enum):  # noqa: UP042
    UNSEEN      = "unseen"
    PENDING_A   = "pending_a"    # A captured/entered; B not yet resolved
    PENDING_B   = "pending_b"    # B captured/entered; A not yet resolved
    PENDING_BOTH = "pending_both"  # both entered, neither resolved yet
    LINKED      = "linked"
    DEAD_ZONE   = "dead_zone"


class LinkStatus(str, Enum):  # noqa: UP042  # see AreaStatus above
    ALIVE    = "alive"
    DEAD     = "dead"
    MEMORIAL = "memorial"  # Phase 4: moved to Box 13


@dataclass
class MonInfo:
    key: str
    level: int = 0
    species: int = 0
    nickname: str = ""
    is_shiny: bool = False


@dataclass
class EncounterLock:
    species: int
    level: int = 0


@dataclass
class LinkEntry:
    area_id: str
    a: MonInfo | None  # Player A's mon; None if A didn't capture
    b: MonInfo | None  # Player B's mon; None if B didn't capture
    status: LinkStatus
    encounter_a: MonInfo | None = None  # A's wild encounter even if not caught (display only)
    encounter_b: MonInfo | None = None  # B's wild encounter even if not caught (display only)
    # Killfeed fields — set at death time, None/empty until the pair dies
    killed_at: str | None = None          # ISO 8601 UTC timestamp
    cause: str = ""                          # "battle", "dead_zone", or "whiteout"
    killer: dict | None = None            # {species, level, is_trainer, trainer_name} (battle only)
    initiating_player: str = ""              # "a" or "b" — whose action triggered the pair death


class _KeyIndexView(Mapping):
    """Read-only any-player view of the per-player key index (A's entry first). For display
    and diagnostics only: rules code uses SoulLinkState.entry_for(player_id, key)."""

    def __init__(self, pidx):
        self._p = pidx

    def __getitem__(self, key):
        for pid in ("a", "b"):
            if key in self._p[pid]:
                return self._p[pid][key]
        raise KeyError(key)

    def __iter__(self):
        return iter(self._p["a"].keys() | self._p["b"].keys())

    def __len__(self):
        return len(self._p["a"].keys() | self._p["b"].keys())


def _partner(player_id: str) -> str:
    return "b" if player_id == "a" else "a"


def is_shiny(key: str) -> bool:
    """Determine if a mon is shiny from its personality:otId key (Gen III formula).

    NOTE: This is a standalone utility kept for backward compatibility with tests.
    The state machine uses self.adapter.is_shiny() at runtime for game-appropriate logic.
    """
    parsed = _parse_pid_otid_key(key)
    if parsed is None:
        return False
    return pid_otid_shiny(*parsed)


def _valid_battle_identity(session, battle_id) -> bool:
    """The ONE strict validator for the battle request identity (card C5-10, hardened per Codex's
    C5-10 review): a hex nonce of 1..16 characters and an integer counter in 1..2**32-1, never a
    bool. Both the event path and the queue helper call this, so the two can never disagree about
    what a valid pair is, and nothing is ever coerced into validity."""
    if not isinstance(session, str) or not 1 <= len(session) <= 16:
        return False
    if any(c not in "0123456789abcdefABCDEF" for c in session):
        return False
    return (isinstance(battle_id, int) and not isinstance(battle_id, bool)
            and 1 <= battle_id < 2 ** 32)


class SoulLinkState:
    def __init__(self, data_dir: str = None, species_lock: bool = False, gender_lock: bool = False, type_lock: bool = False, explode_mode: bool = False, is_rr: bool = False, adapter: GameRulesAdapter = None, rival_team_swap: bool = False, overworld_presence: bool = False, native_messages: bool = False, native_sounds: bool = False, battle_calc: bool = True, pc_trade_npc: bool = True, phone_calls: bool = True, retry_same_species: bool = True, ignore_wild_faints: bool = False):
        # When data_dir is provided (manager mode) use it; otherwise fall back to the
        # module-level globals so monkeypatch works in tests and the standalone server
        # keeps working unchanged.
        self._data_dir      = data_dir if data_dir else DATA_DIR
        self._links_path    = os.path.join(self._data_dir, "links.json") if data_dir else LINKS_PATH
        self._memorial_path = os.path.join(self._data_dir, "memorial.json") if data_dir else MEMORIAL_PATH
        # Whether the connected clients are Radical Red / CFRU (True) or vanilla/AP (False).
        # Controls species ID interpretation — CFRU IDs vs National Dex.
        self.is_rr: bool = is_rr
        # Game adapter — provides game-specific rule logic.
        # Default to Gen3Adapter for backward compatibility.
        if adapter is not None:
            self.adapter = adapter
        else:
            from server.adapters.gen3_frlge import Gen3Adapter
            self.adapter = Gen3Adapter(is_rr=is_rr)
        self.links: list[LinkEntry] = []
        self.area_states: dict[str, AreaStatus] = {}
        # area_id → {player_id → MonInfo} for captures not yet linked
        self.pending_captures: dict[str, dict[str, MonInfo]] = {}
        # area_id → {player_id → MonInfo} for a surviving half whose partner was lost
        # outside the tracked run state. These are display-only and never participate
        # in quarantine, death propagation, or party synchronization.
        self.unpaired_mons: dict[str, dict[str, MonInfo]] = {}
        # area_id -> player_id -> the first eligible species that player must catch.
        self.encounter_locks: dict[str, dict[str, EncounterLock]] = {}
        # KEY-SCOPE: {player: {monKey: LinkEntry}}. A mon's identity is scoped per player: two
        # players may hold equal keys (an in-game NPC-trade or fixed-DV gift mon), one may not.
        # entry_for() is the lookup; _key_index is a read-only any-player view for display.
        self._pidx: dict[str, dict[str, LinkEntry]] = {"a": {}, "b": {}}
        # commands queued for delivery to each player on their next request
        self.queued_commands: dict[str, list[dict]] = {"a": [], "b": []}
        # SYNC_COMMANDS that have been DELIVERED but not yet answered, as
        # {player: {(monKey, cmd): reconciler_passes_remaining}}.  queued_commands empties the
        # instant a command ships, so without this the window between delivery and the client's
        # answer looks like state drift and _reconcile_party_keys issues the command a second
        # time.  Armed on delivery, cleared by any client event naming the key (every answer
        # carries one), counted down by the reconciler itself.  In-memory only: after a restart
        # nothing of ours is on the wire, and the hello path re-queues what is still outstanding.
        self.sync_inflight: dict[str, dict[tuple[str, str], int]] = {"a": {}, "b": {}}
        # DEATH_COMMANDS delivered to each player, as {player: {monKey: reconciler_passes_remaining}}.
        # Shaped like sync_inflight, but FAINT_REPAIR_RECONCILES long, there is no answer to clear
        # it, and it does not count down during a battle (the client holds an active battler's
        # faint until battle end).
        self.death_inflight: dict[str, dict[str, int]] = {"a": {}, "b": {}}
        # O-24 re-issues spent per key ({player: {monKey: n}}); cleared when the key reads HP 0.
        self.faint_repairs: dict[str, dict[str, int]] = {"a": {}, "b": {}}
        # Keys whose repair budget is spent, as {player: {monKey: cool-down passes remaining}}.
        # On the status payload; the budget refills when the cool-down runs out.  All three are
        # in-memory only: reset/rollback build a fresh state, and a restart re-helloes.
        self.faint_repair_stalled: dict[str, dict[str, int]] = {"a": {}, "b": {}}
        # Last persistence error, or "" when the most recent save succeeded. Surfaced on the
        # dashboard: a run that has silently stopped saving looks identical to one that is fine.
        self.save_failed: str = ""
        self.load_failed: str = ""
        # set of monKeys known to be in each player's party right now
        self.party_keys: dict[str, set[str]] = {"a": set(), "b": set()}
        # cached party stats per monKey (populated by party_to_box events, echoed in party_mon)
        # KEY-SCOPE-2: {player: {monKey: stats}} -- two players may hold equal keys
        self.mon_stats: dict[str, dict[str, dict]] = {"a": {}, "b": {}}
        # True once the player has entered a non-gift encounter area (Pokéballs guaranteed).
        # no_catch and area state transitions are suppressed until this activates.
        self.pokeballs_obtained: dict[str, bool] = {"a": False, "b": False}
        # Committed ROM type — set once on first hello, persisted across restarts.
        self.rom_type: str = ""
        # Committed artifact kind (hello `artifact_kind`, default "clean") — set once like
        # rom_type; a later hello of another kind is refused as MIXED GAMES (PLAN A3).
        self.artifact_kind: str = ""
        # Presentation-side "is this key in use?" hook (server.py caches: pc_boxes,
        # party_details, _mon_cache) consulted by the key_change collision preflight.
        # Adapter-neutral: the rules layer never sees what the presentation layer stores.
        self.presentation_key_in_use = None
        # The last native-trade outcome (uncertain | committed | rolled_back | conflict), token-bound,
        # for the status page and oracles; on_trade_outcome(record) lets the server journal it.
        self.trade_last: dict | None = None
        self._trade_backlog: list[dict] = []   # a trade restored by load(), journaled once wired up
        self.on_trade_outcome = None
        # Title-sensitive acquisition policy; standalone states use their run adapter.
        # Species/evolution, gender/type, shiny and capability rules stay run-global:
        # admitted titles share those semantics, but their static map sites can differ.
        self.player_adapter_for = None
        # Committed trainer names — set once per player on first hello, static for run.
        self.trainer_names: dict[str, str] = {"a": "", "b": ""}
        # monKeys awaiting memorialize_done confirmation from each player
        self.pending_memorials: dict[str, set[str]] = {"a": set(), "b": set()}
        # tracks which players have sent at least one hello this session
        self._has_helld: set[str] = set()
        # Last-known party size for each player (from hello/tick party snapshots).
        # Used to check room for paired retrieval after link formation.
        self.party_size: dict[str, int] = {"a": 0, "b": 0}
        # Lock rules (opt-in, default off)
        self.species_lock: bool = species_lock
        self.gender_lock: bool = gender_lock
        self.type_lock: bool = type_lock
        self.retry_same_species: bool = retry_same_species
        self.ignore_wild_faints: bool = ignore_wild_faints
        # Explode Mode: when true, partner-faint propagation sends `force_explode`
        # to the surviving partner instead of `force_faint`.  The client's
        # force_explode handler runs the Variant-3 menu-skip path (auto-fires
        # Explosion via direct gActionForBanks/gChosenMovesByBanks/gBattleStruct
        # writes) for active battlers, or just M.forceFaint for bench mons.
        # When false, behaves identically to before (legacy "Faint pending switch").
        self.explode_mode: bool = explode_mode
        # Shiny Clause: always on.  Shiny captures are bonus mons outside the link system.
        # Tracked for dedup (prevent duplicate HUD/sound on replayed events).
        self.bonus_keys: dict[str, set[str]] = {"a": set(), "b": set()}
        # Pending bonus encounters: when a player catches a shiny, the partner gets
        # a slot in this FIFO queue.  Their next non-shiny capture becomes the bonus pair partner.
        self.pending_bonus: dict[str, deque[str]] = {"a": deque(), "b": deque()}
        # KEY-SCOPE-3: {player: {key: {"since", "refused"}}}. A trade-window clash left this
        # player holding two mons with one key; events naming it are refused until an admin
        # resolves it (resolve_ambiguous_key), because the server cannot tell which mon they mean.
        self.ambiguous_keys: dict[str, dict[str, dict]] = {"a": {}, "b": {}}
        # KEY-SCOPE-5: {player: {key: count}} of the latest party snapshot, taken BEFORE blob filtering;
        # the key_change self-report proof. In-memory only: a stale observation must never waive a
        # collision after a restart (every connection re-helloes).
        self.party_key_census: dict[str, dict[str, int]] = {}
        # T4: retained snapshots remain displayable while the client withholds RAM.
        self.party_hidden: dict[str, bool] = {"a": False, "b": False}
        # A declared durable-trade lease needs a later visible hello, not a tick.
        self.trade_recovery_pending: dict[str, bool] = {"a": False, "b": False}
        self.trade_recovery_errors: dict[str, str] = {"a": "", "b": ""}
        self.trade_finals: dict[str, dict[str, dict]] = {"a": {}, "b": {}}
        # KEY-SCOPE-5: {player: {key: slot}} of the latest and the previous party snapshot (in-memory)
        self.party_slots: dict[str, dict[str, int]] = {}
        self.prev_party_slots: dict[str, dict[str, int]] = {}
        # KEY-SCOPE-5 (cx-06ec4e8e F3): per player, snapshot number, and per key the snapshot its
        # current presence began in and the last snapshot it was seen in (in-memory)
        self.snapshot_no: dict[str, int] = {}
        self.key_streak: dict[str, dict[str, int]] = {}
        self.key_last_seen: dict[str, dict[str, int]] = {}
        # KEY-SCOPE-5: accepted {old_key, new_key} migrations per player, newest last. Persisted.
        self.key_migration_ledger: dict[str, deque] = {
            pid: deque(maxlen=KEY_MIGRATION_LEDGER_LIMIT) for pid in ("a", "b")}
        # True once the run is definitively over (no alive links, no pending captures).
        self.run_over: bool = False
        # Manual attempts counter (set by the user via the stream index page).
        self.attempts_count: int = 0
        # Player identity lock: {player_id: {"ot_id": str, "trainer_name": str}}
        # Set on first hello with a non-empty party; verified on subsequent hellos.
        self.player_identity: dict[str, dict] = {}
        # Rejection message for display on status page (cleared on successful hello).
        self.identity_error: dict[str, str] = {}
        # Areas where a species/lock clause rejected a capture — suppress no_catch
        # until the player successfully captures a valid mon on this area.
        self.retry_areas: dict[str, set[str]] = {"a": set(), "b": set()}
        # Areas where dupes clause was already notified at wild battle start.
        # Suppresses the duplicate gui_prompt on the subsequent no_catch event.
        # In-memory only — not persisted (tied to the current session).
        self.dupe_notified_areas: dict[str, set[str]] = {"a": set(), "b": set()}
        # Per-player auto-rebuild context, set when a whiteout fires and the
        # player still has alive linked pairs boxed; cleared once every queued
        # party_mon has been confirmed via sync_retrieve_done or dropped via
        # sync_retrieve_failed. Persisted so a mid-rebuild restart can re-arm.
        self.rebuild_pending: dict[str, dict | None] = {"a": None, "b": None}
        # Partner team blobs for the Rival Team Swap feature (Gen3/RR only,
        # adapter-gated).  Cached per player as a list of {slot, species_id,
        # level, key, blob} where blob is the raw 100-byte party-mon struct.
        # Sent by Lua on hello and whenever the local party signature changes
        # (composition or level deltas).  In-memory only; not persisted —
        # blobs are re-sent on every reconnect.
        self.partner_blobs: dict[str, list[dict]] = {"a": [], "b": []}
        # Latest battle request identity per player (card C5-10): (session nonce, battle
        # counter, trainer id) from the client's most recent `trainer_battle_start`, echoed on
        # the `replace_rival_team` commands that belong to that battle so the client can refuse
        # a late or mismatched one.  The nonce is minted per client PROCESS, so it is what makes
        # a restart safe: a restarted client's counter 1 is not the previous session's counter 1.
        # Runtime only, never persisted -- absent for Gen 1, Gen 2 and old Gen 3 clients, which
        # never send one, and gone after a server restart (a manual inject must then wait for a
        # fresh trainer_battle_start rather than reuse metadata it cannot trust).
        self.latest_battle_requests: dict[str, tuple[str, int, int]] = {}
        # Players whose client declared `battle_identity: true` in its hello (card C5-10b): only
        # for those may the server refuse an identity-less manual rival inject. Clients that never
        # declare it -- Gen 1, Gen 2, and the old Gen 3 RR client, which stays the production RR
        # client until the patch card lands -- keep the pre-card behaviour exactly.
        self.battle_identity_clients: dict[str, bool] = {}
        # Rival Team Swap auto-trigger.  Set once at run creation via the
        # `--rival-team-swap` CLI flag (mirrors --explode-mode).  When True,
        # the server auto-issues replace_rival_team on trainer_battle_start
        # whenever the trainer ID is in the adapter's rival set AND the
        # partner has cached blobs.  Per-run rule, never flipped at runtime.
        self.rival_team_swap: bool = rival_team_swap
        # Overworld Presence (peer ghost).  Set once at run creation via the
        # `--overworld-presence` CLI flag (mirrors --rival-team-swap).  When
        # True, the server relays each player's `ghost_pos` to the partner and
        # honours `peer_interact`, so the partner walks the overworld as a live
        # NPC.  When False, both are dropped server-side — no ghost ever spawns.
        # ROM-patch-required: the Lua side additionally self-gates on
        # patch_present(), so the feature is live only when this flag is ON AND
        # both players are patched.  Per-run rule, never flipped at runtime.
        self.overworld_presence: bool = False  # ponytail: forced off -- peer ghost deferred post-RC; ON disabled the PC trade NPC (docs/gen3/TODO.md, test_overworld_presence_deferred.py)
        # Native-enhancement toggles (ROM-patch features the Lua client can route either natively
        # or through its Lua fallback).  All four ride the same `config` command the client gets
        # on hello; none are flipped at runtime.  Unlike the opt-in run RULES above, these do not
        # change soullink semantics — they pick the rendering/audio/UX path:
        #  * native_messages (default OFF): field message box + in-battle notifications drawn by
        #    the patch instead of the Lua HUD overlay.  Off until A/B testing picks a winner.
        #  * native_sounds   (default OFF): notification sounds via the patch's PlaySE.
        #  * battle_calc     (default ON):  the bundled Battle Calc damage display.  Baked into the
        #    patched ROM; the client gates it via the patch's config byte (1 = disabled).
        #  * pc_trade_npc    (default ON):  the Pokémon-Center trade NPC.  Only EFFECTIVE while
        #    overworld_presence is OFF (the ghost replaces it as the trade entry point).
        # ponytail: native text is DISABLED for the Gen 3 RC (owner 2026-09-23, docs/gen3/TODO.md).
        # The flag, CLI option and saved rule are still accepted and ignored, so the code can be
        # re-enabled by deleting this override (here and in load()).
        self.native_messages: bool = False
        self.native_sounds: bool = native_sounds
        self.battle_calc: bool = battle_calc
        self.pc_trade_npc: bool = pc_trade_npc
        # phone_calls (default ON, owner 2026-10-02): Gen 2's companion rings a Pokégear call for
        # first link / dead zone / fallen. Its own switch, NOT native_messages (default off).
        self.phone_calls: bool = phone_calls
        # Talk-to-partner: each player's latest in-game progress (badge count), reported via the
        # `status` event; surfaced to the partner in the talk-to-partner box (the nuzlocke helper).
        self.player_badges: dict[str, int] = {"a": 0, "b": 0}
        # One in-flight linked-pair trade. {token, initiator, a_slot, b_slot, a_blob_hex, b_blob_hex,
        # a_key, b_key, a_label, b_label, link, confirms:{"a":None,"b":None}}. Both players confirm via
        # a native YES/NO menu before the faithful blob swap is applied.  None = no trade pending.
        self.pending_trade: dict | None = None
        self._trade_token: int = 0
        # MAJOR-1: clients that answer apply_prepare (hello trade_prepare: true); a trade takes the
        # prepare round only when BOTH declared it, so other clients keep the direct apply
        self.trade_prepare: dict[str, bool] = {"a": False, "b": False}
        # tick `trade_blocked`: the cartridge refuses any trade right now (Gen 2: the Bug-Catching
        # Contest masks the party, 9805ac1c SlinkTradeCheckParty), so no pair is eligible either way
        self.trade_blocked: dict[str, bool] = {"a": False, "b": False}
        # tick `awaiting_save` (BURIAL-VISIBLE): a GB client's boxed burial waits on an in-game SAVE
        self.awaiting_save: dict[str, bool] = {"a": False, "b": False}
        # Per-player tick countdown during which _reconcile_party_keys is suppressed after a trade
        # completes. A trade is a party↔party swap, so no box/party sync is ever legitimately needed
        # from it; but for a few ticks each client's party read is still settling, and the drift
        # reconciler would otherwise mis-read the freshly-received mon as "ghost-boxed" and queue a
        # spurious party_mon ("Unbox") to the partner. See _reconcile_party_keys / _handle_trade_done.
        self._trade_settle_ticks: dict[str, int] = {"a": 0, "b": 0}

    # ── public API ───────────────────────────────────────────────────────────

    def party_snapshot_withheld(self, player_id: str, msg: dict) -> bool:
        """Whether a snapshot is unavailable, independently of its attached data."""
        return self.adapter.supports_trade_recovery() and (
            msg.get("party_hidden") is True
            or msg.get("event") == "hello" and bool(self._trade_recovery_error(msg))
            or msg.get("event") == "hello" and bool(msg.get("trade_outstanding"))
            or self.trade_recovery_pending[player_id] and msg.get("event") != "hello"
            or self.party_hidden[player_id] and "party" not in msg)

    def _trade_recovery_error(self, msg: dict) -> str:
        if not self.adapter.supports_trade_recovery():
            return ""
        if "party_hidden" in msg and type(msg["party_hidden"]) is not bool:
            return "Invalid party_hidden: expected boolean"
        records = msg.get("trade_outstanding", [])
        if not isinstance(records, list) or any(
            not isinstance(r, dict) or not isinstance(r.get("token"), str) or not r["token"]
            or type(r.get("epoch")) not in (int, float) or not 1 <= r["epoch"] <= 0xFFFFFFFF
            or r["epoch"] != int(r["epoch"])
            for r in records
        ):
            return "Invalid trade_outstanding: expected token and nonzero uint32 epoch"
        return ""

    def _declare_trade_outstanding(self, player_id: str, records: list) -> None:
        """Record uncertainty after identity acceptance; a journal is never party evidence."""
        self.trade_recovery_pending[player_id] = True
        pt = self.pending_trade
        if not pt or pt.get("phase") not in ("applying", "uncertain", "conflict"):
            return
        epochs = [r["epoch"] for r in records if r["token"] == pt["token"]]
        if not epochs:
            return  # an older token cannot change this trade
        self.queued_commands[player_id] = [
            c for c in self.queued_commands[player_id]
            if not (c.get("cmd") in ("apply_trade", "apply_prepare")
                    and c.get("token") == pt["token"])]
        saved = pt.setdefault("recovery_epochs", {}).setdefault(player_id, [])
        changed = False
        for epoch in epochs:
            if epoch not in saved:
                saved.append(epoch)  # opaque identity; never a numeric ordering comparison
                changed = True
        if pt["verdict"].get(player_id) in (None, "await"):
            if pt["verdict"].get(player_id) is None:
                pt["verdict"][player_id] = "await"
                pt["phase"] = "uncertain"
                self._record_trade(pt, "uncertain")
                changed = True
            if not pt.setdefault("hello_only", {}).get(player_id):
                pt["hello_only"][player_id] = True
                changed = True
        if changed:
            self._save()

    def refuse_hidden_event(self, player_id: str, msg: dict) -> list[dict] | None:
        """A withheld party cannot supply gameplay mutations or retire owed reports."""
        event = msg.get("event")
        if (self.adapter.supports_trade_recovery() and self.party_hidden[player_id]
                and event in self.HIDDEN_PARTY_EVENTS):
            log.warning("[%s] %s refused: party hidden; waiting for a trustworthy snapshot", player_id, event)
            old_key, new_key = msg.get("old_key"), msg.get("new_key")
            if (event == "key_change" and isinstance(old_key, str) and old_key
                    and isinstance(new_key, str) and new_key and old_key != new_key):
                return [{"cmd": "key_change_rejected", "old_key": old_key,
                         "new_key": new_key, "reason": "party hidden"}]
            return [{"cmd": "noop", "refused": "party_hidden"}]
        return None

    def normalize_party_snapshot(self, player_id: str, msg: dict):
        """Keep keyed records; refuse unknowable nonempty snapshots before adoption."""
        event = msg.get("event")
        if event not in ("hello", "tick", "safe") or "party" not in msg:
            return None
        if self.party_snapshot_withheld(player_id, msg):
            return None
        raw = msg["party"]
        if raw is None:
            if event == "hello":
                msg["party"] = []
            else:
                msg.pop("party")  # no snapshot, not an empty party
            return None
        party = [m for m in raw if isinstance(m, dict) and isinstance(m.get("key"), str) and m["key"]] if isinstance(raw, list) else []
        dropped = len(raw) - len(party) if isinstance(raw, list) else 1
        if dropped:
            log.warning("[%s] dropped %s malformed or unkeyed party entries", player_id, dropped)
        if not isinstance(raw, list) or (raw and not party):
            if event == "hello":
                self.identity_error[player_id] = "Party snapshot contains no keyed entries"
                msg["_rejected"] = True
            return [{"cmd": "noop", "refused": "party_snapshot"}]
        msg["party"] = party
        return None

    def handle_event(self, player_id: str, msg: dict) -> list[dict]:
        """
        Process one event from player_id.
        Returns commands to send back to player_id (including any queued cross-player commands).
        Cross-player commands are queued and delivered on the partner's next call.
        """
        if self.load_failed:
            return [{"cmd": "noop", "refused": "load_failed"}]
        malformed = self.normalize_party_snapshot(player_id, msg)
        if malformed is not None:
            return malformed
        event = msg.get("event", "unknown")
        refused = self.refuse_hidden_event(player_id, msg)
        if refused is not None:
            return refused

        # The client has spoken about this key, so nothing we sent for it is still on the wire:
        # every answer to a SYNC_COMMAND (sync_retrieve_done/_failed, box_mon_failed,
        # memorialize_done/_failed, the stats_cache a deposit emits) carries the key.
        if msg.get("key"):
            self._ack_inflight(player_id, msg["key"])

        recovery_hello = event == "hello" and (self.adapter.supports_trade_recovery() or (
            ("party_hidden" in msg or "trade_outstanding" in msg) and self.adapter.refused_trade_recovery()))
        if not recovery_hello:
            self._tick_pending_trade()  # Gen 3 recovery hellos must pass identity first

        if self._refuse_ambiguous(player_id, msg):
            pass                                        # KEY-SCOPE-3: which of two mons? refused
        elif self._hold_for_trade(player_id, msg):
            pass                                        # replayed once the trade settles
        elif event == "hello":
            queued_before = len(self.queued_commands[player_id])
            self._handle_hello(player_id, msg)           # runs this hello's trade evidence too
            if recovery_hello and msg.get("_rejected"):
                # A wrong-save/malformed hello must not receive a previously queued APPLY.
                refused = self.queued_commands[player_id][queued_before:]
                del self.queued_commands[player_id][queued_before:]
                return refused or [{"cmd": "noop", "refused": "identity"}]
            if not msg.get("_rejected"):
                self.trade_prepare[player_id] = msg.get("trade_prepare") is True
                if recovery_hello:
                    self._tick_pending_trade()
        elif event == "area_enter":
            self._handle_area_enter(player_id, msg)
        elif event == "ghost_pos":
            self._handle_ghost_pos(player_id, msg)
        elif event == "peer_interact":
            self._handle_peer_interact(player_id, msg)
        elif event == "trade_request":
            self._handle_trade_request(player_id, msg)
        elif event == "trade_query":
            self._handle_trade_query(player_id)
        elif event == "trade_offer":
            self._handle_trade_offer(player_id, msg)
        elif event == "mon_chosen":
            self._handle_mon_chosen(player_id, msg)
        elif event == "menu_result":
            self._handle_menu_result(player_id, msg)
        elif event == "trade_done":
            self._handle_trade_done(player_id, msg)
        elif event == "apply_ready":
            self._handle_apply_ready(player_id, msg)
        elif event == "status":
            self._handle_status(player_id, msg)
        elif event == "capture":
            self._handle_capture(player_id, msg)
        elif event == "faint":
            self._handle_faint(player_id, msg)
        elif event == "release":
            self._handle_release(player_id, msg)
        elif event == "no_catch":
            self._handle_no_catch(player_id, msg)
        elif event == "whiteout":
            self._handle_whiteout(player_id, msg)
        elif event == "party_to_box":
            self._handle_party_to_box(player_id, msg)
        elif event == "box_to_party":
            self._handle_box_to_party(player_id, msg)
        elif event == "key_change":
            self._handle_key_change(player_id, msg)
        elif event == "stats_cache":
            # Stats-only update from exec_box_mon: cache without triggering sync commands.
            # Also mark key as no longer in party so subsequent party_to_box decisions are accurate.
            # Decrement party_size immediately so box_to_party full-party checks use the current
            # count; otherwise party_size stays stale until the next tick (up to 0.5 s), which
            # can cause a false "partner's party full" block if the partner tries to withdraw
            # their linked mon during that window.
            key = msg.get("key", "")
            stats = msg.get("stats")
            if key and stats:
                self.mon_stats[player_id][key] = stats
                if key in self.party_keys[player_id]:
                    self.party_size[player_id] = max(0, self.party_size.get(player_id, 0) - 1)
                self.party_keys[player_id].discard(key)
        elif event == "sync_retrieve_done":
            # Sent by exec_party_mon after successfully retrieving a mon from the box.
            key = msg.get("key", "")
            if key:
                self.party_keys[player_id].add(key)
                rb = self.rebuild_pending.get(player_id)
                if rb and key in rb.get("queued_keys", []):
                    rb["restored_keys"].add(key)
                    self._maybe_finish_rebuild(player_id)
        elif event == "sync_retrieve_failed":
            # Retrieval failed (party full, no stats, etc.) — don't add to party_keys.
            # Since both linked mons must stay in sync, re-box the partner's mon too.
            key = msg.get("key", "")
            if key:
                self.party_keys[player_id].discard(key)
                log.info(f"[{player_id}] sync_retrieve_failed for {key[:8]}")
                rb = self.rebuild_pending.get(player_id)
                if rb and key in rb.get("queued_keys", []):
                    # Drop the key from the rebuild plan; corresponding partner
                    # half stays where it is (sync handler below will reconcile
                    # if needed) and the banner clears once everything resolves.
                    rb["queued_keys"] = [k for k in rb["queued_keys"] if k != key]
                    self._maybe_finish_rebuild(player_id)
                # Find the linked partner and re-box them to maintain sync
                entry = self.entry_for(player_id, key)
                if entry and entry.status == LinkStatus.ALIVE:
                    partner = _partner(player_id)
                    partner_mon = entry.a if player_id == "b" else entry.b
                    if partner_mon and partner_mon.key in self.party_keys[partner]:
                        self.queued_commands[partner].append({"cmd": "box_mon", "key": partner_mon.key})
                        self.party_keys[partner].discard(partner_mon.key)
                        self.queued_commands[partner].append({
                            "cmd": "hud_show",
                            "text": "[!] " + self._mon_label(partner_mon) + ": re-boxed",
                            "r": 255, "g": 200, "b": 60
                        })
                        log.info(f"[{partner}] re-boxing {partner_mon.key[:8]} — partner's retrieve failed")
        elif event == "box_mon_failed":
            # The deposit did not happen. The server has ALREADY discarded this key from
            # party_keys and decremented party_size -- it does that the moment it queues
            # the command, so a swap in flight does not read as a full party -- so silence
            # here left the server's party model permanently one mon short of the
            # cartridge. Put it back.
            key = msg.get("key", "")
            if key:
                if key not in self.party_keys[player_id]:
                    self.party_keys[player_id].add(key)
                    self.party_size[player_id] = self.party_size.get(player_id, 0) + 1
                log.warning("[%s] box_mon_failed key=%s reason=%s — party model restored",
                            player_id, key[:8], msg.get("reason", "unknown"))
                self._save()
        elif event == "memorialize_done":
            self._handle_memorialize_done(player_id, msg)
        elif event == "memorialize_failed":
            self._handle_memorialize_failed(player_id, msg)
        elif event == "trainer_battle_start":
            self._handle_trainer_battle_start(player_id, msg)
        elif event == "rival_team_replaced":
            self._handle_rival_team_replaced(player_id, msg)
        elif event in ("safe", "tick"):
            if "trade_blocked" in msg:
                self.trade_blocked[player_id] = msg["trade_blocked"] is True
            if "awaiting_save" in msg:
                self.awaiting_save[player_id] = msg["awaiting_save"] is True
            # Accept pokéballs activation update from tick events so the server learns
            # the nuzlocke became active mid-session (between hello events).
            if msg.get("has_pokeballs") is True:
                self.pokeballs_obtained[player_id] = True
            # Update party size from tick/safe snapshots for paired retrieval checks.
            hidden = self.party_snapshot_withheld(player_id, msg)
            if self.adapter.supports_trade_recovery() and (hidden or "party" in msg):
                changed = self.party_hidden[player_id] != hidden
                self.party_hidden[player_id] = hidden
                if changed:
                    self._save()
            party = None if hidden else msg.get("party")
            if party is not None:
                old_size = self.party_size.get(player_id, 0)
                self.party_size[player_id] = len(party)
                if old_size != len(party):
                    log.debug(f"[PARTY] player={player_id}  party_size {old_size} → {len(party)}  (tick/safe)")
                # Rival Team Swap: refresh the per-player blob cache from the
                # same snapshot.  See _ingest_party_blobs for the shape.
                self._ingest_party_blobs(player_id, party)
                self._trade_evidence(player_id, party)
                # Reconcile party_keys against Lua's actual party snapshot.  Catches
                # state drift from spurious party_to_box / missed box_to_party events
                # in the Lua diff loop — without this, a phantom deposit propagates
                # a real box_mon to the partner and the Soul Link party-sync rule
                # silently breaks until something else forces correction.
                self._reconcile_party_keys(player_id, party, in_battle=bool(msg.get("in_battle")))

        if (event in ("hello", "tick", "safe") and "party" in msg and not msg.get("_rejected")
                and not self.party_hidden[player_id]):
            self._resume_prepared_trade_on_visible_snapshot()

        cmds = self.queued_commands[player_id][:]
        self.queued_commands[player_id].clear()
        self._arm_inflight(player_id, cmds)
        if any(c.get("cmd") in DURABLE_SYNC_COMMANDS for c in cmds):
            self._save()                                 # handed over: no longer owed after a restart
        if cmds:
            _summary = ", ".join(
                c.get("cmd", "?") + (":" + c["key"][:8] if "key" in c else "")
                for c in cmds
            )
            log.debug(f"[CMD QUEUE→{player_id}] flushing {len(cmds)} cmd(s): {_summary}")
        return cmds if cmds else [{"cmd": "noop"}]

    def _handle_ghost_pos(self, player_id: str, msg: dict):
        """Relay a player's overworld position to the partner as an (ephemeral) ghost_pos
        command for the engine-NPC peer ghost. High-frequency; never persisted."""
        # Overworld Presence toggle: when off, drop the relay so the partner never
        # receives a ghost_pos → no ghost ever spawns. The Lua side keeps emitting
        # (it self-gates only on patch presence); we gate the run-level feature here.
        if not self.overworld_presence:
            return
        partner = _partner(player_id)
        try:
            cmd = {
                "cmd": "ghost_pos",
                "mg": int(msg.get("mg", 0)), "mn": int(msg.get("mn", 0)),
                "x": int(msg.get("x", 0)),   "y": int(msg.get("y", 0)),   # TILE coords
                "f": int(msg.get("f", 1)),   "gfx": int(msg.get("gfx", 0)),
                "mv": int(msg.get("mv", 0)), "run": int(msg.get("run", 0)),
                "an": int(msg.get("an", 0)),   # partner's live animNum (exact animation)
                # partner avatar (so they render as THEMSELVES): live sprite images/anims ROM ptrs +
                # true 16-colour palette (64 hex). Pass-through only; identical across the same RR build.
                "imgs": int(msg.get("imgs", 0)), "anim": int(msg.get("anim", 0)),
                "pcol": str(msg.get("pcol", "") or "")[:64],
            }
        except (TypeError, ValueError):
            # A nil RAM read on the client can emit a null/garbage field at ~20 Hz; dropping the
            # sample keeps the connection alive (an uncaught error here tears down the TCP loop).
            return
        q = self.queued_commands[partner]
        # coalesce: at most one ghost_pos is ever queued (latest wins), so this ~20 Hz relay can't grow
        # the queue. Fast path replaces it in place when it's already last; otherwise drop any stale
        # ghost_pos and append the fresh one.
        if q and q[-1].get("cmd") == "ghost_pos":
            q[-1] = cmd
        else:
            self.queued_commands[partner] = [c for c in q if c.get("cmd") != "ghost_pos"] + [cmd]

    def _handle_peer_interact(self, player_id: str, msg: dict):
        """A player talked to their partner's ghost (pressed A toward the engine-NPC). The
        patch already showed a native box on the initiator's side; notify the PARTNER so they
        know someone said hey. Testing stub for the richer trade/ready-check flows."""
        # Overworld Presence toggle: inert when the feature is off (no ghost to talk to).
        if not self.overworld_presence:
            return
        self._notify_hey(player_id)

    def _notify_hey(self, player_id: str):
        """Friendly '<partner> says hey!' notification to the OTHER player. Used as the no-trade
        fallback for talk-to-partner and by the legacy peer_interact event."""
        partner = _partner(player_id)
        sayer = (self.player_identity.get(player_id) or {}).get("trainer_name", "")
        text = f"{sayer} says hey!" if sayer else "Your partner says hey!"
        self.queued_commands[partner].append({
            "cmd": "msgbox", "text": text, "fb": "prompt",
        })
        log.info(f"[{player_id}] says hey → notifying {partner}")

    def _mon_label(self, mon: "MonInfo") -> str:
        """Display label for a linked mon: nickname, else species name, else a key prefix."""
        return mon.nickname or self.adapter.species_name(mon.species) or mon.key[:8]

    def _handle_status(self, player_id: str, msg: dict):
        """Cache a player's in-game progress (badge count). NOTE: nothing consumes this yet —
        the overlays read SLinkServer's separate hello/tick badge bitmask; this 0-8 count is
        parked here for a future talk-to-partner progress display."""
        try:
            b = int(msg.get("badges", 0))
        except (TypeError, ValueError):
            b = 0
        self.player_badges[player_id] = max(0, min(8, b))

    def _eligible_trade_pairs(self, player_id: str):
        """(slot, key, entry, partner_blob) for each of the player's party mons that is one half of a
        LIVE link whose partner half is in the partner's party (blobs cached for both) — i.e. the only
        mons that may be traded. Sorted by party slot. This is the linked-pair invariant's source set."""
        partner = _partner(player_id)
        if (self.trade_blocked[player_id] or self.trade_blocked[partner]
                or self.party_hidden[player_id] or self.party_hidden[partner]):
            return []
        par_blobs = self.partner_blobs.get(partner, [])
        out = []
        for be in sorted(self.partner_blobs.get(player_id, []), key=lambda e: e.get("slot", 99)):
            k = be.get("key", "")
            entry = self.entry_for(player_id, k)
            if not entry or entry.status != LinkStatus.ALIVE:
                continue
            my_mon  = entry.a if player_id == "a" else entry.b
            par_mon = entry.b if player_id == "a" else entry.a
            if not my_mon or not par_mon or my_mon.key != k:
                continue
            par_be = next((e for e in par_blobs if e.get("key") == par_mon.key), None)
            if par_be is None or not self._trade_sane(player_id, be, par_be):
                continue
            if self._trade_key_clash(player_id, par_mon.key, k):
                continue
            out.append((int(be.get("slot", 0)), k, entry, par_be))
        return out

    def _trade_sane(self, player_id: str, mine: dict, theirs: dict) -> bool:
        """Vanilla trade sanity (CheckAnyOtherAliveMonsForTrade / ValidateOTTrademon), which the GB
        overlays leave to the host (asm review 1b33bc31 MINOR-4): neither mon may be abnormal
        (a species the adapter does not know, a level outside 1-100), and neither player may be
        left with no living mon. A field the snapshot did not carry is not held against it."""
        def normal(e):
            return (("species_id" not in e or self.adapter.species_types(e["species_id"]) is not None)
                    and ("level" not in e or 1 <= int(e["level"] or 0) <= 100))

        def alive(e):
            return e.get("hp") is None or e["hp"] > 0

        def keeps_one(pid, gives, gets):
            return alive(gets) or any(alive(e) for e in self.partner_blobs.get(pid, [])
                                      if e.get("key") != gives.get("key"))
        return (normal(mine) and normal(theirs)
                and keeps_one(player_id, mine, theirs) and keeps_one(_partner(player_id), theirs, mine))

    # A trade holds the single `pending_trade` slot. If a side abandons it (walks away from a menu /
    # the scene, or disconnects) the slot would wedge every future trade. `age` counts events seen
    # while the trade makes NO progress (each trade handler resets it to 0); past this many, abandon.
    # Because the link is only mutated ATOMICALLY in _handle_trade_done (once BOTH sides report), an
    # abort here can never leave the link half-swapped — it just frees the slot.
    # Counted in events, not seconds: both clients tick every 30 frames, so ~4000 events is roughly 17
    # minutes with no acted-on trade event (an unanswered confirm, a partner who walked away).
    TRADE_WATCHDOG_EVENTS = 4000
    TRADE_FINAL_LIMIT = 256
    # Ticks to suppress party-key drift reconciliation on each side after a trade completes, while the
    # clients' party snapshots settle (prevents the post-trade spurious "Unbox" party_mon).
    TRADE_SETTLE_TICKS = 12

    def _tick_pending_trade(self):
        pt = self.pending_trade
        if pt is None:
            return
        if pt.get("phase") in ("uncertain", "conflict"):
            return                                     # already the watchdog's product: evidence or a human settles it
        pt["age"] = pt.get("age", 0) + 1
        if pt["age"] > self.TRADE_WATCHDOG_EVENTS:
            if pt.get("phase") == "applying":
                pt.setdefault("verdict", {"a": None, "b": None})
                # MAJOR-4 (review e9d5e136): an armed APPLY nobody picked up yet could still commit
                # AFTER an evidence rollback. First ask each silent side that speaks the prepare
                # round to withdraw it: an unpicked lease answers a certain "none", a commit past
                # its latch answers uncertain. Only a side still silent after that awaits evidence.
                ask = [pid for pid in ("a", "b") if pt["verdict"][pid] is None and self.trade_prepare[pid]]
                if ask and not pt.get("withdraw_asked"):
                    pt["withdraw_asked"], pt["age"] = True, 0
                    for pid in ask:
                        self.queued_commands[pid].append({"cmd": "withdraw_trade", "token": pt["token"]})
                    log.warning(f"trade watchdog: asked {ask} to withdraw 'applying' trade {pt.get('token')}")
                    self._save()
                    return
                # apply_trade was already dispatched, so freeing the slot could strand keys the
                # players no longer hold -- and guessing an outcome is worse (evolution, a failed
                # append). Every side that never reported is now UNCERTAIN: its next party
                # snapshot settles it (_trade_evidence); one that never sends one stays surfaced.
                log.warning(f"trade watchdog: 'applying' trade {pt.get('token')} is uncertain; "
                            f"waiting for party evidence ({pt.get('verdict')})")
                pt["phase"] = "uncertain"
                pt.setdefault("verdict", {"a": None, "b": None})
                silent = [pid for pid in ("a", "b") if pt["verdict"][pid] is None]
                for pid in silent:
                    pt["verdict"][pid] = "await"
                if silent:                             # a side that declared itself was journaled then
                    self._record_trade(pt, "uncertain")
                self._save()
                return
            log.info(f"trade watchdog: abandoning stuck trade (phase {pt.get('phase')}, token {pt.get('token')})")
            if self.adapter.supports_trade_recovery():
                for pid in ("a", "b"):
                    self.queued_commands[pid] = [c for c in self.queued_commands[pid]
                        if not (c.get("cmd") in ("apply_trade", "apply_prepare") and c.get("token") == pt["token"])]
            self.pending_trade = None
            for pid in ("a", "b"):
                self.queued_commands[pid].append({
                    "cmd": "msgbox", "text": "Trade canceled - no response.",
                    "fb": "prompt"})

    # MAJOR-2 (review e9d5e136): the link is swapped only at _commit_trade, but a mon changes hands
    # at the cartridge's commit. Until the trade settles, an event naming a trade key would be routed
    # by the unswapped halves (a received mon's faint killing the wrong partner, an evolved key lost as
    # unlinked); such events wait in pt["held_events"] and replay after commit or rollback.
    TRADE_HELD_EVENTS = {"faint": "_handle_faint", "party_to_box": "_handle_party_to_box",
                         "box_to_party": "_handle_box_to_party", "release": "_handle_release"}
    HIDDEN_PARTY_EVENTS = frozenset(("capture", "faint", "party_to_box", "box_to_party",
                                   "key_change", "whiteout", "release"))

    AMBIGUITY_GATED = ("faint", "party_to_box", "box_to_party", "release", "key_change", "stats_cache",
                       "sync_retrieve_done", "sync_retrieve_failed", "box_mon_failed",
                       "memorialize_done", "memorialize_failed")

    def _refuse_ambiguous(self, player_id: str, msg: dict) -> bool:
        """KEY-SCOPE-3: an event naming a key this player holds twice (a trade-window clash) is
        refused and counted in the persisted latch; routing it by the index would act on the
        surviving twin's link."""
        key = msg.get("key") or msg.get("old_key") or ""
        named = {key, msg.get("new_key") or ""} if msg.get("event") == "key_change" else {key}
        latches = [self.ambiguous_keys[player_id][k] for k in named if k in self.ambiguous_keys[player_id]]
        if msg.get("event") not in self.AMBIGUITY_GATED or not latches:
            return False
        for latch in latches:                          # KEY-SCOPE-5: a latched new_key vetoes too
            latch["refused"] = latch.get("refused", 0) + 1
        log.error(f"[{player_id}] {msg.get('event')} {key} REFUSED: the key names two of this "
                  f"player's mons (trade clash); resolve with POST /api/debug/resolve_ambiguous_key")
        if msg.get("event") == "key_change":
            self.queued_commands[player_id].append(
                {"cmd": "key_change_rejected", "old_key": key, "new_key": msg.get("new_key", ""),
                 "reason": "ambiguous key (trade clash)"})
            msg["_key_change_status"] = "rejected"
        self._save()
        return True

    def resolve_ambiguous_key(self, player_id: str, key: str) -> tuple[bool, str]:
        """An admin checked the cartridge: `key` now names ONE mon again (the duplicate was
        released, memorialized or traded away), so events naming it route normally."""
        if player_id not in ("a", "b") or self.ambiguous_keys[player_id].pop(key, None) is None:
            return False, "no ambiguous key for that player"
        log.warning(f"[{player_id}] ambiguous key {key} resolved by admin")
        self._save()
        return True, ""

    def _hold_for_trade(self, player_id: str, msg: dict) -> bool:
        pt = self.pending_trade
        key = msg.get("key") or ""
        if (msg.get("event") not in self.TRADE_HELD_EVENTS or not key or not pt
                or pt.get("phase") not in ("applying", "uncertain", "conflict")):
            return False
        new = [n[0] for n in (pt.get("new") or {}).values() if n]
        gets = pt[f"{_partner(player_id)}_key"]
        # a key the trade names, or an unindexed one with the incoming mon's OT (its evolution)
        ours = (key in (pt["a_key"], pt["b_key"], *new)
                or (self.entry_for(player_id, key) is None
                    and self.adapter.parse_ot_id(key) == self.adapter.parse_ot_id(gets)))
        if not ours:
            return False
        pt.setdefault("held_events", []).append([player_id, dict(msg)])
        log.info(f"[{player_id}] {msg.get('event')} {key} held until trade {pt['token']} settles")
        self._save()
        return True

    def _replay_trade_events(self, pt: dict):
        """Run the events _hold_for_trade kept, now that the link says who holds which mon."""
        for player_id, msg in pt.pop("held_events", []):
            if msg.get("_cause") == "whiteout":
                # the whited-out player's WHOLE party died: the kill is of whatever half of this
                # link that player holds now (KEY-SCOPE: its key is looked up in its own index)
                half = pt["link"].a if player_id == "a" else pt["link"].b
                msg = {**msg, "key": half.key if half else msg.get("key")}
            log.info(f"[{player_id}] replaying held {msg.get('event')} {msg.get('key')} (trade {pt['token']})")
            getattr(self, self.TRADE_HELD_EVENTS[msg["event"]])(player_id, msg)

    def _handle_trade_request(self, player_id: str, msg: dict):
        """Talk-to-partner → show the native action menu (TRADE / SAY HEY). TRADE opens the party
        picker; SAY HEY just pings the partner."""
        unavailable = self.adapter.trade_unavailable_reason()
        if unavailable and not all(self.trade_prepare.values()):
            self.queued_commands[player_id].append({"cmd": "msgbox", "text": unavailable})
            return
        if self.pending_trade is not None:
            return                                # a trade is already in flight — ignore silently (no spam)
        self._trade_token += 1
        token = f"t{self._trade_token}"
        native_offer = msg.get("native_offer") is True and self.adapter.native_trade_ui()
        self.pending_trade = {"phase": "choosing" if native_offer else "menu",
                              "initiator": player_id, "token": token, "reprompts": 0, "age": 0}
        if native_offer:
            return  # the ROM receptionist already showed the action/party menus
        # Native multichoice list — the proper menu with labelled options. `text` is spoken by whoever
        # the player just talked to (the box stays open under the floating menu): presence OFF means the
        # Pokémon-Center trade NPC (Prof Oak), presence ON means the partner's overworld ghost.
        partner_name = (self.player_identity.get(_partner(player_id)) or {}).get("trainer_name", "")
        who = partner_name or "your partner"
        if self.overworld_presence:
            text = f"{who} is right here!\nWhat will you do?"
        else:
            text = f"OAK: Took you long enough.\n{who} is waiting. Make it quick."
        self.queued_commands[player_id].append({
            "cmd": "show_choices", "token": token, "options": ["Trade", "Say hey"], "text": text})
        log.info(f"[{player_id}] trade_request → action menu (Trade/Say hey) token={token}")

    def _handle_trade_query(self, player_id: str):
        """The native receptionist needs a six-bit eligible-party mask."""
        mask = 0
        if self.adapter.native_trade_ui() and self.pending_trade is None:
            for slot, _key, _entry, _blob in self._eligible_trade_pairs(player_id):
                if 0 <= slot < 6:
                    mask |= 1 << slot
        self.queued_commands[player_id].append({"cmd": "trade_mask", "mask": mask})

    def _handle_trade_offer(self, player_id: str, msg: dict):
        """Reuse the linked-pair picker after the ROM has chosen a physical slot."""
        accepted = False
        if self.adapter.native_trade_ui() and self.pending_trade is None:
            try:
                slot = int(msg.get("slot"))
            except (TypeError, ValueError):
                slot = -1
            match = next((pair for pair in self._eligible_trade_pairs(player_id)
                          if pair[0] == slot), None)
            if match is not None:
                _slot, my_key, entry, partner_blob = match
                partner = _partner(player_id)
                incoming_key = partner_blob.get("key", "")
                # A Gen 1 DV:OT:species key is not guaranteed unique. Neither
                # recipient may already hold the key it is about to receive.
                if (my_key in self.party_keys[player_id]
                        and incoming_key in self.party_keys[partner]
                        and my_key not in self.party_keys[partner]
                        and incoming_key not in self.party_keys[player_id]
                        and entry.status == LinkStatus.ALIVE):
                    self._handle_trade_request(player_id, {"native_offer": True})
                    pt = self.pending_trade
                    if pt is not None:
                        self._handle_mon_chosen(player_id, {"token": pt["token"], "slot": slot})
                        accepted = bool(self.pending_trade and
                                        self.pending_trade.get("phase") == "confirming")
        ack = {"cmd": "trade_offer_ack", "ok": accepted}
        if accepted:
            ack["token"] = self.pending_trade["token"]   # the initiator withdraws under it (_handle_menu_result)
        self.queued_commands[player_id].append(ack)

    def _handle_mon_chosen(self, player_id: str, msg: dict):
        """The initiator picked a party slot. Enforce the linked-pair invariant: the slot MUST be one
        half of a live link with the partner's half present. Eligible → confirm menu to BOTH; ineligible
        → re-prompt "Pick a linked POKeMON!"; cancel (slot 7) → abort."""
        pt = self.pending_trade
        if not pt or pt.get("phase") != "choosing" or str(msg.get("token", "")) != pt["token"]:
            return
        if player_id != pt["initiator"]:
            return
        try:
            slot = int(msg.get("slot", 7))
        except (TypeError, ValueError):
            slot = 7
        if slot < 0 or slot > 5:                       # SLOT_CANCEL (7) / B-press
            self.pending_trade = None
            self.queued_commands[player_id].append({
                "cmd": "msgbox", "text": "Trade canceled.", "fb": "prompt"})
            return
        if pt.get("choice_deferred"):
            return  # replayed picks cannot replace the held choice or renew its watchdog
        pt["age"] = 0                                  # progress — reset the abandonment watchdog
        if (self.adapter.supports_trade_recovery() and all(self.trade_prepare.values())
                and (any(self.party_hidden.values()) or any(self.trade_recovery_pending.values()))):
            # Cached identity pins the selection only; it cannot authorize a trade while hidden.
            mine = next((b for b in self.partner_blobs[player_id] if b.get("slot") == slot), None)
            entry = self.entry_for(player_id, mine.get("key", "")) if mine else None
            partner = _partner(player_id)
            other = (entry.b if player_id == "a" else entry.a) if entry else None
            theirs = next((b for b in self.partner_blobs[partner]
                           if other and b.get("key") == other.key), None)
            if not mine or not theirs:
                self._cancel_trade("Trade canceled - a POKeMON is\nno longer available.")
                return
            pt["choice_deferred"] = {"slot": slot, "key": mine["key"],
                                     "partner_key": theirs["key"], "partner_slot": theirs["slot"]}
            return
        match = next((p for p in self._eligible_trade_pairs(player_id) if p[0] == slot), None)
        if match is None:                              # not a linked mon -> re-prompt (capped)
            pt["reprompts"] = pt.get("reprompts", 0) + 1
            if pt["reprompts"] > 3:
                self.pending_trade = None
                self.queued_commands[player_id].append({
                    "cmd": "msgbox", "text": "Trade canceled.", "fb": "prompt"})
                return
            self.queued_commands[player_id].append({
                "cmd": "msgbox", "text": "Pick a linked POKeMON!", "fb": "prompt"})
            self.queued_commands[player_id].append({"cmd": "choose_mon", "token": pt["token"]})
            return
        _slot, my_key, entry, par_be = match
        partner = _partner(player_id)
        my_be = next(e for e in self.partner_blobs[player_id] if e.get("key") == my_key)
        a_be = my_be if player_id == "a" else par_be
        b_be = par_be if player_id == "a" else my_be
        pt.update({
            "phase": "confirming", "link": entry,
            "a_slot": int(a_be["slot"]), "b_slot": int(b_be["slot"]),
            "a_blob_hex": a_be["blob"].hex(), "b_blob_hex": b_be["blob"].hex(),
            "a_key": entry.a.key, "b_key": entry.b.key,
            "a_label": self._mon_label(entry.a), "b_label": self._mon_label(entry.b),
        })
        # The initiator already committed (chose Trade + picked the mon); only the PARTNER confirms.
        partner = _partner(player_id)
        p_gives = pt["a_label"] if partner == "a" else pt["b_label"]   # the partner's own half
        p_gets  = pt["b_label"] if partner == "a" else pt["a_label"]   # the initiator's half
        # Non-blocking HUD for the initiator (a field msgbox would set sScriptContext2Enabled and block
        # the native trade scene from starting); the partner gets the confirm menu.
        self.queued_commands[player_id].append({
            "cmd": "hud_show", "text": "Trade offer sent - waiting for partner...",
            "r": 255, "g": 220, "b": 60, "frames": 600})
        prompt = {"cmd": "show_menu", "token": pt["token"],
                  "text": f"Trade your {p_gives} for {p_gets}?"}
        if self.adapter.native_trade_ui():
            prompt.update({"slot": pt[f"{partner}_slot"],
                           "blob_hex": pt[f"{player_id}_blob_hex"]})
        self.queued_commands[partner].append(prompt)
        log.info(f"[{player_id}] chose slot {slot} → partner-confirm {entry.a.key[:8]}<->{entry.b.key[:8]}")

    def _handle_menu_result(self, player_id: str, msg: dict):
        """Native-menu choices for the trade flow, phase-aware:
        - "menu":       the initiator's TRADE(YES)/WAVE(NO) action menu.
        - "confirming": the PARTNER's accept(YES)/decline(NO) of the offer."""
        pt = self.pending_trade
        if not pt or str(msg.get("token", "")) != pt["token"]:
            return
        try:
            choice = int(msg.get("choice", 0))
        except (TypeError, ValueError):
            choice = 0
        if (pt.get("phase") in ("menu", "choosing", "confirming", "preparing", "applying") and pt.get("menu_deferred")
                and player_id == pt["initiator"] and choice == 0 and msg.get("withdraw") is not True):
            return  # a replayed Trade choice cannot renew the hidden wait or issue two pickers
        if (pt.get("phase") in ("confirming", "preparing", "applying") and pt.get("confirm_deferred")
                and player_id == _partner(pt["initiator"])
                and choice == 1):
            return  # replayed YES cannot renew the watchdog or duplicate native preparation
        phase = pt.get("phase")
        # Only an event a branch below acts on is progress. Anything else (a partner's menu_result
        # while preparing/applying, the initiator's bare choice 0 mid-trade) is discarded, and
        # resetting the abandonment watchdog for it let an echoing client keep a stuck trade alive.
        initiator = pt["initiator"]
        if ((phase == "menu" and player_id == initiator)
                or (player_id == initiator and phase in ("choosing", "confirming", "preparing", "applying")
                    and msg.get("withdraw") is True)
                or (phase == "confirming" and player_id == _partner(initiator))):
            pt["age"] = 0
        if phase == "menu":                                # initiator's action menu (multichoice index)
            if player_id != pt["initiator"]:
                return
            if msg.get("withdraw") is True:
                self.pending_trade = None
                self.queued_commands[player_id].append({"cmd": "msgbox", "text": "Trade canceled.", "fb": "prompt"})
                return
            if choice == 0:                                # TRADE → open the party picker (if eligible)
                if (self.adapter.supports_trade_recovery() and all(self.trade_prepare.values())
                        and (any(self.party_hidden.values()) or any(self.trade_recovery_pending.values()))):
                    pt["menu_deferred"] = True
                    self._save()
                    return
                if not self._eligible_trade_pairs(player_id):
                    self.pending_trade = None
                    self.queued_commands[player_id].append({
                        "cmd": "msgbox", "text": ("Trade paused - party withheld.\nWait for a fresh snapshot."
                                                  if any(self.party_hidden.values()) else
                                                  "No linked pair in your party to trade."), "fb": "prompt"})
                    return
                pt["phase"] = "choosing"
                self.queued_commands[player_id].append({"cmd": "choose_mon", "token": pt["token"]})
            elif choice == 1:                              # SAY HEY → ping the partner
                self.pending_trade = None
                self._notify_hey(player_id)
            else:                                          # B-press / cancel (0x7F) → abort silently
                self.pending_trade = None
            return
        if player_id == pt["initiator"] and phase in ("choosing", "confirming", "preparing", "applying"):
            # The initiator's cartridge left its offer (timed out, B, a late ack): an explicit
            # `withdraw` (never a bare choice 0, which a Gen 3 menu replay can carry) cancels it
            # BEFORE anything is applied, or a later YES would apply the partner side alone.
            if msg.get("withdraw") is not True:
                return
            log.info(f"[{player_id}] withdrew trade offer {pt['token']} ({phase})")
            if phase == "applying":
                # MINOR-7: it never armed its APPLY, so it certainly did not trade
                if pt.get("verdict", {}).get(player_id) is None:
                    pt["verdict"][player_id] = "none"
                    self._settle_trade(pt)
                return
            self._cancel_trade("Trade did not go through.")
            return
        if phase == "confirming":                          # partner accepted / declined the offer
            if player_id != _partner(pt["initiator"]):
                return
            if choice == 1:
                if (self.adapter.supports_trade_recovery() and all(self.trade_prepare.values())
                        and (any(self.party_hidden.values()) or any(self.trade_recovery_pending.values()))):
                    # Preserve this exact token/offer while the withheld party obtains a fresh
                    # visible census. A hidden snapshot cannot authorize native prepare yet.
                    pt["confirm_deferred"] = True
                    self._save()
                    return
                if all(self.trade_prepare.values()):
                    self._prepare_trade(pt)
                else:
                    self._execute_trade(pt)
            else:
                self.pending_trade = None
                if msg.get("uncertain") is True:
                    pt["problem"] = "Trade canceled - no response."
                    self._record_trade(pt, "uncertain")
                    for pid in ("a", "b"):
                        self.queued_commands[pid].append({
                            "cmd": "msgbox", "text": "Trade canceled - no response.", "fb": "prompt"})
                else:
                    self.queued_commands[pt["initiator"]].append({
                        "cmd": "msgbox", "text": "Your partner declined the trade.", "fb": "prompt"})
                    self.queued_commands[player_id].append({
                        "cmd": "msgbox", "text": "Trade declined.", "fb": "prompt"})

    def _cancel_trade(self, text: str):
        """Clear a trade nothing was applied for, telling both players."""
        self.pending_trade = None
        for pid in ("a", "b"):
            self.queued_commands[pid].append({"cmd": "msgbox", "text": text, "fb": "prompt"})

    def _prepare_trade(self, pt: dict):
        """MAJOR-1 (review e9d5e136): the dual-apply race. Before either cartridge may commit, ask
        each client whether it can still take its APPLY (visit accepted, same slot, cartridge still
        waiting); apply_trade goes out only after both answer ok, otherwise both cancel with
        nothing staged. What is left is one delivery latency inside the cartridge APPLY wait."""
        if self._trade_unavailable(pt):
            return
        pt.update(phase="preparing", ready={"a": False, "b": False}, age=0)
        for pid in ("a", "b"):
            self.queued_commands[pid].append({"cmd": "apply_prepare", "token": pt["token"],
                                              "slot": pt[f"{pid}_slot"], "old_key": pt[f"{pid}_key"]})
        log.info(f"trade {pt['token']} preparing: waiting for apply_ready from both sides")

    def _handle_apply_ready(self, player_id: str, msg: dict):
        pt = self.pending_trade
        if not pt or pt.get("phase") != "preparing" or str(msg.get("token", "")) != pt["token"]:
            return
        pt["age"] = 0
        if msg.get("ok") is not True:
            log.info(f"[{player_id}] cannot take trade {pt['token']}; canceled before any apply")
            self._cancel_trade("Trade did not go through.")
            return
        pt["ready"][player_id] = True
        if all(pt["ready"].values()):
            if self.adapter.supports_trade_recovery() and any(self.party_hidden.values()):
                # A native pre-save can transiently withhold one party just before its
                # apply_ready. Keep both pre-saved leases; only a NEW visible census may
                # release this wait, and _execute_trade revalidates the whole pair then.
                self._save()
                return
            self._execute_trade(pt)

    def _resume_prepared_trade_on_visible_snapshot(self):
        pt = self.pending_trade
        if (self.adapter.supports_trade_recovery() and pt and pt.get("phase") == "choosing"
                and pt.get("choice_deferred") and not any(self.party_hidden.values())
                and not any(self.trade_recovery_pending.values())):
            choice = pt["choice_deferred"]
            initiator = pt["initiator"]
            match = next((p for p in self._eligible_trade_pairs(initiator)
                          if p[0] == choice["slot"] and p[1] == choice["key"]
                          and p[3].get("key") == choice["partner_key"]
                          and p[3].get("slot") == choice["partner_slot"]), None)
            if not all(self.trade_prepare.values()) or match is None:
                self._cancel_trade("Trade canceled - a POKeMON is\nno longer available.")
            else:
                del pt["choice_deferred"]
                self._handle_mon_chosen(initiator, {"token": pt["token"], "slot": choice["slot"]})
            return
        if (self.adapter.supports_trade_recovery() and pt and pt.get("phase") == "menu"
                and pt.get("menu_deferred") and not any(self.party_hidden.values())
                and not any(self.trade_recovery_pending.values())):
            initiator = pt["initiator"]
            if not all(self.trade_prepare.values()) or not self._eligible_trade_pairs(initiator):
                self._cancel_trade("Trade canceled - a POKeMON is\nno longer available.")
            else:
                pt["phase"], pt["age"] = "choosing", 0
                self.queued_commands[initiator].append({"cmd": "choose_mon", "token": pt["token"]})
                self._save()
            return
        if (self.adapter.supports_trade_recovery() and pt and pt.get("phase") == "confirming"
                and pt.get("confirm_deferred") and not any(self.party_hidden.values())
                and not any(self.trade_recovery_pending.values())):
            # Revalidate the original pair, keys and slots against the newly visible party
            # before either native client receives a prepare/apply command.
            if any(not any(blob.get("key") == pt[f"{pid}_key"]
                           and blob.get("slot") == pt[f"{pid}_slot"]
                           for blob in self.partner_blobs[pid]) for pid in ("a", "b")):
                self._cancel_trade("Trade canceled - a POKeMON is\nno longer available.")
            elif not all(self.trade_prepare.values()):
                self._cancel_trade("Trade did not go through.")
            else:
                self._prepare_trade(pt)
            return
        ready = (pt or {}).get("ready") or {}
        if (not self.adapter.supports_trade_recovery() or not pt or pt.get("phase") != "preparing"
                or ready.get("a") is not True or ready.get("b") is not True
                or any(self.party_hidden.values())):
            return
        self._execute_trade(pt)  # retains _trade_unavailable's full pair/key/hidden validation

    def _trade_key_clash(self, player_id: str, gets: str, gives: str) -> bool:
        """KEY-SCOPE-2: a recipient that already indexes the key it would receive (a boxed live
        link, say) would hold two live links on one key; neither side may trade into that."""
        return (self.entry_for(player_id, gets) is not None
                or self.entry_for(_partner(player_id), gives) is not None)

    def _trade_unavailable(self, pt: dict) -> bool:
        """Re-validate at confirm: the players free-roam while the partner deliberates, so the offered
        pair may have fainted/died or been boxed since mon_chosen. Cancels (and says so) if it has."""
        entry = pt["link"]
        if any(self.party_hidden.values()):
            self._cancel_trade("Trade canceled - party withheld.\nWait for a fresh snapshot.")
            return True
        if (entry.status != LinkStatus.ALIVE
                or self._trade_key_clash("a", pt["b_key"], pt["a_key"])
                or pt["a_key"] not in self.party_keys["a"]
                or pt["b_key"] not in self.party_keys["b"]
                or (self.adapter.native_trade_ui()
                    and (pt["a_key"] in self.party_keys["b"]
                         or pt["b_key"] in self.party_keys["a"]))):
            self._cancel_trade("Trade canceled - a POKeMON is\nno longer available.")
            log.info(f"trade aborted at confirm: pair no longer tradable (token {pt['token']})")
            return True
        for pid in ("a", "b"):
            current = [row for row in self.partner_blobs[pid] if row.get("key") == pt[f"{pid}_key"]]
            if len(current) != 1 or current[0]["blob"].hex() != pt[f"{pid}_blob_hex"]:
                self._cancel_trade("Trade canceled - a POKeMON changed.\nPlease make a new offer.")
                log.info("trade %s canceled: player %s's offered record changed", pt["token"], pid)
                return True
        return False

    def _execute_trade(self, pt: dict):
        """Both confirmed → tell each client to run the NATIVE trade (stage gEnemyParty[0] + the trade
        scene) for its half. We do NOT mutate the link here. The swap is applied ATOMICALLY in
        _handle_trade_done once BOTH sides report their post-trade mon, so a half-completed trade
        (one side's scene fails / the player waits forever / disconnects) can NEVER leave the link
        half-swapped with mismatched keys (the old 'pairs break if you wait too long' bug). The
        watchdog (_tick_pending_trade) moves a side that never reports to UNCERTAIN: its next
        party snapshot settles it (_trade_evidence), nothing is guessed."""
        if self._trade_unavailable(pt):
            return
        # old_key lets the client re-locate the mon if the party was reordered after mon_chosen
        # (the slot index is a snapshot); token lets trade_done reports be matched to THIS trade.
        a_command = {
            "cmd": "apply_trade", "slot": pt["a_slot"], "blob_hex": pt["b_blob_hex"],
            "old_key": pt["a_key"], "token": pt["token"]}
        b_command = {
            "cmd": "apply_trade", "slot": pt["b_slot"], "blob_hex": pt["a_blob_hex"],
            "old_key": pt["b_key"], "token": pt["token"]}
        if self.adapter.native_trade_ui():
            a_command["partner_name"] = (self.player_identity.get("b") or {}).get("trainer_name", "")
            b_command["partner_name"] = (self.player_identity.get("a") or {}).get("trainer_name", "")
        self.queued_commands["a"].append(a_command)
        self.queued_commands["b"].append(b_command)
        pt["phase"] = "applying"
        pt["done"] = {"a": False, "b": False}
        pt["new"]  = {"a": None, "b": None}   # (new_key, new_species) reported by each client post-scene
        # Per side: None (scene running) | "await" (uncertain: next party snapshot decides) |
        # "traded" | "none" | a conflict reason. _settle_trade acts once neither is None/"await".
        pt["verdict"] = {"a": None, "b": None}
        pt["age"]  = 0
        self._save()                                    # BLOCKER-1: a restart from here restores it
        log.info(f"trade applying: A slot {pt['a_slot']} <-> B slot {pt['b_slot']} (link {pt['link'].area_id})")

    def _handle_trade_done(self, player_id: str, msg: dict):
        """Each client reports its post-trade mon (new_key/new_species — captures any trade-evolution).
        We BUFFER each side's result and apply the whole swap ATOMICALLY only once BOTH have reported,
        so the link is never observable in a half-swapped state. Then play the jingle + 'Traded!' box."""
        pt = self.pending_trade
        # NIT-11 (invariant review): an `uncertain` trade (watchdog, restore) still takes a side's
        # report while that side awaits, so a later after_reset declaration is not lost
        if not pt or pt.get("phase") not in ("applying", "uncertain"):
            return
        # A stale report from an earlier aborted trade must not count toward this one. Every
        # client echoes apply_trade's token (Gen 3 emit_trade_done, Gen 1/2 trade_*), so a report
        # without it matches no trade (review e9d5e136 NIT: the empty-token compat path is gone).
        if str(msg.get("token", "") or "") != pt["token"]:
            return
        new_key = str(msg.get("new_key", "") or "")
        try:
            new_species = int(msg.get("new_species", 0) or 0)
        except (TypeError, ValueError):
            new_species = 0
        pt.setdefault("new", {"a": None, "b": None})
        pt.setdefault("done", {"a": False, "b": False})[player_id] = True
        pt.setdefault("verdict", {"a": None, "b": None})
        if pt["verdict"][player_id] not in (None, "await"):
            return                                      # already decided (a replayed report)
        pt["age"] = 0                                   # progress — reset the abandonment watchdog
        barred = self.adapter.supports_trade_recovery() and (
            self.party_hidden[player_id] or self.trade_recovery_pending[player_id]
            or pt.get("hello_only", {}).get(player_id) or msg.get("after_reset") is True)
        if msg.get("uncertain") or not new_key or barred:
            # the side cannot vouch (a reset after its commit entry, a native result 2): party evidence
            # decides. Journaled as the watchdog does, once, when the side first declares it.
            if msg.get("after_reset") is True or barred:
                # MAJOR-5: a native result 2 holds a RAM party no save ever saw; only the
                # reloaded save (the post-reset hello) is evidence for this side
                pt.setdefault("hello_only", {})[player_id] = True
                if self.adapter.supports_trade_recovery():
                    self.party_hidden[player_id] = self.trade_recovery_pending[player_id] = True
                    pt["phase"] = "uncertain"
            if pt["verdict"][player_id] is None:
                pt["verdict"][player_id] = "await"
                self._record_trade(pt, "uncertain")
            self._save()
            return
        gets = pt[f"{_partner(player_id)}_key"]
        if new_key == pt[f"{player_id}_key"]:
            pt["verdict"][player_id] = "none"           # the client still holds its own mon: refused / never swapped
        elif new_key == gets or self._is_trade_descendant(pt, player_id, new_key, new_species):
            pt["verdict"][player_id] = "traded"
            pt["new"][player_id] = (new_key, new_species)
        else:
            # MINOR-1 (review e9d5e136): never index a key the trade cannot have produced
            pt["verdict"][player_id] = f"{player_id}: reported {new_key}, neither {gets} nor its evolution"
        self._settle_trade(pt)

    def _is_trade_descendant(self, pt: dict, player_id: str, key: str, species: int) -> bool:
        """An unindexed key with the incoming mon's OT (per the adapter) and evolution family: the
        received mon under a new key, e.g. a trade evolution. Never another live link's key."""
        gets_half = pt["link"].b if player_id == "a" else pt["link"].a       # the link is not swapped yet
        fam = self.adapter.evo_family
        family = fam(gets_half.species) if gets_half and gets_half.species else None
        return bool(family is not None and species and self.entry_for(player_id, key) is None
                    and self.adapter.parse_ot_id(key) == self.adapter.parse_ot_id(pt[f"{_partner(player_id)}_key"])
                    and fam(species) == family)

    def _trade_evidence(self, player_id: str, party: list, from_hello: bool = False):
        """A party snapshot from a side whose trade outcome is uncertain decides that side:
        outgoing key gone + the incoming key (or ONE unindexed descendant: same OT per the
        adapter, same evolution family, e.g. a trade evolution) present -> traded; outgoing
        present + incoming absent -> none; anything else -> a conflict reason. Only the snapshot
        AFTER the side became uncertain counts, so a mid-scene party is never evidence."""
        pt = self.pending_trade
        if not pt or pt.get("verdict", {}).get(player_id) != "await":
            return
        if (pt.get("hello_only") or {}).get(player_id) and not from_hello:
            return                                      # MAJOR-5: wait for the reloaded save
        keys = {m.get("key") for m in party if m.get("key")}
        if not keys:
            return   # ponytail: an unreadable/empty party is no evidence; a real party is never empty
        partner = _partner(player_id)
        gives, gets = pt[f"{player_id}_key"], pt[f"{partner}_key"]
        entry = pt["link"]
        gets_half = entry.b if player_id == "a" else entry.a    # the link is not swapped yet
        if gets in keys:
            got = [(gets, gets_half.species if gets_half else 0)]
        else:
            got = [(m["key"], int(m.get("species_id") or 0)) for m in party
                   if m.get("key") and self._is_trade_descendant(pt, player_id, m["key"],
                                                                  int(m.get("species_id") or 0))]
        kept = gives in keys
        if not kept and len(got) == 1:
            pt["verdict"][player_id] = "traded"
            pt["new"][player_id] = got[0]
        elif kept and not got:
            pt["verdict"][player_id] = "none"
        elif kept:
            pt["verdict"][player_id] = f"{player_id}: holds BOTH {gives} and {got[0][0]} (duplicated)"
        elif not got:
            pt["verdict"][player_id] = f"{player_id}: holds NEITHER {gives} nor {gets}"
        else:
            pt["verdict"][player_id] = f"{player_id}: several candidates for {gets}"
        log.warning(f"[{player_id}] trade {pt['token']} evidence: {pt['verdict'][player_id]}")
        self._settle_trade(pt)

    def _settle_trade(self, pt: dict):
        """Act once BOTH sides have a verdict: both traded -> commit; both none -> roll back
        (the link was never touched); anything else -> a sticky 'conflict' surfaced through
        trade_problem(), never guessed."""
        v = pt["verdict"]
        if any(x in (None, "await") for x in v.values()):
            self._save()                                # keep the persisted verdicts current
            return
        if v["a"] == v["b"] == "traded":
            self._commit_trade(pt)
            return
        if v["a"] == v["b"] == "none":
            log.info(f"trade {pt['token']} did not happen on either side — rolled back")
            self._record_trade(pt, "rolled_back")
            self.pending_trade = None
            self._remember_trade_final(pt, "rolled_back")
            self._replay_trade_events(pt)
            self._save()
            self._announce_trade_final(pt)
            for pid in ("a", "b"):
                self.queued_commands[pid].append({
                    "cmd": "msgbox", "text": "Trade did not go through.", "fb": "prompt"})
            return
        pt["phase"] = "conflict"
        pt["problem"] = "; ".join(f"{pid}: {v[pid]}" if v[pid] in ("traded", "none") else v[pid]
                                  for pid in ("a", "b"))
        log.error(f"trade {pt['token']} CONFLICT — link left as it was, needs a human: {pt['problem']}")
        self._record_trade(pt, "conflict")
        self._save()
        for pid in ("a", "b"):
            self.queued_commands[pid].append({
                "cmd": "msgbox", "text": "TRADE ERROR - party mismatch.\nSee the status page.",
                "fb": "prompt"})

    @property
    def on_trade_outcome(self):
        return self._on_trade_outcome

    @on_trade_outcome.setter
    def on_trade_outcome(self, fn):
        # load() restores a mid-apply trade before the server can wire its journal: flush it here
        self._on_trade_outcome = fn
        if fn:
            backlog, self._trade_backlog = self._trade_backlog, []
            for rec in backlog:
                fn(rec)

    def _trade_to_json(self) -> dict | None:
        """The pending trade for links.json once apply_trade may have left (applying/uncertain/
        conflict). Earlier phases applied nothing and may be dropped. The link is stored by index."""
        pt = self.pending_trade
        if not pt or pt.get("phase") not in ("applying", "uncertain", "conflict"):
            return None
        index = next((i for i, e in enumerate(self.links) if e is pt.get("link")), None)
        if index is None:
            return None
        return {**{k: v for k, v in pt.items() if k != "link"}, "link_index": index}

    def _restore_trade(self, saved: dict):
        """BLOCKER-1 (review e9d5e136): a restart or rollback mid-trade may have lost reports and
        commands, so an `applying` trade comes back UNCERTAIN: every undecided side awaits its next
        party snapshot (_trade_evidence), and the outcome is journaled once the server wires it."""
        index = saved.get("link_index")
        if not isinstance(index, int) or not 0 <= index < len(self.links):
            log.error(f"restored trade {saved.get('token')} names no link; dropped: {saved}")
            return
        pt = {k: v for k, v in saved.items() if k != "link_index"}
        pt["link"] = self.links[index]
        pt["new"] = {pid: tuple(v) if v else None for pid, v in (pt.get("new") or {}).items()}
        pt.setdefault("verdict", {"a": None, "b": None})
        self.pending_trade = pt
        if self.adapter.supports_trade_recovery():
            for pid in ("a", "b"):
                if pt.get("recovery_epochs", {}).get(pid):
                    self.party_hidden[pid] = self.trade_recovery_pending[pid] = True
        if pt.get("phase") == "applying":
            pt["phase"] = "uncertain"
            for pid in ("a", "b"):
                if pt["verdict"].get(pid) is None:
                    pt["verdict"][pid] = "await"
            self._record_trade(pt, "uncertain")
            self._trade_backlog.append(dict(self.trade_last))
        log.warning(f"restored trade {pt['token']} as {pt['phase']}: {pt['verdict']}")

    def _record_trade(self, pt: dict, outcome: str):
        """Publish a trade outcome: trade_last (status) + on_trade_outcome (server journal)."""
        new = pt.get("new") or {}
        self.trade_last = {
            "token": pt["token"], "outcome": outcome, "at": time.time(),
            "a_key": pt["a_key"], "b_key": pt["b_key"],
            "a_new": (new.get("a") or (None,))[0], "b_new": (new.get("b") or (None,))[0],
            "verdict": dict(pt.get("verdict") or {}), "problem": pt.get("problem", "")}
        if self.on_trade_outcome:
            self.on_trade_outcome(dict(self.trade_last))

    def _remember_trade_final(self, pt: dict, verdict: str) -> None:
        """Bookkeeping only. Call after settlement, before its durable state write."""
        if not self.adapter.supports_trade_recovery():
            return
        verdict = pt.get("final_verdict", verdict)
        for pid in ("a", "b"):
            finals = self.trade_finals[pid]
            finals.setdefault(pt["token"], {"token": pt["token"], "verdict": verdict})
            while len(finals) > self.TRADE_FINAL_LIMIT:
                del finals[next(iter(finals))]

    def _queue_trade_final(self, player_id: str, token: str, epoch: int | None = None) -> None:
        if not self.adapter.supports_trade_recovery() or self.save_failed:
            return  # never retire the client's journal before the ledger is durable
        final = self.trade_finals[player_id].get(token)
        if not final:
            return
        queued = self.queued_commands[player_id]
        prior = [c for c in queued if c.get("cmd") == "trade_final" and c.get("token") == token]
        if epoch is None and prior:
            return
        queued[:] = [c for c in queued if not (c in prior and ("epoch" not in c or c["epoch"] == epoch))]
        command = {"cmd": "trade_final", "token": token, "verdict": final["verdict"]}
        if epoch is not None:
            command["epoch"] = epoch
        queued.append(command)

    def _announce_trade_final(self, pt: dict) -> None:
        for pid in ("a", "b"):
            epochs = pt.get("recovery_epochs", {}).get(pid) or [None]
            for epoch in epochs:
                self._queue_trade_final(pid, pt["token"], epoch)

    def link_in_trade(self, entry) -> bool:
        """True while the pending trade holds `entry`: removing it would settle the trade onto a link
        links.json no longer has."""
        return self.pending_trade is not None and self.pending_trade.get("link") is entry

    def trade_problem(self) -> dict | None:
        """The uncertain/conflicted trade for the status page, or None."""
        pt = self.pending_trade
        if not pt or pt.get("phase") not in ("uncertain", "conflict"):
            return None
        return {"phase": pt["phase"], "token": pt["token"], "a_key": pt["a_key"], "b_key": pt["b_key"],
                "verdict": dict(pt["verdict"]), "problem": pt.get("problem", "")}

    def trade_held(self) -> list[dict]:
        """Invariant review MAJOR-3: the events waiting on the pending trade, for the board banner
        (kept out of trade_problem(), whose shape the Gen 2 reconciliation verifier pins)."""
        pt = self.pending_trade or {}
        return [{"player": pid, "event": m.get("event"), "key": m.get("key")}
                for pid, m in pt.get("held_events", [])]

    def resolve_trade(self, token: str, action: str, sides: dict | None = None) -> tuple[bool, str]:
        """MAJOR-3 (review e9d5e136, invariant review MAJOR-3): an admin settles a conflicted or
        stuck trade by hand. A side whose outcome is KNOWN (verdict traded/none, from its report or
        its party) keeps it whatever the action, so every link half ends up naming the mon that side
        holds; the action decides only the sides the server cannot: `commit` -> traded, `rollback`
        -> none, `adopt` -> nothing (refused while a side is undecided). Contradictory evidence
        (a conflict reason such as "holds NEITHER") is never filled: the admin names that side's
        outcome in `sides` ({"a"|"b": "traded"|"none"}, which overrides any verdict). Refused while
        a side has not answered its apply at all (verdict None): its APPLY may still land. Both
        traded commits the swap, both none rolls back, one of each splits (_split_trade). Any
        undelivered apply for the trade is dropped, the slot clears, held events replay against the
        result and the outcome is journaled with "resolved by admin: <action>"."""
        pt = self.pending_trade
        if not pt or str(token) != pt.get("token") or pt.get("phase") not in ("applying", "uncertain", "conflict"):
            return False, "no applied trade with that token"
        fill = {"commit": "traded", "rollback": "none", "adopt": None}
        if action not in fill:
            return False, "action must be commit, rollback or adopt"
        sides = sides or {}
        if not isinstance(sides, dict) or any(pid not in ("a", "b") or o not in ("traded", "none")
                                              for pid, o in sides.items()):
            return False, 'sides must map "a"/"b" to "traded" or "none"'
        v = pt.get("verdict") or {}
        out = {}
        for pid in ("a", "b"):
            if pid in sides:
                out[pid] = sides[pid]
            elif v.get(pid) is None:
                return False, f"{pid} has not answered its apply yet; wait for its report or the watchdog"
            elif v[pid] in ("traded", "none"):
                out[pid] = v[pid]
            elif v[pid] != "await":
                return False, f"{v[pid]}: contradictory evidence; name that side in sides"
            elif fill[action] is None:
                return False, "adopt needs both sides' outcome known; use commit or rollback"
            else:
                out[pid] = fill[action]
        sides = out
        for pid in ("a", "b"):                          # OMP review: an undelivered APPLY must never land
            self.queued_commands[pid] = [c for c in self.queued_commands[pid]
                                         if not (c.get("cmd") in ("apply_trade", "apply_prepare")
                                                 and c.get("token") == pt["token"])]
        pt["problem"] = f"resolved by admin: {action}"
        if self.adapter.supports_trade_recovery():
            pt["final_verdict"] = "resolved"
        log.warning(f"trade {pt['token']} {pt['problem']} (verdict {v}) -> {sides}")
        if sides["a"] == sides["b"] == "traded":
            self._commit_trade(pt)
        elif sides["a"] == sides["b"] == "none":
            self._record_trade(pt, "rolled_back")
            self.pending_trade = None
            self._remember_trade_final(pt, "rolled_back")
            self._replay_trade_events(pt)
            self._save()
            self._announce_trade_final(pt)
        else:
            self._split_trade(pt, "a" if sides["a"] == "traded" else "b")
        return True, ""

    def _split_trade(self, pt: dict, taker: str):
        """Only `taker` traded: it holds a copy of the partner's mon (its reported key, else the
        partner's), its own mon is gone; the partner still holds its own. Point the taker's half
        at the copy and leave the partner's, so a death on either side reaches the other's mon."""
        entry = pt["link"]
        src = entry.b if taker == "a" else entry.a
        nk, ns = pt["new"].get(taker) or (src.key, 0)
        old = pt[f"{taker}_key"]
        setattr(entry, taker, replace(src, key=nk, species=ns or src.species))
        giver = _partner(taker)
        other = self._pidx[taker].get(nk)
        clashing = other is not None and other is not entry and other.status == LinkStatus.ALIVE
        if src.key in self.mon_stats[giver] and not clashing:     # the partner still holds the original
            self.mon_stats[taker][nk] = dict(self.mon_stats[giver][src.key])
        for gone in self._trade_old_keys(pt, taker):
            self._unindex(taker, gone, entry)
            self.party_keys[taker].discard(gone)
            self.bonus_keys[taker].discard(gone)
            if gone != nk:
                self.mon_stats[taker].pop(gone, None)
        if not clashing:
            self.party_keys[taker].add(nk)
        clash = self._index_traded(pt, entry)
        self.pending_trade = None
        self._remember_trade_final(pt, "split")
        self._replay_trade_events(pt)
        self._retire_traded_clash(entry, clash)
        self._save()
        log.warning(f"trade {pt['token']} split: {taker} holds a copy {nk}, its {old} is gone")
        self._record_trade(pt, "split")
        self._announce_trade_final(pt)
        self._trade_settle_ticks[taker] = self.TRADE_SETTLE_TICKS

    def _load_mon_stats(self, saved: dict) -> dict:
        """KEY-SCOPE-2: links.json keeps mon_stats per player ({"a": {...}, "b": {...}}). An older
        file has one flat {key: stats} map: each key goes to the player whose link, party or
        pending capture names it (keys were unique across players then), else to both."""
        if set(saved) <= {"a", "b"} and all(isinstance(v, dict) for v in saved.values()):
            return {"a": dict(saved.get("a") or {}), "b": dict(saved.get("b") or {})}
        out = {"a": {}, "b": {}}
        for key, stats in saved.items():
            owners = [pid for pid in ("a", "b")
                      if self.entry_for(pid, key) is not None or key in self.party_keys[pid]
                      or any(players.get(pid) and players[pid].key == key
                             for players in self.pending_captures.values())] or ["a", "b"]
            for pid in owners:
                out[pid][key] = stats
        return out

    def _index_traded(self, pt: dict, entry: "LinkEntry") -> list:
        """Index a swapped or split trade link. KEY-SCOPE-2: a half whose key already names
        ANOTHER live link of the same player cannot be told apart from it; that link keeps the
        index row, held events naming the key are dropped, and _retire_traded_clash retires
        the traded pair (the U5 fail-closed rule of a colliding key_change)."""
        clash = []
        for pid, half in (("a", entry.a), ("b", entry.b)):
            other = self._pidx[pid].get(half.key) if half else None
            if other is not None and other is not entry and other.status == LinkStatus.ALIVE:
                clash.append((pid, half.key, other))
        self._index_entry(entry)
        for pid, key, other in clash:
            self._pidx[pid][key] = other
            self.ambiguous_keys[pid][key] = {"since": datetime.now(UTC).isoformat(), "refused": 0}
            self.party_keys[pid].discard(key)
            log.error(f"[{pid}] trade {pt['token']}: {key} already identifies a live link in "
                      f"{other.area_id}; the traded pair in {entry.area_id} is retired identity_lost")
        dropped = {(pid, key) for pid, key, _ in clash}
        if dropped:
            pt["held_events"] = [e for e in pt.get("held_events", [])
                                 if (e[0], e[1].get("key")) not in dropped]
        return clash

    def _retire_traded_clash(self, entry: "LinkEntry", clash: list):
        if not clash or entry.status != LinkStatus.ALIVE:
            return
        pid, key, _other = clash[0]
        self._propagate_faint(pid, entry, cause="identity_lost")
        for cpid, ckey, _ in clash:            # the key names a live mon too: never bury by it
            self.pending_memorials[cpid].discard(ckey)
            self.queued_commands[cpid] = [c for c in self.queued_commands[cpid]
                                          if not (c.get("cmd") in ("memorialize",) + DEATH_COMMANDS
                                                  and c.get("key") == ckey)]

    @staticmethod
    def _trade_old_keys(pt: dict, pid: str) -> set[str]:
        """The key pid gave in the trade: current, plus the original a mid-window key_change
        migrated (invariant review MINOR-8)."""
        return {k for k in (pt[f"{pid}_key"], pt.get(f"{pid}_key0")) if k}

    def _commit_trade(self, pt: dict):
        """Apply the swap atomically, once both sides are known to have traded; pt["new"] holds
        each side's post-trade (key, species), including any trade-evolution."""
        # ── apply the swap atomically ─────────────────────────────────────────────
        # The mon DATA moves to the other player (entry.a now tracks the mon A holds, entry.b the mon B
        # holds); then each half's key/species is patched from that side's post-scene readback (captures
        # trade-evolution). The per-player index + party_keys are rebuilt from the two OLD keys to the two NEW keys.
        entry = pt["link"]
        entry.a, entry.b = entry.b, entry.a             # A now owns B's old mon and vice-versa
        for pid in ("a", "b"):                          # KEY-SCOPE: each key leaves its giver's index
            for old in self._trade_old_keys(pt, pid):
                self._unindex(pid, old, entry)
        for pid, half in (("a", entry.a), ("b", entry.b)):
            nk, ns = (pt["new"].get(pid) or ("", 0))
            if half and nk:
                half.key = nk
                if ns:
                    half.species = ns
        # party_keys: A drops its traded-away key + gains the mon it now holds (entry.a's key); same for B.
        self.party_keys["a"] -= self._trade_old_keys(pt, "a")
        self.party_keys["a"].add(entry.a.key)
        self.party_keys["b"] -= self._trade_old_keys(pt, "b")
        self.party_keys["b"].add(entry.b.key)
        clash = self._index_traded(pt, entry)           # KEY-SCOPE-2: a same-player clash fails closed
        # MINOR-6: per-key bookkeeping follows each mon to its new holder (and trade-evolved key)
        clashed = {(pid, key) for pid, key, _ in clash}
        for giver, taker in (("a", "b"), ("b", "a")):
            new = (pt["new"].get(taker) or ("", 0))[0] or pt[f"{giver}_key"]
            if (taker, new) in clashed:
                continue                                # KEY-SCOPE-3: never onto the surviving twin
            for old in self._trade_old_keys(pt, giver):
                if old in self.bonus_keys[giver]:
                    self.bonus_keys[giver].discard(old)
                    self.bonus_keys[taker].add(new)
                if old in self.mon_stats[giver]:           # the stats move to the new holder
                    self.mon_stats[taker][new] = self.mon_stats[giver].pop(old)

        for pid in ("a", "b"):
            gives = pt["a_label"] if pid == "a" else pt["b_label"]
            gets  = pt["b_label"] if pid == "a" else pt["a_label"]
            self.queued_commands[pid].append({"cmd": "play_sound", "sound": 25})   # SE_SUCCESS
            self.queued_commands[pid].append({
                "cmd": "msgbox", "text": f"Traded {gives} for {gets}!",
                "r": 100, "g": 255, "b": 160, "frames": 300})
        self.pending_trade = None
        self._remember_trade_final(pt, "committed")
        self._replay_trade_events(pt)                   # MAJOR-2: after the swap, against the real holders
        self._retire_traded_clash(entry, clash)
        self._save()
        log.info(f"trade complete (token {pt['token']})")
        self._record_trade(pt, "committed")
        self._announce_trade_final(pt)
        # Arm the post-trade settle window on BOTH sides: the swap is party↔party, so suppress the
        # drift reconciler while each client's party read settles (no spurious "Unbox" party_mon).
        self._trade_settle_ticks = {"a": self.TRADE_SETTLE_TICKS, "b": self.TRADE_SETTLE_TICKS}

    @classmethod
    def load(cls, data_dir: str = None, species_lock: bool = False, gender_lock: bool = False, type_lock: bool = False, explode_mode: bool = False, is_rr: bool = False, adapter: GameRulesAdapter = None, rival_team_swap: bool = False, overworld_presence: bool = False, native_messages: bool = False, native_sounds: bool = False, battle_calc: bool = True, pc_trade_npc: bool = True, phone_calls: bool = True, retry_same_species: bool = True, ignore_wild_faints: bool = False) -> "SoulLinkState":
        """Load persisted state from data/links.json, or return a fresh instance."""
        state = cls(data_dir=data_dir, species_lock=species_lock, gender_lock=gender_lock, type_lock=type_lock, explode_mode=explode_mode, is_rr=is_rr, adapter=adapter, rival_team_swap=rival_team_swap, overworld_presence=overworld_presence, native_messages=native_messages, native_sounds=native_sounds, battle_calc=battle_calc, pc_trade_npc=pc_trade_npc, phone_calls=phone_calls, retry_same_species=retry_same_species, ignore_wild_faints=ignore_wild_faints)
        try:
            with open(state._links_path) as f:
                data = json.load(f)
        except FileNotFoundError:
            return state
        except (OSError, ValueError) as exc:
            state.load_failed = f"{type(exc).__name__}: {exc}"
            log.error(f"Failed to read {state._links_path}: {state.load_failed}")
            return state
        try:
            for ed in data.get("links", []):
                a = MonInfo(**ed["a"]) if ed.get("a") else None
                b = MonInfo(**ed["b"]) if ed.get("b") else None
                enc_a = MonInfo(**ed["encounter_a"]) if ed.get("encounter_a") else None
                enc_b = MonInfo(**ed["encounter_b"]) if ed.get("encounter_b") else None
                entry = LinkEntry(
                    area_id=ed["area_id"],
                    a=a, b=b,
                    status=LinkStatus(ed["status"]),
                    encounter_a=enc_a,
                    encounter_b=enc_b,
                    killed_at=ed.get("killed_at"),
                    cause=ed.get("cause", ""),
                    killer=ed.get("killer"),
                    initiating_player=ed.get("initiating_player", ""),
                )
                state.links.append(entry)
                state._index_entry(entry)
            for area_id, status_str in data.get("area_states", {}).items():
                state.area_states[area_id] = AreaStatus(status_str)
            for area_id, players in data.get("pending_captures", {}).items():
                state.pending_captures[area_id] = {
                    pid: MonInfo(**mon_data)
                    for pid, mon_data in players.items()
                }
            for area_id, players in data.get("unpaired_mons", {}).items():
                if not isinstance(area_id, str) or not isinstance(players, dict):
                    continue
                restored = {
                    pid: MonInfo(**mon_data)
                    for pid, mon_data in players.items()
                    if pid in ("a", "b") and isinstance(mon_data, dict)
                }
                if restored:
                    state.unpaired_mons[area_id] = restored
            for area_id, players in data.get("encounter_locks", {}).items():
                if not isinstance(area_id, str) or not isinstance(players, dict):
                    continue
                locks = {}
                for pid, lock_data in players.items():
                    if pid not in ("a", "b") or not isinstance(lock_data, dict):
                        continue
                    species = lock_data.get("species")
                    level = lock_data.get("level", 0)
                    if (isinstance(species, int) and not isinstance(species, bool) and species > 0
                            and isinstance(level, int) and not isinstance(level, bool) and level >= 0):
                        locks[pid] = EncounterLock(species=species, level=level)
                if locks:
                    state.encounter_locks[area_id] = locks
            state.mon_stats = state._load_mon_stats(data.get("mon_stats") or {})
            # Restore Pokéball gate; default True for both if any links exist
            # (backwards-compat: old saves without this field).
            saved_pb = data.get("pokeballs_obtained", {})
            if saved_pb:
                state.pokeballs_obtained["a"] = bool(saved_pb.get("a", False))
                state.pokeballs_obtained["b"] = bool(saved_pb.get("b", False))
            elif state.links or state.pending_captures:
                # Old save without field but has game state → infer both had Pokéballs.
                state.pokeballs_obtained = {"a": True, "b": True}
            # Restore pending memorials (will be re-queued on next hello from each player).
            saved_pm = data.get("pending_memorials", {})
            state.pending_memorials["a"] = set(saved_pm.get("a", []))
            state.pending_memorials["b"] = set(saved_pm.get("b", []))
            # Restore lock rules from persisted state (CLI flags are initial defaults;
            # saved values take precedence so mid-run restarts honor the original config).
            saved_rules = data.get("rules", {})
            if saved_rules:
                state.species_lock = bool(saved_rules.get("species_lock", species_lock))
                state.gender_lock = bool(saved_rules.get("gender_lock", gender_lock))
                state.type_lock = bool(saved_rules.get("type_lock", type_lock))
                state.retry_same_species = bool(saved_rules.get("retry_same_species", retry_same_species))
                state.ignore_wild_faints = bool(saved_rules.get("ignore_wild_faints", ignore_wild_faints))
                state.explode_mode = bool(saved_rules.get("explode_mode", explode_mode))
                state.rival_team_swap = bool(saved_rules.get("rival_team_swap", rival_team_swap))
                state.overworld_presence = False  # deferred post-RC; see __init__
                state.native_messages = False  # disabled for the RC; see __init__
                state.native_sounds = bool(saved_rules.get("native_sounds", native_sounds))
                state.battle_calc = bool(saved_rules.get("battle_calc", battle_calc))
                state.pc_trade_npc = bool(saved_rules.get("pc_trade_npc", pc_trade_npc))
                state.phone_calls = bool(saved_rules.get("phone_calls", phone_calls))
            state.run_over = bool(data.get("run_over", False))
            state.attempts_count = int(data.get("attempts_count", 0))
            state.rom_type = data.get("rom_type", "")
            state.artifact_kind = data.get("artifact_kind", "")
            # Infer is_rr from persisted rom_type (belt-and-suspenders with CLI flag)
            if state.rom_type.endswith("_rr"):
                state.is_rr = True
            # Restore game_id — validate adapter matches if one was provided.
            saved_game_id = data.get("game_id", "")
            effective_rr = state.is_rr
            if saved_game_id and state.adapter.game_id != saved_game_id:
                # A rom_type cutover (e.g. Gen 2's U5) may have moved this rom_type onto a
                # different game_id since this run was saved. Refuse rather than silently
                # reopening it under either the stale saved adapter or a mismatched new one
                # when the registry says that specific migration is unsafe.
                from server.adapters import game_id_for_rom_type, persisted_migration_refusal
                current_game_id = game_id_for_rom_type(state.rom_type) if state.rom_type else None
                refusal = persisted_migration_refusal(saved_game_id, current_game_id)
                if refusal:
                    raise UnsafeGameMigration(f"{state._links_path}: {refusal}")
                log.warning(f"Saved game_id={saved_game_id!r} differs from adapter "
                            f"game_id={state.adapter.game_id!r}; using saved game_id")
                # Re-resolve adapter from registry if available
                try:
                    from server.adapters import get_adapter
                    # rom_type is restored above and selects per-variant data inside the
                    # adapter (e.g. Gen 1's Red/Blue/Yellow encounter tables).
                    state.adapter = get_adapter(saved_game_id, is_rr=effective_rr,
                                                rom_type=state.rom_type)
                except (KeyError, ImportError):
                    log.warning(f"No adapter for saved game_id={saved_game_id!r}; keeping current adapter")
            elif (effective_rr != getattr(state.adapter, '_is_rr', False)
                  or getattr(state.adapter, "rom_type", state.rom_type) != state.rom_type):
                # Adapter game_id matches but is_rr differs, or the adapter reports a rom_type
                # other than the saved one (one game_id can carry several titles whose data
                # the rom_type selects) — recreate
                try:
                    from server.adapters import get_adapter
                    state.adapter = get_adapter(state.adapter.game_id, is_rr=effective_rr,
                                                rom_type=state.rom_type)
                except (KeyError, ImportError, ValueError):
                    # ValueError: a fail-closed adapter refusing the saved rom_type; keep the
                    # current adapter rather than half-load the run (OMP cx-6ecf4fc8 #2)
                    log.warning(f"Could not rebuild the adapter for rom_type={state.rom_type!r}; "
                                "keeping the current adapter")
            saved_names = data.get("trainer_names", {})
            if saved_names:
                state.trainer_names["a"] = saved_names.get("a", "")
                state.trainer_names["b"] = saved_names.get("b", "")
            # Restore player identity lock
            state.player_identity = data.get("player_identity", {})
            # Restore lock-clause retry areas
            saved_retry = data.get("retry_areas", {})
            state.retry_areas["a"] = set(saved_retry.get("a", []))
            state.retry_areas["b"] = set(saved_retry.get("b", []))
            # Restore shiny clause bonus keys
            saved_bonus = data.get("bonus_keys", {})
            state.bonus_keys["a"] = set(saved_bonus.get("a", []))
            state.bonus_keys["b"] = set(saved_bonus.get("b", []))
            # Restore pending bonus encounters (FIFO queue per player)
            saved_pending = data.get("pending_bonus", {})
            state.pending_bonus["a"] = deque(saved_pending.get("a", []))
            state.pending_bonus["b"] = deque(saved_pending.get("b", []))
            saved_amb = data.get("ambiguous_keys") or {}
            state.ambiguous_keys = {pid: dict(saved_amb.get(pid) or {}) for pid in ("a", "b")}
            saved_ledger = data.get("key_migration_ledger") or {}
            state.key_migration_ledger = {pid: deque(
                ({"old_key": r["old_key"], "new_key": r["new_key"]} for r in saved_ledger.get(pid) or []
                 if isinstance(r, dict) and isinstance(r.get("old_key"), str)
                 and isinstance(r.get("new_key"), str)),
                maxlen=KEY_MIGRATION_LEDGER_LIMIT) for pid in ("a", "b")}
            # Restore in-flight auto-rebuild context.
            saved_rebuild = data.get("rebuild_pending", {})
            for pid in ("a", "b"):
                rb = saved_rebuild.get(pid)
                if rb:
                    state.rebuild_pending[pid] = {
                        "started_at":         rb.get("started_at", ""),
                        "queued_keys":        list(rb.get("queued_keys", [])),
                        "queued_partner_keys": list(rb.get("queued_partner_keys", [])),
                        "restored_keys":      set(rb.get("restored_keys", [])),
                    }
            state._trade_token = int(data.get("trade_token", 0) or 0)
            if data.get("pending_trade"):
                state._restore_trade(data["pending_trade"])
            if state.adapter.supports_trade_recovery() and "trade_recovery" in data:
                recovery = data["trade_recovery"]
                for pid in ("a", "b"):
                    row = recovery.get(pid) if isinstance(recovery, dict) else None
                    valid = (isinstance(row, dict) and type(row.get("pending")) is bool
                             and type(row.get("hidden")) is bool and isinstance(row.get("problem", ""), str))
                    state.trade_recovery_pending[pid] = row["pending"] if valid else True
                    state.party_hidden[pid] = (row["hidden"] if valid else True) or state.trade_recovery_pending[pid]
                    problem = row.get("problem", "") if valid else "Stored recovery information is unreadable"
                    state.trade_recovery_errors[pid] = problem if isinstance(problem, str) else "Stored recovery information is unreadable"
            if state.adapter.supports_trade_recovery():
                saved_finals = data.get("trade_finals", {})
                for pid in ("a", "b"):
                    rows = saved_finals.get(pid, []) if isinstance(saved_finals, dict) else []
                    for row in rows if isinstance(rows, list) else []:
                        if (isinstance(row, dict) and isinstance(row.get("token"), str) and row["token"]
                                and isinstance(row.get("verdict"), str)
                                and row["verdict"] in ("committed", "rolled_back", "split", "resolved")
                                and (not state.pending_trade or row["token"] != state.pending_trade["token"])):
                            state.trade_finals[pid][row["token"]] = {"token": row["token"], "verdict": row["verdict"]}
                    state.trade_finals[pid] = dict(list(state.trade_finals[pid].items())[-state.TRADE_FINAL_LIMIT:])
            # Undelivered partner syncs, last: only for a key that is still a live link's half. A key the
            # ledger shows migrated (evolution, NPC trade) or a dead pair's half would reach a cartridge
            # that no longer holds it and come back as a failed sync.
            saved_sync = data.get("queued_sync")
            if isinstance(saved_sync, dict):
                for pid in ("a", "b"):
                    migrated = {r["old_key"] for r in state.key_migration_ledger[pid]}
                    for c in saved_sync.get(pid) or []:
                        if not (isinstance(c, dict) and c.get("cmd") in DURABLE_SYNC_COMMANDS and c.get("key")):
                            continue
                        entry = state.entry_for(pid, c["key"])
                        if (c["key"] in migrated or c["key"] in state.pending_memorials[pid]
                                or entry is None or entry.status != LinkStatus.ALIVE):
                            continue
                        state.queued_commands[pid].append(c)
            log.info(f"Loaded {len(state.links)} links from {state._links_path}")
        except UnsafeGameMigration:
            # Operator-facing and fatal: this run must not start under either adapter.
            # Not caught below — a corrupt-file warning-and-continue would silently
            # resume it under the stale adapter, exactly what this refusal prevents.
            raise
        except Exception as e:
            state.load_failed = f"{type(e).__name__}: {e}"
            log.error(f"Failed to load {state._links_path}: {state.load_failed}")
        return state

    # ── event handlers ───────────────────────────────────────────────────────

    def _set_area_state(self, area_id: str, new_status: AreaStatus, *,
                        player: str = "", reason: str = "") -> None:
        """Set area_states[area_id] and emit a DEBUG log when the value changes."""
        old = self.area_states.get(area_id, AreaStatus.UNSEEN)
        self.area_states[area_id] = new_status
        if old != new_status:
            ctx_parts = []
            if player:
                ctx_parts.append(f"player={player}")
            if reason:
                ctx_parts.append(f"reason={reason}")
            ctx = "  " + "  ".join(ctx_parts) if ctx_parts else ""
            log.debug(f"[AREA] {area_id}: {old.value} → {new_status.value}{ctx}")

    def _handle_hello(self, player_id: str, msg: dict):
        """
        Reconcile on reconnect.
        Only flags mons that are still IN the party with hp == 0 as newly fainted —
        missing-from-party mons may simply be boxed (do not treat as dead).
        """
        refused = (self.adapter.refused_trade_recovery()
                   if "party_hidden" in msg or "trade_outstanding" in msg else "")
        if refused:
            self.identity_error[player_id] = refused
            msg["_rejected"] = True
            self.queued_commands[player_id].append({"cmd": "noop", "refused": "trade_recovery"})
            return
        error = self._trade_recovery_error(msg)
        hidden = self.party_snapshot_withheld(player_id, msg)
        party = [] if hidden else msg.get("party", [])

        # ── Identity lock ── (before anything about this hello is adopted: a wrong save must
        # leave party_size / the blob cache exactly as they were)
        # PREFER THE CARTRIDGE'S OWN TRAINER ID over one inferred from a mon.
        # Deriving it from party[0]'s key makes the lock depend on which mon happens to be
        # in the lead slot: an in-game-trade mon is a DIFFERENT OT by definition, so leading
        # with one locked the run to the wrong trainer permanently and every later hello was
        # rejected as WRONG SAVE. A client that reports `ot_id` is telling us what the save
        # says; only fall back to the mon key for clients that do not.
        incoming_ot = str(msg.get("ot_id") or "").strip() or None
        if hidden and not incoming_ot:
            self.identity_error[player_id] = "Withheld party requires explicit trainer identity"
            msg["_rejected"] = True
            self.queued_commands[player_id].append({"cmd": "noop", "refused": "identity"})
            return
        if not incoming_ot and party:
            first_key = party[0].get("key", "")
            incoming_ot = self.adapter.parse_ot_id(first_key)
        incoming_name = msg.get("trainer_name", "")

        # Capability declaration (card C5-10b, hardened per Codex's C5-10 review): adopted only
        # AFTER the identity-lock checks below have passed. A hello rejected as a wrong save must
        # not be able to grant or revoke anything -- otherwise a spoofed or misdirected hello
        # could flip the manual-inject gate for this slot.
        declare_identity = msg.get("battle_identity") is True

        if incoming_ot:
            existing = self.player_identity.get(player_id)
            if existing:
                if existing["ot_id"] != incoming_ot:
                    err = (f"Identity mismatch for slot {player_id.upper()}: "
                           f"expected {existing['trainer_name']} "
                           f"(OT {existing['ot_id'][:8]}), "
                           f"got {incoming_name or '?'} "
                           f"(OT {incoming_ot[:8]})")
                    log.warning(f"[{player_id}] REJECTED: {err}")
                    self.identity_error[player_id] = err
                    # Queue rejection commands — disconnect HUD + refuse processing
                    self.queued_commands[player_id].append({
                        "cmd": "hud_show",
                        "text": f"[x] WRONG SAVE: slot {player_id.upper()}",
                        "r": 255, "g": 0, "b": 0, "frames": 600,
                    })
                    # Signal the hello was rejected — caller checks this flag
                    msg["_rejected"] = True
                    return
                else:
                    # Identity matches — clear any previous error
                    self.identity_error.pop(player_id, None)
                    log.debug(f"[IDENTITY] player={player_id}  stored_ot={existing['ot_id'][:8]}  "
                              f"incoming_ot={incoming_ot[:8]}  result=ok")
                    # Update trainer name if it changed (e.g. first connect had no name)
                    if incoming_name:
                        self.player_identity[player_id]["trainer_name"] = incoming_name
                        self.player_identity[player_id].pop("trainer_name_placeholder", None)
                        self._save()
            else:
                # First hello with a party — lock identity
                self.player_identity[player_id] = {
                    "ot_id": incoming_ot,
                    "trainer_name": incoming_name or player_id.upper(),
                    # PHONE-NAMES F8: the slot letter is synthesized, never a name to show
                    **({} if incoming_name else {"trainer_name_placeholder": True}),
                }
                self.identity_error.pop(player_id, None)
                log.info(f"[{player_id}] Identity locked: {incoming_name or player_id.upper()} (OT {incoming_ot[:8]})")
                self._save()

        # Adopted here: past every identity-lock return above.
        self.battle_identity_clients[player_id] = declare_identity
        if not declare_identity:
            # C5-11c MAJOR 7: an ACCEPTED legacy hello also clears the stored battle identity, so
            # a client that does not speak the protocol cannot leave a stale pair behind for the
            # manual inject to inherit.
            self.latest_battle_requests.pop(player_id, None)

        if self.adapter.supports_trade_recovery():
            before_recovery = (self.trade_recovery_pending[player_id], self.party_hidden[player_id],
                               self.trade_recovery_errors[player_id])
            records = [] if error else [{"token": r["token"], "epoch": int(r["epoch"])}
                                        for r in msg.get("trade_outstanding", [])]
            self.trade_recovery_errors[player_id] = error
            changed_trade = False
            if error:
                self.trade_recovery_pending[player_id] = True
                pt = self.pending_trade
                if pt:
                    self.queued_commands[player_id] = [c for c in self.queued_commands[player_id]
                        if not (c.get("cmd") in ("apply_trade", "apply_prepare") and c.get("token") == pt["token"])]
                if (pt and pt.get("phase") in ("applying", "uncertain")
                        and pt["verdict"].get(player_id) in (None, "await")):
                    changed_trade = (pt["verdict"].get(player_id) is None or pt["phase"] != "uncertain"
                                     or not pt.get("hello_only", {}).get(player_id))
                    pt["verdict"][player_id] = "await"
                    pt["phase"] = "uncertain"
                    pt.setdefault("hello_only", {})[player_id] = True
                    if changed_trade:
                        self._record_trade(pt, "uncertain")
            elif records:
                self._declare_trade_outstanding(player_id, records)
            elif not hidden:
                self.trade_recovery_pending[player_id] = False
            self.party_hidden[player_id] = hidden
            if changed_trade or before_recovery != (self.trade_recovery_pending[player_id], hidden, error):
                self._save()
        if not hidden:
            old_size = self.party_size.get(player_id, 0)
            self.party_size[player_id] = len(party)
            log.debug(f"[PARTY] player={player_id}  party_size {old_size} → {len(party)}  (hello)")
            # Rival Team Swap: refresh the per-player blob cache from the same
            # snapshot.  Each party entry carries blob_hex (200 chars) from
            # build_party_snapshot — see archive/gen3-old-client:lua/clients/gen3_frlge_client.lua.
            self._ingest_party_blobs(player_id, party)

            # Accept pokéballs status from Lua (M.hasPokeballs() reads actual bag).
            # If the field is absent (old client), fall back to non-empty party heuristic.
            has_pokeballs = msg.get("has_pokeballs")
            if has_pokeballs is True or has_pokeballs is None and party:
                self.pokeballs_obtained[player_id] = True

            # Rebuild party_keys from the snapshot
            self._has_helld.add(player_id)
            # A new hello from player_id means a fresh session start for that player.
            # Discard the partner from _has_helld so any events arriving before the partner's
            # hello use the optimistic path (exec_box_mon is idempotent if key not in party).
            # This prevents stale party_keys from the previous session blocking sync commands.
            self._has_helld.discard(_partner(player_id))
            self.party_keys[player_id] = {
                m["key"] for m in party if m.get("key") and m.get("maxHP", 0) > 0
            }
            # Strip dead/memorial mons that may have been re-added (e.g. hp=0 mon still in party
            # slot when a reconnect happens before the Lua sends the faint event back).
            for _k in list(self.party_keys[player_id]):
                _e = self.entry_for(player_id, _k)
                if (_e and _e.status in (LinkStatus.DEAD, LinkStatus.MEMORIAL)
                        or _k in self.ambiguous_keys[player_id]):
                    self.party_keys[player_id].discard(_k)

            # Re-quarantine: if any pending (unlinked) captures are in the party,
            # remove from party_keys and re-queue box_mon so they go back to the box.
            # Safety: never quarantine if it would leave the player with no alive mons.
            alive_keys = {m["key"] for m in party if m.get("key") and m.get("hp", 0) > 0}
            quarantined = set()
            for area_id, players in self.pending_captures.items():
                cap = players.get(player_id)
                if cap and cap.key in self.party_keys[player_id]:
                    remaining_alive = alive_keys - quarantined - {cap.key}
                    if not remaining_alive:
                        log.info(f"[{player_id}] skip re-quarantine: {cap.key[:8]} (no alive mons would remain)")
                        continue
                    quarantined.add(cap.key)
                    self.party_keys[player_id].discard(cap.key)
                    self.queued_commands[player_id].append({"cmd": "box_mon", "key": cap.key})
                    log.info(f"[{player_id}] re-quarantine on hello: {cap.key[:8]} (pending in {area_id})")

            # Invariant review MAJOR-2: a hello that settles a pending trade must do so BEFORE its
            # own deaths are routed, so they follow the swapped link.
            pt = self.pending_trade
            if pt and pt.get("phase") == "applying" and pt.get("verdict", {}).get(player_id) is None:
                # A fresh hello while this side never reported: a Lua reload lost its owed trade_done.
                # The hello is sent only at the checkpoint, never inside the trade scene, so its party
                # is evidence now rather than after the watchdog.
                pt["verdict"][player_id] = "await"
                self._record_trade(pt, "uncertain")
            self._trade_evidence(player_id, party, from_hello=True)

        for m in party:
            key = m.get("key", "")
            hp  = m.get("hp", 1)
            if not key:
                continue
            if hp > 0:
                # Mon is alive — log confirmation if it's a known linked mon.
                entry = self.entry_for(player_id, key)
                if entry and entry.status == LinkStatus.ALIVE:
                    log.debug(f"[RECONCILE] player={player_id}  key={key[:8]}  decision=alive_confirmed")
                continue
            # Mon is in party but hp == 0 → it fainted while we were disconnected.
            # Only treat as a Soul Link death if the nuzlocke run was already active.
            if not self.pokeballs_obtained[player_id]:
                log.debug(f"[RECONCILE] player={player_id}  key={key[:8]}  decision=ignored  reason=pre_nuzlocke")
                continue
            entry = self.entry_for(player_id, key)
            if key in self.ambiguous_keys[player_id]:
                log.error(f"[{player_id}] hello: {key} at hp 0 is ambiguous (trade clash) — not propagated")
                continue
            if self._hold_for_trade(player_id, {"event": "faint", "key": key, "_level": m.get("level", 0)}):
                continue                                # a trade key: replayed once the trade settles
            if entry and entry.status == LinkStatus.ALIVE:
                log.info(f"[RECONCILE] player={player_id}  key={key[:8]}  decision=faint_detected  reason=hp=0_in_party")
                self._propagate_faint(player_id, entry, level=m.get("level", 0))
            else:
                reason = "not_linked" if not entry else f"status={entry.status.value}"
                log.debug(f"[RECONCILE] player={player_id}  key={key[:8]}  decision=ignored  reason={reason}")

        # Invariant review MINOR-6: a dead mon alive in the party (a restart lost its queued
        # force_faint) is killed BEFORE the memorials below bury it -- the client runs commands in
        # arrival order, and a memorialize first would bury it at full HP. Same guards as O-24.
        if not self.run_over:
            for m in party:
                key, hp = m.get("key"), m.get("hp")
                entry = self.entry_for(player_id, key) if key else None
                half = entry and (entry.a if player_id == "a" else entry.b)
                if (isinstance(hp, (int, float)) and hp > 0 and half and half.key == key
                        and entry.status != LinkStatus.ALIVE and not self._is_quarantined(player_id, key)
                        and not self._has_pending_command(player_id, key, *DEATH_COMMANDS)):
                    self.queued_commands[player_id].append(
                        {"cmd": "force_faint", "key": key, "nickname": half.nickname or ""})
                    log.warning(f"[{player_id}] hello: dead {key[:8]} alive in party — force_faint before burial")

        if not hidden:
            # Re-queue any memorials that were not yet confirmed before disconnecting
            for key in list(self.pending_memorials[player_id]):
                if not any(c.get("cmd") == "memorialize" and c.get("key") == key
                           for c in self.queued_commands[player_id]):
                    self.queued_commands[player_id].append({"cmd": "memorialize", "key": key})
                    log.info(f"[{player_id}] reconnect: re-queued memorialize for {key[:8]}")

        # Dead/memorial mons in party → re-memorialize (handles script reload case
        # where Lua's memorialized_keys is lost and player retrieved a dead mon).
        for m in party:
            key = m.get("key", "")
            if not key:
                continue
            entry = self.entry_for(player_id, key)
            if (entry and entry.status in (LinkStatus.DEAD, LinkStatus.MEMORIAL)
                    and not any(c.get("cmd") == "memorialize" and c.get("key") == key
                                for c in self.queued_commands[player_id])):
                self.queued_commands[player_id].append({"cmd": "memorialize", "key": key})
                self.queued_commands[player_id].append({
                    "cmd": "hud_show",
                    "text": "[x] Dead in party -> grave",
                    "r": 255, "g": 80, "b": 80
                })
                log.warning(f"[{player_id}] hello: {key[:8]} is dead/memorial but in party — re-memorializing")

        # Back-fill display data (nickname/species) for any linked mons whose MonInfo
        # was loaded from links.json without this data (e.g., captured in a prior session).
        display_updated = False
        for m in party:
            key  = m.get("key", "")
            nick = m.get("nickname", "")
            sid  = m.get("species_id", 0)
            if not key or (not nick and not sid):
                continue
            entry = self.entry_for(player_id, key)
            if not entry:
                continue
            # the half whose key this is: mid-trade a party holds the PARTNER's key, and writing
            # its species onto this player's own half broke the trade-evolution family check
            mon = next((h for h in (entry.a, entry.b) if h and h.key == key), None)
            if mon:
                if nick and not mon.nickname:
                    mon.nickname = nick
                    display_updated = True
                if sid and mon.species != sid:
                    mon.species = sid
                    display_updated = True
        if display_updated:
            self._save()

        # Send resolved areas to the client so the encounter HUD doesn't re-fire
        # after a script reload or reconnect.
        self._queue_resolved_areas(player_id)

        # Tell the client the run's patch-feature config. overworld_presence: when OFF, the
        # companion patch's Pokémon-Center trade NPC becomes the trade entry point (the peer
        # ghost — and its talk-to-trade trigger — only exists when presence is ON); the client
        # gates the PC-NPC driver on pc_trade_npc AND NOT overworld_presence. native_messages /
        # native_sounds pick the patch path over the Lua HUD fallback; battle_calc drives the
        # patch's calc config byte (display is baked into the patched ROM, the byte hides it).
        self.queued_commands[player_id].append({
            "cmd": "config", "overworld_presence": bool(self.overworld_presence),
            "native_messages": bool(self.native_messages),
            "native_sounds": bool(self.native_sounds),
            "battle_calc": bool(self.battle_calc),
            "pc_trade_npc": bool(self.pc_trade_npc),
            "phone_calls": bool(self.phone_calls),
        })

        self.awaiting_save[player_id] = False   # BURIAL-VISIBLE: a waiting client re-asserts it on its ticks
        # INV-CLIENT-2: this player's dead/memorial keys, authoritative. The GB clients REPLACE their
        # re-zero set with it (a debug revive, an unlink, a rollback or another save with the same OT
        # leaves no stale kill); a client that does not know the command ignores it.
        self.queued_commands[player_id].append({"cmd": "dead_keys", "keys": sorted(
            half.key for e in self.links if e.status in (LinkStatus.DEAD, LinkStatus.MEMORIAL)
            for half in ((e.a if player_id == "a" else e.b),) if half and half.key)})

        # Re-arm an in-flight rebuild: reconcile restored_keys from the fresh
        # party snapshot (some party_mons may have executed before disconnect),
        # then re-queue party_mon + rebuild_start for any still-outstanding
        # keys so the auto-rebuild resumes seamlessly.
        rb = self.rebuild_pending.get(player_id)
        if rb and not hidden:
            queued_keys = list(rb.get("queued_keys", []))
            restored = set(rb.get("restored_keys", set()))
            party_now = self.party_keys[player_id]
            for k in queued_keys:
                if k in party_now:
                    restored.add(k)
            rb["restored_keys"] = restored
            outstanding_picks: list[MonInfo] = []
            for k in queued_keys:
                if k in restored:
                    continue
                entry = self.entry_for(player_id, k)
                if not entry:
                    continue
                mon = entry.a if player_id == "a" else entry.b
                if mon:
                    outstanding_picks.append(mon)
            if outstanding_picks:
                self._queue_rebuild_commands(player_id, outstanding_picks, [])
                log.info(f"[{player_id}] reconnect: re-armed rebuild — "
                         f"{len(outstanding_picks)} outstanding")
            self._maybe_finish_rebuild(player_id)

        # Re-send game_over if the run was already over before this reconnect
        if self.run_over:
            self.queued_commands[player_id].append({"cmd": "game_over"})

        if self.adapter.supports_trade_recovery() and not error:
            for record in records:
                self._queue_trade_final(player_id, record["token"], record["epoch"])

    def _queue_resolved_areas(self, player_id: str) -> None:
        """Replace one client's resolved-area set from authoritative server state."""
        resolved = {
            area_id
            for area_id, status in self.area_states.items()
            if status in (AreaStatus.LINKED, AreaStatus.DEAD_ZONE)
        }
        resolved.update(
            area_id
            for area_id, players in self.pending_captures.items()
            if player_id in players
        )
        self.queued_commands[player_id].append({
            "cmd": "resolved_areas",
            "areas": sorted(resolved),
        })

    def _adapter_for(self, player_id: str):
        return self.player_adapter_for(player_id) if self.player_adapter_for else self.adapter

    def _handle_area_enter(self, player_id: str, msg: dict):
        area_id = msg.get("area_id", "")
        if not area_id:
            return
        old_retry = self.retry_areas[player_id]
        self.retry_areas[player_id] = {a for a in old_retry if a == area_id}
        retry_changed = old_retry != self.retry_areas[player_id]
        if retry_changed:
            log.info("[%s] cleared retry areas %s on entering %s", player_id,
                     sorted(old_retry - self.retry_areas[player_id], key=str), area_id)
        # Gift areas (oaks_lab, intro, etc.) are not encounter areas — their captures
        # are handled directly via _handle_capture.  Don't create pending area state.
        if self._adapter_for(player_id).is_gift_area(area_id):
            if retry_changed:
                self._save()
            return
        # Always track area state — Lua only sends area_enter events once it has confirmed
        # Pokéballs are available (M.hasPokeballs() gate on the client side).
        status = self.area_states.get(area_id, AreaStatus.UNSEEN)
        if status == AreaStatus.UNSEEN:
            # PENDING_X means "waiting for X to act" — the other player already entered.
            self._set_area_state(area_id,
                AreaStatus.PENDING_B if player_id == "a" else AreaStatus.PENDING_A,
                player=player_id)
        elif status == AreaStatus.PENDING_B and player_id == "b" or status == AreaStatus.PENDING_A and player_id == "a":
            self._set_area_state(area_id, AreaStatus.PENDING_BOTH, player=player_id)
        self._save()

    def _is_gift_capture(self, area_id: str, is_egg: bool, player_id: str) -> bool:
        """Effective gift status for a capture event.

        True if the area is a known gift area OR the capture is a definitive egg
        from outside the daycare (NPC egg-givers in encounter areas).
        """
        adapter = self._adapter_for(player_id)
        if adapter.is_gift_area(area_id):
            return True
        return bool(is_egg and not adapter.is_daycare_area(area_id))

    def _handle_capture(self, player_id: str, msg: dict):
        area_id = msg.get("area_id", "")
        key     = msg.get("key", "")
        if not area_id or not key:
            return
        if self.entry_for(player_id, key) is not None:
            # A replay (reconnect, re-emission): this mon is already a link's half. A fresh catch never
            # carries a linked key, and treating it as one retired a live pair in the retire branches below.
            log.debug(f"[{player_id}] capture {key[:8]}: already linked -- duplicate event ignored")
            return
        is_egg  = bool(msg.get("is_egg", False))
        # `gift=true` is the Lua's authoritative signal for any new mon received
        # outside battle (gifts, starters, fossils, eggs) — independent of whether
        # the current area is a known gift area.
        gift_capture = bool(msg.get("gift", False)) or self._is_gift_capture(area_id, is_egg, player_id)

        # A catch in any non-gift area confirms Pokéballs are available.
        if not gift_capture:
            self.pokeballs_obtained[player_id] = True

        # ── Shiny Clause (always on) ──────────────────────────────────────────
        # Shiny captures are bonus mons kept outside the link system.
        # They bypass all area state checks, species/gender/type clauses, and quarantine.
        if self.adapter.is_shiny(key):
            if key in self.bonus_keys[player_id]:
                log.debug(f"[SHINY] player={player_id}  key={key[:8]}  action=duplicate_skip")
                return  # duplicate event, already handled
            self.bonus_keys[player_id].add(key)
            self.party_keys[player_id].add(key)
            partner = _partner(player_id)
            species = msg.get("species_id", 0)
            sname = self.adapter.species_name(species) if species else "Pokémon"
            log.info(f"[{player_id}] ★ SHINY CLAUSE: {sname} ({key[:8]}) in {area_id} — kept as bonus")
            log.debug(f"[SHINY] player={player_id}  key={key[:8]}  action=detected  species={sname}  area={area_id}")
            # Capturing player: shiny sound + prominent GUI prompt
            self.queued_commands[player_id].append({"cmd": "play_sound", "sound": 95})  # SE_SHINY
            # Momentous: native message box when patched+overworld, else the center prompt.
            self.queued_commands[player_id].append({
                "cmd": "msgbox", "fb": "prompt",
                "text": f"Shiny {sname} found!",
                "r": 255, "g": 215, "b": 0,
                "frames": 600,
            })
            # Partner: shiny sound + notification
            self.queued_commands[partner].append({"cmd": "play_sound", "sound": 95})  # SE_SHINY
            self.queued_commands[partner].append({
                "cmd": "msgbox", "fb": "prompt",
                "text": f"Partner found a shiny {sname}!",
                "r": 255, "g": 215, "b": 0,
                "frames": 480,
            })
            # Undo Lua's resolved_areas mark so the normal encounter for this area
            # is not consumed.  Skip if the area is already legitimately resolved
            # or if this player already has a pending capture here.
            status = self.area_states.get(area_id, AreaStatus.UNSEEN)
            already_resolved = status in (AreaStatus.LINKED, AreaStatus.DEAD_ZONE)
            has_pending = bool(self.pending_captures.get(area_id, {}).get(player_id))
            if not already_resolved and not has_pending:
                self.queued_commands[player_id].append({
                    "cmd": "unresolve_area",
                    "area_id": area_id,
                })
            # Queue a pending bonus slot for the partner
            self.pending_bonus[partner].append(key)
            log.debug(f"[SHINY] player={player_id}  key={key[:8]}  action=queued  pending_for={partner}  queue_depth={len(self.pending_bonus[partner])}")
            # Cache stats (including species_id) so bonus pair formation can check lock clauses
            stats = msg.get("stats")
            if not stats:
                lv = msg.get("level", 0)
                mhp = msg.get("maxHP", 0)
                if lv and mhp:
                    stats = {"level": lv, "maxHP": mhp}
            if stats is None:
                stats = {}
            stats["species_id"] = species  # always store, even if 0
            self.mon_stats[player_id][key] = stats
            self._save()
            return

        # ── Pending bonus encounter ─────────────────────────────────────────────────
        # If this player has a pending bonus (the partner caught a shiny earlier),
        # intercept this capture to form the bonus pair BEFORE normal area processing.
        if self.pending_bonus[player_id]:
            shiny_key = self.pending_bonus[player_id][0]  # peek; pop only on success
            partner = _partner(player_id)

            # Reconstruct MonInfo for the shiny side using cached stats
            shiny_stats = self.mon_stats[partner].get(shiny_key, {})
            shiny_species = shiny_stats.get("species_id", 0)
            shiny_mon_info = MonInfo(key=shiny_key, species=shiny_species,
                                     level=shiny_stats.get("level", 0),
                                     is_shiny=True)

            cap_species_local = msg.get("species_id", 0)
            this_mon = MonInfo(key=key, level=msg.get("level", 0),
                               nickname=msg.get("nickname", ""),
                               species=cap_species_local)

            # Build a_mon/b_mon for _check_link_violation
            # pending_bonus[player_id] was populated when the PARTNER caught a shiny.
            # So partner's side = shiny, player's side = this new catch.
            if player_id == "a":
                a_mon_for_check = this_mon      # A is catching; A is "a" side
                b_mon_for_check = shiny_mon_info  # partner (B) caught the shiny; B is "b" side
            else:
                a_mon_for_check = shiny_mon_info  # partner (A) caught the shiny; A is "a" side
                b_mon_for_check = this_mon        # B is catching; B is "b" side

            # Check lock clauses
            result = self._check_link_violation(a_mon_for_check, b_mon_for_check)
            if result:
                violation, _violator = result
                log.debug(f"[SHINY] player={player_id}  key={key[:8]}  action=violated  reason={violation!r}  shiny_key={shiny_key[:8]}")
                nickname = msg.get("nickname", "")
                self.party_keys[player_id].discard(key)
                self.queued_commands[player_id].append({"cmd": "force_faint", "key": key, "nickname": nickname})
                self._queue_memorialize(player_id, key)
                self.queued_commands[player_id].append({"cmd": "play_sound", "sound": 26})   # SE_FAILURE
                self.queued_commands[player_id].append({
                    "cmd": "gui_prompt",
                    "text": f"[x] {violation} - retry",
                    "r": 255, "g": 200, "b": 60,
                    "frames": 360,
                })
                # Unresolve area so the player can try their bonus encounter again
                cur_status = self.area_states.get(area_id, AreaStatus.UNSEEN)
                if cur_status not in (AreaStatus.LINKED, AreaStatus.DEAD_ZONE):
                    has_pending = bool(self.pending_captures.get(area_id, {}).get(player_id))
                    if not has_pending:
                        self.queued_commands[player_id].append({
                            "cmd": "unresolve_area",
                            "area_id": area_id,
                        })
                        # as the other clause rejections: the reroll's own no_catch must not dead-zone it
                        self.retry_areas[player_id].add(area_id)
                self._save()
                return

            # No violation — form the bonus pair
            self.pending_bonus[player_id].popleft()
            self.bonus_keys[partner].discard(shiny_key)

            bonus_area_id = f"_bonus_{shiny_key[:8]}"
            entry = LinkEntry(area_id=bonus_area_id,
                              a=a_mon_for_check, b=b_mon_for_check,
                              status=LinkStatus.ALIVE)
            self.links.append(entry)
            self._index_entry(entry)

            # Track party presence
            self.party_keys[player_id].add(key)
            # Cache stats for this new catch
            stats_local = msg.get("stats")
            if not stats_local:
                lv = msg.get("level", 0)
                mhp = msg.get("maxHP", 0)
                if lv and mhp:
                    stats_local = {"level": lv, "maxHP": mhp}
            if stats_local:
                stats_local["species_id"] = cap_species_local
                self.mon_stats[player_id][key] = stats_local

            # Party sync at formation: both mons should be in the same location.
            bonus_in_box = msg.get("in_box", False)
            shiny_in_party = shiny_key in self.party_keys[partner]
            if bonus_in_box and shiny_in_party:
                # Bonus catch went to box (party full) — sync shiny to box too
                self.queued_commands[partner].append({"cmd": "box_mon", "key": shiny_key})
                self.party_keys[partner].discard(shiny_key)
                log.info(f"[{player_id}] bonus pair formed — shiny {shiny_key[:8]} boxed to sync (bonus in box)")
            elif not bonus_in_box and not shiny_in_party:
                # Shiny is in box — sync bonus catch to box too
                self.queued_commands[player_id].append({"cmd": "box_mon", "key": key})
                self.party_keys[player_id].discard(key)
                log.info(f"[{player_id}] bonus pair formed — {key[:8]} boxed to sync (shiny in box)")

            # Unresolve area so the player's normal area encounter is still available
            cur_status = self.area_states.get(area_id, AreaStatus.UNSEEN)
            if cur_status not in (AreaStatus.LINKED, AreaStatus.DEAD_ZONE):
                has_pending = bool(self.pending_captures.get(area_id, {}).get(player_id))
                if not has_pending:
                    self.queued_commands[player_id].append({
                        "cmd": "unresolve_area",
                        "area_id": area_id,
                    })

            # Notify both players
            cap_sname = self.adapter.species_name(cap_species_local) if cap_species_local else "Pokémon"
            shiny_sname = self.adapter.species_name(shiny_species) if shiny_species else "Pokémon"
            box_text = f"{shiny_sname} and {cap_sname} linked!"
            log.info(f"[{player_id}] ★ BONUS PAIR: {shiny_key[:8]} ↔ {key[:8]} in {bonus_area_id}")
            log.debug(f"[SHINY] player={player_id}  key={key[:8]}  action=formed  shiny_key={shiny_key[:8]}  area={bonus_area_id}  shiny_species={shiny_sname}  cap_species={cap_sname}")
            self.queued_commands[player_id].append({"cmd": "play_sound", "sound": 25})   # SE_SUCCESS
            self.queued_commands[partner].append({"cmd": "play_sound", "sound": 25})
            self.queued_commands[player_id].append({
                "cmd": "msgbox", "text": box_text,
                "r": 255, "g": 215, "b": 0, "frames": 480,
            })
            self.queued_commands[partner].append({
                "cmd": "msgbox", "text": box_text,
                "r": 255, "g": 215, "b": 0, "frames": 480,
            })
            self._save()
            return

        # Gifts/eggs link under the gift namespace so they form a standalone gift
        # pair and never lock a real area's wild-encounter slot — and so they link
        # even if that real area is already LINKED/DEAD_ZONE.
        if gift_capture:
            area_id = self._adapter_for(player_id).gift_link_area(area_id)

        status = self.area_states.get(area_id, AreaStatus.UNSEEN)

        if status == AreaStatus.DEAD_ZONE and not gift_capture:
            # Area is dead — retire this mon immediately without altering the display entry,
            # so the original "no catch" record is preserved in the Linked Pairs table.
            log.warning(
                f"[{player_id}] capture in dead-zone area={area_id} key={key} — retiring immediately"
            )
            nickname = msg.get("nickname", "")
            label = self._label_from_msg(msg, key)
            self.party_keys[player_id].discard(key)
            self.queued_commands[player_id].append({"cmd": "force_faint", "key": key, "nickname": nickname})
            self._queue_memorialize(player_id, key)
            self.queued_commands[player_id].append({"cmd": "play_sound", "sound": 26})  # SE_FAILURE
            self.queued_commands[player_id].append({
                "cmd": "hud_show",
                "text": f"[x] DZ: {label}",
                "r": 255, "g": 80, "b": 80, "frames": 360,
            })
            self._save()
            return

        if status == AreaStatus.LINKED and not gift_capture:
            # Extra capture in an already-linked area — illegal, retire immediately.
            # (Gifts/eggs bypass this — they are not wild encounters, so several may
            # arrive in the same area; each pairs in turn under the gift namespace.)
            log.warning(
                f"[{player_id}] extra capture in already-linked area={area_id} key={key} — retiring immediately"
            )
            nickname = msg.get("nickname", "")
            label = self._label_from_msg(msg, key)
            self.party_keys[player_id].discard(key)
            self.queued_commands[player_id].append({"cmd": "force_faint", "key": key, "nickname": nickname})
            self._queue_memorialize(player_id, key)
            self.queued_commands[player_id].append({"cmd": "play_sound", "sound": 26})  # SE_FAILURE
            self.queued_commands[player_id].append({
                "cmd": "hud_show",
                "text": f"[x] Linked: {label}",
                "r": 255, "g": 80, "b": 80, "frames": 360,
            })
            self._save()
            return

        # Guard: don't overwrite an existing pending capture for this player.
        # A second capture in the same area is illegal — retire it immediately.
        existing = self.pending_captures.get(area_id, {}).get(player_id)
        if existing:
            if existing.key == key:
                return  # duplicate event for the same mon, ignore
            log.warning(
                f"[{player_id}] second capture in area={area_id} "
                f"(already have {existing.key}), retiring {key}"
            )
            nickname = msg.get("nickname", "")
            label = self._label_from_msg(msg, key)
            self.party_keys[player_id].discard(key)
            self.queued_commands[player_id].append({"cmd": "force_faint", "key": key, "nickname": nickname})
            self._queue_memorialize(player_id, key)
            self.queued_commands[player_id].append({"cmd": "play_sound", "sound": 26})  # SE_FAILURE
            self.queued_commands[player_id].append({
                "cmd": "hud_show",
                "text": f"[x] 2nd: {label}",
                "r": 255, "g": 80, "b": 80, "frames": 360,
            })
            self._save()
            return

        # Species clause: reject capture immediately if the player already has this
        # species family in an alive link OR a pending capture in another area.
        # Don't wait for the partner to catch — reject now, unresolve the area,
        # and let the player try again.
        cap_species = msg.get("species_id", 0)
        encounter_lock = None if gift_capture else self.encounter_locks.get(area_id, {}).get(player_id)
        if (self.retry_same_species and encounter_lock
                and cap_species != encounter_lock.species):
            required = self.adapter.species_name(encounter_lock.species) or f"species {encounter_lock.species}"
            caught = self.adapter.species_name(cap_species) if cap_species else "unknown species"
            nickname = msg.get("nickname", "")
            log.warning("[%s] capture %s violates %s encounter lock %s; retiring %s",
                        player_id, caught, area_id, required, key)
            self.party_keys[player_id].discard(key)
            self.queued_commands[player_id].append(
                {"cmd": "force_faint", "key": key, "nickname": nickname})
            self._queue_memorialize(player_id, key)
            self.queued_commands[player_id].append({"cmd": "play_sound", "sound": 26})
            self.queued_commands[player_id].append({
                "cmd": "gui_prompt",
                "text": f"[x] {area_id}: catch {required}",
                "r": 255, "g": 200, "b": 60, "frames": 360,
            })
            self.queued_commands[player_id].append(
                {"cmd": "unresolve_area", "area_id": area_id})
            self._save()
            return

        if self.species_lock and cap_species and not self.adapter.is_fixed_species_gift(area_id):
            cap_base = self.adapter.evo_family(cap_species)
            dup_name: str | None = None

            # Check alive links
            for entry in self.links:
                if entry.status != LinkStatus.ALIVE:
                    continue
                player_mon = entry.a if player_id == "a" else entry.b
                if player_mon and player_mon.species and self.adapter.evo_family(player_mon.species) == cap_base:
                    dup_name = self.adapter.species_name(player_mon.species)
                    break
                partner_mon = entry.b if player_id == "a" else entry.a
                if partner_mon and partner_mon.species and self.adapter.evo_family(partner_mon.species) == cap_base:
                    dup_name = self.adapter.species_name(partner_mon.species)
                    break

            # Check pending captures in OTHER areas
            if not dup_name:
                for pend_area, pend_map in self.pending_captures.items():
                    if pend_area == area_id:
                        continue
                    pend_mon = pend_map.get(player_id)
                    if pend_mon and pend_mon.species and self.adapter.evo_family(pend_mon.species) == cap_base:
                        dup_name = self.adapter.species_name(pend_mon.species)
                        break
                    pend_mon = pend_map.get(_partner(player_id))
                    if pend_mon and pend_mon.species and self.adapter.evo_family(pend_mon.species) == cap_base:
                        dup_name = self.adapter.species_name(pend_mon.species)
                        break

            if dup_name:
                cap_name = self.adapter.species_name(cap_species)
                log.info(
                    f"[{player_id}] species clause: captured {cap_name} "
                    f"same family as existing {dup_name} — rejecting in {area_id}"
                )
                nickname = msg.get("nickname", "")
                self.queued_commands[player_id].append({"cmd": "force_faint", "key": key, "nickname": nickname})
                self._queue_memorialize(player_id, key)
                self.queued_commands[player_id].append({"cmd": "play_sound", "sound": 26})   # SE_FAILURE
                self.queued_commands[player_id].append({
                    "cmd": "gui_prompt",
                    "text": f"[x] Dup {dup_name}",
                    "r": 255, "g": 200, "b": 60,
                    "frames": 360,
                })
                self.queued_commands[player_id].append({
                    "cmd": "unresolve_area",
                    "area_id": area_id,
                })
                locks = self.encounter_locks.get(area_id, {})
                locks.pop(player_id, None)
                if not locks:
                    self.encounter_locks.pop(area_id, None)
                self.retry_areas[player_id].add(area_id)
                self._save()
                return

        if self.retry_same_species and not gift_capture and cap_species and encounter_lock is None:
            self.encounter_locks.setdefault(area_id, {})[player_id] = EncounterLock(
                species=cap_species, level=msg.get("level", 0))

        if area_id not in self.pending_captures:
            self.pending_captures[area_id] = {}

        mon = MonInfo(key=key, level=msg.get("level", 0),
                      nickname=msg.get("nickname", ""),
                      species=msg.get("species_id", 0))
        self.pending_captures[area_id][player_id] = mon
        log.debug(f"[PENDING] {area_id}  player={player_id}  action=add  key={key[:8]}"
                  f"  species={mon.species}  lv={mon.level}")
        self.retry_areas[player_id].discard(area_id)  # valid capture clears retry
        in_box = msg.get("in_box", False)
        # Quarantine: unlinked mons must go to the box until linked.
        # Exception: never quarantine if it would empty the party (e.g. starter).
        # party_size reflects count BEFORE this capture; if >= 1, the player has
        # at least one other mon and it's safe to deposit.  Default to 0 (safe)
        # when party_size hasn't been reported yet — prevents quarantining the
        # starter before the first hello sets the actual count.
        party_count = self.party_size.get(player_id, 0)
        if not in_box and party_count >= 1 and not gift_capture:
            self.queued_commands[player_id].append({"cmd": "box_mon", "key": key})
            log.info(f"[{player_id}] quarantine: {key[:8]} → box (pending link)")
        elif not in_box and gift_capture:
            log.info(f"[{player_id}] skip quarantine: {key[:8]} (gift {area_id}{', egg' if is_egg else ''})")
        elif not in_box:
            log.info(f"[{player_id}] skip quarantine: {key[:8]} (only mon in party)")
        # Cache stats from capture event so party_mon can restore them later.
        stats = msg.get("stats")
        if not stats:
            # Capture events send hp/maxHP/level as top-level fields, not nested.
            lv = msg.get("level", 0)
            mhp = msg.get("maxHP", 0)
            if lv and mhp:
                stats = {"level": lv, "maxHP": mhp}
        if stats:
            self.mon_stats[player_id][key] = stats

        partner     = _partner(player_id)
        partner_cap = self.pending_captures[area_id].get(partner)

        if partner_cap:
            # Both players captured in this area — check lock rules before linking.
            # Gift areas always produce the same species, so clauses don't apply.
            a_mon = self.pending_captures[area_id].get("a")
            b_mon = self.pending_captures[area_id].get("b")

            # Fixed-species gift areas bypass clause checks — both players always receive
            # the same predetermined species with no meaningful choice difference.
            # Player-choice gifts (starters at oaks_lab, fossils at cinnabar_lab) still enforce.
            is_gift = self.adapter.is_fixed_species_gift(area_id)
            result = None if is_gift else self._check_link_violation(a_mon, b_mon)
            if result:
                violation, violator = result
                # If violator is "" (cross-player), reject the player who just captured.
                # If violator is a specific player, reject that player's capture.
                reject_pid = violator if violator else player_id
                reject_key = (a_mon.key if reject_pid == "a" else b_mon.key)
                reject_nick = (a_mon.nickname if reject_pid == "a" else b_mon.nickname) or ""
                log.warning(f"[{reject_pid}] {violation} in area={area_id} — rejecting capture")
                self.party_keys[reject_pid].discard(reject_key)
                self.queued_commands[reject_pid].append({"cmd": "force_faint", "key": reject_key, "nickname": reject_nick})
                self._queue_memorialize(reject_pid, reject_key)
                self.queued_commands[reject_pid].append({"cmd": "play_sound", "sound": 26})   # SE_FAILURE
                self.queued_commands[_partner(reject_pid)].append({"cmd": "play_sound", "sound": 22})  # SE_BOO
                del self.pending_captures[area_id][reject_pid]
                log.debug(f"[PENDING] {area_id}  player={reject_pid}  action=reject  key={reject_key[:8]}  reason={violation!r}")
                # Area stays pending — waiting for the violating player to retry
                self._set_area_state(area_id,
                    AreaStatus.PENDING_A if reject_pid == "a" else AreaStatus.PENDING_B,
                    player=reject_pid, reason="clause_violation_retry")
                self.queued_commands[reject_pid].append({
                    "cmd": "gui_prompt",
                    "text": "[x] " + violation,
                    "r": 255, "g": 200, "b": 60,
                    "frames": 360,
                })
                # Tell Lua to un-resolve this area so the rejected player can retry
                # and no_catch won't fire when they leave.
                self.queued_commands[reject_pid].append({
                    "cmd": "unresolve_area",
                    "area_id": area_id,
                })
                locks = self.encounter_locks.get(area_id, {})
                locks.pop(reject_pid, None)
                if not locks:
                    self.encounter_locks.pop(area_id, None)
                self.retry_areas[reject_pid].add(area_id)
                self._save()
                return

            entry = LinkEntry(area_id=area_id, a=a_mon, b=b_mon, status=LinkStatus.ALIVE)
            self.links.append(entry)
            self._index_entry(entry)
            self._set_area_state(area_id, AreaStatus.LINKED, player=player_id, reason="both_captured")
            del self.pending_captures[area_id]
            self.encounter_locks.pop(area_id, None)
            for pid in ("a", "b"):
                self._queue_resolved_areas(pid)
            log.debug(f"[PENDING] {area_id}  removed  (link formed)")
            log.info(f"Linked {a_mon.key} ↔ {b_mon.key} in {area_id}")
            # Notify both players with success sound + link info HUD
            a_label = self._mon_label(a_mon)
            b_label = self._mon_label(b_mon)
            # "msgbox" = a momentous event: the client shows it in the native in-game
            # message box when the companion patch is present + overworld, else falls back
            # to the HUD overlay (so unpatched clients are unaffected). Text is kept clean
            # ASCII for the FR-charmap message box; r/g/b/frames style the HUD fallback.
            box_text = f"{a_label} and {b_label} linked!"
            linked_box = {"cmd": "msgbox", "text": box_text, "r": 100, "g": 255, "b": 160, "frames": 300}
            # O-29 tag: the run's first two-sided link (dead-zone entries have a missing or
            # key="" side). Derived from the persisted links, so it fires once per run.
            first_link = sum(1 for e in self.links if e.a and e.a.key and e.b and e.b.key) == 1
            if first_link:
                linked_box["phone"] = "first_link"
            self.queued_commands[player_id].append({"cmd": "play_sound", "sound": 25})   # SE_SUCCESS
            self.queued_commands[partner].append({"cmd": "play_sound", "sound": 25})
            for pid in (player_id, partner):
                box = dict(linked_box)   # PHONE-NAMES: a per-recipient phone_data, never shared
                own, other = (a_mon, b_mon) if pid == "a" else (b_mon, a_mon)
                data = self._phone_data(_partner(pid), other, own) if first_link else None
                if data:
                    box["phone_data"] = data
                self.queued_commands[pid].append(box)
            # Un-quarantine: both mons were boxed while pending — retrieve to party
            # ONLY if both players have room. Both must stay in sync.
            a_has_room = self.party_size.get("a", 6) < 6
            b_has_room = self.party_size.get("b", 6) < 6
            if a_has_room and b_has_room:
                for pid, mon_obj in [("a", a_mon), ("b", b_mon)]:
                    cmd: dict = {"cmd": "party_mon", "key": mon_obj.key}
                    if mon_obj.nickname:
                        cmd["nickname"] = mon_obj.nickname
                    cached = self.mon_stats[pid].get(mon_obj.key)
                    if cached:
                        cmd["stats"] = cached
                    # Cancel any pending box_mon for this key (quarantine command may still be queued)
                    self.queued_commands[pid] = [
                        c for c in self.queued_commands[pid]
                        if not (c.get("key") == mon_obj.key and c.get("cmd") == "box_mon")
                    ]
                    self.queued_commands[pid].append(cmd)
                    log.info(f"Post-link: {pid}:{mon_obj.key[:8]} → party_mon (un-quarantine)")
            else:
                # At least one party is full — keep both in box, cancel stale quarantine commands.
                for pid in ("a", "b"):
                    mon_obj = a_mon if pid == "a" else b_mon
                    self.queued_commands[pid] = [
                        c for c in self.queued_commands[pid]
                        if not (c.get("key") == mon_obj.key and c.get("cmd") == "box_mon")
                    ]
                full_pid = "a" if not a_has_room else "b"
                log.info(f"Post-link: both stay in box — {full_pid} party full ({self.party_size.get(full_pid, 0)}/6)")
        else:
            # PENDING_X = "waiting for X to act"
            self._set_area_state(area_id,
                AreaStatus.PENDING_B if player_id == "a" else AreaStatus.PENDING_A,
                player=player_id, reason="first_capture")
            # Notify partner that a new link opportunity is available.
            disp = self.adapter.area_display_name(area_id) or humanize_area_id(area_id)
            nick = mon.nickname or self.adapter.species_name(mon.species) or "a mon"
            self.queued_commands[partner].append({
                "cmd": "hud_show",
                "text": f"Partner caught {nick} in {disp}",
                "r": 100, "g": 180, "b": 255,
                "frames": 300,
            })

        self._save()

    def _handle_faint(self, player_id: str, msg: dict):
        key = msg.get("key", "")
        if not key:
            return
        if self.ignore_wild_faints and msg.get("_is_wild_battle") is True:
            log.info(f"[FAINT GATE] player={player_id}  key={key[:8]}  suppressed=True  reason=wild_battle")
            return
        was_in_party = key in self.party_keys[player_id]
        self.party_keys[player_id].discard(key)
        if was_in_party:
            log.debug(f"[PARTY] player={player_id}  party_keys remove {key[:8]}  (faint)")
        # Soul Link deaths only count once the nuzlocke run is active (pokéballs obtained).
        # Faints before that point (e.g. starter knocked out before first Pokéball) are ignored.
        if not self.pokeballs_obtained[player_id]:
            log.debug(f"[FAINT GATE] player={player_id}  key={key[:8]}  suppressed=True  reason=nuzlocke_not_active")
            return
        entry = self.entry_for(player_id, key)
        if not entry or entry.status != LinkStatus.ALIVE:
            log.debug(f"[{player_id}] faint {key[:8]}: no alive linked entry — ignored "
                      f"(status={entry.status.value if entry else 'not_found'})")
            return
        # Build killer info from server-enriched fields (injected by server.py before routing here).
        killer_species = msg.get("_killer_species", 0)
        killer: dict | None = None
        if killer_species:
            killer = {
                "species":      killer_species,
                "level":        msg.get("_killer_level", 0),
                "is_trainer":   bool(msg.get("_is_trainer", False)),
                "trainer_name": msg.get("_trainer_name", ""),
                "trainer_class": msg.get("_trainer_class", ""),
            }
        self._propagate_faint(player_id, entry, killer=killer,
                              level=msg.get("_level", 0), cause="whiteout" if msg.get("_cause") == "whiteout" else "battle")

    def _handle_release(self, player_id: str, msg: dict):
        """Owner ruling O-35: a PC release of a linked mon loses it, so its partner dies and is
        memorialized like a faint. Only the releaser's own half of an ALIVE link counts; the
        released mon no longer exists, so it gets no memorialize of its own."""
        key = msg.get("key", "")
        if not key:
            return
        self.party_keys[player_id].discard(key)
        entry = self.entry_for(player_id, key)
        half = entry and (entry.a if player_id == "a" else entry.b)
        if (not self.pokeballs_obtained[player_id] or not entry or entry.status != LinkStatus.ALIVE
                or not half or half.key != key):
            log.info(f"[{player_id}] release {key[:8]}: no alive linked half of this player — ignored")
            return
        log.info(f"[{player_id}] released linked {key[:8]} — the partner dies (O-35)")
        self._propagate_faint(player_id, entry, cause="release")
        self.pending_memorials[player_id].discard(key)
        self.queued_commands[player_id] = [c for c in self.queued_commands[player_id]
                                           if not (c.get("cmd") == "memorialize" and c.get("key") == key)]
        self._save()

    def _dupes_reroll(self, player_id: str, area_id: str, text: str):
        """Send GUI prompt + unresolve_area so the player gets another encounter."""
        self.queued_commands[player_id].append({
            "cmd": "gui_prompt",
            "text": text,
            "r": 100, "g": 200, "b": 255,
            "frames": 300,
        })
        self.queued_commands[player_id].append({
            "cmd": "unresolve_area",
            "area_id": area_id,
        })

    def check_dupe_on_encounter(self, player_id: str, area_id: str, enc_species: int,
                                partner_battle_species: int = 0) -> bool:
        """Check for a dupes-clause reroll at wild battle start and notify the player immediately.

        Called as soon as the enemy species is known (first tick with in_battle=True) so the
        player sees the gui_prompt during the battle rather than after fleeing.

        Returns True if a dupe was detected and the gui_prompt was queued.
        """
        if not self.species_lock or not enc_species or not area_id:
            return False
        if not self.pokeballs_obtained.get(player_id):
            return False
        current = self.area_states.get(area_id, AreaStatus.UNSEEN)
        if current in (AreaStatus.LINKED, AreaStatus.DEAD_ZONE):
            return False
        # Skip if player already has a capture pending for this area (they caught already).
        if self.pending_captures.get(area_id, {}).get(player_id):
            return False

        partner = _partner(player_id)
        enc_base = self.adapter.evo_family(enc_species)
        enc_name = self.adapter.species_name(enc_species)

        # Check 1: same family as something this player already has in an alive link.
        for entry in self.links:
            if entry.status != LinkStatus.ALIVE:
                continue
            player_mon = entry.a if player_id == "a" else entry.b
            if player_mon and player_mon.species and self.adapter.evo_family(player_mon.species) == enc_base:
                existing_name = self.adapter.species_name(player_mon.species)
                log.info(
                    f"[{player_id}] dupes clause (battle start): {enc_name} "
                    f"same family as existing {existing_name} in {entry.area_id}"
                )
                self.dupe_notified_areas[player_id].add(area_id)
                self._dupes_reroll(player_id, area_id, f"Dupes clause: {enc_name} -- reroll!")
                return True
            partner_mon = entry.b if player_id == "a" else entry.a
            if partner_mon and partner_mon.species and self.adapter.evo_family(partner_mon.species) == enc_base:
                existing_name = self.adapter.species_name(partner_mon.species)
                log.info(
                    f"[{player_id}] dupes clause (battle start): {enc_name} "
                    f"same family as partner's {existing_name} in {entry.area_id}"
                )
                self.dupe_notified_areas[player_id].add(area_id)
                self._dupes_reroll(player_id, area_id, f"Dupes clause: {enc_name} -- reroll!")
                return True

        # Check 2: partner already captured on this area with same evo family.
        partner_cap = self.pending_captures.get(area_id, {}).get(partner)
        if partner_cap and partner_cap.species:
            partner_base = self.adapter.evo_family(partner_cap.species)
            if enc_base == partner_base:
                partner_name = self.adapter.species_name(partner_cap.species)
                log.info(
                    f"[{player_id}] dupes clause (battle start): {enc_name} "
                    f"same family as partner's {partner_name} on {area_id}"
                )
                self.dupe_notified_areas[player_id].add(area_id)
                self._dupes_reroll(player_id, area_id, f"Dupes clause: {enc_name} -- reroll!")
                return True
        for pend_area, pend_map in self.pending_captures.items():
            if pend_area == area_id:
                continue
            pend_mon = pend_map.get(partner)
            if pend_mon and pend_mon.species and self.adapter.evo_family(pend_mon.species) == enc_base:
                partner_name = self.adapter.species_name(pend_mon.species)
                log.info(
                    f"[{player_id}] dupes clause (battle start): {enc_name} "
                    f"same family as partner's pending {partner_name} on {pend_area}"
                )
                self.dupe_notified_areas[player_id].add(area_id)
                self._dupes_reroll(player_id, area_id, f"Dupes clause: {enc_name} -- reroll!")
                return True

        # Check 3: partner is currently in a wild battle on the same area with the same evo family.
        # Covers the concurrent-battle case where neither player has captured yet -- and that is a
        # precondition, not just a description. Once the partner holds a pending capture HERE their
        # slot for this area is filled (a second capture in one area is retired on arrival), so what
        # they happen to be battling now can never link against this encounter. Checking it anyway
        # costs this player a legal encounter for a collision that cannot happen. The mirror of the
        # `pending_captures[area_id][player_id]` early return above.
        if partner_battle_species and not partner_cap:
            partner_base = self.adapter.evo_family(partner_battle_species)
            if enc_base == partner_base:
                partner_name = self.adapter.species_name(partner_battle_species)
                log.info(
                    f"[{player_id}] dupes clause (battle start): {enc_name} "
                    f"same family as partner's concurrent battle {partner_name} on {area_id}"
                )
                self.dupe_notified_areas[player_id].add(area_id)
                self._dupes_reroll(player_id, area_id, f"Dupes clause: {enc_name} -- reroll!")
                return True

        return False

    def _handle_no_catch(self, player_id: str, msg: dict):
        area_id = msg.get("area_id", "")
        if not area_id:
            return

        # Gift areas can never become dead zones — wild encounters there are coincidental.
        if self._adapter_for(player_id).is_gift_area(area_id):
            log.debug(f"[{player_id}] no_catch ignored — {area_id} is a gift area")
            return

        current = self.area_states.get(area_id, AreaStatus.UNSEEN)
        if current in (AreaStatus.LINKED, AreaStatus.DEAD_ZONE):
            return  # Already resolved

        # If this player already captured here, the no_catch is a stale/spurious event.
        if self.pending_captures.get(area_id, {}).get(player_id):
            log.debug(f"[{player_id}] no_catch ignored — player already captured in {area_id}")
            return

        # Clause retry: a previous capture was rejected (species/gender/type clause).
        # Suppress ALL no_catch until the player successfully captures a valid mon.
        # Also suppress if the PARTNER has a retry pending — their rejected capture
        # means the area is still open; B dead-zoning it would destroy A's chance.
        partner = _partner(player_id)
        if area_id in self.retry_areas[player_id] or area_id in self.retry_areas[partner]:
            log.info(f"[{player_id}] no_catch suppressed — retry pending for {area_id}")
            self.queued_commands[player_id].append({
                "cmd": "unresolve_area",
                "area_id": area_id,
            })
            return

        # Species clause reroll: if species lock is active and the encountered
        # species belongs to a family the player already has in an alive link,
        # the encounter doesn't count — area stays open for a different catch.
        enc_species = msg.get("species_id", 0)
        enc_level   = msg.get("level", 0)

        # Dupes already notified at battle start — keep area open without repeating the prompt.
        if area_id in self.dupe_notified_areas[player_id]:
            self.dupe_notified_areas[player_id].discard(area_id)
            self.queued_commands[player_id].append({"cmd": "unresolve_area", "area_id": area_id})
            log.info(f"[{player_id}] no_catch — dupes reroll for {area_id} (already notified at battle start)")
            return

        if self.species_lock and enc_species:
            enc_base = self.adapter.evo_family(enc_species)
            enc_name = self.adapter.species_name(enc_species)

            # Check 1: same family as something this player already has alive
            for entry in self.links:
                if entry.status != LinkStatus.ALIVE:
                    continue
                player_mon = entry.a if player_id == "a" else entry.b
                if player_mon and player_mon.species and self.adapter.evo_family(player_mon.species) == enc_base:
                    existing_name = self.adapter.species_name(player_mon.species)
                    log.info(
                        f"[{player_id}] species clause reroll: {enc_name} "
                        f"same family as existing {existing_name} in {entry.area_id} — no_catch suppressed"
                    )
                    self._dupes_reroll(player_id, area_id, f"Dupes clause: {enc_name} -- reroll!")
                    return
                partner_mon = entry.b if player_id == "a" else entry.a
                if partner_mon and partner_mon.species and self.adapter.evo_family(partner_mon.species) == enc_base:
                    existing_name = self.adapter.species_name(partner_mon.species)
                    log.info(
                        f"[{player_id}] species clause reroll: {enc_name} "
                        f"same family as partner's {existing_name} in {entry.area_id} — no_catch suppressed"
                    )
                    self._dupes_reroll(player_id, area_id, f"Dupes clause: {enc_name} -- reroll!")
                    return

            # Check 2: partner already captured on this area and species would
            # violate species lock (same family) — catching it would be rejected
            # anyway, so give the player a free reroll.
            partner_cap = self.pending_captures.get(area_id, {}).get(partner)
            if partner_cap and partner_cap.species:
                partner_base = self.adapter.evo_family(partner_cap.species)
                if enc_base == partner_base:
                    partner_name = self.adapter.species_name(partner_cap.species)
                    log.info(
                        f"[{player_id}] dupes clause reroll: {enc_name} "
                        f"same family as partner's {partner_name} on {area_id} — no_catch suppressed"
                    )
                    self._dupes_reroll(player_id, area_id, f"Dupes clause: {enc_name} -- reroll!")
                    return
            for pend_area, pend_map in self.pending_captures.items():
                if pend_area == area_id:
                    continue
                pend_mon = pend_map.get(partner)
                if pend_mon and pend_mon.species and self.adapter.evo_family(pend_mon.species) == enc_base:
                    partner_name = self.adapter.species_name(pend_mon.species)
                    log.info(
                        f"[{player_id}] dupes clause reroll: {enc_name} "
                        f"same family as partner's pending {partner_name} on {pend_area} — no_catch suppressed"
                    )
                    self._dupes_reroll(player_id, area_id, f"Dupes clause: {enc_name} -- reroll!")
                    return

        if self.retry_same_species:
            area_locks = self.encounter_locks.setdefault(area_id, {})
            lock = area_locks.get(player_id)
            if lock is None and enc_species:
                lock = EncounterLock(species=enc_species, level=enc_level)
                area_locks[player_id] = lock
                log.info("[%s] encounter lock: %s species=%s level=%s",
                         player_id, area_id, enc_species, enc_level)
            elif not area_locks:
                self.encounter_locks.pop(area_id, None)

            self.queued_commands[player_id].append({"cmd": "unresolve_area", "area_id": area_id})
            if lock:
                required = self.adapter.species_name(lock.species) or f"species {lock.species}"
                encountered = self.adapter.species_name(enc_species) if enc_species else ""
                text = (f"First encounter: {required} -- retry it!"
                        if not encountered or enc_species == lock.species
                        else f"Find {required}, not {encountered}!")
                self.queued_commands[player_id].append({
                    "cmd": "gui_prompt",
                    "text": text,
                    "r": 255, "g": 220, "b": 60, "frames": 360,
                })
            self._save()
            return

        self._set_area_state(area_id, AreaStatus.DEAD_ZONE,
                            player=player_id, reason="no_catch")
        self.encounter_locks.pop(area_id, None)
        partner_cap = self.pending_captures.get(area_id, {}).get(partner)
        log.info(f"[DEAD ZONE] {area_id}  reason=no_catch  triggered_by={player_id}  partner_had_capture={partner_cap is not None}")
        # Notify both players with a failure sound + HUD banner naming the dead area.
        area_disp = self.adapter.area_display_name(area_id) or area_id
        dz_text = f"{area_disp} is a dead zone!"
        self.queued_commands[player_id].append({"cmd": "play_sound", "sound": 26})   # SE_FAILURE
        partner     = _partner(player_id)
        self.queued_commands[player_id].append({
            "cmd": "msgbox", "text": dz_text,
            "r": 255, "g": 80, "b": 80, "frames": 480, "phone": "dead_zone",   # O-29 tag
            **self._phone_extra(partner, None, None),
        })
        self.queued_commands[partner].append({"cmd": "play_sound", "sound": 26})
        self.queued_commands[partner].append({
            "cmd": "msgbox", "text": dz_text,
            "r": 255, "g": 80, "b": 80, "frames": 480, "phone": "dead_zone",
            **self._phone_extra(player_id, None, None),
        })
        partner_cap = self.pending_captures.get(area_id, {}).get(partner)

        # Always create a LinkEntry so the dead zone and both sides' encounters are logged.
        # Current player had no catch (None); partner may or may not have caught something.
        # Build a sentinel MonInfo (key="") for the no-catch side if encounter data was sent.
        enc_level   = msg.get("level", 0)
        no_catch_mon = MonInfo(key="", species=enc_species, level=enc_level) if enc_species else None
        a_mon = (None if player_id == "a" else partner_cap)
        b_mon = (None if player_id == "b" else partner_cap)
        a_enc = (no_catch_mon if player_id == "a" else None)
        b_enc = (no_catch_mon if player_id == "b" else None)
        entry = LinkEntry(area_id=area_id, a=a_mon, b=b_mon, status=LinkStatus.DEAD,
                          encounter_a=a_enc, encounter_b=b_enc,
                          killed_at=datetime.now(UTC).isoformat(),
                          cause="dead_zone",
                          initiating_player=player_id)
        self.links.append(entry)
        self._index_entry(entry)

        if partner_cap:
            self.queued_commands[partner].append({"cmd": "force_faint", "key": partner_cap.key, "nickname": partner_cap.nickname or ""})
            self.party_keys[partner].discard(partner_cap.key)
            self._queue_memorialize(partner, partner_cap.key)
            log.info(f"[{partner}] dead-zone retire: {partner_cap.key} in {area_id}")
        else:
            log.info(f"dead zone {area_id}: {player_id} no catch, partner had no pending capture")

        if area_id in self.pending_captures:
            del self.pending_captures[area_id]
            log.debug(f"[PENDING] {area_id}  removed  (dead zone)")

        self._check_game_over()
        self._save()

    def _handle_whiteout(self, player_id: str, msg: dict):
        """
        Force-faint all living linked partners of the whited-out player.
        Only processes mons known to be in the whited-out player's party
        (tracked via party_keys) to avoid killing boxed mons' partners.

        Then either: (a) queue an auto-rebuild from alive boxed linked pairs,
        or (b) fire game_over if no alive linked pairs remain anywhere. The
        rebuild's party_mon commands are queued BEFORE the force-faint loop's
        memorialize commands so the Lua deferred queue drains in the right
        order — party_mon lands first (q_count goes 1→2), then memorialize
        for the dying mons can proceed (PMC-heal-softlock guard releases as
        soon as a second slot is occupied).
        """
        partner = _partner(player_id)
        now = datetime.now(UTC).isoformat()
        # Same gate as _handle_faint: before the first Poke Ball nothing is a Soul Link death. A lost
        # rival battle would otherwise retire a gift-linked starter pair through this path.
        if not self.pokeballs_obtained[player_id]:
            log.debug(f"[WHITEOUT GATE] player={player_id}  suppressed=True  reason=nuzlocke_not_active")
            return
        if self.ignore_wild_faints and msg.get("_is_wild_battle") is True:
            log.info(f"[WHITEOUT GATE] player={player_id}  suppressed=True  reason=wild_battle")
            return

        # Plan rebuild against the pre-whiteout state. Dying-pair halves are
        # still in party_keys at this point, so the co-location check inside
        # _alive_pc_mons filters them out — only fully-boxed alive pairs
        # survive into the picks.
        player_picks, partner_picks = self._plan_rebuild(player_id)
        if player_picks:
            self.rebuild_pending[player_id] = {
                "started_at":         now,
                "queued_keys":        [m.key for m in player_picks],
                "queued_partner_keys": [m.key for m in partner_picks],
                "restored_keys":      set(),
            }
            self._queue_rebuild_commands(player_id, player_picks, partner_picks)
            log.info(f"[{player_id}] whiteout rebuild armed — "
                     f"restoring {len(player_picks)} mon(s); partner mirrors "
                     f"{len(partner_picks)}")

        retired = []
        for entry in self.links:
            if entry.status != LinkStatus.ALIVE:
                continue
            player_mon  = entry.a if player_id == "a" else entry.b
            partner_mon = entry.b if player_id == "a" else entry.a
            if not player_mon or not partner_mon:
                continue
            # Only act on mons we believe are in the whited-out player's party
            if player_mon.key not in self.party_keys[player_id]:
                continue
            # Invariant review MINOR-4: a link the pending trade names dies by the SWAPPED halves
            if self._hold_for_trade(player_id, {"event": "faint", "key": player_mon.key, "_cause": "whiteout"}):
                continue
            self.queued_commands[partner].append({"cmd": "force_faint", "key": partner_mon.key, "nickname": partner_mon.nickname or ""})
            self.party_keys[partner].discard(partner_mon.key)
            entry.status = LinkStatus.DEAD
            entry.killed_at = now
            entry.cause = "whiteout"
            entry.initiating_player = player_id
            self._queue_memorialize(player_id, player_mon.key)
            self._queue_memorialize(partner, partner_mon.key)
            retired.append(partner_mon.key)

        self.party_keys[player_id].clear()

        if retired:
            log.info(f"[{player_id}] whiteout — force-fainting {len(retired)} partner mon(s)")

        # No alive boxed pairs left after a real whiteout → run is over. With
        # no party and no rebuild candidates, the whited-out player cannot
        # battle or progress. Firing game_over now also drops the blocked
        # last-party-mon memorialize cleanly in the Lua client (it checks
        # game_over_flag and removes the command instead of waiting).
        if retired and not player_picks:
            self.queued_commands[player_id].append({
                "cmd":  "hud_show",
                "text": "[x] PC empty",
                "r": 255, "g": 80, "b": 80, "frames": 360,
            })
            if not self.run_over:
                self.run_over = True
                log.info(f"[{player_id}] whiteout — no rebuild possible, run over")
                for pid in ("a", "b"):
                    self.queued_commands[pid].append({"cmd": "game_over"})

        if retired or player_picks:
            # Belt-and-suspenders: also run the standard check (covers the
            # no-whiteout-events but already-dead-pairs case, and is a no-op
            # if run_over was just set above).
            self._check_game_over()
            self._save()

    def _handle_party_to_box(self, player_id: str, msg: dict):
        """
        Player deposited a linked mon at the PC.
        Cache its stats and send box_mon to partner so their linked mon is auto-deposited.
        """
        key = msg.get("key", "")
        if not key:
            return
        # Cache stats so we can echo them back in the partner's party_mon command later.
        stats = msg.get("stats")
        if stats:
            self.mon_stats[player_id][key] = stats
        # Decrement party_size immediately (same reason as stats_cache handler): avoids a
        # false "partner's party full" block if the partner tries to withdraw their linked mon
        # before the next tick arrives and corrects the count.
        if key in self.party_keys[player_id]:
            old_size = self.party_size.get(player_id, 0)
            self.party_size[player_id] = max(0, old_size - 1)
            log.debug(f"[PARTY] player={player_id}  party_size {old_size} → {self.party_size[player_id]}  (party_to_box)")
        self.party_keys[player_id].discard(key)
        log.debug(f"[PARTY] player={player_id}  party_keys remove {key[:8]}  (party_to_box)")
        entry = self.entry_for(player_id, key)
        if not entry or entry.status != LinkStatus.ALIVE:
            return
        partner     = _partner(player_id)
        partner_mon = entry.b if player_id == "a" else entry.a

        # Queue box_mon when: (a) partner's mon is known to be in their party, OR
        # (b) partner hasn't sent hello yet this session — we can't tell, so queue
        # optimistically. exec_box_mon is idempotent: it no-ops if key not found.
        partner_in_party = (
            partner not in self._has_helld
            or partner_mon.key in self.party_keys[partner]
        )
        if partner_mon and partner_in_party:
            # Cancel any pending party_mon for the same key before queuing box_mon.
            self.queued_commands[partner] = [
                c for c in self.queued_commands[partner]
                if not (c.get("key") == partner_mon.key and c.get("cmd") == "party_mon")
            ]
            self.queued_commands[partner].append({"cmd": "box_mon", "key": partner_mon.key})
            self.party_keys[partner].discard(partner_mon.key)
            log.debug(f"[PARTY] player={partner}  party_keys remove {partner_mon.key[:8]}  (partner party_to_box sync)")
            log.info(f"[{player_id}] party_to_box {key[:8]} → box_mon {partner}:{partner_mon.key[:8]}")
        else:
            log.info(f"[{player_id}] party_to_box {key[:8]} → no box_mon queued (no linked partner or partner already boxed)")
        self._save()

    def _handle_box_to_party(self, player_id: str, msg: dict):
        """
        Player retrieved a linked mon from the PC.
        Both linked mons must always be in the same place. If the partner can't
        fit their mon (party full), block the withdrawal and re-box this player's mon.
        """
        key = msg.get("key", "")
        if not key:
            return

        # During an active auto-rebuild, server-driven party_mon writes confirm
        # via sync_retrieve_done — they don't normally produce a box_to_party
        # event. If we see one whose key the rebuild is restoring (race / Lua
        # detecting the write as a withdrawal), treat it as the rebuild path
        # confirming: add to party_keys and let _maybe_finish_rebuild close
        # things out. Suppresses the noisy reactive HUDs during rebuild.
        rb = self.rebuild_pending.get(player_id)
        if rb and key in rb.get("queued_keys", []):
            self.party_keys[player_id].add(key)
            rb["restored_keys"].add(key)
            self._maybe_finish_rebuild(player_id)
            return

        # Quarantine enforcement: if this mon is a pending (unlinked) capture,
        # re-queue box_mon to send it back to the box. Don't add to party_keys.
        for area_id, players in self.pending_captures.items():
            cap = players.get(player_id)
            if cap and cap.key == key:
                self.queued_commands[player_id].append({"cmd": "box_mon", "key": key})
                log.warning(f"[{player_id}] box_to_party blocked: {key[:8]} is quarantined (pending in {area_id})")
                self.queued_commands[player_id].append({
                    "cmd": "hud_show",
                    "text": "[!] " + self._mon_label(cap) + ": unlinked",
                    "r": 255, "g": 200, "b": 60
                })
                return

        entry = self.entry_for(player_id, key)
        if not entry:
            self.party_keys[player_id].add(key)
            return

        # Dead/memorial mons must stay in the memorial box — re-box immediately.
        if entry.status in (LinkStatus.DEAD, LinkStatus.MEMORIAL):
            my_mon = entry.a if entry.a.key == key else entry.b
            nick = my_mon.nickname or self.adapter.species_name(my_mon.species) or msg.get("nickname") or key[:8]
            self.queued_commands[player_id].append({"cmd": "memorialize", "key": key})
            self.queued_commands[player_id].append({
                "cmd": "hud_show",
                "text": "[x] " + nick + ": dead -> grave",
                "r": 255, "g": 80, "b": 80
            })
            log.warning(f"[{player_id}] box_to_party blocked: {key[:8]} is dead/memorial — re-memorializing")
            return

        if entry.status != LinkStatus.ALIVE:
            self.party_keys[player_id].add(key)
            return
        partner     = _partner(player_id)
        partner_mon = entry.b if player_id == "a" else entry.a

        # Check if partner's party has room using the server's logical view.
        # party_keys is already updated for queued box_mon commands (keys are discarded
        # in _handle_party_to_box before the command is sent), but party_size lags
        # behind until partner's Lua ACKs the command.  Subtract pending box_mon
        # commands from party_size so we don't get false "party full" blocks when a
        # swap is in-flight.
        if partner_mon and partner_mon.key not in self.party_keys[partner]:
            pending_box_mons = sum(
                1 for c in self.queued_commands[partner] if c.get("cmd") == "box_mon"
            )
            adjusted_party_size = max(0, self.party_size.get(partner, 0) - pending_box_mons)
            logical_size = max(self._linked_party_size(partner), adjusted_party_size)
            log.debug(f"[PARTY] box_to_party full-check: partner={partner}  "
                      f"party_size={self.party_size.get(partner,0)}  "
                      f"pending_box_mons={pending_box_mons}  logical={logical_size}")
            if logical_size >= 6:
                self.queued_commands[player_id].append({"cmd": "box_mon", "key": key})
                my_mon = entry.a if entry.a.key == key else entry.b
                nick = my_mon.nickname or self.adapter.species_name(my_mon.species) or msg.get("nickname") or key[:8]
                self.queued_commands[player_id].append({
                    "cmd": "hud_show",
                    "text": "[!] " + nick + ": re-boxed",
                    "r": 255, "g": 200, "b": 60
                })
                log.info(f"[{player_id}] box_to_party blocked: {key[:8]} — partner {partner} party logically full ({logical_size}/6)")
                return

        self.party_keys[player_id].add(key)
        log.debug(f"[PARTY] player={player_id}  party_keys add {key[:8]}  (box_to_party)")
        if partner_mon and partner_mon.key not in self.party_keys[partner]:
            cmd: dict = {"cmd": "party_mon", "key": partner_mon.key}
            if partner_mon.nickname:
                cmd["nickname"] = partner_mon.nickname
            cached = self.mon_stats[partner].get(partner_mon.key)
            if cached:
                cmd["stats"] = cached
            # Cancel any pending box_mon for the same key before queuing party_mon.
            self.queued_commands[partner] = [
                c for c in self.queued_commands[partner]
                if not (c.get("key") == partner_mon.key and c.get("cmd") == "box_mon")
            ]
            self.queued_commands[partner].append(cmd)
            # Don't add to party_keys yet — wait for sync_retrieve_done confirmation.
            log.info(f"[{player_id}] box_to_party {key[:8]} → party_mon {partner}:{partner_mon.key[:8]}"
                     + (" (stats cached)" if cached else " (no cached stats)"))
        self._save()

    # ── helpers ──────────────────────────────────────────────────────────────

    def _linked_party_size(self, player_id: str) -> int:
        """Count party keys excluding bonus (shiny clause) mons."""
        bonus = self.bonus_keys.get(player_id, set())
        return sum(1 for k in self.party_keys[player_id] if k not in bonus)

    def _arm_inflight(self, player_id: str, delivered: list[dict]) -> None:
        """Mark the SYNC_COMMANDS just handed to player_id as on the wire."""
        for c in delivered:
            if c.get("cmd") in SYNC_COMMANDS and c.get("key"):
                self.sync_inflight[player_id][(c["key"], c["cmd"])] = SYNC_INFLIGHT_RECONCILES
            elif c.get("cmd") in DEATH_COMMANDS and c.get("key"):
                self.death_inflight[player_id][c["key"]] = FAINT_REPAIR_RECONCILES

    def _ack_inflight(self, player_id: str, key: str) -> None:
        """Forget every in-flight SYNC_COMMAND for key — the client has answered."""
        inflight = self.sync_inflight[player_id]
        for ident in [i for i in inflight if i[0] == key]:
            del inflight[ident]

    def _expire_inflight(self, player_id: str) -> None:
        """Spend one reconciler pass off player_id's in-flight window.

        Called only from _reconcile_party_keys, and only once it is actually going to
        reconcile — a pass the reconciler skips (rebuild, trade) is not a chance the
        client had to answer, so it must not count against the window.
        """
        inflight = self.sync_inflight[player_id]
        for ident in list(inflight):
            inflight[ident] -= 1
            if inflight[ident] <= 0:
                del inflight[ident]

    def _has_pending_command(self, player_id: str, key: str, *cmds: str) -> bool:
        """True if a matching command for key is queued for player_id, or has been
        delivered and is still unanswered (in flight on the client — see sync_inflight)."""
        if any((key, c) in self.sync_inflight[player_id] for c in cmds):
            return True
        if key in self.death_inflight[player_id] and any(c in DEATH_COMMANDS for c in cmds):
            return True
        return any(
            c.get("key") == key and c.get("cmd") in cmds
            for c in self.queued_commands[player_id]
        )

    def _is_quarantined(self, player_id: str, key: str) -> bool:
        """True if key is a pending (un-linked) capture for player_id."""
        for players in self.pending_captures.values():
            cap = players.get(player_id)
            if cap and cap.key == key:
                return True
        return False

    def _repair_lost_faints(self, player_id: str, party: list, in_battle: bool) -> None:
        """Owner ruling O-24: re-issue a DEATH_COMMAND the client never applied.

        On for every generation, as ruled -- there is no per-game switch.  The evidence is
        the party snapshot every client sends, `key` and `hp` per mon.  A key there at
        HP > 0 whose link is not ALIVE is a dead mon walking: either the force_faint was
        lost, or the player revived it (or withdrew a memorialized one); dead stays dead.
        Only the PARTY is visible: a dead mon in a box never qualifies, so a lost faint on a
        mon that was then deposited is not repaired here.

        The re-issue is always plain force_faint, even in Explode Mode: the Explosion is a
        battle-time cue, and both commands leave the mon at zero HP.

        Deduped by _has_pending_command: a death command still queued (the snapshot that
        races the original) or delivered within death_inflight's FAINT_REPAIR_RECONCILES
        window is not repeated.  Only out-of-battle passes count, so a faint held to battle
        end is not re-sent mid-battle.  FAINT_REPAIR_BUDGET re-issues per incident, then
        the key stalls (error log + faint_repair_stalled) for FAINT_REPAIR_COOLDOWN passes
        and its budget refills.
        """
        inflight = self.death_inflight[player_id]
        spent = self.faint_repairs[player_id]
        stalled = self.faint_repair_stalled[player_id]
        if not in_battle:
            for key in list(inflight):
                inflight[key] -= 1
                if inflight[key] <= 0:
                    del inflight[key]
            for key in list(stalled):
                stalled[key] -= 1
                if stalled[key] <= 0:
                    del stalled[key]
                    spent.pop(key, None)
                    log.warning(f"[{player_id}] {key} faint repair cool-down over — budget refilled")
        if self.run_over:
            return
        for mon in party:
            key, hp = mon.get("key"), mon.get("hp")
            if not key or not isinstance(hp, (int, float)):
                continue                      # no hp on the wire = no evidence
            if hp <= 0:
                spent.pop(key, None)          # the kill landed; a later revive is a new incident
                stalled.pop(key, None)
                continue
            entry = self.entry_for(player_id, key)
            if entry is None or entry.status == LinkStatus.ALIVE or key in stalled:
                continue
            half = entry.a if player_id == "a" else entry.b
            if half is None or half.key != key or self._is_quarantined(player_id, key):
                continue
            if self._has_pending_command(player_id, key, *DEATH_COMMANDS):
                continue
            n = spent.get(key, 0)
            if n >= FAINT_REPAIR_BUDGET:
                stalled[key] = FAINT_REPAIR_COOLDOWN
                log.error(f"[{player_id}] {key} is dead but still alive in party after "
                          f"{FAINT_REPAIR_BUDGET} re-issued force_faint — stalled, budget "
                          f"refills in {FAINT_REPAIR_COOLDOWN} passes")
                continue
            spent[key] = n + 1
            self.queued_commands[player_id].append(
                {"cmd": "force_faint", "key": key, "nickname": half.nickname or ""})
            log.warning(f"[{player_id}] {key} is dead but alive in party (hp={hp}) — "
                        f"re-issued force_faint ({n + 1}/{FAINT_REPAIR_BUDGET})")

    def _reconcile_party_keys(self, player_id: str, party: list, in_battle: bool = False) -> None:
        """
        Repair `party_keys[player_id]` to match Lua's actual party snapshot.

        Lua sends the full party each tick.  If our tracked set has drifted (e.g.
        a phantom party_to_box from the Lua diff loop made the server think a mon
        was deposited when it really wasn't), this reconciles silently.

        For each ghost-boxed key (in tick but not in party_keys) we also check the
        partner: if the partner is alive and currently boxed per the server, queue
        a party_mon to pull them back so the Soul Link party-sync rule is restored
        in both halves at once.

        Skipped:
          - Mons with pending box_mon/party_mon/memorialize (in-flight; reconciling
            mid-flight would race with Lua's pending_sync queue).
          - Memorial/dead mons (must never be re-added to party_keys; if Lua sees
            one in party that's a separate problem handled by _handle_box_to_party).
          - Quarantined mons (pending_captures; must stay boxed until link forms).
          - Active rebuild (rebuild_pending owns party_keys bookkeeping for its
            duration; let it finish, then next tick reconciles cleanly).
        """
        # O-24 is keyed by link status and death_inflight, not by the party bookkeeping the gates
        # below protect, so it runs first (invariant review MINOR-5: a rebuild whose answer was
        # lost, or an `applying` trade, used to switch it off for as long as they lasted).
        self._repair_lost_faints(player_id, party, in_battle)
        if self.rebuild_pending.get(player_id):
            return
        # A trade owns party bookkeeping while the swap is in flight + a short settle window after.
        # The swap is party↔party (both halves stay in party), so the reconciler can only produce
        # false positives here — most damagingly a spurious party_mon ("Unbox") to the partner when
        # a settling tick makes the freshly-received mon look "ghost-boxed". Only the 'applying'
        # phase mutates RAM, so only it suppresses reconciliation: a trade wedged at the menu /
        # confirm stage (player walked away) must not suspend drift enforcement for both runs
        # until the watchdog fires.
        if self.pending_trade is not None and self.pending_trade.get("phase") == "applying":
            return
        if self._trade_settle_ticks.get(player_id, 0) > 0:
            self._trade_settle_ticks[player_id] -= 1
            return

        # This pass is a real chance for the client to have answered what we sent it.
        self._expire_inflight(player_id)

        actual_keys = self._canonical_party_keys(player_id, party)
        # An uncertain/conflicted trade still owns its two keys: until it settles, either key may be
        # on either side, so drift enforcement on them would issue a spurious Unbox/deposit.
        frozen = ({self.pending_trade["a_key"], self.pending_trade["b_key"]}
                  if self.trade_problem() else set()) | set(self.ambiguous_keys[player_id])
        tracked_keys = self.party_keys[player_id]
        partner = _partner(player_id)

        # Ghost-boxed: Lua reports key in party, server thinks it's deposited.
        for key in actual_keys - tracked_keys - frozen:
            if self._has_pending_command(player_id, key,
                                          "box_mon", "party_mon", "memorialize"):
                continue
            if self._is_quarantined(player_id, key):
                continue
            entry = self.entry_for(player_id, key)
            if entry is None:
                # Untracked mon (e.g. unlinked, never had a partner) — server
                # has no opinion about box vs. party for it, so just sync.
                self.party_keys[player_id].add(key)
                continue
            if entry.status != LinkStatus.ALIVE:
                # Memorial/dead mon should not be in party — server is right,
                # Lua is wrong.  Leave party_keys alone; _handle_box_to_party
                # already re-memorializes via the dead/memorial guard.
                continue
            self.party_keys[player_id].add(key)
            log.warning(f"[{player_id}] tick reconcile: re-added {key[:8]} to party_keys "
                        f"(ghost-boxed — Lua reports in party)")
            # Pull partner if they're server-side boxed too — restores the rule
            # break that the phantom deposit created.
            partner_mon = entry.b if player_id == "a" else entry.a
            if (partner_mon and partner_mon.key not in self.party_keys[partner]
                    and not self._has_pending_command(partner, partner_mon.key,
                                                      "party_mon", "box_mon", "memorialize")):
                cmd: dict = {"cmd": "party_mon", "key": partner_mon.key}
                if partner_mon.nickname:
                    cmd["nickname"] = partner_mon.nickname
                cached = self.mon_stats[partner].get(partner_mon.key)
                if cached:
                    cmd["stats"] = cached
                self.queued_commands[partner].append(cmd)
                log.warning(f"[{partner}] tick reconcile: queued party_mon {partner_mon.key[:8]} "
                            f"to restore party-sync with {player_id}")

        # Ghost-party: server thinks key is in party, Lua doesn't see it.  Rarer,
        # but possible if a real party_to_box's emission was suppressed somewhere.
        for key in tracked_keys - actual_keys - frozen:
            if self._has_pending_command(player_id, key,
                                          "box_mon", "party_mon", "memorialize"):
                continue
            if key in self.pending_memorials.get(player_id, set()):
                continue
            self.party_keys[player_id].discard(key)
            log.warning(f"[{player_id}] tick reconcile: discarded {key[:8]} from party_keys "
                        f"(ghost-party — Lua doesn't see it in party)")

    # ── auto-rebuild helpers (after whiteout) ────────────────────────────────

    def _alive_pc_mons(self, player_id: str) -> list[tuple[str, str]]:
        """Enumerate alive linked-pair halves boxed in this player's PC after a
        whiteout, paired with their partner's boxed half. Walks self.links in
        chronological capture order (oldest first). Bonus (shiny-clause) pairs
        are eligible too — they are linked pairs and follow the same
        co-location rule. Pending unlinked captures are NOT included; they
        remain quarantined per Soul Link rules.

        Returns a list of (player_key, partner_key) tuples.
        """
        out: list[tuple[str, str]] = []
        partner = _partner(player_id)
        my_party = self.party_keys.get(player_id, set())
        partner_party = self.party_keys.get(partner, set())
        for entry in self.links:
            if entry.status != LinkStatus.ALIVE:
                continue
            my_mon = entry.a if player_id == "a" else entry.b
            partner_mon = entry.b if player_id == "a" else entry.a
            if not my_mon or not partner_mon:
                continue
            if not my_mon.key or not partner_mon.key:
                continue
            # Both halves must be currently boxed (Soul Link co-location).
            if my_mon.key in my_party or partner_mon.key in partner_party:
                continue
            out.append((my_mon.key, partner_mon.key))
        return out

    def _plan_rebuild(self, player_id: str) -> tuple[list[MonInfo], list[MonInfo]]:
        """Greedy pick of alive boxed pairs for auto-rebuild after a whiteout.
        Returns (player_picks, partner_picks). A pair is included only if the
        partner has room (logical party + already-picked partner halves < 6);
        otherwise the pair is skipped entirely — both halves must be
        co-located. Stops at 6 player picks or when candidates are exhausted.
        """
        partner = _partner(player_id)
        partner_size_start = self._linked_party_size(partner)
        player_picks: list[MonInfo] = []
        partner_picks: list[MonInfo] = []
        for my_key, _partner_key in self._alive_pc_mons(player_id):
            if len(player_picks) >= 6:
                break
            if partner_size_start + len(partner_picks) >= 6:
                # Partner has no more room. Per Soul Link co-location, both
                # halves must move together — skip this and any further pairs.
                break
            entry = self.entry_for(player_id, my_key)
            if not entry:
                continue
            my_mon = entry.a if player_id == "a" else entry.b
            partner_mon = entry.b if player_id == "a" else entry.a
            if not my_mon or not partner_mon:
                continue
            player_picks.append(my_mon)
            partner_picks.append(partner_mon)
        return player_picks, partner_picks

    def _queue_rebuild_commands(self, player_id: str,
                                 player_picks: list[MonInfo],
                                 partner_picks: list[MonInfo]) -> None:
        """Queue party_mon commands for both player and partner halves of the
        rebuild, plus a persistent REBUILDING banner for the player and a
        soft informational HUD for the partner.
        """
        partner = _partner(player_id)

        def _enqueue(pid: str, mon: MonInfo) -> None:
            # Cancel any stale box_mon for this key (same idempotent pattern
            # used in the post-link path at _handle_capture).
            self.queued_commands[pid] = [
                c for c in self.queued_commands[pid]
                if not (c.get("key") == mon.key and c.get("cmd") == "box_mon")
            ]
            cmd: dict = {"cmd": "party_mon", "key": mon.key}
            if mon.nickname:
                cmd["nickname"] = mon.nickname
            cached = self.mon_stats[pid].get(mon.key)
            if cached:
                cmd["stats"] = cached
            self.queued_commands[pid].append(cmd)

        for mon in player_picks:
            _enqueue(player_id, mon)
        for mon in partner_picks:
            _enqueue(partner, mon)

        def _label(mon: MonInfo) -> str:
            if mon.nickname:
                return mon.nickname
            if mon.species:
                name = self.adapter.species_name(mon.species)
                if name:
                    return name
            return mon.key[:6]

        labels = [_label(m) for m in player_picks[:3]]
        suffix = "" if len(player_picks) <= 3 else f" +{len(player_picks) - 3}"
        banner_text = "REBUILDING: " + ", ".join(labels) + suffix
        self.queued_commands[player_id].append({
            "cmd":  "rebuild_start",
            "text": banner_text,
            "keys": [m.key for m in player_picks],
        })
        if partner_picks:
            self.queued_commands[partner].append({
                "cmd":    "hud_show",
                "text":   f">> Rebuilt {len(player_picks)}",
                "r": 100, "g": 180, "b": 255,
                "frames": 360,
            })

    def _maybe_finish_rebuild(self, player_id: str) -> None:
        """Clear rebuild state and tell the client to dismiss the banner once
        every queued rebuild key has either been confirmed
        (sync_retrieve_done) or dropped (sync_retrieve_failed)."""
        rb = self.rebuild_pending.get(player_id)
        if not rb:
            return
        queued = set(rb.get("queued_keys", []))
        restored = rb.get("restored_keys", set())
        if queued - restored:
            return  # still waiting on some sync events
        self.queued_commands[player_id].append({"cmd": "rebuild_done"})
        log.info(f"[{player_id}] rebuild complete — {len(restored)} restored, "
                 f"{len(queued) - len(restored)} dropped")
        self.rebuild_pending[player_id] = None
        self._save()

    def _handle_key_change(self, player_id: str, msg: dict):
        """Handle a key change (nature change, NPC trade, evolution, transform, APEX chip).

        The Lua client detected that a mon's key changed.  For Gen 3 (RR Nature
        Changer), personality changes but otId/species/nickname stay the same.
        For NPC in-game trades, the outgoing mon's key is replaced by the
        received mon's key. Owner ruling 35 retires that pair after migration if the
        resulting pair violates an enabled species/family, gender or type clause.
        For Gen 1, evolution changes the internal species index in the key, and
        may also update species/nickname.

        Validate -> accept | reject -> mutate (PLAN A1).  The reply always carries one of
        `key_change_ack{old_key,new_key,migrated}` or `key_change_rejected{old_key,new_key,
        reason}`, appended to this player's queue so it drains in the same reply.

          - replay (`new_key` already indexes the old key's link): ack, no mutation;
          - `old_key` referenced nowhere (Gen 3 nature change on an unlinked mon): ack
            `migrated:false`, no mutation;
          - `new_key` load-bearing elsewhere (a different ALIVE link, or any live structure,
            including the presentation caches through `presentation_key_in_use`): REJECT.
            The pair cannot be told apart from the colliding one any more, so the decided
            consequence (U5) is fail-closed: the old key's link dies `cause="identity_lost"`,
            partner force-fainted, both memorialized.  A DEAD/MEMORIAL index hit is NOT a
            collision -- a buried key is reusable by design (`_index_entry` logs it);
          - otherwise migrate old -> new in every server-side tracking structure.
            Optional ``new_species`` / ``new_nickname`` update the linked MonInfo (Gen 1
            evolution).  If the old key's link is already DEAD/MEMORIAL the death is
            re-queued under the new key (A2: a transformation never revives).
        """
        old_key = msg.get("old_key", "")
        new_key = msg.get("new_key", "")
        if not old_key or not new_key or old_key == new_key:
            return

        reason = msg.get("reason", "nature_change")
        log.info(f"[{player_id}] key_change ({reason}): {old_key[:8]} → {new_key[:8]}")
        side = player_id
        entry = self.entry_for(player_id, old_key)
        pt = self.pending_trade
        if (entry is None and pt and pt.get("phase") in ("applying", "uncertain", "conflict")
                and old_key in (pt.get("a_key"), pt.get("b_key"))):
            entry = pt["link"]            # mid-trade a player holds the partner's (unswapped) half

        def _ack(migrated: bool):
            msg["_key_change_status"] = "migrated" if migrated else msg.get("_key_change_status", "unknown")
            self.queued_commands[player_id].append(
                {"cmd": "key_change_ack", "old_key": old_key, "new_key": new_key,
                 "migrated": migrated})

        # KEY-SCOPE-5: the ledger answers a replay even after a reconnect re-reported old_key.
        live_old = self.entry_for(player_id, old_key)
        if ((live_old is None or live_old.status != LinkStatus.ALIVE)       # F4: a new live mon on old_key
                and {"old_key": old_key, "new_key": new_key} in self.key_migration_ledger[player_id]):
            log.info(f"[{player_id}] key_change replay for {new_key[:8]} (ledger) — already migrated")
            msg["_key_change_status"] = "replayed"
            _ack(False)
            return

        if entry is None and not self._key_refs(old_key, player_id):
            if self.entry_for(player_id, new_key) is not None:
                # Replay: the migration already happened (resent after a reconnect).
                log.info(f"[{player_id}] key_change replay for {new_key[:8]} — already migrated")
            else:
                # Unknown old key: nothing to migrate (Gen 3 nature change on an unlinked mon).
                log.warning(
                    f"[{player_id}] key_change: old_key {old_key[:8]} not found in any"
                    " tracking structure — possible spurious event"
                )
            _ack(False)
            return

        # Collision check BEFORE any mutation.
        # KEY-SCOPE: only THIS player's keys can collide; the partner may hold an equal key
        hit = self.entry_for(player_id, new_key)
        collision = ""
        if hit is not None and hit is not entry and hit.status == LinkStatus.ALIVE:
            collision = f"live link in {hit.area_id}"
        else:
            refs = self._key_refs(new_key, player_id)
            # KEY-SCOPE-4: this player's own tick can beat its key_change here.  When the latest
            # party snapshot holds new_key exactly once and old_key nowhere, that party hit IS the
            # changing mon; a real twin shows as a second copy, or old_key still in the party.
            # KEY-SCOPE-5: counted on the RAW snapshot, before blob filtering drops entries.
            # cx-06ec4e8e F3: and new_key first appeared only after old_key was last seen -- a
            # new_key already present beside old_key is another mon.
            raw = self.party_key_census.get(player_id)
            last = self.key_last_seen.get(player_id, {})
            self_report = (raw is not None and raw.get(new_key, 0) == 1 and old_key not in raw
                           and old_key in last
                           and self.key_streak[player_id].get(new_key, 0) > last[old_key])
            if self_report:
                refs -= {"party_keys", "partner_blobs"}
            hook = self.presentation_key_in_use
            used = (False if refs or not hook else
                    hook(new_key, player_id, party=False) if self_report else hook(new_key, player_id))
            if refs:
                collision = ", ".join(sorted(refs))
            elif used is None:               # KEY-SCOPE-5: an advertised box census is missing or stale
                collision = "box census unavailable"
            elif used:
                collision = "presentation cache"
        if collision == "box census unavailable":
            # cx-8f3a6ce9 F2: uncertainty, not evidence of a collision -- refuse, retire nothing
            log.warning(f"[{player_id}] key_change {old_key[:8]} → {new_key[:8]} refused: box census "
                        f"unavailable; the pair keeps its identity")
            self.queued_commands[player_id].append(
                {"cmd": "key_change_rejected", "old_key": old_key, "new_key": new_key,
                 "reason": collision})
            msg["_key_change_status"] = "rejected"
            return
        if collision:
            log.error(f"[{player_id}] key_change REJECTED: {new_key[:8]} is load-bearing "
                      f"({collision}); {old_key[:8]} keeps its identity")
            self.queued_commands[player_id].append(
                {"cmd": "key_change_rejected", "old_key": old_key, "new_key": new_key,
                 "reason": f"key collision: {collision}"})
            msg["_rejected"] = True
            msg["_key_change_status"] = "rejected"
            if entry is not None and entry.status == LinkStatus.ALIVE:
                # U5: the pair dies -- the mon can no longer be told apart from the other one.
                self._propagate_faint(player_id, entry, cause="identity_lost")
            return

        # ── accepted: migrate ────────────────────────────────────────────────────────
        # 1. Links + key index
        if entry is not None:
            # this player's half; mid-trade the half it holds is the partner's (unswapped) one
            own = getattr(entry, side)
            sides = ([side] if own and own.key == old_key else
                     [pid for pid in ("a", "b") if getattr(entry, pid) and getattr(entry, pid).key == old_key])
            for pid in sides:
                self._unindex(pid, old_key, entry)
                for mon in ((entry.a, entry.encounter_a) if pid == "a" else (entry.b, entry.encounter_b)):
                    if mon and mon.key == old_key:
                        mon.key = new_key
            mon = getattr(entry, side)
            if mon and mon.key == new_key:
                # Update species/nickname if provided (Gen 1 evolution changes species)
                new_species = msg.get("new_species")
                new_nickname = msg.get("new_nickname")
                if new_species is not None:
                    mon.species = new_species
                if new_nickname is not None:
                    mon.nickname = new_nickname
            self._index_entry(entry)   # logs KEY COLLISION when a buried key is displaced

        # 2. Pending captures
        for _area_id, players in self.pending_captures.items():
            cap = players.get(player_id)
            if cap and cap.key == old_key:
                cap.key = new_key

        # 3. Preserved unpaired mons
        for players in self.unpaired_mons.values():
            mon = players.get(player_id)
            if mon and mon.key == old_key:
                mon.key = new_key
                if msg.get("new_species") is not None:
                    mon.species = msg["new_species"]
                if msg.get("new_nickname") is not None:
                    mon.nickname = msg["new_nickname"]

        # 4. Party keys
        if old_key in self.party_keys[player_id]:
            self.party_keys[player_id].discard(old_key)
            self.party_keys[player_id].add(new_key)

        # 5. Mon stats cache
        if old_key in self.mon_stats[player_id]:
            self.mon_stats[player_id][new_key] = self.mon_stats[player_id].pop(old_key)

        # 6. Bonus keys (shiny clause)
        if old_key in self.bonus_keys[player_id]:
            self.bonus_keys[player_id].discard(old_key)
            self.bonus_keys[player_id].add(new_key)

        # 7. Pending memorials
        if old_key in self.pending_memorials[player_id]:
            self.pending_memorials[player_id].discard(old_key)
            self.pending_memorials[player_id].add(new_key)

        # 8. Queued commands referencing the old key — and the same commands already
        #    on the wire, so the drift reconciler still sees them as in flight.
        for cmd in self.queued_commands[player_id]:
            for field in ("key", "old_key"):
                if cmd.get(field) == old_key:
                    cmd[field] = new_key
        inflight = self.sync_inflight[player_id]
        for ident in [i for i in inflight if i[0] == old_key]:
            inflight[(new_key, ident[1])] = inflight.pop(ident)

        # 9. Pending bonus queue (shiny keys in partner's pending_bonus)
        owner_q = _partner(player_id)                   # KEY-SCOPE: this player's shiny waits there
        self.pending_bonus[owner_q] = deque(
            new_key if k == old_key else k for k in self.pending_bonus[owner_q]
        )

        # 10. Partner blobs (rival team swap cache) and an active whiteout rebuild
        for be in self.partner_blobs[player_id]:
            if be.get("key") == old_key:
                be["key"] = new_key
        rb = self.rebuild_pending.get(player_id)
        if rb:
            for field in ("queued_keys", "queued_partner_keys"):
                rb[field] = [new_key if k == old_key else k for k in rb.get(field, [])]
            if old_key in rb.get("restored_keys", set()):
                rb["restored_keys"].discard(old_key)
                rb["restored_keys"].add(new_key)

        # 11. An in-flight trade naming the old key
        pt = self.pending_trade
        if pt:
            for field in ("a_key", "b_key"):
                if pt.get(field) == old_key:
                    pt.setdefault(f"{field}0", old_key)   # MINOR-8: the commit also retires the original
                    pt[field] = new_key
            for pid, n in (pt.get("new") or {}).items():
                if n and n[0] == old_key:
                    pt["new"][pid] = (new_key, msg.get("new_species") or n[1])

        self.key_migration_ledger[player_id].append({"old_key": old_key, "new_key": new_key})
        _ack(True)

        # Owner ruling 35: the NPC exchange already happened. Keep its real identity,
        # then retire THIS pair if the resulting pair violates an enabled clause.
        if reason == "npc_trade" and entry is not None and entry.status == LinkStatus.ALIVE:
            violation = self._check_link_mutation_violation(entry)
            if violation:
                log.warning("[%s] NPC trade clause retirement in %s: %s", player_id, entry.area_id, violation[0])
                changed = getattr(entry, player_id)
                if changed and changed.key == new_key:
                    self.party_keys[player_id].discard(new_key)
                    if not self._has_pending_command(player_id, new_key, *DEATH_COMMANDS):
                        self.queued_commands[player_id].append(
                            {"cmd": "force_faint", "key": new_key, "nickname": changed.nickname or ""})
                self._propagate_faint(player_id, entry, cause="npc_trade_clause")

        # A2: a transformation never revives.  The link was already buried under the old
        # key, so the death is owed again under the new one.
        # Two obligations, deduplicated separately (review cx-6aacc4f1 #2): a queued memorial
        # must not suppress the faint and a queued faint must not suppress the memorial.
        if entry is not None and entry.status != LinkStatus.ALIVE:
            owed = []
            if not self._has_pending_command(player_id, new_key, *DEATH_COMMANDS):
                self.queued_commands[player_id].append({"cmd": "force_faint", "key": new_key})
                owed.append("force_faint")
            if not self._has_pending_command(player_id, new_key, "memorialize"):
                self._queue_memorialize(player_id, new_key)
                owed.append("memorialize")
            if owed:
                log.info(f"[{player_id}] key_change on a buried link — re-queued "
                         f"{'+'.join(owed)} for {new_key[:8]}")

        self._save()

    def _canonical_party_keys(self, player_id: str, party: list) -> set:
        """KEY-SCOPE-5, the drift reconciler only (a hello is the cartridge's own word): a stale
        tick's retired alias counts as its migration's terminal key, so it cannot roll the
        migration back. Only the exact stale case aliases: the terminal key is tracked in the
        party, absent from this snapshot, and (when the previous snapshot saw it) sat in the
        alias's slot. Anything else is a live mon under its own key."""
        newest: dict[str, dict] = {}
        for rec in self.key_migration_ledger[player_id]:
            newest[rec["old_key"]] = newest[rec["new_key"]] = rec
        keys = {m.get("key") for m in party if isinstance(m, dict) and m.get("key")}
        prev = self.prev_party_slots.get(player_id) or {}
        out = set()
        for m in party:
            key = m.get("key") if isinstance(m, dict) else None
            if not key:
                continue
            canon, seen = key, set()
            while canon not in seen and canon in newest and newest[canon]["old_key"] == canon:
                seen.add(canon)
                canon = newest[canon]["new_key"]
            stale = (canon != key and canon not in keys and canon in self.party_keys[player_id]
                     and prev.get(canon, m.get("slot")) == m.get("slot"))
            out.add(canon if stale else key)
        return out

    def _key_refs(self, key: str, player_id: str) -> set[str]:
        """Names of the live tracking structures that reference `key` (empty = unreferenced).

        The collision preflight for `_handle_key_change`.  `_key_index` is judged separately
        (a DEAD/MEMORIAL hit is not load-bearing) and `mon_stats` is deliberately absent: it
        is a never-pruned cache that still holds every buried key, and the migration
        overwrites it -- counting it would turn "buried keys are reusable" into a pair kill.

        KEY-SCOPE: only player_id's structures count (the partner may hold an equal key). A
        player's own shiny waits in the PARTNER's pending_bonus queue; the pending trade names
        both players' keys and still counts for either.
        """
        refs: set[str] = set()
        if key in self.pending_bonus[_partner(player_id)]:
            refs.add("pending_bonus")
        for pid in (player_id,):
            if key in self.party_keys[pid]:
                refs.add("party_keys")
            if key in self.bonus_keys[pid]:
                refs.add("bonus_keys")
            if key in self.pending_memorials[pid]:
                refs.add("pending_memorials")
            if any(be.get("key") == key for be in self.partner_blobs[pid]):
                refs.add("partner_blobs")
            if any(c.get("key") == key or c.get("old_key") == key
                   for c in self.queued_commands[pid]):
                refs.add("queued_commands")
            if any(i[0] == key for i in self.sync_inflight[pid]):
                refs.add("sync_inflight")
            rb = self.rebuild_pending.get(pid)
            if rb and (key in rb.get("queued_keys", []) or key in rb.get("queued_partner_keys", [])
                       or key in rb.get("restored_keys", set())):
                refs.add("rebuild_pending")
        if any(players.get(player_id) and players[player_id].key == key
               for players in self.pending_captures.values()):
            refs.add("pending_captures")
        if any(players.get(player_id) and players[player_id].key == key
               for players in self.unpaired_mons.values()):
            refs.add("unpaired_mons")
        pt = self.pending_trade
        if pt and key in (pt.get("a_key"), pt.get("b_key")):
            refs.add("pending_trade")
        return refs

    def _check_link_mutation_violation(self, entry: LinkEntry) -> tuple[str, str] | None:
        """Validate the post-exchange pair without treating its own identity as a duplicate."""
        if entry.status != LinkStatus.ALIVE or not entry.a or not entry.b:
            return None
        return self._check_link_violation(entry.a, entry.b, existing_entry=entry)

    def _check_link_violation(self, a_mon: MonInfo, b_mon: MonInfo, *,
                              existing_entry: LinkEntry | None = None) -> tuple[str, str] | None:
        """Return (violation_message, violating_player_id) or None if the link is valid."""
        # ── Key collision ─────────────────────────────────────────────────────────────
        # CHECKED FIRST, and unconditionally: this is not a rule the player opted into, it
        # is a correctness floor. A mon key is not guaranteed unique -- Gen 1's is
        # DVs:OTID:species, and OT is the same for every mon a player caught themselves, so
        # two of one species with identical DVs collide (about 1 in 65536 per same-species
        # pair). `_key_index` is a plain dict, so the second link silently ALIASED the
        # first: every later lookup by that key -- faint propagation above all -- resolved
        # to the wrong pair, and killed the wrong mon on the partner's cartridge.
        #
        # Refusing is strictly better than aliasing, and refusing here rather than at
        # _index_entry means it reuses the machinery a clause rejection already has: the
        # capture is force-fainted and buried, the area stays open, and the player simply
        # catches another one. A collision is rare enough that costing someone one
        # encounter is a fair price for never silently mixing two mons up.
        for mon, pid in ((a_mon, "a"), (b_mon, "b")):
            if not (mon and mon.key):
                continue
            existing = self.entry_for(pid, mon.key)       # KEY-SCOPE: per player
            if existing is not None and existing is not existing_entry and existing.status == LinkStatus.ALIVE:
                # Player-facing: this string reaches the HUD (gui_prompt at :2345 / :2632).
                # Name the mon and the place; the mon key and the raw area_id are the
                # operator's, and the caller's log line carries both.
                who = mon.nickname or (self.adapter.species_name(mon.species) if mon.species else None) or "that Pokemon"
                where = self.adapter.area_display_name(existing.area_id) or existing.area_id
                return (f"Key collision: {who} is already linked in {where}", pid)

        if self.species_lock and a_mon.species and b_mon.species:
            # Cross-player check: A and B can't be the same species/family
            a_base = self.adapter.evo_family(a_mon.species)
            b_base = self.adapter.evo_family(b_mon.species)
            if a_base == b_base:
                a_name = self.adapter.species_name(a_mon.species)
                b_name = self.adapter.species_name(b_mon.species)
                if a_mon.species == b_mon.species:
                    # Both sides are bad — blame the later capturer (caller decides)
                    return (f"Species clause: both are {a_name}", "")
                else:
                    return (f"Species clause: {a_name} & {b_name} same family", "")

            # Same-save duplicate check: neither player can have a species/family
            # that already exists in one of their other alive links.
            for entry in self.links:
                if entry is existing_entry or entry.status != LinkStatus.ALIVE:
                    continue
                if entry.a and entry.a.species and self.adapter.evo_family(entry.a.species) == a_base:
                    existing_name = self.adapter.species_name(entry.a.species)
                    a_name = self.adapter.species_name(a_mon.species)
                    return (f"Species clause: {a_name} — A already has {existing_name}", "a")
                if entry.b and entry.b.species and self.adapter.evo_family(entry.b.species) == b_base:
                    existing_name = self.adapter.species_name(entry.b.species)
                    b_name = self.adapter.species_name(b_mon.species)
                    return (f"Species clause: {b_name} — B already has {existing_name}", "b")
                if entry.a and entry.a.species and self.adapter.evo_family(entry.a.species) == b_base:
                    existing_name = self.adapter.species_name(entry.a.species)
                    b_name = self.adapter.species_name(b_mon.species)
                    return (f"Species clause: {b_name} — already have {existing_name}", "b")
                if entry.b and entry.b.species and self.adapter.evo_family(entry.b.species) == a_base:
                    existing_name = self.adapter.species_name(entry.b.species)
                    a_name = self.adapter.species_name(a_mon.species)
                    return (f"Species clause: {a_name} — already have {existing_name}", "a")

        if self.gender_lock and a_mon.key and b_mon.key and a_mon.species and b_mon.species:
            a_gender = self.adapter.gender_from_key(a_mon.key, a_mon.species)
            b_gender = self.adapter.gender_from_key(b_mon.key, b_mon.species)
            if a_gender in ("male", "female") and a_gender == b_gender:
                symbol = "♂" if a_gender == "male" else "♀"
                return (f"Gender clause: both are {symbol}", "")

        if self.type_lock and a_mon.species and b_mon.species:
            a_types = self.adapter.species_types(a_mon.species)
            b_types = self.adapter.species_types(b_mon.species)
            if a_types and b_types:
                # Collect unique types for each mon (monotypes have t1==t2)
                a_set = {a_types[0], a_types[1]} if a_types[0] != a_types[1] else {a_types[0]}
                b_set = {b_types[0], b_types[1]} if b_types[0] != b_types[1] else {b_types[0]}
                shared = a_set & b_set
                if shared:
                    shared_names = ", ".join(sorted(self.adapter.type_name(t) for t in shared))
                    return (f"Type clause: shared {shared_names}", "")

        # All clause checks passed — log the evaluation summary at DEBUG.
        a_name = self.adapter.species_name(a_mon.species) if a_mon.species else a_mon.key[:8]
        b_name = self.adapter.species_name(b_mon.species) if b_mon.species else b_mon.key[:8]
        checks = []
        if self.species_lock:
            checks.append("species=OK")
        if self.gender_lock:
            checks.append("gender=OK")
        if self.type_lock:
            checks.append("type=OK")
        if checks:
            log.debug(f"[CLAUSE] {a_name} ↔ {b_name}  " + "  ".join(checks))
        return None

    def queued_death_cmd(self, player_id: str, key: str) -> str | None:
        """The death command queued for `key` on `player_id`, or None.

        One predicate for every "did the partner get killed" check, so Explode Mode's
        `force_explode` can never again be missed by a caller that only knew about
        `force_faint`.  Returns the command name so callers can report what happened.
        """
        for c in self.queued_commands.get(player_id, []):
            if c.get("cmd") in DEATH_COMMANDS and c.get("key") == key:
                return c["cmd"]
        return None

    def _phone_data(self, caller: str, caller_mon: MonInfo | None,
                    receiver_mon: MonInfo | None) -> dict | None:
        """PHONE-NAMES (docs/protocol.md section 5): an O-29 call as its receiver hears it. None
        when the caller has no real trainer name, so the client keeps the fixed text."""
        identity = self.player_identity.get(caller) or {}
        name = self.trainer_names.get(caller) or (
            "" if identity.get("trainer_name_placeholder") else identity.get("trainer_name") or "")
        if not name:
            return None
        data: dict = {"trainer_name": name}
        for field, mon in (("caller_mon", caller_mon), ("receiver_mon", receiver_mon)):
            if mon is not None and mon.species:
                data[field] = {"species_id": mon.species, "nickname": mon.nickname or ""}
        return data

    def _phone_extra(self, caller: str, caller_mon: MonInfo | None, receiver_mon: MonInfo | None) -> dict:
        data = self._phone_data(caller, caller_mon, receiver_mon)
        return {"phone_data": data} if data else {}

    def _propagate_faint(self, player_id: str, entry: LinkEntry, killer: dict | None = None,
                         level: int = 0, cause: str = "battle"):
        """Mark entry dead, queue force_faint (or force_explode if Explode Mode is on)
        for partner, queue memorialize for both.  `cause` is what the memorial records:
        "battle" (default), "identity_lost" (a rejected identity migration), or
        "npc_trade_clause" (owner ruling 35). Non-battle retirements do not trigger Explode."""
        partner     = _partner(player_id)
        player_mon  = entry.a if player_id == "a" else entry.b
        partner_mon = entry.b if player_id == "a" else entry.a
        if player_mon:
            # A dead mon is not in the linked party; owned here, not left to each caller (the hello
            # path did not do it, so a fresh death still counted toward "party full").
            self.party_keys[player_id].discard(player_mon.key)
        if partner_mon:
            # Explode Mode: send `force_explode` so the client takes the
            # Variant-3 menu-skip path (auto-Explosion for active battlers,
            # immediate forceFaint for bench mons).  When off — or when the
            # game's client wouldn't understand it — fall back to the legacy
            # `force_faint` deferred-faint command.  Without the adapter gate,
            # a Gen 1/2/4/5 run with --explode-mode would emit a command those
            # clients only log, and the linked mon would never faint.
            explode = cause == "battle" and self.explode_mode and bool(
                self.adapter and self.adapter.supports_explode_mode())
            cmd_name = "force_explode" if explode else "force_faint"
            death = {"cmd": cmd_name, "key": partner_mon.key, "nickname": partner_mon.nickname or ""}
            if cause == "battle":
                # O-29: an optional, generation-neutral tag; a client that can ring a phone
                # call for it does (Gen 2 phone.lua), every other client ignores the key.
                death["phone"] = "fallen"
                death.update(self._phone_extra(player_id, player_mon, partner_mon))
            self.queued_commands[partner].append(death)
            self.queued_commands[partner].append({"cmd": "play_sound", "sound": 26})   # SE_FAILURE — the KO notification's noise (HUD convention)
            self.party_keys[partner].discard(partner_mon.key)
            log.debug(f"[PARTY] player={partner}  party_keys remove {partner_mon.key[:8]}  ({cmd_name} from {player_id})")
            log.info(f"[{player_id}] faint → {cmd_name} {partner}:{partner_mon.key}")
        entry.status = LinkStatus.DEAD
        entry.killed_at = datetime.now(UTC).isoformat()
        entry.cause = cause
        entry.killer = killer
        entry.initiating_player = player_id
        # Update MonInfo levels to death-time values so memorial shows current level.
        if player_mon:
            lv = level or self.mon_stats[player_id].get(player_mon.key, {}).get("level", 0)
            if lv:
                player_mon.level = lv
        if partner_mon:
            lv = self.mon_stats[partner].get(partner_mon.key, {}).get("level", 0)
            if lv:
                partner_mon.level = lv
        if player_mon:
            self._queue_memorialize(player_id, player_mon.key)
        if partner_mon:
            self._queue_memorialize(partner, partner_mon.key)
        self._check_game_over()
        self._save()

    def _index_entry(self, entry: LinkEntry):
        """Index both halves by key.

        The backstop for the collision check in `_check_link_violation`: that one refuses
        a colliding CAPTURE, but links also arrive from disk, from a bonus pair and from a
        rejection, and this dict is where an alias would actually take effect. Overwriting
        silently is what made the original defect invisible, so a collision that reaches
        here is reported loudly even though the write still happens -- a half-indexed link
        would be worse than an aliased one.
        """
        for pid, half in (("a", entry.a), ("b", entry.b)):
            if not half:
                continue
            prev = self._pidx[pid].get(half.key)
            if prev is not None and prev is not entry:
                log.error("KEY COLLISION: %s:%s now indexes the link in %s, displacing the one "
                          "in %s — two of one player's mons share a key and lookups by it "
                          "(faint propagation especially) will resolve to the wrong pair",
                          pid, half.key, entry.area_id, prev.area_id)
            self._pidx[pid][half.key] = entry

    @property
    def _key_index(self) -> Mapping:
        return _KeyIndexView(self._pidx)

    def entry_for(self, player_id: str, key: str) -> "LinkEntry | None":
        """The link whose `player_id` half is `key` (KEY-SCOPE: identity is per player)."""
        return self._pidx[player_id].get(key) if key else None

    def _unindex(self, player_id: str, key: str, entry: "LinkEntry | None" = None):
        """Drop player_id's index row for key (only if it names `entry`, when given)."""
        if entry is None or self._pidx[player_id].get(key) is entry:
            self._pidx[player_id].pop(key, None)

    def unindex_entry(self, entry: "LinkEntry"):
        """Drop both halves of `entry` from the per-player index (an admin unlink)."""
        for pid, half in (("a", entry.a), ("b", entry.b)):
            if half:
                self._unindex(pid, half.key, entry)

    def _queue_memorialize(self, player_id: str, key: str):
        """
        Queue a memorialize command for player_id's key.
        Cancels any stale box_mon/party_mon for the same key (they're now dead).
        """
        self.queued_commands[player_id] = [
            c for c in self.queued_commands[player_id]
            if not (c.get("cmd") in ("box_mon", "party_mon") and c.get("key") == key)
        ]
        self.queued_commands[player_id].append({"cmd": "memorialize", "key": key})
        self.pending_memorials[player_id].add(key)
        log.info(f"[{player_id}] memorialize queued for {key[:8]}")

    def _label_from_msg(self, msg: dict, key: str) -> str:
        """Return the best available display label for a mon from a capture event dict."""
        nick = msg.get("nickname", "")
        species = msg.get("species_id", 0)
        return nick or (self.adapter.species_name(species) if species else None) or key[:8]

    def _handle_memorialize_done(self, player_id: str, msg: dict):
        """
        Lua client confirmed it moved the mon to a memorial box.
        When both sides of a pair are confirmed, mark entry MEMORIAL and write memorial.json.
        """
        key = msg.get("key", "")
        if not key:
            return
        self.pending_memorials[player_id].discard(key)
        self.party_keys[player_id].discard(key)
        log.info(f"[{player_id}] memorialize_done key={key[:8]}")
        entry = self.entry_for(player_id, key)
        if not entry or entry.status != LinkStatus.DEAD:
            self._save()
            return
        a_key = entry.a.key if entry.a else None
        b_key = entry.b.key if entry.b else None
        a_done = (a_key is None) or (a_key not in self.pending_memorials["a"])
        b_done = (b_key is None) or (b_key not in self.pending_memorials["b"])
        if a_done and b_done:
            entry.status = LinkStatus.MEMORIAL
            log.info(f"pair in {entry.area_id} fully memorialized")
            self._write_memorial(entry)
        self._save()

    def _handle_memorialize_failed(self, player_id: str, msg: dict):
        """
        Lua couldn't move the mon to a memorial box (all boxes full or key not found).
        Treat as done — remove from pending so the pair can reach MEMORIAL status.
        The mon stays wherever it was; this is a best-effort operation.
        """
        key = msg.get("key", "")
        reason = msg.get("reason", "unknown")
        if not key:
            return
        self.pending_memorials[player_id].discard(key)
        self.party_keys[player_id].discard(key)              # as memorialize_done: dead is not party
        log.warning(f"[{player_id}] memorialize_failed key={key[:8]} reason={reason}")
        # Check if the pair can now be finalized despite the failure
        entry = self.entry_for(player_id, key)
        if not entry or entry.status != LinkStatus.DEAD:
            self._save()
            return
        a_key = entry.a.key if entry.a else None
        b_key = entry.b.key if entry.b else None
        a_done = (a_key is None) or (a_key not in self.pending_memorials["a"])
        b_done = (b_key is None) or (b_key not in self.pending_memorials["b"])
        if a_done and b_done:
            entry.status = LinkStatus.MEMORIAL
            log.info(f"pair in {entry.area_id} marked memorial (with failed memorialization)")
            self._write_memorial(entry)
        self._save()

    def _ingest_party_blobs(self, player_id: str, party: list) -> None:
        """
        Extract per-slot 100-byte mon blobs from a party snapshot (carried on
        every hello / tick / safe) and cache them in `self.partner_blobs`.

        Whole-party snapshot every call — no incremental diff.  The Lua side
        already gates these snapshots to TICK_INTERVAL (~0.5 s), so traffic
        is bounded.  Empty input clears the cache (treated as "partner went
        offline" by downstream gates).

        Each entry in `party` is the existing snapshot dict shape from
        `build_party_snapshot`, with one new field `blob_hex` (200 chars).
        Entries missing or malforming `blob_hex` are silently dropped —
        callers (capture/faint/sync handlers) still get the rest of the
        snapshot via the existing pathways, this is best-effort caching.
        """
        if not isinstance(party, list):
            return
        # KEY-SCOPE-5: the raw key multiset, before any entry is dropped for its blob
        self.party_key_census[player_id] = dict(Counter(
            m["key"] for m in party if isinstance(m, dict) and isinstance(m.get("key"), str) and m["key"]))
        self.prev_party_slots[player_id] = self.party_slots.get(player_id) or {}
        n = self.snapshot_no[player_id] = self.snapshot_no.get(player_id, 0) + 1
        streak = self.key_streak.setdefault(player_id, {})
        last = self.key_last_seen.setdefault(player_id, {})
        for k in self.party_key_census[player_id]:
            if last.get(k) != n - 1:
                streak[k] = n
            last[k] = n
        self.party_slots[player_id] = {m["key"]: m.get("slot") for m in party
                                       if isinstance(m, dict) and isinstance(m.get("key"), str)}
        # Per-game blob size — see GameRulesAdapter.party_blob_size. 0 means this game
        # doesn't cache blobs at all, so there is nothing to ingest.
        expected = self.adapter.party_blob_size()
        if not expected:
            self.partner_blobs[player_id] = []
            return
        accepted: list[dict] = []
        for s in party:
            if not isinstance(s, dict):
                continue
            blob_hex = s.get("blob_hex", "")
            if not isinstance(blob_hex, str) or len(blob_hex) != expected * 2:
                continue
            try:
                blob = bytes.fromhex(blob_hex)
            except ValueError:
                continue
            if len(blob) != expected:
                continue
            accepted.append({
                "slot": int(s.get("slot", len(accepted))),
                "species_id": int(s.get("species_id", 0) or 0),
                "level": int(s.get("level", 0) or 0),
                "key": str(s.get("key", "") or ""),
                "blob": blob,
                # trade sanity (_trade_sane): only when the snapshot carries it
                **({"hp": int(s["hp"])} if isinstance(s.get("hp"), int) else {}),
            })
        self.partner_blobs[player_id] = accepted

    # ── Rival Team Swap: trainer-battle detection + injection ───────────────

    def queue_rival_team_swap(self, target_player: str, trainer_id: int,
                              source: str = "auto",
                              session: str | None = None,
                              battle_id: int | None = None) -> tuple[bool, str]:
        """Queue a `replace_rival_team` command for `target_player`.

        Used by:
        - Phase 2 manual inject (source='manual') — fires from dashboard button.
        - Phase 3 auto-trigger (source='auto') — fires from trainer_battle_start.

        Battle request identity (card C5-10, docs/protocol.md): `session` is the nonce the
        CLIENT minted for this process and `battle_id` the counter of the battle it announced
        with `trainer_battle_start`; the client refuses a command that does not carry both and
        match the battle it is in. The auto path passes the pair it just received. The manual
        path passes neither and inherits the LATEST pair stored for this player (the battle the
        dashboard is looking at) -- and REFUSES when there is none (a server restart, or a
        client that never announced with an identity): sending an id-less command at a client
        that requires one is a guaranteed refusal one round trip later, and inventing an id is
        worse. `never retag`: the pair is copied into the queued command here and never updated
        afterwards, so a command always carries the identity it was created for.

        When the player's client sends no identity at all — Gen 1, Gen 2 and old Gen 3 clients —
        both fields are omitted entirely, so those clients keep byte-identical payloads and
        today's behaviour.

        Returns (ok, reason). When ok=False the caller can surface `reason`
        to the user (dashboard button needs that to explain why nothing fired).

        Gates checked in order:
        - partner has cached blobs at all (i.e. they're connected and Lua sent
          at least one party_blob_sync).
        Caller is responsible for additional gates such as the rival-ID
        membership check (which uses the adapter) and feature-flag/toggle
        state (Phase 3) — keeping THIS function strictly about queueing.
        """
        partner = _partner(target_player)
        blobs = self.partner_blobs.get(partner, [])
        if not blobs:
            return False, f"partner {partner!r} has no cached party blobs"
        if session is not None or battle_id is not None:
            # an EXPLICITLY supplied pair must be valid: only a fully omitted one may inherit
            if not _valid_battle_identity(session, battle_id):
                return False, (f"invalid battle identity supplied for {target_player!r} "
                               f"(session={session!r}, battle_id={battle_id!r})")
        elif source == "manual":
            # only the dashboard's manual inject may inherit the stored identity
            stored = self.latest_battle_requests.get(target_player)
            session, battle_id = stored[:2] if stored else (None, None)
        else:
            # AUTO echoes ONLY the identity of the announcement that triggered it (Codex C5-10
            # MAJOR 2): inheriting here would let a stale pair ride a fresh trigger.
            session, battle_id = None, None
        if (source == "manual" and not (session and battle_id)
                and self.battle_identity_clients.get(target_player)):
            return False, (f"no battle request identity for {target_player!r} — a manual rival "
                           "inject needs the client's latest trainer_battle_start (session nonce "
                           "+ battle counter); wait for the next rival battle")
        command = {
            "cmd": "replace_rival_team",
            "trainer_id": int(trainer_id) if trainer_id is not None else 0,
            "n": len(blobs),
            "blobs_hex": [b["blob"].hex() for b in blobs],
            "source": source,
        }
        if session and battle_id:
            command["session"] = session
            command["battle_id"] = battle_id
        self.queued_commands[target_player].append(command)
        log.info(
            f"[{target_player}] queued replace_rival_team "
            f"(trainer_id={trainer_id}, n={len(blobs)}, source={source}, "
            f"session={session}, battle_id={battle_id})"
        )
        return True, "queued"

    def _handle_trainer_battle_start(self, player_id: str, msg: dict):
        """
        Detect rival fights and auto-fire replace_rival_team when the per-run
        rival_team_swap flag is on AND the trainer is in the adapter's rival
        set AND the partner has cached blobs.

        Gates checked in order:
        1. Valid trainer_id (positive int).
        2. Trainer ID is in adapter.rival_trainer_ids() (RR-only for MVP;
           vanilla/AP/Emerald adapters return empty set → no-op).
        3. Per-run rival_team_swap flag (set at run creation; not a live toggle).
        4. Partner has cached blobs (queue_rival_team_swap final gate).
        """
        trainer_id = msg.get("trainer_id")
        if not isinstance(trainer_id, int) or trainer_id <= 0:
            return
        # The client's battle request id (card C5-10). Old Gen 3 clients and Gen 1/2 omit it:
        # nothing is stored and nothing is echoed, so their commands stay byte-identical.
        session = msg.get("session")
        battle_id = msg.get("battle_id")
        if _valid_battle_identity(session, battle_id):
            # stored, not merged: the tuple is the identity of the battle being announced, and a
            # queued command keeps the tuple it was created with.
            self.latest_battle_requests[player_id] = (session, battle_id, trainer_id)
        elif session is None and battle_id is None:
            # LEGACY announcement (an old client, or none at all): it proves this client does not
            # speak the protocol, so any identity stored for it is cleared rather than inherited
            # (Codex C5-10 MAJOR 2 -- a stale identity must not survive a legacy hello).
            self.latest_battle_requests.pop(player_id, None)
        else:
            # MALFORMED: rejected, never treated as legacy, never stored. The stored tuple (from
            # a previous good announcement) is left alone -- only the manual path may use it.
            log.warning(f"[{player_id}] trainer_battle_start with a malformed battle identity "
                        f"(session={session!r}, battle_id={battle_id!r}); not stored")
            session, battle_id = None, None
        rival_ids = self.adapter.rival_trainer_ids() if self.adapter else set()
        is_rival = trainer_id in rival_ids
        log.info(
            f"[{player_id}] trainer_battle_start trainer_id={trainer_id} "
            f"is_rival={is_rival} session={session} battle_id={battle_id}"
        )
        if not is_rival:
            return
        if not self.rival_team_swap:
            log.info(
                f"[{player_id}] rival team swap auto-trigger DISABLED "
                f"(trainer_id={trainer_id})"
            )
            return
        ok, reason = self.queue_rival_team_swap(
            player_id, trainer_id=trainer_id, source="auto",
            session=session, battle_id=battle_id)
        if not ok:
            log.info(
                f"[{player_id}] rival team swap auto-trigger skipped: {reason}"
            )

    def _handle_rival_team_replaced(self, player_id: str, msg: dict):
        """Readback ack from the Lua write — log mismatch loudly so the
        dashboard banner can surface a CFRU/RR drift.

        msg = {trainer_id: int, species_ids: list[int], error?: str}
        """
        trainer_id = msg.get("trainer_id", 0)
        species = msg.get("species_ids") or []
        err = str(msg.get("error", "") or "")
        if err:
            # The client couldn't perform the swap. It names WHY with a code
            # (patch_required / patch_failed / patch_timeout) -- that stays in the log line
            # below. The HUD is player-facing only, so it says the swap did not happen and
            # points at the console; without it the player fights the canned rival team all
            # run and never learns anything went wrong.
            log.warning(f"[{player_id}] rival team swap FAILED: {err}")
            self.queued_commands[player_id].append({
                "cmd": "hud_show", "text": "Rival Swap failed - see the SLink log",
                "r": 255, "g": 80, "b": 80, "frames": 600})
            return
        if not isinstance(species, list):
            log.warning(f"[{player_id}] rival_team_replaced: species_ids not a list")
            return
        log.info(
            f"[{player_id}] rival_team_replaced ack trainer_id={trainer_id} "
            f"species={species}"
        )

    def _write_memorial(self, entry: LinkEntry):
        """Append the retired pair to memorial.json via atomic rewrite."""
        os.makedirs(self._data_dir, exist_ok=True)
        try:
            with open(self._memorial_path) as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            data = {"retired_pairs": []}
        data["retired_pairs"].append({
            "area_id": entry.area_id,
            "a": asdict(entry.a) if entry.a else None,
            "b": asdict(entry.b) if entry.b else None,
            "killed_at": entry.killed_at,
            "cause": entry.cause,
            "killer": entry.killer,
            "initiating_player": entry.initiating_player,
        })
        try:
            self._atomic_write_json(self._memorial_path, data)
        except OSError as e:
            log.warning(f"[SAVE] memorial.json write failed (non-fatal): {e}")

    def _check_game_over(self):
        """
        Check if the Soul Link run is over.
        Conditions (all must be true):
        1. Both players have obtained Pokéballs (nuzlocke started)
        2. At least one real linked pair has ever existed (both a and b present)
        3. Zero alive links remain
        4. Zero pending captures that could still form new links
        5. Not already flagged
        When triggered, queues game_over command to both players.
        """
        if self.run_over:
            return
        # Gate: nuzlocke must be active for both players
        if not (self.pokeballs_obtained.get("a") and self.pokeballs_obtained.get("b")):
            return
        # Gate: run must have actually started — at least one real pair ever formed
        has_real_pair = any(
            e.a is not None and e.a.key and e.b is not None and e.b.key
            for e in self.links
        )
        if not has_real_pair:
            return
        # Any alive links?
        if any(e.status == LinkStatus.ALIVE for e in self.links):
            return
        # Any pending captures waiting to form links?
        if self.pending_captures:
            return
        # Game over
        self.run_over = True
        log.info("GAME OVER — no alive links and no pending captures remain")
        for pid in ("a", "b"):
            self.queued_commands[pid].append({"cmd": "game_over"})

    def _atomic_write_json(self, path: str, payload):
        """Preserve the state monkeypatch seam; publication/retries live in json_files."""
        return json_files.atomic_write_json(path, payload)

    def _save(self):
        if self.load_failed:
            raise RuntimeError(f"Refusing to overwrite unreadable run state: {self.load_failed}")
        os.makedirs(self._data_dir, exist_ok=True)
        payload = {
            "game_id": self.adapter.game_id,
            "rules": {
                "species_lock": self.species_lock,
                "gender_lock": self.gender_lock,
                "type_lock": self.type_lock,
                "retry_same_species": self.retry_same_species,
                "ignore_wild_faints": self.ignore_wild_faints,
                "explode_mode": self.explode_mode,
                "rival_team_swap": self.rival_team_swap,
                "overworld_presence": self.overworld_presence,
                "native_messages": self.native_messages,
                "native_sounds": self.native_sounds,
                "battle_calc": self.battle_calc,
                "pc_trade_npc": self.pc_trade_npc,
                "phone_calls": self.phone_calls,
            },
            "links": [
                {
                    "area_id": e.area_id,
                    "a":          asdict(e.a) if e.a else None,
                    "b":          asdict(e.b) if e.b else None,
                    "status":     e.status.value,
                    "encounter_a": asdict(e.encounter_a) if e.encounter_a else None,
                    "encounter_b": asdict(e.encounter_b) if e.encounter_b else None,
                    "killed_at":   e.killed_at,
                    "cause":       e.cause,
                    "killer":      e.killer,
                    "initiating_player": e.initiating_player,
                }
                for e in self.links
            ],
            "area_states": {k: v.value for k, v in self.area_states.items()},
            # Persist pending captures so the same-player no_catch guard survives restarts.
            "pending_captures": {
                area_id: {
                    player_id: asdict(mon)
                    for player_id, mon in players.items()
                }
                for area_id, players in self.pending_captures.items()
            },
            "unpaired_mons": {
                area_id: {
                    player_id: asdict(mon)
                    for player_id, mon in players.items()
                }
                for area_id, players in self.unpaired_mons.items()
            },
            "encounter_locks": {
                area_id: {
                    player_id: asdict(lock)
                    for player_id, lock in players.items()
                }
                for area_id, players in self.encounter_locks.items()
            },
            # Cached party stats per monKey (echoed in party_mon commands after box_to_party).
            "mon_stats": self.mon_stats,
            # Pokéball gate: whether each player has confirmed they can catch Pokémon.
            "pokeballs_obtained": self.pokeballs_obtained,
            # Committed ROM type (set once on first hello, static for run lifetime).
            "rom_type": self.rom_type,
            "artifact_kind": self.artifact_kind,
            "trainer_names": self.trainer_names,
            # Player identity lock: OT ID + trainer name per slot.
            "player_identity": self.player_identity,
            # Memorials awaiting Lua confirmation (re-queued on reconnect).
            "pending_memorials": {
                pid: list(keys) for pid, keys in self.pending_memorials.items()
            },
            # Partner syncs not yet handed to their client (typically an offline partner): a restart
            # must not drop them, or the pair stays split with nothing to notice. Delivered ones are
            # forgotten; their answers and the tick reconciler own them from there.
            "queued_sync": {
                pid: [c for c in cmds if c.get("cmd") in DURABLE_SYNC_COMMANDS]
                for pid, cmds in self.queued_commands.items()
            },
            "retry_areas": {
                pid: list(areas) for pid, areas in self.retry_areas.items()
            },
            "bonus_keys": {
                pid: list(keys) for pid, keys in self.bonus_keys.items()
            },
            "pending_bonus": {
                pid: list(q) for pid, q in self.pending_bonus.items()
            },
            "ambiguous_keys": self.ambiguous_keys,
            "key_migration_ledger": {pid: list(q) for pid, q in self.key_migration_ledger.items()},
            "run_over": self.run_over,
            "attempts_count": self.attempts_count,
            # BLOCKER-1: an applied-but-unsettled trade survives a restart; tokens never restart at t1
            "pending_trade": self._trade_to_json(),
            "trade_token": self._trade_token,
            "rebuild_pending": {
                pid: (
                    {
                        "started_at":         rb.get("started_at", ""),
                        "queued_keys":        list(rb.get("queued_keys", [])),
                        "queued_partner_keys": list(rb.get("queued_partner_keys", [])),
                        "restored_keys":      list(rb.get("restored_keys", set())),
                    }
                    if rb else None
                )
                for pid, rb in self.rebuild_pending.items()
            },
        }
        try:
            if self.adapter.supports_trade_recovery():
                payload["trade_recovery"] = {
                    pid: {"pending": self.trade_recovery_pending[pid], "hidden": self.party_hidden[pid],
                          "problem": self.trade_recovery_errors[pid]}
                    for pid in ("a", "b")}
                payload["trade_finals"] = {pid: list(self.trade_finals[pid].values()) for pid in ("a", "b")}
            self._atomic_write_json(self._links_path, payload)
            self.save_failed = ""
        except OSError as e:
            # NOT fatal — losing the run in memory would be worse than losing the file. But it must
            # not be silent either: this repo commonly lives in a synced folder (Google Drive),
            # which is exactly the lock _atomic_write_json's retry loop exists for. If those
            # retries are exhausted the run is no longer being persisted, and the player should
            # find that out now rather than at the next restart.
            self.save_failed = str(e)
            log.warning(f"[SAVE] links.json write failed (non-fatal): {e}")
