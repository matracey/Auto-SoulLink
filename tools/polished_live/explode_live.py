#!/usr/bin/env python3
"""RC1 authoring/offline oracle. --run needs the coordinator's exclusive emulator grant.

SYNTH fixture and logical server link; TEST HOST calls the real composed client's
handle_command. Route, selected action, Explosion and ResolveFaints are native.
This single-cartridge test does not qualify server Explode admission or a partner.
EXPLODE_SCOPE.md 'DECIDED 2026-10-06': eligible active action explodes; other
deaths use plain faint. Bench parity is deferred to the overworld checkpoint.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from tools.polished_live.overlay_pin import overlay_sha1  # noqa: E402

WORK = Path("F:/slink-work/lanes/pol-explodelive")
FIXTURE = Path("F:/slink-work/lanes/g2int-live/pol/fixture/polished_overlay_warp.SaveRAM")
ROUTE = REPO / "tools/polished_live/routes/faint_f3_route.json"
OVERLAY = overlay_sha1()
FIXTURE_HASH = "75c7a5dc30126f746567202cfb39fe583dfa04eb226063560541f6cbd29f36b8"
CASES = ("explode", "active-faint", "bench-faint")
DISCLOSURE = "SYNTH fixture+logical link; TEST HOST handle_command; native wild route/action/faint; no partner/capability qualification"
SYMBOLS = (
    "wPartyMons",
    "wPartyMon1",
    "wPartyMon2",
    "wPartyMonOTs",
    "wPartyMonNicknames",
    "wPartyMon1Moves",
    "wPartyMon1PP",
    "wPartyMon1Status",
    "wPartyMon1HP",
    "wPartyCount",
    "wPokemonData",
    "sPokemonData",
    "wBattleMonMoves",
    "wBattleMonPP",
    "wCurPlayerMove",
    "wCurMoveNum",
    "wCurBattleMon",
    "wBattlePlayerAction",
    "wBattleMonHP",
    "wBattleMonStatus",
    "wWhichMonFaintedFirst",
    "wPlayerSubStatus2",
    "wBattleMode",
    "wLinkMode",
    "hBattleTurn",
    "hROMBank",
    "wMapStatus",
    "wScriptRunning",
    "wGameLogicPaused",
)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def flat(row, delta=0):
    bank, address = row
    if bank == 0 and 0xC000 <= address + delta < 0xD000:
        return address + delta - 0xC000
    if bank == 1 and 0xD000 <= address + delta < 0xE000:
        return 0x1000 + address + delta - 0xD000
    raise ValueError("unexpected writer RAM mapping")


def derive(symbols, rom, explosion):
    from tools.polished_live.faint_probe import derive_contract

    contract = derive_contract(symbols, rom)
    assert symbols["wPartyMon2"][1] - symbols["wPartyMon1"][1] == 48
    sites = {k: contract["sites"][k] for k in ("faint", "copy_return")}
    for name, label, address in (
        ("hold", "BattleTurn", 0x416A),
        ("selfdestruct", "BattleCommand_selfdestruct", None),
    ):
        bank, base = symbols[label]
        at = address if address is not None else base
        offset = bank * 0x4000 + at - 0x4000
        target = symbols[
            "DetermineMoveOrder" if name == "hold" else "GetOpponentAbilityAfterMoldBreaker"
        ][1]
        wanted = bytes((0xCD, target & 255, target >> 8))
        if rom[offset : offset + 3] != wanted or (name == "hold" and bank != 15):
            raise ValueError(f"{name}: instruction drift")
        sites[name] = {"bank": bank, "addr": at, "rom_offset": offset, "bytes": wanted.hex()}
    geometry = {
        k: symbols[label][1] - symbols["wPartyMons"][1]
        for k, label in (
            ("moves", "wPartyMon1Moves"),
            ("pp", "wPartyMon1PP"),
            ("status", "wPartyMon1Status"),
            ("hp", "wPartyMon1HP"),
        )
    }
    if geometry != {"moves": 2, "pp": 22, "status": 32, "hp": 34}:
        raise ValueError("native party geometry drift")
    return {
        "schema": "polished-explode-live-v1",
        "sites": sites,
        "geometry": geometry,
        "symbols": {k: symbols[k] for k in SYMBOLS},
        "stride": 48,
        "explosion": explosion,
        "snapshot_sizes": {"WRAM": 32768, "CartRAM": 32768, "VRAM": 16384, "OAM": 160, "HRAM": 127},
    }


def saved_party(raw, symbols):
    from server.adapters import polished_codec as pc

    bank, base = symbols["sPokemonData"]
    start = bank * 8192 + base - 0xA000

    def read(label, delta, length):
        at = start + symbols[label][1] - symbols["wPokemonData"][1] + delta
        return raw[at : at + length]

    count = read("wPartyCount", 0, 1)[0]
    if not 3 <= count <= 6:
        raise ValueError("fixture needs three occupied party slots")
    mons = []
    for slot in range(count):
        blob = (
            read("wPartyMons", slot * 48, 48)
            + read("wPartyMonOTs", slot * 11, 11)
            + read("wPartyMonNicknames", slot * 11, 11)
        )
        mon = pc.decode_party_blob(blob)
        if mon["is_egg"] or mon["hp"] <= 0:
            raise ValueError("fixture target must be a living non-egg")
        mons.append({"key": pc.key(mon), "species": mon["species_id"], "level": mon["level"]})
    return mons


def verify_sources(config):
    for path, want in config["provenance"]["source_inputs"].items():
        if sha((REPO / path).read_bytes()) != want:
            raise ValueError("source changed: " + path)


def prepare(case, fixture=FIXTURE, route=ROUTE):
    from tools.polished_live.faint_probe import RELEASE, prepare as prepare_faint, read_symbols
    from tools.polished_live.rival_gate_probe import parse_route

    rom, _, proof = prepare_faint()
    if hashlib.sha1(rom).hexdigest() != OVERLAY:
        raise ValueError("overlay differs from RC1 cut")
    raw = Path(fixture).read_bytes()
    if len(raw) not in (32768, 32790) or sha(raw) != FIXTURE_HASH:
        raise ValueError("fixture differs from SYNTH75c7a5dc input")
    symbols = read_symbols(REPO / "data/polished/polished_slink.sym")
    moves = json.loads((REPO / "data/games/polished_crystal/moves.json").read_text())
    (explosion,) = [m["id"] for m in moves["moves"] if m["constant"] == "EXPLOSION"]
    contract = derive(symbols, rom, explosion)
    mons = saved_party(raw, symbols)
    slot = 2 if case == "bench-faint" else 0
    from tools.polished_live.rival_swap_live import check_route_calibration

    check_route_calibration(route, OVERLAY)
    steps = parse_route(Path(route), 20000)
    proof.update(
        contract_sha256=sha(json.dumps(contract, sort_keys=True, separators=(",", ":")).encode()),
        driver_sha256=sha(Path(__file__).with_suffix(".lua").read_bytes()),
        oracle_sha256=sha(Path(__file__).read_bytes()),
        fixture_sha256=sha(raw),
        route_sha256=sha(json.dumps(steps, sort_keys=True).encode()),
        release=str(RELEASE),
        source_inputs={
            str(p.relative_to(REPO)).replace("\\", "/"): sha(p.read_bytes())
            for folder in (
                "lua",
                "server",
                "data/games/polished_crystal",
                "data/polished",
                "tools/polished_live",
            )
            for p in sorted((REPO / folder).rglob("*"))
            if p.is_file() and p.suffix in (".lua", ".py", ".json", ".sym")
        },
    )
    proof["source_inputs"]["data/polished_sources.lock.json"] = sha(
        (REPO / "data/polished_sources.lock.json").read_bytes()
    )
    config = {
        "case": case,
        "contract": contract,
        "provenance": proof,
        "steps": steps,
        "target": {**mons[slot], "slot": slot},
        "partner": mons[-1],
        "disclosure": DISCLOSURE,
        "frame_cap": 14000,
    }
    return rom, raw, config


def expected_writes(config, before):
    c, slot = config["contract"], config["target"]["slot"]
    s = c["symbols"]
    ram = bytes.fromhex(before["WRAM"])

    def at(label, delta=0):
        return s[label][1] + delta

    def byte(label, delta=0):
        return ram[flat(s[label], delta)]

    if config["case"] == "explode":
        m = byte("wCurMoveNum")
        if m > 3:
            raise ValueError("invalid selected move slot")
        return [
            (at("wBattleMonMoves", m), c["explosion"]),
            (at("wBattleMonPP", m), byte("wBattleMonPP", m) & 0xC0 | 1),
            (at("wPartyMons", slot * 48 + c["geometry"]["moves"] + m), c["explosion"]),
            (
                at("wPartyMons", slot * 48 + c["geometry"]["pp"] + m),
                byte("wPartyMons", slot * 48 + c["geometry"]["pp"] + m) & 0xC0 | 1,
            ),
            (at("wCurPlayerMove"), c["explosion"]),
        ]
    party = [(at("wPartyMons", slot * 48 + offset), 0) for offset in (32, 34, 35)]
    if config["case"] == "bench-faint":
        return party
    return [
        (at("wBattleMonStatus"), 0),
        *party,
        (at("wBattleMonHP"), 0),
        (at("wBattleMonHP", 1), 0),
    ] + ([(at("wWhichMonFaintedFirst"), 1)] if byte("wWhichMonFaintedFirst") == 0 else [])


def evaluate(trace, config):
    """Pure consumer: recompute diffs/expected bytes; never trust producer PASS/allowed claims."""
    why = []
    try:
        if not isinstance(trace, list) or [e["ord"] for e in trace] != list(
            range(1, len(trace) + 1)
        ):
            raise ValueError("ordered trace required")
        if any(a["frame"] > b["frame"] for a, b in zip(trace, trace[1:], strict=False)):
            raise ValueError("frame order")
        if trace[0]["kind"] != "begin" or trace[0]["provenance"] != config["provenance"]:
            raise ValueError("provenance")
        if trace[-1]["kind"] != "final" or trace[-1]["completed"] is not True:
            raise ValueError("incomplete")
        allowed = {
            "begin",
            "route",
            "route_tail",
            "command",
            "operation",
            "scope_begin",
            "bench_hold",
            "native",
            "wrong_bank",
            "consumer",
            "wire",
            "hud",
            "final",
        }
        if any(e["kind"] not in allowed for e in trace):
            raise ValueError("driver error/unknown event")
        if (
            trace[0].get("disclosure") != DISCLOSURE
            or trace[-1].get("driver_errors") != 0
            or trace[-1].get("post_operation_writes") != 0
        ):
            raise ValueError("disclosure/callback failures")
        routes = [e for e in trace if e["kind"] == "route"]
        if [(e["step"], e["frames"], e["buttons"]) for e in routes] != [
            (i + 1, step["frames"], step["buttons"]) for i, step in enumerate(config["steps"])
        ]:
            raise ValueError("complete pinned native route required")
        if (
            any(
                b["frame"] - a["frame"] != a["frames"]
                for a, b in zip(routes, routes[1:], strict=False)
            )
            or trace[-1]["frame"] - routes[-1]["frame"] < routes[-1]["frames"]
        ):
            raise ValueError("route elapsed frames")
        commands = [e for e in trace if e["kind"] == "command"]
        operations = [e for e in trace if e["kind"] == "operation"]
        if len(commands) != 1 or len(operations) != 1:
            raise ValueError("one command/operation required")
        cmd, op = commands[0], operations[0]
        target = config["target"]
        if cmd["key"] != target["key"] or cmd["slot"] != target["slot"] or cmd["mode"] != 1:
            raise ValueError("command binding")
        if cmd["cmd"] != ("force_explode" if config["case"] == "explode" else "force_faint"):
            raise ValueError("command kind")
        if op["ord"] <= cmd["ord"] or op["key"] != target["key"]:
            raise ValueError("operation binding/order")
        scopes = [e for e in trace if e["kind"] == "scope_begin" and e["ord"] == op["scope_ord"]]
        if (
            len(scopes) != 1
            or scopes[0]["context"] != op["context"]
            or scopes[0]["key"] != target["key"]
            or not cmd["ord"] < op["scope_ord"] < op["ord"]
        ):
            raise ValueError("scope ordering")
        pre = bytes.fromhex(op["before"]["WRAM"])
        hr = bytes.fromhex(op["before"]["HRAM"])
        for field, label in (
            ("mode", "wBattleMode"),
            ("active", "wCurBattleMon"),
            ("action", "wBattlePlayerAction"),
            ("map_status", "wMapStatus"),
            ("script", "wScriptRunning"),
            ("link", "wLinkMode"),
        ):
            if op[field] != pre[flat(config["contract"]["symbols"][label])]:
                raise ValueError("guard/preimage mismatch " + field)
        for field, label in (("bank", "hROMBank"), ("turn", "hBattleTurn")):
            if op[field] != hr[config["contract"]["symbols"][label][1] - 0xFF80]:
                raise ValueError("HRAM guard/preimage mismatch")
        if op["link"] != 0 or op["party_hp"] <= 0:
            raise ValueError("linked mode/dead preimage")
        at = flat(config["contract"]["symbols"]["wPartyMons"], target["slot"] * 48 + 34)
        if op["party_hp"] != int.from_bytes(pre[at : at + 2], "big"):
            raise ValueError("HP/preimage mismatch")
        want = expected_writes(config, op["before"])
        if [(w["addr"], w["value"]) for w in op["writes"]] != want or any(
            w["domain"] != "System Bus" for w in op["writes"]
        ):
            raise ValueError("exact write plan/order")
        permitted = {("WRAM", flat((0 if a < 0xD000 else 1, a))) for a, _ in want}
        changed = []
        for domain, size in config["contract"]["snapshot_sizes"].items():
            a, b = bytes.fromhex(op["before"][domain]), bytes.fromhex(op["after"][domain])
            if len(a) != size or len(b) != size:
                raise ValueError("snapshot size")
            changed.extend((domain, n) for n, (x, y) in enumerate(zip(a, b, strict=True)) if x != y)
        if not changed or any(x not in permitted for x in changed):
            raise ValueError("diff outside independently derived set/empty")
        ram = bytes.fromhex(op["after"]["WRAM"])
        if any(ram[flat((0 if a < 0xD000 else 1, a))] != v for a, v in want):
            raise ValueError("write postimage")
        consumption = op["ord"]
        if config["case"] == "bench-faint":
            holds = [e for e in trace if e["kind"] == "bench_hold"]
            if not holds or any(
                e["writes"] or e["changed"] or e["before"] != e["after"] for e in holds
            ):
                raise ValueError("bench battle write")
            for e in holds:
                raw = bytes.fromhex(e["before"]["WRAM"])
                hr = bytes.fromhex(e["before"]["HRAM"])
                sym = config["contract"]["symbols"]
                if (
                    not cmd["ord"] < e["ord"] < op["scope_ord"]
                    or len(raw) != 32768
                    or len(hr) != 127
                ):
                    raise ValueError("bench hold causal order/image")
                for field, label in (("mode", "wBattleMode"), ("active", "wCurBattleMon")):
                    if e[field] != raw[flat(sym[label])]:
                        raise ValueError("bench guard/preimage mismatch")
                at = flat(sym["wPartyMons"], target["slot"] * 48 + 34)
                if (
                    e["party_hp"] != int.from_bytes(raw[at : at + 2], "big")
                    or e["bank"] != hr[sym["hROMBank"][1] - 0xFF80]
                    or raw[flat(sym["wLinkMode"])] != 0
                ):
                    raise ValueError("bench HP/bank/link preimage")
            hold = config["contract"]["sites"]["hold"]
            if any(
                e["mode"] != 1
                or e["active"] == target["slot"]
                or e["pc"] != hold["addr"]
                or e["bank"] != hold["bank"]
                or e["party_hp"] <= 0
                for e in holds
            ):
                raise ValueError("bench hold qualification")
            if (
                op["context"] != "overworld"
                or op["mode"] != 0
                or op["map_status"] != 2
                or op["script"] != 0
            ):
                raise ValueError("bench checkpoint")
        else:
            hold = config["contract"]["sites"]["hold"]
            if (
                op["context"] != "battle"
                or op["pc"] != hold["addr"]
                or op["bank"] != hold["bank"]
                or op["action"] != 0
                or op["active"] != target["slot"]
            ):
                raise ValueError("qualified USEMOVE hold")
            needed = (
                ["selfdestruct", "faint", "copy_return"]
                if config["case"] == "explode"
                else ["faint", "copy_return"]
            )
            cursor = op["ord"]
            for name in needed:
                rows = [
                    e
                    for e in trace
                    if e["kind"] == "native"
                    and e["site"] == name
                    and e["ord"] > cursor
                    and (name == "copy_return" or e["turn"] == 0)
                    and e["slot"] == target["slot"]
                    and e["key"] == target["key"]
                ]
                if not rows:
                    raise ValueError(f"native {name} missing")
                e = rows[0]
                site = config["contract"]["sites"][name]
                if (e["bank"], e["pc"], e["bytes"]) != (site["bank"], site["addr"], site["bytes"]):
                    raise ValueError("native hook qualification")
                if name != "selfdestruct" and e["battle_hp"] != 0:
                    raise ValueError("native faint HP")
                if name == "copy_return" and (e["party_hp"] != 0 or not e["fainted"]):
                    raise ValueError("native copyback")
                if name == "selfdestruct" and e["move"] != config["contract"]["explosion"]:
                    raise ValueError("native Explosion selection")
                if name == "selfdestruct":
                    consumption = e["ord"]
                cursor = e["ord"]
            if config["case"] == "active-faint":
                consumers = [e for e in trace if e["kind"] == "consumer" and e["ord"] > cursor]
                if not consumers:
                    raise ValueError("bound post-copy consumer missing")
                consumer = consumers[0]
                battle = consumer["battle"]
                if (
                    battle["slot"] != target["slot"]
                    or battle["mode"] != 1
                    or battle["link_mode"] != 0
                    or battle["hp"] != 0
                    or battle["status"] != 0
                    or battle["fainted"] is not True
                ):
                    raise ValueError("consumer native witness")
                capture = consumer["capture"]
                attempt = op["attempt"]
                if any(
                    type(record[k]) is not int or record[k] < 0
                    for record, keys in (
                        (capture, ("epoch", "visit", "generation", "attempt_seq", "seq")),
                        (attempt, ("epoch", "visit", "generation", "seq")),
                    )
                    for k in keys
                ):
                    raise ValueError("capture clock types")
                if (
                    consumer["batch_generation"] != attempt["epoch"]
                    or consumer["phase"] != "after_party_copyback"
                    or capture["key"] != target["key"]
                    or any(capture[k] != attempt[k] for k in ("identity", "epoch", "visit"))
                    or capture["generation"] != attempt["generation"]
                    or capture["attempt_seq"] != attempt["seq"]
                    or capture["seq"] <= attempt["seq"]
                ):
                    raise ValueError("post-copy capture binding")
                consumption = consumer["ord"]
        hud = [e for e in trace if e["kind"] == "hud" and "QUAL_TARGET KO'd" in e["text"]]
        ticks = [
            e
            for e in trace
            if e["kind"] == "wire"
            and e["sent"] is True
            and e["message"].get("event") == "tick"
            and any(
                m.get("key") == target["key"] and m.get("hp") == 0
                for m in e["message"].get("party", [])
            )
        ]
        if (
            len(hud) != 1
            or not ticks
            or hud[0]["ord"] <= (op["scope_ord"] if config["case"] == "bench-faint" else op["ord"])
            or (config["case"] == "bench-faint" and hud[0]["ord"] >= op["ord"])
            or hud[0].get("dead") is not True
        ):
            raise ValueError("one local settlement")
        if config["case"] != "bench-faint" and not any(
            consumption < e["ord"] < hud[0]["ord"] for e in ticks
        ):
            raise ValueError("sent HP0 tick after native consumption, before settlement")
        if config["case"] == "active-faint" and hud[0]["fs_seq"] <= capture["seq"]:
            raise ValueError("settlement clock")
        if any(e["kind"] == "wire" and e["message"].get("event") == "faint" for e in trace):
            raise ValueError("commanded faint echo")
    except (ValueError, KeyError, TypeError, IndexError) as error:
        why.append(str(error))
    return not why, why


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--case", choices=CASES, required=True)
    ap.add_argument("--fixture", type=Path, default=FIXTURE)
    ap.add_argument("--route", type=Path, default=ROUTE)
    ap.add_argument("--lane", type=Path, default=WORK)
    ap.add_argument("--timeout", type=int, default=600)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--run", action="store_true")
    mode.add_argument("--trace", type=Path)
    args = ap.parse_args(argv)
    if args.timeout <= 0:
        ap.error("positive timeout required")
    rom, fixture, config = prepare(args.case, args.fixture, args.route)
    if args.trace:
        ok, why = evaluate(json.loads(args.trace.read_text()), config)
        print(json.dumps({"ok": ok, "reasons": why}))
        return 0 if ok else 1
    if args.dry_run:
        print(json.dumps(config))
        return 0
    # Allocate fresh evidence; do not overwrite a failed/stale attempt or external SaveRAM.
    root = args.lane.resolve()
    root.mkdir(parents=True, exist_ok=True)
    lane = root / (args.case + "-" + uuid.uuid4().hex[:12])
    lane.mkdir()
    from dataclasses import replace

    from tools.polished_live import harness
    launch_config = replace(harness.launch_config(dict(os.environ, POL_LANE=str(lane), POL_KIND="overlay")),
                            symbols=tuple(dict.fromkeys((*harness.SYMBOLS, *config["contract"]["symbols"]))))
    launch_config.rom.parent.mkdir()
    launch_config.rom.write_bytes(rom)
    launch_config.sram.mkdir(parents=True)
    (launch_config.sram / launch_config.save_name).write_bytes(fixture)
    run = lane / "probe"
    run.mkdir()
    (run / "input.json").write_text(json.dumps(config))
    # Logical test link only; no native partner cartridge is claimed.
    from server.adapters.gen2_polished import Gen2PolishedAdapter
    from server.state import LinkEntry, LinkStatus, MonInfo, SoulLinkState

    state = SoulLinkState(
        data_dir=str(run / "server"), adapter=Gen2PolishedAdapter(artifact_kind="overlay")
    )
    state.rom_type, state.artifact_kind = "polished_crystal", "overlay"
    entry = LinkEntry(
        area_id="route_29",
        a=MonInfo(**{k: config["target"][k] for k in ("key", "species", "level")}),
        b=MonInfo(**config["partner"]),
        status=LinkStatus.ALIVE,
    )
    state.links.append(entry)
    state._index_entry(entry)
    state._save()
    (run / "server/rom_contract.json").write_text(
        json.dumps({"players": {"a": {"rom_sha1": OVERLAY}}})
    )
    port = harness.free_port()
    http = harness.free_port()
    log = (run / "server.log").open("w")
    server = None
    try:
        verify_sources(config)
        server = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "server.server",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--http-port",
                str(http),
                "--data-dir",
                str(run / "server"),
            ],
            cwd=REPO,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        started = time.monotonic()
        text, pid = harness.launch(
            launch_config,
            "tools/polished_live/explode_live.lua",
            run,
            {
                "POL_EXPLODE_CONFIG": str(run / "input.json"),
                "SLINK_HOST": "127.0.0.1",
                "SLINK_PORT": str(port),
            },
            args.timeout,
        )
        verify_sources(config)
        trace = json.loads((run / "trace.json").read_text())
        ok, why = evaluate(trace, config)
        markers = [x for x in text.splitlines() if x.startswith("RESULT:")]
        if (
            len(markers) != 1
            or not markers[0].startswith(f"RESULT: PASS explode-live {args.case} (0 checks failed)")
            or time.monotonic() - started >= args.timeout
        ):
            ok = False
            why.append("recording completion/deadline")
    except Exception as error:
        ok, why, pid = False, [f"recorder failed: {type(error).__name__}: {error}"], None
    finally:
        if server is not None and server.poll() is None:
            harness.kill_own(server, "private explode server")
        log.close()
    (run / "oracle.json").write_text(
        json.dumps({"ok": ok, "reasons": why, "pid": pid, "config": config}, indent=2)
    )
    print(json.dumps({"lane": str(lane), "ok": ok, "reasons": why}))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.path.insert(0, str(REPO))
    raise SystemExit(main())
