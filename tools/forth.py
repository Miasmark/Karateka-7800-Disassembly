#!/usr/bin/env python3
"""
Decompile an indirect-threaded Forth image out of a cartridge.

    python tools/forth.py game.a78 --at 70E6
    python tools/forth.py game.a78 --thread 4098 --count 40
    python tools/forth.py game.a78 --map

Some 7800 games are not 6502 programs. Karateka is a Forth program with a
6502 interpreter underneath it, which is why a tracing disassembler reaches
173 instructions in a 48K ROM and stops: the rest is not reachable as code,
because it is not code. It is a thread of 16-bit words.

This reads that thread.

## What indirect threading looks like from the outside

A *word* is an address. The two bytes at that address are its **code field**,
and what they point at decides what kind of word it is:

    [W] == DOCOL     a colon definition -- its body is a list of words at W+2
    [W] == W + 2     a primitive -- 6502 code follows the code field
    anything else    a constant, a variable, or something stranger

That single rule is enough to walk the whole program, and it is checkable: a
wrong guess about DOCOL gives colon definitions whose bodies are not words,
which shows up immediately as tokens that resolve nowhere.

## What it cannot give you

**Names.** A shipped Forth has no dictionary -- headers are stripped because
nobody types at a game -- so the words have addresses and nothing else.
`w_70E6` is the best anyone can do. Karateka's ROM contains no name headers at
all; the only ASCII in it is the game's own text.

So this prints structure, not meaning. Working out that `w_70E6` is "read the
controls and decide what the player wants" is still a person's job -- but it is
a person reading forty lines of stack code instead of forty thousand bytes of
hex, which is the difference between possible and not.
"""
import argparse
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cart as cart_module
import m6502


