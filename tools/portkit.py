#!/usr/bin/env python3
"""
Ship a conversion as a recipe, not as a copy.

    python tools/portkit.py verify  recipe.json
    python tools/portkit.py extract recipe.json --out build/
    python tools/portkit.py build   recipe.json --out karateka-7800.a78

## The problem a BPS patch cannot solve

A patch is a delta between two files. That works for a hack -- change some bytes
of a cartridge, ship the difference, the difference is your own work. It stops
working the moment the output draws on a *second* source.

A 7800 conversion of the 8-bit Karateka is exactly that case. Its graphics come
off an Atari 800 disk, its sound code comes off the same disk, and the rest is
new. A BPS from the 7800 cartridge to that output would contain every one of
those 8-bit bytes verbatim, because to the patch format they are simply bytes
that were not there before. The patch *is* the content, dressed as a diff.

## What ships instead

A recipe: where each piece comes from, and what to do with it. No payload.

    sources   the images the builder needs, by SHA-256 of the whole file
    regions   named extents within them -- disk sector or ROM offset, length,
              and the SHA-256 of that extent alone
    new       files of original work, which do ship, because they are yours
    output    the SHA-256 the finished cartridge must have

Everyone who builds it supplies their own copies of the sources and gets a
byte-identical result. Nothing copyrighted travels, and the output hash means a
build that silently differs is caught rather than distributed.

That is the same arrangement decompilation projects use, and it is stricter than
a patch rather than looser: a patch will happily apply to the wrong file and
produce rubbish, while this refuses before it starts.

## The rule this file enforces

**A recipe may not contain payload bytes.** Not base64, not hex, not "just this
one table". The moment it does, it is a copy with extra steps, and the whole
arrangement collapses. `verify` rejects a recipe carrying anything that looks
like embedded data, so the guarantee is checked rather than promised.
"""
import argparse
import hashlib
import io
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PAYLOAD_KEYS = ("data", "bytes", "payload", "base64", "b64", "hex", "content")


class RecipeError(Exception):
    pass


def sha(blob):
    return hashlib.sha256(blob).hexdigest()


def load_recipe(path):
    with io.open(path, encoding="utf-8") as f:
        recipe = json.load(f)
    _refuse_payload(recipe)
    return recipe


def _refuse_payload(node, where="recipe"):
    """Refuse a recipe that carries content rather than coordinates.

    The whole arrangement rests on the recipe being instructions. A single
    embedded table would make it a redistribution wearing a JSON hat, and the
    temptation is real -- one awkward asset, just this once. So it is a rule
    the code enforces instead of a convention the author remembers.
    """
    if isinstance(node, dict):
        for k, v in node.items():
            if k.lower() in PAYLOAD_KEYS and isinstance(v, str) and len(v) > 64:
                raise RecipeError(
                    "%s.%s carries %d characters of what looks like embedded "
                    "data. A recipe says where bytes come from; it never "
                    "contains them." % (where, k, len(v)))
            _refuse_payload(v, "%s.%s" % (where, k))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            _refuse_payload(v, "%s[%d]" % (where, i))
    elif isinstance(node, str) and len(node) > 4096:
        raise RecipeError("%s holds a %d-character string, which is content, "
                          "not a coordinate" % (where, len(node)))


def resolve(recipe, root, supplied):
    """Find each source the recipe needs, and check it is the right one."""
    found = {}
    for name, spec in recipe["sources"].items():
        path = supplied.get(name)
        if not path:
            for cand in spec.get("look_in", []):
                cand = os.path.join(root, cand) if not os.path.isabs(cand) \
                    else cand
                if os.path.exists(cand):
                    path = cand
                    break
        if not path or not os.path.exists(path):
            raise RecipeError(
                "cannot find the source %r (%s). Supply it with --%s <path>."
                % (name, spec.get("what", "?"), name))
        blob = io.open(path, "rb").read()
        got = sha(blob)
        if spec.get("sha256") and got != spec["sha256"]:
            raise RecipeError(
                "%s is not the expected image.\n  wanted sha256 %s\n  found     "
                " %s\nA different dump of the same thing will not do: the "
                "recipe's offsets are measured against one exact file."
                % (name, spec["sha256"], got))
        found[name] = (path, blob)
    return found


