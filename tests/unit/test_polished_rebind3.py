"""MODEL packets: real frozen judges, no emulator or verdict-only stubs."""

import copy
import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from tools import verify_polished_release as v
from tools.gen2_code_digest import run_stamp
from tools.polished_live import rebind3 as b

ROOT = Path(__file__).resolve().parents[2]


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value if isinstance(value, bytes) else (json.dumps(value) + "\n").encode())
    return path


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


@pytest.fixture
def packet(tmp_path):
    root = tmp_path / "repo"
    manifest = json.loads((ROOT / v.MANIFEST).read_text())
    manifest["code_digest_files"] = ["lua/model.lua"]
    manifest["code_digest_exclude"] = []
    put(root / v.MANIFEST, manifest)
    put(root / "lua/model.lua", b"return 1\n")
    put(root / v.PROVENANCE, {"output": {"sha1": v.overlay_sha1(ROOT)}})
    put(root / "patch/dist/SLink-Polished.ups", b"MODEL")
    put(
        root / "data/polished/polished_slink.sym",
        b"00:DC00 wPartyCount\n00:DD00 wPartyMonNicknames\n02:D800 wPokeDB1UsedEntries\n02:D820 wPokeDB2UsedEntries\n",
    )
    for name in [
        "tools/verify_polished_release.py",
        "tools/polished_live/live.lua",
        "tools/polished_live/capture_contract.json",
    ]:
        put(root / name, (ROOT / name).read_bytes())
    for item in manifest["items"]:
        if item["kind"] == "LIVE":
            put(root / item["receipt"], {"old": item["id"]})
    git(root, "init", "-q")
    git(root, "config", "core.autocrlf", "false")
    git(root, "add", ".")
    git(
        root,
        "-c",
        "user.name=MODEL",
        "-c",
        "user.email=model@example.invalid",
        "commit",
        "-qm",
        "model",
    )
    cut = git(root, "rev-parse", "HEAD")
    digest = v.compute_code_digest(root, manifest["code_digest_files"])
    selected = [
        "LIVE-CAPTURE-PARTY-ONLY",
        "LIVE-EXPLODE-ACTIVE-FAINT",
        "LIVE-DUO-NATURAL-FAINT",
        "LIVE-SHIPPED-TRADE",
        "LIVE-PANEL-HOST-PAGING",
    ]
    mapping = {
        "schema": "polished-rebind3-map-v1",
        "source_head": cut,
        "code_digest": digest,
        "overlay_sha1": v.overlay_sha1(ROOT),
        "manifest_sha256": sha(root / v.MANIFEST),
        "reviewed_by": "MODEL reviewer",
        "diagnostic_subset": selected,
        "items": {},
    }
    for rid in selected:
        item = next(x for x in manifest["items"] if x["id"] == rid)
        run = tmp_path / "evidence" / rid
        run.mkdir(parents=True)
        roles = {}
        if rid == selected[0]:
            label = json.loads((root / "tools/polished_live/capture_contract.json").read_text())[
                "failed_throw_check"
            ]
            primary = put(
                run / "result.txt",
                (
                    "[live] STAGE2-catch1 throw 1 at frame 10: caught false to_pc false capture-site bank-3 hits 0\n"
                    + "[ok] STAGE2 "
                    + label
                    + "\nRESULT: PASS\n"
                ).encode(),
            )
            roles["negative"] = str(
                put(
                    run / "capture_negative.json",
                    {"failed_throw_count": 1, "capture_hits": 0, "ok": True, "throws": [
                        {"frame": 10, "caps_before": 0, "frame_end_exclusive": 11,
                         "wrong_bank_hits": 0, "caught": False, "to_pc": False, "cap_hits": 0},
                    ]},
                )
            )
        elif rid == selected[1]:
            from tests.unit.test_polished_explode_live import trace_for

            config, trace = trace_for("active-faint")
            config["provenance"]["rom_sha1"] = mapping["overlay_sha1"]
            primary = put(run / "oracle.json", {"ok": True, "config": config})
            roles["trace"] = str(put(run / "trace.json", trace))
        elif rid == selected[2]:
            from tests.unit.test_polished_duo_driver import natural_ev

            primary = put(run / "evidence.json", {"verdict": "PASS", "evidence": natural_ev()})
        elif rid == selected[3]:
            from tests.unit.test_polished_trade_duo_live import retained_model

            retained_model(run)
            primary = put(run / "verdict.json", {"verdict": "PASS"})
        else:
            rows = ["SOUL LINK", "", "PAIRS 1/1", "BADGES 0/8", "DEAD ZONES 0", "", " A-B"]
            primary = put(run / "oracle.json", {"verdict": "PASS", "server_rows": rows})
            roles["panel"] = str(
                put(
                    run / "panel_host.json",
                    {
                        "lines_per_page": 2,
                        "pair_rows": rows,
                        "client_receipts": [{"addr": "C62D", "n": 17}],
                        "driver_writes": [{} for _ in range(6)],
                        "pages": [{"page": i + 1, "pages": 4} for i in range(4)],
                    },
                )
            )
            roles["links"] = str(
                put(
                    run / "links.json",
                    {
                        "links": [
                            {
                                "status": "alive",
                                "a": {"key": "a", "nickname": "A"},
                                "b": {"key": "b", "nickname": "B"},
                            }
                        ],
                        "area_states": {},
                    },
                )
            )
            roles["status"] = str(put(run / "status.json", {"players": {"a": {"badges": 0}}}))
            roles["wire"] = str(
                put(
                    run / "wire.jsonl",
                    (
                        json.dumps(
                            {
                                "dir": "c2s",
                                "msg": {
                                    "event": "hello",
                                    "panel": True,
                                    "panel_abi": 3,
                                    "companion_abi": 3,
                                    "rom_sha1": mapping["overlay_sha1"],
                                },
                            }
                        )
                        + "\n"
                        + json.dumps(
                            {
                                "dir": "s2c",
                                "msg": {"commands": [{"cmd": "link_panel", "rows": rows}]},
                            }
                        )
                        + "\n"
                    ).encode(),
                )
            )
            roles["result"] = str(put(run / "result.txt", b"RESULT: PASS\n"))
        facts = put(
            run / "facts.json",
            {str(i): {"count": i + 1} for i in range(len(item["expect_checks"]))},
        )
        witness = put(
            tmp_path / "witnesses" / (rid + ".json"),
            {
                "evidence_root": str(run),
                "source_head": cut,
                "code_digest": digest,
                "overlay_sha1": mapping["overlay_sha1"],
            },
        )
        binding = {
            "path": str(witness),
            "sha256": sha(witness),
            "scope": str(run),
            "source_head": cut,
            "code_digest": digest,
        }
        console = put(run / "job.console.txt", b"MODEL console\n")
        launch = put(
            run / "job.LAUNCH.json",
            {
                "schema": "slink-freeze3-launch-v1",
                "source_head": cut,
                "head": cut,
                "created_utc": "2026-10-12T00:00:00Z",
                "evidence_root": str(run),
                "code_digest": digest,
                "overlay_sha1": mapping["overlay_sha1"],
                "dirty_production": [],
                "gen2_code_digest": run_stamp(root),
                "overlay_provenance_sha256": sha(root / v.PROVENANCE),
                "ups_sha256": sha(root / "patch/dist/SLink-Polished.ups"),
                "requirement_manifest_sha256": sha(root / v.MANIFEST),
                "verifier_sha256": sha(root / "tools/verify_polished_release.py"),
                "source_checkout": str(root),
                "argv": ["python", "MODEL"],
                "environment": {"PYTHONUTF8": "1"},
                "inputs": [],
                "driver_files": [
                    {"path": str(p), "sha256": sha(p)}
                    for p in sorted((root / "tools/polished_live").rglob("*"))
                    if p.is_file()
                ],
                "console": str(console),
            },
        )
        put(
            run / "job.exit.json",
            {
                "argv": ["python", "MODEL"],
                "exit_code": 0,
                "started_utc": "2026-10-12T00:00:01Z",
                "ended_utc": "2026-10-12T00:00:02Z",
                "seconds": 1,
                "witness": str(witness),
                "witness_sha256": sha(witness),
                "launch": str(launch),
                "launch_sha256": sha(launch),
            },
        )

        def record(path, binding=binding):
            return {"path": str(path), "sha256": sha(path), "run_witness": binding.copy()}

        mapping["items"][rid] = {
            "primary": record(primary),
            "secondary_evidence": [
                record(p) for p in sorted(run.rglob("*")) if p.is_file() and p != primary
            ],
            "target_sha256": sha(root / item["receipt"]),
            "checks_reviewed": True,
            "checks": [
                {
                    "name": name,
                    "assertions": [
                        {
                            "artifact": str(facts),
                            "pointer": f"/{i}/count",
                            "op": "eq",
                            "expected": i + 1,
                        }
                    ],
                }
                for i, name in enumerate(item["expect_checks"])
            ],
            "roles": roles,
            "launches": [
                {
                    "path": str(launch),
                    "argv": ["python", "MODEL"],
                    "environment": {"PYTHONUTF8": "1"},
                    "allowed_exit_codes": [0],
                }
            ],
            "rom_sha1_executed": mapping["overlay_sha1"],
            "synth": ["MODEL only"],
            "not_proven": ["No emulator execution"],
        }
    return root, put(tmp_path / "map.json", mapping), tmp_path


