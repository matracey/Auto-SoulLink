#!/usr/bin/env python3
"""Stamp selected companion families and certify explicitly declared as-built companions.

Every companion prints "SoulLink <version>" on its main menu from a fixed-width field (patch/tools/rom_identity.py), so a stamp
changes only that field (and the cartridge checksums). The owner's ruling (2026-10-02, "Version-masked identity"): qualification
evidence and signed fingerprints are keyed on the CANONICAL identity (field and checksums masked), exact hashes name the cartridge
on disk and are regenerated here. This tool rebuilds each family with --version, proves the result is the same build up to the
version (canonical identities unchanged, else it stops: that is a real code change and needs qualification, not a stamp), and
refreshes the exact pins, UPS files, admission rows, jar entries and md5 tables. It ends by writing patch/dist/companion_version.json
(version + the sha256 of every shipped companion file) which tools/make_release.py checks before it packages anything.

Owner ruling 2026-10-09: the RC stamps rb/pure/gen3; vanilla Gen 2 and Polished ship as built, with their UPS bytes pinned to
published provenance. --as-built declares this exception per family; it is not an unstamped packaging bypass.

    python tools/stamp_release.py --version v0.3.0 --plan        # print the steps, run nothing
    python tools/stamp_release.py --version v0.3.0               # stamp every family
    python tools/stamp_release.py --version dev --only rb,pure   # restore the committed 'dev' builds of some families
    python tools/stamp_release.py --version v0.4.0 --only rb,pure,gen3 --as-built gen2,polished

Needs the clean ROMs and toolchains the individual builders need (see patch/README.md). Run on a clean tree; the result is the
release commit's artifact set (commit it, then tag).
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime
import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "patch" / "tools"))
import rom_identity  # noqa: E402

PY = sys.executable
DIST = ROOT / "patch" / "dist"
PINS = DIST / "companion_pins.json"
GEN3_MANIFEST = DIST / "gen3_companions.json"
PURE_PROVENANCE = ROOT / "data" / "purergb" / "overlay_provenance.json"
GEN2_PROVENANCE = ROOT / "data" / "gen2" / "overlay_provenance.json"
VERSION_FILE = DIST / "companion_version.json"
SCHEMA = "slink-companion-version-v1"
SCHEMA_V2 = "slink-companion-version-v2"
FAMILIES = ("rb", "pure", "gen2", "gen3")
AS_BUILT = {
    "gen2": ("data/gen2/overlay_provenance.json", "gen2-overlay-provenance-v1",
             ("SLink-Crystal.ups", "SLink-Gold.ups", "SLink-Silver.ups")),
    "polished": ("data/polished/overlay_provenance.json", "polished-overlay-provenance-v1",
                 ("SLink-Polished.ups",)),
}
VERSION_RE = re.compile(r"dev|v\d+\.\d+\.\d+(?:-dev)?")
SHIPPED = ("SLink-RR.ups", "SLink-RB-Red.ups", "SLink-RB-Blue.ups", "SLink-PureRed.ups", "SLink-PureBlue.ups", "SLink-PureGreen.ups",
           "SLink-Crystal.ups", "SLink-Gold.ups", "SLink-Silver.ups", "SLink-FireRed.ups", "SLink-LeafGreen.ups", "SLink-Emerald.ups",
           "gen3_companions.json", "companion_pins.json")
README_TABLES = ("patch/README.md", "patch/gen1/README.md")
GEN3_ROMS = {"firered": "Pokemon - FireRed Version (USA).gba", "leafgreen": "Pokemon - LeafGreen Version (USA).gba",
             "emerald": "Pokemon - Emerald Version (USA, Europe).gba", "radical_red": "Pokemon - Radical Red.gba"}
RB_DUMPS = {"red": ("Pokemon - Red Version (USA, Europe) (SGB Enhanced).gb", "SLink-RB-Red"),
            "blue": ("Pokemon - Blue Version (USA, Europe) (SGB Enhanced).gb", "SLink-RB-Blue")}


@dataclasses.dataclass(frozen=True)
class Step:
    family: str
    name: str
    argv: tuple[str, ...]
    env: tuple[tuple[str, str], ...] = ()

    def shown(self) -> str:
        return " ".join(f'"{a}"' if " " in a else a for a in self.argv)


def check_version(version: str) -> str:
    if not VERSION_RE.fullmatch(version) or len(version) > rom_identity.VERSION_MAX:
        raise SystemExit(f"--version must be dev or vX.Y.Z[-dev] and at most {rom_identity.VERSION_MAX} characters, got {version!r}")
    return version


def rom_dir_for(name: str, rom_dirs: list[pathlib.Path]) -> pathlib.Path:
    if not rom_dirs:                       # --plan: print the commands, find nothing
        return pathlib.Path("<rom-dir>")
    for d in rom_dirs:
        if (d / name).is_file():
            return d
    raise SystemExit(f"clean ROM not found: {name} (looked in {[str(d) for d in rom_dirs]}; pass --rom-dir)")


def gen2_overlays_admitted() -> bool:
    """Any ADMITTED overlay forbids stamping until a separate grant workflow is supplied."""
    rows = []
    for title in ("crystal", "gold", "silver"):
        doc = _json(ROOT / "data" / "games" / f"gen2_{title}" / "admission.json")
        rows += [r for r in doc.get("artifacts", []) if r.get("kind") == "overlay"]
    return any(r.get("status") == "ADMITTED" for r in rows)


def plan(version: str, families: tuple[str, ...], rom_dirs: list[pathlib.Path], *, promote: bool | None = None,
         gen2_args: tuple[str, ...] = (), jar: bool = True) -> list[Step]:
    """The ordered steps. Pure: builds argv lists only, so the plan is testable without a toolchain."""
    if "gen2" in families and gen2_overlays_admitted():
        raise SystemExit("Gen 2 overlays are ADMITTED: stamping needs a separate grant workflow; use --as-built gen2")
    steps: list[Step] = []
    if "rb" in families:
        steps.append(Step("rb", "build Red/Blue", (PY, "patch/gen1/tools/build.py", "--version", version)))
        for key, (dump, stem) in RB_DUMPS.items():
            steps.append(Step("rb", f"UPS {stem}", (PY, "patch/tools/make_ups.py", "create", str(rom_dir_for(dump, rom_dirs) / dump),
                                                   f"patch/gen1/build/slink_{key}.gb", f"patch/dist/{stem}")))
        steps.append(Step("rb", "pins (md5, canonical)", (PY, "tools/gen_companion_pins.py")))
    if "pure" in families:
        steps.append(Step("pure", "build pureRGB overlay", (PY, "tools/build_purergb_overlay.py", "--version", version)))
        steps += [Step("pure", "profile", (PY, "tools/gen_gen1_profile.py", "--foundation", "purergb", "--kind", "overlay")),
                  Step("pure", "engine signals", (PY, "tools/gen_gen1_engine_signals.py", "--kind", "overlay")),
                  Step("pure", "admission", (PY, "tools/gen_gen1_admission_profiles.py", "--kind", "overlay")),
                  Step("pure", "UPR entries", (PY, "tools/gen_upr_gen1_ini.py"))]
        if jar:
            steps.append(Step("pure", "randomizer jar entries", (PY, "tools/upr_resource_update.py", "--install", "--label", f"stamp {version}")))
            # the Gen 3 write-domain models are pinned to the jar's sha256 (server/upr_gen3_write_domain.py refuses
            # any other jar), so a new jar needs them re-pinned or Manager randomization of FR/LG/Emerald stops
            for title in ("frlg", "emerald"):
                steps.append(Step("pure", f"re-pin Gen 3 write domains ({title})",
                                  (PY, "-m", "server.upr_gen3_write_domain", "--write", "--title", title)))
    if "gen2" in families:
        steps.append(Step("gen2", "build Gen 2 overlays", (PY, "tools/build_gen2_companion.py", "--version", version, *gen2_args)))
        prov = ("--provenance", "data/gen2/build_provenance.json", "--overlay-provenance", "data/gen2/overlay_provenance.json")
        steps.append(Step("gen2", "admission rows", (PY, "tools/gen_gen2_admission.py", *prov)))
        if promote or (promote is None and gen2_overlays_admitted()):
            steps.append(Step("gen2", "re-issue the G4 grant for the new exact bytes (--promote-overlays)",
                              (PY, "tools/gen_gen2_admission.py", *prov, "--promote-overlays")))
    if "gen3" in families:
        for target in ("firered", "leafgreen", "emerald", "radical_red"):
            rom = GEN3_ROMS[target]
            steps.append(Step("gen3", f"build {target}", (PY, "patch/tools/build.py", "--target", target, "--rom",
                                                         str(rom_dir_for(rom, rom_dirs) / rom), "--version", version)))
        steps += [Step("gen3", "engine signals", (PY, "tools/gen_gen3_engine_signals.py")),
                  Step("gen3", "profile", (PY, "tools/gen_gen3_profile.py")),
                  Step("gen3", "write checkpoint", (PY, "tools/gen_gen3_write_checkpoint.py"))]
        for target in ("firered", "leafgreen"):
            rom = GEN3_ROMS[target]
            steps.append(Step("gen3", f"T5 candidate {target}", (PY, "patch/tools/build.py", "--target", target, "--trade-candidate",
                                                                "--rom", str(rom_dir_for(rom, rom_dirs) / rom))))
    return steps


def _json(path: pathlib.Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def identities() -> dict[str, str]:
    """{record name: canonical identity} of everything currently published. A stamp must not change any of it."""
    out: dict[str, str] = {}
    for slug, pin in (_json(PINS).get("pins") or {}).items():
        if pin.get("canonical_sha1"):
            out[f"companion_pins:{slug}"] = pin["canonical_sha1"]
    for key, row in (_json(PURE_PROVENANCE).get("outputs") or {}).items():
        if row.get("canonical_sha1"):
            out[f"pure:{key}"] = row["canonical_sha1"]
    for key, row in (_json(GEN2_PROVENANCE).get("outputs") or {}).items():
        if row.get("canonical_sha1"):
            out[f"gen2:{key}"] = row["canonical_sha1"]
    for title, row in (_json(GEN3_MANIFEST).get("titles") or {}).items():
        for field in ("canonical_sha1", "canonical_payload_sha256"):
            if row.get(field):
                out[f"gen3:{title}:{field}"] = row[field]
    return out


def identity_drift(before: dict[str, str], after: dict[str, str], families: tuple[str, ...]) -> list[str]:
    """Added, changed or removed canonical identities for the families that were stamped."""
    prefixes = {"rb": ("companion_pins:rb-",), "pure": ("pure:",), "gen2": ("gen2:",), "gen3": ("gen3:", "companion_pins:rr")}
    mine = tuple(p for f in families for p in prefixes[f])
    return sorted(f"{k}: {before.get(k, 'missing')[:12]} -> {after.get(k, 'missing')[:12]}"
                  for k in before.keys() | after.keys()
                  if k.startswith(mine) and after.get(k) != before.get(k))


def update_md5_tables(old: dict[str, str], new: dict[str, str]) -> list[str]:
    """Replace each companion's previous patched md5 by the new one in the docs that carry it (patch/README.md tables)."""
    touched = []
    for rel in README_TABLES:
        path = ROOT / rel
        text = path.read_text(encoding="utf-8")
        out = text
        for slug, md5 in new.items():
            if old.get(slug) and old[slug] != md5:
                out = out.replace(old[slug], md5)
        if out != text:
            path.write_text(out, encoding="utf-8", newline="\n")
            touched.append(rel)
    return touched


