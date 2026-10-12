"""FireRed ABI2 trade consumer MODEL controls; no production readiness claim."""

import pytest
from lupa import lua54

from server.adapters.gen3_codec import encode_party_mon
from tests.unit import test_gen3_native as native_model
from tests.unit.gen3_trade_journal_model import JournalModel
from tests.unit.gen3_world import mon_record
from tests.unit.test_gen3_native import World


@pytest.fixture(autouse=True)
def bizhawk_lua_version(monkeypatch):
    monkeypatch.setattr(native_model, "lupa", lua54)


class TradeNativeWorld(World):
    def __init__(self, *, capability=1, production=True, title="firered",
                 player="a", initial_seq=10, epoch=0x12345678, pack="gen3_frlg"):
        super().__init__(abi=2, pack=pack)
        self.battle = False
        self.recovery_clear = True
        assert self.lua.eval("_VERSION") == "Lua 5.4"
        self.put(self.n["BASE"] + 0x40, capability, 4)
        self.module = self.lua.execute((native_model.ROOT / "lua/gen3/native.lua").read_text())
        self.native = self.module.new(self.lua.table_from(self.profile, recursive=True), self.lua.table(
            io=self.native_io, writes=self.writes, reads=self.reads, title=title, player=player,
            production=production, artifact_kind="companion", initial_seq=initial_seq,
            timeout_frames=8, send=lambda event, fields: self.events.append((event, fields)),
            log=self.logs.append, in_battle=lambda: self.battle,
            trade_recovery_clear=lambda: self.recovery_clear,
            trade_safe=lambda: self.safe and not self.battle))
        self.native.set_session_epoch(self.native, epoch)
        self.service()
        self.old = mon_record(1, 2, species=4)
        self.incoming = mon_record(5, 6, species=7)
        self.old_key = "00000001:00000002"
        self.incoming_key = "00000005:00000006"
        self.prepared_results = []
        self.seed_party(self.old, mon_record(3, 4, species=1))

    def seed_party(self, *rows):
        self.put(self.ram["PARTY_COUNT_ADDR"], len(rows))
        for slot, row in enumerate(rows):
            raw = encode_party_mon(row, rr=False)
            for i, byte in enumerate(raw):
                self.put(self.ram["PARTY_BASE"] + slot * 100 + i, byte)

    def phase(self, value):
        self.put(self.n["BASE"] + 0x48, value, 4)

    def prepare(self, *, ready=True, token="server-token"):
        job = self.native.prepare_trade(self.native, self.lua.table(token=token, old_key=self.old_key, slot=0),
                                        lambda *args: self.prepared_results.append(args), lambda: True)
        self.service()
        assert job.posted
        self.prepare_seq = job.seq
        self.prepare_args = bytes(job.args.values())
        if ready:
            self.witness(bits=1, result=0, phase=2)
            self.ack()
            self.service()
        return job

    def witness(self, *, bits=31, result=1, phase=4, revision=2, flags=3, saved=1, final_seq=None):
        base = self.n["BASE"] + 0x50
        raw = bytearray(80)
        raw[0:4] = (0x12345678).to_bytes(4, "little")
        raw[4:8] = self.prepare_args[12:16]
        raw[8:24] = self.prepare_args[16:32]
        raw[24:26] = revision.to_bytes(2, "little")
        raw[26:28] = flags.to_bytes(2, "little")
        raw[28:32] = bits.to_bytes(4, "little")
        scene = getattr(self, "scene_seq", 999)
        for i, seq in enumerate((self.prepare_seq, scene, scene, scene, final_seq if final_seq is not None else scene)):
            raw[32 + i*2:34 + i*2] = seq.to_bytes(2, "little")
        raw[42], raw[43] = result, saved
        for i in range(5):
            raw[44 + i*4:48 + i*4] = (123).to_bytes(4, "little")  # same native frame is valid
        raw[64:72] = self.prepare_args[4:12]
        raw[72:80] = bytes.fromhex("0500000006000000")
        for i, byte in enumerate(raw):
            self.put(base + i, byte)
        self.phase(phase)

    def stage_scene(self):
        self.prepare()
        got = []
        stage = self.native.transfer(self.native, "enemy", self.lua.table(
            blobs_hex=self.lua.table(encode_party_mon(self.incoming, rr=False).hex())), lambda *a: got.append(a), lambda: True)
        self.service()
        assert not stage.posted and got == [(None,)]
        self.progress, self.results = [], []
        visit = self.native.trade_visit(self.native)
        job = self.native.transfer(self.native, "scene", self.lua.table(
            token="server-token", old_key=self.old_key, slot=0, visit=visit.id),
            lambda *a: self.results.append(a), lambda: True, lambda w: self.progress.append(to_python(w)))
        self.service()
        assert job.posted
        self.scene_seq = job.seq
        return job


