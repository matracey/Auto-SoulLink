#!/usr/bin/env python3
"""Capture A9 HTTP receipts, or replay a saved pair without network access.

Capture: --http-port PORT --out DIR [--label SCENARIO_MARKER]
Replay:  --selftest --html FILE --status FILE [--require ASSERTION ...]
False markers mean not observed in this snapshot, not necessarily a UI defect.
Exit 0: capture/replay completed and required markers pass; 1: required marker
failed; 2: input/network error. No browser, emulator, or server is launched.
"""
import argparse
import json
import sys
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

LABELS = ["ATK", "DEF", "SPD", "SPC", "", "ACC", "EVA"]
ASSERTIONS = ("paired_sprites", "trainer_names", "badge_pips", "phase_label",
              "wild_here", "stat_stage_labels", "psn_b", "stat_stage_chip",
              "def_minus_one", "live_players")


class Node:
    def __init__(self, tag="", attrs=()):
        self.tag, self.attrs, self.children = tag, dict(attrs), []

    def has(self, cls):
        return cls in (self.attrs.get("class") or "").split()

    def nodes(self, cls=None, tag=None):
        for child in self.children:
            if isinstance(child, Node):
                if (cls is None or child.has(cls)) and (tag is None or child.tag == tag):
                    yield child
                yield from child.nodes(cls, tag)

    def text(self):
        return " ".join(" ".join(c.text() if isinstance(c, Node) else c
                                 for c in self.children).split())


