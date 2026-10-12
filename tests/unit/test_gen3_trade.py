"""Durable trade MODEL falsifiers; native completion is not a save witness."""

import json
from pathlib import Path

import lupa
import pytest

from tests.unit import gen3_world as gw
from tests.unit.gen3_trade_journal_model import JournalModel
from tests.unit.gen3_world import World, lua_to_py
from tests.unit.test_gen3_client import (
    KA,
    KB,
    KC,
    KP,
    PARTNER,
    A,
    B,
    C,
    apply,
    mb_ack,
    party,
    swap_in_partner,
    trade_world,
)
from tests.unit.test_state import _offer_and_pick, make_state_with_link

ROOT = Path(__file__).resolve().parents[2]


class TradeWorld:
    """ABI-independent cartridge boundary; it publishes explicit typed milestones."""

    def __init__(self, precommit_refusal_reasons=None):
        self.lua = lupa.LuaRuntime(unpack_returned_tuples=True)
        self.frame = 100
        self.epoch = 1
        self.connected = True
        self.capable = True
        self.events, self.jobs = [], []
        self.hold_reports, self.pending_reports = False, set()
        self.logs, self.hud_messages = [], []
        self.incoming = {"key": "00000005:00000006", "species": 25, "slot": 0, "hp": 20}
        self.rows = [{"key": "00000001:00000002", "slot": 0, "species": 4, "hp": 20},
                     {"key": "00000003:00000004", "slot": 1, "species": 7, "hp": 20}]
        self.visit = {"id": 7, "accepted": True, "pre_saved": True, "apply_open": True}

        def transfer(_, step, args, done, valid, progress=None):
            handle = self.lua.table()
            self.jobs.append({"step": step, "args": args, "done": done, "valid": valid,
                              "progress": progress, "handle": handle})
            return handle

        def cancel(_, handle):
            if handle.posted:
                return False
            handle.cancelled = True
            return True

        native = self.lua.table(trade_capable=lambda *_: self.capable,
                                trade_visit=lambda *_: self.lua.table_from(self.visit),
                                trade_eligible=lambda *_: True, transfer=transfer, cancel=cancel)
        self.native = native

        def send(name, fields, epoch=None):
            epoch = self.epoch if epoch is None else epoch
            self.events.append((name, lua_to_py(fields)))
            if name == "trade_done" and self.hold_reports:
                self.pending_reports.add((epoch, fields.token))
            return True

        module = self.lua.execute((ROOT / "lua/gen3/trade.lua").read_text())
        self.deps = self.lua.table(
            native=native, frame=lambda: self.frame, eligible=lambda: self.connected,
            ready_to_send=lambda: self.connected, clear=lambda: True, capacity=6, mon_size=100,
            prepare_frames=600, apply_frames=1800, epoch=lambda: self.epoch,
            report_pending=lambda epoch, value: (epoch, value) in self.pending_reports,
            decode_blob=lambda _: self.lua.table_from(self.incoming), log=self.logs.append,
            saveram=lambda: None,  # successful void host flush, explicitly a MODEL seam
            hud=self.lua.table(show=lambda text, *_: self.hud_messages.append(str(text))),
            party=lambda: self.lua.table_from(self.rows, recursive=True), key=lambda m: m.key,
            send=send)
        if precommit_refusal_reasons is not None:
            self.deps.precommit_refusal_reasons = self.lua.table_from(precommit_refusal_reasons)
        self.journal_model = JournalModel(ot="00000002")
        self.deps.journal = self.journal_model(self.lua)
        self.trade = module.new(self.deps)

    def command(self, method, **fields):
        return getattr(self.trade, method)(self.trade, self.lua.table_from(fields))

    def tick(self):
        self.frame += 1
        self.trade.tick(self.trade)

    def prepare(self):
        self.command("prepare", token="t", slot=0, old_key=self.rows[0]["key"])

    def start(self):
        self.prepare()
        self.command("apply", token="t", slot=0, old_key=self.rows[0]["key"],
                     blob_hex="0500000006000000" + "00" * 92)
        self.tick()
        stage = self.jobs[-1]
        assert stage["step"] == "enemy"
        self.dispatch(stage)
        stage["done"](None, 0, None)
        scene = self.jobs[-1]
        assert scene["step"] == "scene"
        self.dispatch(scene)
        return scene

    def dispatch(self, job):
        result = job["valid"]()
        assert result is True
        job["handle"].posted = True

    def received(self):
        self.rows[0] = {"key": "00000005:00000006", "slot": 0, "species": 65, "hp": 20}

    def progress(self, job, **fields):
        job["progress"](self.lua.table_from(fields))


def test_late_apply_after_withdrawal_cannot_queue_a_native_operation():
    world = TradeWorld()
    world.prepare()
    assert ("apply_ready", {"token": "t", "ok": True}) in world.events
    world.command("withdraw", token="t")
    world.command("apply", token="t", slot=0, old_key=world.rows[0]["key"], blob_hex="05" * 100)
    world.tick()
    assert world.jobs == []
    assert any(name == "trade_done" and fields.get("new_key") == world.rows[0]["key"]
               for name, fields in world.events)


def test_prepared_apply_queues_partner_staging_through_native_owner():
    world = TradeWorld()
    world.prepare()
    world.command("apply", token="t", slot=0, old_key=world.rows[0]["key"], blob_hex="05" * 100)
    world.tick()
    assert [job["step"] for job in world.jobs] == ["enemy"]


