# What the other cartridge projects taught this one

Eight sibling repositories were reviewed against the Karateka work:
`asteroids`, `ballblazer`, `centipede`, `digdug`, `galaga`, `mspacman`,
`poleposition2`, and the `midnight-mutants-toolkit`. Their game-specific
findings do not transfer -- Karateka is threaded Forth and they are hand-written
6502 -- and the user predicted that correctly.

The **methodology** transfers, and three items found real defects here.

## It changed something

### A read tap that reports zero needs a positive control

`probes/entityslots.lua` exists to count things that did *not* happen: how many
of the nine entity slots sit idle through a fight. That is the single easiest
measurement to get silently wrong -- a wrong address, a wrong moment, a filter
that excludes the thing being asked about -- and the result is a clean false
negative nobody revisits, because a ruled-out cause does not get re-checked.

The probe had no control. It does now: slot `$1878` is the player's, `w_7898`
writes a behaviour word there on every command, and it cannot be idle during a
fight. If the control reports no changes, the probe says so in capitals and
tells you to ignore the other eight numbers.

The sibling case was three read taps on a graphics block all reporting zero,
where one of the three was already *proved* live. That is the only reason the
"this region is dead" conclusion was not filed.

### A scan for absolute operands cannot see an indirect load

The conclusion that the difficulty switches affect nothing rested partly on a
scan of every absolute access to the RIOT page. Taken alone that scan proves
"not reached absolutely" and never "not reached" -- a sibling project got a
confident clean zero on a block that turned out to be read through `(ptr),Y`,
where the ROM address exists only as two immediate bytes somewhere else.

Re-checked here, and the conclusion holds, but for a different reason than the
one first given: the decisive evidence is that the two difficulty words'
addresses appear **zero times as a 16-bit value anywhere in the 49,152 bytes**.
In an indirect-threaded image a word can only run if its code-field address
reaches W, and that address has to exist somewhere first. A search for
`LDA #$02` or `LDA #$82` feeding a zero-page pointer turns up one candidate,
`$58F7`, and it does not form `$0282`.

The scan was not what made the answer right. Saying which evidence carries a
negative is the point.

### Patching a ROM desynchronises `.inp` playback from the first frame

Already learned the hard way here -- the retracted "13.40 to 4.71 frames" came
from comparing two runs that had drifted into different states -- but the
sibling write-up states the remedy better than this project had it: **compare
at a matched game state detected from RAM, never at a matched frame number**,
and failing that trust only findings of the form "this whole category of thing
is gone", which timing drift cannot manufacture.

It also explains why the replacement measurement survived. The 13-to-9-frame
result is a *distribution* -- median loop period over a whole session, 88.3% of
iterations at exactly 13 against 84.6% at exactly 9 -- and a distribution does
not care that two runs drifted apart.

## It confirmed something

**"That can't be what the game does" is a premise, not a measurement.** The
finding that `SWCHB = $80` is unreachable rests on reasoning -- Reset, Select
and Pause cannot all be held during play -- not on a measurement. The reasoning
is sound and the sibling pitfall is a warning that this exact shape of argument
"feels strongest exactly when it has been checked least". Logging `$0282` across
a session would settle it for the cost of one probe run, and until that is done
the claim should carry its status.

**A negative result is only as good as the window you measured it over.** The
entity-slot probe already refuses to conclude anything under 300 fighting
frames. That guard was written before this review and the sibling experience --
two negatives filed from windows too short, one costing a day -- says it should
stay.

## It did not transfer

The siblings' productive tricks are all shaped by hand-written 6502 and find no
purchase in a Forth image:

- **Grepping for `SED` to find BCD arithmetic** located scoring in Galaga and
  Ms. Pac-Man immediately. Karateka's arithmetic is inside interpreter
  primitives shared by everything, so a hit tells you nothing about which game
  logic used it.
- **Walking the live display list to find graphics** works because those games
  point MARIA at ROM. Karateka is Forth over the same hardware, so this would
  still work in principle -- it is simply not the open question here.
- **Gap analysis** (`--check-gaps`, ported into the Midnight Mutants toolkit
  three commits ago) asks which bytes the disassembler never reached. For a
  threaded image the equivalent question is which *words* nothing names, and
  `tools/forth.py --map` already answers it -- that is how the two unused
  difficulty primitives were found.
- **Cross-checking against a private historical reference**, held deliberately
  unconsulted until independent work is substantially done. No such reference
  exists for Karateka, and the 7800 manual has already served the equivalent
  role once, by distinguishing a tap from a hold and explaining why the
  button-latch fix had to be withdrawn.
