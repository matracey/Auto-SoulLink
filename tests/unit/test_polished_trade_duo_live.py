"""Trade/cold-load judge falsifiers; no emulator launch."""
import copy
import inspect
from pathlib import Path

import pytest

from tools.polished_live import trade_duo_live as t


def evidence():
    return strict_evidence()


def test_trade_and_cold_load_positive():
    wire, before, cold, state, milestones, durable = evidence()
    assert t.judge(wire, before, cold, state, milestones=milestones, durable=durable) == ("PASS", [])


@pytest.mark.parametrize("fault", ["no-done", "uncertain", "old-cold", "dead", "pending", "faint"],
                         ids=["missing-side", "uncertain", "not-durable", "dead-link", "inflight", "false-faint"])
def test_judge_controls_fail(fault):
    wire, before, cold, state, milestones, durable = copy.deepcopy(evidence())
    if fault == "no-done":
        wire["b"] = []
    elif fault == "uncertain":
        wire["a"][-1]["msg"]["uncertain"] = True
    elif fault == "old-cold":
        cold["b"]["party"] = t.party(before["b"])
    elif fault == "dead":
        state["links"][0]["status"] = "dead"
    elif fault == "pending":
        state["pending_trade"] = {"phase": "applying"}
    else:
        wire["a"].append({"dir": "c2s", "msg": {"event": "faint"}})
    assert t.judge(wire, before, cold, state, milestones=milestones, durable=durable)[0] == "FAIL"


def test_builder_test_mode_defaults_off_and_test_output_cannot_publish(tmp_path):
    from tools import build_polished_companion as build
    sig = inspect.signature(build.build)
    assert sig.parameters["test_trade_enable"].default is False
    assert sig.parameters["test_output"].default is None
    original = (build.UPS_PATH.read_bytes(), build.PROVENANCE_PATH.read_bytes())
    for args in ({"test_trade_enable": True}, {"test_output": build.OUT_DIR}, {"test_output": tmp_path, "check": True}):
        with pytest.raises(RuntimeError):
            build.build(**args)
    assert original == (build.UPS_PATH.read_bytes(), build.PROVENANCE_PATH.read_bytes())


def test_setup_is_one_link_from_real_save_keys(tmp_path):
    _, before, _, _, _, _ = evidence()
    events = t.seed_server(tmp_path, before)
    assert [e["msg"]["event"] for e in events] == ["hello", "capture", "hello", "capture"]
    for row in events:
        if row["msg"]["event"] == "capture":
            assert row["msg"]["key"] == t.party(before[row["player"]])[0]["key"]
    from server.server import SLinkServer
    reloaded = SLinkServer(data_dir=str(tmp_path))
    assert not reloaded.state.load_failed, reloaded.state.load_failed
    assert reloaded.state.rom_type == "polished_crystal"


def test_lua55_syntax():
    from lupa.lua55 import LuaRuntime
    lua = LuaRuntime()
    for name in ("tools/polished_live/trade_duo_live.lua", "lua/gen2/polished_trade.lua", "lua/gen2/client.lua"):
        assert lua.eval("function(s) return assert(load(s)) ~= nil end")((t.ROOT/Path(name)).read_text())


def test_symbol_contract_fits_real_json_decoder_and_full_sym_is_red_control():
    import json

    from lupa.lua55 import LuaRuntime

    from tools.build_gen2_companion import _symbols
    syms = _symbols(t.ROOT/"data/polished/polished_slink.sym")
    lua = LuaRuntime(unpack_returned_tuples=True)
    codec = lua.execute((t.ROOT/"lua/json_codec.lua").read_text())
    old, why = codec.decode(json.dumps(syms))
    assert old is None and "too many JSON values" in why
    decoded = codec.decode(json.dumps(t.probe_symbols(syms)))
    assert decoded.NoYesBox[2] == syms["NoYesBox"][1]


