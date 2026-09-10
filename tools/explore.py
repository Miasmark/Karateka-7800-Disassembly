#!/usr/bin/env python3
"""
Guess at a music format, listen to the guess, and adjust until it sings.

    python tools/explore.py game.a78 --at b6:A951
    python tools/explore.py game.a78            (starts from an audiotrace scan)

`audiotrace.py` finds where a cartridge keeps its music. Turning those bytes
into notes is the part no tool can do alone, because every player invents its
own layout. But it is also not something a person should do with a hex editor
and a pencil.

So this splits the job the way it wants to be split. **The machine proposes,
the ear disposes.** It reads a stretch of bytes, offers a set of plausible
readings of them, renders each one to audio, and lets you change the parameters
and listen again. Music is unmistakable when you hear it and invisible when you
stare at it, so the ear is the right instrument -- this just puts it within
reach.

## The two shapes worth trying first

**Serial**: one record per note, fields packed into consecutive bytes. Midnight
Mutants is like this -- two bytes a note, an instrument and duration in the
first, a waveform and pitch in the second.

**Parallel**: separate arrays, one for pitch and one for duration, advanced in
step. The player behind 58 images in the library is like this, and it looks
like noise if you read it as serial.

Neither is exotic and most players are one or the other, so trying both against
your ear costs a minute and settles it. When it sounds right, `--emit` writes
the format file that `songfmt.py` reads.

## What it does not claim

**It cannot tell music from anything else.** The ranking assumes you are
already pointed at music and guesses at the *layout*; asked to rank graphics
or 6502 code it answers just as confidently. That is not a bug to tune out.
The structural tests it leans on -- a repeating record size, streams that end
where each other begin -- detect *structure*, and MARIA cell data repeats
every 4 bytes while 6502 instructions average 3, so both score about as well
as a real tune does. Pointing it somewhere sensible is your job (audiotrace.py
does it by default), and rejecting a wrong reading is your ear's.

A reading that sounds right is a hypothesis, not a proof. Check it against
`tracker.py capture` of the same game before trusting it -- that records what
the cartridge actually plays, and if your reading and the machine disagree,
your reading is wrong. That is exactly how the two format files in `formats/`
were confirmed.
"""
import argparse
import io
import json
import os
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cart as cart_module
import tracker

CART = None
ROM = None
STATE = None


def default_state(space, addr):
    return {
        "shape": "serial",
        "space": space,
        "addr": addr,
        "count": 64,
        # serial
        "stride": 2,
        "pitch_byte": 1, "pitch_shift": 0, "pitch_bits": 5,
        "dur_byte": 0, "dur_shift": 0, "dur_bits": 4,
        "vol_byte": 0, "vol_shift": 4, "vol_bits": 4,
        # parallel
        "pitch_at": addr,
        "dur_at": addr + 64,
        # playback
        "chip": "tia",
        "audc": 12,
        "volume": 8,
        "fixed_duration": 0,     # 0 = take it from the data
        "dur_scale": 1,
        "rate": 60.0,
    }


def bits(v, shift, width):
    return (v >> shift) & ((1 << width) - 1)


def decode(st):
    """The bytes, read the way the current settings say. Returns notes."""
    c = CART
    sp = st["space"]
    notes = []
    n = max(1, min(int(st["count"]), 512))

    def rd(a):
        try:
            return c.byte(sp, a)
        except Exception:                                    # noqa: BLE001
            return 0

    if st["shape"] == "parallel":
        for i in range(n):
            p = rd(st["pitch_at"] + i)
            d = rd(st["dur_at"] + i)
            notes.append({"pitch": p, "duration": d, "volume": None})
    else:
        stride = max(1, min(int(st["stride"]), 8))
        for i in range(n):
            base = st["addr"] + i * stride
            rec = [rd(base + k) for k in range(stride)]

            def field(byte_i, shift, width):
                if byte_i >= stride:
                    return 0
                return bits(rec[byte_i], shift, width)

            notes.append({
                "pitch": field(st["pitch_byte"], st["pitch_shift"],
                               st["pitch_bits"]),
                "duration": field(st["dur_byte"], st["dur_shift"],
                                  st["dur_bits"]),
                "volume": field(st["vol_byte"], st["vol_shift"],
                                st["vol_bits"]),
                "raw": rec,
            })
    return notes


