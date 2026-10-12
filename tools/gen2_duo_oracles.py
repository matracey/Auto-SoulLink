"""Independent Gen 2 duo witness, link and faint-propagation oracles (H2/H5).

`faint_oracle` decodes immutable LINK_SAVE images and final native saves, requires
A's production battle_faint plus a DEAD server pair and force_faint issuance, and
verifies B's measured checkpoint, permit spans and exact status/HP byte delta.
`on_verified`, when supplied to either scenario oracle, receives the decoded
{a, b, area, titles, status} facts only after every check passes; return stays None.
Gen 2 case/title and boot-file SHA-256 bindings are mandatory, including when the
runner supplies qualified per-side OT IDs and boot fixtures.

Both functions are PLAIN, testable without an emulator or a DuoRun instance. Codex wires them
into tools/e2e_duo.py as thin bound-method wrappers, because tools/duo_oracle_pipeline.py
resolves `EvidenceContract` callbacks via `getattr(owner, name)` -- the registered name must be
a real method on the runner:

    # tools/e2e_duo.py, on the DuoRun class:
    def check_gen2_save_witness(self, results):
        return gen2_duo_oracles.check_save_witness(results)

    def assert_gen2_link_saved(self, results, **kwargs):
        return gen2_duo_oracles.link_oracle(results, data_dir=self.data_dir, **kwargs)

    FAMILY_EVIDENCE["gen2_new"] = EvidenceContract("check_gen2_save_witness", require_oracle=True)
    # SCENARIOS["link"] (gen2_new family) row: "oracle": "assert_gen2_link_saved",
    # "oracle_kwargs": {} -- {} is correct for EVERY title pairing (C<->C, G<->S, C<->G, O-16):
    # no shared title is supplied by the caller. Each side's
    # title/fixture/expected-OT are derived from that side's OWN result-text markers (below), so the
    # same static oracle_kwargs row serves every GAMES pairing without per-lane configuration.

Both raise RuntimeError (never return False) naming the instance and the exact reason on any
refusal; both return None on success. `validate_pipeline` binds `witness(results)` with zero
kwargs and `oracle(results, **oracle_kwargs)`, so `check_save_witness` takes exactly one
positional argument and every other `link_oracle` parameter besides `results` must be a keyword.

## Per-instance result-text markers this module parses (H1 driver contract, fixed 2026-09-23)

One JSON object on the same line as its tag, in each instance's `results[inst]` text
(`D.result`); the LAST occurrence of a repeated tag wins:

- `DUO_GEN2 {...}`: player, scenario, attempt, case, title, rom_sha1, fixture_sha256. `case` names
  the committed boot fixture 1:1 (tests/fixtures/gen2/<case>.SaveRAM, e.g. "gold_battle") -- this
  is where a title's boot save comes from when the caller does not override it.
- `SAVE_WITNESS {...}`: frame, save_completed_frame, gate_saves, client_saves, cartram_sha256
  (sha256 of CartRAM[0:0x8000] -- the first 32768 bytes of the flushed file), cartram_bytes
  (32768), saveram_path (absolute, forward slashes), saveram_bytes (32768 + 22 = 32790, the
  22-byte RTC trailer included), flushed_matches (bool, the driver's own claim -- re-verified
  here independently, never trusted alone). Optional snapshot_path names an immutable raw
  CartRAM or full SaveRAM image, authenticated against cartram_sha256 before scratch comparison.
- `CLIENT {...}`: qualification, production_admitted, pack, title ("crystal"/"gold"/"silver"),
  rom_sha1. `title` selects the gen2_codec layout for THIS side only -- Gold/Silver/Crystal never
  share a layout even when they share an area id (route_29 is title-independent, the party/save
  struct offsets are not).
- `HELLO {...}`: frame, ot_id. The client's own trainer id, reported at connect time before any
  capture -- this is where a title's expected captured-mon OT id comes from when the caller does
  not override it (a self-caught wild mon's OT is always its own trainer's id).
- `RECEIPT {...}`: PASS-only; schema, header, title, rom_sha1, ... . Cross-checked against
  CLIENT/DUO_GEN2 when present; never required, since a FAIL run has no RECEIPT line.
- `ENGINE_CAPTURE {...}`: frame, site_id, acquisition, area_id, destination, slot, key,
  species_id, level. Cross-checked against the independently PYDEC-decoded flushed party.

### Per-side title, never a caller argument

Neither function accepts a `title` parameter. Each side's title is derived from that instance's
OWN markers and cross-checked for internal agreement (`_boot_marker`): DUO_GEN2.title must equal
CLIENT.title (same rom_sha1), and RECEIPT.title/rom_sha1 must agree too when a RECEIPT line is
present. A mismatch is refused, never silently resolved by trusting one marker over another --
this catches a driver that started the wrong title's client, or mixed up which side's result text
is which, independently of the H1 driver's own `SLINK_GEN2_TITLE` self-check. The two sides may
be different titles (Gold<->Silver, Crystal<->Gold, owner ruling O-16): `for_foundation` is called
per instance with ITS OWN cross-checked title, never a shared/assumed one.

`ot_ids` and `boot_saveram` accept the runner's qualified per-instance preflight data.
Standalone callers may omit them and derive them from HELLO.ot_id / DUO_GEN2.case;
either path requires the marker's fixture_sha256 to match the complete boot file.

`check_save_witness` only needs `SAVE_WITNESS` + `CLIENT` + `DUO_GEN2` (saveram_path is absolute,
so no runner/data_dir dependency). `link_oracle` additionally needs `HELLO`, `ENGINE_CAPTURE`, the
two boot fixtures (committed SaveRAM this scenario always boots from) and the server's persisted
`links.json` under `data_dir`.

### Removed guard: "OT ids must differ" (H2b carry, see git log -p / commit 23fbc80e)

An earlier revision refused up front when the (then caller-supplied) `ot_ids` mapping had equal
values for "a" and "b". That guard validated the CALLER'S input, not any observed evidence, so a
mistaken-but-harmless ot_ids argument would abort the oracle before it read a single byte of
either save -- and conversely proved nothing about whether the two sides actually caught the same
mon, since the DECODED saves were never consulted. It was removed once the real invariant already
had a fitting check a few lines later: `decoded["a"]["key"] == decoded["b"]["key"]`, which compares
the two INDEPENDENTLY PYDEC-decoded full identity keys (DV:OT:species) and refuses if they match.
That check is grounded in the flushed saves themselves, catches the identical-capture case the old
guard was trying to catch, and (unlike the old guard) still works now that ot_ids is normally
derived per side from each instance's own HELLO marker rather than supplied as one shared mapping.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


class ClauseUnobserved(RuntimeError):
    """A independently valid clean pair did not exercise the requested RNG-dependent clause."""


def _clause_need(condition, reason):
    if not condition:
        raise RuntimeError(f"clause: {reason}")

SAVE_WITNESS_FIELDS = ("frame", "save_completed_frame", "gate_saves", "client_saves",
                       "cartram_sha256", "cartram_bytes", "saveram_path", "saveram_bytes",
                       "flushed_matches")
DUO_GEN2_FIELDS = ("case", "title", "rom_sha1", "fixture_sha256")
CARTRAM_BYTES = 0x8000
SAVERAM_BYTES = CARTRAM_BYTES + 22  # 22-byte RTC trailer, outside the equality (P3b.7 plan)


def _last_tagged(text, tag):
    """The last `TAG {json}` line's parsed payload, or None if the tag never appears."""
    lines = [line for line in (text or "").splitlines() if line.startswith(tag + " ")]
    if not lines:
        return None
    payload = lines[-1][len(tag) + 1:]
    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"malformed {tag} marker {payload!r}: {exc}") from exc


def _require_fields(marker, fields, label):
    if not isinstance(marker, dict):
        raise RuntimeError(f"{label} marker must be an object")
    missing = [field for field in fields if field not in marker]
    if missing:
        raise RuntimeError(f"{label} marker missing field(s) {missing}: {marker}")
    for field in fields:
        value = marker[field]
        if value is None:
            raise RuntimeError(f"{label} {field} cannot be null")
        if field in ("rom_sha1", "fixture_sha256", "cartram_sha256"):
            length = 40 if field == "rom_sha1" else 64
            if not isinstance(value, str) or re.fullmatch(rf"[0-9a-fA-F]{{{length}}}", value) is None:
                raise RuntimeError(f"{label} {field} must be a {length}-hex hash")
        if field in ("case", "title", "saveram_path") and (not isinstance(value, str) or not value):
            raise RuntimeError(f"{label} {field} must be nonempty text")


def _fixture_path(case):
    """The committed boot fixture for a DUO_GEN2 `case` name, e.g. "gold_battle" ->
    tests/fixtures/gen2/gold_battle.SaveRAM (every gen2_new duo case is named after its fixture
    1:1, lua/tests/duo/duo_gen2_main.lua LAUNCH CONTRACT)."""
    return REPO_ROOT / "tests/fixtures/gen2" / f"{case}.SaveRAM"


def _duo_marker(inst, text):
    """DUO_GEN2 marker for this instance: case/title/rom_sha1, required."""
    duo = _last_tagged(text, "DUO_GEN2")
    if duo is None:
        raise RuntimeError(f"{inst}: missing DUO_GEN2 marker")
    _require_fields(duo, DUO_GEN2_FIELDS, f"{inst} DUO_GEN2")
    from tools.gen2_fixtures import BY_NAME
    spec = BY_NAME.get(duo["case"])
    if spec is None or spec.title != duo["title"]:
        raise RuntimeError(f"{inst}: inconsistent title markers: case {duo['case']!r} does not belong to title {duo['title']!r}")
    return duo


def _executed_identity(title, kind="clean"):
    if kind not in ("clean", "overlay"):
        raise RuntimeError("unknown Gen 2 executed artifact")
    directory = REPO_ROOT / f"data/games/gen2_{title}"
    if kind == "clean":
        profile = json.loads((directory / "profile.json").read_text())["titles"][title]
        return profile["rom_sha1"], None
    publication = json.loads((REPO_ROOT / "data/gen2/overlay_provenance.json").read_text())
    raw = (directory / "overlay/binding.json").read_bytes().replace(b"\r\n", b"\n")
    binding = json.loads(raw)
    sha = publication["outputs"]["poke" + title]["sha1"]
    if binding.get("rom_sha1") != sha or binding.get("kind") != kind or binding.get("title") != title:
        raise RuntimeError("overlay executed binding differs from publication")
    return sha, hashlib.sha256(raw).hexdigest()


def _checkpoint_for(title, kind="clean"):
    directory = REPO_ROOT / f"data/games/gen2_{title}"
    if kind == "overlay":
        return json.loads((directory / "overlay/binding.json").read_text())["checkpoint"]
    return json.loads((directory / "write_checkpoint.json").read_text())["titles"][title]


# C-5 (docs/gen2/RANDOMIZER_GATES.md, docs/gen2/C5_RUNBOOK.md): None for every unrandomized run, whose executed
# artifacts stay pinned to the published ones exactly as before. A Manager-randomized run sets it with
# randomized_from_run(<dir holding rom_contract.json + roms/>): {"a"/"b": the side's contract sha1, "roms": {side: path}}.
# A side whose CLIENT says rand_overlay must then execute exactly its own contract sha1 on the overlay binding, and it
# is judged as the overlay layout it is (UPR writes no overlay byte, docs/gen2/RANDOMIZER.md).
RANDOMIZED = None


def randomized_from_run(run_dir):
    run_dir = Path(run_dir)
    contract = json.loads((run_dir / "rom_contract.json").read_text(encoding="utf-8"))
    sides = {side: contract["players"][side]["rom_sha1"] for side in ("a", "b")}
    roms = {side: run_dir / "roms" / f"{side}.gbc" for side in sides}
    for side, rom in roms.items():
        if hashlib.sha1(rom.read_bytes()).hexdigest() != sides[side]:
            raise RuntimeError(f"{side}: randomized ROM differs from its contract sha1")
    return {**sides, "roms": roms}


def _randomized_artifact(inst, client, duo):
    side = str(inst)[:1]
    if RANDOMIZED is None or side not in ("a", "b"):
        raise RuntimeError(f"{inst}: rand_overlay CLIENT in a run with no randomized contract")
    _sha, binding = _executed_identity(client["title"], "overlay")
    if (duo.get("artifact_kind") != "overlay" or client.get("rom_sha1") != RANDOMIZED[side]
            or client.get("qualification") != "PHYSICAL_RECEIPTED" or client.get("production_admitted") is not True
            or client.get("binding_sha256") != binding or duo.get("binding_sha256") != binding):
        raise RuntimeError(f"{inst}: randomized artifact is not its contract cartridge on the overlay binding")
    return "overlay", client["rom_sha1"]


def _randomized_rom(inst):
    if RANDOMIZED is None:
        raise RuntimeError(f"{inst}: no randomized contract")
    return Path(RANDOMIZED["roms"][str(inst)[:1]]).read_bytes()


def _randomized_gift_species(inst, title, row_prefix):
    """The species the executed randomized ROM gives at a pack gift row (server scan_randomized, the R4 read)."""
    from server.adapters.gen2_gsc import scan_randomized

    rows = [g for g in scan_randomized(_randomized_rom(inst), title)["gifts"] if g["id"].startswith(row_prefix)]
    if len(rows) != 1:
        raise RuntimeError(f"{inst}: no single {row_prefix} gift row in the randomized ROM")
    return rows[0]["species"]


def _randomized_npc_trade(inst, title, wanted, ot_id):
    """(given species, OT) of the NPCTrades row (data/events/npc_trades.asm npctrade, 32 bytes: dialog, wanted, given,
    nickname[11], DVs[2], item, OT ID (dw), ...) that still wants `wanted` from `ot_id`: trades=given keeps the request."""
    sym = (REPO_ROOT / f"data/gen2/{title}_slink.sym").read_text(encoding="utf-8")
    bank, addr = next(line.split()[0].split(":") for line in sym.splitlines() if line.endswith(" NPCTrades"))
    base, rom = int(bank, 16) * 0x4000 + int(addr, 16) - 0x4000, _randomized_rom(inst)
    rows = [rom[base + 32 * i:base + 32 * (i + 1)] for i in range(7)]
    hits = [row for row in rows if row[1] == wanted and int.from_bytes(row[17:19], "little") == ot_id]
    if len(hits) != 1:
        raise RuntimeError(f"{inst}: no single NPC trade wanting {wanted} from OT {ot_id} (trades=given required)")
    return hits[0][2], ot_id


# The server's own refusal texts (server/server.py _decide_admission), matched EXACTLY: a different wording is a finding
# to record, never a reason to loosen this oracle.
C5_NO_CONTRACT_REASON = ("randomized cartridges for this game must be made by the Manager "
                         "(this run has no randomized-ROM contract)")


def _c5_wrong_rom_reason(player, got, want):
    return f"this is not the ROM built for player {player} (sha1 {got.lower()[:12]}, expected {want.lower()[:12]})"


def c5_refused_oracle(results, *, data_dir, status, events, expect, on_verified=None, on_reason=None):
    """C-5 G-c / G-i: each refused side ran its randomized cart (rand_overlay at its own contract sha1), sent its hello,
    and the SERVER refused it with the exact reason; it received no command, holds no identity and forms no link. An
    admitted side (G-c's A) was admitted. expect: {side: "admitted" | "rejected"}."""
    check_save_witness(results)
    contract_path = Path(data_dir) / "rom_contract.json"
    contract = json.loads(contract_path.read_text(encoding="utf-8")) if contract_path.is_file() else None
    players = (status or {}).get("players") or {}
    links_path = Path(data_dir) / "links.json"   # absent when no side was ever admitted (G-i): nothing persisted
    document = json.loads(links_path.read_text(encoding="utf-8")) if links_path.is_file() else {}
    if not links_path.is_file() and "admitted" in expect.values():
        raise RuntimeError("an admitted side left no links.json")
    observed = {}
    for side, state in expect.items():
        text = (results or {}).get(side) or ""
        client = _last_tagged(text, "CLIENT") or {}
        verdict = _last_tagged(text, "SERVER_VERDICT") or {}
        if client.get("artifact_kind") != "rand_overlay":
            raise RuntimeError(f"{side}: not a randomized cart (CLIENT {client.get('artifact_kind')!r})")
        if verdict.get("expect") != ("refused" if state == "rejected" else "admitted") or verdict.get("hellos", 0) < 1:
            raise RuntimeError(f"{side}: SERVER_VERDICT {verdict} does not match the expected {state}")
        row = players.get(side) or {}
        reason = row.get("admission_reason", "")
        observed[side] = {"admission": row.get("admission"), "reason": reason}
        if on_reason is not None:
            on_reason(f"C5_SERVER_VERDICT {json.dumps({'side': side, **observed[side]}, sort_keys=True)}")
        if row.get("admission") != state:
            raise RuntimeError(f"{side}: server admission {row.get('admission')!r} ({reason!r}), expected {state}")
        if state == "admitted":
            if verdict.get("rx", 0) < 1:
                raise RuntimeError(f"{side}: the admitted side received no command")
            continue
        if contract is None:
            want = C5_NO_CONTRACT_REASON
        else:
            want = _c5_wrong_rom_reason(side, client["rom_sha1"], contract["players"][side]["rom_sha1"])
            if client["rom_sha1"] == contract["players"][side]["rom_sha1"]:
                raise RuntimeError(f"{side}: the refused cart IS its contract cartridge; the control is invalid")
        if reason != want:
            raise RuntimeError(f"{side}: server refusal reason {reason!r} != expected {want!r}")
        if verdict.get("rx") != 0:
            raise RuntimeError(f"{side}: the refused side received {verdict.get('rx')} command(s)")
        if str(side) in ((document.get("player_identity") or {})):
            raise RuntimeError(f"{side}: the refused side's identity was locked")
        if not any(e.get("player") == side and e.get("text") == f"REJECTED — {want}" for e in events or []):
            raise RuntimeError(f"{side}: no 'REJECTED — {want}' event in the journal")
    if document.get("links"):
        raise RuntimeError(f"a refused run formed links: {document['links']}")
    if on_verified is not None:
        on_verified({"a": observed["a"]["admission"], "b": observed["b"]["admission"], "area": "none",
                     "titles": "/".join(str((_last_tagged(results[s], "CLIENT") or {}).get("title")) for s in ("a", "b")),
                     "status": "refused"})


def _client_artifact(inst, client, duo):
    kind = client.get("artifact_kind", "clean")
    if kind == "rand_overlay":
        return _randomized_artifact(inst, client, duo)
    if duo.get("artifact_kind", "clean") != kind:
        raise RuntimeError(f"{inst}: CLIENT/DUO_GEN2 artifact kind differs")
    sha, binding = _executed_identity(client["title"], kind)
    if client["rom_sha1"] != sha:
        raise RuntimeError(f"{inst}: wrong executed artifact ROM pin")
    if kind == "overlay" and (client.get("qualification") != "PHYSICAL_RECEIPTED"
                              or client.get("production_admitted") is not True
                              or client.get("binding_sha256") != binding or duo.get("binding_sha256") != binding):
        raise RuntimeError(f"{inst}: overlay artifact lacks production/binding proof")
    return kind, sha


def _boot_marker(inst, text):
    """SAVE_WITNESS + CLIENT for one instance, plus this instance's title cross-checked across
    its OWN markers (DUO_GEN2 always, RECEIPT when present) -- never a caller-supplied title.
    Returns (witness, client, title)."""
    witness = _last_tagged(text, "SAVE_WITNESS")
    if witness is None:
        raise RuntimeError(f"{inst}: missing SAVE_WITNESS marker")
    _require_fields(witness, SAVE_WITNESS_FIELDS, f"{inst} SAVE_WITNESS")
    client = _last_tagged(text, "CLIENT")
    if not client or client.get("title") not in ("crystal", "gold", "silver"):
        raise RuntimeError(f"{inst}: missing/invalid CLIENT title marker: {client}")
    title = client["title"]
    _require_fields(client, ("title", "rom_sha1"), f"{inst} CLIENT")

    duo = _duo_marker(inst, text)
    if duo["title"] != title or duo["rom_sha1"] != client.get("rom_sha1"):
        raise RuntimeError(f"{inst}: DUO_GEN2 title/rom_sha1 {duo['title']!r}/{duo['rom_sha1']!r} "
                           f"disagrees with CLIENT {title!r}/{client.get('rom_sha1')!r} -- "
                           f"inconsistent title markers")
    kind, _sha = _client_artifact(inst, client, duo)

    receipt = _last_tagged(text, "RECEIPT")
    if receipt is not None and (receipt.get("title") != title
                                or receipt.get("rom_sha1") != client.get("rom_sha1")
                                or receipt.get("artifact_kind", "clean") != client.get("artifact_kind", "clean")):
        raise RuntimeError(f"{inst}: RECEIPT title/rom_sha1 {receipt.get('title')!r}/"
                           f"{receipt.get('rom_sha1')!r} disagrees with CLIENT/DUO_GEN2 "
                           f"{title!r}/{client.get('rom_sha1')!r} -- inconsistent title markers")

    return witness, client, title


def _hello_ot_id(inst, text):
    hello = _last_tagged(text, "HELLO")
    if not hello or not isinstance(hello.get("ot_id"), int):
        raise RuntimeError(f"{inst}: missing/invalid HELLO ot_id marker: {hello}")
    return hello["ot_id"]


