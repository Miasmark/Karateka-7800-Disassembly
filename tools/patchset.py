#!/usr/bin/env python3
"""
A bundle of patches you can pick from, checked a section at a time.

    python tools/patchset.py list   karateka.abp
    python tools/patchset.py check  karateka.abp --rom karateka.a78
    python tools/patchset.py apply  karateka.abp --rom karateka.a78 \\
        --with dose-4,generous-reach,remap --out fixed.a78

## Why not just ship BPS files

A BPS is a delta between two whole files, with a CRC32 of the whole source and
the whole target. That is the right shape for "here is my hack", and the wrong
shape for "here are nine independent fixes, take the ones you want":

  - Nine fixes give 512 combinations. Nine BPS files cannot express them; you
    would have to build and ship every combination you want anyone to have.
  - A whole-file CRC means a patch refuses a dump that differs anywhere,
    including in a header it does not care about.
  - Two patches that happen to touch the same bytes apply cleanly one after the
    other and silently produce a ROM that is neither.

Nothing standard fixes this. VCDIFF has windows with optional per-window
checksums, but they are chosen by the compressor and mean nothing to a person.
NINJA (.RUP) and BPM bundle patches for *several files*; neither addresses
several regions of one file. PPF validates one hardcoded block. So this format
exists, and it is deliberately boring: JSON plus BPS files, in a zip or a
directory.

## What it adds

**Sections.** A named byte range with a CRC32 of its *pre-image*. A patch names
the section it edits, so applying it checks 168 bytes rather than 49152 -- and a
dump with a different header, or with another fix already applied elsewhere,
still passes.

**Knobs.** Each option says what it has an opinion about. Two options turning
the same knob are alternatives, and asking for both is refused as a choice not
yet made, rather than applied in file order.

**Floats.** Some fixes need somewhere to put new code, and hardcoding an
address is how a patch stops working on a dump that differs. A float declares
how much room it needs and where to look:

    "length": 32, "search": [{"addr": "0xFF00", "length": 256}], "fill": 0

The patcher finds a run of that many bytes all equal to `fill` in the search
ranges, drops the blob there, and writes the address it chose into every fixup
site the float lists. Two floats never get the same space.

(The obvious phrasing for this is "find 16 bytes with a CRC of zero", and the
test here is on the bytes instead, because CRC32 of sixteen zero bytes is not
zero -- it is 0x8B9F9C56. Same intent, honest arithmetic.)

**Headers.** The target is described by the size and SHA-256 of its *body*.
A `.a78` with a 128-byte header and a headerless `.bin` are the same body, so
the same bundle applies to both and the header is handed back untouched.

## The format

An **.abp** -- an anchored bundle of patches. A zip, so it opens in anything:

    patchset.json          the manifest
    p/<option>.<sec>.bps   one BPS per (option, section) pair
    f/<float>.bin          raw bytes for each float

A directory or a zip containing them; both are read the same way.
"""
import argparse
import hashlib
import io
import json
import os
import sys
import zipfile
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bps

NL2 = chr(10)          # for messages that wrap onto a second line
FORMAT = "patchset/2"
# A bundle with an option that grows the body is patchset/3, so a reader that
# predates growth refuses it by name instead of applying sections to the wrong
# offsets. Everything else is unchanged, and /2 bundles still read.
FORMAT_GROW = "patchset/3"
FORMATS = (FORMAT, FORMAT_GROW)
# .a78 cart-type bits that put something at $4000-$7FFF, or bank the image:
# growing a linear cartridge down into $4000 would collide with any of them.
# POKEY at $0450/$0440 and the YM2151 at $0460 live elsewhere.
A78_HEADER = 128
A78_BUSY_4000 = 0xFFFF & ~(0x0040 | 0x0400 | 0x0800)
ENCODINGS = ("lo8", "hi8", "abs16")


class PatchSetError(Exception):
    pass


def h(v, default=None):
    """Accept 4096, "0x1000" or "$1000" -- manifests should read like addresses."""
    if v is None:
        return default
    if isinstance(v, int):
        return v
    return int(str(v).lstrip("$").replace("0x", "").replace("0X", ""), 16)


def crc32(data):
    return zlib.crc32(data) & 0xFFFFFFFF


def crc32_combine(crc_a, crc_b, len_b):
    """crc32(a + b) from crc32(a), crc32(b) and len(b), without the bytes.

    CRC32's register is linear: running b from register r gives Z(r) ^ R(b),
    where Z is `len_b` zero bytes. So crc(a+b) = crc(b) ^ Z(crc(a)), and
    Z(x) is zlib's CRC of that many zeros started from x (its init and final
    XOR cancel out). How a span's pre-image CRC follows from its sections'.
    """
    z = zlib.crc32(bytes(len_b), crc_a ^ 0xFFFFFFFF) ^ 0xFFFFFFFF
    return (crc_b ^ z) & 0xFFFFFFFF


