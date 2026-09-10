#!/usr/bin/env python3
"""
Point this at a cartridge and get its music out.

    python capture.py game.a78                 -> game.trk
    python capture.py game.a78 --render        -> game.trk and game.wav

It reads the header to find out whether the sound is TIA or a cartridge POKEY
and, if POKEY, where it answers; runs MAME with `probes/audio.lua` and the
right settings; then turns the log into a tracker song. That is steps 1 and 2
of the workflow in docs/audio.md, which are the only two that needed thinking
about.

MAME and the 7800 BIOS have to be findable. In order of preference:

    --mame / A7800_MAME          the executable
    --rompath / A7800_ROMPATH    the directory holding the BIOS images

Without those it looks in the usual install locations and for a `bios` folder
beside the cartridge or beside this toolkit.
"""
import argparse
import io
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cart as cartlib
import tracker

HERE = os.path.dirname(os.path.abspath(__file__))
PROBE = os.path.join(os.path.dirname(HERE), "probes", "audio.lua")

MAME_GUESSES = [
    os.path.expandvars(r"%LOCALAPPDATA%\Programs\MAME\mame.exe"),
    os.path.expandvars(r"%ProgramFiles%\MAME\mame.exe"),
    r"C:\mame\mame.exe",
    "/usr/games/mame", "/usr/bin/mame", "/usr/local/bin/mame",
]


def find_mame(explicit=None):
    for c in [explicit, os.environ.get("A7800_MAME")]:
        if c and os.path.isfile(c):
            return c
    from shutil import which
    got = which("mame") or which("mame64")
    if got:
        return got
    for c in MAME_GUESSES:
        if os.path.isfile(c):
            return c
    return None


def find_rompath(rom, explicit=None):
    """Where the 7800 BIOS images live.

    MAME needs them to boot the machine at all, and they are not part of the
    cartridge, so this has to come from somewhere outside it.
    """
    for c in [explicit, os.environ.get("A7800_ROMPATH")]:
        if c and os.path.isdir(c):
            return c
    near = [os.path.join(os.path.dirname(os.path.abspath(rom)), "bios"),
            os.path.join(os.path.dirname(os.path.abspath(rom)), "..", "bios"),
            os.path.join(os.path.dirname(HERE), "bios"),
            os.path.join(os.path.dirname(os.path.dirname(HERE)), "bios")]
    for c in near:
        if os.path.isdir(c):
            return os.path.normpath(c)
    return None


def inspect(rom):
    """What the header says about this cartridge's sound."""
    c = cartlib.Cart(rom)
    bases = c.pokeys()
    region = (c.info or {}).get("region", "NTSC").lower()
    return {"chip": ("pokey2" if len(bases) > 1 else
                     ("pokey" if bases else "tia")),
            "pokey": bases[0] if bases else None,
            "pokeys": bases,
            "region": region,
            "machine": "a7800p" if region == "pal" else "a7800",
            "title": (c.info or {}).get("title", os.path.basename(rom))}


# --------------------------------------------------------- the a7800 fork
# Its Lua predates `install_write_tap`, so probes/audio.lua sees nothing there.
# Its debugger does the same job: a watchpoint action can call `logerror`, and
# `-log` sends that to error.log. A companion Lua script stamps frame numbers
# into the same log, and the two interleave in order.

FRAME_PROBE = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "probes", "a7800-frames.lua")

TIA_WATCH = [(0x0015, 0x06), (0x0115, 0x06), (0x0215, 0x06)]

NL = chr(10)


def watch_script(info):
    """The -debugscript that logs every audio write, for this cartridge.

    The newline inside the format string has to reach the debugger as the two
    characters backslash-n. Writing a real newline there ends the command
    instead, and the watchpoint silently never fires -- you get a log full of
    frame markers and no writes at all.
    """
    esc = chr(92) + "n"
    spans = []
    if info["pokeys"]:
        for base in info["pokeys"]:
            spans.append((base, 9))
    else:
        spans.extend(TIA_WATCH)
    lines = []
    for addr, length in spans:
        lines.append('wpset %X,%X,w,1,{logerror "A78W %%04X %%02X%s",'
                     'wpaddr,wpdata; g}' % (addr, length, esc))
    lines.append("go")
    return "".join(l + NL for l in lines)


