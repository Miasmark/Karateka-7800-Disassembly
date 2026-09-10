#!/usr/bin/env python3
"""
Atari 7800 cartridge images: the .a78 header, and the mappers it describes.

A 7800 cartridge is a window onto CPU address space, and which window depends on
the mapper. This module reads the header, picks a mapper, and gives the rest of
the toolkit one interface regardless:

    cart = Cart("game.a78")
    cart.spaces()                  -> ["f6", "b0", ..., "b7", "f7"]
    cart.space_of(0x8123, bank=3)  -> "b3"
    cart.byte("f6", 0x4000)        -> int
    cart.vectors()                 -> {"NMI": .., "RESET": .., "IRQ": ..}

A *space* is a name for one fixed view of ROM: "f6" and "f7" for regions that
never change, "b0".."b7" for the banks that take turns at a windowed region.
Addresses inside a space are CPU addresses, not file offsets, so a listing can
be read against a debugger without arithmetic.

Headerless dumps are accepted; the mapper is then guessed from the size, which
is right for linear ROMs and right for SuperGame only if the reset vector lands
in the last bank. Pass mapper= to override when the guess is wrong.
"""
import os

BANK = 0x4000


# ----------------------------------------------------------------- the header
# Bit meanings, checked against a 1,309-image retail-and-homebrew library by
# looking at cartridges whose mapper is independently known. The anchors:
#
#   $0001  Ballblazer, Commando            POKEY at $4000
#   $0002  Midnight Mutants, Xenophobe     SuperGame
#   $0004  Impossible Mission              on-cart RAM at $4000
#   $0008  Crossbow, Alien Brigade (144K)  an extra ROM bank at $4000
#   $0010  Midnight Mutants, Xenophobe     bank 6 at $4000
#   $0040  files that name POKEY 450       POKEY at $0450
#   $0400  nine images, all "POKEY 440"    a second POKEY at $0440
#   $0100  Double Dragon, Rampage          Activision banking
#   $0200  F-18 Hornet                     Absolute banking
#   $0800  45 images, every one a YM demo  YM2151 audio, not a mapper
#   $2000  the Bankset test series         Bankset
#   $8000  files that name POKEY 800       POKEY at $0800
#
# Published bit lists disagree with this above bit 6 -- several put Activision
# at $0200 and Absolute at $0400. The cartridges say otherwise: F-18 Hornet is
# an Absolute-mapper game and its header reads $0200. Trust the images.
#
# Since confirmed against the canonical source: `a78header.asm` in
# 7800-devtools/a78_asm_header, the dasm include homebrew authors actually
# build their headers with. It agrees with the library on **every bit**,
# including the two the published lists get wrong, and it names the three that
# no image in the library exercises:
#
#   $0020  EXRAM/X2   hotspot banked RAM
#   $0080  EXRAM/A8   mirror RAM
#   $4000  EXRAM/M2   halt banked RAM
#
# Those three had been guesses here. Note they are RAM variants rather than
# mappers, which is why nothing broke while they were wrong.
CART_FLAGS = [
    (0x0001, "POKEY @ $4000-$7FFF"),
    (0x0002, "SuperGame bank switched"),
    (0x0004, "EXRAM: RAM @ $4000"),
    (0x0008, "EXROM: ROM @ $4000"),
    (0x0010, "EXFIX: 2nd-last bank @ $4000"),
    (0x0020, "EXRAM/X2: hotspot banked RAM"),
    (0x0040, "POKEY @ $0450"),
    (0x0080, "EXRAM/A8: mirror RAM"),
    (0x0100, "Activision banking"),
    (0x0200, "Absolute banking"),
    (0x0400, "POKEY @ $0440 (second)"),
    (0x0800, "YM2151 @ $0460"),
    (0x1000, "SOUPER"),
    (0x2000, "Bankset"),
    (0x4000, "EXRAM/M2: halt banked RAM"),
    (0x8000, "POKEY @ $0800"),
]
CONTROLLER = {0: "none", 1: "joystick", 2: "lightgun", 3: "paddle",
              4: "trakball", 5: "2-button joystick", 6: "keypad",
              7: "ST mouse", 8: "Amiga mouse", 9: "AtariVox", 10: "SNES pad"}