def test_commit_and_scene_completion_without_native_save_is_uncertain_not_success():
    world = TradeWorld()
    job = world.start()
    world.progress(job, commit_entered=True, scene_done=True)
    world.received()
    job["done"](None, 0, None)
    world.tick()
    results = [f for n, f in world.events if n == "trade_done"]
    assert results == [{"token": "t", "uncertain": True, "after_reset": True}]
    assert world.trade.hide_party(world.trade) is True


def test_only_native_commit_scene_and_save_plus_final_readback_can_succeed():
    world = TradeWorld()
    job = world.start()
    world.progress(job, commit_entered=True, scene_done=True, save_success=True, final_result="committed")
    world.received()
    job["done"](None, 0, None)
    world.tick()
    assert ("trade_done", {"token": "t", "slot": 0, "new_key": "00000005:00000006", "new_species": 65}) in world.events
    assert [j["step"] for j in world.jobs] == ["enemy", "scene"]


def test_reset_keeps_committed_token_and_owes_uncertainty_after_hello():
    world = TradeWorld()
    job = world.start()
    world.progress(job, commit_entered=True)
    world.connected = False
    world.trade.reset(world.trade)
    world.tick()
    assert not [f for n, f in world.events if n == "trade_done"]
    world.connected = True
    world.tick()
    assert [f for n, f in world.events if n == "trade_done"] == [
        {"token": "t", "uncertain": True, "after_reset": True}]
    world.progress(job, commit_entered=True, scene_done=True, save_success=True)
    world.received()
    job["done"](None, 0, None)
    world.tick()
    assert not [f for n, f in world.events if n == "trade_done" and "new_key" in f]


def test_reset_has_no_nonreload_parameter_or_false_bypass():
    world = TradeWorld()
    arity = world.lua.eval('function(f) return debug.getinfo(f, "u").nparams end')
    assert arity(world.trade.reset) == 1  # self only; no reset(false) mode
    job = world.start()
    world.progress(job, commit_entered=True)
    world.trade.reset(world.trade, False)  # Lua ignores surplus args, never weakens reset
    world.tick()
    assert world.trade.reloaded(world.trade, "t", world.epoch) is False
    assert world.trade.hide_party(world.trade) is True
    assert [f for n, f in world.events if n == "trade_done"] == [
        {"token": "t", "uncertain": True, "after_reset": True}]


def test_reset_before_unpicked_commit_withdraws_without_late_dispatch():
    world = TradeWorld()
    world.prepare()
    world.command("apply", token="t", old_key=world.rows[0]["key"], slot=0, blob_hex="05" * 100)
    world.tick()
    stage = world.jobs[-1]
    world.trade.reset(world.trade)
    stage["done"](None, 0, None)
    world.tick()
    assert len(world.jobs) == 1 and stage["handle"].cancelled is True
    assert ("trade_done", {"token": "t", "slot": 0, "new_key": "00000001:00000002", "new_species": 0}) in world.events


def test_native_failure_never_queues_raw_party_replacement():
    world = TradeWorld()
    job = world.start()
    world.progress(job, commit_entered=True)
    job["done"]("native refused", 0, None)
    world.tick()
    assert [j["step"] for j in world.jobs] == ["enemy", "scene"]
    assert [f for n, f in world.events if n == "trade_done"] == [
        {"token": "t", "uncertain": True, "after_reset": True}]


@pytest.mark.parametrize("allow,committed,why,reason,unchanged", [
    (True, False, "native refused", "model_pre_commit_refused", True),
    (True, False, "native refused", "identity", False),
    (True, True, "native refused", "model_pre_commit_refused", False),
    (True, False, "native timeout", "model_pre_commit_refused", False),
    (False, False, "native refused", "model_pre_commit_refused", False),
], ids=["listed", "unlisted", "contradictory-commit", "timeout", "production-empty"])
def test_scene_refusal_uses_only_explicit_precommit_guarantees(allow, committed, why, reason, unchanged):
    # MODEL-only name. T2 has not defined any production pre-commit guarantee.
    world = TradeWorld(precommit_refusal_reasons={"model_pre_commit_refused": True} if allow else None)
    job = world.start()
    if committed:
        world.progress(job, commit_entered=True)
    job["done"](why, 7, reason)
    world.tick()
    expected = ({"token": "t", "slot": 0, "new_key": world.rows[0]["key"], "new_species": 0}
                if unchanged else {"token": "t", "uncertain": True, "after_reset": True})
    assert [f for n, f in world.events if n == "trade_done"] == [expected]
    assert world.trade.hide_party(world.trade) is (not unchanged)
    if not unchanged and why == "native refused":
        assert len(world.logs) == 1 and reason in world.logs[0] and "result 7" in world.logs[0]


def test_post_save_without_tokened_final_result_is_not_success():
    world = TradeWorld()
    job = world.start()
    world.progress(job, commit_entered=True, scene_done=True, save_success=True)
    world.received()
    job["done"](None, 0, None)
    world.tick()
    assert not [f for n, f in world.events if n == "trade_done" and "new_key" in f]
    assert [f for n, f in world.events if n == "trade_done"] == [{"token": "t", "uncertain": True}]
    assert world.trade.hide_party(world.trade) is True


def test_interrupted_scene_publication_is_not_claimed_unchanged():
    world = TradeWorld()
    world.prepare()
    world.command("apply", token="t", old_key=world.rows[0]["key"], slot=0, blob_hex="05" * 100)
    world.tick()
    stage = world.jobs[-1]
    world.dispatch(stage)
    stage["done"](None, 0, None)
    scene = world.jobs[-1]
    scene["handle"].publish_attempted = True  # sink failed after opcode low byte
    scene["done"]("native dispatch interrupted", 0, None)
    world.tick()
    assert [f for n, f in world.events if n == "trade_done"] == [
        {"token": "t", "uncertain": True, "after_reset": True}]


