"""The Polished Crystal randomizer pipeline (docs/polished/UPR_HANDLER.md, fork patches 0016-0017): the
companion OVERLAY is randomized, so every output must keep the overlay's spans, bank $7E and the header
byte-identical (server/upr_polished_write_domain.py) and the rule tables -- base data, types, the evolution
graph, the learnsets -- equal to the source's (upr_pipeline._check_content_polished). Each check is pinned
red by a one-byte mutation, and the unpinned scan (server/adapters/polished_rom_scan.py) is checked
against the generated pack on the release ROM.

ROM-backed tests need the pinned v3.2.3 release at $SLINK_WORK_ROOT/cache/polished/release/ (absent
skips, a wrong sha1 fails). No jar runs here: prepare_pair is driven with a stand-in for randomize().
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

from patch.tools.make_ups import ups_apply
from server import upr_pipeline as P, upr_polished_write_domain as W, upr_settings as U
from server.adapters import polished_rom_scan as S

REPO = Path(__file__).resolve().parents[2]
RELEASE = Path(os.environ.get("SLINK_WORK_ROOT", "F:/slink-work")) / "cache/polished/release/polishedcrystal-3.2.3.gbc"
PACK = REPO / "data" / "games" / "polished_crystal"
BASE = S.offsets()["PokemonStatsOffset"]
PIKACHU = BASE + 24 * S.BASE_DATA_SIZE


def overlay_byte_ownership() -> set[int]:
    return {i for lo, hi in W.ups_spans((REPO / "patch/dist/SLink-Polished.ups").read_bytes()) for i in range(lo, hi)}


def _pack(name: str) -> dict:
    return json.loads((PACK / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def roms():
    """(release bytes, overlay bytes), the overlay rebuilt from the published UPS."""
    if not RELEASE.is_file():
        pytest.skip(f"pinned Polished Crystal release absent: {RELEASE}")
    release = RELEASE.read_bytes()
    assert S.identify(release) == {"kind": "clean"}, f"{RELEASE} is not the pinned v3.2.3 release"
    prov = json.loads((REPO / "data/polished/overlay_provenance.json").read_text())["output"]
    overlay = ups_apply(release, (REPO / prov["ups"]["file"]).read_bytes())
    assert hashlib.md5(overlay).hexdigest() == prov["md5"] and S.identify(overlay) == {"kind": "overlay"}
    return release, overlay


def _files(tmp_path, overlay: bytes, *flips: int) -> tuple[str, str]:
    """(source overlay path, output path) where the output is the overlay with ``flips`` XORed."""
    out = bytearray(overlay)
    for off in flips:
        out[off] ^= 0x01
    src, dst = tmp_path / "overlay.gbc", tmp_path / "out.gbc"
    src.write_bytes(overlay)
    dst.write_bytes(bytes(out))
    return str(src), str(dst)


def _evos_record(rom: bytes, index: int) -> int:
    table = S.offsets()["PokemonMovesetsTableOffset"] + (index - 1) * 2
    return table // 0x4000 * 0x4000 + int.from_bytes(rom[table:table + 2], "little") - 0x4000


# ── the scan, against the generated pack ─────────────────────────────────────────────────
def test_scan_base_data_and_evolutions_match_the_pack(roms):
    release, _overlay = roms
    rom = S.Rom(release)
    for sid, row in _pack("species_index")["species"].items():
        rec = rom.base_stats(int(sid))
        assert rec["stats"] == list(row["base_stats"].values()), sid
        assert (rec["types"], rec["catch_rate"], rec["base_exp"]) == (row["type_ids"], row["catch_rate"], row["base_exp"]), sid
        assert (rec["items"], rec["abilities"], rec["growth_rate"]) == (row["held_item_ids"], row["ability_ids"], row["growth_rate_id"]), sid
    methods = _pack("evolutions")["methods"]
    for sid in range(1, 292):
        got = [(e[0], e[-2], e[-1]) for e in rom.evos_attacks(sid)["evolutions"]]
        assert got == [(m["method_id"], m["target"], m["target_form"]) for m in methods.get(str(sid), [])], sid
    assert rom.evos_attacks(1) == {"evolutions": [[1, 16, 2, 1]], "learnset": rom.evos_attacks(1)["learnset"]}
    assert rom.records == 337


def test_scan_wild_tables_match_the_pack(roms):
    """The pack writes a plain slot's NO_FORM as form 1, a LEVEL_FROM_BADGES level as None and the
    rock-smash set's absent rare list as []; the ROM keeps the raw bytes."""
    release, _overlay = roms
    rom, enc = S.Rom(release), _pack("encounter_tables")

    def slot(level, species, form):
        return (level if level <= 100 else None, species, form or 1)

    want = {}
    for g in enc["wild"]["grass"]:
        want.setdefault((g["table"][:-8], g["map_group"], g["map_number"]), {})[g["time"]] = (
            g["rate"], [(x["level"], x["species"], x["form"]) for x in g["slots"]])
    got = {}
    for table, rows in rom.wild().items():
        for row in rows:
            if table.endswith("Grass"):
                got[(table, *row["map"])] = {t: (row["rates"][i], [slot(*s) for s in row["slots"][7 * i:7 * i + 7]])
                                             for i, t in enumerate(("morning", "day", "night"))}
    assert got == want
    assert [(g["table"][:-8], g["map_group"], g["map_number"], g["rate"], [(x["level"], x["species"], x["form"]) for x in g["slots"]])
            for g in enc["wild"]["water"]] == [
        (t, *row["map"], row["rates"][0], [slot(*s) for s in row["slots"]])
        for t, rows in rom.wild().items() if t.endswith("Water") for row in rows]
    assert [[[(x["threshold"], x["species"], x["form"], x["level"]) for x in g[rod]] for rod in ("old", "good", "super")]
            for g in enc["fishing"]["groups"]] == [
        [[(c, sp, f or 1, lv) for c, sp, f, lv in r] for r in g["rods"]] for g in rom.fishing()]
    assert [[[(x["weight"], x["species"], x["form"], x["level"]) for x in st[k]] for k in ("common", "rare") if st.get(k)]
            for st in enc["tree"]["sets"]] == [
        [[(c, sp, f or 1, lv) for c, sp, f, lv in lst] for lst in st] for st in rom.trees()]
    assert [(x["weight"], x["species"], x["min_level"], x["max_level"]) for x in enc["contest"]["slots"]] == [
        (c, sp, lo, hi) for c, sp, _f, lo, hi in rom.contest()]