def to_song(st, notes):
    """Notes as a tracker song, so the existing renderer can play them."""
    song = tracker.Song(title="explore", chip=st["chip"], region="ntsc")
    song.rate = float(st.get("rate") or 60.0)
    nch = song.nch
    fixed = int(st.get("fixed_duration") or 0)
    scale = max(1, int(st.get("dur_scale") or 1))
    fmax = 255 if tracker.is_pokey(st["chip"]) else tracker.AUDF_MAX
    vol = max(0, min(15, int(st["volume"])))
    audc = int(st["audc"])
    for note in notes:
        d = fixed if fixed else max(1, int(note["duration"]) * scale)
        d = min(d, 240)
        v = vol if note["volume"] is None else \
            max(0, min(15, int(note["volume"])))
        cell = (audc, int(note["pitch"]) & fmax, v)
        for k in range(d):
            row = [cell if ch == 0 else (0, 0, 0) for ch in range(nch)]
            song.add(row)
    if not song.rows:
        song.add([(0, 0, 0)] * nch)
    return song


def musicality(notes):
    """A rough score for "does this look like a tune rather than like code".

    Not a decision, a sort order. Real music uses a handful of distinct
    pitches, spreads them over a usable range, comes back to notes it has
    already used, and does not spend most of its bytes on zero.

    The scoring has to resist one specific trap: **narrowing a field always
    shrinks the alphabet**, so a naive "few distinct values is musical" score
    ranks a 4-bit read of anything above the truth. So the alphabet is judged
    against the range the field can even express, and a reading whose pitches
    are crammed into a few adjacent values scores badly however tidy it looks.
    """
    ps = [n["pitch"] for n in notes]
    if len(ps) < 8:
        return 0.0
    live = [p for p in ps if p]
    if len(live) < 6:
        return 0.0
    alphabet = len(set(live))
    if alphabet < 3:
        return 0.0      # one note held for the whole passage is not a melody
    spread = max(live) - min(live)
    score = 0.0

    # a tune has several distinct pitches, but not one per note
    if 4 <= alphabet <= 32:
        score += 1.0 - abs(alphabet - 12) / 40.0
    # and they are spread out, not three adjacent values
    if spread >= 8:
        score += min(1.0, spread / 48.0)
    # repetition: notes recur
    score += min(1.0, (len(live) - alphabet) / max(1.0, len(live)))
    # not mostly silence
    score += min(1.0, len(live) / max(1.0, len(ps)))
    # durations that look like durations: small, repeated, non-zero
    ds = [n["duration"] for n in notes if n["duration"]]
    if len(ds) >= len(ps) * 0.6:
        da = len(set(ds))
        if 1 <= da <= 10 and max(ds) <= 96:
            score += 1.0 + min(0.5, (len(ds) - da) / max(1.0, len(ds)))
    return round(score, 3)


