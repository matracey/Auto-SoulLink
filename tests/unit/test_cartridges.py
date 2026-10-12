"""Cartridge ordering and contracts, checked against the pinned ROM artifacts."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from patch.gen1.tools import inject
from server import upr_pipeline
from server.adapters.gen1_rom_scan import fingerprint_rom

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _any_jar_is_trusted(monkeypatch):
    """These tests pass placeholder jar names; the hash pin is test_upr_jar_pinning's."""
    monkeypatch.setattr(upr_pipeline, "jar_is_trusted", lambda _jar: True)


def _vanilla():
    sources = {p: str(REPO / "patch" / "build" / f"gen1_{title}.gb")
               for p, title in (("a", "red"), ("b", "blue"))}
    for path in sources.values():
        if not Path(path).is_file():
            pytest.skip(f"clean dump absent: {path}")
    return sources


def _fake_pair(monkeypatch, check=None):
    def prepare(jar, settings_path, sources, out_dir):
        if check:
            check(sources)
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        players = {}
        for seed, (pid, source) in enumerate(sources.items(), 1):
            data = Path(source).read_bytes()
            output = Path(out_dir) / f"{pid}_randomized.gbc"
            output.write_bytes(data)
            players[pid] = {"output": str(output), "seed": seed,
                            "sha1": hashlib.sha1(data).hexdigest(),
                            "fingerprint": fingerprint_rom(data)}
        return {"upr_version": "4.6.1-slink1", "settings_sha256": "abc",
                "categories": ["wild"], "players": players}
    monkeypatch.setattr(upr_pipeline, "prepare_pair", prepare)


def test_vanilla_injects_after_randomizing_and_contract_pins_final(tmp_path, monkeypatch):
    # First falsifier: a contract for the intermediate rejects the handed-out companion.
    from server.cartridges import provision

    sources = _vanilla()
    def clean_inputs(actual):
        assert actual == sources
        assert all(inject.describe(Path(p).read_bytes())["already"] is None
                   for p in actual.values())
    _fake_pair(monkeypatch, clean_inputs)
    result = provision(str(tmp_path), sources, companion=True,
                       randomize={"settings_path": "settings.rnqs"}, jar="fake.jar")
    contract = json.loads((tmp_path / "rom_contract.json").read_text())
    for pid, player in result["players"].items():
        final = Path(player["output"]).read_bytes()
        intermediate = (tmp_path / "roms" / f"{pid}_randomized.gbc").read_bytes()
        assert inject.describe(final)["already"]["abi"] is not None
        assert inject.describe(final)["already"]["identical"]
        assert final != intermediate
        assert contract["players"][pid]["rom_sha1"] == hashlib.sha1(final).hexdigest()
        assert contract["players"][pid]["rom_sha1"] != hashlib.sha1(intermediate).hexdigest()
        assert player["fingerprint"] == fingerprint_rom(final) == fingerprint_rom(intermediate)
        assert contract["players"][pid]["fingerprint"] == player["fingerprint"]
        assert player["kind"] == "rand_companion"
        assert Path(player["output"]) == tmp_path / "roms" / f"{pid}.gb"   # a Red/Blue dump is .gb
        assert "output" not in result["randomizer"]["players"][pid]


def _pure(title="red"):
    path = REPO / ".cache" / "purergb" / f"poke{title}.gbc"
    if not path.is_file():
        pytest.skip(f"pinned pureRGB build absent: {path}")
    return str(path)


def _gen2():
    """{'a': clean pokegold, 'b': clean pokesilver} -- the pret decomp builds under
    .cache/gen2-build/ (tools/gen2_source_data.py's home), skipped when not built."""
    gold = REPO / ".cache" / "gen2-build" / "pokegold" / "pokegold.gbc"
    silver = REPO / ".cache" / "gen2-build" / "pokegold" / "pokesilver.gbc"
    for path in (gold, silver):
        if not path.is_file():
            pytest.skip(f"pinned Gen 2 build absent: {path}")
    return {"a": str(gold), "b": str(silver)}


@pytest.mark.parametrize("companion", [True])   # companion=False is refused now: test_a_companion_title_cannot_be_prepared_clean
@pytest.mark.parametrize("randomize", [False, True])
def test_vanilla_modes(tmp_path, monkeypatch, companion, randomize):
    from patch.tools.make_ups import ups_apply
    from server import cartridges, patcher

    sources = _vanilla()
    _fake_pair(monkeypatch)
    result = cartridges.provision(str(tmp_path), sources, companion=companion,
                                  randomize={"settings_path": "s.rnqs"} if randomize else None,
                                  jar="fake.jar")
    assert result["family"] == cartridges.FAMILY_VANILLA
    assert result["companion"] is companion
    assert (result["randomizer"] is not None) is randomize
    assert (tmp_path / "rom_contract.json").exists() is randomize
    for pid, title in (("a", "red"), ("b", "blue")):
        player = result["players"][pid]
        original = Path(sources[pid]).read_bytes()
        final = Path(player["output"]).read_bytes()
        expected = original
        if companion:
            expected = (inject.inject(original) if randomize else
                        ups_apply(original, Path(patcher.patch_path(f"rb-{title}")).read_bytes()))
        assert final == expected
        assert player["source"] == sources[pid]
        assert player["source_title"] == upr_pipeline.describe_rom(sources[pid], True)["title"]
        assert player["rom_sha1"] == hashlib.sha1(final).hexdigest()
        assert player["fingerprint"] == fingerprint_rom(final)
        expected_kind = "companion" if companion else "clean"
        if randomize:
            expected_kind = "rand_companion" if companion else "rand"
        assert player["kind"] == expected_kind


