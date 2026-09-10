#!/usr/bin/env python3
"""
Karateka fixes: build a patched cartridge and a headerless BPS for each.

    python patches/karateka.py --list
    python patches/karateka.py --build          (all of them)
    python patches/karateka.py --build 1        (just one)

Each fix is independent and each is checked against the bytes it expects to
find, so building against the wrong dump fails by name instead of producing a
corrupt ROM.

## Why the patches are headerless

A `.a78` carries a 128-byte header describing the mapper, and the same
cartridge circulates with different headers -- fixed over the years as
mappings were corrected. A patch built against the header applies to one dump
and refuses every other copy of the same game.

So the BPS is built against the ROM *without* it. That is what the romhacking
tools expect, and it means these apply to any dump of Karateka regardless of
which header it carries. A playable `.a78` is written alongside for testing.

## What is here

Fix 1 is worth having. The two after it are documented, measured, and honestly
marginal -- they are here because the arithmetic is more convincing when you
can run it than when you read it.
"""
import argparse
import io
import os
import hashlib
import subprocess
import sys
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "tools"))

HDR = 128
# Where to find the cartridge. Nothing here ships a ROM: supply your own
# dump of the NTSC release, crc32 FEC21472, and point KARATEKA_ROM at it or
# drop it beside the toolkit. The first of these that exists wins.
ROM_NAME = "Karateka (NTSC) (Atari) (1987) (FEC21472).a78"
SOURCES = [
    os.environ.get("KARATEKA_ROM", ""),
    os.path.join(ROOT, "..", "karateka", ROM_NAME),
    os.path.join(ROOT, ROM_NAME),
    os.path.expanduser(os.path.join("~", "Documents", "Atari 7800",
                                    "Rom Library", ROM_NAME)),
]
OUTDIR = os.environ.get(
    "KARATEKA_OUT", os.path.join(ROOT, "..", "karateka", "patches"))


# Every build gets a fresh cartridge signature before it is written. An NTSC
# 7800 hashes the cartridge and checks a signature over that hash at
# $FF80-$FFF7; if it does not match, the console starts up in 2600 mode
# instead of refusing, which on real hardware looks like a black screen and
# not like an error. Karateka's $FFF9 is $47, so the hashed range is the
# whole 48K from $4000 -- every byte any fix here writes is inside it.
#
# PAL consoles have no crypto check and no emulator verifies anything, which
# is why this went unnoticed through forty-odd builds. See tools/sign7800.py.
#
# Signing happens on the finished image, after the Patcher is done, so the
# 120 signature bytes are not in `p.writes` and --check keeps comparing only
# what the fixes themselves wrote.
sys.path.insert(0, os.path.join(ROOT, "tools"))
import sign7800


def _sign(rom, what):
    """Sign a finished image, or say clearly why it could not be."""
    try:
        return sign7800.signed(rom)
    except sign7800.SignError as e:
        raise SystemExit("could not sign %s: %s" % (what, e))


class Patcher(object):
    """Byte edits against a headerless ROM, each checked before it is made."""

    def __init__(self, rom, base=0x4000):
        self.rom = bytearray(rom)
        self.base = base
        # what this patcher wrote, so a fix can be compared with another
        # without building a cartridge to diff
        self.writes = []

    def off(self, addr):
        return addr - self.base

    def peek(self, addr, n):
        o = self.off(addr)
        return bytes(self.rom[o:o + n])

    def put(self, addr, data, expect=None):
        o = self.off(addr)
        if expect is not None:
            found = bytes(self.rom[o:o + len(expect)])
            if found != bytes(expect):
                raise SystemExit(
                    "at $%04X expected %s but found %s -- this is not the ROM "
                    "this patch was written for"
                    % (addr, bytes(expect).hex(), found.hex()))
        self.rom[o:o + len(data)] = bytes(data)
        self.writes.append((addr, bytes(data)))
        return self


# --------------------------------------------------------------------- fix 1
SWCHA_LATCH, INPT0_LATCH, INPT1_LATCH = 0xCF, 0xD0, 0xD1
# $FF80 and $FFA0 were the original homes for these two routines, and both were
# wrong. Scanning the ROM for runs of zero bytes -- which the patch-set floats
# do as a matter of course -- says $FF80-$FFF9 is not free: it holds
# high-entropy data sitting immediately below the 6502 vectors, which on a 7800
# is where the BIOS signature block lives. Emulators do not verify it, so every
# build so far ran, and nothing about that makes overwriting 45 bytes of it a
# good idea.
#
# $FF40 is 64 bytes of genuine zeroes. Both routines fit with room over.
LATCH, WRAP = 0xFF40, 0xFF60



# The cutscenes ask "is anything pressed?" and skip if so. p_8A1C answers it
# from INPT0, INPT1 and then $A2/$A3 -- the *decoded* direction. That is live
# in the stock game, because the reader samples SWCHA on the spot.
#
# It stops being live the moment the controls are latched. $A2/$A3 then mean
# "a direction was pressed at some point since the last read", which is what
# the latch is for and exactly the wrong answer to this question: a tap during
# a fight survives into the next cutscene and skips it. Reported twice, and
# `karateka-dose-0` ruled out the loop cadence.
#
# The replacement reads SWCHA directly for the two direction tests. Bits 6-7
# are player one's horizontal and 4-5 the vertical, active low, the same split
# the decoder at $5A07 makes, and the count it pushes is unchanged.
#
# It belongs in fix 1 because fix 1 causes it, and it is harmless without the
# latch: reading SWCHA live is what the stock word effectively did.
ANYINPUT = 0xA890
ANYINPUT_CELLS = [0x8A42, 0x8A50]      # w_8A3E and w_8A4C, the two cutscene polls
P_ANYINPUT = 0x8A1C

ANYINPUT_CODE = [
    0xCA, 0x94, 0x00, 0xCA, 0x94, 0x00, 0xA5, 0x08, 0x10, 0x01,
    0x88, 0xA5, 0x09, 0x10, 0x01, 0x88, 0xAD, 0x80, 0x02, 0x29,
    0xC0, 0xC9, 0xC0, 0xF0, 0x01, 0x88, 0xAD, 0x80, 0x02, 0x29,
    0x30, 0xC9, 0x30, 0xF0, 0x01, 0x88, 0x94, 0x00, 0x4C, 0x1E,
    0x40,
]


def fix_input_latch(p):
    """Remember a press until the game gets round to reading it.

    The game reads its controls once per trip round a loop that takes 13 to 14
    frames, so a press shorter than that can fall between two reads and be
    lost entirely: measured, a 33 ms tap is seen 16% of the time.

    The display interrupt's last phase spends 85 cycles doing nothing before it
    writes the background colour. Those cycles place a raster boundary and
    cannot be taken -- but they can be spent differently, so long as the
    replacement costs exactly 85. A JSR plus four NOPs (14) calls a sampler
    that latches the controls and pads itself back out to 71.

    Measured after: a 33 ms tap is seen 46% of the time, and the response no
    longer depends on how long you hold. When the game is listening at all,
    the latch never misses -- 97 windows out of 97.
    """
    p.put(0x5945, [0x20, LATCH & 0xFF, LATCH >> 8,      # JSR latch     6
                   0xEA, 0xEA, 0xEA, 0xEA],             # NOP x4        8
          expect=[0xA0, 0x10, 0x88, 0xD0, 0xFD, 0xEA, 0xEA])
    p.put(LATCH, [
        0xAD, 0x80, 0x02,        # LDA SWCHA   4   directions are active low,
        0x25, SWCHA_LATCH,       # AND $CF     3   so AND remembers a press
        0x85, SWCHA_LATCH,       # STA $CF     3
        0xA5, 0x08,              # LDA INPT0   3   buttons set bit 7 when held,
        0x05, INPT0_LATCH,       # ORA $D0     3   so ORA remembers those
        0x85, INPT0_LATCH,       # STA $D0     3
        0xA5, 0x09,              # LDA INPT1   3
        0x05, INPT1_LATCH,       # ORA $D1     3
        0x85, INPT1_LATCH,       # STA $D1     3     = 28
        0xA0, 0x06,              # LDY #$06    2
        0x88,                    # DEY         2  x6
        0xD0, 0xFD,              # BNE -3      3/2   = 31
        0xEA, 0xEA, 0xEA,        # NOP x3      6
        0x60,                    # RTS         6     = 71, so 14 + 71 = 85
    ])
    p.put(0x5A11, [0xA5, SWCHA_LATCH, 0xEA], expect=[0xAD, 0x80, 0x02])
    p.put(0x59EB, [0xA5, INPT0_LATCH], expect=[0xA5, 0x08])
    p.put(0x59F4, [0xA5, INPT1_LATCH], expect=[0xA5, 0x09])
    p.put(0x59FE, [0x20, WRAP & 0xFF, WRAP >> 8, 0xEA, 0xEA, 0xEA],
          expect=[0x20, 0x07, 0x5A, 0x20, 0xE9, 0x59])
    p.put(WRAP, [
        0x20, 0x07, 0x5A,        # JSR read directions (now from $CF)
        0x20, 0xE9, 0x59,        # JSR read buttons    (now from $D0/$D1)
        0xA9, 0xFF, 0x85, SWCHA_LATCH,     # clear: all bits set = nothing held
        0xA9, 0x00, 0x85, INPT0_LATCH, 0x85, INPT1_LATCH,
        0x60,
    ])
    # and give the cutscenes a live answer, since the latch stopped $A2/$A3
    # being one
    p.put(ANYINPUT,
          [(ANYINPUT + 2) & 0xFF, (ANYINPUT + 2) >> 8] + ANYINPUT_CODE,
          expect=[0x00] * (2 + len(ANYINPUT_CODE)))
    for c in ANYINPUT_CELLS:
        p.put(c, [ANYINPUT & 0xFF, ANYINPUT >> 8],
              expect=[P_ANYINPUT & 0xFF, P_ANYINPUT >> 8])
    return p


# --------------------------------------------------------------------- fix 2
# Where the game *actually* reads the buttons. Found with tools/forth.py: of
# the 271 words the thread names, five read INPT0/INPT1 straight from the TIA,
# and none read $A4 or $A7 at all. So fix 1 latched a path the game does not
# use -- the directions benefit, because those go through $A2, but every
# button test still samples the chip live and still misses a short press.
BUTTON_SITES = [
    (0x704A, 0x09), (0x7074, 0x09), (0x70B2, 0x09), (0x70CA, 0x09),
    (0x8A24, 0x08), (0x8A29, 0x09),
]
# Point them at the LATCH, not at the snapshot. Pointing them at $A4/$A7 --
# the bytes the reader fills -- looked tidier and measured 50% -> 1%, because
# the reader runs once per loop and the wrapper clears the latch right after
# it, so the snapshot is stale almost all the time. These words run far more
# often than the reader does; reading the chip live beat reading a stale copy.
# The latch is the only source that is both fresh and remembers.
SNAPSHOT = {0x08: INPT0_LATCH, 0x09: INPT1_LATCH}


def fix_button_path(p):
    """Make the button tests read the same snapshot the directions do.

    Each site becomes a read of the latch, which holds bit 7 set if the button
    has been pressed at any point since the game last read its controls. The
    tests are `BMI`/`BPL` on bit 7, which the latch answers exactly as the chip
    did, so none of the logic changes -- only the window it asks about.

    Live, the test asks "is the button down right now", and a 100 ms press is
    down for six of the thirteen frames between reads. Against the latch it
    asks "has it been pressed since the last read", which a press of any length
    inside that window satisfies.

    ## WITHDRAWN -- this makes the game worse, and the manual says why

    By that measurement it works: the button tests see a 100 ms press 76% of
    the time instead of 50%. In play it is worse than the original -- brief
    taps stop registering and you have to hold the button down.

    The measurement was of the wrong thing. Karateka distinguishes a *tap*
    from a *hold*, and the manual is explicit: "Assume fighting stance. Press
    right button" against "Advance. Hold down left button, move handle right."
    Holding is a different command, not a longer version of the same one.

    A latch cannot preserve that distinction. It turns "was pressed at some
    point recently" into "is pressed", so a tap arrives looking exactly like a
    hold and goes on looking like one for the rest of the loop. The game does
    the hold action, or nothing -- which is worse than sampling live, because
    live at least got it right half the time.

    Fix 1 is unaffected. The directions carry no tap-versus-hold meaning of
    their own; what they mean depends on what the buttons are doing, and the
    buttons are still read live there.

    Kept rather than deleted because the mistake is instructive: a proxy metric
    said this was a clear improvement, and it was measuring something real --
    just not the thing that matters.
    """
    fix_input_latch(p)
    for addr, reg in BUTTON_SITES:
        p.put(addr, [0xA5, SNAPSHOT[reg]], expect=[0xA5, reg])
    return p


# --------------------------------------------------------------------- fix 3
# What actually sets the game's pace. The main loop, w_A59C, updates nine
# entities and waits a full frame after each:
#
#     LIT $18xx / @ / EXECUTE      run this entity's action
#     vblank? / 0BRANCH -6         spin until vertical blank starts
#     vblank? / 0BRANCH -4         spin until it ends
#
# Nine updates at roughly a frame and a half each is the 13-frame loop measured
# everywhere else in this work -- and it means the interpreter's speed is
# almost irrelevant. The machine is not computing for thirteen frames, it is
# waiting for twelve of them.
#
# BRANCH adds its inline cell to the address of that cell, so skipping a
# 14-byte block is a branch of $000C from the offset cell.
WAIT_BLOCKS = [0xA5CA, 0xA5E0, 0xA5F6, 0xA60C, 0xA622, 0xA638, 0xA64E, 0xA664]
BRANCH_WORD = 0x4D56
WAIT_PATTERN = [0x6A, 0x59, 0x3C, 0x50, 0x6C, 0x4D, 0xFA, 0xFF,
                0x6A, 0x59, 0x6C, 0x4D, 0xFC, 0xFF]


def _skip_waits(p, which):
    for i in which:
        a = WAIT_BLOCKS[i]
        p.put(a, [BRANCH_WORD & 0xFF, BRANCH_WORD >> 8, 0x0C, 0x00],
              expect=WAIT_PATTERN[:4])


def fix_half_the_waits(p):
    """Wait after every second entity instead of after every one.

    This is the lever that matters, and it is a blunt one: those waits pace the
    whole game, not just its input. An entity gets a frame to itself, so
    removing waits speeds up movement and animation exactly as much as it
    speeds up the response to the stick. Take out all eight and Karateka runs
    about eight times too fast.

    Halving them is offered as something to feel rather than as a
    recommendation. To try a different dose, change the list passed to
    `_skip_waits`: `[0, 2, 4, 6]` skips four of the eight, `[0, 4]` skips two,
    and `range(8)` skips them all.

    ## Measured properly, on a session recorded per build

    The number that matters is the main loop's period, and it wants a
    *distribution* rather than a mean -- a session holds fights, menus and a
    minute-long intermission, and the mean of those describes none of them:

        original   median 13 frames   88.3% of iterations exactly 13
        this fix   median  9 frames   84.6% of iterations exactly 9

    Four waits skipped, four frames saved. **A wait is worth exactly one frame**,
    which also fixes the ceiling for this approach: skipping all eight gives a
    5-frame loop, not a zero-frame one.

    So the game thinks 1.44 times as often -- and moves 1.44 times as fast. An
    earlier claim here of "about four times faster" was wrong; it came from a
    whole-session average of control reads, a figure dominated by menus, which
    poll the stick twice a frame and are not the thing anybody is asking about.

    Dispatches a frame go 403 to 434 -- barely changed, and that is the whole
    argument: the interpreter is not doing more work, it is spending less of it
    spinning on the vertical blank.

    ## An earlier number here was wrong

    A scripted session reported this cadence falling from 13.40 frames to 4.71,
    which was worthless. Scripted input never reaches the main loop: over 2.25
    million dispatches it landed inside w_A59C twice. The patch was inert in
    the very run that appeared to demonstrate it, and what actually differed
    was which screen the two runs drifted into -- their dispatch totals differ
    by 0.1%, and four thousand frames is plenty for that to diverge.

    The lesson is cheap to state and was not cheap to learn: measure a change
    in the state the change applies to, and check that the changed code runs at
    all in the run being measured.
    """
    fix_input_latch(p)
    _skip_waits(p, [0, 2, 4, 6])
    return p


def fix_fewer_waits(p):
    """A gentler dose of fix 3: skip two of the eight waits, not four.

    Fix 3 is a big change to how the game moves, and "better" there is a matter
    of taste rather than of measurement -- faster repositioning also means a
    faster opponent. This exists so the two can be compared rather than argued
    about.
    """
    fix_input_latch(p)
    _skip_waits(p, [0, 4])
    return p


# --------------------------------------------------------------------- fix 5
# The hit test, found with tools/forth.py rather than guessed at.
#
# Ten primitives compare a limb against a body point and OR their result into
# $18C0/$18C1 -- which is the software stand-in for the collision registers a
# 7800 does not have. Five of them belong to one fighter and five to the
# other, and both groups end up in the same shape:
#
#     A = |limb - body|                  ( first axis  )
#     if A < threshold ...
#     B = |limb - body|                  ( second axis )
#     if B < threshold ...
#
# So a hit is exactly `|dx| < R and |dy| < R`: a square window of half-width R
# around the limb. There are two thresholds, one per fighter:
#
#     $18C3   compared at $6A68 and $6A88
#     $18C4   compared at $79FC and $7A1C
#
# Both are written in one place, w_A0C2, from four constants that each of the
# six encounter words pushes before calling it:
#
#     encounter   $18C3  $18C4      (two more, which are AI counters)
#     w_A1D0        8      10
#     w_A222        8      10
#     w_A274        8      10
#     w_A2C6        8      10
#     w_A318        9      10
#     w_A36A        9      10
#
# One fighter's window is 8 or 9; the other's is 10, in every
# encounter. w_A0C2 does contain an adjustment that would move them
# toward each other -- `1-` on $18C4, `1+` on $18C3, converging on 9
# and 9 -- but it never runs: the condition is `SWCHB = $80`, which
# needs Reset, Select and Pause held down together. See fix 17.
# The developers knew the two were not the same. The switch they
# wired to it has never worked.
#
# This sets the smaller one to 10 so both fighters have the same reach.
REACH_CELLS = [0xA20C, 0xA25E, 0xA2B0, 0xA302, 0xA354, 0xA3A6]
REACH_NOW = [8, 8, 8, 8, 9, 9]
REACH_PAIR = 10


def fix_even_reach(p):
    """Give both fighters the same hit window.

    Which of the two thresholds belongs to the player is an inference, not a
    measurement: the tests that read $18C3 probe from $187D and $1885 against
    $189E/$18A0/$18A2, and the ones that read $18C4 probe from $189C and $18A4
    against $187F/$1881/$1883. The first block of addresses is the one the main
    loop touches first, which is the usual place to find the player -- but it
    is the ordering of two address ranges, and nothing more.

    Setting both to 10 makes the inference not matter. If the player had the
    smaller window, this fixes it; if the opponent did, this hands the player a
    slightly tougher fight rather than a broken one. Either way the reach stops
    being asymmetric, which is the part that is hard to defend.

    Not measured yet -- it wants play-testing, not a profiler. What is measured
    is that all six encounters really do hold these constants, and the build
    refuses if any of them does not.
    """
    for addr, now in zip(REACH_CELLS, REACH_NOW):
        p.put(addr, [REACH_PAIR, 0x00], expect=[now, 0x00])
    return p


def fix_generous_reach(p):
    """Fix 5, and two units of slack on top, for both fighters.

    A separate fix rather than a knob because it is a different claim. Fix 5
    argues that a game should not give one fighter more reach than the other.
    This argues that ten units is too tight for anybody, which is a matter of
    taste and wants somebody to play it.
    """
    for addr, now in zip(REACH_CELLS, REACH_NOW):
        p.put(addr, [REACH_PAIR + 2, 0x00], expect=[now, 0x00])
    # the other fighter's threshold is a constant 10 in all six
    for addr in REACH_CELLS:
        p.put(addr + 4, [REACH_PAIR + 2, 0x00], expect=[REACH_PAIR, 0x00])
    return p



def fix_everything(p):
    """Fix 3 and fix 5 together, because they fix different things.

    Fix 3 is about when the game listens; fix 5 is about whether the swing
    counts. They do not interact -- one edits the wait blocks in the main loop,
    the other edits six constants in the encounter setups -- so there is no
    reason to choose between them, and every reason to play the combination
    rather than two halves of it.
    """
    fix_half_the_waits(p)
    fix_even_reach(p)
    return p



def fix_everything_generous(p):
    """Fix 7 with the generous window instead of the even one.

    Fix 6 on its own leaves the 13-frame loop in place, which is the wrong pair
    to try: a wider hit window matters most when you can actually get a swing
    in, and that is what fix 3 buys. So this is the combination worth playing
    against fix 7 -- same cadence, two more units of reach for both fighters --
    rather than fix 6 alone against the stock game.
    """
    fix_half_the_waits(p)
    fix_generous_reach(p)
    return p


# --------------------------------------------------------------------- fix 9
# The whole control mapping is 100 contiguous bytes at $781E, and it already
# branches on the stance byte $187C at the top. Read out, it is:
#
#     walking ($187C == 0)      button 1          -> 2  change stance
#                               right             -> 1  walk
#                               left              -> nothing
#     fighting ($187C != 0)     button 1          -> 3  change stance
#                               button 2 + left   -> 4  attack, moving left
#                               button 2 + right  -> 5  attack, moving right
#                               left              -> 6  step left
#                               right             -> 7  step right
#
# Seven commands, and `w_7898` turns each into one behaviour word which it
# stores in the player's slot $1878. There are exactly seven; nothing is
# waiting unused.
#
# Two things stand out. **The vertical stick is never read here at all** --
# `$A3` does not appear in the mapping -- so up and down are free. And in
# walking stance, left produces no command: only right starts a walk.
#
# This does three things, all of which fit in the same 34 bytes:
#
#     walking    up   changes stance   (was button 1)
#                left or right walks   (was right only)
#     fighting   down changes stance   (was button 1)
#
# Stance change moves to *opposite* directions in the two stances, which is the
# point: on a control read that fires twice, a single stance key can bounce
# straight back, and up-then-down cannot.
#
# It also leaves button 1 unused in fighting stance, which is where a
# punch/kick split would have to go. That is not done here -- see the notes in
# docs/fixing-karateka.md on why six attacks need four behaviours the game
# does not have.
MAP_AT = 0x7831
MAP_WAS = [
    0xD0, 0x17,              # 7831  BNE $784A       -> fighting stance
    0xA5, 0x08,              # 7833  LDA INPT0       walking: button 1
    0x10, 0x05,              # 7835  BPL $783C
    0xA0, 0x02,              # 7837  LDY #$02          change stance
    0x4C, 0x7B, 0x78,        # 7839  JMP $787B
    0xA5, 0xA2,              # 783C  LDA $A2         horizontal
    0xF0, 0x07,              # 783E  BEQ $7847         centred: nothing
    0x30, 0x05,              # 7840  BMI $7847         left:    nothing
    0xA0, 0x01,              # 7842  LDY #$01          right:   walk
    0x4C, 0x7B, 0x78,        # 7844  JMP $787B
    0x4C, 0x7B, 0x78,        # 7847  JMP $787B
    0xA5, 0x08,              # 784A  LDA INPT0       fighting: button 1
    0x10, 0x05,              # 784C  BPL $7853
    0xA0, 0x03,              # 784E  LDY #$03          change stance
    0x4C, 0x7B, 0x78,        # 7850  JMP $787B
]
MAP_NOW = [
    0xD0, 0x11,              # 7831  BNE $7844       -> fighting stance
    0xA5, 0xA3,              # 7833  LDA $A3         walking: vertical
    0x30, 0x08,              # 7835  BMI $783F         up: change stance
    0xA5, 0xA2,              # 7837  LDA $A2         horizontal
    0xF0, 0x06,              # 7839  BEQ $7841         centred: nothing
    0xA0, 0x01,              # 783B  LDY #$01          either way: walk
    0xD0, 0x02,              # 783D  BNE $7841         LDY #$01 leaves Z clear
    0xA0, 0x02,              # 783F  LDY #$02          change stance
    0x4C, 0x7B, 0x78,        # 7841  JMP $787B
    0xA5, 0xA3,              # 7844  LDA $A3         fighting: vertical
    0xC9, 0x01,              # 7846  CMP #$01
    0xD0, 0x09,              # 7848  BNE $7853         not down: carry on
    0xA0, 0x03,              # 784A  LDY #$03          down: change stance
    0x4C, 0x7B, 0x78,        # 784C  JMP $787B
    0xEA, 0xEA, 0xEA, 0xEA,  # 784F  spare
]


