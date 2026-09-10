#!/usr/bin/env python3
"""
Trace an Atari 8-bit cartridge, following the code rather than sweeping it.

    python tools/a8dis.py karateka.car
    python tools/a8dis.py karateka.car --at B7B1
    python tools/a8dis.py karateka.car --map
    python tools/a8dis.py karateka.car --hardware POKEY

`portscan.py` answers "how much of this is portable" by reading every byte as a
possible opcode. That is the right instrument for a question about proportions,
and the wrong one for "what does this program do", because most of what it
counts is data that happens to spell an instruction.

This follows execution instead. Start at the cartridge's own vectors, walk each
instruction, take both sides of every branch, enter every JSR, and stop at RTS.
What it reaches is code; what it never reaches is data or unreachable, and the
difference is worth more than either number alone.

## The bank problem, and how much of it is solvable

An XEGS 128K cartridge is sixteen 8K banks. One of them appears at $8000-$9FFF
at a time, chosen by writing its number to $D500-$D5FF; the last bank is always
at $A000-$BFFF. So an address in the low window means nothing without knowing
the bank, and the bank is a runtime value.

The tracer carries the bank as part of its position: it walks (bank, address)
pairs, not addresses. When it sees the standard idiom

    LDA #$07
    STA $D500

it knows the bank from that point on. When the bank comes from a computed value
-- a table index, a variable -- it cannot know, and says so rather than
guessing. Those sites are listed in the report, because each one is a place
where a human has to look at what feeds it.

This is honest rather than complete. A routine only ever entered with bank 3
live will be traced under whatever banks reach it, and code reached only through
a computed bank switch will not be traced at all.

## What the report is for

Porting. The interesting output is not the listing, it is the map: which banks
hold reachable code, where the interrupt handlers are, which routines touch
POKEY, which touch ANTIC, and which do both. A routine that touches only POKEY
moves to the 7800 nearly unchanged. A routine that touches ANTIC and GTIA
together is the one to read carefully.
"""
import argparse
import collections
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import m6502
import portscan

BANK_SIZE = 8192

# Atari 8-bit OS locations a cartridge cares about. Not the whole map -- the
# ones whose appearance in a trace tells you something about structure.
OS_VECTORS = {
    0x0200: "VDSLST (display list interrupt vector)",
    0x0201: "VDSLST+1",
    0x0222: "VVBLKI (immediate vertical blank vector)",
    0x0223: "VVBLKI+1",
    0x0224: "VVBLKD (deferred vertical blank vector)",
    0x0225: "VVBLKD+1",
    0x0230: "SDLSTL (display list shadow)",
    0x0231: "SDLSTH",
    0x022F: "SDMCTL (DMACTL shadow)",
    0x026F: "GRACTL shadow",
    0x0278: "PTRIG0 / STICK0 shadow",
    0x0279: "STICK1 shadow",
    0x0284: "STRIG0 shadow",
    0x0285: "STRIG1 shadow",
    0x02C0: "PCOLR0", 0x02C1: "PCOLR1", 0x02C2: "PCOLR2", 0x02C3: "PCOLR3",
    0x02C4: "COLOR0", 0x02C5: "COLOR1", 0x02C6: "COLOR2", 0x02C7: "COLOR3",
    0x02C8: "COLOR4 (background)",
    # The OS jump table. Getting these one slot out turns "installs a vblank
    # handler" into "still calls the disk", which is the opposite conclusion,
    # so they are written out in full rather than from memory.
    0xE450: "DISKIV", 0xE453: "DSKINV (disk handler)",
    0xE456: "CIOV (character I/O)", 0xE459: "SIOV (serial I/O)",
    0xE45C: "SETVBV (install vblank vector)",
    0xE45F: "SYSVBV", 0xE462: "XITVBV", 0xE465: "SIOINV",
    0xE468: "SENDEV", 0xE46B: "INTINV", 0xE46E: "CIOINV",
    0xE471: "BLKBDV", 0xE474: "WARMSV", 0xE477: "COLDSV",
}


