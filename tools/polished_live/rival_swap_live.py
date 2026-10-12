#!/usr/bin/env python3
"""Client/server rival swap recorder. Authoring/dry-run does NOT authorize EmuHawk.

SYNTH: scene SaveRAM, and a scripted TCP partner derived with derive_save's
second-identity builder. Native: buttons, battle, production client writer.
No direct command injection: server.server --rival-team-swap handles the client's
trainer_battle_start (server/server.py:5776,5803; state.py:4597-4652).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tools.polished_live.overlay_pin import overlay_sha1  # noqa: E402

sys.path.insert(0, str(ROOT))
from server.adapters import polished_codec as pc  # noqa: E402
from tools.polished_live import duo, rival_gate_probe as probe  # noqa: E402

SHA1 = overlay_sha1()
FIXTURE = Path("F:/slink-work/lanes/pol-rival-live/fixture/rival.SaveRAM")
DISCLOSURE = Path("F:/slink-work/lanes/pol-rival-live/out/disclosure.json")
ROUTE = Path("F:/slink-work/lanes/pol-rival-live/out/calib-68894579/synth-o562jtrj/probe/route.json")
SYMBOLS = ("wOTPartyCount", "wOTPartyMons", "wOTPartyMonOTs", "wOTPartyMonNicknames",
           "wOTPartyDataEnd", "hBattleTurn", "wOtherTrainerClass", "wOtherTrainerID",
           "wCurOTMon", "wCurPartyMon", "wEnemyMonSpecies", "wEnemyMonForm",
           "wEnemyMonLevel", "wEnemyMonMoves", "wEnemyMonHP", "LoadBattleMenu")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def check_route_calibration(route, overlay_sha1=SHA1):
    """Fail fast when a probe-calibrated route was calibrated on a different overlay.

    A route written by rival_gate_probe --calibrate sits beside calibration.json and the probe's
    result.txt, which names the overlay it ran ("[probe] overlay sha1 <40 hex>"). Boot timing (title
    screen) moves with the overlay, so a route calibrated elsewhere burns a full emulator run to come
    back OPEN. Hand-authored routes have no calibration.json and are not judged here (absent skips);
    a calibrated route whose overlay cannot be read, or differs, is refused (present-but-wrong fails).
    """
    route = Path(route)
    if not (route.parent / "calibration.json").is_file():
        return None
    result = route.parent / "result.txt"
    found = re.findall(r"overlay sha1 ([0-9a-f]{40})", result.read_text(errors="replace")) if result.is_file() else []
    if len(set(found)) != 1:
        raise ValueError(f"calibrated route {route} has no single overlay sha1 in {result.name}: cannot verify")
    if found[0] != overlay_sha1.lower():
        raise ValueError(f"route {route} was calibrated on overlay {found[0]}, not {overlay_sha1}: recalibrate")
    return found[0]


def symbols():
    from tools.polished_live.runtime import read_symbols
    return read_symbols(ROOT / "data/polished/polished_slink.sym", SYMBOLS)


def partner_from_save(data):
    """Disclosed second identity, no fabricated mon stats/moves; raw saved records."""
    ds = duo._derive_save()
    derived, disclosure = ds.derive_identity(data, name="RivalB", player_id=53699)
    a, b = duo.fixture_identity(data), duo.fixture_identity(derived)
    if duo.distinct_refusals(a, b):
        raise ValueError("scripted partner must have a distinct save identity")
    party = []
    c = ds.MAIN
    for slot in range(ds.party_count(derived, c)):
        blob = (derived[c.mon_at(slot):c.mon_at(slot)+48]
                + derived[c.ot_at(slot):c.ot_at(slot)+11]
                + derived[c.nick_at(slot):c.nick_at(slot)+11])
        mon = pc.decode_party_blob(blob)
        if mon["is_egg"]:
            raise ValueError("partner fixture includes an egg")
        party.append({"key": pc.key(mon), "slot": slot, "species_id": pc.effective_species(mon["species_id"], mon["form"]),
                      "level": mon["level"], "hp": mon["hp"], "max_hp": mon["max_hp"],
                      "nickname": mon["nickname"], "blob_hex": blob.hex()})
    from server.adapters.gen2_polished import _companion_abi
    hello = {"event": "hello", "player": "b", "seq": 1, "rom_type": "polished_crystal",
             "artifact_kind": "overlay", "foundation": "gen2_polished", "rom_sha1": SHA1,
             "companion_abi": _companion_abi(), "ot_id": b["player_id"], "trainer_name": b["name"],
             "party": party, "has_pokeballs": True, "in_battle": False, "area_id": 0,
             "pc_boxes": [], "pc_boxes_generation": 1, "writes_enabled": False}
    return hello, disclosure


def expected_plan(blobs):
    raw = [bytes.fromhex(b) for b in blobs]
    if not 1 <= len(raw) <= 6 or any(len(b) != 70 for b in raw):
        raise ValueError("invalid partner blobs")
    raw.sort(key=lambda b: int.from_bytes(b[34:36], "big") == 0)  # client living-first stable order
    syms = symbols()
    spans = []
    for label, lo, hi in (("wOTPartyMons", 0, 48), ("wOTPartyMonOTs", 48, 59), ("wOTPartyMonNicknames", 59, 70)):
        spans.append({"addr": syms[label][1], "bytes": list(b"".join(b[lo:hi] for b in raw))})
    spans.append({"addr": syms["wOTPartyCount"][1], "bytes": [len(raw)]})
    return raw, spans


def evaluate(trace, wire, hello, binding):
    """Strict complete-recording oracle; missing gate is OPEN, never PASS.

    Independent expected image comes from the scripted partner's normal hello,
    corroborated by the real server wire, not from a recorder-supplied expected image.
    """
    try:
        why = []
        if not isinstance(trace, list) or not trace or any(not isinstance(r, dict) for r in trace):
            raise ValueError("trace must be nonempty object list")
        if [r.get("ord") for r in trace] != list(range(1, len(trace)+1)):
            why.append("ORDINALS")
        if any(r.get("kind") not in {"start", "command", "reply", "battle_start", "writer_begin", "writer_end",
                                     "write", "post", "continued", "error", "overflow", "final"} for r in trace):
            why.append("UNKNOWN_ROW")
        if any(type(r.get("frame")) is not int for r in trace) or any(a["frame"] > b["frame"] for a, b in zip(trace, trace[1:], strict=False)):
            why.append("FRAME_ORDER")
        groups = {k: [r for r in trace if r.get("kind") == k] for k in
                  ("final", "writer_begin", "writer_end", "write", "post", "continued", "error", "overflow")}
        final = groups["final"]
        if len(final) != 1 or trace[-1] is not final[0] or final[0].get("completed") is not True:
            why.append("INCOMPLETE")
        elif final[0].get("writes") != len(groups["write"]) or final[0].get("overflow") != 0:
            why.append("CENSORED")
        if groups["error"] or groups["overflow"]:
            why.append("RECORDER_ERROR")
        if trace[0].get("kind") != "start" or trace[0].get("rom_sha1", "").lower() != SHA1 or trace[0].get("setup") != "SYNTH":
            why.append("PROVENANCE")
        for field in ("fixture_sha256", "route_sha256", "disclosure_sha256"):
            want = binding[field]
            if not isinstance(want, str) or len(want) != 64 or any(c not in "0123456789abcdef" for c in want) or trace[0].get(field) != want:
                why.append("INPUT_BINDING: " + field)
        raw, spans = expected_plan([m["blob_hex"] for m in hello["party"]])
        plan = [(s["addr"]+i, b) for s in spans for i, b in enumerate(s["bytes"])]
        commands = [c for r in wire if r.get("dir") == "s2c" for c in r.get("msg", {}).get("commands", [])
                    if c.get("cmd") == "replace_rival_team"]
        replies = [r["msg"] for r in wire if r.get("dir") == "c2s" and r.get("msg", {}).get("event") == "rival_team_replaced"]
        if not groups["writer_begin"] and not commands and not replies and not groups["write"]:
            return ("FAIL" if why else "OPEN"), why + ["NO_SWAP"]
        if (len(commands) != 1 or commands[0].get("source") != "auto" or commands[0].get("trainer_id") != 0x1B03
                or commands[0].get("n") != len(raw) or commands[0].get("blobs_hex") != [m["blob_hex"] for m in hello["party"]]):
            why.append("SERVER_COMMAND")
        if len(replies) != 1 or replies[0].get("error") or replies[0].get("trainer_id") != 0x1B03 or replies[0].get("species_ids") != [b[0] for b in raw]:
            why.append("REPLY")
        rx = [r for r in trace if r.get("kind") == "command"]
        tx = [r for r in trace if r.get("kind") == "reply"]
        if len(rx) != 1 or not commands or rx[0].get("msg") != commands[0] or len(tx) != 1 or not replies or tx[0].get("msg") != replies[0]:
            why.append("WIRE_CORROBORATION")
        if not groups["writer_begin"]:
            return ("FAIL" if why else "OPEN"), why + ["NO_SWAP"]
        if len(groups["writer_begin"]) != 1 or len(groups["writer_end"]) != 1:
            why.append("SECOND_WRITE")
            return "FAIL", why
        begin, end = groups["writer_begin"][0], groups["writer_end"][0]
        if begin.get("declared_spans") != [[s["addr"], len(s["bytes"])] for s in spans]:
            why.append("DECLARED_SPANS")
        if end.get("ok") is not True:
            why.append("WRITER_FAILED")
        if (begin.get("bank"), begin.get("pc"), begin.get("side"), begin.get("mode"), begin.get("trainer")) != (15, 0x47DD, 1, 2, 0x1B03):
            why.append("OPERATION_PREDICATE")
        if not rx or not (rx[0]["ord"] < begin["ord"] < end["ord"]):
            why.append("COMMAND_TOO_LATE")
        if not tx or tx[0]["ord"] <= end["ord"]:
            why.append("REPLY_BEFORE_WRITE")
        writes = groups["write"]
        if [(w.get("addr"), w.get("value")) for w in writes] != plan:
            why.append("WRITE_PLAN")
        if any(w.get("domain") != "System Bus" or w.get("api") != "write_u8" or w.get("pc") != 0x47DD
               or w.get("bank") != 15 or not begin["ord"] < w["ord"] < end["ord"] for w in writes):
            why.append("OUTSIDE_WRITER")
        before, after = bytes.fromhex(begin["wram"]), bytes.fromhex(end["wram"])
        if len(before) != 32768 or len(after) != 32768:
            why.append("WRAM_SIZE")
        else:
            want = bytearray(before)
            for addr, value in plan:
                want[addr-0xC000] = value  # all declared writer spans are WRAM bank 1
            if bytes(want) != after:
                why.append("OUTSIDE_SPAN_OR_WRONG_IMAGE")
        posts = [p for p in groups["post"] if p["ord"] > end["ord"]]
        if not posts:
            why.append("NO_POST_480D")
        else:
            post = posts[0]
            if (post.get("bank"), post.get("pc"), post.get("side"), post.get("trainer")) != (15, 0x480D, 1, 0x1B03):
                why.append("POST_CONTEXT")
            if post.get("image") != [s["bytes"] for s in spans]:
                why.append("POST_IMAGE")
        continued = groups["continued"]
        mon = pc.decode_party_blob(raw[0])
        expected_enemy = {"species": mon["species_id"], "level": mon["level"], "moves": mon["moves"], "hp": mon["hp"]}
        if len(continued) != 1 or not posts or continued[0]["ord"] <= posts[0]["ord"] or continued[0].get("enemy") != expected_enemy:
            why.append("NATIVE_CONTINUATION")
        elif (continued[0].get("bank"), continued[0].get("pc")) != tuple(symbols()["LoadBattleMenu"]) or continued[0].get("trainer") != 0x1B03 or continued[0].get("mode") != 2:
            why.append("CONTINUATION_CONTEXT")
        return ("FAIL" if why else "PASS"), why
    except (KeyError, ValueError, TypeError, IndexError, AttributeError) as exc:
        return "FAIL", ["MALFORMED: " + str(exc)]


class Partner:
    """A second TCP identity, using normal hello/tick wire vocabulary, never state mutation."""
    def __init__(self, port, hello):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=5)
        self.file = self.sock.makefile("rb")
        self.hello, self.seq, self.error = hello, 0, None
        self.stop = threading.Event()
        self.responses = []

    def send(self, event):
        self.seq += 1
        msg = dict(self.hello, event=event, seq=self.seq)
        self.sock.sendall((json.dumps(msg)+"\n").encode())
        reply = json.loads(self.file.readline())
        self.responses.append(reply)
        if any(c.get("refused") or c.get("cmd") == "identity_error" for c in reply.get("commands", [])):
            raise ValueError(f"partner refused: {reply}")
        if event == "hello" and not any(c.get("cmd") == "config" for c in reply.get("commands", [])):
            raise ValueError(f"partner hello not admitted: {reply}")
        return reply

    def start(self):
        self.send("hello")
        def run():
            try:
                while not self.stop.wait(1):
                    self.send("tick")
            except Exception as exc:
                self.error = str(exc)
        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()

    def close(self):
        self.stop.set()
        if hasattr(self, "thread"):
            self.thread.join(6)
        self.file.close()
        self.sock.close()


def server_command(port, http, data, wire):
    return [sys.executable, "-m", "server.server", "--host", "127.0.0.1", "--port", str(port),
            "--http-port", str(http), "--data-dir", str(data), "--wire-log", str(wire),
            "--rival-team-swap", "--no-phone-calls", "--verbose"]


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--fixture", type=Path, default=FIXTURE)
    p.add_argument("--disclosure", type=Path, default=DISCLOSURE)
    p.add_argument("--route", type=Path, default=ROUTE)
    p.add_argument("--out", type=Path, default=Path("F:/slink-work/lanes/pol-rivalswaplive/out"))
    p.add_argument("--frames", type=int, default=18000)
    p.add_argument("--timeout", type=int, default=600)
    p.add_argument("--fixed-route", action="store_true",
                   help="replay the route file's frame steps verbatim (desyncs on random wild encounters once the "
                        "client is live); default is WRAM-verified feedback navigation inside the client session")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--rejudge", type=Path, help="existing run directory; original receipts are untouched")
    args = p.parse_args(argv)
    if args.rejudge:
        d = args.rejudge
        result = evaluate(json.loads((d/"trace.json").read_text()), duo.read_jsonl(d/"wire/wire_a.jsonl"),
                          json.loads((d/"partner.json").read_text()), json.loads((d/"input.json").read_text()))
        print(json.dumps({"analysis": "OFFLINE", "verdict": result[0], "reasons": result[1]}))
        return int(result[0] != "PASS")
    if not 1 <= args.frames <= 60000 or not 1 <= args.timeout <= 1800:
        p.error("frames/timeout outside bounded limits")
    data = args.fixture.read_bytes()
    probe.load_disclosure(args.disclosure, data)
    check_route_calibration(args.route)
    # Earlier Lua route files encode an empty array as {}; accept ONLY that empty object.
    route = json.loads(args.route.read_text())
    if set(route) != {"steps"}:
        raise ValueError("route fields")
    steps = []
    for s in route["steps"]:
        buttons = [] if s["buttons"] == {} else s["buttons"]
        if set(s) != {"buttons", "frames"} or type(s["frames"]) is not int or s["frames"] <= 0 or not isinstance(buttons, list) or len(set(buttons)) != len(buttons) or any(b not in probe.BUTTONS for b in buttons):
            raise ValueError("invalid native route")
        steps.append(dict(s, buttons=buttons))
    if sum(s["frames"] for s in steps) > args.frames:
        raise ValueError("route exceeds frame cap")
    hello, partner_disclosure = partner_from_save(data)
    _, spans = expected_plan([m["blob_hex"] for m in hello["party"]])
    manifest = {"setup": "SYNTH", "fixture_sha256": digest(data), "route_sha256": digest(args.route.read_bytes()),
                "disclosure_sha256": digest(args.disclosure.read_bytes()), "rom_sha1": SHA1,
                "partner": "SYNTH second save identity; scripted TCP hello/tick, no second emulator",
                "partner_disclosure": partner_disclosure, "native": "buttons, trainer battle, real client/server, writer, send-in",
                "steps": steps, "feedback": not args.fixed_route, "frames": args.frames, "spans": spans, "symbols": symbols()}
    manifest["source_head"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    manifest["source_sha256"] = {f: digest((ROOT/f).read_bytes()) for f in (
        "lua/gen2/client.lua", "lua/gen2/entry.lua", "lua/gen2/signals.lua", "lua/gen2/polished_rival.lua",
        "tools/polished_live/rival_swap_live.lua", "tools/polished_live/rival_swap_live.py",
        "data/polished/polished_slink.sym", "data/polished/overlay_provenance.json")}
    if args.dry_run:
        print(json.dumps({k: v for k, v in manifest.items() if k != "steps"}, indent=2))
        return 0
    args.out.mkdir(parents=True, exist_ok=True)
    run = Path(tempfile.mkdtemp(prefix="synth-", dir=args.out))
    rom, sha, _ = probe.stage(run, full_site=True)
    if sha != SHA1:
        raise ValueError("overlay changed; re-pin this driver")
    from dataclasses import replace

    from tools.polished_live import harness
    launch_config = replace(harness.launch_config(dict(os.environ, POL_LANE=str(run), POL_KIND="overlay")),
                            rom_src=rom, rom=rom, sram=run / "sram",
                            symbols=tuple(dict.fromkeys((*harness.SYMBOLS, *SYMBOLS))))
    harness.prepare_save(launch_config, args.fixture)
    (run/"input.json").write_text(json.dumps(manifest))
    (run/"partner.json").write_text(json.dumps(hello))
    srvdir, wiredir = run/"server", run/"wire"
    srvdir.mkdir()
    (srvdir/"rom_contract.json").write_text(json.dumps(duo.contract_for(SHA1)))
    port, http = harness.free_port(), harness.free_port()
    partner = None
    with (run/"server.log").open("w") as log:
        srv = subprocess.Popen(server_command(port, http, srvdir, wiredir), cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic()+40
            while time.monotonic() < deadline:
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=1):
                        break
                except OSError:
                    if srv.poll() is not None:
                        raise RuntimeError("server exited before ready") from None
                    time.sleep(.2)
            partner = Partner(port, hello)
            partner.start()
            started = time.monotonic()
            text, pid = harness.launch(launch_config, "tools/polished_live/rival_swap_live.lua", run,
                                      {"POL_SWAP_CONFIG": (run/"input.json").as_posix(), "SLINK_HOST": "127.0.0.1",
                                       "SLINK_PORT": str(port), "SLINK_PLAYER": "a"}, args.timeout)
            try:
                trace = json.loads((run/"trace.json").read_text())
                verdict, reasons = evaluate(trace, duo.read_jsonl(wiredir/"wire_a.jsonl"), hello, manifest)
            except (OSError, ValueError) as exc:
                verdict, reasons = "FAIL", ["MISSING_OR_INVALID_TRACE: "+str(exc)]
            if "RESULT: PASS rival-swap recording" not in text or time.monotonic()-started >= args.timeout or partner.error:
                verdict, reasons = "FAIL", reasons+["INCOMPLETE_LAUNCH_OR_PARTNER: "+str(partner.error)]
            receipt = {"verdict": verdict, "reasons": reasons, "pid": pid, "manifest": manifest,
                       "trace_sha256": digest((run/"trace.json").read_bytes()) if (run/"trace.json").exists() else None,
                       "partner_responses": partner.responses}
            (run/"receipt.json").write_text(json.dumps(receipt, indent=2))
            print(json.dumps({"verdict": verdict, "reasons": reasons, "evidence": str(run)}))
            return int(verdict != "PASS")
        finally:
            if partner:
                partner.close()
            harness.kill_own(srv, "SLink server")


if __name__ == "__main__":
    raise SystemExit(main())
