#!/usr/bin/env python3
"""
A tracker for the Atari 7800's TIA sound, and a way to hear what a cartridge
is already doing.

Every 7800 has two TIA audio channels, and each is three registers: AUDC picks
a waveform, AUDF divides the clock, AUDV sets the volume. That is the whole
instrument. This module models it accurately enough to render to a WAV, reads
and writes a plain-text tracker format, and turns a song into 6502 data.

Four things it does:

  python tracker.py notes                    what pitches the TIA can play
  python tracker.py render song.trk -o a.wav hear a song
  python tracker.py capture log.txt -o a.trk turn a MAME capture into a song
  python tracker.py export song.trk          6502 tables plus a player

`capture` is the one that pairs with the rest of the toolkit: `probes/audio.lua`
logs every AUDC/AUDF/AUDV write from any running 7800 game, and this turns that
log into an editable tracker file. That works on a cartridge whose music player
has not been reverse-engineered at all -- the register writes are the music,
whatever produced them.

## The chip, briefly

AUDF is **five bits**. Code often writes a whole byte and lets the chip discard
the top three, so a value read out of a ROM may need masking before it means
anything -- an unmasked byte plays about three times too low, which in the
polynomial voices drops below the range where a pitch is audible at all and
turns the voice into a tapping noise.

AUDC selects between pure dividers and polynomial counters (LFSRs). The pure
ones give the tones you can write melodies with; the polynomial ones give
buzzes and noise. AUDV is a 4-bit linear volume.

The TIA is not tuned to anything. Its pitches are `clock / ((AUDF+1) * divisor
* 2)`, which lands where it lands -- `notes` prints how far off each one is, in
cents, so a melody can be written around the notes that are actually close.
"""
import argparse
import math
import os
import re
import struct
import sys
import wave

# NTSC divides the 3.579545 MHz colour clock by 114; PAL's is a shade lower.
# Everything scales off this, so a song rendered for the wrong region is in the
# wrong key as well as the wrong tempo.
CLOCK = {"ntsc": 31400.0, "pal": 31200.0}
FRAME = {"ntsc": 60.0, "pal": 50.0}
SAMPLE_RATE = 44100
AUDF_MAX = 31                       # five bits


# ----------------------------------------------------------------- the chip
def _poly(bits, taps, length, xnor=False, seed=None):
    """An LFSR: the taps combined and shifted back in at the top.

    With XNOR the **all-ones state is the lockup** -- the register stops
    changing and the output goes flat -- so an XNOR generator must never be
    seeded that way. That mistake is silent: you get a table, of the right
    length, of the wrong thing.
    """
    if seed is None:
        seed = 0 if xnor else (1 << bits) - 1
    reg, out = seed, []
    mask = (1 << bits) - 1
    for _ in range(length):
        out.append(reg & 1)
        fb = 0
        for t in taps:
            fb ^= (reg >> t) & 1
        if xnor:
            fb ^= 1
        reg = ((reg >> 1) | (fb << (bits - 1))) & mask
    return out


def _pokey_lfsr(taps, length, seed=None):
    """POKEY's polynomials, as `s[n] = NOT(s[n-a] XOR s[n-b])`.

    Written as a recurrence rather than a register because that is the form
    that was measured: each table was recovered from the running chip by
    inverting the decimation (see `pokey_poly_step`), and Berlekamp-Massey on
    the recovered cycle gives exactly this. Both come out as the textbook
    polynomials -- x^9 + x^4 + 1 and x^17 + x^12 + 1 -- with **XNOR** feedback,
    and the XNOR is why a plain XOR generator mispredicts every single bit.

    Verified against the whole cycle, not a sample of it: 0 mispredictions in
    511 and in 131,071.
    """
    a, b = min(taps), max(taps)
    s = list(seed) if seed else ([0] * (b - 1) + [1])
    while len(s) < length + b:
        s.append(1 ^ s[len(s) - a] ^ s[len(s) - b])
    return s[b:b + length]


POLY4 = _poly(4, (0, 1), 15)
POLY5 = _poly(5, (0, 2), 31)
POLY9 = _poly(9, (0, 4), 511)
SQUARE = [1, 0]

# For each AUDC: the bit pattern the output stage walks, and how many divided
# clocks one step of that pattern lasts. A pure tone is the pattern [1,0], so
# its period is two steps; "div 6" means each step lasts six, giving the
# familiar period of twelve.
AUDC_MODE = {
    0x00: (None, 1),    0x0B: (None, 1),        # silence
    0x04: (SQUARE, 1),  0x05: (SQUARE, 1),      # pure tone
    0x0C: (SQUARE, 6),  0x0D: (SQUARE, 6),      # pure tone, div 6
    0x06: (SQUARE, 31), 0x0A: (SQUARE, 31),     # pure tone, div 31
    0x0E: (SQUARE, 93),                         # pure tone, div 93
    0x01: (POLY4, 1),                           # 4-bit poly, a pitched buzz
    0x02: (POLY4, 31),                          # 4-bit poly, div 31
    0x03: (POLY4, 31),                          # 5-bit poly gating 4-bit
    0x07: (POLY5, 1),   0x09: (POLY5, 1),       # 5-bit poly
    0x0F: (POLY5, 6),                           # 5-bit poly, div 6
    0x08: (POLY9, 1),                           # 9-bit poly -- white noise
}
AUDC_NAME = {
    0x00: "silence", 0x01: "buzz (4-bit poly)", 0x02: "buzz, div 31",
    0x03: "buzz, gated", 0x04: "tone", 0x05: "tone",
    0x06: "tone, div 31", 0x07: "5-bit poly", 0x08: "white noise",
    0x09: "5-bit poly", 0x0A: "tone, div 31", 0x0B: "silence",
    0x0C: "tone, div 6", 0x0D: "tone, div 6", 0x0E: "tone, div 93",
    0x0F: "5-bit poly, div 6",
}
TONE_MODES = [0x04, 0x0C, 0x06, 0x0E]      # the ones you can write a tune with


def is_tone(audc):
    return AUDC_MODE.get(audc, (None, 1))[0] is SQUARE


def frequency(audc, audf, region="ntsc"):
    """The pitch of one setting, in Hz, or None if it is silent.

    For a tone this is the note. For a polynomial voice it is the rate at
    which the pattern repeats, which is what you hear as its pitch.
    """
    table, pre = AUDC_MODE.get(audc, (None, 1))
    if table is None:
        return None
    return CLOCK[region] / ((audf + 1) * pre * len(table))


def channel(audc, audf, nsamples, phase=0.0, region="ntsc"):
    """`nsamples` of -1/+1 for one channel, plus the phase to carry forward.

    Carrying the phase matters: restarting it every row puts a click at each
    row boundary, which is audible as a buzz over the whole song.
    """
    table, pre = AUDC_MODE.get(audc, (None, 1))
    if table is None:
        return [0.0] * nsamples, phase
    rate = CLOCK[region] / ((audf + 1) * pre)
    step = rate / SAMPLE_RATE
    out = []
    for _ in range(nsamples):
        phase += step
        out.append(1.0 if table[int(phase) % len(table)] else -1.0)
    return out, phase


# ------------------------------------------------------------- notes and pitch
A4 = 440.0
NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def midi_of(freq):
    return 69.0 + 12.0 * math.log(freq / A4, 2)


