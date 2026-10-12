"""Unit tests for tools/gen2_duo_oracles.py (card gen2-H2): a MODEL exercise of the two
`gen2_new` evidence callbacks against synthetic saves and synthetic server state. No emulator;
red-capable per refusal. Synthetic saves are built by mutating the committed played-origin
fixtures tests/fixtures/gen2/crystal_battle{,_ot2}.SaveRAM (and, for the cross-title cases,
gold_battle.SaveRAM/silver_battle.SaveRAM) through gen2_codec's own encoder, so every positive
case is still an independently-checksummed, structurally valid save of its own title."""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

import pytest

from server.adapters import gen2_codec as codec
from tools import gen2_duo_oracles as oracles
from tools.gen2_fixtures import _REGION_STARTS, _saved_field

# These MODEL mutations change saved bytes, not source/layout facts. Verify each
# pinned layout once; keep all per-case decode/hash/checksum checks fresh.
_verified_layout = lru_cache(maxsize=None)(codec.for_foundation)


@pytest.fixture(autouse=True)
def _reuse_verified_layout(monkeypatch):
    monkeypatch.setattr(codec, "for_foundation", _verified_layout)


def _replace_tag(text, tag, value):
    lines = [line for line in text.splitlines() if not line.startswith(tag + " ")]
    return "\n".join(lines + [f"{tag} {json.dumps(value)}"])


def _replace_tag_in_place(text, tag, value):
    return "\n".join(f"{tag} {json.dumps(value)}" if line.startswith(tag + " ") else line for line in text.splitlines())


def test_client_cannot_change_artifact_kind_without_its_duo_identity(good_case):
    results, _directory, _decoded = good_case
    client = oracles._last_tagged(results["a"], "CLIENT")
    client["artifact_kind"] = "overlay"
    text = _replace_tag(results["a"], "CLIENT", client)
    with pytest.raises(RuntimeError, match="artifact"):
        oracles._boot_marker("a", text)


def _overlay_markers(results):
    """MODEL replay: retain saved bytes and RAM facts, change the executed identity only."""
    pins = {title: oracles._executed_identity(title) for title in ("crystal", "gold", "silver")}
    overlays = {title: oracles._executed_identity(title, "overlay") for title in pins}

    def visit(value):
        if isinstance(value, list):
            return [visit(row) for row in value]
        if not isinstance(value, dict):
            return value
        value = {key: visit(row) for key, row in value.items()}
        title = value.get("title")
        if title in pins and value.get("rom_sha1") == pins[title][0] and "domain" not in value:
            value.update(rom_sha1=overlays[title][0], artifact_kind="overlay", binding_sha256=overlays[title][1])
            if value.get("production_admitted") is True:
                value["qualification"] = "PHYSICAL_RECEIPTED"
        return value

    for side, text in results.items():
        lines = []
        for line in text.splitlines():
            tag, _, raw = line.partition(" ")
            if raw.startswith("{"):
                line = tag + " " + json.dumps(visit(json.loads(raw)))
            lines.append(line)
        results[side] = "\n".join(lines)


def test_overlay_clause_uses_executed_identity_and_shared_ram_facts(clause_case):
    results, kwargs = clause_case
    _overlay_markers(results)
    oracles.clause_oracle(results, **kwargs)


def test_overlay_wrong_rom_admission_keeps_refused_half(admission_case):
    results, kwargs = admission_case
    _overlay_markers(results)
    oracles.admit_wrong_rom_oracle(results, **kwargs)


def test_overlay_soft_reset_uses_executed_identity(soft_reset_case):
    results, kwargs = soft_reset_case
    _overlay_markers(results)
    oracles.soft_reset_oracle(results, **kwargs)


def test_overlay_reconnect_relaunch_qualifies_its_own_artifact(reconnect_case, monkeypatch):
    from tests.live import test_gen2_new_gates
    results, kwargs = reconnect_case
    for texts in (results, kwargs["initial_results"], kwargs["relaunch_results"]):
        _overlay_markers(texts)
    path = Path(kwargs["data_dir"]) / "links.json"
    doc = json.loads(path.read_text())
    doc["artifact_kind"] = "overlay"
    path.write_text(json.dumps(doc))
    for snapshot in kwargs["snapshots"].values():
        snapshot["links"]["artifact_kind"] = "overlay"
    original = test_gen2_new_gates.qualified_identity
    def qualified(name, raw, *, repo, kind):
        assert kind == "overlay"
        # The MODEL fixture has clean physical reports only; production must request overlay.
        return original(name, raw, repo=repo, kind="clean")
    monkeypatch.setattr(test_gen2_new_gates, "qualified_identity", qualified)
    oracles.reconnect_oracle(results, **kwargs)


def _edit_saved_record(path, layout, slot, offset, payload):
    raw = bytearray(path.read_bytes())
    region, start = _region_and_offset(layout, "wPartyMon1")
    _poke(raw, region, start + slot * layout.party_size + offset, payload)
    for copy_name in ("primary", "backup"):
        checksum = codec.sav_checksum(bytes(raw[:CART]), layout, copy_name)
        at = layout.checksum_offsets[copy_name]
        raw[at:at + 2] = checksum.to_bytes(2, "little")
    path.write_bytes(raw)


@pytest.fixture
def faint_case(good_case, layout, tmp_path):
    return _make_faint_case(good_case, {"a": layout, "b": layout}, tmp_path)


def _make_faint_case(good_case, layouts, tmp_path):
    results, data_dir, decoded = good_case
    images = {}
    for inst in ("a", "b"):
        layout = layouts[inst]
        text = results[inst]
        final = oracles._last_tagged(text, "SAVE_WITNESS")
        path = Path(final["saveram_path"])
        link_path = tmp_path / f"{inst}.linked.SaveRAM"
        link_path.write_bytes(path.read_bytes())
        images[inst] = link_path
        party = codec.decode_saved_party(path.read_bytes()[:CART], layout, copy_name="primary")
        slot = party["count"] - 1
        _edit_saved_record(path, layout, slot, layout.constants["MON_HP"], bytes(2))
        _edit_saved_record(path, layout, slot, layout.constants["MON_STATUS"], bytes(1))
        final.update(frame=9000, save_completed_frame=8990, gate_saves=2, client_saves=2,
                     cartram_sha256=hashlib.sha256(path.read_bytes()[:CART]).hexdigest())
        client = oracles._last_tagged(text, "CLIENT")
        client.update(registered_sites=["battle_faint", "save_completed"],
                      rom_sha1=layout.profile["titles"][layout.title]["rom_sha1"])
        header = oracles._last_tagged(text, "DUO_GEN2")
        header.update(player=inst, scenario="gen2_faint", rom_sha1=client["rom_sha1"])
        text = _replace_tag(_replace_tag(text, "DUO_GEN2", header), "CLIENT", client)
        text = _replace_tag(text, "LINK_SAVE", {"frame": 5000, "key": decoded[inst]["key"],
            "gate_saves": 1, "client_saves": 1, "save_completed_frame": 4990,
            "saveram_path": str(link_path), "saveram_bytes": len(link_path.read_bytes()),
            "cartram_bytes": CART, "cartram_sha256": hashlib.sha256(link_path.read_bytes()[:CART]).hexdigest()})
        if inst == "a":
            text = _replace_tag(text, "ENGINE_FAINT", {"frame": 7000, "site_id": "battle_faint",
                "cause": "battle", "key": decoded[inst]["key"], "slot": slot})
            text = _replace_tag(text, "FAINT_SENT", {"frame": 7001, "key": decoded[inst]["key"], "seq": 42})
        else:
            pack = json.loads((ROOT / f"data/games/gen2_{layout.title}/write_checkpoint.json").read_text())
            primary = pack["titles"][layout.title]["primary"]
            stack = primary["caller_stack"]
            stack_bytes = bytearray(stack["required_read_bytes"])
            for word in stack["required_words"]:
                at = word["offset_from_sp"]
                stack_bytes[at:at + 2] = word["value"].to_bytes(2, word["endianness"])
            pokemon = next(r for r in layout.regions if r.name == "pokemon")
            start = pokemon.primary + layout.addresses["wPartyMon1"] - layout.addresses["wPokemonData"]
            n = layout.party_size * layout.constants["PARTY_LENGTH"]
            log = []
            for index, (offset, length) in enumerate(((layout.constants["MON_STATUS"], 1),
                                                     (layout.constants["MON_HP"], 2)), 1):
                log.append({"domain": "System Bus", "addr": layout.addresses["wPartyMon1"] + slot * 48 + offset,
                    "n": length, "why": "overworld", "status": "written", "completed": length,
                    "attempted": length, "batch_index": index, "batch_size": 2,
                    "site": "lua/gen2/entry.lua production", "evidence": "U2 PHYSICAL receipt",
                    "title": layout.title, "artifact": layout.profile["titles"][layout.title]["artifact"],
                    "rom_sha1": client["rom_sha1"]})
            write = {"frame": 7100, "key": decoded[inst]["key"], "slot": slot, "kind": "party_hp", "ok": True,
                "before_party_hex": link_path.read_bytes()[start:start + n].hex(),
                "after_party_hex": path.read_bytes()[start:start + n].hex(), "log": log,
                "checkpoint": {"pc": primary["execution_before"]["pc"], "sp": stack["minimum_sp"],
                    "hrom_bank": primary["execution_before"]["bank"], "svbk": 1, "sc": 0,
                    "stack_hex": stack_bytes.hex(), "anchor_hex": primary["anchors"]["ow_player_input"]["expected_hex"],
                    "anchors": {name: {"rom_hex": row["expected_hex"], "mapped_hex": row["expected_hex"]}
                                for name, row in primary["anchors"].items()},
                    "state": {row["symbol"]: row["value"] for row in primary["state_predicates"]}}}
            text += f"\nRX force_faint key={decoded[inst]['key']}"
            text = _replace_tag(text, "PARTY_HP_WRITE", write)
        results[inst] = _replace_tag(text, "SAVE_WITNESS", final)
    document = json.loads((Path(data_dir) / "links.json").read_text())
    document["links"][0].update(status="dead", cause="battle", initiating_player="a", killed_at="2026-09-23T12:00:00Z")
    document["pending_memorials"] = {inst: [row["key"]] for inst, row in decoded.items()}
    (Path(data_dir) / "links.json").write_text(json.dumps(document))
    (Path(data_dir) / "server.log").write_text(f"[a] faint → force_faint b:{decoded['b']['key']}\n", encoding="utf-8")
    return results, data_dir, images


def test_faint_saved_bytes_and_checkpoint_pass(faint_case):
    results, data_dir, _ = faint_case
    assert oracles.faint_oracle(results, data_dir=data_dir) is None


def _memorial_case(faint_case, native=True):
    results, data_dir, _ = faint_case
    path = Path(data_dir) / "links.json"
    doc = json.loads(path.read_text())
    row = doc["links"][0]
    row["status"] = "memorial"
    doc["pending_memorials"] = {"a": [], "b": []}
    path.write_text(json.dumps(doc))
    log = Path(data_dir) / "server.log"
    text = log.read_text(encoding="utf-8")
    event = "memorialize_done" if native else "memorialize_failed"
    text += f"[b] {event} key={row['b']['key'][:8]}\n"
    text += f"[a] {event} key={row['a']['key'][:8]}\n"
    text += "pair in route_29 fully memorialized\n" if native else "pair in route_29 marked memorial (with failed memorialization)\n"
    log.write_text(text, encoding="utf-8")
    if native:
        layout = codec.for_foundation("crystal")
        for inst in ("a", "b"):
            witness = oracles._last_tagged(results[inst], "SAVE_WITNESS")
            save = Path(witness["saveram_path"])
            raw = bytearray(save.read_bytes())
            party = codec.decode_saved_party(bytes(raw[:CART]), layout, copy_name="primary")["mons"]
            mon = party[-1]
            key = codec.key(mon)
            marker = {field: mon[field] for field in ("raw_hex", "ot_raw_hex", "nickname_raw_hex", "species_marker")}
            marker.update(frame=7200, key=key, slot=len(party) - 1)
            box = codec.verify_boxes(bytes(raw[:CART]), layout)[13]
            boxed = {**mon, "raw_hex": mon["raw_hex"][:layout.box_mon_size * 2]}
            box.update(count=1, mons=[boxed])
            start, length = layout.storage_boxes[13]
            raw[start:start + length] = codec.encode_box(box, layout)
            region = next(r for r in layout.regions if r.name == "pokemon")
            _poke(raw, region, layout.addresses["wPartyCount"] - layout.addresses["wPokemonData"], bytes([len(party) - 1]))
            _poke(raw, region, layout.addresses["wPartySpecies"] - layout.addresses["wPokemonData"] + len(party) - 1, bytes([255]))
            for copy_name in ("primary", "backup"):
                offset = layout.checksum_offsets[copy_name]
                raw[offset:offset + 2] = codec.sav_checksum(bytes(raw[:CART]), layout, copy_name).to_bytes(2, "little")
            save.write_bytes(raw)
            # Native preimage and ACK are added after the write, before the final save receipt.
            lines = [line for line in results[inst].splitlines() if not line.startswith(("SAVE_WITNESS ", "RESULT:"))]
            lines += ["MEMORIAL_PREIMAGE " + json.dumps(marker),
                      "MEMORIAL_ACK " + json.dumps({"frame": 7300, "event": "memorialize_done", "key": key, "box": 13})]
            witness["cartram_sha256"] = hashlib.sha256(raw[:CART]).hexdigest()
            lines += ["SAVE_WITNESS " + json.dumps(witness), "RESULT: PASS"]
            results[inst] = "\n".join(lines)
    return results, data_dir


def test_faint_accepts_independently_saved_memorial_with_preimages(faint_case):
    results, data_dir = _memorial_case(faint_case)
    facts = []
    assert oracles.faint_oracle(results, data_dir=data_dir, on_verified=facts.append) is None
    assert facts[0]["status"] == "memorial"


@pytest.fixture
def active_faint_case(faint_case, request):
    results, data_dir = _memorial_case(faint_case)
    layout = codec.for_foundation("crystal")
    hold = json.loads((ROOT / "data/games/gen2_crystal/write_checkpoint.json").read_text())["titles"]["crystal"]["battle_hold"]
    target = oracles._last_tagged(results["b"], "ENGINE_CAPTURE")
    slot = 1
    profile = layout.profile["titles"]["crystal"]
    spans = [(hold["write"]["targets"]["wBattleMonHP"]["address"], 2),
             (layout.addresses["wPartyMon1"] + 48 + layout.constants["MON_STATUS"], 1),
             (layout.addresses["wPartyMon1"] + 48 + layout.constants["MON_HP"], 2),
             (hold["write"]["targets"]["wBattlePlayerAction"]["address"], 1)]
    log = [{"domain": "System Bus", "addr": address, "n": n, "why": "battle_hold", "status": "written",
            "completed": n, "attempted": n, "batch_index": index, "batch_size": 4,
            "site": "lua/gen2/entry.lua production", "evidence": "U2 PHYSICAL receipt", "title": "crystal",
            "artifact": profile["artifact"], "rom_sha1": profile["rom_sha1"]} for index, (address, n) in enumerate(spans, 1)]
    write = {"frame": 7100, "seq": 1, "key": target["key"], "slot": slot, "active_slot": slot,
             "kind": "battle_faint", "ok": True, "pc": hold["execution_before"]["pc"], "hrom_bank": hold["execution_before"]["bank"],
             "battle_hp_before_hex": "000c", "battle_hp_after_hex": "0000", "hp_before_hex": "000c", "hp_after_hex": "0000",
             "status_after_hex": "00", "action_before_hex": "00", "action_after_hex": "01", "log": log}
    active = {"frame": 6000, "key": target["key"], "slot": slot, "cur_battle_mon": slot,
              "battle_mon_species": target["species_id"], "battle_mode": 1, "battle_type": 0, "link_mode": 0}
    for side in ("a", "b"):
        head = oracles._last_tagged(results[side], "DUO_GEN2")
        head["scenario"] = "gen2_faint_active"
        results[side] = _replace_tag_in_place(results[side], "DUO_GEN2", head)
        lines = []
        for line in results[side].splitlines():
            if line.startswith("PARTY_HP_WRITE ") and side == "b":
                lines += ["BATTLE_HOLD_WRITE " + json.dumps(write), "BATTLE_TRACE " + json.dumps({"frame": 7100, "seq": 2, "what": "faint"}),
                          "NEXT_MON " + json.dumps({"frame": 7110}), "REPLACED " + json.dumps({"frame": 7120, "active_slot": 0, "hp": 12}),
                          "LINKED_HP_STATUS 0000 00"]
                if getattr(request, "param", None):
                    repeat = json.loads(line[len("PARTY_HP_WRITE "):])
                    repeat.update(frame=7150, before_party_hex=repeat["after_party_hex"])
                    if request.param == "changed_repeat":
                        raw = bytearray.fromhex(repeat["after_party_hex"])
                        raw[1] ^= 1
                        repeat["after_party_hex"] = raw.hex()
                    lines.append("PARTY_HP_WRITE " + json.dumps(repeat))
                continue
            if line.startswith("RESULT:"):
                lines.append("RECEIPT " + json.dumps({**head, "schema": "gen2-duo-faint-active-v1"}))
            lines.append(line)
            if line.startswith("LINK_SAVE "):
                lines.append(("B_ACTIVE " + json.dumps({"frame": 6000})) if side == "a" else "LINKED_ACTIVE " + json.dumps(active))
        results[side] = "\n".join(lines)
    return results, data_dir


def test_faint_active_independent_memorial_proof(active_faint_case):
    results, data_dir = active_faint_case
    facts = []
    oracles.faint_active_oracle(results, data_dir=data_dir, on_verified=facts.append)
    assert facts[0]["status"] == "memorial" and facts[0]["death"] == "active"


@pytest.mark.parametrize("active_faint_case", ["repeat", "changed_repeat"], indirect=True)
def test_faint_active_checkpoint_repeat_must_be_idempotent(active_faint_case, request):
    results, data_dir = active_faint_case
    if request.node.callspec.params["active_faint_case"] == "repeat":
        oracles.faint_active_oracle(results, data_dir=data_dir)
    else:
        with pytest.raises(RuntimeError, match="idempotent"):
            oracles.faint_active_oracle(results, data_dir=data_dir)


def test_a_wild_active_faint_refuses_an_enemy_faint_witness(active_faint_case):
    """OMP cx-6387febc F1: the trainer-only enemy_faint witness is refused in the WILD active faint (the Python
    rule had only a Lua-side test). A schema-valid row after the last trace, in an otherwise passing wild proof."""
    results, data_dir = active_faint_case
    lines = results["b"].splitlines()
    at = max(i for i, l in enumerate(lines) if l.startswith("BATTLE_TRACE "))
    last = json.loads(lines[at][len("BATTLE_TRACE "):])
    row = {"seq": last["seq"] + 1, "what": "enemy_faint", "frame": last["frame"] + 100, "battle_mode": 2,
           "species": 16, "hp_before": 9, "hp_after": 0, "title": "crystal"}
    lines.insert(at + 1, "BATTLE_TRACE " + json.dumps(row))
    results["b"] = chr(10).join(lines)
    with pytest.raises(RuntimeError, match="enemy_faint row in a wild scenario"):
        oracles.faint_active_oracle(results, data_dir=data_dir)


def test_faint_active_allows_survivor_battle_changes_not_reward_quantified(active_faint_case):
    results, data_dir = active_faint_case
    layout = codec.for_foundation("crystal")
    save = Path(oracles._last_tagged(results["b"], "SAVE_WITNESS")["saveram_path"])
    for field, value in (("MON_PP", b"\x01"), ("MON_EXP", b"\0\0\x90"), ("MON_HP", b"\0\x01"), ("MON_STATUS", b"\x08")):
        _edit_saved_record(save, layout, 0, layout.constants[field], value)
    _refresh_faint_hash(results, "b")
    oracles.faint_active_oracle(results, data_dir=data_dir)


def test_faint_active_readback_can_follow_memorial_ack(active_faint_case):
    results, data_dir = active_faint_case
    text = results["b"].replace("LINKED_HP_STATUS 0000 00\n", "")
    lines = []
    for line in text.splitlines():
        lines.append(line)
        if line.startswith("MEMORIAL_ACK "):
            lines.append("LINKED_HP_STATUS 0000 00")
    results["b"] = "\n".join(lines)
    oracles.faint_active_oracle(results, data_dir=data_dir)


@pytest.mark.parametrize("fault", ["missing_box_key", "starter_dead", "starter_identity", "starter_name", "preimage_status", "server_not_memorial"])
def test_faint_active_refuses_independent_save_and_server_mutations(active_faint_case, fault):
    results, data_dir = active_faint_case
    layout = codec.for_foundation("crystal")
    if fault == "server_not_memorial":
        path = Path(data_dir) / "links.json"
        document = json.loads(path.read_text())
        document["links"][0]["status"] = "dead"
        path.write_text(json.dumps(document))
    elif fault == "preimage_status":
        marker = oracles._last_tagged(results["b"], "MEMORIAL_PREIMAGE")
        raw = bytearray.fromhex(marker["raw_hex"])
        raw[layout.constants["MON_STATUS"]] = 1
        marker["raw_hex"] = raw.hex()
        results["b"] = _replace_tag_in_place(results["b"], "MEMORIAL_PREIMAGE", marker)
    else:
        path = Path(oracles._last_tagged(results["b"], "SAVE_WITNESS")["saveram_path"])
        if fault == "starter_dead":
            _edit_saved_record(path, layout, 0, layout.constants["MON_HP"], bytes(2))
        elif fault == "starter_identity":
            _edit_saved_record(path, layout, 0, layout.constants["MON_DVS"], b"\0\0")
        else:
            raw = bytearray(path.read_bytes())
            if fault == "missing_box_key":
                at = layout.storage_boxes[13][0]
                raw[at:at + 2] = bytes([0, 255])
            else:
                region, offset = _region_and_offset(layout, "wPartyMonNicknames")
                _poke(raw, region, offset, bytes([0x81]))
                for copy_name in ("primary", "backup"):
                    at = layout.checksum_offsets[copy_name]
                    raw[at:at + 2] = codec.sav_checksum(bytes(raw[:CART]), layout, copy_name).to_bytes(2, "little")
            path.write_bytes(raw)
        _refresh_faint_hash(results, "b")
    with pytest.raises(RuntimeError):
        oracles.faint_active_oracle(results, data_dir=data_dir)


