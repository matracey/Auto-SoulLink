"""COLD-BOOT-BOXES: a cold boot baselines at party=0 / boxes n=0 (observe_known runs rescan_boxes
only inside `if not st.baselined`), the save then loads, and the later quiet re-seed reads the
STALE box cache. A native gift into a full party's box then sees every boxed key as unknown and
publishes nothing ("N new boxed keys, none reported (ambiguous)"). R1 is RED until observe_known
rescans the boxes before its seed_known(party); R2/R4/R5 are controls that pass today.

The `w.step(3)` after the load is load-bearing: the party-count change must stay quiet across TWO
frame boundaries before observe_known re-seeds (client.lua observe_known). A gift signalled
sooner than that finds the loaded party unknown and publishes it as captures (a PRE-EXISTING
behaviour, pinned below so a future fix records the change). gen3_exp is registered but
unrouted, so a World cannot build it; the live duo row gift_box_gen3 is its only harness."""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.unit.gen3_world import World, key_of, mon_record
from tests.unit.test_gen3_client import OT, party

ROOT = Path(__file__).resolve().parents[2]
TITLES = [("gen3_frlg", "firered"), ("gen3_rr", "radical_red"), ("gen3_emerald", "emerald")]
FULL = (0x11111111, 0x22222222, 0x33333333, 0x44444444, 0x55555555, 0x66666666)
OLD1, OLD2, GIFT = 0xA0000001, 0xA0000002, 0xB0000003
NEW1, NEW2 = 0xC0000001, 0xC0000002
STARTER = mon_record(0x77777777, OT, species=4, nickname="STARTER")


def boxed(pid):
    return mon_record(pid, OT, species=19, nickname="BOXED")


def world(monkeypatch, pack, title, pre=None, kind=None):
    """A bare World; `pre(w)` runs BEFORE the first step (a complete first census)."""
    w = World(pack, title, kind)
    if pre:
        pre(w)
    w.step_to(60)
    assert w.client.writes_enabled
    return w


def sent_text(w):
    return "\n".join(w.lines)


def _r1(w):
    w.set_party(party(*FULL))                                # the save loads
    w.set_box(0, 0, boxed(OLD1))
    w.set_box(0, 1, boxed(OLD2))
    w.step(3)                                                # the quiet count interval settles
    w.set_box(0, 2, boxed(GIFT))                             # native gift into the full party's box
    w.fire("mon_given")
    w.step(5)
    caps = w.events("capture")
    assert len(caps) == 1, (caps, [line for line in w.logs if "boxed keys" in line])
    assert caps[0]["in_box"] is True and caps[0]["gift"] is True
    assert caps[0]["key"] == key_of(GIFT, OT)
    for old in (OLD1, OLD2):
        assert key_of(old, OT) not in sent_text(w)
    assert not any("3 new boxed keys" in line for line in w.logs)


@pytest.mark.parametrize("pack,title", TITLES)
def test_r1_cold_boot_save_load_then_gift_into_a_full_party_box_is_one_gift_capture(monkeypatch, pack, title):
    _r1(world(monkeypatch, pack, title))                     # baselined at party=0, boxes n=0


def test_r1_on_the_radical_red_companion_artifact(monkeypatch):
    _r1(world(monkeypatch, "gen3_rr", "radical_red", kind="companion"))


@pytest.mark.parametrize("pack,title", TITLES)
def test_r2_control_two_new_boxed_mons_in_one_frame_stay_ambiguous(monkeypatch, pack, title):
    def pre(w):
        w.set_party(party(*FULL))
        w.set_box(0, 0, boxed(OLD1))
    w = world(monkeypatch, pack, title, pre)
    w.set_box(0, 1, boxed(NEW1))
    w.set_box(0, 2, boxed(NEW2))
    w.fire("mon_given")
    w.step(5)
    assert w.events("capture") == []
    assert any("acquisition retained: incomplete baseline or ambiguous PC identity" in line
               for line in w.logs)
    w.step(60)
    assert w.events("capture") == []


@pytest.mark.parametrize("pack,title", TITLES)
def test_r4_control_new_game_starter_is_one_gift_capture(monkeypatch, pack, title):
    w = world(monkeypatch, pack, title)
    w.set_party([])
    w.set_party([STARTER])
    w.fire("mon_given")
    w.step(5)
    (cap,) = w.events("capture")
    assert cap["key"] == key_of(0x77777777, OT) and cap["gift"] is True
    assert "in_box" not in cap


@pytest.mark.parametrize("pack,title", TITLES)
def test_r5_control_full_unchanged_party_pc_move_is_one_boxed_capture(monkeypatch, pack, title):
    w = world(monkeypatch, pack, title, lambda w: w.set_party(party(*FULL)))
    w.set_box(0, 0, boxed(NEW1))
    w.fire("pc_move")
    w.step(5)
    (cap,) = w.events("capture")
    assert cap["key"] == key_of(NEW1, OT) and cap["in_box"] is True


@pytest.mark.parametrize("pack,title", TITLES)
def test_settle_window_two_quiet_frames_after_the_load(monkeypatch, pack, title):
    """The re-seed needs the count to stay quiet for 2 frames; sooner, the loaded party is
    published as gift captures (pre-existing: identical before the box rescan fix)."""
    def run(quiet):
        w = world(monkeypatch, pack, title)
        w.set_party(party(*FULL))
        w.set_box(0, 0, boxed(OLD1))
        w.step(quiet)
        w.set_box(0, 2, boxed(GIFT))
        w.fire("mon_given")
        w.step(5)
        return w.events("capture")
    assert len(run(0)) == len(FULL)                  # no settle: the six loaded mons are published
    caps = run(2)
    assert [c["key"] for c in caps] == [key_of(GIFT, OT)]


def test_a_settle_scans_the_boxes_exactly_once():
    """observe_known's first settle used to scan twice in one frame (the baseline branch and the
    unconditional re-learn): 2 x 14 boxes x 30 records decrypted for nothing on every title, and
    a stale generation in the baseline log. The unit World baselines at hello and never reaches
    that branch, so the invariant is pinned on the function body: ONE rescan_boxes() call."""
    src = (ROOT / "lua/gen3/client.lua").read_text(encoding="utf-8")
    start = src.index("local function observe_known()")
    body = src[start:src.index("drv.frame_hooks", start)]
    body = body[:body.index("seed_known(party)")]            # the part that scans before re-seeding
    assert body.count("rescan_boxes()") == 1, body.count("rescan_boxes()")
