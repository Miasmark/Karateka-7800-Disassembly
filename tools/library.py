#!/usr/bin/env python3
"""
Find and open ROMs in a 7800 collection.

Collections arrive as one big zip (Trebor's PROPack and friends) or as an
unpacked tree. Either way you want the same three things: find the cartridge
whose name you half remember, see what it is without unpacking anything, and
hand its path to the rest of the toolkit.

This reads zips in place -- no 22MB extraction to search a filename.

  python library.py --find "midnight"                 # search
  python library.py --find "f-18" --info              # search and lay out
  python library.py --find "exo" --extract out/       # unpack the matches
  python library.py --survey "double dragon"          # full survey of a match
  python library.py --stats                           # what is in the collection

Point it at a collection with --lib, or set A7800_LIB, or let it look in the
usual places next to this toolkit.
"""
import argparse
import fnmatch
import io
import os
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cart as cartlib

ROM_EXT = (".a78", ".bin", ".rom")


def default_lib():
    """Where to look when nobody said."""
    env = os.environ.get("A7800_LIB")
    if env:
        return env
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for guess in (os.path.join(here, "roms"),
                  os.path.join(os.path.dirname(here), "roms"),
                  os.path.dirname(here)):
        if os.path.isdir(guess):
            hits = [f for f in os.listdir(guess)
                    if f.lower().endswith(".zip") or f.lower().endswith(ROM_EXT)]
            if hits:
                return guess
    return "."


class Library(object):
    """A collection of ROMs, whether zipped or loose."""

    def __init__(self, root):
        self.root = root
        self.zips = {}                  # path -> ZipFile, opened lazily
        self.items = []                 # (display_name, container, member)
        self._index()

    def _index(self):
        if os.path.isfile(self.root):
            paths = [self.root]
        else:
            paths = []
            for dirpath, _dirs, files in os.walk(self.root):
                for f in files:
                    paths.append(os.path.join(dirpath, f))

        for p in paths:
            low = p.lower()
            if low.endswith(".zip"):
                try:
                    z = zipfile.ZipFile(p)
                except zipfile.BadZipFile:
                    continue
                self.zips[p] = z
                for m in z.namelist():
                    if m.lower().endswith(ROM_EXT):
                        self.items.append((m, p, m))
            elif low.endswith(ROM_EXT):
                rel = os.path.relpath(p, self.root) if os.path.isdir(self.root) else p
                self.items.append((rel, None, p))
        self.items.sort(key=lambda t: t[0].lower())

    # -- finding ------------------------------------------------------------
    def find(self, pattern):
        """Match on any part of the path, case-insensitively.

        A bare word is treated as a substring, which is what people mean; a
        pattern containing * or ? is treated as a glob.
        """
        pat = pattern.lower()
        glob = any(c in pat for c in "*?[")
        out = []
        for name, container, member in self.items:
            hay = name.lower()
            if (fnmatch.fnmatch(hay, pat) if glob else pat in hay):
                out.append((name, container, member))
        return out

    def read(self, item):
        """The bytes of one item."""
        _name, container, member = item
        if container is None:
            with open(member, "rb") as f:
                return f.read()
        return self.zips[container].read(member)

    def extract(self, item, outdir):
        """Write one item out and return the path, so tools can take a path."""
        name = os.path.basename(item[0])
        os.makedirs(outdir, exist_ok=True)
        path = os.path.join(outdir, name)
        with open(path, "wb") as f:
            f.write(self.read(item))
        return path

    def open_cart(self, item, **kw):
        """A Cart for an item, extracting to a temporary file if it is zipped.

        cart.Cart wants a path because everything downstream reports paths, so
        a zipped member gets spooled to a temp file. The caller gets the Cart
        and the path it lives at.
        """
        _name, container, member = item
        if container is None:
            return cartlib.Cart(member, **kw), member
        import tempfile
        d = tempfile.mkdtemp(prefix="a7800lib-")
        path = os.path.join(d, os.path.basename(member))
        with open(path, "wb") as f:
            f.write(self.read(item))
        return cartlib.Cart(path, **kw), path