def test_a_cartridge_keeps_its_own_extension(tmp_path):
    """BizHawk picks the system by database hit first and by extension second (PLAN A15),
    and a cartridge the run made is never in the database: named .gb, a pureRGB cartridge
    ran on the DMG core in mono (seen by the owner). The output takes the source's
    extension -- Red/Blue dumps .gb, pureRGB (and Yellow) .gbc."""
    from server import cartridges
    result = cartridges.provision(str(tmp_path / "pure"), {"a": _pure("red"), "b": _pure("blue")},
                                  companion=True, randomize=None)
    assert [Path(p["output"]).name for p in result["players"].values()] == ["a.gbc", "b.gbc"]
    red = REPO / "patch" / "build" / "gen1_red.gb"
    if red.is_file():
        result = cartridges.provision(str(tmp_path / "vanilla"), {"a": str(red), "b": str(red)},
                                      companion=True, randomize=None)
        assert [Path(p["output"]).name for p in result["players"].values()] == ["a.gb", "b.gb"]


@pytest.mark.parametrize("title", ["red", "blue", "green"])
@pytest.mark.parametrize("companion", [True])   # companion=False is refused now (patch-first, owner 2026-10-02)
@pytest.mark.parametrize("randomize", [False, True])
def test_pure_modes_and_overlay_before_randomizer(tmp_path, monkeypatch, title, companion, randomize):
    from server import cartridges
    from server.adapters.gen1_rom_scan import identify
    from tools.gen1_playthrough import _overlay_admission_row

    source = _pure(title)
    want, _entry = _overlay_admission_row(f"pure{title}")
    def check_inputs(sources):
        for path in sources.values():
            data = Path(path).read_bytes()
            assert identify(data)["kind"] == ("overlay" if companion else "clean")
            if companion:
                assert hashlib.sha1(data).hexdigest() == want
    _fake_pair(monkeypatch, check_inputs)
    monkeypatch.setattr(upr_pipeline, "jar_is_fork", lambda jar: True)
    def no_inject(*args):
        pytest.fail("vanilla injector must never receive a pureRGB cartridge")
    monkeypatch.setattr(inject, "inject", no_inject)
    result = cartridges.provision(str(tmp_path), {"a": source, "b": source}, companion=companion,
                                  randomize={"settings_path": "s.rnqs"} if randomize else None,
                                  jar="fork.jar")
    assert result["family"] == cartridges.FAMILY_PURE
    assert (tmp_path / "rom_contract.json").exists() is randomize
    for player in result["players"].values():
        data = Path(player["output"]).read_bytes()
        assert player["rom_sha1"] == hashlib.sha1(data).hexdigest()
        assert player["fingerprint"] == fingerprint_rom(data)
        if companion:
            assert player["rom_sha1"] == want
        else:
            assert data == Path(source).read_bytes()
    if randomize:
        contract = json.loads((tmp_path / "rom_contract.json").read_text())
        for pid, player in result["players"].items():
            assert contract["players"][pid]["rom_sha1"] == player["rom_sha1"]


@pytest.mark.parametrize("companion", [True])
@pytest.mark.parametrize("randomize", [False, True])
def test_picked_overlay_is_reused_without_repatching(tmp_path, monkeypatch, companion, randomize):
    from server import cartridges

    source = _pure()
    built = cartridges.provision(str(tmp_path / "first"), {"a": source, "b": source},
                                 companion=True, randomize=None)
    sources = {pid: row["output"] for pid, row in built["players"].items()}
    def no_patch(*args):
        pytest.fail("a pinned overlay must not be patched again")
    monkeypatch.setattr(cartridges, "ups_apply", no_patch)
    monkeypatch.setattr(upr_pipeline, "jar_is_fork", lambda jar: True)
    _fake_pair(monkeypatch)
    result = cartridges.provision(str(tmp_path / "second"), sources, companion=companion,
                                  randomize={"settings_path": "s.rnqs"} if randomize else None,
                                  jar="fork.jar")
    assert result["companion"] is companion
    for pid, row in result["players"].items():
        assert Path(row["output"]).read_bytes() == Path(sources[pid]).read_bytes()
        assert row["kind"] == ("rand_companion" if randomize else "companion")


