#!/usr/bin/env python3
"""
Render one MARIA display-list object -- a real sprite, not a 256-entry sheet.

    python tools/spritedump.py rom.a78 --base 0xD846 --width 4 --lines 8 \
        --palette 21,3F,00 -o out.png

    # a taller object built from several zones, each its own width:
    python tools/spritedump.py rom.a78 \
        --stack 0xD846:4:24 --stack 0xC046:7:24 --palette-regs regs.txt -o out.png

## Why this and not gfx.py

`gfx.py --sheet` renders all 256 character codes on a fixed grid, which is
right for a *font* (`docs/graphics.md` covers that). A direct-mode display-list
object is a different shape of question: one address, one width in bytes, one
height in scanlines, one palette -- and the object is very often taller than
one zone, built from several display-list entries whose graphics addresses
step by exactly `lines * 256` between zones, because that is what MARIA's own
descending-page addressing does to a sprite that spans more than one zone.
`--stack` renders each segment at its own width and pastes them top to bottom,
which a fixed 16x16 character grid cannot express at all.

## Finding the base, width and lines to pass

Capture a running game with `probes/dumpgfx.lua`, then read the dump with
`dlwalk.py --raw ... --follow`. Group the zone entries that share an x
position and whose `gfx` addresses step by a consistent `lines * 256` -- that
grouping *is* one object. Zones that do not step consistently are unrelated
things that happen to share a row.

## Colour

MARIA decides an object's colours from its own palette registers at runtime,
not from anything in the display-list entry beyond a 0-7 palette *number*. So
this needs the three colour bytes for that palette, from one of:

  --palette 21,3F,00        given directly, high to low priority colour
  --palette-regs FILE        a `dumpgfx_regs.txt` from probes/dumpgfx.lua,
                              plus --palette-index to say which of the 8

Without either, this renders raw 2-bit pixel indices as four grey levels,
which shows the real silhouette without inventing a colour.

## Pixel format

Fixed at 2 bits/pixel, 4 pixels/byte, MSB first (`160A`/`160B` -- see
`docs/graphics.md`). That is what CTRL read-mode 00 selects, and it is what
every direct-mode object found in Karateka uses; a game using 320-width modes
needs a different unpacker, which this does not attempt to guess.
"""
import argparse
import os
import sys

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from disasm import Cart
from palette import ntsc7800

TRANSPARENT = (0, 0, 0)


def unpack_byte(b):
    """Four 2-bit pixel indices from one byte, MSB first."""
    return [(b >> (6 - 2 * p)) & 3 for p in range(4)]


def pixel_page(base, lines, line, descending):
    """Which ROM page holds this output line, per MARIA's own addressing.

    **The zone offset counts down**: the first scanline of a zone reads the
    *highest* page and the last reads the base. Reading pages upward renders
    every sprite upside down -- this project's own proof is Midnight Mutants'
    lettering at f7:$E0, which spells GAME OVER only read this way.

    `descending=False` is for data that is not a MARIA zone at all -- a
    contiguous blob, or a character-mode font page, which the hardware
    addresses the other way round (`CHARBASE + line`, ascending).
    """
    n = (lines - 1 - line) if descending else line
    return (base + n * 256) & 0xFFFF


def render_object(cart, space, base, width_bytes, lines, colours,
                  descending=True):
    """One display-list object, `width_bytes` wide by `lines` tall."""
    pal = [TRANSPARENT] + list(colours)
    img = Image.new("RGB", (width_bytes * 4, lines), (255, 0, 255))
    px = img.load()
    for line in range(lines):
        page = pixel_page(base, lines, line, descending)
        for bi in range(width_bytes):
            b = cart.byte(space, (page + bi) & 0xFFFF)
            for p, idx in enumerate(unpack_byte(b)):
                px[bi * 4 + p, line] = pal[idx]
    return img


