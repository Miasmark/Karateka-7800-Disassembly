#!/usr/bin/env python3
"""
A tracker you can actually type in: a grid in the browser, backed by the same
renderer that exports.

    python trackeredit.py song.trk
    python trackeredit.py game.a78        (reads its songs, or records it)

It serves a local page -- nothing leaves the machine -- showing the song as a
grid of rows and channels. Click a cell and type; arrow keys move; Enter
commits and steps down, the way a tracker does. Play any part of it, and save
back to the same `.trk`.

Handed a cartridge, it tries the ROM first. If a file in `formats/` says it
describes this game, the songs are **read out of the ROM** -- every one of them,
in a moment, with no emulator. Recording is the fallback, not the default,
because a recording only ever catches whichever song happened to play in the
window you gave it.

With no format file it says what `audiotrace` can still see, so you know where
the music is even when nothing can decode it yet, and then records.

Playback is rendered on the server by `tracker.render`, not re-synthesised in
JavaScript. That is the whole point: what you hear is the file, run through the
exact code path that writes the WAV and the 6502 tables, so there is no second
sound model to drift out of step with the first.
"""
import argparse
import io
import json
import os
import sys
import tempfile
import threading
import webbrowser
import wave
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tracker

SONG = None
PATH = None
DIRTY = False


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


# --------------------------------------------------------------- the payload
def sounding_rows():
    """The first and last row at which anything is actually audible.

    A capture usually opens on a stretch of silence -- the game sitting on its
    title screen -- and often ends on one too. Hunting for the music by ear,
    a few rows at a time, is miserable, so the song says where it is.
    """
    s = SONG
    first = last = None
    live = [(0, 0, 0)] * s.nch
    for i, cells in enumerate(s.rows):
        for c in range(s.nch):
            if cells[c] is not None:
                live[c] = cells[c]
        if any(v for _a, _f, v in live):
            if first is None:
                first = i
            last = i
    return first, last


def song_json():
    s = SONG
    rows = []
    for i, cells in enumerate(s.rows):
        # AUDCTL is per chip, and a cartridge may carry two POKEYs. A channel
        # must be formatted with the AUDCTL of the chip it lives on and with
        # its index *within* that chip, or channels 5-8 are read against the
        # first chip's clock select.
        rows.append({
            "audctl": list(s.all_ctls(i)),
            "cells": [None if c is None else list(c) for c in cells],
            "text": [tracker.format_cell(c, s.region, True, s.chip, n % 4,
                                         s.ctl_of(i, n))
                     for n, c in enumerate(cells)],
        })
    first, last = sounding_rows()
    return {
        "title": s.title, "chip": s.chip, "region": s.region, "rate": s.rate,
        "nch": s.nch, "rows": rows, "path": PATH, "dirty": DIRTY,
        "first_sound": first, "last_sound": last,
        "unsupported": [{"what": w, "audctl": c} for w, c in s.unsupported],
        "warnings": s.warnings,
        "distortions": ({str(d): tracker.POKEY_DISTORTION[d][0] for d in range(8)}
                        if tracker.is_pokey(s.chip) else
                        {("%X" % a): tracker.AUDC_NAME[a] for a in range(16)}),
    }


def set_cell(row, ch, text):
    """Parse one cell of text into the song. Raises ValueError with a reason."""
    global DIRTY
    s = SONG
    ctl = s.ctl_of(row, ch)
    warn = []
    cell = tracker.parse_cell(text, s.region, warn.append, s.chip, ch % 4, ctl)
    cells = list(s.rows[row])
    cells[ch] = cell
    s.rows[row] = tuple(cells)
    DIRTY = True
    return {"text": tracker.format_cell(cell, s.region, True, s.chip, ch, ctl),
            "cell": None if cell is None else list(cell),
            "warnings": warn}


def render_range(lo, hi):
    """A WAV of rows [lo, hi).

    Rendered from a copy that starts at the *resolved* state of row lo, so a
    section beginning on a run of holds still plays what was sounding rather
    than silence.
    """
    s = SONG
    lo = max(0, min(lo, len(s.rows)))
    hi = max(lo + 1, min(hi, len(s.rows)))
    part = tracker.Song(s.title, s.region, s.rate, s.chip)
    state = [(0, 0, 0)] * s.nch
    for i in range(lo):
        for c in range(s.nch):
            if s.rows[i][c] is not None:
                state[c] = s.rows[i][c]
    part.add(list(state), audctl=s.all_ctls(lo))
    for i in range(lo + 1, hi):
        part.add(list(s.rows[i]), audctl=s.all_ctls(i))
    buf = io.BytesIO()
    tmp = os.path.join(os.path.dirname(os.path.abspath(PATH or ".")),
                       "._trackeredit_preview.wav")
    tracker.render(part, tmp)
    with open(tmp, "rb") as f:
        data = f.read()
    try:
        os.remove(tmp)
    except OSError:
        pass
    return data


# ---------------------------------------------------------------- the server
FORMATS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "formats")