def test_refuses_unpinned_source_before_spending(tmp_path, monkeypatch):
    from server.cartridges import CartridgeError, provision

    sources = _vanilla()
    altered = tmp_path / "modified.gb"
    altered.write_bytes(inject.inject(Path(sources["b"]).read_bytes()))
    sources["b"] = str(altered)
    monkeypatch.setattr(upr_pipeline, "prepare_pair", lambda *args: pytest.fail("spent on refused pair"))
    with pytest.raises(CartridgeError, match="pinned"):
        provision(str(tmp_path / "run"), sources, companion=True,
                  randomize={"settings_path": "s.rnqs"}, jar="fake.jar")
    assert not (tmp_path / "run").exists()


def test_refuses_mixed_families_before_spending(tmp_path, monkeypatch):
    from server.cartridges import CartridgeError, provision

    sources = _vanilla()
    sources["b"] = _pure()
    monkeypatch.setattr(upr_pipeline, "prepare_pair", lambda *args: pytest.fail("spent on mixed pair"))
    with pytest.raises(CartridgeError, match="different families"):
        provision(str(tmp_path / "run"), sources, companion=True,
                  randomize={"settings_path": "s.rnqs"}, jar="fake.jar")
    assert not (tmp_path / "run").exists()


def _overlay_sha1(dirname):
    with open(REPO / "data" / "games" / f"gen2_{dirname}" / "admission.json", encoding="utf-8") as fh:
        return next(a for a in json.load(fh)["artifacts"] if a["kind"] == "overlay")["sha1"]


def test_gen2_companion_follows_admission(tmp_path):
    """Patch-first: with its overlay row activated, every Gen 2 title gets its own SLink-<Title>.ups,
    producing exactly the admitted overlay sha1 (Crystal checked explicitly, OVERLAY_ADMISSION_NEXT)."""
    from server import cartridges

    crystal = REPO / ".cache" / "gen2-build" / "pokecrystal" / "pokecrystal.gbc"
    if not crystal.is_file():
        pytest.skip(f"pinned Gen 2 build absent: {crystal}")
    for sources, names in ((_gen2(), ("gold", "silver")), ({"a": str(crystal), "b": str(crystal)}, ("crystal", "crystal"))):
        result = cartridges.provision(str(tmp_path / names[0]), sources, companion=True, randomize=None)
        assert result["family"] == cartridges.upr_pipeline.FAMILY_GEN2
        for pid, dirname in zip(("a", "b"), names, strict=True):
            player = result["players"][pid]
            assert Path(player["output"]).suffix == ".gbc"
            assert player["kind"] == "companion" and player["rom_sha1"] == _overlay_sha1(dirname)


def test_gen2_rolled_back_row_refuses_the_provision(tmp_path, monkeypatch):
    """Fail closed (review cx-f18ac278 F1, patch-first owner 2026-10-02): COMPANION_TITLES alone never
    composes a Gen 2 overlay the launcher refuses, and the launcher refuses every clean Gen 2 cartridge
    too, so an overlay row that is not activated means NO cartridge is prepared (it used to hand out
    the clean one as picked)."""
    from server import cartridges

    monkeypatch.setattr(cartridges.patcher, "gen2_overlay_admitted", lambda title: False)
    with pytest.raises(cartridges.CartridgeError, match="Gen 2 companion is not admitted yet"):
        cartridges.provision(str(tmp_path), _gen2(), companion=True, randomize=None)
    assert not (tmp_path / "roms").exists(), "a refused provision must not write any cartridge"


def test_gen2_overlay_admitted_reads_the_activated_row(monkeypatch):
    from server import patcher

    row = {"kind": "overlay", "selection": "SELECTED", "status": "ADMITTED", "binding_sha256": "a" * 64,
           "runtime_gate": {"id": "G4", "state": "ADMITTED", "grant_fingerprint": "b" * 64}}
    monkeypatch.setattr(patcher, "_gen2_overlay_row", lambda title: row)
    assert patcher.gen2_overlay_admitted("gold")
    half_written = {**row["runtime_gate"], "grant_fingerprint": "b" * 63}
    for key, value in (("selection", "FUTURE"), ("status", "BUILT"), ("runtime_gate", None),
                       ("binding_sha256", None), ("runtime_gate", half_written)):
        monkeypatch.setattr(patcher, "_gen2_overlay_row", lambda title, k=key, v=value: {**row, k: v})
        assert not patcher.gen2_overlay_admitted("gold"), key