@pytest.mark.parametrize("fault", ["slot", "key", "battle_mode", "pc", "bank", "zero_before", "party_hp", "status", "action",
    "permit", "enemy_first", "later_frame", "lost", "echo", "no_replacement", "same_slot", "dead_starter", "readback", "go", "schema"])
def test_faint_active_refuses_marker_mutations(active_faint_case, fault):
    results, data_dir = active_faint_case
    if fault in ("slot", "key", "battle_mode"):
        tag = "LINKED_ACTIVE"
        row = oracles._last_tagged(results["b"], tag)
        row[{"slot": "cur_battle_mon", "key": "key", "battle_mode": "battle_mode"}[fault]] = "bad" if fault == "key" else 0
    elif fault in ("pc", "bank", "zero_before", "party_hp", "status", "action", "permit"):
        tag = "BATTLE_HOLD_WRITE"
        row = oracles._last_tagged(results["b"], tag)
        if fault == "permit":
            row["log"][3]["addr"] -= 1
        else:
            field, value = {"pc": ("pc", 0), "bank": ("hrom_bank", 0), "zero_before": ("battle_hp_before_hex", "0000"),
                            "party_hp": ("hp_after_hex", "0001"), "status": ("status_after_hex", "01"), "action": ("action_after_hex", "00")}[fault]
            row[field] = value
    elif fault in ("enemy_first", "later_frame", "lost"):
        tag = "BATTLE_TRACE"
        row = oracles._last_tagged(results["b"], tag)
        row["frame" if fault == "later_frame" else "what"] = 7101 if fault == "later_frame" else ("lost" if fault == "lost" else "enemy_turn")
    elif fault in ("same_slot", "dead_starter"):
        tag = "REPLACED"
        row = oracles._last_tagged(results["b"], tag)
        row["active_slot" if fault == "same_slot" else "hp"] = 1 if fault == "same_slot" else 0
    else:
        tag = None
        if fault == "echo":
            results["b"] += "\nFAINT_SENT {}"
        elif fault == "no_replacement":
            results["b"] = "\n".join(line for line in results["b"].splitlines() if not line.startswith("REPLACED "))
        elif fault == "readback":
            results["b"] = results["b"].replace("LINKED_HP_STATUS 0000 00", "LINKED_HP_STATUS 0001 00")
        elif fault == "go":
            results["a"] = "\n".join(line for line in results["a"].splitlines() if not line.startswith("B_ACTIVE "))
        else:
            results["b"] = results["b"].replace("gen2-duo-faint-active-v1", "gen2-duo-faint-v1")
    if tag:
        results["b"] = _replace_tag_in_place(results["b"], tag, row)
    with pytest.raises(RuntimeError):
        oracles.faint_active_oracle(results, data_dir=data_dir)


def test_faint_refuses_nack_only_memorial_without_saved_boxes(faint_case):
    results, data_dir = _memorial_case(faint_case, native=False)
    with pytest.raises(RuntimeError):
        oracles.faint_oracle(results, data_dir=data_dir)


@pytest.mark.parametrize("fault", ["missing_a", "missing_b", "wrong_key", "no_transition", "wrong_order", "pending"])
def test_faint_memorial_requires_ordered_server_evidence(faint_case, fault):
    results, data_dir = _memorial_case(faint_case)
    path = Path(data_dir) / "server.log"
    lines = path.read_text(encoding="utf-8").splitlines()
    if fault in ("missing_a", "missing_b"):
        prefix = f"[{fault[-1]}] memorialize_done"
        lines = [line for line in lines if not line.startswith(prefix)]
    elif fault == "wrong_key":
        lines[1] = "[b] memorialize_done key=BAD:KEY"
    elif fault == "no_transition":
        lines.pop()
    elif fault == "wrong_order":
        lines = [lines[-1], *lines[:-1]]
    else:
        state = Path(data_dir) / "links.json"
        doc = json.loads(state.read_text())
        doc["pending_memorials"]["b"] = [doc["links"][0]["b"]["key"]]
        state.write_text(json.dumps(doc))
    path.write_text("\n".join(lines), encoding="utf-8")
    with pytest.raises(RuntimeError):
        oracles.faint_oracle(results, data_dir=data_dir)


@pytest.mark.parametrize("fault", ["missing_key", "wrong_box", "duplicate_key", "pp", "non_pp", "missing_preimage",
    "alive_preimage", "preimage_key", "unknown_move", "wrong_ack", "late_write", "other_b_mon"])
def test_faint_memorial_refuses_native_evidence_corruption(faint_case, fault):
    results, data_dir = _memorial_case(faint_case)
    layout = codec.for_foundation("crystal")
    if fault in ("missing_key", "wrong_box", "duplicate_key", "pp", "non_pp", "other_b_mon"):
        witness = oracles._last_tagged(results["b"], "SAVE_WITNESS")
        path = Path(witness["saveram_path"])
        raw = bytearray(path.read_bytes())
        start, size = layout.storage_boxes[13]
        if fault in ("wrong_box", "duplicate_key"):
            target, _ = layout.storage_boxes[12]
            raw[target:target + size] = raw[start:start + size]
        if fault in ("missing_key", "wrong_box"):
            raw[start:start + 2] = bytes([0, 255])
        elif fault in ("pp", "non_pp"):
            at = start + layout.addresses["sBoxMon1"] - layout.addresses["sBox"]
            raw[at + layout.constants["MON_PP" if fault == "pp" else "MON_ITEM"]] ^= 1
        path.write_bytes(raw)
        if fault == "other_b_mon":
            _edit_saved_record(path, layout, 0, layout.constants["MON_HAPPINESS"], bytes([1]))
        _refresh_faint_hash(results, "b")
    elif fault == "missing_preimage":
        results["a"] = "\n".join(line for line in results["a"].splitlines() if not line.startswith("MEMORIAL_PREIMAGE "))
    elif fault == "wrong_ack":
        ack = oracles._last_tagged(results["a"], "MEMORIAL_ACK")
        ack["event"] = "memorialize_failed"
        results["a"] = _replace_tag_in_place(results["a"], "MEMORIAL_ACK", ack)
    elif fault == "late_write":
        line = next(line for line in results["b"].splitlines() if line.startswith("PARTY_HP_WRITE "))
        results["b"] = results["b"].replace(line + "\n", "") + "\n" + line
    else:
        marker = oracles._last_tagged(results["a"], "MEMORIAL_PREIMAGE")
        if fault == "preimage_key":
            marker["key"] = "bad"
        else:
            raw = bytearray.fromhex(marker["raw_hex"])
            raw[layout.constants["MON_HP"] + 1 if fault == "alive_preimage" else layout.constants["MON_MOVES"]] = 1 if fault == "alive_preimage" else 255
            marker["raw_hex"] = raw.hex()
        results["a"] = _replace_tag_in_place(results["a"], "MEMORIAL_PREIMAGE", marker)
    with pytest.raises(RuntimeError):
        oracles.faint_oracle(results, data_dir=data_dir)


def _repeat_write(results):
    write = oracles._last_tagged(results["b"], "PARTY_HP_WRITE")
    repeat = deepcopy(write)
    repeat.update(frame=write["frame"] + 1, before_party_hex=write["after_party_hex"])
    return repeat


@pytest.mark.parametrize("frame", [7101, 8995])
def test_faint_accepts_one_idempotent_repeat(faint_case, frame):
    results, data_dir, _ = faint_case
    repeat = _repeat_write(results)
    repeat["frame"] = frame
    results["b"] += f"\nRX force_faint key={repeat['key']}\nPARTY_HP_WRITE {json.dumps(repeat)}"
    assert oracles.faint_oracle(results, data_dir=data_dir) is None


@pytest.mark.parametrize("fault", ["key", "slot", "bytes", "checkpoint", "third_write"])
def test_faint_repeat_must_be_idempotent_and_qualified(faint_case, fault):
    results, data_dir, _ = faint_case
    repeat = _repeat_write(results)
    if fault == "key":
        repeat["key"] = "wrong"
    elif fault == "slot":
        repeat["slot"] = 0
    elif fault == "bytes":
        raw = bytearray.fromhex(repeat["after_party_hex"])
        raw[0] ^= 1
        repeat["after_party_hex"] = raw.hex()
    elif fault == "checkpoint":
        repeat["checkpoint"]["state"]["wBattleMode"] = 1
    results["b"] += f"\nRX force_faint key={repeat['key']}\nPARTY_HP_WRITE {json.dumps(repeat)}"
    if fault == "third_write":
        results["b"] += f"\nRX force_faint key={repeat['key']}\nPARTY_HP_WRITE {json.dumps(repeat)}"
    with pytest.raises(RuntimeError):
        oracles.faint_oracle(results, data_dir=data_dir)


@pytest.mark.parametrize("name", ["ow_player_input", "player_events_caller"])
@pytest.mark.parametrize("domain", ["rom_hex", "mapped_hex"])
@pytest.mark.parametrize("fault", ["missing", "bad"])
def test_faint_requires_every_anchor_in_both_domains(faint_case, name, domain, fault):
    results, data_dir, _ = faint_case
    write = oracles._last_tagged(results["b"], "PARTY_HP_WRITE")
    if fault == "missing":
        write["checkpoint"]["anchors"][name].pop(domain)
    else:
        raw = bytearray.fromhex(write["checkpoint"]["anchors"][name][domain])
        raw[0] ^= 1
        write["checkpoint"]["anchors"][name][domain] = raw.hex()
    results["b"] = _replace_tag(results["b"], "PARTY_HP_WRITE", write)
    with pytest.raises(RuntimeError, match="anchor"):
        oracles.faint_oracle(results, data_dir=data_dir)


@pytest.mark.parametrize("titles", [("gold", "silver"), ("crystal", "gold")])
def test_cross_title_faint_and_decoded_fact_receipt(tmp_path, titles):
    layouts = {side: codec.for_foundation(title) for side, title in zip(("a", "b"), titles, strict=True)}
    ots = {"crystal": 46401, "gold": 50342, "silver": 51084}
    sides = {side: (layouts[side], ROOT / f"tests/fixtures/gen2/{title}_battle.SaveRAM",
                    f"{title}_battle", title, ots[title], 16 + index * 3)
             for index, (side, title) in enumerate(zip(("a", "b"), titles, strict=True))}
    base = _duo_case(tmp_path, sides)
    results, data_dir, _ = _make_faint_case(base, layouts, tmp_path)
    facts = []
    assert oracles.faint_oracle(results, data_dir=data_dir, on_verified=facts.append) is None
    assert facts == [{"a": base[2]["a"]["key"], "b": base[2]["b"]["key"],
                      "titles": "/".join(titles), "area": "route_29", "status": "dead"}]


def test_link_facts_are_decoded_and_not_emitted_on_refusal(good_case):
    results, data_dir, decoded = good_case
    facts = []
    assert oracles.link_oracle(results, data_dir=data_dir, on_verified=facts.append) is None
    assert facts == [{"a": decoded["a"]["key"], "b": decoded["b"]["key"],
                      "area": "route_29", "titles": "crystal/crystal", "status": "alive"}]
    facts.clear()
    (Path(data_dir) / "links.json").write_text('{"links": []}')
    with pytest.raises(RuntimeError):
        oracles.link_oracle(results, data_dir=data_dir, on_verified=facts.append)
    assert facts == []


@pytest.mark.parametrize("field", ["gate_saves", "client_saves", "save_completed_frame"])
def test_faint_requires_a_new_save_after_link(faint_case, field):
    results, data_dir, _ = faint_case
    link = oracles._last_tagged(results["b"], "LINK_SAVE")
    final = oracles._last_tagged(results["b"], "SAVE_WITNESS")
    final[field] = link[field]
    if field in ("gate_saves", "client_saves"):
        final.update(gate_saves=link["gate_saves"], client_saves=link["client_saves"])
    results["b"] = _replace_tag(results["b"], "SAVE_WITNESS", final)
    with pytest.raises(RuntimeError, match="newer|chronology"):
        oracles.faint_oracle(results, data_dir=data_dir)


def test_gold_marker_cannot_claim_layout_identical_silver_fixture(tmp_path):
    layouts = {title: codec.for_foundation(title) for title in ("gold", "silver")}
    silver = ROOT / "tests/fixtures/gen2/silver_battle.SaveRAM"
    assert codec.strict_checksum_witness(silver.read_bytes()[:CART], layouts["gold"])["valid"]
    sides = {side: (layouts[title], ROOT / f"tests/fixtures/gen2/{title}_battle.SaveRAM",
                    f"{title}_battle", title, ot, species)
             for side, title, ot, species in (("a", "gold", 50342, 16), ("b", "silver", 51084, 19))}
    results, _data_dir, _ = _duo_case(tmp_path, sides)
    duo = oracles._last_tagged(results["a"], "DUO_GEN2")
    duo.update(case="silver_battle", fixture_sha256=hashlib.sha256(silver.read_bytes()).hexdigest())
    results["a"] = _replace_tag(results["a"], "DUO_GEN2", duo)
    with pytest.raises(RuntimeError, match="case.*title|title.*case"):
        oracles.check_save_witness(results)


def _refresh_faint_hash(results, inst, tag="SAVE_WITNESS"):
    marker = oracles._last_tagged(results[inst], tag)
    marker["cartram_sha256"] = hashlib.sha256(Path(marker["saveram_path"]).read_bytes()[:CART]).hexdigest()
    results[inst] = _replace_tag(results[inst], tag, marker)


@pytest.mark.parametrize("fault", [
    "missing_link_a", "missing_link_b", "missing_engine_faint", "missing_faint_sent", "missing_write",
    "duplicate_write", "unregistered", "not_production", "wrong_pin", "no_rx", "server_command", "server_key",
    "alive_pair", "poison_cause", "wrong_initiator", "no_death_time", "duplicate_link", "pending_key",
    "a_alive", "b_alive", "b_other_hp", "b_target_pp", "link_dead", "link_hash", "link_checksum", "link_key",
    "write_preimage", "write_readback", "extra_write_a", "write_before_link", "write_after_save",
    "faint_before_link", "faint_after_save", "faint_key", "faint_site", "faint_cause", "faint_slot", "sent_key",
    "sent_seq", "boolean_save_count", "negative_save_count", "faint_wrong_slot",
])
def test_faint_mutations_refused(faint_case, layout, fault):
    original, data_dir, _ = faint_case
    results = deepcopy(original)
    write = oracles._last_tagged(results["b"], "PARTY_HP_WRITE")
    faint = oracles._last_tagged(results["a"], "ENGINE_FAINT")
    b_key = write["key"]
    missing = {"missing_link_a": ("a", "LINK_SAVE"), "missing_link_b": ("b", "LINK_SAVE"),
        "missing_engine_faint": ("a", "ENGINE_FAINT"), "missing_faint_sent": ("a", "FAINT_SENT"),
        "missing_write": ("b", "PARTY_HP_WRITE")}
    if fault in missing:
        side, tag = missing[fault]
        results[side] = "\n".join(line for line in results[side].splitlines() if not line.startswith(tag + " "))
    elif fault == "duplicate_write":
        results["b"] += "\nPARTY_HP_WRITE " + json.dumps(write)
    elif fault in ("unregistered", "not_production", "wrong_pin"):
        client = oracles._last_tagged(results["a"], "CLIENT")
        if fault == "unregistered":
            client["registered_sites"] = ["save_completed"]
        elif fault == "not_production":
            client["production_admitted"] = False
        else:
            client["rom_sha1"] = "f" * 40
            duo = oracles._last_tagged(results["a"], "DUO_GEN2")
            duo["rom_sha1"] = client["rom_sha1"]
            results["a"] = _replace_tag(results["a"], "DUO_GEN2", duo)
        results["a"] = _replace_tag(results["a"], "CLIENT", client)
    elif fault == "no_rx":
        results["b"] = results["b"].replace("RX force_faint", "RX noop")
    elif fault in ("server_command", "server_key"):
        (Path(data_dir) / "server.log").write_text(
            f"[a] faint → {'noop' if fault == 'server_command' else 'force_faint'} b:"
            f"{'wrong' if fault == 'server_key' else b_key}\n", encoding="utf-8")
    elif fault in ("alive_pair", "poison_cause", "wrong_initiator", "no_death_time", "duplicate_link", "pending_key"):
        path = Path(data_dir) / "links.json"
        doc = json.loads(path.read_text())
        row = doc["links"][0]
        if fault == "duplicate_link":
            doc["links"].append(deepcopy(row))
        elif fault == "pending_key":
            doc["pending_captures"] = {"route_30": {"b": row["b"]}}
        else:
            field, value = {"alive_pair": ("status", "alive"), "poison_cause": ("cause", "poison"),
                            "wrong_initiator": ("initiating_player", "b"), "no_death_time": ("killed_at", None)}[fault]
            row[field] = value
        path.write_text(json.dumps(doc))
    elif fault in ("a_alive", "b_alive", "b_other_hp", "b_target_pp", "link_dead"):
        inst = "a" if fault == "a_alive" else "b"
        tag = "LINK_SAVE" if fault == "link_dead" else "SAVE_WITNESS"
        marker = oracles._last_tagged(results[inst], tag)
        path = Path(marker["saveram_path"])
        slot = 0 if fault == "b_other_hp" else 1
        offset = layout.constants["MON_PP"] if fault == "b_target_pp" else layout.constants["MON_HP"]
        value = b"\x01" if fault == "b_target_pp" else b"\x00\x00" if fault == "link_dead" else b"\x00\x01"
        _edit_saved_record(path, layout, slot, offset, value)
        _refresh_faint_hash(results, inst, tag)
    elif fault in ("link_hash", "link_checksum", "link_key"):
        marker = oracles._last_tagged(results["a"], "LINK_SAVE")
        if fault == "link_hash":
            marker["cartram_sha256"] = "0" * 64
        elif fault == "link_key":
            marker["key"] = "wrong"
        else:
            path = Path(marker["saveram_path"])
            raw = bytearray(path.read_bytes())
            raw[layout.checksum_offsets["primary"]] ^= 1
            path.write_bytes(raw)
            marker["cartram_sha256"] = hashlib.sha256(raw[:CART]).hexdigest()
        results["a"] = _replace_tag(results["a"], "LINK_SAVE", marker)
    elif fault in ("write_preimage", "write_readback", "write_before_link", "write_after_save"):
        if fault.startswith("write_before") or fault.startswith("write_after"):
            write["frame"] = 4999 if fault == "write_before_link" else 9001
        else:
            field = "before_party_hex" if fault == "write_preimage" else "after_party_hex"
            raw = bytearray.fromhex(write[field])
            raw[0] ^= 1
            write[field] = raw.hex()
        results["b"] = _replace_tag(results["b"], "PARTY_HP_WRITE", write)
    elif fault == "extra_write_a":
        results["a"] += "\nPARTY_HP_WRITE " + json.dumps(write)
    elif fault.startswith("faint_"):
        field, value = {"faint_before_link": ("frame", 4999), "faint_after_save": ("frame", 9001),
            "faint_key": ("key", "wrong"), "faint_site": ("site_id", "poison_faint"),
            "faint_cause": ("cause", "poison"), "faint_slot": ("slot", 6), "faint_wrong_slot": ("slot", 0)}[fault]
        faint[field] = value
        results["a"] = _replace_tag(results["a"], "ENGINE_FAINT", faint)
    elif fault in ("sent_key", "sent_seq"):
        sent = oracles._last_tagged(results["a"], "FAINT_SENT")
        sent["key" if fault == "sent_key" else "seq"] = "wrong" if fault == "sent_key" else True
        results["a"] = _replace_tag(results["a"], "FAINT_SENT", sent)
    else:
        final = oracles._last_tagged(results["a"], "SAVE_WITNESS")
        value = -1 if fault == "negative_save_count" else True
        final.update(gate_saves=value, client_saves=value)
        results["a"] = _replace_tag(results["a"], "SAVE_WITNESS", final)
    with pytest.raises(RuntimeError):
        oracles.faint_oracle(results, data_dir=data_dir)


