"""Verifier judgments over real hashed evidence; MODEL observations, no emulator."""
import hashlib
import json
from pathlib import Path

import pytest

from tests.unit import test_verify_polished_release as fixtures

verifier = fixtures.verifier
FAILED_THROW = "an observed failed throw does not fire the capture site"
CAPTURE_TEXT = f"[ok] STAGE2 {FAILED_THROW}\nRESULT: PASS capture\n"


def put(path, value):
    raw = value.encode() if isinstance(value, str) else json.dumps(value).encode()
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


@pytest.fixture
def packet(tmp_path):
    item = fixtures._live_item(expect_scenario="live/capture-party-only",
                               expect_checks=["run verdict", FAILED_THROW])
    root, _ = fixtures._root(tmp_path, [item])
    receipt_path, doc = fixtures._receipt(root)
    doc.update(scenario_id=item["expect_scenario"],
               checks=[{"name": name, "result": "PASS"} for name in item["expect_checks"]])
    doc["evidence_sha256"] = put(Path(doc["evidence_file"]), CAPTURE_TEXT)
    negative = root / "lane/capture_negative.json"
    digest = put(negative, {"ok": True, "failed_throw_count": 1, "capture_hits": 0,
                            "throws": [{"caught": False, "cap_hits": 0},
                                       {"caught": True, "cap_hits": 1}]})
    doc["secondary_evidence"] = [{"path": str(negative), "sha256": digest,
                                  "run_witness": doc["run_witness"]}]
    return root, item, receipt_path, doc


def judge(packet):
    root, item, receipt_path, doc = packet
    put(receipt_path, doc)
    return verifier.check_live(item, root, fixtures.ROM, doc["code_digest"])


def test_observed_failed_throw_and_successful_capture_pass(packet):
    assert judge(packet).ok


@pytest.mark.parametrize("primary", ['{"verdict":"PASS","reasons":[]}',
                                     '{"ok":true,"failed_throw_count":0,"capture_hits":0}'])
def test_capture_json_cannot_bypass_its_text_producer_contract(packet, primary):
    doc = packet[3]
    doc["evidence_sha256"] = put(Path(doc["evidence_file"]), primary)
    row = judge(packet)
    assert not row.ok
    assert any("capture primary must be text" in why for why in row.reasons)


def test_capture_text_without_negative_label_is_refused(packet):
    doc = packet[3]
    doc["evidence_sha256"] = put(Path(doc["evidence_file"]), "RESULT: PASS capture\n")
    row = judge(packet)
    assert not row.ok
    assert any("failed-throw" in why for why in row.reasons)


@pytest.mark.parametrize("fault", ["absent", "no-failed-throws", "failed-capture-hit",
                                  "negative-hits", "missing-caught", "missing-hits",
                                  "duplicate-observation-file"])
def test_capture_rejudges_bound_throw_observations_not_the_summary(packet, fault):
    doc = packet[3]
    record = doc["secondary_evidence"][0]
    observations = {"ok": True, "failed_throw_count": 1, "capture_hits": 0,
                    "throws": [{"caught": False, "cap_hits": 0}]}
    if fault == "absent":
        record["path"] = str(Path(record["path"]).with_name("unrelated.json"))
    elif fault == "no-failed-throws":
        observations["throws"] = [{"caught": True, "cap_hits": 1}]
    elif fault == "failed-capture-hit":
        observations["throws"][0]["cap_hits"] = 1
    elif fault == "negative-hits":
        observations["throws"][0]["cap_hits"] = -1
    elif fault == "missing-caught":
        observations["throws"][0].pop("caught")
    elif fault == "missing-hits":
        observations["throws"][0].pop("cap_hits")
    else:
        directory = Path(record["path"]).parent / "second"
        directory.mkdir()
        other = directory / "capture_negative.json"
        other_hash = put(other, {**observations, "second": True})
        doc["secondary_evidence"].append({"path": str(other), "sha256": other_hash,
                                          "run_witness": doc["run_witness"]})
    record["sha256"] = put(Path(record["path"]), observations)
    row = judge(packet)
    assert not row.ok
    assert any("capture replay" in why for why in row.reasons)


@pytest.mark.parametrize("scenario", ["title/wordmark-vs-clean-control", "r1/manager-randomized-pair"])
def test_text_pass_cannot_skip_json_only_scenario_checks(packet, scenario):
    packet[1]["expect_scenario"] = scenario
    packet[3]["scenario_id"] = scenario
    packet[3]["evidence_sha256"] = put(Path(packet[3]["evidence_file"]), "RESULT: PASS fake\n")
    row = judge(packet)
    assert not row.ok
    assert any("primary must be JSON" in why for why in row.reasons)


def generic(packet):
    packet[1]["expect_scenario"] = "fake/scenario"
    packet[3]["scenario_id"] = "fake/scenario"
    return packet[3]


