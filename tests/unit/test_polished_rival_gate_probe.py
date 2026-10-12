"""Read-only rival gate oracle/runner tests: synthetic traces and ROMs, no emulator.

Lua tests also execute the read-only driver against mocked emulator APIs. Native
callback-PC semantics remain OPEN until an owner-authorized played run produces evidence.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location(
    "rival_gate_probe", REPO / "tools/polished_live/rival_gate_probe.py")
P = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(P)


def hit(kind="gate", **fields):
    addr = P.HOOKS[kind]
    row = {"kind": kind, "hook_addr": addr, "pc": addr, "sp": 0xC0DC, "bank": 15,
           "matched": True, "mode": 2, "trainer_class": 9, "trainer_id": 1,
           "cur_ot_mon": 0, "cur_party_mon": 0, "ot_party_count": 3,
           "site_bytes": "218bd2fa0cd1", "hook_bytes": "218bd2fa0cd1"}
    row.update(fields)
    row.setdefault("qualified", row["bank"] == P.BANK and row["pc"] == addr)
    return row


def complete(events):
    counts = {name: {"total": 0, "qualified": 0, "wrong_pc": 0,
                     "wrong_banks": {}, "wrong_bank_samples": 0} for name in P.HOOKS}
    for row in events:
        if row.get("kind") not in counts:
            continue
        c = counts[row["kind"]]
        c["total"] += 1
        if row["bank"] != P.BANK:
            bank = str(row["bank"])
            c["wrong_banks"][bank] = c["wrong_banks"].get(bank, 0) + 1
            c["wrong_bank_samples"] += 1
        elif row["pc"] == P.HOOKS[row["kind"]]:
            c["qualified"] += 1
        else:
            c["wrong_pc"] += 1
    rows = copy.deepcopy(events) + [{"kind": "final", "completed": True,
                                     "guest_writes": 0, "cpu_changes": 0, "driver_errors": 0,
                                     "hook_hits": sum(c["total"] for c in counts.values()),
                                     "wrong_bank_sample_limit": 16, "hook_counts": counts}]
    for i, row in enumerate(rows, 1):
        row.update(ord=i, frame=100 + i)
    return rows


def good_trace():
    return complete([hit(), hit("next"), hit("last")])


def test_good_trace_passes():
    assert P.evaluate(good_trace()) == (True, [])
    assert P.verdict([]) == "PASS"


def test_gate_alone_satisfies_optional_sibling_hits():
    assert P.evaluate(complete([hit()])) == (True, [])


@pytest.mark.parametrize(("event", "reason"), [
    (hit(pc=0x47E0), "PC_IS_NEXT_INSTRUCTION"),
    (hit(pc=0x480D), "PC_OTHER"),
    (hit(pc=None), "PC_OTHER"),
    (hit(bank=0x10), "WRONG_BANK_HIT"),
    (hit(matched=False), "WRONG_BANK_HIT"),
    (hit(matched=1), "WRONG_BANK_HIT"),
    (hit(cur_ot_mon=1, cur_party_mon=0), "BAD_SELECTED_INDEX"),
    (hit(cur_ot_mon=3, cur_party_mon=3), "BAD_SELECTED_INDEX"),
    (hit(cur_ot_mon=-1, cur_party_mon=-1), "BAD_SELECTED_INDEX"),
    (hit(ot_party_count=0), "BAD_SELECTED_INDEX"),
    (hit(ot_party_count=7), "BAD_SELECTED_INDEX"),
    (hit(cur_ot_mon=None), "BAD_SELECTED_INDEX"),
    (hit(cur_ot_mon=False), "BAD_SELECTED_INDEX"),
    (hit(mode=1), "NOT_TRAINER_BATTLE"),
    (hit(site_bytes="228bd2fa0cd1"), "SITE_BYTES_MISMATCH"),
    (hit(site_bytes="218bd2"), "SITE_BYTES_MISMATCH"),
    (hit(site_bytes="not hex"), "SITE_BYTES_MISMATCH"),
    (hit(site_bytes=None), "SITE_BYTES_MISMATCH"),
    (hit(hook_addr=0x47E0), "HOOK_ADDRESS"),
])
def test_each_gate_defect_fails_with_specific_reason(event, reason):
    ok, reasons = P.evaluate(complete([event]))
    assert not ok
    assert any(r.startswith(reason + ":") for r in reasons), reasons


def test_every_qualified_gate_must_agree_not_just_first():
    ok, reasons = P.evaluate(complete([hit(), hit(pc=0x47E0)]))
    assert not ok
    assert P.verdict(reasons) == "PC_IS_NEXT_INSTRUCTION"
    assert any("0x47e0" in r for r in reasons)


def test_other_pc_verdict_carries_observed_value():
    ok, reasons = P.evaluate(complete([hit(pc=0x1234)]))
    assert not ok
    assert P.verdict(reasons) == "PC_OTHER"
    assert any("0x1234" in r for r in reasons)


@pytest.mark.parametrize("events", [[], [hit("last")], [hit("next"), hit("last")],
                                    [hit(bank=0x10, matched=False)]])
def test_zero_qualified_gate_hits_are_open_not_pass(events):
    ok, reasons = P.evaluate(complete(events))
    assert not ok
    assert P.verdict(reasons) == "NO_GATE_HIT"
    assert any("OPEN" in r for r in reasons)


def test_wrong_bank_callbacks_are_not_counted_or_pc_checked():
    trace = complete([hit(bank=0x10, matched=False, pc=0x1234), hit()])
    assert P.evaluate(trace) == (True, [])


def test_optional_next_callback_pc_must_be_own_address():
    ok, reasons = P.evaluate(complete([hit(), hit("next", pc=0x47E1)]))
    assert not ok
    assert any(r.startswith("NEXT_PC_MISMATCH:") for r in reasons)


@pytest.mark.parametrize(("kind", "reason"), [("guest_write", "GUEST_WRITE"),
                                               ("cpu_change", "CPU_CHANGE"),
                                               ("overflow", "TRACE_OVERFLOW"),
                                               ("driver_error", "DRIVER_ERROR")])
def test_mutation_and_censored_recordings_fail(kind, reason):
    ok, reasons = P.evaluate(complete([hit(), {"kind": kind}]))
    assert not ok
    assert any(r.startswith(reason + ":") for r in reasons)


def test_missing_final_cannot_pass_even_with_good_gate():
    ok, reasons = P.evaluate([hit()])
    assert not ok
    assert any(r.startswith("INCOMPLETE_TRACE:") for r in reasons)


@pytest.mark.parametrize("field", ["guest_writes", "cpu_changes"])
def test_mutation_counter_fails_even_if_event_is_missing(field):
    trace = good_trace()
    trace[-1][field] = 1
    ok, reasons = P.evaluate(trace)
    assert not ok
    assert any(r.startswith("PROBE_MUTATION:") for r in reasons)


@pytest.mark.parametrize("trace", [None, {}, [None], ["gate"]])
def test_malformed_trace_fails_closed(trace):
    assert P.evaluate(trace) == (False, ["INVALID_TRACE: expected a list of event objects"])


def route_file(tmp_path, data):
    path = tmp_path / "route.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_native_route_parsing_and_exact_frame_boundary(tmp_path):
    steps = [{"frames": 3, "buttons": []}, {"frames": 2, "buttons": ["Up", "A"]}]
    assert P.parse_route(route_file(tmp_path, {"steps": steps}), 5) == steps
    assert P.parse_route(None, 5) == []


@pytest.mark.parametrize("data", [
    [], {"steps": {}}, {"steps": [], "warp": [20, 1]},
    {"steps": [{"frames": 1, "buttons": [], "write": [0xD235, 9]}]},
    {"steps": [{"frames": 0, "buttons": []}]},
    {"steps": [{"frames": -1, "buttons": []}]},
    {"steps": [{"frames": True, "buttons": []}]},
    {"steps": [{"frames": 1.5, "buttons": []}]},
    {"steps": [{"frames": 1, "buttons": "A"}]},
    {"steps": [{"frames": 1, "buttons": ["Reset"]}]},
    {"steps": [{"frames": 1, "buttons": ["A", "A"]}]},
    {"steps": [{"frames": 1, "buttons": [{}]}]},
])
def test_route_rejects_invalid_or_synthetic_steps(tmp_path, data):
    with pytest.raises(ValueError):
        P.parse_route(route_file(tmp_path, data), 10)


def test_route_cannot_overrun_observation_bound(tmp_path):
    path = route_file(tmp_path, {"steps": [{"frames": 11, "buttons": []}]})
    with pytest.raises(ValueError, match="exceeds --frames"):
        P.parse_route(path, 10)


def test_argument_validation_accepts_played_fixture_and_route(tmp_path):
    fixture = tmp_path / "native.SaveRAM"
    fixture.write_bytes(b"native fixture")
    route = route_file(tmp_path, {"steps": [{"frames": 3, "buttons": ["Start"]}]})
    args = P.parse_args(["--setup", "played", "--fixture", str(fixture), "--route", str(route), "--frames", "3"])
    assert args.fixture == fixture
    assert args.steps == [{"frames": 3, "buttons": ["Start"]}]


@pytest.mark.parametrize("extra", [[], ["--setup", "synthetic"], ["--frames", "0"],
                                    ["--trace-cap", "0"], ["--timeout", "-1"]])
def test_arguments_reject_missing_setup_synthetic_or_unbounded_run(tmp_path, extra):
    fixture = tmp_path / "native.SaveRAM"
    fixture.write_bytes(b"fixture")
    prefix = [] if not extra else ["--setup", "played"]
    with pytest.raises(SystemExit) as exc:
        P.parse_args(prefix + ["--fixture", str(fixture)] + extra)
    assert exc.value.code == 2


def test_missing_fixture_is_argument_error(tmp_path):
    with pytest.raises(SystemExit) as exc:
        P.parse_args(["--setup", "played", "--fixture", str(tmp_path / "missing.SaveRAM")])
    assert exc.value.code == 2


def test_bad_route_is_argument_error(tmp_path):
    fixture = tmp_path / "native.SaveRAM"
    fixture.write_bytes(b"fixture")
    route = route_file(tmp_path, {"steps": [{"frames": 4, "buttons": []}]})
    with pytest.raises(SystemExit) as exc:
        P.parse_args(["--setup", "played", "--fixture", str(fixture), "--route", str(route), "--frames", "3"])
    assert exc.value.code == 2


def synthetic_rom(site=b"\x21\x8b\xd2\xfa\x0c\xd1"):
    rom = bytearray(16 * 0x4000)
    offset = 15 * 0x4000 + 0x47DD - 0x4000
    rom[offset:offset + len(site)] = site
    return bytes(rom)


def test_pinned_rom_site_is_bank_qualified():
    rom = bytearray(synthetic_rom())
    rom[0x47DD:0x47DD + 3] = b"bad"  # unqualified/raw-address access must not be used
    assert P.check_site_bytes(bytes(rom)) == "218bd2fa0cd1"


@pytest.mark.parametrize("rom", [synthetic_rom(b"\x22\x8b\xd2\xfa\x0c\xd1"), b"", synthetic_rom()[:247776]],
                         ids=["wrong-site-bytes", "empty", "truncated"])  # ids: ROM images overflow pytest's env var
def test_staged_site_mismatch_or_truncation_prevents_launch(rom):
    with pytest.raises(ValueError, match="pinned prefix"):
        P.check_site_bytes(rom)


@pytest.mark.parametrize("text", ["", "crashed", "RESULT: FAIL rival-gate-probe recording (1 checks failed)",
                                   "RESULT: PASS another-probe", "RESULT: PASS rival-gate-probe recording x\nRESULT: PASS rival-gate-probe recording x"])
def test_missing_failed_or_ambiguous_result_fails_closed(text):
    assert P.recording_reasons(text)[0].startswith("INCOMPLETE_RECORDING:")


def test_successful_recording_marker_does_not_override_zero_hits():
    assert P.recording_reasons("RESULT: PASS rival-gate-probe recording (0 checks failed) frame 12000") == []
    ok, reasons = P.evaluate(complete([]))
    assert not ok
    assert P.verdict(reasons) == "NO_GATE_HIT"


@pytest.mark.parametrize("elapsed", [600, 601])
def test_late_successful_result_is_still_a_deadline_failure(elapsed):
    text = "RESULT: PASS rival-gate-probe recording (0 checks failed) frame 12000"
    reasons = P.recording_reasons(text, elapsed, 600)
    assert any(r.startswith("DEADLINE:") for r in reasons)


def test_result_before_deadline_is_eligible_for_oracle():
    text = "RESULT: PASS rival-gate-probe recording (0 checks failed) frame 12000"
    assert P.recording_reasons(text, 599, 600) == []


def test_lua55_loads_driver_without_executing_it():
    from lupa.lua55 import LuaRuntime

    lua = LuaRuntime(unpack_returned_tuples=True)
    load = lua.eval("function(source) local fn, err = load(source, '@rival_gate_probe.lua'); return fn ~= nil, err end")
    source = (REPO / "tools/polished_live/rival_gate_probe.lua").read_text(encoding="utf-8")
    ok, error = load(source)
    assert ok, error


def _lua_value(value):
    """Match the production encoder's empty-table-as-array representation."""
    if not hasattr(value, "items"):
        return value
    items = dict(value.items())
    if set(items) == set(range(1, len(items) + 1)):
        return [_lua_value(items[i]) for i in range(1, len(items) + 1)]
    return {key: _lua_value(item) for key, item in items.items()}


