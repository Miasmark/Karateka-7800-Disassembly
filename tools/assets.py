#!/usr/bin/env python3
"""
Find a cartridge's music and artwork, and mark them up for the other tools.

    python tools/assets.py game.a78 [-c annotations.json]
        [--blocks out.json] [--manifest assets.json]

The disassembler tells you what is code. What is left is data, and data is not
one thing: it is graphics, music tables, text, and a lot of bytes nobody has
identified yet. Reading it as hex is the slow way to find out which.

This does what `audiotrace.py` does for sound, and the same trick for pictures.
Every store to a MARIA register is a place the hardware is being pointed at
something; trace back through straight-line code to whatever supplied the
address and you have found a display list, a character base, or a sprite table.
Then walk the display lists to the graphics they name.

What comes out:

  * `--blocks` writes annotation blocks, ready to merge into an annotations
    file, so the disassembler stops listing artwork as instructions and starts
    drawing it. Graphics blocks carry `"gfx": true`.
  * `--manifest` writes a description of every asset found, for the tools that
    work on assets rather than on listings -- `gfx.py` for a sprite sheet,
    `songfmt.py` for a song.

What it will not do is guess. An address that reaches a MARIA register is
evidence; a run of high-entropy bytes is not, and is reported as unidentified
rather than dressed up as a sprite. The confidence of every find is in the
output, and the ones that came from a display list are the ones to trust.

Some unidentified runs are unidentifiable on purpose. Homebrew commonly ships
artwork LZ4-compressed with `lz4raw`, which strips the header precisely so the
6502 does not have to parse one -- so there is no signature to match and the
target size lives in the code rather than the data. A run that looks like noise
and is never pointed at may be compressed rather than unused.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import a7800
import audiotrace
import cart as cart_module
import dlwalk

# MARIA registers worth watching, by offset in $20-$3F.
MARIA_POINTERS = {0x2C: "DPPH", 0x30: "DPPL", 0x34: "CHARBASE"}
MARIA_ALL = dict((off, name) for off, name in a7800.MARIA.items()) \
    if hasattr(a7800, "MARIA") else {}


def maria_map():
    """Absolute addresses of the MARIA pointer registers, with mirrors."""
    regs = {}
    for mirror in (0x0000, 0x0100, 0x0200, 0x0300):
        for off, name in MARIA_POINTERS.items():
            regs[mirror + off] = name
    return regs


def graphics_writers(an):
    """Every store to a MARIA pointer register, and what fed it."""
    regs = maria_map()
    out = []
    for loc in sorted(an.insn):
        mn, _mode, operand, _ln = an.insn[loc]
        if mn not in audiotrace.STORES or operand is None:
            continue
        if operand not in regs:
            continue
        src = audiotrace.source_of(an, loc, audiotrace.STORES[mn])
        out.append({"at": loc, "reg": regs[operand], "src": src})
    return out


# The eight MARIA palettes, three colours each, plus the background. A display
# list entry names which palette to use; the colours themselves are registers,
# written whenever the game likes.
PALETTE_REGS = {}
for _p in range(8):
    for _c in range(1, 4):
        PALETTE_REGS[0x20 + _p * 4 + _c] = (_p, _c)
PALETTE_REGS[0x20] = (None, 0)          # BACKGRND, shared by every palette


def palette_writes(an):
    """Immediate values the code writes to MARIA's palette registers.

    This is the honest half of "which palette is this artwork drawn in". The
    colours live in registers, not in the data, and a game rewrites them per
    zone, per frame, per fade -- so no static answer is *the* answer. What can
    be recovered is the set of values the code writes as constants, which in
    practice is where most of a game's palettes come from.

    Reported per palette slot. A slot with three colours from one routine is
    very likely a real palette; a slot with fifteen is a fade, and the ones you
    want are whichever of them the artwork is on screen with.
    """
    regs = {}
    for mirror in (0x0000, 0x0100, 0x0200, 0x0300):
        for off, slot in PALETTE_REGS.items():
            regs[mirror + off] = slot
    found = {}
    for loc in sorted(an.insn):
        mn, _mode, operand, _ln = an.insn[loc]
        if mn not in audiotrace.STORES or operand is None:
            continue
        if operand not in regs:
            continue
        src = audiotrace.source_of(an, loc, audiotrace.STORES[mn])
        if not src or src[2] != "imm" or src[3] is None:
            continue
        pal, idx = regs[operand]
        found.setdefault(pal, {}).setdefault(idx, set()).add(src[3])
    out = []
    for pal in sorted(found, key=lambda x: (x is None, x)):
        slots = found[pal]
        out.append({
            "palette": pal,
            "colours": {str(i): sorted(v) for i, v in sorted(slots.items())},
            "complete": all(i in slots for i in (1, 2, 3)),
        })
    return out


def constant_pairs(writers):
    """DPPH/DPPL written as immediates near each other make a display list.

    A display-list pointer is two registers, so the address only exists once
    both halves are known. Matching them by proximity is what the code itself
    does -- the two stores are almost always adjacent.
    """
    highs, lows, out = {}, {}, []
    for w in writers:
        src = w["src"]
        if not src or src[2] != "imm" or src[3] is None:
            continue
        (sp, addr) = w["at"]
        (highs if w["reg"] == "DPPH" else lows)[addr] = (sp, src[3])
    for ha, (hsp, hv) in sorted(highs.items()):
        best = None
        for la, (lsp, lv) in lows.items():
            if lsp != hsp:
                continue
            d = abs(la - ha)
            if d < 64 and (best is None or d < best[0]):
                best = (d, lv)
        if best:
            out.append((hsp, (hv << 8) | best[1]))
    return out


def charbases(writers):
    """CHARBASE values written as immediates: the high byte of a font page."""
    out = []
    for w in writers:
        src = w["src"]
        if w["reg"] != "CHARBASE" or not src or src[2] != "imm":
            continue
        if src[3] is not None:
            out.append((w["at"][0], src[3] << 8))
    return sorted(set(out))


def source_for(cart, space):
    """A dlwalk.Source over one cartridge space."""
    base = cart.base_of(space)
    return dlwalk.Source(cart.slice(space, base, cart.size_of(space)), base)


def walk_lists(cart, space, addr, seen):
    """Decode a display list and collect the graphics addresses it names."""
    found = []
    if (space, addr) in seen:
        return found
    seen.add((space, addr))
    try:
        src = source_for(cart, space)
        entries = dlwalk.walk_dl(src, addr)
    except Exception:                                        # noqa: BLE001
        return found
    for e in entries:
        gfx = e.get("gfx")
        if gfx is None:
            continue
        found.append({"space": space, "addr": gfx,
                      "width": e.get("width"), "palette": e.get("palette"),
                      "from": "%s:%04X" % (space, addr)})
    return found


def from_ram_dump(cart, path, at, dll_addr, zones=25):
    """Graphics addresses named by a display list captured from a running game.

    This is not an optional extra. On this machine the display list lives in
    RAM and is rebuilt every frame, so the pointers in the ROM point at RAM and
    a static trace stops there -- correctly, because the list does not exist
    until the game runs. `probes/dumpdl.lua` snapshots RAM and prints the DLL
    address; feed both back in here and the artwork resolves.
    """
    raw = open(path, "rb").read()
    src = dlwalk.Source(raw, at)
    out = []
    try:
        zonelist = dlwalk.walk_dll(src, dll_addr, zones)
    except Exception as e:                                   # noqa: BLE001
        raise ValueError("could not read a display list list at $%04X in %s: %s"
                         % (dll_addr, os.path.basename(path), e))
    for z in zonelist:
        dl = z.get("dl")
        if dl is None:
            continue
        try:
            entries = dlwalk.walk_dl(src, dl)
        except Exception:                                    # noqa: BLE001
            continue
        for e in entries:
            gfx = e.get("gfx")
            if gfx is None or gfx < 0x4000:
                continue                     # still in RAM: not cartridge art
            cands = spaces_holding(cart, gfx)
            for sp in cands:
                out.append({"space": sp, "addr": gfx, "width": e.get("width"),
                            "palette": e.get("palette"),
                            "from": "live DL $%04X" % dl,
                            "candidates": cands})
    return out


def spaces_holding(cart, addr):
    """Which cartridge spaces could supply this CPU address.

    A windowed address belongs to whichever bank was mapped when MARIA read
    it, and the capture does not record that -- so every candidate bank is
    reported rather than one being picked. The fixed regions resolve exactly.
    """
    out = []
    for sp in cart.spaces():
        try:
            base = cart.base_of(sp)
        except Exception:                                    # noqa: BLE001
            continue
        if base <= addr < base + cart.size_of(sp):
            out.append(sp)
    return out


def data_runs(an, cart, minimum=16):
    """Every stretch the tracer never reached as code, per space."""
    runs = {}
    for sp in cart.spaces():
        size = cart.size_of(sp)
        base = cart.base_of(sp) if hasattr(cart, "base_of") else 0
        covered = bytearray(size)
        for (s2, a), (_m, _md, _o, ln) in an.insn.items():
            if s2 != sp:
                continue
            i = a - base
            for k in range(max(0, i), min(size, i + ln)):
                covered[k] = 1
        out, start = [], None
        for i in range(size):
            if not covered[i] and start is None:
                start = i
            elif covered[i] and start is not None:
                if i - start >= minimum:
                    out.append((base + start, base + i))
                start = None
        if start is not None and size - start >= minimum:
            out.append((base + start, base + size))
        runs[sp] = out
    return runs


def collect_graphics(gfx_hits, charbase_pages):
    """One entry per distinct address the hardware was pointed at.

    Not per enclosing data run: a run is just "bytes the tracer never reached",
    and a big one holds artwork and lookup tables side by side. Labelling the
    whole run by one hit inside it puts a `graphics` tag on byte tables, which
    is worse than saying nothing.
    """
    out = {}
    for g in gfx_hits:
        key = (g["space"], g["addr"])
        e = out.setdefault(key, {
            "loc": "%s:%04X" % key, "kind": "graphics",
            "width": g.get("width"), "palette": g.get("palette"),
            "refs": 0, "source": g["from"],
            "certain": len(g.get("candidates") or [1]) == 1,
            "banks": g.get("candidates") or [g["space"]],
        })
        e["refs"] += 1
    for sp, addr in charbase_pages:
        key = (sp, addr)
        out.setdefault(key, {
            "loc": "%s:%04X" % key, "kind": "graphics", "width": None,
            "palette": None, "refs": 1, "source": "CHARBASE points here",
            "certain": True, "banks": [sp],
        })
    return sorted(out.values(), key=lambda a: a["loc"])


def collect_audio(audio_tables):
    return sorted(({"loc": "%s:%04X" % (t["space"], t["addr"]),
                    "kind": "audio", "regs": sorted(t["regs"]),
                    "how": t["how"], "certain": True}
                   for t in audio_tables), key=lambda a: a["loc"])


def main():
    ap = argparse.ArgumentParser(
        description=__doc__.strip().split("\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("rom")
    ap.add_argument("-c", "--config")
    ap.add_argument("--low", choices=["none", "ram", "bank6", "rom"])
    ap.add_argument("--mapper", choices=["linear", "supergame", "absolute"])
    ap.add_argument("--blocks", help="write annotation blocks here")
    ap.add_argument("--manifest", help="write the asset manifest here")
    ap.add_argument("--ram", metavar="DUMP",
                    help="a RAM dump from probes/dumpdl.lua, so the live "
                         "display list can be followed to the artwork")
    ap.add_argument("--at", type=lambda v: int(v, 0), default=0x1800,
                    help="CPU address the --ram dump starts at")
    ap.add_argument("--dll", type=lambda v: int(v, 0),
                    help="the display-list-list address dumpdl.lua printed")
    ap.add_argument("--zones", type=int, default=25)
    ap.add_argument("--min", type=int, default=16,
                    help="ignore data runs shorter than this (default 16)")
    args = ap.parse_args()

    try:
        an = audiotrace.analyse(args.rom, args.config, args.low, args.mapper)
    except (RuntimeError, cart_module.UnknownMapper,
            cart_module.UnknownSpace) as e:
        sys.stderr.write("%s\n" % e)
        return 2
    cart = an.cart

    # --- sound: reuse the tracer that already does this properly
    audio_tables = []
    for g in audiotrace.cluster(audiotrace.find_writers(an, cart)):
        for t in audiotrace.tables_in(g):
            audio_tables.append({"space": g["space"], "addr": t["addr"],
                                 "regs": t["regs"], "how": t["how"]})

    # --- pictures: the same idea, pointed at MARIA
    gw = graphics_writers(an)
    lists = constant_pairs(gw)
    seen, gfx_hits = set(), []
    for sp, addr in lists:
        gfx_hits.extend(walk_lists(cart, sp, addr, seen))
    chars = charbases(gw)

    ram_hits = []
    if args.ram:
        if args.dll is None:
            sys.stderr.write("--ram needs --dll: the address dumpdl.lua "
                             "printed.\n")
            return 2
        try:
            ram_hits = from_ram_dump(cart, args.ram, args.at, args.dll,
                                     args.zones)
        except (ValueError, IOError) as e:
            sys.stderr.write("%s\n" % e)
            return 2
        gfx_hits.extend(ram_hits)

    runs = data_runs(an, cart, args.min)
    unreached = sum(hi - lo for spans in runs.values() for lo, hi in spans)

    gfx = collect_graphics(gfx_hits, chars)
    audio = collect_audio(audio_tables)
    palettes = palette_writes(an)
    sure = [a for a in gfx if a["certain"]]
    unsure = [a for a in gfx if not a["certain"]]

    print("%d MARIA pointer writes, %d display list pointer%s"
          % (len(gw), len(lists), "" if len(lists) == 1 else "s"))
    print("%d graphics address%s, %d audio table%s, %d bytes never reached "
          "as code\n"
          % (len(gfx), "" if len(gfx) == 1 else "es",
             len(audio), "" if len(audio) == 1 else "s", unreached))

    for a in audio:
        print("  %-12s audio      read by the sound code (%s)"
              % (a["loc"], " ".join(a["regs"])))
    if audio:
        print("       ^ where the sound data is, not what it means. These are "
              "the tables")
        print("         the player reads; the arrangement -- which notes, in "
              "what order --")
        print("         is its own format. songfmt.py reads that, given a "
              "description.")
    if sure:
        print("  (entries are taken from the captured list as-is; a zone that")
        print("   was not in use can decode as a plausible-looking entry, so")
        print("   check anything that lands somewhere odd)")
        print("")
    for a in sure:
        w = ("%d bytes wide" % a["width"]) if a["width"] else "character set"
        print("  %-12s graphics   %-16s %s%s"
              % (a["loc"], w, a["source"],
                 "" if a["refs"] < 2 else "  (x%d)" % a["refs"]))
    if unsure:
        print("")
        print("  %d more graphics addresses sit in the paged window. MARIA read"
              % len(unsure))
        print("  them from whichever bank was mapped at the time, which the")
        print("  capture does not record, so the bank is a guess and is left")
        print("  as one:")
        shown = {}
        for a in unsure:
            shown.setdefault(a["loc"].split(":")[1], a)
        for _k, a in sorted(shown.items())[:12]:
            w = ("%d wide" % a["width"]) if a["width"] else "?"
            print("     $%-6s %-9s any of %s"
                  % (a["loc"].split(":")[1], w, ", ".join(a["banks"])))
        if len(shown) > 12:
            print("     ... and %d more" % (len(shown) - 12))

    if not gfx and not audio:
        print("  Nothing identified. The tracer reaches the display code")
        print("  through an indirect jump on most games -- give it an entry")
        print("  point in the annotations and run again.")

    if palettes:
        done = [p for p in palettes if p["complete"] and p["palette"] is not None]
        print("")
        print("  %d palette slot%s written as constants%s"
              % (len(palettes), "" if len(palettes) == 1 else "s",
                 (", %d complete" % len(done)) if done else ""))
        for p in palettes:
            name = "background" if p["palette"] is None else "palette %d" % p["palette"]
            bits = []
            for i in ("1", "2", "3", "0"):
                if i in p["colours"]:
                    v = p["colours"][i]
                    bits.append("%s" % " ".join("$%02X" % c for c in v[:4]))
            print("     %-12s %s" % (name, "  |  ".join(bits)))
        print("  These are the colours the code writes, not the colours this")
        print("  artwork is drawn in -- MARIA holds them in registers and a game")
        print("  rewrites them per zone. spriteedit.py --palette-from offers them.")

    in_ram = [a for _sp, a in lists if a < 0x4000]
    if in_ram and not ram_hits:
        print("")
        print("%d of the %d display-list pointers point into RAM ($%04X%s)."
              % (len(in_ram), len(lists), in_ram[0],
                 ", ..." if len(in_ram) > 1 else ""))
        print("That is normal and not a failure: on this machine the display")
        print("list is built in RAM every frame, so it does not exist until the")
        print("game runs and a static trace correctly stops here. To follow it:")
        print("")
        print("  mame a7800 -cart %s -autoboot_script probes/dumpdl.lua \\"
              % os.path.basename(args.rom))
        print("       -video none -sound none -nothrottle -str 30")
        print("  python tools/assets.py %s --ram ramdump.bin --dll <printed>"
              % os.path.basename(args.rom))

    if args.blocks:
        # Only things with a contiguous extent become blocks. A 7800 sprite is
        # line-planar -- W bytes on each of H successive pages -- so it is not
        # a run of bytes and a block cannot describe it. Those go to the
        # manifest instead, where gfx.py can draw them properly.
        blocks = []
        for a in audio:
            blocks.append({"loc": a["loc"], "name": "aud_%s"
                           % a["loc"].replace(":", "_"),
                           "note": "audio table -> %s" % " ".join(a["regs"])})
        for sp, addr in chars:
            blocks.append({"loc": "%s:%04X" % (sp, addr),
                           "end": "%s:%04X" % (sp, min(addr + 0x800, 0xFFFF)),
                           "gfx": True,
                           "name": "chr_%s_%04X" % (sp, addr),
                           "note": "character set: CHARBASE points here"})
        with open(args.blocks, "w", encoding="utf-8") as f:
            json.dump({"blocks": blocks}, f, indent=2)
            f.write("\n")
        print("\nwrote %s -- %d blocks, merge into your annotations"
              % (args.blocks, len(blocks)))

    if args.manifest:
        doc = {"rom": os.path.basename(args.rom),
               "graphics": gfx, "audio": audio, "palettes": palettes,
               "display_lists": ["%s:%04X" % (sp, a) for sp, a in lists],
               "charbases": ["%s:%04X" % (sp, a) for sp, a in chars],
               "unreached_bytes": unreached}
        with open(args.manifest, "w", encoding="utf-8") as f:
            json.dump(doc, f, indent=2)
            f.write("\n")
        print("wrote %s -- %d graphics, %d audio"
              % (args.manifest, len(gfx), len(audio)))
        if sure:
            a = sure[0]
            sp, ad = a["loc"].split(":")
            print("  draw one:  python tools/gfx.py %s --space %s --base 0x%s"
                  % (args.rom, sp, ad))
    return 0


    return 0


if __name__ == "__main__":
    sys.exit(main())
