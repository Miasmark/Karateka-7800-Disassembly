#!/usr/bin/env python3
"""
Find a cartridge's music by following the code that writes to the sound chip.

    python audiotrace.py game.a78 [-c annotations.json]

`tracker.py capture` records what a game plays while it runs. This does the
static half: it disassembles the ROM, finds every store to a TIA or POKEY audio
register, works backwards to whatever supplies the value, and reports the tables
it lands on. That is the part of "where is the music?" that otherwise costs an
afternoon of reading.

What it can do, and what it cannot:

  * **Locating the player** is reliable. Audio stores cluster tightly -- a music
    routine and a sound-effect routine are usually a few hundred bytes each and
    nowhere near each other -- so grouping the stores by address separates them
    without knowing anything about the game.

  * **Finding the tables** works whenever the value comes from an indexed load,
    which is how nearly every player is written: `LDA table,Y / STA AUDC0`. The
    table's address falls straight out. Where the load goes through a zero-page
    pointer instead, it follows one more hop to whatever fills that pointer,
    which is usually the pointer table itself.

  * **Reconstructing the song** is not attempted, and should be treated with
    suspicion wherever a tool claims it. Every player invents its own format:
    Midnight Mutants nests song -> track -> pattern -> note, four levels deep,
    with a duration index and an instrument packed into one byte. Nothing
    portable can guess that. What this gives you is the addresses and the bytes;
    the shape is yours to work out, and `tracker.py capture` is the way to check
    a guess against what the game actually plays.
"""
import argparse
import io
import json
import os
import sys
import contextlib
import io as _io

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import disasm as D
import a7800
import cart as cart_module

# The registers worth watching, by offset from their chip's base.
TIA_AUDIO = {0x15: "AUDC0", 0x16: "AUDC1", 0x17: "AUDF0",
             0x18: "AUDF1", 0x19: "AUDV0", 0x1A: "AUDV1"}
POKEY_AUDIO = {0: "AUDF1", 1: "AUDC1", 2: "AUDF2", 3: "AUDC2", 4: "AUDF3",
               5: "AUDC3", 6: "AUDF4", 7: "AUDC4", 8: "AUDCTL"}

STORES = {"STA": "A", "STX": "X", "STY": "Y"}
# What an instruction leaves in a register. A read-modify-write on the
# accumulator counts: the value still came from whatever was loaded before it.
SETS = {"LDA": "A", "LDX": "X", "LDY": "Y", "TAX": "X", "TAY": "Y",
        "TXA": "A", "TYA": "A", "PLA": "A", "AND": "A", "ORA": "A",
        "EOR": "A", "ADC": "A", "SBC": "A", "LSR": "A", "ASL": "A",
        "ROL": "A", "ROR": "A", "INX": "X", "DEX": "X", "INY": "Y",
        "DEY": "Y", "LDA_": "A"}
INDEXED = {"abx", "aby", "zpx", "zpy"}
INDIRECT = {"izx", "izy"}


# Stores to an audio register, as raw opcodes. 8D = STA abs, 9D = STA abs,X,
# 99 = STA abs,Y -- between them they cover how nearly every player writes.
def _store_patterns():
    out = []
    for op in (0x8D, 0x9D, 0x99):
        for a in (0x15, 0x16, 0x17, 0x18, 0x19, 0x1A):     # TIA
            out.append(bytes([op, a, 0x00]))
        for base in (0x4000, 0x0450, 0x0800, 0x0440):      # POKEY
            for r in range(9):
                out.append(bytes([op, (base + r) & 0xFF, base >> 8]))
    return out


STORE_PATTERNS = _store_patterns()


def _zp_store_patterns():
    """Stores to TIA's audio registers through zero page.

    TIA sits at $00-$1F, so `STA $15,X` is the natural way to write AUDC and
    the way several players do -- Midnight Mutants' engine among them. The
    absolute forms above miss those entirely, which is why cartridges on that
    engine had no fingerprint at all and fell back to matching on title and
    size, one image at a time.

    Kept separate, and used only when the absolute scan finds nothing, so that
    every signature already written into a format file keeps its value.
    """
    out = []
    for op in (0x85, 0x95, 0x84, 0x94, 0x86, 0x96):
        for a in (0x15, 0x16, 0x17, 0x18, 0x19, 0x1A):
            out.append(bytes([op, a]))
    return out


ZP_STORE_PATTERNS = _zp_store_patterns()


