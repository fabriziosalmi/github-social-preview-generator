"""Minimal TrueType reader: outlines, metrics and GPOS pair kerning.

Build-time only. Pure standard library, no third-party font stack. It reads
just enough of the sfnt container to turn a static ``.ttf`` into the compact
glyph pack consumed at render time (see ``tools/build_glyphpack.py``).

Supported: ``head`` ``hhea`` ``hmtx`` ``maxp`` ``OS/2`` ``post`` ``cmap`` (4, 12),
``loca`` ``glyf`` (simple + composite) and ``GPOS`` pair kerning (lookup type 2
formats 1 and 2, reached directly or through a type 9 extension).

Deliberately unsupported: CFF/OTF outlines, variable-font deltas, contextual
positioning. The vendored sources are static TrueType, so none of it applies.
"""

from __future__ import annotations

import struct
from typing import Dict, List, Optional, Sequence, Tuple

# glyf simple-glyph point flags
_ON_CURVE = 0x01
_X_SHORT = 0x02
_Y_SHORT = 0x04
_REPEAT = 0x08
_X_SAME_OR_POS = 0x10
_Y_SAME_OR_POS = 0x20

# glyf composite-component flags
_ARG_1_AND_2_ARE_WORDS = 0x0001
_ARGS_ARE_XY_VALUES = 0x0002
_WE_HAVE_A_SCALE = 0x0008
_MORE_COMPONENTS = 0x0020
_WE_HAVE_AN_X_AND_Y_SCALE = 0x0040
_WE_HAVE_A_TWO_BY_TWO = 0x0080

_VALUE_FORMAT_BITS = (
    0x0001,  # XPlacement
    0x0002,  # YPlacement
    0x0004,  # XAdvance
    0x0008,  # YAdvance
    0x0010,  # XPlaDevice
    0x0020,  # YPlaDevice
    0x0040,  # XAdvDevice
    0x0080,  # YAdvDevice
)


class FontError(Exception):
    """Raised when a font file is malformed or uses an unsupported flavour."""


Point = Tuple[int, int, bool]  # x, y, on-curve
Contour = List[Point]


def _u8(b: bytes, o: int) -> int:
    return b[o]


def _u16(b: bytes, o: int) -> int:
    return struct.unpack_from(">H", b, o)[0]


def _s16(b: bytes, o: int) -> int:
    return struct.unpack_from(">h", b, o)[0]


def _u32(b: bytes, o: int) -> int:
    return struct.unpack_from(">I", b, o)[0]


def _f2dot14(b: bytes, o: int) -> float:
    return _s16(b, o) / 16384.0


