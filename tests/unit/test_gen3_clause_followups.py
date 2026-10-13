"""CLAUSE-FIX-2: captured live failures, replayed without an emulator."""
from pathlib import Path

import pytest

from server.adapters import gen3_codec as codec
from tests.unit.test_gen3_clause_rows import _ball_case, _release_case


def rewrite_party_record(image, slot, changes):
    p = codec.parse_flash(image)
    sb1 = bytearray(p["sb1"])
    offset = codec.SB1_PARTY_OFFSET + slot * 100
    mon = codec.decode_party_mon(sb1[offset:offset+100])
    mon.update(changes)
    sb1[offset:offset+100] = codec.encode_party_mon(mon)
    body, layout = bytearray(image), codec.slot_layout()
    for entry in layout:
        if entry["object"] != "sb1":
            continue
        sector = next(s for s in p["sectors"][14*p["slot"]:14*(p["slot"]+1)] if s["id"] == entry["id"])
        at = sector["index"] * codec.SECTOR_SIZE
        body[at:at+codec.SECTOR_SIZE] = codec.write_sector(sb1[entry["offset"]:entry["offset"]+entry["size"]],
                                                         entry["id"], p["counter"], layout)
    return bytes(body)


def test_release_walk_training_is_not_a_pc_record_corruption(monkeypatch, tmp_path):
    rules, run, results, _ = _release_case(monkeypatch, tmp_path)
    saved = {i: run._gen3_flushed(i) for i in ("a", "b")}
    # Captured failure shape: native Route1 training before the PC (LG learns
    # Bubble at level7). Keep its key/OT/IVs/item unchanged.
    saved["a"] = rewrite_party_record(saved["a"], 0, {
        "experience": 237, "level": 7, "max_hp": 25, "attack": 11, "defense": 14,
        "sp_attack": 12, "sp_defense": 14, "speed": 11, "moves": [33, 39, 145, 0], "pp": [35, 30, 30, 0]})
    run._gen3_flushed = saved.__getitem__
    rules.release_oracle(run, results)


def test_release_still_reports_an_unexplained_field_with_its_diff(monkeypatch, tmp_path):
    rules, run, results, _ = _release_case(monkeypatch, tmp_path)
    saved = {i: run._gen3_flushed(i) for i in ("a", "b")}
    saved["a"] = rewrite_party_record(saved["a"], 0, {"held_item": 999})
    run._gen3_flushed = saved.__getitem__
    with pytest.raises(RuntimeError, match="held_item"):
        rules.release_oracle(run, results)


def test_ball_gate_allows_native_training_and_a_consumed_battle_berry(monkeypatch, tmp_path):
    rules, run, results, _ = _ball_case(monkeypatch, tmp_path)
    saved = {i: run._gen3_flushed(i) for i in ("a", "b")}
    fixture = rewrite_party_record(run._gen3_fixture_bytes("a"), 0, {"held_item": 139})
    run._gen3_fixture_bytes = lambda _: fixture
    for side in ("a", "b"):
        saved[side] = rewrite_party_record(saved[side], 1, {"experience": 15626})
    run._gen3_flushed = saved.__getitem__
    rules.ball_gate_oracle(run, results)


def test_rr_release_drains_without_a_bound_trade_journal():
    from tests.unit.gen3_trade_journal_model import JournalModel
    from tests.unit.gen3_world import World
    from tests.unit.test_gen3_client import KB, A, party
    w = World("gen3_rr", "radical_red", "companion", journal=JournalModel(run=None))
    w.set_party(party(A, 0x22222222))
    w.step_to(60)
    w.command(cmd="config", run_id="")
    w.step()
    w.fire("pc_release_begin")
    w.set_party(party(A))
    w.regs["R13"] -= 4
    w.fire("pc_release")
    w.step(2)
    assert [m["key"] for m in w.events("release")] == [KB]
    w.connected = False
    w.step(2)
    w.connected = True
    w.step(5)
    assert [m["key"] for m in w.events("release")] == [KB, KB]


