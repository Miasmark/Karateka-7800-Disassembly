#!/usr/bin/env python3
"""
Read a Standard MIDI File, far enough to get notes out of it.

    python midi.py song.mid            # what is in it
    python midi.py song.mid --track 2  # the notes on one track

This is a decoder, not a converter: it turns a `.mid` into tracks of
`(start, end, note, velocity)` in seconds, and stops there. Fitting those onto
a chip that has 32 pitches and two voices is a different problem with different
compromises, and it lives in `tracker.py import`.

Standard MIDI is small enough to read directly and there is no reason to take a
dependency for it: a header chunk saying how ticks map to time, then track
chunks of delta-time-plus-event. The two things that catch people out are both
handled here -- **running status**, where an event omits its status byte and
inherits the last one, and **tempo changes**, which alter what a tick is worth
partway through and are all too easy to read once and apply globally.
"""
import argparse
import os
import struct
import sys


class MidiError(Exception):
    pass


def _varint(data, i):
    """MIDI's variable-length quantity: 7 bits a byte, high bit means more."""
    v = 0
    for _ in range(4):
        if i >= len(data):
            raise MidiError("file ends inside a variable-length number")
        b = data[i]
        i += 1
        v = (v << 7) | (b & 0x7F)
        if not b & 0x80:
            return v, i
    raise MidiError("variable-length number longer than four bytes")


def _chunks(data):
    i = 0
    while i + 8 <= len(data):
        tag = data[i:i + 4]
        size = struct.unpack(">I", data[i + 4:i + 8])[0]
        body = data[i + 8:i + 8 + size]
        if len(body) < size:
            raise MidiError("%s chunk claims %d bytes and the file has %d"
                            % (tag.decode("latin1", "replace"), size, len(body)))
        yield tag, body
        i += 8 + size


def read(path):
    """Parse a .mid into {"division", "format", "tracks": [...]}.

    Each track is {"name", "channels", "programs", "notes": [...]} and each note
    is a dict with `start`, `end` (seconds), `note` (MIDI number), `velocity`.
    """
    raw = open(path, "rb").read()
    head = None
    raw_tracks = []
    for tag, body in _chunks(raw):
        if tag == b"MThd":
            if len(body) < 6:
                raise MidiError("MThd chunk is too short")
            head = struct.unpack(">HHh", body[:6])
        elif tag == b"MTrk":
            raw_tracks.append(body)
    if head is None:
        raise MidiError("no MThd header: this is not a Standard MIDI File")
    fmt, _ntrk, division = head
    if division <= 0:
        raise MidiError(
            "SMPTE timing (division $%04X) is not supported; this reads "
            "ticks-per-quarter-note files, which is nearly all of them"
            % (division & 0xFFFF))

    # Tempo lives on one track and applies to all of them, so gather every
    # change first, then convert ticks to seconds against the whole map.
    tempo_map = []               # (tick, microseconds per quarter note)
    parsed = []
    for body in raw_tracks:
        parsed.append(_events(body, tempo_map))
    tempo_map.sort(key=lambda x: x[0])
    if not tempo_map or tempo_map[0][0] > 0:
        tempo_map.insert(0, (0, 500000))          # 120 bpm, the default

    tracks = []
    for evs in parsed:
        tracks.append(_notes(evs, division, tempo_map))
    return {"format": fmt, "division": division, "tracks": tracks,
            "tempo_changes": len(tempo_map),
            "path": os.path.basename(path)}


def _events(body, tempo_map):
    """(tick, status, data) for one track, resolving running status."""
    out = []
    i, tick, status = 0, 0, None
    while i < len(body):
        delta, i = _varint(body, i)
        tick += delta
        if i >= len(body):
            break
        b = body[i]
        if b & 0x80:
            status = b
            i += 1
        elif status is None:
            raise MidiError("running status before any status byte")
        if status == 0xFF:                        # meta
            if i + 1 > len(body):
                break
            kind = body[i]
            i += 1
            length, i = _varint(body, i)
            data = body[i:i + length]
            i += length
            if kind == 0x51 and length == 3:      # set tempo
                tempo_map.append((tick, (data[0] << 16) | (data[1] << 8) | data[2]))
            out.append((tick, 0xFF, kind, data))
            if kind == 0x2F:                      # end of track
                break
        elif status in (0xF0, 0xF7):              # sysex, skipped
            length, i = _varint(body, i)
            i += length
        else:
            n = 1 if (status & 0xF0) in (0xC0, 0xD0) else 2
            data = body[i:i + n]
            i += n
            out.append((tick, status, None, data))
    return out