def fix_stance_on_the_stick(p):
    """Stance change on up and down; left walks as well as right.

    Untested by anything but the assembler. The rewrite is byte-exact and the
    build checks all 34 original bytes before touching them, so it is the right
    code in the right place -- but whether it *plays* better is not something
    static analysis can answer, and there is one specific way it could go
    wrong.

    The walk behaviours poll the vertical stick themselves: `p_7042` and
    `p_706C` decode up/centre/down into 1/2/3 once a walk is running. So
    vertical is unused when a command is chosen and used afterwards, and
    holding up to change stance may also mean something to whatever is running
    at the time. If changing stance starts doing an odd second thing, that is
    where it comes from, and the answer is to require the stick centred
    horizontally as well -- two more bytes, and there is room.

    Left starting a walk is the other guess. The command layer only ever
    offered `right`, which is a strange thing to write on purpose, so either
    walking left was meant to go through the fighting stance or it was an
    oversight. If the figure walks left with the wrong animation, that is the
    answer, and the fix is to give command 1 a left-facing twin.
    """
    p.put(MAP_AT, MAP_NOW, expect=MAP_WAS)
    return p


def fix_the_lot(p):
    """Fix 3, fix 6 and fix 9: cadence, reach and mapping together."""
    fix_half_the_waits(p)
    fix_generous_reach(p)
    fix_stance_on_the_stick(p)
    return p



# -------------------------------------------------------------------- fix 11
# The mapping, corrected. An earlier reading of it here was wrong in a way
# worth recording: it said the vertical stick was never read, and that six
# attacks would need four behaviour words the game does not have. Both false.
# Vertical is read one layer up, in the dispatcher w_7898, and all six attacks
# are already there:
#
#     w_7898  command 6:  install w_74BA / p_706C / $187A C!
#             command 7:  install w_7602 / p_7042 / $187A C!
#
# `p_706C` is "left hemisphere + up/centre/down -> 1/2/3" and `p_7042` is the
# same for the right. The height lands in $187A, and w_74E8 and w_7612 each
# CASE on it into three strikes -- the left family striking with limb $187D,
# the right family with limb $1885. Punch and kick, three heights each, and
# the manual says which is which: left hemisphere punches, right kicks.
#
# So what the stock game does is:
#
#     walking    button 1              change stance
#                right                 walk
#     fighting   button 1              change stance
#                button 2 + left       walk left
#                button 2 + right      walk right
#                left  + height        punch high/mid/low
#                right + height        kick  high/mid/low
#
# Movement is the button-modified action and attacking is what a bare stick
# does. That is backwards from every other action game, and it is the whole
# control complaint: repositioning takes two hands, and any stray push throws
# a strike.
#
# This swaps them, keeping the player always facing right:
#
#     walking    button 1, or up       change stance
#                right                 walk
#     fighting   right + button 1      punch, height from the vertical
#                right + button 2      kick,  height from the vertical
#                right                 walk right
#                left                  walk left
#                down, stick centred   change stance
#
# Four pieces, because a mapping is not just its decoder:
#
#   1. the walking-stance decoder, 23 bytes at $7833
#   2. the fighting-stance decoder, 49 bytes at $784A
#   3. the punch family's height now comes from the right hemisphere too, so
#      the two thread cells naming p_706C become p_7042 -- one in the
#      dispatcher, one in w_753E, which re-polls every six frames so that
#      holding the stick repeats the strike
#   4. the walk chains poll "button 2 + direction" to decide whether to keep
#      walking; with movement no longer needing the button those tests are
#      NOPed out, or a walk would stop the instant it started
MAP2_AT = 0x7833
MAP2_WAS = [
    0xA5, 0x08, 0x10, 0x05, 0xA0, 0x02, 0x4C, 0x7B, 0x78,   # walking stance
    0xA5, 0xA2, 0xF0, 0x07, 0x30, 0x05, 0xA0, 0x01, 0x4C, 0x7B, 0x78,
    0x4C, 0x7B, 0x78,
    0xA5, 0x08, 0x10, 0x05, 0xA0, 0x03, 0x4C, 0x7B, 0x78,   # fighting stance
    0xA5, 0x09, 0x10, 0x12, 0xA5, 0xA2, 0x10, 0x05, 0xA0, 0x04, 0x4C, 0x7B,
    0x78, 0xC9, 0x01, 0xD0, 0x05, 0xA0, 0x05, 0x4C, 0x7B, 0x78,
    0xA5, 0xA2, 0x10, 0x05, 0xA0, 0x06, 0x4C, 0x7B, 0x78,
    0xC9, 0x01, 0xD0, 0x05, 0xA0, 0x07, 0x4C, 0x7B, 0x78,
]
# Two blocks of new code, in the 6,448 free bytes at $A6D0. Assembled from
# scratchpad sources with tools/asm.py rather than by hand: both are branchy,
# and the first hand-built attempt put a BMI twelve kilobytes from its target.
HEIGHT = 0xA710          # a Forth word: code field, then the code
FIGHT = 0xA740           # the fighting-stance decoder

# Every site that asks "which height is the stick calling for?".
HEIGHT_CELLS = [0x7920,      # w_7898, command 6 -- the punch, when issued
                0x793E,      # w_7898, command 7 -- the kick, when issued
                0x7568,      # w_753E -- the punch chain, re-polled every 6 frames
                0x769A]      # w_7670 -- the kick chain, same
P_LEFT, P_RIGHT = 0x706C, 0x7042

HEIGHT_CODE = [
    0xCA, 0x94, 0x00, 0xCA, 0x94, 0x00, 0xA5, 0x08, 0x05, 0x09,
    0x10, 0x1A, 0xA5, 0xA2, 0xC9, 0x01, 0xD0, 0x14, 0xA5, 0xA3,
    0xF0, 0x0C, 0x30, 0x05, 0xA0, 0x03, 0x4C, 0x36, 0xA7, 0xA0,
    0x01, 0x4C, 0x36, 0xA7, 0xA0, 0x02, 0x94, 0x00, 0x4C, 0x1E,
    0x40,
]
FIGHT_CODE = [
    0xA5, 0xA2, 0xC9, 0x01, 0xD0, 0x0D, 0xA5, 0x08, 0x30, 0x23,
    0xA5, 0x09, 0x30, 0x24, 0xA0, 0x05, 0x4C, 0x7B, 0x78, 0xA5,
    0xA2, 0x30, 0x11, 0xA5, 0x08, 0x05, 0x09, 0x30, 0x08, 0xA5,
    0xA3, 0xC9, 0x01, 0xD0, 0x02, 0xA0, 0x03, 0x4C, 0x7B, 0x78,
    0xA0, 0x04, 0x4C, 0x7B, 0x78, 0xA0, 0x06, 0x4C, 0x7B, 0x78,
    0xA0, 0x07, 0x4C, 0x7B, 0x78,
]

# A walk keeps going while one of two words says yes. The stock pair asks for
# button 2 with a direction, because button 2 was how you moved.
#
# The first version of the remap NOPed that test out so a bare direction would
# keep a walk going -- and a test removed is not a test inverted. They then said
# yes with *any* button held, so you kept walking through a strike and could
# not stand still to fight. These replacements ask for a direction and **no**
# button, which is what the stock pair meant in the stock mapping.
#
# Six bytes where four were free, so they are new words in free space and the
# three cells that named the old pair are repointed.
WALKL, WALKR = 0xA840, 0xA860
WALKL_CELLS = [0x749E]                    # w_749A, the walk-left chain
WALKR_CELLS = [0x72E8, 0x73FC]            # w_72E4 and w_73F8, walking right
P_WALKL, P_WALKR = 0x70AA, 0x70C2

WALKL_CODE = [
    0xCA, 0x94, 0x00, 0xCA, 0x94, 0x00, 0xA5, 0x08, 0x05, 0x09,
    0x30, 0x08, 0xA5, 0xA2, 0x10, 0x04, 0xA0, 0x01, 0x94, 0x00,
    0x4C, 0x1E, 0x40,
]
WALKR_CODE = [
    0xCA, 0x94, 0x00, 0xCA, 0x94, 0x00, 0xA5, 0x08, 0x05, 0x09,
    0x30, 0x14, 0xA5, 0xA2, 0xC9, 0x01, 0xD0, 0x0E, 0xAD, 0x7B,
    0x18, 0xF0, 0x05, 0xA0, 0x01, 0x4C, 0x80, 0xA8, 0xA0, 0x02,
    0x94, 0x00, 0x4C, 0x1E, 0x40,
]


MAP2_NOW = [
    # --- walking stance, $7833 -- unchanged ---------------------------------
    0xA5, 0xA2,              # 7833  LDA $A2        horizontal
    0xC9, 0x01,              # 7835  CMP #$01
    0xD0, 0x04,              # 7837  BNE $783D
    0xA0, 0x01,              # 7839  LDY #$01         right: walk
    0xD0, 0x0A,              # 783B  BNE $7847        (LDY #1 leaves Z clear)
    0xA5, 0x08,              # 783D  LDA INPT0      button 1
    0x30, 0x04,              # 783F  BMI $7845        pressed: change stance
    0xA5, 0xA3,              # 7841  LDA $A3        vertical
    0x10, 0x02,              # 7843  BPL $7847        not up: nothing
    0xA0, 0x02,              # 7845  LDY #$02         change stance
    0x4C, 0x7B, 0x78,        # 7847  JMP $787B
    # --- fighting stance, $784A: now a jump out -----------------------------
    # It grew six bytes past the 49 available here when the stance change
    # learned to keep out of the way of a button, so it lives at $A740 and
    # jumps back to the shared tail at $787B.
    0x4C, FIGHT & 0xFF, FIGHT >> 8,
] + [0xEA] * 46

def fix_remap(p):
    """Bare stick moves, buttons strike, stance change on the stick.

    Two defects from the first version of this are fixed here, both found by
    playing it.

    **Kicks always came out middle.** The height decoder `p_7042` bails out
    when INPT1 is held, which was right while a bare stick attacked and button
    2 meant "move" -- and exactly backwards once button 2 *is* the kick. The
    height came back 0, the CASE that picks high/middle/low matched nothing,
    and the chain fell through to the middle strike. Punches were unaffected
    because they are on button 1, which is why only half the controls looked
    broken. A replacement decoder at $A710 guards on "a button is held"
    instead, and all four sites that ask for a height now use it.

    **Down could drop you out of stance mid-fight.** The stance change asked
    for down with the stick centred and did not care about the buttons, so
    releasing forward while still holding the button after a low strike left
    exactly that combination and read as stand-down. It now requires no button
    held, which costs six bytes and pushes the fighting-stance decoder out of
    the 49 available in the primitive and into free space at $A740.

    Still not measured by anything but playing, which is what found both of
    these.
    """
    p.put(MAP2_AT, MAP2_NOW, expect=MAP2_WAS)
    p.put(FIGHT, FIGHT_CODE, expect=[0x00] * len(FIGHT_CODE))
    return _remap_common(p)


def fix_the_lot_2(p):
    """Fix 3, fix 6 and fix 11: cadence, reach and the corrected mapping."""
    fix_half_the_waits(p)
    fix_generous_reach(p)
    fix_remap(p)
    return p


# -------------------------------------------------------------------- fix 13
# A patch built to settle a question rather than to improve the game.
#
# The hit window is two bytes: $18C3, compared at $6A68/$6A88, and $18C4,
# compared at $79FC/$7A1C. One is 8 or 9 and the other 10 in every encounter,
# so one fighter reaches further -- and which one is the player has been an
# *inference* throughout:
#
#     the tests reading $18C3 probe from $187D and $1885
#     the tests reading $18C4 probe from $189C and $18A4
#     the player's command dispatcher writes its behaviour into slot $1878
#
# $1878 sits in the first block, so the player is probably the $18C3 fighter,
# and probably has the smaller window. Fixes 5 and 6 were built so that would
# not matter: set both the same and the inference is irrelevant.
#
# This does the opposite on purpose. It gives the $18C3 fighter 12 against the
# other's 10, so one round of play settles it:
#
#     your strikes connect from further      -> the player is $18C3 and did
#                                               have the smaller window
#     the opponent hits you from further     -> the player is $18C4 and the
#                                               inference was backwards
#
# Either answer is worth having and neither can be read off the ROM.
PLAYER_REACH = 12


def fix_player_reach(p):
    """Give the $18C3 fighter more reach, to find out who it is.

    Not a fix; a probe with a controller attached. If it turns out backwards,
    nothing already built is wrong -- fixes 5 and 6 set both windows the same,
    which is right either way. What changes is a sentence in the documentation
    that is currently doing more work than it has earned.
    """
    for addr, now in zip(REACH_CELLS, REACH_NOW):
        p.put(addr, [PLAYER_REACH, 0x00], expect=[now, 0x00])
    return p

# ----------------------------------------------------------------- fixes 14-15
# Knockback, which the 8-bit version has and this one does not.
#
# Both post-hit routines place a spark and move nobody:
#
#     7A30  LDA pos / SBC #$0E / STA $18D5 / LDA #$8C / STA $18D3 / RTS
#     6A9C  the same, into $18CD / $18CB with #$8D
#
# There is one per direction. The $7Axx collision group probes the second
# fighter's limbs against the first's body, so $7A30 runs when **the first
# fighter is struck**; the $6Axx group is the mirror, and $6A9C runs when the
# second is. Every body point is built per frame from a base plus an animation
# offset -- `LDA $186D / CLC / ADC ($00,X) / STA $187D` -- so the bases are
# $186D for the first fighter and $188C for the second, and moving one moves
# that fighter.
#
# **The call sites are patched, not the routines.** $7A30 has five callers and
# one of them, at $8CAF, is in the $8Cxx approach code rather than the
# collision group. Rewriting the routine would give that caller a knockback
# too, in a subsystem this has not read. Redirecting the four collision callers
# reaches exactly what it means to.
#
# **The thing to watch for is nothing happening.** The base is very likely
# rewritten by the animation script on the next update, in which case the shove
# is erased before it is drawn. That is the question this exists to answer, and
# no amount of reading answers it.
KNOCK_CALLERS_A = [0x7A78, 0x7AB4, 0x7AF2, 0x7B2E]    # first fighter struck
KNOCK_CALLERS_B = [0x6AE4, 0x6B20, 0x6B5E, 0x6B9A]    # second fighter struck
HIT_A, HIT_B = 0x7A30, 0x6A9C
BASE_A, BASE_B = 0x186D, 0x188C
# In the free bytes at $A6D0-ish -- but $A6D0/$A6F0 themselves are far too
# tight now (SHOVE_A carries the pillar parallax on top of GOON_TABLE, ~555
# bytes; see _shove_world_src), and would run into $A710's block. Moved past
# $A990's slot-8 gate instead, which ends at $A9B6. $AC00 leaves SHOVE_A the
# full ~555 bytes it needs (ending $ABEB) with margin before SHOVE_B starts.
SHOVE_A, SHOVE_B = 0xA9C0, 0xAC00

# Every ordinary walk tick calls $65C8 to add the tick's delta into a table
# of display-list zone X-fields, 62 ($3E) bytes apart, one family per zone --
# keeping a multi-zone sprite's segments in horizontal sync as it moves.
# PLAYER_TABLE is the player's own (below, no longer used by the shove --
# see _shove_world_src); the six families that respond to $188C changes split
# into two groups that were originally lumped together as one "GOON_TABLE",
# on the assumption that whatever tracks the goon's movement is the goon.
#
# It is not, not all of it. Reading the actual display-list records behind
# each family (`probes/ramdump.lua` + a raw dump, four bytes ending at the
# X-field already being synced) settled it directly:
#
#   family E ($252F/$256D/$25AB)   gfx ptr $5A5A, X=$34 -- a few pixels off
#   family G ($25ED/$262B/$2669)   gfx ptr $4D59, X=$3A -- exactly $188C
#   families A-D (12 addresses)    gfx ptr $FB9F, shared across all four,
#                                   X = $DC/$1C/$5C/$9C -- 64 apart, evenly
#                                   spaced, matching neither $186D nor $188C
#
# G's X field reading exactly $188C at the moment of the dump is the goon's
# own body; E, a companion zone-group a handful of pixels off (the natural
# offset between two zone-bands of one fighting stance, the same shape the
# player/goon sprite-ID work found much earlier), is the rest of it.
# GOON_OWN_TABLE is those six. A-D share one graphics pointer, sit at four
# fixed, evenly-spaced columns unrelated to either fighter's own coordinate,
# and only ever move by however much delta someone hands them -- the
# background tiles a fight pans past. WORLD_TABLE is those twelve, and only
# a hit that is supposed to move the world reaches them; see
# _shove_world_src.
#
# Both tables confirmed empirically (`probes/tracetable.lua`, filtering by PC
# in $65C8-$6622) across a full round of knockback-light play with both
# fighters walking both directions extensively; the player used only one
# pair of families the whole round, the goon used six -- a real asymmetry
# between the two sprites, not a sampling gap, as far as that recording
# shows.
PLAYER_TABLE = [0x2537, 0x2575, 0x25B3, 0x25F5, 0x2633, 0x2671]
GOON_OWN_TABLE = [
    0x252F, 0x256D, 0x25AB,
    0x25ED, 0x262B, 0x2669,
]
WORLD_TABLE = [
    0x2423, 0x2461, 0x249F,
    0x2427, 0x2465, 0x24A3,
    0x242B, 0x2469, 0x24A7,
    0x242F, 0x246D, 0x24AB,
]
GOON_TABLE = GOON_OWN_TABLE + WORLD_TABLE

# The foreground pillars: real parallax, confirmed inside a single dispatch
# of $90A0 (the goon's own $188C apply) -- a run of JSR $65C8 calls, one per
# family, all in the same tick. PILLAR_FAR moves at the same rate as
# GOON_TABLE; PILLAR_NEAR moves at exactly double that, every time, on both
# the tick this was found in and every other event checked. Found by
# grouping every instance of the pillar's graphics pointer ($D0F8/$C8F8/
# $C0F8, width 3 -- the *other* $D0F8-shaped object at width 1 is the small
# WORLD_TABLE accent tiles, a different graphic that cost real time to stop
# conflating with this one) by X-field and diffing widely separated frames;
# see docs/fixing-karateka.md for the two false leads before this, including
# a write-tap that reported zero hits on a window that simply never
# exercised the code, not because the write was actually invisible to it.
PILLAR_FAR = [
    0x2379, 0x23B7, 0x23F5, 0x2433, 0x2471, 0x24AF,
    0x24ED, 0x252B, 0x2569, 0x25A7, 0x25E5, 0x2623,
]
PILLAR_NEAR = [
    0x229D, 0x22DB, 0x2319, 0x2357, 0x2395, 0x23D3, 0x2411, 0x244F,
    0x248D, 0x24CB, 0x2509, 0x2547, 0x2585, 0x25C3, 0x2601, 0x263F,
    0x267D, 0x26BB, 0x26F9, 0x2737,
]

# Where the scene setup puts the pillars, and the reason a knockback needs a
# bound at all.
#
# Measured, not guessed: a write-tap over $2000-$2800 across a full session
# (676k frames) caught every write to these addresses tagged with the PC that
# made it. Two game-side writers place them -- $59A0 (a fill-with-constant
# record) and then $58CE, the tail of the display-list builder at $58A4 that
# blits 4-byte MARIA headers off the Forth stack. Of 217 placements each:
#
#   PILLAR_FAR  head $2379 -> 160 ($A0), 217 times out of 217
#   PILLAR_NEAR head $229D -> 175 ($AF), 217 times out of 217
#   WORLD_TABLE head $2423 ->   0,       217 times out of 217
#
# Always the same value. There is no level-layout table and no world index
# for the background: placement is a constant scene reset once per encounter,
# and from there the display-list X byte *is* the position of record -- the
# only state there is. (The fighters are the opposite case: $252F and $2537
# take 30732 and 1748 writes from the same builder, redrawn every frame from
# $188C/$186D, which is why the shove's writes to GOON_OWN_TABLE are
# cosmetic and only its $188C write actually persists.)
#
# Nothing bounds these objects directly -- no CMP against a pillar address,
# none against $188C either. The limit is implicit: the world only moves when
# the walk cascade scrolls it, and the *walk* is what is bounded ($A488
# tests $186D against #$A0 and hands off to $A490). An injected shove
# inherits no bound, so a run of hits walks the pillars off their home with
# nothing to reconcile it until the next encounter's reset.
PILLAR_FAR_HOME = 0xA0
PILLAR_NEAR_HOME = 0xAF

# $F9: free zero page, per docs/karateka-map.md -- named by no stock
# instruction, written zero times across two probe runs. $D7 and $DC, the
# other two candidates the map lists, are already claimed by the loop-gate
# fixes; $EF looks free and is not (51 writes, all at boot).
SCRATCH = 0xF9


def _shove_world_src(hit_addr, amount, tables, org, tables_2x=()):
    """A hit's effect: the $6F-graduated resistance against the exit moves
    $188C either way, but which display-list families get walked by the same
    delta depends on which fighter was struck -- HIT_A (the goon struck the
    player) passes GOON_TABLE plus PILLAR_FAR at the normal rate and
    PILLAR_NEAR (`tables_2x`) at double it, since that is the real parallax
    ratio confirmed for both columns; HIT_B (the player struck the goon)
    passes only GOON_OWN_TABLE and no `tables_2x` at all -- a goon hit does
    not move the world, pillars included. See the comment above
    GOON_OWN_TABLE/WORLD_TABLE for the first split and how it was found, and
    above PILLAR_FAR/PILLAR_NEAR for the parallax pair.

    Three shapes before this one, not two:

    The first moved the *struck* fighter: the goon's base on a goon hit, the
    player's own base and its own zone table (PLAYER_TABLE) on a player hit,
    floored at zero instead of resisted. Looked right taken alone -- the
    struck fighter's drawn position tracked its logical one exactly, verified
    per-hit -- and was wrong regardless, because the player's on-screen
    position is not free-standing state. It is pinned. A person watching
    normal combat movement confirmed it directly: holding a direction in a
    fight moves the goon and the background while the player's own sprite
    sits still ($186D took zero writes across a 500-frame window of held
    input), and only unpins at the far wall. $186D still exists and the exit
    check still reads it, but nothing draws it as a screen position in
    combat; $188C and its tables are what a fight actually moves.

    The second corrected that for a player hit, then swung too far the other
    way: a goon hit got nothing at all. Also wrong -- a goon hit needs its
    own recoil to show up the same way its footsteps do, or a punch
    connecting has no visible effect on the goon whatsoever.

    The third had both hits walk the *same* eighteen addresses, on the
    reasoning that whatever tracks the goon's movement is simply "the goon".
    It is not: twelve of those eighteen are a repeating background tile,
    sharing one graphics pointer and sitting at four fixed columns that
    match neither fighter's coordinate (`probes/ramdump.lua`, read directly
    against the display-list records). Only the other six -- the ones whose
    X-field actually reads $188C -- are the goon's own body. Having a goon
    hit walk the background tiles too reads as the player sliding sideways
    every time the goon is struck, the same illusion a world-moving *player*
    hit is supposed to produce and a goon hit is not.

    No equivalent of this resistance exists on the *near* side yet, so this
    does not by itself protect against being run out the near end of the
    arena."""
    lines = [
        "HIT = $%04X" % hit_addr,
        "GOONX = $%04X" % BASE_B,
        "SCRATCH = $%02X" % SCRATCH,
        ".org $%04X" % org,
        "    JSR HIT",
        "    LDA GOONX",
        "    STA SCRATCH",
        "    LDA #$6F",
        "    SEC",
        "    SBC GOONX",
        "    BCC immune",
        "    LSR A",
        "    BEQ immune",
        "    CMP #%d" % amount,
        "    BCC usehalf",
        "    LDA #%d" % amount,
        "usehalf:",
        "    CLC",
        "    ADC GOONX",
        "    STA GOONX",
        "    SEC",
        "    LDA GOONX",         # SCRATCH = new - old (the actual delta)
        "    SBC SCRATCH",
        "    STA SCRATCH",
        "    JMP sync",
        "immune:",
        "    RTS",
        "sync:",
    ]
    for t in tables:
        lines += [
            "    LDA $%04X" % t,
            "    CLC",
            "    ADC SCRATCH",
            "    STA $%04X" % t,
        ]
    for t in tables_2x:
        # Add SCRATCH twice rather than precomputing 2*SCRATCH into a second
        # byte -- there is only the one confirmed-free zero page cell. CLC
        # before each ADC rather than relying on the first ADC's carry, since
        # whether that carry is set depends on the addr's own current value,
        # not just the delta.
        lines += [
            "    LDA $%04X" % t,
            "    CLC",
            "    ADC SCRATCH",
            "    CLC",
            "    ADC SCRATCH",
            "    STA $%04X" % t,
        ]
    lines += ["    RTS"]
    return lines


