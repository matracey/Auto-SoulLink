"""
tools/apply_bps.py — apply a BPS v1 patch (byuu's format) to a source ROM

Port of docs/purergb/probes/bps_apply.py, hardened into a reusable tool: raises
ValueError (not assert) with precise messages, and adds a verifying CLI.

BPS v1 layout: "BPS1" magic, VLQ source/target/metadata sizes, metadata bytes,
then a stream of VLQ-encoded actions (SourceRead/TargetRead/SourceCopy/TargetCopy),
trailed by three little-endian uint32 CRC32s (source, target, patch-so-far).

Usage:
    python tools/apply_bps.py SOURCE PATCH OUT [--expect-sha1 HEX]
"""
import argparse
import hashlib
import struct
import sys
import zlib

_SOURCE_READ, _TARGET_READ, _SOURCE_COPY, _TARGET_COPY = range(4)


def read_vlq(data: bytes, pos: int, end: int | None = None) -> tuple[int, int]:
    """Decode one BPS variable-length quantity starting at pos. Returns (value, new_pos)."""
    result = 0
    shift = 1
    if end is None:
        end = len(data)
    if not 0 <= pos <= end <= len(data):
        raise ValueError("invalid BPS variable-length quantity bounds")
    while True:
        if pos == end:
            raise ValueError("truncated BPS variable-length quantity")
        x = data[pos]
        pos += 1
        result += (x & 0x7F) * shift
        if x & 0x80:
            return result, pos
        shift <<= 7
        result += shift


def apply(source: bytes, patch: bytes) -> bytes:
    """Apply a BPS v1 patch to source, returning the target bytes.

    Raises ValueError on malformed headers/actions, size mismatches, or failed
    source, target, or patch CRC32 checks.
    """
    if patch[:4] != b"BPS1":
        raise ValueError(f"not a BPS patch: magic {patch[:4]!r} != b'BPS1'")
    if len(patch) < 19:
        raise ValueError(f"patch too short to hold header + CRC trailer: {len(patch)} bytes")
    src_crc, tgt_crc, patch_crc = struct.unpack("<III", patch[-12:])
    if zlib.crc32(patch[:-4]) != patch_crc:
        raise ValueError(f"patch CRC mismatch: {zlib.crc32(patch[:-4]):08x} != {patch_crc:08x}")
    end = len(patch) - 12
    pos = 4
    src_size, pos = read_vlq(patch, pos, end)
    tgt_size, pos = read_vlq(patch, pos, end)
    meta_size, pos = read_vlq(patch, pos, end)
    if pos + meta_size > end:
        raise ValueError("BPS metadata exceeds the action stream")
    pos += meta_size
    if len(source) != src_size:
        raise ValueError(f"source size mismatch: got {len(source)}, patch expects {src_size}")
    if zlib.crc32(source) != src_crc:
        raise ValueError(f"source CRC mismatch: {zlib.crc32(source):08x} != {src_crc:08x}")
    target = bytearray()
    out = src_rel = tgt_rel = 0
    while pos < end:
        data, pos = read_vlq(patch, pos, end)
        action, length = data & 3, (data >> 2) + 1
        if out + length > tgt_size:
            raise ValueError("BPS action exceeds the declared target size")
        if action == _SOURCE_READ:
            if out + length > src_size:
                raise ValueError("BPS source read exceeds the source size")
            target.extend(source[out:out + length])
        elif action == _TARGET_READ:
            if pos + length > end:
                raise ValueError("truncated BPS target literal")
            target.extend(patch[pos:pos + length])
            pos += length
        elif action == _SOURCE_COPY:
            d, pos = read_vlq(patch, pos, end)
            src_rel += (-1 if d & 1 else 1) * (d >> 1)
            if src_rel < 0 or src_rel + length > src_size:
                raise ValueError("BPS source copy is outside the source")
            target.extend(source[src_rel:src_rel + length])
            src_rel += length
        else:  # _TARGET_COPY: byte-at-a-time, may read bytes just written (RLE)
            d, pos = read_vlq(patch, pos, end)
            tgt_rel += (-1 if d & 1 else 1) * (d >> 1)
            if not 0 <= tgt_rel < out:
                raise ValueError("BPS target copy must reference produced bytes")
            for i in range(length):
                target.append(target[tgt_rel + i])
            tgt_rel += length
        out += length
    if out != tgt_size:
        raise ValueError(f"target size mismatch: produced {out}, patch expects {tgt_size}")
    if zlib.crc32(bytes(target)) != tgt_crc:
        raise ValueError(f"target CRC mismatch: {zlib.crc32(bytes(target)):08x} != {tgt_crc:08x}")
    return bytes(target)


def main() -> int:
    ap = argparse.ArgumentParser(description="Apply a BPS v1 patch to a source ROM.")
    ap.add_argument("source")
    ap.add_argument("patch")
    ap.add_argument("out")
    ap.add_argument("--expect-sha1", help="fail (exit 2) if the target sha1 doesn't match")
    args = ap.parse_args()

    with open(args.source, "rb") as f:
        source = f.read()
    with open(args.patch, "rb") as f:
        patch = f.read()
    target = apply(source, patch)

    print(f"source sha1={hashlib.sha1(source).hexdigest()} size={len(source)}")
    print(
        f"target sha1={hashlib.sha1(target).hexdigest()} "
        f"md5={hashlib.md5(target).hexdigest()} size={len(target)}"
    )
    if args.expect_sha1 and hashlib.sha1(target).hexdigest() != args.expect_sha1.lower():
        print(f"expected sha1 {args.expect_sha1} does not match", file=sys.stderr)
        return 2
    with open(args.out, "wb") as f:
        f.write(target)
    return 0


if __name__ == "__main__":
    sys.exit(main())