def player_signature(rom):
    """A fingerprint of the code that writes to the sound chip.

    `rom` is the ROM bytes without the header, or a path.

    Two cartridges built on the same music engine have the same bytes around
    their audio stores, whatever the game is called. That makes this a better
    key for a format description than a title: a format file describes an
    *engine*, and the engine is what recurs. Across the library, 40 signatures
    span more than one game, covering 373 images -- and the biggest single one
    covers 27 different titles.

    Deliberately crude: the bytes either side of the first few stores, hashed.
    It is a lookup key, not a proof, and a format file that matches on it should
    still be checked against the game it lands on.
    """
    import hashlib
    if isinstance(rom, str):
        raw = open(rom, "rb").read()
        if len(raw) > 128 and raw[1:10] == b"ATARI7800":
            raw = raw[128:]
        rom = raw
    def scan(patterns):
        found = []
        for pat in patterns:
            i = rom.find(pat)
            while i != -1:
                found.append(i)
                i = rom.find(pat, i + 1)
        return found

    hits = scan(STORE_PATTERNS)
    if not hits:
        # Nothing writes an audio register absolutely. Try the zero-page
        # forms before giving up -- a player that uses them is still a player.
        hits = scan(ZP_STORE_PATTERNS)
    if not hits:
        return None
    hits.sort()
    blob = b"".join(rom[max(0, i - 24):i + 24] for i in hits[:6])
    return hashlib.sha1(blob).hexdigest()[:12]


def analyse(rom, config=None, low=None, mapper=None):
    """Disassemble, and hand back the analyzer plus the cart."""
    grab = {}
    orig = D.Analyzer.name_all

    def spy(self):
        orig(self)
        grab["an"] = self

    D.Analyzer.name_all = spy
    argv = ["audiotrace", rom, "-o", os.devnull + "_dir"]
    if config:
        argv += ["-c", config]
    if low:
        argv += ["--low", low]
    if mapper:
        argv += ["--mapper", mapper]
    saved, sys.argv = sys.argv, argv
    try:
        import tempfile
        out = tempfile.mkdtemp(prefix="audiotrace-")
        sys.argv = [x if x != os.devnull + "_dir" else out for x in argv]
        with contextlib.redirect_stdout(_io.StringIO()):
            rc = D.main()
    finally:
        sys.argv = saved
        D.Analyzer.name_all = orig
    if "an" not in grab:
        raise RuntimeError("the disassembler did not get far enough to analyse "
                           "this image (rc=%s)" % rc)
    return grab["an"]


def audio_map(cart):
    """Which absolute addresses are audio registers on this cartridge."""
    regs = {}
    for mirror in (0x000, 0x100, 0x200):
        for off, name in TIA_AUDIO.items():
            regs[mirror + off] = ("TIA", name)
    for base in cart.pokeys():
        for off, name in POKEY_AUDIO.items():
            regs[base + off] = ("POKEY", name)
    return regs


def prev_insn(an, sp, addr):
    """The instruction that falls through into `addr`, if there is one."""
    for back in (1, 2, 3):
        c = (sp, addr - back)
        if c in an.insn and an.insn[c][3] == back:
            return c
    return None


def source_of(an, loc, reg, depth=16):
    """Walk back through straight-line code for what last set `reg`.

    Straight-line only: following a branch backwards would need to know which
    way it went, and a wrong guess here invents a table that does not exist.
    Stopping early and saying so is the honest failure.
    """
    cur = loc
    for _ in range(depth):
        cur = prev_insn(an, *cur)
        if cur is None:
            return None
        mn, mode, operand, _ln = an.insn[cur]
        if SETS.get(mn) == reg:
            return cur, mn, mode, operand
    return None


def zp_filler(an, space, zp, before, depth=400):
    """What last wrote the zero-page pointer `zp`, looking back from `before`.

    A player that reads its notes through `LDA (ptr),Y` keeps the table address
    in RAM, so the interesting address is one hop further back: whatever stored
    into that pointer, which is usually an indexed load from a table of
    pointers.
    """
    best = None
    for (sp, a), (mn, mode, operand, _ln) in an.insn.items():
        if sp != space or mn != "STA" or operand != zp:
            continue
        if a >= before or (best and a <= best[0][1]):
            continue
        src = source_of(an, (sp, a), "A")
        if src:
            best = ((sp, a), src)
    return best


def find_writers(an, cart):
    """Every audio store, with whatever supplies its value."""
    regs = audio_map(cart)
    out = []
    for loc in sorted(an.insn):
        mn, mode, operand, _ln = an.insn[loc]
        if mn not in STORES or operand is None or operand not in regs:
            continue
        chip, name = regs[operand]
        src = source_of(an, loc, STORES[mn])
        entry = {"at": loc, "chip": chip, "reg": name, "src": src, "hop": None}
        if src and src[2] in INDIRECT:
            entry["hop"] = zp_filler(an, loc[0], src[3], loc[1])
        out.append(entry)
    return out


