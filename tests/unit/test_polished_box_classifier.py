"""BOX3 client classifier: real permits/codecs/census, explicit-only and capability OFF.

MODEL evidence, not a native-save or emulator receipt. BOX3_STAGE2 §§2.1–2.3:
memorial remains uncomposed; its future byte rules use the same pure oracle.
"""
from __future__ import annotations

import copy
import json
from unittest.mock import patch

import pytest

from tests.unit import test_polished_withdraw_path as withdraw, test_polished_write_path as path
from tests.unit.test_polished_write_path import (
    MODULE,
    PARTY_COUNT,
    PARTY_END,
    PARTY_MONS,
    RECORD,
    SYM,
    Rig,
    box_flat,
    entry_for,
    key_of,
    party,
    plant,
)

COMPLETE = "PROVED_COMPLETE"
NEGATIVE = "PROVED_NOT_COMPLETE"
UNCERTAIN = "UNCERTAIN"
FAULT_HARNESS = path.HARNESS.replace(
    "function io.read_u8(a, d)",
    """function io.read_u8(a, d)
        if io.bcls_hit and io.bcls_fault == 'unreadable' then return nil end""",
).replace(
    "function io.write_u8(a, v, d)",
    """function io.write_u8(a, v, d)
        io.bcls_index = (io.bcls_index or 0) + 1
        local hit = io.bcls_index == io.bcls_at
        if hit then
            io.bcls_hit = true
            if io.bcls_fault == 'drop' then return end
        end""",
).replace(
    'else mem[a] = v end',
    """else mem[a] = v end
        if hit and io.bcls_fault == 'throw' then error('after byte', 0) end
        if hit and io.bcls_fault == 'identity' then
            mem[0xD47B] = ((mem[0xD47B] or 0) + 1) % 256
        end""",
)


def setup(cmd="box_mon", *, n=3, faults=False, mons=None, bank=2):
    mons = mons or party(n)
    with patch.object(path, "HARNESS", FAULT_HARNESS if faults else path.HARNESS):
        rig = Rig(mons)
    target = mons[min(1, len(mons) - 1)] if cmd == "box_mon" else party(1)[0]
    if cmd == "party_mon":
        # A genuinely distinct source, not a both-places ambiguity.
        target = party(10)[9]
        plant(rig.img, 1, 1, bank, 1, entry_for(target))
    return rig, key_of(target)


def context(rig):
    return rig.lua.eval("{write_id='bw-test',session='lifetime',generation=1,valid=function() return true end}")


def attempt(rig, cmd, key, ctx=None):
    result = rig.overworld().boxes.attempt_box_write(cmd, key, ctx or context(rig))
    return result if isinstance(result, tuple) else (result, None)


def plain(value):
    if hasattr(value, "items"):
        return {k: plain(v) for k, v in value.items()}
    return value


def oracle(p, after, *, context_valid=True):
    """Independent exact byte oracle (no executor boolean, text or permit receipt)."""
    if not p["source_valid"] or not context_valid or after is None:
        return UNCERTAIN
    before, post = p["before"], p.get("postimage")

    def matches(want, ignored=None):
        if not want or want["party_count"] != after["party_count"]:
            return False
        return all(v == after["bytes"][d][a]
                   for d, data in want["bytes"].items() for a, v in data.items()
                   if not (ignored or {}).get(d, {}).get(a))

    if matches(post):
        return COMPLETE
    if matches(before, p.get("pre_ignored")):
        return NEGATIVE
    return UNCERTAIN


def test_planner_is_read_only_and_predicts_exact_party_and_sealed_entry():
    rig, key = setup()
    ctx = context(rig)
    before_mem, before_cart = dict(rig.mem.items()), bytes(rig.img.mem["CartRAM"])
    p = rig.overworld().boxes.plan_box_write("box_mon", key, ctx)
    assert p.source.slot == 1 and p.destination.box == 1 and p.destination.entry == 1
    assert rig.writes() == []
    assert dict(rig.mem.items()) == before_mem and bytes(rig.img.mem["CartRAM"]) == before_cart
    assert p.before.party_count == 3 and p.postimage.party_count == 2
    result, evidence = attempt(rig, "box_mon", key, ctx)
    assert result.outcome == COMPLETE and result.party_count == 2
    assert oracle(plain(evidence), plain(evidence.after)) == COMPLETE
    assert key not in [m.key for m in rig.parts.reads.read_party().mons.values()]
    assert key in rig.census_keys()[0][1]


