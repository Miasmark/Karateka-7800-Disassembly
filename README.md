# Atari 7800 disassembly toolkit

Tools and hard-won notes for taking apart 7800 cartridges, extracted from a
complete byte-identical disassembly of a 128K commercial game.

Nothing here is specific to that game. The cartridge model was tested against
**2,664 retail and homebrew images** and lays out all but four of them — including Activision's 8K-granular mapper and bankset cartridges, whose two halves are read separately with `side=`; the
disassembler reproduces the hand-verified 128K disassembly byte for byte while
also handling unbanked 4K-48K ROMs.

MIT licensed, which covers the code and the notes. It cannot grant rights
over the games the tools analyse, and it does not need to -- see `NOTICE`
and the section below.

## No ROM ships with this

Not one cartridge byte is in this repository, and that is enforced rather
than promised: `.gitignore` refuses every ROM extension, `portkit.py`
refuses a recipe carrying embedded data, and the patch-set format ships
section CRCs instead of the bytes they check. Decoded artwork counts as
cartridge bytes too, so the rendered sprite sheets and screenshots are
left out as well -- every one of them regenerates from your own dump.

Supply your own copy of the game you are working on. For the Karateka
work specifically, that is the NTSC release, crc32 `FEC21472`:

```
export KARATEKA_ROM="/path/to/Karateka (NTSC) (Atari) (1987) (FEC21472).a78"
python patches/karateka.py --build
```

The tools also look for it beside the toolkit and a few levels down from
the parent directory, so a normal library layout usually needs no
configuration at all.

## The patches are in `dist/`

Fifty BPS patches and one `.abp` bundle, and they are there because both
formats exist to travel without the game. Apply one to your own dump:

```
python tools/bps.py apply karateka.bin dist/karateka-45-tweak.bps out.bin
```

`karateka-45-tweak` is the one to start with -- faster reactions, knockback
that spends the hall's own travel budget, remapped controls, a working
difficulty switch, and the stance check the port left out of its ending.
`-46-` and `-47-` are the same with a longer walking step.

The bundle is the pick-and-mix version, which refuses a selection that
cannot mean one thing rather than resolving it by file order:

```
python tools/patchset.py list dist/karateka.abp
python tools/patchset.py apply dist/karateka.abp --rom karateka.bin        --with knockback-light,remap --out out.bin
```

Either way the result comes out with a valid NTSC cartridge signature, so
it boots on real hardware and not just in an emulator -- see
`tools/sign7800.py` for why that is not automatic.

**None of these carries a byte of the game**, and that is checked rather
than asserted: `selftest.py` decodes every published patch and compares
each stored literal against the original at the same address, requiring
zero matches, and confirms the bundle's sections describe their pre-image
with a CRC32 instead of quoting it. A BPS emits a literal only for a run
that differs, so every stored byte is authored; across `dist/` that is
17,462 literals and no match. The check has a negative control: plant one
cartridge byte in a patch and re-seal it, and it fires.

## Start here

```
python tools/workbench.py game.a78             # open everything at once
```

That is the one command worth remembering: it reads the header, scans for
artwork and music, and each result has a button that opens it in the right
editor. Everything below is the same work done a piece at a time.

```
python tools/survey.py game.a78 --strings      # what am I even looking at
python tools/disasm.py game.a78 -c annotations.json -o src
python tools/verify.py game.a78 -d src         # must pass, from day one
```

To hear a cartridge's music instead of reading its code:

```
python tools/capture.py game.a78 --render      # .a78 -> .log -> .trk -> .wav
```

It works out TIA or POKEY from the header, picks the right machine for the
region, records in MAME and renders. To edit what comes out:

```
python tools/trackeredit.py game.trk           a grid you can type notes into
```

`audio.md` breaks the capture into its four steps for when one comes out wrong;
on Windows both jobs are a drag onto `Render dropped file.bat` or
`Open in tracker.bat`.

