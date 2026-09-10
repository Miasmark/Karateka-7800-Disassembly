#!/usr/bin/env python3
"""
Pull a game's music out of its ROM, and push it back.

    python songfmt.py pull game.a78 -f formats/mm-tia.json -o songs.json
    python songfmt.py push game.a78 -f formats/mm-tia.json -i songs.json -o new.a78

No tool can guess a player's format, so this does not try: a format file says
what the bytes mean, and both directions fall out of that one description.
`audiotrace.py` finds the tables; the format file says how to read them.

## Why push is the hard half

Writing music back into a cartridge is not writing a file. The data sits in
whatever gaps the original author left, with other data hard against it, and
nothing announces where one ends and the next begins. Re-encoding a song a byte
longer than it was silently eats whatever followed.

So this is deliberately **in place and bounded**. Pull records the exact byte
span every element occupies. Push re-encodes into those same spans and refuses,
by name and by byte count, anything that no longer fits. After writing, it
compares the whole image against the original and fails if a single byte
outside the declared spans changed.

That is a narrow kind of editing -- you can change notes freely, but a pattern
cannot grow -- and the narrowness is the point. It is the difference between an
edit you can trust and one that appears to work until some unrelated thing
breaks.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cart as cart_module
from tracker import CHANNELS, pokey_unsupported, is_pokey


def _wrap(text, width):
    words, line, out = text.split(), "", []
    for w in words:
        if len(line) + len(w) + 1 > width:
            out.append(line)
            line = w
        else:
            line = (line + " " + w).strip()
    if line:
        out.append(line)
    return out


class FormatError(Exception):
    pass


def parse_loc(text):
    sp, addr = text.split(":")
    return sp, int(addr.lstrip("$"), 16)


class Reader:
    """ROM access that remembers every byte it touched.

    The record of spans is what makes push safe, so it is collected here rather
    than reconstructed later -- anything read is, by definition, something the
    format claims to own.
    """

    def __init__(self, cart):
        self.cart = cart
        self.spans = []             # (space, start, length, what)

    def bytes_at(self, space, addr, n, what):
        data = bytes(self.cart.slice(space, addr, n))
        self.spans.append([space, addr, n, what])
        return data

    def byte(self, space, addr, what):
        return self.bytes_at(space, addr, 1, what)[0]

    def word(self, space, addr, what):
        d = self.bytes_at(space, addr, 2, what)
        return d[0] | (d[1] << 8)


def space_for(cart, addr, bank):
    """Which space an address names, given the bank a song plays in."""
    sp = cart.space_of(addr, bank)
    if sp is None:
        raise FormatError(
            "$%04X is in the paged window and this song has no bank. Add it to "
            "\"banks\" in the format file -- a track pointer there means a "
            "different byte in every bank." % addr)
    return sp


def bits(value, lo, width):
    return (value >> lo) & ((1 << width) - 1)


def put_bits(value, lo, width, field):
    mask = ((1 << width) - 1) << lo
    return (value & ~mask & 0xFF) | ((field << lo) & mask)


def pull(cart, fmt):
    # Players do not share a shape. `reader` says which one this file
    # describes; the default is the nested song/track/pattern/note layout.
    if fmt.get("reader") == "parallel":
        return pull_parallel(cart, fmt)
    if fmt.get("reader") == "rmt":
        return pull_rmt(cart, fmt)
    if fmt.get("reader") == "direct":
        return pull_direct(cart, fmt)
    r = Reader(cart)
    songs_cfg = fmt["songs"]
    tsp, taddr = parse_loc(songs_cfg["table"])
    stride = songs_cfg["stride"]
    banks = {int(k): v for k, v in songs_cfg.get("banks", {}).items()}
    tk, pat_cfg, note_cfg = fmt["track"], fmt["pattern"], fmt["note"]

    durations = None
    if "durations" in fmt:
        dsp, daddr = parse_loc(fmt["durations"]["table"])
        durations = list(r.bytes_at(dsp, daddr, fmt["durations"]["count"],
                                    "duration table"))

    # The tables a note leans on come out with it. A song that carries its own
    # durations, waveforms and instruments can be rendered and edited without
    # the cartridge it came from -- which is the difference between extracting
    # data and extracting a song.
    waveforms = None
    if "waveforms" in fmt:
        wsp, waddr = parse_loc(fmt["waveforms"]["table"])
        waveforms = list(r.bytes_at(wsp, waddr, fmt["waveforms"]["count"],
                                    "waveform table"))
    instruments = None
    if "instruments" in fmt:
        ic = fmt["instruments"]
        isp, iaddr = parse_loc(ic["table"])
        instruments = [list(r.bytes_at(isp, iaddr + i * ic["stride"],
                                       ic["stride"], "instrument %d" % i))
                       for i in range(ic["count"])]

    # Patterns are reused -- a track repeats one, and different songs draw on
    # the same material. Midnight Mutants' 65 references resolve to 35 distinct
    # patterns. Holding each once is both a truer model of the ROM and the only
    # safe one: with a copy per reference, pushing writes the same bytes
    # repeatedly and the last unedited copy silently undoes the edit.
    out = {"format": fmt.get("name", "?"), "chip": fmt.get("chip", "tia"),
           "durations": durations, "waveforms": waveforms,
           "instruments": instruments,
           "engine": fmt.get("instruments", {}).get("engine"),
           "audc_from": fmt.get("audc_from"),
           "instrument_fields": fmt.get("instruments", {}).get("fields"),
           "patterns": {}, "songs": []}

    titles = songs_cfg.get("titles") or {}
    for n in range(songs_cfg["count"]):
        base = taddr + n * stride
        bank = banks.get(n)
        # What a song *is* -- the title theme, the death jingle -- cannot be
        # recovered from the bytes by any amount of analysis. Somebody has to
        # play the game and say. When they have, it belongs in the format file
        # beside everything else that was worked out, because the next person
        # otherwise starts from "song 7".
        song = {"n": n, "bank": bank, "at": "%s:%04X" % (tsp, base),
                "title": titles.get(str(n)), "voices": []}
        for vi, off in enumerate(songs_cfg["voice_ptr"]):
            ptr = r.word(tsp, base + off, "song %d voice %d pointer" % (n, vi))
            voice = {"pointer": "$%04X" % ptr, "order": [], "terminator": None}
            if ptr:
                try:
                    read_voice(r, cart, fmt, ptr, bank, voice, n, vi,
                               out["patterns"])
                except FormatError as e:
                    voice["error"] = str(e)
            song["voices"].append(voice)
        out["songs"].append(song)

    out["spans"] = r.spans
    return out


def find_rmt(cart):
    """Locate an RMT module in a cartridge, or None.

    Raster Music Tracker modules carry their own magic -- "RMT" then '4' or '8'
    for the channel count -- so they announce themselves and do not need a
    player fingerprint. That matters here: the same module format turns up
    behind several different player builds, so keying on the player would split
    one format across several descriptions and miss games that embed a module
    without a recognisable player at all.
    """
    for space in cart.spaces():
        base = cart.base_of(space)
        size = cart.size_of(space)
        try:
            blob = bytes(cart.slice(space, base, size))
        except Exception:                                    # noqa: BLE001
            continue
        i = blob.find(b"RMT")
        while i != -1:
            if i + 16 <= len(blob) and blob[i + 3] in (0x34, 0x38):
                return space, base, base + i, blob
            i = blob.find(b"RMT", i + 1)
    return None


def pull_rmt(cart, fmt):
    """Report an RMT module's structure. Deliberately not its notes.

    What comes out is everything the module says about itself and can be
    checked against itself: channel count, pattern length, speeds, where the
    instrument, track and song tables are, how long the song is and how many
    tracks it uses. Every pointer is validated against the module's own bounds,
    so a wrong reading shows up as an out-of-range address rather than as
    plausible rubbish.

    What does **not** come out is the notes, and that is a limit worth stating
    rather than papering over. RMT's track data is only half the story: the
    sound comes from its instrument engine -- per-instrument envelope tables for
    volume, distortion and pitch, plus effects -- and reproducing a tune means
    reimplementing that replayer, not parsing a note list. Decoding the notes
    without it would produce a song that looks right and plays wrong.

    So this identifies and describes; `tracker.py capture` is still how you hear
    one. See docs/audio.md.
    """
    got = find_rmt(cart)
    if not got:
        raise FormatError(
            "no RMT module in this cartridge. The magic is \"RMT\" followed by "
            "'4' or '8'; nothing here has it.")
    space, base, addr, blob = got
    off = addr - base

    def byte(i):
        return blob[off + i]

    def word(i):
        return blob[off + i] | (blob[off + i + 1] << 8)

    channels = 8 if byte(3) == 0x38 else 4

    # An RMT module's pointers are absolute addresses for wherever the module
    # was assembled to live, which need not be where it sits in the ROM -- a
    # banked cartridge, or one that copies the module into RAM, puts it
    # somewhere else entirely. So take the origin from the module rather than
    # from the ROM: the instrument table always follows the 16-byte header, so
    # its pointer minus 16 is where the module thinks it starts.
    origin = word(8) - 16
    lo, hi = origin, origin + (len(blob) - off)

    def check(name, a):
        return {"name": name, "addr": a, "in_range": lo <= a < hi,
                "at": "$%04X" % a,
                "file": off + (a - origin) if lo <= a < hi else None}

    instruments = check("instruments", word(8))
    tracks_lo = check("track pointers (low)", word(10))
    tracks_hi = check("track pointers (high)", word(12))
    song = check("song", word(14))

    ntracks = 0
    if tracks_lo["in_range"] and tracks_hi["in_range"]:
        tl, th = tracks_lo["file"], tracks_hi["file"]
        span = min(th - tl, 256) if th > tl else 0
        for k in range(span):
            a = (blob[th + k] << 8) | blob[tl + k]
            if a and lo <= a < hi:
                ntracks = k + 1

    rows = 0
    order = []
    if song["in_range"]:
        s = song["file"]
        for k in range(0, min(len(blob) - s, 4096), channels):
            row = list(blob[s + k:s + k + channels])
            if len(row) < channels or all(x == 0xFF for x in row):
                break
            order.append(row)
            rows += 1

    return {
        "format": fmt.get("name", "RMT module"),
        "chip": "pokey2" if channels == 8 else "pokey",
        "reader": "rmt",
        "module_at": "%s:%04X" % (space, addr),
        "origin": "$%04X" % origin,
        "magic": blob[off:off + 4].decode("latin1"),
        "channels": channels,
        "track_length": byte(4),
        "song_speed": byte(5),
        "player_frequency": byte(6),
        "tables": [instruments, tracks_lo, tracks_hi, song],
        "tracks": ntracks,
        "song_rows": rows,
        "order": order[:32],
        "renderable": False,
        "why_not": ("RMT's sound comes from its instrument engine -- per-"
                    "instrument envelope tables for volume, distortion and "
                    "pitch, plus effects. Reading the note list without it "
                    "would give a song that looks right and plays wrong, so "
                    "this reports the structure and stops. Record it with "
                    "tracker.py capture to hear it."),
    }


def pull_direct(cart, fmt):
    """A format that names the note bytes outright, rather than the tables above them.

    Every other reader here describes a *player*: where its song table is, how
    a track points at a pattern, which byte of a note holds the pitch. That is
    the right description when you have read the player's code, and it is the
    only way to get all of a cartridge's songs.

    This one describes a *finding*. `explore.py` works out what the note bytes
    mean by ear, and what it learns is real -- the record size, which field is
    the pitch, where the two streams sit -- but it stops short of the song and
    track tables, because those live in the player's code and no amount of
    listening reveals them. Without somewhere to put that knowledge it is lost
    when the browser closes, which is how the same cartridge gets decoded three
    times.

    So `reader: "direct"` is deliberately less than a player description: one
    voice, one stretch of notes, at an address someone worked out. It plays,
    which means it can be checked, and it is a starting point for the real
    thing rather than a substitute for it. `guessed: true` marks it as what it
    is, and tools say so rather than presenting it as an established format.
    """
    r = Reader(cart)
    v = fmt["voice"]
    shape = v.get("shape", "serial")
    count = int(v.get("count", 64))
    notes = []
    err = None

    def field(rec, spec):
        if not spec:
            return None
        byte_i, shift, width = spec
        if byte_i >= len(rec):
            return 0
        return (rec[byte_i] >> shift) & ((1 << width) - 1)

    try:
        if shape == "parallel":
            psp, paddr = parse_loc(v["pitch_at"])
            dsp, daddr = parse_loc(v["dur_at"])
            for i in range(count):
                d = r.byte(dsp, daddr + i, "duration %d" % i)
                if d == 0:
                    break
                notes.append({"pitch": r.byte(psp, paddr + i, "pitch %d" % i),
                              "duration": d, "volume": None})
        else:
            sp, addr = parse_loc(v["at"])
            stride = max(1, int(v.get("stride", 2)))
            for i in range(count):
                rec = list(r.bytes_at(sp, addr + i * stride, stride,
                                      "note %d" % i))
                notes.append({"pitch": field(rec, v.get("pitch")) or 0,
                              "duration": field(rec, v.get("duration")) or 0,
                              "volume": field(rec, v.get("volume"))})
    except Exception as e:                                    # noqa: BLE001
        err = str(e)

    return {
        "format": fmt.get("name", "?"),
        "chip": fmt.get("chip", "tia"),
        "reader": "direct",
        "guessed": bool(fmt.get("guessed")),
        "playback": fmt.get("playback") or {},
        "voices": [{"shape": shape, "notes": notes, "error": err,
                    "at": v.get("at") or v.get("pitch_at", "?")}],
        "spans": r.spans,
    }


def render_direct(doc, region="ntsc"):
    """The direct reading as per-frame (audc, audf, volume), one voice.

    The playback settings are the listener's, not the cartridge's: a waveform
    and a volume chosen in `explore.py` because they made the tune audible. A
    player picks those per note from an instrument table this reader knows
    nothing about, so they are recorded as what they are -- how it was played
    back while being worked out -- and not dressed up as the game's own.
    """
    p = doc.get("playback") or {}
    audc = int(p.get("audc", 12))
    vol = max(0, min(15, int(p.get("volume", 8))))
    fixed = int(p.get("fixed_duration") or 0)
    scale = max(1, int(p.get("dur_scale") or 1))
    tracks = []
    for v in doc["voices"]:
        frames = []
        for n in v["notes"]:
            d = fixed if fixed else max(1, int(n["duration"]) * scale)
            d = min(d, 240)
            nv = vol if n.get("volume") is None else \
                max(0, min(15, int(n["volume"])))
            for _ in range(d):
                frames.append((audc, int(n["pitch"]), nv))
        tracks.append(frames)
    return tracks


def pull_parallel(cart, fmt):
    """A player that keeps pitch and duration in two separate arrays.

    Midnight Mutants' player nests song -> track -> pattern -> note, with the
    fields of a note packed into consecutive bytes. This one does none of that.
    Each voice has **two parallel streams** -- one byte of pitch and one byte of
    duration per note, in different places -- read through two pointers that
    advance together. A zero in the duration stream ends the part.

    That is not a variation on the other model, it is a different shape, which
    is the whole reason `songfmt` describes players with a file instead of
    assuming one. `reader: "parallel"` selects this.

    The envelope is a linear decay of the whole AUDC byte: POKEY packs volume
    into its low nibble, so decrementing the byte fades the note. How fast comes
    from a table indexed by the note's own duration -- longer notes fade slower.
    """
    r = Reader(cart)
    v = fmt["voices_at"]
    nvoices = int(fmt.get("voices", 4))
    sp, _ = parse_loc(v["audc"])

    def table(key, n):
        s2, a = parse_loc(v[key])
        return list(r.bytes_at(s2, a, n, key))

    audc = table("audc", nvoices)
    plo = table("pitch_lo", nvoices)
    phi = table("pitch_hi", nvoices)
    dlo = table("dur_lo", nvoices)
    dhi = table("dur_hi", nvoices)

    decay = None
    if "decay" in fmt:
        dsp, da = parse_loc(fmt["decay"]["table"])
        decay = list(r.bytes_at(dsp, da, fmt["decay"]["count"], "decay table"))

    limit = int(fmt.get("limit", 256))
    voices = []
    for i in range(nvoices):
        pp = (phi[i] << 8) | plo[i]
        dp = (dhi[i] << 8) | dlo[i]
        notes = []
        err = None
        try:
            for k in range(limit):
                d = r.byte(sp, dp + k, "voice %d duration %d" % (i, k))
                if d == 0:
                    break
                p = r.byte(sp, pp + k, "voice %d pitch %d" % (i, k))
                notes.append({"duration": d, "pitch": p})
        except Exception as e:                                # noqa: BLE001
            err = str(e)
        voices.append({"audc": audc[i], "pitch_at": "%s:%04X" % (sp, pp),
                       "dur_at": "%s:%04X" % (sp, dp), "notes": notes,
                       "error": err})
    out = {"format": fmt.get("name", "?"), "chip": fmt.get("chip", "pokey"),
           "reader": "parallel", "decay": decay, "voices": voices,
           "spans": r.spans}
    return out


def render_parallel(doc, region="ntsc"):
    """The parallel-stream song as per-frame (audc, audf, volume) per voice.

    Volume comes out of the AUDC byte and decays with it, which is what the
    player does: one subtraction from the whole byte, once every `decay[dur]`
    frames. Modelling it as a separate envelope would be tidier and wrong.
    """
    decay = doc.get("decay") or []
    tracks = []
    for v in doc["voices"]:
        frames = []
        for n in v["notes"]:
            dur = n["duration"]
            audc = v["audc"]
            step = decay[dur] if dur < len(decay) else 0
            cur = audc
            countdown = step
            for f in range(dur):
                frames.append(((cur >> 5) & 7, n["pitch"], cur & 0x0F))
                if step:
                    countdown -= 1
                    if countdown == 0:
                        countdown = step
                        cur = (cur - 1) & 0xFF
        tracks.append(frames)
    return tracks


def read_voice(r, cart, fmt, ptr, bank, voice, n, vi, patterns):
    """Walk one voice: a list of pattern pointers, then each pattern.

    `voice["order"]` is the sequence of pattern addresses -- repeats included,
    because the repetition is the arrangement. The patterns themselves go into
    the shared table, read once however many times they are named.
    """
    tk, pat_cfg, note_cfg = fmt["track"], fmt["pattern"], fmt["note"]
    sp = space_for(cart, ptr, bank)
    order, term = [], None
    for _ in range(tk.get("limit", 64)):
        lo = r.byte(sp, ptr, "song %d voice %d track" % (n, vi))
        hi = r.byte(sp, ptr + 1, "song %d voice %d track" % (n, vi))
        if hi == 0:                       # end_when: high_byte_zero
            term = lo
            break
        order.append(lo | (hi << 8))
        ptr += 2
    voice["terminator"] = term

    for p in order:
        psp = space_for(cart, p, bank)
        key = "%s:%04X" % (psp, p)
        voice["order"].append(key)
        if key in patterns:
            patterns[key]["used_by"].append("song %d voice %d" % (n, vi))
            continue
        count = r.byte(psp, p, "pattern %s count" % key)
        rec = pat_cfg["record"]
        raw = r.bytes_at(psp, p + 1, count * rec, "pattern %s notes" % key)
        notes = []
        for i in range(count):
            b = raw[i * rec:(i + 1) * rec]
            fields = {}
            for name, (byte_i, lo, width) in note_cfg["fields"].items():
                fields[name] = bits(b[byte_i], lo, width)
            fields["rest"] = (b[1] == 0)
            notes.append(fields)
        patterns[key] = {"count": count, "notes": notes,
                         "used_by": ["song %d voice %d" % (n, vi)]}


def encode_note(fields, fmt):
    """Turn a note's fields back into its bytes."""
    rec = fmt["pattern"]["record"]
    b = [0] * rec
    if fields.get("rest"):
        # A rest is the whole pitch byte zero; the player tests it directly, so
        # anything else in that byte would stop it being a rest.
        for name, (byte_i, lo, width) in fmt["note"]["fields"].items():
            if byte_i == 0:
                b[0] = put_bits(b[0], lo, width, fields[name])
        b[1] = 0
        return bytes(b)
    for name, (byte_i, lo, width) in fmt["note"]["fields"].items():
        v = fields[name]
        if not 0 <= v < (1 << width):
            raise FormatError("%s is %d, which does not fit in %d bit%s"
                              % (name, v, width, "" if width == 1 else "s"))
        b[byte_i] = put_bits(b[byte_i], lo, width, v)
    return bytes(b)


