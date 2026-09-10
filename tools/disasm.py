#!/usr/bin/env python3
"""
Bank-aware recursive-descent 6502 disassembler for Atari 7800 cartridges.

The address map comes from cart.py, so linear ROMs (4K-48K), SuperGame images
and their bank6/RAM/extra-ROM variants all work without changing anything here.
A SuperGame cart, for example, resolves to:
    $4000-$7FFF  fixed  -> ROM bank 6          space "f6"
    $8000-$BFFF  banked -> ROM bank 0..7       space "b0".."b7"
    $C000-$FFFF  fixed  -> ROM bank 7 (last)   space "f7"
    a write to $8000-$FFFF sets the window bank -- where the switch lives and
    how the value maps to a bank both come from the mapper
while a 48K linear cart is a single space called "rom".

The tracer follows control flow from the reset/NMI/IRQ vectors, carrying an
abstract value for A/X/Y (immediate constants only) so that the
`LDA #n / STA $8000` bank-switch idiom resolves automatically.  Sites where the
bank value is not a tracked constant are reported as unresolved so they can be
pinned down by hand in the annotation file.

Usage:
    python disasm.py <rom.a78> [-c annotations.json] [-o outdir]
"""
import argparse
import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import m6502
import a7800
import cart
import cart as cart_module

BANK_SIZE = 0x4000


# The cartridge model lives in cart.py; Cart is re-exported so callers that
# only want a disassembler do not have to know that.
class Cart(cart.Cart):
    @property
    def header(self):
        """The .a78 header bytes, or None -- the old attribute name."""
        return self.header_bytes


