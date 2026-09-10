# The .abp format: an anchored bundle of patches

A bundle of patches you pick from, checked a section at a time.
Read by [tools/patchset.py](../tools/patchset.py); written by any tool that can
emit JSON and BPS.

## Why it exists

A BPS is a delta between two whole files, carrying a CRC32 of the source, the
target and itself. That is exactly right for "here is my hack" and wrong for
"here are nine independent fixes, take the ones you want":

- Nine fixes are 512 combinations. Nine BPS files cannot express them; you
  build and ship every combination you are willing to let anyone have.
- A whole-file CRC refuses a dump that differs anywhere, including in a header
  the patch does not care about.
- Two patches touching the same bytes apply cleanly one after the other and
  silently produce a ROM that is neither.

Nothing standard covers it. **VCDIFF** (xdelta3) has windows with optional
per-window checksums, but windows are chosen by the compressor and mean nothing
to a person. **NINJA `.RUP`** and **BPM** bundle patches for several *files*;
neither addresses several regions of one file. **PPF 3.0** validates one
hardcoded block. So this is deliberately boring: JSON plus BPS files, in a zip
or a directory.

## The name

**`.abp`** -- an *anchored bundle of patches*, after the anchors that identify
the target without a whole-file hash.

There is no unclaimed three-letter extension left; that space was exhausted
decades ago. So the test is not "is it free". It is **is anything similar, or
anything popular, already using it in a way that would be mistaken for this** --
because those are the only collisions that cost anybody anything.

Checked against both halves:

**Nothing similar.** No patch format uses it. `.ips`, `.ups`, `.bps`, `.aps`,
`.ppf`, `.rup`, `.bpm` and `.xdelta` are the field, and `.abp` appears in none
of the romhacking patcher documentation, tool lists or format round-ups.

**Nothing popular.** The claimants are an AVS barcode profile, an Android
car-log backup, ArduBlock and Open Roberta project files, and an ABBYY OCR
database. Six of them, all niche, none plausibly registered on the machine of
somebody patching a cartridge.

The seventh claim was Adblock Plus, and it does not survive checking. Adblock
Plus filter lists are `.txt`; "ABP" is the project's name and `abp:` is a URI
scheme for subscribe links, not a file extension it writes. The extension
directories that list it are cross-copying each other. It was the one popular
name on the list, and it is not really on the list.

One honest reservation: `.abp` and `.bps` share two letters and are easy to
misread side by side in a folder -- and `.bps` is precisely the adjacent format.
That is tolerable because the confusion is loud rather than quiet: this reader
refuses a `.bps` as not being a zip, and a BPS patcher refuses an `.abp` for the
same reason in reverse. Neither can quietly do the wrong thing, and the family
resemblance is arguably worth the glance.

Runner-up was `.spb` (*section patch bundle*), with only two claimants -- a
Microsoft ESP flight-sim mission and a Samsung Kies phonebook backup -- but Kies
shipped on a great many Windows machines, which fails the "nothing popular"
half, and the name says less.

The file is a zip, so anything can open it.

## Layout

```
patchset.json          the manifest
p/<option>.<sec>.bps   one BPS per (option, section) pair
f/<float>.bin          raw bytes for each float
```

A zip or a directory; both read identically.

## The manifest

```json
{
  "format": "patchset/2",
  "name": "Karateka (NTSC) fixes",
  "what": "one sentence",
  "target": {
    "what": "the dump this was measured against",
    "body_size": 49152,
    "body_sha256": "...",
    "anchors": [{ "addr": "0x4400", "length": 256, "crc32": "0x..." }],
    "headers": [0, 128],
    "base": "0x4000"
  },
  "knobs": { "hit window": "how close a strike has to be to land" },
  "sections": {
    "s_A20C": { "addr": "0xA20C", "length": 6, "crc32": "0x551E684F" }
  },
  "options": [
    {
      "id": "generous-reach",
      "title": "same reach, and two units of slack for both",
      "knob": "hit window",
      "requires": ["input-latch"],
      "note": "prose the patcher shows next to the checkbox",
      "patches": {
        "s_A20C": { "bps": "p/generous-reach.s_A20C.bps", "volatile": [] }
      },
      "floats": [ ... ]
    }
  ]
}
```

Addresses may be `4096`, `"0x1000"` or `"$1000"` anywhere they appear.
`base` is the CPU address of `body[0]`, so a manifest reads in the addresses
you disassembled in rather than file offsets.

