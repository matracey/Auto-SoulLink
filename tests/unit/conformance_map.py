"""Item-level evidence map for docs/protocol.md §9 (the 47-item conformance checklist).

One entry per checklist item (1-47, plus 38a/38b since the doc splits 38 into two rows).
`evidence_layer` records which lane ultimately proves the item, per PLAN §5.6:

  * "transport" (items 1-7): stream mechanics -- production connector tests
    (test_connector_fragmentation.py / test_connector_reconnect.py).
  * "world" (items 8-38 incl. 38a/38b): payload/admission logic, the semantic reducer, and
    the deaths/keys rules -- driven by a lupa fake-server harness against injected signals.
  * "live" (items 39-47): measured UI/input, post-scene evidence, or a rival-swap readback --
    only a running emulator can settle these.

Golden wire transcripts (tests/fixtures/gen3/wire/*.jsonl, produced by card C1-3) are
*characterization* evidence layered on top of "world" items where the wire shape is fully
visible from the transcript alone -- never a replacement evidence_layer of their own (PLAN
§5.6: "never a complete oracle"). `transcript_check` names the checker function in
test_protocol_conformance.py that applies to a transcript, or None if the item cannot be
judged from a transcript (e.g. it needs RAM state, not just wire lines).
"""
from __future__ import annotations

from typing import NamedTuple


class Item(NamedTuple):
    id: str
    title: str
    evidence_layer: str  # "transport" | "world" | "transcript" | "live"
    transcript_check: str | None = None
    disagreement: str | None = None
    resolution: str | None = None


LAYERS = {"transport", "world", "transcript", "live"}