MOCK_DRIVER = r"""
local frame, bank, pc = 0, 15, 0x47DD
cur_mode = 2
local hooks = {}
output, finished, recording_ok, recording_name = nil, false, nil, nil
os.getenv = function() return "mock" end
emu = {
    framecount = function() return frame end,
    getregister = function(name) return name == "PC" and pc or 0xC0DC end,
}
client = {speedmode = function() end}
local gate_offset = 15 * 0x4000 + 0x47DD - 0x4000
local gate_bytes = {0x21, 0x8B, 0xD2, 0xFA, 0x0C, 0xD1}
memory = {read_u8 = function(addr)
    return gate_bytes[addr - gate_offset + 1] or 0
end}
local L = {
    RUN = "mock",
    ROOT = "mock",
    SYM = {hBattleTurn = {0, 0xFFD1}},
    slurp = function() return "config" end,
    bus = function(addr) return addr == 0xFFD1 and (mock_side or 0) or bank end,
    rw = function(name)
        if name == "wBattleMode" then return cur_mode end
        if name == "wOTPartyCount" then return 3 end
        if name == "wOtherTrainerClass" then return mock_class or 0 end
        if name == "wOtherTrainerID" then return mock_id or 0 end
        return 0
    end,
    hex = function(bytes)
        local out = {}
        for _, byte in ipairs(bytes) do out[#out + 1] = string.format("%02x", byte) end
        return table.concat(out)
    end,
    hook_at = function(name, expected_bank, addr, callback)
        assert(expected_bank == 15)
        hooks[#hooks + 1] = {addr = addr, callback = callback}
    end,
    check = function(name, ok) recording_ok, recording_name = ok, name end,
    finish = function() finished = true end,
}
L.json = {
    decode = function()
        return {steps = route_frames and {{frames = route_frames, buttons = {}}} or {},
                frames = mock_frames or 1, trace_cap = mock_cap,
                setup = mock_setup, disclosure_sha256 = mock_disc, expected = {trainer_id=3}}
    end,
    encode = function(trace)
        if encoder_mode == "nil" then return nil end
        if encoder_mode == "throw" then error('encoder "failed"\nagain', 0) end
        return encode_trace(trace)
    end,
}
L.frame = function()
    frame = frame + 1
    if mode_script and mode_script[frame] ~= nil then cur_mode = mode_script[frame] end
    for _, hook in ipairs(hooks) do
        for i = 1, wrong_callbacks do
            bank, pc = (i % 2 == 0 and 16 or 17), 0x1234
            hook.callback(false)
        end
        bank, pc = 15, hook.addr
        hook.callback(true)
    end
    if wrong_pc then
        bank, pc = 15, 0x47E0
        hooks[1].callback(true)
    end
    if frame_error then error('driver "failed"\nagain', 0) end
end
dofile = function(path)
    if path and path:match("recording_guard.lua$") then return RECORDING_GUARD end
    return L
end
io.open = function()
    return {
        write = function(self, text)
            assert(type(text) == "string", "write must receive JSON text")
            output = text
            return self
        end,
        close = function() end,
    }
end
"""


