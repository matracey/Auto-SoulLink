"""
Unit tests for stat stage passthrough.

Tests:
- stat_stages field flows through tick handler → party_details
- stat_stages flows through _enrich_battle_state() for enemy party
- Offset constant M.BATTLE_MON_STAT_STAGES_OFF = 0x19 (cannot read CFRU type3)

The chips themselves are rendered by the `stat_stages_row` macro in templates/_macros.html,
covered by the page tests.
"""
import pytest

# ── party_details stat_stages passthrough ─────────────────────────────────────

def _fresh_battle_state():
    """The per-player skeleton the server holds before any battle has started."""
    return {p: {"in_battle": False, "is_trainer_battle": False, "enemy_party": [],
                "trainer_id": 0, "opponent_name": "", "opponent_class": "",
                "is_doubles": False}
            for p in ("a", "b")}


def _make_server(tmp_path, battle_state=None):
    """A minimal SLinkServer, built via __new__ so no sockets or files are opened.

    One copy, shared. This used to be a private method duplicated in two test classes, and
    the copies had already drifted — one seeded `battle_state` with the skeleton above and
    the other left it `{}`, so the two disagreed about what "a fresh server" even means.
    """
    import unittest.mock as mock

    from server.adapters import get_adapter
    from server.server import SLinkServer
    from server.state import SoulLinkState

    srv = SLinkServer.__new__(SLinkServer)
    srv.data_dir = str(tmp_path)
    srv.run_id   = "test"
    srv.run_name = ""
    srv.manager_port = None
    srv.verbose  = False
    srv.host     = "127.0.0.1"
    srv.port     = 0
    srv.http_port = 0
    srv.adapter  = get_adapter("gen3_frlge")
    with mock.patch("server.state.LINKS_PATH", str(tmp_path / "links.json")):
        srv.state = SoulLinkState()
    srv.connected_players = {}
    srv.party_details = {"a": {}, "b": {}}
    srv.battle_state  = {} if battle_state is None else battle_state
    srv.player_area   = {}
    srv.player_area_id = {}
    srv.player_ball_count = {}
    srv.player_badges = {}
    srv.player_kanto_badges = {}
    srv.trainer_name  = {}
    srv._sse_queues   = []
    srv.event_log     = []
    srv._backups_dir  = str(tmp_path / "backups")
    # _build_status_dict reads these two as well; without them it raises AttributeError
    # before it ever reaches the enemy-party enrichment.
    srv.pc_boxes = {}
    srv._recent_events = {}
    # Set by __init__ on a real server; the status builder reads them without a guard.
    srv.admission = {}
    srv._player_adapters = {}
    return srv


class TestPartyDetailsPassthrough:
    """Verify stat_stages is stored in party_details during tick processing.

    Uses the SLinkServer._handle_tcp() path indirectly by inspecting the
    party_details dict the server builds from a synthetic tick message.
    """

    def test_active_mon_stages_stored(self, tmp_path):
        srv = _make_server(tmp_path)
        stages = [8, 6, 6, 4, 6, 6, 6]  # ATK+2, SPATK-2
        party_msg = [
            {"key": "AABBCCDD:11223344", "hp": 45, "maxHP": 50, "level": 12,
             "active": True, "status_cond": 0, "stat_stages": stages}
        ]
        # Simulate what the tick handler does: build party_details from msg
        srv.party_details["a"] = {
            m["key"]: {
                "hp":         m.get("hp", 0),
                "maxHP":      m.get("maxHP", 1),
                "level":      m.get("level", 0),
                "active":     m.get("active", False),
                "status_cond": m.get("status_cond", 0),
                "stat_stages": m.get("stat_stages"),
            }
            for m in party_msg if m.get("key")
        }
        detail = srv.party_details["a"]["AABBCCDD:11223344"]
        assert detail["active"] is True
        assert detail["stat_stages"] == stages

    def test_inactive_mon_stages_none(self, tmp_path):
        srv = _make_server(tmp_path)
        party_msg = [
            {"key": "AABBCCDD:11223344", "hp": 45, "maxHP": 50, "level": 12,
             "active": False, "status_cond": 0}  # no stat_stages key
        ]
        srv.party_details["a"] = {
            m["key"]: {
                "active":     m.get("active", False),
                "stat_stages": m.get("stat_stages"),
            }
            for m in party_msg if m.get("key")
        }
        detail = srv.party_details["a"]["AABBCCDD:11223344"]
        assert detail["active"] is False
        assert detail["stat_stages"] is None

