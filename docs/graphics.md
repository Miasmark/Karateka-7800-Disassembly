# Finding and reading 7800 graphics

## Everything is line-planar

This is the one structural fact to internalise. MARIA fetches scanline *n* of an
object from **page (high byte + n)**, same low byte. So an 8-line sprite at
`$8040` is:

```
line 0  $8040   line 4  $8440
line 1  $8140   line 5  $8540
line 2  $8240   line 6  $8640
line 3  $8340   line 7  $8740
```

Not a contiguous 8-byte block. Dump it contiguously and you get one line each
from eight unrelated objects -- a striped mess that is easy to mistake for
compression.

A corollary worth having in mind: a "sprite sheet" in a 7800 ROM is organised by
*line across all objects*, so a page of ROM holds one row of many sprites. When
you find a page that looks like a horizontal smear of fragments, you have found
graphics, not garbage.

## The zone offset counts DOWN

**The first scanline of a zone reads the highest page and the last reads the
base.** So the top line of a sprite `H` lines tall is at `base + (H-1)*256`, not
at `base`, and reading the pages upward renders every sprite upside down.

This is easy to get wrong and easy to miss, because an upside-down sprite is
still a plausible-looking sprite. `gfx.py` and `spriteedit.py` both did it the
wrong way round until someone looked at a rendered sheet and said so.

Midnight Mutants supplies the proof. Its lettering at `f7:$E0` spells **GAME
OVER** one way round and nothing the other:

```
ascending    (wrong)      ᴚƎVO ƎMAϽ
descending   (right)      GAME OVER
```

Both tools now read descending by default and take `--ascending` for data that
is not a MARIA zone -- a contiguous blob, or a table you are viewing as pixels,
where the lines really do ascend.

## Pixel formats

`CTRL`'s read mode plus the display list entry's write mode select the format.
The two that matter most:

* **160A/160B** -- two bits per pixel, four pixels per byte, MSB first. Each
  2-bit value indexes into the entry's palette (value 0 is transparent /
  background).
* **320A and 320D** -- one bit per pixel, eight per byte, twice the horizontal
  resolution and correspondingly fewer colours.
* **320B and 320C** reach four colours at 320 resolution by pairing bytes, so
  they are *not* the same shape as 320A. `gfx.py` and `spriteedit.py` read the
  1-bit form only; artwork in B or C comes out the right size and the wrong
  image.

The same bytes decode differently under each, so if artwork comes out as noise
in one mode, try the other before concluding it is compressed.

## Character mode

In indirect mode a display list entry points at a list of character *numbers*,
and MARIA forms each character's graphics address as:

```
((CHARBASE + line) << 8) | character_number
```

Character sets are therefore line-planar too: page `CHARBASE+0` is line 0 of all
256 characters, `CHARBASE+1` is line 1, and so on.

That is how the set must be laid out *at the address CHARBASE names* -- it is
not necessarily how the cartridge stores it. A ROM is free to keep its font
**linearly**, each glyph's scanlines in consecutive bytes, and de-interleave
into the line-planar form while copying the set into RAM at init. One shipped
game does exactly that, storing 4x5-pixel glyphs six bytes apart (five
scanlines plus a pad byte) and interleaving two pointers a fixed distance apart
as it copies. Rendered with the line-planar reader, such a font is noise at
every base and every `--lines` value, because the grid slices across glyph
cells instead of along them. `gfx.py --linear CELL` reads the linear form; if a
charset render is unrecoverable noise everywhere, try it before concluding the
region isn't graphics.

This is why game text is so often not ASCII. The character number is an index
into whatever order the artist drew the alphabet in, and a game that only needs
capitals, digits and a few punctuation marks will pack them however it likes.

**To read a game's alphabet:** render the character-set pages as an image, look
at the picture, and write down the mapping. Ten minutes with `gfx.py --sheet`
beats any amount of frequency analysis on the text bytes.

## Finding graphics in an unknown ROM

1. **If the game runs, walk the live display list first.** `dlwalk.py` reports
   what MARIA is actually being pointed at, with the width and line count
   already decoded -- which beats inferring them, and is sometimes the only
   thing that works at all. In one 16K title the 4K sprite block had **zero**
   absolute references anywhere in the ROM: no `LDA #$Cx / STA ptr` pointer
   setup, no pointer table, nothing a static search could find, because the
   display list is built in RAM and the addresses only ever exist there. Every
   static search for it was guaranteed to fail before it was run.