@pytest.mark.parametrize("fault", ["sibling_fixture", "sibling_hash", "cross_title_case", "null_rom", "bad_rom", "null_hash"])
def test_link_fixture_and_marker_bindings_refuse(good_case, fault):
    results, data_dir, _ = good_case
    duo = oracles._last_tagged(results["a"], "DUO_GEN2")
    duo["fixture_sha256"] = hashlib.sha256(FIXTURES["a"].read_bytes()).hexdigest()
    if fault in ("sibling_fixture", "cross_title_case"):
        duo["case"] = "crystal_town" if fault == "sibling_fixture" else "gold_battle"
        if fault == "cross_title_case":
            duo["fixture_sha256"] = hashlib.sha256((ROOT / "tests/fixtures/gen2/gold_battle.SaveRAM").read_bytes()).hexdigest()
            # The bad case/title relationship must refuse before save-layout decoding.
            results["a"] = _replace_tag(results["a"], "DUO_GEN2", duo)
            with pytest.raises(RuntimeError, match="case.*title|title.*case"):
                oracles.check_save_witness(results)
            return
    elif fault == "sibling_hash":
        duo["fixture_sha256"] = hashlib.sha256((ROOT / "tests/fixtures/gen2/crystal_town.SaveRAM").read_bytes()).hexdigest()
    elif fault in ("null_rom", "bad_rom"):
        value = None if fault == "null_rom" else "not-a-sha1"
        duo["rom_sha1"] = value
        client = oracles._last_tagged(results["a"], "CLIENT")
        client["rom_sha1"] = value
        results["a"] = _replace_tag(results["a"], "CLIENT", client)
    else:
        duo["fixture_sha256"] = None
    results["a"] = _replace_tag(results["a"], "DUO_GEN2", duo)
    with pytest.raises(RuntimeError):
        oracles.link_oracle(results, data_dir=data_dir)


@pytest.mark.parametrize("field,value", [("pc", 0), ("hrom_bank", 0), ("svbk", 2), ("sc", 128),
    ("sp", 0), ("stack_hex", "0000"), ("anchor_hex", "00"), ("state", {})])
def test_faint_wrong_checkpoint_refused(faint_case, field, value):
    results, data_dir, _ = faint_case
    write = oracles._last_tagged(results["b"], "PARTY_HP_WRITE")
    write["checkpoint"][field] = value
    results["b"] = _replace_tag(results["b"], "PARTY_HP_WRITE", write)
    with pytest.raises(RuntimeError, match="checkpoint|anchor|caller"):
        oracles.faint_oracle(results, data_dir=data_dir)


@pytest.mark.parametrize("symbol", ["wMapStatus", "wMapEventStatus", "wScriptRunning", "wScriptMode",
    "wScriptFlags", "wScriptStackSize", "wJoypadDisable", "wGameLogicPaused", "wInputType", "wBattleMode",
    "wStateFlags", "hMapEntryMethod", "wLinkMode", "hSerialConnectionStatus", "wSavedAtLeastOnce"])
def test_faint_every_checkpoint_predicate_refuses(faint_case, symbol):
    results, data_dir, _ = faint_case
    write = oracles._last_tagged(results["b"], "PARTY_HP_WRITE")
    bit = {"wScriptFlags": 4, "wStateFlags": 128}.get(symbol, 1)
    write["checkpoint"]["state"][symbol] ^= bit
    results["b"] = _replace_tag(results["b"], "PARTY_HP_WRITE", write)
    with pytest.raises(RuntimeError, match=symbol):
        oracles.faint_oracle(results, data_dir=data_dir)


@pytest.mark.parametrize("field,value", [("domain", "WRAM"), ("addr", 0), ("n", 3), ("why", "battle"),
    ("status", "error"), ("completed", 0), ("attempted", 0), ("batch_index", 0), ("batch_size", 1),
    ("site", "harness"), ("evidence", "MODEL"), ("title", "gold"), ("artifact", "pokegold"), ("rom_sha1", "0" * 40)])
def test_faint_permit_receipt_mutations_refused(faint_case, field, value):
    results, data_dir, _ = faint_case
    write = oracles._last_tagged(results["b"], "PARTY_HP_WRITE")
    write["log"][1][field] = value
    results["b"] = _replace_tag(results["b"], "PARTY_HP_WRITE", write)
    with pytest.raises(RuntimeError, match="permit span"):
        oracles.faint_oracle(results, data_dir=data_dir)


def test_faint_allows_native_battle_changes_to_a_other_mon(faint_case, layout):
    results, data_dir, _ = faint_case
    marker = oracles._last_tagged(results["a"], "SAVE_WITNESS")
    _edit_saved_record(Path(marker["saveram_path"]), layout, 0, layout.constants["MON_PP"], b"\x01")
    _refresh_faint_hash(results, "a")
    assert oracles.faint_oracle(results, data_dir=data_dir) is None


def test_faint_allows_a_to_gain_a_level_before_eventual_death(faint_case, layout):
    results, data_dir, _ = faint_case
    marker = oracles._last_tagged(results["a"], "SAVE_WITNESS")
    _edit_saved_record(Path(marker["saveram_path"]), layout, 1, layout.constants["MON_LEVEL"], b"\x06")
    _refresh_faint_hash(results, "a")
    path = Path(data_dir) / "links.json"
    doc = json.loads(path.read_text())
    doc["links"][0]["a"]["level"] = 6
    path.write_text(json.dumps(doc))
    assert oracles.faint_oracle(results, data_dir=data_dir) is None

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = {"a": ROOT / "tests/fixtures/gen2/crystal_battle.SaveRAM",
            "b": ROOT / "tests/fixtures/gen2/crystal_battle_ot2.SaveRAM"}
OT_IDS = {"a": 46401, "b": 44068}
CASES = {"a": "crystal_battle", "b": "crystal_battle_ot2"}
CART = oracles.CARTRAM_BYTES

# Cross-title fixtures (owner ruling O-16: Gold<->Silver, Crystal<->Gold also pair). OTs and
# fixture names confirmed against the real committed saves by the H1b duo driver / Codex H3b.
GOLD_FIXTURE = ROOT / "tests/fixtures/gen2/gold_battle.SaveRAM"
SILVER_FIXTURE = ROOT / "tests/fixtures/gen2/silver_battle.SaveRAM"
GOLD_OT = 0xC4A6
SILVER_OT = 0xC78C


@pytest.fixture(scope="module")
def layout():
    return codec.for_foundation("crystal")


def _region_and_offset(layout, symbol):
    address = layout.addresses[symbol]
    for region in layout.regions:
        base = layout.addresses[_REGION_STARTS[region.name]]
        if base <= address < base + region.length:
            return region, address - base
    raise RuntimeError(f"{symbol} not in any saved region")


def _poke(cart, region, offset, data):
    for base in (region.primary, region.backup):
        cart[base + offset:base + offset + len(data)] = data


def build_capture(layout, fixture_path, *, ot_id, species=16, dv_word=None, ball_delta=-1):
    """A synthetic post-battle flushed save: the given committed boot fixture plus one new party
    mon and a smaller Ball pocket, re-checksummed on both the primary and backup copies.

    Returns (full_save_bytes_with_rtc_tail, decoded_new_mon).
    """
    raw = bytearray(fixture_path.read_bytes())
    tail = bytes(raw[CART:])
    cart = bytearray(raw[:CART])
    pokemon = next(r for r in layout.regions if r.name == "pokemon")

    def rel(base_symbol, symbol):
        return layout.addresses[symbol] - layout.addresses[base_symbol]

    party = codec.decode_saved_party(bytes(cart), layout, copy_name="primary")
    template = party["mons"][0]
    count = party["count"]
    dv_word = (template["dv_word"] ^ 0x1234) if dv_word is None else dv_word

    count_off = rel("wPokemonData", "wPartyCount")
    species_off = rel("wPokemonData", "wPartySpecies")
    record_off = rel("wPokemonData", "wPartyMon1") + count * layout.party_size
    ot_off = rel("wPokemonData", "wPartyMonOTs") + count * layout.name_size
    nick_off = rel("wPokemonData", "wPartyMonNicknames") + count * layout.nickname_size

    new_mon = dict(template)
    new_mon.update(species_id=species, species_marker=species, ot_id=ot_id, dv_word=dv_word,
                  raw_hex="00" * 48)
    new_mon["dvs"] = codec.decode_dvs(dv_word)
    new_record = codec.encode_party_mon(new_mon, layout)

    _poke(cart, pokemon, record_off, new_record)
    _poke(cart, pokemon, ot_off, bytes.fromhex(template["ot_raw_hex"]))
    _poke(cart, pokemon, nick_off, bytes.fromhex(template["nickname_raw_hex"]))
    _poke(cart, pokemon, species_off + count, bytes([species]))
    _poke(cart, pokemon, species_off + count + 1, bytes([255]))
    _poke(cart, pokemon, count_off, bytes([count + 1]))

    if ball_delta:
        before = _saved_field(bytes(cart), layout, "wNumBalls", 1)[0]
        pocket = _saved_field(bytes(cart), layout, "wBalls", before * 2 + 1)
        # wBalls lives in "player" on Crystal but "player3" on Gold/Silver (three split player
        # regions): resolve the region that actually contains it rather than assuming one name.
        balls_region, balls_off = _region_and_offset(layout, "wBalls")
        _poke(cart, balls_region, balls_off + 1, bytes([pocket[1] + ball_delta]))  # slot 0's quantity byte

    for copy_name in ("primary", "backup"):
        checksum = codec.sav_checksum(bytes(cart), layout, copy_name)
        off = layout.checksum_offsets[copy_name]
        cart[off:off + 2] = checksum.to_bytes(2, "little")

    decoded_new = codec.decode_party_mon(new_record, layout, species_marker=species)
    return bytes(cart) + tail, decoded_new


def _marker_text(*, saveram_path, cartram, key, species, level, hello_ot_id, title="crystal",
                  case="crystal_battle", area_id="route_29", gate_saves=1, client_saves=1,
                  flushed_matches=True, sha256=None, site_id="capture_party_finalized",
                  acquisition="wild", destination="party", result="PASS", rom_sha1=None,
                  receipt=False):
    if rom_sha1 is None:
        rom_sha1 = oracles._executed_identity(title)[0]
    duo = {"player": "a", "scenario": "link", "attempt": 1, "case": case, "title": title,
          "rom_sha1": rom_sha1, "fixture_sha256": hashlib.sha256(
              (ROOT / "tests/fixtures/gen2" / f"{case}.SaveRAM").read_bytes()).hexdigest()}
    witness = {"frame": 5000, "save_completed_frame": 4990, "gate_saves": gate_saves,
              "client_saves": client_saves,
              "cartram_sha256": sha256 or hashlib.sha256(cartram).hexdigest(),
              "cartram_bytes": len(cartram), "saveram_path": str(saveram_path),
              "saveram_bytes": len(cartram) + 22, "flushed_matches": flushed_matches}
    client = {"qualification": "PASS", "production_admitted": True, "pack": f"gen2_{title}",
             "title": title, "rom_sha1": rom_sha1}
    hello = {"frame": 3303, "ot_id": hello_ot_id}
    capture = {"frame": 4800, "site_id": site_id, "acquisition": acquisition,
              "area_id": area_id, "destination": destination, "slot": 1, "key": key,
              "species_id": species, "level": level}
    lines = [f"DUO_GEN2 {json.dumps(duo)}", f"CLIENT {json.dumps(client)}",
             f"HELLO {json.dumps(hello)}", f"SAVE_WITNESS {json.dumps(witness)}",
             f"ENGINE_CAPTURE {json.dumps(capture)}", f"CAUGHT {key}"]
    if receipt:
        rec = {"schema": "gen2-duo-link-v1", "title": title, "rom_sha1": rom_sha1, "key": key}
        lines.append(f"RECEIPT {json.dumps(rec)}")
    lines.append(f"RESULT: {result} (caught {key})" if result == "PASS" else f"RESULT: {result}")
    return "\n".join(lines)


def _duo_case(tmp_path, sides):
    """sides: {"a"/"b": (layout, fixture_path, case, title, ot_id, species)}. Builds one
    synthetic post-battle save per side plus a matching links.json; returns
    (results, data_dir, decoded) exactly like `good_case`."""
    decoded, results = {}, {}
    for inst, (side_layout, fixture_path, case, title, ot_id, species) in sides.items():
        save, mon = build_capture(side_layout, fixture_path, ot_id=ot_id, species=species)
        path = tmp_path / f"{inst}.SaveRAM"
        path.write_bytes(save)
        key = codec.key(mon)
        results[inst] = _marker_text(saveram_path=path, cartram=save[:CART], key=key,
                                     species=species, level=mon["level"], hello_ot_id=ot_id,
                                     title=title, case=case)
        decoded[inst] = {"key": key, "species": species, "level": mon["level"]}
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    links_doc = {"links": [{"area_id": "route_29", "status": "alive",
                            "a": decoded["a"], "b": decoded["b"]}]}
    (data_dir / "links.json").write_text(json.dumps(links_doc), encoding="utf-8")
    return results, str(data_dir), decoded


@pytest.fixture
def good_case(tmp_path, layout):
    sides = {"a": (layout, FIXTURES["a"], CASES["a"], "crystal", OT_IDS["a"], 16),
            "b": (layout, FIXTURES["b"], CASES["b"], "crystal", OT_IDS["b"], 19)}
    return _duo_case(tmp_path, sides)


# ---------------------------------------------------------------------------
# Positive case
# ---------------------------------------------------------------------------

def test_good_case_passes_both_stages(good_case):
    results, data_dir, _decoded = good_case
    assert oracles.check_save_witness(results) is None
    assert oracles.link_oracle(results, data_dir=data_dir) is None


# ---------------------------------------------------------------------------
# Cross-title pairing (owner ruling O-16): same oracle, no title/OT/fixture kwargs needed --
# each side derives its own from its own DUO_GEN2/CLIENT/HELLO markers.
# ---------------------------------------------------------------------------

def test_gold_silver_cross_title_link_passes(tmp_path):
    gold_layout, silver_layout = codec.for_foundation("gold"), codec.for_foundation("silver")
    sides = {"a": (gold_layout, GOLD_FIXTURE, "gold_battle", "gold", GOLD_OT, 16),
            "b": (silver_layout, SILVER_FIXTURE, "silver_battle", "silver", SILVER_OT, 19)}
    results, data_dir, _decoded = _duo_case(tmp_path, sides)
    assert oracles.check_save_witness(results) is None
    assert oracles.link_oracle(results, data_dir=data_dir) is None


def test_crystal_gold_cross_title_link_passes(tmp_path, layout):
    gold_layout = codec.for_foundation("gold")
    sides = {"a": (layout, FIXTURES["a"], CASES["a"], "crystal", OT_IDS["a"], 16),
            "b": (gold_layout, GOLD_FIXTURE, "gold_battle", "gold", GOLD_OT, 19)}
    results, data_dir, _decoded = _duo_case(tmp_path, sides)
    assert oracles.check_save_witness(results) is None
    assert oracles.link_oracle(results, data_dir=data_dir) is None


# ---------------------------------------------------------------------------
# Title-marker cross-check refusal (H2b: per-side title, never a caller argument)
# ---------------------------------------------------------------------------

def test_title_mismatch_between_duo_gen2_and_client_refused(good_case):
    """DUO_GEN2 and CLIENT disagreeing on this instance's own title must be refused, not
    silently resolved by trusting one marker over the other."""
    results, _data_dir, _decoded = good_case
    duo = oracles._last_tagged(results["a"], "DUO_GEN2")
    original_line = f"DUO_GEN2 {json.dumps(duo)}"
    duo["title"] = "gold"
    bad = dict(results)
    bad["a"] = bad["a"].replace(original_line, f"DUO_GEN2 {json.dumps(duo)}")
    with pytest.raises(RuntimeError, match="inconsistent title markers"):
        oracles.check_save_witness(bad)


def test_title_mismatch_with_receipt_refused(good_case, tmp_path, layout):
    """A RECEIPT line (PASS-only) whose title disagrees with CLIENT/DUO_GEN2 is refused too,
    even though RECEIPT is optional when absent."""
    save, mon = build_capture(layout, FIXTURES["a"], ot_id=OT_IDS["a"], species=16)
    path = tmp_path / "a.SaveRAM"
    path.write_bytes(save)
    key = codec.key(mon)
    text = _marker_text(saveram_path=path, cartram=save[:CART], key=key, species=16,
                        level=mon["level"], hello_ot_id=OT_IDS["a"], case=CASES["a"],
                        receipt=True)
    receipt = oracles._last_tagged(text, "RECEIPT")
    original_line = f"RECEIPT {json.dumps(receipt)}"
    receipt["title"] = "silver"
    text = text.replace(original_line, f"RECEIPT {json.dumps(receipt)}")
    with pytest.raises(RuntimeError, match="inconsistent title markers"):
        oracles.check_save_witness({"a": text})


# ---------------------------------------------------------------------------
# check_save_witness refusals
# ---------------------------------------------------------------------------

def test_missing_save_witness_marker_refused(good_case):
    results, _data_dir, _decoded = good_case
    bad = dict(results)
    bad["a"] = bad["a"].replace("SAVE_WITNESS ", "SAVE_WITNESS_X ")
    with pytest.raises(RuntimeError, match="missing SAVE_WITNESS"):
        oracles.check_save_witness(bad)


def test_missing_client_title_refused(good_case):
    results, _data_dir, _decoded = good_case
    bad = dict(results)
    bad["a"] = bad["a"].replace("CLIENT ", "CLIENT_X ")
    with pytest.raises(RuntimeError, match="CLIENT title"):
        oracles.check_save_witness(bad)


def test_missing_duo_gen2_marker_refused(good_case):
    results, _data_dir, _decoded = good_case
    bad = dict(results)
    bad["a"] = bad["a"].replace("DUO_GEN2 ", "DUO_GEN2_X ")
    with pytest.raises(RuntimeError, match="missing DUO_GEN2"):
        oracles.check_save_witness(bad)


def test_gate_saves_mismatch_is_torn_witness(good_case):
    results, _data_dir, _decoded = good_case
    witness = oracles._last_tagged(results["a"], "SAVE_WITNESS")
    witness["gate_saves"] = witness["client_saves"] + 1
    bad = dict(results)
    bad["a"] = bad["a"].replace(f'SAVE_WITNESS {json.dumps(oracles._last_tagged(results["a"], "SAVE_WITNESS"))}',
                                f"SAVE_WITNESS {json.dumps(witness)}")
    with pytest.raises(RuntimeError, match="torn witness"):
        oracles.check_save_witness(bad)


def test_flushed_matches_false_refused(good_case):
    results, _data_dir, _decoded = good_case
    witness = oracles._last_tagged(results["a"], "SAVE_WITNESS")
    original_line = f"SAVE_WITNESS {json.dumps(witness)}"
    witness["flushed_matches"] = False
    bad = dict(results)
    bad["a"] = bad["a"].replace(original_line, f"SAVE_WITNESS {json.dumps(witness)}")
    with pytest.raises(RuntimeError, match="flushed_matches"):
        oracles.check_save_witness(bad)


def test_missing_saveram_file_refused(good_case, tmp_path):
    results, _data_dir, _decoded = good_case
    witness = oracles._last_tagged(results["a"], "SAVE_WITNESS")
    original_line = f"SAVE_WITNESS {json.dumps(witness)}"
    witness["saveram_path"] = str(tmp_path / "does_not_exist.SaveRAM")
    bad = dict(results)
    bad["a"] = bad["a"].replace(original_line, f"SAVE_WITNESS {json.dumps(witness)}")
    with pytest.raises(RuntimeError, match="does not exist"):
        oracles.check_save_witness(bad)


def test_sha256_mismatch_refused(good_case, tmp_path):
    """The independently recomputed hash must match the marker's claim, not just be well-formed."""
    results, _data_dir, _decoded = good_case
    witness = oracles._last_tagged(results["a"], "SAVE_WITNESS")
    original_line = f"SAVE_WITNESS {json.dumps(witness)}"
    witness["cartram_sha256"] = "00" * 32
    bad = dict(results)
    bad["a"] = bad["a"].replace(original_line, f"SAVE_WITNESS {json.dumps(witness)}")
    with pytest.raises(RuntimeError, match="does not match"):
        oracles.check_save_witness(bad)


def test_short_saveram_file_refused(good_case, tmp_path):
    results, _data_dir, _decoded = good_case
    witness = oracles._last_tagged(results["a"], "SAVE_WITNESS")
    original_line = f"SAVE_WITNESS {json.dumps(witness)}"
    truncated = tmp_path / "short.SaveRAM"
    truncated.write_bytes(Path(witness["saveram_path"]).read_bytes()[:-1])
    witness["saveram_path"] = str(truncated)
    bad = dict(results)
    bad["a"] = bad["a"].replace(original_line, f"SAVE_WITNESS {json.dumps(witness)}")
    with pytest.raises(RuntimeError, match="torn save"):
        oracles.check_save_witness(bad)


def test_corrupt_checksum_refused(good_case, layout):
    """A tampered save whose reported hash matches the (also tampered) file must still fail the
    independent gen2_codec checksum/primary-backup witness."""
    results, _data_dir, _decoded = good_case
    witness = oracles._last_tagged(results["a"], "SAVE_WITNESS")
    original_line = f"SAVE_WITNESS {json.dumps(witness)}"
    path = Path(witness["saveram_path"])
    corrupt = bytearray(path.read_bytes())
    player = next(r for r in layout.regions if r.name == "player")
    corrupt[player.primary + 5] ^= 0xFF  # inside a checksummed span; checksum left stale
    path.write_bytes(bytes(corrupt))
    witness["cartram_sha256"] = hashlib.sha256(bytes(corrupt[:CART])).hexdigest()
    bad = dict(results)
    bad["a"] = bad["a"].replace(original_line, f"SAVE_WITNESS {json.dumps(witness)}")
    with pytest.raises(RuntimeError, match="checksum/primary-backup/marker witness refused"):
        oracles.check_save_witness(bad)


# ---------------------------------------------------------------------------
# link_oracle refusals
# ---------------------------------------------------------------------------