def rewrite_witness(doc, mutate):
    record = doc["run_witness"]
    path = Path(record["path"])
    body = json.loads(path.read_text())
    mutate(body)
    record["sha256"] = put(path, body)


@pytest.mark.parametrize("fault", ["empty-root", "relative-root", "relative-scope", "relative-path",
                                  "descriptor-head-alias", "document-extra", "document-head-conflict"])
def test_witness_requires_literal_paths_and_exact_unambiguous_fields(packet, monkeypatch, fault):
    doc = generic(packet)
    record = doc["run_witness"]
    monkeypatch.chdir(record["scope"])
    if fault in ("empty-root", "relative-root"):
        rewrite_witness(doc, lambda body: body.update(evidence_root="" if fault == "empty-root" else "."))
    elif fault == "relative-scope":
        record["scope"] = "."
    elif fault == "relative-path":
        record["path"] = Path(record["path"]).name
    elif fault == "descriptor-head-alias":
        record["head"] = "d" * 40
    elif fault == "document-extra":
        rewrite_witness(doc, lambda body: body.update(verdict="PASS"))
    else:
        rewrite_witness(doc, lambda body: body.update(head="d" * 40))
    row = judge(packet)
    assert not row.ok
    assert any("witness" in why for why in row.reasons)


def test_optional_witness_head_may_be_absent(packet):
    doc = generic(packet)
    rewrite_witness(doc, lambda body: body.pop("head"))
    assert judge(packet).ok


@pytest.mark.parametrize("pair", ["primary-secondary", "primary-witness", "secondary-witness",
                                 "secondary-secondary"])
@pytest.mark.parametrize("identity", ["path", "sha256"])
def test_evidence_alias_policy_preserves_primary_isolation(packet, pair, identity):
    doc = generic(packet)
    support = doc["secondary_evidence"][0]
    witness = doc["run_witness"]
    primary = Path(doc["evidence_file"])
    if pair == "primary-secondary":
        target = primary if identity == "path" else primary.with_name("copied-primary.txt")
        target.write_bytes(primary.read_bytes())
        support.update(path=str(target), sha256=doc["evidence_sha256"])
    elif pair == "primary-witness":
        body = json.loads(Path(witness["path"]).read_text())
        body.update(verdict="PASS")
        doc["evidence_sha256"] = put(primary, body)
        target = primary if identity == "path" else primary.with_name("copied-witness.json")
        target.write_bytes(primary.read_bytes())
        witness.update(path=str(target), sha256=doc["evidence_sha256"])
    elif pair == "secondary-witness":
        original = Path(witness["path"])
        target = original if identity == "path" else original.with_name("copied-support.json")
        target.write_bytes(original.read_bytes())
        support.update(path=str(target), sha256=witness["sha256"])
    else:
        original = Path(support["path"])
        target = original if identity == "path" else original.with_name("copied-support.json")
        target.write_bytes(original.read_bytes())
        doc["secondary_evidence"].append({"path": str(target), "sha256": support["sha256"],
                                          "run_witness": witness})
    row = judge(packet)
    if identity == "sha256" and pair in ("secondary-secondary", "secondary-witness"):
        assert row.ok, row.reasons
    else:
        assert not row.ok
        assert any("alias" in why for why in row.reasons)


@pytest.mark.parametrize("empty_audit", ["[]", "{}"])
def test_independent_empty_supporting_audits_are_valid(packet, empty_audit):
    doc = generic(packet)
    support = doc["secondary_evidence"][0]
    original = Path(support["path"])
    support["sha256"] = put(original, empty_audit)
    independent = original.with_name("independent-empty-audit.json")
    independent.write_bytes(original.read_bytes())
    doc["secondary_evidence"].append({"path": str(independent), "sha256": support["sha256"],
                                      "run_witness": doc["run_witness"]})
    row = judge(packet)
    assert row.ok, row.reasons


@pytest.mark.parametrize("relative", ["lua/gen2/run.lua", "lua/gen2/new_seam/reads.lua"])
def test_whole_gen2_tree_is_bound_to_execution_digest(tmp_path, relative):
    config, _ = fixtures._runtime_digest_fixture(tmp_path)
    target = tmp_path / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"qualified behavior\n")
    baseline = verifier.compute_code_digest(tmp_path, config["code_digest_files"],
                                            excludes=config.get("code_digest_exclude", []))
    target.write_bytes(b"changed runtime behavior\n")
    assert verifier.compute_code_digest(tmp_path, config["code_digest_files"],
                                         excludes=config.get("code_digest_exclude", [])) != baseline


def test_secondary_descriptor_rejects_unbound_alias_fields(packet):
    doc = generic(packet)
    doc["secondary_evidence"][0]["head"] = "d" * 40
    row = judge(packet)
    assert not row.ok
    assert any("secondary_evidence" in why and "exact fields" in why for why in row.reasons)