def test_planner_excludes_backup_references_even_with_clear_allocation_flag():
    rig, key = setup()
    at = SYM["sBackupNewBox1"][0] * 0x2000 + SYM["sBackupNewBox1"][1] - 0xA000
    rig.img.mem["CartRAM"][at] = 1
    p = rig.overworld().boxes.plan_box_write("box_mon", key, context(rig))
    assert p.destination.entry == 2
    assert rig.writes() == []


@pytest.mark.parametrize("cmd", ["box_mon", "party_mon", "memorialize"], ids=["deposit", "withdraw", "memorial-off"])
def test_real_attempts_and_default_off_wire(cmd):
    rig, key = setup(cmd)
    result, p = attempt(rig, cmd, key)
    expected = NEGATIVE if cmd == "memorialize" else COMPLETE
    assert result.outcome == expected
    assert result.party_count == (3 if cmd == "memorialize" else 2 if cmd == "box_mon" else 4)
    assert oracle(plain(p), plain(p.after)) == expected
    assert all("box_write_contract" not in row for row in rig.sent("hello"))
    assert rig.sent("box_write_result") == []
    if cmd == "memorialize":
        assert rig.writes() == []
        assert p.postimage is None


@pytest.mark.parametrize("cmd", ["box_mon", "party_mon", "memorialize"], ids=["deposit", "withdraw", "memorial"])
def test_missing_expected_source_never_invents_membership_or_count(cmd):
    rig, _ = setup(cmd)
    result, _ = attempt(rig, cmd, "absent")
    assert result.outcome == UNCERTAIN and result.party_count is None and result.stats is None
    assert rig.writes() == []


@pytest.mark.parametrize("guard", ["egg-party", "mail-party", "last", "egg-box", "mail-box", "full", "dead"],
                         ids=["egg-party", "mail-party", "last-mon", "egg-box", "mail-box", "full-party", "dead-party"])
def test_verified_no_emission_guard_refusals(guard):
    mons = party(1 if guard == "last" else 6 if guard == "full" else 3)
    cmd = "party_mon" if guard in {"egg-box", "mail-box", "full", "dead"} else "box_mon"
    if guard == "egg-party":
        mons[1]["is_egg"] = True
    if guard == "mail-party":
        mons[1]["held_item"] = json.loads((path.REPO / "data/games/polished_crystal/items.json").read_text())["mail_ids"][0]
    rig, key = setup(cmd, mons=mons)
    if guard == "last":
        key = key_of(mons[0])
    if guard in {"egg-box", "mail-box"}:
        target = party(10)[9]
        target["is_egg"] = guard == "egg-box"
        if guard == "mail-box":
            target["held_item"] = json.loads((path.REPO / "data/games/polished_crystal/items.json").read_text())["mail_ids"][0]
        plant(rig.img, 1, 1, 2, 1, entry_for(target))
    if guard == "dead":
        rig.img.mem["CartRAM"][box_flat(1)] = 0
        key = key_of(mons[0])
        for a in (PARTY_MONS + 34, PARTY_MONS + 35):
            rig.mem[a] = 0
    result, p = attempt(rig, cmd, key)
    assert result.outcome == NEGATIVE and result.party_count == len(mons)
    assert p.guard_refusal is True and p.emission_started is False
    assert rig.writes() == []


def test_no_box_healthy_legacy_shortcut_is_not_new_operation_proof():
    rig = Rig(party())
    key = key_of(party()[0])
    result, _ = attempt(rig, "party_mon", key)
    assert result.outcome == UNCERTAIN and result.party_count is None
    assert rig.overworld().boxes.withdraw(key) is True  # legacy shape and behavior unchanged
    assert rig.writes() == []