def _shove_world_bounded_src(hit_addr, amount, tables, org, tables_2x=()):
    """_shove_world_src plus a ceiling on how far the world may be pushed.

    Same tables, same parallax split, same $6F exit-side resistance. The one
    addition is a bound, and the reason for it is measured rather than
    theorised.

    The unbounded version moves the world on every player hit with nothing to
    stop it, because nothing in the stock game stops it either -- the world
    only ever moves as a consequence of the walk cascade, so it inherits the
    walk's own limit and never needed one of its own. A shove injected at hit
    time skips the walk entirely and so skips the limit. Traced on a real
    session, the far pillar's head walked 160 -> 196 over nineteen hits
    without a single scroll event in between:

        861  $2379 160 162 $AA94   <- the shove
        ...  nineteen of these, no $660A anywhere
        4150 $2379 194 196 $AA94
        4422 $2379 196 195 $660A   <- the game finally scrolls, from 36px off

    Nothing reconciles that until the next encounter's placement resets it.
    Pushed far enough past MARIA's 160-pixel window the pillar wraps and
    re-enters from the left, which is the reported symptom: foreground pillars
    sliding in from the left when they belong at the stage's right edge.

    The bound is the placement constant itself. PILLAR_FAR is placed at $A0
    every time, and a player hit always shoves the world in the + direction,
    so the headroom is $A0 - current, and the delta is whichever is smaller:
    the resistance's answer or that headroom. At home there is no headroom and
    the hit is absorbed -- the same graceful nothing the $6F resistance
    already does at the far end.

    One bound covers both columns. PILLAR_NEAR is placed at $AF and moves at
    exactly double, so its offset from home stays exactly twice the far
    column's under both movers (the game's scroll and this shove preserve the
    ratio), which makes its half-headroom identical to the far column's. No
    second test needed, which matters -- there is only the one free zero page
    byte to work with.
    """
    lines = [
        "HIT = $%04X" % hit_addr,
        "GOONX = $%04X" % BASE_B,
        "SCRATCH = $%02X" % SCRATCH,
        "PILLAR = $%04X" % tables_2x_guard(tables),
        ".org $%04X" % org,
        "    JSR HIT",
        # exit-side resistance: half the headroom toward $6F, capped at amount
        "    LDA #$6F",
        "    SEC",
        "    SBC GOONX",
        "    BCC immune",
        "    LSR A",
        "    BEQ immune",
        "    CMP #%d" % amount,
        "    BCC keep",
        "    LDA #%d" % amount,
        "keep:",
        "    STA SCRATCH",           # SCRATCH = the delta the resistance wants
        # world-side bound: never push the pillars back past their placed home
        "    LDA #$%02X" % PILLAR_FAR_HOME,
        "    SEC",
        "    SBC PILLAR",
        "    BCC immune",            # already at or past home: no headroom
        "    BEQ immune",
        "    CMP SCRATCH",
        "    BCS apply",             # headroom >= wanted: take the full delta
        "    STA SCRATCH",           # otherwise take exactly the headroom
        "apply:",
        "    LDA GOONX",
        "    CLC",
        "    ADC SCRATCH",
        "    STA GOONX",
        "    JMP sync",
        "immune:",
        "    RTS",
        "sync:",
    ]
    for t in tables:
        lines += [
            "    LDA $%04X" % t,
            "    CLC",
            "    ADC SCRATCH",
            "    STA $%04X" % t,
        ]
    for t in tables_2x:
        lines += [
            "    LDA $%04X" % t,
            "    CLC",
            "    ADC SCRATCH",
            "    CLC",
            "    ADC SCRATCH",
            "    STA $%04X" % t,
        ]
    lines += ["    RTS"]
    return lines


def tables_2x_guard(tables):
    """The address the bound reads: PILLAR_FAR's head.

    Kept as a function rather than a bare constant so the guard address is
    derived from the table list actually being written, not restated. If the
    far column ever stops being part of the player-hit walk, this raises
    instead of silently guarding an address nothing moves.
    """
    if PILLAR_FAR[0] not in tables:
        raise ValueError("bounded shove needs PILLAR_FAR in its table walk")
    return PILLAR_FAR[0]


# Two more free zero-page bytes, found the same way $D7/$DC/$F9 were: named
# by no instruction anywhere in the ROM, in any zero-page addressing mode
# (zp, zp,X, zp,Y, (zp,X), (zp),Y -- not just bare absolute). $D7/$DC/$F9
# are already claimed by other fixes (loop gate, strike rhythm, this
# knockback's own display-list-sync delta), so this needed its own search.
DRIFT = 0xD2      # cumulative world-push this arena, since ARENA last reset
LASTARENA = 0xD3  # $18AA as of the last hit, to notice a fresh placement

# How far a run of player-hit shoves is allowed to push the world before it
# stops moving it further. Fix 37's approach -- bound against the object's
# own placed position -- turned out to be broken by construction: every
# scene places PILLAR_FAR at its bound already (confirmed 217/217), so the
# very first hit of any fight found zero headroom and the fix did nothing.
# The real problem isn't "how far from home" -- it's that nothing in the
# stock game limits how far $188C can be pushed during a fight at all (a
# full scan for any compare instruction against $188C found none); walking
# never needed one because a real walk-hold only lasts so many frames, but a
# run of hits has no such limit and can push it far enough to wrap the byte
# and re-enter from the other side -- the original "pillars slide in from
# the left" report. So this bounds *our own contribution* instead of the
# object's absolute position, reset whenever $18AA shows a fresh placement
# happened (confirmed empirically: $18AA holds one value for an entire
# scene -- 1/2/3 for a whole level 1/2/3 in a traced session -- and changes
# exactly on the frame everything else resets too). 48 is a judgment call,
# not a measurement: comfortably short of ever wrapping a byte even
# stacked with ordinary walking drift in between hits, and easy to retune.
MAX_WORLD_DRIFT = 48


def _shove_world_capped_src(hit_addr, amount, tables, org, tables_2x=()):
    """Same $6F exit-side resistance as _shove_world_src, plus a second,
    independent cap: the running total this shove has itself contributed
    since the current arena ($18AA) was placed can't exceed
    MAX_WORLD_DRIFT. See the comment above DRIFT/MAX_WORLD_DRIFT for why
    this replaces fix 37's per-object bound rather than refining it."""
    lines = [
        "HIT = $%04X" % hit_addr,
        "GOONX = $%04X" % BASE_B,
        "SCRATCH = $%02X" % SCRATCH,
        "DRIFT = $%02X" % DRIFT,
        "LASTARENA = $%02X" % LASTARENA,
        "ARENA = $18AA",
        ".org $%04X" % org,
        "    JSR HIT",
        # a fresh arena placement resets how much we've pushed so far
        "    LDA ARENA",
        "    CMP LASTARENA",
        "    BEQ samearena",
        "    STA LASTARENA",
        "    LDA #$00",
        "    STA DRIFT",
        "samearena:",
        # exit-side resistance: half the headroom toward $6F, capped at amount
        "    LDA #$6F",
        "    SEC",
        "    SBC GOONX",
        "    BCC immune",
        "    LSR A",
        "    BEQ immune",
        "    CMP #%d" % amount,
        "    BCC usehalf",
        "    LDA #%d" % amount,
        "usehalf:",
        "    STA SCRATCH",
        # world-drift cap: never push more than the remaining budget
        "    LDA #%d" % MAX_WORLD_DRIFT,
        "    SEC",
        "    SBC DRIFT",
        "    BCC immune",           # already past the cap (shouldn't happen)
        "    BEQ immune",            # no budget left
        "    CMP SCRATCH",
        "    BCS takeall",           # budget covers the resisted delta as-is
        "    STA SCRATCH",            # else take exactly what's left
        "takeall:",
        "    LDA DRIFT",
        "    CLC",
        "    ADC SCRATCH",
        "    STA DRIFT",
        "    LDA GOONX",
        "    CLC",
        "    ADC SCRATCH",
        "    STA GOONX",
        "    JMP sync",
        "immune:",
        "    RTS",
        "sync:",
    ]
    for t in tables:
        lines += [
            "    LDA $%04X" % t,
            "    CLC",
            "    ADC SCRATCH",
            "    STA $%04X" % t,
        ]
    for t in tables_2x:
        lines += [
            "    LDA $%04X" % t,
            "    CLC",
            "    ADC SCRATCH",
            "    CLC",
            "    ADC SCRATCH",
            "    STA $%04X" % t,
        ]
    lines += ["    RTS"]
    return lines


# A fix of its own, not a composite with fix 31/37 (same "governs" knob,
# meant as an alternative to both) -- but given its own address pair anyway
# rather than reusing SHOVE_A/SHOVE_B, since the drift-cap logic doesn't fit
# in the room fix 31/37 leave (581 bytes against 576 available; the extra
# arena-check and drift-cap steps cost 26 bytes over the unbounded version).
# $AD00 is the next clear stretch in the same free-space pool past SHOVE_B,
# with nothing else in this file claiming past $AC00.
SHOVE_A38, SHOVE_B38 = 0xAD00, 0xAFC0


# The four table walkers a real walk step drives, found by tracing a live
# pillar write back up its call path rather than by reading code: the write
# lands at $660A (inside $65C8), reached from $8EA2, reached from $914C --
# one of the satellites in the six-copy machine. Reading that satellite
# ($913C) shows the full set it calls after negating $AA:
#
#     $9062  the goon's own body, $188C, and its derived cells
#     $8ECC  a table set every one of the six satellites walks
#     $8E88  a world/pillar set (satellites $914C, $9166, $91F2, $920C)
#     $8E66  a second world/pillar set (satellites $917E, $9198, $9226, $9240)
#
# All four are the same shape -- push three literal args, JSR $65C8 -- and
# all read $AA through $65C8. Nothing else is needed: set $AA, call these,
# and the world moves exactly as far as a walk step moves it, through the
# game's own code.
#
# This is why every earlier attempt failed. Setting $AA alone, or
# reinstalling the walk chain minus its footsteps, or repeating that chain
# six times, all moved the goon and nothing else -- they routed through
# satellite $9110, which calls $9062 and $8ECC and *no world mover at all*.
# The pillars were never reachable that way. Confirmed live: with these four
# calls a player hit walks the pillar 160 -> 158 -> 156 ..., every write
# landing at $660A, the game's own walker, with no display-list address
# named by this patch anywhere.
# Which of the four to actually call is the whole question, and it was
# settled by running each one alone against a real session and diffing the
# addresses it wrote at $660A against the others:
#
#   $9062          the goon's own body, $188C and its derived cells
#   $8ECC          the near world's lower half ALONE -- zone rows 8-10,
#                  the $2423/$2427/$242B/$242F columns, three zones each,
#                  four $65C8 calls, then RTS at $8F3A
#   $8F3E          the near world's upper AND lower half -- eight $65C8
#                  calls running to a single RTS at $901A: rows 5-7
#                  ($2369/$236D/$2371/$2375) and then, with no RTS between
#                  them at $8FAC, rows 8-10 again
#
# That missing RTS at $8FAC is the whole trap. Read as separate routines
# -- which is how they look, since $8FAC opens with the same LDY #$00
# prologue every one of these has -- $8F3E is "the top half" and calling
# it next to $8ECC looks like covering both. It is not: the bottom gets
# walked twice and the top once, so the halves of one pillar travel at
# two different rates and shear apart. Measured on level 3, hit frames
# came out top=2 bottom=4 against the two unit steps taken. $8F3E alone
# is the pair.
#   $8E88          both foreground pillar columns, all 32 addresses:
#                  PILLAR_FAR's 12 and PILLAR_NEAR's 20
#   $8E66          six addresses nothing here had mapped --
#                  $2607, $2645, $2683, $26C1, $26FF, $273D -- the far-edge
#                  set, the first level's cliff tiles
#
# $8ECC and $8F3E are two halves of one thing, which is easy to miss on
# level 1 where the upper half is empty and never written all session.
# Level 3 fills it: dumped there, both families hold the *same* four X
# coordinates -- 76, 140, 204, 12 -- across all six zone rows, four
# columns 64 apart running rows 5 through 10. Calling only $8ECC moves
# rows 8-10 and leaves 5-7 behind, which is exactly the reported "only the
# bottom half of the longer pillars moves". The game's own satellites call
# one or the other, never both, and the phase machine alternates so both
# halves travel over a full walk; a one-shot step has to call both.
#
# The far actors are a different matter and stay out. They are held at the
# edges until the level wants them, and a hit is not the level wanting
# them: walking drags them in because walking is how you travel toward
# them, and a knockback is not travel.
WORLD_MOVERS = [0x9062, 0x8F3E]   # $8F3E already carries the lower half

# ...except when a far actor has already scrolled into view, in which case
# it is part of the visible world and standing still is the wrong answer --
# it would slide against everything around it. So each far mover is called
# only if its own column is on screen, tested against its head address.
#
# $A0 is the threshold and it is not a guess: it is MARIA's visible width,
# and it is the same constant the game's own edge check uses ($A488
# compares $186D against it). The placement values agree -- PILLAR_FAR
# parks at exactly $A0, PILLAR_NEAR at $AF, the cliffs at $FC, all at or
# past the edge -- while level 2's pillar had walked down to 13-19 by the
# time it was genuinely in view.
#
# One head per group is enough because a group is a vertical column and
# every zone in it carries the same X (confirmed in a level 3 dump).
# $8E88 owns both pillar columns and can only be gated once, so it is
# gated on the far column, which is the one that crosses the edge first
# when the world travels left.
FAR_MOVERS = [
    (0x8E88, 0x2379),   # both pillar columns, gated on PILLAR_FAR's head
    (0x8E66, 0x2607),   # the cliff set, gated on its own head
]
ONSCREEN = 0xA0


# ------------------------------------------------------------------ fix 43
# A hit shoves a fixed number of pixels, and 2 is a number picked to look
# right rather than to mean anything. The walk cycle has a number that does
# mean something: the stride is drawn over eight units, which is why fix 22
# stops at four (double) and warns that the feet stop keeping up past it.
# Eight units spread over three hits is 3-3-2, so three blows move the world
# exactly one stride and the knockback stays commensurate with walking
# instead of drifting against it.
#
# It needs one byte of state per side to remember where in the three-hit
# cycle it is. $F7 and $F8 are it: zero writes across a full recorded
# playthrough -- all six stages, both bird stages and the ending, 9595
# frames -- with $E8 as the positive control at 3.9M writes, the same test
# and the same standard that cleared $D7/$DC/$F9. They sit directly under
# $F9, which fix 37 already holds, so they carry the same "reachable in
# principle from a wrapped stack base" caveat and the same measured answer.
#
# Garbage at power-on repairs itself: a phase past the end of the cycle
# fails every guard, so that hit is a short one, and the advance below
# wraps it to 0. Nothing needs to initialise these.
CADENCE = (3, 3, 2)         # eight units of world over three hits
CADENCE_PHASE_A = 0x00F7    # where in the cycle a hit on the player is
CADENCE_PHASE_B = 0x00F8    # and one on the goon, counted separately

# Its own pair rather than fix 39's. Sharing would be legitimate -- the two
# are alternatives on the same knob and never both applied, which is how
# fix 31 and fix 37 share SHOVE_A/SHOVE_B -- but the cadence version is
# larger, and reusing $AFC0 leaves its goon routine ending at $AFFC, three
# bytes under fix 41's $B000. Three bytes is not margin. $B100 is clear
# past everything this file claims.
SHOVE_A43, SHOVE_B43 = 0xB100, 0xB1C0


def _cadence(cadence, amount):
    """Normalise a cadence, and hold the shape the emitters assume.

    Both emitters turn "which hits get the extra pixel" into a single
    unsigned compare against the phase, which only works if the cadence
    never rises: the hits taking a given step have to be a prefix of the
    cycle. 3-3-2 is; 3-2-3 would need a compare per phase and is not worth
    the bytes for a difference nobody can see."""
    if cadence is None:
        return (amount,)
    if list(cadence) != sorted(cadence, reverse=True):
        raise SystemExit("cadence %r must not rise: see _cadence" % (cadence,))
    return tuple(cadence)


def _cadence_advance_src(cadence):
    """Step the phase byte, wrapping -- and repairing anything unexpected."""
    return [
        "    INC PHASE",
        "    LDA PHASE",
        "    CMP #$%02X" % len(cadence),
        "    BCC cadence_kept",
        "    LDA #$00",
        "    STA PHASE",
        "cadence_kept:",
    ]


def _shove_world_native_src(hit_addr, amount, org, movers=WORLD_MOVERS,
                            cadence=None, phase=None):
    """A player hit moves the world one walk step's worth, by handing the
    delta to the game's own table walkers instead of writing display-list
    addresses by hand.

    Every other shape this took wrote the addresses itself, from a list
    mapped out of level 1 -- which meant the list was level 1's. Level 3
    extends its background pillars upward and level 2 places its at the far
    edge, and neither is in that list, so neither moved with the rest of the
    world. Going through $65C8 the way a walk step does covers whatever a
    given level actually has, because it is the same code that scrolls that
    level when you walk through it.

    Two details of the real path this has to match, both learned the hard
    way by getting them wrong:

    The sign is inverted between intent and effect. Satellite `$913C`
    negates `$AA` before calling any mover (`LDA #0 / SEC / SBC $AA`), so a
    walk chain setting `-2` -- "the player means to go left" -- hands the
    movers `+2`, and the world travels right. That is what makes a pinned
    player read as walking left. Handing the movers the intent value
    directly sends the world the wrong way, which is exactly what the first
    version of this did.

    And the game never hands a mover a magnitude. `$936E`/`$9375` stashes
    the raw value in `$18BE` as a countdown, forces `$AA` to exactly +/-1,
    and loops one unit step at a time until `$18BE` reaches zero. So a
    two-pixel step is two one-pixel steps, not one two-pixel step. This
    calls the movers once per unit for the same reason -- it keeps the
    per-call delta at the +/-1 every other caller of these routines uses,
    rather than a value the game itself never passes them.

    No resistance and no drift cap here on purpose. Both existed to bound a
    hand-written shove that nothing else limited; a step through the game's
    own walkers is ordinary movement, the same pixels holding the stick
    would give. See WORLD_MOVERS above for how the four routines were found
    and why the earlier attempts reached none of them."""
    cadence = _cadence(cadence, amount)
    lines = [
        "HIT = $%04X" % hit_addr,
        "AA = $00AA",
        ".org $%04X" % org,
        "    JSR HIT",
    ]
    if phase is not None:
        lines.insert(1, "PHASE = $%04X" % phase)
    # one unit step per pixel, sign already inverted the way the satellite
    # inverts it -- the movers want the effect, not the intent
    for step in range(max(cadence)):
        # steps past the shortest hit in the cycle are conditional: with a
        # non-increasing cadence the hits that get step `step` are exactly
        # phases 0..n-1, so one unsigned compare covers it
        n = sum(1 for a in cadence if a > step)
        if n < len(cadence):
            lines += [
                "    LDA PHASE",
                "    CMP #$%02X" % n,
                "    BCS cadence_done",
            ]
        lines += ["    LDA #$01", "    STA AA"]
        lines += ["    JSR $%04X" % m for m in movers]
        # a far actor already in view travels with the rest of the world;
        # one still parked past the edge is left where the level put it.
        # Retested every step, since a step can carry a column across.
        for i, (mover, head) in enumerate(FAR_MOVERS):
            skip = "far%d_%d" % (step, i)
            lines += [
                "    LDA $%04X" % head,
                "    CMP #$%02X" % ONSCREEN,
                "    BCS %s" % skip,
                "    JSR $%04X" % mover,
                "%s:" % skip,
            ]
    if len(cadence) > 1:
        lines += ["cadence_done:"] + _cadence_advance_src(cadence)
    lines += [
        "    LDA #$00",
        "    STA AA",
        "    RTS",
    ]
    return lines


def _shove_native_world(p, amount, cadence=None):
    """A player hit steps the world; a goon hit still moves only the goon,
    through the applier that owns it ($9062)."""
    org_a, org_b = ((SHOVE_A38, SHOVE_B38) if cadence is None
                    else (SHOVE_A43, SHOVE_B43))
    io_a = _assemble(_shove_world_native_src(
        HIT_A, amount, org_a, cadence=cadence, phase=CADENCE_PHASE_A))
    io_b = _assemble(_shove_native_src(
        HIT_B, amount, GOON_APPLY, org_b,
        cadence=cadence, phase=CADENCE_PHASE_B))
    p.put(org_a, io_a, expect=[0x00] * len(io_a))
    p.put(org_b, io_b, expect=[0x00] * len(io_b))
    for a in KNOCK_CALLERS_A:
        p.put(a, [0x20, org_a & 0xFF, org_a >> 8],
              expect=[0x20, HIT_A & 0xFF, HIT_A >> 8])
    for a in KNOCK_CALLERS_B:
        p.put(a, [0x20, org_b & 0xFF, org_b >> 8],
              expect=[0x20, HIT_B & 0xFF, HIT_B >> 8])
    return p


def fix_knockback_light_native(p):
    """Two pixels of world, moved the way the walk step moves it."""
    return _shove_native_world(p, 2)


# ------------------------------------------------------------------ fix 44
# Fix 43 keeps knockback commensurate with a walk *step*. It is still not
# commensurate with the *hall*, because it moves the scenery without ever
# telling the game the scenery moved.
#
# The game keeps a travel odometer -- see "The odometer" in
# docs/karateka-map.md. $18BD is a phase index 0-4 and $18B3-$18BC are five
# pairs, one segment of the hall each, with a read head in every pair:
# travelling right pours a segment from its odd byte into its even one,
# travelling left pours it back. Eight dispatchers -- four machines, each a
# (left, right) pair, at $924A/$92E0, $93D8/$9466, $955C/$95EA and
# $96E2/$9770 -- all drive that one odometer, and a unit of travel is
# always the same three things: spend a unit from the current segment, step
# the phase when a segment empties, and call the satellite that phase names.
#
# Knockback does none of it, so after a beating the odometer and the
# scenery disagree, permanently for that hall: the game still thinks it
# owes you travel it has already spent, so the far actors arrive late, the
# hall's ends move, and a badly knocked-about run can walk the scenery past
# where the level meant it to stop. Fix 39's own note called the missing
# bound a KNOWN GAP; this is where the bound actually lives.
#
# So fix 44 spends the odometer for every unit it shoves. Two things fall
# out of doing that properly rather than just decrementing a counter:
#
# **The hall's left end becomes a real limit.** When phase 0's counter
# reaches zero the world is as far left as that hall goes, and the shove
# stops there. The game's own answer at that point is
# `if $18DC == 0: JMP $A490` -- a screen transition, not a return -- which
# is emphatically not a thing to do from inside a hit handler, so this
# stops instead of imitating it.
#
# **Phases 0 and 4 do not move the world at all.** Every one of the eight
# dispatchers calls $91A2 there, and $91A2 is two instructions --
# `JSR $901E / RTS` -- the *player's* body and nothing else. That is how a
# hall's ends work: the background is pinned and the player crosses the
# screen instead. It is also the one satellite that does not negate $AA
# first, so $901E takes the intent (-1, leftward) rather than the effect. A
# knockback in those two segments therefore slides the player, which is
# both what the game does there and what a knockback ought to look like.
#
# Phases 1-3 are unchanged from fix 43: $9062, $8F3E, and the far movers
# behind the on-screen gate. That gate is standing in for the phase rule --
# each machine calls exactly one far mover, at phase 1 or phase 3 depending
# on which machine is live, and which machine is live is a runtime fact
# with no static caller (see $936E's note). "Move it if it is already
# visible" reaches the same answer from the other side, and unlike the
# phase rule it does not need to know which machine is running.
#
# Indexing the pairs wants a register and X is the Forth data-stack
# pointer, so Y carries phase*2 -- which rules out INC/DEC, since the 6502
# has those in abs,X only. Load-modify-store through `LDA abs,Y` does the
# same work in four more bytes.
SHOVE_A44, SHOVE_B44 = 0xB300, 0xB3C0
ODOM_PHASE = 0x18BD         # which segment the world is in, 0-4
ODOM_BEHIND = 0x18B3        # segment p's leftward budget is BEHIND + 2p
ODOM_AHEAD = 0x18B4         # and its rightward one is AHEAD + 2p
PLAYER_BODY = 0x901E        # what $91A2 calls at a hall's two ends


