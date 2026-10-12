"""The Manager must not tell a Gen 1 player that a feature needs a ROM patch it doesn't.

Explode Mode and Rival Swap once sat under a heading that said "These need the companion
ROM patch applied to BOTH players' games". That is true on Gen 3, where gEnemyParty is
encrypted and checksummed, and false on Gen 1, where the enemy party is plaintext at a
fixed address and the move choice is a plain RAM write — both are live-tested that way by
the `rivalswap` and `explode_g1` duo scenarios.

The New-run form now names the game family up front and greys what that family cannot
honour, with the reason on the option. That knowledge is one table, `OPTION_SUPPORT`, read
through `option_support()`; these tests pin that its answers stay accurate.
"""
from __future__ import annotations

import pytest

from server.manager import GAMES, OPTIONS, new_run_form, option_support

GEN1 = ["red", "blue"]
RR = ["firered_rr", "firered_rr"]


@pytest.mark.parametrize("key", ["explode_mode", "rival_team_swap"])
def test_the_no_patch_generations_are_allowed_and_say_so(key):
    """These two work on an unmodified Gen 1 cartridge and the form has to say so."""
    s = option_support(key, GEN1)
    assert s["ok"], s
    assert "no patch" in s["why"].lower(), s


@pytest.mark.parametrize("key", ["battle_calc"])
def test_the_radical_red_only_features_are_greyed_elsewhere(key):
    """The other half of the same honesty: a Gen 1 player switching these on gets nothing,
    so they cannot be switched on."""
    assert not option_support(key, GEN1)["ok"]
    assert option_support(key, RR)["ok"]


def test_rr_trade_npc_is_available_with_the_durable_delta():
    """RR-DURABLE: the witness UPS trades; only an old-UPS client is refused, by the server."""
    assert option_support("pc_trade_npc", RR)["ok"] is True


def test_overworld_presence_is_greyed_everywhere_while_deferred():
    """The peer ghost is deferred post-RC; presence ON would also disable RR's trade NPC."""
    for pair in (GEN1, RR):
        assert not option_support("overworld_presence", pair)["ok"]


def test_the_pc_trade_npc_row_says_gen1_trades_at_the_receptionist():
    """Greyed is right (only the Gen 3 client reads the switch), "Radical Red only" was
    not: Gen 1 (and Gen 2) trade at the Cable Club receptionist, always. The row says so
    and shows as on, so it does not read as "no trade NPC here"."""
    for pair in (GEN1, ["purered", "pureblue"], ["crystal", "gold"]):
        s = option_support("pc_trade_npc", pair)
        assert not s["ok"] and s["always"] is True and "receptionist" in s["why"], s
    assert "Radical Red only" not in option_support("pc_trade_npc", GEN1)["why"]


def test_native_sounds_is_a_gen1_feature_now():
    """The Red/Blue companion patch and the pureRGB overlay both play notifications on the
    main thread (slink.asm SlinkSfxService); Yellow has no patch at all."""
    assert option_support("native_sounds", GEN1)["ok"]
    assert option_support("native_sounds", RR)["ok"]
    assert option_support("native_sounds", ["purered", "pureblue"])["ok"]
    # Yellow shares the family and has no patch: the toggle is allowed and the cartridge
    # decides at hello (`sfx` capability), exactly as the panel does. The label says so.
    assert "unpatched" in dict(OPTIONS)["native_sounds"][1].lower()


def test_gen2_companion_features_are_allowed():
    """Gen 2 shipped the companion patch (owner signs G4, 2026-09-26): Explode Mode, Rival
    Swap and Native Sounds are allowed like Gen 1's, on the same patched-mailbox basis."""
    GEN2 = ["crystal", "gold"]
    for key in ("explode_mode", "rival_team_swap", "native_sounds"):
        assert option_support(key, GEN2)["ok"], key
    # No calc, same reasoning as Gen 1/pureRGB: the calculator is pinned to modern mechanics.
    assert not option_support("battle_calc", GEN2)["ok"]


def test_the_gender_clause_cannot_be_chosen_on_gen1():
    """Gen 1 has no gender at all, so the clause is not merely unlikely to fire — it
    cannot. Leaving it silently inert is how a player concludes the rules are broken."""
    s = option_support("gender_lock", GEN1)
    assert not s["ok"] and "never fire" in s["why"], s
    assert option_support("gender_lock", RR)["ok"]