def test_rr_trade_reports_without_run_id_wait_and_block_a_later_release():
    from tests.unit.gen3_trade_journal_model import JournalModel
    from tests.unit.gen3_world import World
    from tests.unit.test_gen3_client import KB, A, B, party
    w = World("gen3_rr", "radical_red", "companion", journal=JournalModel(run=None))
    w.set_party(party(A, B))
    w.step_to(60)
    w.command(cmd="config", run_id="")
    w.step()
    # Real RR construction cannot prepare without its run binding. The actual
    # apply refusal enqueues trade_done and menu_result through owed.list.
    w.command(cmd="apply_trade", token="t", slot=1, old_key=KB, blob_hex=w.encode(party(B)[0]).hex())
    w.step(2)
    w.fire("pc_release_begin")
    w.set_party(party(A))
    w.regs["R13"] -= 4
    w.fire("pc_release")
    w.step(5)
    assert not w.events("trade_done") and not w.events("menu_result") and not w.events("release")
    # RR-DURABLE: RR reads its trainer (derived.SB2_NAME_OFFSET, old-client evidence), so the
    # run binding alone completes the journal binding and the queued order drains in order.
    assert w.d["SB2_NAME_OFFSET"] == 0
    w.command(cmd="config", run_id="managed-model-run")
    w.step(5)
    assert [m["event"] for m in w.sent if m["event"] in ("trade_done", "menu_result", "release")] == [
        "trade_done", "menu_result", "release"]


def test_duo_server_has_a_stable_unique_managed_run_identity():
    import e2e_duo as h

    from tests.unit.test_e2e_duo_lane_isolation import _args
    runs = [h.DuoRun("release_gen3", _args(game="gen3_rr", scenario="release_gen3", server_flags=[])) for _ in range(2)]
    ids = []
    for run in runs:
        command = run.server_cmd()
        assert "--run-id" in command
        ids.append(command[command.index("--run-id") + 1])
        assert ids[-1] and command == run.server_cmd()
    assert ids[0] != ids[1]


def test_native_ui_writes_do_not_fake_a_ball_gate_failure(monkeypatch, tmp_path):
    rules, run, results, _ = _ball_case(monkeypatch, tmp_path)
    results = {side: text.replace('"attempted":0', '"attempted":74') for side, text in results.items()}
    rules.ball_gate_oracle(run, results)


def test_rr_family_target_exists_in_both_own_time_tables():
    import gen3_clause_rows as rules
    import gen3_fixtures as fx
    import rr_rom_encounters as wild

    from tools.pin_gen3_site import ROM_SPECS, load_rom
    if not ROM_SPECS["rr"][3].is_file():
        pytest.skip(f"clean Radical Red dump absent: {ROM_SPECS['rr'][3]}")
    rom = load_rom("rr")
    directory = Path(fx.REPO) / "tests/fixtures/gen3"
    owned = {side: codec.rr_party_from_save((directory / name).read_bytes())[0]["species"]
             for side, name in (("a", "rr_family_synth.sav"), ("b", "rr_family_galar_synth_b.sav"))}
    assert owned == {"a": 289, "b": 1223}
    facts = rules.species_facts(rom, "radical_red")
    decoded = wild.decode_encounters(rom)
    for period, expected in (("Day", {("a", 288)}), ("Night", {("b", 1222)})):
        slots = wild.effective_maps(decoded, period)[(3, 19)]["habitats"]["land"]["slots"]
        matches = {(side, slot["species_id"]) for side, species in owned.items() for slot in slots
                   if slot["species_id"] != species and rules.same_family(facts, species, slot["species_id"])}
        assert matches == expected


def test_rr_gender_branch_has_eight_observation_attempts():
    import e2e_duo as h
    assert h.scenario_attempt_limit("gender_clause_gen3", "gen3_rr") == 8
    assert h.scenario_attempt_limit("gender_clause_gen3", "gen3_frlg") == 6   # FR/LG/Emerald observation budget (freeze #3)


def test_ball_gate_requires_a_separate_post_flip_stock_phase():
    import e2e_duo as h
    assert h.SCENARIOS["ball_gate_gen3"]["post_flip_stock"] is True


