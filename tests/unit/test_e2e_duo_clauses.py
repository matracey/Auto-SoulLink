"""The clause scenarios' Python halves, off synthetic receipts, fixture bytes and a fake server.

Both oracles read four surfaces (the two receipts, /api/status, links.json + events.json, and
each cartridge's flushed save), and the bodies they judge only run on the emulator lane. These
pins are the other half: each check removed from an oracle has to fail its own case, and the
good cases have to pass with the fixture bytes the lane produces.

The save bytes come from the real fixtures through the same helpers the admission pins build
their post-run images with (`_put_fainted_in_box12` for the rejected half's Box 12, and a
current-box writer modeled on it for the accepted half's quarantined capture).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))
sys.path.insert(0, str(REPO / "tests" / "unit"))

import e2e_duo as duo  # noqa: E402
import test_e2e_duo_admission as adm  # noqa: E402

from server.adapters import gen1_codec as codec  # noqa: E402


def _catch_in_box1(image, rom):
    """The accepted half's state: an unlinked capture quarantined in the CURRENT box.

    Modeled on test_e2e_duo_admission._put_fainted_in_box12, but into Box 1 — the current box —
    at full HP (the accepted half's catch is boxed by the capture-time box_mon, never
    force-fainted), with Box 1's individual and whole-bank checksums recomputed and the box
    copied into the sCurBoxData mirror the game writes on save.
    """
    blob = bytearray(adm._new_mon_from_starter(image, rom))
    layout = codec.BOX_LAYOUT
    start = codec.verify_boxes(image)["boxes"][1]["offset"]
    image[start] = 1
    image[start + layout["species"]] = blob[0]
    image[start + layout["species"] + 1] = codec.SPECIES_END
    image[start + layout["mons"]:start + layout["mons"] + codec.BOX_MON_SIZE] = (
        blob[:codec.BOX_MON_SIZE])
    party_start = codec.SRAM_LAYOUT["sPartyData"]
    for field in ("ot_names", "nicknames"):
        source = party_start + codec.PARTY_LAYOUT[field]
        target = start + layout[field]
        image[target:target + codec.NAME_SIZE] = image[source:source + codec.NAME_SIZE]
    image[codec.SRAM_LAYOUT["individual_checksums"][0]] = codec.sav_checksum(
        image[start:start + codec.BOX_SIZE])
    bank_start = codec.SRAM_LAYOUT["box_banks"][0]
    end = codec.SRAM_LAYOUT["all_boxes_checksums"][0]
    image[end] = codec.sav_checksum(image[bank_start:end])
    mirror = codec.SRAM_LAYOUT["sCurBoxData"]
    image[mirror:mirror + codec.BOX_SIZE] = image[start:start + codec.BOX_SIZE]
    adm._seal_main(image)
    return codec.key(codec.decode_party_mon(blob))


def _type_receipt(player, key, species, verdict):
    rows = [f"CAUGHT {key}", f"CLAUSE_CAPTURE {key} species={species} level=5"]
    if verdict == "rejected":
        rows += [
            "TYPE_CLAUSE " + json.dumps({
                "player": player, "verdict": "rejected", "key": key, "species": species,
                "prompt": "[x] Type clause: shared Normal", "memorialize": True,
                "sound26": True, "unresolve_area": "route_1"}),
            f"MEMORIAL {key} box12=true hp=0 memorialize_done=1 memorialize_failed=0",
            "SEEN capture=1 box_mon=1 party_mon=0 force_faint=1 memorialize=1",
        ]
    else:
        rows += [
            "TYPE_CLAUSE " + json.dumps({
                "player": player, "verdict": "accepted", "key": key, "species": species,
                "force_faint": False, "type_prompt": "", "unresolve_area": ""}),
            "SEEN capture=1 box_mon=1 party_mon=0 force_faint=0 memorialize=0",
        ]
    rows.append("SAVE_WITNESS type_clause_new frames=900")
    return "\n".join(rows)


def _type_stub(tmp_path, monkeypatch, rejected_side="a"):
    """A DuoRun whose two clauses disagree: `rejected_side` is force-fainted, the other keeps
    its catch quarantined in Box 1."""
    accepted = "b" if rejected_side == "a" else "a"
    a_sram, a_rom = adm._fixture_save("red")
    b_sram, b_rom = adm._fixture_save("blue")
    images = {"a": bytearray(a_sram), "b": bytearray(b_sram)}
    keys = {"a": adm._put_fainted_in_box12(images["a"], a_rom) if rejected_side == "a"
            else _catch_in_box1(images["a"], a_rom),
            "b": adm._put_fainted_in_box12(images["b"], b_rom) if rejected_side == "b"
            else _catch_in_box1(images["b"], b_rom)}
    # Both halves caught: the starter is slot 0 and the boxed catch is the new key.
    start = codec.SRAM_LAYOUT["sPartyData"]
    parties = {inst: codec.decode_party(bytes(images[inst])[start:start + codec.PARTY_LAYOUT["size"]])
               for inst in ("a", "b")}

    run = duo.DuoRun.__new__(duo.DuoRun)
    run.scenario = "type_clause_new"
    run.emus = []
    run.cfg = dict(duo.SCENARIOS["type_clause_new"])
    run.gcfg = dict(duo.GAMES["gen1_new"])
    run.data_dir = str(tmp_path)
    run._boot_keys = {inst: codec.key(parties[inst][0]) for inst in ("a", "b")}
    run._pydec_note = lambda fact: None
    run._status = lambda: {
        "links": [],
        "pending_captures": {"route_1": {accepted: {"key": keys[accepted], "species": 16}}},
        "area_states": {"route_1": f"pending_{rejected_side}"},
    }
    (tmp_path / "links.json").write_text(json.dumps({
        "links": [],
        "retry_areas": {"a": ["route_1"] if rejected_side == "a" else [],
                        "b": ["route_1"] if rejected_side == "b" else []}}), encoding="utf-8")

    mirror = codec.SRAM_LAYOUT["sCurBoxData"]

    def saved(inst, **_kwargs):
        image = bytes(images[inst])
        return (image, parties[inst],
                codec.decode_box(image[mirror:mirror + codec.BOX_SIZE]), codec)

    monkeypatch.setattr(run, "_saved_gen1_party", saved)
    results = {"a": _type_receipt("a", keys["a"], 16, "rejected" if rejected_side == "a" else "accepted"),
               "b": _type_receipt("b", keys["b"], 19, "rejected" if rejected_side == "b" else "accepted")}
    for inst, text in results.items():
        (tmp_path / f"e2e_type_clause_new_{inst}_result.txt").write_text(text, encoding="utf-8")
    monkeypatch.setattr(run, "_result_path",
                        lambda inst: str(tmp_path / f"e2e_type_clause_new_{inst}_result.txt"))
    # `_artifact` times the receipts against `_started`; a filesystem clock that rounds the other
    # way would make this run's own write look stale, so the floor is disabled (the stale path
    # has its own pin in test_e2e_duo_admission.py).
    run._started = 0.0
    return run, results, keys, rejected_side, accepted


@pytest.mark.parametrize("rejected_side", ["a", "b"])
def test_type_clause_oracle_reads_both_directions(tmp_path, monkeypatch, rejected_side):
    """Which half the RNG rejects is not knowable ahead of the lane, so both are pinned."""
    run, results, _keys, _rejected, _accepted = _type_stub(tmp_path, monkeypatch, rejected_side)
    run.assert_type_clause_new_saved(results)


@pytest.mark.parametrize(("old", "new", "message"), [
    ("[x] Type clause: shared Normal", "[x] Type clause: shared Fire",
     "did not name Normal"),
    ('"memorialize": true', '"memorialize": false', "lacks memorialize/sound26"),
    ('"sound26": true', '"sound26": false', "lacks memorialize/sound26"),
    ('"unresolve_area": "route_1"', '"unresolve_area": ""', "not 'route_1'"),
    ('"unresolve_area": "route_1"', '"unresolve_area": "?"', "not 'route_1'"),
    ("box12=true hp=0", "box12=false hp=40", "rejected Box 12 receipt"),
    ("force_faint=1 memorialize=1", "force_faint=0 memorialize=0", "had to be force-fainted"),
])
def test_type_clause_oracle_refuses_a_broken_rejected_receipt(
        tmp_path, monkeypatch, old, new, message):
    run, results, keys, rejected, _accepted = _type_stub(tmp_path, monkeypatch)
    results[rejected] = results[rejected].replace(old, new, 1)
    with pytest.raises(RuntimeError, match=message):
        run.assert_type_clause_new_saved(results)


def test_type_clause_oracle_refuses_a_prompt_on_the_accepted_half(tmp_path, monkeypatch):
    run, results, keys, _rejected, accepted = _type_stub(tmp_path, monkeypatch, "b")
    results[accepted] = results[accepted].replace(
        '"type_prompt": ""', '"type_prompt": "[x] Type clause: shared Normal"')
    with pytest.raises(RuntimeError, match="got the rejection prompt too"):
        run.assert_type_clause_new_saved(results)


def test_type_clause_oracle_refuses_unresolve_on_the_accepted_half(tmp_path, monkeypatch):
    """The partner gets SE_BOO and nothing else (server/state.py:1593)."""
    run, results, keys, _rejected, accepted = _type_stub(tmp_path, monkeypatch, "b")
    results[accepted] = results[accepted].replace(
        '"unresolve_area": ""', '"unresolve_area": "route_1"')
    with pytest.raises(RuntimeError, match="was sent unresolve_area"):
        run.assert_type_clause_new_saved(results)


def test_type_clause_oracle_refuses_two_verdicts_of_a_kind(tmp_path, monkeypatch):
    run, results, keys, _rejected, accepted = _type_stub(tmp_path, monkeypatch)
    results[accepted] = results[accepted].replace('"verdict": "accepted"',
                                                  '"verdict": "rejected"')
    with pytest.raises(RuntimeError, match="expected exactly one rejected and one accepted"):
        run.assert_type_clause_new_saved(results)


def test_type_clause_oracle_refuses_a_link_that_still_formed(tmp_path, monkeypatch):
    run, results, keys, rejected, _accepted = _type_stub(tmp_path, monkeypatch)
    run._status = lambda: {
        "links": [{"area_id": "route_1", "a_key": keys["a"], "b_key": keys["b"],
                   "status": "alive"}],
        "pending_captures": {},
        "area_states": {"route_1": "linked"},
    }
    with pytest.raises(RuntimeError, match="still formed a link"):
        run.assert_type_clause_new_saved(results)


def test_type_clause_oracle_refuses_a_pending_that_kept_the_rejected_half(tmp_path, monkeypatch):
    run, results, keys, rejected, _accepted = _type_stub(tmp_path, monkeypatch)
    status = run._status()
    status["pending_captures"]["route_1"][rejected] = {"key": keys[rejected], "species": 19}
    run._status = lambda: status
    with pytest.raises(RuntimeError, match="expected only the accepted half"):
        run.assert_type_clause_new_saved(results)


def test_type_clause_oracle_refuses_the_wrong_area_state(tmp_path, monkeypatch):
    run, results, keys, rejected, _accepted = _type_stub(tmp_path, monkeypatch)
    status = run._status()
    status["area_states"]["route_1"] = "pending_b"
    run._status = lambda: status
    with pytest.raises(RuntimeError, match="area_states.route_1 is 'pending_b'"):
        run.assert_type_clause_new_saved(results)


def test_type_clause_oracle_refuses_a_rejection_that_was_not_retryable(tmp_path, monkeypatch):
    """retry_areas is the durable half of the clause_violation_retry area state
    (server/state.py:1618, :3080)."""
    run, results, keys, rejected, _accepted = _type_stub(tmp_path, monkeypatch)
    (tmp_path / "links.json").write_text(json.dumps({
        "links": [], "retry_areas": {"a": [], "b": []}}), encoding="utf-8")
    with pytest.raises(RuntimeError, match="leave route_1 retryable"):
        run.assert_type_clause_new_saved(results)


def test_type_clause_oracle_refuses_the_accepted_catch_left_in_the_party(tmp_path, monkeypatch):
    run, results, keys, _rejected, accepted = _type_stub(tmp_path, monkeypatch, "b")
    real = run._saved_gen1_party

    def catch_in_party(inst, **kwargs):
        sram, party, box, codec_module = real(inst, **kwargs)
        if inst != accepted:
            return sram, party, box, codec_module
        return sram, party + [adm._fake_mon(keys[inst])], box, codec_module

    monkeypatch.setattr(run, "_saved_gen1_party", catch_in_party)
    with pytest.raises(RuntimeError, match="still holds"):
        run.assert_type_clause_new_saved(results)


def test_type_clause_oracle_refuses_a_rejected_mon_that_never_reached_box12(tmp_path, monkeypatch):
    run, results, keys, rejected, _accepted = _type_stub(tmp_path, monkeypatch)
    real = run._saved_gen1_party

    def empty_box12(inst, **kwargs):
        sram, party, box, codec_module = real(inst, **kwargs)
        if inst != rejected:
            return sram, party, box, codec_module
        image = bytearray(sram)
        info = codec_module.verify_boxes(image)["boxes"][12]
        start = info["offset"]
        image[start:start + codec_module.BOX_SIZE] = bytes(codec_module.BOX_SIZE)
        image[start + codec_module.BOX_LAYOUT["species"]] = codec_module.SPECIES_END
        # Box 12 is bank 1's slot 5; re-seal it so the failure is the CONTENT check, not the
        # checksum one.
        image[codec_module.SRAM_LAYOUT["individual_checksums"][1] + 5] = codec_module.sav_checksum(
            image[start:start + codec_module.BOX_SIZE])
        bank = codec_module.SRAM_LAYOUT["box_banks"][1]
        end = codec_module.SRAM_LAYOUT["all_boxes_checksums"][1]
        image[end] = codec_module.sav_checksum(image[bank:end])
        return bytes(image), party, box, codec_module

    monkeypatch.setattr(run, "_saved_gen1_party", empty_box12)
    with pytest.raises(RuntimeError, match="Box 12 holds"):
        run.assert_type_clause_new_saved(results)


def _species_receipts(a_key, b_key, species_a, species_b, path):
    a_text = "\n".join([
        f"CAUGHT {a_key}",
        f"PENDING_CAPTURE {a_key} species={species_a} level=4",
        "PARTY party=1 box=1",
        "SPECIES_CLAUSE " + json.dumps({
            "player": "a", "key": a_key, "species": species_a, "capture": 1, "box_mon": 1,
            "party_mon": 0, "force_faint": 0, "sync_retrieve_done": 1}),
        "SAVE_WITNESS species_clause_new_a frames=900",
    ])
    b_rows = [f"A_PENDING species={species_a}"]
    if path == "reroll_observed":
        b_rows += [f"ENCOUNTER 1 species={species_a} dupe_of_a=true",
                   f"REROLL_SEEN species={species_a} "
                   f"prompt=Dupes clause: Rattata -- reroll!",
                   "REROLL_RAN no_catch=1 unresolve_area=route_1",
                   f"ENCOUNTER 2 species={species_b} dupe_of_a=false"]
    else:
        b_rows += [f"ENCOUNTER 1 species={species_b} dupe_of_a=false"]
    b_rows += [
        f"CAUGHT {b_key}",
        f"PATH {path}",
        "SPECIES_CLAUSE " + json.dumps({
            "player": "b", "key": b_key, "dupe_species": species_a,
            "rerolls": 1 if path == "reroll_observed" else 0, "path": path, "no_catch": 0,
            "capture": 1, "force_faint": 0, "party_mon": 1, "sync_retrieve_done": 1}),
        "SAVE_WITNESS species_clause_new_b frames=910",
    ]
    return a_text, "\n".join(b_rows)


def _species_stub(tmp_path, monkeypatch, path="reroll_observed"):
    a_sram, a_rom = adm._fixture_save("red")
    b_sram, b_rom = adm._fixture_save("blue")
    a_image, b_image = bytearray(a_sram), bytearray(b_sram)
    key_a = adm._add_caught_to_party(a_image, a_rom)
    key_b = adm._add_caught_to_party(b_image, b_rom)
    start = codec.SRAM_LAYOUT["sPartyData"]
    parties = {inst: codec.decode_party(bytes(image)[start:start + codec.PARTY_LAYOUT["size"]])
               for inst, image in (("a", a_image), ("b", b_image))}

    run = duo.DuoRun.__new__(duo.DuoRun)
    run.scenario = "species_clause_new"
    run.emus = []
    run.cfg = dict(duo.SCENARIOS["species_clause_new"])
    run.gcfg = dict(duo.GAMES["gen1_new"])
    run.data_dir = str(tmp_path)
    run._boot_keys = {inst: codec.key(parties[inst][0]) for inst in ("a", "b")}
    run._pydec_note = lambda fact: None
    run._species_release = {"key": key_a, "species": 16}
    run._status = lambda: {"links": [{"area_id": "route_1", "a_key": key_a, "b_key": key_b,
                                      "status": "alive"}]}
    (tmp_path / "links.json").write_text(json.dumps({
        "links": [{"area_id": "route_1", "status": "alive", "a": {"key": key_a},
                   "b": {"key": key_b}}]}), encoding="utf-8")
    rows = [{"ts": "1", "player": "b", "type": "reroll", "text": "🔁 Dupes clause: Rattata "
                                                                 "-- reroll!"}] \
        if path == "reroll_observed" else []
    (tmp_path / "events.json").write_text(json.dumps(rows), encoding="utf-8")

    def saved(inst, **_kwargs):
        return bytes(a_image if inst == "a" else b_image), parties[inst], [], codec

    monkeypatch.setattr(run, "_saved_gen1_party", saved)
    results = dict(zip(("a", "b"),
                       _species_receipts(key_a, key_b, 16, 19, path), strict=True))
    return run, results, key_a, key_b


@pytest.mark.parametrize("path", ["reroll_observed", "reroll_unobserved"])
def test_species_clause_oracle_reads_either_branch(tmp_path, monkeypatch, path):
    run, results, _ka, _kb = _species_stub(tmp_path, monkeypatch, path)
    run.assert_species_clause_new_saved(results)


def test_species_clause_oracle_refuses_an_observed_path_without_a_reroll_seen(
        tmp_path, monkeypatch):
    run, results, _ka, _kb = _species_stub(tmp_path, monkeypatch)
    results["b"] = results["b"].replace(
        "REROLL_SEEN species=16 prompt=Dupes clause: Rattata -- reroll!\n", "")
    with pytest.raises(RuntimeError, match="no REROLL_SEEN line exists"):
        run.assert_species_clause_new_saved(results)


def test_species_clause_oracle_refuses_a_prompt_with_an_emoji(tmp_path, monkeypatch):
    """The in-game prompt is the server's emoji-free literal (state.py:1726-1738); the 🔁 lives
    in the events.json row only."""
    run, results, _ka, _kb = _species_stub(tmp_path, monkeypatch)
    results["b"] = results["b"].replace("prompt=Dupes clause: Rattata -- reroll!",
                                        "prompt=🔁 Dupes clause: Rattata -- reroll!")
    with pytest.raises(RuntimeError, match="emoji-free"):
        run.assert_species_clause_new_saved(results)


def test_species_clause_oracle_refuses_a_reroll_on_the_wrong_species(tmp_path, monkeypatch):
    run, results, _ka, _kb = _species_stub(tmp_path, monkeypatch)
    results["b"] = results["b"].replace("REROLL_SEEN species=16", "REROLL_SEEN species=19")
    with pytest.raises(RuntimeError, match="the reroll ran on species 19"):
        run.assert_species_clause_new_saved(results)


def test_species_clause_oracle_refuses_a_dead_zone_row(tmp_path, monkeypatch):
    run, results, _ka, _kb = _species_stub(tmp_path, monkeypatch)
    rows = json.loads((tmp_path / "events.json").read_text(encoding="utf-8"))
    rows.append({"ts": "2", "player": "b", "type": "dead_zone", "text": "route_1"})
    (tmp_path / "events.json").write_text(json.dumps(rows), encoding="utf-8")
    with pytest.raises(RuntimeError, match="dead_zone row"):
        run.assert_species_clause_new_saved(results)


def test_species_clause_oracle_refuses_a_disagreement_on_the_dupe_species(tmp_path, monkeypatch):
    run, results, _ka, _kb = _species_stub(tmp_path, monkeypatch)
    results["b"] = results["b"].replace('"dupe_species": 16', '"dupe_species": 19')
    with pytest.raises(RuntimeError, match="dupe_species=19"):
        run.assert_species_clause_new_saved(results)


def test_species_clause_oracle_refuses_a_dead_link(tmp_path, monkeypatch):
    run, results, _ka, _kb = _species_stub(tmp_path, monkeypatch)
    document = json.loads((tmp_path / "links.json").read_text(encoding="utf-8"))
    document["links"][0]["status"] = "dead"
    (tmp_path / "links.json").write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(RuntimeError, match="durable route_1 pair"):
        run.assert_species_clause_new_saved(results)


def test_species_clause_oracle_refuses_a_second_capture_for_a(tmp_path, monkeypatch):
    run, results, _ka, _kb = _species_stub(tmp_path, monkeypatch)
    results["a"] = results["a"].replace('"capture": 1', '"capture": 2')
    with pytest.raises(RuntimeError, match="capture=2"):
        run.assert_species_clause_new_saved(results)


def test_species_clause_oracle_refuses_a_reroll_row_without_the_path(tmp_path, monkeypatch):
    run, results, _ka, _kb = _species_stub(tmp_path, monkeypatch, "reroll_unobserved")
    results["b"] = results["b"].replace("PATH reroll_unobserved", "PATH reroll_observed")
    with pytest.raises(RuntimeError, match="no REROLL_SEEN line exists"):
        run.assert_species_clause_new_saved(results)


def test_species_clause_oracle_refuses_the_runner_never_having_released_b(tmp_path, monkeypatch):
    run, results, _ka, _kb = _species_stub(tmp_path, monkeypatch)
    del run._species_release
    with pytest.raises(RuntimeError, match="live release leg never ran"):
        run.assert_species_clause_new_saved(results)


def test_species_clause_oracle_refuses_a_mark_the_runner_did_not_write(tmp_path, monkeypatch):
    run, results, _ka, _kb = _species_stub(tmp_path, monkeypatch)
    results["b"] = results["b"].replace("A_PENDING species=16", "A_PENDING species=19")
    with pytest.raises(RuntimeError, match="B's echo of the runner's mark"):
        run.assert_species_clause_new_saved(results)


# ── the release gate ────────────────────────────────────────────────────────

def _release_stub(tmp_path, monkeypatch, species=16, server_species=16):
    a_sram, a_rom = adm._fixture_save("red")
    b_sram, b_rom = adm._fixture_save("blue")
    images = {"a": bytearray(a_sram), "b": bytearray(b_sram)}
    key_a = adm._add_caught_to_party(images["a"], a_rom)
    adm._add_caught_to_party(images["b"], b_rom)
    run = duo.DuoRun.__new__(duo.DuoRun)
    run.scenario = "species_clause_new"
    run.emus = []
    run.cfg = dict(duo.SCENARIOS["species_clause_new"])
    run.gcfg = dict(duo.GAMES["gen1_new"])
    run.data_dir = str(tmp_path)
    run.go_files = {inst: str(tmp_path / f"duo_go_species_clause_new_{inst}.txt")
                    for inst in ("a", "b")}
    run._pydec_note = lambda fact: None
    run._status = lambda: {"pending_captures": {"route_1": {
        "a": {"key": key_a, "species": server_species}}}}
    receipt = "\n".join([
        f"CAUGHT {key_a}",
        f"PENDING_CAPTURE {key_a} species={species} level=4",
        "SAVE_WITNESS species_clause_new_a frames=900",
    ])
    monkeypatch.setattr(duo, "read_result", lambda scenario, inst: receipt if inst == "a" else "")
    Path(run.go_files["a"]).write_text("GO\n", encoding="utf-8")
    Path(run.go_files["b"]).write_text("GO\n", encoding="utf-8")
    return run, key_a


def test_release_gate_writes_a_pending_into_bs_go_file(tmp_path, monkeypatch):
    run, key_a = _release_stub(tmp_path, monkeypatch)
    run.assert_species_clause_release()
    assert run._species_release == {"key": key_a, "species": 16}
    text = Path(run.go_files["b"]).read_text(encoding="utf-8")
    assert "A_PENDING species=16" in text and "GO" in text, text


def test_release_gate_refuses_a_server_that_has_no_pending(tmp_path, monkeypatch):
    run, _key_a = _release_stub(tmp_path, monkeypatch)
    run._status = lambda: {"pending_captures": {}}
    # The gate waits through DuoRun.wait_for now, so the budget is the scenario's own.
    run.cfg["timeout"] = 0.2
    with pytest.raises(TimeoutError, match="SERVER to hold A's pending capture"):
        run.assert_species_clause_release()


def test_release_gate_refuses_a_server_numbering_mismatch(tmp_path, monkeypatch):
    run, _key_a = _release_stub(tmp_path, monkeypatch, species=16, server_species=19)
    with pytest.raises(RuntimeError, match="the server holds species 19"):
        run.assert_species_clause_release()


def test_release_gate_waits_for_as_marker(tmp_path, monkeypatch):
    run, _key_a = _release_stub(tmp_path, monkeypatch)
    monkeypatch.setattr(duo, "read_result", lambda scenario, inst: "CAUGHT AAAA:1111:01\n")
    run.cfg["timeout"] = 0.2  # the gate waits through DuoRun.wait_for now
    with pytest.raises(TimeoutError, match="PENDING_CAPTURE marker"):
        run.assert_species_clause_release()


# ── the retry-on-PASS driver ────────────────────────────────────────────────

def _retry_receipts(path):
    """One attempt's receipts: A passes, B passes with the given PATH and, when observed, the
    REROLL_SEEN line the oracle wants."""
    reroll = ("REROLL_SEEN species=16 prompt=Dupes clause: Rattata -- reroll!\n"
              if path == "reroll_observed" else "")
    return {"a": "RESULT: PASS (A pending AAAA:1111:01)",
            "b": f"RESULT: PASS ({path}: B linked)\nPATH {path}\n{reroll}"}


def _retry_driver(monkeypatch, tmp_path, outcomes, name="species_clause_new"):
    """A fake DuoRun whose per-attempt receipts are `outcomes` (a list of B-receipt paths, or
    ("FAIL", reason) tuples), returning the attempts it was asked to run."""
    monkeypatch.setattr(duo, "BUILD", str(tmp_path))
    built, active = [], {"attempt": 0}

    class FakeRun:
        def __init__(self, scenario, args, attempt):
            assert scenario == name
            built.append(attempt)
            active["attempt"] = attempt

        def run(self):
            outcome = outcomes[active["attempt"] - 1]
            return not (isinstance(outcome, tuple) and outcome[0] == "FAIL")

    def fake_result(_scenario, inst):
        outcome = outcomes[active["attempt"] - 1]
        if isinstance(outcome, tuple) and outcome[0] == "FAIL":
            text = f"RESULT: FAIL ({outcome[1]})"
            return text
        return _retry_receipts(outcome)[inst]

    monkeypatch.setattr(duo, "DuoRun", FakeRun)
    monkeypatch.setattr(duo, "read_result", fake_result)
    (tmp_path / "e2e_species_clause_new_pydec_result.txt").write_text(
        f"attempt 1 of {duo.scenario_attempt_limit(name, 'gen1_new')}\n", encoding="utf-8")
    args = type("Args", (), {"game": "gen1_new", "idle_jitter": 0})()
    return built, args


def test_species_retry_returns_immediately_when_the_reroll_was_observed(capsys, tmp_path,
                                                                       monkeypatch):
    built, args = _retry_driver(monkeypatch, tmp_path, ["reroll_observed"])
    assert duo.run_scenario_with_rng_retry("species_clause_new", args) == (True, 1)
    assert built == [1]
    out = capsys.readouterr().out
    assert "[duo] species_clause_new: reroll branch observed on attempt 1" in out
    assert "re-running the whole scenario" not in out
    assert "NOT observed" not in out


def test_species_retry_reruns_a_pass_until_the_branch_shows(capsys, tmp_path, monkeypatch):
    built, args = _retry_driver(monkeypatch, tmp_path,
                                ["reroll_unobserved", "reroll_observed"])
    assert duo.run_scenario_with_rng_retry("species_clause_new", args) == (True, 2)
    assert built == [1, 2]
    out = capsys.readouterr().out
    assert "reroll branch not observed on attempt 1; re-running the whole scenario" in out
    assert "reroll branch observed on attempt 2" in out
    assert "NOT observed" not in out


def test_species_retry_gives_up_after_the_whole_budget_and_records_it(capsys, tmp_path,
                                                                     monkeypatch):
    built, args = _retry_driver(monkeypatch, tmp_path, ["reroll_unobserved"] * 8)
    assert duo.run_scenario_with_rng_retry("species_clause_new", args) == (True, 8)
    assert built == list(range(1, 9))
    out = capsys.readouterr().out
    assert ("[duo] species_clause_new: reroll branch NOT observed after 8 attempts "
            "(D-4 stays partial)") in out
    pydec = (tmp_path / "e2e_species_clause_new_pydec_result.txt").read_text(encoding="utf-8")
    assert "reroll branch NOT observed after 8 attempts (D-4 stays partial)" in pydec


def test_the_reroll_retry_is_species_clause_only(capsys, tmp_path, monkeypatch):
    """A link_new PASS carrying the same text must not be re-run: the branch is D-4's alone."""
    built, args = _retry_driver(monkeypatch, tmp_path, ["reroll_unobserved"], name="link_new")
    assert duo.run_scenario_with_rng_retry("link_new", args) == (True, 1)
    assert built == [1]
    assert "reroll branch" not in capsys.readouterr().out