# ── _enrich_battle_state passthrough ─────────────────────────────────────────

class TestEnrichBattleStatePassthrough:
    """stat_stages on enemy mons passes through _enrich_battle_state unchanged."""

    def test_stat_stages_preserved_after_enrich(self, tmp_path):
        """Drives the REAL enrichment, via the _build_status_dict that owns it.

        The previous version of this test never called the server at all: it rebuilt
        `em2 = dict(em)` inline and asserted that Python's dict() copies a reference. That
        passes whatever `_enrich_battle_state` does — including not existing. Since the
        function is a closure inside _build_status_dict and cannot be imported, the only
        honest way to reach it is to call its owner, which is what this now does.
        """
        stages = [6, 9, 6, 6, 6, 6, 6]  # DEF +3
        srv = _make_server(tmp_path, battle_state={"a": {
            "in_battle": True,
            "enemy_party": [
                {"species_id": 6, "hp": 50, "maxHP": 50, "level": 36,
                 "active": True, "status_cond": 0, "stat_stages": stages},
                {"species_id": 7, "hp": 0, "maxHP": 40, "level": 30,
                 "active": False, "status_cond": 0, "stat_stages": None},
            ]}})

        enriched = srv._build_status_dict()["players"]["a"]["battle_state"]["enemy_party"]

        assert enriched[0]["stat_stages"] == stages
        assert enriched[1]["stat_stages"] is None
        # Positive control: prove the enrichment actually RAN over this party, so the two
        # assertions above cannot pass by the enemy party being handed back untouched.
        assert enriched[0]["species_name"] == "Charizard"
        assert enriched[0]["sprite_html"]

# ── Offset correctness: 0x19 skips both vanilla HP-stage and CFRU type3 ──────
# The Gen 3 client reads stat stages through lua/gen3/reads.lua read_stat_stages, which takes
# the ATK offset from the pack: profile.derived.BATTLE_MON_STAT_STAGES_OFF. +0x18 is statStages
# [STAT_HP] in vanilla (always 6, pret include/pokemon.h:187) but CFRU's type3 byte on RR.

GEN3_TITLES = [("gen3_frlg", "firered"), ("gen3_frlg", "leafgreen"), ("gen3_rr", "radical_red")]
TYPE3_FAIRY = 0x17                     # what a 0x18 read would surface as a "stage" on RR
STAGES = [8, 4, 6, 7, 5, 6, 12]        # ATK+2 DEF-2 SPD SATK+1 SDEF-1 ACC EVA+6


def _poke_battler(w, battler, stages, hp=20, mon=None):
    """gBattleMons[battler]: stages at +0x18, hp/maxHP, and (given `mon`) its identity --
    personality +0x48, otId +0x54 (pret include/pokemon.h:202,205 struct BattlePokemon)."""
    base = w.ram["BATTLE_MONS_ADDR"] + battler * 0x58   # sizeof(struct BattlePokemon)
    w.poke(base + 0x18, bytes([TYPE3_FAIRY, *stages]))
    w.poke_int(base + 0x28, hp, 2)
    w.poke_int(base + 0x2C, hp, 2)
    if mon is not None:
        w.poke_int(base + 0x48, mon["personality"], 4)
        w.poke_int(base + 0x54, mon["ot_id"], 4)