def cluster(writers, gap=0x200):
    """Group writers into routines: same space, within `gap` bytes."""
    groups = []
    for w in sorted(writers, key=lambda w: (w["at"][0], w["at"][1])):
        sp, a = w["at"]
        if groups and groups[-1]["space"] == sp and a - groups[-1]["hi"] <= gap:
            groups[-1]["hi"] = a
            groups[-1]["writers"].append(w)
        else:
            groups.append({"space": sp, "lo": a, "hi": a, "writers": [w]})
    return groups


def tables_in(group):
    """The distinct data addresses a group reads, and how."""
    seen = {}
    for w in group["writers"]:
        for src, why in ((w["src"], "direct"), (w["hop"][1] if w["hop"] else None,
                                                "through a pointer")):
            if not src:
                continue
            _sloc, mn, mode, operand = src
            if operand is None or mode in ("imm", "imp", "acc"):
                continue
            if operand < 0x4000:            # RAM, not a table in ROM
                continue
            key = (operand, mode)
            seen.setdefault(key, {"addr": operand, "mode": mode, "how": why,
                                  "regs": set()})
            seen[key]["regs"].add(w["reg"])
    return sorted(seen.values(), key=lambda t: t["addr"])


def coverage(an, cart):
    """Bytes reached as code, per space, as a fraction.

    This decides how much the rest of the report is worth. A static trace can
    only see code the disassembler reached, so a player behind an indirect jump
    or a RAM vector is invisible -- and the report would otherwise say
    "constants only" and read like "this game has no music", which is a
    different claim entirely.
    """
    out = {}
    for sp in cart.spaces():
        size = cart.size_of(sp)
        n = sum(ln for (s2, _a), (_m, _md, _o, ln) in an.insn.items() if s2 == sp)
        out[sp] = (n, size)
    return out


def dump(cart, space, addr, n=16):
    try:
        return " ".join("%02X" % b for b in cart.slice(space, addr, n))
    except Exception:                        # noqa: BLE001
        return "(outside this space)"



# ---------------------------------------------------------------- the engine
# Locating the Atari in-house music engine, the one behind Midnight Mutants and
# Commando, which turns up in at least 42 images in the library. Its tables
# have a shape distinctive enough to find without running anything: sixteen
# instrument records of sixteen bytes with only the first ten used, a sixteen
# entry duration table immediately before them, pointer lists ended by an entry
# whose high byte is zero, and count-prefixed patterns of fixed-size notes.
#
# Every step is checked against the next. A wrong instrument table gives a
# duration table that is not sixteen descending frame counts; a wrong song
# table gives pointers that do not land on pointer lists; a wrong track list
# gives patterns whose counts run off the end of the bank. A bad guess fails
# loudly rather than producing plausible rubbish, which is the only way a
# search like this is worth having.


def _u16(c, sp, a):
    return c.byte(sp, a) | (c.byte(sp, a + 1) << 8)


def find_instruments(c, sp, base, size, blob):
    """Sixteen 16-byte records whose last six bytes are always zero."""
    out = []
    span = 16 * 16
    for off in range(0, len(blob) - span):
        live = 0
        ok = True
        for k in range(16):
            r = blob[off + k * 16: off + k * 16 + 16]
            if any(r[10:]):
                ok = False
                break
            if any(r[:10]):
                live += 1
        if ok and live >= 10:
            out.append((base + off, live))
    return out


def duration_table(c, sp, instruments):
    """The sixteen bytes before the instruments, if they look like durations.

    Frame counts, longest first: every entry non-zero, strictly descending, and
    none longer than a few seconds. That is a sharp test -- sixteen bytes of
    anything else almost never satisfies it -- and it is what confirms the
    instrument table was found in the right place rather than sixteen bytes off.
    """
    a = instruments - 16
    try:
        vals = [c.byte(sp, a + i) for i in range(16)]
    except Exception:                                        # noqa: BLE001
        return None
    if any(v == 0 for v in vals):
        return None
    if any(vals[i] <= vals[i + 1] for i in range(15)):
        return None
    if vals[0] > 0xC0:
        return None
    return a, vals


def looks_like_pattern(c, sp, addr, lo, hi, record=2):
    """A count byte and that many fixed-size notes, all inside the bank."""
    if not (lo <= addr < hi):
        return False
    try:
        n = c.byte(sp, addr)
    except Exception:                                        # noqa: BLE001
        return False
    # Any non-zero count that fits. An earlier cap of $C0 "because patterns
    # are short" threw away Midnight Mutants' 196-note pattern at f6:$78A7 and
    # with it the whole song table, since one unresolvable song broke the run.
    # Fitting inside the bank is the real constraint; length is not.
    if n == 0:
        return False
    return addr + 1 + n * record <= hi