def find_format(rom):
    """A shipped format file that says it describes this cartridge, or None.

    Matching is deliberately strict -- title substring *and* exact ROM size --
    because borrowing another game's format description does not fail loudly.
    It produces a song, from the wrong bytes, that looks entirely plausible.
    """
    import cart as cartlib
    try:
        c = cartlib.Cart(rom)
    except Exception:                                        # noqa: BLE001
        return None
    title = ((c.info or {}).get("title") or "").lower()
    size = len(c.rom)
    sig = None
    guesses = []
    if not os.path.isdir(FORMATS):
        return None
    for name in sorted(os.listdir(FORMATS)):
        if not name.endswith(".json"):
            continue
        path = os.path.join(FORMATS, name)
        try:
            doc = json.load(io.open(path, encoding="utf-8"))
        except ValueError:
            continue
        m = doc.get("match")
        if not m:
            continue
        if "magic" in m:
            # A format that describes a self-identifying container -- an RMT
            # module, say -- is found by its own marker, not by whose player
            # wraps it. The same module turns up behind several player builds.
            want = m["magic"].encode("latin1")
            if not any(want in bytes(c.slice(sp, c.base_of(sp), c.size_of(sp)))
                       for sp in c.spaces()):
                continue
        if "player" in m:
            if sig is None:
                import audiotrace
                sig = audiotrace.player_signature(c.rom) or ""
            if m["player"] != sig:
                continue
        want = str(m.get("title", "")).lower()
        if want and want not in title:
            continue
        if "size" in m and int(m["size"]) != size:
            continue
        if doc.get("guessed"):
            # A reading worked out by ear. It is worth having and worth
            # trying, but it must never shadow a description written from
            # the player's own code, however the filenames happen to sort.
            guesses.append(path)
            continue
        return path
    return guesses[0] if guesses else None


def songs_from_rom(rom, fmt_path):
    """Pull every song out of a cartridge and render each to a tracker file.

    This is the route that needs no emulator: the notes are in the ROM, and a
    format file says what the bytes mean. `capture` records what a game happens
    to play in the window you gave it; this reads what it *can* play, all of it,
    in seconds rather than a minute of emulation.
    """
    import cart as cartlib
    import songfmt
    cart = cartlib.Cart(rom)
    fmt = json.load(io.open(fmt_path, encoding="utf-8"))
    pulled = songfmt.pull(cart, fmt)
    out = []

    if pulled.get("reader") == "rmt":
        # Identified, described, and honestly not playable from the ROM: RMT's
        # sound lives in its instrument engine, not in its note list.
        raise ValueError(
            "%s at %s -- %d channels, %d tracks. RMT modules are not rendered "
            "from the ROM: %s"
            % (pulled["magic"], pulled["module_at"], pulled["channels"],
               pulled["tracks"], pulled["why_not"]))

    if pulled.get("reader") == "direct":
        # A reading someone worked out by ear rather than a player they read.
        # It plays, which is the point -- a finding you cannot hear is a
        # finding you cannot check.
        tracks = songfmt.render_direct(pulled)
        length = max([len(t) for t in tracks] or [0])
        if not length:
            return [], pulled
        song = tracker.Song(title=pulled.get("format", "?"),
                            chip=pulled.get("chip", "tia"))
        nch = song.nch
        for i in range(length):
            cells = []
            for ch in range(nch):
                cells.append(tracks[ch][i]
                             if ch < len(tracks) and i < len(tracks[ch])
                             else (0, 0, 0))
            song.add(cells)
        sounding = sum(1 for row in song.states()
                       if any(v for _a, _f, v in row))
        return ([{"n": 0, "rows": len(song), "sounding": sounding,
                  "seconds": round(len(song) / song.rate, 1), "song": song}],
                pulled)

    if pulled.get("reader") == "parallel":
        # A flat song per cartridge rather than a numbered set: this player
        # holds one tune, four voices, and no song table to index.
        tracks = songfmt.render_parallel(pulled)
        length = max([len(t) for t in tracks] or [0])
        if not length:
            return [], pulled
        song = tracker.Song(title=pulled.get("format", "?"),
                            chip=pulled.get("chip", "pokey"))
        nch = song.nch
        last = [None] * nch
        for i in range(length):
            cells = []
            for ch in range(nch):
                cur = tracks[ch][i] if ch < len(tracks) and i < len(tracks[ch])                     else (0, 0, 0)
                cells.append(None if cur == last[ch] else cur)
                last[ch] = cur
            song.add(cells)
        sounding = sum(1 for row in song.states()
                       if any(v for _a, _f, v in row))
        return ([{"n": 0, "rows": len(song), "sounding": sounding,
                  "seconds": round(len(song) / song.rate, 1), "song": song}],
                pulled)

    for entry in pulled["songs"]:
        n = entry["n"]
        try:
            tracks, audctl = songfmt.render(pulled, n)
        except songfmt.FormatError as e:
            out.append({"n": n, "error": str(e), "rows": 0})
            continue
        length = max([len(t) for t in tracks] or [0])
        if not length:
            continue
        song = tracker.Song(title=(entry.get("title")
                                   or "%s song %d"
                                   % (pulled.get("format", "?"), n)),
                            chip=pulled.get("chip", "tia"))
        nch = song.nch
        last = [None] * nch
        for i in range(length):
            cells = []
            for ch in range(nch):
                cur = tracks[ch][i] if ch < len(tracks) and i < len(tracks[ch]) \
                    else (0, 0, 0)
                cells.append(None if cur == last[ch] else cur)
                last[ch] = cur
            song.add(cells, audctl=audctl)
        sounding = sum(1 for row in song.states() if any(v for _a, _f, v in row))
        out.append({"n": n, "rows": len(song), "sounding": sounding,
                    "seconds": round(len(song) / song.rate, 1), "song": song})
    return out, pulled


