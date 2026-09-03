"""The card layout.

One composition, used for every repository: a left-aligned title and
description set black on near-white, with a coloured spine down the left edge.
The house style is the form; the hue is the only thing that changes.

Three decisions drive everything here, and each answers a way the card is
actually seen:

* **It is read in a feed, not zoomed.** A social platform renders this at
  roughly 430-550px wide, so anything under about 30px on the 1280px canvas is
  illegible. There is nothing that small on the card.
* **It sits on a white page.** Without an edge, a near-white card dissolves
  into the surrounding feed. The spine gives it a boundary at any thumbnail
  size, and carries the repository's colour while it is there.
* **Neither type size is a constant.** The title and the description compete
  for one fixed height, so the layout solves for them: the title takes as much
  as it can, and the description takes the largest size at which its whole text
  still fits. Nothing is ever truncated.
"""

from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

from . import patterns, svg
from .color import Oklch, mix
from .errors import RenderError
from .model import Preview
from .palette import Palette
from .seed import Rng
from .typography import Face, format_number

WIDTH = 1280.0
HEIGHT = 640.0

#: Nothing meaningful is drawn outside this inset, so a re-crop by another
#: platform still contains the whole message.
MARGIN = 96.0
TOP = 92.0
BOTTOM = 88.0

DISPLAY_FACE = "InterDisplay-SemiBold"
BODY_FACE = "Inter-Regular"

#: The title is set as large as it fits, in whole steps so two cards with
#: similar names do not land a hair apart.
TITLE_LARGEST = 184.0
TITLE_SMALLEST = 84.0
TITLE_STEP = 4.0
TITLE_LEADING = 0.98
TITLE_TRACKING = -0.03
TITLE_MAX_LINES = 2

#: The description is solved for rather than fixed. The ceiling exists only to
#: stop a three-word description from competing with the title.
DESC_LARGEST = 64.0
DESC_SMALLEST = 30.0
DESC_STEP = 2.0
DESC_LEADING_RATIO = 1.33

#: Baseline to baseline between the two registers. Generous on purpose: they
#: are a title and a subtitle, not one block of text.
GAP = 140.0

#: The field is seeded from this constant rather than from the repository, so
#: the texture is identical on every card and only its hue changes. Seeding it
#: per repository would vary the form too, which is the opposite of a house
#: style.
FIELD_SEED = "gspg.card.field.v1"
FIELD_OPACITY = 0.28

#: Width of the coloured spine.
SPINE = 22.0


class Composition:
    """Shared state and drawing primitives for a single card."""

    def __init__(self, preview: Preview, palette: Palette, seed: str) -> None:
        self.preview = preview
        self.palette = palette
        self.seed = seed
        self.rng = Rng("gspg.compose.v2", seed)
        self.document = svg.Document(
            WIDTH, HEIGHT, generator="github-social-preview-generator"
        )
        self.display = Face.load(DISPLAY_FACE)
        self.body = Face.load(BODY_FACE)

    # -- primitives ------------------------------------------------------

    def text(
        self,
        face: Face,
        content: str,
        size: float,
        x: float,
        y: float,
        color: Oklch,
        tracking: float = 0.0,
        anchor: str = "start",
    ) -> float:
        run = face.run(content, size, x, y, tracking, anchor)
        element = svg.text(run, ("fill", color.css()))
        if element is not None:
            self.document.body.add(element)
        return run.width

    def paragraph(
        self,
        face: Face,
        lines: Sequence[str],
        size: float,
        x: float,
        first_baseline: float,
        leading: float,
        color: Oklch,
        tracking: float = 0.0,
    ) -> float:
        """Draw stacked lines and return the baseline of the last one."""
        baseline = first_baseline
        for index, line in enumerate(lines):
            self.text(face, line, size, x, baseline, color, tracking)
            if index < len(lines) - 1:
                baseline += leading
        return baseline

    # -- ground ----------------------------------------------------------

    def paint_paper(self) -> None:
        """A near-white ground carrying a trace of the hue, like printed stock."""
        base = Oklch(0.982, 0.004, self.palette.hue)
        edge = Oklch(0.952, 0.012, self.palette.hue)
        self.document.body.add(svg.rect(0, 0, WIDTH, HEIGHT, ("fill", base.hex())))
        gradient = svg.linear_gradient(
            self.document.unique_id("paper"),
            [(0.0, base.hex()), (0.62, base.hex()), (1.0, edge.hex())],
            x1=0.0, y1=0.0, x2=0.65, y2=1.0,
        )
        self.document.body.add(
            svg.rect(0, 0, WIDTH, HEIGHT, ("fill", self.document.define(gradient)))
        )

    def paint_field(self, pattern: str) -> None:
        field = patterns.Field(
            self.document, self.palette, Rng(FIELD_SEED),
            -40.0, -40.0, WIDTH + 80.0, HEIGHT + 80.0,
        )
        element = patterns.draw(pattern, field)
        element.attrs.append(("opacity", format_number(FIELD_OPACITY)))
        self.document.body.add(element)

    def paint_spine(self) -> None:
        """The coloured edge. Without it the card dissolves into a white feed."""
        self.document.body.add(
            svg.rect(0, 0, SPINE, HEIGHT, ("fill", self.palette.accent_deep.hex()))
        )
        self.document.body.add(
            svg.rect(
                0.75, 0.75, WIDTH - 1.5, HEIGHT - 1.5,
                ("fill", "none"),
                ("stroke", self.ink.fade(0.18).css()),
                ("stroke-width", 1.5),
            )
        )

    # -- colours ---------------------------------------------------------

    @property
    def ink(self) -> Oklch:
        """Near-black, with a trace of the hue so it sits on the paper."""
        return Oklch(0.175, 0.012, self.palette.hue)

    @property
    def muted(self) -> Oklch:
        return mix(self.ink, Oklch(0.55, 0.012, self.palette.hue), 0.60)