def targets(root):
    return {
        str(p): p.read_bytes() for p in (root / "tests/fixtures/polished/receipts").glob("*.json")
    }


def remap_single(packet, rid):
    root, path, tmp = packet
    mapping = json.loads(path.read_text())
    row = next(iter(mapping["items"].values()))
    item = next(x for x in json.loads((root / v.MANIFEST).read_text())["items"] if x["id"] == rid)
    row["target_sha256"] = sha(root / item["receipt"])
    mapping["items"] = {rid: row}
    mapping["diagnostic_subset"] = [rid]
    return mapping, row, item


@pytest.mark.parametrize(
    "attack",
    [
        "regex-disguise",
        "regex-subspan",
        "regex-padding",
        "pointer-container",
        "nested-summary",
        "pointer-alias",
        "raw-regex-alias",
    ],
)
def test_round2_summary_and_blanket_bypasses_refuse(packet, attack):
    root, path, tmp = packet
    rid = (
        "LIVE-WRITES-OVERWORLD"
        if "container" in attack or "summary" in attack
        else "LIVE-HELLO-GATE-FRAME"
    )
    if rid == "LIVE-WRITES-OVERWORLD":
        writes_packet(packet)  # valid raw diff, so no earlier gate masks this assertion regression
        mapping = json.loads(path.read_text())
        row = mapping["items"][rid]
        item = next(
            i for i in json.loads((root / v.MANIFEST).read_text())["items"] if i["id"] == rid
        )
    else:
        mapping, row, item = remap_single(packet, rid)
    facts = Path(row["checks"][0]["assertions"][0]["artifact"])
    count = len(item["expect_checks"])
    if attack in {"pointer-container", "nested-summary"}:
        value = [{"what": str(i), "ok": True, "detail": "MODEL echo"} for i in range(count)]
        put(facts, {"checks" if attack == "pointer-container" else "observations": value})
    else:
        put(facts, {str(i): {"count": 1} for i in range(count)})
    primary = Path(row["primary"]["path"])
    primary.write_bytes(b"frame 27 raw_count 1\nRESULT: PASS\n")
    row["primary"]["sha256"] = sha(primary)
    for record in row["secondary_evidence"]:
        if Path(record["path"]) == facts:
            record["sha256"] = sha(facts)
    checks = []
    for i, name in enumerate(item["expect_checks"]):
        if attack.startswith("regex") or attack == "raw-regex-alias":
            pattern = {
                "regex-disguise": r"^RESULT: P[A]SS$",
                "regex-subspan": r"P[A]SS",
                "regex-padding": r"^RESULT: P[A]SS$",
                "raw-regex-alias": r"^frame 27 raw_count 1$",
            }[attack]
            assertion = {
                "artifact": str(primary),
                "op": "regex_count",
                "pattern": pattern + "(?:)" * i,
                "expected": 1,
            }
        else:
            prefix = "checks" if attack == "pointer-container" else "observations"
            pointer = f"/{i}/count" if attack == "pointer-alias" else f"/{prefix}/{i}"
            assertion = {
                "artifact": str(facts),
                "op": "eq",
                "pointer": pointer,
                "expected": 1 if attack == "pointer-alias" else value[i],
            }
        checks.append({"name": name, "assertions": [assertion]})
    row["checks"] = checks
    put(path, mapping)
    before = targets(root)
    report = b.run(root, path, tmp / "bypass", install=True)
    assert not report["ok"], report
    assert targets(root) == before