### Anchors: identity without a whole-file hash

A hash of the whole file is true of exactly one dump -- the pristine one -- and
false of every ROM the bundle produces. Ask it whether a half-patched cartridge
is Karateka and it says no, which is the wrong answer to a question the patcher
has to get right.

**Anchors** are extents no option touches and no float can be placed in. They
are as true after patching as before, so they identify the game rather than the
dump. They pin the header size at the same time: the offset at which every
anchor lines up is where the body starts.

Two things disqualify a candidate anchor, and the second matters more than it
looks: overlapping anything patchable, and low byte diversity. A run of zeroes
matches every cartridge ever made, so an anchor made of one would pass while
proving nothing -- a check that cannot fail is worse than no check.

`body_sha256` stays in the manifest, demoted to a note. It answers "is this the
untouched dump", which is worth reporting and is not what decides whether the
bundle applies.

### Sections

A named byte range and the CRC32 of its **pre-image**. A patch names the
section it edits, so applying it verifies six bytes rather than 49152.

Three consequences, and they are the point:

- A dump with a different header still passes.
- A ROM already patched *elsewhere* is still a valid target for options whose
  own sections nobody has touched.
- A section is a fixed extent. A BPS that would change its length is refused;
  growing code goes in a float.

CRC32 rather than SHA-256 because a section is a guard against applying to the
wrong bytes, not an identity, and CRC32 is what BPS itself uses.

### Already applied, and why the "after" checksum is free

A section that matches neither its pre-image nor anything else used to mean
"wrong ROM", which is the same answer for a cartridge that already has the fix
and one that has somebody else's. Those want different sentences and different
behaviour: skip the first, refuse the second.

So each patch also has an *after* state, and it needs no manifest field,
because **a BPS header already carries the CRC32 of its target**. A second copy
would only be a fact that can drift from the first. The patcher reads it from
the patch.

Three states per (option, section):

```
    crc(section) == section.crc32     applies
    crc(section) == bps.target_crc    already applied
    neither                           blocked, something else changed it
```

`volatile` covers the one case that breaks: bytes a float overwrote with the
address it was given. The patch left a placeholder there, so those ranges are
masked back to `0xFF` before comparing. Without it, every option carrying a
float would report "not applied" about a cartridge that plainly has it.

An option whose sections all read "applied" is skipped rather than reapplied,
which makes `apply` idempotent: running it twice gives the same bytes.

**Knob rivalry.** Put `dose-4` on a cartridge and `dose-5` reads "half
applied", which sounds like damage and is not: they are two settings of one
knob, some of dose-5's waits are already skipped, and one it does not want is
too. Sections cannot tell those apart, because to them a byte is right or
wrong. The knob can, so the report says "ruled out by a rival setting" and
names which one.


### Dependencies, read rather than declared

A BPS header carries the CRC32 of its source as well as its target. So for one
section there is a small map of state to cause: the pristine bytes were caused
by nobody, and each option's target CRC was caused by that option.

Then **a patch whose source CRC is another option's target CRC was authored on
top of that option**, and cannot apply without it. That is a dependency nobody
had to write down, and unlike a written one it cannot be wrong. `patchset.py
deps` prints both:

```
  proven -- one option's patch starts from another's result:
     louder             needs loud

  stated -- written in the manifest. Where the bytes do not also
  prove it, the claim is about meaning, not about applying:
     dose-4             needs input-latch   (not proven -- it applies either
                                             way, but is pointless)
```

Those two really are different things and the report should not blur them.
Karateka's doses genuinely apply without the input latch; they are just not
worth having without it. A chain, by contrast, is arithmetic.

**Three consequences the implementation has to honour**, each of which was a bug
before the test caught it:

- Two options on one section is a clash *unless* one is built on the other, in
  which case it is a chain. Refusing it would reject exactly the case the
  derivation exists to find.
- Dependencies apply first. That is invisible while every patch starts from the
  pristine bytes, and wrong the moment one starts from another's result.
- Applicability is judged per *patch*, against what that patch expects, not
  against the section's original CRC -- and against the cartridge as it stands
  when the option's turn comes, not as it arrived.

A patch whose source CRC matches nothing in the bundle was built against a ROM
the bundle does not describe. There is no order that makes it apply, so it is
refused by name rather than left to fail later.

