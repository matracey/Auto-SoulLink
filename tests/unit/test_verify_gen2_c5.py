"""verify_gen2_release.py --c5: the C-5 randomized duo evidence reader. MODEL trees built in tmp_path, never receipts."""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))

import verify_gen2_release as gate  # noqa: E402

HEAD = "d" * 64
JAR = "j" * 64
SHA = {"a": "1" * 40, "b": "2" * 40}


def _write(path: Path, text: str) -> str:
    path.write_text(text, encoding="utf-8", newline="\n")
    return hashlib.sha256(text.encode()).hexdigest()


def _side(side: str, sha: str = None, kind: str = "rand_overlay", verdict: str = "RESULT: PASS (ok)") -> str:
    sha = sha or SHA[side[0]]
    return (f'DUO_GEN2 {{"rom_sha1": "{sha}"}}\nCLIENT {{"artifact_kind": "{kind}", "rom_sha1": "{sha}"}}\n'
            f"{verdict}\n")


def _pydec(digest: str = HEAD, dirty=()) -> str:
    stamp = {"schema": "gen2-code-digest-v1", "digest": digest, "commit": "MODEL", "dirty": list(dirty)}
    return f"CODE_DIGEST {json.dumps(stamp)}\nPYDEC: PASS a=x\n"


def _cell(folder: Path, cid: str, texts: dict | None = None, **overrides) -> Path:
    _, pair, scenario = cid.split("/")
    prefix = f"duo_{scenario.removeprefix('gen2_')}_{pair}_"
    sides = ["a", "b"] + (["a_initial", "a_same_save", "a_wrong_save"] if scenario == "gen2_reconnect" else [])
    bodies = {side: _side(side) for side in sides}
    bodies["pydec"] = _pydec()
    bodies.update(texts or {})
    receipts = {f"receipts/c5/{prefix}{side}_result.txt": _write(folder / f"{prefix}{side}_result.txt", body)
                for side, body in bodies.items()}
    manifest = {"schema": gate.C5_SCHEMA, "receipt_marker": gate.C5_MARKER, "evidence_level": "PHYSICAL",
                "cell": cid, "covers": gate.C5_CELLS[cid], "pair": pair, "scenario": scenario, "code_digest": HEAD,
                "contract": {"players": {s: {"rom_sha1": SHA[s]} for s in "ab"}},
                "provision": {"jar_sha256": JAR, "players": {s: {"rom_sha1": SHA[s], "kind": "rand_companion"}
                                                              for s in "ab"}},
                "receipts": receipts,
                "disclosures": {"SYNTH": ["fixture RTC trailer"], "HARNESS": ["c5_runner _shim"],
                                "NATIVE": "the randomized cartridge runs natively"}}
    manifest.update(overrides)
    path = folder / f"{cid.replace('/', '__')}.manifest.json"
    _write(path, json.dumps(manifest, indent=1) + "\n")
    return path


def _tree(tmp_path: Path) -> tuple[Path, Path]:
    tmp_path = tmp_path / "model"   # tmp_path itself may hold conftest output
    (tmp_path / "tests").mkdir(parents=True)
    shutil.copyfile(REPO / gate.C5_REQUIREMENTS, tmp_path / gate.C5_REQUIREMENTS)
    (tmp_path / "data").mkdir()
    (tmp_path / "data/upr_jars.json").write_text(json.dumps({"MODEL jar": JAR}), encoding="utf-8")
    folder = tmp_path / "c5"
    folder.mkdir()
    for cid in gate.C5_CELLS:
        _cell(folder, cid)
    return tmp_path, folder


def _errors(root: Path, folder: Path) -> list[str]:
    return gate.c5_errors(folder, root=root, head=HEAD)


def test_committed_requirements_match_the_pins():
    assert gate._c5_requirement_errors(REPO) == []
    doc = json.loads((REPO / gate.C5_REQUIREMENTS).read_text(encoding="utf-8"))
    assert [row["gate"] for row in doc["named_limits"]] == ["G-g"]


def test_full_valid_set_passes(tmp_path):
    assert _errors(*_tree(tmp_path)) == []


def _missing_cell(root, folder):
    (folder / "c5__cg__link.manifest.json").unlink()


def _fail_cell(root, folder):
    _cell(folder, "c5/gs/gen2_gift", {"b": _side("b", verdict="RESULT: FAIL (desync)")})


def _stale(root, folder):
    _cell(folder, "c5/cc/link", {"pydec": _pydec("e" * 64)}, code_digest="e" * 64)


def _dirty(root, folder):
    _cell(folder, "c5/cc/link", {"pydec": _pydec(dirty=["lua/gen2/signals.lua"])})


def _sha_mismatch(root, folder):
    _cell(folder, "c5/cc/gen2_gift", {"b": _side("b", sha="3" * 40)})


def _wrong_kind(root, folder):
    _cell(folder, "c5/cc/link", {"a": _side("a", kind="overlay")})


def _missing_disclosure(root, folder):
    path = _cell(folder, "c5/ct/gen2_npc_trade")
    doc = json.loads(path.read_text(encoding="utf-8"))
    del doc["disclosures"]["HARNESS"]
    path.write_text(json.dumps(doc), encoding="utf-8")


def _unpinned_jar(root, folder):
    (root / "data/upr_jars.json").write_text(json.dumps({"other": "0" * 64}), encoding="utf-8")