def test_gen2_randomizes_the_companion_overlay(tmp_path, monkeypatch):
    """Gen 2 randomize (owner ruling 2026-10-03, C-5): the UPS overlay is applied FIRST and the
    overlay is what the randomizer reads (pureRGB order; docs/gen2/RANDOMIZER.md proved it safe).
    The handed-out cartridge is the randomizer's output, pinned by rom_contract.json with its seed;
    no Gen 1 fingerprint is taken (Gen 2 has no client-reproducible one yet)."""
    from server import cartridges, upr_pipeline

    sources = _gen2()
    # a Gen 2 pick is refused outright without its companion (patch-first), so activate the overlay row
    monkeypatch.setattr(cartridges.patcher, "gen2_overlay_admitted", lambda title: True)
    monkeypatch.setattr(upr_pipeline, "jar_is_fork", lambda _jar: True)
    monkeypatch.setattr(upr_pipeline, "fingerprint_any", lambda _rom: pytest.fail("Gen 1 fingerprint on Gen 2"))
    seen = {}

    def prepare(jar, settings_path, inputs, out_dir):
        players = {}
        for seed, (pid, path) in enumerate(sorted(inputs.items()), 7):
            data = bytearray(Path(path).read_bytes())
            seen[pid] = hashlib.sha1(data).hexdigest()
            data[0x60000] ^= seed                         # stands in for UPR's tables
            output = Path(out_dir) / f"{pid}_randomized.gbc"
            output.write_bytes(data)
            players[pid] = {"output": str(output), "seed": seed, "source_sha1": seen[pid], "content_hash": "h"}
        return {"upr_version": "4.6.1-slink3", "settings_sha256": "abc", "categories": ["wild"],
                "spec": {}, "summary": "wild encounters random", "players": players}
    monkeypatch.setattr(upr_pipeline, "prepare_pair", prepare)
    result = cartridges.provision(str(tmp_path), sources, companion=True,
                                  randomize={"settings_path": "s.rnqs"}, jar="fake.jar")
    assert result["family"] == upr_pipeline.FAMILY_GEN2
    assert seen == {"a": _overlay_sha1("gold"), "b": _overlay_sha1("silver")}, "randomized the clean dump"
    contract = json.loads((tmp_path / "rom_contract.json").read_text())
    for seed, pid in enumerate("ab", 7):
        player = result["players"][pid]
        final = Path(player["output"]).read_bytes()
        assert final == (tmp_path / "roms" / f"{pid}_randomized.gbc").read_bytes()
        assert player["kind"] == "rand_companion" and player["fingerprint"] == ""
        assert contract["players"][pid] == {"fingerprint": "", "rom_sha1": hashlib.sha1(final).hexdigest(),
                                            "seed": str(seed)}


def test_gen2_randomize_needs_the_fork_jar(tmp_path, monkeypatch):
    """Only the fork's Gen2RomHandler was measured against the overlay (R0): any other jar is refused
    before a byte is written or Java starts."""
    from server import cartridges, upr_pipeline

    sources = _gen2()
    monkeypatch.setattr(cartridges.patcher, "gen2_overlay_admitted", lambda title: True)
    monkeypatch.setattr(upr_pipeline, "jar_is_fork", lambda _jar: False)
    monkeypatch.setattr(upr_pipeline, "prepare_pair", lambda *a: pytest.fail("spent on a stock jar"))
    with pytest.raises(cartridges.CartridgeError, match="UPR fork jar"):
        cartridges.provision(str(tmp_path), sources, companion=True,
                             randomize={"settings_path": "s.rnqs"}, jar="fake.jar")
    assert not (tmp_path / "roms").exists()


def test_yellow_goes_clean_and_its_red_partner_keeps_the_companion(tmp_path, monkeypatch):
    """Per player (review cx-7847d499 A1): Yellow has no companion, so it is handed out as picked,
    but its Red partner is an admitted title and must still get its companion."""
    from server.cartridges import provision

    yellow = REPO / "Pokemon - Yellow Version (USA, Europe).gbc"
    if not yellow.is_file():
        pytest.skip("clean Yellow dump absent")
    sources = _vanilla()
    sources["b"] = str(yellow)
    monkeypatch.setattr(upr_pipeline, "prepare_pair", lambda *args: pytest.fail("no randomizer asked for"))
    result = provision(str(tmp_path / "run"), sources, companion=True, randomize=None)
    assert result["players"]["a"]["kind"] == "companion"
    assert Path(result["players"]["a"]["output"]).read_bytes() != Path(sources["a"]).read_bytes()
    assert result["players"]["b"]["kind"] == "clean"
    assert Path(result["players"]["b"]["output"]).read_bytes() == yellow.read_bytes()
    assert result["companion"] is True


def test_the_companion_decision_is_server_side_per_player():
    """No browser input can grant a title the companion: only COMPANION_TITLES (and, for Gen 2, its
    activated catalog row) can."""
    from server.cartridges import COMPANION_TITLES, companion_admitted

    assert companion_admitted({"variant": "Red"}) and "Red" in COMPANION_TITLES
    for variant in ("Yellow", "", None):
        assert not companion_admitted({"variant": variant}), variant


