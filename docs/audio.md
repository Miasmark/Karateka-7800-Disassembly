# Sound: the TIA, and the tracker

Every 7800 has two TIA audio channels. Each is three registers, and that is the
whole instrument:

| register | channel 0 | channel 1 | what it does |
|---|---|---|---|
| AUDC | `$15` | `$16` | waveform, 4 bits |
| AUDF | `$17` | `$18` | frequency divider, **5 bits** |
| AUDV | `$19` | `$1A` | volume, 4 bits, linear |

`tools/tracker.py` models this well enough to render a song to a WAV, reads and
writes a plain-text tracker format, turns a capture from a running game into an
editable song, and emits 6502 data with a player.

## AUDF is five bits, and this bites

Players routinely write a whole byte and let the chip discard the top three,
often because the same byte carries something else in its high bits. A value
lifted out of a ROM therefore means nothing until it is masked. Feed the
unmasked byte to a synthesiser and every note comes out about three times too
low — which in the polynomial voices drops below the range where a pitch is
audible at all, so the voice stops sounding like a voice and starts sounding
like tapping.

The same applies to AUDV. In a capture from a real game you will see values like
`$83` in the volume slot and `$D8` in the frequency slot; the chip keeps `$3`
and `$18`.

## The waveforms

AUDC picks between plain dividers and polynomial counters (LFSRs).

| AUDC | what | AUDC | what |
|---|---|---|---|
| `$0`, `$B` | silence | `$8` | white noise (9-bit poly) |
| `$4`, `$5` | tone | `$1` | buzz (4-bit poly) |
| `$C`, `$D` | tone, ÷6 | `$2`, `$3` | buzz, ÷31 |
| `$6`, `$A` | tone, ÷31 | `$7`, `$9` | 5-bit poly |
| `$E` | tone, ÷93 | `$F` | 5-bit poly, ÷6 |

The four tone modes are the ones you can write a melody with. The frequency is

```
clock / ((AUDF + 1) * divisor * 2)
```

with the clock 31,400 Hz on NTSC and 31,200 on PAL — so **the same song is in a
different key on a PAL machine**, not merely slower.

Seed the LFSRs non-zero and feed back with XOR. The XNOR-with-all-ones
construction lands on a lockup state where the register never changes; the
output is then flat DC, and all that is left to hear is the volume moving,
which sounds like tapping. That failure is quiet and easy to mistake for a
data problem.

## The TIA is not tuned to anything

Its pitches fall where the divider puts them. `tracker.py notes` prints every
one with its distance from equal temperament:

```
$ python tools/tracker.py notes
AUDC $4  tone               491-15700 Hz   25 of 32 within 25 cents
AUDC $C  tone, div 6         82- 2617 Hz   25 of 32 within 25 cents
AUDC $6  tone, div 31        16-  506 Hz    7 of 32 within 25 cents
AUDC $E  tone, div 93         5-  169 Hz    7 of 32 within 25 cents
```

Toward the top of a range the dividers are coarse and neighbouring semitones
collapse onto the same AUDF. Writing `F-4` on AUDC `$C` gets you an E4: the
nearest the chip can manage is 113 cents away, which is nearer to some other
note than to the one you asked for. The tracker says so rather than quietly
writing a different tune:

```
  out of tune: F-4 on AUDC $C is -113 cents out -- the nearest the chip
  gets is AUDF $07, which reads as E4
```

Write melodies around the pitches that are actually close, or pick a different
divisor for that phrase.

## The song format

Plain text, two channels, one row per frame by default. A cell is `AUDC PITCH
AUDV`; `...` holds whatever was playing and `off` clears the voice. Pitch is a
note name or a raw `$1F`.

```
title Scale test
region ntsc
rate 8
;   ch0        | ch1
C C-4 8        | C C-3 5
C D-4 8        | ...
off            | off
8 $10 6        | ...
```

Only a wholly cleared cell is written as `off`. A voice can sit at volume zero
with a waveform and pitch already loaded — players commonly set those a frame
early and then ramp the volume — and collapsing that to `off` would throw the
registers away.

## Capturing what a cartridge already plays

`probes/audio.lua` logs every AUDC/AUDF/AUDV write from any running 7800 game,
and `tracker.py capture` turns that log into a song. **This works on a cartridge
whose music player has never been looked at**: the register writes are the
music, whatever produced them.

```
mame a7800 -cart game.a78 -autoboot_script probes/audio.lua -video none -sound none -str 40
python tools/tracker.py capture a7800-audio.log -o game.trk
python tools/tracker.py render game.trk -o game.wav
```

The log records one line per frame in which anything changed, so gaps are
holds — which is what a tracker row of `...` means anyway, and why the two
line up without interpretation.

Two things to know before reading a quiet result:

* **The game has to be playing.** An attract screen is often silent, and the
  probe is a passive logger. Drive it, or capture against a `-playback`
  recording (see `emulation.md`).
* **A POKEY game will look silent on the TIA.** Ballblazer produced one frame
  of TIA change in a minute of play, because its music is on the cartridge
  POKEY; Xenophobe, whose sound is TIA, gave 806 frames from the same harness.
  Capture POKEY by passing its base address to the probe -- see below.

## The workflow, cartridge to WAV

One command, if MAME and the BIOS can be found:

```
python tools/capture.py game.a78 --render
```

It reads the header to decide TIA or POKEY and where, picks `a7800` or
`a7800p` from the region, records, converts and renders. On Windows, dropping
the `.a78` on `Render dropped file.bat` does the same and plays the result.

```
  Ballblazer (NTSC)
  sound      POKEY at $4000
  machine    a7800 (NTSC)
  recording  1500 frames, about 25 seconds
  log        Ballblazer (NTSC).log
  song       Ballblazer (NTSC).trk -- 1500 rows, 730 with a change
  wav        Ballblazer (NTSC).wav -- 25.0 s
```

MAME and the 7800 BIOS have to be findable. `--mame` / `A7800_MAME` names the
executable, `--rompath` / `A7800_ROMPATH` the folder holding the BIOS images;
without them it checks the usual install locations and any `bios` folder beside
the cartridge or the toolkit.

The four steps below are what that command does, and are worth knowing when a
capture comes out wrong.


### 1. Ask the cartridge which chip it uses

```
python tools/cart.py "Food Fight (NTSC).a78"
```

Look for a `POKEY` line. If there is one, note the address; if there is not,
the game's sound is TIA. That decides the next step and nothing after it.

```
  cart type   $0000
  mapper      linear, 32K, no banking
  vectors     NMI $B3C8  RESET $EFFA  IRQ $EFFD        <- no POKEY line: TIA
```

### 2. Capture the register writes while it plays

```
set A7800_DRIVE=1
mame a7800 -cart "Food Fight (NTSC).a78" -autoboot_script probes\audio.lua ^
     -sound none -video none -nothrottle -str 40
```

For a cartridge POKEY, add its base address from step 1:

```
set A7800_POKEY=0x4000
```

That writes `a7800-audio.log` in the current directory.

* `A7800_DRIVE=1` taps fire on a loop, because most cartridges sit silent on a
  title screen and this is a passive logger. It is a blunt instrument; for
  music that only appears in real play, record a session and capture against
  `-playback` instead (see `emulation.md`).
* `-sound none` still records the log -- it comes from the emulation, not from
  the speakers. Leave it out and a `-nothrottle` run plays the whole session
  through your audio device at twenty times speed.
* `-str 40` is emulated seconds. `A7800_AUDIO_FRAMES` sets how many frames are
  logged (3600 by default), and `A7800_AUDIO_SKIP` throws away the first n --
  useful for skipping a long attract sequence.

### 3. Turn the log into a song

```
python tools/tracker.py capture a7800-audio.log -o foodfight.trk
```