# ------------------------------------------------------------------ playback
#
# Turning notes back into register values needs the player's envelope, and an
# envelope is the one part of a music format that is genuinely a program rather
# than a layout. So it is not described declaratively and guessed at: the format
# file names an engine, and an engine that is not implemented is refused rather
# than approximated. A song rendered with the wrong envelope sounds plausible
# and is wrong, which is the worst way for a tool to fail.


class Adsr5(object):
    """Attack, decay, sustain, release, off -- five stages, one per frame.

    Volume is an 8-bit accumulator whose high nibble reaches the 4-bit AUDV, so
    a decrement of $38 a frame is a slope, not a step. Stage 2 writes no volume
    at all: the note holds whatever it last wrote, and anything else writing
    AUDV during that time keeps it.

    Transcribed from the five handlers the player reaches through a jump table,
    and checked against the running machine -- see `--verify`.
    """

    STAGES = ("attack", "decay", "sustain", "release", "off")

    def __init__(self, rows, fields):
        self.rows = rows
        self.f = fields

    def row(self, inst):
        return self.rows[inst % len(self.rows)]

    def note_on(self, inst, rest=False):
        r, f = self.row(inst), self.f
        if rest:
            # A rest takes the shortcut the player takes: straight to the last
            # stage, so the voice is cut rather than ramped down.
            self.state, self.counter, self.vol, self.inst = 4, 0, 0, inst
            return 0
        self.inst = inst
        self.state = 0
        self.counter = r[f["counter"]]
        self.vol = r[f["flags"]] & 0x0F
        return self.vol

    def tick(self):
        """One frame. Returns the AUDV value written, or None if none was."""
        r, f = self.row(self.inst), self.f
        st = self.state
        if st == 4:
            return 0
        if st == 2:                              # sustain writes no volume
            self.counter = (self.counter - 1) & 0xFF
            if self.counter & 0x80:
                self.counter = r[f["release_len"]]
                self.state = 3
            return None
        if st == 0:
            v = self.vol + r[f["attack"]]
            self.vol = r[f["peak"]] if v > 0xFF or v > r[f["peak"]] else v
        elif st == 1:
            v = self.vol - r[f["decay"]]
            self.vol = r[f["sustain"]] if v < 0 or v < r[f["sustain"]] else v
        else:
            v = self.vol - r[f["release"]]
            self.vol = 0 if v < 0 else v
        out = ((self.vol >> 4) | r[f["flags"]]) & 0x0F
        self.counter = (self.counter - 1) & 0xFF
        if self.counter & 0x80:
            if st == 0:
                self.counter, self.state = r[f["decay_len"]], 1
                self.vol = r[f["peak"]]
            elif st == 1:
                self.counter, self.state = r[f["sustain_len"]], 2
            else:
                self.state = 4
        return out