def run_mock_driver(*, encoder="normal", callbacks=7001, wrong_pc=False, cap=5, frame_error=False,
                    frames=1, modes=None, setup=None, disclosure=None, with_name=False, route_frames=None,
                    side=0, klass=0, tid=0, mutant=False):
    from lupa.lua55 import LuaRuntime

    lua = LuaRuntime(unpack_returned_tuples=True)
    lua.globals().RECORDING_GUARD = lua.execute(
        (REPO / "tools/polished_live/recording_guard.lua").read_text(encoding="utf-8"))
    globals_ = lua.globals()
    globals_.mock_side, globals_.mock_class, globals_.mock_id = side, klass, tid
    globals_.encoder_mode = encoder
    globals_.wrong_callbacks = callbacks
    globals_.wrong_pc = wrong_pc
    globals_.mock_cap = cap
    globals_.frame_error = frame_error
    globals_.mock_frames = frames
    globals_.route_frames = route_frames
    globals_.mode_script = lua.table_from(modes) if modes else None
    globals_.mock_setup = setup
    globals_.mock_disc = disclosure
    globals_.encode_trace = lambda value: json.dumps(_lua_value(value))
    lua.execute(MOCK_DRIVER)
    source = (REPO / "tools/polished_live/rival_gate_probe.lua").read_text(encoding="utf-8")
    if mutant:
        assert source.count("and side == 1 and mode == 2") == 1
        source = source.replace("and side == 1 and mode == 2", "and side == 1")
    lua.execute(source)
    assert globals_.finished is True
    result = json.loads(globals_.output), globals_.recording_ok
    return result + (globals_.recording_name,) if with_name else result


def test_wrong_bank_flood_preserves_qualified_rows_and_exact_callback_totals():
    trace, recorded = run_mock_driver()
    assert recorded is True
    final = trace[-1]
    assert final["wrong_bank_sample_limit"] == 16
    assert final["hook_hits"] == 3 * 7002
    for name in P.HOOKS:
        rows = [row for row in trace if row["kind"] == name]
        samples = [row for row in rows if row["matched"] is False]
        qualified = [row for row in rows if row["raw_qualified"] is True]
        assert len(samples) == 16
        assert len(qualified) == 1
        assert qualified[0]["pc"] == P.HOOKS[name]
        assert qualified[0]["hook_addr"] == P.HOOKS[name] and qualified[0]["bank"] == P.BANK
        assert qualified[0]["mode"] == 2 and qualified[0]["cur_ot_mon"] == 0
        assert qualified[0]["site_bytes"] == "218bd2fa0cd1"
        assert final["hook_counts"][name] == {
            "total": 7002, "qualified": 1, "wrong_pc": 0,
            "wrong_banks": {"16": 3500, "17": 3501}, "wrong_bank_samples": 16,
        }
    assert P.evaluate(trace) == (True, [])


def test_wrong_pc_after_wrong_bank_flood_keeps_diagnostic_verdict():
    trace, recorded = run_mock_driver(wrong_pc=True)
    assert recorded is True
    bad = [row for row in trace if row["kind"] == "gate" and row["matched"] and not row["raw_qualified"]]
    assert len(bad) == 1
    assert bad[0]["pc"] == P.NEXT
    assert bad[0]["cur_ot_mon"] == 0 and bad[0]["site_bytes"] == "218bd2fa0cd1"
    assert trace[-1]["hook_counts"]["gate"]["wrong_pc"] == 1
    ok, reasons = P.evaluate(trace)
    assert not ok and P.verdict(reasons) == "PC_IS_NEXT_INSTRUCTION"


def test_capacity_boundary_still_fails_and_counts_all_callbacks():
    trace, recorded = run_mock_driver(cap=1)
    assert recorded is True
    assert trace[-1]["hook_hits"] == 3 * 7002
    assert sum(row["kind"] == "overflow" for row in trace) == 1
    ok, reasons = P.evaluate(trace)
    assert not ok and any(reason.startswith("TRACE_OVERFLOW:") for reason in reasons)


@pytest.mark.parametrize("encoder", ["nil", "throw"])
def test_encoder_failure_emits_readable_independent_fallback_and_finishes(encoder):
    trace, recorded = run_mock_driver(encoder=encoder)
    assert recorded is False
    assert [row["kind"] for row in trace] == ["driver_error", "final"]
    assert trace[0]["error"]
    if encoder == "throw":
        assert trace[0]["error"] == 'encoder "failed"\nagain'
    final = trace[-1]
    assert final["completed"] is False and final["driver_errors"] == 1
    assert final["hook_hits"] == 3 * 7002
    assert final["wrong_bank_sample_limit"] == 16
    assert all(c["total"] == 7002 and c["wrong_bank_samples"] == 16 for c in final["hook_counts"].values())
    ok, reasons = P.evaluate(trace)
    assert not ok and any(reason.startswith("DRIVER_ERROR:") for reason in reasons)


def test_driver_failure_emits_final_with_preserved_totals_and_finishes():
    trace, recorded = run_mock_driver(frame_error=True)
    assert recorded is False
    assert trace[-1]["completed"] is False and trace[-1]["driver_errors"] == 1
    assert trace[-1]["hook_hits"] == 3 * 7002
    assert trace[-2]["error"] == 'driver "failed"\nagain'
    ok, reasons = P.evaluate(trace)
    assert not ok and any(reason.startswith("DRIVER_ERROR:") for reason in reasons)


@pytest.mark.parametrize("field", [
    "total", "qualified", "wrong_pc", "wrong_banks", "wrong_bank_samples",
])
def test_missing_individual_hook_counter_is_rejected(field):
    trace = good_trace()
    del trace[-1]["hook_counts"]["gate"][field]
    assert P.evaluate(trace)[0] is False


@pytest.mark.parametrize("field", ["total", "qualified", "wrong_pc", "wrong_bank_samples"])
@pytest.mark.parametrize("value", [True, -1, 1.5, 999])
def test_malformed_or_overstated_individual_hook_counter_is_rejected(field, value):
    trace = good_trace()
    trace[-1]["hook_counts"]["gate"][field] = value
    assert P.evaluate(trace)[0] is False


@pytest.mark.parametrize("field", ["hook_counts", "wrong_bank_sample_limit", "hook_hits"])
def test_missing_mandatory_final_counter_is_rejected(field):
    trace = good_trace()
    del trace[-1][field]
    assert P.evaluate(trace)[0] is False


def test_overstated_qualified_count_rejected_even_with_balanced_total():
    trace = good_trace()
    trace[-1]["hook_counts"]["gate"]["qualified"] += 1
    trace[-1]["hook_counts"]["gate"]["total"] += 1
    trace[-1]["hook_hits"] += 1
    assert P.evaluate(trace)[0] is False


def test_observed_wrong_bank_count_cannot_exceed_aggregate_claim():
    trace = complete([hit(bank=16, matched=False), hit()])
    trace[-1]["hook_counts"]["gate"]["wrong_banks"]["16"] = 0
    assert P.evaluate(trace)[0] is False


def test_empty_array_wrong_bank_map_matches_production_encoder():
    trace = good_trace()
    for counts in trace[-1]["hook_counts"].values():
        counts["wrong_banks"] = []
    assert P.evaluate(trace) == (True, [])


def test_optional_last_callback_wrong_pc_cannot_pass():
    ok, reasons = P.evaluate(complete([hit(), hit("last", pc=P.LAST + 1)]))
    assert not ok
    assert any(reason.startswith("LAST_PC_MISMATCH:") for reason in reasons)


