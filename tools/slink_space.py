#!/usr/bin/env python3
"""One work root for every SLink scratch, plus a report and a guarded prune/move.

Layout: ``$SLINK_WORK_ROOT`` (default F:/slink-work) with fixed subdirs
``wt/`` (git worktrees), ``lanes/`` (emulator lanes + state dirs), ``tmp/`` (pytest and
tool temps), ``cache/`` (heavy pinned inputs), ``evidence/`` (archived uncommitted run
evidence).  Other tools adopt it with ``from slink_space import work_root``.

    python tools/slink_space.py report [--json]
    python tools/slink_space.py prune                 # dry run: prints the plan, writes it
    python tools/slink_space.py prune --apply         # re-scans, aborts if anything changed
    python tools/slink_space.py move-to-work-root [--apply]
    python tools/slink_space.py root [category]       # print a work-root path

prune/move are DRY-RUN unless --apply.  An apply re-reads the plan file the dry run
wrote, rebuilds the plan from live state and aborts unless the two are identical.
See docs/space.md.  Stdlib only.
"""
from __future__ import annotations

import argparse
import contextlib
import glob
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_WORK_ROOT = "F:/slink-work"
CATEGORIES = ("wt", "lanes", "tmp", "cache", "evidence")
DEFAULT_LANE_AGE = 3 * 86400
DEFAULT_TMP_AGE = 2 * 3600
_EVIDENCE_NAME = re.compile(r"evidence|receipt|proof", re.I)
_DRIVE_COPY = re.compile(r" \(\d+\)(\.[^./\\ ]+)?$")  # Google Drive conflict copy: "master (1)"
_SHA = re.compile(r"^[0-9a-f]{40}$")
_OWN_FILES = re.compile(r"slink-space-(apply\.lock|(prune|move)-plan\.json)")
# Deleted files Google Drive put back into the main checkout (seen 2026-10-02).
KNOWN_DRIVE_RESTORES = frozenset({"lua/memory_gba.lua", "server/adapters/gen2_crystal.py"})
_IS_WIN = os.name == "nt"


def work_root(category: str | None = None) -> Path:
    """The SLink work root (or one category under it), created on first use."""
    if category is not None and category not in CATEGORIES:
        raise ValueError(f"unknown work-root category {category!r}; expected one of {CATEGORIES}")
    path = Path(os.environ.get("SLINK_WORK_ROOT") or DEFAULT_WORK_ROOT)
    if category:
        path = path / category
    path.mkdir(parents=True, exist_ok=True)
    return path


# ---------------------------------------------------------------- filesystem helpers

def _norm(p) -> str:
    s = str(p)
    for prefix in ("\\\\?\\", "\\??\\", "//?/"):
        if s.startswith(prefix):
            s = s[len(prefix):]
    return os.path.normcase(os.path.abspath(s)).replace("\\", "/").rstrip("/")


def link_detection_available() -> bool:
    """Windows link detection reads reparse attributes straight off lstat (any Python
    version); without them a junction would look like a plain dir, so deleters refuse."""
    return not _IS_WIN or hasattr(os.lstat("."), "st_reparse_tag")


def _require_link_detection() -> None:
    if not link_detection_available():
        raise RuntimeError("link detection unavailable on this Python; refusing to delete or copy")


_LINK_TAGS = {0xA000000C: "symlink", 0xA000001D: "symlink", 0xA0000003: "junction"}
LINK_KINDS = ("symlink", "junction")


def _st_link_kind(st) -> str | None:
    """'symlink' / 'junction' for the reparse tags that are links, 'leaf' for any other
    reparse point (cloud placeholder, dedup, ...: counted, never entered, never recreated
    as a link), None for an ordinary file or dir."""
    if stat.S_ISLNK(st.st_mode):
        return "symlink"
    if getattr(st, "st_file_attributes", 0) & 0x400:  # FILE_ATTRIBUTE_REPARSE_POINT
        return _LINK_TAGS.get(getattr(st, "st_reparse_tag", None), "leaf")
    return None


def _entry_link_kind(entry: os.DirEntry) -> str | None:
    try:
        return _st_link_kind(entry.stat(follow_symlinks=False))
    except OSError:
        return "leaf"  # unreadable: fail closed, never enter it


def _entry_is_link(entry: os.DirEntry) -> bool:
    return _entry_link_kind(entry) in LINK_KINDS


def _path_is_link(p) -> bool:
    try:
        return _st_link_kind(os.lstat(str(p))) in LINK_KINDS
    except OSError:
        return False


def _link_target(p: str) -> str:
    t = os.readlink(p)
    for prefix in ("\\\\?\\", "\\??\\"):
        if t.startswith(prefix):
            t = t[len(prefix):]
    return t


def tree_stats(path) -> dict:
    """Size, file count, newest mtime and links of a tree.  Never follows a symlink or a
    junction (worktrees here hold junctions into the shared .cache)."""
    p = str(path)
    out = {"size": 0, "files": 0, "newest": 0.0, "links": [], "leaf_dirs": 0}
    try:
        st = os.lstat(p)
    except OSError:
        return out
    out["newest"] = st.st_mtime
    if _path_is_link(p) or not stat.S_ISDIR(st.st_mode):
        out["size"], out["files"] = (0, 0) if _path_is_link(p) else (st.st_size, 1)
        return out
    stack = [p]
    while stack:
        d = stack.pop()
        try:
            entries = list(os.scandir(d))
        except OSError:
            continue
        for e in entries:
            kind = _entry_link_kind(e)
            if kind in LINK_KINDS:
                with contextlib.suppress(OSError):
                    out["links"].append([os.path.relpath(e.path, p), _link_target(e.path),
                                         kind, os.path.isdir(e.path)])
                continue
            try:
                est = e.stat(follow_symlinks=False)
            except OSError:
                continue
            out["newest"] = max(out["newest"], est.st_mtime)
            if kind == "leaf":
                out["leaf_dirs"] += int(stat.S_ISDIR(est.st_mode))
            if stat.S_ISDIR(est.st_mode) and kind != "leaf":
                stack.append(e.path)
            else:
                out["size"] += est.st_size
                out["files"] += 1
    return out


def _force(fn, p: str) -> None:
    try:
        fn(p)
    except PermissionError:  # Drive leaves read-only bits behind
        os.chmod(p, stat.S_IWRITE)
        fn(p)


def _rmtree(path) -> None:
    """Delete a tree, clearing read-only bits.  A symlink or junction is unlinked, never
    entered.  ponytail: own walker instead of shutil.rmtree(onerror=chmod) so link safety
    doesn't hang on how a given Python version classifies junctions."""
    _require_link_detection()
    p = str(path)
    if not _path_is_link(p) and os.path.isdir(p):
        try:
            top_leaf = _st_link_kind(os.lstat(p)) == "leaf"
        except OSError:
            top_leaf = True
        if top_leaf or tree_stats(p)["leaf_dirs"]:
            raise RuntimeError(f"{p}: holds a non-link reparse dir (cloud placeholder?); "
                               "refusing to delete it")
    _rmtree_walk(p)