def test_a_real_failure_is_never_re_run_for_the_reroll(capsys, tmp_path, monkeypatch):
    built, args = _retry_driver(monkeypatch, tmp_path, [("FAIL", "force_faint never arrived")])
    assert duo.run_scenario_with_rng_retry("species_clause_new", args) == (False, 1)
    assert built == [1]
    assert "re-running the whole scenario" not in capsys.readouterr().out


def test_species_retry_keeps_the_ball_rng_retry_on_top(capsys, tmp_path, monkeypatch):
    """The bodies return `hunt ended out-of-balls` unchanged, so attempt 1's ball miss still
    restarts the run — and the restarted attempt can then observe the branch."""
    built, args = _retry_driver(monkeypatch, tmp_path,
                                [("FAIL", "hunt ended out-of-balls"), "reroll_observed"])
    assert duo.run_scenario_with_rng_retry("species_clause_new", args) == (True, 2)
    assert built == [1, 2]
    out = capsys.readouterr().out
    assert "reroll branch observed on attempt 2" in out


def test_scenario_attempt_limit_is_the_single_source(capsys, tmp_path, monkeypatch):
    assert duo.scenario_attempt_limit("species_clause_new", "gen1_new") == 8
    assert duo.scenario_attempt_limit("link_new", "gen1_new") == 3
    assert duo.scenario_attempt_limit("poison_new", "gen1_new") == 4
    assert duo.scenario_attempt_limit("explode_new", "gen1_new") == 4
    assert duo.scenario_attempt_limit("ball_gate_new", "gen1_new") == 1
    assert duo.scenario_attempt_limit("faint", "gen3_rr") == 1