def convert_debug_log(errlog, out, info):
    """error.log -> the per-frame format tracker.read_capture expects.

    The watchpoint lines carry an address and a byte; the Lua markers carry the
    frame. Holding the last value written to each register and emitting a row
    when a frame ends is exactly what probes/audio.lua does in Lua -- it just
    happens here instead, because a debugger action cannot hold state.
    """
    bases = info["pokeys"]
    nvals = (9 * len(bases)) if bases else 6
    slot = {}
    if bases:
        for i, base in enumerate(bases):
            for r in range(9):
                slot[base + r] = i * 9 + r
    else:
        for mirror in (0x0000, 0x0100, 0x0200):
            for off, k in ((0x15, 0), (0x17, 1), (0x19, 2),
                           (0x16, 3), (0x18, 4), (0x1A, 5)):
                slot[mirror + off] = k
    cur = [0] * nvals
    last = [-1] * nvals
    rows = []
    frame = 0
    with io.open(errlog, encoding="latin1", errors="replace") as f:
        for line in f:
            # Both sources tag their lines: the debugger writes the marker
            # bare, Lua's logerror prefixes "[luaengine] ". Find the token
            # rather than anchoring at the start, or the frame markers are
            # silently dropped and every row with them.
            line = line.strip()
            i = line.find("A78W ")
            if i < 0:
                i = line.find("A78FRAME ")
            if i > 0:
                line = line[i:]
            if line.startswith("A78W "):
                parts = line.split()
                if len(parts) < 3:
                    continue
                try:
                    addr, data = int(parts[1], 16), int(parts[2], 16)
                except ValueError:
                    continue
                k = slot.get(addr)
                if k is not None:
                    cur[k] = data & 0xFF
            elif line.startswith("A78FRAME "):
                try:
                    frame = int(line.split()[1])
                except (ValueError, IndexError):
                    continue
                if cur != last:
                    rows.append((frame, list(cur)))
                    last = list(cur)
    with io.open(out, "w", encoding="utf-8") as f:
        if bases:
            names = ",".join("$%04X" % b for b in bases)
            heads = []
            for i in range(len(bases)):
                n = i * 4
                heads.append("f%d c%d  f%d c%d  f%d c%d  f%d c%d  ctl"
                             % (n+1, n+1, n+2, n+2, n+3, n+3, n+4, n+4))
            f.write("# chip %s  base %s%s"
                    % ("pokey2" if len(bases) > 1 else "pokey", names, NL))
            f.write("# frame  " + "   ".join(heads) + "   (hex)" + NL)
        else:
            f.write("# chip tia" + NL)
            f.write("# frame  c0 f0 v0  c1 f1 v1   (hex)" + NL)
        for fr, vals in rows:
            f.write("%d %s%s" % (fr, " ".join("%02X" % v for v in vals), NL))
    return len(rows)


def is_a7800_fork(exe):
    """True if this executable is the 7800-devtools fork rather than MAME."""
    return os.path.basename(exe).lower().startswith("a7800")