def test_same_id_reclassification_is_idempotent_even_after_later_death():
    rig, key = setup("party_mon")
    ctx = context(rig)
    result, p = attempt(rig, "party_mon", key, ctx)
    assert result.outcome == COMPLETE
    writes = len(rig.writes())
    assert rig.overworld().boxes.classify_box_write(p, ctx).outcome == COMPLETE
    assert len(rig.writes()) == writes
    # Terminal replay belongs to the owed-slot owner, NOT today's byte classifier.
    slot = 3
    rig.mem[PARTY_MONS + slot * RECORD + 34] = 0
    rig.mem[PARTY_MONS + slot * RECORD + 35] = 0
    fresh, _ = attempt(rig, "party_mon", key, ctx)
    assert fresh.outcome == NEGATIVE
    assert len(rig.writes()) == writes


# Every emitted byte of the REAL operation, including unchanged bytes and count-last.
# Deposit: entry[49], flag, Banks, Entries, full party block. Withdraw: 70 slot bytes,
# count, Entries, Banks. Each short ID names command, byte ordinal and fault.
BYTE_CASES = [(cmd, index, fault)
              for cmd, total in (("box_mon", 52 + PARTY_END - PARTY_COUNT), ("party_mon", 73))
              for index in range(1, total + 1) for fault in ("drop", "throw", "unreadable")]


@pytest.fixture(scope="module")
def fault_rigs():
    out = {}
    for cmd in ("box_mon", "party_mon"):
        rig, key = setup(cmd, faults=True)
        out[cmd] = (rig, key, dict(rig.mem.items()), {d: bytes(b) for d, b in rig.img.mem.items()})
    return out


def reset_fault_rig(saved):
    rig, key, mem, images = saved
    for a in list(rig.mem.keys()):
        rig.mem[a] = None
    for a, v in mem.items():
        rig.mem[a] = v
    for d, data in images.items():
        rig.img.mem[d][:] = data
    rig.log.writes = rig.lua.table()
    rig.io.bcls_index, rig.io.bcls_hit, rig.io.bcls_fault, rig.io.bcls_at = 0, None, None, None
    return rig, key


@pytest.mark.parametrize("cmd,index,fault", BYTE_CASES,
                         ids=[f"{'d' if c == 'box_mon' else 'w'}{i}-{f}" for c, i, f in BYTE_CASES])
def test_every_write_fault_obeys_exact_byte_oracle(cmd, index, fault, fault_rigs):
    rig, key = reset_fault_rig(fault_rigs[cmd])
    rig.io.bcls_at, rig.io.bcls_fault = index, fault
    result, p = attempt(rig, cmd, key)
    assert rig.io.bcls_hit is True
    assert p.emission_started is True
    if fault == "unreadable":
        assert result.outcome == UNCERTAIN
    else:
        assert result.outcome == oracle(plain(p), plain(p.after))
    if result.outcome == UNCERTAIN:
        assert result.party_count is None and result.stats is None
    else:
        assert result.party_count == rig.count()


@pytest.mark.parametrize("index", [1, 50, 53, 52 + PARTY_END - PARTY_COUNT],
                         ids=["staging", "flag", "compaction", "count-last"])
def test_identity_changed_mid_deposit_is_uncertain(index):
    rig, key = setup(faults=True)
    # Harness uses the symbol, not a vanilla identity coordinate.
    harness = FAULT_HARNESS.replace("0xD47B", str(SYM["wPlayerID"][1]))
    with patch.object(path, "HARNESS", harness):
        rig, key = setup(faults=False)
    rig.io.bcls_at, rig.io.bcls_fault = index, "identity"
    result, _ = attempt(rig, "box_mon", key)
    assert result.outcome == UNCERTAIN and result.party_count is None


