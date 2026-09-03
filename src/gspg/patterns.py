"""Procedural background fields.

Each generator paints one rectangle and returns a single ``<g>``. They share a
:class:`Field` context so a template can hand any of them the same region and
palette and get back something coherent.

Three constraints run through all of them:

* **Deterministic.** Every random choice comes from the seeded
  :class:`~gspg.seed.Rng`, so artwork is a pure function of the repository name.
* **Filter-free.** Softness comes from gradients and from stacking translucent
  geometry, never from ``feGaussianBlur``, because rasterisers disagree there.
* **Bounded.** Generators cap their own element count. A background that
  balloons to a megabyte is a bug, not a detail.
"""

from __future__ import annotations

import math
from typing import Callable, Dict, List, Optional, Tuple

from . import svg
from .color import Oklch, mix
from .errors import RenderError
from .noise import Perlin2D, ScalarField
from .palette import Palette
from .seed import Rng

Point = Tuple[float, float]


class Field:
    """The rectangle a pattern paints, plus everything it needs to paint it."""

    def __init__(
        self,
        document: svg.Document,
        palette: Palette,
        rng: Rng,
        x: float,
        y: float,
        width: float,
        height: float,
    ) -> None:
        if width <= 0 or height <= 0:
            raise RenderError("a pattern field needs a positive size")
        self.document = document
        self.palette = palette
        self.rng = rng
        self.x = x
        self.y = y
        self.width = width
        self.height = height

    @property
    def right(self) -> float:
        return self.x + self.width

    @property
    def bottom(self) -> float:
        return self.y + self.height

    def noise(self, *salt: object) -> Perlin2D:
        return Perlin2D(self.rng.fork("noise", *salt))

    def ink(self, weight: float, tint: float = 0.0) -> str:
        """A hairline colour at ``weight`` alpha, ``tint`` of the way to the accent."""
        base = mix(self.palette.hairline, self.palette.accent, tint)
        return base.fade(weight).css()


def _clip(field: Field, group: svg.Element) -> svg.Element:
    """Clip ``group`` to the field, so a generator may overshoot freely."""
    clip_id = field.document.unique_id("clip")
    clip = svg.Element("clipPath", (("id", clip_id),))
    clip.add(svg.rect(field.x, field.y, field.width, field.height))
    field.document.defs.add(clip)
    group.attrs.insert(0, ("clip-path", "url(#%s)" % (clip_id,)))
    return group


# -- generators ----------------------------------------------------------


def topography(field: Field) -> svg.Element:
    """Contour lines over a warped noise field: a survey map of nothing."""
    rng = field.rng
    noise = field.noise("topography")
    surface = ScalarField.from_noise(
        noise,
        field.width,
        field.height,
        columns=168,
        rows=84,
        scale=rng.uniform(2.4, 3.6),
        octaves=4,
        warp=rng.uniform(0.35, 0.85),
    )

    group = svg.group(("fill", "none"), ("stroke-linejoin", "round"),
                      ("stroke-linecap", "round"))
    levels = 17
    span = 0.86
    for index in range(levels):
        level = -span / 2.0 + span * index / float(levels - 1)
        emphasis = index % 4 == 0
        stroke = field.ink(0.34 if emphasis else 0.17, tint=0.15 + 0.5 * index / levels)
        width = 1.5 if emphasis else 0.9
        for polyline in surface.contours(level):
            if len(polyline) < 4:
                continue
            points = [(field.x + px, field.y + py) for px, py in polyline]
            group.add(svg.polyline(points, ("stroke", stroke), ("stroke-width", width)))
    return _clip(field, group)


def ridgeline(field: Field) -> svg.Element:
    """Stacked ridges, each filled so it occludes the one behind it."""
    rng = field.rng
    noise = field.noise("ridgeline")
    rows = rng.randint(22, 30)
    samples = 150
    amplitude = field.height / rows * rng.uniform(3.2, 4.6)

    group = svg.group()
    for row in range(rows):
        # Back rows sit higher and read fainter, which is what makes the stack
        # feel like depth rather than like a list of wiggles.
        depth = row / float(rows - 1)
        baseline = field.y + field.height * (0.18 + 0.86 * depth)
        points: List[Point] = []
        for sample in range(samples + 1):
            t = sample / float(samples)
            value = noise.fbm(t * 3.1, row * 0.34, octaves=3)
            edge = math.sin(math.pi * t) ** 0.6  # taper to the frame
            points.append(
                (field.x + t * field.width, baseline - value * amplitude * edge)
            )
        closing = [(field.right, field.bottom + 4.0), (field.x, field.bottom + 4.0)]
        fill = mix(field.palette.background, field.palette.background_deep, 1.0 - depth)
        group.add(
            svg.polyline(
                points + closing,
                ("fill", fill.fade(0.92).css()),
                ("stroke", field.ink(0.10 + 0.30 * depth, tint=0.25 + 0.55 * depth)),
                ("stroke-width", 1.1),
                ("stroke-linejoin", "round"),
            )
        )
    return _clip(field, group)