def _shove_odometer_src(hit_addr, org, cadence, phase, movers=WORLD_MOVERS):
    """Fix 43's shove, spending the hall's travel budget as it goes.

    See the block comment above for why this is not just a decrement. The
    unit step is one subroutine called up to three times rather than three
    copies of itself: it is no longer four instructions, and only the
    cadence guard needs to stay unrolled."""
    cad = _cadence(cadence, None)
    lines = [
        "HIT = $%04X" % hit_addr,
        "AA = $00AA",
        "PHASE = $%04X" % phase,
        "ODOM = $%04X" % ODOM_PHASE,
        "BEHIND = $%04X" % ODOM_BEHIND,
        "AHEAD = $%04X" % ODOM_AHEAD,
        ".org $%04X" % org,
        "    JSR HIT",
    ]
    for step in range(max(cad)):
        n = sum(1 for a in cad if a > step)
        if n < len(cad):
            lines += [
                "    LDA PHASE",
                "    CMP #$%02X" % n,
                "    BCS cadence_done",
            ]
        lines += ["    JSR unit"]
    lines += ["cadence_done:"] + _cadence_advance_src(cad) + [
        "    LDA #$00",
        "    STA AA",
        "    RTS",
        # ---- one unit of leftward travel, or nothing if the hall ends here
        "unit:",
        "    LDA ODOM",
        "    CMP #$05",
        "    BCS nothing",      # a phase the game never sets: leave it alone
        "    ASL A",
        "    TAY",              # Y = phase*2, the offset into the pairs
        "seek:",
        "    LDA BEHIND,Y",
        "    BNE spend",
        "    LDA ODOM",
        "    BEQ nothing",      # phase 0 empty: the hall goes no further left
        "    DEC ODOM",
        "    DEY",
        "    DEY",
        "    JMP seek",
        "spend:",
        "    SEC",
        "    SBC #$01",
        "    STA BEHIND,Y",     # a unit out of this segment...
        "    LDA AHEAD,Y",
        "    CLC",
        "    ADC #$01",
        "    STA AHEAD,Y",      # ...and into the far side of it
        "    LDA ODOM",
        "    BEQ ends",
        "    CMP #$04",
        "    BEQ ends",
        # phases 1-3: the world travels and the player stays pinned
        "    LDA #$01",
        "    STA AA",
    ]
    lines += ["    JSR $%04X" % m for m in movers]
    for i, (mover, head) in enumerate(FAR_MOVERS):
        skip = "far%d" % i
        lines += [
            "    LDA $%04X" % head,
            "    CMP #$%02X" % ONSCREEN,
            "    BCS %s" % skip,
            "    JSR $%04X" % mover,
            "%s:" % skip,
        ]
    lines += [
        "    RTS",
        # phases 0 and 4: the background is pinned, so the player moves.
        # $91A2 does not negate $AA, so this is the intent and not the
        # effect -- -1 is leftward, which is the way a hit sends you.
        "ends:",
        "    LDA #$FF",
        "    STA AA",
        "    JSR $%04X" % PLAYER_BODY,
        "nothing:",
        "    RTS",
    ]
    return lines


def fix_knockback_odometer(p):
    """Fix 43, and the hall's own travel budget kept honest.

    3-3-2 as before; what is added is that every unit shoved is a unit
    spent out of the segment the world is standing in, so the odometer and
    the scenery stop disagreeing -- and the hall's left end becomes a real
    limit on how far a beating can push you. See the block comment above."""
    io_a = _assemble(_shove_odometer_src(
        HIT_A, SHOVE_A44, CADENCE, CADENCE_PHASE_A))
    io_b = _assemble(_shove_native_src(
        HIT_B, CADENCE[0], GOON_APPLY, SHOVE_B44,
        cadence=CADENCE, phase=CADENCE_PHASE_B))
    p.put(SHOVE_A44, io_a, expect=[0x00] * len(io_a))
    p.put(SHOVE_B44, io_b, expect=[0x00] * len(io_b))
    for a in KNOCK_CALLERS_A:
        p.put(a, [0x20, SHOVE_A44 & 0xFF, SHOVE_A44 >> 8],
              expect=[0x20, HIT_A & 0xFF, HIT_A >> 8])
    for a in KNOCK_CALLERS_B:
        p.put(a, [0x20, SHOVE_B44 & 0xFF, SHOVE_B44 >> 8],
              expect=[0x20, HIT_B & 0xFF, HIT_B >> 8])
    return p


def fix_knockback_cadence(p):
    """3-3-2: three hits move the world one whole stride.

    Fix 39's two pixels are a number that looks right. Eight is a number
    the game already has -- the walk stride is drawn over eight units, and
    fix 22 stops at four for exactly that reason. Splitting eight over
    three hits keeps knockback commensurate with walking instead of
    drifting against it, and 3-3-2 is the only even-ish way to do it.

    Everything else is fix 39: the same walkers, the same inverted sign,
    the same one-unit-per-call stepping, the same on-screen gate on the far
    actors, and on the goon's side the same $6F-graduated resistance with
    only the cap moving. The two sides count separately, so an exchange of
    blows does not make one side's cycle depend on the other's.

    What it does not do -- and neither does fix 39 -- is touch the game's
    own travel odometer. Walking runs the phase machine at $92E0/$924A,
    which drains a five-segment budget ($18B3-$18BC) and steps a phase
    index ($18BD) as the world goes by; a knockback calls the walkers
    underneath all of that. So being knocked about does not spend the
    level, which is the behaviour you want, but it does mean the odometer
    and the actual scenery position disagree by however far you have been
    shoved. See "The odometer" in docs/karateka-map.md."""
    return _shove_native_world(p, CADENCE[0], cadence=CADENCE)


# Each stage word seeds the fight's counters through w_A0C2, pushing two
# literals: the cell immediately before the call goes to $18C2 (and its
# clamp $18E1), the one before that to $18BF/$18E2. $18C2 is the
# opponent's hit points -- `DEC $18C2 / BNE` at $6908 drops into the death
# sequence at $6912 when it reaches zero.
#
# Which of the two pushed values lands in $18C2 was settled by watching a
# real fight rather than by reasoning about stack order: stage 1 seeds both
# counters to 13 and so cannot distinguish them, but stage 2's pair is
# (13, 17) and the trace shows $18C2 taking 17 -- the cell nearest the
# call. That also matches the run of values rising with difficulty.
OPPONENT_HP = [
    (0xA218, 13, 1),    # stage 1
    (0xA26A, 17, 2),    # stage 2
    (0xA2BC, 21, 3),    # stage 3
    (0xA30E, 21, 4),    # stage 4
    (0xA360, 21, 5),    # stage 5
    (0xA3B2, 25, 6),    # stage 6
]


# The ending stages an embrace: w_A3BC calls w_A158, which dresses both
# fighters from $5C47/$5C50, stands them five pixels apart at $50 and $55,
# and installs w_8BA6 -- a colour-and-sound celebration loop. What it never
# does is look at how the player arrived. $187C (stance: 0 walking,
# non-zero fighting) is read from exactly two places in the ROM and neither
# is here, so walking in still in fighting stance earns the same embrace.
# The Apple II original does not allow that; she kicks you. This adds the
# check back.
#
# It is a reconstruction, not a repair. There is no disabled princess-kick
# code in this ROM to switch on -- nothing here was found dormant and
# re-enabled. The rule is taken from the original game and the strike is
# built out of parts this port does have.
#
# The hook is one cell. $A1A0 is the word w_A158 installs into the goon's
# slot; point it at ours instead, decide, and hand off. The strike is
# copied from $8CA8, which is how the game itself hits the player: push
# the body point $187F and JSR $7A30 (HIT_A). Handing off to w_8BA6 either
# way matters -- our word overwrites itself in the slot, so it runs exactly
# once and the ending still proceeds afterwards.
# One thing the first version of this got wrong, caught in play: reading
# $187C at the ending can never fire, because the player always arrives in
# walking stance. Every stage start zeroes it outright -- `STY $187C` at
# $A503, plain 6502, not a Forth store -- so by the time the final hall is
# staged the stance is gone regardless of how the fight was left.
#
# So the stance has to be kept as the player sets it. $187C is written from
# three places in the whole ROM: that `STY` at $A503, and two Forth cells
# naming p_5280 -- $7226, reached with a literal 1 on the stack, and
# $726A, reached with a literal 0. Those two *are* the stance control: one
# per direction, nothing else in the game selects a stance. Repointing them
# at a store that snapshots on the way past therefore records every stance
# the player ever chooses, and only those.
#
# $E7 holds the snapshot: named by no zero-page instruction anywhere in
# the ROM, checked the same way $D7/$DC/$F9 and $D2/$D3 were.
#
# Which value to snapshot is the whole fix, and the first answer was
# backwards. It kept the *outgoing* contents of $187C -- `LDA $187C / STA
# $E7` before falling into the store -- which is the stance the player was
# in before the change, i.e. the opposite of the one they just picked.
# Traced over a full run it inverts cleanly: $E7 reads 1 through every
# stretch spent walking and 0 through every stretch spent in stance, and
# the recorded playthrough, which drops out of stance and *walks* off
# stage 6, armed the kick. Reported as "the ending still does not work".
#
# p_5280 is `LDA $02,X / STA ($00,X)`: the address is the top stack cell
# and the value sits one cell below it, at $02,X. Snapshotting that instead
# keeps the stance being selected. $E7 then answers exactly the question
# the Apple II asks at the door -- "is this player in fighting stance?" --
# carried past the transition that would otherwise erase it.
#
# It survives across stages on purpose. Nothing resets it at a stage
# boundary, so it means "the last stance you chose", not "the last stance
# you chose in this hall". That is the rule and not a shortcut: clearing
# the final guard needs fighting stance, so every real run arrives with
# $E7 = 1 unless the player deliberately drops out of it before walking
# through the last door -- which is the thing the original makes you do.
PRINCESS_KICK = 0xB000
STANCE_KEEP = 0xB030        # a store primitive that snapshots first
STANCE_SNAP = 0xE7          # free zero page: the preserved stance
STORE_CODE = 0x5282         # p_5280's own code, which STANCE_KEEP falls into
STANCE_STORES = [0x7226, 0x726A]   # the two cells naming p_5280 after $187C
STORE_WORD = 0x5280
NOOP_WORD = 0x9908          # w_9908, a single EXIT
WALK_DEATH = 0x6937         # force health to 1 and decrement: the
                            # instant death a walking-stance hit gives
ENDING_WORD = 0x8BA6        # w_8BA6, the celebration loop
ENDING_CELL = 0xA1A0        # the cell inside w_A158 that installs it


def _stance_keep_src(org):
    """A store that remembers what it is about to write.

    Stance writes go through `p_5280` like every other store. Repointing
    the two cells that name it after `$187C` sends those two -- and only
    those two -- here first, where the value being stored is kept, before
    falling into `p_5280`'s own code to do the store it was going to do.
    Nothing else in the game routes through this.

    `$02,X` is where that value is: p_5280 is `LDA $02,X / STA ($00,X)`,
    address on top of the stack and value one cell under it. Reading the
    *contents* of `$187C` here instead -- which the first version did --
    yields the stance being left rather than the one being taken, and gets
    the ending exactly backwards."""
    return [
        "SNAP  = $%02X" % STANCE_SNAP,
        "STORE = $%04X" % STORE_CODE,
        ".org $%04X" % (org + 2),
        "    LDA $02,X",
        "    STA SNAP",
        "    JMP STORE",
    ]


def _princess_kick_src(org):
    """Walked in: the stock ending. Came armed: the game's own death.

    Two wrong strikes before this one, both found by play rather than by
    reading. `$7A30` writes `$18D3`/`$18D5`, which are the *goon's* spark
    -- it is the routine for the player landing a blow, not taking one --
    and `$6A9C`, the player's own, is no better: both are spark placers
    that set a graphic and a coordinate and return, which is why the
    knockback fixes had to add movement themselves. A spark at the ending
    is then painted over by the embrace in the same breath, so nothing
    showed at all. Reaching for `$8CA8`, the bird's whole hit, failed for
    a different reason: the ending parks the no-op `w_9908` in both the
    player's slot and the collision resolver's, so the masks it sets are
    written and never read.

    The real death is simpler than any of that, and it is the rule this
    was trying to restore in the first place. At `$6932` the game asks the
    stance, and a player struck while *walking* has health forced to 1 and
    decremented -- an instant kill:

        6932  LDA $187C / BNE $693C     ; fighting: decrement normally
        6937  LDA #$01 / STA $18BF      ; walking: health := 1
        693C  DEC $18BF                 ; -> 0
        6941  install $7750 into $1878  ; the death

    `$18BF` is the player's health: `$67BF` draws the bar from it and
    `$68B2` flashes a warning under 3. Confirmed by trace -- in a recorded
    run that ends in death, it reaches 0 at `$693C`.

    So the armed path jumps to `$6937` and lets the game kill the player
    exactly as a walking-stance hit does, after parking the no-op in the
    goon's slot so no celebration plays over it."""
    return [
        "SNAP  = $%02X" % STANCE_SNAP,
        "SLOT  = $1897",
        "SLOTH = $1898",
        "NEXT  = $401E",
        ".org $%04X" % (org + 2),
        "    LDA #$%02X" % (ENDING_WORD & 0xFF),
        "    STA SLOT",
        "    LDA #$%02X" % (ENDING_WORD >> 8),
        "    STA SLOTH",
        "    LDA SNAP",
        "    BNE armed",
        "    JMP NEXT",
        "armed:",
        "    LDA #$%02X" % (NOOP_WORD & 0xFF),
        "    STA SLOT",
        "    LDA #$%02X" % (NOOP_WORD >> 8),
        "    STA SLOTH",
        "    JMP $%04X" % WALK_DEATH,
    ]


def fix_princess_kick(p):
    """Approach the princess in fighting stance and she strikes you.

    Restores the one rule this port left out of its ending -- see
    PRINCESS_KICK above for why it is a reconstruction rather than a
    repair, and docs/karateka-map.md for how the ending was found to be
    present but unconditional."""
    code = _assemble(_princess_kick_src(PRINCESS_KICK))
    cf = [(PRINCESS_KICK + 2) & 0xFF, (PRINCESS_KICK + 2) >> 8]
    p.put(PRINCESS_KICK, cf + code, expect=[0x00] * (2 + len(code)))
    p.put(ENDING_CELL, [PRINCESS_KICK & 0xFF, PRINCESS_KICK >> 8],
          expect=[ENDING_WORD & 0xFF, ENDING_WORD >> 8])
    # keep the stance the transition is about to throw away
    keep = _assemble(_stance_keep_src(STANCE_KEEP))
    cf2 = [(STANCE_KEEP + 2) & 0xFF, (STANCE_KEEP + 2) >> 8]
    p.put(STANCE_KEEP, cf2 + keep, expect=[0x00] * (2 + len(keep)))
    for cell in STANCE_STORES:
        p.put(cell, [STANCE_KEEP & 0xFF, STANCE_KEEP >> 8],
              expect=[STORE_WORD & 0xFF, STORE_WORD >> 8])
    return p


def fix_ending_testbed(p):
    """Fix 41 plus fix 40: the restored kick, reachable in a few minutes.

    Testing the ending means getting to the end, and the ending is the one
    thing a long run can cost you at the last fight. Glass jaw makes the
    trip short; the kick is the thing being tested."""
    fix_glass_jaw(p)
    fix_princess_kick(p)
    return p


def fix_glass_jaw(p):
    """Every opponent dies in one hit. A testing aid, not a game change.

    Reaching the later stages to look at something -- the bird's exemptions
    on 4/5/6, the princess in the stage-6 screen -- otherwise means playing
    well through everything before it, and a recording made that way is
    long and easy to lose to one bad fight. This makes the trip short
    enough to record in a couple of minutes.

    Only the opponent's hit points change. The player's own survival is
    untouched, so a run recorded against this is still a real run and can
    still be lost; it is quicker, not invincible."""
    for addr, was, _stage in OPPONENT_HP:
        p.put(addr, [0x01, 0x00], expect=[was, 0x00])
    return p


def _shove_capped(p, amount):
    """_shove with the world-drift cap instead of fix 37's per-object bound.
    Only the player-hit side changes -- a goon hit still hands its delta to
    GOON_APPLY and never touches the world or pillars, so it has nothing to
    cap. See _shove_world_capped_src."""
    io_a = _assemble(_shove_world_capped_src(
        HIT_A, amount, GOON_TABLE + PILLAR_FAR, SHOVE_A38,
        tables_2x=PILLAR_NEAR))
    io_b = _assemble(_shove_native_src(HIT_B, amount, GOON_APPLY, SHOVE_B38))
    p.put(SHOVE_A38, io_a, expect=[0x00] * len(io_a))
    p.put(SHOVE_B38, io_b, expect=[0x00] * len(io_b))
    for a in KNOCK_CALLERS_A:
        p.put(a, [0x20, SHOVE_A38 & 0xFF, SHOVE_A38 >> 8],
              expect=[0x20, HIT_A & 0xFF, HIT_A >> 8])
    for a in KNOCK_CALLERS_B:
        p.put(a, [0x20, SHOVE_B38 & 0xFF, SHOVE_B38 >> 8],
              expect=[0x20, HIT_B & 0xFF, HIT_B >> 8])
    return p


def fix_knockback_light_capped(p):
    """Fix 31's two-unit shove, with a world-drift cap instead of fix 37's
    broken per-object bound -- see _shove_world_capped_src for why 37 did
    nothing and what this does differently."""
    return _shove_capped(p, 2)


def _assemble(lines):
    import asm
    return list(asm.Assembler().assemble(lines))


# The goon's own per-tick applier, called from seven places inside the
# six-copy walk-cycle machine (all of them: save $AA, sometimes negate it,
# JSR here, restore $AA -- nothing more). It does two $65C8 calls covering
# exactly GOON_OWN_TABLE, then continues straight into $188C, $189C, $189E,
# $18A0, $18A2, $18A4 (the goon's "body points"/"striking limbs", per
# docs/karateka-map.md -- rebuilt fresh from $188C every frame regardless,
# so touching them here is a bonus, not a requirement).
#
# Confirmed callable cold, without reproducing any caller's context: $65C8's
# own address arithmetic adds a constant baked into its own bytes ($2224) to
# Y*62+aux*4, where Y and aux are $9062's own compile-time literals -- there
# is no ambient base pointer a caller has to arrange, only the ordinary Forth
# convention that a pushed small literal's high byte is zero. Checked against
# all seven real call sites; none sets up anything beyond the $AA dance.
#
# It only ever walks the goon's own six addresses, never WORLD_TABLE or
# either pillar column, so this only helps the HIT_B (goon knocked back by
# the player) side of a shove -- HIT_A (goon hits player, world moves) still
# needs an explicit table walk, since no single native routine covers that
# case. Used by `_shove_bounded` (fix 37), not by the plain `_shove` (fix
# 31) -- confirmed working there by play. See "goon hits player" in
# docs/fixing-karateka.md for how this was chased down.
GOON_APPLY = 0x9062


def _shove_native_src(hit_addr, amount, apply_addr, org,
                      cadence=None, phase=None):
    """Same $6F-graduated resistance as _shove_world_src, but hands the
    clamped delta to the game's own applier instead of hand-walking a table.

    Does not pre-write GOONX the way _shove_world_src does -- GOON_APPLY's
    own first act is `LDA $188C/ADC $AA/STA $188C`, so writing $188C here
    too would apply the delta twice. This only computes the clamped delta
    into $AA and lets GOON_APPLY make the one authoritative write.

    With a cadence, only the cap varies -- the resistance is unchanged and
    still decides first, so a goon backed against the wall takes the same
    reduced shove whichever hit of the cycle it is. The available shove is
    held on the 6502 stack across the phase arithmetic because X is the
    Forth data-stack pointer and cannot be borrowed, and Y is scratch here
    only until the applier is called."""
    cad = _cadence(cadence, amount)
    lines = [
        "HIT = $%04X" % hit_addr,
        "GOONX = $%04X" % BASE_B,
        "AA = $00AA",
        ".org $%04X" % org,
        "    JSR HIT",
        "    LDA #$6F",
        "    SEC",
        "    SBC GOONX",
        "    BCC immune",
        "    LSR A",
        "    BEQ immune",
    ]
    if len(cad) == 1:
        lines += [
            "    CMP #%d" % cad[0],
            "    BCC usehalf",
            "    LDA #%d" % cad[0],
        ]
    else:
        lines.insert(1, "PHASE = $%04X" % phase)
        # keep the wall's answer, take this hit's place in the cycle, then
        # step the cycle on -- INC/CMP leaves Y alone, so Y is still the
        # position when the caps below test it
        lines += [
            "    PHA",
            "    LDA PHASE",
            "    TAY",
        ] + _cadence_advance_src(cad) + [
            "    PLA",
        ]
        # one cap per run of equal amounts; with a non-increasing cadence
        # each run is a prefix, so one unsigned compare picks it
        runs, end = [], 0
        for a in cad:
            if runs and runs[-1][0] == a:
                runs[-1][1] += 1
            else:
                runs.append([a, 1])
        for j, (a, n) in enumerate(runs):
            end += n
            last = (j == len(runs) - 1)
            if not last:
                lines += ["    CPY #$%02X" % end, "    BCS cap%d" % j]
            lines += [
                "    CMP #%d" % a,
                "    BCC usehalf",
                "    LDA #%d" % a,
            ]
            if not last:
                lines += ["    JMP usehalf", "cap%d:" % j]
    lines += [
        "usehalf:",
        "    STA AA",
        "    JSR $%04X" % apply_addr,
        "    LDA #$00",
        "    STA AA",
        "    RTS",
        "immune:",
        "    RTS",
    ]
    return lines


def _shove(p, amount):
    """Wrap each post-hit routine. Either fighter's hit moves $188C, but a
    goon hit walks only its own six display-list families (GOON_OWN_TABLE)
    and a player hit walks all eighteen, background tiles included
    (GOON_TABLE), plus the two foreground-pillar columns at their real
    parallax ratio (PILLAR_FAR at the normal rate, PILLAR_NEAR at double) --
    see `_shove_world_src` for what that split is, how it was found, and
    the wrong shapes this took before it.

    The shrink toward $6F is the exit-side resistance, and it predates the
    player-hit correction -- built for the goon's own shove, which had two
    earlier shapes that did not work:

    A hard cap at $70 -- the exit, per `w_A4D2` -- stopped the deadlock and
    relocated the pile-up to the cap. Nothing pulls the goon back, so hit after
    hit walked it to $70 and pinned it there, and with the player pursuing, the
    fight ended up pinned against that wall.

    A player-relative limit did not fire at all, for the reason the person
    playing it gave: the player is always closing, so goonX - playerX stays
    small and the limit never triggers.

    So the shove itself shrinks: half the remaining headroom, capped at the full
    amount. From $50 a run of hits gives 8, 8, 7, 4, 2, 1, then nothing. The
    goon approaches $6F and never arrives, which keeps it fightable rather than
    pinned against a wall.

    Nothing yet resists a run of hits toward the *near* end of the arena --
    that is the next gap, not this one.
    """
    io_a = _assemble(_shove_world_src(HIT_A, amount, GOON_TABLE + PILLAR_FAR,
                                       SHOVE_A, tables_2x=PILLAR_NEAR))
    io_b = _assemble(_shove_world_src(HIT_B, amount, GOON_OWN_TABLE, SHOVE_B))
    p.put(SHOVE_A, io_a, expect=[0x00] * len(io_a))
    p.put(SHOVE_B, io_b, expect=[0x00] * len(io_b))
    for a in KNOCK_CALLERS_A:
        p.put(a, [0x20, SHOVE_A & 0xFF, SHOVE_A >> 8],
              expect=[0x20, HIT_A & 0xFF, HIT_A >> 8])
    for a in KNOCK_CALLERS_B:
        p.put(a, [0x20, SHOVE_B & 0xFF, SHOVE_B >> 8],
              expect=[0x20, HIT_B & 0xFF, HIT_B >> 8])
    return p


def _shove_bounded(p, amount):
    """_shove with the world-side ceiling -- see _shove_world_bounded_src.

    Only the player-hit side changes. The goon-hit side is untouched: it hands
    its delta to the game's own applier and never moves the pillars or the
    background at all, so it has nothing to overrun.
    """
    io_a = _assemble(_shove_world_bounded_src(
        HIT_A, amount, GOON_TABLE + PILLAR_FAR, SHOVE_A, tables_2x=PILLAR_NEAR))
    io_b = _assemble(_shove_native_src(HIT_B, amount, GOON_APPLY, SHOVE_B))
    p.put(SHOVE_A, io_a, expect=[0x00] * len(io_a))
    p.put(SHOVE_B, io_b, expect=[0x00] * len(io_b))
    for a in KNOCK_CALLERS_A:
        p.put(a, [0x20, SHOVE_A & 0xFF, SHOVE_A >> 8],
              expect=[0x20, HIT_A & 0xFF, HIT_A >> 8])
    for a in KNOCK_CALLERS_B:
        p.put(a, [0x20, SHOVE_B & 0xFF, SHOVE_B >> 8],
              expect=[0x20, HIT_B & 0xFF, HIT_B >> 8])
    return p


def fix_knockback_light(p):
    """Two units -- a quarter of a walking step, felt rather than seen."""
    return _shove(p, 2)


def fix_knockback_light_bounded(p):
    """Fix 31's two-unit shove, with the world stopped at the pillars' home.

    Same feel as fix 31 when the world has somewhere to go; the difference
    only shows once the world is back at its placed position, where fix 31
    keeps pushing and this one absorbs the hit instead.
    """
    return _shove_bounded(p, 2)


def fix_knockback(p):
    """Four units of shove on a hit -- half a walking step."""
    return _shove(p, 4)


def fix_knockback_hard(p):
    """Eight units: a whole walking step, which is what the 8-bit one looks like.

    A walk moves 2 units a frame across four frames, so eight is exactly one
    step given back. That is enough to be unmistakable, which is what a first
    test wants: if eight does nothing, four was never going to show either, and
    the animation script is rewriting the base.
    """
    return _shove(p, 8)


def fix_the_lot_3(p):
    """Everything worth playing at once: cadence, reach, mapping, knockback."""
    fix_half_the_waits(p)
    fix_generous_reach(p)
    fix_remap(p)
    fix_knockback_hard(p)
    return p