def test_round2_leftover_recovery_does_not_prevent_rollback(packet, monkeypatch):
    root, path, tmp = packet
    replace = b.os.replace
    before = targets(root)
    for target in before:
        Path(target + ".rebind3-rollback").write_bytes(b"OLD unrelated recovery")
    calls = 0

    def fail_second(source, target):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("second candidate failed")
        return replace(source, target)

    monkeypatch.setattr(b.os, "replace", fail_second)
    report = b.run(root, path, tmp / "leftover", install=True)
    assert not report["ok"]
    assert targets(root) == before, report
    assert not report.get("unrecovered"), report


def test_round2_staged_bytes_changed_after_judgment_refuse(packet, monkeypatch):
    root, path, tmp = packet
    original = b.install_all

    def tamper(candidates):
        candidates[0][1].write_bytes(b'{"tampered":true}\n')
        return original(candidates)

    monkeypatch.setattr(b, "install_all", tamper)
    before = targets(root)
    report = b.run(root, path, tmp / "staged-race", install=True)
    assert not report["ok"], report
    assert targets(root) == before


def test_round2_failed_rollback_names_unrecovered_target(packet, monkeypatch):
    root, path, tmp = packet
    before = targets(root)
    replace = b.os.replace
    calls = 0

    def fail_install_and_restore(source, target):
        nonlocal calls
        calls += 1
        if calls >= 2:
            raise OSError("MODEL media refuses replacement")
        return replace(source, target)

    monkeypatch.setattr(b.os, "replace", fail_install_and_restore)
    report = b.run(root, path, tmp / "unrecovered", install=True)
    assert not report["ok"] and report["partial"] and not report["installed"]
    changed = {p for p, data in targets(root).items() if data != before[p]}
    assert changed == {r["target"] for r in report["unrecovered"]}
    assert len(changed) == 1 and report["rollback_complete"] is False
    for r in report["unrecovered"]:
        assert Path(r["recovery"]).read_bytes() == before[r["target"]]


def test_round2_late_staged_change_rolls_back_earlier_candidate(packet, monkeypatch):
    root, path, tmp = packet
    before = targets(root)
    replace = b.os.replace

    def late_change(source, target):
        result = replace(source, target)
        if Path(source).parent == tmp / "late":
            for staged in (tmp / "late").glob("*.json"):
                staged.write_bytes(b"{}")
        return result

    monkeypatch.setattr(b.os, "replace", late_change)
    report = b.run(root, path, tmp / "late", install=True)
    assert not report["ok"] and targets(root) == before, report


def writes_packet(packet):
    root, path, tmp = packet
    mapping, row, item = remap_single(packet, "LIVE-WRITES-OVERWORLD")
    facts = Path(row["checks"][0]["assertions"][0]["artifact"])
    put(facts, {str(i): {"count": i + 1} for i in range(len(item["expect_checks"]))})
    for record in row["secondary_evidence"]:
        if Path(record["path"]) == facts:
            record["sha256"] = sha(facts)
    row["checks"] = [
        {
            "name": name,
            "assertions": [
                {"artifact": str(facts), "pointer": f"/{i}/count", "op": "eq", "expected": i + 1}
            ],
        }
        for i, name in enumerate(item["expect_checks"])
    ]
    before = {
        "cart_hex": "00" * 32768,
        "party_start": 0xDC00,
        "party_hex": "00" * 322,
        "allocation": {str(a): 0 for start in (0x2800, 0x2820) for a in range(start, start + 26)},
    }
    operations, expected = {}, {}
    for i, leg in enumerate(("a", "b", "d", "e", "f")):
        after = copy.deepcopy(before)
        after["party_hex"] = "00" * i + "01" + "00" * (321 - i)
        expected[leg] = [f"party@{0xDC00 + i}"]
        operations[leg] = {
            "before": copy.deepcopy(before),
            "after": after,
            "changed": [{"kind": "party", "a": 0xDC00 + i}],
            "expected": expected[leg],
        }
    operations["a"]["writes"] = [
        {"op": "write_u8", "addr": 0xDC00, "v": 1, "domain": "System Bus", "frame": 50}
    ]
    trace = put(
        facts.parent / "write-trace.json",
        {"rom_sha1": mapping["overlay_sha1"], "operations": operations},
    )
    row["secondary_evidence"].append(
        {
            "path": str(trace),
            "sha256": sha(trace),
            "run_witness": row["primary"]["run_witness"].copy(),
        }
    )
    row["roles"]["trace"] = str(trace)
    row["write_expected"] = expected
    put(path, mapping)
    return trace


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "missing-operations",
        "summary-only",
        "changed-image",
        "claimed-diff",
        "expanded-allowance",
        "short-image",
        "missing-tap",
        "wrong-tap",
        "outside-tap",
    ],
)
def test_round2_writes_requires_raw_recomputed_diff(packet, fault):
    root, path, tmp = packet
    trace = writes_packet(packet)
    doc = json.loads(trace.read_text())
    if fault == "missing-operations":
        doc.pop("operations")
    elif fault == "summary-only":
        doc["operations"] = {k: {"ok": True, "detail": "diff PASS"} for k in "abdef"}
    elif fault == "changed-image":
        doc["operations"]["a"]["after"]["cart_hex"] = "01" + "00" * 32767
    elif fault == "claimed-diff":
        doc["operations"]["b"]["changed"] = []
    elif fault == "expanded-allowance":
        doc["operations"]["d"]["expected"].append("cart@0")
    elif fault == "short-image":
        doc["operations"]["e"]["before"]["cart_hex"] = "00"
        doc["operations"]["e"]["after"]["cart_hex"] = "00"
    elif fault == "missing-tap":
        doc["operations"]["a"].pop("writes")
    elif fault == "wrong-tap":
        doc["operations"]["a"]["writes"][0]["v"] = 2
    elif fault == "outside-tap":
        doc["operations"]["a"]["writes"].append(
            {"op": "write_u8", "addr": 0xDC10, "v": 0, "domain": "System Bus", "frame": 50}
        )
    put(trace, doc)
    mapping = json.loads(path.read_text())
    for record in next(iter(mapping["items"].values()))["secondary_evidence"]:
        if Path(record["path"]) == trace:
            record["sha256"] = sha(trace)
    put(path, mapping)
    before = targets(root)
    report = b.run(root, path, tmp / "writes", install=True)
    assert report["ok"] is (fault is None), report
    if fault:
        assert targets(root) == before


