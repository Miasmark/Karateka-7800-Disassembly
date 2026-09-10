# Karateka (Atari 7800): a disassembly, what it found, and patches

Karateka's 1987 7800 port taken apart from the outside in, and put back
together with about forty fixes. No cartridge data is here in any form --
see [No ROM ships with this](#no-rom-ships-with-this).

The short version of what came out of it: **the game is a Forth machine**,
its slowness is a scheduler and not a lack of cycles, its world scrolls
through a five-segment odometer that nothing else had described, its
ending is present but missing one rule, and every build anyone has ever
made of it -- including the first forty of mine -- would have failed on
real NTSC hardware for a reason no emulator reveals.

## What is in here

| | |
|---|---|
| [docs/karateka-map.md](docs/karateka-map.md) | **What the machine is.** The Forth image, the scheduler, RAM, the scroll machinery, the odometer, the stages, the audio driver, the cartridge signature. The reference. |
| [docs/fixing-karateka.md](docs/fixing-karateka.md) | **How each fix got to its shape**, wrong turns kept in deliberately. Longer than the map, because most of the work was being wrong first. |
| [patches/karateka.py](patches/karateka.py) | All 48 fixes, as annotated source. Every one carries its own reasoning and what it was measured against. |
| `dist/` | 50 BPS patches and one `.abp` bundle -- the patches themselves, ready to apply. |
| `probes/` | 43 Lua probes for MAME. Most findings below came from one of these rather than from reading. |
| `tools/` | The general 7800 toolkit this grew out of: disassembler, cartridge model, graphics and music extraction, patch formats, signing. |
| `docs/` | Eleven more documents on the hardware, the formats and the method. |

## What it found

**It is an indirect-threaded Forth image, not hand-written 6502.** `NEXT`
at `$401E`, `DOCOL` at `$4C3E`, `EXIT` at `$4D86`, a data stack in zero
page indexed by X from a base of `$CF`. Almost every "routine" is a list
of addresses. That single fact reorganised everything after it: a
conventional disassembly of this ROM is mostly a disassembly of data.

**A static tracer reaches 0.7% of it -- until you tell it where the
primitives are.** `disasm.py` follows 6502 control flow from the reset
vector and stops after boot, because the machine then stops executing 6502
and starts executing a thread. But a primitive's code field points at its
own code two bytes on, which makes all 200 of them findable in a dozen
lines. Feed those in as entry points and coverage goes to **18.3%** -- 8977
bytes, 4407 instructions -- and the gap check then reports that *no traced
code enters any gap*: every apparent call into unexplained territory is a
`$20` or `$4C` byte falling inside threaded data. The image really is
entered only through the thread. `patches/karateka-entries.py` generates
them.

**The slowness is a scheduler.** `w_A59C` runs nine slots, one entity per
round with a frame of padding each, so a decision costs 13 frames. Probed
across real fights, **87.5% of those slots hold the do-nothing word** --
the loop spends most of its time waiting on entities that have nothing to
say. That is what fixes 27-36 attack, and why reactions can be made ~5
frames without touching game speed.

**How the world scrolls, completely.** Everything that moves goes through
one primitive, `$65C8`, whose addressing turned out to be
`$2227 + Y*62 + aux*4` rather than a base pointer anyone could find on the
stack. All twenty of its call sites are mapped. `$8F3E` is the trap in
that table: it has no `RTS` after its first pass and falls through into a
second, so reading it as two peer routines -- which is exactly what it
looks like -- makes one pillar's halves travel at 2:1 and shear apart.

**The odometer.** Behind the walkers sits a five-segment travel budget,
`$18B3`-`$18BC` with a phase index in `$18BD`, driven by **eight
dispatchers in four machines** that all agree what a phase means. Halls
are 365 to 395 units wide and you start 115 to 145 in. Phases 0 and 4 are
the ends, where the background is pinned and the *player* crosses the
screen instead. Nothing had described this, and knockback that ignores it
desynchronises the scenery from the game's idea of where the scenery is --
permanently, for that hall.

**The stages are a ring.** Six stage words, each setting the next, and
stage 6 sets 0. There is no stage 7 and `$18AA` is never compared against
7 anywhere in the ROM, which is why the princess could not be found by
looking for one.

**The ending exists; one rule is missing.** An early conclusion here was
that the ending was never finished. That was wrong, and the correction is
kept in the docs because the reasoning was reasonable and the conclusion
was not. `w_A158` dresses both figures, stands them five pixels apart and
runs a colour celebration -- rendered and zoomed, the overlap is an
embrace. What is genuinely absent is the stance check: walk in still in
fighting stance and this port greets you exactly like walking in unarmed.
Fix 41 puts it back.

**Death, and why walking stance is lethal.** `$18BF` is the player's
health and `$18C2` the opponent's. Between them at `$6932` sits a stance
test: struck while walking, health is forced to 1 and decremented -- an
instant kill. That rule is what the restored princess kick reuses, so the
strike is the game's own rather than an invention.

**The audio, in full, and nothing dormant in it.** The driver is four
routines; a note is four bytes and the gate byte is a duration in NMI
ticks. `$64AE` holds exactly twelve descriptor pointers and **all twelve
play**: a title theme, a sting when a fight starts, a sting when a stage
ends, two cues over the one cutscene (the warlord, then the princess), and
two one-tick noise blips that are the only sounds combat makes. Between
the opening and closing stings the channels carry nothing else, which is
what "no sound while walking" actually is. Identified by screenshotting
the frame each cue starts on, not by guessing from length.

**The NMI handler is not sound.** It is a five-phase background-colour
gradient writing `$20`. An earlier note here called it audio; it is
corrected in place, and the correction matters because it means the
wait-skipping fixes could never have desynchronised the music -- the music
was never on the loop's clock.

**Every patched build was broken on real hardware.** An NTSC 7800 hashes
the cartridge and checks a signature at `$FF80`-`$FFF7`. Karateka's
`$FFF9` is `$47`, so the hashed range is the whole 48K and every byte any
fix writes is inside it. A stale signature is not refused -- the console
starts in **2600 mode**, which looks like a black screen and not like an
error. PAL consoles do not check and no emulator does, so forty builds
worked everywhere they were tested and nowhere they were played.
[tools/sign7800.py](tools/sign7800.py) fixes that; the scheme is Rabin
with public exponent 2, so signing is a square root of the hash and
verifying is one squaring.

## The patches

41 live fixes over 14 knobs, plus 7 kept and marked withdrawn because
being wrong in a recorded way is worth more than tidiness.

**Start with `karateka-45-tweak`.** Reactions every ~5 frames against
animation every ~10, knockback of 8 units over 3 hits that spends the
hall's own travel budget, remapped controls, held strikes at 3-1-1, a
difficulty switch that works *and* the right way round, and the princess
kick. `-46-` and `-47-` are the same with a longer walking step.

```
python tools/bps.py apply karateka.bin dist/karateka-45-tweak.bps out.bin
```

Or pick your own combination from the bundle, which refuses a selection
that cannot mean one thing rather than resolving it by file order:

```
python tools/patchset.py list dist/karateka.abp
python tools/patchset.py apply dist/karateka.abp --rom karateka.bin \
       --with knockback-light,remap --out out.bin
```

Either way the result comes out with a valid NTSC signature, so it boots
on real hardware and not only in an emulator.

**PAL is supported separately.** `dist/karateka-pal.abp` carries fourteen
of these plus a `pal-tweak` composite, translated bank-wise from the NTSC
builds rather than re-derived -- the European release is the same game in
four banks. The cadence fixes are not in it: they all rest on the input
latch, whose hook site is inside the 50 Hz retime of the NMI handler,
which is the one piece of logic Europe rewrote. Built by
`patches/karateka-pal.py`; needs `KARATEKA_PAL_ROM` pointed at your own
dump.

To build them yourself from your own dump:

```
export KARATEKA_ROM="/path/to/Karateka (NTSC) (Atari) (1987) (FEC21472).a78"
python patches/karateka.py --build      # every fix
python patches/karateka.py --check      # no two disagree unless same knob
python tools/selftest.py                # the toolkit against itself
```

`--check` sorts every pair of fixes into independent, alternatives,
dependency or CONFLICT. It exists because fix 9 was withdrawn for looking
independent and not being -- and it cannot catch a *semantic* clash, which
is stated where it is easy to find rather than discovered later.

## No ROM ships with this

Not one cartridge byte is in this repository, and that is enforced rather
than promised. `.gitignore` refuses every whole-ROM extension.
`portkit.py` refuses a recipe carrying embedded data. Decoded artwork
counts as cartridge bytes too, so the rendered sprite sheets and
screenshots are left out; every one regenerates from your own dump.

The patches in `dist/` are the exception and are safe by construction,
which was audited rather than assumed: a BPS stores a literal only for a
run that *differs* from the source, so across `dist/` that is **17,462
literal bytes and not one equal to the original** at the same address. The
`.abp` is stronger -- its sections describe their pre-image with a CRC32
instead of quoting it, and its float blobs appear nowhere in the
cartridge. `selftest.py` checks all of that on every run, and the check
has a negative control: plant one cartridge byte in a patch, re-seal its
checksum, and it fires.

Supply your own copy of the NTSC release, crc32 `FEC21472`. The tools look
for it beside the toolkit and a few levels down from the parent directory,
so a normal library layout usually needs no configuration.

## The toolkit

Nothing in `tools/` is Karateka-specific. The cartridge model was tested
against **2,664 retail and homebrew images** and lays out all but four,
including Activision's 8K-granular mapper and bankset cartridges; the
disassembler reproduces a hand-verified 128K disassembly byte for byte
while also handling unbanked 4K-48K ROMs.

```
python tools/workbench.py game.a78             # open everything at once
python tools/survey.py game.a78 --strings      # what am I even looking at
python tools/disasm.py game.a78 -c annotations.json -o src
python tools/verify.py game.a78 -d src         # must pass, from day one
```

### Tools

| tool | what it is for |
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
| `dmabudget.py` | **What MARIA leaves you.** MARIA draws by DMA and halts the 6502 while it does, so the cycle budget is a function of what is on screen. Give it a screen and it reports what drawing costs and what is left for game logic. The constants are measured, not quoted -- a cartridge that counts loop iterations per frame, one build per display-list shape -- and the model was validated by predicting shapes it had never seen. |
| `mksprite.py` | **Artwork in, not just out.** Turns a PNG into a direct-mode sprite laid out the way MARIA reads one -- bottom-first, a page per scanline -- with `--frames N` packing an animation side by side at the stride shipping sprite sheets use. Refuses an image with more colours than the mode has. |
| `newgame.py` | **Starts a game.** Writes a project that assembles, boots and puts a moving sprite on screen: the two-level display list, the vblank-synced main loop, and a sprite stored the way MARIA actually reads one -- bottom-up, a page per scanline. Every other tool here reads a cartridge somebody else wrote; this one writes the smallest cartridge that is still a real one. The register values come from shipping 1987 code, and the source is commented to be edited. |

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
| [karateka-map.md](docs/karateka-map.md) | what the machine is |
| [fixing-karateka.md](docs/fixing-karateka.md) | how each fix got its shape |
| [karateka.md](docs/karateka.md) | the first pass, and the loop measurements |
| [karateka-a8.md](docs/karateka-a8.md) | the Atari 8-bit version, for comparison |
| [karateka-from-siblings.md](docs/karateka-from-siblings.md) | what other ports of the same engine reveal |
| [porting-karateka.md](docs/porting-karateka.md) | what porting the 8-bit version would take |
| [hardware.md](docs/hardware.md) | MARIA, TIA, POKEY, the BIOS |
| [cartridges.md](docs/cartridges.md) | headers, mappers, the 2,664-image survey |
| [graphics.md](docs/graphics.md) | display lists, sprites, palettes |
| [audio.md](docs/audio.md) | finding and reading a music player |
| [emulation.md](docs/emulation.md) | driving MAME, probes, recordings |
| [patchset-format.md](docs/patchset-format.md) | the `.abp` format |
| [method.md](docs/method.md) | how to take a cartridge apart |
| [pitfalls.md](docs/pitfalls.md) | the mistakes, so they are made once |

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

## Related

- [Anchored-Bundle-of-Patches](https://github.com/Miasmark/Anchored-Bundle-of-Patches)
  -- the `.abp` format on its own, with the console-specific parts removed.
  Same `patchset/2` format; bundles written by either work in the other.

MIT licensed -- see [LICENSE](LICENSE), and [NOTICE](NOTICE) for what that
does and does not cover.