def name_of(freq):
    """Nearest note name and how far off it is, in cents."""
    m = midi_of(freq)
    n = int(round(m))
    cents = (m - n) * 100.0
    return "%s%d" % (NAMES[n % 12], n // 12 - 1), cents


def parse_note(text):
    """'C-4', 'C#4', 'Bb3' -> midi number, or None.

    The octave may be two digits: these chips reach well past octave 9 when a
    channel is on the 1.79 MHz clock, and `name_of` will happily print A13.

    A leading '-' is the tracker convention for "no accidental", not a minus
    sign, so an octave cannot be written negative. Sub-audio pitches therefore
    have no writable name -- `format_cell` falls back to the raw divider for
    them, which its round-trip check catches automatically.
    """
    m = re.match(r"^([A-Ga-g])([#b-]?)(\d{1,2})$", text.strip())
    if not m:
        return None
    step = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}[m.group(1).upper()]
    acc = {"#": 1, "b": -1, "-": 0, "": 0}[m.group(2)]
    octave = int(m.group(3))
    return (octave + 1) * 12 + step + acc


def best_audf(audc, midi, region="ntsc"):
    """The AUDF whose pitch is closest to a note, and the error in cents."""
    want = A4 * (2.0 ** ((midi - 69) / 12.0))
    best, err = None, None
    for f in range(AUDF_MAX + 1):
        got = frequency(audc, f, region)
        if not got:
            continue
        c = 1200.0 * math.log(got / want, 2)
        if err is None or abs(c) < abs(err):
            best, err = f, c
    return best, err


# ---------------------------------------------------------------- the POKEY
# An optional cartridge chip, on about a fifth of the library. Four channels
# instead of two, and an eight-bit divider instead of five -- which is the
# whole reason it sounds in tune where the TIA does not.
#
# Per channel: AUDF is the divider, AUDC packs a 4-bit volume, a volume-only
# flag, and a 3-bit distortion select. AUDCTL is global.
#
# WHAT IS MODELLED. Four channels with all eight distortions and the
# per-channel clock selection; both 16-bit pairs; and both high-pass filters.
# The 16-bit dividers were checked against MAME's POKEY across all four
# pairing paths and agree to 0.00 cents; the filters reproduce its spectrum
# peak for peak.
#
# NOT modelled: the 9-bit polynomial (AUDCTL $80), and volume-only mode, which
# renders as silence -- see `pokey_channel`. Rather than render those wrongly,
# an AUDCTL carrying them is refused outright. See `POKEY_UNSUPPORTED`.
POKEY_BASE_CLOCK = {"ntsc": 1789772.5, "pal": 1773447.0}   # colour clock / 2
POKEY_DIV = {"64k": 28, "15k": 114}

# AUDCTL bits.
CTL_15K      = 0x01     # base clock 15 kHz instead of 64 kHz     (supported)
CTL_HPF_2    = 0x02     # channel 2 high-pass filtered by 4          (tier 2)
CTL_HPF_1    = 0x04     # channel 1 high-pass filtered by 3          (tier 2)
CTL_JOIN_34  = 0x08     # channels 3+4 as one 16-bit divider         (tier 2)
CTL_JOIN_12  = 0x10     # channels 1+2 as one 16-bit divider         (tier 2)
CTL_CH3_FAST = 0x20     # channel 3 clocked at 1.79 MHz           (supported)
CTL_CH1_FAST = 0x40     # channel 1 clocked at 1.79 MHz           (supported)
CTL_POLY9    = 0x80     # 9-bit polynomial instead of 17-bit         (tier 2)

POKEY_SUPPORTED = 0xFF          # every AUDCTL bit is modelled
# Nothing in AUDCTL is refused any more. The mechanism stays because it is the
# honest way to fail: if a future chip variant or a misread bit turns up, it
# belongs here rather than being rendered as something plausible.
POKEY_UNSUPPORTED = {}

# 16-bit pairing and the high-pass filters both work by wiring two channels
# together, so they are described as pairs rather than as per-channel flags.
# In both cases the LOW-numbered channel of the pair is the one that carries
# the clock, and channel numbers here are 0-based (POKEY's "channel 1" is 0).
JOIN_PAIRS = {CTL_JOIN_12: (0, 1), CTL_JOIN_34: (2, 3)}
HPF_PAIRS = {CTL_HPF_1: (0, 2), CTL_HPF_2: (1, 3)}

# POKEY's own polynomials. The 17-bit one is long enough that a period of it
# is 131,071 steps -- generated once, lazily, because it is 128 KB of list.
_POLY_CACHE = {}


def pokey_poly(bits, taps, xnor=False, seed=None):
    key = (bits, taps, xnor, seed)
    if key not in _POLY_CACHE:
        _POLY_CACHE[key] = _poly(bits, taps, (1 << bits) - 1, xnor, seed)
    return _POLY_CACHE[key]


def poly4():
    """The 4-bit register. Recovered from the chip and identical, up to phase,
    to the table this file has always had."""
    if 4 not in _POLY_CACHE:
        _POLY_CACHE[4] = _poly(4, (0, 1), 15)
    return _POLY_CACHE[4]


def poly5():
    """The 5-bit register: shifts left, XNOR of bits 2 and 4, seeded zero.

    Transcribed from `a7800`'s `poly_init_4_5` and confirmed against the chip:
    distortion 1 is this polynomial gating a tone, and it reproduces the
    hardware exactly at three different divider and clock settings.
    """
    if 5 not in _POLY_CACHE:
        lfsr, out = 0, []
        for _ in range(31):
            lfsr = ((lfsr + lfsr) + (~((lfsr >> 2) ^ (lfsr >> 4)) & 1)) & 0x1F
            out.append(lfsr & 1)
        _POLY_CACHE[5] = out
    return _POLY_CACHE[5]


def poly9():
    """The 9-bit register: x^9 + x^4 + 1, XNOR. 511 steps.

    Recovered from MAME v0.287 and then checked against `a7800` v5.2, which
    lists a "pokey poly9 sequence correction" -- the two agree, at 1.0000 on
    both the 64 kHz and 15 kHz clocks. The companion note in that release,
    "pokey init state corrected", is the starting phase, which is inaudible and
    which a phase-aligned comparison absorbs.
    """
    if 9 not in _POLY_CACHE:
        _POLY_CACHE[9] = _pokey_lfsr((4, 9), 511)
    return _POLY_CACHE[9]


def poly17():
    """The 17-bit register: x^17 + x^12 + 1, XNOR. 131,071 steps."""
    if 17 not in _POLY_CACHE:
        _POLY_CACHE[17] = _pokey_lfsr((12, 17), 131071)
    return _POLY_CACHE[17]


def poly_long(audctl):
    """Whichever of the two long polynomials AUDCTL has selected."""
    return poly9() if audctl & CTL_POLY9 else poly17()


# Distortion, from AUDC bits 5-7. Two pairs are duplicates on the real chip.
#   gate  -- the 5-bit poly must pass before the second stage advances
#   stage -- what supplies the output bit, or None for a plain toggle
POKEY_DISTORTION = {
    0: ("poly5 gating poly17", True,  "17"),
    1: ("poly5 gating a tone", True,  None),
    2: ("poly5 gating poly4",  True,  "4"),
    3: ("poly5 gating a tone", True,  None),
    4: ("poly17",              False, "17"),
    5: ("pure tone",           False, None),
    6: ("poly4",               False, "4"),
    7: ("pure tone",           False, None),
}


def pokey_unsupported(audctl):
    """Which AUDCTL bits this tier does not model."""
    return [name for bit, name in sorted(POKEY_UNSUPPORTED.items())
            if audctl & bit]


def pokey_clock(ch, audctl, region="ntsc"):
    """The clock feeding one channel, in Hz, and its divider offset.

    Channels 1 and 3 can be switched to the raw 1.79 MHz clock. On that clock
    the divider is (AUDF + 4) rather than (AUDF + 1): three extra counts of
    pipeline that only show up at this speed.
    """
    base = POKEY_BASE_CLOCK[region]
    if ch == 0 and (audctl & CTL_CH1_FAST):
        return base, 4
    if ch == 2 and (audctl & CTL_CH3_FAST):
        return base, 4
    return base / POKEY_DIV["15k" if audctl & CTL_15K else "64k"], 1