def cut(recipe, sources, name):
    """One named region, taken from whichever source holds it."""
    spec = recipe["regions"][name]
    src = spec["from"]
    if src not in sources:
        raise RecipeError("region %r wants source %r, which is not loaded"
                          % (name, src))
    _path, blob = sources[src]
    if spec.get("header"):
        blob = blob[spec["header"]:]
    if src == "xegs" or (blob[:4] == b"CART" and "sector" not in spec):
        # a .car carries a 16-byte header; regions are measured from the ROM
        blob = blob[16:]
    if "sector" in spec:
        # disk regions are addressed the way a disk is
        import atx
        sectors, info = atx.read_atx(_path)
        size = info["sector_size"]
        first = spec["sector"]
        count = spec.get("sectors", 1)
        data = b"".join(sectors.get(first + i, b"\x00" * size)
                        for i in range(count))
        data = data[spec.get("offset", 0):]
    else:
        off = spec["offset"]
        data = blob[off:]
    if "length" in spec:
        data = data[:spec["length"]]
    if spec.get("sha256"):
        got = sha(data)
        if got != spec["sha256"]:
            raise RecipeError(
                "region %r does not hash as expected.\n  wanted %s\n  found  %s"
                "\nEither the source differs from the one this was measured "
                "against, or the coordinates are wrong." % (name,
                                                            spec["sha256"], got))
    return data


def cmd_verify(recipe, root, supplied):
    sources = resolve(recipe, root, supplied)
    print("sources")
    for name, (path, blob) in sources.items():
        print("  %-10s %-46s %d bytes  ok"
              % (name, os.path.basename(path), len(blob)))
    print("")
    print("regions")
    bad = 0
    for name in recipe.get("regions", {}):
        try:
            data = cut(recipe, sources, name)
            note = recipe["regions"][name].get("what", "")
            print("  %-18s %6d bytes  ok   %s" % (name, len(data), note))
        except RecipeError as e:
            bad += 1
            print("  %-18s FAILED: %s" % (name, str(e).splitlines()[0]))
    print("")
    missing = [f for f in recipe.get("new", [])
               if not os.path.exists(os.path.join(root, f))]
    if missing:
        print("original files this recipe expects but cannot find:")
        for f in missing:
            print("   %s" % f)
        bad += len(missing)
    if bad:
        print("%d problem(s); the build would not be reproducible." % bad)
        return 1
    print("everything the recipe needs is present and hashes as expected.")
    return 0


def cmd_extract(recipe, root, supplied, out):
    sources = resolve(recipe, root, supplied)
    if not os.path.isdir(out):
        os.makedirs(out)
    for name in recipe.get("regions", {}):
        data = cut(recipe, sources, name)
        path = os.path.join(out, name + ".bin")
        io.open(path, "wb").write(data)
        print("  %-18s %6d bytes -> %s" % (name, len(data),
                                           os.path.relpath(path, root)))
    print("")
    print("These came off your own copies and stay on your machine. They are "
          "the inputs to a build, not something to pass on.")
    return 0


def cmd_build(recipe, root, supplied, out):
    """Assemble the cartridge, and refuse to be quietly wrong about it."""
    sources = resolve(recipe, root, supplied)
    steps = recipe.get("build")
    if not steps:
        print("This recipe has no build section yet -- it can locate and check "
              "its inputs, which is the part that has to exist before any port "
              "code does, but it cannot assemble anything.")
        return 1
    raise RecipeError("build is not implemented yet")


def main():
    ap = argparse.ArgumentParser(
        description=__doc__.strip().split("\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["verify", "extract", "build"])
    ap.add_argument("recipe")
    ap.add_argument("--out", default="build")
    ap.add_argument("--source", action="append", default=[], metavar="NAME=PATH",
                    help="point a named source at a file on your machine")
    args = ap.parse_args()

    root = os.path.dirname(os.path.abspath(args.recipe))
    supplied = {}
    for s in args.source:
        if "=" not in s:
            sys.stderr.write("--source wants NAME=PATH\n")
            return 2
        k, v = s.split("=", 1)
        supplied[k] = v

    try:
        recipe = load_recipe(args.recipe)
        print("%s -- %s" % (recipe.get("name", os.path.basename(args.recipe)),
                            recipe.get("what", "")))
        print("")
        if args.command == "verify":
            return cmd_verify(recipe, root, supplied)
        if args.command == "extract":
            return cmd_extract(recipe, root, supplied,
                               os.path.join(root, args.out))
        return cmd_build(recipe, root, supplied, args.out)
    except (RecipeError, ValueError) as e:
        sys.stderr.write("%s\n" % e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
