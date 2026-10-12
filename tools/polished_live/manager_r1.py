#!/usr/bin/env python3
"""Stage R1 (live randomization run, owner-authorised 2026-10-04): the REAL Manager over HTTP.

    python tools/polished_live/manager_r1.py            # shipped release jar (POL_JAR overrides)
    python tools/polished_live/manager_r1.py nojar      # no explicit jar override; Manager may find a fallback
    python tools/polished_live/manager_r1.py oldjar     # an older trusted fork jar (no polished_offsets.ini)
    POL_OLD_JAR overrides the allowlisted pre-v0.4.0 negative-control jar.

Starts `python -m server.manager` on a private port with a private --data-dir under F:/slink-work/lanes/pol-rand,
drives it ONLY through its HTTP API (no internals), and records every call + response in <lane>/r1_<mode>/calls.jsonl.
The jar mode creates a gen2_polished run, provisions a randomized PAIR from the picker's Polished release with the
companion on, downloads both ROMs through /api/runs/{id}/rom/{p} and copies the run's rom_contract.json.
The raw write_domain.json records endpoint diff confinement (not a Java memory-write sequence),
retaining the real pre-UPR overlay, post-UPR output, downloaded cartridge and domain resources.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from bisect import bisect_right
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

REPO = Path(__file__).resolve().parents[2]
LANE = Path(os.environ.get("POL_LANE", "F:/slink-work/lanes/pol-rand"))
JAR = Path(os.environ.get("POL_JAR", "E:/Google Drive/SLink/.cache/slink-upr/PokeRandoZX.jar"))
OLD_JAR = Path(os.environ.get("POL_OLD_JAR", "E:/Google Drive/SLink/.cache/slink-upr/PokeRandoZX-pre-v0.4.0-db4bc65c.jar"))
RELEASE = Path("F:/slink-work/cache/polished/release/polishedcrystal-3.2.3.gbc")
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
# widest sensible Polished spec: every category the handler writes (forms are always in the pool, no setting)
SPEC = {"wild": "random", "wild_restriction": "none", "wild_block_legendaries": True, "wild_min_catch_rate": 0,
        "wild_levels": 0, "starters": "random", "statics": "random", "static_levels": 0, "trainers": "random",
        "trainers_similar_strength": False, "trainers_rival_starter": True, "trainers_block_legendaries": True,
        "trainers_match_typing": False, "trades": "given_and_requested"}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Api:
    def __init__(self, port: int, log: Path):
        self.base, self.log = f"http://127.0.0.1:{port}", log

    def call(self, method: str, path: str, body=None, raw=False, timeout=900):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"} if data else {})
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                status, payload = r.status, r.read()
        except urllib.error.HTTPError as e:
            status, payload = e.code, e.read()
        row = {"t": t0, "secs": round(time.time() - t0, 2), "method": method, "path": path, "body": body,
               "status": status}
        if raw:
            row["bytes"], row["sha1"] = len(payload), hashlib.sha1(payload).hexdigest()
        else:
            try:
                row["response"] = json.loads(payload)
            except ValueError:
                row["response_text"] = payload.decode("utf-8", "replace")
        with open(self.log, "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
        print(f"[r1] {method} {path} -> {status} ({row['secs']}s)", flush=True)
        return status, (payload if raw else row.get("response", row.get("response_text")))


def _check_jar(jar: Path, mode: str) -> dict:
    """Fail closed on an unpinned jar or a mislabeled positive/negative control."""
    data = jar.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    allowlist = REPO / "data/upr_jars.json"
    pins = allowlist.read_bytes()
    if digest not in json.loads(pins).values():
        raise ValueError(f"jar SHA256 {digest} is not in the release allowlist")
    with ZipFile(BytesIO(data)) as archive:
        present = "com/dabomstew/pkrandom/config/polished_offsets.ini" in archive.namelist()
    if mode in ("jar", "oldjar") and present != (mode == "jar"):
        raise ValueError(f"{mode}: polished_offsets.ini must be {'present' if mode == 'jar' else 'absent'}")
    return {"path": jar.resolve().as_posix(), "sha256": digest, "polished_offsets_present": present,
            "allowlist_path": allowlist.resolve().as_posix(), "allowlist_sha256": hashlib.sha256(pins).hexdigest()}


def _retain_bytes(out: Path, name: str, data: bytes, *, source: str) -> dict:
    target = out / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return {"path": name, "source_path": source, "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(), "sha1": hashlib.sha1(data).hexdigest()}


def _capture_domain_model(out: Path, jar: Path) -> dict:
    """Snapshot the resources before provisioning; model the existing exclusion audit exactly."""
    from server import upr_polished_write_domain as domain

    resources = {}
    for name, source in {
        "domain_source": REPO / "server/upr_polished_write_domain.py",
        "pipeline_source": REPO / "server/upr_pipeline.py",
        "overlay_provenance": REPO / "data/polished/overlay_provenance.json",
        "jar": jar,
    }.items():
        resources[name] = _retain_bytes(out, "write_domain/resources/" + source.name,
                                        source.read_bytes(), source=source.resolve().as_posix())
    prov = json.loads((out / resources["overlay_provenance"]["path"]).read_text(encoding="utf-8"))
    ups = REPO / prov["output"]["ups"]["file"]
    patch = ups.read_bytes()
    resources["ups"] = _retain_bytes(out, "write_domain/resources/" + ups.name, patch,
                                     source=ups.resolve().as_posix())
    entry = "com/dabomstew/pkrandom/config/polished_offsets.ini"
    with ZipFile(out / resources["jar"]["path"]) as archive:
        resources["jar_offsets"] = _retain_bytes(out, "write_domain/resources/polished_offsets.ini",
                                                archive.read(entry), source=jar.resolve().as_posix() + ":" + entry)
    size = prov["output"]["size"]
    protected = [
        {"name": "cartridge_header", "start": domain.HEADER.start, "end": domain.HEADER.stop},
        {"name": "slink_service_bank", "start": domain.BANK_7E.start, "end": domain.BANK_7E.stop},
        *[{"name": "companion_ups", "start": start, "end": end} for start, end in domain.ups_spans(patch)],
    ]
    allowed, cursor = [], 0
    for row in sorted(protected, key=lambda item: item["start"]):
        start, end = min(size, row["start"]), min(size, row["end"])
        if cursor < start:
            allowed.append({"name": f"unprotected_rom_{cursor:06x}_{start:06x}", "start": cursor, "end": start})
        cursor = max(cursor, end)
    if cursor < size:
        allowed.append({"name": f"unprotected_rom_{cursor:06x}_{size:06x}", "start": cursor, "end": size})
    table = {"schema": "polished-r1-permitted-domain-v1", "rom_size": size,
             "offset_semantics": "end-exclusive", "protected_regions": protected, "allowed_regions": allowed,
             "policy": "complement of header, UPS-owned bytes and bank $7E; content invariants are separate"}
    encoded = (json.dumps(table, indent=2, sort_keys=True) + "\n").encode("utf-8")
    return {"resources": resources, "table": _retain_bytes(out, "write_domain/domain_table.json", encoded,
                                                          source="server.upr_polished_write_domain")}


def _emit_write_domain(out: Path, run_dir: Path, model: dict) -> None:
    table = json.loads((out / model["table"]["path"]).read_text(encoding="utf-8"))
    allowed = table["allowed_regions"]
    starts = [region["start"] for region in allowed]
    players = {}
    for player in "ab":
        pre_path = run_dir / "roms" / f"{player}_companion.gbc"
        post_path = run_dir / "roms" / f"{player}_randomized.gbc"
        pre, post = pre_path.read_bytes(), post_path.read_bytes()
        if len(pre) != len(post) or len(pre) != table["rom_size"]:
            raise ValueError(f"player {player}: size changed {len(pre)} -> {len(post)} (domain {table['rom_size']})")
        images = {
            "post_overlay_pre_upr": _retain_bytes(out, f"write_domain/{player}/pre-upr.gbc", pre,
                                                  source=pre_path.resolve().as_posix()),
            "post_upr": _retain_bytes(out, f"write_domain/{player}/post-upr.gbc", post,
                                     source=post_path.resolve().as_posix()),
        }
        downloaded = out / f"rom_{player}.gbc"
        data = downloaded.read_bytes()
        images["final_download"] = {"path": downloaded.name, "source_path": downloaded.resolve().as_posix(),
                                    "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                                    "sha1": hashlib.sha1(data).hexdigest()}
        changes, outside = [], []
        for offset, (old, new) in enumerate(zip(pre, post, strict=True)):
            if old == new:
                continue
            index = bisect_right(starts, offset) - 1
            region = allowed[index] if index >= 0 and offset < allowed[index]["end"] else None
            changes.append({"offset": offset, "old": old, "new": new,
                            "region": region["name"] if region else None})
            if region is None:
                outside.append(offset)
        players[player] = {"images": images, "changed_offsets": changes, "out_of_domain": outside}
    record = {"schema": "polished-r1-write-domain-v1", "write_ops": None,
              "write_ops_provenance": "offline randomizer: no runtime write sequence; criterion is diff confinement",
              "domain_model": model, "players": players}
    (out / "write_domain.json").write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main(mode: str) -> int:
    out = LANE / f"r1_{mode}"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    jar_input = _check_jar(JAR if mode == "jar" else OLD_JAR, mode) if mode in ("jar", "oldjar") else None
    if jar_input is not None:
        (out / "jar_input.json").write_text(json.dumps(jar_input, indent=2) + "\n", encoding="utf-8")
    env = dict(os.environ)
    env.pop("SLINK_UPR_JAR", None)
    if mode == "jar":
        env["SLINK_UPR_JAR"] = str(JAR)
    elif mode == "oldjar":
        env["SLINK_UPR_JAR"] = str(OLD_JAR)
    port = free_port()
    log = open(out / "manager.log", "w", encoding="utf-8")  # noqa: SIM115
    mgr = subprocess.Popen([sys.executable, "-m", "server.manager", "--host", "127.0.0.1", "--port", str(port),
                            "--data-dir", str(out / "mgr")], cwd=str(REPO), env=env, stdout=log,
                           stderr=subprocess.STDOUT)
    (out / "manager.pid").write_text(str(mgr.pid))
    print(f"[r1] manager pid {mgr.pid} port {port} mode {mode}", flush=True)
    api = Api(port, out / "calls.jsonl")
    summary: dict = {"mode": mode, "manager_pid": mgr.pid, "port": port, "jar_env": env.get("SLINK_UPR_JAR")}
    summary["jar_input"] = jar_input
    try:
        for _ in range(120):
            try:
                urllib.request.urlopen(api.base + "/api/runs", timeout=2).read()
                break
            except OSError:
                time.sleep(0.5)
        _, roms = api.call("GET", "/api/roms")
        pol = [r for r in roms["roms"] if r.get("family") == "gen2_polished"]
        summary["picker_polished"] = pol
        release = next((r["path"] for r in pol if r.get("kind") == "clean"), str(RELEASE))
        q = urllib.parse.urlencode({"rom_a": release, "rom_b": release})
        _, summary["status"] = api.call("GET", f"/api/randomizer/status?{q}")
        if summary["status"].get("jar_found"):
            summary["effective_jar"] = _check_jar(Path(summary["status"]["jar"]), mode)
        domain_model = _capture_domain_model(out, Path(summary["status"]["jar"])) if summary["status"].get("jar_polished") else None
        _, new = api.call("POST", "/api/runs/new", {"name": f"pol-rand {mode}", "game": "gen2_polished"})
        run_id = new.get("run_id") or new.get("run", {}).get("run_id")
        summary["run_id"] = run_id
        status, page = api.call("GET", f"/runs/{run_id}/randomizer")
        (out / "randomizer_page.html").write_text(page if isinstance(page, str) else json.dumps(page), encoding="utf-8")
        status, cart = api.call("POST", f"/api/runs/{run_id}/cartridges",
                                {"rom_a": release, "rom_b": release, "companion": True, "randomize": True,
                                 "spec": SPEC})
        summary["cartridges_status"], summary["cartridges"] = status, cart
        if status == 200 and cart.get("ok"):
            for p in "ab":
                _, blob = api.call("GET", f"/api/runs/{run_id}/rom/{p}", raw=True)
                (out / f"rom_{p}.gbc").write_bytes(blob)
                summary[f"rom_{p}_sha1"] = hashlib.sha1(blob).hexdigest()
            run_dir = out / "mgr" / run_id
            if domain_model is None:
                raise ValueError("successful randomized pair has no captured write-domain resources")
            _emit_write_domain(out, run_dir, domain_model)
            shutil.copyfile(run_dir / "rom_contract.json", out / "rom_contract.json")
            contract = json.loads((out / "rom_contract.json").read_text(encoding="utf-8"))
            summary["contract"] = contract
            summary["contract_matches_downloads"] = all(
                contract["players"][p]["rom_sha1"] == summary[f"rom_{p}_sha1"] for p in "ab")
            _, summary["runs"] = api.call("GET", "/api/runs")
    finally:
        if mgr.poll() is None:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(mgr.pid)], capture_output=True)
            mgr.wait(timeout=20)
        log.close()
    (out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k not in ("runs", "cartridges")}, indent=1)[:6000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "jar"))