# ------------------------------------------------------------------ analysis
class Analyzer:
    def __init__(self, cart, cfg):
        self.cart = cart
        self.cfg = cfg
        self.code = set()                    # (space, addr) -> first byte of insn
        self.insn = {}                       # (space, addr) -> (mn, mode, operand, length)
        self.labels = {}                     # (space, addr) -> name
        self.kinds = {}                      # (space, addr) -> 'sub'|'code'|'data'
        self.xrefs = defaultdict(set)        # (space, addr) -> {(space, addr) of referrer}
        self.ramrefs = defaultdict(set)      # ram addr -> {referrers}
        self.bankswitch = {}                 # (space, addr) -> bank, list, or None
        self.bankseen = {}                   # (space, addr) -> {banks resolved here}
        self.unresolved = []                 # sites we could not resolve
        self.forced_data = set()             # (space, addr) covered by a data block
        self.illegal_stops = set()           # traces abandoned on an illegal opcode
        self._pending = []

    # -- helpers -------------------------------------------------------------
    def mark(self, loc, kind):
        if self.kinds.get(loc) != "sub":
            self.kinds[loc] = kind

    def add_entry(self, space, addr, bank, kind="code", src=None, regs=None):
        """Queue a trace.

        `regs` carries (A, X, Y) across a transfer that is a *continuation* of
        the same instruction stream -- a JMP or a taken branch, neither of which
        touches the registers. That is what lets `LDA #n / JMP trampoline`
        resolve: at the JMP the accumulator provably still holds n, and bank
        trampolines reached that way are common enough to matter.

        It is deliberately not passed for JSR. A subroutine is reached from many
        callers, and pinning it to one caller's registers would be a guess.
        """
        if space is None:
            if src:
                self.unresolved.append((src, addr))
            return
        if not self.cart.in_space(space, addr):
            return
        self.mark((space, addr), kind)
        if src:
            self.xrefs[(space, addr)].add(src)
        self._pending.append((space, addr, bank, regs))

    def target_space(self, cur_space, tgt, bank):
        """The space a control transfer lands in.

        `space_of` needs to be told which bank is in the window, and when the
        tracer does not know, it returns None -- the transfer is recorded as
        unresolved and no cross-reference is kept. For a transfer *within* the
        window that is needlessly pessimistic: a branch is relative and cannot
        leave its bank, and a JMP or JSR to an address in the same window
        region runs before any switch could take effect. Either way the target
        is in the bank already executing, whether or not the tracer can name
        which one that is.

        Without this, code reached with an unknown bank kept no cross-references
        at all, so none of its branch targets earned a label -- b5:$B96E is
        branched to from three bytes earlier and still printed as a bare
        address.
        """
        got = self.cart.space_of(tgt, bank)
        if got is not None:
            return got
        if cur_space.startswith("b"):
            for start, end, kind, _arg in self.cart._region:
                if start <= tgt < end and kind == "window":
                    return cur_space
        return None

    # -- the trace -----------------------------------------------------------
    def run(self, entries):
        for e in entries:
            self.add_entry(e[0], e[1], e[2], "sub")
        seen = set()
        while self._pending:
            item = self._pending.pop()
            space, addr, bank = item[0], item[1], item[2]
            regs = item[3] if len(item) > 3 else None
            a, x, y = regs if regs else (None, None, None)
            while True:
                key = (space, addr, bank, a)
                if key in seen:
                    break
                seen.add(key)
                loc = (space, addr)
                if loc in self.forced_data or not self.cart.in_space(space, addr):
                    break
                # manual override: assert which bank is really in the $8000
                # window here, for paths the constant-tracker cannot follow
                pinned = self.cfg.bankat.get(fmt_loc(loc))
                if pinned is not None:
                    bank = pinned

                op = self.cart.byte(space, addr)
                mn, mode, illegal = m6502.OPCODES[op]
                # An undocumented opcode in a traced path means the path is not
                # code. This cartridge uses none in anything confirmed, so a
                # branch that lands on one is a static edge the machine never
                # takes -- b0:$BDEC branches into a text block that way. Walking
                # on emits instructions the assembler cannot reproduce, which
                # breaks the round trip.
                if illegal:
                    self.illegal_stops.add(loc)
                    break
                n = m6502.MODES[mode]
                if not self.cart.in_space(space, addr + n):
                    break
                operand = None
                if n == 1:
                    operand = self.cart.byte(space, addr + 1)
                elif n == 2:
                    operand = (self.cart.byte(space, addr + 1)
                               | (self.cart.byte(space, addr + 2) << 8))

                self.code.add(loc)
                self.insn[loc] = (mn, mode, operand, 1 + n)
                if loc not in self.kinds:
                    self.kinds[loc] = "code"

                nxt = addr + 1 + n

                # --- abstract A/X/Y (immediates only) ---
                if mn == "LDA":
                    a = operand if mode == "imm" else None
                elif mn == "LDX":
                    x = operand if mode == "imm" else None
                elif mn == "LDY":
                    y = operand if mode == "imm" else None
                elif mn == "TAX":
                    x = a
                elif mn == "TAY":
                    y = a
                elif mn == "TXA":
                    a = x
                elif mn == "TYA":
                    a = y
                elif mn in ("PLA",):
                    a = None
                elif mn in ("ADC", "SBC", "AND", "ORA", "EOR", "ASL", "LSR",
                            "ROL", "ROR", "LAX", "LDA"):
                    a = None
                elif mn in ("INX", "DEX"):
                    x = None if x is None else (x + (1 if mn == "INX" else -1)) & 0xFF
                elif mn in ("INY", "DEY"):
                    y = None if y is None else (y + (1 if mn == "INY" else -1)) & 0xFF

                # --- bank switching ---
                # Where a switch lives and what a written value means are the
                # mapper's business, not this loop's. A linear cart has no
                # switch at all, so a store into ROM there is just a store.
                sw = self.cart.map.switch
                if mn in m6502.STORES and mode in ("abs", "abx", "aby") \
                        and operand is not None and sw is not None \
                        and sw[0] <= operand <= sw[1]:
                    src = {"STA": a, "STX": x, "STY": y}.get(mn)
                    forced = self.cfg.banksw.get(fmt_loc(loc))
                    if isinstance(forced, list):
                        # bank number came from a table; fork the trace so every
                        # bank the table can select gets explored
                        self.bankswitch[loc] = forced
                        for b in forced[1:]:
                            self._pending.append((space, nxt, b, (a, x, y)))
                        bank = forced[0]
                    elif forced == "keep":
                        # e.g. `LDA cur_bank / STA $8000` -- restores whatever
                        # bank was already selected, so the tracer's idea of the
                        # current bank stays valid.
                        self.bankswitch[loc] = "keep"
                    else:
                        val = forced if forced is not None else src
                        sel = (None if val is None
                               else self.cart.map.bank_from_write(operand, val))
                        seen_here = self.bankseen.setdefault(loc, set())
                        if sel is None:
                            self.bankswitch.setdefault(loc, None)
                            self.unresolved.append((loc, None))
                            bank = None
                        else:
                            seen_here.add(sel)
                            self.bankswitch[loc] = (sorted(seen_here)[0]
                                                    if len(seen_here) == 1
                                                    else sorted(seen_here))
                            bank = sel

                # --- references ---
                elif mode in ("abs", "abx", "aby", "ind", "zp", "zpx", "zpy",
                              "izx", "izy") and operand is not None:
                    tgt = operand
                    if tgt >= 0x4000:
                        tspace = self.cart.space_of(tgt, bank)
                        if tspace:
                            self.xrefs[(tspace, tgt)].add(loc)
                            if (tspace, tgt) not in self.kinds:
                                self.kinds[(tspace, tgt)] = "data"
                    else:
                        self.ramrefs[tgt].add(loc)

                # --- control flow ---
                if mn == "JSR":
                    tspace = self.target_space(space, operand, bank)
                    self.add_entry(tspace, operand, bank, "sub", loc)
                elif mn == "JMP" and mode == "abs":
                    tspace = self.target_space(space, operand, bank)
                    self.add_entry(tspace, operand, bank, "sub", loc, (a, x, y))
                    break
                elif mn == "JMP" and mode == "ind":
                    break
                elif mn in m6502.BRANCHES:
                    tgt = (nxt + ((operand ^ 0x80) - 0x80)) & 0xFFFF
                    tspace = self.target_space(space, tgt, bank)
                    self.add_entry(tspace, tgt, bank, "code", loc, (a, x, y))
                elif mn in ("RTS", "RTI", "BRK", "JAM"):
                    break

                addr = nxt

    # LD{A,X,Y} #imm tracked per register; ST{A,X,Y} reads back whichever
    # register's tracked value is current. A handler doesn't have to use the
    # same register for both bytes of a vector -- e.g. `LDA #lo / LDY #hi /
    # STA vec_lo / STY vec_hi` is a common shape when A is freed up for
    # something else in between the two stores (found in Centipede: a second
    # NMI-vector target used exactly this shape and was invisible to an
    # A-only version of this scan even with the right `ram_vectors` entry --
    # see docs/pitfalls.md). Each register's tracked value is cleared by
    # anything that loads it other than an immediate -- mirroring, not
    # expanding, how conservative the original A-only version was: this does
    # not attempt to track values through arithmetic (ADC/INX/...), so it can
    # still miss a vector built that way, on the same terms the original
    # implementation already accepted for A.
    _VEC_LOADERS = {"A": "LDA", "X": "LDX", "Y": "LDY"}
    _VEC_STORERS = {"A": "STA", "X": "STX", "Y": "STY"}
    _VEC_CLOBBERS = {
        "A": ("LDA", "PLA", "TXA", "TYA"),
        "X": ("LDX", "TAX", "TSX"),
        "Y": ("LDY", "TAY"),
    }

    def scan_ram_vectors(self, lo_addr, hi_addr, window=24):
        """Find immediate-load + store pairs that write a 16-bit vector.

        MARIA's NMI goes through `JMP (nmi_vec)`, and each DLI installs the
        handler for the next zone, so the whole DLI chain is only reachable by
        following these stores.  Returns a list of (space, addr) handlers.
        """
        found = []
        spaces = sorted({s for (s, a) in self.code})
        for space in spaces:
            addrs = sorted(a for (s, a) in self.code if s == space)
            los, his = [], []
            last_imm = {"A": None, "X": None, "Y": None}
            for a in addrs:
                mn, mode, operand, length = self.insn[(space, a)]
                loaded = False
                for reg, ld in self._VEC_LOADERS.items():
                    if mn == ld and mode == "imm":
                        last_imm[reg] = operand
                        loaded = True
                if loaded:
                    continue
                stored = False
                for reg, st in self._VEC_STORERS.items():
                    if mn == st and mode in ("abs", "zp"):
                        v = last_imm[reg]
                        if operand == lo_addr and v is not None:
                            los.append((a, v))
                        elif operand == hi_addr and v is not None:
                            his.append((a, v))
                        stored = True
                if stored:
                    continue
                for reg, clobbers in self._VEC_CLOBBERS.items():
                    if mn in clobbers:
                        last_imm[reg] = None
            for la, lv in los:
                for ha, hv in his:
                    if abs(ha - la) <= window:
                        found.append((lv | (hv << 8), (space, la)))
        return found

    # -- naming --------------------------------------------------------------
    def name_all(self):
        for loc in sorted(set(self.kinds) | set(self.labels)):
            if fmt_loc(loc) in self.cfg.labels:
                self.labels[loc] = self.cfg.labels[fmt_loc(loc)]
                continue
            if loc in self.labels:
                continue
            # only name something that is actually referenced, or is a
            # subroutine entry -- otherwise every instruction gets a label
            if not self.xrefs.get(loc) and self.kinds.get(loc) != "sub":
                continue
            space, addr = loc
            tag = space[1:] if space.startswith("b") else ""
            kind = self.kinds.get(loc, "code")
            if kind == "sub" and loc in self.code:
                self.labels[loc] = "sub%s_%04X" % (tag, addr)
            elif kind == "data" or loc not in self.code:
                self.labels[loc] = "dat%s_%04X" % (tag, addr)
            else:
                self.labels[loc] = "L%s_%04X" % (tag, addr)