def test_prepare_replay_cannot_extend_expired_readiness():
    world = TradeWorld()
    world.prepare()
    world.frame += 601
    world.prepare()
    assert world.events[-1] == ("apply_ready", {"token": "t", "ok": False})


def test_prepare_waits_for_native_consent_and_presave_ack():
    world = TradeWorld()
    pending = []
    world.native.trade_authorized = lambda *_: True
    world.native.prepare_trade = lambda _, cmd, done, valid: pending.append((cmd, done, valid)) or world.lua.table()
    world.visit["pre_saved"] = False
    world.prepare()
    assert world.events == []
    assert len(pending) == 1
    world.visit["pre_saved"] = True
    pending[0][1](None, world.lua.table(**world.visit, old_key=world.rows[0]["key"]))
    assert world.events == [("apply_ready", {"token": "t", "ok": True})]


def test_native_presave_ready_follows_a_fresh_visible_party_snapshot():
    world = TradeWorld()
    pending = []
    visible = [False]
    world.native.trade_authorized = lambda *_: True
    world.native.prepare_trade = lambda _, cmd, done, valid: pending.append((cmd, done, valid)) or world.lua.table()

    def publish():
        if not visible[0]:
            return False
        world.events.append(("tick", {"party": [world.rows[0]["key"]]}))
        return True

    world.deps.publish_visible_snapshot = publish
    world.trade = world.lua.execute((ROOT / "lua/gen3/trade.lua").read_text()).new(world.deps)
    world.prepare()
    pending[0][1](None, world.lua.table(**world.visit, old_key=world.rows[0]["key"]))
    assert not [fields for event, fields in world.events if event == "apply_ready"]
    world.tick()
    assert not [fields for event, fields in world.events if event == "apply_ready"]
    visible[0] = True
    world.tick()
    assert [event for event, _ in world.events] == ["tick", "apply_ready"]
    assert world.events[-1] == ("apply_ready", {"token": "t", "ok": True})


def test_native_presave_visible_barrier_expires_without_late_ready():
    world = TradeWorld()
    pending = []
    visible = [False]
    world.native.trade_authorized = lambda *_: True
    world.native.prepare_trade = lambda _, cmd, done, valid: pending.append((cmd, done, valid)) or world.lua.table()
    world.deps.publish_visible_snapshot = lambda: visible[0]
    world.trade = world.lua.execute((ROOT / "lua/gen3/trade.lua").read_text()).new(world.deps)
    world.prepare()
    pending[0][1](None, world.lua.table(**world.visit, old_key=world.rows[0]["key"]))
    world.frame += 601
    world.tick()
    visible[0] = True
    world.tick()
    assert [fields for event, fields in world.events if event == "apply_ready"] == [
        {"token": "t", "ok": False}]
    assert not world.jobs


def test_reorder_at_scene_dispatch_relocates_identity_without_touching_bystander():
    world = TradeWorld()
    world.prepare()
    world.command("apply", token="t", old_key=world.rows[0]["key"], slot=0, blob_hex="05" * 100)
    world.tick()
    stage = world.jobs[-1]
    world.dispatch(stage)
    stage["done"](None, 0, None)
    old_scene = world.jobs[-1]
    world.rows[0]["slot"], world.rows[1]["slot"] = 1, 0
    valid, reason = old_scene["valid"]()
    assert valid is False and reason == "guard:moved"
    old_scene["done"](reason, 0, None)
    new_scene = world.jobs[-1]
    assert new_scene is not old_scene and new_scene["args"].slot == 1
    world.dispatch(new_scene)
    old_scene["progress"](world.lua.table(commit_entered=True, scene_done=True, save_success=True, final_result="committed"))
    new_scene["done"](None, 0, None)
    world.tick()
    assert not [f for n, f in world.events if n == "trade_done" and "new_key" in f]


@pytest.mark.parametrize("invalid", ["duplicate", "egg", "bad_checksum", "capacity", "lost_visit"])
def test_prepare_refuses_ineligible_identity_or_visit(invalid):
    world = TradeWorld()
    if invalid == "duplicate":
        world.rows.append(dict(world.rows[0], slot=2))
    elif invalid == "egg":
        world.rows[0]["is_egg"] = 1
    elif invalid == "bad_checksum":
        world.rows[0]["checksum_ok"] = False
    elif invalid == "capacity":
        world.rows += [dict(world.rows[1], slot=i) for i in range(2, 7)]
    else:
        world.visit["accepted"] = False
    world.prepare()
    assert world.events[-1] == ("apply_ready", {"token": "t", "ok": False})
    assert world.jobs == []


def test_scene_ack_and_received_ram_without_save_never_report_trade_success(monkeypatch):
    monkeypatch.setenv("SLINK_GEN3_BATTLE_NONCE", "0000BEEF")
    world, blob = trade_world()
    apply(world, blob)
    world.step(2)
    mb_ack(world)
    world.step()
    swap_in_partner(world)
    mb_ack(world)
    world.step()
    assert not [event for event in world.events("trade_done") if event.get("new_key") == KP]