def to_python(value):
    if lua54.lua_type(value) == "table":
        return {k: to_python(v) for k, v in value.items()}
    return value


def _binding_world(title):
    if title == "radical_red":
        from tests.unit.test_gen3_native_rr_trade import RRTradeWorld
        return RRTradeWorld(bind=False)
    return TradeNativeWorld(title=title)


@pytest.mark.parametrize("title", ["firered", "leafgreen", "radical_red"])
@pytest.mark.parametrize("reset", ["frame", "beacon", "epoch"])
@pytest.mark.parametrize("blocked", [None, "journal", "producer"])
def test_automatic_binding_recovers_only_when_unowned_and_clear(title, reset, blocked):
    w = _binding_world(title)
    assert w.native.bind_trade_session(w.native, 9)
    w.service()
    assert w.native.trade_capable(w.native)
    epoch_address = (w.n.get("TRADE_BASE") or w.n["BASE"]) + 0x44
    w.put(epoch_address, 0, 4)
    w.output.clear()
    if blocked == "journal":
        w.recovery_clear = False
    elif blocked == "producer":
        w.phase(1)
    if reset == "frame":
        w.frame = 1
    elif reset == "beacon":
        w.put(w.n["BASE"], 0, 4)
        w.service()
        w.put(w.n["BASE"], w.n["SIG"], 4)
    w.service()
    if blocked:
        assert not w.native.trade_capable(w.native) and not w.output
        w.recovery_clear = True
        w.phase(0)
        w.service()
    assert w.native.trade_capable(w.native)
    assert w.read(epoch_address, 4) == 9


@pytest.mark.parametrize("title", ["firered", "leafgreen", "radical_red"])
@pytest.mark.parametrize("retire", ["cancel", "reset", "guard"])
def test_retired_automatic_binding_can_retry(title, retire):
    w = _binding_world(title)
    jobs = []
    enqueue = w.native.set_session_epoch

    def capture(native, epoch):
        job = enqueue(native, epoch)
        if lua54.lua_type(job) == "table":
            jobs.append(job)
        return job

    w.native.set_session_epoch = capture
    w.safe = False
    assert w.native.bind_trade_session(w.native, 9)
    w.service()
    assert len(jobs) == 1
    if retire == "cancel":
        assert w.native.cancel(w.native, jobs[0])
    elif retire == "reset":
        w.frame = 1
        w.service()
    else:
        w.recovery_clear = False
        w.service()
        w.recovery_clear = True
    w.safe = True
    w.service()
    assert w.native.trade_capable(w.native)
    assert w.native.mailbox(w.native).session_epoch == 9


@pytest.mark.parametrize("field", ["session_epoch", "visit_id", "token", "revision", "visit_flags",
                                  "milestones", "milestone_seq", "final_result", "save_status",
                                  "milestone_frame", "old_pid", "old_otid", "received_pid", "received_otid"])
def test_trade_witness_layout_is_validated_before_native_io(field):
    def corrupt(native):
        native["abi_v2"]["structs"]["SlinkTradeWitnessV2"]["fields"][field]["offset"] += 1
    with pytest.raises(lua54.LuaError, match="trade witness field"):
        World(abi=2, pack="gen3_frlg", mutate_native=corrupt)