def test_type_clause_oracle_refuses_a_missing_quarantined_catch(tmp_path, monkeypatch):
    """The accepted half's catch has to be IN its current box: the capture-time box_mon is what
    keeps an unlinked capture off the party (server/state.py:1554-1557)."""
    run, results, keys, _rejected, accepted = _type_stub(tmp_path, monkeypatch, "b")
    real = run._saved_gen1_party

    def empty_box(inst, **kwargs):
        sram, party, box, codec_module = real(inst, **kwargs)
        if inst != accepted:
            return sram, party, box, codec_module
        return sram, party, [], codec_module

    monkeypatch.setattr(run, "_saved_gen1_party", empty_box)
    with pytest.raises(RuntimeError, match="expected the quarantined"):
        run.assert_type_clause_new_saved(results)


# ── r2 finding 1: the release gate and A's ball miss ────────────────────────

def test_release_gate_raises_gamergmiss_on_as_ball_miss(tmp_path, monkeypatch):
    """A's hunt can end out-of-balls before PENDING_CAPTURE exists; a TimeoutError there would
    escape DuoRun.run() (it is not GameRngMiss) and the bounded retry would never classify the
    receipts. The gate raises the harness's own RNG channel instead."""
    run, _key_a = _release_stub(tmp_path, monkeypatch)
    monkeypatch.setattr(duo, "read_result",
                        lambda scenario, inst: (duo.RNG_OUT_OF_BALLS if inst == "a" else ""))
    with pytest.raises(duo.GameRngMiss):
        run.assert_species_clause_release()