def pokey_joined(audctl):
    """{high channel: low channel} for each 16-bit pair AUDCTL has switched on.

    Joining two channels makes one divider sixteen bits wide: the low
    channel's AUDF is the low byte, the high channel's the high byte. The
    sound comes out of the HIGH channel, and the low one stops being a voice
    at all -- players set its volume to zero and it is muted here regardless,
    because a joined channel that still sings is the loudest possible way to
    get this wrong.
    """
    out = {}
    for bit, (lo, hi) in JOIN_PAIRS.items():
        if audctl & bit:
            out[hi] = lo
    return out


def pokey_divider(ch, audfs, audctl):
    """(divider, offset, clock channel) for one channel this row.

    `audfs` is all four AUDF values, because with pairing a channel's pitch is
    not a function of its own register alone. The offset is the pipeline delay
    added to the divider before it divides: one count normally, four on the
    1.79 MHz clock, and seven when a 16-bit pair runs on that clock.
    """
    joined = pokey_joined(audctl)
    if ch in joined:
        lo = joined[ch]
        value = (audfs[ch] << 8) | audfs[lo]
        fast = (lo == 0 and (audctl & CTL_CH1_FAST)) or \
               (lo == 2 and (audctl & CTL_CH3_FAST))
        return value, (7 if fast else 1), lo
    _clk, off = pokey_clock(ch, audctl)
    return audfs[ch], off, ch


def pokey_rate(ch, audfs, audctl, region="ntsc"):
    """Divider ticks per second for one channel, given the whole row."""
    value, off, clock_ch = pokey_divider(ch, audfs, audctl)
    clk, _off = pokey_clock(clock_ch, audctl, region)
    return clk / float(value + off)


def pokey_poly_step(ch, audfs, audctl, region="ntsc"):
    """How far the polynomial counters advance between two divider ticks.

    This is the part that is easy to get wrong, and getting it wrong is not
    obvious. The polynomials are **not** clocked by the channel: they run
    continuously at the 1.79 MHz base clock, and a channel merely samples
    whichever bit they are showing when its divider fires. So a channel on the
    64 kHz clock skips 28 polynomial steps per tick, and one on the 15 kHz
    clock skips 114.

    Advancing them one step per tick instead -- which this file used to do --
    produces a sequence that is a decimation of the real one. It reproduces
    whatever divider you happened to test and nothing else, and because the
    result is still noise, it sounds entirely convincing. The giveaway was that
    the same polynomial appeared to have period 73 on the 64 kHz clock and 511
    on the 15 kHz clock; 28*(AUDF+1) shares a factor of 7 with 511 = 7*73.
    """
    value, off, clock_ch = pokey_divider(ch, audfs, audctl)
    base = POKEY_BASE_CLOCK[region]
    clk, _off = pokey_clock(clock_ch, audctl, region)
    return int(round(base / clk)) * (value + off)


def pokey_frequency(ch, audf, audctl=0, region="ntsc"):
    """The pitch of one channel setting, in Hz.

    Takes a single AUDF, so it describes a channel on its own. Under 16-bit
    pairing a channel's pitch depends on its partner too -- use `pokey_rate`
    with the whole row for that.
    """
    clk, off = pokey_clock(ch, audctl, region)
    return clk / (2.0 * (audf + off))


def pokey_best_audf(ch, midi, audctl=0, region="ntsc"):
    """The AUDF closest to a note, and the error in cents."""
    want = A4 * (2.0 ** ((midi - 69) / 12.0))
    best, err = None, None
    for f in range(256):
        got = pokey_frequency(ch, f, audctl, region)
        c = 1200.0 * math.log(got / want, 2)
        if err is None or abs(c) < abs(err):
            best, err = f, c
    return best, err


def pokey_channel(ch, audc, audf, audctl, nsamples, state=None, region="ntsc",
                  rate=None, pstep=None):
    """`nsamples` of -1/+1 for one POKEY channel, plus state and clock edges.

    `rate` overrides the divider rate for a channel that is not clocked by its
    own AUDF -- the high half of a 16-bit pair. The third return value marks
    the samples on which the divider ticked, which is what clocks the
    high-pass flip-flop.

    The divider clocks the channel; what comes out on each clock depends on
    the distortion -- a plain toggle, a polynomial bit, or a polynomial bit
    that only advances when the 5-bit polynomial lets it through.

    Volume-only mode (AUDC bit 4) bypasses all of it and puts the volume
    straight on the output as a DC level -- which is what a player uses to push
    samples through. Measured against MAME, the output is exactly linear in the
    volume nibble and has no AC content at all: 372 units per step, peak-to-peak
    zero. So it is held at full scale here and the mixer scales it by volume,
    which reproduces both cases correctly. A level that never changes is an
    inaudible offset, as on the chip; a level that changes every frame or two is
    the steps, which is the sample.

    It used to return silence. That was wrong, and it silently dropped whatever
    a game was pushing through this mode.
    """
    st = state or {"phase": 0.0, "out": 1, "p4": 0, "p5": 0, "p17": 0}
    if audc & 0x10:                       # volume-only: no oscillator at all
        return [1.0] * nsamples, st, [False] * nsamples
    dist = (audc >> 5) & 7
    _, gate, stage = POKEY_DISTORTION[dist]
    if rate is None:
        clk, off = pokey_clock(ch, audctl, region)
        rate = clk / (audf + off)         # divider ticks per second
    if pstep is None:
        audfs = [0, 0, 0, 0]
        audfs[ch % 4] = audf
        pstep = pokey_poly_step(ch % 4, audfs, audctl, region)
    step = rate / SAMPLE_RATE
    t4, t5, t17 = poly4(), poly5(), poly_long(audctl)
    out, edges = [], []
    phase = st["phase"]
    for _ in range(nsamples):
        phase += step
        ticks = int(phase)
        phase -= ticks
        edges.append(ticks > 0)
        for _ in range(min(ticks, 64)):   # a very fast divider is noise anyway
            # The polynomials free-run at the base clock; the channel only
            # samples them when its divider fires. `pstep` is how many steps
            # they have taken since the last tick -- 28 per tick on the 64 kHz
            # clock, 114 on the 15 kHz one, and never 1 unless the channel is
            # on the 1.79 MHz clock with AUDF 0.
            st["p4"] = (st["p4"] + pstep) % len(t4)
            st["p5"] = (st["p5"] + pstep) % len(t5)
            st["p17"] = (st["p17"] + pstep) % len(t17)
            # The 5-bit polynomial gates the output stage: unless the channel
            # is in a NOTPOLY5 mode, nothing happens on a tick it blocks.
            passed = True if not gate else bool(t5[st["p5"]])
            if not passed:
                continue
            if stage == "5":
                st["out"] ^= 1            # gated pure tone
            elif stage == "4":
                st["out"] = t4[st["p4"]]
            elif stage == "17":
                st["out"] = t17[st["p17"]]
            else:
                st["out"] ^= 1
        out.append(1.0 if st["out"] else -1.0)
    st["phase"] = phase
    return out, st, edges

def note_table(region="ntsc", limit_cents=50.0):
    """Every pitch the tone modes can produce, as notes."""
    rows = []
    for audc in TONE_MODES:
        for audf in range(AUDF_MAX + 1):
            f = frequency(audc, audf, region)
            nm, cents = name_of(f)
            rows.append({"audc": audc, "audf": audf, "hz": f,
                         "note": nm, "cents": cents,
                         "usable": abs(cents) <= limit_cents})
    return rows


