"""CODE-DIGEST: a receipt binds the production code it was earned on; MODEL tests over temp git repos."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import gen2_code_digest as code  # noqa: E402
import verify_gen2_release as gate  # noqa: E402


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "t@t")
    git(tmp_path, "config", "user.name", "t")
    git(tmp_path, "config", "core.autocrlf", "false")
    for rel, text in {"lua/gen2/client.lua": "a", "lua/hud.lua": "h", "lua/tests/drv.lua": "d",
                      "server/state.py": "s", "docs/x.md": "x", "data/games/gen2_gold/pack.json": "{}"}.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "base")
    return tmp_path


def commit(repo, rel, text):
    (repo / rel).parent.mkdir(parents=True, exist_ok=True)
    (repo / rel).write_text(text)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", rel)


@pytest.mark.parametrize(("path", "production"), [
    ("lua/gen2/client.lua", True), ("lua/hud.lua", True), ("lua/core/x.lua", True),
    ("server/adapters/gen2_codec.py", True), ("data/games/gen2_silver/receipts/r.json", True),
    ("lua/tests/duo/scenario_gen2_link.lua", False), ("lua/gen1/client.lua", False),
    ("docs/protocol.md", False), ("tools/e2e_duo.py", False), ("server/README.md", False),
    # DIGEST-DOCS: prose under a data pack is not what the game executes
    ("data/games/gen2_crystal/README.md", False), ("data/games/gen2_gold/NOTES.txt", False),
    ("data/games/gen2_crystal/charmap.lua", True)])
def test_production_scope(path, production):
    assert code.is_production(path) is production


def test_every_non_doc_file_in_the_gen2_packs_is_json_or_lua():
    """DIGEST-DOCS guard: the digest drops only the documentation suffixes. A new pack file type
    must be a deliberate decision (behavioural -> keep it; prose -> add its suffix), never a silent
    change to what the digest covers."""
    import subprocess
    listed = subprocess.run(["git", "-C", str(code.ROOT), "ls-files", "data/games/gen2_crystal",
                             "data/games/gen2_gold", "data/games/gen2_silver"],
                            capture_output=True, text=True, check=True).stdout.split()
    assert listed, "no Gen 2 pack files found"
    odd = [p for p in listed if not p.endswith(code.DOC_SUFFIXES) and not p.endswith((".json", ".lua"))]
    assert odd == [], f"new Gen 2 pack file types need a digest decision: {odd}"


def test_a_clean_stamp_binds_head_and_only_production_changes_stale_it(repo):
    stamp = code.run_stamp(repo)
    assert stamp["dirty"] == [] and code.stamp_verdict(stamp, code.head_digest(repo)) is None
    commit(repo, "lua/tests/drv.lua", "driver edit")
    commit(repo, "docs/x.md", "prose edit")
    assert code.stamp_verdict(stamp, code.head_digest(repo)) is None
    commit(repo, "lua/gen2/client.lua", "client edit")
    assert code.stamp_verdict(stamp, code.head_digest(repo)).startswith("STALE: code digest")


def test_a_dirty_or_missing_stamp_never_binds(repo):
    (repo / "server/state.py").write_text("uncommitted")
    (repo / "lua/new_module.lua").write_text("untracked")
    stamp = code.run_stamp(repo)
    assert stamp["dirty"] == ["lua/new_module.lua", "server/state.py"]
    assert code.stamp_verdict(stamp, stamp["digest"]).startswith("STALE: run on a dirty")
    assert code.stamp_verdict(None, stamp["digest"]).startswith("STALE-UNKNOWN")
    assert code.stamp_verdict({"digest": stamp["digest"], "dirty": []}, stamp["digest"]).startswith("STALE-UNKNOWN")


def _stamp(digest, dirty=()):
    return {"schema": "gen2-code-digest-v1", "digest": digest, "commit": "c" * 40, "scope": [], "dirty": list(dirty)}


def _tree(tmp_path, pydec_stamps, gate_stamps):
    """A duo matrix whose pydec receipts carry the given CODE_DIGEST stamps, and a new-gates manifest."""
    receipts = tmp_path / "receipts"
    receipts.mkdir()
    proofs = []
    for scenario, stamp in pydec_stamps.items():
        path = receipts / f"{scenario}_pydec.txt"
        lines = ([f"CODE_DIGEST {json.dumps(stamp)}"] if stamp else []) + ["PYDEC: PASS a=x b=y"]
        path.write_text("\n".join(lines) + "\n")
        proofs.append({"scenario": scenario, "receipts": {"pydec": {"path": path.relative_to(tmp_path).as_posix()}}})
    (tmp_path / gate.DUO_MATRIX).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / gate.DUO_MATRIX).write_text(json.dumps({"requirements": [{"id": "duo.c.c", "proofs": proofs}]}))
    rows = []
    for rid, (kind, stamp) in gate_stamps.items():
        path = receipts / f"{rid}.json"
        path.write_text(json.dumps({"code_digest": stamp} if stamp else {}))
        rows.append({"id": rid, "axes": {"kind": kind},
                     "proofs": [{"receipts": {"receipt": {"path": path.relative_to(tmp_path).as_posix()}}}]})
    (tmp_path / gate.NEW_GATES).write_text(json.dumps({"requirements": rows}))


def test_verifier_names_each_stale_cell_and_passes_bound_ones(tmp_path):
    head = "h" * 64
    _tree(tmp_path, {"link": _stamp(head), "gen2_faint": _stamp("o" * 64), "gen2_poison": None,
                     "gen2_pc_ops": _stamp(head, ["lua/gen2/client.lua"])},
          {"g.w6": ("w6_gate", _stamp(head)), "g.u2": ("write_window", None),
           "g.panel": ("panel_gate", _stamp("o" * 64)), "g.qual": ("qualification", None)})
    verdicts = dict(gate.code_staleness(tmp_path, head))
    assert set(verdicts) == {"duo.c.c/gen2_faint", "duo.c.c/gen2_poison", "duo.c.c/gen2_pc_ops", "g.u2", "g.panel"}
    assert verdicts["duo.c.c/gen2_faint"].startswith("STALE: code digest")
    assert verdicts["duo.c.c/gen2_poison"].startswith("STALE-UNKNOWN")
    assert verdicts["duo.c.c/gen2_pc_ops"].startswith("STALE: run on a dirty")
    assert verdicts["g.u2"].startswith("STALE-UNKNOWN")   # client-path gate kinds need a stamp
    _tree_clean = tmp_path / "clean"
    _tree_clean.mkdir()
    (_tree_clean / "tests").mkdir()
    _tree(_tree_clean, {"link": _stamp(head)}, {"g.w6": ("w6_gate", _stamp(head))})
    assert gate.code_staleness(_tree_clean, head) == []


def test_stale_is_red_for_release_evidence_and_a_warning_elsewhere(monkeypatch, capsys):
    for part in ("g4_packet_errors", "fixtures_errors", "inspect_run_errors", "new_gates_errors", "live_gates_errors",
                 "trade_gates_errors", "duo_pairs_errors", "c5_gate_errors"):
        monkeypatch.setattr(gate, part, lambda *_a, **_k: [])
    monkeypatch.setattr(gate, "stale_errors", lambda *_a, **_k: ["duo.c.c/link: STALE-UNKNOWN: receipt records no code digest"])
    assert gate.release_evidence_errors() == ["code: duo.c.c/link: STALE-UNKNOWN: receipt records no code digest"]
    monkeypatch.setattr(gate, "duo_matrix_errors", lambda *_a, **_k: [])
    assert gate.main(["--duo-matrix"]) == 0
    assert "STALE  duo.c.c/link: STALE-UNKNOWN" in capsys.readouterr().out
    monkeypatch.setattr(gate, "stale_errors", lambda *_a, **_k: [])
    assert gate.release_evidence_errors() == []


def test_committed_tree_head_digest_is_reproducible():
    assert code.head_digest(ROOT) == code.head_digest(ROOT, "HEAD")


def test_c5_equivalence_changes_only_the_exact_admission_blob(monkeypatch):
    before, after = code.C5_LAZY_ERROR_BLOBS
    admission = "lua/admission.lua"
    rows = [(admission, after), ("lua/gen2/client.lua", "1" * 40),
            ("lua/core/session.lua", "2" * 40), ("server/state.py", "3" * 40),
            ("data/games/gen2_crystal/profile.json", "4" * 40)]
    monkeypatch.setattr(code, "_head_rows", lambda *_: rows)
    dirty = []
    monkeypatch.setattr(code, "dirty_paths", lambda *_: dirty)
    current = code.digest_of(rows)
    qualified = code.digest_of((path, before if path == admission else blob) for path, blob in rows)
    assert code.c5_admission_equivalent(ROOT, current) == qualified
    assert code.head_digest(ROOT) == current
    assert code.c5_admission_equivalent(ROOT, qualified) is None

    for index, (path, blob) in enumerate(list(rows)):
        rows[index] = (path, "f" * 40)
        changed = code.digest_of(rows)
        assert code.c5_admission_equivalent(ROOT, changed) != qualified, path
        assert code.c5_admission_equivalent(ROOT, current) is None, path
        rows[index] = (path, blob)
    rows.append(("server/new.py", "5" * 40))
    assert code.c5_admission_equivalent(ROOT, code.digest_of(rows)) != qualified
    rows.pop()
    for path, _ in rows:
        dirty[:] = [path]
        assert code.c5_admission_equivalent(ROOT, current) is None, path


# ---- the overlay row of a pack's admission.json is a build output: promotion and release stamps must not stale receipts ----

def _admission(overlay_status, overlay_sha="a" * 40, clean_status="BUILT"):
    return json.dumps({"schema": "x", "artifacts": [
        {"kind": "clean", "status": clean_status, "sha1": "c" * 40},
        {"kind": "overlay", "status": overlay_status, "sha1": overlay_sha, "grant_fingerprint": "g" * 64}]})


def test_promoting_or_restamping_the_overlay_row_does_not_move_the_digest(repo):
    commit(repo, "data/games/gen2_crystal/admission.json", _admission("BUILT"))
    before = code.head_digest(repo)
    commit(repo, "data/games/gen2_crystal/admission.json", _admission("ADMITTED"))
    assert code.head_digest(repo) == before                                    # BUILT -> ADMITTED
    commit(repo, "data/games/gen2_crystal/admission.json", _admission("ADMITTED", overlay_sha="b" * 40))
    assert code.head_digest(repo) == before                                    # a release stamp: new exact overlay hash


def test_any_other_admission_change_still_moves_the_digest(repo):
    commit(repo, "data/games/gen2_crystal/admission.json", _admission("BUILT"))
    before = code.head_digest(repo)
    commit(repo, "data/games/gen2_crystal/admission.json", _admission("BUILT", clean_status="ADMITTED"))
    assert code.head_digest(repo) != before                                    # the G1 gate / clean rows are code the client reads
    commit(repo, "data/games/gen2_crystal/other.json", "{}")
    assert code.head_digest(repo) != before