def check_save_witness(results):
    """witness_validator: `witness(results)`, exactly one argument (see module docstring).

    For each of "a"/"b": derives this side's own title (cross-checked across its DUO_GEN2/CLIENT/
    RECEIPT markers), re-derives the flushed CartRAM hash from the file on disk (never trusts the
    driver's own `flushed_matches` claim alone), requires the save/gate counters agree, and
    requires gen2_codec's independent checksum/primary-backup/marker witness to pass. Raises
    RuntimeError naming the instance and field on any refusal; returns None.
    """
    _check_save_witness(results, ("a", "b"))


def normalized_gameplay_cartram(raw, layout):
    """Comparison view only: callers must authenticate a complete raw baseline first.

    Exclude the audited native gfx scratch on C/G/S and G/S's SRAM window stack.
    Mail, backup regions, boxes, sStackTop, RTC halt sentinel and gaps stay protected.
    Pinned ram/sram.asm: C1-3/G1-11 ($60 tiles); G79-84 (window top byte included).
    """
    if not isinstance(raw, bytes) or len(raw) not in (CARTRAM_BYTES, SAVERAM_BYTES):
        raise RuntimeError("gameplay CartRAM comparison requires 32768 or 32790 raw bytes")

    def flat(symbol):
        bank, addr = layout.sram_banks[symbol], layout.addresses[symbol]
        if type(bank) is not int or not 0 <= bank < 4 or type(addr) is not int or not 0xA000 <= addr < 0xC000:
            raise RuntimeError(f"invalid native scratch symbol geometry: {symbol}")
        return bank * 0x2000 + addr - 0xA000

    try:
        if layout.title not in ("crystal", "gold", "silver"):
            raise RuntimeError("unknown native scratch title")
        scratch = flat("sScratch"), flat("sPartyMail")
        if scratch != (0, 0x600):
            raise RuntimeError("native sScratch geometry differs from audited source")
        spans = [scratch]
        if layout.title in ("gold", "silver"):
            window = flat("sWindowStackBottom"), flat("sWindowStackTop") + 1
            if window != (0x1800, 0x2000):
                raise RuntimeError("native SRAM window-stack geometry differs from audited source")
            spans.append(window)
    except KeyError as exc:
        raise RuntimeError(f"missing pinned native scratch symbol: {exc}") from exc
    view = bytearray(raw[:CARTRAM_BYTES])
    for start, end in spans:
        view[start:end] = bytes(end - start)
    return bytes(view)


def witness_save_bytes(witness, layout):
    """Read a save, accepting native scratch drift only against a fully authenticated image."""
    try:
        path = Path(witness["saveram_path"])
        if not path.is_file():
            raise RuntimeError(f"witness saveram_path does not exist: {path}")
        current = path.read_bytes()
        if witness.get("cartram_bytes") != CARTRAM_BYTES or witness.get("saveram_bytes") != SAVERAM_BYTES or len(current) != SAVERAM_BYTES:
            raise RuntimeError("witness save byte length differs -- torn save")
        sha = witness.get("cartram_sha256")
        if not isinstance(sha, str) or re.fullmatch(r"[0-9a-fA-F]{64}", sha) is None:
            raise RuntimeError("witness CartRAM digest malformed")

        def matches(raw):
            return hashlib.sha256(raw[:CARTRAM_BYTES]).hexdigest() == sha.lower()

        if "snapshot_path" in witness:
            snapshot = witness["snapshot_path"]
            if not isinstance(snapshot, str) or not snapshot:
                raise RuntimeError("explicit witness snapshot path malformed")
            baseline = Path(snapshot).read_bytes()
            if len(baseline) not in (CARTRAM_BYTES, SAVERAM_BYTES) or not matches(baseline):
                raise RuntimeError("explicit witness snapshot length/digest differs")
        elif matches(current):
            return current
        else:
            backup = Path(str(path) + ".bak")
            if not backup.is_file():
                raise RuntimeError("current CartRAM digest does not match witness and no authenticated backup exists")
            baseline = backup.read_bytes()
            if len(baseline) != SAVERAM_BYTES or not matches(baseline):
                raise RuntimeError("no authenticated witness baseline: backup length/digest differs")
        if normalized_gameplay_cartram(current, layout) != normalized_gameplay_cartram(baseline, layout):
            raise RuntimeError("witness gameplay bytes changed outside audited native scratch")
        return current
    except (KeyError, OSError, TypeError, ValueError) as exc:
        raise RuntimeError(f"witness baseline missing or malformed: {exc}") from exc


def _check_save_witness(results, instances):
    from server.adapters import gen2_codec as codec

    for inst in instances:
        text = (results or {}).get(inst) or ""
        witness, _client, title = _boot_marker(inst, text)

        if witness["cartram_bytes"] != CARTRAM_BYTES:
            raise RuntimeError(f"{inst}: SAVE_WITNESS cartram_bytes={witness['cartram_bytes']}, "
                               f"expected {CARTRAM_BYTES}")
        if witness["saveram_bytes"] != SAVERAM_BYTES:
            raise RuntimeError(f"{inst}: SAVE_WITNESS saveram_bytes={witness['saveram_bytes']}, "
                               f"expected {SAVERAM_BYTES} (CartRAM + 22-byte RTC trailer)")
        gate_saves, client_saves = witness.get("gate_saves"), witness.get("client_saves")
        if type(gate_saves) is not int or type(client_saves) is not int or gate_saves < 1 or gate_saves != client_saves:
            raise RuntimeError(f"{inst}: gate_saves={gate_saves} != client_saves={client_saves} "
                               f"(a save the client attempted was refused, or none landed) "
                               f"-- torn witness")
        if witness["flushed_matches"] is not True:
            raise RuntimeError(f"{inst}: SAVE_WITNESS flushed_matches={witness['flushed_matches']!r}"
                               f" -- the driver itself reports its dump disagreed with the file")
        sha = witness["cartram_sha256"]
        if not (isinstance(sha, str) and re.fullmatch(r"[0-9a-fA-F]{64}", sha)):
            raise RuntimeError(f"{inst}: SAVE_WITNESS cartram_sha256 is not a 64-hex sha256: {sha!r}")

        layout = codec.for_foundation(title)
        cartram = witness_save_bytes(witness, layout)[:CARTRAM_BYTES]
        report = codec.strict_checksum_witness(cartram, layout)
        if not report["valid"]:
            raise RuntimeError(f"{inst}: independent checksum/primary-backup/marker witness "
                               f"refused: {report}")


def _ball_pocket(raw, layout):
    from tools.gen2_fixtures import _saved_field

    count = _saved_field(raw, layout, "wNumBalls", 1)[0]
    pocket = _saved_field(raw, layout, "wBalls", count * 2 + 1)
    return [(pocket[index * 2], pocket[index * 2 + 1]) for index in range(count)]


def _pair_oracle(results, *, data_dir, area_id="route_29", ot_ids=None, boot_saveram=None,
                 status="alive", snapshots=None, pocket_check=None):
    """post-result oracle: `oracle(results, **oracle_kwargs)` (see module docstring).

    Compares the two flushed saves (PYDEC via gen2_codec, each decoded with ITS OWN side's
    cross-checked title/layout) against the boot fixtures and the server's persisted
    `links.json` under `data_dir`. Refuses: a title/rom_sha1 marker mismatch, a checksum-invalid
    flushed or boot save, altered links.json, wrong area, an unchanged Ball pocket, identical
    full keys, a missing/torn witness, and a party mon without a matching server capture. Raises
    RuntimeError naming the instance/field on any refusal; returns None on success.

    `ot_ids`/`boot_saveram` are optional per-instance override dicts (unit-test escape hatch,
    see module docstring); a side missing from either mapping derives its own expected OT id
    from its HELLO marker and its own boot fixture from its DUO_GEN2 `case`.
    """
    from server.adapters import gen2_codec as codec

    ot_ids = dict(ot_ids) if ot_ids else {}
    boot_saveram = dict(boot_saveram) if boot_saveram else {}

    decoded = {}
    for inst in ("a", "b"):
        text = (results or {}).get(inst) or ""
        witness, _client, title = _boot_marker(inst, text)
        duo = _duo_marker(inst, text)
        capture = _last_tagged(text, "ENGINE_CAPTURE")
        if capture is None:
            raise RuntimeError(f"{inst}: missing ENGINE_CAPTURE marker")
        if capture.get("site_id") != "capture_party_finalized":
            raise RuntimeError(f"{inst}: ENGINE_CAPTURE site_id={capture.get('site_id')!r}, "
                               f"expected 'capture_party_finalized'")
        if capture.get("acquisition") != "wild" or capture.get("destination") != "party":
            raise RuntimeError(f"{inst}: ENGINE_CAPTURE acquisition/destination = "
                               f"{capture.get('acquisition')!r}/{capture.get('destination')!r}, "
                               f"expected 'wild'/'party'")
        if capture.get("area_id") != area_id:
            raise RuntimeError(f"{inst}: ENGINE_CAPTURE area_id={capture.get('area_id')!r}, "
                               f"expected {area_id!r}")

        layout = codec.for_foundation(title)
        path = snapshots[inst] if snapshots else witness["saveram_path"]
        flushed = Path(path).read_bytes()[:CARTRAM_BYTES]
        flushed_report = codec.strict_checksum_witness(flushed, layout)
        if not flushed_report["valid"]:
            raise RuntimeError(f"{inst}: flushed save fails the independent checksum/primary-"
                               f"backup/marker witness: {flushed_report}")

        boot_path = Path(boot_saveram[inst]) if inst in boot_saveram else _fixture_path(duo["case"])
        if not boot_path.is_absolute():
            boot_path = REPO_ROOT / boot_path
        boot_bytes = boot_path.read_bytes()
        boot = boot_bytes[:CARTRAM_BYTES]
        boot_report = codec.strict_checksum_witness(boot, layout)
        if not boot_report["valid"]:
            raise RuntimeError(f"{inst}: boot fixture {boot_path} fails the independent checksum/"
                               f"primary-backup/marker witness: {boot_report}")
        if hashlib.sha256(boot_bytes).hexdigest().lower() != duo["fixture_sha256"].lower():
            raise RuntimeError(f"{inst}: DUO_GEN2 fixture_sha256 differs from the selected boot fixture hash")

        boot_party = codec.decode_saved_party(boot, layout, copy_name="primary")["mons"]
        new_party = codec.decode_saved_party(flushed, layout, copy_name="primary")["mons"]
        boot_keys = [codec.key(mon) for mon in boot_party]
        new_keys = [codec.key(mon) for mon in new_party]
        if len(new_keys) != len(boot_keys) + 1:
            raise RuntimeError(f"{inst}: party has {len(new_keys)} mon(s), expected exactly one "
                               f"more than the boot fixture's {len(boot_keys)}: {new_keys}")
        if new_keys[:len(boot_keys)] != boot_keys:
            raise RuntimeError(f"{inst}: existing party mons changed identity: "
                               f"{new_keys[:len(boot_keys)]} != {boot_keys}")
        new_mon = new_party[-1]
        new_key = new_keys[-1]
        expected_ot = ot_ids[inst] if inst in ot_ids else _hello_ot_id(inst, text)
        if new_mon["ot_id"] != expected_ot:
            raise RuntimeError(f"{inst}: captured mon OT id {new_mon['ot_id']} != expected "
                               f"{expected_ot}")
        if capture.get("key") != new_key:
            raise RuntimeError(f"{inst}: ENGINE_CAPTURE key {capture.get('key')!r} disagrees "
                               f"with the flushed save's new party key {new_key!r}")
        if capture.get("species_id") != new_mon["species_id"] or capture.get("level") != new_mon["level"]:
            raise RuntimeError(f"{inst}: ENGINE_CAPTURE species/level differs from decoded capture")
        caught_lines = [line for line in text.splitlines() if line.startswith("CAUGHT ")]
        if not caught_lines or caught_lines[-1][len("CAUGHT "):] != new_key:
            raise RuntimeError(f"{inst}: CAUGHT line {caught_lines[-1:] or None} does not name "
                               f"the flushed save's new party key {new_key!r}")

        before_pocket, after_pocket = _ball_pocket(boot, layout), _ball_pocket(flushed, layout)
        before_total = sum(qty for _item, qty in before_pocket)
        after_total = sum(qty for _item, qty in after_pocket)
        if pocket_check is not None:   # gen2_ball_gate: a zero-Ball boot, so "decreased" cannot hold
            pocket_check(inst, before_pocket, after_pocket)
        elif after_total >= before_total:
            raise RuntimeError(f"{inst}: Ball pocket did not decrease: {before_pocket} -> "
                               f"{after_pocket} -- unchanged Ball pocket")

        decoded[inst] = {"key": new_key, "species": new_mon["species_id"], "level": new_mon["level"], "title": title}

    if decoded["a"]["key"] == decoded["b"]["key"]:
        raise RuntimeError(f"both sides captured the identical full key {decoded['a']['key']!r}")

    links_path = Path(data_dir) / "links.json"
    if not links_path.is_file():
        raise RuntimeError(f"no links.json under data_dir: {links_path}")
    raw_links_text = links_path.read_text(encoding="utf-8")
    document = json.loads(raw_links_text)
    kinds = {(_last_tagged(results[side], "CLIENT") or {}).get("artifact_kind", "clean") for side in ("a", "b")}
    if len(kinds) != 1 or next(iter(kinds)) != (document.get("artifact_kind") or "clean"):
        raise RuntimeError("duo receipts belong to another server artifact kind")
    rows = document.get("links") or []
    statuses = {status} if isinstance(status, str) else set(status)
    matches = [row for row in rows if row.get("area_id") == area_id and row.get("status") in statuses]
    if len(matches) != 1:
        raise RuntimeError(f"expected exactly one {status} {area_id!r} link in links.json, found "
                           f"{len(matches)}: {matches}")
    row = matches[0]
    for inst in ("a", "b"):
        mon = row.get(inst) or {}
        want = decoded[inst]
        if mon.get("key") != want["key"]:
            raise RuntimeError(f"links.json {inst}.key={mon.get('key')!r} != the flushed save's "
                               f"{want['key']!r} -- altered links.json")
        # Death-time level may differ after A's sacrifice battles. The link proof
        # compares capture-time levels; the faint proof binds final identity/HP instead.
        if mon.get("species") != want["species"] or status == "alive" and mon.get("level") != want["level"]:
            raise RuntimeError(f"links.json {inst} species/level {mon.get('species')}/"
                               f"{mon.get('level')} disagrees with the engine capture "
                               f"{want['species']}/{want['level']} -- altered links.json")

    # Stats and delivery ledgers reference existing mons, not additional captures.
    bookkeeping = {"mon_stats", "gameplay_delivery"} | ({"pending_memorials"} if status != "alive" else set())
    accepted_text = json.dumps({k: v for k, v in document.items() if k not in bookkeeping})
    for inst in ("a", "b"):
        occurrences = accepted_text.count(decoded[inst]["key"])
        if occurrences != 1:
            raise RuntimeError(f"{inst}: key {decoded[inst]['key']} appears {occurrences} times "
                               f"in links.json, expected exactly once (the formed link) -- the "
                               f"server accepted something the saves don't show")
    return decoded, row, document


def _verified_facts(decoded, area_id, status):
    return {"a": decoded["a"]["key"], "b": decoded["b"]["key"], "area": area_id,
            "titles": "/".join(decoded[inst]["title"] for inst in ("a", "b")), "status": status}


def link_oracle(results, *, data_dir, area_id="route_29", ot_ids=None, boot_saveram=None, on_verified=None):
    """Require the independently decoded captures to form one alive server pair."""
    decoded, _, _ = _pair_oracle(results, data_dir=data_dir, area_id=area_id, ot_ids=ot_ids, boot_saveram=boot_saveram)
    if on_verified is not None:
        on_verified(_verified_facts(decoded, area_id, "alive"))


def _faint_need(condition, reason):
    if not condition:
        raise RuntimeError(f"faint: {reason}")


def _one_marker(text, tag):
    lines = [line for line in text.splitlines() if line.startswith(tag + " ")]
    _faint_need(len(lines) == 1, f"exactly one {tag} marker required")
    value = _last_tagged(text, tag)
    _faint_need(isinstance(value, dict), f"{tag} must be an object")
    return value


def _frame(row, field="frame"):
    value = row.get(field)
    _faint_need(type(value) is int and value >= 0, f"invalid {field}")
    return value


def _hex_bytes(value, size, label):
    _faint_need(isinstance(value, str) and re.fullmatch(r"[0-9a-fA-F]*", value) is not None
                and len(value) == size * 2, f"invalid {label} bytes")
    return bytes.fromhex(value)


