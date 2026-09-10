# Making a game

Everything else in this toolkit reads a cartridge somebody else wrote. This
page is the other direction, and it exists because six disassemblies kept
turning up the same small set of things that are hard to *discover* and easy to
state once known.

Start with a cartridge that already works:

```
python tools/newgame.py mygame --title "My Game" --build
mame a7800 -cart mygame/game.a78
```

That writes `mygame/game.asm` -- commented to be read and edited -- assembles
it, and produces a bootable `.a78` with a sprite moving across the screen. Edit
the art at the top of `newgame.py`, or the source directly.

## The order that matters

MARIA is a DMA engine walking a two-level list, so bringing a screen up is
mostly about doing four things in the right order:

1. **DMA off** (`CTRL` = `$60`) before touching anything it reads. Pointing
   MARIA at a half-written display list is the standard way to get a screen of
   garbage, and it looks like a data bug rather than a timing one.
2. **Build the DLL**, one three-byte entry per zone: flags and the offset in
   byte 0, then the display list's address high-then-low. The offset is
   **scanlines minus one**. The DLL may live in ROM -- MARIA fetches it by DMA
   and does not care -- and games build it in RAM only because they change it.
3. **Build the display lists**, four bytes per object. A list ends at the first
   entry whose byte 1 is zero, which is also why a width of 32 cannot be
   encoded: byte 1 carries the palette in bits 7-5 and **32 minus the width**
   in bits 4-0, so a full-width object would read as a terminator.
4. **Point `DPPH`/`DPPL` at the DLL and turn DMA on** (`CTRL` = `$40` for
   160A). Those two pointer registers are write-only; you cannot read back what
   you set, so keep a copy if you need one.

`CTRL` `$40` and `$60` are not chosen here -- they are what the 1987 cartridges
write to `$3C`. `dlwalk.py` decodes a display list if you want to check your
own against a shipping one.

## The part that is not like other machines

A direct-mode sprite is **not a bitmap**. MARIA fetches one scanline per page:
consecutive scanlines are 256 bytes apart, and the offset counts **down**, so
the top scanline comes from the *highest* page and the bottom one from the
address in the display list.

Store your art as a flat run of bytes and it will still assemble, still boot,
and draw one row repeated down the object -- a failure that looks like a
graphics-corruption bug and is really a layout mistake. `newgame.py` lays the
rows out bottom-up, a page apart, and the selftest asserts it, because this is
the single most reliable way to lose an afternoon.

To check your own artwork, render it the way the hardware reads it:

```
python tools/gfx.py mygame/game.a78 --space rom --base 0xD000 --direct 4 --lines 8
python tools/gfx.py mygame/game.a78 --space rom --base 0xD000 --direct 4 --lines 8 --sheet 16
```

If the sprite comes out sheared diagonally, the width is wrong; if it comes out
as one row repeated, the page striding is.

## Input

`SWCHA` (`$0280`) holds both joysticks -- player 1 in the high nibble, player 2
in the low -- and **every bit is active low**: a clear bit means pushed. Player
1 is bit 7 right, 6 left, 5 down, 4 up. The buttons are `INPT4` and `INPT1`,
bit 7 clear while held. Testing any of these the obvious way round is the
classic first input bug, and it fails silently: the game simply behaves as
though the stick is held in the opposite direction constantly.

The scaffold reads the stick and the button, so there is a working example to
edit rather than a register table to interpret.

## Splitting the screen with a DLI

Set bit 7 of a zone's DLL entry and MARIA raises an interrupt at the end of
that zone. Two things about it catch people out:

* **It arrives as NMI, not IRQ.** `SEI` does not protect you. The handler has
  to exist and be correct before you turn DMA on, not merely before you first
  want a split.
* **Whatever it changes stays changed.** Put it back during VBLANK or the
  screen splits once and then stays that way for good.

That is how a game gets more than eight palettes onto a screen, or a status
bar in different colours from the playfield. Keep the handler short: it runs
inside the visible frame, and every cycle in it is a cycle the game does not
get -- which the budget below will not know about unless you account for it.

## Artwork

```
python tools/mksprite.py ship.png --label ship -o ship.asm
python tools/mksprite.py rocks.png --frames 12 --label rock -o rocks.asm
```

Colours are palette indices, not RGB: four in 160A, two in 320A, index 0 being
the background and therefore transparent. To check what you got, render it back
the way the hardware reads it with `gfx.py --direct`, which is a genuine
round trip -- it will show you an upside-down sprite as upside-down.

## The budget

MARIA halts the 6502 while it draws, so "how much can my game compute" is a
question about how much is on screen. `dmabudget.py` answers it:

```
python tools/dmabudget.py --uniform 12,16,4,8 --afford
```

NTSC gives you **29,868 CPU cycles per frame** (262 scanlines at 114.00), and
drawing is charged per scanline, so a tall zone costs proportionally more than
a short one and an empty zone is not free. The numbers are measured -- see
`docs/hardware.md` for the table and the method.

For scale: Asteroids mid-play spends about **10%** of its frame on DMA, which
is what a screen of small objects costs. A full screen of wide objects or
character-mode tiles can pass 50-60%. If the report says you are over budget,
nothing warns you at runtime -- MARIA does not yield, the frame just arrives
with your logic half-done, which presents as sluggish input or a game that
speeds up when fewer things are on screen.

The cheapest things to cut, in order: object *width* (each byte costs on every
scanline of the zone), then object *count*, then zone height. Making a zone
empty for part of the screen is nearly free.

## Where the rest is written down

* `docs/hardware.md` -- register map, DLL and DL bit layouts, holey DMA.
* `docs/graphics.md` -- pixel formats, palettes, the offset direction, and how
  to find graphics in a ROM.
* `docs/pitfalls.md` -- every way this project has found to get a
  confident wrong answer, most of them found the slow way. The four/five-byte display list entry, the
  write-only registers, and the RAM mirroring are all in there.
* `docs/audio.md` and `tracker.py` -- music, including 6502 export with a
  player, if you want sound in the thing you just built.