def compose_without_global_print():
    import json

    from tests.unit import test_polished_write_path as wp
    from tests.unit.test_polished_client import _entry, _pair
    lua = wp.lupa.LuaRuntime(unpack_returned_tuples=True)
    mem = lua.table_from(wp.sysbus(wp.party()))
    deps, _, log = lua.execute(wp.HARNESS.replace("ROOTDIR", json.dumps(wp.ROOT)))(wp.overlay()[1], mem, wp.seal_save(wp.Image()))
    deps.polished_trade_dev = True
    lua.globals().print = None  # MODEL of the live refusal: no Lua-function global print
    parts, why = _pair(_entry(lua).build(deps))
    assert parts and why is None
    assert parts.dev_polished_trade is not None, list(log.lines.values())


def test_composition_passes_explicit_logger_without_global_print():
    compose_without_global_print()


@pytest.mark.parametrize("result", [0, 2], ids=["complete-transient-read", "uncertain-never-release"])
def test_complete_retries_transient_postimage_but_poison_never_releases(result):
    from tests.unit.test_polished_trade_pump import Pump
    p = Pump()
    p.accepted(p.apply_command())
    p.frame()
    base = p.parts.profile.overlay.trade.lease.base
    gen = p.mem[base+6]
    p.put(5, 7)
    p.put(7, gen)
    p.put(8, result)
    p.lua.execute('''return function(b,reads)
        local poll,read=b.poll_done,reads.read_party
        local once,refuse=true,false
        b.poll_done=function(self,...)
            local done,a,c=poll(self,...)
            if once and done and done.disposition=='COMPLETE' then once=false; refuse=true end
            return done,a,c
        end
        reads.read_party=function(...)
            if refuse then refuse=false; return nil,'MODEL transient post-DONE read' end
            return read(...)
        end
    end''')(p.binder, p.parts.reads)
    before = len(p.writes())
    p.frame()
    if result == 0:
        assert not p.sent("trade_done") and p.mem[base+5] == 7
        p.frame(3)
        reports = p.sent("trade_done")
        assert len(reports) == 1 and not reports[0].get("uncertain")
        assert reports[0]["new_key"] == p.sent("hello")[0]["party"][-1]["key"]
        assert p.mem[base+5] == 8
        p.put(5, 0)  # MODEL native RELEASE consumption/closure
        p.frame(2)
        assert p.binder.phase(p.binder) == (None, None)
    else:
        p.frame(3)
        assert p.mem[base+5] == 7
        assert p.sent("trade_done")[0]["uncertain"] is True
        assert not any(w["addr"] == base+5 and w["value"] == 8 for w in p.writes()[before:])


@pytest.mark.parametrize("armed", [False, True], ids=["pre-apply-expires", "native-commit-outlives-offer-deadline"])
def test_offer_deadline_cannot_cancel_an_armed_native_commit(armed):
    from tests.unit.test_polished_trade_pump import Pump
    p = Pump()
    p.accepted(*([p.apply_command()] if armed else []))
    p.frame()
    # MODEL elapsed native frames; preserve timeline continuity, not a savestate load.
    p.io.frame += p.parts.profile.overlay.trade.timeouts.APPLY + 1
    p.client.last_frame = p.io.frame
    p.frame()
    if not armed:
        assert not p.sent("trade_done")
        assert p.sent("menu_result")[-1]["choice"] == 0
        assert p.binder.disposition(p.binder)[0] == "NOT_PERFORMED"
        return
    assert not p.sent("trade_done") and not p.cancel_trace
    base = p.parts.profile.overlay.trade.lease.base
    gen = p.mem[base+6]
    p.put(5, 7)
    p.put(7, gen)
    p.put(8, 0)
    p.frame(3)
    assert len(p.sent("trade_done")) == 1 and not p.sent("trade_done")[0].get("uncertain")
    assert p.mem[base+5] == 8