# -------------------------------------------------------------------- fix 17
# The difficulty switches do nothing, and it is a one-word bug.
#
# `w_A0C2` sets up an encounter, and twice it does this:
#
#     LIT $0282  C@   LIT $0080  =   0BRANCH +4   1-   LIT $18C4  C!
#     LIT $0282  C@   LIT $0080  =   0BRANCH +4   1+   LIT $18C3  C!
#
# $0282 is SWCHB, and the game's own five switch primitives at $5A40-$5A8C
# read bits 0, 1, 3, 6 and 7 of it -- Reset, Select, Pause, and the left and
# right difficulty switches, which is the standard 7800 layout confirmed out
# of this ROM rather than out of a manual.
#
# The switches are active low. So `SWCHB = $80` asks for bit 7 set and bits
# 0, 1 and 3 clear: right difficulty in the A position **while Reset, Select
# and Pause are all held down at once**. Reset restarts the game. The test
# cannot be true while anybody is playing, so the `1-` and the `1+` never run,
# and the two hit windows keep the values the encounter pushed.
#
# It reads like `=` written where a mask test was meant. The bundled `AND`
# primitive is `p_4FA8` and it takes the same two cells off the stack, so the
# repair is one word in each of the two places:
#
#     LIT $0282  C@   LIT $0080  AND   0BRANCH +4   1-  ...
#
# Then bit 7 alone decides, which is what a difficulty switch is for.
#
# ## What the adjustment says about who is who
#
# The direction is the interesting part, and it survives the bug. `1+` goes to
# $18C3, which is 8 or 9; `1-` goes to $18C4, which is 10. So the condition, if
# it ever fired, would move the two windows *toward each other* -- 9 and 9.
#
# $18C3 governs the first fighter's strikes reaching the second, and $18C4 the
# reverse. The player's command dispatcher writes its behaviour into slot
# $1878, which is in the first block. So the smaller window is the player's,
# and the adjustment exists to hand the player parity.
#
# That is a second, independent line of evidence for the same conclusion as the
# address ordering, which is worth more than either alone -- and it is what
# checking the switches actually bought, even though the switches themselves
# turned out to be dead.
SWITCH_TESTS = [0xA0EA, 0xA102]     # the two `=` cells in w_A0C2
EQUALS, AND_WORD = 0x4FEC, 0x4FA8


def fix_difficulty_switch(p):
    """Make the right difficulty switch do what it was meant to do.

    Two cells. With it, position A equalises the two hit windows at 9 and 9;
    position B leaves the player on 8 or 9 against the opponent's 10, which is
    the game everybody has been playing.

    Note the sense is the wrong way round for a difficulty switch -- A is the
    harder setting by convention and here it is the kinder one. Fixing that
    means testing the bit inverted, which is a different patch and a guess
    about intent; this one only makes the existing test reachable.
    """
    for a in SWITCH_TESTS:
        p.put(a, [AND_WORD & 0xFF, AND_WORD >> 8],
              expect=[EQUALS & 0xFF, EQUALS >> 8])
    return p


# ------------------------------------------------------------------ fix 48
# Fix 17 makes the difficulty switch *reachable*. It does not make it mean
# what the switch on the front of the console says it means, and fix 17's
# own docstring says so: on Atari hardware **A is the expert position and B
# is the novice one**, and the adjustment the game reaches for hands the
# player parity. Fix 17 therefore gives you an easier game in A and a
# harder one in B, which is backwards.
#
# The test is `LIT $0282 C@ LIT $0080 <word> 0BRANCH`, and `0BRANCH`
# branches -- skipping the adjustment -- when the flag is zero. Fix 17 puts
# `AND` in that slot, so the adjustment runs when bit 7 is *set*, which is
# A. Inverting it means a flag that is true when the masked bits are
# *clear*, and there is no such primitive in the image to point at.
#
# It is a small one to add. The two words the game's own `=` hands off to
# do all the awkward work already: `$403B` pops a cell and writes $FFFF
# into the new top, `$404F` pops a cell and writes $0000. Both leave the
# two operands consumed and one flag pushed, so a comparison word only has
# to decide which to jump to and never has to touch X -- which matters,
# because X is the Forth data-stack pointer and cannot be borrowed.
#
# No scratch byte either: "is the 16-bit AND zero" can be answered by
# testing each half and bailing out early, so nothing has to be held
# anywhere between the two.
#
# What it does not do is change which way the adjustment goes. The game
# moves the two hit windows *toward* each other -- $18C3 up, $18C4 down,
# 8-or-9 and 10 becoming 9 and 9 -- and that is the designers' intent
# preserved. All this decides is which position of the switch asks for it.
BITZERO = 0xB400            # a comparison word the image does not have
TRUE_WORD, FALSE_WORD = 0x403B, 0x404F


def _bitzero_src(org):
    """`( value mask -- flag )`, true when none of the masked bits are set.

    The mirror of the `AND` fix 17 uses, and the same shape as the image's
    own `=` at `$4FEE`: decide, then jump to whichever of the game's two
    flag-pushers is wanted and let it do the stack."""
    return [
        "TRUE  = $%04X" % TRUE_WORD,
        "FALSE = $%04X" % FALSE_WORD,
        ".org $%04X" % (org + 2),
        "    LDA $00,X",
        "    AND $02,X",
        "    BNE set",
        "    LDA $01,X",
        "    AND $03,X",
        "    BNE set",
        "    JMP TRUE",
        "set:",
        "    JMP FALSE",
    ]


def fix_difficulty_switch_proper(p):
    """The difficulty switch, working *and* the right way round.

    Fix 17 with the sense corrected: B, the novice position, is now the one
    that equalises the two hit windows at 9 and 9, and A leaves the player
    on 8 or 9 against the opponent's 10. Stock, neither position does
    anything at all."""
    code = _assemble(_bitzero_src(BITZERO))
    cf = [(BITZERO + 2) & 0xFF, (BITZERO + 2) >> 8]
    p.put(BITZERO, cf + code, expect=[0x00] * (2 + len(code)))
    for a in SWITCH_TESTS:
        p.put(a, [BITZERO & 0xFF, BITZERO >> 8],
              expect=[EQUALS & 0xFF, EQUALS >> 8])
    return p


# ----------------------------------------------------------------- fixes 18-19
# Two other ways to ask for a stance change, so the guard in fix 11 can go.
#
# Fix 11 keeps down-with-the-stick-centred and adds "no button held" to stop it
# firing on the way out of a low strike. That works, and it is a guard -- a
# thing to remember rather than a thing that is obviously right. Both of these
# pick a gesture nothing else uses instead, and drop the guard.
#
# **Fix 18, both buttons.** The vertical stick then plays no part in the
# fighting stance at all: up and down are purely a strike height, and no order
# of releasing anything can produce a stand-down. It has one trap, and it is
# why the walking-stance block had to move as well. If fighting -> walking is
# both buttons, holding both must do *nothing* in walking stance -- otherwise
# button 1 puts you into fighting stance, both buttons take you straight back
# out, and the stance flickers for as long as you hold them. Guarding walking
# on button 2 costs four bytes and there were none spare in the primitive.
#
# **Fix 19, up and left together.** Left alone still walks left, so the gesture
# is the diagonal, and nothing else in the fighting stance reads left with a
# vertical. No guard, and no change to walking either -- but unlike fix 18 it
# is still a direction, so it is one bad diagonal away from a stand-down rather
# than none.
WALK18, FIGHT18, FIGHT19 = 0xA780, 0xA7C0, 0xA800

WALK18_CODE = [
    0xA5, 0x09, 0x30, 0x08, 0xA5, 0x08, 0x30, 0x0F, 0xA5, 0xA3,
    0x30, 0x0B, 0xA5, 0xA2, 0xC9, 0x01, 0xD0, 0x02, 0xA0, 0x01,
    0x4C, 0x7B, 0x78, 0xA0, 0x02, 0x4C, 0x7B, 0x78,
]
FIGHT18_CODE = [
    0xA5, 0x08, 0x25, 0x09, 0x30, 0x1F, 0xA5, 0xA2, 0xC9, 0x01,
    0xD0, 0x0D, 0xA5, 0x08, 0x30, 0x1A, 0xA5, 0x09, 0x30, 0x1B,
    0xA0, 0x05, 0x4C, 0x7B, 0x78, 0xA5, 0xA2, 0x30, 0x03, 0x4C,
    0x7B, 0x78, 0xA0, 0x04, 0x4C, 0x7B, 0x78, 0xA0, 0x03, 0x4C,
    0x7B, 0x78, 0xA0, 0x06, 0x4C, 0x7B, 0x78, 0xA0, 0x07, 0x4C,
    0x7B, 0x78,
]
FIGHT19_CODE = [
    0xA5, 0xA2, 0xC9, 0x01, 0xD0, 0x0D, 0xA5, 0x08, 0x30, 0x1E,
    0xA5, 0x09, 0x30, 0x1F, 0xA0, 0x05, 0x4C, 0x7B, 0x78, 0xA5,
    0xA2, 0x10, 0x09, 0xA5, 0xA3, 0x30, 0x08, 0xA0, 0x04, 0x4C,
    0x7B, 0x78, 0x4C, 0x7B, 0x78, 0xA0, 0x03, 0x4C, 0x7B, 0x78,
    0xA0, 0x06, 0x4C, 0x7B, 0x78, 0xA0, 0x07, 0x4C, 0x7B, 0x78,
]

# $7833-$7849 is the walking-stance block and $784A-$787A the fighting one.
# Where a variant moves a block out, what is left behind is a jump and padding.
WALKING_BLOCK = MAP2_NOW[:23]
MAP18_NOW = ([0x4C, WALK18 & 0xFF, WALK18 >> 8] + [0xEA] * 20
             + [0x4C, FIGHT18 & 0xFF, FIGHT18 >> 8] + [0xEA] * 46)
MAP19_NOW = (WALKING_BLOCK
             + [0x4C, FIGHT19 & 0xFF, FIGHT19 >> 8] + [0xEA] * 46)


def _remap_common(p):
    """What all three mappings share: the height word and free movement."""
    p.put(HEIGHT, [(HEIGHT + 2) & 0xFF, (HEIGHT + 2) >> 8] + HEIGHT_CODE,
          expect=[0x00] * (2 + len(HEIGHT_CODE)))
    for cell in HEIGHT_CELLS:
        was = p.peek(cell, 2)
        if was not in (bytes([P_LEFT & 0xFF, P_LEFT >> 8]),
                       bytes([P_RIGHT & 0xFF, P_RIGHT >> 8])):
            raise SystemExit("at $%04X expected a height word and found %s"
                             % (cell, was.hex()))
        p.put(cell, [HEIGHT & 0xFF, HEIGHT >> 8], expect=list(was))
    for base, code in ((WALKL, WALKL_CODE), (WALKR, WALKR_CODE)):
        p.put(base, [(base + 2) & 0xFF, (base + 2) >> 8] + code,
              expect=[0x00] * (2 + len(code)))
    for cells, was, now in ((WALKL_CELLS, P_WALKL, WALKL),
                            (WALKR_CELLS, P_WALKR, WALKR)):
        for c in cells:
            p.put(c, [now & 0xFF, now >> 8], expect=[was & 0xFF, was >> 8])
    return p


def fix_remap_bothbuttons(p):
    """Fix 11, with both buttons together to stand down and no guard."""
    p.put(MAP2_AT, MAP18_NOW, expect=MAP2_WAS)
    p.put(WALK18, WALK18_CODE, expect=[0x00] * len(WALK18_CODE))
    p.put(FIGHT18, FIGHT18_CODE, expect=[0x00] * len(FIGHT18_CODE))
    return _remap_common(p)


def fix_remap_upleft(p):
    """Fix 11, with up and left together to stand down and no guard."""
    p.put(MAP2_AT, MAP19_NOW, expect=MAP2_WAS)
    p.put(FIGHT19, FIGHT19_CODE, expect=[0x00] * len(FIGHT19_CODE))
    return _remap_common(p)


def fix_the_lot_4(p):
    """Cadence, reach, knockback, and the both-buttons mapping."""
    fix_half_the_waits(p)
    fix_generous_reach(p)
    fix_remap_bothbuttons(p)
    fix_knockback_hard(p)
    return p



# ----------------------------------------------------------------- fixes 21-23
# Three knobs that are two cells or eight, found by reading the thread rather
# than by moving code anywhere.
#
# **Walk speed.** Commands 4 and 5 install a four-step chain that sets $AA to
# -2 or +2 each step, so a walk cycle covers eight units. That is the "slog to
# reposition" everybody feels, and it is separate from the loop cadence: it can
# be changed without the fight getting faster with it, which is the trade every
# dose makes. Eight cells, four each way.
#
# The thing to watch is the feet. The stride is drawn for eight units a cycle,
# so a faster walk slides, and past some point it stops looking like walking.
# That is a judgement nobody can make from a listing.
#
# **Strike repeat.** Both attack chains count to six and then re-poll the stick
# to decide whether to strike again -- w_753E for the punch family, w_7670 for
# the kick. Lower it and a held strike repeats sooner. Two cells.
#
# **The AI's separation test.** w_9866 works out goonX - playerX and compares it
# against 15. That is the opponent deciding something about distance, and it is
# the most likely home of the retreating that makes closing such work. One
# cell, and the one to be careful with: it is the only item here that changes
# what the opponent *decides* rather than how fast something moves.
WALK_LEFT_CELLS = [0x7436, 0x7450, 0x746A, 0x7484]
WALK_RIGHT_CELLS = [0x7280, 0x729A, 0x72B4, 0x72CE]
REPEAT_CELLS = [0x7554, 0x7686]
AI_GAP_CELL = 0x9892


def _walk_speed(p, units):
    for c in WALK_LEFT_CELLS:
        p.put(c, [(-units) & 0xFF, ((-units) >> 8) & 0xFF], expect=[0xFE, 0xFF])
    for c in WALK_RIGHT_CELLS:
        p.put(c, [units, 0x00], expect=[0x02, 0x00])
    return p


def fix_walk_brisk(p):
    """Three units a step instead of two: half again as fast to reposition."""
    return _walk_speed(p, 3)


def fix_walk_fast(p):
    """Four units a step -- double, and the point where the feet may give up."""
    return _walk_speed(p, 4)


def fix_strike_repeat(p):
    """Re-poll after four frames instead of six, so a held strike repeats sooner."""
    for c in REPEAT_CELLS:
        p.put(c, [0x04, 0x00], expect=[0x06, 0x00])
    return p


def fix_ai_close(p):
    """Make the opponent's distance test fire at 10 units instead of 15.

    The least certain thing built here. `w_9866` computes goonX - playerX and
    compares it against 15, and what it does either side of that has not been
    read -- only that it is the game's own notion of "how far apart are we",
    and that a game whose opponent backs off is a game where that number is
    doing something.

    Play it against the stock behaviour before believing anything about it. If
    the opponent gets *more* passive rather than less, the comparison runs the
    other way and the number wants raising, not lowering.
    """
    p.put(AI_GAP_CELL, [0x0A, 0x00], expect=[0x0F, 0x00])
    return p


# -------------------------------------------------------------------- fix 25
# Not a fix: a way to reach the fight with the bird in it.
#
# `w_A52A` is a CASE on $18AA, the encounter number, and its arms are one cell
# each:
#
#     $18AA == 0 -> w_A1D0   encounter 1
#              1 -> w_A222   2
#              2 -> w_A274   3
#              3 -> w_A2C6   4      <- the bird
#              4 -> w_A318   5
#              5 -> w_A36A   6
#
# Each encounter word then sets $18AA to its own number, so the chain runs
# itself. Pointing the *first* arm at encounter 4 starts the game there and
# leaves the rest of the sequence intact: w_A2C6 sets $18AA to 4, the next
# dispatch matches 4, and encounters 5 and 6 follow as they always did.
#
# One cell. It exists because a measurement was made over a window that did
# not include this fight -- `Find a free byte.bat` reported three untouched
# bytes across 166 seconds of play that never got past encounter 3, and a
# negative is only as good as the window it was taken over. Re-run the probe
# on this build and the window covers the bird.
DISPATCH_FIRST_ARM = 0xA53C
ENCOUNTER_1, ENCOUNTER_4 = 0xA1D0, 0xA2C6


def fix_start_at_four(p):
    """Start the game at the fourth encounter, where the bird is.

    A test build, and it should never be combined with anything you intend to
    judge on feel: the first three fights are where the pacing complaints came
    from, and this skips them.
    """
    p.put(DISPATCH_FIRST_ARM, [ENCOUNTER_4 & 0xFF, ENCOUNTER_4 >> 8],
          expect=[ENCOUNTER_1 & 0xFF, ENCOUNTER_1 >> 8])
    return p


# -------------------------------------------------------------------- fix 26
# A strike rhythm that is not a metronome.
#
# Fix 23 moved the gate from six to four, which is a different tempo and still
# a uniform one -- and uniformity was the complaint. The 8-bit version was
# described as falling into a 3-1-1 pattern, audible rather than merely felt.
#
# Both attack chains end with
#
#     $191A C@  1+  DUP  $191A C!  LIT $0006  =  0BRANCH ...
#
# so the gate is one comparison against a constant. This replaces the three
# cells `LIT $0006 =` with a word that compares against a three-entry table and
# steps through it, and a BRANCH over the two cells that are left -- the branch
# lands exactly where execution would have gone anyway, so it costs nothing.
#
# The table is 4, 2, 2, and the arithmetic is worth stating because it caught
# me out. $191A is reset to **1** when a strike starts and the gate fires when
# it *reaches* the table value, so a value of n means n-1 calls. The stock 6 is
# five strikes before the pause, which is what a person watching it counted;
# 4, 2, 2 is three then one then one, the 3-1-1 that was heard on the 8-bit.
#
# ## The byte
#
# The phase lives in $DC, and finding a byte to put it in was most of the work.
# Nothing in the ROM names $DC; it sits above the data stack's base of $CF
# (`LDX #$CF` at $408F); and two probe runs -- 9,961 frames over encounters 1-3
# and 2,471 more starting at encounter 4, so the bird is covered -- saw no
# writes to it at all, against a control that saw 3.9 million writes to the
# interpreter's own thread pointer.
#
# $D7 and $F9 were equally clean and are not used, because both can be reached
# by an indexed store at a stack depth of 2 and $DC's shallowest route needs a
# depth of 8. When three candidates are equally clean, take the one that is
# hardest to reach by accident.
GATE = 0xA8C0
GATE_CELLS = [0x7552, 0x7684]      # the LIT before each $0006
BRANCH_WORD_G = 0x4D56

GATE_CODE = [
    0xB5, 0x00, 0xA4, 0xDC, 0xD9, 0xDA, 0xA8, 0xD0, 0x0C, 0xC8,
    0xC0, 0x03, 0x90, 0x02, 0xA0, 0x00, 0x84, 0xDC, 0x4C, 0x42,
    0x40, 0x4C, 0x56, 0x40, 0x04, 0x02, 0x02,
]


def fix_strike_rhythm(p):
    """Long, short, short instead of tick, tick, tick.

    The table holds what the counter is *compared against*, and the counter
    starts at 1, so an entry of n runs for n-1 calls. 4, 2, 2 is three, one,
    one. The stock value of 6 is five, which is what counting the strikes in
    the original gives -- and is how the off-by-one in an earlier version of
    this comment got found.

    Not measured. Three bytes at the tail of GATE_CODE change the pattern.
    """
    p.put(GATE, [(GATE + 2) & 0xFF, (GATE + 2) >> 8] + GATE_CODE,
          expect=[0x00] * (2 + len(GATE_CODE)))
    for c in GATE_CELLS:
        # LIT $0006 =   becomes   gate BRANCH +2, which lands where the LIT
        # would have left execution anyway
        p.put(c, [GATE & 0xFF, GATE >> 8,
                  BRANCH_WORD_G & 0xFF, BRANCH_WORD_G >> 8,
                  0x02, 0x00],
              expect=None)
    return p



# -------------------------------------------------------------------- fix 27
# The staged first step of disconnecting the loops.
#
# `EXECUTE` does two things at once: it advances an entity one animation step
# and it is the only moment that entity can decide anything. The loop gives
# each of nine entities one call per round with a frame of padding apiece, so
# a round is thirteen frames and *everything* -- movement, animation, AI, and
# how fast the game notices the stick -- is measured in rounds.
#
# This gates the two apart in the cheapest way that can be played. Every wait
# is skipped, so the loop spins at about five frames, and the entities are
# executed on one round in three, so an animation step lands every fifteen.
#
# **That is slower than stock, not faster** -- fifteen frames against thirteen.
# The point of the stage is to answer one question, "does gating the entities
# break the game", without the answer being confounded by everything also
# running quick. If it plays like itself, the loop is then free to run at
# whatever rate the controls want, and the player's dispatcher can be exempted
# from the gate so reactions land in five frames while animation stays at
# fifteen. That exemption is the next step and this one has to survive first.
#
# Only the nine EXECUTEs in the main loop are repointed. There are three more
# in the image -- w_4142, w_514C and w_5AC4 -- and they have nothing to do with
# the entity round.
LOOPGATE = 0xA8E0
LOOPGATE_CELLS = [0xA5C8, 0xA5DE, 0xA5F4, 0xA60A, 0xA620,
                  0xA636, 0xA64C, 0xA662, 0xA678]
P_EXECUTE = 0x513C

LOOPGATE_CODE = [
    0xA5, 0xD7, 0x18, 0x69, 0x10, 0xC9, 0x90, 0x90, 0x0B, 0x29,
    0x0F, 0x18, 0x69, 0x01, 0xC9, 0x03, 0x90, 0x02, 0xA9, 0x00,
    0x85, 0xD7, 0x29, 0x0F, 0xF0, 0x05, 0xE8, 0xE8, 0x4C, 0x1E,
    0x40, 0xB5, 0x00, 0x85, 0xEB, 0xB5, 0x01, 0x85, 0xEC, 0xE8,
    0xE8, 0x4C, 0xEA, 0x00,
]


def fix_loop_gate(p):
    """Spin the loop at five frames; animate every third round.

    Not measured and frankly the most likely of anything here to misbehave:
    it is the first change that moves every entity's timing at once, and
    anything quietly assuming a thirteen-frame round -- sound cadence, the
    fight timer, cutscene lengths, the AI's own counters -- moves with it.
    Nothing in the listing announces such an assumption.

    If the game plays roughly like itself but a shade slow, the gate works and
    the ratio wants tuning. If something specific goes wrong -- music dragging,
    a timer that never expires -- that is the assumption showing itself, and it
    is worth more than a smooth result would be.
    """
    fix_input_latch(p)
    _skip_waits(p, range(len(WAIT_BLOCKS)))
    p.put(LOOPGATE,
          [(LOOPGATE + 2) & 0xFF, (LOOPGATE + 2) >> 8] + LOOPGATE_CODE,
          expect=[0x00] * (2 + len(LOOPGATE_CODE)))
    for c in LOOPGATE_CELLS:
        p.put(c, [LOOPGATE & 0xFF, LOOPGATE >> 8],
              expect=[P_EXECUTE & 0xFF, P_EXECUTE >> 8])
    return p



# ----------------------------------------------------------------- fixes 28-29
# The staged gate worked, so this is the tuning and then the point of it.
#
# Fix 27 ran a five-frame round with a 3:1 gate: an animation step every fifteen
# frames against the stock thirteen. Played, nothing assumed a thirteen-frame
# round -- no dragging music, no timer that never expired -- and it read as "a
# touch slow", which is what fifteen against thirteen should read as.
#
# **Fix 28** keeps one wait, so the round is six frames, and gates 2:1: a step
# every twelve. A shade the other side of stock, and one bit of phase instead
# of two.
#
# **Fix 29** is fix 28 with the player's dispatcher exempt. `w_7898` reads the
# controls and turns them into a command, and it is installed into slot $1878
# and nowhere else -- eight sites, all the player's -- so exempting it by
# address is exactly "let the player decide every round" with no way to catch
# an opponent by accident.
#
# Animation stays at twelve frames. Reactions land in six. That is the thing
# the whole exercise was for: responsiveness and game speed, finally two
# different numbers.
GATE2, GATE3 = 0xA910, 0xA950

GATE2_CODE = [
    0xA5, 0xD7, 0x18, 0x69, 0x10, 0xC9, 0x90, 0x90, 0x04, 0x29,
    0x0F, 0x49, 0x01, 0x85, 0xD7, 0x29, 0x0F, 0xF0, 0x05, 0xE8,
    0xE8, 0x4C, 0x1E, 0x40, 0xB5, 0x00, 0x85, 0xEB, 0xB5, 0x01,
    0x85, 0xEC, 0xE8, 0xE8, 0x4C, 0xEA, 0x00,
]
GATE3_CODE = [
    0xA5, 0xD7, 0x18, 0x69, 0x10, 0xC9, 0x90, 0x90, 0x04, 0x29,
    0x0F, 0x49, 0x01, 0x85, 0xD7, 0xB5, 0x01, 0xC9, 0x78, 0xD0,
    0x06, 0xB5, 0x00, 0xC9, 0x98, 0xF0, 0x0B, 0xA5, 0xD7, 0x29,
    0x0F, 0xF0, 0x05, 0xE8, 0xE8, 0x4C, 0x1E, 0x40, 0xB5, 0x00,
    0x85, 0xEB, 0xB5, 0x01, 0x85, 0xEC, 0xE8, 0xE8, 0x4C, 0xEA,
    0x00,
]