def test_trade_witness_size_is_validated_before_native_io():
    def corrupt(native):
        native["abi_v2"]["structs"]["SlinkTradeWitnessV2"]["size"] -= 1
    with pytest.raises(lua54.LuaError, match="trade witness size"):
        World(abi=2, pack="gen3_frlg", mutate_native=corrupt)


@pytest.mark.parametrize("capability,production,title,capable", [
    (1, True, "firered", True), (0, True, "firered", False),
    (4, True, "firered", False), (1, False, "firered", False),
    (1, None, "firered", False), (1, True, "leafgreen", True), (1, True, "emerald", False),
])
def test_trade_capability_requires_advertised_bit_and_production_identity(capability, production, title, capable):
    world = TradeNativeWorld(capability=capability, production=production, title=title)
    assert world.native.trade_capable(world.native) is capable


def test_prepare_requires_consent_presave_and_matching_ready_phase():
    w = TradeNativeWorld()
    job = w.prepare(ready=False)
    assert w.read(w.n["BASE"] + 6, 2) == 29
    assert job.args[1] == 0 and job.args[2] == 0
    assert bytes(job.args.values())[4:12] == bytes.fromhex("0100000002000000")
    w.witness(bits=0, flags=1, saved=0, result=0, phase=1)
    w.service()
    assert w.native.trade_visit(w.native)[0] is None
    assert not w.prepared_results
    w.witness(bits=1, result=0, phase=2)
    w.ack()
    w.service()
    assert w.prepared_results[0][0] is None
    assert w.native.trade_visit(w.native).old_key == w.old_key


def test_emerald_published_binding_uses_same_durable_contract():
    w=TradeNativeWorld(title="emerald",pack="gen3_emerald",capability=87)
    assert w.native.trade_capable(w.native) is True
    w.prepare(ready=False)
    assert w.read(w.n["BASE"]+6,2)==29
    w.witness(bits=1,result=0,phase=2)
    w.ack()
    w.service()
    assert w.native.trade_visit(w.native).old_key==w.old_key


@pytest.mark.parametrize("field,value", [("is_egg", 1), ("is_bad_egg", 1), ("species", 0),
                                       ("species", 412), ("held_item", 121), ("held_item", 132), ("mail", 0)])
def test_ineligible_outgoing_never_posts_prepare(field, value):
    w = TradeNativeWorld()
    w.old[field] = value
    w.seed_party(w.old)
    assert not w.native.trade_authorized(w.native, "t", w.old_key)
    before = list(w.output)
    result = w.native.prepare_trade(w.native, w.lua.table(token="t", old_key=w.old_key, slot=0), None, None)
    assert result[0] is None and w.output == before


@pytest.mark.parametrize("problem", ["duplicate", "checksum", "unsafe", "battle"])
def test_local_eligibility_and_unique_identity_are_checked(problem):
    w = TradeNativeWorld()
    if problem == "duplicate":
        w.seed_party(w.old, w.old)
    elif problem == "checksum":
        w.put(w.ram["PARTY_BASE"] + 28, 0xFFFF, 2)
    elif problem == "unsafe":
        w.safe = False
    else:
        w.battle = True
    assert not w.native.trade_authorized(w.native, "t", w.old_key)


@pytest.mark.parametrize("phase", [1, 2, 3, 4, 5, 6, 0xFFFFFFFF])
def test_owned_unknown_or_unreconciled_done_phase_blocks_epoch_rewrite(phase):
    w = TradeNativeWorld()
    w.phase(phase)
    before = list(w.output)
    assert w.native.set_session_epoch(w.native, 9)[0] is None
    w.service()
    assert w.output == before


def test_epoch_prepare_race_is_rechecked_at_dispatch():
    w = TradeNativeWorld()
    job = w.native.set_session_epoch(w.native, 9)
    w.phase(1)
    before = list(w.output)
    w.service()
    assert not job.posted and w.output == before
    assert w.read(w.n["BASE"] + 0x44, 4) == 0x12345678


