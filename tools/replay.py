#!/usr/bin/env python3
"""
Replay a recorded session and measure what the game did.

    python tools/replay.py game.a78 session.inp
    python tools/replay.py game.a78 session.inp --compare patched.a78

A recorded session is the only honest way to measure a change to a game like
Karateka. Scripted input reaches a title screen and stops; everything measured
that way here was measuring the wrong state, including one before-and-after
number that looked convincing and was comparing two runs that had drifted into
different screens.

A replay is exact. MAME reproduces the same frames with the same inputs, so two
replays of one recording give byte-identical profiles -- checked, not assumed.
That makes a difference between two runs attributable to the thing that
changed.

## The one thing a replay cannot do

It replays *inputs*, not *intentions*: button states against frame numbers. Play
it back on a build that runs at a different speed and the same presses land at
different moments, and the fight goes somewhere else. So `--compare` is honest
only for changes that do not alter the game's pace. For one that does -- and
skipping the frame waits certainly does -- record a session on each build and
compare the profiles rather than expecting the same fight twice.
"""
import argparse
import io
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PROBE = os.path.join(ROOT, "probes", "threadprof.lua")

MAME_GUESSES = [
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "MAME",
                 "mame.exe"),
    r"C:\Program Files\MAME\mame.exe",
]


def find_mame(explicit=None):
    for p in ([explicit] if explicit else []) + MAME_GUESSES:
        if p and os.path.exists(p):
            return p
    return None


def replay(mame, rom, inp, log, seconds, rompath, loop=None, skip=None):
    inp = os.path.abspath(inp)
    cmd = [mame, "a7800", "-cart", os.path.abspath(rom),
           "-autoboot_script", PROBE,
           "-input_directory", os.path.dirname(inp),
           "-playback", os.path.basename(inp),
           "-video", "none", "-sound", "none", "-nothrottle",
           "-skip_gameinfo"]
    if seconds:
        cmd += ["-seconds_to_run", str(seconds)]
    if rompath:
        cmd += ["-rompath", rompath]
    env = dict(os.environ)
    env["A7800_PROF_LOG"] = os.path.abspath(log)
    if loop:
        env["A7800_LOOP"] = str(int(loop.lstrip("$"), 16))
    if skip:
        env["A7800_PROF_SKIP"] = str(skip)
    r = subprocess.run(cmd, capture_output=True, text=True, env=env,
                       timeout=1200)
    if not os.path.exists(log):
        sys.stderr.write((r.stdout + r.stderr)[-600:] + "\n")
        return None
    return log


def summarise(rom, log, label):
    sys.path.insert(0, HERE)
    import cart as cart_module
    import forth
    counts, meta = forth.load_profile(log)
    cart = cart_module.Cart(rom)
    im = forth.Image(cart, cart.spaces()[0]).discover()
    lines = []
    frames = int(meta.get("frames", 0)) or 1
    disp = int(meta.get("dispatches", 0))
    lines.append("%s" % label)
    lines.append("  %d frames, %d dispatches, %.1f a frame"
                 % (frames, disp, disp / frames))
    if "loopgap" in meta:
        g = float(meta["loopgap"])
        lines.append("  the main loop comes round every %.2f frames "
                     "(%.1f times a second), shortest %s, longest %s"
                     % (g, 60.0 / max(g, 1e-9), meta.get("loopmin", "?"),
                        meta.get("loopmax", "?")))
    hist = forth.loop_histogram(log)
    if hist:
        tot = sum(hist.values())
        run = 0
        median = None
        for g in sorted(hist):
            run += hist[g]
            if median is None and run >= tot / 2:
                median = g
        lines.append("  loop period: median %d frames; the distribution:" % median)
        for g in sorted(hist):
            n = hist[g]
            if n * 40 >= tot or n > 20:
                tag = " (an intermission, not the loop)" if g >= 65 else ""
                lines.append("     %3s frames  %6d  %5.1f%%%s"
                             % ("65+" if g >= 65 else g, n, 100.0 * n / tot, tag))
    if "inputgap" in meta:
        gap = float(meta["inputgap"])
        lines.append("  the controls are read every %.2f frames "
                     "(%.1f times a second)" % (gap, 60.0 / max(gap, 1e-9)))
    out = []
    forth.report_profile(im, counts, meta, out)
    for line in out:
        if line.startswith("   ") and "%" in line:
            lines.append("  " + line.strip())
        if line.startswith("the busiest"):
            break
    return lines


def main():
    ap = argparse.ArgumentParser(
        description=__doc__.strip().split("\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("rom")
    ap.add_argument("inp")
    ap.add_argument("--compare", metavar="ROM",
                    help="replay the same recording on a second build too. "
                         "Only meaningful if the change does not alter the "
                         "game's speed; if it does, record a session on each.")
    ap.add_argument("--seconds", type=int, default=0,
                    help="stop after this many seconds of emulated time")
    ap.add_argument("--mame")
    ap.add_argument("--rompath", help="where the 7800 BIOS lives")
    ap.add_argument("--loop", metavar="HEX",
                    help="a thread address visited once per iteration of the "
                         "game's main loop; reports the loop's period, which "
                         "is what responsiveness actually depends on")
    ap.add_argument("--skip", type=int, metavar="FRAMES",
                    help="ignore this many frames at the start of the "
                         "recording before measuring anything -- for asking "
                         "whether a shape in the numbers belongs to ordinary "
                         "play or to whatever the opening few seconds happen "
                         "to be doing, without a second recording")
    args = ap.parse_args()

    mame = find_mame(args.mame)
    if not mame:
        sys.stderr.write("could not find MAME; pass --mame\n")
        return 2
    if not os.path.exists(args.inp):
        sys.stderr.write("no such recording: %s\n" % args.inp)
        return 2

    jobs = [(args.rom, "as recorded")]
    if args.compare:
        jobs.append((args.compare, "the other build"))

    for i, (rom, label) in enumerate(jobs):
        log = os.path.join(ROOT, "replay-%d.log" % i)
        if os.path.exists(log):
            os.remove(log)
        got = replay(mame, rom, args.inp, log, args.seconds, args.rompath,
                     args.loop, args.skip)
        if not got:
            sys.stderr.write("no profile came back for %s\n" % rom)
            return 1
        for line in summarise(rom, got,
                              "%s  (%s)" % (os.path.basename(rom), label)):
            print(line)
        print("")

    if args.compare:
        print("Remember what a replay is: the same presses at the same frames.")
        print("If the two builds do not run at the same speed, the second one")
        print("was playing a different fight, and only the shape of the")
        print("profile is comparable -- not the outcome.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
