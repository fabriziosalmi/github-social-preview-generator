"""Gradient noise and the scalar fields the procedural artwork is drawn from.

Perlin noise on a seeded permutation table, plus fractional Brownian motion
and domain warping on top. All of it is deterministic and depends only on the
seed, so a repository's artwork is a pure function of its name.

The noise is tileable on request: the permutation lattice wraps at a chosen
period, which lets a pattern repeat cleanly when a template needs it to.
"""

from __future__ import annotations

import math
from typing import List, Sequence, Tuple

from .seed import Rng

_GRADIENTS: Tuple[Tuple[float, float], ...] = tuple(
    (math.cos(math.tau * i / 16.0), math.sin(math.tau * i / 16.0)) for i in range(16)
)


def _fade(t: float) -> float:
    """Ken Perlin's improved quintic interpolant: zero first and second derivative."""
    return t * t * t * (t * (t * 6.0 - 15.0) + 10.0)


class Perlin2D:
    """Seeded 2-D Perlin noise returning values in roughly ``[-1, 1]``."""

    __slots__ = ("_perm", "_period")

    def __init__(self, rng: Rng, period: int = 256) -> None:
        if period < 4:
            raise ValueError("noise period must be at least 4")
        self._period = period
        table = rng.shuffled(range(period))
        self._perm: List[int] = table + table  # doubled to avoid a modulo per lookup

    def _gradient(self, ix: int, iy: int) -> Tuple[float, float]:
        period = self._period
        index = self._perm[(self._perm[ix % period] + (iy % period)) % period]
        return _GRADIENTS[index % 16]

    def at(self, x: float, y: float) -> float:
        x0, y0 = math.floor(x), math.floor(y)
        fx, fy = x - x0, y - y0
        x0, y0 = int(x0), int(y0)

        dots = []
        for dx, dy in ((0, 0), (1, 0), (0, 1), (1, 1)):
            gx, gy = self._gradient(x0 + dx, y0 + dy)
            dots.append(gx * (fx - dx) + gy * (fy - dy))

        u, v = _fade(fx), _fade(fy)
        top = dots[0] + u * (dots[1] - dots[0])
        bottom = dots[2] + u * (dots[3] - dots[2])
        return top + v * (bottom - top)

    def fbm(
        self,
        x: float,
        y: float,
        octaves: int = 4,
        lacunarity: float = 2.0,
        gain: float = 0.5,
    ) -> float:
        """Sum successive octaves; the result is normalised back to ``[-1, 1]``."""
        total = 0.0
        amplitude = 1.0
        normaliser = 0.0
        frequency = 1.0
        for _ in range(max(1, octaves)):
            total += amplitude * self.at(x * frequency, y * frequency)
            normaliser += amplitude
            amplitude *= gain
            frequency *= lacunarity
        return total / normaliser if normaliser else 0.0

    def warped(
        self, x: float, y: float, strength: float = 0.6, octaves: int = 4
    ) -> float:
        """fbm sampled through an fbm-displaced domain: organic, non-repeating flow."""
        offset_x = self.fbm(x + 5.2, y + 1.3, octaves)
        offset_y = self.fbm(x + 9.7, y + 4.1, octaves)
        return self.fbm(x + strength * offset_x, y + strength * offset_y, octaves)