def test_wrong_area_refused(good_case):
    results, data_dir, _decoded = good_case
    capture = oracles._last_tagged(results["a"], "ENGINE_CAPTURE")
    original_line = f"ENGINE_CAPTURE {json.dumps(capture)}"
    capture["area_id"] = "route_30"
    bad = dict(results)
    bad["a"] = bad["a"].replace(original_line, f"ENGINE_CAPTURE {json.dumps(capture)}")
    with pytest.raises(RuntimeError, match="expected 'route_29'"):
        oracles.link_oracle(bad, data_dir=data_dir)


def test_wrong_site_id_refused(good_case):
    results, data_dir, _decoded = good_case
    capture = oracles._last_tagged(results["a"], "ENGINE_CAPTURE")
    original_line = f"ENGINE_CAPTURE {json.dumps(capture)}"
    capture["site_id"] = "capture_party"
    bad = dict(results)
    bad["a"] = bad["a"].replace(original_line, f"ENGINE_CAPTURE {json.dumps(capture)}")
    with pytest.raises(RuntimeError, match="capture_party_finalized"):
        oracles.link_oracle(bad, data_dir=data_dir)


def test_no_new_mon_refused(layout, tmp_path):
    """The flushed save is byte-identical to the boot fixture: nothing was caught."""
    boot = FIXTURES["a"].read_bytes()
    path = tmp_path / "a.SaveRAM"
    path.write_bytes(boot)
    party = codec.decode_saved_party(boot[:CART], layout, copy_name="primary")
    key = codec.key(party["mons"][0])
    results = {"a": _marker_text(saveram_path=path, cartram=boot[:CART], key=key,
                                 species=party["mons"][0]["species_id"],
                                 level=party["mons"][0]["level"], hello_ot_id=OT_IDS["a"],
                                 case=CASES["a"]),
              "b": ""}
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    (data_dir / "links.json").write_text("{}", encoding="utf-8")
    with pytest.raises(RuntimeError, match="expected exactly one"):
        oracles.link_oracle(results, data_dir=str(data_dir))


def test_existing_party_mon_changed_refused(layout, tmp_path):
    save, mon = build_capture(layout, FIXTURES["a"], ot_id=OT_IDS["a"], species=16)
    cart = bytearray(save[:CART])
    pokemon = next(r for r in layout.regions if r.name == "pokemon")
    # Flip the STARTER's (slot 0) DV word: species-list marker is untouched, so the record
    # stays structurally decodable, but its full identity key now differs.
    dv_off = layout.addresses["wPartyMon1"] - layout.addresses["wPokemonData"] + layout.constants["MON_DVS"]
    _poke(cart, pokemon, dv_off, bytes([0xFF, 0xFF]))
    for copy_name in ("primary", "backup"):
        checksum = codec.sav_checksum(bytes(cart), layout, copy_name)
        off = layout.checksum_offsets[copy_name]
        cart[off:off + 2] = checksum.to_bytes(2, "little")
    tail = save[CART:]
    save = bytes(cart) + tail
    path = tmp_path / "a.SaveRAM"
    path.write_bytes(save)
    key = codec.key(mon)
    results = {"a": _marker_text(saveram_path=path, cartram=save[:CART], key=key,
                                 species=mon["species_id"], level=mon["level"],
                                 hello_ot_id=OT_IDS["a"], case=CASES["a"])}
    with pytest.raises(RuntimeError, match="changed identity"):
        oracles.link_oracle(results, data_dir=str(tmp_path))


def test_ot_id_mismatch_refused(layout, tmp_path):
    save, mon = build_capture(layout, FIXTURES["a"], ot_id=1, species=16)
    path = tmp_path / "a.SaveRAM"
    path.write_bytes(save)
    key = codec.key(mon)
    results = {"a": _marker_text(saveram_path=path, cartram=save[:CART], key=key,
                                 species=mon["species_id"], level=mon["level"],
                                 hello_ot_id=OT_IDS["a"], case=CASES["a"])}
    with pytest.raises(RuntimeError, match="OT id"):
        oracles.link_oracle(results, data_dir=str(tmp_path))


def test_engine_capture_key_disagreement_refused(good_case):
    results, data_dir, _decoded = good_case
    capture = oracles._last_tagged(results["a"], "ENGINE_CAPTURE")
    original_line = f"ENGINE_CAPTURE {json.dumps(capture)}"
    capture["key"] = "0000:0000:01"
    bad = dict(results)
    bad["a"] = bad["a"].replace(original_line, f"ENGINE_CAPTURE {json.dumps(capture)}")
    with pytest.raises(RuntimeError, match="disagrees"):
        oracles.link_oracle(bad, data_dir=data_dir)


def test_unchanged_ball_pocket_refused(layout, tmp_path):
    save, mon = build_capture(layout, FIXTURES["a"], ot_id=OT_IDS["a"], species=16, ball_delta=0)
    path = tmp_path / "a.SaveRAM"
    path.write_bytes(save)
    key = codec.key(mon)
    results = {"a": _marker_text(saveram_path=path, cartram=save[:CART], key=key,
                                 species=mon["species_id"], level=mon["level"],
                                 hello_ot_id=OT_IDS["a"], case=CASES["a"])}
    with pytest.raises(RuntimeError, match="unchanged Ball pocket"):
        oracles.link_oracle(results, data_dir=str(tmp_path))


def test_identical_full_keys_refused(layout, tmp_path):
    """Both sides somehow produce the exact same DV:OT:species key."""
    results = {}
    for inst in ("a", "b"):
        save, mon = build_capture(layout, FIXTURES[inst], ot_id=55, species=16, dv_word=0xABCD)
        path = tmp_path / f"{inst}.SaveRAM"
        path.write_bytes(save)
        key = codec.key(mon)
        results[inst] = _marker_text(saveram_path=path, cartram=save[:CART], key=key,
                                     species=16, level=mon["level"], hello_ot_id=55,
                                     case=CASES[inst])
    with pytest.raises(RuntimeError, match="identical full key"):
        oracles.link_oracle(results, data_dir=str(tmp_path), ot_ids={"a": 55, "b": 55})


def test_link_oracle_flushed_checksum_refused(good_case, layout):
    """H2b carry: link_oracle independently checksum-witnesses the flushed (post-save) image
    itself -- it must not rely on check_save_witness having run first. Gold/Silver rewrite
    sWindowStack/sScratch in SRAM after a flush, so this has to be a real, separate check on
    the same post-save bytes link_oracle decodes, using gen2_codec's real checksum spans."""
    results, data_dir, _decoded = good_case
    witness = oracles._last_tagged(results["a"], "SAVE_WITNESS")
    path = Path(witness["saveram_path"])
    corrupt = bytearray(path.read_bytes())
    player = next(r for r in layout.regions if r.name == "player")
    corrupt[player.primary + 5] ^= 0xFF  # primary copy only: breaks copies_agree, stale checksum
    path.write_bytes(bytes(corrupt))
    with pytest.raises(RuntimeError, match="checksum/primary-backup/marker witness"):
        oracles.link_oracle(results, data_dir=data_dir)


def test_link_oracle_boot_fixture_checksum_refused(good_case, tmp_path, layout):
    """The same independent checksum witness also covers the boot fixture link_oracle reads
    (whether derived from DUO_GEN2.case or overridden via boot_saveram)."""
    results, _data_dir, decoded = good_case
    boot = bytearray(FIXTURES["a"].read_bytes())
    player = next(r for r in layout.regions if r.name == "player")
    boot[player.primary + 5] ^= 0xFF
    bad_boot = tmp_path / "bad_boot.SaveRAM"
    bad_boot.write_bytes(bytes(boot))
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    links_doc = {"links": [{"area_id": "route_29", "status": "alive",
                            "a": decoded["a"], "b": decoded["b"]}]}
    (data_dir / "links.json").write_text(json.dumps(links_doc), encoding="utf-8")
    with pytest.raises(RuntimeError, match="checksum/primary-backup/marker witness"):
        oracles.link_oracle(results, data_dir=str(data_dir), boot_saveram={"a": str(bad_boot)})


@pytest.fixture
def admission_case(tmp_path):
    layout = codec.for_foundation("crystal")
    pin = layout.profile["titles"]["crystal"]["rom_sha1"]
    wrong = "f2f52230b536214ef7c9924f483392993e226cfb"
    boot = {side: FIXTURES[side] for side in ("a", "b")}
    paths = {}
    for side in boot:
        paths[side] = tmp_path / f"{side}.SaveRAM"
        paths[side].write_bytes(boot[side].read_bytes())
    digest = hashlib.sha256(paths["b"].read_bytes()[:CART]).hexdigest()
    head = {"player": "a", "scenario": "gen2_admit_wrong_rom", "attempt": 1,
            "title": "crystal", "rom_sha1": pin, "case": "crystal_battle",
            "fixture_sha256": hashlib.sha256(boot["a"].read_bytes()).hexdigest()}
    client = {"production_admitted": True, "title": "crystal", "rom_sha1": pin}
    witness = {"frame": 5000, "save_completed_frame": 4990, "gate_saves": 1,
               "client_saves": 1, "cartram_sha256": hashlib.sha256(paths["a"].read_bytes()[:CART]).hexdigest(),
               "cartram_bytes": CART, "saveram_bytes": CART + 22, "saveram_path": str(paths["a"]),
               "flushed_matches": True}
    refused = {"player": "b", "scenario": head["scenario"], "attempt": 1, "title": "crystal",
               "rom_sha1": wrong, "expect_admission": "refused"}
    refusal = {"frame": 1, "rom_sha1": wrong, "client": False,
               "console": "[gen2] refused (production admission): ROM sha1"}
    receipt = {**head, "schema": "gen2-duo-admit-wrong-rom-v1", "expect_admission": "admitted"}
    def lines(rows):
        return "\n".join(f"{tag} {json.dumps(value)}" for tag, value in rows) + "\nRESULT: PASS"
    results = {
        "a": lines([("DUO_GEN2", head), ("CLIENT", client), ("BOOTED", {"frame": 10}),
                    ("HELLO", {"frame": 20, "ot_id": OT_IDS["a"]}),
                    ("HOLD", {"frame": 620, "frames": 600, "hellos": 1}),
                    ("SAVE_WITNESS", witness), ("RECEIPT", receipt)]),
        "b": lines([("DUO_GEN2", refused), ("ADMISSION_REFUSED", refusal),
                    ("NO_TRAFFIC", {"frame": 620, "frames": 600, "tx": 0}),
                    ("CARTRAM_UNCHANGED", {"before": digest, "after": digest}),
                    ("RECEIPT", {**refused, "schema": receipt["schema"], "refusal": refusal,
                                 "cartram_sha256": digest})])}
    doc = {"links": [], "player_identity": {"a": {"ot_id": str(OT_IDS["a"])}}, "pending_captures": {}}
    events = [{"type": "hello", "player": "a", "text": "Connected (Crystal)"}]
    snapshot = {"links": doc, "events": events,
                "status": {"players": {"a": {"connected": True, "admission": "admitted"}, "b": {}}},
                "raw": {"_live": {"connected_players": {"a": {"rom_type": "Crystal"}}}}}
    (tmp_path / "links.json").write_text(json.dumps(doc))
    (tmp_path / "events.json").write_text(json.dumps(events))
    return results, {"data_dir": str(tmp_path), "before": deepcopy(snapshot), "after": deepcopy(snapshot),
                     "boot_saveram": boot, "refused_saveram": paths["b"]}


def test_wrong_rom_admission_independent_evidence(admission_case):
    results, kwargs = admission_case
    facts = []
    oracles.admit_wrong_rom_oracle(results, **kwargs, on_verified=facts.append)
    assert facts[0]["a"] == "admitted" and facts[0]["b"] == "refused"
    assert facts[0]["status"] == "refused"


def test_wrong_rom_admission_accepts_terminal_disconnect(admission_case):
    results, kwargs = admission_case
    kwargs["after"]["status"]["players"]["a"]["connected"] = False
    kwargs["after"]["raw"]["_live"]["connected_players"]["a"]["connected"] = False
    oracles.admit_wrong_rom_oracle(results, **kwargs)


@pytest.mark.parametrize("fault", ("never_connected", "admission_lost", "identity_error"))
def test_wrong_rom_terminal_disconnect_keeps_admission_requirements(admission_case, fault):
    results, kwargs = admission_case
    kwargs["after"]["status"]["players"]["a"]["connected"] = False
    if fault == "never_connected":
        kwargs["before"]["status"]["players"]["a"]["connected"] = False
    elif fault == "admission_lost":
        kwargs["after"]["status"]["players"]["a"]["admission"] = "refused"
    else:
        kwargs["after"]["status"]["players"]["a"]["identity_error"] = "wrong save"
    with pytest.raises(RuntimeError, match="admitted public status"):
        oracles.admit_wrong_rom_oracle(results, **kwargs)


@pytest.mark.parametrize("fault", ["pin", "role", "missing", "client", "tx", "short_hold", "digest",
    "disk", "hello_again", "identity", "player", "event", "links", "admitted_party", "fixture_hash"])
def test_wrong_rom_admission_refuses_mutations(admission_case, fault):
    results, kwargs = admission_case
    if fault in ("pin", "role", "fixture_hash"):
        side = "a" if fault == "fixture_hash" else "b"
        row = oracles._last_tagged(results[side], "DUO_GEN2")
        row[{"pin": "rom_sha1", "role": "expect_admission", "fixture_hash": "fixture_sha256"}[fault]] = (
            "admitted" if fault == "role" else "0" * (64 if fault == "fixture_hash" else 40))
        results[side] = _replace_tag(results[side], "DUO_GEN2", row)
    elif fault == "missing":
        results["b"] = "\n".join(line for line in results["b"].splitlines() if not line.startswith("ADMISSION_REFUSED "))
    elif fault in ("client", "tx", "hello_again"):
        side, tag = ("a", "HELLO_AGAIN") if fault == "hello_again" else ("b", fault.upper())
        results[side] += f"\n{tag} {{}}"
    elif fault == "short_hold":
        row = oracles._last_tagged(results["b"], "NO_TRAFFIC")
        row["frames"] = 1
        results["b"] = _replace_tag(results["b"], "NO_TRAFFIC", row)
    elif fault == "digest":
        results["b"] = _replace_tag(results["b"], "CARTRAM_UNCHANGED", {"before": "0" * 64, "after": "0" * 64})
    elif fault == "disk":
        path = Path(kwargs["refused_saveram"])
        raw = bytearray(path.read_bytes())
        raw[0] ^= 1
        path.write_bytes(raw)
    elif fault == "identity":
        kwargs["after"]["links"]["player_identity"]["b"] = {"ot_id": "7"}
    elif fault == "player":
        kwargs["after"]["raw"]["_live"]["connected_players"]["b"] = {}
    elif fault == "event":
        kwargs["after"]["events"].append({"type": "hello", "player": "b"})
    elif fault == "links":
        kwargs["after"]["links"]["links"].append({"area_id": "route_29"})
    else:
        witness = oracles._last_tagged(results["a"], "SAVE_WITNESS")
        Path(witness["saveram_path"]).write_bytes(build_capture(codec.for_foundation("crystal"),
            FIXTURES["a"], ot_id=OT_IDS["a"], species=16)[0])
        witness["cartram_sha256"] = hashlib.sha256(Path(witness["saveram_path"]).read_bytes()[:CART]).hexdigest()
        results["a"] = _replace_tag(results["a"], "SAVE_WITNESS", witness)
    with pytest.raises(RuntimeError):
        oracles.admit_wrong_rom_oracle(results, **kwargs)


@pytest.fixture(scope="module")
def reconnect_qualification_cache():
    from functools import lru_cache

    from tests.live.test_gen2_new_gates import qualified_identity

    return lru_cache(maxsize=8)(qualified_identity)


@pytest.fixture
def reconnect_case(good_case, tmp_path, monkeypatch, reconnect_qualification_cache):
    from tests.live import test_gen2_new_gates

    monkeypatch.setattr(test_gen2_new_gates, "qualified_identity", reconnect_qualification_cache)
    initial, data_dir, decoded = good_case
    pin = codec.for_foundation("crystal").profile["titles"]["crystal"]["rom_sha1"]
    for side in ("a", "b"):
        initial[side] = "\n".join(line for line in initial[side].splitlines() if not line.startswith("RESULT:"))
        for tag in ("DUO_GEN2", "CLIENT"):
            row = oracles._last_tagged(initial[side], tag)
            row.update(rom_sha1=pin)
            if tag == "DUO_GEN2":
                row.update(player=side, scenario="gen2_reconnect")
            initial[side] = _replace_tag(initial[side], tag, row)
        initial[side] = _replace_tag(initial[side], "RECONNECT_READY",
            {"frame": 5100, "phase": "initial", "player": side, "key": decoded[side]["key"]})
    head = oracles._last_tagged(initial["b"], "DUO_GEN2")
    initial["b"] = _replace_tag(initial["b"], "B_STAYED", {"frame": 10000, "hellos": 1, "force_faint": 0, "box_mon": 0})
    initial["b"] = _replace_tag(initial["b"], "RECEIPT", {**head, "schema": "gen2-duo-reconnect-v1", "phase": "initial"})
    initial["b"] += "\nRESULT: PASS"
    initial_path = Path(oracles._last_tagged(initial["a"], "SAVE_WITNESS")["saveram_path"])
    staged, final, texts = {}, {}, {}
    for phase in ("same_save", "wrong_save"):
        seed = initial_path.read_bytes() if phase == "same_save" else FIXTURES["b"].read_bytes()
        staged[phase] = tmp_path / f"{phase}-seed.SaveRAM"
        final[phase] = tmp_path / f"{phase}-final.SaveRAM"
        staged[phase].write_bytes(seed)
        final[phase].write_bytes(seed)
        ot = OT_IDS["a"] if phase == "same_save" else OT_IDS["b"]
        head = {"player": "a", "scenario": "gen2_reconnect", "attempt": 2 if phase == "same_save" else 3,
                "case": "crystal_battle", "title": "crystal", "rom_sha1": pin,
                "fixture_sha256": hashlib.sha256(seed).hexdigest()}
        back = {"frame": 3500, "phase": phase, "hellos": 1, "ot_id": ot,
                "expected_key": decoded["a"]["key"], "linked": phase == "same_save"}
        rows = [("DUO_GEN2", head), ("CLIENT", {"title": "crystal", "rom_sha1": pin, "production_admitted": True}),
                ("BOOTED", {"frame": 3000}), ("HELLO", {"frame": 3300, "ot_id": ot}), ("RECONNECT_HELLO", back)]
        if phase == "wrong_save":
            rows.extend([("RX_TEXT", {"frame": 3510, "cmd": "hud_show", "text": "[x] WRONG SAVE: slot A"}),
                         ("WRONG_SAVE_HUD", {"frame": 3511, "text": "[x] WRONG SAVE: slot A"})])
        rows.append(("RECEIPT", {**head, **back, "schema": "gen2-duo-reconnect-v1"}))
        texts[phase] = "\n".join(f"{tag} {json.dumps(value)}" for tag, value in rows) + "\nRESULT: PASS"
    doc = json.loads((Path(data_dir) / "links.json").read_text())
    doc.update(player_identity={side: {"ot_id": str(OT_IDS[side])} for side in ("a", "b")}, pending_captures={})
    events = [{"type": "hello", "player": side, "text": "Connected (Crystal)"} for side in ("a", "b")]
    players = {side: {"connected": True, "party_keys": [decoded[side]["key"]], "identity_error": ""} for side in ("a", "b")}
    snapshots = {"initial": {"links": deepcopy(doc), "events": deepcopy(events), "status": {"players": deepcopy(players)}}}
    snapshots["disconnected"] = deepcopy(snapshots["initial"])
    snapshots["disconnected"]["status"]["players"]["a"]["connected"] = False
    events.insert(0, {"type": "hello", "player": "a", "text": "Connected (Crystal)"})
    snapshots["same_save"] = {"links": deepcopy(doc), "events": deepcopy(events), "status": {"players": deepcopy(players)}}
    snapshots["before_wrong"] = deepcopy(snapshots["same_save"])
    snapshots["before_wrong"]["status"]["players"]["a"]["connected"] = False
    events.insert(0, {"type": "hello", "player": "a", "text": "REJECTED — wrong save/slot"})
    players["a"]["identity_error"] = "Identity mismatch for slot A"
    snapshots["wrong_save"] = {"links": deepcopy(doc), "events": deepcopy(events), "status": {"players": deepcopy(players)}}
    (Path(data_dir) / "links.json").write_text(json.dumps(doc))
    (Path(data_dir) / "events.json").write_text(json.dumps(events))
    return {"a": texts["wrong_save"], "b": initial["b"]}, {"data_dir": data_dir, "initial_results": initial,
        "relaunch_results": texts, "boot_saveram": FIXTURES, "staged_saves": staged,
        "relaunch_saves": final, "snapshots": snapshots}


def test_reconnect_independent_three_phase_evidence(reconnect_case):
    results, kwargs = reconnect_case
    facts = []
    oracles.reconnect_oracle(results, **kwargs, on_verified=facts.append)
    assert facts[0]["status"] == "alive" and facts[0]["a"] != facts[0]["b"]


def _advance_backup_clock(raw, layout, symbol="wGameTimeFrames", byte=0):
    raw = bytearray(raw)
    region, offset = _region_and_offset(layout, symbol)
    raw[region.backup + offset + byte] += 5
    at = layout.checksum_offsets["backup"]
    raw[at:at + 2] = codec.sav_checksum(bytes(raw[:CART]), layout, "backup").to_bytes(2, "little")
    return bytes(raw)


