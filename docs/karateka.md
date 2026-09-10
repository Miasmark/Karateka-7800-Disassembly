# Karateka: where the input lag comes from

Karateka on the 7800 has a reputation for unresponsive controls. The usual
explanations are guesses about sloppy polling or a slow port read. Neither is
right, and both are the wrong shape of answer: the read is fine, and it happens
in the right place. The problem is how *often* the game gets round to it.

Measured, in the emulator, during play:

```
port $0280 sampled 249 times over frames 874-4200
mean 13.40 frames between samples = 4.5 per second
     5 frames apart:    14 times
     7 frames apart:    14 times
    13 frames apart:   122 times
    14 frames apart:    70 times
    19 frames apart:    14 times
    23 frames apart:    14 times
```

**Karateka looks at the joystick about four and a half times a second.** The
machine draws sixty frames in that second. The common case is a 13- or
14-frame gap -- roughly 220 milliseconds -- before the game even *notices* the
stick moved, and that is before any animation or state machine has run.

This matters more here than it would in most games because of how Karateka is
played. From the manual: "Kick. Move handle right." "Punch. Move handle left."
"Kick high. Move handle to upper right." Every attack is a joystick direction,
not a button. And: "Beware of danger when standing or running. In these
positions you're unprotected. One blow from a guard will destroy you." The game
asks for precisely timed directional input and then samples that input four and
a half times a second.

## Why it is that slow

Karateka's game logic is not 6502 code. It is a threaded-code interpreter --
Forth-style indirect threading -- and the game is a program written for it.

Reset stores `$6C` into RAM at `$00EA`. That is the opcode for `JMP
(indirect)`. The fetched thread word lands in `$00EB/$00EC` immediately after
it, so the three bytes at `$00EA` spell a `JMP ($xxxx)` that the interpreter
then *executes out of zero page*. The inner loop at `$401E` is:

```
sub_401E:                  ; NEXT
    LDY  #$01              ; 2
    LDA  ($E8),Y           ; 5    fetch the thread word
    STA  $EC               ; 3
    DEY                    ; 2
    LDA  ($E8),Y           ; 5
    STA  $EB               ; 3
    CLC                    ; 2
    LDA  $E8               ; 3    advance the thread pointer by two
    ADC  #$02              ; 2
    STA  $E8               ; 3
    BCC  L_4034            ; 3
    INC  $E9               ; 5    (only on a page crossing)
L_4034:
    JMP  $00EA             ; 3    into RAM...
                           ; 5    ...where JMP ($xxxx) dispatches
```

That is **41 cycles of dispatch before any work is done**, on every single
operation.

### It is Forth

Not merely "threaded code": the whole inner interpreter is Forth's, and all
four parts are in the ROM.

| | | |
|---|---|---|
| `NEXT` | `$401E` | fetch the next thread word, advance IP, dispatch |
| `DOCOL` | `$4C3E` | enter a colon definition |
| `EXIT` | `$4D86` -> `$4D88` | leave one |
| literal | `$4C7E` -> `$4C80` | push the next thread word as data |

`DOCOL` is textbook:

```
4C3E  LDA $E9 / PHA      ; push the caller's IP...
      LDA $E8 / PHA      ; ...onto the 6502 stack, used as the return stack
      CLC
      LDA $EB / ADC #$02 ; IP = this word's parameter field
      STA $E8
      TYA / ADC $EC
      STA $E9
      JMP $401E          ; NEXT
```

and `EXIT` at `$4D88` is `PLA / STA $E8 / PLA / STA $E9 / JMP $401E` -- pull
the saved IP back and carry on. Measured over 2,100 frames of play, colon
entries and exits balance exactly, at **16.5 of each per frame**, which is the
check that they are what they look like.

**There is no dictionary.** The ROM holds no word headers -- no count bytes
with the high bit set, no name strings, no link fields; the only ASCII in it is
the game's own text. That is what a *shipped* Forth looks like: target-compiled
and headerless, because a player never types at it. So the mechanism is
recoverable and the word names are gone for good.

