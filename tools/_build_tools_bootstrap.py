"""
tools/_build_tools_bootstrap.py — Phase 10 build-tool auto-installer

Locates or downloads RGBDS (pinned), returns the directory containing
rgbasm/rgblink/rgbfix binaries. Called by tools/build_pret_syms.py before
any compilation step.

On Windows: auto-downloads the official rgbds-win64.zip to
  .cache/build-tools/rgbds-<version>/
inside the current worktree, verifies SHA-256 against the release page, and
extracts.

On Linux x86_64: installs pinned portable releases in the same cache, without sudo.
Other Unix configurations require an existing install.

Driving RGBDS directly avoids the GNU-make + w64devkit dependency entirely —
we never call `make`, only rgbasm.exe and rgblink.exe. pureRGB's Makefile
does call `make`, though (tools/build_purergb_syms.py), so this module also
knows how to fetch w64devkit — the only pinned toolchain that ships GNU make,
a C compiler, and coreutils on Windows.

Usage:
    from tools._build_tools_bootstrap import ensure_rgbds, ensure_w64devkit
    rgbds_bin = ensure_rgbds()             # v1.0.1 (default, existing callers)
    rgbds_bin = ensure_rgbds("v1.0.3")     # a different pinned version
    devkit_bin = ensure_w64devkit()        # 2.10.0, dir with make.exe etc.
    jdk_bin = ensure_jdk()                 # Temurin JDK 17 (javac + jar), dir with javac.exe

Env overrides (skip download, use an existing install as-is):
    SLINK_RGBDS_BIN      — dir already containing rgbasm/rgblink/rgbfix
    SLINK_W64DEVKIT_BIN  — dir already containing make/busybox (or sh)
    SLINK_JDK_BIN        — dir already containing javac + jar

Windows RGBDS/w64devkit paths with spaces are copied intact to the space-free
$SLINK_WORK_ROOT/cache/build-tools cache; the original install is never changed.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile

# Pinned RGBDS releases — add a new entry here to add a version; the
# `RGBDS_VERSION` default stays v1.0.1 so existing callers are unaffected.
RGBDS_VERSION = "v1.0.1"
RGBDS_PINS: dict[str, dict] = {
    "v1.0.1": {
        "url": "https://github.com/gbdev/rgbds/releases/download/v1.0.1/rgbds-win64.zip",
        "sha256": "554187d717cca78136a81d167107ea15742e7f622797d0b339c0bfb7ab749097",
        "size": 559_001,
    },
    "v1.0.3": {
        "url": "https://github.com/gbdev/rgbds/releases/download/v1.0.3/rgbds-win64.zip",
        "sha256": "b66c23cb6d073dd3866ea30ef1ca5164549e0dae9ebe771957aff25e2658b0e3",
        "size": 1_104_362,
        "linux_x86_64": {
            "url": "https://github.com/gbdev/rgbds/releases/download/v1.0.3/rgbds-linux-x86_64.tar.xz",
            "sha256": "280a52061a0c516999bee75ac357628d6d50784309e0486cef25f7460e6f330b",
            "size": 1_403_152,
        },
    },
}

# Pinned w64devkit release (a 7z self-extracting .exe; binaries land under a
# nested `w64devkit/bin/` once extracted — verified against the real SFX).
W64DEVKIT_VERSION = "2.10.0"
W64DEVKIT_URL = "https://github.com/skeeto/w64devkit/releases/download/v2.10.0/w64devkit-x64-2.10.0.7z.exe"
W64DEVKIT_SHA256 = "18d0a4c71a166f8401ab6305781bec5882b40b5e06ba9807c61cb5f3b3c6325e"
W64DEVKIT_SIZE = 67_127_496

# Pinned Eclipse Temurin JDK 17 (portable zip; the UPR ZX fork needs javac + jar,
# the machine only ships a Java 8 JRE). Resolved through the Adoptium API
# (`/v3/binary/latest/17/ga/windows/x64/jdk/hotspot/normal/eclipse?project=jdk`)
# on 2026-09-18; the sha256 is the one the API published AND the one observed
# on the downloaded file. The zip nests everything under `jdk-17.0.20.1+1/`.
JDK_VERSION = "17.0.20.1+1"
JDK_URL = ("https://github.com/adoptium/temurin17-binaries/releases/download/"
           "jdk-17.0.20.1%2B1/OpenJDK17U-jdk_x64_windows_hotspot_17.0.20.1_1.zip")
JDK_SHA256 = "e53a79c3c3d86865bd7e787903884331068e71321714ffd44f145785affc7cb0"
JDK_SIZE = 190_817_615

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
DOWNLOAD_CACHE = REPO_ROOT / ".cache" / "downloads"

REQUIRED_BINARIES = ("rgbasm", "rgblink", "rgbfix")
REQUIRED_DEVKIT_BINARIES = ("make",)


def _binary_name(name: str) -> str:
    return f"{name}.exe" if platform.system() == "Windows" else name


def _which_in_dir(dir_path: pathlib.Path, name: str) -> pathlib.Path | None:
    candidate = dir_path / _binary_name(name)
    return candidate if candidate.exists() else None


def _check_existing_path(binary: str) -> pathlib.Path | None:
    """Return path to `binary` on PATH if found and executable, else None."""
    found = shutil.which(binary)
    return pathlib.Path(found) if found else None


def _tool_files(root: pathlib.Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): _sha256(p)
            for p in sorted(root.rglob("*")) if p.is_file()}


def space_free_toolchain(bin_dir: pathlib.Path, name: str) -> pathlib.Path:
    """Copy a spaced Windows install once, including sibling GCC libraries, without changing its source."""
    if platform.system() != "Windows":
        return bin_dir
    resolved = bin_dir.resolve()
    if " " not in str(resolved):
        return resolved
    if __package__:
        from .slink_space import work_root
    else:
        from slink_space import work_root

    cache = (work_root("cache") / "build-tools").resolve()
    if " " in str(cache):
        raise RuntimeError(f"SLINK_WORK_ROOT must resolve to a space-free path for make toolchains: {cache}")
    source = resolved.parent if resolved.name.casefold() == "bin" else resolved
    files = _tool_files(source)
    digest = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()
    target = (cache / f"{name}-{digest[:20]}").resolve()
    if not target.is_relative_to(cache) or " " in str(target):
        raise RuntimeError(f"space-free toolchain cache escapes {cache}: {target}")
    if not target.exists():
        cache.mkdir(parents=True, exist_ok=True)
        print(f"[bootstrap] Copying spaced toolchain {source} -> {target}", file=sys.stderr)
        with tempfile.TemporaryDirectory(prefix="toolchain-", dir=cache) as temporary:
            staged = pathlib.Path(temporary) / "install"
            shutil.copytree(source, staged)
            if _tool_files(staged) != files:
                raise RuntimeError(f"toolchain changed while copying {source}; refusing the partial copy")
            try:
                staged.rename(target)
            except OSError:
                if not target.is_dir():
                    raise
                # Another installer may have published the same copy; verify it below.
    if _tool_files(target) != files:
        raise RuntimeError(f"space-free toolchain cache is incomplete or changed: {target}")
    return target / resolved.relative_to(source)


def _verify_rgbds_version(rgbasm: pathlib.Path, version: str) -> bool:
    """Return True if `rgbasm --version` reports `version` (e.g. 'v1.0.3').
    The expected output is 'rgbasm v1.0.3\\n' or similar."""
    try:
        result = subprocess.run(
            [str(rgbasm), "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False
    output = (result.stdout or "") + (result.stderr or "")
    return version in output


def _download_and_verify(url: str, expected_sha256: str, expected_size: int) -> pathlib.Path:
    """Download `url` into DOWNLOAD_CACHE, verify SHA-256, return path to file.
    If already present and hash matches, skip the download."""
    DOWNLOAD_CACHE.mkdir(parents=True, exist_ok=True)
    filename = url.rsplit("/", 1)[-1]
    target = DOWNLOAD_CACHE / filename

    if target.exists() and target.stat().st_size == expected_size:
        if _sha256(target) == expected_sha256:
            return target
        target.unlink()  # corrupt cache, re-download

    print(f"[bootstrap] Downloading {url} ({expected_size} bytes)...", file=sys.stderr)
    with urllib.request.urlopen(url) as resp, open(target, "wb") as out:
        shutil.copyfileobj(resp, out)

    actual_size = target.stat().st_size
    if actual_size != expected_size:
        target.unlink()
        raise RuntimeError(
            f"Download size mismatch: got {actual_size} bytes, expected {expected_size}. "
            f"URL: {url}"
        )

    actual_hash = _sha256(target)
    if actual_hash != expected_sha256:
        target.unlink()
        raise RuntimeError(
            f"SHA-256 mismatch on {filename}: "
            f"got {actual_hash}, expected {expected_sha256}. "
            f"Either the release was retagged or the download is corrupt."
        )
    print(f"[bootstrap] Verified SHA-256 of {filename}", file=sys.stderr)
    return target


def _sha256(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _install_rgbds_windows(version: str) -> pathlib.Path:
    """Download + extract the pinned RGBDS win64 zip. Return the bin dir."""
    pin = RGBDS_PINS[version]
    cache_dir = REPO_ROOT / ".cache" / "build-tools" / f"rgbds-{version}"
    cache_dir.mkdir(parents=True, exist_ok=True)

    # Skip if already extracted and binaries exist (the zip puts them under bin/; re-extracting over binaries another build is
    # running raced with it: "PermissionError ... bin\rgbgfx.exe")
    for installed in (cache_dir / "bin", cache_dir):
        if all((installed / _binary_name(b)).exists() for b in REQUIRED_BINARIES):
            return installed

    zip_path = _download_and_verify(pin["url"], pin["sha256"], pin["size"])

    print(f"[bootstrap] Extracting RGBDS {version} to {cache_dir}", file=sys.stderr)
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(cache_dir)

    # Binaries live under bin/ inside the zip.
    bin_dir = cache_dir / "bin" if (cache_dir / "bin" / _binary_name("rgbasm")).exists() else cache_dir
    missing = [b for b in REQUIRED_BINARIES if not (bin_dir / _binary_name(b)).exists()]
    if missing:
        raise RuntimeError(
            f"RGBDS extraction did not produce expected binaries: {missing}. "
            f"Contents of {cache_dir}: {[p.name for p in cache_dir.iterdir()]}"
        )

    return bin_dir


def _install_rgbds_linux(version: str) -> pathlib.Path:
    pin = RGBDS_PINS[version]["linux_x86_64"]
    archive = _download_and_verify(pin["url"], pin["sha256"], pin["size"])
    bin_dir = REPO_ROOT / ".cache" / "build-tools" / f"rgbds-{version}" / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:xz") as tf:
        for name in (*REQUIRED_BINARIES, "rgbgfx"):
            member = tf.getmember(name)
            if not member.isfile():
                raise RuntimeError(f"RGBDS archive member {name} is not a regular file")
            with tf.extractfile(member) as source, (bin_dir / name).open("wb") as target:
                shutil.copyfileobj(source, target)
            (bin_dir / name).chmod(0o755)
    if not _verify_rgbds_version(bin_dir / "rgbasm", version):
        raise RuntimeError(f"Installed RGBDS {version} cannot run on this Linux host")
    return bin_dir


def _install_instructions_unix(version: str) -> str:
    system = platform.system()
    if system == "Darwin":
        return f"  brew install rgbds   # installs RGBDS (current Homebrew version may differ from {version})"
    return (
        "  # Debian/Ubuntu:\n"
        "  sudo apt install rgbds\n"
        "  # Arch:\n"
        "  sudo pacman -S rgbds\n"
        "  # Or build from source: https://github.com/gbdev/rgbds/releases/tag/" + version
    )


def ensure_rgbds(version: str = RGBDS_VERSION) -> pathlib.Path:
    """Locate or install RGBDS `version` (must be a key of RGBDS_PINS).
    Return the directory containing the binaries.

    Resolution order:
      1. SLINK_RGBDS_BIN env var — an existing dir with the binaries, trusted as-is.
      2. .cache/build-tools/rgbds-<version>/ inside the worktree (auto-installed).
      3. RGBDS binaries on system PATH (accepted if the version matches).
      4. On Windows or supported Linux x86_64 releases: install to .cache/build-tools/.
      5. Otherwise: print install instructions and exit.

    Raises RuntimeError if RGBDS cannot be located after attempting install.
    """
    if version not in RGBDS_PINS:
        raise RuntimeError(f"No pin for RGBDS {version}. Known: {sorted(RGBDS_PINS)}")

    override = os.environ.get("SLINK_RGBDS_BIN")
    if override:
        override_dir = pathlib.Path(override)
        if all((override_dir / _binary_name(b)).exists() for b in REQUIRED_BINARIES):
            return space_free_toolchain(override_dir, f"rgbds-{version}")
        raise RuntimeError(f"SLINK_RGBDS_BIN={override} is missing {REQUIRED_BINARIES}")

    cache_dir = REPO_ROOT / ".cache" / "build-tools" / f"rgbds-{version}"
    for installed in (cache_dir / "bin", cache_dir):
        if all((installed / _binary_name(b)).exists() for b in REQUIRED_BINARIES):
            if _verify_rgbds_version(installed / _binary_name("rgbasm"), version):
                return space_free_toolchain(installed, f"rgbds-{version}")
            # Cache exists but wrong version — refresh
            shutil.rmtree(cache_dir)
            break

    rgbasm_on_path = _check_existing_path("rgbasm")
    if rgbasm_on_path and _verify_rgbds_version(rgbasm_on_path, version):
        print(
            f"[bootstrap] Using system RGBDS at {rgbasm_on_path.parent}",
            file=sys.stderr,
        )
        return space_free_toolchain(rgbasm_on_path.parent, f"rgbds-{version}")

    if platform.system() == "Windows":
        return space_free_toolchain(_install_rgbds_windows(version), f"rgbds-{version}")
    if (platform.system() == "Linux" and platform.machine() == "x86_64"
            and "linux_x86_64" in RGBDS_PINS[version]):
        return _install_rgbds_linux(version)

    if rgbasm_on_path:
        actual_version = subprocess.run(
            [str(rgbasm_on_path), "--version"],
            capture_output=True, text=True,
        ).stdout.strip()
        raise RuntimeError(
            f"Found RGBDS at {rgbasm_on_path} but version mismatch.\n"
            f"  Expected: {version}\n"
            f"  Got:      {actual_version}\n"
            f"Install the pinned version:\n{_install_instructions_unix(version)}"
        )

    raise RuntimeError(
        f"RGBDS {version} not found on PATH. Install manually:\n"
        f"{_install_instructions_unix(version)}\n"
        f"After install, re-run."
    )


def _find_devkit_bin(root: pathlib.Path) -> pathlib.Path | None:
    """Search `root` for a directory containing make.exe (the SFX extracts a
    nested w64devkit/bin/, but this tolerates a flatter layout too)."""
    for candidate in (root, root / "w64devkit" / "bin", root / "bin"):
        if (candidate / _binary_name("make")).exists():
            return candidate
    for make_path in root.rglob(_binary_name("make")):
        return make_path.parent
    return None


def _verify_devkit(bin_dir: pathlib.Path) -> bool:
    """`make --version` must actually run — the giveaway that a stub bin/
    (binaries present but no busybox/sh support DLLs) is unusable."""
    make_exe = bin_dir / _binary_name("make")
    try:
        result = subprocess.run(
            [str(make_exe), "--version"], capture_output=True, text=True, timeout=5,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False
    return result.returncode == 0 and "GNU Make" in (result.stdout or "")


def _install_w64devkit_windows() -> pathlib.Path:
    """Download + self-extract the pinned w64devkit SFX. Return the bin dir."""
    cache_dir = REPO_ROOT / ".cache" / "build-tools" / f"w64devkit-{W64DEVKIT_VERSION}"

    existing = _find_devkit_bin(cache_dir) if cache_dir.exists() else None
    if existing and _verify_devkit(existing):
        return existing

    sfx_path = _download_and_verify(W64DEVKIT_URL, W64DEVKIT_SHA256, W64DEVKIT_SIZE)
    cache_dir.mkdir(parents=True, exist_ok=True)

    print(f"[bootstrap] Extracting w64devkit {W64DEVKIT_VERSION} to {cache_dir}", file=sys.stderr)
    seven_zip = shutil.which("7z")
    if seven_zip:
        _run_quiet([seven_zip, "x", str(sfx_path), f"-o{cache_dir}", "-y"])
    if not (seven_zip and _find_devkit_bin(cache_dir)):
        # Either no 7-Zip on PATH, or this 7-Zip build can't parse the SFX
        # payload (seen with old bundled 7-Zip versions) — the SFX is a
        # normal executable and self-extracts when simply run.
        _run_quiet([str(sfx_path), "-y", f"-o{cache_dir}"])

    bin_dir = _find_devkit_bin(cache_dir)
    if not bin_dir or not _verify_devkit(bin_dir):
        raise RuntimeError(
            f"w64devkit extraction did not produce a working make.exe under {cache_dir}"
        )
    return bin_dir


def _run_quiet(cmd: list[str]) -> None:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[bootstrap] command failed: {' '.join(cmd)}", file=sys.stderr)
        if result.stdout:
            print(result.stdout, file=sys.stderr)
        if result.stderr:
            print(result.stderr, file=sys.stderr)


def ensure_w64devkit() -> pathlib.Path:
    """Locate or install w64devkit 2.10.0. Return the dir with make.exe etc.

    Resolution order:
      1. SLINK_W64DEVKIT_BIN env var — an existing dir with make, trusted as-is.
      2. .cache/build-tools/w64devkit-2.10.0/ inside the worktree (auto-installed).
      3. On Windows only: auto-download + self-extract to .cache/build-tools/.
      4. On macOS / Linux: raise (w64devkit is Windows-only; use system make/gcc).
    """
    override = os.environ.get("SLINK_W64DEVKIT_BIN")
    if override:
        override_dir = pathlib.Path(override)
        if (override_dir / _binary_name("make")).exists() and _verify_devkit(override_dir):
            return space_free_toolchain(override_dir, f"w64devkit-{W64DEVKIT_VERSION}")
        raise RuntimeError(f"SLINK_W64DEVKIT_BIN={override} has no working make")

    if platform.system() != "Windows":
        raise RuntimeError(
            "w64devkit is a Windows-only bundle. Install GNU make + gcc via your "
            "system package manager and set SLINK_W64DEVKIT_BIN to its bin dir."
        )

    return space_free_toolchain(_install_w64devkit_windows(), f"w64devkit-{W64DEVKIT_VERSION}")


def _find_jdk_bin(root: pathlib.Path) -> pathlib.Path | None:
    for candidate in (root, root / "bin", *(p / "bin" for p in root.glob("jdk-*"))):
        if (candidate / _binary_name("javac")).exists():
            return candidate
    return None


def ensure_jdk() -> pathlib.Path:
    """Locate or install the pinned Temurin JDK 17. Return the dir with javac + jar.

    Resolution order:
      1. SLINK_JDK_BIN env var — an existing dir with javac, trusted as-is.
      2. .cache/build-tools/jdk-17/ inside the worktree (auto-installed).
      3. On Windows only: auto-download + extract to .cache/build-tools/jdk-17/.
      4. On macOS / Linux: raise (install a JDK 17 and set SLINK_JDK_BIN).
    """
    override = os.environ.get("SLINK_JDK_BIN")
    if override:
        override_dir = pathlib.Path(override)
        if (override_dir / _binary_name("javac")).exists():
            return override_dir
        raise RuntimeError(f"SLINK_JDK_BIN={override} has no javac")

    cache_dir = REPO_ROOT / ".cache" / "build-tools" / "jdk-17"
    existing = _find_jdk_bin(cache_dir) if cache_dir.exists() else None
    if existing:
        return existing

    if platform.system() != "Windows":
        raise RuntimeError(
            "The pinned JDK zip is Windows x64 only. Install a JDK 17 and set "
            "SLINK_JDK_BIN to its bin dir."
        )

    zip_path = _download_and_verify(JDK_URL, JDK_SHA256, JDK_SIZE)
    cache_dir.mkdir(parents=True, exist_ok=True)
    print(f"[bootstrap] Extracting JDK {JDK_VERSION} to {cache_dir}", file=sys.stderr)
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(cache_dir)
    bin_dir = _find_jdk_bin(cache_dir)
    if not bin_dir:
        raise RuntimeError(f"JDK extraction did not produce javac under {cache_dir}")
    return bin_dir


def main() -> int:
    """CLI entry point: print the RGBDS binary directory and exit code."""
    try:
        binary_dir = ensure_rgbds()
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(binary_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
