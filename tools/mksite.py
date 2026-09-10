#!/usr/bin/env python
"""Package the published pages into site/ as a standalone, browsable folder.

The pages are already self-contained -- every image is a data URI and nothing
is fetched from a CDN, because the artifact host forbids it.  So packaging is
mostly a matter of collecting them under stable names and turning the
artifact URLs they link to each other with into relative filenames, so the
folder browses offline exactly as the published set browses online.

Refuses to write a page that still references anything outside itself.
"""

import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SITE = os.path.join(ROOT, "site")

# out name -> (source, artifact id, gallery title)
PAGES = {
    "index.html":    ("build/report.html",   "fc8cc11c", "Teardown"),
    "rooms.html":    ("build/rooms.html",    "19dc49cc", "Rooms"),
    "bestiary.html": ("pages/bestiary.html", "5e003ec1", "Bestiary"),
    "bosses.html":   ("build/bosses.html",   "cf4c9f9e", "Bosses"),
    "items.html":    ("build/items.html",    "4f9c8563", "Items"),
    "sprites.html":  ("pages/sprites.html",  "ed9b4562", "Sprite sheet"),
    "editor.html":   ("pages/editor.html",   "44fefe1b", "Editor manual"),
    "versions.html": ("pages/versions.html", "b5c40afe", "NTSC and PAL"),
}

BY_ID = {aid: name for name, (_, aid, _) in PAGES.items() if aid}


def localise(html):
    """Rewrite sibling artifact URLs to relative filenames."""
    def sub(m):
        return BY_ID.get(m.group(1)[:8], m.group(0))
    return re.sub(r"https://claude\.ai/code/artifact/([0-9a-f-]{36})", sub, html)


def audit(name, html):
    """Every page must stand alone: no local paths, no remote hosts."""
    bad = []
    for ref in re.findall(r'(?:src|href)="([^"#]+)"', html):
        if ref.startswith("data:"):
            continue
        if ref.startswith(("http://", "https://", "//")):
            bad.append("remote: " + ref)
        elif ref not in PAGES:
            bad.append("unresolved: " + ref)
    for tag in re.findall(r"<(?:script|link)\b[^>]*>", html):
        if "src=" in tag or "href=" in tag:
            if "data:" not in tag and not re.search(r'"[a-z]+\.html"', tag):
                bad.append("external asset: " + tag[:60])
    return bad


def main():
    import argparse
    ap = argparse.ArgumentParser(
        description=__doc__.strip().split("\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-o", "--out", default=SITE,
                    help="where to write the folder (default: site/)")
    args = ap.parse_args()
    # Arguments are parsed first on purpose: this function empties its output
    # directory, and it used to do that before looking at argv at all -- so
    # `mksite.py --help` deleted the site and then printed nothing.
    out = args.out
    if os.path.isdir(out):
        shutil.rmtree(out)
    os.makedirs(out)

    total, failed = 0, 0
    print("%-14s %8s  %s" % ("page", "size", "source"))
    for name, (src, _, title) in PAGES.items():
        path = os.path.join(ROOT, src)
        if not os.path.exists(path):
            print("  MISSING %s (%s)" % (name, src))
            failed += 1
            continue
        with open(path, encoding="utf-8") as f:
            html = localise(f.read())
        bad = audit(name, html)
        if bad:
            print("  REJECTED %s: %s" % (name, "; ".join(bad[:3])))
            failed += 1
            continue
        out = os.path.join(out, name)
        with open(out, "w", encoding="utf-8") as f:
            f.write(html)
        size = os.path.getsize(out)
        total += size
        print("%-14s %7.0fK  %s" % (name, size / 1024, src))

    print("\n%d pages, %.1f MB, self-contained" % (len(PAGES) - failed, total / 1048576.0))
    print("open %s" % os.path.join(out, "index.html"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
