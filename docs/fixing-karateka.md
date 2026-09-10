# Fixing the 7800 Karateka: options ranked by effort

Not porting it -- fixing the cartridge that exists. Everything below is
measured against the real ROM unless it says otherwise.

## What the main loop is

`tools/forth.py --at A59C` gives the whole thing. Stripped of noise:

```
w_A59C:                       ( the main loop )
    ...setup, one frame wait...
    LIT $18AD  @  EXECUTE     ( entity 1: run whatever word it points at )
    wait one frame
    LIT $1878  @  EXECUTE     ( entity 2 )
    wait one frame
    LIT $18AF  @  EXECUTE
    ... $1897, $18B1, $18C5, $18C7, $18DF ...
    LIT $18C9  @  EXECUTE     ( entity 9 -- gets half a wait )
    ...cleanup on $18DD...
    BRANCH -$F6               ( round again )
```

Nine entity slots. Each holds a pointer to its current behaviour word; `@` is
`p_523E`, `EXECUTE` is `p_513C`, which drops the pointer into W and jumps to
the NEXT stub at `$00EA`.

A "wait" is fourteen bytes and does exactly one thing:

```
    p_596A   ( BIT $28 / BPL -- push bit 7 of the frame flag )
    p_503C   ( branch while true  )   spin until the flag clears
    p_596A
    p_4D6C   ( branch while false )   spin until it sets again
```

No side effects, no display work. One frame edge, waited for. That is why
skipping a block is worth exactly one frame, which is what the recorded
sessions showed: four skipped gave a 9-frame loop, and all eight would give 5.

**The interpreter is not the problem.** Between the stock ROM and the
four-waits-skipped build, dispatches per frame went 403 to 434 while the loop
went 13 frames to 9. Per loop that is 5,239 down to 3,906 -- so removing four
frames removed 1,333 dispatches, about 330 per frame skipped, out of roughly
400 in a frame. **Around 80% of everything the interpreter executes is the spin
loop.** The machine is not computing for thirteen frames, it is waiting for
twelve of them.

**Update rate and game speed are the same knob.** An entity's animation advances
once per update, and an update happens once per loop, so skipping waits makes
the game more responsive *and* faster in exactly equal measure. That is not a
side effect to be engineered away; it is the same fact seen twice.

## What the hit test is

Ten primitives compare a limb against a body point and OR their result into
`$18C0`/`$18C1` -- the software stand-in for the collision registers a 7800
does not have. Five belong to each fighter (one ORs `$F0`, the other `$0F`, so
the byte is a nibble per fighter). All of them reach the same shape:

```
  79EA   A = |limb.x - body.x|
         CMP $18C4          a hit if it is under the threshold
  7A0A   B = |limb.y - body.y|
         CMP $18C4          and under it on the other axis too
```

A hit is `|dx| < R and |dy| < R`: a square window of half-width R. And the
points being compared are built per frame from a base position plus an offset
the animation supplies:

```
  6BEE   LDA $186D          fighter A's base X
         CLC / ADC ($00,X)  plus this frame's offset, from a table
         STA $187D          = one body point
```

So the whole thing really is distance, attack and animation frame: **reach
varies by attack through the offsets, not through R.** R is one byte.

There are two of those bytes, one per fighter -- `$18C3`, compared at
`$6A68`/`$6A88`, and `$18C4`, compared at `$79FC`/`$7A1C` -- both written in
`w_A0C2` from constants each of the six encounter setups pushes:

```
    encounter    $18C3   $18C4
    w_A1D0         8       10
    w_A222         8       10
    w_A274         8       10
    w_A2C6         8       10
    w_A318         9       10
    w_A36A         9       10
```

**One fighter's hit window is 8 or 9. The other's is 10. In every encounter.**
`w_A0C2` does contain an adjustment that would move them toward each other --
`1-` on `$18C4`, `1+` on `$18C3`, converging on 9 and 9 -- and **it has never
run**. The condition is `SWCHB = $80`, and the switches are active low, so that
asks for the right difficulty switch in position A *while Reset, Select and
Pause are all held down at once*. Reset restarts the game.

It reads like `=` written where a mask test was meant. Fix 17 is the two-cell
repair. Somebody knew the two windows were not the same and put it on a switch;
the switch has been dead since 1987.

The direction survives the bug, and it is evidence. `1+` goes to the smaller
window, `1-` to the larger, so the adjustment exists to hand somebody parity --
and the smaller window, `$18C3`, governs the *first* fighter's strikes, which
is the block the player's slot `$1878` lives in. That is a second, independent
line pointing at the same conclusion as the address ordering.

Which one is the player is an inference from address ordering and nothing more,
so fixes 5-7 set both to 10 (or both to 12), which makes the inference not
matter.

## What the hit does not do

Both post-hit routines are the same, and neither moves anybody:

```
  7A30   LDA pos / SBC #$0E / STA $18D5      place a graphic, 14 units back
         LDA #$8C / STA $18D3
  6A9C   ...identical, into $18CD / $18CB with #$8D
```

That is a hit spark. **The 7800 version has no knockback**, which is option 6
below.

## Picking fixes rather than building combinations

`patches/karateka.py --bundle` writes `karateka.abp`: twelve options over
four knobs, twenty-two sections, and two floats. See
[patchset-format.md](patchset-format.md).

```
python tools/patchset.py list  karateka.abp
python tools/patchset.py apply karateka.abp --rom karateka.a78     --with dose-4,generous-reach,remap --out fixed.a78
```

Each knob is a radio group -- one cadence, one hit window, one mapping -- and
the patcher refuses two settings of one knob rather than applying them in file
order. Sections carry their own CRC32, so a ROM already patched elsewhere is
still a valid target for whatever nobody has touched, and applying options in
two goes gives a byte-identical result to applying them at once.

**It also caught a bug in fix 1.** The input-latch routines were hardcoded at
`$FF80` and `$FFA0`, and neither address is free: `$FF80`-`$FFF9` holds
high-entropy data right below the 6502 vectors, which is where a 7800's BIOS
signature block lives. Emulators do not check it, so every build so far ran.
As floats, the two routines go to the 64 genuinely free bytes at `$FF40`
instead, and the bundle's output differs from fix 12 in exactly the two JSR
operands and the relocated code.

## Built to be played, not yet measured

Four new builds, and one correction to every old one.

**The correction.** Fix 1 put its two routines at `$FF80` and `$FFA0`, and
neither address is free -- `$FF80`-`$FFF9` holds high-entropy data immediately
below the 6502 vectors, which on a 7800 is the BIOS signature block. Emulators
do not verify it, so every build so far ran. They now go to `$FF40`, which is
64 genuine zeroes. Every fix that includes fix 1 changed with it.

**Fix 13, `player-reach` -- a test rather than a fix.** Which of the two hit
windows belongs to the player has been an inference all along: the `$18C3`
tests probe from `$187D`/`$1885`, the `$18C4` tests from `$189C`/`$18A4`, and
the player's dispatcher writes into slot `$1878`, which sits in the first
block. Fixes 5 and 6 were built so that would not matter. This does the
opposite deliberately -- `$18C3` gets 12 against the other's 10 -- so one
encounter settles it:

- your strikes connect from further: the player is `$18C3` and did have the
  smaller window
- the opponent starts reaching you: the inference was backwards

Either answer is worth having, and neither can be read off the ROM.

**Fixes 14 and 15, knockback.** Four units and eight. The post-hit routines
place a spark and move nobody; each knows which fighter was struck, because
there is one per direction, and every body point is built from a base plus an
animation offset, so the bases are `$186D` and `$188C`. The **call sites** are
redirected rather than the routines, because `$7A30` has a fifth caller at
`$8CAF` in the approach code, which should not learn about knockback.

Try fix 15 first. If eight units -- one whole walking step -- shows nothing,
four never would, and the answer is that the animation script rewrites the base
before the shove is drawn. That is the thing worth knowing and it takes one
fight to find out.

**Fix 16** is 3, 6, 11 and 15 together. Knockback pushes fighters apart, so it
wants the shorter loop underneath it or closing again is the old slog.

All four are in the bundle too, knockback as its own knob.

## The difficulty switches affect nothing

Asked directly, and the answer is nothing at all. Three lines of evidence, none
of which needs the game run.

The image builds five console-switch primitives, one per switch, all in a row:

```
   p_5A3E   LDA #$01 / AND $A9      Reset            called
   p_5A50   LDA #$02 / AND $A9      Select           called
   p_5A64   LDA #$08 / AND $A9      Pause            called
   p_5A78   BIT $A9 / BVC           left difficulty  NEVER CALLED
   p_5A8A   BIT $A9 / BPL           right difficulty NEVER CALLED
```

(That also confirms the bit layout out of this ROM rather than out of a manual:
0, 1, 3, 6, 7, with `$A9` holding `SWCHB XOR $FF`.)

**Searching all 49,152 bytes for a cell naming either difficulty word finds
zero.** Reset appears once, Select twice, Pause four times. The two difficulty
primitives were compiled and never invoked.

**No other access reads SWCHB.** Sweeping every absolute access into the RIOT
page and its mirrors turns up `LDA $0280` for the joystick, three init writes,
and nothing else that lands on `$0282` or a mirror of it.

**And the one place difficulty was meant to matter is broken.** `w_A0C2` tests
`SWCHB = $80`, which needs Reset, Select and Pause held together -- see fix 17.

So the feature was written, wired to the hit windows, and never worked: three of
the five switch words are hooked up and the two for difficulty are not, while
the code that would have consumed them has an equality test where a mask test
belongs. Fix 17 makes the right switch live, and is currently the only thing
that makes either switch do anything at all.

## Two defects in the remap, found by playing it

Both were reported from a session, and neither could have been found by
reading.

**Kicks always came out middle, punches were fine.** The height decoder
`p_7042` opens with

```
  704A  LDA $09        INPT1
  704C  BMI $7068      button 2 held: push 0 and give up
```

which was right while a bare stick attacked and button 2 meant *move*, and is
exactly backwards once button 2 *is* the kick. The height came back 0, the CASE
in `w_7612` that picks high/middle/low matched nothing, and the chain fell
through to the middle strike. Punches were untouched because they are on button
1 -- which is why exactly half the controls looked broken, and is the detail
that identified the cause.

A replacement word at `$A710` guards on **"a button is held"** instead, true
for a punch and a kick alike and false the moment both are released, so a
strike stops repeating when you let go rather than running on while you walk.
All four sites that ask for a height now use it: the two in the dispatcher
where a command is issued, and the two six-frame re-polls inside the punch and
kick chains.

**Down could drop you out of stance mid-fight.** The stance change asked for
down with the stick centred and did not care about the buttons, so releasing
forward while still holding the button after a low strike left exactly that
combination and read as stand-down. It now requires no button held.

That guard costs six bytes and the fighting-stance decoder only had 49, so it
moved to `$A740` and the block in the primitive became a `JMP`. Both new blocks
are assembled from source with `tools/asm.py` rather than by hand -- the first
hand-built attempt put a `BMI` twelve kilobytes from its target, which the
assembler caught and a hex editor would not have.

Fix 11 is corrected in place rather than superseded: this is a wrong
implementation of a right idea, not a wrong idea. Fixes 12 and 16 change with
it, and in the bundle both blocks are floats like everything else.

## Two ways to drop the stance guard

Fix 11 keeps down-with-the-stick-centred and adds "no button held" so it stops
firing on the way out of a low strike. That works, and it is a *guard* -- a
thing to remember rather than a thing that is obviously right. Both of these
pick a gesture nothing else uses instead, and drop the guard.

**Fix 18, both buttons together.** The vertical stick then plays no part in the
fighting stance at all: up and down are purely a strike height, and no order of
releasing anything can produce a stand-down. The guard is gone, not tightened.

It has one trap, and it is why the walking-stance block had to move as well.
**If fighting-to-walking is both buttons, then holding both must do nothing in
walking stance** -- otherwise button 1 puts you into fighting stance, both
buttons take you straight back out, and the stance flickers for as long as you
hold them. Guarding walking on button 2 costs four bytes and there were none
spare in the primitive, so both halves now live in free space.

**Fix 19, up and left together.** Left alone still walks left, so the gesture is
the diagonal, and nothing else in the fighting stance reads left with a
vertical. No guard, and no change to walking either. But it is still a
direction, so it is one bad diagonal from a stand-down where fix 18 is none.

Fix 20 is 3, 6, 15 and 18 -- fix 16 with the both-buttons mapping. All three
mappings are alternatives on the same knob, so the bundle will refuse two of
them and `--check` says so.

### A generator bug this turned up

Making the variants floats broke fix 11, and the reason is worth keeping. The
bundle builder marked *every* float's fixup site as a placeholder in *every*
option's patch. Fix 18 relocates the walking block and fixes up `$7834` -- which
is the second byte of fix 11's walking block, real code. Fix 11 came out with
`$FF` in it.

Placeholders are now taken from the floats an option actually carries. The
symptom was loud, but only because two options happened to disagree about one
byte; a bundle where they did not would have been quietly wrong.

## Three things a second session found

**Movement never stopped.** A walk keeps going while one of two words says yes,
and the stock pair asks for button 2 with a direction, because button 2 was how
you moved. The first remap NOPed that test out so a bare direction would keep a
walk going -- and *a test removed is not a test inverted*. They then said yes
with any button held, so you kept walking through a strike and could not stand
still to fight.