def _rmtree_walk(p: str) -> None:
    if _path_is_link(p):
        try:
            os.unlink(p)
        except OSError:
            os.rmdir(p)
        return
    if not os.path.isdir(p):
        if os.path.lexists(p):
            _force(os.remove, p)
        return
    for e in list(os.scandir(p)):
        kind = _entry_link_kind(e)
        if kind == "leaf" and e.is_dir(follow_symlinks=False):
            raise RuntimeError(f"{e.path}: non-link reparse dir; refusing to enter it")
        if kind in LINK_KINDS or e.is_dir(follow_symlinks=False):
            _rmtree_walk(e.path)
        else:
            _force(os.remove, e.path)
    _force(os.rmdir, p)


# ---------------------------------------------------------------- process helpers

_PROCS: list | None = None


def processes() -> list[tuple[int, str]]:
    """(pid, command line) of every running process; cached for one scan."""
    global _PROCS
    if _PROCS is None:
        if _IS_WIN:
            cmd = ["powershell", "-NoProfile", "-Command",
                   "Get-CimInstance Win32_Process | ForEach-Object "
                   "{ \"$($_.ProcessId)`t$($_.CommandLine)\" }"]
        else:
            cmd = ["ps", "-eo", "pid=,args="]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=120)
            if r.returncode:
                raise OSError(f"{cmd[0]} exited {r.returncode}: {r.stderr.strip()[:200]}")
        except (OSError, subprocess.SubprocessError) as e:
            _PROCS = e  # cached failure: every caller sees it, nobody re-runs it
            raise
        procs = []
        for line in r.stdout.splitlines():
            pid, _, cmdline = line.strip().replace("\t", " ", 1).partition(" ")
            if pid.isdigit() and cmdline:
                procs.append((int(pid), cmdline))
        _PROCS = procs
    if isinstance(_PROCS, BaseException):
        raise _PROCS
    return _PROCS


def process_scan_problem() -> str | None:
    """Why the in-use scan can't be trusted, or None.  Removal and moves fail closed on it."""
    try:
        procs = processes()
    except Exception as e:
        return f"process scan unavailable ({e})"
    if not procs:
        return "process scan unavailable (no processes listed)"
    if not cwd_scan_available():
        return "process scan unavailable (no cwd scan on this platform)"
    _cwds()
    if _CWD_MISSING:
        return f"process scan incomplete (cwd unreadable for live pid {_CWD_MISSING[:5]})"
    return None


_CWDS: dict | None = None
_CWD_MISSING: list = []
# (PEB.ProcessParameters, RTL_USER_PROCESS_PARAMETERS.CurrentDirectory.DosPath) offsets.
# x64: PEB+0x20, params+0x38 (ntdll PEB / RTL_USER_PROCESS_PARAMETERS layouts, e.g. the
# Process Hacker phnt headers). WOW64: the 32-bit PEB from NtQueryInformationProcess class
# 26 (ProcessWow64Information; NULL for a native process), PEB32+0x10, params32+0x24.
# Cross-checked 2026-10-02 against three live WOW64 processes and a SysWOW64\cmd.exe test.
_PEB64 = (0x20, 0x38)
_PEB32 = (0x10, 0x24)


def cwd_scan_available() -> bool:
    import ctypes
    return (_IS_WIN and ctypes.sizeof(ctypes.c_void_p) == 8) or os.path.isdir("/proc/self")


def _win_cwd(pid: int) -> str | None:
    """Current directory of a process, read from its PEB (64-bit Windows, stdlib only)."""
    import ctypes
    c = ctypes
    k = c.WinDLL("kernel32", use_last_error=True)
    nt = c.WinDLL("ntdll")
    k.OpenProcess.restype = c.c_void_p
    k.ReadProcessMemory.argtypes = [c.c_void_p, c.c_void_p, c.c_void_p, c.c_size_t,
                                    c.POINTER(c.c_size_t)]
    k.CloseHandle.argtypes = [c.c_void_p]
    nt.NtQueryInformationProcess.argtypes = [c.c_void_p, c.c_ulong, c.c_void_p, c.c_ulong,
                                             c.c_void_p]
    h = k.OpenProcess(0x0410, False, pid)  # QUERY_INFORMATION | VM_READ
    if not h:
        return None

    def read(addr, n):
        buf, got = c.create_string_buffer(n), c.c_size_t()
        if not k.ReadProcessMemory(h, addr, buf, n, c.byref(got)) or got.value != n:
            raise OSError("ReadProcessMemory")
        return buf.raw

    def u(raw):
        return int.from_bytes(raw, "little")

    try:
        # A 32-bit (WOW64) process keeps its live cwd in the 32-bit PEB (class 26 gives it).
        peb32 = c.c_void_p()
        if nt.NtQueryInformationProcess(h, 26, c.byref(peb32), c.sizeof(peb32), None) != 0:
            return None
        if peb32.value:
            (p_off, d_off), ptr, peb = _PEB32, 4, peb32.value
        else:
            pbi = (c.c_void_p * 6)()  # PROCESS_BASIC_INFORMATION; [1] = PebBaseAddress
            if nt.NtQueryInformationProcess(h, 0, pbi, c.sizeof(pbi), None) != 0 or not pbi[1]:
                return None
            (p_off, d_off), ptr, peb = _PEB64, 8, pbi[1]
        params = u(read(peb + p_off, ptr))
        ustr = read(params + d_off, 2 * ptr)  # UNICODE_STRING: Length, Max, [pad], Buffer
        length, buf = u(ustr[:2]), u(ustr[ptr:2 * ptr])
        return read(buf, length).decode("utf-16-le") if length and buf else None
    except Exception:  # torn read, odd-length buffer, ...: unreadable, never a crash
        return None
    finally:
        k.CloseHandle(h)


def _proc_cwd(pid: int) -> str | None:
    if _IS_WIN:
        return _win_cwd(pid)
    try:
        return os.readlink(f"/proc/{pid}/cwd")
    except OSError:
        return None


def _cwds() -> dict[int, str]:
    """pid -> current directory for every listed process; cached for one scan.  A live
    process whose cwd can't be read goes to _CWD_MISSING (the scan is then incomplete)."""
    global _CWDS, _CWD_MISSING
    if _CWDS is None:
        out, missing = {}, []
        for pid, _ in processes():
            try:
                cwd = _proc_cwd(pid)
            except Exception:
                cwd = None  # counted as unreadable below: the scan is incomplete, not crashed
            if cwd:
                out[pid] = cwd
            elif pid != os.getpid() and pid_alive(pid):
                missing.append(pid)
        _CWDS, _CWD_MISSING = out, missing
    return _CWDS


def users_of(path) -> list[int]:
    """Pids whose command line names this path (whole components) or whose current
    directory is inside it.  Best effort: open handles are not scanned."""
    if process_scan_problem():
        return []  # callers refuse on process_scan_problem() before anything is removed
    n = _norm(path).lower()
    named = re.compile(re.escape(n) + r"(?=$|[/\s\"'])")
    pids = {pid for pid, cmd in processes() if named.search(cmd.replace("\\", "/").lower())}
    for pid, cwd in _cwds().items():
        c = _norm(cwd).lower()
        if c == n or c.startswith(n + "/"):
            pids.add(pid)
    pids.discard(os.getpid())
    return sorted(pids)


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if _IS_WIN:
        import ctypes
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.OpenProcess.restype = ctypes.c_void_p
        k.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
        k.CloseHandle.argtypes = [ctypes.c_void_p]
        h = k.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return ctypes.get_last_error() == 5  # access denied => it exists
        code = ctypes.c_ulong()
        ok = k.GetExitCodeProcess(h, ctypes.byref(code))
        k.CloseHandle(h)
        return not ok or code.value == 259  # unreadable => assume alive; 259 = STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


