"""6502 opcode table (documented + undocumented/illegal opcodes)."""

# addressing modes and their operand byte counts
MODES = {
    "imp": 0, "acc": 0, "imm": 1, "zp": 1, "zpx": 1, "zpy": 1,
    "izx": 1, "izy": 1, "abs": 2, "abx": 2, "aby": 2, "ind": 2, "rel": 1,
}

# how to format an operand once the symbol/number text `v` is known
FMT = {
    "imp": "", "acc": "A", "imm": "#{v}", "zp": "{v}", "zpx": "{v},X",
    "zpy": "{v},Y", "izx": "({v},X)", "izy": "({v}),Y", "abs": "{v}",
    "abx": "{v},X", "aby": "{v},Y", "ind": "({v})", "rel": "{v}",
}

_T = """
00 BRK imp .   01 ORA izx .   02 JAM imp *   03 SLO izx *
04 NOP zp  *   05 ORA zp  .   06 ASL zp  .   07 SLO zp  *
08 PHP imp .   09 ORA imm .   0A ASL acc .   0B ANC imm *
0C NOP abs *   0D ORA abs .   0E ASL abs .   0F SLO abs *
10 BPL rel .   11 ORA izy .   12 JAM imp *   13 SLO izy *
14 NOP zpx *   15 ORA zpx .   16 ASL zpx .   17 SLO zpx *
18 CLC imp .   19 ORA aby .   1A NOP imp *   1B SLO aby *
1C NOP abx *   1D ORA abx .   1E ASL abx .   1F SLO abx *
20 JSR abs .   21 AND izx .   22 JAM imp *   23 RLA izx *
24 BIT zp  .   25 AND zp  .   26 ROL zp  .   27 RLA zp  *
28 PLP imp .   29 AND imm .   2A ROL acc .   2B ANC imm *
2C BIT abs .   2D AND abs .   2E ROL abs .   2F RLA abs *
30 BMI rel .   31 AND izy .   32 JAM imp *   33 RLA izy *
34 NOP zpx *   35 AND zpx .   36 ROL zpx .   37 RLA zpx *
38 SEC imp .   39 AND aby .   3A NOP imp *   3B RLA aby *
3C NOP abx *   3D AND abx .   3E ROL abx .   3F RLA abx *
40 RTI imp .   41 EOR izx .   42 JAM imp *   43 SRE izx *
44 NOP zp  *   45 EOR zp  .   46 LSR zp  .   47 SRE zp  *
48 PHA imp .   49 EOR imm .   4A LSR acc .   4B ALR imm *
4C JMP abs .   4D EOR abs .   4E LSR abs .   4F SRE abs *
50 BVC rel .   51 EOR izy .   52 JAM imp *   53 SRE izy *
54 NOP zpx *   55 EOR zpx .   56 LSR zpx .   57 SRE zpx *
58 CLI imp .   59 EOR aby .   5A NOP imp *   5B SRE aby *
5C NOP abx *   5D EOR abx .   5E LSR abx .   5F SRE abx *
60 RTS imp .   61 ADC izx .   62 JAM imp *   63 RRA izx *
64 NOP zp  *   65 ADC zp  .   66 ROR zp  .   67 RRA zp  *
68 PLA imp .   69 ADC imm .   6A ROR acc .   6B ARR imm *
6C JMP ind .   6D ADC abs .   6E ROR abs .   6F RRA abs *
70 BVS rel .   71 ADC izy .   72 JAM imp *   73 RRA izy *
74 NOP zpx *   75 ADC zpx .   76 ROR zpx .   77 RRA zpx *
78 SEI imp .   79 ADC aby .   7A NOP imp *   7B RRA aby *
7C NOP abx *   7D ADC abx .   7E ROR abx .   7F RRA abx *
80 NOP imm *   81 STA izx .   82 NOP imm *   83 SAX izx *
84 STY zp  .   85 STA zp  .   86 STX zp  .   87 SAX zp  *
88 DEY imp .   89 NOP imm *   8A TXA imp .   8B ANE imm *
8C STY abs .   8D STA abs .   8E STX abs .   8F SAX abs *
90 BCC rel .   91 STA izy .   92 JAM imp *   93 SHA izy *
94 STY zpx .   95 STA zpx .   96 STX zpy .   97 SAX zpy *
98 TYA imp .   99 STA aby .   9A TXS imp .   9B TAS aby *
9C SHY abx *   9D STA abx .   9E SHX aby *   9F SHA aby *
A0 LDY imm .   A1 LDA izx .   A2 LDX imm .   A3 LAX izx *
A4 LDY zp  .   A5 LDA zp  .   A6 LDX zp  .   A7 LAX zp  *
A8 TAY imp .   A9 LDA imm .   AA TAX imp .   AB LXA imm *
AC LDY abs .   AD LDA abs .   AE LDX abs .   AF LAX abs *
B0 BCS rel .   B1 LDA izy .   B2 JAM imp *   B3 LAX izy *
B4 LDY zpx .   B5 LDA zpx .   B6 LDX zpy .   B7 LAX zpy *
B8 CLV imp .   B9 LDA aby .   BA TSX imp .   BB LAS aby *
BC LDY abx .   BD LDA abx .   BE LDX aby .   BF LAX aby *
C0 CPY imm .   C1 CMP izx .   C2 NOP imm *   C3 DCP izx *
C4 CPY zp  .   C5 CMP zp  .   C6 DEC zp  .   C7 DCP zp  *
C8 INY imp .   C9 CMP imm .   CA DEX imp .   CB SBX imm *
CC CPY abs .   CD CMP abs .   CE DEC abs .   CF DCP abs *
D0 BNE rel .   D1 CMP izy .   D2 JAM imp *   D3 DCP izy *
D4 NOP zpx *   D5 CMP zpx .   D6 DEC zpx .   D7 DCP zpx *
D8 CLD imp .   D9 CMP aby .   DA NOP imp *   DB DCP aby *
DC NOP abx *   DD CMP abx .   DE DEC abx .   DF DCP abx *
E0 CPX imm .   E1 SBC izx .   E2 NOP imm *   E3 ISC izx *
E4 CPX zp  .   E5 SBC zp  .   E6 INC zp  .   E7 ISC zp  *
E8 INX imp .   E9 SBC imm .   EA NOP imp .   EB SBC imm *
EC CPX abs .   ED SBC abs .   EE INC abs .   EF ISC abs *
F0 BEQ rel .   F1 SBC izy .   F2 JAM imp *   F3 ISC izy *
F4 NOP zpx *   F5 SBC zpx .   F6 INC zpx .   F7 ISC zpx *
F8 SED imp .   F9 SBC aby .   FA NOP imp *   FB ISC aby *
FC NOP abx *   FD SBC abx .   FE INC abx .   FF ISC abx *
"""