That is the same mistake as the kick bug, made in the same patch, in the
opposite direction: one guard was left in place when it should have been
inverted, the other was deleted when it should have been inverted. The
replacements ask for a direction and **no** button, which is what the stock pair
meant in the stock mapping. Six bytes where four were free, so they are new
words in free space and the three cells that named the old pair are repointed.

**Knockback pushed the goon through the stage exit.** Nothing clamps the goon's
position, so repeated shoves walked it through the barrier the player cannot
cross until it dies -- deadlocking the stage, and making walking left fatal.

The cap is `$70`, and it is not a guess: `w_A4D2` compares the *player's* X
against `$0070` to decide the stage is cleared, so that is where the exit is,
and both fighters' positions are in the same space (the AI in `w_9866` subtracts
one from the other). A shove that would put the goon at or past it now leaves
it where it was. The player's shove was already floored at zero, for the same
reason from the other end.

**The princess scene skipping: it was the latch.** `karateka-dose-0` ruled out
the cadence, so the mechanism was traced rather than guessed at.

The cutscenes ask "is anything pressed?" and skip if so:

```
   w_8A3E:  p_59FC        read the controls
            p_8A1C        is anything pressed?
            0BRANCH +4
            w_A52A        ...then skip the scene
```

and `p_8A1C` answers from `INPT0`, `INPT1`, and then **`$A2`/`$A3` -- the
decoded direction**. That is a live reading in the stock game, because the
reader samples SWCHA on the spot.

It stops being live the moment the controls are latched. `$A2`/`$A3` then mean
"a direction was pressed at some point since the last read", which is exactly
what the latch is for and exactly the wrong answer to this question. A tap
during a fight survives into the next cutscene and skips it.

The replacement word reads SWCHA directly for the two direction tests -- bits
6-7 are player one's horizontal and 4-5 the vertical, active low, the same
split the decoder at `$5A07` makes -- and pushes the same count. It lives in
fix 1 because fix 1 causes it, and it is harmless without the latch, since
reading SWCHA live is what the stock word effectively did.

Twelve of the cutscene steps call `w_8A3E` and one calls `w_8A4C`; both poll
words are repointed.

## Four small knobs, and what is left after them

Read out of the thread rather than found by moving code anywhere.

**Fix 21 / 22, walk speed.** Commands 4 and 5 install a four-step chain setting
`$AA` to -2 or +2 each step, so a walk cycle covers eight units. That is the
slog to reposition, and it is **separate from the loop cadence** -- it can be
changed without the fight getting faster with it, which is the trade every dose
makes. Eight cells, four each way; 21 is three units a step, 22 is four.

Watch the feet. The stride is drawn for eight units a cycle, so a faster walk
slides, and past some point it stops looking like walking. Nobody can call that
from a listing.

**Fix 23, strike repeat.** Both attack chains count to six and then re-poll the
stick to decide whether to strike again -- `w_753E` for the punch family,
`w_7670` for the kick. Four instead of six. Two cells.

**Fix 24, the opponent's distance test.** `w_9866` works out `goonX - playerX`
and compares it against 15. That is the opponent deciding something about
distance, and it is the likeliest home of the retreating that makes closing
such work.

It is also the least certain thing built here, and the only one that changes
what the opponent *decides* rather than how fast something moves: what happens
either side of that number has not been read. If the opponent gets more passive
rather than less, the comparison runs the other way and the number wants
raising, not lowering.

### Still unbuilt, and why

- **Buttons through the latch for the strike decision** -- the remap reads
  `$08`/`$09` live in the decoder, so a tap shorter than a loop is still lost.
  The 8-bit split is the answer: latched for *deciding* a strike, live for
  *repeating* it. Fix 2 failed at this once, and the reason it failed is an
  ordering question between the reader and the decoder that the listing does
  not settle. A day, and it wants the 33 ms tap harness.
- **The difficulty switch choosing a wait dose at runtime** -- both switch
  primitives exist and neither is called, so a build could offer two cadences
  on a switch instead of two ROMs. A wait block is fourteen bytes and a call to
  a conditional one is six, so it fits. Two or three days.
- **Only paying a frame for busy entities** -- still waiting on
  `Measure entity slots.bat`, which is an hour and decides it either way.

## Option 5 is dead, and the probe is what killed it

188 seconds of fighting, control passing -- the player's slot changed 958
times, so the instrument was working when the other eight reported what they
did.

```
   slot  addr   distinct  changes  commonest  dwell   % of the fight
    1    $18AD      193      199    w_68EE    9901    87.6%
    2    $1878      270      958    w_7898    2313    20.5%   <- the player
    3    $18AF      193      193    w_9518    3730    33.0%
    4    $1897      246      950    w_881A    1316    11.6%   <- the opponent
    5    $18B1      188      188    w_9866   10868    96.1%
    6    $18C5      170      171    w_66C8   10868    96.1%
    7    $18C7      167      169    w_673A   10868    96.1%
    8    $18DF      155      230    w_9908    9896    87.5%
    9    $18C9      167      168    w_689A   10868    96.1%
```

**Nothing is idle.** Four slots sit on one word for 96% of the fight -- with an
identical dwell, so they entered a state together and stayed -- but the words
they sit on are not no-ops:

```
   p_66C8   pushes constants onto the stack
   p_673A   EORs $18E3 / $18E5 / $18E7      a flicker
   p_689A   reads $18C2, EORs $1919         another toggle
   p_68EE   reads $18DC and branches
```

Toggles. Their *rate* is what they are for, and a rate is set by how often the
slot is called. Skipping the wait after one does not stop it doing nothing --
it makes it do its thing twice as often, and a toggle called twice a frame is
a toggle that has stopped. There is no free frame here: every slot's update
has an effect whose timing matters.

So the three days do not get spent, which is the whole reason the hour was
worth spending.

**Two things it gave for free.** Slot 4, `$1897`, is the opponent: 950 changes
against the player's 958, which is what two fighters running the same kind of
state machine look like and nothing else does. And slot 5 runs `w_9866` -- the
`goonX - playerX` comparison -- for 96% of the fight, so fix 24 is aimed at
something that really is being asked constantly rather than at a branch that
might never run.

**And a lesson about the instrument.** The probe led with "0 of 9 idle", which
was true and useless: nothing in a running game holds one pointer and never
changes it, so a test for exactly that can only come back zero. The signal was
the dwell column all along. It now reports how many slots held one word for
over 90% of the fight, which is the question that had an interesting answer.

## A varying strike rhythm, and the one byte in the way

Fix 23 moved the gate from six to four. That is a different tempo, not a
varying one, and the complaint was about uniformity -- reported from the 8-bit
version as a 3-1-1 pattern, audible rather than merely felt.

Making it vary is easy in shape. Both attack chains do

```
   $191A C@  1+  DUP  $191A C!  $0006  =  0BRANCH ...
```

so the gate is one comparison, and a word that compared against `PATTERN[phase]`
instead -- advancing the phase each time it fired -- would give any rhythm
wanted. What it needs is somewhere to keep the phase, and **there is no byte in
this image that can be shown to be free by reading it.**

The reason is the interpreter. Its data stack lives in zero page, indexed by X,
based at `$CF` (`LDX #$CF` at `$408F`) and growing down. So `LDA $55,X` names
one address in the listing and touches a different one every call, and
zero-page indexing wraps: a base of `$2A` with an empty stack reaches `$F9`, a
base of `$08` with a shallow one reaches `$D7`.

Of the 48 bytes above the stack base, exactly four are never named directly
anywhere in the ROM -- `$D7`, `$DC`, `$EF`, `$F9` -- and every one of them is
reachable in principle from some indexed base at some stack depth. Which of
those depths actually occurs is a runtime fact.

`$191A` itself will not do either. Its high nibble is free -- the counter only
ever holds 1 to 6 -- but the chains reset it with a plain `LIT $0001 ... C!`,
which would wipe a phase stored there on every strike.

**`Find a free byte.bat`** settles it. It taps the four candidates plus `$E8`
as a control -- the thread pointer, written on every dispatch, so a run where
it reports zero is a broken tap rather than a quiet game. Play a stage
properly, and whichever candidate comes back untouched is a byte a patch can
have.

That is evidence rather than proof: a code path nobody reached could still use
it. So play widely rather than long, and prefer a candidate whose nearby
indexed bases are also absent.

With a byte in hand this is an afternoon: one word for the gate, one small
table, and the pattern becomes something to tune by ear rather than to argue
about.

## The free byte, and the window it was measured over

```
   # frames 9961
   D7  0 writes
   DC  0 writes
   EF  51 writes, first from PC $FB93
   F9  0 writes
   E8  3920822 writes          <- the control: the taps worked
```

`$EF` is out. `$D7`, `$DC` and `$F9` were untouched across 166 seconds of real
play, and `$E8` -- the interpreter's thread pointer -- saw 3.9 million writes,
so the run proves something rather than nothing.

**But the session never reached the fourth fight**, which is where the bird
appears, and a negative is only as good as the window it was taken over. That
objection came from the person playing it, and it is the right one.

What can be said statically: the six encounters differ only in four constants
and one setup word each. The three setup words unique to encounters 4-6 --
`w_9B18`, `w_9D24`, `w_9E28` -- are structurally identical to one another and
write only to zero page `$31`, `$33`, `$35` and `$36`. None of them is a
primitive, so none contains machine code that could store anywhere else.

What cannot: the bird's *runtime* behaviour runs from an entity slot through
the main loop, and there is no way to separate "code the bird runs" from "code
the loop runs" by reading.

**So fix 25 exists.** `w_A52A` is a CASE on `$18AA` and its arms are one cell
each; pointing the first at encounter 4 starts the game there and leaves the
sequence intact, because each encounter word sets `$18AA` to its own number and
the chain runs itself from there. One cell.

Build it, run `Find a free byte.bat` on it, and the window includes the bird.
If `$D7`, `$DC` and `$F9` come back clean a second time, one of them can carry
the strike-rhythm phase.

It is a test build and should never be combined with anything judged on feel:
it skips the three fights the pacing complaints came from.

## The byte was found, and the rhythm is built

Two probe runs, deliberately covering different halves of the game:

```
   run 1   9961 frames, encounters 1-3         D7  DC  F9  all untouched
   run 2   2471 frames, from encounter 4       D7  DC  F9  all untouched
   control $E8, the thread pointer             3.9M and 878K writes
```

`$EF` saw exactly 51 writes in *both* runs despite one being four times longer,
which places them at boot rather than in play -- and puts it out either way.

The second run exists because the first was taken over a window that never
reached the bird. Fix 25 starts the game at encounter 4 so the window could
include it.

**`$DC` is the one used**, and not because it was cleanest -- all three were
equally clean. `$D7` and `$F9` can each be reached by an indexed store at a
stack depth of *2*, which is a depth that happens constantly; `$DC`'s shallowest
route needs a depth of 8. When the empirical evidence cannot separate three
candidates, the static analysis still can.

### Fix 26

Both attack chains end with `LIT $0006 =`, a comparison against a constant.
Three cells become a word that compares against a table and steps through it,
followed by a `BRANCH +2` filling the other two -- and that branch lands exactly
where execution would have gone anyway, so it costs nothing:

```
   $7552:  A8C0  4D56  0002      gate, BRANCH, +2
   $7558:  4D6C                  the 0BRANCH that consumes the flag
```

The table is **4, 2, 2**, and the arithmetic is worth stating because it caught
me out. `$191A` is reset to **1** when a strike starts, and the gate fires when
the counter *reaches* the table value -- so an entry of n runs for n-1 calls.

The stock value is 6, which is **five** strikes before the pause, and that is
exactly what someone watching the original counted. An earlier version of this
note said "counts to six", which was loose in a way that mattered: 6, 2, 2 would
have given 5-1-1, not the 3-1-1 that was asked for. 4, 2, 2 gives it.

## Disconnecting the loops: what it would take, and the space for it

**Space is not the constraint.** The stock ROM has 7,127 free bytes in 27 runs,
6,448 of them contiguous at `$A6D0`. Every patch built so far -- three
relocated decoders, a height word, two walk pollers, a live cutscene poll, two
knockback shoves and a rhythm gate -- has consumed **374 bytes**. After the
largest combined build there are still **6,753 free, 5,957 of them in one
run**. A decoupled loop is a few hundred bytes. It would not come close.

The constraint is the coupling.

### What is actually tied together

`EXECUTE` on an entity slot does two things at once: it advances that entity one
animation step, *and* it is the only moment that entity can make a decision.
One call, one step, one chance to react. The loop hands each of nine entities
one call per round and pads with a frame apiece, so a round is thirteen frames
and everything -- movement, animation, AI, and how fast the game notices the
stick -- is measured in rounds.

### Three ways out, and the one worth doing

**A. All entities in one pass, one wait at the end.** Remove the eight waits,
add one multi-frame wait. The round is still thirteen frames and entities
update together instead of staggered. **This gains nothing** -- the player still
gets one call per thirteen frames. Cheap, tempting, and worth ruling out
explicitly.

**B. Gate the animation instead of the loop.** Run the loop fast -- skip all
eight waits, about five frames a round -- and make `EXECUTE` skip an entity on
most rounds so animations keep their old rate. Then exempt the case that
matters: when the player's slot holds `w_7898`, the dispatcher, the player is
idle and waiting for input, and that gets executed every round.

