"""A small, explicit SVG document builder.

Two rules shape this module, both in service of previews that look the same
everywhere:

* **No filters.** ``feTurbulence``, ``feGaussianBlur`` and friends are
  specified loosely enough that librsvg, Chromium and resvg disagree on the
  result. Everything here is paths, shapes and gradients, which they render
  identically. Grain and softness are produced geometrically instead.
* **No text elements.** Strings arrive already converted to outlines by
  :mod:`gspg.typography`, so no font needs to be installed or matched.

The emitted document is deterministic: identical input yields byte-identical
output, which is what makes the checksums in the lock file meaningful.
"""

from __future__ import annotations

from typing import Iterable, List, Optional, Sequence, Tuple, Union

from .typography import TextRun, format_number

Number = Union[int, float]
Attrs = Sequence[Tuple[str, object]]

_ESCAPES = (("&", "&amp;"), ("<", "&lt;"), (">", "&gt;"), ('"', "&quot;"))


def escape(value: str) -> str:
    """Escape a string for use in an XML attribute or text node."""
    for needle, replacement in _ESCAPES:
        value = value.replace(needle, replacement)
    return value


def _attr_value(value: object) -> str:
    if isinstance(value, bool):
        raise TypeError("SVG attributes take no booleans; pass an explicit string")
    if isinstance(value, float):
        return format_number(value)
    if isinstance(value, int):
        return str(value)
    return escape(str(value))


def _render_attrs(attrs: Attrs) -> str:
    return "".join(
        ' %s="%s"' % (name, _attr_value(value)) for name, value in attrs if value is not None
    )


class Element:
    """One SVG node. Children are rendered indented beneath the open tag."""

    __slots__ = ("tag", "attrs", "children", "text")

    def __init__(
        self,
        tag: str,
        attrs: Optional[Attrs] = None,
        children: Optional[List["Element"]] = None,
        text: Optional[str] = None,
    ) -> None:
        self.tag = tag
        self.attrs: List[Tuple[str, object]] = list(attrs or ())
        self.children: List[Element] = list(children or ())
        self.text = text

    def add(self, child: Optional["Element"]) -> "Element":
        """Append ``child`` and return it; ``None`` is ignored for easy chaining."""
        if child is not None:
            self.children.append(child)
        return child  # type: ignore[return-value]

    def extend(self, children: Iterable[Optional["Element"]]) -> "Element":
        for child in children:
            self.add(child)
        return self

    def render(self, indent: int = 0, step: str = "  ") -> List[str]:
        pad = step * indent
        opening = "%s<%s%s" % (pad, self.tag, _render_attrs(self.attrs))
        if not self.children and self.text is None:
            return [opening + "/>"]
        if self.text is not None and not self.children:
            return [opening + ">" + escape(self.text) + "</" + self.tag + ">"]
        lines = [opening + ">"]
        for child in self.children:
            lines.extend(child.render(indent + 1, step))
        lines.append("%s</%s>" % (pad, self.tag))
        return lines


# -- shorthand constructors ----------------------------------------------


def group(*attrs: Tuple[str, object]) -> Element:
    return Element("g", attrs)


def rect(
    x: Number, y: Number, width: Number, height: Number, *attrs: Tuple[str, object]
) -> Element:
    return Element("rect", (("x", x), ("y", y), ("width", width), ("height", height)) + attrs)


def circle(cx: Number, cy: Number, r: Number, *attrs: Tuple[str, object]) -> Element:
    return Element("circle", (("cx", cx), ("cy", cy), ("r", r)) + attrs)


def path(d: str, *attrs: Tuple[str, object]) -> Element:
    return Element("path", (("d", d),) + attrs)


def line(
    x1: Number, y1: Number, x2: Number, y2: Number, *attrs: Tuple[str, object]
) -> Element:
    return Element("line", (("x1", x1), ("y1", y1), ("x2", x2), ("y2", y2)) + attrs)


def polyline(points: Sequence[Tuple[float, float]], *attrs: Tuple[str, object]) -> Element:
    """A polyline emitted as a path, which serialises shorter than ``points``."""
    if not points:
        raise ValueError("a polyline needs at least one point")
    d = "M" + " L".join(
        "%s %s" % (format_number(x), format_number(y)) for x, y in points
    )
    return path(d, *attrs)


def text(run: TextRun, *attrs: Tuple[str, object]) -> Optional[Element]:
    """Emit a laid-out :class:`~gspg.typography.TextRun` as a filled path."""
    if not run:
        return None
    return Element("path", (("d", run.d), ("transform", run.transform)) + attrs)


def linear_gradient(
    gradient_id: str,
    stops: Sequence[Tuple[float, str]],
    x1: Number = 0,
    y1: Number = 0,
    x2: Number = 1,
    y2: Number = 0,
) -> Element:
    """A gradient in objectBoundingBox units, the portable default."""
    element = Element(
        "linearGradient",
        (("id", gradient_id), ("x1", x1), ("y1", y1), ("x2", x2), ("y2", y2)),
    )
    element.extend(_stops(stops))
    return element


def radial_gradient(
    gradient_id: str,
    stops: Sequence[Tuple[float, str]],
    cx: Number = 0.5,
    cy: Number = 0.5,
    r: Number = 0.5,
) -> Element:
    element = Element(
        "radialGradient", (("id", gradient_id), ("cx", cx), ("cy", cy), ("r", r))
    )
    element.extend(_stops(stops))
    return element


def _stops(stops: Sequence[Tuple[float, str]]) -> List[Element]:
    rendered: List[Element] = []
    for offset, color in stops:
        attrs: List[Tuple[str, object]] = [("offset", float(offset))]
        if color.startswith("rgba("):
            # Split rgba() into the colour/opacity pair SVG 1.1 understands.
            body = color[5:-1]
            red, green, blue, alpha = (part.strip() for part in body.split(","))
            attrs.append(("stop-color", "rgb(%s,%s,%s)" % (red, green, blue)))
            attrs.append(("stop-opacity", alpha))
        else:
            attrs.append(("stop-color", color))
        rendered.append(Element("stop", attrs))
    return rendered


class Document:
    """A complete SVG document with a defs section and a body."""

    def __init__(self, width: Number, height: Number, generator: Optional[str] = None) -> None:
        self.width = width
        self.height = height
        self.generator = generator
        self.defs = Element("defs")
        self.body = Element("g", (("id", "artwork"),))
        self._ids = 0

    def unique_id(self, prefix: str) -> str:
        """A document-local id; the counter keeps output stable across runs."""
        self._ids += 1
        return "%s-%d" % (prefix, self._ids)

    def define(self, element: Element) -> str:
        """Add ``element`` to ``<defs>`` and return the ``url(#id)`` reference."""
        for name, value in element.attrs:
            if name == "id":
                self.defs.add(element)
                return "url(#%s)" % (value,)
        raise ValueError("elements added to <defs> must carry an id")

    def to_string(self) -> str:
        root = Element(
            "svg",
            (
                ("xmlns", "http://www.w3.org/2000/svg"),
                ("width", self.width),
                ("height", self.height),
                ("viewBox", "0 0 %s %s"
                 % (format_number(self.width), format_number(self.height))),
                ("shape-rendering", "geometricPrecision"),
            ),
        )
        if self.defs.children:
            root.add(self.defs)
        root.add(self.body)
        lines = ['<?xml version="1.0" encoding="UTF-8"?>']
        if self.generator:
            lines.append("<!-- %s -->" % (escape(self.generator).replace("--", "- -"),))
        lines.extend(root.render())
        return "\n".join(lines) + "\n"
