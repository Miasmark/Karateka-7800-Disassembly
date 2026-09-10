"""Atari 7800 cartridge signatures: verify one, or make a valid one.

## Why this exists at all

An NTSC 7800 checks a digital signature in the cartridge before it will
start the console in 7800 mode. If the check fails it starts up in 2600
mode, so a modified cartridge does not refuse to run -- it runs as the
wrong machine, which on real hardware looks like a black or garbage
screen rather than an error. PAL consoles have no crypto check at all, and
neither MAME nor the a7800 fork verifies anything. That combination is
why a patch project can go a long way without noticing: every build works
everywhere it is tested and none of them would boot on the console the
game was sold for.

**This is an NTSC concern only, and the cartridges prove it.** Retail PAL
dumps of both Karateka and Midnight Mutants carry `$FF` across the whole
of `$FF80`-`$FFF7`: not a signature that fails, but unprogrammed EPROM
where a signature would go. Atari never signed the European releases
because nothing over there was going to look. So a PAL build needs no
signing, and signing one is harmless but pointless -- it writes 120 bytes
over filler that nothing reads, and changes the dump's CRC for no gain.

`region()` reads the `.a78` header's TV byte to tell them apart, and the
CLI skips a cartridge it knows is PAL. A headerless image carries no
region, so there it does what it is told.

Every byte of a patch is inside the hashed range for most cartridges, so
this is not about avoiding the signature block -- it is about recomputing
the signature afterwards.

## What the console actually checks

Three things, and the first is the only interesting one:

1.  The 120 bytes at `$FF80`-`$FFF7` are a signature over a hash of the
    cartridge. `$FFF9`'s high nibble says which page the hash starts at;
    it runs from there to `$FFFF`, with `$FF80`-`$FFF7` treated as zero.
2.  `$FFF8` must have its high nibble `$F` and its low bit set. `$FF`.
3.  `$FFF9`'s low nibble must be 3 or 7 (3 skips the rainbow), and the
    reset vector at `$FFFC` must point inside the hashed range -- which
    stops a cartridge from carrying somebody else's signed block verbatim
    and jumping out of it.

The signature scheme is Rabin, not RSA: **the public exponent is 2**. The
console squares the signature mod `n` and compares. Squares do not always
exist, which is why byte 4 of the hash is a don't-care that the signer
tweaks until the value is a quadratic residue mod both primes, and why
only the low three bits of byte 0 are compared.

## Provenance

The algorithm, the permutation table and the key material are from Bruce
Tomlin's `sign7800.c` (2004), which was itself a hand-decompilation of
Atari's own ST-based signing program. This is a port rather than a
translation: the C carries a hand-written bignum library because it had
to, and Python has integers, so the four bignum registers and 250 lines
of `multiply`/`divide`/`hashpower` collapse into `pow()`. The hash and the
tables are reproduced exactly.

Checked against `karateka-original.bin` and a stock `Commando` dump, both
of which verify, and against every patched build in this project, none of
which did before this module was wired into the build.

## Use

    python tools/sign7800.py FILE...          report each file's status
    python tools/sign7800.py --write FILE...  sign in place
    python tools/sign7800.py --write --force FILE...   PAL included

or from Python, on an image rather than a file::

    rom = sign7800.signed(rom)      # 48K headerless image in, signed out
    sign7800.verify(rom)            # True/False
"""

import io
import os
import sys

HDR = 128


# --------------------------------------------------------------- key material
# From sign7800.c. `S` is 264 bytes because the two permutations overlap:
# the forward pass uses S[0:256] and the reverse pass S[8:264].
S = bytes.fromhex(
    "c765abcaeef78309e1d0926762b67255"
    "8e91dcc581be782059b7e63d0645afc8"
    "083138d1fb7384a917fc3487a394fa90"
    "b8edce3b5b0a43d9f35382b30d6d5a60"
    "9d51a7b91110bce47f8041e7e3f65626"
    "35ecd6df0c7ff49eac5246efcfbfa23f"
    "a41315974a1cb0428cb105588018772b"
    "023ea8491a6acb6e0b8aebf14f14798b"
    "d89f9b5719f82a2d760ee82e4bf90703"
    "de93167ed4e5b2f07d7adad2a1cc1de0"
    "5e23a095221e3685fe1f39aa8996ad0f"
    "2fc047275d24eac3a5f5215f1b408fae"
    "7425ddc17ccda670d7337b2c75bb8699"
    "bd549a6c6332484c8dba5c61c44e2937"
    "12c6989cd5696be2044de9c2883adb64"
    "01446fb5f23028fd50713cb46668c9d3"
    "ca83c7abf76509ee")