def fmt_loc(loc):
    return "%s:%04X" % (loc[0], loc[1])


def parse_loc(s):
    """'f6:$4A92', 'f6:4A92' and 'f6:0x4A92' all mean the same location."""
    space, addr = s.split(":")
    addr = addr.strip()
    if addr.startswith("$"):
        addr = addr[1:]
    elif addr.lower().startswith("0x"):
        addr = addr[2:]
    return (space, int(addr, 16))


# -------------------------------------------------------------------- config
class Config:
    def __init__(self, path=None):
        d = {}
        if path and os.path.exists(path):
            with open(path) as f:
                d = json.load(f)
        self.entries = [parse_loc(s) for s in d.get("entries", [])]
        self.labels = d.get("labels", {})
        self.comments = d.get("comments", {})
        self.headers = d.get("headers", {})       # block comment printed above a line
        self.ram = {int(k, 16): v for k, v in d.get("ram", {}).items()}
        self.banksw = {k: v for k, v in d.get("banksw", {}).items()}
        self.blocks = d.get("blocks", [])
        # Pairs of RAM addresses holding a jump vector the hardware or the game
        # calls through. MARIA's display-interrupt slot is the classic case;
        # leave it empty and nothing is lost but handlers reached only that way.
        self.ram_vectors = [tuple(int(str(x), 0) for x in pair)
                            for pair in d.get("ram_vectors", [])]
        self.bankat = d.get("bankat", {})
        self.notes = d.get("notes", {})


# ------------------------------------------------------------------- emitter
# (mnemonic, mode) pairs that exist as documented opcodes
MODESET = {(mn, md) for (mn, md, il) in m6502.OPCODES.values() if not il}
ZP_OF = {"abs": "zp", "abx": "zpx", "aby": "zpy"}


