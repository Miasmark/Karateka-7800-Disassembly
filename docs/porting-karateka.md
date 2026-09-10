# Porting Karateka from the Atari 8-bit: what the job actually is

The 8-bit version is widely held to be the good one. This is an assessment of
what moving it to the 7800 would involve, measured off the disk rather than
guessed at.

## The disk

`karateka.atx`, 712 sectors, single density. ATX rather than ATR because the
disk is protected: eight sectors appear twice, none flagged as errors, which is
duplicate-sector protection rather than damage. `tools/atx.py` reads it, takes
the good copy of each sector, and can write a plain ATR.

**There are no files on it.** No DOS directory -- it boots its own loader, so
the program is sectors. The boot chain is four instructions:

```
$3811  JSR $3846    ; point the display list at $385B, DMA on
$3814  JSR $385E    ; delay
$3817  JSR $381D    ; read sector 91 to $3000 through DSKINV
$381A  JMP $3000
```

and sector 91 is a decryptor: it kills DMA, builds a table in zero page, and
runs `EOR`/`ROL`/`SBC`/`ROR` chains over itself.

Sixty-nine sectors have high byte entropy and the other 545 do not, and the
high-entropy ones are spread right through the disk -- 50-68, 73-90, 114-134,
140-157 -- interleaved with sectors full of plain 6502.

An earlier version of this said those were the animation frames. They are not.
A bitmap has a row width, and at its true width consecutive rows resemble each
other; testing every width from 4 to 64 bytes, those regions score 0.487 to
0.492 at *all* of them, which is what random bytes do. They are compressed or
encrypted, not pictures.

The artwork is in the sparse sectors instead, which is obvious in hindsight: a
figure on a black background is mostly zero bytes, so sprite data is *low*
entropy, not high. Two hundred and nineteen sectors are mostly-but-not-entirely
blank, and those score 0.21 to 0.34 with clear preferred widths -- 6, 12, 20,
24 bytes a row, multiples of each other, which is what a sprite sheet looks
like.

So the game code reads directly, the artwork is locatable, and some 69 sectors
of packed data still have to be understood before a port could use whatever is
in them.

## What the code talks to

`tools/portscan.py` counts every instruction whose operand lands on Atari 8-bit
hardware, and sorts the result by what becomes of it on a 7800:

```
CARRIES OVER          POKEY    19 accesses
HAS AN EQUIVALENT     PIA      15 accesses
NO EQUIVALENT         GTIA     54 accesses
                      ANTIC    54 accesses
                      OS       11 accesses

153 accesses: 19 carry over (12%), 119 need the surrounding logic rebuilt (78%)
```

Both machines run a 6502, which is the first thing anyone says about this port
and the least useful fact about it. The CPU is free. Everything the code says to
the hardware is the work.

### The sound is nearly free

POKEY is POKEY. A 7800 cartridge can carry the same chip the Atari 800 has --
Commando and Ballblazer do -- and the register layout is identical. The sound
code moves across with its base address changed from `$D200` to `$4000` and
nothing else. That is the part everyone expects to be hard.

### The controls are a rename

The 8-bit reads joysticks through a PIA at `$D300`; the 7800 reads them through
the RIOT at `$0280` and the TIA at `$0C`-`$0D`. Same information, different
address and bit order. Fifteen sites.

### The graphics are a rebuild, and not for the reason people assume

It is not that MARIA is weaker. It is that the two machines describe a screen
in different terms:

- **ANTIC** is told *what mode each scanline is*, and fetches a bitmap or
  character data accordingly. Hardware scrolling is two registers.
- **MARIA** is told *what objects live in each zone*, and fetches each one from
  its own address. There is no scroll register.

So `HSCROL`/`VSCROL` -- 21 of the 54 ANTIC accesses -- have no counterpart at
all. They become a redraw.

**And the 7800 has no hardware collision detection.** GTIA reports which player
touched which playfield; MARIA reports nothing. Every collision read is a piece
of game logic that has to be replaced with arithmetic on coordinates.

That last point is worth dwelling on, because the 7800 version already released
in 1987 has, by common account, collision detection so poor that hitting an
opponent at close range is nearly impossible. This is where that came from. It
is the part of the port with no mechanical translation, and the part where a
team under deadline had to invent something.

