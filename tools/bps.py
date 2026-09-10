#!/usr/bin/env python3
"""
BPS patches: changed bytes only, with the checks built in.

Rather than invent a format, this is `beat`'s BPS, which is what current
romhacking tools speak -- Floating IPS, RomPatcher.js, beat itself -- so a patch
written here applies with any of them and vice versa.

It was chosen over the alternatives for what it verifies:

    IPS   the oldest and most widely supported, but records offsets and data
          and nothing else: no source check, no target check, and a 24-bit
          offset ceiling. A patch applied to the wrong ROM simply corrupts it.
    UPS   XOR-based, carries CRC32 of source, target and patch. A fine fit, but
          less well supported by current tools than BPS.
    BPS   delta-encoded, carries CRC32 of source, target and the patch itself,
          and is the format those tools default to.

Layout, all integers in the variable-length encoding below:

    "BPS1"
    source-size, target-size, metadata-size, [metadata]
    actions...
    uint32  CRC32 of the source file
    uint32  CRC32 of the patched file
    uint32  CRC32 of everything in the patch before this field

The checksums sit at the end rather than in a header because the last one
covers the patch body; the effect is the same and the format is the standard
one. Every one of the three is checked on apply.

An action is (length - 1) << 2 | kind. Only two kinds are emitted here --
SourceRead for runs that are unchanged and TargetRead for runs that are not --
which is exactly right for an editor whose every write is in place and whose
output is always the same size as its input. The other two kinds, SourceCopy
and TargetCopy, are understood when reading someone else's patch.

Usage:
  python bps.py create <original> <modified> <out.bps>
  python bps.py apply  <original> <patch.bps> <out.a78>
  python bps.py info   <patch.bps>
"""

import argparse
import os
import sys
import zlib

MAGIC = b"BPS1"
SOURCE_READ, TARGET_READ, SOURCE_COPY, TARGET_COPY = 0, 1, 2, 3


class PatchError(Exception):
    """The patch is malformed, or does not belong to this ROM."""


# ------------------------------------------------------------------ numbers
def encode_number(n):
    """BPS variable-length integer: 7 bits per byte, high bit ends it."""
    out = bytearray()
    while True:
        x = n & 0x7F
        n >>= 7
        if n == 0:
            out.append(0x80 | x)
            return bytes(out)
        out.append(x)
        n -= 1


def decode_number(data, i):
    n, shift = 0, 1
    while True:
        if i >= len(data):
            raise PatchError("patch ends inside a number")
        b = data[i]
        i += 1
        n += (b & 0x7F) * shift
        if b & 0x80:
            return n, i
        shift <<= 7
        n += shift


# ------------------------------------------------------------------- create
def create(source, target, metadata=b""):
    """Build a patch turning `source` into `target`."""
    if not isinstance(source, (bytes, bytearray)):
        raise PatchError("source must be bytes")
    out = bytearray(MAGIC)
    out += encode_number(len(source))
    out += encode_number(len(target))
    out += encode_number(len(metadata))
    out += metadata

    # Walk both, emitting one action per run of same-or-different bytes. The
    # editor only ever writes in place, so runs are short and sparse and this
    # stays far smaller than the ROM.
    i, n = 0, min(len(source), len(target))
    while i < n:
        same = source[i] == target[i]
        j = i
        while j < n and (source[j] == target[j]) == same:
            j += 1
        run = j - i
        if same:
            out += encode_number(((run - 1) << 2) | SOURCE_READ)
        else:
            out += encode_number(((run - 1) << 2) | TARGET_READ)
            out += target[i:j]
        i = j
    if len(target) > n:                      # target longer: literal tail
        run = len(target) - n
        out += encode_number(((run - 1) << 2) | TARGET_READ)
        out += target[n:]

    out += zlib.crc32(source).to_bytes(4, "little")
    out += zlib.crc32(target).to_bytes(4, "little")
    out += zlib.crc32(bytes(out)).to_bytes(4, "little")
    return bytes(out)