class Cart(object):
    """An XEGS cartridge, addressed the way the 6502 sees it."""

    def __init__(self, blob, path=""):
        self.path = path
        self.header = b""
        if blob[:4] == b"CART":
            self.header = blob[:16]
            self.kind = int.from_bytes(blob[4:8], "big")
            blob = blob[16:]
        else:
            self.kind = None
        self.rom = blob
        self.banks = len(blob) // BANK_SIZE
        self.fixed = self.banks - 1        # the bank wired to $A000-$BFFF

    def read(self, bank, addr):
        """One byte as seen from the CPU, or None if it is not cartridge."""
        if 0x8000 <= addr < 0xA000:
            off = (bank % self.banks) * BANK_SIZE + (addr - 0x8000)
            return self.rom[off]
        if 0xA000 <= addr < 0xC000:
            return self.rom[self.fixed * BANK_SIZE + (addr - 0xA000)]
        return None

    def offset(self, bank, addr):
        """Where a CPU address lives in the file, for cross-referencing."""
        if 0x8000 <= addr < 0xA000:
            return (bank % self.banks) * BANK_SIZE + (addr - 0x8000)
        if 0xA000 <= addr < 0xC000:
            return self.fixed * BANK_SIZE + (addr - 0xA000)
        return None

    def word(self, bank, addr):
        lo, hi = self.read(bank, addr), self.read(bank, addr + 1)
        if lo is None or hi is None:
            return None
        return lo | (hi << 8)

    def vectors(self):
        """The cartridge footer the OS reads at power-on."""
        f = self.fixed * BANK_SIZE
        tail = self.rom[f + 0x1FFA:f + 0x2000]
        return {
            "start": tail[0] | (tail[1] << 8),
            "present": tail[2],
            "option": tail[3],
            "init": tail[4] | (tail[5] << 8),
        }



class Scene(object):
    """The machine as it stands once a scene's overlays are in RAM.

    Karateka's cartridge is a disk that happens to be silicon. It does not run
    from ROM: a 22-instruction loader copies whole 8K banks into RAM and jumps
    there, exactly the way the floppy version copies sectors. Nothing about the
    game is visible from the cartridge windows alone -- the trace of the ROM
    reaches seventy-five bytes and then leaves for $7760.

    So this reconstructs what the loader would have built. Bank 12 goes to
    $0480 as the code common to every scene; a per-scene code bank goes to
    $1000 and a per-scene data bank to $6000, both chosen from two seven-entry
    tables; bank 14 stays paged in at $8000. After that the address space is
    the one the game actually executes in, and a trace from $7760 follows the
    real thing.

    The seven scenes are the seven pairs in those tables, and the loader is
    driven by the byte at $D0, which the start vector sets to zero.
    """

    COMMON = (12, 0x0480)      # LDX #$80 / LDY #$04 / JSR $B2CC
    CODE = 0x1000              # LDX #$00 / LDY #$10
    DATA = 0x6000              # LDX #$00 / LDY #$60
    CODE_TABLE = 0x9FB5        # in bank 13; $2FB5 once bank 13 is at $1000
    DATA_TABLE = 0x9FBC
    SCENES = 7
    RESIDENT = 14              # LDA #$0E / STA $D500, the last thing it does

    def __init__(self, cart, scene=0):
        self.cart = cart
        self.banks = cart.banks
        self.fixed = cart.fixed
        self.scene = scene
        self.rom = cart.rom
        self.kind = cart.kind
        self.ram = bytearray(0x8000)
        self.have = bytearray(0x8000)
        self.code_bank = cart.read(13, self.CODE_TABLE + scene)
        self.data_bank = cart.read(13, self.DATA_TABLE + scene)
        for bank, dest in ((self.COMMON[0], self.COMMON[1]),
                           (self.code_bank, self.CODE),
                           (self.data_bank, self.DATA)):
            self._copy(bank, dest)

    def _copy(self, bank, dest):
        """The loader at $B2CC: 8K from $8000, byte for byte."""
        for i in range(BANK_SIZE):
            a = dest + i
            if a >= 0x8000:
                break          # the copy would run into the cartridge window
            self.ram[a] = self.cart.read(bank, 0x8000 + i)
            self.have[a] = 1

    def read(self, bank, addr):
        if addr < 0x8000:
            return self.ram[addr] if self.have[addr] else None
        return self.cart.read(bank, addr)

    def word(self, bank, addr):
        lo, hi = self.read(bank, addr), self.read(bank, addr + 1)
        if lo is None or hi is None:
            return None
        return lo | (hi << 8)

    def where(self, addr):
        """Say which bank a RAM address came from, for cross-referencing."""
        if addr >= 0x8000:
            return "cartridge window"
        if self.CODE <= addr < self.CODE + BANK_SIZE:
            return "bank %d + $%04X" % (self.code_bank, addr - self.CODE)
        if self.DATA <= addr < self.DATA + BANK_SIZE:
            return "bank %d + $%04X" % (self.data_bank, addr - self.DATA)
        if self.COMMON[1] <= addr < self.COMMON[1] + BANK_SIZE:
            return "bank %d + $%04X" % (self.COMMON[0], addr - self.COMMON[1])
        return "RAM"

    def offset(self, bank, addr):
        return self.cart.offset(bank, addr)

    def vectors(self):
        return self.cart.vectors()