The log names its own chip, so there is no flag to get wrong. The result is
plain text you can read and edit.

### 4. Render it

```
python tools/tracker.py render foodfight.trk -o foodfight.wav
```

On Windows, steps 3 and 4 are one drag: drop either the `.log` or the `.trk`
onto **`Render dropped file.bat`** and it does the rest, then plays the result.

### What it looks like when it works

Food Fight, start to finish, is 260 frames of change in 30 seconds:

```
off          | off
6 $1D  9     | D A2   9
6 D0   9     | ...
6 $1A  9     | D A2   0
6 E0   9     | D C#3  9
```

All 260 logged frames replay to their exact register state.

### If the song comes out empty

`capture` will say so, and it is nearly always one of three things:

* the game never left its attract screen -- set `A7800_DRIVE=1`, or use a
  `-playback` recording;
* the game's sound is on a cartridge POKEY and you captured the TIA -- step 1
  tells you, and `A7800_POKEY` fixes it;
* the tap was garbage collected. That cannot happen with the shipped probe,
  which keeps its taps in a global, but it is the first thing to check in any
  probe you write yourself.

## Editing it: the tracker proper

```
python tools/trackeredit.py song.trk        a song
python tools/trackeredit.py game.a78        a cartridge -- captured, then opened
```

That serves a local page -- nothing leaves the machine -- showing the song as a
grid of rows and channels, with the AUDCTL column beside them on POKEY. On
Windows, `Open in tracker.bat` takes the same three kinds of file by drag.

| | |
|---|---|
| click a cell, or press **Enter** | start typing into it |
| type a hex digit or `.` | starts editing with that character |
| **Enter** | commit and step down a row |
| **Tab** | commit and move across |
| **Escape** | abandon the edit |
| **Delete** | silence that channel (`off`) |
| **Backspace** | hold it (`...`) |
| arrows, PageUp/PageDown | move about |

A cell is typed exactly as it reads in the file: `5 C-4 A` on POKEY,
`C C-4 8` on the TIA. Ask for a note the chip cannot reach and it takes the
nearest one and says how far out it was, in the bar along the bottom -- the
same warning `render` gives, at the moment you type it rather than later.

**Play from row** plays the number of seconds in the box beside it, from the
cursor; **Play all** plays the whole song, and says how long that is. The audio
element under the grid has an ordinary transport, and the row under the
playhead is highlighted as it goes, so you can watch where you are.

Both are rendered by `tracker.render` on the server rather than re-synthesised
in the browser, which is deliberate: what you hear is the file put through the
exact code that writes the WAV and the 6502 tables. A second sound model in
JavaScript would be a second thing to keep true. Rendering ten seconds takes
about a second, so a click is not instant -- the status line says so while it
works, because a silent wait is indistinguishable from a broken button.

### A capture usually opens on silence

A game sits on its title screen for a while, and the recording starts when MAME
does. Commando's music does not begin until **row 1053 -- 17.6 seconds in** --
so hunting for it a couple of seconds at a time is hopeless.

The tracker works out the first and last audible row when it loads, says so,
and puts the cursor on the music rather than on the silence. **Go to first
sound** jumps there and plays. A song where nothing is audible at all says that
too, which usually means the capture needs `A7800_DRIVE=1` or a longer run.

**Save** writes back to the same `.trk`; **Export .asm** writes the data and
player beside it.

## Getting a song back into a ROM

`tracker.py export` writes the song as data plus a small player that consumes
one row per frame from vertical blank -- six bytes a row for the TIA, nine for
POKEY (AUDF and AUDC per channel, then AUDCTL). A row is emitted whole even
where it only holds: a fixed stride is simpler to play than any packing, and a
short cue is a few hundred bytes. Pack it later if the space matters.

The POKEY player includes a `PlayInit` that writes SKCTL, which the chip needs
before it will make a sound, and a `POKEY_BASE` equate to match the header.

## How this was checked

* **The formula, against the rendered audio.** Tones were synthesised and their
  rising edges counted; every one lands within 0.5% of its calculated pitch, and
  the residue is the counting window, not the synthesis.
* **The model, against a validated one.** All sixteen waveforms at three pitches
  were compared sample-for-sample against the renderer used on a fully
  disassembled commercial game, whose output was confirmed by ear against the
  real thing. Identical on all 48 settings.
* **The capture loop, against itself.** A 438-frame capture from a running game
  was converted to a tracker file and replayed; every logged frame reproduced
  its exact register state. That test is what caught the format collapsing
  loaded-but-silent voices to `off`.

# POKEY

About a fifth of the library has one: **279 of 1,309 images** declare a POKEY,
142 at `$4000`, 127 at `$0450` and 10 at `$0800`. Eighteen declare *two* --
header bit `$0400` adds one at `$0440`. `cart.py` reports which and where, and
`capture.py` records every declared chip.

Four channels instead of two, and an eight-bit divider instead of five. That
second difference is the one you hear: the TIA has 32 dividers and no G4 on its
melody voice, while POKEY has 256 and plays a plain major scale without
argument. Compare `examples/demo-tia.trk` with `examples/demo-pokey.trk`.

| | TIA | POKEY |
|---|---|---|
| channels | 2 | 4 |
| divider | 5 bits | 8 bits |
| volume | own register (AUDV) | packed into AUDC |
| waveform | AUDC, 16 modes | AUDC bits 5-7, 8 distortions |
| global control | none | AUDCTL |
| polynomials | 4, 5, 9-bit | 4, 5, 17-bit |

A POKEY cell is `DIST PITCH VOL` rather than the TIA's `AUDC PITCH VOL`,
because the chip packs distortion and volume into one register and splitting
them is what makes a row readable. AUDCTL is global rather than per channel, so
it is a directive line that applies from that point on:

```
chip pokey
audctl $00
5 C-4 A         | 5 E-4 7      | 5 G-4 6      | 5 C-3 8
```

## What is modelled, and what is refused

Modelled: four channels, all eight distortions, the 64 kHz and 15 kHz base
clocks, the 1.79 MHz clock on channels 1 and 3, **both 16-bit pairs** (`$08`,
`$10`) and **both high-pass filters** (`$02`, `$04`).

Also modelled: the 9-bit polynomial (`$80`) and volume-only mode. Nothing in
AUDCTL is refused any more. The refusal machinery stays, because it is the
honest way to fail if a gap ever turns up again -- see below for why that
matters more than it sounds.

### Why the last two are worth doing anyway

The boundary was drawn by measurement, not by taste. Ten POKEY titles captured
and their AUDCTL writes tallied:

| feature | titles using it |
|---|---|
| 16-bit channel pairing | 2 of 10 -- Arkanoid, Galaxian 16bit |
| high-pass filters | 2 of 10 -- Acidjazzed, Bomb Jack demo |
| 9-bit polynomial | 0 of 10 |
| volume-only | 1 of 10 -- Arkanoid |

The useful part is that **it is all-or-nothing**. A game that uses one of these
uses it almost the whole time -- Acidjazzed 99.9% of its logged frames, Arkanoid
98.7%, Bomb Jack 86.8%, Galaxian 97.2%. There is no partial credit, so each
feature converts whole games from refused to working rather than improving
anything gradually. The four games above went from refused to rendering.

## The noise, and how it was got wrong twice

The polynomial voices took three attempts. The first was wrong, the second was
wrong in a more interesting way, and the third is checked against eighteen
settings instead of one. The failure is worth writing down because it is the
kind that never announces itself: **noise generated wrongly sounds exactly like
noise generated correctly.**

### The real bug: the polynomials are not clocked by the channel