_P = bytes.fromhex(
    "703ac16f9a92e52918e79e50d6a58dcc"
    "4d522ad45c10718124cfda6a4a72eed5"
    "ca361e1b2a20c0dc15e8ee53ebf32e08"
    "725935f899573b")

_Q = bytes.fromhex(
    "165615799a53604f87559fb27872e5bc"
    "7df86fca832a1fa763a3552bd8b845e2"
    "a11f412b041b9b5be128c5e06e7dc80b"
    "221ee7d4474ce5c89222adef7018f13d"
    "1b")

_N = bytes.fromhex(
    "09cac9c6b412081b6058814b8601d8bf"
    "d925a07bdc3279843b7cbc2fe2e2fa8d"
    "0a003bc5ecaf2d8acd06936aa5144677"
    "c46ab25336ef8cce0ca26871d373e8f7"
    "6d06b520ef23470c5155c8fef458c43f"
    "20a76738b076e2c4d80563f83c583b2d"
    "22cc88b3718f1d800a87bda15923e970"
    "e2d3ec4668804239")

P = int.from_bytes(_P, "big")
Q = int.from_bytes(_Q, "big")
N = int.from_bytes(_N, "big")
SIGLEN = len(_N)                        # 120 bytes, $FF80-$FFF7

assert P * Q == N, "key material does not multiply out"
assert P % 4 == 3 and Q % 4 == 3, "square roots here assume both primes are 3 mod 4"


class SignError(Exception):
    pass


# --------------------------------------------------------------------- the hash
def _expand(rom):
    """A headerless image, placed in a 64K space the way the console sees it."""
    if len(rom) > 0xC000:
        rom = rom[len(rom) - 0xC000:]
    cart = bytearray(0x10000)
    cart[0x10000 - len(rom):] = rom
    return cart


def cart_hash(rom):
    """The console's own hash of the cartridge, 120 bytes.

    Two passes over every page from `$FFF9`'s start page to `$FE`, forward
    with one permutation and backward with the other, around a 2048-bit
    double shift. The `$FF00` page seeds the accumulator with its
    signature zone zeroed, so the hash does not depend on the signature it
    is about to authenticate."""
    cart = _expand(rom)
    acc = bytearray(cart[0xFF00:0x10000])
    acc[0x80:0xF8] = b"\0" * 0x78
    start = cart[0xFFF9] & 0xF0

    def pass_(page, perm, a, carry):
        base = page << 8
        for i in range(256):
            v = a + acc[i] + carry
            carry = (v >> 8) & 1
            v = (v & 0xFF) + cart[base + i] + carry
            a = perm[v & 0xFF]
            acc[i] = a
            carry = (v >> 8) & 1
        return a, carry

    def rotate(carry):
        for i in range(256):
            v = (acc[i] << 1) + carry
            acc[i] = v & 0xFF
            carry = v >> 8
        return carry

    fwd, rev = S[0:256], S[8:264]
    a, carry = 0, 1
    for page in range(start, 0xFF):
        a, carry = pass_(page, fwd, a, carry)
        carry = 0
    carry = rotate(1)
    carry = rotate(carry)
    for page in range(0xFE, start - 1, -1):
        a, carry = pass_(page, rev, a, carry)
        carry = 1

    h = bytearray(acc[i] ^ acc[i + 0x50] ^ acc[i + 0x88] for i in range(0x78))
    h[0] &= 0x07        # only three bits of the top byte are compared
    h[4] = 0            # and byte 4 is the don't-care the signer tweaks
    return bytes(h)


# ------------------------------------------------------------------- verify
def unsigned(rom):
    """True when the signature block was never programmed.

    All `$FF` is an erased EPROM, not a signature that happens to be
    wrong, and the difference matters when reporting: a PAL cartridge is
    not broken, it was simply never signed. Every retail PAL dump checked
    looks like this."""
    cart = _expand(rom)
    return set(cart[0xFF80:0xFF80 + SIGLEN]) in ({0xFF}, {0x00})


def region(blob):
    """'pal', 'ntsc' or None, from the .a78 header's TV byte.

    Byte 57 of the 128-byte header is 0 for NTSC and 1 for PAL. A
    headerless image says nothing, and gets None."""
    if len(blob) % 0x1000 != HDR:
        return None
    return "pal" if blob[57] == 1 else "ntsc"