**This is what "enlarge the segment" buys.** Comparing one option's source
against another's target only works where both describe the *same extent*,
which is why sections belong to the bundle rather than to an option. Widening a
section so two options share it costs nothing: a patch still changes only the
bytes it means to, however much of the section it spans.

### Knobs

Each option may declare one thing it has an opinion about. Two options
declaring the same knob are **alternatives**: asking for both is refused as a
choice not yet made, rather than resolved by file order. `requires` names
options that must come first, and they are pulled in and ordered automatically.

This is the part a checkbox UI needs: knobs are radio groups, everything else
is a checkbox, and `requires` greys things in and out.

### Floats

Code that needs somewhere to live, without hardcoding where:

```json
{
  "id": "sampler",
  "length": 28,
  "fill": 0,
  "from": "end",
  "align": 1,
  "blob": "f/sampler.bin",
  "search": [{ "addr": "0xFF40", "length": 64 },
             { "addr": "0xA6D0", "length": 6448 }],
  "fixups": [{ "addr": "0x5946", "encode": "abs16", "expect": "0xFFFF" }]
}
```

The patcher scans the search ranges for `length` consecutive bytes equal to
`fill`, skipping anything already claimed by a section patch or an earlier
float, and writes the blob there. Then every fixup site gets the address it
chose: `lo8`, `hi8` or `abs16`.

`expect` is what should be at the site beforehand. The section patch is
expected to leave a placeholder there, so a float whose call site was not
prepared fails loudly instead of writing an address nobody reads.

`from: "end"` packs floats toward the end of a range, leaving the front
contiguous. `align` constrains the chosen address in CPU terms.

`crc32` is the blob's own checksum, and it closes a hole. Sections cover the
code that *calls* a float; nothing covers the float itself, so two options
differing only inside one -- two knockback strengths with byte-identical call
sites and blobs differing in a single operand -- both report "already applied"
about a cartridge carrying the other, and the patcher offers a no-op. The fixup
already wrote the address down, so the blob is read back from where it actually
landed and checked. Only `abs16` fixups carry a whole address; with anything
narrower the check abstains rather than guessing.

> The natural way to describe this is "find sixteen bytes with a CRC of zero".
> The implementation tests the bytes instead, because CRC32 of sixteen zero
> bytes is `0x8B9F9C56`, not zero. Same intent, honest arithmetic.

**This is not a nicety.** Its first use found a real bug: Karateka's input-latch
fix hardcoded a 28-byte routine at `$FF80` and a 17-byte one at `$FFA0`, and
neither address is free — `$FF80`–`$FFF9` holds high-entropy data immediately
below the 6502 vectors, which on a 7800 is where the BIOS signature block sits.
Emulators do not check it, so every build ran. The float found the 64 genuinely
free bytes at `$FF40` and put both routines there instead.

### Headers

`target` describes the **body**: its size, its anchors, and (as a note) its SHA-256. The patcher tries the
offsets in `headers`, plus `len(file) - body_size`, and takes the one whose
hash matches. Whatever preceded the body is handed back untouched, so a
128-byte `.a78` and a bare `.bin` take the same bundle.

The offset is chosen by where the **anchors** line up, which works identically on
a pristine and a patched dump. That matters more than it sounds: reading a
128-byte header as if it were code is an easy way to produce a confident, wrong
answer, and it is exactly what happened before anchors existed.
produce a confident, wrong answer.

## Applying

```
python tools/patchset.py list   game.abp
python tools/patchset.py info   game.abp
python tools/patchset.py check  game.abp --rom game.a78
python tools/patchset.py apply  game.abp --rom game.a78 \
    --with dose-4,generous-reach,remap --out fixed.a78
```

`check` works on a modified ROM and says which options are still applicable and
which are already on. `apply` resolves requirements, refuses an incoherent
selection, verifies only the sections it needs, patches them, places floats and
fixes up their callers.

Applying two options in one go and one after the other give byte-identical
results. The self-test pins that, because if those paths ever diverge the
sections are not independent and the format is lying.

## What it does not do

**Semantic clashes.** Two options can edit disjoint bytes and still fight — a
Karateka fix was withdrawn for putting stance change on a stick direction that
another part of the game reads as an attack height. Nothing here catches that;
only reading the code does.

**Length changes.** A section is a fixed extent. Code that grows goes in a
float.

**Ordering beyond `requires`.** Options are applied dependencies-first and
otherwise in a stable order. If two options need a specific relative order and
do not overlap, say so with `requires`.