def reg_name(name, write):
    """Pick the right half of a register's name.

    These chips do different jobs on read and write at the same address, and
    the difference is not cosmetic: $D010 written is GRAFP, $D010 read is
    TRIG0. Reporting "writes player graphics" for a routine that polls the fire
    button is the kind of wrong that sends someone looking in the wrong place.
    """
    if " / " not in name:
        return name
    w, r = name.split(" / ", 1)
    return w if write else r


def symbol(addr, write=True):
    """A name for an address, if it has one worth printing."""
    if addr in OS_VECTORS:
        return OS_VECTORS[addr]
    folded = portscan.unmirror(addr)
    if folded in portscan.A8:
        chip, name = portscan.A8[folded]
        return "%s %s" % (chip, reg_name(name, write))
    if 0xD500 <= addr <= 0xD5FF:
        return "cartridge bank select"
    return None


# Anything that leaves A holding something the peephole cannot know. Kept
# deliberately wide: a stale immediate would invent a bank switch, and a
# missed one only costs an "unknown" in the report.
A_CLOBBER = {"LDA", "PLA", "TXA", "TYA", "AND", "ORA", "EOR", "ADC",
             "SBC", "ASL", "LSR", "ROL", "ROR", "LAX", "JSR"}


class Trace(object):
    """Everything a walk of the cartridge learned."""

    def __init__(self, cart):
        self.cart = cart
        self.code = {}          # (bank, pc) -> (mnemonic, mode, operand, size)
        self.entries = {}       # (bank, pc) -> why we started there
        self.calls = collections.defaultdict(set)   # (bank,target) -> callers
        self.hw = []            # every hardware access reached
        self.switches = []      # every write to the bank register
        self.installed = {}     # OS vector -> the address written into it
        self.indirect = []      # JMP ($xxxx) sites, resolved where possible
        self.left = []          # jumps out of cartridge space
        self.bailed = []        # where the walk stopped on a bad opcode

    def reached(self, bank):
        return sum(size for (b, _pc), (_m, _md, _o, size)
                   in self.code.items() if b == bank)


