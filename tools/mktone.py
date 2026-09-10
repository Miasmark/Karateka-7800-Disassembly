#!/usr/bin/env python3
"""Build a 7800 cartridge that holds one POKEY setting forever.

    python tools/mktone.py out.a78 <audctl> <audf16>

A controlled oracle for the sound model. Validating the 16-bit divider against
a real game does not work -- every attempt fights the game: other channels
sounding, settings held for four frames at a time, dividers up in the
ultrasonic. Ballblazer, which uses no unmodelled feature at all, scored 64% by
that method, so the method was measuring itself.

These cartridges write one setting and loop forever, so the only thing in the
recording is the thing under test:

    python tools/mktone.py t.a78 0x50 1000
    mame a7800 -cart t.a78 -sound none -wavwrite t.wav -video none -str 4

Measure the fundamental in `t.wav` and compare it with
`tracker.pokey_rate(...) / 2`. That is how the 16-bit pairs were confirmed to
0.00 cents and the high-pass filters peak for peak; see docs/audio.md.
"""
import struct
import sys

POKEY = 0x4000          # cart type $0001 -- POKEY at $4000


def header(size, title="POKEY tone test"):
    h = bytearray(128)
    h[0] = 1
    h[1:10] = b"ATARI7800"
    t = title.encode("latin1")[:32]
    h[17:17 + len(t)] = t
    h[49:53] = struct.pack(">I", size)
    h[53:55] = struct.pack(">H", 0x0001)          # POKEY @ $4000
    h[55] = 1                                     # joystick
    h[56] = 1
    h[57] = 0                                     # NTSC
    h[100:128] = b"ACTUAL CART DATA STARTS HERE"
    return bytes(h)


def rom(audctl, audf_lo, audc_lo, audf_hi, audc_hi, lo_ch=0, hi_ch=1):
    """16K at $C000-$FFFF: set the registers, then loop forever."""
    code = bytearray()

    def w(val, addr):
        code.extend([0xA9, val & 0xFF])                       # LDA #val
        code.extend([0x8D, addr & 0xFF, (addr >> 8) & 0xFF])  # STA addr

    code.extend([0x78])                       # SEI
    code.extend([0xD8])                       # CLD
    code.extend([0xA2, 0xFF, 0x9A])           # LDX #$FF / TXS
    w(0x00, 0x0021)                           # MARIA off: nothing to draw
    w(0x03, POKEY + 0x0F)                     # SKCTL: out of reset
    # Volumes off everywhere first, so only the channel under test sounds.
    for ch in range(4):
        w(0x00, POKEY + ch * 2 + 1)
        w(0x00, POKEY + ch * 2)
    w(audctl, POKEY + 0x08)                   # AUDCTL
    w(audf_lo, POKEY + lo_ch * 2)
    w(audf_hi, POKEY + hi_ch * 2)
    w(audc_lo, POKEY + lo_ch * 2 + 1)
    w(audc_hi, POKEY + hi_ch * 2 + 1)
    here = 0xC000 + len(code)
    code.extend([0x4C, here & 0xFF, (here >> 8) & 0xFF])      # JMP *

    body = bytearray(b"\xEA" * 0x4000)
    body[0:len(code)] = code
    body[0x3FFA:0x3FFC] = struct.pack("<H", 0xC000)   # NMI
    body[0x3FFC:0x3FFE] = struct.pack("<H", 0xC000)   # RESET
    body[0x3FFE:0x4000] = struct.pack("<H", 0xC000)   # IRQ
    return bytes(body)


def build(path, audctl, audf16, audc=0xA8, lo_ch=0, hi_ch=1):
    """A 16-bit pair: low byte in the low channel, high byte in the high one."""
    body = rom(audctl, audf16 & 0xFF, 0x00, (audf16 >> 8) & 0xFF, audc,
               lo_ch, hi_ch)
    with open(path, "wb") as f:
        f.write(header(len(body)))
        f.write(body)
    return path


def build8(path, audctl, audf, audc=0xA8, ch=0):
    """A plain 8-bit channel, for the control measurement."""
    body = rom(audctl, audf, audc, 0x00, 0x00, ch, (ch + 1) % 4)
    with open(path, "wb") as f:
        f.write(header(len(body)))
        f.write(body)
    return path


def main():
    import argparse
    ap = argparse.ArgumentParser(
        description=__doc__.strip().split("\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__)
    ap.add_argument("out", help="the .a78 to write")
    ap.add_argument("audctl", type=lambda v: int(v, 0),
                    help="AUDCTL value, e.g. 0x50 for a 16-bit pair 1+2 on "
                         "the 1.79 MHz clock")
    ap.add_argument("audf", type=lambda v: int(v, 0),
                    help="the divider: 16 bits when audctl joins a pair, "
                         "8 otherwise")
    ap.add_argument("--audc", type=lambda v: int(v, 0), default=0xA8,
                    help="AUDC for the sounding channel (default $A8: pure "
                         "tone, volume 8)")
    ap.add_argument("--pair", choices=["12", "34"], default="12",
                    help="which channel pair to use (default 12)")
    ap.add_argument("--eight", action="store_true",
                    help="one plain 8-bit channel instead of a 16-bit pair")
    args = ap.parse_args()

    lo, hi = (0, 1) if args.pair == "12" else (2, 3)
    if args.eight:
        build8(args.out, args.audctl, args.audf, args.audc, ch=lo)
    else:
        build(args.out, args.audctl, args.audf, args.audc, lo_ch=lo, hi_ch=hi)
    print("%s  AUDCTL $%02X  AUDF %d  AUDC $%02X"
          % (args.out, args.audctl, args.audf, args.audc))
    return 0


if __name__ == "__main__":
    sys.exit(main())