class Emitter:
    def __init__(self, cart, an, cfg, cycles=False):
        self.cart, self.an, self.cfg = cart, an, cfg
        self.cycles = cycles
        self.equates = {}               # name -> address, for unlabellable refs
        self.gfx = set()                # (space, addr) to draw rather than list
        self.gfx_wide = set()           # those drawn two bytes to a line
        self.used = defaultdict(dict)     # space -> {symbol name: address}
        self.emitted = defaultdict(set)   # space -> {label names actually written}

    def opnd_text(self, loc, mn, mode, operand, length):
        if mode in ("imp", "acc"):
            return m6502.FMT[mode]
        space, addr = loc
        if mode == "imm":
            return "#$%02X" % operand
        if loc in self.an.bankswitch:
            # a store into $8000-$FFFF is the SuperGame bank register, not a
            # reference to whatever code/data happens to live at that address
            return m6502.FMT[mode].format(v="$%04X" % operand)
        if mode == "rel":
            tgt = (addr + length + ((operand ^ 0x80) - 0x80)) & 0xFFFF
            return self.ref_name(loc, tgt)
        # memory operand
        v = self.ref_name(loc, operand,
                          zp=(mode in ("zp", "zpx", "zpy", "izx", "izy")),
                          mn=mn)
        return m6502.FMT[mode].format(v=v)

    def note(self, space, name, addr):
        self.used[space][name] = addr
        return name

    def ref_name(self, src, tgt, zp=False, mn=None):
        # A cartridge POKEY answers at $4000, $0450 or $0800 depending on the
        # header. Its registers read as one thing and write as another, and the
        # instruction says which we are looking at, so use the right name.
        ct = (self.cart.info or {}).get("cart_type", 0)
        pk = a7800.pokey_base(ct)
        if pk is not None and pk <= tgt < pk + 16:
            write = None if mn is None else (mn in m6502.STORES)
            return self.note(src[0], a7800.sym_for(tgt, ct, write), tgt)
        # hardware register?
        hw = a7800.HW.get(tgt)
        if hw and tgt < 0x0400:
            return self.note(src[0], hw, tgt)
        if tgt in self.cfg.ram:
            return self.note(src[0], self.cfg.ram[tgt], tgt)
        if tgt < 0x4000:
            if tgt < 0x0040:
                return "$%02X" % tgt
            return self.note(src[0], "ram_%04X" % tgt, tgt)
        # cart address: label if we have one in the same space (or fixed space)
        for space in self.spaces_for(src[0], tgt):
            if (space, tgt) in self.an.labels:
                return self.note(src[0], self.an.labels[(space, tgt)], tgt)
        # Referenced, but nothing in this file defines a label there -- the
        # address is inside a multi-byte instruction, or points into data that
        # was never traced (Midnight Mutants' sound-effect pointer table is
        # twelve such targets).
        # DiStella's answer is the right one: name it anyway and emit an
        # equate, so the cross-reference is not lost and the source still
        # assembles. Without this the operand prints as a bare number and the
        # reader has no way to tell it was referenced at all.
        if not zp and tgt >= 0x4000 and self.spaces_for(src[0], tgt):
            name = "ref_%04X" % tgt
            self.equates.setdefault(name, tgt)
            return self.note(src[0], name, tgt)
        return "$%02X" % tgt if zp else "$%04X" % tgt

    def spaces_for(self, cur_space, tgt):
        """Which spaces a reference to `tgt` might name.

        This has to come from the mapper. Hardcoding SuperGame's map -- f6 low,
        window in the middle, f7 high -- silently breaks every other layout: on
        an Absolute cartridge $4000-$7FFF is the *window*, not a fixed bank, so
        every lookup asked for a space that does not exist, found no label, and
        fell back to printing a bare address. The listing still assembled, which
        is why it went unnoticed; it was just unreadable. F-18 Hornet had 1,110
        bare operands and five labelled ones.

        A window is ambiguous by nature -- the same address is a different byte
        in every bank -- so from inside a window space, prefer that space, and
        otherwise offer them all and let the caller take the first that has a
        label.
        """
        c = self.cart
        for start, end, kind, arg in c._region:
            if not (start <= tgt < end):
                continue
            if kind == "fixed":
                return [c._fixed_name(arg)]
            if kind == "window":
                if cur_space.startswith("b"):
                    return [cur_space]
                return [x for x in c.spaces() if x.startswith("b")]
            return []                      # on-cart RAM: never a code label
        return []

    def _file_start(self, space):
        """Where this space begins in the image file, header included."""
        c = self.cart
        head = 128 if c.header_bytes else 0
        return head + c._offset(space, c.base_of(space))

    def emit_space(self, space, out):
        cart, an, cfg = self.cart, self.an, self.cfg
        base = cart.base_of(space)
        bank = cart.bank_of(space)
        w = []
        lo, n = cart.base_of(space), cart.size_of(space)
        role = ("swapped into the $%04X-$%04X window" % (lo, lo + n - 1)
                if space.startswith("b")
                else "fixed at $%04X-$%04X" % (lo, lo + n - 1))
        ncode = sum(1 for (s, a) in an.code if s == space)
        w.append("; " + "=" * 76)
        # take the name from the cartridge's own a78 header, so a PAL image is
        # not labelled as the NTSC one; fall back to the file name
        title = (cart.info or {}).get("title") or             os.path.splitext(os.path.basename(cart.path))[0]
        w.append("; %s -- ROM bank %d" % (title, bank))
        w.append(";   space '%s': %s" % (space, role))
        w.append(";   file offset $%05X-$%05X (a78 header included)"
                 % (self._file_start(space), self._file_start(space)
                    + cart.size_of(space) - 1))
        w.append(";   %d instructions reached by the tracer" % ncode)
        if space in cfg.notes:
            for line in cfg.notes[space].splitlines():
                w.append(";   " + line)
        w.append("; " + "=" * 76)
        w.append("")
        equ_at = len(w)                   # equates get spliced in here at the end
        w.append("    .org $%04X" % base)
        w.append("")

        blocks = {}
        for b in cfg.blocks:
            s, a = parse_loc(b["loc"])
            if s == space:
                blocks[a] = b

        addr = base
        end = base + cart.size_of(space)
        data_run = []

        def flush():
            if not data_run:
                return
            # A run can straddle the edge of a graphics block, so break it
            # where the kind changes and emit each piece in its own style.
            piece = []
            kind = None
            for a, b in data_run:
                k = (space, a) in self.gfx
                if kind is None:
                    kind = k
                if k != kind:
                    self._emit_run(w, space, piece[0][0],
                                   bytes(x[1] for x in piece), kind)
                    piece, kind = [], k
                piece.append((a, b))
            if piece:
                self._emit_run(w, space, piece[0][0],
                               bytes(x[1] for x in piece), kind)
            data_run.clear()

        while addr < end:
            loc = (space, addr)
            blk = blocks.get(addr)
            if blk:
                flush()
                addr = self.emit_block(w, space, addr, blk)
                continue
            if loc in an.code:
                flush()
                self.emit_insn(w, space, addr)
                addr += an.insn[loc][3]
            else:
                if loc in an.labels or loc in cfg.headers or len(data_run) >= 4096:
                    flush()
                data_run.append((addr, cart.byte(space, addr)))
                addr += 1
        flush()

        # Everything referenced by name but not *defined* in this file needs an
        # equate: hardware registers, RAM, labels in the other banks, and labels
        # that landed inside a data block (e.g. the high half of a word table,
        # which code addresses as its own base) so were never written out.
        defined = self.emitted[space]
        ext = {n: v for n, v in self.used[space].items() if n not in defined}
        equ = ["; ---- equates: hardware, RAM and cross-bank labels ----"]
        for n, v in sorted(ext.items(), key=lambda kv: (kv[1], kv[0])):
            equ.append("%-16s = $%04X" % (n, v))
        equ += ["", ""]
        w[equ_at:equ_at] = equ

        with open(out, "w", encoding="utf-8") as f:
            f.write("\n".join(w) + "\n")

    def emit_label(self, w, space, addr):
        loc = (space, addr)
        an, cfg = self.an, self.cfg
        key = fmt_loc(loc)
        if key in cfg.headers:
            w.append("")
            for line in cfg.headers[key].splitlines():
                w.append("; " + line)
        if loc in an.labels:
            refs = sorted(an.xrefs.get(loc, ()))
            if refs:
                names = ", ".join(
                    "%s" % (an.labels.get(r) or fmt_loc(r)) for r in refs[:8])
                if len(refs) > 8:
                    names += ", +%d more" % (len(refs) - 8)
                w.append("; xrefs: " + names)
            w.append("%s:" % an.labels[loc])
            self.emitted[space].add(an.labels[loc])

    def emit_insn(self, w, space, addr):
        an, cfg = self.an, self.cfg
        loc = (space, addr)
        self.emit_label(w, space, addr)
        mn, mode, operand, length = an.insn[loc]
        raw = self.cart.slice(space, addr, length)
        txt = self.opnd_text(loc, mn, mode, operand, length)
        illegal = m6502.OPCODES[raw[0]][2]
        # `LDX $00FF,Y` could reassemble as zero-page,Y; force the absolute form
        force = (mode in ZP_OF and operand is not None and operand < 0x100
                 and (mn, ZP_OF[mode]) in MODESET)
        line = "    %-9s %s" % (mn + ("*" if illegal else ".w" if force else ""), txt)
        comment = cfg.comments.get(fmt_loc(loc), "")
        if loc in an.bankswitch:
            b = an.bankswitch[loc]
            auto = "bank switch -> " + (
                "restores the shadowed bank" if b == "keep" else
                "bank %s (%s)" % ("/".join(map(str, b)),
                                  "varies by caller"
                                  if len(self.an.bankseen.get(loc, ())) > 1
                                  else "from a table")
                if isinstance(b, list) else
                "bank %d" % b if b is not None else
                "UNRESOLVED (value not a constant)")
            comment = (comment + "  " if comment else "") + auto
        if self.cycles:
            n = m6502.CYCLES[raw[0]]
            more = m6502.extra_cycles(mn, mode)
            cyc = "%d%s" % (n, "+" if more else " ")
            comment = ("%s  %s" % (cyc, comment)).rstrip()
        w.append("%-44s ; %04X: %-8s %s"
                 % (line, addr, raw.hex(" ").upper(), comment))

    def _emit_run(self, w, space, start, byts, as_gfx):
        if as_gfx:
            self.emit_gfx(w, space, start, byts,
                          (space, start) in self.gfx_wide)
        else:
            self.emit_bytes(w, space, start, byts)

    def emit_gfx(self, w, space, start, byts, wide=False):
        """One byte per line with its bits drawn, DiStella-style.

        `wide` draws two bytes a line, which is what a 16-pixel-wide 7800
        sprite actually is -- the 2600 was 8 and DiStella only ever needed one.
        """
        step = 2 if wide else 1
        for i in range(0, len(byts), step):
            chunk = byts[i:i + step]
            bits = "".join("X" if (b >> k) & 1 else " "
                           for b in chunk for k in range(7, -1, -1))
            self.emit_label(w, space, start + i)
            w.append("    .byte %-24s ; |%s| %04X"
                     % (",".join("$%02X" % b for b in chunk), bits, start + i))

    def emit_bytes(self, w, space, start, byts):
        self.emit_label(w, space, start)
        for i in range(0, len(byts), 16):
            chunk = byts[i:i + 16]
            hexs = ",".join("$%02X" % b for b in chunk)
            ascii_ = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
            w.append("    .byte %-64s ; %04X  %s" % (hexs, start + i, ascii_))

    def emit_block(self, w, space, addr, blk):
        cart, an = self.cart, self.an
        end = parse_loc(blk["end"])[1] if "end" in blk else addr + blk.get("len", 1)
        if blk.get("name"):
            an.labels[(space, addr)] = blk["name"]
        self.emit_label(w, space, addr)
        typ = blk.get("type", "bytes")
        data = cart.slice(space, addr, end - addr)
        if typ == "words":
            for i in range(0, len(data) - 1, 2):
                v = data[i] | (data[i + 1] << 8)
                nm = self.ref_name((space, addr + i), v)
                w.append("    .word %-20s ; %04X: %02X %02X"
                         % (nm, addr + i, data[i], data[i + 1]))
        else:
            # a label can land inside a data block (something references the
            # middle of a table); break the block so the label still gets a
            # definition, otherwise the listing will not reassemble
            cuts = sorted(a for a in range(addr + 1, end)
                          if (space, a) in an.labels
                          or fmt_loc((space, a)) in self.cfg.headers)
            for lo, hi in zip([addr] + cuts, cuts + [end]):
                self._emit_run(w, space, lo, cart.slice(space, lo, hi - lo),
                               (space, lo) in self.gfx)
        return end