def test_epoch_binding_waits_for_journal_reconciliation():
    w = TradeNativeWorld()
    w.recovery_clear = False
    before = list(w.output)
    assert w.native.set_session_epoch(w.native, 9)[0] is None
    w.service()
    assert w.output == before


@pytest.mark.parametrize("loss", ["epoch_zero", "epoch_changed", "capability", "frame_reset", "beacon"])
def test_owned_scene_cannot_be_rearmed_after_a_discontinuity(loss):
    w = TradeNativeWorld()
    w.stage_scene()
    if loss == "epoch_zero":
        w.put(w.n["BASE"] + 0x44, 0, 4)
    elif loss == "epoch_changed":
        w.put(w.n["BASE"] + 0x44, 9, 4)
    elif loss == "capability":
        w.put(w.n["BASE"] + 0x40, 0, 4)
    elif loss == "frame_reset":
        w.frame = 0
    else:
        w.put(w.n["BASE"], 0, 4)
    w.service()
    assert not w.native.trade_capable(w.native)
    assert w.results and w.results[0][0] is not None
    # Restoring RAM words is not independent save/journal reconciliation.
    w.put(w.n["BASE"], w.n["SIG"], 4)
    w.put(w.n["BASE"] + 0x40, 1, 4)
    w.put(w.n["BASE"] + 0x44, 0, 4)
    w.phase(0)
    w.ack()
    w.service()
    assert w.native.set_session_epoch(w.native, 10)[0] is None


def test_prepare_token_mapping_uses_the_whole_server_identity_and_unique_visits():
    w = TradeNativeWorld()
    w.prepare(token="same-prefix-0000000000000001")
    first = w.prepare_args
    withdraw = w.native.withdraw_trade(w.native, "same-prefix-0000000000000001")
    w.service()
    w.witness(bits=17, result=2, final_seq=withdraw.seq)
    w.ack(status=3)
    w.service()
    w.prepare(token="same-prefix-0000000000000002")
    assert first[12:16] != w.prepare_args[12:16]
    assert first[16:32] != w.prepare_args[16:32]


def test_full_witness_is_bound_to_original_arguments_and_scene_sequences():
    w = TradeNativeWorld()
    job = w.stage_scene()
    assert job.op == 21
    assert bytes(job.args.values()) == w.prepare_args
    w.witness()
    w.ack()
    w.service()
    assert w.results == [(None, 0, None)]
    assert w.progress[-1] == {"commit_entered": True, "scene_done": True, "save_success": True,
                             "final_result": "committed", "unchanged_proved": False}
    assert w.native.trade_active(w.native)
    assert w.native.trade_reconciled(w.native, "wrong-token") is False
    assert w.native.trade_reconciled(w.native, "server-token") is True
    assert not w.native.trade_active(w.native)


@pytest.mark.parametrize("offset,width,value", [
    (0, 4, 9), (4, 4, 9), (8, 1, 0), (23, 1, 0), (24, 2, 0), (24, 2, 3),
    (26, 2, 1), (28, 4, 29), (28, 4, 27), (28, 4, 23), (28, 4, 15),
    (32, 2, 999), (34, 2, 999), (36, 2, 999), (38, 2, 999), (40, 2, 999),
    (42, 1, 0), (42, 1, 2), (43, 1, 0), (64, 4, 9), (68, 4, 9), (72, 4, 9), (76, 4, 9),
])
def test_ack_cannot_substitute_for_every_required_witness_binding(offset, width, value):
    w = TradeNativeWorld()
    w.stage_scene()
    w.witness()
    w.put(w.n["BASE"] + 0x50 + offset, value, width)
    w.ack()
    w.service()
    assert w.results[0][0] is not None
    assert not any(p.get("save_success") for p in w.progress)
    assert not w.native.trade_capable(w.native)