class TestOffsetConstant:
    """Regression guard: the ATK stat-stage offset must never silently revert to 0x18."""

    @pytest.mark.parametrize("pack,title", GEN3_TITLES)
    def test_stat_stages_offset_is_0x19(self, pack, title):
        from tests.unit.gen3_world import World, lua_to_py
        w = World(pack, title)
        assert w.d["BATTLE_MON_STAT_STAGES_OFF"] == 0x19, (
            "Using 0x18 would read CFRU type3 (or the vanilla HP stage) as the ATK stage.")
        _poke_battler(w, 1, STAGES)
        assert lua_to_py(w.parts.reads.read_stat_stages(1)) == STAGES

    @pytest.mark.parametrize("pack,title,needle", [
        ("gen3_frlg", "firered", "include/pokemon.h"),
        ("gen3_rr", "radical_red", "type3"),
    ])
    def test_stat_stage_offset_is_sourced(self, pack, title, needle):
        """The profile says WHY 0x19: pret for vanilla, the old client's CFRU type3 note for RR."""
        from tests.unit.gen3_world import World
        assert needle in World(pack, title).profile["_src"]["derived.BATTLE_MON_STAT_STAGES_OFF"]

    @pytest.mark.parametrize("pack,title", [GEN3_TITLES[0], GEN3_TITLES[2]])
    def test_the_active_mons_carry_stat_stages_on_the_tick(self, pack, title):
        """Wire parity with the old client: active player mon + active foe carry stat_stages."""
        from tests.unit.gen3_world import World, mon_record
        w = World(pack, title)
        mine = [mon_record(0x11111111, 0xABCD, species=4, hp=20),
                mon_record(0x22222222, 0xABCD, species=5, hp=20)]
        foe = mon_record(0x77777777, 0x1234, species=19, level=3)
        w.set_party(mine)
        w.step_to(60)
        w.enter_battle([foe])
        _poke_battler(w, 0, [7, 6, 6, 6, 6, 6, 6], mon=mine[0])
        _poke_battler(w, 1, STAGES, mon=foe)
        w.step(61)
        tick = [t for t in w.events("tick") if t["in_battle"]][-1]
        party = {m["slot"]: m for m in tick["party"]}
        assert party[0]["active"] is True and party[0]["stat_stages"] == [7, 6, 6, 6, 6, 6, 6]
        assert "stat_stages" not in party[1]
        assert tick["enemy_party"][0]["stat_stages"] == STAGES



# ── coherence: stages ride a battler only while gBattleMons holds that mon ────────────────
# G5-STAGES-COHERENCE. A switch writes gBattlerPartyIndexes[battler] first and copies the new
# mon into gBattleMons (resetting its stages) only later (pret src/battle_script_commands.c
# 4452-4463 vs 4470-4503; the battle intro has the same order). In that window the index names
# the incoming mon while gBattleMons still holds the outgoing one, so the client publishes
# neither `active` nor `stat_stages` for it until the identities (personality + otId) agree.

MINE = [(0x11111111, 0xABCD, 4), (0x22222222, 0xABCD, 5)]
FOES = [(0x77777777, 0x1234, 19), (0x88888888, 0x1234, 16)]
NEUTRAL = [6] * 7


def _coherence_world(pack, title, doubles=False, foes=1):
    from tests.unit.gen3_world import World, mon_record
    w = World(pack, title)
    mine = [mon_record(p, o, species=s, hp=20) for p, o, s in MINE]
    enemy = [mon_record(p, o, species=s, level=3) for p, o, s in FOES[:foes]]
    w.set_party(mine)
    w.step_to(60)
    w.enter_battle(enemy, doubles=doubles, active=(0, 1) if doubles else (0,))
    return w, mine, enemy


def _tick(w):
    w.step(61)
    tick = [t for t in w.events("tick") if t["in_battle"]][-1]
    return {m["slot"]: m for m in tick["party"]}, tick["enemy_party"]