ENGINES = {"adsr5": Adsr5}


def render(songs, n):
    """One song, as a list of per-frame (audc, audf, audv) per voice.

    A voice runs its patterns in order, a note holds the channel for as many
    frames as its duration index says, and the envelope supplies the volume in
    between. Voices are independent and can be different lengths; the song is
    as long as its longest voice.
    """
    chip = songs.get("chip", "tia")
    if chip not in CHANNELS:
        raise FormatError(
            "unknown chip %r. This toolkit models %s."
            % (chip, " and ".join(sorted(CHANNELS))))

    # `voices` was declared and never checked, which meant a format that named
    # the wrong chip rendered anyway: two TIA voices padded out to four silent
    # POKEY channels, or four POKEY voices quietly truncated to two. Both
    # produce a file that plays, so neither announces itself.
    declared = len(songs["songs"][0]["voices"]) if songs.get("songs") else 0
    if declared > CHANNELS[chip]:
        raise FormatError(
            "this format describes %d voices but %s has %d channels. Either "
            "the chip is wrong or the voice_ptr list is."
            % (declared, chip, CHANNELS[chip]))

    # POKEY's AUDCTL is global and changes what every channel means -- 16-bit
    # pairing, the 1.79 MHz clock, high-pass. Rendering as if it were zero when
    # it is not gives pitches that are wrong by octaves, so an unmodelled
    # setting is refused here exactly as the tracker refuses it.
    audctl = songs.get("audctl") or 0
    if is_pokey(chip):
        missing = pokey_unsupported(audctl)
        if missing:
            raise FormatError(
                "this song's AUDCTL ($%02X) uses %s, which this model does not "
                "cover. Rendering it would give the wrong sound rather than no "
                "sound, so it is refused. See docs/audio.md, \"POKEY\"."
                % (audctl, ", ".join(missing)))

    eng_name = songs.get("engine")
    if eng_name not in ENGINES:
        raise FormatError(
            "this format's instrument engine is %r, which is not implemented. "
            "Rendering it would mean inventing an envelope, and a song with the "
            "wrong envelope sounds convincing and is wrong. Implemented: %s."
            % (eng_name, ", ".join(sorted(ENGINES)) or "none"))
    durations = songs.get("durations")
    waves = songs.get("waveforms")
    instruments = songs.get("instruments")
    # Where a note's AUDC comes from is a property of the player, not a
    # constant. Midnight Mutants spends three bits of the pitch byte on an
    # index into a waveform table -- it can afford to, because TIA's AUDF is
    # only five bits wide. A POKEY player has no spare bits: AUDF is the whole
    # byte, so the control value comes from the instrument instead. Both are
    # "a note names its timbre"; they differ in where the room was found.
    from_instrument = songs.get("audc_from") == "instrument"
    needed = [(durations, "durations"), (instruments, "instruments")]
    if not from_instrument:
        needed.append((waves, "waveforms"))
    for need, what in needed:
        if not need:
            raise FormatError(
                "this song carries no %s table, so it cannot be rendered. Pull "
                "it again with a format file that describes one." % what)
    fields = songs["instrument_fields"]

    matching = [s for s in songs["songs"] if s["n"] == n]
    if not matching:
        raise FormatError("no song %d in this file" % n)
    song = matching[0]

    tracks = []
    for voice in song["voices"]:
        env = ENGINES[eng_name](instruments, fields)
        frames = []
        for key in voice["order"]:
            pat = songs["patterns"].get(key)
            if pat is None:
                raise FormatError("song %d names pattern %s, which is not in "
                                  "this file" % (n, key))
            for note in pat["notes"]:
                length = durations[note["duration"] % len(durations)]
                rest = note.get("rest")
                audf = 0 if rest else note["pitch"]
                if rest:
                    audc = 0
                elif from_instrument:
                    # POKEY keeps distortion in bits 7-5 of AUDC and volume in
                    # 3-0, so the instrument's control byte shifts down by five
                    # -- $A0 is distortion 5, $80 is 4. Shifting by four
                    # instead reads $A as a distortion that does not exist and
                    # every note comes out on the wrong waveform, which is
                    # audible but not obviously a bug: it still plays a tune.
                    row = instruments[note["instrument"] % len(instruments)]
                    flags = row[fields["flags"]]
                    audc = ((flags >> 5) & 7 if is_pokey(chip)
                            else (flags >> 4) & 0x0F)
                else:
                    audc = waves[note["waveform"] % len(waves)] & 0x0F
                v = env.note_on(note["instrument"], rest)
                # On POKEY the timbre and the volume share one register, so
                # what the player writes when a note finishes is the whole
                # byte as zero -- distortion included -- and not the timbre at
                # volume nil. A note whose release merely decays to zero keeps
                # its distortion, so this turns on the envelope's *stage* and
                # not on the volume being zero: the capture has 961 frames at
                # volume zero that still carry distortion 5.
                off = 4 if from_instrument else None
                frames.append((0 if env.state == off else audc, audf, v))
                for _ in range(length - 1):
                    # The byte a frame writes belongs to the stage the
                    # envelope was in when the frame began; `tick` advances
                    # the stage at the end. Reading it afterwards silences the
                    # timbre one frame early -- which looked like a fix,
                    # because it moved the error rather than removing it.
                    was = env.state
                    v2 = env.tick()
                    frames.append((0 if was == off else audc, audf,
                                   v if v2 is None else v2))
                    if v2 is not None:
                        v = v2
        tracks.append(frames)
    return tracks, audctl


