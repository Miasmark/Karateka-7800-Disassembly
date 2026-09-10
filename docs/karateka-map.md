# Karateka (7800): what is where

A map of the cartridge, assembled from `tools/forth.py`, the entity and
free-RAM probes, and everything the patches had to find out to work. Companion
to [karateka.md](karateka.md), which is about the interpreter, and
[fixing-karateka.md](fixing-karateka.md), which is about changing it.

## The main loop is a scheduler, not a cast

`w_A59C` gives each of nine slots one `EXECUTE` per round and waits a frame
between them. It is natural to read that as nine game entities animating in
turn. It is not. **Two of the nine are the fighters; the other seven are the
game's subsystems**, taking turns:

| # | slot | usually runs | what it is |
|---|---|---|---|
| 1 | `$18AD` | `w_68EE` | reads `$18DC`, the section counter, and branches |
| 2 | `$1878` | `w_7898` | **the player** -- reads the controls, picks a command |
| 3 | `$18AF` | `w_9518` | works on `$18DC`, `$AA` and the goon's X |
| 4 | `$1897` | `w_881A` | **the opponent** -- the same dispatcher shape as the player's |
| 5 | `$18B1` | `w_9866` | the AI: computes `goonX - playerX` and compares it |
| 6 | `$18C5` | `p_66C8` | collision resolution: reads and clears `$18C0` |
| 7 | `$18C7` | `p_673A` | updates a table at `$18E3-$18FD`, almost certainly the display list |
| 8 | `$18DF` | `w_9908` | the opponent's attack bridge -- idle (`w_9908` is a single `EXIT`) except the round its dispatcher (slot 4) starts one |
| 9 | `$18C9` | `p_689A` | reads `$18C2`, toggles `$1919` |

Slots 2 and 4 are the fighters, and the probe says so independently of the
code: 958 and 950 pointer changes over a fight, against 168-230 for everything
else. Two things running the same kind of state machine look like that and
nothing else does.

**Slot 8 sits idle 87.5% of the time** and still costs a frame every round
regardless. It is not an independent subsystem: `w_A59C` resets `$18DF` to
`w_9908` (one cell, `EXIT`) exactly once, at entry -- the loop's own closing
branch targets $A5AA, past that reset -- and what lands there afterward comes
from `w_881A`, the opponent's dispatcher in slot 4, *earlier in the same
round*, when it starts an attack. Two self-reinstalling chains keep it
occupied one animation step at a time until the attack ends. `fix_slot8_gate`
(see `docs/fixing-karateka.md`) skips the wait after this slot specifically
when it is idle, which the check above is precise enough to do safely.

Three of the nine hold *primitives* rather than colon words, which is why the
decompiler prints them as nonsense until you disassemble them as code.

## RAM

Everything below was established either by tracing the code or by a probe, and
the ones that were guessed say so.

### The fighters

| | first fighter (player) | second fighter (goon) |
|---|---|---|
| entity slot | `$1878` | `$1897` |
| base position | `$186D` (x), `$186E` (y) | `$188C`, `$188D` |
| body points | `$187F`, `$1881`, `$1883` | `$189E`, `$18A0`, `$18A2` |
| striking limbs | `$187D`, `$1885` | `$189C`, `$18A4` |
| hit window | `$18C3` | `$18C4` |
| post-hit spark | `$18CB`, `$18CD` | `$18D3`, `$18D5` |

Body points are built fresh each frame from the base plus an animation offset
(`LDA $186D / CLC / ADC ($00,X) / STA $187D`), so the base is the fighter and
the rest is the pose.

### State

| address | what |
|---|---|
| `$187A` | strike height: 1 high, 2 middle, 3 low |
| `$187B` | splits one of the movement pollers two ways |
| `$187C` | stance: 0 walking, non-zero fighting. Read twice (`$6933`, `$782F`) and written from three places: `STY $187C` at `$A503`, which zeroes it at every stage start, and two Forth cells naming `p_5280` -- `$7226` behind a literal 1 and `$726A` behind a literal 0. Those two cells are the stance control; nothing else in the game selects a stance |
| `$18A7` | the player's current command number, stored by the decoder at `$7891` (`STY $18A7`) immediately before `w_7898` dispatches on it |
| `$18AA` | encounter number, 1-6 (0 before the first) |
| `$18AB` | a toggle that makes the command decoder decode on alternate calls |
| `$18C0`, `$18C1` | the collision mask: `$F0` for one fighter, `$0F` for the other |
| `$18C2` | the **opponent's** hit points. `DEC $18C2` at `$6908` on a landed blow; reaching zero is his death |
| `$18BF` | the **player's** hit points. `$67BF` draws the health bar from it (`>> 2`), `$68B2` flashes a warning under 3 |
| `$18E1`, `$18E2` | the ceilings those two regenerate up to (`$6962` and `$6988` clamp against them) |
| `$18DC` | a section counter, incremented at the `$A0` transition. Also gates combat: the collision resolver reads the masks at `$6702` and throws the hit away unless this is zero |
| `$18B3`-`$18BC` | five pairs: the hall's travel odometer, one segment per pair. See "The odometer" |
| `$18BD` | which of those five segments the world is currently in, 0-4 |
| `$18BE` | pixels still owed on the current move, seeded by `$9375` and ticked at `$92D4`/`$9362` |
| `$18E3`-`$18FD` | pairs on a stride of 4, rewritten every round by `p_673A` |
| `$1919`, `$191A` | `$191A` counts a strike's frames; `$1919` is toggled by `p_689A` |
| `$AA` | movement velocity, added to the base position at `$905C` |
| `$A2`, `$A3` | decoded stick: horizontal and vertical, each -1/0/+1 |
| `$28` | frame flag; bit 7 is what the wait blocks spin on |
| `$E8`, `$EB`/`$EC` | the interpreter's thread pointer and W |
| `$00`-`$CF` | the Forth data stack, indexed by X and based at `$CF` |

### Free

`$D7`, `$DC` and `$F9` are named by no instruction in the ROM and were written
zero times across two probe runs covering different halves of the game. `$DC`
carries the strike-rhythm phase, `$D7` the loop gate's counter, and `$F9` the
knockback shove's display-list-sync delta -- all three claimed by fixes now,
none left free. `$EF` looked free and is not: 51 writes, all at boot.

## Words worth knowing