Of the 233 dispatches a frame, **7.1% enter a colon definition** and the rest
are primitives. `NEXT` alone accounts for about **9,565 cycles per frame**,
before any primitive does anything, on a 1.79 MHz processor that MARIA is
already halting for DMA. Reset points the thread pointer at `$4098`, and the "program" is the
block of address pairs that follows. Token `$4C7E` is a push-literal primitive
(`LDA ($E8),Y / PHA / INC $E8 ...` -- it consumes the next thread word as
data), `$523E` is a fetch, `$526C` a store. It is a stack machine.

This is also why a tracing disassembler finds almost nothing: 173 instructions
reached in a 48K ROM, because the rest is not reachable as code. It is a
program in another language.

Measured throughput:

```
interpreted operations per frame:  mean 214.5   min 5   max 254
interpreted operations between one look at the controls and the next:
                                   mean 2874   min 1206   max 5307
```

So the main loop is about **2,900 interpreted operations long**, and the
machine can retire about **215 of them per frame** -- which is exactly the
13-to-14-frame gap the histogram shows. The two measurements are independent
and they agree.

At 41 cycles of dispatch each, 215 operations per frame is roughly 8,800
cycles of pure overhead -- a substantial fraction of a frame on a 1.79 MHz 6502
that MARIA is already halting for DMA, and that is before the primitives do
anything.

## What this is not

It is not a bug in the input code. `$5A07` reads SWCHA once and shifts it eight
times into four direction accumulators at `$A2/$A3/$A5/$A6`; `$59E9` reads
INPT0 and INPT1 into `$A4/$A7`. Both are called by one word at `$59FE`:

```
$59FE  JSR $5A07     ; directions
       JSR $59E9     ; buttons
       JMP $401E     ; NEXT
```

That is clean, and it costs almost nothing. Stance and attacks are sampled
together, so the whole control surface moves at the same 4.5 Hz. The lag is
structural: the game is interpreted, the loop is long, and the controls are
read once per trip round it.

## Can it be fixed?

Partly, and the useful thing is knowing which part.

### What the player actually loses

The lag is only half the complaint. The other half is that presses vanish.
Driving a press at varied phases and watching the game's own direction
variables move:

| press held | the game reacts |
|---|---|
| 2 frames (33 ms) | 16% |
| 6 frames (100 ms) | 34% |
| 10 frames (167 ms) | 37% |

A quick tap does nothing two times in three. That is not latency, it is input
*loss*, and it is what "unresponsive" means to someone holding the stick.

### The fix that fits

The game reads the controls once per trip round its loop. Nothing cheap makes
that loop faster -- it is a Forth program and `NEXT` alone eats a third of
every frame. But the reads do not have to be the only sampling. The display
interrupt already runs several times a frame, and its last phase spends **85
cycles doing nothing** before writing the background colour:

```
5945  LDY #$10 / DEY / BNE -3 / NOP / NOP     7 bytes, 85 cycles
```

Those cycles place a raster boundary, so they cannot be taken -- but they can
be *spent differently*, provided the replacement costs exactly 85 cycles. It
does: a `JSR` plus four NOPs (14) into a sampler that latches the controls and
pads itself back out to 71. The game's existing input word then reads the latch
instead of the port, and clears it. Anything pressed at any point between reads
is still there when the game looks.

`patches/karateka-input-latch.py` builds it. Free zero page (`$CF-$D1`) and free
ROM (`$FF80`) were both found by measurement -- taps recording what the running
game never touches -- rather than by looking for filler, which would have been
wrong: the largest run of `$00` bytes in the image, 6,448 of them at `$A6D0`,
is read a thousand times a session.

### What it got, and what it did not

| press held | original | patched |
|---|---|---|
| 2 frames | 16% | **46%** |
| 6 frames | 34% | **47%** |
| 10 frames | 37% | **47%** |

Short presses improve sharply and the response stops depending on how long you
hold, which is the latch working. It is not the ~100% the design aimed at, and
the shortfall splits cleanly in two:

**The latch held a press when the game looked only 72% of the time.** The
design assumed the interrupt's phase 0 runs every frame. It does not -- it
comes round every 1.9 to 3.7 frames, because the interrupt fires a few times a
frame and rotates through five phases. Latching in every phase would close
this; each has its own slack, and each needs its own cycle-exact edit.

