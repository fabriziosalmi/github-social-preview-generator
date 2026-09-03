"""Text as outlines.

Nothing here asks the operating system for a font. Faces are loaded from the
glyph packs under ``assets/fonts`` (built by ``tools/build_glyphpack.py``) and
every string becomes an SVG ``<path>``. The consequences are the point of the
exercise:

* the same input renders identically on any machine, with no font installed;
* no renderer-specific text shaping can shift a baseline by a pixel;
* the output has no external dependency, so it survives being opened anywhere.

Coordinates inside a run stay in integer font units and are placed by the
path's own ``transform``, which keeps the emitted path short and exact.
"""

from __future__ import annotations

import json
import os
from typing import Dict, List, Optional, Sequence, Tuple

from .errors import AssetError

#: Substituted for any character the pack does not carry.
FALLBACK_CHAR = "·"

#: Glyph packs ship inside the package, so an installed copy needs nothing from
#: the source tree. ``GSPG_FONT_DIR`` overrides it, which is how the build
#: regenerates packs in place and how a test can point at a fixture.
_ASSET_ROOT = os.environ.get("GSPG_FONT_DIR") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "assets", "fonts"
)


def format_number(value: float) -> str:
    """Compact decimal for SVG output, keeping enough digits to stay exact.

    Nine significant digits is well past the precision any rasteriser acts on,
    while still surviving values such as a 12/2048 font scale — four decimals
    would quietly shear a headline by a couple of pixels. Exponent notation is
    avoided because not every SVG consumer accepts it in a transform.
    """
    if value != value or value in (float("inf"), float("-inf")):
        raise ValueError("cannot serialise non-finite coordinate %r" % (value,))
    if value == int(value) and abs(value) < 1e15:
        return str(int(value))
    text = "%.9g" % (value,)
    if "e" in text or "E" in text:
        text = "%.12f" % (value,)
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text if text not in ("-0", "") else "0"


#: Kept as a private alias so call sites inside this module read naturally.
_format_number = format_number


class TextRun:
    """A laid-out single line of text, ready to be emitted as one path."""

    __slots__ = ("d", "transform", "width", "size", "face")

    def __init__(self, d: str, transform: str, width: float, size: float, face: "Face") -> None:
        self.d = d
        self.transform = transform
        self.width = width
        self.size = size
        self.face = face

    def __bool__(self) -> bool:
        return bool(self.d)


