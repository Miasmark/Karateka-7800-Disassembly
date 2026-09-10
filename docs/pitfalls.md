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

## `video:snapshot()` under `-video none` can show a pixel that was never drawn

A screenshot taken with `MACHINE.video:snapshot()` while running `-video none`
showed a small bright dot on screen, at a screen position that appeared to
shift between frames in a way that looked like it was tracking a game object.
It wasn't. Reading the same coordinates directly off the framebuffer with the
screen device's own accessor --

```lua
local scr = MACHINE.screens[":screen"]
scr:pixel(x, y)   -- returns a packed ARGB int; unpack with plain arithmetic
```

-- returned the surrounding background colour at every one of those
coordinates, on every frame the screenshot had shown a dot. The PNG was
rendering something the hardware never actually output.

`-video none` is meant for headless runs that never look at a frame at all.
Asking it for a snapshot anyway still produces a file, with no error, and nothing
about the output looks wrong -- it takes a `scr:pixel()` cross-check (or an
actual `-video` backend) to notice the picture and the framebuffer disagree.

**Any visual claim made from a `video:snapshot()` PNG taken under `-video
none` needs a `scr:pixel()` (or real `-video soft`/`bgfx`) cross-check before
it goes in a finding.** A `-video soft` re-render of the same frame is a valid
independent check but can be far slower against a long recording than just
reading the pixel directly -- reach for `scr:pixel()` first.

## A read/write tap's reported PC can be MARIA's DMA, not the 6502

A PC-tagged tap on a ROM address that came back with real, non-trivial hit
counts -- thousands of them, spread across dozens of instruction addresses --
looked like solid evidence of several genuine readers. Every single one turned
out to be false. The reported PCs resolved to instructions with no reference to
the tapped address at all: `LDA ram_2230,X`, `ROL A`, `CLC` -- register-only or
RAM-indexed operations that cannot have produced a read of a ROM address
elsewhere on the map. One batch of hits was even attributed to a PC that was
itself a RAM address, which cannot be a real 6502 program counter.

MARIA's own graphics DMA shares the same bus the 6502 does, and it runs
constantly during normal play -- scanning display-list entries, including empty
ones, every frame. A tap installed on the shared address space cannot tell a
6502 fetch from a MARIA fetch; it reports whatever the CPU's PC register
happens to hold at that bus cycle, which for a MARIA-driven access is
essentially arbitrary. The signature is a tap with real, sustained hit counts
whose reported PCs, checked one by one, never actually name the tapped address
in their own operand.