**Where the rest goes -- corrected.** An earlier reading of this said the game
"refuses" a third of the presses the latch held. That was wrong, and the way it
was wrong is worth keeping. Narrowing the measurement to windows where both the
latch and the game's own reader ran gives **97 of 97** -- the latch held the
press and the game acted on it, every time. And the count of windows where the
game *was* reading input but the latch had missed it is **zero**. The latch is
never the bottleneck when Karateka is listening.

What the earlier figure had folded in was stretches where the game does not
look at its controls at all. Sampled over two-second windows, the game's reader
never runs in 27 of 69 of them. Its loop is off doing something else -- an
animation, a transition -- for seconds at a time.

So the press is not lost any more; it is *held* until the game gets round to
looking:

| | original | patched |
|---|---|---|
| quick presses answered | 51% | **61%** |
| ...of 2-frame taps | 16% | **46%** |
| delay when answered | 2.3 frames | 5.6 frames |

The original answers fast *when it happens to catch you* -- 2 frames -- and
drops half of everything. The patch drops far fewer and pays for it in delay on
exactly the presses that used to vanish. That is the right trade for a game
where a missed stance change is fatal.

### One thing to know while playing it

Do not mash. The latch remembers a press until the game reads it, so a single
press will take. Pressing three times can be worse than pressing once: if the
game happens to read between two of them it will act twice, and on the stance
button that means toggling back to where you started -- which is the failure
that gets you killed in one hit.

## The prototype behaves the same

atariprotos lists a 12/23/86 build described as "very close to final". It is in
the library, and it is worth measuring rather than assuming, because "the
release was rushed" is the usual explanation offered for how Karateka turned
out.

The prototype runs the *same Forth engine* -- `NEXT` at `$401E` is byte-for-byte
identical -- with the game code relocated by exactly $96 and only 48% of the
ROM in common. Its input routines are character-for-character the same as the
final's, at `$5A9D` and `$5A7F`, writing the same zero-page variables.

| | prototype (12/86) | final (1987) |
|---|---|---|
| interpreter dispatches per frame | 231.5 | 231.9 |
| presses answered | 54% | 51% |
| delay when answered | 2.3 frames | 2.3 frames |

So the responsiveness was not lost late in development. Two months before the
final build, with substantially different game code, the machine was already
spending the same 232 dispatches a frame and dropping the same half of the
player's presses. Whatever went wrong, it was not a last-minute regression --
it was the decision to run the game on an interpreter, which was made long
before either build.

## Animation and input are the same problem

The published complaints separate "choppy animation" from "unresponsive
controls". They are one thing seen twice. The display list is rewritten once
every 2.9 frames -- about **20 screen updates a second** -- and the loop that
rewrites it is the same loop that reads the controls. The game cannot animate
faster than it thinks, and it cannot listen more often than it loops.

That is also why the input latch helps but cannot finish the job. It removes
the losses caused by *not looking*; it cannot make the game look more often.

## Can it be made faster without recoding it?

Some. Not enough. Here is the arithmetic.

An interpreted operation costs about **88 cycles**: 41 in `NEXT` and roughly 47
in the primitive itself. The 47 is an estimate -- it comes from walking each
primitive's code and summing cycles, which counts both sides of short branches
and so runs high -- but it is the right order, and the direction of its error
matters: if the real work is cheaper, dispatch's share is *larger* than the 46%
this gives.

The machine retires about **242 operations a frame**, and a game step takes
about **2,900 of them**, which is the 12-to-13-frame loop measured elsewhere in
this document.

| change | `NEXT` becomes | what it touches | loop gets faster by |
|---|---|---|---|
| move `NEXT` into RAM, self-modify a `JMP` operand instead of going indirect | 36 | repoint 100 `JMP $401E` sites -- all identical 3-byte sequences | ~6% |
| keep the low byte of IP in Y, dropping the 16-bit add | 28 | rewrite every primitive that touches IP: `DOCOL`, `EXIT`, literal, the branches -- about a dozen | ~17% |
| compile the thread to native 6502 | 0 | recode everything | ~90% |

