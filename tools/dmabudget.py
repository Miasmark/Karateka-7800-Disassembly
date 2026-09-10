#!/usr/bin/env python3
"""
What MARIA leaves you: the 7800's cycle budget, for a given screen.

    python tools/dmabudget.py --uniform 12,16,4,8
    python tools/dmabudget.py --zone 16:2@8 --zone 16:6@16 --zone 16:0@0
    python tools/dmabudget.py --uniform 12,16,4,8 --afford

MARIA draws by DMA and halts the 6502 while it does, so on the 7800 "how much
can my game compute" is a question about how much it is drawing. Every other
machine of the era lets you answer that from a manual; here the honest answer
was a shrug, so these numbers were measured.

HOW THEY WERE MEASURED
    A cartridge that spins a fixed-cost loop for the whole visible period and
    publishes the iteration count each frame. Build it once per display-list
    shape, change nothing but the display lists, and the difference in the
    count is the DMA cost. The loop was calibrated by running it again with
    extra NOPs: the fitted cost came out at 14.020 cycles per iteration
    against 14.016 counted by hand, and the measured window at exactly 241.0
    scanlines of 114.00 cycles.

HOLEY DMA, and what it is worth
    A zone can tell MARIA to suppress graphics fetches from part of memory, so
    one display-list entry can span a region that is mostly empty. The saving
    is large and now modelled: `h` on a zone. Measured, it is exactly the byte
    cost and nothing else -- the objects still pay for their display-list
    entries, and their pixels become free. Two 20-byte objects over 192
    scanlines went from 1,412 iterations a frame to 1,825, which is 0.753
    cycles a byte against the 0.744 charged here.

    Which addresses are suppressed was measured rather than looked up: with
    the 16K bit set, a fetch is dropped when **address bit 12 is set**
    ($D000 and $F000 free; $C000 and $E000 pay in full). The 8K bit made no
    difference anywhere in $C000-$FFFF, so this tool does not model it, and
    does not pretend to know what it does.

WHAT DOES NOT COST WHAT YOU MIGHT THINK
    Zone height does not matter: the same objects in 8-scanline and
    16-scanline zones cost within 0.2% of each other. Nor does where the
    graphics live -- fetching them from RAM measures identically to fetching
    them from ROM, to the iteration.

ACCURACY
    Typically well under 1%, and 1.5% at worst across the holey and
    display-interrupt cases added later. The one systematic error is on
    single-byte-wide objects, about 2.5%, where the linear model UNDER-states
    the cost -- so a screen full of very narrow objects reads optimistic.

    A caution about one number that is NOT in this model. Measuring
    Ballblazer's own screen off an emulator trace suggested MARIA was taking
    70.3 cycles a scanline where this predicts 39.6. That measurement is not
    trusted and no correction was made for it: the cycle total behind it
    under-counts taken branches, and it compared two runs that had already
    diverged. Every controlled test of the difference it blamed -- zone
    height, graphics in RAM, holey DMA, display interrupts -- came back
    showing the model right. If you find a real screen this under-states,
    that would be worth knowing; nothing here demonstrates one.

    Seventeen configurations were fitted (objects per zone, object width, zone
    count, zone height, 4- versus 5-byte entries), then the model was checked
    by PREDICTING four shapes it had never seen. All four landed within 0.3%.
    Worst residual anywhere: 56 cycles, which is 0.2% of a frame.

    These are MAME's timings. MAME's 7800 DMA model is good enough that the
    constants fall out as round numbers in MARIA colour clocks -- a graphics
    byte costs 2.98, which is the documented 3 -- but that is corroboration,
    not silicon.
"""
import argparse
import sys