def precision_grid(field: Field) -> svg.Element:
    """A drafting grid: modular rules, tick marks and a few activated cells."""
    rng = field.rng
    step = rng.choice((32.0, 40.0, 48.0))
    group = svg.group()

    columns = int(math.ceil(field.width / step)) + 1
    rows = int(math.ceil(field.height / step)) + 1
    minor = svg.group(("stroke", field.ink(0.13)), ("stroke-width", 1))
    major = svg.group(("stroke", field.ink(0.26, tint=0.3)), ("stroke-width", 1))
    for column in range(columns):
        x = field.x + column * step
        (major if column % 4 == 0 else minor).add(
            svg.line(x, field.y, x, field.bottom)
        )
    for row in range(rows):
        y = field.y + row * step
        (major if row % 4 == 0 else minor).add(svg.line(field.x, y, field.right, y))
    group.add(minor)
    group.add(major)

    # Activated cells, biased towards the right so the text side stays quiet.
    cells = svg.group()
    for _ in range(rng.randint(10, 18)):
        column = rng.randint(0, max(0, columns - 2))
        row = rng.randint(0, max(0, rows - 2))
        if rng.chance(0.55) and column < columns * 0.45:
            continue
        weight = rng.uniform(0.05, 0.16)
        cells.add(
            svg.rect(
                field.x + column * step,
                field.y + row * step,
                step,
                step,
                ("fill", field.palette.accent.fade(weight).css()),
            )
        )
    group.add(cells)

    nodes = svg.group(("fill", field.palette.accent.fade(0.55).css()))
    for _ in range(rng.randint(6, 12)):
        column = rng.randint(0, columns - 1)
        row = rng.randint(0, rows - 1)
        nodes.add(svg.circle(field.x + column * step, field.y + row * step, 2.0))
    group.add(nodes)
    return _clip(field, group)


def orbits(field: Field) -> svg.Element:
    """Concentric rings with dashed arcs and satellites, centred off-frame."""
    rng = field.rng
    cx = field.x + field.width * rng.uniform(0.68, 0.94)
    cy = field.y + field.height * rng.uniform(0.30, 0.70)
    group = svg.group(("fill", "none"))

    largest = max(field.width, field.height) * 0.95
    count = rng.randint(11, 17)
    for index in range(count):
        radius = largest * (0.10 + 0.90 * (index / float(count - 1)) ** 1.35)
        dashed = rng.chance(0.42)
        attrs: List[Tuple[str, object]] = [
            ("stroke", field.ink(0.44 if dashed else 0.24, tint=0.2 + 0.5 * index / count)),
            ("stroke-width", 1.5 if dashed else 1.1),
        ]
        if dashed:
            segment = rng.uniform(6.0, 26.0)
            attrs.append(("stroke-dasharray", "%s %s" % (
                svg.format_number(segment), svg.format_number(segment * rng.uniform(0.8, 2.4))
            )))
        group.add(svg.circle(cx, cy, radius, *attrs))

        if rng.chance(0.5):
            angle = rng.uniform(0.0, math.tau)
            group.add(
                svg.circle(
                    cx + radius * math.cos(angle),
                    cy + radius * math.sin(angle),
                    rng.uniform(2.0, 4.5),
                    ("fill", field.palette.accent.fade(rng.uniform(0.35, 0.8)).css()),
                    ("stroke", "none"),
                )
            )
    return _clip(field, group)