def strict_evidence():
    """Isolated literal MODEL: no retained lane fixture or emulator dependency."""
    from tests.unit.test_polished_duo_driver import synth_pair
    a, b = synth_pair()
    before = {"a": a, "b": b}
    original = {r: t.party(data) for r, data in before.items()}
    wire, cold, milestones, durable = {}, {}, {}, {}
    link = {"status": "alive"}
    for r, other in (("a", "b"), ("b", "a")):
        ident = t.duo.fixture_identity(before[r])
        mons = copy.deepcopy(original[r][1:] + [original[other][0]])
        # Native AddTempMonToParty resets non-egg happiness to BASE_HAPPINESS (70).
        received = bytearray.fromhex(mons[-1]["blob_hex"])
        received[26] = 70
        mons[-1]["blob_hex"] = received.hex()
        cold[r] = {"party": mons, "trainer_name": ident["name"], "ot_id": ident["player_id"]}
        wire[r] = [{"dir": "c2s", "msg": {"event": "hello", "party": original[r]}},
                   {"dir": "s2c", "msg": {"commands": [{"cmd": "apply_trade", "token": "t1"}]}},
                   {"dir": "c2s", "msg": {"event": "trade_done", "token": "t1", "new_key": mons[-1]["key"]}}]
        names = ["SlinkTradeApplyCommit", "SlinkTradeCommit", "ForceGameSave", "SlinkTradeCommit.CheckSave",
                 "SlinkTradePublishDone", "SlinkTradeExit" if r == "a" else "SlinkTradeResponderExit"]
        milestones[r] = [{"kind": "native", "frame": 10 + i, "detail": name, "qualified": True,
                          "pc": 0x5300 + i, "bank": 126, "expected_hex": "abcd"}
                         for i, name in enumerate(names)]
        durable[r] = {"copies": {c: copy.deepcopy(cold[r]) for c in ("main", "backup")},
                      "integrity": {c: {"valid": True, "markers_valid": True} for c in ("main", "backup")}}
        link[r] = {"key": mons[-1]["key"]}
    return wire, before, cold, {"links": [link], "pending_trade": None}, milestones, durable


def test_f9_trade_requires_native_evidence_not_just_protocol_and_keys():
    wire, before, cold, state, _, _ = strict_evidence()
    assert t.judge(wire, before, cold, state)[0] == "FAIL"


def test_f9_complete_native_trade_evidence_passes():
    wire, before, cold, state, milestones, durable = strict_evidence()
    assert t.judge(wire, before, cold, state, milestones=milestones, durable=durable) == ("PASS", [])


@pytest.mark.parametrize("fault", [
    "missing-commit", "missing-save", "wrong-order", "unqualified", "missing-exit",
    "cold-id", "cold-name", "cold-received", "cold-survivor", "save-received",
    "save-survivor", "save-id", "save-name", "missing-backup", "missing-raw",
])
def test_f9_trade_rejects_native_or_durable_record_defects(fault):
    wire, before, cold, state, milestones, durable = strict_evidence()
    if fault in ("missing-commit", "missing-save", "missing-exit"):
        name = {"missing-commit": "SlinkTradeCommit", "missing-save": "ForceGameSave",
                "missing-exit": "SlinkTradeResponderExit"}[fault]
        milestones["b"] = [m for m in milestones["b"] if m["detail"] != name]
    elif fault == "wrong-order":
        milestones["a"][1]["frame"] = 100
    elif fault == "unqualified":
        milestones["b"][0]["qualified"] = False
    elif fault.startswith("cold-") and fault[5:] in ("id", "name"):
        cold["a"]["ot_id" if fault == "cold-id" else "trainer_name"] = 0
    elif fault.startswith("save-") and fault[5:] in ("id", "name"):
        durable["b"]["copies"]["backup"]["ot_id" if fault == "save-id" else "trainer_name"] = 0
    elif fault == "missing-backup":
        durable["a"]["copies"].pop("backup")
    elif fault == "missing-raw":
        cold["a"]["party"][-1].pop("blob_hex")
    else:
        target = cold["a"] if fault.startswith("cold-") else durable["b"]["copies"]["backup"]
        mon = target["party"][-1 if fault.endswith("received") else 0]
        raw = bytearray.fromhex(mon["blob_hex"])
        raw[2] ^= 1  # corrupt a move without changing identity/key/HP
        mon["blob_hex"] = raw.hex()
    assert t.judge(wire, before, cold, state, milestones=milestones, durable=durable)[0] == "FAIL"