Reaction latency falls from thirteen frames to about five for the first input;
animation is unchanged. The slot probe already says the player's slot holds
`w_7898` 20% of the time, so that is measurably the state you are in when
waiting to react.

Cost: a replacement for `p_513C`, a round counter, and one byte of RAM -- and
there is already a proven-free one at `$DC`, plus a probe that can prove more.
Perhaps a hundred bytes of code. **Two or three days**, with a playable first
build in an afternoon, because the ratio can be a constant and tuned by feel
the way the wait dose was.

**C. Halve what each step advances.** Make every behaviour chain move half as
far per call and run twice as often. This is the option originally ranked at
weeks, and it deserved it: fifty behaviour words, every timing constant in each.
B gets the same result by gating calls rather than editing every chain, so C is
dominated.

### What could sink B

It is the first change that moves *every* entity's timing at once. Anything that
quietly assumes a thirteen-frame round -- sound cadence, the fight timer,
cutscene lengths, the AI's own counters -- shifts with it. Nothing in the
listing announces such an assumption; the only way to find them is to build it
and play, which is also true of every other change here and has found something
every time.

The staged version: skip the waits and gate everything at 3:1 first, with the
player exemption off. If that plays like the stock game at a shorter loop, the
gate works and the exemption is the easy part.

## Fix 27: the staged step, built

Every wait skipped, so the loop spins at about five frames, and the nine
entity `EXECUTE`s replaced with a gated one that runs them on **one round in
three**. An animation step then lands every fifteen frames against the stock
thirteen.

**Slower than stock, not faster.** The worry when this was proposed was that
the player would end up moving too fast, and the arithmetic says the opposite:
5 x 3 = 15. That is the point of staging it. The question this build answers is
"does gating the entities break the game", and it answers it without the answer
being confounded by everything also running quick.

Only the nine `EXECUTE`s in `w_A59C` are repointed. There are three more in the
image -- `w_4142`, `w_514C`, `w_5AC4` -- and they have nothing to do with the
entity round.

The state is one byte, `$D7`, holding a call counter in bits 4-7 and a phase in
bits 0-1: nine calls to a round, three rounds to a cycle. **Not `$DC`**, which
fix 26 already uses for the strike rhythm -- and two patches quietly sharing a
RAM byte is precisely the clash `--check` cannot see, because it compares what
fixes write to *ROM*. `$D7` came back untouched in both probe runs alongside
`$DC`; the difference is that its shallowest route to an accidental indexed
store needs a stack depth of 2 rather than 8, so it is the second-safest of the
three rather than the first. It is written nine times a round, which is the
redeeming feature: if `$D7` does belong to something, this fails at once and
obviously rather than corrupting something once an hour.

### What to watch for

Not "is it faster" -- it is not meant to be. Whether it plays like *itself*.

Anything quietly assuming a thirteen-frame round moves with this: sound
cadence, the fight timer, cutscene lengths, the AI's own counters. Nothing in
the listing announces such an assumption, and a specific failure -- music
dragging, a timer that never expires, an opponent that stops reacting -- is
worth more here than a smooth result, because it names the assumption.

If it plays roughly like itself and a shade slow, the gate works. Then the loop
is free to run at whatever rate the controls want, and the player's dispatcher
can be exempted from the gate so reactions land in five frames while animation
stays at fifteen. That is the next step, and this one has to survive first.

## The gate held, so here is the point of it

Fix 27 played, and nothing assumed a thirteen-frame round: no dragging music,
no timer that never expired, no opponent that stopped reacting. It read as "a
touch slow", which is exactly what fifteen frames against thirteen should read
as, and the controls felt considerably better -- which they should not have,
with the exemption off, and is worth a note below.

**Fix 28** keeps one wait, so the round is six frames, and gates 2:1: a step
every twelve. A shade the other side of stock rather than a shade slow, and one
bit of phase instead of two.

**Fix 29** is fix 28 with the player's dispatcher exempt from the gate.
`w_7898` reads the controls and turns them into a command, and it is installed
into slot `$1878` and nowhere else -- eight sites, every one the player's -- so
exempting it *by word address* is exactly "let the player decide every round",
with no way to catch an opponent by accident.

```
   A961  LDA $01,X          the word about to run, high byte
   A963  CMP #$78
   A965  BNE gated
   A967  LDA $00,X          ...and low: $7898, the dispatcher
   A969  CMP #$98
   A96B  BEQ run            run it whatever the phase says
```

**Animation every twelve frames. Reactions every six.** For the first time in
this cartridge those are two different numbers, which is the whole thing the
exercise was for.

The counter is advanced *before* the exemption is tested, deliberately.
Skipping the advance on the exempt call would make a round eight counted calls
instead of nine, and the phase would drift a little further every round until
the gate meant nothing -- which would have presented as "it stopped working
after a while", the worst kind of bug to go looking for.

### Why fix 27 already felt more responsive

It should not have. With the exemption off, the player's dispatcher was gated
like everything else, so decisions still landed every fifteen frames.

The likely answer is that the loop's *other* work -- everything in `w_A59C`
outside the nine `EXECUTE`s -- was never gated, and runs every round. If the
control reader is among it, the controls are being sampled at five frames
rather than thirteen, and with the latch remembering a press between samples
that alone makes a tap far harder to lose. It would also explain why the stance
button still missed occasionally: sampling got better, but the *decision* that
consumes the sample was still every fifteen frames.

If that is right, fix 29 should close most of the remaining gap, because the
decision is what moves this time. If the stance button still misses at the same
rate, the cause is somewhere else and worth chasing separately.

## Measuring the tap rate, and getting it wrong first

"Tapping the stance button works about 90% of the time" is a real observation
that cannot be compared with anything, so `probes/tapstance.lua` puts it on a
scale: press button 1 for a fixed number of frames, watch `$187C` -- the stance
byte, which the command decoder branches on -- and count how often it moves.

**The first four numbers it produced were rubbish, and it is worth saying how.**
MAME's "P1 Button 1" is INPT1; "P1 Button 2" is INPT0. The stance change reads
INPT0. The probe pressed the wrong field, so nothing it did reached the game,
and what it reported was the rate at which the stance changed *on its own* --
which is a number, differs between builds because they run at different speeds,
and means nothing whatever. Stock 5.8%, fix 28 10.0%, fix 29 15.0%: all noise,
all retracted.

Two things gave it away. Every tap length produced an identical rate, which is
impossible if taps matter. And the control -- run only because the reading
looked wrong -- put a **60-frame** press at 10% against **6.2% for pressing
nothing at all**.

That control is now inside the probe: it holds the button for 120 frames before
any trial, and if the stance never moves it says so in capitals and refuses to
present the rates. This project wrote up "a read tap that reports zero needs a
positive control" from a sibling repository four exchanges ago, and then shipped
a probe without one.

### With the right button, and in an actual fight

The corrected-button table was still wrong, for a second reason: it measured
whatever the game was doing 900 frames after Select -- the title, the walk to
the palace, an intermission -- and the stance byte means nothing outside a
fight. The probe now waits for `$18AA`, which is 0 until an encounter is set up
and 1-6 afterwards, and refuses to report if it never gets one.

Under those conditions, with the control passing:

```
   tap         stock   fix 29   fix 29 + remap
    8 frames    0.0%    97.5%      97.5%
   16 frames    0.0%    97.5%      97.5%
   40 frames    7.5%    62.5%      57.5%
```

**Stock does not respond to a tap at all** -- 0 of 40 at both 8 and 16 frames --
while a 120-frame hold moves the stance every time, which is what the control
proves. That is the original complaint stated numerically: *have to hold down
the press*.

**Fix 29 lands 97.5% of them.** Reactions every six frames instead of every
thirteen, and the difference is not subtle.

**The remap makes no difference at these lengths** -- 97.5% either way. So the
toggle is not what limits a tap; the decision rate is. The remap's value is
elsewhere.

**But the 40-frame row is the toggle, and it is real.** Every build drops when
the press is long enough to span several decisions: stock 7.5%, fix 29 from
97.5% to 62.5%. A held toggle flips and flips back and lands where it started,
which reads as a miss and is one -- the prediction that this would explain some
of them was right, just not at the lengths a person actually taps.

Two earlier tables in this section are superseded. The first pressed the wrong
button; the second pressed the right one outside a fight. Both looked like
results.



## Knockback: yes, both fighters, and three goes at the cornering

Asked directly: **the player is shoved too.** `_shove` installs two wrappers,
one per direction of hit --

```
   A6D0  when the PLAYER is struck    push left,  floored at 0
   A6F0  when the GOON is struck      push right, resisted near the exit
```

-- because the collision groups come in pairs: `$7A30` runs when the first
fighter is hit and `$6A9C` when the second, and each moves the base position of
whoever was hit.

### The units

`w_A4D2` compares the player's X against `$0070` to decide the stage is
cleared, so the exit is at 112 and the arena runs from roughly 0 to there. The
encounter setups place the goon at `$26` (38) or `$50` (80). A walking step
covers 8 units over four frames, so a step is about 7% of the stage and the
default shove is exactly one step given back.

### Three shapes, two of them wrong

**A hard cap at `$70`** stopped the deadlock and relocated the pile-up to the
cap. Nothing pulls the goon back, so hit after hit walked it to the boundary
and pinned it there -- and with the player pursuing, the camera followed and the
fight ended up in the left quarter of the screen.

**A player-relative limit** never fired at all, for the reason the person
playing it gave before it was built: the player is always closing, so
`goonX - playerX` stays small and the limit does nothing.

**Resistance works because it is the right shape.** The shove is half the
remaining headroom, capped at the full amount, and zero at `$6F`:

```
   A6F3  LDA #$6F
   A6F5  SEC / SBC $188C      headroom
   A6F9  BCC done             already past it: immune
   A6FB  LSR                  half of what is left
   A6FC  BEQ done
   A6FE  CMP #$08             ...but never more than a full shove
```

From `$50` a run of hits gives 8, 8, 7, 4, 2, 1, then nothing. The goon
approaches the exit and never arrives, so there is no wall to be pinned
against and no moment where the shove stops mattering abruptly.

A hard boundary and an accumulating force were never going to sit well
together; a boundary the force fades into was the answer, and it came from the
person watching the camera drift rather than from the listing.



## Making the stage bigger: tried, broke, and the reason is better than the attempt

Fix 30 moved the stage-clear trigger from `$70` to `$90`, on the grounds that
`$70` is a trigger rather than a wall -- which it is. It broke at once: the
defeated goon sat mid-stage, the camera stopped, and the player walked into an
invisible barrier on the right.

My first reading was that the barrier had always been there and the trigger
merely fired before anyone met it. The person playing it had a better one --
*defeating the goon makes the right of the screen the exit* -- and the ROM
agrees:

```
   w_8686 installs w_A4D2 into slot $1897
```

`$1897` is the **opponent's** slot, identified from the entity probe by its 950
changes against the player's 958. So when the goon dies its slot is repurposed
to run the exit check, which is why the defeated goon stops being anywhere in
particular. And `w_A4D2` is that check:

```
   LIT $186D C@   LIT $0070   w_5012        ( SWAP < : is the player past 112? )
   0BRANCH +$16
   LIT $0016  LIT $18DE  C!
   LIT $A3EC  LIT $1878  !                  ( advance the encounter )
```

**`$0070` occurs exactly once in the entire threaded image, and never as a
machine-code immediate.** There is no second constant placing the exit. The
trigger is a number; the exit is a *picture*, and the picture is in the
backdrop.

So moving the number moves where the game *consummates* the exit without moving
where the exit *is*. The player walks to the drawn gate at 112, nothing
happens, and they carry on to the end of the backdrop -- which is the invisible
barrier, and was invisible only because nobody had ever been allowed to reach
it.

### What that means for a longer stage

It is an artwork job, not a logic one. The trigger would have to move *and* the
backdrop would have to extend, and the backdrop is display-list data in RAM --
the code writes no MARIA register anywhere in the image, so no comparison a
disassembler can show governs how far it goes.

`tools/dlwalk.py` reads a display list and the sibling toolkit has a `dumpdl`
probe for capturing one from a running game. That is what would say how many
background objects exist and where they stop.

Worth doing only if a longer stage is wanted for its own sake. The knockback
resistance already keeps the fight off the boundary, which is what prompted the
question.



## Three strengths of knockback

A walking step covers 8 units over four frames, which is the yardstick:

```
   hits ->        1  2  3  4  5  6  7  8   total   ends at
   fix 31 light   2  2  2  2  2  2  2  2     16      $60
   fix 14         4  4  4  4  4  4  3  2     29      $6D
   fix 15 hard    8  8  7  4  2  1  0  0     30      $6E
```

Fix 15 gives a whole walking step back per hit and reads as strong. Fix 14 is
half a step. Fix 31 is a quarter -- a nudge felt rather than watched.

The interesting column is the last one. Eight hits put the goon in almost the
same place whichever dose is used, because the resistance is doing the work:
fix 15 spends its budget in the first three hits and then taper, fix 14 spreads
it out. What the dose really changes is **how it feels per hit**, not where the
goon ends up -- and the light dose is the only one that stays clear of the
taper altogether, so it is the only one where a hit late in a fight shoves as
far as a hit early in one.

## Slot 8, precisely: what it is, and a fix out of it

Not a subsystem, on closer reading. `w_9908` -- what slot 8 usually holds -- is
one cell, `EXIT`, and nothing else: no branch, no data, no other primitive.
Calling it does exactly nothing.