def _seconds(tick, division, tempo_map):
    """Ticks to seconds, honouring every tempo change up to that point."""
    secs = 0.0
    last_tick, tempo = tempo_map[0]
    for change_tick, new_tempo in tempo_map[1:]:
        if change_tick >= tick:
            break
        secs += (change_tick - last_tick) * tempo / 1e6 / division
        last_tick, tempo = change_tick, new_tempo
    secs += (tick - last_tick) * tempo / 1e6 / division
    return secs


def _notes(evs, division, tempo_map):
    """Pair note-ons with note-offs and convert to seconds."""
    name, channels, programs = "", set(), set()
    open_notes = {}
    notes = []
    for tick, status, kind, data in evs:
        if status == 0xFF:
            if kind == 0x03 and not name:
                name = data.decode("latin1", "replace").strip()
            continue
        cmd, chan = status & 0xF0, status & 0x0F
        if cmd == 0xC0:
            programs.add(data[0] if data else 0)
            continue
        if cmd not in (0x80, 0x90) or len(data) < 2:
            continue
        note, vel = data[0], data[1]
        channels.add(chan)
        # A note-on with velocity 0 is a note-off. Treating it as an on is the
        # classic way to end up with every note stuck on forever.
        if cmd == 0x90 and vel > 0:
            open_notes.setdefault((chan, note), []).append((tick, vel))
        else:
            stack = open_notes.get((chan, note))
            if stack:
                start_tick, v = stack.pop(0)
                notes.append({
                    "start": _seconds(start_tick, division, tempo_map),
                    "end": _seconds(tick, division, tempo_map),
                    "note": note, "velocity": v, "channel": chan})
    for (chan, note), stack in open_notes.items():
        for start_tick, v in stack:                # never released
            notes.append({"start": _seconds(start_tick, division, tempo_map),
                          "end": _seconds(start_tick, division, tempo_map) + 0.25,
                          "note": note, "velocity": v, "channel": chan})
    notes.sort(key=lambda n: (n["start"], n["note"]))
    return {"name": name, "channels": sorted(channels),
            "programs": sorted(programs), "notes": notes}


NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def note_name(n):
    return "%s%d" % (NAMES[n % 12], n // 12 - 1)


def describe(doc):
    lines = ["%s -- format %d, %d ticks per quarter note, %d tempo change%s"
             % (doc["path"], doc["format"], doc["division"],
                doc["tempo_changes"],
                "" if doc["tempo_changes"] == 1 else "s")]
    lines.append("")
    lines.append("  %-3s %-24s %6s %5s %-11s %s"
                 % ("#", "name", "notes", "poly", "range", "seconds"))
    for i, t in enumerate(doc["tracks"]):
        n = t["notes"]
        if not n:
            lines.append("  %-3d %-24s %6d" % (i, t["name"][:24] or "(unnamed)", 0))
            continue
        lo = min(x["note"] for x in n)
        hi = max(x["note"] for x in n)
        end = max(x["end"] for x in n)
        lines.append("  %-3d %-24s %6d %5d %-11s %.1f"
                     % (i, t["name"][:24] or "(unnamed)", len(n), max_poly(n),
                        "%s-%s" % (note_name(lo), note_name(hi)), end))
    return "\n".join(lines)


def max_poly(notes):
    """The most notes sounding at once -- a 7800 voice can play one."""
    edges = []
    for n in notes:
        edges.append((n["start"], 1))
        edges.append((n["end"], -1))
    edges.sort()
    cur = best = 0
    for _t, d in edges:
        cur += d
        best = max(best, cur)
    return best


def main():
    ap = argparse.ArgumentParser(
        description=__doc__.strip().split("\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file")
    ap.add_argument("--track", type=int, help="list the notes on one track")
    args = ap.parse_args()
    try:
        doc = read(args.file)
    except (MidiError, IOError) as e:
        sys.stderr.write("%s\n" % e)
        return 2
    if args.track is None:
        print(describe(doc))
        return 0
    if not 0 <= args.track < len(doc["tracks"]):
        sys.stderr.write("no track %d; the file has %d\n"
                         % (args.track, len(doc["tracks"])))
        return 2
    t = doc["tracks"][args.track]
    print("%s -- %d notes" % (t["name"] or "(unnamed)", len(t["notes"])))
    for n in t["notes"]:
        print("  %8.3f %8.3f  %-5s vel %3d  ch %d"
              % (n["start"], n["end"], note_name(n["note"]), n["velocity"],
                 n["channel"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
