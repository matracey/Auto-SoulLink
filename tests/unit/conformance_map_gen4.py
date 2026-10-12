"""The Gen 4 conformance map for docs/protocol.md §9 (card G3-A, "Gen 4 protocol conformance map").

The Gen 3 map (tests/unit/conformance_map.py) records, per §9 item, WHICH evidence lane proves it.
This file records the same thing for Gen 4 with one extra axis the Gen 3 map does not have: an
explicit NA column. Gen 4 is a second foundation (lua/gen4/client.lua + lua/gen4/entry.lua over
data/games/gen4_hgss/ and data/games/gen4_hge/), so several §9 items are Gen-3-only features the
Gen 4 client deliberately does not implement (Explode Mode, Rival Team Swap, the native
prompt/trade companion). Those are marked `evidence_layer="na"` with a REASON and carry no test.

Axes:

  evidence_layer
    "transport"  stream mechanics, proven by the SHARED connector/session lane
                 (tests/unit/test_connector_fragmentation.py, test_connector_reconnect.py);
                 Gen 4 uses the same lua/connector.lua + lua/core/session.lua as Gen 3, and the
                 gen4_world harness stubs net.send, so it cannot prove framing itself.
    "world"      payload/admission logic and the semantic reducer, proven by driving the
                 PRODUCTION lua/gen4/client.lua through tests/unit/gen4_world.py (a fake NDS +
                 fake BizHawk + fake server). Every line the client sends is strict-validated at
                 send time by gen4_world.World._make_net against tests/unit/protocol_schema.py.
    "live"       measured UI / on-screen text; only the real HUD/emulator settles these. The
                 gen4_world harness injects a RECORDING hud double (gen4_world.py:470-474), so
                 anything about the real HUD (e.g. text sanitisation) is not world-observable.
    "na"         NOT APPLICABLE to Gen 4 (a Gen-3-only feature or a server-side item). `na_reason`
                 is mandatory on every na row.

  test          the id of the proving test. For "world" items it is a test function registered in
                 tests/unit/test_gen4_protocol_conformance.py::_WORLD_TESTS (the meta-test
                 requires every world item to resolve to a real world test). `test=None` on a
                 world item is the "coverage owed" signal the meta-test raises. For "transport"
                 items it names the shared lane; for "live" items it may be None (not yet written).

  open          an optional named-OPEN note: a gap that is real and tracked, not a silent pass.
                 The hge routing suppression (tests/unit/gen4_world.py:458-459, which drops the
                 "not one the server routes" problem for heartgold_hge) is the load-bearing OPEN:
                 heartgold_hge has no _ROM_TYPE_TO_GAME_ID row yet (server/adapters/__init__.py:42-87;
                 G3a adds it), so an hge hello is currently NOT routable. That must be asserted
                 explicitly in the conformance suite, never inherited silently from the harness.

Every non-na item is a claim Gen 4 must satisfy; every na item is a claim Gen 4 does NOT make,
with the reason recorded so a future Gen 4 feature can flip the row to applicable.
"""
from __future__ import annotations

from typing import NamedTuple


class Item(NamedTuple):
    id: str
    title: str
    evidence_layer: str  # "transport" | "world" | "live" | "na"
    test: str | None = None
    na_reason: str | None = None
    open: str | None = None


LAYERS = {"transport", "world", "live", "na"}

# The shared connector/session lane that proves the transport items for every foundation
# (Gen 4 uses the same lua/connector.lua + lua/core/session.lua as Gen 3).
_TRANSPORT = "shared:lua/connector.lua + lua/core/session.lua (tests/unit/test_connector_fragmentation.py, test_connector_reconnect.py)"