def _faint_checkpoint(write, layout, artifact_kind="clean"):
    """Check measured PC/bank, both anchors in both domains, caller and state predicates."""
    primary = _checkpoint_for(layout.title, artifact_kind)["primary"]
    point = write["checkpoint"]
    _faint_need(isinstance(point, dict), "checkpoint observation missing")
    for field, expected in (("pc", primary["execution_before"]["pc"]),
                            ("hrom_bank", primary["execution_before"]["bank"])):
        _faint_need(type(point.get(field)) is int and point[field] == expected, f"checkpoint {field} differs")
    anchor = primary["anchors"]["ow_player_input"]["expected_hex"]
    _faint_need(_hex_bytes(point.get("anchor_hex"), len(anchor) // 2, "anchor") == bytes.fromhex(anchor),
                "mapped checkpoint anchor differs")
    anchors = point.get("anchors")
    _faint_need(isinstance(anchors, dict) and set(anchors) == set(primary["anchors"]),
                "checkpoint anchors missing or unrecognized")
    for name, source in primary["anchors"].items():
        observed = anchors[name]
        _faint_need(isinstance(observed, dict), f"checkpoint anchor {name} missing")
        expected = bytes.fromhex(source["expected_hex"])
        for domain in ("rom_hex", "mapped_hex"):
            _faint_need(_hex_bytes(observed.get(domain), len(expected), f"anchor {name}/{domain}") == expected,
                        f"checkpoint anchor {name}/{domain} differs")
    _faint_owner(point, primary)
    stack = primary["caller_stack"]
    sp = point.get("sp")
    _faint_need(type(sp) is int and stack["minimum_sp"] <= sp
                and sp + stack["required_read_bytes"] <= stack["exclusive_stack_end"], "checkpoint SP outside stack")
    raw = _hex_bytes(point.get("stack_hex"), stack["required_read_bytes"], "caller stack")
    for word in stack["required_words"]:
        at = word["offset_from_sp"]
        _faint_need(int.from_bytes(raw[at:at + 2], word["endianness"]) == word["value"], "checkpoint caller differs")
    for predicate in primary["state_predicates"]:
        _faint_need(_faint_predicate(point, predicate), f"checkpoint predicate {predicate['symbol']} refused")


def _faint_owner(point, hold):
    """The hold's WRAM bank and no serial transfer (gen2_write_safety.lua evaluate/evaluate_frame)."""
    owner = hold["ownership_requirements"]
    svbk, sc = point.get("svbk"), point.get("sc")
    _faint_need(type(svbk) is int and 0 <= svbk <= 7
                and (svbk or 1) == owner["effective_wram_bank"], "checkpoint WRAM bank differs")
    serial = owner["serial_control"]
    _faint_need(type(sc) is int and 0 <= sc <= 255 and sc & serial["mask"] == serial["value"],
                "checkpoint serial owner active")


def _faint_predicate(point, predicate):
    state = point.get("state")
    _faint_need(isinstance(state, dict), "checkpoint state missing")
    value = state.get(predicate["symbol"])
    return (predicate["operator"] == "masked_equal" and type(value) is int
            and 0 <= value < 1 << (8 * predicate["width"]) and value & predicate["mask"] == predicate["value"])


# O-23: Silver's U2 proof is Gold's write-window receipt (lua/gen2_write_safety.lua M.RECEIPT_TITLE).
RECEIPT_TITLE = {"crystal": "crystal", "gold": "gold", "silver": "gold"}


def _faint_battle_bench(write, layout, captured, artifact_kind="clean"):
    """O-32 (0752a3ab): a bench death on receipt at a battle frame end. The frame evidence follows
    gen2_write_safety.lua evaluate_frame (the battle hold's WRAM bank, serial and wLinkMode predicates)
    with wBattleMode WILD or TRAINER (reads.lua read_battle's enumeration). PC == the frame end the U2
    receipt's battle_bench run measured (0x0040) is a documented HARNESS invariant, stricter than
    evaluate_frame (which checks no PC): it can only false-negative. The receipt is cross-referenced
    (battle_bench + battle_faint PASS/PHYSICAL, this pack's battle hold rows); its full qualification is
    the release verifier's write-window lane. Not the active battler: the harness's write-time battle
    snapshot (row.battle, read_battle), and `captured` binds the target to the mon this battle caught."""
    title = layout.title
    data = _checkpoint_for(title, artifact_kind)
    owner = title if artifact_kind == "overlay" else RECEIPT_TITLE[title]
    namespace = "overlay/" if artifact_kind == "overlay" else ""
    receipt = json.loads((REPO_ROOT / f"data/games/gen2_{title}/receipts/{namespace}{owner}.write_window.json").read_text())
    if artifact_kind == "overlay":
        sha, binding = _executed_identity(title, artifact_kind)
        _faint_need(receipt.get("rom_sha1") == sha and receipt.get("artifact_kind") == artifact_kind
                    and receipt.get("binding_sha256") == binding, "U2 receipt belongs to another artifact")
    runs = receipt.get("runs") or {}
    _faint_need(receipt.get("title") == owner and receipt.get("battle_hold") == data["battle_hold"]
                and all(isinstance(runs.get(mode), dict) and runs[mode].get("result") == "PASS"
                        and runs[mode].get("evidence_level") == "PHYSICAL" for mode in ("battle_faint", "battle_bench")),
                "no U2 battle_bench receipt for this battle hold")
    bench = runs["battle_bench"].get("bench_write") or {}
    point = write["checkpoint"]
    _faint_need(isinstance(point, dict), "checkpoint observation missing")
    _faint_need(bench.get("ok") is True and bench.get("where") == "frame_end" and type(bench.get("pc")) is int
                and type(point.get("pc")) is int and point["pc"] == bench["pc"], "battle_bench frame-end pc differs")
    _faint_owner(point, data["battle_hold"])
    for predicate in data["battle_hold"]["state_predicates"]:
        _faint_need(_faint_predicate(point, predicate), f"battle_bench predicate {predicate['symbol']} refused")
    wild_or_trainer = (layout.constants["WILD_BATTLE"], layout.constants["TRAINER_BATTLE"])
    _faint_need(point["state"].get("wBattleMode") in wild_or_trainer, "battle_bench outside a wild/trainer battle")
    battle = write.get("battle")
    _faint_need(isinstance(battle, dict) and not battle.get("error") and battle.get("mode") in wild_or_trainer
                and type(battle.get("active_slot")) is int and 0 <= battle["active_slot"] < layout.constants["PARTY_LENGTH"]
                and battle["active_slot"] != write.get("slot"), "battle_bench write-time snapshot missing or names the active battler")
    _faint_need(isinstance(captured, dict) and captured.get("key") == write.get("key")
                and captured.get("destination") == "party" and captured.get("slot") == write.get("slot")
                and _frame(captured) <= _frame(write), "battle_bench target not proven off the active battler")


def _faint_write(write, layout, linked, final, key, captured=None, artifact_kind="clean"):
    """B's (or a clause rejection's) party HP/status zero: at the overworld checkpoint (why=overworld), or,
    O-32, on receipt at a battle frame end (every permit why=battle_bench; `captured` proves the slot is
    not the active battler -- callers without that evidence refuse a bench write)."""
    from server.adapters import gen2_codec as codec

    slot = write.get("slot")
    _faint_need(type(slot) is int and 0 <= slot < len(linked), "B write slot invalid")
    _faint_need(write.get("ok") is True and not write.get("error") and write.get("kind") == "party_hp"
                and write.get("key") == key and codec.key(linked[slot]) == key, "B write ownership/result differs")
    log = write.get("log")
    _faint_need(isinstance(log, list) and len(log) == 2, "B write must carry exactly two permit spans")
    why = "battle_bench" if all(isinstance(row, dict) and row.get("why") == "battle_bench" for row in log) else "overworld"
    if why == "battle_bench":
        _faint_battle_bench(write, layout, captured, artifact_kind)
    else:
        _faint_checkpoint(write, layout, artifact_kind)
    n = layout.party_size * layout.constants["PARTY_LENGTH"]
    before = _hex_bytes(write.get("before_party_hex"), n, "before party")
    after = _hex_bytes(write.get("after_party_hex"), n, "after party")
    for index, mon in enumerate(linked):
        start = index * layout.party_size
        _faint_need(before[start:start + layout.party_size].hex() == mon["raw_hex"], "B write preimage differs from LINK_SAVE")
        _faint_need(after[start:start + layout.party_size].hex() == final[index]["raw_hex"], "B saved party differs from write readback")
    expected = bytearray(before)
    start = slot * layout.party_size
    status, hp = layout.constants["MON_STATUS"], layout.constants["MON_HP"]
    expected[start + status] = 0
    expected[start + hp:start + hp + 2] = bytes(2)
    _faint_need(after == bytes(expected), "B write changed bytes outside target HP/status")
    profile = layout.profile["titles"][layout.title]
    for index, (offset, length) in enumerate(((status, 1), (hp, 2)), 1):
        row = log[index - 1]
        expected = {"domain": "System Bus", "addr": layout.addresses["wPartyMon1"] + start + offset,
                    "n": length, "why": why, "status": "written", "completed": length,
                    "attempted": length, "batch_index": index, "batch_size": 2,
                    "site": "lua/gen2/entry.lua production", "evidence": "U2 PHYSICAL receipt",
                    "title": layout.title, "artifact": profile["artifact"], "rom_sha1": profile["rom_sha1"]}
        _faint_need(isinstance(row, dict) and all(type(row.get(k)) is type(v) and row[k] == v
                    for k, v in expected.items()) and not row.get("error"), "B permit span/provenance differs")


def _memorial_observation(text, key, layout, witness):
    """The synchronous production observation, before successful native deposition."""
    from server.adapters import gen2_codec as codec

    preimage, ack = _one_marker(text, "MEMORIAL_PREIMAGE"), _one_marker(text, "MEMORIAL_ACK")
    raw = _hex_bytes(preimage.get("raw_hex"), layout.party_size, "memorial preimage")
    ot = _hex_bytes(preimage.get("ot_raw_hex"), layout.name_size, "memorial OT")
    nickname = _hex_bytes(preimage.get("nickname_raw_hex"), layout.nickname_size, "memorial nickname")
    mon = codec.decode_party_mon(raw, layout, species_marker=preimage.get("species_marker"), ot=ot, nickname=nickname)
    _faint_need(preimage.get("key") == codec.key(mon) == key and mon["hp"] == 0,
                "memorial preimage does not name the actual HP-zero linked mon")
    box_number = layout.constants["NUM_BOXES"] - 1
    _faint_need(ack.get("event") == "memorialize_done" and ack.get("key") == key and ack.get("box") == box_number,
                "memorial needs successful final-box acknowledgement")
    pre_at, ack_at = text.index("MEMORIAL_PREIMAGE "), text.index("MEMORIAL_ACK ")
    _faint_need(pre_at < ack_at < text.index("SAVE_WITNESS ")
                and _frame(preimage) <= _frame(ack) < _frame(witness, "save_completed_frame"),
                "memorial preimage/ack/save chronology differs")
    pre_line = next(at for at, line in enumerate(text.splitlines()) if line.startswith("MEMORIAL_PREIMAGE "))
    for line_at, write in _tag_rows(text, "PARTY_HP_WRITE"):
        _faint_need(line_at < pre_line, "HP write occurred after memorial preimage")
        _faint_need(_frame(write) <= _frame(preimage), "HP write frame after memorial preimage")
    slot = preimage.get("slot")
    _faint_need(type(slot) is int and 0 <= slot < layout.constants["PARTY_LENGTH"], "memorial preimage party slot invalid")
    return mon, preimage


def _memorial_party(text, key, layout, linked_path, witness):
    """Bind a real pre-deposit party observation to one independently decoded final Box 14 mon."""
    from server.adapters import gen2_codec as codec

    mon, preimage = _memorial_observation(text, key, layout, witness)
    box_number = layout.constants["NUM_BOXES"] - 1
    saved = Path(witness["saveram_path"]).read_bytes()
    final, inventory = _clause_inventory(saved, layout)
    _, original = _clause_inventory(Path(linked_path).read_bytes(), layout)
    _faint_need(set(inventory) == set(original), "memorial saved inventory changed identity")
    _faint_need(key in inventory and inventory[key][0] == box_number
                and all(codec.key(row) != key for row in final), "linked mon not uniquely absent from party and present in Box 14")
    boxed = inventory[key][1]
    _faint_need(boxed["raw_hex"] == _deposited_record(mon["raw_hex"], layout).hex()
                and boxed["species_marker"] == mon["species_marker"]
                and boxed["ot_raw_hex"] == mon["ot_raw_hex"] and boxed["nickname_raw_hex"] == mon["nickname_raw_hex"],
                "saved memorial differs from native deposit of actual preimage")
    for other_key, (place, original_mon) in original.items():
        if other_key == key:
            continue
        now_place, now_mon = inventory[other_key]
        _faint_need(place == now_place, "unrelated mon changed storage location during memorial")
        if place != "party":
            _faint_need(all(now_mon[field] == original_mon[field] for field in ("raw_hex", "species_marker", "ot_raw_hex", "nickname_raw_hex")),
                        "unrelated boxed mon changed during memorial")
    slot = preimage.get("slot")
    _faint_need(type(slot) is int and 0 <= slot <= len(final), "memorial preimage party slot invalid")
    # Restore only the independently witnessed record into the final remaining party.
    # This is the pre-deposit view used for HP-write checks, not a claim that Box 14 stores HP.
    party_before_deposit = list(final)
    party_before_deposit.insert(slot, mon)
    return party_before_deposit, preimage


# gen2_faint_active_trainer (O-30 review MINOR-5): the Route 30 youngsters B's walk meets (C maps/Route30.asm:424-426,
# G :339-340: TrainerYoungsterJoey YOUNGSTER JOEY1, TrainerYoungsterMikey YOUNGSTER MIKEY).
ROUTE30_TRAINERS = ("YOUNGSTER", ("JOEY1", "MIKEY"))


def validate_faint_active_markers(results, *, title_b, key_a, key_b, species_b, trainer=False):
    """Source-bound active-hold evidence, shared with the receipt matrix; no save normalization. trainer: B's
    battle is a Route 30 trainer battle (ForcePlayerMonChoice, no NEXT_MON, a live turn against the replacement
    after REPLACED -- an enemy_turn, OR a witnessed enemy_faint: an independent wEnemyMonHP read, trainer-only,
    a positive-HP baseline for the SAME species zeroing out strictly after the replacement, TRAINER-FAINT-LIVE-TURN)."""
    from server.adapters import gen2_codec as codec

    scenario = "gen2_faint_active_trainer" if trainer else "gen2_faint_active"
    schema = "gen2-duo-faint-active-trainer-v1" if trainer else "gen2-duo-faint-active-v1"

    a, b = results["a"], results["b"]
    go, a_link, a_faint = (_one_marker(a, tag) for tag in ("B_ACTIVE", "LINK_SAVE", "ENGINE_FAINT"))
    _faint_need(a.index("LINK_SAVE ") < a.index("B_ACTIVE ") < a.index("ENGINE_FAINT ")
                and _frame(a_link) <= _frame(go) <= _frame(a_faint) and a_faint.get("key") == key_a,
                "A faint did not wait for B_ACTIVE")
    for side in ("a", "b"):
        _reconnect_pass(results[side])
        head, receipt = _one_marker(results[side], "DUO_GEN2"), _one_marker(results[side], "RECEIPT")
        _faint_need(head.get("scenario") == scenario and head.get("player") == side
                    and receipt.get("schema") == schema, "active faint scenario/schema differs")
    layout = codec.for_foundation(title_b)
    client = _one_marker(b, "CLIENT")
    _faint_need(isinstance(client.get("registered_sites"), list) and "battle_faint" in client["registered_sites"],
                "B production signals lack battle_faint")
    pack = json.loads((REPO_ROOT / f"data/games/gen2_{title_b}/write_checkpoint.json").read_text())
    _faint_need(pack["source"] == layout.profile["source"], "battle hold source differs")
    hold = _checkpoint_for(title_b, client.get("artifact_kind", "clean"))["battle_hold"]
    active, write, link = (_one_marker(b, tag) for tag in ("LINKED_ACTIVE", "BATTLE_HOLD_WRITE", "LINK_SAVE"))
    slot = active.get("slot")
    _faint_need(type(slot) is int and 0 <= slot < layout.constants["PARTY_LENGTH"]
                and all(type(active.get(field)) is int for field in ("cur_battle_mon", "battle_mon_species", "battle_mode", "link_mode", "battle_type"))
                and active.get("key") == key_b and active.get("cur_battle_mon") == slot
                and active.get("battle_mon_species") == species_b and active.get("battle_mode") == (2 if trainer else 1)
                and active.get("link_mode") == 0 and _frame(link) <= _frame(active),
                f"linked mon not active in {'trainer' if trainer else 'wild'} battle")
    if trainer:
        pack = json.loads((REPO_ROOT / f"data/games/gen2_{title_b}/trainers.json").read_text(encoding="utf-8"))
        cls, tid = active.get("other_trainer_class"), active.get("other_trainer_id")
        party = pack["parties"].get(str(cls), {}).get(str(tid), {}) if type(cls) is int and type(tid) is int else {}
        _faint_need(pack["class_constants"].get(str(cls)) == ROUTE30_TRAINERS[0]
                    and party.get("constant") in ROUTE30_TRAINERS[1], f"opposing trainer {cls}/{tid} is not a Route 30 youngster")
    lines = b.splitlines()
    position = {tag: next(i for i, line in enumerate(lines) if line.startswith(tag + " "))
                for tag in ("LINK_SAVE", "LINKED_ACTIVE", "BATTLE_HOLD_WRITE", "MEMORIAL_PREIMAGE", "MEMORIAL_ACK", "SAVE_WITNESS")}
    commands = [i for i, line in enumerate(lines) if line == f"RX force_faint key={key_b}"]
    _faint_need(commands and position["LINK_SAVE"] < position["LINKED_ACTIVE"] < commands[0] < position["BATTLE_HOLD_WRITE"],
                "battle write precedes active witness/force_faint")
    _faint_need(write.get("ok") is True and not write.get("error") and write.get("kind") == "battle_faint"
                and all(type(write.get(field)) is int for field in ("slot", "active_slot", "pc", "hrom_bank"))
                and write.get("key") == key_b and write.get("slot") == write.get("active_slot") == slot,
                "active battle write ownership/result differs")
    _faint_need(write.get("pc") == hold["execution_before"]["pc"] and write.get("hrom_bank") == hold["execution_before"]["bank"],
                "active battle write PC/bank differs")
    _faint_need(_hex_bytes(write.get("battle_hp_before_hex"), 2, "active HP before") != bytes(2), "active battler was already fainted")
    _hex_bytes(write.get("hp_before_hex"), 2, "party HP before")
    _hex_bytes(write.get("action_before_hex"), 1, "action before")
    for field, expected in (("battle_hp_after_hex", bytes(2)), ("hp_after_hex", bytes(2)),
                            ("status_after_hex", bytes(1)), ("action_after_hex", bytes([hold["write"]["skip_action"]]))):
        _faint_need(_hex_bytes(write.get(field), len(expected), field) == expected, "active write readback differs: " + field)
    log = write.get("log")
    _faint_need(isinstance(log, list) and len(log) == 4, "active battle write requires four permit spans")
    targets = hold["write"]["targets"]
    base = layout.addresses["wPartyMon1"] + slot * layout.party_size
    spans = ((targets["wBattleMonHP"]["address"], 2), (base + layout.constants["MON_STATUS"], 1),
             (base + layout.constants["MON_HP"], 2), (targets["wBattlePlayerAction"]["address"], 1))
    profile = layout.profile["titles"][title_b]
    for index, ((address, length), row) in enumerate(zip(spans, log, strict=True), 1):
        expected = {"domain": "System Bus", "addr": address, "n": length, "why": "battle_hold",
                    "status": "written", "completed": length, "attempted": length, "batch_index": index, "batch_size": 4,
                    "site": "lua/gen2/entry.lua production", "evidence": "U2 PHYSICAL receipt", "title": title_b,
                    "artifact": profile["artifact"], "rom_sha1": profile["rom_sha1"]}
        _faint_need(isinstance(row, dict) and all(type(row.get(k)) is type(v) and row[k] == v for k, v in expected.items())
                    and not row.get("error"), "active battle permit span/provenance differs")
    seq = write.get("seq")
    _faint_need(type(seq) is int and seq > 0 and _frame(active) <= _frame(write), "active write sequence/frame invalid")
    replaced = _one_marker(b, "REPLACED")
    replacement_pos = next(i for i, line in enumerate(lines) if line.startswith("REPLACED "))
    traces = _tag_rows(b, "BATTLE_TRACE")
    previous = 0
    after = []
    for pos, trace in traces:
        _faint_need(type(trace.get("seq")) is int and trace["seq"] > previous and trace["seq"] != seq
                    and trace.get("what") in ("faint", "enemy_turn", "enemy_faint") and _frame(trace) >= _frame(active),
                    "lost/unknown/unordered battle trace")
        if trace.get("what") == "enemy_faint":
            # TRAINER-FAINT-LIVE-TURN (post-RC, OMP cx-4ece9985): an independent wEnemyMonHP witness. Valid
            # only in a trainer battle, strictly after the replacement (by FRAME, not line position), with a
            # positive-HP baseline for the SAME species zeroing out -- not a switch, not a stale pre-battle read.
            _faint_need(trainer, "enemy_faint row in a wild scenario")
            _faint_need(trace.get("battle_mode") == 2, "enemy_faint row outside a trainer battle")
            _faint_need(type(trace.get("species")) is int and 1 <= trace["species"] <= 251,
                        "enemy_faint row names no valid species")
            _faint_need(type(trace.get("hp_before")) is int and trace["hp_before"] > 0,
                        "enemy_faint row has no positive HP baseline")
            _faint_need(trace.get("hp_after") == 0, "enemy_faint row did not zero the foe")
            _faint_need(trace.get("title") == title_b, "enemy_faint row names another title")
            _faint_need(_frame(trace) > _frame(replaced), "enemy_faint row is not strictly after the replacement")
        previous = trace["seq"]
        if trace["seq"] > seq:
            _faint_need(pos > position["BATTLE_HOLD_WRITE"], "post-write trace printed before write")
            after.append((pos, trace))
    _faint_need(after and after[0][1]["what"] == "faint" and _frame(after[0][1]) == _frame(write), "first engine trace is not same-frame faint")
    _faint_need(not _tag_rows(b, "FAINT_SENT"), "B echoed the commanded faint")
    for pos, row in _tag_rows(b, "ENGINE_FAINT"):
        _faint_need(row.get("key") == key_b and pos > position["BATTLE_HOLD_WRITE"] and _frame(row) >= _frame(write), "B engine faint differs from command")
    if trainer:   # ForcePlayerMonChoice asks nothing; the replacement then takes a live turn: an enemy_turn, or
        # a witnessed enemy faint (a crit-KO can zero the foe before it ever moves, TRAINER-FAINT-LIVE-TURN).
        # Chronology is by FRAME, not line position: every enemy_faint field was already schema-checked above.
        _faint_need(not _tag_rows(b, "NEXT_MON"), "NEXT_MON in a trainer battle")
        next_mon, next_pos = after[0][1], after[0][0]
        _faint_need(any((trace["what"] == "enemy_turn" or trace["what"] == "enemy_faint")
                        and _frame(trace) > _frame(replaced) for _pos, trace in traces),
                    "no live enemy turn or witnessed enemy faint against the replacement")
    else:
        next_mon = _one_marker(b, "NEXT_MON")
        next_pos = next(i for i, line in enumerate(lines) if line.startswith("NEXT_MON "))
    readbacks = [(i, line) for i, line in enumerate(lines) if line.startswith("LINKED_HP_STATUS ")]
    _faint_need(after[0][0] <= next_pos < replacement_pos and _frame(after[0][1]) <= _frame(next_mon) <= _frame(replaced)
                and type(replaced.get("active_slot")) is int and 0 <= replaced["active_slot"] < 6 and replaced["active_slot"] != slot
                and type(replaced.get("hp")) is int and replaced["hp"] > 0, "no living replacement after active faint")
    _faint_need(len(readbacks) == 1 and readbacks[0][1] == "LINKED_HP_STATUS 0000 00" and readbacks[0][0] > replacement_pos,
                "active linked readback differs or precedes replacement")
    pre, save = _one_marker(b, "MEMORIAL_PREIMAGE"), _one_marker(b, "SAVE_WITNESS")
    _faint_need(position["BATTLE_HOLD_WRITE"] < position["MEMORIAL_PREIMAGE"] < position["MEMORIAL_ACK"] < position["SAVE_WITNESS"]
                and _frame(pre) >= _frame(write) and _frame(save, "save_completed_frame") > _frame(write), "active memorial/save chronology differs")
    for pos, repeat in _tag_rows(b, "PARTY_HP_WRITE"):
        _faint_need(position["BATTLE_HOLD_WRITE"] < pos < position["MEMORIAL_PREIMAGE"] and repeat.get("ok") is True
                    and repeat.get("kind") == "party_hp" and repeat.get("key") == key_b
                    and _hex_bytes(repeat.get("before_party_hex"), 288, "repeat before") == _hex_bytes(repeat.get("after_party_hex"), 288, "repeat after"),
                    "active checkpoint repeat is not idempotent")
    return {"active": active, "write": write, "next_mon": next_mon, "replaced": replaced}


def _faint_oracle(results, *, data_dir, area_id, ot_ids, boot_saveram, active=False, engine=("battle_faint", "battle"),
                  trainer=False):
    site, cause = engine
    from server.adapters import gen2_codec as codec

    check_save_witness(results)
    stages, snapshots, parties = {}, {}, {}
    for inst in ("a", "b"):
        text = results[inst]
        witness, client, title = _boot_marker(inst, text)
        _faint_need(client.get("production_admitted") is True, f"{inst}: not the production client")
        _faint_need(type(witness.get("gate_saves")) is int and type(witness.get("client_saves")) is int,
                    f"{inst}: save counters must be integers")
        if inst == "a":
            _faint_need(isinstance(client.get("registered_sites"), list)
                        and site in client["registered_sites"], f"A production signals lack {site}")
        layout = codec.for_foundation(title)
        _faint_need(client.get("rom_sha1") == _executed_identity(title, client.get("artifact_kind", "clean"))[0], f"{inst}: wrong ROM pin")
        stage = _one_marker(text, "LINK_SAVE")
        _faint_need(type(stage.get("gate_saves")) is int and type(stage.get("client_saves")) is int
                    and stage["gate_saves"] == stage["client_saves"] >= 1
                    and witness["gate_saves"] > stage["gate_saves"]
                    and witness["client_saves"] > stage["client_saves"], f"{inst}: final save counters are not newer than LINK_SAVE")
        _faint_need(_frame(stage, "save_completed_frame") <= _frame(stage)
                    and _frame(stage, "save_completed_frame") < _frame(witness, "save_completed_frame"),
                    f"{inst}: final save completion is not newer than LINK_SAVE")
        raw = Path(stage["saveram_path"]).read_bytes()
        _faint_need(stage.get("saveram_bytes") == len(raw) == SAVERAM_BYTES
                    and stage.get("cartram_bytes") == CARTRAM_BYTES
                    and hashlib.sha256(raw[:CARTRAM_BYTES]).hexdigest() == stage.get("cartram_sha256"),
                    f"{inst}: LINK_SAVE size/hash differs")
        _faint_need(codec.strict_checksum_witness(raw[:CARTRAM_BYTES], layout)["valid"], f"{inst}: LINK_SAVE checksum refused")
        linked = codec.decode_saved_party(raw[:CARTRAM_BYTES], layout, copy_name="primary")["mons"]
        saved = Path(witness["saveram_path"]).read_bytes()[:CARTRAM_BYTES]
        final = codec.decode_saved_party(saved, layout, copy_name="primary")["mons"]
        _faint_need(_frame(stage) < _frame(witness, "save_completed_frame") <= _frame(witness), f"{inst}: save chronology differs")
        stages[inst], snapshots[inst], parties[inst] = stage, stage["saveram_path"], (layout, linked, final, witness)
    decoded, row, document = _pair_oracle(results, data_dir=data_dir, area_id=area_id, ot_ids=ot_ids,
                                         boot_saveram=boot_saveram, status=("dead", "memorial"), snapshots=snapshots)
    _faint_need(row.get("cause") == "battle" and row.get("initiating_player") == "a" and row.get("killed_at"),
                "server death is not A's battle faint")
    if active:
        _faint_need(row["status"] == "memorial", "active faint requires independently saved memorial pair")
    memorial_preimages = {}
    if row["status"] == "memorial":
        for inst in ("a", "b"):
            layout, linked, _final, witness = parties[inst]
            observed, preimage = _memorial_party(results[inst], decoded[inst]["key"], layout, snapshots[inst], witness)
            parties[inst] = layout, linked, observed, witness
            memorial_preimages[inst] = preimage
    for inst in ("a", "b"):
        _, linked, final, _ = parties[inst]
        key = decoded[inst]["key"]
        old_keys, new_keys = [codec.key(m) for m in linked], [codec.key(m) for m in final]
        _faint_need(len(set(old_keys)) == len(old_keys) and sorted(old_keys) == sorted(new_keys), f"{inst}: party identity changed")
        old, new = linked[old_keys.index(key)], final[new_keys.index(key)]
        _faint_need(stages[inst].get("key") == key and old["hp"] > 0 and new["hp"] == 0, f"{inst}: linked mon did not go alive to HP zero")
        if inst == "b":
            _faint_need(old_keys == new_keys, "B party order changed")
            for before, after in zip(linked, final, strict=True):
                if active:
                    _faint_need(all(after[field] == before[field] for field in ("species_id", "dv_word", "ot_id", "ot_raw_hex", "nickname_raw_hex", "species_marker")),
                                "active B identity/names changed")
                    _faint_need((after["hp"] == 0 and after["status"] == 0) if codec.key(before) == key else after["hp"] > 0,
                                "active B linked preimage not dead or starter not alive")
                    continue  # Actual battle reward/PP/stat changes are not independently quantified here.
                expected = bytearray.fromhex(before["raw_hex"])
                if codec.key(before) == key:
                    expected[parties[inst][0].constants["MON_STATUS"]] = 0
                    at = parties[inst][0].constants["MON_HP"]
                    expected[at:at + 2] = bytes(2)
                _faint_need(after["raw_hex"] == expected.hex()
                            and all(after[field] == before[field] for field in ("ot_raw_hex", "nickname_raw_hex", "species_marker")),
                            "B other mons or target non-HP/status bytes changed")
    a_key, b_key = decoded["a"]["key"], decoded["b"]["key"]
    faint, sent = _one_marker(results["a"], "ENGINE_FAINT"), _one_marker(results["a"], "FAINT_SENT")
    _faint_need(faint.get("site_id") == site and faint.get("cause") == cause
                and faint.get("key") == sent.get("key") == a_key, "A faint event/key/cause differs")
    _faint_need(type(faint.get("slot")) is int and 0 <= faint["slot"] < len(parties["a"][2])
                and codec.key(parties["a"][2][faint["slot"]]) == a_key
                and type(sent.get("seq")) is int and sent["seq"] >= 0, "A faint slot/sequence invalid")
    _faint_need(_frame(stages["a"]) < _frame(faint) <= _frame(sent)
                <= _frame(parties["a"][3], "save_completed_frame"), "A faint chronology differs")
    if memorial_preimages:
        _faint_need(_frame(sent) <= _frame(memorial_preimages["a"]), "A memorial preimage precedes faint")
    _faint_need(not any(line.startswith("PARTY_HP_WRITE ") for line in results["a"].splitlines()), "A used a harness/permit faint instead of battle")
    if active:
        proof = validate_faint_active_markers(results, title_b=decoded["b"]["title"], key_a=a_key, key_b=b_key,
                                              species_b=decoded["b"]["species"], trainer=trainer)
        layout, linked, final, witness = parties["b"]
        slot = proof["active"]["slot"]
        replacement = proof["replaced"]["active_slot"]
        _faint_need(slot < len(final) and codec.key(final[slot]) == b_key and replacement < len(final)
                    and codec.key(final[replacement]) != b_key and final[replacement]["hp"] > 0, "active/replacement slots differ from saved identities")
        for _, repeat in _tag_rows(results["b"], "PARTY_HP_WRITE"):
            raw = _hex_bytes(repeat.get("before_party_hex"), layout.party_size * layout.constants["PARTY_LENGTH"], "active idempotent party")
            observed = [codec.decode_party_mon(raw[index * layout.party_size:(index + 1) * layout.party_size], layout,
                          species_marker=raw[index * layout.party_size]) for index in range(len(final))]
            _faint_need([codec.key(mon) for mon in observed] == [codec.key(mon) for mon in final], "active checkpoint repeat changes identity")
            _faint_write(repeat, layout, observed, observed, b_key, artifact_kind=(_last_tagged(results["b"], "CLIENT") or {}).get("artifact_kind", "clean"))
    else:
        _bench_faint_writes(results, parties, stages, b_key)
    log = (Path(data_dir) / "server.log").read_text(encoding="utf-8", errors="replace")
    issued = re.search(r"\[a\] faint → force_faint b:" + re.escape(b_key) + r"(?:\s|$)", log)
    _faint_need(issued, "server did not issue force_faint to B")
    if row["status"] == "memorial":
        end = log.find(f"pair in {area_id} fully memorialized", issued.end())
        _faint_need(end >= 0, "server lacks successful memorial transition after death")
        pending = document.get("pending_memorials")
        _faint_need(isinstance(pending, dict), "server memorial pending state missing")
        for inst in ("a", "b"):
            key = decoded[inst]["key"]
            pattern = rf"\[{inst}\] memorialize_done key=" + re.escape(key[:8]) + r"(?:\s|$)"
            ack = re.search(pattern, log[issued.end():end])
            _faint_need(ack and isinstance(pending.get(inst), list) and key not in pending[inst],
                        f"server lacks settled {inst} memorial success after death")
    facts = _verified_facts(decoded, area_id, row["status"])
    if active:
        facts["death"] = "active"
    return facts


def _bench_faint_writes(results, parties, stages, b_key):
    """Unchanged bench-write proof, kept distinct from the active battle-hold contract."""
    writes = [(index, json.loads(line.removeprefix("PARTY_HP_WRITE ")))
              for index, line in enumerate(results["b"].splitlines()) if line.startswith("PARTY_HP_WRITE ")]
    _faint_need(1 <= len(writes) <= 2 and all(isinstance(row, dict) for _, row in writes),
                "one write and at most one idempotent repeat required")
    command = f"RX force_faint key={b_key}"
    commands = [index for index, line in enumerate(results["b"].splitlines()) if line == command]
    _faint_need(len(writes) <= len(commands) <= 2, "B lacks force_faint receipt or repeats exceed the lane budget")
    layout, linked, final, witness = parties["b"]
    previous = None
    for ordinal, (position, write) in enumerate(writes):
        _faint_need(commands[ordinal] < position, "B write precedes force_faint receipt")
        if previous is None:
            _faint_need(_frame(stages["b"]) < _frame(write) <= _frame(witness, "save_completed_frame"), "B write chronology differs")
        _faint_write(write, layout, linked if previous is None else final, final, b_key, artifact_kind=(_last_tagged(results["b"], "CLIENT") or {}).get("artifact_kind", "clean"))
        if previous is not None:
            _faint_need(_frame(write) >= _frame(previous) and write["slot"] == previous["slot"]
                        and bytes.fromhex(write["before_party_hex"]) == bytes.fromhex(previous["after_party_hex"])
                        and bytes.fromhex(write["after_party_hex"]) == bytes.fromhex(write["before_party_hex"]),
                        "B repeat is not idempotent")
        previous = write


def faint_oracle(results, *, data_dir, area_id="route_29", ot_ids=None, boot_saveram=None, on_verified=None):
    """PYDEC death proof plus source-checked synchronous production party_hp write evidence."""
    try:
        facts = _faint_oracle(results, data_dir=data_dir, area_id=area_id, ot_ids=ot_ids, boot_saveram=boot_saveram)
        if on_verified is not None:
            on_verified(facts)
    except (KeyError, TypeError, ValueError, OSError, IndexError) as exc:
        raise RuntimeError(f"faint evidence missing or malformed: {exc}") from exc


def faint_active_oracle(results, *, data_dir, area_id="route_29", ot_ids=None, boot_saveram=None, on_verified=None,
                        trainer=False):
    """Active battle-hold death + saved memorial proof; battle rewards are not quantified. trainer=True is
    gen2_faint_active_trainer (a Route 30 trainer battle, O-30 review MINOR-5)."""
    try:
        facts = _faint_oracle(results, data_dir=data_dir, area_id=area_id, ot_ids=ot_ids, boot_saveram=boot_saveram,
                              active=True, trainer=trainer)
        if on_verified is not None:
            on_verified(facts)
    except (KeyError, TypeError, ValueError, OSError, IndexError, AttributeError) as exc:
        raise RuntimeError(f"active faint evidence missing or malformed: {exc}") from exc


def _admit_need(condition, reason):
    if not condition:
        raise RuntimeError(f"admit_wrong_rom: {reason}")


def _admit_markers(results, admitted, refused):
    _admit_need({admitted, refused} == {"a", "b"}, "explicit admitted/refused roles must be distinct")
    lock = json.loads((REPO_ROOT / "data/gen2_sources.lock.json").read_text())
    wrong_pin = lock["outputs"]["pokecrystal11"]["sha1"]
    heads = {}
    for inst, role in ((admitted, "admitted"), (refused, "refused")):
        text = results[inst]
        head, receipt = _one_marker(text, "DUO_GEN2"), _one_marker(text, "RECEIPT")
        _admit_need(head.get("player") == inst and head.get("scenario") == "gen2_admit_wrong_rom"
                    and head.get("expect_admission", "admitted") == role, f"{inst}: header role/scenario differs")
        for field in ("player", "scenario", "attempt", "title", "rom_sha1"):
            _admit_need(field in head and receipt.get(field) == head[field], f"{inst}: receipt {field} differs")
        _admit_need(receipt.get("schema") == "gen2-duo-admit-wrong-rom-v1"
                    and receipt.get("expect_admission") == role, f"{inst}: receipt role/schema differs")
        verdicts = [line for line in text.splitlines() if line.startswith("RESULT:")]
        _admit_need(len(verdicts) == 1 and re.match(r"^RESULT: PASS(?:\s|$)", verdicts[0]), f"{inst}: no sole PASS verdict")
        heads[inst] = head
    text = results[refused]
    head = heads[refused]
    _admit_need(head.get("title") == "crystal" and head.get("rom_sha1") == wrong_pin,
                "refused input must be pinned Crystal 1.1")
    for tag in ("CLIENT", "BOOTED", "MYKEY", "HELLO", "HELLO_AGAIN", "TX", "SAVE_WITNESS", "HOLD", "ENGINE_CAPTURE"):
        _admit_need(not any(line.startswith(tag + " ") for line in text.splitlines()), f"refused half printed {tag}")
    refusal, quiet, cart = (_one_marker(text, tag) for tag in ("ADMISSION_REFUSED", "NO_TRAFFIC", "CARTRAM_UNCHANGED"))
    _admit_need(refusal.get("client") is False and refusal.get("rom_sha1") == wrong_pin
                and isinstance(refusal.get("console"), str)
                and ("refused (production admission)" in refusal["console"] or "not a Gen 2 cartridge" in refusal["console"]),
                "refusal lacks pinned ROM or production refusal line")
    _admit_need(type(quiet.get("tx")) is int and quiet["tx"] == 0
                and type(quiet.get("frames")) is int and quiet["frames"] >= 600
                and _frame(quiet) >= _frame(refusal) + quiet["frames"], "refused hold/traffic differs")
    _admit_need(text.index("ADMISSION_REFUSED ") < text.index("NO_TRAFFIC ") < text.index("CARTRAM_UNCHANGED "),
                "refused marker chronology differs")
    receipt = _one_marker(text, "RECEIPT")
    _admit_need(receipt.get("refusal") == refusal and receipt.get("cartram_sha256") == cart.get("after"),
                "refused receipt evidence differs")
    text = results[admitted]
    witness, client, title = _boot_marker(admitted, text)
    _admit_need(client.get("production_admitted") is True
                and client.get("rom_sha1") == _executed_identity(title, client.get("artifact_kind", "clean"))[0],
                "admitted half lacks production/pinned ROM")
    hello, hold = _one_marker(text, "HELLO"), _one_marker(text, "HOLD")
    _one_marker(text, "BOOTED")
    _admit_need(not any(line.startswith(("HELLO_AGAIN ", "ADMISSION_REFUSED ", "ENGINE_CAPTURE ")) for line in text.splitlines()),
                "admitted half rehelloed/refused/captured")
    _admit_need(type(hold.get("hellos")) is int and hold["hellos"] == 1
                and type(hold.get("frames")) is int and hold["frames"] >= 600
                and _frame(hold) >= _frame(hello) + hold["frames"]
                and _frame(witness, "save_completed_frame") > _frame(hold), "admitted hold/save chronology differs")
    return heads, cart


def check_admit_wrong_rom_witness(results, *, boot_saveram, refused_saveram, admitted="a", refused="b"):
    """Only the admitted role saves; the refused role must retain its independent seed image."""
    try:
        _heads, cart = _admit_markers(results, admitted, refused)
        _check_save_witness(results, (admitted,))
        boot = Path(boot_saveram[refused]).read_bytes()
        final = Path(refused_saveram).read_bytes()
        digest = hashlib.sha256(boot[:CARTRAM_BYTES]).hexdigest()
        _admit_need(len(boot) == len(final) == SAVERAM_BYTES and boot[:CARTRAM_BYTES] == final[:CARTRAM_BYTES],
                    "refused disk CartRAM differs from seed")
        _admit_need(cart.get("before") == cart.get("after") == digest, "refused CartRAM markers differ from seed")
    except (KeyError, TypeError, ValueError, OSError, IndexError) as exc:
        raise RuntimeError(f"admit_wrong_rom evidence missing or malformed: {exc}") from exc


def admit_wrong_rom_oracle(results, *, data_dir, before, after, boot_saveram, refused_saveram,
                            admitted="a", refused="b", on_verified=None):
    """Prove no refused server identity/traffic or save change, with one admitted native save."""
    from server.adapters import gen2_codec as codec

    try:
        check_admit_wrong_rom_witness(results, boot_saveram=boot_saveram, refused_saveram=refused_saveram,
                                      admitted=admitted, refused=refused)
        duo = _duo_marker(admitted, results[admitted])
        witness, _, title = _boot_marker(admitted, results[admitted])
        seed = Path(boot_saveram[admitted]).read_bytes()
        saved = Path(witness["saveram_path"]).read_bytes()
        _admit_need(hashlib.sha256(seed).hexdigest() == duo["fixture_sha256"], "admitted fixture hash differs")
        layout = codec.for_foundation(title)
        _admit_need(codec.strict_checksum_witness(seed[:CARTRAM_BYTES], layout)["valid"], "admitted seed checksum invalid")
        old = codec.decode_saved_party(seed[:CARTRAM_BYTES], layout, copy_name="primary")["mons"]
        new = codec.decode_saved_party(saved[:CARTRAM_BYTES], layout, copy_name="primary")["mons"]
        _admit_need(old and new == old, "admitted party changed during passive hold")
        hello_ot = _hello_ot_id(admitted, results[admitted])
        for label, snapshot in (("before", before), ("after", after)):
            players = snapshot["status"]["players"]
            live = snapshot["raw"]["_live"]["connected_players"]
            doc, events = snapshot["links"], snapshot["events"]
            _admit_need(isinstance(live, dict) and refused not in live and admitted in live,
                        f"{label}: refused player row or admitted row missing")
            active = players[admitted]
            # RESULT is followed by client.exit(); the final poll may see a closed
            # socket. The admission record and saved identity survive that close.
            _admit_need((label == "after" or active.get("connected") is True)
                        and active.get("admission") == "admitted"
                        and not active.get("identity_error"), f"{label}: admitted public status differs")
            idle = players.get(refused, {})
            _admit_need(not any(idle.get(field) for field in ("connected", "party_keys", "trainer_name", "current_area_id", "identity_error")),
                        f"{label}: refused public player adopted state")
            identities = doc["player_identity"]
            _admit_need(refused not in identities and str(identities[admitted]["ot_id"]) == str(hello_ot),
                        f"{label}: server identity lock differs")
            _admit_need(isinstance(events, list) and all(isinstance(row, dict) for row in events)
                        and not any(row.get("player") == refused for row in events), f"{label}: refused server event")
            hellos = [row for row in events if row.get("type") == "hello" and row.get("player") == admitted]
            _admit_need(len(hellos) == 1 and hellos[0].get("text", "").startswith("Connected ("),
                        f"{label}: missing sole accepted hello")
            _admit_need(doc["links"] == [] and doc.get("pending_captures", {}) == {}, f"{label}: unexpected link/capture")
        _admit_need(before["links"] == after["links"], "links.json changed across passive hold")
        _admit_need(before["events"] == after["events"], "server events changed across passive hold")
        for name in ("links", "events"):
            disk = json.loads((Path(data_dir) / f"{name}.json").read_text(encoding="utf-8"))
            _admit_need(disk == after[name], f"persisted {name} differs from final snapshot")
        if on_verified is not None:
            wrong = _one_marker(results[refused], "DUO_GEN2")
            on_verified({admitted: "admitted", refused: "refused", "area": "none", "status": "refused",
                         "titles": "/".join(title if side == admitted else "crystal" for side in ("a", "b")),
                         f"rom_{refused}": wrong["rom_sha1"]})
    except (KeyError, TypeError, ValueError, OSError, IndexError) as exc:
        raise RuntimeError(f"admit_wrong_rom evidence missing or malformed: {exc}") from exc


def _reconnect_need(condition, reason):
    if not condition:
        raise RuntimeError(f"reconnect: {reason}")


def _reconnect_pass(text):
    rows = [line for line in text.splitlines() if line.startswith("RESULT:")]
    _reconnect_need(len(rows) == 1 and re.match(r"^RESULT: PASS(?:\s|$)", rows[0]), "missing sole PASS verdict")


def _reconnect_head(text, player):
    head, client = _one_marker(text, "DUO_GEN2"), _one_marker(text, "CLIENT")
    _duo_marker(player, text)
    _reconnect_need(head.get("player") == player and head.get("scenario") == "gen2_reconnect",
                    "player/scenario differs")
    _kind, pin = _client_artifact(player, client, head)
    _reconnect_need(head["rom_sha1"] == client.get("rom_sha1") == pin
                    and client.get("title") == head["title"] and client.get("production_admitted") is True,
                    "production client/title/ROM pin differs")
    _one_marker(text, "HELLO")
    _reconnect_need(not any(line.startswith("HELLO_AGAIN ") for line in text.splitlines()), "duplicate hello")
    return head


def _reconnect_receipt(text, head, phase):
    receipt = _one_marker(text, "RECEIPT")
    _reconnect_need(receipt.get("schema") == "gen2-duo-reconnect-v1" and receipt.get("phase") == phase,
                    "receipt schema/phase differs")
    for field in ("player", "scenario", "attempt", "case", "title", "rom_sha1", "fixture_sha256"):
        _reconnect_need(receipt.get(field) == head.get(field), f"receipt {field} differs")
    return receipt


def check_reconnect_witness(results, *, initial_results, relaunch_results, staged_saves):
    """Initial captures must save; only the two explicitly bound relaunch phases omit saving."""
    try:
        _reconnect_need(set(relaunch_results) == set(staged_saves) == {"same_save", "wrong_save"}, "both relaunches required")
        _reconnect_need(results["a"] == relaunch_results["wrong_save"] and results["b"] == initial_results["b"],
                        "final results do not name wrong-save A and held B")
        check_save_witness(initial_results)
        for inst in ("a", "b"):
            text = initial_results[inst]
            head = _reconnect_head(text, inst)
            ready, save, capture = (_one_marker(text, tag) for tag in ("RECONNECT_READY", "SAVE_WITNESS", "ENGINE_CAPTURE"))
            _reconnect_need(ready.get("phase") == "initial" and ready.get("player") == inst
                            and ready.get("key") == capture.get("key") and _frame(ready) >= _frame(save)
                            and text.index("SAVE_WITNESS ") < text.index("RECONNECT_READY "), "initial ready/save binding differs")
            if inst == "a":
                _reconnect_need(not any(line.startswith(("RESULT:", "RECEIPT ")) for line in text.splitlines()),
                                "initial A must be killed before verdict")
            else:
                _reconnect_pass(text)
                _reconnect_receipt(text, head, "initial")
                stayed = _one_marker(text, "B_STAYED")
                _reconnect_need(stayed.get("hellos") == 1 and stayed.get("force_faint") == stayed.get("box_mon") == 0
                                and _frame(stayed) >= _frame(ready), "B did not stay quiet")
                suffix = text[text.index("RECONNECT_READY "):]
                _reconnect_need(not re.search(r"^RX (?:force_faint|box_mon)(?:\s|$)", suffix, re.M), "B received a mutation command")
        original = _duo_marker("a", initial_results["a"])
        for phase in ("same_save", "wrong_save"):
            text = relaunch_results[phase]
            _reconnect_pass(text)
            head = _reconnect_head(text, "a")
            _one_marker(text, "BOOTED")
            back, hello = _one_marker(text, "RECONNECT_HELLO"), _one_marker(text, "HELLO")
            receipt = _reconnect_receipt(text, head, phase)
            _reconnect_need(head["title"] == original["title"] and head["case"] == f"{head['title']}_battle",
                            "relaunch title/battle case differs")
            seed = Path(staged_saves[phase]).read_bytes()
            _reconnect_need(head["fixture_sha256"] == hashlib.sha256(seed).hexdigest(), "staged save fingerprint differs")
            _reconnect_need(back.get("phase") == phase and back.get("hellos") == 1
                            and back.get("linked") is (phase == "same_save")
                            and back.get("ot_id") == hello.get("ot_id") and _frame(back) >= _frame(hello),
                            "relaunch hello/phase differs")
            for field in ("expected_key", "linked", "ot_id"):
                _reconnect_need(receipt.get(field) == back.get(field), f"relaunch receipt {field} differs")
            _reconnect_need(not any(line.startswith(("SAVE_WITNESS ", "ENGINE_CAPTURE ", "RECONNECT_READY ")) for line in text.splitlines()),
                            "relaunch unexpectedly played/saved")
            _reconnect_need(not re.search(r"^RX (?:force_faint|box_mon)(?:\s|$)", text, re.M), "A relaunch received mutation command")
            if phase == "wrong_save":
                hud = _one_marker(text, "WRONG_SAVE_HUD")
                target = "[x] WRONG SAVE: slot A"
                messages = [json.loads(line[len("RX_TEXT "):]) for line in text.splitlines() if line.startswith("RX_TEXT ")]
                _reconnect_need(hud.get("text") == target and _frame(hud) >= _frame(back)
                                and any(isinstance(row, dict) and row.get("cmd") == "hud_show" and row.get("text") == target
                                        and _frame(row) >= _frame(hello) for row in messages), "wrong-save HUD missing or early")
            else:
                _reconnect_need(not any(line.startswith("WRONG_SAVE_HUD ") for line in text.splitlines()), "same save refused")
    except (KeyError, TypeError, ValueError, OSError, IndexError, AttributeError) as exc:
        raise RuntimeError(f"reconnect evidence missing or malformed: {exc}") from exc


def _relaunch_checksum_witness(raw, layout):
    """CONTINUE rewrites backup player data while VBlank can advance the five clock bytes.

    C save.asm:596-609; G/S:538-550. Both checksums/markers still must be valid.
    Compare each codec copy region separately: G/S backup player data is split.
    This exception is only for a relaunch flush, never an ordinary SAVE_WITNESS.
    """
    from server.adapters import gen2_codec as codec
    from tools.gen2_fixtures import _REGION_STARTS

    if not isinstance(raw, bytes) or len(raw) != CARTRAM_BYTES:
        return False
    report = codec.checksum_report(raw, layout)
    if not all(report[copy]["checksum_valid"] and report[copy]["markers_valid"] for copy in ("primary", "backup")):
        return False
    clock = (("wGameTimeHours", 2), ("wGameTimeMinutes", 1), ("wGameTimeSeconds", 1), ("wGameTimeFrames", 1))
    hours = layout.addresses["wGameTimeHours"]
    if tuple(layout.addresses[name] for name, _ in clock) != (hours, hours + 2, hours + 3, hours + 4):
        return False
    allowed = {region.name: set() for region in layout.regions}
    for symbol, width in clock:
        address = layout.addresses[symbol]
        found = [region for region in layout.regions if layout.addresses[_REGION_STARTS[region.name]] <= address
                 and address + width <= layout.addresses[_REGION_STARTS[region.name]] + region.length]
        if len(found) != 1:
            return False
        region = found[0]
        offset = address - layout.addresses[_REGION_STARTS[region.name]]
        allowed[region.name].update(range(offset, offset + width))
    return all(all(raw[region.primary + offset] == raw[region.backup + offset] or offset in allowed[region.name]
                   for offset in range(region.length)) for region in layout.regions)


def reconnect_oracle(results, *, data_dir, initial_results, relaunch_results, boot_saveram,
                     staged_saves, relaunch_saves, snapshots, on_verified=None):
    """Independent saved identities and unchanged links across a kill and both required reconnects."""
    from server.adapters import gen2_codec as codec
    from tests.live.test_gen2_new_gates import qualified_identity
    from tools.gen2_fixtures import _saved_field

    try:
        check_reconnect_witness(results, initial_results=initial_results, relaunch_results=relaunch_results,
                                 staged_saves=staged_saves)
        decoded, _, document = _pair_oracle(initial_results, data_dir=data_dir, boot_saveram=boot_saveram)
        title, linked_key = decoded["a"]["title"], decoded["a"]["key"]
        layout = codec.for_foundation(title)
        witness = _one_marker(initial_results["a"], "SAVE_WITNESS")
        initial_raw = witness_save_bytes(witness, layout)
        original_ot = int.from_bytes(_saved_field(initial_raw, layout, "wPlayerID", 2), "big")
        _reconnect_need(_hello_ot_id("a", initial_results["a"]) == original_ot, "initial trainer OT differs from save")
        for phase in ("same_save", "wrong_save"):
            seed, flushed = Path(staged_saves[phase]).read_bytes(), Path(relaunch_saves[phase]).read_bytes()
            _reconnect_need(len(seed) == len(flushed) == SAVERAM_BYTES, "relaunch save size differs")
            _reconnect_need(codec.strict_checksum_witness(seed[:CARTRAM_BYTES], layout)["valid"]
                            and _relaunch_checksum_witness(flushed[:CARTRAM_BYTES], layout), "relaunch checksum refused")
            old = codec.decode_saved_party(seed[:CARTRAM_BYTES], layout, copy_name="primary")["mons"]
            new = codec.decode_saved_party(flushed[:CARTRAM_BYTES], layout, copy_name="primary")["mons"]
            ot = int.from_bytes(_saved_field(seed, layout, "wPlayerID", 2), "big")
            _reconnect_need(old and old == new and ot == int.from_bytes(_saved_field(flushed, layout, "wPlayerID", 2), "big"),
                            "relaunch saved party/trainer changed")
            back = _one_marker(relaunch_results[phase], "RECONNECT_HELLO")
            _reconnect_need(back["expected_key"] == linked_key and back["ot_id"] == ot,
                            "relaunch marker identity differs from decoded save")
            keys = [codec.key(mon) for mon in new]
            if phase == "same_save":
                _reconnect_need(normalized_gameplay_cartram(seed, layout) == normalized_gameplay_cartram(initial_raw, layout)
                                and ot == original_ot and linked_key in keys,
                                "same-save seed lost initial linked image")
            else:
                kind = _one_marker(relaunch_results[phase], "CLIENT").get("artifact_kind", "clean")
                kind = "overlay" if kind == "rand_overlay" else kind   # C-5: the overlay's fixture qualification
                qualified_ot = qualified_identity(f"{title}_battle_ot2", seed, repo=REPO_ROOT, kind=kind)
                _reconnect_need(qualified_ot == ot != original_ot and linked_key not in keys,
                                "wrong-save seed is not qualified other-OT battle input")
        _reconnect_need(set(snapshots) == {"initial", "disconnected", "same_save", "before_wrong", "wrong_save"},
                        "all five server snapshots required")
        baseline = snapshots["initial"]
        identities = baseline["links"]["player_identity"]
        _reconnect_need(str(identities["a"]["ot_id"]) == str(original_ot), "initial server identity differs")
        for inst in ("a", "b"):
            hellos = [row for row in baseline["events"] if row.get("type") == "hello" and row.get("player") == inst]
            _reconnect_need(len(hellos) == 1 and hellos[0].get("text", "").startswith("Connected ("),
                            "initial server needs exactly one accepted hello per side")
        # mon_stats is a display cache refreshed by hello/tick, not encounter/identity state.
        # Reconnect may flush the latest cache; every persisted rule field must stay fixed.
        rule_state = {key: value for key, value in baseline["links"].items() if key != "mon_stats"}
        for phase, snap in snapshots.items():
            players = snap["status"]["players"]
            _reconnect_need(players["b"].get("connected") is True and not players["b"].get("identity_error"), "B disconnected or rejected")
            _reconnect_need(snap["links"]["links"] == baseline["links"]["links"]
                            and snap["links"]["player_identity"] == identities
                            and snap["links"].get("pending_captures", {}) == baseline["links"].get("pending_captures", {}),
                            f"{phase}: link/identity/pending changed")
            _reconnect_need({key: value for key, value in snap["links"].items() if key != "mon_stats"} == rule_state,
                            f"{phase}: persisted rule state changed")
            if phase in ("disconnected", "before_wrong"):
                _reconnect_need(players["a"].get("connected") is False, f"{phase}: A never disconnected")
            elif phase == "wrong_save":
                _reconnect_need("Identity mismatch for slot A" in players["a"].get("identity_error", ""), "wrong-save server rejection absent")
            else:
                _reconnect_need(players["a"].get("connected") is True and not players["a"].get("identity_error")
                                and linked_key in players["a"].get("party_keys", []), f"{phase}: A link not adopted")
        _reconnect_need(snapshots["disconnected"]["events"] == baseline["events"], "events changed while disconnected")
        for old_phase, new_phase, text in (("initial", "same_save", "Connected ("),
                                          ("before_wrong", "wrong_save", "REJECTED — wrong save/slot")):
            old, new = snapshots[old_phase]["events"], snapshots[new_phase]["events"]
            _reconnect_need(isinstance(old, list) and isinstance(new, list) and len(new) == len(old) + 1 and new[1:] == old,
                            f"{new_phase}: event history changed beyond one hello")
            row = new[0]
            _reconnect_need(row.get("player") == "a" and row.get("type") == "hello"
                            and (row.get("text", "").startswith(text) if new_phase == "same_save" else row.get("text") == text),
                            f"{new_phase}: missing expected server hello")
        _reconnect_need(snapshots["same_save"]["events"] == snapshots["before_wrong"]["events"], "events changed between relaunches")
        _reconnect_need(snapshots["before_wrong"]["links"] == snapshots["wrong_save"]["links"], "wrong save changed persisted state")
        _reconnect_need(document == snapshots["wrong_save"]["links"], "final links differ from wrong-save snapshot")
        disk_events = json.loads((Path(data_dir) / "events.json").read_text(encoding="utf-8"))
        _reconnect_need(disk_events == snapshots["wrong_save"]["events"], "final events differ from wrong-save snapshot")
        if on_verified is not None:
            on_verified(_verified_facts(decoded, "route_29", "alive"))
    except (KeyError, TypeError, ValueError, OSError, IndexError, AttributeError, AssertionError) as exc:
        raise RuntimeError(f"reconnect evidence missing or malformed: {exc}") from exc


def soft_reset_oracle(results, *, data_dir, before, after, boot_saveram, on_verified=None):
    """Both native saves preserve the party; one WRAM-clear reset produces one same-OT rehello."""
    from server.adapters import gen2_codec as codec
    from tools.gen2_fixtures import _saved_field

    def need(condition, reason):
        if not condition:
            raise RuntimeError(f"soft_reset: {reason}")

    try:
        check_save_witness(results)
        titles, identities = {}, {}
        reset_tags = ("HELLO_AT_CHECKPOINT", "CHORD_GATE", "CHORD", "RESET_SEEN", "HELLO_CLEARED",
                      "WRITES_PAUSED", "REBOOTED", "WRITES_RESUMED", "HELLO_AGAIN", "REHELLO", "NO_WRITES_IN_WINDOW")
        for inst in ("a", "b"):
            text = results[inst]
            _reconnect_pass(text)
            head = _one_marker(text, "DUO_GEN2")
            witness, client, title = _boot_marker(inst, text)
            hello, receipt, booted = (_one_marker(text, tag) for tag in ("HELLO", "RECEIPT", "BOOTED"))
            need(head.get("player") == inst and head.get("scenario") == "gen2_soft_reset", "player/scenario differs")
            need(client.get("production_admitted") is True
                 and head["rom_sha1"] == _executed_identity(title, client.get("artifact_kind", "clean"))[0], "production/pinned ROM differs")
            need(receipt.get("schema") == "gen2-duo-soft-reset-v1" and receipt.get("input_mode") == "normal_buttons"
                 and receipt.get("harness_write_scopes") == [], "receipt schema/input/write scopes differ")
            for field in ("player", "scenario", "attempt", "case", "title", "rom_sha1", "fixture_sha256"):
                need(receipt.get(field) == head.get(field), f"{inst}: receipt {field} differs")
            for field, value in (("hello", hello), ("client", client), ("save", witness), ("booted", booted)):
                need(receipt.get(field) == value, f"{inst}: receipt {field} evidence differs")
            need(not any(line.startswith(("ENGINE_CAPTURE ", "PARTY_HP_WRITE ")) for line in text.splitlines()), "capture/write during passive reset")
            layout = codec.for_foundation(title)
            seed, final = Path(boot_saveram[inst]).read_bytes(), Path(witness["saveram_path"]).read_bytes()
            need(len(seed) == SAVERAM_BYTES and hashlib.sha256(seed).hexdigest() == head["fixture_sha256"], "boot fixture hash/size differs")
            need(codec.strict_checksum_witness(seed[:CARTRAM_BYTES], layout)["valid"], "boot checksum refused")
            old = codec.decode_saved_party(seed[:CARTRAM_BYTES], layout, copy_name="primary")["mons"]
            new = codec.decode_saved_party(final[:CARTRAM_BYTES], layout, copy_name="primary")["mons"]
            ot = int.from_bytes(_saved_field(seed, layout, "wPlayerID", 2), "big")
            need(old and old == new, "saved party changed across passive reset")
            need(ot == int.from_bytes(_saved_field(final, layout, "wPlayerID", 2), "big") == hello.get("ot_id"), "saved/hello trainer identity differs")
            titles[inst], identities[inst] = title, ot
            if inst == "a":
                rows = {tag: _one_marker(text, tag) for tag in reset_tags}
                # Main's HELLO_AGAIN can arrive during CONTINUE before REBOOTED is logged.
                ordered = ("HELLO", "HELLO_AT_CHECKPOINT", "CHORD_GATE", "CHORD", "RESET_SEEN",
                           "HELLO_CLEARED", "WRITES_PAUSED", "REBOOTED", "WRITES_RESUMED", "REHELLO",
                           "NO_WRITES_IN_WINDOW", "SAVE_WITNESS")
                need(all(text.index(left + " ") < text.index(right + " ") for left, right in zip(ordered, ordered[1:], strict=False)),
                     "reset markers out of order")
                at, chord, reset = rows["HELLO_AT_CHECKPOINT"], rows["CHORD"], rows["RESET_SEEN"]
                need(at.get("writes_enabled") is True and at.get("hellos") == 1 and at.get("ot_id") == ot,
                     "reset did not start admitted with writes enabled")
                need(type(chord.get("frames")) is int and chord["frames"] == 4
                     and _frame(rows["CHORD_GATE"]) <= _frame(chord), "native chord differs")
                for tag, start, low, high in (("RESET_SEEN", chord, 30, 60), ("HELLO_CLEARED", reset, 0, 180),
                                             ("WRITES_PAUSED", reset, 180, 420), ("WRITES_RESUMED", reset, 0, None)):
                    row = rows[tag]
                    delta = _frame(row) - _frame(start)
                    need(type(row.get("delta")) is int and row["delta"] == delta
                         and delta >= low and (high is None or delta <= high), f"{tag}: measured delta outside reset window")
                again, rehello, resumed = rows["HELLO_AGAIN"], rows["REHELLO"], rows["WRITES_RESUMED"]
                need(again.get("n") == rehello.get("hellos") == 2 and again.get("ot_id") == rehello.get("ot_id") == ot,
                     "reset rehello count/identity differs")
                need(_frame(rows["WRITES_PAUSED"]) <= _frame(again) <= _frame(rehello)
                     and _frame(rows["REBOOTED"]) <= _frame(resumed) <= _frame(rehello)
                     and _frame(witness, "save_completed_frame") > _frame(rehello), "resume/rehello/save chronology differs")
                need(type(rows["NO_WRITES_IN_WINDOW"].get("writes")) is int and rows["NO_WRITES_IN_WINDOW"]["writes"] == 0,
                     "write in reset window")
                for field, tag in (("reset", "RESET_SEEN"), ("paused", "WRITES_PAUSED"), ("rehello", "REHELLO")):
                    need(receipt.get(field) == rows[tag], f"receipt {field} differs")
            else:
                need(not any(line.startswith(tuple(tag + " " for tag in reset_tags)) for line in text.splitlines()), "idle partner reset/rehelloed")
                idle = _one_marker(text, "IDLE_PARTNER")
                need(idle.get("hellos") == 1 and receipt.get("idle") == idle
                     and _frame(hello) <= _frame(idle) < _frame(witness, "save_completed_frame"), "idle partner hold/save differs")
        for label, snapshot in (("before", before), ("after", after)):
            document = snapshot["links"]
            need(document["links"] == [] and document.get("pending_captures", {}) == {}, "unexpected link/capture")
            for inst in ("a", "b"):
                player = snapshot["status"]["players"][inst]
                need((label != "before" or player.get("connected") is True) and not player.get("identity_error"),
                     f"{label}: missing initial connection/rejected {inst}")
                need(str(document["player_identity"][inst]["ot_id"]) == str(identities[inst]), f"{label}: server trainer identity differs")
                hellos = [row for row in snapshot["events"] if row.get("type") == "hello" and row.get("player") == inst]
                count = 2 if label == "after" and inst == "a" else 1
                need(len(hellos) == count and all(row.get("text", "").startswith("Connected (") for row in hellos),
                     f"{label}: accepted hello count differs")
        need(before["links"] == after["links"], "persisted rules/identity changed across reset")
        need(isinstance(before["links_bytes"], bytes) and before["links_bytes"] == after["links_bytes"]
             and json.loads(before["links_bytes"]) == before["links"], "links.json bytes changed across reset")
        need(len(after["events"]) == len(before["events"]) + 1 and after["events"][1:] == before["events"]
             and after["events"][0].get("player") == "a" and after["events"][0].get("type") == "hello",
             "event history changed beyond one A rehello")
        for name in ("links", "events"):
            need(json.loads((Path(data_dir) / f"{name}.json").read_text(encoding="utf-8")) == after[name], f"final {name} differs from snapshot")
        need((Path(data_dir) / "links.json").read_bytes() == after["links_bytes"], "final links.json bytes differ")
        if on_verified is not None:
            on_verified({"a": "reset", "b": "idle", "area": "none", "titles": "/".join(titles[inst] for inst in ("a", "b")),
                         "status": "unchanged"})
    except (KeyError, TypeError, ValueError, OSError, IndexError, AttributeError) as exc:
        raise RuntimeError(f"soft_reset evidence missing or malformed: {exc}") from exc


def _tag_rows(text, tag):
    values = [(at, json.loads(line[len(tag) + 1:])) for at, line in enumerate(text.splitlines()) if line.startswith(tag + " ")]
    _clause_need(all(isinstance(row, dict) for _, row in values), f"malformed {tag}")
    return values


def _clause_source(title):
    from tools.gen2_source_data import load_context

    source = load_context(title, root=REPO_ROOT).source_record()
    packs = {}
    for name in ("species_index", "evolutions", "encounter_tables"):
        packs[name] = json.loads((REPO_ROOT / f"data/games/gen2_{title}/{name}.json").read_text())
        _clause_need(packs[name]["source"] == source, f"{name} source provenance differs")
    enc = packs["encounter_tables"]
    slots = {(slot["species"], slot["level"]) for row in enc["wild"]["grass"]
             if enc["map_areas"][str(row["map_group"] * 256 + row["map_number"])] == "route_29" for slot in row["slots"]}
    return packs, slots, source


def _clause_inventory(raw, layout):
    from server.adapters import gen2_codec as codec

    party = codec.decode_saved_party(raw[:CARTRAM_BYTES], layout, copy_name="primary")["mons"]
    boxes = codec.verify_boxes(raw[:CARTRAM_BYTES], layout)
    rows = [("party", mon) for mon in party]
    rows += [(index, mon) for index, box in enumerate(boxes) for mon in box["mons"]]
    keys = [codec.key(mon) for _, mon in rows]
    _clause_need(len(keys) == len(set(keys)), "duplicate identity across saved party/boxes")
    return party, {codec.key(mon): (place, mon) for place, mon in rows}


def _clause_captures(results, boot_saveram, kind):
    from server.adapters import gen2_codec as codec
    from tools.gen2_fixtures import _saved_field

    check_save_witness(results)
    decoded = {}
    for inst in ("a", "b"):
        text = results[inst]
        _reconnect_pass(text)
        witness, client, title = _boot_marker(inst, text)
        head, cap, receipt = (_one_marker(text, tag) for tag in ("DUO_GEN2", "ENGINE_CAPTURE", "RECEIPT"))
        packs, slots, source = _clause_source(title)
        _clause_need(head.get("player") == inst and head.get("scenario") == f"gen2_{kind}_clause"
                     and client.get("production_admitted") is True
                     and head["rom_sha1"] == _executed_identity(title, client.get("artifact_kind", "clean"))[0], "clause production/header differs")
        _clause_need(receipt.get("schema") == f"gen2-duo-{kind}-clause-v1", "clause receipt schema differs")
        for field in ("player", "scenario", "attempt", "case", "title", "rom_sha1", "fixture_sha256"):
            _clause_need(receipt.get(field) == head.get(field), f"clause receipt {field} differs")
        seed, saved = Path(boot_saveram[inst]).read_bytes(), Path(witness["saveram_path"]).read_bytes()
        layout = codec.for_foundation(title)
        _clause_need(len(seed) == SAVERAM_BYTES and hashlib.sha256(seed).hexdigest() == head["fixture_sha256"]
                     and codec.strict_checksum_witness(seed[:CARTRAM_BYTES], layout)["valid"], "boot fingerprint/checksum differs")
        _, old = _clause_inventory(seed, layout)
        party, current = _clause_inventory(saved, layout)
        new_keys = set(current) - set(old)
        _clause_need(set(old) <= set(current) and len(new_keys) == 1, "saved inventory is not boot plus one catch")
        key = next(iter(new_keys))
        place, mon = current[key]
        _clause_need(cap.get("key") == key and cap.get("species_id") == mon["species_id"] and cap.get("level") == mon["level"]
                     and cap.get("area_id") == "route_29" and cap.get("site_id") == "capture_party_finalized"
                     and cap.get("acquisition") == "wild" and cap.get("destination") == "party", "engine capture differs from saved catch")
        _clause_need((mon["species_id"], mon["level"]) in slots, "catch outside pinned Route 29 grass slots")
        ot = int.from_bytes(_saved_field(seed, layout, "wPlayerID", 2), "big")
        _clause_need(mon["ot_id"] == ot == _hello_ot_id(inst, text), "captured OT differs from saved trainer")
        _clause_need(sum(n for _, n in _ball_pocket(saved, layout)) < sum(n for _, n in _ball_pocket(seed, layout)), "Ball pocket did not decrease")
        _clause_need(f"CAUGHT {key}" in text.splitlines(), "saved catch lacks CAUGHT marker")
        sent = _one_marker(text, "CAPTURE_SENT")
        _clause_need(sent.get("key") == key and _frame(cap) <= _frame(sent) < _frame(witness, "save_completed_frame"), "capture/send/save chronology differs")
        species = packs["species_index"]["species"][str(mon["species_id"])]
        ratio = species["gender_ratio"]
        _clause_need(ratio not in (0, 254, 255), "clause lane needs mixed-gender species")
        gender = "female" if ((mon["dvs"]["attack"] << 4) | mon["dvs"]["speed"]) <= ratio else "male"
        types = list(dict.fromkeys(t.removesuffix("_TYPE").title() for t in species["types"]))
        decoded[inst] = {"key": key, "species": mon["species_id"], "level": mon["level"], "title": title,
                         "mon": mon, "place": place, "party": party, "current": current, "layout": layout, "witness": witness,
                         "types": types, "gender": gender, "packs": packs, "receipt": receipt}
    _clause_need(decoded["a"]["key"] != decoded["b"]["key"], "captures share identity")
    return decoded


def _deposited_record(party_hex, layout):
    """Native deposit projection; every byte except restored PP must equal the party prefix.

    Both pins: move_mon.asm:711-766 stops at the first zero move and retains PP-Up
    bits. ComputeMaxPP (G/S item_effects.asm:2736-2782; C:2752-2798) caps each
    PP-Up increment at seven, so base 40 with three PP Ups restores 61, not 64.
    """
    raw = bytearray(_hex_bytes(party_hex, layout.party_size, "deposited party")[:layout.box_mon_size])
    pack = json.loads((REPO_ROOT / f"data/games/gen2_{layout.title}/moves.json").read_text(encoding="utf-8"))
    _clause_need(pack.get("schema") == "gen2-moves-v1" and pack.get("title") == layout.title
                 and pack.get("source") == layout.profile["source"], "deposit move pack title/source differs")
    rows = pack.get("moves")
    _clause_need(isinstance(rows, list) and all(isinstance(row, dict) and type(row.get("id")) is int for row in rows),
                 "deposit move table malformed")
    moves = {row["id"]: row for row in rows}
    _clause_need(len(moves) == len(rows) and set(moves) == set(range(1, 252)), "deposit move table has duplicate/missing/invalid ids")
    for slot in range(layout.constants["NUM_MOVES"]):
        move = raw[layout.constants["MON_MOVES"] + slot]
        if move == 0:
            break  # The native routine leaves this and all later PP bytes untouched.
        _clause_need(move in moves, f"deposit move {move} missing from pinned table")
        base = moves[move].get("pp")
        _clause_need(type(base) is int and 1 <= base <= 63, "deposit base PP malformed")
        offset = layout.constants["MON_PP"] + slot
        packed = raw[offset]
        maximum = base + (packed >> 6) * min(base // 5, 7)
        _clause_need(maximum <= 63, "deposit restored PP overflows packed PP bits")
        raw[offset] = (packed & 0xC0) | maximum
    return bytes(raw)


def _clause_rejection(results, decoded, document, events, kind):
    from server.adapters import gen2_codec as codec

    verdicts = {}
    for inst, row in decoded.items():
        cc, cv = _one_marker(results[inst], "CLAUSE_CAPTURE"), _one_marker(results[inst], "CLAUSE_VERDICT")
        _clause_need(cc.get("key") == row["key"] and cc.get("species_id") == row["species"] and cc.get("area_id") == "route_29"
                     and cc.get("types") == row["types"] and cc.get("gender") == row["gender"], "CLAUSE_CAPTURE differs from source/PYDEC")
        verdicts[inst] = cv.get("verdict")
        _clause_need(row["receipt"].get("clause") == kind and row["receipt"].get("verdict") == cv.get("verdict")
                     and _frame(cv) >= _frame(cc) and _frame(row["witness"], "save_completed_frame") > _frame(cv), "clause receipt/verdict chronology differs")
    shared = sorted(set(decoded["a"]["types"]) & set(decoded["b"]["types"]))
    violates = bool(shared) if kind == "type" else decoded["a"]["gender"] == decoded["b"]["gender"]
    if set(verdicts.values()) == {"linked"}:
        _clause_need(not violates and all(row["receipt"].get("path") == "clause_unobserved" for row in decoded.values()), "invalid clean clause result")
        return None
    _clause_need(violates and sorted(verdicts.values()) == ["partner_rejected", "rejected"], "no valid observed clause roles")
    reject = next(inst for inst in decoded if verdicts[inst] == "rejected")
    partner = "b" if reject == "a" else "a"
    own, other = decoded[reject], decoded[partner]
    _clause_need(other["place"] != other["layout"].constants["NUM_BOXES"] - 1
                 and (other["place"] != "party" or other["mon"]["hp"] > 0), "retained pending counterpart died or was memorialized")
    _clause_need(all(row["receipt"].get("path") == "clause_observed" for row in decoded.values()), "observed clause path missing")
    _clause_need(document["links"] == [] and document["area_states"].get("route_29") == f"pending_{reject}"
                 and document["retry_areas"].get(reject) == ["route_29"], "rejected clause changed link/retry state")
    pending = document["pending_captures"]
    _clause_need(set(pending) == {"route_29"} and set(pending["route_29"]) == {partner}
                 and pending["route_29"][partner].get("key") == other["key"]
                 and pending["route_29"][partner].get("species") == other["species"], "retained pending capture differs")
    captures = [row for row in events if row.get("type") == "capture" and row.get("area_id") == "route_29"]
    _clause_need(len(captures) == 2 and [r.get("player") for r in captures] == [reject, partner]
                 and all(r.get("key") == decoded[r["player"]]["key"] for r in captures), "rejection not the later server capture")
    explanation = "Type clause: shared " + ", ".join(shared) if kind == "type" else "Gender clause: both are " + ("♀" if own["gender"] == "female" else "♂")
    _clause_need(any(row.get("type") == "violation" and row.get("player") == reject and explanation in row.get("text", "") for row in events), "server lacks source-derived clause violation")
    text, key = results[reject], own["key"]
    commands = (f"RX force_faint key={key}", f"RX memorialize key={key}", "RX play_sound sound=26", "RX unresolve_area area_id=route_29")
    _clause_need(all(any(line == cmd or line.startswith(cmd + " ") for line in text.splitlines()) for cmd in commands), "rejected command set incomplete")
    _clause_need("RX play_sound sound=22" in results[partner].splitlines(), "partner lacks SE_BOO")
    _clause_need(not re.search(r"^RX force_faint(?:\s|$)", results[partner], re.M), "retained counterpart received force_faint")
    _clause_need(any(row.get("cmd") == "gui_prompt" and row.get("text") == "[x] " + explanation for _, row in _tag_rows(text, "RX_TEXT")), "clause prompt differs from source")
    writes = _tag_rows(text, "PARTY_HP_WRITE")
    _clause_need(len(writes) == 1 and not _tag_rows(results[partner], "PARTY_HP_WRITE"), "exactly one rejected-mon write required")
    _, write = writes[0]
    _clause_need(text.index("CAPTURE_SENT ") < text.index(commands[0]) < text.index("PARTY_HP_WRITE "),
                 "rejection write preceded its capture/force_faint command")
    layout = own["layout"]
    n = layout.party_size * layout.constants["PARTY_LENGTH"]
    before, after = _hex_bytes(write.get("before_party_hex"), n, "clause before"), _hex_bytes(write.get("after_party_hex"), n, "clause after")
    count = len(own["party"]) + (own["place"] != "party")
    pre, post = [], []
    for slot in range(count):
        start = slot * layout.party_size
        pre.append(codec.decode_party_mon(before[start:start + layout.party_size], layout, species_marker=before[start]))
        post.append(codec.decode_party_mon(after[start:start + layout.party_size], layout, species_marker=after[start]))
    captures = [row for _, row in _tag_rows(text, "ENGINE_CAPTURE") if row.get("key") == key]
    _faint_write(write, layout, pre, post, key, captured=captures[0] if len(captures) == 1 else None, artifact_kind=(_last_tagged(text, "CLIENT") or {}).get("artifact_kind", "clean"))
    _clause_need(pre[write["slot"]]["hp"] > 0 and _frame(write) < _frame(own["witness"], "save_completed_frame"), "rejection did not faint a live mon before save")
    by_key = {codec.key(mon): mon for mon in post}
    _clause_need(len(by_key) == len(post) and set(by_key) == {codec.key(mon) for mon in own["party"]} | {key}, "write party differs from saved inventory")
    for mon in own["party"]:
        _clause_need(by_key[codec.key(mon)]["raw_hex"] == mon["raw_hex"], "saved party differs from checkpoint postimage")
    ack, rejected = _one_marker(text, "MEMORIAL_ACK"), _one_marker(text, "REJECTED_MON")
    ending = rejected.get("ending")
    _clause_need(ack.get("key") == rejected.get("key") == key and own["receipt"].get("ending") == ending
                 and _frame(rejected) >= max(_frame(write), _frame(ack))
                 and _frame(rejected) < _frame(own["witness"], "save_completed_frame"), "rejection ending/chronology differs")
    if ending == "dead":
        _clause_need(ack.get("event") == "memorialize_failed" and own["place"] == "party"
                     and own["mon"]["hp"] == 0 and rejected.get("in_party") is True and rejected.get("hp") == 0, "failed memorial must persist HP zero in party")
    else:
        box = layout.constants["NUM_BOXES"] - 1
        pre_mon, preimage = _memorial_observation(text, key, layout, own["witness"])
        _clause_need(ending == "memorial" and ack.get("event") == "memorialize_done" and ack.get("box") == box
                     and own["place"] == box and rejected.get("box") == box and rejected.get("in_party") is False
                     and preimage["slot"] == write["slot"] and pre_mon["raw_hex"] == by_key[key]["raw_hex"]
                     and all(own["mon"][field] == pre_mon[field] for field in ("species_marker", "ot_raw_hex", "nickname_raw_hex"))
                     and own["mon"]["raw_hex"] == _deposited_record(pre_mon["raw_hex"], layout).hex(),
                     "memorial not independently saved in final box")
    _clause_need(key not in document["pending_memorials"].get(reject, []), "memorial acknowledgement not settled")
    return {**_verified_facts(decoded, "route_29", "clause_observed"), "clause": kind, "rejected": reject, "ending": ending}


def _species_clause(results, decoded, document, events, pending_snapshot):
    a, b = decoded["a"], decoded["b"]
    families = b["packs"]["evolutions"]["family"]
    _clause_need(families[str(a["species"])] != families[str(b["species"])], "linked captures share evolution family")
    _clause_need(isinstance(pending_snapshot, dict), "species lane needs pre-release pending snapshot")
    pending = pending_snapshot["links"]["pending_captures"]
    _clause_need(pending_snapshot["links"]["links"] == [] and set(pending) == {"route_29"}
                 and set(pending["route_29"]) == {"a"} and pending["route_29"]["a"].get("key") == a["key"]
                 and pending["route_29"]["a"].get("species") == a["species"], "A was not uniquely pending before B release")
    baseline_events = pending_snapshot["events"]
    _clause_need(any(row.get("type") == "capture" and row.get("player") == "a" and row.get("key") == a["key"] for row in baseline_events)
                 and not any(row.get("type") in ("capture", "reroll", "linked") and row.get("player") == "b" for row in baseline_events),
                 "pending event baseline is after B gameplay or before A capture")
    _clause_need(isinstance(events, list) and len(events) >= len(baseline_events)
                 and events[-len(baseline_events):] == baseline_events, "pending baseline not retained in final event history")
    _clause_need(document.get("pending_captures", {}) == {} and document["area_states"].get("route_29") == "linked", "species area not settled linked")
    for inst, row in decoded.items():
        text = results[inst]
        linked = _one_marker(text, "LINKED")
        _clause_need(isinstance(linked.get("text"), str) and linked["text"].endswith(" linked!")
                     and _frame(linked) < _frame(row["witness"], "save_completed_frame"), "link not followed by native save")
        _clause_need(not re.search(r"^RX force_faint(?:\s|$)", text, re.M), "species catch was rejected")
        for _, message in _tag_rows(text, "RX_TEXT"):
            _clause_need(" is a dead zone!" not in message.get("text", "")
                         and not message.get("text", "").startswith(("[x] Species clause", "[x] Dup ")), "species rejection/dead zone")
    mark = _one_marker(results["a"], "PENDING_CAPTURE")
    _clause_need(mark.get("key") == a["key"] and mark.get("species_id") == a["species"] and mark.get("area_id") == "route_29"
                 and _frame(mark) < _frame(_one_marker(results["a"], "LINKED")), "pending marker differs")
    text = results["b"]
    ap = _one_marker(text, "A_PENDING")
    _clause_need(ap.get("species_id") == a["species"], "B released for wrong pending species")
    encounters, rerolls = _tag_rows(text, "ENCOUNTER"), _tag_rows(text, "REROLL")
    _clause_need(1 <= len(encounters) <= 8 and len(rerolls) == len(encounters) - 1, "species encounter/reroll budget differs")
    prompt = "Dupes clause: " + b["packs"]["species_index"]["species"][str(a["species"])]["name"].title() + " -- reroll!"
    for index, (position, encounter) in enumerate(encounters, 1):
        last = index == len(encounters)
        species = encounter.get("species_id")
        dupe = families[str(species)] == families[str(a["species"])]
        _clause_need(encounter.get("n") == index and encounter.get("dupe") is dupe and dupe is not last
                     and _frame(encounter) >= _frame(ap), "species encounter family/order differs")
        if last:
            cap = _one_marker(text, "ENGINE_CAPTURE")
            _clause_need(species == b["species"] and _frame(encounter) <= _frame(cap), "catch not final nonduplicate encounter")
        else:
            rpos, reroll = rerolls[index - 1]
            _clause_need(reroll.get("n") == index and reroll.get("species_id") == species and reroll.get("prompt") == prompt
                         and position < rpos < encounters[index][0] and _frame(encounter) <= _frame(reroll), "reroll order/prompt differs")
            # The first in-battle tick may prompt during intro text, before ENCOUNTER.
            # Bound each prompt to this hunt window so another battle cannot supply it.
            previous = _tag_rows(text, "A_PENDING")[0][0] if index == 1 else rerolls[index - 2][0]
            _clause_need(any(previous < pos < rpos and msg.get("cmd") == "gui_prompt" and msg.get("text") == prompt
                             for pos, msg in _tag_rows(text, "RX_TEXT")), "reroll lacks observed prompt")
    server_rerolls = [row for row in events if row.get("type") == "reroll"]
    _clause_need(len(server_rerolls) == len(rerolls) and all(row.get("player") == "b" and row.get("area_id") == "route_29"
                 and row.get("text") == "🔁 " + prompt for row in server_rerolls), "server rerolls differ from observed encounters")
    ordered = list(reversed(events))
    b_captures = [index for index, row in enumerate(ordered) if row.get("type") == "capture" and row.get("player") == "b" and row.get("key") == b["key"]]
    _clause_need(len(b_captures) == 1 and all(index < b_captures[0] for index, row in enumerate(ordered) if row.get("type") == "reroll"), "reroll after B capture")
    _clause_need(not any(row.get("type") in ("dead_zone", "violation") for row in events), "species server rejected catch")
    for inst, role in (("a", "pending"), ("b", "reroller")):
        receipt = decoded[inst]["receipt"]
        _clause_need(receipt.get("role") == role and receipt.get("species_id") == decoded[inst]["species"], "species receipt role/catch differs")
    receipt = b["receipt"]
    _clause_need(receipt.get("dupe_species") == a["species"] and receipt.get("rerolls") == len(rerolls)
                 and receipt.get("path") == ("reroll_observed" if rerolls else "reroll_unobserved"), "species receipt path/count differs")
    if not rerolls:
        return None
    return {**_verified_facts(decoded, "route_29", "alive"), "clause": "species", "rerolls": len(rerolls)}


def clause_oracle(results, *, kind, data_dir, boot_saveram, pending_snapshot=None, on_verified=None):
    """Source-derived clause predicates plus independent saved inventory and server outcomes."""
    try:
        _clause_need(kind in ("type", "gender", "species"), "unknown clause kind")
        decoded = _clause_captures(results, boot_saveram, kind)
        document = json.loads((Path(data_dir) / "links.json").read_text(encoding="utf-8"))
        events = json.loads((Path(data_dir) / "events.json").read_text(encoding="utf-8"))
        if kind == "species":
            facts = _species_clause(results, decoded, document, events, pending_snapshot)
            _pair_oracle(results, data_dir=data_dir, boot_saveram=boot_saveram)
        else:
            facts = _clause_rejection(results, decoded, document, events, kind)
        if facts is None:
            _pair_oracle(results, data_dir=data_dir, boot_saveram=boot_saveram)
            _clause_need(all(row["place"] == "party" and row["mon"]["hp"] > 0 for row in decoded.values()), "clean linked captures not alive")
            raise ClauseUnobserved(f"{kind} clause unobserved: independently valid alive pair; retry needed")
        if on_verified is not None:
            on_verified(facts)
    except (KeyError, TypeError, ValueError, OSError, IndexError, AttributeError) as exc:
        raise RuntimeError(f"clause evidence missing or malformed: {exc}") from exc


# --- DUO-WAVE-C: whiteout, pc_ops, changebox (roadmap rows 9-11) ------------------------------------
#
# Each oracle re-derives its claim from the immutable LINK_SAVE images, the final flushed saves (PYDEC via
# gen2_codec, each side with its own title's layout), the server's links.json and server.log. The marker
# verdicts of lua/tests/duo/scenario_gen2_{whiteout,pc_ops,changebox}.lua are never trusted alone.

def _wave_need(condition, reason):
    if not condition:
        raise RuntimeError(f"wave-c: {reason}")


def _wave_head(results, inst, scenario, schema):
    """Sole PASS, production client, scenario/player header, receipt schema -> (witness, layout, title)."""
    from server.adapters import gen2_codec as codec

    text = results[inst]
    _reconnect_pass(text)
    witness, client, title = _boot_marker(inst, text)
    head, receipt = _one_marker(text, "DUO_GEN2"), _one_marker(text, "RECEIPT")
    _wave_need(client.get("production_admitted") is True and head.get("player") == inst
               and head.get("scenario") == scenario and receipt.get("schema") == schema,
               f"{inst}: production/header/receipt schema differs")
    layout = codec.for_foundation(title)
    _wave_need(client.get("rom_sha1") == _executed_identity(title, client.get("artifact_kind", "clean"))[0], f"{inst}: wrong ROM pin")
    return witness, layout, title


def _wave_stage(text, inst, layout, witness):
    """LINK_SAVE: an immutable checksum-valid image, older than the final witnessed save -> (stage, raw)."""
    from server.adapters import gen2_codec as codec

    stage = _one_marker(text, "LINK_SAVE")
    raw = Path(stage["saveram_path"]).read_bytes()
    _wave_need(stage.get("saveram_bytes") == len(raw) == SAVERAM_BYTES and stage.get("cartram_bytes") == CARTRAM_BYTES
               and hashlib.sha256(raw[:CARTRAM_BYTES]).hexdigest() == stage.get("cartram_sha256"),
               f"{inst}: LINK_SAVE size/hash differs")
    _wave_need(codec.strict_checksum_witness(raw[:CARTRAM_BYTES], layout)["valid"], f"{inst}: LINK_SAVE checksum refused")
    _wave_need(type(stage.get("gate_saves")) is int and type(witness.get("gate_saves")) is int
               and witness["gate_saves"] > stage["gate_saves"] >= 1
               and _frame(stage, "save_completed_frame") < _frame(witness, "save_completed_frame"),
               f"{inst}: the final save is not newer than LINK_SAVE")
    return stage, raw


def _wave_lines(text, prefix):
    return [(at, line) for at, line in enumerate(text.splitlines()) if line.startswith(prefix)]


def _wave_after(rows, at):
    return [row for row in rows if row[0] > at]


def _wave_saved_current_box(raw, layout):
    from tools.gen2_fixtures import _saved_field

    return _saved_field(raw[:CARTRAM_BYTES], layout, "wCurBox", 1)[0]


def _wave_b_bench(results, decoded, layout, stage_raw, witness, stages):
    """B's half of a propagated death (as faint_oracle): memorial of the zeroed record, only HP/status changed."""
    from server.adapters import gen2_codec as codec

    key = decoded["b"]["key"]
    linked = codec.decode_saved_party(stage_raw[:CARTRAM_BYTES], layout, copy_name="primary")["mons"]
    observed, _ = _memorial_party(results["b"], key, layout, stages["b"]["saveram_path"], witness)
    _wave_need([codec.key(m) for m in linked] == [codec.key(m) for m in observed], "B party order/identity changed")
    for before, after in zip(linked, observed, strict=True):
        expected = bytearray.fromhex(before["raw_hex"])
        if codec.key(before) == key:
            expected[layout.constants["MON_STATUS"]] = 0
            at = layout.constants["MON_HP"]
            expected[at:at + 2] = bytes(2)
        _wave_need(after["raw_hex"] == expected.hex()
                   and all(after[f] == before[f] for f in ("ot_raw_hex", "nickname_raw_hex", "species_marker")),
                   "B other mons or target non-HP/status bytes changed")
    _bench_faint_writes(results, {"b": (layout, linked, observed, witness)}, stages, key)


def _wave_memorial_settled(log, since, decoded, document, area_id):
    end = log.find(f"pair in {area_id} fully memorialized", since)
    _wave_need(end >= 0, "server lacks the memorial transition after the death")
    pending = document.get("pending_memorials")
    for inst in ("a", "b"):
        key = decoded[inst]["key"]
        ack = re.search(rf"\[{inst}\] memorialize_done key=" + re.escape(key[:8]) + r"(?:\s|$)", log[since:end])
        _wave_need(ack and isinstance(pending, dict) and isinstance(pending.get(inst), list) and key not in pending[inst],
                   f"server lacks a settled {inst} memorial")


def _whiteout_oracle(results, *, data_dir, area_id, ot_ids, boot_saveram):
    from server.adapters import gen2_codec as codec

    check_save_witness(results)
    heads, stages, raws = {}, {}, {}
    for inst in ("a", "b"):
        heads[inst] = _wave_head(results, inst, "gen2_whiteout", "gen2-duo-whiteout-v1")
        stages[inst], raws[inst] = _wave_stage(results[inst], inst, heads[inst][1], heads[inst][0])
    decoded, row, document = _pair_oracle(results, data_dir=data_dir, area_id=area_id, ot_ids=ot_ids,
                                          boot_saveram=boot_saveram, status="memorial",
                                          snapshots={inst: stages[inst]["saveram_path"] for inst in ("a", "b")})
    _wave_need(row.get("cause") == "battle" and row.get("initiating_player") == "a" and row.get("killed_at"),
               "server death is not A's battle faint")
    a_key, b_key = decoded["a"]["key"], decoded["b"]["key"]
    a, (witness, layout, _title) = results["a"], heads["a"]
    # A: the starter's engine faint, then the linked key's; the pre-heal whiteout party all at HP 0; the heal revives
    faints = _tag_rows(a, "ENGINE_FAINT")
    linked = codec.decode_saved_party(raws["a"][:CARTRAM_BYTES], layout, copy_name="primary")["mons"]
    linked_keys = [codec.key(m) for m in linked]
    _wave_need(len(linked) == 2 and a_key in linked_keys, "A's linked party is not [starter, linked catch]")
    starter = next(k for k in linked_keys if k != a_key)
    _wave_need([f.get("key") for _, f in faints] == [starter, a_key]
               and all(f.get("site_id") == "battle_faint" and f.get("cause") == "battle" for _, f in faints),
               "A's engine faints are not the starter's, then the linked key's")
    whiteout, revived = _one_marker(a, "ENGINE_WHITEOUT"), _one_marker(a, "REVIVED")
    party = whiteout.get("party")
    _wave_need(whiteout.get("site_id") == "whiteout_before_heal" and isinstance(party, list)
               and sorted(m.get("key") for m in party) == sorted(linked_keys) and all(m.get("hp") == 0 for m in party),
               "the pre-heal whiteout party is not the linked party at HP 0")
    _wave_need(revived.get("key") == a_key and type(revived.get("hp")) is int and revived["hp"] > 0
               and _frame(faints[1][1]) <= _frame(whiteout) <= _frame(revived), "no revived linked mon after the whiteout")
    whiteouts = [line for _, line in _wave_lines(a, "TX ") if '"event":"whiteout"' in line]
    _wave_need(len(whiteouts) == 1 and not any('"event":"whiteout"' in line for _, line in _wave_lines(results["b"], "TX ")),
               "exactly one whiteout event, from A only")
    # A's final save: the dead key only in Box 14 (the native deposit of the observed record); the starter healed
    saved = Path(witness["saveram_path"]).read_bytes()
    final, inventory = _clause_inventory(saved, layout)
    _, original = _clause_inventory(raws["a"], layout)
    _wave_need(set(inventory) == set(original), "A's saved inventory changed identity")
    box_number = layout.constants["NUM_BOXES"] - 1
    _wave_need(inventory[a_key][0] == box_number and [codec.key(m) for m in final] == [starter]
               and final[0]["hp"] == final[0]["max_hp"] > 0 and final[0]["status"] == 0,
               "A's save is not the healed starter with the dead key in Box 14")
    preimage, ack = _one_marker(a, "MEMORIAL_PREIMAGE"), _one_marker(a, "MEMORIAL_ACK")
    raw = _hex_bytes(preimage.get("raw_hex"), layout.party_size, "memorial preimage")
    ot = _hex_bytes(preimage.get("ot_raw_hex"), layout.name_size, "memorial OT")
    nickname = _hex_bytes(preimage.get("nickname_raw_hex"), layout.nickname_size, "memorial nickname")
    mon = codec.decode_party_mon(raw, layout, species_marker=preimage.get("species_marker"), ot=ot, nickname=nickname)
    boxed = inventory[a_key][1]
    _wave_need(codec.key(mon) == preimage.get("key") == a_key and ack.get("event") == "memorialize_done"
               and ack.get("key") == a_key and ack.get("box") == box_number
               and boxed["raw_hex"] == _deposited_record(mon["raw_hex"], layout).hex()
               and boxed["ot_raw_hex"] == mon["ot_raw_hex"] and boxed["nickname_raw_hex"] == mon["nickname_raw_hex"],
               "A's Box 14 record is not the native deposit of the observed dead mon")
    # The revived record itself was buried: no write zeroed it first (run_over suppresses O-24, owner ruling
    # 2026-09-24; state.py _repair_lost_faints returns while run_over), so the preimage carries the healed HP.
    _wave_need(mon["hp"] == revived["hp"] > 0 and _frame(revived) <= _frame(preimage),
               "A's memorial is not the revived record")
    _wave_need(not _tag_rows(a, "PARTY_HP_WRITE"), "A's party was written")
    # B: the propagated bench death, exactly as faint_oracle
    _wave_b_bench(results, decoded, heads["b"][1], raws["b"], heads["b"][0], stages)
    # server: A's faint -> force_faint to B; the only pair dead -> GAME OVER, game_over to both; no re-issue
    log = (Path(data_dir) / "server.log").read_text(encoding="utf-8", errors="replace")
    issued = re.search(r"\[a\] faint → force_faint b:" + re.escape(b_key) + r"(?:\s|$)", log)
    _wave_need(issued, "server did not issue force_faint to B")
    _wave_need("GAME OVER" in log[issued.start():] and document.get("run_over") is True, "the server did not end the run")
    _wave_need(not re.search(re.escape(a_key) + r" is dead but alive in party", log),
               "the server re-issued force_faint under run_over")
    for inst in ("a", "b"):
        lines = results[inst].splitlines()
        faint_line = next(i for i, l in enumerate(lines) if l.startswith("ENGINE_FAINT ") and a_key in l) if inst == "a" else             next(i for i, l in enumerate(lines) if l == f"RX force_faint key={b_key}")
        _wave_need(any(l == "RX game_over" for l in lines[faint_line:]), f"{inst} never received game_over after the death")
    _wave_need(f"RX force_faint key={a_key}" not in a.splitlines(), "A received force_faint for its own key")
    _wave_memorial_settled(log, issued.end(), decoded, document, area_id)
    return {**_verified_facts(decoded, area_id, "memorial"), "repair": "run_over"}


def whiteout_oracle(results, *, data_dir, area_id="route_29", ot_ids=None, boot_saveram=None, on_verified=None):
    """W-7: the whiteout heal revives the dead linked mon and the memorial buries the revived record.

    O-24 is not exercised: the only pair's death ends the run and run_over suppresses _repair_lost_faints
    (owner ruling 2026-09-24); tests/unit/test_state_faint_repair.py covers O-24. facts["repair"] == "run_over"."""
    try:
        facts = _whiteout_oracle(results, data_dir=data_dir, area_id=area_id, ot_ids=ot_ids, boot_saveram=boot_saveram)
        if on_verified is not None:
            on_verified(facts)
    except (KeyError, TypeError, ValueError, OSError, IndexError, AttributeError, StopIteration) as exc:
        raise RuntimeError(f"whiteout evidence missing or malformed: {exc}") from exc


def _pc_ops_oracle(results, *, data_dir, area_id, ot_ids, boot_saveram):
    from server.adapters import gen2_codec as codec

    check_save_witness(results)
    heads, stages, raws = {}, {}, {}
    for inst in ("a", "b"):
        heads[inst] = _wave_head(results, inst, "gen2_pc_ops", "gen2-duo-pc-ops-v2")
        stages[inst], raws[inst] = _wave_stage(results[inst], inst, heads[inst][1], heads[inst][0])
    decoded, row, document = _pair_oracle(results, data_dir=data_dir, area_id=area_id, ot_ids=ot_ids,
                                          boot_saveram=boot_saveram, status=("dead", "memorial"),
                                           snapshots={inst: stages[inst]["saveram_path"] for inst in ("a", "b")})
    keys = {inst: decoded[inst]["key"] for inst in ("a", "b")}
    link_at = {inst: _wave_lines(results[inst], "LINK_SAVE ")[0][0] for inst in ("a", "b")}
    # A: the binder's PC events and the wire, in order, all for the linked key; the box release sends release{key}
    # (owner ruling O-35, client 149b38e2)
    a = results["a"]
    events = [(at, row_) for at, row_ in _tag_rows(a, "ENGINE_PC") if at > link_at["a"]]
    _wave_need([(e.get("kind"), e.get("collection"), e.get("key")) for _, e in events]
               == [("party_to_box", None, keys["a"]), ("box_to_party", None, keys["a"]),
                   ("party_to_box", None, keys["a"]), ("pc_release", "box", keys["a"])],
               "A's engine PC events are not deposit, withdraw, deposit, box release of the linked key")
    # TX lines are clipped at 220 chars by the driver: read the two fields, never the whole object
    sends = [(at, {"event": (re.search(r'"event":"(\w+)"', line) or [None, None])[1],
                   "key": (re.search(r'"key":"([^"]+)"', line) or [None, None])[1]})
             for at, line in _wave_after(_wave_lines(a, "TX "), link_at["a"])]
    storage = [(at, m) for at, m in sends if m.get("event") in ("party_to_box", "box_to_party", "release")]
    _wave_need([(m["event"], m.get("key")) for _, m in storage]
               == [("party_to_box", keys["a"]), ("box_to_party", keys["a"]), ("party_to_box", keys["a"]), ("release", keys["a"])]
               and all(storage[i][0] > events[i][0] for i in range(4)) and events[3][0] > storage[2][0],
               "A's sends are not the three transfers and the release after their engine events")
    # B: box_mon, party_mon, box_mon for its own linked key, each physically run, and nothing of its own on the wire
    b = results["b"]
    rx = [line for _, line in _wave_after(_wave_lines(b, "RX "), link_at["b"])
          if line.split(" ")[1] in ("box_mon", "party_mon", "force_faint", "memorialize")]
    # a sync command re-issued after its in-flight window arrives twice in a row and is absorbed by the client
    # (state.py SYNC_INFLIGHT_RECONCILES; live G-S run: RX party_mon twice): consecutive repeats collapse
    rx = [line for i, line in enumerate(rx) if i == 0 or line != rx[i - 1]]
    _wave_need(rx == [f"RX {cmd} key={keys['b']}" for cmd in ("box_mon", "party_mon", "box_mon", "force_faint", "memorialize")],
               "B's commands are not box_mon, party_mon, box_mon, force_faint, memorialize for its key")
    _wave_need(not any(l.split(" ")[1] in ("force_faint", "memorialize") for _, l in _wave_after(_wave_lines(a, "RX "), link_at["a"])),
               "A received a death command for its released half")
    _wave_need(not any('"event":"party_to_box"' in l or '"event":"box_to_party"' in l for _, l in _wave_lines(b, "TX ")),
               "B sent a storage event of its own")
    # the saves: A's catch released (in no party or box), B's in the memorial box; every other mon where the link left it
    finals = {}
    for inst in ("a", "b"):
        layout, witness = heads[inst][1], heads[inst][0]
        final, inventory = _clause_inventory(Path(witness["saveram_path"]).read_bytes(), layout)
        _, original = _clause_inventory(raws[inst], layout)
        others = set(original) - {keys[inst]}
        _wave_need(others <= set(inventory) and all(inventory[k][0] == original[k][0] for k in others),
                   f"{inst}: an unrelated mon moved")
        finals[inst] = (final, inventory)
    _wave_need(keys["a"] not in finals["a"][1] and set(finals["a"][1]) == set(_clause_inventory(raws["a"], heads["a"][1])[1]) - {keys["a"]},
               "A's released key is still in its save")
    place = finals["b"][1].get(keys["b"], (None,))[0]
    _wave_need(place == heads["b"][1].constants["NUM_BOXES"] - 1, "B's released partner is not in the memorial box (Box 14)")
    # the server: two mirrored deposits, one withdrawal, then A's release kills the pair (O-35, cause "release")
    log = (Path(data_dir) / "server.log").read_text(encoding="utf-8", errors="replace")
    box_mon = re.findall(r"\[a\] party_to_box " + re.escape(keys["a"][:8]) + r" → box_mon b:" + re.escape(keys["b"][:8]), log)
    party_mon = re.findall(r"\[a\] box_to_party " + re.escape(keys["a"][:8]) + r" → party_mon b:" + re.escape(keys["b"][:8]), log)
    _wave_need(len(box_mon) == 2 and len(party_mon) == 1, "server did not mirror two deposits and one withdrawal")
    released = re.findall(r"\[a\] released linked " + re.escape(keys["a"][:8]) + r" — the partner dies", log)
    _wave_need(len(released) == 1, "server log lacks exactly one release of A's linked key")
    _wave_need(row.get("status") in ("dead", "memorial") and row.get("killed_at") and row.get("cause") == "release"
               and row.get("initiating_player") == "a", "the server did not kill the pair for A's release")
    _wave_need(keys["a"] not in (document.get("pending_memorials") or {}).get("a", []), "A's released half awaits a memorial")
    return {**_verified_facts(decoded, area_id, row["status"]), "release": "propagated"}


def pc_ops_oracle(results, *, data_dir, area_id="route_29", ot_ids=None, boot_saveram=None, on_verified=None):
    """S-6 by play: deposit/withdraw/deposit mirrored to B physically; A's box release kills the pair and B's
    boxed partner is memorialized into Box 14 (owner ruling O-35). facts["release"] == "propagated"."""
    try:
        facts = _pc_ops_oracle(results, data_dir=data_dir, area_id=area_id, ot_ids=ot_ids, boot_saveram=boot_saveram)
        if on_verified is not None:
            on_verified(facts)
    except (KeyError, TypeError, ValueError, OSError, IndexError, AttributeError, StopIteration) as exc:
        raise RuntimeError(f"pc_ops evidence missing or malformed: {exc}") from exc


def changebox_oracle(results, *, data_dir, area_id="route_29", ot_ids=None, boot_saveram=None, on_verified=None):
    """W-5: the gen2_faint proof, then B's BOX14 round trip keeps the memorial and ends on BOX1."""
    try:
        facts = _faint_oracle(results, data_dir=data_dir, area_id=area_id, ot_ids=ot_ids, boot_saveram=boot_saveram)
        _wave_need(facts["status"] == "memorial", "changebox needs the memorial pair")
        b = results["b"]
        for inst in ("a", "b"):
            _wave_head(results, inst, "gen2_changebox", "gen2-duo-changebox-v1")
        changes = [(at, c) for at, c in _tag_rows(b, "ENGINE_PC")]
        _wave_need([(c.get("kind"), c.get("site_id"), c.get("old_box"), c.get("new_box")) for _, c in changes]
                   == [("box_change", "change_box_loaded", 0, 13), ("box_change", "change_box_loaded", 13, 0)],
                   "B's box changes are not BOX1 -> BOX14 -> BOX1")
        to, back = _one_marker(b, "CHANGEBOX_TO"), _one_marker(b, "CHANGEBOX_BACK")
        _wave_need(to.get("cur_box") == 13 and type(to.get("box_count")) is int and to["box_count"] >= 1
                   and back.get("cur_box") == 0, "BOX14 did not list the memorial or B did not return to BOX1")
        _wave_need(not any('"event":"party_to_box"' in l or '"event":"box_to_party"' in l for _, l in _wave_lines(b, "TX ")),
                   "B sent a storage event; a box change is not a transfer")
        _wave_need(not _tag_rows(results["a"], "ENGINE_PC"), "A changed its PC state")
        from server.adapters import gen2_codec as codec

        witness, _client, title = _boot_marker("b", b)
        layout = codec.for_foundation(title)
        saved = Path(witness["saveram_path"]).read_bytes()
        _wave_need(_wave_saved_current_box(saved, layout) == 0, "B's saved current box is not BOX1")
        _, inventory = _clause_inventory(saved, layout)
        _wave_need(inventory.get(facts["b"], (None,))[0] == layout.constants["NUM_BOXES"] - 1,
                   "B's memorial left Box 14 across the box change")
        facts = {**facts, "box_change": "BOX1->BOX14->BOX1"}
        if on_verified is not None:
            on_verified(facts)
    except (KeyError, TypeError, ValueError, OSError, IndexError, AttributeError, StopIteration) as exc:
        raise RuntimeError(f"changebox evidence missing or malformed: {exc}") from exc


def poison_oracle(results, *, data_dir, area_id="route_29", ot_ids=None, boot_saveram=None, on_verified=None):
    """S-4 poison half / D-7: A's linked mon faints to overworld poison (poison_faint), B's partner is force-fainted.

    The faint_oracle proof with A's engine faint bound to poison_faint/poison. The server records the death as
    cause "battle": the wire `faint` carries no cause (state.py _handle_faint -> _propagate_faint default)."""
    try:
        for inst in ("a", "b"):
            _wave_head(results, inst, "gen2_poison", "gen2-duo-poison-v1")
        facts = _faint_oracle(results, data_dir=data_dir, area_id=area_id, ot_ids=ot_ids, boot_saveram=boot_saveram,
                              engine=("poison_faint", "poison"))
        facts = {**facts, "death": "poison"}
        if on_verified is not None:
            on_verified(facts)
    except (KeyError, TypeError, ValueError, OSError, IndexError, AttributeError, StopIteration) as exc:
        raise RuntimeError(f"poison evidence missing or malformed: {exc}") from exc


def _whiteout_rebuild_oracle(results, *, data_dir, area_id, ot_ids, boot_saveram):
    from server.adapters import gen2_codec as codec

    check_save_witness(results)
    heads, stages, raws = {}, {}, {}
    for inst in ("a", "b"):
        heads[inst] = _wave_head(results, inst, "gen2_whiteout_rebuild", "gen2-duo-whiteout-rebuild-v1")
        stages[inst], raws[inst] = _wave_stage(results[inst], inst, heads[inst][1], heads[inst][0])
    decoded, row, document = _pair_oracle(results, data_dir=data_dir, area_id=area_id, ot_ids=ot_ids,
                                          boot_saveram=boot_saveram, status="alive",
                                          snapshots={inst: stages[inst]["saveram_path"] for inst in ("a", "b")})
    _wave_need(not row.get("killed_at") and document.get("run_over") is not True, "the pair died or the run ended")
    # both saves: the linked party order restored, the linked key in no box, nothing else moved. HP: A whited out, so
    # HealParty leaves every mon at full HP; B never whites out, so only its rebuilt linked mon is healed (the
    # withdraw's CalcMonStats) and every other B mon keeps its LINK_SAVE HP/status (a starter hit in B's own capture
    # battle stays hit: the df04e065 sweep's G-S cell, A4FC 17/20 at LINK_SAVE and in the final save)
    for inst in ("a", "b"):
        layout, witness = heads[inst][1], heads[inst][0]
        linked = codec.decode_saved_party(raws[inst][:CARTRAM_BYTES], layout, copy_name="primary")["mons"]
        final, inventory = _clause_inventory(Path(witness["saveram_path"]).read_bytes(), layout)
        _, original = _clause_inventory(raws[inst], layout)
        _wave_need([codec.key(m) for m in final] == [codec.key(m) for m in linked] and set(inventory) == set(original),
                   f"{inst}: the rebuilt party/inventory differs from the linked save")
        at_link = {codec.key(m): (m["hp"], m["status"]) for m in linked}
        rebuilt = decoded[inst]["key"]
        _wave_need(rebuilt in at_link, f"{inst}: the rebuilt key is not in the LINK_SAVE party")
        _wave_need(all(m["hp"] == m["max_hp"] > 0 and m["status"] == 0 for m in final
                       if inst == "a" or codec.key(m) == rebuilt), f"{inst}: a rebuilt mon is not at full HP")
        _wave_need(inst == "a" or all((m["hp"], m["status"]) == at_link[codec.key(m)] for m in final
                                      if codec.key(m) != rebuilt), "b: an unlinked mon's HP/status changed since LINK_SAVE")
    a, b = results["a"], results["b"]
    a_key, b_key = decoded["a"]["key"], decoded["b"]["key"]
    starter = next(codec.key(m) for m in codec.decode_saved_party(raws["a"][:CARTRAM_BYTES], heads["a"][1],
                                                                   copy_name="primary")["mons"] if codec.key(m) != a_key)
    whiteout = _one_marker(a, "ENGINE_WHITEOUT")
    _wave_need(whiteout.get("site_id") == "whiteout_before_heal"
               and [(m.get("key"), m.get("hp")) for m in whiteout.get("party") or []] == [(starter, 0)],
               "the pre-heal whiteout party is not the starter alone at HP 0")
    _wave_need(len([l for _, l in _wave_lines(a, "TX ") if '"event":"whiteout"' in l]) == 1
               and not any('"event":"whiteout"' in l for _, l in _wave_lines(b, "TX ")), "exactly one whiteout event, from A only")
    lines_a = a.splitlines()
    at = next(i for i, l in enumerate(lines_a) if l.startswith("ENGINE_WHITEOUT "))
    _wave_need([l for l in lines_a[at:] if l in ("RX rebuild_start", "RX rebuild_done")] == ["RX rebuild_start", "RX rebuild_done"],
               "A was not told rebuild_start then rebuild_done once each")
    for inst, key in (("a", a_key), ("b", b_key)):
        text = results[inst].splitlines()
        link_at = next(i for i, l in enumerate(text) if l.startswith("LINK_SAVE "))
        _wave_need(not any(l.startswith(("RX force_faint", "RX memorialize", "RX game_over")) for l in text[link_at:]),
                   f"{inst} received a death command")
        _wave_need(f"RX party_mon key={key}" in text[link_at:], f"{inst} never received the rebuild party_mon")
    _wave_need("RX rebuild_start" not in b.splitlines(), "B received rebuild_start")
    log = (Path(data_dir) / "server.log").read_text(encoding="utf-8", errors="replace")
    mirror = re.search(r"\[a\] party_to_box " + re.escape(a_key[:8]) + r" → box_mon b:" + re.escape(b_key[:8]), log)
    armed = re.search(r"\[a\] whiteout rebuild armed — restoring 1 mon\(s\)", log)
    done = re.search(r"\[a\] rebuild complete — 1 restored", log)
    _wave_need(mirror and armed and done and mirror.start() < armed.start() < done.start(),
               "server log lacks the mirrored deposit, the armed rebuild and its completion, in order")
    _wave_need("→ force_faint" not in log and "GAME OVER" not in log, "the server killed something or ended the run")
    return {**_verified_facts(decoded, area_id, "alive"), "rebuild": "restored"}


def whiteout_rebuild_oracle(results, *, data_dir, area_id="route_29", ot_ids=None, boot_saveram=None, on_verified=None):
    """D-7: a whiteout with both linked halves boxed rebuilds the pair from the PCs; no death, the pair stays ALIVE."""
    try:
        facts = _whiteout_rebuild_oracle(results, data_dir=data_dir, area_id=area_id, ot_ids=ot_ids, boot_saveram=boot_saveram)
        if on_verified is not None:
            on_verified(facts)
    except (KeyError, TypeError, ValueError, OSError, IndexError, AttributeError, StopIteration) as exc:
        raise RuntimeError(f"whiteout rebuild evidence missing or malformed: {exc}") from exc


# --- gen2_ball_gate (D-2, card DUO-WAVE-D; docs/gen2/reviews/DUO_WAVE_D_FACTS_2026-09-24.md section 1) -----------

POKE_BALL = 5            # const POKE_BALL ; 05 (C/G constants/item_constants.asm:13)
AIDE_BALLS = 5           # giveitem POKE_BALL, 5 (C maps/ElmsLab.asm:504, G :461)


def _ball_need(condition, reason):
    if not condition:
        raise RuntimeError(f"ball_gate: {reason}")


def _ball_gate_side(inst, text, boot_path, layout):
    """One side's zero-Ball start, pre-Ball encounter/faint and flip, in line order; returns the starter key."""
    from server.adapters import gen2_codec as codec
    from tools.gen2_fixtures import BY_NAME

    duo = _duo_marker(inst, text)
    _ball_need(BY_NAME[duo["case"]].target == "town", f"{inst} booted {duo['case']!r}, not a zero-Ball town fixture")
    boot = Path(boot_path).read_bytes()[:CARTRAM_BYTES]
    _ball_need(_ball_pocket(boot, layout) == [], f"{inst} boot fixture already holds Poke Balls")
    starter = codec.key(codec.decode_saved_party(boot, layout, copy_name="primary")["mons"][0])
    rows = {tag: _tag_rows(text, tag) for tag in ("HELLO", "BALL_PRE", "BALL_FLIP", "ENGINE_FAINT", "FAINT_SENT",
                                                  "ENGINE_CAPTURE")}
    for tag in ("HELLO", "BALL_PRE", "BALL_FLIP"):
        _ball_need(len(rows[tag]) >= 1 and (tag == "HELLO" or len(rows[tag]) == 1), f"{inst} needs one {tag}")
    (_, hello), (pre_at, pre), (flip_at, flip) = rows["HELLO"][0], rows["BALL_PRE"][0], rows["BALL_FLIP"][0]
    _ball_need(hello.get("has_pokeballs") is False and hello.get("ball_count") == 0,
               f"{inst} first hello already had Poke Balls")
    _ball_need(pre.get("has_pokeballs") is False and pre.get("ball_count") == 0 and pre.get("area_id") == "route_29",
               f"{inst} pre-Ball encounter is not a zero-Ball route_29 battle")
    _ball_need(flip.get("has_pokeballs") is True and 1 <= (flip.get("ball_count") or 0) <= AIDE_BALLS
               and pre_at < flip_at, f"{inst} Ball flip is not the aide's natural stack after the encounter")
    captures = [at for at, _ in rows["ENGINE_CAPTURE"]]
    _ball_need(captures and min(captures) > flip_at, f"{inst} caught before the Ball flip")
    faints = [at for at, row in rows["ENGINE_FAINT"] if row.get("site_id") == "battle_faint" and at < flip_at]
    _ball_need(faints and all(row.get("key") == starter for at, row in rows["ENGINE_FAINT"] if at < flip_at),
               f"{inst} no pre-Ball battle_faint of the starter {starter}")
    _ball_need(any(row.get("key") == starter and min(faints) < at < flip_at for at, row in rows["FAINT_SENT"]),
               f"{inst} the pre-Ball starter faint was never sent")
    for at, line in enumerate(text.splitlines()):
        if line.startswith("TX ") and at < flip_at:
            event = re.search(r'"event"\s*:\s*"(\w+)"', line)
            _ball_need(not event or event.group(1) not in ("no_catch", "capture"),
                       f"{inst} sent {event and event.group(1)} before the first Ball")
        if line.startswith(("RX force_faint", "RX memorialize")):
            _ball_need(False, f"{inst} received a death command: {line}")
    return starter


def ball_gate_oracle(results, *, data_dir, area_id="route_29", ot_ids=None, boot_saveram=None, on_verified=None):
    """D-2: zero-Ball starts, a pre-Ball encounter that resolves nothing and a pre-Ball faint the server suppresses,
    the aide's natural Balls opening the server gate, then one alive route_29 pair from the first post-Ball catches.
    Every fact is re-read from the flushed saves, the boot fixtures, links.json and slink.log; the driver's marker
    lines only locate them. No SYNTH field (O-33) is involved."""
    from server.adapters import gen2_codec as codec

    boot_saveram = dict(boot_saveram) if boot_saveram else {}
    starters = {}
    for inst in ("a", "b"):
        text = (results or {}).get(inst) or ""
        _witness, _client, title = _boot_marker(inst, text)
        duo = _duo_marker(inst, text)
        boot = Path(boot_saveram[inst]) if inst in boot_saveram else _fixture_path(duo["case"])
        starters[inst] = _ball_gate_side(inst, text, boot if boot.is_absolute() else REPO_ROOT / boot,
                                         codec.for_foundation(title))

    def natural(inst, before, after):
        # A zero-quantity bag slot is removed (pack.asm), so an empty `after` is what "every one of the
        # aide's AIDE_BALLS Balls got thrown" looks like -- the Gen 2 shake-check catch algorithm can
        # legitimately fail AIDE_BALLS - 1 times before succeeding on the last Ball (fsw-postrc 2026-09-25,
        # duo_ball_gate_cc_b: a level-2 full-HP RATTATA, catch rate 255, needed all 5).
        used_some = len(after) == 1 and after[0][0] == POKE_BALL and 1 <= after[0][1] < AIDE_BALLS
        used_all = after == []
        _ball_need(before == [] and (used_some or used_all),
                   f"{inst} Ball pocket {before} -> {after} is not the aide's natural stack minus the thrown Balls")

    decoded, _row, document = _pair_oracle(results, data_dir=data_dir, area_id=area_id, ot_ids=ot_ids,
                                           boot_saveram=boot_saveram, pocket_check=natural)
    _ball_need(document.get("pokeballs_obtained") == {"a": True, "b": True},
               f"server pokeballs_obtained {document.get('pokeballs_obtained')!r}, expected both True")
    _ball_need(len(document.get("links") or []) == 1,
               "expected exactly one link: the pre-Ball encounters and faints must form or kill none")
    log = (Path(data_dir) / "slink.log").read_text(encoding="utf-8")
    for inst in ("a", "b"):
        _ball_need(f"[{inst}] faint key={starters[inst]}" in log, f"slink.log lacks {inst}'s pre-Ball starter faint")
    _ball_need("faint →" not in log, "the server issued a death command for a pre-Ball faint")
    if on_verified is not None:
        on_verified(_verified_facts(decoded, area_id, "alive"))


# --- DUO-WAVE-D O-33 synthetic-setup duos: gen2_boxed_capture, gen2_gift, gen2_egg_hatch, gen2_npc_trade ------------
# (lua/tests/duo/gen2_synth_duo.lua; docs/gen2/reviews/DUO_WAVE_D_FACTS_2026-09-24.md). The boot bytes are a synthetic
# fixture: they are re-derived from their base fixture by tools/gen2_synth_fixtures.build_named and must equal the
# committed disclosure's sha256, and every claim is a DELTA from them (the synthetic fields are never evidence, O-33).

SYNTH_KINDS = {"gen2_boxed_capture": "full", "gen2_gift": "bill", "gen2_egg_hatch": "hatch", "gen2_npc_trade": "trade",
               "gen2_evolution": "evolve"}
# Kinds whose two halves may end on the SAME key: Kyle's ONIX has fixed DVs and OT on every cartridge
# (C/G data/events/npc_trades.asm:15); KEY-SCOPE (71d68454) scopes identity per player.
SYNTH_SAME_KEY_OK = {"trade"}
# kind -> (key_change reason, engine site) the linked hatchling must publish and have acked
SYNTH_CHANGE = {"trade": ("npc_trade", "npc_trade_finalized"), "evolve": ("evolution", "evolution_species_published")}
SYNTH_CAPTURE = {"full": ("capture_box_finalized", "wild", "box"), "bill": ("gift_party_finalized", "gift", "party"),
                 "hatch": ("hatch_finalized", "egg_hatch", "party"), "trade": ("hatch_finalized", "egg_hatch", "party"),
                 "evolve": ("hatch_finalized", "egg_hatch", "party")}
EEVEE, PIDGEY, BELLSPROUT, ONIX = 133, 16, 69, 95   # constants/pokemon_constants.asm (national order in Gen 2)
CATERPIE, METAPOD = 10, 11
EVOLVE_LEVEL = 7                                     # CaterpieEvosAttacks (data/pokemon/evos_attacks.asm)
KYLE_OT = 48926                                      # NPC_TRADE_KYLE OT ID (C/G data/events/npc_trades.asm:15)


def _synth_need(condition, reason):
    if not condition:
        raise RuntimeError(f"synth duo: {reason}")


def _synth_boot(inst, duo, boot_path):
    """The staged synthetic bytes, proven to be the builder's output of their base fixture (O-33 disclosure)."""
    from tools import gen2_synth_fixtures as synth

    name = duo.get("synth")
    _synth_need(isinstance(name, str) and name in synth.DUO_FIXTURES + synth.SYNTH_FIXTURES,
                f"{inst} booted no registered synthetic setup: {name!r}")
    raw = Path(boot_path).read_bytes()
    _synth_need(hashlib.sha256(raw).hexdigest() == duo["fixture_sha256"], f"{inst} boot bytes are not the staged ones")
    built, disclosure = synth.build_named(name)
    committed = json.loads((REPO_ROOT / "tests/fixtures/gen2" / f"{name}.synth.json").read_text(encoding="utf-8"))
    _synth_need(built == raw and committed == disclosure and disclosure["sha256"] == duo["fixture_sha256"],
                f"{inst} synthetic bytes differ from the builder's disclosed output")
    return raw[:CARTRAM_BYTES], name


def _synth_side(inst, text, kind, boot_path):
    from server.adapters import gen2_codec as codec

    witness, _client, title = _boot_marker(inst, text)
    duo = _duo_marker(inst, text)
    layout = codec.for_foundation(title)
    boot, name = _synth_boot(inst, duo, boot_path)
    flushed = witness_save_bytes(witness, layout)[:CARTRAM_BYTES]
    _synth_need(codec.strict_checksum_witness(flushed, layout)["valid"], f"{inst} flushed save fails the checksum witness")
    site, acquisition, destination = SYNTH_CAPTURE[kind]
    captures = [row for _, row in _tag_rows(text, "ENGINE_CAPTURE")
                if (row.get("site_id"), row.get("acquisition"), row.get("destination")) == (site, acquisition, destination)]
    _synth_need(len(captures) == 1, f"{inst} expected one {site} capture, got {len(captures)}")
    capture = captures[0]
    before = codec.decode_saved_party(boot, layout, copy_name="primary")["mons"]
    after = codec.decode_saved_party(flushed, layout, copy_name="primary")["mons"]
    keys_before = [codec.key(m) for m in before if not m["is_egg"]]
    keys_after = [codec.key(m) for m in after if not m["is_egg"]]
    final_key = capture["key"]
    if kind == "full":
        off, length = layout.active_box
        box_before = codec.decode_box(boot[off:off + length], layout)["mons"]
        box_after = codec.decode_box(flushed[off:off + length], layout)["mons"]
        _synth_need(len(before) == 6 and keys_after == keys_before, f"{inst} the full party changed")
        _synth_need([codec.key(m) for m in box_after] == [codec.key(m) for m in box_before] + [final_key],
                    f"{inst} the active box did not gain exactly the captured {final_key}")
        _synth_need(capture.get("area_id") == "route_29", f"{inst} box catch outside route_29")
        balls_before, balls_after = _ball_pocket(boot, layout), _ball_pocket(flushed, layout)
        _synth_need(sum(q for _, q in balls_after) == sum(q for _, q in balls_before) - 1,
                    f"{inst} Ball pocket {balls_before} -> {balls_after} is not one thrown Ball")
    elif kind == "bill":
        gift = EEVEE if _client.get("artifact_kind") != "rand_overlay" else _randomized_gift_species(
            inst, title, "BillsFamilysHouse:BillScript")
        _synth_need(keys_after == keys_before + [final_key] and after[-1]["species_id"] == gift,
                    f"{inst} the party did not gain exactly Bill's {'EEVEE' if gift == EEVEE else gift} {final_key}")
    elif kind == "hatch":
        _synth_need([m["is_egg"] for m in before] == [False, True] and not any(m["is_egg"] for m in after),
                    f"{inst} the egg was not the one hatched")
        _synth_need(keys_after == keys_before + [final_key] and after[1]["species_id"] == PIDGEY,
                    f"{inst} the hatchling {final_key} is not the party's second slot")
    else:   # trade / evolve: the hatched mon (linked), then its native key_change in the same slot
        reason, change_site = SYNTH_CHANGE[kind]
        changes = [row for _, row in _tag_rows(text, "ENGINE_KEY_CHANGE")
                   if row.get("reason") == reason and row.get("site_id") == change_site]
        _synth_need(len(changes) == 1 and changes[0].get("old_key") == capture["key"],
                    f"{inst} no single {reason} key_change of the hatched mon {capture['key']}")
        slot = 1 if kind == "trade" else 0
        was = BELLSPROUT if kind == "trade" else CATERPIE
        _synth_need(capture.get("species_id") == was, f"{inst} the hatchling is not species {was}")
        final_key = changes[0]["new_key"]
        mon = after[slot] if len(after) > slot else {}
        if kind == "trade":
            given, ot = (ONIX, KYLE_OT) if _client.get("artifact_kind") != "rand_overlay" else _randomized_npc_trade(
                inst, title, BELLSPROUT, KYLE_OT)
            ok = mon.get("species_id") == given and mon.get("ot_id") == ot
        else:
            ok = mon.get("species_id") == METAPOD and mon.get("level", 0) >= EVOLVE_LEVEL
        _synth_need(ok and codec.key(mon) == final_key and capture["key"] not in keys_after,
                    f"{inst} slot {slot + 1} is not the {reason} result {final_key}")
        lines = text.splitlines()
        tx = [i for i, line in enumerate(lines) if line.startswith("TX ") and '"event":"key_change"' in line]
        _synth_need(bool(tx) and any(line.startswith("RX key_change_ack") for line in lines[tx[0]:]),
                    f"{inst} the key_change was not acked")
    if kind != "full":
        _synth_need(capture.get("area_id") not in (None, "", "nil"), f"{inst} the capture carries no area")
    for line in text.splitlines():
        _synth_need(not line.startswith(("RX force_faint", "RX memorialize")), f"{inst} received a death command: {line}")
    return {"key": final_key, "capture": capture, "title": title, "synth": name, "area": capture.get("area_id")}


def synth_duo_oracle(results, *, scenario, data_dir, boot_saveram, ot_ids=None, on_verified=None):
    """One of the four O-33 synthetic-setup duos: both sides' native acquisition (box catch, gift, hatch, or hatch then
    NPC trade) forms one ALIVE server pair with the independently decoded final keys; no death command anywhere.
    ot_ids is accepted for the runner's common call shape; each side's OT is the synthetic base's own."""
    from server.adapters.gen2_gsc import Gen2GSCAdapter

    kind = SYNTH_KINDS[scenario]
    check_save_witness(results)
    sides = {inst: _synth_side(inst, (results or {}).get(inst) or "", kind, boot_saveram[inst]) for inst in ("a", "b")}
    _synth_need(kind in SYNTH_SAME_KEY_OK or sides["a"]["key"] != sides["b"]["key"], "both sides hold the identical key")
    areas = {Gen2GSCAdapter(side["title"]).gift_link_area(side["area"]) if kind != "full" else side["area"]
             for side in sides.values()}
    _synth_need(len(areas) == 1, f"the two captures resolve to different link areas: {sorted(areas)}")
    area = areas.pop()
    document = json.loads((Path(data_dir) / "links.json").read_text(encoding="utf-8"))
    rows = document.get("links") or []
    _synth_need(len(rows) == 1 and rows[0].get("area_id") == area and rows[0].get("status") == "alive",
                f"expected one alive {area} link, found {[(r.get('area_id'), r.get('status')) for r in rows]}")
    for inst in ("a", "b"):
        _synth_need((rows[0].get(inst) or {}).get("key") == sides[inst]["key"],
                    f"links.json {inst}.key differs from the flushed save's {sides[inst]['key']}")
    log = (Path(data_dir) / "slink.log").read_text(encoding="utf-8")
    _synth_need("faint →" not in log, "the server issued a death command")
    if on_verified is not None:
        on_verified({"a": sides["a"]["key"], "b": sides["b"]["key"], "area": area,
                     "titles": "/".join(sides[inst]["title"] for inst in ("a", "b")), "status": "alive"})
