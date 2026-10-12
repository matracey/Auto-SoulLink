"""Pre-Popen producer observations, not qualification or runtime-branch witnesses."""
import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from tools.polished_live import manager_r1, runtime, trade_duo_live


@pytest.mark.parametrize("dev,cold,gate", [("0", "0", 0), ("1", "1", 7)])
def test_trade_launch_records_prepared_command_and_actual_inputs_before_popen(tmp_path, monkeypatch, dev, cold, gate):
    repo, root, driver_root = (tmp_path / name for name in ("repo", "selected", "drivers"))
    files = {
        repo / "tools/polished_live/trade_duo_live.lua": b"executed driver",
        driver_root / "tools/polished_live/pol_lib.lua": b"selected driver helper",
        root / "lua/slink.lua": b"normal composition entry",
        root / "lua/gen2/entry.lua": b"selected composition",
        root / "lua/gen2/polished_trade.lua": b"selected shipped binder",
        root / "data/games/polished_crystal/profile.json": b"selected profile",
        root / "data/polished/overlay_provenance.json": b"selected provenance",
    }
    for path, data in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    symbols = tmp_path / "selected.sym"
    symbols.write_text("02:4003 SlinkTradeCommitEnabled\n00:c123 Counter\n", encoding="utf-8")
    rom = tmp_path / "actual.gbc"
    rom.write_bytes(bytes(0x8004))
    save = tmp_path / "sram/actual.SaveRAM"
    save.parent.mkdir()
    if cold == "0":
        save.write_bytes(b"actual battery")
    base = tmp_path / "base.ini"
    base.write_bytes(b"base configuration")
    config = replace(runtime.LaunchConfig.from_env({"POL_LANE": str(tmp_path)}),
                     repo=repo, rom=rom, sram=save.parent, save_name=save.name,
                     sym=symbols, symbols=("Counter",), base_config=base,
                     emuhawk="actual-emulator")
    run = tmp_path / "a" / ("g2" if cold == "1" else "g1")
    cfg = tmp_path / "side.ini"
    monkeypatch.setenv("POL_INHERITED", "kept")
    monkeypatch.setenv("UNRELATED_SECRET", "not recorded")
    env = {"SLINK_ROOT": root.as_posix(), "POL_DRIVER_ROOT": driver_root.as_posix(),
           "SLINK_PLAYER": "a", "POL_TRADE_DEV": dev, "POL_COLD": cold}
    launched = []

    def write_config(base_path, target, **kwargs):
        Path(target).write_bytes(b"generated configuration")
        # Evidence must bind the last bytes, not bytes captured before preparation.
        actual = bytearray(rom.read_bytes())
        actual[0x8003] = gate
        rom.write_bytes(actual)
        (root / "lua/gen2/entry.lua").write_bytes(b"last selected composition")

    class Process:
        pid = 123

    def popen(command, **kwargs):
        record = json.loads((run / "launch.json").read_text(encoding="utf-8"))
        assert record["schema"] == "polished-trade-launch-v1"
        assert record["phase"] == "pre_popen"
        assert record["argv"] == command
        assert record["cwd"] == kwargs["cwd"]
        assert command == ["actual-emulator", f"--lua={(repo / 'tools/polished_live/trade_duo_live.lua').as_posix()}",
                           f"--config={cfg.as_posix()}", rom.as_posix()]
        assert record["environment"] == {key: value for key, value in kwargs["env"].items()
                                          if key.startswith(("SLINK_", "POL_"))}
        assert record["environment"]["POL_INHERITED"] == "kept"
        assert "UNRELATED_SECRET" not in record["environment"]
        assert record["launcher"]["selection"] == {"POL_TRADE_DEV": dev, "POL_COLD": cold, "SLINK_PLAYER": "a"}
        assert record["rom_sha1"] == hashlib.sha1(rom.read_bytes()).hexdigest()
        assert record["gate"] == {"symbol": "SlinkTradeCommitEnabled", "symbols_path": symbols.as_posix(),
                                  "bank": 2, "addr": 0x4003, "rom_offset": 0x8003, "value": gate}
        for name, path in {"rom": rom, "configuration": cfg, "symbols": symbols,
                           "executed_lua_driver": repo / "tools/polished_live/trade_duo_live.lua",
                           "normal_lua_slink": root / "lua/slink.lua", "generated_symbols": run / "syms.json"}.items():
            assert record["inputs"][name] == {"path": path.as_posix(), "status": "present",
                                              "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        expected_save = {"path": save.as_posix(), "status": "absent", "sha256": None}
        if cold == "0":
            expected_save.update(status="present", sha256=hashlib.sha256(b"actual battery").hexdigest())
        assert record["inputs"]["saveram"] == expected_save
        sources = {item["path"]: item for item in record["source_inputs"]}
        for path in files:
            assert sources[path.as_posix()]["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
        assert record["source_scope"] == "prelaunch input inventory; not observed Lua loads"
        assert "verdict" not in record
        launched.append(record)
        return Process()

    proc = trade_duo_live.launch_trade_side(config, run, env, config_path=cfg,
                                            popen=popen, write_config=write_config)
    assert proc.pid == 123 and len(launched) == 1
    assert (run / "emuhawk.pid").read_text() == "123"


@pytest.fixture
def r1_images(tmp_path, monkeypatch):
    from zipfile import ZipFile

    from patch.tools.make_ups import ups_create

    repo, out, run = (tmp_path / name for name in ("repo", "evidence", "manager-run"))
    out.mkdir()
    for name in ("server/upr_polished_write_domain.py", "server/upr_pipeline.py"):
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((manager_r1.REPO / name).read_bytes())
    before = bytearray(0x200000)
    before[0x200] = 1
    patch = repo / "patch/dist/SLink-Polished.ups"
    patch.parent.mkdir(parents=True)
    patch.write_bytes(ups_create(bytes(len(before)), bytes(before)))
    prov = repo / "data/polished/overlay_provenance.json"
    prov.parent.mkdir(parents=True)
    prov.write_text(json.dumps({"output": {"sha1": hashlib.sha1(before).hexdigest(),
                                         "size": len(before), "ups": {"file": "patch/dist/SLink-Polished.ups"}}}))
    jar = tmp_path / "actually-selected.jar"
    with ZipFile(jar, "w") as archive:
        archive.writestr("com/dabomstew/pkrandom/config/polished_offsets.ini", b"actual jar domain resource\n")
    monkeypatch.setattr(manager_r1, "REPO", repo)
    roms = run / "roms"
    roms.mkdir(parents=True)
    for player in "ab":
        (roms / f"{player}_companion.gbc").write_bytes(before)
    return out, run, jar, bytes(before)


@pytest.mark.parametrize("offset,allowed", [
    (0x133, True), (0x134, False), (0x14F, False), (0x150, True),
    (0x200, False), (0x201, True), (0x1F8000, False),
    (0x1FBFFF, False), (0x1FC000, True), (0x1FFFFF, True),
])
def test_r1_diff_confinement_retains_authentic_images_and_boundary_regions(r1_images, offset, allowed):
    out, run, jar, before = r1_images
    model = manager_r1._capture_domain_model(out, jar)
    for player, value in (("a", 2), ("b", 3)):
        after = bytearray(before)
        after[offset] = value
        (run / "roms" / f"{player}_randomized.gbc").write_bytes(after)
        (out / f"rom_{player}.gbc").write_bytes(after)
    manager_r1._emit_write_domain(out, run, model)
    record = json.loads((out / "write_domain.json").read_text())
    assert record["write_ops"] is None
    assert record["write_ops_provenance"] == "offline randomizer: no runtime write sequence; criterion is diff confinement"
    table = json.loads((out / model["table"]["path"]).read_text())
    assert model["table"]["sha256"] == hashlib.sha256((out / model["table"]["path"]).read_bytes()).hexdigest()
    for player, value in (("a", 2), ("b", 3)):
        row = record["players"][player]
        images = row["images"]
        assert (out / images["post_overlay_pre_upr"]["path"]).read_bytes() == before
        for phase in ("post_upr", "final_download"):
            data = (out / images[phase]["path"]).read_bytes()
            assert data[offset] == value and len(data) == len(before)
            assert images[phase]["sha256"] == hashlib.sha256(data).hexdigest()
        assert row["out_of_domain"] == ([] if allowed else [offset])
        changed, = row["changed_offsets"]
        assert changed["offset"] == offset and changed["old"] == before[offset] and changed["new"] == value
        if allowed:
            region, = [region for region in table["allowed_regions"] if region["name"] == changed["region"]]
            assert region["start"] <= offset < region["end"]
        else:
            assert changed["region"] is None
    for resource in model["resources"].values():
        assert resource["sha256"] == hashlib.sha256((out / resource["path"]).read_bytes()).hexdigest()


def test_r1_empty_endpoint_diff_does_not_claim_an_empty_runtime_write_sequence(r1_images):
    out, run, jar, before = r1_images
    model = manager_r1._capture_domain_model(out, jar)
    for player in "ab":
        (run / "roms" / f"{player}_randomized.gbc").write_bytes(before)
        (out / f"rom_{player}.gbc").write_bytes(before)
    manager_r1._emit_write_domain(out, run, model)
    record = json.loads((out / "write_domain.json").read_text())
    assert record["write_ops"] is None
    assert all(row["changed_offsets"] == [] and row["out_of_domain"] == [] for row in record["players"].values())


def test_r1_size_change_cannot_be_described_as_a_confined_endpoint_diff(r1_images):
    out, run, jar, before = r1_images
    model = manager_r1._capture_domain_model(out, jar)
    (run / "roms/a_randomized.gbc").write_bytes(before + b"\x01")
    (out / "rom_a.gbc").write_bytes(before + b"\x01")
    with pytest.raises(ValueError, match="size changed"):
        manager_r1._emit_write_domain(out, run, model)


@pytest.mark.parametrize("mode,present,trusted,accepted", [
    ("jar", True, True, True), ("oldjar", False, True, True),
    ("jar", False, True, False), ("oldjar", True, True, False),
    ("jar", True, False, False), ("oldjar", False, False, False),
])
def test_r1_jar_controls_fail_closed_on_untrusted_or_wrong_capability(tmp_path, monkeypatch, mode, present, trusted, accepted):
    from zipfile import ZipFile

    jar = tmp_path / "selected.jar"
    with ZipFile(jar, "w") as archive:
        archive.writestr("com/dabomstew/pkrandom/config/" + ("polished_offsets.ini" if present else "other.ini"), b"offsets")
    digest = hashlib.sha256(jar.read_bytes()).hexdigest()
    repo = tmp_path / "repo"
    pin = repo / "data/upr_jars.json"
    pin.parent.mkdir(parents=True)
    pin.write_text(json.dumps({"fixture": digest if trusted else "0" * 64}))
    monkeypatch.setattr(manager_r1, "REPO", repo)
    if accepted:
        observation = manager_r1._check_jar(jar, mode)
        assert observation["sha256"] == digest and observation["polished_offsets_present"] is present
        assert observation["allowlist_sha256"] == hashlib.sha256(pin.read_bytes()).hexdigest()
    else:
        with pytest.raises(ValueError, match="allowlist|polished_offsets.ini"):
            manager_r1._check_jar(jar, mode)