class CartridgeModel:
    """Cartridge protocol seam injected through Entry's documented native factory."""

    def __init__(self):
        self.jobs = []
        self.visit = {"id": 9, "accepted": True, "pre_saved": True, "apply_open": True}

    def __call__(self, runtime):
        self.lua = runtime

        def transfer(_, step, args, done, valid, progress=None):
            job = {"step": step, "args": args, "done": done, "valid": valid,
                   "progress": progress, "handle": runtime.table()}
            self.jobs.append(job)
            return job["handle"]

        def service(*_):
            for job in list(self.jobs):
                if job["handle"].posted or job["handle"].cancelled:
                    continue
                result = job["valid"]()
                ok, why = result if isinstance(result, tuple) else (result, None)
                if not ok:
                    job["handle"].cancelled = True
                    job["done"](why, 0, None)
                else:
                    job["handle"].posted = True
                break

        def cancel(_, handle):
            if handle.posted:
                return False
            handle.cancelled = True
            return True

        return runtime.table(trade_capable=lambda *_: True, trade_eligible=lambda *_: True,
                             trade_visit=lambda *_: runtime.table_from(self.visit),
                             transfer=transfer, service=service, cancel=cancel, idle=lambda *_: True)

    def ack(self, **milestones):
        job = self.jobs[-1]
        assert job["handle"].posted
        if job["progress"]:
            job["progress"](self.lua.table_from(milestones))
        job["done"](None, 0, None)


def durable_client(monkeypatch, player="a", mons=None):
    monkeypatch.setenv("SLINK_GEN3_BATTLE_NONCE", "0000BEEF")
    carrier = CartridgeModel()
    carrier.journal_model = JournalModel(player=player)
    world = World("gen3_rr", "radical_red", "companion", player=player, native=carrier,
                  journal=carrier.journal_model)
    world.set_party(party(A, B) if mons is None else mons)
    world.step_to(60)
    assert world.events("hello")[0]["trade_prepare"] is True
    return world, carrier, world.encode(PARTNER).hex().upper()


def start_client_trade(world, carrier, blob):
    world.command(cmd="apply_prepare", token="t", slot=1, old_key=KB)
    world.step()
    assert world.events("apply_ready")[-1]["ok"] is True
    apply(world, blob, token="t")
    world.step(2)
    assert carrier.jobs[-1]["step"] == "enemy"
    carrier.ack()
    world.step()
    assert carrier.jobs[-1]["step"] == "scene"


def test_client_success_requires_full_witness_and_reports_evolved_readback(monkeypatch):
    world, carrier, blob = durable_client(monkeypatch)
    start_client_trade(world, carrier, blob)
    swap_in_partner(world)
    carrier.ack(commit_entered=True, scene_done=True, save_success=True, final_result="committed")
    world.step()
    assert world.events("trade_done")[-1]["new_key"] == KP
    assert world.events("trade_done")[-1]["token"] == "t"
    assert [j["step"] for j in carrier.jobs] == ["enemy", "scene"]


def test_client_uncertainty_survives_disconnect_and_reset_hello_order(monkeypatch):
    world, carrier, blob = durable_client(monkeypatch)
    start_client_trade(world, carrier, blob)
    carrier.jobs[-1]["progress"](carrier.lua.table(commit_entered=True))
    world.connected = False
    world.client.driver.on_reset()
    world.client.hello_sent = False
    world.step()
    assert world.events("trade_done") == []
    world.connected = True
    start = len(world.sent)
    world.step(2)
    messages = world.sent[start:]
    assert [m["event"] for m in messages if m["event"] in ("hello", "trade_done")] == ["hello", "trade_done"]
    assert all(m.get("party_hidden") for m in messages if m["event"] == "hello")
    assert world.events("trade_done")[-1]["uncertain"] is True
    assert world.events("trade_done")[-1]["after_reset"] is True
    assert world.events("trade_done")[-1]["token"] == "t"


@pytest.mark.parametrize("save_success", [False, True])
def test_frame_rollback_never_qualifies_a_visible_reload_hello(monkeypatch, save_success):
    world, carrier, blob = durable_client(monkeypatch)
    start_client_trade(world, carrier, blob)
    carrier.jobs[-1]["progress"](carrier.lua.table(
        commit_entered=True, scene_done=True, save_success=save_success))
    swap_in_partner(world)  # MODEL contents after the unknown-provenance load
    start = len(world.sent)
    world.frame = 1
    world.step(3)
    messages = world.sent[start:]
    report = next(m for m in messages if m["event"] == "trade_done")
    assert report["uncertain"] is True and report["after_reset"] is True
    hellos = [m for m in messages if m["event"] == "hello"]
    assert len(hellos) == 1 and hellos[0]["party"] == [] and hellos[0]["party_hidden"] is True


def test_unsaved_uncertainty_hides_party_across_reconnect_without_reset(monkeypatch):
    world, carrier, blob = durable_client(monkeypatch)
    start_client_trade(world, carrier, blob)
    swap_in_partner(world)
    carrier.ack(commit_entered=True, scene_done=True)
    world.step()
    assert world.events("trade_done")[-1]["uncertain"] is True
    world.connected = False
    world.step()
    world.connected = True
    world.step()
    assert world.events("hello")[-1]["party"] == []
    assert "pc_boxes" not in world.events("hello")[-1]


def test_waiting_unposted_trade_does_not_hide_an_ordinary_acquisition(monkeypatch):
    world, _carrier, blob = durable_client(monkeypatch)
    world.command(cmd="apply_prepare", token="t", slot=1, old_key=KB)
    world.step()
    world.break_checkpoint()
    apply(world, blob, token="t")
    world.step()
    world.set_party(party(A, B, C))
    world.fire("mon_given")
    world.step(40)
    assert [e["key"] for e in world.events("capture")] == [KC]