def test_the_unreleased_pair_is_retryable_only_with_as_ball_miss():
    """The real classifier over the real pair of phrases: B's unreleased message is
    CONSEQUENCE, which earns a retry only alongside A's CAUSE_RNG."""
    consequence = "RESULT: FAIL (runner never released B (A_PENDING))"
    assert duo.retryable_gen1_rng("gen1_new", {"a": duo.RNG_OUT_OF_BALLS, "b": consequence}, 1)
    assert not duo.retryable_gen1_rng("gen1_new", {"a": "RESULT: PASS (caught)", "b": consequence}, 1)
    assert duo.classify_gen1_result(consequence) == "CONSEQUENCE"


def test_the_release_gate_still_times_out_when_a_never_finishes(tmp_path, monkeypatch):
    """No marker and no terminal RESULT: the old TimeoutError stands (a hung hunt is not RNG)."""
    run, _key_a = _release_stub(tmp_path, monkeypatch)
    monkeypatch.setattr(duo, "read_result", lambda scenario, inst: "")
    run.cfg["timeout"] = 0.2
    with pytest.raises(TimeoutError, match="PENDING_CAPTURE marker"):
        run.assert_species_clause_release()


def test_the_give_up_annotation_is_archived_with_its_attempt(capsys, tmp_path, monkeypatch):
    """r2 finding 5: the archive used to be copied BEFORE the line was appended, so the
    third-attempt PYDEC copy was missing the partial-coverage note."""
    built, args = _retry_driver(monkeypatch, tmp_path, ["reroll_unobserved"] * 8)
    assert duo.run_scenario_with_rng_retry("species_clause_new", args) == (True, 8)
    archived = next(tmp_path.rglob("*pydec_attempt8_result.txt")).read_text(encoding="utf-8")
    assert "reroll branch NOT observed after 8 attempts (D-4 stays partial)" in archived
    assert built == list(range(1, 9))


