#!/usr/bin/env python3
"""
First look at an unknown 7800 cartridge: header, layout, and where the
interesting things are.

Run this before anything else. It answers the questions that decide how the
rest of the work goes -- how the ROM is banked, which banks hold code and which
hold graphics, whether the text is plain ASCII or a custom alphabet, and where
the entry points are.

Usage:
  python survey.py <rom.a78> [--strings] [--min 4]
"""
import argparse
import collections
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cart as cartlib

BANK = 0x4000


def entropy(b):
    if not b:
        return 0.0
    c = collections.Counter(b)
    n = len(b)
    return -sum((v / n) * math.log2(v / n) for v in c.values())


def classify(block):
    """A rough guess at what a block of ROM holds.

    Entropy separates the three things a cartridge is mostly made of. 6502 code
    lands near 6 bits/byte because the opcode distribution is lumpy but wide;
    bitmap graphics run higher, often above 7, because pixel data uses the whole
    byte range evenly; tables and filler run lower. It is a hint, not a verdict
    -- confirm by disassembling.
    """
    e = entropy(block)
    zero = block.count(0) / len(block)
    ff = block.count(0xFF) / len(block)
    if zero > 0.85 or ff > 0.85:
        return "empty/filler", e
    if e > 7.2:
        return "graphics or compressed", e
    if e > 5.2:
        return "code, probably", e
    return "tables or sparse data", e


def strings(block, minlen=4):
    out, cur, start = [], [], 0
    for i, ch in enumerate(block):
        if 32 <= ch < 127:
            if not cur:
                start = i
            cur.append(chr(ch))
        else:
            if len(cur) >= minlen:
                out.append((start, "".join(cur)))
            cur = []
    if len(cur) >= minlen:
        out.append((start, "".join(cur)))
    return out


def high_bit_strings(block, minlen=4):
    """Text stored with bit 7 set, which plain string tools miss.

    Plenty of 7800 games hold their text this way, or in a custom alphabet
    where 'A' is 1 rather than 65. If the plain scan finds nothing and this one
    finds runs, the game has its own character encoding and the next job is
    working out the mapping from the character-set graphics.
    """
    stripped = bytes(b & 0x7F for b in block)
    return strings(stripped, minlen)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("rom")
    ap.add_argument("--strings", action="store_true",
                    help="dump the readable text found in each bank")
    ap.add_argument("--min", type=int, default=4, help="shortest string to report")
    args = ap.parse_args()

    raw = open(args.rom, "rb").read()
    print("=" * 74)
    try:
        c = cartlib.Cart(args.rom)
    except cartlib.UnknownMapper as e:
        info = cartlib.read_header(raw)
        print("%s\n" % os.path.basename(args.rom))
        if info:
            print("  title       %s" % (info["title"] or "-"))
            print("  cart type   $%04X  %s" % (info["cart_type"],
                                               ", ".join(info["flags"]) or "-"))
        print("\n  This image cannot be laid out:\n    %s" % e)
        return 1
    print(c.describe())

    if c.info:
        print("  controllers %s / %s" % (c.info["controller1"], c.info["controller2"]))
        save = [n for n, on in (("high score cart", c.info["save_hsc"]),
                                ("SaveKey", c.info["save_savekey"])) if on]
        if save:
            print("  saves to    %s" % ", ".join(save))

    if c.nbanks > 1:
        pr = c.probe_fixed_high()
        top = pr["ranked"][0] if pr["ranked"] else None
        if top and top["score"] >= 6:
            print("  $C000 bank  probe says bank %d (reset $%04X, %s)%s"
                  % (top["bank"], top["reset"], top["bytes"],
                     "" if pr["agrees"] else "  <-- DISAGREES with the default"))
        else:
            print("  $C000 bank  probe inconclusive; using the default, bank %d"
                  % pr["default"])

    print("\n%-6s %10s %8s %7s %7s  %s"
          % ("bank", "file off", "entropy", "zero%", "ff%", "likely contents"))
    nb = max(1, len(c.rom) // BANK)
    for i in range(nb):
        b = c.rom[i * BANK:(i + 1) * BANK]
        if not b:
            continue
        what, e = classify(b)
        head = 128 if c.header_bytes else 0
        print("%-6d %10d %8.3f %6.1f%% %6.1f%%  %s"
              % (i, head + i * BANK, e, b.count(0) / len(b) * 100,
                 b.count(0xFF) / len(b) * 100, what))

    # Text is the fastest way into an unfamiliar ROM: it names the screens, the
    # items and the messages, and each string is an anchor for the code that
    # prints it.
    print()
    plain = sum(len(strings(c.rom[i * BANK:(i + 1) * BANK], args.min))
                for i in range(nb))
    high = sum(len(high_bit_strings(c.rom[i * BANK:(i + 1) * BANK], args.min))
               for i in range(nb))
    print("readable text: %d plain runs, %d more if bit 7 is stripped" % (plain, high))
    if plain < 5 and high < 5:
        print("  Neither, so the game almost certainly uses its own alphabet.")
        print("  Find the character-set graphics first and read the mapping off")
        print("  them -- see docs/graphics.md.")

    if args.strings:
        for i in range(nb):
            b = c.rom[i * BANK:(i + 1) * BANK]
            hits = strings(b, args.min)
            if not hits:
                continue
            print("\n-- bank %d --" % i)
            for off, s in hits[:40]:
                print("   +%04X  %r" % (off, s))
            if len(hits) > 40:
                print("   ... %d more" % (len(hits) - 40))
    return 0


if __name__ == "__main__":
    sys.exit(main())