def lattice(field: Field) -> svg.Element:
    """An isometric lattice with a subset of edges lit by the noise field."""
    rng = field.rng
    noise = field.noise("lattice")
    step = rng.choice((34.0, 42.0, 50.0))
    height_step = step * math.sqrt(3.0) / 2.0
    rows = int(math.ceil(field.height / height_step)) + 2
    columns = int(math.ceil(field.width / step)) + 2

    def node(column: int, row: int) -> Point:
        offset = (step / 2.0) if row % 2 else 0.0
        return (
            field.x + column * step + offset - step,
            field.y + row * height_step - height_step,
        )

    group = svg.group(("stroke-linecap", "round"))
    for row in range(rows):
        for column in range(columns):
            origin = node(column, row)
            for target in (node(column + 1, row), node(column, row + 1),
                           node(column - 1, row + 1)):
                midpoint = ((origin[0] + target[0]) / 2.0, (origin[1] + target[1]) / 2.0)
                energy = noise.fbm(midpoint[0] / 260.0, midpoint[1] / 260.0, octaves=3)
                if energy < -0.12:
                    continue
                lit = energy > 0.24
                group.add(
                    svg.line(
                        origin[0], origin[1], target[0], target[1],
                        ("stroke", field.ink(0.32 if lit else 0.12, tint=0.6 if lit else 0.0)),
                        ("stroke-width", 1.3 if lit else 0.9),
                    )
                )
    return _clip(field, group)


def constellation(field: Field) -> svg.Element:
    """Poisson-disc points joined to near neighbours: a graph, not a starfield."""
    rng = field.rng
    radius = rng.uniform(34.0, 46.0)
    points = _poisson_disc(field, radius, rng)

    group = svg.group()
    edges = svg.group(("stroke-linecap", "round"))
    link_distance = radius * 1.72
    seen = set()
    for index, first in enumerate(points):
        for other, second in enumerate(points):
            if other <= index:
                continue
            distance = math.hypot(first[0] - second[0], first[1] - second[1])
            if distance > link_distance:
                continue
            pair = (index, other)
            if pair in seen:
                continue
            seen.add(pair)
            closeness = 1.0 - distance / link_distance
            edges.add(
                svg.line(
                    first[0], first[1], second[0], second[1],
                    ("stroke", field.ink(0.08 + 0.24 * closeness, tint=0.35)),
                    ("stroke-width", 0.9),
                )
            )
    group.add(edges)

    nodes = svg.group(("stroke", "none"))
    for x, y in points:
        emphasis = rng.chance(0.14)
        nodes.add(
            svg.circle(
                x, y, rng.uniform(1.4, 2.4) * (2.0 if emphasis else 1.0),
                ("fill", (field.palette.accent if emphasis else field.palette.hairline)
                 .fade(0.7 if emphasis else 0.34).css()),
            )
        )
    group.add(nodes)
    return _clip(field, group)


def flowlines(field: Field) -> svg.Element:
    """Streamlines traced through the noise field's gradient."""
    rng = field.rng
    noise = field.noise("flow")
    scale = rng.uniform(210.0, 330.0)
    step = 7.0
    max_steps = 150
    lines = rng.randint(120, 170)

    group = svg.group(("fill", "none"), ("stroke-linecap", "round"))
    for index in range(lines):
        x = field.x + rng.random() * field.width
        y = field.y + rng.random() * field.height
        points: List[Point] = [(x, y)]
        for _ in range(max_steps):
            angle = noise.fbm(x / scale, y / scale, octaves=3) * math.pi * 2.4
            x += math.cos(angle) * step
            y += math.sin(angle) * step
            if not (field.x - 20 <= x <= field.right + 20
                    and field.y - 20 <= y <= field.bottom + 20):
                break
            points.append((x, y))
        if len(points) < 6:
            continue
        depth = index / float(lines)
        group.add(
            svg.polyline(
                points,
                ("stroke", field.ink(0.10 + 0.22 * (1.0 - depth), tint=0.2 + 0.6 * depth)),
                ("stroke-width", 0.9 + 0.7 * (1.0 - depth)),
            )
        )
    return _clip(field, group)


def strata(field: Field) -> svg.Element:
    """Horizontal bands of varying density, like a printed core sample."""
    rng = field.rng
    noise = field.noise("strata")
    group = svg.group()
    y = field.y
    while y < field.bottom:
        thickness = rng.uniform(5.0, 26.0)
        density = (noise.fbm(0.7, y / 190.0, octaves=3) + 1.0) / 2.0
        if density > 0.34:
            if rng.chance(0.30):
                # A ruled band: fine vertical strokes rather than a flat fill.
                spacing = rng.uniform(4.0, 9.0)
                band = svg.group(
                    ("stroke", field.ink(0.10 + 0.20 * density, tint=0.4)),
                    ("stroke-width", 1),
                )
                x = field.x
                while x < field.right:
                    band.add(svg.line(x, y, x, y + thickness))
                    x += spacing
                group.add(band)
            else:
                group.add(
                    svg.rect(
                        field.x, y, field.width, thickness,
                        ("fill", mix(field.palette.hairline, field.palette.accent, 0.3)
                         .fade(0.035 + 0.075 * density).css()),
                    )
                )
        y += thickness + rng.uniform(2.0, 12.0)
    return _clip(field, group)


