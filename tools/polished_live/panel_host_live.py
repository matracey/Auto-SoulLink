#!/usr/bin/env python3
"""Polished SLink PANEL published by the REAL HOST (RC proof, OPEN-PANEL-PAGES host half). Needs EmuHawk.

    SLINK_WORK_ROOT=F:/slink-work PYTEST_DEBUG_TEMPROOT=F:/slink-work/tmp \
        python tools/polished_live/panel_host_live.py [--lane F:/slink-work/lanes/pol-panelhost]

One EmuHawk (player a) on the COMMITTED overlay (patch/dist/SLink-Polished.ups applied to the pinned release ROM, sha1
checked against data/polished/overlay_provenance.json) + the real server (server.server) + lua/slink.lua. Player b is a
scripted TCP identity (rival_swap_live.partner_from_save: a SYNTH second save identity, normal hello/tick/capture vocabulary,
never a state edit). b's `capture` on route_29 is sent first; player a then makes ONE real native wild catch on Route 29, so
the server forms a real linked pair and publishes a `link_panel` to a. The client holds it (panel:hold), the Phone card's SLink
contact opens the ROM panel, and the CLIENT answers each AWAIT with the page and STAGED. The driver writes no mailbox byte
(panel_host_live.lua writes only wPokegearFlags and 5 wPhoneList bytes, SYNTH, outside the mailbox).

Oracle (written to <lane>/run/oracle.json):
  O1 the rendered/staged page text equals the server rows (checked in the Lua, rows read from the wire),
  O2 those rows equal what the SERVER STATE says (links.json + /api/status: pair names/levels, pairs alive, dead zones, badges),
  O3 the link_panel on the server's own wire log is the one the client held,
  O4 the client's panel-permit receipts attribute every mailbox write (and the driver made none).
Pagination is replayable through evaluate/judge_pages without a live process: the expected
count comes from the server's rows and the pinned panel geometry, never the observed count.
Malformed wire evidence is an error, not a line silently omitted from the proof.
SYNTH disclosure: the fixture save (g2int-live warp fixture), wPokegearFlags=$87, wPhoneList=0, the scripted partner identity.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import threading
import time
import urllib.request

REPO = pathlib.Path(__file__).resolve().parents[2]
if __name__ == "__main__":
    sys.path.insert(0, str(REPO))

from tools.polished_live.runtime import read_jsonl  # noqa: E402

LANE_DEFAULT = "F:/slink-work/lanes/pol-panelhost"


def popcount(v) -> int:
    from server.board import badge_count
    return badge_count(v)




def judge_server_rows(rows, links, area_states, badges, line_max=16) -> tuple[list, list]:
    """O2: the link_panel rows the server published, against the server's OWN persisted state. Returns (checks, failures)."""
    checks, fails = [], []

    def need(name, ok, detail=""):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})
        if not ok:
            fails.append(f"{name}: {detail}")

    live = [e for e in links if e.get("a") and e.get("b") and e["a"].get("key") and e["b"].get("key")]
    need("O2 server state holds >= 1 fully linked pair", len(live) >= 1, f"{len(live)} pair(s)")
    if not live:
        return checks, fails
    alive = sum(1 for e in links if e.get("status") == "alive")
    dead_zones = sum(1 for v in area_states.values() if v == "dead_zone")
    # OPEN-PANEL-PAGES: Gen2PolishedAdapter.info_panel_width() == 16 (the ROM panel's line_max), so the server sends the COMPACT
    # single-line rows (server.py _build_link_panel), not the wide `label|field|...` rows. Order: SOUL LINK, blank, PAIRS a/b,
    # BADGES n/8, DEAD ZONES n, then a blank and one line per pair: ' ' (alive; 'X' otherwise) + mine-theirs, names <= 8 glyphs.
    need("O2 every row fits the ROM panel line and carries no '|' (the ROM cannot draw it)",
         all(len(r) <= line_max and "|" not in r for r in rows), [r for r in rows if len(r) > line_max or "|" in r])
    need("O2 title row is SOUL LINK", rows[:1] == ["SOUL LINK"], rows[:1])
    need("O2 'PAIRS a/b' equals the server's link count", f"PAIRS {alive}/{len(live)}" in rows, f"want PAIRS {alive}/{len(live)} in {rows}")
    need("O2 'DEAD ZONES n' equals the server's dead-zone count", f"DEAD ZONES {dead_zones}" in rows, f"want DEAD ZONES {dead_zones}")
    need("O2 'BADGES n/8' equals the popcount of player a's badges", f"BADGES {popcount(badges)}/8" in rows,
         f"badges {badges!r} -> BADGES {popcount(badges)}/8")
    e0 = live[0]
    want_pair = (("X" if e0.get("status") != "alive" else " ")
                 + f"{e0['a']['nickname'][:8]}-{e0['b']['nickname'][:8]}")[:line_max]
    need("O2 pair line names player a's mon then the PARTNER's mon, whole", want_pair in rows, f"want {want_pair!r} in {rows}")
    return checks, fails