# ----------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("rom")
    ap.add_argument("-c", "--config", default=None)
    ap.add_argument("-o", "--outdir", default="src")
    ap.add_argument("--low", choices=["none", "ram", "bank6", "rom"],
                    help="what sits at $4000-$7FFF, when the header is wrong")
    ap.add_argument("--mapper", choices=["linear", "supergame", "absolute"],
                    help="override the mapper the header declares")
    ap.add_argument("--cycles", action="store_true",
                    help="note each instruction's cycle count")
    ap.add_argument("--check-gaps", action="store_true",
                    help="for every apparent JSR/JMP into a gap, say whether "
                         "it is a real instruction the tracer reached (so the "
                         "gap holds missed code) or just the opcode byte "
                         "occurring inside data. Implies --gaps.")
    ap.add_argument("--gaps", action="store_true",
                    help="report byte ranges reached as neither code nor a "
                         "declared data block -- the true unexplained set, "
                         "as opposed to just 'not code' (which includes "
                         "every table, tile sheet and audio stream you've "
                         "already annotated). Cheap to run repeatedly as "
                         "annotations.json grows; ranges are sorted largest "
                         "first so the next annotation to write is obvious.")
    ap.add_argument("--map", action="store_true",
                    help="write a coverage-<space>.png heatmap per bank into "
                         "the outdir: one pixel per byte, green for code, "
                         "blue for a declared data block, red for neither. "
                         "The same picture --gaps gives you as text, meant "
                         "for a glance rather than a read. Needs Pillow "
                         "(pip install pillow); every other feature of this "
                         "tool has no dependencies, so this import only "
                         "happens if you ask for --map.")
    args = ap.parse_args()

    try:
        cart = Cart(args.rom, mapper=args.mapper, low=args.low)
    except (cart_module.UnknownMapper, cart_module.UnknownSpace) as e:
        sys.stderr.write("%s\n" % e)
        return 2
    cfg = Config(args.config)
    an = Analyzer(cart, cfg)

    gfx, gfx_wide = set(), set()
    for b in cfg.blocks:
        s, a = parse_loc(b["loc"])
        e = parse_loc(b["end"])[1] if "end" in b else a + b.get("len", 1)
        for x in range(a, e):
            an.forced_data.add((s, x))
        # "gfx": true draws the block's bits beside it instead of listing
        # hex. "wide": true puts two bytes on a line, which is what a
        # 16-pixel 7800 sprite is; the 2600 only ever needed one.
        if b.get("gfx"):
            gfx.add((s, a))
            for x in range(a, e):
                gfx.add((s, x))
            if b.get("wide"):
                gfx_wide.add((s, a))

    # vectors live in whatever space owns $FFFA
    v = cart.vectors()
    nmi, res, irq = v["NMI"], v["RESET"], v["IRQ"]

    entries = []
    for name, v in (("RESET", res), ("NMI", nmi), ("IRQ", irq)):
        sp = cart.space_of(v, None)
        if sp:
            entries.append((sp, v, None))
            an.labels[(sp, v)] = name + "_" + ("%04X" % v)
    entries += [(s, a, None) for (s, a) in cfg.entries]

    try:
        an.run(entries)
    except cart_module.UnknownSpace as e:
        sys.stderr.write("%s\n" % e)
        return 2

    # Handlers reached only through a RAM vector are invisible to a plain
    # trace: nothing in ROM names them. Re-trace through each declared vector
    # until no new handler turns up. "ram_vectors" in the config is a list of
    # [lo, hi] pairs; MARIA's display-interrupt slot is the usual one, but it
    # is per-game and there is no way to guess it.
    dli = {}
    for lo_a, hi_a in cfg.ram_vectors:
      for _ in range(8):
        new = []
        for tgt, site in an.scan_ram_vectors(lo_a, hi_a):
            sp = cart.space_of(tgt, None)
            if sp and (sp, tgt) not in dli:
                dli[(sp, tgt)] = site
                new.append((sp, tgt, None))
        if not new:
            break
        for sp, tgt, _b in new:
            an.mark((sp, tgt), "sub")
        an.run(new)
    for loc in sorted(dli):
        an.labels.setdefault(loc, "VEC_%04X" % loc[1])

    an.name_all()
    for name, v in (("NMI", nmi), ("RESET", res), ("IRQ", irq)):
        sp = cart.space_of(v, None)
        if sp:
            an.labels[(sp, v)] = cfg.labels.get(fmt_loc((sp, v)), name + "_HANDLER")

    os.makedirs(args.outdir, exist_ok=True)
    em = Emitter(cart, an, cfg, cycles=args.cycles)
    em.gfx, em.gfx_wide = gfx, gfx_wide
    spaces = cart.spaces()
    for space in spaces:
        n = sum(1 for (s, a) in an.code if s == space)
        if space.startswith("b") and n == 0 and space not in cfg.notes:
            continue
        em.emit_space(space, os.path.join(args.outdir, "%s.asm" % space))

    # ---- report ----
    print("vectors: NMI=$%04X RESET=$%04X IRQ=$%04X" % (nmi, res, irq))
    print("\ncoverage (bytes reached as code, per 16K space):")
    tot = 0
    for space in spaces:
        cnt = sum(an.insn[(s, a)][3] for (s, a) in an.code if s == space)
        tot += cnt if space.startswith(("f",)) or cnt else 0
        if cnt:
            print("  %-4s bank %d  %6d/%d bytes  %5.1f%%   (%d instructions)"
                  % (space, cart.bank_of(space), cnt, cart.size_of(space),
                     100.0 * cnt / cart.size_of(space),
                     sum(1 for (s, a) in an.code if s == space)))
    if an.bankswitch:
        print("\nbank-switch sites:")
        for loc in sorted(an.bankswitch):
            b = an.bankswitch[loc]
            print("  %-10s -> %s" % (
                fmt_loc(loc),
                "restores shadow" if b == "keep" else
                "banks %s (table)" % "/".join(map(str, b)) if isinstance(b, list) else
                "bank %d" % b if b is not None else "UNRESOLVED"))
    print("\nmost-referenced RAM addresses:")
    for a_, refs in sorted(an.ramrefs.items(), key=lambda kv: -len(kv[1]))[:40]:
        nm = cfg.ram.get(a_) or a7800.HW.get(a_) or ""
        print("  $%04X  %3d refs  %s" % (a_, len(refs), nm))

    if args.check_gaps:
        args.gaps = True

    if args.gaps or args.map:
        status = coverage_status(an, cart, spaces)

    if args.gaps:
        print("\ngaps (neither code nor a declared data block):")
        total_gap = 0
        for space in spaces:
            base = cart.base_of(space)
            gap = sorted(a for a, s in status[space].items() if s == 0)
            if not gap:
                continue
            total_gap += len(gap)
            ranges = []
            start = prev = gap[0]
            for a_ in gap[1:]:
                if a_ != prev + 1:
                    ranges.append((start, prev))
                    start = a_
                prev = a_
            ranges.append((start, prev))
            ranges.sort(key=lambda r: r[0] - r[1])
            print("  %s: %d bytes in %d range%s" %
                  (space, len(gap), len(ranges), "" if len(ranges) == 1 else "s"))
            for lo, hi in ranges:
                print("    $%04X-$%04X  (%d bytes)" % (lo, hi, hi - lo + 1))
        if total_gap == 0:
            print("  none -- every byte is either code or a declared block")

    if args.check_gaps:
        check_gaps(an, cart, spaces, status)

    if args.map:
        try:
            from PIL import Image
        except ImportError:
            sys.stderr.write(
                "--map needs Pillow: pip install pillow\n"
                "(every other feature of this tool has no dependencies -- "
                "this is the one opt-in exception)\n")
            return 2
        print("\ncoverage maps (green=code, blue=data block, red=gap):")
        COLORS = {0: (200, 60, 60), 1: (70, 150, 90), 2: (70, 100, 200)}
        for space in spaces:
            size = cart.size_of(space)
            cols = 256 if size >= 32768 else 128 if size >= 8192 else 64
            rows = (size + cols - 1) // cols
            base = cart.base_of(space)
            img = Image.new("RGB", (cols, rows), COLORS[0])
            px = img.load()
            s = status[space]
            for i in range(size):
                st = s.get(base + i, 0)
                if st == 0:
                    continue
                px[i % cols, i // cols] = COLORS[st]
            scale = 4 if cols <= 128 else 3
            img = img.resize((cols * scale, rows * scale), Image.NEAREST)
            path = os.path.join(args.outdir, "coverage-%s.png" % space)
            img.save(path)
            print("  %s -> %s" % (space, path))


def check_gaps(an, cart, spaces, status):
    """Are the gaps really free of missed code?

    A gap is a byte range reached as neither code nor a declared block, so
    the worry is that it holds a routine the trace never entered. The
    obvious check -- scan the ROM for a JSR/JMP whose operand lands in a gap
    -- is misleading on its own, and reliably so: the $20/$4C/$6C byte
    values occur constantly inside data and in the middle of longer
    instructions, and in practice those coincidences are ALL you find. In
    two 16K titles checked with this, every apparent direct branch into a
    gap was a coincidence.

    Two things make the raw scan trustworthy:

    1. The tracer already knows every address that is the first byte of an
       instruction. A candidate whose opcode byte is not one of those is not
       an instruction at all, and can be dismissed.

    2. A direct JSR/JMP that the tracer DID reach was also followed, so its
       target is code by construction and cannot still be a gap. A real hit
       there means the annotations changed under the trace -- worth saying
       out loud, not worth expecting.

    The case that genuinely escapes the tracer is `JMP ($xxxx)`, which it
    cannot follow. For those the operand is the POINTER, not the target, so
    the pointer is dereferenced here; when it lives in RAM the target is not
    knowable statically at all, and those sites are listed separately
    because they are exactly what an annotations `ram_vectors` entry exists
    to resolve.
    """
    real, bogus, ramind = [], [], []
    for space in spaces:
        base, size = cart.base_of(space), cart.size_of(space)
        st = status[space]
        in_rom = lambda x: base <= x < base + size
        for a in range(base, base + size - 2):
            op = cart.byte(space, a)
            if op not in (0x20, 0x4C, 0x6C):
                continue
            operand = cart.byte(space, a + 1) | (cart.byte(space, a + 2) << 8)
            traced = (space, a) in an.code
            if op == 0x6C:
                if not in_rom(operand):
                    if traced:
                        ramind.append((space, a, operand))
                    continue
                if not in_rom(operand + 1):
                    continue
                target = (cart.byte(space, operand)
                          | (cart.byte(space, operand + 1) << 8))
                name = "JMP ($%04X) ->" % operand
            else:
                target = operand
                name = "JSR" if op == 0x20 else "JMP"
            if not in_rom(target) or st.get(target) != 0:
                continue
            (real if traced else bogus).append((space, a, target, name))

    print("\ngap entry points (apparent JSR/JMP into a gap):")
    if not (real or bogus or ramind):
        print("  none -- no byte pattern anywhere in the ROM even looks like "
              "a call into a gap")
        return 0
    if bogus:
        print("  %d coincidence%s -- the $20/$4C/$6C byte is not an instruction "
              "start, so it is data or a mid-instruction byte, not a call:"
              % (len(bogus), "" if len(bogus) == 1 else "s"))
        for space, a, t, nm in bogus[:12]:
            print("    %s:%04X  %-16s $%04X   (not traced as code)"
                  % (space, a, nm, t))
        if len(bogus) > 12:
            print("    ... and %d more" % (len(bogus) - 12))
    if real:
        print("  %d REAL call site%s -- a traced instruction branching into a "
              "gap. The gap holds code, or the annotations changed under the "
              "trace; either way investigate:"
              % (len(real), "" if len(real) == 1 else "s"))
        for space, a, t, nm in real:
            print("    %s:%04X  %-16s $%04X   <-- MISSED CODE" % (space, a, nm, t))
    elif bogus:
        print("  no real call site among them: every apparent branch into a "
              "gap is a byte coincidence, so no traced code enters any gap.")
    if ramind:
        print("  %d traced JMP ($xxxx) through a RAM pointer -- the target is "
              "not knowable statically, so no gap claim covers these. If a "
              "gap is suspected to be a handler, this is how it gets reached; "
              "resolve with a 'ram_vectors' entry or a live probe:"
              % len(ramind))
        for space, a, ptr in ramind[:8]:
            nm = an.ram.get(ptr) if hasattr(an, "ram") else None
            print("    %s:%04X  JMP ($%04X)%s" % (space, a, ptr,
                                                 "  " + nm if nm else ""))
        if len(ramind) > 8:
            print("    ... and %d more" % (len(ramind) - 8))

    # The case above cannot see code that has no reachable caller at all, and
    # the "coincidence" verdict is circular for it: nothing inside a wholly
    # unreached region is an instruction start, so every reference into one
    # gets dismissed no matter how real it is.
    #
    # One shape of that is worth testing directly, because a tracer can never
    # find it and it is unambiguous when present: a gap that a traced JMP
    # steps straight over. Falling into the bytes after a JMP is impossible,
    # so if a routine is reached at all it is reached from somewhere else --
    # and the tracer, arriving only via the JMP, never enters. In Pole
    # Position II this hid three routines and 929 bytes, the whole of the
    # car's physics, behind nine bytes that were three JSRs.
    skipped = []
    for space in spaces:
        base = cart.base_of(space)
        st = status[space]
        gap = sorted(a for a, s in st.items() if s == 0)
        if not gap:
            continue
        starts = [gap[0]] + [b for a_, b in zip(gap, gap[1:]) if b != a_ + 1]
        ends = dict()
        run_start = gap[0]
        for a_, b in zip(gap, gap[1:]):
            if b != a_ + 1:
                ends[run_start] = a_
                run_start = b
        ends[run_start] = gap[-1]
        for g in starts:
            j = g - 3
            if j < base or (space, j) not in an.code:
                continue
            if cart.byte(space, j) != 0x4C:
                continue
            tgt = cart.byte(space, j + 1) | (cart.byte(space, j + 2) << 8)
            if tgt > g:
                skipped.append((space, g, ends[g], j, tgt))
    if skipped:
        print("  %d gap%s stepped over by a traced JMP -- unreachable by "
              "fall-through, so the tracer cannot enter even if the bytes are "
              "live code with a caller elsewhere. Decode by hand and add an "
              "entry if they are instructions:"
              % (len(skipped), "" if len(skipped) == 1 else "s"))
        for space, g, e, j, tgt in skipped:
            print("    %s:%04X-%04X (%d bytes)  skipped by %s:%04X JMP $%04X"
                  % (space, g, e, e - g + 1, space, j, tgt))
    return len(real) + len(skipped)


def coverage_status(an, cart, spaces):
    """{space: {addr: 0|1|2}} -- 0 gap, 1 code, 2 declared data block.

    Shared by --gaps and --map so both agree on exactly what counts as
    covered: an instruction's full byte range (an.code, length from
    an.insn), plus every address any 'blocks' entry in annotations.json
    claims (an.forced_data). Anything else in the space's own address
    range is a gap -- reached as neither.
    """
    result = {}
    for space in spaces:
        base = cart.base_of(space)
        size = cart.size_of(space)
        s = {a: 0 for a in range(base, base + size)}
        for (sp, a) in an.code:
            if sp != space:
                continue
            length = an.insn[(sp, a)][3]
            for i in range(length):
                if a + i in s:
                    s[a + i] = 1
        for (sp, a) in an.forced_data:
            if sp == space and a in s and s[a] == 0:
                s[a] = 2
        result[space] = s
    return result


if __name__ == "__main__":
    main()
