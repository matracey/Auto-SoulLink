"""Gen 4 (HGSS) data tools -- CARD gen4-G2-data-tools.

tools/gen_gen4_{area_map,encounters,trainers,acquisition}.py generate data/games/gen4_hgss/
{area_map,locations,encounters,trainers,acquisition}.json from the pinned pret/pokeheartgold clone.

Tests that need the clone skip BY NAME when it is absent and FAIL when it is at another commit or
dirty (absent skips, wrong fails). The rest read only the committed JSON. Every control below was
revert-tested once: the mutation that must turn it red is named in the test.
"""

from __future__ import annotations

import copy
import json
import re
import subprocess
import sys
import types
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import gen4_pins  # noqa: E402
import gen_gen4_acquisition as acq  # noqa: E402
import gen_gen4_area_map as base  # noqa: E402
import gen_gen4_encounters as enc  # noqa: E402
import gen_gen4_names as names  # noqa: E402
import gen_gen4_trainers as trn  # noqa: E402

DATA = ROOT / "data" / "games" / "gen4_hgss"
TOOLS = {
    "area_map": (base, ["area_map.json", "locations.json"]),
    "encounters": (enc, ["encounters.json"]),
    "trainers": (trn, ["trainers.json"]),
    "acquisition": (acq, ["acquisition.json"]),
}
ALL_FILES = [f for _m, fs in TOOLS.values() for f in fs]
PIN = gen4_pins.SOURCE_COMMITS["pokeheartgold_citation"]


def load(name: str) -> dict:
    return json.loads((DATA / name).read_text(encoding="utf-8"))


# ── the pinned clone: absent -> skip by name, wrong commit -> FAIL ───────────────────────────────


@pytest.fixture(scope="module")
def clone() -> Path:
    try:
        path = base.locate_clone()
    except base.PretAbsent as exc:
        pytest.skip(f"pret/pokeheartgold @ {PIN[:8]} not available: {exc}")
    try:
        base.verify_clone(path)
    except base.PretMismatch as exc:
        pytest.fail(str(exc))
    return path


def run_tool(mod, argv: list[str], monkeypatch, capsys) -> tuple[int, str]:
    monkeypatch.setattr(sys, "argv", [mod.__name__, *argv])
    rc = mod.main()
    return rc, capsys.readouterr().err


# ── --check mode: regenerate in memory, diff vs committed ────────────────────────────────────────


def _skip_absent_rom(rc: int, err: str) -> None:
    absent = re.fullmatch(r"OPEN: absent input (?:heartgold|heartgold_hge): (.+)\n?", err)
    if rc == 2 and absent:
        pytest.skip(f"absent input: {absent[1]}")


@pytest.mark.parametrize("tool", sorted(TOOLS))
def test_check_mode_matches_committed(tool, clone, monkeypatch, capsys):
    mod, files = TOOLS[tool]
    rc, err = run_tool(mod, ["--check", "--pret", str(clone)], monkeypatch, capsys)
    _skip_absent_rom(rc, err)
    assert rc == 0, f"{tool} drifted from the committed JSON: {err}"


@pytest.mark.parametrize("tool", sorted(TOOLS))
def test_check_mode_detects_drift(tool, clone, monkeypatch, capsys, tmp_path):
    """Control: a one-byte edit to a committed file must turn --check red (revert-tested by
    making finish() always return True)."""
    mod, files = TOOLS[tool]
    for name in files:
        (tmp_path / name).write_text((DATA / name).read_text(encoding="utf-8"), encoding="utf-8")
    victim = tmp_path / files[0]
    text = victim.read_text(encoding="utf-8")
    victim.write_text(text.replace('"commit": "' + PIN, '"commit": "' + "0" * 40, 1), encoding="utf-8")
    rc, err = run_tool(mod, ["--check", "--pret", str(clone), "--out-dir", str(tmp_path)], monkeypatch, capsys)
    _skip_absent_rom(rc, err)
    assert rc == 1 and "DRIFT" in err and files[0] in err


def test_check_mode_flags_a_missing_committed_file(clone, monkeypatch, capsys, tmp_path):
    rc, err = run_tool(trn, ["--check", "--pret", str(clone), "--out-dir", str(tmp_path)], monkeypatch, capsys)
    assert rc == 1 and "DRIFT" in err


# ── pret availability controls (no clone needed) ─────────────────────────────────────────────────


def test_absent_clone_is_open_and_names_the_path(monkeypatch, capsys, tmp_path):
    missing = tmp_path / "no-pokeheartgold"
    for mod, _f in TOOLS.values():
        rc, err = run_tool(mod, ["--check", "--pret", str(missing)], monkeypatch, capsys)
        assert rc == 2 and str(missing) in err and "OPEN" in err
    with pytest.raises(base.PretAbsent, match="no-pokeheartgold"):
        base.locate_clone(str(missing))


def _fake_clone(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "fake-pret"
    (repo / "include" / "constants").mkdir(parents=True)
    (repo / "include" / "constants" / "maps.h").write_text("// stub\n")
    env = ["-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false"]
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), *env, "commit", "-q", "-m", "x"], check=True)
    head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    return repo, head


def test_wrong_pret_head_fails_not_skips(monkeypatch, capsys, tmp_path):
    repo, head = _fake_clone(tmp_path)
    assert head != PIN
    with pytest.raises(base.PretMismatch, match="pinned"):
        base.verify_clone(repo)
    for mod, _f in TOOLS.values():
        rc, err = run_tool(mod, ["--check", "--pret", str(repo)], monkeypatch, capsys)
        assert rc == 1 and "FAIL" in err and head in err


def test_dirty_pret_clone_fails(monkeypatch, tmp_path):
    repo, head = _fake_clone(tmp_path)
    monkeypatch.setattr(base, "PIN", head)
    base.verify_clone(repo)  # clean at the (patched) pin: accepted
    (repo / "include" / "constants" / "maps.h").write_text("// edited\n")
    with pytest.raises(base.PretMismatch, match="tracked modifications"):
        base.verify_clone(repo)


# ── committed data: provenance, counts, retirement precondition ─────────────────────────────────


def test_every_file_records_the_pinned_commit_and_inputs():
    lock = json.loads((ROOT / "data" / "gen4_sources.lock.json").read_text(encoding="utf-8"))
    assert lock["sources"]["pokeheartgold_citation"]["commit"] == PIN
    for name in ALL_FILES:
        src = load(name)["source"]
        assert src["repo"] == "pret/pokeheartgold" and src["commit"] == PIN, name
        assert src["inputs"] and all(len(h) == 64 for h in src["inputs"].values()), name


def test_successors_exist_before_gen4_hgsspt_can_be_deleted():
    """PLAN 4.6: data/games/gen4_hgsspt/** may go only after its successors exist in gen4_hgss/."""
    for name in ALL_FILES:
        assert (DATA / name).stat().st_size > 1000, f"{name} is missing or a stub"


def test_area_map_and_locations_counts():
    area, loc = load("area_map.json"), load("locations.json")
    assert len(area["maps"]) == 540 and len(loc["locations"]) == 540
    assert set(area["maps"]) == set(loc["locations"])
    assert {m for m in area["maps"] if area["maps"][m] is None} == set(area["unmapped_maps"])
    for mid, aid in area["maps"].items():
        assert aid is None or mid_in(area, aid, int(mid)), (mid, aid)
        assert loc["locations"][mid]["area"] == aid


def mid_in(area: dict, aid: str, mid: int) -> bool:
    return mid in area["areas"][aid]["maps"] or mid in area["areas"][aid]["unused_maps"]


def test_special_zones_bug_contest_and_safari_are_their_own_areas():
    area = load("area_map.json")
    assert area["areas"]["bug_catching_contest"]["maps"] == [487]
    assert area["areas"]["bug_catching_contest"]["special"] == "bug_contest"
    assert area["areas"]["national_park"]["maps"] == [96] and area["areas"]["national_park"]["unused_maps"] == [488], "the contest map must not stay in National Park"
    sub = {a: v for a, v in area["areas"].items() if v["parent"] == "safari_zone"}
    assert len(sub) == 12 and all(v["maps"] == [] and v["special"] == "safari" for v in sub.values())
    assert len(area["areas"]["safari_zone"]["maps"]) == 15
    modes = load("acquisition.json")["special_modes"]
    assert modes["bug_contest"]["area"] == "bug_catching_contest" and set(modes["safari"]["areas"]) == set(sub)
    assert {r["species"]["name"] for r in modes["roamers"].values()} == {"Raikou", "Entei", "Latias", "Latios"}


def split_banks(doc: dict) -> set[str]:
    hg, ss = doc["versions"]["heartgold"]["banks"], doc["versions"]["soulsilver"]["banks"]
    return {k for k in hg if hg[k] != ss[k]}


def test_encounter_counts_and_hg_ss_split_preserved():
    doc = load("encounters.json")
    assert doc["bank_count"] == 142 and doc["split_bank_count"] == 53
    for title in ("heartgold", "soulsilver"):
        assert len(doc["versions"][title]["banks"]) == 142
        assert len(doc["versions"][title]["headbutt"]) == 60
    differing = split_banks(doc)
    assert len(differing) == 53
    assert differing == {k for k, b in doc["banks"].items() if b["version_split"]}
    assert len(doc["banks_without_map"]) == 7 and len(doc["safari"]["areas"]) == 12


def test_a_merged_hg_eq_ss_bank_is_detected():
    """Control: collapsing SS into HG for a bank that pret splits (R30) must change the detector's
    answer (revert-tested by swapping split() for a HEARTGOLD-only collapse in the generator)."""
    doc = load("encounters.json")
    assert "R30" in split_banks(doc)
    doc["versions"]["soulsilver"]["banks"]["R30"] = doc["versions"]["heartgold"]["banks"]["R30"]
    assert "R30" not in split_banks(doc) and len(split_banks(doc)) == 52


def test_split_banks_match_pret_independently(clone):
    """Oracle: the banks that differ per version are exactly the gs_enc_data.json entries that contain
    a {"HEARTGOLD","SOULSILVER"} node (computed here, not by the generator)."""
    raw = json.loads(base.read(clone, "files/fielddata/encountdata/gs_enc_data.json"))["encounters"]

    def has_pair(o) -> bool:
        if isinstance(o, dict):
            return set(o) == {"HEARTGOLD", "SOULSILVER"} or any(has_pair(v) for v in o.values())
        return isinstance(o, list) and any(has_pair(v) for v in o)

    expected = {e["map"] for e in raw if has_pair(e)}
    assert len(expected) == 53
    doc = load("encounters.json")
    assert {doc["banks"][k]["json_map"] for k in split_banks(doc)} == expected
    # spot check a land-level split and a swarm split against the raw source
    r30 = next(e for e in raw if e["map"] == "R30")
    hg_lv = r30["land"]["mons"][0]["level"]
    assert doc["versions"]["heartgold"]["banks"]["R30"]["land"]["day"][0]["min_level"] == (hg_lv["HEARTGOLD"] if isinstance(hg_lv, dict) else hg_lv)
    assert doc["versions"]["soulsilver"]["banks"]["R30"]["swarm"]["land"]["name"] == "Ledyba"
    assert doc["versions"]["heartgold"]["banks"]["R30"]["swarm"]["land"]["name"] == "Pidgey"