def track_list(c, sp, addr, lo, hi, limit=64):
    """Pointer list ended by an entry whose high byte is zero."""
    if not (lo <= addr < hi):
        return None
    out = []
    a = addr
    for _ in range(limit):
        if not (lo <= a < hi - 1):
            return None
        v = _u16(c, sp, a)
        a += 2
        if (v >> 8) == 0:
            return out
        out.append(v)
    return None


def spaces_covering(cart, addr):
    """Every space in which `addr` is a real byte.

    A pointer in the paged window means a different byte in every bank, so a
    search that resolves it in one space only will miss any player whose song
    table sits in the fixed bank and whose music does not -- which is Midnight
    Mutants exactly. Trying each bank and recording which one worked is also
    how the format file's `banks` mapping gets written.
    """
    out = []
    for sp in cart.spaces():
        lo = cart.base_of(sp)
        hi = lo + cart.size_of(sp)
        if lo <= addr < hi:
            out.append((sp, lo, hi))
    return out


def voice_resolves(cart, ptr, sp, lo, hi):
    """Does this voice pointer lead to a track list of real patterns?"""
    tl = track_list(cart, sp, ptr, lo, hi)
    # `not tl` would also accept the empty list, which is what you get when the
    # very first word has a zero high byte -- true of any bank where the
    # pointer lands on zeros, so it made silence look like a valid voice.
    if tl is None or not tl:
        return False
    for pat in tl:
        if not looks_like_pattern(cart, sp, pat, lo, hi):
            return False
    return True


def song_reach(cart, tsp, addr, voices):
    """The distinct patterns one song entry reaches, or None if it does not."""
    ptrs = [_u16(cart, tsp, addr + i * 2) for i in range(voices)]
    live = [p for p in ptrs if p]
    if not live:
        # Every pointer zero. Within a song that just means unused voices;
        # as a whole entry it means the grouping is wrong, so say which.
        return "blank"
    works = []
    for sp, lo, hi in spaces_covering(cart, live[0]):
        pats = set()
        ok = True
        for ptr in live:
            tl = track_list(cart, sp, ptr, lo, hi)
            if tl is None or not tl:
                ok = False
                break
            for pat in tl:
                if not looks_like_pattern(cart, sp, pat, lo, hi):
                    ok = False
                    break
                pats.add((sp, pat))
            if not ok:
                break
        if ok:
            works.append((sp, pats))
    if not works:
        return None
    # More than one bank can satisfy a pointer into the paged window, and
    # which one the player actually had mapped is runtime state that no static
    # search recovers. Take the first, but report that there was a choice --
    # a silently wrong bank yields a song that plays and is not the game's.
    sp, pats = works[0]
    return sp, pats, len(works)


def song_ok(cart, tsp, addr, voices):
    """One song entry, if some single bank resolves all of its voices.

    All of a song's voices are required to live in the same bank, because that
    is what the player does: it pages once and reads everything. Letting each
    voice pick its own bank would accept nonsense.
    """
    ptrs = [_u16(cart, tsp, addr + i * 2) for i in range(voices)]
    live = [p for p in ptrs if p]
    if not live:
        return None
    for sp, lo, hi in spaces_covering(cart, live[0]):
        if all(voice_resolves(cart, p, sp, lo, hi) for p in live):
            return sp
    return None