They run continuously at the 1.79 MHz base clock. A channel only *samples*
whichever bit they are showing when its divider fires. So a channel on the
64 kHz clock skips 28 polynomial steps per tick and one on the 15 kHz clock
skips 114 -- `pokey_poly_step` is that number.

Advancing them one step per tick instead, which this file did for a long time,
gives a **decimation** of the real sequence. It reproduces whatever divider you
happened to test and nothing else.

That is exactly what happened. The polynomials were "recovered" from a capture
at AUDF 10 on the 64 kHz clock, and the tables that came out reproduced that
capture perfectly. They were fitted to one setting. The giveaway, once looked
for, is arithmetic:

| clock | tick rate | apparent period of the 9-bit polynomial |
|---|---|---|
| 64 kHz | 5810.9 Hz | **73** |
| 15 kHz | 1427.3 Hz | **511** |

The sequence is 511 long. On the 64 kHz clock the step is 28x(AUDF+1), which
shares a factor of 7 with 511 = 7 x 73, so only 73 of the 511 entries are ever
visited and the period *looks* like 73. A whole "the 9-bit register has period
73, not 511" finding was an artefact of the bug.

### What the tables actually are

With the stepping fixed, each polynomial was recovered properly: capture one
distortion at one setting, invert the decimation (`table[(i*step) % L]`, valid
where `gcd(step, L) = 1`), and majority-vote across every period in the
recording. All came back with vote purity 1.0000 and every index covered --
131,071 of 131,071 for the long one, off a 100-second capture.

Berlekamp-Massey on the recovered cycles gives degree 10 and degree 18, one
more than the register width each time. That extra factor is `(1 + x)`, and
factoring it out leaves the **textbook polynomials**:

```
poly9   s[n] = NOT(s[n-4]  ^ s[n-9])      x^9  + x^4  + 1
poly17  s[n] = NOT(s[n-12] ^ s[n-17])     x^17 + x^12 + 1
```

The `(1 + x)` *is* the XNOR. POKEY feeds back with XNOR, not XOR, which is why
a plain XOR generator with the same taps mispredicts **every single bit** --
0 of 511 and 0 of 131,071 correct. Both recurrences reproduce their entire
cycle with zero errors.

`poly4` turned out to be the table this file always had, rotated by one.
`poly5` is `a7800`'s `poly_init_4_5` form, transcribed rather than derived.

### Checked across eighteen settings, not one

Six distortions x three divider-and-clock combinations, comparing the model's
per-tick output against the chip's:

| | 64 kHz, AUDF 10 | 64 kHz, AUDF 23 | 15 kHz, AUDF 10 |
|---|---|---|---|
| 0 poly5 gating poly17 | 1.0000 | 1.0000 | 1.0000 |
| 1 poly5 gating a tone | 1.0000 | 1.0000 | 1.0000 |
| 2 poly5 gating poly4 | 1.0000 | 1.0000 | 1.0000 |
| 4 poly17 | 1.0000 | 1.0000 | 1.0000 |
| 5 pure tone | 1.0000 | 1.0000 | 1.0000 |
| 6 poly4 | 1.0000 | 1.0000 | 1.0000 |

The recovered starting phases corroborate each other: the same capture gives
`p5 = 30` whether it is read through distortion 0 or distortion 1, and `p4 = 6`
through distortion 2 or distortion 6.

**Varying one parameter would have caught the original error immediately.** One
setting cannot distinguish a correct model from a decimated one, and a single
1.0000 is not evidence of much.

### Checked against both emulators

The tables were recovered from **MAME v0.287** and then re-measured against
**`a7800` v5.2**, the 7800-devtools fork, which lists a *"pokey poly9 sequence
correction"* and *"pokey init state corrected to match real hardware"*. Every
case agrees:

| | 64 kHz, AUDF 10 | 64 kHz, AUDF 23 | 15 kHz, AUDF 10 |
|---|---|---|---|
| all six distortions, a7800 | 1.0000 | 1.0000 | 1.0000 |
| poly9 (`$80` / `$81`), a7800 | 1.0000 | — | 1.0000 |

So the two emulators produce the same sequences, and the fork's "poly9
correction" does not change what a phase-aligned comparison sees. Its companion
note names the other half: *init state*, which is the starting phase -- audibly
irrelevant, and absorbed by aligning phases before comparing.

One caution that cost an hour: **reading the fork's source is not a substitute
for measuring it.** Transcribing its `poly_init_4_5` for the 4-bit register
produced a table that disagreed with both emulators, while the one already in
this file matched. Source and silicon are both evidence and neither wins by
default.

The other trap was in the comparison rather than the model. At 64 kHz with
AUDF 10 there are only 7.59 audio samples per divider tick, so a fixed sampling
grid lands differently depending on when the tone starts -- and a7800 starts it
a little later than MAME. That alone made every distortion look like a
mismatch, including the pure tone, whose frequency measured identically on both
to 0.00 cents. Sweeping the sampling phase resolved all of them to 1.0000.
Where a measurement is that sensitive, disagreement is a claim about the
measurement first.

## Volume-only is a level, not silence

AUDC bit 4 takes the oscillator out of the path and puts the volume nibble
straight on the output. The model used to render that as silence, reasoning
that a level written once a frame is a step rather than a waveform. Measured,
that reasoning was wrong in the way that matters: MAME's output is exactly
linear in the volume nibble -- 372 units per step -- with peak-to-peak AC of
zero. It is a DC level.

So the channel is now held at full scale and the mixer scales it by volume,
which gets both cases right. A level that never changes is an inaudible offset,
exactly as on the chip. A level that changes every frame or two is a series of
steps, which is the sample the game was pushing through -- and which the old
model dropped on the floor without saying so.

## How the 16-bit dividers and the filters were checked

Comparing a render against a game's own audio does not work: the noise floor is
too high. Ballblazer, which uses **no** unmodelled feature at all, scored only
64% agreement by that method -- the disagreement was time alignment, other
channels sounding, and pitch detection on noise waveforms, none of it the model.
A test that a known-good case fails is not a test.

So the measurement uses a purpose-built cartridge instead: 16K that writes one
POKEY setting and loops forever, so the only thing in the recording is the thing
under test. `tools/mktone.py` builds them.

**16-bit pairing, against MAME's POKEY:**

| case | AUDCTL | AUDF16 | predicted | MAME | error |
|---|---|---|---|---|---|
| pair 1+2, 64 kHz | `$10` | 1000 | 31.93 Hz | 31.93 Hz | 0.00 cents |
| pair 1+2, 1.79 MHz | `$50` | 1000 | 888.67 Hz | 888.67 Hz | 0.00 cents |
| pair 3+4, 64 kHz | `$08` | 4000 | 7.99 Hz | 7.99 Hz | 0.00 cents |
| pair 3+4, 1.79 MHz | `$28` | 4000 | 223.33 Hz | 223.33 Hz | 0.00 cents |

Eight cases across all four pairing paths, worst error **0.00 cents**. The
divider offset is what makes this a real test rather than a formality: a joined
pair on the fast clock divides by AUDF+7, not the AUDF+4 an eight-bit channel
uses, and getting that wrong is a small enough error to sound plausible.

**The high-pass filters** are a flip-flop, not a filter in the usual sense: the
filtered channel's output is sampled on every clock of its partner and XORed
with the live output, so what survives is whatever changed faster than the
partner's clock. Checked by spectrum, since the XOR creates sidebands rather
than shifting a pitch:

```
AUDF 60 / clock 30      MAME : 34 67 1014 1047 2062 2096 3144 4124
                        mine : 34 67 1013 1048 2062 2095 3144 4124
```

Those sidebands are 33 Hz apart -- the channel-3 clock -- which is the
modulation the flip-flop produces. 22 of 24 peaks matched across three settings
on filter 1, and 8 of 8 on filter 2; the two misses are the eighth-ranked peak
falling either side of the cutoff, not a structural difference.

