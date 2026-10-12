#!/usr/bin/env python3
"""Card C8: two-instance Polished Crystal SMOKE (hello + party census + 20-box census + one reconnect, ZERO writes).

    python tools/polished_live/duo.py          # ends `RESULT: PASS polished-duo-smoke` or `RESULT: FAIL ...`
    python tools/polished_live/duo.py --fixture-a A.SaveRAM --fixture-b B.SaveRAM --distinct-identities

Two EmuHawk (players a and b) boot the integrated overlay (patch/dist/SLink-Polished.ups on the pinned release,
sha1 97628616... was integrated at that time; the executable pin is INTEGRATED_SHA1 below) under private dirs F:/slink-work/lanes/pol-duo/run/{a,b}/ (own ROM copy, SaveRAM copy, config,
result, log) and talk to ONE real SLink server whose rom_contract.json pins BOTH players to the overlay sha1.
Evidence is read from the SERVER (the wire transcript `--wire-log`, /api/status, /api/debug/raw_state, slink.log);
the Lua driver (duo.lua) only supplies the client's own hello line and a zero-write tap. Then b's own EmuHawk PID is
killed and relaunched on the same private SaveRAM: a must stay connected, b must be re-admitted with an identical census.

DISCLOSURE: by default both sides boot copies of ONE saved identity (the SYNTH warp fixture), so this proves hello +
census only. With --distinct-identities each side boots ITS OWN fixture (--fixture-a / --fixture-b, env POL_FIXTURE_A /
POL_FIXTURE_B, falling back to POL_FIXTURE), the run refuses to start unless the two saves are two different trainers
(different bytes, player ID, name and disjoint party mon keys, read from the SaveRAM bytes), and the evaluator requires
each side's server-reported party keys / trainer / OT id to equal what ITS fixture's bytes decode to, no identity_error,
and both identities unchanged across b's reconnect. The second save is a SYNTH identity derivative (O-33, derive_save.py),
not independently played. Identity is read from the wire hello (ot_id, trainer_name, party) and /api/status
(players.<p>.trainer_name, .party_keys, .identity_error).
Only PIDs this script starts are ever killed (taskkill /T /F /PID); never an image-name kill.

PLAY (`--scenario link|boxsync|faint|whiteout|faint-natural|whiteout-natural|all`, card g2p-duoplay): each scenario is its own
fresh run (lane play-<name>/) driven by duo_play.lua through numbered step files, and judged by a server-side oracle
that FAILS when the specified rule and reporting path did not fire:
  S1 link      both sides catch natively on Route 29 -> /api/status holds an ALIVE link of exactly those two keys.
  S3 boxsync   box capability is always on (supports_box_mon retired; False -> SKIPPED, never PASS); A's
               first catch is quarantined (box_mon, executed + read back on A), then party_mon brings it back.
  S2 faint     after S1: SYNTH HP 0 on A's linked mon + a dropped TCP session -> the server's hello reconcile kills the
               link -> force_faint on B's wire -> B's own client writes it -> B's cartridge reads HP 0, nothing else.
  S4 whiteout  after S1: SYNTH HP 0 on A's whole party, same path, every linked partner (and only those) dies on B.
  S2n faint-natural     after S1: SYNTH HP 1 on the linked lead -> FIGHT -> native faint after party copyback.
  S4n whiteout-natural  after S1: SYNTH HP 1 on every non-egg -> native switches/last weak lead FIGHT -> whiteout.
The old HP-0/reconnect controls remain distinct from the native faint-copyback and guarded pre-heal LoseMoney paths.
Native oracles never accept hello_reconcile as PASS. Whiteout sends an empty payload; all-HP0 evidence comes
from the qualified pre-heal hook and send-time cartridge snapshot. S4n proves whiteout event sent plus faint
propagation, NOT whiteout-handler effects. Surviving strong mons continue native fights/encounters within a bound.
Native post-stage multiplicities are counted before key deduplication: one linked faint and one force_faint per
expected partner. A copyback witness must itself contain that key at HP0. Unaffected B records are compared by key,
including all 48 record bytes and both 11-byte name arrays; absent raw evidence fails, and only slot/address movement
is ignored. An HP-only snapshot cannot establish collateral safety.
Evidence: <lane>/play-<name>/evidence.json; one `RESULT: PASS|FAIL|SKIPPED polished-duo-play-<name>` line per scenario.
PLAY startup HTTP readiness has a 40-second bound; a child not listening yet is not a rule failure.
The native disclosure lists scenario paths, not evidence that every path ran; steps and PLAY_FINAL
are the witnesses (S1 without box capability can finish with zero client writes).
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from tools.polished_live.overlay_pin import overlay_sha1  # noqa: E402

LANE = Path(os.environ.get("POL_DUO_LANE", "F:/slink-work/lanes/pol-duo"))
RELEASE = Path("F:/slink-work/cache/polished/release/polishedcrystal-3.2.3.gbc")
UPS = REPO / "patch/dist/SLink-Polished.ups"
DEFAULT_FIXTURE = "F:/slink-work/lanes/g2int-live/pol/fixture/polished_overlay_warp.SaveRAM"
HERE = Path(__file__).resolve().parent
INTEGRATED_SHA1 = overlay_sha1()
ROM_NAME = "pol_overlay.gbc"            # BizHawk names the save from the file stem, '_' -> ' '
SAVE_NAME = "pol overlay.SaveRAM"
ADAPTER, KIND = "gen2_polished", "overlay"
# every command that can write a box/party/trade/faint (docs: server/state.py + server.py cmd names)
FORBIDDEN_CMDS = frozenset({
    "box_mon", "party_mon", "force_faint", "force_explode", "memorialize", "replace_rival_team", "apply_trade",
    "apply_prepare", "trade_final", "trade_mask", "withdraw_trade", "rebuild_start", "rebuild_done", "key_change_ack",
    "choose_mon", "game_over"})
ISOLATED = ("rom", "saveram", "config")  # per-side paths that must differ between the two sides


# ── pure parts (unit-tested: tests/unit/test_polished_duo_driver.py) ───────────────────────────────
def contract_for(sha1: str) -> dict:
    """rom_contract.json (server/adapters/gen2_polished.py rom_contract_by_sha1): BOTH players pinned to the overlay."""
    return {"upr_version": "none (overlay, not randomized)", "categories": [],
            "players": {"a": {"rom_sha1": sha1}, "b": {"rom_sha1": sha1}}}


def side_paths(run: Path, role: str, generation: int = 1) -> dict:
    """One side's private files. rom/saveram/config are per SIDE; `run` (result, sent log, stop file) per launch."""
    base = Path(run) / role
    return {"rom": (base / "rom" / ROM_NAME).as_posix(), "saveram": (base / "sram" / SAVE_NAME).as_posix(),
            "sram_dir": (base / "sram").as_posix(), "config": (base / "config.ini").as_posix(),
            "run": (base / f"g{generation}").as_posix(), "result": (base / f"g{generation}" / "result.txt").as_posix()}


def side_env(paths: dict, role: str, host: str, port: int) -> dict:
    """The environment duo.lua / pol_lib.lua read (pol_lib: SLINK_ROOT POL_OUT POL_RUN POL_SYMS)."""
    return {"SLINK_ROOT": str(REPO).replace("\\", "/"), "POL_OUT": paths["result"], "POL_RUN": paths["run"],
            "POL_SYMS": paths["run"] + "/syms.json", "SLINK_HOST": host, "SLINK_PORT": str(port),
            "SLINK_PLAYER": role, "DUO_ROLE": role}


# ── per-side fixtures + identity (pure; the identity layout is derive_save.py's, never duplicated) ──────
class FixtureError(ValueError):
    """A fixture is not a SaveRAM this driver can read an identity from."""