def judge_pages(rows, pages, lines_per_page) -> tuple[list, list]:
    """Require the complete ordered 1..ceil(server rows / panel lines) observation."""
    expected = (len(rows) + lines_per_page - 1) // lines_per_page
    indexes = [p.get("page") for p in pages]
    counts = [p.get("pages") for p in pages]
    checks = [
        {"check": "pages: every server-derived page rendered exactly once in order",
         "ok": expected > 0 and indexes == list(range(1, expected + 1))
         and all(type(index) is int for index in indexes),
         "detail": {"expected": expected, "rendered": indexes}},
        {"check": "pages: runtime counts are stable and equal the server-derived count",
         "ok": bool(pages) and all(type(count) is int and count == expected for count in counts),
         "detail": {"expected": expected, "runtime_counts": counts}},
    ]
    return checks, [f"{c['check']}: {c['detail']}" for c in checks if not c["ok"]]


def evaluate(ev, links_doc, status, wire, sha1, *, lua_pass, lines_per_page=2) -> dict:
    """Replay the real-host oracle from retained JSON payloads; no emulator or server."""
    why, checks = [], []
    if not lua_pass:
        why.append("LUA_RESULT_NOT_PASS (see result.txt)")
    pair_rows = ev.get("pair_rows") or []
    server_panels = [c["rows"] for r in wire if r.get("dir") == "s2c"
                     for c in (r.get("msg") or {}).get("commands", []) if c.get("cmd") == "link_panel"]
    checks.append({"check": "O3 the server's wire log carries the link_panel the client held",
                   "ok": bool(pair_rows) and pair_rows in server_panels,
                   "detail": f"{len(server_panels)} link_panel(s) on the wire; held {pair_rows}"})
    badges = (status.get("players", {}).get("a", {}) or {}).get("badges", 0)
    c2, _ = judge_server_rows(pair_rows, links_doc.get("links", []), links_doc.get("area_states", {}), badges)
    checks += c2
    hello_a = next((r["msg"] for r in wire if r.get("dir") == "c2s"
                    and (r.get("msg") or {}).get("event") == "hello"), {})
    checks.append({"check": "hello: panel=true, panel_abi=3, companion_abi=3, overlay sha1",
                   "ok": hello_a.get("panel") is True and hello_a.get("panel_abi") == 3
                   and hello_a.get("companion_abi") == 3 and hello_a.get("rom_sha1") == sha1,
                   "detail": {k: hello_a.get(k) for k in ("panel", "panel_abi", "companion_abi", "rom_sha1", "artifact_kind")}})
    rec = ev.get("client_receipts") or []
    checks.append({"check": "O4 the client's permit logged the mailbox writes; the driver wrote 6 SYNTH bytes outside it",
                   "ok": len(rec) > 0 and len(ev.get("driver_writes", [])) == 6,
                   "detail": {"client_receipts": len(rec), "driver_writes": ev.get("driver_writes"),
                              "mailbox": ev.get("mailbox")}})
    page_checks, _ = judge_pages(pair_rows, ev.get("pages", []), lines_per_page)
    checks += page_checks
    failed = [c for c in checks if not c["ok"]]
    why += [f"{c['check']}: {c['detail']}" for c in failed]
    return {"verdict": "PASS" if lua_pass and not failed and not why else "FAIL", "why": why,
            "checks": checks, "rom_sha1": sha1, "server_rows": pair_rows,
            "expected_pages": (len(pair_rows) + lines_per_page - 1) // lines_per_page,
            "pages_rendered": [{"page": p.get("page"), "box_rows": p.get("box_rows"),
                                "staged": p.get("staged_text")} for p in ev.get("pages", [])]}


