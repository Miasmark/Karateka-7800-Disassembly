#!/usr/bin/env python3
"""
Atari 7800 NTSC colour bytes to RGB.

A colour byte is hue in the high nibble and luminance in the low. Hue 0 is
greyscale; hues 1-15 step around the NTSC colour wheel. This uses the widely
published base RGB per hue at high luminance and scales by the luma nibble,
which is close enough to read artwork by -- real output depends on the console,
the television and the decoder.

gfx.py's earlier approximation derived hue from a phase formula and put hue 9
in the olive range when it should be blue; that is visible immediately on the
zombie sprites, whose faces are blue in play.
"""

HUE = [
    (236, 236, 236),   # 0  grey
    (252, 252,  84),   # 1  yellow
    (252, 188,  60),   # 2  orange
    (252, 124,  88),   # 3  red-orange
    (252, 104, 124),   # 4  pink
    (236,  88, 180),   # 5  purple
    (188,  88, 236),   # 6  purple-blue
    (140, 100, 252),   # 7  blue-purple
    ( 92, 120, 252),   # 8  blue
    ( 60, 148, 252),   # 9  light blue
    ( 56, 180, 252),   # A  turquoise
    ( 56, 204, 180),   # B  green-blue
    ( 56, 212, 108),   # C  green
    (100, 220,  60),   # D  yellow-green
    (168, 220,  60),   # E  orange-green
    (216, 212,  60),   # F  light orange
]


def ntsc7800(byte, gamma=0.80, chroma=0.75):
    """Colour byte to RGB.

    Luminance and chrominance are independent in NTSC, so a dark colour is not
    a dimmed bright one -- it keeps its chroma. An earlier version multiplied
    the whole base RGB by (lum/15)**0.85, which is a brightness dimmer: it made
    every low-luma entry both too dark and too grey, and it wrecked hue balance,
    since scaling (252,252,84) keeps R=G where real gold has R>G.

    This sets Y from the luma nibble and adds the hue's offset from its own grey
    level at roughly constant amplitude. The hue table itself is unchanged --
    its angles were verified against play (hue 9 blue on the zombie faces, hue C
    green on the grass, hue 7 the purple pool).

    Chroma is held at full amplitude all the way down, including luma 0, which
    is what the hardware does: a luma-0 colour is not black unless its hue is
    neutral. `$70` is dark blue and `$20` dark red -- both visible on the Skull's
    projectile, whose palette is `$70 $20 $0C`.

    An earlier version faded chroma out over the bottom luma steps to force
    luma 0 black. That was over-correction: the case it was written for, the
    bat's black wings, is handled by the hue being neutral, since a neutral hue
    has no chroma offset to survive. Fading it also erased every dark saturated
    colour in the game.
    """
    hue, lum = (byte >> 4) & 0x0F, byte & 0x0F
    base = HUE[hue]
    grey = 0.299 * base[0] + 0.587 * base[1] + 0.114 * base[2]
    y = 255.0 * ((lum / 15.0) ** gamma)
    k = chroma
    return tuple(max(0, min(255, int(round(y + (c - grey) * k)))) for c in base)


if __name__ == "__main__":
    for b in (0x00, 0x94, 0x0C, 0x11, 0x26, 0x20, 0xB0, 0xB4, 0xB6):
        print("$%02X -> %s" % (b, ntsc7800(b)))


# ---------------------------------------------------------------------------
# Palettes sampled from play, not read from ROM.
#
# MARIA palettes 2, 4 and 6 are only ever loaded from the base block at
# b3:$81C9, and those values do not match what the game shows: palette 2's
# middle entry is $02, a flat grey, yet the pumpkin-head zombie is plainly
# orange on screen. Something rewrites them at a point not found in the
# disassembly -- every candidate an opcode scan turned up was a byte pattern
# inside graphics data.
#
# Where a screenshot exists, the colours are taken from it. Anything here is
# observed, not derived, and is marked as such wherever it is displayed.
# Palettes 2-5 have no per-area source. Exactly two block copies write them in
# the whole ROM -- b3:$81C9 during the intro and b0:$A581 when the Grampa screen
# opens -- and nothing reloads them when an area is entered, so the world keeps
# whichever ran last. The values below are sampled from play and remain useful
# for sprites; item icons now read the blocks directly (see tools/rooms.py).
PLAY_PALETTE = {
    # pumpkin-head zombie: dark rind, orange body, lit orange
    2: [(38, 28, 20), (198, 106, 38), (240, 152, 62)],
    # headless zombie: the neck stub, dark red with pale flecks
    4: [(64, 22, 20), (146, 38, 30), (232, 228, 214)],
}

# Palette 6 is not an identity and has no fixed value: f6:$4C70 rewrites all
# three of its registers every frame from the random generator --
#
#     JSR Random : STA P6C1 : EOR #$F0 : STA P6C2 : ADC #$10 : STA P6C3
#
# so the entries are c, c^$F0 and (c^$F0)+$10: always hue-contrasted at matched
# luminance rather than three unrelated colours, which is what makes it read as
# a strobe instead of noise. sub_4329 forces any part into palette 6 while its
# flash flag is set, and the bulb-head zombie is authored to use it permanently.
UNSOURCED = {}


def flash_palette(seed):
    """One frame of palette 6, reproducing f6:$4C70 from a random byte."""
    c1 = seed & 0xFF
    c2 = c1 ^ 0xF0
    c3 = (c2 + 0x10) & 0xFF
    return [ntsc7800(c) for c in (c1, c2, c3)]

# A note on orange: on NTSC hardware of this era orange sits very close to brown,
# and the two are separated as much by chroma artifacting on a composite signal
# as by the palette entry itself. A flat RGB conversion will always read a shade
# duller than a CRT does, so the sampled values above are closer to the intent
# than an arithmetic conversion of the colour byte would be.


def palette_rgb(index, rom_bytes):
    """RGB triple for a MARIA palette: sampled where known, else converted."""
    if index in PLAY_PALETTE:
        return PLAY_PALETTE[index], "observed"
    return [ntsc7800(b) for b in rom_bytes], "rom"