def test_waiting_unposted_trade_does_not_hide_a_battle_faint(monkeypatch):
    world, _carrier, blob = durable_client(monkeypatch)
    world.command(cmd="apply_prepare", token="t", slot=1, old_key=KB)
    world.step()
    world.enter_battle([PARTNER])
    apply(world, blob, token="t")
    world.step()
    mons = party(A, B)
    mons[0]["hp"] = 0
    world.set_party(mons)
    world.fire("faint")
    world.step()
    assert [e["key"] for e in world.events("faint")] == [KA]


def test_verified_trade_purges_stale_box_command_only_after_success(monkeypatch):
    world, carrier, blob = durable_client(monkeypatch)
    start_client_trade(world, carrier, blob)
    world.command(cmd="box_mon", key=KB)
    world.step()
    assert world.client.deferred.size(world.client.deferred) == 1
    swap_in_partner(world)
    carrier.ack(commit_entered=True, scene_done=True, save_success=True, final_result="committed")
    world.step()
    assert world.client.deferred.size(world.client.deferred) == 0
    assert world.events("box_mon_failed") == []


def test_late_received_ram_cannot_upgrade_uncertainty_to_success():
    world = TradeWorld()
    job = world.start()
    job["done"]("native timeout", 0, None)
    world.tick()
    world.received()
    for _ in range(20):
        world.tick()
    assert [f for n, f in world.events if n == "trade_done"] == [
        {"token": "t", "uncertain": True, "after_reset": True}]


def test_clean_artifact_cannot_advertise_an_injected_companion(monkeypatch):
    monkeypatch.setenv("SLINK_GEN3_BATTLE_NONCE", "0000BEEF")
    world = World(native=CartridgeModel())
    world.set_party(party(A, B))
    world.step_to(60)
    assert world.events("hello")[0]["trade_prepare"] is False


@pytest.mark.parametrize("scenario", ["plain", "reordered", "battle", "missing_beacon"])
def test_legacy_companion_without_save_witness_refuses_trade(monkeypatch, scenario):
    monkeypatch.setenv("SLINK_GEN3_BATTLE_NONCE", "0000BEEF")
    world, blob = trade_world(present=scenario != "missing_beacon")
    if scenario == "reordered":
        world.set_party(party(B, A))
    elif scenario == "battle":
        world.enter_battle([PARTNER])
    world.command(cmd="apply_prepare", token="legacy", slot=1, old_key=KB)
    apply(world, blob, token="legacy")
    world.step(3)
    assert world.events("apply_ready")[-1]["ok"] is False
    assert world.events("trade_done")[-1]["new_key"] == KB and world.writes == []
    assert world.events("hello")[0]["trade_prepare"] is False


@pytest.mark.parametrize("pack,title", [("gen3_frlg", "firered"), ("gen3_frlg", "leafgreen"),
                                        ("gen3_emerald", "emerald"), ("gen3_rr", "radical_red")])
def test_clean_rom_never_advertises_companion_trade_prepare(pack, title, monkeypatch):
    monkeypatch.setenv("SLINK_GEN3_BATTLE_NONCE", "0000BEEF")
    world = World(pack, title, "clean")
    world.set_party(party(A, B))
    world.step_to(60)
    assert world.events("hello")[0]["trade_prepare"] is False


def server_and_clients_applying(monkeypatch, world, carrier, delay_before_dispatch=0):
    other, other_carrier, _ = durable_client(monkeypatch, player="b", mons=[PARTNER])
    # The completion path now sends a real visible HELLO. Keep this MODEL
    # trainer consistent with B's owned fixture instead of relying on reports
    # that used to bypass a second identity check.
    other.set_trainer(PARTNER["ot_id"], "B")
    other_carrier.journal_model.journal.bind(other_carrier.journal_model.journal, "model-run", f"{PARTNER['ot_id']:08X}")
    other.client.hello_sent = False
    other.step()
    state = make_state_with_link(KB, KP)
    state.links[0].a.species, state.links[0].b.species = 5, 25
    state.handle_event("a", world.events("hello")[-1])
    state.handle_event("b", other.events("hello")[-1])
    # MODEL menu choices only. From PREPARE onward both clients consume the
    # server's commands and produce their own readiness and terminal reports.
    token = _offer_and_pick(state, slot=1)
    b_commands = state.handle_event("b", {"event": "menu_result", "token": token, "choice": 1})
    a_commands = state.handle_event("a", {"event": "noop"})
    assert state.pending_trade["phase"] == "preparing"
    world.command(**next(c for c in a_commands if c["cmd"] == "apply_prepare"))
    other.command(**next(c for c in b_commands if c["cmd"] == "apply_prepare"))
    world.step()
    other.step()
    state.handle_event("a", world.events("apply_ready")[-1])
    b_commands = state.handle_event("b", other.events("apply_ready")[-1])
    assert state.pending_trade["phase"] == "applying"
    a_commands = state.handle_event("a", {"event": "noop"})
    world.command(**next(c for c in a_commands if c["cmd"] == "apply_trade"))
    other.command(**next(c for c in b_commands if c["cmd"] == "apply_trade"))
    other.step(2)
    other_carrier.ack()
    other.step()
    other.set_party([party(A, B)[1]])
    start_b = len(other.sent)
    other_carrier.ack(commit_entered=True, scene_done=True, save_success=True, final_result="committed")
    other.step()
    for message in other.sent[start_b:]:
        state.handle_event("b", message)
    assert other.events("trade_done")[-1]["new_key"] == KB
    assert state.pending_trade["verdict"] == {"a": None, "b": "traded"}
    assert (state.links[0].a.key, state.links[0].b.key) == (KB, KP)
    world.step()
    world.frame += delay_before_dispatch
    world.step()
    carrier.ack()
    world.step()
    return state, token, other