@pytest.mark.parametrize("cut", [71, 72], ids=["both-places-full-party", "entries-clear-banks-pending"])
def test_retained_withdraw_cleanup_is_bounded_and_never_reappends(cut):
    rig, key = setup("party_mon", n=5, faults=True)
    ctx = context(rig)
    rig.io.bcls_at, rig.io.bcls_fault = cut, "throw"
    result, p = attempt(rig, "party_mon", key, ctx)
    assert result.outcome == UNCERTAIN
    assert rig.count() == 6
    writes = len(rig.writes())
    rig.io.bcls_fault = None
    proved = rig.overworld().boxes.classify_box_write(p, ctx, True)
    assert proved.outcome == COMPLETE and proved.party_count == 6
    assert all(w["domain"] == "CartRAM" for w in rig.writes()[writes:])
    assert len(rig.writes()) - writes <= 2
    assert key not in rig.census_keys()[0][1]


@pytest.mark.parametrize("change", ["options", "generation", "session", "write-id", "hold", "survivor", "entry", "neighbour"],
                         ids=["options", "generation", "session", "id", "hold", "survivor", "sealed-entry", "banks-neighbour"])
def test_cleanup_rejects_changed_context_or_torn_postimage(change):
    rig, key = setup("party_mon", faults=True)
    ctx = context(rig)
    rig.io.bcls_at, rig.io.bcls_fault = 72, "throw"
    _, p = attempt(rig, "party_mon", key, ctx)
    rig.io.bcls_fault = None
    if change == "options":
        rig.mem[0xCFF6] = 1
    elif change in {"generation", "session", "write-id"}:
        ctx[{"generation": "generation", "session": "session", "write-id": "write_id"}[change]] = "changed"
    elif change == "hold":
        rig.mem[SYM["wScriptRunning"][1]] = 1
    elif change == "survivor":
        rig.mem[PARTY_MONS + 2] = ((rig.mem[PARTY_MONS + 2] or 0) + 1) % 256
    elif change == "entry":
        a = path.entry_flat(2, 1)
        rig.img.mem["CartRAM"][a + 1] ^= 1
    else:
        rig.img.mem["CartRAM"][box_flat(1) + 20] ^= 2
    writes = len(rig.writes())
    result = rig.overworld().boxes.classify_box_write(p, ctx, True)
    assert result.outcome == UNCERTAIN and result.party_count is None and result.stats is None
    assert len(rig.writes()) == writes


def test_lost_hold_allows_later_read_only_proof_under_same_lifetime():
    rig, key = setup(faults=True)
    ctx = context(rig)
    rig.io.bcls_at, rig.io.bcls_fault = 1, "unreadable"
    result, p = attempt(rig, "box_mon", key, ctx)
    assert result.outcome == UNCERTAIN
    writes = len(rig.writes())
    rig.io.bcls_fault = None
    rig.io.frame += 1
    later = rig.overworld().boxes.classify_box_write(p, ctx)
    assert later.outcome == NEGATIVE and later.party_count == 3
    assert len(rig.writes()) == writes


@pytest.fixture(scope="module")
def pure_rig():
    return Rig(party())


def pure_evidence(cmd, state):
    before = {"bytes": {"System Bus": {1: 3, 2: 11}, "CartRAM": {1: 1, 2: 0, 3: 7}, "WRAM": {1: 1}},
              "party_count": 3, "complete": True}
    post = copy.deepcopy(before)
    if cmd == "memorialize":
        post["bytes"]["CartRAM"].update({1: 0, 2: 1})  # same sealed entry, box-20 publication, source cleared
    elif cmd == "box_mon":
        post["bytes"]["System Bus"][1] = 2
        post["party_count"] = 2
        post["bytes"]["CartRAM"][2] = 1
    else:
        post["bytes"]["System Bus"][1] = 4
        post["party_count"] = 4
        post["bytes"]["CartRAM"][1] = 0
    e = {"cmd": cmd, "source_valid": True, "context_valid": True, "emission_started": True,
         "before": before, "postimage": post, "after": copy.deepcopy(post if state == "post" else before),
         "stats": {"hp": 20}}
    if state == "torn":
        e["after"]["bytes"]["System Bus"][2] = 99
    elif state == "unreadable":
        e.pop("after")
    elif state == "identity":
        e["context_valid"] = False
    elif state == "missing":
        e["source_valid"] = False
    return e


