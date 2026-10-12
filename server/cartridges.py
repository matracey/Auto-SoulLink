"""Provision a run's cartridges, preserving each family's patch/randomizer order.

Game identity and patch facts belong to the scanner, patch registry and overlay
admission tooling. This module owns only their ordering and the final-file contract.
"""
from __future__ import annotations

import hashlib
import json
import logging
import shutil
import tempfile
from pathlib import Path

from patch.gen1.tools import inject
from patch.tools.make_ups import ups_apply
from server import patcher, upr_pipeline
from server.adapters.gen1_rom_scan import RomScanError, identify
from server.upr_settings import FAMILY_EMERALD, FAMILY_PURE, FAMILY_VANILLA, GEN3_FAMILIES
from tools.gen1_playthrough import REPO, _overlay_admission_row

log = logging.getLogger(__name__)


class CartridgeError(Exception):
    """The selected cartridges could not be provisioned; the message is the refusal."""


def _publish(directory: Path, changes: dict[Path, bytes | None]) -> None:
    """Stage the whole publication and roll back completed replacements on I/O failure."""
    staging = Path(tempfile.mkdtemp(prefix=".cartridges-", dir=directory))
    backups, published = {}, []
    retain = False
    try:
        for destination, content in changes.items():
            backup = staging / f"old-{destination.name}"
            if destination.exists() or destination.is_symlink():
                shutil.copy2(destination, backup, follow_symlinks=False)
                backups[destination] = backup
            else:
                backups[destination] = None
            if content is not None:
                (staging / f"new-{destination.name}").write_bytes(content)
        try:
            for destination, content in changes.items():
                if content is None:
                    destination.unlink(missing_ok=True)
                else:
                    (staging / f"new-{destination.name}").replace(destination)
                published.append(destination)
        except OSError as exc:
            for destination in reversed(published):
                try:
                    backup = backups[destination]
                    if backup is None:
                        destination.unlink(missing_ok=True)
                    else:
                        backup.replace(destination)
                except OSError:
                    retain = True
                    log.exception("Could not restore cartridge publication file %s", destination)
            if retain:
                raise CartridgeError(
                    f"{exc}; rollback incomplete, recovery files retained in {staging}") from exc
            raise
    finally:
        if not retain:
            try:
                shutil.rmtree(staging)
            except OSError:
                log.warning("Could not remove cartridge staging directory %s", staging, exc_info=True)


# Patch-first (owner 2026-10-01, REQUIRED 2026-10-02): the titles whose SLink companion the
# launcher and the server REQUIRE. Every cartridge of these titles gets its companion, decided
# here per player, never by the browser, and a pick that cannot get it is refused, not handed out
# clean. Yellow has no free WRAM for the mailbox, so it is handed out as picked. Radical Red is
# patched through /patcher (describe_rom does not identify it, so the Manager never sees a pick
# of it); it is listed so the list is exactly the patcher's targets, test_cartridges.py pins that.
COMPANION_TITLES = ("Red", "Blue", "PureRed", "PureBlue", "PureGreen",
                    "Crystal", "Gold", "Silver", "FireRed", "LeafGreen", "Emerald", "Radical Red",
                    "Polished Crystal")
_GEN2_TITLES = ("Crystal", "Gold", "Silver")   # the title the launcher and the adapter refuse clean for as well


def companion_admitted(info: dict) -> bool:
    """Does this pick's title get the companion? describe_rom's variant, e.g. 'Red'. A Gen 2 title
    also needs its activated catalog row."""
    variant = info.get("variant")
    if variant == upr_pipeline.POLISHED_VARIANT:
        return True    # the patcher target (SLink-Polished.ups) is the overlay the randomizer reads
    if variant in ("Crystal", "Gold", "Silver"):
        return variant in COMPANION_TITLES and patcher.gen2_overlay_admitted(variant.lower())
    return variant in COMPANION_TITLES


def _vanilla_target(data: bytes) -> dict:
    md5 = hashlib.md5(data).hexdigest()
    target = next((t for t in patcher.targets().values() if t["base_md5"] == md5), None)   # live: a stamp may have run
    if target is None:
        # patcher.TARGETS deliberately excludes Yellow: it has zero free WRAM.
        raise CartridgeError(
            "this cartridge's companion is not in the patch registry; restore the published "
            "companion patch")
    return target


def _companion(data: bytes, family: str, info: dict) -> bytes:
    if info.get("kind") == "overlay":      # a pinned companion overlay picked as itself (pureRGB, Polished)
        return data
    if family == FAMILY_PURE:
        # Reuse the playthrough tool's admission lookup and its clean + UPS proof,
        # but apply to the user's picked bytes without requiring a separate ROM cache.
        want, entry = _overlay_admission_row(identify(data)["variant"])
        out = ups_apply(data, (Path(REPO) / entry["ups"]).read_bytes())
        if hashlib.sha1(out).hexdigest() != want:
            raise CartridgeError("companion UPS did not produce the admitted pureRGB overlay; "
                                 "restore the patch matching admission_overlay.json")
        return out
    target = _vanilla_target(data)
    out = ups_apply(data, Path(patcher.patch_path(target["slug"])).read_bytes())
    if hashlib.md5(out).hexdigest() != target["patched_md5"]:
        raise CartridgeError("companion UPS does not match the patch registry; restore the "
                             "published companion patch")
    return out