def trace_audio(rom):
    """What audiotrace can see, for a cartridge with no format file."""
    import audiotrace
    try:
        an = audiotrace.analyse(rom, None)
    except Exception:                                        # noqa: BLE001
        return None
    cart = an.cart
    tables = []
    for g in audiotrace.cluster(audiotrace.find_writers(an, cart)):
        for t in audiotrace.tables_in(g):
            tables.append("%s:$%04X -> %s"
                          % (g["space"], t["addr"], " ".join(sorted(t["regs"]))))
    return tables


MIDI_DOC = None            # the file currently open for import, if any


def midi_open(data_b64):
    """Take an uploaded .mid and summarise its tracks.

    The file arrives as base64 from a file input rather than as a path: the
    browser knows what you picked and the server does not, and asking someone
    to retype a path they just clicked is a poor trade.
    """
    global MIDI_DOC
    import base64
    import midi as midi_mod
    raw = base64.b64decode(data_b64)
    tmp = os.path.join(tempfile.gettempdir(), "a7800-tracker-import.mid")
    with open(tmp, "wb") as f:
        f.write(raw)
    MIDI_DOC = midi_mod.read(tmp)
    out = []
    for i, t in enumerate(MIDI_DOC["tracks"]):
        n = t["notes"]
        if not n:
            continue
        lo = min(x["note"] for x in n)
        hi = max(x["note"] for x in n)
        out.append({
            "index": i,
            "name": t["name"] or "(unnamed)",
            "notes": len(n),
            "poly": midi_mod.max_poly(n),
            "low": midi_mod.note_name(lo), "high": midi_mod.note_name(hi),
            "seconds": round(max(x["end"] for x in n), 2),
            "programs": t["programs"],
        })
    return {"path": MIDI_DOC["path"], "division": MIDI_DOC["division"],
            "tempo_changes": MIDI_DOC["tempo_changes"], "tracks": out}