PURE_CASES = [(cmd, state) for cmd in ("box_mon", "party_mon", "memorialize")
              for state in ("post", "pre", "torn", "unreadable", "identity", "missing")]


@pytest.mark.parametrize("cmd,state", PURE_CASES, ids=[f"{c}-{s}" for c, s in PURE_CASES])
def test_pure_evidence_table_including_future_memorial_postimages(cmd, state, pure_rig):
    rig = pure_rig
    mod = rig.lua.execute(MODULE)
    e = pure_evidence(cmd, state)
    result = mod.classify_box_write(rig.lua.table_from(e, recursive=True))
    expected = COMPLETE if state == "post" else NEGATIVE if state == "pre" else UNCERTAIN
    assert result.outcome == expected
    if expected == UNCERTAIN:
        assert result.party_count is None and result.stats is None


MUTANTS = [
    ("context", "e.context_valid ~= true", "false", "identity"),
    ("source", "e.source_valid ~= true", "false", "missing"),
    ("whole-image", "have.bytes[domain][at] ~= value", "false", "torn"),
    ("post-proof", "image_matches(e.postimage, e.after)", "false", "post"),
    ("pre-proof", "image_matches(e.before, e.after, e.pre_ignored)", "false", "pre"),
]


@pytest.mark.parametrize("name,old,new,state", MUTANTS, ids=[m[0] for m in MUTANTS])
def test_classifier_mutants_fail_the_same_outcome_oracle(name, old, new, state, pure_rig):
    assert old in MODULE
    rig = pure_rig
    mod = rig.lua.execute(MODULE.replace(old, new, 1))
    evidence = pure_evidence("memorialize", state)
    expected = COMPLETE if state == "post" else NEGATIVE if state == "pre" else UNCERTAIN
    assert mod.classify_box_write(rig.lua.table_from(evidence, recursive=True)).outcome != expected


def test_guard_exception_never_reports_unread_party_count(pure_rig):
    rig = pure_rig
    mod = rig.lua.execute(MODULE)
    e = {"source_valid": True, "context_valid": True, "guard_refusal": True, "emission_started": False}
    result = mod.classify_box_write(rig.lua.table_from(e))
    assert result.outcome == NEGATIVE and result.party_count is None
    e["emission_started"] = True
    result = mod.classify_box_write(rig.lua.table_from(e))
    assert result.outcome == UNCERTAIN and result.party_count is None


@pytest.mark.parametrize("rule", ["guard-emission", "scratch", "inactive-tail", "count", "no-count-leak", "no-stats-leak"],
                         ids=["guard-emission", "scratch-exception", "inactive-exception", "proved-count", "count-leak", "stats-leak"])
def test_additional_classification_rule_mutants_are_killed(rule, pure_rig):
    rig = pure_rig
    e = pure_evidence("box_mon", "pre")
    old, new, expected = "", "", NEGATIVE
    if rule == "guard-emission":
        e = {"source_valid": True, "context_valid": True, "guard_refusal": True, "emission_started": True}
        old, new, expected = "and not e.emission_started", "", UNCERTAIN
    elif rule in {"scratch", "inactive-tail"}:
        domain = "CartRAM" if rule == "scratch" else "System Bus"
        e["pre_ignored"] = {domain: {3: True}}
        e["before"]["bytes"][domain][3] = 7
        e["postimage"]["bytes"][domain][3] = 9
        e["after"]["bytes"][domain][3] = 8
        old, new = "e.pre_ignored)", "nil)"
    elif rule == "count":
        e["after"]["party_count"] = 2
        old, new, expected = "if want.party_count ~= have.party_count then return false end", "", UNCERTAIN
    else:
        e = pure_evidence("box_mon", "torn")
        old = 'local uncertain = {outcome = "UNCERTAIN", reason = e.reason or "box write byte proof unavailable"}'
        new = old[:-1] + (", party_count = 3}" if rule == "no-count-leak" else ", stats = e.stats}")
        expected = UNCERTAIN
    assert old in MODULE
    good = rig.lua.execute(MODULE).classify_box_write(rig.lua.table_from(e, recursive=True))
    bad = rig.lua.execute(MODULE.replace(old, new, 1)).classify_box_write(rig.lua.table_from(e, recursive=True))
    assert good.outcome == expected
    if rule == "no-count-leak":
        assert good.party_count is None and bad.party_count is not None
    elif rule == "no-stats-leak":
        assert good.stats is None and bad.stats is not None
    else:
        assert bad.outcome != expected