# ------------------------------------------------------------ the song format
CHANNELS = {"tia": 2, "pokey": 4, "pokey2": 8}

# How many POKEYs a chip name means. Eighteen images in the library declare two
# -- header bit $0400 puts a second one at $0440 beside the $0450 -- and they
# are not a stereo pair: on Byzex and Acidjazzed the two chips are written
# different values in 86% of frames, so a dual-POKEY cartridge is eight
# independent voices and half its music is lost if only one is read.
POKEYS = {"pokey": 1, "pokey2": 2}


def is_pokey(chip):
    """True for any POKEY cartridge, one chip or two.

    Worth a function rather than `chip == "pokey"` scattered about: when the
    two-chip name arrived, every one of those comparisons quietly became a test
    for "exactly one POKEY", and a song with eight voices started being read
    with the TIA's five-bit divider.
    """
    return chip in POKEYS


class Song(object):
    """Rows of channels. A cell is (a, f, v) or None to hold what was playing.

    `a` is the waveform selector and `v` the volume, for both chips -- but they
    live in different places on the hardware. The TIA has a separate AUDC and
    AUDV, so they map straight across. POKEY packs both into one AUDC byte as
    `(distortion << 5) | volume`, so `a` is the 3-bit distortion and the byte
    is assembled on the way out. Keeping the tracker's shape the same for both
    is what lets the format, the renderer and the exporter stay single-track.

    POKEY also has AUDCTL, which is global rather than per channel, so it is
    held per row. `audctl[i]` is a tuple with one value per POKEY on the
    cartridge -- normally one, but two on the images that carry a second chip.
    Use `ctl_of(row, ch)` rather than indexing it, so a channel always gets the
    AUDCTL of the chip it actually lives on.
    """

    def __init__(self, title="", region="ntsc", rate=None, chip="tia"):
        self.title = title
        self.region = region
        self.rate = rate or FRAME[region]
        self.chip = chip
        self.rows = []
        self.audctl = []                # POKEY only; parallel to rows
        self.warnings = []

    @property
    def nch(self):
        return CHANNELS[self.chip]

    @property
    def nchips(self):
        """How many POKEYs this song is written for. TIA has none."""
        return POKEYS.get(self.chip, 0)

    def ctl_of(self, row, ch=0):
        """The AUDCTL governing channel `ch` on this row.

        Channels 0-3 are the first POKEY, 4-7 the second. Asking for the row's
        AUDCTL without saying which channel is what produced the bug this
        method exists to prevent: the second chip's four voices rendered with
        the first chip's clock and pairing settings, which is wrong in a way
        that still sounds like music.
        """
        if not self.audctl or row >= len(self.audctl):
            return 0
        v = self.audctl[row]
        if isinstance(v, (list, tuple)):
            i = ch // 4
            return v[i] if i < len(v) else (v[0] if v else 0)
        return v

    def all_ctls(self, row):
        v = self.audctl[row] if self.audctl and row < len(self.audctl) else 0
        return tuple(v) if isinstance(v, (list, tuple)) else (v,)

    @property
    def unsupported(self):
        """[(feature, audctl)] this tier does not model.

        Computed from the song rather than recorded as it is parsed. A song
        built by read_capture never went through the parser, and when this was
        an attribute that path skipped the check entirely -- so a capture that
        a file would refuse rendered anyway, which is the exact failure the
        refusal exists to prevent.
        """
        if not self.nchips:
            return []
        out = []
        seen = set()
        for i in range(len(self.audctl)):
            seen.update(self.all_ctls(i))
        for ctl in sorted(seen):
            for name in pokey_unsupported(ctl):
                if (name, ctl) not in out:
                    out.append((name, ctl))
        return out

    def add(self, *cells, **kw):
        if len(cells) == 1 and isinstance(cells[0], (list, tuple))                 and (not cells[0] or isinstance(cells[0][0], (tuple, type(None)))):
            cells = tuple(cells[0])
        if len(cells) != self.nch:
            raise ValueError("a %s row has %d channels, got %d"
                             % (self.chip, self.nch, len(cells)))
        self.rows.append(tuple(cells))
        ctl = kw.get("audctl", self.audctl[-1] if self.audctl else 0)
        if not isinstance(ctl, (list, tuple)):
            ctl = (ctl,) * max(1, self.nchips)
        else:
            ctl = tuple(ctl)
            if self.nchips and len(ctl) < self.nchips:
                ctl = ctl + (ctl[-1] if ctl else 0,) * (self.nchips - len(ctl))
        self.audctl.append(ctl)

    # -- resolved playback state, row by row
    def states(self):
        cur = [(0, 0, 0)] * self.nch
        for i, r in enumerate(self.rows):
            cur = [r[c] if r[c] is not None else cur[c] for c in range(self.nch)]
            yield tuple(cur)

    def audc_byte(self, cell):
        """The AUDC register value for a cell on this chip."""
        a, _f, v = cell
        if is_pokey(self.chip):
            return ((a & 7) << 5) | (v & 0x0F)
        return a & 0x0F

    def __len__(self):
        return len(self.rows)


CELL_RE = re.compile(r"^\s*(?:(\.\.\.|---)|(?:([0-9A-Fa-f]{1,2})\s+"
                     r"([A-Ga-g][#b-]?-?\d|\$[0-9A-Fa-f]{1,2}|\d{1,2})"
                     r"\s+([0-9A-Fa-f]{1,2})))\s*$")


# A note further than this from anything the chip can produce is nearer some
# other note than the one that was asked for, so writing it silently would put
# a different tune in the ROM than the one in the file.
OUT_OF_TUNE = 50.0


def parse_cell(text, region, warn=None, chip="tia", ch=0, audctl=0):
    """Text to (a, f, v). '...' holds, 'off' clears.

    On the TIA `a` is AUDC; on POKEY it is the 3-bit distortion, because that
    chip packs distortion and volume into one register and splitting them is
    what makes a row readable.

    `warn` is called when a note is asked for that the chip cannot get within
    half a semitone of.
    """
    t = text.strip()
    if not t or t in (".", "...", "---"):
        return None
    if t.lower() in ("off", "===", "x"):
        return (0, 0, 0)
    parts = t.split()
    if len(parts) != 3:
        raise ValueError("a cell is '%s PITCH VOL', or '...' to hold, or "
                         "'off' -- got %r"
                         % ("DIST" if is_pokey(chip) else "AUDC", text))
    try:
        a = int(parts[0], 16)
    except ValueError:
        raise ValueError("%s is a hex digit, got %r"
                         % ("distortion" if is_pokey(chip) else "AUDC", parts[0]))
    top = 7 if is_pokey(chip) else 15
    if not 0 <= a <= top:
        raise ValueError("%s is 0-%X, got %r"
                         % ("distortion" if is_pokey(chip) else "AUDC",
                            top, parts[0]))
    fmax = 255 if is_pokey(chip) else AUDF_MAX
    pitch = parts[1]
    if pitch.startswith("$"):
        f = int(pitch[1:], 16)
    elif pitch.isdigit():
        f = int(pitch)
    else:
        midi = parse_note(pitch)
        if midi is None:
            raise ValueError("not a note or a divider value: %r" % pitch)
        if is_pokey(chip):
            f, cents = pokey_best_audf(ch, midi, audctl, region)
        else:
            f, cents = best_audf(a, midi, region)
        if f is None:
            raise ValueError("AUDC $%X is silent, so %r has no pitch" % (a, pitch))
        if warn and abs(cents) > OUT_OF_TUNE:
            hz = (pokey_frequency(ch, f, audctl, region) if is_pokey(chip)
                  else frequency(a, f, region))
            got, _ = name_of(hz)
            warn("%s is %+.0f cents out -- the nearest this chip gets is "
                 "$%02X, which reads as %s" % (pitch, cents, f, got))
    if not 0 <= f <= fmax:
        raise ValueError("the divider is 0-%d on this chip, got %d" % (fmax, f))
    try:
        v = int(parts[2], 16)
    except ValueError:
        raise ValueError("volume is a hex digit 0-F, got %r" % parts[2])
    if not 0 <= v <= 15:
        raise ValueError("volume is 0-F, got %r" % parts[2])
    return (a, f, v)


