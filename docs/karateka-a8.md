# What the 8-bit Karateka cartridge actually is

Traced with `tools/a8dis.py` off `Karateka.car` -- CART type 14, an XEGS 128K
cartridge, sixteen 8K banks.

## It is a disk that happens to be silicon

The obvious thing to expect from a cartridge is code running out of ROM. This
does not do that. Following the code from the cartridge's own start vector
reaches **seventy-five bytes** and then leaves for RAM:

```
  B7B1  LDA #$00 / STA $D0          the scene number
        LDA #$00 / STA $022F        DMA off
        LDA #$E1 / STA $0230        a display list at $BFE1: JVB to itself,
        LDA #$BF / STA $0231        i.e. a blank screen while it loads
        LDA #$22 / STA $022F        DMA back on
        LDA #$0D / STA $D500        page in bank 13
  B7CE  LDX #$00 / LDY #$10
        JSR $B2CC                   copy 8K from $8000 to $1000
        JSR $2F5A                   ...and run what just landed there
        JMP $7760                   ...then jump to the game
```

`$B2CC` is the whole loading mechanism, twenty-two instructions of it:

```
  B2CC  STX $02 / STY $03           destination
        LDA #$00 / STA $00
        LDA #$80 / STA $01          source: $8000, the cartridge window
  B2D8  LDY #$00
  B2DA  LDA ($00),Y / STA ($02),Y
        INY / BNE $B2DA
        INC $01 / INC $03
        LDA $01 / CMP #$A0          until the source reaches $A000
        BNE $B2DA
        RTS
```

Eight kilobytes, byte for byte, from the paged window into RAM. That is the
same shape as the floppy version's sector loader, and it is the single most
important fact about porting this: **the game does not execute from ROM at
all.**

## The overlay tables

The second-stage loader at `$2F5A` reads two seven-entry tables indexed by the
scene number in `$D0`:

```
    scene   $0480 common   $1000 code   $6000 data   $8000
      0        bank 12        bank 13     bank 11    bank 14
      1        bank 12        bank  7     bank  8    bank 14
      2        bank 12        bank  1     bank  2    bank 14
      3        bank 12        bank  5     bank  6    bank 14
      4        bank 12        bank  3     bank  4    bank 14
      5        bank 12        bank 13     bank 11    bank 14
      6        bank 12        bank  9     bank 10    bank 14
```

`tools/a8dis.py --overlays` prints this. Seven scenes; scene 5 reuses scene 0's
pair. Bank 12 is the code common to every scene, bank 14 stays paged in at
$8000, bank 15 is fixed at $A000 and holds the loader. Bank 0 is never selected
on any path traced.

Rebuild that address space and the game becomes visible: `--scene 0` reaches
**11,881 bytes of instructions** where the raw cartridge reached seventy-five.

## The frame

This is the part worth having.

```
python tools/a8dis.py Karateka.car --scene 0 --frame
```

The game installs an *immediate* vertical blank handler through the OS's
`SETVBV` -- which is the only OS call in the whole reachable image -- and that
handler does this, every frame, and then `RTI`s:

```
    #  calls   lives in     reaches  and touches
    1  $1169   bank 13          9 B  -                     save $14/$15/$03/$04
    2  $0F3C   bank 12         62 B  ANTIC NMIEN
    3  $104E   bank 13        231 B  POKEY AUDF1/AUDC1/AUDF2/AUDC2   the music
    4  $10DE   bank 13          9 B  -                     a 16-bit countdown
    5  $0F66   bank 12         14 B  -                     scene state machine
    6  $0F87   bank 12          6 B  ANTIC DLISTL/DLISTH   pick a display list
    7  $0F97   bank 12         31 B  ANTIC WSYNC, GTIA COLPF0
    8  $0FFB   bank 12         21 B  POKEY SKSTAT
    9  $0FA2   bank 12         85 B  GTIA TRIG0, TRIG1     the buttons
   10  $117E   bank 13          9 B  -                     restore $14/$15/...
```

Not one of those is a game entity. This is presentation, and it runs at a fixed
60 Hz. The game's own logic lives in the main line, which the handler
interrupts -- which is why steps 1 and 10 exist at all: both halves use the same
zero-page bytes.

**Compare the 7800 version.** That one gives each of nine entities an entire
frame to itself and takes thirteen frames to think once, measured in
[karateka.md](karateka.md). The two are not the same program arranged
differently; they are opposite arrangements. The 8-bit version has a fixed
presentation frame and free-running logic. The 7800 version has the logic
itself parcelled out across frames.

That is the mechanical answer to why the 8-bit version feels controllable.

## The button, and the patch that was withdrawn

Step 9 is the input layer, and it is the concrete version of something that was
guessed at earlier and got half-right:

```
  0FB0  LDA $DB                  is this a state that accepts input
  0FB2  BPL $0FD7
  0FB4  LDA $D010                ; GTIA TRIG0 -- read every single frame
  0FB7  BNE $0FC4                not pressed
  0FB9  INC $DB                  pressed: count the frames it has been held
  0FBB  LDA $DB
  0FBD  CMP #$14                 twenty frames -- a hold, not a tap
  0FBF  BNE $0FE0
  0FC1  JMP $0FE1
```

The button is sampled **every frame**, and held frames are *counted*, so a tap
and a hold are different inputs rather than the same input sampled luckily.

An earlier attempt to fix the 7800 version latched the button so a tap could not
be missed. That made the game worse and was withdrawn, and the reason is here:
the 7800 game also distinguishes tap from hold, so a latch turns every tap into
a hold. The 8-bit version shows what the fix should have been -- sample at 60 Hz
and count -- not what was tried.

The joystick *directions* are read separately, in the main line, at `$0990`:
`LDA $D300`, mask the high nibble, and index two eleven-byte tables at `$0960`
and `$096B` to turn sixteen stick positions into a direction pair. (The high
nibble is joystick 1, i.e. port 2.) There are four `NOP`s sitting in the middle
of that routine where something was assembled over.

## What this means for a port

**The RAM model does not survive.** The game occupies `$0480`-`$7FFF` -- about
31 KB of RAM -- and reloads three 8K banks into it at every scene change. A 7800
has 4 KB. The overlay scheme has to be replaced outright with banked ROM
execution, and that is a bigger job than any of the graphics work: every
absolute address in three banks per scene is written for its RAM location.

**The frame structure does survive, and is the thing worth taking.** A 60 Hz
presentation interrupt over free-running logic is exactly as implementable on a
7800 as on an 800; MARIA's DMA takes more of the frame than ANTIC's, but the
arrangement is unaffected. Copying the *structure* is most of what would make a
7800 Karateka feel like the 8-bit one, and it is independent of the artwork.

**The sound is a rename.** Step 3 is 231 bytes writing AUDF1/AUDC1/AUDF2/AUDC2.
On a POKEY cartridge that moves across with its base address changed.

**Only one OS call exists**, `SETVBV`, and it is replaceable with two stores.

## Caveats

The trace follows calls and both sides of branches from the entry points, so it
covers what is reachable without running anything. It does not follow computed
jumps: two sites at `$2F6B` and `$2F7A` select a bank from a table, which is the
overlay loader itself and is understood, but a jump table elsewhere would leave
code unvisited. 11,881 bytes reached is a floor, not a total.

Scene 0 is the title. The scenes with the fighting are 1-4 and 6, and their
main-line entry is not at a fixed address -- `$7760` is only scene 0's. Reading
those loops is the next step, and it is what would confirm that the entity
update in the 8-bit version is a single pass rather than a rota.