But the slot itself is not decoration either. `w_A59C` sets `$18DF` to
`w_9908` once, at the top -- and the loop's own closing `BRANCH` targets $A5AA,
*after* that reset, so it runs once per encounter, not once per round. What
lands in `$18DF` between resets comes from `w_881A`, the opponent's dispatcher,
which runs *earlier in the same round*, in slot 4. When the opponent decides to
attack, its dispatcher installs the attack's first behaviour word into `$18DF`
right then, and two self-reinstalling chains (`$8D32<->$8D68` for one attack
family, `$8DBC<->$8DDE<->$8DAC` for the other) keep putting themselves back
into `$18DF` round after round, one animation step per pass, until the attack
finishes and the slot goes back to idle.

So slot 8 is a one-round bridge that lets the opponent's decision and its
first visible step land in the same round instead of the next -- and it sits
idle **87.5% of the time**, which is the entity probe's own number from a real
fight, not a guess.

### Fix 32: skip the wait, not the check

The `EXECUTE` for slot 8 has to run every round regardless -- it is the only
thing that can catch the round an attack starts. What does not have to run
every round is the *wait after it*, the eighth and last of the nine. Fix 32
reads `$18DF`, compares it to `w_9908`, and skips that one wait only when they
match:

```
   average round length = 13 - 0.875 = 12.125 frames   (stock: 13)
```

A touch under 7% off the top, and it costs nothing on the round an attack
actually starts -- the busy path is the original 14 bytes of wait code,
untouched, reached by the same branch.

This is the fine-grained version of "only pay for entities that are doing
something" from earlier in this document, and it only fits because the earlier
findings gave it somewhere to land: the entity probe supplied the 87.5%
number, and the branch-target check (confirming the reset runs once, not every
round) is what made it safe to build at all -- get that backwards and the fix
would silently drop every attack's first frame.

**It conflicts with fix 27 and doses 6, 7 and 8**, which touch this exact
wait unconditionally -- real conflicts, and `--check` catches them. Fix 27 is
withdrawn as a result: fix 28 and fix 29 already do the same idea better, and
there was no reason to keep the plainer version live once it started colliding
with something more precise. Fix 33 is fix 12 plus this, in the bundle as
`slot8-gate`, independent of the loop-cadence knob.

**Fixes 28 and 29 are a different case, and an earlier version of this
document got it wrong** -- it listed them alongside fix 27 as conflicting,
which `--check` does not actually say and which is not true. `_install_gate`
(what fixes 28 and 29 are built from) skips seven of the eight original waits
and deliberately keeps the eighth -- "keep the last one" is in the code as a
comment -- because every entity's `EXECUTE` gets repointed to a round-counter
gate instead, and the wait right after slot 8 is left as plain, unconditional,
full-price code. That is precisely the address fix 32 patches, and the two do
not share a single byte. `--check` confirms it; the correction is not "trust
me now instead," it is that the tool says so and can be asked again.

### Fix 34: fix 29 and fix 32, actually combined

They compose at the semantic level too, not just the byte level, which is
worth showing rather than assuming from "neither touches the other's bytes."
Slot 8's `EXECUTE` cell is repointed to fix 29's gate word, and *that* word
ends in `JMP $401E` on every path through it -- skipped or not -- so the
thread pointer always continues to the very next cell in `w_A59C`'s own body,
which is exactly where fix 32's replacement sits. Fix 32 gets to ask "is
`$18DF` idle right now" every round regardless of what fix 29's gate decided
for that round, against whatever `$18DF` actually holds by the time control
reaches it.

```
   fix 29 alone     round = 6 frames flat
   fix 34            round = 5 + 0.125 * 1 = 5.125 frames average
```

Reactions -- the player is exempt from the gate -- land in about 5.125 frames
instead of a flat 6. Everything else, gated 2:1, animates roughly every 10.25
frames instead of 12.

That is the diminishing return the question anticipated correctly: fix 29
already took the loop from 13 frames to 6, and the eighth wait was always the
smallest single piece left standing. Built as fix 34, `loop-gate-player-slot8`
in the bundle.

Not measured by play -- the 87.5% and the 12.125-frame figure are the entity
probe's and arithmetic on it, not a capture of this specific build.

---

## Knockback left the drawn fighter behind its logical one

A person playing `knockback-light` reported the screen getting stuck near the
end of the first stage, with the two fighters visibly clustered wrong and the
camera no longer centred on them -- the same cornering symptom from the
knockback section above, but this time it kept happening well short of the
`$6F` cap, so it had to be something else. The lead came from the person
playing it, not from the disassembly: they connected it to the goon dying and
to the camera no longer panning, and asked whether the shove was updating
something that ordinary movement updates and knockback does not.

It was. Every ordinary walk tick calls a shared routine at `$65C8` three times
per phase, and $65C8 does this:

```
6604  LDA $AA            read the current velocity
6607  CLC
6608  ADC ($02,X)         add it to a pointer computed from the call's args
660A  STA ($02,X)         write it back
660D  LDA #$3E / ADC $02,X / STA $02,X     advance the pointer by 62 bytes
6618  DEC $00,X / BNE $6606                repeat
```

So a walk step does not just move the fighter's base (`$186D`/`$188C`) --
it also walks a table of display-list zone X-fields by the exact same
delta, keeping a multi-zone sprite's segments in horizontal sync as it
moves. The shove added for knockback (fixes 14/15/16/29/31 and anything
built on them) was a bare `STA` into the base, added once, never calling
`$65C8` and never touching that table. A hit moved the fighter's *logical*
position -- the one hit detection, the exit check, and the AI's distance
test all read -- but left its *drawn* position exactly where it was. The
error is small on one hit and accumulates on every one after, which is
consistent with "gets worse over the course of a fight" rather than "broken
from the first punch," and reads exactly like a camera drifting off-centre
even though nothing about the camera itself is wrong.

### Finding the table without guessing at it

Two things made this traceable rather than a guess:

**The real 6502 PC, not the Forth thread pointer.** Tapping writes to
`$186D`/`$188C` and logging the *Forth* IP at the moment of the write gave
misleading agreement -- unrelated call sites reported the same IP, because
for a machine-code primitive the thread pointer has already advanced to
whatever cell follows the call, and many different callers share that next
cell. Reading `cpu.state["PC"].value` directly at the same moment gave the
true, unambiguous call site every time: `$905C`/`$90A0` for ordinary
walking, `$A6DD`/`$A708` (now moved -- see below) for the shove, with
nothing else in a full round.

**Diffing writes, not enumerating them.** `$65C8` computes its target
pointer from two small integers pushed by the caller and whatever the
second Forth stack cell already holds, which made the table's own base
non-obvious from statics alone. Tapping every write in `$2300`-`$2700`
(the RAM-resident display-list region already established earlier in this
project) during a walking window, filtered to writes whose PC falls inside
`$65C8`, gave the real addresses directly: `probes/tracetable.lua`.