### Ballblazer, end to end

The flagship POKEY game runs the whole pipeline with no warnings:

```
A7800_POKEY=0x4000 mame a7800 -cart ballblazer.a78 \
    -autoboot_script probes/audio.lua -sound none -video none -str 55
python tools/tracker.py capture a7800-audio.log -o bb.trk   # 2400 rows, no warnings
python tools/tracker.py render bb.trk -o bb.wav             # 40 s, sound in 35 of 40 seconds
python tools/tracker.py export bb.trk -o bb.asm             # 21,601 bytes of data
```

All 1,468 logged frames replay to their exact register state. It uses AUDCTL
`$00` and `$60` -- the second putting channels 1 and 3 on the 1.79 MHz clock,
which is what pushes channel 3 into octave 8 and above in the capture.

That fast clock is also what found the last bug in the note code: pitches that
high name octaves of two digits, and the note parser accepted only one, so it
returned `None` and the capture crashed. Sub-audio pitches had the mirror
problem -- `name_of` prints `E-2` for octave −2, but a leading `-` is the
tracker's "no accidental" filler, so that reads back as E2. Two-digit octaves
now parse, negative ones fall back to the raw divider, and the round-trip check
rejects anything that would not read back identically.

## Two POKEYs: eight voices, and MAME plays four

Eighteen images declare a second POKEY at `$0440`. They are **not a stereo
pair**. Tapping both chips on Byzex and Acidjazzed:

```
Byzex       frames same=205  differ=1295     (86% of frames)
Acidjazzed  frames same=202  differ=1298
```

The two chips are written different values in most frames, so a dual-POKEY
cartridge is eight independent voices and reading one chip loses half the music.
All eight of Byzex's voices sound, with 125, 42, 18, 8, 8, 18, 92 and 39
distinct settings each.

### The emulator does not help here

MAME v0.287 has no cartridge type for the second chip:

```
Unsupported mapper, please contact MAMEdevs (Header 0x440)
Starting Atari 7800 ROM Carts w/POKEY @ 0x0450
```

It loads these as single-POKEY carts and **discards** the `$0440` writes. (An
earlier note here said `$0440` mirrored `$0450`. It does not -- that reading
came from CPU-bus taps catching writes MAME then threw away.)

That turns out not to matter, because `probes/audio.lua` taps the **CPU bus**,
not the chip, and the renderer works from the register stream rather than from
MAME's audio. So the second chip's music is fully recoverable even though the
emulator will not play it. Nothing extra is needed:

```
python tools/capture.py game.a78 --render
  sound      POKEY2 at $0440, $0450
             (two POKEYs, both captured -- eight voices. MAME plays only the
              first, so its own audio will be missing half of this; the
              toolkit renders from the registers and does not care.)
```

### Each chip is its own machine

AUDCTL, the 16-bit pairs and the high-pass filters are all chip-local, so the
renderer treats a two-chip song as two independent groups of four and mixes at
the end. Pairing channel 3 of one chip with channel 0 of the next would be wrong
in a way that still sounds like music.

The file format carries one AUDCTL per chip:

```
chip pokey2
audctl $10 $04
```

and `Song.ctl_of(row, ch)` is how a channel gets the AUDCTL of the chip it
actually lives on. That method exists because `chip == "pokey"` was scattered
through the code, and every one of those comparisons quietly became "exactly one
POKEY" the moment the two-chip name arrived -- an eight-voice song was briefly
being read with the TIA's five-bit divider.

### What is checked, and what is not

* **The capture replays exactly.** 969 logged frames of Byzex, **0 register
  mismatches** across all eight voices and both AUDCTLs.
* **The file round-trips**, two AUDCTLs included.
* **Each chip renders through the model verified bit-for-bit against
  hardware**, so both halves are as trustworthy as the single-chip case.
* **The mix is not verified against an emulator, because none plays both
  chips.** Every part is checked; the sum is not. That is the honest limit and
  it will stay until MAME grows the cartridge type.

`export` refuses a two-chip song rather than emitting the single-chip player,
which would assemble, run, and play half the music at the wrong settings.
## Capturing POKEY

Same probe, with the base address from the header:

```
python tools/cart.py game.a78                    # find the POKEY line
A7800_POKEY=0x0450 mame a7800 -cart game.a78     -autoboot_script probes/audio.lua -sound none -video none -str 40
python tools/tracker.py capture a7800-audio.log -o game.trk
```

The log names its own chip, so `capture` needs no flag. Pass `-sound none`:
the log comes from the emulation, not the speakers, and without it a
`-nothrottle` run plays the session at twenty times speed through your audio
device.

## How much of this is verified

Honestly less than the TIA side, and the difference matters.

**Verified.** Frequencies against the divider formula at both base clocks and
on the fast clock. The polynomial counters are maximal-length (15, 31 and
131,071 steps). Capture round trip: a 1,795-frame capture from a running POKEY
game converted to a tracker file and replayed reproduces **every logged frame's
exact register state**. That test is what caught note names being written for
dividers they do not uniquely identify -- with 256 dividers several share a
note, so a name can be perfectly readable and still change the tune on reload.
Names are now only used where re-reading them gives back the same divider.

**Not verified: the sound itself, against hardware.** The TIA model could be
checked sample-for-sample against a renderer confirmed by ear on a real game.
There is no such reference for POKEY here, and the obvious substitute did not
work: **MAME's `-wavwrite` records no cartridge POKEY audio in the build used**,
while recording TIA audio from the same machine perfectly. That was checked
three ways -- a purpose-built 4K test cartridge that holds one register setting
and loops, plus Ballblazer and a POKEY music demo -- all silent in the WAV,
while Midnight Mutants records at peak 21392. The chip is present and live:
MAME instantiates `:cartslot:a78_pokey:pokey`, the test ROM's writes land, and
reading its RANDOM register gives 29 distinct values over 30 frames.

So the distortion mapping and the relative levels rest on documentation rather
than on measurement. If you can play a rendered WAV beside the same game in an
emulator with working POKEY output, that comparison is the missing check.

# Finding the music in the ROM

`tracker.py capture` watches a game play. `audiotrace.py` does the other half:
it reads the code.

```
python tools/audiotrace.py game.a78 [-c annotations.json] [--json]
```

It disassembles the cartridge, finds every store to a TIA or POKEY audio
register, walks backwards through straight-line code to whatever supplies the
value, and reports the tables that turns up. Against Midnight Mutants, with only
the annotations that give the disassembler its entry points -- nothing in them
mentions sound (two of the four routines are elided here for length):

```
23 audio stores in 4 routines, from 8.8% of the ROM traced

b0:$A00B-$A055  10 stores, TIA: AUDC0 AUDC1 AUDF0 AUDF1 AUDV0 AUDV1
    table $A05C,X (through a pointer) -> AUDC0 AUDF0

f6:$754C-$772E  9 stores, TIA: AUDC0 AUDF0 AUDV0
    table $76F6,Y (direct)         -> AUDC0
    table $7744,Y (direct)         -> AUDV0
    table $774C,Y (direct)         -> AUDF0
```

Those are the right answers. `$76F6`, `$7744` and `$A05C` are the waveform
table, the instrument table and the sound-effect pointer table -- the same
addresses that took a long time to find by hand, and `$774C` is the instrument
table's second column. The two routines it separates are the music player and
the effects player, split purely on how far apart the stores are.

Run bare, with no annotations at all, it still finds 15 of those stores and two
of the four tables -- `$76F6` and `$774C`, both in the music player. The entry
points are what raise it to 23 stores and add the instrument table and the
sound-effect table. Worth knowing which way that dependency runs: annotations
improve the report, but the player is found without them.

