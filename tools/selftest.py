#!/usr/bin/env python3
"""
Run the toolkit against itself.

    python tools/selftest.py [--rom game.a78] [--format f.json] [--log capture.log]

Most of this runs with no cartridge at all, because the toolkit ships without
one: the example songs, the display-list decoder, the note tables, the cycle
table, the docs and the refusals are all self-contained. Point `--rom` at an
image and the round trips run too.

This exists because the regression was six commands typed from memory, and the
thing that slipped through was not a crash -- it was a number in the docs that
had quietly stopped being true. So the doc checks sit here alongside the code
checks, and they are not softer.
"""
import argparse
import glob
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

PASS, FAIL, SKIP = "ok", "FAILED", "skipped"


class Results(object):
    def __init__(self, verbose=False):
        self.rows = []
        self.verbose = verbose

    def add(self, name, status, detail=""):
        self.rows.append((name, status, detail))
        mark = {PASS: "  ok  ", FAIL: " FAIL ", SKIP: "  --  "}[status]
        print(("%s %-34s %s" % (mark, name, detail)).rstrip())
        return status == PASS

    def check(self, name, fn):
        try:
            detail = fn()
        except Exception as e:                                   # noqa: BLE001
            if self.verbose:
                import traceback
                traceback.print_exc()
            return self.add(name, FAIL, "%s: %s" % (type(e).__name__, e))
        if detail is None:
            return self.add(name, SKIP, "nothing to test")
        return self.add(name, PASS, detail)

    @property
    def failed(self):
        return [r for r in self.rows if r[1] == FAIL]