class ScalarField:
    """A sampled height field over a rectangle, plus contour extraction."""

    def __init__(
        self,
        width: float,
        height: float,
        columns: int,
        rows: int,
        values: Sequence[Sequence[float]],
    ) -> None:
        self.width = width
        self.height = height
        self.columns = columns
        self.rows = rows
        self.values = values

    @classmethod
    def from_noise(
        cls,
        noise: Perlin2D,
        width: float,
        height: float,
        columns: int,
        rows: int,
        scale: float = 3.0,
        octaves: int = 4,
        warp: float = 0.0,
    ) -> "ScalarField":
        values = []
        for row in range(rows + 1):
            line = []
            for column in range(columns + 1):
                x = scale * column / float(columns)
                y = scale * row / float(rows) * (height / width)
                if warp:
                    line.append(noise.warped(x, y, warp, octaves))
                else:
                    line.append(noise.fbm(x, y, octaves))
            values.append(line)
        return cls(width, height, columns, rows, values)

    def _point(self, column: float, row: float) -> Tuple[float, float]:
        return (
            column * self.width / float(self.columns),
            row * self.height / float(self.rows),
        )

    def contours(self, level: float) -> List[List[Tuple[float, float]]]:
        """Marching squares at ``level``, returned as joined polylines.

        Segments are emitted per cell and then stitched end to end, which is
        what turns a cloud of unordered edges into the long sweeping lines that
        make a topographic field read as deliberate rather than noisy.
        """
        segments: List[Tuple[Tuple[float, float], Tuple[float, float]]] = []
        values = self.values
        for row in range(self.rows):
            for column in range(self.columns):
                corners = (
                    values[row][column],
                    values[row][column + 1],
                    values[row + 1][column + 1],
                    values[row + 1][column],
                )
                index = sum(
                    (1 << i) for i, value in enumerate(corners) if value >= level
                )
                if index in (0, 15):
                    continue
                segments.extend(self._cell_segments(column, row, corners, index, level))
        return _stitch(segments)

    def _cell_segments(
        self,
        column: int,
        row: int,
        corners: Tuple[float, float, float, float],
        index: int,
        level: float,
    ) -> List[Tuple[Tuple[float, float], Tuple[float, float]]]:
        top_left, top_right, bottom_right, bottom_left = corners

        def interpolate(a: float, b: float) -> float:
            span = b - a
            return 0.5 if abs(span) < 1e-12 else (level - a) / span

        top = self._point(column + interpolate(top_left, top_right), row)
        right = self._point(column + 1, row + interpolate(top_right, bottom_right))
        bottom = self._point(column + interpolate(bottom_left, bottom_right), row + 1)
        left = self._point(column, row + interpolate(top_left, bottom_left))

        # Saddle cases (5 and 10) are resolved with the cell average, which
        # avoids the crossed lines a naive table produces.
        if index in (5, 10):
            average = sum(corners) / 4.0
            if (index == 5) == (average >= level):
                return [(left, top), (right, bottom)]
            return [(left, bottom), (right, top)]

        table = {
            1: [(left, top)], 2: [(top, right)], 3: [(left, right)],
            4: [(right, bottom)], 6: [(top, bottom)], 7: [(left, bottom)],
            8: [(bottom, left)], 9: [(bottom, top)], 11: [(bottom, right)],
            12: [(right, left)], 13: [(right, top)], 14: [(top, left)],
        }
        return table.get(index, [])


def _stitch(
    segments: Sequence[Tuple[Tuple[float, float], Tuple[float, float]]],
    tolerance: float = 1e-6,
) -> List[List[Tuple[float, float]]]:
    """Join segments sharing an endpoint into the longest polylines possible.

    Endpoints are snapped to a lattice and indexed, so stitching stays linear
    in the number of segments rather than quadratic; a dense field produces
    tens of thousands of them.
    """
    def key(point: Tuple[float, float]) -> Tuple[int, int]:
        return (int(round(point[0] / tolerance)), int(round(point[1] / tolerance)))

    outgoing: dict = {}
    for index, (start, _end) in enumerate(segments):
        outgoing.setdefault(key(start), []).append(index)

    used = [False] * len(segments)
    polylines: List[List[Tuple[float, float]]] = []
    for index, (start, end) in enumerate(segments):
        if used[index]:
            continue
        used[index] = True
        chain = [start, end]
        while True:
            following = None
            for candidate in outgoing.get(key(chain[-1]), ()):
                if not used[candidate]:
                    following = candidate
                    break
            if following is None:
                break
            used[following] = True
            chain.append(segments[following][1])
            if key(chain[-1]) == key(chain[0]):
                break
        if len(chain) > 2:
            polylines.append(chain)
    return polylines