def pinned_md5s() -> dict[str, str]:
    return {slug: pin["patched_md5"] for slug, pin in (_json(PINS).get("pins") or {}).items() if pin.get("patched_md5")}


def as_built_record(family: str, root: pathlib.Path, dist: pathlib.Path) -> dict:
    """Bind fixed shipped paths to the unchanged, published provenance and UPS bytes."""
    rel, schema, names = AS_BUILT[family]
    raw = (root / rel).read_bytes()
    provenance = json.loads(raw)
    if not isinstance(provenance, dict) or provenance.get("schema") != schema:
        raise ValueError(f"{rel}: wrong provenance schema")
    if family == "gen2":
        outputs = provenance.get("outputs")
        if not isinstance(outputs, dict):
            raise ValueError(f"{rel}: missing outputs")
        rows = list(outputs.values())
    else:
        rows = [provenance.get("output")]
    pins = {}
    for row in rows:
        ups = row.get("ups") if isinstance(row, dict) else None
        if not isinstance(ups, dict):
            raise ValueError(f"{rel}: missing UPS record")
        name = next((n for n in names if ups.get("file") == f"patch/dist/{n}"), None)
        digest = ups.get("sha256")
        if name is None or name in pins or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError(f"{rel}: invalid, duplicate or unexpected UPS pin")
        if hashlib.sha256((dist / name).read_bytes()).hexdigest() != digest:
            raise ValueError(f"{name}: as-built bytes differ from {rel}")
        pins[name] = digest
    if set(pins) != set(names):
        raise ValueError(f"{rel}: missing shipped UPS pins")
    return {"mode": "as-built", "provenance": rel,
            "provenance_sha256": hashlib.sha256(raw).hexdigest(), "files": pins}