def test_unchanged_is_positive_terminal_evidence_not_a_failure_reason():
    w = TradeNativeWorld()
    w.stage_scene()
    w.witness(bits=17, result=2)
    w.ack(status=3)
    w.service()
    assert w.progress[-1]["unchanged_proved"]
    assert w.results[0][0] == "native refused"
    assert w.native.trade_reconciled(w.native, "server-token")


def test_commit_bit_cannot_disappear_into_an_unchanged_result():
    w = TradeNativeWorld()
    w.stage_scene()
    w.witness(bits=3, result=0, phase=3)
    w.service()
    assert w.progress[-1]["commit_entered"]
    w.witness(bits=17, result=2)
    w.ack(status=3)
    w.service()
    assert not any(p.get("unchanged_proved") for p in w.progress)
    assert not w.native.trade_capable(w.native)


@pytest.mark.parametrize("phase", [0, 1, 2, 3, 5, 99])
def test_terminal_witness_requires_native_done_phase(phase):
    w = TradeNativeWorld()
    w.stage_scene()
    w.witness(phase=phase)
    w.ack()
    w.service()
    assert w.results[0][0] is not None
    assert not any(p.get("save_success") for p in w.progress)


def test_torn_revision_is_not_a_witness_even_with_a_matching_ack():
    w = TradeNativeWorld()
    w.stage_scene()
    w.witness()
    before_read = w.native_io.read_bytes

    def torn(address, size):
        result = before_read(address, size)
        if address == w.n["BASE"] + 0x50:
            w.put(address + 0x18, 4, 2)
        return result
    w.native_io.read_bytes = torn
    w.ack()
    w.service()
    assert w.results[0][0] is not None
    assert not any(p.get("save_success") for p in w.progress)


def test_scene_staging_is_owned_and_overwrite_cannot_be_acked_as_success():
    w = TradeNativeWorld()
    w.stage_scene()
    w.witness()
    w.put(w.n["BLOB_BUF"] + 99, 0xFE)
    w.ack()
    w.service()
    assert w.results[0][0] == "native staging overwritten"
    assert not w.progress


def test_withdraw_has_its_own_sequence_and_too_late_is_distinct():
    w = TradeNativeWorld()
    w.prepare()
    job = w.native.withdraw_trade(w.native, "server-token")
    w.service()
    assert job.op == 30 and job.posted
    w.witness(bits=17, result=2, final_seq=job.seq)
    w.ack(status=3)
    w.service()
    assert not w.native.trade_active(w.native)
    w.put(w.n["BASE"] + 14, 14, 2)
    assert w.native.mailbox(w.native).reason_name == "withdraw_too_late"


def test_native_v2_never_uses_legacy_record_replacement_opcodes():
    w = TradeNativeWorld()
    w.prepare()
    result = w.native.transfer(w.native, "party", w.lua.table(slot=0, blob_hex="00"*100), None, None)
    assert result[0] is None
    w.service()
    assert w.read(w.n["BASE"] + 6, 2) == 0


class ProtocolWorld(TradeNativeWorld):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.trace = []
        self.flush_fails = False
        self.journal_model = JournalModel(ot="00000002")
        self.journal = self.journal_model(self.lua)
        module = self.lua.execute((native_model.ROOT / "lua/gen3/trade.lua").read_text())

        def flush():
            self.trace.append(("saveram", None))
            if self.flush_fails:
                raise OSError("MODEL flush failed")

        def decode(hex_string):
            return self.reads.decode_party_mon(self.lua.table(*bytes.fromhex(hex_string)))

        self.trade = module.new(self.lua.table(
            native=self.native, journal=self.journal, frame=lambda: self.frame, epoch=lambda: 1,
            eligible=lambda: True, ready_to_send=lambda: True, clear=lambda: True,
            capacity=6, mon_size=100, prepare_frames=600, apply_frames=1800,
            decode_blob=decode, key=self.reads.key, party=self.reads.read_party, saveram=flush,
            send=lambda name, fields, *_: self.trace.append((name, to_python(fields))),
            log=lambda text: self.trace.append(("log", text))))

    def start_protocol(self):
        self.trade.prepare(self.trade, self.lua.table(token="server-token", old_key=self.old_key))
        self.service()
        self.prepare_seq = self.read(self.n["BASE"] + 8, 2)
        self.prepare_args = self.raw(self.n["BASE"] + 16, 32)
        assert self.read(self.n["BASE"] + 6, 2) == 29
        self.witness(bits=1, result=0, phase=2)
        self.ack()
        self.service()
        assert ("apply_ready", {"token": "server-token", "ok": True}) in self.trace
        self.trade.apply(self.trade, self.lua.table(token="server-token", old_key=self.old_key,
                                                   blob_hex=encode_party_mon(self.incoming, rr=False).hex()))
        self.trade.tick(self.trade)
        self.service()  # stage-only; completion persists journal before queuing scene
        assert self.journal.hidden(self.journal)
        self.service()
        assert self.read(self.n["BASE"] + 6, 2) == 21
        self.scene_seq = self.read(self.n["BASE"] + 8, 2)


