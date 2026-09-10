#!/usr/bin/env python3
"""
What it would take to move Atari 8-bit code to the 7800, counted rather than guessed.

    python tools/portscan.py karateka-sectors.bin
    python tools/portscan.py image.bin --sites GTIA

Both machines run a 6502, which is the thing people notice first and the least
useful fact about the job. The CPU is free; everything the code says to the
hardware is the work. So this counts what the code says, register by register,
and sorts it into what carries over and what has to be rebuilt.

## The three categories, and why they are the right ones

**Carries over.** POKEY is POKEY. A 7800 cartridge can have the same chip the
Atari 800 has, at a different address, and sound code moves across with the
base address changed and nothing else. That is a genuinely free port of the
part everybody expects to be hard.

**Has an equivalent, needs rewriting.** Joysticks are read through a PIA on one
machine and a RIOT on the other. The idea survives, the code does not.

**Has no equivalent at all.** This is the part worth knowing before starting.
The 7800 has no player/missile graphics and no hardware collision detection:
MARIA fetches sprites through a display list and reports nothing about what
overlapped what. Every `P0PL` read is a piece of game logic that must be
replaced with arithmetic, not a register that must be renamed. Likewise ANTIC's
display lists and MARIA's are both called display lists and are not the same
idea: ANTIC describes *modes per scanline*, MARIA describes *objects per zone*.

The counts below are of instructions, not of difficulty, and they say where the
work is rather than how long it takes. A single `HITCLR` in a collision routine
can outweigh forty colour writes.
"""
import argparse
import collections
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import m6502

# Atari 8-bit hardware, by address. Read and write meanings differ on these
# chips, so where they differ both are named.
A8 = {}
for i in range(4):
    A8[0xD000 + i] = ("GTIA", "HPOSP%d / M%dPF" % (i, i))
    A8[0xD004 + i] = ("GTIA", "HPOSM%d / P%dPF" % (i, i))
    A8[0xD008 + i] = ("GTIA", "SIZEP%d / M%dPL" % (i, i))
    A8[0xD00C + i] = ("GTIA", "SIZEM/GRAFP / P%dPL" % i)
    A8[0xD010 + i] = ("GTIA", "GRAFM/GRAFP / TRIG%d" % i)
    A8[0xD012 + i] = ("GTIA", "COLPM%d" % i)
    A8[0xD016 + i] = ("GTIA", "COLPF%d" % i)
A8[0xD01A] = ("GTIA", "COLBK")
A8[0xD01B] = ("GTIA", "PRIOR")
A8[0xD01C] = ("GTIA", "VDELAY")
A8[0xD01D] = ("GTIA", "GRACTL")
A8[0xD01E] = ("GTIA", "HITCLR")
A8[0xD01F] = ("GTIA", "CONSOL")
for i in range(4):
    A8[0xD200 + i * 2] = ("POKEY", "AUDF%d / POT%d" % (i + 1, i * 2))
    A8[0xD201 + i * 2] = ("POKEY", "AUDC%d / POT%d" % (i + 1, i * 2 + 1))
A8[0xD208] = ("POKEY", "AUDCTL / ALLPOT")
A8[0xD209] = ("POKEY", "STIMER / KBCODE")
A8[0xD20A] = ("POKEY", "SKRES / RANDOM")
A8[0xD20B] = ("POKEY", "POTGO")
A8[0xD20D] = ("POKEY", "SEROUT / SERIN")
A8[0xD20E] = ("POKEY", "IRQEN / IRQST")
A8[0xD20F] = ("POKEY", "SKCTL / SKSTAT")
A8[0xD300] = ("PIA", "PORTA (joysticks)")
A8[0xD301] = ("PIA", "PORTB")
A8[0xD302] = ("PIA", "PACTL")
A8[0xD303] = ("PIA", "PBCTL")
A8[0xD400] = ("ANTIC", "DMACTL")
A8[0xD401] = ("ANTIC", "CHACTL")
A8[0xD402] = ("ANTIC", "DLISTL")
A8[0xD403] = ("ANTIC", "DLISTH")
A8[0xD404] = ("ANTIC", "HSCROL")
A8[0xD405] = ("ANTIC", "VSCROL")
A8[0xD407] = ("ANTIC", "PMBASE")
A8[0xD409] = ("ANTIC", "CHBASE")
A8[0xD40A] = ("ANTIC", "WSYNC")
A8[0xD40B] = ("ANTIC", "VCOUNT")
A8[0xD40E] = ("ANTIC", "NMIEN")
A8[0xD40F] = ("ANTIC", "NMIRES / NMIST")

# What each becomes on a 7800, and what that costs.
#   same    the same silicon, at a different address
#   remap   a different chip doing the same job
#   rebuild no equivalent; the game logic around it has to change
VERDICT = {
    "POKEY":  ("same", "POKEY at $4000 on a cartridge that has one. Change the "
                       "base address; the register layout is identical."),
    "PIA":    ("remap", "joysticks come from the RIOT at $0280 (SWCHA) and the "
                        "TIA at $0C-$0D. Same information, different address "
                        "and bit order."),
    "GTIA":   ("rebuild", "no player/missile hardware and no collision "
                          "registers. Sprites become MARIA display-list "
                          "objects; every collision read becomes arithmetic."),
    "ANTIC":  ("rebuild", "MARIA's display lists are not ANTIC's. ANTIC says "
                          "'this scanline is mode 4'; MARIA says 'this zone "
                          "holds these objects at these addresses'. Scrolling "
                          "and character modes have no direct counterpart."),
    "OS":     ("rebuild", "the 7800 has no OS ROM and no disk. SIO and CIO "
                          "calls become reads from cartridge ROM, which is "
                          "usually a simplification."),
}