class PatchSet(object):
    """A manifest plus the files it names, from a directory or a zip."""

    def __init__(self, path):
        self.path = path
        self._zip = None
        if os.path.isdir(path):
            raw = io.open(os.path.join(path, "patchset.json"),
                          encoding="utf-8").read()
        else:
            self._zip = zipfile.ZipFile(path)
            raw = self._zip.read("patchset.json").decode("utf-8")
        self.m = json.loads(raw)
        if self.m.get("format") not in FORMATS:
            raise PatchSetError("%s is %r, not one of %s"
                                % (path, self.m.get("format"), ", ".join(FORMATS)))
        if self.m.get("format") != FORMAT_GROW and any(
                o.get("grow") for o in self.m["options"]):
            raise PatchSetError("%s grows the cartridge, which needs %s"
                                % (path, FORMAT_GROW))
        self.sections = self.m["sections"]
        self.options = {o["id"]: o for o in self.m["options"]}
        self.base0 = h(self.m["target"].get("base"), 0)   # the dump's own
        self.base = self.base0    # the body in hand: find_body and grow move it
        self.pristine = None      # set by find_body: is this an untouched dump
        self._deps = None         # derived dependencies, read from the patches

    def read(self, member):
        if self._zip is not None:
            return self._zip.read(member)
        return io.open(os.path.join(self.path, member), "rb").read()

    # ------------------------------------------------------------- growth
    def grows(self, options=None):
        """The growth these options ask for, as (size, at, fill), or None.

        An option that needs more cartridge than the dump has says so:

            "grow": {"size": 49152, "at": "front", "fill": "0xFF"}

        `at: "front"` is a linear 7800 cartridge: it ends at $FFFF, so a larger
        one starts lower and the new bytes come before the old. `at: "end"`
        appends. Options that grow must agree on where and with what; the
        largest size wins. Sections in the new space describe the fill, so
        they are checked like any other.
        """
        want = [self.options[o]["grow"] for o in (options or self.options)
                if self.options[o].get("grow")]
        if not want:
            return None
        ats = {g.get("at", "front") for g in want}
        fills = {h(g.get("fill"), 0xFF) & 0xFF for g in want}
        if len(ats) > 1 or len(fills) > 1:
            raise PatchSetError("options grow the cartridge in different ways "
                                "(at %s, fill %s)" % (sorted(ats), sorted(fills)))
        return max(h(g["size"]) for g in want), ats.pop(), fills.pop()

    def layouts(self):
        """Every body this bundle can meet, as (size, base): the dump as it
        was, and the dump grown by any option. A grown cartridge is found by
        the same anchors, at their addresses in the grown body."""
        size0 = self.m["target"]["body_size"]
        out = [(size0, self.base0)]
        for o in self.options:
            g = self.options[o].get("grow")
            if g:
                n = h(g["size"])
                base = self.base0 - (n - size0) if g.get("at", "front") == "front" \
                    else self.base0
                if (n, base) not in out:
                    out.append((n, base))
        return out

    def grow(self, body, size, at, fill, header=b""):
        """The body grown to `size`; the base moves with it. A header that
        declares a cartridge type with anything at $4000-$7FFF refuses."""
        if len(body) >= size:
            return body
        hd = parse_a78(header)
        if hd and at == "front" and hd["cart_type"] & A78_BUSY_4000:
            raise PatchSetError(
                "this cartridge's header declares type $%04X, which puts "
                "something at $4000 or banks the image; growing it to %dK "
                "would collide with that" % (hd["cart_type"], size // 1024))
        pad = bytearray([fill]) * (size - len(body))
        if at == "front":
            self.base -= len(pad)
            return pad + body
        return body + pad

    def grown_view(self, body):
        """For reporting: the body as the bundle's growth would leave it, so
        sections in the new space can be judged on an ungrown dump."""
        g = self.grows()
        if not g or len(body) >= g[0]:
            return body
        return self.grow(bytearray(body), *g)      # moves self.base with it

    # ------------------------------------------------------------- the target
    def find_body(self, blob):
        """Where the body starts, decided by ground nobody patches.

        The obvious identity for a target is a hash of the whole thing, and it
        is the wrong one here: it is true of exactly one file, the pristine
        dump, and false of every ROM this bundle has ever produced. Ask it
        whether a half-patched cartridge is Karateka and it says no.

        So identity comes from **anchors**: extents that no option in the
        bundle touches and no float can be placed in. They are as true after
        patching as before, they pin the header size at the same time -- the
        offset where the anchors line up is where the body starts -- and they
        cost a few hundred bytes of checksum.

        `body_sha256` stays, demoted to a note: it says whether this dump is
        the untouched one, which is worth reporting and is not what decides
        whether the bundle applies.
        """
        want = self.m["target"]["body_size"]
        digest = self.m["target"].get("body_sha256")
        anchors = self.m["target"].get("anchors", [])
        best = None
        declared = [h(x) for x in self.m["target"].get("headers", [0])]
        for size, base in self.layouts():
            candidates = [h(x) for x in self.m["target"].get("headers", [0])]
            if len(blob) - size > 0:
                candidates.append(len(blob) - size)
            for off in dict.fromkeys(candidates):
                if off < 0 or off + size > len(blob):
                    continue
                body = bytearray(blob[off:off + size])
                hit = sum(1 for a in anchors if self.anchor_ok(body, a, base))
                # A grown cartridge also matches as the ungrown dump read from
                # its tail (anchors are in the old space, which moved up
                # intact). Ties go to a body that starts at a declared header
                # size, then to the larger body.
                key = (hit, off in declared, size)
                if best is None or key > best[0]:
                    best = (key, off, body, base)
        if best is None:
            raise PatchSetError(
                "no %d-byte body anywhere in this file; the bundle is for %s"
                % (want, self.m["target"].get("what", "another cartridge")))
        (hit, _at_header, _size), off, body, self.base = best
        if anchors and hit < len(anchors):
            raise PatchSetError(
                "this is not the cartridge the bundle is for.\n"
                "  %d of %d anchors match at the best offset (%d).\n"
                "  Anchors are extents no option touches, so a patched copy "
                "still matches all of them;\n  failing them means a different "
                "game, a different revision, or a different dump."
                % (hit, len(anchors), off))
        self.pristine = (digest is None
                         or hashlib.sha256(bytes(body)).hexdigest() == digest)
        return blob[:off], body

    def anchor_ok(self, body, a, base=None):
        at = h(a["addr"]) - (self.base if base is None else base)
        if at < 0:
            return False
        return crc32(bytes(body[at:at + a["length"]])) == h(a["crc32"])

    def section_bytes(self, body, sid):
        s = self.sections[sid]
        at = h(s["addr"]) - self.base
        if at < 0 or at + s["length"] > len(body):
            raise PatchSetError(
                "section %s ($%04X) is outside this %d-byte body; the option "
                "that uses it has to grow the cartridge first"
                % (sid, h(s["addr"]), len(body)))
        return at, body[at:at + s["length"]]

    def check_sections(self, body, ids):
        """Every named section still holds what it did when this was built."""
        bad = []
        for sid in ids:
            _at, data = self.section_bytes(body, sid)
            want = h(self.sections[sid]["crc32"])
            if crc32(bytes(data)) != want:
                bad.append((sid, want, crc32(bytes(data))))
        return bad

    # ----------------------------------------------------- patches and spans
    def entries(self, option):
        """An option's patches, normalised to (sections, bps, volatile, before).

        A patch may cover more than one section. The sections it covers are its
        **span**, and the BPS is a delta over their bytes concatenated in
        address order -- so one rewrite that reaches across three separate
        places is one patch and one pair of checksums, rather than three that
        have to be kept in step by hand.

        Three shapes are accepted, because a manifest is written by people:

            "patches": {"s_A20C": "p/x.bps"}                  one section
            "patches": {"s_A20C": {"bps": "...", ...}}        one, with detail
            "patches": [{"sections": ["s_A20C", "s_A25E"],    a span
                         "bps": "...", "before": "0x..."}]
        """
        raw = self.options[option].get("patches", {})
        items = raw if isinstance(raw, list) else [
            (dict(v, sections=[k]) if isinstance(v, dict)
             else {"sections": [k], "bps": v}) for k, v in raw.items()]
        out = []
        for e in items:
            sids = e["sections"]
            sids = [sids] if isinstance(sids, str) else list(sids)
            sids.sort(key=lambda s: h(self.sections[s]["addr"]))
            before = e.get("before")
            if before is None:
                if len(sids) != 1:
                    raise PatchSetError(
                        "the patch %r in option %r spans %d sections, so it "
                        "needs a 'before' checksum: the span's pre-image "
                        "cannot be worked out from the sections' own."
                        % (e.get("bps"), option, len(sids)))
                before = self.sections[sids[0]]["crc32"]
            out.append((tuple(sids), e["bps"],
                        [tuple(v) for v in e.get("volatile", [])], h(before)))
        return out

    def all_sections(self, option):
        return sorted({s for sids, _b, _v, _c in self.entries(option)
                       for s in sids})

    def span_crc(self, sids):
        """The CRC32 of a span's pre-image, from its sections' CRCs alone."""
        crc = 0
        for sid in sids:
            s = self.sections[sid]
            crc = crc32_combine(crc, h(s["crc32"]), s["length"])
        return crc

    def span_bytes(self, body, sids):
        """A span's bytes, concatenated, plus where each piece came from."""
        where, data = [], bytearray()
        for sid in sids:
            at, chunk = self.section_bytes(body, sid)
            where.append((at, len(chunk)))
            data += chunk
        return where, data

    def write_span(self, body, where, data):
        i = 0
        for at, n in where:
            body[at:at + n] = data[i:i + n]
            i += n

    # --------------------------------------------------- already applied?
    def crcs(self, member):
        head = bps.read_header(self.read(member))
        return head["source_crc"], head["target_crc"]

    def entry_state(self, body, entry):
        """applies / applied / blocked, for one patch over its whole span.

        Judged against what *this patch* expects, not against the sections'
        original contents: for a patch built on another's result those differ,
        and using the pristine value refuses the chain at the last moment.
        """
        sids, member, volatile, _before = entry
        _where, data = self.span_bytes(body, sids)
        src, tgt = self.crcs(member)
        if crc32(bytes(data)) == src:
            return "applies"
        probe = bytearray(data)
        for off, n in volatile:
            # Bytes a float wrote its address into. The patch left a
            # placeholder there and the fixup overwrote it, so comparing them
            # against the patch's own target would say "not applied" about a
            # cartridge that plainly is.
            for i in range(off, off + n):
                probe[i] = 0xFF
        if crc32(bytes(probe)) == tgt:
            return "applied"
        # A patch built on this one's result may have been applied on top:
        # then the span holds *its* target, and this patch is applied
        # underneath it. Follow the chain back from what is there. Without
        # this, a chained result reads the lower option as "blocked", and
        # applying the same selection again refuses a cartridge it made.
        if tgt in self._chain_back(sids, crc32(bytes(data))):
            return "applied"
        return "blocked"

    def _closure(self, option):
        """Every option this one needs, directly or through another."""
        seen, todo = set(), [option]
        while todo:
            for r in self.needs(todo.pop()):
                if r not in seen:
                    seen.add(r)
                    todo.append(r)
        return seen

    def _chain_forward(self, sids, crc, options):
        """Every state these options' patches over the span can take it to."""
        fwd = {}
        for o in options:
            for e_sids, member, _v, _b in self.entries(o):
                if e_sids == tuple(sids):
                    src, tgt = self.crcs(member)
                    fwd.setdefault(src, set()).add(tgt)
        seen, todo = {crc}, [crc]
        while todo:
            for tgt in fwd.get(todo.pop(), ()):
                if tgt not in seen:
                    seen.add(tgt)
                    todo.append(tgt)
        return seen

    def _chain_back(self, sids, crc):
        """Every state this span passed through on the way to `crc`, by
        following each patch over the span from its target to its source."""
        back = {}
        for o in self.options:
            for e_sids, member, _v, _b in self.entries(o):
                if e_sids == tuple(sids):
                    src, tgt = self.crcs(member)
                    back.setdefault(tgt, set()).add(src)
        seen, todo = set(), [crc]
        while todo:
            for src in back.get(todo.pop(), ()):
                if src not in seen:
                    seen.add(src)
                    todo.append(src)
        return seen

    def float_ok(self, body, f):
        """Is this float's blob really the one at the address it was given?

        Sections cover the code that calls a float; nothing covers the float
        itself, and two options can differ *only* inside one -- two knockback
        strengths whose call sites are byte-identical and whose blobs differ in
        one operand. Judged on sections alone, both report "already applied"
        about a cartridge that has the other, and the patcher offers to apply
        something that is a no-op.

        The fixup already wrote the address somewhere known, so it can be read
        back and the blob checked where it actually landed. Only `abs16` fixups
        carry a whole address; with anything narrower this abstains rather than
        guessing, and returns True.
        """
        if "crc32" not in f:
            return True
        site = None
        for fx in f.get("fixups", []):
            if fx["encode"] == "abs16":
                site = h(fx["addr"]) - self.base
                break
        if site is None or site + 1 >= len(body):
            return True
        at = (body[site] | (body[site + 1] << 8)) - self.base
        if at < 0 or at + f["length"] > len(body):
            return False
        return crc32(bytes(body[at:at + f["length"]])) == h(f["crc32"])

    def option_state(self, body, option):
        es = self.entries(option)
        if not es:
            return "applies"
        states = {self.entry_state(body, e) for e in es}
        if states == {"applies"}:
            return "applies"
        if states == {"applied"}:
            floats = self.options[option].get("floats", [])
            if all(self.float_ok(body, f) for f in floats):
                return "applied"
            # the call sites carry this option and the code they call does not,
            # which means a rival option's float is sitting there
            return "blocked"
        if states <= {"applies", "applied"}:
            return "partial"
        return "blocked"

    # ------------------------------------------------------- dependencies
    def derived_requires(self):
        """Dependencies read out of the patches, not out of a declaration.

        A patch says what it expects to find and what it leaves behind: a BPS
        header carries both CRCs. So for one span there is a little map of
        state to cause -- the pre-image was caused by nobody, and every
        option's target CRC by that option.

        A patch whose *source* CRC is another option's *target* CRC over the
        same span was authored on top of that option and cannot apply without
        it. Nobody writes that down, and unlike a written claim it cannot be
        wrong.

        It only works where two options describe the same span, which is why
        sections belong to the bundle rather than to an option. Widening a
        section, or spanning several, costs nothing: a patch changes only the
        bytes it means to however much it covers.
        """
        produced = {}
        for o in self.options:
            for sids, member, _v, _before in self.entries(o):
                produced.setdefault((sids, self.crcs(member)[1]),
                                    set()).add(o)
                # the pre-image, caused by nobody: from the sections' own
                # CRCs, not from a patch's `before`. Taking it from `before`
                # kept whichever patch over the span came last, so once two
                # options shared a span, the first one's start looked like a
                # state nothing produces.
                produced.setdefault((sids, self.span_crc(sids)), set())
        requires, unknown = {}, {}
        for o in self.options:
            deps = set()
            for sids, member, _v, _before in self.entries(o):
                key = (sids, self.crcs(member)[0])
                if key not in produced:
                    unknown.setdefault(o, []).append("+".join(sids))
                    continue
                deps |= produced[key] - {o}
            requires[o] = deps
        return requires, unknown

    def needs(self, option):
        """Everything an option needs: what it says, and what its bytes prove."""
        derived, _unknown = self._deps_cached()
        return set(self.options[option].get("requires", [])) | derived[option]

    def _deps_cached(self):
        if getattr(self, "_deps", None) is None:
            self._deps = self.derived_requires()
        return self._deps

    def survey(self, body):
        """Every option's state, with knob rivalry taken into account.

        Without this step the report is honest and useless. Put dose-4 on a
        cartridge and dose-5 comes back "half applied -- needs a human", which
        sounds like damage and is not: the two are settings of one knob, three
        of dose-5's waits are already skipped and one it does not want is too.
        The sections cannot tell those apart, because to them a byte is right
        or wrong. The knob can.
        """
        out = {}
        for o in self.options:
            if self.entries(o):
                out[o] = self.option_state(body, o)
        # An option built on another's result reads as blocked until that one
        # has run -- which `apply` does first. For the report, it applies if
        # each blocked span of it gets to its starting point through patches
        # of the options it needs, and those can apply themselves.
        changed = True
        while changed:
            changed = False
            for o, st in out.items():
                if st != "blocked":
                    continue
                deps = self._closure(o)
                if not all(out.get(d, "applies") in ("applies", "applied")
                           for d in deps):
                    continue
                ok = True
                for e in self.entries(o):
                    if self.entry_state(body, e) != "blocked":
                        continue
                    sids, member = e[0], e[1]
                    now = crc32(bytes(self.span_bytes(body, sids)[1]))
                    if self.crcs(member)[0] not in self._chain_forward(sids, now, deps):
                        ok = False
                        break
                if ok:
                    out[o] = "applies"
                    changed = True
        on = {o for o, st in out.items() if st == "applied"}
        knobs_on = {self.options[o].get("knob") for o in on}
        knobs_on.discard(None)
        for o, st in list(out.items()):
            if st in ("partial", "blocked") and o not in on                     and self.options[o].get("knob") in knobs_on:
                out[o] = "superseded"
        return out


    # ------------------------------------------------------------- selection
    def resolve(self, wanted):
        """Expand requirements, then refuse a selection that cannot mean one thing."""
        for w in wanted:
            if w not in self.options:
                raise PatchSetError("no option %r; try `list`" % w)
        _derived, unknown = self._deps_cached()
        chosen, queue = [], list(wanted)
        while queue:
            o = queue.pop(0)
            if o in chosen:
                continue
            if o in unknown:
                raise PatchSetError(
                    "%s expects section(s) %s to be in a state nothing in this "
                    "bundle produces. It was built against a ROM this bundle "
                    "does not describe, so there is no order that makes it "
                    "apply." % (o, ", ".join(sorted(unknown[o]))))
            chosen.append(o)
            queue.extend(self.needs(o))
        # dependencies first, so a fix lands on the bytes it expects
        order = []
        while chosen:
            free = [o for o in chosen
                    if all(r not in chosen for r in self.needs(o))]
            if not free:
                raise PatchSetError("these options require each other in a "
                                    "circle: %s" % ", ".join(sorted(chosen)))
            free.sort()
            for o in free:
                order.append(o)
                chosen.remove(o)
        # `free` is the set whose requirements are all already out of `chosen`,
        # so appending it puts dependencies first. An earlier version reversed
        # this, which was invisible while every patch started from the pristine
        # bytes and wrong the moment one started from another patch's result.

        by_knob, problems = {}, []
        for o in order:
            k = self.options[o].get("knob")
            if k:
                by_knob.setdefault(k, []).append(o)
        for k, os_ in by_knob.items():
            if len(os_) > 1:
                problems.append(
                    "%s are alternatives -- each sets %r a different way, so "
                    "asking for both is a choice not yet made"
                    % (" and ".join(sorted(os_)), k))
        # Section clashes between two settings of one knob are the same fact
        # said again; reporting both buries the sentence that helps.
        clashing_knobs = {k for k, v in by_knob.items() if len(v) > 1}

        def ancestors(o, seen=None):
            seen = set() if seen is None else seen
            for r in self.needs(o):
                if r not in seen:
                    seen.add(r)
                    ancestors(r, seen)
            return seen

        owner, said = {}, set()
        for o in order:
            for sid in self.all_sections(o):
                prev = owner.get(sid)
                if prev is not None:
                    # Two options on one section is a clash only when neither
                    # is built on the other. When one is, they are a chain --
                    # which is the whole point of reading dependencies out of
                    # the checksums, so treating it as a clash would refuse
                    # exactly the case the derivation exists to find.
                    if prev in ancestors(o) or o in ancestors(prev):
                        owner[sid] = o
                        continue
                    pair = tuple(sorted((prev, o)))
                    knob = self.options[o].get("knob")
                    if pair in said or knob in clashing_knobs:
                        owner[sid] = o
                        continue
                    said.add(pair)
                    problems.append(
                        "%s and %s both rewrite section %r; one would land on "
                        "top of the other" % (prev, o, sid))
                owner[sid] = o
        if problems:
            raise PatchSetError("\n".join(problems))
        return order

    # ----------------------------------------------------------------- floats
    def place(self, body, floats, taken):
        """Find room for each float, and never the same room twice."""
        out = {}
        for f in floats:
            n, fill = f["length"], h(f.get("fill"), 0) & 0xFF
            align = f.get("align", 1)
            found = None
            for rng in f["search"]:
                lo = h(rng["addr"]) - self.base
                hi = lo + rng["length"]
                spots = range(hi - n, lo - 1, -1) \
                    if f.get("from", "end") == "end" else range(lo, hi - n + 1)
                for at in spots:
                    if (at + self.base) % align:
                        continue
                    if any(not (at + n <= a or b <= at) for a, b in taken):
                        continue
                    if all(x == fill for x in body[at:at + n]):
                        found = at
                        break
                if found is not None:
                    break
            if found is None:
                raise PatchSetError(
                    "no room for float %r: it needs %d consecutive bytes of "
                    "$%02X and there are none left in %s"
                    % (f["id"], n, fill,
                       ", ".join("$%04X+%d" % (h(r["addr"]), r["length"])
                                 for r in f["search"])))
            taken.append((found, found + n))
            out[f["id"]] = found + self.base
        return out

    def fixup(self, body, at, value, encode):
        if encode == "lo8":
            body[at] = value & 0xFF
        elif encode == "hi8":
            body[at] = (value >> 8) & 0xFF
        elif encode == "abs16":
            body[at] = value & 0xFF
            body[at + 1] = (value >> 8) & 0xFF
        else:
            raise PatchSetError("unknown fixup encoding %r; known: %s"
                                % (encode, ", ".join(ENCODINGS)))

    # ------------------------------------------------------------------ apply
    def apply(self, blob, wanted, report=None):
        say = report if report is not None else (lambda _s: None)
        header, body = self.find_body(blob)
        order = self.resolve(wanted)
        say("applying %d option(s): %s" % (len(order), ", ".join(order)))
        header = bytearray(header)
        g = self.grows(order)
        if g and len(body) < g[0]:
            before = len(body)
            body = self.grow(body, g[0], g[1], g[2], bytes(header))
            say("  cartridge grown from %dK to %dK (%s, filled with $%02X); the "
                "body now starts at $%04X"
                % (before // 1024, len(body) // 1024, g[1], g[2], self.base))
        if header:
            if set_a78_size(header, len(body)):
                say("  %d-byte header kept, its ROM size set to %d"
                    % (len(header), len(body)))
            elif parse_a78(header) is None and len(header) > A78_HEADER:
                # e.g. a cartridge another bundle grew: this bundle's body is
                # its tail, and what comes before is carried through untouched
                say("  the %d bytes before the body kept as they were"
                    % len(header))
            else:
                say("  %d-byte header kept as it was" % len(header))
        if not self.pristine:
            say("  not the pristine dump, which is fine: the anchors say it "
                "is the right game, and")
            say("  each option decides for itself whether its own bytes are "
                "still where it left them.")

        # Each option is judged against the cartridge as it stands when its
        # turn comes, not against the one that arrived. A chained option --
        # one whose patch starts from another's result -- reads as "blocked"
        # before its predecessor has run and "applies" after, and evaluating
        # everything up front would refuse exactly the case the derived
        # dependencies exist to support.
        # judged on the grown view, so an option with sections in space this
        # cartridge does not have yet reads as applicable rather than failing
        keep = self.base
        initial = self.survey(self.grown_view(body))
        self.base = keep
        todo, taken = [], []
        for o in order:
            st = self.option_state(body, o)
            if st == "applied":
                say("  %-16s already applied; left alone" % o)
                continue
            if st != "applies":
                if initial.get(o) == "superseded":
                    rival = sorted(x for x in self.options
                                   if x != o
                                   and self.options[x].get("knob")
                                   == self.options[o].get("knob")
                                   and initial.get(x) == "applied")
                    raise PatchSetError(
                        "  %s cannot apply: %s already on this cartridge, and "
                        "they are two%s  settings of %r. Start from an "
                        "unpatched ROM to change your mind about it."
                        % (o, _andlist(rival), NL2,
                           self.options[o].get("knob")))
                if st == "partial":
                    raise PatchSetError(
                        "  %s is half applied: some of its sections carry it "
                        "and some do not.%s  Something edited this ROM by "
                        "hand, and no automatic answer is right." % (o, NL2))
                blocked = [e for e in self.entries(o)
                           if self.entry_state(body, e) == "blocked"]
                who = [_span_name(e[0]) for e in blocked]
                # A span is judged as a whole, so a clash in one section of a
                # 33-section span names all 33. The option is not on this
                # cartridge (that was "applied" or "partial" above), so a
                # section that no longer holds the dump's bytes is the one
                # something else changed.
                moved = [sid for e in blocked for sid in e[0]
                         if crc32(bytes(self.section_bytes(body, sid)[1]))
                         != h(self.sections[sid]["crc32"])]
                raise PatchSetError(
                    "  %s cannot apply: the bytes it covers (%s) hold neither "
                    "what it expects nor%s  what it would write. Something "
                    "else has changed them%s." % (
                        o, "; ".join(who), NL2,
                        ": %s" % ", ".join(
                            "%s ($%04X, %d bytes)" % (sid, h(self.sections[sid]["addr"]),
                                                      self.sections[sid]["length"])
                            for sid in moved)
                        if moved and len(moved) < sum(len(e[0]) for e in blocked)
                        else ""))
            todo.append(o)
            for sids, member, _vol, _before in self.entries(o):
                where, before = self.span_bytes(body, sids)
                after, _warn = bps.apply(bytes(before), self.read(member))
                if len(after) != len(before):
                    raise PatchSetError(
                        "option %r changes span %s from %d bytes to %d; a "
                        "section is a fixed extent, and code that grows goes "
                        "in a float" % (o, "+".join(sids), len(before),
                                        len(after)))
                self.write_span(body, where, after)
                taken.extend((at, at + n) for at, n in where)
                say("  %-16s -> %-22s %d bytes" % (o, _span_name(sids), len(after)))
        if not todo:
            say("  nothing left to do -- this cartridge already has all of it")
            return bytes(header) + bytes(body)

        floats = [f for o in todo for f in self.options[o].get("floats", [])]
        placed = self.place(body, floats, taken)
        for f in floats:
            at = placed[f["id"]] - self.base
            blobf = self.read(f["blob"])
            if len(blobf) > f["length"]:
                raise PatchSetError("float %r is %d bytes but claims %d"
                                    % (f["id"], len(blobf), f["length"]))
            body[at:at + len(blobf)] = blobf
            say("  float %-10s at $%04X (%d bytes)"
                % (f["id"], placed[f["id"]], len(blobf)))
            for fx in f.get("fixups", []):
                site = h(fx["addr"]) - self.base
                if "expect" in fx:
                    want = h(fx["expect"])
                    n = 2 if fx["encode"] == "abs16" else 1
                    got = (body[site] | (body[site + 1] << 8)) if n == 2 \
                        else body[site]
                    if got != want:
                        raise PatchSetError(
                            "fixup for float %r at $%04X expected $%04X and "
                            "found $%04X -- the patch that should have left a "
                            "placeholder there did not"
                            % (f["id"], h(fx["addr"]), want, got))
                self.fixup(body, site, placed[f["id"]], fx["encode"])
        return bytes(header) + bytes(body)

def _span_name(sids):
    """A span for a message: its sections, or first..last past six."""
    return "+".join(sids) if len(sids) <= 6 else (
        "%d sections, %s..%s" % (len(sids), sids[0], sids[-1]))


# --------------------------------------------------------------------- headers
def parse_a78(header):
    """The fields growth cares about, from a 128-byte .a78 header, or None."""
    if len(header) != A78_HEADER or bytes(header[1:10]) != b"ATARI7800":
        return None
    return {"rom_size": int.from_bytes(bytes(header[49:53]), "big"),
            "cart_type": int.from_bytes(bytes(header[53:55]), "big")}


def set_a78_size(header, size):
    """Make a .a78 header's ROM size (bytes 49-52, big-endian, excluding the
    header) say `size`. True if it changed. A grown body under a header that
    still says 32K is read as 32K by an emulator, which then maps it wrong."""
    if parse_a78(header) is None:
        return False
    new = size.to_bytes(4, "big")
    if bytes(header[49:53]) == new:
        return False
    header[49:53] = new
    return True


# --------------------------------------------------------------------- making
def write_bundle(out, manifest, files):
    """Write a bundle: the manifest, plus every member it names.

    A zip rather than a directory, because a bundle is a thing you send
    someone, and because the manifest naming a file that is not there should be
    impossible rather than merely unlikely -- so it is checked here.
    """
    named = set()
    for o in manifest["options"]:
        raw = o.get("patches", {})
        for spec in (raw if isinstance(raw, list) else raw.values()):
            named.add(spec if isinstance(spec, str) else spec["bps"])
        for f in o.get("floats", []):
            named.add(f["blob"])
    if any(o.get("grow") for o in manifest["options"]):
        manifest["format"] = FORMAT_GROW       # see FORMAT_GROW
    missing = sorted(named - set(files))
    if missing:
        raise PatchSetError("the manifest names files that were not supplied: "
                            + ", ".join(missing))
    spare = sorted(set(files) - named)
    if spare:
        raise PatchSetError("these files are not named by the manifest: "
                            + ", ".join(spare))
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("patchset.json", json.dumps(manifest, indent=2) + "\n")
        for name in sorted(files):
            z.writestr(name, files[name])
    return out


# ------------------------------------------------------------------------ CLI
def cmd_list(ps):
    print("%s" % ps.m.get("name", "(unnamed)"))
    if ps.m.get("what"):
        print("  %s" % ps.m["what"])
    print("  target: %s, %d bytes, sha256 %s"
          % (ps.m["target"].get("what", "?"), ps.m["target"]["body_size"],
             ps.m["target"]["body_sha256"][:16] + "..."))
    print("")
    by_knob = {}
    for o in ps.m["options"]:
        by_knob.setdefault(o.get("knob") or "", []).append(o)
    for knob in sorted(by_knob, key=lambda k: (k == "", k)):
        if knob:
            print("  %s -- %s" % (knob, ps.m.get("knobs", {}).get(knob, "")))
            print("  (pick at most one)")
        else:
            print("  independent")
        for o in by_knob[knob]:
            req = o.get("requires", [])
            print("    %-18s %s%s%s" % (o["id"], o["title"],
                                        "   [needs %s]" % ", ".join(req)
                                        if req else "",
                                        "   [grows the cartridge to %dK]"
                                        % (h(o["grow"]["size"]) // 1024)
                                        if o.get("grow") else ""))
            if o.get("note"):
                for line in _wrap(o["note"], 66):
                    print("        %s" % line)
        print("")
    return 0


def cmd_info(ps):
    print("sections")
    for sid, s in sorted(ps.sections.items(),
                         key=lambda kv: h(kv[1]["addr"])):
        print("  %-18s $%04X + %-5d crc32 %08X   %s"
              % (sid, h(s["addr"]), s["length"], h(s["crc32"]),
                 s.get("what", "")))
    floats = [(o["id"], f) for o in ps.m["options"]
              for f in o.get("floats", [])]
    if floats:
        print("")
        print("floats -- placed wherever there is room, not at a fixed address")
        for oid, f in floats:
            where = ", ".join("$%04X+%d" % (h(r["addr"]), r["length"])
                              for r in f["search"])
            print("  %-18s %3d bytes of $%02X in %s   (%s)"
                  % (f["id"], f["length"], h(f.get("fill"), 0), where, oid))
            for fx in f.get("fixups", []):
                print("        writes its address at $%04X as %s"
                      % (h(fx["addr"]), fx["encode"]))
    return 0


def cmd_deps(ps):
    """The dependency graph, and how much of it the bytes actually support."""
    derived, unknown = ps.derived_requires()
    print("dependencies")
    print("")
    print("  proven -- one option's patch starts from another's result, so it")
    print("  cannot apply without it:")
    any_proven = False
    for o in sorted(derived):
        if derived[o]:
            any_proven = True
            print("     %-18s needs %s" % (o, ", ".join(sorted(derived[o]))))
    if not any_proven:
        print("     none: every patch starts from the pristine bytes")
    print("")
    print("  stated -- written in the manifest. Where the bytes do not also")
    print("  prove it, the claim is about meaning, not about applying:")
    any_stated = False
    for o in sorted(ps.options):
        said = set(ps.options[o].get("requires", []))
        if not said:
            continue
        any_stated = True
        for r in sorted(said):
            kind = "proven too" if r in derived.get(o, ())                 else "not proven -- it applies either way, but is pointless"
            print("     %-18s needs %-18s (%s)" % (o, r, kind))
    if not any_stated:
        print("     none")
    if unknown:
        print("")
        print("  built on something this bundle does not describe:")
        for o in sorted(unknown):
            print("     %-18s section(s) %s"
                  % (o, ", ".join(sorted(unknown[o]))))
        return 1
    return 0


def cmd_check(ps, rom):
    """What this bundle makes of a cartridge, patched or not."""
    blob = io.open(rom, "rb").read()
    header, body = ps.find_body(blob)
    print("%s" % os.path.basename(rom))
    print("  body at offset %d, %d bytes; anchors all match, so this is %s"
          % (len(header), len(body), ps.m["target"].get("what", "the target")))
    if len(body) != ps.m["target"]["body_size"]:
        print("  grown from the dump's %d bytes; it now starts at $%04X"
              % (ps.m["target"]["body_size"], ps.base))
    hd = parse_a78(header)
    if hd and hd["rom_size"] != len(body):
        print("  its header says %d bytes, not %d -- apply would correct it"
              % (hd["rom_size"], len(body)))
    body = ps.grown_view(body)
    print("  %s" % ("an untouched dump" if ps.pristine
                    else "modified -- which options is what follows"))
    print("")
    rows = {}
    for oid, st in ps.survey(body).items():
        rows.setdefault(st, []).append(oid)
    for state, what in (("applied", "already on this cartridge"),
                        ("applies", "can still be applied"),
                        ("superseded", "ruled out by a rival setting"),
                        ("partial", "half applied -- needs a human"),
                        ("blocked", "cannot apply; something else edited "
                                    "their bytes")):
        if rows.get(state):
            print("  %-32s %s" % (what, ", ".join(sorted(rows[state]))))
    return 1 if (rows.get("partial") or rows.get("blocked")) else 0


def cmd_apply(ps, rom, wanted, out):
    blob = io.open(rom, "rb").read()
    result = ps.apply(blob, wanted, report=lambda s: print(s))
    result, note = _resign(result, ps.m.get("target", {}).get("region"))
    io.open(out, "wb").write(result)
    print("")
    print("  wrote %s" % out)
    print("  %d bytes, sha256 %s"
          % (len(result), hashlib.sha256(result).hexdigest()[:16] + "..."))
    print("  %s" % note)
    return 0


def _resign(image, declared=None):
    """Give a freshly assembled image a cartridge signature.

    This is the one case a patch file cannot carry. An NTSC 7800 checks a
    signature over a hash of the whole cartridge, so every combination of
    options has a different one and there is nothing to precompute -- it
    has to be computed here, at apply time, or the result boots a real
    console in 2600 mode rather than refusing.

    A cartridge whose header bytes rule signing out is reported rather
    than failed: the patch itself is still good, and on a PAL console or
    an emulator it makes no difference at all."""
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    try:
        import sign7800
    except ImportError:
        return image, "signature: not checked (tools/sign7800.py missing)"
    body = image[128:] if len(image) % 0x1000 == 128 else image
    head = image[:len(image) - len(body)]
    # PAL cartridges are never checked and carry no signature -- the block
    # is erased EPROM in the retail dumps -- so signing one writes 120
    # bytes over filler and changes the CRC for nothing.
    #
    # Where the region comes from matters. A headered .a78 declares it,
    # but a headerless dump does not, and it cannot be recovered from the
    # bytes: an unsigned NTSC homebrew and a PAL cartridge look identical
    # from the inside. So the *bundle* is asked first -- it knows which
    # cartridge it was built for even when the file handed to it has had
    # its header stripped.
    where = declared or sign7800.region(image)
    if where == "pal":
        return image, "signature: not needed, this is a PAL cartridge"
    try:
        out = head + sign7800.signed(body)
    except sign7800.SignError as e:
        return image, "signature: NOT SIGNED -- %s" % e
    if where:
        return out, "signature: valid for a real NTSC 7800"
    return out, ("signature: valid for a real NTSC 7800 -- assumed, since a "
                 "headerless image declares no region and this bundle does "
                 "not either")


def _andlist(names):
    """"a is", "a and b are", "a, b and c are" -- a report should read."""
    if len(names) == 1:
        return "%s is" % names[0]
    return "%s and %s are" % (", ".join(names[:-1]), names[-1])


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


def main():
    ap = argparse.ArgumentParser(
        description=__doc__.strip().split("\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command",
                    choices=["list", "info", "deps", "check", "apply"])
    ap.add_argument("bundle")
    ap.add_argument("--rom", help="the cartridge to check or patch")
    ap.add_argument("--with", dest="wanted", default="",
                    help="comma-separated option ids to apply")
    ap.add_argument("--out", help="where to write the patched cartridge")
    args = ap.parse_args()
    try:
        ps = PatchSet(args.bundle)
        if args.command == "list":
            return cmd_list(ps)
        if args.command == "info":
            return cmd_info(ps)
        if args.command == "deps":
            return cmd_deps(ps)
        if not args.rom:
            sys.stderr.write("%s needs --rom\n" % args.command)
            return 2
        if args.command == "check":
            return cmd_check(ps, args.rom)
        wanted = [w.strip() for w in args.wanted.split(",") if w.strip()]
        if not wanted:
            sys.stderr.write("apply needs --with <ids>; try `list`\n")
            return 2
        if not args.out:
            sys.stderr.write("apply needs --out\n")
            return 2
        return cmd_apply(ps, args.rom, wanted, args.out)
    except (PatchSetError, bps.PatchError, IOError, ValueError) as e:
        sys.stderr.write("%s\n" % e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