def test_pure_stock_jar_refuses_before_patching_or_randomizing(tmp_path, monkeypatch):
    from server import cartridges

    source = _pure()
    monkeypatch.setattr(upr_pipeline, "jar_is_fork", lambda jar: False)
    monkeypatch.setattr(upr_pipeline, "prepare_pair", lambda *args: pytest.fail("spent on stock jar"))
    monkeypatch.setattr(cartridges, "ups_apply", lambda *args: pytest.fail("patched before jar check"))
    with pytest.raises(cartridges.CartridgeError) as exc:
        cartridges.provision(str(tmp_path / "run"), {"a": source, "b": source}, companion=True,
                             randomize={"settings_path": "s.rnqs"}, jar="stock.jar")
    assert str(exc.value) == upr_pipeline.PUREGB_RANDOMIZER_REFUSAL
    assert not (tmp_path / "run").exists()


def test_pipeline_refusal_preserves_existing_outputs_and_contract(tmp_path, monkeypatch):
    from server.cartridges import CartridgeError, provision

    sources = _vanilla()
    (tmp_path / "roms").mkdir()
    for name in ("a.gb", "b.gb"):
        (tmp_path / "roms" / name).write_bytes(b"old cartridge")
    (tmp_path / "rom_contract.json").write_text("old contract")
    def refuse(*args):
        raise upr_pipeline.UprPipelineError("a real pipeline refusal")
    monkeypatch.setattr(upr_pipeline, "prepare_pair", refuse)
    with pytest.raises(CartridgeError, match="^a real pipeline refusal$"):
        provision(str(tmp_path), sources, companion=True,
                  randomize={"settings_path": "s.rnqs"}, jar="fake.jar")
    assert (tmp_path / "rom_contract.json").read_text() == "old contract"
    assert all((tmp_path / "roms" / name).read_bytes() == b"old cartridge" for name in ("a.gb", "b.gb"))


@pytest.fixture
def publication_inputs(tmp_path, monkeypatch):
    sources = {p: str(tmp_path / f"source-{p}.gbc") for p in ("a", "b")}
    for player, source in sources.items():
        Path(source).write_bytes(f"new-{player}".encode())
    monkeypatch.setattr(upr_pipeline, "describe_rom", lambda *a, **kw: {
        "clean": True, "variant": "Yellow", "title": "Pokemon Yellow"})
    monkeypatch.setattr(upr_pipeline, "family_of", lambda _: "gen1_rby")
    monkeypatch.setattr(upr_pipeline, "fingerprint_any", lambda rom: hashlib.sha1(rom).hexdigest())
    monkeypatch.setattr(upr_pipeline, "prepare_pair", lambda *a: {
        "upr_version": "test", "settings_sha256": "test", "categories": [],
        "players": {p: {"output": source, "seed": 1} for p, source in sources.items()}})
    return sources


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("randomize", [False, True])
@pytest.mark.parametrize("failure", ["stage", "second", "contract"])
def test_publication_failure_preserves_entire_previous_set(
        tmp_path, monkeypatch, publication_inputs, existing, randomize, failure):
    from server.cartridges import CartridgeError, provision

    run = tmp_path / "run"
    (run / "roms").mkdir(parents=True)
    paths = [run / "roms" / "a.gbc", run / "roms" / "b.gbc", run / "rom_contract.json"]
    previous = {path: f"old-{path.name}".encode() for path in paths} if existing else {}
    for path, content in previous.items():
        path.write_bytes(content)
    write, replace, unlink = Path.write_bytes, Path.replace, Path.unlink

    def write_checked(path, content):
        if failure == "stage" and path.name in ("b.gbc", "new-b.gbc"):
            raise OSError("injected publication failure")
        return write(path, content)

    def replace_checked(path, destination):
        target = paths[1] if failure == "second" else paths[2]
        if failure in ("second", "contract") and destination == target and not path.name.startswith("old-"):
            raise OSError("injected publication failure")
        return replace(path, destination)

    def unlink_checked(path, **kwargs):
        if failure == "contract" and path == paths[2]:
            raise OSError("injected publication failure")
        return unlink(path, **kwargs)

    monkeypatch.setattr(Path, "write_bytes", write_checked)
    monkeypatch.setattr(Path, "replace", replace_checked)
    monkeypatch.setattr(Path, "unlink", unlink_checked)
    with pytest.raises(CartridgeError, match="injected publication failure"):
        provision(str(run), publication_inputs, companion=False,
                  randomize={"settings_path": "test"} if randomize else None, jar="test.jar")
    assert {path: path.read_bytes() for path in paths if path.exists()} == previous
    assert not list(run.glob(".cartridges-*"))