@pytest.mark.parametrize("value", [True, -1, 1.5, 999])
def test_legacy_hook_hits_counter_must_match_callback_totals(value):
    trace = good_trace()
    trace[-1]["hook_hits"] = value
    assert P.evaluate(trace)[0] is False


@pytest.mark.parametrize("wrong_banks", [
    {"16": True}, {"16": -1}, {"16": 1.5}, {"16": 999}, {"0x10": 1}, [1],
])
def test_wrong_bank_map_rejects_invalid_or_inconsistent_counts(wrong_banks):
    trace = complete([hit(bank=16, matched=False), hit()])
    trace[-1]["hook_counts"]["gate"]["wrong_banks"] = wrong_banks
    assert P.evaluate(trace)[0] is False


def test_wrong_bank_retained_sample_count_cannot_be_overstated():
    trace = complete([hit(bank=16, matched=False), hit()])
    trace[-1]["hook_counts"]["gate"]["wrong_bank_samples"] = 2
    assert P.evaluate(trace)[0] is False


def test_wrong_pc_consumes_protected_capacity_without_losing_diagnostics():
    trace, recorded = run_mock_driver(wrong_pc=True, cap=4)
    assert recorded is True
    bad = [row for row in trace if row["kind"] == "gate" and row["matched"] and not row["raw_qualified"]]
    assert len(bad) == 1 and bad[0]["pc"] == P.NEXT and bad[0]["mode"] == 2
    assert sum(row["kind"] == "overflow" for row in trace) == 1
    ok, reasons = P.evaluate(trace)
    assert not ok and P.verdict(reasons) == "PC_IS_NEXT_INSTRUCTION"
    assert any(reason.startswith("TRACE_OVERFLOW:") for reason in reasons)


# ══ Card B2: SYNTH setup mode, disclosure binding, the stronger rival-card oracle, battle-mode timeline ═══════════
#
# RED CONTROLS (each negative case below is a separate parametrize id):
#   class outside {1B..1F}, id out of range, wrong fixture identity     -> test_rival_gate_defects_fail_with_specific_reason
#   wild gate row (mode 1, class 0) never qualifies                      -> test_a_wild_battle_row_never_qualifies_as_a_rival_gate
#   missing / misordered / mismatched next+last witnesses                -> test_rival_witness_defects_fail
#   setup binding absent / wrong                                         -> test_setup_binding_is_required
#   wrong-bank hits are counted and excluded                             -> test_wrong_bank_rows_are_excluded_from_the_rival_oracle
#   wild control with no observed wild mode / with a qualified hit       -> test_wild_control_defects_fail
#   synth without a matching disclosure                                  -> test_synth_requires_a_bound_disclosure
DISC = "ab" * 32
SITE6 = "218bd2fa0cd1"


def rival_hit(kind="gate", **fields):
    base = {"battle_turn": 1, "trainer_class": 0x1B, "trainer_id": 3, "ot_party_count": 2, "mode": 2, "cur_ot_mon": 0,
            "cur_party_mon": 0, "site_bytes": SITE6}
    base.update(fields)
    return hit(kind, **base)


def trio(**fields):
    return [rival_hit(kind, **fields) for kind in ("gate", "next", "last")]


def mode_row(mode, **fields):
    row = {"kind": "mode", "mode": mode, "trainer_class": 0, "trainer_id": 0, "ot_party_count": 0}
    row.update(fields)
    return row


def bound(trace, setup="synth", disc=DISC):
    final = trace[-1]
    if setup is not None:
        final["setup"] = setup
    if disc is not None:
        final["disclosure_sha256"] = disc
    return trace


def rival_trace(events=None):
    return bound(complete(trio() if events is None else events))


def rival_eval(trace, **kw):
    kw.setdefault("disclosure_sha256", DISC)
    return P.evaluate_rival(trace, **kw)


def has(reasons, prefix):
    return any(r.startswith(prefix + ":") for r in reasons)


def test_rival_constants_are_the_documented_five_classes_and_fixture_identity():
    assert frozenset({0x1B, 0x1C, 0x1D, 0x1E, 0x1F}) == P.RIVAL_CLASSES
    assert P.FIXTURE_EXPECT == {"trainer_class": 0x1B, "trainer_id": 3, "enemy_party": 2}
    assert P.PINNED_SITE_HEX == SITE6


def test_good_rival_trace_passes_and_generic_oracle_is_unchanged():
    assert rival_eval(rival_trace()) == (True, [])
    assert P.verdict([]) == "PASS"
    # played / generic oracle keeps accepting non-rival classes and optional witnesses (the pre-existing contract)
    assert P.evaluate(good_trace()) == (True, [])
    assert P.evaluate(complete([hit()])) == (True, [])
    assert P.evaluate(rival_trace()) == (True, [])


@pytest.mark.parametrize(("events", "prefix"), [
    (trio(trainer_class=9), "NOT_RIVAL_CLASS"),
    (trio(trainer_class=0x1A), "NOT_RIVAL_CLASS"),
    (trio(trainer_class=0x20), "NOT_RIVAL_CLASS"),
    (trio(trainer_class=0), "NOT_RIVAL_CLASS"),
    (trio(trainer_class=None), "NOT_RIVAL_CLASS"),
    (trio(trainer_class=True), "NOT_RIVAL_CLASS"),
    (trio(trainer_class="1b"), "NOT_RIVAL_CLASS"),
    (trio(trainer_class=0x1C), "FIXTURE_IDENTITY_MISMATCH"),
    (trio(trainer_class=0x1F), "FIXTURE_IDENTITY_MISMATCH"),
    (trio(trainer_id=4), "FIXTURE_IDENTITY_MISMATCH"),
    (trio(trainer_id=0), "FIXTURE_IDENTITY_MISMATCH"),
    (trio(ot_party_count=3), "FIXTURE_IDENTITY_MISMATCH"),
    (trio(ot_party_count=1), "FIXTURE_IDENTITY_MISMATCH"),
    (trio(trainer_id=None), "BAD_TRAINER_ID"),
    (trio(trainer_id=-1), "BAD_TRAINER_ID"),
    (trio(trainer_id=256), "BAD_TRAINER_ID"),
    (trio(trainer_id=True), "BAD_TRAINER_ID"),
    (trio(trainer_id=3.0), "BAD_TRAINER_ID"),
    (trio(site_bytes="218bd2ffffff"), "SITE_BYTES_NOT_PINNED"),
    (trio(site_bytes="218bd2fa0cd2"), "SITE_BYTES_NOT_PINNED"),
    (trio(site_bytes="218bd2"), "SITE_BYTES_MISMATCH"),
    (trio(site_bytes=None), "SITE_BYTES_MISMATCH"),
    (trio(cur_ot_mon=1, cur_party_mon=0), "BAD_SELECTED_INDEX"),
    (trio(cur_ot_mon=2, cur_party_mon=2), "BAD_SELECTED_INDEX"),
    (trio(cur_ot_mon=False, cur_party_mon=False), "BAD_SELECTED_INDEX"),
    (trio(mode=1), "NOT_TRAINER_BATTLE"),
    (trio(mode=0), "NOT_TRAINER_BATTLE"),
    ([rival_hit(pc=0x47E0), rival_hit("next"), rival_hit("last")], "PC_IS_NEXT_INSTRUCTION"),
    ([rival_hit(pc=0x1234), rival_hit("next"), rival_hit("last")], "PC_OTHER"),
], ids=["class-9", "class-1a", "class-20", "class-0", "class-none", "class-bool", "class-str",
        "class-1c-not-fixture", "class-1f-not-fixture", "id-4", "id-0", "party-3", "party-1",
        "id-none", "id-neg", "id-256", "id-bool", "id-float", "site-trailer", "site-last-byte",
        "site-3-bytes", "site-none", "idx-differ", "idx-out-of-range", "idx-bool", "mode-wild",
        "mode-none", "pc-next-instruction", "pc-other"])
def test_rival_gate_defects_fail_with_specific_reason(events, prefix):
    ok, reasons = rival_eval(rival_trace(events))
    assert not ok
    assert has(reasons, prefix), reasons


