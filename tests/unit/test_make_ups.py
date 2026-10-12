import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from patch.tools import make_ups


def create_args(tmp_path, source=b"\x01\x02\x03", target=b"\x01\x02", ips=True):
    original, modified = tmp_path / "original.gba", tmp_path / "modified.gba"
    original.write_bytes(source)
    modified.write_bytes(target)
    return SimpleNamespace(original=str(original), modified=str(modified),
                           out_basename=str(tmp_path / "patch"), ips=ips)


@pytest.mark.parametrize("target", [b"", b"\x01\x02"])
def test_ips_refuses_shrinking_instead_of_zero_padding(target):
    with pytest.raises(ValueError, match="shrink"):
        make_ups.ips_create(b"\x01\x02\x03", target)


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("optimized", [False, True])
def test_cli_rejects_shrinking_ips_without_publishing_any_output(tmp_path, existing, optimized):
    args = create_args(tmp_path)
    outputs = [tmp_path / "patch.ups", tmp_path / "patch.ips"]
    if existing:
        for output in outputs:
            output.write_bytes(b"previous patch")
    result = subprocess.run(
        [sys.executable, *(["-O"] if optimized else []), "-m", "patch.tools.make_ups",
         "create", args.original, args.modified, args.out_basename, "--ips"],
        cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True,
        timeout=10, check=False)
    assert result.returncode != 0
    assert "shrink" in result.stderr
    assert "round-trip OK" not in result.stdout
    for output in outputs:
        if existing:
            assert output.read_bytes() == b"previous patch"
        else:
            assert not output.exists()


@pytest.mark.parametrize("format_name", ["ups", "ips"])
def test_roundtrip_failure_preserves_both_existing_outputs(tmp_path, monkeypatch, format_name):
    args = create_args(tmp_path, target=b"\x01\x02\x04")
    outputs = [tmp_path / "patch.ups", tmp_path / "patch.ips"]
    for output in outputs:
        output.write_bytes(b"previous patch")
    monkeypatch.setattr(make_ups, f"{format_name}_apply", lambda *_: b"wrong")
    with pytest.raises(ValueError, match="round-trip mismatch"):
        make_ups.cmd_create(args)
    assert all(output.read_bytes() == b"previous patch" for output in outputs)


@pytest.mark.parametrize("target", [b"\x01\x02\x03", b"\x01\x02\x04", b"\x01\x02\x03\x04"])
def test_same_size_and_growing_patches_roundtrip(tmp_path, target):
    args = create_args(tmp_path, target=target)
    assert make_ups.cmd_create(args) == 0
    for format_name in ("ups", "ips"):
        patch = (tmp_path / f"patch.{format_name}").read_bytes()
        assert getattr(make_ups, f"{format_name}_apply")(b"\x01\x02\x03", patch) == target


def test_ups_only_still_supports_shrinking(tmp_path):
    args = create_args(tmp_path, ips=False)
    assert make_ups.cmd_create(args) == 0
    assert make_ups.ups_apply(b"\x01\x02\x03", (tmp_path / "patch.ups").read_bytes()) == b"\x01\x02"
    assert not (tmp_path / "patch.ips").exists()