@pytest.mark.parametrize("where", ["cold", "main", "backup"])
@pytest.mark.parametrize("slot", [0, -1], ids=["surviving", "received"])
@pytest.mark.parametrize("offset", [1, 2, 22, 32, 38, 48, 59],
                         ids=["item", "moves", "pp", "status", "stats", "ot", "nickname"])
def test_f9_full_record_mutations_fail_in_every_persistence_witness(where, slot, offset):
    wire, before, cold, state, milestones, durable = strict_evidence()
    snapshot = cold["a"] if where == "cold" else durable["a"]["copies"][where]
    row = snapshot["party"][slot]
    raw = bytearray.fromhex(row["blob_hex"])
    raw[offset] ^= 1
    row["blob_hex"] = raw.hex()
    assert t.judge(wire, before, cold, state, milestones=milestones, durable=durable)[0] == "FAIL"


def retained_model(run_dir):
    """Physical save extraction and cold dumps enter the same public replay seam."""
    import json
    wire, before, cold, state, milestones, _ = strict_evidence()
    paths = set()
    def put(name, data):
        path = run_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data if isinstance(data, bytes) else json.dumps(data).encode())
        paths.add(path.resolve())
    sites = {row["detail"]: {k: row[k] for k in ("pc", "bank", "expected_hex")}
             for rows in milestones.values() for row in rows}
    put("manifest.json", {"test_build": False, "rom_sha1": t.overlay_sha1(), "native_sites": sites})
    put("state-after-trade.json", state)
    ds = t.duo._derive_save()
    for r in ("a", "b"):
        put(f"before-{r}.SaveRAM", before[r])
        saved = bytearray(before[r])
        for c in ds.ALL_COPIES:
            for i, row in enumerate(cold[r]["party"]):
                raw = bytes.fromhex(row["blob_hex"])
                saved[c.mon_at(i):c.mon_at(i) + 48] = raw[:48]
                saved[c.ot_at(i):c.ot_at(i) + 11] = raw[48:59]
                saved[c.nick_at(i):c.nick_at(i) + 11] = raw[59:]
        ds._reseal(saved)
        put(f"after-{r}.SaveRAM", bytes(saved))
        put(f"{r}/g2/ready.json", cold[r])
        put(f"{r}/g1/milestones.jsonl", "".join(json.dumps(row) + "\n" for row in milestones[r]).encode())
        put(f"wire/wire_{r}.jsonl", "".join(json.dumps(row) + "\n" for row in wire[r]).encode())
    return paths


def test_f9_retained_replay_uses_bound_physical_inputs(tmp_path):
    bound = retained_model(tmp_path)
    assert t.replay_saved(tmp_path, bound_artifacts=bound) == ("PASS", [])


@pytest.mark.parametrize("name", ["manifest.json", "before-a.SaveRAM", "after-b.SaveRAM",
                                  "a/g1/milestones.jsonl", "b/g2/ready.json", "wire/wire_a.jsonl",
                                  "state-after-trade.json"])
def test_f9_retained_replay_rejects_unbound_sources(tmp_path, name):
    bound = retained_model(tmp_path)
    bound.remove((tmp_path / name).resolve())
    verdict, reasons = t.replay_saved(tmp_path, bound_artifacts=bound)
    assert verdict == "FAIL" and any("unbound replay source" in reason for reason in reasons)