def _install_gate(p, where, code):
    fix_input_latch(p)
    _skip_waits(p, range(len(WAIT_BLOCKS) - 1))     # keep the last one
    p.put(where, [(where + 2) & 0xFF, (where + 2) >> 8] + code,
          expect=[0x00] * (2 + len(code)))
    for c in LOOPGATE_CELLS:
        p.put(c, [where & 0xFF, where >> 8],
              expect=[P_EXECUTE & 0xFF, P_EXECUTE >> 8])
    return p


def fix_loop_gate_tuned(p):
    """Six-frame round, entities every other round: a step every 12 frames."""
    return _install_gate(p, GATE2, GATE2_CODE)


def fix_loop_gate_player(p):
    """As fix 28, and the player decides every round instead of every other.

    The counter is advanced before the exemption is tested. Skipping the
    advance for the exempt call would make a round eight counted calls instead
    of nine, and the phase would drift further every round until the gate meant
    nothing -- the sort of thing that would have looked like "it stopped
    working after a while".
    """
    return _install_gate(p, GATE3, GATE3_CODE)



# -------------------------------------------------------------------- fix 30
# A longer stage, and the reason it is only two bytes.
#
# $0070 is not the edge of the world. It is the value `w_A4D2` compares the
# player's X against to decide the stage is cleared -- a trigger part way
# along, not a wall. The coordinate space runs much further:
#
#     A488  LDA $186D
#     A48B  CMP #$A0        the player reaches 160...
#     A48D  BCS $A490
#     A490  LDA #$EB
#     A492  STA $186D       ...and is put at 235
#     A495  INC $18DC
#     A498  LDA #$77 / STA $1879
#     A49D  LDA #$50 / STA $1878     a new behaviour word
#
# So the game already moves the player between 0 and 235 and only ends the
# stage at 112. Whether the *artwork* carries on past 112 is the open question
# and nothing in the listing answers it -- but moving the trigger is one cell,
# so the experiment costs less than the analysis would.
#
# $90 keeps it clear of the $A0 transition with room to spare. If the
# background runs out, it will be obvious immediately.
EXIT_CELL = 0xA4DC
EXIT_STOCK, EXIT_LONG = 0x0070, 0x0090


def fix_longer_stage(p):
    """Move the stage-clear line from 112 to 144.

    Untested, and the one thing that could make it pointless is not in the
    ROM's logic at all: if the background is only drawn out to 112, walking
    past it scrolls into whatever happens to be next in memory. That shows up
    in the first second of play, which is why this is worth building rather
    than reasoning about.

    If it is combined with knockback, the resistance line in `_shove` wants
    moving too -- it is $6F, chosen to sit just under the old exit, and it will
    hold the goon back at the wrong place otherwise. That is one byte in
    `shove_b3_*.asm`.
    """
    p.put(EXIT_CELL, [EXIT_LONG & 0xFF, EXIT_LONG >> 8],
          expect=[EXIT_STOCK & 0xFF, EXIT_STOCK >> 8])
    return p


# -------------------------------------------------------------------- fix 32
# The last of the nine waits, made conditional on whether it has anything to
# wait for.
#
# Slot 8, $18DF, is not a subsystem. It is a one-shot bridge: at the top of
# w_A59C -- once, when the word is entered, not on every round; the loop's own
# BRANCH lands at $A5AA, past this reset -- $18DF is set to w_9908, a word
# whose entire body is EXIT and nothing else. Slot 8's own EXECUTE, later in
# the SAME pass through the loop, runs whatever is there.
#
# What can put something else there is w_881A, the opponent's dispatcher,
# which runs in slot 4 -- *earlier* in the same round. If the opponent decides
# to attack this round, its dispatcher installs the attack's first behaviour
# word into $18DF right then, so slot 8 can take that word's first animation
# step in the SAME round rather than the next one. Two self-reinstalling
# chains do this: $8D32<->$8D68 and $8DBC<->$8DDE(<-$8DAC), each advancing one
# more step of a punch or a kick and putting itself straight back into $18DF
# for the following round -- which is why the reset at the top only runs once:
# an attack's state has to survive across rounds, and it does.
#
# The entity probe measured this directly: over a real fight, slot 8 held
# w_9908 -- nothing to do -- for 87.5% of the rounds. The wait after it
# (WAIT_BLOCKS[7], the eighth and last) is spent regardless, every round,
# whether or not there was anything to wait *for*.
#
# This makes that one wait conditional: read $18DF, compare it to $9908, and
# skip the wait only when they are equal. Every other wait is untouched, and
# so is the EXECUTE that precedes it -- the check has to still run every round
# to catch the round the opponent starts something.
#
#     average round length = 13 - 0.875 = 12.125 frames  (stock: 13)
#
# a touch under 7% off the top, for free, on top of anything else applied --
# except doses 6, 7 and 8, which already touch this exact wait unconditionally
# and would conflict with it rather than combine.
SLOT8_GATE = 0xA990
SLOT8_GATE_CELLS = [
    0x4C7E, 0x18DF, 0x5250, 0x4C7E, 0x9908, 0x4FEC, 0x4D6C, 0x0006,
    0x4D56, 0xFCD0, 0x596A, 0x503C, 0x4D6C, 0xFFFA, 0x596A, 0x4D6C,
    0xFFFC, 0x4D56, 0xFCBE,
]
SLOT8_WAIT_AT = 0xA664
BRANCH_WORD_8 = 0x4D56


def fix_slot8_gate(p):
    """Skip the eighth wait when the opponent has nothing queued in $18DF.

    Verified against the ROM before building: the branch that ends w_A59C
    targets $A5AA, after the one-time $18DF reset at the top, so the reset
    really does run once per encounter rather than once per round -- if it
    ran every round this fix would be wrong, because the opponent's attack
    chains reinstall themselves into $18DF for the *next* round, and a
    reset that fired every round would erase that before slot 8 ever saw it.

    Not measured by play; the 87.5% figure is the entity probe's, and the
    12.125-frame average is arithmetic on it, not a capture of this specific
    build.
    """
    cells = SLOT8_GATE_CELLS
    words = bytearray()
    for c in cells:
        words += bytes([c & 0xFF, c >> 8])
    p.put(SLOT8_GATE, list(words), expect=[0x00] * len(words))
    p.put(SLOT8_WAIT_AT, [BRANCH_WORD_8 & 0xFF, BRANCH_WORD_8 >> 8, 0x2A, 0x03],
          expect=[0x6A, 0x59, 0x3C, 0x50])
    return p


def fix_slot8_gate_combined(p):
    """Fix 12 -- cadence, reach, mapping -- plus the slot-8 gate on top.

    Built from fix_the_lot_2 rather than its own choice of reach and mapping,
    so this is recognised as an alternative to fixes 6/8/13/16 on the hit
    window and to fixes 18/19 on control mapping, instead of a raw conflict
    against each of them individually.
    """
    fix_the_lot_2(p)
    fix_slot8_gate(p)
    return p


# -------------------------------------------------------------------- fix 34
# Fix 29 (the loop gate, player exempt) and fix 32 (the slot-8 wait gate),
# combined -- checked, not assumed, after a wrong claim about this exact pair.
#
# _install_gate skips seven of the eight original waits and deliberately
# leaves the eighth in place ("keep the last one"): every entity's EXECUTE is
# repointed to the round-counter gate, but $A664 -- the wait right after slot
# 8 -- is untouched, still full-price, every round, independent of the gate.
# Fix 32 patches exactly that address and nothing else GATE3/LOOPGATE_CELLS
# touch, so the two do not share a byte -- confirmed with --check, not assumed
# from the fact that both are about waits.
#
# They compose semantically as well as at the byte level, and this is worth
# stating rather than taking on faith. After the (now-gated) slot-8 EXECUTE
# finishes -- whether it actually ran this round or was skipped by fix 29's
# own counter -- the thread pointer lands on the very next cell, which is
# fix 32's replacement at $A664. So fix 32 always gets to ask its own
# question -- "is $18DF still idle right now" -- regardless of what fix 29's
# gate decided, and answers it against the state $18DF is *actually* in.
#
# The arithmetic: fix 29 alone leaves one wait in every 6-frame round costing
# a flat frame. Fix 32 makes that one conditional on the same 87.5% idle
# figure the entity probe measured:
#
#     round length = 5 + 0.125 * 1 = 5.125 frames    (fix 29 alone: 6)
#
# Reactions -- the player is exempt from the gate -- land in ~5.125 frames
# instead of 6. Everything else, gated 2:1, animates every ~10.25 instead of
# 12. Smaller than the 13->6 the gate itself bought, which is what
# "diminishing" predicts: the eighth wait was always the smallest single
# piece left to take.
def fix_loop_gate_player_slot8(p):
    fix_loop_gate_player(p)
    fix_slot8_gate(p)
    return p


# -------------------------------------------------------------------- fix 35
# Fix 34 plus a faster walk, because "speed the game up" and "speed reactions
# up" turned out to need answering separately.
#
# Fix 29 (inside fix 34) is not a bigger version of fix 3's 1.44x. It uses the
# same eight physical wait blocks -- there is only one set -- but skips SEVEN
# of them unconditionally where fix 3 skips four, and then adds something
# fix 3 never had: every entity's `EXECUTE` is repointed to a round counter
# that only actually runs 8 of the 9 entities every *other* round. The shorter
# round buys faster decisions; the gate spends that back so animation lands
# close to where it always did. That trade is the entire point of building
# the gate in the first place, so fix 34 alone does not give movement or
# animation back the 1.44x fix 3 did -- it was built specifically not to.
#
# Fixes 21/22 are a different knob entirely: the walking velocity constant
# $AA, written by the movement chains commands 4 and 5 install, nothing to do
# with any wait or any gate. Checked with --check before building this: zero
# bytes in common with fix 34, so there is nothing to reconcile, only two
# independent changes to stack.
# ------------------------------------------------------------------ fix 45
# The one pairing the ranked list asked for and nothing had ever built.
#
# Option 6 says knockback belongs on a faster loop and never on the stock
# cadence, and the reason is structural rather than aesthetic: a shove
# creates distance, and closing distance at 13 frames a decision is exactly
# the slog knockback exists to relieve. Every knockback fix so far --
# 31, 37, 38, 39, 43, 44 -- has been built on the stock cadence, because
# each was made to answer a question about knockback rather than to be
# played. Fix 44 is now confirmed in play and fix 34 is the best cadence
# there is (reactions every ~5 frames against animation every ~10), and
# they have never been in the same ROM.
#
# They cannot collide: the two touch no byte in common -- checked directly
# rather than assumed, since that assumption was wrong once already for
# fix 29 and fix 32.
def fix_tweak(p):
    """The unofficial build: how this port plays once the port's own gaps
    are closed.

    Fix 34's reactions and fix 44's knockback are the two mechanics, and
    the composite the ranked list has been asking for since option 6 was
    written. On top of those, three things this port either got wrong or
    left out:

        fix 11  the controls, remapped so a bare stick walks and the two
                buttons punch and kick at three heights each
        fix 41  the princess's kick, the one rule missing from the ending
        fix 26  held strikes at 3-1-1 instead of evenly spaced
        fix 48  the difficulty switch, working and the right way round

    Byte-independence checked rather than assumed: none of the six shares
    an address with any other.

    Fix 48 is the one that touches the hit window, and it is here anyway
    because it does not touch it *by default*: the switch decides, the way
    the console's front panel says it should. Leave it in A -- the expert
    position, and where the game has effectively always been -- and the
    windows are what they always were. Move it to B and the player gets
    parity. Fixes 5 and 6, which change the window outright and for
    everyone, stay out.

    Note for anyone changing fix 11 and fix 41 together: the remap changes
    which input produces the stance command, not the command, so the two
    cells fix 41 repoints ($7226 and $726A) are still the only path to
    $187C and the stance snapshot still tracks."""
    fix_loop_gate_player_slot8(p)
    fix_knockback_odometer(p)
    fix_remap(p)
    fix_princess_kick(p)
    fix_strike_rhythm(p)
    fix_difficulty_switch_proper(p)
    return p


def fix_tweak_brisk_walk(p):
    """Fix 45 plus fix 21's three-unit walking step."""
    fix_tweak(p)
    fix_walk_brisk(p)
    return p


def fix_tweak_fast_walk(p):
    """Fix 45 plus fix 22's four-unit walking step.

    Fix 22's own warning applies and is worth reading before judging this
    one: the stride is drawn for eight units a cycle, so this may be where
    the feet stop keeping up with the ground."""
    fix_tweak(p)
    fix_walk_fast(p)
    return p


def fix_loop_gate_player_slot8_brisk_walk(p):
    """Fix 34's reaction speed, plus fix 21's three-unit walking step."""
    fix_loop_gate_player_slot8(p)
    fix_walk_brisk(p)
    return p


def fix_loop_gate_player_slot8_fast_walk(p):
    """Fix 34's reaction speed, plus fix 22's four-unit (double) walking step."""
    fix_loop_gate_player_slot8(p)
    fix_walk_fast(p)
    return p

