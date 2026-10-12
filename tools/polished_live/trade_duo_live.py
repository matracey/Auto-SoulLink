"""Shipped native trade + cold CONTINUE; --test-build selects the isolated dev build.

SYNTH: retained receptionist fixture, derived second identity, exactly one link
seeded through State hello/capture events. Receptionist onward uses native input,
real clients/server and native commit/save. Only owned process PIDs are stopped.

The judge consumes both sides' ordered, qualified APPLY/commit/native-save/
save-readback/publish/exit witnesses, not a producer PASS or protocol ACK alone.
Cold CONTINUE and both checksummed save copies must preserve trainer ID AND name,
and every received/surviving record byte, OT and nickname. Native slot rotation
is allowed. For this unchanged-key fixture, the sole record transformation is
non-egg happiness -> BASE_HAPPINESS (70): native engine/pokemon/move_mon.asm
AddTempMonToParty, and patch/polished/src/trade_commit.asm .CheckAppend. Eggs keep
their happiness. Trade evolution can change species/item/stats/default nickname;
this gate's deliberately non-evolving exchanged fixture does not certify those
paths, and cannot waive bytes for them. Missing raw/native evidence fails closed.
Launch JSON records the prepared argv, effective launch environment and input
bytes immediately before Popen. Its source inventory and requested selection
are prelaunch observations, not proof of the eventual Lua branch or a verdict.
Shipped composition never falls back to the isolated development binder.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tools.polished_live.overlay_pin import overlay_sha1  # noqa: E402

sys.path.insert(0, str(ROOT))
from server.adapters import polished_codec as pc  # noqa: E402
from tools.polished_live import duo, reload_probe  # noqa: E402

LANE = Path("F:/slink-work/lanes/pol-tradeduo")
SHIPPED = overlay_sha1()
FIXTURE = Path("F:/slink-work/lanes/pol-svclive/apply-done1/attempt-0003/sram_overlay/pol overlay.SaveRAM")
DISCLOSURE = "SYNTH: retained receptionist fixture, second identity derivative, one ALIVE link seeded via server events; trade menus/commit/trade_done native"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def probe_symbols(syms):
    from tools.polished_live import harness
    # The full ~70k-label SYM exceeds json_codec's 100k-value bound (each row is an array).
    return {name: syms[name] for name in (*harness.SYMBOLS, "NoYesBox", *NATIVE_SITES,
                                         "SlinkTradeResponderService")}


def party(data):
    ds = duo._derive_save()
    duo.fixture_identity(data)
    rows = []
    for i in range(ds.party_count(data, ds.MAIN)):
        c = ds.MAIN
        blob = data[c.mon_at(i):c.mon_at(i)+48]+data[c.ot_at(i):c.ot_at(i)+11]+data[c.nick_at(i):c.nick_at(i)+11]
        m = pc.decode_party_blob(blob)
        rows.append({"slot": i, "key": pc.key(m), "species_id": pc.effective_species(m["species_id"], m["form"]),
                     "nickname": m["nickname"], "level": m["level"], "hp": m["hp"], "blob_hex": blob.hex()})
    return rows


def seed_server(path, saves, rom_sha=SHIPPED):
    from server.adapters.gen2_polished import _companion_abi
    from server.server import SLinkServer
    from server.state import LinkStatus
    (path/"rom_contract.json").write_text(json.dumps(duo.contract_for(rom_sha)))
    server = SLinkServer(data_dir=str(path), phone_calls=False)
    state = server.state
    events = []
    for role in ("a", "b"):
        ident, mons = duo.fixture_identity(saves[role]), party(saves[role])
        hello = {"event": "hello", "party": mons, "ot_id": ident["player_id"], "trainer_name": ident["name"], "has_pokeballs": True,
                 "rom_type": "polished_crystal", "foundation": "gen2_polished", "artifact_kind": "overlay",
                 "rom_sha1": rom_sha, "companion_abi": _companion_abi()}
        capture = dict(mons[0], event="capture", area_id="route_29")
        for event in (hello, capture):
            server._dispatch(role, event)  # admission also commits rom_type/artifact_kind needed by journal reload
            events.append({"player": role, "msg": event})
    assert len(state.links) == 1 and state.links[0].status == LinkStatus.ALIVE, "SYNTH link setup failed"
    state._save()
    return events


NATIVE_SITES = ("SlinkTradeApplyCommit", "SlinkTradeCommit", "ForceGameSave",
                "SlinkTradeCommit.CheckSave", "SlinkTradePublishDone", "SlinkTradeExit",
                "SlinkTradeResponderExit")


def native_sites(syms, rom):
    """Expected hook bytes come from the staged artifact, never the live hook itself."""
    sites = {}
    for name in NATIVE_SITES:
        bank, addr = syms[name]
        offset = bank * 0x4000 + addr - 0x4000 if bank else addr
        sites[name] = {"bank": bank, "pc": addr, "rom_offset": offset,
                       "expected_hex": rom[offset:offset + 4].hex()}
    return sites


def _blob(row):
    """Require the complete physical 48+11+11 record; no decoded-field fallback."""
    if not isinstance(row, dict):
        raise ValueError("record missing")
    if "blob_hex" in row:
        text = row["blob_hex"]
    else:
        fields = (("record_hex", 48), ("ot_hex", 11), ("nickname_hex", 11))
        if any(not isinstance(row.get(field), str) or not re.fullmatch(
                r"[0-9a-fA-F]{" + str(size * 2) + r"}", row[field]) for field, size in fields):
            raise ValueError("raw record/name arrays missing")
        text = "".join(row[field] for field, _ in fields)
    if not isinstance(text, str) or not re.fullmatch(r"[0-9a-fA-F]{140}", text):
        raise ValueError("full party blob missing")
    raw = bytes.fromhex(text)
    key = pc.key(pc.decode_party_blob(raw))
    if row.get("key") is not None and row["key"] != key:
        raise ValueError("record key disagrees with bytes")
    return key, raw


def _identity(snapshot):
    if "player_id_hex" in snapshot:
        text = snapshot.get("player_id_hex")
        name = snapshot.get("player_name_hex")
        if (not isinstance(text, str) or not re.fullmatch(r"[0-9a-fA-F]{4}", text)
                or not isinstance(name, str) or not re.fullmatch(r"[0-9a-fA-F]{16}", name)):
            raise ValueError("raw trainer identity missing")
        return int(text, 16), pc.decode_text(bytes.fromhex(name))
    if type(snapshot.get("ot_id")) is not int or not isinstance(snapshot.get("trainer_name"), str):
        raise ValueError("trainer identity missing")
    return snapshot["ot_id"], snapshot["trainer_name"]


def _record_reasons(role, label, snapshot, expected, identity):
    reasons = []
    try:
        if _identity(snapshot) != (identity["player_id"], identity["name"]):
            reasons.append(f"{role}: {label} identity differs")
        rows = snapshot.get("party")
        if not isinstance(rows, list) or len(rows) != len(expected):
            raise ValueError("party count differs")
        actual = dict(_blob(row) for row in rows)
        if len(actual) != len(rows) or actual != expected:
            reasons.append(f"{role}: {label} full party differs")
    except (KeyError, TypeError, ValueError) as exc:
        reasons.append(f"{role}: {label} raw evidence invalid: {exc}")
    return reasons


def _milestone_reasons(role, rows, sites=None, *, expected_token=None):
    """Count native saves/DONE inside their protocol phases, never across a whole visit."""
    rows = rows if isinstance(rows, list) else []
    reasons, permitted = [], set()
    last_frame = -1
    for row in rows:
        frame = row.get("frame") if isinstance(row, dict) else None
        if type(frame) is not int or frame < last_frame or frame < 0:
            return [f"{role}: native timeline invalid"]
        last_frame = frame

    def boundary(kind, field, name):
        matches = [(i, row) for i, row in enumerate(rows) if row.get("kind") == kind
                   and isinstance(row.get("detail"), dict) and row["detail"].get(field) == name]
        if len(matches) != 1:
            reasons.append(f"{role}: phase boundary not exactly one: {name}")
            return None
        return matches[0]

    apply = boundary("receive", "cmd", "apply_trade")
    done = boundary("send", "event", "trade_done")
    if apply is None or done is None:
        return reasons
    apply_index, apply_row = apply
    done_index, done_row = done
    token = apply_row["detail"].get("token")
    if not token or done_row["detail"].get("token") != token or (
            expected_token is not None and token != expected_token):
        reasons.append(f"{role}: APPLY token differs")
    if apply_index >= done_index:
        return reasons + [f"{role}: APPLY phase boundaries out of order"]

    def native(name, start, end):
        return [(i, row) for i, row in enumerate(rows) if start < i < end
                and row.get("kind") == "native" and row.get("detail") == name]

    def qualified(phase, name, matches, *, optional=False):
        permitted.update(i for i, _ in matches)
        if (optional and len(matches) > 1) or (not optional and len(matches) != 1):
            count = "at most one" if optional else "exactly one"
            reasons.append(f"{role}: {phase} native milestone not {count}: {name}")
            return None
        if not matches:
            return None
        index, row = matches[0]
        if (row.get("qualified") is not True or type(row.get("pc")) is not int
                or type(row.get("bank")) is not int or not isinstance(row.get("expected_hex"), str)
                or not re.fullmatch(r"(?:[0-9a-fA-F]{2})+", row["expected_hex"])):
            reasons.append(f"{role}: {phase} native milestone unqualified: {name}")
        if sites is not None and any(row.get(field) != (sites.get(name) or {}).get(field)
                                     for field in ("pc", "bank", "expected_hex")):
            reasons.append(f"{role}: {phase} native milestone differs from staged site: {name}")
        return index

    if role == "a":
        query = boundary("send", "event", "trade_query")
        if query is None:
            return reasons
        if query[0] >= apply_index:
            reasons.append(f"{role}: pre-query boundary out of order")
        qualified("pre-query", "ForceGameSave", native("ForceGameSave", -1, query[0]), optional=True)
    else:
        prompt = boundary("receive", "cmd", "show_menu")
        consent = boundary("send", "event", "menu_result")
        if prompt is None or consent is None:
            return reasons
        if not prompt[0] < consent[0] < apply_index:
            return reasons + [f"{role}: PROMPT phase boundaries out of order"]
        if (prompt[1]["detail"].get("token") != token or consent[1]["detail"].get("token") != token
                or consent[1]["detail"].get("choice") != 1):
            reasons.append(f"{role}: PROMPT token or consent differs")
        saved = qualified("PROMPT", "ForceGameSave", native("ForceGameSave", prompt[0], consent[0]))
        published = qualified("PROMPT", "SlinkTradePublishDone",
                              native("SlinkTradePublishDone", prompt[0], consent[0]))
        if saved is not None and published is not None and saved >= published:
            reasons.append(f"{role}: PROMPT native milestones out of order")

    last_index = apply_index
    for name in NATIVE_SITES[:5]:
        index = qualified("APPLY", name, native(name, apply_index, done_index))
        if index is not None:
            if index <= last_index:
                reasons.append(f"{role}: APPLY native milestone out of order: {name}")
            last_index = index
    exit_name = "SlinkTradeExit" if role == "a" else "SlinkTradeResponderExit"
    qualified("RELEASE", exit_name, native(exit_name, done_index, len(rows)))
    for i, row in enumerate(rows):
        if row.get("kind") == "native" and row.get("detail") in NATIVE_SITES and i not in permitted:
            reasons.append(f"{role}: native milestone outside its phase: {row['detail']}")
    return reasons


def judge(wire, before, cold, state, *, milestones=None, durable=None, sites=None):
    reasons = []
    original = {r: party(before[r]) for r in ("a", "b")}
    tokens = []
    milestones, durable = milestones or {}, durable or {}
    for r, other in (("a", "b"), ("b", "a")):
        events = [row["msg"] for row in wire[r] if row.get("dir") == "c2s"]
        done = [m for m in events if m.get("event") == "trade_done"]
        hellos = [m for m in events if m.get("event") == "hello"]
        applies = [c for row in wire[r] if row.get("dir") == "s2c" for c in row.get("msg", {}).get("commands", [])
                   if c.get("cmd") == "apply_trade"]
        if not hellos or [m["key"] for m in hellos[0].get("party", [])] != [m["key"] for m in original[r]]:
            reasons.append(r+": setup keys not witnessed by client hello")
        if len(applies) != 1 or not applies[0].get("token") or len(done) != 1 or done[0].get("token") != applies[0]["token"]:
            reasons.append(r+": APPLY/token not bound")
        else:
            tokens.append(applies[0]["token"])
        if len(done) != 1 or done[0].get("uncertain") or done[0].get("new_key") != original[other][0]["key"]:
            reasons.append(r+": trade_done not one exact received key")
        if any(m.get("event") in ("faint", "whiteout") for m in events):
            reasons.append(r+": false death")
        reasons.extend(_milestone_reasons(r, milestones.get(r), sites,
                                         expected_token=applies[0].get("token") if len(applies) == 1 else None))
        identity = duo.fixture_identity(before[r])
        expected = dict(_blob(mon) for mon in original[r][1:])
        received_key, incoming = _blob(original[other][0])
        incoming = bytearray(incoming)
        if not incoming[21] & 0x40:
            incoming[26] = 70
        expected[received_key] = bytes(incoming)
        reasons.extend(_record_reasons(r, "cold CONTINUE", cold.get(r) or {}, expected, identity))
        proof = durable.get(r) or {}
        copies, integrity = proof.get("copies") or {}, proof.get("integrity") or {}
        for copy in ("main", "backup"):
            if (integrity.get(copy) or {}).get("valid") is not True or (
                    integrity.get(copy) or {}).get("markers_valid") is not True:
                reasons.append(f"{r}: durable {copy} corrupt or missing")
            reasons.extend(_record_reasons(r, "durable " + copy, copies.get(copy) or {}, expected, identity))
    if len(tokens) != 2 or tokens[0] != tokens[1]:
        reasons.append("two sides did not complete the same transaction")
    links = state.get("links", [])
    if len(links) != 1 or links[0].get("status") != "alive":
        reasons.append("server link not ALIVE")
    else:
        for r, other in (("a", "b"), ("b", "a")):
            if links[0].get(r, {}).get("key") != original[other][0]["key"]:
                reasons.append("server "+r+" not re-keyed")
    if state.get("pending_trade") is not None:
        reasons.append("server trade still pending")
    return ("FAIL" if reasons else "PASS"), reasons


def replay_saved(run_dir, *, bound_artifacts=None):
    """Rejudge bound physical inputs; never trust verdict.json or an unbound save.

    Local inspection may omit bound_artifacts. The release verifier supplies its
    resolved secondary-artifact set; every source byte opened here must belong to it.
    """
    run_dir = Path(run_dir)
    bound = None if bound_artifacts is None else {Path(p).resolve() for p in bound_artifacts}

    def source(relative):
        path = run_dir / relative
        if bound is not None and path.resolve() not in bound:
            raise ValueError(f"unbound replay source: {relative}")
        return path

    try:
        manifest = json.loads(source("manifest.json").read_text())
        if manifest.get("test_build") is not False or manifest.get("rom_sha1") != overlay_sha1():
            return "FAIL", ["retained run is not the shipped overlay"]
        before = {r: source(f"before-{r}.SaveRAM").read_bytes() for r in ("a", "b")}
        wire = {r: duo.read_jsonl(source(f"wire/wire_{r}.jsonl")) for r in ("a", "b")}
        cold = {r: json.loads(source(f"{r}/g2/ready.json").read_text()) for r in ("a", "b")}
        milestones = {r: duo.read_jsonl(source(f"{r}/g1/milestones.jsonl")) for r in ("a", "b")}
        durable = {r: reload_probe.extract_save(source(f"after-{r}.SaveRAM").read_bytes()) for r in ("a", "b")}
        state = json.loads(source("state-after-trade.json").read_text())
        sites = manifest.get("native_sites")
        if not isinstance(sites, dict) or any(name not in sites for name in NATIVE_SITES):
            return "FAIL", ["retained staged native site evidence missing"]
        return judge(wire, before, cold, state, milestones=milestones, durable=durable, sites=sites)
    except (OSError, KeyError, TypeError, ValueError) as exc:
        return "FAIL", [f"retained trade evidence invalid or missing: {exc}"]


def stage_root(enabled):
    root = LANE/"client"
    shutil.copytree(ROOT/"lua", root/"lua", dirs_exist_ok=True)
    shutil.copytree(ROOT/"data/games/polished_crystal", root/"data/games/polished_crystal", dirs_exist_ok=True)
    shutil.copytree(ROOT/"data/polished", root/"data/polished", dirs_exist_ok=True)
    provenance = enabled/"data/polished/overlay_provenance.json"
    shutil.copyfile(provenance, root/"data/polished/overlay_provenance.json")
    prov = json.loads(provenance.read_text())
    path = root/"data/games/polished_crystal/profile.json"
    profile = json.loads(path.read_text())
    profile["titles"]["polished"]["overlay"]["rom_sha1"] = prov["output"]["sha1"]
    profile["source"]["overlay_sha1"] = prov["output"]["sha1"]
    profile["source"]["overlay_provenance_sha256"] = sha(provenance.read_bytes())
    # The isolated profile follows the enabled build's relocated service labels.
    from tools.build_gen2_companion import _symbols
    syms = _symbols(enabled/"data/polished/polished_slink.sym")
    def relocate(obj):
        if isinstance(obj, dict):
            if obj.get("symbol") in syms and "addr" in obj and "bank" in obj:
                obj["bank"], obj["addr"] = syms[obj["symbol"]]
            for value in obj.values():
                relocate(value)
        elif isinstance(obj, list):
            for value in obj:
                relocate(value)
    relocate(profile["titles"]["polished"]["overlay"]["trade"])
    path.write_text(json.dumps(profile, indent=2)+"\n")
    return root, syms, prov["output"]["sha1"]


def launch_trade_side(config, run_dir, env_extra, *, config_path=None, popen=None, write_config=None):
    """Record each actual side/generation at the prepared process boundary."""
    from tools.polished_live import runtime

    def observed_file(path):
        path = Path(path)
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            return {"path": path.as_posix(), "status": "absent", "sha256": None}, None
        return {"path": path.as_posix(), "status": "present", "sha256": sha(data)}, data

    def recording_popen(command, *, cwd, env):
        def actual_path(value):
            path = Path(value)
            return path if path.is_absolute() else Path(cwd) / path

        rom_path = actual_path(command[-1])
        driver = actual_path(next(arg.removeprefix("--lua=") for arg in command if arg.startswith("--lua=")))
        cfg = actual_path(next(arg.removeprefix("--config=") for arg in command if arg.startswith("--config=")))
        root, driver_root = actual_path(env["SLINK_ROOT"]), actual_path(env["POL_DRIVER_ROOT"])
        paths = {
            "rom": rom_path, "saveram": actual_path(config.sram / config.save_name),
            "configuration": cfg, "base_configuration": actual_path(config.base_config),
            "symbols": actual_path(config.sym), "generated_symbols": actual_path(env["POL_SYMS"]),
            "executed_lua_driver": driver, "normal_lua_slink": root / "lua/slink.lua",
            "driver_helper": driver_root / "tools/polished_live/pol_lib.lua",
            "trade_sites": run_dir / "trade-sites.json",
            "producer_python": Path(__file__), "runtime_python": Path(runtime.__file__),
            "duo_python": Path(duo.__file__),
            "configuration_writer_python": Path(runtime.__file__).parents[1] / "gen1_playthrough.py",
        }
        inputs, contents = {}, {}
        for name, path in paths.items():
            inputs[name], contents[name] = observed_file(path)
        rom = contents["rom"]
        gate = {"symbol": "SlinkTradeCommitEnabled", "symbols_path": paths["symbols"].as_posix(),
                "bank": None, "addr": None, "rom_offset": None, "value": None}
        if contents["symbols"] is not None:
            selected = runtime.parse_symbols(contents["symbols"].decode("utf-8")).get(gate["symbol"])
            if selected is not None:
                bank, addr = selected
                offset = bank * 0x4000 + addr - 0x4000 if bank else addr
                gate.update(bank=bank, addr=addr, rom_offset=offset,
                            value=rom[offset] if rom is not None and 0 <= offset < len(rom) else None)
        # Snapshot a conservative source/pack inventory, including transitive
        # helpers. This binds files available to the selected root; it does not
        # claim that Lua has already loaded them (the child does not exist yet).
        source_paths = set(paths.values())
        for directory, pattern in ((root / "lua", "*.lua"),
                                   (root / "data/games/polished_crystal", "*"),
                                   (root / "data/polished", "*")):
            if directory.is_dir():
                source_paths.update(path for path in directory.rglob(pattern) if path.is_file())
            else:
                source_paths.add(directory)
        source_rows = {row["path"]: row for row in inputs.values()}
        sources = [source_rows[path.as_posix()] if path.as_posix() in source_rows else observed_file(path)[0]
                   for path in sorted(source_paths)]
        record = {
            "schema": "polished-trade-launch-v1", "phase": "pre_popen",
            "launcher": {"name": driver.as_posix(),
                         "normal_entrypoint": paths["normal_lua_slink"].as_posix(),
                         "selection": {key: env.get(key) for key in ("POL_TRADE_DEV", "POL_COLD", "SLINK_PLAYER")}},
            "argv": list(command), "cwd": cwd,
            "environment": {key: value for key, value in sorted(env.items()) if key.startswith(("SLINK_", "POL_"))},
            "rom_sha1": hashlib.sha1(rom).hexdigest() if rom is not None else None,
            "gate": gate, "inputs": inputs, "source_inputs": sources,
            "source_scope": "prelaunch input inventory; not observed Lua loads",
        }
        (run_dir / "launch.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        return (subprocess.Popen if popen is None else popen)(command, cwd=cwd, env=env)

    return runtime.start_owned(config, "tools/polished_live/trade_duo_live.lua", run_dir, env_extra,
                               config_path=config_path, popen=recording_popen, write_config=write_config)


def run(args):
    from dataclasses import replace

    from tools.polished_live import harness
    from tools.polished_live.runtime import pinned_rom_bytes, read_symbols
    if args.test_build:
        enabled = LANE/"enabled"
        rom = (enabled/"cache/polished/companion-overlay/polishedcrystal-3.2.3.gbc").read_bytes()
        root, syms, rom_sha = stage_root(enabled)
    else:
        root = ROOT
        syms = read_symbols(ROOT / "data/polished/polished_slink.sym")
        rom = pinned_rom_bytes(duo.RELEASE, overlay_sha1(), patch=ROOT / "patch/dist/SLink-Polished.ups")
        rom_sha = hashlib.sha1(rom).hexdigest()
    gate_bank, gate_addr = syms["SlinkTradeCommitEnabled"]
    assert rom[gate_bank*0x4000+gate_addr-0x4000] == 1
    run_dir = args.lane/args.name
    args.lane.mkdir(parents=True, exist_ok=True)
    run_dir.mkdir(exist_ok=False)
    a = args.fixture.read_bytes()
    b, derivation = duo._derive_save().derive_identity(a, name="TradeB", player_id=53699)
    saves = {"a": a, "b": b}
    for r in saves:
        (run_dir/f"before-{r}.SaveRAM").write_bytes(saves[r])
    srvdir = run_dir/"server"
    srvdir.mkdir()
    events = seed_server(srvdir, saves, rom_sha)
    (srvdir/"rom_contract.json").write_text(json.dumps(duo.contract_for(rom_sha)))
    manifest = {"disclosure": DISCLOSURE, "derivation": derivation, "seed_events": events,
                "rom_sha1": rom_sha, "shipped_overlay_sha1": overlay_sha1(), "test_build": args.test_build,
                "gate": {"bank": gate_bank, "addr": gate_addr, "value": 1}, "source_head": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()}
    sites = native_sites(syms, rom)
    manifest["native_sites"] = sites
    (run_dir/"manifest.json").write_text(json.dumps(manifest, indent=2))
    port, http = harness.free_port(), harness.free_port()
    processes = {}
    paths = {}
    config = replace(harness.launch_config(), symbols=tuple(probe_symbols(syms)),
                     sym=(enabled if args.test_build else ROOT) / "data/polished/polished_slink.sym")
    def launch(role, cold=False):
        paths[role] = p = duo.side_paths(run_dir, role, 2 if cold else 1)
        Path(p["run"]).mkdir(parents=True)
        if not cold:
            duo.stage_side(p, rom, run_dir/f"before-{role}.SaveRAM")
        Path(p["run"], "trade-sites.json").write_text(json.dumps(sites))
        env = dict(os.environ, **duo.side_env(p, role, "127.0.0.1", port))
        env.update(SLINK_ROOT=root.as_posix(), POL_DRIVER_ROOT=ROOT.as_posix(), POL_COLD="1" if cold else "0",
                   POL_TRADE_DEV="1" if args.test_build else "0")
        side_config = replace(config, rom=Path(p["rom"]), sram=Path(p["sram_dir"]),
                              save_name=Path(p["saveram"]).name)
        proc = launch_trade_side(side_config, Path(p["run"]), env, config_path=p["config"])
        processes[role] = proc
        Path(p["run"], "pid.txt").write_text(str(proc.pid))
    with (run_dir/"server.log").open("w") as log:
        srv = subprocess.Popen([sys.executable, "-m", "server.server", "--host", "127.0.0.1", "--port", str(port),
                                "--http-port", str(http), "--data-dir", str(srvdir), "--wire-log", str(run_dir/"wire"),
                                "--no-phone-calls", "--verbose"], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic()+40
            while time.monotonic() < deadline:
                try:
                    duo.http_json(http, "/api/status")
                    break
                except Exception:
                    time.sleep(.2)
            launch("a")
            launch("b")
            deadline = time.monotonic()+args.timeout
            while time.monotonic() < deadline:
                duo.http_json(http, "/api/status")
                ready = all(Path(paths[r]["run"], "ready.json").exists() for r in ("a", "b"))
                if ready:
                    Path(paths["a"]["run"], "go").touch()
                    Path(paths["b"]["run"], "go").touch()
                if all(Path(paths[r]["run"], "done.json").exists() for r in ("a", "b")):
                    break
                if any(Path(paths[r]["run"], "failure.json").exists() for r in ("a", "b")):
                    break
                if any(p.poll() is not None for p in processes.values()):
                    break
                time.sleep(1)
            for r in processes:
                Path(paths[r]["run"], "stop").touch()
            time.sleep(3)
            for proc in processes.values():
                harness.kill_own(proc, "trade side")
            wire = {r: duo.read_jsonl(run_dir/f"wire/wire_{r}.jsonl") for r in ("a", "b")}
            state = json.loads((srvdir/"links.json").read_text())
            (run_dir/"state-after-trade.json").write_text(json.dumps(state, indent=2))
            traded = all(Path(paths[r]["run"], "done.json").exists() for r in ("a", "b"))
            cold = {}
            durable = {}
            milestones = {r: duo.read_jsonl(run_dir / r / "g1/milestones.jsonl") for r in ("a", "b")}
            if traded:
                for r in ("a", "b"):
                    data = Path(paths[r]["saveram"]).read_bytes()
                    proof = reload_probe.extract_save(data)
                    durable[r] = proof
                    (run_dir / f"after-{r}.SaveRAM").write_bytes(data)
                    (run_dir/f"durable-{r}.json").write_text(json.dumps(proof, indent=2))
                    launch(r, cold=True)
                deadline = time.monotonic()+120
                while time.monotonic() < deadline and not all(Path(paths[r]["run"], "ready.json").exists() for r in ("a", "b")):
                    time.sleep(1)
                for r in ("a", "b"):
                    path = Path(paths[r]["run"], "ready.json")
                    if path.exists():
                        cold[r] = json.loads(path.read_text())
            verdict, reasons = judge(wire, saves, cold, state, milestones=milestones, durable=durable, sites=sites)
            for role in ("a", "b"):
                for failure in (run_dir/role).glob("g*/failure.json"):
                    verdict = "FAIL"
                    reasons.append(role+": "+json.loads(failure.read_text())["reason"])
            (run_dir/"verdict.json").write_text(json.dumps({"verdict": verdict, "reasons": reasons, "cold": cold,
                                                          "milestones": milestones, "durable": durable,
                                                          "disclosure": DISCLOSURE}, indent=2))
            print(run_dir, verdict, reasons)
            return 0 if verdict == "PASS" else 1
        finally:
            for proc in processes.values():
                harness.kill_own(proc, "owned trade/cold side")
            harness.kill_own(srv, "owned trade server")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--lane", type=Path, default=LANE, help="private attempt output root")
    p.add_argument("--fixture", type=Path, default=FIXTURE)
    p.add_argument("--name", required=True, help="new private attempt name; never overwrite a receipt")
    p.add_argument("--timeout", type=int, default=600)
    p.add_argument("--test-build", action="store_true", help="explicit old isolated test build/dev composition; default uses shipped normal launcher")
    return run(p.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