@pytest.mark.parametrize("klass", [0x1B, 0x1C, 0x1D, 0x1E, 0x1F], ids=["rival0", "rival1", "rival2", "lyra1", "lyra2"])
@pytest.mark.parametrize("tid", [0, 1, 3, 0x1C, 255], ids=["id0", "id1", "id3", "id1c", "id255"])
def test_five_class_gate_accepts_any_valid_id_and_does_not_test_ids_against_classes(klass, tid):
    ok, reasons = rival_eval(rival_trace(trio(trainer_class=klass, trainer_id=tid)),
                             expected={"trainer_class": klass, "trainer_id": tid, "enemy_party": 2})
    assert (ok, reasons) == (True, [])


@pytest.mark.parametrize("count", [1, 2, 3, 6], ids=["n1", "n2", "n3", "n6"])
def test_five_class_gate_accepts_any_valid_enemy_party_size(count):
    assert rival_eval(rival_trace(trio(ot_party_count=count)),
                      expected={"trainer_class": 0x1B, "trainer_id": 3, "enemy_party": count}) == (True, [])


@pytest.mark.parametrize("fields", [{"trainer_class": 9}, {"trainer_class": 0x20}, {"trainer_class": 0}],
                         ids=["class-9", "class-20", "class-0"])
def test_five_class_gate_still_refuses_classes_outside_the_set_without_a_fixture_identity(fields):
    ok, reasons = rival_eval(rival_trace(trio(**fields)), expected=None)
    assert not ok and has(reasons, "NOT_RIVAL_CLASS")


def test_a_wild_battle_row_never_qualifies_as_a_rival_gate():
    wild = [hit(mode=1, trainer_class=0, trainer_id=0, ot_party_count=1, site_bytes=SITE6)]
    ok, reasons = rival_eval(rival_trace(wild))
    assert not ok
    assert has(reasons, "NOT_TRAINER_BATTLE") and has(reasons, "NOT_RIVAL_CLASS") and has(reasons, "NO_RIVAL_GATE")
    # even a wild row dressed with a rival class and a trainer-like record is not a rival gate while mode says wild
    dressed = [rival_hit(mode=1), rival_hit("next"), rival_hit("last")]
    ok, reasons = rival_eval(rival_trace(dressed))
    assert not ok and has(reasons, "NOT_TRAINER_BATTLE") and has(reasons, "NO_RIVAL_GATE")


@pytest.mark.parametrize(("events", "prefix"), [
    ([rival_hit()], "WITNESS_MISSING"),
    ([rival_hit(), rival_hit("next")], "WITNESS_MISSING"),
    ([rival_hit(), rival_hit("last")], "WITNESS_MISSING"),
    ([rival_hit("next"), rival_hit(), rival_hit("last")], "WITNESS_MISSING"),
    ([rival_hit(), rival_hit("last"), rival_hit("next")], "WITNESS_MISSING"),
    ([rival_hit(), rival_hit("next", bank=0x10, matched=False), rival_hit("last")], "WITNESS_MISSING"),
    ([rival_hit(), rival_hit("next"), rival_hit("last", bank=0x10, matched=False)], "WITNESS_MISSING"),
    ([rival_hit(), rival_hit("next", bank=0x10, matched=True, qualified=True), rival_hit("last")], "WRONG_BANK_HIT"),
    ([rival_hit(), rival_hit("next", pc=0x47E1), rival_hit("last")], "NEXT_PC_MISMATCH"),
    ([rival_hit(), rival_hit("next"), rival_hit("last", pc=0x480E)], "LAST_PC_MISMATCH"),
    ([rival_hit(), rival_hit("next", trainer_class=0x1C), rival_hit("last")], "WITNESS_MISMATCH"),
    ([rival_hit(), rival_hit("next"), rival_hit("last", trainer_class=0x1C)], "WITNESS_MISMATCH"),
    ([rival_hit(), rival_hit("next", trainer_id=4), rival_hit("last")], "WITNESS_MISMATCH"),
    ([rival_hit(), rival_hit("next"), rival_hit("last", trainer_id=4)], "WITNESS_MISMATCH"),
    ([rival_hit(), rival_hit("next", ot_party_count=3), rival_hit("last")], "WITNESS_MISMATCH"),
    ([rival_hit(), rival_hit("next", cur_ot_mon=1, cur_party_mon=1), rival_hit("last")], "WITNESS_MISMATCH"),
    ([rival_hit(), rival_hit("next"), rival_hit("last", cur_ot_mon=1, cur_party_mon=1)], "WITNESS_MISMATCH"),
    ([rival_hit(), rival_hit("next", cur_party_mon=1), rival_hit("last")], "WITNESS_MISMATCH"),
    ([rival_hit(), rival_hit("next", mode=1), rival_hit("last")], "WITNESS_MISMATCH"),
    ([rival_hit(), rival_hit("next"), rival_hit("last", mode=0)], "WITNESS_MISMATCH"),
    ([rival_hit(), rival_hit("next", site_bytes="218bd2ffffff"), rival_hit("last")], "SITE_BYTES_NOT_PINNED"),
], ids=["gate-only", "no-last", "no-next", "next-before-gate", "last-before-next", "next-wrong-bank",
        "last-wrong-bank", "next-claims-match-in-wrong-bank", "next-pc", "last-pc", "next-class", "last-class", "next-id", "last-id",
        "next-count", "next-idx", "last-idx", "next-party-idx", "next-wild", "last-no-mode", "next-site"])
def test_rival_witness_defects_fail(events, prefix):
    ok, reasons = rival_eval(rival_trace(events))
    assert not ok
    assert has(reasons, prefix), reasons


def test_the_first_rival_gate_is_the_one_whose_witness_chain_is_required():
    second = [rival_hit(cur_ot_mon=1, cur_party_mon=1)]
    chain = [rival_hit(), rival_hit("next"), rival_hit("last")] + second
    assert rival_eval(rival_trace(chain)) == (True, [])
    # the later gate does not satisfy the chain for the earlier one
    ok, reasons = rival_eval(rival_trace([rival_hit(), rival_hit("next", cur_ot_mon=1, cur_party_mon=1),
                                           rival_hit("last", cur_ot_mon=1, cur_party_mon=1)]))
    assert not ok and has(reasons, "WITNESS_MISMATCH")


@pytest.mark.parametrize(("setup", "disc", "arg"), [
    (None, DISC, DISC), ("played", DISC, DISC), ("synthetic", DISC, DISC), (None, None, DISC),
    ("synth", None, DISC), ("synth", "cd" * 32, DISC), ("synth", DISC, "cd" * 32),
    ("synth", DISC, None), ("synth", DISC, "not hex"), ("synth", "not hex", "not hex"),
    ("synth", DISC.upper(), DISC),
], ids=["no-setup", "played", "synthetic", "no-binding", "no-disclosure", "trace-differs", "arg-differs",
        "arg-none", "arg-not-hex", "both-not-hex", "case-differs"])
def test_setup_binding_is_required(setup, disc, arg):
    ok, reasons = P.evaluate_rival(bound(complete(trio()), setup=setup, disc=disc), disclosure_sha256=arg)
    assert not ok
    assert has(reasons, "SETUP_BINDING"), reasons


@pytest.mark.parametrize("events", [
    trio() + [{"kind": "guest_write"}], trio() + [{"kind": "cpu_change"}], trio() + [{"kind": "overflow"}],
    trio() + [{"kind": "driver_error"}],
], ids=["guest-write", "cpu-change", "overflow", "driver-error"])
def test_rival_oracle_inherits_every_recording_integrity_failure(events):
    ok, reasons = rival_eval(rival_trace(events))
    assert not ok and reasons


def test_rival_oracle_inherits_missing_final_and_counter_checks():
    assert not rival_eval(trio())[0]                                  # no final
    trace = rival_trace()
    trace[-1]["guest_writes"] = 1
    assert has(rival_eval(trace)[1], "PROBE_MUTATION")
    trace = rival_trace()
    trace[-1]["hook_counts"]["gate"]["total"] += 1
    assert not rival_eval(trace)[0]
    assert rival_eval(None) == (False, ["INVALID_TRACE: expected a list of event objects"])


