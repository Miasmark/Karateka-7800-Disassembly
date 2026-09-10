# The 7800, as much as a disassembler needs

Enough to read code with. For exact timings and electrical detail go to the
MARIA specification; this is the working subset, with the parts that mislead
people called out.

## Memory map

| range | what |
|---|---|
| `$0000-$001F` | TIA (audio, and the console-control register) |
| `$0020-$003F` | MARIA |
| `$0040-$00FF` | RAM -- **mirror of `$2040-$20FF`** (the 6502's zero page) |
| `$0100-$011F` | TIA again |
| `$0120-$013F` | MARIA again |
| `$0140-$01FF` | RAM -- **mirror of `$2140-$21FF`** (the 6502's stack) |
| `$0280-$02FF` | RIOT I/O -- joysticks, console switches, timers |
| `$0480-$04FF` | RIOT RAM |
| `$1800-$27FF` | the 4K RAM block |
| `$2800-$3FFF` | mirrors of RAM |
| `$4000-$FFFF` | cartridge |

The two RAM mirrors matter constantly in practice: a game will build a structure
at `$2140` and hand MARIA the address `$0140`, and both are correct.

## MARIA

MARIA draws the screen by walking a two-level list, and it does so by DMA --
stealing cycles from the 6502. How much it steals depends on how much you ask it
to draw, which is why 7800 code is so often written around a scanline budget.

**Display List List (DLL)** -- one entry per zone, three bytes:

```
byte 0:  bit 7    trigger a display interrupt at the end of this zone
         bit 6    holey DMA, 16K
         bit 5    holey DMA, 8K
         bits 3-0 offset: scanlines in this zone, minus one
byte 1:  display list address, high
byte 2:  display list address, low
```

**Display List (DL)** -- one entry per object drawn in that zone, four bytes:

```
byte 0:  graphics address, low
byte 1:  palette (bits 7-5) | width (bits 4-0, as 32 minus the width)
byte 2:  graphics address, high
byte 3:  horizontal position
```

...or five, if byte 1's low five bits are zero:

```
byte 0:  graphics address, low
byte 1:  bit 6 write mode, bit 5 indirect (character) mode, bits 4-0 zero
byte 2:  graphics address, high
byte 3:  palette | width          <-- moved
byte 4:  horizontal position
```

A byte 1 of zero ends the list. See `pitfalls.md`: the four/five ambiguity is
the single most reliable way to get a confident wrong answer out of a 7800 ROM.

### Registers

| addr | name | notes |
|---|---|---|
| `$20` | `BACKGRND` | border and background colour |
| `$21-$3F` | `P0C1`..`P7C3` | eight palettes of three colours; colour 0 is the background |
| `$24` | `WSYNC` | write to stall until the next scanline |
| `$28` | `MSTAT` | read: bit 7 set during vertical blank |
| `$2C`, `$30` | `DPPH`, `DPPL` | where the DLL is. **Write-only** |
| `$34` | `CHARBASE` | high byte for character (indirect) mode |
| `$38` | `OFFSET` | |
| `$3C` | `CTRL` | DMA enable, read mode, character width, border, kangaroo |

`DPPH`/`DPPL` being write-only is why finding a running game's display list
needs a write tap rather than a memory read -- see `probes/dumpdl.lua`.

### Palettes

Eight palettes, three colours each, plus one shared background. A display list
entry names which palette its object uses, so the same graphics drawn twice with
different palette numbers costs nothing extra.

A colour byte is hue in the high nibble, luminance in the low. Hue 0 is
greyscale. `tools/palette.py` converts to RGB well enough to read artwork by;
real output depends on the console and the television.

### What MARIA costs

MARIA draws by DMA and halts the 6502 while it does, so the frame budget is a
function of what is on screen. These are **measured**, not quoted: a cartridge
that spins a fixed-cost loop for the whole visible period and publishes the
iteration count, built once per display-list shape with nothing else changed.
`tools/dmabudget.py` implements the model and explains the method; the loop was
calibrated against extra NOPs (14.020 cycles/iteration fitted, 14.016 counted
by hand) and the model was checked by predicting four shapes it had not seen,
all within 0.3%.

NTSC: **262 scanlines/frame, 114.00 CPU cycles each, 29,868 cycles/frame.** Of
those, 21 lines are VBLANK, where DMA is off and the CPU has everything.

| what | CPU cycles | colour clocks |
|---|---|---|
| every scanline inside a zone, even an empty one | 5.63 | 22.5 |
| a zone boundary (the DLL fetch) | 1.68 | 6.7 |
| one 4-byte DL entry, per scanline | 2.08 | 8.3 |
| a 5-byte entry instead, per scanline | +0.48 | +1.9 |
| one graphics byte, per scanline | 0.74 | 3.0 |
| one character in character mode, per scanline | 1.49 or 2.25 | 6 or 9 |

The graphics byte landing on 3.0 colour clocks -- the documented figure -- is
the main reason to trust the rest of the table.

Two more costs, measured later and now in `dmabudget.py`:

| what | CPU cycles |
|---|---|
| one display interrupt (MARIA's signal plus the 6502's entry and exit) | 16.6 |
| a graphics byte in a region **holey DMA** suppresses | 0 |

Holey DMA is a large saving, not a rounding error -- two 20-byte objects over
192 scanlines went from 1,412 iterations a frame to 1,825 -- and it is exactly
the byte cost that disappears: the objects still pay for their display-list
entries. Which addresses are suppressed was measured, not looked up: with the
16K bit set a fetch is dropped when **address bit 12 is set** (`$D000` and
`$F000` free, `$C000` and `$E000` charged in full). The 8K bit made no
measurable difference anywhere in `$C000-$FFFF`.

Two things that cost nothing extra, also measured, because both looked like
plausible suspects for a discrepancy that turned out not to exist: **zone
height** (8-scanline and 16-scanline zones with the same objects agree to
0.2%) and **where the graphics live** (RAM and ROM measure identically).

A character costs one fetch for the character list plus its own data bytes, so
it is two fetches with `CTRL` bit 4 clear and three with it set. **Bit 4 set
means TWO bytes per character, not one.** This file and `a7800.py` both had it
the other way round until it was measured; the giveaway was that setting the
bit made drawing more expensive. Both shipping games in this series clear it.

For scale, Asteroids mid-play -- rocks, ship, score -- spends about 10% of its
frame on DMA. A full screen of 16-byte-wide objects can pass 60%.

### Holey DMA

With holey DMA on, reads from certain pages return zero instead of fetching.
Games use it so one display list entry can span a region where most of the
graphics are absent, without paying DMA for the empty parts. If graphics vanish
in a region that looks correct in the ROM, check the zone's holey bits.

## Character (indirect) mode

Set the indirect bit in a five-byte entry and the "graphics address" points at a
list of *character numbers* instead of pixels. MARIA forms each character's
address as:

```
((CHARBASE + line) << 8) | character_number
```

So a character set is stored line-planar, exactly like sprites: page
`CHARBASE+0` holds line 0 of all 256 characters, page `CHARBASE+1` holds line 1,
and so on. This is what makes text cheap -- one byte per character on screen --
and it is why a game's alphabet is often nowhere near ASCII. Render the pages
with `tools/gfx.py` and read the mapping off the image.

## TIA

On the 7800 the TIA is used almost entirely for audio: `AUDC0/1`, `AUDF0/1`,
`AUDV0/1` (control, frequency, volume). Two channels, and cartridges that wanted
more shipped a POKEY -- the header flags say where it is mapped.

`INPTCTRL` at `$0001` is the console-control register. Games write it during
startup to switch out of 2600 mode and enable MARIA; you will see it in the
first few instructions after RESET, which is how the reset probe in
`cart.py` recognises real startup code.

## POKEY, when a cartridge has one

The console has no POKEY. It is an extra chip *on the cartridge*, fitted by
games that wanted more than the TIA's two channels, and the `.a78` header says
whether one is present and where it answers.

Three placements occur, and **only one of them touches the address map**:

| flag | base | in the way of anything? |
|---|---|---|
| `$0001` | `$4000` | inside the cartridge address space |
| `$0040` | `$0450` | no -- unused space below the RIOT RAM |
| `$8000` | `$0800` | no -- unused space below the RAM block |

For the `$4000` case the chip claims `$4000-$400F`, which is cartridge address
space. In practice it never collides, and the library says why: every ordinary
cartridge declaring a POKEY at `$4000` is either **16K or 32K linear** -- so its
ROM starts at `$C000` or `$8000` and never reaches down that far -- or a
**SuperGame with nothing else mapped at `$4000`**, which is exactly the slot the
header's other flags would have filled with RAM or a bank. Of 1,309 images, two
declare a POKEY at `$4000` *and* ROM there; both are prototypes with confused
headers, and `cart.py` warns rather than silently reporting a layout it cannot
justify.

### Registers

Sixteen of them, and **reading one is a different register from writing it**:

| offset | write | read |
|---|---|---|
| `$0`-`$7` | `AUDF1`, `AUDC1` ... `AUDF4`, `AUDC4` | `POT0`-`POT7` |
| `$8` | `AUDCTL` | `ALLPOT` |
| `$9` | `STIMER` | `KBCODE` |
| `$A` | `SKRES` | `RANDOM` |
| `$D` | `SEROUT` | `SERIN` |
| `$E` | `IRQEN` | `IRQST` |
| `$F` | `SKCTL` | `SKSTAT` |

The audio pairs alternate -- frequency at even offsets, control/volume at odd --
which is how POKEY usage announces itself in a write trace: heavy traffic on
`$x001/$x003/$x005/$x007` against lighter traffic on the even ones.

The read/write split is not a curiosity. Ballblazer reads `$400A` a couple of
hundred times a second and never writes it: that is `RANDOM`, the hardware
random number generator, and the same address on write would be `SKRES`. A
disassembler knows which side an instruction is on, so it can pick the right
name -- `disasm.py` does, and `LDA RANDOM` next to `STA SKCTL` is a much better
listing than two references to `$400A`.

## RIOT

Joysticks and console switches at `$0280-$0283`, timers at `$0284`/`$0294-$0297`.
Reading `SWCHA` gives both joystick directions; the fire buttons come from the
TIA `INPT` registers, which catches people out.

## PAL and NTSC

PAL machines run 50 fields per second against NTSC's 60, and have more
scanlines. Any game that measures time in frames therefore runs slower on PAL
unless it was retimed, and plenty of European releases were not: a music track
whose durations are frame counts plays at 83.3% speed.

Use `a7800p` in MAME for PAL. Expect PAL and NTSC images of the same game to
differ substantially -- not just a flag, but relocated code -- so treat them as
two targets that share a design rather than one ROM with a switch.