# -- the solver ----------------------------------------------------------


class Fit:
    """The type sizes and line breaks chosen for one card."""

    __slots__ = ("title_size", "title_lines", "desc_size", "desc_lines", "height")

    def __init__(self, title_size, title_lines, desc_size, desc_lines, height) -> None:
        self.title_size = title_size
        self.title_lines = title_lines
        self.desc_size = desc_size
        self.desc_lines = desc_lines
        self.height = height


def solve(
    composition: Composition, column: float, available: float
) -> Fit:
    """Choose type sizes so both blocks fit, without ever cutting the text.

    The title claims space first. Giving the description priority instead
    produces a headline that shrinks to within a few points of its own
    subtitle, at which point the card reads as two paragraphs rather than as a
    name with an explanation under it.
    """
    preview = composition.preview
    display, body = composition.display, composition.body

    def measure(title_size, title_lines, desc_size, desc_lines) -> float:
        height = display.cap_height_px(title_size) \
            + title_size * TITLE_LEADING * (len(title_lines) - 1)
        if desc_lines:
            height += GAP + desc_size * DESC_LEADING_RATIO * (len(desc_lines) - 1) \
                + body.cap_height_px(desc_size)
        return height

    def wrap_description(size: float) -> List[str]:
        if not preview.description:
            return []
        # No line limit, so nothing is ever ellipsised.
        return body.wrap(preview.description, size, column, 99, balance=False)

    def wrap_title(size: float) -> Optional[List[str]]:
        """Natural line breaks at ``size``, or None if the title cannot fit.

        Asking for the balanced two-line version directly would hide the
        failure: ``wrap`` ellipsises whatever does not fit, and a truncated
        headline still measures narrower than the column. So the natural break
        is taken first and the size rejected if it needs a third line.
        """
        natural = display.wrap(preview.title, size, column, TITLE_MAX_LINES + 1,
                               TITLE_TRACKING, balance=False)
        if len(natural) > TITLE_MAX_LINES:
            return None
        lines = display.wrap(preview.title, size, column, TITLE_MAX_LINES, TITLE_TRACKING)
        widest = max(
            (display.measure(line, size, TITLE_TRACKING) for line in lines), default=0.0
        )
        return lines if widest <= column else None

    title_size = TITLE_LARGEST
    while title_size >= TITLE_SMALLEST:
        title_lines = wrap_title(title_size)
        if title_lines is not None:
            desc_size = DESC_LARGEST
            while desc_size >= DESC_SMALLEST:
                desc_lines = wrap_description(desc_size)
                height = measure(title_size, title_lines, desc_size, desc_lines)
                if height <= available:
                    return Fit(title_size, title_lines, desc_size, desc_lines, height)
                desc_size -= DESC_STEP
        title_size -= TITLE_STEP

    # Nothing fits even at both floors: keep the text whole and overflow the
    # safe area rather than cutting anything in half.
    title_lines = display.wrap(
        preview.title, TITLE_SMALLEST, column, TITLE_MAX_LINES, TITLE_TRACKING
    )
    desc_lines = wrap_description(DESC_SMALLEST)
    return Fit(
        TITLE_SMALLEST, title_lines, DESC_SMALLEST, desc_lines,
        measure(TITLE_SMALLEST, title_lines, DESC_SMALLEST, desc_lines),
    )


def card(composition: Composition, pattern: str) -> None:
    """Draw the whole card."""
    composition.paint_paper()
    composition.paint_field(pattern)

    column = WIDTH - MARGIN * 2.0
    available = HEIGHT - TOP - BOTTOM
    fit = solve(composition, column, available)

    first = TOP + max(0.0, (available - fit.height) / 2.0) \
        + composition.display.cap_height_px(fit.title_size)
    baseline = composition.paragraph(
        composition.display, fit.title_lines, fit.title_size, MARGIN, first,
        fit.title_size * TITLE_LEADING, composition.ink, tracking=TITLE_TRACKING,
    )
    if fit.desc_lines:
        composition.paragraph(
            composition.body, fit.desc_lines, fit.desc_size, MARGIN,
            baseline + GAP, fit.desc_size * DESC_LEADING_RATIO, composition.muted,
        )
    composition.paint_spine()


def render_into(preview: Preview, palette: Palette, seed: str, pattern: str) -> Composition:
    """Build a finished composition for ``preview``."""
    if pattern not in patterns.PATTERNS:
        raise RenderError(
            "unknown pattern %r; available: %s"
            % (pattern, ", ".join(patterns.names()))
        )
    composition = Composition(preview, palette, seed)
    card(composition, pattern)
    return composition
