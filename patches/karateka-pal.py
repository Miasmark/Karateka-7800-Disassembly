#!/usr/bin/env python3
"""Build the PAL patch bundle by translating the NTSC fixes, not rewriting them.

The PAL release is the same game in a different box -- see "The PAL release
is the same game in a different box" in docs/karateka-map.md. Four 16K
banks instead of a linear 48K, in a different order, with in-bank offsets
unchanged and every byte of game logic identical. So a fix does not have
to be re-derived for PAL; its addresses have to be moved to the right
bank, and that is arithmetic:

    NTSC $4000-$7FFF  ->  PAL bank 2, file offset + $8000
    NTSC $8000-$BFFF  ->  PAL bank 0, file offset - $4000
    NTSC $C000-$FFFF  ->  PAL bank 3, file offset + $4000

This applies each fix to the NTSC image exactly as `karateka.py` builds
it, then translates the bytes it wrote. Nothing here re-implements a fix,
so nothing here can drift from one.

## What ports and what does not

Every fix is checked byte by byte against the PAL image before it is
accepted, and they fall into three groups:

  - **identical** -- every byte the fix expects is the byte PAL has.
  - **free-space fill** -- the only disagreement is `$00` against `$FF`,
    because NTSC pads its free pool with zeroes and PAL with erased
    EPROM. The fix is claiming empty space either way, so it ports.
  - **blocked** -- the fix writes over code PAL actually changed.

Twenty fixes are blocked, and all twenty for the same reason: fix 1's
seven-byte hook at `$5945` sits inside `$58EA`-`$5956`, which is the 50 Hz
retime of the NMI handler and the one piece of logic Europe rewrote.

    NTSC $5945  A0 10 88 D0 FD EA EA     a delay loop the latch replaces
    PAL  $5945  01 85 A8 4C 57 59 00     different code entirely

Everything that carries the input latch inherits that -- which is the
cadence work, the control remap's decoder half, and the `tweak` builds.
Porting them means finding where the PAL handler has room for the same
hook, which is a reverse-engineering job on that handler and not a
translation. It is deliberately not guessed at here.

## Signing

PAL cartridges carry no signature -- the block at `$FF80`-`$FFF7` in the
retail dump is `$FF` end to end, because no European console checks. So
nothing signs anything here, and `sign7800.py` will tell you the same if
you ask it.
"""
import io
import os
import sys
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import bps                              # noqa: E402
import patchset                         # noqa: E402
import karateka as K                    # noqa: E402

PAL_NAME = "Karateka (PAL) (Atari) (1987) (6C8D9E68).a78"
OUTDIR = K.OUTDIR

# the fixes worth offering, in the order they should be read
WANTED = [5, 6, 11, 14, 15, 21, 22, 26, 39, 40, 41, 43, 44, 48]

# and a PAL-only composite, since the NTSC `tweak` build cannot port: it
# carries the loop gate, which carries the input latch, which is blocked
PAL_TWEAK = {
    "id": "pal-tweak", "title": "the unofficial build, as far as PAL can go",
    "parts": [44, 11, 41, 26, 48],
    "note": "Knockback that spends the hall's travel budget, the control "
            "remap, the princess's kick, 3-1-1 strikes and a working "
            "difficulty switch. No cadence work: that needs the input "
            "latch, and the latch's hook site is the one thing PAL "
            "rewrote.",
}


def pal_offset(addr):
    """A CPU address in the NTSC image -> a file offset in the PAL image."""
    o = addr - 0x4000
    if o < 0x4000:
        return o + 0x8000
    if o < 0x8000:
        return o - 0x4000
    return o + 0x4000


def find_pal():
    for path in (os.environ.get("KARATEKA_PAL_ROM", ""),
                 os.path.join(ROOT, "..", "karateka", PAL_NAME)):
        if path and os.path.exists(path):
            return path
    found = K._hunt(PAL_NAME, os.path.join(ROOT, ".."))
    if found:
        return found
    raise SystemExit(
        "could not find the PAL dump. This project ships no ROM: supply\n"
        "your own %s and set KARATEKA_PAL_ROM to it." % PAL_NAME)