def capture(rom, out=None, seconds=40, frames=None, skip=0, drive=True,
            mame=None, rompath=None, log=None, quiet=False):
    info = inspect(rom)
    exe = find_mame(mame)
    if not exe:
        raise RuntimeError(
            "cannot find MAME. Pass --mame, or set A7800_MAME to the "
            "executable.")
    roms = find_rompath(rom, rompath)
    if not roms:
        raise RuntimeError(
            "cannot find the 7800 BIOS images. Pass --rompath, or set "
            "A7800_ROMPATH to the folder holding them.")
    if not os.path.isfile(PROBE):
        raise RuntimeError("cannot find %s" % PROBE)

    stem = os.path.splitext(os.path.abspath(rom))[0]
    log = log or (stem + ".log")
    out = out or (stem + ".trk")
    frames = frames or int(seconds * (50 if info["region"] == "pal" else 60))

    env = dict(os.environ)
    env["A7800_AUDIO_LOG"] = log
    env["A7800_AUDIO_FRAMES"] = str(frames)
    env["A7800_AUDIO_SKIP"] = str(skip)
    if drive:
        env["A7800_DRIVE"] = "1"
    else:
        env.pop("A7800_DRIVE", None)
    if info["pokeys"]:
        # Every declared chip, not just the first. They carry different music.
        env["A7800_POKEY"] = ",".join("0x%04X" % b for b in info["pokeys"])
    else:
        env.pop("A7800_POKEY", None)

    if not quiet:
        print("  %s" % info["title"])
        print("  sound      %s%s" % (
            info["chip"].upper(),
            "" if not info["pokeys"] else
            " at " + ", ".join("$%04X" % b for b in info["pokeys"])))
        if len(info["pokeys"]) > 1:
            print("             (two POKEYs, both captured -- eight voices. "
                  "MAME plays only the")
            print("              first, so its own audio will be missing half "
                  "of this; the")
            print("              toolkit renders from the registers and does "
                  "not care.)")
        print("  machine    %s (%s)%s"
              % (info["machine"], info["region"].upper(),
                 "  [a7800 fork: debugger watchpoints]"
                 if is_a7800_fork(exe) else ""))
        print("  recording  %d frames, about %d seconds" % (frames, seconds))

    workdir = os.path.dirname(os.path.abspath(rom))
    fork = is_a7800_fork(exe)
    errlog = os.path.join(workdir, "error.log")
    if fork:
        # No Lua taps in the fork, so watch the registers from the debugger and
        # stamp frame numbers from Lua. Both land in error.log, in order.
        wp = os.path.join(workdir, "a78-watch.txt")
        with io.open(wp, "w", encoding="utf-8") as f:
            f.write(watch_script(info))
        if os.path.exists(errlog):
            os.remove(errlog)
        cmd = [exe, info["machine"], "-rompath", roms, "-cart",
               os.path.abspath(rom), "-debug", "-debugscript", wp,
               "-autoboot_script", FRAME_PROBE, "-autoboot_delay", "1", "-log",
               "-sound", "none", "-video", "none", "-nothrottle",
               "-seconds_to_run", str(int(seconds) + 8)]
    else:
        cmd = [exe, info["machine"], "-rompath", roms, "-cart",
               os.path.abspath(rom), "-autoboot_script", PROBE,
               "-sound", "none", "-video", "none", "-nothrottle",
               "-seconds_to_run", str(int(seconds) + 5)]
    # MAME writes its log into the working directory, so run it where the
    # cartridge is rather than wherever this was invoked from.
    proc = subprocess.run(cmd, env=env, cwd=workdir,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if fork:
        if not os.path.isfile(errlog):
            msg = proc.stdout.decode("utf-8", "replace").strip().splitlines()
            raise RuntimeError(
                "a7800 wrote no error.log, so the watchpoints never fired.\n  "
                + "\n  ".join(msg[-6:] or ["(no output)"]))
        if not convert_debug_log(errlog, log, info):
            raise RuntimeError(
                "a7800 logged no audio writes -- check %s by hand." % errlog)
    if not os.path.isfile(log):
        msg = proc.stdout.decode("utf-8", "replace").strip().splitlines()
        raise RuntimeError(
            "%s produced no log.\n  " % ("a7800" if fork else "MAME") +
            "\n  ".join(msg[-6:] or ["(no output)"]))

    song = tracker.read_capture(log, info["region"])
    voiced = sum(1 for row in song.rows if any(x for x in row))
    with open(out, "w", encoding="utf-8") as f:
        f.write(tracker.dump(song))
    if not quiet:
        print("  log        %s" % os.path.basename(log))
        print("  song       %s -- %d rows, %d with a change"
              % (os.path.basename(out), len(song), voiced))
        if voiced <= 2:
            print("  NOTE       almost nothing changed. If the game needs more "
                  "than a\n             button press to start, record a "
                  "session and capture\n             against -playback "
                  "instead.")
        for name, ctl in song.unsupported:
            print("  NOTE       uses %s (AUDCTL $%02X), which a tier-1 POKEY\n"
                  "             does not model -- render will refuse it"
                  % (name, ctl))
    return {"log": log, "trk": out, "song": song, "info": info}


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().split("\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("rom")
    ap.add_argument("-o", "--out", help="the .trk to write")
    ap.add_argument("--seconds", type=float, default=40.0)
    ap.add_argument("--frames", type=int, help="frames to log (default: seconds)")
    ap.add_argument("--skip", type=int, default=0,
                    help="discard this many frames first")
    ap.add_argument("--no-drive", action="store_true",
                    help="do not press fire; use for a game that starts itself")
    ap.add_argument("--render", action="store_true", help="also write a WAV")
    ap.add_argument("--mame")
    ap.add_argument("--rompath")
    args = ap.parse_args()

    try:
        r = capture(args.rom, args.out, args.seconds, args.frames, args.skip,
                    not args.no_drive, args.mame, args.rompath)
    except (RuntimeError, cartlib.UnknownMapper) as e:
        sys.stderr.write("%s\n" % e)
        return 2

    if args.render:
        wav = os.path.splitext(r["trk"])[0] + ".wav"
        try:
            info = tracker.render(r["song"], wav)
            print("  wav        %s -- %.1f s"
                  % (os.path.basename(wav), info["seconds"]))
        except ValueError as e:
            sys.stderr.write("  not rendered: %s\n" % e)
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