def read_header(raw):
    """Parse a 128-byte .a78 header, or return None if there isn't one."""
    if len(raw) < 128 or raw[1:10] != b"ATARI7800":
        return None
    h = raw[:128]
    ctype = int.from_bytes(h[53:55], "big")
    return {
        "version": h[0],
        "title": h[17:49].split(b"\0")[0].decode("latin1").strip(),
        "rom_size": int.from_bytes(h[49:53], "big"),
        "cart_type": ctype,
        "flags": [name for bit, name in CART_FLAGS if ctype & bit],
        "controller1": CONTROLLER.get(h[55], "?"),
        "controller2": CONTROLLER.get(h[56], "?"),
        "region": "PAL" if h[57] & 1 else "NTSC",
        "save_hsc": bool(h[58] & 1),
        "save_savekey": bool(h[58] & 2),
        "footer": h[100:128].rstrip(b"\0").decode("latin1"),
    }


# ----------------------------------------------------------------- the mappers
# Each mapper is a list of regions covering $4000-$FFFF. A region is
#   (start, end_exclusive, kind, arg)
# kind "fixed"   -> arg is the bank index, always visible
#      "window"  -> the bank on show is chosen at run time
#      "ram"     -> on-cart RAM, not ROM
# Regions are what give spaces their names, so this table *is* the address map.

class UnknownMapper(Exception):
    """The image is banked in a way this module cannot lay out."""


class UnknownSpace(KeyError):
    """A space was named that this cartridge's layout does not contain.

    KeyError renders its message through repr(), which turns a helpful
    multi-line explanation into one line full of backslash-n. Callers that
    catch KeyError still work; the text just reads properly.
    """

    def __str__(self):
        return self.args[0] if self.args else ""


class Mapper(object):
    name = "?"
    switch = None            # where a write selects a bank, as (lo, hi) or None
    note = ""
    window_banks = None      # banks the window can show; None means all of them

    def __init__(self, nbanks):
        self.nbanks = nbanks

    @property
    def nwindow(self):
        return self.nbanks if self.window_banks is None else self.window_banks

    def regions(self):
        raise NotImplementedError

    def bank_from_write(self, addr, value):
        """The bank a write selects, or None if this write is not a switch."""
        if self.switch is None:
            return None
        lo, hi = self.switch
        if lo <= addr <= hi:
            return value % self.nwindow
        return None