class Face:
    """One weight of one family, loaded from a glyph pack."""

    _cache: Dict[str, "Face"] = {}

    def __init__(self, pack: Dict[str, object], name: str) -> None:
        self.name = name
        self.family = str(pack["family"])
        self.style = str(pack["style"])
        self.units_per_em = int(pack["unitsPerEm"])  # type: ignore[arg-type]
        self.ascender = int(pack["ascender"])  # type: ignore[arg-type]
        self.descender = int(pack["descender"])  # type: ignore[arg-type]
        self.cap_height = int(pack["capHeight"])  # type: ignore[arg-type]
        self.x_height = int(pack["xHeight"])  # type: ignore[arg-type]
        self._glyphs: Dict[str, List] = pack["glyphs"]  # type: ignore[assignment]
        self._kern: Dict[str, Dict[str, int]] = pack["kern"]  # type: ignore[assignment]
        if FALLBACK_CHAR not in self._glyphs:
            raise AssetError("glyph pack %r lacks the fallback glyph" % (name,))

    # -- loading ---------------------------------------------------------

    @classmethod
    def load(cls, name: str, root: Optional[str] = None) -> "Face":
        """Load ``<name>.glyphs.json``, caching by resolved path."""
        directory = root or _ASSET_ROOT
        path = os.path.join(directory, name + ".glyphs.json")
        cached = cls._cache.get(path)
        if cached is not None:
            return cached
        if not os.path.exists(path):
            raise AssetError(
                "glyph pack not found: %s\nRun `make fonts` to build it."
                % (path,)
            )
        try:
            with open(path, "r", encoding="utf-8") as handle:
                pack = json.load(handle)
        except ValueError as error:
            raise AssetError("glyph pack %s is not valid JSON: %s" % (path, error))
        if pack.get("schema") != 1:
            raise AssetError(
                "glyph pack %s has schema %r, expected 1" % (path, pack.get("schema"))
            )
        face = cls(pack, name)
        cls._cache[path] = face
        return face

    # -- metrics ---------------------------------------------------------

    @property
    def glyph_count(self) -> int:
        return len(self._glyphs)

    @property
    def kern_pair_count(self) -> int:
        return sum(len(rights) for rights in self._kern.values())

    def supports(self, char: str) -> bool:
        return char in self._glyphs

    def _entry(self, char: str) -> List:
        entry = self._glyphs.get(char)
        if entry is None:
            entry = self._glyphs[FALLBACK_CHAR]
        return entry

    def advance_units(self, char: str) -> int:
        return int(self._entry(char)[0])

    def kern_units(self, left: str, right: str) -> int:
        return self._kern.get(left, {}).get(right, 0)

    def scale(self, size: float) -> float:
        return size / float(self.units_per_em)

    def cap_height_px(self, size: float) -> float:
        return self.cap_height * self.scale(size)

    def ascender_px(self, size: float) -> float:
        return self.ascender * self.scale(size)

    def descender_px(self, size: float) -> float:
        return self.descender * self.scale(size)

    def advance_units_for(self, text: str, tracking: float = 0.0) -> int:
        """Total advance of ``text`` in font units, including kerning.

        ``tracking`` is extra letter spacing expressed in em, the unit type
        designers actually think in, and is applied between glyphs only.
        """
        if not text:
            return 0
        tracking_units = int(round(tracking * self.units_per_em))
        total = 0
        previous = ""
        for index, char in enumerate(text):
            if index:
                total += self.kern_units(previous, char) + tracking_units
            total += self.advance_units(char)
            previous = char
        return total

    def measure(self, text: str, size: float, tracking: float = 0.0) -> float:
        """Width of ``text`` in user units at ``size``."""
        return self.advance_units_for(text, tracking) * self.scale(size)

    # -- layout ----------------------------------------------------------

    def run(
        self,
        text: str,
        size: float,
        x: float = 0.0,
        y: float = 0.0,
        tracking: float = 0.0,
        anchor: str = "start",
    ) -> TextRun:
        """Lay ``text`` out with its baseline at ``y`` and return one path.

        ``anchor`` is ``start``, ``middle`` or ``end``, matching the SVG
        property of the same name — resolved here rather than delegated, so the
        result does not depend on the renderer's text engine.
        """
        if anchor not in ("start", "middle", "end"):
            raise ValueError("anchor must be start, middle or end, not %r" % (anchor,))

        width_units = self.advance_units_for(text, tracking)
        scale = self.scale(size)
        width = width_units * scale
        offset = {"start": 0.0, "middle": -width / 2.0, "end": -width}[anchor]

        tracking_units = int(round(tracking * self.units_per_em))
        commands: List[str] = []
        pen = 0
        previous = ""
        for index, char in enumerate(text):
            if index:
                pen += self.kern_units(previous, char) + tracking_units
            entry = self._entry(char)
            if len(entry) == 4:
                start_x, start_y, path = entry[1], entry[2], entry[3]
                commands.append("M%d %d%s" % (pen + start_x, start_y, path))
            pen += int(entry[0])
            previous = char

        transform = "translate(%s %s) scale(%s -%s)" % (
            _format_number(x + offset),
            _format_number(y),
            _format_number(scale),
            _format_number(scale),
        )
        return TextRun("".join(commands), transform, width, size, self)

    # -- line breaking ---------------------------------------------------

    def wrap(
        self,
        text: str,
        size: float,
        max_width: float,
        max_lines: int = 3,
        tracking: float = 0.0,
        balance: bool = True,
    ) -> List[str]:
        """Break ``text`` into at most ``max_lines`` lines that fit ``max_width``.

        Repository names are the hard case: ``cloudflare-backup-actions`` is a
        single word wider than any sensible column, so breaks are allowed after
        a hyphen, slash or underscore as well as at spaces. The last line is
        ellipsised when the text still does not fit, and with ``balance`` the
        lines are re-flowed to even out their lengths, which is what stops a
        two-line title from ending in a single orphan word.
        """
        tokens = _tokenise(text)
        if not tokens:
            return []

        lines = self._greedy_wrap(tokens, size, max_width, tracking)
        if len(lines) > max_lines:
            lines = self._truncate(tokens, size, max_width, max_lines, tracking)
        elif balance and 1 < len(lines) <= max_lines:
            lines = self._balance(tokens, size, max_width, len(lines), tracking) or lines
        return lines

    def _greedy_wrap(
        self, tokens: Sequence[Tuple[str, str]], size: float, max_width: float, tracking: float
    ) -> List[str]:
        lines: List[str] = []
        current = ""
        for index, (piece, separator) in enumerate(tokens):
            candidate = piece if not current else current + separator + piece
            if not current or self.measure(candidate, size, tracking) <= max_width:
                current = candidate
            else:
                lines.append(current)
                current = piece
        if current:
            lines.append(current)
        return lines

    def _truncate(
        self,
        tokens: Sequence[Tuple[str, str]],
        size: float,
        max_width: float,
        max_lines: int,
        tracking: float,
    ) -> List[str]:
        lines = self._greedy_wrap(tokens, size, max_width, tracking)
        if len(lines) <= max_lines:
            return lines
        kept = lines[:max_lines]
        last = kept[-1].rstrip() + "…"
        while self.measure(last, size, tracking) > max_width and len(last) > 1:
            last = last[:-2].rstrip() + "…"
        kept[-1] = last
        return kept

    def _balance(
        self,
        tokens: Sequence[Tuple[str, str]],
        size: float,
        max_width: float,
        line_count: int,
        tracking: float,
    ) -> Optional[List[str]]:
        """Minimise the squared slack across ``line_count`` lines.

        A small dynamic program over break positions: exact for the line counts
        a preview ever uses, and it never widens a line past ``max_width``.
        """
        count = len(tokens)
        if line_count < 2 or count < line_count:
            return None

        segments = [[""] * (count + 1) for _ in range(count)]
        widths = [[0.0] * (count + 1) for _ in range(count)]
        for first in range(count):
            text = ""
            for last in range(first, count):
                piece, separator = tokens[last]
                text = piece if last == first else text + separator + piece
                segments[first][last + 1] = text
                widths[first][last + 1] = self.measure(text, size, tracking)

        infinity = float("inf")
        # best[lines][index] = least cost of laying tokens[index:] out in `lines`.
        best = [[infinity] * (count + 1) for _ in range(line_count + 1)]
        split = [[0] * (count + 1) for _ in range(line_count + 1)]
        best[0][count] = 0.0
        for lines_left in range(1, line_count + 1):
            for index in range(count + 1):
                for end in range(index + 1, count + 1):
                    width = widths[index][end]
                    if width > max_width:
                        break
                    tail = best[lines_left - 1][end]
                    if tail == infinity:
                        continue
                    slack = max_width - width
                    cost = tail + slack * slack
                    if cost < best[lines_left][index]:
                        best[lines_left][index] = cost
                        split[lines_left][index] = end
        if best[line_count][0] == infinity:
            return None

        result: List[str] = []
        index = 0
        for lines_left in range(line_count, 0, -1):
            end = split[lines_left][index]
            result.append(segments[index][end])
            index = end
        return result

    def fit_size(
        self,
        text: str,
        max_width: float,
        max_lines: int,
        largest: float,
        smallest: float,
        tracking: float = 0.0,
        step: float = 1.0,
    ) -> Tuple[float, List[str]]:
        """Largest size in ``[smallest, largest]`` where ``text`` fits, with its lines.

        Fitting means both the line count and every line's width. When even the
        smallest size overflows — one very long unbreakable token — the size is
        scaled down past the floor rather than letting the text run out of the
        frame, because a slightly small headline is a design choice and a
        clipped one is a bug.
        """
        if largest < smallest:
            raise ValueError("largest must not be below smallest")
        tokens = _tokenise(text)
        if not tokens:
            return largest, []

        size = largest
        while size > smallest:
            lines = self._greedy_wrap(tokens, size, max_width, tracking)
            if len(lines) <= max_lines and self._widest(lines, size, tracking) <= max_width:
                break
            size -= step
        size = max(size, smallest)

        lines = self._greedy_wrap(tokens, size, max_width, tracking)
        widest = self._widest(lines, size, tracking)
        if widest > max_width:
            size = max(1.0, size * max_width / widest)
        return size, self.wrap(text, size, max_width, max_lines, tracking)

    def _widest(self, lines: Sequence[str], size: float, tracking: float) -> float:
        return max((self.measure(line, size, tracking) for line in lines), default=0.0)


#: Breaks are allowed after these characters as well as at spaces, which is
#: what lets a hyphenated repository name wrap instead of overflowing.
_BREAK_AFTER = "-/_.:"


def _tokenise(text: str) -> List[Tuple[str, str]]:
    """Split ``text`` into ``(piece, separator_before)`` break candidates."""
    tokens: List[Tuple[str, str]] = []
    for word_index, word in enumerate(text.split()):
        pieces: List[str] = []
        current = ""
        for char in word:
            current += char
            if char in _BREAK_AFTER and current:
                pieces.append(current)
                current = ""
        if current:
            pieces.append(current)
        for piece_index, piece in enumerate(pieces):
            separator = " " if (word_index and piece_index == 0) else ""
            tokens.append((piece, separator))
    return tokens