def provision(run_dir: str, sources: dict[str, str], *, companion: bool,
              randomize: dict | None, jar: str = "") -> dict:
    """Write a.<ext>/b.<ext> under run_dir/roms from two pinned, same-family sources.

    The extension is the SOURCE's: BizHawk picks the system by database hit first and by
    extension second (PLAN A15), and a randomized or overlay ROM is never in the database,
    so a pure cartridge named .gb would run on the DMG core in mono. Red/Blue dumps are .gb
    (the DMG core, where the client is proven), Yellow and pureRGB are .gbc.

    Vanilla randomizes clean bytes before structural injection; pureRGB, Gen 2 and Polished Crystal
    randomize the admitted overlay after UPS application. Only randomized runs get a contract,
    and that contract fingerprints and hashes the final handed-out cartridges.
    ``randomize`` is None or {"settings_path": <Manager-written .rnqs path>}.
    """
    try:
        return _provision(run_dir, sources, companion=companion, randomize=randomize, jar=jar)
    except (upr_pipeline.UprPipelineError, RomScanError, inject.InjectError,
            OSError, ValueError) as exc:
        raise CartridgeError(str(exc)) from exc


def _provision(run_dir, sources, *, companion, randomize, jar):
    if set(sources) != {"a", "b"}:
        raise CartridgeError("choose a cartridge for each of players a and b")
    # Pinning is independent of the jar: pureRGB needs no randomizer for a copy/UPS.
    infos = {pid: upr_pipeline.describe_rom(path, jar_fork=True)
             for pid, path in sources.items()}
    for pid, info in infos.items():
        if not info["clean"]:
            raise CartridgeError(f"player {pid}: choose a pinned cartridge; "
                                 f"{info['title'] or 'source ROM not found'}")
    family = upr_pipeline.family_of(sources)
    data = {pid: Path(path).read_bytes() for pid, path in sources.items()}
    if family == upr_pipeline.FAMILY_GEN3_EXP and randomize is not None:
        raise CartridgeError(upr_pipeline.EXPANSION_REFUSAL)
    # Per player: a pick whose title has no admitted companion (Yellow) is handed out as picked;
    # its partner still gets its own companion.
    want = {pid: bool(companion) and companion_admitted(infos[pid]) for pid in sources}
    # Patch-first (owner 2026-10-02): the launcher and the server refuse a clean cartridge of a
    # companion title, so one that cannot be given its companion is refused here, never handed out.
    for pid, info in infos.items():
        variant = info.get("variant")
        if variant in COMPANION_TITLES and not want[pid]:
            if variant in (*_GEN2_TITLES, upr_pipeline.POLISHED_VARIANT) and companion:
                # an unactivated overlay row: the Gen 2 launcher admits no clean cartridge either
                raise CartridgeError(
                    f"player {pid}: the Gen 2 companion is not admitted yet for {variant}; "
                    "a clean cartridge is refused by the launcher, so none is prepared")
            raise CartridgeError(
                f"player {pid}: {variant} needs the SLink companion patch, so a clean cartridge "
                "is not prepared (companion cannot be turned off)")
    if family == FAMILY_VANILLA:
        for pid, rom in data.items():
            if want[pid]:
                _vanilla_target(rom)  # refuse before either randomizer starts
    if randomize is not None and family == upr_pipeline.FAMILY_POLISHED and not upr_pipeline.POLISHED_RANDOMIZER_ENABLED:
        raise CartridgeError(upr_pipeline.POLISHED_RANDOMIZER_REFUSAL)
    if randomize is not None:
        if not isinstance(randomize, dict) or not randomize.get("settings_path"):
            raise CartridgeError("randomize needs settings_path pointing to a .rnqs file")
        jar = jar or upr_pipeline.find_upr_jar()
        if not jar:
            raise CartridgeError("UPR jar not found; choose PokeRandoZX.jar or set SLINK_UPR_JAR")
        if not upr_pipeline.jar_is_trusted(jar):   # before any patching or Java
            raise CartridgeError(upr_pipeline.untrusted_jar_message(jar))
        if family == FAMILY_PURE and not upr_pipeline.jar_is_fork(jar):
            raise CartridgeError(upr_pipeline.PUREGB_RANDOMIZER_REFUSAL)
        if family in GEN3_FAMILIES and not upr_pipeline.jar_is_fork(jar):
            raise CartridgeError(upr_pipeline.EMERALD_RANDOMIZER_REFUSAL if family == FAMILY_EMERALD
                                 else upr_pipeline.FRLG_RANDOMIZER_REFUSAL)
        if family == upr_pipeline.FAMILY_GEN2 and not upr_pipeline.jar_is_fork(jar):
            raise CartridgeError(upr_pipeline.GEN2_RANDOMIZER_REFUSAL)
        if family == upr_pipeline.FAMILY_POLISHED and not upr_pipeline.jar_is_fork(jar):
            raise CartridgeError(upr_pipeline.POLISHED_JAR_REFUSAL)
    # overlay first, then randomize the overlay (pureRGB; Gen 2: docs/gen2/RANDOMIZER.md proved the order safe;
    # Polished: the UPR handler writes in place and the write-domain audit re-proves it never touches the
    # overlay's spans, bank $7E or the header)
    overlay_first = family in (FAMILY_PURE, upr_pipeline.FAMILY_GEN2, upr_pipeline.FAMILY_POLISHED)
    if overlay_first or randomize is None:
        data = {pid: _companion(rom, family, infos[pid]) if want[pid] else rom
                for pid, rom in data.items()}

    directory = Path(run_dir).resolve()
    roms = directory / "roms"
    outputs = {pid: roms / f"{pid}{Path(path).suffix.lower() or '.gb'}" for pid, path in sources.items()}
    # Never replace the picked original, including through a symlink or hard link.
    destinations = list(outputs.values())
    if randomize is not None:
        ext = ".gba" if family in GEN3_FAMILIES else ".gbc"
        destinations.extend(roms / f"{pid}_randomized{ext}" for pid in sources)
        if overlay_first:
            destinations.extend(roms / f"{pid}_companion.gbc" for pid in sources if want[pid])
    for output in destinations:
        for source in sources.values():
            if output.resolve() == Path(source).resolve() or (
                    output.exists() and output.samefile(source)):
                raise CartridgeError("source is a run output; choose the original cartridge elsewhere")
    roms.mkdir(parents=True, exist_ok=True)
    randomized = None
    if randomize is not None:
        inputs = dict(sources)
        if overlay_first:
            for pid, rom in data.items():
                if want[pid]:
                    staged = roms / f"{pid}_companion.gbc"
                    staged.write_bytes(rom)
                    inputs[pid] = str(staged)
        randomized = upr_pipeline.prepare_pair(jar, randomize["settings_path"], inputs, str(roms))
        data = {pid: Path(row["output"]).read_bytes()
                for pid, row in randomized["players"].items()}
        if family == FAMILY_VANILLA:
            data = {pid: inject.inject(rom) if want[pid] else rom for pid, rom in data.items()}
        if family in GEN3_FAMILIES:
            from tools.gen3_companions import overlay_randomized
            data = {pid: overlay_randomized(upr_pipeline.gen3_title(rom), Path(sources[pid]).read_bytes(), rom)
                    if want[pid] else rom for pid, rom in data.items()}

    players = {}
    for pid, rom in data.items():
        has_companion = want[pid] or infos[pid].get("kind") == "overlay"
        kind = "rand_companion" if has_companion else "rand"
        if randomize is None:
            kind = "companion" if has_companion else "clean"
        # fingerprint_any: Gen 3 cartridges use the Gen 3 fingerprint, Gen 1 the wild/fishing
        # scanner; Gen 2 and Polished have no client-reproducible fingerprint yet (the adapter card) and the
        # Emerald Expansion never randomizes, so none of them has one to cross-check at hello.
        fingerprint = "" if family in (upr_pipeline.FAMILY_GEN2, upr_pipeline.FAMILY_POLISHED,
                                       upr_pipeline.FAMILY_GEN3_EXP) else upr_pipeline.fingerprint_any(rom)
        players[pid] = {"source": sources[pid], "source_title": infos[pid]["title"],
                        "output": str(outputs[pid]), "rom_sha1": hashlib.sha1(rom).hexdigest(),
                        "fingerprint": fingerprint, "kind": kind}
    changes: dict[Path, bytes | None] = {outputs[pid]: rom for pid, rom in data.items()}
    contract_path = directory / "rom_contract.json"
    if randomized is not None:
        contract = {k: randomized[k] for k in ("upr_version", "settings_sha256", "categories")}
        contract["players"] = {
            pid: {"fingerprint": player["fingerprint"], "rom_sha1": player["rom_sha1"],
                  "seed": str(randomized["players"][pid]["seed"])}
            for pid, player in players.items()}
        changes[contract_path] = (json.dumps(contract, indent=2) + "\n").encode("utf-8")
        randomized = {**randomized, "players": {
            pid: {k: v for k, v in row.items() if k != "output"}
            for pid, row in randomized["players"].items()}}
    else:
        # Re-provisioning without randomization must not retain an earlier admission pin.
        changes[contract_path] = None
    _publish(directory, changes)
    return {"family": family, "companion": any(want.values()), "randomizer": randomized, "players": players}