def push(cart, fmt, songs, strict=True):
    """Encode the songs back to bytes, in place. Returns a list of writes.

    One write per pattern, not per reference: a shared pattern is one piece of
    data, and editing it changes every song that names it. That is what the
    cartridge does, so it is what this does.
    """
    writes = []
    for key, pat in sorted(songs.get("patterns", {}).items()):
        psp, paddr = parse_loc(key)
        data = b"".join(encode_note(nf, fmt) for nf in pat["notes"])
        room = pat["count"] * fmt["pattern"]["record"]
        used = ", ".join(pat.get("used_by", [])) or "?"
        if len(data) > room:
            raise FormatError(
                "pattern %s now needs %d bytes but owns %d. A pattern cannot "
                "grow: whatever follows it in the ROM is not yours. Shorten it, "
                "or move it into free space and repoint the tracks that name "
                "it (%s)." % (key, len(data), room, used))
        if len(data) < room and strict:
            raise FormatError(
                "pattern %s now has %d notes where it had %d. Shrinking would "
                "leave stale bytes the player still reads; pad it back, or pass "
                "--allow-shrink to fill the tail with rests."
                % (key, len(pat["notes"]), pat["count"]))
        if len(data) < room:
            data = data + b"\x00" * (room - len(data))
        writes.append((psp, paddr + 1, data, "pattern %s (%s)" % (key, used)))
    return writes


