#!/usr/bin/env python3
"""Which production code a Gen 2 receipt was earned on (card CODE-DIGEST).

A receipt that binds only fixture bytes and the clean ROM sha1 stays "valid" after the client or
server changes under it. A runner records `run_stamp()` next to its verdict, and
tools/verify_gen2_release.py compares that stamp's digest with `head_digest()`: when they differ,
the receipt is STALE.

The digest is sha256 over the sorted "<path> <git blob id>" lines of every tracked production file
at HEAD. It is exact, and cheap because git already hashed the content. It only binds the tree the
runner ran on when that tree is clean for these paths, so run_stamp() also records the dirty
production paths. The verifier treats any dirty stamp as STALE.

    python tools/gen2_code_digest.py          # print this checkout's stamp as JSON
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# What a Gen 2 duo or live gate executes: the Gen 2 client, the shared Lua modules at lua/ top level
# that it dofiles, the extracted shared core, the server, and the Gen 2 data packs, whose shipped
# receipts and admission rows the client re-validates at load. Drivers (lua/tests) and docs are
# harness and prose, so they are not included.
CODE_SCOPE = ("lua/*.lua", "lua/gen2/**", "lua/core/**", "server/**/*.py",
              "data/games/gen2_crystal/**", "data/games/gen2_gold/**", "data/games/gen2_silver/**")


# Prose that can sit inside a data pack (the pack README) is not what the game executes. Only these
# suffixes are dropped -- fail-safe: any other file type under the packs stays in the digest, and
# tests/unit/test_gen2_receipt_code_digest.py fails on a new one so it gets a deliberate decision.
# (Before this, the comment above claimed docs were excluded while the packs' README was counted.)
DOC_SUFFIXES = (".md", ".txt", ".rst")


def is_production(path: str) -> bool:
    if path.startswith(("lua/gen2/", "lua/core/", "data/games/gen2_crystal/", "data/games/gen2_gold/",
                        "data/games/gen2_silver/")):
        return not path.endswith(DOC_SUFFIXES)
    if path.startswith("lua/") and path.count("/") == 1:
        return path.endswith(".lua")
    return path.startswith("server/") and path.endswith(".py")


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                          encoding="utf-8", check=True).stdout


def digest_of(rows) -> str:
    """rows: (path, blob id) pairs. Order-independent."""
    return hashlib.sha256("\n".join(sorted(f"{path} {blob}" for path, blob in rows)).encode()).hexdigest()


# The overlay row of a pack's admission.json (the SLink companion build: its sha1/md5/UPS, BUILT -> ADMITTED, the grant
# fingerprint) is a BUILD OUTPUT the owner promotes and a release stamp regenerates. The evidence is bound to it by its own
# means (overlay_sha1 + equivalent_sha1s, the G4 grant fingerprint), not by this digest: hashing it here would make promoting
# the overlays, or stamping a release version, stale every receipt that justified the promotion. Every other row (the clean
# ROMs, the G1 gate) stays in the digest.
ADMISSION_PACK = re.compile(r"data/games/gen2_(?:crystal|gold|silver)/admission\.json")


def admission_id(text: str) -> str:
    """A stand-in for the blob id of an admission.json: the sha256 of its canonical JSON without the overlay row(s)."""
    doc = json.loads(text)
    doc["artifacts"] = [row for row in doc.get("artifacts", []) if row.get("kind") != "overlay"]
    return "norm:" + hashlib.sha256(json.dumps(doc, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _head_rows(root: Path, rev: str) -> list[tuple[str, str]]:
    rows = []
    for line in _git(root, "ls-tree", "-r", rev).splitlines():
        meta, _, path = line.partition("\t")
        if is_production(path):
            blob = meta.split()[2]
            if ADMISSION_PACK.fullmatch(path):
                blob = admission_id(_git(root, "show", f"{rev}:{path}"))
            rows.append((path, blob))
    return rows


def head_digest(root: Path = ROOT, rev: str = "HEAD") -> str:
    return digest_of(_head_rows(root, rev))


# Only this exact diagnostic-allocation edit can carry the C-5 evidence forward.
# test_admission.py proves the source delta and differential Lua behavior.
C5_LAZY_ERROR_BLOBS = (
    "ed2ea5ad2291adbe9a2e200c46d8e260941efeea",
    "002c1c1d51655d15bc0c4506e0cec187f3b75601",
)


def c5_admission_equivalent(root: Path, head: str) -> str | None:
    """Return the original evidence digest for the pinned SOURCE/MODEL-equivalent edit.

    Raw code digests and physical receipts stay unchanged. Any other production
    edit, including an uncommitted one, must earn its own evidence.
    """
    if not (root / ".git").exists():
        return None
    rows = _head_rows(root, "HEAD")
    path = "lua/admission.lua"
    before, after = C5_LAZY_ERROR_BLOBS
    if digest_of(rows) != head or dict(rows).get(path) != after:
        return None
    if dirty_paths(root):
        return None
    return digest_of((name, before if name == path else blob) for name, blob in rows)


def dirty_paths(root: Path = ROOT) -> list[str]:
    """Production paths whose working tree differs from HEAD, untracked files included."""
    out = []
    for line in _git(root, "status", "--porcelain", "--untracked-files=all").splitlines():
        path = line[3:].split(" -> ")[-1].strip('"')
        if is_production(path):
            out.append(path)
    return sorted(out)


def run_stamp(root: Path = ROOT) -> dict:
    """The stamp a runner writes beside its verdict (e2e_duo: a `CODE_DIGEST {json}` line in the pydec
    receipt; JSON gate receipts: a top-level "code_digest" object)."""
    return {"schema": "gen2-code-digest-v1", "digest": head_digest(root),
            "commit": _git(root, "rev-parse", "HEAD").strip(), "scope": list(CODE_SCOPE),
            "dirty": dirty_paths(root)}


def stamp_verdict(stamp, head: str) -> str | None:
    """None when the stamp binds HEAD's production code; otherwise STALE-UNKNOWN or STALE with a reason."""
    if not isinstance(stamp, dict) or stamp.get("schema") != "gen2-code-digest-v1":
        return "STALE-UNKNOWN: receipt records no code digest"
    if stamp.get("dirty") != []:
        return f"STALE: run on a dirty production tree ({len(stamp.get('dirty') or [1])} path(s))"
    if stamp.get("digest") != head:
        return f"STALE: code digest {str(stamp.get('digest'))[:12]} (commit {str(stamp.get('commit'))[:8]}) != HEAD {head[:12]}"
    return None


if __name__ == "__main__":
    json.dump(run_stamp(), sys.stdout, indent=2)
    print()