LOADS = {0xAD: "abs", 0xBD: "abx", 0xB9: "aby", 0x2C: "abs", 0x0D: "abs",
         0x2D: "abs", 0x4D: "abs", 0x6D: "abs", 0xCD: "abs", 0xEC: "abs",
         0xCC: "abs", 0xAE: "abs", 0xAC: "abs", 0xBE: "aby", 0xBC: "abx"}
STORES = {0x8D: "abs", 0x9D: "abx", 0x99: "aby", 0x8E: "abs", 0x8C: "abs",
          0x0E: "abs", 0x2E: "abs", 0x4E: "abs", 0x6E: "abs", 0xEE: "abs",
          0xCE: "abs"}


def unmirror(addr):
    """Fold an address onto the register it actually reaches.

    Atari 8-bit hardware repeats across its page: GTIA every 32 bytes through
    $D0xx, POKEY every 16 through $D2xx, the PIA every 4 through $D3xx, ANTIC
    every 16 through $D4xx. Code uses the mirrors freely -- indexing off a base
    naturally lands on them -- so matching only the canonical addresses
    undercounts. It reported no joystick reads at all on a game that plainly
    has some.
    """
    page = addr & 0xFF00
    if page == 0xD000:
        return 0xD000 + (addr & 0x1F)
    if page == 0xD200:
        return 0xD200 + (addr & 0x0F)
    if page == 0xD300:
        return 0xD300 + (addr & 0x03)
    if page == 0xD400:
        return 0xD400 + (addr & 0x0F)
    return addr


def scan(blob, base=0):
    """Every instruction that touches Atari 8-bit hardware.

    A linear sweep, not a trace: it reads the image as a stream of opcodes and
    notes the ones whose operand lands on a hardware register. That
    over-reports, because data can spell an instruction -- so treat the counts
    as an upper bound and the *shape* as the finding. Nothing here depends on
    reaching the code, which matters when most of a disk is data and some of it
    is encrypted.
    """
    hits = []
    i = 0
    while i < len(blob) - 2:
        op = blob[i]
        mode = LOADS.get(op) or STORES.get(op)
        if mode:
            addr = unmirror(blob[i + 1] | (blob[i + 2] << 8))
            if addr in A8:
                chip, name = A8[addr]
                hits.append({"at": base + i, "addr": addr, "chip": chip,
                             "name": name, "op": m6502.OPCODES[op][0],
                             "write": op in STORES})
            elif 0xE400 <= addr <= 0xE4FF:
                hits.append({"at": base + i, "addr": addr, "chip": "OS",
                             "name": "OS vector $%04X" % addr,
                             "op": m6502.OPCODES[op][0], "write": op in STORES})
        i += 1
    return hits


def report(hits, out):
    by_chip = collections.Counter(h["chip"] for h in hits)
    by_reg = collections.Counter((h["chip"], h["name"]) for h in hits)
    order = ["POKEY", "PIA", "GTIA", "ANTIC", "OS"]

    out.append("what the code talks to, and what becomes of it")
    out.append("")
    for kind, title in (("same", "CARRIES OVER -- the same chip"),
                        ("remap", "HAS AN EQUIVALENT -- rewrite the access"),
                        ("rebuild", "NO EQUIVALENT -- rebuild the logic")):
        chips = [c for c in order
                 if c in by_chip and VERDICT[c][0] == kind]
        if not chips:
            continue
        out.append(title)
        for c in chips:
            out.append("  %-6s %4d accesses" % (c, by_chip[c]))
            for line in _wrap(VERDICT[c][1], 66):
                out.append("         %s" % line)
            regs = [(n, k) for (cc, n), k in by_reg.items() if cc == c]
            regs.sort(key=lambda x: -x[1])
            for n, k in regs[:6]:
                out.append("           %-26s %4d" % (n, k))
            if len(regs) > 6:
                out.append("           ... and %d more registers"
                           % (len(regs) - 6))
        out.append("")

    hard = sum(by_chip[c] for c in order
               if c in by_chip and VERDICT[c][0] == "rebuild")
    easy = sum(by_chip[c] for c in order
               if c in by_chip and VERDICT[c][0] == "same")
    total = sum(by_chip.values()) or 1
    out.append("%d hardware accesses in all: %d carry over (%.0f%%), "
               "%d need the surrounding logic rebuilt (%.0f%%)"
               % (total, easy, 100.0 * easy / total, hard,
                  100.0 * hard / total))


def _wrap(text, width):
    words, line, out = text.split(), "", []
    for w in words:
        if len(line) + len(w) + 1 > width:
            out.append(line)
            line = w
        else:
            line = (line + " " + w).strip()
    if line:
        out.append(line)
    return out


def main():
    ap = argparse.ArgumentParser(
        description=__doc__.strip().split("\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("image")
    ap.add_argument("--base", default="0",
                    help="address the image loads at, for reporting sites")
    ap.add_argument("--sites", metavar="CHIP",
                    help="list every access to one chip, with addresses")
    args = ap.parse_args()

    blob = io.open(args.image, "rb").read()
    hits = scan(blob, int(args.base, 0))
    print("%s  (%d bytes)" % (os.path.basename(args.image), len(blob)))
    print("")
    if args.sites:
        want = args.sites.upper()
        rows = [h for h in hits if h["chip"] == want]
        print("every %s access (%d):" % (want, len(rows)))
        for h in rows[:200]:
            print("   %06X  %-4s %-26s %s"
                  % (h["at"], h["op"], h["name"],
                     "write" if h["write"] else "read"))
        return 0
    out = []
    report(hits, out)
    for line in out:
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
