#!/usr/bin/env python3
"""Create (and verify) UPS and IPS patches by diffing two ROMs — no external tool needed.

UPS is the primary format: it has CRC32 integrity for source/target/patch and, unlike
IPS, no offset ceiling. IPS is offered as a convenience but **only addresses the low
16 MB** of a ROM (24-bit offsets), so for the 32 MB Radical Red ROM it is usable only
for changes below file offset 0xFFFFFF. Shrinking is supported only by UPS.

Every requested patch is re-applied and compared byte-for-byte with the modified
ROM before any output is written.

CLI:
    python make_ups.py create <original.gba> <modified.gba> <out_basename> [--ips]
    python make_ups.py apply  <original.gba> <patch.ups|.ips> <out.gba>
    python make_ups.py verify <original.gba> <modified.gba> <patch.ups|.ips>
"""
import argparse
import hashlib
import struct
import sys
import zlib


# ---------------------------------------------------------------- UPS VLE
def _ups_encode(n: int) -> bytes:
    out = bytearray()
    while True:
        x = n & 0x7F
        n >>= 7
        if n == 0:
            out.append(0x80 | x)
            break
        out.append(x)
        n -= 1
    return bytes(out)


def _ups_decode(data: bytes, pos: int):
    value = 0
    shift = 1
    while True:
        x = data[pos]
        pos += 1
        value += (x & 0x7F) * shift
        if x & 0x80:
            break
        shift <<= 7
        value += shift
    return value, pos


# ---------------------------------------------------------------- UPS
def ups_create(source: bytes, target: bytes) -> bytes:
    patch = bytearray(b"UPS1")
    patch += _ups_encode(len(source))
    patch += _ups_encode(len(target))
    out_size = max(len(source), len(target))
    last = 0
    i = 0
    while i < out_size:
        sb = source[i] if i < len(source) else 0
        tb = target[i] if i < len(target) else 0
        if sb != tb:
            patch += _ups_encode(i - last)
            while i < out_size:
                sb = source[i] if i < len(source) else 0
                tb = target[i] if i < len(target) else 0
                x = sb ^ tb
                patch.append(x)
                i += 1
                if x == 0:
                    break
            last = i
        else:
            i += 1
    patch += struct.pack("<I", zlib.crc32(source) & 0xFFFFFFFF)
    patch += struct.pack("<I", zlib.crc32(target) & 0xFFFFFFFF)
    patch += struct.pack("<I", zlib.crc32(bytes(patch)) & 0xFFFFFFFF)
    return bytes(patch)


def ups_apply(source: bytes, patch: bytes) -> bytes:
    if patch[:4] != b"UPS1":
        raise ValueError("not a UPS1 patch")
    # integrity: patch CRC covers everything but the trailing patch-CRC field
    want_patch_crc = struct.unpack("<I", patch[-4:])[0]
    got_patch_crc = zlib.crc32(patch[:-4]) & 0xFFFFFFFF
    if want_patch_crc != got_patch_crc:
        raise ValueError("UPS patch CRC mismatch (corrupt patch)")
    src_crc = struct.unpack("<I", patch[-12:-8])[0]
    if (zlib.crc32(source) & 0xFFFFFFFF) != src_crc:
        raise ValueError("source ROM CRC mismatch (wrong base ROM)")
    pos = 4
    src_size, pos = _ups_decode(patch, pos)
    dst_size, pos = _ups_decode(patch, pos)
    out = bytearray(dst_size)
    for k in range(dst_size):
        out[k] = source[k] if k < len(source) else 0
    body_end = len(patch) - 12
    i = 0
    while pos < body_end:
        rel, pos = _ups_decode(patch, pos)
        i += rel
        while pos < body_end:
            x = patch[pos]
            pos += 1
            if i < dst_size:
                out[i] ^= x
            i += 1
            if x == 0:
                break
    result = bytes(out)
    tgt_crc = struct.unpack("<I", patch[-8:-4])[0]
    if (zlib.crc32(result) & 0xFFFFFFFF) != tgt_crc:
        raise ValueError("target CRC mismatch after apply")
    return result


# ---------------------------------------------------------------- IPS
IPS_MAX = 0x1000000  # 24-bit offsets