@pytest.mark.parametrize("mode", ["mixed", "unlisted", "wrong-scenario", "failed-replay"])
def test_round2_covered_checks_require_named_successful_replay(packet, mode):
    root, path, tmp = packet
    mapping = json.loads(path.read_text())
    rid = "LIVE-DUO-NATURAL-FAINT"
    row = mapping["items"][rid]
    name = (
        "the server marks the link dead after the kill"
        if mode != "unlisted"
        else row["checks"][0]["name"]
    )
    row["checks"] = [
        {"name": c["name"], "covered_by": "verifier_replay"} if c["name"] == name else c
        for c in row["checks"]
    ]
    if mode == "failed-replay":
        primary = Path(row["primary"]["path"])
        doc = json.loads(primary.read_text())
        doc["evidence"]["wire"]["a"].append(copy.deepcopy(doc["evidence"]["wire"]["a"][-1]))
        put(primary, doc)
        row["primary"]["sha256"] = sha(primary)
    if mode == "wrong-scenario":
        manifest_path = root / v.MANIFEST
        manifest = json.loads(manifest_path.read_text())
        next(i for i in manifest["items"] if i["id"] == rid)["expect_scenario"] = "duo/unsupported"
        put(manifest_path, manifest)
        mapping["manifest_sha256"] = sha(manifest_path)
        for r in mapping["items"].values():
            launch = Path(r["launches"][0]["path"])
            doc = json.loads(launch.read_text())
            doc["requirement_manifest_sha256"] = sha(manifest_path)
            put(launch, doc)
            end = launch.with_name("job.exit.json")
            doc = json.loads(end.read_text())
            doc["launch_sha256"] = sha(launch)
            put(end, doc)
            for record in r["secondary_evidence"]:
                if Path(record["path"]) in (launch, end):
                    record["sha256"] = sha(Path(record["path"]))
    put(path, mapping)
    before = targets(root)
    report = b.run(root, path, tmp / "covered", install=True)
    assert report["ok"] is (mode == "mixed"), report
    if mode != "mixed":
        assert targets(root) == before


def round3_map(packet, observed=None, expected=None, pattern=None, text=None, assertions=None):
    root, path, tmp = packet
    mapping, row, item = remap_single(packet, "LIVE-HELLO-GATE-FRAME")
    facts = Path(row["checks"][0]["assertions"][0]["artifact"])
    values = {str(i): {"count": i + 1} for i in range(len(item["expect_checks"]))}
    values["observation"] = observed
    put(facts, values)
    for record in row["secondary_evidence"]:
        if Path(record["path"]) == facts:
            record["sha256"] = sha(facts)
    row["checks"] = [
        {
            "name": name,
            "assertions": [
                {"artifact": str(facts), "op": "eq", "pointer": f"/{i}/count", "expected": i + 1}
            ],
        }
        for i, name in enumerate(item["expect_checks"])
    ]
    raw = put(facts.parent / "round3.txt", (text or "raw count 17\n").encode())
    row["secondary_evidence"].append(
        {"path": str(raw), "sha256": sha(raw), "run_witness": row["primary"]["run_witness"].copy()}
    )
    if assertions is not None:
        row["checks"][0]["assertions"] = assertions(str(raw), str(facts))
    elif pattern is not None:
        row["checks"][0]["assertions"] = [
            {"artifact": str(raw), "op": "regex_count", "pattern": pattern, "expected": 1}
        ]
    else:
        row["checks"][0]["assertions"] = [
            {"artifact": str(facts), "op": "eq", "pointer": "/observation", "expected": expected}
        ]
    put(path, mapping)


@pytest.mark.parametrize(
    "value,expected",
    [
        *[
            ({key: True}, {key: True})
            for key in ("passed", "success", "done", "all_pass", "outcome", "summary", "complete")
        ],
        ({"failures": []}, {"failures": []}),
        ({"packet": {"armed": True}}, {"packet": {"armed": True}}),
        ({"events": [[]]}, {"events": [[]]}),
        ([1], [True]),
    ],
)
def test_round3_item1_containers_refuse(packet, value, expected):
    root, path, tmp = packet
    round3_map(packet, value, expected)
    before = targets(root)
    report = b.run(root, path, tmp / "r3-container", install=True)
    assert not report["ok"], report
    assert targets(root) == before


@pytest.mark.parametrize(
    "value", ["RESULT: PASS #0", ["RESULT: PASS #0"], {"lines": ["RESULT: PASS #0"]}]
)
def test_round3_item2_string_summaries_refuse(packet, value):
    root, path, tmp = packet
    round3_map(packet, value, value)
    before = targets(root)
    report = b.run(root, path, tmp / "r3-string", install=True)
    assert not report["ok"], report
    assert targets(root) == before


@pytest.mark.parametrize(
    "line",
    [
        "check: passed",
        "check: PASSED",
        "check-OK",
        "check ok",
        "check=Success",
        "check: SUCCEEDED",
        "check done",
        "check ALL GOOD",
        "check: ✓",
        "check: ✔",
        "check: successful",
        "check: complete",
        "check: completed",
        "check: true",
        "STATUS captured 12",
        "RESULT captured 12",
    ],
)
def test_round3_item3_verdict_lines_refuse(packet, line):
    root, path, tmp = packet
    round3_map(packet, pattern=r"^.+$", text=line + "\n")
    before = targets(root)
    report = b.run(root, path, tmp / "r3-verdict", install=True)
    assert not report["ok"], report
    assert targets(root) == before