def walk(cart, start, quiet_banks=False):
    """Follow the code from a set of entry points.

    Straight-line runs are walked in a loop so that a peephole -- the last
    immediate loaded -- stays available; that is what makes `LDA #n / STA $D500`
    readable as a bank switch rather than an unexplained store. Branches, calls
    and jumps push a new position onto the worklist.
    """
    t = Trace(cart)
    work = list(start)
    for bank, pc, why in start:
        t.entries[(bank, pc)] = why
    seen = set()

    while work:
        bank, pc, _why = work.pop()
        last_imm = None
        while True:
            if (bank, pc) in seen:
                break
            seen.add((bank, pc))
            op = cart.read(bank, pc)
            if op is None:
                # nothing loaded here: either a jump into RAM the overlays do
                # not cover, or the walk ran off the end of a window
                t.left.append((bank, pc))
                break
            mn, mode, illegal = m6502.OPCODES[op]
            if illegal or mn == "JAM":
                t.bailed.append((bank, pc, mn))
                break
            size = m6502.LENGTH[op]
            n = m6502.MODES[mode]
            operand = None
            if n == 1:
                operand = cart.read(bank, pc + 1)
            elif n == 2:
                operand = cart.word(bank, pc + 1)
            if n and operand is None:
                # an operand that reads off the end of the window, not an
                # implied instruction -- those legitimately have none
                t.bailed.append((bank, pc, "operand off cartridge"))
                break
            t.code[(bank, pc)] = (mn, mode, operand, size)

            here, pc = pc, pc + size

            # --- what this instruction touches -------------------------------
            if mode in ("abs", "abx", "aby", "zp", "zpx", "zpy"):
                addr = operand
                folded = portscan.unmirror(addr)
                if folded in portscan.A8 or 0xE400 <= addr <= 0xE4FF:
                    chip = portscan.A8.get(folded, ("OS", ""))[0]
                    name = portscan.A8.get(
                        folded, ("OS", OS_VECTORS.get(addr, "$%04X" % addr)))[1]
                    t.hw.append({"bank": bank, "at": here, "addr": addr,
                                 "chip": chip, "name": name, "op": mn,
                                 "write": mn in m6502.STORES})
                elif 0xD500 <= addr <= 0xD5FF and mn in m6502.STORES:
                    t.switches.append({"bank": bank, "at": here,
                                       "to": last_imm, "op": mn})
                    if last_imm is not None and not quiet_banks:
                        bank = last_imm % cart.banks
                elif addr in OS_VECTORS and mn in m6502.STORES:
                    t.installed.setdefault(addr, set()).add(last_imm)

            if mn == "LDA" and mode == "imm":
                last_imm = operand
            elif mn in A_CLOBBER:
                last_imm = None      # A no longer holds what we saw

            # --- where it goes -----------------------------------------------
            if mn == "JSR":
                t.calls[(bank, operand)].add((bank, here))
                work.append((bank, operand, "called from $%04X" % here))
                continue
            if mn in m6502.BRANCHES:
                dest = (pc + ((operand ^ 0x80) - 0x80)) & 0xFFFF
                work.append((bank, dest, "branch from $%04X" % here))
                continue
            if mn == "JMP":
                if mode == "abs":
                    work.append((bank, operand, "jump from $%04X" % here))
                else:
                    dest = cart.word(bank, operand)
                    t.indirect.append({"bank": bank, "at": here,
                                       "via": operand, "to": dest})
                    if dest is not None and cart.read(bank, dest) is not None:
                        work.append((bank, dest,
                                     "JMP ($%04X) at $%04X" % (operand, here)))
                break
            if mn in ("RTS", "RTI", "BRK"):
                break
    return t