def test_trainers_counts_and_roles():
    doc = load("trainers.json")
    assert doc["trainer_count"] == len(doc["trainers"]) == 738 and doc["version_split"] is False
    names = {(t["name"], t["role"]) for t in doc["trainers"].values() if t["role"]}
    assert ("Falkner", "leader") in names and ("Lance", "champion") in names and ("Silver", "rival") in names
    assert all(t["const"].startswith("TRAINER_") for t in doc["trainers"].values())


# ── acquisition: 61 script sites, C producers, 13 NPC records (10/2/1) ───────────────────────────


def test_acquisition_inventory_counts_are_pinned():
    doc = load("acquisition.json")
    inv = doc["inventory"]
    assert inv["script_site_count"] == len(doc["script_sites"]) == 62
    assert inv["script_command_counts"] == {"GiveMon": 13, "GiveEgg": 3, "GiveDaycareEgg": 1, "GiveTogepiEgg": 1, "GiveSpikyEarPichu": 1, "GiveLoanMon": 2, "CreateRoamer": 8, "WildBattle": 21, "LoadNPCTrade": 11, "ChooseStarter": 1}
    assert inv["npc_record_count"] == len(doc["npc_trade_records"]) == 13
    assert inv["npc_classification"] == {"authored_exchange": 10, "authored_loan_grant": 2, "dormant_narc_record_in_pinned_authored_scan": 1}
    assert inv["npc_load_site_count"] == 11 and inv["npc_distinct_exchange_ids"] == [0, 1, 2, 3, 5, 8, 9, 10, 11, 12]
    assert inv["commands_without_a_c_producer"] == []
    assert inv["c_call_site_count"] == 29


def test_npc_records_classification_and_loans_are_not_exchanges():
    recs = {r["index"]: r for r in load("acquisition.json")["npc_trade_records"]}
    assert [i for i, r in recs.items() if r["source_class"] == "authored_loan_grant"] == [6, 7]
    assert [i for i, r in recs.items() if r["source_class"].startswith("dormant")] == [4]
    assert recs[5]["same_species"] and recs[10]["same_species"] and recs[5]["source_class"] == recs[10]["source_class"] == "authored_exchange"
    assert recs[8]["load_sites"] == ["scr_seq_0195_R10R0201:64", "scr_seq_0196_R10R0202:207"], "ID 8 has two load sites"
    for i in (6, 7):
        assert "never key_change" in recs[i]["identity_rule"] and recs[i]["load_sites"] == []


def test_loan_species_come_from_the_npc_record_not_the_map_argument():
    """Contradicts docs/gen4/research/data/acquisition_manifest.json, which parsed GiveLoanMon's third
    argument (a map number: 75 / 101) as a species. The macro is GiveLoanMon tradeno, level, mapno."""
    sites = {s["id"]: s for s in load("acquisition.json")["script_sites"] if s["command"] == "GiveLoanMon"}
    assert {s["species"]["value"]["name"] for s in sites.values()} == {"Shuckle", "Spearow"}
    assert {s["loan_map_arg"] for s in sites.values()} == {75, 101}


def test_nothing_unresolved_vanishes():
    doc = load("acquisition.json")
    listed = {u["id"] for u in doc["unresolved"]}
    for s in doc["script_sites"]:
        assert s["status"] in {"resolved", "version_branch", "candidates", "unresolved"}
        if s["status"] in {"candidates", "unresolved"}:
            assert s["id"] in listed, f"{s['id']} is {s['status']} but not in `unresolved`"
    assert {"hg_engine", "mystery_gift", "link_trade_gts_pal_park"} <= listed
    assert any(u["status"] == "open_policy" for u in doc["unresolved"]), "daycare withdrawal open question"
    # the runtime HG/SS branches that decide a site are carried, with the version each side takes
    by_id = {s["id"]: s for s in doc["script_sites"]}
    assert by_id["scr_seq_0021_D17R0110:73"]["level"]["by_version"] == {"heartgold": 45, "soulsilver": 70}  # Ho-Oh
    assert by_id["scr_seq_0104_D40R0107:85"]["level"]["by_version"] == {"heartgold": 70, "soulsilver": 45}  # Lugia
    latios = by_id["scr_seq_0750_T03:396"]["species"]["by_version"]
    assert (latios["heartgold"]["name"], latios["soulsilver"]["name"]) == ("Latios", "Latias")
    assert {c["name"] for c in by_id["scr_seq_0755_T03R0101:359"]["species"]["candidates"]} >= {"Omanyte", "Cranidos"}


def test_site_areas_exist_and_zones_follow_d10():
    doc, area = load("acquisition.json"), load("area_map.json")
    for s in doc["script_sites"]:
        assert s["area"] in area["areas"], s["id"]
        assert s["zone"] == acq.ZONE_POLICY[s["kind"]]["zone"]
    zones = {s["kind"]: s["zone"] for s in doc["script_sites"]}
    assert zones["roamer"] == "roamer" and zones["static"] == "gift" and zones["npc_exchange"] == "exchange"
    for bank in load("encounters.json")["banks"].values():
        assert all(a in area["areas"] for a in bank["areas"])


def test_sites_match_the_independent_research_inventory():
    """The generator re-derives the hits; docs/gen4/research/data was produced separately, so every research
    hit must still be one of ours -- and the difference is pinned, so a hit the generator invents (or drops)
    cannot ride on the subset comparison."""
    ours = {(s["file"], s["line"], s["command"], s["map_id"]) for s in load("acquisition.json")["script_sites"]}
    research = json.loads((ROOT / "docs/gen4/research/data/acquisition_manifest.json").read_text(encoding="utf-8"))
    theirs = {(h["file"], h["line"], h["command"], h["map_num"]) for h in research["hits"]}
    assert theirs <= ours, f"the generator lost a researched hit: {sorted(theirs - ours)}"
    assert ours - theirs == {("scr_seq_0265.s", 101, "GiveDaycareEgg", 38)}, f"unexpected new hits: {sorted(ours - theirs)}"
    raw = json.loads((ROOT / "docs/gen4/research/data/npc_trades.json").read_text(encoding="utf-8"))
    recs = load("acquisition.json")["npc_trade_records"]
    for r, old in zip(recs, raw, strict=True):
        assert r["record"] == {k: v for k, v in old["hg"].items() if not k.endswith("_name")} and old["hg"] == old["ss"]
        assert r["source_class"] == old["source_class"]


# ── C producers: a new, unclassified call site must fail generation ─────────────────────────────


def test_unclassified_c_producer_fails_generation(clone, tmp_path, monkeypatch):
    """Control (revert-tested by deleting the `problems` check in scan_c): a classified producer that
    disappears from the table, or a changed call count, is reported."""
    producers, problems = acq.scan_c(clone)
    assert problems == [] and len(producers) == 25
    victim = ("src/trainer_data.c", "CreateNPCTrainerParty", "Party_AddMon")
    monkeypatch.setitem(acq.C_PRODUCERS, victim, {**acq.C_PRODUCERS[victim], "n": 3})
    _p, problems = acq.scan_c(clone)
    assert any("4 call sites" in p for p in problems)
    monkeypatch.delitem(acq.C_PRODUCERS, victim)
    _p, problems = acq.scan_c(clone)
    assert any("unclassified C producer" in p and "trainer_data.c" in p for p in problems)


def test_npc_narc_parse_matches_record_count(clone):
    recs = acq.parse_npc_narc((clone / "files/a/1/1/2").read_bytes())
    assert len(recs) == 13 and recs[0]["give_species"] == 95 and recs[4]["give_species"] == 78


# ── reachability: story gates per script site (CARD gen4-G2-acq-reachability) ───────────────────
# Every control names the revert that must turn it red (all revert-tested in a scratch copy).

REQUIRED_KINDS = {"gift", "loan", "npc_exchange", "static", "egg", "starter"}