@pytest.mark.parametrize(
    "pattern,text",
    [
        (r"^raw 17(?=\nRESULT: P[A]SS)", "raw 17\nRESULT: PASS\n"),
        (r"^raw 17(?!\nRESULT: FAIL)", "raw 17\nRESULT: PASS\n"),
        (r"(?<=RESULT: PASS\n)raw 17", "RESULT: PASS\nraw 17\n"),
        (r"(?<!FAIL\n)raw 17", "RESULT: PASS\nraw 17\n"),
        (r"(?=(raw 17))\1", "raw 17\n"),
        (r"(?=(?P<raw>raw 17))(?P=raw)", "raw 17\n"),
    ],
)
def test_round3_item4_lookaround_refuses(packet, pattern, text):
    root, path, tmp = packet
    round3_map(packet, pattern=pattern, text=text)
    before = targets(root)
    report = b.run(root, path, tmp / "r3-lookaround", install=True)
    assert not report["ok"], report
    assert targets(root) == before


@pytest.mark.parametrize(
    "explicit,positive,accepted",
    [(False, False, False), (False, True, False), (True, False, False), (True, True, True)],
)
def test_round3_item5_absence_needs_positive_observation(packet, explicit, positive, accepted):
    root, path, tmp = packet

    def checks(raw, facts):
        absence = {"artifact": raw, "op": "regex_count", "pattern": "never-seen", "expected": 0}
        if explicit:
            absence["absence"] = True
        return [absence] + (
            [{"artifact": facts, "op": "eq", "pointer": "/0/count", "expected": 1}]
            if positive
            else []
        )

    round3_map(packet, assertions=checks)
    before = targets(root)
    report = b.run(root, path, tmp / "r3-absence", install=True)
    assert report["ok"] is accepted, report
    if not accepted:
        assert targets(root) == before


@pytest.mark.parametrize("error", [KeyboardInterrupt, SystemExit, BaseException])
@pytest.mark.parametrize("after", [True, False])
def test_round3_item6_interrupt_does_not_lose_ownership(packet, monkeypatch, error, after):
    root, path, tmp = packet
    original = b.os.replace
    fired = False

    def interrupt(source, target):
        nonlocal fired
        if not fired:
            fired = True
            if after:
                original(source, target)
            raise error("MODEL interrupted replace")
        return original(source, target)

    monkeypatch.setattr(b.os, "replace", interrupt)
    before = targets(root)
    report = b.run(root, path, tmp / "r3-interrupt", install=True)
    assert not report["ok"], report
    assert targets(root) == before, report
    assert report["rollback_complete"] and not report["unrecovered"], report


@pytest.mark.parametrize("value", [True, False, {"coordinates": [12, 31]}])
def test_round3_scalar_boolean_and_numeric_container_remain_valid(packet, value):
    root, path, tmp = packet
    round3_map(packet, value, value)
    report = b.run(root, path, tmp / "r3-positive", install=True)
    assert report["ok"], report


def test_round3_absence_fingerprint_keeps_pattern_identity(packet):
    root, path, tmp = packet

    def checks(raw, facts):
        return [
            {
                "artifact": raw,
                "op": "regex_count",
                "pattern": "missing-one",
                "expected": 0,
                "absence": True,
            },
            {"artifact": facts, "op": "eq", "pointer": "/0/count", "expected": 1},
        ]

    round3_map(packet, assertions=checks)
    mapping = json.loads(path.read_text())
    row = next(iter(mapping["items"].values()))
    row["checks"][1]["assertions"] = copy.deepcopy(row["checks"][0]["assertions"])
    row["checks"][1]["assertions"][0]["pattern"] = "missing-two"
    put(path, mapping)
    report = b.run(root, path, tmp / "r3-absence-identities", install=True)
    assert report["ok"], report


@pytest.mark.parametrize("mode", ["valid", "alone", "unproven-sibling", "fail-line", "non-verdict"])
def test_round3_producer_completion_needs_proved_siblings(packet, mode):
    root, path, tmp = packet
    round3_map(packet, 17, 17)
    mapping = json.loads(path.read_text())
    row = next(iter(mapping["items"].values()))
    index = 1 if mode == "non-verdict" else 0
    row["checks"][index] = {
        "name": row["checks"][index]["name"],
        "covered_by": "producer_completion",
    }
    if mode == "unproven-sibling":
        row["checks"][1]["assertions"][0]["expected"] = 999
    elif mode == "fail-line":
        primary = Path(row["primary"]["path"])
        primary.write_bytes(primary.read_bytes() + b"  [FAIL] a producer check failed\n")
        row["primary"]["sha256"] = sha(primary)
    elif mode == "alone":
        row["checks"] = row["checks"][:1]
        manifest_path = root / v.MANIFEST
        manifest = json.loads(manifest_path.read_text())
        next(i for i in manifest["items"] if i["id"] == "LIVE-HELLO-GATE-FRAME")[
            "expect_checks"
        ] = ["run verdict"]
        put(manifest_path, manifest)
        mapping["manifest_sha256"] = sha(manifest_path)
        launch = Path(row["launches"][0]["path"])
        doc = json.loads(launch.read_text())
        doc["requirement_manifest_sha256"] = sha(manifest_path)
        put(launch, doc)
        end = launch.with_name("job.exit.json")
        doc = json.loads(end.read_text())
        doc["launch_sha256"] = sha(launch)
        put(end, doc)
        for record in row["secondary_evidence"]:
            if Path(record["path"]) in (launch, end):
                record["sha256"] = sha(Path(record["path"]))
    put(path, mapping)
    before = targets(root)
    report = b.run(root, path, tmp / "completion", install=True)
    assert report["ok"] is (mode == "valid"), report
    if mode != "valid":
        assert targets(root) == before