`--json` prints the tables as annotation blocks, ready to paste into a config
so the disassembler stops treating them as unreached code.

## What it will not do

**Reconstruct the song.** Every player invents its own format. Midnight Mutants
nests song, track, pattern and note four levels deep and packs an instrument
and a duration index into one byte; nothing portable can guess that, and a tool
claiming otherwise is guessing. What you get is where the data is and what it
looks like. Working out the shape is the actual reverse engineering, and
`capture` is how you check a guess -- if your reading of the tables and the
game's own output disagree, your reading is wrong.

**Find a player the disassembler never reached.** This is the limit that bites,
and the report says so rather than letting a quiet result read as a verdict:

```
Only 3.2% of this ROM was traced, and 1 of the 1 routines write
nothing but constants. That reads like "no music", but it is far more
likely the player was never reached...
```

Food Fight traces at 3.2% and Ballblazer at 17.3%, so on both, `audiotrace`
finds only the silence-on-startup writes -- while `capture` records 260 and 730
changed frames respectively. Nothing is wrong with either tool: a static trace
cannot follow an indirect jump or a handler installed through a RAM vector, and
a running machine does not care how the code was reached.

Note that the whole-ROM figure is not the thing that decides it -- Midnight
Mutants succeeds at 8.8%, below Ballblazer's 17.3%. What matters is coverage of
the bank the player lives in, and on a 160K cartridge seven idle banks drag the
average down without meaning anything. Midnight Mutants reaches **54% on `f6`**,
which is where its player is. The number to read is the one for the bank you
care about; the whole-ROM figure is printed as a floor, and it is the
combination of a low floor *and* nothing but constant writes that makes a quiet
report untrustworthy. That is the pair the warning tests.

To improve coverage, give the disassembler a way in -- an address in `entries`,
or a `ram_vectors` pair if the handler is installed into RAM. That is exactly
what gets `f6` to 54%.

# Pulling a song out of a ROM, and putting it back

`audiotrace.py` finds the tables. `songfmt.py` reads them as a song, and writes
an edited song back into the cartridge.

```
python tools/songfmt.py pull game.a78 -f formats/mm-tia.json -o song.json
python tools/songfmt.py push game.a78 -f formats/mm-tia.json -i song.json -o edited.a78
```

It knows nothing about any particular game. Everything specific lives in the
format file, which says where the song table is, how a track is terminated, how
a pattern counts its notes and which bits of a note byte mean what:

```json
"note": {
  "fields": {
    "instrument": [0, 4, 4],
    "duration":   [0, 0, 4],
    "waveform":   [1, 5, 3],
    "pitch":      [1, 0, 5]
  },
  "_fields": "name: [byte, lowest bit, width]",
  "rest_when": "byte1 == 0"
}
```

`formats/mm-tia.json` is the worked example: Midnight Mutants, four levels deep
-- song names two tracks, a track lists patterns, a pattern is a count and that
many notes, a note packs an instrument and a duration index into one byte and
steals AUDF's three unused high bits for the waveform. Writing one of these for
a new game is the reverse engineering; the tool is what stops you doing it with
a hex editor.

## Patterns are shared, and that is not a detail

Midnight Mutants' twelve songs make **65 pattern references that resolve to 35
distinct patterns**. The pull holds each pattern once, keyed by address, and
records which voices name it:

```json
"b3:8CEC": { "count": 72, "used_by": ["song 6 voice 0", "song 6 voice 0"], ... }
```

The first version kept a copy per reference, and it was quietly wrong in the
worst way: pushing wrote every copy in turn, so a pattern used twice had the
last *unedited* copy land on top of the edit. It round-tripped perfectly and
silently discarded your work. One pattern, one entry, one write.

The corollary is worth saying out loud: editing a shared pattern changes every
song that names it. The pull tells you which, and the push repeats it in the
write log.

## Hearing what you pulled

A pull carries the tables the notes lean on -- durations, waveforms and
instruments -- so the song is self-contained and does not need the cartridge
again:

```
python tools/songfmt.py render song.json -n 6 -o song.trk
python tools/tracker.py render song.trk -o song.wav
```

That `.trk` is an ordinary tracker file: `trackeredit.py` opens it, you can edit
it, and `songfmt push` puts an edited song back in the ROM. Before this existed
the only way to hear a pulled song was to push it into a cartridge and run the
emulator, which is a slow loop and, as it turned out, an easy one to fool
yourself with.

### The envelope is a program, not a layout

Everything else about a player is layout -- where a table is, which bits of a
byte are the pitch -- and layout can be described declaratively. Volume over
time cannot: it is a state machine that runs once a frame. So the format file
**names an engine** rather than describing one, and an engine `songfmt` does
not implement is refused:

```
this format's instrument engine is None, which is not implemented. Rendering it
would mean inventing an envelope, and a song with the wrong envelope sounds
convincing and is wrong. Implemented: adsr5.
```

`adsr5` is the five-stage envelope Midnight Mutants runs: attack, decay,
sustain, release, off. Two details in it are worth knowing, because both are
audible and neither is guessable:

* **Volume is an 8-bit accumulator whose high nibble reaches the 4-bit AUDV.**
  A decay decrement of `$38` a frame is therefore a slope, not a step. Instrument
  2 runs `8 8 4 1 0 0 0 ...` -- four frames of sound in a sixteen-frame note,
  and the gap is where the articulation comes from.
* **The sustain stage writes no volume at all.** That is why a sound effect can
  leave a hole in the music: it sets `AUDV0 = 0` on its way out and the melody,
  sitting in sustain, does not write it back until the next note-on.

Pull and push work without an `instruments` section. Only `render` needs one.

### Checking a format file is right

`--verify` renders the song and compares it against a capture of the same game
frame by frame:

```
$ python tools/songfmt.py render song.json -n 6 -o song.trk --verify capture.log
  verify: best fit at capture frame 1097 -- 703 of 703 frames identical (100.0%)
```

**703 of 703.** The registers the renderer computes from the ROM bytes are the
registers the hardware wrote, for every frame the tune plays.

It searches for the alignment rather than assuming frame 0, because a capture
opens on however long the game takes to reach the tune -- eighteen seconds of
title screen, here -- and comparing frame 0 to frame 0 compares silence against
music and reports 0%. The frame it landed on is printed so the number can be
judged.

This is the check worth running on any new format file. A wrong bitfield, a
wrong duration table, a bank mapped wrongly: all of them still produce a song,
and most of them still sound like music. Only the comparison against the machine
tells you which.

## What it refuses to do

A pattern occupies the bytes it occupies. Whatever follows belongs to something
else -- another pattern, a table, code -- so:

* **Adding notes is refused.** `pattern b3:8CEC now needs 146 bytes but owns
  144. A pattern cannot grow: whatever follows it in the ROM is not yours.`
* **Removing notes is refused** unless you pass `--allow-shrink`, which pads the
  tail with rests. Without it, a shortened pattern would leave stale bytes the
  player still reads.
* **Every write is checked against the spans the format declared.** After
  building the image, push diffs it against the original and refuses, with exit
  3, if a single byte outside those spans changed.

That is the "limited" half of reversible, and it is deliberate. Anything that
moves data has to repoint whatever names it, and a tool that guesses at that
turns a music edit into a corrupted cartridge. To make a pattern longer, put it
in free space and repoint the track by hand.

## How this was checked

* **No-op round trip.** Pull, push without editing: **0 bytes changed**.
* **Edited push.** 72 bytes changed, every one inside the 2,693 bytes the format
  owns; pulling the result back gives exactly the song that was pushed.