def _tampered(root, folder):
    path = folder / "duo_reconnect_gs_a_same_save_result.txt"
    path.write_text(path.read_text(encoding="utf-8") + "edited\n", encoding="utf-8")


def _duplicated(root, folder):
    shutil.copyfile(folder / "c5__cc__link.manifest.json", folder / "c5__cc__link_copy.manifest.json")


def _limit_removed(root, folder):
    path = root / gate.C5_REQUIREMENTS
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["named_limits"] = []
    path.write_text(json.dumps(doc), encoding="utf-8")


def _cell_dropped_from_requirements(root, folder):
    path = root / gate.C5_REQUIREMENTS
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["cells"] = doc["cells"][:-1]
    path.write_text(json.dumps(doc), encoding="utf-8")


def _not_physical(root, folder):
    _cell(folder, "c5/cc/link", evidence_level="MODEL")


def _wrong_marker(root, folder):
    _cell(folder, "c5/cc/link", receipt_marker="gen2.requirement.C-4", schema="gen2-duo-v1")


def _reconnect_phase_missing(root, folder):
    path = folder / "c5__cc__gen2_reconnect.manifest.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["receipts"].pop("receipts/c5/duo_reconnect_cc_a_wrong_save_result.txt")
    path.write_text(json.dumps(doc), encoding="utf-8")


GAPS = [
    (_missing_cell, "c5/cg/link: missing (no PASS manifest)"),
    (_fail_cell, "c5/gs/gen2_gift: b: RESULT is not PASS"),
    (_stale, f"c5/cc/link: STALE: manifest CODE_DIGEST {'e' * 64} != current code digest {HEAD}"),
    (_dirty, "c5/cc/link: STALE: the run's CODE_DIGEST stamp is dirty (['lua/gen2/signals.lua'])"),
    (_sha_mismatch, f"c5/cc/gen2_gift: b: CLIENT is not rand_overlay at the contract sha1 {'2' * 12}"),
    (_wrong_kind, f"c5/cc/link: a: CLIENT is not rand_overlay at the contract sha1 {'1' * 12}"),
    (_missing_disclosure, "c5/ct/gen2_npc_trade: missing HARNESS disclosure"),
    (_unpinned_jar, f"c5/cc/link: jar sha256 {JAR} is not pinned in data/upr_jars.json"),
    (_tampered, "c5/gs/gen2_reconnect: receipt tampered: duo_reconnect_gs_a_same_save_result.txt"),
    (_duplicated, "c5/cc/link: duplicated (c5__cc__link.manifest.json, c5__cc__link_copy.manifest.json)"),
    (_limit_removed, "tests/gen2_c5_requirements.json: named limit G-g missing or altered (owner ruling 2026-10-04"),
    (_cell_dropped_from_requirements, "tests/gen2_c5_requirements.json: required cells differ"),
    (_not_physical, "c5/cc/link: evidence_level 'MODEL' is not PHYSICAL"),
    (_wrong_marker, "c5/cc/link: receipt_marker 'gen2.requirement.C-4' is not gen2.requirement.C-5"),
    (_reconnect_phase_missing, "c5/cc/gen2_reconnect: a_wrong_save: no pinned result receipt"),
]


@pytest.mark.parametrize("mutate, expected", GAPS)
def test_each_gap_is_red_with_a_specific_message(tmp_path, mutate, expected):
    root, folder = _tree(tmp_path)
    mutate(root, folder)
    errors = _errors(root, folder)
    assert any(error.startswith(expected) for error in errors), errors


def test_equivalent_receipts_still_require_every_original_witness(tmp_path, monkeypatch):
    current = "c" * 64
    monkeypatch.setattr(gate.code_digest, "c5_admission_equivalent",
                        lambda _root, head: HEAD if head == current else None)
    for index, (mutate, expected) in enumerate(GAPS):
        root, folder = _tree(tmp_path / str(index))
        assert gate.c5_errors(folder, root=root, head=current) == []
        mutate(root, folder)
        errors = gate.c5_errors(folder, root=root, head=current)
        expected = expected.replace(f"current code digest {HEAD}", f"current code digest {current}")
        assert any(error.startswith(expected) for error in errors), errors

    root, folder = _tree(tmp_path / "mismatched-stamp")
    _cell(folder, "c5/cc/link", code_digest=current)
    errors = gate.c5_errors(folder, root=root, head=current)
    assert f"c5/cc/link: pydec: CODE_DIGEST is not one clean stamp at {current[:12]}" in errors


def test_no_receipts_dir_is_red_not_green(tmp_path):
    root, _ = _tree(tmp_path)
    assert _errors(root, root / "absent") == [f"{root / 'absent'}: no C-5 receipts directory"]


def test_cli_exit_codes(tmp_path, monkeypatch, capsys):
    root, folder = _tree(tmp_path)
    real = gate.c5_errors
    monkeypatch.setattr(gate, "c5_errors", lambda d: real(d, root=root, head=HEAD))
    assert gate.main(["--c5", str(folder)]) == 0
    assert "NAMED LIMIT  G-g: roamer catches are not detected on overlay carts" in capsys.readouterr().out
    _missing_cell(root, folder)
    assert gate.main(["--c5", str(folder)]) == 1
    assert "RED  c5/cg/link: missing" in capsys.readouterr().out