def test_scan_script_sites_and_npc_trades_match_the_pack(roms):
    """P8: the species/form/level at all 53 resolved script sites equal script_sites.json and the statics/gifts
    pack rows of the same source line; the 9 NPC trades equal gifts.json npc_trades, on release AND overlay."""
    release, overlay = roms
    sites = [s for s in _pack("script_sites")["sites"]
             if s["offset"] is not None]
    rows = {r["source"].split()[-1]: r for r in [*_pack("static_encounters")["encounters"], *_pack("gifts")["gifts"]]}
    for rom in (release, overlay):
        mons = S.Rom(rom).scripted_mons()
        assert len(mons) == len(sites) == 53
        for mon, site in zip(mons, sites, strict=True):
            assert (mon["source"], mon["kind"], mon["species"], mon["level"]) == (
                site["source"], site["kind"], site["species"], site["level"])
            assert mon["form"] == site["form"] & 0x1F == rows[site["source"]]["form"]
            assert (rows[site["source"]]["species"], rows[site["source"]]["level"]) == (mon["species"], mon["level"])
        trades = S.Rom(rom).npc_trades()
        assert [(t["requested"], t["requested_form"], t["offered"], t["offered_form"], t["dvs"], t["personality"],
                 t["ball"], t["item"], t["ot_id"]) for t in trades] == [
            (p["requested_species"], p["requested_form"], p["offered_species"], p["offered_form"], p["dvs"],
             p["personality"], p["ball"], p["item"], p["ot_id"]) for p in _pack("gifts")["npc_trades"]]