# ── H-1 (i)/(j): the ordered reroll sequence and the species budget phrase ──

def test_species_clause_oracle_refuses_a_dupe_encounter_after_the_catch(tmp_path, monkeypatch):
    """The observed branch is an ORDER: every dupe escape comes before the non-dupe catch."""
    run, results, _ka, _kb = _species_stub(tmp_path, monkeypatch)
    results["b"] = results["b"].replace(
        "ENCOUNTER 2 species=19 dupe_of_a=false",
        "ENCOUNTER 2 species=19 dupe_of_a=false\nENCOUNTER 3 species=16 dupe_of_a=true")
    with pytest.raises(RuntimeError, match="not the dupe escape"):
        run.assert_species_clause_new_saved(results)


def test_species_clause_oracle_refuses_a_reroll_count_that_disagrees(tmp_path, monkeypatch):
    run, results, _ka, _kb = _species_stub(tmp_path, monkeypatch)
    results["b"] = results["b"].replace('"rerolls": 1', '"rerolls": 2')
    with pytest.raises(RuntimeError, match="says 2 reroll"):
        run.assert_species_clause_new_saved(results)


def test_species_clause_oracle_refuses_a_reroll_newer_than_the_link_row(tmp_path, monkeypatch):
    """events.json is newest-first: a reroll row above the link row would put the prompt after
    the catch that ended the rerolling."""
    run, results, _ka, _kb = _species_stub(tmp_path, monkeypatch)
    rows = json.loads((tmp_path / "events.json").read_text(encoding="utf-8"))
    rows.append({"ts": "0", "player": "b", "type": "linked", "text": "✓ Linked"})  # older
    rows.insert(0, {"ts": "4", "player": "b", "type": "reroll", "text": "🔁 again"})  # newest
    (tmp_path / "events.json").write_text(json.dumps(rows), encoding="utf-8")
    with pytest.raises(RuntimeError, match="NEWER than the link row"):
        run.assert_species_clause_new_saved(results)


