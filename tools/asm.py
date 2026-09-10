#!/usr/bin/env python3
"""
A minimal 6502 assembler that understands exactly the dialect disasm.py emits.

Its only job is to prove the disassembly is faithful: assemble a generated
listing, compare the result byte-for-byte against the original bank, and fail
loudly on any difference.  That turns "here is a listing" into "here is a
listing that provably rebuilds the ROM".

Supported:
    NAME = $XXXX          equates (hardware regs, RAM, cross-bank labels)
    .org $XXXX
    label:
    .byte $XX,$XX,...
    .word sym,$XXXX,...
    .res N[,$XX]          N filler bytes (default $FF)
    MNEM / MNEM operand   all documented addressing modes
    MNEM.w operand        force absolute where a zero-page form also exists

Usage:
    python asm.py <file.asm> [-o out.bin]
"""
import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import m6502

# (mnemonic, mode) -> opcode, documented opcodes only
ENC = {}
for _op, (_mn, _md, _il) in m6502.OPCODES.items():
    if not _il:
        ENC.setdefault((_mn, _md), _op)

ZP_OF = {"abs": "zp", "abx": "zpx", "aby": "zpy"}


class AsmError(Exception):
    pass


def strip_comment(line):
    i = line.find(";")
    return (line if i < 0 else line[:i]).rstrip()