* **Growth and shrink both refused**, with the messages above.
* **The machine reads the edited bytes.** A MAME read tap on the pattern's
  address range recorded 94 reads in both images, **46 returning a different
  byte** -- `5F`→`59`, `5D`→`57`, the exact edit.
* **The cartridge plays them.** Capturing both images for 30 seconds: 398 frames
  logged in both, **307 with different audio registers**, AUDF0 carrying the
  same `5F`→`59` and `5D`→`57`. Rendered to WAV, 510,756 samples differ.

That last pair took three failed attempts, all of which reported zero
difference, and all of which were the test being wrong rather than the tool:
the patterns being edited were ones the captured window never played. The read
tap is what settled it -- it showed reads landing on `8CEC` and never on the
addresses under test. When verifying an edit reaches the hardware, check that
the data is *read* before concluding anything from what you hear.

# Getting a tune in from MIDI

```
python tools/midi.py song.mid                        # what is in it
python tools/tracker.py import song.mid --chip pokey -o song.trk
```

`midi.py` reads a Standard MIDI File far enough to get notes out: tracks with
names, note ranges, how polyphonic each is, and timing in seconds. It handles
the two things that quietly break naive parsers -- **running status**, where an
event omits its status byte and inherits the last one, and **tempo changes**,
which alter what a tick is worth partway through. A note-on with velocity 0 is
a note-off, too; treating it as an on leaves every note sounding forever.

```
test.mid -- format 1, 480 ticks per quarter note, 2 tempo changes

  #   name                      notes  poly range       seconds
  1   lead                          5     1 C4-G4       2.5
  2   chord                         3     3 C3-G3       1.0
```

`poly` is the number to look at before importing: **a 7800 voice plays one note
at a time.** A track showing 3 will lose two of every chord.

## What the import costs, said out loud

The conversion is lossy in two specific ways, and both are counted rather than
absorbed:

```
  voice 1  <- track 1 lead                 5 notes
  voice 2  <- track 2 chord                3 notes, 120 overlapping dropped
  tuning   210 frames placed; median error 2 cents, worst 10
```

**Polyphony.** One note per voice. `--pick high` (default), `low` or `first`
chooses which survives, and the rest are counted in the report.

**Tuning, and this is the big one.** The same file onto the TIA:

```
  tuning   210 frames placed; median error 46 cents, worst 113
           120 of them (57%) are more than 25 cents out
```

Two cents on POKEY against forty-six on the TIA is not a bug in the importer.
The TIA is not tuned to anything: its five-bit divider produces a scatter of
pitches, and most notes a composer would write simply do not exist on it. Half
a semitone out is what transcription onto it actually sounds like.

So for POKEY, importing a melody works. For the TIA, the import is best read as
a measurement of how far your tune is from what the chip can play -- and
`tracker.py notes` lists the pitches that *are* there, which is what a melody
for it has to be written around rather than transcribed onto.

## Importing into a song you are already working on

`trackeredit.py` has the same import behind an **Import MIDI** button, and this
is the shape that gets used: **one track into one voice, leaving the rest of
the song alone.** Pick a file, get the track list, click the voice you want it
in.

```
track          notes  poly  range    secs   into voice
1 lead             5     1  C4-G4     2.5   [ch1] [ch2] [ch3] [ch4]
2 chord            3     3  C3-G3     1.0   [ch1] [ch2] [ch3] [ch4]
```

`poly` is highlighted when it is above 1, because that is the column that tells
you what the import is about to throw away. Every import reports back what it
cost:

```
ch1: 150 frames placed, median 3 cents out (worst 10)
ch2: 60 frames placed, median 2 cents out; 120 overlapping notes dropped
```

Other voices are untouched, which is checked in the self-test rather than
asserted here -- importing a part beside one you already have is the whole
point, and quietly overwriting the rest would ruin it.

# Opening a cartridge in the tracker: read it, do not record it

```
python tools/trackeredit.py game.a78
```

Handed a cartridge, the tracker now tries the **ROM** first:

```
orig_mm.a78 -- read from the ROM with mm-tia.json, no emulator
   song 0    960 rows   16.0 s  64 sounding
   song 1   3712 rows   61.9 s  2859 sounding
   ...
   song 10   384 rows    6.4 s  377 sounding
   opening song 1, the one with the most in it -- --song-number picks another
```

Eleven songs, in a moment, with nothing emulated. Compare that with recording,
which gets **one** song -- whichever happened to play in the window -- after a
minute of emulation, and only if the game reached it at all.

## How it knows: fingerprint the player, not the title

```json
"match": { "player": "7e91de9d794b" }
```

That is a hash of the bytes around the cartridge's audio-register writes, from
`audiotrace.py --signature`. It is the right key because **a format file
describes an engine, and the engine is what recurs.** Matching on the title
would catch the six images called "Midnight Mutants" and miss every hack and
rebuild that shares the identical player.

`title` and `size` still work and are checked when present, but the player
fingerprint is the one to reach for.

### How much recurs, measured

Fingerprinting every image in the library:

| | |
|---|---|
| images scanned | 2,665 |
| images with a recognisable audio player | 841 |
| distinct player fingerprints | 166 (in the first 900 scanned) |
| fingerprints spanning **more than one game** | 40 |
| images those cover | **373, across 186 distinct titles** |

The largest cluster is one player shared by **27 different titles** -- Arkanoid,
Donkey Kong, the whole Mario Bros sound-demo set, the AtariAge jingle. One
format file for that engine would open 58 images. The dual-POKEY set
(Acidjazzed, Byzex, OutRun, OutRun 2, Popcorn, White Lamp) is another six titles
on one player.

That is the case for working engine by engine rather than game by game.

### A fingerprint is not a promise

It says "same music engine". It does not say "same addresses". A regional
release or a rebuild can share the player and keep its tables somewhere else --
Midnight Mutants' European release does exactly that, and additionally
understates its own mapper in the header:

```
mm-tia.json matched this cartridge, but reading it failed:
   this cartridge has no space 'f6'. It declares cart type $0002, which maps: b0, b1, ...
It describes the player, not this layout. Recording instead.
```

So a match is a *hypothesis*, and failing it is a reason to fall back to
recording rather than to stop. Worth remembering when a format file lands on a
cartridge it was not written against: check the songs it produces before
trusting them.

`--format` names one explicitly, `--song-number` picks which song to open, and
`--capture` forces the emulator anyway.

## When nothing describes the cartridge

Most cartridges have no format file, and the tracker says what it can before
falling back:

```
No format file describes this cartridge, so its songs cannot be read from
the ROM. audiotrace can still see where the music lives:
   f6:$76F6 -> AUDC0
   f6:$774C -> AUDF0
Turning those tables into songs needs a format description -- see
templates/format.json. Recording the game instead:
```

That is the honest division of labour. `audiotrace` finds *where* the music is
without knowing anything about the game; turning those addresses into songs
needs someone to work out the format, which is what `templates/format.json` is
for. Until then, recording is what there is.

# Working out a player, engine first

The 27-title cluster turned out to be tractable, and the method generalises.

## Find the engine, not the game

`audiotrace.py --signature` fingerprints a cartridge's audio-write code. Run it
over the library and the clusters fall out; the largest was 58 images across 27
titles. **Start there**, not with whichever game you happened to be curious
about.

## Read it

The disassembler traced only 3.7% of that ROM -- the player sits behind an
indirect jump, the usual case. Scanning the bytes for stores to POKEY registers
found it anyway, in about a second:

```
$C1BC  STA abs,Y  AUDC1
$C21B  STA abs,Y  AUDF1
$C278  STA abs,Y  AUDC1
```

Disassembling that stretch gave the whole player in fifty lines.

## What it turned out to be

Nothing like Midnight Mutants. **Two parallel byte streams per voice** -- one
pitch and one duration per note, in *separate arrays*, read through pointers
that advance together. A zero in the duration stream ends the part. No
patterns, no bitfields, no song table.

