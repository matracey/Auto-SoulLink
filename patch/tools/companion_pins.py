"""patch/dist/companion_pins.json: the build manifest of the companions that ship as UPS patches.

    {"schema": "slink-companion-pins-v1", "pins": {slug: {"patched_md5", "canonical_sha1", "version",
                                                         "version_slot": {"offset", "length"}}}}

One entry per slug (rr, rb-red, rb-blue), each written by the tool that owns that build; server/patcher.py reads
`patched_md5` and the stamp tool rewrites the file for a release. `update` is a read-modify-write that touches only
the slug it is given, so the builders can run in any order (and next to each other) without clobbering one another.
"""
from __future__ import annotations

import json
import os
import stat
import tempfile
from pathlib import Path

SCHEMA = "slink-companion-pins-v1"
PINS = Path(__file__).resolve().parents[1] / "dist" / "companion_pins.json"


def load(path: Path = PINS) -> dict:
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    if doc.get("schema") != SCHEMA:
        raise ValueError(f"{path}: schema {doc.get('schema')!r}, expected {SCHEMA!r}")
    return doc


def update(slug: str, fields: dict, path: Path = PINS) -> dict:
    """Merge `fields` into pins[slug] (created if absent) and write the file back; returns the merged pin."""
    path = Path(path).resolve()
    # Never unlink the sidecar: every writer must lock the same inode.
    with Path(str(path) + ".lock").open("a+b") as lock:
        if os.name == "nt":
            import msvcrt

            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        doc = load(path)
        pin = doc["pins"].setdefault(slug, {})
        pin.update(fields)
        tmp = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", newline="\n", dir=path.parent,
                prefix=f".{path.name}.", suffix=".tmp", delete=False,
            ) as output:
                tmp = Path(output.name)
                output.write(json.dumps(doc, indent=2) + "\n")
            os.chmod(tmp, stat.S_IMODE(path.stat().st_mode))
            os.replace(tmp, path)
        finally:
            if tmp is not None:
                tmp.unlink(missing_ok=True)
        return pin