def ips_create(source: bytes, target: bytes) -> bytes:
    if len(target) < len(source):
        raise ValueError("IPS cannot shrink a ROM; use UPS for this ROM")
    # changes beyond 16 MB are unrepresentable in IPS
    if len(target) > IPS_MAX and any(
        (i >= IPS_MAX) and (i >= len(source) or source[i] != target[i])
        for i in range(IPS_MAX, len(target))
    ):
        raise ValueError(
            "IPS cannot represent changes at/after 16 MB; use UPS for this ROM"
        )
    patch = bytearray(b"PATCH")
    n = max(len(source), len(target))
    i = 0
    while i < n:
        sb = source[i] if i < len(source) else 0
        tb = target[i] if i < len(target) else 0
        if sb == tb:
            i += 1
            continue
        start = i
        chunk = bytearray()
        while i < n and i < start + 0xFFFF:
            sb = source[i] if i < len(source) else 0
            tb = target[i] if i < len(target) else 0
            if sb == tb:
                # allow a short equal gap rather than splitting; stop at >3 equal
                run = 0
                k = i
                while k < n:
                    a = source[k] if k < len(source) else 0
                    b = target[k] if k < len(target) else 0
                    if a != b:
                        break
                    run += 1
                    k += 1
                if run > 5:
                    break
            chunk.append(tb)
            i += 1
        if start == 0x454F46:  # "EOF" — nudge so the offset isn't the terminator
            start -= 1
            chunk.insert(0, target[start])
        patch += struct.pack(">I", start)[1:]   # 3-byte offset
        patch += struct.pack(">H", len(chunk))
        patch += chunk
    patch += b"EOF"
    return bytes(patch)


def ips_apply(source: bytes, patch: bytes) -> bytes:
    if patch[:5] != b"PATCH":
        raise ValueError("not an IPS patch")
    out = bytearray(source)
    pos = 5
    while True:
        if patch[pos:pos + 3] == b"EOF":
            break
        offset = int.from_bytes(patch[pos:pos + 3], "big")
        pos += 3
        size = int.from_bytes(patch[pos:pos + 2], "big")
        pos += 2
        if size == 0:  # RLE record
            rle_size = int.from_bytes(patch[pos:pos + 2], "big")
            pos += 2
            value = patch[pos]
            pos += 1
            data = bytes([value]) * rle_size
        else:
            data = patch[pos:pos + size]
            pos += size
        if offset + len(data) > len(out):
            out.extend(b"\x00" * (offset + len(data) - len(out)))
        out[offset:offset + len(data)] = data
    return bytes(out)


# ---------------------------------------------------------------- CLI
def _read(p):
    with open(p, "rb") as f:
        return f.read()


def _md5(b):
    return hashlib.md5(b).hexdigest()


def cmd_create(args):
    src = _read(args.original)
    tgt = _read(args.modified)
    ups = ups_create(src, tgt)
    if ups_apply(src, ups) != tgt:
        raise ValueError("UPS round-trip mismatch!")
    ips = ips_create(src, tgt) if args.ips else None
    if ips is not None and ips_apply(src, ips) != tgt:
        raise ValueError("IPS round-trip mismatch!")
    out_ups = args.out_basename + ".ups"
    with open(out_ups, "wb") as f:
        f.write(ups)
    print(f"UPS  -> {out_ups}  ({len(ups)} bytes)  round-trip OK (md5 {_md5(tgt)})")
    if ips is not None:
        out_ips = args.out_basename + ".ips"
        with open(out_ips, "wb") as f:
            f.write(ips)
        print(f"IPS  -> {out_ips}  ({len(ips)} bytes)  round-trip OK")
    return 0


def cmd_apply(args):
    src = _read(args.original)
    patch = _read(args.patch)
    out = ups_apply(src, patch) if args.patch.endswith(".ups") else ips_apply(src, patch)
    with open(args.out, "wb") as f:
        f.write(out)
    print(f"applied -> {args.out}  (md5 {_md5(out)})")
    return 0


def cmd_verify(args):
    src = _read(args.original)
    tgt = _read(args.modified)
    patch = _read(args.patch)
    out = ups_apply(src, patch) if args.patch.endswith(".ups") else ips_apply(src, patch)
    ok = _md5(out) == _md5(tgt)
    print(f"verify: {'OK' if ok else 'FAIL'}  expected {_md5(tgt)}  got {_md5(out)}")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create")
    c.add_argument("original")
    c.add_argument("modified")
    c.add_argument("out_basename")
    c.add_argument("--ips", action="store_true")
    c.set_defaults(func=cmd_create)
    a = sub.add_parser("apply")
    a.add_argument("original")
    a.add_argument("patch")
    a.add_argument("out")
    a.set_defaults(func=cmd_apply)
    v = sub.add_parser("verify")
    v.add_argument("original")
    v.add_argument("modified")
    v.add_argument("patch")
    v.set_defaults(func=cmd_verify)
    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