FIXES = [
    {
        "n": 1,
        "slug": "input-latch",
        "title": "remember a press until the game reads it",
        "governs": "input latching",
        "apply": fix_input_latch,
        "measured": "33 ms tap seen 16% -> 46%; the directions benefit, the "
                    "buttons do not -- see fix 2",
    },
    {
        "n": 3,
        "slug": "half-the-waits",
        "title": "update two entities per frame instead of one (includes 1)",
        "governs": "wait dose", "parts": [1],
        "apply": fix_half_the_waits,
        "measured": "the main loop's period falls from 13 frames to 9 "
                    "(medians, from a recorded session on each build). Each "
                    "wait skipped is worth exactly one frame, so the game "
                    "thinks 1.44x as often -- and moves 1.44x as fast",
    },
    {
        "n": 4,
        "slug": "fewer-waits",
        "title": "a gentler version of fix 3 (includes 1)",
        "governs": "wait dose", "parts": [1],
        "apply": fix_fewer_waits,
        "measured": "not measured, but predictable: a skipped wait is "
                    "worth one frame, so two of them should give an 11-frame "
                    "loop against the original's 13 and fix 3's 9",
    },
    {
        "n": 5,
        "slug": "even-reach",
        "title": "give both fighters the same hit window",
        "governs": "hit window",
        "apply": fix_even_reach,
        "measured": "not measured -- it wants playing. The asymmetry is not "
                    "an inference though: one threshold is 8 or 9 and the "
                    "other is 10 in all six encounters. The difficulty "
                    "switch was meant to close that gap and never has "
                    "-- see fix 17",
    },
    {
        "n": 6,
        "slug": "generous-reach",
        "title": "fix 5, plus two units of slack for both",
        "governs": "hit window",
        "apply": fix_generous_reach,
        "measured": "not measured. Ten units may simply be tight; this exists "
                    "so that can be felt rather than argued",
    },
    {
        "n": 7,
        "slug": "combined",
        "title": "fix 3 and fix 5 together -- the one to play",
        "parts": [3, 5],
        "apply": fix_everything,
        "measured": "the parts are measured separately: a 9-frame loop "
                    "instead of 13, and a hit window that is the same size "
                    "for both fighters. They touch different code and do not "
                    "interact",
    },
    {
        "n": 8,
        "slug": "combined-generous",
        "title": "fix 3 and fix 6 -- the roomier one to play against fix 7",
        "parts": [3, 6],
        "apply": fix_everything_generous,
        "measured": "not measured beyond its parts. Play it against fix 7 "
                    "back to back; the only difference is two units of reach "
                    "for both fighters",
    },
    {
        "n": 9,
        "slug": "stance-on-the-stick",
        "title": "up and down change stance; left walks too",
        "governs": "control mapping",
        "apply": fix_stance_on_the_stick,
        "withdrawn": True,
        "measured": 'WITHDRAWN. It put stance change on down in fighting stance, and down is height 3 -- low punch and low kick. It destroyed two of the six attacks. Superseded by fix 11.',
    },
    {
        "n": 10,
        "slug": "everything",
        "title": "fixes 3, 6 and 9 -- cadence, reach and mapping",
        "parts": [3, 6, 9],
        "apply": fix_the_lot,
        "withdrawn": True,
        "measured": 'WITHDRAWN: it contains fix 9. Use fix 12.',
    },
    {
        "n": 11,
        "slug": "remap",
        "title": "bare stick moves, buttons strike (replaces fix 9)",
        "governs": "control mapping",
        "apply": fix_remap,
        "measured": "not measured. The mapping it replaces is read out byte "
                    "for byte above the patch, and all 72 original bytes are "
                    "checked before writing",
    },
    {
        "n": 12,
        "slug": "everything",
        "title": "fixes 3, 6 and 11 -- cadence, reach and mapping",
        "parts": [3, 6, 11],
        "apply": fix_the_lot_2,
        "measured": "the parts are separate; the combination is not measured. "
                    "This is the one to play if only one gets played",
    },
    {
        "n": 13,
        "slug": "player-reach",
        "title": "give the $18C3 fighter more reach -- a test, not a fix",
        "governs": "hit window",
        "apply": fix_player_reach,
        "measured": "nothing yet: this exists to be played once. If your "
                    "strikes suddenly connect from further, the player is the "
                    "$18C3 fighter and did have the smaller window. If the "
                    "opponent starts reaching you, the inference was backwards",
    },
    {
        "n": 14,
        "slug": "knockback",
        "title": "push the struck fighter back four units",
        "governs": "knockback",
        "apply": fix_knockback,
        "measured": "nothing yet. The 8-bit version has knockback and this one "
                    "has none; whether the animation script erases the shove "
                    "before it is drawn is the thing to find out",
    },
    {
        "n": 15,
        "slug": "knockback-hard",
        "title": "eight units -- a whole walking step given back",
        "governs": "knockback",
        "apply": fix_knockback_hard,
        "measured": "nothing yet. Try this one first: if eight units does "
                    "nothing visible, four never would either, and the answer "
                    "is that the base gets rewritten every update",
    },
    {
        "n": 16,
        "slug": "everything-knockback",
        "title": "fixes 3, 6, 11 and 15 -- the whole thing with knockback",
        "parts": [3, 6, 11, 15],
        "apply": fix_the_lot_3,
        "measured": "the parts are separate; the combination is not measured. "
                    "Knockback pushes fighters apart, so it needs the shorter "
                    "loop underneath it or closing again is the old slog",
    },
    {
        "n": 48,
        "slug": "difficulty-switch-proper",
        "title": "the difficulty switch working, and the right way round",
        "governs": "difficulty switch",
        "apply": fix_difficulty_switch_proper,
        "measured": "the bug is not in doubt and neither is the sense. The "
                    "stock test is SWCHB = $80, which needs Reset, Select "
                    "and Pause held together, so neither adjustment has "
                    "ever run; fix 17 makes it reachable but leaves A -- "
                    "the expert position on every Atari console -- as the "
                    "kind one. This inverts the test, so B equalises the "
                    "two hit windows at 9 and 9 and A leaves the player on "
                    "8 or 9 against the opponent's 10. The direction of the "
                    "adjustment is the designers' and is untouched; all "
                    "that changes is which position asks for it. Costs one "
                    "comparison word the image does not have, which reuses "
                    "the game's own flag-pushers at $403B and $404F so it "
                    "never touches X.",
    },
    {
        "n": 17,
        "slug": "difficulty-switch",
        "title": "make the right difficulty switch work at all -- "
                 "see also fix 48, which also gets the sense right",
        "governs": "difficulty switch",
        "apply": fix_difficulty_switch,
        "measured": "not measured, but the bug is not in doubt: the test is "
                    "SWCHB = $80, which needs Reset, Select and Pause held "
                    "together, so neither adjustment has ever run. With this, "
                    "position A equalises the two hit windows at 9",
    },
    {
        "n": 18,
        "slug": "remap-bothbuttons",
        "title": "fix 11, but both buttons stand you down",
        "governs": "control mapping",
        "apply": fix_remap_bothbuttons,
        "measured": "not measured. Up and down stop meaning anything except a "
                    "strike height, so no order of releasing things can drop "
                    "you out of stance -- the guard is gone rather than "
                    "tightened",
    },
    {
        "n": 19,
        "slug": "remap-upleft",
        "title": "fix 11, but up and left together stand you down",
        "governs": "control mapping",
        "apply": fix_remap_upleft,
        "measured": "not measured. A diagonal nothing else uses, and no guard "
                    "-- but still a direction, so still one bad push away",
    },
    {
        "n": 20,
        "slug": "everything-bothbuttons",
        "title": "fixes 3, 6, 15 and 18",
        "parts": [3, 6, 15, 18],
        "apply": fix_the_lot_4,
        "measured": "not measured as a whole; fix 16 with the both-buttons "
                    "mapping in place of fix 11",
    },
    {
        "n": 21, "slug": "walk-brisk", "governs": "walk speed",
        "title": "walk three units a step instead of two",
        "apply": fix_walk_brisk,
        "measured": "not measured. Repositioning gets half again as quick "
                    "without the fight speeding up with it, which is the trade "
                    "every wait dose makes. Watch the feet",
    },
    {
        "n": 22, "slug": "walk-fast", "governs": "walk speed",
        "title": "walk four units a step -- double",
        "apply": fix_walk_fast,
        "measured": "not measured. The stride is drawn for eight units a "
                    "cycle, so this is where the feet probably stop keeping up",
    },
    {
        "n": 23, "slug": "strike-repeat", "governs": "strike repeat",
        "title": "a held strike repeats after four frames, not six",
        "apply": fix_strike_repeat,
        "measured": "not measured. Two cells: both attack chains count to six "
                    "before re-polling the stick",
    },
    {
        "n": 24, "slug": "ai-close", "governs": "opponent distance",
        "title": "the opponent's distance test fires at 10 instead of 15",
        "apply": fix_ai_close,
        "measured": "not measured, and the least certain thing here -- what "
                    "the opponent does either side of that number has not been "
                    "read. If it gets more passive, the number wants raising",
    },
    {
        "n": 25, "slug": "start-at-four", "governs": "starting encounter",
        "title": "start at the fourth fight -- a test build, not a fix",
        "apply": fix_start_at_four,
        "measured": "one cell. It exists so a probe can be run over a window "
                    "that includes the bird, which no session so far has "
                    "reached",
    },
    {
        "n": 26, "slug": "strike-rhythm", "governs": "strike repeat",
        "title": "held strikes go long-short-short instead of even",
        "apply": fix_strike_rhythm,
        "measured": "not measured. The table is 4/2/2, and an entry of n "
                    "runs for n-1 calls because the counter starts at 1, "
                    "so the pattern is 3-1-1 -- one long strike then two "
                    "quick ones, the ratio the 8-bit version was heard "
                    "doing. An earlier version of this note said 6/2/2, "
                    "which was the figure from before the off-by-one was "
                    "found; the correction reached the docstring and not "
                    "here. Three bytes at the tail of GATE_CODE change "
                    "the shape.",
    },
    {
        "n": 27, "slug": "loop-gate", "governs": "wait dose",
        "title": "five-frame loop, entities on one round in three -- WITHDRAWN, superseded",
        "withdrawn": True,
        "apply": fix_loop_gate,
        "measured": "WITHDRAWN once its job was done. Played and reported: "
                    "nothing timing-dependent broke, controls felt more "
                    "responsive, pace read as a touch slow -- which is what "
                    "confirmed the gate itself was sound and cleared fix 28 "
                    "and fix 29 to be built. Fix 29 is strictly better at the "
                    "same idea, and keeping fix 27 live only left it "
                    "conflicting with fix 32 over the one wait both touch.",
    },
    {
        "n": 28, "slug": "loop-gate-tuned", "governs": "wait dose",
        "title": "six-frame loop, entities every other round",
        "apply": fix_loop_gate_tuned,
        "measured": "not measured. A step every 12 frames against the stock "
                    "13, where fix 27 gave 15 and read as a touch slow",
    },
    {
        "n": 29, "slug": "loop-gate-player", "governs": "wait dose",
        "title": "fix 28, and the player decides every round",
        "apply": fix_loop_gate_player,
        "measured": "not measured. Animation every 12 frames, reactions every "
                    "6 -- the point of the whole exercise, and the first build "
                    "where responsiveness and game speed are separate numbers",
    },
    {
        "n": 30, "slug": "longer-stage", "governs": "stage length",
        "title": "end the stage at 144 instead of 112",
        "apply": fix_longer_stage,
        "withdrawn": True,
        "measured": 'WITHDRAWN. It broke at once: the defeated goon sat mid-stage, the camera stopped, and the player walked into an invisible barrier on the right. The barrier was always there -- the $70 trigger fired before anyone could reach it -- so moving the trigger past it just made the stage unfinishable.',
    },
    {
        "n": 31, "slug": "knockback-light", "governs": "knockback",
        "title": "two units -- a quarter of a walking step",
        "apply": fix_knockback_light,
        "measured": "not measured. A walking step covers 8 units over four "
                    "frames, so this is a nudge where fix 15 gives the whole "
                    "step back",
    },
    {
        "n": 37, "slug": "knockback-light-bounded", "governs": "knockback",
        "title": "fix 31, with the world shove stopped at the pillars' home -- WITHDRAWN, superseded",
        "withdrawn": True,
        "apply": fix_knockback_light_bounded,
        "measured": "the bound is measured, the feel is not. 217 of 217 "
                    "scene placements put PILLAR_FAR's head at $A0 and "
                    "PILLAR_NEAR's at $AF, so $A0 is the world's home rather "
                    "than a guess; a traced session showed fix 31 walking the "
                    "far pillar 160 -> 196 over nineteen hits with no scroll "
                    "in between, which is what this stops. Whether absorbing "
                    "those hits feels right is a play question",
    },
    {
        "n": 38, "slug": "knockback-light-capped", "governs": "knockback",
        "title": "fix 31, with a world-drift cap instead of fix 37's bound -- WITHDRAWN, superseded",
        "withdrawn": True,
        "apply": fix_knockback_light_capped,
        "measured": "fix 37's own bound turned out to be broken by "
                    "construction -- PILLAR_FAR's placement value and its "
                    "ceiling are the same $A0, so every fight starts with "
                    "zero headroom and it does nothing. This bounds the "
                    "shove's own cumulative contribution since the arena "
                    "was last placed (tracked in two newly-found free "
                    "zero-page bytes, $D2/$D3) instead of the object's "
                    "absolute position, since nothing in the stock game "
                    "bounds $188C's position at all -- confirmed by a full "
                    "scan finding zero compare instructions against it "
                    "anywhere in the ROM. Not play-tested yet. SUPERSEDED by fix 44: the game does bound this, just not with a compare -- the hall's travel budget in $18B3-$18BC runs out, and spending that budget gives the same limit without a private counter.",
    },
    {
        "n": 42, "slug": "ending-testbed", "governs": "the ending",
        "title": "fix 41 plus one-hit opponents, to reach the ending fast",
        "apply": fix_ending_testbed,
        "measured": "the parts are measured separately; this is the pair "
                    "built together so the restored kick can be reached in "
                    "a couple of minutes instead of a full playthrough.",
    },
    {
        "n": 41, "slug": "princess-kick", "governs": "the ending",
        "title": "approach the princess in fighting stance and she kills you",
        "apply": fix_princess_kick,
        "measured": "the gap is measured, the restoration is a "
                    "reconstruction. This port's ending works but never "
                    "consults stance, so arriving armed earns the same "
                    "embrace as walking in. Stance cannot simply be read "
                    "at the ending -- every stage start zeroes $187C at "
                    "$A503 -- so the two cells naming p_5280 after $187C "
                    "are repointed to keep the value being stored, at "
                    "$02,X, in $E7. Keeping the outgoing contents instead "
                    "was the first version and inverts the whole ending: "
                    "traced over a run, $E7 then reads 1 through every "
                    "stretch spent walking. The strike is the game's own: "
                    "$6937 forces the player's health $18BF to 1 and "
                    "decrements it, which is what a walking-stance hit "
                    "does. Spark routines ($7A30, $6A9C) and the bird's "
                    "full hit ($8CA8) were all tried first and all do "
                    "nothing at the ending: the sparks carry no damage "
                    "and the ending parks the no-op in both the player "
                    "and collision slots. Verified by screenshot both "
                    "ways on the recording that walks off stage 6: left "
                    "alone $E7 reads 0 and the stock embrace plays; with "
                    "$E7 forced to 1 at the transition the player buckles "
                    "and collapses at the princess's feet over about a "
                    "second, and the game returns to attract."},
    {
        "n": 40, "slug": "glass-jaw", "governs": "opponent hit points",
        "title": "every opponent dies in one hit -- a testing aid",
        "apply": fix_glass_jaw,
        "measured": "not a game change and not meant to ship. The six "
                    "per-stage constants that seed $18C2 (13, 17, 21, 21, "
                    "21, 25) all become 1, so one landed hit drops any "
                    "opponent. Which of w_A0C2's two pushed values reaches "
                    "$18C2 was established by trace, not by reading stack "
                    "order: stage 2 seeds the pair (13, 17) and $18C2 takes "
                    "17. The player's own survival is untouched, so a run "
                    "recorded against this can still be lost.",
    },
    {
        "n": 45, "slug": "tweak",
        "title": "the unofficial build -- reactions, knockback, controls, the kick",
        "parts": [29, 32, 44, 11, 41, 26, 48],
        "apply": fix_tweak,
        "measured": "the parts are measured separately and both are "
                    "confirmed in play; this is the first ROM to hold "
                    "them at once. Option 6 has said since it was written "
                    "that knockback belongs on a faster loop and never on "
                    "the stock cadence -- a shove makes distance, and "
                    "closing it at 13 frames a decision is the slog "
                    "knockback is meant to relieve -- and every knockback "
                    "fix until now was built on the stock cadence because "
                    "each was made to answer a question rather than to be "
                    "played. On top of those it carries fix 11's "
                    "control remap, fix 41's princess kick and fix "
                    "26's 3-1-1 strike rhythm and fix 48's difficulty "
                    "switch. Fixes 5 and 6, which change the hit "
                    "window outright, stay out; fix 48 is in because "
                    "it changes nothing by default -- the switch "
                    "decides, and A, where the game has effectively "
                    "always been, is the stock window. "
                    "Byte-independence checked directly, not assumed: "
                    "no two of the six share an address at all.",
    },
    {
        "n": 46, "slug": "tweak-brisk-walk",
        "title": "fix 45 plus a three-unit walking step",
        "parts": [29, 32, 44, 11, 41, 26, 48, 21],
        "apply": fix_tweak_brisk_walk,
        "measured": "the parts are separate; not measured together. "
                    "Reactions ~5.1 frames, animation ~10.25, walking half "
                    "again as far per step, knockback 8 units over three "
                    "hits -- four different numbers on purpose.",
    },
    {
        "n": 47, "slug": "tweak-fast-walk",
        "title": "fix 45 plus a four-unit (double) walking step",
        "parts": [29, 32, 44, 11, 41, 26, 48, 22],
        "apply": fix_tweak_fast_walk,
        "measured": "the parts are separate; not measured together. Fix "
                    "22's note applies: the stride is drawn for eight "
                    "units a cycle, so this is where the feet may stop "
                    "keeping up. Worth playing beside fix 46 rather than "
                    "instead of it.",
    },
    {
        "n": 44, "slug": "knockback-odometer", "governs": "knockback",
        "title": "3-3-2, and the shove spends the hall's own travel budget",
        "apply": fix_knockback_odometer,
        "measured": "fix 43 plus the accounting it was missing. Every unit "
                    "shoved is now a unit spent out of $18B3-$18BC, the "
                    "five-segment odometer all eight of the game's movement "
                    "dispatchers drive, with $18BD stepped when a segment "
                    "empties -- so the scenery and the game's idea of where "
                    "the scenery is stop disagreeing. Two consequences, "
                    "both the game's own rules rather than additions: the "
                    "hall's left end is now a real limit, which is the "
                    "bound fix 39's note called a KNOWN GAP, and in "
                    "segments 0 and 4 -- where every dispatcher calls "
                    "$91A2, two instructions moving the player's body and "
                    "nothing else -- a hit slides the player instead of the "
                    "world, because that is how the ends of a hall work. "
                    "Traced: the shove drains the segment it is standing in "
                    "and stops dead at the left wall instead of walking the "
                    "scenery past it. Played and confirmed, the "
                    "segment-0 behaviour included: at a hall's far left "
                    "a hit slides the player and leaves the scenery "
                    "standing, which is what the stock walk does there.",
    },
    {
        "n": 43, "slug": "knockback-cadence", "governs": "knockback",
        "title": "3-3-2: three hits shove the world one whole walk stride",
        "apply": fix_knockback_cadence,
        "measured": "fix 39 with a cycle on it, so fix 39's measurements "
                    "carry: same walkers, same inverted sign, same "
                    "one-unit-per-call stepping, same on-screen gate. What "
                    "is new is the eight: the walk stride is drawn over "
                    "eight units (the same eight fix 22 stops at four "
                    "for), so three hits at 3-3-2 move the world exactly "
                    "one stride and knockback stays commensurate with "
                    "walking. The cycle position lives in $F7 for hits on "
                    "the player and $F8 for hits on the goon -- both "
                    "measured at zero writes across a full recorded "
                    "playthrough, 9595 frames, with $E8 as the positive "
                    "control at 3.9M writes. A garbage value at power-on "
                    "costs one short hit and then wraps itself. The "
                    "arithmetic is 8 units per 3 hits against fix 39's 6. "
                    "SUPERSEDED IN PRACTICE by fix 44, which is this plus "
                    "the odometer accounting; 43 is kept as the "
                    "without-accounting half so the two can be compared.",
    },
    {
        "n": 39, "slug": "knockback-light-native", "governs": "knockback",
        "title": "a player hit steps the world, through the game's own walkers",
        "apply": fix_knockback_light_native,
        "measured": "traced, then played for 229 seconds. A player hit takes "
                    "two one-pixel steps through the table walkers a real "
                    "walk step drives ($9062 and $8F3E, which carries both "
                    "halves of the near world; $8E88 and $8E66 only when "
                    "their column has already scrolled into view), so "
                    "whatever a given "
                    "level actually has moves -- it is that level's own "
                    "scroll code doing it, not a list mapped out of level 1. "
                    "Confirmed live: the far pillar walks 160 -> 161 -> 162 "
                    "on the first hit, every write landing at $660A inside "
                    "the game's own $65C8, no display-list address named by "
                    "the patch at all. 85 bytes against fix 31's 601. Two "
                    "corrections are baked in after getting both wrong: the "
                    "satellite negates $AA before calling any mover, so the "
                    "movers want the effect and not the intent (the first "
                    "version sent the world the wrong way), and the game "
                    "never passes a mover a magnitude -- $9375 forces $AA to "
                    "+/-1 and loops on a countdown in $18BE, so two pixels is "
                    "two unit steps. KNOWN GAP: nothing bounds it. The only "
                    "edge check in the path ($A488) reads $186D, which is "
                    "pinned in combat, and a full scan finds no compare "
                    "against $188C anywhere in the ROM -- the world has no "
                    "native limiter, because stock movement is bounded by "
                    "the player letting go of the stick -- but that turned "
                    "out not to matter once the far actors were left out. "
                    "Running each mover alone and diffing what it wrote "
                    "showed $8E88 owns both pillar columns (all 32) and "
                    "$8E66 owns six addresses nothing here had mapped "
                    "($2607, $2645, $2683, $26C1, $26FF, $273D -- the cliff "
                    "tiles), so those two are not called. On a hit frame "
                    "exactly nineteen addresses move: $188C, WORLD_TABLE and "
                    "GOON_OWN_TABLE, with zero pillar or cliff writes on any "
                    "hit frame across a full recording. Level 3 then "
                    "showed the near world is two halves, not one: $8ECC "
                    "walks zone rows 8-10 and $8F3E rows 5-7, and dumped on "
                    "level 3 both hold the same four X coordinates (76, 140, "
                    "204, 12) across all six rows -- one set of four columns "
                    "split between two routines. Calling only $8ECC moved "
                    "rows 8-10 and left 5-7 standing, which is why the taller "
                    "background pillars there moved from the waist down. Both "
                    "are called now; verified on a hit frame that rows 5-7 "
                    "and 8-10 step together. The far actors are gated "
                    "rather than excluded: each is called only when its own "
                    "column has already scrolled into view, tested against "
                    "$A0 -- MARIA's visible width, and the same constant the "
                    "game's own edge check uses. Measured over a full "
                    "recording: of 24 hits, 23 left the pillars alone while "
                    "they sat parked at their placed $A0, and the one hit "
                    "that landed after ordinary walking had carried the "
                    "column to 158 moved it with the rest of the world, "
                    "and a play session confirmed the far actors behave. "
                    "The near world then sheared on level 3: $8F3E has no "
                    "RTS after its rows 5-7 pass, so it falls through at "
                    "$8FAC and walks rows 8-10 as well -- it is the pair, "
                    "not the top half. Calling it beside $8ECC walked the "
                    "bottom twice per step and the top once, measured on "
                    "level 3 as top=2 bottom=4 on hit frames, which is the "
                    "half-rate the halves were seen drifting apart at. "
                    "$8F3E alone now: same window remeasured gives top=40 "
                    "bottom=40 with zero unequal frames.",
    },
    {
        "n": 32, "slug": "slot8-gate",
        "title": "skip the 8th wait when the opponent has nothing queued",
        "apply": fix_slot8_gate,
        "measured": "not measured. The 87.5% idle figure is the entity "
                    "probe's; 13 - 0.875 = 12.125 frames average is "
                    "arithmetic on it. Conflicts with fixes 27, 28, 29 and "
                    "doses 6, 7 and 8, all of which touch this same wait "
                    "unconditionally",
    },
    {
        "n": 33, "slug": "slot8-gate-combined",
        "title": "fix 12 plus the slot-8 gate",
        "parts": [3, 6, 11, 32],
        "apply": fix_slot8_gate_combined,
        "measured": "the parts are separate; not measured together",
    },
    {
        "n": 34, "slug": "loop-gate-player-slot8",
        "title": "fix 29 plus the slot-8 gate -- checked to not overlap",
        "parts": [29, 32],
        "apply": fix_loop_gate_player_slot8,
        "measured": "not measured by play. Arithmetic: 5 + 0.125 frames "
                    "average per round against fix 29's flat 6 -- confirmed "
                    "byte-independent with --check first, since the claim "
                    "that this pair conflicted was wrong",
    },
    {
        "n": 35, "slug": "loop-gate-player-slot8-brisk-walk",
        "title": "fix 34 plus a three-unit walking step",
        "parts": [29, 32, 21],
        "apply": fix_loop_gate_player_slot8_brisk_walk,
        "measured": "the parts are separate; not measured together. Reactions "
                    "~5.1 frames, animation ~10.25, walking half again as far "
                    "per step -- three different numbers on purpose",
    },
    {
        "n": 36, "slug": "loop-gate-player-slot8-fast-walk",
        "title": "fix 34 plus a four-unit (double) walking step",
        "parts": [29, 32, 22],
        "apply": fix_loop_gate_player_slot8_fast_walk,
        "measured": "the parts are separate; not measured together. Fix 22's "
                    "own note applies: the stride is drawn for eight units a "
                    "cycle, so this may be where the feet stop keeping up",
    },
    {
        "n": 2,
        "slug": "button-path",
        "title": "latch the buttons too -- WITHDRAWN, makes the game worse",
        "governs": "where the button tests read from",
        "apply": fix_button_path,
        "withdrawn": True,
        "measured": "the button tests see a 100 ms press 50% -> 76%, and the "
                    "game plays WORSE: brief taps stop working. The "
                    "measurement was of the wrong thing -- see the docstring.",
    },
]


# ------------------------------------------------------------------- driver
def _hunt(name, root, depth=4):
    """Look for a dump by name under `root`, a few levels down.

    A ROM library is usually a tree of set names and regions rather than a
    flat folder, and this project should not have anybody's particular
    library baked into it -- so search for the filename instead of naming
    the path it happens to sit in."""
    root = os.path.abspath(root)
    base = root.rstrip(os.sep).count(os.sep)
    for here, dirs, files in os.walk(root):
        if here.count(os.sep) - base >= depth:
            dirs[:] = []
            continue
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        if name in files:
            return os.path.join(here, name)
    return None


def load_source():
    found = _hunt(ROM_NAME, os.path.join(ROOT, ".."))
    for path in SOURCES + ([found] if found else []):
        if path and os.path.exists(path):
            raw = io.open(path, "rb").read()
            if raw[1:10] != b"ATARI7800":
                raise SystemExit("%s has no .a78 header" % path)
            return path, raw[:HDR], raw[HDR:]
    raise SystemExit(
        "could not find Karateka. This project ships no ROM: supply your own\n"
        "dump of the NTSC release (crc32 FEC21472) and either set\n"
        "    KARATEKA_ROM=/path/to/%s\n"
        "or put it in one of:\n    %s"
        % (ROM_NAME, "\n    ".join(x for x in SOURCES[1:])))


def build(which=None):
    src, header, rom = load_source()
    if not os.path.isdir(OUTDIR):
        os.makedirs(OUTDIR)
    print("source: %s" % os.path.basename(src))
    print("  %d bytes of ROM behind a %d-byte header, crc32 %08X\n"
          % (len(rom), len(header), zlib.crc32(rom) & 0xFFFFFFFF))

    plain = os.path.join(OUTDIR, "karateka-original.bin")
    io.open(plain, "wb").write(rom)

    for fix in FIXES:
        if which and fix["n"] != which:
            continue
        if fix.get("withdrawn") and not which:
            print("fix %d  %s" % (fix["n"], fix["title"]))
            print("        not built by default; ask for it by number")
            print("")
            continue
        p = Patcher(rom)
        fix["apply"](p)
        out = bytes(p.rom)
        changed = sum(1 for a, b in zip(rom, out) if a != b)
        # count the fix's own bytes before signing, so the figure reported
        # is the size of the change and not the change plus 120
        out = _sign(out, "fix %d" % fix["n"])
        stem = "karateka-%d-%s" % (fix["n"], fix["slug"])
        binp = os.path.join(OUTDIR, stem + ".bin")
        a78p = os.path.join(OUTDIR, stem + ".a78")
        bpsp = os.path.join(OUTDIR, stem + ".bps")
        io.open(binp, "wb").write(out)
        io.open(a78p, "wb").write(header + out)
        rc = subprocess.run(
            [sys.executable, os.path.join(ROOT, "tools", "bps.py"), "create",
             plain, binp, bpsp], capture_output=True, text=True)
        if rc.returncode != 0:
            print("  BPS failed: %s" % (rc.stdout + rc.stderr).strip()[:200])
            continue
        print("fix %d  %s" % (fix["n"], fix["title"]))
        print("        %d bytes changed" % changed)
        print("        %-34s playable" % os.path.basename(a78p))
        print("        %-34s %d bytes, applies to any headerless dump"
              % (os.path.basename(bpsp), os.path.getsize(bpsp)))
        print("        signature: %s"
              % ("valid" if sign7800.verify(out) else "FAILED"))
        print("        measured: %s\n" % fix["measured"])



# ------------------------------------------------------------------ conflicts
#
# These fixes are meant to be mixed. Fix 3 changes when the game listens, fix 6
# how far a strike reaches, fix 11 what the stick means -- three different
# things, and there is no reason a build cannot have all three. But "different
# things" is a claim about bytes, and claims about bytes should be checked
# rather than believed. Fix 9 was withdrawn for exactly this class of mistake:
# it looked independent and was not.
#
# So every fix records the bytes it writes, and the checker sorts each pair of
# fixes into one of three states:
#
#     independent   they touch no byte in common
#     dependency    they share bytes and agree on every one of them -- which is
#                   what a composite looks like, and what fix 3 and fix 11 both
#                   including fix 1 looks like
#     CONFLICT      they share a byte and disagree about its value
#
# A conflict is not "these cannot both be applied" -- applying both is exactly
# what produces the wrong ROM silently. It is "the second one lands on top of
# the first and the result is neither".
#
# This catches overlap, which is the mechanical half. It cannot catch a
# semantic clash: two fixes that touch different bytes and still fight, the way
# fix 9's stance-on-down fought with the attack heights it never wrote to.
# Nothing but reading the code catches that, which is why the note is here.


def plan(fix, rom):
    """Every byte a fix writes, as {address: value}, without building a ROM."""
    p = Patcher(rom)
    fix["apply"](p)
    out = {}
    for addr, data in p.writes:
        for i, b in enumerate(data):
            out[addr + i] = b
    return out


def knobs(fix, by_n):
    """Everything a fix has an opinion about, its parts included."""
    out = set()
    if fix.get("governs"):
        out.add(fix["governs"])
    for n in fix.get("parts", ()):
        out |= knobs(by_n[n], by_n)
    return out


def compare(x, y, plans, by_n):
    """How two fixes relate.

    Three outcomes, and the middle one is the reason this is not just an
    overlap test:

        independent   no byte in common
        alternatives  they turn the same knob to different settings. Fix 5 and
                      fix 6 are two hit-window sizes; you pick one. Bytes
                      disagreeing there is the point, not a bug.
        dependency    shared bytes, all agreed -- a composite and its parts
        CONFLICT      shared bytes, disagreeing, about *different* knobs. This
                      is the one that matters: each fix is correct alone and
                      the pair is neither.
    """
    a, b = plans[x["n"]], plans[y["n"]]
    shared = set(a) & set(b)
    if not shared:
        return "independent", 0, []
    bad = sorted(v for v in shared if a[v] != b[v])
    if not bad:
        return "dependency", len(shared), []
    if knobs(x, by_n) & knobs(y, by_n):
        return "alternatives", len(shared), bad
    return "CONFLICT", len(shared), bad


def check():
    """Every pair of fixes, and every composite against the parts it claims."""
    _src, _header, rom = load_source()
    by_n = {f["n"]: f for f in FIXES}
    plans = {}
    for fix in FIXES:
        plans[fix["n"]] = plan(fix, rom)

    print("what each fix writes, and what it has an opinion about")
    for fix in FIXES:
        mark = "  (withdrawn)" if fix.get("withdrawn") else ""
        parts = (" = " + " + ".join("fix %d" % n for n in fix["parts"])
                 if fix.get("parts") else "")
        print("  fix %-2d %-20s %4d bytes   %s%s%s"
              % (fix["n"], fix["slug"], len(plans[fix["n"]]),
                 ", ".join(sorted(knobs(fix, by_n))) or "-", parts, mark))
    print("")

    # A composite that quietly lost a part would still look self-consistent,
    # so the claim is checked rather than trusted.
    print("composites against the parts they claim")
    broken = 0
    for fix in FIXES:
        for n in fix.get("parts", ()):
            whole, part = plans[fix["n"]], plans[n]
            missing = [a for a in part if a not in whole]
            differs = [a for a in part if a in whole and whole[a] != part[a]]
            if missing or differs:
                broken += 1
                print("  fix %-2d does NOT contain fix %d: %d bytes missing, "
                      "%d different" % (fix["n"], n, len(missing), len(differs)))
            else:
                print("  fix %-2d contains all %d bytes of fix %d"
                      % (fix["n"], len(part), n))
    print("")

    print("how they relate")
    bad = 0
    for i, x in enumerate(FIXES):
        for y in FIXES[i + 1:]:
            kind, shared, dis = compare(x, y, plans, by_n)
            if kind == "independent":
                continue
            if kind == "dependency":
                print("  fix %-2d + fix %-2d  dependency: %d bytes, all agreed"
                      % (x["n"], y["n"], shared))
            elif kind == "alternatives":
                print("  fix %-2d + fix %-2d  alternatives: two settings of %s"
                      % (x["n"], y["n"],
                         ", ".join(sorted(knobs(x, by_n) & knobs(y, by_n)))))
            else:
                # A pair where either side is withdrawn is worth printing and
                # not worth failing over: nothing builds them together. It is
                # still the clearest demonstration the check works, so it stays
                # visible rather than being filtered out.
                moot = x.get("withdrawn") or y.get("withdrawn")
                bad += 0 if moot else 1
                print("  fix %-2d + fix %-2d  CONFLICT over %d of %d shared "
                      "bytes, and they are not the same knob%s:"
                      % (x["n"], y["n"], len(dis), shared,
                         "  (moot: one is withdrawn)" if moot else ""))
                for a in dis[:4]:
                    print("        $%04X  fix %d writes $%02X, fix %d writes "
                          "$%02X" % (a, x["n"], plans[x["n"]][a], y["n"],
                                     plans[y["n"]][a]))
                if len(dis) > 4:
                    print("        ...and %d more" % (len(dis) - 4))
    print("")
    if bad or broken:
        print("%d conflicting pair(s) among the live fixes, %d broken "
              "composite(s)." % (bad, broken))
        return 1
    print("No two fixes disagree about a byte unless they are two settings of "
          "the same knob, and")
    print("every composite really does contain the parts it names.")
    print("")
    print("What this cannot check: a semantic clash. Fix 9 was withdrawn "
          "because it put stance")
    print("change on down, and down is an attack height -- two fixes writing "
          "different bytes and")
    print("still fighting. Only reading the code catches that.")
    return 0


# ----------------------------------------------------------------- the doses
#
# Fix 3 skips four of the eight waits and fix 4 skips two, which is two points
# on a line that has nine. A wait is worth exactly one frame, so the loop runs
# at 13 frames minus however many are skipped, and "which of these feels right"
# is not a question analysis can answer -- it is a question about an action
# game, and it wants a dial rather than two positions.
#
# The waits are spread evenly rather than taken from one end, because each one
# belongs to a particular entity: skipping the first four would give four
# entities a frame each and starve the rest, while spacing them keeps every
# entity's share the same. Even spacing also reproduces fix 3 and fix 4
# exactly, at doses 4 and 2, which is a small check that the spacing rule is
# the one those were built with.