def test_unsaved_uncertainty_cross_wire_waits_for_qualified_reload_not_reset(monkeypatch):
    world, carrier, _ = durable_client(monkeypatch)
    state, token, _other = server_and_clients_applying(monkeypatch, world, carrier)
    start = len(world.sent)
    swap_in_partner(world)
    carrier.ack(commit_entered=True, scene_done=True)
    world.step(35)
    for message in world.sent[start:]:
        state.handle_event("a", message)
    report = next(m for m in world.sent[start:] if m["event"] == "trade_done")
    assert report["token"] == token and report["uncertain"] is True and report["after_reset"] is True
    assert state.pending_trade is not None
    assert state.pending_trade["verdict"]["a"] == "await"
    assert (state.links[0].a.key, state.links[0].b.key) == (KB, KP)
    assert all(not m.get("party") for m in world.sent[start:] if m["event"] == "tick")
    # A reset callback alone supplies no qualified battery evidence.
    start = len(world.sent)
    world.client.driver.on_reset()
    world.client.hello_sent = False
    world.step(3)
    for message in world.sent[start:]:
        state.handle_event("a", message)
    assert state.pending_trade is not None and state.pending_trade["verdict"]["a"] == "await"
    assert (state.links[0].a.key, state.links[0].b.key) == (KB, KP)


def test_reset_uncertainty_cross_wire_stays_hidden_without_battery_proof(monkeypatch):
    world, carrier, _ = durable_client(monkeypatch)
    state, token, _other = server_and_clients_applying(monkeypatch, world, carrier)
    carrier.jobs[-1]["progress"](carrier.lua.table(commit_entered=True))
    world.connected = False
    world.client.driver.on_reset()
    world.client.hello_sent = False
    swap_in_partner(world)  # MODEL: the reloaded native save contains the received mon
    world.step()
    world.connected = True
    start = len(world.sent)
    world.step(3)
    messages = world.sent[start:]
    hellos = [m for m in messages if m["event"] == "hello"]
    assert hellos[0]["party"] == []
    assert len(hellos) == 1 and hellos[0]["party_hidden"] is True
    for message in messages:
        state.handle_event("a", message)
    assert next(m for m in messages if m["event"] == "trade_done")["after_reset"] is True
    assert state.pending_trade is not None


def test_reset_without_evidence_never_learns_the_withheld_party_baseline(monkeypatch):
    world, carrier, blob = durable_client(monkeypatch)
    start_client_trade(world, carrier, blob)
    carrier.jobs[-1]["progress"](carrier.lua.table(commit_entered=True))
    world.connected = False
    world.client.driver.on_reset()
    world.client.hello_sent = False
    swap_in_partner(world)
    world.step()
    world.connected = True
    world.step(2)
    existing = party(A, B, C)
    existing[1] = PARTNER
    world.set_party(existing)
    world.fire("mon_given")
    world.step(40)
    assert world.events("capture") == []


def test_v1_refusal_answers_certain_unchanged_and_cancels_menu_on_wire(monkeypatch):
    monkeypatch.setenv("SLINK_GEN3_BATTLE_NONCE", "0000BEEF")
    world, blob = trade_world()
    apply(world, blob, token="refused")
    world.step()
    report = world.events("trade_done")[-1]
    assert (report["token"], report["slot"], report["new_key"], report["new_species"]) == ("refused", 1, KB, 0)
    assert world.events("menu_result")[-1]["withdraw"] is True
    assert world.writes == []


def test_uncertainty_surfaces_player_text_and_reason_once():
    world = TradeWorld()
    job = world.start()
    job["done"]("native timeout", 0, None)
    world.tick()
    job["done"]("native timeout", 0, None)
    world.tick()
    assert world.hud_messages == ["TRADE UNCERTAIN - CHECK PARTY"]
    assert len(world.logs) == 1 and "native timeout" in world.logs[0]


def test_accepted_apply_outlives_ready_deadline_without_splitting_pair(monkeypatch):
    world, carrier, _ = durable_client(monkeypatch)
    state, token, other = server_and_clients_applying(monkeypatch, world, carrier, delay_before_dispatch=601)
    outcomes = []
    state.on_trade_outcome = outcomes.append
    start = len(world.sent)
    swap_in_partner(world)
    carrier.ack(commit_entered=True, scene_done=True, save_success=True, final_result="committed")
    world.step()
    for message in world.sent[start:]:
        state.handle_event("a", message)
    assert state.pending_trade is None
    assert (state.links[0].a.key, state.links[0].b.key) == (KP, KB)
    # Replayed production reports from either client cannot commit a second time.
    state.handle_event("a", world.events("trade_done")[-1])
    state.handle_event("b", other.events("trade_done")[-1])
    # A visible HELLO is recorded as checkpoint recovery before its party
    # evidence settles. Only one terminal commit may result, including replays.
    assert all(o["outcome"] in ("uncertain", "committed") for o in outcomes)
    assert [(o["token"], o["outcome"]) for o in outcomes if o["outcome"] != "uncertain"] == [(token, "committed")]
    assert state.trade_problem() is None