def format_cell(cell, region, notes=True, chip="tia", ch=0, audctl=0):
    """A cell as text.

    Only a wholly cleared cell becomes `off`. A voice can sit at volume zero
    with a waveform and a pitch already loaded -- players routinely set those
    a frame early and then ramp the volume -- and collapsing that to `off`
    would throw the registers away, so the next row would have to restate them
    and editing the volume up would give silence instead of the note.
    """
    if cell is None:
        return "..."
    a, f, v = cell
    if cell == (0, 0, 0):
        return "off"
    if is_pokey(chip):
        tone = POKEY_DISTORTION[a & 7][2] is None
        hz = pokey_frequency(ch, f, audctl, region)
    else:
        tone = is_tone(a)
        hz = frequency(a, f, region)
    pitch = "$%02X" % f
    if notes and tone and hz:
        nm, cents = name_of(hz)
        # Only write the note name if reading it back gives this exact
        # divider. POKEY has 256 of them and several land on the same note,
        # so a name can be perfectly readable and still not identify the
        # setting -- which would quietly change the tune on the next load.
        midi = parse_note(nm) if abs(cents) <= 50 else None
        if midi is not None:
            back = (pokey_best_audf(ch, midi, audctl, region)[0]
                    if is_pokey(chip) else best_audf(a, midi, region)[0])
            if back == f:
                pitch = nm
    return "%X %-4s %X" % (a, pitch, v)