@pytest.mark.parametrize("mode", ["valid", "failed-leg", "same-leg", "fail-echo"])
def test_round3_producer_completion_binds_each_r2_leg(packet, mode):
    root, path, tmp = packet
    mapping, row, item = remap_single(packet, "LIVE-R2-RANDOMIZED-BOOT")
    home = Path(row["primary"]["path"]).parent

    def record(p):
        return {
            "path": str(p),
            "sha256": sha(p),
            "run_witness": row["primary"]["run_witness"].copy(),
        }

    facts = put(home / "r2-facts.json", {str(i): i + 10 for i in range(len(item["expect_checks"]))})
    row["secondary_evidence"].append(record(facts))
    row["checks"] = [
        {"name": name, "covered_by": "producer_completion"}
        if i < 2
        else {
            "name": name,
            "assertions": [
                {"artifact": str(facts), "pointer": f"/{i}", "op": "eq", "expected": i + 10}
            ],
        }
        for i, name in enumerate(item["expect_checks"])
    ]
    b_text = b"RESULT: FAIL\n" if mode == "failed-leg" else b"RESULT: PASS\n"
    if mode == "fail-echo":
        b_text += b"[FAIL] missed required observation\n"
    leg_b = put(home / "r2b.txt", b_text)
    row["secondary_evidence"].append(record(leg_b))
    row["roles"]["producer_r2a"] = row["primary"]["path"]
    row["roles"]["producer_r2b"] = str(leg_b) if mode != "same-leg" else row["primary"]["path"]
    players, bindings = {}, {}
    for player in ("a", "b"):
        rom = put(home / (player + ".gbc"), ("MODEL " + player).encode())
        h = hashlib.sha1(rom.read_bytes()).hexdigest()
        observation = put(home / (player + ".json"), {"rom_sha1": h})
        players[player] = {"rom_sha1": h}
        bindings[player] = {
            "rom": str(rom),
            "observation": str(observation),
            "sha1_pointer": "/rom_sha1",
        }
        row["secondary_evidence"] += [record(rom), record(observation)]
    contract = put(home / "contract.json", {"players": players})
    row["secondary_evidence"].append(record(contract))
    for binding in bindings.values():
        binding["contract"] = str(contract)
    row["rom_bindings"] = bindings
    row["rom_sha1_executed"] = players["a"]["rom_sha1"]
    put(path, mapping)
    before = targets(root)
    report = b.run(root, path, tmp / "r2-completion", install=True)
    assert report["ok"] is (mode == "valid"), report
    if mode != "valid":
        assert targets(root) == before


def test_valid_five_families_stage_then_install(packet):
    root, path, tmp = packet
    before = targets(root)
    report = b.run(root, path, tmp / "stage")
    assert report["ok"], report
    assert report["partial"] and not report["installed"] and targets(root) == before
    report = b.run(root, path, tmp / "install", install=True)
    assert report["ok"] and report["installed"], report
    assert targets(root) != before


@pytest.mark.parametrize(
    "mutation",
    [
        "missing-secondary",
        "changed-secondary",
        "missing-witness",
        "changed-witness",
        "witness-alias",
        "scope",
        "failed-throw",
        "duplicate-event",
        "hook-hp",
        "collateral",
        "missing-page",
        "milestone-order",
        "cold-record",
        "duplicate-json",
        "generic-pass",
        "case-substitution",
    ],
)
def test_mutations_never_change_receipts(packet, mutation):
    root, path, tmp = packet
    m = json.loads(path.read_text())
    rows = m["items"]
    row = rows["LIVE-CAPTURE-PARTY-ONLY"]
    changed = None
    if mutation == "missing-secondary":
        row["secondary_evidence"] = [
            r for r in row["secondary_evidence"] if not r["path"].endswith("capture_negative.json")
        ]
    elif mutation == "changed-secondary":
        Path(row["roles"]["negative"]).write_bytes(b"{}")
    elif mutation in ("missing-witness", "changed-witness", "witness-alias"):
        w = Path(row["primary"]["run_witness"]["path"])
        if mutation == "missing-witness":
            w.unlink()
        elif mutation == "changed-witness":
            w.write_bytes(b"{}")
        else:
            doc = json.loads(w.read_text())
            doc["sourceHead"] = doc.pop("source_head")
            put(w, doc)
            for r in [row["primary"], *row["secondary_evidence"]]:
                r["run_witness"]["sha256"] = sha(w)
    elif mutation == "scope":
        row["primary"]["run_witness"]["scope"] = str(tmp)
    elif mutation == "failed-throw":
        changed = Path(row["primary"]["path"])
        changed.write_bytes(changed.read_bytes().replace(b"caught false", b"caught true"))
    elif mutation in ("duplicate-event", "hook-hp", "collateral"):
        row = rows["LIVE-DUO-NATURAL-FAINT"]
        changed = Path(row["primary"]["path"])
        doc = json.loads(changed.read_text())
        ev = doc["evidence"]
        if mutation == "duplicate-event":
            ev["wire"]["a"].append(copy.deepcopy(ev["wire"]["a"][-1]))
        elif mutation == "hook-hp":
            for mon in ev["steps"]["a"]["battle"]["site_hits"]["faint_copyback"][0]["party"]:
                mon["hp"] = 1
        else:
            ev["steps"]["b"]["await"]["snapshot"]["party"][0]["record_hex"] = "ff" * 48
        put(changed, doc)
    elif mutation == "missing-page":
        row = rows["LIVE-PANEL-HOST-PAGING"]
        changed = Path(row["roles"]["panel"])
        doc = json.loads(changed.read_text())
        doc["pages"].pop()
        put(changed, doc)
    elif mutation in ("milestone-order", "cold-record"):
        row = rows["LIVE-SHIPPED-TRADE"]
        home = Path(row["primary"]["path"]).parent
        if mutation == "milestone-order":
            changed = home / "a/g1/milestones.jsonl"
            doc = [json.loads(line) for line in changed.read_text().splitlines()]
            doc[1]["frame"] = 1000
            put(changed, ("".join(json.dumps(x) + "\n" for x in doc)).encode())
        else:
            changed = home / "a/g2/ready.json"
            doc = json.loads(changed.read_text())
            raw = bytearray.fromhex(doc["party"][-1]["blob_hex"])
            raw[2] ^= 1
            doc["party"][-1]["blob_hex"] = raw.hex()
            put(changed, doc)
    elif mutation == "case-substitution":
        from tests.unit.test_polished_explode_live import trace_for

        row = rows["LIVE-EXPLODE-ACTIVE-FAINT"]
        config, trace = trace_for("bench-faint")
        config["provenance"]["rom_sha1"] = m["overlay_sha1"]
        changed = Path(row["primary"]["path"])
        put(changed, {"ok": True, "config": config})
        trace_path = Path(row["roles"]["trace"])
        put(trace_path, trace)
        for record in row["secondary_evidence"]:
            if Path(record["path"]) == trace_path:
                record["sha256"] = sha(trace_path)
    elif mutation == "duplicate-json":
        row = rows["LIVE-EXPLODE-ACTIVE-FAINT"]
        changed = Path(row["primary"]["path"])
        changed.write_bytes(b'{"ok":false,"ok":true}')
    else:
        row["checks"] = []
    if changed:
        for r in [row["primary"], *row["secondary_evidence"]]:
            if Path(r["path"]) == changed:
                r["sha256"] = sha(changed)
    put(path, m)
    before = targets(root)
    report = b.run(root, path, tmp / "bad", install=True)
    assert not report["ok"], report
    assert targets(root) == before


