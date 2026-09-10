# Using MAME as an instrument

An emulator is the only place a hypothesis about a ROM becomes a fact. Used
carelessly it is also an excellent way to manufacture confident nonsense.

## Read this before writing a tap

```lua
TAP = mem:install_write_tap(lo, hi, "name", fn)
```

The global assignment is not style. `install_write_tap` returns a tap object,
and if nothing holds a reference to it, Lua's garbage collector reclaims it and
**the tap stops firing without any message**. It typically survives boot, dies a
few hundred frames in, and leaves you with output that looks fine.

This cost an afternoon and produced two published-then-retracted claims. Guard
against it: have every probe count writes to an address you know is busy, and
print that count. A number that stops growing means a dead tap, not a quiet game.

More generally: **treat every negative result from an emulator probe as
unproven until you have shown the probe still works.** "I saw nothing" and "my
instrument was switched off" look identical from the outside.

**A third way to switch the instrument off without noticing: a callback that
errors.** A tap function that indexes `cpu.state` with a register name that
doesn't exist for the CPU core in use throws a Lua runtime error -- and that
error is swallowed the same way a GC'd tap or a written-once-then-abandoned
address is: the script keeps running, prints its normal progress lines, and
just never adds an event. On the m6502 core the stack pointer is
`cpu.state["SP"]`, not `cpu.state["S"]` -- a plausible guess that produces
exactly this failure, discovered only by writing a two-line probe that dumps
`for k,v in pairs(cpu.state) do print(k) end` once at startup and reading the
real key names back. Do that dump the first time any new probe touches a
register beyond `PC`, before trusting a clean-looking run with zero results.

## Headless runs

```
mame a7800  -cart game.a78 -autoboot_script probe.lua -video none -sound none -nothrottle -str 30
mame a7800p -cart game.a78 ...
```

* `a7800` is NTSC, `a7800p` is PAL. Using the wrong one against a PAL image
  gives you a game that runs but times everything wrong.
* `-str N` exits after N seconds of emulated time; combined with `-nothrottle`
  a thirty-second run takes a couple of seconds.
* `-video none -sound none` for anything scripted. Add `-snapshot_directory` and
  call `manager.machine.video:snapshot()` when you want frames.
* The BIOS must be findable: `-rompath` at the directory holding the 7800 BIOS
  images.

## Driving the game

Nothing reaches a hypothesis if the game is sitting on its title screen.

```lua
local fire = manager.machine.ioport.ports[":buttons"].fields["P1 Button 1"]
local joy  = manager.machine.ioport.ports[":joysticks"]
fire:set_value(1)
joy.fields["P1 Up"]:set_value(1)
```

Pulsing fire (`(F % 40) < 8`) gets through title screens and dialogue reliably.
Scripted movement is much less reliable -- games check collision and timing, and
"hold up for 200 frames" often does not arrive anywhere. When you need a
specific game state, record it by hand instead:

```
mame a7800 -cart game.a78 -record session.inp
mame a7800 -cart game.a78 -playback session.inp -autoboot_script probe.lua
```

A recording is reproducible and can be replayed against a *modified* ROM, which
is how you compare stock and patched behaviour on identical input. That only
holds while your edits do not change timing or input handling -- a patch that
shifts a frame count will desynchronise a recording, and the divergence is
usually obvious (the player walks into a wall).

**`-playback`/`-record` silently resolve through `input_directory`, not your
given path.** MAME's `input_directory` setting (default `inp`, persisted in a
generated `mame.ini` you may not know exists) gets prepended to whatever you
pass `-playback`/`-record` -- including an absolute path, at least on the
version this was checked against. A recording sitting right where you pointed
still fails with `Input file ... not found` for no visible reason. Fix by
passing `-input_directory .` (or wherever the file actually lives) explicitly;
don't waste time re-checking the path itself once you've already confirmed the
file exists on disk.

## Watching the right thing

Three probe shapes cover most needs:

**Count writes to a region** -- which addresses are hot, how many distinct
values, when each was first touched. Good for finding where a variable lives.
`probes/watch.lua`.

**Snapshot memory at a chosen frame** -- then decode it offline with a real
tool rather than in Lua. `probes/dumpdl.lua` does this for display lists, and
prints the exact command to decode what it dumped.

**Assert a model** -- the strongest kind. Compute what your model predicts,
compare against what the machine did, and print only when they differ. A probe
that prints nothing for 1,800 frames and then prints one line has told you far
more than one that prints 1,800 lines.

## Registers you cannot read

`DPPH`/`DPPL` are write-only, so there is no way to ask a running machine where
the screen is being drawn from -- you have to watch the write. The same applies
to most MARIA registers. Where a game keeps a RAM shadow of a register, prefer
reading the shadow; where it does not, tap the write.

## Comparing two builds

Run both, capture the same measurement from each, diff the measurements -- not
the screenshots. Screenshots differ for uninteresting reasons (one frame of
animation phase) and are identical for interesting ones (a colour that only
changes on alternate frames). A number extracted at a known frame is a better
witness than a picture.