def _derive_save():
    mod = sys.modules.get("polished_derive_save")
    if mod is None:
        spec = importlib.util.spec_from_file_location("polished_derive_save", HERE / "derive_save.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules["polished_derive_save"] = mod
        spec.loader.exec_module(mod)
    return mod


def resolve_fixtures(fixture_a=None, fixture_b=None, env=None) -> tuple[Path, Path]:
    """CLI flag > POL_FIXTURE_A / POL_FIXTURE_B > POL_FIXTURE (the default for BOTH sides) > the default fixture."""
    env = os.environ if env is None else env
    common = env.get("POL_FIXTURE") or DEFAULT_FIXTURE
    return (Path(fixture_a or env.get("POL_FIXTURE_A") or common), Path(fixture_b or env.get("POL_FIXTURE_B") or common))


def fixture_identity(data: bytes) -> dict:
    """The trainer a SaveRAM image belongs to, decoded from its bytes with derive_save's layout + the repo codec:
    sha256 (and its recorded 8-char prefix), player id, player name, and the party mon keys in slot order. Main and
    backup copy must agree; a wrong-size/marker/checksum image raises FixtureError (never guesses)."""
    ds = _derive_save()
    problems = ds._layout_problems(data) + (ds._checksum_problems(data) if len(data) == ds.SAVE_SIZE else [])
    if problems:
        raise FixtureError("; ".join(problems))
    per_copy = {}
    for c in ds.ALL_COPIES:
        ident = ds._identity(data, c)
        keys = [ds.pc.key(ds._mon(data, c, s)) for s in range(ds.party_count(data, c))]
        per_copy[c.name] = (ident["id"], ident["name"], keys)
    if per_copy["main"] != per_copy["backup"]:
        raise FixtureError(f"main and backup copies disagree on identity/party: {per_copy}")
    player_id, name, keys = per_copy["main"]
    sha = hashlib.sha256(bytes(data)).hexdigest()
    return {"sha256": sha, "prefix": sha[:8], "player_id": player_id, "name": name, "keys": keys}


def distinct_refusals(a: dict, b: dict) -> list[str]:
    """Why --distinct-identities must refuse to start (empty = two different trainers). a/b = fixture_identity()."""
    out = []
    if a["sha256"] == b["sha256"]:
        out.append("fixtures_byte_identical")
    if a["player_id"] == b["player_id"]:
        out.append("player_id_shared")
    if a["name"] == b["name"]:
        out.append("player_name_shared")
    if set(a["keys"]) & set(b["keys"]):
        out.append("party_keys_overlap")
    return out


def stage_side(paths: dict, rom: bytes, fixture: Path) -> str:
    """Give ONE side its own ROM copy and its own SaveRAM copy of ITS fixture; returns the copy's sha256 (must equal the
    fixture's: the side boots exactly the bytes whose identity the evaluator will expect)."""
    from dataclasses import replace

    from tools.polished_live.runtime import LaunchConfig, prepare_save
    config = replace(LaunchConfig.from_env(), rom=Path(paths["rom"]), sram=Path(paths["sram_dir"]),
                     save_name=Path(paths["saveram"]).name)
    config.rom.parent.mkdir(parents=True, exist_ok=True)
    config.rom.write_bytes(rom)
    prepare_save(config, fixture)
    return hashlib.sha256(Path(paths["saveram"]).read_bytes()).hexdigest()


def parse_wire(records: list) -> dict:
    """One player's wire_<p>.jsonl: its hellos (with connection id), connect/disconnect counts, server->client commands."""
    out = {"hellos": [], "connects": 0, "disconnects": 0, "commands": {}}
    for rec in records:
        msg = rec.get("msg") if isinstance(rec, dict) else None
        if not isinstance(msg, dict):
            continue
        if rec.get("dir") == "meta":
            key = {"_connect": "connects", "_disconnect": "disconnects"}.get(msg.get("event"))
            if key:
                out[key] += 1
        elif rec.get("dir") == "c2s" and msg.get("event") == "hello":
            out["hellos"].append({"conn": rec.get("conn"), "msg": msg})
        elif rec.get("dir") == "s2c":
            for cmd in msg.get("commands") or []:
                name = cmd.get("cmd") if isinstance(cmd, dict) else None
                out["commands"][str(name)] = out["commands"].get(str(name), 0) + 1
    return out


def parse_log(text: str) -> dict:
    """slink.log: per-player admission verdicts (on change) and adapter routes in hello order, and the TCP client source ports in
    accept order (the wire tap numbers connections in the same order, so conn N = ports[N-1])."""
    out = {"admission": {"a": [], "b": []}, "routes": {"a": [], "b": []}, "ports": []}
    for line in text.splitlines():
        if m := re.search(r"\[(a|b)\] admission: (\w+)", line):
            out["admission"][m[1]].append(m[2])
        elif m := re.search(r"\[(a|b)\] route \S+ -> (\S+)", line):
            out["routes"][m[1]].append(m[2])
        elif m := re.search(r"Client connected: \('[\d.]+', (\d+)\)", line):
            out["ports"].append(int(m[1]))
    return out


def hello_view(rec: dict | None, k: int, role: str, log: dict, status_player: dict) -> dict:
    """What the SERVER saw for this player's k-th hello (rec = parse_wire hello), joined with its own log verdicts."""
    if not rec:
        return {"seen": False}
    msg, routes = rec["msg"], log["routes"][role]
    boxes = msg.get("pc_boxes")
    # /api/status admission = the verdict of the NEWEST hello (re-decided on every hello; slink.log only logs a change,
    # so a clean reconnect leaves no line there). A live identity_error is a refusal too.
    verdict = "identity_error" if status_player.get("identity_error") else status_player.get("admission", "none")
    return {"seen": True, "conn": rec.get("conn"), "admission": verdict,
            "adapter": (routes[min(k, len(routes) - 1)] if routes else ""),
            "artifact_kind": msg.get("artifact_kind"), "rom_type": msg.get("rom_type"), "rom_sha1": msg.get("rom_sha1"),
            "party_keys": [e.get("key", "") for e in msg.get("party") or []], "party_count": len(msg.get("party") or []),
            "pc_boxes_generation": msg.get("pc_boxes_generation"),
            "pc_boxes": None if boxes is None else sorted(f"{e.get('box')}:{e.get('key')}" for e in boxes),
            "server_party_keys": list(status_player.get("party_keys") or []),
            "server_pc_boxes_n": len(status_player.get("pc_boxes") or []),
            "ot_id": msg.get("ot_id"), "trainer_name": msg.get("trainer_name"),
            "server_trainer_name": status_player.get("trainer_name"),
            "identity_error": status_player.get("identity_error") or ""}


def _hello_reasons(tag: str, h: dict | None, staged: str) -> list[str]:
    if not h or not h.get("seen"):
        return [f"{tag}:hello_missing"]
    out = []
    if h.get("admission") != "admitted":
        out.append(f"{tag}:hello_rejected")
    if h.get("adapter") != ADAPTER:
        out.append(f"{tag}:adapter_mismatch")
    if h.get("artifact_kind") != KIND:
        out.append(f"{tag}:artifact_kind_mismatch")
    if str(h.get("rom_sha1") or "").lower() != staged.lower():
        out.append(f"{tag}:rom_sha1_mismatch")
    keys = h.get("party_keys") or []
    if not keys:
        out.append(f"{tag}:party_empty")
    elif (h.get("party_count") != len(keys) or len(set(keys)) != len(keys) or "" in keys
          or sorted(keys) != sorted(h.get("server_party_keys") or [])):
        out.append(f"{tag}:party_incomplete")
    gen = h.get("pc_boxes_generation")
    if type(gen) is not int or gen < 0 or h.get("pc_boxes") is None:
        out.append(f"{tag}:box_census_incomplete")
    elif h.get("server_pc_boxes_n") != len(h["pc_boxes"]):
        out.append(f"{tag}:box_census_refused")
    return out


def evaluate(sides: dict, staged_sha1: str) -> tuple[bool, list[str]]:
    """sides = {"a": side, "b": side}; each side: player, paths{rom,saveram,config}, client_port, hello, reconnect (hello or
    None), continuous (never disconnected before the stop), client_writes, commands{name: n} (server->client, all
    connections), exit_marker. Returns (ok, reasons); every defect has its own reason code, fail closed."""
    reasons: list[str] = []
    if sorted(sides) != ["a", "b"]:
        return False, ["sides_missing"]
    reconnecting = [r for r in sides if sides[r].get("reconnect") is not None]
    if not reconnecting:
        reasons.append("reconnect_missing")
    for role, s in sorted(sides.items()):
        reasons += _hello_reasons(role, s.get("hello"), staged_sha1)
        if role in reconnecting:
            reasons += _hello_reasons(f"{role}/reconnect", s["reconnect"], staged_sha1)
            h1, h2 = s.get("hello") or {}, s["reconnect"]
            if h1.get("seen") and h2.get("seen") and (
                    h1.get("party_keys") != h2.get("party_keys") or h1.get("pc_boxes") != h2.get("pc_boxes")):
                reasons.append(f"{role}:census_changed_after_reconnect")
        elif not s.get("continuous"):
            reasons.append(f"{role}:disconnected_unexpectedly")
        if s.get("client_writes") != 0:
            reasons.append(f"{role}:write_observed")
        bad = sorted(set(s.get("commands") or {}) & FORBIDDEN_CMDS)
        if bad:
            reasons.append(f"{role}:command_observed:{','.join(bad)}")
        if not s.get("exit_marker"):
            reasons.append(f"{role}:exit_marker_missing")
    a, b = sides["a"], sides["b"]
    for field in ISOLATED:
        if a["paths"].get(field) == b["paths"].get(field):
            reasons.append(f"isolation:{field}_shared")
    if a.get("client_port") is None or a.get("client_port") == b.get("client_port"):
        reasons.append("isolation:client_port_shared_or_unknown")
    return not reasons, reasons


def _identity_reasons(tag: str, h: dict, own: dict, other: dict) -> list[str]:
    """One hello's identity against the fixture ITS side booted (own) and the other side's (other)."""
    out = []
    if h.get("identity_error"):
        out.append(f"{tag}:identity_error_present")
    keys = sorted(h.get("party_keys") or [])
    if keys and keys == sorted(other["keys"]) and keys != sorted(own["keys"]):
        out.append(f"{tag}:booted_other_fixture")
    else:
        if keys != sorted(own["keys"]):
            out.append(f"{tag}:party_keys_not_from_fixture")
        if h.get("ot_id") != own["player_id"]:
            out.append(f"{tag}:ot_id_not_from_fixture")
        if h.get("trainer_name") != own["name"]:
            out.append(f"{tag}:trainer_name_not_from_fixture")
    if h.get("server_trainer_name") != h.get("trainer_name"):
        out.append(f"{tag}:server_trainer_name_differs")
    return out


def evaluate_identities(sides: dict, expected: dict) -> list[str]:
    """The distinct-identities requirements on top of `evaluate`. expected = {"a": fixture_identity, "b": ...}. A side whose
    hello is missing is left to `evaluate` (hello_missing); everything else has its own reason code, fail closed."""
    if sorted(expected) != ["a", "b"] or sorted(sides) != ["a", "b"]:
        return ["identity:expected_missing"]
    out: list[str] = []
    if set(expected["a"]["keys"]) & set(expected["b"]["keys"]) or expected["a"]["player_id"] == expected["b"]["player_id"]:
        out.append("identity:expected_fixtures_not_distinct")
    seen = {}
    for role, other in (("a", "b"), ("b", "a")):
        s = sides[role]
        h = s.get("hello")
        if not h or not h.get("seen"):
            continue
        seen[role] = h
        out += _identity_reasons(role, h, expected[role], expected[other])
        rc = s.get("reconnect")
        if rc is not None and rc.get("seen"):
            out += _identity_reasons(f"{role}/reconnect", rc, expected[role], expected[other])
            if (h.get("ot_id"), h.get("trainer_name")) != (rc.get("ot_id"), rc.get("trainer_name")):
                out.append(f"{role}:identity_changed_after_reconnect")
        elif rc is None:                      # the side that stayed up: its identity must be untouched by the peer's reconnect
            fin = s.get("final")
            if not fin:
                out.append(f"{role}:final_identity_missing")
            else:
                if fin.get("identity_error"):
                    out.append(f"{role}:final_identity_error_present")
                if sorted(fin.get("party_keys") or []) != sorted(h.get("party_keys") or []) or \
                        fin.get("trainer_name") != h.get("trainer_name"):
                    out.append(f"{role}:identity_changed_while_peer_reconnected")
    if len(seen) == 2:
        ka, kb = set(seen["a"].get("party_keys") or []), set(seen["b"].get("party_keys") or [])
        if ka and ka == kb:
            out.append("identity:party_keys_identical")
        elif ka & kb:
            out.append("identity:party_keys_overlap")
        if seen["a"].get("trainer_name") and seen["a"].get("trainer_name") == seen["b"].get("trainer_name"):
            out.append("identity:trainer_name_shared")
        if seen["a"].get("ot_id") is not None and seen["a"].get("ot_id") == seen["b"].get("ot_id"):
            out.append("identity:ot_id_shared")
    return out


def evaluate_distinct(sides: dict, staged_sha1: str, expected: dict) -> tuple[bool, list[str]]:
    """`evaluate` (hello/census/reconnect/zero-writes/isolation) plus the two-identity requirements."""
    _, reasons = evaluate(sides, staged_sha1)
    reasons = reasons + evaluate_identities(sides, expected)
    return not reasons, reasons


# ═══════════════════════ PLAY scenarios (`--scenario NAME|all`): the Soul Link rules firing ═══════════════════════
# Each scenario is its own run (fresh lane, fresh server state) driven by duo_play.lua. Every oracle below reads
# SERVER evidence (wire transcript, /api/status, slink.log) plus the CARTRIDGE read-backs the Lua side took from
# WRAM/CartRAM, and each has a red control in tests/unit/test_polished_duo_driver.py: it FAILS on a transcript where
# the rule did not fire. Verdicts are PASS, FAIL or SKIPPED (S3 only, when the adapter capability is off): never a
# PASS for a rule the run did not observe.
PLAY_ORDER = ("link", "boxsync", "faint", "whiteout", "faint-natural", "whiteout-natural")
PLAY_SCENARIOS = {
    "link": "S1 encounter linking: both players catch in one area -> the server pairs the two keys",
    "boxsync": "S3 party sync / quarantine: box_mon on the first catch executed on that cartridge, party_mon back",
    "faint": "S2 faint propagation: A's linked mon at HP 0 -> force_faint to B -> B's partner reads HP 0 on B",
    "whiteout": "S4 whiteout: A's whole party at HP 0 -> every linked partner force-fainted on B, nothing else",
    "faint-natural": "S2n native faint: HP1 setup -> native copyback faint event -> exactly B's linked partner HP0",
    "whiteout-natural": "S4n native whiteout event sent + faint propagation: guarded all-HP0 witness; whiteout handler UNPROVEN",
}
SYNTH_SETUP = {
    "link": [],
    "boxsync": [],
    "faint": ["A: current HP of the ONE linked party record poked to 0 (2 WRAM bytes, the untapped writer)",
              "A: TCP session dropped (connector.disconnect) so the client's own reconnect re-sends hello"],
    "whiteout": ["A: current HP of EVERY party record poked to 0 (2 WRAM bytes each, the untapped writer)",
                 "A: TCP session dropped (connector.disconnect) so the client's own reconnect re-sends hello"],
    "faint-natural": ["A: current HP of the ONE linked party record set to 1 (two disclosed WRAM bytes, never HP0)",
                      "A: linked mon moved to lead by a separately disclosed party-record/OT/nickname swap"],
    "whiteout-natural": ["A: current HP of EVERY non-egg party record set to 1 (two disclosed WRAM bytes each, never HP0)",
                         "A: linked mon moved to lead by a separately disclosed party-record/OT/nickname swap"],
}
# Natural scenarios exercise the production faint observer and guarded LoseMoney whiteout hook. The original
# SYNTH HP0 scenarios deliberately retain their reconnect path as separate controls; they are not native-loss proof.
BOX_CAPABILITY_OFF = ("adapter gen2_polished supports_box_mon() is False: the server skips quarantine ('client has no "
                      "box executor') and sends no box_mon/party_mon, so S3 cannot be observed")


def play_names(arg: str) -> list[str]:
    """`--scenario all` = every play scenario in PLAY_ORDER; else one known name (ValueError otherwise)."""
    if arg == "all":
        return list(PLAY_ORDER)
    if arg not in PLAY_SCENARIOS:
        raise ValueError(f"unknown scenario {arg!r}: choose from {', '.join(PLAY_ORDER)} or all")
    return [arg]


def _as_list(v) -> list:
    return list(v) if isinstance(v, list) else []   # Lua encodes an empty array as {}


def wire_slice(records: list, since: int = 0) -> list:
    """(index, dir, msg) for records[since:], objects only."""
    out = []
    for i, rec in enumerate(records or []):
        if i < since or not isinstance(rec, dict) or not isinstance(rec.get("msg"), dict):
            continue
        out.append((i, rec.get("dir"), rec["msg"]))
    return out


def c2s_events(records: list, event: str, since: int = 0) -> list[dict]:
    return [m for _, d, m in wire_slice(records, since) if d == "c2s" and m.get("event") == event]


def s2c_commands(records: list, since: int = 0) -> list[dict]:
    out = []
    for _, d, m in wire_slice(records, since):
        if d == "s2c":
            out += [c for c in _as_list(m.get("commands")) if isinstance(c, dict)]
    return out


def _step(ev: dict, role: str, label: str) -> dict:
    return ((ev.get("steps") or {}).get(role) or {}).get(label) or {}


def _snap(step: dict) -> dict:
    return step.get("snapshot") or {}


def _party(snap: dict) -> dict:
    """{key: {slot, hp, max_hp, status, species}} from a cartridge snapshot."""
    return {m["key"]: m for m in _as_list(snap.get("party")) if isinstance(m, dict) and m.get("key")}


def _box_keys(snap: dict) -> set:
    return {m.get("key") for m in _as_list(snap.get("box")) if isinstance(m, dict) and m.get("key")}


def _caught_key(ev: dict, role: str) -> str | None:
    cap = _step(ev, role, f"catch_{role}").get("capture") or {}
    return cap.get("key") or None


def _link_reasons(ev: dict, before_stage: bool = False) -> tuple[list[str], dict]:
    """S1. Both native catches on the server wire in one area -> exactly those keys ALIVE in `status.linked`.
    A later native loss/Run can legitimately send no_catch; natural loss gates judge S1 only before the stage mark."""
    reasons, facts = [], {}
    caps = {}
    wires = (ev.get("wire") or {})
    if before_stage:
        mark = (ev.get("marks") or {}).get("stage") or {}
        wires = {r: wires.get(r, [])[:mark[r]] if type(mark.get(r)) is int and mark[r] >= 0 else []
                 for r in ("a", "b")}
    for role in ("a", "b"):
        st = _step(ev, role, f"catch_{role}")
        if not st.get("ok"):
            reasons.append(f"{role}:catch_failed")
            continue
        if (st.get("capture_site_hits") or 0) < 1:
            reasons.append(f"{role}:capture_site_not_fired")
        key = _caught_key(ev, role)
        wire = [m for m in c2s_events(wires.get(role, []), "capture") if m.get("key") == key]
        if not key or not wire:
            reasons.append(f"{role}:capture_not_on_wire")
            continue
        caps[role] = wire[0]
        snap = _snap(st)
        if key not in _party(snap) and key not in _box_keys(snap):
            reasons.append(f"{role}:caught_mon_not_on_cartridge")
    if len(caps) < 2:
        return reasons, facts
    area = caps["a"].get("area_id") or ""
    facts.update(area_id=area, a_key=caps["a"]["key"], b_key=caps["b"]["key"])
    if not area or area != (caps["b"].get("area_id") or ""):
        reasons.append("capture_areas_differ")
    for role in ("a", "b"):
        if any(m.get("area_id") == area for m in c2s_events(wires.get(role, []), "no_catch")):
            reasons.append(f"{role}:no_catch_sent")
    status = (ev.get("status") or {}).get("linked") or {}
    links = [lk for lk in _as_list(status.get("links")) if isinstance(lk, dict) and lk.get("area_id") == area]
    exact = [lk for lk in links if lk.get("a_key") == facts["a_key"] and lk.get("b_key") == facts["b_key"]]
    if not links:
        reasons.append("link_missing")
    elif not exact:
        reasons.append("link_keys_mismatch")
    elif exact[0].get("status") != "alive":
        reasons.append("link_not_alive")
    if (status.get("area_states") or {}).get(area) == "dead":
        reasons.append("area_dead_zoned")
    return reasons, facts


def oracle_link(ev: dict) -> tuple[str, list[str], dict]:
    reasons, facts = _link_reasons(ev)
    return ("PASS" if not reasons else "FAIL"), reasons, facts


def _lead_permutation_valid(stage: dict, linked_key: str, targets: set) -> bool:
    """Independently derive 48/11/11 spans from symbols
     compare every logged byte and keyed postimage."""
    from tools.polished_live.faint_probe import read_symbols
    try:
        sym = read_symbols(REPO / 'data/polished/polished_slink.sym')
        stride = sym['wPartyMon2'][1] - sym['wPartyMon1'][1]
        names = (sym['wPartyMonNicknames'][1] - sym['wPartyMonOTs'][1]) // 6
        if (stride, names) != (48, 11):
            return False
        arrays = [('wPartyMons','record_hex',stride),('wPartyMonOTs','ot_hex',names),('wPartyMonNicknames','nickname_hex',names)]
        before = _party(stage['before'])
        lead_after = _party(stage['lead_after'])
        final = _party(_snap(stage))
        if not before or set(before) != set(lead_after) or set(before) != set(final):
            return False
        if any(len(_as_list(x.get('party'))) != len(before) for x in (stage['before'],stage['lead_after'],_snap(stage))):
            return False
        lead = before[linked_key]['slot']
        slots = {m['slot']:m for m in before.values()}
        if set(slots) != set(range(len(before))):
            return False
        expected = []
        if lead != 0:
            if any(bytes.fromhex(slots[x]['record_hex'])[1] != 0 for x in (0, lead)):
                return False
            for label,field,size in arrays:
                bank,addr = sym[label]
                base = addr-0xc000 if addr<0xd000 else (bank or 1)*0x1000+addr-0xd000
                for dst,src in ((0,lead),(lead,0)):
                    old,new = bytes.fromhex(slots[dst][field]),bytes.fromhex(slots[src][field])
                    if len(old)!=size or len(new)!=size:
                        return False
                    expected.extend({'array':label,'wram':base+dst*size+i,'slot':dst,'key':slots[src]['key'],'old':old[i],'new':new[i]} for i in range(size))
        if stage.get('lead_writes') != expected:
            return False
        for key,mon in before.items():
            dst=lead if mon['slot']==0 else 0 if mon['slot']==lead else mon['slot']
            if lead_after[key] != dict(mon,slot=dst):
                return False
            if final[key]['slot'] != dst:
                return False
            for _,field,size in arrays:
                raw=bytes.fromhex(mon[field])
                post=bytes.fromhex(final[key][field])
                if len(raw)!=size or len(post)!=size:
                    return False
                allowed=bytearray(raw)
                if field=='record_hex' and key in targets:
                    allowed[34:36]=b'\x00\x01'
                if post!=allowed:
                    return False
        return True
    except (ValueError,KeyError,TypeError,IndexError):
        return False



def _natural_stage_reasons(stage: dict, linked_key: str, whole_party: bool) -> tuple[list[str], set]:
    """Only the disclosed HP1 byte pairs may establish the native-loss setup; HP0 injection is never evidence."""
    reasons = []
    before, after = _party(stage.get("before") or {}), _party(_snap(stage))
    targets = ({k for k, m in before.items() if not (m.get("egg") or m.get("is_egg"))} if whole_party
               else {linked_key} if linked_key else set())
    writes = [w for w in _as_list(stage.get("synth_writes")) if isinstance(w, dict)]
    if not _lead_permutation_valid(stage, linked_key, targets):
        reasons.append('a:lead_not_exact_permutation')
    if not stage.get("ok"):
        reasons.append("a:stage_failed")
    if stage.get("op") != "synth_hp1":
        reasons.append("a:stage_not_hp1")
    if {w.get("key") for w in writes} != targets:
        reasons.append("a:stage_wrong_target")
    if not targets or any(type((before.get(k) or {}).get("hp")) is not int or before[k]["hp"] <= 0 for k in targets):
        reasons.append("a:stage_target_not_alive")
    if set(before) != set(after) or any((after.get(k) or {}).get("hp") != 1 for k in targets):
        reasons.append("a:stage_not_landed")
    if (after.get(linked_key) or {}).get("slot") != 0:
        reasons.append("a:linked_mon_not_lead")
    for key, mon in before.items():
        if key not in targets and (after.get(key) or {}).get("hp") != mon.get("hp"):
            reasons.append(f"a:stage_collateral_hp_change:{key}")
    for key in sorted(targets):
        rows = [w for w in writes if w.get("key") == key]
        old_hp = (before.get(key) or {}).get("hp")
        valid = (type(old_hp) is int and len(rows) == 2
                 and all(type(w.get("wram")) is int and w["wram"] >= 0
                         and type(w.get("old")) is int and 0 <= w["old"] <= 255
                         and type(w.get("new")) is int and 0 <= w["new"] <= 255 for w in rows))
        if valid:
            high, low = sorted(rows, key=lambda w: w["wram"])
            from tools.polished_live.faint_probe import read_symbols
            sym = read_symbols(REPO / 'data/polished/polished_slink.sym')
            bank, addr = sym['wPartyMons']
            base = addr-0xc000 if addr<0xd000 else (bank or 1)*0x1000+addr-0xd000
            hp_off = sym['wPartyMon1HP'][1] - addr
            slot = (after.get(key) or {}).get('slot')
            expected_at = base + slot * 48 + hp_off if type(slot) is int else -1
            try:
                raw_old = bytes.fromhex(_party(stage['lead_after'])[key]['record_hex'])[hp_off:hp_off+2]
            except (KeyError, TypeError, ValueError):
                raw_old = b''  # incomplete/invalid recording fails the oracle instead of dropping evidence

            valid = (hp_off == 34 and [high['wram'],low['wram']] == [expected_at,expected_at+1]
                     and bytes([high["old"], low["old"]]) == raw_old
                     and [high["old"], low["old"]] == [old_hp >> 8, old_hp & 255]
                     and all(w.get("slot") == slot for w in rows))
            if high["new"] == low["new"] == 0:
                reasons.append("a:stage_hp_zero_injection")
            valid = valid and [high["new"], low["new"]] == [0, 1]
        if not valid:
            reasons.append(f"a:stage_hp1_write_invalid:{key}")
    return reasons, targets


def _natural_report_reasons(ev: dict, targets: set, linked_key: str, whole_party: bool) -> tuple[list[str], dict]:
    """Bind the post-mark native server event to a send-time HP0 report and a qualified read-only engine witness."""
    reasons, facts = [], {"server_path": "none"}
    mark = (ev.get("marks") or {}).get("stage") or {}
    wires = ev.get("wire") or {}
    for role in ("a", "b"):
        records = wires.get(role, [])
        offset = mark.get(role)
        if type(offset) is not int or not 0 <= offset <= len(records):
            reasons.append("stage_mark_missing")
            offset = 0
        # A normal terminal disconnect belongs to runner teardown, not a reconnect-assisted death report.
        if any(m.get("event") in ("hello", "_connect") for _, _, m in wire_slice(records, offset)):
            reasons.append(f"{role}:poststage_reconnect")
    wa, offset = wires.get("a", []), mark.get("a", 0)
    offset = offset if type(offset) is int and offset >= 0 else 0
    faint = [m for m in c2s_events(wa, "faint", offset) if m.get("key") == linked_key]
    whiteout = c2s_events(wa, "whiteout", offset)
    facts["linked_faint_count"] = len(faint)
    if len(faint) != 1:
        reasons.append("a:linked_faint_count_not_one")
    zero_hello = [m for m in c2s_events(wa, "hello", offset)
                  if (_party(m).get(linked_key) or {}).get("hp") == 0]
    events = whiteout if whole_party else faint
    facts["server_path"] = ("whiteout_event" if whole_party and whiteout else "faint_event" if faint
                            else "whiteout_event" if whiteout else "hello_reconcile" if zero_hello else "none")
    if not events:
        reasons.append("a:native_event_missing")
    if not whole_party and whiteout:
        reasons.append("a:unexpected_whiteout_event")
    if not whole_party and any(m.get("key") != linked_key for m in c2s_events(wa, "faint", offset)):
        reasons.append("a:unexpected_faint_key")
    battle, stage = _step(ev, "a", "battle"), _step(ev, "a", "stage")
    if not battle.get("ok") or battle.get("op") != "lose_native":
        reasons.append("a:native_battle_failed")
    if whole_party and battle.get("run_used"):
        reasons.append("a:whiteout_run_used")
    stage_frame = _snap(stage).get("frame")

    def after_stage(row) -> bool:
        return type(stage_frame) is int and type(row.get("frame")) is int and row["frame"] > stage_frame

    event_name = "whiteout" if whole_party else "faint"
    reports = [r for r in _as_list(battle.get("reports")) if isinstance(r, dict) and r.get("event") == event_name
               and (whole_party or r.get("key") == linked_key) and after_stage(r)]
    if not reports:
        reasons.append("a:native_report_missing")
    hits = (battle.get("site_hits") or {}).get("whiteout" if whole_party else "faint_copyback")
    qualified = [h for h in _as_list(hits) if isinstance(h, dict) and h.get("qualified") is True and after_stage(h)
                 and type(h.get("pc")) is int and type(h.get("bank")) is int
                 and isinstance(h.get("expected_hex"), str) and re.fullmatch(r"(?:[0-9a-fA-F]{2})+", h["expected_hex"])]
    facts["native_report_frames"] = [r["frame"] for r in reports]
    all_keys = set(_party(_snap(stage)))

    def all_zero(party) -> bool:
        mons = _party({"party": party})
        return (bool(mons) and set(mons) == all_keys and len(mons) == len(_as_list(party))
                and {k for k, m in mons.items() if not (m.get("egg") or m.get("is_egg"))} == targets
                and all(mons[k].get("hp") == 0 for k in targets))

    if whole_party:
        # The production wire event is whiteout{}. Native per-mon faint can retire the pair first.
        facts['whiteout_handler_proved'] = False
        facts['proof_claim'] = 'native whiteout event sent + faint propagation; whiteout handler UNPROVEN'
        if whiteout and faint:
            facts['server_path'] = 'whiteout_event_and_faint_propagation'
        if not faint:
            reasons.append('a:linked_faint_wire_missing')
        if reports and any(not all_zero(r.get("party")) for r in reports):
            reasons.append("a:whiteout_report_party_not_all_zero")
        site_hits = battle.get("site_hits") or {}
        if "heal_party" not in site_hits:
            reasons.append("a:whiteout_heal_witness_missing")
        heals = [h for h in _as_list(site_hits.get("heal_party")) if isinstance(h, dict) and type(h.get("frame")) is int]
        witnesses = [h for h in qualified if h.get("site") == "LoseMoney" and all_zero(h.get("party"))
                     and any(h["frame"] <= r["frame"] and all_zero(r.get("party"))
                             and not any(h["frame"] <= heal["frame"] <= r["frame"] for heal in heals) for r in reports)]
        if any(h["frame"] <= heal["frame"] <= r["frame"] for h in qualified for heal in heals for r in reports):
            reasons.append("a:whiteout_healed_before_report")
        if not witnesses:
            reasons.append("a:whiteout_preheal_hook_missing")
    else:
        zero_reports = [r for r in reports if (_party(r).get(linked_key) or {}).get("hp") == 0]
        if reports and not zero_reports:
            reasons.append("a:faint_copyback_hp_not_zero")
        staged_slot = (_party(_snap(stage)).get(linked_key) or {}).get("slot")
        witnesses = [h for h in qualified if h.get("battle_hp") == 0 and h.get("slot") == staged_slot
                     and (_party(h).get(linked_key) or {}).get("slot") == staged_slot
                     and (_party(h).get(linked_key) or {}).get("hp") == 0
                     and any(h["frame"] <= r["frame"] and (_party(r).get(linked_key) or {}).get("slot") == staged_slot
                             for r in zero_reports)]
        if not witnesses:
            reasons.append("a:faint_copyback_hook_missing")
    facts["native_hook_frames"] = [h["frame"] for h in witnesses]
    return reasons, facts


def _propagation_reasons(ev: dict, whole_party: bool, natural: bool = False) -> tuple[list[str], dict]:
    """Shared B-side propagation proof; native variants additionally demand HP1 setup and the native event witnesses.
    Legacy HP0 scenarios retain hello reconciliation; native scenarios require the exact force_faint and DEAD link."""
    reasons, facts = _link_reasons(ev, before_stage=natural)
    reasons = [f"link:{r}" for r in reasons]
    facts["server_path"] = "none"
    stage = _step(ev, "a", "stage")
    if natural:
        linked_key = facts.get("a_key") or _caught_key(ev, "a")
        stage_reasons, want_targets = _natural_stage_reasons(stage, linked_key, whole_party)
        report_reasons, report_facts = _natural_report_reasons(ev, want_targets, linked_key, whole_party)
        reasons += stage_reasons + report_reasons
        facts.update(report_facts)
    if "a_key" not in facts:
        return reasons, facts
    mark = (ev.get("marks") or {}).get("stage") or {}
    wa, wb = (ev.get("wire") or {}).get("a", []), (ev.get("wire") or {}).get("b", [])
    if not natural:
        pre_a = _party(stage.get("before") or {})
        if not stage.get("ok"):
            reasons.append("a:stage_failed")
        targets = {w.get("key") for w in _as_list(stage.get("synth_writes")) if isinstance(w, dict)}
        want_targets = set(pre_a) if whole_party else {facts["a_key"]}
        if targets != want_targets:
            reasons.append("a:stage_wrong_target")
        post_a = _party(_snap(stage))
        if not want_targets or any((post_a.get(k) or {}).get("hp", 1) != 0 for k in want_targets):
            reasons.append("a:stage_not_landed")
        zero_hello = [h for h in c2s_events(wa, "hello", mark.get("a", 0))
                      if any(isinstance(e, dict) and e.get("key") == facts["a_key"] and e.get("hp") == 0
                             for e in _as_list(h.get("party")))]
        faint_ev = [m for m in c2s_events(wa, "faint", mark.get("a", 0)) if m.get("key") in want_targets]
        whiteout_ev = c2s_events(wa, "whiteout", mark.get("a", 0))
        facts["server_path"] = ("whiteout_event" if whiteout_ev else "faint_event" if faint_ev
                                else "hello_reconcile" if zero_hello else "none")
        if facts["server_path"] == "none":
            reasons.append("a:hp0_not_reported")
    linked = (ev.get("status") or {}).get("linked") or {}
    partners = {lk["b_key"]: lk["a_key"] for lk in _as_list(linked.get("links"))
                if isinstance(lk, dict) and lk.get("status") == "alive" and lk.get("a_key") in want_targets
                and lk.get("b_key")}
    facts["expected_partners"] = sorted(partners)
    if not partners:
        reasons.append("no_linked_partner")
    b_mark = mark.get("b", 0)
    b_mark = b_mark if type(b_mark) is int and b_mark >= 0 else 0
    kills = [c for c in s2c_commands(wb, b_mark) if c.get("cmd") in ("force_faint", "force_explode")]
    if natural and any(c.get("cmd") != "force_faint" for c in kills):
        reasons.append("b:force_faint_wrong_command")
    killed = {c.get("key") for c in kills if not natural or c.get("cmd") == "force_faint"}
    facts["b_force_faint_keys"] = sorted(str(k) for k in killed)
    if natural:
        counts = {kb: sum(c.get("cmd") == "force_faint" and c.get("key") == kb for c in kills)
                  for kb in partners}
        facts["b_force_faint_counts"] = counts
        for kb, count in counts.items():
            if count != 1:
                reasons.append(f"b:force_faint_count_not_one:{kb}")
    for kb in sorted(partners):
        if kb not in killed:
            reasons.append(f"b:force_faint_missing:{kb}")
    if killed - set(partners):
        reasons.append("b:force_faint_wrong_key")
    pre_b, await_b = _party(_snap(_step(ev, "b", "pre"))), _step(ev, "b", "await")
    post_b = _party(_snap(await_b))
    received = await_b.get("received")
    received = [received] if isinstance(received, dict) else _as_list(received)
    received_keys = {c.get("key") for c in received if isinstance(c, dict) and c.get("cmd") == "force_faint"}
    if not await_b.get("ok") or (natural and (received_keys != set(partners)
                                             or any(not isinstance(c, dict) or c.get("cmd") != "force_faint" for c in received))):
        reasons.append("b:force_faint_not_received_by_client")
    readbacks = await_b.get("force_faint_readback")
    readbacks = [readbacks] if isinstance(readbacks, dict) else _as_list(readbacks)
    if natural:
        facts["b_cartridge_witness"] = {}
    for kb in sorted(partners):
        if (pre_b.get(kb) or {}).get("hp", 0) <= 0:
            reasons.append(f"b:partner_not_alive_before:{kb}")
        readback_ok = False
        if natural and readbacks:
            readback_ok = any(isinstance(row, dict) and row.get("key") == kb and row.get("hp") == 0
                              and type(row.get("slot")) is int
                              and (_party(row).get(kb) or {}).get("hp") == 0
                              and (_party(row).get(kb) or {}).get("slot") == row["slot"]
                              and any(isinstance(cmd, dict) and cmd.get("cmd") == "force_faint" and cmd.get("key") == kb
                                      and type(cmd.get("frame")) is int and row.get("command_frame") == cmd["frame"]
                                      and type(row.get("frame")) is int and row["frame"] >= cmd["frame"]
                                      and row.get("seq") == cmd.get("seq") for cmd in received) for row in readbacks)
            if not readback_ok:
                reasons.append(f"b:force_faint_readback_invalid:{kb}")
        if kb not in post_b:
            if not readback_ok:
                reasons.append(f"b:partner_missing_from_party:{kb}")
            else:
                facts["b_cartridge_witness"][kb] = "force_faint_readback"
        elif post_b[kb].get("hp") != 0:
            reasons.append(f"b:cartridge_hp_not_zero:{kb}")
        elif natural:
            facts["b_cartridge_witness"][kb] = "await_snapshot"
    ignored = {"slot", "address", "addr", "wram"}

    def contents(row):
        return {field: value.lower() if field.endswith("_hex") and isinstance(value, str) else value
                for field, value in row.items() if field not in ignored}

    for key, mon in pre_b.items():
        if key in partners:
            continue
        post = post_b.get(key) or {}
        if post.get("hp") != mon.get("hp"):
            reasons.append(f"b:collateral_hp_change:{key}")
        complete = all(isinstance(row.get(field), str)
                       and re.fullmatch(r"[0-9a-fA-F]{" + str(size * 2) + r"}", row[field])
                       for row in (mon, post)
                       for field, size in (("record_hex", 48), ("ot_hex", 11), ("nickname_hex", 11)))
        if not complete:
            reasons.append(f"b:collateral_raw_evidence_missing:{key}")
            continue
        if contents(mon) != contents(post):
            reasons.append(f"b:collateral_record_change:{key}")
    after = (ev.get("status") or {}).get("after") or {}
    for lk in _as_list(after.get("links")):
        if isinstance(lk, dict) and lk.get("b_key") in partners and lk.get("status") == "alive":
            reasons.append(f"link_still_alive:{lk.get('area_id')}")
    if natural:
        for lk in _as_list(linked.get("links")):
            if not isinstance(lk, dict) or lk.get("b_key") not in partners or lk.get("a_key") not in want_targets:
                continue
            exact = [row for row in _as_list(after.get("links")) if isinstance(row, dict)
                     and all(row.get(field) == lk.get(field) for field in ("area_id", "a_key", "b_key"))]
            if not exact:
                reasons.append(f"dead_link_missing:{lk.get('area_id')}")
            elif not (exact[0].get("status") == "dead"
                      or (exact[0].get("status") == "memorial" and exact[0].get("killed_at"))):
                # A kill the server already advanced dead -> memorial (killed_at stamped) once both clients reached a safe
                # overworld is the same kill one stage later; a memorial row with no kill stamp proves nothing.
                reasons.append(f"link_not_dead:{lk.get('area_id')}")
    return reasons, facts


def oracle_faint(ev: dict) -> tuple[str, list[str], dict]:
    reasons, facts = _propagation_reasons(ev, whole_party=False)
    return ("PASS" if not reasons else "FAIL"), reasons, facts


def oracle_whiteout(ev: dict) -> tuple[str, list[str], dict]:
    reasons, facts = _propagation_reasons(ev, whole_party=True)
    return ("PASS" if not reasons else "FAIL"), reasons, facts


def oracle_faint_natural(ev: dict) -> tuple[str, list[str], dict]:
    reasons, facts = _propagation_reasons(ev, whole_party=False, natural=True)
    return ("PASS" if not reasons else "FAIL"), reasons, facts


def oracle_whiteout_natural(ev: dict) -> tuple[str, list[str], dict]:
    reasons, facts = _propagation_reasons(ev, whole_party=True, natural=True)
    return ("PASS" if not reasons else "FAIL"), reasons, facts


def oracle_boxsync(ev: dict) -> tuple[str, list[str], dict]:
    """S3. Capability off -> SKIPPED (and FAIL if a box command was sent anyway). On: A's first catch is quarantined
    (box_mon on A's wire, no box_mon_failed), A's cartridge shows it left the party and sits in the box census; after
    the link the server's party_mon brings it back and A's cartridge shows it in the party, out of the box."""
    wa, wb = (ev.get("wire") or {}).get("a", []), (ev.get("wire") or {}).get("b", [])
    box_cmds = [c for c in s2c_commands(wa) + s2c_commands(wb) if c.get("cmd") in ("box_mon", "party_mon")]
    if not ev.get("box_capable"):
        if box_cmds:
            return "FAIL", ["box_command_sent_while_capability_false"], {}
        return "SKIPPED", [BOX_CAPABILITY_OFF], {}
    reasons, facts = _link_reasons(ev)
    reasons = [f"link:{r}" for r in reasons]
    ka = _caught_key(ev, "a")
    if not ka:
        return "FAIL", reasons or ["a:catch_failed"], facts
    if f"skip quarantine: {ka[:8]}" in (ev.get("log") or ""):
        reasons.append("a:quarantine_skipped")
    if not any(c.get("cmd") == "box_mon" and c.get("key") == ka for c in s2c_commands(wa)):
        reasons.append("a:box_mon_missing")
    if any(m.get("key") == ka for m in c2s_events(wa, "box_mon_failed")):
        reasons.append("a:box_mon_failed")
    caught, boxed = _snap(_step(ev, "a", "catch_a")), _step(ev, "a", "boxed")
    if not boxed.get("ok"):
        reasons.append("a:box_mon_not_received_by_client")
    bsnap = _snap(boxed)
    if ka in _party(bsnap):
        reasons.append("a:box_mon_not_executed")
    if ka not in _box_keys(bsnap):
        reasons.append("a:boxed_mon_not_in_census")
    # the catch snapshot races the deposit (box_mon may already have run): compare counts only when it predates it
    if ka in _party(caught) and bsnap.get("party_count") != (caught.get("party_count") or 0) - 1:
        reasons.append("a:party_count_not_decremented")
    if not any(c.get("cmd") == "party_mon" and c.get("key") == ka for c in s2c_commands(wa)):
        reasons.append("a:party_mon_missing")
    back = _step(ev, "a", "withdrawn")
    wsnap = _snap(back)
    if not back.get("ok"):
        reasons.append("a:party_mon_not_received_by_client")
    if ka not in _party(wsnap):
        reasons.append("a:party_mon_not_executed")
    if ka in _box_keys(wsnap):
        reasons.append("a:withdrawn_mon_still_in_box")
    linked = ((ev.get("status") or {}).get("after") or {}).get("players", {}).get("a", {})
    if ka not in _as_list(linked.get("party_keys")):
        reasons.append("a:server_party_model_missing_key")
    return ("PASS" if not reasons else "FAIL"), reasons, facts


ORACLES = {"link": oracle_link, "boxsync": oracle_boxsync, "faint": oracle_faint, "whiteout": oracle_whiteout,
           "faint-natural": oracle_faint_natural, "whiteout-natural": oracle_whiteout_natural}


def play_steps(name: str, box_capable: bool) -> list[tuple[str, str]]:
    """The (role, label) sequence play_main drives for a scenario (the oracles read these labels)."""
    steps = [("a", "catch_a")]
    if box_capable:
        steps.append(("a", "boxed"))
    steps.append(("b", "catch_b"))
    if box_capable:
        steps.append(("a", "withdrawn"))
    if name in ("faint", "whiteout"):
        steps += [("b", "pre"), ("a", "stage"), ("b", "await")]
    elif name in ("faint-natural", "whiteout-natural"):
        steps += [("b", "pre"), ("a", "stage"), ("a", "battle"), ("b", "await")]
    return steps


def adapter_box_capability() -> bool:
    """Always True: the supports_box_mon capability was retired (every admitted client composes a box_mon
    executor, so the server always queues quarantine). BOX_CAPABILITY_OFF stays for an explicit False."""
    return True


def play_step_lines(text: str) -> dict:
    """PLAY_STEP lines of one side's result file, by label (unlabelled steps by op#k)."""
    out = {}
    for line in text.splitlines():
        if line.startswith("PLAY_STEP "):
            try:
                rec = json.loads(line[len("PLAY_STEP "):])
            except ValueError:
                continue
            out[rec.get("label") or f"{rec.get('op')}#{rec.get('k')}"] = rec
    return out


# ── live runner ────────────────────────────────────────────────────────────────────────────────────
def emu_health(pid: int, run=subprocess.run) -> dict:
    """Diagnostic for a side that went silent: is the EmuHawk window responding, and is the process still burning CPU?
    (A Lua script death leaves a responsive, CPU-active emulator; a hang does not.) Never raises."""
    script = (f"$p=Get-Process -Id {int(pid)} -ErrorAction Stop; $c1=$p.CPU; Start-Sleep -Milliseconds 1500; $p.Refresh(); "
              "[pscustomobject]@{responding=$p.Responding; cpu_delta=[math]::Round($p.CPU-$c1,2); threads=$p.Threads.Count} "
              "| ConvertTo-Json -Compress")
    try:
        out = run(["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True, timeout=30)
        return json.loads(out.stdout) if out.returncode == 0 and out.stdout.strip() else {
            "error": (out.stderr or out.stdout or "no output").strip()[:300]}
    except Exception as exc:  # noqa: BLE001 - a diagnostic must never mask the failure it describes
        return {"error": f"{type(exc).__name__}: {exc}"[:300]}


from tools.polished_live.runtime import kill_own as kill_pid, read_jsonl  # noqa: E402


def http_json(port: int, path: str) -> dict:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as r:
        return json.loads(r.read())




def client_line(result: Path, tag: str) -> dict | None:
    if result.is_file():
        for line in result.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith(tag + " "):
                return json.loads(line[len(tag) + 1:])
    return None


def exit_marker(result: Path, role: str) -> bool:
    return result.is_file() and f"RESULT: PASS duo-side-{role}" in result.read_text(encoding="utf-8", errors="replace")


def parse_args(argv=None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Two-instance Polished Crystal smoke (hello + census + reconnect, zero writes)")
    ap.add_argument("--fixture-a", help="SaveRAM for side a (env POL_FIXTURE_A; default POL_FIXTURE, then the warp fixture)")
    ap.add_argument("--fixture-b", help="SaveRAM for side b (env POL_FIXTURE_B; default POL_FIXTURE, then the warp fixture)")
    ap.add_argument("--distinct-identities", action="store_true",
                    help="require two genuinely distinct trainers: refuse identical fixtures, check each side against ITS fixture")
    ap.add_argument("--scenario", choices=(*PLAY_ORDER, "all"),
                    help="run a Soul Link PLAY scenario (duo_play.lua) instead of the zero-write smoke: "
                         + "; ".join(f"{k} = {v}" for k, v in PLAY_SCENARIOS.items()))
    return ap.parse_args(argv)


def plan_fixtures(args, env=None) -> tuple[dict, dict]:
    """Per-side fixture paths and their identities, decoded from the files themselves. In --distinct-identities mode this
    REFUSES (SystemExit) unless the two saves are two different trainers: nothing is staged or launched before it."""
    fixtures = dict(zip(("a", "b"), resolve_fixtures(args.fixture_a, args.fixture_b, env), strict=True))
    try:
        ident = {r: fixture_identity(fixtures[r].read_bytes()) for r in ("a", "b")}
    except (OSError, FixtureError) as exc:
        raise SystemExit(f"fixture unreadable as a Polished SaveRAM: {exc}") from exc
    if args.distinct_identities:
        refused = distinct_refusals(ident["a"], ident["b"])
        if refused:
            raise SystemExit(f"--distinct-identities refused: {refused} (a {fixtures['a']} {ident['a']['prefix']}, "
                             f"b {fixtures['b']} {ident['b']['prefix']})")
    return fixtures, ident


def main(argv=None) -> int:
    args = parse_args(argv)
    fixtures, ident = plan_fixtures(args)
    if args.scenario:
        return play_main(args, fixtures, ident)
    sys.path[:0] = [str(REPO / "tools"), str(REPO)]
    from dataclasses import replace

    from tools.polished_live import harness  # sym_table, free_port (reused unchanged)
    from tools.polished_live.runtime import pinned_rom_bytes, start_owned

    deadline = time.monotonic() + float(os.environ.get("POL_DUO_DEADLINE", "1200"))
    config = harness.launch_config()
    rom = pinned_rom_bytes(RELEASE, INTEGRATED_SHA1, patch=UPS)
    sha1 = hashlib.sha1(rom).hexdigest()
    run = LANE / "run"
    if run.exists():
        shutil.rmtree(run)
    srv_dir, wire_dir = run / "srv", run / "wire"
    srv_dir.mkdir(parents=True)
    (srv_dir / "rom_contract.json").write_text(json.dumps(contract_for(sha1)), encoding="utf-8")
    base_paths = {r: side_paths(run, r) for r in ("a", "b")}
    for r, p in base_paths.items():     # own ROM copy + own SaveRAM copy of ITS fixture + own config per side
        if stage_side(p, rom, fixtures[r]) != ident[r]["sha256"]:
            raise SystemExit(f"side {r}: private SaveRAM copy differs from its fixture {fixtures[r]}")
    print(f"[duo] staged overlay sha1 {sha1}; mode {'distinct-identities' if args.distinct_identities else 'single-fixture smoke'}; "
          f"lane {run}", flush=True)
    for r in ("a", "b"):
        print(f"[duo] fixture {r}: {fixtures[r]} sha256 {ident[r]['sha256']} (prefix {ident[r]['prefix']}) player "
              f"{ident[r]['name']!r} id {ident[r]['player_id']} expected party keys {ident[r]['keys']}", flush=True)

    port, http = harness.free_port(), harness.free_port()
    srv_log = open(run / "server.log", "w", encoding="utf-8")  # noqa: SIM115 - closed in finally
    srv = subprocess.Popen([sys.executable, "-m", "server.server", "--host", "127.0.0.1", "--port", str(port),
                            "--http-port", str(http), "--data-dir", str(srv_dir), "--verbose", "--wire-log", str(wire_dir)],
                           cwd=str(REPO), stdout=srv_log, stderr=subprocess.STDOUT)
    procs: dict[str, subprocess.Popen] = {}
    pids: dict[str, list] = {"a": [], "b": []}
    gen = {"a": 0, "b": 0}

    def launch(role: str) -> dict:
        gen[role] += 1
        p = side_paths(run, role, gen[role])
        side_config = replace(config, rom=Path(p["rom"]), sram=Path(p["sram_dir"]),
                              save_name=Path(p["saveram"]).name)
        procs[role] = start_owned(side_config, "tools/polished_live/duo.lua", Path(p["run"]),
                                  side_env(p, role, "127.0.0.1", port), config_path=p["config"])
        pids[role].append(procs[role].pid)
        print(f"[duo] EmuHawk {role} generation {gen[role]} pid {procs[role].pid}", flush=True)
        return p

    def wait(what: str, pred, timeout: float) -> None:
        end = min(time.monotonic() + timeout, deadline)
        while time.monotonic() < end:
            if pred():
                return
            for role, pr in procs.items():
                if pr.poll() is not None and what != "exit":
                    raise RuntimeError(f"{what}: EmuHawk {role} pid {pr.pid} exited early rc={pr.returncode}")
            time.sleep(1.5)
        raise RuntimeError(f"timeout waiting for {what}")

    def server_view() -> dict:
        wire = {r: parse_wire(read_jsonl(wire_dir / f"wire_{r}.jsonl")) for r in ("a", "b")}
        log_path = srv_dir / "slink.log"
        log = parse_log(log_path.read_text(encoding="utf-8", errors="replace") if log_path.is_file() else "")
        return {"status": http_json(http, "/api/status"), "raw": http_json(http, "/api/debug/raw_state"), "wire": wire, "log": log}

    sides: dict = {}
    notes: list[str] = []
    ok, reasons = False, ["driver_error"]
    try:
        wait("server http", lambda: bool(http_json(http, "/api/status")), 40)
        print(f"[duo] server pid {srv.pid} tcp {port} http {http} data {srv_dir}", flush=True)
        pa, pb = launch("a"), launch("b")
        wait("both clients hello", lambda: client_line(Path(pa["result"]), "DUO_CLIENT") and client_line(Path(pb["result"]), "DUO_CLIENT"), 600)
        view1 = server_view()
        print(f"[duo] phase 1: a {client_line(Path(pa['result']), 'DUO_CLIENT')}\n[duo] phase 1: b {client_line(Path(pb['result']), 'DUO_CLIENT')}", flush=True)
        b_before = hashlib.sha256(Path(base_paths["b"]["saveram"]).read_bytes()).hexdigest()
        kill_pid(procs["b"], "EmuHawk b (reconnect test)")
        wait("exit", lambda: server_view()["wire"]["b"]["disconnects"] >= 1, 60)
        mid = server_view()
        notes.append(f"b killed: server saw b disconnect; a disconnects so far {mid['wire']['a']['disconnects']}, "
                     f"a connected {mid['status']['players']['a']['connected']}, b connected {mid['status']['players']['b']['connected']}")
        b_after = hashlib.sha256(Path(base_paths["b"]["saveram"]).read_bytes()).hexdigest()
        pb2 = launch("b")
        wait("b reconnect hello", lambda: client_line(Path(pb2["result"]), "DUO_CLIENT"), 600)
        view2 = server_view()
        a_continuous = view2["wire"]["a"]["connects"] == 1 and view2["wire"]["a"]["disconnects"] == 0
        for p in (pa, pb2):    # stop file -> L.finish -> client.exit (the exit marker)
            Path(p["run"], "stop").write_text("stop", encoding="utf-8")
        wait("exit", lambda: all(pr.poll() is not None for pr in procs.values()), 90)
        fin = server_view()
        for role in ("a", "b"):
            if procs[role].poll() is None:
                kill_pid(procs[role], f"EmuHawk {role}")

        for role, final_p in (("a", pa), ("b", pb2)):
            w, st, log = fin["wire"][role], view2["status"]["players"][role], fin["log"]
            hellos = w["hellos"]
            h1 = hello_view(hellos[0] if hellos else None, 0, role, log, view1["status"]["players"][role])
            h2 = hello_view(hellos[1] if len(hellos) > 1 else None, 1, role, log, st) if role == "b" else None
            side_ports = [log["ports"][r["conn"] - 1] for r in hellos if r.get("conn") and r["conn"] - 1 < len(log["ports"])]
            sides[role] = {"player": role, "paths": {k: base_paths[role][k] for k in ISOLATED},
                           "client_port": side_ports[0] if side_ports else None, "client_ports": side_ports,
                           "pids": pids[role], "hello": h1, "reconnect": h2,
                           "continuous": a_continuous if role == "a" else None,
                           "client_writes": (client_line(Path(final_p["result"]), "DUO_FINAL") or {}).get("lua_writes", -1)
                           + (client_line(Path(final_p["result"]), "DUO_FINAL") or {}).get("panel_writes", 0),
                           "commands": w["commands"], "exit_marker": exit_marker(Path(final_p["result"]), role),
                           "client_hello_line": client_line(Path(final_p["result"]), "DUO_CLIENT")}
            if role == "a":      # the side that stayed up: its server-side identity AFTER b's reconnect
                fa = view2["status"]["players"]["a"]
                sides["a"]["final"] = {"trainer_name": fa.get("trainer_name"), "party_keys": list(fa.get("party_keys") or []),
                                       "identity_error": fa.get("identity_error") or ""}
        if args.distinct_identities:
            ok, reasons = evaluate_distinct(sides, sha1, ident)
        else:
            ok, reasons = evaluate(sides, sha1)
        notes.append(f"b SaveRAM sha256 before kill {b_before[:16]} after kill {b_after[:16]} (unchanged: {b_before == b_after})")
    except Exception as exc:  # noqa: BLE001 - any harness failure is a FAIL with the reason, never a silent pass
        reasons = [f"driver_error: {exc}"]
        ok = False
    finally:
        for role, pr in procs.items():
            kill_pid(pr, f"EmuHawk {role}")
        kill_pid(srv, "server")
        srv_log.close()
    print("[duo] ---- evidence ----", flush=True)
    for role, s in sorted(sides.items()):
        print(f"[duo] side {role}: EmuHawk pids {s['pids']} client ports {s['client_ports']}", flush=True)
        for tag, h in (("hello", s["hello"]), ("reconnect", s["reconnect"])):
            if h:
                print(f"[duo]   {tag}: seen={h.get('seen')} admission={h.get('admission')} adapter={h.get('adapter')} "
                      f"kind={h.get('artifact_kind')} rom_sha1={h.get('rom_sha1')}\n[duo]     party {h.get('party_count')} "
                      f"keys {h.get('party_keys')} (server party_keys {h.get('server_party_keys')})\n[duo]     box census "
                      f"generation {h.get('pc_boxes_generation')} client entries {len(h.get('pc_boxes') or [])} server "
                      f"stored {h.get('server_pc_boxes_n')}\n[duo]     identity: ot {h.get('ot_id')} "
                      f"name {h.get('trainer_name')} server status trainer_name {h.get('server_trainer_name')} identity_error {h.get('identity_error')!r}", flush=True)
        if s.get("final"):
            print(f"[duo]   status after b reconnected: {s['final']}", flush=True)
        print(f"[duo]   continuous={s['continuous']} client_writes={s['client_writes']} server_commands={s['commands']} "
              f"exit_marker={s['exit_marker']}", flush=True)
    for n in notes:
        print(f"[duo] {n}", flush=True)
    if args.distinct_identities:
        disclosure = (f"fixtures a {ident['a']['prefix']} ({ident['a']['name']}, id {ident['a']['player_id']}) and b "
                      f"{ident['b']['prefix']} ({ident['b']['name']}, id {ident['b']['player_id']}): SYNTH identity derivative "
                      f"(O-33), not independently played; two distinct identities admitted, hello + census + reconnect only, "
                      f"no trade exercised")
    else:
        disclosure = (f"both sides share ONE saved identity (fixture {ident['a']['prefix']}): hello + census smoke only, "
                      f"no trade possible")
    print(f"[duo] server data dir {srv_dir}; wire {wire_dir}; staged ROM sha1 {sha1}; {disclosure}", flush=True)
    (run / "evidence.json").write_text(json.dumps(
        {"ok": ok, "reasons": reasons, "mode": "distinct-identities" if args.distinct_identities else "single-fixture",
         "fixtures": {r: {"path": str(fixtures[r]), **ident[r]} for r in ("a", "b")}, "disclosure": disclosure,
         "sides": sides, "notes": notes}, indent=1, default=str), encoding="utf-8")
    print(f"RESULT: {'PASS' if ok else 'FAIL'} polished-duo-smoke" + ("" if ok else f" -- {reasons}"), flush=True)
    return 0 if ok else 1


# ── PLAY runner (`--scenario NAME|all`) ────────────────────────────────────────────────────────────
def play_main(args, fixtures: dict, ident: dict) -> int:
    """Run each selected scenario as its own fresh run; one RESULT line per scenario, rc 0 only if none FAILED."""
    box_capable = adapter_box_capability()
    verdicts = {}
    for name in play_names(args.scenario):
        if name == "boxsync" and not box_capable:
            verdict, reasons, facts = oracle_boxsync({"box_capable": False, "wire": {}})
            print(f"[play] {name}: not launched -- {reasons[0]}", flush=True)
        else:
            verdict, reasons, facts = play_run(args, name, fixtures, ident, box_capable)
        verdicts[name] = verdict
        tail = "" if verdict == "PASS" else f" -- {reasons}"
        print(f"RESULT: {verdict} polished-duo-play-{name}{tail}", flush=True)
    return 1 if any(v == "FAIL" for v in verdicts.values()) else 0


def play_run(args, name: str, fixtures: dict, ident: dict, box_capable: bool) -> tuple[str, list[str], dict]:
    sys.path[:0] = [str(REPO / "tools"), str(REPO)]
    from dataclasses import replace

    from tools.polished_live import harness
    from tools.polished_live.runtime import pinned_rom_bytes, start_owned

    deadline = time.monotonic() + float(os.environ.get("POL_DUO_DEADLINE", "3600"))
    config = harness.launch_config()
    rom = pinned_rom_bytes(RELEASE, INTEGRATED_SHA1, patch=UPS)
    sha1 = hashlib.sha1(rom).hexdigest()
    run = LANE / f"play-{name}"
    if run.exists():
        raise SystemExit("evidence already exists; choose a fresh POL_DUO_LANE: " + str(run))
    srv_dir, wire_dir = run / "srv", run / "wire"
    srv_dir.mkdir(parents=True)
    (srv_dir / "rom_contract.json").write_text(json.dumps(contract_for(sha1)), encoding="utf-8")
    paths = {r: side_paths(run, r) for r in ("a", "b")}
    for r, p in paths.items():
        if stage_side(p, rom, fixtures[r]) != ident[r]["sha256"]:
            raise SystemExit(f"side {r}: private SaveRAM copy differs from its fixture {fixtures[r]}")
    print(f"[play] {name}: {PLAY_SCENARIOS[name]}; overlay sha1 {sha1}; box capability {box_capable}; lane {run}",
          flush=True)
    port, http = harness.free_port(), harness.free_port()
    srv_log = open(run / "server.log", "w", encoding="utf-8")  # noqa: SIM115 - closed in finally
    srv = subprocess.Popen([sys.executable, "-m", "server.server", "--host", "127.0.0.1", "--port", str(port),
                            "--http-port", str(http), "--data-dir", str(srv_dir), "--verbose", "--wire-log", str(wire_dir)],
                           cwd=str(REPO), stdout=srv_log, stderr=subprocess.STDOUT)
    procs: dict[str, subprocess.Popen] = {}
    ev: dict = {"scenario": name, "box_capable": box_capable, "steps": {"a": {}, "b": {}}, "wire": {"a": [], "b": []},
                "marks": {}, "status": {}, "log": ""}
    nstep = {"a": 0, "b": 0}
    finished: set[str] = set()      # sides that ran their stop step: their exit is expected, not an early death

    def result_text(role: str) -> str:
        f = Path(paths[role]["result"])
        return f.read_text(encoding="utf-8", errors="replace") if f.is_file() else ""

    def wire(role: str) -> list:
        return read_jsonl(wire_dir / f"wire_{role}.jsonl")

    def wait(what: str, pred, timeout: float):
        end = min(time.monotonic() + timeout, deadline)
        while time.monotonic() < end:
            got = pred()
            if got:
                return got
            for role, pr in procs.items():
                if pr.poll() is not None and role not in finished and what != "exit":
                    raise RuntimeError(f"{what}: EmuHawk {role} pid {pr.pid} exited early rc={pr.returncode}")
            time.sleep(1.0)
        raise RuntimeError(f"timeout waiting for {what}")

    def step(role: str, label: str, op: dict, timeout: float = 600) -> dict:
        nstep[role] += 1
        k = nstep[role]
        Path(paths[role]["run"], f"step_{k}.json").write_text(json.dumps({**op, "label": label}), encoding="utf-8")
        rec = wait(f"{role} step {k} {label}", lambda: play_step_lines(result_text(role)).get(label), timeout)
        ev["steps"][role][label] = rec
        print(f"[play] {role} {label}: ok={rec.get('ok')} why={rec.get('why')}", flush=True)
        return rec

    def snap_status(label: str) -> dict:
        st = http_json(http, "/api/status")
        keep = ("connected", "party_keys", "admission", "identity_error", "nuzlocke_active")
        ev["status"][label] = {"links": st.get("links"), "area_states": st.get("area_states"),
                               "pending_captures": st.get("pending_captures"),
                               "players": {p: {k: st["players"][p].get(k) for k in keep} for p in ("a", "b")}}
        return st

    def mark(label: str) -> None:
        ev["marks"][label] = {r: len(wire(r)) for r in ("a", "b")}


    def server_ready() -> bool:
        """A not-yet-listening child remains inside the existing bounded startup wait."""
        try:
            return bool(http_json(http, "/api/status"))
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            return False
    verdict, reasons, facts = "FAIL", ["driver_error"], {}
    try:
        wait("server http", server_ready, 40)
        for role in ("a", "b"):
            p = paths[role]
            side_config = replace(config, rom=Path(p["rom"]), sram=Path(p["sram_dir"]),
                                  save_name=Path(p["saveram"]).name)
            procs[role] = start_owned(side_config, "tools/polished_live/duo_play.lua", Path(p["run"]),
                                      side_env(p, role, "127.0.0.1", port), config_path=p["config"])
            print(f"[play] EmuHawk {role} pid {procs[role].pid} (server pid {srv.pid} tcp {port} http {http})", flush=True)
        wait("both sides ready", lambda: "PLAY_READY" in result_text("a") and "PLAY_READY" in result_text("b"), 600)
        mark("start")
        snap_status("start")
        natural = name in ("faint-natural", "whiteout-natural")
        attempts = 1 if natural else 4
        a = step("a", "catch_a", {"op": "catch", "attempts": attempts}, 1200)
        ka = (a.get("capture") or {}).get("key")
        if a.get("ok") and ka:
            if box_capable:
                step("a", "boxed", {"op": "await_cmd", "cmd": "box_mon", "key": ka, "frames": 3600}, 300)
            b = step("b", "catch_b", {"op": "catch", "attempts": attempts}, 1200)
            kb = (b.get("capture") or {}).get("key")
            if b.get("ok") and kb:
                def linked():
                    lk = http_json(http, "/api/status").get("links") or []
                    return any(x.get("a_key") == ka and x.get("b_key") == kb for x in lk)
                try:
                    wait("link on the server", linked, 60)
                except RuntimeError as exc:
                    print(f"[play] {exc}", flush=True)
                if box_capable:
                    step("a", "withdrawn", {"op": "await_cmd", "cmd": "party_mon", "key": ka, "frames": 3600}, 300)
                snap_status("linked")
                if name in ("faint", "whiteout"):
                    step("b", "pre", {"op": "snapshot"}, 120)
                    mark("stage")
                    op = {"op": "synth_hp0", "all": True} if name == "whiteout" else {"op": "synth_hp0", "keys": [ka]}
                    step("a", "stage", op, 300)
                    step("b", "await", {"op": "await_cmd", "cmd": "force_faint", "key": kb, "frames": 3600}, 300)
                    time.sleep(3)
                elif natural:
                    step("b", "pre", {"op": "snapshot"}, 120)
                    mark("stage")
                    whole = name == "whiteout-natural"
                    op = {"op": "synth_hp1", "all": True, "lead": ka} if whole else {
                        "op": "synth_hp1", "keys": [ka], "lead": ka}
                    staged = step("a", "stage", op, 120)
                    if staged.get("ok"):
                        step("a", "battle", {"op": "lose_native", "key": ka, "all": whole, "frames": 72000 if whole else 24000}, 600)
                        step("b", "await", {"op": "await_cmd", "cmd": "force_faint", "key": kb, "frames": 3600}, 300)
                        time.sleep(3)
            snap_status("after")
        for role in ("a", "b"):
            step(role, f"stop_{role}", {"op": "stop"}, 120)
            finished.add(role)
        wait("exit", lambda: all(pr.poll() is not None for pr in procs.values()), 90)
    except Exception as exc:  # noqa: BLE001 - any harness failure is a FAIL with the reason, never a silent pass
        reasons = [f"driver_error: {exc}"]
        ev["driver_error"] = str(exc)
        if "timeout waiting for" in str(exc):
            ev["emu_health"] = {r: emu_health(pr.pid) if pr.poll() is None else {"exited": pr.returncode}
                                for r, pr in procs.items()}
            ev["lua_beat"] = {r: (Path(paths[r]["run"]) / f"beat_{r}.txt").read_text(encoding="utf-8", errors="replace")
                              if (Path(paths[r]["run"]) / f"beat_{r}.txt").is_file() else None for r in procs}
            print(f"[play] DIAG emu_health {ev['emu_health']} lua_beat {ev['lua_beat']}", flush=True)
    finally:
        for role, pr in procs.items():
            kill_pid(pr, f"EmuHawk {role}")
        kill_pid(srv, "server")
        srv_log.close()
    ev["wire"] = {r: wire(r) for r in ("a", "b")}
    log_path = srv_dir / "slink.log"
    ev["log"] = log_path.read_text(encoding="utf-8", errors="replace") if log_path.is_file() else ""
    if "driver_error" not in ev:
        verdict, reasons, facts = ORACLES[name](ev)
    finals = {r: client_line(Path(paths[r]["result"]), "PLAY_FINAL") for r in ("a", "b")}
    b_fixture = (f"fixture b: SYNTH identity derivative {ident['b']['prefix']} (O-33, derive_save.py)"
                 if args.distinct_identities else "fixture b: a copy of fixture a (one shared trainer identity)")
    disclosure = {"setup_synth": ["fixture a: O-33 SYNTH warp fixture (Route 29, five SYNTH level-50 mons)", b_fixture]
                  + SYNTH_SETUP[name],
                  "native": ["wild catches (walk, Bag -> Ball, throw) firing the capture_party site",
                             "the client's capture/hello lines", "every server rule and command",
                             "the receiving client's own write at its overworld hold", "the cartridge read-backs"]}
    for line in disclosure["setup_synth"]:
        print(f"[play] SYNTH setup: {line}", flush=True)
    print(f"[play] {name}: facts {facts}; client finals {finals}", flush=True)
    (run / "evidence.json").write_text(json.dumps(
        {"verdict": verdict, "reasons": reasons, "facts": facts, "disclosure": disclosure, "finals": finals,
         "fixtures": {r: {"path": str(fixtures[r]), **ident[r]} for r in ("a", "b")}, "evidence": ev},
        indent=1, default=str), encoding="utf-8")
    return verdict, reasons, facts


if __name__ == "__main__":
    sys.exit(main())