@pytest.mark.parametrize("phase", ["same_save", "wrong_save"])
def test_reconnect_native_continue_clock_difference_is_allowed(reconnect_case, phase):
    results, kwargs = reconnect_case
    layout = codec.for_foundation("crystal")
    path = kwargs["relaunch_saves"][phase]
    path.write_bytes(_advance_backup_clock(path.read_bytes(), layout))
    report = codec.strict_checksum_witness(path.read_bytes()[:CART], layout)
    assert report["primary"]["checksum_valid"] and report["backup"]["checksum_valid"] and not report["copies_agree"]
    oracles.reconnect_oracle(results, **kwargs)


@pytest.mark.parametrize("title", ["crystal", "gold", "silver"])
@pytest.mark.parametrize("symbol,byte", [("wGameTimeHours", 0), ("wGameTimeHours", 1), ("wGameTimeMinutes", 0),
                                       ("wGameTimeSeconds", 0), ("wGameTimeFrames", 0)])
def test_relaunch_clock_witness_maps_each_source_field(title, symbol, byte):
    layout = codec.for_foundation(title)
    raw = (ROOT / f"tests/fixtures/gen2/{title}_battle.SaveRAM").read_bytes()
    changed = _advance_backup_clock(raw, layout, symbol, byte)
    assert not codec.strict_checksum_witness(changed[:CART], layout)["valid"]
    assert oracles._relaunch_checksum_witness(changed[:CART], layout)


@pytest.mark.parametrize("title", ["crystal", "gold", "silver"])
@pytest.mark.parametrize("fault", ["checksum", "marker", "wGameTimeCap", "wPlayerID", "wCurMapData", "wPartyMon1", "after_clock"])
def test_relaunch_clock_witness_refuses_other_copy_corruption(title, fault):
    layout = codec.for_foundation(title)
    raw = bytearray((ROOT / f"tests/fixtures/gen2/{title}_battle.SaveRAM").read_bytes()[:CART])
    if fault == "checksum":
        raw[layout.checksum_offsets["backup"]] ^= 1
    elif fault == "marker":
        raw[layout.markers["backup"][0][0]] ^= 1
    else:
        symbol = "wGameTimeFrames" if fault == "after_clock" else fault
        region, offset = _region_and_offset(layout, symbol)
        raw[region.backup + offset + (fault == "after_clock")] ^= 1
        at = layout.checksum_offsets["backup"]
        raw[at:at + 2] = codec.sav_checksum(bytes(raw), layout, "backup").to_bytes(2, "little")
    assert not oracles._relaunch_checksum_witness(bytes(raw), layout)


def test_ordinary_save_witness_still_refuses_clock_copy_difference(good_case):
    results, _, _ = good_case
    layout = codec.for_foundation("crystal")
    witness = oracles._last_tagged(results["a"], "SAVE_WITNESS")
    path = Path(witness["saveram_path"])
    path.write_bytes(_advance_backup_clock(path.read_bytes(), layout))
    _refresh_faint_hash(results, "a")
    with pytest.raises(RuntimeError, match="independent checksum"):
        oracles.check_save_witness(results)


@pytest.mark.parametrize("fault", ["same_hash", "wrong_hash", "same_seed", "wrong_seed", "same_final",
    "wrong_final", "same_witness", "wrong_witness", "same_hello", "wrong_hud", "b_faint", "not_killed",
    "link_changed", "identity_changed", "duplicate_capture", "wrong_accepted", "b_disconnected", "result_swap",
    "initial_duplicate_hello", "rule_state"])
def test_reconnect_refuses_mutations(reconnect_case, fault):
    results, kwargs = reconnect_case
    phase = "wrong_save" if fault.startswith("wrong") else "same_save"
    texts, snapshots = kwargs["relaunch_results"], kwargs["snapshots"]
    if fault.endswith("hash"):
        head = oracles._last_tagged(texts[phase], "DUO_GEN2")
        head["fixture_sha256"] = "0" * 64
        texts[phase] = _replace_tag(texts[phase], "DUO_GEN2", head)
    elif fault.endswith("seed") or fault.endswith("final"):
        mapping = kwargs["staged_saves"] if fault.endswith("seed") else kwargs["relaunch_saves"]
        mapping[phase].write_bytes(FIXTURES["a"].read_bytes())
    elif fault.endswith("witness"):
        texts[phase] += "\nSAVE_WITNESS {}"
    elif fault == "same_hello":
        texts[phase] += "\nHELLO_AGAIN {}"
    elif fault == "wrong_hud":
        texts[phase] = texts[phase].replace("WRONG SAVE", "RIGHT SAVE")
    elif fault == "b_faint":
        kwargs["initial_results"]["b"] += "\nRX force_faint key=bad"
        results["b"] = kwargs["initial_results"]["b"]
    elif fault == "not_killed":
        snapshots["disconnected"]["status"]["players"]["a"]["connected"] = True
    elif fault == "link_changed":
        snapshots["same_save"]["links"]["links"][0]["status"] = "dead"
    elif fault == "identity_changed":
        snapshots["same_save"]["links"]["player_identity"]["a"]["ot_id"] = "7"
    elif fault == "duplicate_capture":
        snapshots["same_save"]["events"].insert(0, {"type": "capture", "player": "a"})
    elif fault == "wrong_accepted":
        snapshots["wrong_save"]["status"]["players"]["a"]["identity_error"] = ""
    elif fault == "b_disconnected":
        snapshots["same_save"]["status"]["players"]["b"]["connected"] = False
    elif fault == "initial_duplicate_hello":
        for snapshot in snapshots.values():
            snapshot["events"].append({"type": "hello", "player": "b", "text": "Connected (Crystal)"})
    elif fault == "rule_state":
        snapshots["same_save"]["links"]["run_over"] = True
    else:
        results["a"] = texts["same_save"]
    if fault.startswith("wrong") and fault != "wrong_accepted":
        results["a"] = texts["wrong_save"]
    with pytest.raises(RuntimeError):
        oracles.reconnect_oracle(results, **kwargs)


@pytest.fixture
def soft_reset_case(tmp_path, request):
    from tools.gen2_fixtures import _saved_field

    titles = getattr(request, "param", ("crystal", "crystal"))
    fixtures, ots = {}, {}
    results = {}
    for side, title in zip(("a", "b"), titles, strict=True):
        case = "crystal_battle_ot2" if side == "b" and titles == ("crystal", "crystal") else f"{title}_battle"
        fixtures[side] = ROOT / f"tests/fixtures/gen2/{case}.SaveRAM"
        layout = codec.for_foundation(title)
        pin = layout.profile["titles"][title]["rom_sha1"]
        ots[side] = int.from_bytes(_saved_field(fixtures[side].read_bytes(), layout, "wPlayerID", 2), "big")
        path = tmp_path / f"{side}.SaveRAM"
        path.write_bytes(fixtures[side].read_bytes())
        head = {"player": side, "scenario": "gen2_soft_reset", "attempt": 1,
                "title": title, "rom_sha1": pin, "case": case,
                "fixture_sha256": hashlib.sha256(fixtures[side].read_bytes()).hexdigest()}
        client = {"title": title, "rom_sha1": pin, "production_admitted": True}
        boot, hello = {"frame": 100}, {"frame": 110, "ot_id": ots[side]}
        save = {"frame": 5000, "save_completed_frame": 4990, "gate_saves": 1, "client_saves": 1,
                "cartram_bytes": CART, "saveram_bytes": CART + 22, "flushed_matches": True,
                "cartram_sha256": hashlib.sha256(path.read_bytes()[:CART]).hexdigest(), "saveram_path": str(path)}
        rows = [("DUO_GEN2", head), ("CLIENT", client), ("BOOTED", boot), ("HELLO", hello)]
        receipt = {**head, "schema": "gen2-duo-soft-reset-v1", "booted": boot, "hello": hello,
                   "save": save, "client": client, "input_mode": "normal_buttons", "harness_write_scopes": []}
        if side == "a":
            reset, paused = {"frame": 233, "delta": 33}, {"frame": 473, "delta": 240}
            rehello = {"frame": 1001, "ot_id": ots[side], "hellos": 2}
            rows.extend([("HELLO_AT_CHECKPOINT", {"frame": 150, "ot_id": ots[side], "hellos": 1, "writes_enabled": True}),
                         ("CHORD_GATE", {"frame": 199}), ("CHORD", {"frame": 200, "frames": 4}),
                         ("RESET_SEEN", reset), ("HELLO_CLEARED", {"frame": 240, "delta": 7}),
                         ("WRITES_PAUSED", paused), ("REBOOTED", {"frame": 900}),
                         ("HELLO_AGAIN", {"frame": 999, "ot_id": ots[side], "n": 2}),
                         ("WRITES_RESUMED", {"frame": 1000, "delta": 767}), ("REHELLO", rehello),
                         ("NO_WRITES_IN_WINDOW", {"writes": 0})])
            receipt.update(reset=reset, paused=paused, rehello=rehello)
        else:
            idle = {"frame": 2000, "hellos": 1}
            rows.append(("IDLE_PARTNER", idle))
            receipt.update(idle=idle)
        rows.extend([("SAVE_WITNESS", save), ("RECEIPT", receipt)])
        results[side] = "\n".join(f"{tag} {json.dumps(value)}" for tag, value in rows) + "\nRESULT: PASS"
    links = {"links": [], "player_identity": {side: {"ot_id": str(ots[side])} for side in ("a", "b")}, "pending_captures": {}}
    events = [{"type": "hello", "player": side, "text": "Connected (Crystal)"} for side in ("a", "b")]
    before = {"links": deepcopy(links), "events": deepcopy(events), "status": {"players": {
        side: {"connected": True, "identity_error": ""} for side in ("a", "b")}}}
    before["links_bytes"] = json.dumps(links).encode()
    after = deepcopy(before)
    after["events"].insert(0, {"type": "hello", "player": "a", "text": "Connected (Crystal)"})
    (tmp_path / "links.json").write_text(json.dumps(links))
    (tmp_path / "events.json").write_text(json.dumps(after["events"]))
    return results, {"data_dir": str(tmp_path), "before": before, "after": after, "boot_saveram": fixtures}


@pytest.mark.parametrize("soft_reset_case", [("crystal", "crystal"), ("gold", "silver"), ("crystal", "gold")], indirect=True)
def test_soft_reset_independent_evidence(soft_reset_case):
    results, kwargs = soft_reset_case
    facts = []
    oracles.soft_reset_oracle(results, **kwargs, on_verified=facts.append)
    titles = "/".join(oracles._last_tagged(results[side], "DUO_GEN2")["title"] for side in ("a", "b"))
    assert facts == [{"a": "reset", "b": "idle", "area": "none", "titles": titles, "status": "unchanged"}]


def test_soft_reset_after_snapshot_may_follow_normal_driver_exit(soft_reset_case):
    results, kwargs = soft_reset_case
    for side in ("a", "b"):
        kwargs["after"]["status"]["players"][side]["connected"] = False
    oracles.soft_reset_oracle(results, **kwargs)


@pytest.mark.parametrize("fault", ["reset_delta", "clear_delta", "pause_delta", "resume_delta", "late_clear",
    "early_pause", "wrong_ot", "no_pause", "writes", "write_row", "b_reset", "capture", "fixture_hash",
    "party", "identity", "rule_state", "extra_hello", "rejected_hello", "b_disconnect", "receipt", "links_bytes"])
def test_soft_reset_refuses_mutations(soft_reset_case, fault):
    results, kwargs = soft_reset_case
    if fault.endswith("delta") or fault in ("late_clear", "early_pause"):
        tag = {"reset_delta": "RESET_SEEN", "clear_delta": "HELLO_CLEARED", "pause_delta": "WRITES_PAUSED",
               "resume_delta": "WRITES_RESUMED", "late_clear": "HELLO_CLEARED", "early_pause": "WRITES_PAUSED"}[fault]
        row = oracles._last_tagged(results["a"], tag)
        if fault.endswith("delta"):
            row["delta"] += 1
        else:
            row.update(frame=500 if fault == "late_clear" else 250, delta=267 if fault == "late_clear" else 17)
        results["a"] = _replace_tag(results["a"], tag, row)
    elif fault in ("wrong_ot", "writes", "fixture_hash", "receipt"):
        tag, field, value = {"wrong_ot": ("HELLO_AGAIN", "ot_id", 1), "writes": ("NO_WRITES_IN_WINDOW", "writes", 1),
                             "fixture_hash": ("DUO_GEN2", "fixture_sha256", "0" * 64),
                             "receipt": ("RECEIPT", "harness_write_scopes", ["party"])}[fault]
        row = oracles._last_tagged(results["a"], tag)
        row[field] = value
        results["a"] = _replace_tag(results["a"], tag, row)
    elif fault == "no_pause":
        results["a"] = "\n".join(line for line in results["a"].splitlines() if not line.startswith("WRITES_PAUSED "))
    elif fault in ("write_row", "b_reset", "capture"):
        side, tag = {"write_row": ("a", "PARTY_HP_WRITE"), "b_reset": ("b", "HELLO_AGAIN"), "capture": ("a", "ENGINE_CAPTURE")}[fault]
        results[side] += f"\n{tag} {{}}"
    elif fault == "party":
        save = oracles._last_tagged(results["a"], "SAVE_WITNESS")
        raw = build_capture(codec.for_foundation("crystal"), FIXTURES["a"], ot_id=OT_IDS["a"], species=16)[0]
        Path(save["saveram_path"]).write_bytes(raw)
        save["cartram_sha256"] = hashlib.sha256(raw[:CART]).hexdigest()
        results["a"] = _replace_tag(results["a"], "SAVE_WITNESS", save)
    elif fault == "identity":
        kwargs["after"]["links"]["player_identity"]["a"]["ot_id"] = "1"
    elif fault == "rule_state":
        kwargs["after"]["links"]["run_over"] = True
    elif fault == "extra_hello":
        kwargs["after"]["events"].insert(0, {"type": "hello", "player": "b", "text": "Connected (Crystal)"})
    elif fault == "rejected_hello":
        kwargs["after"]["events"][0]["text"] = "REJECTED — wrong save/slot"
    elif fault == "links_bytes":
        kwargs["after"]["links_bytes"] += b" "
    else:
        kwargs["before"]["status"]["players"]["b"]["connected"] = False
    with pytest.raises(RuntimeError):
        oracles.soft_reset_oracle(results, **kwargs)


def _clause_base(tmp_path, species_pair=(16, 19)):
    layout = codec.for_foundation("crystal")
    base = _duo_case(tmp_path, {side: (layout, FIXTURES[side], CASES[side], "crystal", OT_IDS[side], species)
                               for side, species in zip(("a", "b"), species_pair, strict=True)})
    results, data_dir, decoded = base
    for side in ("a", "b"):
        save = oracles._last_tagged(results[side], "SAVE_WITNESS")
        _edit_saved_record(Path(save["saveram_path"]), layout, 1, layout.constants["MON_LEVEL"], bytes([3]))
        _refresh_faint_hash(results, side)
        cap = oracles._last_tagged(results[side], "ENGINE_CAPTURE")
        cap["level"] = decoded[side]["level"] = 3
        results[side] = _replace_tag(results[side], "ENGINE_CAPTURE", cap)
    (Path(data_dir) / "links.json").write_text(json.dumps({"links": [{"area_id": "route_29", "status": "alive", **decoded}]}))
    return base


@pytest.fixture
def clause_case(tmp_path):
    layout = codec.for_foundation("crystal")
    results, data_dir, images = _make_faint_case(_clause_base(tmp_path), {"a": layout, "b": layout}, tmp_path)
    keys, captures = {}, {}
    for side in ("a", "b"):
        cap = oracles._last_tagged(results[side], "ENGINE_CAPTURE")
        captures[side], keys[side] = cap, cap["key"]
        head = oracles._last_tagged(results[side], "DUO_GEN2")
        head["scenario"] = "gen2_type_clause"
        save = oracles._last_tagged(results[side], "SAVE_WITNESS")
        if side == "a":
            Path(save["saveram_path"]).write_bytes(images[side].read_bytes())
            save["cartram_sha256"] = hashlib.sha256(images[side].read_bytes()[:CART]).hexdigest()
        rows = [("DUO_GEN2", head), ("CLIENT", oracles._last_tagged(results[side], "CLIENT")),
                ("HELLO", oracles._last_tagged(results[side], "HELLO")), ("ENGINE_CAPTURE", cap),
                ("CAPTURE_SENT", {"frame": 4801, "key": cap["key"], "seq": 8}),
                ("CLAUSE_CAPTURE", {"frame": 5000, "key": cap["key"], "species_id": cap["species_id"], "area_id": "route_29",
                                    "types": ["Normal", "Flying"] if side == "a" else ["Normal"],
                                    "gender": "female" if int(cap["key"][0], 16) <= 7 else "male"})]
        text = "\n".join(f"{tag} {json.dumps(row)}" for tag, row in rows) + f"\nCAUGHT {cap['key']}"
        verdict = "partner_rejected" if side == "a" else "rejected"
        if side == "a":
            text += "\nRX play_sound sound=22"
        else:
            text += f"\nRX force_faint key={cap['key']}\nRX memorialize key={cap['key']}\nRX play_sound sound=26\nRX unresolve_area area_id=route_29"
            text += "\nRX_TEXT " + json.dumps({"frame": 7000, "cmd": "gui_prompt", "text": "[x] Type clause: shared Normal"})
        text += "\nCLAUSE_VERDICT " + json.dumps({"frame": 7001, "verdict": verdict})
        if side == "b":
            text += "\nPARTY_HP_WRITE " + json.dumps(oracles._last_tagged(results[side], "PARTY_HP_WRITE"))
            text += "\nMEMORIAL_ACK " + json.dumps({"frame": 7200, "event": "memorialize_failed", "key": cap["key"], "reason": "unsupported"})
            text += "\nREJECTED_MON " + json.dumps({"frame": 7300, "key": cap["key"], "ending": "dead", "in_party": True, "slot": 1, "hp": 0, "status": 0})
        text += "\nSAVE_WITNESS " + json.dumps(save)
        text += "\nRECEIPT " + json.dumps({**head, "schema": "gen2-duo-type-clause-v1", "clause": "type", "verdict": verdict,
            "path": "clause_observed", "ending": "dead" if side == "b" else None}) + "\nRESULT: PASS"
        results[side] = text
    doc = {"links": [], "pending_captures": {"route_29": {"a": {"key": keys["a"], "species": captures["a"]["species_id"]}}},
           "area_states": {"route_29": "pending_b"}, "retry_areas": {"a": [], "b": ["route_29"]}, "pending_memorials": {"a": [], "b": []}}
    (Path(data_dir) / "links.json").write_text(json.dumps(doc))
    events = [{"type": "violation", "player": "b", "area_id": "route_29", "text": "[x] Type clause: shared Normal"},
              *[{"type": "capture", "player": side, "key": keys[side], "area_id": "route_29"} for side in ("b", "a")]]
    (Path(data_dir) / "events.json").write_text(json.dumps(events))
    return results, {"kind": "type", "data_dir": data_dir, "boot_saveram": FIXTURES}


def test_clause_observed_independent_dead_pending_proof(clause_case):
    results, kwargs = clause_case
    facts = []
    oracles.clause_oracle(results, **kwargs, on_verified=facts.append)
    assert facts[0]["ending"] == "dead" and facts[0]["rejected"] == "b"


@pytest.mark.parametrize("box_number", [13, 12])
def test_clause_memorial_requires_saved_final_box(clause_case, box_number):
    results, kwargs = clause_case
    layout = codec.for_foundation("crystal")
    save = oracles._last_tagged(results["b"], "SAVE_WITNESS")
    path = Path(save["saveram_path"])
    raw = bytearray(path.read_bytes())
    party = codec.decode_saved_party(bytes(raw[:CART]), layout, copy_name="primary")["mons"]
    mon = dict(party[-1])
    preimage = {field: mon[field] for field in ("raw_hex", "ot_raw_hex", "nickname_raw_hex", "species_marker")}
    preimage.update(frame=7180, key=codec.key(mon), slot=len(party) - 1)
    results["b"] = results["b"].replace("MEMORIAL_ACK ", "MEMORIAL_PREIMAGE " + json.dumps(preimage) + "\nMEMORIAL_ACK ")
    mon["raw_hex"] = mon["raw_hex"][:layout.box_mon_size * 2]
    box = codec.verify_boxes(bytes(raw[:CART]), layout)[box_number]
    box.update(count=1, mons=[mon])
    start, length = layout.storage_boxes[box_number]
    raw[start:start + length] = codec.encode_box(box, layout)
    region = next(r for r in layout.regions if r.name == "pokemon")
    _poke(raw, region, layout.addresses["wPartyCount"] - layout.addresses["wPokemonData"], bytes([1]))
    _poke(raw, region, layout.addresses["wPartySpecies"] - layout.addresses["wPokemonData"] + 1, bytes([255]))
    for copy_name in ("primary", "backup"):
        offset = layout.checksum_offsets[copy_name]
        raw[offset:offset + 2] = codec.sav_checksum(bytes(raw[:CART]), layout, copy_name).to_bytes(2, "little")
    path.write_bytes(raw)
    _refresh_faint_hash(results, "b")
    for tag in ("MEMORIAL_ACK", "REJECTED_MON", "RECEIPT"):
        row = oracles._last_tagged(results["b"], tag)
        if tag == "MEMORIAL_ACK":
            row.update(event="memorialize_done", box=13)
        elif tag == "REJECTED_MON":
            row.update(ending="memorial", box=13, in_party=False)
        else:
            row.update(ending="memorial")
        results["b"] = _replace_tag_in_place(results["b"], tag, row)
    if box_number == 13:
        oracles.clause_oracle(results, **kwargs)
    else:
        with pytest.raises(RuntimeError, match="memorial"):
            oracles.clause_oracle(results, **kwargs)