ITEMS: list[Item] = [
    # -- Transport (connector tests) --
    Item("1", "one JSON object per line, envelope fields", "transport", "check_line_shape",
         disagreement="old client's hand JSON encoder emits {} for an empty Lua table "
                       "(tick.enemy_party/tick.pc_boxes) instead of []; harmless, server "
                       "treats {} as falsy-empty (server.py:1858-1860 dict iteration is a "
                       "no-op, :1876-1877 `bs.get(\"enemy_party\") or []`)",
         resolution="new client always sends [] for an empty list, never {}"),
    Item("2", "seq starts at 1, +1 per event across reconnects", "transport", "check_seq_monotonic"),
    Item("3", "nothing sent while disconnected, no buffering", "transport"),
    Item("4", "first line after connect/reconnect is hello", "transport", "check_hello_first"),
    Item("5", "tolerates noop and unknown commands", "transport"),
    Item("6", "a >4 MiB line is discarded, next line still parses", "transport"),
    Item("7", "reply field order is irrelevant", "transport"),

    # -- hello / admission (world) --
    Item("8", "hello carries rom_type/party/has_pokeballs/trainer_name/badges", "world",
         "check_hello_shape"),
    Item("9", "every party entry has key/hp/.../blob_hex with correct length", "world",
         "check_hello_shape"),
    Item("10", "ot_id present, or derivable from party[0].key", "world"),
    Item("11", "hello omits party when save not loaded or borrowed", "world"),
    Item("12", "WRONG SAVE toast renders either field spelling, no crash", "world",
         disagreement="WRONG SAVE toast accepts both field spellings (A1)",
         resolution="accept both color/duration and r,g,b,frames"),
    Item("13", "resolved_areas + config booleans applied", "world"),

    # -- tick / snapshots (world reducer) --
    # The 30-frame period is proven live (a transcript's `t` is capture order, not frames --
    # see tests/fixtures/gen3/wire/README.md); the transcript checker below covers only the
    # field-shape half of this item.
    Item("14", "tick is periodic (Gen 3: every 30 frames) with required fields", "world",
         "check_tick_shape",
         disagreement="same {} vs [] encoder bug as item 1, on tick's enemy_party/pc_boxes "
                       "when the Lua table is empty (12 of 12 old-client transcripts)",
         resolution="new client always sends [] for an empty list, never {}"),
    Item("15", "first tick of a wild battle has in_battle/enemy_party shape", "world",
         "check_battle_tick_shape"),
    Item("16", "trainer battle tick has is_trainer_battle/trainer_id or opponent fields",
         "world", "check_battle_tick_shape"),
    Item("17", "status_cond bit layout, stat_stages 7-list, pp fields", "world"),
    Item("18", "pc_boxes entries 0-based, memorial_box_index matches adapter", "world"),
    Item("19", "safe sent on first overworld frame after battle", "world",
         disagreement="`safe` stays field-less (A3)",
         resolution="safe's tick-shaped fields are optional; Gen 3 sends {event:\"safe\"} only"),

    # -- encounter events (world reducer) --
    Item("20", "area_enter fires on map change with known/empty area_id", "world"),
    Item("21", "capture has key/area_id/species_id/level, in_box/gift/is_egg flags", "world",
         "check_event_shapes_once_only"),
    Item("22", "pre-pokeballs capture uses area_id=\"intro\"", "world"),
    Item("23", "no_catch fires once per unresolved battle, never for gifts/twice", "world",
         "check_event_shapes_once_only"),
    Item("24", "unresolve_area re-arms no_catch/encounter HUD", "world"),
    Item("25", "faint fires once per real HP>0->0 transition, not on forced zeroing",
         "world", "check_event_shapes_once_only"),
    Item("26", "whiteout fires exactly once when the whole party is at 0 HP", "world",
         "check_event_shapes_once_only"),

    # -- party/box sync (world reducer) --
    Item("27", "party_to_box / box_to_party fire for real, non-commanded moves", "world",
         "check_event_shapes_once_only"),
    Item("28", "box_mon: deposit when safe, stats_cache first, refuse-last-mon, "
               "box_mon_failed on failure", "world", "check_keyed_replies",
         disagreement="box_mon_failed never sent by the old client -- new client sends it (A2)"),
    Item("29", "party_mon: withdraw when safe, exactly one sync_retrieve_done/failed", "world",
         "check_keyed_replies"),
    Item("30", "memorialize: move when safe, exactly one memorialize_done/failed", "world",
         "check_keyed_replies"),
    Item("31", "deferred commands run FIFO, one/frame, safe-state only, cancel/dedupe rules",
         "world",
         resolution="FIFO cancel/dedupe rules unchanged; adds retry-at-tail only for "
                     "party_full / last_party_mon refusals"),
    Item("32", "commands on an unknown key are silent no-ops", "world"),

    # -- deaths (world) --
    Item("33", "force_faint on benched vs active mon, no faint event emitted", "world",
         resolution="benched force_faint deferred to the checkpoint (as Gen 1 does, "
                     "lua/gen1/client.lua:667-699), landing within one checkpoint frame in "
                     "the overworld; item 33 is rewritten in P6"),
    Item("34", "force_explode handled at least as force_faint; gated by adapter capability",
         "world"),
    Item("35", "game_over sets persistent HUD state, ticks keep running", "world"),

    # -- keys (world) --
    Item("36", "keys stable across box/party moves, reconnects, nickname/item changes", "world"),
    Item("37", "same-mon key change reported via key_change, no spurious capture/move events",
         "world"),
    Item("38", "keys are scoped per player; a pair's halves may share a key", "world"),
    Item("38a", "every key_change is answered by exactly one ack/rejected in the same reply",
         "world"),
    Item("38b", "a re-sent key_change after reconnect is idempotent, a rejected one mutates "
                "nothing", "world"),

    # -- prompts / trade (live) --
    Item("39", "show_choices -> one menu_result, token echoed, cancel code if unshowable",
         "live", "check_token_echo"),
    Item("40", "show_menu -> one menu_result with yes=1/no=0", "live", "check_token_echo"),
    Item("41", "choose_mon -> one mon_chosen 0-5 or 7, only one prompt/trade in flight", "live",
         "check_token_echo"),
    Item("42", "apply_trade -> exactly one trade_done with slot-readback new_key", "live"),
    Item("43", "no key_change/capture/party_to_box for traded keys; queued sync discarded",
         "live"),

    # -- misc (live) --
    Item("44", "no box_mon/party_mon/memorialize executes during apply_trade", "live"),
    Item("45", "trainer_battle_start fires once per trainer battle, not wild/borrowed", "live",
         "check_rival_team_replaced"),
    Item("45a", "replace_rival_team echoes the announcing battle_id; a missing/mismatched id is "
                "refused with stale_battle_id and nothing written", "world",
         "check_battle_id_echo",
         disagreement="the shipped Gen 3 client and every Gen 1/Gen 2 client send no battle_id; "
                       "before card C5-10 the server had no id to echo",
         resolution="the field is optional on the wire (old clients unchanged) and required by "
                    "the new Gen 3 client's own guard"),
    Item("46", "replace_rival_team always produces one rival_team_replaced", "live",
         "check_rival_team_replaced"),
    Item("47", "status.badges is a count, not a bitmask", "live", "check_status_badges"),
]

BY_ID = {item.id: item for item in ITEMS}

assert len(BY_ID) == len(ITEMS), "duplicate id in ITEMS"
assert all(item.evidence_layer in LAYERS for item in ITEMS)