class Partner:
    """Scripted TCP identity `b`: hello, ticks, capture. Normal wire vocabulary only; commands it receives are ignored."""

    def __init__(self, port, hello):
        import socket
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=8)
        self.file = self.sock.makefile("rb")
        self.hello, self.seq, self.error, self.replies = hello, 0, None, []
        self.lock = threading.Lock()
        self.stop = threading.Event()

    def send(self, event, **extra):
        with self.lock:
            self.seq += 1
            msg = dict(self.hello, event=event, seq=self.seq, **extra)
            self.sock.sendall((json.dumps(msg) + "\n").encode())
            reply = json.loads(self.file.readline())
            self.replies.append({"event": event, "reply": reply})
            if any(c.get("refused") or c.get("cmd") == "identity_error" for c in reply.get("commands", [])):
                raise ValueError(f"partner refused: {reply}")
            return reply

    def start(self):
        reply = self.send("hello")
        if not any(c.get("cmd") == "config" for c in reply.get("commands", [])):
            raise ValueError(f"partner hello not admitted: {reply}")

        def run():
            try:
                while not self.stop.wait(1):
                    self.send("tick")
            except Exception as exc:  # noqa: BLE001 - reported in the verdict
                self.error = str(exc)
        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()

    def close(self):
        self.stop.set()
        if hasattr(self, "thread"):
            self.thread.join(6)
        self.file.close()
        self.sock.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lane", default=os.environ.get("POL_LANE", LANE_DEFAULT))
    ap.add_argument("--timeout", type=int, default=2400)
    args = ap.parse_args(argv)
    lane = pathlib.Path(args.lane)

    for sub in ("tools", "patch/tools", "tools/polished_live"):
        sys.path.insert(0, str(REPO / sub))
    import rival_swap_live as R
    import run_pol_panel as RP

    from tools.polished_live import duo, harness as H

    config = RP.panel_config(dict(os.environ, POL_LANE=str(lane), POL_KIND="overlay"))
    sha1 = RP.stage_from_committed_ups(config)
    source = pathlib.Path(os.environ.get("POL_FIXTURE", RP.DEFAULT_FIXTURE))
    H.prepare_save(config, source, clear=True)
    fixture = source.read_bytes()
    hello_b, partner_disclosure = R.partner_from_save(fixture)
    if hello_b["rom_sha1"] != sha1:
        raise SystemExit("overlay changed; the partner hello pins another sha1")

    run = lane / "run"
    if run.exists():
        shutil.rmtree(run)
    srv_dir, wire_dir = run / "srv", run / "wire"
    srv_dir.mkdir(parents=True)
    (srv_dir / "rom_contract.json").write_text(json.dumps(duo.contract_for(sha1)), encoding="utf-8")
    port, http = H.free_port(), H.free_port()
    srv_log = open(run / "server.log", "w", encoding="utf-8")  # noqa: SIM115 - closed in finally
    srv = subprocess.Popen([sys.executable, "-m", "server.server", "--host", "127.0.0.1", "--port", str(port),
                            "--http-port", str(http), "--data-dir", str(srv_dir), "--verbose", "--wire-log", str(wire_dir)],
                           cwd=str(REPO), stdout=srv_log, stderr=subprocess.STDOUT)
    print(f"[panelhost] server pid {srv.pid} tcp {port} http {http}", flush=True)
    partner = None
    verdict, why = "FAIL", []
    text = ""
    try:
        time.sleep(4)
        partner = Partner(port, hello_b)
        partner.start()
        m = hello_b["party"][0]
        cap_reply = partner.send("capture", area_id="route_29", gift=False, held_item_id=0, hp=m["hp"], maxHP=m["max_hp"],
                                 in_box=False, key=m["key"], level=m["level"], nickname=m["nickname"],
                                 species_id=m["species_id"], stats={"level": m["level"], "maxHP": m["max_hp"]})
        print(f"[panelhost] partner capture reply: {json.dumps(cap_reply)[:300]}", flush=True)
        text, pid = H.launch(config, "tools/polished_live/panel_host_live.lua", run,
                             {"SLINK_HOST": "127.0.0.1", "SLINK_PORT": str(port), "SLINK_PLAYER": "a"}, args.timeout)
        # server state, read while the server is still up
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{http}/api/status", timeout=5) as r:
                status = json.loads(r.read())
        except Exception as exc:  # noqa: BLE001
            status = {"error": str(exc)}
        time.sleep(1)
        shutil.copyfile(srv_dir / "links.json", run / "links_final.json")
        (run / "status_final.json").write_text(json.dumps(status), encoding="utf-8")

        ev = json.loads((run / "panel_host.json").read_text(encoding="utf-8")) if (run / "panel_host.json").exists() else {}
        links_doc = json.loads((run / "links_final.json").read_text(encoding="utf-8"))
        wire = read_jsonl(wire_dir / "wire_a.jsonl")
        profile = json.loads((REPO / "data/games/polished_crystal/profile.json").read_text(encoding="utf-8"))
        lines_per_page = profile["titles"]["polished"]["overlay"]["panel"]["lines"]
        oracle = evaluate(ev, links_doc, status, wire, sha1,
                          lua_pass="RESULT: PASS pol-panel-host" in text, lines_per_page=lines_per_page)
        if partner.error:
            oracle["why"].append("PARTNER: " + partner.error)
            oracle["verdict"] = "FAIL"
        verdict, why = oracle["verdict"], oracle["why"]
        oracle.update(partner={"name": hello_b["trainer_name"], "disclosure": partner_disclosure},
                      synth="fixture save (g2int-live warp), wPokegearFlags=$87, wPhoneList=0, scripted partner b",
                      fixture_sha256=hashlib.sha256(fixture).hexdigest())
        (run / "oracle.json").write_text(json.dumps(oracle, indent=2, default=str), encoding="utf-8")
        print(json.dumps({"verdict": verdict, "why": why, "evidence": str(run)}, indent=2), flush=True)
    finally:
        if partner:
            partner.close()
        H.kill_own(srv, "server")
        srv_log.close()
        client_log = REPO / "slink_lua.log"
        if client_log.exists():
            shutil.copyfile(client_log, run / "slink_lua.log")
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