def test_the_pinned_reader_admits_the_two_pinned_artifacts_and_nothing_else(roms):
    release, overlay = roms
    assert S.Rom(overlay).base_stats(25) == S.Rom(release).base_stats(25)
    mutant = bytearray(overlay)
    mutant[PIKACHU + 8] ^= 1
    with pytest.raises(S.RomScanError, match="SHA1"):
        S.Rom(bytes(mutant))
    assert S.Rom(bytes(mutant), pinned=False).base_stats(25)["catch_rate"] != S.Rom(overlay).base_stats(25)["catch_rate"]
    with pytest.raises(S.RomScanError, match="PKPCRYSTAL"):
        S.Rom(bytes(0x200000), pinned=False)


# ── the content check ────────────────────────────────────────────────────────────────────
def test_content_check_passes_an_unchanged_overlay(roms, tmp_path):
    _release, overlay = roms
    assert len(P._check_content_polished(*_files(tmp_path, overlay))["base_stats"]) == 337


@pytest.mark.parametrize("field, offset", [("hp", 0), ("speed", 3), ("type1", 6), ("type2", 7), ("growth_rate", 16)])
def test_content_check_is_red_when_a_base_data_byte_changes(roms, tmp_path, field, offset):
    _release, overlay = roms
    with pytest.raises(P.UprPipelineError, match="base stats or types differ"):
        P._check_content_polished(*_files(tmp_path, overlay, PIKACHU + offset))


def test_content_check_is_red_when_an_evolution_or_a_learnset_changes(roms, tmp_path):
    _release, overlay = roms
    bulbasaur = _evos_record(overlay, 1)
    assert overlay[bulbasaur:bulbasaur + 6] == bytes([1, 16, 2, 1, 0xFF, 1])      # LEVEL 16 -> IVYSAUR, $FF, Lv 1
    for off, needle in ((bulbasaur + 1, "evolution targets differ"), (bulbasaur + 2, "evolution targets differ"),
                        (bulbasaur + 6, "level-up movesets differ")):
        with pytest.raises(P.UprPipelineError, match=needle):
            P._check_content_polished(*_files(tmp_path, overlay, off))


def test_content_check_ignores_only_the_catch_rate(roms, tmp_path):
    """Negative control: the minimum-catch-rate option rewrites it; every other base-data byte is compared."""
    _release, overlay = roms
    P._check_content_polished(*_files(tmp_path, overlay, PIKACHU + 8, BASE + 8))
    with pytest.raises(P.UprPipelineError, match="base stats or types differ"):
        P._check_content_polished(*_files(tmp_path, overlay, PIKACHU + 33))          # a TM/HM bit byte


def test_content_check_refuses_an_unpinned_source_and_a_changed_header(roms, tmp_path):
    _release, overlay = roms
    src, out = _files(tmp_path, overlay, PIKACHU + 8)
    with pytest.raises(P.UprPipelineError, match="not the pinned"):
        P._check_content_polished(out, src)
    with pytest.raises(P.UprPipelineError, match="header changed"):
        P._check_content_polished(*_files(tmp_path, overlay, 0x140))


# ── the write-domain audit ───────────────────────────────────────────────────────────────
def test_the_overlay_write_domain_covers_every_final_ups_byte():
    spans = W.ups_spans((REPO / "patch/dist/SLink-Polished.ups").read_bytes())
    assert W.geometry()["ups"] == overlay_byte_ownership()
    assert spans[0][0] == 0x70 and any(lo == 0x7E * 0x4000 for lo, _ in spans)
    assert any(lo == 0xC030 and hi - lo == 6 for lo, hi in spans), spans
    assert spans[-1][1] <= 0x7E * 0x4000 + 0x4000
    # the header checksums ($14E-$14F) ride inside the ROM0 bridge's run now, not beside it
    assert any(lo <= 0x14E and hi >= 0x14F for lo, hi in spans), spans


@pytest.mark.parametrize("offset, what", [(0x70, "companion overlay"), (0xDA8, "companion overlay"),
                                          (0x3F40, "companion overlay"), (0x90A1D, "companion overlay"),
                                          (0x1F8000, "SLink bank"), (0x1FB000, "SLink bank"),
                                          (0x140, "cartridge header"), (0x14E, "cartridge header")])
def test_write_domain_is_red_on_an_overlay_bank_7e_or_header_byte(roms, tmp_path, offset, what):
    _release, overlay = roms
    with pytest.raises(P.UprPipelineError, match=what):
        W.check_output(*_files(tmp_path, overlay, offset))