def walk_gates(obj):
    """Independent of the generator: every dict carrying both `token` and `cite` anywhere in a record."""
    if isinstance(obj, dict):
        # (an `npc:std_script` TRIGGER carries the std script's name as its `token`; it is not a gate)
        if "token" in obj and "cite" in obj and obj.get("kind") != "npc:std_script":
            yield obj
        for v in obj.values():
            yield from walk_gates(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from walk_gates(v)


def test_every_required_site_has_a_reachability_record():
    """Revert: drop the `site["reachability"] = ...` line in build_doc (or any record in the committed JSON)."""
    doc = load("acquisition.json")
    sites = doc["script_sites"]
    assert len(sites) == 62 and {s["kind"] for s in sites if s["kind"] in REQUIRED_KINDS} == REQUIRED_KINDS
    for s in sites:
        rec = s.get("reachability")
        assert rec, f"{s['id']}: no reachability record"
        assert rec["map"]["id"] == s["map_id"] and rec["map"]["const"] == s["map_const"] and rec["map"]["token"] == s["map_token"]
        assert rec["status"] in {"resolved", "partial", "unresolved"}
        assert (rec["status"] == "resolved") == (rec["unresolved"] == []), f"{s['id']}: status {rec['status']} vs unresolved list"
        assert rec["sources"]["script"].endswith(s["file"])
        assert rec["status"] == "unresolved" or rec["entries"], s["id"]
    # the producer plan's named sites (docs/gen4/G2_PRODUCER_PLAN.md V4-V10) are all covered with at least one entry
    by_id = {s["id"]: s for s in sites}
    for sid in ("scr_seq_0843_T20R0101:169", "scr_seq_0892_T25R0401:34", "scr_seq_0098_D38R0104:37", "scr_seq_0241_R35R0101:61", "scr_seq_0880_T24R0201:47", "scr_seq_0864_T22R0601:40", "scr_seq_0092_D36R0101:1910", "scr_seq_0243_R36:100", "scr_seq_0860_T22PC0101:79", "scr_seq_0858_T22FS0101:53"):
        assert by_id[sid]["reachability"]["entries"], sid
    inv = doc["inventory"]
    assert inv["reachability_status_counts"] == dict(sorted(Counter(s["reachability"]["status"] for s in sites).items()))
    assert sum(sum(v.values()) for v in inv["reachability_by_command"].values()) == 62
    assert "NOT derived" in doc["reachability_scope"]


def test_reachability_statuses_are_pinned_per_command():
    """All 62 sites derive fully at the pin (the 9 dead label blocks, referenced by no jump, are recorded as `dead_blocks_ignored`, not gaps). A new unmodelled
    condition or an entry without a trigger turns a site `partial` and fails here until it is looked at."""
    inv = load("acquisition.json")["inventory"]
    assert inv["reachability_status_counts"] == {"resolved": 62}
    assert {c: sum(v.values()) for c, v in inv["reachability_by_command"].items()} == inv["script_command_counts"]


def test_reachability_gate_cites_exist_in_the_pinned_clone_and_hold_the_token(clone):
    """Revert: break a cite in a scratch copy (see the mutation test below) or delete the cite_problem check."""
    n = 0
    for s in load("acquisition.json")["script_sites"]:
        for g in walk_gates(s["reachability"]):
            rel, _, line = g["cite"].rpartition(":")
            text = (clone / rel).read_text(encoding="utf-8", errors="replace").splitlines()
            assert 1 <= int(line) <= len(text) and g["token"] in text[int(line) - 1], f"{s['id']}: {g['cite']} does not hold {g['token']}"
            n += 1
        for e in s["reachability"]["entries"]:
            for t in e["triggers"]:
                rel, _, line = t["cite"].rpartition(":")
                # a per-map trigger cites the `_EV_scr_seq_` expression that starts the entry; a COMMON (std)
                # script trigger cites the scriptId NAME the map object calls
                want = t.get("token", "_EV_scr_seq_")
                assert want in (clone / rel).read_text(encoding="utf-8", errors="replace").splitlines()[int(line) - 1], f"{s['id']}: trigger {t['cite']}"
    assert n > 300


def test_reachability_spot_checks_read_the_source_not_the_generator(clone):
    """Hand-checked gates (read from the pinned .s / zone_event json): starter, Bill's Eevee, version-split roamers."""
    by_id = {s["id"]: s["reachability"] for s in load("acquisition.json")["script_sites"]}
    assert {(g["token"], g["state"]) for g in by_id["scr_seq_0843_T20R0101:169"]["gates"]} == {("FLAG_GOT_STARTER", "unset")}
    eevee = by_id["scr_seq_0892_T25R0401:34"]
    assert {(g["token"], g.get("state"), g.get("op"), g.get("value")) for g in eevee["gates"]} >= {
        ("FLAG_HIDE_GOLDENROD_BILL", "unset", None, None),
        ("FLAG_GOT_EEVEE_FROM_BILL", "unset", None, None),
        ("VAR_SPECIAL_RESULT", None, "==", "0"),
        ("VAR_SPECIAL_x8005", None, "!=", "6"),
    }
    assert next(g for g in eevee["gates"] if g["scope"] == "npc_visibility")["cite"].startswith("files/fielddata/eventdata/zone_event/196_T25R0401.json:")
    latias = next(g for g in by_id["scr_seq_0776_T06:70"]["gates"] if g["class"] == "version")
    latios = next(g for g in by_id["scr_seq_0776_T06:87"]["gates"] if g["class"] == "version")
    assert latias["versions"] == ["heartgold"] and latios["versions"] == ["soulsilver"], "Latias is the HG roamer, Latios the SS one"
    src = (clone / "files/fielddata/script/scr_seq/scr_seq_0776_T06.s").read_text(encoding="utf-8").splitlines()
    assert src[69].strip() == "CreateRoamer 2" and src[86].strip() == "CreateRoamer 3"


def test_reachability_verifier_is_red_on_a_missing_record_an_uncited_gate_and_a_wrong_cite(clone):
    """Revert-tested: deleting a record, a cite, or pointing a cite at a line without the token each turns
    verify_reachability red; the unmutated doc is clean."""
    sites = copy.deepcopy(load("acquisition.json")["script_sites"])
    assert acq.verify_reachability(clone, sites) == []
    missing = copy.deepcopy(sites)
    del missing[0]["reachability"]
    assert any("no reachability record" in p for p in acq.verify_reachability(clone, missing))
    uncited = copy.deepcopy(sites)
    del next(walk_gates(uncited[0]["reachability"]))["cite"]
    assert any("uncited" in p for p in acq.verify_reachability(clone, uncited))
    wrong = copy.deepcopy(sites)
    gate = next(walk_gates(wrong[0]["reachability"]))
    gate["cite"] = gate["cite"].rpartition(":")[0] + ":1"
    assert any("does not hold" in p for p in acq.verify_reachability(clone, wrong))
    bad_status = copy.deepcopy(sites)
    bad_status[0]["reachability"]["status"] = "partial"
    assert any("does not match" in p for p in acq.verify_reachability(clone, bad_status))
    bad_trigger = copy.deepcopy(sites)
    next(t for e in bad_trigger[0]["reachability"]["entries"] for t in e["triggers"])["cite"] = "files/fielddata/script/scr_seq/nope.s:1"
    assert any("names no file" in p for p in acq.verify_reachability(clone, bad_trigger))


def test_a_gate_without_a_cite_fails_generation(clone, monkeypatch):
    """Revert: delete the verify_reachability call in build_doc."""
    real = acq._gate
    monkeypatch.setattr(acq, "_gate", lambda *a, **k: {**real(*a, **k), "cite": ""})
    with pytest.raises(base.PretMismatch, match="uncited"):
        acq.build_doc(clone)


def test_reachability_regeneration_is_byte_identical(clone):
    """Revert: iterate a set (unordered) instead of sorted() in reachability()/flow()."""
    a, b = acq.build(clone), acq.build(clone)
    assert a == b and a["acquisition.json"] + "\n" == (DATA / "acquisition.json").read_bytes().replace(b"\r\n", b"\n").decode("utf-8")


def _reach(text: str, *, trig: bool = True):
    lines = text.strip("\n").splitlines()
    hit = next(i for i, ln in enumerate(lines) if "GiveMon" in ln)
    ms = types.SimpleNamespace(token="T", event_rel="ev.json", hdr_rel=None, event_member=1, hdr_member=None, triggers=lambda _n: [{"kind": "npc", "cite": "ev.json:1", "gates": []}] if trig else [])
    return acq.Script("x.s", lines), ms, hit


def _keys(gates: dict) -> set[tuple]:
    return {(g["token"], g.get("state"), g.get("op"), g.get("value")) for g in gates.values()}


def test_flow_reports_the_polarity_that_reaches_the_site():
    """Revert: swap NEG_OP / the not-taken state in jump_gate."""
    sc, _ms, hit = _reach(
        """
scr_seq_T_000:
	Compare VAR_C, 5
	GoToIfEq _0020
	GoToIfSet FLAG_A, _0020
	Compare VAR_B, 3
	GoToIfEq _0010
	End
_0010:
	GiveMon SPECIES_A, 5, 0, 0, 0, VAR_SPECIAL_RESULT
	End
_0020:
	End
"""
    )
    must, may = sc.flow(hit)["roots"]["scr_seq_T_000"]
    assert _keys(must) == {("VAR_C", None, "!=", "5"), ("FLAG_A", "unset", None, None), ("VAR_B", None, "==", "3")} and may == {}


def test_flow_handles_shared_subroutines_loops_and_switch_cases():
    """Revert: stop walking at the first predecessor / drop the Case edge (the T07R0501 prize corner is only reachable through Case)."""
    sc, _ms, hit = _reach(
        """
scr_seq_T_000:
	GoToIfSet FLAG_X, _0040
	Call _0010
	End
scr_seq_T_001:
	Call _0010
	End
_0010:
	Switch VAR_S
	Case 1, _0020
	Case 2, _0020
	GoTo _0030
_0020:
	GoToIfSet FLAG_L, _0030
	GiveMon SPECIES_A, 5, 0, 0, 0, VAR_SPECIAL_RESULT
	End
_0030:
	GoTo _0020
_0040:
	End
"""
    )
    fl = sc.flow(hit)
    assert sorted(fl["roots"]) == ["scr_seq_T_000", "scr_seq_T_001"] and fl["unresolved"] == []
    m0, may0 = fl["roots"]["scr_seq_T_000"]
    m1, _may1 = fl["roots"]["scr_seq_T_001"]
    assert _keys(m0) == {("FLAG_X", "unset", None, None), ("FLAG_L", "unset", None, None)}, "the walk terminates on the _0020/_0030 loop and the loop-only gate is not necessary"
    assert _keys(m1) == {("FLAG_L", "unset", None, None)}
    assert {("VAR_S", None, "==", "1"), ("VAR_S", None, "!=", "1")} <= _keys(may0)


def test_flow_records_dead_blocks_and_flags_unmodelled_conditions():
    """Revert: treat a no-predecessor block as a root, or drop the `unresolved` entry for an unrecognised GoToIf."""
    sc, ms, hit = _reach(
        """
scr_seq_T_000:
	GoTo _0010
_0005:
	GoTo _0010
_0010:
	GoToIfNoItemSpace ITEM_X, 1, _0020
	GiveMon SPECIES_A, 5, 0, 0, 0, VAR_SPECIAL_RESULT
	End
_0020:
	End
"""
    )
    fl = sc.flow(hit)
    assert [d["label"] for d in fl["dead"]] == ["_0005"] and list(fl["roots"]) == ["scr_seq_T_000"]
    assert len(fl["unresolved"]) == 1 and "GoToIfNoItemSpace" in fl["unresolved"][0]["reason"]
    rec = acq.reachability(sc, ms, hit, 1, "MAP_T", StubStd())
    assert rec["status"] == "partial" and rec["dead_blocks_ignored"][0]["label"] == "_0005" and rec["gates"] == []


def test_reachability_statuses_resolved_partial_unresolved():
    """Revert: make `status` always "resolved"."""
    body = "scr_seq_T_000:\n\tGoToIfSet FLAG_A, _0020\n\tGiveMon SPECIES_A, 5, 0, 0, 0, VAR_SPECIAL_RESULT\n\tEnd\n_0020:\n\tEnd\n"
    sc, ms, hit = _reach(body)
    rec = acq.reachability(sc, ms, hit, 1, "MAP_T", StubStd())
    assert rec["status"] == "resolved" and [(g["token"], g["state"], g["cite"]) for g in rec["gates"]] == [("FLAG_A", "unset", "x.s:2")]
    sc, ms, hit = _reach(body, trig=False)
    rec = acq.reachability(sc, ms, hit, 1, "MAP_T", StubStd())
    assert rec["status"] == "partial" and "trigger of scr_seq_T_000" in rec["unresolved"][0]["what"] and [g["token"] for g in rec["gates"]] == ["FLAG_A"]
    sc, ms, hit = _reach("_0010:\n\tGiveMon SPECIES_A, 5, 0, 0, 0, VAR_SPECIAL_RESULT\n\tEnd\n")
    rec = acq.reachability(sc, ms, hit, 1, "MAP_T", StubStd())
    assert rec["status"] == "unresolved" and rec["entries"] == [] and rec["gates"] == [] and rec["unresolved"]


# ── hge mode: tools/gen_gen4_acquisition.py hge -> data/games/gen4_hge/acquisition.json ──────────
# Script-side sections are the vanilla ones (proved by member hashes of both ROMs); the C inventory is
# scanned from the pinned hg-engine fork. ROM/fork/xMAP absent -> skip BY NAME; present but wrong -> FAIL.

HGE_DATA = ROOT / "data" / "games" / "gen4_hge"


def load_hge() -> dict:
    return json.loads((HGE_DATA / "acquisition.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def hge_inputs() -> dict:
    """pret clone, hge ROM, vanilla ROM, fork clone, xMAP -- all at their pins."""
    try:
        pret = base.locate_clone()
        base.verify_clone(pret)
        hge_rom, hge_sha = names.locate_rom("hge", None)
        van_rom, van_sha = names.locate_rom("hgss", None)
        src, commit = names.locate_src("hge", None)
        xmap = acq.locate_xmap(None)
    except (base.PretAbsent, names.Absent) as exc:
        reason = str(exc)
        pytest.skip(reason if reason.startswith(gen4_pins.HGE_FORK_ABSENT) else f"hge acquisition input not available: {reason}")
    except (base.PretMismatch, names.Mismatch) as exc:
        pytest.fail(str(exc))
    return {"pret": pret, "hge_rom": hge_rom, "hge_sha": hge_sha, "van_rom": van_rom, "van_sha": van_sha, "src": src, "commit": commit, "xmap": xmap}


def hge_args(i: dict, **override: str) -> list[str]:
    a = {"pret": str(i["pret"]), "rom": str(i["hge_rom"]), "vanilla-rom": str(i["van_rom"]), "src": str(i["src"]), "xmap": str(i["xmap"]), **override}
    return ["hge"] + [x for k, v in a.items() for x in (f"--{k}", v)]


def test_hge_check_mode_matches_committed(hge_inputs, monkeypatch, capsys):
    rc, err = run_tool(acq, [*hge_args(hge_inputs), "--check"], monkeypatch, capsys)
    assert rc == 0, f"hge acquisition drifted from the committed JSON: {err}"


def test_hge_check_mode_detects_drift(hge_inputs, monkeypatch, capsys, tmp_path):
    """Control: a one-byte edit of the committed file must turn --check red."""
    text = (HGE_DATA / "acquisition.json").read_text(encoding="utf-8")
    (tmp_path / "acquisition.json").write_text(text.replace(hge_inputs["commit"], "0" * 40, 1), encoding="utf-8")
    rc, err = run_tool(acq, [*hge_args(hge_inputs), "--check", "--out-dir", str(tmp_path)], monkeypatch, capsys)
    assert rc == 1 and "DRIFT" in err


@pytest.mark.parametrize("flag", ["rom", "vanilla-rom", "src", "xmap"])
def test_hge_absent_input_is_open_and_names_it(flag, hge_inputs, monkeypatch, capsys, tmp_path):
    missing = tmp_path / f"no-{flag}"
    rc, err = run_tool(acq, [*hge_args(hge_inputs, **{flag: str(missing)}), "--check"], monkeypatch, capsys)
    assert rc == 2 and "OPEN" in err and str(missing) in err


def test_hge_wrong_rom_fails_not_skips(hge_inputs, monkeypatch, capsys, tmp_path):
    fake = tmp_path / "not-the-pinned-rom.nds"
    fake.write_bytes(b"\0" * 64)
    rc, err = run_tool(acq, [*hge_args(hge_inputs, rom=str(fake)), "--check"], monkeypatch, capsys)
    assert rc == 1 and "FAIL" in err and "pinned" in err


def test_hge_committed_provenance_and_member_proof():
    doc = load_hge()
    pins = gen4_pins.ROM_SPECS
    assert doc["_schema"] == acq.HGE_SCHEMA
    assert doc["rom_sha1"] == pins["heartgold_hge"][0] and doc["vanilla_rom_sha1"] == pins["heartgold"][0]
    assert doc["source"]["fork_commit"] == gen4_pins.SOURCE_COMMITS["hg_engine_fork"]
    assert doc["source"]["pret"]["commit"] == PIN
    assert doc["source"]["inputs"] and all(len(h) == 64 for h in doc["source"]["inputs"].values())
    assert {"hooks", "include/config.h", "include/debug.h"} <= set(doc["source"]["inputs"])
    script, trade = doc["script_narc"], doc["trade_narc"]
    assert script["site_member_count"] == len(script["site_members"]) == 48 and script["member_count"] == 965
    assert all(m["vanilla_sha256"] == m["hge_sha256"] and len(m["hge_sha256"]) == 64 for m in script["site_members"].values())
    assert script["members_differing_in_hge"] == [3] and script["differing_members_holding_sites"] == []
    assert trade["all_equal"] and trade["member_count"] == 13
    assert all(m["vanilla_sha256"] == m["hge_sha256"] for m in trade["members"].values())


def test_hge_script_sections_are_the_vanilla_ones():
    van, hge = load("acquisition.json"), load_hge()
    for key in ("script_sites", "npc_trade_records", "runtime_branches", "zone_policy", "special_modes"):
        assert hge[key] == van[key], key
    inv = hge["inventory"]
    assert inv["script_site_count"] == 62 and inv["npc_record_count"] == 13 and inv["commands_without_a_c_producer"] == []
    assert not any(u["id"] == "hg_engine" for u in hge["unresolved"]) and any(u["id"] == "hge_runtime_receipt" for u in hge["unresolved"])


def test_hge_c_producer_counts_are_pinned():
    doc = load_hge()
    inv = doc["inventory"]
    assert inv["c_producer_count"] == len(doc["c_producers"]) == 18
    assert inv["c_call_site_count"] == 14 and inv["c_inactive_call_site_count"] == 4 and inv["c_definition_count"] == len(doc["c_definitions"]) == 5
    assert inv["vanilla_producer_count"] == len(doc["vanilla_c_producers"]) == 25
    assert inv["vanilla_producer_status_counts"] == {"kept": 10, "kept_callee_replaced": 8, "patched_inline": 1, "replaced": 6}
    by_fn = {(r["file"], r["function"], r["api"]): r["hge_status"] for r in doc["vanilla_c_producers"]}
    assert by_fn[("src/script_pokemon_util.c", "GiveMon", "Party_AddMon")] == "replaced"
    assert by_fn[("src/field/scrcmd_pokemon_misc.c", "ScrCmd_GiveTogepiEgg", "Party_AddMon")] == "replaced"
    assert by_fn[("src/scrcmd_party.c", "ScrCmd_GiveMon", "GiveMon")] == "kept_callee_replaced", "the script command is kept, its helper is not"
    assert by_fn[("src/battle/battle_command.c", "Task_GetPokemon", "Party_AddMon")] == "kept_callee_replaced"
    assert by_fn[("src/choose_starter.c", "CreateStarter", "Party_AddMon")] == "patched_inline"
    assert by_fn[("src/get_egg.c", "GiveEggToPlayer", "Party_AddMon")] == "kept"
    assert doc["api_resolution"]["PokeParty_Add"]["vanilla_function"] == "Party_AddMon" and doc["api_resolution"]["PokeParty_Add"]["vanilla_status"] == "kept"
    assert doc["api_resolution"]["CreateBoxMonData"]["vanilla_status"] == "replaced"
    for r in doc["c_producers"]:
        assert r["cls"] in {"acquisition", "not_acquisition", "external", "infrastructure", "script_dispatch"}, r


def test_hge_c_scan_matches_the_tables(hge_inputs):
    calls, defs, problems = acq.scan_hge_c(hge_inputs["src"], acq.fork_defines(hge_inputs["src"]))
    assert problems == [] and len(calls) == 18 and len(defs) == 5


def test_unclassified_hge_c_producer_fails_generation(hge_inputs, monkeypatch, capsys):
    """Control (revert-tested by deleting the unclassified-producer problem in scan_hge_c): a classified
    producer that leaves the table, or a changed call count, is reported -- and `hge` exits 1."""
    src, defined = hge_inputs["src"], acq.fork_defines(hge_inputs["src"])
    victim = ("src/pokemon.c", "GiveMon", "PokeParty_Add")
    monkeypatch.setitem(acq.HGE_PRODUCERS, victim, {**acq.HGE_PRODUCERS[victim], "n": 2})
    assert any("1 active" in p for p in acq.scan_hge_c(src, defined)[2])
    monkeypatch.delitem(acq.HGE_PRODUCERS, victim)
    assert any("unclassified hge C producer" in p and "pokemon.c" in p for p in acq.scan_hge_c(src, defined)[2])
    rc, err = run_tool(acq, [*hge_args(hge_inputs), "--check"], monkeypatch, capsys)
    assert rc == 1 and "unclassified hge C producer" in err


def test_hge_scan_flags_a_new_producer_in_a_synthetic_fork(tmp_path):
    """ROM-free control: a PokeParty_Add in an unknown function is reported; commented-out calls are not call sites
    and #ifdef'd-out ones are carried as inactive lines."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "x.c").write_text(
        "#ifdef OFF\nvoid Dead(void)\n{\n    PokeParty_Add(p, m);\n}\n#endif\n"
        "// PokeParty_Add(p, m);\n"
        "void Fresh(void)\n{\n    /* PokeParty_Add(a, b); */\n    PokeParty_Add(p, m);\n}\n",
        encoding="utf-8",
    )
    _calls, _defs, problems = acq.scan_hge_c(tmp_path, set())
    problems = [p for p in problems if p.startswith("unclassified")]  # the real table's rows are all "no longer found" here
    assert len(problems) == 2
    assert any("'Fresh'" in p and "[11]" in p for p in problems) and any("'Dead'" in p and "[4]" in p for p in problems)


def test_hge_active_map_handles_ifdef_else_and_defined_expressions():
    lines = ["a", "#ifdef A", "b", "#else", "c", "#endif", "#if defined(A) && defined(B)", "d", "#endif", "#ifndef B", "e", "#endif"]

    def live(defined: set[str]) -> list[str]:
        return [ln for ln, on in zip(lines, acq.active_map(lines, defined), strict=True) if on and not ln.startswith("#")]

    assert live({"A"}) == ["a", "b", "e"]
    assert live({"A", "B"}) == ["a", "b", "d"]
    assert live(set()) == ["a", "c", "e"]
    with pytest.raises(ValueError, match="unsupported"):
        acq.active_map(["#if FOO >= 3", "x", "#endif"], set())


SITES = [{"file": "scr_seq_0002_A.s"}, {"file": "scr_seq_0005_B.s"}]


def synthetic_hashes(monkeypatch, *, flip: dict[str, set[int]] | None = None, drop: dict[str, int] | None = None):
    """narc_member_hashes stub: 10 script members and 3 trade members in both ROMs; `flip` changes the listed hge
    members, `drop` truncates the hge narc (member-count mismatch)."""
    flip, drop = flip or {}, drop or {}

    def fake(rom: str, narc: str) -> tuple[str, ...]:
        n = drop.get(narc, 10 if narc == acq.SCRIPT_NARC else 3) if rom == "hge" else (10 if narc == acq.SCRIPT_NARC else 3)
        return tuple(("x" if rom == "hge" and i in flip.get(narc, set()) else "h") + f"{narc}{i}" for i in range(n))

    monkeypatch.setattr(acq, "narc_member_hashes", fake)


def test_member_hash_equality_is_red_on_one_differing_site_member(monkeypatch):
    """Control (revert-tested by deleting the `v[n] != h[n]` problem in script_member_proof): one differing
    script member that holds a site fails; a differing member that holds none (member 3 in the real build) does not."""
    synthetic_hashes(monkeypatch)
    script, trade, problems = acq.script_member_proof("vanilla", "hge", SITES)
    assert problems == [] and script["site_member_count"] == 2 and trade["all_equal"]
    synthetic_hashes(monkeypatch, flip={acq.SCRIPT_NARC: {5}})
    script, _t, problems = acq.script_member_proof("vanilla", "hge", SITES)
    assert len(problems) == 1 and "member 5 (scr_seq_0005_B.s) differs" in problems[0]
    assert script["differing_members_holding_sites"] == [5]
    synthetic_hashes(monkeypatch, flip={acq.SCRIPT_NARC: {3}})
    script, _t, problems = acq.script_member_proof("vanilla", "hge", SITES)
    assert problems == [] and script["members_differing_in_hge"] == [3]
    synthetic_hashes(monkeypatch, flip={acq.TRADE_NARC: {1}})
    assert any("NPC trade records" in p for p in acq.script_member_proof("vanilla", "hge", SITES)[2])
    synthetic_hashes(monkeypatch, drop={acq.SCRIPT_NARC: 4})
    assert any("members in" in p or "missing" in p for p in acq.script_member_proof("vanilla", "hge", SITES)[2])


def test_real_member_hash_mismatch_fails_generation(hge_inputs, monkeypatch, capsys):
    """Control on the real ROMs (monkeypatched hash): one flipped hge member hash turns generation red."""
    real = acq.narc_member_hashes
    hge_rom = str(hge_inputs["hge_rom"])

    def flipped(rom: str, narc: str) -> tuple[str, ...]:
        h = list(real(rom, narc))
        if narc == acq.SCRIPT_NARC and rom == hge_rom:
            h[21] = "f" * 64  # scr_seq_0021_D17R0110 (Ho-Oh) holds sites
        return tuple(h)

    monkeypatch.setattr(acq, "narc_member_hashes", flipped)
    rc, err = run_tool(acq, [*hge_args(hge_inputs), "--check"], monkeypatch, capsys)
    assert rc == 1 and "member 21" in err and "differs" in err


# ── G2 review fixes (OMP cx-ec4c0e87): cross-entry resolution, namespaces, guards ─────────────────
# Each control below names the revert that must turn it red.


class _StubCtx:
    """The three things resolve() reads from Ctx."""

    species = {"SPECIES_A": 1, "SPECIES_B": 2}
    fossils: dict = {}

    @staticmethod
    def sp(sid: int) -> dict:
        return {"id": sid, "name": f"S{sid}"}


def _script(text: str) -> list[str]:
    return text.strip("\n").splitlines()


def _site(lines: list[str]) -> int:
    return next(i for i, ln in enumerate(lines) if "GiveMon" in ln)


def test_resolve_does_not_borrow_a_write_from_another_entry():
    """Revert: scan from line 0 instead of entry_start() in resolve() -> literal Species A (wrong script)."""
    lines = _script("""
scr_seq_X_000:
	SetVar VAR_SPECIAL_x8004, SPECIES_A
	End

scr_seq_X_001:
	GiveMon VAR_SPECIAL_x8004, 5, 0, 0, 0, VAR_SPECIAL_RESULT
	End
""")
    r = acq.resolve("VAR_SPECIAL_x8004", "species", lines, _site(lines), _StubCtx)
    assert r["resolution"] == "unresolved" and r["reason"] == "cross_entry" and r["outside_entry_lines"] == [2]
    own = _script("""
scr_seq_X_000:
	SetVar VAR_SPECIAL_x8004, SPECIES_A
	End

scr_seq_X_001:
	SetVar VAR_SPECIAL_x8004, SPECIES_B
	GiveMon VAR_SPECIAL_x8004, 5, 0, 0, 0, VAR_SPECIAL_RESULT
""")
    r = acq.resolve("VAR_SPECIAL_x8004", "species", own, _site(own), _StubCtx)
    assert r["resolution"] == "literal" and r["value"]["id"] == 2 and r["set_at_lines"] == [6]


def test_resolve_refuses_a_shared_subroutine_with_several_callers():
    """The Oak's Lab shape: entries each SetVar then `Call _0801`, the GiveMon lives in _0801 after the LAST
    entry. Textually the nearest entry owns it, but it is reached from all of them. Revert: drop the
    entry_roots() check -> literal for the last entry's value only."""
    lines = _script("""
scr_seq_X_000:
	SetVar VAR_SPECIAL_x8004, SPECIES_A
	Call _0801
	End

scr_seq_X_001:
	SetVar VAR_SPECIAL_x8004, SPECIES_B
	Call _0801
	End

_0801:
	GiveMon VAR_SPECIAL_x8004, 5, 0, 0, 0, VAR_SPECIAL_RESULT
	Return
""")
    assert acq.entry_roots(lines, _site(lines)) == ["scr_seq_X_000", "scr_seq_X_001"]
    r = acq.resolve("VAR_SPECIAL_x8004", "species", lines, _site(lines), _StubCtx)
    assert r["resolution"] == "unresolved" and r["reason"] == "cross_entry" and r["entries"] == ["scr_seq_X_000", "scr_seq_X_001"]


def test_resolve_keeps_a_local_label_inside_one_entry_literal():
    """Control for the control above: a jump-only label called only from its own entry stays resolvable."""
    lines = _script("""
scr_seq_X_000:
	SetVar VAR_SPECIAL_x8004, SPECIES_A
	Call _0801
	End

_0801:
	GiveMon VAR_SPECIAL_x8004, 5, 0, 0, 0, VAR_SPECIAL_RESULT
	Return
""")
    assert acq.entry_roots(lines, _site(lines)) == ["scr_seq_X_000"]
    r = acq.resolve("VAR_SPECIAL_x8004", "species", lines, _site(lines), _StubCtx)
    assert r["resolution"] == "literal" and r["set_at_lines"] == [2]


def test_generator_asserts_set_at_lines_stay_inside_the_entry():
    """Revert: delete the assert in assert_in_own_entry() -> no raise."""
    lines = _script("""
scr_seq_X_000:
	SetVar VAR_SPECIAL_x8004, SPECIES_A
	End

scr_seq_X_001:
	SetVar VAR_SPECIAL_x8004, SPECIES_B
	GiveMon VAR_SPECIAL_x8004, 5, 0, 0, 0, VAR_SPECIAL_RESULT
""")
    bad = {"id": "x", "species": {"resolution": "literal", "value": 1, "set_at_lines": [2]}}
    with pytest.raises(AssertionError, match="outside the site's entry"):
        acq.assert_in_own_entry(bad, lines, _site(lines))
    acq.assert_in_own_entry({"id": "x", "species": {"resolution": "literal", "set_at_lines": [6]}}, lines, _site(lines))


def test_committed_oak_lab_starter_is_cross_entry_not_candidates():
    doc = load("acquisition.json")
    by_id = {s["id"]: s for s in doc["script_sites"]}
    site = by_id["scr_seq_0740_T01R0301:629"]
    assert site["status"] == "unresolved" and site["species"]["reason"] == "cross_entry" and len(site["species"]["entries"]) == 3
    assert "set_at_lines" not in site["species"]
    assert site["id"] in {u["id"] for u in doc["unresolved"]}
    assert doc["inventory"]["status_counts"]["unresolved"] == 1
    # every set_at_lines citation in the committed data is one increasing run ending before the site
    for s in by_id.values():
        for v in s.values():
            if isinstance(v, dict) and v.get("set_at_lines"):
                assert v["set_at_lines"] == sorted(v["set_at_lines"]) and v["set_at_lines"][-1] < s["line"], s["id"]


def test_safari_encounter_keys_join_area_map_and_acquisition(clone):
    """Revert: drop the `safari_` prefix in encounters safari_for() -> keys are bare ('plains')."""
    assert set(json.loads(enc.build(clone)["encounters.json"])["safari"]["areas"]) == set(load("encounters.json")["safari"]["areas"])  # generator, not just the file
    safari = set(load("encounters.json")["safari"]["areas"])
    area_sub = {a for a, v in load("area_map.json")["areas"].items() if v["parent"] == "safari_zone"}
    assert safari == area_sub and len(safari) == 12 and all(k.startswith("safari_") for k in safari)
    assert safari == set(load("acquisition.json")["special_modes"]["safari"]["areas"])


def test_titled_keeps_a_letter_after_an_apostrophe_lowercase():
    """Revert: use the old r"[A-Za-z]+" pattern -> Farfetch'D."""
    assert base.titled("FARFETCH'D") == "Farfetch'd" and base.titled("FARFETCH’D") == "Farfetch’d"
    assert base.titled("MR. MIME") == "Mr. Mime" and base.titled("HO-OH") == "Ho-Oh" and base.titled("MIME JR.") == "Mime Jr."
    assert "’D" not in (DATA / "trainers.json").read_text(encoding="utf-8")


def test_site_count_is_a_real_pin(clone, monkeypatch):
    """Revert: restore the tautology `len(sites) == sum(Counter(...))` -> a changed count is not caught."""
    assert acq.SITE_COUNT == 62
    monkeypatch.setattr(acq, "SITE_COUNT", 61)
    with pytest.raises(AssertionError, match="script site count 62 != pinned 61"):
        acq.build_doc(clone)


def test_out_of_scope_commands_are_counted_and_pinned(clone, monkeypatch, tmp_path):
    """Revert: delete the count assert in scan_out_of_scope() (or the 1:1 assert) -> no raise."""
    doc = load("acquisition.json")["out_of_scope_commands"]
    assert {c: v["count"] for c, v in doc.items()} == {"RetrieveDaycareMon": 1, "MysteryGift": 14, "NPCTradeExec": 11, "GetFossilPokemon": 2}
    assert "GiveDaycareEgg" not in doc and "GiveDaycareEgg" not in acq.OUT_OF_SCOPE, "the egg is a placed site, not an unplaceable command"
    assert all(len(v["sites"]) == v["count"] and all(":" in x for x in v["sites"]) for v in doc.values())
    files = sorted((clone / "files/fielddata/script/scr_seq").glob("*.s"))
    assert acq.scan_out_of_scope(files) == doc
    with monkeypatch.context() as mp:
        mp.setitem(acq.OUT_OF_SCOPE, "MysteryGift", {**acq.OUT_OF_SCOPE["MysteryGift"], "count": 13})
        with pytest.raises(AssertionError, match="MysteryGift: 14 sites, pinned 13"):
            acq.scan_out_of_scope(files)
    # LoadNPCTrade / NPCTradeExec 1:1 per file: a synthetic file with a load and no exec
    f = tmp_path / "scr_seq_9999_X.s"
    f.write_text("scr_seq_X_000:\n\tLoadNPCTrade 1\n\tEnd\n", encoding="utf-8")
    with pytest.raises(AssertionError, match="not 1:1 per file"):
        acq.scan_out_of_scope([f])
    committed_loads = sum(1 for s in load("acquisition.json")["script_sites"] if s["command"] == "LoadNPCTrade")
    assert committed_loads == doc["NPCTradeExec"]["count"]


# ── COMMON (std) script sites: the Day-Care egg has no map of its own, only objects that run it ─────────


def _std_daycare(clone):
    """The Day-Care std script, the GiveDaycareEgg hit, and the owner/skip split the generator computes."""
    ctx, std = acq.Ctx(clone), acq.StdScripts(clone)
    sc = std.script(265)
    hit = next(i for i, ln in enumerate(sc.lines) if ln.strip() == "GiveDaycareEgg")
    return ctx, std, sc, hit, acq.std_callers(clone, ctx, std, sc, 265, hit)


def test_the_daycare_egg_site_is_bound_to_the_day_care_man_map_through_the_std_tables():
    """The command has no map token in its file name. The binding is: sScriptBankMapping puts `_std_daycare`
    (9500) in scr_seq member 265; `std_daycare_man` is that same id, so it is ScrDef index 0; the object that
    calls that member stands on map 38 (Route 34). Every hop is cited in the site record."""
    site = next(s for s in load("acquisition.json")["script_sites"] if s["command"] == "GiveDaycareEgg")
    via = site["via"]
    assert (site["id"], site["file"], site["line"]) == ("scr_seq_0265:101", "scr_seq_0265.s", 101)
    assert (site["map_id"], site["map_const"], site["map_token"], site["area"]) == (38, "MAP_ROUTE_34", "R34", "route_34")
    assert site["kind"] == "egg" and site["zone"] == "gift" and site["status"] == "resolved"
    assert (via["kind"], via["std"], via["scriptId"], via["member"], via["entry"]) == ("std_script", "std_daycare_man", 9500, 0, "scr_seq_0265_000")
    assert via["cite"].startswith("src/fieldmap.c:") and via["called_by"]["map_id"] == 38
    assert via["called_by"]["cite"] == "files/fielddata/eventdata/zone_event/035_R34.json:230"
    assert site["reachability"]["entries"][0]["triggers"][0]["token"] == "std_daycare_man"


def test_the_day_care_lady_is_not_an_egg_source_and_its_exclusion_is_recorded(clone):
    """`std_daycare_lady` (9501) is ScrDef index 1 -- the withdrawal member, which has no GiveDaycareEgg -- and it
    is run from the Route 34 DAYCARE interior (map 331). So the Lady's map must produce no egg site, and the
    reason must be on the site rather than absent."""
    _, std, sc, hit, (owners, skipped) = _std_daycare(clone)
    assert acq.std_member_of(sc, hit) == 0 and std.entries(sc)[0] == "scr_seq_0265_000"
    assert [o["std"] for o in owners] == ["std_daycare_man"] and [o["std"] for o in skipped] == ["std_daycare_lady"]
    assert skipped[0]["member"] == 1 and "does not contain the command" in skipped[0]["why"]
    assert {c["map_id"] for o in owners for c in o["called_by"]} == {38}
    assert {c["map_id"] for o in skipped for c in o["called_by"]} == {331}
    sites = [s for s in load("acquisition.json")["script_sites"] if s["command"] == "GiveDaycareEgg"]
    assert [s["map_id"] for s in sites] == [38], "the Lady's interior must not become an egg source"
    recorded = next(s for s in sites if s["map_id"] == 38)["via"]["members_without_this_command"]
    assert [r["std"] for r in recorded] == ["std_daycare_lady"] and recorded[0]["member"] == 1


def test_a_std_script_table_with_the_ladys_member_id_swapped_binds_the_lady_map_too(clone, monkeypatch):
    """One-byte revert control (`std_script.h`: `std_daycare_lady 9501` -> `9500`). If the binding ignored the
    ScrDef index and just took "an object that calls this file", swapping the ids would change nothing; here it
    must add the Lady's interior as a second egg site, so the discrimination above is real."""
    _, std, sc, hit, (owners, _skipped) = _std_daycare(clone)
    assert [o["std"] for o in owners] == ["std_daycare_man"]
    monkeypatch.setitem(std.consts, "std_daycare_lady", 9500)
    owners2, skipped2 = acq.std_callers(clone, acq.Ctx(clone), std, sc, 265, hit)
    assert sorted(o["std"] for o in owners2) == ["std_daycare_lady", "std_daycare_man"] and skipped2 == []
    assert {c["map_id"] for o in owners2 for c in o["called_by"]} == {38, 331}


def test_the_zone_event_prefix_is_a_narc_member_not_a_map_id(clone):
    """The trap, pinned on the generator's own table. 035_R34.json is map 38 and 302_R34R0101.json is map 331;
    only `.eventsBank` (src/data/map_headers.h) says so, and the file name's token agrees with it."""
    maps = base.Maps(clone)
    assert maps.by_events_bank[35][0] == 38 and maps.by_events_bank[302][0] == 331
    assert maps.rows[38]["const"] == "MAP_ROUTE_34" and maps.rows[331]["const"] == "MAP_ROUTE_34_DAYCARE"
    assert maps.rows[38]["events_bank"] == 35 and maps.rows[331]["events_bank"] == 302
    assert (maps.by_token["R34"], maps.by_token["R34R0101"]) == (38, 331)
    assert (38 != 35) and (331 != 302), "the prefixes are member numbers; reading one as a map id lands elsewhere"
    # both maps share a mapsec, so the egg's area is route_34 either way -- but the SITE is the Day-Care Man's
    assert maps.rows[38]["mapsec"] == maps.rows[331]["mapsec"] == "MAPSEC_ROUTE_34"
    assert maps.rows[38]["enc_bank"] == "R34" and maps.rows[331]["enc_bank"] is None


def test_every_std_script_bind_is_checked_against_the_header_that_claims_the_event_member(clone):
    """The generator asserts both directions for every zone_event file it binds (token vs `.eventsBank`); a map
    header that claims a member another header's token owns must fail generation, not silently pick a side."""
    ctx = acq.Ctx(clone)
    for rel, _line in acq.std_event_index(str(clone)).get("std_daycare_man", []):
        assert acq.event_bank_map(ctx, rel) == 38
    assert acq.event_bank_map(ctx, "files/fielddata/eventdata/zone_event/302_R34R0101.json") == 331
    with pytest.raises(AssertionError, match="its token names map"):
        broken = base.Maps(clone)
        broken.by_token.pop("R34R0101")
        monkey = acq.Ctx(clone)
        monkey.maps = broken
        acq.event_bank_map(monkey, "files/fielddata/eventdata/zone_event/302_R34R0101.json")


def test_the_hge_inventory_carries_the_same_bound_egg_site():
    """gen4_hge's script sections are the vanilla ones (proved by NARC member hashes), so the egg site must be
    in its inventory too -- and the hge pack's own gift split must place it identically."""
    van = {s["id"]: s for s in load("acquisition.json")["script_sites"]}
    hge = {s["id"]: s for s in load_hge()["script_sites"]}
    assert "scr_seq_0265:101" in hge and hge["scr_seq_0265:101"]["via"] == van["scr_seq_0265:101"]["via"]
    assert hge["scr_seq_0265:101"]["map_id"] == 38


def test_every_map_enc_bank_exists_in_the_bank_table(clone, monkeypatch):
    """Revert: delete the `set(users) <= set(banks)` assert in encounters build() -> no raise."""
    real = base.Maps

    class Bogus(real):
        def __init__(self, c):
            super().__init__(c)
            self.rows[next(iter(self.rows))]["enc_bank"] = "NOT_A_BANK"

    monkeypatch.setattr(base, "Maps", Bogus)
    with pytest.raises(AssertionError, match="NOT_A_BANK"):
        enc.build(clone)


def test_cli_with_no_files_fails(clone, monkeypatch, capsys):
    """Revert: drop the empty-set guard in cli() -> rc 0 (all([]) is True)."""
    monkeypatch.setattr(sys, "argv", ["x", "--pret", str(clone), "--check"])
    assert base.cli(lambda c: {}, "x") == 1
    assert "no files" in capsys.readouterr().err


def test_unused_maps_are_not_listed_as_area_places(clone):
    """Revert: delete the unused_maps partition in build_model() -> UNUSED consts back in `maps`."""
    model = base.build_model(clone)  # the generator itself, not just the committed file
    assert all("UNUSED" not in model["maps"].rows[m]["const"] for v in model["areas"].values() for m in v["maps"])
    area, loc = load("area_map.json"), load("locations.json")["locations"]
    total_unused = 0
    for aid, v in area["areas"].items():
        assert not any("UNUSED" in loc[str(m)]["const"] for m in v["maps"]), aid
        assert all("UNUSED" in loc[str(m)]["const"] for m in v["unused_maps"]), aid
        assert set(v["maps"]).isdisjoint(v["unused_maps"])
        total_unused += len(v["unused_maps"])
    assert total_unused == 23 and area["areas"]["national_park"]["unused_maps"] == [488]
    # the map -> area table still carries them, so the join is maps U unused_maps
    for mid, aid in area["maps"].items():
        assert aid is None or int(mid) in area["areas"][aid]["maps"] + area["areas"][aid]["unused_maps"]


# ── hge: script DISPATCH (opcode -> handler) and Vanilla address-space guards ────────────────────


def test_macro_opcodes_resolve_opcode_and_composite_macros():
    inc = (
        ".macro Plain a\n\t.short 7\n\t.short \\a\n.endm\n"
        ".macro Other\n\t.short 9\n.endm\n"
        ".macro Composite x\n\t.if \\x\n\tPlain \\x\n\t.else\n\tOther\n\t.endif\n.endm\n"
        ".macro Data\n\t.word 1\n.endm\n"
    )
    ops = acq.macro_opcodes(inc)
    assert ops["Plain"] == {7} and ops["Other"] == {9} and ops["Composite"] == {7, 9} and ops["Data"] == set()


def test_dispatch_check_is_red_on_a_used_unaccounted_hooked_handler():
    """Control (revert-tested by making check_dispatch never append the problem): a handler a site script reaches
    that the fork hooks fails unless the inventory accounts for it."""
    used = {"ScrCmd_GiveEgg": {"GiveEgg"}, "ScrCmd_GiveMon": {"GiveMon"}, "ScrCmd_Wait": {"Wait"}}
    hooked = {"ScrCmd_GiveEgg": ["ScrCmd_GiveEgg"], "ScrCmd_Unused": ["x"]}
    rows, problems = acq.check_dispatch(used, hooked, {"ScrCmd_GiveEgg": "fork row"})
    assert problems == [] and [r["handler"] for r in rows] == ["ScrCmd_GiveEgg"]
    hooked["ScrCmd_GiveMon"] = ["synthetic"]
    _rows, problems = acq.check_dispatch(used, hooked, {"ScrCmd_GiveEgg": "fork row"})
    assert len(problems) == 1 and "ScrCmd_GiveMon" in problems[0] and "synthetic" in problems[0]


def test_hge_committed_dispatch_check():
    disp = load_hge()["script_dispatch"]
    assert disp["command_table_size"] == 853 and disp["commands_used"] > 200 and disp["handlers_used"] > 200
    assert {r["handler"] for r in disp["hooked_handlers_used"]} == {"ScrCmd_CreateRoamer", "ScrCmd_GiveEgg", "ScrCmd_GiveTogepiEgg"}
    assert all(r["accounted_by"] for r in disp["hooked_handlers_used"])


def test_real_synthetic_hook_on_a_used_handler_fails_generation(hge_inputs):
    """A hook on ScrCmd_GiveMon (the GiveMon command's handler, kept in the real build) must fail the dispatch check."""
    van = acq.make_vanilla(hge_inputs["pret"], hge_inputs["xmap"], hge_inputs["van_rom"], hge_inputs["src"])
    files = sorted({s["file"] for s in load("acquisition.json")["script_sites"]})
    accounted = {"ScrCmd_CreateRoamer": "x", "ScrCmd_GiveEgg": "x", "ScrCmd_GiveTogepiEgg": "x"}
    sites = load("acquisition.json")["script_sites"]
    _doc, problems = acq.script_dispatch(hge_inputs["pret"], files, van, accounted, acq.std_member_scope(hge_inputs["pret"], sites))
    assert problems == []
    fn = van.find("ScrCmd_GiveMon")[0]
    van.hooks.append({"region": van.region[fn[3]], "name": "synthetic_hook", "addr": fn[0]})
    _doc, problems = acq.script_dispatch(hge_inputs["pret"], files, van, accounted, acq.std_member_scope(hge_inputs["pret"], sites))
    assert len(problems) == 1 and "ScrCmd_GiveMon" in problems[0] and "synthetic_hook" in problems[0]


def synthetic_vanilla(tmp_path: Path, xmap_lines: list[str], hooks: str, bases: dict[int, int]):
    xmap = tmp_path / "t.xMAP"
    xmap.write_text("\n".join(xmap_lines) + "\n", encoding="utf-8")
    return acq.Vanilla(xmap, "Static main\n    Object src/x.o\n", bases, hooks, set())


def test_unknown_overlay_hook_is_skipped_not_a_keyerror(tmp_path):
    van = synthetic_vanilla(tmp_path, ["  02000000 00000010 .text   foo\t(x.o)"], "arm9 a 08000000 1\n0099 b 08000010 1\n0098 c 02000020 1\n", {})
    assert van.skipped == 1 and [h["name"] for h in van.hooks] == ["a", "c"]


def test_overlapping_xmap_ranges_fail_instead_of_misclassifying(tmp_path):
    with pytest.raises(names.Mismatch, match="overlap"):
        synthetic_vanilla(tmp_path, ["  02000000 00000020 .text   foo\t(x.o)", "  02000010 00000010 .text   bar\t(x.o)"], "", {})
    synthetic_vanilla(tmp_path, ["  02000000 00000010 .text   foo\t(x.o)", "  02000010 00000010 .text   bar\t(x.o)"], "", {})  # adjacent is fine


def test_xmap_overlap_is_checked_against_the_running_max_end(tmp_path):
    """A(0x100,+0x40) B(0x120,+0x10) C(0x130,+0x20): B and C both sit inside A's span."""
    lines = ["  02000100 00000040 .text   fa\t(x.o)", "  02000120 00000010 .text   fb\t(x.o)", "  02000130 00000020 .text   fc\t(x.o)"]
    with pytest.raises(names.Mismatch, match=r"overlap in region arm9: fa .* and fb"):
        synthetic_vanilla(tmp_path, lines, "", {})


class NoHooks:
    """Vanilla stand-in for script_dispatch: no function is hooked."""

    hooks: list = []

    def find(self, name, obj=None):
        return []


def test_dispatch_flags_a_used_command_that_resolves_to_no_opcode(tmp_path):
    """Control (revert-tested by deleting the `not ops[cmd]` problem in script_dispatch): a used command whose macro
    parses to an empty opcode set (hex .short, .if-only body) would make the handler check silently blind."""
    (tmp_path / "src/data/fieldmap").mkdir(parents=True)
    (tmp_path / "asm/macros").mkdir(parents=True)
    (tmp_path / "files/fielddata/script/scr_seq").mkdir(parents=True)
    (tmp_path / "src/data/fieldmap/script_cmd_table.h").write_text("const ScrCmdFunc gScriptCmdTable[] = {\n    ScrCmd_Nop,\n    ScrCmd_Wait,\n};\n", encoding="utf-8")
    (tmp_path / "asm/macros/script.inc").write_text(".macro Wait\n\t.short 1\n.endm\n.macro HexOp\n\t.short 0xFD13\n.endm\n", encoding="utf-8")
    (tmp_path / "asm/macros/movement.inc").write_text("", encoding="utf-8")
    (tmp_path / "files/fielddata/script/scr_seq/a.s").write_text("scr_seq_0001_A:\n\tWait\n", encoding="utf-8")
    (tmp_path / "files/fielddata/script/scr_seq/b.s").write_text("scr_seq_0002_B:\n\tWait\n\tHexOp\n", encoding="utf-8")
    doc, problems = acq.script_dispatch(tmp_path, ["a.s"], NoHooks(), {})
    assert problems == [] and doc["handlers_used"] == 1
    (tmp_path / "asm/macros/script.inc").write_text(".macro Wait\n\t.short 1\n.endm\n.macro HexOp\n\t.short 0xFD13\n.endm\n.macro ScrDef\n\t.word 0\n.endm\n", encoding="utf-8")
    (tmp_path / "files/fielddata/script/scr_seq/c.s").write_text("scr_seq_0003_C:\n\tScrDef _x\n\tWait\n", encoding="utf-8")
    assert acq.script_dispatch(tmp_path, ["c.s"], NoHooks(), {})[1] == [], "a header/data macro has no handler and is accepted"
    _doc, problems = acq.script_dispatch(tmp_path, ["a.s", "b.s"], NoHooks(), {})
    assert len(problems) == 1 and "HexOp" in problems[0] and "no opcode" in problems[0] and "b.s" in problems[0]


def test_header_macro_set_is_exactly_the_empty_opcode_macros(hge_inputs):
    """Pin: the macros of script.inc that parse to no opcode are exactly HEADER_MACROS (14)."""
    ops = acq.macro_opcodes(base.read(hge_inputs["pret"], "asm/macros/script.inc"))
    assert {n for n, o in ops.items() if not o} == set(acq.HEADER_MACROS) and len(acq.HEADER_MACROS) == 14


# ── hge reachability: shared with HG only because the event + header members are byte-identical ──


def test_hge_reachability_is_the_vanilla_one_and_its_sources_are_proved():
    """Revert: drop `reachability_narc` from build_hge or the member comparison in reachability_member_proof."""
    van, hge = load("acquisition.json"), load_hge()
    assert [s["reachability"] for s in hge["script_sites"]] == [s["reachability"] for s in van["script_sites"]]
    assert hge["inventory"]["reachability_status_counts"] == van["inventory"]["reachability_status_counts"] and hge["reachability_scope"] == van["reachability_scope"]
    proof = hge["reachability_narc"]
    ev, hdr = proof["events"], proof["header"]
    assert ev["narc"] == "a/0/3/2" and ev["member_count"] == 491 and ev["members_differing_in_hge"] == []
    assert hdr["narc"] == "a/0/1/2" and hdr["members_differing_in_hge"] == [3] and "3" not in hdr["members"], "member 3 (common scripts) differs; no header member is 3, so the ENTRY question is settled; the CallStd PATH question is callstd below"
    for key in ("events", "header"):
        want = {str(s["reachability"]["sources"][key]["member"]) for s in van["script_sites"] if s["reachability"]["sources"][key]}
        assert set(proof[key]["members"]) == want and want
        assert all(m["vanilla_sha256"] == m["hge_sha256"] and len(m["hge_sha256"]) == 64 for m in proof[key]["members"].values())
    assert ev["members"]["58"]["file"].endswith("058_T20R0101.json") and hdr["members"]["616"]["file"].endswith("scr_seq_0616_T20R0101_hdr.s")


def _reach_site(ev: int, hdr: int | None) -> dict:
    src = {"script": "x.s", "events": {"file": f"{ev:03d}_T.json", "narc": acq.EVENT_NARC, "member": ev}, "header": {"file": f"scr_seq_{hdr:04d}_T_hdr.s", "narc": acq.SCRIPT_NARC, "member": hdr} if hdr is not None else None}
    return {"reachability": {"sources": src}}


def test_reachability_member_proof_is_red_on_one_differing_event_or_header_member(monkeypatch):
    """Revert: delete the `v[n] != h[n]` problem in reachability_member_proof."""

    def stub(flip: set[tuple[str, int]] = frozenset(), cut: bool = False):
        def fake(rom: str, narc: str) -> tuple[str, ...]:
            n = 8 if cut and rom == "hge" else 10
            return tuple(("x" if rom == "hge" and (narc, i) in flip else "h") + f"{narc}{i}" for i in range(n))

        monkeypatch.setattr(acq, "narc_member_hashes", fake)

    sites = [_reach_site(4, 6), _reach_site(5, None)]
    stub()
    proof, problems = acq.reachability_member_proof("vanilla", "hge", sites)
    assert problems == [] and sorted(proof["events"]["members"]) == ["4", "5"] and list(proof["header"]["members"]) == ["6"]
    stub({(acq.EVENT_NARC, 5)})
    assert any("a/0/3/2 member 5" in p and "differs" in p for p in acq.reachability_member_proof("vanilla", "hge", sites)[1])
    stub({(acq.SCRIPT_NARC, 6)})
    assert any("a/0/1/2 member 6" in p and "differs" in p for p in acq.reachability_member_proof("vanilla", "hge", sites)[1])
    stub({(acq.SCRIPT_NARC, 3), (acq.EVENT_NARC, 0)})  # members holding no reachability source may differ
    proof, problems = acq.reachability_member_proof("vanilla", "hge", sites)
    assert problems == [] and proof["header"]["members_differing_in_hge"] == [3]
    stub(cut=True)
    assert any("members in" in p for p in acq.reachability_member_proof("vanilla", "hge", sites)[1])


@pytest.mark.parametrize(("narc", "member"), [(acq.EVENT_NARC, 58), (acq.SCRIPT_NARC, 616)])
def test_real_event_or_header_member_mismatch_fails_generation(hge_inputs, monkeypatch, capsys, narc, member):
    """Control on the real ROMs (monkeypatched hash): the Elm lab zone_event member (58) or its header script (616)
    flipped in the hge ROM turns generation red."""
    real = acq.narc_member_hashes
    hge_rom = str(hge_inputs["hge_rom"])

    def flipped(rom: str, name: str) -> tuple[str, ...]:
        h = list(real(rom, name))
        if name == narc and rom == hge_rom:
            h[member] = "f" * 64
        return tuple(h)

    monkeypatch.setattr(acq, "narc_member_hashes", flipped)
    rc, err = run_tool(acq, [*hge_args(hge_inputs), "--check"], monkeypatch, capsys)
    assert rc == 1 and f"{narc} member {member}" in err and "differs" in err


# ── reachability fixes (OMP cx-1076f5ca): CallStd, empty-gate justification, dead edges ───────────
# Each control names the revert that must turn it red.


class StubStd:
    """What reachability() needs from StdScripts: resolve(arg) -> call dict | None, writes(member, entry) -> {token: cite}."""

    def __init__(self, writes: dict[str, dict[str, str]] | None = None):
        self._writes = writes or {}

    def resolve(self, arg: str):
        return {"std": arg, "id": 2000, "member": 3, "entry": f"scr_seq_0003_{arg[-3:]}", "callee_cite": f"files/fielddata/script/scr_seq/scr_seq_0003.s:{arg[-3:]}"} if arg.startswith("std_") else None

    def writes(self, member: int, entry: str) -> dict[str, str]:
        return self._writes.get(entry, {})


_CALLER = """
scr_seq_T_000:
	CallStd std_001
	GoToIfSet FLAG_C, _0020
	GiveMon SPECIES_A, 5, 0, 0, 0, VAR_SPECIAL_RESULT
	End
_0020:
	End
"""


def test_callstd_callee_that_writes_a_tested_flag_makes_the_site_partial_and_is_cited():
    """Revert: delete the callee-writes vs gate-token intersection in reachability() (F1)."""
    sc, ms, hit = _reach(_CALLER)
    rec = acq.reachability(sc, ms, hit, 1, "MAP_T", StubStd({"scr_seq_0003_001": {"FLAG_C": "files/fielddata/script/scr_seq/scr_seq_0003.s:99"}}))
    assert [c["std"] for c in rec["callstd"]["calls"]] == ["std_001"] and rec["callstd"]["calls"][0]["cite"] == "x.s:2"
    assert rec["status"] == "partial" and [(c["token"], c["write_cite"]) for c in rec["callstd"]["conflicts"]] == [("FLAG_C", "files/fielddata/script/scr_seq/scr_seq_0003.s:99")]
    assert any("CallStd std_001" in u["what"] and "FLAG_C" in u["what"] and u["cite"] == "x.s:2" for u in rec["unresolved"])
    clean = acq.reachability(sc, ms, hit, 1, "MAP_T", StubStd({"scr_seq_0003_001": {"FLAG_OTHER": "y:1"}}))
    assert clean["status"] == "resolved" and clean["callstd"]["conflicts"] == [] and len(clean["callstd"]["calls"]) == 1, "the check ran and found nothing"


def test_callstd_write_after_the_call_in_the_caller_is_not_a_conflict():
    """Revert: drop the `via after the call` exemption (the caller redefines the variable itself)."""
    sc, ms, hit = _reach(
        """
scr_seq_T_000:
	CallStd std_001
	GetMenuChoice VAR_SPECIAL_RESULT
	Compare VAR_SPECIAL_RESULT, 0
	GoToIfEq _0010
	End
_0010:
	GiveMon SPECIES_A, 5, 0, 0, 0, VAR_SPECIAL_RESULT
	End
"""
    )
    rec = acq.reachability(sc, ms, hit, 1, "MAP_T", StubStd({"scr_seq_0003_001": {"VAR_SPECIAL_RESULT": "y:1"}}))
    assert rec["status"] == "resolved" and rec["callstd"]["conflicts"] == []


def test_unresolvable_callstd_target_is_unresolved_not_ignored():
    """Revert: skip a CallStd whose target does not resolve."""
    sc, ms, hit = _reach(_CALLER.replace("std_001", "bogus"))
    rec = acq.reachability(sc, ms, hit, 1, "MAP_T", StubStd())
    assert rec["status"] == "partial" and any("CallStd bogus" in u["what"] for u in rec["unresolved"])


def test_real_std_resolution_and_callee_closure(clone):
    """Revert: break the sScriptBankMapping parse (first bank with id >= lo) or the ScrDef entry order."""
    std = acq.StdScripts(clone)
    bag = std.resolve("std_bag_is_full")
    assert (bag["id"], bag["member"], bag["entry"]) == (2009, 3, "scr_seq_0003_009")
    assert std.resolve("std_in_person_evaluate_dex")["member"] == 148
    assert std.resolve("std_nonexistent") is None
    w = std.writes(3, "scr_seq_0003_009")
    assert isinstance(w, dict) and all(re.fullmatch(r"(FLAG|VAR)_\w+", t) for t in w)
    cite = next(iter(w.values()), None)
    if cite:
        rel, _, n = cite.rpartition(":")
        assert (clone / rel).read_text(encoding="utf-8").splitlines()[int(n) - 1]


def test_committed_callstd_check_covers_every_site_and_matches_the_source(clone):
    """Every CallStd line on a site's path is listed with a cite; the independent regex over the site files finds
    no CallStd the records omit when it sits in the same block chain (the whole-file superset must contain them)."""
    doc = load("acquisition.json")
    all_lines = {}
    for s in doc["script_sites"]:
        rec = s["reachability"]
        assert "callstd" in rec and set(rec["callstd"]) == {"calls", "conflicts"}, s["id"]
        text = all_lines.setdefault(s["file"], (clone / "files/fielddata/script/scr_seq" / s["file"]).read_text(encoding="utf-8").splitlines())
        for c in rec["callstd"]["calls"]:
            n = int(c["cite"].rpartition(":")[2])
            assert re.match(rf"\s*CallStd {c['std']}\b", text[n - 1]) and c["member"] >= 0, (s["id"], c)
        assert rec["status"] == ("resolved" if not rec["unresolved"] else "partial")
    pichu = next(s for s in doc["script_sites"] if s["id"] == "scr_seq_0092_D36R0101:1910")["reachability"]["callstd"]["calls"]
    assert {(c["std"], c["member"]) for c in pichu} == {("std_play_pichu_music", 3), ("std_fade_end_pichu_music", 3)}, "the only CallStd on a path to any site"
    inv = doc["inventory"]
    assert inv["callstd_conflict_count"] == sum(len(s["reachability"]["callstd"]["conflicts"]) for s in doc["script_sites"])
    assert inv["callstd_call_count"] == sum(len(s["reachability"]["callstd"]["calls"]) for s in doc["script_sites"]) > 0
    assert set(inv["callstd_targets"]) == {c["std"] for s in doc["script_sites"] for c in s["reachability"]["callstd"]["calls"]}


def test_a_new_callstd_gate_fails_generation(clone, monkeypatch):
    """Revert: remove the CallStd conflict guard in build_doc. A callee that may write a flag/var a site tests must be
    modelled (a cited gate) before generation succeeds; the guard fails rather than silently marking `partial`."""
    tokens = {g["token"] for s in load("acquisition.json")["script_sites"] for g in walk_gates(s["reachability"]) if g["scope"] == "script"}
    monkeypatch.setattr(acq.StdScripts, "writes", lambda self, member, entry: dict.fromkeys(tokens, "files/fielddata/script/scr_seq/scr_seq_0003.s:1"))
    with pytest.raises(base.PretMismatch, match="CallStd callee writes a tested flag"):
        acq.build_doc(clone)


def test_resolved_with_empty_gates_must_be_justified(clone):
    """Revert: delete the `empty_gates` justification (generation fails) or accept `resolved` + [] without one."""
    doc = load("acquisition.json")
    empties = [s for s in doc["script_sites"] if s["reachability"]["status"] == "resolved" and s["reachability"]["gates"] == []]
    assert [s["id"] for s in empties] == ["scr_seq_0092_D36R0101:1910"]
    just = empties[0]["reachability"]["empty_gates"]
    assert just["reason"] == "entries_have_disjoint_gate_sets" and {e["entry"] for e in just["entries"]} == {e["entry"] for e in empties[0]["reachability"]["entries"]} and len(just["entries"]) == 2
    for e in just["entries"]:
        assert e["gate_count"] > 0 and re.match(r"files/.*:\d+$", e["cite"])
        rel, _, n = e["cite"].rpartition(":")
        assert e["entry"] in (clone / rel).read_text(encoding="utf-8").splitlines()[int(n) - 1]
    sites = copy.deepcopy(doc["script_sites"])
    assert acq.verify_reachability(clone, sites) == []
    del next(s for s in sites if s["id"] == "scr_seq_0092_D36R0101:1910")["reachability"]["empty_gates"]
    assert any("empty gates" in p for p in acq.verify_reachability(clone, sites))


def test_missing_empty_gate_justification_fails_generation(clone, monkeypatch):
    """Revert: drop verify_reachability's empty-gates rule."""
    real = acq.reachability

    def stripped(*a, **k):
        rec = real(*a, **k)
        rec.pop("empty_gates", None)
        return rec

    monkeypatch.setattr(acq, "reachability", stripped)
    with pytest.raises(base.PretMismatch, match="empty gates"):
        acq.build_doc(clone)


def test_empty_gates_justification_kinds():
    """Revert: always report the disjoint reason."""
    sc, ms, hit = _reach("scr_seq_T_000:\n\tGiveMon SPECIES_A, 5, 0, 0, 0, VAR_SPECIAL_RESULT\n\tEnd\n")
    rec = acq.reachability(sc, ms, hit, 1, "MAP_T", StubStd())
    assert rec["status"] == "resolved" and rec["gates"] == [] and rec["empty_gates"]["reason"] == "no_gate_on_any_route"
    sc, ms, hit = _reach("scr_seq_T_000:\n\tGoToIfSet FLAG_A, _0020\n\tGiveMon SPECIES_A, 5, 0, 0, 0, VAR_SPECIAL_RESULT\n\tEnd\n_0020:\n\tEnd\n")
    assert "empty_gates" not in acq.reachability(sc, ms, hit, 1, "MAP_T", StubStd())


def test_flow_ignores_jump_edges_that_follow_an_unconditional_terminator_in_their_block():
    """F3. Revert: drop the terminator cut-off in Script.__init__/prefix. Dead lines after Return/End/GoTo
    (here a GoTo after Return) must not make a block a predecessor or contribute gates."""
    sc, _ms, hit = _reach(
        """
scr_seq_T_000:
	Call _0010
	End
_0010:
	GoToIfSet FLAG_A, _0030
	Return
	GoToIfSet FLAG_DEAD, _0030
	GoTo _0020
_0020:
	GiveMon SPECIES_A, 5, 0, 0, 0, VAR_SPECIAL_RESULT
	End
_0030:
	End
"""
    )
    fl = sc.flow(hit)
    assert fl["roots"] == {}, "the only way into _0020 is a GoTo after a Return: dead code"


def test_flow_keeps_live_gates_before_a_mid_block_call():
    """F3 control: a Call in the middle of a block does not stop the block; gates before it still apply."""
    sc, _ms, hit = _reach(
        """
scr_seq_T_000:
	GoToIfSet FLAG_A, _0030
	Call _0030
	GoTo _0020
_0020:
	GiveMon SPECIES_A, 5, 0, 0, 0, VAR_SPECIAL_RESULT
	End
_0030:
	Return
"""
    )
    must, _may = sc.flow(hit)["roots"]["scr_seq_T_000"]
    assert _keys(must) == {("FLAG_A", "unset", None, None)}


def test_successor_graph_is_built_once_per_script():
    """F5. Revert: rebuild succ inside the per-root loop (the attribute disappears)."""
    sc, _ms, _hit = _reach("scr_seq_T_000:\n\tGoTo _0010\n_0010:\n\tGiveMon SPECIES_A, 5, 0, 0, 0, VAR_SPECIAL_RESULT\n\tEnd\n")
    assert sc.succ[0] == [(1, "jump", 1)]


def test_reachability_pin_is_earned_after_the_callstd_check():
    """The 62/62 pin: derived from the per-site records AFTER the CallStd check, not typed in."""
    doc = load("acquisition.json")
    sites = doc["script_sites"]
    assert doc["inventory"]["callstd_conflict_count"] == 0
    resolved = [s for s in sites if s["reachability"]["status"] == "resolved" and s["reachability"]["callstd"]["conflicts"] == [] and s["reachability"]["unresolved"] == []]
    assert len(resolved) == len(sites) == 62 and doc["inventory"]["reachability_status_counts"] == {"resolved": len(resolved)}


def test_hge_callstd_paths_prove_or_flag_member_3():
    """Revert: drop `reachability_narc.callstd` from build_hge. Callees outside member 3 are proved byte-identical;
    callees in member 3 (which DIFFERS in hge) mark the site as inheriting an unproven path."""
    van, hge = load("acquisition.json"), load_hge()
    cs = hge["reachability_narc"]["callstd"]
    assert cs["member_3_differs"] is True and cs["narc"] == "a/0/1/2"
    used = {c["member"] for s in van["script_sites"] for c in s["reachability"]["callstd"]["calls"]}
    assert 3 in used and set(map(int, cs["members"])) == used
    for m, row in cs["members"].items():
        assert (row["vanilla_sha256"] == row["hge_sha256"]) == (m != "3") and row["proven_identical"] == (m != "3")
    crossing = {s["id"] for s in van["script_sites"] if any(c["member"] == 3 for c in s["reachability"]["callstd"]["calls"])}
    assert crossing and {i for i, v in cs["sites"].items() if v["status"] == "inherits_unproven_callstd_member_3"} == crossing
    assert {i for i, v in cs["sites"].items() if v["status"] == "proven_identical"} == {s["id"] for s in van["script_sites"]} - crossing
    assert all(v["members"] for i, v in cs["sites"].items() if i in crossing)
    assert [s["reachability"] for s in hge["script_sites"]] == [s["reachability"] for s in van["script_sites"]], "records stay vanilla; the inheritance status is a separate section"


def test_hge_callstd_proof_is_red_on_a_differing_non_3_member_and_flags_member_3(monkeypatch):
    """Revert: treat every differing member as acceptable (or none)."""

    def stub(flip: set[int]):
        monkeypatch.setattr(acq, "narc_member_hashes", lambda rom, narc: tuple(("x" if rom == "hge" and i in flip else "h") + str(i) for i in range(10)))

    sites = [
        {"id": "a", "reachability": {"callstd": {"calls": [{"member": 3}, {"member": 5}]}}},
        {"id": "b", "reachability": {"callstd": {"calls": [{"member": 5}]}}},
        {"id": "c", "reachability": {"callstd": {"calls": []}}},
    ]
    stub({3})
    proof, problems = acq.callstd_member_proof("vanilla", "hge", sites)
    assert problems == [] and proof["member_3_differs"] and proof["sites"]["a"]["status"] == "inherits_unproven_callstd_member_3" and proof["sites"]["b"]["status"] == "proven_identical" and proof["sites"]["c"]["status"] == "proven_identical"
    stub({5})
    _proof, problems = acq.callstd_member_proof("vanilla", "hge", sites)
    assert len(problems) == 1 and "member 5" in problems[0] and "differs" in problems[0]
    stub(set())
    proof, problems = acq.callstd_member_proof("vanilla", "hge", sites)
    assert problems == [] and not proof["member_3_differs"] and proof["sites"]["a"]["status"] == "proven_identical"