def test_failed_publication_rollback_keeps_recovery_files(tmp_path, monkeypatch, publication_inputs):
    from server.cartridges import CartridgeError, provision

    run = tmp_path / "run"
    (run / "roms").mkdir(parents=True)
    (run / "roms" / "a.gbc").write_bytes(b"previous-a")
    replace = Path.replace

    def fail(path, destination):
        if path.name in ("new-b.gbc", "old-a.gbc"):
            raise OSError("injected failure")
        return replace(path, destination)

    monkeypatch.setattr(Path, "replace", fail)
    with pytest.raises(CartridgeError, match="rollback incomplete, recovery files retained"):
        provision(str(run), publication_inputs, companion=False, randomize=None)
    staging, = run.glob(".cartridges-*")
    assert (staging / "old-a.gbc").read_bytes() == b"previous-a"


@pytest.mark.parametrize("randomize", [False, True])
def test_successful_publication_replaces_pair_and_contract(tmp_path, publication_inputs, randomize):
    from server.cartridges import provision

    run = tmp_path / "run"
    run.mkdir()
    (run / "rom_contract.json").write_text("old contract")
    result = provision(str(run), publication_inputs, companion=False,
                       randomize={"settings_path": "test"} if randomize else None, jar="test.jar")
    for player in ("a", "b"):
        assert Path(result["players"][player]["output"]).read_bytes() == f"new-{player}".encode()
    assert (run / "rom_contract.json").exists() is randomize
    if randomize:
        contract = json.loads((run / "rom_contract.json").read_text())
        assert contract["players"]["a"]["rom_sha1"] == result["players"]["a"]["rom_sha1"]
    assert not list(run.glob(".cartridges-*"))


def test_nonrandomized_reprovision_drops_previous_contract(tmp_path):
    from server.cartridges import provision

    (tmp_path / "rom_contract.json").write_text("old randomization contract")
    provision(str(tmp_path), _vanilla(), companion=True, randomize=None)
    assert not (tmp_path / "rom_contract.json").exists()


def test_overlay_hash_must_match_admission(tmp_path, monkeypatch):
    from server import cartridges

    source = _pure()
    real_lookup = cartridges._overlay_admission_row
    monkeypatch.setattr(cartridges, "_overlay_admission_row",
                        lambda title: ("0" * 40, real_lookup(title)[1]))
    with pytest.raises(cartridges.CartridgeError, match="admitted pureRGB overlay"):
        cartridges.provision(str(tmp_path / "run"), {"a": source, "b": source},
                             companion=True, randomize=None)
    assert not (tmp_path / "run").exists()


def test_missing_jar_and_malformed_settings_refuse(tmp_path, monkeypatch):
    from server.cartridges import CartridgeError, provision

    sources = _vanilla()
    monkeypatch.setattr(upr_pipeline, "find_upr_jar", lambda: None)
    with pytest.raises(CartridgeError, match="jar not found"):
        provision(str(tmp_path), sources, companion=True, randomize={"settings_path": "s.rnqs"})
    with pytest.raises(CartridgeError, match="settings_path"):
        provision(str(tmp_path), sources, companion=True, randomize={})


def test_finds_default_jar(tmp_path, monkeypatch):
    from server.cartridges import provision

    sources = _vanilla()
    _fake_pair(monkeypatch)
    fake = upr_pipeline.prepare_pair
    monkeypatch.setattr(upr_pipeline, "find_upr_jar", lambda: "discovered.jar")
    def check(jar, *args):
        assert jar == "discovered.jar"
        return fake(jar, *args)
    monkeypatch.setattr(upr_pipeline, "prepare_pair", check)
    provision(str(tmp_path), sources, companion=True, randomize={"settings_path": "s.rnqs"})


def test_requires_both_players(tmp_path):
    from server.cartridges import CartridgeError, provision

    with pytest.raises(CartridgeError, match="players a and b"):
        provision(str(tmp_path), {}, companion=False, randomize=None)


def test_missing_source_refuses(tmp_path):
    from server.cartridges import CartridgeError, provision

    with pytest.raises(CartridgeError, match="source ROM not found"):
        provision(str(tmp_path), {"a": "missing.gb", "b": "missing.gb"}, companion=False, randomize=None)


def test_refuses_overwriting_picked_original(tmp_path):
    from server.cartridges import CartridgeError, provision

    sources = _vanilla()
    (tmp_path / "roms").mkdir()
    original = Path(sources["a"]).read_bytes()
    picked = tmp_path / "roms" / "b.gb"
    picked.write_bytes(original)
    sources["a"] = str(picked)
    with pytest.raises(CartridgeError, match="source is a run output"):
        provision(str(tmp_path), sources, companion=True, randomize=None)
    assert picked.read_bytes() == original



def test_refuses_overwriting_another_players_source_with_intermediate(tmp_path, monkeypatch):
    from server.cartridges import CartridgeError, provision

    sources = _vanilla()
    (tmp_path / "roms").mkdir()
    picked = tmp_path / "roms" / "a_randomized.gbc"
    original = Path(sources["b"]).read_bytes()
    picked.write_bytes(original)
    sources["b"] = str(picked)
    _fake_pair(monkeypatch)
    with pytest.raises(CartridgeError, match="source is a run output"):
        provision(str(tmp_path), sources, companion=True,
                  randomize={"settings_path": "s.rnqs"}, jar="fake.jar")
    assert picked.read_bytes() == original