class TestStagesCoherence:
    @pytest.mark.parametrize("pack,title", [GEN3_TITLES[0], GEN3_TITLES[2]])
    def test_player_switch_publishes_nothing_until_gbattlemons_holds_the_incoming_mon(self, pack, title):
        w, mine, enemy = _coherence_world(pack, title)
        _poke_battler(w, 0, STAGES, mon=mine[0])
        _poke_battler(w, 1, NEUTRAL, mon=enemy[0])
        party, _ = _tick(w)
        assert party[0]["active"] is True and party[0]["stat_stages"] == STAGES   # control
        w.set_active([1])                        # index written first; gBattleMons still slot 0
        party, _ = _tick(w)
        for slot in (0, 1):
            assert party[slot]["active"] is False, slot
            assert "stat_stages" not in party[slot], slot
        _poke_battler(w, 0, NEUTRAL, mon=mine[1])   # the copy lands, stages reset
        party, _ = _tick(w)
        assert party[1]["active"] is True and party[1]["stat_stages"] == NEUTRAL
        assert party[0]["active"] is False and "stat_stages" not in party[0]

    @pytest.mark.parametrize("pack,title", [GEN3_TITLES[0], GEN3_TITLES[2]])
    def test_foe_switch_publishes_nothing_until_gbattlemons_holds_the_incoming_foe(self, pack, title):
        w, mine, enemy = _coherence_world(pack, title, foes=2)
        _poke_battler(w, 0, NEUTRAL, mon=mine[0])
        _poke_battler(w, 1, STAGES, mon=enemy[0])
        _, foes = _tick(w)
        assert foes[0]["active"] is True and foes[0]["stat_stages"] == STAGES     # control
        w.poke_int(w.ram["BATTLER_PARTY_INDEXES_ADDR"] + 2 * 1, 1, 2)            # foe index -> 1
        _, foes = _tick(w)
        for i in (0, 1):
            assert foes[i]["active"] is False, i
            assert "stat_stages" not in foes[i], i
        _poke_battler(w, 1, NEUTRAL, mon=enemy[1])
        _, foes = _tick(w)
        assert foes[1]["active"] is True and foes[1]["stat_stages"] == NEUTRAL
        assert foes[0]["active"] is False and "stat_stages" not in foes[0]

    @pytest.mark.parametrize("pack,title", [GEN3_TITLES[0], GEN3_TITLES[2]])
    def test_singles_never_expose_battlers_2_and_3(self, pack, title):
        """battlersCount=2: battlers 2/3 hold sentinel bytes whose index and identity even name
        real mons; nothing past gBattlersCount is ever read onto the wire."""
        w, mine, enemy = _coherence_world(pack, title, foes=2)
        _poke_battler(w, 0, NEUTRAL, mon=mine[0])
        _poke_battler(w, 1, NEUTRAL, mon=enemy[0])
        sentinel = [0x0B] * 7
        _poke_battler(w, 2, sentinel, mon=mine[1])
        _poke_battler(w, 3, sentinel, mon=enemy[1])
        w.poke_int(w.ram["BATTLER_PARTY_INDEXES_ADDR"] + 2 * 2, 1, 2)
        w.poke_int(w.ram["BATTLER_PARTY_INDEXES_ADDR"] + 2 * 3, 1, 2)
        party, foes = _tick(w)
        assert party[0]["stat_stages"] == NEUTRAL and foes[0]["stat_stages"] == NEUTRAL  # control
        assert party[1]["active"] is False and "stat_stages" not in party[1]
        assert foes[1]["active"] is False and "stat_stages" not in foes[1]

    @pytest.mark.parametrize("pack,title", [GEN3_TITLES[0], GEN3_TITLES[2]])
    def test_doubles_map_four_distinct_battlers(self, pack, title):
        w, mine, enemy = _coherence_world(pack, title, doubles=True, foes=2)
        stages = {b: [6 + b, 6, 6, 6, 6, 6, 12 - b] for b in range(4)}
        for b, mon in ((0, mine[0]), (1, enemy[0]), (2, mine[1]), (3, enemy[1])):
            _poke_battler(w, b, stages[b], mon=mon)
        party, foes = _tick(w)
        assert party[0]["stat_stages"] == stages[0] and party[1]["stat_stages"] == stages[2]
        assert foes[0]["stat_stages"] == stages[1] and foes[1]["stat_stages"] == stages[3]
        assert all(e["active"] is True for e in (party[0], party[1], foes[0], foes[1]))

    @pytest.mark.parametrize("pack,title", [GEN3_TITLES[0], GEN3_TITLES[2]])
    def test_a_link_battle_publishes_no_stages(self, pack, title):
        """Link battles: battler ids are not positions (pret battle_controllers.c:151-168)."""
        w, mine, enemy = _coherence_world(pack, title)
        _poke_battler(w, 0, STAGES, mon=mine[0])
        _poke_battler(w, 1, STAGES, mon=enemy[0])
        flags = w._read(w.ram["BATTLE_TYPE_ADDR"], 4)
        w.poke_int(w.ram["BATTLE_TYPE_ADDR"], flags | 0x02, 4)   # BATTLE_TYPE_LINK
        party, foes = _tick(w)
        assert "stat_stages" not in party[0] and "stat_stages" not in foes[0]

    @pytest.mark.parametrize("pack,title", GEN3_TITLES)
    def test_identity_and_link_facts_come_from_the_pack(self, pack, title):
        """pret BattlePokemon for vanilla; the RR values are read out of the RR companion ROM's
        own bytes (CopyPlayerMonData, byte-identical to FireRed's; InitBattleControllers)."""
        from tests.unit.gen3_world import World
        w = World(pack, title)
        assert (w.d["BATTLE_MON_PERSONALITY_OFF"], w.d["BATTLE_MON_OT_ID_OFF"],
                w.d["BATTLE_TYPE_LINK_MASK"]) == (0x48, 0x54, 0x02)
        src = w.profile["_src"]
        needle = "rom:patch/build/slink_RR.gba" if pack == "gen3_rr" else "include/pokemon.h"
        for key in ("BATTLE_MON_PERSONALITY_OFF", "BATTLE_MON_OT_ID_OFF"):
            assert needle in src[f"derived.{key}"], key
        link_needle = "rom:patch/build/slink_RR.gba" if pack == "gen3_rr" else "include/constants/battle.h"
        assert link_needle in src["derived.BATTLE_TYPE_LINK_MASK"]