def test_write_domain_lets_upr_own_its_tables_and_needs_the_overlay_as_source(roms, tmp_path):
    release, overlay = roms
    first_wild = S.offsets()["JohtoGrassWildMonsOffset"] + 6
    assert W.check_output(*_files(tmp_path, overlay, first_wild, PIKACHU + 8)) == {"changed": 2, "ups_bytes": len(overlay_byte_ownership())}
    with pytest.raises(P.UprPipelineError, match="not the pinned Polished Crystal companion overlay"):
        W.check_output(*_files(tmp_path, release, first_wild))


# ── routing ──────────────────────────────────────────────────────────────────────────────
def test_family_and_describe_rom_recognise_release_and_overlay(roms, tmp_path):
    release, overlay = roms
    paths = {}
    for kind, data in (("clean", release), ("overlay", overlay)):
        paths[kind] = tmp_path / f"{kind}.gbc"
        paths[kind].write_bytes(data)
        info = P.describe_rom(str(paths[kind]), jar_fork=True)
        assert (info["family"], info["kind"], info["variant"]) == (U.FAMILY_POLISHED, kind, P.POLISHED_VARIANT)
        assert info["clean"] is True                    # a pinned release or overlay is offered
    assert P.family_of({"a": str(paths["clean"]), "b": str(paths["overlay"])}) == U.FAMILY_POLISHED
    assert P.FAMILY_POLISHED == U.FAMILY_POLISHED and P.POLISHED_RANDOMIZER_ENABLED is True


def test_randomize_never_runs_the_gen1_identify_on_polished_and_needs_a_matching_jar(roms, tmp_path, monkeypatch):
    _release, overlay = roms
    src, jar, settings = tmp_path / "in.gbc", tmp_path / "PokeRandoZX.jar", tmp_path / "s.rnqs"
    src.write_bytes(overlay)
    jar.write_bytes(b"not a jar")
    settings.write_bytes(U.build_spec(U.default_spec(U.FAMILY_POLISHED), family=U.FAMILY_POLISHED))
    monkeypatch.setattr(P, "identify", lambda _rom: pytest.fail("Gen 1 identify() ran on Polished"))
    monkeypatch.setattr(P, "_run_bounded", lambda *a: pytest.fail("Java started"))
    monkeypatch.setattr(P, "jar_is_fork", lambda _jar: True)          # a fork, but no Polished entry
    with pytest.raises(P.UprPipelineError, match="header checksum"):
        P.randomize(str(jar), str(settings), str(src), str(tmp_path / "out.gbc"), java=sys.executable)


def test_jar_supports_polished_matches_the_exact_header_checksum(roms, tmp_path):
    import zipfile
    release, overlay = roms
    jar = tmp_path / "fork.jar"
    with zipfile.ZipFile(jar, "w") as zf:
        zf.writestr("com/dabomstew/pkrandom/config/polished_offsets.ini",
                    "[Polished Crystal (U) 3.2.3]\nCRCInHeader=0x6CA3\n[Polished Crystal (U) 3.2.3]\nCRCInHeader=0xA36C // big-endian\n")
    assert P.jar_supports_polished(str(jar), release)
    assert not P.jar_supports_polished(str(jar), overlay)         # an overlay needs the CRCInHeader=-1 section
    assert not P.jar_supports_polished(str(tmp_path / "missing.jar"), release)
    wild = tmp_path / "fork19.jar"                                # patch 0019: overlay accepted by structure
    with zipfile.ZipFile(wild, "w") as zf:
        zf.writestr("com/dabomstew/pkrandom/config/polished_offsets.ini",
                    "[Polished Crystal (U) 3.2.3]\nCRCInHeader=0xA36C\n"
                    "[Polished Crystal SLink overlay (U) 3.2.3]\nCRCInHeader=-1\n")
    assert P.jar_supports_polished(str(wild), release) and P.jar_supports_polished(str(wild), overlay)
    changed = bytearray(overlay)
    changed[0x14E:0x150] = b"\x12\x34"                            # a rebuilt overlay's header checksum
    assert P.jar_supports_polished(str(wild), bytes(changed))
    plain = bytearray(release)
    plain[0x14E:0x150] = b"\x12\x34"                              # the release with a wrong checksum is not an overlay
    assert not P.jar_supports_polished(str(wild), bytes(plain))
    bare = bytearray(overlay)
    bare[0x1F8000:0x1FC000] = b"\xff" * 0x4000                    # bridge bytes but an empty bank $7E
    assert not P.jar_supports_polished(str(wild), bytes(bare))