def test_the_species_budget_phrase_is_retryable_on_later_attempts():
    """The reroll observation and the hunt's RNG budget are the same attempts, so this phrase
    keeps its retry inside the limit — unlike the ball miss, which is attempt-1 only."""
    miss = f"RESULT: FAIL ({duo.SPECIES_BUDGET_MISS})"
    assert duo.classify_gen1_result(miss) == "CAUSE_RNG"
    assert duo.retryable_gen1_rng("gen1_new", {"a": "RESULT: PASS (x)", "b": miss}, 3, limit=8)
    assert duo.retryable_gen1_rng("gen1_new", {"a": "RESULT: PASS (x)", "b": miss}, 7, limit=8)
    assert not duo.retryable_gen1_rng("gen1_new", {"a": "RESULT: PASS (x)", "b": miss}, 8,
                                      limit=8)
    # a second ball miss retries too (owner 2026-09-18); a third does not
    assert duo.retryable_gen1_rng("gen1_new", {"a": "RESULT: PASS (x)", "b": duo.RNG_OUT_OF_BALLS},
                                  2, limit=8)
    assert not duo.retryable_gen1_rng("gen1_new", {"a": "RESULT: PASS (x)", "b": duo.RNG_OUT_OF_BALLS},
                                      3, limit=8)


