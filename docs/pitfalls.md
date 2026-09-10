# Traps

Every one of these produced a confidently wrong answer during a real 7800
disassembly. They are in rough order of how much time they cost.

## MAME write taps are garbage-collected

```lua
mem:install_write_tap(0x20, 0x3F, "pal", function(o, d) ... end)   -- WRONG
TAP = mem:install_write_tap(0x20, 0x3F, "pal", function(o, d) ... end)  -- right
```

`install_write_tap` returns a tap object. Drop the return value and Lua collects
it the next time the collector runs -- a few hundred frames in, typically well
after boot. The tap fires during startup, then silently stops.

What that looks like: plausible output, from a probe that has quietly died. It
produced two false conclusions in a row -- "nothing writes to the palette during
play" and "palette 6 is never animated" -- about a game whose palettes are
rewritten every single frame. Neither error announced itself; both looked like
findings.

**Any negative result from a tap is suspect until you have proved the tap was
still alive.** Have the probe count writes to something you know is busy and
print the count. If that number stops growing, your tap is dead.

## Five-byte display list entries put the palette somewhere else

A MARIA display list entry is four bytes, unless it is five. You tell from
byte 1: if its low five bits are zero -- and the byte is not zero, which would
end the list -- the entry is extended, five bytes long, and **the palette and
width are in byte 3**.

Read an extended entry as a direct one and every field you extract is wrong but
valid-looking. A real case: an entry with byte 1 = `$60` was read as palette 3.
Byte 1's low five bits are clear, so it was extended, and the actual palette
came from byte 3 -- palette 0. The wrong answer was consistent with everything
else visible and survived until a hardware probe contradicted it.

`tools/dlwalk.py` handles this, and its `--selftest` demonstrates the failure
mode on a hand-built pair of entries.

## The zone offset counts down, and sprites are line-planar

A DLL entry's offset field is the number of scanlines minus one, and MARIA
decrements it as it draws. Graphics for successive scanlines therefore come from
successive *pages*, not successive bytes: scanline n of a sprite lives at
(high byte + n) << 8 | low byte.

So a sprite is not a contiguous block. Dump it as one and you get stripes from
eight unrelated objects. Every graphics tool here reads line-planar by default
for this reason.

## RAM is mirrored into the first two pages

The 6502 needs a zero page and a stack, so the 7800 mirrors `$2040-$20FF` down
to `$0040-$00FF` and `$2140-$21FF` down to `$0140-$01FF`.

Games use both views freely, often building a display list through the low
addresses and pointing MARIA at it there. A dump of the RAM block will then be
asked for an address it does not appear to contain. `dlwalk.unmirror()` folds
them; do the same anywhere you map addresses to a dump.

## The header's mapper bits are not what the published lists say

Several widely-copied bit tables put Activision banking at `$0200` and Absolute
at `$0400`. Checked against 1,309 real images, the cartridges disagree:
Double Dragon and Rampage (Activision) read `$0100`, and F-18 Hornet (an
Absolute-mapper game) reads `$0200`.

Worse, some bits are not mappers at all. `$0800` looks like a mapper flag and is
set on 45 images -- every single one of which names YM2151 in its filename, and
is otherwise an ordinary 48K or 128K cart. Treating it as a mapper meant
refusing 45 perfectly readable ROMs.

The lesson generalises: when a spec and a corpus disagree, the corpus is the
thing that actually has to run. `tools/cart.py` documents which bits were
confirmed against which known games, and marks the rest as unconfirmed rather
than guessing.

## A header-inclusive patch is refused by an identical ROM

Two dumps of the same PAL cartridge held byte-identical cartridge data and
differed only in the declared cart type in the header. A BPS patch built across
the header therefore refused one of them, for a difference that has no effect on
anything.

Build patches against the cartridge data only and keep whatever header the
target image came with.

## The tracer's silence is not evidence

Anything a recursive-descent tracer cannot reach comes out as `.byte`, and the
listing still round-trips perfectly. Byte-identity says you have every byte; it
says nothing about whether you have understood them.

Two specific ways this bites:

* **A table read past its end.** A countdown used as an index into a table one
  entry shorter than the countdown allows will read whatever follows -- usually
  the first opcode of the next routine -- and use it as data. It looks
  deliberate in a listing. Two of these were sitting in one commercial game,
  each drawing a garbage frame at the start of a death animation.
* **Unreferenced data that is not unreferenced.** Before declaring a block
  unused, check every way it could be addressed, including as the second half
  of a 16-bit pointer and as a page number assembled at run time. A block
  "proven" unreferenced twice turned out to be reached through a page register
  loaded from a different table entirely.

## Cross-bank references need a bank, not just an address

In a banked cart the same address means different bytes depending on which bank
is in the window. A reference to `$8123` is only meaningful together with the
bank that was selected when it executed. Track the bank alongside the address
everywhere -- the "space" idea in `cart.py` exists for exactly this -- or you
will eventually chase a routine that is not there.

## Reference resolution has to come from the mapper too

The tracer was taught to ask the mapper where a bank switch lives and what a
written value means. The *emitter* was not, and kept its own hardcoded copy of
SuperGame's map: `$4000-$7FFF` is f6, `$C000+` is f7, the middle is the window.

On any other layout that is wrong. An Absolute cartridge puts its **window** at
`$4000-$7FFF`, so every reference there asked for a space called "f6", found no
label, and quietly printed a bare address instead. The listing still assembled
-- a literal `$5039` is perfectly valid -- so the round trip passed and nothing
complained. F-18 Hornet came out with **1,110 bare operands and five labelled
ones**: a jump table at `$5000` whose targets had no names at all.

The lesson is not "check the emitter" but that *one* component knowing the
memory map is not enough. Anything that turns an address into a name needs the
same source of truth, and a round-trip test will not catch the difference,
because both spellings assemble.

## A branch cannot leave its bank

Resolving a control transfer by asking `space_of(target, bank)` returns nothing
when the tracer does not know which bank is in the window -- so the transfer was
filed as unresolved and no cross-reference was kept. For transfers *inside* the
window that is needlessly pessimistic: a branch is relative and physically
cannot leave its bank, and a JMP or JSR within the same window region runs
before any switch could take effect.

The cost was invisible in the same way. In Midnight Mutants, `b5:$B96E` is
branched to from three bytes earlier, and printed as a bare `$B96E` with no
label and no xref, because that stretch of bank 5 had been reached without the
tracer knowing the bank. Fixing it added twenty instructions of coverage and
turned the last thirteen bare operands into labels.

Both of these were found by reading DiStella's source and asking what it does
that this does not.
