"""tools/stamp_release.py: the plan, the version-only guarantee and the shipped-version record."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import stamp_release as sr  # noqa: E402


def _as_built_repo(root):
    dist = root / "patch/dist"
    dist.mkdir(parents=True)
    for name in sr.SHIPPED:
        (dist / name).write_bytes(name.encode())
    (dist / "SLink-Polished.ups").write_bytes(b"Polished as built")
    gen2 = {"schema": "gen2-overlay-provenance-v1", "outputs": {}}
    for key, title in (("pokecrystal", "Crystal"), ("pokegold", "Gold"), ("pokesilver", "Silver")):
        name = f"SLink-{title}.ups"
        gen2["outputs"][key] = {"ups": {"file": f"patch/dist/{name}",
                                           "sha256": hashlib.sha256(name.encode()).hexdigest()}}
    polished = {"schema": "polished-overlay-provenance-v1", "output": {"ups": {
        "file": "patch/dist/SLink-Polished.ups", "sha256": hashlib.sha256(b"Polished as built").hexdigest()}}}
    for family, doc in (("gen2", gen2), ("polished", polished)):
        path = root / f"data/{family}/overlay_provenance.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(doc), encoding="utf-8")
    return dist


def test_release_certificate_declares_both_as_built_families(tmp_path, monkeypatch):
    dist = _as_built_repo(tmp_path)
    monkeypatch.setattr(sr, "ROOT", tmp_path)
    monkeypatch.setattr(sr, "DIST", dist)
    monkeypatch.setattr(sr, "VERSION_FILE", dist / "companion_version.json")
    doc = sr.write_version_file("v0.4.0", ("rb", "pure", "gen3"), as_built=("gen2", "polished"))
    assert doc["schema"] == "slink-companion-version-v2"
    assert doc["version"] == "v0.4.0"
    assert doc["families"]["rb"] == {"mode": "stamped", "version": "v0.4.0"}
    for family in ("gen2", "polished"):
        entry = doc["families"][family]
        assert entry["mode"] == "as-built" and "version" not in entry
        assert entry["provenance"] == f"data/{family}/overlay_provenance.json"
        assert entry["provenance_sha256"] == hashlib.sha256(
            (tmp_path / entry["provenance"]).read_bytes()).hexdigest()
    assert doc["families"]["polished"]["files"] == {
        "SLink-Polished.ups": hashlib.sha256(b"Polished as built").hexdigest()}
    assert doc["files"]["SLink-Polished.ups"] == doc["families"]["polished"]["files"]["SLink-Polished.ups"]


def test_an_admitted_gen2_stamp_refuses_before_any_step_is_run(monkeypatch):
    monkeypatch.setattr(sr, "gen2_overlays_admitted", lambda: True)
    with pytest.raises(SystemExit, match="ADMITTED.*as-built"):
        sr.plan("v0.4.0", ("gen2",), [])


def test_cli_declares_as_built_scope_and_writes_only_after_validation(tmp_path, monkeypatch):
    from types import SimpleNamespace
    dist = _as_built_repo(tmp_path)
    monkeypatch.setattr(sr, "ROOT", tmp_path)
    monkeypatch.setattr(sr, "DIST", dist)
    monkeypatch.setattr(sr, "VERSION_FILE", dist / "companion_version.json")
    monkeypatch.setattr(sr, "identities", lambda: {})
    monkeypatch.setattr(sr, "pinned_md5s", lambda: {})
    monkeypatch.setattr(sr, "update_md5_tables", lambda *args: [])
    monkeypatch.setattr(sr.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(stdout=str(tmp_path / ".git")))
    selected = []
    monkeypatch.setattr(sr, "plan", lambda version, families, *args, **kw: selected.extend(families) or [])
    monkeypatch.setattr(sys, "argv", ["stamp_release", "--version", "v0.4.0", "--only", "rb,pure,gen3",
                                    "--as-built", "gen2,polished"])
    assert sr.main() == 0
    assert selected == ["rb", "pure", "gen3"]
    assert json.loads(sr.VERSION_FILE.read_text())["families"]["gen2"]["mode"] == "as-built"
    sr.VERSION_FILE.unlink()
    selected.clear()
    (dist / "SLink-Crystal.ups").write_bytes(b"wrong")
    with pytest.raises(SystemExit, match="as-built.*differ"):
        sr.main()
    assert not selected and not sr.VERSION_FILE.exists()


def test_as_built_policy_cannot_override_a_selected_stamped_family(tmp_path, monkeypatch):
    dist = _as_built_repo(tmp_path)
    monkeypatch.setattr(sr, "ROOT", tmp_path)
    monkeypatch.setattr(sr, "DIST", dist)
    monkeypatch.setattr(sr, "VERSION_FILE", dist / "companion_version.json")
    with pytest.raises(SystemExit, match="both stamped and as-built"):
        sr.write_version_file("v0.4.0", sr.FAMILIES, as_built=("gen2", "polished"))
    assert not sr.VERSION_FILE.exists()


@pytest.mark.parametrize("family", ["gen2", "polished"], ids=["vanilla-gen2", "polished"])
def test_as_built_certificate_rechecks_provenance_before_publication(tmp_path, monkeypatch, family):
    dist = _as_built_repo(tmp_path)
    monkeypatch.setattr(sr, "ROOT", tmp_path)
    monkeypatch.setattr(sr, "DIST", dist)
    monkeypatch.setattr(sr, "VERSION_FILE", dist / "companion_version.json")
    name = "SLink-Crystal.ups" if family == "gen2" else "SLink-Polished.ups"
    (dist / name).write_bytes(b"wrong")
    with pytest.raises(SystemExit, match="as-built.*differ"):
        sr.write_version_file("v0.4.0", ("rb", "pure", "gen3"), as_built=("gen2", "polished"))
    assert not sr.VERSION_FILE.exists()


def test_v2_certificate_requires_explicit_as_built_scope_before_a_partial_restamp(tmp_path, monkeypatch):
    from types import SimpleNamespace
    dist = _as_built_repo(tmp_path)
    monkeypatch.setattr(sr, "ROOT", tmp_path)
    monkeypatch.setattr(sr, "DIST", dist)
    monkeypatch.setattr(sr, "VERSION_FILE", dist / "companion_version.json")
    sr.write_version_file("v0.4.0", ("rb", "pure", "gen3"), as_built=("gen2", "polished"))
    before = sr.VERSION_FILE.read_bytes()
    selected = []
    monkeypatch.setattr(sr.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(stdout=str(tmp_path / ".git")))
    monkeypatch.setattr(sr, "identities", lambda: {})
    monkeypatch.setattr(sr, "pinned_md5s", lambda: {})
    monkeypatch.setattr(sr, "update_md5_tables", lambda *args: [])
    monkeypatch.setattr(sr, "plan", lambda *args, **kw: selected.append(args) or [])
    monkeypatch.setattr(sys, "argv", ["stamp_release", "--version", "dev", "--only", "rb,pure"])
    with pytest.raises(SystemExit, match="v2 certificate.*explicit --as-built"):
        sr.main()
    assert not selected and sr.VERSION_FILE.read_bytes() == before
    with pytest.raises(SystemExit, match="v2 certificate.*explicit --as-built"):
        sr.write_version_file("dev", ("rb", "pure"))
    assert sr.VERSION_FILE.read_bytes() == before


@pytest.fixture
def roms(tmp_path):
    for name in (*sr.GEN3_ROMS.values(), *(dump for dump, _ in sr.RB_DUMPS.values())):
        (tmp_path / name).write_bytes(b"")
    return [tmp_path]


@pytest.mark.parametrize("good", ["dev", "v0.3.0", "v10.20.30", "v0.3.0-dev"])
def test_good_versions(good):
    assert sr.check_version(good) == good


@pytest.mark.parametrize("bad", ["0.3.0", "v1.2", "v0.3.0-rc1", "v100.200.300-dev", "", "DEV", "v1.2.3 "])
def test_bad_versions_stop_the_tool(bad):
    with pytest.raises(SystemExit):
        sr.check_version(bad)


def test_every_unadmitted_family_is_stamped_with_the_version_and_in_dependency_order(roms, monkeypatch):
    monkeypatch.setattr(sr, "gen2_overlays_admitted", lambda: False)
    steps = sr.plan("v0.3.0", sr.FAMILIES, roms, promote=True)
    by_family = {f: [s for s in steps if s.family == f] for f in sr.FAMILIES}
    assert all(by_family[f] for f in sr.FAMILIES)
    builds = [s for s in steps if s.name.startswith("build ")]
    assert len(builds) == 7                                                    # RB, pureRGB, Gen 2, FR, LG, Emerald, RR
    assert all(s.argv[s.argv.index("--version") + 1] == "v0.3.0" for s in builds)
    pure = [s.name for s in by_family["pure"]]
    assert pure[0] == "build pureRGB overlay" and pure[-1] == "re-pin Gen 3 write domains (emerald)"   # the re-pin follows the jar
    assert pure.index("UPR entries") < pure.index("randomizer jar entries")
    gen2 = by_family["gen2"]
    assert gen2[0].argv[1] == "tools/build_gen2_companion.py" and "--promote-overlays" in gen2[-1].argv
    assert [s.name for s in by_family["rb"]][-1] == "pins (md5, canonical)"


def test_gen2_promotion_follows_the_rows_and_families_can_be_selected(roms, monkeypatch):
    monkeypatch.setattr(sr, "gen2_overlays_admitted", lambda: False)
    steps = sr.plan("dev", ("gen2",), roms)
    assert {s.family for s in steps} == {"gen2"} and not any("--promote-overlays" in s.argv for s in steps)     # rows BUILT: nothing to re-issue
    assert any("--promote-overlays" in s.argv for s in sr.plan("dev", ("gen2",), roms, promote=True))
    monkeypatch.setattr(sr, "gen2_overlays_admitted", lambda: True)
    for promote in (None, False, True):
        with pytest.raises(SystemExit, match="ADMITTED"):
            sr.plan("dev", ("gen2",), roms, promote=promote)


def test_a_missing_clean_rom_is_named(tmp_path):
    with pytest.raises(SystemExit, match="Pokemon - FireRed"):
        sr.plan("dev", ("gen3",), [tmp_path])


def test_identity_drift_names_exactly_the_moved_records():
    before = {"pure:pokered": "a" * 40, "gen2:pokegold": "b" * 40, "companion_pins:rb-red": "c" * 40, "companion_pins:rr": "d" * 40}
    after = dict(before)
    assert sr.identity_drift(before, after, sr.FAMILIES) == []
    after["gen2:pokegold"] = "e" * 40
    del after["pure:pokered"]
    drift = sr.identity_drift(before, after, sr.FAMILIES)
    assert [d.split(":")[0] for d in drift] == ["gen2", "pure"]
    assert sr.identity_drift(before, after, ("rb",)) == []                    # families that were not stamped are not judged
    assert sr.identity_drift(before, {**before, "companion_pins:rr": "0" * 40}, ("gen3",))[0].startswith("companion_pins:rr")


@pytest.mark.parametrize("family,key", [
    ("rb", "companion_pins:rb-red"),
    ("pure", "pure:pokered"),
    ("gen2", "gen2:pokegold"),
    ("gen3", "companion_pins:rr"),
    ("gen3", "gen3:firered:canonical_payload_sha256"),
])
def test_new_selected_family_identities_are_drift(family, key):
    after = {key: "a" * 40}
    assert sr.identity_drift({}, after, (family,)) == [f"{key}: missing -> {'a' * 12}"]
    assert sr.identity_drift({}, after, tuple(f for f in sr.FAMILIES if f != family)) == []


@pytest.mark.parametrize("accept,code", [(False, 2), (True, 0)])
def test_added_identity_requires_explicit_acceptance_before_publication(tmp_path, monkeypatch, accept, code):
    dist = _as_built_repo(tmp_path)
    monkeypatch.setattr(sr, "ROOT", tmp_path)
    monkeypatch.setattr(sr, "DIST", dist)
    monkeypatch.setattr(sr, "VERSION_FILE", dist / "companion_version.json")
    sr.write_version_file("v0.4.0", ("rb", "pure", "gen3"), as_built=("gen2", "polished"))
    certificate = sr.VERSION_FILE.read_bytes()
    snapshots = iter([{}, {"gen3:firered:canonical_payload_sha256": "a" * 64}])
    published = []
    monkeypatch.setattr(sr, "identities", lambda: next(snapshots))
    monkeypatch.setattr(sr, "pinned_md5s", lambda: {})
    monkeypatch.setattr(sr, "plan", lambda *args, **kwargs: [])
    monkeypatch.setattr(sr.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(stdout=".git"))
    monkeypatch.setattr(sr, "update_md5_tables", lambda *args: published.append("tables"))

    def write_version(version, families, *, as_built):
        assert (version, families, as_built) == ("dev", ("rb", "pure", "gen3"), ("gen2", "polished"))
        published.append("version")
        return {"files": {}}

    monkeypatch.setattr(sr, "write_version_file", write_version)
    monkeypatch.setattr(sys, "argv", ["stamp_release", "--version", "dev", "--only", "rb,pure,gen3",
                                    "--as-built", "gen2,polished"]
                        + (["--accept-new-identity"] if accept else []))
    assert sr.main() == code
    assert published == (["tables", "version"] if accept else [])
    assert sr.VERSION_FILE.read_bytes() == certificate


def test_md5_tables_follow_the_new_pins(tmp_path, monkeypatch):
    doc = tmp_path / "patch" / "README.md"
    doc.parent.mkdir()
    doc.write_text("| `SLink-RB-Red.ups` | base | `" + "1" * 32 + "` |\nother " + "2" * 32 + "\n", encoding="utf-8")
    monkeypatch.setattr(sr, "ROOT", tmp_path)
    monkeypatch.setattr(sr, "README_TABLES", ("patch/README.md",))
    assert sr.update_md5_tables({"rb-red": "1" * 32}, {"rb-red": "3" * 32}) == ["patch/README.md"]
    text = doc.read_text(encoding="utf-8")
    assert "3" * 32 in text and "1" * 32 not in text and "2" * 32 in text
    assert sr.update_md5_tables({"rb-red": "3" * 32}, {"rb-red": "3" * 32}) == []           # nothing to do when unchanged


def test_the_version_file_binds_the_shipped_bytes(tmp_path, monkeypatch):
    dist = tmp_path / "patch" / "dist"
    dist.mkdir(parents=True)
    (dist / "SLink-RR.ups").write_bytes(b"rr")
    monkeypatch.setattr(sr, "DIST", dist)
    monkeypatch.setattr(sr, "VERSION_FILE", dist / "companion_version.json")
    doc = sr.write_version_file("v0.3.0", ("rb",))
    assert doc["version"] == "mixed" or doc["families"] == {"rb": "v0.3.0"}
    doc = sr.write_version_file("v0.3.0", sr.FAMILIES)
    assert doc["version"] == "v0.3.0" and set(doc["families"]) == set(sr.FAMILIES)
    import hashlib
    assert doc["files"] == {"SLink-RR.ups": hashlib.sha256(b"rr").hexdigest()}
    assert json.loads((dist / "companion_version.json").read_text(encoding="utf-8"))["schema"] == sr.SCHEMA


def test_the_shared_jar_is_only_installed_when_asked(roms):
    names = [s.name for s in sr.plan("dev", ("pure",), roms)]
    assert "randomizer jar entries" in names
    assert "randomizer jar entries" not in [s.name for s in sr.plan("dev", ("pure",), roms, jar=False)]


def test_plan_needs_no_clean_roms(monkeypatch):
    monkeypatch.setattr(sr, "gen2_overlays_admitted", lambda: False)
    steps = sr.plan("dev", sr.FAMILIES, [])
    assert any("<rom-dir>" in a for s in steps for a in s.argv)


def test_installing_a_new_jar_re_pins_the_gen3_write_domains(roms):
    """The pure stamp installs a rebuilt randomizer jar; server.upr_gen3_write_domain refuses any jar its models were not
    built from, so a stamp that skips the re-pin breaks Manager randomization of FireRed, LeafGreen and Emerald."""
    steps = sr.plan("v9.9.9", ("pure",), roms, jar=True)
    argvs = [step.argv for step in steps]
    install = next(i for i, argv in enumerate(argvs) if "tools/upr_resource_update.py" in argv)
    for title in ("frlg", "emerald"):
        repin = next(i for i, argv in enumerate(argvs)
                     if "server.upr_gen3_write_domain" in argv and "--write" in argv and argv[-1] == title)
        assert repin > install, title
    assert not [a for a in (s.argv for s in sr.plan("v9.9.9", ("pure",), roms, jar=False))
                if "server.upr_gen3_write_domain" in a], "no new jar, nothing to re-pin"