class Linear(Mapper):
    """No banking: the ROM sits at the top of the address space.

    4K, 8K, 16K, 32K and 48K images all work this way; the image simply ends at
    $FFFF and starts wherever its size puts it. 48K reaches down to $4000 and is
    the largest a 7800 can address without a mapper.
    """
    name = "linear"
    # 48K reaches $4000. A handful of homebrew images are 52K, which is exactly
    # $3000-$FFFF -- four more kilobytes, overlaying what is normally a RAM
    # mirror. The size arithmetic is exact and `start` falls out of it, but no
    # image in the library boots far enough under emulation to confirm the
    # layout by reading memory back, so treat 52K as inferred rather than
    # established. `describe()` says so.
    MAX = 52 * 1024

    def __init__(self, size):
        if size > self.MAX:
            raise UnknownMapper(
                "%dK cannot be linear: without a mapper the 7800 can only see "
                "$4000-$FFFF, which is 48K. The header does not say how this "
                "image is banked -- pass mapper= if you know." % (size // 1024))
        Mapper.__init__(self, 1)
        self.size = size
        self.start = 0x10000 - size
        if size > 48 * 1024:
            self.note = ("52K: $3000-$FFFF, inferred from the size and not "
                         "yet confirmed against a running machine")

    def regions(self):
        return [(self.start, 0x10000, "fixed", 0)]


class SuperGame(Mapper):
    """The common 128K mapper: 16K banks, one window, the last bank fixed.

    $8000-$BFFF shows any bank; a write anywhere in $8000-$FFFF sets which. The
    write is data-driven, so the disassembler has to track the value in the
    accumulator to know where a JSR into the window lands -- that is the whole
    reason the tracer carries abstract register values.

    Three things can sit at $4000-$7FFF, and the header says which: nothing,
    on-cart RAM (flag $0004), or the second-to-last bank (flag $0010). Getting
    this wrong is quiet: the code still disassembles, it just disassembles the
    wrong bank.

    The last bank is always the one at $C000. That is not an assumption: across
    207 SuperGame images in which the reset vector could be matched to real
    startup code, 199 put the last bank there and the eight exceptions look like
    misfires of the heuristic rather than a different layout.
    """
    name = "supergame"
    switch = (0x8000, 0xFFFF)

    def __init__(self, nbanks, low="none"):
        Mapper.__init__(self, nbanks)
        self.low = low                       # "none" | "ram" | "bank6" | "rom"
        # "rom" is the 144K arrangement (flag $0008): nine banks, the ninth at
        # $C000, and a fixed ROM bank at $4000. Which bank lands low is inferred
        # from the 128K case rather than confirmed -- see probe_fixed_high() and
        # the docs to check it against a particular image.
        self.inferred = (low == "rom")

    def regions(self):
        r = []
        if self.low in ("bank6", "rom"):
            r.append((0x4000, 0x8000, "fixed", self.nbanks - 2))
        elif self.low == "ram":
            r.append((0x4000, 0x8000, "ram", None))
        r.append((0x8000, 0xC000, "window", None))
        r.append((0xC000, 0x10000, "fixed", self.nbanks - 1))
        return r


class Absolute(Mapper):
    """F-18 Hornet: two selectable 16K banks low, 32K fixed high.

    Verified against the NTSC image running under MAME. The game writes only to
    $8000 and only the values $01 and $02, and reading $4000 back afterwards
    shows $01 selecting bank 0 and $02 selecting bank 1. $8000-$FFFF is the last
    32K -- bank 2 at $8000, bank 3 at $C000 -- which the same read-back confirms.

    Poking other values shows the selection following the low bit, odd choosing
    bank 0 and even bank 1, rather than ignoring anything out of range. That is
    the emulator's reading of the hardware; the cartridge itself never writes
    anything but 1 and 2, so only those two are established behaviour.
    """
    name = "absolute"
    switch = (0x8000, 0x8000)
    window_banks = 2

    def regions(self):
        return [(0x4000, 0x8000, "window", None),
                (0x8000, 0x10000, "fixed", self.nbanks - 2)]

    def bank_from_write(self, addr, value):
        if addr != 0x8000:
            return None
        return (value & 1) ^ 1


class Activision(Mapper):
    """Double Dragon and Rampage: 8K granularity, and a switch at $FF80.

    The odd one out. Every other 7800 mapper thinks in 16K banks; this one
    scatters four fixed 8K blocks around a 16K window, and the blocks are not
    in address order in the file:

        $4000-$5FFF   file $1A000   (8K block 13)
        $6000-$7FFF   file $18000   (8K block 12)
        $8000-$9FFF   file $1E000   (8K block 15)
        $A000-$DFFF   the window, 16K, eight banks
        $E000-$FFFF   file $1C000   (8K block 14)

    Note $E000 is block 14 and $8000 is block 15 -- reversed against what
    address order would suggest, which is exactly the kind of thing that makes
    a hand-guessed layout disassemble into plausible nonsense.

    A write to $FF80-$FF8F selects the window bank from the low three bits of
    the *address*, not the value: `STA $FF83` selects bank 3 whatever is in A.
    That is unlike every other mapper here, where the value chooses.

    Transcribed from `a78_rom_act_device::read_40xx` in the a7800 fork.
    """
    name = "activision"
    switch = (0xFF80, 0xFF8F)
    window_banks = 8

    def regions(self):
        return [(0x4000, 0x6000, "fixed", (0x1A000, "h13")),
                (0x6000, 0x8000, "fixed", (0x18000, "h12")),
                (0x8000, 0xA000, "fixed", (0x1E000, "h15")),
                (0xA000, 0xE000, "window", None),
                (0xE000, 0x10000, "fixed", (0x1C000, "h14"))]

    def bank_from_write(self, addr, value):
        """The address selects the bank here, not the value written."""
        lo, hi = self.switch
        if lo <= addr <= hi:
            return addr & 7
        return None


UNSUPPORTED = {
    0x2000: "Bankset: two parallel sets of banks, one seen by the CPU and one "
            "by MARIA, so a single address holds two different bytes.",
    0x1000: "SOUPER (Rikki and Vikki): its own mapper plus extra hardware, "
            "neither of which this module lays out. Two images in the library "
            "set this bit, and the canonical a78 header source names it, so "
            "the assignment is certain even though the layout is not "
            "implemented.",
}


def pick_mapper(size, header=None, mapper=None, low=None, bankset=False):
    """Choose a mapper from the header, the size, or an explicit override.

    `bankset` means `size` is already one half of the image: each side of a
    bankset cartridge is laid out by an ordinary mapper, flat or SuperGame
    depending on the same flags everything else uses.
    """
    nbanks = max(1, size // BANK)
    if mapper is None and header:
        flags = header["cart_type"]
        for bit, why in UNSUPPORTED.items():
            if bankset and bit == 0x2000:
                continue
            if flags & bit:
                raise UnknownMapper(why)
        if flags & 0x0100:
            mapper = "activision"
        elif flags & 0x0200:
            mapper = "absolute"
        elif flags & 0x0002:
            mapper = "supergame"
            if low is None:
                low = ("bank6" if flags & 0x0010 else
                       "ram" if flags & 0x0004 else
                       "rom" if flags & 0x0008 else "none")
        else:
            mapper = "linear"
    if mapper is None:
        # No header. Anything of exactly 16K or less can only be linear, and
        # bigger images are almost always SuperGame.
        mapper = "linear" if size <= BANK else "supergame"
    if mapper == "linear":
        return Linear(size)
    if mapper == "supergame":
        return SuperGame(nbanks, low or "none")
    if mapper == "absolute":
        return Absolute(nbanks)
    if mapper == "activision":
        if size != 128 * 1024:
            raise UnknownMapper(
                "the Activision mapper is a 128K layout and this image is %dK. "
                "Its four fixed blocks are at fixed file offsets, so a "
                "different size would land them somewhere else."
                % (size // 1024))
        return Activision(nbanks)
    raise ValueError("unknown mapper %r" % mapper)


# -------------------------------------------------------------------- the cart
class Cart(object):
    """A cartridge image, laid out.

    `side` matters only for bankset cartridges, which carry **two parallel sets
    of banks at the same addresses**: one the CPU sees, one MARIA sees. A single
    address holds two different bytes depending on which chip is asking, so the
    two cannot both be described by one address map. Open the side you want:

        Cart("game.a78")                 # what the CPU executes
        Cart("game.a78", side="maria")   # what MARIA fetches -- the artwork

    Verified against a7800: on `Bankset Test - 2x32K`, CPU $8000 reads the file
    at $0000 and CPU $9000 reads $1000, so the first half is the CPU's and the
    second is MARIA's.
    """

    def __init__(self, path, mapper=None, low=None, side="sally"):
        raw = open(path, "rb").read()
        self.path = path
        self.header_bytes = None
        self.info = read_header(raw)
        if self.info is not None:
            self.header_bytes, raw = raw[:128], raw[128:]
        self.side = side
        self.bankset = bool((self.info or {}).get("cart_type", 0) & 0x2000)
        if self.bankset:
            if side not in ("sally", "maria"):
                raise ValueError("side is 'sally' or 'maria', not %r" % side)
            half = len(raw) // 2
            raw = raw[:half] if side == "sally" else raw[half:]
        self.rom = raw
        self.map = pick_mapper(len(raw), self.info, mapper, low,
                               bankset=self.bankset)
        self.nbanks = self.map.nbanks
        self._region = list(self.map.regions())
        self.warnings = []
        self._check_pokey()
        self._check_activision()

    # -- the optional sound chip -------------------------------------------
    def pokey(self):
        """Where the cartridge's POKEY answers, or None.

        Only the $4000 placement touches the address map, and only in
        principle: a POKEY there claims $4000-$400F, and every ordinary cart
        that declares one is 16K or 32K (so its ROM starts at $C000 or $8000)
        or is a SuperGame with nothing else at $4000. The $0450 and $0800
        placements sit in space no cartridge uses.
        """
        bases = self.pokeys()
        return bases[0] if bases else None

    def pokeys(self):
        """Every POKEY the header declares, lowest address first.

        Nine images declare two: bit $0400 adds one at $0440 beside the $0450
        one, and every image that sets it says so in its own title.
        """
        ct = (self.info or {}).get("cart_type", 0)
        return [base for bit, base in ((0x0400, 0x0440), (0x0040, 0x0450),
                                       (0x8000, 0x0800), (0x0001, 0x4000))
                if ct & bit]

    def _check_activision(self):
        """An Activision image whose vectors come out wrong is mis-ordered.

        Verified against a7800: for a correct dump, $4000 is 8K block 13,
        $8000 is block 15 and $E000 is block 14, and the vectors read back
        sensibly. The "(OM)" dumps in circulation have blocks 14 and 15
        swapped, so the vectors land in a block full of zeros -- and a7800
        cannot boot them either, so this is the image being wrong rather than
        the layout.
        """
        if not isinstance(self.map, Activision):
            return
        rst = self.vectors().get("RESET", 0)
        if 0x4000 <= rst <= 0xFFFF:
            return
        alt = self.rom[0x1FFFC] | (self.rom[0x1FFFD] << 8)
        extra = ""
        if 0x4000 <= alt <= 0xFFFF:
            extra = (" Blocks 14 and 15 look swapped: reading the vectors from "
                     "block 15 instead gives RESET $%04X. The \"(OM)\" dumps "
                     "are like this, and no emulator runs them." % alt)
        self.warnings.append(
            "this Activision image's reset vector reads $%04X, which is not a "
            "usable address, so the fixed blocks are probably not in the order "
            "this mapper expects.%s" % (rst, extra))

    def _check_pokey(self):
        """A POKEY at $4000 and ROM at $4000 cannot both be right."""
        base = self.pokey()
        if base != 0x4000:
            return
        for start, end, kind, b in self._region:
            if start <= 0x4000 < end and kind in ("fixed", "window", "ram"):
                self.warnings.append(
                    "the header puts a POKEY at $4000 and also maps %s there; "
                    "one of the two is wrong, and reads of $4000-$400F will not "
                    "be what this layout says"
                    % ("on-cart RAM" if kind == "ram" else "ROM"))
                return

    # -- naming -------------------------------------------------------------
    def spaces(self):
        """Every space in the cart, low address first, windowed banks in order."""
        out = []
        for start, end, kind, bank in self._region:
            if kind == "fixed":
                out.append(self._fixed_name(bank))
            elif kind == "window":
                out.extend("b%d" % i for i in range(self.map.nwindow))
        return out

    def _fixed_name(self, arg):
        """What to call a fixed region.

        Normally a 16K bank index, so `f6` and `f7` mean what they always have.
        A mapper whose fixed regions are not 16K-aligned -- Activision maps four
        separate 8K blocks -- supplies `(byte_offset, name)` instead and names
        them itself, because `f6` for two different 8K halves of bank 6 would
        collide.
        """
        if isinstance(arg, tuple):
            return arg[1]
        return "rom" if isinstance(self.map, Linear) else "f%d" % arg

    def _fixed_offset(self, arg):
        """Where a fixed region starts in the file."""
        return arg[0] if isinstance(arg, tuple) else arg * BANK

    def _file_base(self, space):
        """The file offset a space's first byte lives at."""
        for _start, _end, kind, arg in self._region:
            if kind == "fixed" and self._fixed_name(arg) == space:
                return self._fixed_offset(arg)
        return self.bank_of(space) * BANK

    def space_of(self, addr, bank=None):
        """The space holding CPU `addr`, given which bank is in the window."""
        for start, end, kind, b in self._region:
            if not start <= addr < end:
                continue
            if kind == "fixed":
                return self._fixed_name(b)
            if kind == "window":
                return None if bank is None else "b%d" % bank
            return None                      # RAM: not ROM, no space
        return None

    def _region_of_space(self, space):
        for start, end, kind, b in self._region:
            if kind == "fixed" and self._fixed_name(b) == space:
                return start, end
            if kind == "window" and space.startswith("b"):
                return start, end
        # A bare KeyError here is useless. The usual cause is a dump whose
        # header understates the mapping -- Midnight Mutants' European release
        # declares $0002 where the cartridge really is $0012, so nothing is
        # mapped at $4000 and every f6 reference fails. Say that, and say how
        # to override it.
        have = ", ".join(self.spaces()) or "(none)"
        raise UnknownSpace(
            "this cartridge has no space %r. It declares cart type $%04X, "
            "which maps: %s.\n"
            "If the header understates the mapping -- some dumps do -- pass "
            "low='bank6' (or mapper=) to Cart, or --low bank6 on the command "
            "line, to map the second-to-last bank at $4000."
            % (space, (self.info or {}).get("cart_type", 0), have))

    def base_of(self, space):
        return self._region_of_space(space)[0]

    def size_of(self, space):
        start, end = self._region_of_space(space)
        return end - start

    def bank_of(self, space):
        return 0 if space == "rom" else int(space[1:])

    # -- reading ------------------------------------------------------------
    def _offset(self, space, addr):
        if isinstance(self.map, Linear):
            return addr - self.map.start
        return self._file_base(space) + (addr - self.base_of(space))

    def byte(self, space, addr):
        return self.rom[self._offset(space, addr)]

    def slice(self, space, addr, n):
        o = self._offset(space, addr)
        return self.rom[o:o + n]

    def word(self, space, addr):
        return self.byte(space, addr) | (self.byte(space, addr + 1) << 8)

    def in_space(self, space, addr):
        return self.base_of(space) <= addr < self.base_of(space) + self.size_of(space)

    def vectors(self):
        """NMI/RESET/IRQ, read from whatever space owns $FFFA."""
        sp = self.space_of(0xFFFA, bank=self.nbanks - 1)
        return {name: self.word(sp, a)
                for name, a in (("NMI", 0xFFFA), ("RESET", 0xFFFC),
                                ("IRQ", 0xFFFE))}

    # -- checking a layout rather than trusting it -------------------------
    def probe_fixed_high(self):
        """Which bank really sits at $C000, judged by its reset vector.

        The bank at $C000 owns $FFFC, so its reset vector should point at code
        that looks like 6502 startup -- SEI, CLD, a stack set-up. Scoring every
        bank that way and taking the winner is how the "last bank" rule was
        established; run it on a new image before believing the default.
        """
        best = []
        for i in range(self.nbanks):
            off = i * BANK
            if off + BANK > len(self.rom):
                break
            v = self.rom[off + 0x3FFC] | (self.rom[off + 0x3FFD] << 8)
            if v < 0xC000:
                continue
            t = off + (v - 0xC000)
            w = self.rom[t:t + 14]
            if len(w) < 14:
                continue
            s = ((2 if w[0] in (0x78, 0xD8, 0x4C, 0x20) else 0)
                 + (2 if 0x78 in w[:8] else 0)      # SEI
                 + (2 if 0xD8 in w[:8] else 0)      # CLD
                 + (2 if 0x9A in w[:10] else 0)     # TXS
                 + (1 if (0x85 in w[:10] or 0x8D in w[:10]) else 0)
                 + (1 if len(set(w)) > 4 else 0))
            best.append({"bank": i, "reset": v, "score": s,
                         "bytes": bytes(w[:6]).hex(" ")})
        best.sort(key=lambda x: -x["score"])
        return {"ranked": best, "default": self.nbanks - 1,
                "agrees": bool(best) and best[0]["bank"] == self.nbanks - 1}

    def file_bytes(self, rom=None):
        """The image as it sits on disk, header included if it had one."""
        return (self.header_bytes or b"") + bytes(self.rom if rom is None else rom)

    def describe(self):
        lines = []
        h = self.info
        lines.append("%s  (%d bytes%s)"
                     % (os.path.basename(self.path), len(self.rom),
                        ", 128-byte header" if h else ", headerless"))
        if h:
            lines.append("  title       %s" % (h["title"] or "-"))
            lines.append("  cart type   $%04X%s"
                         % (h["cart_type"],
                            ("  " + ", ".join(h["flags"])) if h["flags"] else ""))
            lines.append("  region      %s" % h["region"])
        if isinstance(self.map, Linear):
            lines.append("  mapper      linear, %dK, no banking"
                         % (len(self.rom) // 1024))
        else:
            lines.append("  mapper      %s, %d bank%s of %dK%s"
                         % (self.map.name, self.nbanks,
                            "" if self.nbanks == 1 else "s", BANK // 1024,
                            "  (%s)" % self.map.note if self.map.note else ""))
        for start, end, kind, b in self._region:
            if kind == "fixed":
                if isinstance(b, tuple):
                    what = "file $%05X, space %s" % (b[0], b[1])
                else:
                    what = "bank %d, space %s" % (b, self._fixed_name(b))
            elif kind == "window":
                n = self.map.nwindow
                what = "banks 0-%d, spaces b0-b%d" % (n - 1, n - 1)
            else:
                what = "on-cart RAM"
            lines.append("  $%04X-$%04X %s" % (start, end - 1, what))
        for pk in self.pokeys():
            lines.append("  POKEY       $%04X-$%04X%s"
                         % (pk, pk + 15,
                            "  (inside the cartridge address space)"
                            if pk == 0x4000 else ""))
        v = self.vectors()
        lines.append("  vectors     NMI $%04X  RESET $%04X  IRQ $%04X"
                     % (v["NMI"], v["RESET"], v["IRQ"]))
        for w in self.warnings:
            lines.append("  WARNING     %s" % w)
        return "\n".join(lines)


def main():
    import argparse
    ap = argparse.ArgumentParser(
        description="Describe a cartridge: header, mapper, and layout.")
    ap.add_argument("rom", nargs="+")
    ap.add_argument("--mapper", help="override the header's mapper")
    ap.add_argument("--low", help="override what sits below $4000")
    args = ap.parse_args()
    rc = 0
    for path in args.rom:
        try:
            print(Cart(path, mapper=args.mapper, low=args.low).describe())
        except (UnknownMapper, UnknownSpace, IOError) as e:
            import sys as _s
            _s.stderr.write("%s: %s\n" % (path, e))
            rc = 2
        print()
    return rc


if __name__ == "__main__":
    import sys
    sys.exit(main())