class TrueTypeFont:
    """A parsed static TrueType font."""

    def __init__(self, data: bytes) -> None:
        self.data = data
        self.tables = self._read_table_directory()

        head = self._table("head")
        self.units_per_em = _u16(head, 18)
        self._loca_long = _s16(head, 50) == 1

        self.num_glyphs = _u16(self._table("maxp"), 4)

        hhea = self._table("hhea")
        self.ascender = _s16(hhea, 4)
        self.descender = _s16(hhea, 6)
        self.line_gap = _s16(hhea, 8)
        self._num_h_metrics = _u16(hhea, 34)

        self.cap_height, self.x_height = self._read_os2_heights()
        self._loca = self._read_loca()
        self._cmap = self._read_cmap()

    # -- container -------------------------------------------------------

    def _read_table_directory(self) -> Dict[str, Tuple[int, int]]:
        data = self.data
        if len(data) < 12:
            raise FontError("file is too short to hold an sfnt header")
        tag = data[:4]
        if tag == b"ttcf":
            raise FontError("TrueType collections are not supported; extract one face first")
        if tag not in (b"\x00\x01\x00\x00", b"true"):
            if tag == b"OTTO":
                raise FontError("CFF (OTTO) outlines are not supported; use a TrueType build")
            raise FontError("unrecognised sfnt version %r" % (tag,))
        count = _u16(data, 4)
        tables = {}
        for i in range(count):
            o = 12 + i * 16
            name = data[o : o + 4].decode("latin-1")
            tables[name] = (_u32(data, o + 8), _u32(data, o + 12))
        return tables

    def has_table(self, name: str) -> bool:
        return name in self.tables

    def _table(self, name: str) -> bytes:
        if name not in self.tables:
            raise FontError("required table %r is missing" % (name,))
        offset, length = self.tables[name]
        if offset + length > len(self.data):
            raise FontError("table %r runs past end of file" % (name,))
        return self.data[offset : offset + length]

    # -- metrics ---------------------------------------------------------

    def _read_os2_heights(self) -> Tuple[int, int]:
        """Return (capHeight, xHeight), falling back to ratios when absent."""
        if self.has_table("OS/2"):
            os2 = self._table("OS/2")
            version = _u16(os2, 0)
            if version >= 2 and len(os2) >= 90:
                x_height = _s16(os2, 86)
                cap_height = _s16(os2, 88)
                if cap_height > 0 and x_height > 0:
                    return cap_height, x_height
        return int(self.units_per_em * 0.72), int(self.units_per_em * 0.52)

    def advance_width(self, glyph_id: int) -> int:
        hmtx = self._table("hmtx")
        index = min(glyph_id, self._num_h_metrics - 1)
        offset = index * 4
        if offset + 2 > len(hmtx):
            raise FontError("hmtx is too short for glyph %d" % (glyph_id,))
        return _u16(hmtx, offset)

    # -- character mapping -----------------------------------------------

    def _read_cmap(self) -> Dict[int, int]:
        cmap = self._table("cmap")
        best_offset = None
        best_score = -1
        for i in range(_u16(cmap, 2)):
            rec = 4 + i * 8
            platform = _u16(cmap, rec)
            encoding = _u16(cmap, rec + 2)
            offset = _u32(cmap, rec + 4)
            # Prefer a full-repertoire Unicode subtable, then the BMP ones.
            score = {
                (3, 10): 5,
                (0, 6): 5,
                (0, 4): 5,
                (3, 1): 4,
                (0, 3): 4,
                (0, 2): 3,
                (0, 1): 3,
                (0, 0): 3,
            }.get((platform, encoding), -1)
            if score > best_score:
                best_score, best_offset = score, offset
        if best_offset is None or best_score < 0:
            raise FontError("no Unicode cmap subtable found")

        fmt = _u16(cmap, best_offset)
        if fmt == 4:
            return self._read_cmap4(cmap, best_offset)
        if fmt == 12:
            return self._read_cmap12(cmap, best_offset)
        raise FontError("cmap subtable format %d is not supported" % (fmt,))

    @staticmethod
    def _read_cmap4(cmap: bytes, base: int) -> Dict[int, int]:
        seg_count = _u16(cmap, base + 6) // 2
        ends = base + 14
        starts = ends + seg_count * 2 + 2
        deltas = starts + seg_count * 2
        ranges = deltas + seg_count * 2
        mapping: Dict[int, int] = {}
        for seg in range(seg_count):
            end = _u16(cmap, ends + seg * 2)
            start = _u16(cmap, starts + seg * 2)
            delta = _u16(cmap, deltas + seg * 2)
            range_offset = _u16(cmap, ranges + seg * 2)
            if start > end:
                continue
            for code in range(start, min(end, 0xFFFF) + 1):
                if range_offset == 0:
                    glyph = (code + delta) & 0xFFFF
                else:
                    addr = ranges + seg * 2 + range_offset + (code - start) * 2
                    if addr + 2 > len(cmap):
                        continue
                    glyph = _u16(cmap, addr)
                    if glyph:
                        glyph = (glyph + delta) & 0xFFFF
                if glyph:
                    mapping[code] = glyph
        return mapping

    @staticmethod
    def _read_cmap12(cmap: bytes, base: int) -> Dict[int, int]:
        mapping: Dict[int, int] = {}
        for i in range(_u32(cmap, base + 12)):
            g = base + 16 + i * 12
            start, end, start_glyph = _u32(cmap, g), _u32(cmap, g + 4), _u32(cmap, g + 8)
            if end - start > 0x10FFFF:
                raise FontError("implausible cmap format 12 group")
            for offset in range(end - start + 1):
                mapping[start + offset] = start_glyph + offset
        return mapping

    def glyph_id(self, char: str) -> Optional[int]:
        return self._cmap.get(ord(char))

    # -- outlines --------------------------------------------------------

    def _read_loca(self) -> List[int]:
        loca = self._table("loca")
        count = self.num_glyphs + 1
        if self._loca_long:
            if len(loca) < count * 4:
                raise FontError("loca is too short for numGlyphs")
            return list(struct.unpack_from(">%dI" % count, loca, 0))
        if len(loca) < count * 2:
            raise FontError("loca is too short for numGlyphs")
        return [v * 2 for v in struct.unpack_from(">%dH" % count, loca, 0)]

    def contours(self, glyph_id: int, depth: int = 0) -> List[Contour]:
        """Return the glyph outline as a list of contours in font units."""
        if depth > 8:
            raise FontError("composite glyph nesting is too deep")
        if not 0 <= glyph_id < self.num_glyphs:
            raise FontError("glyph id %d out of range" % (glyph_id,))
        glyf = self._table("glyf")
        start, end = self._loca[glyph_id], self._loca[glyph_id + 1]
        if end <= start:
            return []  # empty glyph, e.g. space
        glyph = glyf[start:end]
        num_contours = _s16(glyph, 0)
        if num_contours >= 0:
            return self._simple_contours(glyph, num_contours)
        return self._composite_contours(glyph, depth)

    @staticmethod
    def _simple_contours(glyph: bytes, num_contours: int) -> List[Contour]:
        end_pts = [_u16(glyph, 10 + i * 2) for i in range(num_contours)]
        num_points = (end_pts[-1] + 1) if end_pts else 0
        pos = 10 + num_contours * 2
        pos += 2 + _u16(glyph, pos)  # skip instructions

        flags: List[int] = []
        while len(flags) < num_points:
            flag = _u8(glyph, pos)
            pos += 1
            flags.append(flag)
            if flag & _REPEAT:
                repeat = _u8(glyph, pos)
                pos += 1
                flags.extend([flag] * repeat)
        flags = flags[:num_points]

        xs: List[int] = []
        value = 0
        for flag in flags:
            if flag & _X_SHORT:
                delta = _u8(glyph, pos)
                pos += 1
                value += delta if flag & _X_SAME_OR_POS else -delta
            elif not flag & _X_SAME_OR_POS:
                value += _s16(glyph, pos)
                pos += 2
            xs.append(value)

        ys: List[int] = []
        value = 0
        for flag in flags:
            if flag & _Y_SHORT:
                delta = _u8(glyph, pos)
                pos += 1
                value += delta if flag & _Y_SAME_OR_POS else -delta
            elif not flag & _Y_SAME_OR_POS:
                value += _s16(glyph, pos)
                pos += 2
            ys.append(value)

        contours: List[Contour] = []
        first = 0
        for last in end_pts:
            points = [
                (xs[i], ys[i], bool(flags[i] & _ON_CURVE))
                for i in range(first, min(last + 1, num_points))
            ]
            if points:
                contours.append(points)
            first = last + 1
        return contours

    def _composite_contours(self, glyph: bytes, depth: int) -> List[Contour]:
        contours: List[Contour] = []
        pos = 10
        while True:
            flags = _u16(glyph, pos)
            component_id = _u16(glyph, pos + 2)
            pos += 4
            if flags & _ARG_1_AND_2_ARE_WORDS:
                arg1, arg2 = _s16(glyph, pos), _s16(glyph, pos + 2)
                pos += 4
            else:
                arg1, arg2 = struct.unpack_from(">bb", glyph, pos)
                pos += 2

            a = d = 1.0
            b = c = 0.0
            if flags & _WE_HAVE_A_SCALE:
                a = d = _f2dot14(glyph, pos)
                pos += 2
            elif flags & _WE_HAVE_AN_X_AND_Y_SCALE:
                a, d = _f2dot14(glyph, pos), _f2dot14(glyph, pos + 2)
                pos += 4
            elif flags & _WE_HAVE_A_TWO_BY_TWO:
                a, b = _f2dot14(glyph, pos), _f2dot14(glyph, pos + 2)
                c, d = _f2dot14(glyph, pos + 4), _f2dot14(glyph, pos + 6)
                pos += 8

            if not flags & _ARGS_ARE_XY_VALUES:
                raise FontError("point-matched composite components are not supported")
            dx, dy = arg1, arg2

            for contour in self.contours(component_id, depth + 1):
                contours.append(
                    [
                        (
                            int(round(a * x + c * y + dx)),
                            int(round(b * x + d * y + dy)),
                            on_curve,
                        )
                        for x, y, on_curve in contour
                    ]
                )
            if not flags & _MORE_COMPONENTS:
                break
        return contours

    # -- GPOS pair kerning ------------------------------------------------

    def kern_pairs(self, glyph_ids: Sequence[int]) -> Dict[Tuple[int, int], int]:
        """Return ``{(left, right): xAdvance}`` restricted to ``glyph_ids``."""
        wanted = set(glyph_ids)
        pairs: Dict[Tuple[int, int], int] = {}
        if self.has_table("GPOS"):
            self._collect_gpos_kerning(self._table("GPOS"), wanted, pairs)
        if not pairs and self.has_table("kern"):
            self._collect_legacy_kerning(self._table("kern"), wanted, pairs)
        return {pair: value for pair, value in pairs.items() if value}

    def _collect_gpos_kerning(
        self, gpos: bytes, wanted: set, out: Dict[Tuple[int, int], int]
    ) -> None:
        feature_list = _u16(gpos, 6)
        lookup_list = _u16(gpos, 8)

        lookup_indices = set()
        for i in range(_u16(gpos, feature_list)):
            rec = feature_list + 2 + i * 6
            if gpos[rec : rec + 4] != b"kern":
                continue
            feature = feature_list + _u16(gpos, rec + 4)
            for j in range(_u16(gpos, feature + 2)):
                lookup_indices.add(_u16(gpos, feature + 4 + j * 2))
        if not lookup_indices:
            return

        for index in sorted(lookup_indices):
            if index >= _u16(gpos, lookup_list):
                continue
            lookup = lookup_list + _u16(gpos, lookup_list + 2 + index * 2)
            lookup_type = _u16(gpos, lookup)
            for k in range(_u16(gpos, lookup + 4)):
                subtable = lookup + _u16(gpos, lookup + 6 + k * 2)
                actual_type, actual_offset = lookup_type, subtable
                if lookup_type == 9:  # extension positioning
                    actual_type = _u16(gpos, subtable + 2)
                    actual_offset = subtable + _u32(gpos, subtable + 4)
                if actual_type == 2:
                    self._read_pair_pos(gpos, actual_offset, wanted, out)

    def _read_pair_pos(
        self, gpos: bytes, base: int, wanted: set, out: Dict[Tuple[int, int], int]
    ) -> None:
        fmt = _u16(gpos, base)
        coverage = self._read_coverage(gpos, base + _u16(gpos, base + 2))
        value_format1 = _u16(gpos, base + 4)
        value_format2 = _u16(gpos, base + 6)
        size1 = self._value_record_size(value_format1)
        size2 = self._value_record_size(value_format2)
        x_advance_offset = self._x_advance_offset(value_format1)

        def read_x_advance(offset: int) -> int:
            if x_advance_offset is None:
                return 0
            return _s16(gpos, offset + x_advance_offset)

        if fmt == 1:
            for i in range(_u16(gpos, base + 8)):
                left = coverage[i] if i < len(coverage) else None
                if left is None or left not in wanted:
                    continue
                pair_set = base + _u16(gpos, base + 10 + i * 2)
                for j in range(_u16(gpos, pair_set)):
                    rec = pair_set + 2 + j * (2 + size1 + size2)
                    right = _u16(gpos, rec)
                    if right not in wanted:
                        continue
                    value = read_x_advance(rec + 2)
                    if value:
                        out[(left, right)] = value
        elif fmt == 2:
            class_def1 = self._read_class_def(gpos, base + _u16(gpos, base + 8))
            class_def2 = self._read_class_def(gpos, base + _u16(gpos, base + 10))
            class1_count = _u16(gpos, base + 12)
            class2_count = _u16(gpos, base + 14)
            record_size = size1 + size2
            covered = [g for g in coverage if g in wanted]
            by_class1: Dict[int, List[int]] = {}
            for glyph in covered:
                by_class1.setdefault(class_def1.get(glyph, 0), []).append(glyph)
            by_class2: Dict[int, List[int]] = {}
            for glyph in wanted:
                by_class2.setdefault(class_def2.get(glyph, 0), []).append(glyph)
            for c1, lefts in by_class1.items():
                if c1 >= class1_count:
                    continue
                row = base + 16 + c1 * class2_count * record_size
                for c2, rights in by_class2.items():
                    if c2 >= class2_count:
                        continue
                    value = read_x_advance(row + c2 * record_size)
                    if not value:
                        continue
                    for left in lefts:
                        for right in rights:
                            out[(left, right)] = value

    def _collect_legacy_kerning(
        self, kern: bytes, wanted: set, out: Dict[Tuple[int, int], int]
    ) -> None:
        if len(kern) < 4 or _u16(kern, 0) != 0:
            return  # Apple-flavoured `kern` tables are out of scope
        pos = 4
        for _ in range(_u16(kern, 2)):
            length = _u16(kern, pos + 2)
            coverage = _u16(kern, pos + 4)
            if (coverage & 0xFF00) >> 8 == 0 and coverage & 0x0001:
                n_pairs = _u16(kern, pos + 6)
                for i in range(n_pairs):
                    rec = pos + 14 + i * 6
                    left, right = _u16(kern, rec), _u16(kern, rec + 2)
                    if left in wanted and right in wanted:
                        value = _s16(kern, rec + 4)
                        if value:
                            out[(left, right)] = value
            pos += length

    @staticmethod
    def _value_record_size(value_format: int) -> int:
        return 2 * sum(1 for bit in _VALUE_FORMAT_BITS if value_format & bit)

    @staticmethod
    def _x_advance_offset(value_format: int) -> Optional[int]:
        if not value_format & 0x0004:
            return None
        return 2 * sum(1 for bit in (0x0001, 0x0002) if value_format & bit)

    @staticmethod
    def _read_coverage(data: bytes, base: int) -> List[int]:
        fmt = _u16(data, base)
        if fmt == 1:
            count = _u16(data, base + 2)
            return [_u16(data, base + 4 + i * 2) for i in range(count)]
        if fmt == 2:
            glyphs: List[int] = []
            for i in range(_u16(data, base + 2)):
                rec = base + 4 + i * 6
                start, end = _u16(data, rec), _u16(data, rec + 2)
                index = _u16(data, rec + 4)
                needed = index + (end - start) + 1
                if len(glyphs) < needed:
                    glyphs.extend([0] * (needed - len(glyphs)))
                for offset in range(end - start + 1):
                    glyphs[index + offset] = start + offset
            return glyphs
        raise FontError("coverage format %d is not supported" % (fmt,))

    @staticmethod
    def _read_class_def(data: bytes, base: int) -> Dict[int, int]:
        fmt = _u16(data, base)
        classes: Dict[int, int] = {}
        if fmt == 1:
            start = _u16(data, base + 2)
            for i in range(_u16(data, base + 4)):
                classes[start + i] = _u16(data, base + 6 + i * 2)
        elif fmt == 2:
            for i in range(_u16(data, base + 2)):
                rec = base + 4 + i * 6
                first, last = _u16(data, rec), _u16(data, rec + 2)
                value = _u16(data, rec + 4)
                for glyph in range(first, last + 1):
                    classes[glyph] = value
        else:
            raise FontError("class definition format %d is not supported" % (fmt,))
        return classes


