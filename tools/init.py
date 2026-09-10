#!/usr/bin/env python3
"""
Start work on a cartridge: write the annotations file the disassembler reads.

    python tools/init.py game.a78 [-o annotations.json]

The first hour on a new game is always the same: read the header, find the
vectors, guess which bank is really mapped, run the disassembler, see how much
of it got reached. This does that once and writes down what it found, so the
file you edit starts with the facts already in it instead of an empty skeleton.

It records only what it can read off the cartridge -- vectors, mapper, chips,
banks. Everything it is unsure about is written as a note saying so rather than
as a guess, because a guess in an annotations file is indistinguishable from a
finding once it is on disk.
"""
import argparse
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cart as cart_module


def coverage_of(rom, config, low=None, mapper=None):
    """Disassemble and report bytes reached per space, or None if it fails."""
    import tempfile
    import subprocess
    here = os.path.dirname(os.path.abspath(__file__))
    out = tempfile.mkdtemp(prefix="init-")
    cmd = [sys.executable, os.path.join(here, "disasm.py"), rom, "-o", out]
    if config:
        cmd += ["-c", config]
    if low:
        cmd += ["--low", low]
    if mapper:
        cmd += ["--mapper", mapper]
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if p.returncode != 0:
        return None
    return p.stdout.decode("utf-8", "replace")


def build(cart, path):
    """The starter file: facts as values, uncertainties as notes."""
    vectors = cart.vectors()
    spaces = cart.spaces()

    # The vectors are the one set of entry points every cartridge has, and the
    # tracer needs at least one. NMI matters more than it looks on this machine:
    # MARIA's display interrupt goes through it, so the handler is usually where
    # the graphics code starts.
    entries, labels = [], {}
    home = cart.space_of(0xFFFA, bank=cart.nbanks - 1) or spaces[-1]
    for name, addr in sorted(vectors.items()):
        sp = cart.space_of(addr, bank=cart.nbanks - 1)
        if sp is None:
            continue
        loc = "%s:%04X" % (sp, addr)
        if loc not in entries:
            entries.append(loc)
            labels[loc] = "ENTRY_%s" % name.title()

    notes = {}
    info = cart.info or {}
    head = ["%s" % os.path.basename(path),
            "mapper %s, %d KB, %s"
            % (cart.map.name, len(cart.rom) // 1024, info.get("region", "?"))]
    pokeys = cart.pokeys()
    if pokeys:
        head.append("POKEY at %s -- so the music is not on the TIA"
                    % ", ".join("$%04X" % b for b in pokeys))
    else:
        head.append("no POKEY declared; audio is the TIA's two voices")
    notes[home] = "\n".join(head + [
        "",
        "Vectors: " + "  ".join("%s $%04X" % (k, v)
                                for k, v in sorted(vectors.items())),
        "",
        "Written by init.py. Everything here is read from the cartridge; the",
        "judgement calls are yours to add.",
    ])

    doc = {
        "_README": [
            "Started by init.py from %s." % os.path.basename(path),
            "",
            "Every human judgement about this ROM lives in this file; the",
            "generated listings are disposable, so renaming is free.",
            "",
            "What to do next, roughly in order:",
            "",
            "  1. python tools/disasm.py %s -c %s"
            % (os.path.basename(path), "annotations.json"),
            "     Read the coverage it reports. Low coverage is normal at",
            "     first and is the number you are trying to move.",
            "",
            "  2. Every unresolved indirect jump it reports is a hole. Add the",
            "     target to `entries`, or the pointer pair to `ram_vectors` if",
            "     the handler is installed into RAM.",
            "",
            "  3. python tools/audiotrace.py %s -c annotations.json"
            % os.path.basename(path),
            "     Finds the sound code and the tables feeding it.",
            "",
            "  4. Name things in `labels` as soon as you know what they are.",
            "",
            "See docs/method.md for the working order and docs/pitfalls.md for",
            "the traps that each cost real time.",
        ],
        "entries": entries,
        "ram_vectors": [],
        "bankat": {},
        "labels": labels,
        "ram": {},
        "comments": {},
        "headers": {},
        "blocks": [],
        "notes": notes,
    }
    return doc


def main():
    ap = argparse.ArgumentParser(
        description=__doc__.strip().split("\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("rom")
    ap.add_argument("-o", "--out", default="annotations.json")
    ap.add_argument("--low", choices=["none", "ram", "bank6", "rom"])
    ap.add_argument("--mapper", choices=["linear", "supergame", "absolute"])
    ap.add_argument("--force", action="store_true",
                    help="overwrite an existing annotations file")
    args = ap.parse_args()

    try:
        cart = cart_module.Cart(args.rom, mapper=args.mapper, low=args.low)
    except (cart_module.UnknownMapper, cart_module.UnknownSpace, IOError) as e:
        sys.stderr.write("%s\n" % e)
        return 2

    # An annotations file is hand-written work. Never overwrite one silently.
    if os.path.exists(args.out) and not args.force:
        sys.stderr.write(
            "%s already exists. That file is where all the hand work lives, so "
            "it is not overwritten without --force.\n" % args.out)
        return 3

    doc = build(cart, args.rom)
    with io.open(args.out, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2)
        f.write("\n")

    print(cart.describe())
    print()
    print("wrote %s" % args.out)
    print("  %d entry point%s from the vectors: %s"
          % (len(doc["entries"]), "" if len(doc["entries"]) == 1 else "s",
             ", ".join(doc["entries"])))

    out = coverage_of(args.rom, args.out, args.low, args.mapper)
    if out is None:
        print("  the disassembler did not run cleanly on this image yet -- try")
        print("  --mapper or --low if the header understates the mapping")
    else:
        cov = [l for l in out.splitlines() if "%" in l and ":" not in l[:6]]
        for line in cov[:12]:
            print("  %s" % line.strip())

    print()
    # 7800 filenames are full of spaces and parentheses, so quote the path --
    # an unquoted one is a command that looks copyable and is not.
    print('next:  python tools/disasm.py "%s" -c "%s"' % (args.rom, args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