def find_song_table(cart, tsp, lo, hi, voices, stride,
                    min_songs=4, slack=2):
    """The longest run of song entries that resolve all the way down to notes.

    Every candidate is followed through: pointer -> track list -> patterns ->
    counts that fit inside the bank. Nothing is accepted on shape alone, which
    is what keeps a run of coincidental-looking words from being read as a song
    table.

    A run tolerates `slack` consecutive unresolvable entries rather than
    stopping at the first. Real tables contain them: one of Midnight Mutants'
    twelve songs names a track this search cannot follow, and requiring an
    unbroken run rejected the table outright over a single entry. Trailing
    failures are trimmed so the reported count is songs actually verified.
    """
    best = None
    # Step by one. Stepping by two assumes the table is word-aligned, and
    # Midnight Mutants' sits at $7F65 -- odd -- so an even-stepped scan walked
    # straight past it and settled for a five-entry coincidence elsewhere. The
    # assembler had no reason to align it and did not.
    for addr in range(lo, hi - stride * min_songs):
        # The table must start where it starts. Allowing slack at the front as
        # well as the middle let a run begin two entries early on bytes that
        # resolve to nothing, which moved Commando's table from $B640 to $B630
        # -- right shape, wrong address, and no complaint from anything.
        if song_ok(cart, tsp, addr, voices) is None:
            continue
        entries, banks, misses = 0, {}, 0
        a, last_good = addr, -1
        reach, blanks, ambiguous = set(), 0, set()
        while a + stride <= hi and misses <= slack:
            got = song_reach(cart, tsp, a, voices)
            if got == "blank":
                blanks += 1
                misses += 1
            elif got is None:
                misses += 1
            else:
                sp, pats, nbanks = got
                if nbanks > 1:
                    ambiguous.add(entries)
                misses = 0
                banks[entries] = sp
                reach |= pats
                last_good = entries
            entries += 1
            a += stride
        count = last_good + 1
        verified = len(banks)
        # Rank on how much *music* the table reaches, not on how many entries
        # resolve. Any run of plausible pointers resolves; a real song table
        # addresses a lot of distinct patterns, while a coincidence addresses
        # one or two over and over. Alien Brigade's first candidate verified
        # five songs and reached a single pattern between them, which is the
        # signature of the wrong answer.
        # Reachable music first, then *fewer blank entries*, then more songs.
        # The middle term is what separates readings of the same bytes: a
        # four-voice song whose last two slots are zero is ordinary, but split
        # it into two-voice entries and those zeros become a whole blank song
        # in the middle of the table, which no real one has. Without this,
        # Commando's eleven four-voice songs read as twenty-two two-voice ones
        # at the same address -- same patterns, higher count, wrong.
        score = (len(reach), -blanks, verified)
        if verified >= min_songs and (best is None or score > best[1]):
            best = (addr, score, voices, stride, banks, count, verified,
                    sorted(ambiguous))
    return best


def find_engine(cart):
    """Every table this engine uses, or None. Reports what it checked."""
    results = []
    for sp in cart.spaces():
        base, size = cart.base_of(sp), cart.size_of(sp)
        try:
            blob = bytes(cart.slice(sp, base, size))
        except Exception:                                    # noqa: BLE001
            continue
        lo, hi = base, base + size
        for instruments, live in find_instruments(cart, sp, base, size, blob):
            dur = duration_table(cart, sp, instruments)
            if not dur:
                continue
            daddr, dvals = dur
            for voices, stride in ((4, 8), (2, 4)):
                got = find_song_table(cart, sp, lo, hi, voices, stride)
                if got:
                    (saddr, score, v, st, banks, total, nsongs,
                     ambig) = got
                    results.append({
                        "space": sp, "instruments": instruments,
                        "instrument_rows": live,
                        "durations": daddr, "duration_values": dvals,
                        "songs": saddr, "count": total,
                        "verified": nsongs, "patterns": score[0],
                        "blanks": -score[1], "ambiguous": ambig,
                        "voices": v, "stride": st, "banks": banks,
                    })
    if not results:
        return None
    results.sort(key=lambda r: (-r["patterns"], r["blanks"],
                                -r["verified"]))
    return results[0]


def find_waveforms(rom, config=None, low=None, mapper=None, space=None):
    """The table this player indexes for its AUDC values, if tracing finds it.

    The structural search cannot get this one. A TIA player spends the top
    three bits of its pitch byte on a waveform index, and the table those index
    is eight arbitrary values sitting in the middle of code -- Midnight
    Mutants' is `04 0C 01 06 08 07 0F 09` at f6:$76F6, with instructions either
    side. Nothing about those bytes says "table".

    But the *other* half of this tool already finds it, because the player
    reaches it the way players reach everything: `LDA table,X / STA AUDC0`.
    So this asks the tracer rather than guessing, which is the whole reason
    both halves live in one file.
    """
    try:
        an = analyse(rom, config, low, mapper)
    except Exception:                                        # noqa: BLE001
        return None
    cart = an.cart
    best = None
    for group in cluster(find_writers(an, cart)):
        for t in tables_in(group):
            if not any(r.startswith("AUDC") for r in t.get("regs", ())):
                continue
            if space and group["space"] != space:
                continue
            # prefer the smallest plausible one: a waveform table is a handful
            # of entries, not a song's worth of data
            if best is None or t["addr"] < best[1]:
                best = (group["space"], t["addr"])
    return best