def test_wrong_bank_rows_are_excluded_from_the_rival_oracle():
    wrong = [rival_hit(bank=0x10, matched=False), rival_hit("next", bank=0x11, matched=False),
             rival_hit("last", bank=0x10, matched=False)]
    ok, reasons = rival_eval(rival_trace(wrong))
    assert not ok and P.verdict(reasons) == "NO_GATE_HIT"
    assert not has(reasons, "WRONG_BANK_HIT") and not has(reasons, "NOT_RIVAL_CLASS")
    # wrong-bank samples mixed into a genuine trace are counted and excluded, the genuine chain still passes
    mixed = [rival_hit(bank=0x10, matched=False, trainer_class=9), *trio()]
    assert rival_eval(rival_trace(mixed)) == (True, [])
    # a wrong-bank row with a rival record never supplies the witness for a real gate
    only_gate = [rival_hit(), rival_hit("next", bank=0x10, matched=False), rival_hit("last", bank=0x10, matched=False)]
    assert has(rival_eval(rival_trace(only_gate))[1], "WITNESS_MISSING")


def test_zero_hits_and_non_gate_hits_stay_open_not_pass_for_the_rival_card_too():
    for events in ([], [rival_hit("next"), rival_hit("last")]):
        ok, reasons = rival_eval(rival_trace(events))
        assert not ok and P.verdict(reasons) == "NO_GATE_HIT"


def test_a_custom_staged_site_is_compared_in_full():
    other = "218bd2fa0cd2"
    assert rival_eval(rival_trace(trio(site_bytes=other)), site_hex=other) == (True, [])
    assert has(rival_eval(rival_trace(trio(site_bytes=other)))[1], "SITE_BYTES_NOT_PINNED")
    with pytest.raises(ValueError):
        rival_eval(rival_trace(), site_hex="218bd2")


def test_staged_site_must_equal_the_full_pinned_six_bytes():
    assert P.require_pinned_site(SITE6) == SITE6
    for bad in ("218bd2ffffff", "218bd2", "", "zz"):
        with pytest.raises(ValueError, match="pinned"):
            P.require_pinned_site(bad)


# ── wild-battle control ─────────────────────────────────────────────────────────────────────────

def wild_trace(events):
    return bound(complete(events))


def wild_eval(trace, **kw):
    kw.setdefault("disclosure_sha256", DISC)
    return P.evaluate_wild_control(trace, **kw)


def test_wild_control_passes_with_an_observed_wild_battle_and_no_qualified_gate():
    assert wild_eval(wild_trace([mode_row(1), mode_row(0)])) == (True, [])
    # wrong-bank callbacks are counted and excluded: they are not qualified hits
    assert wild_eval(wild_trace([mode_row(1), rival_hit(bank=0x10, matched=False)])) == (True, [])


@pytest.mark.parametrize(("events", "prefix"), [
    ([], "WILD_NOT_OBSERVED"),
    ([mode_row(0)], "WILD_NOT_OBSERVED"),
    ([mode_row(2)], "WILD_NOT_OBSERVED"),
    ([mode_row(True)], "WILD_NOT_OBSERVED"),
    ([mode_row(1.0)], "WILD_NOT_OBSERVED"),
    ([mode_row(1), rival_hit()], "WILD_QUALIFIED_HIT"),
    ([mode_row(1), mode_row(2)], "CONTROL_TRAINER_BATTLE"),
    ([mode_row(1), {"kind": "guest_write"}], "GUEST_WRITE"),
    ([mode_row(1), {"kind": "overflow"}], "TRACE_OVERFLOW"),
], ids=["no-modes", "mode-0-only", "trainer-only", "mode-bool", "mode-float", "qualified-rival-gate",
        "trainer-contamination", "guest-write", "overflow"])
def test_wild_control_defects_fail(events, prefix):
    ok, reasons = wild_eval(wild_trace(events))
    assert not ok
    assert has(reasons, prefix), reasons


def test_wild_control_needs_the_setup_binding_and_a_complete_recording():
    assert has(wild_eval(bound(complete([mode_row(1)]), setup="played"))[1], "SETUP_BINDING")
    assert has(wild_eval(wild_trace([mode_row(1)]), disclosure_sha256=None)[1], "SETUP_BINDING")
    assert has(wild_eval([mode_row(1)])[1], "INCOMPLETE_TRACE")
    assert wild_eval({}) == (False, ["INVALID_TRACE: expected a list of event objects"])


def test_verdict_reports_an_unobserved_wild_control_as_open():
    ok, reasons = wild_eval(wild_trace([]))
    assert not ok and P.verdict(reasons) == "WILD_NOT_OBSERVED"
    assert P.verdict(["NO_GATE_HIT: x", "WILD_NOT_OBSERVED: y"]) == "NO_GATE_HIT"


# ── setup mode + disclosure binding ─────────────────────────────────────────────────────────────

FIXTURE_BYTES = b"native fixture bytes"
FIXTURE_SHA = hashlib.sha256(FIXTURE_BYTES).hexdigest()
SOURCE_SHA = hashlib.sha256(b"the source it was derived from").hexdigest()


def disclosure_file(tmp_path, **override):
    doc = {"synth": True, "statement": "SYNTH Cherrygrove-scene derivative", "input_sha256": SOURCE_SHA,
           "output_sha256": FIXTURE_SHA, "pinned_input": True, "native_acceptance": "UNVERIFIED"}
    doc.update(override)
    path = tmp_path / "rival_scene.disclosure.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def test_disclosure_binds_the_derivative_fixture(tmp_path):
    path = disclosure_file(tmp_path)
    bound_doc = P.load_disclosure(path, FIXTURE_BYTES)
    assert bound_doc["role"] == "derivative"
    assert bound_doc["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert bound_doc["fixture_sha256"] == FIXTURE_SHA


def test_disclosure_also_binds_the_declared_source_as_a_control_fixture(tmp_path):
    path = disclosure_file(tmp_path)
    assert P.load_disclosure(path, b"the source it was derived from")["role"] == "source"


@pytest.mark.parametrize("override", [
    {"synth": False}, {"synth": "yes"}, {"statement": "played from the start"}, {"statement": ""},
    {"output_sha256": "00" * 32, "input_sha256": "11" * 32},
    {"output_sha256": FIXTURE_SHA.upper(), "input_sha256": "11" * 32},
    {"output_sha256": "nothex", "input_sha256": "11" * 32},
    {"output_sha256": None, "input_sha256": None},
], ids=["synth-false", "synth-str", "statement-not-synth", "statement-empty", "hash-mismatch", "hash-case",
        "hash-not-hex", "hash-null"])
def test_disclosure_that_does_not_bind_this_fixture_is_refused(tmp_path, override):
    with pytest.raises(ValueError):
        P.load_disclosure(disclosure_file(tmp_path, **override), FIXTURE_BYTES)


@pytest.mark.parametrize("text", ["", "not json", "[]", "null", '"s"'], ids=["empty", "garbage", "array", "null", "string"])
def test_disclosure_must_be_a_json_object(tmp_path, text):
    path = tmp_path / "d.json"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError):
        P.load_disclosure(path, FIXTURE_BYTES)


def test_missing_disclosure_file_is_refused(tmp_path):
    with pytest.raises(ValueError):
        P.load_disclosure(tmp_path / "nope.json", FIXTURE_BYTES)


def synth_args(tmp_path, *extra, disclosure=True, fixture_bytes=FIXTURE_BYTES):
    fixture = tmp_path / "rival_scene.SaveRAM"
    fixture.write_bytes(fixture_bytes)
    argv = ["--setup", "synth", "--fixture", str(fixture)]
    if disclosure:
        argv += ["--disclosure", str(disclosure_file(tmp_path))]
    return P.parse_args(argv + list(extra))


def test_synth_accepts_a_bound_disclosure_and_records_the_binding(tmp_path):
    args = synth_args(tmp_path)
    assert args.setup == "synth" and args.expect == "rival"
    assert args.disclosure_info["role"] == "derivative"
    assert args.disclosure_sha256 == args.disclosure_info["sha256"]
    assert len(args.disclosure_sha256) == 64


