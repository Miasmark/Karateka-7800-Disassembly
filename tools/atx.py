#!/usr/bin/env python3
"""
Read an ATX floppy image: sectors out, and what is actually on the disk.

    python tools/atx.py game.atx                 what is there
    python tools/atx.py game.atx --atr out.atr   a plain image other tools read
    python tools/atx.py game.atx --boot          the boot record, disassembled
    python tools/atx.py game.atx --extract DIR   the files, if it has a directory

ATX exists because ATR does not: it keeps each sector's angular position and
status flags, which is how Atari 8-bit copy protection worked -- duplicate
sector numbers, deliberate CRC errors, sectors timed so a copier cannot write
them back. A plain image throws that away, which is why protected disks
circulate as ATX.

None of that matters for reading the program off the disk, and this does not
try to emulate a drive. It takes the good copy of each sector and hands back
the data.

## What "extract the files" means on an Atari disk

Usually nothing. A game boots from sector 1 with its own loader and has no
directory at all -- the "filesystem" is a boot record saying "load this many
sectors starting here and jump". `--extract` handles a DOS 2 disk when there is
one and says so plainly when there is not, rather than inventing files.
"""
import argparse
import io
import os
import struct
import sys

MAGIC = b"AT8X"


class AtxError(Exception):
    pass


def read_atx(path):
    """Sectors from an ATX image, as {sector number: bytes}.

    Where a sector appears more than once -- which on a protected disk is the
    protection, not damage -- the copy without an error flag wins. That is the
    one a real drive would eventually return, and the one the game's loader is
    written to get.
    """
    raw = io.open(path, "rb").read()
    if raw[:4] != MAGIC:
        raise AtxError("%s is not an ATX image (no AT8X magic)"
                       % os.path.basename(path))
    start = struct.unpack_from("<I", raw, 0x1C)[0]
    density = raw[0x12] if len(raw) > 0x12 else 0
    size = 128 if density != 2 else 256

    sectors, dupes, bad = {}, 0, 0
    pos = start
    while pos + 32 <= len(raw):
        rec_size = struct.unpack_from("<I", raw, pos)[0]
        rec_type = struct.unpack_from("<H", raw, pos + 4)[0]
        if rec_size == 0:
            break
        if rec_type != 0:                       # not a track record
            pos += rec_size
            continue
        track = raw[pos + 8]
        count = struct.unpack_from("<H", raw, pos + 10)[0]
        list_off = struct.unpack_from("<I", raw, pos + 20)[0]
        if list_off:
            lp = pos + list_off
            entries = lp + 8                    # past the sector-list header
            for i in range(count):
                e = entries + i * 8
                if e + 8 > len(raw):
                    break
                num = raw[e]
                status = raw[e + 1]
                data_off = struct.unpack_from("<I", raw, e + 4)[0]
                if not num:
                    continue
                # 0x08 = CRC error, 0x10 = record not found, 0x20 = deleted
                broken = bool(status & 0x38)
                absolute = track * 18 + num     # 18 sectors a track, SD
                blob = raw[pos + data_off:pos + data_off + size]
                if len(blob) < size:
                    blob = blob + b"\x00" * (size - len(blob))
                if absolute in sectors:
                    dupes += 1
                    if broken:
                        continue                # keep the good copy
                if broken:
                    bad += 1
                sectors[absolute] = blob
        pos += rec_size
    return sectors, {"density": density, "sector_size": size,
                     "duplicates": dupes, "flagged": bad}


def to_atr(sectors, size):
    """A plain ATR, which every other Atari tool can read."""
    top = max(sectors) if sectors else 0
    body = bytearray()
    for n in range(1, top + 1):
        body += sectors.get(n, b"\x00" * size)
    para = len(body) // 16
    head = struct.pack("<HHHBBIB3s", 0x0296, para & 0xFFFF, size,
                       (para >> 16) & 0xFF, 0, 0, 0, b"\x00" * 3)
    return bytes(head[:16]) + bytes(body)


def boot_record(sectors):
    """What sector 1 says: where it loads, how much, and where it starts.

    An Atari boot record is six bytes of header and then code. The loader in
    ROM reads sector 1, takes the count, pulls that many sectors in after it,
    and jumps. A game that boots this way has no directory and no DOS -- the
    disk *is* the program.
    """
    one = sectors.get(1)
    if not one:
        return None
    flag, count, load, init = one[0], one[1], \
        struct.unpack_from("<H", one, 2)[0], struct.unpack_from("<H", one, 4)[0]
    return {"flag": flag, "sectors": count, "load": load, "init": init,
            "code": one[6:]}