def brief(lib, item):
    """One line: what this cartridge is, without unpacking it."""
    raw = lib.read(item)
    h = cartlib.read_header(raw)
    body = len(raw) - (128 if h else 0)
    if h:
        try:
            mapper = cartlib.pick_mapper(body, h).name
        except cartlib.UnknownMapper:
            mapper = "unsupported"
        return "%5dK  %-10s %-6s $%04X" % (body // 1024, mapper,
                                           h["region"], h["cart_type"])
    return "%5dK  %-10s %-6s %s" % (body // 1024, "headerless", "?", "-")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lib", default=None,
                    help="collection: a zip, or a directory to walk")
    ap.add_argument("--find", help="substring, or a glob if it has * or ?")
    ap.add_argument("--info", action="store_true", help="lay out each match")
    ap.add_argument("--survey", help="find one match and survey it fully")
    ap.add_argument("--extract", metavar="DIR", help="write matches out")
    ap.add_argument("--stats", action="store_true",
                    help="mappers and sizes across the whole collection")
    ap.add_argument("--limit", type=int, default=40)
    args = ap.parse_args()

    root = args.lib or default_lib()
    lib = Library(root)
    if not lib.items:
        print("no ROMs found under %s" % root)
        print("point at one with --lib, or set A7800_LIB")
        return 1
    print("%d ROMs in %s\n" % (len(lib.items), root))

    if args.stats:
        import collections
        kinds, sizes, bad = collections.Counter(), collections.Counter(), 0
        for it in lib.items:
            raw = lib.read(it)
            h = cartlib.read_header(raw)
            body = len(raw) - (128 if h else 0)
            sizes[body // 1024] += 1
            try:
                m = cartlib.pick_mapper(body, h)
                low = getattr(m, "low", "")
                kinds["%s%s" % (m.name, "/" + low if low else "")] += 1
            except cartlib.UnknownMapper:
                bad += 1
        print("mappers")
        for k, n in kinds.most_common():
            print("   %-20s %5d" % (k, n))
        print("   %-20s %5d" % ("(unsupported)", bad))
        print("\nsizes")
        for k, n in sorted(sizes.items()):
            print("   %5dK %5d" % (k, n))
        return 0

    target = args.survey or args.find
    if not target:
        ap.error("nothing to do: pass --find, --survey or --stats")

    hits = lib.find(target)
    if not hits:
        print("nothing matches %r" % target)
        return 1

    if args.survey:
        if len(hits) > 1:
            print("%d matches; surveying the first. Narrow it to pick another.\n"
                  % len(hits))
            for name, _c, _m in hits[:8]:
                print("   %s" % name)
            print()
        import survey
        try:
            _c, path = lib.open_cart(hits[0])
        except cartlib.UnknownMapper:
            path = lib.extract(hits[0], os.environ.get("TEMP", "."))
        
        sys.argv = ["survey", path]
        return survey.main()

    print("%d match%s" % (len(hits), "" if len(hits) == 1 else "es"))
    for name, container, member in hits[:args.limit]:
        print("  %s  %s" % (brief(lib, (name, container, member)),
                            os.path.basename(name)))
    if len(hits) > args.limit:
        print("  ... %d more" % (len(hits) - args.limit))

    if args.info:
        print()
        for it in hits[:args.limit]:
            try:
                c, _p = lib.open_cart(it)
            except cartlib.UnknownMapper as e:
                print("%s" % os.path.basename(it[0]))
                print("  cannot be laid out: %s\n" % e)
                continue
            print(c.describe())
            print()

    if args.extract:
        print()
        for it in hits[:args.limit]:
            print("  wrote %s" % lib.extract(it, args.extract))
    return 0


if __name__ == "__main__":
    sys.exit(main())