def test_synth_wild_control_is_selectable(tmp_path):
    args = synth_args(tmp_path, "--expect", "wild", fixture_bytes=b"the source it was derived from")
    assert args.expect == "wild" and args.disclosure_info["role"] == "source"


def test_synth_requires_a_bound_disclosure(tmp_path):
    with pytest.raises(SystemExit) as exc:
        synth_args(tmp_path, disclosure=False)
    assert exc.value.code == 2


def test_synth_with_a_disclosure_for_another_fixture_is_an_argument_error(tmp_path):
    with pytest.raises(SystemExit) as exc:
        synth_args(tmp_path, fixture_bytes=b"some other save")
    assert exc.value.code == 2


def test_played_setup_is_unchanged_and_rejects_a_disclosure_or_expectation(tmp_path):
    fixture = tmp_path / "native.SaveRAM"
    fixture.write_bytes(b"native fixture")
    args = P.parse_args(["--setup", "played", "--fixture", str(fixture)])
    assert args.setup == "played" and args.disclosure_info is None and args.disclosure_sha256 is None
    for extra in (["--disclosure", str(disclosure_file(tmp_path))], ["--expect", "rival"], ["--expect", "wild"]):
        with pytest.raises(SystemExit) as exc:
            P.parse_args(["--setup", "played", "--fixture", str(fixture)] + extra)
        assert exc.value.code == 2


def test_synth_rejects_an_unknown_expectation(tmp_path):
    with pytest.raises(SystemExit) as exc:
        synth_args(tmp_path, "--expect", "both")
    assert exc.value.code == 2


def test_labels_never_call_a_synth_run_played(tmp_path):
    args = synth_args(tmp_path)
    assert P.lane_prefix("played") == "played-" and P.lane_prefix("synth") == "synth-"
    line = P.setup_line(args, FIXTURE_SHA)
    assert "SYNTH" in line and "played" not in line and args.disclosure_sha256 in line and FIXTURE_SHA in line
    assert "derivative" in line
    played = P.parse_args(["--setup", "played", "--fixture", str(args.fixture)])
    assert "no synthetic guest setup" in P.setup_line(played, FIXTURE_SHA) and "SYNTH" not in P.setup_line(played, FIXTURE_SHA)


def test_oracle_selection_follows_setup_and_expectation(tmp_path):
    played = P.parse_args(["--setup", "played", "--fixture", str(synth_args(tmp_path).fixture)])
    synth = synth_args(tmp_path)
    wild = synth_args(tmp_path, "--expect", "wild", fixture_bytes=b"the source it was derived from")
    clean = rival_trace()
    assert P.run_oracle(played, good_trace(), SITE6) == (True, [])
    assert P.run_oracle(synth, bound(complete(trio()), disc=synth.disclosure_sha256), SITE6) == (True, [])
    assert not P.run_oracle(synth, good_trace(), SITE6)[0]               # generic class 9 trace is not a rival pass
    assert not P.run_oracle(synth, clean, SITE6)[0]                      # binding differs from this run's disclosure
    assert P.run_oracle(wild, bound(complete([mode_row(1)]), disc=wild.disclosure_sha256), SITE6) == (True, [])


# ── Lua: battle-mode timeline, setup binding and the (still unchanged) bounded rows ─────────────

def test_lua_records_battle_mode_transitions_as_bounded_separate_rows():
    trace, recorded = run_mock_driver(frames=6, callbacks=0, modes={1: 0, 3: 1, 5: 0}, cap=50)
    assert recorded is True
    rows = [r for r in trace if r["kind"] == "mode"]
    assert [(r["mode"], r["frame"]) for r in rows] == [(1, 3), (0, 5)]
    assert all({"trainer_class", "trainer_id", "ot_party_count"} <= set(r) for r in rows)
    assert trace[-1]["mode_changes"] == 2 and trace[-1]["mode_rows_dropped"] == 0


def test_lua_observes_battle_mode_during_the_native_route_and_the_idle_tail():
    trace, recorded = run_mock_driver(frames=8, route_frames=4, callbacks=0, modes={1: 0, 2: 1, 3: 0, 6: 2}, cap=50)
    assert recorded is True
    assert [(r["mode"], r["frame"]) for r in trace if r["kind"] == "mode"] == [(1, 2), (0, 3), (2, 6)]


def test_lua_mode_rows_have_their_own_cap_and_never_consume_the_hook_capacity():
    modes = {f: (f % 2) + 1 for f in range(1, 201)}                      # a transition every single frame
    trace, recorded = run_mock_driver(frames=200, callbacks=0, modes=modes, cap=100000)
    assert recorded is True
    rows = [r for r in trace if r["kind"] == "mode"]
    assert len(rows) == 64
    assert trace[-1]["mode_changes"] == 200 and trace[-1]["mode_rows_dropped"] == 200 - 64
    assert not any(r["kind"] == "overflow" for r in trace)
    assert trace[-1]["hook_counts"]["gate"]["total"] == 200


def test_lua_mode_rows_do_not_change_hook_accounting_or_the_generic_oracle():
    trace, recorded = run_mock_driver(frames=3, modes={1: 2}, cap=50)
    assert recorded is True
    assert [r["mode"] for r in trace if r["kind"] == "mode"] == [2]
    assert trace[-1]["hook_hits"] == 3 * 3 * 7002
    assert P.evaluate(trace) == (True, [])


def test_lua_final_carries_the_setup_and_disclosure_binding():
    disc = "cd" * 32
    trace, _ = run_mock_driver(setup="synth", disclosure=disc)
    assert trace[-1]["setup"] == "synth" and trace[-1]["disclosure_sha256"] == disc
    played, _ = run_mock_driver()
    assert "setup" not in played[-1] and "disclosure_sha256" not in played[-1]


def test_lua_check_names_the_setup_not_always_played():
    _, _, name = run_mock_driver(setup="synth", disclosure="cd" * 32, with_name=True)
    assert "synth" in name and "played" not in name
    _, _, name = run_mock_driver(with_name=True)
    assert "played" in name




@pytest.mark.parametrize("mode", ["success", "stall", "budget", "old-witness"],
                         ids=["native-route", "npc-bound", "total-bound", "stale-mutant"])
def test_calibration_uses_positions_and_fresh_trainer_witness(mode):
    """Execute the real Lua calibration; no wall time or screenshots stand in for movement."""
    from lupa.lua55 import LuaRuntime

    lua = LuaRuntime(unpack_returned_tuples=True)
    lua.globals().RECORDING_GUARD = lua.execute(
        (REPO / "tools/polished_live/recording_guard.lua").read_text(encoding="utf-8"))
    g = lua.globals()
    g.encoder_mode, g.wrong_callbacks, g.mock_cap = "normal", 0, 100000
    g.mock_setup, g.mock_disc = "synth", "ab" * 32
    g.encode_trace = lambda value: json.dumps(_lua_value(value))
    g.stall, g.budget = mode == "stall", 100 if mode == "budget" else 18000
    lua.execute(MOCK_DRIVER)
    lua.execute(r"""
        local L = dofile()
        local x, y, group, map = 48, 12, 24, 3
        cur_mode = 0
        L.hook = function() end
        L.ow_idle = function() return true end
        L.rw = function(name)
            local values = {wMapGroup=group, wMapNumber=map, wXCoord=x, wYCoord=y,
                wBattleMode=cur_mode, wOtherTrainerClass=27, wOtherTrainerID=3, wOTPartyCount=2}
            return values[name] or 0
        end
        L.json.decode = function()
            return {steps={}, frames=budget, trace_cap=100000, setup="synth", calibrate=true, expect="rival"}
        end
        local oldframe = L.frame
        L.frame = function(buttons)
            if not stall then
                if buttons.Left then x=x-1 elseif buttons.Right then x=x+1
                elseif buttons.Up then y=y-1 elseif buttons.Down then y=y+1 end
                if x == -1 then group,map,x=26,4,39 end
                if group==26 and x==33 and y==7 and buttons.A then cur_mode=2 end
            end
            oldframe()
        end
        written = {}
        io.open = function(path)
            return {write=function(self,text) written[path]=text; output=text; return self end,
                    close=function() end}
        end
    """)
    source = (REPO / "tools/polished_live/rival_gate_probe.lua").read_text()
    if mode == "old-witness":
        anchor = 'hook_counts.last.qualified > before and L.rw("wBattleMode") == 2'
        assert source.count(anchor) == 1
        source = source.replace(anchor, 'hook_counts.last.qualified > 0')
    lua.execute(source)
    trace = json.loads(g.written["mock/trace.json"])
    if mode in ("stall", "budget"):
        assert g.recording_ok is False
        reason = "1800-frame stall" if mode == "stall" else "total frame bound"
        assert any(reason in r.get("error", "") for r in trace)
    else:
        assert g.recording_ok is True
        milestones = json.loads(g.written["mock/calibration.json"])
        if mode == "old-witness":
            with pytest.raises(AssertionError):
                assert milestones[-1]["mode"] == 2
        else:
            assert milestones[-1]["mode"] == 2
            assert (milestones[-1]["group"], milestones[-1]["x"], milestones[-1]["y"]) == (26, 33, 7)
            route = json.loads(g.written["mock/route.json"])
            assert sum(r["frames"] for r in route["steps"]) == trace[-1]["elapsed"]


