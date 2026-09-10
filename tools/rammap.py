#!/usr/bin/env python3
"""
Emit RAM_MAP.md: every RAM/IO address the traced code touches, how often, from
which banks, and the name it has been given in annotations.json.

Usage: python rammap.py <rom.a78> -c annotations.json -o RAM_MAP.md
"""
import argparse
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import a7800
from disasm import Cart, Config, Analyzer, BANK_SIZE, parse_loc

BANDS = [
    (0x0000, 0x001F, "TIA registers"),
    (0x0020, 0x003F, "MARIA registers"),
    (0x0040, 0x00FF, "zero page RAM (mirror of $2040-$20FF)"),
    (0x0100, 0x013F, "TIA/MARIA mirror"),
    (0x0140, 0x01FF, "stack + display list list (mirror of $2140-$21FF)"),
    (0x0200, 0x027F, "TIA/MARIA mirror"),
    (0x0280, 0x02FF, "RIOT I/O"),
    (0x0480, 0x04FF, "RIOT RAM"),
    (0x1800, 0x1FFF, "RAM"),
    (0x2000, 0x27FF, "RAM"),
    (0x2800, 0x3FFF, "RAM mirror"),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("rom")
    ap.add_argument("-c", "--config")
    ap.add_argument("-o", "--out", default="RAM_MAP.md")
    args = ap.parse_args()

    cart = Cart(args.rom)
    cfg = Config(args.config)
    an = Analyzer(cart, cfg)
    for b in cfg.blocks:
        s, a = parse_loc(b["loc"])
        e = parse_loc(b["end"])[1] if "end" in b else a + b.get("len", 1)
        for x in range(a, e):
            an.forced_data.add((s, x))

    last = cart.rom[cart.fixed_hi * BANK_SIZE:]
    entries = []
    for o in (0x3FFA, 0x3FFC, 0x3FFE):
        v = last[o] | (last[o + 1] << 8)
        sp = cart.space_of(v, None)
        if sp:
            entries.append((sp, v, None))
    entries += [(s, a, None) for (s, a) in cfg.entries]
    an.run(entries)
    for _ in range(8):
        new = []
        for tgt, site in an.scan_ram_vectors(0x2135, 0x2136):
            sp = cart.space_of(tgt, None)
            if sp and (sp, tgt) not in an.code:
                new.append((sp, tgt, None))
        if not new:
            break
        an.run(new)

    banks = defaultdict(set)
    for addr, refs in an.ramrefs.items():
        for (s, a) in refs:
            banks[addr].add(s)

    out = ["# Midnight Mutants -- RAM and I/O map", "",
           "Every address below $4000 referenced by code the tracer reached.",
           "`refs` counts distinct referencing instructions. Names come from",
           "`annotations.json`; unnamed entries are still unidentified.", ""]
    named = sum(1 for a in an.ramrefs if a in cfg.ram or a7800.HW.get(a))
    out += ["%d distinct addresses referenced, %d named." % (len(an.ramrefs), named), ""]

    for lo, hi, title in BANDS:
        rows = [(a, r) for a, r in an.ramrefs.items() if lo <= a <= hi]
        if not rows:
            continue
        out += ["## $%04X-$%04X  %s" % (lo, hi, title), "",
                "| addr | name | refs | banks |", "|---|---|---|---|"]
        for a_, refs in sorted(rows):
            nm = cfg.ram.get(a_) or a7800.HW.get(a_) or ""
            out.append("| `$%04X` | %s | %d | %s |"
                       % (a_, "`%s`" % nm if nm else "",
                          len(refs), ", ".join(sorted(banks[a_]))))
        out.append("")

    with open(args.out, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    print("wrote %s (%d addresses)" % (args.out, len(an.ramrefs)))


if __name__ == "__main__":
    main()
