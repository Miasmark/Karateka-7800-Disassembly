#!/usr/bin/env python3
"""
Run a cartridge's own code, and listen to what it writes to the sound chip.

    python tools/sim.py game.a78 --seconds 20 -o game.log

`capture.py` gets the same log by driving an emulator. This gets it by
executing the 6502 directly: RAM, the cartridge mapper, enough of MARIA to keep
a game's timing loop happy, and a trap on every write to TIA or POKEY.

The reason to have it is not to replace the emulator, which is better at being
an emulator. It is that **a player written for a format nobody has described is
still the authority on that format.** Reimplementing RMT's replayer means
reading 803 instructions and hoping; running it means the answer is correct by
construction. The same goes for every other player in the library.

## State: unfinished, and the gap is MARIA

The 6502 core works. It boots cartridges, runs their startup code, switches
banks, takes interrupts and reaches their main loops -- checked against real
disassemblies instruction by instruction.

What it does **not** yet do is get a game as far as its music, and the reason is
always the same: **MARIA's display interrupt.** One NMI a frame is not what the
hardware does. MARIA raises a DLI per display-list zone -- many a frame, at
positions the game chooses -- and players hang off those. Concretely:

  * Midnight Mutants runs, but its music routine is never called: its timing
    comes from DLIs, not from the frame.
  * The RMT demos install their handler through a RAM vector at `$0042` that
    only gets written once the display list is running. Without DLIs the vector
    stays zero, and forcing an NMI through it corrupts the stack.

So finishing this means walking the display list the game builds in RAM --
which `dlwalk.py` already knows how to do -- and raising an NMI at each zone
boundary. That is the remaining work, and it is not small.

Until then `capture.py` is the way to hear a cartridge, and this is groundwork.
See `docs/emulation.md`.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cart as cart_module
import m6502

# Flags
C, Z, I, D, B, U, V, N = 1, 2, 4, 8, 16, 32, 64, 128

# `m6502.BRANCHES` is a set of names; the core needs to know which flag each
# one tests and which way round.
BRANCH_ON = {"BCC": (C, False), "BCS": (C, True),
             "BNE": (Z, False), "BEQ": (Z, True),
             "BPL": (N, False), "BMI": (N, True),
             "BVC": (V, False), "BVS": (V, True)}


class Bus(object):
    """The 7800's memory map, as much of it as sound needs.

    RAM, the cartridge through its mapper, and traps on the audio registers.
    MARIA is present only as far as a timing loop can tell: `MSTAT` bit 7 says
    whether the machine is in vertical blank, which is what a game waits on.
    """

    def __init__(self, cart, drive=False):
        self.cart = cart
        self.ram = bytearray(0x10000)
        self.bank = cart.nbanks - 1 if cart.nbanks > 1 else 0
        self.audio = {}                 # address -> last value written
        self.writes = []                # (frame, address, value)
        self.frame = 0
        self.vblank = False
        self.drive = drive
        self.pokeys = set()
        for base in cart.pokeys():
            for r in range(16):
                self.pokeys.add(base + r)

    # -- reads
    def read(self, a):
        a &= 0xFFFF
        if a >= 0x4000:
            sp = self.cart.space_of(a, self.bank)
            if sp is not None:
                try:
                    return self.cart.byte(sp, a)
                except Exception:                            # noqa: BLE001
                    return 0xFF
            return self.ram[a]
        if (a & 0xFF) == 0x28 and 0x20 <= (a & 0xFF) <= 0x3F:
            # MSTAT: bit 7 set during vertical blank
            return 0x80 if self.vblank else 0x00
        if a in (0x0008, 0x0009, 0x000A, 0x000B):
            return 0x00                  # INPT0-3: no paddles
        if a in (0x000C, 0x000D):
            # INPT4/5: fire buttons, active low
            return 0x00 if self.drive else 0x80
        if a == 0x0280:
            # SWCHA: joystick directions, also active low. All ones is centred.
            return 0xFF
        if a == 0x0282:
            # SWCHB: the console switches, and they are **active low** -- a set
            # bit means "not pressed". Returning zeros here reads as reset and
            # select both held down, and a game that waits for them to be
            # released waits forever. Midnight Mutants spins on exactly that.
            #   bit 0 reset, bit 1 select, bit 3 colour/BW
            return 0x0B
        return self.ram[a]

    # -- writes
    def write(self, a, v):
        a &= 0xFFFF
        v &= 0xFF
        if a >= 0x8000 and self.cart.nbanks > 1:
            b = self.cart.map.bank_from_write(a, v)
            if b is not None:
                self.bank = b
                return
        if a >= 0x4000:
            if a in self.pokeys:
                self.audio[a] = v
                self.writes.append((self.frame, a, v))
                return
            return                       # ROM: writes go nowhere
        low = a & 0xFF
        if a < 0x0400 and 0x15 <= low <= 0x1A and (a & 0x300) in (0, 0x100, 0x200):
            self.audio[0x0000 + low] = v
            self.writes.append((self.frame, low, v))
            return
        if a in self.pokeys:
            self.audio[a] = v
            self.writes.append((self.frame, a, v))
            return
        self.ram[a] = v


class CPU(object):
    """A 6502, written against the tables in `m6502.py`.

    Plain and unhurried rather than fast: correctness here is the whole point,
    since a subtly wrong core produces a plausible log. It is checked against a
    real emulator's capture of a real game -- see `--verify`.
    """

    def __init__(self, bus):
        self.bus = bus
        self.a = self.x = self.y = 0
        self.s = 0xFD
        self.p = U | I
        self.pc = self.word(0xFFFC)
        self.cycles = 0

    def word(self, a):
        return self.bus.read(a) | (self.bus.read(a + 1) << 8)

    def push(self, v):
        self.bus.write(0x0100 + self.s, v & 0xFF)
        self.s = (self.s - 1) & 0xFF

    def pop(self):
        self.s = (self.s + 1) & 0xFF
        return self.bus.read(0x0100 + self.s)

    def nmi(self):
        self.push((self.pc >> 8) & 0xFF)
        self.push(self.pc & 0xFF)
        self.push((self.p | U) & ~B & 0xFF)
        self.p |= I
        self.pc = self.word(0xFFFA)
        self.cycles += 7

    def setzn(self, v):
        v &= 0xFF
        self.p = (self.p & ~(Z | N)) | (Z if v == 0 else 0) | (v & N)
        return v

    def addr(self, mode):
        """Resolve an operand address. Returns (address, extra_cycles)."""
        b = self.bus
        pc = self.pc
        if mode == "imm":
            self.pc += 1
            return pc, 0
        if mode == "zp":
            self.pc += 1
            return b.read(pc), 0
        if mode == "zpx":
            self.pc += 1
            return (b.read(pc) + self.x) & 0xFF, 0
        if mode == "zpy":
            self.pc += 1
            return (b.read(pc) + self.y) & 0xFF, 0
        if mode == "abs":
            self.pc += 2
            return b.read(pc) | (b.read(pc + 1) << 8), 0
        if mode == "abx":
            self.pc += 2
            base = b.read(pc) | (b.read(pc + 1) << 8)
            a = (base + self.x) & 0xFFFF
            return a, 1 if (a ^ base) & 0xFF00 else 0
        if mode == "aby":
            self.pc += 2
            base = b.read(pc) | (b.read(pc + 1) << 8)
            a = (base + self.y) & 0xFFFF
            return a, 1 if (a ^ base) & 0xFF00 else 0
        if mode == "izx":
            self.pc += 1
            z = (b.read(pc) + self.x) & 0xFF
            return b.read(z) | (b.read((z + 1) & 0xFF) << 8), 0
        if mode == "izy":
            self.pc += 1
            z = b.read(pc)
            base = b.read(z) | (b.read((z + 1) & 0xFF) << 8)
            a = (base + self.y) & 0xFFFF
            return a, 1 if (a ^ base) & 0xFF00 else 0
        if mode == "ind":
            self.pc += 2
            p = b.read(pc) | (b.read(pc + 1) << 8)
            # the page-wrap bug is real hardware and games rely on it
            lo = b.read(p)
            hi = b.read((p & 0xFF00) | ((p + 1) & 0xFF))
            return lo | (hi << 8), 0
        if mode == "rel":
            self.pc += 1
            off = b.read(pc)
            return (self.pc + (off - 256 if off & 0x80 else off)) & 0xFFFF, 0
        return 0, 0                      # imp, acc

    def compare(self, reg, v):
        d = (reg - v) & 0x1FF
        self.p = (self.p & ~C) | (1 if reg >= v else 0)
        self.setzn(d & 0xFF)

    def adc(self, v):
        if self.p & D:
            # BCD. Rare in players but not unheard of, and silently wrong
            # arithmetic is exactly the kind of bug that hides.
            lo = (self.a & 0x0F) + (v & 0x0F) + (self.p & C)
            hi = (self.a >> 4) + (v >> 4) + (1 if lo > 9 else 0)
            if lo > 9:
                lo += 6
            r = ((hi << 4) | (lo & 0x0F)) & 0xFF
            if hi > 9:
                hi += 6
                r = ((hi << 4) | (lo & 0x0F)) & 0xFF
            self.p = (self.p & ~C) | (1 if hi > 15 else 0)
            self.setzn(r)
            self.a = r
            return
        t = self.a + v + (self.p & C)
        self.p = (self.p & ~(C | V)) | (1 if t > 0xFF else 0)
        if (~(self.a ^ v) & (self.a ^ t)) & 0x80:
            self.p |= V
        self.a = self.setzn(t)

    def sbc(self, v):
        if self.p & D:
            lo = (self.a & 0x0F) - (v & 0x0F) - (1 - (self.p & C))
            hi = (self.a >> 4) - (v >> 4)
            if lo & 0x10:
                lo -= 6
                hi -= 1
            if hi & 0x10:
                hi -= 6
            t = self.a - v - (1 - (self.p & C))
            self.p = (self.p & ~C) | (0 if t & 0x100 else 1)
            self.a = self.setzn(((hi & 0x0F) << 4) | (lo & 0x0F))
            return
        self.adc(v ^ 0xFF)

    def step(self):
        b = self.bus
        op = b.read(self.pc)
        entry = m6502.OPCODES.get(op)
        self.pc = (self.pc + 1) & 0xFFFF
        if entry is None:
            self.cycles += 2
            return
        mn, mode = entry[0], entry[1]
        cyc = m6502.CYCLES[op]
        a, extra = self.addr(mode)

        def rd():
            return b.read(a)

        if mn == "LDA":
            self.a = self.setzn(rd())
        elif mn == "LDX":
            self.x = self.setzn(rd())
        elif mn == "LDY":
            self.y = self.setzn(rd())
        elif mn == "STA":
            b.write(a, self.a)
        elif mn == "STX":
            b.write(a, self.x)
        elif mn == "STY":
            b.write(a, self.y)
        elif mn == "TAX":
            self.x = self.setzn(self.a)
        elif mn == "TAY":
            self.y = self.setzn(self.a)
        elif mn == "TXA":
            self.a = self.setzn(self.x)
        elif mn == "TYA":
            self.a = self.setzn(self.y)
        elif mn == "TSX":
            self.x = self.setzn(self.s)
        elif mn == "TXS":
            self.s = self.x
        elif mn == "PHA":
            self.push(self.a)
        elif mn == "PLA":
            self.a = self.setzn(self.pop())
        elif mn == "PHP":
            self.push(self.p | U | B)
        elif mn == "PLP":
            self.p = (self.pop() | U) & ~B
        elif mn == "AND":
            self.a = self.setzn(self.a & rd())
        elif mn == "ORA":
            self.a = self.setzn(self.a | rd())
        elif mn == "EOR":
            self.a = self.setzn(self.a ^ rd())
        elif mn == "ADC":
            self.adc(rd())
        elif mn == "SBC":
            self.sbc(rd())
        elif mn == "CMP":
            self.compare(self.a, rd())
        elif mn == "CPX":
            self.compare(self.x, rd())
        elif mn == "CPY":
            self.compare(self.y, rd())
        elif mn == "INC":
            b.write(a, self.setzn(rd() + 1))
        elif mn == "DEC":
            b.write(a, self.setzn(rd() - 1))
        elif mn == "INX":
            self.x = self.setzn(self.x + 1)
        elif mn == "INY":
            self.y = self.setzn(self.y + 1)
        elif mn == "DEX":
            self.x = self.setzn(self.x - 1)
        elif mn == "DEY":
            self.y = self.setzn(self.y - 1)
        elif mn in ("ASL", "LSR", "ROL", "ROR"):
            v = self.a if mode == "acc" else rd()
            if mn == "ASL":
                c, v = (v >> 7) & 1, (v << 1) & 0xFF
            elif mn == "LSR":
                c, v = v & 1, v >> 1
            elif mn == "ROL":
                c, v = (v >> 7) & 1, ((v << 1) | (self.p & C)) & 0xFF
            else:
                c, v = v & 1, ((v >> 1) | ((self.p & C) << 7)) & 0xFF
            self.p = (self.p & ~C) | c
            v = self.setzn(v)
            if mode == "acc":
                self.a = v
            else:
                b.write(a, v)
        elif mn == "BIT":
            v = rd()
            self.p = (self.p & ~(Z | N | V)) | (Z if not (self.a & v) else 0) \
                | (v & (N | V))
        elif mn == "JMP":
            self.pc = a
        elif mn == "JSR":
            r = (self.pc - 1) & 0xFFFF
            self.push((r >> 8) & 0xFF)
            self.push(r & 0xFF)
            self.pc = a
        elif mn == "RTS":
            self.pc = (self.pop() | (self.pop() << 8)) + 1 & 0xFFFF
        elif mn == "RTI":
            self.p = (self.pop() | U) & ~B
            self.pc = self.pop() | (self.pop() << 8)
        elif mn in BRANCH_ON:
            bit, want = BRANCH_ON[mn]
            if bool(self.p & bit) == want:
                cyc += 1 + (1 if (a ^ self.pc) & 0xFF00 else 0)
                self.pc = a
        elif mn == "CLC":
            self.p &= ~C
        elif mn == "SEC":
            self.p |= C
        elif mn == "CLI":
            self.p &= ~I
        elif mn == "SEI":
            self.p |= I
        elif mn == "CLV":
            self.p &= ~V
        elif mn == "CLD":
            self.p &= ~D
        elif mn == "SED":
            self.p |= D
        elif mn == "BRK":
            self.pc = (self.pc + 1) & 0xFFFF
            self.push((self.pc >> 8) & 0xFF)
            self.push(self.pc & 0xFF)
            self.push(self.p | U | B)
            self.p |= I
            self.pc = self.word(0xFFFE)
        elif mn == "NOP":
            pass
        else:
            pass                          # undocumented: treated as a NOP
        if extra and mn in ("LDA", "LDX", "LDY", "ADC", "SBC", "AND", "ORA",
                            "EOR", "CMP"):
            cyc += extra
        self.cycles += cyc


# NTSC: 3.579545 MHz colour clock / 2 for the CPU, 262 lines a frame.
CYCLES_PER_FRAME = {"ntsc": 29829, "pal": 35780}
VBLANK_FRACTION = 0.12


def run(cart, frames, region="ntsc", drive=False, nmi=True, quiet=False):
    """Execute the cartridge for `frames` frames, collecting audio writes."""
    bus = Bus(cart, drive=drive)
    cpu = CPU(bus)
    per = CYCLES_PER_FRAME[region]
    for f in range(frames):
        bus.frame = f + 1
        target = cpu.cycles + per
        vb_at = cpu.cycles + int(per * (1.0 - VBLANK_FRACTION))
        bus.vblank = False
        while cpu.cycles < target:
            if not bus.vblank and cpu.cycles >= vb_at:
                bus.vblank = True
                # MARIA raises NMI at the end of the visible screen, and a
                # great many games do their whole frame's work in that handler.
                #
                # NMI is **non-maskable**: the I flag does not block it. Gating
                # on I here meant the handler never ran for any game that sets
                # I and leaves it set, which is most of them -- and the symptom
                # was a game that runs, spins in its main loop and never plays
                # a note.
                if nmi:
                    cpu.nmi()
            cpu.step()
    return bus


def write_log(bus, cart, path, region="ntsc"):
    """The same per-frame format probes/audio.lua produces."""
    bases = cart.pokeys()
    nvals = (9 * len(bases)) if bases else 6
    slot = {}
    if bases:
        for i, base in enumerate(bases):
            for r in range(9):
                slot[base + r] = i * 9 + r
    else:
        for off, k in ((0x15, 0), (0x17, 1), (0x19, 2),
                       (0x16, 3), (0x18, 4), (0x1A, 5)):
            slot[off] = k
    cur = [0] * nvals
    last = [-1] * nvals
    rows = []
    byframe = {}
    for f, a, v in bus.writes:
        byframe.setdefault(f, []).append((a, v))
    for f in sorted(byframe):
        for a, v in byframe[f]:
            k = slot.get(a)
            if k is not None:
                cur[k] = v
        if cur != last:
            rows.append((f, list(cur)))
            last = list(cur)
    with open(path, "w", encoding="utf-8") as fh:
        if bases:
            names = ",".join("$%04X" % b for b in bases)
            fh.write("# chip %s  base %s\n"
                     % ("pokey2" if len(bases) > 1 else "pokey", names))
            heads = []
            for i in range(len(bases)):
                n = i * 4
                heads.append("f%d c%d  f%d c%d  f%d c%d  f%d c%d  ctl"
                             % (n+1, n+1, n+2, n+2, n+3, n+3, n+4, n+4))
            fh.write("# frame  " + "   ".join(heads) + "   (hex)\n")
        else:
            fh.write("# chip tia\n")
            fh.write("# frame  c0 f0 v0  c1 f1 v1   (hex)\n")
        for f, vals in rows:
            fh.write("%d %s\n" % (f, " ".join("%02X" % v for v in vals)))
    return len(rows)


def main():
    ap = argparse.ArgumentParser(
        description=__doc__.strip().split("\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("rom")
    ap.add_argument("-o", "--out")
    ap.add_argument("--seconds", type=float, default=20.0)
    ap.add_argument("--frames", type=int)
    ap.add_argument("--drive", action="store_true",
                    help="hold fire, for a game that waits at a title screen")
    ap.add_argument("--no-nmi", action="store_true",
                    help="do not call the NMI handler each frame")
    ap.add_argument("--verify", metavar="LOG",
                    help="an emulator capture of the same game, to check "
                         "this against")
    args = ap.parse_args()

    try:
        cart = cart_module.Cart(args.rom)
    except (cart_module.UnknownMapper, cart_module.UnknownSpace, IOError) as e:
        sys.stderr.write("%s\n" % e)
        return 2
    region = ((cart.info or {}).get("region", "NTSC")).lower()
    frames = args.frames or int(args.seconds * (50 if region == "pal" else 60))

    bus = run(cart, frames, region, drive=args.drive, nmi=not args.no_nmi)
    out = args.out or (os.path.splitext(args.rom)[0] + "-sim.log")
    n = write_log(bus, cart, out, region)
    print("%s -- %d frames simulated, %d audio writes, %d changed rows"
          % (os.path.basename(out), frames, len(bus.writes), n))
    if not bus.writes:
        print("  Nothing was written to the sound chip. The game may need "
              "input (--drive),")
        print("  or may depend on hardware this does not provide. capture.py "
              "runs a real")
        print("  emulator and does not care.")

    if args.verify:
        import tracker
        a = list(tracker.read_capture(out, region).states())
        b = list(tracker.read_capture(args.verify, region).states())
        n2 = min(len(a), len(b))
        best = (0, -1)
        for off in range(0, min(400, n2 - 60)):
            same = sum(1 for i in range(n2 - off) if a[i + off] == b[i])
            if same > best[1]:
                best = (off, same)
            same = sum(1 for i in range(n2 - off) if b[i + off] == a[i])
            if same > best[1]:
                best = (-off, same)
        off, same = best
        total = n2 - abs(off)
        print("  verify: %d of %d rows identical (%.1f%%) at offset %d"
              % (same, total, 100.0 * same / max(total, 1), off))
    return 0


if __name__ == "__main__":
    sys.exit(main())
