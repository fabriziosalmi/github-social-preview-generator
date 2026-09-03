#!/usr/bin/env python3
"""Turn a static TrueType file into the compact glyph pack used at render time.

The renderer never opens a ``.ttf`` and never asks the operating system for a
font: it draws text as vector paths taken from these packs. That is what makes
output byte-identical on any machine, with no font installed and no network.

Usage::

    tools/build_glyphpack.py vendor/fonts/Inter-Regular.ttf src/gspg/assets/fonts/

Run it through ``make fonts`` rather than by hand.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Dict, List, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fontlib import FontError, TrueTypeFont, contours_to_path  # noqa: E402

#: Characters every pack must carry. Latin-1 plus the punctuation a repository
#: title, description or badge realistically uses. Anything outside this set is
#: reported by the builder and substituted at render time.
CHARSET = "".join(
    [
        # ASCII printable
        "".join(chr(c) for c in range(0x20, 0x7F)),
        # Latin-1 letters (Italian, French, German, Spanish, Nordic)
        "ÀÁÂÃÄÅÆÇÈÉÊËÌÍÎÏÐÑÒÓÔÕÖØÙÚÛÜÝÞßàáâãäåæçèéêëìíîïðñòóôõöøùúûüýþÿ",
        "ĀāĆćČčĐđĒēĖėĘęĢģĪīĮįŁłŃńŅņŌōŒœŚśŠšŪūŲųŸŹźŻżŽž",
        # Punctuation, marks and symbols used by the templates
        " ¡¢£¤¥§¨©ª«¬®¯°±²³´µ¶·¸¹º»¼½¾¿×÷",
        "‐‑‒–—―‘’‚“”„†‡•…‰‹›⁄€™",
        "←↑→↓↔∅∆∏∑−√∞∫≈≠≤≥■□▪▶◀●○◆◇★☆✓✗⌘",
    ]
)


def build_pack(path: str, family: str, style: str) -> Tuple[Dict[str, object], List[str]]:
    with open(path, "rb") as handle:
        font = TrueTypeFont(handle.read())

    charset = sorted(set(CHARSET))
    glyphs: Dict[str, List[object]] = {}
    char_to_glyph: Dict[str, int] = {}
    missing: List[str] = []

    for char in charset:
        glyph_id = font.glyph_id(char)
        if glyph_id is None:
            missing.append(char)
            continue
        char_to_glyph[char] = glyph_id
        advance = font.advance_width(glyph_id)
        start_x, start_y, commands = contours_to_path(font.contours(glyph_id))
        # Blank glyphs (space and friends) carry an advance and nothing else.
        glyphs[char] = [advance] if not commands else [advance, start_x, start_y, commands]

    glyph_to_chars: Dict[int, List[str]] = {}
    for char, glyph_id in char_to_glyph.items():
        glyph_to_chars.setdefault(glyph_id, []).append(char)

    # Nested by left character: smaller on disk than flat pairs, and a diff
    # then shows one line per left character instead of thousands of pairs.
    kern: Dict[str, Dict[str, int]] = {}
    for (left, right), value in font.kern_pairs(sorted(glyph_to_chars)).items():
        for left_char in glyph_to_chars.get(left, ()):
            for right_char in glyph_to_chars.get(right, ()):
                kern.setdefault(left_char, {})[right_char] = value

    pack = {
        "schema": 1,
        "family": family,
        "style": style,
        "source": os.path.basename(path),
        "unitsPerEm": font.units_per_em,
        "ascender": font.ascender,
        "descender": font.descender,
        "lineGap": font.line_gap,
        "capHeight": font.cap_height,
        "xHeight": font.x_height,
        "glyphs": glyphs,
        "kern": kern,
    }
    return pack, missing


def write_pack(pack: Dict[str, object], destination: str) -> None:
    """Write one JSON object per line so packs stay reviewable in a diff."""
    glyphs = pack.pop("glyphs")
    kern = pack.pop("kern")
    lines = ["{"]
    for key in ("schema", "family", "style", "source", "unitsPerEm", "ascender",
                "descender", "lineGap", "capHeight", "xHeight"):
        lines.append('  %s: %s,' % (json.dumps(key), json.dumps(pack[key])))

    lines.append('  "glyphs": {')
    glyph_lines = [
        '    %s: %s' % (json.dumps(char), json.dumps(value, separators=(",", ":")))
        for char, value in sorted(glyphs.items())
    ]
    lines.append(",\n".join(glyph_lines))
    lines.append("  },")

    lines.append('  "kern": {')
    kern_lines = [
        '    %s: %s'
        % (json.dumps(left), json.dumps(rights, separators=(",", ":"), sort_keys=True))
        for left, rights in sorted(kern.items())
    ]
    lines.append(",\n".join(kern_lines))
    lines.append("  }")
    lines.append("}")
    with open(destination, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source", help="static TrueType file to read")
    parser.add_argument("outdir", help="directory to write the .glyphs.json pack into")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    stem = os.path.splitext(os.path.basename(args.source))[0]
    family, _, style = stem.partition("-")
    try:
        pack, missing = build_pack(args.source, family, style or "Regular")
    except FontError as error:
        print("error: %s: %s" % (args.source, error), file=sys.stderr)
        return 1

    destination = os.path.join(args.outdir, stem + ".glyphs.json")
    glyph_count = len(pack["glyphs"])
    kern_count = sum(len(v) for v in pack["kern"].values())
    write_pack(pack, destination)

    if not args.quiet:
        size = os.path.getsize(destination)
        print(
            "%-28s %4d glyphs  %5d kern pairs  %6.1f KiB"
            % (stem, glyph_count, kern_count, size / 1024.0)
        )
        if missing:
            print("  missing from font: %s" % ("".join(missing),))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