def verify(rom):
    """Does this image carry a signature the console would accept?"""
    cart = _expand(rom)
    sig = int.from_bytes(bytes(cart[0xFF80:0xFF80 + SIGLEN]), "big")
    got = bytearray((sig * sig % N).to_bytes(SIGLEN, "big"))
    got[0] &= 0x07
    got[4] = 0
    return bytes(got) == cart_hash(rom)


def header_ok(rom):
    """The three non-crypto rules, returned as a list of complaints."""
    cart = _expand(rom)
    bad = []
    if cart[0xFFF8] & 0xF1 != 0xF1:
        bad.append("$FFF8 is $%02X: needs high nibble $F and the low bit set"
                   % cart[0xFFF8])
    if cart[0xFFF9] & 0x0B != 3:
        bad.append("$FFF9 low nibble is %X: must be 3 or 7"
                   % (cart[0xFFF9] & 0x0F))
    start = cart[0xFFF9] & 0xF0
    if (start << 8) < 0x10000 - min(len(rom), 0xC000):
        bad.append("$FFF9 asks for a hash larger than the image")
    if cart[0xFFFD] < start:
        bad.append("reset vector $%02X%02X points outside the hashed range"
                   % (cart[0xFFFD], cart[0xFFFC]))
    return bad


# --------------------------------------------------------------------- sign
def _sqrt_mod(a, p):
    """A square root of a mod p, for p = 3 mod 4. None if a is not one."""
    if pow(a, (p - 1) // 2, p) != 1:
        return None
    return pow(a, (p + 1) // 4, p)


def signature(rom):
    """The 120 signature bytes for this image.

    Byte 4 of the hash is not compared by the console, so it is free to
    move: step it until the hash is a quadratic residue mod both primes,
    which usually takes a handful of tries and is the whole reason that
    byte is a don't-care."""
    h = bytearray(cart_hash(rom))
    qinv = pow(Q, -1, P)
    for tweak in range(256):
        h[4] = tweak
        value = int.from_bytes(bytes(h), "big")
        rp = _sqrt_mod(value, P)
        if rp is None:
            continue
        rq = _sqrt_mod(value, Q)
        if rq is None:
            continue
        # Garner: lift the two roots to one root mod P*Q
        sig = (rq + Q * (qinv * (rp - rq) % P)) % N
        if sig * sig % N != value:
            continue                    # cannot happen; cheap to be sure
        return sig.to_bytes(SIGLEN, "big"), tweak
    raise SignError("no quadratic residue in 256 tweaks -- this should not "
                    "happen for a real cartridge image")


def signed(rom):
    """The same image with a signature the console will accept.

    Returns it unchanged if it already verifies, so this is safe to run
    over an untouched dump."""
    bad = header_ok(rom)
    if bad:
        raise SignError("; ".join(bad))
    if verify(rom):
        return rom
    sig, _ = signature(rom)
    out = bytearray(rom)
    # the image's last byte is $FFFF, so the signature block starts $80 back
    out[len(rom) - 0x80:len(rom) - 0x80 + SIGLEN] = sig
    if not verify(bytes(out)):
        raise SignError("signed image still does not verify")
    return bytes(out)


# ---------------------------------------------------------------------- cli
def _load(path):
    raw = io.open(path, "rb").read()
    if len(raw) % 0x1000 == HDR:
        return raw[HDR:], raw[:HDR]
    return raw, b""


def main(argv):
    write = force = False
    paths = []
    for a in argv:
        if a in ("-w", "--write"):
            write = True
        elif a == "--force":
            force = True                # sign a PAL cartridge anyway
        elif a in ("-h", "--help"):
            print(__doc__)
            return 0
        else:
            paths.append(a)
    if not paths:
        print(__doc__)
        return 2

    rc = 0
    for path in paths:
        rom, hdr = _load(path)
        name = os.path.basename(path)
        where = region(hdr + rom)
        if where == "pal" and not force:
            print("%-46s PAL -- no signature needed%s"
                  % (name, "" if unsigned(rom) else ", and it carries one"))
            continue
        bad = header_ok(rom)
        if bad:
            print("%-46s CANNOT SIGN: %s" % (name, "; ".join(bad)))
            rc = 1
            continue
        if verify(rom):
            print("%-46s valid" % name)
            continue
        if not write:
            print("%-46s %s" % (name, (
                "never signed -- fine on PAL, boots a real NTSC 7800 in "
                "2600 mode" if unsigned(rom) else
                "INVALID -- would boot a real NTSC 7800 in 2600 mode")))
            rc = 1
            continue
        out = signed(rom)
        io.open(path, "wb").write(hdr + out)
        print("%-46s signed" % name)
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