@pytest.mark.parametrize("phase", ["wait", "queued_stage", "queued_scene"])
def test_expired_apply_before_scene_publication_reports_certain_unchanged(phase):
    world = TradeWorld()
    world.prepare()
    world.command("apply", token="t", old_key=world.rows[0]["key"], slot=0, blob_hex="05" * 100)
    if phase != "wait":
        world.tick()
    if phase == "queued_scene":
        stage = world.jobs[-1]
        world.dispatch(stage)
        stage["done"](None, 0, None)
    world.frame += 1801
    if phase != "wait":
        job = world.jobs[-1]
        ok, reason = job["valid"]()
        assert ok is False
        job["done"](reason, 0, None)
    world.tick()
    assert [f for n, f in world.events if n == "trade_done"] == [
        {"token": "t", "slot": 0, "new_key": world.rows[0]["key"], "new_species": 0}]
    assert all(not j["handle"].posted for j in world.jobs if j["step"] == "scene")
    assert world.trade.state(world.trade) == (None, None)


def test_withdrawal_drains_posted_staging_before_releasing_trade_ownership():
    world = TradeWorld()
    world.prepare()
    world.command("apply", token="t", old_key=world.rows[0]["key"], slot=0, blob_hex="05" * 100)
    world.tick()
    stage = world.jobs[-1]
    world.dispatch(stage)
    world.command("withdraw", token="t")
    world.tick()
    assert world.trade.state(world.trade)[0].phase == "draining"
    assert not [f for n, f in world.events if n == "trade_done"]
    world.command("prepare", token="u", old_key=world.rows[0]["key"], slot=0)
    assert world.events[-1] == ("apply_ready", {"token": "u", "ok": False})
    stage["done"](None, 0, None)
    world.tick()
    assert [j["step"] for j in world.jobs] == ["enemy"]
    assert world.trade.state(world.trade) == (None, None)
    assert [f for n, f in world.events if n == "trade_done"] == [
        {"token": "t", "slot": 0, "new_key": world.rows[0]["key"], "new_species": 0}]
    world.command("prepare", token="u", old_key=world.rows[0]["key"], slot=0)
    assert world.events[-1] == ("apply_ready", {"token": "u", "ok": True})


def test_partner_key_comes_from_the_read_facade_not_duplicate_blob_parsing():
    world = TradeWorld()
    profile = json.loads((ROOT / "data/games/gen3_rr/profile.json").read_text())["titles"]["radical_red"]
    reads_module = world.lua.execute((ROOT / "lua/gen3/reads.lua").read_text(encoding="utf-8"))
    reads = reads_module.new(world.lua.table_from(profile, recursive=True), world.lua.table(
        read_u8=lambda _: 0, read_u16=lambda _: 0, read_u32=lambda _: 0,
        read_bytes=lambda _, n: world.lua.table_from([0] * n)))
    blob = gw.codec.encode_party_mon(PARTNER, rr=True).hex().upper()
    decoded = []

    def decode(hex_blob):
        mon = reads.decode_party_mon(world.lua.table_from(bytes.fromhex(hex_blob)))
        decoded.append(mon)
        return mon

    # Deliberately distinct facade projection: decoding still consumes the real
    # blob, but a second PID/OTID parser in trade.lua cannot synthesize this key.
    world.deps.decode_blob = decode
    world.deps.key = lambda mon: mon.key or "facade/" + reads.key(mon)
    world.prepare()
    world.command("apply", token="t", slot=0, old_key=world.rows[0]["key"], blob_hex=blob)
    world.tick()
    stage = world.jobs[-1]
    world.dispatch(stage)
    stage["done"](None, 0, None)
    scene = world.jobs[-1]
    world.dispatch(scene)
    world.progress(scene, commit_entered=True, scene_done=True, save_success=True, final_result="committed")
    assert len(decoded) == 1 and reads.key(decoded[0]) == KP and decoded[0].species == 25
    world.rows[0] = dict(lua_to_py(decoded[0]), slot=0)
    scene["done"](None, 0, None)
    world.tick()
    assert [f for n, f in world.events if n == "trade_done"][-1]["new_key"] == "facade/" + KP


@pytest.mark.parametrize("fault", ["decode", "eligible", "key"])
def test_apply_facade_exception_reports_unchanged_and_releases_prepared(fault):
    world = TradeWorld()
    world.prepare()

    def fail(*_):
        raise RuntimeError("incoming facade failure")

    if fault == "decode":
        world.deps.decode_blob = fail
    elif fault == "eligible":
        world.native.trade_eligible = lambda _, mon: fail() if mon.key == world.incoming["key"] else True
    else:
        world.deps.key = lambda mon: fail() if mon.key == world.incoming["key"] else mon.key
    world.command("apply", token="t", slot=0, old_key=world.rows[0]["key"], blob_hex="05" * 100)
    world.tick()
    assert world.trade.state(world.trade) == (None, None)
    assert world.jobs == []
    assert [f for n, f in world.events if n == "trade_done"] == [
        {"token": "t", "slot": 0, "new_key": world.rows[0]["key"], "new_species": 0}]


def test_reissued_text_token_in_new_epoch_does_not_hit_old_retirement():
    world = TradeWorld()
    world.prepare()
    world.command("withdraw", token="t")
    world.epoch = 2
    world.visit["id"] = 8
    world.prepare()
    assert world.events[-1] == ("apply_ready", {"token": "t", "ok": True})


def test_epoch_change_keeps_unanswered_report_and_blocks_ambiguous_token_reuse():
    world = TradeWorld()
    world.hold_reports = True
    world.prepare()
    world.command("withdraw", token="t")
    world.epoch, world.visit["id"] = 2, 8
    world.prepare()
    assert world.events[-1] == ("apply_ready", {"token": "t", "ok": False})
    assert (1, "t") in world.pending_reports
    world.pending_reports.clear()  # the server acknowledged the old epoch's report
    world.prepare()
    assert world.events[-1] == ("apply_ready", {"token": "t", "ok": True})


