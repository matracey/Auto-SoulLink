"""Side-effect-free status payloads for the Manager's no-run response."""


def empty_status_payload() -> dict:
    """Return fresh containers matching the run server's empty status schema.

    There is no game selected, so the badge catalogue is empty. A contract test
    compares the remaining nested keys and JSON types with the run serializer.
    """
    return {
        "save_failed": "",
        "load_failed": "",
        "dispatch_errors": {"a": 0, "b": 0},
        "players": {
            pid: {
                "connected": False,
                "rom_type": "?",
                "last_event": "—",
                "last_seen": "—",
                "last_seen_age": None,
                "last_seen_label": "—",
                "stale": False,
                "nuzlocke_active": False,
                "current_area": "",
                "current_area_id": "",
                "current_area_display": "",
                "ball_count": 0,
                "badges": 0,
                "kanto_badges": 0,
                "trainer_name": "",
                "pc_boxes": [],
                "party_keys": [],
                "party_details": {},
                "queued": 0,
                "battle_state": {
                    "in_battle": False,
                    "is_trainer_battle": False,
                    "enemy_party": [],
                    "trainer_id": 0,
                    "opponent_name": "",
                    "opponent_class": "",
                    "is_doubles": False,
                    "calc_preview": None,
                },
                "identity_error": "",
                "awaiting_save": False,
                "admission": "admitted",
                "admission_reason": "",
                # No cartridge, so no capabilities: every flag False, every list empty.
                # The keys mirror server.ui_capabilities.ui_capabilities.
                "capabilities": {
                    "game_id": "", "abilities": False, "explode_mode": False,
                    "info_panel": False, "info_panel_width": 0, "stat_stage_labels": [],
                    "mons_per_box": 0, "memorial_box_index": 0, "party_blob_size": 0,
                    "badges": [],
                },
                # What the cartridge reported at hello (panel, sound path): nothing yet.
                "companion": {"panel": None, "sfx": None},
                "encounter_table": None,
                "trainer_panel_html": "",
            }
            for pid in ("a", "b")
        },
        "links": [],
        "area_states": {},
        "pending_captures": {},
        "encounter_locks": {},
        "rules": {
            "species_lock": False,
            "gender_lock": False,
            "type_lock": False,
            "retry_same_species": True,
            "explode_mode": False,
            "rival_team_swap": False,
            "overworld_presence": False,
            "native_messages": False,
            "native_sounds": False,
            "battle_calc": True,
            "pc_trade_npc": True,
            "phone_calls": True,
        },
        "recent_events": [],
        "killfeed": [],
        "run_over": False,
        "attempts_count": 0,
        "bonus_keys": {"a": [], "b": []},
        "pending_bonus": {"a": [], "b": []},
        "faint_repair_stalled": {"a": [], "b": []},
        "ambiguous_keys": {"a": {}, "b": {}},
        "trade_problem": None,
        "trade_held": [],
        "trade_last": None,
        "badge_slugs": [],
    }
