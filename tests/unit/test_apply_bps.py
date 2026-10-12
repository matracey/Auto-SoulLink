"""Unit tests for tools/apply_bps.py — synthetic BPS only, no ROMs, no network."""
import hashlib
import struct
import sys
import zlib
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.apply_bps import apply, main  # noqa: E402


def _vlq(n: int) -> bytes:
    out = bytearray()
    while True:
        x = n & 0x7F
        n >>= 7
        if n == 0:
            out.append(x | 0x80)
            return bytes(out)
        out.append(x)
        n -= 1


def _action(action: int, length: int) -> bytes:
    return _vlq(((length - 1) << 2) | action)


def _signed_vlq(delta: int) -> bytes:
    return _vlq((abs(delta) << 1) | (1 if delta < 0 else 0))


def _build_bps(source: bytes, target: bytes, body: bytes) -> bytes:
    header = b"BPS1" + _vlq(len(source)) + _vlq(len(target)) + _vlq(0)
    payload = header + body
    src_tgt_crc = struct.pack(
        "<II", zlib.crc32(source) & 0xFFFFFFFF, zlib.crc32(target) & 0xFFFFFFFF
    )
    # The trailing patch CRC covers everything before it, i.e. payload + src/target CRCs.
    patch_crc = zlib.crc32(payload + src_tgt_crc) & 0xFFFFFFFF
    return payload + src_tgt_crc + struct.pack("<I", patch_crc)


def _make_fixture():
    """64-byte source -> target built with all four BPS actions."""
    source = bytes(range(64))
    # SourceRead(8):  target[0:8]  = source[0:8]
    # TargetRead(4):  target[8:12] = literal b"1234"
    # SourceCopy(6):  target[12:18] = source[20:26]                    (out now 18)
    # TargetCopy(5):  self-overlapping backref to out-1 (index 17) -- the classic
    #                 BPS RLE trick: each byte read was itself just written, so the
    #                 byte at index 17 (source[25]) repeats 5 times.
    rle_byte = source[25]
    target = source[0:8] + b"1234" + source[20:26] + bytes([rle_byte]) * 5
    body = (
        _action(0, 8)
        + _action(1, 4)
        + b"1234"
        + _action(2, 6)
        + _signed_vlq(20)
        + _action(3, 5)
        + _signed_vlq(17)  # tgt_rel: 0 -> 17 == out(18) - 1
    )
    return source, target, _build_bps(source, target, body)


def test_apply_reproduces_target():
    source, target, patch = _make_fixture()
    assert apply(source, patch) == target


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("expected", [None, "correct", "uppercase", "wrong"])
def test_cli_verifies_before_publishing(tmp_path, monkeypatch, existing, expected):
    source, target, patch = _make_fixture()
    source_path, patch_path, output = (tmp_path / name for name in ("source", "patch", "out"))
    source_path.write_bytes(source)
    patch_path.write_bytes(patch)
    if existing:
        output.write_bytes(b"trusted output")
    argv = ["apply_bps", str(source_path), str(patch_path), str(output)]
    if expected:
        digest = hashlib.sha1(target).hexdigest()
        argv += ["--expect-sha1", "0" * 40 if expected == "wrong" else
                 digest.upper() if expected == "uppercase" else digest]
    monkeypatch.setattr(sys, "argv", argv)
    assert main() == (2 if expected == "wrong" else 0)
    if expected == "wrong":
        assert output.exists() == existing
        if existing:
            assert output.read_bytes() == b"trusted output"
    else:
        assert output.read_bytes() == target


def test_bad_magic():
    source, _, patch = _make_fixture()
    bad = b"XXXX" + patch[4:]
    with pytest.raises(ValueError, match="not a BPS patch"):
        apply(source, bad)


def test_source_size_mismatch():
    source, _, patch = _make_fixture()
    with pytest.raises(ValueError, match="source size mismatch"):
        apply(source + b"\x00", patch)


def test_source_crc_mismatch():
    source, _, patch = _make_fixture()
    tampered = bytearray(source)
    tampered[0] ^= 0xFF
    with pytest.raises(ValueError, match="source CRC mismatch"):
        apply(bytes(tampered), patch)


def test_patch_crc_mismatch():
    source, _, patch = _make_fixture()
    # Flip a body byte (not the trailer) so the patch-so-far CRC no longer matches.
    tampered = bytearray(patch)
    tampered[5] ^= 0xFF
    with pytest.raises(ValueError, match="patch CRC mismatch"):
        apply(source, bytes(tampered))


def test_target_crc_mismatch():
    source, target, patch = _make_fixture()
    # Corrupt the target-size VLQ won't work cleanly, so instead corrupt the
    # TargetRead literal bytes: this changes the produced target but recomputing
    # the patch CRC would also change, so directly patch the trailer's target CRC
    # to a wrong value while leaving the body (and thus patch CRC) untouched.
    header_len = 4 + len(_vlq(len(source))) + len(_vlq(len(target))) + len(_vlq(0))
    tampered = bytearray(patch)
    # corrupt a TargetRead literal byte inside the body (after header, before trailer)
    tampered[header_len + 3] ^= 0xFF
    # recompute the patch CRC (covers payload + src/target CRCs) so only target CRC fails
    tampered[-4:] = struct.pack("<I", zlib.crc32(bytes(tampered[:-4])) & 0xFFFFFFFF)
    with pytest.raises(ValueError, match="target CRC mismatch"):
        apply(source, bytes(tampered))
