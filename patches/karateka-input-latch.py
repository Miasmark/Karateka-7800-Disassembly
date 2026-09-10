"""Patch Karateka so a short press is never dropped.

The game reads its controls once per trip round the main loop, and that loop
takes 13 to 14 frames. A press held for less than that can fall entirely
between two reads: measured, a 100 ms tap is seen 74% of the time and a 33 ms
one 36%. The player experiences that as the game ignoring them.

This does not make the loop faster -- nothing cheap does, the game is a Forth
program and the interpreter's dispatch alone eats a third of every frame. What
it does is make sure no press is *lost*: the display interrupt, which runs
every frame anyway, samples the controls and latches them, and the game's
existing input word reads the latch instead of the port. Anything pressed at
any point during those thirteen frames is still there when the game looks.

## Why it fits

The interrupt's last phase spends 85 cycles doing nothing before it writes the
background colour -- `LDY #$10 / DEY / BNE` and two NOPs. That delay is there
to place a raster boundary, so those cycles cannot simply be taken; the
replacement has to consume *exactly* 85 cycles or the colour band moves. It
does:

    JSR + 4 NOPs        6 + 8            = 14
    latch routine      28 + 31 + 6 + 6   = 71
                                           ---
                                           85

The 31 is a shortened delay loop and the 6 is three NOPs, sized to make the
total come out right.

## What it actually achieved

Measured, driving a repeated press at varied phases and watching the game's own
direction variables move:

    press held      original    patched
    2 frames  33ms     16%        46%
    6 frames 100ms     34%        47%
    10 frames 167ms    37%        47%

Short presses improve markedly, and the response stops depending on how long
you hold -- which is the signature of the latch doing its job. It is not the
~100% the design aimed at, and the reason splits in two, both measured:

  * The latch was holding a press when the game looked only **72%** of the
    time. The design assumed the display interrupt's phase 0 runs once per
    frame; it does not. It comes round every 1.9 to 3.7 frames depending on
    what is on screen, because the interrupt fires a few times per frame and
    cycles through five phases. Latching in *every* phase would close this,
    and each phase has its own slack to do it in, but each needs its own
    cycle-exact edit.

  * Of the presses the latch did hold, the game acted on only **47/72**. That
    part is not an input problem at all: the game had the press and chose not
    to move. Karateka's state machine appears to refuse input while an
    animation is playing, which is a design decision no amount of input
    plumbing changes.

So this fixes about half of what is fixable in the input path, and the other
half of the felt problem is in the game's own logic.

## What it assumes

`$CF-$D1` are free: measured, nothing in the game reads or writes $CF-$DE in
4000 frames. The routine goes at $FF80, which is never read in the same
window. That last one is the weaker claim -- a game state this session did not
reach could read it -- so it is checked again after patching rather than
assumed.
"""
import io
import os
import sys

# No ROM ships with this: supply your own dump of the NTSC release,
# crc32 FEC21472, via KARATEKA_ROM or beside the toolkit.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_NAME = "Karateka (NTSC) (Atari) (1987) (FEC21472).a78"
SRC = (os.environ.get("KARATEKA_ROM")
       or os.path.join(_ROOT, "..", "karateka", _NAME))
OUT = os.environ.get(
    "KARATEKA_LATCH_OUT",
    os.path.join(_ROOT, "..", "karateka", "Karateka (input latch).a78"))

HDR = 128          # .a78 header; ROM is linear at $4000
SWCHA_LATCH = 0xCF
INPT0_LATCH = 0xD0
INPT1_LATCH = 0xD1
LATCH = 0xFF80     # the per-frame sampler, called from the display interrupt
WRAP = 0xFFA0      # what the game's input word calls instead


def off(addr):
    """File offset of a CPU address, for a linear cart mapped at $4000."""
    return HDR + addr - 0x4000


def patch(rom):
    w = bytearray(rom)

    def put(addr, data, expect=None):
        o = off(addr)
        if expect is not None and bytes(w[o:o + len(expect)]) != bytes(expect):
            raise SystemExit("at $%04X expected %s, found %s -- wrong ROM?"
                             % (addr, bytes(expect).hex(),
                                bytes(w[o:o + len(expect)]).hex()))
        w[o:o + len(data)] = bytes(data)

    # --- the display interrupt's last phase: spend the delay usefully -------
    # was: LDY #$10 / DEY / BNE -3 / NOP / NOP        7 bytes, 85 cycles
    put(0x5945, [0x20, LATCH & 0xFF, LATCH >> 8,     # JSR latch      6
                 0xEA, 0xEA, 0xEA, 0xEA],            # NOP x4         8
        expect=[0xA0, 0x10, 0x88, 0xD0, 0xFD, 0xEA, 0xEA])

    # --- the sampler itself: 71 cycles including RTS -----------------------
    put(LATCH, [
        0xAD, 0x80, 0x02,        # LDA $0280   4   directions, active low
        0x25, SWCHA_LATCH,       # AND $CF     3   so AND remembers a press
        0x85, SWCHA_LATCH,       # STA $CF     3
        0xA5, 0x08,              # LDA INPT0   3   buttons, bit 7 = pressed
        0x05, INPT0_LATCH,       # ORA $D0     3   so ORA remembers a press
        0x85, INPT0_LATCH,       # STA $D0     3
        0xA5, 0x09,              # LDA INPT1   3
        0x05, INPT1_LATCH,       # ORA $D1     3
        0x85, INPT1_LATCH,       # STA $D1     3      = 28
        0xA0, 0x06,              # LDY #$06    2
        0x88,                    # DEY         2  x6
        0xD0, 0xFD,              # BNE -3      3/2    = 31
        0xEA, 0xEA, 0xEA,        # NOP x3      6
        0x60,                    # RTS         6      = 71
    ])

    # --- the readers take the latch, not the port --------------------------
    put(0x5A11, [0xA5, SWCHA_LATCH, 0xEA],           # LDA $CF / NOP
        expect=[0xAD, 0x80, 0x02])                   # was LDA $0280
    put(0x59EB, [0xA5, INPT0_LATCH], expect=[0xA5, 0x08])
    put(0x59F4, [0xA5, INPT1_LATCH], expect=[0xA5, 0x09])

    # --- and clear the latch once the game has taken it --------------------
    put(0x59FE, [0x20, WRAP & 0xFF, WRAP >> 8, 0xEA, 0xEA, 0xEA],
        expect=[0x20, 0x07, 0x5A, 0x20, 0xE9, 0x59])
    put(WRAP, [
        0x20, 0x07, 0x5A,        # JSR $5A07   read directions from $CF
        0x20, 0xE9, 0x59,        # JSR $59E9   read buttons from $D0/$D1
        0xA9, 0xFF,              # LDA #$FF
        0x85, SWCHA_LATCH,       # STA $CF     all bits set = nothing pressed
        0xA9, 0x00,              # LDA #$00
        0x85, INPT0_LATCH,       # STA $D0
        0x85, INPT1_LATCH,       # STA $D1
        0x60,                    # RTS
    ])
    return bytes(w)


def main():
    rom = io.open(SRC, "rb").read()
    out = patch(rom)
    d = os.path.dirname(OUT)
    if d and not os.path.isdir(d):
        os.makedirs(d)
    io.open(OUT, "wb").write(out)
    changed = sum(1 for a, b in zip(rom, out) if a != b)
    print("wrote %s" % OUT)
    print("  %d bytes differ from the original" % changed)


if __name__ == "__main__":
    main()