def test_wholly_swallowed_stage_proves_safe_prestate_without_trusting_receipts():
    rig, key = setup()
    rig.io.write_u8 = rig.lua.eval("function(io) return function() io.swallowed = (io.swallowed or 0) + 1 end end")(rig.io)
    result, p = attempt(rig, "box_mon", key)
    assert rig.io.swallowed == 49 and p.emission_started is True
    assert result.outcome == NEGATIVE and result.party_count == 3
    assert oracle(plain(p), plain(p.after)) == NEGATIVE
    assert key not in rig.census_keys()[0][1]


@pytest.mark.parametrize("capacity", ["box-slots", "allocation"], ids=["all-nonmemorial-full", "both-pokedb-banks-full"])
def test_capacity_guard_refuses_before_emission(capacity):
    rig, key = setup()
    if capacity == "box-slots":
        entry = 1
        for box in range(1, 20):
            entry = path.fill(rig.img, box, entry)
    else:
        for bank in (1, 2):
            start = path.FLAG_FLAT[bank]
            rig.img.mem["WRAM"][start:start + 26] = b"\xff" * 26
    result, p = attempt(rig, "box_mon", key)
    assert result.outcome == NEGATIVE and result.party_count == 3
    assert p.guard_refusal and not p.emission_started
    assert rig.writes() == []


@pytest.mark.parametrize("cmd", ["box_mon", "party_mon", "memorialize"], ids=["deposit", "withdraw", "memorial"])
def test_unreadable_initial_proof_never_emits_or_reports_a_count(cmd):
    rig, key = setup(cmd, faults=True)
    rig.io.bcls_hit, rig.io.bcls_fault = True, "unreadable"
    result, p = attempt(rig, cmd, key)
    assert result.outcome == UNCERTAIN and result.party_count is None and result.stats is None
    assert p is None and rig.writes() == []


@pytest.mark.parametrize("refusal", ["uncomposed", "nil", "throw"], ids=["missing-stat-codec", "stat-rebuild-refused", "stat-rebuild-throws"])
def test_rebuild_guards_are_proved_before_any_emission(refusal):
    old = "local STATS, BASE, VARIANT, MOVE_PP = deps.stats, deps.base_stats, deps.variant_record, deps.move_pp"
    codec = {
        "uncomposed": "nil",
        "nil": "{party_from_savemon=function() return nil,'rebuild guard' end}",
        "throw": "{party_from_savemon=function() error('rebuild guard',0) end}",
    }[refusal]
    rig, key = setup("party_mon")
    raw = withdraw.build(rig, [(old, old.replace("deps.stats", codec))])
    result, p = raw.attempt_box_write("party_mon", key, context(rig))
    assert result.outcome == NEGATIVE and result.party_count == 3
    assert p.guard_refusal and not p.emission_started
    assert rig.writes() == []


@pytest.mark.parametrize("cmd", ["box_mon", "party_mon", "memorialize"], ids=["duplicate-party", "duplicate-box", "both-places-memorial"])
def test_ambiguous_expected_source_never_proves_a_negative(cmd):
    mons = party()
    if cmd == "box_mon":
        mons[1] = copy.deepcopy(mons[0])
    rig, key = setup(cmd, mons=mons)
    if cmd == "party_mon":
        plant(rig.img, 1, 2, 2, 2, entry_for(party(10)[9]))
    elif cmd == "memorialize":
        plant(rig.img, 1, 1, 2, 1, entry_for(mons[0]))
    result, _ = attempt(rig, cmd, key)
    assert result.outcome == UNCERTAIN and result.party_count is None and result.stats is None
    assert rig.writes() == []