def record_stride(space, addr, n=192, top=32):
    """Guess the record size of a serial format, by self-similarity.

    A serial player stores one fixed-size record per note, so some column of
    that record changes slowly -- an instrument that holds for a phrase, a
    duration repeated across a run of notes. That makes the byte stream
    resemble itself at a lag of exactly one record, and not at other lags.
    Midnight Mutants reads `09 3D 09 37 09 33`: every other byte is the same,
    so lag 2 matches 50% of the time and lag 3 barely at all.

    Lags run to 32, not to a note-sized 8. Commando's audio table repeats at
    16 -- ten meaningful bytes then six zeros -- which is an *instrument*
    table, the same shape as Midnight Mutants'. Scanning only note-sized lags
    found no structure there at all and left the table looking like noise.

    Returns (stride, strength) with strength in 0..1, or (0, 0.0) if nothing
    stands out. Like the parallel terminator test, this is structure rather
    than taste -- which is the only reason it is allowed to outvote the ear's
    shortlist ordering.
    """
    b = []
    for i in range(n):
        try:
            b.append(CART.byte(space, addr + i))
        except Exception:                                    # noqa: BLE001
            break
    if len(b) < 24:
        return 0, 0.0
    best, scores = (0, 0.0), {}
    # A lag is only worth measuring if the data repeats at it several times
    # over; at one or two repetitions the rate is noise wearing a percentage
    # sign. Cap the scan so every lag has at least three spans of evidence.
    top = max(2, min(top, len(b) // 3))
    for lag in range(1, top + 1):
        pairs = [(b[i], b[i + lag]) for i in range(len(b) - lag)]
        if not pairs:
            continue
        scores[lag] = sum(1 for x, y in pairs if x == y) / len(pairs)
    if 1 not in scores or len(scores) < 3:
        return 0, 0.0
    # A "1-byte record" is not a record, it is a run of repeated bytes, and
    # runs inflate every longer lag as a side effect. So a real record size
    # has to beat the trivial adjacent-repeat rate, not merely be the largest
    # number in the table -- otherwise a duration stream of `14 14 14 14` is
    # reported as a 2-byte record structure.
    runs = scores.pop(1)

    # Structure repeats at several scales at once, and the strongest lag is
    # usually not the record. Midnight Mutants stores 2-byte notes *and* a
    # 6-note phrase, so lag 12 scores highest (0.69) while the record is 2
    # (0.48). What marks the record is not its absolute rate but that it beats
    # the lags it does *not* divide into: at lag 2 the odd lags score zero, so
    # 2 explains the whole picture and 12 is 2 repeated. Take the smallest lag
    # that stands clear of its own non-multiples.
    best = (0, 0.0)
    for k in sorted(scores):
        if scores[k] < runs + 0.1:
            continue
        others = [v for j, v in scores.items() if j % k != 0]
        if not others:
            continue
        if scores[k] - max(others) >= 0.1:
            best = (k, round(scores[k], 3))
            break
    return best


def varying_column(space, addr, stride, n=64):
    """Which byte of a record moves, and which holds still.

    The column that changes on nearly every note is the pitch; the one that
    repeats is an instrument or a duration index. Returns (most, least).

    Measured as "how often does this column repeat its previous value", not as
    a count of distinct values, because we do not know where the data ends and
    a fixed window reads past it. That window is the weak point and it cannot
    be fixed from here: a 9-note pattern is 18 bytes, so *every* usable window
    overshoots it and measures whatever follows. On a run of real patterns
    this picks the right column about six times in seven, and when it misses,
    the other column is the next suggestion down. That is the tool working as
    intended -- it shortlists, your ear picks. Midnight Mutants' pattern is 22 notes; over
    96 bytes the tail put 14 distinct values into a column that is constant
    `09` throughout the actual music, and the distinct-count reading called
    the constant column the moving one. Adjacent-repeat survives that.
    """
    best, worst, hi, lo = 0, 0, -1.0, 2.0
    for k in range(stride):
        vals = []
        for i in range(n // max(stride, 1)):
            try:
                vals.append(CART.byte(space, addr + i * stride + k))
            except Exception:                                # noqa: BLE001
                break
        if len(vals) < 4:
            continue
        same = sum(1 for i in range(1, len(vals))
                   if vals[i] == vals[i - 1]) / (len(vals) - 1)
        if same < lo:
            lo, best = same, k          # moves most == repeats least
        if same > hi:
            hi, worst = same, k
    return best, worst


def run_length(space, addr, cap=256):
    """How many non-zero bytes sit at `addr` before the first zero.

    A parallel player ends a part with a zero duration, so this is how long
    the stream claims to be -- which is also how many notes to read.
    """
    n = 0
    while n < cap:
        try:
            if CART.byte(space, addr + n) == 0:
                break
        except Exception:                                    # noqa: BLE001
            break
        n += 1
    return n


def suggest(space, addr, chip):
    """Rank a spread of readings of the same bytes, best first.

    The parallel gap is *searched* rather than guessed from a list. It is the
    length of the pitch stream, which is whatever the composer needed -- the
    player behind 58 images in the library uses 11 -- so a tidy list of round
    numbers misses it exactly when it matters.
    """
    out = []
    rstride, rstrength = record_stride(space, addr)
    if rstride >= 8:
        # Too wide to be one note. A record this size holding a handful of
        # meaningful bytes and then padding is what an envelope looks like, so
        # say so rather than proposing eight-byte notes: the useful next move
        # is to find what indexes this table, not to keep reading it as a tune.
        note = ("these bytes repeat every %d, which is too wide for a note. "
                "This looks like an instrument or envelope table -- the thing "
                "a player indexes into, not the tune itself. Look for the "
                "note data that references it." % rstride)
        return [(0.0, note, default_state(space, addr))]
    pcol, dcol = varying_column(space, addr, rstride) if rstride else (0, 0)
    for stride in (1, 2, 3, 4):
        for pb in range(stride):
            for pshift, pbits in ((0, 8), (0, 5), (0, 6), (0, 4), (3, 5)):
                st = default_state(space, addr)
                st.update({"shape": "serial", "stride": stride,
                           "pitch_byte": pb, "pitch_shift": pshift,
                           "pitch_bits": pbits, "chip": chip,
                           "dur_byte": (pb + 1) % max(stride, 1),
                           "dur_shift": 0, "dur_bits": 8 if stride > 1 else 4})
                score = musicality(decode(st))
                why = ("serial, %d bytes a note, pitch in byte %d "
                       "(%d bits from %d)" % (stride, pb, pbits, pshift))
                if rstride and stride == rstride:
                    score += 2.0 * rstrength
                    why += " (the bytes repeat at %d)" % rstride
                    if pb == pcol:
                        score += 1.0
                        why += ", and byte %d is the one that moves" % pcol
                        st["dur_byte"] = dcol
                elif rstride:
                    # The bytes repeat at `rstride`, so a reading built on any
                    # other record size contradicts the evidence rather than
                    # merely lacking it. Penalising the mismatch and rewarding
                    # the match are the same claim; only doing the second let a
                    # one-byte-per-note reading of a two-byte record win on
                    # sheer variety.
                    score -= 2.0 * rstrength
                out.append((round(score, 3), why, st))
    for gap in range(3, 257):
        n = run_length(space, addr + gap)
        if n < 4:
            continue
        st = default_state(space, addr)
        st.update({"shape": "parallel", "pitch_at": addr,
                   "dur_at": addr + gap, "chip": chip, "count": n})
        score = musicality(decode(st))
        why = "parallel, durations %d bytes after the pitches" % gap
        # A parallel player usually lays its two streams end to end and ends
        # each with a zero, so the gap *is* the stream length. Two things then
        # have to agree: the byte just before the durations closes the pitch
        # stream, and the durations run for exactly that many notes. Testing
        # only the second fires by chance often enough to beat a true serial
        # reading; testing both is sharp. This is structure, not taste, which
        # is why it outranks any amount of scoring.
        try:
            capped = CART.byte(space, addr + gap - 1) == 0
        except Exception:                                    # noqa: BLE001
            capped = False
        if capped and n + 1 == gap:
            score += 3.0
            why += " (both streams end exactly where the other begins)"
        elif rstride:
            # The bytes repeat at a fixed record size, which is what a serial
            # format looks like and what two independent streams do not. Absent
            # the terminator proof above, that is evidence against this shape.
            score -= 2.0 * rstrength
        out.append((round(score, 3), why, st))
    out.sort(key=lambda x: -x[0])
    # keep the best of each kind so the list is not all one shape
    top, seen_par = [], 0
    for row in out:
        if row[1].startswith("parallel"):
            if seen_par >= 5:
                continue
            seen_par += 1
        top.append(row)
        if len(top) >= 12:
            break
    return top


def format_signature():
    """This cartridge's player fingerprint, or None if nothing writes audio.

    Keying a saved reading on the *player* rather than the title is what makes
    it worth saving at all: the engine is what recurs. One signature in the
    shipped set covers 53 images. A finding filed under a title helps one game;
    the same finding filed under its player may open a shelf of them.
    """
    try:
        import audiotrace
        return audiotrace.player_signature(ROM) or None
    except Exception:                                        # noqa: BLE001
        return None


def emit_format(st):
    """The format file this reading amounts to, for songfmt.py.

    It emits `reader: "direct"`, which is an honest description of what an ear
    can establish: these bytes, read this way, are the notes. It is not a
    player description -- there is no song table, no track list, no instrument
    engine, because none of those can be heard. `guessed: true` says so in the
    file itself, and every tool that loads it repeats the warning.

    That is still worth writing down. It plays, so the next person can check it
    in seconds instead of starting from a hex dump, and the `match` block files
    it under the player's fingerprint so it is found again automatically.
    """
    sig = format_signature()
    match = {}
    if sig:
        match["player"] = sig
    else:
        # No audio stores found to fingerprint -- fall back to identifying the
        # one cartridge, which is weaker but never matches the wrong game.
        match["size"] = len(CART.rom)
        title = ((CART.info or {}).get("title") or "").strip()
        if title:
            match["title"] = title

    doc = {
        "name": "%s -- read by ear, not from the player"
                % os.path.splitext(os.path.basename(ROM))[0],
        "note": [
            "Written by explore.py from a reading that sounded right.",
            "",
            "This describes the note bytes only. A real format file also",
            "describes the player above them -- the song table, the tracks,",
            "the instruments -- which is where the rest of the cartridge's",
            "music is. See templates/format.json for that shape.",
            "",
            "Confirm it against the hardware before trusting it:",
            "    python tools/tracker.py capture <rom> --seconds 20",
            "and compare. A reading that sounds right is a hypothesis.",
        ],
        "guessed": True,
        "reader": "direct",
        "chip": st["chip"],
        "match": match,
        "playback": {
            "audc": int(st["audc"]),
            "volume": int(st["volume"]),
            "rate": float(st.get("rate") or 60.0),
            "fixed_duration": int(st.get("fixed_duration") or 0),
            "dur_scale": int(st.get("dur_scale") or 1),
            "_note": "How it was played back while being worked out, not what "
                     "the cartridge does. The player picks these per note from "
                     "an instrument table this file does not describe.",
        },
    }

    if st["shape"] == "parallel":
        doc["voice"] = {
            "shape": "parallel",
            "pitch_at": "%s:%04X" % (st["space"], st["pitch_at"]),
            "dur_at": "%s:%04X" % (st["space"], st["dur_at"]),
            "count": int(st["count"]),
            "_note": "Two streams read in step; a zero duration ends the part.",
        }
    else:
        doc["voice"] = {
            "shape": "serial",
            "at": "%s:%04X" % (st["space"], st["addr"]),
            "count": int(st["count"]),
            "stride": int(st["stride"]),
            "pitch": [int(st["pitch_byte"]), int(st["pitch_shift"]),
                      int(st["pitch_bits"])],
            "duration": [int(st["dur_byte"]), int(st["dur_shift"]),
                         int(st["dur_bits"])],
            "volume": [int(st["vol_byte"]), int(st["vol_shift"]),
                       int(st["vol_bits"])],
            "_fields": "name: [byte within the record, lowest bit, width]",
        }
    return doc


def emit_path(explicit=None):
    """Where a saved reading goes, and whether the toolkit will find it there.

    Default is `formats/`, because a finding filed anywhere else is a file
    nobody opens again. Landing there means `trackeredit` and the workbench
    pick it up for this cartridge automatically the next time either runs --
    which is the point, and also the risk, so the name carries `-guessed` and
    the file carries `guessed: true`.
    """
    if explicit:
        return os.path.abspath(explicit)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    formats = os.path.join(root, "formats")
    stem = os.path.splitext(os.path.basename(ROM))[0]
    stem = "".join(c if (c.isalnum() or c in "-_") else "-" for c in stem)
    stem = "-".join(x for x in stem.split("-") if x).lower()
    return os.path.join(formats, "%s-guessed.json" % stem)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        if ctype == "application/json":
            body = json.dumps(body).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        p = self.path.split("?")[0]
        try:
            if p == "/":
                return self._send(200, PAGE, "text/html; charset=utf-8")
            if p == "/api/state":
                return self._send(200, view())
            if p == "/api/wav":
                song = to_song(STATE, decode(STATE))
                import tempfile
                tmp = os.path.join(tempfile.gettempdir(), "_explore.wav")
                tracker.render(song, tmp)
                data = open(tmp, "rb").read()
                try:
                    os.remove(tmp)
                except OSError:
                    pass
                return self._send(200, data, "audio/wav")
        except Exception as e:                                # noqa: BLE001
            return self._send(500, {"error": "%s: %s" % (type(e).__name__, e)})
        self._send(404, {"error": "no such thing"})

    def do_POST(self):
        global STATE
        n = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return self._send(400, {"error": "bad JSON"})
        try:
            if self.path == "/api/set":
                for k, v in body.items():
                    if k in STATE:
                        STATE[k] = v
                return self._send(200, view())
            if self.path == "/api/suggest":
                out = []
                for score, why, st in suggest(STATE["space"], STATE["addr"],
                                              STATE["chip"]):
                    out.append({"score": score, "why": why, "state": st})
                return self._send(200, {"suggestions": out})
            if self.path == "/api/emit":
                path = emit_path(body.get("path"))
                d = os.path.dirname(path)
                if d and not os.path.isdir(d):
                    os.makedirs(d)
                doc = emit_format(STATE)
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(doc, f, indent=2)
                    f.write("\n")
                by = ("the player fingerprint %s, so every cartridge built "
                      "on this engine finds it" % doc["match"]["player"]
                      if "player" in doc["match"]
                      else "this cartridge's title and size")
                active = os.path.basename(os.path.dirname(path)) == "formats"
                return self._send(200, {"path": path, "matched_by": by,
                                        "active": active})
        except Exception as e:                                # noqa: BLE001
            return self._send(500, {"error": "%s: %s" % (type(e).__name__, e)})
        self._send(404, {"error": "no such thing"})


def view():
    notes = decode(STATE)
    raw = []
    sp = STATE["space"]
    start = STATE["pitch_at"] if STATE["shape"] == "parallel" else STATE["addr"]
    for i in range(64):
        try:
            raw.append(CART.byte(sp, start + i))
        except Exception:                                    # noqa: BLE001
            raw.append(0)
    return {
        "rom": os.path.basename(ROM),
        "spaces": CART.spaces(),
        "state": STATE,
        "notes": notes[:64],
        "raw": raw,
        "score": musicality(notes),
        "seconds": round(len(to_song(STATE, notes)) / (STATE.get("rate") or 60.0), 1),
    }


PAGE = r"""<!doctype html>
<meta charset="utf-8"><title>format explorer</title>
<style>
 :root{--bg:#15151b;--fg:#e6e6ee;--dim:#8b8b9c;--line:#2c2c38;--accent:#d8a657}
 *{box-sizing:border-box}
 body{margin:0;background:var(--bg);color:var(--fg);
      font:13px/1.6 ui-monospace,SFMono-Regular,Consolas,monospace}
 header{padding:10px 16px;border-bottom:1px solid var(--line);
        display:flex;gap:14px;align-items:center;flex-wrap:wrap}
 h1{font-size:14px;margin:0;font-weight:600}
 h2{font-size:11px;margin:14px 0 4px;color:var(--accent);font-weight:600;
    text-transform:uppercase;letter-spacing:.08em}
 .muted{color:var(--dim)}
 main{display:flex;gap:20px;padding:12px 16px;align-items:flex-start}
 .col{min-width:280px}
 label{color:var(--dim);display:inline-block}
 input,select{background:#0f0f14;color:var(--fg);border:1px solid var(--line);
   border-radius:3px;padding:2px 5px;font:inherit;width:70px}
 button{background:#22222c;color:var(--fg);border:1px solid var(--line);
        border-radius:3px;padding:4px 11px;font:inherit;cursor:pointer}
 button:hover{border-color:var(--accent)}
 button.go{border-color:var(--accent);color:var(--accent)}
 .row{display:flex;gap:8px;align-items:center;margin:3px 0;flex-wrap:wrap}
 table{border-collapse:collapse}
 td,th{padding:1px 8px 1px 0;text-align:left}
 th{color:var(--dim);font-weight:500}
 .hex{color:var(--dim);word-break:break-all;max-width:420px}
 .sug{border:1px solid var(--line);border-radius:3px;padding:4px 8px;
      margin:3px 0;cursor:pointer}
 .sug:hover{border-color:var(--accent)}
 .bar{display:inline-block;height:8px;background:var(--accent);
      border-radius:2px;vertical-align:middle}
 #msg{min-height:18px}
</style>
<header>
  <h1>format explorer</h1><span class="muted" id="what"></span>
  <span style="flex:1"></span>
  <button class="go" id="play">Play</button>
  <button onclick="doSuggest()">Suggest readings</button>
  <button onclick="emit()">Save as format file</button>
</header>
<main>
  <div class="col">
    <h2>where</h2>
    <div class="row"><label>space</label><select id="space"></select>
      <label>address</label><input id="addr"><label>notes</label><input id="count"></div>
    <h2>shape</h2>
    <div class="row">
      <label><input type="radio" name="shape" value="serial" style="width:auto"> serial</label>
      <label><input type="radio" name="shape" value="parallel" style="width:auto"> parallel</label>
    </div>
    <div id="serialbox">
      <div class="row"><label>bytes a note</label><input id="stride"></div>
      <div class="row"><label>pitch: byte</label><input id="pitch_byte">
        <label>shift</label><input id="pitch_shift"><label>bits</label><input id="pitch_bits"></div>
      <div class="row"><label>dur: byte</label><input id="dur_byte">
        <label>shift</label><input id="dur_shift"><label>bits</label><input id="dur_bits"></div>
      <div class="row"><label>vol: byte</label><input id="vol_byte">
        <label>shift</label><input id="vol_shift"><label>bits</label><input id="vol_bits"></div>
    </div>
    <div id="parbox">
      <div class="row"><label>pitches at</label><input id="pitch_at">
        <label>durations at</label><input id="dur_at"></div>
    </div>
    <h2>playback</h2>
    <div class="row"><label>chip</label><select id="chip">
        <option value="tia">TIA</option><option value="pokey">POKEY</option></select>
      <label>waveform</label><input id="audc"><label>volume</label><input id="volume"></div>
    <div class="row"><label>fixed length</label><input id="fixed_duration">
      <label>x duration</label><input id="dur_scale"><label>rows/s</label><input id="rate"></div>
    <div id="msg" class="muted"></div>
    <h2>bytes</h2>
    <div class="hex" id="hex"></div>
  </div>
  <div class="col">
    <h2>what that reads as</h2>
    <div id="score" class="muted"></div>
    <table id="notes"></table>
  </div>
  <div class="col" id="sugcol"></div>
</main>
<audio id="au" style="display:none"></audio>
<script>
const $=i=>document.getElementById(i);
let S=null;
const FIELDS=['addr','count','stride','pitch_byte','pitch_shift','pitch_bits',
  'dur_byte','dur_shift','dur_bits','vol_byte','vol_shift','vol_bits',
  'pitch_at','dur_at','audc','volume','fixed_duration','dur_scale','rate'];

async function load(){ S=await (await fetch('/api/state')).json(); draw(); }
function hex(n){ return '$'+n.toString(16).toUpperCase(); }

function draw(){
  const st=S.state;
  $('what').textContent=S.rom+'  '+st.space+':'+hex(st.addr)+
    '   '+S.seconds+'s';
  const sp=$('space'); sp.innerHTML='';
  for(const s of S.spaces){
    const o=document.createElement('option'); o.value=s; o.textContent=s;
    if(s===st.space) o.selected=true; sp.appendChild(o);
  }
  for(const f of FIELDS){
    const el=$(f); if(!el) continue;
    el.value=(f==='addr'||f==='pitch_at'||f==='dur_at')
      ? hex(st[f]) : st[f];
  }
  $('chip').value=st.chip;
  for(const r of document.querySelectorAll('input[name=shape]'))
    r.checked = (r.value===st.shape);
  $('serialbox').style.display = st.shape==='serial' ? '' : 'none';
  $('parbox').style.display = st.shape==='parallel' ? '' : 'none';
  $('hex').textContent=S.raw.map(b=>b.toString(16).padStart(2,'0')).join(' ');
  $('score').innerHTML='plausibility '+S.score+
    ' <span class="bar" style="width:'+Math.round(S.score*40)+'px"></span>'+
    ' <span class="muted">&mdash; a sort order, not a verdict. Use your ears.</span>';
  let h='<tr><th>#</th><th>pitch</th><th>dur</th><th>vol</th><th>bytes</th></tr>';
  S.notes.slice(0,32).forEach((n,i)=>{
    h+='<tr><td class="muted">'+i+'</td><td>'+n.pitch+'</td><td>'+n.duration+
       '</td><td>'+(n.volume===null?'-':n.volume)+'</td><td class="muted">'+
       (n.raw?n.raw.map(b=>b.toString(16).padStart(2,'0')).join(' '):'')+
       '</td></tr>';
  });
  $('notes').innerHTML=h;
}

async function set(patch){
  S=await (await fetch('/api/set',{method:'POST',body:JSON.stringify(patch)})).json();
  draw();
}
for(const f of FIELDS){
  const el=$(f); if(!el) continue;
  el.onchange=()=>{
    let v=el.value.trim();
    v=(v.startsWith('$')||v.startsWith('0x'))?parseInt(v.replace('$','0x'),16):parseInt(v,10);
    if(isNaN(v)) return;
    const p={}; p[f]=v; set(p);
  };
}
$('space').onchange=()=>set({space:$('space').value});
$('chip').onchange=()=>set({chip:$('chip').value});
for(const r of document.querySelectorAll('input[name=shape]'))
  r.onchange=()=>set({shape:r.value});

$('play').onclick=async()=>{
  $('msg').textContent='rendering…';
  const a=$('au'); a.src='/api/wav?'+Date.now();
  a.play().then(()=>$('msg').textContent='')
          .catch(e=>$('msg').textContent=e.message);
};
async function doSuggest(){
  $('sugcol').innerHTML='<h2>readings</h2><div class="muted">scoring…</div>';
  const j=await (await fetch('/api/suggest',{method:'POST',body:'{}'})).json();
  let h='<h2>readings, likeliest first</h2>'+
    '<div class="muted">Click one, then press Play. These are guesses at the '+
    '<em>layout</em>, and they assume you are pointed at music &mdash; the '+
    'ranking cannot tell a tune from graphics or code, which are structured '+
    'too. A high score on silence means the bytes are tidy, not musical. '+
    'Your ear decides; the number only sorts the list.</div>';
  for(const s of j.suggestions)
    h+='<div class="sug" onclick=\'apply('+JSON.stringify(s.state)+')\'>'+
       s.score.toFixed(2)+'  '+s.why+'</div>';
  $('sugcol').innerHTML=h;
}
async function apply(st){ S=await (await fetch('/api/set',{method:'POST',
  body:JSON.stringify(st)})).json(); draw(); $('msg').textContent='loaded — press Play'; }
async function emit(){
  const j=await (await fetch('/api/emit',{method:'POST',body:'{}'})).json();
  $('msg').textContent = j.error ? j.error : ('wrote '+j.path);
}
load();
</script>
"""


def main():
    global CART, ROM, STATE
    ap = argparse.ArgumentParser(
        description=__doc__.strip().split("\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("rom")
    ap.add_argument("--at", help="where to start, as space:hex (e.g. b6:A951)")
    ap.add_argument("--chip", choices=["tia", "pokey"])
    ap.add_argument("--port", type=int, default=8125)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--emit", nargs="?", const="", metavar="PATH",
                    help="write the format file for the current reading and "
                         "exit, without opening the browser. With no path it "
                         "goes to formats/, where the toolkit will find it.")
    args = ap.parse_args()

    try:
        CART = cart_module.Cart(args.rom)
    except (cart_module.UnknownMapper, cart_module.UnknownSpace, IOError) as e:
        sys.stderr.write("%s\n" % e)
        return 2
    ROM = os.path.abspath(args.rom)
    chip = args.chip or ("pokey" if CART.pokeys() else "tia")

    space = addr = None
    from_scan = False
    if args.at:
        sp, _, a = args.at.partition(":")
        space, addr = sp, int(a.lstrip("$"), 16)
    else:
        # No address given: ask audiotrace where the music is.
        try:
            import audiotrace
            an = audiotrace.analyse(ROM, None)
            for g in audiotrace.cluster(audiotrace.find_writers(an, an.cart)):
                for t in audiotrace.tables_in(g):
                    space, addr = g["space"], t["addr"]
                    from_scan = True
                    break
                if space:
                    break
        except Exception:                                    # noqa: BLE001
            pass
    if space is None:
        space = CART.spaces()[0]
        addr = CART.base_of(space)
        print("No audio table found to start from -- opening at %s:$%04X. "
              "Use --at." % (space, addr))
    elif from_scan:
        print("starting at %s:$%04X, where audiotrace found an audio table"
              % (space, addr))
    else:
        print("starting at %s:$%04X, as asked" % (space, addr))

    STATE = default_state(space, addr)
    STATE["chip"] = chip
    if args.emit is not None:
        # Scripted use: take the best-ranked reading of this address and write
        # it out. Nobody has heard it, so it is a starting point rather than a
        # finding, and both the file and this message say so.
        ranked = suggest(space, addr, chip)
        if ranked and ranked[0][0] > 0:
            STATE = ranked[0][2]
        path = emit_path(args.emit or None)
        d = os.path.dirname(path)
        if d and not os.path.isdir(d):
            os.makedirs(d)
        with io.open(path, "w", encoding="utf-8") as f:
            print(json.dumps(emit_format(STATE), indent=2), file=f)
        print("wrote %s" % path)
        if ranked:
            print("  reading: %s" % ranked[0][1])
        print("  Nothing has listened to this. Open it without --emit, press "
              "Play, and adjust before trusting it.")
        return 0
    srv = HTTPServer(("127.0.0.1", args.port), Handler)
    url = "http://127.0.0.1:%d/" % args.port
    print("%s -- %s, %s" % (os.path.basename(ROM), CART.map.name, chip.upper()))
    print("open %s" % url)
    if not args.no_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("")
    return 0


if __name__ == "__main__":
    sys.exit(main())