def waveform_candidates(cart, sp, near, span=1024):
    """Where a TIA player's waveform table might be, by shape.

    Eight consecutive bytes, every one a valid four-bit AUDC value, all eight
    distinct, within a kilobyte of the duration table. That is a tight enough
    description to leave two candidates in every cartridge tried -- and always
    exactly two, adjacent, because the real table is nine low-valued bytes and
    both the window at `a` and the one at `a+1` satisfy it.

    Nothing in the ROM breaks that tie. The bytes cannot say which of two
    overlapping windows the player indexes; only running the game can, so this
    returns both and leaves the choice to whoever has a capture.
    """
    base, size = cart.base_of(sp), cart.size_of(sp)
    out = []
    lo = max(base, near - span)
    hi = min(base + size - 8, near + span)
    for a in range(lo, hi):
        v = [cart.byte(sp, a + i) for i in range(8)]
        if all(x <= 0x0F for x in v) and len(set(v)) == 8:
            out.append(a)
    return out


def engine_format(cart, rom, found):
    """A format file for what `find_engine` located, ready for songfmt.py.

    Everything here was read out of the cartridge and checked against the next
    table down, so this is a description rather than a guess -- but it is a
    description of *structure*, and structure is not sound. Whether it is right
    is settled by `tracker.py capture` and a frame-by-frame comparison, exactly
    as Commando's was. Until someone does that, treat it as a strong lead.

    The one field that cannot be recovered by looking is what a song *is*: the
    title theme, the death jingle. Somebody has to play the game and say.
    """
    sp = found["space"]
    pokey = bool(cart.pokeys())
    chip = "pokey" if pokey else "tia"
    banks = {}
    for n, bank_space in found["banks"].items():
        if bank_space.startswith("b"):
            banks[str(n)] = int(bank_space[1:])

    doc = {
        "name": "%s -- %s music player"
                % (((cart.info or {}).get("title") or
                    os.path.splitext(os.path.basename(rom))[0]), chip.upper()),
        # Prose goes under `_about`, not `note`: songfmt reads `note` as the
        # note *field spec*, so a paragraph there silently replaces the
        # description of what a note looks like and every read fails with a
        # type error a long way from the cause.
        "_about": [
            "Found by audiotrace.py --engine, which locates the Atari in-house",
            "music engine by the shape of its tables and follows every pointer",
            "down to real notes before reporting anything.",
            "",
            "The same engine drives Midnight Mutants and Commando. What varies",
            "between cartridges is the addresses and the chip; the layout does",
            "not. Both of those known players share a duration table that is",
            "byte-for-byte identical.",
            "",
            "Structure verified, sound not. Confirm with:",
            "    python tools/capture.py <rom> --seconds 45",
            "then compare frame by frame. Commando's description matches its",
            "hardware on 576 of 576 frames; nothing here has had that done yet.",
        ],
        "chip": chip,
        "voices": found["voices"],
        "match": {},
        "songs": {
            "table": "%s:%04X" % (sp, found["songs"]),
            "count": found["count"],
            "stride": found["stride"],
            "voice_ptr": [i * 2 for i in range(found["voices"])],
        },
        "track": {
            "kind": "ptr_list", "width": 2, "endian": "little",
            "end_when": "high_byte_zero", "keep_terminator": True,
            "limit": 64,
        },
        "pattern": {
            "kind": "count_prefixed", "count_width": 1, "record": 2,
            "limit": 255,
        },
        "note": {
            "fields": {
                "instrument": [0, 4, 4],
                "duration": [0, 0, 4],
            },
            "_fields": "name: [byte, lowest bit, width]",
            "rest_when": "byte1 == 0",
        },
        "durations": {
            "table": "%s:%04X" % (sp, found["durations"]),
            "width": 1, "count": 16,
            "_note": " ".join("%02X" % v for v in found["duration_values"]),
        },
        "instruments": {
            "table": "%s:%04X" % (sp, found["instruments"]),
            "stride": 16, "count": 16, "engine": "adsr5",
            "fields": {
                "flags": 0, "counter": 1, "peak": 2, "attack": 3,
                "decay_len": 4, "sustain": 5, "decay": 6, "sustain_len": 7,
                "release_len": 8, "release": 9,
            },
        },
    }

    if banks:
        doc["songs"]["banks"] = banks
        doc["songs"]["_banks"] = (
            "Which bank each song's track pointers resolve against, worked out "
            "by trying each and keeping the one where every voice led to real "
            "patterns.")
        if found.get("ambiguous"):
            doc["songs"]["_banks_uncertain"] = (
                "Songs %s each had more than one bank in which every voice "
                "resolved. Which bank the player actually had mapped is "
                "runtime state and no static search recovers it, so the first "
                "that worked was taken. A wrong bank here does not fail -- it "
                "produces a song, from the wrong bytes, that sounds like "
                "music. Check these against a capture before trusting them."
                % ", ".join(str(x) for x in found["ambiguous"]))

    # Where the pitch lives depends on the chip, and the reason is register
    # width. TIA's AUDF is five bits, leaving three in the pitch byte for a
    # waveform index; POKEY's is the whole byte, so there is no room and the
    # control value comes from the instrument instead.
    if pokey:
        doc["note"]["fields"]["pitch"] = [1, 0, 8]
        doc["audc_from"] = "instrument"
    else:
        doc["note"]["fields"]["pitch"] = [1, 0, 5]
        doc["note"]["fields"]["waveform"] = [1, 5, 3]
        wf = find_waveforms(rom, space=sp)
        if wf:
            doc["waveforms"] = {
                "table": "%s:%04X" % wf, "count": 8,
                "_note": ("Found by tracing the code that writes AUDC, not by "
                          "shape -- these are eight arbitrary values sitting "
                          "in the middle of instructions. The note's waveform "
                          "field indexes this, and the player shifts the pitch "
                          "byte right by five to get it."),
            }
        else:
            # The tracer never reaches some players at all -- Alien Brigade's
            # among them -- so fall back to shape, which narrows it to two.
            cands = waveform_candidates(cart, sp, found["durations"])
            if cands:
                doc["waveforms"] = {
                    "table": "%s:%04X" % (sp, cands[-1]), "count": 8,
                    "_note": ("Found by shape, not by tracing: eight "
                              "consecutive distinct values, all valid 4-bit "
                              "AUDC, near the duration table."),
                }
                if len(cands) > 1:
                    doc["waveforms"]["_alternatives"] = [
                        "%s:%04X" % (sp, a) for a in cands]
                    doc["waveforms"]["_uncertain"] = (
                        "More than one window fits, and they overlap: the real "
                        "table is nine low-valued bytes, so the eight starting "
                        "at each of two addresses both qualify. The ROM cannot "
                        "say which the player indexes. Render each against a "
                        "capture and keep the one that reproduces what the "
                        "cartridge plays -- on Midnight Mutants, where the "
                        "answer is known, it is the later of the two.")
            else:
                doc["_waveforms"] = (
                    "This player indexes a waveform table with the top three "
                    "bits of the pitch byte. Neither tracing nor shape found "
                    "it, so the song will not render until a \"waveforms\" "
                    "block names it.")

    # From the bytes the cart already holds, not by re-opening the path: the
    # cart may have been built from something that is not a file on disk, and
    # its `rom` is header-stripped already, which is what the hash wants.
    sig = player_signature(cart.rom)
    if sig:
        doc["match"]["player"] = sig
    else:
        doc["match"]["size"] = len(cart.rom)
        title = ((cart.info or {}).get("title") or "").strip()
        if title:
            doc["match"]["title"] = title
    return doc


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().split("\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("rom")
    ap.add_argument("-c", "--config")
    ap.add_argument("--low", choices=["none", "ram", "bank6", "rom"])
    ap.add_argument("--mapper", choices=["linear", "supergame", "absolute"])
    ap.add_argument("--bytes", type=int, default=16,
                    help="how much of each table to show")
    ap.add_argument("--signature", action="store_true",
                    help="print this cartridge's player fingerprint and stop, "
                         "for pasting into a format file's match block")
    ap.add_argument("--engine", action="store_true",
                    help="hunt for the Atari in-house music engine's tables "
                         "and report what was found")
    ap.add_argument("--emit", nargs="?", const="", metavar="PATH",
                    help="with --engine, write a format file for what was "
                         "found. With no path it goes to formats/.")
    ap.add_argument("--json", action="store_true",
                    help="emit annotation blocks for the tables found")
    args = ap.parse_args()

    if args.signature:
        sig = player_signature(args.rom)
        if not sig:
            print("no audio stores in this image, so it has no player "
                  "fingerprint")
            return 1
        print(sig)
        return 0

    if args.engine:
        # Structural, not traced: this searches the bytes for the engine's
        # table shapes, so it works on a cartridge whose player the tracer
        # never reaches -- which is most of them.
        try:
            cart = cart_module.Cart(args.rom, low=args.low,
                                    mapper=args.mapper)
        except (cart_module.UnknownMapper, cart_module.UnknownSpace,
                IOError) as e:
            sys.stderr.write("%s\n" % e)
            return 2
        found = find_engine(cart)
        if not found:
            print("no Atari in-house engine found in this image.")
            print("It looks for 16 instrument records of 16 bytes with the "
                  "last 6 zero, a 16-entry descending duration table just "
                  "before them, and a song table whose pointers reach real "
                  "patterns. All three have to hold.")
            return 1
        sp = found["space"]
        print("%s" % os.path.basename(args.rom))
        print("  durations   %s:$%04X   %s"
              % (sp, found["durations"],
                 " ".join("%02X" % v for v in found["duration_values"])))
        print("  instruments %s:$%04X   16 x 16 bytes, %d rows used"
              % (sp, found["instruments"], found["instrument_rows"]))
        print("  song table  %s:$%04X   %d songs, %d voices, stride %d"
              % (sp, found["songs"], found["count"], found["voices"],
                 found["stride"]))
        print("  %d of them followed all the way down to real patterns"
              % found["verified"])
        if args.emit is not None:
            doc = engine_format(cart, args.rom, found)
            path = args.emit
            if not path:
                root = os.path.dirname(
                    os.path.dirname(os.path.abspath(__file__)))
                stem = os.path.splitext(os.path.basename(args.rom))[0]
                stem = "".join(ch if (ch.isalnum() or ch in "-_") else "-"
                               for ch in stem)
                stem = "-".join(x for x in stem.split("-") if x).lower()
                path = os.path.join(root, "formats", stem + ".json")
            d = os.path.dirname(path)
            if d and not os.path.isdir(d):
                os.makedirs(d)
            with io.open(path, "w", encoding="utf-8") as f:
                print(json.dumps(doc, indent=2), file=f)
            print("  wrote %s" % path)
            print("  Structure verified, sound not. Compare it against a "
                  "capture before trusting it.")
        return 0

    try:
        an = analyse(args.rom, args.config, args.low, args.mapper)
    except (RuntimeError, cart_module.UnknownMapper,
            cart_module.UnknownSpace) as e:
        sys.stderr.write("%s\n" % e)
        return 2
    cart = an.cart

    writers = find_writers(an, cart)
    if not writers:
        print("No audio stores in the traced code.")
        print("Either the player is only reached through a path the tracer did "
              "not follow\n(add an entry point to the annotations), or this "
              "cartridge makes no sound.")
        return 1

    cov = coverage(an, cart)
    reached = sum(n for n, _t in cov.values())
    total = sum(t for _n, t in cov.values())
    frac = 100.0 * reached / max(total, 1)

    groups = cluster(writers)
    print("%d audio store%s in %d routine%s, from %.1f%% of the ROM traced\n"
          % (len(writers), "" if len(writers) == 1 else "s",
             len(groups), "" if len(groups) == 1 else "s", frac))

    blocks = []
    for g in groups:
        regs = sorted({w["reg"] for w in g["writers"]})
        chips = sorted({w["chip"] for w in g["writers"]})
        span = "%s:$%04X-$%04X" % (g["space"], g["lo"], g["hi"])
        print("%s  %d stores, %s: %s" % (span, len(g["writers"]),
                                         "/".join(chips), " ".join(regs)))
        tabs = tables_in(g)
        if not tabs:
            print("    writes constants only -- initialisation or silence\n")
            continue
        for t in tabs:
            idx = {"abx": ",X", "aby": ",Y", "zpx": ",X", "zpy": ",Y"}.get(t["mode"], "")
            print("    table $%04X%-2s %-16s -> %s"
                  % (t["addr"], idx, "(%s)" % t["how"], " ".join(sorted(t["regs"]))))
            print("        %s" % dump(cart, g["space"], t["addr"], args.bytes))
            blocks.append({"loc": "%s:%04X" % (g["space"], t["addr"]),
                           "len": args.bytes,
                           "note": "audio table -> %s" % " ".join(sorted(t["regs"]))})
        print()

    constant_only = sum(1 for g in groups if not tables_in(g))
    if frac < 25.0 and constant_only:
        print("Only %.1f%% of this ROM was traced, and %d of the %d routines "
              "write\nnothing but constants. That reads like \"no music\", but "
              "it is far more\nlikely the player was never reached: a static "
              "trace cannot follow an\nindirect jump or a handler installed "
              "through a RAM vector.\n"
              "\n  * `tracker.py capture` will still record it -- that watches "
              "the running\n    machine and does not care how the code is "
              "reached.\n"
              "  * To find it statically, give the disassembler a way in: add "
              "the address\n    to \"entries\" in an annotations file, or "
              "\"ram_vectors\" if the handler is\n    installed into RAM.\n"
              % (frac, constant_only, len(groups)))

    print("The addresses are solid; the format is not something a tool can "
          "guess.\nCheck any reading of it against `tracker.py capture`, which "
          "records what\nthe game actually plays.")

    if args.json:
        print("\n; paste into the annotations' \"blocks\":")
        print(json.dumps(blocks, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