def test_calibration_requires_disclosed_synth_and_no_timed_route(tmp_path):
    fixture = tmp_path / "fixture.SaveRAM"
    fixture.write_bytes(b"fixture")
    with pytest.raises(SystemExit):
        P.parse_args(["--setup", "played", "--fixture", str(fixture), "--calibrate"])
    args = synth_args(tmp_path, "--calibrate")
    assert args.calibrate is True
    with pytest.raises(SystemExit):
        synth_args(tmp_path, "--calibrate", "--route", str(tmp_path / "route.json"))


RETAINED = {
    "synth-xq6b4nrp": "0121f76bdee7d422d1411477fa6efd7043f60131461d6f9264a377116620f4a1",
    "synth-i_xfq96x": "1ce96295ed630aa7fd58a7ba56e47a5a30bf9f6cb1c79ffe157abb18e62eb41c",
    "synth-1agu9bbq": "1e53789331507f5e436d5e3e11504a7805ce4b4f0581a31c249f1ee34c67a5b9",
}


def retained(name):
    path = REPO / "tests/fixtures/polished/rival" / (name + ".json")
    if not path.exists():
        pytest.skip("OPEN: absent retained rival trace " + str(path))
    raw = path.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == RETAINED[name]
    return json.loads(raw)


def test_retained_wild_is_unqualified_operation_control():
    trace = retained("synth-1agu9bbq")
    assert P.evaluate_wild_control(trace, disclosure_sha256=trace[-1]["disclosure_sha256"]) == (True, [])


def test_operation_requires_measured_enemy_side():
    assert not P.operation_qualified(rival_hit(battle_turn=None))
    assert P.operation_qualified(rival_hit(battle_turn=1))
    assert not P.operation_qualified(rival_hit(battle_turn=0))


@pytest.mark.parametrize("name,expect,verdict", [
    ("synth-i_xfq96x", "rival", "MISSING_SIDE"),
    ("synth-xq6b4nrp", "rival", "NO_GATE_HIT"),
    ("synth-1agu9bbq", "wild", "PASS"),
], ids=["rival-open-side", "first-open-no-rival", "wild-pass-control"])
def test_retained_trace_rejudgements(name, expect, verdict):
    trace = retained(name)
    original = copy.deepcopy(trace)
    fn = P.evaluate_rival if expect == "rival" else P.evaluate_wild_control
    ok, reasons = fn(trace, disclosure_sha256=trace[-1]["disclosure_sha256"])
    assert P.verdict(reasons) == verdict
    assert ok == (verdict == "PASS")
    assert trace == original
    assert sum(P.operation_qualified(r) for r in trace) == 0


@pytest.mark.parametrize("fields,want", [
    ({"side": 1, "klass": 27, "tid": 3}, True),
    ({"side": 0, "klass": 27, "tid": 3}, False),
    ({"side": 2, "klass": 27, "tid": 3}, False),
    ({"side": 1, "klass": 0, "tid": 3}, False),
    ({"side": 1, "klass": 27, "tid": 4}, False),
    ({"side": 1, "klass": 27, "tid": 3, "modes": {1: 1}}, False),
], ids=["rival", "player", "side-domain", "non-rival", "wrong-id", "wild-stale-identity"])
def test_live_lua_and_python_operation_predicates_agree(fields, want):
    trace, recorded = run_mock_driver(callbacks=0, **fields)
    assert recorded
    for row in trace:
        if row["kind"] in P.HOOKS:
            assert row["raw_qualified"] is True
            assert row["qualified"] is want
            assert P.operation_qualified(row) is want
    assert trace[-1]["hook_counts"]["gate"]["qualified"] == 1  # raw count never filtered


def test_mode_guard_mutants_are_red_on_synth_stale_identity():
    # Explicit MODEL/SYNTH copy, not a rewritten physical receipt: stale rival identity
    # in a real wild-side/mode row isolates the otherwise redundant mode guard.
    wild = next(r for r in retained("synth-1agu9bbq") if r.get("kind") == "gate" and r.get("qualified"))
    stale = dict(wild, trainer_class=27, trainer_id=3)
    assert not P.operation_qualified(wild)
    assert not P.operation_qualified(stale)
    import inspect
    source = inspect.getsource(P.operation_qualified)
    anchor = 'and type(row.get("mode")) is int and row["mode"] == 2'
    assert source.count(anchor) == 1
    ns = dict(P.__dict__)
    exec(source.replace(anchor, ""), ns)
    with pytest.raises(AssertionError):
        assert not ns["operation_qualified"](stale)
    assert not ns["operation_qualified"](wild)  # real class0/id0 still excludes it
    trace, recorded = run_mock_driver(callbacks=0, side=1, klass=27, tid=3, modes={1: 1}, mutant=True)
    assert recorded
    gate = next(r for r in trace if r["kind"] == "gate")
    with pytest.raises(AssertionError):
        assert gate["qualified"] is False


def test_offline_cli_never_stages_launches_or_rewrites(tmp_path, monkeypatch, capsys):
    from types import SimpleNamespace

    trace = retained("synth-i_xfq96x")
    path = tmp_path / "trace.json"
    path.write_text(json.dumps(trace))
    args = SimpleNamespace(rejudge=path, expect="rival", setup="synth",
                           disclosure_sha256=trace[-1]["disclosure_sha256"])
    before = path.read_bytes()
    monkeypatch.setattr(P, "parse_args", lambda argv: args)
    monkeypatch.setattr(P, "stage", lambda *a, **kw: pytest.fail("offline attempted ROM staging"))
    monkeypatch.setattr(P, "WORK", tmp_path / "must-not-create")
    assert P.main([]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["verdict"] == "MISSING_SIDE"
    assert result["conditional_source_model"]["verdict"] == "PASS"
    assert "NOT PHYSICAL PASS" in result["conditional_source_model"]["assumption"]
    assert result["qualified_gate_frames"] == []
    assert path.read_bytes() == before
    assert not P.WORK.exists()


@pytest.mark.parametrize("override", [{"qualified": False}, {"raw_qualified": False}],
                         ids=["forged-operation", "forged-raw"])
def test_current_schema_forged_qualification_fails(override):
    trace, _ = run_mock_driver(callbacks=0, side=1, klass=27, tid=3, setup="synth", disclosure=DISC)
    row = next(r for r in trace if r["kind"] == "gate")
    row.update(override)
    ok, reasons = P.evaluate_wild_control(trace, disclosure_sha256=DISC)
    assert not ok
    assert any("QUALIFICATION_MISMATCH" in r or "qualified" in r.lower() for r in reasons)



def test_witness_cannot_skip_to_a_later_send_in_of_the_same_identity():
    ok, reasons = rival_eval(rival_trace([rival_hit(), *trio()]))
    assert not ok and has(reasons, "WITNESS_MISSING")