@pytest.mark.parametrize("title", ["crystal", "gold", "silver"])
def test_deposit_pp_real_sentret_record(title):
    # h10-gs-type-run2: Gold 22BA:C4A6:A1, PARTY_HP_WRITE slot 1 -> saved sBox14.
    party = "a10021000000c4a60000080000000000000000000022ba22000000460000000200000000000d00060006000600060007"
    box = "a10021000000c4a60000080000000000000000000022ba230000004600000002"
    assert oracles._deposited_record(party, codec.for_foundation(title)).hex() == box


@pytest.mark.parametrize("fault", ["missing", "hp", "bytes", "nickname", "late_write"])
def test_clause_memorial_requires_actual_preimage(clause_case, fault):
    test_clause_memorial_requires_saved_final_box(clause_case, 13)
    results, kwargs = clause_case
    if fault == "missing":
        results["b"] = "\n".join(line for line in results["b"].splitlines() if not line.startswith("MEMORIAL_PREIMAGE "))
    elif fault == "late_write":
        line = next(line for line in results["b"].splitlines() if line.startswith("PARTY_HP_WRITE "))
        results["b"] = results["b"].replace(line + "\n", "") + "\n" + line
    else:
        pre = oracles._last_tagged(results["b"], "MEMORIAL_PREIMAGE")
        if fault == "nickname":
            pre["nickname_raw_hex"] = "00" * 11
        else:
            raw = bytearray.fromhex(pre["raw_hex"])
            offset = codec.for_foundation("crystal").constants["MON_HP"] + 1 if fault == "hp" else 1
            raw[offset] ^= 1
            pre["raw_hex"] = raw.hex()
        results["b"] = _replace_tag_in_place(results["b"], "MEMORIAL_PREIMAGE", pre)
    with pytest.raises(RuntimeError):
        oracles.clause_oracle(results, **kwargs)


@pytest.mark.parametrize("ups", [0, 1, 2, 3])
def test_deposit_pp_up_bits_and_40_pp_cap(ups):
    layout = codec.for_foundation("gold")
    raw = bytearray.fromhex("a10021000000c4a60000080000000000000000000022ba22000000460000000200000000000d00060006000600060007")
    raw[layout.constants["MON_MOVES"]] = 45  # Growl: base PP 40 in the pinned move pack.
    raw[layout.constants["MON_PP"]] = ups * 64 + 1
    expected = bytes(raw[:layout.box_mon_size])
    expected = expected[:23] + bytes([ups * 64 + 40 + ups * 7]) + expected[24:]
    assert oracles._deposited_record(raw.hex(), layout) == expected


def test_deposit_pp_stops_at_first_zero_move():
    layout = codec.for_foundation("silver")
    raw = bytearray(48)
    raw[layout.constants["MON_MOVES"]:layout.constants["MON_MOVES"] + 4] = bytes([33, 0, 45, 33])
    raw[layout.constants["MON_PP"]:layout.constants["MON_PP"] + 4] = bytes([34, 0xC1, 2, 3])
    expected = bytes(raw[:layout.box_mon_size])
    expected = expected[:23] + bytes([35]) + expected[24:]
    assert oracles._deposited_record(raw.hex(), layout) == expected


@pytest.mark.parametrize("move", [252, 253, 254, 255])
def test_deposit_pp_rejects_unknown_move(move):
    layout = codec.for_foundation("gold")
    raw = bytearray(48)
    raw[layout.constants["MON_MOVES"]] = move
    with pytest.raises(RuntimeError, match="move"):
        oracles._deposited_record(raw.hex(), layout)


@pytest.mark.parametrize("fault", [None, "pp", "pp_up", "non_pp"])
def test_memorial_deposit_restores_consumed_pp_exactly(clause_case, fault):
    test_clause_memorial_requires_saved_final_box(clause_case, 13)
    results, kwargs = clause_case
    layout = codec.for_foundation("crystal")
    write = oracles._last_tagged(results["b"], "PARTY_HP_WRITE")
    at = layout.party_size * write["slot"] + layout.constants["MON_PP"]
    for field in ("before_party_hex", "after_party_hex"):
        record = bytearray.fromhex(write[field])
        assert record[at] == 35  # The modeled starter-derived capture knows Scratch, base PP 35.
        record[at] = 34
        write[field] = record.hex()
    results["b"] = _replace_tag_in_place(results["b"], "PARTY_HP_WRITE", write)
    preimage = oracles._last_tagged(results["b"], "MEMORIAL_PREIMAGE")
    raw_preimage = bytearray.fromhex(preimage["raw_hex"])
    raw_preimage[layout.constants["MON_PP"]] = 34
    preimage["raw_hex"] = raw_preimage.hex()
    results["b"] = _replace_tag_in_place(results["b"], "MEMORIAL_PREIMAGE", preimage)
    if fault:
        witness = oracles._last_tagged(results["b"], "SAVE_WITNESS")
        path = Path(witness["saveram_path"])
        raw = bytearray(path.read_bytes())
        at = layout.storage_boxes[13][0] + layout.addresses["sBoxMon1"] - layout.addresses["sBox"]
        at += layout.constants["MON_ITEM" if fault == "non_pp" else "MON_PP"]
        raw[at] ^= 0x40 if fault == "pp_up" else 1
        path.write_bytes(raw)
        _refresh_faint_hash(results, "b")
        with pytest.raises(RuntimeError, match="memorial"):
            oracles.clause_oracle(results, **kwargs)
    else:
        oracles.clause_oracle(results, **kwargs)


@pytest.mark.parametrize("fault", ["title", "source", "duplicate", "boolean_pp", "zero_pp", "overflow_pp"])
def test_deposit_pp_refuses_malformed_pack(monkeypatch, fault):
    layout = codec.for_foundation("gold")
    path = ROOT / "data/games/gen2_gold/moves.json"
    pack = json.loads(path.read_text())
    raw = bytearray(48)
    raw[layout.constants["MON_MOVES"]] = 33
    raw[layout.constants["MON_PP"]] = 0xC1
    if fault == "title":
        pack["title"] = "crystal"
    elif fault == "source":
        pack["source"]["rom_sha1"] = "0" * 40
    elif fault == "duplicate":
        pack["moves"].append(pack["moves"][0])
    else:
        next(row for row in pack["moves"] if row["id"] == 33)["pp"] = {"boolean_pp": True, "zero_pp": 0, "overflow_pp": 63}[fault]
    read = Path.read_text
    monkeypatch.setattr(Path, "read_text", lambda self, *args, **kwargs: json.dumps(pack) if self == path else read(self, *args, **kwargs))
    with pytest.raises(RuntimeError, match="deposit"):
        oracles._deposited_record(raw.hex(), layout)


def test_type_clause_valid_clean_pair_is_retry_not_release(tmp_path):
    results, data_dir, _ = _clause_base(tmp_path, (187, 19))
    pin = codec.for_foundation("crystal").profile["titles"]["crystal"]["rom_sha1"]
    for side in ("a", "b"):
        head = oracles._last_tagged(results[side], "DUO_GEN2")
        head.update(player=side, scenario="gen2_type_clause", rom_sha1=pin)
        client = oracles._last_tagged(results[side], "CLIENT")
        client["rom_sha1"] = pin
        results[side] = _replace_tag(_replace_tag(results[side], "DUO_GEN2", head), "CLIENT", client)
        cap = oracles._last_tagged(results[side], "ENGINE_CAPTURE")
        results[side] = _replace_tag(results[side], "CAPTURE_SENT", {"frame": 4801, "key": cap["key"], "seq": 8})
        results[side] = _replace_tag(results[side], "CLAUSE_CAPTURE", {"frame": 4802, "key": cap["key"], "species_id": cap["species_id"],
            "area_id": "route_29", "types": ["Grass", "Flying"] if side == "a" else ["Normal"],
            "gender": "female" if int(cap["key"][0], 16) <= 7 else "male"})
        results[side] = _replace_tag(results[side], "CLAUSE_VERDICT", {"frame": 4803, "verdict": "linked"})
        results[side] = _replace_tag(results[side], "RECEIPT", {**head, "schema": "gen2-duo-type-clause-v1",
            "clause": "type", "verdict": "linked", "path": "clause_unobserved"})
    (Path(data_dir) / "events.json").write_text("[]")
    facts = []
    with pytest.raises(oracles.ClauseUnobserved):
        oracles.clause_oracle(results, kind="type", data_dir=data_dir, boot_saveram=FIXTURES, on_verified=facts.append)
    assert facts == []


@pytest.mark.parametrize("fault", ["pending", "retry", "area", "link", "events", "write_checkpoint", "write_bytes", "alive",
                                   "prompt", "ack", "other_key", "types", "schema", "write_order"])
def test_clause_observed_refuses_mutations(clause_case, fault):
    results, kwargs = clause_case
    path = Path(kwargs["data_dir"]) / "links.json"
    doc = json.loads(path.read_text())
    if fault == "pending":
        doc["pending_captures"] = {}
    elif fault == "retry":
        doc["retry_areas"]["b"] = []
    elif fault == "area":
        doc["area_states"]["route_29"] = "linked"
    elif fault == "link":
        doc["links"] = [{"area_id": "route_29", "status": "alive"}]
    elif fault == "events":
        (Path(kwargs["data_dir"]) / "events.json").write_text("[]")
    elif fault in ("write_checkpoint", "write_bytes"):
        row = oracles._last_tagged(results["b"], "PARTY_HP_WRITE")
        if fault == "write_checkpoint":
            row["checkpoint"]["state"]["wBattleMode"] = 1
        else:
            row["after_party_hex"] = row["before_party_hex"]
        results["b"] = _replace_tag(results["b"], "PARTY_HP_WRITE", row)
    elif fault == "alive":
        save = oracles._last_tagged(results["b"], "SAVE_WITNESS")
        _edit_saved_record(Path(save["saveram_path"]), codec.for_foundation("crystal"), 1, codec.for_foundation("crystal").constants["MON_HP"], b"\x00\x01")
        _refresh_faint_hash(results, "b")
    elif fault == "prompt":
        results["b"] = results["b"].replace("shared Normal", "shared Fire")
    elif fault == "ack":
        results["b"] = results["b"].replace("memorialize_failed", "memorialize_done")
    elif fault == "other_key":
        results["b"] = results["b"].replace("force_faint key=", "force_faint key=BAD")
    elif fault == "write_order":
        command = next(line for line in results["b"].splitlines() if line.startswith("RX force_faint "))
        results["b"] = results["b"].replace(command + "\n", "") + "\n" + command
    elif fault == "types":
        row = oracles._last_tagged(results["a"], "CLAUSE_CAPTURE")
        row["types"] = ["Fire"]
        results["a"] = _replace_tag(results["a"], "CLAUSE_CAPTURE", row)
    else:
        results["b"] = results["b"].replace("gen2-duo-type-clause-v1", "gen2-duo-link-v1")
    path.write_text(json.dumps(doc))
    with pytest.raises(RuntimeError):
        oracles.clause_oracle(results, **kwargs)


def _bench_kill(text, mode=1):
    """O-32: the rejected catch killed on receipt while its capture battle is still up (final sweep
    gen2_type_clause, frame 5761: pc 0x0040 at the frame end, hROMBank 3, wBattleMode 1, why=battle_bench),
    with the harness's write-time battle snapshot (the lead, slot 0, is the active battler)."""
    row = oracles._last_tagged(text, "PARTY_HP_WRITE")
    row["checkpoint"].update(pc=0x0040, hrom_bank=3, sc=124)
    row["checkpoint"]["state"].update(wBattleMode=mode, wScriptFlags=4, wScriptMode=1, wScriptRunning=255, wStateFlags=64)
    row["battle"] = {"mode": mode, "active_slot": 0}
    for permit in row["log"]:
        permit["why"] = "battle_bench"
    return _replace_tag_in_place(text, "PARTY_HP_WRITE", row), row


@pytest.mark.parametrize("mode", [1, 2])      # WILD_BATTLE, TRAINER_BATTLE
def test_clause_rejection_bench_kill_in_the_capture_battle_passes(clause_case, mode):
    results, kwargs = clause_case
    results["b"], _ = _bench_kill(results["b"], mode)
    facts = []
    oracles.clause_oracle(results, **kwargs, on_verified=facts.append)
    assert facts[0]["rejected"] == "b"


@pytest.mark.parametrize("fault", ["mixed_why", "overworld_why", "active", "box_capture", "late_capture",
                                   "not_in_battle", "link_battle", "pc", "serial", "site", "active_battler",
                                   "no_battle", "battle_error", "battle_mode_0", "battle_mode_3", "battle_mode_255"])
def test_clause_rejection_bench_kill_refuses(clause_case, fault):
    results, kwargs = clause_case
    text, row = _bench_kill(results["b"], int(fault.rsplit("_", 1)[1]) if fault.startswith("battle_mode_") else 1)
    capture = oracles._last_tagged(text, "ENGINE_CAPTURE")
    if fault.startswith("battle_mode_"):      # outside wild/trainer (reads.lua read_battle's enumeration)
        pass
    elif fault == "active_battler":           # the write-time snapshot names the target as the active battler
        row["battle"]["active_slot"] = row["slot"]
    elif fault == "no_battle":
        del row["battle"]
    elif fault == "battle_error":
        row["battle"] = {"error": "battle mode outside source enumeration"}
    elif fault == "mixed_why":
        row["log"][0]["why"] = "overworld"
    elif fault == "overworld_why":            # the checkpoint's permit with battle evidence
        for permit in row["log"]:
            permit["why"] = "overworld"
    elif fault == "active":                   # the target is not the mon this battle caught
        capture["slot"] = 0
    elif fault == "box_capture":
        capture["destination"] = "box"
    elif fault == "late_capture":
        capture["frame"] = row["frame"] + 1
    elif fault == "not_in_battle":
        row["checkpoint"]["state"]["wBattleMode"] = 0
    elif fault == "link_battle":
        row["checkpoint"]["state"]["wLinkMode"] = 1
    elif fault == "pc":                       # not the frame end the U2 battle_bench run measured
        row["checkpoint"]["pc"] = 0x0041
    elif fault == "serial":
        row["checkpoint"]["sc"] = 0x80
    else:
        row["log"][1]["site"] = "harness"
    text = _replace_tag_in_place(text, "ENGINE_CAPTURE", capture)
    results["b"] = _replace_tag_in_place(text, "PARTY_HP_WRITE", row)
    with pytest.raises(RuntimeError):
        oracles.clause_oracle(results, **kwargs)


def test_faint_bench_write_needs_active_battler_evidence(faint_case):
    """gen2_faint's B write has no capture to prove it off the active battler: a battle_bench write refuses."""
    results, data_dir, _ = faint_case
    results["b"], _ = _bench_kill(results["b"])
    with pytest.raises(RuntimeError, match="active battler"):
        oracles.faint_oracle(results, data_dir=data_dir)


@pytest.fixture
def species_clause_case(tmp_path):
    results, data_dir, decoded = _clause_base(tmp_path)
    pin = codec.for_foundation("crystal").profile["titles"]["crystal"]["rom_sha1"]
    prompt = "Dupes clause: Pidgey -- reroll!"
    for side in ("a", "b"):
        old = results[side]
        head = oracles._last_tagged(old, "DUO_GEN2")
        head.update(player=side, scenario="gen2_species_clause", rom_sha1=pin)
        client = oracles._last_tagged(old, "CLIENT")
        client["rom_sha1"] = pin
        cap, save = oracles._last_tagged(old, "ENGINE_CAPTURE"), oracles._last_tagged(old, "SAVE_WITNESS")
        save.update(frame=8000, save_completed_frame=7990)
        rows = [("DUO_GEN2", head), ("CLIENT", client), ("HELLO", oracles._last_tagged(old, "HELLO"))]
        if side == "b":
            rows += [("A_PENDING", {"frame": 3500, "species_id": 16}),
                     ("ENCOUNTER", {"frame": 3600, "n": 1, "species_id": 16, "dupe": True}),
                     ("RX_TEXT", {"frame": 3610, "cmd": "gui_prompt", "text": prompt}),
                     ("REROLL", {"frame": 3700, "n": 1, "species_id": 16, "prompt": prompt}),
                     ("ENCOUNTER", {"frame": 4000, "n": 2, "species_id": 19, "dupe": False})]
        rows += [("ENGINE_CAPTURE", cap), ("CAPTURE_SENT", {"frame": 4801, "key": cap["key"], "seq": 8})]
        if side == "a":
            rows += [("PENDING_CAPTURE", {"frame": 5000, "key": cap["key"], "species_id": 16, "area_id": "route_29"})]
        rows += [("LINKED", {"frame": 7000, "text": "A and B linked!"}), ("SAVE_WITNESS", save),
                 ("RECEIPT", {**head, "schema": "gen2-duo-species-clause-v1", "role": "pending" if side == "a" else "reroller",
                   "species_id": cap["species_id"], "dupe_species": None if side == "a" else 16,
                   "rerolls": 0 if side == "a" else 1, "path": "pending" if side == "a" else "reroll_observed"})]
        results[side] = "\n".join(f"{tag} {json.dumps(row)}" for tag, row in rows) + f"\nCAUGHT {cap['key']}\nRESULT: PASS"
    capture_a = {"type": "capture", "player": "a", "key": decoded["a"]["key"], "area_id": "route_29"}
    pending = {"links": {"links": [], "pending_captures": {"route_29": {"a": decoded["a"]}}}, "events": [capture_a]}
    doc = json.loads((Path(data_dir) / "links.json").read_text())
    doc.update(pending_captures={}, area_states={"route_29": "linked"})
    events = [{"type": "linked", "player": "b", "area_id": "route_29"},
              {"type": "capture", "player": "b", "key": decoded["b"]["key"], "area_id": "route_29"},
              {"type": "reroll", "player": "b", "area_id": "route_29", "text": "🔁 " + prompt}, capture_a]
    (Path(data_dir) / "links.json").write_text(json.dumps(doc))
    (Path(data_dir) / "events.json").write_text(json.dumps(events), encoding="utf-8")
    return results, {"kind": "species", "data_dir": data_dir, "boot_saveram": FIXTURES, "pending_snapshot": pending}


def test_species_clause_pending_reroll_and_link_proof(species_clause_case):
    results, kwargs = species_clause_case
    facts = []
    oracles.clause_oracle(results, **kwargs, on_verified=facts.append)
    assert facts[0]["clause"] == "species" and facts[0]["rerolls"] == 1


@pytest.mark.parametrize("missing_prompt", [None, 1, 2])
def test_species_clause_early_prompts_are_bound_to_each_battle(species_clause_case, missing_prompt):
    results, kwargs = species_clause_case
    prompt = "Dupes clause: Pidgey -- reroll!"
    lines = []
    inserted = False
    for line in results["b"].splitlines():
        if line.startswith(("ENCOUNTER ", "REROLL ", "RX_TEXT ")):
            if not inserted:
                for number, frame in ((1, 3600), (2, 4000)):
                    if missing_prompt != number:
                        lines.append("RX_TEXT " + json.dumps({"frame": frame - 10, "cmd": "gui_prompt", "text": prompt}))
                    lines.append("ENCOUNTER " + json.dumps({"frame": frame, "n": number, "species_id": 16, "dupe": True}))
                    lines.append("REROLL " + json.dumps({"frame": frame + 100, "n": number, "species_id": 16, "prompt": prompt}))
                lines.append("ENCOUNTER " + json.dumps({"frame": 4400, "n": 3, "species_id": 19, "dupe": False}))
                inserted = True
            continue
        lines.append(line)
    receipt = oracles._last_tagged(results["b"], "RECEIPT")
    receipt["rerolls"] = 2
    results["b"] = _replace_tag_in_place("\n".join(lines), "RECEIPT", receipt)
    path = Path(kwargs["data_dir"]) / "events.json"
    events = json.loads(path.read_text(encoding="utf-8"))
    events.insert(2, dict(next(row for row in events if row.get("type") == "reroll")))
    path.write_text(json.dumps(events), encoding="utf-8")
    if missing_prompt is None:
        facts = []
        oracles.clause_oracle(results, **kwargs, on_verified=facts.append)
        assert facts[0]["rerolls"] == 2
    else:
        with pytest.raises(RuntimeError, match="reroll lacks observed prompt"):
            oracles.clause_oracle(results, **kwargs)


def test_species_clause_unobserved_is_typed_retry_not_release(species_clause_case):
    results, kwargs = species_clause_case
    text = results["b"]
    text = "\n".join(line for line in text.splitlines() if not line.startswith(("REROLL ", "RX_TEXT "))
                     and not (line.startswith("ENCOUNTER ") and json.loads(line[len("ENCOUNTER "):])["dupe"]))
    last = oracles._last_tagged(text, "ENCOUNTER")
    last["n"] = 1
    text = _replace_tag(text, "ENCOUNTER", last)
    receipt = oracles._last_tagged(text, "RECEIPT")
    receipt.update(path="reroll_unobserved", rerolls=0)
    results["b"] = _replace_tag(text, "RECEIPT", receipt)
    path = Path(kwargs["data_dir"]) / "events.json"
    path.write_text(json.dumps([row for row in json.loads(path.read_text(encoding="utf-8")) if row.get("type") != "reroll"]), encoding="utf-8")
    facts = []
    with pytest.raises(oracles.ClauseUnobserved):
        oracles.clause_oracle(results, **kwargs, on_verified=facts.append)
    assert facts == []


