#!/usr/bin/env python3
"""
Round-trip check: reassemble every generated listing and compare it against the
original ROM bank, byte for byte.

Usage: python verify.py <rom.a78> [-d src]
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from disasm import Cart
from asm import Assembler, AsmError


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("rom")
    ap.add_argument("-d", "--dir", default="src")
    ap.add_argument("--low", choices=["none", "ram", "bank6", "rom"],
                    help="what sits at $4000-$7FFF, when the header is wrong")
    ap.add_argument("--mapper", choices=["linear", "supergame", "absolute"],
                    help="override the mapper the header declares")
    args = ap.parse_args()

    cart = Cart(args.rom, mapper=args.mapper, low=args.low)
    ok = True
    for name in sorted(os.listdir(args.dir)):
        if not name.endswith(".asm"):
            continue
        space = name[:-4]
        path = os.path.join(args.dir, name)
        want = cart.slice(space, cart.base_of(space), cart.size_of(space))
        try:
            got = Assembler().assemble(
                open(path, encoding="utf-8").read().splitlines())
        except AsmError as e:
            print("  %-4s FAIL  %s" % (space, e))
            ok = False
            continue
        if got == want:
            print("  %-4s OK    %d bytes reassemble identically" % (space, len(got)))
        else:
            ok = False
            n = min(len(got), len(want))
            first = next((i for i in range(n) if got[i] != want[i]), n)
            print("  %-4s FAIL  size %d vs %d; first difference at +$%04X "
                  "(CPU $%04X): got %s want %s"
                  % (space, len(got), len(want), first,
                     cart.base_of(space) + first,
                     got[first:first + 6].hex(" ") if first < len(got) else "-",
                     want[first:first + 6].hex(" ")))
    print("\nROUND-TRIP", "PASSED" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