**Before trusting a tap's PC as a genuine reader, open the listing at that
address and confirm the instruction actually references the tapped range.**
"Fires a lot" is not evidence by itself when the bus is shared -- only "fires,
and the instruction at that PC reads or writes this exact address" is. This
caught two false leads in one project (a set of allegedly-live RAM cells that
turned out to be permanently dead, and a ROM data gap that looked read but
wasn't) using the identical check both times.

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

## A data block's start address can be a literal operand elsewhere in the code

A `blocks` entry's `loc` looked like it overlapped the table before it by one
byte -- easy fix, move `loc` forward by one. The round trip failed immediately,
diverging at an unrelated instruction several hundred bytes away. The real
cause: some other routine loads that exact address as a two-byte immediate
constant (`LDA #$FF / STA ptr_lo`, `LDA #$96 / STA ptr_hi`, forming a pointer),
and the emitter had resolved that literal to the block's own symbolic name.
Move the block and the literal now points at a name that means a different
address -- the assembled bytes change, silently, because both spellings are
individually valid 6502.

The overlap suspicion was reasonable (a table's declared length looked short by
exactly one byte) and still turned out to be wrong: the byte in question was
real data belonging to the table, coincidentally zero, not evidence of a
boundary error. A block's `end` is usually safe to extend when it stops short
of real code with no gap in between -- that was tested and confirmed repeatedly
on the same project with no breakage. Its `loc` is the one that can be
load-bearing elsewhere in the ROM. **Verify with a full round-trip after moving
a block's start address, every time, even when the change looks obviously
correct** -- especially then, since an obviously-correct change is the one you
are least likely to check.

## A `gfx`/data block silently overrides `entries`, even at a confirmed vector

A block's `end` was extended past a game's actual IRQ entry point, on live
display-list evidence that looked continuous right up to a known-real routine
further along. The IRQ vector (`$FFFE/$FFFF`, unambiguous -- it's read
straight from the cartridge, not guessed) pointed inside that "confirmed"
block. The listing rendered the labeled entry point as `.byte $40` instead of
`RTI`: the disassembler had accepted the `entries` seed, then discarded it,
because `forced_data` (what a `blocks` entry populates) is checked *before*
the opcode is even read in the trace loop -- `if loc in self.forced_data:
break`. `entries` never overrides a block; a block always wins. This can hide
an entire subtree of real code with nothing louder than a label sitting on
top of a byte dump, easy to skim past if you don't notice the mnemonic column
is empty.

The lesson generalizes past this one case: **any address in `entries` (or
reachable from one) that ends up inside a `blocks` range is not being traced,
full stop** -- regardless of how strong the evidence for that block's extent
looked. After declaring or widening a `gfx`/data block, grep the regenerated
listing for `J(SR|MP)\s+dat_<block's address range>` (a real call landing on
a `dat_`-prefixed label is direct proof of misclassified code -- this is how
the case above was actually caught, a sound-effect routine called from
thirteen places had been sitting under the block, silently rendered as
`.byte` the whole time) and check every declared `entries`/label address
against the block's span by hand. Two rounds of "shrink the block until the
round trip and the evidence agree" were needed before this was fully
resolved -- the first fix (an end address short enough to stop overlapping
the *known* real code after it) still overlapped the vector, because the
vector was in the *middle* of the block, not at either edge.

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

## Live churn confirms activity, not what a byte means

Two RAM arrays lit up on nearly every one-second snapshot across a whole
recording, twelve bytes each, right where a twelve-segment centipede would
put its position. Concluded: row array, column array. Both got named and
committed that way, live evidence and all.

They were a status-bits byte and a direction/graphics-offset byte for an
18-slot general object table (every enemy type plus the player's own shot),
not coordinates at all -- caught later, independently, by finding the exact
instruction sequence for a poison-mushroom collision check operating on one
of them with `AND`/`ORA` against bit-pattern constants (`$40`, `$E7`), not
`ADC`/`SBC` against anything coordinate-shaped. Live data proves a byte is
*doing something every frame*; it says nothing about *what kind of thing* --
a status/flags byte that toggles state on every collision check churns just
as continuously as a position that updates every frame, and from a
snapshot-diff alone the two are indistinguishable.

The check that would have caught it directly, and the reason it wasn't run:
**a 6502 idiom for testing or setting bits almost never puts a bitwise
mnemonic on the address itself.** `AND`/`ORA`/`EOR` do have direct memory
addressing modes, but real code overwhelmingly loads the value into A first
(`LDA addr,X`) and masks with an immediate afterward (`AND #$40`) -- so
grepping the generated listing for which mnemonics reference an address
directly (the `most-referenced RAM addresses` report's own address column,
or any `xrefs:` line) will show mostly `LDA`/`STA`/`CMP` regardless of
whether the byte is a coordinate or a bitfield, because the bitwise op that
would give it away is one line down, working on the accumulator, with no
operand naming the address at all. (A version of this was tried as an
automatic classifier in `disasm.py` itself -- tally bitwise vs. arithmetic
mnemonics referencing each address, print a verdict. It mis-classified the
exact motivating byte, because the same byte also had a 2-bit sub-counter
packed into it and `DEC`'d directly, which is memory-direct and *does* show
up this way -- a packed byte can legitimately be mixed. Reverted rather than
shipped, since a wrong automatic verdict here is worse than no verdict: it
would have produced the same false confidence as the original mistake, just
with a tool's name on it instead of a human's.)

The manual check that does work: for a byte suspected of being a coordinate
or counter, grep the listing for every `LDA <name>` (or the indexed form)
and read the one or two lines immediately after each hit. Bit-pattern
constants there (especially ones that aren't round numbers -- `$40`, `$E7`,
not `$05` or `$0A`) mean bitfield, regardless of how the byte behaves live.

## A small-range index doesn't have to mean "player slot"

Four evenly-spaced fields, seven bytes apart, each three bytes long, each
touched by `LDA/STA field,X` with X ranging 0-2. The obvious read: three
players' worth of some four-part value. Four *separate* three-byte BCD
numbers -- player one's score, player two's, a team score for co-op mode,
and a spare block belonging to something else entirely -- with X selecting
a byte *within* one number, not a player.

Both readings explain the exact same access pattern equally well from the
code alone; nothing about `LDA field,X` distinguishes "X indexes a
digit-place shared by three players" from "X indexes a byte within this one
independent field, and the four fields are unrelated to each other except
by being the same shape." The tell, when there is one, is usually external
to the indexed instructions themselves: what other code treats the *whole
field* as a single unit (a 3-byte BCD add with carry chained across exactly
those three bytes and no further, for instance, is a strong sign of "one
number," and if there are four such adds at four different base addresses
rather than one parameterized loop over three player slots, that is itself
the answer). Don't let "a loop variable that happens to range over a small
number matches the number of players" stand in for actually checking what
happens at each value of that variable.

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

## A periodic RAM snapshot can miss a real, frequent write

A "sample every N frames, keep only bytes that ever changed" probe (cheap,
and generally a good first tool) reported three RAM cells as constant across
an entire ~26,000-frame recording, spanning eleven confirmed level
transitions of a level-indexed table write that should have changed each
time. Read as "this code path doesn't actually run per level" and the whole
finding was downgraded on that basis.

It was wrong. A *different*, unrelated routine (an in-flight projectile's
position setup) wrote to the exact same three cells constantly during normal
play, because the game reused them as shared scratch space between distinct
subsystems. The level-indexed write did fire, every time, exactly as
expected -- but its value was overwritten by the unrelated routine within a
frame or two, and the once-a-second snapshot essentially never landed on the
narrow window where it was visible. A dedicated PC-tagged write-tap on the
exact three addresses, unthrottled, caught all of it immediately and matched
every value against the source table by hand.

The general form: **absence of change in a coarse periodic snapshot is
evidence about that snapshot's sampling rate, not about whether writes are
happening** -- especially for any RAM address that shows up as a write target
from more than one place in earlier probing (a strong hint it's reused
scratch space, not dedicated state). When a snapshot-based finding
contradicts a specific, testable prediction (like "this address should
change at this exact frame"), reach for a write-tap on that one prediction
before trusting the snapshot's silence.

A second, distinct way to reach the same wrong conclusion: a candidate BCD
score-add routine (right shape on paper -- carry-chained accumulator add,
selected by an index, a near-exact structural match for an already-confirmed
score finding in a sibling project) showed zero net change in six candidate
bytes across a whole ~10-minute recording, and was written up as a rejected,
probably-inactive finding. It was live *every frame*, unconditionally --
just usually adding zero, because most frames have no scoring event to
apply. No other routine was overwriting anything here; the accumulator
simply spends nearly all of its life not moving, by design, and a coarse
sample can't tell "runs constantly, net-zero most of the time" apart from
"never runs" without also checking a narrow, high-activity window where a
real change is expected (here, a window already known from a *different*
confirmed live event happening at the same moment). Same fix as the case
above -- a narrow, unthrottled, frame-exact write-tap -- but a different
root cause worth naming on its own: the write target doesn't have to be
shared scratch space for coarse sampling to lie; a legitimately-quiet
accumulator does it just as well.

## A computed jump table can be wider than its first N entries suggest

A table built as `LDA lo_table,Y` / `LDA hi_table,Y` / `JMP (ptr)` was
computed by hand from its declared bytes for `Y=0..7` (an apparent 8-entry
table, lo and hi arrays 8 bytes each, back to back in ROM). Two of the eight
looked like garbage addresses (outside plausible code space) and were
written off as unused table slots; the other six were declared as trial
entry points and traced cleanly. Case closed, or so it seemed -- except live
PC-tagged probing kept finding real, executing code a few bytes past where
the sixth entry's routine returned, with no static cross-reference pointing
at it from anywhere in the traced program.

The actual index (a state/stage counter, not a fixed small enum) ran higher
than 7 during real play. Reading the lo table at `Y=8` walks past its own
8-byte declared end and *into the hi table's first byte* -- which is exactly
where it needs to be, because the hi table sits immediately after the lo
table with no gap, and the two "garbage" `Y=0`/`Y=1` entries' hi-bytes double
as `Y=8`/`Y=9`'s lo-bytes once the low byte for the corresponding target
address happens to match. The matching hi byte for the overrun entries comes
from ROM bytes immediately past the hi table's own declared end. Both
"overrun" targets turned out to be real, live-confirmed code.

The lesson: when a table-driven dispatch's index is a *counter* rather than
a hardcoded small set, don't assume the table's width from "how many entries
look plausible" -- check what value range the index variable actually reaches
live, and treat unreachable-looking table slots as a hint to keep reading
past the declared boundary, not as proof the table stops there.

## A private reference can be right about the game and still wrong about your ROM

Cross-checking a project against a privately-consulted, unlicensed
historical source for the same game (never quoted or copied in -- used
strictly to generate hypotheses to re-derive independently) mostly
corroborated existing findings, including one case where the outside
source's own constants matched two threshold values this project had
already found by reading its own code, which was enough on its own to
settle what a mysterious ~600-frame counter climb actually represented.

But two other subsystems -- a monster-movement table and a level-counter
readout -- were described by that source as built a structurally
*different* way than what this project's own live-verified bytes showed:
one direction-computed-live where this ROM reads a table, one
arithmetic-per-frame where this ROM writes from a table. The instinct to
resolve the mismatch by re-reading this project's own code for the
missing piece (a matching arithmetic routine) came up empty -- and rather
than force a fit either direction, both findings were left honestly
un-reconciled, with the project's own mechanism kept as confirmed and its
*identity* downgraded back to an open question.

The general lesson: a reference source for "the same game" is not
automatically a reference for *this specific build* of it -- ports
routinely restructure a subsystem (table-driven here, computed-live
there) or change specific constants (a scoring table came out with
different absolute values, indexed differently, in the same
cross-check) without changing what the player experiences. Treat
disagreement as data, not as an error to resolve in either direction:
keep whatever this project already independently verified against its
own ROM, note the mismatch, and don't let an outside source talk you out
of live-confirmed evidence -- but also don't let it talk you into
over-claiming an identity your own bytes haven't actually settled.

## MAME's "Total playback frames" line reports where the script stopped, not where the recording ends

Building a probe against a `.inp` recording, an early exploratory run's
own printed summary (`Total playback frames: N`) was taken at face value
as the file's true length and used to size every later probe's own
`MACHINE:exit()` threshold. Every subsequent probe in the project then
"confirmed" a number in that same neighborhood -- because each one was,
by construction, incapable of running past its own threshold to find
out otherwise. A live-vs-real-time determinism check even passed cleanly
against this same wrong boundary: both runs agreed with each other, which
felt like confirmation, but they were agreeing about a boundary neither
of them was free to cross.

The actual file was close to three times longer than every probe had
been reading. A byte-pair that tracked cleanly against the true range
(and against independently-supplied ground truth: specific score/value
checkpoints from the person who made the recording) had been rejected
outright one project-day earlier, on the grounds that live data showed it
"only reaching 5" -- true, for the ~35,900 frames the probe actually
looked at, and irrelevant to what the byte did across the other roughly
70,000 frames nobody had asked it about yet.

The fix came from a plain domain-expert observation, not from re-reading
any log more carefully: told that watching the recording play back
*without giving it any further input* still showed the game progressing
well past where every probe had been stopping, the only honest
conclusion was that the recording's own content extended further than
assumed -- a `.inp` playback tool doesn't invent input, so continued
progress with no live input pending means there was still recorded input
left to consume. Removing the artificial threshold and setting a generous
one purely as a safety cap (not a length estimate) confirmed the real
figure directly from MAME's own end-of-playback message, this time
because the run was actually allowed to reach it.

The lesson: MAME's own "Total playback frames" summary line is not a
property of the `.inp` file -- it's a property of *this particular
process's own execution*, printed at whatever frame the process (your
script's exit condition, a crash, a manual kill) happened to stop at. It
looks like file metadata and reads like file metadata, but treating it as
a fact about the recording rather than a fact about the last run is a
silent, compounding trap: every probe that inherits the same threshold
"independently confirms" the same wrong boundary, and cross-checks against
those probes (determinism tests, behavioral comparisons) can pass cleanly
while both sides are simply agreeing about where they were told to stop.
Establish the real length once, with no exit condition at all (or a cap
orders of magnitude larger than any current guess), before trusting any
"traced across the whole recording" claim -- and treat "the game kept
progressing with no further input given" as a direct, load-bearing signal
that a recording is longer than assumed, not as something to explain away.

## A screenshot sweep only proves what you actually opened

A probe captured a fine-grained screenshot sequence across an event
window -- fine enough, in fact, that the frame which would have shown the
real outcome was sitting right there in the output directory. The
write-up, though, was built from a sparser subset of that same sequence:
a handful of frames opened by hand, spaced widely enough that every one
of them landed just before or just after the actual event. Nothing in
those frames showed the thing that had happened, so the conclusion
written up was "it didn't happen" -- confidently, and with real
screenshots cited as evidence, because the frames genuinely had been
looked at. They just weren't the right ones, and the ones that were right
were never opened.

The tell, when the correction arrived, was blunt: a person who'd actually
watched the session said plainly that the thing the write-up called
absent was visible in the very images being cited. Going back and opening
*every* frame in the already-captured sequence (not capturing new data --
the old data already had the answer) settled it in minutes.

The lesson: capturing enough data and looking at enough of it are two
different steps, and a probe finishing with "N screenshots written" says
nothing about how many of them a person or an agent actually opened
before drawing a conclusion. A sparse, hand-picked viewing of a dense
capture can systematically straddle a brief event -- especially a scored
transition, a text pop-up, a state change that only holds for a few dozen
frames -- and still look like due diligence, because every frame that
*was* opened really did support the conclusion. Before writing up a
negative result ("X never happens," "the state never changes") from a
screenshot sequence, either view the sequence in full, or narrow
mechanically first (binary-search the transition, diff the underlying
state, grep for the on-screen text's tile pattern) so that "not found in
what I checked" and "not found" are actually the same claim.

## Two "different" bytes can be the same array slot, hit two ways

Two zero-page-adjacent addresses looked like independent scratch bytes:
one project's own code read and wrote them directly by name throughout a
whole animation routine, with no `,X`/`,Y` in sight. A live write-tap on
those two exact addresses, though, kept catching hits from a completely
unrelated-looking instruction -- `STA some_other_base,Y` -- which made no
sense until the tap's own frame log was checked against what `Y` actually
held at that moment: it was a fixed, special-cased index used elsewhere
in the same project for one specific entity. `some_other_base + Y`
landed exactly on the two "independent" addresses. They weren't
independent at all -- they were `some_array[that one entity's index]`,
and the animation routine was just hitting that one array slot through a
hardcoded absolute address instead of going through `,Y` like everything
else touching the same array does.

Once seen, the rest followed fast: a second flat-looking byte the same
routine gated on turned out to be `a_third_array[that same index]`, and
an earlier conclusion -- "these bytes are shared scratch, reused by an
unrelated enemy's animation, so any observation about them can't be
trusted" -- had to be retracted. The "unrelated" routine wasn't
unrelated; it was the *same* entity, in a different phase of its own
state machine, and the two addresses were never shared with anything
else at all.

The lesson: when a live write-tap on a fixed address keeps getting hit
by indexed instructions whose base doesn't match, don't write off the
coincidence -- solve for the index (`target = base + index`) and check
what that index means elsewhere in the project. A hardcoded address
inside dedicated, single-entity code is frequently just an indexed array
access with the index already known and inlined, not a genuinely
separate variable -- and mistaking it for one can produce a confident,
plausible-sounding "these are unrelated, shared scratch bytes" reading
that sends the next several hours in the wrong direction.

## A reference-suggested identity is a hypothesis to test, not an answer to adopt

A project held off consulting its private historical reference until its
independent work was substantially done, precisely so a later comparison
would mean something. When the comparison came, the reference offered a
specific, plausible-sounding identity for a byte this project had been
carrying as an open mystery for a while: a named accumulator, described
as tracking progress toward a bonus unrelated to the thing this project
had already ruled the byte out as being. It fit everything already known
about the byte's behavior -- live every frame, BCD, the right general
shape.

It was still wrong. A direct check -- a live write-tap on the byte's
own high-order digits, fired only at its rare boundary-crossings, each
one matched frame-for-frame against a screenshot of the real on-screen
value -- showed the byte tracking something else entirely, at a fixed
numeric scale, matching exactly at both crossings checked. The
reference's own suggested label never made it into any file; the
byte's real identity did, and it happened to be the very thing an
*earlier* pass of this same project had already ruled it out as being --
a conclusion that turned out to be a casualty of an unrelated bug
(a probe silently reading only a fraction of a long recording, see the
`.inp` playback-length pitfall above) rather than anything to do with
the reference at all.

The lesson: a private reference earns exactly the same treatment as any
other unverified source in this methodology -- a source of testable
guesses, not settled facts, however specific or confident-sounding its
naming is. The value of holding off on consulting one until independent
work is done isn't just avoiding bias in the *findings* -- it's having
strong-enough independent tooling already built (write-taps,
frame-exact screenshot correlation) that a reference's suggestion can be
checked in minutes instead of adopted on the strength of a plausible
label. Treat a match as corroboration worth keeping and a mismatch as
data worth recording, in either direction, but never let the reference's
own confidence substitute for this project's own verification step.

## A scan for absolute operands cannot see an indirect load

A 2,592-byte graphics block had no obvious consumer, so the ROM was scanned
byte-by-byte for every `LDA`/`STA` absolute and absolute-indexed opcode whose
operand landed inside it. The scan came back with exactly zero real hits (the
one apparent hit was the scanner decoding a `JMP`'s operand bytes as though
they were an opcode). That was written up as "no 6502 code ever touches this
region, consistent with MARIA DMA-ing it directly."

It was wrong, and the scan could never have found the answer. The block is read
by `LDA (ptr),Y` -- an indirect load through a zero-page pointer, whose
instruction bytes contain a *zero-page* address, not the ROM address being
read. The actual ROM address only ever exists as two immediate bytes somewhere
else entirely (`LDA #$C0` / `STA $B1`), possibly pages away from the load. A
scan keyed on absolute operands is structurally blind to this, and returns a
confident, clean zero.

**A zero result from an absolute-operand scan means "not reached absolutely",
never "not reached".** Before concluding a region is unreferenced, also search
for its *page* as an immediate (`A9 <page>` followed by a zero-page store), and
for its address as a little-endian word sitting in a pointer table. The
give-away that an indirect path exists at all is a routine that reads through
`(zp),Y` with the pointer set by a caller.

## A read tap that reports zero needs a positive control

Read taps were installed on three sub-ranges of a graphics block to answer "do
the recordings actually read this?". All three reported zero reads after boot.
The tempting conclusion -- that the region is dead weight -- was avoided only
because one of the three ranges had already been *proved* to be live: its bytes
had been decoded into a legible font, and strings built from it appear on
screen. A definitely-used range reporting zero reads meant the measurement was
wrong, not the data.

Two things were happening. The graphics are copied ROM-to-RAM once during
init, so all reads fall inside the boot window and none occur later; and the
copy uses an indirect load, so a frame-window filter chosen to exclude the
BIOS checksum sweep excluded the game's own copy along with it.

**Put a known-live range in the same tap run as the range you are asking
about.** If the control reports zero, the run proves nothing about either.
This costs one extra tap and converts a confident false negative into an
obvious instrument failure.

## Patching a ROM desynchronises `.inp` playback from the first frame

Fault injection is a good instinct: zero out a region, replay a recording, diff
the screenshots, see what disappeared. Applied to a cartridge it silently
invalidates itself. The patched ROM boots on a different timeline -- the same
recording that produced one score at frame 2250 in the original produced a
different score at frame 2250 in every patched build. From then on, a
"screenshot at frame N" comparison is comparing two unrelated moments of play,
and the changed-pixel percentages it yields are noise dressed as measurement.

One result did survive: zeroing the maze-wall tiles removed every wall from the
screen while dots, actors, score and text still rendered. That held because it
is *structural* -- an entire class of element absent regardless of when the
frame was taken -- not because the two frames lined up.

**Compare patched and unpatched runs at a matched game state, never a matched
frame number** -- detect the state from RAM and screenshot on that condition.
Failing that, only trust findings of the form "this whole category of thing is
gone", which no amount of timing drift can manufacture.

## Text in a tile-based game is not ASCII, and greps for it come back empty

A day-one check recorded a real anomaly: unlike its sibling cartridges, this
ROM appeared to contain no `GCC(c)1984`-style developer signature. Both halves
of that were wrong. The signature is present in plain ASCII -- the original
check only scanned the *leading* bytes of the block it lives near, and the
string sits close to that block's end. And the game's own visible credit,
`COPYRIGHT ATARI 1984`, is genuinely not ASCII at all: it is stored as
*character codes* for the game's tile set, where `A` is `$5E` and `0` is `$54`,
so no text search of any kind will surface it.

**Search a tile-based ROM for strings twice: once as ASCII, once re-encoded
into its own character set.** The tile encoding is usually recoverable from a
message table -- decode one known on-screen string (a menu item, a fruit name)
and the whole alphabet's offset falls out. And when a signature search comes
back empty, confirm the search actually covered the whole region before
recording the absence as a finding.

## Scanning for a JSR into a gap finds coincidences, not code

Every gap in a disassembly raises the same question -- is there a routine in
there the trace never entered? -- and the same answer suggests itself: scan the
ROM for a `JSR`/`JMP` whose operand lands inside the gap. That scan is close to
pure noise. `$20`, `$4C` and `$6C` are ordinary byte values that occur
constantly inside graphics and tables and as the second or third byte of longer
instructions, and each one is followed by two more bytes that form some address.
Across two 16K titles checked this way, **every single apparent direct branch
into a gap was a coincidence** -- 16 in one, 19 in the other, zero real. Being
taken in by that hit list three times in one project is what produced this
entry.

Two structural facts make the raw scan almost meaningless, and both are easy to
miss because they argue in opposite directions:

* **A direct `JSR`/`JMP` the tracer reached, it also followed.** Its target is
  therefore already code, and cannot still be a gap. So for direct calls the
  scan is looking for something that by construction should not exist -- a real
  hit means the annotations changed under the trace, not that a routine is
  hiding.
* **For `JMP ($xxxx)` the operand is the pointer, not the target.** This is the
  one form the tracer genuinely cannot follow, so it is the one worth scanning
  for -- and comparing its operand against the gap list, as the obvious scan
  does, checks the wrong address entirely. Dereference the pointer first. If the
  pointer is in RAM the target is not knowable statically at all, which is what
  an annotations `ram_vectors` entry is for.

`disasm.py --check-gaps` does all of this: it classifies each candidate as a
real call site or a coincidence by asking whether the opcode byte is an address
the tracer reached as an instruction start, dereferences indirect jumps, and
lists RAM-pointer indirects separately. The point is to make "no missed code" a
checked claim rather than an asserted one.

## Before blaming the random generator, measure it

A run of six consecutive "rare" outcomes looked impossible, so the generator
became the suspect: a short period, a bad seed, a correlation between draws.
Measuring it took one probe and 4,027 samples, and the distribution was uniform
to within a point. The generator was fine.

The real cause was upstream and much duller: a `BMI` early-out taken before the
random draw ever happened, making the outcome unconditional while a bias byte
stayed negative. The sequence was not improbable, because it was not random.

The lesson is about order of operations. A biased-looking result implicates the
whole path from decision to output, and the generator is the most interesting
suspect but almost never the guilty one -- it is also the cheapest thing on that
path to test. **Measure the generator first precisely because it is cheap and
boring**, then spend the saved effort re-reading the branch structure above the
draw. An unconditional early-out and a broken PRNG produce identical-looking
output, and only one of them is likely.

## An array ends where the code stops indexing, not where the data looks like it stops

An object table was read as running past its real end, and the slots "beyond"
it held plausible-looking values that supported a wrong theory about spare
object types. The correction was wrong too, in the same way: it was also about
bytes outside the array. Two rounds of reasoning, both about data that was
never part of the structure.

Nothing in the bytes marks the boundary. What marks it is the code: the
comparison that bounds the index (`CPX #n / BCS done`), the loop's own start
value, the width of the stride when several parallel tables share an index.
**Get the bound from the indexing code before interpreting any entry**, and be
most careful when the first byte past the end looks meaningful -- adjacent
variables are as likely to hold plausible values as the array is, and a theory
built on them will fit the data comfortably while being about the wrong bytes
entirely.

## A register bit documented by its name, not by its effect

`CTRL` bit 4 is "character width", and this toolkit recorded it as `1 = 1 byte`
-- which is what the name suggests and the wrong way round. Setting the bit
makes character mode cost *more*, because it fetches two bytes per character
rather than one.

Nothing about reading a disassembly would have caught it. Both games in this
series clear the bit, so both behave identically whichever way you read it, and
a wrong bit description survives any amount of static analysis that never asks
the bit to do anything. It fell out of a timing measurement taken for an
unrelated reason: the configuration that should have been cheaper measured
dearer, by exactly one byte-fetch per character.

The general form: **a bit whose documented meaning your corpus never exercises
is unverified, no matter how many ROMs you have read.** When a measurement
disagrees with a bit description, suspect the description -- it was probably
written from a name.

## A display list entry's horizontal position is in pixels, not bytes

An object's width is given in **bytes** and its horizontal position in
**pixels**, in the same four-byte entry, and nothing about the encoding hints
at the change of unit. Reading both as bytes gives a sprite that will not move
past a quarter of the screen; reading both as pixels gives one whose width is
four times too small.

This surfaced while writing a scaffold, not while reading one: the clamp on a
moving sprite was computed as "160 pixels / 4 per byte = 40", the sprite
started at 40, and it moved left perfectly and refused to move right at all.
The asymmetry is the tell -- one direction bounded by a wrong constant and the
other by zero, which happens to be correct in either unit.

Measured on hardware: in 160A mode, position 0 to 159 spans the visible width,
one unit per pixel, whatever the object's width. So the rightmost position that
still shows a whole object is `160 - width_in_bytes * 4`.

## MAME's tracer collapses loops, and a trace diff against it lies

Comparing a simulator against MAME instruction by instruction is the strongest
check there is -- the first differing PC is the bug, with no inference in
between. But `trace file,0` does not record every instruction. It detects
repeated loops and replaces them with a summary line, so a spin loop of two
hundred iterations appears as two iterations and a note.

Diffed naively, the first "divergence" is therefore wherever the traced program
first loops, and it looks utterly convincing: a plausible instruction, a
plausible register, a plausible story. In this project it pointed at a
memory-clearing loop and a wrong X register, and the real answer was four
hundred thousand instructions further on.

    trace out.txt,0,noloop

`noloop` is the whole fix. The file gets large -- 90MB for fourteen seconds --
and the emulator drops to about 60% speed, which is a small price for a diff
that means what it says.

Two related limits of the same instrument, both of which produce silent false
negatives:

* **Read taps do not see instruction fetches from cartridge ROM.** Counting
  interrupts by tapping the handler's entry address reports zero, which reads
  exactly like "no interrupts are being raised". Write taps on RAM and on
  MARIA registers are unaffected.
* **A tap can stop firing when the driver remaps.** One installed over
  `$0000-$03FF` here went quiet at frame 81, and a trace that simply stops
  looks like a machine that has gone idle. It happened twice, at different
  frames, and the second time it silently truncated a comparison of two
  register-write sequences -- the missing half read as "the game never does
  this" rather than "the instrument stopped".

And one that is not the instrument's fault at all: **the 7800 BIOS runs for
about 133 frames before the cartridge gets control**, building its own
display list and toggling MARIA's registers every frame. Anything sampled by
frame number in the first two seconds is measuring the logo screen. A display
list read at frame 16 and attributed to the game was, in this project, the
central evidence for a wrong conclusion.

## A negative result is only as good as the window you measured it over

Twice in one investigation, and expensively both times.

A simulator was returning a constant zero for POKEY's `RANDOM` register. The
discrepancy was noticed early -- the emulator read `$4000` as `E4 E4 E4` where
the simulator read `00 00 00` -- and dismissed after checking whether the game
ever read POKEY. It did not, in the 300 frames that were checked. The music
engine that reads `$400A` does not start until frame 400. That register was
the entire bug, and it cost a day.

Then the same game's sibling looked broken because its simulation scored 7.7%
against a captured log. Every run of it was 14 seconds. The game leaves its
attract loop at frame 1170 -- about 19.5 seconds -- so no run had ever reached
the music being compared against.

The shape is always the same: a measurement returns "this never happens", the
window was too short for it to have happened yet, and the negative is filed as
a fact. It then does its damage silently, because a ruled-out cause is not
revisited.

**Record the window with the result.** "Never reads POKEY" is not a finding;
"never reads POKEY in the first 300 frames" is, and it visibly invites the
question of what happens in frame 301. When a negative rules out a whole line
of inquiry, deliberately re-measure it over a window several times longer
before believing it.

## The capture probe presses fire, so a committed log is not a passive recording

`probes/audio.lua` taps fire on a loop when `A7800_DRIVE=1`, and `capture.py`
turns that on **by default** -- the flag it offers is `--no-drive`, not
`--drive`. Most cartridges sit silent on a title screen until someone presses
something, so this is the right default for getting music out of one.

It does mean a committed `.log` is a recording of a game that has been
*started*, and comparing anything passive against it is comparing two
different sessions. Ballblazer's driven capture holds 1,544 states where a
no-input run of the same length holds 742; Midnight Mutants' holds 818 against
456, and the two diverge by frame 162.

Nothing about the file says which it is. When a log is the reference for
anything, record how it was captured -- driven or not, how long, what input --
next to it, and when comparing, reproduce those conditions rather than
assuming the default was passive.

## "That can't be what the game does" is a premise, not a measurement

A simulator performed exactly one bank switch across four hundred frames of a
128K cartridge and then sat in one bank, spinning in a tight loop. That was
written up as a bank-switching failure, on reasoning that sounds unanswerable:
a cartridge with eight banks which switches once is obviously broken.

The emulator performs exactly one bank switch too, from the same instruction to
the same address, and spends its time in the same loop -- which turned out to
be the game's own delay routine, a busy-wait of twelve thousand iterations that
the emulator executes 1,745,101 times in the same window.

Nothing about the simulator's behaviour was wrong. What was wrong was comparing
it against an expectation instead of against the hardware, when the hardware
was sitting right there and the comparison costs one trace.

The tell is the shape of the argument. "A game with eight banks would not use
one" is a claim about what programmers do, not an observation about what this
program does, and it feels strongest exactly when it has been checked least.
Before reporting that a simulation does something a real machine would not,
make the real machine do it and look.

## A MAME port lookup that silently does nothing

`probes/audio.lua` drives the fire button when `A7800_DRIVE=1`, which is how
`capture.py` gets music out of a cartridge sitting on a title screen -- it is
that tool's default. The lookup was `MACHINE.ioport.ports[":buttons"]`.

MAME reports the tag as `":BUTTONS"`. The lookup returns nil, the field lookup
on nil is guarded, `fire` stays nil, and the probe then captures happily while
pressing nothing at all. There is no error, and the log it produces looks
exactly like a driven one -- it just contains a game that never started. Five
7800 titles captured this way yielded a single row each, which reads as "this
cartridge is silent" rather than "the button was never pressed".

Case is the specific bug; the general one is that **a lookup which can return
nil and a feature which can do nothing are the same bug when nothing checks**.
The probe now searches the port tags case-insensitively and prints a warning
if it was asked to drive and found no button, because a capture that silently
changed meaning is worse than one that fails.

Worth suspecting whenever an emulator is upgraded under a script: tags,
casing and field names are not a stable interface.

## A budget figure that answers a different question than the one asked

`dmabudget.py --afford` reported "how many more objects fit" and computed
objects present in EVERY zone -- a band spanning the whole screen. Read as
"how many more sprites", which is the obvious reading and the one it invites,
it understates the headroom by the number of zones: twenty-five, on the screen
it was first used against.

It produced a real wrong answer. A game's remaining budget was reported as
"room for 13 more 8-byte objects", which sounds tight next to a display array
of sixteen slots, and the honest figure for a sprite occupying one 16-line
zone was 210. The conclusion happened to survive -- DMA was not the constraint
either way -- but only because the error pointed the wrong direction from the
conclusion.

It now prints both columns and says which is which. The general form is worth
keeping in mind for any capacity number: **a rate has a denominator, and the
reader will supply their own if the tool does not.** "Objects" meant
object-zone-instances; the reader means sprites.

## A formula makes a bad detector on its own, because formulas alias

Pole Position II decays the car's speed by an eighth on contact with a puddle
(`speed - speed>>3`). Since that routine had exactly two xrefs and both sat
inside the collision handler, a drop of precisely `speed >> 3` looked like a
sound identification of a puddle hit -- derived from the listing, not guessed,
and it correctly found five hits confirmed by screenshots and by timing.

It also produced two false positives. The game has a *scripted* stop -- a flat
17 per step, every six frames, to zero -- used at the end of qualifying and at
time-out. Wherever `speed >> 3` equals 17, that is speed 136 to 143, the two
are indistinguishable from a single transition, and both false positives
landed there. A third mechanic, a flat `-16` penalty, aliases the same way at
speed 128 to 135.

Sampling the whole sequence rather than the one transition settled it in a
minute: `153 136 119 102 85 68 51 34 17 0` has a constant difference, and a
proportional decay from 153 would have given 134, not 136.

**A single before-and-after pair cannot distinguish a proportional rule from a
constant one -- the sequence can.** When a detector is built from an
arithmetic signature, work out which other values in the state space produce
the same delta, state that band, and check it before trusting any hit inside
it. This costs one extra probe run and is the difference between five findings
and seven claims.

## "No missed code" can mean "the tracer had no way in"

`--check-gaps` scans for JSR/JMP operands landing in a gap and dismisses any
whose opcode byte is not a known instruction start. That rule is sound, and on
six finished disassemblies every apparent call into a gap really was a byte
coincidence.

It is also circular for the case that matters most. Nothing inside a *wholly
unreached* region is an instruction start, so a reference from inside one gap
to another gets filed as a coincidence no matter how real it is. Pole Position
II hid its entire car physics this way -- three routines, 929 bytes, 27.5% of
the ROM covered and the tool reporting "70 coincidences, no real call site".

What gave it away was arithmetic, not the tool: six routines wrote the speed
byte and **not one of them accelerated the car**. A variable that demonstrably
goes up, with no writer that increases it, means the writer has not been found.
Then a raw byte scan for `85 CE` found eight more, all inside gaps.

The mechanism was a nine-byte gap holding three JSRs, sitting directly after a
`JMP` that stepped over it. Falling into the bytes after a JMP is impossible,
so a tracer that arrives only via that JMP can never enter, however live the
code is. `--check-gaps` now detects this shape directly and reports it
separately; it fires once on Pole Position II and not at all on the six
finished projects.

**Two habits follow.** Model the variable, not just the code: list what must
exist (something increments this, something clears that) and check the listing
actually contains each one. And treat a gap adjacent to an unconditional jump
as suspect by default -- that is precisely where a tracer is blind.