# CPU cycles, NTSC, measured as described above. In MARIA colour clocks
# (4 per CPU cycle) these are 22.5, 6.7, 8.3, 3.0 and 1.9 -- the graphics byte
# landing on the documented 3 is the main reason to trust the rest.
PER_LINE  = 5.633    # a scanline inside any zone, even an empty one
PER_ZONE  = 1.678    # the DLL fetch at a zone boundary
PER_OBJ   = 2.081    # reading one 4-byte display-list entry, per scanline
PER_BYTE  = 0.744    # one graphics byte, per scanline
FIVE_XTRA = 0.483    # a 5-byte entry costs this much more than a 4-byte one
DLI_COST  = 16.6     # one display interrupt: MARIA's signal plus the 6502's
                     # own entry and exit. Measured by toggling the bit on 24
                     # zones and on 12, which agreed at 16.9 and 16.3.

REGIONS = {                      # lines/frame, CPU Hz, frames/sec
    "ntsc": (262, 1789772.5, 59.9224),
    "pal":  (312, 1773447.0, 49.8607),
}
MAX_ZONE_LINES = 16              # the DLL offset field is 4 bits: lines-1 <= 15


class Zone(object):
    def __init__(self, lines, count, width, five=False, chars=0,
                 holey=False, dli=False):
        # chars: 0 = direct mode; 1 or 2 = character mode, that many bytes per
        # character. In character mode `width` counts CHARACTERS, and each one
        # costs a fetch from the character list plus its own data bytes.
        # holey: this zone's graphics sit in a region holey DMA suppresses,
        # so MARIA pays for the display-list entry and fetches no pixels.
        # Measured: the saving is exactly the byte cost, 0.753 per byte
        # against the 0.744 charged here.
        self.lines, self.count, self.width = lines, count, width
        self.five, self.chars = five, chars
        self.holey, self.dli = holey, dli

    def cycles(self):
        bytes_per_obj = 0 if self.holey else self.width
        if self.chars:
            per_obj = (PER_OBJ + FIVE_XTRA
                       + bytes_per_obj * (1 + self.chars) * PER_BYTE)
        else:
            per_obj = (PER_OBJ + bytes_per_obj * PER_BYTE
                       + (FIVE_XTRA if self.five else 0))
        return (self.lines * (PER_LINE + per_obj * self.count) + PER_ZONE
                + (DLI_COST if self.dli else 0))

    def label(self):
        tag = ("".join(x for x, on in (("holey", self.holey), ("dli", self.dli))
                       if on))
        tag = "  " + tag if tag else ""
        if self.chars:
            return "%2d lines x %2d obj @ %2d chars (%d b/char)%s" % (
                self.lines, self.count, self.width, self.chars, tag)
        return "%2d lines x %2d obj @ %2d bytes%s%s" % (
            self.lines, self.count, self.width,
            "  (5-byte)" if self.five else "", tag)


def parse_zone(spec):
    """LINES:COUNT@WIDTH, with an optional suffix.

        (none)  direct mode, 4-byte entry
        5       direct mode, 5-byte entry
        c       character mode, 1 byte per character  (CTRL bit 4 clear)
        c2      character mode, 2 bytes per character (CTRL bit 4 SET)
        h       the zone's graphics are in a region holey DMA suppresses,
                so its objects cost their headers and no pixels
        d       the zone raises a display interrupt

    In character mode WIDTH counts characters, not bytes.
    """
    holey = dli = False
    while spec and spec[-1] in "hd":
        if spec[-1] == "h":
            holey = True
        else:
            dli = True
        spec = spec[:-1]
    chars = 0
    if spec.endswith("c2"):
        chars, spec = 2, spec[:-2]
    elif spec.endswith("c"):
        chars, spec = 1, spec[:-1]
    five = spec.endswith("5")
    if five:
        spec = spec[:-1]
    try:
        lines, rest = spec.split(":")
        count, width = rest.split("@")
        return Zone(int(lines), int(count), int(width), five, chars,
                    holey, dli)
    except ValueError:
        raise SystemExit("bad --zone %r: want LINES:COUNT@WIDTH, e.g. 16:4@8"
                         % spec)