def test_companion_titles_are_exactly_the_patchers_companion_targets_minus_yellow():
    """Patch-first (owner 2026-10-02): the list the Manager and the launcher refuse clean cartridges for
    is the list of titles the patcher can patch. A new patch target must be decided here, in the open;
    Yellow (zero free WRAM) has no target and is never in the list."""
    from server import cartridges, patcher
    title_of = {"rr": "Radical Red", "rb-red": "Red", "rb-blue": "Blue", "pure-red": "PureRed",
                "pure-blue": "PureBlue", "pure-green": "PureGreen", "gen2-crystal": "Crystal",
                "gen2-gold": "Gold", "gen2-silver": "Silver", "firered": "FireRed",
                "leafgreen": "LeafGreen", "emerald": "Emerald",
                "polished-crystal": "Polished Crystal"}
    assert set(patcher.TARGETS) <= set(title_of), "a new patcher target needs a COMPANION_TITLES decision"
    assert set(cartridges.COMPANION_TITLES) == {title_of[slug] for slug in patcher.TARGETS}
    assert "Yellow" not in cartridges.COMPANION_TITLES and len(set(cartridges.COMPANION_TITLES)) == len(
        cartridges.COMPANION_TITLES)


@pytest.mark.parametrize("randomize", [False, True])
def test_a_companion_title_cannot_be_prepared_clean(tmp_path, monkeypatch, randomize):
    """companion=False (or a randomized-clean request) is refused for Red/Blue, before the randomizer
    is spent and before anything is written: the launcher would refuse the cartridge anyway."""
    from server.cartridges import CartridgeError, provision

    monkeypatch.setattr(upr_pipeline, "prepare_pair", lambda *a: pytest.fail("spent on a refused pick"))
    with pytest.raises(CartridgeError, match="Red needs the SLink companion patch"):
        provision(str(tmp_path / "run"), _vanilla(), companion=False,
                  randomize={"settings_path": "s.rnqs"} if randomize else None, jar="fake.jar")
    assert not (tmp_path / "run").exists()


@pytest.mark.parametrize("randomize", [False, True])
def test_a_pure_pick_cannot_be_prepared_clean_and_the_randomized_overlay_still_can(tmp_path, monkeypatch, randomize):
    from server.cartridges import CartridgeError, provision

    source = _pure()
    monkeypatch.setattr(upr_pipeline, "jar_is_fork", lambda jar: True)
    monkeypatch.setattr(upr_pipeline, "prepare_pair", lambda *a: pytest.fail("spent on a refused pick"))
    with pytest.raises(CartridgeError, match="needs the SLink companion patch"):
        provision(str(tmp_path / "run"), {"a": source, "b": source}, companion=False,
                  randomize={"settings_path": "s.rnqs"} if randomize else None, jar="fork.jar")
    assert not (tmp_path / "run").exists()


def test_randomize_then_patch_every_companion_cartridge_carries_the_companion(tmp_path, monkeypatch):
    """Randomized companion titles are randomized and THEN carry the companion: Red/Blue are injected into
    the randomized bytes; pureRGB randomizes the overlay itself. A randomized-clean pair is not offered."""
    from server import cartridges

    sources = _vanilla()
    _fake_pair(monkeypatch)
    result = cartridges.provision(str(tmp_path), sources, companion=True,
                                  randomize={"settings_path": "s.rnqs"}, jar="fake.jar")
    assert {row["kind"] for row in result["players"].values()} == {"rand_companion"}
    for pid, row in result["players"].items():
        final = Path(row["output"]).read_bytes()
        assert inject.describe(final)["already"] is not None, "the randomized cartridge must carry the payload"
        assert final == inject.inject(Path(sources[pid]).read_bytes())


def test_injection_refusal_is_reported_before_final_outputs(tmp_path, monkeypatch):
    from server.cartridges import CartridgeError, provision

    sources = _vanilla()
    _fake_pair(monkeypatch)
    actual_inject = inject.inject
    count = 0
    def fail_second(rom):
        nonlocal count
        count += 1
        if count == 2:
            raise inject.InjectError("second cartridge cannot take the payload")
        return actual_inject(rom)
    monkeypatch.setattr(inject, "inject", fail_second)
    with pytest.raises(CartridgeError, match="second cartridge cannot take the payload"):
        provision(str(tmp_path), sources, companion=True,
                  randomize={"settings_path": "s.rnqs"}, jar="fake.jar")
    assert not (tmp_path / "roms" / "a.gb").exists()
    assert not (tmp_path / "roms" / "b.gb").exists()
    assert not (tmp_path / "rom_contract.json").exists()