def test_a_mixed_pair_takes_the_stricter_answer():
    """A soul link is symmetric: if either cartridge cannot honour an option, the run
    cannot."""
    assert not option_support("battle_calc", ["firered_rr", "red"])["ok"]


def test_an_unnamed_game_forbids_nothing():
    """Detect-on-connect: nothing is known to be impossible yet."""
    assert all(option_support(k, [""])["ok"] for k in OPTIONS)


def test_the_form_table_covers_every_family_and_option():
    form = new_run_form()
    assert set(form["support"]) == {k for k, _, _ in GAMES}
    for family in form["support"].values():
        assert set(family) == set(OPTIONS)
        assert all({"ok", "why"} <= set(v) for v in family.values())


def test_native_messages_is_disabled_for_every_game(tmp_path):
    """Native text is disabled for the Gen 3 RC (owner 2026-09-23, docs/gen3/TODO.md):
    greyed on Radical Red too, and the server ignores every way of turning it on."""
    from server.state import SoulLinkState
    s = option_support("native_messages", RR)
    assert not s["ok"] and "not available yet" in s["why"].lower(), s
    assert SoulLinkState(data_dir=str(tmp_path), native_messages=True).native_messages is False


def test_the_archipelago_games_are_not_listed():
    """Owner 2026-10-02: no SLink client supports Archipelago yet (Gen 1 has no AP profile, the Gen 3
    launcher refuses a header-only build), so the Manager lists no AP game and the New-run form has
    no chip for one. Every listed game is one a run can be created for."""
    from server.manager import GAME_FAMILY, GAMES, new_run_form
    keys = {k for k, _, _ in GAMES}
    assert not {"gen1_ap", "gen3_ap"} & keys and "gen1_ap" not in GAME_FAMILY
    members = {m for _, _, ms in GAMES for m in ms}
    assert not {m for m in members if m.endswith("_ap")}
    from server.manager import UNADMITTED_GAMES
    assert not UNADMITTED_GAMES, "no game is listed-but-not-admitted now; the seam stays for the next one"
    assert all(not g["unadmitted"] and "Archipelago" not in g["label"] for g in new_run_form()["games"])


def test_gen4_and_gen5_are_not_offered_in_the_manager():
    """Owner 2026-09-23: Gen 4/5 never ran on a real game; their code stays (tag
    archive/gen4-gen5) but the New-run form must not list them at all."""
    from server.manager import new_run_form
    keys = {g["key"] for g in new_run_form()["games"]}
    assert not {k for k in keys if k.startswith(("gen4", "gen5"))}


def test_phone_calls_is_a_gen2_checkbox_default_on():
    """Owner 2026-10-02: its own Native UI row, offered only where a Pokégear exists, on unless switched off."""
    from server import manager
    assert "phone_calls" in dict(manager.OPTION_GROUPS)["Native UI"]
    support = manager.OPTION_SUPPORT["phone_calls"]
    assert support["all"] is False and support["gen2_gsc"]["ok"] is True
    assert ("phone_calls", "--no-phone-calls", True) in manager.RUN_FLAGS


def test_the_new_run_form_seeds_every_default_on_option_checked():
    """A default-ON run flag must render checked on a fresh New-run form (review cx-158f3337 F1: phone_calls
    rendered unchecked while the run was ON, so the box read the opposite of what the run did)."""
    import re
    from pathlib import Path

    from server import manager
    html = (Path(__file__).resolve().parents[2] / "server/templates/manager.html").read_text(encoding="utf-8")
    seed = re.search(r"draft: \{ name: '', game: '', opts: \{([^}]*)\}", html)[1]
    seeded = {k for k, v in re.findall(r"(\w+): (true|false)", seed) if v == "true"}
    want = {key for key, _flag, default in manager.RUN_FLAGS if default is True}
    assert seeded == want


@pytest.mark.parametrize("key", ["explode_mode", "rival_team_swap"], ids=["explode", "rival"])
def test_polished_battle_options_are_selectable_with_the_companion(key):
    support = option_support(key, ["polished_crystal", "polished_crystal"])
    assert support["ok"] is True
    assert "companion" in support["why"].lower()
    assert new_run_form()["support"]["gen2_polished"][key] == support



def test_polished_phone_refusal_names_the_missing_call_transport():
    row = option_support("phone_calls", ["polished_crystal"])
    assert row["ok"] is False
    assert "call transport" in row["why"]
    assert "not written" not in row["why"]