When a change *should* be invisible, that is a test: build it, run both, and
show the measurement is unchanged.

## Which emulator to use, and for what

Two are worth having, and they are good at different things.

**MAME** (v0.287 here) is what the probes run on. Its Lua exposes
`install_write_tap`, which is how `probes/audio.lua`, `dumpdl.lua` and
`watch.lua` see anything at all.

**[`a7800`](https://github.com/7800-devtools/a7800)** (v5.2 here) is the
7800-devtools fork of MAME's driver. It is ahead on hardware fidelity --
corrected POKEY poly9 sequence and init state, better two-tone mode, accurate
MARIA DMA hole penalties, mid-scanline register updates -- and ahead on
cartridge formats, with Bankset bankswitching and POKEY@800 for several layouts
that MAME answers `Unsupported mapper` for.

### The audio model agrees with both

Every distortion, at three divider and clock settings, plus poly9 on two
clocks: **1.0000 against a7800 and 1.0000 against MAME**. The fork's poly9
correction does not change the sequence a phase-aligned comparison sees. So
capturing on MAME is sound; the register stream and the chip model both check
out against the more accurate emulator.

### But the probes only run on MAME

`a7800` v5.2 forks an older MAME whose Lua predates `install_write_tap`:

```
[LUA ERROR] attempt to index (get) lua_nil value "install_write_tap"
```

Its Lua also exposes the machine as `manager:machine()` rather than
`manager.machine`. The probes now resolve that difference themselves, so they
load cleanly on both, but on `a7800` they cannot install the taps and there is
no headless substitute -- `-debugscript` watchpoints print to the debugger
console, not stdout.

**So today: capture on MAME, check sound accuracy on a7800.** The split costs
nothing for audio, because the capture records what the *cartridge writes*,
which does not depend on how well the chip is emulated.

### But a7800 can be probed too, without touching its C++

The Lua tap is missing; the debugger is not. `-debugscript` sets watchpoints,
and a watchpoint action can call `logerror`, which `-log` sends to `error.log`:

```
wpset 4000,10,w,1,{logerror "AUD %04X %02X
",wpaddr,wpdata; g}
go
```

```
a7800 a7800 -cart game.a78 -debug -debugscript wp.txt -log       -sound none -video none -nothrottle -str 4
```

That captured **694 POKEY writes** from Ballblazer headlessly. Frame boundaries
come from the other side: a7800's Lua does expose `machine:logerror()`, so an
`-autoboot_script` can stamp `FRAME n` into the same log, giving the same
per-frame structure `probes/audio.lua` produces.

That probe is written. `capture.py` recognises the fork from its executable
name and switches routes by itself:

```
python tools/capture.py game.a78 --mame .../a7800.exe
  machine    a7800 (NTSC)  [a7800 fork: debugger watchpoints]
  song       game.trk -- 719 rows, 172 with a change
```

It writes the watchpoint script for whichever chip the header declares, runs
the fork with `-debug -debugscript ... -autoboot_script probes/a7800-frames.lua
-log`, and folds `error.log` back into the ordinary per-frame format. TIA and
POKEY both work. Two details cost an hour each and are worth knowing:

* **The newline in the watchpoint's format string must reach the debugger as
  backslash-n**, not as a real newline. A real one ends the command, and the
  watchpoint then never fires -- leaving a log full of frame markers and no
  writes, which looks like a game that makes no sound.
* **Lua's `logerror` prefixes its lines with `[luaengine] `** while the
  debugger writes its own bare. Anchoring the parse at the start of the line
  drops every frame marker, and with them every row.

### How closely the two agree

Capturing Ballblazer on both and aligning: **561 of 658 rows identical, 85.3%**.
The residue is not the probe. The fork's MARIA DMA timing differs from MAME's,
so the game's own code runs at a slightly different rate and reaches its music
at slightly different moments. Expect agreement, not equality, and treat a
capture as a record of *that emulator's* run.

The alternative, if the Lua tap is wanted properly, is upstream work: a7800
would need `address_space::install_write_tap` (MAME's `memory_passthrough_handler`
machinery, which post-dates its fork point) plus the Lua binding for it. That is
a core memory-system backport, not a small patch -- which is exactly why the
watchpoint route is worth having.

### Neither emulates the second POKEY

Both instantiate one chip for a dual-POKEY cartridge:

```
Starting Atari 7800 ROM Carts w/POKEY @ 0x0450 ':cartslot:a78_p450_t0'
Starting Atari C012294 POKEY ':cartslot:a78_p450_t0:pokey450'
```

a7800 at least recognises the header rather than reporting an unsupported
mapper, but the `$0440` chip is still not there. This is why the toolkit
captures POKEY from the **CPU bus** instead of the device: it records what the
game writes to both chips whether or not anything is listening, and
`tracker.py` renders all eight voices from that. See `docs/audio.md`.