def _num(value: int) -> str:
    return str(value)


def _append(parts: List[str], token: str) -> None:
    """Append a coordinate, dropping the separator when the sign supplies one."""
    if parts and not token.startswith("-") and parts[-1][-1] not in "MmLlHhVvQqZz":
        parts.append(" ")
    parts.append(token)


def contours_to_path(contours: Sequence[Contour]) -> Tuple[int, int, str]:
    """Convert TrueType quadratic contours to a compact relative SVG path.

    Returns ``(start_x, start_y, commands)`` where ``commands`` is everything
    after the opening move. Splitting the origin out lets the renderer place a
    glyph by writing one absolute ``M`` and concatenating the rest verbatim:
    every later command, including the move that opens a second contour, is
    relative to the current point.

    Coordinates stay in font units and use ``h``/``v`` shorthands for the
    axis-aligned segments that make up most stems, which is lossless and
    roughly halves the stored pack.
    """
    parts: List[str] = []
    origin: Optional[Tuple[int, int]] = None
    cx = cy = 0
    for contour in contours:
        if not contour:
            continue
        points = list(contour)
        # Rotate so the contour starts on an on-curve point, synthesising the
        # implied midpoint when a contour is made entirely of control points.
        start_index = next((i for i, p in enumerate(points) if p[2]), None)
        if start_index is None:
            x0, y0, _ = points[0]
            x1, y1, _ = points[-1]
            points.insert(0, ((x0 + x1) // 2, (y0 + y1) // 2, True))
            start_index = 0
        points = points[start_index:] + points[:start_index]

        sx, sy = points[0][0], points[0][1]
        if origin is None:
            origin = (sx, sy)
        else:
            # After `z` the current point is the previous subpath's start.
            parts.append("m")
            _append(parts, _num(sx - cx))
            _append(parts, _num(sy - cy))
        cx, cy = sx, sy

        i = 1
        count = len(points)
        while i <= count:
            x, y, on_curve = points[i % count]
            if on_curve:
                dx, dy = x - cx, y - cy
                if dx == 0 and dy == 0:
                    i += 1
                    continue
                if dy == 0:
                    parts.append("h")
                    _append(parts, _num(dx))
                elif dx == 0:
                    parts.append("v")
                    _append(parts, _num(dy))
                else:
                    parts.append("l")
                    _append(parts, _num(dx))
                    _append(parts, _num(dy))
                cx, cy = x, y
                i += 1
                continue

            nx, ny, next_on_curve = points[(i + 1) % count]
            if not next_on_curve:
                nx, ny = (x + nx) // 2, (y + ny) // 2
                step = 1
            else:
                step = 2
            parts.append("q")
            _append(parts, _num(x - cx))
            _append(parts, _num(y - cy))
            _append(parts, _num(nx - cx))
            _append(parts, _num(ny - cy))
            cx, cy = nx, ny
            i += step
        parts.append("z")
        cx, cy = sx, sy

    if origin is None:
        return 0, 0, ""
    return origin[0], origin[1], "".join(parts)