Then read [`docs/method.md`](docs/method.md) — the working order — and
[`docs/pitfalls.md`](docs/pitfalls.md), which is a list of things that produced
confidently wrong answers in real work. The garbage-collection trap in the
emulator section is worth reading before you write any probe.

## What's here

### Tools

| | |
|---|---|
| `workbench.py` | One place to open a cartridge: what the header says, what a scan finds, and a button on each result that launches the right editor with the space, base and format already filled in. A launcher, not another tool. |
| `cart.py` | The `.a78` header and the mappers. Header flags checked against the image library, not against published bit lists — they disagree, and the cartridges win. |
| `library.py` | Search a ROM collection **inside its zip**, without extracting 22MB to find one file. Lays out matches, extracts them, or surveys one. |
| `init.py` | Starts a game: reads the header, takes the vectors as entry points, writes the annotations file and reports what the disassembler reached with it. Refuses to overwrite an existing one. |
| `survey.py` | First look: layout, per-bank entropy, strings, where the vectors point. |
| `disasm.py` | Bank-aware recursive-descent 6502 disassembler. `--cycles` annotates timings, `gfx` blocks draw their bits, `--low`/`--mapper` override a header that understates the mapping. Carries constants through so `LDA #n / STA $8000` resolves by itself; reports every switch it could not resolve. |
| `asm.py` | The assembler that closes the loop. |
| `verify.py` | Reassembles every listing and compares to the ROM, byte for byte. |
| `build.py` | Rebuilds a complete image from listings. |
| `dlwalk.py` | Decodes MARIA display lists — including the five-byte entries that put the palette in a different byte. `--selftest` demonstrates the failure mode. |
| `gfx.py` | Renders character sets and sprite pages, line-planar. |
| `spritedump.py` | Renders one direct-mode MARIA display-list object -- a real sprite at a known base/width/height/palette, optionally stacked from several zone-sized segments -- rather than a fixed 256-entry character sheet. Reads the palette straight out of a `dumpgfx.lua` register dump so the colours are the ones the game actually used. |
| `spriteedit.py` | Paint a cartridge's artwork in the browser and write it back. Pen, fill, line, rectangle and ellipse, with undo and copy/paste between cells. Renders in greys because the colours are MARIA registers rather than part of the artwork; a picker and the palettes the cartridge's own code writes let you choose. Reads the line-planar layout in either pixel format, opens straight from an `assets.py` manifest, and refuses to save if a byte outside the region you opened would change. |
| `explore.py` | Work out an unknown music format by ear. Reads a stretch of bytes, ranks plausible layouts of them -- serial records or parallel streams -- renders each to audio, and lets you adjust and listen until it sings. The workbench puts an **explore** button on every audio table it finds, so the address carries across. Saving writes a `reader: "direct"` format into `formats/`, keyed on the player fingerprint, and the tracker then opens those notes from the ROM. The ranking uses structure the bytes prove about themselves, so it assumes you are pointed at music and cannot tell a tune from graphics or code. Your ear decides; confirm with `tracker.py capture`. |
| `forth.py` | Decompile an indirect-threaded Forth image out of a cartridge. Some 7800 games are not 6502 programs -- Karateka is a Forth program with an interpreter underneath, which is why a tracing disassembler reaches 173 instructions in a 48K ROM and stops. This finds the interpreter by shape (the loop every primitive returns to, the routines that save and restore the thread pointer, the word that eats the following cell) and walks the thread: `--at` decompiles a definition, `--callers` says who names a word, `--map` summarises. It recovers structure, not names -- a shipped Forth has no dictionary. |
| `replay.py` | Replay a recorded session and measure what the game did. MAME reproduces a recording exactly -- two replays give byte-identical profiles -- so a before-and-after number means something, which a scripted run cannot deliver: scripted input reaches a title screen and stops. Reports dispatches a frame, how often the controls are read, and which definitions the time went to. `--compare` replays the same session against a second build, honest only where the change does not alter the game's speed. |
| `atx.py` | Read an ATX floppy image -- the format protected Atari 8-bit disks circulate in, which keeps each sector's angular position and error flags so copy protection survives. Takes the good copy of each sector, exports a plain ATR other tools read, shows the boot record, and extracts files when the disk has a directory -- saying so plainly when it does not, which for a self-booting game is the usual answer. |
| `a8dis.py` | Trace an Atari 8-bit cartridge by following its code rather than sweeping it, and reconstruct the RAM it builds. Karateka's XEGS cartridge is a disk that happens to be silicon: a 22-instruction loader copies whole 8K banks into RAM and jumps there, so a trace of the ROM reaches 75 bytes and leaves. `--overlays` shows which banks each scene loads, `--scene N` rebuilds that address space and traces the real game inside it, and `--frame` prints what the game does every vertical blank -- which is the comparison that matters against the 7800 version. |
| `portscan.py` | What it would take to move Atari 8-bit code to the 7800, counted rather than guessed. Both machines run a 6502, which is the least useful fact about the job; the work is everything the code says to the hardware. Sorts every hardware access into what carries over (POKEY is POKEY, at a different address), what has an equivalent needing a rewrite (joysticks), and what has none at all (player/missile graphics, hardware collision detection, ANTIC's display lists). |
| `portkit.py` | Ship a conversion as a recipe rather than as a copy. A BPS patch is a delta between two files, which breaks the moment the output draws on a second source: a 7800 build using Atari 8-bit artwork would carry every one of those bytes inside the "patch". So this ships coordinates instead -- which images are needed (by SHA-256), which extents to take from them (hashed individually), what original work goes with them, and the hash the finished cartridge must have. Everyone supplies their own copies and gets a byte-identical result. It refuses a recipe that carries embedded data, so the guarantee is enforced rather than promised. |
| `patchset.py` | A bundle of patches you can pick from, checked a section at a time. A BPS is a delta between two whole files with a CRC of each, which is the wrong shape for "here are nine independent fixes, take the ones you want": nine fixes are 512 combinations, a whole-file CRC refuses a dump whose header differs, and two patches touching the same bytes apply cleanly and silently produce a ROM that is neither. Nothing standard covers this -- VCDIFF's windows are chosen by the compressor, NINJA and BPM bundle patches for several *files*, PPF validates one hardcoded block. So a patch set names **sections** (a byte range plus the CRC32 of its pre-image, so applying checks 168 bytes rather than 49152), **knobs** (two options turning the same one are alternatives and asking for both is refused rather than resolved by file order), and **floats** (code with no fixed home: the bundle says how much room it needs and where to look, the patcher finds a run of free bytes, and every call site learns the address it chose). Headers are handled by identifying the body rather than the file, so headered and bare dumps take the same bundle -- and because sections stand alone, a ROM already patched elsewhere is still a valid target for whatever nobody has touched. |
| `palette.py` | 7800 colour bytes to RGB. |
| `rammap.py` | Every RAM address the traced code touches, and how often. |
| `audiotrace.py` | Finds a cartridge's music *in the ROM*: locates every audio-register write, traces back to the tables feeding it, and reports them. `--engine` additionally hunts for the Atari in-house music engine -- the one behind Midnight Mutants, Commando, Alien Brigade, Fatal Run and Meltdown, which share a byte-identical duration table -- by the shape of its tables, following every pointer down to real patterns before reporting anything. `--emit` writes the format file, flagging any song whose bank was ambiguous, because that is runtime state a static search cannot recover. |
| `songfmt.py` | Pulls a game's songs out of the ROM as editable data and pushes edited songs back in place, driven by a JSON description of the player's format. Refuses any write that would grow a pattern or touch a byte the format did not declare. `render` turns a pulled song into a tracker file, and `--verify` checks it against a capture frame by frame. |
| `assets.py` | Finds the artwork and the music *as data*: traces MARIA and audio register writes back to what feeds them, follows a captured display list to the graphics it names, and writes annotation blocks plus a manifest the asset tools consume. Bank-ambiguous finds are reported as candidates, not findings. |
| `sim.py` | **Unfinished.** A 6502 core that runs a cartridge's own code and traps its audio writes, so a player is its own authority on its format. Boots cartridges and reaches their main loops; does not yet reach their music, because that needs MARIA's per-zone display interrupts. Groundwork, not a tool yet. |
| `capture.py` | Cartridge to song in one step: reads the header for the sound chip — both of them, on the eighteen images that carry two POKEYs — runs MAME with the probe, converts the log. Recognises the `a7800` fork and switches to debugger watchpoints, which is the only route that works there. |
| `midi.py` | Reads a Standard MIDI File: tracks, names, note ranges, polyphony and timing. Handles running status and tempo changes, which is where naive parsers quietly lose notes. |
| `trackeredit.py` | The tracker itself: a grid in the browser where you type notes, hear them and save. Imports a MIDI track straight into one voice, leaving the rest of the song alone. Backed by the same renderer that exports, so there is only one sound model. |
| `tracker.py` | Sound, for the TIA and for cartridge POKEY: a note table showing what each chip can and cannot play, a text song format, WAV rendering, capture from a running game, MIDI import, and 6502 export with a player. |
| `selftest.py` | Runs the toolkit against itself. Most checks need no cartridge; `--rom`, `--format` and `--log` add the round trips and the frame-by-frame check against hardware. The doc checks are in here too, because what slipped through last time was not a crash but a stale number. |
| `mktone.py` | Builds a cartridge that holds one POKEY setting forever — a controlled single-tone oracle for checking the sound model against a real emulator, since comparing against a game's own audio measures the comparison more than the model. |
| `bps.py` | BPS patches. Build them headerless. |
| `sign7800.py` | Cartridge signatures. An NTSC 7800 hashes the cartridge and checks a signature over that hash at `$FF80`-`$FFF7`; a cartridge that fails is not refused, it is started in **2600 mode**, which looks like a black screen rather than an error. PAL consoles do not check and no emulator does, so a patched ROM works everywhere it gets tested and nowhere it gets played. Verifies, and signs -- the scheme is Rabin with public exponent 2, so a signature is a square root of the hash mod `n`, found by stepping the hash's one don't-care byte until a root exists. A port of Bruce Tomlin's `sign7800.c`, checked against stock dumps of two different games. Every build path here signs; the patch-set has to do it at apply time, since the signature covers the whole image and every combination of options has a different one. |
| `mksite.py` | Packs generated pages into self-contained HTML. |

### On Windows

`Open workbench.bat` — drag a cartridge onto it to open the workbench: the
header, the mapper, a scan for artwork and music, and a button on each result
that opens it in the right editor. Start here with something unfamiliar.

`Open in tracker.bat` — drag a `.a78`, `.log` or `.trk` onto it to open the
song in the tracker grid and edit it.

`Render dropped file.bat` — drag a `.a78` cartridge, a `.log` capture or a
`.trk` song onto it. A cartridge is recorded in MAME first; all three end as a
WAV beside the file, which it then plays. Several at once is fine, an existing
file is kept as `.bak` rather than overwritten, and the sound chip comes from
the cartridge header so nothing needs choosing.

### Probes

`probes/watch.lua`, `probes/dumpdl.lua`, `probes/dumpgfx.lua`, `probes/inputlag.lua` and `probes/audio.lua` — MAME scripts

`Play Atari 8-bit.bat` — playtest an Atari 8-bit disk in Altirra (portable, in `../altirra`, carrying its own OS kernel so no Atari ROMs are needed, and reading ATX natively so copy protection survives). Defaults to a 48K NTSC Atari 800. It cannot record input the way MAME can; save states are the substitute.
for watching writes, capturing a live display list, and logging every audio
register write (TIA, or cartridge POKEY via `A7800_POKEY=<base>`) so
`tracker.py` can turn a running game's music into an editable song. All three
carry the garbage-collection warning inline, because a dead tap does not
announce itself.

### Docs

| | |
|---|---|
| [`method.md`](docs/method.md) | The order of work, and why byte-identity is the discipline everything rests on. |
| [`pitfalls.md`](docs/pitfalls.md) | Traps that each produced a wrong answer in real work. |
| [`hardware.md`](docs/hardware.md) | Memory map, MARIA, display lists, TIA, RIOT, PAL vs NTSC. |
| [`cartridges.md`](docs/cartridges.md) | Header format, mapper flags with the evidence for each, mapper layouts. |
| [`graphics.md`](docs/graphics.md) | Line-planar layout, pixel formats, character mode, finding artwork. |
| [`emulation.md`](docs/emulation.md) | MAME as an instrument, and how to avoid measuring nothing. |
| [`audio.md`](docs/audio.md) | The TIA's two voices, POKEY's four, why one chip is out of tune and the other is not, the tracker, and pulling songs out of a ROM and pushing them back. |

`a7800.py` and `m6502.py` are libraries, not commands: the machine's constants
and the 6502 opcode and cycle tables. Everything else runs from the shell.

### Templates

`templates/annotations.json` — the annotation file, with every key explained.
All human judgement goes here; generated listings stay disposable.

`templates/format.json` — a player-format description for `songfmt.py`, with
every key explained: where a game keeps its songs, what the bits of a note mean,
and which envelope engine to run. `formats/` holds two filled in for real
engines and verified at 100% against hardware: `mm-tia.json` (53 images) and
`aa-pokey.json` (58 images across 27 titles), plus `rmt.json`, which
identifies the 84 cartridges carrying a Raster Music Tracker module without
pretending it can play one. Between them, 23% of every cartridge in the library
with a recognisable player or module.

## The one rule

**The rebuild must stay byte-identical.** Assemble every listing straight back
and compare it to the ROM, from the first hour rather than the last. It costs
seconds, and it is what makes everything else — renaming, re-marking data,
regenerating — free rather than frightening.

It proves you have every byte. It does not prove you understand them: anything
the tracer could not reach comes out as `.byte` and still round-trips perfectly.
Watch the coverage figure too, and treat a bank stuck low as an open question.

## Requirements

Python 3, no dependencies. MAME with 7800 BIOS images for the probes (`a7800`
for NTSC, `a7800p` for PAL).

## Status

The mapper layer, disassembler, assembler, round-trip verifier and display-list
decoder are exercised against real images and the results are reproducible.

Verified against running hardware (MAME): the SuperGame layout at 128K and at
512K including the width of its bank switch, and the Absolute mapper on F-18
Hornet. The TIA sound model matches a renderer validated by ear on a real game,
sample for sample across all sixteen waveforms, and captures from running
cartridges — TIA and POKEY alike — replay to the exact register state on every
logged frame. The POKEY model covers four channels, all eight distortions, the
clock selects, both 16-bit pairs, both high-pass filters, both polynomial
lengths and volume-only mode — all of it measured against MAME with
purpose-built single-tone cartridges rather than taken from a datasheet. The
16-bit dividers agree to 0.00 cents across all four pairing paths, the filters
reproduce its spectrum peak for peak, and all eight distortion modes reproduce
its output bit for bit across three different divider and clock settings each.
The polynomial voices took three attempts to get right: the first two were
checked at a single setting, which cannot tell a correct model from a decimated
one. `docs/audio.md` records how that went wrong, because noise generated the
wrong way sounds exactly like noise generated the right way.

Measured against **MAME v0.287 and `a7800` v5.2** — the 7800-devtools fork,
which corrects POKEY's poly9 sequence and init state. Both agree with the model
at 1.0000 on every case. Capture runs on MAME (the fork's Lua predates
`install_write_tap`); accuracy is checked on the fork. `docs/emulation.md` has
the split. The display-list decoder was
checked against a live list pulled out of a running game, not only against its
own self-test.

Activision banking, Bankset and SOUPER are recognised and refused with an
explanation rather than laid out wrongly.

## Examples

`examples/exo-annotations.json` — a real annotation file worked out with these
tools, for a 512K homebrew whose inter-bank calls go through a trampoline that
`RTS`es into the destination. It shows what the format looks like when the
tracer needs help, and why.