The table turned out to be several 3-entry families, 62 (`$3E`) bytes
apart -- one family per zone of the moving fighter's sprite. Cross-checking
each family's write frames against which fighter's base changed that same
frame (`probes/tracefighters.lua`'s log) attributed them cleanly: the
player used one pair of families (`$2537`/`$2575`/`$25B3` and
`$25F5`/`$2633`/`$2671`) for the whole round; the goon used six
(`$2423`/`$2461`/`$249F`, `$2427`/`$2465`/`$24A3`, `$242B`/`$2469`/`$24A7`,
`$242F`/`$246D`/`$24AB`, `$252F`/`$256D`/`$25AB`,
`$25ED`/`$262B`/`$2669`). That is a real asymmetry between the two sprites,
confirmed across a full round of both fighters walking both directions
extensively, not a sampling gap -- though it is only as good as the one
recording it came from.

### The fix, first attempt: walk each fighter's own table

The first working version computed the *actual* delta a shove applied --
`old - new` for the player's floor, `new - old` for the goon's `$6F`
resistance, since either can clamp the nominal amount down -- and walked
that same delta across each struck fighter's own table: `PLAYER_TABLE` on
a player hit, the goon's own six addresses on a goon hit. Generated from
source with `tools/asm.py` rather than by hand, per the project's own
standing lesson about hand-assembling branches, and verified by replaying
the same session two ways at once: tapping every write inside
`$2300`-`$2700` in a narrow window around a real shove showed every one of
the struck fighter's table entries change by the shove's own delta, on the
exact frame of the hit.

It was still wrong, in a way the verification above could not catch,
because the verification was only checking that the *struck* fighter's
own drawn position matched its own logical one -- which it did, perfectly.
The question it never asked was whether the struck fighter's own position
is what should be moving on screen at all.

### The player's own position is not what moves

The correction came from a person watching the actual game, not from
disassembly, and it took several rounds to land because every piece of
evidence gathered *before* asking them pointed the wrong way convincingly
enough to keep being trusted.

The claim, stated plainly: in combat, the player's own drawn position is
*pinned*. Holding a direction does not move the player's sprite -- it moves
the goon and the background, in the opposite direction, and the player only
becomes free to move on screen once the level's own edge is reached and
there is no more world left to pan. "Camera" is the wrong word for this --
there is one screen, not a scrolling one -- but the effect is the same
shape: the player is the fixed point everything else is drawn relative to.

Three separate, independent pieces of static and dynamic evidence back
this once it was known to look for:

- `$186D` took **zero writes** across a 500-frame window of continuously
  held input in a fight. Not a small number -- zero. Whatever holding a
  direction does in combat, it does not touch the player's own coordinate.
- Bracketing one isolated walk-tick and one isolated hit at exact,
  machine-stop-verified frames and diffing the raw screen pixels before and
  after showed the player's own sprite in the *identical* screen position
  both times, down to the pixel, while other things moved.
- The fighting-stance decoder (`$784A`, 49 bytes) and the open-walking
  decoder are different code for a reason established much earlier in this
  project -- left-with-no-button means something different in each. It was
  never a strong leap that "walking" in the two stances might not mean the
  same thing to the renderer either; it just took being told to go check.

That reframes the whole knockback question. A hit landing on the *player*
needs to move the same thing ordinary combat movement moves -- the world,
including the goon -- not the player's own sprite, which was never
free-standing screen state to begin with.

### Two more wrong shapes before this one

**Second shape: move the world for every hit, whoever was struck.** Have
both `SHOVE_A` (goon struck the player) and `SHOVE_B` (player struck the
goon) run the identical routine -- `$188C`'s `$6F`-resistance plus the full
table walk -- since a hit, either direction, is "world-relative." Wrong,
caught the same way as the first shape: watching it play, a hit landing on
the *goon* now shifted the world out from under the still-pinned player,
reading as the player sliding sideways every time the goon was struck --
the identical illusion the player-hit fix was built to remove, just from
the other trigger.

**Third shape, and the one that shipped: split which fighter's hit moves
which table.** A hit on the *player* still moves `$188C` plus the full
table (the goon's own body and the background both react, since the whole
world is supposed to shift). A hit on the *goon* moves `$188C` plus **only
the goon's own body** -- `GOON_OWN_TABLE`, six addresses -- confirmed to be
the goon's own two zone-bands by reading the actual display-list record
behind each one (`probes/ramdump.lua`, four bytes ending at the X-field
already being synced): one family's X-field read `$188C` exactly, at the
moment of the dump; its companion, a few pixels off, is the natural offset
between two zone-bands of one stance, the same shape the original
player/goon sprite identification found much earlier in this project.

Both routines now share one implementation, `_shove_world_src`, called
twice with a different stock-hit address and a different table argument --
`GOON_TABLE` (all eighteen addresses known at the time) for a player hit,
`GOON_OWN_TABLE` (six) for a goon hit. `SHOVE_B` shrank from a full
~207-byte routine to nothing extra at all once it stopped needing its own
copy of the resistance arithmetic. Verified the same way each previous
round was: tapping every write inside `$2300`-`$2700` around one real
event of each kind and checking, on the exact frame, which table moved and
which did not -- a player hit moves all eighteen tracked addresses, a goon
hit moves exactly six, checked on real recordings, not asserted from the
code.

`SHOVE_A`/`SHOVE_B` moved again during this round, to `$A9C0`/`$AAA0` --
the earlier `$AA20` for `SHOVE_B` was sized for the smaller, first-shape
routine and stopped being far enough away once both routines briefly
carried the full eighteen-address walk.

The free zero page byte used to hold the delta between the store and the
sync loop is `$F9`, per the "Free" list under RAM in `docs/karateka-map.md`
-- `$D7` and `$DC`, the other two candidates that list names, are already
claimed by the loop-gate fixes.

### The real foreground pillars, and what actually drives them

`GOON_TABLE`, at the point the third shape shipped, covered eighteen
addresses across two sub-tables: the goon's own body (six) and four small
`$D0FB`-family accent tiles (twelve) that had been called "the world" or
"the pillars" in earlier notes on nothing stronger than "they respond to
`$188C` and sit at plausible-looking positions." A person playing pointed
out the actual pillars visible on screen are tall, narrow, and vertically
striped -- not what either of those objects render as -- and that stacking
a *vertically*-striped tile down the display list, over and over, is
exactly what would produce a tall pillar out of many short repeats. That
was the correct object, and it was not in any table this project had built.

**Finding it took two more false leads before the real one.** A
short-and-wide static object (`$F005`/`$F01D`, width 24, the widest
graphic found) looked briefly like a candidate purely because it sat near
the other tracked addresses; direct address tracing (corrected once,
after an early pass accidentally traced a record's *start* address instead
of its X-field three bytes in) showed it is set once at encounter setup
and never touched again across two entire recordings on two different
builds -- genuinely static set dressing, unrelated to the pillars. Then,
having correctly identified the real pillar graphic (`$D0F8`/`$C8F8`/
`$C0F8`, width 3) by its stripe direction, the first movement check on it
-- a write-tap over a known long stretch of combat movement on a different
recording -- came back with zero hits, and was reported as "static,
confirmed twice, probably fixed set dressing by original design." That
was wrong, and specifically wrong in an instructive way: a person playing
pointed at two frame ranges where they had watched the pillars move on
screen (having built `watch.bat`'s frame-counter overlay and a half-speed
option for exactly this kind of precise pointing), and warned that a
probe "can go silent due to DMA" -- meaning: do not trust an absence of
write-tap hits as proof of no writes; check the actual RAM state directly.

Direct RAM dumps at the frames named, walked with `tools/dlwalk.py` and
diffed, found real movement immediately: two columns, mapped to 32
addresses total (20 at one X, 12 at the other, `probes/ramdump.lua` +
grouping by graphics pointer and X-field), both genuinely moving, by a
lot, across both named windows. Re-running the *original* write-tap
against this exact recording and window then caught every write cleanly,
at `$660A` inside `$65C8` -- the same mechanism as everything else in this
document. So the write-tap was never blind; the DMA warning, while the
right instinct to force a re-check with, was not actually the mechanism
this time -- the first test had simply used a window on a different
recording that never happened to trigger this particular movement. That
distinction matters for how much to trust a "zero hits" result generally:
a write-tap over a window that never exercises the code in question and a
write-tap that is genuinely blind to the write produce an identical
symptom, and only checking the same event a second, independent way tells
them apart.

**What was found, once real data was in hand:** the 32 addresses split
into two groups by delta, cleanly, on every event checked -- a "near"
column of 20 (`PILLAR_NEAR`, not yet a named constant in the source) that
moves at almost exactly **twice** the delta a "far" column of 12
(`PILLAR_FAR`) moves, both keyed to `$188C`. That is real parallax --
two depth layers, correctly implemented in the original game -- confirmed
inside a single interpreter dispatch: one call to `$90A0` (the goon's own
`$186D`-shaped apply, `LDA $188C / CLC / ADC $AA / STA $188C`) is preceded
by a run of `JSR $65C8` calls that, in that one tick, walk the goon's own
body, the four `$D0FB` accents, and both pillar columns together -- the
near column advanced by exactly double the delta of everything else in
the same tick, not across two ticks.

**Both pillar columns are in the shove fix now.** `PILLAR_FAR` (twelve
addresses) walks at the normal delta, in the same 1x list as `GOON_TABLE`;
`PILLAR_NEAR` (twenty addresses) walks at exactly double it, via a second
loop in `_shove_world_src` (`tables_2x`) that adds the delta to each
address twice rather than precomputing `2*delta` into a second scratch
byte -- there is only the one confirmed-free zero page cell (`$F9`), and
doubling by adding twice, with an explicit `CLC` before each `ADC`, avoids
needing a second one. Both groups are wired into the player-hit call only
(`HIT_A`); a goon hit still passes neither. `SHOVE_A` grew to roughly 555
bytes carrying all of it and moved again, to `$A9C0`-`$AC00`, with `SHOVE_B`
following at `$AC00`. Verified the same way as every other round: tapping
one real player-hit event and checking, on the exact frame, that all
fifty addresses moved -- forty-two of them by the shove's own delta, the
twenty in `PILLAR_NEAR` by exactly double it.

### What arms $90A0's cascade: found

Two earlier rounds of this investigation guessed at this from correlation
and got it wrong both times -- see "The real foreground pillars" above.
The tie was broken by finding the actual arming mechanism rather than
reasoning about co-occurring events one more time.

`$AA` gets armed by a small self-reinstalling chain: one link sets `$AA`
to a small magnitude (2, sometimes 4), calls a footstep/draw word, then
reinstalls the *next* link into `$1878` -- **the player's own dispatch
slot**, not the goon's -- roughly every 13 frames. Checked directly
against the one tick already traced with per-write evidence: at that exact
frame, the goon's own slot (`$1897`) held its ordinary AI dispatch word,
not the parallel chain that also exists there (`$83C6`/`$83E2`/`$83FA` --
the goon has its own copy of the identical mechanism, which is exactly why
the two were so easy to conflate from correlation alone). So for the one
event with hard evidence, goon AI was not even active; the cascade came
from the player's slot by itself.

Climbing one level further: the chain's first link is installed by
`w_7898`, the player's own command dispatcher -- already named earlier in
this project, now actually read. It tests the current command number 1-7
in sequence and installs a different behaviour word into `$1878` for each.

**Commands 3 and 5 are the directional pair, not one command.** An earlier
pass through this same climb misread a decompile started two bytes off
true and reported "command 3" as the whole story; re-checked properly,
`w_7898` installs `$7234` for command 3 and `$727A` for command 5, and
they are mirror images: `$7234`'s *second* link (`$7244`) sets `$AA=-2`,
while `$727A` sets `$AA=+2` in its very first link. Left and right,
each its own command number, each the head of an identical
self-reinstalling chain.

**And the input state behind it is found too.** `$08`/`$09` -- read
directly by the fighting-stance decoder (`$784A`) much earlier in this
project, and assumed there to be "raw stick bits" -- are not stick bits at
all. Per this project's own memory map, `$0000`-`$001F` is TIA, and `$08`/
`$09` are `INPT0`/`INPT1`, the two fire-button lines: bit 7 reads 1
released, 0 held. Capturing them alongside every write to the player's
slot during a live window where command 5's chain was firing continuously
showed `$08=$00` (button 1 held) and `$09=$80` (button 2 released), both
constant for the entire span, alongside `a2=1` (a direction held) -- the
exact combination asked for: **button 1 held together with a direction**
is the input state present, without exception, every time this chain
fires. That also lines up with this project's much earlier note that
stock open-walking's own continuation test polls "button 2 plus
direction" -- the same shape of gesture, a different button, for a
different stance.

### Which way $AA actually pushes: confirmed

One piece was still an assumption rather than a measurement: does a
positive `$AA` push right and a negative one push left, matching the
stick, or is it inverted (a "camera pans opposite the walk direction"
scheme)? A new combined probe (`probes/tracedir.lua`) sampled
`$186D`/`$188C`/`$AA`/`SWCHA`/`$A2` from one frame counter in one pass,
replayed against `karateka-31-knockback-light.a78` with its matching
`.inp`, removing all the earlier cross-log guesswork about whether two
logs even came from the same recording.

The result is unambiguous and holds across many consecutive samples in
both directions, with the player's own `$186D` pinned at 38 throughout:

```
frame playerX goonX AA AAsigned swcha a2
1316  38      63    254 -2      $BF   255   <- left held
1329  38      61    254 -2      $BF   255
1342  38      61    254 -2      $BF   255
1355  38      59    254 -2      $BF   255   <- goonX falling, 2/tick

1602  38      70    2   2       $7F   1     <- right held
1628  38      72    2   2       $7F   1
1641  38      74    253 -3      $7F   1
1654  38      77    2   2       $7F   1     <- goonX rising, 2/tick
```

`$BF` (bit 6 clear) is left on `SWCHA`; `$7F` (bit 7 clear) is right.
Left held gives `$AA=-2` and `$188C` counts down; right held gives
`$AA=+2` and `$188C` counts up. Combined with the pixel measurement
from earlier this session (increasing raw X moves the goon's drawn
sprite rightward on screen), the mechanism is the plain, unsurprising
one: **positive `$AA` pushes everything it's applied to right, negative
pushes left, and it always matches the stick's own direction** -- there
is no inversion, no "camera" convention where holding left scrolls the
world right. Holding left makes the whole tracked world (goon,
`WORLD_TABLE`, both pillar columns) drift left together with the input;
holding right, right. (Individual frames can still show a value that
looks like it breaks the pattern -- e.g. frame 1080-1106 above the
sampled table -- because more than one state machine can write `$AA`
within a single frame, as `trace_aa_setter.log` shows directly; a
single end-of-frame sample only ever sees the last write. The pattern
is read from runs of consecutive, uncontested frames, not any one
sample in isolation.)

### How the stage is laid out, and why the pillars drifted

Chasing a report that the foreground pillars "slide in from the left when
they should always be at the rightmost section of the stage" turned up
the answer to a question this project had been circling for a while: how
the level is laid out, and what defines its left and right ends.

The answer is that **there is no level-layout table and no world index**
for the background. A write-tap over `$2000`-`$2800` across a 676k-frame
session, tagged with the PC of every write, found exactly two game-side
placement writers -- `$59A0` (fill-with-constant-record) and `$58CE`, the
tail of the display-list builder at `$58A4` that blits four-byte MARIA
headers off the Forth stack. Across 217 placements each:

```
PILLAR_FAR  head $2379 -> 160 ($A0)   217 of 217
PILLAR_NEAR head $229D -> 175 ($AF)   217 of 217
WORLD_TABLE head $2423 ->   0         217 of 217
```

Always the same value, never varying. Placement is a constant scene reset
once per encounter, and from there **the display-list X byte is the
position of record** -- the only state there is. Confirmed on the stock
ROM too: with its own recording, `$2379` sits at exactly 160 from each
reset to the next and the scroll primitive never touches it.

The fighters are the opposite case, and the same scan shows it: `$252F`
and `$2537` take 30732 and 1748 writes from that same builder, redrawn
every frame from `$188C`/`$186D`. Which means the shove's writes to
`GOON_OWN_TABLE` and `PLAYER_TABLE` are cosmetic -- overwritten on the
next frame -- and only its `$188C` write actually persists. The pillars
and background tiles are the reverse: nothing rebuilds them, so a write
there is permanent until the next encounter.

**What defines the ends of the stage is therefore nothing direct.** There
is no `CMP` against any pillar address, and none against `$188C` either.
The limit is implicit: the world only moves when the walk cascade scrolls
it, and the *walk* is what carries a bound (`$A488` tests `$186D` against
`#$A0`, handing off to `$A490`). The background inherits its range from
the walk because the walk is the only thing that ever moves it.

A knockback shove injected at hit time skips the walk, and so inherits no
bound at all. Traced on a real session, the far pillar's head walked
160 -> 196 over nineteen hits without one scroll event between them:

```
861  $2379 160 162 $AA94   <- the shove
     ... nineteen of these, no $660A anywhere ...
4150 $2379 194 196 $AA94
4422 $2379 196 195 $660A   <- the game finally scrolls, from 36px off
```

Nothing reconciles that until the next encounter's placement. Pushed far
enough past MARIA's 160-pixel window the pillar wraps and re-enters from
the left -- the reported symptom exactly.

Two things this ruled *out* along the way, both hypotheses this
investigation had been running on. First, the table list was never
incomplete: filtering that same 676k-frame log for the scroll primitive's
own store (`$660A`) yields 56 distinct addresses, and the patch's five
table lists are those same 56, exact match, no gaps either direction.
Second, the parallax ratios were never wrong: net deltas in one real
scroll event are player 0, goon -4, world -4, `PILLAR_FAR` -4,
`PILLAR_NEAR` -8. The 1x/2x split is correct.

**Fix 37 is the bound**, kept separate from fix 31 so the two can be
played against each other. It caps the delta at `$A0 - $2379`: whichever
is smaller, what the `$6F` resistance wants or the headroom left before
the pillars are back at their placed home. At home the hit is absorbed --
the same graceful nothing the exit-side resistance already does. One test
covers both columns, because `PILLAR_NEAR` sits at double the offset from
its own home under both movers, making its half-headroom identical to the
far column's -- which matters when there is one free zero page byte.

**What is not established:** whether the game legitimately scrolls the
pillars *above* `$A0` when the player walks the other way. The stock
recording never scrolls them at all, so it could not answer this, and the
patched log cannot -- its first `$660A` write already starts from 196,
downstream of the drift. If walking left does legitimately push past home,
fix 37 goes inert in that state rather than misbehaving, but it would be
a real limitation and only play will show it. This is the same trap noted
above with the write-tap: data that looks like an answer but was taken
under conditions that could not produce one.

**What is still open:** whether button 1 is *required* (a negative
control -- a window with the direction held and the button released, or
vice versa -- has not been captured to confirm the chain does not also
fire without it, only that it always has so far when it did fire), and,
possibly the same root cause as the arming question, what un-pins the
player later in one long recording -- not any of `$18DC`, stance, or the
current slot-4 word, all unchanged across every transition traced so far.

### The kick uses the same $AA, and the goon's own knockback now calls its real applier

The player's kick (commands 6 and 7 in `w_7898`, the two dispatcher
entries that also write `$187A`) turned out to arm `$AA` exactly the
same way the walk commands do -- `$AA=+1` for command 6, `$AA=-3` for
command 7, through an identical self-reinstalling chain link. The
difference is what bounds it: the walk chain re-arms indefinitely for as
long as a direction is held, while the kick layers a bounded
animation-phase counter (`$191A`, stepping through phase words
`$76DE`/`$76F4`/`$770A`, converging back through `$7670`) that runs a
fixed sequence and stops -- which is why a kick pushes the world out and
springs it back instead of leaving a permanent step. Same primitive,
different arming shape.