2. `survey.py` flags banks whose entropy runs above about 7 bits/byte. Bitmap
   graphics use the full byte range fairly evenly; code does not.
3. Render candidate pages with `gfx.py` and look. Human pattern recognition is
   the best tool available here and it is not close.
4. Once you find one object, its neighbours are usually adjacent -- artwork is
   laid out in blocks, and finding the block boundaries tells you the frame
   count of an animation. For a direct-mode sheet the frames are usually packed
   at a stride equal to the object's own width, so `gfx.py --direct W --sheet N`
   lays the whole set out at once. Do that before trying to read any single
   frame: a wrong `W` shears the sheet diagonally, which is unmistakable across
   thirty frames and invisible in one, and the bank boundaries and the frame
   count both fall out of the same picture.

The ordering matters. Steps 2 and 3 are a search of the cartridge; step 1 is a
question put to the running machine, and it answers directly what the others
only narrow down.

## Palettes

Eight palettes, three colours each plus a shared background; a display list
entry names which one its object uses. So the *same* graphics drawn with a
different palette number is a completely different-looking object at no cost in
ROM, and games lean on this heavily -- expect to find one sprite used for four
enemies.

Colour bytes are hue in the high nibble, luminance in the low; hue 0 is
greyscale. `palette.py` converts to RGB closely enough to identify artwork by
eye. It will not match a real television, and it should not be trusted for
judgements like "is this colour visible against that background" -- for those,
run it and look.

One consequence worth watching for: a palette whose colour 0 is black makes
anything drawn on it invisible against a black background. That is a real bug
class, not a hypothetical -- a commercial game shipped two inventory icons drawn
on such a palette, one of them entirely invisible.

## Animation

Animation frames are chosen by a frame counter indexed into a table. Two things
to check whenever you find one:

* **The table's length against the counter's range.** A counter that can reach 4
  indexing a 4-entry table reads one past the end -- usually the first byte of
  whatever follows, drawn as a frame. This is a common shipped bug and it looks
  completely deliberate in a listing.
* **Which counter it uses.** Games often keep several counters running at
  different rates (every frame, every other frame, every eighth). Which one an
  animation reads *is* its speed, and switching a table from one to another is
  the cleanest possible way to retime an animation without touching its frames.


# Finding the artwork without knowing where it is

`audiotrace.py` finds the music by following the code to the sound chip.
`assets.py` does the same for pictures, and then hands both to the tools that
work on assets rather than on listings.

```
python tools/assets.py game.a78 -c annotations.json     --manifest assets.json --blocks blocks.json
```

Every store to a MARIA pointer register is the hardware being aimed at
something. Trace back through straight-line code to whatever supplied the
address and you have a display list, a character base, or a sprite table.

## The display list is in RAM, and that is not a failure

Run it statically on a real game and every display-list pointer comes out
pointing at RAM:

```
5 of the 5 display-list pointers point into RAM ($0140, ...).
```

That is correct, and it is the whole shape of graphics on this machine: the
display list is **built in RAM every frame**, so it does not exist until the
game runs. A static trace has nowhere further to go. What it *can* find
statically is CHARBASE -- though note that CHARBASE names *an address*, not
necessarily a ROM one. A game that copies its character set into RAM at init
will have CHARBASE pointing into RAM, and a static read of it tells you where
the set ends up rather than where it is stored. If CHARBASE resolves to RAM,
the set is being relocated from somewhere in the cartridge and the copy routine
is what leads back to the source.

To get the rest, capture a live list and feed it back:

```
mame a7800 -cart game.a78 -autoboot_script probes/dumpdl.lua      -video none -sound none -nothrottle -str 30
python tools/assets.py game.a78 --ram ramdump.bin --dll 0x0140     --manifest assets.json
```

On Midnight Mutants that turns 5 dead pointers into 183 graphics addresses.

## What it refuses to claim

**A data run is not an asset.** An unreached run is just "bytes the tracer
never got to", and a big one holds artwork and lookup tables side by side. An
earlier version labelled whole runs by a single hit inside them and duly tagged
`tbl_DefAmount` as graphics. Now each address the hardware was actually pointed
at is its own entry.

**A windowed address has no bank.** MARIA read `$80A8` from whichever bank was
mapped at that instant, and the capture does not record which. So of 183
addresses only 15 are reported as findings; the other 168 are listed as
candidates with every bank they could belong to. Guessing one would be right
about an eighth of the time and confident every time.

