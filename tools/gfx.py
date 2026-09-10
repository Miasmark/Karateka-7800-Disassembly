#!/usr/bin/env python3
"""
Render MARIA character sets out of the cartridge.

In indirect (character map) mode MARIA forms the address of a character's
graphics as

    ((CHARBASE + line) << 8) | character_number

so a character set is stored *line-planar*: page CHARBASE+0 holds line 0 of all
256 characters, page CHARBASE+1 holds line 1, and so on.  Midnight Mutants runs
with CTRL = $50 (read mode 00 = 160x2, one-byte characters), so each character
is one byte = 4 pixels wide, and each byte holds four 2-bit pixels, MSB first.

Colour is decided at run time by the MARIA palette registers, so by default this
renders the raw 2-bit pixel indices as four grey levels -- that shows the real
artwork without inventing colours.  --palette applies an approximate NTSC
rendering of a supplied 3-colour palette instead.

Usage:
  python gfx.py <rom.a78> --space b1 --base 0x8000 --lines 8 -o out.png
  python gfx.py <rom.a78> --sheet            # every charset candidate
"""
import argparse
import os
import sys

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from disasm import Cart

GREY = [(20, 20, 24), (105, 105, 115), (175, 175, 185), (245, 245, 250)]


def ntsc(color):
    """Approximate an Atari 7800 colour byte (hue<<4 | luma) as RGB.

    This is an approximation for previewing only -- real NTSC output depends on
    the console and TV, and emulators do not agree on an exact table.
    """
    import colorsys
    hue, lum = (color >> 4) & 0x0F, color & 0x0F
    y = lum / 15.0
    if hue == 0:
        v = int(y * 255)
        return (v, v, v)
    h = ((hue - 1) / 15.0 + 0.62) % 1.0
    r, g, b = colorsys.hsv_to_rgb(h, 0.55 * (1.0 - abs(y - 0.5)), min(1.0, y + 0.25))
    return (int(r * 255), int(g * 255), int(b * 255))


def render_charset(cart, space, base, lines, pal, scale=4, cols=16,
                   descending=True):
    """256 characters, each 4px wide by `lines` tall, laid out in a grid.

    **MARIA's zone offset counts DOWN**: the first scanline of a zone reads the
    highest page and the last reads the base. So line 0 comes from
    `base + (lines-1)*256`, and reading the pages upward renders everything
    upside down. The proof is Midnight Mutants' lettering at `f7:$E0`, which
    spells GAME OVER only this way round.

    `descending=False` for data that is not a MARIA zone.
    """
    rows = 256 // cols
    img = Image.new("RGB", (cols * 4, rows * lines), pal[0])
    px = img.load()
    for c in range(256):
        cx, cy = (c % cols) * 4, (c // cols) * lines
        for l in range(lines):
            n = (lines - 1 - l) if descending else l
            b = cart.byte(space, base + n * 256 + c)
            for p in range(4):
                idx = (b >> (6 - 2 * p)) & 3
                px[cx + p, cy + l] = pal[idx]
    return img.resize((img.width * scale, img.height * scale), Image.NEAREST)


def grid(img, cols, rows, cell_w, cell_h, colour=(70, 70, 90)):
    px = img.load()
    for i in range(1, cols):
        for y in range(img.height):
            px[i * cell_w, y] = colour
    for j in range(1, rows):
        for x in range(img.width):
            px[x, j * cell_h] = colour
    return img


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("rom")
    ap.add_argument("--space", default="b1")
    ap.add_argument("--base", default="0x8000")
    ap.add_argument("--lines", type=int, default=8)
    ap.add_argument("--scale", type=int, default=4)
    ap.add_argument("--palette", help="three hex colour bytes, e.g. 36,13,0D")
    ap.add_argument("--ascending", action="store_true",
                    help="read lines in ascending page order; MARIA counts a "
                         "zone offset down, so descending is right for zones")
    ap.add_argument("--side", choices=["sally", "maria"], default="sally",
                    help="bankset cartridges: which parallel set to read")
    ap.add_argument("--grid", action="store_true")
    ap.add_argument("-o", "--out", default="gfx.png")
    args = ap.parse_args()

    cart = Cart(args.rom, side=args.side)
    if args.palette:
        cols = [int(x, 16) for x in args.palette.split(",")]
        pal = [(16, 16, 20)] + [ntsc(c) for c in cols]
    else:
        pal = GREY

    img = render_charset(cart, args.space, int(args.base, 0), args.lines,
                         pal, args.scale, descending=not args.ascending)
    if args.grid:
        img = grid(img, 16, 256 // 16, 4 * args.scale, args.lines * args.scale)
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    img.save(args.out)
    print("wrote %s (%dx%d)" % (args.out, img.width, img.height))


if __name__ == "__main__":
    main()