class DOM(HTMLParser):
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self, source):
        super().__init__(convert_charrefs=True)
        self.root = Node()
        self.stack = [self.root]
        self.feed(source)
        self.close()

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs)
        self.stack[-1].children.append(node)
        if tag not in self.VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def inspect_board(source, status):
    root = DOM(source).root
    content = next((n for n in root.nodes() if n.attrs.get("id") == "content"), root)
    players = status.get("players") or {}
    results = {}

    def record(name, ok, evidence):
        results[name] = (bool(ok), evidence)

    pairs = []
    for pair in content.nodes("mk-pair", "article"):
        halves = list(pair.nodes("mk-half"))
        good = len(halves) == 2 and all(
            sum(h.has(pid) and not h.has("empty") and any(
                (im.attrs.get("data-species") or "").isdigit()
                and int(im.attrs["data-species"]) > 0
                for im in h.nodes("mon-sprite", "img")) for h in halves) == 1
            for pid in ("a", "b"))
        if good:
            pairs.append(pair.attrs.get("id"))
    record("paired_sprites", pairs, {"pair_ids": pairs})
    cards = {}
    for card in content.nodes("mk-nowcard"):
        side = next(card.nodes("mk-side"), None)
        if side and side.text().lower() in ("a", "b"):
            cards[side.text().lower()] = card
    names, badges, wild, psn, chips, def_chips = {}, {}, {}, [], [], []
    for pid in ("a", "b"):
        p, card = players.get(pid) or {}, cards.get(pid, Node())
        name = next(card.nodes("mk-now-name"), Node())
        rendered = next(name.nodes(tag="b"), Node()).text()
        names[pid] = {"json": p.get("trainer_name"), "dom": rendered,
                      "ok": bool(p.get("trainer_name")) and rendered == p.get("trainer_name")}
        badge = next(card.nodes("mk-badges"), Node())
        pips = list(badge.nodes("mk-badge-pip"))
        mask = p.get("badges")
        count = bin(mask).count("1") if isinstance(mask, int) and mask >= 0 else -1
        badges[pid] = {"dom": badge.text(), "pips": len(pips),
                       "on": sum(n.has("on") for n in pips), "mask": mask}
        badges[pid]["ok"] = (len(pips) == 8 and badges[pid]["on"] == count
                              and badge.text() == f"{count}/8")
        panel = next((n for n in card.nodes("mk-wild", "details")
                      if n.attrs.get("id") == "d-wild:" + pid), Node())
        table = p.get("encounter_table") or {}
        expected = [(method, list(dict.fromkeys(m.get("name", "") for m in mons)))
                    for method, mons in table.items()]
        rows = [n.text() for n in panel.nodes("mk-wild-row")]
        wild[pid] = {"rows": rows, "json": expected,
                     "ok": bool(expected) and "Wild here" in panel.text()
                     and rows == [" ".join((method + " " + ", ".join(mons)).split())
                                  for method, mons in expected]}
        details = p.get("party_details") or {}
        keys = p.get("party_keys") or []
        battle = (p.get("battle_state") or {}).get("in_battle")
        key = (next((k for k in keys if details.get(k, {}).get("active")), None)
               if battle else next(iter(keys), None))
        mon = details.get(key) or {}
        mine = next((n for n in card.nodes("mk-cbt") if n.has("mine")), Node())
        cond = mon.get("status_cond") or 0
        if pid == "b" and mon.get("species_id") == 176 and cond & 8 and not cond & 135:
            psn = [n.text() for n in mine.nodes("sc-psn") if n.has("sc") and n.text() == "PSN"]
        stages = mon.get("stat_stages") or []
        for i, raw in enumerate(stages[:7]):
            if not isinstance(raw, int) or not LABELS[i] or not 0 <= raw <= 12 or raw == 6:
                continue
            delta = raw - 6
            text = f"{'+' if delta > 0 else chr(0x2212)}{abs(delta)} {LABELS[i]}"
            tone = "ss-up" if delta > 0 else "ss-dn"
            if battle and any(n.text() == text and n.has(tone) for n in mine.nodes("stat-stage")):
                evidence = {"player": pid, "key": key, "stages": stages, "dom": text}
                chips.append(evidence)
                if i == 1 and delta == -1:
                    def_chips.append(evidence)
    record("trainer_names", all(v["ok"] for v in names.values()), names)
    record("badge_pips", all(v["ok"] for v in badges.values()), badges)
    expected_phase = ("Game over" if status.get("run_over") else "Run in progress"
                      if all((players.get(p) or {}).get("nuzlocke_active") for p in ("a", "b"))
                      else "Waiting for Pokéballs")
    labels = [n.text() for n in content.nodes("phase-label")]
    record("phase_label", labels == [expected_phase], {"dom": labels, "expected": expected_phase})
    record("wild_here", all(v["ok"] for v in wild.values()), wild)
    caps = (players.get("a") or {}).get("capabilities") or {}
    record("stat_stage_labels", caps.get("stat_stage_labels") == LABELS, caps.get("stat_stage_labels"))
    b_party = (players.get("b") or {}).get("party_details") or {}
    record("psn_b", psn, {"dom": psn, "party": {
        key: {field: mon.get(field) for field in ("species_id", "status_cond", "hp")}
        for key, mon in b_party.items()}})
    record("stat_stage_chip", chips, chips)
    record("def_minus_one", def_chips, def_chips)
    live = {pid: {k: (players.get(pid) or {}).get(k)
                  for k in ("connected", "stale", "last_seen_age")} for pid in ("a", "b")}
    record("live_players", all(v["connected"] is True and v["stale"] is False
                              for v in live.values()), live)
    return results


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Expected standalone duo server; redirect to " + newurl)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--http-port", type=int)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--label", default="unspecified")
    ap.add_argument("--selftest", action="store_true", help="offline replay of saved HTML/JSON")
    ap.add_argument("--html", type=Path)
    ap.add_argument("--status", type=Path)
    ap.add_argument("--require", nargs="+", choices=ASSERTIONS, default=[])
    args = ap.parse_args()
    if args.selftest:
        if not args.html or not args.status:
            ap.error("--selftest requires --html FILE --status FILE")
        source = args.html.read_text(encoding="utf-8")
        status = json.loads(args.status.read_text(encoding="utf-8"))
        receipt = None
        provenance = {"mode": "offline-replay", "html": str(args.html), "status": str(args.status)}
    else:
        if not args.http_port or not 1 <= args.http_port <= 65535 or not args.out:
            ap.error("capture requires --http-port 1..65535 --out DIR")
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
        receipt = args.out / ("board-" + stamp)
        receipt.mkdir(parents=True, exist_ok=False)
        base = f"http://127.0.0.1:{args.http_port}/"
        opener = build_opener(ProxyHandler({}), NoRedirect())
        provenance = {"mode": "http-capture", "label": args.label, "fetches": []}

        def fetch(path, filename, partial=False):
            url = urljoin(base, path)
            if urlsplit(url).netloc != urlsplit(base).netloc or urlsplit(url).scheme != "http":
                raise ValueError("Poll URL is not on the requested loopback origin")
            started = datetime.now(UTC).isoformat()
            headers = {"HX-Request": "true", "HX-Target": "content"} if partial else {}
            with opener.open(Request(url, headers=headers), timeout=5) as response:
                data = response.read()
            (receipt / filename).write_bytes(data)
            provenance["fetches"].append({"url": url, "file": filename, "started": started,
                                           "ended": datetime.now(UTC).isoformat()})
            return data.decode("utf-8")

        source = fetch("/", "index.html")
        status = json.loads(fetch("/api/status", "status.json"))
        content = next((n for n in DOM(source).root.nodes() if n.attrs.get("id") == "content"), None)
        if content is None or not content.attrs.get("hx-get"):
            raise ValueError("No #content[hx-get] board poll target in root response")
        # Fetch even if poll URL is /: save the actual HTMX request as a separate receipt.
        partial = fetch(content.attrs["hx-get"], "poll.html", partial=True)
        (receipt / "capture.json").write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")
    results = inspect_board(source, status)
    required_failed = any(not results[name][0] for name in args.require)
    lines = ["BOARD_CAPTURE " + json.dumps(provenance, ensure_ascii=True)]
    for name, (ok, evidence) in results.items():
        snippet = json.dumps(evidence, ensure_ascii=True, separators=(",", ":"))
        lines.append(f"BOARD_ASSERT {name} ok={str(ok).lower()} evidence={snippet}")
    if not args.selftest:
        # Never combine two HTML responses to manufacture a pass across different moments.
        poll_results = inspect_board(partial, status)
        required_failed |= any(not poll_results[name][0] for name in args.require)
        for name, (ok, evidence) in poll_results.items():
            lines.append(f"BOARD_POLL_ASSERT {name} ok={str(ok).lower()} evidence="
                         + json.dumps(evidence, ensure_ascii=True, separators=(",", ":")))
    text = "\n".join(lines) + "\n"
    if receipt:
        (receipt / "assertions.txt").write_text(text, encoding="utf-8")
        print("BOARD_RECEIPT " + str(receipt))
    print(text, end="")
    return int(required_failed)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print("BOARD_ERROR " + str(exc), file=sys.stderr)
        raise SystemExit(2) from exc