The envelope is a linear decay of the whole AUDC byte: POKEY keeps volume in
the low nibble, so subtracting one fades the note, and how often comes from a
table indexed by the note's own duration. Long notes fade slowly.

That is why `songfmt` describes players with a file rather than assuming one --
and why it now has several. `reader: "parallel"` selects this shape;
omitting it keeps the nested song/track/pattern/note default.

A detail that confirmed the reading before any test did: voices 2 and 3 carry
the same melody one AUDF apart. That is a detuning trick, and it is not
something a misparse produces.

## The same engine on a different chip

Commando's music sat unread for a while because the scan pointed at the wrong
thing. `audiotrace` found tables at `b6:$A951` and reported them, and they are
real -- but they are the *instrument* table, sixteen records of sixteen bytes
with only the first ten used. Nothing there is a note. The reasonable
conclusion from a short table of unfamiliar bytes is that the cartridge holds
some small sound effect and not much else, and that is exactly the wrong
conclusion.

What settled it was not a better decoder but a better prior: Atari-era houses
shared their sound code, so the first thing to check is whether an unknown
player is a known player. Commando's is Midnight Mutants'.

The evidence is layout, not vibes. Both keep a 16-entry duration table
immediately before 16 instrument records of 16 bytes; Midnight Mutants at
`f6:$7734` and `f6:$7744`, Commando at `b6:$A941` and `b6:$A951`, sixteen bytes
apart in both. Both end a pointer list with an entry whose high byte is zero.
Both store patterns count-prefixed with fixed-size notes, and pack the note as
an instrument in the high nibble of the first byte and a duration index in the
low one. Both run the same five-stage envelope over an 8-bit volume
accumulator whose high nibble reaches the chip.

What differs is forced by the chip, and is worth stating because it is the
part that does not transfer. TIA's AUDF is five bits wide, so Midnight Mutants
has three bits going spare in its pitch byte and spends them on an index into
a waveform table. POKEY's AUDF is the whole byte -- pitches here run from $00
to $F3 -- so there is no room, and the control value comes from the instrument
instead. `audc_from: instrument` selects that. Same idea, different budget.

Two things that only a capture could have caught, both of which produced music
that sounded fine:

**POKEY keeps distortion in bits 7-5.** Reading the instrument's control byte
with a four-bit shift instead of five turns $A0 into distortion $A, which does
not exist, and every note comes out on the wrong waveform. Agreement with the
hardware was 86.6% -- high enough to look like rounding error somewhere, low
enough to be completely wrong.

**A frame's byte belongs to the stage the envelope was in when the frame
began.** Reading the stage after advancing it silences the timbre one frame
early. That moved agreement from 99.1% to 99.2% and looked like a fix; it had
actually shifted the error rather than removed it, which the frame-level diff
showed and a percentage never would.

With both corrected, song 0 matches the running machine on 576 of 576 frames
across all four voices -- every AUDC, AUDF and volume, every frame. Song 1
follows in the same capture at 94 of 96; the last two frames are the player
cutting a note short to start what comes next, which is sequencing above the
level a song describes.

Eleven songs, 43 patterns, 1,398 notes, and a no-op push that changes zero
bytes.

## Hunting the engine, not the game

Two format files took a long time each, because each meant reading a player's
code. That does not scale to a library of 2,700 images, and it does not need
to: the same engine turns up again and again. Midnight Mutants and Commando
run the *same one*, and their duration tables are byte-for-byte identical --

```
60 48 40 30 24 20 18 12 10 0C 09 08 06 04 03 02
```

-- as are Alien Brigade's, Fatal Run's and Meltdown's. Atari-era houses shared
their sound code, and this is what that looks like from the ROM side.

`audiotrace.py --engine` searches for it structurally, so it works on a
cartridge whose player the tracer never reaches:

```
python tools/audiotrace.py game.a78 --engine [--emit]

  durations   b6:$A941   60 48 40 30 24 20 18 12 10 0C 09 08 06 04 03 02
  instruments b6:$A951   16 x 16 bytes, 16 rows used
  song table  b6:$B640   11 songs, 4 voices, stride 8
  11 of them followed all the way down to real patterns
```

`--emit` writes the format file.

### Why it is allowed to search at all

Guessing at structure is how you produce a song from the wrong bytes that
sounds like music. What makes this safe is that every step is checked against
the next, and the chain is long:

- Sixteen 16-byte instrument records whose **last six bytes are always zero**.
- A **sixteen-entry duration table immediately before them**, every entry
  non-zero and strictly descending. Sixteen bytes of anything else almost
  never satisfies that.
- A song table whose voice pointers reach **pointer lists ended by a
  high-byte-zero entry**, whose entries are **count-prefixed patterns whose
  counts fit inside the bank**.

A wrong instrument table gives a duration table that is not descending. A
wrong song table gives pointers that land on nothing. The search reports only
what survives all of it, and says how many songs it followed the whole way
down.

The one thing it cannot find by shape is the **waveform table**: a TIA player
spends the top three bits of its pitch byte on an index into eight arbitrary
values sitting in the middle of instructions. Nothing about those bytes says
"table". So it asks the tracer instead, which finds it the way the player
reaches it -- `LDA table,X / STA AUDC0`. That is why both halves live in one
file.

### What came of it

Five cartridges were put through the search and then checked against captures
of themselves, which is the only thing that settles whether a description is
right:

| cartridge | agreement with the machine |
|---|---|
| Commando | 576/576 frames, all four voices |
| Missing In Action | 2358/2358 frames |
| Meltdown | 846/846 frames |
| Fatal Run | 487/487 frames of pitch and waveform; 85.2% including volume |
| Alien Brigade | 71% at best -- **not accepted** |

Fatal Run's caveat is worth stating rather than rounding away. Its notes are
exactly right; the shortfall is entirely in AUDV, because this engine's
sustain stage writes no volume at all, so a sound effect playing over the
music leaves holes in the recording. Midnight Mutants, whose description was
confirmed long before any of this, behaves identically against its own capture
-- 87.96% including volume, 96.79% on pitch and waveform. A capture with
effects in it cannot score 100%, and treating that as a failure would reject
two descriptions that are correct.

Alien Brigade is the one that did not clear the bar, at 71% on pitch and
waveform, and it is not in `formats/`. The search finds its instrument and
duration tables confidently -- its duration table is byte-identical to the
others -- so the engine is certainly there; something about the song table or
the voice grouping is still wrong. A structurally valid table is not
necessarily *the* table, which is the whole reason for checking against the
machine rather than against itself.

Three new format files came out of this, all fingerprinted by player so they
cover the whole engine family rather than one image each.

### Where it is uncertain, and how it says so

**Which bank a pointer resolves against is runtime state.** On a bank-switched
cartridge a track pointer in `$8000-$BFFF` means a different byte in every
bank, and which one was mapped when the song started is not in the ROM. The
search tries each and keeps one where every voice resolves -- but where more
than one does, the choice is a coin flip, and a wrong bank does not fail. It
produces a song, from the wrong bytes, that sounds like music.

So it records which songs were ambiguous, in the format file, by number. On
Midnight Mutants it chooses `{0:3, 3:0, 4:3, 5:4, 6:3}` against the
hand-written `{0:3, 3:0, 4:3, 5:5, 6:3}` -- four of five right, and song 5,
the one it got wrong, is one of the two it flagged.

**Structure verified is not sound verified.** The emitted file says so, and
names the command that settles it. Commando's hand-written description matches
its hardware on 576 of 576 frames across four voices; the auto-generated one
for the same cartridge pulls the identical 43 patterns and 1,398 notes and
scores the same 100%. That is the standard, and nothing that has not been
through it should be trusted further than a strong lead.