def line(cart, t, bank, pc):
    """One instruction, formatted, with a name for its operand if it has one."""
    mn, mode, operand, size = t.code[(bank, pc)]
    n = m6502.MODES[mode]
    if mode == "rel":
        text = "$%04X" % ((pc + size + ((operand ^ 0x80) - 0x80)) & 0xFFFF)
    elif n == 2:
        text = "$%04X" % operand
    elif n == 1:
        text = "$%02X" % operand
    else:
        text = ""
    body = ("%s %s" % (mn, m6502.FMT[mode].format(v=text))).strip()
    raw = " ".join("%02X" % cart.read(bank, pc + i) for i in range(size))
    note = ""
    if n == 2:
        s = symbol(operand, mn in m6502.STORES)
        if s:
            note = "  ; " + s
    if mn == "JSR" and not note:
        note = "  ; -> $%04X" % operand
    return "  %04X  %-8s %-14s%s" % (pc, raw, body, note)


def decode(cart, t, bank, pc):
    """Fill in one instruction the trace never reached.

    A listing that stops dead at the first unvisited byte is useless for the
    thing listings are for -- reading past where the tracer gave up, to see
    *why* it gave up. So the formatter can decode on demand; the report still
    only counts what the trace actually reached.
    """
    op = cart.read(bank, pc)
    if op is None:
        return None
    mn, mode, _il = m6502.OPCODES[op]
    size = m6502.LENGTH[op]
    n = m6502.MODES[mode]
    operand = (cart.read(bank, pc + 1) if n == 1 else
               cart.word(bank, pc + 1) if n == 2 else None)
    if n and operand is None:
        return None
    return (mn, mode, operand, size)


def listing(cart, t, bank, start, count=64):
    out = []
    pc, n = start, 0
    while n < count:
        if (bank, pc) not in t.code:
            filled = decode(cart, t, bank, pc)
            if filled is None:
                out.append("  %04X  (off the cartridge)" % pc)
                break
            t.code[(bank, pc)] = filled          # for the formatter only
            out.append(line(cart, t, bank, pc).rstrip() + "   *")
            pc += filled[3]
            n += 1
            if filled[0] in ("RTS", "RTI", "JMP", "BRK"):
                break
            continue
        out.append(line(cart, t, bank, pc))
        mn = t.code[(bank, pc)][0]
        pc += t.code[(bank, pc)][3]
        n += 1
        if mn in ("RTS", "RTI", "JMP", "BRK"):
            break
    return out


def routines(t):
    """Every JSR target, with what hardware the code at it reaches.

    A crude but useful unit: walk forward from the entry until something ends
    the run, and attribute the hardware seen along the way. It undercounts --
    a routine that branches away has parts this does not follow -- so treat the
    chip set as "at least these", which is the direction that matters when the
    question is what a routine is for.
    """
    out = {}
    for (bank, target) in t.calls:
        chips, pc, size = set(), target, 0
        while (bank, pc) in t.code and size < 512:
            mn, _mode, operand, n = t.code[(bank, pc)]
            folded = portscan.unmirror(operand) if operand is not None else 0
            if folded in portscan.A8:
                chips.add(portscan.A8[folded][0])
            size += n
            pc += n
            if mn in ("RTS", "RTI", "JMP", "BRK"):
                break
        out[(bank, target)] = {"chips": chips, "size": size,
                               "callers": len(t.calls[(bank, target)])}
    return out



SETVBV = 0xE45C


def frame_handler(space, t):
    """Find the routine the game runs every vertical blank.

    The OS installs a vblank vector through SETVBV: A says which vector, X and
    Y carry the address high and low. So the handler is not a constant to look
    up, it is the argument to a call -- and reading it back means finding the
    call and the three immediates in front of it.

    Returns (address, vector number) or (None, None).
    """
    for (bank, pc), (mn, _md, operand, _n) in sorted(t.code.items()):
        if mn not in ("JMP", "JSR") or operand != SETVBV:
            continue
        args = {}
        back = pc
        for _ in range(6):                # LDA/LDX/LDY #imm, in any order
            back -= 2
            e = t.code.get((bank, back))
            if not e or e[1] != "imm" or e[0] not in ("LDA", "LDX", "LDY"):
                break
            args[e[0]] = e[2]
        if "LDX" in args and "LDY" in args:
            return (args["LDX"] << 8) | args["LDY"], args.get("LDA")
    return None, None