@pytest.mark.parametrize("change", ["rewrite", "add", "remove"])
def test_evidence_race_at_real_judge_entry_refuses(packet, monkeypatch, change):
    root, path, tmp = packet
    before = targets(root)
    original = v.check_live

    def raced(item, *args, **kwargs):
        receipt = json.loads(Path(item["receipt"]).read_text())
        artifact = Path(receipt["evidence_file"]).parent / "facts.json"
        if change == "rewrite":
            put(artifact, {"changed": True})
        elif change == "add":
            put(artifact.with_name("intruder.json"), {})
        else:
            artifact.unlink()
        return original(item, *args, **kwargs)

    monkeypatch.setattr(v, "check_live", raced)
    report = b.run(root, path, tmp / "race", install=True)
    assert not report["ok"], report
    assert targets(root) == before


def test_partial_install_failure_rolls_back(packet, monkeypatch):
    root, path, tmp = packet
    before = targets(root)
    replace = b.os.replace
    calls = 0

    def fail_second(source, target):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("MODEL installation interruption")
        return replace(source, target)

    monkeypatch.setattr(b.os, "replace", fail_second)
    report = b.run(root, path, tmp / "rollback", install=True)
    assert not report["ok"] and calls > 2, report
    assert targets(root) == before
    assert not list(root.rglob("*.rebind3-rollback"))


def test_target_race_preserves_foreign_edit(packet, monkeypatch):
    root, path, tmp = packet
    original = b.install_all
    changed = []

    def raced(candidates):
        target = candidates[0][0]
        put(target, {"foreign": "edit"})
        changed.append(target)
        return original(candidates)

    monkeypatch.setattr(b, "install_all", raced)
    before = targets(root)
    report = b.run(root, path, tmp / "target-race", install=True)
    assert not report["ok"], report
    before[str(changed[0])] = changed[0].read_bytes()
    assert targets(root) == before


def test_receipt_only_commit_allowed_but_runtime_edit_refused(packet):
    root, path, tmp = packet
    report = b.run(root, path, tmp / "first-install", install=True)
    assert report["ok"], report
    git(root, "add", "tests/fixtures/polished/receipts")
    git(
        root,
        "-c",
        "user.name=MODEL",
        "-c",
        "user.email=model@example.invalid",
        "commit",
        "-qm",
        "metadata",
    )
    mapping = json.loads(path.read_text())
    manifest = json.loads((root / v.MANIFEST).read_text())
    for item in manifest["items"]:
        if item["id"] in mapping["items"]:
            mapping["items"][item["id"]]["target_sha256"] = sha(root / item["receipt"])
    put(path, mapping)
    report = b.run(root, path, tmp / "metadata")
    assert report["ok"], report
    (root / "lua/model.lua").write_bytes(b"return 2\n")
    before = targets(root)
    report = b.run(root, path, tmp / "runtime", install=True)
    assert not report["ok"] and targets(root) == before


@pytest.mark.parametrize(
    "change",
    ["full-census", "unknown-id", "grade", "argv", "no-launch", "duplicate-map", "target-hash"],
)
def test_binding_controls(packet, change):
    root, path, tmp = packet
    mapping = json.loads(path.read_text())
    row = next(iter(mapping["items"].values()))
    if change == "full-census":
        mapping.pop("diagnostic_subset")
    elif change == "unknown-id":
        mapping["items"]["LIVE-INVENTED"] = row
    elif change == "grade":
        row["grade"] = "PHYSICAL"
    elif change == "argv":
        row["launches"][0]["argv"] = ["other"]
    elif change == "no-launch":
        row["launches"] = []
    elif change == "target-hash":
        row["target_sha256"] = "0" * 64
    put(path, mapping)
    if change == "duplicate-map":
        raw = path.read_bytes()
        path.write_bytes(raw.replace(b'"reviewed_by":', b'"reviewed_by":"alias", "reviewed_by":'))
    before = targets(root)
    report = b.run(root, path, tmp / "binding", install=True)
    assert not report["ok"], report
    assert targets(root) == before


@pytest.mark.parametrize("wrong", [False, True])
def test_randomized_roms_bind_actual_per_player_bytes(tmp_path, wrong):
    records = []
    bindings = {}
    expected = {}
    for player in ("a", "b"):
        rom = put(tmp_path / (player + ".gbc"), ("MODEL distinct ROM " + player).encode())
        want = hashlib.sha1(rom.read_bytes()).hexdigest()
        expected[player] = {"rom_sha1": want}
        obs = put(tmp_path / (player + ".json"), {"hello": {"rom_sha1": want}})
        bindings[player] = {
            "rom": str(rom),
            "observation": str(obs),
            "sha1_pointer": "/hello/rom_sha1",
        }
        records.extend([rom, obs])
    contract = put(tmp_path / "contract.json", {"players": expected})
    for binding in bindings.values():
        binding["contract"] = str(contract)
    primary = put(tmp_path / "primary.json", {"verdict": "PASS"})
    records.extend([contract, primary])
    row = {
        "primary": {"path": str(primary)},
        "rom_bindings": bindings,
        "rom_sha1_executed": "0" * 40 if wrong else expected["a"]["rom_sha1"],
    }
    if wrong:
        with pytest.raises(ValueError, match="substituted source overlay"):
            b.replay_extra(
                {"id": "LIVE-R1-MANAGER-PAIR", "bind": "source"},
                row,
                set(records),
                tmp_path,
                b.Snapshot(),
            )
    else:
        b.replay_extra(
            {"id": "LIVE-R1-MANAGER-PAIR", "bind": "source"},
            row,
            set(records),
            tmp_path,
            b.Snapshot(),
        )