def dose_indices(n):
    """Which of the eight waits to skip, spread as evenly as n allows."""
    if n <= 0:
        return []
    if n >= len(WAIT_BLOCKS):
        return list(range(len(WAIT_BLOCKS)))
    return [int(round(i * len(WAIT_BLOCKS) / float(n))) for i in range(n)]


def build_doses():
    """One cartridge per dose, 0 through 8, to be chosen by playing them."""
    src, header, rom = load_source()
    if not os.path.isdir(OUTDIR):
        os.makedirs(OUTDIR)
    plain = os.path.join(OUTDIR, "karateka-original.bin")
    io.open(plain, "wb").write(rom)
    print("source: %s\n" % os.path.basename(src))
    print("  dose  waits skipped        loop     file")
    for n in range(len(WAIT_BLOCKS) + 1):
        which = dose_indices(n)
        p = Patcher(rom)
        fix_input_latch(p)
        _skip_waits(p, which)
        out = _sign(bytes(p.rom), "dose %d" % n)
        stem = "karateka-dose-%d" % n
        binp = os.path.join(OUTDIR, stem + ".bin")
        io.open(binp, "wb").write(out)
        io.open(os.path.join(OUTDIR, stem + ".a78"), "wb").write(header + out)
        subprocess.run(
            [sys.executable, os.path.join(ROOT, "tools", "bps.py"), "create",
             plain, binp, os.path.join(OUTDIR, stem + ".bps")],
            capture_output=True, text=True)
        same = ""
        if n == 4:
            same = "  (this is fix 3)"
        elif n == 2:
            same = "  (this is fix 4)"
        print("   %d     %-20s %2d fr    %s.a78%s"
              % (n, " ".join(str(i) for i in which) or "none",
                 13 - n, stem, same))
    print("")
    print("Every one of these includes fix 1, and none of them touches the hit")
    print("window or the mapping, so a dose can be combined with fix 6 or")
    print("fix 11 without either standing on the other.")
    print("")
    print("13 frames is the original. The frame count is arithmetic, not a")
    print("measurement: doses 2 and 4 were measured at 11 and 9, and a wait is")
    print("worth exactly one frame either way.")


# ------------------------------------------------------------------- bundling
#
# The fixes as a patch set: sections with their own checksums, options you pick
# from, and floats for the code that needs somewhere to live.
#
# ## What the free-space scan turned up
#
# Fix 1 puts a 28-byte sampler at $FF80 and a 17-byte wrapper at $FFA0, both
# hardcoded, and neither of those addresses is free. $FF80-$FFF9 holds
# high-entropy data sitting immediately below the 6502 vectors -- on a 7800
# that is where the BIOS signature block lives. Emulators do not check it, which
# is why every build so far has run, but overwriting 45 bytes of it is not
# something to do on purpose.
#
# The ROM has 7,127 bytes genuinely free in runs of 16 or more, the useful ones
# being 64 bytes at $FF40 and 6,448 at $A6D0. Both routines fit in $FF40 with
# room to spare.
#
# So the two routines become floats: the bundle says how much room each needs
# and where to look, the patcher finds it, and the JSR that calls each one gets
# the address the patcher chose rather than an address someone typed. That is
# the whole point of the mechanism, and it turned up a real bug on its first
# use.
FREE = [(0xFF40, 64), (0xA6D0, 6448)]

# Where fix 1 currently hardcodes its two routines, and the JSR operand that
# has to learn where each one actually went.
FLOATS = {
    SHOVE_A: {"id": "shove-first",
              "what": "spark, then push the first fighter back",
              "search": [(0xA6D0, 6448)],
              "fixups": [(a + 1, "abs16") for a in KNOCK_CALLERS_A]},
    SHOVE_B: {"id": "shove-second",
              "what": "spark, then push the second fighter back",
              "search": [(0xA6D0, 6448)],
              "fixups": [(a + 1, "abs16") for a in KNOCK_CALLERS_B]},
    SHOVE_A38: {"id": "shove-first-capped",
                "what": "spark, then push the first fighter back, "
                        "world-drift capped",
                "search": [(0xA6D0, 6448)],
                "fixups": [(a + 1, "abs16") for a in KNOCK_CALLERS_A]},
    SHOVE_B38: {"id": "shove-second-capped",
                "what": "spark, then push the second fighter back, "
                        "via the goon's own applier",
                "search": [(0xA6D0, 6448)],
                "fixups": [(a + 1, "abs16") for a in KNOCK_CALLERS_B]},
    SHOVE_A43: {"id": "shove-first-cadenced",
                "what": "spark, then step the world back 3-3-2 over three "
                        "hits",
                "search": [(0xA6D0, 6448)],
                "fixups": [(a + 1, "abs16") for a in KNOCK_CALLERS_A]},
    SHOVE_B43: {"id": "shove-second-cadenced",
                "what": "spark, then push the second fighter back 3-3-2, "
                        "via the goon's own applier",
                "search": [(0xA6D0, 6448)],
                "fixups": [(a + 1, "abs16") for a in KNOCK_CALLERS_B]},
    SHOVE_A44: {"id": "shove-first-odometer",
                "what": "spark, then step the world back 3-3-2, spending "
                        "the hall's travel budget",
                "search": [(0xA6D0, 6448)],
                "fixups": [(a + 1, "abs16") for a in KNOCK_CALLERS_A]},
    SHOVE_B44: {"id": "shove-second-odometer",
                "what": "spark, then push the second fighter back 3-3-2, "
                        "via the goon's own applier",
                "search": [(0xA6D0, 6448)],
                "fixups": [(a + 1, "abs16") for a in KNOCK_CALLERS_B]},
    WALK18: {"id": "walking-decoder",
             "what": "the walking-stance half, for the both-buttons mapping",
             "search": [(0xA6D0, 6448)],
             "fixups": [(0x7834, "abs16")]},
    FIGHT18: {"id": "fighting-decoder-bb",
              "what": "the fighting-stance half, both buttons to stand down",
              "search": [(0xA6D0, 6448)],
              "fixups": [(0x784B, "abs16")]},
    FIGHT19: {"id": "fighting-decoder-ul",
              "what": "the fighting-stance half, up and left to stand down",
              "search": [(0xA6D0, 6448)],
              "fixups": [(0x784B, "abs16")]},
    GATE: {"id": "strike-gate",
           "what": "how long a strike runs, from a three-entry rhythm table",
           "search": [(0xA6D0, 6448)],
           "fixups": [(c, "abs16") for c in GATE_CELLS]},
    ANYINPUT: {"id": "any-input-live",
               "what": "is anything pressed right now, for the cutscene skip",
               "search": [(0xA6D0, 6448)],
               "fixups": [(c, "abs16") for c in ANYINPUT_CELLS]},
    WALKL: {"id": "keep-walking-left",
            "what": "keep walking left: a direction and no button",
            "search": [(0xA6D0, 6448)],
            "fixups": [(c, "abs16") for c in WALKL_CELLS]},
    WALKR: {"id": "keep-walking-right",
            "what": "keep walking right: a direction and no button",
            "search": [(0xA6D0, 6448)],
            "fixups": [(c, "abs16") for c in WALKR_CELLS]},
    HEIGHT: {"id": "strike-height",
             "what": "which height the stick is calling for, 1/2/3 or none",
             "search": [(0xA6D0, 6448)],
             "fixups": [(c, "abs16") for c in HEIGHT_CELLS]},
    FIGHT: {"id": "fighting-decoder",
            "what": "the fighting-stance half of the command decoder",
            "search": [(0xA6D0, 6448)],
            "fixups": [(0x784B, "abs16")]},
    LATCH: {"id": "sampler",
            "what": "reads the controls every frame and remembers a press",
            "fixups": [(0x5946, "abs16")]},
    WRAP:  {"id": "reader",
            "what": "reads the latches, then clears them",
            "fixups": [(0x59FF, "abs16")]},
}

MERGE_GAP = 8       # writes closer than this share a section


def _dose_only(n):
    """A dose without fix 1 folded into it: the bundle states that as a need."""
    def apply(p):
        _skip_waits(p, dose_indices(n))
        return p
    return apply


def bundle_options():
    """The options a patch set offers, each doing exactly one thing."""
    out = [{
        "id": "input-latch", "knob": "input latching",
        "title": "remember a press until the game reads it",
        "note": "The controls are read once per trip round a loop that takes "
                "13 frames, so a press shorter than that can fall between two "
                "reads. Measured: a 33 ms tap seen 16% of the time becomes "
                "46%.",
        "apply": fix_input_latch,
    }]
    for n in range(1, len(WAIT_BLOCKS) + 1):
        out.append({
            "id": "dose-%d" % n, "knob": "loop cadence",
            "title": "skip %d of the 8 waits -- a %d-frame loop" % (n, 13 - n),
            "requires": ["input-latch"],
            "note": "Each wait skipped is worth exactly one frame. Doses 2 and "
                    "4 were measured on recorded sessions at 11 and 9 frames; "
                    "the rest is arithmetic." if n in (2, 4) else "",
            "apply": _dose_only(n),
        })
    out += [
        {"id": "even-reach", "knob": "hit window",
         "title": "give both fighters the same reach",
         "note": "One fighter's hit window is 8 or 9 and the other's is 10, in "
                 "every encounter. This sets both to 10.",
         "apply": fix_even_reach},
        {"id": "generous-reach", "knob": "hit window",
         "title": "same reach, and two units of slack for both",
         "note": "A different claim from even-reach: that ten units is tight "
                 "for anybody. Wants playing rather than arguing.",
         "apply": fix_generous_reach},
        {"id": "slot8-gate", "knob": None,
         "title": "skip the 8th wait when the opponent has nothing queued",
         "note": "$18DF sits on a do-nothing word 87.5% of the time in a "
                 "real fight (the entity probe's own number). This skips "
                 "just that one wait on the rounds it is idle, and checks "
                 "every round rather than assuming -- it cannot skip a wait "
                 "the opponent's attack chain actually needed. Cannot be "
                 "combined with loop-gate or loop-gate-player, which touch "
                 "the same wait unconditionally.",
         "apply": fix_slot8_gate},
        {"id": "loop-gate", "knob": "loop cadence",
         "title": "six-frame loop, entities every other round",
         "note": "The loop spins at six frames and the entities animate every "
                 "other round, so a step lands every twelve against the stock "
                 "thirteen. Responsiveness and game speed stop being the same "
                 "number.",
         "apply": fix_loop_gate_tuned},
        {"id": "loop-gate-player", "knob": "loop cadence",
         "title": "as loop-gate, and the player decides every round",
         "note": "Animation every twelve frames, reactions every six. "
                 "Measured: 98.8% of eight-frame taps land, against 13.8% for "
                 "two-frame ones on the stock cartridge.",
         "apply": fix_loop_gate_player},
        {"id": "loop-gate-player-slot8", "knob": "loop cadence",
         "title": "loop-gate-player plus the slot-8 gate",
         "note": "Checked byte-independent with --check, not assumed: "
                 "loop-gate-player leaves the wait right after slot 8 "
                 "untouched on purpose, which is exactly what slot8-gate "
                 "edits. ~5.125 frames a round on average instead of a flat "
                 "6, animation at ~10.25 instead of 12.",
         "apply": fix_loop_gate_player_slot8},
        {"id": "loop-gate-player-slot8-brisk-walk", "knob": None,
         "title": "fix 34 plus a three-unit walking step",
         "note": "Reactions ~5.1 frames, animation ~10.25, walking half "
                 "again as far per step. Separate knobs stacked, not one "
                 "bigger number -- deciding, animating and walking all move "
                 "at their own rate now.",
         "apply": fix_loop_gate_player_slot8_brisk_walk},
        {"id": "player-reach", "knob": "hit window",
         "title": "give the $18C3 fighter more reach -- a test, not a fix",
         "note": "Play one encounter. If your strikes connect from further, "
                 "the player is the $18C3 fighter and did have the smaller "
                 "window. If the opponent starts reaching you, the inference "
                 "behind the other two hit-window options was backwards.",
         "apply": fix_player_reach},
        {"id": "knockback-light", "knob": "knockback",
         "title": "two units -- a quarter of a walking step",
         "note": "A walking step covers 8 units over four frames. This is a "
                 "nudge; knockback-hard gives the whole step back.",
         "apply": fix_knockback_light},
        {"id": "knockback", "knob": "knockback",
         "title": "push the struck fighter back four units",
         "note": "The 8-bit version has knockback and this one has none. "
                 "Watch for nothing happening: the animation script may "
                 "rewrite the position before the shove is drawn.",
         "apply": fix_knockback},
        {"id": "knockback-hard", "knob": "knockback",
         "title": "eight units -- a whole walking step given back",
         "note": "Try this before the gentler one. If eight units shows "
                 "nothing, four never would, and the answer is that the base "
                 "gets rewritten every update.",
         "apply": fix_knockback_hard},
        {"id": "walk-brisk", "knob": "walk speed",
         "title": "walk three units a step instead of two",
         "note": "Repositioning gets half again as quick without the fight "
                 "speeding up with it, which is the trade every cadence dose "
                 "makes. The stride is drawn for eight units a cycle, so watch "
                 "whether the feet keep up.",
         "apply": fix_walk_brisk},
        {"id": "walk-fast", "knob": "walk speed",
         "title": "walk four units a step -- double",
         "note": "Where the feet probably stop keeping up. Built so that can "
                 "be seen rather than argued.",
         "apply": fix_walk_fast},
        {"id": "strike-rhythm", "knob": "strike repeat",
         "title": "held strikes go long-short-short instead of even",
         "note": "The gate is a three-entry table, 4/2/2, stepped through on "
                 "each strike -- three calls then one then one. Fix 23 moved the "
                 "constant; this makes it vary, which was the actual "
                 "complaint. The phase lives in $DC, a byte two probe runs "
                 "found nothing writes.",
         "apply": fix_strike_rhythm},
        {"id": "strike-repeat", "knob": "strike repeat",
         "title": "a held strike repeats after four frames, not six",
         "note": "Both attack chains count to six before re-polling the stick.",
         "apply": fix_strike_repeat},
        {"id": "ai-close", "knob": "opponent distance",
         "title": "the opponent's distance test fires at 10 instead of 15",
         "note": "The least certain option here. What the opponent does either "
                 "side of that number has not been read -- only that it is the "
                 "game's own notion of how far apart you are. If it gets more "
                 "passive rather than less, the number wants raising.",
         "apply": fix_ai_close},
        {"id": "start-at-four", "knob": "starting encounter",
         "title": "start at the fourth fight -- a test build, not a fix",
         "note": "For running a probe over a window that includes the bird. "
                 "Never combine it with anything you mean to judge on feel: "
                 "it skips the three fights the pacing complaints came from.",
         "apply": fix_start_at_four},
        {"id": "remap-bothbuttons", "knob": "control mapping",
         "title": "as remap, but both buttons together stand you down",
         "note": "Up and down then mean nothing but a strike height, so no "
                 "order of releasing things can drop you out of stance. "
                 "Holding both buttons in walking stance deliberately does "
                 "nothing, or the stance would flicker.",
         "apply": fix_remap_bothbuttons},
        {"id": "remap-upleft", "knob": "control mapping",
         "title": "as remap, but up and left together stand you down",
         "note": "A diagonal nothing else uses, and no guard -- but still a "
                 "direction, so still one bad push away from a stand-down.",
         "apply": fix_remap_upleft},
        {"id": "remap", "knob": "control mapping",
         "title": "bare stick moves, buttons strike",
         "note": "Stock, the stick alone attacks and you hold button 2 to "
                 "move. This swaps them: direction walks, button 1 punches, "
                 "button 2 kicks, both with the height from the vertical, and "
                 "down with the stick centred changes stance.",
         "apply": fix_remap},
    ]
    return out


def _runs(addrs, gap):
    """Contiguous-ish spans, so a section is worth checksumming."""
    out = []
    for a in sorted(addrs):
        if out and a - (out[-1][1]) <= gap:
            out[-1][1] = a + 1
        else:
            out.append([a, a + 1])
    return [(a, b - a) for a, b in out]


def pick_anchors(rom, sections, n=4, size=256):
    """Extents no option touches, to identify the cartridge by.

    A hash of the whole file is true of exactly one dump -- the pristine one --
    and false of every ROM this bundle produces, so it cannot answer "is this
    the right game" about a cartridge that already has a fix on it. Anchors
    can: they are ground no option stands on, as true after patching as before.

    Two things disqualify a candidate. Overlapping a section or a float's
    search range means a patch could change it. And low byte diversity means it
    would match the wrong cartridge just as happily -- a run of zeroes is not
    evidence of anything, and that is the failure mode worth avoiding, because
    it looks like a passing check.
    """
    busy = []
    for sec in sections.values():
        at = int(sec["addr"], 16) - 0x4000
        busy.append((at, at + sec["length"]))
    for at, ln in FREE:
        busy.append((at - 0x4000, at - 0x4000 + ln))
    out = []
    for i in range(n):
        start = (len(rom) * (2 * i + 1)) // (2 * n)
        for at in range(start, len(rom) - size):
            if any(not (at + size <= a or b <= at) for a, b in busy):
                continue
            if any(not (at + size <= a or b <= at) for a, b in
                   [(int(x["addr"], 16) - 0x4000,
                     int(x["addr"], 16) - 0x4000 + x["length"]) for x in out]):
                continue
            chunk = rom[at:at + size]
            if len(set(chunk)) < 32:
                continue
            out.append({"addr": "0x%04X" % (at + 0x4000), "length": size,
                        "crc32": "0x%08X" % (zlib.crc32(chunk) & 0xFFFFFFFF)})
            break
    if len(out) < n:
        raise SystemExit("could not find %d usable anchors" % n)
    return out


def build_bundle(out_path=None):
    """Write the patch set: sections, options, floats and their BPS files."""
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import patchset
    import bps

    src, header, rom = load_source()
    opts = bundle_options()
    plans = {o["id"]: plan(o, rom) for o in opts}

    for at, n in FREE:
        blk = rom[at - 0x4000:at - 0x4000 + n]
        if any(blk):
            raise SystemExit("the free range $%04X+%d is not actually free"
                             % (at, n))

    # Sections come from every option's writes together, so two options that
    # touch the same place agree about where it starts and ends -- otherwise
    # their pre-image checksums would describe different extents and neither
    # could be checked against the other.
    # A float's extent is the contiguous run its routine actually occupies,
    # not a fixed window: the two of them are 32 bytes apart, so anything
    # coarser has the first swallow the second.
    def float_run(p, base):
        out, a = [], base
        while a in p:
            out.append(a)
            a += 1
        return out

    float_at = set()
    for base in FLOATS:
        for o in opts:
            float_at.update(float_run(plans[o["id"]], base))
    every = set()
    for p in plans.values():
        every |= set(p)
    sections = {}
    for at, n in _runs(every - float_at, MERGE_GAP):
        sections["s_%04X" % at] = {
            "what": "", "addr": "0x%04X" % at, "length": n,
            "crc32": "0x%08X" % patchset.crc32(rom[at - 0x4000:at - 0x4000 + n]),
        }

    anchors = pick_anchors(rom, sections)
    files, manifest_options = {}, []
    for o in opts:
        p, floats = plans[o["id"]], []
        myfixups = set()
        for base, f in FLOATS.items():
            got = float_run(p, base)
            if not got:
                continue
            # Only this option's own fixup sites become placeholders. Taking
            # them from every float in the bundle would put $FF into a byte
            # another option writes real code to -- fix 18 relocates the
            # walking block and fixes up $7834, which is the second byte of
            # fix 11's walking block.
            for a, _e in f["fixups"]:
                myfixups.add(a)
            blobname = "f/%s.bin" % f["id"]
            blobbytes = bytes(p[a] for a in got)
            files[blobname] = blobbytes
            floats.append({
                "id": f["id"], "what": f["what"], "length": len(got),
                # so two options differing only inside a float stay telling
                # apart once one of them is on a cartridge
                "crc32": "0x%08X" % patchset.crc32(blobbytes),
                "fill": 0, "from": "end", "blob": blobname,
                "search": [{"addr": "0x%04X" % a, "length": n}
                           for a, n in f.get("search", FREE)],
                "fixups": [{"addr": "0x%04X" % a, "encode": e,
                            "expect": "0xFFFF"} for a, e in f["fixups"]],
            })
        # One patch per option, spanning every section it touches. Six
        # two-byte edits scattered across the encounter setups are one change,
        # not six, and describing them as one keeps their checksums in step
        # without anybody having to remember to.
        placeholders = set(myfixups) | {a + 1 for a in myfixups}
        touched = sorted((sid for sid, sec in sections.items()
                          if any(patchset.h(sec["addr"]) <= a
                                 < patchset.h(sec["addr"]) + sec["length"]
                                 for a in p)),
                         key=lambda sid: patchset.h(sections[sid]["addr"]))
        if touched:
            before, after, vol, cursor = bytearray(), bytearray(), [], 0
            for sid in touched:
                at, n = patchset.h(sections[sid]["addr"]), sections[sid]["length"]
                chunk = rom[at - 0x4000:at - 0x4000 + n]
                before += chunk
                piece = bytearray(chunk)
                for a, b in p.items():
                    if at <= a < at + n:
                        piece[a - at] = 0xFF if a in placeholders else b
                        if a in placeholders and (a - 1) not in placeholders:
                            vol.append([cursor + (a - at), 2])
                after += piece
                cursor += n
            member = "p/%s.bps" % o["id"]
            files[member] = bps.create(bytes(before), bytes(after))
            patches = [{"sections": touched, "bps": member,
                        "before": "0x%08X" % patchset.crc32(bytes(before))}]
            if vol:
                patches[0]["volatile"] = vol
        else:
            patches = []
        entry = {"id": o["id"], "title": o["title"]}
        for k in ("knob", "requires", "note"):
            if o.get(k):
                entry[k] = o[k]
        entry["patches"] = patches
        if floats:
            entry["floats"] = floats
        manifest_options.append(entry)

    manifest = {
        "format": patchset.FORMAT,
        "name": "Karateka (NTSC) fixes",
        "what": "Cadence, hit window and control mapping for the 1987 Atari "
                "7800 release. Pick one setting per knob; the patcher refuses "
                "a selection that cannot mean one thing.",
        "target": {
            "what": os.path.basename(src),
            "body_size": len(rom),
            "body_sha256": hashlib.sha256(rom).hexdigest(),
            "headers": [0, HDR],
            "base": "0x4000",
            "anchors": anchors,
        },
        "knobs": {
            "input latching": "whether a press is remembered between reads",
            "loop cadence": "how many frames the main loop takes to think once",
            "hit window": "how close a strike has to be to land",
            "knockback": "whether a hit moves the fighter it lands on",
            "walk speed": "how far a walking step carries you",
            "starting encounter": "which fight the game begins on",
            "strike repeat": "how soon a held strike comes round again",
            "opponent distance": "the separation the opponent's AI reacts to",
            "control mapping": "what the stick and buttons mean",
        },
        "sections": sections,
        "options": manifest_options,
    }
    out_path = out_path or os.path.join(OUTDIR, "karateka.abp")
    if not os.path.isdir(OUTDIR):
        os.makedirs(OUTDIR)
    patchset.write_bundle(out_path, manifest, files)

    print("%s" % out_path)
    print("  %d options over %d knobs" % (len(opts),
                                          len(manifest["knobs"])))
    print("  %d sections, %d bytes covered"
          % (len(sections), sum(s["length"] for s in sections.values())))
    print("  %d float blob(s), placed at apply time rather than hardcoded"
          % len([f for f in files if f.startswith("f/")]))
    print("  %d bps files, %d bytes total"
          % (len([f for f in files if f.startswith("p/")]),
             sum(len(v) for v in files.values())))
    print("")
    print("  python tools/patchset.py list %s" % os.path.basename(out_path))
    return 0

def main():
    ap = argparse.ArgumentParser(
        description=__doc__.strip().split("\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--build", nargs="?", const=0, type=int, metavar="N")
    ap.add_argument("--doses", action="store_true",
                    help="one cartridge per wait dose, 0 through 8")
    ap.add_argument("--bundle", nargs="?", const="", metavar="OUT",
                    help="write a patchset: every fix as a pickable option")
    ap.add_argument("--check", action="store_true",
                    help="which fixes overlap, and whether they agree")
    args = ap.parse_args()
    if args.bundle is not None:
        return build_bundle(args.bundle or None)
    if args.check:
        return check()
    if args.doses:
        build_doses()
        return 0
    if args.list or args.build is None:
        for f in FIXES:
            mark = "  (withdrawn)" if f.get("withdrawn") else ""
            print("  %d  %-20s %s%s" % (f["n"], f["slug"], f["title"], mark))
            print("     measured: %s" % f["measured"])
        return 0
    build(args.build or None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