def render_stack(cart, space, segments, colours, descending=True):
    """Several (base, width_bytes, lines) segments, pasted top to bottom.

    For one object spanning several zones whose width changes partway --
    Karateka's fighters do this, narrower at the head and torso, wider at a
    braced stance -- render each width separately and stack them; a single
    fixed-width render would either crop the wide part or pad the narrow part
    with garbage.
    """
    imgs = [render_object(cart, space, b, w, l, colours, descending)
            for b, w, l in segments]
    width = max(im.width for im in imgs)
    height = sum(im.height for im in imgs)
    out = Image.new("RGB", (width, height), (255, 0, 255))
    y = 0
    for im in imgs:
        out.paste(im, (0, y))
        y += im.height
    return out


def read_palette_regs(path, index):
    """Pull one palette's three colour bytes out of a dumpgfx_regs.txt."""
    want = "$%02X" % (0x21 + index * 4)   # P{n}C1's register address
    lines_by_addr = {}
    for ln in open(path, encoding="utf-8"):
        ln = ln.strip()
        if ln.startswith("$") and "=" in ln:
            addr, val = [x.strip() for x in ln.split("=")]
            lines_by_addr[addr] = int(val.lstrip("$"), 16)
    base = 0x21 + index * 4
    missing = [a for a in (base, base + 1, base + 2)
              if ("$%02X" % a) not in lines_by_addr]
    if missing:
        raise SystemExit(
            "palette %d needs $%02X-$%02X in %s; missing %s"
            % (index, base, base + 2, path,
               ", ".join("$%02X" % a for a in missing)))
    return [lines_by_addr["$%02X" % a] for a in (base, base + 1, base + 2)]


def parse_stack_arg(text):
    parts = text.split(":")
    if len(parts) != 3:
        raise SystemExit("--stack wants base:width:lines, e.g. 0xC046:7:24")
    base, width, lines = parts
    return (int(base, 0), int(width, 0), int(lines, 0))


def main():
    ap = argparse.ArgumentParser(
        description=__doc__.strip().split("\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("rom")
    ap.add_argument("--space", default=None,
                    help="bank/space name, for a mapped cartridge "
                         "(default: the cart's only space, or its first)")
    ap.add_argument("--side", choices=["sally", "maria"], default="sally")
    ap.add_argument("--base", type=lambda v: int(v, 0),
                    help="graphics address of the object's lowest page")
    ap.add_argument("--width", type=int, help="bytes per line")
    ap.add_argument("--lines", type=int, help="scanlines")
    ap.add_argument("--stack", action="append", default=[],
                    metavar="BASE:WIDTH:LINES",
                    help="one segment of a multi-zone object; repeat, "
                         "top to bottom")
    ap.add_argument("--palette", help="three hex colour bytes, e.g. 21,3F,00")
    ap.add_argument("--palette-regs", help="a dumpgfx_regs.txt to read from")
    ap.add_argument("--palette-index", type=int, default=0,
                    help="which of the 8 palettes, with --palette-regs")
    ap.add_argument("--ascending", action="store_true",
                    help="read pages in ascending order; correct for "
                         "CHARBASE character-mode data, wrong for a "
                         "direct-mode zone object")
    ap.add_argument("--scale", type=int, default=8)
    ap.add_argument("-o", "--out", default="sprite.png")
    args = ap.parse_args()

    cart = Cart(args.rom, side=args.side)
    space = args.space or cart.spaces()[0]

    if args.palette_regs:
        colours = read_palette_regs(args.palette_regs, args.palette_index)
    elif args.palette:
        colours = [int(x, 16) for x in args.palette.split(",")]
    else:
        colours = None
    rgb = ([(105, 105, 115), (175, 175, 185), (245, 245, 250)]
           if colours is None else [ntsc7800(c) for c in colours])

    segments = [parse_stack_arg(s) for s in args.stack]
    if not segments:
        if args.base is None or args.width is None or args.lines is None:
            sys.stderr.write(
                "need --base/--width/--lines, or one or more --stack\n")
            return 2
        segments = [(args.base, args.width, args.lines)]

    img = render_stack(cart, space, segments, rgb,
                       descending=not args.ascending)
    img = img.resize((img.width * args.scale, img.height * args.scale),
                     Image.NEAREST)
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    img.save(args.out)
    print("wrote %s (%dx%d)" % (args.out, img.width, img.height))
    return 0


if __name__ == "__main__":
    sys.exit(main())