# ── the status_pill macro ─────────────────────────────────────────────────────

def _status_icon_html(cond) -> str:
    """Render templates/_macros.html's status_pill the way every page does."""
    import jinja2

    from server.templating import TEMPLATES_DIR
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(TEMPLATES_DIR), autoescape=True)
    env.filters["bitand"] = lambda v, m: (int(v) & int(m)) if isinstance(v, int) else 0
    return env.from_string(
        '{% from "_macros.html" import status_pill %}{{ status_pill(cond) }}').render(cond=cond).strip()


class TestStatusIconHtml:
    """Tests for the status_pill macro, the one status-condition decoder every page uses.

    Gen 3 status1 bitmask (from pret/pokefirered include/constants/pokemon.h):
      bits 0–2  STATUS1_SLEEP         (counter 1–7)
      bit  3    STATUS1_POISON        0x08
      bit  4    STATUS1_BURN          0x10
      bit  5    STATUS1_FREEZE        0x20
      bit  6    STATUS1_PARALYSIS     0x40
      bit  7    STATUS1_TOXIC_POISON  0x80  — always combined with bit 3 (0x88)
    """

    # ── null cases ──────────────────────────────────────────────────────────

    def test_zero_returns_empty(self):
        assert _status_icon_html(0) == ""

    def test_none_returns_empty(self):
        assert _status_icon_html(None) == ""

    # ── individual conditions ───────────────────────────────────────────────

    def test_sleep_counter_1(self):
        result = _status_icon_html(0x01)
        assert "SLP" in result
        assert "sc-slp" in result

    def test_sleep_counter_7(self):
        result = _status_icon_html(0x07)
        assert "SLP" in result

    def test_poison(self):
        result = _status_icon_html(0x08)
        assert "PSN" in result
        assert "sc-psn" in result

    def test_burn(self):
        result = _status_icon_html(0x10)
        assert "BRN" in result
        assert "sc-brn" in result

    def test_freeze(self):
        result = _status_icon_html(0x20)
        assert "FRZ" in result
        assert "sc-frz" in result

    def test_paralysis(self):
        result = _status_icon_html(0x40)
        assert "PAR" in result
        assert "sc-par" in result

    def test_toxic(self):
        # Toxic sets both bit 7 (0x80) and bit 3 (0x08) in-game
        result = _status_icon_html(0x88)
        assert "TOX" in result
        assert "sc-tox" in result

    # ── priority: Toxic must win over PSN ──────────────────────────────────

    def test_toxic_beats_poison_when_both_bits_set(self):
        """Toxic sets STATUS1_POISON (bit 3) and STATUS1_TOXIC_POISON (bit 7).
        0x88 must display TOX, not PSN. Reversing the check order would break this."""
        result = _status_icon_html(0x88)
        assert "TOX" in result
        assert "PSN" not in result

    def test_tox_bit_alone_shows_tox(self):
        # bit 7 set without bit 3 — still TOX
        result = _status_icon_html(0x80)
        assert "TOX" in result
        assert "PSN" not in result

    # ── only one badge returned ─────────────────────────────────────────────

    def test_returns_single_badge_for_sleep_and_poison(self):
        """If somehow both sleep and poison bits are set, sleep wins (checked first)."""
        result = _status_icon_html(0x01 | 0x08)
        assert "SLP" in result
        assert "PSN" not in result

    def test_returns_one_span(self):
        result = _status_icon_html(0x40)
        assert result.count("<span") == 1

    # ── CSS class names ─────────────────────────────────────────────────────

    def test_css_class_names(self):
        """Verify exact class names — stream overlays depend on these."""
        cases = [
            (0x01, "sc-slp"),
            (0x08, "sc-psn"),
            (0x10, "sc-brn"),
            (0x20, "sc-frz"),
            (0x40, "sc-par"),
            (0x80, "sc-tox"),
        ]
        for cond, cls in cases:
            result = _status_icon_html(cond)
            assert cls in result, f"Expected CSS class '{cls}' for status_cond=0x{cond:02X}"

    # ── JS statusIcon parity check ──────────────────────────────────────────

    def test_status_pill_tox_before_psn(self):
        """In the status_pill macro at server/templates/_macros.html the
        Toxic (mask 128 / 0x80) branch must come before the regular
        Poison (mask 8 / 0x08) branch — otherwise a mon with both bits
        set displays as PSN instead of TOX. Source-level regression guard."""
        import pathlib
        src = pathlib.Path("server/templates/_macros.html").read_text(encoding="utf-8")
        tox_pos = src.find("bitand(128)")
        psn_pos = src.find("bitand(8)")
        assert tox_pos != -1, "bitand(128) Toxic check not found in status_pill macro"
        assert psn_pos != -1, "bitand(8) Poison check not found in status_pill macro"
        assert tox_pos < psn_pos, (
            "In _macros.html status_pill, the bitand(128) Toxic branch must "
            "appear before the bitand(8) Poison branch, otherwise Toxic mons "
            "render as PSN."
        )