That raised the obvious question: could the shove call the game's own
applier instead of hand-walking a table? Traced the goon's applier
(`$9062`, called from seven places, always ending in `$188C` then
`$189C`/`$189E`/`$18A0`/`$18A2`/`$18A4`) all the way through:

- `$9062` only ever covers the goon's own six addresses -- never
  `WORLD_TABLE`, never either pillar column. Confirmed by finding every
  caller of `$90A0` in the ROM: there are none outside `$9062` itself.
  So it can only replace the `HIT_B` (goon knocked back) side of the
  shove; `HIT_A` (goon hits player, world moves) still needs the
  explicit table walk, because no single native routine bundles
  goon-plus-world together.
- The five extra cells it touches turned out to be a non-issue rather
  than a gap: `docs/karateka-map.md`'s "body points"/"striking limbs"
  entry already establishes they're rebuilt fresh every frame from the
  base position, so they self-heal within one frame regardless of
  whether a hand-rolled shove touches them.
- Calling `$9062` cold, from injected code outside its normal call
  context, looked risky at first (an "ambient base pointer" a caller
  might need to arrange), but the concern didn't survive a full read of
  `$65C8`: the constant it adds (`$2224`) is baked into `$65C8`'s own
  bytes, combined with `$9062`'s own compile-time literals. The
  high-byte cell that looked like it might carry caller state is just
  the top half of a small literal push, always zero by the ordinary
  Forth convention. Checked all seven real callers directly -- none of
  them sets up anything beyond saving (and sometimes negating) `$AA`.

So `HIT_B`'s shove (`_shove_native_src` in `patches/karateka.py`) now
computes the same `$6F`-graduated clamp as before but, instead of
writing `$188C` itself and walking `GOON_OWN_TABLE` by hand, stores the
clamped delta into `$AA` and calls `$9062` directly -- one authoritative
write instead of two. Verified against the real recording: the full
8,396-frame replay completes without desyncing (the load-bearing check --
a corrupted stack pointer would show up as replay divergence long before
the end), and a write-tap confirms `$188C` is now written exclusively
from `$90A0` (324 times across the session, organic AI walks included),
with zero writes from inside `SHOVE_B`'s own code. `HIT_A` is unchanged
and still uses the explicit table.

### The player-hit side, and how five wrong answers got to fix 39

`HIT_A` -- the goon hits the player, so the world moves -- took much
longer, and every wrong turn came from the same root: writing display-list
addresses by hand instead of asking the game to move them.

**Fix 37 bounded the shove against the pillars' placed home and did
nothing at all.** The bound was `$A0`, and `$A0` is also the value every
scene places `PILLAR_FAR` at (217 of 217 placements). So the headroom was
`160 - 160 = 0` from the first hit of every fight and the shove was
absorbed every time. Broken by construction, not by tuning.

**Fix 38 bounded our own cumulative contribution instead**, in two free
zero-page bytes found the same way `$D7`/`$DC`/`$F9` were (named by no
instruction in any zero-page addressing mode). That worked as designed --
the drift accumulator capped at 48 and stopped -- but it was bounding a
hand-written shove that should not have existed.

**Then three attempts to use the game's own machinery, all of which moved
the goon and nothing else.** Setting `$AA` alone; reinstalling the walk
chain minus its footstep calls; repeating that chain six times to fake a
held direction. Every one routed through satellite `$9110`, which calls
`$9062` and `$8ECC` and *no world mover at all*. The pillars were never
reachable that way, which is why all three failed identically.

**What actually worked was tracing backwards.** Instead of reading forward
from the command dispatcher hoping to arrive somewhere, take a live pillar
write and walk up its call path: the write lands at `$660A` inside
`$65C8`, reached from `$8EA2`, reached from `$914C` -- a satellite. Reading
*that* satellite showed the full set it calls, and `$8E88`/`$8E66` fell out
immediately. Forward reading had failed at this for several sessions; one
backwards trace answered it in a single run. Worth remembering as a
technique when a call path resists static search.

Fix 39 is the result: set `$AA` and call the game's own walkers. See "What
moves the world" in `docs/karateka-map.md` for the routine map, the
`$65C8` address formula, and the two inversions it is easy to get wrong
(the satellite negates `$AA`, and movement is unit-stepped rather than
passed as a magnitude) -- both of which this got wrong first, sending the
world the wrong way.

**Which walkers to call is the whole design**, and it was settled by
running each alone and diffing what it wrote. `$9062` and `$8F3E` are
called; `$8E88` (both pillar columns) and `$8E66` (the cliff set) are
gated on whether their own column has already scrolled into view, tested
against `$A0`. Held past the edge, a far actor is left where the level put
it -- walking drags it in because walking is how you travel toward it, and
a knockback is not travel. Already in view, it travels with everything
else, because standing still would slide it against its surroundings.
Measured: of 24 hits, 23 left the pillars parked and the one that landed
after walking had carried the column to 158 moved it. Confirmed in play.

**The last bug was the `$8F3E` fall-through**, and it is documented in the
map because it is a property of the ROM rather than of this fix: `$8F3E`
walks rows 5-7 *and* rows 8-10, so calling it beside `$8ECC` walked the
bottom twice per step and the top once. On level 3, where both rows hold
content, that sheared the tall background pillars into halves moving at
2:1 -- reported from play as "the top pillars appear to be moving at half
the rate," which is a more precise bug report than any trace produced.
`$8F3E` alone: the same window remeasures at top=40, bottom=40, zero
unequal frames.

The result is 113 bytes and names no display-list address anywhere, so it
covers whatever a given level actually has -- which is the point, since
the hand-built lists were level 1's and level 3's geometry is not level
1's.

---

### Fix 41: putting the princess's kick back

The ending in this port works -- see "The ending" in
`docs/karateka-map.md` -- but it is unconditional. `w_A158` stages the
embrace and installs `w_8BA6`'s celebration loop without ever consulting
the player's stance, so walking into the final hall still in fighting
stance is greeted exactly like walking in unarmed. The Apple II original
does not permit that.

Two problems had to be solved, and both were got wrong first -- the
stance one twice.

**Finding a stance to read.** Reading `$187C` at the ending can never
fire: the player always arrives walking. Every stage start zeroes it
outright -- `STY $187C` at `$A503`, plain 6502 -- and the ending is not
staged until long after. Reading it at the *final goon's death* fails the
other way: you fight the last guard in stance by definition, so that
snapshot is 1 on every run and the kick would always fire. What works is
standing in front of the writes. `$187C` is written from three places in
the entire ROM -- that `STY`, and two Forth cells naming `p_5280`,
`$7226` reached with a literal 1 on the stack and `$726A` with a literal
0. Those two *are* the stance control, one per direction; repointing them
at a word of ours sees every stance the player ever chooses and nothing
else.

**Which value to keep, and the inversion that cost a play session.** The
first version snapshotted the outgoing contents:

```
$B032  LDA $187C / STA $E7          ; WRONG: what is about to be overwritten
       JMP $5282
```

That is the stance the player is *leaving*, which after any real stance
change is the opposite of the one they just took. Logging `$187C` and
`$E7` together over a whole traced run shows it invert on every single
transition -- `$E7` reads 1 through every stretch spent walking and 0
through every stretch spent in stance. The recorded playthrough drops out
of stance and walks off stage 6, and it *armed the kick*; a player who
does the wrong thing and strides in still fighting gets `$E7` = 0 and the
embrace. Reported as "the princess ending still does not work... it
really looks like it is stuck as a static screen".

`p_5280` is `LDA $02,X / STA ($00,X)`: address on top of the stack, value
one cell below it. So the value being taken is at `$02,X`, and keeping
that instead is the whole fix:

```
$B032  LDA $02,X / STA $E7          ; keep the stance being selected
       JMP $5282                    ; then p_5280's own code, as before
```

`$E7` is free zero page, checked the way `$D7`/`$DC`/`$F9` were. Nothing
resets it at a stage boundary, so it means "the last stance you chose",
not "the last stance you chose in this hall". That is deliberate and it
is the rule rather than a shortcut: clearing the final guard needs
fighting stance, so every real run arrives with `$E7` = 1 unless the
player deliberately drops out of stance before walking through the last
door -- which is the thing the original makes you do.

**Landing a hit that means anything.** Three attempts failed, each
differently, and the reasons are worth keeping:

- `$7A30` is a spark placer -- a graphic and a coordinate, then `RTS`.
  Which is exactly why the knockback fixes had to add movement of their
  own. A spark drawn at the ending is painted over by the embrace in the
  same breath.
- `$6A9C`, the player's own spark, is no better for the same reason.
- `$8CA8`, the bird's *whole* hit, sets both collision masks -- but the
  ending parks the no-op `w_9908` in the player's slot and the collision
  resolver's, so `$18C0` sits at `$F0` unread. `$18DC` is non-zero by
  then too, which discards the hit at `$6702` regardless.

The answer was the rule itself. `$18BF` is the player's health, and at
`$6932` the game asks the stance before spending it:

```
6932  LDA $187C / BNE $693C     ; fighting: decrement normally
6937  LDA #$01 / STA $18BF      ; walking: health := 1
693C  DEC $18BF                 ; -> 0
6941  install $7750 into $1878  ; the death
```

"Killed by a hit in walking stance" *is* force-to-1-then-decrement, and
`$6937` is its entry. So the armed path parks the no-op in the goon's
slot -- no celebration over a corpse -- and jumps there, letting the game
kill the player with its own code:

```
$B002  LDA #$A6 / STA $1897         ; hand the slot to w_8BA6...
       LDA #$8B / STA $1898
       LDA $E7 / BNE armed          ; ...unless they came armed
       JMP $401E
$B013  LDA #$08 / STA $1897         ; w_9908: no celebration
       LDA #$99 / STA $1898
       JMP $6937                    ; the game's own walking-stance death
```

**The animation was never the problem.** The first report against the
finished fix was that the ending "looks stuck as a static screen, maybe
animations do not work on it", which is a reasonable read of what a
player sees and is not what is happening. Sampling all nine main-loop
slots across the transition shows `w_9F2C`, the seventh hall's setup,
parking `w_9908` in slots 1, 2, 3, 6, 7 and 9 -- including `$18C7`,
`p_673A`, the display-list updater -- and leaving only `$18B1` alive.
That looks conclusive, and it is wrong: the death words write their own
display-list entries through `p_69E2`/`p_6BEC` and do not need `p_673A`
to be scheduled. Screenshots of the armed ending show the full collapse,
frame by frame, over about a second:

| frame | what is on screen |
| --- | --- |
| 8370 | the two figures standing together, as in the embrace |
| 8390 | the player buckles |
| 8400 | he goes down |
| 8410-8420 | he lies on the floor, the princess standing over him |
| 8430 | the game is gone, back to attract |

The chain runs `w_7750` -> `w_7760` -> `w_777A` -> `w_7794` -> `w_77BA`,
twelve frames each, and `w_7794` sets `$18AA` to 0 on its way past, so
the ending really does end. The lesson is the ordinary one: a slot table
says what is *scheduled*, not what can *draw*.

Verified both ways on the recording that walks off stage 6. Left alone it
now reads `$E7` = 0 and plays the stock embrace with its colour cycling,
which is the correct answer for a player who dropped out of stance. With
`$E7` forced to 1 at frame 8360 -- the same state a fighting-stance exit
produces -- it plays the collapse above. Before the fix those two
outcomes were swapped.

**This is a reconstruction, not a repair.** Nothing dormant was switched
back on. The rule comes from the original game; what is reused is this
port's own death. What it asks is also not quite the original's question
-- that one tests stance as you walk up to her, and this port gives the
player no approach to make, so the honest question it can still answer is
how the last fight was left. Fix 42 pairs it with one-hit opponents to
make the whole thing reachable in a couple of minutes.

Not verified: that the walked-in ending is byte-identical to stock frame
for frame. The celebration cycles colours every pass, so single-frame
comparisons differ regardless, and it wants an eye on it rather than a
hash.

### Fix 43: 3-3-2, so three hits are one stride

Fix 39's two pixels are a number that looks right. The game has a number
that means something: the walk stride is drawn over eight units, which is
the reason fix 22 stops at four and warns that the feet stop keeping up
past it. Eight units over three hits is 3-3-2, and then knockback and
walking are commensurate instead of drifting against each other.

Everything else is fix 39 unchanged -- the same walkers, the same inverted
sign, the same one-unit-per-call stepping, the same on-screen gate on the
far actors, and on the goon's side the same `$6F`-graduated resistance
with only the cap moving. Fix 39 itself is byte-identical after the
change; the cadence is an option on the same emitters, and with no cadence
they emit exactly what they emitted before.

The cycle position needs a byte per side. `$F7` and `$F8` are it: zero
writes across a full recorded playthrough -- six stages, both bird stages,
the ending, 9595 frames -- against `$E8`'s 3.9M as the positive control,
which is the same test and the same standard that cleared `$D7`/`$DC`/
`$F9`. Counting the two sides separately keeps an exchange of blows from
making one fighter's cycle depend on the other's. Nothing initialises
them: a garbage value fails every guard, so that one hit is short, and the
advance wraps it.