def dos2_directory(sectors):
    """A DOS 2 directory, if this disk has one.

    The directory lives in sectors 361-368, eight entries of sixteen bytes
    each. A boot disk with its own loader has nothing there, and saying so is
    more useful than printing whatever those bytes happen to spell.
    """
    files = []
    for s in range(361, 369):
        blob = sectors.get(s)
        if not blob:
            continue
        for i in range(8):
            e = blob[i * 16:(i + 1) * 16]
            if len(e) < 16:
                continue
            flag = e[0]
            if flag == 0x00:
                continue
            if not (flag & 0x40 or flag & 0x42):
                continue
            count = struct.unpack_from("<H", e, 1)[0]
            start = struct.unpack_from("<H", e, 3)[0]
            raw_name = e[5:16]
            # Be strict here, or this "finds" files in whatever the game
            # happens to store at sectors 361-368 -- which is exactly what it
            # did on Karateka, reporting 23 files of binary noise. A real
            # DOS 2 name is upper case, digits or spaces, and its counts point
            # somewhere that exists on the disk.
            if not all(48 <= c <= 57 or 65 <= c <= 90 or c == 32
                       for c in raw_name):
                continue
            if not (1 <= start <= 720 and 1 <= count <= 720):
                continue
            name = raw_name[:8].decode("ascii").strip()
            ext = raw_name[8:].decode("ascii").strip()
            if not name:
                continue
            files.append({"name": name, "ext": ext, "sectors": count,
                          "start": start, "flag": flag})
    return files


def read_file(sectors, start, count, size=128):
    """Follow a DOS 2 file's sector chain."""
    out = bytearray()
    s, seen = start, set()
    for _ in range(count + 8):
        if s in seen or s not in sectors:
            break
        seen.add(s)
        blob = sectors[s]
        nxt = ((blob[size - 3] & 0x03) << 8) | blob[size - 2]
        used = blob[size - 1] & 0x7F
        out += blob[:used]
        if not nxt:
            break
        s = nxt
    return bytes(out)


def main():
    ap = argparse.ArgumentParser(
        description=__doc__.strip().split("\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("image")
    ap.add_argument("--atr", metavar="OUT", help="write a plain ATR")
    ap.add_argument("--boot", action="store_true",
                    help="show the boot record")
    ap.add_argument("--extract", metavar="DIR",
                    help="write out the files, if the disk has a directory")
    ap.add_argument("--raw", metavar="OUT",
                    help="write every sector end to end, no header")
    args = ap.parse_args()

    try:
        sectors, info = read_atx(args.image)
    except (AtxError, IOError) as e:
        sys.stderr.write("%s\n" % e)
        return 2

    print("%s" % os.path.basename(args.image))
    print("  %d sectors of %d bytes, density code %d"
          % (len(sectors), info["sector_size"], info["density"]))
    print("  %d sectors appear more than once, %d carry an error flag"
          % (info["duplicates"], info["flagged"]))
    if info["duplicates"] or info["flagged"]:
        print("     -- which on an Atari disk usually means copy protection "
              "rather than damage")

    boot = boot_record(sectors)
    if boot:
        print("  boot record: load %d sectors to $%04X, start at $%04X"
              % (boot["sectors"], boot["load"], boot["init"]))

    files = dos2_directory(sectors)
    if files:
        print("  a DOS 2 directory with %d files:" % len(files))
        for f in files:
            print("     %-8s.%-3s %4d sectors from %d"
                  % (f["name"], f["ext"], f["sectors"], f["start"]))
    else:
        print("  no DOS directory: this disk boots its own loader, so the "
              "program is sectors rather than files")

    if args.atr:
        io.open(args.atr, "wb").write(to_atr(sectors, info["sector_size"]))
        print("  wrote %s" % args.atr)
    if args.raw:
        top = max(sectors)
        blob = b"".join(sectors.get(n, b"\x00" * info["sector_size"])
                        for n in range(1, top + 1))
        io.open(args.raw, "wb").write(blob)
        print("  wrote %s (%d bytes, sectors 1-%d)" % (args.raw, len(blob), top))
    if args.extract:
        if not files:
            print("  nothing to extract: there is no directory on this disk")
            return 0
        if not os.path.isdir(args.extract):
            os.makedirs(args.extract)
        for f in files:
            data = read_file(sectors, f["start"], f["sectors"],
                             info["sector_size"])
            name = ("%s.%s" % (f["name"], f["ext"])).strip(".")
            io.open(os.path.join(args.extract, name), "wb").write(data)
            print("     %s  %d bytes" % (name, len(data)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