def frame_steps(space, t, vbi):
    """The calls the handler makes, in the order it makes them."""
    steps, pc = [], vbi
    while True:
        e = t.code.get((Scene.RESIDENT, pc)) or decode(space, t, Scene.RESIDENT, pc)
        if e is None:
            break
        t.code.setdefault((Scene.RESIDENT, pc), e)
        if e[0] == "JSR":
            steps.append(pc)
        pc += e[3]
        if e[0] in ("RTI", "RTS", "JMP"):
            break
    return steps


def reach(space, t, entry, bank=None):
    """Everything one routine can run, and what hardware it ends up touching.

    Follows calls and both sides of branches, the same walk `walk` does, but
    starting from one routine and reporting only what it covers. Undercounts
    where a jump table intervenes, so read the chip list as "at least".
    """
    bank = Scene.RESIDENT if bank is None else bank
    seen, work, hw = set(), [entry], collections.Counter()
    while work:
        pc = work.pop()
        while True:
            if pc in seen:
                break
            e = t.code.get((bank, pc)) or decode(space, t, bank, pc)
            if e is None:
                break
            t.code.setdefault((bank, pc), e)
            seen.add(pc)
            mn, mode, operand, size = e
            if operand is not None and mode in ("abs", "abx", "aby",
                                                "zp", "zpx", "zpy"):
                folded = portscan.unmirror(operand)
                if folded in portscan.A8:
                    chip, name = portscan.A8[folded]
                    hw["%s %s" % (chip, reg_name(name, mn in m6502.STORES))] += 1
            pc += size
            if mn == "JSR":
                work.append(operand)
                continue
            if mn in m6502.BRANCHES:
                work.append((pc + ((operand ^ 0x80) - 0x80)) & 0xFFFF)
                continue
            if mn == "JMP":
                if mode == "abs":
                    work.append(operand)
                break
            if mn in ("RTS", "RTI", "BRK"):
                break
    return seen, hw


def frame_report(space, t, out):
    vbi, which = frame_handler(space, t)
    if vbi is None:
        out.append("no vertical blank handler was installed on any path the "
                   "trace reached.")
        return
    kind = {6: "immediate", 7: "deferred"}.get(which, "vector %s" % which)
    out.append("the game installs a %s vertical blank handler at $%04X (%s)"
               % (kind, vbi, space.where(vbi)))
    out.append("")
    steps = frame_steps(space, t, vbi)
    out.append("it runs %d routines and returns. Every frame, in this order:"
               % len(steps))
    out.append("")
    out.append("   #  calls   lives in           reaches  and touches")
    for i, pc in enumerate(steps, 1):
        target = t.code[(Scene.RESIDENT, pc)][2]
        seen, hw = reach(space, t, target)
        chips = ", ".join("%s(%d)" % (k, v) for k, v in hw.most_common(4))
        out.append("  %2d  $%04X  %-18s %5d B  %s"
                   % (i, target, space.where(target).split(" + ")[0],
                      len(seen), chips or "-"))
    out.append("")
    out.append("Nothing here is a game entity. This is presentation: sound, "
               "timers, the display list, the")
    out.append("player-missile registers, the controls. The game's own logic "
               "runs in the main line, which")
    out.append("the handler interrupts -- which is why steps 1 and %d save and "
               "restore the zero-page bytes" % len(steps))
    out.append("both halves use.")