NUM = re.compile(r"^\$([0-9A-Fa-f]+)$|^(\d+)$")
SYM = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class Assembler:
    def __init__(self):
        self.sym = {}
        self.org = None

    def value(self, tok, pass2):
        """A number, a symbol, or a small expression over them.

        disasm.py emits neither expressions nor </> operators, so none of this
        is needed to round-trip a listing -- it is here because source written
        BY HAND needs `#<label`, `#>label` and `table+1` to be worth using at
        all, and an assembler that cannot take the low byte of an address
        cannot set a MARIA pointer.

        Deliberately not a full expression parser: `+` and `-` applied left to
        right, and a leading `<` or `>` for the low or high byte. No
        precedence, because there is no operator here that needs it.
        """
        tok = tok.strip()
        if tok[:1] in ("<", ">"):
            v = self.value(tok[1:], pass2)
            if v is None:
                return None
            return (v & 0xFF) if tok[0] == "<" else ((v >> 8) & 0xFF)
        parts = [t for t in re.split(r"([+-])", tok) if t.strip()]
        if len(parts) > 1:
            total = self.value(parts[0], pass2)
            if total is None:
                return None
            for i in range(1, len(parts) - 1, 2):
                rhs = self.value(parts[i + 1], pass2)
                if rhs is None:
                    return None
                total = total + rhs if parts[i] == "+" else total - rhs
            return total
        m = NUM.match(tok)
        if m:
            return int(m.group(1), 16) if m.group(1) else int(m.group(2))
        if tok in self.sym:
            return self.sym[tok]
        if pass2:
            raise AsmError("undefined symbol %r" % tok)
        return None                      # unknown yet -> assume absolute

    def parse_operand(self, mn, opnd, pc, pass2, force_abs):
        """Return (mode, value)."""
        if opnd == "" or opnd is None:
            return ("acc", None) if (mn, "acc") in ENC and (mn, "imp") not in ENC \
                else ("imp", None)
        o = opnd.strip()
        if o == "A":
            return "acc", None
        if o.startswith("#"):
            return "imm", self.value(o[1:], pass2)
        m = re.match(r"^\((.*),\s*X\)$", o)
        if m:
            return "izx", self.value(m.group(1), pass2)
        m = re.match(r"^\((.*)\)\s*,\s*Y$", o)
        if m:
            return "izy", self.value(m.group(1), pass2)
        m = re.match(r"^\((.*)\)$", o)
        if m:
            return "ind", self.value(m.group(1), pass2)
        m = re.match(r"^(.*),\s*([XY])$", o)
        idx = None
        if m:
            o, idx = m.group(1).strip(), m.group(2)
        v = self.value(o, pass2)
        if mn in m6502.BRANCHES:
            return "rel", v
        base = {"X": "abx", "Y": "aby", None: "abs"}[idx]
        if not force_abs and v is not None and v < 0x100:
            zp = ZP_OF[base]
            if (mn, zp) in ENC:
                return zp, v
        return base, v

    def size(self, mode):
        return 1 + m6502.MODES[mode]

    def assemble(self, lines):
        # ---- pass 1: addresses and labels ----
        pc = None
        layout = []
        for ln, raw in enumerate(lines, 1):
            line = strip_comment(raw)
            if not line.strip():
                continue
            m = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.+?)\s*$", line)
            if m:
                self.sym[m.group(1)] = self.value(m.group(2), True)
                continue
            m = re.match(r"^\s*\.org\s+(\S+)\s*$", line, re.I)
            if m:
                pc = self.value(m.group(1), True)
                self.org = pc if self.org is None else self.org
                continue
            m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*):\s*$", line)
            if m:
                if pc is None:
                    raise AsmError("line %d: label before .org" % ln)
                self.sym[m.group(1)] = pc
                continue
            m = re.match(r"^\s*\.res\s+(.*)$", line, re.I)
            if m:
                # Reserve/fill. disasm.py never emits this -- it exists because
                # MARIA's direct mode needs a sprite's scanlines on consecutive
                # PAGES, so authored source has to step the pc across hundreds
                # of bytes it does not care about. Without it that padding has
                # to be written out as .byte lines, and a second .org is not an
                # option: pass 2 requires contiguous output and reports drift.
                parts = [t for t in m.group(1).split(",") if t.strip()]
                if not parts or len(parts) > 2:
                    raise AsmError("line %d: .res wants a count and an "
                                   "optional fill byte" % ln)
                n = self.value(parts[0], True)
                fill = self.value(parts[1], True) if len(parts) == 2 else 0xFF
                if n < 0:
                    raise AsmError("line %d: .res count is negative" % ln)
                layout.append((ln, pc, "res", fill, n))
                pc += n
                continue
            m = re.match(r"^\s*\.(byte|word)\s+(.*)$", line, re.I)
            if m:
                items = [t for t in m.group(2).split(",") if t.strip()]
                n = len(items) * (1 if m.group(1).lower() == "byte" else 2)
                layout.append((ln, pc, "data", m.group(1).lower(), items))
                pc += n
                continue
            m = re.match(r"^\s*([A-Za-z]{3})(\.w)?\s*(.*)$", line)
            if not m:
                raise AsmError("line %d: cannot parse %r" % (ln, line))
            mn, force, opnd = m.group(1).upper(), bool(m.group(2)), m.group(3)
            mode, _ = self.parse_operand(mn, opnd, pc, False, force)
            if (mn, mode) not in ENC:
                raise AsmError("line %d: no opcode for %s %s" % (ln, mn, mode))
            layout.append((ln, pc, "insn", (mn, force), opnd))
            pc += self.size(mode)

        # ---- pass 2: emit ----
        out = bytearray()
        for ln, at, kind, a, b in layout:
            if len(out) != at - self.org:
                raise AsmError("line %d: pc drift (expected $%04X, at $%04X)"
                               % (ln, at, self.org + len(out)))
            if kind == "res":
                out += bytes([a & 0xFF]) * b
            elif kind == "data":
                for t in b:
                    v = self.value(t, True)
                    if a == "byte":
                        out.append(v & 0xFF)
                    else:
                        out += bytes((v & 0xFF, (v >> 8) & 0xFF))
            else:
                mn, force = a
                mode, v = self.parse_operand(mn, b, at, True, force)
                out.append(ENC[(mn, mode)])
                if mode == "rel":
                    d = v - (at + 2)
                    if not -128 <= d <= 127:
                        raise AsmError("line %d: branch out of range (%d)" % (ln, d))
                    out.append(d & 0xFF)
                elif m6502.MODES[mode] == 1:
                    out.append(v & 0xFF)
                elif m6502.MODES[mode] == 2:
                    out += bytes((v & 0xFF, (v >> 8) & 0xFF))
        return bytes(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("-o", "--out")
    args = ap.parse_args()
    lines = open(args.src, encoding="utf-8").read().splitlines()
    data = Assembler().assemble(lines)
    if args.out:
        open(args.out, "wb").write(data)
    print("%s -> %d bytes" % (args.src, len(data)))


if __name__ == "__main__":
    main()