So the honest answer to "can we make it efficient without recoding it" is
**about a fifth**. That turns a 223 ms input cadence into 185 ms. It would not
change how the game feels, and the second row of that table is a dozen
hand-patched primitives in a game with no dictionary and no symbols, where a
mistake shows up as a hang three levels into a fight.

### The more useful conclusion

Dispatch is not the whole story -- **the primitives cost more than the
dispatch**. Even deleting the interpreter entirely leaves the same 2,900
operations of work per game step, and buys a bit over 2x. Karateka is not slow
merely because Forth is indirect-threaded; it is slow because a single step of
its game logic is nearly three thousand operations of a stack machine.

Which means the lever that would actually matter is doing *fewer operations per
step*, and that is a rewrite of the game's logic, not of its interpreter.

### The cheap thing that is left

If the goal is responsiveness rather than speed, the loop does not have to get
faster -- the game has to *look* more often. The input word is one token; the
thread could call it at several points in a step instead of once. That needs no
performance work at all.

It does need thread surgery, and inserting a token shifts every absolute address
after it, so it means either finding spare tokens to overwrite at safe points or
relocating a definition. Both need the thread decompiled first, which is the
project that keeps coming up: `NEXT`, `DOCOL`, `EXIT` and the literal are all
identified, so a decompiler that prints a colon definition as a readable
sequence is buildable -- and it is the prerequisite for every remaining
question here, including the collision detection everyone complains about.

## What actually sets the pace

Everything above about the interpreter is true and almost beside the point.

A profile of a real play session -- `probes/threadprof.lua`, three minutes,
someone actually fighting -- puts **66% of all execution inside one definition**,
`w_A59C`. Decompiled, it updates nine entities and waits a whole frame after
each:

```
LIT $18xx / @ / EXECUTE      run this entity's action
vblank? / 0BRANCH -6         spin until vertical blank starts
vblank? / 0BRANCH -4         spin until it ends
```

Nine EXECUTEs, eighteen vblank tests. Nine updates at about a frame and a half
each is the thirteen-frame loop measured everywhere else in this document.

**The machine is not computing for thirteen frames. It is waiting for twelve of
them.** Karateka's pace is a deliberate decision -- one entity per frame -- not
a consequence of running on a Forth interpreter. Speeding up `NEXT` would have
changed nothing, and the earlier conclusion here that the loop was compute-bound
was wrong.

That conclusion deserves a note, because the reasoning looked sound. Frames per
loop tracked dispatches per loop across a fourfold range, which reads as
compute-bound. It is equally consistent with spin-waiting: while the loop waits,
it is spinning on `vblank?` and `0BRANCH`, so dispatches accumulate in proportion
to the waiting. The correlation could not tell the two apart, and it was taken
as though it could.

### The fix, and what it costs

`patches/karateka.py` fix 3 skips four of the eight waits -- a forward `BRANCH`
over each 14-byte block. Fix 4 skips two, for comparison.

It is not free. Those waits pace the *whole* game: an entity gets a frame, so
removing them speeds up movement and animation exactly as much as the response
to the stick. Played, the result is clearly better -- two enemies passed in
record time, repositioning no longer a slog -- and also faster and somewhat
harder. Whether that is the game you want is a judgement, not a measurement.

### Why the numbers here stop

An earlier version of this section reported the input cadence falling from
13.40 frames to 4.71 under fix 3. That was wrong, and the way it was wrong is
worth keeping. Scripted input never reaches the main loop: over 2.25 million
dispatches it lands inside `w_A59C` twice. The patch was inert in the run that
appeared to demonstrate it. What differed was which state the two runs drifted
into -- their dispatch totals differ by 0.1%, and four thousand frames is
plenty for that to become a different screen.

So this fix has no before-and-after frame count, and cannot have one from a
script. The evidence is someone playing it. That is weaker than a measurement
in general and stronger than one here, because the measurement was not
measuring the patch.

## Reading the thread

`tools/forth.py` decompiles it. Everything else on this page -- making the
game faster, making it listen more often, finding the collision test -- needed
this first, because none of it is 6502 code.

```
python tools/forth.py karateka.a78 --map
python tools/forth.py karateka.a78 --at 7898
python tools/forth.py karateka.a78 --callers 59FC
```