class Image(object):
    """A cartridge plus the four addresses that define its interpreter."""

    def __init__(self, cart, space, next_=None, docol=None, exit_=None,
                 lit=None):
        self.cart = cart
        self.space = space
        self.lo = cart.base_of(space)
        self.hi = self.lo + cart.size_of(space)
        self.next = next_
        self.docol = docol
        self.exit = exit_
        self.lit = lit
        self.notes = []

    # ---------------------------------------------------------------- basics
    def byte(self, addr):
        return self.cart.byte(self.space, addr)

    def word(self, addr):
        return self.byte(addr) | (self.byte(addr + 1) << 8)

    def in_rom(self, addr):
        return self.lo <= addr < self.hi - 1

    # ----------------------------------------------------------- discovery
    def find_next(self):
        """The inner loop: the `JMP` every primitive ends with.

        Counted over *code only*, not over the whole image. A first attempt
        scanned the ROM for `4C xx xx` byte patterns and voted on the target,
        which fails badly here: the thread is much larger than the kernel and
        is full of cells whose bytes happen to spell a JMP. That search
        elected the literal's own address, from 112 imaginary jumps.

        A primitive announces itself structurally -- its code field points at
        the two bytes after it -- so this finds those, follows each one's real
        instructions to its first `JMP`, and counts that. Thread data cannot
        vote.
        """
        counts = {}
        for w in range(self.lo, self.hi - 2):
            if self.word(w) != w + 2:
                continue
            a = w + 2
            for _ in range(24):
                if not self.in_rom(a):
                    break
                op = self.byte(a)
                mn, md, _ill = m6502.OPCODES[op]
                if mn == "JMP" and md == "abs":
                    t = self.word(a + 1)
                    if self.in_rom(t):
                        counts[t] = counts.get(t, 0) + 1
                    break
                if mn in ("RTS", "RTI", "JAM", "BRK"):
                    break
                a += m6502.LENGTH[op]
        if not counts:
            return None
        ranked = sorted(counts.items(), key=lambda kv: -kv[1])
        target, n = ranked[0]
        runner_up = ranked[1][1] if len(ranked) > 1 else 0
        # Being the clear winner is the test, not passing a fixed count. A
        # threshold of "at least eight primitives" was arbitrary and rejected
        # any small image outright; what actually identifies the inner loop is
        # that everything ends there and nothing else comes close.
        if n < 3 or n < runner_up * 2:
            return None
        self.notes.append("NEXT at $%04X, ending %d primitives (next busiest "
                          "target: %d)" % (target, n, runner_up))
        return target

    def find_docol(self):
        """The colon-definition entry: it pushes the thread pointer.

        Recognised by shape rather than by address -- a routine that pushes two
        bytes and then rebuilds the interpreter's pointer from the word just
        fetched. Everything else in a Forth kernel leaves the pointer alone.
        """
        if self.next is None:
            return None
        ip = self.ip_pointer()
        if ip is None:
            return None
        blob = bytes(self.cart.slice(self.space, self.lo, self.hi - self.lo))
        # LDA ip+1 / PHA / LDA ip / PHA
        want = bytes([0xA5, ip + 1, 0x48, 0xA5, ip, 0x48])
        i = blob.find(want)
        if i == -1:
            return None
        addr = self.lo + i
        self.notes.append("DOCOL at $%04X (saves the thread pointer)" % addr)
        return addr

    def find_exit(self):
        """The other half of DOCOL: it pulls the thread pointer back."""
        ip = self.ip_pointer()
        if ip is None:
            return None
        blob = bytes(self.cart.slice(self.space, self.lo, self.hi - self.lo))
        want = bytes([0x68, 0x85, ip, 0x68, 0x85, ip + 1])
        best = None
        i = blob.find(want)
        while i != -1:
            addr = self.lo + i
            # the word is the two bytes before its code, and must point at it
            if self.in_rom(addr - 2) and self.word(addr - 2) == addr:
                best = addr - 2
                break
            i = blob.find(want, i + 1)
        if best is not None:
            self.notes.append("EXIT at $%04X (restores it)" % best)
        return best

    def ip_pointer(self):
        """The zero-page pair NEXT reads the thread through."""
        if self.next is None:
            return None
        # NEXT starts LDY #1 / LDA (ip),Y
        if self.byte(self.next) == 0xA0 and self.byte(self.next + 2) == 0xB1:
            return self.byte(self.next + 3)
        for off in range(0, 12):
            if self.byte(self.next + off) == 0xB1:
                return self.byte(self.next + off + 1)
        return None

    def find_lit(self):
        """The word that takes the *next thread cell* as data, not as a word.

        Everything downstream depends on getting this right: miss it and every
        literal in the program is decompiled as a call to whatever address the
        number happens to equal, which produces a listing that looks fine and
        is nonsense.
        """
        ip = self.ip_pointer()
        if ip is None:
            return None
        blob = bytes(self.cart.slice(self.space, self.lo, self.hi - self.lo))
        # LDA (ip),Y ... INC ip -- reads through the thread pointer and steps it
        want = bytes([0xB1, ip])
        i = blob.find(want)
        while i != -1:
            addr = self.lo + i
            tail = blob[i:i + 12]
            if bytes([0xE6, ip]) in tail and self.in_rom(addr - 2) \
                    and self.word(addr - 2) == addr and addr != self.next:
                self.notes.append("literal at $%04X (eats the next cell)"
                                  % (addr - 2))
                return addr - 2
            i = blob.find(want, i + 1)
        return None

    def discover(self):
        if self.next is None:
            self.next = self.find_next()
        if self.docol is None:
            self.docol = self.find_docol()
        if self.exit is None:
            self.exit = self.find_exit()
        if self.lit is None:
            self.lit = self.find_lit()
        return self

    # ------------------------------------------------------------- classify
    def kind_of(self, word):
        """What sort of word lives at this address."""
        if not self.in_rom(word):
            return "?"
        cf = self.word(word)
        if self.docol is not None and cf == self.docol:
            return "colon"
        if cf == word + 2:
            return "code"
        if self.in_rom(cf):
            return "other"
        return "?"

    def name(self, word):
        if word == self.exit:
            return "EXIT"
        if word == self.lit:
            return "LIT"
        k = self.kind_of(word)
        return {"colon": "w_%04X", "code": "p_%04X",
                "other": "x_%04X", "?": "$%04X"}[k] % word

    # ------------------------------------------------------------ decompile
    def body(self, word, limit=512):
        """The cells of a colon definition, as (address, cell, kind) triples.

        `kind` is "word" for something to execute, "data" for a cell eaten by
        the word before it -- a literal's value, a branch's destination. Those
        are not words, and printing them as words is the single easiest way to
        produce a decompilation that reads plausibly and means nothing.
        """
        out = []
        a = word + 2
        take_data = 0
        for _ in range(limit):
            if not self.in_rom(a):
                break
            cell = self.word(a)
            if take_data:
                out.append((a, cell, "data"))
                take_data -= 1
                a += 2
                continue
            out.append((a, cell, "word"))
            a += 2
            if cell == self.exit:
                break
            if cell == self.lit:
                take_data = 1
            elif self.is_branch(cell):
                take_data = 1
        return out

    def inline_tails(self):
        """The two routines that mean "the next cell is data, not a word".

        A word that takes an inline cell has to do one of two things with the
        thread pointer: add the cell to it (a branch that is taken) or step
        past it (a branch that is not). Both are short, distinctive, and shared
        -- in Karateka every such word ends up at $4D58 or $4D76 -- so finding
        the routines once and asking which words reach them is far more
        reliable than pattern-matching each word.

        The first attempt tested only for "adds through the thread pointer",
        which found the unconditional branch and missed the conditional one and
        the compare-and-branch. Both of those still eat a cell, so seven cells
        in a single definition were decompiled as calls to $0010.
        """
        if getattr(self, "_tails", None) is not None:
            return self._tails
        ip = self.ip_pointer()
        tails = set()
        if ip is None:
            self._tails = tails
            return tails
        blob = bytes(self.cart.slice(self.space, self.lo, self.hi - self.lo))
        for pat in (bytes([0x18, 0xB1, ip, 0x65, ip]),        # IP += cell
                    bytes([0x18, 0xA5, ip, 0x69, 0x02])):     # IP += 2
            i = blob.find(pat)
            while i != -1:
                tails.add(self.lo + i)
                i = blob.find(pat, i + 1)
        self._tails = tails
        return tails

    def is_branch(self, word):
        """Does this word take an inline destination or count?

        True when its code can reach one of the inline tails -- by falling into
        it, jumping to it, or branching to it. Anything that manipulates the
        thread pointer beyond what NEXT already does is, by definition, reading
        or skipping a cell that is not a word.
        """
        if not self.in_rom(word) or self.kind_of(word) != "code":
            return False
        tails = self.inline_tails()
        if not tails:
            return False
        a = word + 2
        for _ in range(40):
            if not self.in_rom(a):
                break
            if a in tails:
                return True
            op = self.byte(a)
            mn, md, _ill = m6502.OPCODES[op]
            ln = m6502.LENGTH[op]
            if md == "abs" and mn in ("JMP", "JSR"):
                if self.word(a + 1) in tails:
                    return True
            if md == "rel":
                t = (a + 2 + ((self.byte(a + 1) ^ 0x80) - 0x80)) & 0xFFFF
                if t in tails:
                    return True
            if mn in ("JMP", "RTS", "RTI"):
                break
            a += ln
        return False

    def disasm(self, addr, limit=24):
        """A primitive's machine code, up to where it hands back to NEXT."""
        out = []
        a = addr
        for _ in range(limit):
            if not self.in_rom(a):
                break
            op = self.byte(a)
            mn, md, _ill = m6502.OPCODES[op]
            ln = m6502.LENGTH[op]
            raw = [self.byte(a + i) for i in range(ln)]
            if md in ("abs", "abx", "aby", "ind"):
                v = raw[1] | (raw[2] << 8)
                txt = "$%04X" % v
            elif md in ("zp", "zpx", "zpy", "izx", "izy"):
                txt = "$%02X" % raw[1]
            elif md == "imm":
                txt = "#$%02X" % raw[1]
            elif md == "rel":
                txt = "$%04X" % ((a + 2 + ((raw[1] ^ 0x80) - 0x80)) & 0xFFFF)
            else:
                txt = ""
            out.append((a, raw, mn, m6502.FMT[md].replace("{v}", txt)))
            a += ln
            if mn in ("JMP", "RTS", "RTI"):
                break
        return out


    # ----------------------------------------------------------- inventory
    def definitions(self):
        """Every colon definition in the image, by address.

        Cheap and complete: a colon definition is any address whose code field
        is DOCOL, and that is one comparison per address. Some will be
        coincidence -- two bytes anywhere in the image can happen to equal
        DOCOL's address -- so each is confirmed by decompiling it and requiring
        that its body reach an EXIT through cells that all resolve to real
        words. A definition that does not is not one.
        """
        out = []
        if self.docol is None:
            return out
        for w in range(self.lo, self.hi - 2):
            if self.word(w) != self.docol:
                continue
            cells = self.body(w, limit=256)
            if not cells or cells[-1][1] != self.exit:
                continue
            ok = True
            for _addr, cell, kind in cells:
                if kind == "data":
                    continue
                if self.kind_of(cell) == "?":
                    ok = False
                    break
            if ok and len(cells) >= 2:
                out.append(w)
        return out

    def xref(self, defs=None):
        """Which definitions name which words."""
        defs = self.definitions() if defs is None else defs
        callers = {}
        for w in defs:
            for _addr, cell, kind in self.body(w, limit=256):
                if kind != "word":
                    continue
                callers.setdefault(cell, set()).add(w)
        return {k: sorted(v) for k, v in callers.items()}