# ── H-2 (l)/(m): the FAIL summary and the explode KO phrase ─────────────────

def test_an_oracle_failure_becomes_a_fail_summary_not_a_traceback(capsys, tmp_path, monkeypatch):
    """The lane's poison run: the oracle raised, the exception escaped run_scenario_with_rng_retry
    and no summary block was printed. The attempt is caught, archived and returned with its
    reason so main() can print FAIL and exit non-zero."""
    monkeypatch.setattr(duo, "BUILD", str(tmp_path))
    built = []

    class BoomRun:
        def __init__(self, name, args, attempt):
            built.append(attempt)

        def run(self):
            raise RuntimeError("links.json carries 2 link(s) ['route_1', 'viridian_forest']")

    monkeypatch.setattr(duo, "DuoRun", BoomRun)
    monkeypatch.setattr(duo, "read_result", lambda _name, _inst: "RESULT: PASS (x)")
    args = type("Args", (), {"game": "gen1_new", "idle_jitter": 0})()
    outcome = duo.run_scenario_with_rng_retry("poison_new", args)
    assert outcome == (False, 1, "RuntimeError: links.json carries 2 link(s) "
                                 "['route_1', 'viridian_forest']")
    assert built == [1], "an oracle failure is never retried"
    lines = duo.summary_lines({"poison_new": outcome}, "gen1_new")
    assert lines == ["  poison_new: FAIL (attempt 1 of 4) — RuntimeError: links.json carries "
                     "2 link(s) ['route_1', 'viridian_forest']"], lines
    assert duo.exit_code({"poison_new": outcome}) == 1
    assert duo.exit_code({"poison_new": (True, 1)}) == 0