It finds the interpreter by shape rather than by address, so it works on the
prototype too without being told anything:

|  | final | prototype |
|---|---|---|
| NEXT | $401E | $401E |
| DOCOL | $4C3E | $4CD4 |
| EXIT | $4D86 | $4E1C |
| literal | $4C7E | $4D14 |
| colon definitions | 301 | 301 |
| primitives | 203 | 203 |

Equal counts either side of two months and a 52% byte change is worth noting:
it is the same program, recompiled.

### What it took to get right

Two searches had to be thrown away first, and both failed in the same
instructive way -- by producing something that looked fine.

**Finding NEXT by scanning for `JMP` byte patterns elected the wrong address.**
The thread is much larger than the kernel and is full of cells whose bytes
happen to spell a jump; the vote returned the literal's own address, from 112
jumps that do not exist. Counting only jumps found by following real
instructions from real primitives -- which announce themselves, because a
primitive's code field points at the two bytes after it -- gives $401E by 64 to
7.

**Recognising only one kind of branch corrupted the listing.** A word that
takes an inline cell must either add it to the thread pointer or step past it,
and Karateka has three such words: an unconditional branch, a conditional one,
and a compare-and-branch. Testing for "adds through the thread pointer" caught
the first and missed the other two, so seven cells in a single definition were
decompiled as calls to `$0010`. The fix was to find the two shared tail
routines that do the pointer arithmetic and ask which words can reach them.

That second failure is the one to watch for in any threaded decompiler. An
unrecognised inline cell does not throw an error -- it silently becomes a call,
and the definition still reads plausibly.

### What it gives you

The input dispatcher, for instance:

```
: w_7898
      789C    p_59FC        ( read the controls )
      78A0    LIT $0001
      78A4    p_4D92 $0010  ( if it equals 1, jump )
      78A8    LIT $70E6     ( else make $70E6 the current action )
      78AC    LIT $1878
      78B0    p_526C        ( store )
      78B2    p_4D56 $0098  ( and jump away )
      78B6    LIT $0002
      78BA    p_4D92 $0010  ( if it equals 2 ... )
```

-- a chain of comparisons that ends by writing the address of a definition into
`$1878`. `--callers 59FC` finds all four definitions that read the controls.

### What it will not give you

Names. A shipped Forth has no dictionary, because nobody types at a game, so
`w_7898` is the best anyone can do. The ROM contains no name headers at all;
the only ASCII in it is the game's own text. This recovers structure, and
turning structure into meaning is still a person reading stack code -- but
forty lines of it, instead of forty thousand bytes of hex.

## Measuring it yourself

`probes/inputlag.lua`, with MAME:

```
A7800_LAG_NEXT=0x00EA mame a7800 -cart karateka.a78 \
    -autoboot_script probes/inputlag.lua \
    -video none -sound none -nothrottle -skip_gameinfo
```

`A7800_LAG_NEXT` is optional and names the interpreter's inner loop, if the
game has one; fetching an opcode is a memory read, so a read tap on that
address counts one hit per interpreted operation.

Two traps worth knowing, both of which produced confident wrong answers here
first:

**An attract screen polls nothing.** Measure before the game starts and you
will find zero reads of `$0280` and conclude the game never looks at its
controls -- which is what happened here first. The fix was in the manual:
Select starts the game. Tested one input at a time, Select alone starts it at
frame 266; **Reset alone and the fire button alone never start it at all**,
and leave the cartridge in attract indefinitely. The probe presses Select.

**Then stop pressing.** An earlier version kept tapping Reset every 200 frames
to be sure the game had started, and the histogram grew ten gaps of 120 frames
that looked like the game ignoring the player for two seconds at a time. They
were restarts -- one per 220 frames, which should have been the giveaway. With
the console left alone after the game begins, those disappear and the real
distribution is the tight 13/14-frame cluster above.

The figures quoted here come from a Select-only start with the console untouched
afterwards, and are identical to those from the messier run: 13.40 frames, 2,874
operations per loop, 214.5 per frame. Two different start sequences agreeing to
three significant figures is the reason to believe them.
