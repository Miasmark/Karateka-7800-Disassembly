#!/usr/bin/env python3
"""
Rebuild a complete .a78 cartridge image from the generated listings.

Bank N comes from whichever listing covers it:
    a fixed region -> src/f<bank>.asm, or src/rom.asm for an unbanked cart
    a window bank  -> src/bN.asm

The 128-byte a78 header is copied from the reference ROM.

Usage: python build.py <reference.a78> [-d src] [-o build/rebuilt.a78]
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from disasm import Cart, BANK_SIZE
from asm import Assembler


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("rom")
    ap.add_argument("-d", "--dir", default="src")
    ap.add_argument("--low", choices=["none", "ram", "bank6", "rom"],
                    help="what sits at $4000-$7FFF, when the header is wrong")
    ap.add_argument("--mapper", choices=["linear", "supergame", "absolute"],
                    help="override the mapper the header declares")
    ap.add_argument("-o", "--out", default="build/rebuilt.a78")
    args = ap.parse_args()

    cart = Cart(args.rom, mapper=args.mapper, low=args.low)
    banks = {}
    for name in sorted(os.listdir(args.dir)):
        if not name.endswith(".asm"):
            continue
        space = name[:-4]
        b = cart.bank_of(space)
        # prefer the listing whose .org matches where the bank really lives
        if b in banks and space.startswith("b"):
            continue
        data = Assembler().assemble(
            open(os.path.join(args.dir, name), encoding="utf-8").read().splitlines())
        want = cart.size_of(space)
        if len(data) != want:
            print("  %s: %d bytes, expected %d" % (space, len(data), want))
            return 1
        banks[b] = data

    missing = [b for b in range(cart.nbanks) if b not in banks]
    if missing:
        print("missing listings for banks: %s" % missing)
        return 1

    image = b"".join(banks[b] for b in range(cart.nbanks))
    out = (cart.header_bytes or b"") + image
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    open(args.out, "wb").write(out)

    ref = open(args.rom, "rb").read()
    same = out == ref
    print("wrote %s (%d bytes)" % (args.out, len(out)))
    print("identical to reference ROM:", "YES" if same else "NO")
    if not same:
        n = min(len(out), len(ref))
        i = next((k for k in range(n) if out[k] != ref[k]), n)
        print("  first difference at file offset $%05X" % i)
    return 0 if same else 1


if __name__ == "__main__":
    sys.exit(main())