def report(zones, region, afford):
    lines_total, hz, fps = REGIONS[region]
    per_line_cycles = hz / fps / lines_total
    frame = hz / fps

    drawn = sum(z.lines for z in zones)
    dma = sum(z.cycles() for z in zones)
    left = frame - dma

    print("region            %s, %d scanlines/frame, %.2f cycles/scanline"
          % (region.upper(), lines_total, per_line_cycles))
    print("frame budget      %8.0f cycles" % frame)
    print("")
    bad = [z for z in zones if z.lines > MAX_ZONE_LINES]
    if bad:
        print("  ** %d zone(s) taller than %d lines. The DLL offset field is"
              % (len(bad), MAX_ZONE_LINES))
        print("     four bits, so lines-1 must fit in 0-15. MARIA will draw")
        print("     something, but not what you asked for.")
        print("")
    print("  %-34s %10s" % ("zone", "cycles"))
    for i, z in enumerate(zones):
        print("  %2d  %-30s %10.0f" % (i, z.label(), z.cycles()))
    print("  %-34s %10.0f" % ("total DMA", dma))
    print("")
    print("drawn scanlines   %8d  of %d" % (drawn, lines_total))
    print("MARIA takes       %8.0f cycles  (%.1f%% of the frame)"
          % (dma, 100.0 * dma / frame))
    print("you get           %8.0f cycles  (%.1f%%)" % (left, 100.0 * left / frame))
    print("                  %8.0f cycles per scanline of game logic, averaged"
          % (left / lines_total))
    if left < 0:
        print("")
        print("OVER BUDGET. MARIA does not skip work to let the 6502 finish --")
        print("the frame simply arrives with your logic unfinished.")
    elif left < frame * 0.25:
        print("")
        print("Under a quarter of the frame left. Shipping games at this point")
        print("move work off the main loop: fewer objects per zone, narrower")
        print("ones, or a zone that is empty for part of the screen.")

    if afford:
        print("")
        # Two different questions, and reporting only the first invites a
        # reader to take it for the second. A band across the whole screen and
        # a sprite in one zone differ by the number of zones they touch, which
        # here is a factor of twenty-five.
        drawn = sum(z.lines for z in zones)
        tall = max((z.lines for z in zones), default=16)
        print("")
        print("what the leftover buys, by object width in bytes:")
        print("   %-8s %14s %16s" % ("width", "full-height", "one %d-line zone" % tall))
        for w in (1, 2, 4, 8, 16):
            band = drawn * (PER_OBJ + w * PER_BYTE)
            one = tall * (PER_OBJ + w * PER_BYTE)
            print("   %-8d %14d %16d"
                  % (w, max(0, int(left / band)), max(0, int(left / one))))
        print("   full-height counts objects present in EVERY zone, spanning")
        print("   all %d drawn scanlines. A sprite is the other column." % drawn)


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().split("\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog=__doc__)
    ap.add_argument("--zone", action="append", default=[], metavar="L:C@W",
                    help="one zone: LINES:COUNT@WIDTH, repeatable. Suffixes: "
                         "5 a 5-byte entry; c/c2 character mode with 1 or 2 "
                         "bytes per character (WIDTH then counts characters); "
                         "h graphics in a holey-suppressed region; d the zone "
                         "raises a display interrupt")
    ap.add_argument("--uniform", metavar="ZONES,LINES,COUNT,WIDTH",
                    help="shorthand for identical zones, e.g. 12,16,4,8")
    ap.add_argument("--region", choices=sorted(REGIONS), default="ntsc")
    ap.add_argument("--afford", action="store_true",
                    help="also report how many more objects the leftover fits")
    args = ap.parse_args()

    zones = [parse_zone(z) for z in args.zone]
    if args.uniform:
        try:
            n, lines, count, width = [int(x) for x in args.uniform.split(",")]
        except ValueError:
            raise SystemExit("bad --uniform: want ZONES,LINES,COUNT,WIDTH")
        zones += [Zone(lines, count, width) for _ in range(n)]
    if not zones:
        ap.error("give it a screen: --uniform or one or more --zone")
    report(zones, args.region, args.afford)
    return 0


if __name__ == "__main__":
    sys.exit(main())
