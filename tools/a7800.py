"""Atari 7800 hardware symbols and memory-map helpers."""

# --- TIA (7800 only uses a handful of the 2600 TIA registers) -----------------
TIA = {
    0x01: "INPTCTRL",   # write: bit0 2600/7800 mode, bit1 BIOS/cart map, bit2 MARIA en
    0x08: "INPT0", 0x09: "INPT1", 0x0A: "INPT2", 0x0B: "INPT3",
    0x0C: "INPT4", 0x0D: "INPT5",
    0x15: "AUDC0", 0x16: "AUDC1",
    0x17: "AUDF0", 0x18: "AUDF1",
    0x19: "AUDV0", 0x1A: "AUDV1",
}

# --- MARIA -------------------------------------------------------------------
MARIA = {
    0x20: "BACKGRND", 0x21: "P0C1", 0x22: "P0C2", 0x23: "P0C3",
    0x24: "WSYNC",    0x25: "P1C1", 0x26: "P1C2", 0x27: "P1C3",
    0x28: "MSTAT",    0x29: "P2C1", 0x2A: "P2C2", 0x2B: "P2C3",
    0x2C: "DPPH",     0x2D: "P3C1", 0x2E: "P3C2", 0x2F: "P3C3",
    0x30: "DPPL",     0x31: "P4C1", 0x32: "P4C2", 0x33: "P4C3",
    0x34: "CHARBASE", 0x35: "P5C1", 0x36: "P5C2", 0x37: "P5C3",
    0x38: "OFFSET",   0x39: "P6C1", 0x3A: "P6C2", 0x3B: "P6C3",
    0x3C: "CTRL",     0x3D: "P7C1", 0x3E: "P7C2", 0x3F: "P7C3",
}

# --- RIOT (6532) -------------------------------------------------------------
RIOT = {
    0x0280: "SWCHA",  0x0281: "SWACNT", 0x0282: "SWCHB", 0x0283: "SWBCNT",
    0x0284: "INTIM",  0x0285: "INTFLG",
    0x0294: "TIM1T",  0x0295: "TIM8T",  0x0296: "TIM64T", 0x0297: "T1024T",
}

HW = {}
HW.update(TIA)
HW.update(MARIA)
HW.update(RIOT)
# TIA/MARIA are mirrored at $0100 and $0200
for _base in (0x0100, 0x0200):
    for _a, _n in list(TIA.items()) + list(MARIA.items()):
        HW[_base + _a] = _n + "_m%d" % (_base >> 8)

MARIA_CTRL_BITS = """CTRL: b7 ColorKill  b6-5 DMA(10=on, 11=off)
      b4 CharWidth(0=1 byte/char, 1=2 bytes/char)  b3 Border(0=bg,1=black)
      b2 Kangaroo  b1-0 ReadMode(00=160x2/4, 10=320A/320D, 11=320B/320C)"""
# b4 was recorded here the other way round ("1=1byte") until it was measured:
# setting it makes character mode cost one MORE fetch per character, not one
# fewer. See docs/hardware.md, "What MARIA costs". Both shipping games in
# this series agree -- they clear b4 and store one byte per character line.
# b6-5: only 10 and 11 were ever observed in shipping code, so 00 and 01 are
# left unlabelled rather than guessed.


def region_of(addr):
    """Coarse classification of a 7800 CPU address."""
    if addr <= 0x001F:
        return "TIA"
    if addr <= 0x003F:
        return "MARIA"
    if addr <= 0x00FF:
        return "RAM_ZP"          # mirror of $2040-$20FF
    if addr <= 0x011F:
        return "TIA"
    if addr <= 0x013F:
        return "MARIA"
    if addr <= 0x01FF:
        return "RAM_STACK"       # mirror of $2140-$21FF
    if addr <= 0x021F:
        return "TIA"
    if addr <= 0x023F:
        return "MARIA"
    if 0x0280 <= addr <= 0x02FF:
        return "RIOT_IO"
    if 0x0480 <= addr <= 0x04FF:
        return "RIOT_RAM"
    if 0x1800 <= addr <= 0x27FF:
        return "RAM"
    if 0x2800 <= addr <= 0x3FFF:
        return "RAM_MIRROR"
    if addr >= 0x4000:
        return "CART"
    return "UNMAPPED"


def sym(addr, width=None):
    """Return a symbolic name for a hardware address, or None."""
    return HW.get(addr)


# --- POKEY ------------------------------------------------------------------
# An optional sound chip on the cartridge, not part of the console. Sixteen
# registers, and READING one is a different register from WRITING it -- $0A is
# SKRES on write and RANDOM on read. That is not a curiosity: Ballblazer reads
# $400A a couple of hundred times a second for random numbers while writing
# nothing to it, which is exactly how you recognise POKEY usage in a listing.
POKEY_WRITE = {
    0x0: "AUDF1", 0x1: "AUDC1", 0x2: "AUDF2", 0x3: "AUDC2",
    0x4: "AUDF3", 0x5: "AUDC3", 0x6: "AUDF4", 0x7: "AUDC4",
    0x8: "AUDCTL", 0x9: "STIMER", 0xA: "SKRES", 0xB: "POTGO",
    0xC: "POKEY_0C", 0xD: "SEROUT", 0xE: "IRQEN", 0xF: "SKCTL",
}
POKEY_READ = {
    0x0: "POT0", 0x1: "POT1", 0x2: "POT2", 0x3: "POT3",
    0x4: "POT4", 0x5: "POT5", 0x6: "POT6", 0x7: "POT7",
    0x8: "ALLPOT", 0x9: "KBCODE", 0xA: "RANDOM", 0xB: "POKEY_0B",
    0xC: "POKEY_0C", 0xD: "SERIN", 0xE: "IRQST", 0xF: "SKSTAT",
}

# Where a cartridge can put it. The a78 header says which, and the choice
# matters to the address map only in the $4000 case -- see pokey_base().
POKEY_BASES = {0x0001: 0x4000, 0x0040: 0x0450, 0x8000: 0x0800}


def pokey_base(cart_type):
    """The address a cartridge's POKEY answers at, or None."""
    for bit, base in POKEY_BASES.items():
        if cart_type & bit:
            return base
    return None


def pokey_syms(base, write=None):
    """POKEY register names at `base`.

    `write` picks which side of the chip you mean: True for the write map,
    False for the read map, None for a combined name like "AUDF1/POT0" where
    the direction is not known.
    """
    out = {}
    for i in range(16):
        w, r = POKEY_WRITE[i], POKEY_READ[i]
        if write is True:
            out[base + i] = w
        elif write is False:
            out[base + i] = r
        else:
            out[base + i] = w if w == r else "%s/%s" % (w, r)
    return out


def sym_for(addr, cart_type=0, write=None):
    """A name for a hardware address, POKEY included when the cart has one."""
    base = pokey_base(cart_type)
    if base is not None and base <= addr < base + 16:
        return pokey_syms(base, write)[addr]
    return HW.get(addr)