| word | what |
|---|---|
| `w_A59C` | the main loop |
| `w_7898` | the player's command dispatcher: 7 commands to 7 behaviour words |
| `w_881A` | the opponent's, the same shape |
| `p_781E` | the command decoder -- stick and buttons to a command number |
| `p_5A07` | the stick decoder: SWCHA to `$A2`/`$A3` |
| `p_7042`, `p_706C` | strike height from the right and left stick hemispheres |
| `w_A0C2` | seeds a stage's fight from two pushed constants: the cell nearest the call DUPs into `$18E1`/`$18C2` (the opponent's health and its ceiling), the one before it into `$18BF`/`$18E2` (the player's). Per stage the pairs run 13/13, 17/13, 21/9, 21/9, 21/9, 25/5 -- the opponent climbing, the player falling |
| `$7A30`, `$6A9C` | spark placers, not damage. `$7A30` sets the **goon's** spark (`$18D3`/`$18D5`) and is what runs when the *player lands* a blow; `$6A9C` sets the player's (`$18CB`/`$18CD`). Each writes a graphic and a coordinate and returns -- which is why the knockback fixes had to supply movement themselves |
| `w_A52A` | the encounter dispatcher: a CASE on `$18AA` |
| `w_A4D2` | the stage-clear check, installed into the *goon's* slot when it dies |
| `$79EA`, `$7A0A` | the overlap test: `abs(dx) < R` on each axis |
| `p_513C` | `EXECUTE` -- 12 sites, of which 9 are the entity round |
| `p_596A`, `p_503C` | the frame-flag test and branch a wait block is made of |

## Graphics

`probes/dumpgfx.lua` presses Select, waits for
`$18AA` (the encounter number) to go non-zero so the capture cannot land on the
title screen by accident, and dumps RAM plus every MARIA register at that
frame. Two runs:

```
title screen  DPPH:DPPL $181F   CHARBASE $F0   CTRL $50
in a fight    DPPH:DPPL $1810   CHARBASE $F0   CTRL $40
```

Both DLLs sit in RAM, which is what was suspected going in, and the *reason*
is now on record rather than assumed: **DPPH/DPPL are write-only**, so MARIA
has to be told where to draw every frame, and telling it means a CPU store --
and a store can only ever target RAM. Nothing about the graphics *content*
requires this. The pixel data behind those display lists is read straight out
of ROM; only the list of *where to look* is rebuilt each frame.

That distinction matters because the two captures use the graphics pointer
differently at the very same ROM address. The title screen is **indirect
(character) mode**: the display list points at a list of one-byte character
codes, and each character's bitmap comes from `((CHARBASE + line) << 8) |
code` -- `CHARBASE=$F0`, so `$F000` upward. Rendering that range as a font
(`gfx.py --base 0xF000 --ascending`) shows a legible capital-letter set,
readable at exactly the even codes (`$40`-`$B2`) the title text's character
list actually used.

The in-fight capture never sets the indirect bit. Its display-list entries are
plain **direct** entries -- `gfx` is a real bitmap address, no character table
involved -- and most of the row is one entry, `gfx=$C000 pal=0 width=1`,
repeated across dozens of x-positions: a single small tile, wallpapered. The
`$xxF8`/`pal 4`/`width 3` entries recur down the screen with their address
falling by exactly `2048` between appearances (`$D0F8`, `$C8F8`, `$C0F8` --
`8 lines * 256` apart), which is what one object *taller than a zone* looks
like once it is spread across several zones: a striped pillar or railing,
rendered vertically down the wall.

Two objects do not fit that background pattern at all. At `x=94-99`, zones
15-17 hold a `pal 2 width 4` object and zones 18-20 hold `pal 2 width 7` at the
same address-per-zone step -- one 24-line figure whose *silhouette widens*
partway down. At `x=38`, the same six zones hold a narrower `pal 1` figure the
same way. Rendered with `tools/spritedump.py --stack` (new -- see below), the
wide one is unmistakably a standing fighter: head, a raised fist, torso, and
legs braced wide apart, in pale mint with peach skin. The narrow one at `x=38`
is a second standing figure in white, arm at its side.

Which is which is not a guess -- the same RAM dump holds `$186D` (the player's
X) and `$188C` (the goon's X), and they read **38** and **94**. So the white
figure at `x=38` is the *player*, mid-walk (`$187C`, the stance byte, reads 0
in this capture -- walking, not fighting, which matches an arm-at-the-side
pose better than a fighting one), and the green wide-stance figure at `x=94`
with the raised fist is the *goon*, encounter 1's opponent.

```
python tools/spritedump.py <rom> \
    --stack 0xD846:4:24 --stack 0xC046:7:24 \
    --palette-regs dumpgfx_regs.txt --palette-index 2 -o fighter.png
```

**`$65C8`: how a fighter's zones stay in sync as it walks.** Direct-mode
zone objects are static list entries -- nothing re-reads `$186D`/`$188C` to
draw them. Instead, every walk tick calls `$65C8`, which adds the tick's
`$AA` delta into a small table of zone X-fields and advances 62 (`$3E`)
bytes to the next one, once per zone. It is the one mechanism behind
essentially every moving thing on screen; see `docs/fixing-karateka.md`,
"Knockback left the drawn fighter behind its logical one" and "The real
foreground pillars, and what actually drives them", for how it was found
and the two rounds of wrong conclusions before the picture below was
confirmed with direct trace data.

**The display-list object catalog, as far as it is mapped.** Everything
found by walking a real display list during a fight, one RAM dump at a
time, cross-checked against `$186D`/`$188C` and against which addresses
`$65C8` actually walks:

| object | gfx pointer | width | palette | tracks | table |
|---|---|---|---|---|---|
| wallpaper tile | `$C000` | 1 | P0 | nothing -- 268 identical copies | -- |
| four accent tiles | `$D0FB`/`$C8FB`/`$C0FB` | 1 | P4 | `$188C`, 1:1, every hit and every walk tick | `WORLD_TABLE` |
| foreground pillars, far column | `$D0F8`/`$C8F8`/`$C0F8` | 3 | P4 | `$188C`, 1:1 -- 12 zone-repeats | `PILLAR_FAR` |
| foreground pillars, near column | same | 3 | P4 | `$188C`, **2:1** (real parallax) -- 20 zone-repeats | `PILLAR_NEAR` |
| goon's own body | `$E84D` family | 7 | P2, mint | `$188C` exactly | `GOON_OWN_TABLE` |
| player's own body | `$D0A3` family | 8 | P1, peach | `$186D`, during open walking only | `PLAYER_TABLE` |
| static object, two slices | `$F005`/`$F01D` | 24 -- the widest found | P4 | nothing -- set once at encounter setup, never again | -- |
| unplaced, right-edge | `$F800`/`$F000` | 5 | P5 | not traced -- x=236, near the coordinate ceiling; a person playing called this "half a cliff tile" | -- |
| unplaced | `$D0E5` family | 12 | P3 | not traced -- x=52, fixed in every dump so far | -- |

The two pillar columns were the discovery that mattered most: the same
graphic (`$D0F8`) both forms the visible foreground pillars *and* is woven
untracked into the wallpaper rows near the top of the display list as
decoration, which is exactly the kind of thing that produces a confident
wrong answer if only one instance is ever checked. The real pillar
addresses were found by grouping every instance of that graphic pointer by
its X-field and diffing across widely separated frames -- see
`docs/fixing-karateka.md` for the method and the two ways the first attempt
got it wrong (chasing the wrong address, then trusting a write-tap over a
window that happened not to exercise the write at all).

**A published catalog page:** every entry above was rendered at native
resolution with the real palette loaded at capture time and laid out for
visual identification. That page embeds decoded cartridge artwork, so it
is a local artefact and is not published here. Regenerate with
`probes/regcapture.lua` (a `dumpgfx.lua` without the Select-mashing boot
phase, which fights a played-back recording's own input) alongside
`probes/ramdump.lua` at the same frame, then `tools/dlwalk.py --follow` and
`tools/spritedump.py` per object.

**The six-copy movement state machine.** Every one of the tracked objects
above is driven through the same small state machine, duplicated six times
at nearly-identical addresses ($924A, $93D8, $9466, $955C, and two more
near $96F3/$9778, found originally by scanning for the `INC $18B4`-shaped
instruction sequence). Each copy cycles a phase pointer (`$18BD`) through
paired elapsed/remaining counters (`$18B3`/`$18B4`, `$18B5`/`$18B6`, and so
on) and, once armed, reaches into a *second* layer of near-identical
satellite routines -- `$9110`, `$913C`, `$9216`, `$91B2`, `$91E2`, `$916E`,
`$91A2` all recur as call targets across the six copies, alongside the
already-mapped `$901E`/`$9062` (the player's own apply chain) and `$90A0`
(its mirror for the goon: `LDA $188C / CLC / ADC $AA / STA $188C`,
preceded by a run of `JSR $65C8` calls, one per table the current tick is
supposed to move).

What is confirmed: in one dispatch of `$90A0`, a single tick moves the
goon's own body, `GOON_OWN_TABLE`, `WORLD_TABLE`, and both pillar columns
together -- traced directly, not inferred, across a frame where the player
held input continuously and the goon was also independently mid-walk.

**The trigger is found, and it is the player's slot, not goon AI.** The
correlation above was genuinely ambiguous -- both a person's stated model
(player drives it) and "the goon happened to also be walking" fit the same
trace equally well, which is exactly why two earlier rounds of this
investigation guessed wrong from correlation alone. What broke the tie:
`$AA` gets armed by a small self-reinstalling chain (`$7278`/`$7334`/...,
one link setting `$AA` to a small magnitude, calling a footstep word, then
reinstalling the *next* link into `$1878` -- the **player's own** dispatch
slot) every ~13 frames. Checked directly against the one tick already
traced: at that exact frame, the goon's own slot (`$1897`, tracked as
`slot4`) held its ordinary AI dispatcher, not the parallel chain that
*also* exists installed into `$1897` (`$83C6`/`$83E2`/`$83FA` -- the goon
has its own copy of the identical mechanism, confirmed by direct
disassembly, which is why the two are so easy to conflate). So for the one
event with hard per-tick evidence, the goon's own AI was not even active;
the cascade came from the player's slot alone.

Climbing further up: the chain's very first link is installed by `w_7898`,
the player's own command dispatcher (already named in this document under
"Words worth knowing"), which tests the current command number 1-7 in
sequence and installs a different behaviour word into `$1878` for each.
**Commands 3 and 5 are the directional pair** -- `$7234` (command 3) and
`$727A` (command 5) are mirror images of the same chain, one setting
`$AA=-2` (in its second link, `$7244`), the other `$AA=+2` in its first.

The input state behind them is found too, and it answers the question
directly: `$08`/`$09`, read by the fighting-stance decoder (`$784A`,
mapped much earlier in this project) and assumed there to be generic
"raw stick bits," are `INPT0`/`INPT1` -- TIA's two fire-button lines,
bit 7 clear while held. Capturing them alongside every write to the
player's slot during a window where command 5's chain was firing
continuously showed `$08=$00` (button 1 held) and `$09=$80` (button 2
released), both constant for the whole span, alongside a held direction
-- button 1 plus a direction, together, every time the chain fires so
far. Not yet captured: a negative control (direction held, button
released, or the reverse) to confirm the button is actually required
rather than merely always present when this was checked.

Also unresolved, same open thread: past frame ~5000 in one long recording,
the player's pin comes and goes -- two isolated four-unit walks that
exactly cancel (frames 5366-5379 and 6419-6432) inside an otherwise pinned
stretch, then a sustained real walk starting at frame 7277. None of
`$18DC`, stance, or the current slot-4 word differ at any of those three
boundaries, so whatever decides "the player's own position is live again"
is not one of the flags already instrumented for this project.

**New tool: `tools/spritedump.py`.** `gfx.py` renders a fixed 256-entry
character grid, which is right for a font and cannot express one arbitrarily
wide, arbitrarily tall, arbitrarily coloured object -- and a Karateka fighter is
exactly that: one address, a width in bytes, a height that spans several
zones, and a palette read from the running machine rather than guessed at.
`--stack` renders each zone-sized segment at its own width and pastes them top
to bottom, which is what a figure whose stance widens partway down needs.

Reproduce from scratch:

```
mame a7800 -cart karateka.a78 -autoboot_script probes/dumpgfx.lua \
     -video none -sound none -nothrottle
python tools/dlwalk.py --raw dumpgfx_ram.bin --at 0x1800 --dll 0x1810 --follow
# group entries that share an x and step by (lines*256) between zones --
# that grouping is one object -- then:
python tools/spritedump.py karateka.a78 --stack <base:width:lines> ... \
     --palette-regs dumpgfx_regs.txt --palette-index <n> -o out.png
```

## What moves the world

Everything that scrolls goes through one primitive, `$65C8`, and its
addressing is now solved rather than sampled. It takes three literals
pushed by its caller -- call them `aux`, `Y` and `N` in push order -- and
walks:

```
address = $2227 + Y*62 + aux*4          then N entries, stride 62
```

62 (`$3E`) is the display-list stride between zones, so `N` is how many
zone rows the object spans and `aux`/`Y` place it. `$2227` is not a
caller's argument; it is built inside `$65C8` from `#$24`, `#$22` and a
`#$03`, which is why hunting for a base pointer on the stack found
nothing. Every address in this file that used to be a hand-collected list
falls out of that formula.

The callers are a small set of table walkers, each a run of push-three,
`JSR $65C8`. All twenty call sites in the ROM, decoded:

| routine | calls | what it owns |
|---|---|---|
| `$8E66` | 1 | `$2607` ×6 -- the far-edge set, level 1's cliff tiles |
| `$8E88` | 2 | `$2379` ×12 and `$229D` ×20 -- both foreground pillar columns |
| `$8ECC` | 4 | `$2423`/`$2427`/`$242B`/`$242F` ×3 -- near world, zone rows 8-10 |
| `$8F3E` | 8 | rows 5-7 (`$2369`/`$236D`/`$2371`/`$2375` ×3) **and then rows 8-10 again** |
| `$901E` | 2 | `$2537` ×3, `$25F5` ×3 -- the player's own body |
| `$9062` | 2 | `$252F` ×3, `$25ED` ×3 -- the goon's own body, then `$188C` and its derived cells inline |

**`$8F3E` is the trap in that table.** It has no `RTS` after its rows 5-7
pass; at `$8FAC` it falls straight through and walks rows 8-10 as well,
running to a single `RTS` at `$901A`. Disassembled cold it looks like two
routines, because `$8FAC` opens with the same `LDY #$00` prologue every
walker has, and `$8ECC` -- which really does end at `$8F3A` -- sits right
there as a matching peer. Reading it that way makes `$8F3E` "the top
half", and calling it beside `$8ECC` walks the bottom twice per step and
the top once. That shears a pillar into halves travelling at 2:1, which is
exactly how it was eventually found, from play rather than from reading.
`$8F3E` alone is the pair.

The near world is two rows because on level 1 the upper one is empty and
never written all session, so it does not announce itself. Level 3 fills
it: dumped there, both families hold the same four X coordinates -- 76,
140, 204, 12 -- across all six zone rows, four columns 64 apart running
rows 5 through 10.

### The satellite layer, and two things it does that are easy to invert

Between the phase machine and the walkers sit about a dozen near-identical
satellites (`$9110`, `$913C`, `$9160`, `$9178`, `$9192`, `$91B2`, `$91EC`,
`$9206`, `$9220`, `$923A` ...). Each is a fighter applier plus one near-world
walker plus optionally a far one:

- the `$8ECC` family: called from `$911D`, `$9133`, `$9149`, `$9163`, `$917B`, `$9195`
- the `$8F3E` family: called from `$91BF`, `$91D7`, `$91EF`, `$9209`, `$9223`, `$923D`

A satellite calls one or the other, never both, and the phase machine
alternates so the halves converge over a full walk cycle. A one-shot step
has no cycle to spread across and has to cover both at once.

**They negate `$AA` first.** `$913C` opens `LDA #0 / SEC / SBC $AA` before
calling anything. So a walk chain setting `-2` -- the player *intends* to
go left -- hands the walkers `+2`, and the world travels right, which is
what makes a pinned player read as walking. The walkers want the effect,
not the intent; passing them the intent sends the world backwards.

**And nothing ever passes a walker a magnitude.** `$936E`/`$9375` stashes
the raw value in `$18BE` as a countdown, forces `$AA` to exactly ±1, and
loops one pixel at a time until `$18BE` reaches zero. Two pixels is two
unit steps, not one two-pixel step.

### Where the far actors are parked

Placement writes come from `$59A0` then `$58CE` -- a generic field-copy
primitive, so the constant is per-scene data and not code. Observed
values: pillars at `$A0` on levels 1 and 3 and `$F3` on level 2, the near
column at `$AF`, the cliffs at `$FC`. All at or past `$A0`, which is
MARIA's visible width and the same constant the game's own edge check
compares `$186D` against at `$A488`. So `X < $A0` is a usable "this column
has scrolled into view" test, and a group is a vertical column whose every
zone carries the same X, so one head address tests the whole column.

### The odometer: how far into the hall the world has travelled

There is a step counter, and it is the thing the satellites hang off.
**`$18BD` is a phase index 0-4** and `$18B3`-`$18BC` are five pairs, one
segment of the hall each. Eight dispatchers drive them -- four machines,
each a (left, right) pair:

| machine | left | right | near-world walker |
|---|---|---|---|
| 1 | `$924A` | `$92E0` | `$8ECC` |
| 2 | `$93D8` | `$9466` | `$8ECC` |
| 3 | `$955C` | `$95EA` | `$8F3E` |
| 4 | `$96E2` | `$9770` | `$8F3E` |

Machines 1-2 walk the near world's lower rows and 3-4 both halves; within
each pair the two differ only in which far mover phase 1 and phase 3 get.
Which machine is live is a runtime fact -- their entry primitives
(`$936E`, `$94F4` and two more) are installed into slots, which is why
`$936E` has no findable static caller.

All eight switch the same five ways, and every one of them agrees about
what a phase means:

| phase | drained going right | drained going left | satellite | what actually moves |
|---|---|---|---|---|
| 0 | `$18B4` | `$18B3` | `$91A2` | the player's body, nothing else |
| 1 | `$18B6` | `$18B5` | varies | a fighter, the near world, one far mover |
| 2 | `$18B8` | `$18B7` | varies | a fighter and the near world |
| 3 | `$18BA` | `$18B9` | varies | a fighter, the near world, the other far mover |
| 4 | `$18BC` | `$18BB` | `$91A2` | the player's body, nothing else |

Phases 0 and 4 are the ends of the hall, and `$91A2` is the whole rule
there: two instructions, `JSR $901E / RTS`. The background is pinned and
the player crosses the screen instead. It is also the only satellite that
does not negate `$AA` first, so `$901E` gets the intent rather than the
effect. Phase 2 is the long middle, where the far actors are parked
off-screen and no far mover runs; phases 1 and 3 are where each one
scrolls in.

One unit of travel does exactly this: read the phase's counter for the
direction you are going; if it is zero, step `$18BD` (`INC` going right,
`DEC` going left) and dispatch again; otherwise decrement it, increment
its partner, and call that phase's satellite. Then tick `$18BE` -- the
per-call pixel countdown `$9375` seeded -- and go round again until it
reaches zero.

So the pair per phase is one segment of the hall with a read head in it,
and the whole set is a **reversible odometer**: walking right pours each
segment from the left byte into the right one, walking left pours it back.
The seeds, read out of the six room words:

| room | width | start | segments (0-4) |
|---|---|---|---|
| 1 | 370 | 120 | 40, 20, 180, 40, 90 |
| 2 | 365 | 115 | 35, 20, 180, 40, 90 |
| 3 | 365 | 115 | 35, 20, 180, 40, 90 |
| 4 | 395 | 145 | 35, 50, 180, 40, 90 |
| 5 | 395 | 145 | 35, 50, 180, 40, 90 |
| 6 | 395 | 145 | 35, 50, 180, 40, 90 |

Every room opens at phase 2 with 60 of that segment's 180 already behind
it. The seventh hall has no seed block at all -- consistent with it being
the one screen where the loop is shut down and nothing travels.

This is also where the halls end. In `$924A`, phase 0 with `$18B3` at zero
means the world is as far left as it goes; if `$18DC` is also zero it
`JMP $A490` rather than returning. Going right, phase 4 exhausted is a
plain `RTS`.

**Knockback has to spend this or it desyncs.** Fixes 39 and 43 call the
walkers directly, underneath the dispatchers, so being shoved moves the
scenery without spending the hall's budget or moving the phase index --
and the disagreement is permanent for that hall. Fix 44 spends it: one
unit of budget per unit shoved, phase stepped when a segment empties, and
the same two rules above obeyed, so a shove in segment 0 or 4 slides the
player rather than the world and a shove that runs the hall out of
leftward budget simply stops. Verified by poking the odometer to each edge
mid-fight and watching the next hit: parked in segment 0 with budget, a
hit moved `$2537` (the player's body) three and left `$2423` (the world)
alone; with two units left in segment 2 and segments 1 and 0 empty, a
three-unit hit moved the world two and stopped.

Watch the left end particularly. Going left, phase 0 with `$18B3` at zero
is the hall's boundary and the game's own answer is
`if $18DC == 0: JMP $A490` -- a transition, and a `JMP`, so it never
returns to its caller. That is fine from the walk chain and would be
catastrophic from inside a hit handler, which is why fix 44 stops there
rather than imitating it.

## Death, and why walking stance is lethal

`$18BF` is the player's health and `$18C2` the opponent's; `w_A0C2` seeds
both per stage (see "Words worth knowing"). What makes a hit lethal is a
stance test sitting between them, at `$6932`:

```
6932  LDA $187C / BNE $693C     ; fighting: decrement health normally
6937  LDA #$01 / STA $18BF      ; walking: health := 1
693C  DEC $18BF                 ; -> 0
693F  BNE $694E
6941  LDA #$77 / STA $1879      ; death: install $7750
6946  INC $18DC                 ;   into the player's slot ($1878)
6949  LDA #$50 / STA $1878
```

So "a hit taken in walking stance kills you" is not a special case
elsewhere -- it is *force the health to 1, then decrement*. `$6937` is the
entry, and jumping there kills the player exactly as the bird does.
Confirmed against a recorded run that ends in a death: `$18BF` reaches 0
at `$693C`.

An earlier pass through this file called the block at `$6912` the
goon-death handler. It is the player-hit handler; `$18C2` is where the
goon's death lives, at `$6908`.

The death word itself is `$7750`, which is also what `$A490` installs at
the `$A0` transition -- a shared "this scene is over" walk-out rather than
anything death-specific.

**Combat is switched off during the ending.** Slot 2 (the player) and slot
6 (collision resolution) both hold `w_9908`, the single-`EXIT` no-op, from
the frame the final hall is staged. Anything that sets a collision mask
there is writing to nobody: `$18C0` sits at `$F0` and is never read.
`$18DC` is non-zero by then too, which would have discarded the hit at
`$6702` even if a resolver were running. Two independent reasons a hit
staged at the ending does nothing, both of which have to be gone around
rather than through -- see fix 41 in `docs/fixing-karateka.md`.

## How the bird is survived

`$8CA8` is "the player takes a hit". It pushes the player's body point
`$187F` and calls `$7A30` -- the routine the knockback fixes wrap -- then
nudges the player's health `$18BF` and sets the high nibble of both
collision masks `$18C0`/`$18C1`, which is what actually feeds damage and
death handling.

`$7A30` itself only places a spark: `LDA $00,X / SBC #$0E / STA $18D5`,
`LDA #$8C / STA $18D3`, and return. No damage, no knockback -- hence the
knockback fixes supplying movement of their own. One loose end worth
flagging rather than smoothing over: `$18D3`/`$18D5` are listed above as
the *second* fighter's spark, yet `$8CA8` uses `$7A30` for a hit on the
player. Either the spark's ownership in that table is mislabelled or the
routine is shared by both directions; it has not been settled, and the
princess-kick work did not need it settled.

It is guarded by three stage/command pairs, checked in a row at `$8C6C`,
`$8C80` and `$8C94`. Each is the same shape -- compare the stage, compare
the command, and on a match jump to `$8CD6`, which is a bare `JMP $401E`
and does nothing at all. So these are not event triggers; they are
*exemptions* from being hit:

| at | stage (`$18AA`) | command (`$18A7`) | |
|---|---|---|---|
| `$8C6C` | 4 | 7 -- the kick | hit skipped |
| `$8C80` | 5 | 5 -- walk right | hit skipped |
| `$8C94` | 6 | 5 -- walk right | hit skipped |
| `$8CA8` | anything else | | **hit lands** |

Command 7 is the right-hemisphere strike -- `w_7898` installs `$7602` for
it and then runs `p_7042`, "strike height from the right stick
hemisphere". Command 5 is walk-right, the positive half of the
directional pair. So this is the bird, across three stages: it arrives on
stage 4 and is beaten by kicking it, and on stages 5 and 6 it is escaped
by walking right instead. That is precisely how a person playing
described those stages, before any of this was read.

Stage 7 is the princess and has no entry here, which fits -- she is not
this hazard. What she does instead (famously, kicking you if you approach
in fighting stance) is unmapped.

Worth noting how this was read, because the code alone does not say
"bird" anywhere. The cluster was found first and misread as a scripted
event trigger, and `$8CD6` being empty made it hard to interpret. Being
told what stage 4 *is* turned unreadable constants into an obvious
mechanic, and identifying `$18A7` then took one trace: it is written from
exactly one place (`$7891`, `STY $18A7`) with values in the command
range. The first write-up of this also recorded only two of the three
pairs, because the middle one disassembled misaligned and was dropped
rather than retried -- the `$18AA` reference scan had said three all
along.

## The stages are a ring, and there is no stage 7

`w_A52A` is a seven-way dispatch on `$18AA`, built exactly like
`w_7898`'s command dispatch -- fetch, then a chain of compare-and-skip.
The seven stage words sit at a regular 82-byte (`$52`) stride, and each
one sets `$18AA` to the *next* stage as part of its own setup. `$18AA` is
never incremented anywhere; it is only ever stored, and these seven
stores (at `+$22` into each word) are where it moves:

| word | dispatched for | sets `$18AA` to |
|---|---|---|
| `w_A1D0` | 0 | 1 |
| `w_A222` | 1 | 2 |
| `w_A274` | 2 | 3 |
| `w_A2C6` | 3 | 4 |
| `w_A318` | 4 | 5 |
| `w_A36A` | 5 | 6 |
| `w_A3BC` | 6 | **0** |

So leaving stage 6 wraps to zero and the dispatcher sends that straight
back to `w_A1D0`, which sets it to 1 again. The stages are a ring. The
dispatch's default case, for any value that matches nothing, is also
`w_A1D0`, so a stray value lands in the same place.

That settles several loose ends at once. Nothing in the ROM compares
`$18AA` against 7 because it never holds 7. This file's own note that it
runs "1-6 (0 before the first)" is right, and 0 is equally "after the
last". And the princess is confirmed not to live in this system at all --
searching for a stage-7 case was searching for something that cannot
exist. Whatever presents her is triggered outside the stage ring.

### The cutscenes: seven screens, one per stage end

Each stage word does more than set the next stage -- it plays a screen.
There are seven of them, a family sharing a prologue (`w_98F6`) and built
identically: set MARIA palette registers, then point at a layout
description. They sit at a regular ~260-byte stride, and their content
pointers sit at an exactly regular `$85` (133-byte) stride:

| screen word | layout data |
|---|---|
| `w_990E` | `$5C5E` |
| `w_9A14` | `$5CE3` |
| `w_9B18` | `$5D68` |
| `w_9C1E` | `$5DED` |
| `w_9D24` | `$5E72` |
| `w_9E28` | `$5EF7` |
| `w_9F2C` | `$5F7C` |

The layout data is not text. It is four-byte records -- an incrementing
first byte, then a flag, then X and Y -- with X stepping by `$40` across
four columns and Y alternating between two rows. A composed grid of
graphics, so reading a cutscene means rendering it, not dumping strings.

Because a stage word is dispatched on the *current* stage and sets the
next one, it runs at that stage's **end**. Which screen plays where:

| stage word | runs at the end of | plays |
|---|---|---|
| `w_A1D0` | stage 0, i.e. game start | `w_990E` |
| `w_A222` | stage 1 | `w_9C1E` |
| `w_A274` | stage 2 | `w_9A14` |
| `w_A2C6` | stage 3 | `w_9D24` |
| `w_A318` | stage 4 | `w_9B18` |
| `w_A36A` | stage 5 | `w_9E28` |
| `w_A3BC` | stage 6 | `w_9F2C` |

The screens are not stored in play order, which is worth knowing before
assuming the sixth word in memory is the sixth thing you see.

**Rendering one without playing to it.** The layout format does not have
to be decoded -- the game will draw any of them on demand. `w_A1D0` runs
at stage 0, and the cell at `$A1F6` is the screen word it plays. Patch
that one cell to any of the seven, replay a recording far enough to get
past the title (the stage dispatch does not run on a passive boot; it
needs input, and lands around frame 355), and screenshot:

```
# in the ROM, $A1F6: 0E 99  ->  the screen word you want, little-endian
mame a7800 -cart screenN.a78 -autoboot_script probes/snapat.lua \
     -playback <any>.inp -input_directory . -sound none -nothrottle
# with A7800_SNAPAT_FRAME=380
```

Renders of all seven are ROM-derived artwork and are not published with this repository; regenerate them from your own dump with `probes/dumpgfx.lua` and `tools/gfx.py`. They are backdrops, and they
walk inward across the set:

| screens | where |
|---|---|
| 1, 4 | outdoors, under a snow-capped mountain |
| 2, 5 | indoors, narrow pillars |
| 3, 6 | indoors, wider pillars |
| 7 | indoors, windowless |

(A first pass through these called 2 and 5 outdoors because the mountain
is still visible between their pillars. It is a view *through* them; the
set is four steps deeper in, not three.)

The figures a screen carries are not part of the screen. They come from
the fighter slots, so a backdrop rendered out of context shows whoever
happens to be loaded -- which is why forcing screen 7 at stage 0 draws
the ordinary opponent and not who belongs there.

A person playing anchored this: the end of stage 1 is the first cutscene,
which is `w_A222` playing `w_9C1E`. Screen `w_990E` runs at stage 0,
before any fight, so it is the title rather than a cutscene -- which is
why the first *story* screen is the one after stage 1 and not the one
first in memory.

`w_A3EC`'s block, gated on the stage being 1 or 5, reaches the last two
screens (`w_9E28` and `w_9F2C`). Whether the princess is drawn by one of
these seven or by something outside them is still unsettled, but if she
is anywhere, `w_9F2C` -- the screen at the end of stage 6, where the ring
closes -- is the place to render first.

### The princess: found, and she is a costume on the opponent slot

The cutscene after stages 1 and 5 is the block inside `w_A3EC`, and it
is two-part: it plays `w_9E28` (screen 6), works through a delay, then
plays `w_9F2C` (screen 7) and immediately calls `w_A112`. That second
half is where she appears.

`w_A112` does not summon an entity. It copies a nine-byte record from
`$5C3E` into `$188E` -- inside the *opponent's* own field block -- and
then places both figures by hand:

```
LIT $5C3E / LIT $188E / LIT $0009 / p_8E14   ; 9 bytes -> the goon's fields
LIT $0060 / LIT $188D / p_5280               ; goon Y = $60
LIT $000A / LIT $188C / p_5280               ; goon X = $0A
LIT $0060 / LIT $186E / ...                  ; player Y = $60, X = $26
```

So the princess is the opponent slot wearing a different costume. That
is why she is absent from the stage ring and from the screen set, and
why searching for a stage-7 case or a seventh entity was never going to
find her -- there is no princess entity to find.

The records live beside the screen layouts and share their shape: 4-byte
entries (graphic, flag, X, Y) terminated by `$FF`, two entries making a
figure two zones tall.

```
$5C1B:  8a 00 26 60 | 8b 01 26 78 | ff      the player,   X=$26
$5C3E:  88 0a 0a 60 | 89 0b 0a 78 | ff      the princess, X=$0A
```

Rendered from the real cutscene rather than a forced backdrop -- replay
any recording to the end of stage 1, around frame 4700-4730, and
screenshot -- she is a figure in a full-length pale robe to the ankles,
hair swept up, standing at the far left of the windowless hall. The
crop is a local render, not published here (see above). The scene draws in greyscale at
that moment, which is the cutscene's own palette and not a capture
artefact.

A person who knew the game said she is shown in the second half of the
cutscene after stage 1; that is exactly `w_A3EC`'s screen-7 half, and
the pointer that had been sitting unexamined in `w_A112` all along.

**Her behaviour word, and what it is not.** `w_A112` finishes by
installing `w_8A5A` into `$1897`, so she runs in the opponent slot like
any fighter. The chain is `$8A5A -> $8A82 -> $8AA2 -> $8ABE -> $8ADA`,
links about `$20` apart, each doing the same three things: call a draw
word (`w_89AA`, `w_89B4`, `w_89BE` ...), set `$AA` to 2, and install the
next link. A walk-in animation, two pixels a step.

Every link then calls `w_8A3E`, which is:

```
p_59FC      ; read the controls
p_8A1C      ; count active inputs -- see below
p_4D6C +4   ; 0BRANCH: if the count is zero, skip past...
w_A52A      ; ...the stage dispatcher
```

`p_8A1C` decrements Y once for each of: `$08` (INPT0, button 1 held),
`$09` (INPT1, button 2 held), `$A2` non-zero and `$A3` non-zero (stick
off centre). So it is "is the player touching anything", and any input
runs `w_A52A`.

It is tempting to read that as the famous business of the princess
kicking a player who approaches still in fighting stance, and this
document briefly did. That is not supported. The test does not look at
stance (`$187C`) at all -- it fires on *any* input including a bare stick
nudge -- and what it triggers is a re-dispatch, not a strike or an
animation. For a cutscene, "any key skips" is the ordinary reading and
fits everything observed. Whatever produces the kick is somewhere else,
and `w_A112` is reached only from `w_A3EC`'s stage-1-or-5 block, so this
appearance is not the ending one.

## The ending, and the one piece of it that really is missing

This section first said the ending was never installed. That was wrong,
and the mistake is instructive: it was concluded from the stage ring
wrapping 6 back to 0 with no stage-7 case, and from a play report of the
two figures "on each other" with the screen blinking. Both observations
were accurate. The conclusion drawn from them was not.

Beating stage 6 runs `w_A3BC`, which plays screen 7 and then calls
`w_A158` -- the ending's own placement word, sitting in exactly the slot
where the stage-1/5 cutscene calls `w_A112`. It:

- copies a nine-byte costume from `$5C47` into `$186F`, the *player's*
  fields, and another from `$5C50` into `$188E`, the opponent's
- puts her at `$188C` = `$55` and him at `$186D` = `$50`
- installs `w_8BA6` into `$1897`

Five pixels apart is not a bug. Rendered and zoomed
(a local render, not published here) the two sprites resolve into an
**embrace** -- his gi, her swept-up hair, facing each other. The overlap
is the pose. At 320x224 on a real screen it reads as one blob, which is
how it got reported, and how it got believed.

`w_8BA6` is the celebration: a `$1000`-iteration loop that cycles four of
the five background-colour sources (`$9E`, `$9F`, `$A0`, `$A1`) and calls
`w_5AC4` -- which writes `$19`/`$1A`, AUDV0 and AUDV1 -- every pass, then
falls into `w_A6C4`. Colour flashing and sound over a held embrace. Crude
and very short, but present and working. Traced over a real run: 4098
writes to `$9E` across the 660-frame hold.

Then the ring wraps and the game starts again. No credits, no text.

**What is genuinely absent is the kick.** Nothing anywhere checks the
player's stance when the ending is staged; `w_A158` installs `w_8BA6`
unconditionally. `$187C` is read from exactly two places in the ROM and
neither is here, so a player who walks in still in fighting stance gets
the same embrace as one who does not. That is the piece this port left
out -- not the ending, one rule inside it.

### The seventh hall shuts the engine down, and the death still draws

`w_9F2C` -- the seventh hall's setup, the last of the seven screen words
-- ends by parking the no-op `w_9908` in six of the nine main-loop slots:
`$18AD`, `$1878`, `$18AF`, `$18C5`, `$18C9` and `$18C7`, then installing
`w_9866` into `$18B1`. `$18C7` is `p_673A`, the display-list updater, so
after the transition the only scheduled words are `w_9866` and whatever
`w_A158` puts in `$1897`.

That table is genuinely alarming and it is not the whole story. A death
staged on this screen animates in full anyway: the death chain writes its
own display-list entries through `p_69E2`/`p_6BEC` and never needs
`p_673A` to be scheduled. Sampled slots say what is *dispatched*, not
what can *draw* -- worth remembering before concluding a screen is
frozen.

The chain itself is five words, each installing the next into `$1878`,
twelve frames apart:

```
w_7750  w_6EE4                              ; collapse, frame 1
w_7760  w_7010, $AA := -2                   ; frame 2, and shove left
w_777A  w_7020, $AA := -1                   ; frame 3
w_7794  w_7030, $18AA := 0, no-op $18AD     ; frame 4, and the ring resets
w_77BA  w_47F4 / w_596A / ...               ; back to attract
```

So death from the ending screen costs about a second and then the game is
gone. It is what `$6941` installs, and the same chain any walking-stance
hit runs.

### `p_5280`'s stack shape

`$5282  LDA $02,X / STA ($00,X) / JMP $4010`. The address is the top
stack cell (`$00,X`/`$01,X`) and the value one cell below it, at `$02,X`.
Anything wedged in front of a store to read what is being written wants
`$02,X`; reading the target's current contents gives the value being
*replaced*, which is a different question and, for stance, the opposite
answer. Fix 41 got this wrong once -- see `docs/fixing-karateka.md`.

### Correction: `p_5250` fetches, it does not increment

`p_5250` is `LDA ($00,X) / STA $00,X / STY $01,X` -- it replaces an
address on the stack with the byte at that address. A first pass through
this material read it as an increment, which made `w_A3EC` look like "the
word that advances the stage" and sent the search for the ending in the
wrong direction. It reads the stage; nothing advances it but the seven
stores above. Any earlier note built on `p_5250` meaning increment --
`w_7670`'s handling of `$191A`, for instance -- wants rechecking.

### The opponent gets a second move set from stage 4

Found while looking for the princess. `$87E0` reads the stage and picks a
base: stages 1-3 give 0, stages 4 and up give 8. That base is added to a
rotating index in `$AE` -- advanced by 7 and masked to `$1F`, so it cycles
32 slots without repeating soon -- and used to build a pointer into a
table at `$872C`.

So the opponent draws its moves from one half of a table for the first
three stages and the other half from stage 4 onward, shuffled rather than
sequenced. Stage 4 is where the bird starts, so the difficulty step and
the bird arrive together.

### Correction: the NMI handler is not sound

An earlier pass through this dismissed the NMI handler at `$58E5` as an
audio round-robin, on the strength of five phases keyed on `$A8` each
loading a different zero-page pointer into `$20`. `$20` is `BACKGRND`.
The handler is cycling background colours down the screen -- a gradient --
and there is no sound during the combat stride at all, which is what
caught it. The IRQ vector (`$59E4`) is a bare `RTI`; nothing uses it.

## Sound

`audiotrace.py` was pointed at this cartridge for the first time and traced
0.7% of it, reporting two routines that "write constants only" -- because its
tracer is a linear 6502 one and this game is threaded, so it never reached
anything real. Going at it through the Forth image instead found exactly one
*named* primitive touching audio, `p_5B48`, and it is silence: `LDY #$00 / STY
$19 / STY $1A / DEY / STY $95 / STY $98`, called by `w_A6A6` and `w_A6C4`,
both restart paths. Widening to all 203 primitive-shaped addresses adds
nothing, so the real driver is not a primitive either -- it is reached only by
raw `JSR`/`JMP`, which means finding it meant searching the whole ROM for
branches into the region around the one clue (`STA $19,X`, an AUDV write
indexed by channel) and reading what pointed there.

The real entry points are `$5B5A` (advance one channel, given its state block
in `$92`/`$93` and a TIA channel offset in `$94`) and `$5B9A` (given a channel
number 0 or 1 in `A`, look up that channel's state-block pointer in a table at
`$64AE` and call `$5B5A`).

**It is serviced from NMI, not from the entity loop**, and that is worth
stating plainly because of what it implies for every timing fix built in this
project. The NMI handler at `$58E5` runs a small state machine on `$A8` --
state 1 sets `BACKGRND` and calls the two-channel advance; state 2 sets
`BACKGRND` again and steps the state forward -- and NMI fires every frame
regardless of what the main loop is doing, waits and all. **The wait-skipping
and loop-gate fixes could never have desynced the music, because the music was
never on the loop's clock to begin with.** That was found by playing (nothing
dragged, nothing drifted) before it was found by reading; this is the reading
that says why the playing came back clean.

The format read off the driver: **4 bytes per note**, `[gate-byte, AUDC,
AUDF, AUDV]`, one pointer per channel advancing by 4 on every call, `$00` in
the gate byte position ending that channel's tune. A capture backs this up
directly --

```
python tools/capture.py karateka.a78 -o karateka-title.trk --seconds 40
```

-- 46 changed rows over 40 seconds, and reading the `.trk` back gives real
notes forming a real two-bar phrase that loops exactly once in the window:
channel 1 carries the melody (F#5, an unnamed pitch at `$16`, G5, C5, G5, C6)
with a stepped volume decay on the sustained notes (`D`,`B`,`9`... down to
silence -- an envelope, not a flat tone); channel 2 harmonises a third below
(C#3, C3, F3, E3, G2). That is the title theme, captured from the actual
running machine rather than inferred, and `$16`'s failure to resolve to a note
name is itself a small piece of evidence for the "might be the typing sound"
guess from early in this project -- a percussive/noise `AUDC` value does not
sit on the note table at all.

### The full inventory, and there is nothing dormant in it

The driver is three routines and they divide cleanly:

```
$5B5A  advance one channel by one note
$5B9A  start tune A: $9B/$9C := $64AE[A*2], then fall into the setup
$5BA7  the same setup, entered with $9B/$9C already pointing at a descriptor
$5BDE  the per-frame tick: decrement each channel's gate, advance on zero
```

A **descriptor** is one byte of TIA channel number followed by the note
list, so the start routine sets the pointer to descriptor+1. Per-channel
state is three zero-page bytes -- `$95` and `$98`, each `[gate, ptr lo,
ptr hi]` -- and a gate of `$FF` means that channel is finished. The gate
byte is a duration in NMI ticks, decremented at `$5BDE` and reloaded from
the note when it reaches zero, which is the piece the earlier read of the
format did not have.

`$64AE` is the table of descriptor pointers and it has exactly **twelve**
entries; the thirteenth slot already holds unrelated data. Every one of
the twelve is started during an ordinary playthrough -- traced by tapping
the pointer high bytes and discarding the driver's own +4 step, so what is
counted is a tune being handed to a channel and not a tune advancing:

| # | descriptor | ch | notes | length | plays | what |
|---|---|---|---|---|---|---|
| 0, 1 | `$620A`, `$6278` | 0, 1 | 27 each | 4.9 s | 2 | the title theme |
| 3, 4 | `$633A`, `$636C` | 0, 1 | 9, 7 | 1.7 s | 8 | **a fight begins** |
| 6 | `$6360` | 0 | 1 | 1 tick | 84 | a noise blip, `AUDC` 8, `AUDF` `$12` |
| 5 | `$6366` | 1 | 1 | 1 tick | 45 | a noise blip, `AUDC` 8, `AUDF` `$1E` |
| 10, 11 | `$6432`, `$646C` | 0, 1 | 14, 16 | 4.0 s | 3 | **the cutscene, first half: the warlord** |
| 7, 8 | `$638A`, `$63E0` | 0, 1 | 21, 20 | 10.1 s | 3 | **the cutscene, second half: the princess** |
| 2, 9 | `$62E6`, `$6308` | 0, 1 | 8, 12 | 1.6 s | 10 | **a stage ends** |

Those identities are from a trace that screenshots the frame each cue
starts on and records the game state with it, so they are what was on
screen and not what the length suggested:

- `$633A`/`$636C` fires with `$18DC` = 1, stance = 1 and `w_864A` in the
  goon's slot -- an opponent stepping up. Six plays over six stages.
- `$62E6`/`$6308` fires with `$18DC` = 0 and `w_A3EC`, the stage-exit
  word, in the player's slot. Six stage ends, plus the two title-to-game
  transitions.
- The other two are **one cutscene, not two**. `$6432`/`$646C` starts over
  the shot of the warlord and `$638A`/`$63E0` starts 230 frames later over
  the shot of the player and the princess -- the first cue handing off to
  the second as it ends. Three plays each, which is the cutscene after
  stage 1 and after stage 5 (and stage 1 again on the replay), exactly the
  two places `w_A112` was already known to run.

The title theme's two plays are the two visits to the title screen in that
recording, which is the control: a tune whose count matches something
countable.

**The ending has no music of its own.** No tune starts between the stage-6
transition at frame 8362 and the title at 9014. What it does instead is
`w_8BA6` writing `AUDV0`/`AUDV1` directly through `w_5AC4`, outside the
driver entirely -- which is why nothing appears in a trace of tune starts,
and is of a piece with the rest of that ending being thinner than the game
around it.

`$620A` through `$64AD` is contiguous and entirely spoken for -- every
byte belongs to one of the twelve, with no gap between the end of one and
the start of the next. **So there is no unused music and no dormant sound
effect in this ROM.** That is worth stating as a result rather than a
silence: the princess's kick was exactly a case of parts being present and
never called, and the same question asked of the audio comes back the
other way.

The two one-tick blips are the only sounds combat makes: 84 plays of
`$6360` and 45 of `$6366` across a full playthrough, both `AUDC` 8, which
is the TIA's noise setting and is why an earlier attempt to read `$16` as
a pitch found it was not on the note table.

So the whole score is: a theme at the title, a sting when a fight starts,
a sting when a stage ends, two cues over the one cutscene, and two
percussive blips. Nothing plays *during* a fight -- between the opening
sting and the closing one the channels carry only those blips, which is
what "no sound during the combat stride" is describing.

**What this makes cheap.** Adding a sound does not need the table, which
is full: `$5BA7` takes a descriptor in `$9B`/`$9C`, so a new sound is a
descriptor placed in the free pool at `$A6D0` and four instructions
wherever it should fire. Footsteps are the obvious candidate -- the walk
chain has a footstep step and it makes no sound -- and the knockback
fixes already run code at the exact moment of a hit, which is the other
one.

## The cartridge signature

An NTSC 7800 will not start a cartridge in 7800 mode unless it verifies. It
does not refuse -- it starts up in **2600 mode instead**, which on real
hardware looks like a black or garbage screen and not like an error
message. PAL consoles have no crypto check at all, and neither MAME nor
the a7800 fork verifies anything. So a patched cartridge works in every
place a patch project actually tests and fails on the one machine the game
was sold for.

Karateka's `$FFF8` is `$FF` and its `$FFF9` is `$47`: the high nibble says
the hash starts at page `$40`, so **the hashed range is the entire 48K**,
and the reset vector `$4000` is inside it as the console requires. Every
byte any fix in this project writes is inside that range. This is not
about avoiding the signature block -- it is about recomputing the
signature afterwards.

Three rules, and only the first is interesting:

| where | rule |
|---|---|
| `$FF80`-`$FFF7` | 120 bytes: the signature over the cartridge hash |
| `$FFF8` | high nibble `$F`, low bit set. `$FF` means "all regions" |
| `$FFF9` | high nibble = first hashed page; low nibble 3 or 7 (3 skips the rainbow) |
| `$FFFC` | the reset vector, which must point inside the hashed range |

The reset-vector rule is there to stop a cartridge carrying somebody
else's signed block verbatim and jumping out of it.

**The scheme is Rabin, not RSA: the public exponent is 2.** The console
squares the signature mod `n` and compares against its own hash. Square
roots do not always exist, which is exactly why byte 4 of the hash is a
don't-care the signer is free to step until the value is a quadratic
residue mod both primes, and why only the low three bits of byte 0 are
compared. `n` is 956 bits and the two primes are both 3 mod 4, so a root
is one `pow()` per prime and a CRT lift.

The hash itself is two passes over every page from the start page to
`$FE` -- forward through one 256-byte permutation, backward through
another offset 8 bytes into the same table -- around a 2048-bit double
shift, with the `$FF00` page seeding the accumulator and its own signature
zone zeroed so the hash cannot depend on what it is authenticating.

`tools/sign7800.py` verifies and signs. It is a port of Bruce Tomlin's
`sign7800.c` (2004), itself a hand-decompilation of Atari's ST program;
the hash and tables are reproduced exactly, while the C's hand-written
bignum library collapses into Python integers. Checked against
`karateka-original.bin` and a stock `Commando` dump, both of which verify.

Every path that emits a cartridge now signs: `karateka.py --build` and
`--doses` sign the image before writing the `.bin`, the `.a78` and the
`.bps`, so applying a `.bps` yields a signed ROM too. The `.abp` case is
the one that cannot be precomputed -- the signature covers the whole
image, so every combination of options has a different one -- and
`patchset.py apply` computes it at apply time and says so.

## What is still dark

**Slots 1, 3 and 9.** Characterised by what they read and not by what they are
for. `$18DC` runs through all three and through the `$A0` transition, so they
are probably the machinery of moving between sections of a stage.

**Five of the seven behaviour chains.** Commands 1-3 and 6-7 were followed far
enough to find the height dispatch and the strike words; nobody has read what
the animation actually does between those points.

**203 addresses look like primitives and 121 are named by the threaded code.**
The rest are either unreached or reached through a path the decompiler does not
follow, and no coverage pass has been done to say which.

**Whether button 1 is actually required for commands 3/5, or merely always
observed alongside them so far.** See "The six-copy movement state
machine" above -- the trigger chain for `$90A0`'s cascade, its head
(`w_7898`, commands 3 and 5, the directional pair), and the input state
present every time it fires (button 1 held plus a direction) are all
confirmed against a live capture now. What has not been captured is a
negative control -- a window with the direction held and the button
released, or the reverse -- to rule out the button being incidental.

**What un-pins the player.** Related to the above, and possibly the same
root cause: in one long recording the player's own position is fixed for
roughly five thousand frames, briefly moves in a way that exactly cancels
itself out twice, then starts moving for real. `$18DC`, stance, and the
current slot-4 word are all unchanged at every one of those transitions --
the actual trigger has not been found.

**Two of the nine display-list objects catalogued this session are still
unplaced.** The width-5 object at the coordinate ceiling (a person playing
called it "half a cliff tile") and the fixed-position width-12 object have
neither been rendered against a confirmed identity nor traced for movement.

**How `$936E` and `$94F3` are reached.** These are the two "check `$AA`
and dispatch" primitives that drive the phase machine, and they demonstrably
execute constantly -- but nothing in the ROM appears to call them. Searched
and came up empty: `JSR` and `JMP` byte patterns for both, plain 2-byte
literal occurrences anywhere (the only hit is `$936C`, which is `$936E`'s
own code field), install patterns into all nine polled slots in both the
Forth `LIT`/`p_526C` idiom and raw `LDA #imm`/`STA`, both interrupt vectors
(NMI is the background gradient, IRQ is a bare `RTI`), and cross-referencing
every PC that touched the hardware stack in the frame one of them first
fires. A live execution breakpoint would settle it and the tooling would not
cooperate: MAME's `-debugscript` never fired the breakpoint, and the Lua
`cpu.debug` object is absent without `-debug` and segfaults the emulator
with it. Whatever reaches them is computed rather than written down, and
finding it needs a different instrument than byte-pattern search.

**Stage 7, the princess -- and it is not built like the bird.** SETTLED;
kept because the three negative scans below are still the reason her
trigger is where it is. She keys off nothing in the stage variable at
all: she is the opponent slot wearing a costume, staged by `w_A158` from
outside the stage dispatch. The obvious guess was that she keys off stance
the way the bird keys off command, and that was checked and is wrong:

- `$187C` (stance) is read from exactly two places in the ROM, `$6932`
  (inside the goon-death handler) and `$782E` (inside the command
  decoder). Neither has anything to do with her. Both still hold; what
  fix 41 needed was the *write* sites, which are three -- `STY $187C` at
  `$A503` and the two Forth cells `$7226` and `$726A`.
- `$18A7` (command) is read from exactly three places, and all three are
  the bird's exemptions.
- `$18AA` (stage) is never compared against 7 anywhere. The highest
  comparison in the ROM is 6, which agrees with this file's own note that
  it runs 1-6.

There is no "stage 7" in the stage variable's terms, and following that
up settled it harder than expected: the stages are a ring that wraps 6
back to 0 (see "The stages are a ring" above), so `$18AA` never reaches
7 and no amount of searching for a stage-7 case would have found her.
She is triggered from outside the stage dispatch entirely.

**Where the princess actually kicks you: SETTLED, and this entry was
wrong twice.** It first said the ending was never installed, which was
wrong -- `w_A3BC` runs `w_9F2C` then `w_A158`, which stages the embrace
and installs the celebration; see "The ending" above. What is genuinely
absent is the stance check, and fix 41 puts it back. The reading that
produced the error is preserved in `docs/fixing-karateka.md` because the
mistake was a useful one: "played to the end and the stages ring round"
is a true observation, and "so the ending was never written" does not
follow from it.

**Found** -- see "The princess" above. She is the opponent slot loaded
with a nine-byte costume record from `$5C3E`, placed by `w_A112` during
the screen-7 half of the cutscene after stages 1 and 5. There is no
princess entity, which is why neither the stage ring nor the screen set
contained her.

**Level 2's upper near-world row.** The rows 5-7 / rows 8-10 pairing was
established on level 3 and the mechanics on level 1. Level 2 was never
dumped for it, so whether its background has the same vertical extent is
assumed rather than known.

**The far-edge set (`$2607` ×6) is identified by behaviour, not by sight.**
It sits past the edge, moves as its own group, and a person playing
recognised the symptom as the cliff tiles -- but nobody has rendered those
entries to confirm what they draw.

### A testing note that costs an hour if it is forgotten

Recorded `.inp` files desync against a build whose knockback differs,
because the shove changes fight outcomes and therefore everything after
them. A level-3 recording made against one knockback build replayed against
another died and restarted on level 1 and never reached level 3, while
still producing perfectly plausible-looking trace output. Any measurement
past the first divergence is measuring a different session. Check `$18AA`'s
transitions against the recording's own before trusting a window.