def apply_writes(raw, cart, writes):
    """Apply writes to a copy of the raw image, returning the new bytes."""
    out = bytearray(raw)
    head = 128 if cart.header_bytes else 0
    for space, addr, data, _what in writes:
        off = head + cart._offset(space, addr)
        out[off:off + len(data)] = data
    return bytes(out)


def declared_spans(cart, songs):
    """Every file offset the format claims, as a set."""
    head = 128 if cart.header_bytes else 0
    owned = set()
    for space, addr, n, _what in songs.get("spans", []):
        base = head + cart._offset(space, addr)
        owned.update(range(base, base + n))
    return owned


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().split("\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")

    pl = sub.add_parser("pull", help="ROM -> song data")
    pl.add_argument("rom")
    pl.add_argument("-f", "--format", required=True)
    pl.add_argument("-o", "--out")
    pl.add_argument("--low"); pl.add_argument("--mapper")

    ps = sub.add_parser("push", help="song data -> ROM, in place")
    ps.add_argument("rom")
    ps.add_argument("-f", "--format", required=True)
    ps.add_argument("-i", "--songs", required=True)
    ps.add_argument("-o", "--out", required=True)
    ps.add_argument("--allow-shrink", action="store_true",
                    help="pad a shortened pattern with rests instead of refusing")
    ps.add_argument("--low"); ps.add_argument("--mapper")

    rd = sub.add_parser("render", help="song data -> .trk for the tracker")
    rd.add_argument("songs")
    rd.add_argument("-n", "--song", type=int, required=True,
                    help="which song number to render")
    rd.add_argument("-o", "--out", required=True)
    rd.add_argument("--region", default="ntsc", choices=["ntsc", "pal"])
    rd.add_argument("--verify", metavar="LOG",
                    help="compare against an audio.lua capture of the same "
                         "song and report how many frames match")

    args = ap.parse_args()
    if not args.cmd:
        ap.print_help()
        return 1

    if args.cmd == "render":
        songs = json.load(open(args.songs, encoding="utf-8"))
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import tracker
        try:
            tracks, audctl = render(songs, args.song)
        except FormatError as e:
            sys.stderr.write(str(e) + "\n")
            return 2

        song = tracker.Song(title="%s -- song %d" % (songs.get("format", "?"),
                                                     args.song),
                            region=args.region, chip=songs.get("chip", "tia"))
        nch = song.nch
        length = max([len(t) for t in tracks] or [0])
        last = [None] * nch
        for i in range(length):
            cells = []
            for ch in range(nch):
                if ch < len(tracks) and i < len(tracks[ch]):
                    cur = tracks[ch][i]
                else:
                    cur = (0, 0, 0)
                cells.append(None if cur == last[ch] else cur)
                last[ch] = cur
            song.add(cells, audctl=audctl)
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(tracker.dump(song))
        print("  %s -- song %d, %d frames, %d voices"
              % (args.out, args.song, length, len(tracks)))
        print("  hear it:  python tools/tracker.py render %s -o song.wav"
              % args.out)

        if args.verify:
            # A capture opens on however long the game takes to reach the tune
            # -- for Midnight Mutants' title screen, about eighteen seconds --
            # so comparing frame 0 to frame 0 compares silence to music and
            # reports 0%. Slide the render along the capture and report the
            # best fit, saying where it landed so the number can be judged.
            played = list(tracker.read_capture(args.verify,
                                               args.region).states())
            best = (0, -1, 0)
            for ch, ours in enumerate(tracks):
                for off in range(max(1, len(played) - 50)):
                    n = min(len(ours), len(played) - off)
                    if n < 100:
                        break
                    same = sum(1 for i in range(n)
                               if ours[i] == played[off + i][ch])
                    if same > best[1]:
                        best = (off, same, n)
            off, same, n = best
            if n:
                print("  verify: best fit at capture frame %d -- %d of %d "
                      "frames identical (%.1f%%)"
                      % (off, same, n, 100.0 * same / n))
            else:
                print("  verify: the capture is too short to compare")
        return 0

    fmt = json.load(open(args.format, encoding="utf-8"))
    try:
        cart = cart_module.Cart(args.rom, mapper=getattr(args, "mapper", None),
                                low=getattr(args, "low", None))
    except (cart_module.UnknownMapper, cart_module.UnknownSpace) as e:
        sys.stderr.write("%s\n" % e)
        return 2

    if args.cmd == "pull":
        try:
            songs = pull(cart, fmt)
        except (FormatError, KeyError, IndexError) as e:
            sys.stderr.write("%s\n" % e)
            return 2
        if songs.get("reader") == "rmt":
            print("%s" % songs["format"])
            print("  %s module at %s -- %d channels, %d lines a pattern, "
                  "speed %d/%d"
                  % (songs["magic"], songs["module_at"], songs["channels"],
                     songs["track_length"], songs["song_speed"],
                     songs["player_frequency"]))
            for t in songs["tables"]:
                print("     %-22s %s%s"
                      % (t["name"], t["at"],
                         "" if t["in_range"] else "   OUTSIDE THE MODULE"))
            print("  %d tracks, %d rows of song order"
                  % (songs["tracks"], songs["song_rows"]))
            print("")
            print("  Not renderable, and that is a limit rather than a bug:")
            for line in _wrap(songs["why_not"], 68):
                print("  %s" % line)
            if args.out:
                with open(args.out, "w", encoding="utf-8") as f:
                    json.dump(songs, f, indent=1)
                print("  wrote %s" % args.out)
            return 0
        if songs.get("reader") == "parallel":
            # A flat list per voice: no patterns to share, nothing to count
            # twice. Report it as what it is rather than forcing it into the
            # other model's vocabulary.
            print("%s" % songs["format"])
            live = [v for v in songs["voices"] if v["notes"]]
            print("  %d voices, %d with notes, %d notes in all"
                  % (len(songs["voices"]), len(live),
                     sum(len(v["notes"]) for v in songs["voices"])))
            for i, v in enumerate(songs["voices"]):
                print("     voice %d  AUDC $%02X  %3d notes  pitch %s  dur %s%s"
                      % (i, v["audc"], len(v["notes"]), v["pitch_at"],
                         v["dur_at"], "  " + v["error"] if v["error"] else ""))
            owned = declared_spans(cart, songs)
            print("  claims %d bytes of the image in %d spans"
                  % (len(owned), len(songs["spans"])))
            if args.out:
                with open(args.out, "w", encoding="utf-8") as f:
                    json.dump(songs, f, indent=1)
                print("  wrote %s" % args.out)
            return 0
        n_notes = sum(len(p["notes"]) for p in songs["patterns"].values())
        n_pats = len(songs["patterns"])
        n_refs = sum(len(v["order"]) for s in songs["songs"] for v in s["voices"])
        # A pattern is shared if anything names it twice -- a track repeating
        # it counts as much as two songs using it, because the bytes are the
        # same bytes either way.
        import collections as _c
        refs = _c.Counter(k for s2 in songs["songs"] for v in s2["voices"]
                          for k in v["order"])
        shared = sum(1 for k, c in refs.items() if c > 1)
        owned = declared_spans(cart, songs)
        print("%s" % songs["format"])
        print("  %d songs, %d patterns, %d notes"
              % (len(songs["songs"]), n_pats, n_notes))
        named = [s2 for s2 in songs["songs"] if s2.get("title")]
        if named:
            for s2 in named:
                live = sum(1 for v in s2["voices"] if v["order"])
                print("     song %-2d %d voices  %s"
                      % (s2["n"], live, s2["title"]))
        print("  %d pattern references -- %d patterns are shared, and editing "
              "one\n     changes every song that names it" % (n_refs, shared))
        print("  claims %d bytes of the image in %d spans"
              % (len(owned), len(songs["spans"])))
        bad = [(s["n"], vi) for s in songs["songs"]
               for vi, v in enumerate(s["voices"]) if v.get("error")]
        if bad:
            print("  unread: %s" % ", ".join("song %d voice %d" % b for b in bad))
        if args.out:
            with open(args.out, "w", encoding="utf-8") as f:
                json.dump(songs, f, indent=1)
            print("  wrote %s" % args.out)
        return 0

    songs = json.load(open(args.songs, encoding="utf-8"))
    raw = open(args.rom, "rb").read()
    try:
        writes = push(cart, fmt, songs, strict=not args.allow_shrink)
    except FormatError as e:
        sys.stderr.write("%s\n" % e)
        return 2
    new = apply_writes(raw, cart, writes)

    # The guarantee: nothing outside what the format claims may move.
    owned = declared_spans(cart, songs)
    stray = [i for i in range(len(raw)) if raw[i] != new[i] and i not in owned]
    if stray:
        sys.stderr.write(
            "refusing to write: %d byte%s outside the song data would change "
            "(first at file offset $%05X). That is a bug in the format "
            "description, not something to override.\n"
            % (len(stray), "" if len(stray) == 1 else "s", stray[0]))
        return 3
    changed = sum(1 for i in range(len(raw)) if raw[i] != new[i])
    with open(args.out, "wb") as f:
        f.write(new)
    print("wrote %s" % args.out)
    print("  %d byte%s changed, all of them inside the %d bytes the format owns"
          % (changed, "" if changed == 1 else "s", len(owned)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
