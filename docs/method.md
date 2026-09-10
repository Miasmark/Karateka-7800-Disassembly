# Getting from a ROM to a disassembly you can trust

This is the working order that produced a byte-identical, fully annotated
disassembly of a 128K commercial 7800 game. Nothing here is specific to that
game; the order is what matters, because each step makes the next one cheaper.

## The rule that makes everything else work

**The rebuild must stay byte-identical.**

Every listing the disassembler emits is assembled straight back and compared to
the ROM it came from. If the comparison fails, the listing is wrong -- not
"close", wrong -- and no annotation on top of it is worth anything.

```
python tools/disasm.py game.a78 -c annotations.json -o src
python tools/verify.py game.a78 -d src
```

Do this from the first hour, not once at the end. It costs seconds and it means
that when you later mark a byte range as data, or rename a label, or split a
table, you find out immediately whether you broke the reconstruction. Everything
else in this document is a way of adding *meaning* to a listing that is already
provably complete.

The trap it protects you from is subtle. A recursive-descent tracer only sees
code it can reach. Anything reached through a computed jump, a RAM vector, or a
bank switch it could not resolve is quietly left as data, and a listing full of
`.byte` still reassembles perfectly. Byte-identity proves you have *all* the
bytes; it does not prove you have understood them. That is what the coverage
number is for -- track what fraction of each bank the tracer reached, and treat
a bank stuck at 30% as an unanswered question.

## Before the order of work: just open it

```
python tools/workbench.py game.a78
```

The steps below are the right shape for the tools and the wrong shape for the
first hour with an unfamiliar ROM, where the question is only "what is in here,
and can I see it?" The workbench answers that: header, mapper, vectors, spaces,
then a scan whose every result has a button that opens it in the right editor
with the space, base and format already filled in. That last part is what is
tedious by hand and silent when you get it wrong.

It launches each editor as its own process on its own port, and stops them when
it stops -- a child left holding a port looks exactly like a stale server on the
next run, which is a genuinely confusing way to lose an afternoon.

Use it to find your way around. Use the rest of this document when you want the
disassembly to be right.

## Order of work

### 1. Survey before disassembling

```
python tools/survey.py game.a78 --strings
```

You want four things before you write a single annotation: how the cart is
banked, which banks are code and which are graphics, whether the text is
readable, and where the vectors point. Entropy per bank is a good enough
classifier -- code sits near 6 bits/byte, bitmap graphics above 7, tables below
5.

If the strings come out readable, you have been handed a map. Every message the
game prints is an anchor: find the string, find what points at it, and you have
found the routine that prints it and usually the table that indexes it.

If the strings do *not* come out readable, the game has its own alphabet, and
that becomes the first real job. Render the character set (`tools/gfx.py`) and
read the mapping off the picture.

### 2. Write the annotations file from what the cartridge already tells you

```
python tools/init.py game.a78 -o annotations.json
```

The vectors are entry points on every cartridge, and the header already knows
the mapper, the region and whether there is a POKEY. There is no reason to type
any of that by hand, so `init.py` reads it off the image, writes the annotations
file with the vectors as entries, then runs the disassembler and tells you how
far it got:

```
  2 entry points from the vectors: f7:FF00, f6:400C
  f6   bank 6    7418/16384 bytes   45.3%   (3465 instructions)
  f7   bank 7    1471/16384 bytes    9.0%   (755 instructions)
```

That 45.3% is from the vectors alone. The same game with a fully hand-built
annotations file reaches 54.2% on `f6` -- so the scaffold gets you most of the
way to where a lot of hand work ends up, and the gap between the two numbers is
the work that is actually yours.

It writes down only what it can read off the cartridge. Anything it is unsure
of goes in as a note saying so, never as a value: once a guess is sitting in an
annotations file it looks exactly like a finding, and the next person to read it
cannot tell them apart. It also refuses to overwrite an existing annotations
file without `--force`, because that file is where all the hand work lives.

### 3. Trace from the vectors, then chase what the tracer misses

The disassembler starts at RESET, NMI and IRQ and follows control flow, carrying
constants in A/X/Y so the `LDA #n / STA $8000` bank-switch idiom resolves by
itself. What it cannot follow, it reports:

* **Unresolved bank switches** -- a switch whose value was not a tracked
  constant. Pin them down by hand in `bankat`.
* **Jump tables** -- `JMP ($nnnn)` and `JSR` through a table. Declare the entry
  points in `entries`.