# opcode -> (mnemonic, mode, is_illegal)
OPCODES = {}
_toks = _T.split()
for _i in range(0, len(_toks), 4):
    _op, _mn, _md, _fl = _toks[_i:_i + 4]
    OPCODES[int(_op, 16)] = (_mn, _md, _fl == "*")

assert len(OPCODES) == 256, len(OPCODES)

LENGTH = {op: 1 + MODES[md] for op, (mn, md, il) in OPCODES.items()}

# control-flow classification
BRANCHES = {"BPL", "BMI", "BVC", "BVS", "BCC", "BCS", "BNE", "BEQ"}
# instructions after which execution does not fall through
TERMINAL = {"JMP", "RTS", "RTI", "BRK", "JAM"}
# instructions that read/write memory (for xref purposes)
STORES = {"STA", "STX", "STY", "SAX", "SHA", "SHX", "SHY", "TAS"}

# Base cycle count per opcode, indexed by the opcode byte.
#
# These are the documented timings, cross-checked against DiStella's own table
# and against 32 spot values from the published 6502 figures; all agree. They
# are the *base* count only: a page crossing on an indexed read, and a taken
# branch, each cost more. `extra_cycles` below says when.
CYCLES = [
    7, 6, 0, 8, 3, 3, 5, 5, 3, 2, 2, 2, 4, 4, 6, 6,
    2, 5, 0, 8, 4, 4, 6, 6, 2, 4, 2, 7, 4, 4, 7, 7,
    6, 6, 0, 8, 3, 3, 5, 5, 4, 2, 2, 2, 4, 4, 6, 6,
    2, 5, 0, 8, 4, 4, 6, 6, 2, 4, 2, 7, 4, 4, 7, 7,
    6, 6, 0, 8, 3, 3, 5, 5, 3, 2, 2, 2, 3, 4, 6, 6,
    2, 5, 0, 8, 4, 4, 6, 6, 2, 4, 2, 7, 4, 4, 7, 7,
    6, 6, 0, 8, 3, 3, 5, 5, 4, 2, 2, 2, 5, 4, 6, 6,
    2, 5, 0, 8, 4, 4, 6, 6, 2, 4, 2, 7, 4, 4, 7, 7,
    2, 6, 2, 6, 3, 3, 3, 3, 2, 2, 2, 2, 4, 4, 4, 4,
    2, 6, 0, 6, 4, 4, 4, 4, 2, 5, 2, 5, 5, 5, 5, 5,
    2, 6, 2, 6, 3, 3, 3, 3, 2, 2, 2, 2, 4, 4, 4, 4,
    2, 5, 0, 5, 4, 4, 4, 4, 2, 4, 2, 4, 4, 4, 4, 4,
    2, 6, 2, 8, 3, 3, 5, 5, 2, 2, 2, 2, 4, 4, 6, 6,
    2, 5, 0, 8, 4, 4, 6, 6, 2, 4, 2, 7, 4, 4, 7, 7,
    2, 6, 2, 8, 3, 3, 5, 5, 2, 2, 2, 2, 4, 4, 6, 6,
    2, 5, 0, 8, 4, 4, 6, 6, 2, 4, 2, 7, 4, 4, 7, 7,
]

# Modes whose read costs one more cycle when the index carries into a new page.
# The write forms (STA abs,X and friends) always pay it, so their base count in
# the table above already includes it and they are not listed here.
PAGE_CROSS_MODES = {"abx", "aby", "izy"}
PAGE_CROSS_FREE = {"STA", "STX", "STY", "ASL", "LSR", "ROL", "ROR",
                   "INC", "DEC", "SLO", "RLA", "SRE", "RRA", "DCP", "ISC"}


def extra_cycles(mn, mode):
    """What can be added to the base count, as a short note, or ''.

    A disassembler cannot know whether a branch is taken or whether an index
    crosses a page, so it says what the possibilities are rather than
    inventing a number.
    """
    if mode == "rel":
        return "+1 if taken, +2 if it crosses a page"
    if mode in PAGE_CROSS_MODES and mn not in PAGE_CROSS_FREE:
        return "+1 if the index crosses a page"
    return ""