def halftone(field: Field) -> svg.Element:
    """A dot screen whose radius follows the noise field."""
    rng = field.rng
    noise = field.noise("halftone")
    step = rng.choice((17.0, 21.0, 25.0))
    group = svg.group(("stroke", "none"))

    rows = int(field.height / step) + 2
    columns = int(field.width / step) + 2
    for row in range(rows):
        for column in range(columns):
            x = field.x + column * step + (step / 2.0 if row % 2 else 0.0)
            y = field.y + row * step
            value = (noise.fbm(x / 250.0, y / 250.0, octaves=3) + 1.0) / 2.0
            radius = (step * 0.42) * (value ** 2.1)
            if radius < 0.5:
                continue
            group.add(
                svg.circle(
                    x, y, radius,
                    ("fill", mix(field.palette.hairline, field.palette.accent, value)
                     .fade(0.10 + 0.32 * value).css()),
                )
            )
    return _clip(field, group)


# -- shared helpers ------------------------------------------------------


def _poisson_disc(field: Field, radius: float, rng: Rng) -> List[Point]:
    """Bridson's algorithm, with a margin so points do not crowd the frame."""
    margin = -radius * 0.5
    x0, y0 = field.x + margin, field.y + margin
    width = field.width - 2 * margin
    height = field.height - 2 * margin
    cell = radius / math.sqrt(2.0)
    columns = max(1, int(math.ceil(width / cell)))
    rows = max(1, int(math.ceil(height / cell)))
    grid: List[Optional[Point]] = [None] * (columns * rows)

    def store(point: Point) -> None:
        column = min(columns - 1, int((point[0] - x0) / cell))
        row = min(rows - 1, int((point[1] - y0) / cell))
        grid[row * columns + column] = point

    def fits(point: Point) -> bool:
        if not (x0 <= point[0] < x0 + width and y0 <= point[1] < y0 + height):
            return False
        column = min(columns - 1, int((point[0] - x0) / cell))
        row = min(rows - 1, int((point[1] - y0) / cell))
        for dy in range(-2, 3):
            for dx in range(-2, 3):
                nx, ny = column + dx, row + dy
                if not (0 <= nx < columns and 0 <= ny < rows):
                    continue
                neighbour = grid[ny * columns + nx]
                if neighbour is not None and math.hypot(
                    neighbour[0] - point[0], neighbour[1] - point[1]
                ) < radius:
                    return False
        return True

    first = (x0 + rng.random() * width, y0 + rng.random() * height)
    points: List[Point] = [first]
    store(first)
    active = [first]
    while active:
        index = rng.randint(0, len(active) - 1)
        origin = active[index]
        placed = False
        for _ in range(24):
            angle = rng.random() * math.tau
            distance = radius * (1.0 + rng.random())
            candidate = (
                origin[0] + math.cos(angle) * distance,
                origin[1] + math.sin(angle) * distance,
            )
            if fits(candidate):
                points.append(candidate)
                store(candidate)
                active.append(candidate)
                placed = True
                break
        if not placed:
            active.pop(index)
    return points


#: Every pattern a manifest may name. The default is fixed in
#: :data:`gspg.model.DEFAULT_PATTERN`; the rest are available as an override
#: for a repository that deliberately wants to look different.
PATTERNS: Dict[str, Callable[[Field], svg.Element]] = {
    "topography": topography,
    "ridgeline": ridgeline,
    "precision-grid": precision_grid,
    "orbits": orbits,
    "lattice": lattice,
    "constellation": constellation,
    "flowlines": flowlines,
    "strata": strata,
    "halftone": halftone,
}

def names() -> List[str]:
    return sorted(PATTERNS)


def resolve(name: Optional[str]) -> str:
    """Validate a pattern name, falling back to the house default."""
    from .model import DEFAULT_PATTERN

    if not name:
        return DEFAULT_PATTERN
    if name not in PATTERNS:
        raise RenderError(
            "unknown pattern %r; available: %s" % (name, ", ".join(names()))
        )
    return name


def draw(name: str, field: Field) -> svg.Element:
    """Run the named generator against ``field``."""
    generator = PATTERNS.get(name)
    if generator is None:
        raise RenderError("unknown pattern %r" % (name,))
    return generator(field)