def classify(writes, ntsc, pal):
    """'identical', 'fill' or a description of what blocks the fix."""
    kinds = set()
    where = None
    for addr, data in writes:
        for i in range(len(data)):
            a, b = ntsc[addr - 0x4000 + i], pal[pal_offset(addr + i)]
            if a != b:
                kinds.add((a, b))
                if where is None and (a, b) != (0x00, 0xFF):
                    where = addr + i
    if not kinds:
        return "identical", None
    if kinds <= {(0x00, 0xFF)}:
        return "fill", None
    return "blocked", where


def main():
    _src, header, ntsc = K.load_source()
    pal_path = find_pal()
    raw = io.open(pal_path, "rb").read()
    pal_hdr, pal = raw[:K.HDR], raw[K.HDR:]
    print("PAL source: %s\n  %d bytes, %d banks of 16K\n"
          % (os.path.basename(pal_path), len(pal), len(pal) // 0x4000))

    by_n = {f["n"]: f for f in K.FIXES}
    sections, files, options, taken = {}, {}, [], []

    def emit(oid, title, note, writes):
        touched = {}
        for addr, data in writes:
            at = pal_offset(addr)
            sid = "s_%05X_%d" % (at, len(data))
            sections[sid] = {
                "addr": at, "length": len(data), "what": title,
                "crc32": "0x%08X" % (zlib.crc32(pal[at:at + len(data)])
                                     & 0xFFFFFFFF),
            }
            member = "p/%s.%s.bps" % (oid, sid)
            files[member] = bps.create(pal[at:at + len(data)], bytes(data))
            touched[sid] = {"bps": member}
        options.append({"id": oid, "title": title, "note": note,
                        "patches": touched})

    for n in WANTED:
        fix = by_n[n]
        p = K.Patcher(ntsc)
        fix["apply"](p)
        state, where = classify(p.writes, ntsc, pal)
        if state == "blocked":
            print("  fix %-3d %-34s BLOCKED at $%04X" % (n, fix["slug"], where))
            continue
        emit(fix["slug"], fix["title"], fix.get("measured", "")[:400],
             p.writes)
        taken.append(n)
        print("  fix %-3d %-34s %s" % (n, fix["slug"],
                                       "identical" if state == "identical"
                                       else "ports (free space is $FF here)"))

    # the composite, built the same way from the same appliers
    p = K.Patcher(ntsc)
    for n in PAL_TWEAK["parts"]:
        by_n[n]["apply"](p)
    state, where = classify(p.writes, ntsc, pal)
    if state != "blocked":
        emit(PAL_TWEAK["id"], PAL_TWEAK["title"], PAL_TWEAK["note"], p.writes)
        print("  %-38s %s" % (PAL_TWEAK["id"], "built from fixes %s"
                              % ", ".join(str(x) for x in PAL_TWEAK["parts"])))

    # anchors: ranges no option touches, to identify the cartridge
    used = set()
    for s in sections.values():
        used.update(range(s["addr"], s["addr"] + s["length"]))
    anchors = []
    for at in (0x0000, 0x8000, 0xC000):
        if not (set(range(at, at + 256)) & used):
            anchors.append({"addr": at, "length": 256,
                            "crc32": "0x%08X" % (zlib.crc32(pal[at:at + 256])
                                                 & 0xFFFFFFFF)})

    import hashlib
    manifest = {
        "format": patchset.FORMAT,
        "name": "Karateka (PAL) fixes",
        "what": "The NTSC fixes that survive translation to the European "
                "release, which is the same game in four banks. Addresses "
                "are file offsets: a bankswitched cartridge has no single "
                "CPU address for one.",
        "target": {
            "what": PAL_NAME, "body_size": len(pal),
            "body_sha256": hashlib.sha256(pal).hexdigest(),
            "headers": [0, K.HDR], "base": 0, "anchors": anchors,
        },
        "knobs": {},
        "sections": sections,
        "options": options,
    }
    out = os.path.join(OUTDIR, "karateka-pal.abp")
    if not os.path.isdir(OUTDIR):
        os.makedirs(OUTDIR)
    patchset.write_bundle(out, manifest, files)
    K._publish(out)
    print("\n%s\n  %d options over %d sections, %d bytes"
          % (out, len(options), len(sections), os.path.getsize(out)))
    print("  no signature: PAL cartridges are not checked and carry none")
    return 0


if __name__ == "__main__":
    sys.exit(main())