* **RAM vectors** -- handlers the hardware or the game calls indirectly. On the
  7800 the display-interrupt slot is the classic one. Declare the address pair
  in `ram_vectors` and the tracer re-traces through whatever it finds there
  until nothing new turns up.

That last one is worth dwelling on. A display-interrupt handler is *invisible*
to a plain trace: nothing in ROM refers to it by address, because the game
writes its address into a RAM slot and MARIA jumps through it. Miss it and a
large, important routine looks like data forever.

### 4. Name things as you learn them, in one file

Keep every human judgement in `annotations.json` and none of it in the generated
listings, which are disposable. Labels, comments, block headers, RAM names,
data-block declarations. Regenerating is then free, and free regeneration is
what lets you rename something at 2am when you finally understand it.

Name from behaviour, not from guesswork. `sub_5890` becomes `CheckGhostTouch`
when you have watched it run.

### 5. Confirm on hardware, not in your head

This is the step people skip and it is the one that catches the errors. A static
read of the code gives you a hypothesis; an emulator running the actual ROM
gives you a fact. See `emulation.md` -- and read the warning about garbage
collection there before you write a single tap, because getting it wrong
produces confident, wrong answers rather than errors.

The pattern that works: form a specific, falsifiable claim ("this table is the
frame sequence for the death animation, and it has four entries"), then build a
probe that would fail loudly if the claim were false. Watching a value change is
weak evidence. Watching it change *exactly when your model says it should* is
strong evidence.

### 6. Change one byte and see it

Once the disassembly is trustworthy the payoff is that you can edit with
confidence, because you know what every byte is. Build the modified image, diff
it against the original, and check the diff is exactly the bytes you meant to
touch and no others. Then run it.

`tools/bps.py` makes patches. Build them against the **cartridge data only**,
with the 128-byte `.a78` header excluded -- headers vary between dumps of the
same game, and a header-inclusive patch is refused by an otherwise identical
image for no reason that matters.

## What "understood" actually means

A listing is understood when you can predict the effect of changing a byte
before you change it, and be right. Short of that you have a transcription.

The useful intermediate is a **claim you have tested**. Write down what you
think a routine does, then construct the experiment that separates that from the
next most likely explanation. Most of the real progress in a project like this
comes from noticing that two explanations you had been treating as one are
actually different, and that you have never distinguished them.

## Reading the listing

Three switches change what the listing tells you, all of them cheap and none
of them affecting what it assembles to.

**`--cycles`** puts each instruction's cycle count in the raw-bytes comment:

```
LDA       ram_0054      ; 401C: A5 54    3
LDA       zone_pal_shadow,Y ; 4029: B9 CC 1E 4+
```

The trailing `+` means the count can be higher -- an indexed read that crosses
a page costs one more, a taken branch one more and two if *it* crosses a page.
A disassembler cannot know which, so it marks the possibility rather than
inventing a number. The counts come from the documented timings, cross-checked
against DiStella's table and 32 published spot values.

That matters here more than on most machines. MARIA steals cycles by DMA, and
a display-interrupt handler has a hard budget; being able to add up a routine
without looking anything up is the difference between changing it confidently
and changing it hopefully.

**`"gfx": true` on a data block** draws the bits beside the bytes:

```
.byte $03,$AB      ; |      XXX X X XX| E8C0
.byte $BB,$AF      ; |X XXX XXX X XXXX| E8C2
```

`"wide": true` puts two bytes on a line, which is what a 16-pixel 7800 sprite
is; DiStella, being a 2600 tool, only ever needed one. The rendering stops
exactly at the block's end.

**Equates for references that cannot carry a label.** An address can be
referenced and still have nowhere to put a label: it is inside a multi-byte
instruction, or it points into data that was never traced. Printing a bare
number there loses the cross-reference -- the reader cannot tell the address
was referenced at all. Instead it gets a name and an equate:

```
ref_A085         = $A085
...
.word ref_A085     ; A05E: 85 A0
```

Midnight Mutants' sound-effect pointer table is twelve such targets. The idea
is DiStella's; it prints `L1234 = $1234` for the same reason.

## Overriding a header that lies

`--low` and `--mapper` force the layout when the dump's header understates it.
Midnight Mutants' European release declares cart type `$0002`, omitting the
bank-6 bit, so nothing is mapped at `$4000` and every `f6` reference fails --
`--low bank6` fixes it, and the error says so when it happens.
