#!/usr/bin/env python3
"""
Render MARIA graphics out of the cartridge -- character sets (indirect mode)
and single display-list objects (direct mode).

In indirect (character map) mode MARIA forms the address of a character's
graphics as

    ((CHARBASE + line) << 8) | character_number

so a character set is stored *line-planar*: page CHARBASE+0 holds line 0 of all
256 characters, page CHARBASE+1 holds line 1, and so on.  Midnight Mutants runs
with CTRL = $50 (read mode 00 = 160x2, one-byte characters), so each character
is one byte = 4 pixels wide, and each byte holds four 2-bit pixels, MSB first.
This is the default mode -- it draws all 256 characters as a grid, right for
reading a font or a character-mode sprite sheet.

Direct mode (--direct WIDTH) is a *single* display-list object, WIDTH bytes
wide (get WIDTH and --lines from the live display list, not a guess -- see
docs/graphics.md). Pointing the indirect-mode grid at a direct-mode object's
address works by accident at best: it reads 256 side-by-side objects sharing
the object's low byte, so a small object gets buried in up to 255 unrelated
neighbours -- background fills, other tiles, whatever else shares that low
byte at a different page -- and only chance decides whether what shows up
in the grid was ever really at that address. A ROM with narrow direct-mode
objects between two confirmed character sets found exactly this: the
un-parametrised grid render showed what looked like real, unrelated artwork
sitting two dozen pages deeper than the object's actual few-scanline extent,
and reading only as far as the true WIDTH/lines showed the real, much
smaller content -- solid fills and diagonal tile edges, not a sprite at all.

Colour is decided at run time by the MARIA palette registers, so by default this
renders the raw 2-bit pixel indices as four grey levels -- that shows the real
artwork without inventing colours.  --palette applies an approximate NTSC
rendering of a supplied 3-colour palette instead.

Usage:
  python gfx.py <rom.a78> --space b1 --base 0x8000 --lines 8 -o out.png
  python gfx.py <rom.a78> --space rom --base 0x8020 --direct 24 --lines 8 -o obj.png
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


def render_direct(cart, space, base, width, lines, pal, scale=4,
                  descending=True):
    """A single direct-mode object, `width` bytes wide by `lines` tall.

    Character mode (`render_charset`) reads 256 side-by-side objects sharing
    one page-per-line layout, which is the wrong shape for one direct-mode
    display-list entry: pointing it at a `width`-byte object and an
    unrelated `lines` guess (128 by default) reads far past the real object
    into whatever unrelated data sits at higher pages of the same low byte,
    and can misread background fills or neighbouring tiles as if they were
    the object itself. Get `width` from the display-list entry (`dlwalk.py`
    reports it as bytes, already decoded from the one's-complement 5-bit
    field) and `lines` from the zone's own scanline count -- both are in
    the live display list, not a guess. Same MARIA-counts-down addressing
    as `render_charset`.
    """
    img = Image.new("RGB", (width * 4, lines), pal[0])
    px = img.load()
    for l in range(lines):
        n = (lines - 1 - l) if descending else l
        for col in range(width):
            b = cart.byte(space, base + n * 256 + col)
            for p in range(4):
                idx = (b >> (6 - 2 * p)) & 3
                px[col * 4 + p, l] = pal[idx]
    return img.resize((img.width * scale, img.height * scale), Image.NEAREST)


def render_sheet(cart, space, base, width, lines, count, pal, scale=4,
                 descending=True, cols=8, gap=1):
    """`count` direct-mode objects packed end to end, laid out as a contact sheet.

    Sprite sheets in the page-strided layout pack consecutive frames at a
    stride equal to the object's own width, so frame i starts `i * width`
    bytes along -- the same `base + line * 256 + col` addressing as one
    object, just walked. Rendering them one at a time works and is how this
    was first done by hand, but a rotation set is 30-odd frames and the
    thing you actually need to see is all of them together: the frame count,
    where one bank ends and the next begins, and whether the width guess
    from the display list is right (a wrong width shears the sheet
    diagonally, which is obvious across 30 frames and invisible in one).
    """
    rows = (count + cols - 1) // cols
    cw, ch = width * 4, lines
    img = Image.new("RGB", (cols * (cw + gap) - gap, rows * (ch + gap) - gap),
                    (40, 40, 48))
    for i in range(count):
        one = render_direct(cart, space, base + i * width, width, lines, pal,
                            scale=1, descending=descending)
        img.paste(one, ((i % cols) * (cw + gap), (i // cols) * (ch + gap)))
    return img.resize((img.width * scale, img.height * scale), Image.NEAREST)


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


def render_linear(cart, space, base, cell, lines, gw, count, pal, scale=4,
                  cols=16):
    """A *linear* character set: each glyph is `cell` consecutive bytes.

    MARIA's own indirect mode wants character data line-planar (page
    CHARBASE+0 is line 0 of every character), and `render_charset` reads
    that layout. But a cartridge is free to *store* its font linearly --
    glyph 0's scanlines, then glyph 1's, and so on -- and convert to the
    line-planar form when it copies the set into RAM at init. A ROM that
    does this renders as pure noise under `render_charset` at every base
    and `--lines` you try, because the grid is slicing across glyph cells
    instead of along them.

    `gw` is bytes per scanline (glyph width; 1 byte = 4 pixels at 2bpp),
    `lines` is scanlines actually drawn, and any remainder of `cell` is
    per-glyph padding that is read but not shown -- a real layout: a font
    of 4x5-pixel glyphs stored 6 bytes apart, the 6th byte unused.
    """
    rows = (count + cols - 1) // cols
    img = Image.new("RGB", (cols * gw * 4, rows * lines), pal[0])
    px = img.load()
    for c in range(count):
        cx, cy = (c % cols) * gw * 4, (c // cols) * lines
        for l in range(lines):
            for w in range(gw):
                b = cart.byte(space, base + c * cell + l * gw + w)
                for p in range(4):
                    idx = (b >> (6 - 2 * p)) & 3
                    px[cx + w * 4 + p, cy + l] = pal[idx]
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
    ap.add_argument("--direct", type=int, metavar="WIDTH",
                    help="render one direct-mode object WIDTH bytes wide, "
                         "instead of the 256-character indirect-mode grid -- "
                         "use for a single display-list sprite, not a "
                         "character set. Get WIDTH and --lines from the "
                         "live display list (dlwalk.py), not a guess.")
    ap.add_argument("--sheet", type=int, metavar="N",
                    help="with --direct, render N consecutive objects packed "
                         "at a stride equal to their width, as a contact "
                         "sheet. What a sprite sheet in the page-strided "
                         "layout actually looks like.")
    ap.add_argument("--sheet-cols", type=int, default=8, metavar="N",
                    help="objects per row in a --sheet (default 8)")
    ap.add_argument("--scale", type=int, default=4)
    ap.add_argument("--palette", help="three hex colour bytes, e.g. 36,13,0D")
    ap.add_argument("--ascending", action="store_true",
                    help="read lines in ascending page order; MARIA counts a "
                         "zone offset down, so descending is right for zones")
    ap.add_argument("--side", choices=["sally", "maria"], default="sally",
                    help="bankset cartridges: which parallel set to read")
    ap.add_argument("--linear", type=int, metavar="CELL",
                    help="render a LINEAR character set: each glyph is CELL "
                         "consecutive bytes, rather than MARIA's line-planar "
                         "layout. Use when a charset render is noise at every "
                         "base -- some ROMs store the font linearly and "
                         "convert it when copying to RAM. Pair with --lines "
                         "(scanlines drawn) and --gw (bytes per scanline); "
                         "any remainder of CELL is per-glyph padding.")
    ap.add_argument("--gw", type=int, default=1, metavar="BYTES",
                    help="--linear only: bytes per scanline (default 1 = 4 "
                         "pixels wide at 2bpp)")
    ap.add_argument("--count", type=int, default=256, metavar="N",
                    help="--linear only: how many glyphs to render "
                         "(default 256)")
    ap.add_argument("--grid", action="store_true")
    ap.add_argument("-o", "--out", default="gfx.png")
    args = ap.parse_args()

    cart = Cart(args.rom, side=args.side)
    if args.palette:
        cols = [int(x, 16) for x in args.palette.split(",")]
        pal = [(16, 16, 20)] + [ntsc(c) for c in cols]
    else:
        pal = GREY

    if args.linear:
        lines = args.lines if args.lines != 8 else args.linear
        img = render_linear(cart, args.space, int(args.base, 0), args.linear,
                            lines, args.gw, args.count, pal, args.scale)
        if args.grid:
            img = grid(img, 16, (args.count + 15) // 16,
                       args.gw * 4 * args.scale, lines * args.scale)
    elif args.direct and args.sheet:
        img = render_sheet(cart, args.space, int(args.base, 0), args.direct,
                           args.lines, args.sheet, pal, args.scale,
                           descending=not args.ascending,
                           cols=args.sheet_cols)
    elif args.direct:
        img = render_direct(cart, args.space, int(args.base, 0), args.direct,
                            args.lines, pal, args.scale,
                            descending=not args.ascending)
    else:
        img = render_charset(cart, args.space, int(args.base, 0), args.lines,
                             pal, args.scale, descending=not args.ascending)
        if args.grid:
            img = grid(img, 16, 256 // 16, 4 * args.scale, args.lines * args.scale)
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    img.save(args.out)
    print("wrote %s (%dx%d)" % (args.out, img.width, img.height))


if __name__ == "__main__":
    main()