def test_post_flip_stock_edits_only_quantity_and_checksum():
    import e2e_duo as h
    import gen3_ball_phases as phases

    from tests.unit.test_e2e_duo_gen3 import PIDGEY, STARTER, _fixture
    native = _fixture([STARTER, PIDGEY], balls=1)
    stocked, manifest = phases.stock_save(native, "firered")
    allowed = {manifest["quantity_offset"] + n for n in (0, 1)} | {manifest["checksum_offset"] + n for n in (0, 1)}
    assert {i for i, (a, b) in enumerate(zip(native, stocked, strict=True)) if a != b} <= allowed
    assert manifest["label"] == "SYNTH post-flip stock"
    assert h.gen3_ball_count(native, "firered") == 1 and h.gen3_ball_count(stocked, "firered") == 20
    assert codec.parse_flash(native)["counter"] == codec.parse_flash(stocked)["counter"]
    assert h.gen3_decode(native) == h.gen3_decode(stocked)
    with pytest.raises(ValueError, match="one-ball"):
        phases.stock_save(_fixture([STARTER, PIDGEY], balls=0), "firered")


def test_phase_boundary_verifies_save_and_exit_before_edit_or_reboot(monkeypatch, tmp_path):
    from types import SimpleNamespace

    import e2e_duo as h
    import gen3_ball_phases as phases

    from tests.unit.test_e2e_duo_gen3 import PIDGEY, STARTER, _fixture, _saved
    fixture = _fixture([dict(STARTER, hp=1), PIDGEY], balls=0)
    native = _saved(fixture, 3, [dict(STARTER, hp=0), PIDGEY], balls=1)
    p = codec.parse_flash(native)
    sb1 = bytearray(p["sb1"])
    sb1[0xEE0+342//8] |= 1 << (342 % 8)
    entry = codec.slot_layout()[1]
    sec = next(s for s in p["sectors"][14:28] if s["id"] == 1)
    body = bytearray(native)
    at = sec["index"] * codec.SECTOR_SIZE
    body[at:at+codec.SECTOR_SIZE] = codec.write_sector(sb1[:entry["size"]], 1, 3, codec.slot_layout())
    native = bytes(body)
    monkeypatch.setattr(h, "BUILD", str(tmp_path))
    witness = tmp_path / "witness.bin"
    witness.write_bytes(native)
    receipt = f"SAVE_WITNESS_DUMP path={witness.as_posix()} bytes=131072 saves=1 frame=1 counter=3\nRESULT: PASS\n"
    calls = []
    run = SimpleNamespace(game="gen3_frlg", lane="model", attempt=1,
                          go_files={i: str(tmp_path / f"{i}.go") for i in ("a", "b")})
    run._gen3_fixture_bytes = lambda i: Path(run._gen3_phase_fixtures[i]).read_bytes() if hasattr(run, "_gen3_phase_fixtures") else fixture
    run._append_reconnect_marker = lambda i, tag: calls.append((i, tag))
    run.wait_results = lambda: (receipt, receipt)
    def witness_check(results):
        assert calls == [("a", "SAVE_GATE"), ("b", "SAVE_GATE")]
        assert results == {"a": receipt, "b": receipt}
        h.check_gen3_witness(native, native, fixture, saves=1)
        calls.append("witness-and-exit")
    run.check_save_witness_gen3 = witness_check
    run._gen3_flushed = lambda _: native
    run._gen3_title = lambda _: "firered"
    run._pydec_note = lambda line: calls.append("manifest")
    def launch(i, phase, seed):
        assert run._phase == {"a": "post_flip", "b": "post_flip"}
        assert "witness-and-exit" in calls and phase == "post_flip" and seed
        assert h.gen3_ball_count(run._gen3_fixture_bytes(i), "firered") == 20
        calls.append("launch-"+i)
    run.launch_instance = launch
    run._gen3_prelude = lambda: calls.append("new-hello")
    run.go = lambda: calls.append("go")
    phases.transition(run)
    assert calls[-4:] == ["launch-a", "launch-b", "new-hello", "go"]
    results = dict.fromkeys(("a", "b"), 'BALL_STOCK_READY {"balls":20,"active":true}\n')
    assert all("RESULT: PASS" in v for v in phases.verify(run, results).values())
    run._ball_phases["a"]["saved"] = fixture
    with pytest.raises(RuntimeError):
        phases.verify(run, results)