def report(cart, t, out):
    v = cart.vectors()
    out.append("%d banks of 8K; bank %d is fixed at $A000-$BFFF, the rest "
               "page in at $8000-$9FFF" % (cart.banks, cart.fixed))
    out.append("  init  $%04X      start $%04X      option $%02X"
               % (v["init"], v["start"], v["option"]))
    out.append("")

    total = sum(size for _k, (_m, _md, _o, size) in t.code.items())
    out.append("reached by following the code")
    if isinstance(cart, Scene):
        out.append("  scene %d: bank %d at $1000 (code), bank %d at $6000 "
                   "(data)," % (cart.scene, cart.code_bank, cart.data_bank))
        out.append("           bank %d at $0480 (common), bank %d at $8000"
                   % (Scene.COMMON[0], Scene.RESIDENT))
        spans = collections.Counter()
        for (b, pc), (_m, _md, _o, size) in t.code.items():
            spans[cart.where(pc).split(" + ")[0]] += size
        for src, got in spans.most_common():
            out.append("  %-22s %5d bytes" % (src, got))
    else:
        for b in range(cart.banks):
            got = t.reached(b)
            if got:
                out.append("  bank %-2d  %5d bytes  %3.0f%% of the bank"
                           % (b, got, 100.0 * got / BANK_SIZE))
    out.append("  %d bytes of instructions in all" % total)
    out.append("")

    out.append("bank switching")
    known = [s for s in t.switches if s["to"] is not None]
    unknown = [s for s in t.switches if s["to"] is None]
    if not t.switches:
        out.append("  none reached")
    if known:
        by = collections.Counter(s["to"] % cart.banks for s in known)
        out.append("  %d sites select a bank the tracer could read:" % len(known))
        out.append("    " + ", ".join("bank %d (%dx)" % (b, n)
                                      for b, n in sorted(by.items())))
    if unknown:
        out.append("  %d sites compute the bank; a human has to read what feeds "
                   "them:" % len(unknown))
        for s in unknown[:12]:
            out.append("    bank %-2d $%04X  %s" % (s["bank"], s["at"], s["op"]))
    out.append("")

    if t.installed:
        out.append("interrupt and OS vectors written")
        for addr in sorted(t.installed):
            vals = [x for x in t.installed[addr] if x is not None]
            out.append("  $%04X  %-42s %s"
                       % (addr, OS_VECTORS.get(addr, ""),
                          " ".join("#$%02X" % x for x in sorted(vals)) or "?"))
        out.append("")

    by_chip = collections.Counter(h["chip"] for h in t.hw)
    out.append("hardware the reached code actually touches")
    if not by_chip:
        out.append("  none")
    for chip, n in by_chip.most_common():
        regs = collections.Counter(reg_name(h["name"], h["write"])
                                   for h in t.hw if h["chip"] == chip)
        out.append("  %-6s %4d  %s" % (chip, n, ", ".join(
            "%s(%d)" % (r, k) for r, k in regs.most_common(6))))
    out.append("")

    rt = routines(t)
    out.append("%d routines are called; grouped by what they talk to" % len(rt))
    groups = collections.defaultdict(list)
    for key, info in rt.items():
        groups[frozenset(info["chips"])].append((key, info))
    order = sorted(groups, key=lambda g: -len(groups[g]))
    for g in order:
        rows = sorted(groups[g], key=lambda x: -x[1]["callers"])
        label = "+".join(sorted(g)) if g else "no hardware (pure logic)"
        out.append("  %-22s %3d routines" % (label, len(rows)))
        if g:
            for (bank, target), info in rows[:8]:
                out.append("      bank %-2d $%04X  %3d bytes  %d callers"
                           % (bank, target, info["size"], info["callers"]))
    out.append("")

    if t.indirect:
        out.append("%d indirect jumps (dispatch tables live behind these)"
                   % len(t.indirect))
        for j in t.indirect[:10]:
            out.append("  bank %-2d $%04X  JMP ($%04X) -> %s"
                       % (j["bank"], j["at"], j["via"],
                          "$%04X" % j["to"] if j["to"] else "not in ROM"))
        out.append("")
    if t.left:
        out.append("%d jumps leave cartridge space (RAM-resident code)"
                   % len(t.left))
        for bank, pc in t.left[:8]:
            out.append("  bank %-2d -> $%04X" % (bank, pc))
        out.append("")
    if t.bailed:
        why = collections.Counter(b[2] for b in t.bailed)
        out.append("the walk stopped %d times on something that is not an "
                   "instruction" % len(t.bailed))
        out.append("  " + ", ".join("%s x%d" % (k, n)
                                    for k, n in why.most_common(6)))
        out.append("  That is normal: it is where code runs into its own data.")


