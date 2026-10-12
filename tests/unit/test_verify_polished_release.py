"""tests/unit/test_verify_polished_release.py — the fail-closed rules of the Polished RC verifier.

`tools/verify_gen2_release.py` is the Gen 2 manifest and is off limits here: Polished Crystal is
a SOLO title, and `tests/unit/test_polished_release_guards.py` pins that manifest's TITLES and
DUO_PAIRS to vanilla. `tools/verify_polished_release.py` is the separate lane, and these tests
are about its RULES, not about Polished working:

  * a LIVE item with no receipt is red, and so is a receipt whose schema is wrong;
  * a receipt whose ROM does not bind to the overlay published today is red, and says both hashes;
  * a receipt whose evidence bytes do not hash to what it claims is red — tampering, not staleness;
  * a receipt whose evidence file cannot be reached at all is ALSO red: an unverifiable
    transcription is not evidence;
  * a SOURCE command that exits non-zero is red, whatever it printed;
  * a MODEL file with no passing test is red: did not run is not passed;
  * an OPEN obligation is red until it is CLOSED, closing it needs a LIVE item that PASSes, and
    `blocking_rc: false` is no bypass;
  * the `required_ids` census, the unreachable jar, partial runs, the LIVE receipt census
    (scenario, expected PASS checks, provenance sha256) and the COMPUTED code digest are all
    fail-closed;
  * Manager options match the qualified capabilities; enabling one cannot close an OPEN obligation.
  * a player ZIP missing the published Polished UPS, or carrying different bytes, is red.

Every RED CONTROL test below APPLIES its mutation, asserts the verifier goes red, and REVERTS.
A control that never mutated anything proves nothing.

Run:  python -m pytest tests/unit/test_verify_polished_release.py -q
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import sys
import zipfile
from types import SimpleNamespace

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tools"))

import verify_polished_release as verifier  # noqa: E402

CODE = "lua/code.lua"               # the one file the fake tree's code digest covers
ROM = "a" * 40                     # the overlay this fake tree publishes
OTHER_ROM = "b" * 40                # an overlay that was published before
EVIDENCE = "RESULT: PASS fake (0 checks failed) frame 1\n"


def _receipt(root: pathlib.Path, *, rom: str = ROM, name: str = "r.json",
             evidence: pathlib.Path | None = None, grade: str = "DEV") -> tuple[pathlib.Path, dict]:
    if evidence is None:
        evidence = root / "lane" / "result.txt"
        evidence.parent.mkdir(parents=True, exist_ok=True)
        evidence.write_text(EVIDENCE, encoding="utf-8")
    witness_path = root / "lane" / "WITNESS.json"
    witness_body = {
        "source_head": "c" * 40, "head": "c" * 40,
        "code_digest": verifier.compute_code_digest(root, [CODE]),
        "overlay_sha1": ROM, "evidence_root": str(root / "lane"),
    }
    witness_path.write_text(json.dumps(witness_body), encoding="utf-8")
    witness = {
        "path": str(witness_path), "sha256": hashlib.sha256(witness_path.read_bytes()).hexdigest(),
        "scope": str(root / "lane"), "source_head": "c" * 40,
        "code_digest": witness_body["code_digest"],
    }
    support = root / "lane" / "support.txt"
    support.write_text("native observation\n", encoding="utf-8")
    doc = {
        "schema": verifier.RECEIPT_SCHEMA,
        "grade": grade,
        "scenario_id": "fake/scenario",
        "date": "2026-10-04",
        "lane": str(root / "lane"),
        "evidence_file": str(evidence),
        "evidence_sha256": hashlib.sha256(evidence.read_bytes()).hexdigest(),
        "rom_sha1_executed": rom,
        "overlay_provenance_sha256": hashlib.sha256(
            (root / "data" / "polished" / "overlay_provenance.json").read_bytes()).hexdigest(),
        "code_digest": verifier.compute_code_digest(root, [CODE]),
        "synth": ["none"],
        "run_witness": witness,
        "secondary_evidence": [{
            "path": str(support), "sha256": hashlib.sha256(support.read_bytes()).hexdigest(),
            "run_witness": witness,
        }],
        "checks": [{"name": "run verdict", "result": "PASS", "observed": "RESULT: PASS"}],
    }
    path = root / "tests" / "fixtures" / "polished" / "receipts" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return path, doc


def _manifest(root: pathlib.Path, items: list[dict], open_items: list[dict] | None = None,
              **doc_over) -> pathlib.Path:
    """A synthetic manifest whose census lists exactly what it declares, unless overridden."""
    path = root / "tests" / "polished_release_requirements.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "schema": verifier.MANIFEST_SCHEMA,
        "required_ids": [i["id"] for i in items] + [i["id"] for i in open_items or []],
        "code_digest_files": [CODE],
        "items": items,
        "open": open_items or [],
    }
    doc.update(doc_over)
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return path


def _root(tmp_path: pathlib.Path, items, open_items=None, **doc_over) -> tuple[pathlib.Path, pathlib.Path]:
    root = tmp_path / "repo"
    (root / "data" / "polished").mkdir(parents=True, exist_ok=True)
    (root / "data" / "polished" / "overlay_provenance.json").write_text(
        json.dumps({"output": {"sha1": ROM, "md5": "0" * 32}}), encoding="utf-8")
    (root / CODE).parent.mkdir(parents=True, exist_ok=True)
    (root / CODE).write_text("return 1\n", encoding="utf-8")
    return root, _manifest(root, items, open_items, **doc_over)


def _live_item(**over) -> dict:
    item = {"id": "LIVE-FAKE", "kind": "LIVE", "description": "a fake live cell",
            "receipt": "tests/fixtures/polished/receipts/r.json",
            "expect_scenario": "fake/scenario", "expect_checks": ["run verdict"]}
    item.update(over)
    return item


def _row(rows, rid="LIVE-FAKE"):
    return next(r for r in rows if r.id == rid)


def _reasons(row) -> str:
    return " | ".join(row.reasons)


# ─────────────────────────────────────────────── happy path


def test_a_bound_receipt_passes(tmp_path):
    root, manifest = _root(tmp_path, [_live_item()])
    _receipt(root)
    rows, errs = verifier.verify(root, manifest)
    assert errs == []
    assert _row(rows).ok, _reasons(_row(rows))


def test_an_open_obligation_blocks_the_rc(tmp_path):
    root, manifest = _root(tmp_path, [_live_item()], [{
        "id": "OPEN-THING", "kind": "OPEN", "status": "OPEN", "blocking_rc": True,
        "description": "not landed", "blocker": "no implementation"}])
    _receipt(root)
    rows, _ = verifier.verify(root, manifest)
    row = _row(rows, "OPEN-THING")
    assert not row.ok and "OPEN: no implementation" in _reasons(row)


# ─────────────────────────────────────────────── receipt presence and shape


def test_a_missing_receipt_is_red(tmp_path):
    root, manifest = _root(tmp_path, [_live_item()])          # no receipt written
    rows, _ = verifier.verify(root, manifest)
    assert "missing receipt: tests/fixtures/polished/receipts/r.json" in _reasons(_row(rows))


@pytest.mark.parametrize("mutate,expect", [
    (lambda d: d.update(schema="polished-live-receipt-v2"), "schema is"),
    (lambda d: d.pop("evidence_sha256"), "missing field 'evidence_sha256'"),
    (lambda d: d.update(evidence_sha256="nothex"), "is not 64 lowercase hex chars"),
    (lambda d: d.update(rom_sha1_executed="xyz"), "is not a sha1"),
    (lambda d: d.update(synth="none"), "field 'synth' is str"),
    (lambda d: d.update(checks=[]), "checks is empty"),
    (lambda d: d.update(checks=[{"name": "x"}]), "needs 'name' and 'result'"),
    (lambda d: d.update(checks=[{"name": "x", "result": "MAYBE"}]), "is not PASS/FAIL/INFO"),
])
def test_a_bad_schema_is_red(tmp_path, mutate, expect):
    root, manifest = _root(tmp_path, [_live_item()])
    _, doc = _receipt(root)
    mutate(doc)
    (root / "tests/fixtures/polished/receipts/r.json").write_text(
        json.dumps(doc, indent=2), encoding="utf-8")
    rows, _ = verifier.verify(root, manifest)
    assert "bad schema" in _reasons(_row(rows)) and expect in _reasons(_row(rows))


def test_a_receipt_may_not_promote_itself_to_physical(tmp_path):
    root, manifest = _root(tmp_path, [_live_item()])
    _, doc = _receipt(root, grade="PHYSICAL")
    (root / "tests/fixtures/polished/receipts/r.json").write_text(
        json.dumps(doc, indent=2), encoding="utf-8")
    rows, _ = verifier.verify(root, manifest)
    assert "grade is 'PHYSICAL'" in _reasons(_row(rows))


def test_a_receipt_recording_a_failed_check_is_red(tmp_path):
    root, manifest = _root(tmp_path, [_live_item()])
    _, doc = _receipt(root)
    doc["checks"].append({"name": "the census followed the battle end", "result": "FAIL",
                          "observed": "frozen at generation 2"})
    (root / "tests/fixtures/polished/receipts/r.json").write_text(
        json.dumps(doc, indent=2), encoding="utf-8")
    rows, _ = verifier.verify(root, manifest)
    assert "receipt records a FAIL check" in _reasons(_row(rows))


# ─────────────────────────────────────────────── the ROM binding


def test_a_receipt_from_another_overlay_is_stale_and_names_both_hashes(tmp_path):
    root, manifest = _root(tmp_path, [_live_item()])
    _receipt(root, rom=OTHER_ROM)
    rows, _ = verifier.verify(root, manifest)
    reasons = _reasons(_row(rows))
    assert "STALE" in reasons and OTHER_ROM in reasons and ROM in reasons


def test_a_randomized_cartridge_binds_on_the_overlay_it_came_from(tmp_path):
    cartridge = "c" * 40
    root, manifest = _root(tmp_path, [_live_item(bind="source")])
    path, doc = _receipt(root, rom=cartridge)
    doc["source_overlay_sha1"] = ROM
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    rows, _ = verifier.verify(root, manifest)
    assert _row(rows).ok, _reasons(_row(rows))


def test_a_source_bound_item_without_the_source_sha_is_red(tmp_path):
    root, manifest = _root(tmp_path, [_live_item(bind="source")])
    _receipt(root, rom="c" * 40)                            # no source_overlay_sha1
    rows, _ = verifier.verify(root, manifest)
    assert "needs a source_overlay_sha1" in _reasons(_row(rows))


def test_a_receipt_naming_a_moved_provenance_is_red(tmp_path):
    root, manifest = _root(tmp_path, [_live_item()])
    path, doc = _receipt(root)
    doc["overlay_provenance_sha256"] = "d" * 64
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    rows, _ = verifier.verify(root, manifest)
    assert "overlay_provenance_sha256" in _reasons(_row(rows))


# ─────────────────────────────────────────────── the evidence bytes


def test_tampered_evidence_bytes_are_red(tmp_path):
    root, manifest = _root(tmp_path, [_live_item()])
    _receipt(root)
    evidence = root / "lane" / "result.txt"
    evidence.write_text(EVIDENCE + "[ok] something that never ran\n", encoding="utf-8")
    rows, _ = verifier.verify(root, manifest)
    reasons = _reasons(_row(rows))
    assert "evidence sha256 mismatch" in reasons and "lanes" not in reasons.split(":")[0]


def test_unreachable_evidence_is_red_not_silently_accepted(tmp_path):
    root, manifest = _root(tmp_path, [_live_item()])
    path, doc = _receipt(root)
    evidence = pathlib.Path(doc["evidence_file"])
    evidence.unlink()
    rows, _ = verifier.verify(root, manifest)
    assert "evidence file not reachable" in _reasons(_row(rows))


# ─────────────────────────────────────────────── SOURCE and BUILD commands


@pytest.mark.parametrize("exit_code,expect_pass", [(0, True), (1, False), (3, False)])
def test_a_source_command_passes_only_on_exit_zero(tmp_path, exit_code, expect_pass):
    root, manifest = _root(tmp_path, [{
        "id": "SRC-FAKE", "kind": "SOURCE", "description": "a command that decides for itself",
        "command": ["$PY", "-c", f"print('drift detected'); raise SystemExit({exit_code})"],
        "timeout": 120}])
    rows, _ = verifier.verify(root, manifest)
    row = _row(rows, "SRC-FAKE")
    assert row.ok is expect_pass, _reasons(row)


def test_a_missing_tool_is_red_without_running_anything(tmp_path):
    root, manifest = _root(tmp_path, [{
        "id": "SRC-GONE", "kind": "SOURCE", "description": "a tool that is not there",
        "command": ["$PY", "tools/does_not_exist.py", "--check"]}])
    rows, _ = verifier.verify(root, manifest)
    assert "missing tool: tools/does_not_exist.py" in _reasons(_row(rows, "SRC-GONE"))


def test_a_command_that_times_out_is_red(tmp_path):
    root, manifest = _root(tmp_path, [{
        "id": "SRC-SLOW", "kind": "SOURCE", "description": "a build that never finishes",
        "command": ["$PY", "-c", "import time; time.sleep(30)"], "timeout": 2}])
    rows, _ = verifier.verify(root, manifest)
    reasons = _reasons(_row(rows, "SRC-SLOW"))
    assert "timed out" in reasons and "did not run is not a pass" in reasons


# ─────────────────────────────────────────────── MODEL


def _pytest_tree(root: pathlib.Path, body: str) -> str:
    path = root / "tests" / "unit" / "test_fake_lane.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return "tests/unit/test_fake_lane.py"


def test_a_model_item_runs_one_pytest_per_file(tmp_path):
    root, _ = _root(tmp_path, [])
    files = [_pytest_tree(root, "def test_ok():\n    assert 1 == 1\n")]
    manifest = _manifest(root, [{"id": "MODEL-FAKE", "kind": "MODEL",
                                 "description": "one green file", "files": files}])
    rows, _ = verifier.verify(root, manifest)
    row = _row(rows, "MODEL-FAKE")
    assert row.ok, _reasons(row)
    assert row.detail == "1/1 files green"


def test_a_model_file_that_only_skips_is_red(tmp_path):
    root, _ = _root(tmp_path, [])
    files = [_pytest_tree(root, "import pytest\n\n\n@pytest.mark.skip(reason='later')\n"
                             "def test_skipped():\n    assert True\n")]
    manifest = _manifest(root, [{"id": "MODEL-FAKE", "kind": "MODEL",
                                 "description": "a file whose only test is skipped",
                                 "files": files}])
    rows, _ = verifier.verify(root, manifest)
    reasons = _reasons(_row(rows, "MODEL-FAKE"))
    assert "is not a pass" in reasons and "1 skipped" in reasons


def test_a_model_item_with_a_missing_file_is_red(tmp_path):
    root, manifest = _root(tmp_path, [{
        "id": "MODEL-FAKE", "kind": "MODEL", "description": "a file that does not exist",
        "files": ["tests/unit/test_never_written.py"]}])
    rows, _ = verifier.verify(root, manifest)
    assert "tests/unit/test_never_written.py: MISSING" in _reasons(_row(rows, "MODEL-FAKE"))


# ─────────────────────────────────────────────── OPEN bookkeeping


def test_closing_an_open_item_needs_a_closed_by(tmp_path):
    root, manifest = _root(tmp_path, [_live_item()], [{
        "id": "OPEN-THING", "kind": "OPEN", "status": "CLOSED", "blocking_rc": True,
        "description": "landed"}])
    _receipt(root)
    _, errs = verifier.verify(root, manifest)
    assert any("closed_by None must name a LIVE item" in e for e in errs)
    assert "CLOSED without a closed_by" in _reasons(verifier.check_open(
        {"id": "X", "kind": "OPEN", "status": "CLOSED"}, root))


def test_a_closed_open_item_closed_by_a_passing_live_item_passes(tmp_path):
    root, manifest = _root(tmp_path, [_live_item()], [{
        "id": "OPEN-THING", "kind": "OPEN", "status": "CLOSED", "blocking_rc": True,
        "description": "landed", "closed_by": "LIVE-FAKE"}])
    _receipt(root)
    rows, errs = verifier.verify(root, manifest)
    assert errs == []
    assert _row(rows, "OPEN-THING").ok, _reasons(_row(rows, "OPEN-THING"))


def test_a_free_text_closed_by_is_no_longer_enough(tmp_path):
    """OLD FALSE-GREEN: any truthy closed_by string closed the item. Now it must be a LIVE id."""
    root, manifest = _root(tmp_path, [_live_item()], [{
        "id": "OPEN-THING", "kind": "OPEN", "status": "CLOSED", "description": "landed",
        "closed_by": "owner 2026-10-04: reviewed"}])
    _receipt(root)
    rows, errs = verifier.verify(root, manifest)
    assert any("must name a LIVE item id" in e for e in errs) and rows == []
    # and the row-level rule agrees when the manifest check is bypassed
    row = verifier.check_open({"id": "X", "kind": "OPEN", "status": "CLOSED",
                               "closed_by": "owner 2026-10-04: reviewed"}, root)
    assert not row.ok and "PASSes in this run" in _reasons(row)


def test_closed_by_a_live_item_that_is_red_is_red(tmp_path):
    """The named LIVE item exists but has no receipt: the closure has nothing behind it."""
    root, manifest = _root(tmp_path, [_live_item()], [{
        "id": "OPEN-THING", "kind": "OPEN", "status": "CLOSED", "description": "landed",
        "closed_by": "LIVE-FAKE"}])                                     # no receipt written
    rows, errs = verifier.verify(root, manifest)
    assert errs == [] and not _row(rows).ok
    assert "PASSes in this run" in _reasons(_row(rows, "OPEN-THING"))


def test_closed_by_a_live_item_outside_the_run_is_red(tmp_path):
    root, manifest = _root(tmp_path, [_live_item()], [{
        "id": "OPEN-THING", "kind": "OPEN", "status": "CLOSED", "description": "landed",
        "closed_by": "LIVE-FAKE"}])
    _receipt(root)
    rows, _ = verifier.verify(root, manifest, only=frozenset({"OPEN-THING"}))
    assert "PASSes in this run" in _reasons(_row(rows, "OPEN-THING"))


@pytest.mark.parametrize("value", [False, 0, None, "no"])
def test_blocking_rc_is_not_a_bypass(tmp_path, value):
    """OLD FALSE-GREEN: status OPEN + blocking_rc false returned PASS."""
    item = {"id": "OPEN-THING", "kind": "OPEN", "status": "OPEN", "blocking_rc": value,
            "description": "not landed", "blocker": "nope"}
    assert not verifier.check_open(item, tmp_path).ok                # the row itself is red
    root, manifest = _root(tmp_path, [_live_item()], [item])
    _receipt(root)
    rows, errs = verifier.verify(root, manifest)
    assert any("blocking_rc must be true or absent" in e for e in errs) and rows == []


def test_an_open_item_without_blocking_rc_is_still_red(tmp_path):
    row = verifier.check_open({"id": "X", "kind": "OPEN", "status": "OPEN", "description": "d"},
                              tmp_path)
    assert not row.ok and "OPEN:" in _reasons(row)


@pytest.mark.parametrize("option", ["explode_mode", "rival_team_swap"], ids=["explode", "rival"])
def test_enabled_manager_options_do_not_close_an_open_obligation(tmp_path, monkeypatch, option):
    import server.manager as manager

    root, manifest = _root(tmp_path, [_live_item()], [{
        "id": "OPEN-THING", "kind": "OPEN", "status": "OPEN", "blocking_rc": True,
        "description": "landed", "blocker": "receipt rebind pending", "manager_options": [option]}])
    _receipt(root)
    rows, _ = verifier.verify(root, manifest)
    row = _row(rows, "OPEN-THING/manager")
    assert row.ok and "qualified availability" in row.detail
    assert not _row(rows, "OPEN-THING").ok
    assert "receipt rebind pending" in _reasons(_row(rows, "OPEN-THING"))

    monkeypatch.setitem(manager.OPTION_SUPPORT[option], "gen2_polished",
                        {"ok": False, "why": "unqualified"})
    rows, _ = verifier.verify(root, manifest)
    assert "expected ok=True" in _reasons(_row(rows, "OPEN-THING/manager"))


def test_unqualified_manager_option_cannot_be_enabled_while_open(tmp_path, monkeypatch):
    import server.manager as manager

    item = {"id": "OPEN-THING", "manager_options": ["phone_calls"]}
    assert verifier.check_open_consistency([item], tmp_path)[0].ok
    monkeypatch.setitem(manager.OPTION_SUPPORT["phone_calls"], "gen2_polished",
                        {"ok": True, "why": "unqualified"})
    assert "expected ok=False" in _reasons(verifier.check_open_consistency([item], tmp_path)[0])


def test_an_open_item_bound_to_an_option_with_no_polished_row_is_red(tmp_path):
    root, manifest = _root(tmp_path, [_live_item()], [{
        "id": "OPEN-THING", "kind": "OPEN", "status": "OPEN", "blocking_rc": True,
        "description": "landed", "blocker": "nope", "manager_options": ["overworld_presence"]}])
    _receipt(root)
    rows, _ = verifier.verify(root, manifest)
    assert "has no gen2_polished row" in _reasons(_row(rows, "OPEN-THING/manager"))


# ─────────────────────────────────────────────── manifest declarations


def test_an_unknown_only_id_is_reported(tmp_path):
    root, manifest = _root(tmp_path, [_live_item()])
    _receipt(root)
    _, errs = verifier.verify(root, manifest, only=frozenset({"LIVE-NOPE"}))
    assert any("unknown id" in e for e in errs)


@pytest.mark.parametrize("item,expect", [
    ({"id": "X-1", "kind": "LIVE", "description": "d"},
     "LIVE needs a receipt"),
    ({"id": "X-1", "kind": "SOURCE", "description": "d"},
     "SOURCE needs a command"),
    ({"id": "X-1", "kind": "MODEL", "description": "d"},
     "MODEL needs files"),
    ({"id": "X-1", "kind": "MANAGER", "description": "d", "check": "nope"},
     "unknown manager check"),
    ({"id": "X-1", "kind": "OPEN", "description": "d"},
     "OPEN items belong in the 'open' list"),
    ({"id": "X-1", "kind": "WAT", "description": "d"},
     "is not one of"),
    ({"id": "", "kind": "SOURCE", "description": "d", "command": ["true"]},
     "is missing 'id'"),
    ({"id": "X-1", "kind": "SOURCE", "description": "", "command": ["true"]},
     "is missing 'description'"),
])
def test_manifest_declaration_errors(item, expect):
    errs = verifier.manifest_errors({"schema": verifier.MANIFEST_SCHEMA, "items": [item]})
    assert any(expect in e for e in errs), errs


def test_duplicate_ids_are_a_manifest_error():
    item = {"id": "X-1", "kind": "SOURCE", "description": "d", "command": ["true"]}
    errs = verifier.manifest_errors({"schema": verifier.MANIFEST_SCHEMA, "items": [item, dict(item)]})
    assert any("duplicate id" in e for e in errs)


def test_a_manifest_with_the_wrong_schema_string_is_refused(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"schema": "something-else", "items": []}), encoding="utf-8")
    with pytest.raises(ValueError, match="manifest schema"):
        verifier.load_manifest(path)
    doc = json.loads((REPO / verifier.MANIFEST).read_text(encoding="utf-8"))
    assert doc["schema"] == verifier.MANIFEST_SCHEMA


# ─────────────────────────────────────────────── the shipped artefacts


def test_the_shipped_manifest_and_receipts_are_wellformed():
    """A verifier whose own artefacts are malformed is worse than no verifier."""
    manifest = verifier.load_manifest(REPO / verifier.MANIFEST)
    assert verifier.manifest_errors(manifest) == []
    receipts = sorted((REPO / manifest["receipts_dir"]).glob("*.json"))
    assert receipts, "no committed receipts"
    for path in receipts:
        doc = json.loads(path.read_text(encoding="utf-8"))
        assert verifier.receipt_schema_errors(doc) == [], f"{path.name}"
        assert doc["grade"] == "DEV", f"{path.name} claims a grade other than DEV"


def test_every_live_item_points_at_a_committed_receipt():
    manifest = verifier.load_manifest(REPO / verifier.MANIFEST)
    live = [i for i in manifest["items"] if i["kind"] == "LIVE"]
    assert live
    for item in live:
        assert (REPO / item["receipt"]).is_file(), item["id"]


def test_every_open_item_blocks_the_rc():
    manifest = verifier.load_manifest(REPO / verifier.MANIFEST)
    opens = manifest["open"]
    live_ids = {i["id"] for i in manifest["items"] if i["kind"] == "LIVE"}
    assert opens
    for item in opens:
        assert item["status"] in ("OPEN", "CLOSED"), item["id"]
        assert item["blocking_rc"] is True, item["id"]
        assert item.get("blocker"), item["id"]
        if item["status"] == "CLOSED":                  # a closure names the LIVE row that closes it
            assert item.get("closed_by") in live_ids, item["id"]


# ─────────────────────────────────────────────── RED CONTROLS
# Each one APPLIES the mutation, asserts red, and REVERTS.


def test_red_control_promoting_a_receipt_to_physical(tmp_path):
    """RED CONTROL: set grade to PHYSICAL in the receipt. The verifier must go red, then recover."""
    root, manifest = _root(tmp_path, [_live_item()])
    path, _ = _receipt(root)
    original = path.read_text(encoding="utf-8")
    try:
        doc = json.loads(original)
        doc["grade"] = "PHYSICAL"
        path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
        rows, _ = verifier.verify(root, manifest)
        assert not _row(rows).ok
    finally:
        path.write_text(original, encoding="utf-8")
    rows, _ = verifier.verify(root, manifest)
    assert _row(rows).ok, _reasons(_row(rows))


def test_red_control_editing_evidence_under_the_receipt(tmp_path):
    """RED CONTROL: append a fake passing line to the evidence file. Red, then revert."""
    root, manifest = _root(tmp_path, [_live_item()])
    _receipt(root)
    evidence = root / "lane" / "result.txt"
    original = evidence.read_bytes()
    try:
        evidence.write_bytes(original + b"[ok] a check that never ran\n")
        rows, _ = verifier.verify(root, manifest)
        assert "evidence sha256 mismatch" in _reasons(_row(rows))
    finally:
        evidence.write_bytes(original)
    rows, _ = verifier.verify(root, manifest)
    assert _row(rows).ok, _reasons(_row(rows))


def test_red_control_deleting_a_committed_receipt(tmp_path):
    """RED CONTROL: delete the receipt file. Red, then restore it byte for byte."""
    root, manifest = _root(tmp_path, [_live_item()])
    path, _ = _receipt(root)
    original = path.read_bytes()
    try:
        path.unlink()
        rows, _ = verifier.verify(root, manifest)
        assert "missing receipt" in _reasons(_row(rows))
    finally:
        path.write_bytes(original)
    rows, _ = verifier.verify(root, manifest)
    assert _row(rows).ok, _reasons(_row(rows))


def test_red_control_moving_the_published_overlay_sha(tmp_path):
    """RED CONTROL: republish a different overlay. A live cell that ran the old one goes red."""
    root, manifest = _root(tmp_path, [_live_item()])
    _receipt(root)                                                    # bound to the published ROM
    assert _row(verifier.verify(root, manifest)[0]).ok
    provenance = root / "data" / "polished" / "overlay_provenance.json"
    original = provenance.read_text(encoding="utf-8")
    try:
        provenance.write_text(json.dumps({"output": {"sha1": OTHER_ROM, "md5": "0" * 32}}),
                              encoding="utf-8")
        rows, _ = verifier.verify(root, manifest)
        reasons = _reasons(_row(rows))
        assert "STALE" in reasons and ROM in reasons and OTHER_ROM in reasons
    finally:
        provenance.write_text(original, encoding="utf-8")
    assert _row(verifier.verify(root, manifest)[0]).ok


# ─────────────────────────────────────────────── CLOSED FALSE-GREEN PATHS
# Each test below fails on the verifier as it stood before the hardening.


def _put(path: pathlib.Path, doc: dict) -> None:
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")


def _open_item(rid: str = "OPEN-THING") -> dict:
    return {"id": rid, "kind": "OPEN", "status": "OPEN", "blocking_rc": True,
            "description": "not landed", "blocker": "no implementation"}


# (2) the obligation census


def test_census_a_deleted_open_list_is_a_manifest_error(tmp_path):
    """OLD FALSE-GREEN: an empty `open` list passed declaration validation."""
    root, manifest = _root(tmp_path, [_live_item()], [_open_item()])
    doc = json.loads(manifest.read_text(encoding="utf-8"))
    doc["open"] = []
    _put(manifest, doc)
    rows, errs = verifier.verify(root, manifest)
    assert rows == [] and any("required id 'OPEN-THING' is absent" in e for e in errs)


def test_census_a_deleted_item_is_a_manifest_error(tmp_path):
    second = _live_item(id="LIVE-SECOND")
    root, manifest = _root(tmp_path, [_live_item(), second])
    doc = json.loads(manifest.read_text(encoding="utf-8"))
    doc["items"].remove(second)
    _put(manifest, doc)
    _, errs = verifier.verify(root, manifest)
    assert any("required id 'LIVE-SECOND' is absent" in e for e in errs)


def test_census_an_unlisted_id_must_be_added_on_purpose(tmp_path):
    root, manifest = _root(tmp_path, [_live_item(), _live_item(id="LIVE-NEW")],
                           required_ids=["LIVE-FAKE"])
    _, errs = verifier.verify(root, manifest)
    assert any("id 'LIVE-NEW' is not in required_ids" in e for e in errs)


@pytest.mark.parametrize("value", [None, [], "LIVE-FAKE"])
def test_census_a_missing_or_empty_required_ids_is_a_manifest_error(tmp_path, value):
    root, manifest = _root(tmp_path, [_live_item()], required_ids=value)
    _, errs = verifier.verify(root, manifest)
    assert any("required_ids must be a non-empty list" in e for e in errs)


# (3) the jar


_POLISHED_JAR_FILES = (
    "com/dabomstew/pkrandom/config/polished_offsets.ini",
    "com/dabomstew/pkrandom/constants/PolishedConstants.class",
    "com/dabomstew/pkrandom/romhandlers/PolishedCrystalRomHandler.class",
    "com/dabomstew/pkrandom/romhandlers/PolishedCrystalRomHandler$Factory.class",
    "com/dabomstew/pkrandom/romhandlers/PolishedCrystalRomHandler$RomEntry.class",
    "com/dabomstew/pkrandom/romhandlers/PolishedCrystalRomHandler$Slot.class",
    "com/dabomstew/pkrandom/romhandlers/PolishedCrystalRomHandler$TrainerRecord.class",
)


def _manager_jar_fixture(tmp_path, monkeypatch, *, omitted=None, listed=True,
                         label="release-capable fork, stamp v0.4.0 (patches 0001-0022)"):
    import server.upr_pipeline as upr
    root = tmp_path / "repo"
    (root / "data").mkdir(parents=True)
    selected = tmp_path / "selected.jar"
    legacy = tmp_path / "private.jar"
    for jar, payload in ((selected, b"selected fixture"), (legacy, b"legacy fixture")):
        with zipfile.ZipFile(jar, "w") as zf:
            for name in _POLISHED_JAR_FILES:
                if jar == selected and name == omitted:
                    continue
                zf.writestr(name, payload)
    sha = hashlib.sha256(selected.read_bytes()).hexdigest()
    pins = {"old patches 0001-0021": hashlib.sha256(legacy.read_bytes()).hexdigest()}
    if listed:
        pins[label] = sha
    path = root / "data/upr_jars.json"
    _put(path, pins)
    monkeypatch.setenv("SLINK_UPR_JAR", str(selected))
    monkeypatch.setattr(upr, "UPR_JAR_ALLOWLIST", str(path))
    # A trusted private decoy must never satisfy the gate for the Manager's selected jar.
    monkeypatch.setattr(verifier, "JAR_PATH", legacy, raising=False)
    return root, selected, sha, label


@pytest.mark.parametrize("label", ["stamp v0.4.0 (patches 0001-0022)", "qualified forms fork"],
                         ids=["new-release-label", "no-patch-range"])
def test_jar_pin_accepts_the_manager_selected_release_jar_with_an_arbitrary_label(tmp_path, monkeypatch, label):
    root, jar, sha, label = _manager_jar_fixture(tmp_path, monkeypatch, label=label)
    ok, detail = verifier._jar_pin(root)
    assert ok, detail
    assert sha in detail and label in detail and str(jar) in detail


def test_jar_pin_refuses_an_unlisted_manager_jar_even_with_a_trusted_private_decoy(tmp_path, monkeypatch):
    root, _jar, sha, _label = _manager_jar_fixture(tmp_path, monkeypatch, listed=False)
    ok, detail = verifier._jar_pin(root)
    assert not ok and "not pinned in data/upr_jars.json" in detail and sha in detail


@pytest.mark.parametrize("missing", _POLISHED_JAR_FILES,
                         ids=["ini", "constants", "handler", "factory", "rom-entry", "wild-slot", "trainer-record"])
def test_jar_pin_refuses_each_missing_polished_resource_or_class(tmp_path, monkeypatch, missing):
    root, _jar, _sha, _label = _manager_jar_fixture(tmp_path, monkeypatch, omitted=missing)
    ok, detail = verifier._jar_pin(root)
    assert not ok and "missing Polished jar entries" in detail and missing in detail


def test_an_unreachable_pinned_jar_is_red(tmp_path, monkeypatch):
    """OLD FALSE-GREEN: the jar pin returned True when the jar file could not be reached."""
    root = tmp_path / "repo"
    (root / "data").mkdir(parents=True)
    (root / "data" / "upr_jars.json").write_text(
        json.dumps({"patches 0001-0021": "e" * 64}), encoding="utf-8")
    import server.upr_pipeline as upr
    monkeypatch.setattr(upr, "find_upr_jar", lambda: str(tmp_path / "no" / "such.jar"))
    ok, detail = verifier._jar_pin(root)
    assert not ok
    assert "jar not reachable" in detail


def test_a_jar_that_hashes_wrong_is_still_red(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    (root / "data").mkdir(parents=True)
    (root / "data" / "upr_jars.json").write_text(
        json.dumps({"patches 0001-0021": "e" * 64}), encoding="utf-8")
    jar = tmp_path / "x.jar"
    jar.write_bytes(b"not the pinned jar")
    import server.upr_pipeline as upr
    monkeypatch.setattr(upr, "find_upr_jar", lambda: str(jar))
    ok, detail = verifier._jar_pin(root)
    assert not ok and "not pinned in data/upr_jars.json" in detail


def test_jar_pin_refuses_when_the_manager_resolver_finds_nothing(tmp_path, monkeypatch):
    import server.upr_pipeline as upr
    monkeypatch.setattr(upr, "find_upr_jar", lambda: None)
    ok, detail = verifier._jar_pin(tmp_path)
    assert not ok and "Manager resolver found no jar" in detail


def test_jar_pin_preserves_the_runtime_trust_refusal(tmp_path, monkeypatch):
    import server.upr_pipeline as upr
    root, _jar, _sha, _label = _manager_jar_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(upr, "jar_is_trusted", lambda path: False)
    ok, detail = verifier._jar_pin(root)
    assert not ok and "jar_is_trusted() refuses" in detail


def test_jar_pin_refuses_an_allowlisted_corrupt_zip(tmp_path, monkeypatch):
    root, jar, _sha, _label = _manager_jar_fixture(tmp_path, monkeypatch)
    jar.write_bytes(b"not a ZIP")
    _put(root / "data/upr_jars.json", {"corrupt": hashlib.sha256(jar.read_bytes()).hexdigest()})
    ok, detail = verifier._jar_pin(root)
    assert not ok and "not a readable ZIP" in detail


# (4) player ZIP companion binding


def _stamp(root, version, **extra):
    """Write the record tools/stamp_release.py leaves in patch/dist/ (only the field the verifier reads)."""
    path = root / "patch" / "dist" / "companion_version.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema": "slink-companion-version-v1", "version": version, **extra}),
                    encoding="utf-8")


def _release_root(tmp_path):
    item = {"id": "REL-PLAYER-ZIP", "kind": "RELEASE", "description": "player ZIP"}
    root, manifest = _root(tmp_path, [item])
    (root / "tools").mkdir()
    (root / "tools" / "check_release_zip.py").write_text("print('PASS')\n", encoding="utf-8")
    return item, root


def test_a_build_release_refusal_is_a_red_row_not_a_dead_verifier(tmp_path, monkeypatch):
    item, root = _release_root(tmp_path)
    _stamp(root, "v0.3.1")

    def refusing_build(**kwargs):
        print("ERROR - the companions are not stamped for this release:", file=sys.stderr)
        sys.exit(1)

    monkeypatch.setitem(sys.modules, "make_release", SimpleNamespace(build_release=refusing_build))
    row = verifier.check_release(item, root)
    assert not row.ok
    assert "companions are not stamped for this release" in _reasons(row)


def test_the_release_is_built_at_the_version_the_companions_were_stamped(tmp_path, monkeypatch):
    item, root = _release_root(tmp_path)
    _stamp(root, "v0.3.1")
    seen = {}

    def spy_build(**kwargs):
        seen.update(kwargs)
        raise SystemExit(1)

    monkeypatch.setitem(sys.modules, "make_release", SimpleNamespace(build_release=spy_build))
    verifier.check_release(item, root)
    assert seen["version"] == "v0.3.1"
    assert not seen.get("allow_unstamped")


def test_an_unstamped_tree_is_red_and_never_builds(tmp_path, monkeypatch):
    item, root = _release_root(tmp_path)
    called = []
    monkeypatch.setitem(sys.modules, "make_release",
                        SimpleNamespace(build_release=lambda **kw: called.append(kw)))
    row = verifier.check_release(item, root)
    assert not row.ok and not called
    assert "tools/stamp_release.py --version" in _reasons(row)


@pytest.mark.parametrize("stamped", ["mixed", "dev", "v0.3.1-dev", "rc", ""])
def test_a_mixed_or_dev_stamp_is_red_and_never_builds(tmp_path, monkeypatch, stamped):
    item, root = _release_root(tmp_path)
    _stamp(root, stamped)
    called = []
    monkeypatch.setitem(sys.modules, "make_release",
                        SimpleNamespace(build_release=lambda **kw: called.append(kw)))
    row = verifier.check_release(item, root)
    assert not row.ok and not called
    assert "tools/stamp_release.py --version" in _reasons(row)


def test_a_corrupt_stamp_record_is_red_and_never_builds(tmp_path, monkeypatch):
    item, root = _release_root(tmp_path)
    path = root / "patch" / "dist" / "companion_version.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    called = []
    monkeypatch.setitem(sys.modules, "make_release",
                        SimpleNamespace(build_release=lambda **kw: called.append(kw)))
    row = verifier.check_release(item, root)
    assert not row.ok and not called


def _as_built_release_stamp(root):
    import stamp_release
    dist = root / "patch/dist"
    dist.mkdir(parents=True, exist_ok=True)
    bodies = {name: name.encode() for name in stamp_release.SHIPPED}
    bodies["SLink-Polished.ups"] = b"Polished as built"
    for name, body in bodies.items():
        (dist / name).write_bytes(body)
    pins = {name: hashlib.sha256(body).hexdigest() for name, body in bodies.items()}
    rel = "data/polished/overlay_provenance.json"
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    _put(root / rel, {"schema": "polished-overlay-provenance-v1", "output": {"ups": {
        "file": "patch/dist/SLink-Polished.ups", "sha256": pins["SLink-Polished.ups"]}}})
    doc = {"schema": "slink-companion-version-v2", "version": "v0.4.0", "files": pins,
           "families": {family: {"mode": "stamped", "version": "v0.4.0"}
                        for family in ("rb", "pure", "gen2", "gen3")}}
    doc["families"]["polished"] = {"mode": "as-built", "provenance": rel,
        "provenance_sha256": hashlib.sha256((root / rel).read_bytes()).hexdigest(),
        "files": {"SLink-Polished.ups": pins["SLink-Polished.ups"]}}
    _put(dist / "companion_version.json", doc)
    return dist, doc


def test_stamped_release_accepts_the_provenance_bound_as_built_polished_certificate(tmp_path):
    _as_built_release_stamp(tmp_path)
    assert verifier._stamped_release(tmp_path) == ("v0.4.0", "")


@pytest.mark.parametrize("fault", ["provenance", "ups", "policy", "unknown-schema"],
                         ids=["changed-provenance", "changed-ups", "uncovered-family", "schema"])
def test_stamped_release_refuses_an_invalid_as_built_certificate(tmp_path, fault):
    dist, doc = _as_built_release_stamp(tmp_path)
    if fault == "provenance":
        path = tmp_path / "data/polished/overlay_provenance.json"
        path.write_text(path.read_text() + "\n")
    elif fault == "ups":
        (dist / "SLink-Polished.ups").write_bytes(b"changed")
    elif fault == "policy":
        del doc["families"]["polished"]
    else:
        doc["schema"] = "unknown"
    _put(dist / "companion_version.json", doc)
    version, why = verifier._stamped_release(tmp_path)
    assert version is None and why


@pytest.mark.parametrize("stdout,stderr,expected", [(b"partial", None, "partial"),
                         (None, b"refusal", "refusal"), (b"out", b"err", "outerr")],
                         ids=["stdout-bytes", "stderr-bytes", "both-bytes"])
def test_timeout_bytes_are_a_failed_result_with_the_captured_reason(tmp_path, monkeypatch, stdout, stderr, expected):
    import subprocess

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("checker", 1, output=stdout, stderr=stderr)

    monkeypatch.setattr(verifier.subprocess, "run", timeout)
    assert verifier._run(["checker"], tmp_path, 1) == (None, expected, True)


@pytest.mark.parametrize("companion", ["matching", "missing", "mismatched"])
def test_player_zip_requires_the_published_polished_companion(tmp_path, monkeypatch, companion):
    item = {"id": "REL-PLAYER-ZIP", "kind": "RELEASE", "description": "player ZIP"}
    root, manifest = _root(tmp_path, [item])
    published = b"published Polished companion"
    provenance_path = root / verifier.PROVENANCE
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    provenance["output"]["ups"] = {"sha256": hashlib.sha256(published).hexdigest()}
    _put(provenance_path, provenance)
    tools = root / "tools"
    tools.mkdir()
    (tools / "check_release_zip.py").write_text("print('PASS synthetic ZIP hygiene')\n", encoding="utf-8")

    def fake_build(*, version, out_dir, skip_generators, quiet, with_patch=False):
        path = out_dir / f"SLink-player-{version}.zip"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(f"SLink-player-{version}/README.md", "synthetic player build")
            if with_patch and companion != "missing":
                payload = published if companion == "matching" else b"another Polished companion"
                archive.writestr(f"SLink-player-{version}/companion/SLink-Polished.ups", payload)
        return path

    _stamp(root, "v0.3.1")
    monkeypatch.setitem(sys.modules, "make_release", SimpleNamespace(build_release=fake_build))
    rows, errs = verifier.verify(root, manifest)
    assert errs == []
    row = _row(rows, item["id"])
    if companion == "matching":
        assert row.ok, _reasons(row)
    else:
        assert not row.ok
        expected = ("missing required companion/SLink-Polished.ups" if companion == "missing"
                    else "companion/SLink-Polished.ups sha256 mismatch")
        assert expected in _reasons(row)


# (4) partial runs


def _main(capsys, root, manifest, *args):
    code = verifier.main(["--root", str(root), "--manifest", str(manifest), *args])
    return code, capsys.readouterr()


def test_a_complete_green_run_exits_zero_and_is_complete(tmp_path, capsys):
    """The positive control for the partial-run rules."""
    root, manifest = _root(tmp_path, [_live_item()])
    _receipt(root)
    code, out = _main(capsys, root, manifest, "--json")
    assert code == 0 and json.loads(out.out)["complete"] is True
    code, out = _main(capsys, root, manifest)
    assert code == 0 and "GATE PASSED" in out.out and "PARTIAL" not in out.out


def test_only_is_a_partial_run_and_never_exits_zero(tmp_path, capsys):
    """OLD FALSE-GREEN: --only on a green item exited 0."""
    root, manifest = _root(tmp_path, [_live_item()])
    _receipt(root)
    code, out = _main(capsys, root, manifest, "--only", "LIVE-FAKE")
    assert code == 3 and "PARTIAL RUN: NOT A RELEASE VERDICT" in out.out


def test_no_release_is_a_partial_run_and_never_exits_zero(tmp_path, capsys):
    root, manifest = _root(tmp_path, [_live_item()])
    _receipt(root)
    code, out = _main(capsys, root, manifest, "--no-release")
    assert code == 3 and "PARTIAL RUN: NOT A RELEASE VERDICT" in out.out


def test_a_partial_json_run_says_complete_false(tmp_path, capsys):
    root, manifest = _root(tmp_path, [_live_item()])
    _receipt(root)
    for flag in (["--only", "LIVE-FAKE"], ["--no-release"]):
        code, out = _main(capsys, root, manifest, "--json", *flag)
        assert code == 3 and json.loads(out.out)["complete"] is False
        assert "PARTIAL RUN: NOT A RELEASE VERDICT" in out.err


def test_a_partial_run_that_is_red_still_exits_nonzero(tmp_path, capsys):
    root, manifest = _root(tmp_path, [_live_item()])                    # no receipt
    code, _ = _main(capsys, root, manifest, "--only", "LIVE-FAKE")
    assert code == 1


def test_list_is_marked_not_a_verdict(tmp_path, capsys):
    root, manifest = _root(tmp_path, [_live_item()])
    code, out = _main(capsys, root, manifest, "--list")
    assert code == 0 and "not a verdict" in out.out


# (5) the LIVE receipt census and the computed code digest


def _write_receipt(root, mutate) -> None:
    path, doc = _receipt(root)
    mutate(doc)
    _put(path, doc)


def test_an_info_only_receipt_is_red(tmp_path):
    """OLD FALSE-GREEN: a receipt whose checks were all INFO passed."""
    root, manifest = _root(tmp_path, [_live_item()])
    _write_receipt(root, lambda d: d.update(checks=[{"name": "run verdict", "result": "INFO"}]))
    rows, _ = verifier.verify(root, manifest)
    reasons = _reasons(_row(rows))
    assert "INFO-only" in reasons and "expected check 'run verdict' is not recorded as PASS" in reasons


def test_a_missing_expected_check_is_red(tmp_path):
    root, manifest = _root(tmp_path, [_live_item(expect_checks=["run verdict", "the real claim"])])
    _receipt(root)
    rows, _ = verifier.verify(root, manifest)
    assert "expected check 'the real claim' is not recorded as PASS" in _reasons(_row(rows))


def test_an_expected_check_recorded_only_as_info_is_red(tmp_path):
    root, manifest = _root(tmp_path, [_live_item(expect_checks=["run verdict", "the real claim"])])
    _write_receipt(root, lambda d: d["checks"].append({"name": "the real claim", "result": "INFO"}))
    rows, _ = verifier.verify(root, manifest)
    assert "'the real claim' is not recorded as PASS" in _reasons(_row(rows))


def test_a_receipt_for_another_scenario_is_red(tmp_path):
    """OLD FALSE-GREEN: any scenario_id was accepted."""
    root, manifest = _root(tmp_path, [_live_item(expect_scenario="live/the-one-we-need")])
    _receipt(root)
    rows, _ = verifier.verify(root, manifest)
    assert "scenario_id 'fake/scenario' is not the expected 'live/the-one-we-need'" in _reasons(_row(rows))


@pytest.mark.parametrize("key,errtext", [("expect_scenario", "needs an expect_scenario"),
                                         ("expect_checks", "non-empty expect_checks")])
def test_a_live_item_must_declare_what_it_expects(tmp_path, key, errtext):
    item = _live_item()
    item.pop(key)
    root, manifest = _root(tmp_path, [item])
    rows, errs = verifier.verify(root, manifest)
    assert rows == [] and any(errtext in e for e in errs)


@pytest.mark.parametrize("value", [None, "UNRECORDED", "", "nothex"])
def test_a_receipt_without_a_provenance_sha256_is_red(tmp_path, value):
    """OLD FALSE-GREEN: a missing / null / UNRECORDED provenance sha256 was skipped."""
    root, manifest = _root(tmp_path, [_live_item()])
    _write_receipt(root, lambda d: d.update(overlay_provenance_sha256=value))
    rows, _ = verifier.verify(root, manifest)
    assert "must record the sha256 of the provenance" in _reasons(_row(rows))


def test_a_receipt_with_no_provenance_field_at_all_is_red(tmp_path):
    root, manifest = _root(tmp_path, [_live_item()])
    _write_receipt(root, lambda d: d.pop("overlay_provenance_sha256"))
    rows, _ = verifier.verify(root, manifest)
    assert "must record the sha256 of the provenance" in _reasons(_row(rows))


@pytest.mark.parametrize("value", ["UNRECORDED", "f" * 64])
def test_a_code_digest_the_verifier_did_not_compute_is_red(tmp_path, value):
    """OLD FALSE-GREEN: code_digest was a free string (UNRECORDED passed)."""
    root, manifest = _root(tmp_path, [_live_item()])
    _write_receipt(root, lambda d: d.update(code_digest=value))
    rows, _ = verifier.verify(root, manifest)
    assert "STALE: code_digest" in _reasons(_row(rows))


def test_changing_a_digested_file_makes_the_receipt_stale_and_reverting_heals_it(tmp_path):
    root, manifest = _root(tmp_path, [_live_item()])
    _receipt(root)
    code = root / CODE
    original = code.read_bytes()
    assert _row(verifier.verify(root, manifest)[0]).ok
    try:
        code.write_bytes(original + b"-- a change after the run\n")
        assert "STALE: code_digest" in _reasons(_row(verifier.verify(root, manifest)[0]))
    finally:
        code.write_bytes(original)
    assert _row(verifier.verify(root, manifest)[0]).ok


def test_the_code_digest_ignores_line_endings(tmp_path):
    root, manifest = _root(tmp_path, [_live_item()])
    _receipt(root)
    (root / CODE).write_bytes(b"return 1\r\n")                           # same text, CRLF
    assert _row(verifier.verify(root, manifest)[0]).ok


def test_a_code_digest_pattern_matching_nothing_is_a_manifest_error(tmp_path):
    root, manifest = _root(tmp_path, [_live_item()], code_digest_files=["lua/none_*.lua"])
    rows, errs = verifier.verify(root, manifest)
    assert rows == [] and any("matches no file" in e for e in errs)


def test_the_code_digest_covers_every_matched_file_in_sorted_path_order(tmp_path):
    (tmp_path / "lua").mkdir()
    (tmp_path / "lua" / "b.lua").write_bytes(b"B")
    (tmp_path / "lua" / "a.lua").write_bytes(b"A")
    expect = hashlib.sha256(b"lua/a.lua\0A\0lua/b.lua\0B\0").hexdigest()
    assert verifier.compute_code_digest(tmp_path, ["lua/b.lua", "lua/*.lua"]) == expect


# (6) closure receipts: see the closed_by tests above


# the shipped manifest


def test_the_shipped_census_lists_every_declared_obligation():
    doc = verifier.load_manifest(REPO / verifier.MANIFEST)
    declared = [i["id"] for i in doc["items"]] + [i["id"] for i in doc["open"]]
    assert sorted(doc["required_ids"]) == sorted(declared)
    for key in ("items", "open"):                                       # RED CONTROL: drop one
        mutated = json.loads(json.dumps(doc))
        mutated[key].pop()
        assert any("is absent" in e for e in verifier.manifest_errors(mutated)), key


def test_every_shipped_live_item_declares_its_scenario_and_checks():
    doc = verifier.load_manifest(REPO / verifier.MANIFEST)
    for item in (i for i in doc["items"] if i["kind"] == "LIVE"):
        receipt = json.loads((REPO / item["receipt"]).read_text(encoding="utf-8"))
        assert item["expect_scenario"] == receipt["scenario_id"], item["id"]
        assert item["expect_checks"], item["id"]


@pytest.mark.parametrize("target", ["secondary", "witness"])
@pytest.mark.parametrize("mutation", ["missing", "corrupt"])
def test_live_supporting_bytes_are_required_and_rehashed(tmp_path, target, mutation):
    root, manifest = _root(tmp_path, [_live_item()])
    _, receipt = _receipt(root)
    record = receipt["secondary_evidence"][0] if target == "secondary" else receipt["run_witness"]
    artifact = pathlib.Path(record["path"])
    if mutation == "missing":
        artifact.unlink()
    else:
        artifact.write_text("corrupted evidence", encoding="utf-8")
    rows, _ = verifier.verify(root, manifest)
    assert not _row(rows).ok, "missing/corrupt supporting evidence must revoke the LIVE verdict"


@pytest.mark.parametrize("field", ["run_witness", "secondary_evidence"])
def test_live_receipt_cannot_omit_supporting_bindings(tmp_path, field):
    root, manifest = _root(tmp_path, [_live_item()])
    _write_receipt(root, lambda doc: doc.pop(field))
    assert not _row(verifier.verify(root, manifest)[0]).ok


@pytest.mark.parametrize("field,value", [
    ("source_head", "d" * 40), ("code_digest", "f" * 64),
    ("overlay_sha1", OTHER_ROM), ("evidence_root", "unrelated-run"),
])
def test_hashed_witness_must_agree_with_execution_binding(tmp_path, field, value):
    root, manifest = _root(tmp_path, [_live_item()])
    path, receipt = _receipt(root)
    witness_path = pathlib.Path(receipt["run_witness"]["path"])
    witness = json.loads(witness_path.read_text())
    witness[field] = value
    witness_path.write_text(json.dumps(witness), encoding="utf-8")
    new_hash = hashlib.sha256(witness_path.read_bytes()).hexdigest()
    receipt["run_witness"]["sha256"] = new_hash
    receipt["secondary_evidence"][0]["run_witness"]["sha256"] = new_hash
    path.write_text(json.dumps(receipt), encoding="utf-8")
    assert not _row(verifier.verify(root, manifest)[0]).ok


@pytest.mark.parametrize("producer", [
    "RESULT: FAIL fake (1 checks failed)\n",
    '{"verdict": "FAIL", "reasons": ["native observation missing"]}',
    '{"ok": false, "reasons": ["native observation missing"]}',
    "no producer verdict",
])
def test_rehashing_a_failed_or_absent_producer_verdict_cannot_make_it_pass(tmp_path, producer):
    root, manifest = _root(tmp_path, [_live_item()])
    path, receipt = _receipt(root)
    evidence = pathlib.Path(receipt["evidence_file"])
    evidence.write_text(producer, encoding="utf-8")
    receipt["evidence_sha256"] = hashlib.sha256(evidence.read_bytes()).hexdigest()
    path.write_text(json.dumps(receipt), encoding="utf-8")
    assert not _row(verifier.verify(root, manifest)[0]).ok


@pytest.mark.parametrize("scenario", [
    "duo/play-faint-natural", "duo/play-whiteout-natural", "panel/real-host-paging",
    "trade/shipped-native-commit-cold-continue",
])
def test_producer_claim_does_not_replace_replay_observations(tmp_path, scenario):
    root, manifest = _root(tmp_path, [_live_item(expect_scenario=scenario)])
    path, receipt = _receipt(root)
    evidence = pathlib.Path(receipt["evidence_file"])
    evidence.write_text('{"verdict":"PASS","reasons":[]}', encoding="utf-8")
    receipt.update(scenario_id=scenario, evidence_sha256=hashlib.sha256(evidence.read_bytes()).hexdigest())
    path.write_text(json.dumps(receipt), encoding="utf-8")
    assert not _row(verifier.verify(root, manifest)[0]).ok, "replay observations are mandatory"


def test_receipt_duplicate_binding_cannot_shadow_the_wrong_execution(tmp_path):
    root, manifest = _root(tmp_path, [_live_item()])
    path, receipt = _receipt(root)
    encoded = json.dumps(receipt)
    path.write_text('{"code_digest":"' + "0" * 64 + '",' + encoded[1:], encoding="utf-8")
    assert not _row(verifier.verify(root, manifest)[0]).ok


def _runtime_digest_fixture(tmp_path):
    repo = pathlib.Path(__file__).resolve().parents[2]
    config = json.loads((repo / "tests/polished_release_requirements.json").read_text(encoding="utf-8"))
    additions = {"lua/gb_panel.lua", "server/adapters/polished_rom_scan.py",
                 "server/adapters/gen2_encounter_presentation.py"}
    for tree in ("lua/core", "data/games/polished_crystal"):
        additions.update(p.relative_to(repo).as_posix() for p in (repo / tree).rglob("*")
                         if p.is_file() and p.suffix.lower() not in (".md", ".txt"))
    files = set(additions)
    for pattern in config["code_digest_files"]:
        files.update(p.relative_to(repo).as_posix() for p in repo.glob(pattern) if p.is_file())
    for relative in files:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"initial\n")
    return config, additions


def test_declared_runtime_digest_covers_and_reacts_to_every_added_input(tmp_path):
    config, additions = _runtime_digest_fixture(tmp_path)
    declared = {p.relative_to(tmp_path).as_posix() for pattern in config["code_digest_files"]
                for p in tmp_path.glob(pattern) if p.is_file()}
    assert additions <= declared
    baseline = verifier.compute_code_digest(tmp_path, config["code_digest_files"],
                                            excludes=config.get("code_digest_exclude", []))
    for relative in sorted(additions):
        target = tmp_path / relative
        original = target.read_bytes()
        target.write_bytes(original + b"changed\n")
        assert verifier.compute_code_digest(tmp_path, config["code_digest_files"],
                                            excludes=config.get("code_digest_exclude", [])) != baseline, relative
        target.write_bytes(original)


@pytest.mark.parametrize("relative", [
    "server/state.py", "server/server.py", "server/adapters/base.py",
    "server/adapters/gen2_gsc.py", "lua/hud.lua",
])
def test_shared_runtime_byte_change_changes_the_polished_digest(tmp_path, relative):
    config, _ = _runtime_digest_fixture(tmp_path)
    target = tmp_path / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"initial\n")
    baseline = verifier.compute_code_digest(tmp_path, config["code_digest_files"],
                                            excludes=config.get("code_digest_exclude", []))
    target.write_bytes(b"Initial\n")
    assert verifier.compute_code_digest(tmp_path, config["code_digest_files"],
                                        excludes=config.get("code_digest_exclude", [])) != baseline, relative


def test_runtime_digest_ignores_only_pack_prose_not_runtime_bytes(tmp_path):
    config, _ = _runtime_digest_fixture(tmp_path)
    baseline = verifier.compute_code_digest(tmp_path, config["code_digest_files"],
                                            excludes=config.get("code_digest_exclude", []))
    for name in ("README.md", "notes.txt", "overlay/design.md"):
        target = tmp_path / "data/games/polished_crystal" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"prose\n")
    assert verifier.compute_code_digest(tmp_path, config["code_digest_files"],
                                        excludes=config.get("code_digest_exclude", [])) == baseline
    (tmp_path / "data/games/polished_crystal/overlay/runtime.bin").write_bytes(b"runtime\n")
    assert verifier.compute_code_digest(tmp_path, config["code_digest_files"],
                                        excludes=config.get("code_digest_exclude", [])) != baseline


@pytest.mark.parametrize("excluded", [["lua/**/*"], ["data/games/polished_crystal/**/*.json"], "bad", [None]])
def test_digest_exclusions_cannot_hide_runtime_code(tmp_path, excluded):
    root, path = _root(tmp_path, [_live_item()])
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["code_digest_exclude"] = excluded
    assert verifier.manifest_errors(doc)