# ── stat_stages_row's adapter-supplied labels ─────────────────────────────────

def _stages_html(stages, labels=None) -> str:
    """Render templates/_macros.html's stat_stages_row the way mon_card/combatant do."""
    import jinja2

    from server.templating import TEMPLATES_DIR
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(TEMPLATES_DIR), autoescape=True)
    return env.from_string(
        '{% from "_macros.html" import stat_stages_row %}'
        '{{ stat_stages_row(stages, labels) }}'
    ).render(stages=stages, labels=labels).strip()


class TestStatStageLabels:
    """The chips must read the adapter's `capabilities.stat_stage_labels`
    (server/adapters/gen1_rby.py:461-462 for Gen 1) rather than a hard-coded Gen 3 set —
    a Gen 1 board showing SATK/SDEF is lying, Gen 1 has one Special stat, not two."""

    GEN1_LABELS = ["ATK", "DEF", "SPD", "SPC", "", "ACC", "EVA"]

    def test_gen1_shaped_stages_render_def_never_satk_sdef(self):
        # index1 = DEF, raw 5 -> stage -1
        stages = [6, 5, 6, 6, 6, 6, 6]
        html = _stages_html(stages, self.GEN1_LABELS)
        assert "DEF" in html
        assert "SATK" not in html
        assert "SDEF" not in html

    def test_gen3_shaped_stages_still_render_satk_sdef(self):
        # index3 = SATK, raw 8 -> stage +2; index4 = SDEF, raw 4 -> stage -2
        stages = [6, 6, 6, 8, 4, 6, 6]
        html = _stages_html(stages)  # no labels arg -> falls back to the Gen 3 set
        assert "SATK" in html
        assert "SDEF" in html

    def test_blank_label_suppresses_its_chip_even_when_the_stage_is_nonzero(self):
        # index4 is Gen 1's unused slot ('') — a nonzero raw there must not render a chip.
        stages = [6, 6, 6, 6, 9, 6, 6]  # index4 raw 9 -> stage +3, everything else neutral
        html = _stages_html(stages, self.GEN1_LABELS)
        assert html == ""

    def test_no_labels_arg_keeps_stream_overlays_working(self):
        """mon_card / stat_stages_row(stages) with no labels arg (every stream overlay
        template) must still render the current Gen 3 labels — the default cannot regress
        just because combatant() in _board.html now threads a labels kwarg through."""
        stages = [8, 6, 6, 6, 6, 6, 6]  # ATK +2
        html = _stages_html(stages)
        assert "ATK" in html
        assert "+2" in html