**A block cannot describe a sprite.** 7800 graphics are line-planar -- `W`
bytes on each of `H` successive pages -- so a sprite is not a run of bytes and
no `blocks` entry can span it. Only character sets and audio tables become
blocks. Sprites go in the manifest with their width, where `gfx.py --direct`
can draw them properly:

```
python tools/gfx.py game.a78 --space b3 --base 0x8020 --direct 24 --lines 8 -o obj.png
```

Get `W` (`--direct`) and `H` (`--lines`) from the live display list
(`dlwalk.py`), not a guess -- and don't reach for the indirect-mode default
(no `--direct`) for a single object just because it's the tool's default
shape. That default reads a 256-wide grid sharing the object's low byte, so
pointing it at a narrow direct-mode object without `--direct` "succeeds" by
drawing up to 255 unrelated neighbours around it -- whatever else happens to
share that low byte at a different page, background fills and other tiles
included -- and nothing in the output says so. A ROM with several
narrow direct-mode tiles between two real character sets found exactly this
the hard way: the ungated grid render showed what looked like genuine,
unrelated artwork sitting two dozen pages deeper than the tiles' real
few-scanline extent, and only reading exactly `W`×`H` showed what was
actually there -- solid colour fills and diagonal tile edges, not a sprite.
Indirect mode (no `--direct`, the default) is for an actual character set --
a real 256-entry sheet like `$A000` above, where the grid *is* the right
shape and `--lines` is genuinely a font's row height, not a sprite's.

## Changing it

```
python tools/spriteedit.py game.a78 --space b3 --base 0xA000 --height 8
python tools/spriteedit.py game.a78 --manifest assets.json
python tools/spriteedit.py game.a78 --side maria --space f7 --base 0xC000
```

`--side` is for bankset cartridges, and `gfx.py` takes it too. Their
artwork lives in the half the CPU never reads, so `--side maria` is
usually the one you want; opening the CPU's half says so rather than
showing you an empty grid and letting you wonder.

A grid you click to paint, backed by the same addressing `gfx.py` draws with, so
what you see is where the bytes are. Tools: pen, flood fill, line, rectangle and
ellipse (outline or filled), plus undo, copy/paste between cells, clear, flip
and shift-with-wrap. `ctrl+Z`/`C`/`V`, `0`-`3` pick a colour, `[` and `]` step
through cells. `--width` is bytes per cell per line (1 for
a character set), `--stride` the gap between one line and the next (256 for
anything MARIA fetches), and `--mode` picks 2 bits per pixel or 1.

Saving writes a new image and reports what moved:

```
wrote game-edited.a78 — 6 bytes changed, all inside the 2048 this region owns
```

**It refuses to save if a byte outside the region would change.** Artwork sits
next to code and tables, and a paint program that quietly clips into the routine
after it is worse than no paint program. The check is a diff against the
original, not a promise.

That the layout is line-planar is visible in the result. Six pixels painted down
the left edge of one character changed six bytes, at file offsets `$E2C6`,
`$E3C6`, `$E4C6`, `$E5C6`, `$E6C6`, `$E7C6` — one page apart each, because
consecutive lines live on consecutive pages.

## Finding the palette

You largely cannot, and it is worth being clear about why. **The colours are not
in the artwork.** MARIA keeps them in registers -- `P0C1`-`P7C3`, three colours
per palette plus a shared background -- and a display-list entry names which of
the eight to draw with. A game rewrites them whenever it likes: per zone, per
frame, for a fade. The same bytes of sprite data are a different picture an
instant later.

So both tools render in greys unless told otherwise, which shows the true pixel
indices rather than an invented picture.

What can be recovered is the set of values the code writes as constants:

```
python tools/assets.py game.a78 -c annotations.json --manifest assets.json
```

```
4 palette slots written as constants, 3 complete
   palette 0    $24  |  $76  |  $7C
   palette 6    $FF  |  $F0  |  $10
   palette 7    $0C $70  |  $20 $B4  |  $0C $11
   background   $00 $08 $0F $25
```

A slot with one value per index is very likely a real palette. A slot with
several is a fade or a per-level change, and which one belongs to a given
sprite is a question only the running game answers.

Hand them to the editor and pick by eye:

```
python tools/spriteedit.py game.a78 --space b3 --base 0xA000     --palette-from assets.json
```

The workbench does that for you after a scan. Right-clicking a swatch also opens
the full 16x16 hue-by-luminance grid, and `gfx.py --palette 24,76,7C` applies
one to a rendered sheet.