def run_tool(name, *args):
    p = subprocess.run([sys.executable, os.path.join(HERE, name)] + list(args),
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return p.stdout.decode("utf-8", "replace")


# --------------------------------------------------------- without a cartridge

def t_json():
    """Every shipped JSON parses, and the template documents its own keys."""
    files = (glob.glob(os.path.join(ROOT, "formats", "*.json"))
             + glob.glob(os.path.join(ROOT, "templates", "*.json"))
             + glob.glob(os.path.join(ROOT, "examples", "*.json")))
    for f in files:
        json.load(io.open(f, encoding="utf-8"))
    tpl = json.load(io.open(os.path.join(ROOT, "templates", "format.json"),
                            encoding="utf-8"))
    # A key with no underscore twin is a key nobody explained.
    documented = set(k[1:] for k in tpl if k.startswith("_"))
    real = set(k for k in tpl if not k.startswith("_"))
    missing = sorted(real - documented - set(["name", "chip"]))
    if missing:
        raise AssertionError("template keys with no _explanation: %s"
                             % ", ".join(missing))
    return "%d files, template documents every key" % len(files)


def t_dlwalk():
    out = run_tool("dlwalk.py", "--selftest")
    if "5-byte entry decoded" not in out:
        raise AssertionError("selftest output changed")
    return "5-byte display-list entry decoded correctly"


def t_check_gaps():
    """--check-gaps must find real missed code, not just say "all clear".

    A checker that can only ever report "nothing found" is worthless, which
    is the toolkit's own read-tap pitfall in a different costume. So this
    builds a 16K image with exactly one hidden routine, reachable only
    through `JMP ($C900)` -- an indirect the tracer cannot follow -- and
    requires that the tool both flags it AND resolves the pointer to the
    real target. It also plants a $20 byte inside data, which must be
    dismissed as a coincidence rather than reported.
    """
    rom = bytearray([0xFF] * 16384)

    def put(addr, bs):
        rom[addr - 0xC000:addr - 0xC000 + len(bs)] = bytes(bs)

    put(0xC000, [0x6C, 0x00, 0xC9])                    # traced: JMP ($C900)
    put(0xC900, [0x00, 0xCF])                          # the pointer -> $CF00
    put(0xCF00, [0xA9, 0x01, 0x8D, 0x00, 0x20, 0x60])  # the hidden routine
    put(0xFFFA, [0x00, 0xC0, 0x00, 0xC0, 0x00, 0xC0])

    tmpdir = tempfile.mkdtemp(prefix="selftest-gaps-")
    rom_path = os.path.join(tmpdir, "synthetic.a78")
    cfg_path = os.path.join(tmpdir, "annotations.json")
    io.open(rom_path, "wb").write(bytes(rom))
    json.dump({"entries": ["rom:C000"], "labels": {}, "ram": {},
               "comments": {}, "blocks": []},
              io.open(cfg_path, "w", encoding="utf-8"))

    out = run_tool("disasm.py", rom_path, "-c", cfg_path,
                   "-o", os.path.join(tmpdir, "src"), "--check-gaps")
    if "MISSED CODE" not in out:
        raise AssertionError("did not flag the hidden routine:\n" + out)
    if "$CF00" not in out:
        raise AssertionError("did not dereference the pointer to $CF00:\n" + out)
    if "1 REAL call site" not in out:
        raise AssertionError("expected exactly one real call site:\n" + out)
    if "coincidence" not in out:
        raise AssertionError("did not dismiss the planted $20 byte:\n" + out)
    return "found code hidden behind an indirect jump; dismissed a decoy"


def t_newgame():
    """The scaffold must assemble to a real 16K cartridge, sprite included.

    Checked here rather than by eye because the failure this guards against
    is silent: store a direct-mode sprite as a flat bitmap and it still
    assembles, still boots, and draws one row of your art repeated eight
    times. So the test asserts the layout MARIA actually reads -- rows one
    page apart, bottom row at the address the display list names, top row
    at the highest page.
    """
    import newgame
    tmpdir = tempfile.mkdtemp(prefix="selftest-newgame-")
    out = run_tool("newgame.py", tmpdir, "--title", "Selftest", "--build",
                   "--force")
    a78 = os.path.join(tmpdir, "game.a78")
    if not os.path.exists(a78):
        raise AssertionError("no cartridge written:\n" + out)
    blob = io.open(a78, "rb").read()
    if len(blob) != 128 + 0x4000:
        raise AssertionError("cartridge is %d bytes, not 128 + 16K" % len(blob))
    rom = blob[128:]

    rows = newgame.sprite_rows()
    top, bottom = rows[0], rows[-1]
    base = newgame.GFX_PAGE << 8
    hi = base + (newgame.SPRITE_LINES - 1) * 0x100      # highest page
    at = lambda addr, n: rom[addr - 0xC000:addr - 0xC000 + n]
    if at(base, len(bottom)) != bottom:
        raise AssertionError("bottom row is not at the DL's own address")
    if at(hi, len(top)) != top:
        raise AssertionError("top row is not at the highest page -- the "
                             "sprite is stored the wrong way up")
    if top == bottom:
        raise AssertionError("test art is symmetric, so it cannot detect "
                             "an inverted sprite")

    reset = rom[0x3FFC - 0x0000] | (rom[0x3FFD] << 8)
    if reset != 0xC000:
        raise AssertionError("RESET vector is $%04X, not $C000" % reset)
    return "assembles to 16K; sprite stored bottom-up, a page per scanline"


def t_dmabudget():
    """The cost model must still reproduce the measurements it was fitted to.

    These seven numbers came off real MAME runs (see dmabudget.py for the
    method). They are here because a plausible-looking edit to one constant
    would otherwise go unnoticed -- the tool prints a confident table either
    way.
    """
    import dmabudget as d
    cases = [                       # 12 zones x 16 lines, 2 objects
        (8, 0, 0, 4177), (1, 0, 0, 2242), (16, 0, 0, 6489),
        (8, 0, 1, 4373),                       # 5-byte entries
        (4, 1, 0, 4372), (8, 1, 0, 6685),      # character mode, 1 byte/char
        (4, 2, 0, 5515),                       # character mode, 2 bytes/char
    ]
    # 24 zones x 8 lines, 2 objects: holey DMA and display interrupts
    extra = [
        (dict(lines=8, count=2, width=20), 24, 1412),
        (dict(lines=8, count=2, width=20, holey=True), 24, 1825),
        (dict(lines=8, count=2, width=8), 24, 1660),
        (dict(lines=8, count=2, width=20, dli=True), 24, 1383),
        (dict(lines=8, count=2, width=8, dli=True), 24, 1631),
    ]
    worst = 0.0
    for kw, n, iters in extra:
        measured = (1960 - iters) * 14.0156      # the counting cartridge
        model = sum(d.Zone(**kw).cycles() for _ in range(n))
        err = abs(model - measured) / measured
        worst = max(worst, err)
        if err > 0.03:
            raise AssertionError("%s: model %.0f vs measured %.0f (%.1f%%)"
                                 % (kw, model, measured, 100 * err))
    if d.Zone(8, 2, 20, holey=True).cycles() >= d.Zone(8, 2, 20).cycles():
        raise AssertionError("holey DMA must be cheaper, not dearer")
    if d.DLI_COST <= 0:
        raise AssertionError("a display interrupt is not free")
    for width, chars, five, measured in cases:
        zones = [d.Zone(16, 2, width, five=bool(five), chars=chars)
                 for _ in range(12)]
        model = sum(z.cycles() for z in zones)
        err = abs(model - measured) / measured
        worst = max(worst, err)
        if err > 0.03:   # the model's honest worst case, at width 1
            raise AssertionError(
                "width %d chars %d five %d: model %.0f vs measured %d (%.1f%%)"
                % (width, chars, five, model, measured, 100 * err))
    if d.REGIONS["ntsc"][0] != 262:
        raise AssertionError("NTSC scanline count changed")
    return ("12 measured configurations reproduced, worst error %.1f%%"
            % (100 * worst))


def t_mksprite():
    """Packing must invert, and must come out bottom-first.

    The orientation half matters more than the packing half: a sprite stored
    the wrong way up still assembles and still draws, just wrongly, so the
    test art here is deliberately asymmetric top-to-bottom and the check is
    that the LAST scanline is emitted first.
    """
    try:
        from PIL import Image
    except ImportError:
        return SKIP, "needs Pillow"
    import mksprite

    for mode, ncol in (("160A", 4), ("320A", 2)):
        bpp, ppb = mksprite.MODES[mode]
        w, h = ppb * 2, 4
        img = Image.new("RGB", (w, h))
        shades = [(0, 0, 0), (90, 90, 90), (180, 180, 180), (255, 255, 255)][:ncol]
        want = []
        for y in range(h):
            row = []
            for x in range(w):
                idx = (x + y) % ncol
                row.append(idx)
                img.putpixel((x, y), shades[idx])
            want.append(row)
        cmap = mksprite.build_map(img, bpp, None)
        rows, width = mksprite.pack(img, mode, 1, cmap)
        if width != w // ppb:
            raise AssertionError("%s: width %d, expected %d" % (mode, width, w // ppb))
        got = mksprite.unpack(rows, width, 1, mode)
        if got != want:
            raise AssertionError("%s: pack/unpack did not round-trip" % mode)

        text = mksprite.emit(rows, width, 1, "art", mode, "test.png")
        body = [l for l in text.split("\n") if l.strip().startswith(".byte")]
        first = [int(t, 16) for t in body[0].split(";")[0].replace(".byte", "").replace("$", "").split(",")]
        if bytes(first) != rows[-1]:
            raise AssertionError("%s: emitted top row first; MARIA reads "
                                 "bottom-first" % mode)

    # frames pack side by side at a stride of one frame's width
    img = Image.new("RGB", (16, 2))
    for x in range(16):
        img.putpixel((x, 0), (255, 255, 255) if x < 8 else (0, 0, 0))
        img.putpixel((x, 1), (255, 255, 255) if x < 8 else (0, 0, 0))
    cmap = mksprite.build_map(img, 2, None)
    rows, width = mksprite.pack(img, "160A", 2, cmap)
    if width != 2:
        raise AssertionError("two frames of 8 pixels should be 2 bytes wide")
    if len(rows[0]) != 4:
        raise AssertionError("a scanline of 2 frames x 2 bytes should be 4")
    if rows[0][:2] == rows[0][2:]:
        raise AssertionError("the two frames packed identically; the split "
                             "is in the wrong place")
    return "160A and 320A round-trip; rows emitted bottom-first; frames stride"


def t_sim_compare():
    """sim.py's score must measure the player, not the phase of the clock.

    The first version of it counted rows landing on the same frame with the
    same values, which conflated "plays the right notes" with "keeps
    playing", and was chaotic: two builds of the simulator differing by 17
    cycles a frame scored 6.3% and 0.1%. This checks the replacement is
    insensitive to frame numbering and separates the two questions.
    """
    import sim

    def log(rows):
        return [(f, tuple(v.split())) for f, v in rows]

    ref = log([(10, "00 01"), (20, "00 02"), (30, "00 03"),
               (40, "00 04"), (50, "00 05"), (60, "00 06")])

    same = sim.compare(ref, ref)
    if same["agreement"] < 0.999 or same["progress"] < 0.999:
        raise AssertionError("a log does not match itself: %r" % same)

    # Same states, every frame number shifted. Must score identically.
    shifted = log([(f + 977, v) for f, v in
                   [(10, "00 01"), (20, "00 02"), (30, "00 03"),
                    (40, "00 04"), (50, "00 05"), (60, "00 06")]])
    sh = sim.compare(shifted, ref)
    if sh["agreement"] < 0.999 or sh["progress"] < 0.999:
        raise AssertionError("frame offset changed the score: %r" % sh)

    # Correct as far as it goes, then stops: agreement high, progress low.
    short = log([(10, "00 01"), (20, "00 02")])
    st = sim.compare(short, ref)
    if st["agreement"] < 0.999:
        raise AssertionError("a correct prefix should agree fully: %r" % st)
    if not 0.2 < st["progress"] < 0.5:
        raise AssertionError("a third of the way through should read as such: "
                             "%r" % st)

    # Wrong notes: agreement must collapse even though the count matches.
    wrong = log([(10, "0A 0B"), (20, "0C 0D"), (30, "0E 0F"),
                 (40, "10 11"), (50, "12 13"), (60, "14 15")])
    wr = sim.compare(wrong, ref)
    if wr["agreement"] > 0.01:
        raise AssertionError("unrelated states should not agree: %r" % wr)

    # A state held for many frames is one event, not many.
    held = log([(10, "00 01"), (11, "00 01"), (12, "00 01"), (20, "00 02")])
    if len(sim.states(held)) != 2:
        raise AssertionError("repeated rows should collapse to one state")
    return "frame-shift invariant; separates agreement from progress"


def t_sim_random():
    """POKEY's RANDOM must not be a constant.

    Ballblazer generates its music instead of playing a score, and asks
    POKEY for the entropy: `CMP $400A / BCS` skips the note when the
    comparison fails. A simulator returning zero there makes the branch
    always skip, so the engine runs, emits nothing, and the game plays
    silence through a player that is working perfectly. That was a real bug
    here and it took a long time to find, so this pins the register down.
    """
    import sim, cart as cart_module

    class FakeCart(object):
        nbanks = 1
        def pokeys(self):
            return [0x4000]
        def space_of(self, a, b):
            return None
        def byte(self, sp, a):
            return 0xFF

    bus = sim.Bus(FakeCart())
    cyc = [0]
    bus.cpu_cycles = lambda: cyc[0]
    seen = set()
    for step in range(64):
        cyc[0] += 37
        seen.add(bus.read(0x400A))
    if len(seen) < 8:
        raise AssertionError("RANDOM returned %d distinct values in 64 reads; "
                             "it is effectively a constant" % len(seen))
    if seen == {0} or seen == {0xFF}:
        raise AssertionError("RANDOM is stuck at a single value")
    return "%d distinct values over 64 reads" % len(seen)


def t_cycles():
    import m6502
    if len(m6502.CYCLES) != 256:
        raise AssertionError("cycle table has %d entries" % len(m6502.CYCLES))
    for op, want in ((0xEA, 2), (0x20, 6), (0x6C, 5), (0x00, 7), (0xBD, 4)):
        if m6502.CYCLES[op] != want:
            raise AssertionError("opcode $%02X: %d cycles, expected %d"
                                 % (op, m6502.CYCLES[op], want))
    return "256 opcodes, spot-checked against the datasheet"


def t_examples():
    """Every example song loads, renders, and survives a text round trip."""
    import tracker
    n = 0
    tmpdir = tempfile.mkdtemp(prefix="selftest-trk-")
    for f in sorted(glob.glob(os.path.join(ROOT, "examples", "*.trk"))):
        song = tracker.load(f)
        again_path = os.path.join(tmpdir, os.path.basename(f))
        io.open(again_path, "w", encoding="utf-8").write(tracker.dump(song))
        again = tracker.load(again_path)
        if again.rows != song.rows:
            raise AssertionError("%s does not survive dump/load"
                                 % os.path.basename(f))
        tracker.render(song, os.path.join(tmpdir, "out.wav"))
        n += 1
    if not n:
        return None
    return "%d songs parse, render, and round-trip as text" % n


def t_notes():
    """The note tables build, and a named divider re-parses to itself."""
    import tracker
    out = []
    for region in ("ntsc", "pal"):
        rows = tracker.note_table(region)
        if not rows:
            raise AssertionError("no TIA notes for %s" % region)
        out.append("%s %d" % (region, len(rows)))
    return "TIA %s" % ", ".join(out)


def t_refusals():
    """The tool says no where it should, and says why."""
    import songfmt
    base = {"chip": "tia", "songs": [{"n": 0, "voices": [{"order": []}]}],
            "patterns": {}, "durations": [1], "waveforms": [0],
            "instruments": [[0] * 16], "instrument_fields": {},
            "engine": "adsr5"}
    cases = [
        ("unknown chip", dict(base, chip="sid"), "unknown chip"),
        ("unimplemented engine", dict(base, engine="nope"), "not implemented"),
        ("wrong voice count", dict(base, chip="pokey",
                                   songs=[{"n": 0, "voices": [{"order": []}]*5}]),
         "voices but"),
        ("no duration table", dict(base, durations=None), "cannot be rendered"),
    ]
    for name, songs, want in cases:
        try:
            songfmt.render(songs, 0)
        except songfmt.FormatError as e:
            if want not in str(e):
                raise AssertionError("%s: message was %r" % (name, str(e)))
        else:
            raise AssertionError("%s was NOT refused" % name)
    # Every AUDCTL bit is modelled now, so nothing exercises the refusal path
    # by default. Test the mechanism anyway by marking a bit unsupported: it is
    # the thing that stops a future gap being rendered as something plausible.
    import tracker
    saved = dict(tracker.POKEY_UNSUPPORTED)
    tracker.POKEY_UNSUPPORTED[0x80] = "a feature nobody has modelled"
    try:
        song = tracker.Song(chip="pokey")
        song.add([(5, 10, 8), None, None, None], audctl=0x80)
        if not song.unsupported:
            raise AssertionError("an unsupported AUDCTL bit was not noticed")
    finally:
        tracker.POKEY_UNSUPPORTED.clear()
        tracker.POKEY_UNSUPPORTED.update(saved)
    return ("%d bad inputs refused, and the AUDCTL refusal path still works"
            % len(cases))


def t_dualpokey():
    """Eight voices: each chip keeps its own AUDCTL, and the format carries it."""
    import tracker
    if tracker.CHANNELS.get("pokey2") != 8:
        raise AssertionError("pokey2 is not eight channels")
    song = tracker.Song(chip="pokey2", region="ntsc")
    for i in range(8):
        song.add([(5, 10 + c, 8) for c in range(8)], audctl=(0x10, 0x04))
    # channel 0-3 belong to the first chip, 4-7 to the second
    if song.ctl_of(0, 0) != 0x10 or song.ctl_of(0, 5) != 0x04:
        raise AssertionError("a channel got the wrong chip's AUDCTL")
    # 16-bit pairing on chip 0 must not silence a voice on chip 1
    tmp = tempfile.mkdtemp(prefix="selftest-p2-")
    path = os.path.join(tmp, "s.trk")
    io.open(path, "w", encoding="utf-8").write(tracker.dump(song))
    again = tracker.load(path)
    if again.rows != song.rows:
        raise AssertionError("an eight-voice song does not survive dump/load")
    if again.audctl != song.audctl:
        raise AssertionError("the second chip's AUDCTL was lost in the file")
    tracker.render(song, os.path.join(tmp, "s.wav"))
    # and the exporter must refuse rather than emit a one-chip player
    try:
        tracker.export_asm(song)
    except ValueError as e:
        if "two POKEYs" not in str(e):
            raise AssertionError("refused for the wrong reason: %s" % e)
    else:
        raise AssertionError("export of a two-chip song was allowed")
    return "8 voices, per-chip AUDCTL, round-trips; export refused"


def t_trackeredit():
    """The tracker UI must survive every chip, including two POKEYs.

    This exists because it did not. When AUDCTL became one value per chip,
    `trackeredit` kept indexing it as a single number and broke for every POKEY
    song -- and nothing noticed, because the browser tools had no test at all.
    """
    import tracker
    import trackeredit as TE
    tmp = tempfile.mkdtemp(prefix="selftest-te-")
    checked = []
    for chip, nch in (("tia", 2), ("pokey", 4), ("pokey2", 8)):
        song = tracker.Song(title=chip, chip=chip, region="ntsc")
        ctl = (0x00, 0x01) if chip == "pokey2" else 0x00
        for _ in range(8):
            song.add([(5 if chip != "tia" else 4, 20 + c, 8)
                      for c in range(nch)], audctl=ctl)
        path = os.path.join(tmp, chip + ".trk")
        io.open(path, "w", encoding="utf-8").write(tracker.dump(song))
        TE.SONG, TE.PATH = tracker.load(path), path
        j = TE.song_json()
        if j["nch"] != nch:
            raise AssertionError("%s: %d channels, expected %d"
                                 % (chip, j["nch"], nch))
        if len(j["rows"][0]["audctl"]) != max(1, TE.SONG.nchips):
            raise AssertionError("%s: wrong number of AUDCTL values" % chip)
        # every channel must format and re-parse
        for c in range(nch):
            text = j["rows"][0]["text"][c]
            TE.set_cell(0, c, text)
        TE.render_range(0, 4)
        checked.append(chip)
    return "%s all format, edit and render" % ", ".join(checked)


def t_midi():
    """MIDI in, notes out -- including the two things that break naive parsers."""
    import struct
    import midi as M
    import tracker

    def vlq(n):
        out = [n & 0x7F]
        n >>= 7
        while n:
            out.append((n & 0x7F) | 0x80)
            n >>= 7
        return bytes(reversed(out))

    def trk(events, name):
        body = vlq(0) + bytes([0xFF, 0x03]) + vlq(len(name)) + name
        for d, data in events:
            body += vlq(d) + data
        body += vlq(0) + bytes([0xFF, 0x2F, 0x00])
        return b"MTrk" + struct.pack(">I", len(body)) + body

    div = 480
    mel = []
    for n in (60, 62, 64):
        mel.append((0, bytes([0x90, n, 100])))
        mel.append((div, bytes([0x80, n, 0])))
    # the chord track omits status bytes after the first: running status, which
    # is the classic way to lose two thirds of a chord without noticing
    chord = [(0, bytes([0x91, 48, 90])), (0, bytes([52, 90])),
             (0, bytes([55, 90])), (div, bytes([0x81, 48, 0])),
             (0, bytes([52, 0])), (0, bytes([55, 0]))]
    data = (b"MThd" + struct.pack(">IHHh", 6, 1, 2, div)
            + trk(mel, b"lead") + trk(chord, b"chord"))
    tmp = tempfile.mkdtemp(prefix="selftest-midi-")
    path = os.path.join(tmp, "t.mid")
    with open(path, "wb") as f:
        f.write(data)

    doc = M.read(path)
    lead, ch = doc["tracks"][0], doc["tracks"][1]
    if len(lead["notes"]) != 3:
        raise AssertionError("melody: %d notes, expected 3" % len(lead["notes"]))
    if len(ch["notes"]) != 3:
        raise AssertionError("running status lost notes: got %d of 3"
                             % len(ch["notes"]))
    if M.max_poly(ch["notes"]) != 3:
        raise AssertionError("the chord did not read as three-voice")
    if abs(lead["notes"][0]["end"] - 0.5) > 0.01:
        raise AssertionError("a quarter note at 120bpm is not 0.5s: %.3f"
                             % lead["notes"][0]["end"])

    # a note-on with velocity 0 is a note-off, not a note that never ends
    off0 = trk([(0, bytes([0x90, 60, 100])), (div, bytes([0x90, 60, 0]))], b"v0")
    p2 = os.path.join(tmp, "v0.mid")
    with open(p2, "wb") as f:
        f.write(b"MThd" + struct.pack(">IHHh", 6, 1, 1, div) + off0)
    n = M.read(p2)["tracks"][0]["notes"]
    if len(n) != 1 or abs(n[0]["end"] - 0.5) > 0.01:
        raise AssertionError("velocity-0 note-on was not treated as note-off")

    # and the monophonic fold picks the note it claims to
    hi, dropped = tracker.midi_voice(ch["notes"], 30, 60.0, "high")
    lo, _ = tracker.midi_voice(ch["notes"], 30, 60.0, "low")
    if hi[0]["note"] != 55 or lo[0]["note"] != 48:
        raise AssertionError("--pick did not choose the stated note")
    if not dropped:
        raise AssertionError("dropped notes were not counted")

    # --- and the same import through the tracker UI, which is where it is
    # actually used: one track into one voice, leaving the others alone.
    import base64
    import trackeredit as TE
    song = tracker.Song(title="host", chip="pokey", region="ntsc")
    for _ in range(200):
        song.add([(5, 40, 8), (5, 41, 8), (5, 42, 8), (5, 43, 8)])
    TE.SONG, TE.PATH = song, os.path.join(tmp, "host.trk")
    keep = [song.rows[i][3] for i in range(0, 50)]

    info = TE.midi_open(base64.b64encode(data).decode())
    if len(info["tracks"]) != 2:
        raise AssertionError("the UI saw %d importable tracks, expected 2"
                             % len(info["tracks"]))
    if info["tracks"][1]["poly"] != 3:
        raise AssertionError("the UI did not report the chord as polyphonic")

    r = TE.midi_apply(0, 0, "high", 5, 8, 0, True)
    if not r["placed"]:
        raise AssertionError("importing into a voice placed nothing")
    if [TE.SONG.rows[i][3] for i in range(0, 50)] != keep:
        raise AssertionError("importing into voice 1 disturbed voice 4")

    r2 = TE.midi_apply(1, 1, "low", 5, 8, 0, True)
    if not r2["dropped"]:
        raise AssertionError("the chord's dropped notes were not counted")

    for bad, why in (((99, 0), "a track that does not exist"),
                     ((0, 9), "a voice that does not exist")):
        try:
            TE.midi_apply(bad[0], bad[1], "high", 5, 8, 0, True)
        except ValueError:
            pass
        else:
            raise AssertionError("%s was accepted" % why)
    return ("running status, tempo, velocity-0 offs; --pick honoured; "
            "UI fills one voice without touching the others")


def t_helps():
    """Every tool answers --help without blowing up."""
    bad = []
    for f in sorted(glob.glob(os.path.join(HERE, "*.py"))):
        name = os.path.basename(f)
        if name.startswith("_"):
            continue
        p = subprocess.run([sys.executable, f, "--help"],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        if p.returncode != 0:
            bad.append("%s (exit %d)" % (name, p.returncode))
    if bad:
        raise AssertionError("; ".join(bad))
    return "every tool has working --help"


def t_readme():
    """The README lists the tools that exist, and no others."""
    s = io.open(os.path.join(ROOT, "README.md"), encoding="utf-8").read()
    claimed = set(re.findall(r"^\| `([a-z0-9_]+\.py)`", s, re.M))
    actual = set(os.path.basename(f)
                 for f in glob.glob(os.path.join(HERE, "*.py")))
    libs = set(["a7800.py", "m6502.py"])
    missing = sorted(claimed - actual)
    undocumented = sorted(actual - claimed - libs)
    if missing:
        raise AssertionError("README lists tools that do not exist: %s"
                             % ", ".join(missing))
    if undocumented:
        raise AssertionError("tools missing from the README: %s"
                             % ", ".join(undocumented))
    return "%d tools documented, none missing" % len(claimed)


def t_links():
    """No markdown link points at a file that is not there."""
    bad = []
    for root, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__")]
        for f in files:
            if not f.endswith(".md"):
                continue
            p = os.path.join(root, f)
            s = io.open(p, encoding="utf-8").read()
            for m in re.finditer(
                    r"\]\(([^)#:]+\.(?:md|json|py|png|html))[^)]*\)", s):
                t = os.path.normpath(os.path.join(root, m.group(1)))
                if not os.path.exists(t):
                    bad.append("%s -> %s"
                               % (os.path.relpath(p, ROOT), m.group(1)))
    if bad:
        raise AssertionError("; ".join(bad))
    return "every markdown link resolves"


# ------------------------------------------------------------ with a cartridge

def _pull(rom, fmt):
    import cart as cart_module
    import songfmt
    cart = cart_module.Cart(rom)
    f = json.load(io.open(fmt, encoding="utf-8"))
    return cart, f, songfmt.pull(cart, f)


def t_pull_push(rom, fmt):
    """Pull then push unedited must give back the same bytes."""
    if not (rom and fmt):
        return None
    import songfmt
    cart, f, songs = _pull(rom, fmt)
    writes = songfmt.push(cart, f, songs)
    raw = bytearray(io.open(rom, "rb").read())
    out = songfmt.apply_writes(raw, cart, writes)
    original = io.open(rom, "rb").read()
    if bytes(out) != original:
        n = sum(1 for a, b in zip(out, original) if a != b)
        raise AssertionError("a no-op push changed %d bytes" % n)
    return "%d patterns, no-op push changes 0 bytes" % len(songs["patterns"])


def t_grow(rom, fmt):
    """A pattern that grew must be refused, not written."""
    if not (rom and fmt):
        return None
    import songfmt
    cart, f, songs = _pull(rom, fmt)
    keys = sorted(songs["patterns"])
    if not keys:
        return None
    pat = songs["patterns"][keys[0]]
    pat["notes"].append(dict(pat["notes"][0]))
    try:
        songfmt.push(cart, f, songs)
    except songfmt.FormatError as e:
        if "cannot grow" not in str(e):
            raise AssertionError("refused, but for the wrong reason: %s" % e)
        return "growing a pattern is refused"
    raise AssertionError("growing a pattern was allowed")


def t_verify(rom, fmt, log, song_n, region):
    """The renderer must reproduce what the hardware actually played."""
    if not (rom and fmt and log):
        return None
    import songfmt
    import tracker
    _cart, _f, songs = _pull(rom, fmt)
    tracks, _ctl = songfmt.render(songs, song_n)
    played = list(tracker.read_capture(log, region).states())
    best = (0, -1, 0)
    for ch, ours in enumerate(tracks):
        for off in range(max(1, len(played) - 50)):
            n = min(len(ours), len(played) - off)
            if n < 100:
                break
            same = sum(1 for i in range(n) if ours[i] == played[off + i][ch])
            if same > best[1]:
                best = (off, same, n)
    off, same, n = best
    if not n:
        return None
    pct = 100.0 * same / n
    if pct < 100.0:
        raise AssertionError("song %d matches on only %d of %d frames (%.1f%%) "
                             "at offset %d" % (song_n, same, n, pct, off))
    return "song %d: %d of %d frames identical to hardware" % (song_n, same, n)


def _unterminated_strings(js):
    """Lines where a JS string literal opens and the line ends still inside it.

    Single- and double-quoted JS strings cannot span lines, so this is exact
    rather than heuristic: reaching a newline inside one is always a syntax
    error. Template literals may legitimately span lines and are skipped, as
    are both kinds of comment -- an apostrophe in a comment is fine, the same
    apostrophe in `'the player's own format'` is not.
    """
    bad, i, line, instr, start = [], 0, 1, None, None
    while i < len(js):
        ch = js[i]
        nxt = js[i + 1] if i + 1 < len(js) else ""
        if ch == "\n":
            line += 1
            if instr in ("'", '"'):
                bad.append((start, instr))
                instr = None
        if instr:
            if ch == "\\":
                i += 2
                continue
            if ch == instr:
                instr = None
        elif ch in "'\"`":
            instr, start = ch, line
        elif ch == "/" and nxt == "/":
            while i < len(js) and js[i] != "\n":
                i += 1
            continue
        elif ch == "/" and nxt == "*":
            i += 2
            while i + 1 < len(js) and not (js[i] == "*" and js[i + 1] == "/"):
                if js[i] == "\n":
                    line += 1
                i += 1
            i += 2
            continue
        i += 1
    return bad


def t_handler_attributes():
    """No page may build a double-quoted HTML attribute out of JSON.

    `JSON.stringify("b6:A951")` returns `"b6:A951"` *including the quotes*, so
    concatenating it into `onclick="handler(...)"` closes the attribute early:

        <button onclick="openExplore("b6:A951")">

    which the browser reads as `onclick="openExplore("`. The button renders,
    looks right, and does nothing when clicked. Nothing else notices -- the
    script parses, the server is healthy, and a test that drives the API over
    HTTP never renders the button at all. That is how this shipped.

    The invariant is exact rather than stylistic: JSON output always contains
    double quotes, so it can never appear inside a double-quoted attribute. It
    is perfectly legal inside a single-quoted one, which is how the format
    explorer passes whole states to its handlers, so only the double-quoted
    case is an error.

    This does not check that the page works -- there is no JavaScript engine
    here to run it. It checks one specific way of breaking a page that no
    server-side test can see.
    """
    import glob
    import re
    checked = 0
    # an on*="  attribute, then anything up to the quote that closes it
    pattern = re.compile(r"""on[a-z]+="[^"]*?\+\s*JSON\.stringify\(""", re.I)
    for path in sorted(glob.glob(os.path.join(HERE, "*.py"))):
        if os.path.basename(path) == os.path.basename(__file__):
            continue        # this file quotes the tags it searches for
        src = io.open(path, encoding="utf-8").read()
        for m in re.finditer(r"<script>(.*?)</script>", src, re.S):
            checked += 1
            hit = pattern.search(m.group(1))
            if hit:
                line = src[:m.start(1) + hit.start()].count("\n") + 1
                raise AssertionError(
                    "%s line %d builds a double-quoted HTML attribute with "
                    "JSON.stringify, whose output contains double quotes. The "
                    "attribute ends early and the handler never runs. Use "
                    "&quot; around an escaped value, or single-quote the "
                    "attribute." % (os.path.basename(path), line))
    if not checked:
        raise AssertionError("found no embedded <script> to check")
    return "%d scripts, no attribute built from quote-bearing JSON" % checked


def t_direct_format():
    """A reading saved by explore.py reads back as the same notes.

    This is the join that makes the explorer worth using rather than merely
    interesting: work out what the bytes mean by ear, press save, and the
    tracker opens them. If the emitted file and the reader disagree about what
    a field means, the saved finding is quietly wrong and nobody notices until
    it is played back weeks later.

    Both samples are the real bytes whose layout is already established, so the
    expected notes are known independently of either side of this round trip.
    """
    import songfmt
    import explore

    PARALLEL = bytes.fromhex("00EA0003C0B2A5B2A5B20014141414140A0A0A0A0A00")
    SERIAL = bytes.fromhex(
        "093D09370933093A09340931"
        "093D09370933093A09340931"
        "093809330930093A0933092F"
        "093D09370933042E")

    class FakeCart(object):
        """Enough cartridge for the reader: a slice at an address."""

        def __init__(self, blob, base):
            self.blob, self.base, self.rom = blob, base, blob
            self.info = {"title": "test"}

        def slice(self, _space, addr, n):
            i = addr - self.base
            if i < 0 or i + n > len(self.blob):
                raise IndexError("past the sample")
            return self.blob[i:i + n]

    # --- parallel: two streams, a zero duration ends the part ---------------
    cart = FakeCart(PARALLEL, 0xC333)
    fmt = {"name": "test", "reader": "direct", "chip": "pokey",
           "guessed": True,
           "voice": {"shape": "parallel", "pitch_at": "rom:C333",
                     "dur_at": "rom:C33E", "count": 32},
           "playback": {"audc": 12, "volume": 8, "rate": 60.0}}
    doc = songfmt.pull(cart, fmt)
    notes = doc["voices"][0]["notes"]
    want_p = [0x00, 0xEA, 0x00, 0x03, 0xC0, 0xB2, 0xA5, 0xB2, 0xA5, 0xB2]
    want_d = [0x14] * 5 + [0x0A] * 5
    if [n["pitch"] for n in notes] != want_p:
        raise AssertionError("parallel pitches read back as %r, expected %r"
                             % ([n["pitch"] for n in notes], want_p))
    if [n["duration"] for n in notes] != want_d:
        raise AssertionError("parallel durations read back as %r"
                             % [n["duration"] for n in notes])
    frames = songfmt.render_direct(doc)[0]
    if len(frames) != sum(want_d):
        raise AssertionError("rendered %d frames for %d frames of notes"
                             % (len(frames), sum(want_d)))

    # --- serial: fixed-size records, pitch in the second byte --------------
    cart = FakeCart(SERIAL, 0x7D23)
    fmt = {"name": "test", "reader": "direct", "chip": "tia", "guessed": True,
           "voice": {"shape": "serial", "at": "f6:7D23", "count": 22,
                     "stride": 2, "pitch": [1, 0, 8], "duration": [0, 0, 4]},
           "playback": {"audc": 12, "volume": 8, "rate": 60.0}}
    doc = songfmt.pull(cart, fmt)
    notes = doc["voices"][0]["notes"]
    if len(notes) != 22:
        raise AssertionError("expected 22 notes, got %d" % len(notes))
    if [n["pitch"] for n in notes[:6]] != [0x3D, 0x37, 0x33, 0x3A, 0x34, 0x31]:
        raise AssertionError("serial pitches read back as %r"
                             % [n["pitch"] for n in notes[:6]])
    if notes[0]["duration"] != 9:
        raise AssertionError("duration field read as %d, expected 9"
                             % notes[0]["duration"])

    # --- what explore writes is what the reader expects --------------------
    saved = explore.CART, explore.ROM
    try:
        explore.CART = cart
        explore.ROM = os.path.join(tempfile.gettempdir(), "sample.a78")
        st = explore.default_state("f6", 0x7D23)
        st.update({"shape": "serial", "stride": 2, "pitch_byte": 1,
                   "pitch_shift": 0, "pitch_bits": 8, "dur_byte": 0,
                   "dur_shift": 0, "dur_bits": 4, "count": 22, "chip": "tia"})
        emitted = explore.emit_format(st)
        back = songfmt.pull(cart, emitted)["voices"][0]["notes"]
        if [n["pitch"] for n in back] != [n["pitch"] for n in notes]:
            raise AssertionError(
                "explore.py emits a format that reads back different notes "
                "than the reading it was written from")
        if not emitted.get("guessed"):
            raise AssertionError("an emitted reading must be marked guessed")
    finally:
        explore.CART, explore.ROM = saved

    return ("parallel and serial readings round-trip through the saved "
            "format, and what explore writes is what songfmt reads")


def t_browser_js():
    """Every browser tool's embedded script must actually parse.

    These tools serve a page whose JavaScript is written inline in a Python
    string, and one stray apostrophe -- `'the player's own format'` -- closes
    the string early and makes the whole script a syntax error. The server
    stays perfectly healthy, answering every request with correct JSON, while
    the page renders blank. That is precisely why no server-side test caught
    it: the workbench passed its checks and was blank for every cartridge.

    So this parses what the browser parses. It is not a full JS parser, but
    unterminated string literals are the failure this class of tool actually
    ships, because the page is assembled by concatenating quoted fragments.
    """
    import glob
    import re
    checked = 0
    for path in sorted(glob.glob(os.path.join(HERE, "*.py"))):
        if os.path.basename(path) == os.path.basename(__file__):
            continue        # this file quotes the tags it searches for
        src = io.open(path, encoding="utf-8").read()
        for m in re.finditer(r"<script>(.*?)</script>", src, re.S):
            checked += 1
            bad = _unterminated_strings(m.group(1))
            if bad:
                line, quote = bad[0]
                before = src[:m.start(1)].count("\n")
                raise AssertionError(
                    "%s: a %s-quoted string opens on line %d of its script "
                    "(about line %d of the file) and never closes, so the "
                    "page renders blank."
                    % (os.path.basename(path), quote, line, before + line))
    if not checked:
        raise AssertionError("found no embedded <script> to check")
    return "%d embedded scripts, every string literal closed" % checked


def t_explore():
    """The format guesser, against two layouts whose answers are known.

    Both samples are real bytes lifted from cartridges whose players were
    established the hard way and confirmed against `tracker.py capture`: 22
    notes of a Midnight Mutants pattern (serial, two bytes a note, pitch in
    the second) and one Arkanoid part (parallel, pitches then durations, the
    two streams laid end to end so the gap is 11). Carrying the bytes here
    rather than reading a cartridge keeps the package ROM-free and tests the
    algorithm instead of one image.

    The bar is that the true reading comes out *first*, not merely somewhere
    in the list -- the tool exists to save a person from auditioning fifty
    wrong readings, so a truth ranked fourth is a failure.
    """
    import explore

    SERIAL = bytes.fromhex(
        "093D09370933093A09340931"      # the phrase, twice
        "093D09370933093A09340931"
        "093809330930093A0933092F"      # then a variation
        "093D09370933042E")             # and out on a new instrument
    PARALLEL = bytes.fromhex("00EA0003C0B2A5B2A5B20014141414140A0A0A0A0A00")

    class FakeCart(object):
        """Just enough cartridge for the guesser: bytes at an address."""

        def __init__(self, blob, base):
            self.blob, self.base = blob, base

        def byte(self, _space, addr):
            i = addr - self.base
            if 0 <= i < len(self.blob):
                return self.blob[i]
            raise IndexError("past the sample")

    saved = explore.CART
    try:
        explore.CART = FakeCart(SERIAL, 0x7D23)
        stride, _ = explore.record_stride("f6", 0x7D23)
        if stride != 2:
            raise AssertionError("record size of a 2-byte serial format read "
                                 "as %d" % stride)
        moves, holds = explore.varying_column("f6", 0x7D23, 2)
        if (moves, holds) != (1, 0):
            raise AssertionError("the moving column of `09 3D 09 37 ...` is "
                                 "byte 1, not byte %d" % moves)
        top = explore.suggest("f6", 0x7D23, "tia")[0][1]
        if "2 bytes a note" not in top or "pitch in byte 1" not in top:
            raise AssertionError("best reading of the serial sample was %r, "
                                 "expected 2 bytes a note with the pitch in "
                                 "byte 1" % top)

        explore.CART = FakeCart(PARALLEL, 0xC333)
        top = explore.suggest("rom", 0xC333, "pokey")[0][1]
        if "parallel" not in top or "durations 11 bytes" not in top:
            raise AssertionError("best reading of the parallel sample was "
                                 "%r, expected parallel with a gap of 11"
                                 % top)
    finally:
        explore.CART = saved
    return ("both known layouts ranked first: 2-byte serial records with the "
            "pitch in byte 1, and parallel streams 11 apart")



def t_sprites(rom):
    """A painted pixel lands in the right byte, and only in that byte."""
    if not rom:
        return None
    import spriteedit as SE
    import cart as cart_module
    c = cart_module.Cart(rom)
    space = c.spaces()[0]
    SE.CART, SE.PATH = c, rom
    SE.DATA = bytearray(io.open(rom, "rb").read())
    SE.REGION = SE.Region(c, space, c.base_of(space), 1, 8, 256, 256, "160")

    # every offset the region computes must agree with the cart's own reader
    for a in range(c.base_of(space), c.base_of(space) + c.size_of(space), 811):
        if SE.DATA[SE.REGION.file_offset(a)] != c.byte(space, a):
            raise AssertionError("file offset for $%04X disagrees with the "
                                 "cartridge reader" % a)

    # MARIA counts a zone offset down, so line 0 is the HIGHEST page. Getting
    # this backwards renders every sprite upside down and still looks like a
    # sprite, which is why it went unnoticed until someone eyeballed a sheet.
    r = SE.REGION
    top = r.addr(0, 0, 0)
    bottom = r.addr(0, r.height - 1, 0)
    if top <= bottom:
        raise AssertionError(
            "line 0 is at $%04X and the last line at $%04X -- the zone offset "
            "must count down" % (top, bottom))
    if top != r.base + (r.height - 1) * r.stride:
        raise AssertionError("line 0 is not at base + (height-1)*stride")
    flat = SE.Region(c, space, c.base_of(space), 1, 8, 256, 256, "160",
                     descending=False)
    if flat.addr(0, 0, 0) != flat.base:
        raise AssertionError("--ascending did not restore the flat order")

    # palettes: greys by default, a chosen palette applies, and it goes back
    if SE.PALETTE != SE.GREYS:
        raise AssertionError("the default palette is not the greys")
    SE.set_palette([None, 0x24, 0x76, 0x7C])
    if SE.PALETTE == SE.GREYS or SE.PALETTE[0] != SE.GREYS[0]:
        raise AssertionError("a chosen palette did not apply, or index 0 moved")
    SE.set_palette([None, None, None, None])
    if SE.PALETTE != SE.GREYS:
        raise AssertionError("clearing the palette did not restore the greys")

    before = SE.REGION.pixels(70)
    want = 3 if before[0][0] != 3 else 1
    SE.REGION.set_pixel(70, 0, 0, want)
    if SE.REGION.pixels(70)[0][0] != want:
        raise AssertionError("a painted pixel did not read back")


    out = os.path.join(tempfile.mkdtemp(prefix="selftest-spr-"), "out.a78")
    r = SE.save(out)
    if r["changed"] != 1:
        raise AssertionError("one pixel changed %d bytes" % r["changed"])

    # the whole-cell path every drawing tool uses
    wide = SE.REGION.width * SE.REGION.ppb
    grid = [[(x + y) % (1 << SE.REGION.bpp) for x in range(wide)]
            for y in range(SE.REGION.height)]
    if SE.REGION.set_cell(71, grid) != grid:
        raise AssertionError("a whole-cell write did not read back")
    for bad, why in (([[0] * wide], "too few lines"),
                     ([[0] * (wide + 1)] * SE.REGION.height, "wrong width")):
        try:
            SE.REGION.set_cell(71, bad)
        except ValueError:
            pass
        else:
            raise AssertionError("a cell with the %s was accepted" % why)

    # and a byte outside the region must stop the save
    SE.DATA[9] ^= 0xFF
    blocked = os.path.join(os.path.dirname(out), "blocked.a78")
    try:
        SE.save(blocked)
    except ValueError:
        pass
    else:
        raise AssertionError("a write outside the region was allowed")
    if os.path.exists(blocked):
        raise AssertionError("the refused save still wrote a file")
    return ("zone offset counts down; palette applies and clears; pixel "
            "and whole-cell writes land correctly; bad shapes and "
            "out-of-region writes refused")


def t_workbench(rom):
    """The launcher describes a cartridge, scans it, and cleans up after itself."""
    if not rom:
        return None
    import cart as cart_module
    import workbench as WB
    WB.ROM = os.path.abspath(rom)
    WB.CART = cart_module.Cart(WB.ROM)
    info = WB.cart_info()
    if not info["spaces"] or not info["vectors"]:
        raise AssertionError("the cartridge summary came out empty")
    m = WB.scan()
    if "graphics" not in m or "audio" not in m:
        raise AssertionError("the scan returned nothing usable")

    # A launched editor must actually serve, and must actually stop. A child
    # left holding its port looks exactly like a stale server on the next run.
    import socket
    import time
    r = WB.launch("spriteedit.py",
                  ["--space", info["spaces"][0]["name"],
                   "--base", str(info["spaces"][0]["start"])], "selftest")
    port = r["port"]

    def up():
        s = socket.socket()
        s.settimeout(0.5)
        try:
            s.connect(("127.0.0.1", port))
            return True
        except OSError:
            return False
        finally:
            s.close()

    for _ in range(40):
        if up():
            break
        time.sleep(0.15)
    else:
        WB.stop_all()
        raise AssertionError("a launched editor never came up on %d" % port)
    WB.stop_all()
    for _ in range(30):
        if not up():
            break
        time.sleep(0.15)
    else:
        raise AssertionError("a launched editor was still holding port %d "
                             "after stop_all" % port)
    return ("summarises, scans (%d graphics, %d audio), launches and stops "
            "cleanly" % (len(m["graphics"]), len(m["audio"])))


def t_songs_from_rom(rom, fmt):
    """A cartridge's songs read out of the ROM, with no emulator involved.

    `capture` records whatever a game happens to play in the window you gave
    it. This reads what it *can* play -- every song, in a moment rather than a
    minute -- and it is the route that should be tried first whenever a format
    file describes the cartridge.
    """
    if not (rom and fmt):
        return None
    import trackeredit as TE

    found = TE.find_format(rom)
    if not found:
        raise AssertionError(
            "no shipped format matched this cartridge, so the tracker would "
            "fall back to recording it. Check the `match` block in %s."
            % os.path.basename(fmt))

    songs, pulled = TE.songs_from_rom(rom, found)
    usable = [s for s in songs if s.get("rows")]
    if not usable:
        raise AssertionError("reading the ROM produced no playable songs")
    if not any(s["sounding"] for s in usable):
        raise AssertionError("every song read out of the ROM is silent")

    # the match block has to actually describe this cartridge
    import json
    import audiotrace
    import cart as cart_module
    doc = json.load(io.open(found, encoding="utf-8"))
    m = doc.get("match") or {}
    if not m:
        raise AssertionError("%s has no match block, so nothing would find it"
                             % os.path.basename(found))
    c = cart_module.Cart(rom)
    if "player" in m:
        sig = audiotrace.player_signature(c.rom)
        if sig != m["player"]:
            raise AssertionError("the match block's player fingerprint (%s) is "
                                 "not this ROM's (%s)" % (m["player"], sig))
    if "size" in m and int(m["size"]) != len(c.rom):
        raise AssertionError("the match block's size (%d) is not this ROM's (%d)"
                             % (int(m["size"]), len(c.rom)))
    # and it must not match something unrelated
    sig = audiotrace.player_signature(c.rom)
    if sig and audiotrace.player_signature(bytes(len(c.rom))) == sig:
        raise AssertionError("the fingerprint is not discriminating")
    return ("%d songs read from the ROM, %d with sound, no emulator"
            % (len(usable), sum(1 for s in usable if s["sounding"])))


def t_formats():
    """Every shipped format file is well formed and says what it matches."""
    import glob as _glob
    import json
    seen = {}
    for path in sorted(_glob.glob(os.path.join(ROOT, "formats", "*.json"))):
        doc = json.load(io.open(path, encoding="utf-8"))
        name = os.path.basename(path)
        m = doc.get("match")
        if not m:
            raise AssertionError("%s has no match block, so nothing finds it"
                                 % name)
        key = m.get("player") or (("magic:" + m["magic"]) if "magic" in m
                                  else None)
        if not key:
            raise AssertionError("%s matches on title/size only. A format file "
                                 "describes an engine or a container; use "
                                 "audiotrace.py --signature, or `magic` for "
                                 "something that identifies itself." % name)
        if key in seen:
            raise AssertionError("%s and %s both claim %s"
                                 % (name, seen[key], key))
        seen[key] = name
        reader = doc.get("reader", "nested")
        if reader not in ("nested", "parallel", "rmt"):
            raise AssertionError("%s wants reader %r, which songfmt does not "
                                 "implement" % (name, reader))
    return "%d format files, each fingerprinted and unique" % len(seen)


def t_disasm(rom):
    if not rom:
        return None
    out = tempfile.mkdtemp(prefix="selftest-")
    p = subprocess.run([sys.executable, os.path.join(HERE, "disasm.py"), rom,
                        "-o", out], stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT)
    if p.returncode != 0:
        raise AssertionError("disasm exited %d" % p.returncode)
    listings = glob.glob(os.path.join(out, "*.asm"))
    if not listings:
        raise AssertionError("no listings written")
    return "%d listings written" % len(listings)


# ----------------------------------------------------------------- merged in
# These seven come from the tree this toolkit was forked into for
# the Karateka work. They cover the tools that arrived with them
# -- a8dis, portscan, forth, patchset, portkit -- plus the two
# guarantees those tools make: that a conversion recipe carries
# coordinates and not content, and that a published patch carries
# none of the cartridge it patches.


def t_engine_finder():
    """The engine hunter, against a cartridge built to have exactly one.

    `audiotrace.py --engine` searches a cartridge for the Atari in-house music
    engine by the shape of its tables. A search like that is only worth having
    if a wrong answer is impossible rather than merely unlikely, so this builds
    an image whose engine is at known addresses and checks all four are found.

    Synthetic rather than a real cartridge, so the package stays ROM-free and
    the test states the format instead of assuming a particular game.
    """
    import audiotrace

    DUR = [0x60, 0x48, 0x40, 0x30, 0x24, 0x20, 0x18, 0x12,
           0x10, 0x0C, 0x09, 0x08, 0x06, 0x04, 0x03, 0x02]
    BASE, SIZE = 0x4000, 0x1000
    DURS, INSTR = 0x4100, 0x4110
    PAT_A, PAT_B = 0x4300, 0x4320
    TRK_1, TRK_2 = 0x4400, 0x4410
    SONGS = 0x4500

    img = bytearray(SIZE)

    def put(addr, data):
        img[addr - BASE:addr - BASE + len(data)] = bytes(data)

    def word(addr, value):
        put(addr, [value & 0xFF, value >> 8])

    put(DURS, DUR)
    for k in range(16):
        # ten bytes used, six of padding -- the shape the search keys on
        put(INSTR + k * 16, [0xA0, 0x01, 0x80, 0x80, 0x05,
                             0x02, 0x13, 0x01, 0x28, k + 1] + [0] * 6)
    # Fence the table with words that cannot resolve, so its start and end are
    # unambiguous. Without this the run can begin a couple of bytes early on
    # padding, and -- because every song here has the same two pointers -- a
    # four-voice reading of the same bytes validates just as well as the real
    # two-voice one and outscores it. Real tables are not that uniform; a test
    # image has to be fenced deliberately to stand in for that.
    for a in range(0x44F0, 0x4500):
        img[a - BASE] = 0xFF
    put(PAT_A, [4] + [0x16, 0x60, 0x1B, 0x48, 0x1B, 0x48, 0x13, 0x55])
    put(PAT_B, [3] + [0x1C, 0x23, 0x1C, 0x2A, 0x13, 0x90])
    word(TRK_1, PAT_A)
    word(TRK_1 + 2, PAT_B)
    word(TRK_1 + 4, 0x0009)          # high byte zero ends the list
    word(TRK_2, PAT_B)
    word(TRK_2 + 2, 0x0000)
    for n in range(6):               # six songs, two voices, stride 4
        word(SONGS + n * 4, TRK_1)
        word(SONGS + n * 4 + 2, TRK_2)
    # Six two-voice songs is 24 bytes, which is three four-voice entries and a
    # remainder -- so a four-voice reading runs into the fence after three and
    # falls short of the minimum, while the two-voice reading gets all six.
    for a in range(SONGS + 24, SONGS + 40):
        img[a - BASE] = 0xFF

    class FakeCart(object):
        """One fixed space, which is all the search needs."""

        def __init__(self, blob):
            self.rom = bytes(blob)
            self.info = {"title": "synthetic"}

        def spaces(self):
            return ["f0"]

        def base_of(self, _sp):
            return BASE

        def size_of(self, _sp):
            return SIZE

        def pokeys(self):
            return []

        def byte(self, _sp, addr):
            i = addr - BASE
            if not 0 <= i < SIZE:
                raise IndexError("outside the image")
            return self.rom[i]

        def slice(self, _sp, addr, n):
            i = addr - BASE
            if i < 0 or i + n > SIZE:
                raise IndexError("outside the image")
            return self.rom[i:i + n]

        def space_of(self, addr, _bank=None):
            return "f0" if BASE <= addr < BASE + SIZE else None

    cart = FakeCart(img)
    found = audiotrace.find_engine(cart)
    if not found:
        raise AssertionError("the engine hunter found nothing in an image "
                             "built to contain exactly one")
    for name, want, got in (("instruments", INSTR, found["instruments"]),
                            ("durations", DURS, found["durations"]),
                            ("song table", SONGS, found["songs"])):
        if got != want:
            raise AssertionError("%s found at $%04X, expected $%04X"
                                 % (name, got, want))
    if found["voices"] != 2 or found["stride"] != 4:
        raise AssertionError("read %d voices at stride %d, expected 2 at 4"
                             % (found["voices"], found["stride"]))
    if found["verified"] < 6:
        raise AssertionError("only %d of 6 songs followed down to patterns"
                             % found["verified"])
    if found["duration_values"] != DUR:
        raise AssertionError("duration table read back wrong")

    # ...and the format it writes must describe the same thing
    doc = audiotrace.engine_format(cart, "synthetic.a78", found)
    import songfmt
    pulled = songfmt.pull(cart, doc)
    if len(pulled["songs"]) != 6:
        raise AssertionError("the emitted format pulls %d songs, not 6"
                             % len(pulled["songs"]))
    if set(pulled["patterns"]) != {"f0:%04X" % PAT_A, "f0:%04X" % PAT_B}:
        raise AssertionError("the emitted format resolves the wrong patterns: "
                             "%s" % sorted(pulled["patterns"]))
    return ("engine located at its known addresses, and the format it emits "
            "pulls the same songs back")


def t_forth_decompiler():
    """The Forth decompiler, against an image built to be one.

    `forth.py` finds a threaded image's interpreter by shape -- the inner loop
    every primitive jumps to, the routine that saves the thread pointer, the
    one that restores it, the word that eats the following cell -- and then
    walks the thread. All four have to be right together: get the literal
    wrong and every number in the program decompiles as a call to whatever
    address it happens to equal, which reads perfectly and means nothing.

    So this assembles a small indirect-threaded image with a known kernel and
    two known definitions, and checks the tool recovers all of it. Synthetic,
    so the package stays ROM-free and the test states the format rather than
    depending on one cartridge.
    """
    import forth

    BASE, SIZE = 0x4000, 0x1000
    IP = 0xE8
    NEXT = 0x4000
    DOCOL = 0x4030
    BRTAIL = 0x4050          # IP += inline cell
    SKIPTAIL = 0x4060        # IP += 2
    LIT = 0x4070             # word; its code is at LIT+2
    EXIT = 0x4080
    BRANCH = 0x4090
    ADD = 0x40A0             # an ordinary primitive
    DEF1 = 0x4100
    DEF2 = 0x4140

    img = bytearray(b"\xFF" * SIZE)

    def put(addr, data):
        img[addr - BASE:addr - BASE + len(data)] = bytes(data)

    def word(addr, v):
        put(addr, [v & 0xFF, v >> 8])

    # NEXT: LDY #1 / LDA (IP),Y / STA $EC / DEY / LDA (IP),Y / STA $EB
    #       / CLC / LDA IP / ADC #2 / STA IP / BCC / INC IP+1 / JMP ($00EA)
    put(NEXT, [0xA0, 0x01, 0xB1, IP, 0x85, 0xEC, 0x88, 0xB1, IP, 0x85, 0xEB,
               0x18, 0xA5, IP, 0x69, 0x02, 0x85, IP, 0x90, 0x02, 0xE6, IP + 1,
               0x4C, 0xEA, 0x00])
    # DOCOL: LDA IP+1 / PHA / LDA IP / PHA / ... / JMP NEXT
    put(DOCOL, [0xA5, IP + 1, 0x48, 0xA5, IP, 0x48, 0x18, 0xA5, 0xEB,
                0x69, 0x02, 0x85, IP, 0x98, 0x65, 0xEC, 0x85, IP + 1,
                0x4C, NEXT & 0xFF, NEXT >> 8])
    # the two tails that mean "the next cell is data"
    put(BRTAIL, [0x18, 0xB1, IP, 0x65, IP, 0x85, IP,
                 0x4C, NEXT & 0xFF, NEXT >> 8])
    put(SKIPTAIL, [0x18, 0xA5, IP, 0x69, 0x02, 0x85, IP,
                   0x4C, NEXT & 0xFF, NEXT >> 8])
    # LIT: reads through IP and steps it, then NEXT
    word(LIT, LIT + 2)
    put(LIT + 2, [0xB1, IP, 0x48, 0xE6, IP, 0xD0, 0x02, 0xE6, IP + 1,
                  0x4C, NEXT & 0xFF, NEXT >> 8])
    # EXIT: pulls the saved thread pointer back
    word(EXIT, EXIT + 2)
    put(EXIT + 2, [0x68, 0x85, IP, 0x68, 0x85, IP + 1,
                   0x4C, NEXT & 0xFF, NEXT >> 8])
    # BRANCH: jumps straight to the tail that adds the inline cell
    word(BRANCH, BRANCH + 2)
    put(BRANCH + 2, [0x4C, BRTAIL & 0xFF, BRTAIL >> 8])
    # an ordinary primitive, which must NOT be read as taking a cell
    word(ADD, ADD + 2)
    put(ADD + 2, [0xB5, 0x00, 0x75, 0x02, 0x95, 0x02, 0xE8, 0xE8,
                  0x4C, NEXT & 0xFF, NEXT >> 8])

    # : DEF1  LIT 1234  ADD  DEF2  BRANCH <8>  EXIT ;
    word(DEF1, DOCOL)
    for i, cell in enumerate([LIT, 0x1234, ADD, DEF2, BRANCH, 0x0008, EXIT]):
        word(DEF1 + 2 + i * 2, cell)
    # : DEF2  ADD  EXIT ;
    word(DEF2, DOCOL)
    for i, cell in enumerate([ADD, EXIT]):
        word(DEF2 + 2 + i * 2, cell)

    class FakeCart(object):
        def __init__(self, blob):
            self.rom = bytes(blob)
            self.info = {"title": "synthetic forth"}

        def spaces(self):
            return ["rom"]

        def base_of(self, _s):
            return BASE

        def size_of(self, _s):
            return SIZE

        def byte(self, _s, addr):
            i = addr - BASE
            if not 0 <= i < SIZE:
                raise IndexError("outside the image")
            return self.rom[i]

        def slice(self, _s, addr, n):
            i = addr - BASE
            if i < 0 or i + n > SIZE:
                raise IndexError("outside the image")
            return self.rom[i:i + n]

    cart = FakeCart(img)
    im = forth.Image(cart, "rom").discover()

    for name, want, got in (("NEXT", NEXT, im.next), ("DOCOL", DOCOL, im.docol),
                            ("EXIT", EXIT, im.exit), ("literal", LIT, im.lit)):
        if got != want:
            raise AssertionError("%s found at %s, expected $%04X"
                                 % (name, ("$%04X" % got) if got else "nothing",
                                    want))
    if im.ip_pointer() != IP:
        raise AssertionError("thread pointer read as $%02X, expected $%02X"
                             % (im.ip_pointer() or 0, IP))

    # the branch must be seen to eat a cell, and the ordinary word must not
    if not im.is_branch(BRANCH):
        raise AssertionError("the branch was not recognised as taking an "
                             "inline cell, so its destination decompiles as a "
                             "call")
    if im.is_branch(ADD):
        raise AssertionError("an ordinary primitive was read as taking an "
                             "inline cell, which swallows the word after it")

    cells = im.body(DEF1)
    kinds = [(c, k) for _a, c, k in cells]
    want = [(LIT, "word"), (0x1234, "data"), (ADD, "word"), (DEF2, "word"),
            (BRANCH, "word"), (0x0008, "data"), (EXIT, "word")]
    if kinds != want:
        raise AssertionError("the definition decompiled as %r, expected %r"
                             % (kinds, want))

    defs = im.definitions()
    if DEF1 not in defs or DEF2 not in defs:
        raise AssertionError("found definitions %s, expected both $%04X and "
                             "$%04X" % (["$%04X" % d for d in defs], DEF1, DEF2))
    callers = im.xref(defs)
    if callers.get(DEF2) != [DEF1]:
        raise AssertionError("cross-reference says $%04X is named by %s, "
                             "expected [$%04X]"
                             % (DEF2, callers.get(DEF2), DEF1))
    if im.kind_of(DEF1) != "colon" or im.kind_of(ADD) != "code":
        raise AssertionError("a definition and a primitive were not told apart")

    return ("interpreter located by shape, thread walked, literals and branch "
            "destinations kept out of the word stream")


def t_a8dis():
    """The 8-bit cartridge tracer, against a cartridge built to a known answer.

    Two bugs made this worth pinning down, and both were silent rather than
    loud. An implied-mode instruction has no operand, and the walk treated the
    missing operand as running off the end of the window -- so every trace
    stopped at the first INY and reported it as data. And the bank peephole
    (LDA #n / STA $D500) is what lets the tracer follow code into a paged bank
    at all; when it misses, the trace simply stays small and looks honest.

    So the fixture is a cartridge that requires both: it switches banks, then
    runs an implied instruction before touching anything. If either mechanism
    breaks, the reached-byte count collapses and the hardware list empties.

    It also checks that a register is named for what the instruction does to
    it. $D010 written is GRAFP; $D010 read is TRIG0. Reporting a button poll
    as a write to player graphics is the kind of wrong that sends someone
    reading the wrong routine for an afternoon.
    """
    import a8dis

    BANK = a8dis.BANK_SIZE
    rom = bytearray(BANK * 16)

    # bank 3, at $8000 once selected
    prog = bytes([
        0xC8,                    # INY          -- implied; used to end the walk
        0xAD, 0x00, 0xD2,        # LDA $D200    -- POKEY AUDF1 read
        0xAD, 0x10, 0xD0,        # LDA $D010    -- TRIG0 (a read)
        0x8D, 0x10, 0xD0,        # STA $D010    -- GRAFP (a write)
        0x60,                    # RTS
    ])
    rom[3 * BANK:3 * BANK + len(prog)] = prog

    # bank 15 is fixed at $A000-$BFFF; the start vector lands at $B000
    boot = bytes([
        0xA9, 0x03,              # LDA #$03
        0x8D, 0x00, 0xD5,        # STA $D500    -- select bank 3
        0x4C, 0x00, 0x80,        # JMP $8000
    ])
    f = 15 * BANK
    rom[f + 0x1000:f + 0x1000 + len(boot)] = boot
    rom[f + 0x1FE0] = 0x60                       # init: RTS
    rom[f + 0x1FFA:f + 0x2000] = bytes([0x00, 0xB0, 0x00, 0x04, 0xE0, 0xBF])

    cart = a8dis.Cart(b"CART" + b"\x00\x00\x00\x0E" + b"\x00" * 8 + bytes(rom))
    if cart.banks != 16 or cart.fixed != 15:
        raise AssertionError("bank geometry read wrong: %d banks, fixed %d"
                             % (cart.banks, cart.fixed))
    v = cart.vectors()
    if v["start"] != 0xB000 or v["init"] != 0xBFE0:
        raise AssertionError("cartridge footer misread: start $%04X init $%04X"
                             % (v["start"], v["init"]))

    t = a8dis.walk(cart, [(0, v["start"], "start"), (0, v["init"], "init")])

    if not any(s["to"] == 3 for s in t.switches):
        raise AssertionError(
            "the bank switch was not read back; without it the tracer cannot "
            "follow code into a paged bank at all")
    # every instruction of the fixture, at the address it was placed at. The
    # bank-3 byte count would also cover the JMP that follows the switch, so
    # the addresses are checked rather than the total.
    want = [0x8000, 0x8001, 0x8004, 0x8007, 0x800A]
    missing = [a for a in want if (3, a) not in t.code]
    if missing:
        raise AssertionError(
            "never reached %s -- the walk stopped early, which is what the "
            "implied-operand bug looked like"
            % " ".join("$%04X" % a for a in missing))
    if t.bailed:
        raise AssertionError("the walk bailed on %s, and every instruction in "
                             "the fixture is legal" % (t.bailed,))

    names = {(h["op"], a8dis.reg_name(h["name"], h["write"])) for h in t.hw}
    if ("LDA", "TRIG0") not in names:
        raise AssertionError("a read of $D010 was not named TRIG0: %s" % names)
    if ("STA", "GRAFM/GRAFP") not in names:
        raise AssertionError("a write to $D010 was not named GRAFP: %s" % names)

    return ("bank switch followed, implied instructions do not end the walk, "
            "%d hardware accesses named by direction" % len(t.hw))


def t_patchset():
    """The patch-set format, against a cartridge invented for the purpose.

    Four things have to hold, and each is a way the format could look fine and
    be wrong:

    **A float finds real free space and everything that calls it learns where.**
    The whole reason floats exist is that a hardcoded address goes stale. This
    checks the blob lands inside the declared free run, that the byte after the
    run is untouched, and that the JSR operand which held a placeholder now
    holds the address actually chosen.

    **Two settings of one knob are refused.** Not applied in file order, which
    is what a plain stack of BPS files does, and which produces a ROM that is
    neither.

    **A header is carried through untouched.** The same body ships headered and
    bare; a bundle that only works on one of them is half a bundle.

    **Applying in two goes equals applying in one.** This is the claim that
    makes per-section checksums worth having: a ROM already patched elsewhere
    is still a valid target for an option whose own bytes nobody has touched.
    If those two paths ever diverge, the sections are not independent and the
    format is lying.
    """
    import json
    import zipfile
    import bps
    import patchset

    # A cartridge: code at 0, a JSR with a placeholder operand, a run of free
    # space, and a byte just past it that must survive.
    rom = bytearray(0x400)
    rom[0x00:0x08] = b"\xA9\x01\x20\xFF\xFF\x60\xEA\xEA"   # JSR $FFFF
    rom[0x10:0x18] = bytes(range(0x10, 0x18))
    rom[0x20:0x28] = b"\x08\x08\x08\x08\x08\x08\x08\x08"
    rom[0x340] = 0x99                                       # just past the free run
    body = bytes(rom)

    def sec(at, n):
        return {"addr": "0x%04X" % at, "length": n,
                "crc32": "0x%08X" % patchset.crc32(body[at:at + n])}

    sections = {"s_0000": sec(0, 8), "s_0010": sec(0x10, 8),
                "s_0020": sec(0x20, 8)}

    def patch(at, n, edit):
        before = body[at:at + n]
        after = bytearray(before)
        edit(after)
        return bps.create(bytes(before), bytes(after))

    files = {
        "p/loud.s_0010.bps": patch(0x10, 8, lambda b: b.__setitem__(0, 0xAA)),
        "p/quiet.s_0010.bps": patch(0x10, 8, lambda b: b.__setitem__(0, 0xBB)),
        "p/routine.s_0020.bps": patch(0x20, 8, lambda b: b.__setitem__(7, 0x60)),
        "f/helper.bin": b"\xEE" * 12,
    }
    manifest = {
        "format": patchset.FORMAT,
        "name": "test",
        "target": {"body_size": len(body), "headers": [0, 16], "base": "0x0000",
                   "body_sha256": hashlib.sha256(body).hexdigest(),
                   "anchors": [{"addr": "0x0030", "length": 16,
                                "crc32": "0x%08X"
                                         % patchset.crc32(body[0x30:0x40])}]},
        "knobs": {"volume": "how loud"},
        "sections": sections,
        "options": [
            {"id": "loud", "knob": "volume", "title": "loud",
             "patches": {"s_0010": "p/loud.s_0010.bps"}},
            {"id": "quiet", "knob": "volume", "title": "quiet",
             "patches": {"s_0010": "p/quiet.s_0010.bps"}},
            {"id": "routine", "title": "a routine that needs somewhere to live",
             "patches": {"s_0020": {"bps": "p/routine.s_0020.bps"}},
             "floats": [{"id": "helper", "length": 12, "fill": 0, "from": "end",
                         "blob": "f/helper.bin",
                         "search": [{"addr": "0x0300", "length": 0x40}],
                         "fixups": [{"addr": "0x0003", "encode": "abs16",
                                     "expect": "0xFFFF"}]}]},
        ],
    }
    out = os.path.join(tempfile.mkdtemp(prefix="patchset-"), "t.patchset")
    patchset.write_bundle(out, manifest, files)
    ps = patchset.PatchSet(out)

    # 1. the float finds room, and the caller learns where
    got = ps.apply(body, ["routine"])
    where = got[3] | (got[4] << 8)
    if not (0x300 <= where <= 0x340 - 12):
        raise AssertionError("the float landed at $%04X, outside the free run "
                             "it was told to search" % where)
    if got[where:where + 12] != b"\xEE" * 12:
        raise AssertionError("the float's bytes are not at the address the "
                             "fixup was given")
    if got[0x340] != 0x99:
        raise AssertionError("the float overran the free range")
    if got[0x27] != 0x60:
        raise AssertionError("the section patch did not apply")

    # 2. two settings of one knob is a choice, not an order of application
    try:
        ps.apply(body, ["loud", "quiet"])
    except patchset.PatchSetError as e:
        if "alternative" not in str(e):
            raise AssertionError("refused for the wrong reason: %s" % e)
    else:
        raise AssertionError(
            "two settings of one knob were applied one after the other, which "
            "produces a ROM that is neither")

    # 3. a header rides along untouched
    headered = b"\x7F" * 16 + body
    out2 = ps.apply(headered, ["loud"])
    if out2[:16] != b"\x7F" * 16 or len(out2) != len(headered):
        raise AssertionError("the header was not carried through intact")
    if out2[16 + 0x10] != 0xAA:
        raise AssertionError("the body was located at the wrong offset")

    # 4. one go and two goes agree
    both = ps.apply(body, ["loud", "routine"])
    step = ps.apply(ps.apply(body, ["routine"]), ["loud"])
    if both != step:
        raise AssertionError(
            "applying two options together and one after the other gave "
            "different ROMs, so the sections are not independent")

    # 5. an option already on the cartridge is recognised, not refused
    once = ps.apply(body, ["loud"])
    if ps.option_state(bytearray(once), "loud") != "applied":
        raise AssertionError(
            "a cartridge that plainly has this option was not recognised as "
            "having it, so applying twice would look like damage")
    twice = ps.apply(once, ["loud"])
    if twice != once:
        raise AssertionError("applying an option twice changed the ROM the "
                             "second time; it is not idempotent")

    # 6. and the anchors identify the game whether or not it is pristine
    if not ps.pristine and ps.find_body(once)[1] is None:
        raise AssertionError("unreachable")
    ps.find_body(once)
    if ps.pristine:
        raise AssertionError("a patched ROM was reported as the pristine dump")
    wrong = bytearray(body)
    wrong[0x30] ^= 0xFF                      # break an anchor
    try:
        ps.find_body(bytes(wrong))
    except patchset.PatchSetError as e:
        if "anchor" not in str(e):
            raise AssertionError("refused for the wrong reason: %s" % e)
    else:
        raise AssertionError(
            "a cartridge failing an anchor was accepted; anchors are what "
            "identify the target once a whole-file hash cannot")

    # 7. a dependency nobody declared, read out of the patches themselves
    #
    # "louder" is authored on top of "loud": its patch starts from the bytes
    # loud leaves behind. Nothing says so in the manifest -- the BPS headers
    # say it, because one patch's source CRC is the other's target CRC.
    loud_out = bytearray(body[0x10:0x18])
    loud_out[0] = 0xAA
    louder = bytearray(loud_out)
    louder[1] = 0xCC
    files2 = dict(files)
    files2["p/louder.s_0010.bps"] = bps.create(bytes(loud_out), bytes(louder))
    orphan = bytearray(body[0x20:0x28])
    orphan[0] = 0x77                      # a state nothing in the bundle makes
    files2["p/orphan.s_0020.bps"] = bps.create(
        bytes(orphan), bytes(bytearray(b"" * 8)))
    m2 = json.loads(json.dumps(manifest))
    m2["options"].append({"id": "louder", "title": "louder still",
                          "patches": {"s_0010": "p/louder.s_0010.bps"}})
    m2["options"].append({"id": "orphan", "title": "built on nothing here",
                          "patches": {"s_0020": "p/orphan.s_0020.bps"}})
    out2 = os.path.join(tempfile.mkdtemp(prefix="patchset-"), "t2.patchset")
    patchset.write_bundle(out2, m2, files2)
    ps2 = patchset.PatchSet(out2)

    derived, unknown = ps2.derived_requires()
    if derived.get("louder") != {"loud"}:
        raise AssertionError(
            "the dependency of louder on loud is written in the two patches' "
            "CRCs and was not found: %r" % (derived.get("louder"),))
    if derived.get("loud"):
        raise AssertionError("loud starts from the pristine bytes and should "
                             "need nothing: %r" % (derived["loud"],))
    if "orphan" not in unknown:
        raise AssertionError(
            "a patch starting from a state nothing in the bundle produces was "
            "not flagged; it can never apply and should say so")

    # asking for louder alone brings loud along, in the right order
    chained = ps2.apply(body, ["louder"])
    if chained[0x10] != 0xAA or chained[0x11] != 0xCC:
        raise AssertionError(
            "louder was applied without loud beneath it, so the derived "
            "dependency was found and then ignored")
    try:
        ps2.apply(body, ["orphan"])
    except patchset.PatchSetError as e:
        if "bundle does not describe" not in str(e):
            raise AssertionError("refused for the wrong reason: %s" % e)
    else:
        raise AssertionError("an option built on an unknown state applied")

    # 8. one patch across several sections
    #
    # Six two-byte edits scattered over a ROM are one change, not six. A span
    # is the sections concatenated in address order, so the BPS is one delta
    # with one pair of checksums -- and the pre-image of a span cannot be
    # worked out from the sections' own CRCs, so it has to be stated.
    span_before = body[0x00:0x08] + body[0x10:0x18]
    span_after = bytearray(span_before)
    span_after[1] = 0x42
    span_after[9] = 0x43
    files3 = dict(files)
    files3["p/wide.bps"] = bps.create(bytes(span_before), bytes(span_after))
    m3 = json.loads(json.dumps(manifest))
    m3["options"] = [o for o in m3["options"] if o["id"] == "loud"]
    m3["options"].append({
        "id": "wide", "title": "one patch, two sections",
        "patches": [{"sections": ["s_0010", "s_0000"], "bps": "p/wide.bps",
                     "before": "0x%08X" % patchset.crc32(bytes(span_before))}]})
    out3 = os.path.join(tempfile.mkdtemp(prefix="patchset-"), "t3.abp")
    patchset.write_bundle(out3, m3,
                          {k: v for k, v in files3.items()
                           if k in ("p/loud.s_0010.bps", "p/wide.bps")})
    ps3 = patchset.PatchSet(out3)
    wide = ps3.apply(body, ["wide"])
    if wide[0x01] != 0x42 or wide[0x11] != 0x43:
        raise AssertionError(
            "a patch spanning two sections did not reach both of them")
    if wide[0x08:0x10] != body[0x08:0x10]:
        raise AssertionError(
            "the span wrote over the gap between its two sections; the pieces "
            "are concatenated for the delta, not treated as one extent")
    if ps3.option_state(bytearray(wide), "wide") != "applied":
        raise AssertionError("a span was not recognised as already applied")

    m4 = json.loads(json.dumps(m3))
    del m4["options"][-1]["patches"][0]["before"]
    out4 = os.path.join(tempfile.mkdtemp(prefix="patchset-"), "t4.abp")
    patchset.write_bundle(out4, m4,
                          {k: v for k, v in files3.items()
                           if k in ("p/loud.s_0010.bps", "p/wide.bps")})
    try:
        patchset.PatchSet(out4).apply(body, ["wide"])
    except patchset.PatchSetError as e:
        if "before" not in str(e):
            raise AssertionError("refused for the wrong reason: %s" % e)
    else:
        raise AssertionError(
            "a multi-section span with no stated pre-image was accepted, so "
            "nothing could tell whether it had been applied")

    # 9. two options that differ only inside a float stay distinguishable
    #
    # Sections cover the code that calls a float; nothing covers the float. Two
    # knockback strengths have byte-identical call sites and blobs differing in
    # one operand, so on sections alone each reports "already applied" about a
    # cartridge carrying the other. The fixup wrote the address down, so the
    # blob can be checked where it actually landed.
    m5 = json.loads(json.dumps(manifest))
    m5["options"] = [o for o in m5["options"] if o["id"] == "routine"]
    m5["knobs"]["strength"] = "how hard"
    files5 = {"p/routine.s_0020.bps": files["p/routine.s_0020.bps"],
              "f/helper.bin": bytes([0xEE]) * 12,
              "f/helper2.bin": bytes([0xEF]) * 12}
    m5["options"][0]["knob"] = "strength"
    m5["options"][0]["floats"][0]["crc32"] = ("0x%08X"
        % patchset.crc32(files5["f/helper.bin"]))
    other = json.loads(json.dumps(m5["options"][0]))
    other["id"] = "routine2"
    other["floats"][0]["blob"] = "f/helper2.bin"
    other["floats"][0]["crc32"] = ("0x%08X"
        % patchset.crc32(files5["f/helper2.bin"]))
    m5["options"].append(other)
    out5 = os.path.join(tempfile.mkdtemp(prefix="patchset-"), "t5.abp")
    patchset.write_bundle(out5, m5, files5)
    ps5 = patchset.PatchSet(out5)
    done = bytearray(ps5.apply(body, ["routine"]))
    if ps5.option_state(done, "routine") != "applied":
        raise AssertionError("an option was not recognised as applied")
    if ps5.option_state(done, "routine2") == "applied":
        raise AssertionError(
            "an option whose float is not the one on the cartridge reported "
            "itself already applied; applying it would look like a no-op and "
            "silently leave the other one in place")

    return ("floats placed in real free space with their callers fixed up, "
            "alternatives refused, headers preserved, incremental "
            "application converges, already-applied options recognised, and "
            "anchors identify the target, and a dependency written only in "
            "two patches' checksums is found and honoured, and one patch "
            "can span several sections, and two options differing only "
            "inside a float stay apart")

def t_patchset_grow():
    """A bundle that grows the cartridge (patchset/3), against an invented one.

    A 2K body at $F800 grows at the front to 4K at $F000 -- the shape of a
    linear 7800 cartridge going from 32K to 48K -- with one section in the old
    space and one in the new, patched by a single span. Six things have to
    hold:

    **The body grows where the option says, and the old bytes move with it.**
    **The .a78 header's ROM size follows the body** -- an emulator believes
    the header, and maps a grown body under a stale size wrong.
    **A grown cartridge is still recognised**: check calls the option
    applied, and applying again changes nothing.
    **Headered and bare give the same body.**
    **An option that does not grow leaves the size alone**, and an ungrown
    dump still reads as a target for the one that does.
    **A header that puts something at $4000 refuses to grow**, and a /2
    manifest that grows is refused as a format error rather than misread.
    """
    import json
    import bps
    import patchset

    old = bytes(((i * 7 + 3) & 0xFF) for i in range(0x800))
    fill = bytes([0xFF])
    grown_pre = fill * 0x800 + old
    base0, base1 = 0xF800, 0xF000

    def sec(addr, n):
        at = addr - base1
        return {"addr": "0x%04X" % addr, "length": n,
                "crc32": "0x%08X" % patchset.crc32(grown_pre[at:at + n])}

    sections = {"s_F100": sec(0xF100, 8), "s_F810": sec(0xF810, 4),
                "s_F900": sec(0xF900, 2)}
    before = grown_pre[0x100:0x108] + grown_pre[0x810:0x814]
    jsr = bytes([0x20, 0x00, 0xF1, 0xEA])                # a JSR into the new space
    after = bytes(range(0x40, 0x48)) + jsr
    nops = bytes([0xEA, 0xEA])
    files = {"p/big.bps": bps.create(before, after),
             "p/small.bps": bps.create(grown_pre[0x900:0x902], nops)}
    manifest = {
        "format": patchset.FORMAT,              # write_bundle must raise it to /3
        "name": "grow test",
        "target": {"body_size": 0x800, "headers": [0, 128], "base": "0x%04X" % base0,
                   "anchors": [{"addr": "0xFC00", "length": 64,
                                "crc32": "0x%08X" % patchset.crc32(old[0x400:0x440])}]},
        "sections": sections,
        "options": [
            {"id": "big", "title": "needs the new space",
             "grow": {"size": 0x1000, "at": "front", "fill": "0xFF"},
             "patches": [{"sections": ["s_F100", "s_F810"], "bps": "p/big.bps",
                          "before": "0x%08X" % patchset.crc32(before)}]},
            {"id": "small", "title": "fits as it is",
             "patches": {"s_F900": "p/small.bps"}},
        ],
    }
    out = os.path.join(tempfile.mkdtemp(prefix="patchset-"), "g.abp")
    patchset.write_bundle(out, manifest, files)
    ps = patchset.PatchSet(out)
    if ps.m["format"] != patchset.FORMAT_GROW:
        raise AssertionError("a growing bundle was written as %r" % ps.m["format"])

    def a78(size, ctype=0):
        hd = bytearray(128)
        hd[0] = 4
        hd[1:10] = b"ATARI7800"
        hd[49:53] = size.to_bytes(4, "big")
        hd[53:55] = ctype.to_bytes(2, "big")
        hd[100:128] = b"ACTUAL CART DATA STARTS HERE"
        return bytes(hd)

    # 1-2. grows at the front, the header follows, the new space is patched,
    #      the old bytes move up
    got = patchset.PatchSet(out).apply(a78(0x800) + old, ["big"])
    if len(got) != 128 + 0x1000:
        raise AssertionError("grown image is %d bytes, want %d"
                             % (len(got), 128 + 0x1000))
    if int.from_bytes(got[49:53], "big") != 0x1000:
        raise AssertionError("the header still says %d bytes"
                             % int.from_bytes(got[49:53], "big"))
    body = got[128:]
    if body[0x100:0x108] != bytes(range(0x40, 0x48)):
        raise AssertionError("the patch did not land in the new space")
    if body[0x000:0x100] != fill * 0x100 or body[0x108:0x800] != fill * 0x6F8:
        raise AssertionError("the rest of the new space is not the fill")
    if body[0xC00:0xC40] != old[0x400:0x440] or body[0x810:0x814] != jsr:
        raise AssertionError("the old bytes did not move up with the growth")

    # 3. recognised when grown, and idempotent
    ps3 = patchset.PatchSet(out)
    _hdr3, body3 = ps3.find_body(got)
    if ps3.base != base1 or ps3.survey(body3).get("big") != "applied":
        raise AssertionError("a grown cartridge is not recognised as "
                             "carrying the option")
    if patchset.PatchSet(out).apply(got, ["big"]) != got:
        raise AssertionError("applying twice changed the cartridge")

    # 4. headered and bare give the same body
    if patchset.PatchSet(out).apply(old, ["big"]) != body:
        raise AssertionError("a headerless dump grows to a different body")

    # 5. an option that does not grow leaves the size alone, and an ungrown
    #    dump still reads as a target for the one that does
    small = patchset.PatchSet(out).apply(a78(0x800) + old, ["small"])
    if len(small) != 128 + 0x800 or small[128 + 0x100:128 + 0x102] != nops:
        raise AssertionError("a non-growing option changed the size or "
                             "missed its bytes")
    ps5 = patchset.PatchSet(out)
    _h5, b5 = ps5.find_body(a78(0x800) + old)
    if ps5.survey(ps5.grown_view(b5)).get("big") != "applies":
        raise AssertionError("an ungrown dump does not read as a target "
                             "for growth")

    # 6. a header with something at $4000 refuses; a /2 manifest that grows
    #    is refused by name
    try:
        patchset.PatchSet(out).apply(a78(0x800, 0x0004) + old, ["big"])
        raise AssertionError("grew a cartridge whose header puts RAM at $4000")
    except patchset.PatchSetError as e:
        if "$4000" not in str(e):
            raise AssertionError("refused for the wrong reason: %s" % e)
    d = tempfile.mkdtemp(prefix="patchset-")
    with open(os.path.join(d, "patchset.json"), "w") as f:
        f.write(json.dumps(dict(manifest, format=patchset.FORMAT)))
    try:
        patchset.PatchSet(d)
        raise AssertionError("a /2 manifest that grows was accepted")
    except patchset.PatchSetError as e:
        if patchset.FORMAT_GROW not in str(e):
            raise AssertionError("refused for the wrong reason: %s" % e)
    return ("grows at the front with the header's size following, the new "
            "space patched and the old moved up, recognised and idempotent "
            "once grown, headered and bare alike, refused under a header "
            "with something at $4000 and as a /2 manifest")


def t_portkit_refuses_payload():
    """A conversion recipe must carry coordinates, never content.

    `portkit.py` exists because a BPS patch cannot express a build that draws
    on two sources: the delta from a 7800 cartridge to a conversion using Atari
    8-bit artwork would contain all of that artwork, so the "patch" would be a
    redistribution wearing a diff's clothes. A recipe avoids that by holding
    only hashes, offsets and lengths -- and that only holds while nobody
    embeds "just one table" inline.

    So the rule is enforced in code, and this checks the enforcement works in
    both directions: it fires on embedded data, and it does not fire on an
    ordinary recipe. A guard that cannot be shown to trip is decoration.
    """
    import base64
    import json
    import portkit

    good = {
        "name": "test",
        "sources": {"disk": {"what": "a disk", "sha256": "00" * 32}},
        "regions": {"art": {"from": "disk", "sector": 10, "sectors": 2,
                            "sha256": "11" * 32, "what": "some artwork"}},
        "new": ["src/main.s"],
    }
    path = os.path.join(tempfile.gettempdir(), "portkit-good.json")
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps(good))
    portkit.load_recipe(path)          # must not raise

    for key in ("data", "bytes", "payload", "base64", "hex"):
        bad = json.loads(json.dumps(good))
        bad["regions"]["art"][key] = base64.b64encode(b"\xAA" * 400).decode()
        p2 = os.path.join(tempfile.gettempdir(), "portkit-bad.json")
        with io.open(p2, "w", encoding="utf-8") as f:
            f.write(json.dumps(bad))
        try:
            portkit.load_recipe(p2)
        except portkit.RecipeError:
            continue
        raise AssertionError(
            "a recipe carrying %d bytes under %r was accepted; the rule that "
            "makes this safe to publish is not being enforced"
            % (400, key))

    # a long prose note is not payload, and must still be allowed
    wordy = json.loads(json.dumps(good))
    wordy["note"] = "why this exists. " * 40
    p3 = os.path.join(tempfile.gettempdir(), "portkit-wordy.json")
    with io.open(p3, "w", encoding="utf-8") as f:
        f.write(json.dumps(wordy))
    portkit.load_recipe(p3)

    return ("a recipe carrying embedded data is refused under every name "
            "tried, and ordinary recipes still load")


def t_dist_carries_no_rom():
    """The published patches must not smuggle the cartridge out with them.

    `dist/` is the one directory in this repository that holds build output,
    and it holds it because both formats there are meant to travel without
    the game. That is a claim about bytes, so it is checked rather than
    believed -- the same standard `recipes carry no payload` holds
    `portkit.py` to.

    For a BPS the question is what its literals are. The encoder emits a
    literal only for a run that differs from the source, so in principle
    every stored byte is the patch author's; this confirms it by decoding
    each patch and comparing every literal against the original at the same
    address. One match would mean a byte of the game riding along.

    For the patch set the question is different, because it stores whole
    blobs. Its sections must describe their pre-image with a CRC32 and never
    quote it, and its float blobs -- code with no fixed home -- must be
    authored rather than lifted, so none of them may appear anywhere in the
    cartridge.

    Skips without a dump, like the other cartridge-dependent checks: with no
    original to compare against there is nothing to be sure of.
    """
    import json
    import zipfile

    root = os.path.dirname(HERE)
    dist = os.path.join(root, "dist")
    if not os.path.isdir(dist):
        return None

    import importlib.util
    kp = os.path.join(root, "patches", "karateka.py")
    if not os.path.exists(kp):
        return None
    spec = importlib.util.spec_from_file_location("karateka_dist", kp)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    try:
        _src, _hdr, orig = mod.load_source()
    except SystemExit:
        return None

    sys.path.insert(0, HERE)
    import bps as bpsmod

    literals = leaked = 0
    for name in sorted(os.listdir(dist)):
        if not name.endswith(".bps"):
            continue
        patch = io.open(os.path.join(dist, name), "rb").read()
        h = bpsmod.read_header(patch)
        i, pos = h["actions_at"], 0
        while i < h["body_end"]:
            v, i = bpsmod.decode_number(patch, i)
            act, ln = v & 3, (v >> 2) + 1
            if act == bpsmod.SOURCE_READ:
                pos += ln
            elif act == bpsmod.TARGET_READ:
                for k in range(ln):
                    literals += 1
                    if pos + k < len(orig) and orig[pos + k] == patch[i + k]:
                        leaked += 1
                i += ln
                pos += ln
            else:
                _o, i = bpsmod.decode_number(patch, i)
                pos += ln
    if leaked:
        raise AssertionError(
            "%d of %d literal bytes in dist/*.bps are the original "
            "cartridge's own; these patches are not safe to publish"
            % (leaked, literals))

    # every bundle, not just the NTSC one: the structural guarantee holds
    # regardless of which cartridge a bundle targets, and a PAL bundle
    # would otherwise go unchecked
    for abp in sorted(f for f in os.listdir(dist) if f.endswith(".abp")):
        abp = os.path.join(dist, abp)
        z = zipfile.ZipFile(abp)
        man = json.loads(z.read("patchset.json"))
        rows = man["sections"]
        rows = rows if isinstance(rows, list) else list(rows.values())
        for r in rows:
            for k, v in r.items():
                if k != "crc32" and isinstance(v, str) and len(v) >= 8 \
                        and all(c in "0123456789abcdefABCDEF" for c in v):
                    raise AssertionError(
                        "section %r stores what looks like byte data in %r; "
                        "sections must carry a CRC32 of the pre-image, not "
                        "the pre-image" % (r.get("what", "?"), k))
        for n in z.namelist():
            if n.startswith("f/") and z.read(n) in orig:
                raise AssertionError(
                    "float blob %s appears verbatim in the cartridge, so it "
                    "is lifted rather than authored" % n)

    return "%d literal bytes across dist/, none of them the cartridge's" % literals


def t_karateka_fixes():
    """The Karateka fixes: no two disagree unless they are the same knob.

    These are meant to be mixed -- cadence, hit window and control mapping are
    three different things and a build can have all three. But "different
    things" is a claim about bytes, and one of these fixes was already
    withdrawn for being wrong about exactly that. So the claim is checked:
    every fix's writes are collected, every pair compared, and a pair that
    disagrees about a byte without being two settings of one knob is a
    failure.

    It also checks each composite really contains the parts it advertises,
    because a composite that quietly lost one would still look self-consistent
    from the inside.

    And it pins the dose rule: doses 2 and 4 must come out byte-identical to
    fixes 4 and 3, which are the two that were measured on real recorded
    sessions. If the even-spacing rule ever stops reproducing them, the nine
    dials are no longer the same instrument those numbers came from.
    """
    import importlib.util

    path = os.path.join(os.path.dirname(HERE), "patches", "karateka.py")
    if not os.path.exists(path):
        return None
    spec = importlib.util.spec_from_file_location("karateka_patches", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    # ask the module where the cartridge is rather than re-deriving it:
    # it searches as well as listing candidates, and a copy of that logic
    # here would drift and quietly turn this test into a skip
    try:
        mod.load_source()
    except SystemExit:
        return None

    # check() reports at length; the test wants the verdict, not the report
    buf, saved = io.StringIO(), sys.stdout
    try:
        sys.stdout = buf
        rc = mod.check()
    finally:
        sys.stdout = saved
    if rc != 0:
        raise AssertionError(
            "the fixes conflict; run patches/karateka.py --check for "
            "which:\n%s" % "\n".join(
                l for l in buf.getvalue().splitlines() if "CONFLICT" in l))

    _src, _hdr, rom = mod.load_source()
    for dose, n in ((4, 3), (2, 4)):
        a = mod.Patcher(rom)
        mod.fix_input_latch(a)
        mod._skip_waits(a, mod.dose_indices(dose))
        b = mod.Patcher(rom)
        [f for f in mod.FIXES if f["n"] == n][0]["apply"](b)
        if bytes(a.rom) != bytes(b.rom):
            raise AssertionError(
                "dose %d no longer matches fix %d, so the measured frame "
                "counts do not describe the doses" % (dose, n))

    live = [f for f in mod.FIXES if not f.get("withdrawn")]
    return ("%d live fixes, no conflicting pair, every composite contains its "
            "parts, doses 2 and 4 still equal fixes 4 and 3" % len(live))


def main():
    ap = argparse.ArgumentParser(
        description=__doc__.strip().split("\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rom", help="a cartridge, to run the round trips")
    ap.add_argument("--format", help="a player-format file to go with --rom")
    ap.add_argument("--log", help="an audio.lua capture of --rom")
    ap.add_argument("--song", type=int, default=0,
                    help="which song --log recorded (default 0)")
    ap.add_argument("--region", default="ntsc", choices=["ntsc", "pal"])
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    r = Results(args.verbose)
    print("without a cartridge")
    r.check("shipped json", t_json)
    r.check("format files", t_formats)
    r.check("display lists", t_dlwalk)
    r.check("POKEY random", t_sim_random)
    r.check("sim compare", t_sim_compare)
    r.check("sprite import", t_mksprite)
    r.check("DMA cost model", t_dmabudget)
    r.check("game scaffold", t_newgame)
    r.check("gap checker", t_check_gaps)
    r.check("6502 cycle table", t_cycles)
    r.check("example songs", t_examples)
    r.check("note tables", t_notes)
    r.check("refusals", t_refusals)
    r.check("two POKEYs", t_dualpokey)
    r.check("tracker UI", t_trackeredit)
    r.check("midi import", t_midi)
    r.check("format guesser", t_explore)
    r.check("browser page scripts", t_browser_js)
    r.check("click handlers", t_handler_attributes)
    r.check("saved readings", t_direct_format)
    r.check("engine finder", t_engine_finder)
    r.check("forth decompiler", t_forth_decompiler)
    r.check("8-bit cartridge tracer", t_a8dis)
    r.check("karateka fixes agree", t_karateka_fixes)
    r.check("patch sets", t_patchset)
    r.check("patch sets that grow", t_patchset_grow)
    r.check("recipes carry no payload", t_portkit_refuses_payload)
    r.check("published patches carry no ROM", t_dist_carries_no_rom)
    r.check("tool --help", t_helps)
    r.check("README tool list", t_readme)
    r.check("doc links", t_links)

    print("")
    print("with a cartridge")
    r.check("songfmt no-op push", lambda: t_pull_push(args.rom, args.format))
    r.check("songfmt refuses growth", lambda: t_grow(args.rom, args.format))
    r.check("render vs hardware",
            lambda: t_verify(args.rom, args.format, args.log, args.song,
                             args.region))
    r.check("sprite edit round trip", lambda: t_sprites(args.rom))
    r.check("songs from the ROM",
            lambda: t_songs_from_rom(args.rom, args.format))
    r.check("workbench", lambda: t_workbench(args.rom))
    r.check("disassembler runs", lambda: t_disasm(args.rom))

    n_ok = sum(1 for x in r.rows if x[1] == PASS)
    n_skip = sum(1 for x in r.rows if x[1] == SKIP)
    print("")
    print("%d passed, %d failed, %d skipped" % (n_ok, len(r.failed), n_skip))
    if n_skip and not args.rom:
        print("Pass --rom, --format and --log to run the rest.")
    return 1 if r.failed else 0


if __name__ == "__main__":
    sys.exit(main())