@pytest.fixture
def refusals(tmp_path):
    """MODEL raw wire/log/write audit, including fresh per-case allocations."""
    root = tmp_path / "repo"
    beacon = put(
        root / "data/games/polished_crystal/overlay/beacon.json",
        {"schema": "polished-overlay-beacon-v1", "spans": [{"offset": 400, "length": 8}]},
    )
    cases, records = {}, []

    def record(path, scope):
        records.append({"path": str(path), "run_witness": {"scope": str(scope)}})
        return str(path)

    for name in ("overlay", "swap", "flip14e", "flipbeacon"):
        run = tmp_path / name
        hello = {"dir": "c2s", "msg": {"event": "hello"}}
        commands = (
            [{"cmd": "config"}] if name == "overlay" else [{"cmd": "noop", "refused": "admission"}]
        )
        wire = (
            []
            if name == "flipbeacon"
            else [
                hello,
                {"dir": "s2c", "msg": {"commands": commands}},
                {"dir": "s2c", "msg": {"commands": [{"cmd": "noop"}]}},
            ]
        )
        reason = "MODEL named refusal: " + name
        log = put(run / "client.log", (reason + "\n").encode())
        cases[name] = {
            "wire": record(
                put(run / "wire.jsonl", "".join(json.dumps(r) + "\n" for r in wire).encode()), run
            ),
            "client_log": record(log, run),
            "writes": record(put(run / "writes.json", []), run),
            "reason": reason,
            "refusal_log": str(log),
        }
        if name == "flipbeacon":
            cases[name]["refusal"] = record(
                put(
                    run / "refusal.json",
                    {
                        "schema": "polished-live-refusal-v1",
                        "refused_by": "client",
                        "reason": reason,
                        "frame": 1,
                        "lua_writes": 0,
                    },
                ),
                run,
            )
    setup = tmp_path / "setup"
    source = put(setup / "original.gbc", b"\0" * 512)
    record(source, setup)
    mutations = []
    for name, at in (("flip14e", 0x14E), ("flipbeacon", 403)):
        data = bytearray(source.read_bytes())
        data[at] = 1
        path = put(setup / (name + ".gbc"), bytes(data))
        record(path, setup)
        mutations.append(
            {
                "path": str(path),
                "offset": at,
                "old": 0,
                "new": 1,
                "sha1": hashlib.sha1(data).hexdigest(),
            }
        )
    mutation = put(
        setup / "mutations.json",
        {
            "source": str(source),
            "source_sha1": hashlib.sha1(source.read_bytes()).hexdigest(),
            "beacon_sha256": sha(beacon),
            "mutations": mutations,
        },
    )
    record(mutation, setup)
    overlay_sha1 = hashlib.sha1(source.read_bytes()).hexdigest()
    put(root / v.PROVENANCE, {"output": {"sha1": overlay_sha1}})
    for name, case in cases.items():
        rom = setup / (name + ".gbc") if name in {"flip14e", "flipbeacon"} else source
        case["rom"] = str(rom)
        case["player"] = "a"
        pinned = "f" * 40 if name == "swap" else overlay_sha1
        case["contract"] = record(
            put(tmp_path / name / "contract.json", {"players": {"a": {"rom_sha1": pinned}}}),
            tmp_path / name,
        )
        wire = Path(case["wire"])
        rows = [json.loads(line) for line in wire.read_text().splitlines()]
        for entry in rows:
            if entry["dir"] == "c2s":
                entry["msg"]["rom_sha1"] = hashlib.sha1(rom.read_bytes()).hexdigest()
        put(wire, "".join(json.dumps(r) + "\n" for r in rows).encode())
    row = {
        "primary": records[0],
        "secondary_evidence": records[1:],
        "r3_cases": cases,
        "roles": {"mutations": str(mutation)},
        "rom_sha1_executed": overlay_sha1,
    }
    bound = {Path(r["path"]).resolve() for r in records}
    return root, row, bound


def test_r3_valid_raw_aggregate_uses_generated_beacon(refusals):
    root, row, bound = refusals
    b.replay_extra({"id": "LIVE-R3-REFUSALS"}, row, bound, root, b.Snapshot())


@pytest.mark.parametrize(
    "audit,zero", [([], True), ({}, True), ({"write": 1}, False), (["w"], False), (None, False)]
)
def test_r3_write_audit_lua_empty_table_contract(refusals, audit, zero):
    root, row, bound = refusals
    put(Path(row["r3_cases"]["swap"]["writes"]), audit)
    if zero:
        b.replay_extra({"id": "LIVE-R3-REFUSALS"}, row, bound, root, b.Snapshot())
    else:
        with pytest.raises(ValueError):
            b.replay_extra({"id": "LIVE-R3-REFUSALS"}, row, bound, root, b.Snapshot())


@pytest.mark.parametrize(
    "mutation",
    [
        "missing-case",
        "refused-control",
        "accepted-mutant",
        "wrong-reason",
        "writes",
        "wrong-beacon",
        "null-reason",
        "no-refusal",
    ],
)
def test_r3_raw_negatives(refusals, mutation):
    root, row, bound = refusals
    if mutation == "missing-case":
        row["r3_cases"].pop("swap")
    elif mutation in ("refused-control", "accepted-mutant"):
        name = "overlay" if mutation == "refused-control" else "swap"
        path = Path(row["r3_cases"][name]["wire"])
        records = [json.loads(line) for line in path.read_text().splitlines()]
        records[1]["msg"]["commands"] = (
            [{"cmd": "noop", "refused": "admission"}] if name == "overlay" else [{"cmd": "config"}]
        )
        put(path, "".join(json.dumps(r) + "\n" for r in records).encode())
    elif mutation == "wrong-reason":
        row["r3_cases"]["swap"]["reason"] = "some other refusal reason"
    elif mutation == "writes":
        put(Path(row["r3_cases"]["flipbeacon"]["writes"]), [{"addr": 1}])
    elif mutation == "wrong-beacon":
        path = Path(row["roles"]["mutations"])
        doc = json.loads(path.read_text())
        doc["beacon_sha256"] = "0" * 64
        put(path, doc)
    elif mutation == "null-reason":
        path = Path(row["r3_cases"]["flipbeacon"]["refusal"])
        doc = json.loads(path.read_text())
        doc["reason"] = None
        put(path, doc)
    else:
        bound.remove(Path(row["r3_cases"]["flipbeacon"]["refusal"]))
    with pytest.raises(ValueError):
        b.replay_extra({"id": "LIVE-R3-REFUSALS"}, row, bound, root, b.Snapshot())