@pytest.mark.parametrize("flush_fails", [False, True])
def test_journal_native_scene_saveram_and_trade_done_end_to_end_model(flush_fails):
    w = ProtocolWorld()
    w.start_protocol()
    w.flush_fails = flush_fails
    w.seed_party(w.incoming, mon_record(3, 4, species=1))
    w.witness()
    w.ack()
    w.service()
    w.trade.tick(w.trade)
    done = [v for name, v in w.trace if name == "trade_done"]
    if flush_fails:
        assert done == [{"token": "server-token", "uncertain": True, "after_reset": True}]
        assert w.journal.hidden(w.journal)
        assert w.native.trade_active(w.native)
    else:
        assert done == [{"token": "server-token", "slot": 0, "new_key": w.incoming_key, "new_species": 7}]
        assert not w.journal.hidden(w.journal)
        assert not w.native.trade_active(w.native)
    names = [name for name, _ in w.trace]
    assert names.index("saveram") < names.index("trade_done")


def test_unchanged_scene_can_retire_write_ahead_only_with_bound_native_proof():
    w = ProtocolWorld()
    w.start_protocol()
    w.witness(bits=17, result=2)
    w.ack(status=3)
    w.service()
    w.trade.tick(w.trade)
    assert [v for name, v in w.trace if name == "trade_done"] == [
        {"token": "server-token", "slot": 0, "new_key": w.old_key, "new_species": 0}]
    assert not w.journal.hidden(w.journal)
    assert not any(name == "saveram" for name, _ in w.trace)


def test_unchanged_native_ownership_stays_closed_when_journal_is_unreadable():
    w = ProtocolWorld()
    w.start_protocol()
    w.journal_model.data = "MODEL torn journal"
    w.witness(bits=17, result=2)
    w.ack(status=3)
    w.service()
    w.trade.tick(w.trade)
    assert w.journal.hidden(w.journal)
    assert not w.trade.capable(w.trade)
    assert w.native.trade_active(w.native), "native DONE is not reconciled while its journal is unreadable"


@pytest.mark.parametrize("failure,reason", [("staging", "native staging overwritten"),
                                           ("prepare", "native prepare lacks ready witness")])
def test_a_dead_trade_session_logs_its_reason_once(failure, reason):
    w = TradeNativeWorld()
    if failure == "staging":
        w.stage_scene()
        w.put(w.n["BLOB_BUF"], 0xFF)
    else:
        w.prepare(ready=False)
        w.ack()
    for _ in range(5):
        w.service()
    notices = [line for line in w.logs if "durable trade unavailable for this session" in line]
    assert len(notices) == 1 and reason in notices[0]


def test_owned_trade_refuses_rebinding_and_uncertain_phase_never_advertises():
    w = TradeNativeWorld()
    w.prepare()
    assert w.native.bind_trade_session(w.native, 9) is False
    before = list(w.output)
    w.phase(5)
    assert not w.native.trade_capable(w.native)
    w.service()
    assert w.output == before