## A fourth reader, for what an ear can establish

The three readers above describe *players*: where the song table sits, how a
track points at a pattern, which bits of a note byte hold the pitch. Each was
written after reading the player's own code, and that is what makes them
trustworthy and what makes them expensive. Two exist. The library holds
hundreds of engines.

`explore.py` attacks the same problem from the other end. It reads a stretch of
bytes, proposes layouts of them, renders each to audio, and lets a person
listen. Music is unmistakable when you hear it and invisible when you stare at
it, so the ear is the right instrument; the tool just puts it in reach.

What that establishes is real but partial. You can hear that the records are
two bytes and that the pitch is in the second. You cannot hear where the song
table is, because nothing in the audio depends on it. So `reader: "direct"`
records exactly the part an ear can settle -- one voice, one stretch of notes,
at an address someone worked out -- and nothing more:

```json
{
  "reader": "direct",
  "guessed": true,
  "match": { "player": "5f0099c74376" },
  "voice": { "shape": "serial", "at": "f6:7D23", "stride": 2,
             "pitch": [1, 0, 8], "duration": [0, 0, 4], "count": 22 }
}
```

Three things about that file matter more than its contents.

**It plays.** The tracker opens it from the ROM like any other format, which
means the next person can check it in seconds instead of starting from a hex
dump. A finding you cannot hear is a finding you cannot check.

**It is filed under the player, not the game.** The `match` block carries the
player fingerprint, so a reading worked out on one cartridge is offered for
every cartridge built on the same engine. That is the whole reason the
signature exists: one signature in the shipped set covers 53 images.

**It says it is a guess.** `guessed: true` is in the file, the workbench labels
it as a reading worked out by ear rather than a description of the player, and
`find_format` will never let one shadow a real format file however the
filenames sort. This matters because a guessed format is dangerous in a
specific way: it plays, so it looks exactly as authoritative as a description
written from the player's code, and it is not.

The danger is the one the whole format system was built to avoid -- borrowing
the wrong description does not fail loudly, it produces a song, from the wrong
bytes, that looks entirely plausible. A guess that sounds right is a
hypothesis. Confirm it with `tracker.py capture`, which records what the
cartridge actually plays; if the reading and the machine disagree, the reading
is wrong. That is how both of the real format files here were confirmed.

## Check it against the machine

```
Arkanoid (Level Start Music Demo)     AtariAge Jingle
  voice 0: 150 of 150 (100.0%)          voice 0: 310 of 310 (100.0%)
  voice 1: 150 of 150 (100.0%)          voice 1: 310 of 310 (100.0%)
  voice 2: 160 of 160 (100.0%)          voice 2: 310 of 310 (100.0%)
  voice 3: 160 of 160 (100.0%)          voice 3: 310 of 310 (100.0%)
```

Two different titles, every voice exact, and all four aligning at the same
frame -- which is itself corroboration, since the voices are read independently.

## What one file bought

**58 of 58** images on that player read cleanly from `formats/aa-pokey.json`.
Together with Midnight Mutants:

| | |
|---|---|
| images with a recognisable player | 841 |
| covered by a format file | **111 (13%)** |
| `aa-pokey.json` | 58 images, 27 titles |
| `mm-tia.json` | 53 images |

Two files, two evenings' worth of work, an eighth of the library. The next
clusters are smaller but the method is the same, and the dual-POKEY set
(Acidjazzed, Byzex, OutRun, Popcorn) is worth checking against RMT before
reverse-engineering anything -- that format is published.

# RMT: identified, described, and not decoded

The dual-POKEY cluster turned out not to need reverse-engineering at all. Its
ROMs begin with four bytes that say what they are:

```
first 24 bytes: 52 4D 54 38 40 06 04 01 10 80 24 80 19 81 52 8C ...
as text       : RMT8@.....$...R.
```

**Raster Music Tracker**, the Atari 8-bit POKEY tracker, embedded whole. Worth
checking for a published format before reading any code -- that was the right
instinct and it saved a disassembly.

## Match the container, not the player

Searching the library for the magic found **84 images across 41 titles** -- far
more than the 12-image cluster that led here, because the same module format
sits behind several different player builds. So `formats/rmt.json` matches on
`magic` rather than on a player fingerprint:

```json
"match": { "magic": "RMT" }
```

Keying on the player would have split one format across several descriptions
and missed every game that embeds a module without a recognisable player.

## The origin has to come from the module

A first pass read only 48 of the 84, and the failures were uniform: all four
pointers out of range. RMT's pointers are **absolute addresses for wherever the
module was assembled to live**, which need not be where it sits in the ROM -- a
banked cartridge, or one that copies the module into RAM, puts it elsewhere.

The module says where it thinks it is: the instrument table always follows the
16-byte header, so its pointer minus 16 is the origin. Taking it from there
instead of from the ROM took it to **84 of 84**, with origins spread across
`$8000`, `$4000`, `$D000` and `$A800` -- which is the confirmation that the
inference is real and not a coincidence.

## Where this stops, and why

```
$ python tools/songfmt.py pull game.a78 -f formats/rmt.json
RMT8 module at rom:8000 -- 8 channels, 64 lines a pattern, speed 6/4
   instruments            $8010
   track pointers (low)   $8024
   track pointers (high)  $8119
   song                   $8C52
245 tracks, 14 rows of song order

Not renderable, and that is a limit rather than a bug:
RMT's sound comes from its instrument engine -- per-instrument envelope
tables for volume, distortion and pitch, plus effects. Reading the note
list without it would give a song that looks right and plays wrong, so
this reports the structure and stops.
```

The note list is only half an RMT tune; the rest is the instrument engine, and
reproducing it means reimplementing the replayer rather than parsing a table.
That is a real piece of work and it is not done, so the tool says so and points
at `capture`, which hears the tune without understanding it.

## Coverage

| | |
|---|---|
| images with a player or an identifiable module | 841 |
| described by a format file | **195 (23%)** |
| `rmt.json` | 84 images, 41 titles |
| `aa-pokey.json` | 58 images, 27 titles |
| `mm-tia.json` | 53 images |

Three files. Two of them render; the third identifies.

# "It found the audio tables but says there is no format" — both are true

This pairing confuses on sight and is worth stating plainly, because the two
halves answer different questions.

Open Commando in the workbench and you get:

```
music   no format file describes this cartridge
audio (3 tables)
   b6:A951   read by the sound code (AUDC1)
   b6:A959   read by the sound code (AUDF1)
   b6:AA51   read by the sound code (AUDCTL)
```

**The tables are where the sound data is.** `audiotrace` follows the code that
writes to the chip back to whatever feeds it, so those three addresses are real
and were found without knowing anything about Commando. They are the tables the
player reads for waveforms, pitches and control values.

**The format is what the bytes mean as music.** Which notes, in what order, for
how long, how they are grouped into patterns and songs -- that is the player's
own invention, and nothing in `formats/` describes Commando's. Finding a table
tells you nothing about the structure that indexes it.

So: `audiotrace` gets you to the data. `songfmt` reads it, but only if someone
has written the description. Between the two sits the actual reverse
engineering, and `templates/format.json` is where that starts.

Until then the tracker records the game instead, which needs no format at all --
the register writes *are* the music, whatever produced them.

## Is Commando worth describing?

Its player fingerprint appears on **one title**. Compare the clusters: 84 images
share the RMT container, 58 share the parallel-stream player, 53 share Midnight
Mutants'. A format file for Commando would open Commando.

That is a perfectly good reason to write one -- if Commando is the game you care
about. It is a poor reason if the goal is coverage. `audiotrace.py --signature`
tells you which of the two you are looking at before you start.