def _drive_prepare_pair(roms, tmp_path, monkeypatch, flips):
    """prepare_pair over two overlays with randomize() replaced: player p's output is the overlay with
    flips[p] XORed. The Gen 1 checks must never run on Polished."""
    from server.adapters import gen1_rom_scan
    _release, overlay = roms
    sources = {}
    for pid in "ab":
        (tmp_path / f"{pid}_companion.gbc").write_bytes(overlay)
        sources[pid] = str(tmp_path / f"{pid}_companion.gbc")
    settings = tmp_path / "settings.rnqs"
    settings.write_bytes(U.build_spec(dict(U.default_spec(U.FAMILY_POLISHED), wild="area", wild_min_catch_rate=2,
                                           starters="unchanged", trainers="unchanged"),
                                      family=U.FAMILY_POLISHED))
    string = settings.read_bytes()[8:].decode("ascii")

    def fake_randomize(jar, settings_path, source, output, java="java"):
        pid = Path(output).name[0]
        data = bytearray(Path(source).read_bytes())
        for off in flips[pid]:
            data[off] ^= 0x01
        Path(output).write_bytes(data)
        return {"seed": {"a": 11, "b": 22}[pid], "settings_string": string, "version": P.SUPPORTED_UPR_VERSION,
                "output": output, "log": output + ".log", "sha1": hashlib.sha1(data).hexdigest(),
                "source_sha1": hashlib.sha1(overlay).hexdigest(), "base_kind": "overlay"}

    monkeypatch.setattr(P, "randomize", fake_randomize)
    monkeypatch.setattr(P, "identify", lambda _rom: pytest.fail("Gen 1 identify() ran on Polished"))
    monkeypatch.setattr(P, "_check_content", lambda *a: pytest.fail("Gen 1 _check_content ran on Polished"))
    monkeypatch.setattr(gen1_rom_scan, "fingerprint_rom", lambda _rom: pytest.fail("Gen 1 fingerprint ran on Polished"))
    return P.prepare_pair("fork.jar", str(settings), sources, str(tmp_path / "out"))


def test_prepare_pair_routes_polished_to_its_own_checks(roms, tmp_path, monkeypatch):
    wild = S.offsets()["JohtoGrassWildMonsOffset"] + 6
    pair = _drive_prepare_pair(roms, tmp_path, monkeypatch, {"a": [wild], "b": [wild + 3, PIKACHU + 8]})
    assert pair["family"] == U.FAMILY_POLISHED and pair["categories"] == ["wild"]
    assert pair["summary"] == "wild encounters 1-to-1 per area, minimum catch rate 2"
    a, b = pair["players"]["a"], pair["players"]["b"]
    assert (a["seed"], b["seed"]) == (11, 22) and a["sha1"] != b["sha1"] and a["content_hash"] != b["content_hash"]
    for row in (a, b):
        assert row["fingerprint"] == "" and len(row["content_hash"]) == 64 and row["write_domain"]["ups_bytes"] == len(overlay_byte_ownership())


def test_prepare_pair_refuses_a_polished_output_that_wrote_into_the_overlay(roms, tmp_path, monkeypatch):
    with pytest.raises(P.UprPipelineError, match="companion overlay"):
        _drive_prepare_pair(roms, tmp_path, monkeypatch, {"a": [PIKACHU + 8], "b": [PIKACHU + 8, 0x71]})


def test_prepare_pair_refuses_a_polished_output_whose_base_stats_moved(roms, tmp_path, monkeypatch):
    with pytest.raises(P.UprPipelineError, match="base stats or types differ"):
        _drive_prepare_pair(roms, tmp_path, monkeypatch, {"a": [PIKACHU], "b": [PIKACHU + 8]})