The player's side is two unconditional unit steps and a third behind one
compare, which works because the cadence never rises -- the hits that get
the extra pixel are a prefix of the cycle, so `LDA $F7 / CMP #2 / BCS` is
the whole test. The goon's side has to cap a value instead of counting
steps, so it keeps the wall's answer on the 6502 stack across the phase
arithmetic (`X` is the Forth data-stack pointer and cannot be borrowed)
and picks one of two caps on `Y`.

Measured by tapping `$2423` -- a near-world display-list head, one write
per unit step -- and `$F7` into one ordered log, which separates the
pixels belonging to a hit from the pixels belonging to walking:

```
976  near DB DC DD   PHASE 0 -> 1      +3
1003 near DE DF E0   PHASE 1 -> 2      +3
1147 near E1 E2      PHASE 2 -> 3 -> 0 +2    (the wrap)
1173 near E3 E4 E5   PHASE 0 -> 1      +3
1551 near E6 E7 E8   PHASE 1 -> 2      +3
```

The goon's side writes `$188C` once per hit rather than once per unit,
since `$9062` applies the whole delta in one `LDA/ADC/STA`, and reads +3,
+3, +2 across the same cycle.

One asymmetry worth knowing: a hit the wall absorbs entirely does not
advance the goon's cycle, because the resistance check returns before the
phase arithmetic. The cycle counts shoves, not blows.

**What neither this nor fix 39 does is touch the game's own odometer.**
Walking runs the phase machine at `$92E0`/`$924A`, which drains a
five-segment budget and steps `$18BD`; knockback calls the walkers
underneath it. See "The odometer" in `docs/karateka-map.md`.

Not played yet. The arithmetic is 8 units per 3 hits against fix 39's 6.

### Fix 44: spending the hall's travel budget

Fix 43 makes knockback commensurate with a walk *step*. It is still not
commensurate with the *hall*: it moves the scenery without telling the
game the scenery moved, so the odometer and the world drift apart and stay
apart for the rest of that hall. The game still believes it owes you the
travel you have already been shoved through, so the far actors arrive
late, the ends of the hall move, and a badly knocked-about run can walk
the scenery past where the level meant it to stop. Fix 39's note called
the missing bound a KNOWN GAP -- this is where the bound actually lives,
and it was in the game all along.

So fix 44 spends a unit of budget for every unit it shoves. What makes it
more than a decrement is that the odometer is not a counter, it is five
segments with a read head, and the game does two specific things with it
that a shove has to do too:

**Segments 0 and 4 do not move the world.** All eight dispatchers call
`$91A2` there, and `$91A2` is `JSR $901E / RTS` -- the player's body and
nothing else. That is how a hall's ends work: the background is pinned and
the player crosses the screen. It is also the one satellite that skips the
`$AA` negation, so `$901E` takes the intent (-1, leftward) rather than the
effect. A knockback in those segments therefore slides the player, which
is both what the game does there and what a knockback ought to look like.

**The hall's left end is a real limit.** Phase 0 with `$18B3` at zero is
as far left as the world goes. The game's answer there is
`if $18DC == 0: JMP $A490` -- a screen transition, and a `JMP`, so it does
not return. Fine from the walk chain, catastrophic from inside a hit
handler. Fix 44 stops instead.

Phases 1-3 are fix 43 unchanged: `$9062`, `$8F3E`, and the far movers
behind the on-screen gate. The gate is standing in for the phase rule --
each machine calls exactly one far mover, at phase 1 or 3 depending on
which machine is live, and which machine is live has no static answer.
"Move it if it is already visible" reaches the same place from the other
side and does not need to know.

One implementation note worth keeping: indexing the pairs wants a register,
`X` is the Forth data-stack pointer and cannot be borrowed, so `Y` carries
phase*2 -- which rules out `INC`/`DEC`, because the 6502 has those in
abs,X only. Load-modify-store through `LDA abs,Y` does the same work in
four more bytes.

Traced three ways against a recorded fight:

```
        ph  b2  a2   near  cycle
f976     2  98  82    DA     0     before
f977     2  95  85    DD     1     hit: -3 budget, +3 world
f1004    2  92  88    E0     2     hit: -3 budget, +3 world
f1148    2  90  90    E2     0     hit: -2 budget, +2 world  (the short one)
```

The five pairs sum to 370 on every frame of hall 1 and never move off it,
which is the conservation check. Then, poking the odometer to each edge
just before a hit:

- parked in segment 0 with budget: the hit spent 3, moved `$2537` (the
  player's body) by 3, and left `$2423` (the world) exactly where it was
- two units left in segment 2 with 1 and 0 empty: a three-unit hit moved
  the world two, stepped the phase down to 0, found nothing, and stopped

Played and confirmed, the segment-0 behaviour included: at a hall's far
left a hit slides the player and leaves the scenery standing, which is
what the stock walk does there.

Fix 44 is built on the stock cadence, which option 6 below says is the
wrong place for knockback to live. Fix 45 is where it goes.

### Fixes 45-47: the unofficial build

Not a new mechanism -- a composite, and the one the ranked list has been
asking for since option 6 was written. Option 6 says knockback belongs on
a faster loop and never on the stock cadence, and the reason is structural
rather than aesthetic: a shove makes distance, and closing distance at 13
frames a decision is precisely the slog knockback exists to relieve. Every
knockback fix until now -- 31, 37, 38, 39, 43, 44 -- was built on the
stock cadence, because each was made to answer a question about knockback
rather than to be played.

Five parts, none of which shares an address with any other:

| part | what it is |
|---|---|
| fix 29 | six-frame loop, the player deciding every round |
| fix 32 | the slot-8 gate: skip the eighth wait when the opponent has nothing queued |
| fix 44 | 3-3-2 knockback spending the hall's travel budget |
| fix 11 | the controls remapped -- bare stick walks, buttons punch and kick at three heights |
| fix 41 | the princess's kick, the rule this port left out of its ending |
| fix 26 | held strikes at 3-1-1 instead of evenly spaced |

Reactions every ~5 frames against animation every ~10, knockback of eight
units over three hits, and an ending that finally asks how you walked in.

`karateka-46-tweak-brisk-walk` and `-47-tweak-fast-walk` add fix 21's
three-unit and fix 22's four-unit walking step. Fix 22's own warning still
applies to 47 -- the stride is drawn for eight units a cycle, so that is
where the feet may stop keeping up with the ground -- which is why the two
are built side by side rather than one replacing the other.

**The hit window is deliberately left stock.** Fixes 5 and 6 even the two
fighters' reach up, and that is the one knob in this list that changes who
wins rather than how the game feels. It wants judging on its own, on top
of a build whose feel has already settled.

**One interaction worth recording** because it is the kind that `--check`
explicitly cannot catch: fix 11 and fix 41 both have opinions about
stance. They do not collide, and the reason is that the remap changes
which *input* produces the stance command rather than changing the
command, so `$7226` and `$726A` are still the only path to `$187C` and fix
41's snapshot still sees every stance the player picks. Fix 9 was
withdrawn for exactly this class of clash -- two fixes writing different
bytes and still fighting -- so it is checked rather than assumed.

Two byte-level notes from building it. `--check` classifies by knob, and a
composite declares `parts` rather than `governs`; declaring `governs` on
these three made the checker call 34 perfectly legitimate alternative
pairs CONFLICT. Declaring `parts` also makes it verify the composite
really contains what it names, which it now does for all six parts. And
fix 26's table is `04 02 02`, not the `06 02 02` its note claimed: an
entry of *n* runs for *n*-1 calls because the counter starts at 1, so the
pattern is 3-1-1. The off-by-one was found long ago and the correction
reached the docstring without reaching the note.

### Fixes 37 and 38: withdrawn, and where the bound really was

Both tried to stop the world drifting under repeated knockback, and both
are superseded by fix 44 obtaining the same limit from the game itself.

Fix 37 bounded the shove at the pillars' home, `$A0`. That was broken by
construction: `$A0` is also PILLAR_FAR's *placement* value, so every fight
starts with zero headroom and the bound never does anything. Fix 38
replaced it with a private drift cap in `$D2`/`$D3`, on the reasoning that
nothing in the ROM bounds `$188C` -- which was true, and confirmed by a
full scan finding zero compare instructions against it anywhere.

The reasoning was sound and the conclusion was wrong, in an instructive
way: the game does bound travel, just not by comparing a position. It
bounds it by *budget*. `$18B3`-`$18BC` hold how far the hall can still
travel in each direction, and running out is the limit. Fix 44 spends that
budget and gets the bound for free, along with everything else that stays
in sync as a result. Withdrawing 37 and 38 also hands `$D2`/`$D3` back to
the free-zero-page pool.

The lesson generalises past this fix: a scan that finds no compare against
an address is evidence that the constraint is not expressed as a compare,
not evidence that there is no constraint.

### The signature, which every build until now got wrong

Forty-odd builds, every one of which would have booted a real NTSC 7800
into 2600 mode. The console hashes the cartridge and checks a signature
over that hash; if it fails it starts as the wrong machine rather than
refusing, so there is no error to notice. PAL consoles do not check, and
no emulator does, which is the whole reason this survived so long: the
builds worked in every place they were tested.

Fix 1's own notes came within one byte of catching it. Its routines were
originally placed at `$FF80` and `$FFA0`, and the comment explaining the
move to `$FF40` says plainly that `$FF80`-`$FFF9` "holds high-entropy data
sitting immediately below the 6502 vectors, which on a 7800 is where the
BIOS signature block lives", and that "emulators do not verify it, so
every build so far ran". That is the right observation and it stops one
step short: not clobbering the signature is necessary and nothing like
sufficient, because the signature is over the cartridge, and Karateka
hashes all 48K of it. Every fix invalidates it.

`tools/sign7800.py` is a port of Bruce Tomlin's `sign7800.c`. There is no
C compiler in this environment, which turned out to be the better
outcome: the C carries a hand-written bignum library with fifteen 128-byte
registers and 250 lines of `multiply`/`divide`/`hashpower`, and once you
see what it is computing, all of that is `pow()`. The scheme is Rabin --
public exponent 2 -- so verifying is one squaring, and signing is a square
root mod each prime plus a CRT lift, with byte 4 of the hash stepped until
the value is a quadratic residue. The permutation table and the hash are
reproduced exactly; the arithmetic is not.

The port was checked before it was trusted, in both directions:

```
karateka-original.bin                          valid
Commando (NTSC) (Atari) (1989) (CD1A98C5).a78  valid
karateka-45-tweak.bin                          INVALID
```

A stock dump of a different game from a different year is the control that
matters -- an implementation that only agreed with Karateka could be
agreeing with a coincidence. Signing then round-trips: the signed build
verifies, and diffing it against the unsigned one shows exactly 120 bytes
changed, all of them in `$FF80`-`$FFF7`.

Wired into every path that emits a cartridge. `--build` and `--doses` sign
the finished image *before* writing the `.bin`, the `.a78` and the `.bps`,
so the patch file carries the signature and applying it yields a signed
ROM. Signing happens after the `Patcher` is finished, so the 120 bytes are
not in `p.writes` and `--check` still compares only what the fixes
themselves wrote -- otherwise every pair of fixes would appear to conflict
over the signature block.

The `.abp` bundle is the case that cannot be precomputed. The signature is
over the whole image, so each of the many combinations of options has a
different one and there is nothing to ship. `patchset.py apply` computes
it at apply time and prints what it did.

All 51 numbered builds verify. The twenty-odd `test-*`, `screen*` and
`t-*` cartridges in the same directory do not, and are left alone: they
are probe builds from earlier investigation that only ever run under an
emulator.

**The general lesson, which is the same one fix 38 taught:** the absence
of a failure is not evidence of correctness when nothing in the test path
performs the check. Emulators skipping the signature is exactly as
informative as the ROM containing no compare against `$188C`.

### Fix 48: the difficulty switch, and which way round it goes

Fix 17 makes the switch *reachable*. The stock test is
`LIT $0282 C@ LIT $0080 = 0BRANCH`, and `SWCHB = $80` asks for the right
difficulty in A **while Reset, Select and Pause are all held down**, so
neither adjustment has ever run in anyone's game. Fix 17 puts `AND` where
the `=` is and bit 7 alone decides.

What fix 17 does not do -- and its own docstring says so -- is get the
sense right. On every Atari console **A is the expert position and B is
the novice one**, and the adjustment the game reaches for hands the
*player* parity: `$18C3` up, `$18C4` down, 8-or-9 against 10 becoming 9
and 9. With `AND` in that slot the adjustment runs when bit 7 is set,
which is A. So fix 17 gives an easier game in the expert position.

Inverting it needs a flag that is true when the masked bits are *clear*,
and the image has no such word. It is a small one to add, because the two
words the game's own `=` hands off to do all the awkward part already:

```
$403B   INX INX / store $FFFF into the new top     -- true
$404F   INX INX / store $0000 into the new top     -- false
```

Both pop one cell and overwrite the next, so between them they consume the
two operands and push one flag. A comparison word therefore only has to
decide which to jump to, and never has to touch `X` -- which matters,
because `X` is the Forth data-stack pointer and cannot be borrowed. And
"is the 16-bit AND zero" can be answered by testing each half and bailing
out early, so it needs no scratch byte either:

```
$B402  LDA $00,X / AND $02,X / BNE set
       LDA $01,X / AND $03,X / BNE set
       JMP $403B                          ; nothing masked in -> true
set:   JMP $404F
```

Twenty-three bytes, and the two cells at `$A0EA` and `$A102` repointed at
it. `--check` classifies 17 and 48 as two settings of the same knob, which
is what they are.

The direction of the adjustment is left alone. That is the designers'
intent and it is also the better evidence: `1+` goes to `$18C3` and `1-`
to `$18C4`, moving the windows toward each other, which is a second
independent line of argument that the smaller window is the player's.

**It is in the unofficial build** even though fixes 5 and 6 are not, and
the distinction is worth stating: 5 and 6 change the hit window outright
and for everyone, while 48 changes nothing by default. The switch decides,
and A -- where the game has effectively always been, since the test never
fired -- is the stock window.

### The PAL bundle, and the one thing that blocks half of it

The European release is the same game in four banks -- see "The PAL
release is the same game in a different box" in `docs/karateka-map.md`.
In-bank offsets are unchanged, so a fix does not need re-deriving for it;
its addresses need moving to the right bank, and that is arithmetic:

```
NTSC $4000-$7FFF  ->  PAL bank 2, file offset + $8000
NTSC $8000-$BFFF  ->  PAL bank 0, file offset - $4000
NTSC $C000-$FFFF  ->  PAL bank 3, file offset + $4000
```

`patches/karateka-pal.py` applies each fix to the NTSC image exactly as
`karateka.py` builds it and translates the bytes it wrote, so nothing is
re-implemented and nothing can drift. Every fix is then checked byte by
byte against the PAL image before being accepted, which sorts them into
three groups:

| | fixes | |
|---|---|---|
| identical | 12 | every byte the fix expects is the byte PAL has |
| free-space fill | 16 | the only disagreement is `$00` against `$FF` |
| blocked | 20 | the fix writes over code PAL actually changed |

**All twenty blocked fixes are blocked by one write.** Fix 1's seven-byte
hook at `$5945` sits inside `$58EA`-`$5956`, the 50 Hz retime of the NMI
handler and the one piece of logic Europe rewrote:

```
NTSC $5945   A0 10 88 D0 FD EA EA     the delay loop the latch replaces
PAL  $5945   01 85 A8 4C 57 59 00     different code entirely
```

Everything carrying the input latch inherits it: the whole cadence family,
the remap's decoder half, and the `tweak` builds. Porting those means
finding where the PAL handler has room for the same hook -- a
reverse-engineering job on that handler, not a translation -- so it is
deliberately not guessed at.

`karateka-pal.abp` therefore ships fourteen fixes and a `pal-tweak`
composite: knockback that spends the hall's travel budget, the control
remap, the princess's kick, 3-1-1 strikes and a working difficulty switch.
No cadence work.

**The banking worry turned out to be unfounded, and it was worth
checking.** On NTSC the free-space pool at `$A6D0`-`$BFFF` is always
present because the cartridge is linear. On PAL that range is bank 0's
tail inside a *switched* window, so patch code living there could in
principle be invisible when the hook fires. Sampled from the CPU's own
view across 1081 frames of a running PAL cartridge, the fix code at
`$B300` and `$B402` and the repointed cell at `$A0EA` are mapped in **100%
of frames** -- bank 0 holds the main loop, so it is resident whenever the
game is running. The patched cartridge also boots and plays identically to
stock through the title and intro.

Two smaller notes. PAL cartridges are never signed, so nothing here signs
one -- `patchset.py` now checks the `.a78` header's TV byte and skips it,
which it did not before and which had quietly put a pointless signature on
the first PAL build. And the bundle refuses an NTSC dump outright: the
body sizes differ, 65536 against 49152, before any anchor is even
consulted.

## Ranked

### 0. Ship what exists -- no effort

`patches/karateka.py --build`, and `--check` before trusting any combination
of them: it collects the bytes every fix writes, compares each pair, and sorts
them into independent, alternatives (two settings of one knob), dependency, or
CONFLICT. A conflicting pair is not "these cannot both be applied" -- applying
both is exactly what quietly produces a ROM that is neither. Fix 1 latches the controls (a 33 ms tap is seen
46% of the time instead of 16%); fix 3 skips four of the eight waits (13-frame
loop becomes 9); fixes 5 and 6 even up the hit window and optionally add two
units of slack. **Fix 7 is 3 and 5 together; fix 8 is 3 and 6.** Those two are
the ones to play, back to back -- same cadence, and the only difference between
them is two more units of reach for both fighters. Fix 11 remaps the controls
and **fix 12 is 3, 6 and 11 together**, which is the one to play if only one
gets played.

Fix 6 on its own is the wrong pair to try: a wider window matters most when you
can get a swing in at all, and that is what fix 3 buys.

### 1. Dose control -- built

`patches/karateka.py --doses` writes nine cartridges, one per dose:

```
    dose  waits skipped        loop
     0    none                 13 fr
     1    0                    12 fr
     2    0 4                  11 fr   (this is fix 4)
     3    0 3 5                10 fr
     4    0 2 4 6               9 fr   (this is fix 3)
     5    0 2 3 5 6             8 fr
     6    0 1 3 4 5 7           7 fr
     7    0 1 2 3 5 6 7         6 fr
     8    0 1 2 3 4 5 6 7       5 fr
```

The waits are spread rather than taken from one end, because each belongs to a
particular entity: skipping the first four would give four entities a frame
each and starve the rest. Even spacing also reproduces fixes 3 and 4 exactly at
doses 4 and 2, which the self-test pins -- those two are the ones measured
against recorded sessions, so if the spacing rule stopped reproducing them the
frame counts would no longer describe the doses.

Every dose includes fix 1 and none touches the hit window or the mapping, so a
dose combines with fix 6 or fix 11 freely. `--check` says so rather than
assuming it.

### 2. Find out how many entity slots are idle in a fight -- the probe is written; it needs an hour of playing

`Measure entity slots.bat`, or:

```
mame a7800 -cart karateka.a78 -autoboot_script probes/entityslots.lua
```

Get into a one-on-one fight, fight for a minute, close MAME. It samples the
nine slot pointers once a frame -- only while `$187C` says the player is in
fighting stance, so the walk to the palace and the title screen do not dilute
the answer -- and reports, per slot, how many distinct behaviours it held and
how often it changed.

**A slot with one distinct value and no changes did nothing all fight.** Count
those: that is how many of the nine frames a round are spent on nothing, and
how much responsiveness is available for free.

This decides option 5 outright, in either direction, and that is why it is
worth doing before the three-day job rather than after.

### 3. Do the button properly -- a day or two

Fix 2 was withdrawn because a sticky latch turns every tap into a hold, and the
game distinguishes them. [karateka-a8.md](karateka-a8.md) shows what the 8-bit
version does instead, and it is not subtle:

```
  0FB4  LDA $D010        read the trigger every frame
  0FB9  INC $DB          held: count the frames
  0FBD  CMP #$14         twenty of them is a hold, not a tap
```

Sample at 60 Hz -- the latch routine at `$FF80` already runs every frame -- and
keep a *count*, not a bit. Then each of the six button-test sites reads either
"pressed since you last looked" or "held for N frames", whichever it wants.
The work is deciding which of the six wants which, and that needs play-testing
rather than analysis.

Orthogonal to everything else here.

### 4. Remap the controls -- built

An earlier version of this section was wrong twice, and both errors are worth
keeping visible because they were errors of the same kind: reading one layer of
a two-layer structure and concluding the other layer did not exist.

It said the vertical stick was never read. It is -- one layer up, in the
dispatcher `w_7898`, not in the decoder. And it said six attacks would need
four behaviour words the game does not have. All six are already there:

```
   w_7898  command 6:  install w_74BA / p_706C / $187A C!
           command 7:  install w_7602 / p_7042 / $187A C!
```

`p_706C` is "left hemisphere + up/centre/down -> 1/2/3", `p_7042` the same for
the right. The height lands in `$187A`, and `w_74E8` and `w_7612` each CASE on
it into three strikes -- the left family striking with limb `$187D`, the right
family with `$1885`. Punch and kick, three heights each, exactly as the 7800
manual describes: left hemisphere punches, right kicks, and the figure always
aims right.

So the stock mapping is:

```
    walking    button 1              change stance
               right                 walk
    fighting   button 1              change stance
               button 2 + left       walk left
               button 2 + right      walk right
               left  + height        punch high/mid/low
               right + height        kick  high/mid/low
```

**Movement is the button-modified action and attacking is what a bare stick
does.** That is backwards from every other action game, and it names the
control complaint: repositioning takes two hands, and any stray push throws a
strike.

**Fix 11 swaps them:**

```
    walking    button 1, or up       change stance
               right                 walk
    fighting   right + button 1      punch, height from the vertical
               right + button 2      kick,  height from the vertical
               right                 walk right
               left                  walk left
               down, stick centred   change stance
```

Four pieces, because a mapping is not just its decoder: the two stance
decoders (23 and 49 bytes, rewritten in place); the punch family's height,
which now comes from the right hemisphere too, so two thread cells naming
`p_706C` become `p_7042` -- one in the dispatcher and one in `w_753E`, which
re-polls every six frames so holding the stick repeats the strike; and the
walk chains' "is button 2 still held" tests, NOPed out, or a walk would stop
the instant it started.

**Fix 12 is 3, 6 and 11 together.**

Two things to watch for, both consequences of the swap rather than faults in
it. Holding a direction now walks continuously where it used to strike, so the
fighting stance will feel much more mobile and possibly too mobile. And an
attack needs the stick pushed forward, so you cannot strike while backing away
-- deliberate, since the player always faces right, but a real change to what
is possible.

Fixes 9 and 10 are withdrawn. Fix 9 put stance change on **down** in fighting
stance, and down is height 3: low punch and low kick. It destroyed two of the
six attacks.

### 5. Only pay a frame for entities that are doing something -- two or three days, conditional on option 2

If most of the nine slots are idle during a fight, the loop is spending frames
on nothing. Testing the fetched pointer against the idle word and branching
over both the update and the wait would give a short loop when two people are
fighting and the full loop when a crowd is on screen -- responsiveness without
a global speed-up, which is the thing every other option trades away.

Each block grows by about six bytes and there are eight of them, so the blocks
have to move somewhere with room and be branched to. The ROM has space; the
thread rewriting is fiddly rather than hard.

Do option 2 first. It is an hour against three days.

### 6. Knockback -- three to five days, and the payoff is real if it works

The 8-bit version pushes a struck fighter back. The 7800 version does not: its
post-hit routines place a spark and nothing else, as shown above. That absence
is a plausible part of why hits there feel like nothing happened, and why
repositioning is such a slog -- neither fighter is ever moved except by walking.

The hook is exact. Each fighter has a base position that every body point is
built from -- `$186D`/`$186E` for one, `$188C`/`$188D` for the other -- and the
post-hit routines at `$7A30` and `$6A9C` already run at the moment of a hit,
already know which fighter was struck, and already have a position in the
accumulator. Adding a displacement to the victim's base is a few bytes in each.

The risk is the reason this is days rather than hours: the base is very likely
rewritten by the animation script on the next update, in which case a one-off
displacement is erased before it is seen. That is the first thing to test, and
it is a fifteen-minute test -- poke the base in a debugger mid-fight and see
whether the fighter stays moved. If it snaps back, knockback has to be
expressed as something the script honours, which is a different and larger job.

Knockback also interacts with the loop: pushing fighters apart means closing
again, and closing again at 13 frames a decision is exactly the slog this is
meant to fix. Do it on top of fix 3 or 7, never on the stock cadence.

### 7. Break the link between update rate and game speed -- weeks

Run the loop two or three times as often and multiply whatever each behaviour
word uses as a delay so the animation looks the same. The only route to
genuinely lower input lag at unchanged game speed.

Needs the entity behaviour words understood well enough to find every timing
constant, and those pointers are set at runtime, so it is a profiling job
before it is an editing job.

### 8. Rebuild the loop the way the 8-bit version does it -- weeks, and it contains option 7

All nine entities in one pass, one frame sync, presentation in an interrupt.
The room exists: at ~80% spinning, the real work of all nine updates fits inside
a single frame with margin. But a one-frame loop is thirteen times too fast, so
this cannot be done without option 7 -- and having done option 7, most of this
is already achieved.

### 9. Speed up the Forth interpreter -- do not

41 cycles per NEXT, 64 primitives ending in it, and nearly irrelevant: four
fifths of all dispatches are the spin loop. A faster NEXT makes the game wait
more efficiently.

(There is a trap here too. NEXT goes through a `JMP ($00EB)` stub planted in
RAM at `$00EA`. Replacing it with a ROM `JMP ($00EB)` looks like free cycles
and is not -- it would jump to the word address rather than to its code field.)

### 10. Port the 8-bit version -- a different project

The 8-bit game runs from 31 KB of RAM, reloading three 8K banks per scene. The
XEGS cartridge assumes 64 KB and would need almost nothing done to it; a 7800
has 4 KB, and every absolute address in three banks per scene is written for
its RAM location. See [porting-karateka.md](porting-karateka.md).

---

## If it were one week

Play fix 12, then fix 7 and fix 8 back to back to separate the reach change
from the mapping change. Then
the fifteen-minute knockback probe from option 6, because it is the cheapest
way to find out whether the biggest remaining item is a few bytes or a rewrite.
Then option 2, then 3 and 4 together -- the button and the mapping are the same
sitting, and both want the same play-testing session.