# Reasons reused across the Gen-3-only rows.
_NA_EXPLODE = (
    "Explode Mode is a Gen 3 / Radical Red feature. Gen4Adapter does not override "
    "supports_explode_mode(), so the base default (server/adapters/base.py:288-301) is False and "
    "the server never sends force_explode to a Gen 4 client."
)
_NA_RIVAL = (
    "Rival Team Swap is a Gen 3 / Radical Red companion-patch feature. Gen4Adapter does not "
    "override rival_trainer_ids(), so the base default (server/adapters/base.py:204) is the empty "
    "set: no Gen 4 trainer is a rival, so replace_rival_team is never queued."
)
_NA_NATIVE_TRADE = (
    "The native trade/prompt transport is a Gen 3 companion-patch feature. Gen 4 has no companion "
    "patch: lua/core/session.lua answers the prompt commands with the immediate cancel sentinel "
    "(session.lua:62,279-281) and logs+ignores apply_trade (session.lua:44-48), so there is no "
    "Gen 4 trade to resolve, block, or follow."
)
_NA_NATIVE_PROMPT = (
    "The native prompt scenes (show_choices/show_menu/choose_mon rendering a real prompt) are a "
    "Gen 3 companion-patch feature. Gen 4 has no companion patch, so these always take the cancel "
    "sentinel path (lua/core/session.lua:62,279-281) and no prompt is ever shown."
)


