#!/usr/bin/env python3
"""
Decode MARIA display lists and display list lists.

This is the part of the 7800 that most repays getting right, and the part
easiest to read wrong. Two traps in particular:

  * A display list entry is four bytes OR five, and you cannot tell from the
    length -- you tell from the second byte. If its low five bits are zero (and
    the byte itself is not zero, which would end the list) the entry is five
    bytes long and **the palette and width live in byte 3, not byte 1**. Read a
    five-byte entry as a four-byte one and you will report the wrong palette
    with complete confidence, because the bytes you read are all valid.

  * The offset field in a DLL entry counts DOWN. A zone whose offset is 7 draws
    eight scanlines, and MARIA reads graphics for scanline n from page
    (high byte + offset) as the offset decreases. That is why sprite data is
    stored line-planar: consecutive scanlines come from consecutive pages, not
    consecutive bytes.

Usage:
  python dlwalk.py <rom.a78> --space f6 --dl 0x4200        # one display list
  python dlwalk.py <rom.a78> --space f7 --dll 0xF000 --zones 25
  python dlwalk.py --raw dump.bin --at 0x1800 --dl 0x1900  # a RAM dump
  python dlwalk.py --selftest
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# In a five-byte header, bit 6 of byte 1 is the write-mode bit and bit 5 marks
# indirect (character) mode. Write mode does not name a graphics mode on its
# own: it combines with CTRL's read-mode bits to pick one of 160A/160B/320A-D.
WRITE_MODE = {0: "write mode 0", 1: "write mode 1"}


# --------------------------------------------------------------------- reading
def unmirror(addr):
    """Fold a 7800 RAM mirror onto the address the RAM really lives at.

    The 6502 needs its zero page and stack in the first two pages, so the 7800
    mirrors two pages of its RAM down there: $0040-$00FF appears again at
    $2040-$20FF, and $0140-$01FF at $2140-$21FF. Display lists are routinely
    built through the low addresses and pointed at from DPPH/DPPL the same way,
    so a dump of $1800-$27FF will be asked for addresses it does not appear to
    contain. It does -- one page up.
    """
    if 0x0040 <= addr <= 0x00FF:
        return addr + 0x2000
    if 0x0140 <= addr <= 0x01FF:
        return addr + 0x2000
    if 0x2800 <= addr <= 0x3FFF:          # the RAM block mirrors again
        return addr - 0x1000
    return addr


class Source(object):
    """Somewhere bytes can be read from by CPU address."""

    def __init__(self, data, base, mirrors=True):
        self.data, self.base, self.mirrors = data, base, mirrors

    def byte(self, addr):
        if self.mirrors:
            addr = unmirror(addr)
        i = addr - self.base
        if not 0 <= i < len(self.data):
            raise IndexError("$%04X is outside this source" % addr)
        return self.data[i]


# ------------------------------------------------------------------- the entry
def decode_entry(src, addr):
    """Decode one display list entry. Returns (dict, length) or (None, 0)."""
    b0 = src.byte(addr)
    b1 = src.byte(addr + 1)

    if b1 == 0:
        return None, 0                       # a zero second byte ends the list

    if (b1 & 0x1F) == 0:
        # Five-byte (extended) entry. Byte 1 carries the mode bits only; the
        # palette and width have moved to byte 3.
        b2, b3, b4 = (src.byte(addr + 2), src.byte(addr + 3), src.byte(addr + 4))
        width = (~b3 & 0x1F) + 1
        return {
            "bytes": 5,
            "addr": addr,
            "gfx": b0 | (b2 << 8),
            "palette": (b3 >> 5) & 7,
            "width": width,
            "hpos": b4,
            "indirect": bool(b1 & 0x20),
            "write_mode": (b1 >> 6) & 1,
            "raw": [b0, b1, b2, b3, b4],
        }, 5

    # Four-byte (direct) entry: palette and width are in byte 1.
    b2, b3 = src.byte(addr + 2), src.byte(addr + 3)
    width = (~b1 & 0x1F) + 1
    return {
        "bytes": 4,
        "addr": addr,
        "gfx": b0 | (b2 << 8),
        "palette": (b1 >> 5) & 7,
        "width": width,
        "hpos": b3,
        "indirect": False,
        "write_mode": None,
        "raw": [b0, b1, b2, b3],
    }, 4


def walk_dl(src, addr, limit=64):
    """Every entry in one display list, stopping at the terminator."""
    out = []
    for _ in range(limit):
        e, n = decode_entry(src, addr)
        if e is None:
            break
        out.append(e)
        addr += n
    return out


# --------------------------------------------------------------------- the DLL
def decode_dll_entry(src, addr):
    """One three-byte display-list-list entry."""
    b0, b1, b2 = src.byte(addr), src.byte(addr + 1), src.byte(addr + 2)
    return {
        "addr": addr,
        "dli": bool(b0 & 0x80),
        "holey16": bool(b0 & 0x40),
        "holey8": bool(b0 & 0x20),
        "offset": b0 & 0x0F,
        "lines": (b0 & 0x0F) + 1,        # offset counts down, so height is +1
        "dl": (b1 << 8) | b2,
        "raw": [b0, b1, b2],
    }


def walk_dll(src, addr, zones):
    return [decode_dll_entry(src, addr + 3 * i) for i in range(zones)]


# ------------------------------------------------------------------- rendering
def show_dl(entries, indent="    "):
    if not entries:
        print(indent + "(empty -- the first entry's second byte was zero)")
        return
    print(indent + "%-6s %-5s %-7s %-4s %-5s %-4s %s"
          % ("at", "bytes", "gfx", "pal", "width", "x", "notes"))
    for e in entries:
        notes = []
        if e["bytes"] == 5:
            notes.append("extended: palette came from byte 3")
            if e["indirect"]:
                notes.append("indirect (character mode)")
            wm = e["write_mode"]
            if wm is not None:
                notes.append("write mode %d (pairs with CTRL read mode)" % wm)
        print(indent + "$%04X %-5d $%04X  %-4d %-5d %-4d %s"
              % (e["addr"], e["bytes"], e["gfx"], e["palette"], e["width"],
                 e["hpos"], "; ".join(notes)))


def show_dll(zones, src=None, follow=False):
    print("%-6s %-4s %-6s %-6s %-6s %s"
          % ("at", "zone", "lines", "DL", "DLI", "holey DMA"))
    line = 0
    for i, z in enumerate(zones):
        holey = ", ".join(n for n, on in (("16K", z["holey16"]),
                                          ("8K", z["holey8"])) if on) or "-"
        print("$%04X %-4d %-6d $%04X  %-6s %s"
              % (z["addr"], i, z["lines"], z["dl"],
                 "yes" if z["dli"] else "-", holey))
        line += z["lines"]
        if follow and src is not None:
            try:
                show_dl(walk_dl(src, z["dl"]), indent="        ")
            except IndexError as e:
                print("        (cannot follow: %s)" % e)
    print("\n%d zones, %d scanlines total" % (len(zones), line))


# -------------------------------------------------------------------- selftest
def selftest():
    """Check the decoder against entries built by hand from the spec."""
    # A four-byte entry: gfx $2010, palette 3, width 8, x=40.
    #   byte1 = palette<<5 | (32 - width) & 0x1F = $60 | $18 = $78
    four = [0x10, 0x78, 0x20, 40]
    # A five-byte entry for the same thing. Byte 1 has zero in its low five
    # bits, which is what makes it extended; the palette|width byte moves to
    # byte 3. This is the shape that gets misread.
    five = [0x10, 0x40, 0x20, 0x78, 40]
    src = Source(bytes(four + five + [0, 0]), 0x1800)

    e, n = decode_entry(src, 0x1800)
    assert n == 4 and e["gfx"] == 0x2010 and e["palette"] == 3 \
        and e["width"] == 8 and e["hpos"] == 40, e
    e, n = decode_entry(src, 0x1804)
    assert n == 5 and e["gfx"] == 0x2010 and e["palette"] == 3 \
        and e["width"] == 8 and e["hpos"] == 40, e

    # The trap itself: read the five-byte entry as if it were four bytes and
    # the palette comes out of byte 1 instead -- a different, plausible answer.
    wrong_palette = (five[1] >> 5) & 7
    assert wrong_palette == 2 and e["palette"] == 3, (wrong_palette, e["palette"])

    # A DLL entry: 8 lines, DLI set, DL at $1F00.
    d = Source(bytes([0x87, 0x1F, 0x00]), 0x1800)
    z = decode_dll_entry(d, 0x1800)
    assert z["lines"] == 8 and z["dli"] and z["dl"] == 0x1F00, z

    print("selftest passed")
    print("  4-byte entry decoded: palette 3, width 8")
    print("  5-byte entry decoded: palette 3, width 8  (from byte 3)")
    print("  misreading it as 4-byte would have said palette %d" % wrong_palette)
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("rom", nargs="?")
    ap.add_argument("--space", help="which cartridge space to read from")
    ap.add_argument("--raw", help="read a flat binary (a RAM dump) instead")
    ap.add_argument("--at", type=lambda x: int(x, 0), default=0x1800,
                    help="CPU address the --raw dump starts at")
    ap.add_argument("--dl", type=lambda x: int(x, 0), help="a display list")
    ap.add_argument("--dll", type=lambda x: int(x, 0), help="a display list list")
    ap.add_argument("--zones", type=int, default=25, help="zones in the DLL")
    ap.add_argument("--follow", action="store_true",
                    help="also decode each display list the DLL points at")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    if args.raw:
        src = Source(open(args.raw, "rb").read(), args.at)
    elif args.rom and args.space:
        import cart as cartlib
        c = cartlib.Cart(args.rom)
        src = Source(c.slice(args.space, c.base_of(args.space),
                             c.size_of(args.space)), c.base_of(args.space))
    else:
        ap.error("give a rom with --space, or --raw with --at")

    if args.dll is not None:
        show_dll(walk_dll(src, args.dll, args.zones), src, args.follow)
    elif args.dl is not None:
        show_dl(walk_dl(src, args.dl))
    else:
        ap.error("nothing to do: pass --dl or --dll")
    return 0


if __name__ == "__main__":
    sys.exit(main())