def test_acknowledged_unsaved_uncertainty_keeps_reset_barrier_across_epochs():
    world = TradeWorld()
    old_scene = world.start()
    old_scene["done"]("native timeout", 0, None)
    world.tick()
    world.epoch, world.visit["id"] = 2, 8
    world.prepare()
    assert world.events[-1] == ("apply_ready", {"token": "t", "ok": False})
    assert world.trade.hide_party(world.trade) is True
    # Delivery and reset are not evidence of a native save. Neither may retire
    # the persistent barrier, even when no transport report is outstanding.
    world.trade.reset(world.trade)
    world.tick()
    assert world.trade.reloaded(world.trade, "t", 1) is False
    assert world.trade.hide_party(world.trade) is True
    world.trade.allow_reload_evidence(world.trade, "t", 1)
    assert world.trade.hide_party(world.trade) is True
    world.trade.server_final(world.trade, world.lua.table(token="t", epoch=1, verdict="resolved"))
    assert world.trade.hide_party(world.trade) is True
    world.trade.reset(world.trade)
    world.tick()
    assert world.trade.reloaded(world.trade, "t", 1) is False
    assert not world.trade.reloaded(world.trade, "t", 2)
    world.trade.allow_reload_evidence(world.trade, "t", 1)
    assert world.trade.hide_party(world.trade) is True
    world.trade.allow_reload_evidence(world.trade, "t", 2)
    assert world.trade.hide_party(world.trade) is True
    old_scene["done"](None, 0, None)
    world.tick()
    assert not [f for n, f in world.events if n == "trade_done" and "new_key" in f]


def test_client_reconnect_reuses_token_only_after_old_report_is_acknowledged(monkeypatch):
    world, carrier, _blob = durable_client(monkeypatch)
    world.command(cmd="apply_prepare", token="t", slot=1, old_key=KB)
    world.step()
    world.command(cmd="withdraw_trade", token="t")
    world.step()
    world.connected = False
    world.step()
    world.connected = True
    before_reconnect = len(world.sent)
    world.step()
    carrier.visit["id"] += 1
    world.command(cmd="apply_prepare", token="t", slot=1, old_key=KB)
    world.step()
    assert world.events("apply_ready")[-1]["ok"] is False
    connection_lines = world.sent[before_reconnect:]
    assert connection_lines[0]["event"] == "hello"
    report_index = next(i for i, m in enumerate(connection_lines)
                        if m["event"] == "trade_done" and m["token"] == "t")
    assert report_index > 0 and connection_lines[report_index]["new_key"] == KB
    # The prepare command above answered line 0 (hello). Respond in order only
    # through the specifically identified replay, not every outstanding line.
    for message in connection_lines[1:report_index]:
        assert message["event"] != "trade_done"
        world.replies.append(json.dumps({"commands": []}))
        world.step()
    world.replies.append(json.dumps({"commands": []}))  # ACK this exact report line
    world.step()
    world.command(cmd="apply_prepare", token="t", slot=1, old_key=KB)
    world.step()
    assert world.events("apply_ready")[-1]["ok"] is True


def test_a_briefly_unauthorized_native_prepare_is_held_not_refused():
    """T5 LG->FR 2026-09-27: apply_prepare arrived while the native side was still busy
    ("native not authorized") and the terminal ok=false cancelled the whole trade. A valid
    prepare waits up to prepare_frames for authorization, then proceeds."""
    world = TradeWorld()
    ready = {"ok": False}
    pending = []
    world.native.trade_authorized = lambda *_: ready["ok"]
    world.native.prepare_trade = lambda _, cmd, done, valid: pending.append((cmd, done, valid)) or world.lua.table()
    world.prepare()
    world.tick(); world.tick()
    assert world.events == [] and pending == []
    ready["ok"] = True
    world.tick()
    assert len(pending) == 1 and world.events == []


@pytest.mark.parametrize("hold", ["native", "journal"])
@pytest.mark.parametrize("new_connection", [False, True])
def test_reset_retires_held_readiness_in_its_original_epoch(hold, new_connection):
    world = TradeWorld()
    ready = {"ok": False}
    pending = []
    world.native.trade_authorized = lambda *_: ready["ok"]
    world.native.prepare_trade = lambda _, cmd, done, valid: pending.append(cmd) or world.lua.table()
    journal = world.deps.journal
    original_ready = journal.ready
    if hold == "journal":
        journal.busy = True
        journal.ready = lambda _: False
    world.prepare()
    assert not world.events and not pending

    world.connected = False
    if new_connection:
        world.epoch += 1
    world.trade.reset(world.trade)
    journal.ready, journal.busy = original_ready, False
    ready["ok"] = True
    world.frame = 1
    world.connected = True
    world.tick()
    world.tick()

    assert not pending
    assert world.events == ([] if new_connection else [("apply_ready", {"token": "t", "ok": False})])
    assert any("reset" in line for line in world.logs)
    world.command("prepare", token="fresh", old_key=world.rows[0]["key"])
    assert len(pending) == 1 and pending[0].token == "fresh"


def test_a_prepare_that_never_gets_authorized_still_refuses_at_the_deadline():
    world = TradeWorld()
    world.native.trade_authorized = lambda *_: False
    world.native.prepare_trade = lambda *_: world.lua.table()
    world.prepare()
    assert world.events == []
    world.frame += 601
    world.tick()
    assert world.events[-1] == ("apply_ready", {"token": "t", "ok": False})