# -------------------------------------------------------------------- apply
def read_header(patch):
    if patch[:4] != MAGIC:
        raise PatchError("not a BPS patch (bad magic)")
    if len(patch) < 4 + 12:
        raise PatchError("patch is too short to hold its checksums")
    body = len(patch) - 12
    want = int.from_bytes(patch[body + 8:body + 12], "little")
    got = zlib.crc32(patch[:body + 8])
    if want != got:
        raise PatchError("the patch file itself is corrupt: its own checksum is "
                         "$%08X but the contents hash to $%08X" % (want, got))
    i = 4
    src_size, i = decode_number(patch, i)
    tgt_size, i = decode_number(patch, i)
    meta_size, i = decode_number(patch, i)
    meta = bytes(patch[i:i + meta_size])
    return {
        "source_size": src_size, "target_size": tgt_size,
        "metadata": meta, "actions_at": i + meta_size, "body_end": body,
        "source_crc": int.from_bytes(patch[body:body + 4], "little"),
        "target_crc": int.from_bytes(patch[body + 4:body + 8], "little"),
        "patch_crc": want,
    }


def apply(source, patch, strict=True):
    """Apply `patch` to `source`. Returns (target, [warnings])."""
    h = read_header(patch)
    warn = []

    if len(source) != h["source_size"]:
        msg = ("this patch expects a %d-byte ROM and yours is %d bytes"
               % (h["source_size"], len(source)))
        if strict:
            raise PatchError(msg)
        warn.append(msg)
    got = zlib.crc32(bytes(source))
    if got != h["source_crc"]:
        msg = ("this patch was made against a different ROM: it expects CRC32 "
               "$%08X and yours is $%08X" % (h["source_crc"], got))
        if strict:
            raise PatchError(msg)
        warn.append(msg)

    out = bytearray()
    src_off = tgt_off = 0
    i = h["actions_at"]
    while i < h["body_end"]:
        n, i = decode_number(patch, i)
        action, length = n & 3, (n >> 2) + 1
        if action == SOURCE_READ:
            out += source[len(out):len(out) + length]
        elif action == TARGET_READ:
            out += patch[i:i + length]
            i += length
        elif action in (SOURCE_COPY, TARGET_COPY):
            m, i = decode_number(patch, i)
            delta = (-1 if m & 1 else 1) * (m >> 1)
            if action == SOURCE_COPY:
                src_off += delta
                out += source[src_off:src_off + length]
                src_off += length
            else:
                tgt_off += delta
                for _ in range(length):
                    out += bytes([out[tgt_off]])
                    tgt_off += 1
    if len(out) != h["target_size"]:
        raise PatchError("patch produced %d bytes, expected %d"
                         % (len(out), h["target_size"]))
    got = zlib.crc32(bytes(out))
    if got != h["target_crc"]:
        msg = ("the patched result does not match what the patch expects: "
               "CRC32 $%08X against $%08X" % (got, h["target_crc"]))
        if strict:
            raise PatchError(msg)
        warn.append(msg)
    return bytes(out), warn


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create"); c.add_argument("original"); c.add_argument("modified"); c.add_argument("out")
    a = sub.add_parser("apply"); a.add_argument("original"); a.add_argument("patch"); a.add_argument("out")
    a.add_argument("--loose", action="store_true", help="warn instead of refusing")
    n = sub.add_parser("info"); n.add_argument("patch")
    args = ap.parse_args()

    if args.cmd == "create":
        src = open(args.original, "rb").read()
        tgt = open(args.modified, "rb").read()
        p = create(src, tgt)
        open(args.out, "wb").write(p)
        h = read_header(p)
        print("wrote %s (%d bytes) for a %d-byte ROM" % (args.out, len(p), h["source_size"]))
        print("  source CRC32 $%08X -> target $%08X" % (h["source_crc"], h["target_crc"]))
        return 0

    if args.cmd == "apply":
        src = open(args.original, "rb").read()
        p = open(args.patch, "rb").read()
        try:
            out, warn = apply(src, p, strict=not args.loose)
        except PatchError as e:
            print("refused: %s" % e)
            return 1
        for w in warn:
            print("WARNING: %s" % w)
        open(args.out, "wb").write(out)
        print("wrote %s (%d bytes)" % (args.out, len(out)))
        return 0

    h = read_header(open(args.patch, "rb").read())
    print("BPS patch")
    print("  source : %d bytes, CRC32 $%08X" % (h["source_size"], h["source_crc"]))
    print("  target : %d bytes, CRC32 $%08X" % (h["target_size"], h["target_crc"]))
    print("  patch  : CRC32 $%08X" % h["patch_crc"])
    if h["metadata"]:
        print("  notes  : %s" % h["metadata"].decode("utf-8", "replace"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