## Use the XEGS cartridge, not the disk

The same game shipped on an Atari XEGS 128K cartridge, and it is a better
starting point on every axis that matters. Measured across both images:

|  | disk | XEGS cart |
|---|---|---|
| packed or encrypted blocks | 71 (10%) | **6 (1%)** |
| directly readable blocks | 545 (76%) | **934 (91%)** |
| POKEY accesses visible | 19 | **64** |
| OS ROM dependencies | 11 | **2** |
| boot loader and decryptor | present | **absent** |
| copy protection | duplicate sectors | **none** |

A cartridge has no loader, no decryptor and no disk I/O, so more of the game is
simply *there*. The two OS references left are the whole of its dependence on
the Atari operating system, against eleven on the disk -- most of which were the
disk loading itself, work a 7800 conversion does not have to do at all.

The POKEY number is the one that changes the estimate. Three times as much of
the sound code is visible, and sound is the part that carries over unchanged.
Against the cartridge the split is:

```
269 hardware accesses: 64 carry over (24%), 191 need rebuilding (71%)
```

against 12% carrying over from the disk. Same game, twice the free lunch,
because none of it is hidden behind a decryptor.

The artwork is easier too. The cartridge's most picture-like region, at
`$0BE00`, scores 0.176 on the row-correlation test at 20 bytes a row -- better
than anything on the disk, whose best was 0.209 -- and the widths that come out
are clean multiples of each other, which is what a sprite sheet looks like.

The disk stays in the recipe. It is the same game and useful for checking one
reading against the other, which is worth having when neither source comes with
documentation.

## What this assessment does not cover

**The counts are an upper bound.** `portscan.py` sweeps the image linearly and
reads every byte as a possible opcode, so data that happens to spell
`STA $D01A` is counted. The distribution is the finding, not the totals.

**It says where the work is, not how long it takes.** One `HITCLR` inside a
collision routine outweighs forty colour writes.

**The counts are static.** Everything above was read off the disk without
running it.

## Running it

Altirra 4.40 is in `../altirra`, unzipped rather than installed -- it is
portable and touches nothing outside its folder. It carries its own OS kernel
(ATOS), so no Atari ROMs are needed, and it reads ATX natively, which matters:
the protection here *is* duplicate sectors, and a plain ATR does not have them.

`Play Atari 8-bit.bat` launches it as a 48K NTSC Atari 800 with BASIC disabled,
which is the machine the game was written for in 1984. Drag any .atx/.atr onto
it for something else; `A8_HARDWARE` and `A8_MEM` override the machine.

**It cannot record input.** There is no equivalent of MAME's `.inp` files, so
an 8-bit session cannot be replayed deterministically the way the 7800 patches
were measured. Altirra does have video recording and save states, and a save
state is the practical substitute: it starts a measurement from the same point
every time, which is most of what a replay bought.

## The question that was worth answering next, answered

Does the 8-bit version pace itself the way the 7800 one does?

**No, and not by a small margin.** It was answerable statically after all --
see [karateka-a8.md](karateka-a8.md) for the trace.

The 8-bit cartridge installs an immediate vertical blank handler that runs ten
routines and returns: save zero page, sound, timers, scene state, choose a
display list, one raster colour change, poll the buttons, restore zero page.
Sixty times a second, unconditionally. None of it is a game entity. The game's
logic runs in the main line, which that handler interrupts.

The 7800 version parcels its logic out across frames instead -- nine entities,
one whole frame each, thirteen frames to think once. The two versions are not
the same program with different graphics. They are opposite arrangements of the
same game, and the arrangement is what the feel comes from.

Two consequences for the port:

- **Take the frame structure.** It is independent of the artwork and of MARIA,
  and it is most of what makes the 8-bit version feel controllable.
- **Do not take the memory model.** The 8-bit game runs from RAM: 31 KB of it,
  reloaded three banks at a time on every scene change. A 7800 has 4 KB. That
  is the largest single piece of work in the whole port, and it is invisible
  from screenshots.

Altirra's performance analyser is still the way to confirm this against the
running game rather than against a trace, and the recipe below is unchanged by
any of it.