# ---------------------------------------------------------------- git helpers

def git_rc(cwd, *args) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def git(cwd, *args) -> str:
    r = git_rc(cwd, *args)
    if r.returncode:
        raise RuntimeError(f"git {' '.join(args)} (in {cwd}) failed: {r.stderr.strip()}")
    return r.stdout


def main_checkout(start) -> Path:
    common = git(start, "rev-parse", "--path-format=absolute", "--git-common-dir").strip()
    return Path(common).parent


def primary_branch(repo) -> str:
    """Resolve the local primary branch without guessing between main and master."""
    remote = git_rc(repo, "symbolic-ref", "--quiet", "refs/remotes/origin/HEAD")
    if remote.returncode == 0:
        ref = remote.stdout.strip()
        prefix = "refs/remotes/origin/"
        if not ref.startswith(prefix):
            raise RuntimeError(f"cannot resolve primary branch from {ref}")
        branch = ref.removeprefix(prefix)
        if git_rc(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}^{{commit}}").returncode:
            raise RuntimeError(f"primary branch {branch} is not available locally")
        return branch
    candidates = [
        name for name in ("main", "master")
        if git_rc(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{name}^{{commit}}").returncode == 0
    ]
    if len(candidates) != 1:
        raise RuntimeError("cannot resolve primary branch: set origin/HEAD or keep one of main/master")
    return candidates[0]


def list_worktrees(repo) -> list[dict]:
    wts, cur = [], None
    for line in git(repo, "worktree", "list", "--porcelain").splitlines():
        if not line:
            cur = None
            continue
        key, _, val = line.partition(" ")
        if key == "worktree":
            cur = {"path": val}
            wts.append(cur)
        elif cur is not None:
            cur[key] = val or True
    return wts


def _status(path, ignored=False) -> str:
    args = ["status", "--porcelain"] + (["--ignored"] if ignored else [])
    return git(path, *args)


def _admin_dir(path) -> str | None:
    try:
        text = (Path(path) / ".git").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    return text.split("gitdir:", 1)[1].strip() if "gitdir:" in text else None


# ---------------------------------------------------------------- scan

def default_locations(repo) -> list[list]:
    """[label, glob pattern, rule, movable] for every known SLink location off the work root."""
    temp = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData/Local") / "Temp"
    temp = temp.as_posix()
    locs = [["slink-wt", "C:/slink-wt/*", "checkouts", True],
            ["slink-cache", "C:/slink-cache/*", "cache", True],
            ["slink", "C:/slink/*", "lane", True]]
    locs += [["temp", f"{temp}/{pat}", "lane", True]
             for pat in ("fs[0-9]*", "fsw-*", "g2*", "uiamb", "exp-fc-*")]
    locs.append(["temp", f"{temp}/pytest-of-*", "pytest", False])
    locs.append(["claude-wt", f"{Path(repo).as_posix()}/.claude/worktrees/*", "worktrees", False])
    return locs


def _root_locations(root) -> list[list]:
    r = Path(root).as_posix()
    # lanes/ belongs to the thread that runs each lane (Gen 4's 46 GB g4, Gen 3's cuts):
    # report only, never age it out, exactly like cache/ and evidence/.
    rules = {"wt": "worktrees", "lanes": "owned-lane", "tmp": "tmp", "cache": "cache",
             "evidence": "evidence"}
    return [[f"work:{c}", f"{r}/{c}/*", rules[c], False] for c in CATEGORIES]


def _age(now, newest) -> float:
    return max(0.0, now - newest) if newest else 0.0


def _fp(st: dict) -> str:
    return f"{st['size']}:{st['files']}:{st['newest']:.0f}"


def _worktree_item(repo, w, scanned, now, lane_age) -> dict:
    path = w["path"]
    item = {"kind": "worktree", "path": path, "branch": None, "head": w.get("HEAD"),
            "lock": None, "size": 0, "newest": 0.0, "links": []}
    if isinstance(w.get("branch"), str):
        item["branch"] = w["branch"].removeprefix("refs/heads/")
    lock = w.get("locked")
    if lock:
        item["lock"] = lock if isinstance(lock, str) else "locked"
        m = re.search(r"\bpid (\d+)", item["lock"])
        item["lock_pid"] = int(m.group(1)) if m else None
        item["lock_alive"] = bool(m) and pid_alive(int(m.group(1)))
    if "prunable" in w or not os.path.isdir(path):
        return {**item, "status": "keep", "reason": "missing checkout; see orphan admin rows"}
    try:
        primary = primary_branch(repo)
    except RuntimeError as exc:
        return {**item, "status": "refuse", "reason": str(exc)}
    if item["branch"] == primary:
        return {**item, "status": "keep", "reason": f"primary branch {primary}"}
    head = item["head"]
    primary_ref = f"refs/heads/{primary}"
    item["merged"] = git_rc(repo, "merge-base", "--is-ancestor", head, primary_ref).returncode == 0
    behind, ahead = git(repo, "rev-list", "--left-right", "--count",
                        f"{primary_ref}...{head}").split()
    item["ahead"], item["behind"] = int(ahead), int(behind)
    status = _status(path)
    lines = status.splitlines()
    item["untracked"] = sum(1 for ln in lines if ln.startswith("??"))
    item["tracked"] = len(lines) - item["untracked"]
    item["status_hash"] = hashlib.sha256(status.encode()).hexdigest()[:16]
    st = tree_stats(path)
    item.update(size=st["size"], newest=st["newest"], links=st["links"], fp=_fp(st))
    if _norm(path) not in scanned:
        return {**item, "status": "refuse", "reason": "outside the scanned set"}
    if item["lock"] and item["lock_pid"] is None:
        return {**item, "status": "refuse", "reason": f"locked ({item['lock']}), no owner pid"}
    if item["lock"] and item["lock_alive"]:
        return {**item, "status": "refuse",
                "reason": f"locked by live pid {item['lock_pid']}"}
    if item["tracked"] or item["untracked"]:
        return {**item, "status": "refuse", "reason":
                f"dirty: {item['tracked']} tracked, {item['untracked']} untracked"}
    if not item["merged"]:
        return {**item, "status": "refuse",
                "reason": f"not merged into {primary} (ahead {item['ahead']})"}
    pids = users_of(path)
    if pids:
        return {**item, "status": "refuse", "reason": f"in use by pid {pids[:5]}"}
    age = _age(now, item["newest"])
    if age < lane_age:
        return {**item, "status": "keep", "reason": f"merged+clean but active {_hage(age)} ago"}
    prob = process_scan_problem()
    if prob:
        return {**item, "status": "refuse", "reason": prob}
    lock_note = f"; stale lock (pid {item['lock_pid']} dead)" if item["lock"] else ""
    return {**item, "status": "stale", "reason": f"merged into {primary} and clean{lock_note}"}


def _path_item(p, label, rule, movable, now, lane_age, tmp_age) -> dict:
    name = os.path.basename(p)
    is_dir = os.path.isdir(p) and not _path_is_link(p)
    item = {"label": label, "path": p, "rule": rule, "movable": movable,
            "kind": "dir" if is_dir else "file"}
    if _path_is_link(p):
        with contextlib.suppress(OSError):
            item["target"] = _link_target(p)
        return {**item, "kind": "link", "size": 0, "newest": 0.0, "links": [],
                "status": "keep", "reason": f"link to {item.get('target', '?')}"}
    st = tree_stats(p)
    item.update(size=st["size"], newest=st["newest"], links=st["links"], fp=_fp(st))
    if rule in ("checkouts", "worktrees"):
        if is_dir and os.path.exists(os.path.join(p, ".git")):
            return {**item, "kind": "checkout", "status": "keep",
                    "reason": "unregistered checkout: may hold uncommitted work, inspect by hand"}
        if not is_dir:
            return {**item, "status": "keep", "reason": "loose file (move files it as evidence)"}
        if rule == "worktrees":  # a worktrees-only place: only an empty leftover is safe to drop
            if st["files"]:
                return {**item, "status": "keep", "reason": "unregistered dir, inspect by hand"}
            prob = process_scan_problem()
            if prob:
                return {**item, "status": "refuse", "reason": prob}
            return {**item, "status": "stale", "reason": "empty leftover worktree dir"}
        rule = "evidence" if _EVIDENCE_NAME.search(name) else "lane"
        item["rule"] = rule
    if rule in ("cache", "evidence"):
        return {**item, "status": "keep", "reason": rule}
    if rule == "tmp" and _OWN_FILES.fullmatch(name):
        return {**item, "status": "keep", "reason": "slink_space's own lock/plan file"}
    if rule == "owned-lane":
        return {**item, "status": "keep", "reason": "work-root lane: owned by its thread, "
                                                    "never pruned"}
    if is_dir and os.path.lexists(os.path.join(p, ".git")):
        return {**item, "kind": "checkout", "status": "refuse",
                "reason": "holds a .git entry (a checkout, not a lane); inspect by hand"}
    pids = users_of(p)
    if pids:
        return {**item, "status": "refuse", "reason": f"in use by pid {pids[:5]}"}
    limit = tmp_age if rule == "tmp" else lane_age
    age = _age(now, st["newest"])
    if age < limit:
        return {**item, "status": "keep", "reason": f"active {_hage(age)} ago"}
    prob = process_scan_problem()
    if prob:
        return {**item, "status": "refuse", "reason": prob}
    since = time.strftime("%Y-%m-%d %H:%M", time.localtime(st["newest"]))
    return {**item, "status": "stale", "reason": f"{rule} untouched since {since}"}


def _drive_hazards(repo) -> list[dict]:
    common = Path(git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir").strip())
    items = []
    candidates = [p for p in common.iterdir() if p.is_file()]
    candidates += [p for p in (common / "refs").rglob("*") if p.is_file()]
    for p in candidates:
        if not _DRIVE_COPY.search(p.name):
            continue
        st = p.stat()
        item = {"label": "drive", "kind": "drive-copy", "path": p.as_posix(), "size": st.st_size,
                "newest": st.st_mtime, "fp": f"{st.st_size}:{st.st_mtime:.0f}", "links": []}
        sha = p.read_text(encoding="utf-8", errors="replace").strip()
        if _SHA.match(sha) and not git_rc(repo, "for-each-ref", "--contains", sha,
                                          "--count=1", "refs/heads").stdout.strip():
            items.append({**item, "status": "refuse", "reason":
                          f"Drive conflict ref -> {sha[:8]}, which no branch contains; "
                          "recover it (git update-ref) first"})
        else:
            items.append({**item, "status": "stale",
                          "reason": "Drive conflict copy git never reads"})
    out = git(repo, "ls-files", "--others", "--exclude-standard", "-z")
    untracked = [f for f in out.split("\0") if f][:500]
    if untracked:
        h = subprocess.run(["git", "-C", str(repo), "hash-object", "--stdin-paths"],
                           input="\n".join(untracked), capture_output=True, text=True,
                           encoding="utf-8")
        blobs = h.stdout.split()
        if h.returncode or len(blobs) != len(untracked):
            items.append({"label": "drive", "kind": "drive-copy", "path": Path(repo).as_posix(),
                          "size": 0, "newest": 0.0, "links": [], "status": "refuse",
                          "reason": f"untracked-copy check skipped: git hash-object failed "
                                    f"({h.returncode}: {h.stderr.strip()[:200]})"})
            return items
        # Prefilter: only blobs git already has can equal a committed revision.
        check = subprocess.run(["git", "-C", str(repo), "cat-file", "--batch-check"],
                               input="\n".join(blobs) + "\n", capture_output=True, text=True,
                               encoding="utf-8").stdout.splitlines()
        known = {ln.split()[0] for ln in check if ln.split()[1:2] != ["missing"]}
        # Not `log --all`: a Drive conflict ref ("master (1)") makes that fatal.
        revs = git(repo, "for-each-ref", "--format=%(objectname)", "refs/heads", "refs/tags")
        for rel, blob in zip(untracked, blobs, strict=True):
            if blob not in known:
                continue
            conflict = bool(_DRIVE_COPY.search(rel))
            base = _DRIVE_COPY.sub(lambda m: m.group(1) or "", rel) if conflict else rel
            hit = subprocess.run(["git", "-C", str(repo), "log", "--stdin", "-1", "--format=%h",
                                  f"--find-object={blob}", "--", base], input=revs,
                                 capture_output=True, text=True, encoding="utf-8").stdout.strip()
            if not hit:
                continue
            p = Path(repo) / rel
            st = p.stat()
            item = {"label": "drive", "kind": "drive-copy", "path": p.as_posix(),
                    "size": st.st_size, "newest": st.st_mtime, "links": [],
                    "fp": f"{st.st_size}:{st.st_mtime:.0f}:{blob}"}
            # Only the shapes Drive is known to produce are junk; anything else equal to an old
            # revision may be someone's deliberate `git rm --cached`.
            if conflict and not (Path(repo) / base).is_file():
                items.append({**item, "status": "refuse", "reason":
                              f"Drive conflict copy of {base}, but {base} itself is gone; "
                              "this may be the only copy, inspect by hand"})
            elif conflict or rel in KNOWN_DRIVE_RESTORES:
                items.append({**item, "status": "stale",
                              "reason": f"Drive-restored copy of {base} as committed in {hit}"})
            else:
                items.append({**item, "status": "refuse", "reason":
                              f"untracked, equals {base} as committed in {hit}; not a known "
                              "Drive restore, inspect by hand"})
    return items


def _orphan_admin(repo) -> list[dict]:
    common = Path(git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir").strip())
    r = git_rc(repo, "worktree", "prune", "--dry-run", "-v")
    items = []
    for line in (r.stdout + r.stderr).splitlines():
        m = re.match(r"Removing worktrees/([^:]+): (.*)", line.strip())
        if m:
            admin = common / "worktrees" / m.group(1)
            st = tree_stats(admin)
            items.append({"label": "admin", "kind": "admin", "path": admin.as_posix(),
                          "id": m.group(1), "size": st["size"], "newest": st["newest"],
                          "links": [], "fp": _fp(st), "status": "stale",
                          "reason": f"orphan worktree admin dir ({m.group(2)})"})
    return items


def scan(repo=None, root=None, locations=None, lane_age=DEFAULT_LANE_AGE,
         tmp_age=DEFAULT_TMP_AGE) -> list[dict]:
    global _PROCS, _CWDS
    _PROCS = _CWDS = None
    repo = main_checkout(repo or Path(__file__).resolve().parent)
    root = Path(root) if root else work_root()
    for c in CATEGORIES:
        (root / c).mkdir(parents=True, exist_ok=True)
    locations = default_locations(repo) if locations is None else locations
    now = time.time()
    wts = list_worktrees(repo)
    registered = {_norm(w["path"]): w for w in wts}
    paths = []  # (path, label, rule, movable)
    for label, pattern, rule, movable in list(locations) + _root_locations(root):
        for p in sorted(glob.glob(pattern)):
            p = Path(p).as_posix()
            if rule == "pytest" or (rule == "tmp" and os.path.basename(p).startswith(
                    "pytest-of-") and not _path_is_link(p)):
                paths += [(Path(c).as_posix(), label, "tmp", False)
                          for c in sorted(glob.glob(f"{glob.escape(p)}/pytest-*"))
                          if not _path_is_link(c) and os.path.isdir(c)]
            else:
                paths.append((p, label, rule, movable))
    scanned = {_norm(p) for p, *_ in paths}
    items = []
    seen = set()
    for p, label, rule, movable in paths:
        n = _norm(p)
        if n in seen:
            continue
        seen.add(n)
        if n in registered:
            items.append({"label": label, "movable": movable,
                          **_worktree_item(repo, registered[n], scanned, now, lane_age)})
        else:
            items.append(_path_item(p, label, rule, movable, now, lane_age, tmp_age))
    for w in wts[1:]:
        if _norm(w["path"]) not in seen:
            items.append({"label": "elsewhere", "movable": False,
                          **_worktree_item(repo, w, scanned, now, lane_age)})
    named = {}
    for it in items:
        if it["kind"] in ("checkout", "worktree") and os.path.isdir(it["path"]):
            admin = _admin_dir(it["path"])
            if admin:
                named[_norm(admin)] = it["path"]
    for it in _orphan_admin(repo):
        user = named.get(_norm(it["path"]))
        if user:
            it.update(status="refuse", reason=f"{it['reason']}, but {user} still points here; "
                      "run `git worktree repair` there instead")
        items.append(it)
    return items + _drive_hazards(repo)


# ---------------------------------------------------------------- plans

def _params(repo, root, locations, lane_age, tmp_age) -> dict:
    repo = main_checkout(repo or Path(__file__).resolve().parent)
    root = Path(root) if root else work_root()
    return {"repo": repo.as_posix(), "root": root.as_posix(),
            "locations": default_locations(repo) if locations is None else locations,
            "lane_age": lane_age, "tmp_age": tmp_age}


def build_plan(kind: str, repo=None, root=None, locations=None, lane_age=DEFAULT_LANE_AGE,
               tmp_age=DEFAULT_TMP_AGE) -> dict:
    params = _params(repo, root, locations, lane_age, tmp_age)
    items = scan(**params)
    plan = {"kind": kind, "params": params, "items": items}
    if kind == "prune":
        plan["actions"] = _prune_actions(params["repo"], items)
    else:
        plan["actions"], plan["refused"] = _move_actions(params, items)
        plan["followups"] = _followups(params["repo"], plan["actions"])
    plan["digest"] = _digest(plan)
    return plan


def _prefer_move(it) -> bool:
    """C: content is moved to the work root, never deleted; only temps and pure junk
    (admin dirs, Drive copies) are deleted there."""
    return (bool(it.get("movable")) and _on_c(it["path"]) and it.get("rule") != "tmp"
            and it["kind"] not in ("admin", "drive-copy", "link"))


def _prune_actions(repo, items) -> list[dict]:
    actions = []
    for it in items:
        if it["status"] != "stale" or _prefer_move(it):
            continue
        a = {"path": it["path"], "size": it["size"], "fp": it.get("fp"), "reason": it["reason"]}
        if it["kind"] == "worktree":
            a.update(op="remove-worktree", branch=it["branch"], head=it["head"],
                     status_hash=it["status_hash"], unlock=bool(it["lock"]),
                     admin=_admin_dir(it["path"]))
        elif it["kind"] == "admin":
            a.update(op="prune-admin")
        elif it["kind"] == "dir":
            a.update(op="remove-dir")
        else:
            a.update(op="remove-file")
        actions.append(a)
    return actions


def _move_actions(params, items) -> tuple[list[dict], list[dict]]:
    root = Path(params["root"])
    actions, refused, seen_dst = [], [], set()
    for it in items:
        if (not it.get("movable") or it["kind"] in ("admin", "drive-copy")
                or not os.path.lexists(it["path"])):
            continue

        def refuse(why, it=it):
            refused.append({"path": it["path"], "reason": why})

        if it["kind"] == "link":
            refuse(f"already a link ({it['reason']})")
            continue
        if it["status"] == "stale" and not _prefer_move(it):
            refuse("stale temp: prune removes it instead")
            continue
        if it.get("lock") and not (it.get("lock_pid") and not it.get("lock_alive")):
            refuse(f"locked ({it['lock']})")
            continue
        prob = process_scan_problem()
        if prob:
            refuse(prob)
            continue
        pids = users_of(it["path"])
        if pids:
            refuse(f"in use by pid {pids[:5]}")
            continue
        name = os.path.basename(it["path"])
        # Registered worktrees -> wt/, pinned inputs -> cache/. Everything else (lanes, state
        # dirs, unregistered checkouts, loose files) -> evidence/<label>/, whatever its age:
        # prune ages out lanes/ and wt/, and "moved, not deleted" has to hold end to end.
        if it["kind"] == "worktree":
            dst = root / "wt" / name
        elif it.get("rule") == "cache":
            dst = root / "cache" / name
        else:
            dst = root / "evidence" / it["label"] / name
        if os.path.lexists(dst):
            refuse(f"destination exists: {dst.as_posix()}")
            continue
        if _norm(dst) in seen_dst:
            refuse(f"destination collides with another item in this plan: {dst.as_posix()}")
            continue
        seen_dst.add(_norm(dst))
        a = {"src": it["path"], "dst": dst.as_posix(), "size": it["size"], "fp": it.get("fp"),
             "links": it.get("links", [])}
        if it["kind"] == "worktree":
            ignored = [ln[3:].rstrip("/") for ln in _status(it["path"], ignored=True).splitlines()
                       if ln.startswith("!!")]
            only_links = all(_path_is_link(os.path.join(it["path"], f)) for f in ignored)
            clean = not (it["tracked"] or it["untracked"]) and only_links
            a.update(op="move-worktree-readd" if clean else "move-worktree-copy",
                     branch=it["branch"], head=it["head"], status_hash=it["status_hash"],
                     unlock=bool(it.get("lock")))
        else:
            a["op"] = "move-file" if it["kind"] == "file" else "move-dir"
        actions.append(a)
    return actions, refused


def _followups(repo, actions) -> list[str]:
    """Tracked lines in the repo that still name a moved path."""
    if not actions:
        return []
    pats = {}
    for a in actions:
        for form in (a["src"], a["src"].replace("/", "\\")):
            pats[form.lower()] = a["dst"]
    args = ["grep", "-n", "-I", "-i", "-F"]
    for p in pats:
        args += ["-e", p]
    out = git_rc(repo, *args).stdout
    lines = []
    for line in out.splitlines():
        low = line.lower()
        hit = max((p for p in pats if p in low), key=len, default=None)
        if hit:
            lines.append(f"{line.split(':', 2)[0]}:{line.split(':', 2)[1]}  {hit} -> {pats[hit]}")
    return lines


# ---------------------------------------------------------------- apply

class PlanChanged(RuntimeError):
    pass


def _strip(actions) -> list:
    return json.loads(json.dumps(actions))


def _digest(plan) -> str:
    body = {k: plan.get(k) for k in ("kind", "params", "actions")}
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


def apply_plan(plan_file, repo=None, root=None, locations=None, lane_age=DEFAULT_LANE_AGE,
               tmp_age=DEFAULT_TMP_AGE, kind=None, on_done=None) -> list[str]:
    """Apply a dry-run plan.  Repo, root, locations and thresholds come from the caller
    (the CLI: always the default locations), never from the file; the file must match."""
    saved = json.loads(Path(plan_file).read_text(encoding="utf-8"))
    if kind and saved.get("kind") != kind:
        raise PlanChanged(f"{plan_file} is a {saved.get('kind')} plan, not {kind}")
    if saved.get("digest") != _digest(saved):
        raise PlanChanged("plan file was edited after the dry run; nothing was done")
    expected = _params(repo, root, locations, lane_age, tmp_age)
    if _strip(saved["params"]) != _strip(expected):
        raise PlanChanged("plan params (repo/root/locations/thresholds) differ from this "
                          "command's; re-run the dry run with the same flags. Nothing was done.")
    with _apply_lock(expected["root"]):
        return _apply_fresh(saved, expected, on_done or (lambda _line: None))


def _apply_fresh(saved, expected, on_done) -> list[str]:
    fresh = build_plan(saved["kind"], **expected)
    if _strip(fresh["actions"]) != _strip(saved["actions"]):
        old = {json.dumps(a, sort_keys=True) for a in saved["actions"]}
        new = {json.dumps(a, sort_keys=True) for a in fresh["actions"]}
        diff = [f"- {a}" for a in sorted(old - new)] + [f"+ {a}" for a in sorted(new - old)]
        raise PlanChanged("state changed since the plan was written; nothing was done. "
                          "Re-run the dry run.\n" + "\n".join(diff))
    repo = saved["params"]["repo"]
    allowed = {_norm(it["path"]) for it in fresh["items"]}
    done = []
    for a in fresh["actions"]:
        target = a.get("path") or a.get("src")
        line = f"{a['op']} {target}" + (f" -> {a['dst']}" if "dst" in a else "")
        try:
            if _norm(target) not in allowed:  # belt and braces: plan == fresh scan already
                raise PlanChanged(f"refusing {target}: outside the scanned set")
            _recheck_use(target)
            _EXEC[a["op"]](repo, a)
        except BaseException as e:
            e.add_note(f"failed at: {line}")
            e.add_note(f"already done ({len(done)}):" + "".join(f"\n  {d}" for d in done))
            raise
        done.append(line)
        on_done(line)
    return done


def _recheck_use(target) -> None:
    """Fresh process snapshot (~0.4 s) right before a destructive action: a lane run may
    have started since the plan was rebuilt."""
    global _PROCS, _CWDS
    _PROCS = _CWDS = None
    prob = process_scan_problem()
    if prob:
        raise PlanChanged(f"refusing {target}: {prob}")
    pids = users_of(target)
    if pids:
        raise PlanChanged(f"refusing {target}: in use by pid {pids[:5]}")


def _registration(repo, path) -> dict | None:
    return next((w for w in list_worktrees(repo) if _norm(w["path"]) == _norm(path)), None)


def _checkout_unclean(path) -> str | None:
    """None when `git status` reads clean; otherwise why not (unreadable counts as unclean)."""
    r = git_rc(path, "status", "--porcelain")
    if r.returncode:
        return f"unreadable by git ({r.stderr.strip()[:200]})"
    return "dirty" if r.stdout.strip() else None


def _clear_readonly(path) -> None:
    """Make plain files writable; never enter or touch links or other reparse points."""
    stack = [str(path)]
    while stack:
        for e in list(os.scandir(stack.pop())):
            if _entry_link_kind(e):  # link or non-link reparse point (cloud, dedup)
                continue
            if e.is_dir(follow_symlinks=False):
                stack.append(e.path)
            elif not os.access(e.path, os.W_OK):
                os.chmod(e.path, stat.S_IWRITE)


def _remove_worktree(repo, a):
    path = a["path"]
    # Re-check at execution time, not just at plan time.
    entry = _registration(repo, path)
    if entry is None or "prunable" in entry:
        raise RuntimeError(f"{path}: no longer a registered worktree; refusing to delete it")
    primary = primary_branch(repo)
    if entry.get("branch") == f"refs/heads/{primary}":
        raise RuntimeError(f"{path}: primary branch {primary}; refusing to delete it")
    if a["branch"] and entry.get("branch") != f"refs/heads/{a['branch']}":
        raise RuntimeError(f"{path}: worktree branch changed; re-run plan/apply")
    if entry.get("locked") and not a["unlock"]:
        raise RuntimeError(f"{path}: locked ({entry['locked']}); refusing")
    why = _checkout_unclean(path)
    if why:
        raise RuntimeError(f"{path}: {why} at execution time; refusing")
    if a["unlock"]:
        git(repo, "worktree", "unlock", path)
    # Unlink untracked links (the .cache junctions) first so no deleter can walk into them.
    for rel, *_ in tree_stats(path)["links"]:
        if not git(path, "ls-files", "--", rel.replace("\\", "/")).strip():
            _rmtree(os.path.join(path, rel))
    _clear_readonly(path)  # Drive's read-only bits would make git stop halfway
    r = git_rc(repo, "worktree", "remove", path)
    if os.path.lexists(path):
        # Fallback for husks git left behind, only once git has let go and nothing live is left.
        entry = _registration(repo, path)
        if entry is not None and "prunable" not in entry:
            raise RuntimeError(f"git worktree remove {path}: {r.stderr.strip()}")
        if entry is not None and entry.get("locked"):
            raise RuntimeError(f"{path}: leftover is locked; finish by hand")
        if os.path.lexists(os.path.join(path, ".git")):
            why = _checkout_unclean(path)
            if why:
                raise RuntimeError(f"{path}: leftover is {why}; finish by hand")
        _rmtree(path)
    # Not `git worktree prune`: that is global and would also drop refused admin dirs.
    if a.get("admin") and os.path.isdir(a["admin"]):
        _rmtree(a["admin"])
    if a["branch"] and a["branch"] != primary:
        git(repo, "branch", "-d", a["branch"])  # -d only: git re-checks the merge itself


def _prune_admin(_repo, a):
    _rmtree(a["path"])  # exactly what `git worktree prune` does, for this one entry only


def _remove_path(_repo, a):
    _rmtree(a["path"])


def _copy_tree(src, dst, move=False):
    """Copy (or move) a tree without following or copying links; links are recreated after.
    A move copies, checks the destination holds the same files and bytes (links excluded)
    as the source did, and only then deletes the source."""
    _require_link_detection()
    before = tree_stats(src)
    _raw_copy(src, dst)
    after = tree_stats(dst)
    if (after["files"], after["size"]) != (before["files"], before["size"]):
        raise RuntimeError(f"{dst} does not match {src} after the copy ({after['files']} files/"
                           f"{after['size']} B vs {before['files']}/{before['size']}); "
                           "source kept")
    if move:
        _rmtree(src)


def _raw_copy(src, dst) -> None:
    if _IS_WIN:
        flags = ["/E", "/XJ", "/COPY:DAT", "/DCOPY:T", "/R:1", "/W:1", "/NFL", "/NDL", "/NP",
                 "/NJH", "/NJS"]
        r = subprocess.run(["robocopy", src, dst, *flags], capture_output=True, text=True)
        if r.returncode >= 8:
            raise RuntimeError(f"robocopy {src} -> {dst} failed ({r.returncode}): {r.stdout}")
    else:
        shutil.copytree(src, dst, symlinks=True, ignore=lambda d, names: [
            n for n in names if _path_is_link(os.path.join(d, n))])


def _relink(dst, links):
    """Recreate the links tree_stats recorded; only kinds this tool knows are links."""
    bad = [link for link in links if link[2] not in LINK_KINDS]
    if bad:
        raise ValueError(f"refusing to fabricate links for non-link entries: {bad[:3]}")
    for rel, target, kind, is_dir in links:
        p = os.path.join(dst, rel)
        if os.path.lexists(p):
            continue
        os.makedirs(os.path.dirname(p), exist_ok=True)
        if kind == "junction" and _IS_WIN:
            import _winapi
            _winapi.CreateJunction(target, p)
        else:
            os.symlink(target, p, target_is_directory=is_dir)


def _move_worktree_readd(repo, a):
    if a["unlock"]:
        git(repo, "worktree", "unlock", a["src"])
    src, dst = a["src"], a["dst"]
    # Not `git worktree move`: across volumes (C: -> F:) it fails with "Improper link".
    # Detached add, verify, drop the old one, then take the branch over in the new one.
    # The source is not touched until the destination verifies; any failure after the add
    # leaves things as they were.
    git(repo, "worktree", "add", "--detach", dst, a["head"])
    try:
        _relink(dst, a["links"])
        if git(dst, "rev-parse", "HEAD").strip() != a["head"] or _status(dst):
            raise RuntimeError(f"{dst}: new worktree does not match {src}; old kept")
    except BaseException:
        _drop_new_worktree(dst)
        raise
    try:
        _remove_worktree(repo, {"path": src, "unlock": False, "branch": None,
                                "admin": _admin_dir(src)})
    except BaseException:
        if _registration(repo, src):  # refused before touching it: just undo the add
            _drop_new_worktree(dst)
        raise
    try:
        if os.path.lexists(src):
            raise RuntimeError(f"{src}: old worktree dir still present after removal")
        if a["branch"]:
            git(dst, "checkout", "-q", a["branch"])  # same commit: no file changes
        current = git(dst, "branch", "--show-current").strip() or None
        if git(dst, "rev-parse", "HEAD").strip() != a["head"] or _status(dst) \
                or current != a["branch"]:
            raise RuntimeError(f"{dst}: after the move HEAD/status/branch do not match the plan")
    except BaseException:
        # dst is a verified clean copy of HEAD: drop it and put the worktree back at src.
        _drop_new_worktree(dst)
        if not os.path.lexists(src):
            target = [src, a["branch"]] if a["branch"] else ["--detach", src, a["head"]]
            git(repo, "worktree", "add", *target)
            _relink(src, a["links"])
        raise


def _drop_new_worktree(dst) -> None:
    """Undo a `worktree add` this run just made (its checkout and its admin dir)."""
    admin = _admin_dir(dst)
    if os.path.lexists(dst):
        _rmtree(dst)
    if admin and os.path.isdir(admin):
        _rmtree(admin)


@contextlib.contextmanager
def _apply_lock(root):
    """One --apply at a time per work root.  A lock left by a dead pid is taken over."""
    lock = Path(root) / "tmp" / "slink-space-apply.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(2):
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            text = lock.read_text(encoding="utf-8", errors="replace").strip()
            if not text.isdigit() or pid_alive(int(text)):
                raise PlanChanged(f"another --apply holds {lock} (pid {text or '?'}); "
                                  "delete it only if that run is gone") from None
            lock.unlink(missing_ok=True)
    else:
        raise PlanChanged(f"could not take {lock}")
    os.write(fd, str(os.getpid()).encode())
    os.close(fd)
    try:
        yield
    finally:
        lock.unlink(missing_ok=True)


def _move_worktree_copy(repo, a):
    if a["unlock"]:
        git(repo, "worktree", "unlock", a["src"])
    before = _status(a["src"])
    _copy_tree(a["src"], a["dst"])
    _relink(a["dst"], a["links"])
    git(a["dst"], "worktree", "repair")
    if _status(a["dst"]) != before:
        raise RuntimeError(f"{a['dst']}: git status differs from {a['src']}; old kept "
                           f"(run `git worktree repair` from the old path to undo)")
    _rmtree(a["src"])


def _move_dir(_repo, a):
    _copy_tree(a["src"], a["dst"])  # copies and verifies files + bytes
    _relink(a["dst"], a["links"])
    _rmtree(a["src"])  # only once the destination is complete


def _move_file(_repo, a):
    size = os.lstat(a["src"]).st_size
    os.makedirs(os.path.dirname(a["dst"]), exist_ok=True)
    shutil.copy2(a["src"], a["dst"])
    copied = os.lstat(a["dst"]).st_size
    if copied != size:
        raise RuntimeError(f"{a['dst']}: copy size {copied} B != source {size} B; source kept")
    _force(os.remove, a["src"])


_EXEC = {"remove-worktree": _remove_worktree, "prune-admin": _prune_admin,
         "remove-dir": _remove_path, "remove-file": _remove_path,
         "move-worktree-readd": _move_worktree_readd, "move-worktree-copy": _move_worktree_copy,
         "move-dir": _move_dir, "move-file": _move_file}


# ---------------------------------------------------------------- output

def _hsize(n) -> str:
    n = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


def _hage(sec) -> str:
    return f"{sec / 86400:.1f}d" if sec >= 86400 else f"{sec / 3600:.1f}h"


def _on_c(p) -> bool:
    return _norm(p).startswith("c:")


def _summary(items) -> list[str]:
    stale = [i for i in items if i["status"] == "stale" and not _prefer_move(i)]
    refused = [i for i in items if i["status"] == "refuse"]
    tot = sum(i["size"] for i in stale)
    c = sum(i["size"] for i in stale if _on_c(i["path"]))
    c_all = sum(i["size"] for i in items if _on_c(i["path"]) and i["kind"] != "admin")
    to_move = [i for i in items if i["status"] == "stale" and _prefer_move(i)]
    return [f"prune deletes: {_hsize(tot)} in {len(stale)} items (on C: {_hsize(c)}); "
            f"stale on C: (moved, not deleted): {_hsize(sum(i['size'] for i in to_move))} in "
            f"{len(to_move)} items; refused: {len(refused)}; "
            f"SLink content still on C: {_hsize(c_all)}"]


def render_report(items, root) -> str:
    now = time.time()
    out = [f"SLink space report -- work root {Path(root).as_posix()}", "", "WORK ROOT"]
    for c in CATEGORIES:
        mine = [i for i in items if i.get("label") == f"work:{c}"]
        out.append(f"  {c:<9}{_hsize(sum(i['size'] for i in mine)):>9}  {len(mine)} items")
        out += [f"      {_hsize(i['size']):>9}  {i['status']:<6} {os.path.basename(i['path'])}"
                f"  ({i['reason']})" for i in mine]
    out += ["", "WORKTREES", f"  {'STATUS':<7}{'SIZE':>9}  {'MERGED':<6} {'+A/-B':<11} "
            f"{'DIRTY t/u':<10} {'LOCK':<14} BRANCH | PATH | REASON"]
    for i in items:
        if i["kind"] != "worktree":
            continue
        ab = f"+{i.get('ahead', '?')}/-{i.get('behind', '?')}"
        dirty = f"{i.get('tracked', '?')}/{i.get('untracked', '?')}"
        lock = "-"
        if i.get("lock"):
            lock = (f"pid {i['lock_pid']} {'live' if i['lock_alive'] else 'dead'}"
                    if i.get("lock_pid") else "no-pid")
        merged = {True: "yes", False: "no"}.get(i.get("merged"), "?")
        out.append(f"  {i['status']:<7}{_hsize(i['size']):>9}  {merged:<6} {ab:<11} "
                   f"{dirty:<10} {lock:<14} {i['branch'] or '(detached)'} | {i['path']}"
                   f" | {i['reason']}")
    out += ["", "OTHER LOCATIONS", f"  {'STATUS':<7}{'SIZE':>9} {'AGE':>7}  {'KIND':<9} PATH | REASON"]
    for i in items:
        if i["kind"] == "worktree" or str(i.get("label", "")).startswith("work:"):
            continue
        age = _hage(_age(now, i["newest"])) if i.get("newest") else "-"
        out.append(f"  {i['status']:<7}{_hsize(i['size']):>9} {age:>7}  {i['kind']:<9} "
                   f"{i['path']} | {i['reason']}")
    out += ["", "SUMMARY", *(f"  {s}" for s in _summary(items))]
    return "\n".join(out)


def render_plan(plan, plan_file) -> str:
    acts = plan["actions"]
    total = sum(a["size"] for a in acts)
    c = sum(a["size"] for a in acts if _on_c(a.get("path") or a.get("src")))
    verb = "remove" if plan["kind"] == "prune" else "move"
    out = [f"DRY RUN -- {plan['kind']}: would {verb} {len(acts)} items, {_hsize(total)} "
           f"({_hsize(c)} on C:)"]
    for a in acts:
        tgt = a.get("path") or a.get("src")
        dst = f"  ->  {a['dst']}" if "dst" in a else ""
        why = f"   # {a['reason']}" if "reason" in a else ""
        out.append(f"  {a['op']:<20}{_hsize(a['size']):>9}  {tgt}{dst}{why}")
    refused = plan.get("refused") or [{"path": i["path"], "reason": i["reason"]}
                                      for i in plan["items"] if i["status"] == "refuse"]
    out += ["", f"REFUSED ({len(refused)})"] + [f"  {r['path']}  # {r['reason']}" for r in refused]
    if plan.get("followups") is not None:
        groups: dict[tuple, list] = {}
        for line in plan["followups"]:
            loc, change = line.split("  ", 1)
            path, lineno = loc.rsplit(":", 1)
            groups.setdefault((path, change), []).append(lineno)
        out += ["", f"FOLLOW-UP path references to edit ({len(plan['followups'])} lines, "
                    f"{len(groups)} file/path pairs; every line in --json)"]
        out += [f"  {path}:{','.join(nums[:5])}{' +' + str(len(nums) - 5) if len(nums) > 5 else ''}"
                f"  {change}" for (path, change), nums in sorted(groups.items())]
    out += ["", f"plan written to {plan_file}",
            f"apply (owner-run): python tools/slink_space.py {plan['kind'] if plan['kind'] == 'prune' else 'move-to-work-root'} --apply --plan \"{plan_file}\""]
    return "\n".join(out)


def _duration(text: str) -> float:
    m = re.fullmatch(r"(\d+(?:\.\d+)?)([smhd])", text.strip())
    if not m:
        raise argparse.ArgumentTypeError(f"bad duration {text!r}; use e.g. 2h or 3d")
    return float(m.group(1)) * {"s": 1, "m": 60, "h": 3600, "d": 86400}[m.group(2)]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("root", help="print the work root (or a category under it)")
    r.add_argument("category", nargs="?")
    for name in ("report", "prune", "move-to-work-root"):
        p = sub.add_parser(name)
        p.add_argument("--json", action="store_true")
        p.add_argument("--repo", default=None)
        p.add_argument("--older-than", type=_duration, default=DEFAULT_LANE_AGE,
                       help="lane/state/worktree age before it counts as stale (default 3d)")
        p.add_argument("--tmp-older-than", type=_duration, default=DEFAULT_TMP_AGE,
                       help="pytest/tool temp age before it counts as stale (default 2h)")
        if name != "report":
            p.add_argument("--apply", action="store_true", help="perform the plan (owner-run)")
            p.add_argument("--plan", default=None, help="plan file (default under tmp/)")
    args = ap.parse_args(argv)
    if args.cmd == "root":
        print(work_root(args.category).as_posix())
        return 0
    kw = {"repo": args.repo, "lane_age": args.older_than, "tmp_age": args.tmp_older_than}
    if args.cmd == "report":
        items = scan(**kw)
        print(json.dumps(items, indent=1) if args.json else render_report(items, work_root()))
        return 0
    kind = "prune" if args.cmd == "prune" else "move"
    plan_file = Path(args.plan or work_root("tmp") / f"slink-space-{kind}-plan.json")
    if args.apply:
        try:
            apply_plan(plan_file, kind=kind, **kw,
                       on_done=lambda line: print(f"done: {line}", flush=True))
        except PlanChanged as e:
            print(f"ABORTED: {e}", file=sys.stderr)
            return 2
        except Exception as e:
            notes = "\n".join(getattr(e, "__notes__", []))
            print(f"FAILED: {type(e).__name__}: {e}\n{notes}", file=sys.stderr)
            return 1
        return 0
    plan = build_plan(kind, **kw)
    plan_file.parent.mkdir(parents=True, exist_ok=True)
    plan_file.write_text(json.dumps(plan, indent=1), encoding="utf-8")
    print(json.dumps(plan, indent=1) if args.json else render_plan(plan, plan_file))
    return 0


if __name__ == "__main__":
    sys.exit(main())