# ── Polished Crystal randomize (docs/polished/UPR_HANDLER.md) ──────────────────────────────────
def _polished():
    import os
    path = Path(os.environ.get("SLINK_WORK_ROOT", "F:/slink-work")) / "cache/polished/release/polishedcrystal-3.2.3.gbc"
    if not path.is_file():
        pytest.skip(f"pinned Polished Crystal release absent: {path}")
    return {"a": str(path), "b": str(path)}


def _polished_ready(monkeypatch):
    """What the Polished client card flips: the enable flag, the picker's verdict and companion admission."""
    from server import cartridges
    describe = upr_pipeline.describe_rom
    monkeypatch.setattr(upr_pipeline, "POLISHED_RANDOMIZER_ENABLED", True)
    monkeypatch.setattr(upr_pipeline, "describe_rom", lambda path, jar_fork: {**describe(path, jar_fork), "clean": True})
    monkeypatch.setattr(cartridges, "companion_admitted", lambda info: True)


def test_polished_randomize_stays_refused_while_the_flag_is_off(tmp_path, monkeypatch):
    from server import cartridges
    sources = _polished()
    _polished_ready(monkeypatch)
    monkeypatch.setattr(upr_pipeline, "POLISHED_RANDOMIZER_ENABLED", False)
    monkeypatch.setattr(upr_pipeline, "prepare_pair", lambda *a: pytest.fail("spent on a held Polished randomize"))
    with pytest.raises(cartridges.CartridgeError, match="Polished Crystal randomizing is not enabled"):
        cartridges.provision(str(tmp_path), sources, companion=True, randomize={"settings_path": "s.rnqs"}, jar="fork.jar")
    assert not (tmp_path / "roms").exists()


def test_polished_randomizes_the_companion_overlay(tmp_path, monkeypatch):
    """UPS first (md5 verified against the patch registry), then the overlay is what the randomizer reads;
    the handed-out cartridge is the randomizer output, pinned by rom_contract.json with its seed."""
    from server import cartridges
    sources = _polished()
    _polished_ready(monkeypatch)
    monkeypatch.setattr(upr_pipeline, "jar_is_fork", lambda _jar: True)
    monkeypatch.setattr(upr_pipeline, "fingerprint_any", lambda _rom: pytest.fail("Gen 1 fingerprint on Polished"))
    overlay_sha1 = json.loads((REPO / "data/polished/overlay_provenance.json").read_text())["output"]["sha1"]
    seen = {}

    def prepare(jar, settings_path, inputs, out_dir):
        players = {}
        for seed, (pid, path) in enumerate(sorted(inputs.items()), 7):
            data = bytearray(Path(path).read_bytes())
            seen[pid] = hashlib.sha1(data).hexdigest()
            data[0x306EE] ^= seed                         # a JohtoGrass slot: stands in for the UPR tables
            output = Path(out_dir) / f"{pid}_randomized.gbc"
            output.write_bytes(data)
            players[pid] = {"output": str(output), "seed": seed, "source_sha1": seen[pid], "content_hash": "h"}
        return {"upr_version": "4.6.1-slink3", "settings_sha256": "abc", "categories": ["wild"],
                "spec": {}, "summary": "wild encounters random", "players": players}
    monkeypatch.setattr(upr_pipeline, "prepare_pair", prepare)
    result = cartridges.provision(str(tmp_path), sources, companion=True,
                                  randomize={"settings_path": "s.rnqs"}, jar="fork.jar")
    assert result["family"] == upr_pipeline.FAMILY_POLISHED
    assert seen == {"a": overlay_sha1, "b": overlay_sha1}, "randomized the release instead of the overlay"
    contract = json.loads((tmp_path / "rom_contract.json").read_text())
    for seed, pid in enumerate("ab", 7):
        player = result["players"][pid]
        final = Path(player["output"]).read_bytes()
        assert final == (tmp_path / "roms" / f"{pid}_randomized.gbc").read_bytes()
        assert player["kind"] == "rand_companion" and player["fingerprint"] == ""
        assert contract["players"][pid] == {"fingerprint": "", "rom_sha1": hashlib.sha1(final).hexdigest(),
                                            "seed": str(seed)}


def test_polished_randomize_needs_the_fork_jar(tmp_path, monkeypatch):
    from server import cartridges
    sources = _polished()
    _polished_ready(monkeypatch)
    monkeypatch.setattr(upr_pipeline, "jar_is_fork", lambda _jar: False)
    monkeypatch.setattr(upr_pipeline, "prepare_pair", lambda *a: pytest.fail("spent on a stock jar"))
    with pytest.raises(cartridges.CartridgeError, match="UPR fork jar"):
        cartridges.provision(str(tmp_path), sources, companion=True, randomize={"settings_path": "s.rnqs"}, jar="fake.jar")
    assert not (tmp_path / "roms").exists()