def certificate_errors(version: str, root: pathlib.Path, dist: pathlib.Path, *,
                       polished: bool = False, doc: dict | None = None) -> list[str]:
    """One fail-closed reader for legacy stamped and provenance-bound as-built certificates."""
    record = dist / "companion_version.json"
    want = "dev" if version == "dev" else "v" + version.lstrip("v")
    hint = f"run: python tools/stamp_release.py --version {want}"
    if doc is None:
        try:
            doc = json.loads(record.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            return [f"{record.name} is missing or unreadable: {exc} ({hint})"]
    if not isinstance(doc, dict):
        return [f"{record.name}: certificate must be an object"]
    schema = doc.get("schema")
    if schema not in (SCHEMA, SCHEMA_V2):
        return [f"{record.name}: unknown schema {schema!r}"]
    errors = []
    if doc.get("version") != want:
        errors.append(f"companions are stamped {doc.get('version')!r}, the release is {want!r} ({hint})")
    families = doc.get("families")
    if not isinstance(families, dict):
        errors.append(f"{record.name}: missing family policies")
        families = {}
    required = {*FAMILIES, *(("polished",) if polished else ())}
    for family in sorted(required - families.keys()):
        errors.append(f"{family}: missing stamped or as-built family policy")
    for family, entry in families.items():
        if family not in (*FAMILIES, "polished"):
            errors.append(f"unknown companion family {family!r}")
        elif schema == SCHEMA:
            if entry != want:
                errors.append(f"{family}: stamped {entry!r}, expected {want!r}")
        elif isinstance(entry, dict) and entry.get("mode") == "stamped":
            if set(entry) != {"mode", "version"} or entry.get("version") != want:
                errors.append(f"{family}: stamped family must equal {want!r}")
        elif isinstance(entry, dict) and entry.get("mode") == "as-built" and family in AS_BUILT:
            try:
                expected = as_built_record(family, root, dist)
                if entry != expected:
                    errors.append(f"{family}: as-built provenance binding differs from the certificate")
            except (OSError, ValueError) as exc:
                errors.append(f"{family}: as-built provenance refused: {exc}")
        else:
            errors.append(f"{family}: invalid stamped/as-built policy")
    files = doc.get("files")
    if not isinstance(files, dict):
        return [*errors, f"{record.name}: missing file pins"]
    names = (*SHIPPED, *(("SLink-Polished.ups",) if polished or "polished" in families else ()))
    for name in names:
        path = dist / name
        if name not in files:
            errors.append(f"{name} is not covered by {record.name}")
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            errors.append(f"{name} is missing or unreadable in {dist}")
            continue
        if name in files and files[name] != digest:
            errors.append(f"{name} changed after it was stamped ({hint})")
    return errors


def check_scope(families: tuple[str, ...], as_built: tuple[str, ...]) -> None:
    if not families or any(f not in FAMILIES for f in families) or len(set(families)) != len(families):
        raise SystemExit(f"--only must name distinct families from {FAMILIES}")
    if any(f not in AS_BUILT for f in as_built) or len(set(as_built)) != len(as_built):
        raise SystemExit(f"--as-built must name distinct families from {tuple(AS_BUILT)}")
    if set(families) & set(as_built):
        raise SystemExit("a family cannot be both stamped and as-built")
    if as_built and not set(FAMILIES) <= set(families) | set(as_built):
        raise SystemExit("an as-built release certificate must declare every companion family")


def check_previous_policy(as_built: tuple[str, ...]) -> dict:
    previous = _json(VERSION_FILE)
    if previous.get("schema") == SCHEMA_V2 and not as_built:
        raise SystemExit("a v2 certificate requires explicit --as-built scope; refusing before any rebuild")
    return previous


def write_version_file(version: str, families: tuple[str, ...], *, as_built: tuple[str, ...] = ()) -> dict:
    check_scope(families, as_built)
    previous = check_previous_policy(as_built)
    if as_built:
        entries = {f: {"mode": "stamped", "version": version} for f in families}
        try:
            entries.update({f: as_built_record(f, ROOT, DIST) for f in as_built})
        except (OSError, ValueError, KeyError) as exc:
            raise SystemExit(f"as-built certificate refused: {exc}") from exc
        names = (*SHIPPED, *(n for f in as_built for n in AS_BUILT[f][2] if n not in SHIPPED))
        files = {n: hashlib.sha256((DIST / n).read_bytes()).hexdigest() for n in names}
        doc = {"schema": SCHEMA_V2, "version": version, "families": entries,
               "stamped": datetime.date.today().isoformat(), "files": files}
        errors = certificate_errors(version, ROOT, DIST, doc=doc, polished="polished" in as_built)
        if errors:
            raise SystemExit("certificate refused: " + "; ".join(errors))
        VERSION_FILE.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8", newline="\n")
        return doc
    stamped = dict(previous.get("families") or {})
    stamped.update(dict.fromkeys(families, version))
    files = {name: hashlib.sha256((DIST / name).read_bytes()).hexdigest() for name in SHIPPED if (DIST / name).is_file()}
    doc = {"schema": SCHEMA, "version": version if set(stamped.values()) == {version} else "mixed", "families": stamped,
           "stamped": datetime.date.today().isoformat(), "files": files}
    VERSION_FILE.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8", newline="\n")
    return doc


def run(step: Step) -> None:
    print(f"\n[stamp:{step.family}] {step.name}\n  $ {step.shown()}", flush=True)
    env = dict(os.environ, **dict(step.env))
    proc = subprocess.run(step.argv, cwd=ROOT, env=env)
    if proc.returncode != 0:
        raise SystemExit(f"step failed ({proc.returncode}): {step.family}: {step.name}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", required=True)
    ap.add_argument("--only", default=",".join(FAMILIES), help=f"comma list of {FAMILIES}")
    ap.add_argument("--as-built", default="", help="provenance-bound, unchanged families: gen2,polished")
    ap.add_argument("--plan", action="store_true", help="print the steps and exit")
    ap.add_argument("--promote-overlays", action="store_true", dest="promote",
                    help="Gen 2: run --promote-overlays even if the rows are not ADMITTED yet (owner G4 signature + green release evidence)")
    ap.add_argument("--rom-dir", action="append", type=pathlib.Path, default=[], help="where the clean ROMs live (repeatable)")
    ap.add_argument("--accept-new-identity", action="store_true",
                    help="regenerate after an INTENTIONAL identity change (a code change or a new masking rule): the canonical-identity "
                         "check is reported but does not stop the tool. Never use it for a release stamp.")
    ap.add_argument("--no-jar", action="store_true",
                    help="do not install the updated UPR fork jar (it is a shared cache file outside git: other trees' tests read it)")
    ap.add_argument("--gen2-arg", action="append", default=[], help="extra argument for tools/build_gen2_companion.py (repeatable)")
    args = ap.parse_args()
    version = check_version(args.version)
    families = tuple(f.strip() for f in args.only.split(",") if f.strip())
    as_built = tuple(f.strip() for f in args.as_built.split(",") if f.strip())
    check_scope(families, as_built)
    check_previous_policy(as_built)
    try:
        unchanged = {f: as_built_record(f, ROOT, DIST) for f in as_built}
    except (OSError, ValueError) as exc:
        raise SystemExit(f"as-built preflight refused: {exc}") from exc
    main_checkout = pathlib.Path(subprocess.run(["git", "rev-parse", "--git-common-dir"], cwd=ROOT, capture_output=True,
                                                text=True, check=True).stdout.strip()).resolve().parent
    rom_dirs = [] if args.plan else [*args.rom_dir, ROOT, main_checkout]
    steps = plan(version, families, rom_dirs, promote=args.promote or None, gen2_args=tuple(args.gen2_arg),
                  jar=not args.no_jar)
    if args.plan:
        for s in steps:
            print(f"[{s.family}] {s.name}: {s.shown()}")
        if as_built:
            print(f"[as-built] certify {', '.join(as_built)} against published provenance; do not rebuild")
        return 0
    before, md5_before = identities(), pinned_md5s()
    seen: set[str] = set()
    for step in steps:
        run(step)
        seen.add(step.family)
    drift = identity_drift(before, identities(), families)
    if drift and args.accept_new_identity:
        print("\nNOTE: the canonical identity moved (accepted by --accept-new-identity):\n  " + "\n  ".join(drift))
        drift = []
    if drift:
        print("\nSTOP: the canonical identity moved, so this was not a version-only change. Restore the artifacts and qualify the\n"
              "change as code:\n  " + "\n  ".join(drift), file=sys.stderr)
        return 2
    try:
        if unchanged != {f: as_built_record(f, ROOT, DIST) for f in as_built}:
            raise ValueError("as-built provenance changed during the stamp")
    except (OSError, ValueError) as exc:
        raise SystemExit(f"as-built final validation refused: {exc}") from exc
    touched = update_md5_tables(md5_before, pinned_md5s())
    doc = write_version_file(version, families, as_built=as_built)
    identity = "regenerated (--accept-new-identity)" if args.accept_new_identity else "unchanged"
    print(f"\nstamped {sorted(seen)} as {version}; canonical identities {identity} ({len(before)} records);"
          f" md5 tables updated in {touched or 'no file'}; wrote {VERSION_FILE.relative_to(ROOT)} ({len(doc['files'])} files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