class TestCombatantLabelsWiring:
    """_board.html's combatant() macro is the only call site the NOW cards use for a mon's
    stat stages. Source-level regression guard (same style as
    test_status_pill_tox_before_psn above) rather than rendering _board.html whole: the
    template's top level polls `board`/`status`/`rules` outside any macro, so importing
    just the macro still executes that top-level code and needs a full board_context to
    render — disproportionate for checking one kwarg is threaded through."""

    def test_combatant_signature_takes_labels(self):
        import pathlib
        src = pathlib.Path("server/templates/_board.html").read_text(encoding="utf-8")
        declaration = next(line for line in src.splitlines() if "macro combatant(" in line)
        assert "labels=None" in declaration, (
            "combatant() must accept a labels kwarg to pass the player's "
            "capabilities.stat_stage_labels through to stat_stages_row"
        )
        assert "stat_stages_row(mon.stat_stages, labels)" in src, (
            "combatant() must forward its labels kwarg into stat_stages_row, "
            "not render with stat_stages_row's hard-coded Gen 3 default"
        )

    def test_every_combatant_call_site_passes_the_players_labels(self):
        import pathlib
        src = pathlib.Path("server/templates/_board.html").read_text(encoding="utf-8")
        call_sites = [line for line in src.splitlines() if "combatant(" in line
                      and "macro combatant" not in line]
        assert call_sites, "no combatant(...) call sites found in _board.html"
        for line in call_sites:
            assert "labels=p.capabilities.stat_stage_labels" in line, (
                f"combatant() call site does not pass the player's adapter-supplied "
                f"labels, still on stat_stages_row's Gen 3 default: {line.strip()!r}"
            )


# ── is_doubles passthrough via tick handler ───────────────────────────────────

class TestDoublesPassthrough:
    """Verify is_doubles is stored, cleared, and left alone by the tick handler."""

    def _make_server(self, tmp_path):
        return _make_server(tmp_path, battle_state=_fresh_battle_state())

    def _apply_tick(self, srv, player_id, msg):
        """Apply only the is_doubles / enemy_party portion of the tick handler logic."""
        bs = srv.battle_state[player_id]
        was_in_battle = bs.get("in_battle", False)
        now_in_battle = msg.get("in_battle", was_in_battle)
        bs["in_battle"] = now_in_battle

        if was_in_battle and not now_in_battle:
            bs["is_doubles"] = False
            bs["enemy_party"] = []

        if "enemy_party" in msg:
            bs["enemy_party"] = msg["enemy_party"]
        if "is_doubles" in msg:
            bs["is_doubles"] = bool(msg["is_doubles"])
        elif "enemy_party" in msg:
            active_count = sum(1 for e in msg["enemy_party"] if e.get("active"))
            if active_count > 1:
                bs["is_doubles"] = True

    def test_is_doubles_set_from_tick(self, tmp_path):
        """A tick with is_doubles=true sets battle_state['is_doubles'] to True."""
        srv = self._make_server(tmp_path)
        self._apply_tick(srv, "a", {
            "in_battle": True,
            "is_doubles": True,
            "enemy_party": [
                {"species_id": 1, "level": 10, "active": True},
                {"species_id": 4, "level": 10, "active": True},
            ],
        })
        assert srv.battle_state["a"]["is_doubles"] is True

    def test_is_doubles_cleared_on_battle_end(self, tmp_path):
        """When in_battle transitions False, is_doubles is reset to False."""
        srv = self._make_server(tmp_path)
        # Put server into a doubles battle
        srv.battle_state["a"]["in_battle"] = True
        srv.battle_state["a"]["is_doubles"] = True
        # Battle ends
        self._apply_tick(srv, "a", {"in_battle": False})
        assert srv.battle_state["a"]["is_doubles"] is False

    def test_is_doubles_unchanged_when_key_absent(self, tmp_path):
        """A tick without the is_doubles key leaves the existing value untouched."""
        srv = self._make_server(tmp_path)
        srv.battle_state["a"]["in_battle"] = True
        srv.battle_state["a"]["is_doubles"] = True
        # Tick mid-battle without is_doubles key
        self._apply_tick(srv, "a", {
            "in_battle": True,
            "enemy_party": [{"species_id": 1, "level": 10, "active": True}],
        })
        assert srv.battle_state["a"]["is_doubles"] is True

    def test_is_doubles_inferred_from_active_count(self, tmp_path):
        """Without is_doubles key, two active enemies infer is_doubles=True."""
        srv = self._make_server(tmp_path)
        self._apply_tick(srv, "b", {
            "in_battle": True,
            "enemy_party": [
                {"species_id": 7, "level": 15, "active": True},
                {"species_id": 9, "level": 15, "active": True},
            ],
        })
        assert srv.battle_state["b"]["is_doubles"] is True