def load(path, warn=None):
    """Parse a tracker file.

    Out-of-tune notes are reported through `warn` rather than silently rounded
    to whatever the chip can manage. AUDCTL settings this tier does not model
    are refused outright -- rendering them as though the feature were absent
    would be a quietly different tune.
    """
    song = Song()
    def note_warn(msg):
        song.warnings.append(msg)
        if warn:
            warn(msg)

    audctl = (0,)
    with open(path, encoding="utf-8") as f:
        for lineno, raw in enumerate(f, 1):
            line = raw.split(";")[0].rstrip()
            if not line.strip():
                continue
            head = line.strip().split(None, 1)
            key = head[0].lower()
            if key in ("title", "region", "rate", "chip", "audctl") and len(head) > 1:
                val = head[1].strip()
                if key == "title":
                    song.title = val
                elif key == "chip":
                    if val.lower() not in CHANNELS:
                        raise ValueError("line %d: chip is tia or pokey" % lineno)
                    if song.rows:
                        raise ValueError("line %d: the chip has to be declared "
                                         "before the first row" % lineno)
                    song.chip = val.lower()
                elif key == "audctl":
                    if not POKEYS.get(song.chip):
                        raise ValueError("line %d: AUDCTL is a POKEY register; "
                                         "declare `chip pokey` first" % lineno)
                    # One value per chip. A cartridge with two POKEYs writes
                    # each its own AUDCTL, and they are rarely the same.
                    parts = [int(v.lstrip("$"), 16) for v in val.split()]
                    if len(parts) > song.nchips:
                        raise ValueError(
                            "line %d: %d AUDCTL values but `chip %s` has %d "
                            "POKEY%s" % (lineno, len(parts), song.chip,
                                         song.nchips,
                                         "" if song.nchips == 1 else "s"))
                    while len(parts) < song.nchips:
                        parts.append(parts[-1] if parts else 0)
                    audctl = tuple(parts)
                    # Noted, not refused. A captured file is a faithful record
                    # even where this tier cannot play it back, and refusing to
                    # *read* it would make the capture useless for reading and
                    # editing too. render() is where it stops, off the AUDCTL
                    # values the song ends up carrying.
                elif key == "region":
                    if val.lower() not in CLOCK:
                        raise ValueError("line %d: region is ntsc or pal" % lineno)
                    song.region = val.lower()
                    song.rate = FRAME[song.region]
                else:
                    song.rate = float(val)
                continue
            cells = [c for c in line.split("|")]
            if len(cells) != song.nch:
                raise ValueError("line %d: a %s row has %d channels separated "
                                 "by '|' -- got %d"
                                 % (lineno, song.chip, song.nch, len(cells)))
            try:
                song.add([parse_cell(c, song.region, note_warn, song.chip,
                                     i % 4,
                                     audctl[min(i // 4, len(audctl) - 1)])
                          for i, c in enumerate(cells)], audctl=audctl)
            except ValueError as e:
                raise ValueError("line %d: %s" % (lineno, e))
    return song


def dump(song, notes=True):
    out = []
    if song.title:
        out.append("title %s" % song.title)
    out.append("chip %s" % song.chip)
    out.append("region %s" % song.region)
    out.append("rate %g" % song.rate)
    out.append(";  " + " | ".join("ch%d" % (i + 1) for i in range(song.nch)))
    last_ctl = None
    for i, cells in enumerate(song.rows):
        ctls = song.all_ctls(i)
        if song.nchips and ctls != last_ctl:
            out.append("audctl " + " ".join("$%02X" % c for c in ctls))
            last_ctl = ctls
        out.append(" | ".join(
            "%-12s" % format_cell(c, song.region, notes, song.chip, n % 4,
                                  song.ctl_of(i, n))
            for n, c in enumerate(cells)).rstrip())
    return "\n".join(out) + "\n"


# ----------------------------------------------------------------- rendering
def render(song, path, gain=0.6):
    """Every channel to a 16-bit mono WAV.

    Refuses a song that needs a feature this tier does not model. Playing it
    with the feature simply absent would not be the same tune, and a WAV that
    is quietly wrong is worse than one that was never written.
    """
    if song.unsupported:
        raise ValueError(
            "this song needs POKEY features this model does not cover:\n"
            + "".join("    %-52s (AUDCTL $%02X)\n" % (n, c)
                      for n, c in song.unsupported)
            + "  Modelled: four channels, all eight distortions, the clock\n"
              "  selects, both 16-bit pairs and both high-pass filters.\n"
              "  Rendering without the rest would not be the same tune, so\n"
              "  it is refused rather than faked.")
    per = int(SAMPLE_RATE / song.rate)
    state = [None] * song.nch
    phase = [0.0] * song.nch
    frames = bytearray()
    for i, row in enumerate(song.states()):
        mix = [0.0] * per
        bufs, edges = [None] * song.nch, [None] * song.nch
        silent = set()

        if song.nchips:
            # Each POKEY is its own machine: AUDCTL, the 16-bit pairs and the
            # high-pass filters are all chip-local, so a two-chip cartridge is
            # rendered as two independent groups of four and mixed at the end.
            # Pairing channel 3 of one chip with channel 0 of the next would be
            # wrong in a way that still sounds like music.
            for chip in range(song.nchips):
                first = chip * 4
                chans = [c for c in range(first, min(first + 4, song.nch))]
                if not chans:
                    continue
                ctl = song.ctl_of(i, first)
                audfs = [row[c][1] for c in chans]
                joined = pokey_joined(ctl)
                for local, ch in enumerate(chans):
                    a, f, v = row[ch]
                    audc = song.audc_byte((a, f, v))
                    rate = pokey_rate(local, audfs, ctl, song.region)
                    pstep = pokey_poly_step(local, audfs, ctl, song.region)
                    bufs[ch], state[ch], edges[ch] = pokey_channel(
                        local, audc, f, ctl, per, state[ch], song.region,
                        rate, pstep)
                # The low half of a 16-bit pair is a divider, not a voice.
                for lo in joined.values():
                    silent.add(first + lo)

                # The high-pass is a flip-flop: the filtered channel's output
                # is sampled on every clock of its partner and XORed with the
                # live output. Equal signals cancel, so what survives is the
                # part that changed faster than the partner's clock.
                for bit, (lo, hi) in HPF_PAIRS.items():
                    if not (ctl & bit):
                        continue
                    lo, hi = first + lo, first + hi
                    if hi >= song.nch or bufs[lo] is None or edges[hi] is None:
                        continue
                    ff = state[lo].get("ff", 1) if state[lo] else 1
                    src, clk = bufs[lo], edges[hi]
                    filt = []
                    for k in range(per):
                        if clk[k]:
                            ff = 1 if src[k] > 0 else 0
                        live = 1 if src[k] > 0 else 0
                        filt.append(1.0 if (live ^ ff) else -1.0)
                    bufs[lo] = filt
                    if state[lo] is not None:
                        state[lo]["ff"] = ff
        else:
            for ch in range(song.nch):
                a, f, _v = row[ch]
                bufs[ch], phase[ch] = channel(a, f, per, phase[ch],
                                              song.region)

        for ch in range(song.nch):
            _a, _f, v = row[ch]
            if v and ch not in silent and bufs[ch] is not None:
                lvl = v / 15.0
                buf = bufs[ch]
                for k, sm in enumerate(buf):
                    mix[k] += sm * lvl
        # Divide by the channel count, not by two, or a four-voice song clips.
        for sm in mix:
            val = int(max(-1.0, min(1.0, sm * gain / song.nch)) * 32767)
            frames += struct.pack("<h", val)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(bytes(frames))
    return {"path": path, "rows": len(song),
            "seconds": len(song) / song.rate, "bytes": len(frames)}


# ------------------------------------------------- capture from a running game
def read_capture(path, region="ntsc", fold=True, chip=None):
    """Turn `probes/audio.lua` output into a Song.

    The log is one line per frame in which anything changed. A TIA log is

        frame  c0 f0 v0  c1 f1 v1

    and a POKEY log carries four channels and AUDCTL

        frame  f1 c1  f2 c2  f3 c3  f4 c4  ctl

    A cartridge with two POKEYs carries both, the second chip's four channels
    followed by its own AUDCTL:

        frame  f1 c1 .. f4 c4  ctl   f5 c5 .. f8 c8  ctl2

    with a `# chip pokey` or `# chip pokey2` header line saying which. Frames
    where nothing changed are not logged, so gaps are held -- which is what a
    tracker row of `...` means, and why the formats line up without
    interpretation.
    """
    lines = []
    declared = None
    with open(path, encoding="utf-8") as f:
        for raw in f:
            t = raw.strip()
            if t.startswith("#") and "chip" in t:
                # "# chip pokey  base $0450" -- take the name, not the rest
                rest = t.split("chip", 1)[1].split()
                if rest and rest[0].lower() in CHANNELS:
                    declared = rest[0].lower()
            if not t or t.startswith(("#", ";")):
                continue
            lines.append(t)
    chip = chip or declared or "tia"
    song = Song(title=os.path.basename(path), region=region, chip=chip)
    nch = song.nch
    chips = song.nchips
    want = 1 + nch * 2 + (chips if chips else nch)
    last = [None] * nch
    prev = None
    ctl = (0,) * max(1, chips)
    for t in lines:
        parts = t.replace(",", " ").split()
        if len(parts) < want:
            continue
        try:
            fr = int(parts[0], 0)
            vals = [int(x, 16) for x in parts[1:want]]
        except ValueError:
            continue
        if prev is not None and fold:
            for _ in range(max(0, fr - prev - 1)):
                song.add([None] * nch, audctl=ctl)
        cells = []
        if chips:
            # Each chip is nine values: four (AUDF, AUDC) pairs then its AUDCTL.
            ctls = []
            for chip_i in range(chips):
                off = chip_i * 9
                ctls.append(vals[off + 8] & 0xFF)
                for local in range(4):
                    ch = chip_i * 4 + local
                    audf = vals[off + local * 2] & 0xFF
                    audc = vals[off + local * 2 + 1] & 0xFF
                    c = ((audc >> 5) & 7, audf, audc & 0x0F)
                    cells.append(None if c == last[ch] else c)
                    last[ch] = c
            ctl = tuple(ctls)
        else:
            for ch in range(2):
                c = (vals[ch * 3] & 0x0F, vals[ch * 3 + 1] & AUDF_MAX,
                     vals[ch * 3 + 2] & 0x0F)
                cells.append(None if c == last[ch] else c)
                last[ch] = c
        song.add(cells, audctl=ctl)
        prev = fr
    return song


# --------------------------------------------------------------- 6502 export
TIA_PLAYER = """; --- a minimal TIA player ------------------------------------------------
; One row per frame. Call PlayTick once from your vertical-blank code.
; Six bytes a row, already masked to what the registers keep.
; A $FF in the first AUDC slot ends the song.

AUDC0 = $15
AUDC1 = $16
AUDF0 = $17
AUDF1 = $18
AUDV0 = $19
AUDV1 = $1A

PlayTick:
        LDY song_row
        LDA SongData,Y
        CMP #$FF
        BEQ SongDone
        STA AUDC0
        LDA SongData+1,Y
        STA AUDF0
        LDA SongData+2,Y
        STA AUDV0
        LDA SongData+3,Y
        STA AUDC1
        LDA SongData+4,Y
        STA AUDF1
        LDA SongData+5,Y
        STA AUDV1
        TYA
        CLC
        ADC #6
        STA song_row
        RTS
SongDone:
        LDA #0
        STA AUDV0
        STA AUDV1
        RTS
"""

POKEY_PLAYER = """; --- a minimal POKEY player ----------------------------------------------
; One row per frame. Call PlayTick once from your vertical-blank code.
; Nine bytes a row: AUDF/AUDC for each of the four channels, then AUDCTL.
; A $FF in the AUDCTL slot ends the song.
;
; POKEY_BASE has to match the cartridge header: $4000, $0450 or $0800.
; Write SKCTL once at startup to take the chip out of reset.

POKEY_BASE = $4000
AUDF1  = POKEY_BASE+0
AUDC1  = POKEY_BASE+1
AUDF2  = POKEY_BASE+2
AUDC2  = POKEY_BASE+3
AUDF3  = POKEY_BASE+4
AUDC3  = POKEY_BASE+5
AUDF4  = POKEY_BASE+6
AUDC4  = POKEY_BASE+7
AUDCTL = POKEY_BASE+8
SKCTL  = POKEY_BASE+15

PlayInit:
        LDA #$03
        STA SKCTL                     ; release POKEY from reset
        RTS

PlayTick:
        LDY song_row
        LDA SongData+8,Y
        CMP #$FF
        BEQ SongDone
        STA AUDCTL
        LDA SongData,Y
        STA AUDF1
        LDA SongData+1,Y
        STA AUDC1
        LDA SongData+2,Y
        STA AUDF2
        LDA SongData+3,Y
        STA AUDC2
        LDA SongData+4,Y
        STA AUDF3
        LDA SongData+5,Y
        STA AUDC3
        LDA SongData+6,Y
        STA AUDF4
        LDA SongData+7,Y
        STA AUDC4
        TYA
        CLC
        ADC #9
        STA song_row
        RTS
SongDone:
        LDA #0
        STA AUDC1
        STA AUDC2
        STA AUDC3
        STA AUDC4
        RTS
"""


def export_asm(song, label="SongData"):
    """The song as 6502 data plus a player for its chip.

    A row is emitted whole even where it only holds -- a fixed stride is
    simpler to play than any packing, and at 60 rows a second a short cue is a
    few hundred bytes. Pack it later if the space matters.
    """
    if song.nchips > 1:
        # The shipped player writes one chip. Emitting it for an eight-voice
        # song would assemble, run, and play half the music at the wrong
        # settings -- so it is refused until there is a player that drives both.
        raise ValueError(
            "this song is for two POKEYs and the exporter only has a "
            "single-chip player.\n"
            "  Rendering and editing work; 6502 export does not yet. "
            "Splitting it into two\n"
            "  four-voice songs and exporting each with its own "
            "POKEY_BASE is the way round it.")
    stride = 9 if is_pokey(song.chip) else 6
    lines = ["; %s -- %d rows at %g/s (%s, %s), %d bytes"
             % (song.title or "song", len(song), song.rate,
                song.chip.upper(), song.region.upper(),
                len(song) * stride + 1),
             POKEY_PLAYER if is_pokey(song.chip) else TIA_PLAYER,
             "%s:" % label]
    for i, row in enumerate(song.states()):
        if is_pokey(song.chip):
            ctl = song.ctl_of(i, 0)
            b = []
            for ch in range(4):
                a, f, v = row[ch]
                b += ["$%02X" % (f & 0xFF), "$%02X" % song.audc_byte((a, f, v))]
            b.append("$%02X" % (ctl & 0xFF))
            lines.append("        .byte " + ",".join(b))
        else:
            out = []
            for ch in range(2):
                a, f, v = row[ch]
                out += ["$%02X" % (a & 0x0F), "$%02X" % (f & AUDF_MAX),
                        "$%02X" % (v & 0x0F)]
            lines.append("        .byte " + ",".join(out))
    lines.append("        .byte $FF                     ; end")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------- main
def report(song):
    for w in getattr(song, "warnings", []):
        sys.stderr.write("  out of tune: %s\n" % w)


def cmd_notes(args):
    if is_pokey(args.chip):
        print("POKEY pitches, %s. Eight bits of divider instead of the TIA's\n"
              "five, so almost everything lands in tune -- 'cents' is the\n"
              "distance from equal temperament." % args.region.upper())
        ctl = args.audctl
        bad = pokey_unsupported(ctl)
        if bad:
            print("\n  AUDCTL $%02X asks for %s, which this tier does not model."
                  % (ctl, " and ".join(bad)))
            return 2
        for ch in range(4):
            clk, off = pokey_clock(ch, ctl, args.region)
            lo = pokey_frequency(ch, 255, ctl, args.region)
            hi = pokey_frequency(ch, 0, ctl, args.region)
            good = 0
            for f in range(256):
                _, c = name_of(pokey_frequency(ch, f, ctl, args.region))
                if abs(c) <= args.cents:
                    good += 1
            print("\nchannel %d   clock %8.0f Hz   %7.1f-%9.1f Hz   "
                  "%d of 256 within %g cents"
                  % (ch + 1, clk, lo, hi, good, args.cents))
            if args.all:
                for f in range(256):
                    hz = pokey_frequency(ch, f, ctl, args.region)
                    nm, c = name_of(hz)
                    mark = "  " if abs(c) <= args.cents else " ~"
                    print("   %s AUDF %3d  %9.2f Hz  %-4s %+6.1f cents"
                          % (mark, f, hz, nm, c))
        print("\ndistortions (AUDC bits 5-7):")
        for d in range(8):
            print("   %d  %s" % (d, POKEY_DISTORTION[d][0]))
        return 0

    rows = note_table(args.region)
    print("TIA pitches, %s (clock %g Hz). 'cents' is the distance from equal\n"
          "temperament -- anything past 50 is nearer its neighbour than itself."
          % (args.region.upper(), CLOCK[args.region]))
    for audc in TONE_MODES:
        sub = [r for r in rows if r["audc"] == audc]
        good = sum(1 for r in sub if abs(r["cents"]) <= args.cents)
        print("\nAUDC $%X  %-16s %5.0f-%5.0f Hz   %d of 32 within %g cents"
              % (audc, AUDC_NAME[audc], sub[-1]["hz"], sub[0]["hz"],
                 good, args.cents))
        if args.all or args.audc == audc:
            for r in sub:
                mark = "  " if abs(r["cents"]) <= args.cents else " ~"
                print("   %s AUDF %2d  %8.2f Hz  %-4s %+6.1f cents"
                      % (mark, r["audf"], r["hz"], r["note"], r["cents"]))
    return 0


def cmd_render(args):
    song = load(args.song)
    report(song)
    out = args.out or os.path.splitext(args.song)[0] + ".wav"
    info = render(song, out, args.gain)
    print("%s -- %d rows, %.2f s, %s %s"
          % (out, info["rows"], info["seconds"], song.chip.upper(),
             song.region.upper()))
    return 0


def midi_voice(notes, rows, rate, pick="high"):
    """One MIDI track laid onto `rows` frames: the note sounding at each frame.

    A 7800 voice is monophonic and a MIDI track usually is not, so something has
    to give. `pick` decides which note wins where several overlap; whatever it
    picks, the ones it drops are counted and reported, because "the chords
    quietly vanished" is not something to find out by ear later.
    """
    out = [None] * rows
    dropped = 0
    for i in range(rows):
        t0 = i / rate
        t1 = (i + 1) / rate
        live = [n for n in notes if n["start"] < t1 and n["end"] > t0]
        if not live:
            continue
        if len(live) > 1:
            dropped += len(live) - 1
        if pick == "low":
            out[i] = min(live, key=lambda n: n["note"])
        elif pick == "first":
            out[i] = min(live, key=lambda n: n["start"])
        else:
            out[i] = max(live, key=lambda n: n["note"])
    return out, dropped


def cmd_import(args):
    """A MIDI file onto a chip that has far fewer notes than MIDI assumes."""
    import midi as midi_mod
    try:
        doc = midi_mod.read(args.file)
    except (midi_mod.MidiError, IOError) as e:
        sys.stderr.write("%s\n" % e)
        return 2

    playable = [i for i, t in enumerate(doc["tracks"]) if t["notes"]]
    if not playable:
        sys.stderr.write("no notes in %s\n" % os.path.basename(args.file))
        return 2
    chosen = args.track or playable[:CHANNELS[args.chip]]
    for t in chosen:
        if not 0 <= t < len(doc["tracks"]):
            sys.stderr.write("no track %d; the file has %d\n"
                             % (t, len(doc["tracks"])))
            return 2
    nch = CHANNELS[args.chip]
    if len(chosen) > nch:
        sys.stderr.write(
            "%d tracks named but %s has %d voices. Pick which ones matter -- "
            "there is no arrangement this tool can invent for you.\n"
            % (len(chosen), args.chip, nch))
        return 2

    song = Song(title=os.path.basename(args.file), region=args.region,
                chip=args.chip)
    rate = song.rate
    end = max(n["end"] for t in chosen for n in doc["tracks"][t]["notes"])
    rows = int(end * rate) + 1
    if args.rows:
        rows = min(rows, args.rows)

    lanes, drops, stats = [], [], []
    for t in chosen:
        lane, dropped = midi_voice(doc["tracks"][t]["notes"], rows, rate,
                                   args.pick)
        lanes.append(lane)
        drops.append(dropped)

    if args.audc is None:
        # a tone you can write a melody with, on each chip
        args.audc = 5 if is_pokey(args.chip) else 0x0C
    vol = max(0, min(15, args.volume))
    for i in range(rows):
        cells = []
        for ch in range(nch):
            n = lanes[ch][i] if ch < len(lanes) else None
            if n is None:
                cells.append((0, 0, 0))
                continue
            if is_pokey(args.chip):
                f, cents = pokey_best_audf(ch % 4, n["note"], 0, args.region)
                cells.append((args.audc, f, vol))
            else:
                f, cents = best_audf(args.audc, n["note"], args.region)
                cells.append((args.audc, f, vol))
            stats.append((abs(cents), n["note"]))
        song.add(cells)

    # Fold repeats so the file shows changes rather than a wall of identical
    # rows -- the same thing `capture` does, for the same reason.
    folded = Song(title=song.title, region=song.region, chip=song.chip)
    last = [None] * nch
    for cells in song.rows:
        row = []
        for ch in range(nch):
            row.append(None if cells[ch] == last[ch] else cells[ch])
            last[ch] = cells[ch]
        folded.add(row)

    out = args.out or (os.path.splitext(args.file)[0] + ".trk")
    with open(out, "w", encoding="utf-8") as f:
        f.write(dump(folded))

    placed = len(stats)
    bad = [c for c, _n in stats if c > args.cents]
    print("%s -- %d rows, %.1f s, %s %s"
          % (out, len(folded), len(folded) / rate, args.chip.upper(),
             args.region.upper()))
    for k, t in enumerate(chosen):
        name = doc["tracks"][t]["name"] or "(unnamed)"
        print("  voice %d  <- track %d %-20s %d notes%s"
              % (k + 1, t, name[:20], len(doc["tracks"][t]["notes"]),
                 "" if not drops[k] else
                 ", %d overlapping dropped" % drops[k]))
    if placed:
        cents = sorted(c for c, _n in stats)
        med = cents[len(cents) // 2]
        print("  tuning   %d frames placed; median error %.0f cents, worst %.0f"
              % (placed, med, cents[-1]))
        if bad:
            print("           %d of them (%.0f%%) are more than %.0f cents out"
                  % (len(bad), 100.0 * len(bad) / placed, args.cents))
    if args.chip == "tia":
        print("  NOTE     the TIA is not tuned to anything. Most notes simply do")
        print("           not exist on it -- `tracker.py notes` lists the ones")
        print("           that do. A melody written for it usually has to be")
        print("           rewritten around them rather than transcribed onto it.")
    total_drop = sum(drops)
    if total_drop:
        print("  NOTE     %d overlapping notes were dropped: a voice plays one"
              % total_drop)
        print("           note at a time. --pick low|first|high chooses which.")
    return 0


def cmd_capture(args):
    song = read_capture(args.log, args.region, chip=args.chip)
    voiced = sum(1 for row in song.rows if any(c for c in row))
    if voiced <= 2:
        sys.stderr.write(
            "  this log has %d change%s in %d frames -- nothing to hear.\n"
            "  Either the game never left its attract screen (drive it, or\n"
            "  capture against a -playback recording), or you captured the\n"
            "  wrong chip: check `python tools/cart.py <rom>` for a POKEY\n"
            "  line and pass --chip pokey.\n"
            % (voiced, "" if voiced == 1 else "s", len(song)))
    # Say up front if the game leans on anything this tier does not model,
    # rather than letting the file look fine until it is rendered.
    if is_pokey(song.chip) and song.audctl:
        seen = {}
        for ctl in set(song.audctl):
            for name in pokey_unsupported(ctl):
                seen.setdefault(name, ctl)
        if seen:
            sys.stderr.write("  this game uses POKEY features this model "
                             "does not cover:\n")
            for name, ctl in sorted(seen.items()):
                sys.stderr.write("    %-52s (AUDCTL $%02X)\n" % (name, ctl))
            sys.stderr.write("  The capture is still faithful and worth "
                             "reading -- but `render` will\n  refuse it "
                             "rather than play a different tune.\n")
    text = dump(song, notes=not args.raw)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
        print("%s -- %s, %d rows, %.2f s" % (args.out, song.chip,
                                             len(song), len(song) / song.rate))
    else:
        sys.stdout.write(text)
    return 0


def cmd_export(args):
    song = load(args.song)
    report(song)
    text = export_asm(song, args.label)
    stride = 9 if is_pokey(song.chip) else 6
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
        print("%s -- %d rows, %d bytes of data" % (args.out, len(song),
                                                   len(song) * stride + 1))
    else:
        sys.stdout.write(text)
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")

    n = sub.add_parser("notes", help="what pitches a chip can play")
    n.add_argument("--chip", default="tia", choices=sorted(CHANNELS))
    n.add_argument("--audctl", type=lambda x: int(x, 16), default=0,
                   help="POKEY only: the AUDCTL in force")
    n.add_argument("--region", default="ntsc", choices=sorted(CLOCK))
    n.add_argument("--cents", type=float, default=25.0)
    n.add_argument("--audc", type=lambda x: int(x, 16), default=None,
                   help="list every AUDF for one waveform")
    n.add_argument("--all", action="store_true")
    n.set_defaults(func=cmd_notes)

    m = sub.add_parser("import", help="a MIDI file onto the chip's voices")
    m.add_argument("file", help="a .mid")
    m.add_argument("-o", "--out")
    m.add_argument("--chip", default="tia", choices=sorted(CHANNELS))
    m.add_argument("--region", default="ntsc", choices=sorted(CLOCK))
    m.add_argument("--track", type=int, action="append",
                   help="a MIDI track to take, once per voice, in order. "
                        "Default: the first tracks that have notes.")
    m.add_argument("--audc", type=lambda x: int(x, 16), default=None,
                   help="waveform. TIA default $C (tone, div 6); POKEY 5 "
                        "(pure tone)")
    m.add_argument("--volume", type=int, default=8)
    m.add_argument("--pick", default="high", choices=["high", "low", "first"],
                   help="which note wins where a track plays several at once")
    m.add_argument("--cents", type=float, default=25.0,
                   help="how far out of tune counts as out of tune")
    m.add_argument("--rows", type=int, help="stop after this many frames")
    m.set_defaults(func=cmd_import)

    r = sub.add_parser("render", help="song to WAV")
    r.add_argument("song")
    r.add_argument("-o", "--out")
    r.add_argument("--gain", type=float, default=0.6)
    r.set_defaults(func=cmd_render)

    c = sub.add_parser("capture", help="MAME audio log to a song")
    c.add_argument("log")
    c.add_argument("-o", "--out")
    c.add_argument("--region", default="ntsc", choices=sorted(CLOCK))
    c.add_argument("--raw", action="store_true", help="divider values, not notes")
    c.add_argument("--chip", default=None, choices=sorted(CHANNELS),
                   help="override what the log's header says")
    c.set_defaults(func=cmd_capture)

    e = sub.add_parser("export", help="song to 6502 data and a player")
    e.add_argument("song")
    e.add_argument("-o", "--out")
    e.add_argument("--label", default="SongData")
    e.set_defaults(func=cmd_export)

    args = ap.parse_args()
    if not args.cmd:
        ap.print_help()
        return 1
    try:
        return args.func(args)
    except (ValueError, OSError) as e:
        # A malformed song or a missing file is the user's mistake, not a bug;
        # a traceback here buries the one line that says what to fix.
        sys.stderr.write("%s\n" % e)
        return 2


if __name__ == "__main__":
    sys.exit(main())