def test_the_explode_ko_phrase_is_retryable_within_its_own_budget():
    """B's explode half can lose the linked mon to the foe before the coerced turn; A reports
    the 180 s READY_ACTIVE wait as a consequence, so the pair retries inside explode_new's declared
    attempts."""
    miss = f"RESULT: FAIL ({duo.EXPLODE_KO_MISS})"
    assert duo.classify_gen1_result(miss) == "CAUSE_RNG"
    assert duo.classify_gen1_result(
        "RESULT: FAIL (B did not park in the required faint window)") == "CONSEQUENCE"
    assert duo.retryable_gen1_rng(
        "gen1_new",
        {"a": "RESULT: FAIL (B did not park in the required faint window)", "b": miss},
        1, limit=duo.scenario_attempt_limit("explode_new", "gen1_new"))
    assert not duo.retryable_gen1_rng(
        "gen1_new", {"a": "RESULT: PASS (x)", "b": miss}, 2, limit=2)
    assert duo.retryable_gen1_rng("gen1_new", {"a": "RESULT: PASS (x)", "b": miss}, 1, limit=2)
    # (s): explode_new's own budget is 4, so the phrase stays retryable on attempts 2 and 3.
    limit = duo.scenario_attempt_limit("explode_new", "gen1_new")
    assert duo.retryable_gen1_rng("gen1_new", {"a": "RESULT: PASS (x)", "b": miss}, 2, limit=limit, scenario="explode_new")
    assert duo.retryable_gen1_rng("gen1_new", {"a": "RESULT: PASS (x)", "b": miss}, 3, limit=limit, scenario="explode_new")
    assert not duo.retryable_gen1_rng("gen1_new", {"a": "RESULT: PASS (x)", "b": miss}, 4,
                                      limit=limit, scenario="explode_new")


def test_the_explode_ko_phrase_is_cross_checked_against_the_body():
    """The Lua card lands the string; until it does, this is skipped with the reason named."""
    body = (REPO / "lua" / "tests" / "duo" / "duo_gen1_main.lua").read_text(encoding="utf-8")
    if f'return false, "{duo.EXPLODE_KO_MISS}"' not in body:
        pytest.skip("the Lua explode-KO phrase has not landed yet (Lua card EX-2)")
    assert duo.GEN1_RNG_REASON_CLASS[duo.EXPLODE_KO_MISS] == "CAUSE_RNG"


def test_a_type_clause_partner_gone_is_the_consequence_of_the_ball_miss():
    """type_clause_new (gen1_pure lane, 2026-09-25): A's only ball missed, so A ended
    out-of-balls and B's verdict wait saw its partner finish. B's phrase is the EFFECT of A's
    miss, so the pair earns the ball-miss retry instead of a FINAL fail on attempt 1."""
    gone = "RESULT: FAIL (no type-clause verdict (partner-gone))"
    assert duo.classify_gen1_result(gone) == "CONSEQUENCE"
    limit = duo.scenario_attempt_limit("type_clause_new", "gen1_pure")
    assert duo.retryable_gen1_rng("gen1_pure", {"a": duo.RNG_OUT_OF_BALLS, "b": gone}, 1,
                                  limit=limit)
    # a verdict wait that simply ran out is not the partner's miss
    assert duo.classify_gen1_result("RESULT: FAIL (no type-clause verdict (false))") == "FINAL"


def test_a_late_ball_miss_does_not_undo_an_unobserved_species_pass(capsys, tmp_path, monkeypatch):
    """gen1_pure_green 2026-09-25: attempts 1, 3, 4 PASSED without the reroll, attempt 2 and 5
    missed the only ball (PureRGB Route 1 Eevee, catch rate 150). The reroll re-runs were
    evidence-seeking; a ball miss past the ball-miss retries must not turn their PASS into a
    FAIL. The last unobserved PASS stands, D-4 partial, and the pydec file says so."""
    miss = ("FAIL", "hunt ended out-of-balls")
    built, args = _retry_driver(monkeypatch, tmp_path,
                                ["reroll_unobserved", miss, "reroll_unobserved",
                                 "reroll_unobserved", miss])
    assert duo.run_scenario_with_rng_retry("species_clause_new", args) == (True, 4)
    assert built == [1, 2, 3, 4, 5]
    line = "attempt 5 ended on the game's RNG; the unobserved PASS of attempt 4 stands"
    assert line in capsys.readouterr().out
    pydec = (tmp_path / "e2e_species_clause_new_pydec_result.txt").read_text(encoding="utf-8")
    assert line in pydec


def test_a_late_real_failure_still_fails_after_an_unobserved_pass(capsys, tmp_path, monkeypatch):
    built, args = _retry_driver(monkeypatch, tmp_path,
                                ["reroll_unobserved", ("FAIL", "force_faint never arrived")])
    assert duo.run_scenario_with_rng_retry("species_clause_new", args) == (False, 2)
    assert built == [1, 2]