def midi_apply(track, ch, pick, audc, volume, offset, grow):
    """Write one MIDI track into one voice of the song already open.

    Filling a single voice, not replacing the song: the point of importing a
    part is to put it beside what is already there. Other voices are untouched,
    and the rows this does not reach keep whatever they held.
    """
    global DIRTY
    if MIDI_DOC is None:
        raise ValueError("no MIDI file is open")
    s = SONG
    if not 0 <= track < len(MIDI_DOC["tracks"]):
        raise ValueError("no track %d in this file" % track)
    if not 0 <= ch < s.nch:
        raise ValueError("no voice %d on a %s song" % (ch + 1, s.chip))
    notes = MIDI_DOC["tracks"][track]["notes"]
    if not notes:
        raise ValueError("track %d has no notes" % track)

    need = int(max(n["end"] for n in notes) * s.rate) + 1 + offset
    if need > len(s.rows):
        if not grow:
            need = len(s.rows)
        else:
            blank = tuple([None] * s.nch)
            while len(s.rows) < need:
                s.rows.append(blank)
                s.audctl.append(s.audctl[-1] if s.audctl
                                else (0,) * max(1, s.nchips))

    lane, dropped = tracker.midi_voice(notes, max(0, need - offset), s.rate,
                                       pick)
    placed, cents = 0, []
    vol = max(0, min(15, int(volume)))
    for i, n in enumerate(lane):
        row = i + offset
        if row >= len(s.rows):
            break
        cells = list(s.rows[row])
        if n is None:
            cells[ch] = (0, 0, 0)
        else:
            if tracker.is_pokey(s.chip):
                f, err = tracker.pokey_best_audf(ch % 4, n["note"],
                                                 s.ctl_of(row, ch), s.region)
            else:
                f, err = tracker.best_audf(audc, n["note"], s.region)
            cells[ch] = (audc, f, vol)
            cents.append(abs(err))
            placed += 1
        s.rows[row] = tuple(cells)
    DIRTY = True

    med = worst = 0.0
    if cents:
        cents.sort()
        med, worst = cents[len(cents) // 2], cents[-1]
    return {"placed": placed, "dropped": dropped,
            "median_cents": round(med), "worst_cents": round(worst),
            "rows": len(s.rows), "chip": s.chip,
            "out_of_tune": sum(1 for c in cents if c > 25)}


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
        q = {}
        if "?" in self.path:
            for pair in self.path.split("?", 1)[1].split("&"):
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    q[k] = v
        try:
            if p == "/":
                return self._send(200, PAGE, "text/html; charset=utf-8")
            if p == "/api/song":
                return self._send(200, song_json())
            if p == "/api/wav":
                lo = int(q.get("from", 0))
                n = int(q.get("rows", 0)) or len(SONG.rows)
                return self._send(200, render_range(lo, lo + n), "audio/wav")
        except ValueError as e:
            return self._send(400, {"error": str(e)})
        except Exception as e:                      # noqa: BLE001
            return self._send(500, {"error": "%s: %s" % (type(e).__name__, e)})
        self._send(404, {"error": "no such thing"})

    def do_POST(self):
        global DIRTY
        n = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return self._send(400, {"error": "bad JSON"})
        try:
            if self.path == "/api/cell":
                r = set_cell(int(body["row"]), int(body["ch"]), body["text"])
                r["dirty"] = DIRTY
                return self._send(200, r)
            if self.path == "/api/meta":
                if "rate" in body:
                    SONG.rate = max(1.0, float(body["rate"]))
                if "title" in body:
                    SONG.title = str(body["title"])[:120]
                DIRTY = True
                return self._send(200, {"ok": True, "dirty": DIRTY,
                                        "rate": SONG.rate, "title": SONG.title})
            if self.path == "/api/midi/open":
                return self._send(200, midi_open(body["data"]))
            if self.path == "/api/midi/apply":
                r = midi_apply(int(body["track"]), int(body["ch"]),
                               body.get("pick", "high"),
                               int(body.get("audc", 12)),
                               int(body.get("volume", 8)),
                               int(body.get("offset", 0)),
                               bool(body.get("grow", True)))
                r["song"] = song_json()
                return self._send(200, r)
            if self.path == "/api/save":
                out = body.get("path") or PATH
                with open(out, "w", encoding="utf-8") as f:
                    f.write(tracker.dump(SONG))
                DIRTY = False
                return self._send(200, {"ok": True, "path": out,
                                        "rows": len(SONG), "dirty": False})
            if self.path == "/api/export":
                out = body.get("path") or (os.path.splitext(PATH)[0] + ".asm")
                with open(out, "w", encoding="utf-8") as f:
                    f.write(tracker.export_asm(SONG))
                return self._send(200, {"ok": True, "path": out})
        except (ValueError, OSError) as e:
            return self._send(400, {"error": str(e)})
        except Exception as e:                      # noqa: BLE001
            return self._send(500, {"error": "%s: %s" % (type(e).__name__, e)})
        self._send(404, {"error": "no such thing"})


PAGE = r"""<!doctype html><html><head><meta charset="utf-8">
<title>7800 tracker</title>
<style>
:root{--bg:#14110f;--panel:#1c1815;--raised:#24201c;--line:#332c26;
      --ink:#eae3d9;--dim:#a1968a;--faint:#6d6259;--accent:#e8843c;
      --accent-dim:#8a4e22;--ok:#7fb069;
      --mono:ui-monospace,"Cascadia Mono",Consolas,monospace}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:13px var(--mono);
     display:flex;flex-direction:column;height:100vh}
header{padding:8px 12px;background:var(--panel);border-bottom:1px solid var(--line);
       display:flex;gap:14px;align-items:center;flex:none;flex-wrap:wrap}
h1{font-size:14px;margin:0;font-weight:600}
h1 span{color:var(--faint);font-weight:400;margin-left:8px}
button{font:12px var(--mono);padding:5px 11px;border-radius:5px;
       border:1px solid var(--line);background:var(--raised);color:var(--ink);
       cursor:pointer}
button:hover{border-color:var(--faint)}
button.go{border-color:var(--accent-dim);color:var(--accent)}
.chip{padding:2px 8px;border:1px solid var(--accent-dim);color:var(--accent);
      border-radius:4px;font-size:11px}
.dirty{color:var(--accent)}
input.meta{background:var(--bg);color:var(--ink);border:1px solid var(--line);
           border-radius:4px;padding:3px 6px;font:12px var(--mono);width:64px}
#grid{flex:1;overflow:auto;padding:0 0 40vh 0}
table{border-collapse:collapse;font:13px var(--mono)}
th{position:sticky;top:0;background:var(--panel);color:var(--faint);
   font-weight:500;font-size:11px;text-align:left;padding:5px 10px;
   border-bottom:1px solid var(--line);z-index:2}
td{padding:0;border-right:1px solid var(--line)}
td.n{color:var(--faint);text-align:right;padding:1px 8px;font-size:11px;
     background:var(--panel);position:sticky;left:0}
td.n.beat{color:var(--dim)}
tr.playing td.n{background:var(--accent-dim);color:#fff}
.cell{padding:1px 8px;white-space:pre;cursor:text;min-width:104px}
.cell.hold{color:var(--faint)}
.cell.off{color:var(--faint);font-style:italic}
.cell.sel{background:var(--accent-dim);color:#fff;outline:1px solid var(--accent)}
.cell input{width:100%;background:#000;color:var(--accent);border:0;
            outline:1px solid var(--accent);font:13px var(--mono);padding:0 6px}
audio{flex:none;width:100%;height:32px;background:var(--panel);
      border-top:1px solid var(--line)}
footer{flex:none;background:var(--panel);border-top:1px solid var(--line);
       padding:6px 12px;color:var(--dim);font-size:11px;min-height:26px}
footer b{color:var(--accent)}
footer.bad{color:#e06c6c}
.legend{color:var(--faint);font-size:11px}
</style></head><body>
<header>
  <h1>7800 tracker<span id="file"></span></h1>
  <span class="chip" id="chip"></span>
  <label class="legend">rate <input class="meta" id="rate"></label>
  <label class="legend">play <input class="meta" id="secs" value="10">s</label>
  <button class="go" id="play">Play from row</button>
  <button id="playall">Play all</button>
  <button id="stop">Stop</button>
  <button id="first">Go to first sound</button>
  <button id="save">Save</button>
  <button id="export">Export .asm</button>
  <button onclick="document.getElementById('midifile').click()">Import MIDI</button>
  <input type="file" id="midifile" accept=".mid,.midi" style="display:none">
  <span id="state" class="legend"></span>
</header>
<div id="midibox"></div>
<div id="grid"></div>
<audio id="au" controls></audio>
<footer id="msg"></footer>
<style>
 #midibox{display:none;padding:10px 14px;border-bottom:1px solid #2c2c38;
          background:#101016}
 #midibox table.mt{border-collapse:collapse;margin:8px 0}
 #midibox .mt th{text-align:left;color:#8b8b9c;font-weight:500;
                 padding:2px 14px 2px 0}
 #midibox .mt td{padding:3px 14px 3px 0;border-top:1px solid #20202a}
 #midibox .mt td.warn{color:#e0a458;font-weight:600}
 #midibox .mrow{display:flex;gap:14px;align-items:center;flex-wrap:wrap}
 #midibox label{color:#8b8b9c}
 #midibox input,#midibox select{background:#0f0f14;color:#e6e6ee;
   border:1px solid #2c2c38;border-radius:3px;padding:2px 5px;font:inherit}
</style>
<script>
const $ = s => document.querySelector(s);
let song = null, sel = {row: 0, ch: 0}, editing = false;

const api = async (p, body) => {
  const r = await fetch(p, body ? {method: 'POST',
      headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)} : {});
  const j = await r.json();
  if (!r.ok) throw new Error(j.error || ('HTTP ' + r.status));
  return j;
};

function say(t, bad) {
  const m = $('#msg');
  m.className = bad ? 'bad' : '';
  m.innerHTML = t;
}

function cellClass(text) {
  if (text === '...') return 'cell hold';
  if (text === 'off') return 'cell off';
  return 'cell';
}

function draw() {
  const t = document.createElement('table');
  const hd = document.createElement('tr');
  hd.innerHTML = '<th></th>' +
    Array.from({length: song.nch}, (_, i) => `<th>ch${i + 1}</th>`).join('') +
    (song.chip === 'pokey' ? '<th>audctl</th>' : '');
  t.appendChild(hd);
  song.rows.forEach((row, i) => {
    const tr = document.createElement('tr');
    tr.dataset.row = i;
    const beat = (i % 4 === 0) ? ' beat' : '';
    let h = `<td class="n${beat}">${i}</td>`;
    row.text.forEach((tx, c) => {
      h += `<td><div class="${cellClass(tx)}" data-row="${i}" data-ch="${c}">${tx}</div></td>`;
    });
    if (song.chip === 'pokey')
      h += `<td><div class="cell hold">$${row.audctl.toString(16).toUpperCase().padStart(2,'0')}</div></td>`;
    tr.innerHTML = h;
    t.appendChild(tr);
  });
  const g = $('#grid');
  g.innerHTML = '';
  g.appendChild(t);
  mark();
}

function cellAt(r, c) {
  return document.querySelector(`.cell[data-row="${r}"][data-ch="${c}"]`);
}

function mark() {
  document.querySelectorAll('.cell.sel').forEach(e => e.classList.remove('sel'));
  const e = cellAt(sel.row, sel.ch);
  if (e) {
    e.classList.add('sel');
    const r = e.getBoundingClientRect(), g = $('#grid').getBoundingClientRect();
    if (r.top < g.top + 30 || r.bottom > g.bottom - 30)
      e.scrollIntoView({block: 'center'});
  }
  $('#state').textContent = `row ${sel.row} of ${song.rows.length}` +
    (song.dirty ? ' — unsaved' : '');
  $('#state').className = song.dirty ? 'legend dirty' : 'legend';
}

function move(dr, dc) {
  sel.row = Math.max(0, Math.min(song.rows.length - 1, sel.row + dr));
  sel.ch = Math.max(0, Math.min(song.nch - 1, sel.ch + dc));
  mark();
}

function beginEdit(preset) {
  const e = cellAt(sel.row, sel.ch);
  if (!e || editing) return;
  editing = true;
  const was = e.textContent;
  const inp = document.createElement('input');
  inp.value = preset !== undefined ? preset : (was === '...' ? '' : was);
  e.textContent = '';
  e.appendChild(inp);
  inp.focus();
  inp.select();
  const finish = async (commit, dr) => {
    if (!editing) return;
    editing = false;
    const text = inp.value;
    e.textContent = was;
    e.className = cellClass(was);
    if (commit) {
      try {
        const r = await api('/api/cell', {row: sel.row, ch: sel.ch, text: text});
        e.textContent = r.text;
        e.className = cellClass(r.text);
        song.rows[sel.row].text[sel.ch] = r.text;
        song.dirty = r.dirty;
        say(r.warnings.length ? '<b>out of tune:</b> ' + r.warnings.join('; ')
                              : 'row ' + sel.row + ' updated', false);
      } catch (err) { say(err.message, true); }
    }
    if (dr) move(dr, 0); else mark();
  };
  inp.onkeydown = ev => {
    ev.stopPropagation();
    if (ev.key === 'Enter') { ev.preventDefault(); finish(true, 1); }
    else if (ev.key === 'Tab') { ev.preventDefault(); finish(true, 0); move(0, 1); }
    else if (ev.key === 'Escape') { ev.preventDefault(); finish(false, 0); }
  };
  inp.onblur = () => finish(true, 0);
}

document.addEventListener('keydown', ev => {
  if (editing || !song) return;
  const k = ev.key;
  if (k === 'ArrowDown') { move(1, 0); ev.preventDefault(); }
  else if (k === 'ArrowUp') { move(-1, 0); ev.preventDefault(); }
  else if (k === 'ArrowRight') { move(0, 1); ev.preventDefault(); }
  else if (k === 'ArrowLeft') { move(0, -1); ev.preventDefault(); }
  else if (k === 'PageDown') { move(16, 0); ev.preventDefault(); }
  else if (k === 'PageUp') { move(-16, 0); ev.preventDefault(); }
  else if (k === 'Enter') { beginEdit(); ev.preventDefault(); }
  else if (k === 'Delete') { quickSet('off'); ev.preventDefault(); }
  else if (k === 'Backspace') { quickSet('...'); ev.preventDefault(); }
  else if (k.length === 1 && /[0-9A-Fa-f.]/.test(k)) { beginEdit(k); ev.preventDefault(); }
});

async function quickSet(text) {
  try {
    const r = await api('/api/cell', {row: sel.row, ch: sel.ch, text: text});
    const e = cellAt(sel.row, sel.ch);
    e.textContent = r.text; e.className = cellClass(r.text) + ' sel';
    song.rows[sel.row].text[sel.ch] = r.text;
    song.dirty = r.dirty;
    move(1, 0);
  } catch (err) { say(err.message, true); }
}

$('#grid').addEventListener('mousedown', ev => {
  const c = ev.target.closest('.cell');
  if (!c || !c.dataset.ch) return;
  sel = {row: +c.dataset.row, ch: +c.dataset.ch};
  mark();
});
$('#grid').addEventListener('dblclick', ev => {
  if (ev.target.closest('.cell')) beginEdit();
});

// Fetch the audio before handing it to the element. Pointing <audio> straight
// at the endpoint means a long render looks like nothing happening at all --
// no error, no progress, just silence until it arrives. This way the wait is
// visible and a failure says why.
let blobUrl = null, playFrom = 0;

async function play(from, rows) {
  const au = $('#au');
  au.pause();
  const secs = Math.max(1, parseFloat($('#secs').value) || 10);
  const want = rows === 0 ? song.rows.length - from
                          : Math.min(rows, song.rows.length - from);
  const dur = want / song.rate;
  say(`rendering ${dur.toFixed(1)}s from row ${from}...`, false);
  try {
    const r = await fetch(`/api/wav?from=${from}&rows=${want}&t=${Date.now()}`);
    if (!r.ok) {
      let m = 'HTTP ' + r.status;
      try { m = (await r.json()).error || m; } catch (e) {}
      throw new Error(m);
    }
    const b = await r.blob();
    if (blobUrl) URL.revokeObjectURL(blobUrl);
    blobUrl = URL.createObjectURL(b);
    playFrom = from;
    au.src = blobUrl;
    await au.play();
    say(`playing rows ${from}-${from + want - 1} (${dur.toFixed(1)}s)`, false);
  } catch (e) {
    say('could not play: ' + e.message, true);
  }
}

// follow the cursor down the grid while it plays
$('#au').ontimeupdate = () => {
  if (!song) return;
  const r = playFrom + Math.floor($('#au').currentTime * song.rate);
  document.querySelectorAll('tr.playing').forEach(t => t.classList.remove('playing'));
  const tr = document.querySelector(`tr[data-row="${r}"]`);
  if (tr) {
    tr.classList.add('playing');
    const b = tr.getBoundingClientRect(), g = $('#grid').getBoundingClientRect();
    if (b.top < g.top || b.bottom > g.bottom) tr.scrollIntoView({block: 'center'});
  }
};
$('#au').onended = () =>
  document.querySelectorAll('tr.playing').forEach(t => t.classList.remove('playing'));

$('#play').onclick = () =>
  play(sel.row, Math.round((parseFloat($('#secs').value) || 10) * song.rate));
$('#playall').onclick = () => play(0, 0);
$('#stop').onclick = () => {
  const a = $('#au');
  a.pause(); a.currentTime = 0;
  document.querySelectorAll('tr.playing').forEach(t => t.classList.remove('playing'));
};
$('#first').onclick = () => {
  if (song.first_sound === null) { say('nothing in this song makes a sound', true); return; }
  sel = {row: song.first_sound, ch: 0};
  mark();
  cellAt(sel.row, 0).scrollIntoView({block: 'center'});
  play(sel.row, Math.round((parseFloat($('#secs').value) || 10) * song.rate));
};
$('#save').onclick = async () => {
  try { const r = await api('/api/save', {}); song.dirty = false; mark();
        say(`saved ${r.rows} rows to <b>${r.path}</b>`, false); }
  catch (e) { say(e.message, true); }
};
$('#export').onclick = async () => {
  try { const r = await api('/api/export', {});
        say(`wrote <b>${r.path}</b>`, false); }
  catch (e) { say(e.message, true); }
};
$('#rate').onchange = async e => {
  try { const r = await api('/api/meta', {rate: parseFloat(e.target.value)});
        song.rate = r.rate; song.dirty = true; mark();
        say('rate is now ' + r.rate + ' rows a second', false); }
  catch (err) { say(err.message, true); }
};

const esc = t => String(t).replace(/[<>&]/g,
  c => ({'<':'&lt;','>':'&gt;','&':'&amp;'}[c]));

/* --- MIDI import ---------------------------------------------------------
   Fills one voice from one track, leaving the rest of the song alone. That is
   the useful shape: a part you already have plus a part you are bringing in.
   Both losses -- polyphony and tuning -- are reported after every import,
   because on the TIA especially they are large and easy not to hear. */
let midiDoc = null;

$('#midifile').onchange = async e => {
  const f = e.target.files[0];
  if (!f) return;
  say('reading ' + f.name + '…', false);
  const buf = await f.arrayBuffer();
  let bin = '';
  const bytes = new Uint8Array(buf);
  for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
  try {
    midiDoc = await api('/api/midi/open', {data: btoa(bin)});
    drawMidi();
    say('', false);
  } catch (err) { say(err.message, true); }
};

function drawMidi() {
  const box = $('#midibox');
  if (!midiDoc || !midiDoc.tracks.length) {
    box.innerHTML = '<div class="muted">nothing importable in that file</div>';
    box.style.display = 'block';
    return;
  }
  let h = '<div class="muted">' + midiDoc.path + ' — ' +
          midiDoc.tempo_changes + ' tempo change(s). ' +
          'Pick a track, pick a voice. <b>poly</b> above 1 means the track ' +
          'plays chords and a voice cannot: the extra notes are dropped.</div>';
  h += '<table class="mt"><tr><th>track</th><th>notes</th><th>poly</th>' +
       '<th>range</th><th>secs</th><th>into voice</th></tr>';
  for (const t of midiDoc.tracks) {
    h += '<tr><td>' + t.index + ' ' + esc(t.name) + '</td><td>' + t.notes +
         '</td><td' + (t.poly > 1 ? ' class="warn"' : '') + '>' + t.poly +
         '</td><td>' + t.low + '–' + t.high + '</td><td>' + t.seconds + '</td><td>';
    for (let c = 0; c < song.nch; c++)
      h += '<button onclick="importTrack(' + t.index + ',' + c + ')">ch' +
           (c + 1) + '</button> ';
    h += '</td></tr>';
  }
  h += '</table>';
  h += '<div class="mrow"><label>keep <select id="mpick">' +
       '<option value="high">highest note</option>' +
       '<option value="low">lowest note</option>' +
       '<option value="first">first started</option></select></label>' +
       '<label>waveform <input id="maudc" value="' +
       (song.chip === 'tia' ? 'C' : '5') + '" size="2"></label>' +
       '<label>volume <input id="mvol" value="8" size="2"></label>' +
       '<label>start at row <input id="moff" value="0" size="5"></label>' +
       '<button onclick="closeMidi()">close</button></div>';
  box.innerHTML = h;
  box.style.display = 'block';
}
function closeMidi(){ $('#midibox').style.display = 'none'; }

async function importTrack(track, ch) {
  const audc = parseInt(($('#maudc').value || '5').replace('$',''), 16);
  try {
    const r = await api('/api/midi/apply', {
      track: track, ch: ch, pick: $('#mpick').value,
      audc: isNaN(audc) ? 5 : audc,
      volume: parseInt($('#mvol').value) || 8,
      offset: parseInt($('#moff').value) || 0, grow: true});
    song = r.song; song.dirty = true; mark(); draw();
    let m = 'ch' + (ch + 1) + ': ' + r.placed + ' frames placed, median ' +
            r.median_cents + ' cents out (worst ' + r.worst_cents + ')';
    if (r.out_of_tune)
      m += ' — ' + r.out_of_tune + ' frames more than 25 cents off';
    if (r.dropped) m += '; ' + r.dropped + ' overlapping notes dropped';
    say(m, r.out_of_tune > r.placed / 4);
  } catch (err) { say(err.message, true); }
}

(async () => {
  song = await api('/api/song');
  $('#file').textContent = song.path || '(unsaved)';
  $('#chip').textContent = song.chip.toUpperCase() + ' · ' + song.region.toUpperCase();
  $('#rate').value = song.rate;
  draw();
  const notes = [];
  if (song.unsupported.length)
    notes.push('<b>will not render:</b> ' +
      song.unsupported.map(u => `${u.what} (AUDCTL $${u.audctl.toString(16)})`).join(', '));
  if (song.first_sound === null) {
    notes.push('<b>nothing in this song is audible</b> — every row is silent');
  } else {
    const t = (song.first_sound / song.rate).toFixed(1);
    notes.push(`first sound at <b>row ${song.first_sound}</b> (${t}s in), ` +
               `last at row ${song.last_sound}`);
    // open on the music rather than on the silence the capture starts with
    sel = {row: song.first_sound, ch: 0};
    mark();
    const e = cellAt(sel.row, 0);
    if (e) e.scrollIntoView({block: 'center'});
  }
  notes.push('click a cell and type · Enter commits · Delete silences · ' +
             'Backspace holds · ' +
             `Play all is the whole ${(song.rows.length / song.rate).toFixed(0)}s`);
  say(notes.join(' — '), song.unsupported.length > 0);
})();
</script></body></html>
"""


def main():
    global SONG, PATH
    ap = argparse.ArgumentParser(description=__doc__.strip().split("\n")[0])
    ap.add_argument("song", help="a .trk song, or a .a78 cartridge")
    ap.add_argument("--port", type=int, default=8123)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--seconds", type=float, default=40.0,
                    help="when recording a cartridge, how long for")
    ap.add_argument("--format", help="a player-format file, to read the songs "
                                     "out of the ROM instead of recording it")
    ap.add_argument("--song-number", type=int,
                    help="which song to open, when reading from the ROM")
    ap.add_argument("--capture", action="store_true",
                    help="record in an emulator even if the songs could be "
                         "read from the ROM")
    args = ap.parse_args()

    path = args.song
    if path.lower().endswith(".a78"):
        fmt = args.format or (None if args.capture else find_format(path))
        usable = []
        if fmt:
            # The notes are in the ROM. Reading them needs no emulator, takes a
            # moment rather than a minute, and gets every song rather than
            # whichever one happened to play during the recording window.
            #
            # A signature says "same music engine", not "same addresses". A
            # regional release or a rebuild can share the player and keep its
            # tables elsewhere -- Midnight Mutants' European release even
            # understates its own mapper. So a failure here is a reason to
            # record, not to give up.
            songs = []
            try:
                songs, _pulled = songs_from_rom(path, fmt)
            except Exception as e:                  # noqa: BLE001
                text = str(e).split("\n")[0]
                if "not rendered from the ROM" in text:
                    # Recognised and deliberately not decoded -- a different
                    # thing from a format landing on a cartridge it does not
                    # fit, and worth saying differently.
                    head, _sep, why = text.partition(": ")
                    print("%s recognised this cartridge:"
                          % os.path.basename(fmt))
                    print("   %s" % head)
                    for line in _wrap(why, 70):
                        print("   %s" % line)
                    print("Recording it instead.")
                else:
                    print("%s matched this cartridge, but reading it failed:"
                          % os.path.basename(fmt))
                    print("   %s" % text)
                    print("It describes the player, not this layout. "
                          "Recording instead.")
            usable = [x for x in songs if x.get("rows")]
            if songs and not usable:
                print("%s describes no playable songs here. Recording instead."
                      % os.path.basename(fmt))
        if usable:
            print("%s -- read from the ROM with %s, no emulator"
                  % (os.path.basename(path), os.path.basename(fmt)))
            for x in usable:
                print("   song %-2d %5d rows  %5.1f s  %d sounding"
                      % (x["n"], x["rows"], x["seconds"], x["sounding"]))
            pick = args.song_number
            if pick is None:
                pick = max(usable, key=lambda x: x["sounding"])["n"]
                print("   opening song %d, the one with the most in it -- "
                      "--song-number picks another" % pick)
            chosen = [x for x in usable if x["n"] == pick]
            if not chosen:
                sys.stderr.write("no song %d in this cartridge\n" % pick)
                return 2
            SONG = chosen[0]["song"]
            PATH = os.path.abspath("%s-song%d.trk"
                                   % (os.path.splitext(path)[0], pick))
            with io.open(PATH, "w", encoding="utf-8") as f:
                f.write(tracker.dump(SONG))
            path = None
        else:
            if not args.capture:
                tables = trace_audio(path)
                if tables:
                    print("No format file describes this cartridge, so its "
                          "songs cannot be read from")
                    print("the ROM. audiotrace can still see where the music "
                          "lives:")
                    for t in tables[:6]:
                        print("   %s" % t)
                    print("Turning those tables into songs needs a format "
                          "description -- see")
                    print("templates/format.json. Recording the game instead:")
            import capture
            try:
                r = capture.capture(path, seconds=args.seconds)
            except (RuntimeError, Exception) as e:  # noqa: BLE001
                sys.stderr.write("%s\n" % e)
                return 2
            path = r["trk"]

    # `path` is None when the song came straight out of the ROM: SONG and PATH
    # are already set, and there is no file to read.
    if path is not None:
        try:
            SONG = tracker.load(path)
        except (ValueError, OSError) as e:
            sys.stderr.write("%s\n" % e)
            return 2
        PATH = os.path.abspath(path)

    srv = HTTPServer(("127.0.0.1", args.port), Handler)
    url = "http://127.0.0.1:%d/" % args.port
    print("%s -- %s, %d rows" % (os.path.basename(PATH), SONG.chip, len(SONG)))
    print("open %s" % url)
    if not args.no_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped%s" % (" -- unsaved changes" if DIRTY else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