ITEMS: list[Item] = [
    # -- Transport (shared connector/session lane; NOT world-provable -- gen4_world stubs net.send) --
    Item("1", "one JSON object per line terminated by exactly one \\n, with event/player/seq", "transport", _TRANSPORT),
    Item("2", "seq starts at 1 and increases by exactly 1 per event, across reconnects", "transport", _TRANSPORT),
    Item("3", "nothing sent while disconnected, no buffering", "transport", _TRANSPORT),
    Item("4", "first line after connect/reconnect is hello", "transport", _TRANSPORT),
    Item("5", "tolerates a noop reply and unknown commands", "transport", _TRANSPORT),
    Item("6", "a server line > 4 MiB is discarded and the next line still parses", "transport", _TRANSPORT),
    Item("7", "reply field order is irrelevant", "transport", _TRANSPORT),

    # -- hello / admission (world) --
    Item("8", "hello carries rom_type/party/has_pokeballs/trainer_name/badges (+ §2.1 pairing fields)", "world",
         "test_world_hello_carries_the_required_fields_on_every_admitted_title",
         open="OPEN/G3a: heartgold_hge has no _ROM_TYPE_TO_GAME_ID row yet "
              "(server/adapters/__init__.py:42-87), so an hge hello is currently refused for "
              "routing. gen4_world.py:458-459 suppresses exactly that problem; the conformance "
              "suite asserts it explicitly (test_open_hge_hello_is_currently_refused_for_routing)."),
    Item("9", "every party entry has key/hp/maxHP/level/slot/species_id/nickname/blob_hex "
               "(len == 2*party_blob_size())", "na",
         na_reason="Gen 4 supplies no blob_hex. lua/gen4/client.lua:276-278 lists blob_hex among the "
                   "fields it does NOT supply, and party_wire (client.lua:282-295) emits only "
                   "key/slot/species_id/level/hp/maxHP/status_cond/moves/pp/held_item_id/active. "
                   "blob_hex feeds Rival Team Swap (a Gen-3 companion feature) and the server's "
                   "party_mon stats cache, neither of which Gen 4 uses: a Gen 4 party_mon draws "
                   "its tail from the server-supplied cmd.stats and its own decryptable save "
                   "record."),
    Item("10", "ot_id present, or derivable from party[0].key", "world",
         "test_world_ot_id_is_present_and_derivable_from_party0_key"),
    Item("11", "hello omits party contents when the save is not loaded or borrowed", "world",
         "test_world_no_hello_ever_carries_a_party_the_client_cannot_read",
         open="PARTIAL. The BORROWED half has no Gen 4 source: `party_borrowed` is a Gen-3-only driver "
              "seam (lua/core/session.lua:26; its only implementor is lua/gen3/client.lua:1066). The "
              "not-loaded half is proved as a REFUSAL rather than an empty list: hello_ready calls "
              "game_is_live, which refuses an unreadable party (lua/gen4/client.lua:502-519), so the "
              "party_wire({}) line at client.lua:521-523 is never sent."),
    Item("12", "WRONG SAVE toast renders either field spelling, no crash", "world",
         "test_world_wrong_save_toast_accepts_either_field_spelling_without_crashing"),
    Item("13", "resolved_areas (incl. empty list) + config booleans are applied", "world",
         "test_world_resolved_areas_and_config_are_applied"),

    # -- tick / snapshots (world) --
    Item("14", "tick is periodic (every 30 frames) and carries has_pokeballs/area_id/loc_name/in_battle/badges/"
               "trainer_name; party when valid; enemy_party ([] outside battle)", "world",
         "test_world_tick_is_periodic_every_30_frames_and_carries_the_required_fields"),
    Item("15", "first tick after a wild battle: in_battle=true, is_trainer_battle=false, non-empty area_id, "
               "enemy_party[0].species_id>0", "world",
         "test_world_wild_battle_tick_has_in_battle_true_and_a_named_foe"),
    Item("16", "trainer-battle tick has is_trainer_battle=true and trainer_id>0, or opponent_name/class", "world",
         "test_world_trainer_battle_tick_names_the_battle_kind",
         open="PARTIAL: Gen 4 supplies is_trainer_battle but NO trainer_id/opponent_* "
              "(lua/gen4/client.lua:277 lists trainer_id/opponent_* as not supplied), so only the "
              "is_trainer_battle half of the item is satisfiable until a trainer-identity source "
              "is wired. The test asserts the supplied half and the documented absence."),
    Item("17", "status_cond uses the §8-5 bit layout; stat_stages is a 7-list; pp_ups/pp_bonuses present "
                "when moves are", "na",
         na_reason="Gen 4 supplies status_cond and pp but NOT stat_stages and NOT pp_bonuses: "
                   "lua/gen4/client.lua:276-278 lists both as unsupplied (pp_bonuses is an int on "
                   "the wire; Gen 4 holds 4 bytes per move), and party_wire (client.lua:282-295) "
                   "emits status_cond/moves/pp only. No Gen 4 battle-stat source is wired, so the "
                   "stat_stages 7-list and the pp_bonuses parity half of the item are unsatisfiable "
                   "today. The supplied halves are asserted under items 8/14/18."),
    Item("18", "pc_boxes entries have 0-based box/slot; client memorial box index == adapter.memorial_box_index",
         "world", "test_world_pc_boxes_are_zero_based_and_the_memorial_box_matches_the_adapter"),
    Item("19", "safe is sent on the first overworld frame after a battle", "world",
         "test_world_safe_is_sent_exactly_once_per_battle_end"),

    # -- encounter events (world) --
    Item("20", "area_enter{area_id, loc_name} fires on map change; area_id is a known id or \"\"", "world",
         "test_world_area_enter_fires_once_per_map_change_and_never_repeats_one",
         open="PARTIAL: the `area_id == \"\"` half is unreachable in Gen 4. step_area drops an empty area "
              "BEFORE arming area_enter (lua/gen4/poll_events.lua:153), so an unmapped map announces "
              "nothing at all rather than an empty id."),
    Item("21", "capture has key/area_id, species_id>0, level>0; in_box/gift/is_egg flags", "world",
         "test_world_capture_carries_key_area_species_level_and_the_acquisition_flags"),
    Item("22", "pre-pokeballs capture uses area_id=\"intro\"", "na",
         na_reason="RETIRED in docs/protocol.md §9 item 22: the pre-pokeballs capture no longer forces "
                   "area_id=\"intro\"; it carries area_now()'s real result (the reference-client "
                   "behaviour is historical). No current client, Gen 4 included, has this obligation."),
    Item("23", "no_catch fires once per unresolved wild battle, never for gifts, never twice, never after a "
               "capture in that battle", "world",
         "test_world_no_catch_ball_gate_once_per_area_and_never_after_capture"),
    Item("24", "unresolve_area{area_id} re-arms no_catch / encounter HUD", "world",
         "test_world_unresolve_area_rearms_no_catch_only_for_the_named_area",
         open="MODEL: no_catch rearming/named-area isolation are wire-proven by the registered test; "
              "test_gen4_hud_parity.py::test_new_encounter_on_later_debounced_area_entry_and_unresolved_reentry "
              "proves the shared HUD's exact banner tuple, resolved-area suppression and unresolve rearming "
              "on HG/SS/hge. Gen4 client.lua's area_enter delivery applies the accepted eligibility/gift "
              "guards. PHYSICAL rendering/expiry is still unverified; there is no missing producer GAP."),
    Item("25", "faint fires once per real HP>0->0 transition and NOT for client-zeroed HP", "world",
         "test_world_faint_fires_once_for_a_real_transition_and_never_for_a_commanded_zero"),
    Item("26", "whiteout fires exactly once when every previously-alive party mon is at 0 HP after a real faint",
         "world",
         "test_world_whiteout_fires_once_per_losing_battle_and_never_twice_for_one",
         open="PARTIAL: Gen 4 latches whiteout on the battle LOSE outcome at battle end "
              "(lua/gen4/poll_events.lua:168-173; the out-of-battle all-fainted path at :425-435 is the "
              "secondary one), NOT on an all-zero party as the Gen 3 clause words it. The test proves "
              "the count the item is really about: once per loss, and never a second time for one."),

    # -- party/box sync (world) --
    Item("27", "party_to_box / box_to_party fire for real, non-commanded moves", "world",
         "test_world_party_to_box_and_box_to_party_fire_for_a_player_driven_move"),
    Item("28", "box_mon: deposit when safe, stats_cache first, refuse last party mon, box_mon_failed on failure",
         "world", "test_world_box_mon_deposits_when_safe_and_sends_stats_cache_first"),
    Item("29", "party_mon: withdraw when safe, exactly one sync_retrieve_done/failed; a present mon acked done",
         "world", "test_world_party_mon_withdraws_when_safe_and_acks_exactly_once"),
    Item("30", "memorialize: move to the memorial box when safe, exactly one memorialize_done/failed", "world",
         "test_world_memorialize_moves_to_the_memorial_box_and_acks_exactly_once"),
    Item("31", "deferred commands run FIFO, at most one/frame, only in the safe state; opposing box/party cancel; "
               "duplicate memorialize deduped", "world",
         "test_world_deferred_commands_run_one_per_frame_in_order_and_dedupe"),
    Item("32", "commands referencing an unknown key are silent no-ops that do not crash", "world",
         "test_world_commands_for_an_unknown_key_are_silent_no_ops"),

    # -- deaths (world) --
    Item("33", "force_faint{key} on a benched/out-of-battle mon writes HP=0 without emitting a faint", "world",
         "test_world_force_faint_on_a_bench_mon_zeroes_hp_and_is_never_echoed_as_a_faint"),
    Item("34", "force_explode handled at least as force_faint; gated by adapter capability", "na", na_reason=_NA_EXPLODE),
    Item("35", "game_over sets a persistent HUD state and does not stop ticks", "world",
         "test_world_game_over_sets_persistent_hud_state_and_ticks_keep_running"),

    # -- keys (world) --
    Item("36", "keys stable across box<->party moves, reconnects, nickname/item changes", "world",
         "test_world_keys_survive_a_box_round_trip_a_held_item_change_and_a_reconnect",
         open="PARTIAL: the held-item half is proved (the write is read back as held_item_id 42 on the "
              "next tick). The NICKNAME half is not exercised: the PK4 nickname lives inside the "
              "shuffled block, not at a plain offset (server/adapters/gen4_codec.py:313), so a test "
              "cannot poke one without re-deriving the permutation."),
    Item("37", "a same-mon key change reported via key_change, with no capture/party_to_box/box_to_party",
         "world", "test_world_an_npc_trade_is_one_key_change_with_no_spurious_move_events"),
    Item("38", "keys come from the mon's own record (per-player scope; a pair's halves may share one)", "world",
         "test_world_the_two_halves_of_a_link_produce_distinct_keys"),
    Item("38a", "every key_change is answered in the same reply by exactly one key_change_ack/rejected", "world",
         "test_world_one_answer_settles_a_key_change_and_the_alias_follows_that_answer",
         open="The SERVER half ('answered in the same reply') is not world-observable: gen4_world has no "
              "server. What is proved is the client half the item names -- the client raises exactly ONE "
              "key_change, exactly one answer settles its alias, and that answer decides whether a later "
              "command under the old key resolves the changed record (ack: no; rejected: yes)."),
    Item("38b", "a key_change re-sent after a reconnect is idempotent; a rejected one mutates nothing", "world",
         "test_world_a_replayed_key_change_answer_is_idempotent_and_writes_nothing",
         open="The SERVER half (migrated:false, presentation caches) is not world-observable: gen4_world "
              "has no server. Proved here: a replayed answer re-sends nothing and writes nothing, and a "
              "rejected change moves zero bytes on the cartridge and is not re-sent without a newer "
              "complete census."),

    # -- prompts / trade (NA: Gen 3 native companion) --
    Item("39", "show_choices -> one menu_result, token echoed, cancel code when unshowable", "na",
         na_reason=_NA_NATIVE_PROMPT),
    Item("40", "two prompts are never in flight at once; a second trade_request is not sent while pending", "na",
         na_reason=_NA_NATIVE_PROMPT),
    Item("41", "apply_trade resolves exactly once (trade_done with readback, or uncertain:true)", "na",
         na_reason=_NA_NATIVE_TRADE),
    Item("42", "after a trade: no key_change/capture/party_to_box for the traded keys; queued sync discarded", "na",
         na_reason=_NA_NATIVE_TRADE),
    Item("43", "during apply_trade the client does not execute box_mon/party_mon/memorialize", "na",
         na_reason=_NA_NATIVE_TRADE),

    # -- misc --
    Item("44", "trainer_battle_start{trainer_id>0} fires once per trainer battle, never wild/borrowed", "na",
         na_reason="Gen 4 emits no trainer_battle_start. lua/gen4/client.lua reports a trainer "
                   "battle on the tick via is_trainer_battle (client.lua:537) and lists "
                   "trainer_id/opponent_* as not supplied (client.lua:277), so there is no "
                   "trainer identity to announce; Rival Team Swap (the consumer of this event) is "
                   "also Gen-3-only."),
    Item("45", "replace_rival_team MUST produce exactly one rival_team_replaced", "na", na_reason=_NA_RIVAL),
    Item("45a", "replace_rival_team echoes the announcing battle_id; missing/mismatched -> stale_battle_id", "na",
         na_reason=_NA_RIVAL),
    Item("46", "status{badges:int 0-8} is a count, not a bitmask", "na",
         na_reason="status{badges} is a server/status-page concern (state.py), not a client-emitted "
                   "line in the Gen 4 protocol path; Gen 4's badge fields are on hello/tick."),
    Item("47", "on-screen text from msgbox/gui_prompt/hud_show passes through the client's text sanitiser", "live",
         na_reason=None,
         open="Not world-observable: gen4_world injects a RECORDING hud double "
              "(gen4_world.py:470-474), so the real hud.lua sanitiser (lua/hud.lua:69,297-298,323-324) "
              "is not exercised in-world. Needs the live lane."),
]

BY_ID = {item.id: item for item in ITEMS}

# --- self-consistency invariants (run at import; cheap and catch a bad edit immediately) ---

assert len(BY_ID) == len(ITEMS), "duplicate id in ITEMS"
assert all(item.evidence_layer in LAYERS for item in ITEMS), "bad evidence_layer"

# every na row carries a reason; no non-na row carries one
assert all(item.na_reason for item in ITEMS if item.evidence_layer == "na"), "an na item has no na_reason"
assert all(not item.na_reason for item in ITEMS if item.evidence_layer != "na"), \
    "a non-na item carries an na_reason"

# every world item names a test id (it may be None = "coverage owed"; the meta-test raises on None)
assert all(item.test is None or isinstance(item.test, str) for item in ITEMS if item.evidence_layer == "world")