def main():
    ap = argparse.ArgumentParser(
        description=__doc__.strip().split("\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("image")
    ap.add_argument("--at", help="disassemble from this address (hex)")
    ap.add_argument("--bank", default="0", help="bank live at $8000-$9FFF")
    ap.add_argument("--count", type=int, default=64)
    ap.add_argument("--scene", type=int, metavar="N",
                    help="run the loader for scene N (0-6) and trace the game "
                         "in the address space it builds")
    ap.add_argument("--from", dest="entry", metavar="ADDR",
                    help="extra entry point for the trace (hex)")
    ap.add_argument("--frame", action="store_true",
                    help="what the game does every vertical blank")
    ap.add_argument("--overlays", action="store_true",
                    help="which cartridge bank each scene loads")
    ap.add_argument("--hardware", metavar="CHIP",
                    help="list every reached access to one chip")
    args = ap.parse_args()

    blob = io.open(args.image, "rb").read()
    cart = Cart(blob, args.image)
    v = cart.vectors()

    if args.overlays:
        print("the loader copies three banks into RAM and pages a fourth in")
        print("")
        print("  scene   $0480 common   $1000 code   $6000 data   $8000")
        for n in range(Scene.SCENES):
            sc = Scene(cart, n)
            print("    %d          bank %-2d       bank %-2d      bank %-2d     "
                  "bank %d" % (n, Scene.COMMON[0], sc.code_bank, sc.data_bank,
                               Scene.RESIDENT))
        print("")
        print("  Bank 15 is fixed at $A000-$BFFF and holds the loader itself.")
        return 0

    entries = [(0, v["init"], "cartridge init vector"),
               (0, v["start"], "cartridge start vector")]
    if args.scene is not None:
        cart = Scene(cart, args.scene)
        # what the start vector jumps to once the overlays are in place
        entries = [(Scene.RESIDENT, 0x7760, "the game, after loading"),
                   (Scene.RESIDENT, 0x2F5A, "the overlay loader in RAM")]
    if args.entry:
        entries.append((int(args.bank, 0), int(args.entry, 16), "asked for"))
    t = walk(cart, entries)

    print("%s  (%d bytes of ROM%s)"
          % (os.path.basename(args.image), len(cart.rom),
             ", CART type %s" % cart.kind if cart.kind else ""))
    print("")

    if args.hardware:
        want = args.hardware.upper()
        rows = [h for h in t.hw if h["chip"] == want]
        print("every reached %s access (%d):" % (want, len(rows)))
        for h in rows[:300]:
            print("  bank %-2d $%04X  %-4s %-26s %s"
                  % (h["bank"], h["at"], h["op"], h["name"],
                     "write" if h["write"] else "read"))
        return 0

    if args.at:
        bank = int(args.bank, 0) if args.scene is None else Scene.RESIDENT
        for ln in listing(cart, t, bank, int(args.at, 16), args.count):
            print(ln)
        print("")
        print("  lines marked * were decoded on request, not reached by the "
              "trace; treat them as a guess about where instructions start.")
        return 0

    out = []
    if args.frame:
        if args.scene is None:
            print("--frame needs --scene N: the handler lives in RAM, so it "
                  "only exists once the loader has run.")
            return 2
        frame_report(cart, t, out)
    else:
        report(cart, t, out)
    for ln in out:
        print(ln)
    return 0


if __name__ == "__main__":
    sys.exit(main())