def load_profile(path):
    """A profile written by probes/threadprof.lua: address -> dispatch count."""
    counts, meta = {}, {}
    for line in io.open(path, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            bits = line[1:].split()
            if len(bits) == 2:
                meta[bits[0]] = bits[1]
            continue
        a, n = line.split()
        counts[int(a, 16)] = int(n)
    return counts, meta


def loop_histogram(path):
    """The distribution of the main loop's period, in frames.

    Reported instead of an average because every average in this work has been
    ruined by a session holding more than one kind of state. A menu polls the
    stick twice a frame; an intermission does not run the loop at all for a
    minute; the mean lands between them and describes nothing that happened.
    """
    hist = {}
    for line in io.open(path, encoding="utf-8"):
        if not line.startswith("# loophist "):
            continue
        _h, _k, g, n = line.split()
        hist[int(g)] = int(n)
    return hist


def report_profile(im, counts, meta, out):
    """Where the time went, by definition rather than by address.

    A bucket lands inside whichever definition contains it, so the report
    reads as "this definition is most of the work" rather than as a list of
    addresses. Buckets belonging to no definition are grouped; those are
    usually the kernel's own loops.
    """
    defs = sorted(im.definitions())
    spans = []
    for w in defs:
        cells = im.body(w, limit=256)
        end = cells[-1][0] + 2 if cells else w + 2
        spans.append((w, end))

    def owner(addr):
        for w, end in spans:
            if w <= addr < end:
                return w
        return None

    total = sum(counts.values()) or 1
    by_def = {}
    for addr, n in counts.items():
        by_def[owner(addr)] = by_def.get(owner(addr), 0) + n
    out.append("%s dispatches over %s frames"
               % (meta.get("dispatches", "?"), meta.get("frames", "?")))
    try:
        out.append("  %.1f dispatches a frame"
                   % (int(meta["dispatches"]) / max(1, int(meta["frames"]))))
    except Exception:                                        # noqa: BLE001
        pass
    out.append("")
    out.append("where the interpreter spent itself:")
    for w, n in sorted(by_def.items(), key=lambda kv: -kv[1])[:14]:
        name = im.name(w) if w is not None else "(outside any definition)"
        out.append("   %-12s %9d   %5.1f%%" % (name, n, 100.0 * n / total))

    hot = max(counts.items(), key=lambda kv: kv[1])
    w = owner(hot[0])
    out.append("")
    out.append("the busiest 16 bytes are $%04X (%.1f%% of everything)"
               % (hot[0], 100.0 * hot[1] / total))
    if w is not None:
        out.append("which is inside %s:" % im.name(w))
        for addr, cell, kind in im.body(w):
            if abs(addr - hot[0]) > 24:
                continue
            mark = "  <==" if abs(addr - hot[0]) < 16 else ""
            if kind == "data":
                out.append("      %04X            $%04X%s" % (addr, cell, mark))
            else:
                out.append("      %04X    %-14s%s" % (addr, im.name(cell), mark))



def render(im, word, out, seen=None, depth=0, maxdepth=0):
    """Print one colon definition, and optionally what it calls."""
    seen = set() if seen is None else seen
    if word in seen:
        return
    seen.add(word)
    pad = "  " * depth
    out.append("%s: %s   ( $%04X )" % (pad, im.name(word), word))
    for addr, cell, kind in im.body(word):
        if kind == "data":
            out.append("%s      %04X    %-12s %s" % (pad, addr, "",
                                                     "$%04X" % cell))
            continue
        nm = im.name(cell)
        note = ""
        if cell == im.lit:
            note = "( the next cell is its value )"
        elif im.is_branch(cell):
            note = "( the next cell is where it goes )"
        elif im.kind_of(cell) == "colon":
            note = "( a definition )"
        elif im.kind_of(cell) == "other":
            note = "( not code and not a definition -- a constant or variable )"
        out.append("%s      %04X    %-12s %s" % (pad, addr, nm, note))
    out.append("%s ;" % pad)
    if depth < maxdepth:
        for _addr, cell, kind in im.body(word):
            if kind == "word" and im.kind_of(cell) == "colon":
                out.append("")
                render(im, cell, out, seen, depth + 1, maxdepth)


def main():
    ap = argparse.ArgumentParser(
        description=__doc__.strip().split("\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("rom")
    ap.add_argument("--space", help="which bank, for a mapped cartridge")
    ap.add_argument("--at", help="decompile the definition at this hex address")
    ap.add_argument("--depth", type=int, default=0,
                    help="also print the definitions it calls, this many deep")
    ap.add_argument("--map", action="store_true",
                    help="what the image contains, and the busiest words")
    ap.add_argument("--callers", metavar="HEX",
                    help="which definitions name this word")
    ap.add_argument("--code", metavar="HEX",
                    help="disassemble a primitive")
    ap.add_argument("--profile", metavar="LOG",
                    help="a log from probes/threadprof.lua: say which "
                         "definitions the game actually spends itself in")
    ap.add_argument("--next", metavar="HEX", help="override NEXT")
    ap.add_argument("--docol", metavar="HEX", help="override DOCOL")
    ap.add_argument("--exit", metavar="HEX", help="override EXIT")
    ap.add_argument("--lit", metavar="HEX", help="override the literal")
    args = ap.parse_args()

    def h(v):
        return int(v.lstrip("$"), 16) if v else None

    try:
        cart = cart_module.Cart(args.rom)
    except (cart_module.UnknownMapper, cart_module.UnknownSpace, IOError) as e:
        sys.stderr.write("%s\n" % e)
        return 2
    space = args.space or cart.spaces()[0]
    im = Image(cart, space, h(args.next), h(args.docol), h(args.exit),
               h(args.lit)).discover()

    if im.next is None or im.docol is None:
        print("This does not look like an indirect-threaded image: no inner "
              "loop that primitives return to, or no colon entry that saves "
              "the thread pointer. Pass --next/--docol if you know them.")
        return 1

    print("%s  (%s)" % (os.path.basename(args.rom), space))
    for n in im.notes:
        print("  %s" % n)
    print("  the thread pointer lives at $%02X" % im.ip_pointer())
    print("")

    if args.profile:
        counts, meta = load_profile(args.profile)
        lines = []
        report_profile(im, counts, meta, lines)
        for line in lines:
            print(line)
        return 0

    if args.code:
        a = h(args.code)
        start = a + 2 if im.word(a) == a + 2 else a
        print("primitive at $%04X:" % a)
        for addr, raw, mn, oper in im.disasm(start):
            print("    %04X  %-9s %-4s %s"
                  % (addr, " ".join("%02X" % b for b in raw), mn, oper))
        return 0

    if args.at:
        out = []
        render(im, h(args.at), out, maxdepth=args.depth)
        print("\n".join(out))
        return 0

    if args.callers:
        want = h(args.callers)
        callers = im.xref()
        who = callers.get(want, [])
        print("%s is named by %d definition(s):" % (im.name(want), len(who)))
        for w in who:
            print("    %s   ( $%04X )" % (im.name(w), w))
        return 0

    if args.map or True:
        defs = im.definitions()
        callers = im.xref(defs)
        prims = [w for w in range(im.lo, im.hi - 2) if im.word(w) == w + 2]
        print("  %d colon definitions" % len(defs))
        print("  %d addresses shaped like a primitive" % len(prims))
        top = sorted(callers.items(), key=lambda kv: -len(kv[1]))[:12]
        print("\n  the most-named words:")
        for w, who in top:
            print("     %-10s named by %4d definitions   %s"
                  % (im.name(w), len(who),
                     {"colon": "(a definition)", "code": "(a primitive)"}
                     .get(im.kind_of(w), "")))
        never = [w for w in defs if w not in callers]
        print("\n  %d definitions nothing else names -- entry points, or "
              "reached only through a table" % len(never))
        for w in never[:8]:
            print("     %s" % im.name(w))
    return 0


if __name__ == "__main__":
    sys.exit(main())