def test_clause_gender_uses_decoded_dvs(clause_case):
    results, kwargs = clause_case
    genders = [oracles._last_tagged(results[side], "CLAUSE_CAPTURE")["gender"] for side in ("a", "b")]
    if genders[0] != genders[1]:
        layout = codec.for_foundation("crystal")
        old_key = oracles._last_tagged(results["b"], "ENGINE_CAPTURE")["key"]
        new_key = ("A" if genders[0] == "male" else "1") + old_key[1:]
        dvs = bytes.fromhex(new_key[:4])
        save = oracles._last_tagged(results["b"], "SAVE_WITNESS")
        _edit_saved_record(Path(save["saveram_path"]), layout, 1, layout.constants["MON_DVS"], dvs)
        results["b"] = results["b"].replace(old_key, new_key)
        _refresh_faint_hash(results, "b")
        write = oracles._last_tagged(results["b"], "PARTY_HP_WRITE")
        for field in ("before_party_hex", "after_party_hex"):
            raw = bytearray.fromhex(write[field])
            start = layout.party_size + layout.constants["MON_DVS"]
            raw[start:start + 2] = dvs
            write[field] = raw.hex()
        results["b"] = _replace_tag(results["b"], "PARTY_HP_WRITE", write)
        cc = oracles._last_tagged(results["b"], "CLAUSE_CAPTURE")
        cc["gender"] = genders[0]
        results["b"] = _replace_tag(results["b"], "CLAUSE_CAPTURE", cc)
        for filename in ("links.json", "events.json"):
            path = Path(kwargs["data_dir"]) / filename
            path.write_text(path.read_text().replace(old_key, new_key))
    symbol = "♀" if genders[0] == "female" else "♂"
    for side in ("a", "b"):
        results[side] = results[side].replace("gen2_type_clause", "gen2_gender_clause").replace("gen2-duo-type-clause-v1", "gen2-duo-gender-clause-v1")
        receipt = oracles._last_tagged(results[side], "RECEIPT")
        receipt["clause"] = "gender"
        results[side] = _replace_tag(results[side], "RECEIPT", receipt).replace("Type clause: shared Normal", "Gender clause: both are " + symbol)
    path = Path(kwargs["data_dir"]) / "events.json"
    path.write_text(path.read_text().replace("Type clause: shared Normal", "Gender clause: both are " + symbol), encoding="utf-8")
    kwargs["kind"] = "gender"
    oracles.clause_oracle(results, **kwargs)


@pytest.mark.parametrize("fault", ["no_pending", "late_pending", "wrong_species", "no_prompt", "no_server_reroll", "encounter_order", "extra_capture"])
def test_species_clause_refuses_mutations(species_clause_case, fault):
    results, kwargs = species_clause_case
    if fault == "no_pending":
        kwargs["pending_snapshot"] = None
    elif fault == "late_pending":
        kwargs["pending_snapshot"]["events"].append({"type": "capture", "player": "b"})
    elif fault in ("wrong_species", "encounter_order"):
        tag = "A_PENDING" if fault == "wrong_species" else "REROLL"
        row = oracles._last_tagged(results["b"], tag)
        row["species_id" if fault == "wrong_species" else "n"] = 19 if fault == "wrong_species" else 2
        results["b"] = _replace_tag(results["b"], tag, row)
    elif fault == "no_prompt":
        results["b"] = "\n".join(line for line in results["b"].splitlines() if not line.startswith("RX_TEXT "))
    else:
        path = Path(kwargs["data_dir"]) / "events.json"
        rows = json.loads(path.read_text(encoding="utf-8"))
        if fault == "no_server_reroll":
            rows = [row for row in rows if row.get("type") != "reroll"]
        else:
            rows.insert(0, next(row for row in rows if row.get("type") == "capture" and row.get("player") == "b"))
        path.write_text(json.dumps(rows), encoding="utf-8")
    with pytest.raises(RuntimeError):
        oracles.clause_oracle(results, **kwargs)


def test_reconnect_same_save_rtc_trailer_may_differ(reconnect_case):
    results, kwargs = reconnect_case
    path = kwargs["staged_saves"]["same_save"]
    raw = bytearray(path.read_bytes())
    raw[-1] ^= 1
    path.write_bytes(raw)
    for tag in ("DUO_GEN2", "RECEIPT"):
        row = oracles._last_tagged(kwargs["relaunch_results"]["same_save"], tag)
        row["fixture_sha256"] = hashlib.sha256(raw).hexdigest()
        kwargs["relaunch_results"]["same_save"] = _replace_tag(kwargs["relaunch_results"]["same_save"], tag, row)
    oracles.reconnect_oracle(results, **kwargs)


def test_missing_links_json_refused(good_case, tmp_path):
    results, _data_dir, _decoded = good_case
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir(exist_ok=True)
    with pytest.raises(RuntimeError, match="no links.json"):
        oracles.link_oracle(results, data_dir=str(empty_dir))


def test_save_witness_authenticates_backup_before_native_scratch_change(good_case):
    results, _, _ = good_case
    witness = oracles._last_tagged(results["a"], "SAVE_WITNESS")
    path = Path(witness["saveram_path"])
    Path(str(path) + ".bak").write_bytes(path.read_bytes())
    raw = bytearray(path.read_bytes())
    raw[4] ^= 1
    path.write_bytes(raw)
    oracles.check_save_witness(results)


@pytest.mark.parametrize("title", ["crystal", "gold", "silver"])
@pytest.mark.parametrize("snapshot", ["backup", "raw", "full"])
def test_native_scratch_requires_authenticated_full_snapshot(tmp_path, title, snapshot):
    layout = codec.for_foundation(title)
    seed = (ROOT / f"tests/fixtures/gen2/{title}_battle.SaveRAM").read_bytes()
    path = tmp_path / "exit.SaveRAM"
    before = bytearray(seed)
    # First actual cg_reconnect_run2 Gold .bak -> exit delta: byte4 121->6, then122->6.
    before[4:12] = bytes([121, 122, 122, 122, 122, 122, 122, 122])
    after = bytearray(before)
    after[4:12] = bytes([6] * 8)
    if title != "crystal":
        after[0x1800] ^= 1
        after[0x1FFF] ^= 1
    after[-1] ^= 1
    path.write_bytes(after)
    witness = {"saveram_path": str(path), "saveram_bytes": CART + 22, "cartram_bytes": CART,
               "cartram_sha256": hashlib.sha256(before[:CART]).hexdigest()}
    baseline = Path(str(path) + ".bak") if snapshot == "backup" else tmp_path / "immutable.raw"
    baseline.write_bytes(before[:CART] if snapshot == "raw" else before)
    if snapshot != "backup":
        witness["snapshot_path"] = str(baseline)
    assert oracles.witness_save_bytes(witness, layout) == bytes(after)


@pytest.mark.parametrize("title", ["crystal", "gold", "silver"])
@pytest.mark.parametrize("protected", ["mail", "mail_backup", "stack", "rtc_halt", "gap", "backup", "box", "boundary"])
def test_native_scratch_never_masks_protected_save_bytes(tmp_path, title, protected):
    layout = codec.for_foundation(title)
    before = (ROOT / f"tests/fixtures/gen2/{title}_battle.SaveRAM").read_bytes()
    def flat(symbol):
        return layout.sram_banks[symbol] * 0x2000 + layout.addresses[symbol] - 0xA000
    offsets = {"mail": flat("sPartyMail"), "mail_backup": flat("sPartyMailBackup"), "stack": flat("sStackTop"),
               "rtc_halt": 0x17EF, "gap": 0x17FF, "backup": layout.regions[0].backup, "box": layout.storage_boxes[13][0], "boundary": 0x2000}
    after = bytearray(before)
    after[offsets[protected]] ^= 1
    path = tmp_path / "protected.SaveRAM"
    path.write_bytes(after)
    Path(str(path) + ".bak").write_bytes(before)
    witness = {"saveram_path": str(path), "saveram_bytes": CART + 22, "cartram_bytes": CART,
               "cartram_sha256": hashlib.sha256(before[:CART]).hexdigest()}
    with pytest.raises(RuntimeError):
        oracles.witness_save_bytes(witness, layout)


@pytest.mark.parametrize("fault", ["no_baseline", "wrong_backup", "short_backup", "wrong_explicit", "missing_explicit"])
def test_native_scratch_refuses_unauthenticated_baselines(good_case, tmp_path, fault):
    results, _, _ = good_case
    witness = oracles._last_tagged(results["a"], "SAVE_WITNESS")
    path = Path(witness["saveram_path"])
    raw = path.read_bytes()
    changed = bytearray(raw)
    changed[0] ^= 1
    if fault in ("wrong_explicit", "missing_explicit"):
        witness["snapshot_path"] = str(tmp_path / "snapshot.raw")
        if fault == "wrong_explicit":
            Path(witness["snapshot_path"]).write_bytes(changed)
        # Even a still-matching current file and backup may not excuse bad explicit provenance.
        Path(str(path) + ".bak").write_bytes(raw)
    else:
        path.write_bytes(changed)
        if fault == "wrong_backup":
            Path(str(path) + ".bak").write_bytes(changed)
        elif fault == "short_backup":
            Path(str(path) + ".bak").write_bytes(raw[:CART])
    with pytest.raises(RuntimeError):
        oracles.witness_save_bytes(witness, codec.for_foundation("crystal"))


@pytest.mark.parametrize("symbol", ["sScratch", "sPartyMail", "sWindowStackBottom", "sWindowStackTop"])
def test_native_scratch_refuses_source_geometry_drift(symbol):
    from dataclasses import replace

    layout = codec.for_foundation("gold")
    addresses = dict(layout.addresses)
    addresses[symbol] += 1
    with pytest.raises(RuntimeError, match="geometry"):
        oracles.normalized_gameplay_cartram(bytes(CART), replace(layout, addresses=addresses))


@pytest.mark.parametrize("where", ["stage", "exit", "protected"])
def test_reconnect_same_save_only_normalizes_authenticated_native_scratch(reconnect_case, where):
    results, kwargs = reconnect_case
    if where == "exit":
        witness = oracles._last_tagged(kwargs["initial_results"]["a"], "SAVE_WITNESS")
        path = Path(witness["saveram_path"])
        Path(str(path) + ".bak").write_bytes(path.read_bytes())
    else:
        path = kwargs["staged_saves"]["same_save"]
    raw = bytearray(path.read_bytes())
    raw[0x600 if where == "protected" else 0x5FF] ^= 1
    path.write_bytes(raw)
    if where != "exit":
        for tag in ("DUO_GEN2", "RECEIPT"):
            text = kwargs["relaunch_results"]["same_save"]
            row = oracles._last_tagged(text, tag)
            row["fixture_sha256"] = hashlib.sha256(raw).hexdigest()
            kwargs["relaunch_results"]["same_save"] = _replace_tag(text, tag, row)
    if where == "protected":
        with pytest.raises(RuntimeError):
            oracles.reconnect_oracle(results, **kwargs)
    else:
        oracles.reconnect_oracle(results, **kwargs)


def test_no_matching_link_refused(good_case, tmp_path):
    results, _data_dir, _decoded = good_case
    data_dir = tmp_path / "other"
    data_dir.mkdir(exist_ok=True)
    (data_dir / "links.json").write_text(json.dumps({"links": []}), encoding="utf-8")
    with pytest.raises(RuntimeError, match="expected exactly one alive"):
        oracles.link_oracle(results, data_dir=str(data_dir))


def test_altered_links_json_key_refused(good_case, tmp_path):
    results, data_dir, decoded = good_case
    document = json.loads((Path(data_dir) / "links.json").read_text(encoding="utf-8"))
    document["links"][0]["a"]["key"] = "9999:9999:01"
    (Path(data_dir) / "links.json").write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(RuntimeError, match="altered links.json"):
        oracles.link_oracle(results, data_dir=data_dir)


def test_altered_links_json_species_refused(good_case, tmp_path):
    results, data_dir, decoded = good_case
    document = json.loads((Path(data_dir) / "links.json").read_text(encoding="utf-8"))
    document["links"][0]["b"]["species"] = 999
    (Path(data_dir) / "links.json").write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(RuntimeError, match="altered links.json"):
        oracles.link_oracle(results, data_dir=data_dir)


def test_mon_stats_bookkeeping_for_the_new_keys_is_not_an_extra_acceptance(good_case, tmp_path):
    """The server keys mon_stats by every party mon (seen on the first physical C<->C duo)."""
    results, data_dir, decoded = good_case
    document = json.loads((Path(data_dir) / "links.json").read_text(encoding="utf-8"))
    document["mon_stats"] = {decoded[i]["key"]: {"kills": 0} for i in ("a", "b")}
    (Path(data_dir) / "links.json").write_text(json.dumps(document), encoding="utf-8")
    oracles.link_oracle(results, data_dir=data_dir)


def test_duplicate_key_elsewhere_in_links_json_refused(good_case, tmp_path):
    """The server accepted (or leaked) a key the two saves don't independently show twice."""
    results, data_dir, decoded = good_case
    document = json.loads((Path(data_dir) / "links.json").read_text(encoding="utf-8"))
    document["pending_captures"] = {"route_1": {"a": {"key": decoded["a"]["key"], "level": 5,
                                                      "species": decoded["a"]["species"]}}}
    (Path(data_dir) / "links.json").write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(RuntimeError, match="accepted something the saves don't show"):
        oracles.link_oracle(results, data_dir=data_dir)


@pytest.mark.parametrize("duplicate", [None, "pending", "link"])
def test_delivery_bookkeeping_does_not_hide_duplicate_acceptance(good_case, duplicate):
    results, data_dir, decoded = good_case
    path = Path(data_dir) / "links.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["gameplay_delivery"] = {
        "sync_versions": {
            side: {decoded[side]["key"]: {"cmd": "party_mon", "active": False, "confirmed": True}}
            for side in ("a", "b")
        }
    }
    if duplicate == "pending":
        document["pending_captures"] = {"route_30": {"a": document["links"][0]["a"]}}
    elif duplicate == "link":
        document["links"].append({**document["links"][0], "area_id": "route_30"})
    path.write_text(json.dumps(document), encoding="utf-8")
    if duplicate is None:
        oracles.link_oracle(results, data_dir=data_dir)
    else:
        with pytest.raises(RuntimeError, match="accepted something the saves don't show"):
            oracles.link_oracle(results, data_dir=data_dir)


# ---------------------------------------------------------------------------
# DUO-WAVE-C: whiteout, pc_ops, changebox (tools/gen2_duo_oracles.py *_oracle)
# ---------------------------------------------------------------------------

def _save_move(path, layout, *, box):
    """The save's last party mon leaves the party: into Box `box` (0-based), or released (None)."""
    raw = bytearray(path.read_bytes())
    party = codec.decode_saved_party(bytes(raw[:CART]), layout, copy_name="primary")["mons"]
    mon = party[-1]
    if box is not None:
        stored = codec.verify_boxes(bytes(raw[:CART]), layout)[box]
        stored.update(count=len(stored["mons"]) + 1,
                      mons=stored["mons"] + [{**mon, "raw_hex": mon["raw_hex"][:layout.box_mon_size * 2]}])
        start, length = layout.storage_boxes[box]
        raw[start:start + length] = codec.encode_box(stored, layout)
    region = next(r for r in layout.regions if r.name == "pokemon")
    _poke(raw, region, layout.addresses["wPartyCount"] - layout.addresses["wPokemonData"], bytes([len(party) - 1]))
    _poke(raw, region, layout.addresses["wPartySpecies"] - layout.addresses["wPokemonData"] + len(party) - 1, bytes([255]))
    _rechecksum(raw, layout)
    path.write_bytes(raw)
    return mon


def _rechecksum(raw, layout):
    for copy_name in ("primary", "backup"):
        offset = layout.checksum_offsets[copy_name]
        raw[offset:offset + 2] = codec.sav_checksum(bytes(raw[:CART]), layout, copy_name).to_bytes(2, "little")


def _wave_text(text, *, scenario, schema, sites=None, drop=(), insert=(), before="SAVE_WITNESS ", witness=None):
    """Retag a result for a wave-C scenario: header/receipt, drop tags, insert lines before `before`."""
    client = oracles._last_tagged(text, "CLIENT")
    header = oracles._last_tagged(text, "DUO_GEN2")
    if sites is not None:
        client["registered_sites"] = sites
    header["scenario"] = scenario
    out = []
    for line in text.splitlines():
        tag = line.split(" ", 1)[0]
        if tag in drop or line.startswith(("RECEIPT ", "RESULT:")):
            continue
        if line.startswith(before):
            out += list(insert)
        if tag == "CLIENT":
            line = "CLIENT " + json.dumps(client)
        elif tag == "DUO_GEN2":
            line = "DUO_GEN2 " + json.dumps(header)
        elif tag == "SAVE_WITNESS" and witness is not None:
            line = "SAVE_WITNESS " + json.dumps(witness)
        out.append(line)
    receipt = {"schema": schema, "title": client["title"], "rom_sha1": client["rom_sha1"]}
    return "\n".join(out + ["RECEIPT " + json.dumps(receipt), "RESULT: PASS"])


@pytest.fixture
def pc_case(good_case, layout, tmp_path):
    results, data_dir, decoded = good_case
    rom = layout.profile["titles"]["crystal"]["rom_sha1"]
    for inst in ("a", "b"):
        text = results[inst]
        final = oracles._last_tagged(text, "SAVE_WITNESS")
        path = Path(final["saveram_path"])
        link_path = tmp_path / f"{inst}.linked.SaveRAM"
        link_path.write_bytes(path.read_bytes())
        key = decoded[inst]["key"]
        _save_move(path, layout, box=None if inst == "a" else 13)   # B's partner: memorialized into Box 14
        final.update(frame=9000, save_completed_frame=8990, gate_saves=2, client_saves=2,
                     cartram_sha256=hashlib.sha256(path.read_bytes()[:CART]).hexdigest())
        link = {"frame": 5000, "key": key, "gate_saves": 1, "client_saves": 1, "save_completed_frame": 4990,
                "saveram_path": str(link_path), "saveram_bytes": len(link_path.read_bytes()), "cartram_bytes": CART,
                "cartram_sha256": hashlib.sha256(link_path.read_bytes()[:CART]).hexdigest()}
        insert = ["LINK_SAVE " + json.dumps(link)]
        if inst == "a":
            def pc(frame, kind, **extra):
                return "ENGINE_PC " + json.dumps({"frame": frame, "kind": kind, "site_id": "x", "key": key, **extra})

            def tx(event):
                return "TX " + json.dumps({"event": event, "key": key, "seq": 1}, separators=(",", ":"))
            insert += [pc(6000, "party_to_box"), tx("party_to_box"), pc(6100, "box_to_party"), tx("box_to_party"),
                       pc(6400, "party_to_box"), tx("party_to_box"), pc(6500, "pc_release", collection="box"),
                       tx("release")]
        else:
            insert += [f"RX {cmd} key={key}" for cmd in ("box_mon", "party_mon", "box_mon", "force_faint", "memorialize")]
        text = _wave_text(text, scenario="gen2_pc_ops", schema="gen2-duo-pc-ops-v2", insert=insert, witness=final)
        results[inst] = text.replace('"rom_sha1": "' + "deadbeef" * 5 + '"', f'"rom_sha1": "{rom}"')
        results[inst] = results[inst].replace('"player": "a"', f'"player": "{inst}"')
    a, b = decoded["a"]["key"][:8], decoded["b"]["key"][:8]
    (Path(data_dir) / "server.log").write_text(
        f"[a] party_to_box {a} → box_mon b:{b}\n[a] box_to_party {a} → party_mon b:{b} (stats cached)\n"
        f"[a] party_to_box {a} → box_mon b:{b}\n[a] released linked {a} — the partner dies (O-35)\n", encoding="utf-8")
    path = Path(data_dir) / "links.json"
    doc = json.loads(path.read_text())
    doc["links"][0].update(status="memorial", cause="release", initiating_player="a", killed_at="2026-09-24T12:00:00Z")
    doc["pending_memorials"] = {"a": [], "b": []}
    path.write_text(json.dumps(doc))
    return results, data_dir, decoded


def test_pc_ops_oracle_passes_the_mirrored_moves_and_the_o35_release_death(pc_case):
    results, data_dir, decoded = pc_case
    facts = []
    assert oracles.pc_ops_oracle(results, data_dir=data_dir, on_verified=facts.append) is None
    assert facts[0]["status"] == "memorial" and facts[0]["release"] == "propagated"


def _pc_fault(pc_case, fault, layout):
    results, data_dir, decoded = pc_case
    if fault == "a-kept":
        witness = oracles._last_tagged(results["a"], "SAVE_WITNESS")
        link = Path(oracles._last_tagged(results["a"], "LINK_SAVE")["saveram_path"])
        path = Path(witness["saveram_path"])
        path.write_bytes(link.read_bytes())
        _save_move(path, layout, box=0)
        new = dict(witness, cartram_sha256=hashlib.sha256(path.read_bytes()[:CART]).hexdigest())
        results["a"] = results["a"].replace("SAVE_WITNESS " + json.dumps(witness), "SAVE_WITNESS " + json.dumps(new))
    elif fault == "b-in-party":
        witness = oracles._last_tagged(results["b"], "SAVE_WITNESS")
        link = Path(oracles._last_tagged(results["b"], "LINK_SAVE")["saveram_path"])
        Path(witness["saveram_path"]).write_bytes(link.read_bytes())
        new = dict(witness, cartram_sha256=hashlib.sha256(link.read_bytes()[:CART]).hexdigest())
        results["b"] = results["b"].replace("SAVE_WITNESS " + json.dumps(witness), "SAVE_WITNESS " + json.dumps(new))
    elif fault == "one-mirror":
        log = Path(data_dir) / "server.log"
        log.write_text("\n".join(log.read_text(encoding="utf-8").splitlines()[:2]) + "\n", encoding="utf-8")
    elif fault == "party-release":
        results["a"] = results["a"].replace('"collection": "box"', '"collection": "party"')
    elif fault == "b-order":
        key = decoded["b"]["key"]
        results["b"] = results["b"].replace(f"RX party_mon key={key}", "RX tmp").replace(
            f"RX box_mon key={key}\nRX tmp", f"RX party_mon key={key}\nRX box_mon key={key}", 1)
    elif fault == "alive":
        path = Path(data_dir) / "links.json"
        doc = json.loads(path.read_text())
        doc["links"][0].update(status="alive", killed_at=None)
        path.write_text(json.dumps(doc))
    elif fault == "no-release-log":
        log = Path(data_dir) / "server.log"
        log.write_text(log.read_text(encoding="utf-8").replace("released linked", "released unlinked"), encoding="utf-8")
    elif fault == "no-release-send":
        results["a"] = results["a"].replace('TX {"event":"release"', 'TX {"event":"noop"')
    elif fault == "b-reissue-ok":
        key = decoded["b"]["key"]
        results["b"] = results["b"].replace(f"RX party_mon key={key}", f"RX party_mon key={key}\nRX party_mon key={key}")
        return results, data_dir
    elif fault == "b-reissue-out-of-order":
        key = decoded["b"]["key"]
        results["b"] = results["b"].replace(f"RX memorialize key={key}", f"RX memorialize key={key}\nRX party_mon key={key}")
    elif fault == "b-no-memorialize":
        key = decoded["b"]["key"]
        results["b"] = results["b"].replace(f"RX memorialize key={key}\n", "")
    return results, data_dir


@pytest.mark.parametrize("fault,match", [
    ("a-kept", "released key is still"), ("b-in-party", "not in the memorial box"), ("one-mirror", "mirror two deposits"),
    ("party-release", "engine PC events"), ("b-order", "box_mon, party_mon, box_mon, force_faint"), ("alive", "dead"),
    ("no-release-log", "exactly one release"), ("no-release-send", "and the release"),
    ("b-no-memorialize", "force_faint, memorialize"), ("b-reissue-out-of-order", "force_faint, memorialize")])
def test_pc_ops_oracle_refuses_independent_save_server_and_marker_faults(pc_case, fault, match, layout):
    results, data_dir = _pc_fault(pc_case, fault, layout)
    with pytest.raises(RuntimeError, match=match):
        oracles.pc_ops_oracle(results, data_dir=data_dir)


@pytest.fixture
def changebox_case(faint_case):
    results, data_dir = _memorial_case(faint_case)
    j = json.dumps
    changes = ["ENGINE_PC " + j({"frame": 7400, "kind": "box_change", "site_id": "change_box_loaded", "old_box": 0, "new_box": 13}),
               "CHANGEBOX_TO " + j({"frame": 7450, "cur_box": 13, "box_count": 1}),
               "ENGINE_PC " + j({"frame": 7500, "kind": "box_change", "site_id": "change_box_loaded", "old_box": 13, "new_box": 0}),
               "CHANGEBOX_BACK " + j({"frame": 7550, "cur_box": 0, "box_count": 0})]
    for inst in ("a", "b"):
        results[inst] = _wave_text(results[inst], scenario="gen2_changebox", schema="gen2-duo-changebox-v1",
                                   insert=changes if inst == "b" else ())
    return results, data_dir


def test_changebox_oracle_passes_the_box14_round_trip(changebox_case):
    results, data_dir = changebox_case
    facts = []
    assert oracles.changebox_oracle(results, data_dir=data_dir, on_verified=facts.append) is None
    assert facts[0]["status"] == "memorial" and facts[0]["box_change"] == "BOX1->BOX14->BOX1"


@pytest.mark.parametrize("fault,match", [
    ("wrong-box", "not BOX1 -> BOX14 -> BOX1"), ("unlisted", "did not list the memorial"),
    ("transfer", "not a transfer"), ("saved-box", "saved current box is not BOX1"), ("schema", "receipt schema")])
def test_changebox_oracle_refuses(changebox_case, fault, match, layout):
    results, data_dir = changebox_case
    b = results["b"]
    if fault == "wrong-box":
        b = b.replace('"new_box": 13', '"new_box": 12', 1)
    elif fault == "unlisted":
        b = b.replace('"cur_box": 13, "box_count": 1', '"cur_box": 13, "box_count": 0')
    elif fault == "transfer":
        b = b.replace("\nSAVE_WITNESS", '\nTX {"event":"party_to_box"}\nSAVE_WITNESS')
    elif fault == "saved-box":
        witness = oracles._last_tagged(b, "SAVE_WITNESS")
        path = Path(witness["saveram_path"])
        raw = bytearray(path.read_bytes())
        region, offset = _region_and_offset(layout, "wCurBox")
        _poke(raw, region, offset, bytes([13]))
        _rechecksum(raw, layout)
        path.write_bytes(raw)
        new = dict(witness, cartram_sha256=hashlib.sha256(bytes(raw[:CART])).hexdigest())
        b = b.replace("SAVE_WITNESS " + json.dumps(witness), "SAVE_WITNESS " + json.dumps(new))
    elif fault == "schema":
        b = b.replace("gen2-duo-changebox-v1", "gen2-duo-faint-v1")
    results["b"] = b
    with pytest.raises(RuntimeError, match=match):
        oracles.changebox_oracle(results, data_dir=data_dir)


@pytest.fixture
def whiteout_case(faint_case):
    results, data_dir = _memorial_case(faint_case)
    j = json.dumps
    layout = codec.for_foundation("crystal")
    a = results["a"]
    key = oracles._last_tagged(a, "ENGINE_FAINT")["key"]
    link = Path(oracles._last_tagged(a, "LINK_SAVE")["saveram_path"]).read_bytes()
    starter = codec.key(codec.decode_saved_party(link[:CART], layout, copy_name="primary")["mons"][0])
    # the memorialized record is the HEALED one: HP back at 14 (HealParty), the rest as observed
    preimage = oracles._last_tagged(a, "MEMORIAL_PREIMAGE")
    raw = bytearray.fromhex(preimage["raw_hex"])
    at = layout.constants["MON_HP"]
    raw[at:at + 2] = (14).to_bytes(2, "big")
    a = a.replace("MEMORIAL_PREIMAGE " + j(preimage), "MEMORIAL_PREIMAGE " + j({**preimage, "raw_hex": raw.hex()}))
    insert = ["ENGINE_FAINT " + j({"frame": 6900, "site_id": "battle_faint", "cause": "battle", "key": starter, "slot": 0}),
              "ENGINE_FAINT " + j({"frame": 7000, "site_id": "battle_faint", "cause": "battle", "key": key, "slot": 1}),
              "FAINT_SENT " + j({"frame": 7001, "key": key, "seq": 42}),
              "RX game_over",
              "ENGINE_WHITEOUT " + j({"frame": 7050, "site_id": "whiteout_before_heal",
                                      "party": [{"key": starter, "hp": 0}, {"key": key, "hp": 0}]}),
              "TX " + j({"event": "whiteout", "seq": 43}, separators=(",", ":")),
              "REVIVED " + j({"frame": 7060, "key": key, "slot": 1, "hp": 14, "ticks": 3})]
    results["a"] = _wave_text(a, scenario="gen2_whiteout", schema="gen2-duo-whiteout-v1",
                              sites=["battle_faint", "whiteout_before_heal"], drop=("ENGINE_FAINT", "FAINT_SENT"),
                              insert=insert, before="MEMORIAL_PREIMAGE ")
    results["b"] = _wave_text(results["b"], scenario="gen2_whiteout", schema="gen2-duo-whiteout-v1",
                              insert=["RX game_over"], before="MEMORIAL_PREIMAGE ")
    log = Path(data_dir) / "server.log"
    first, rest = log.read_text(encoding="utf-8").split("\n", 1)
    log.write_text(f"{first}\nGAME OVER — no alive links and no pending captures remain\n{rest}", encoding="utf-8")
    path = Path(data_dir) / "links.json"
    doc = json.loads(path.read_text())
    doc["run_over"] = True
    path.write_text(json.dumps(doc))
    return results, data_dir


def test_whiteout_oracle_passes_revive_and_burial_under_game_over(whiteout_case):
    results, data_dir = whiteout_case
    facts = []
    assert oracles.whiteout_oracle(results, data_dir=data_dir, on_verified=facts.append) is None
    assert facts[0]["status"] == "memorial" and facts[0]["repair"] == "run_over"


@pytest.mark.parametrize("fault,match", [
    ("no-game-over", "did not end the run"), ("catch-first", "starter's, then the linked"),
    ("pre-heal-hp", "linked party at HP 0"), ("not-revived", "no revived linked mon"),
    ("two-whiteouts", "exactly one whiteout"), ("b-whiteout", "exactly one whiteout"),
    ("b-no-game-over", "b never received game_over"), ("o24", "re-issued force_faint under run_over"),
    ("dead-preimage", "not the revived record"), ("starter-hurt", "healed starter")])
def test_whiteout_oracle_refuses(whiteout_case, fault, match, layout):
    results, data_dir = whiteout_case
    a = results["a"]
    key = oracles._last_tagged(a, "REVIVED")["key"]
    log = Path(data_dir) / "server.log"
    if fault == "no-game-over":
        log.write_text("\n".join(l for l in log.read_text(encoding="utf-8").splitlines() if "GAME OVER" not in l) + "\n",
                       encoding="utf-8")
    elif fault == "catch-first":
        lines = a.splitlines()
        i = [n for n, l in enumerate(lines) if l.startswith("ENGINE_FAINT ")]
        lines[i[0]], lines[i[1]] = lines[i[1]], lines[i[0]]
        a = "\n".join(lines)
    elif fault == "pre-heal-hp":
        a = a.replace(f'{{"key": "{key}", "hp": 0}}', f'{{"key": "{key}", "hp": 5}}')
    elif fault == "not-revived":
        a = a.replace('"hp": 14, "ticks"', '"hp": 0, "ticks"')
    elif fault == "two-whiteouts":
        a = a.replace("\nREVIVED", '\nTX {"event":"whiteout","seq":44}\nREVIVED')
    elif fault == "b-whiteout":
        results["b"] = results["b"].replace("\nSAVE_WITNESS", '\nTX {"event":"whiteout"}\nSAVE_WITNESS')
    elif fault == "b-no-game-over":
        results["b"] = results["b"].replace("RX game_over\n", "")
    elif fault == "o24":
        log.write_text(log.read_text(encoding="utf-8") + f"[a] {key} is dead but alive in party (hp=14) — re-issued force_faint (1/3)\n",
                       encoding="utf-8")
    elif fault == "dead-preimage":
        a = a.replace('"hp": 14, "ticks"', '"hp": 9, "ticks"')
    elif fault == "starter-hurt":
        witness = oracles._last_tagged(a, "SAVE_WITNESS")
        path = Path(witness["saveram_path"])
        _edit_saved_record(path, layout, 0, layout.constants["MON_HP"], (3).to_bytes(2, "big"))
        new = dict(witness, cartram_sha256=hashlib.sha256(path.read_bytes()[:CART]).hexdigest())
        a = a.replace("SAVE_WITNESS " + json.dumps(witness), "SAVE_WITNESS " + json.dumps(new))
    results["a"] = a
    with pytest.raises(RuntimeError, match=match):
        oracles.whiteout_oracle(results, data_dir=data_dir)


@pytest.fixture
def poison_case(faint_case):
    results, data_dir = _memorial_case(faint_case)
    j = json.dumps
    for inst in ("a", "b"):
        text = results[inst]
        if inst == "a":
            faint = oracles._last_tagged(text, "ENGINE_FAINT")
            text = text.replace("ENGINE_FAINT " + j(faint), "ENGINE_FAINT " + j({**faint, "site_id": "poison_faint", "cause": "poison"}))
        results[inst] = _wave_text(text, scenario="gen2_poison", schema="gen2-duo-poison-v1",
                                   sites=["poison_faint", "save_completed"] if inst == "a" else None)
    return results, data_dir


def test_poison_oracle_binds_the_death_to_poison_faint(poison_case):
    results, data_dir = poison_case
    facts = []
    assert oracles.poison_oracle(results, data_dir=data_dir, on_verified=facts.append) is None
    assert facts[0]["status"] == "memorial" and facts[0]["death"] == "poison"


@pytest.mark.parametrize("fault,match", [
    ("battle-death", "cause differs"), ("no-site", "lack poison_faint"), ("schema", "receipt schema")])
def test_poison_oracle_refuses(poison_case, fault, match):
    results, data_dir = poison_case
    a = results["a"]
    if fault == "battle-death":
        a = a.replace('"site_id": "poison_faint", "cause": "poison"', '"site_id": "battle_faint", "cause": "battle"')
    elif fault == "no-site":
        a = a.replace('"registered_sites": ["poison_faint", "save_completed"]', '"registered_sites": ["battle_faint"]')
    elif fault == "schema":
        a = a.replace("gen2-duo-poison-v1", "gen2-duo-faint-v1")
    results["a"] = a
    with pytest.raises(RuntimeError, match=match):
        oracles.poison_oracle(results, data_dir=data_dir)


@pytest.fixture
def rebuild_case(good_case, layout, tmp_path):
    results, data_dir, decoded = good_case
    j = json.dumps
    rom = layout.profile["titles"]["crystal"]["rom_sha1"]
    for inst in ("a", "b"):
        text = results[inst]
        final = oracles._last_tagged(text, "SAVE_WITNESS")
        path = Path(final["saveram_path"])
        link_path = tmp_path / f"{inst}.linked.SaveRAM"
        link_path.write_bytes(path.read_bytes())   # the rebuilt save: the linked party again, all at full HP
        key = decoded[inst]["key"]
        final.update(frame=9000, save_completed_frame=8990, gate_saves=2, client_saves=2)
        link = {"frame": 5000, "key": key, "gate_saves": 1, "client_saves": 1, "save_completed_frame": 4990,
                "saveram_path": str(link_path), "saveram_bytes": len(link_path.read_bytes()), "cartram_bytes": CART,
                "cartram_sha256": hashlib.sha256(link_path.read_bytes()[:CART]).hexdigest()}
        insert = ["LINK_SAVE " + j(link)]
        if inst == "a":
            starter = codec.key(codec.decode_saved_party(link_path.read_bytes()[:CART], layout, copy_name="primary")["mons"][0])
            insert += ["ENGINE_PC " + j({"frame": 6000, "kind": "party_to_box", "key": key}),
                       'TX {"event":"party_to_box"}',
                       "ENGINE_FAINT " + j({"frame": 7000, "site_id": "battle_faint", "cause": "battle", "key": starter, "slot": 0}),
                       "ENGINE_WHITEOUT " + j({"frame": 7050, "site_id": "whiteout_before_heal", "party": [{"key": starter, "hp": 0}]}),
                       'TX {"event":"whiteout"}', "RX rebuild_start", f"RX party_mon key={key}", "RX rebuild_done"]
        else:
            insert += [f"RX box_mon key={key}", f"RX party_mon key={key}"]
        text = _wave_text(text, scenario="gen2_whiteout_rebuild", schema="gen2-duo-whiteout-rebuild-v1", insert=insert,
                          witness=final)
        results[inst] = text.replace('"rom_sha1": "' + "deadbeef" * 5 + '"', f'"rom_sha1": "{rom}"').replace(
            '"player": "a"', f'"player": "{inst}"')
    a, b = decoded["a"]["key"][:8], decoded["b"]["key"][:8]
    (Path(data_dir) / "server.log").write_text(
        f"[a] party_to_box {a} → box_mon b:{b}\n[a] whiteout rebuild armed — restoring 1 mon(s); partner mirrors 1\n"
        f"[a] whiteout\n[a] rebuild complete — 1 restored, 0 dropped\n", encoding="utf-8")
    return results, data_dir


def test_whiteout_rebuild_oracle_passes_the_pc_rebuild(rebuild_case):
    results, data_dir = rebuild_case
    facts = []
    assert oracles.whiteout_rebuild_oracle(results, data_dir=data_dir, on_verified=facts.append) is None
    assert facts[0]["status"] == "alive" and facts[0]["rebuild"] == "restored"


@pytest.mark.parametrize("fault,match", [
    ("no-armed", "armed rebuild"), ("killed", "killed something"), ("two-whiteout-party", "starter alone"),
    ("no-done", "rebuild_start then rebuild_done"), ("b-death", "b received a death command"),
    ("b-start", "B received rebuild_start"), ("hurt", "not at full HP")])
def test_whiteout_rebuild_oracle_refuses(rebuild_case, fault, match, layout):
    results, data_dir = rebuild_case
    log = Path(data_dir) / "server.log"
    if fault == "no-armed":
        log.write_text(log.read_text(encoding="utf-8").replace("rebuild armed", "rebuild skipped"), encoding="utf-8")
    elif fault == "killed":
        log.write_text(log.read_text(encoding="utf-8") + "[a] faint → force_faint b:X\n", encoding="utf-8")
    elif fault == "two-whiteout-party":
        results["a"] = results["a"].replace('"hp": 0}]', '"hp": 0}, {"key": "x", "hp": 0}]')
    elif fault == "no-done":
        results["a"] = results["a"].replace("RX rebuild_done\n", "")
    elif fault == "b-death":
        results["b"] = results["b"].replace("\nSAVE_WITNESS", "\nRX game_over\nSAVE_WITNESS")
    elif fault == "b-start":
        results["b"] = results["b"].replace("\nSAVE_WITNESS", "\nRX rebuild_start\nSAVE_WITNESS")
    elif fault == "hurt":
        witness = oracles._last_tagged(results["a"], "SAVE_WITNESS")
        path = Path(witness["saveram_path"])
        _edit_saved_record(path, layout, 0, layout.constants["MON_HP"], (3).to_bytes(2, "big"))
        new = dict(witness, cartram_sha256=hashlib.sha256(path.read_bytes()[:CART]).hexdigest())
        results["a"] = results["a"].replace("SAVE_WITNESS " + json.dumps(witness), "SAVE_WITNESS " + json.dumps(new))
    with pytest.raises(RuntimeError, match=match):
        oracles.whiteout_rebuild_oracle(results, data_dir=data_dir)


def _hurt_b(results, layout, *, linked_too, rebuilt, field="MON_HP", value=(3).to_bytes(2, "big")):
    """Set B's final-save HP of the rebuilt mon (rebuilt=True) or of its unlinked starter to 3; linked_too applies the
    same hurt to B's LINK_SAVE copy (a starter hit in B's own capture battle)."""
    witness = oracles._last_tagged(results["b"], "SAVE_WITNESS")
    link = oracles._last_tagged(results["b"], "LINK_SAVE")
    mons = codec.decode_saved_party(Path(witness["saveram_path"]).read_bytes()[:CART], layout, copy_name="primary")["mons"]
    slot = next(i for i, m in enumerate(mons) if (codec.key(m) == link["key"]) == rebuilt)
    text = results["b"]
    for row, tag in ((witness, "SAVE_WITNESS"), (link, "LINK_SAVE")) if linked_too else ((witness, "SAVE_WITNESS"),):
        path = Path(row["saveram_path"])
        _edit_saved_record(path, layout, slot, layout.constants[field], value)
        text = text.replace(f"{tag} " + json.dumps(row),
                            f"{tag} " + json.dumps(dict(row, cartram_sha256=hashlib.sha256(path.read_bytes()[:CART]).hexdigest())))
    results["b"] = text


def test_whiteout_rebuild_oracle_keeps_bs_unlinked_hp_as_linked(rebuild_case, layout):
    """df04e065 sweep G-S: B never whites out, so its starter keeps the HP its own capture battle left (17/20 at
    LINK_SAVE and in the final save); only the rebuilt linked mon is healed by the withdraw."""
    results, data_dir = rebuild_case
    _hurt_b(results, layout, linked_too=True, rebuilt=False)
    assert oracles.whiteout_rebuild_oracle(results, data_dir=data_dir) is None


@pytest.mark.parametrize("rebuilt,match", [(False, "HP/status changed since LINK_SAVE"), (True, "not at full HP")])
@pytest.mark.parametrize("field,value", [("MON_HP", (3).to_bytes(2, "big")), ("MON_STATUS", bytes([0x08]))])  # 3 HP / PSN
def test_whiteout_rebuild_oracle_refuses_a_changed_b_mon(rebuild_case, layout, rebuilt, match, field, value):
    results, data_dir = rebuild_case
    _hurt_b(results, layout, linked_too=False, rebuilt=rebuilt, field=field, value=value)
    with pytest.raises(RuntimeError, match=match):
        oracles.whiteout_rebuild_oracle(results, data_dir=data_dir)


def test_pc_ops_oracle_absorbs_a_consecutive_sync_reissue(pc_case, layout):
    """Live G-S pc_ops: the server re-issued party_mon after its in-flight window; B received it twice in a row."""
    results, data_dir = _pc_fault(pc_case, "b-reissue-ok", layout)
    assert oracles.pc_ops_oracle(results, data_dir=data_dir) is None
