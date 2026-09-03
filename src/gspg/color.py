"""Perceptual colour in OKLCH, with gamut mapping to sRGB hex.

Palettes are built in OKLCH rather than HSL so that a hue rotation keeps the
same apparent lightness and saturation. That is the difference between a set
of themes that look designed and a set that looks randomly hue-shifted.

Every conversion here is pure arithmetic on floats: no lookup tables, no
platform-dependent behaviour, so a palette is reproducible bit for bit.
"""

from __future__ import annotations

import math
from typing import Tuple

__all__ = ["Oklch", "srgb_to_oklch", "clamp", "mix", "relative_luminance", "contrast_ratio"]

# OKLab <-> linear sRGB, Björn Ottosson's matrices.
_LMS_FROM_OKLAB = (
    (1.0, 0.3963377774, 0.2158037573),
    (1.0, -0.1055613458, -0.0638541728),
    (1.0, -0.0894841775, -1.2914855480),
)
_LINEAR_FROM_LMS = (
    (4.0767416621, -3.3077115913, 0.2309699292),
    (-1.2684380046, 2.6097574011, -0.3413193965),
    (-0.0041960863, -0.7034186147, 1.7076147010),
)
_LMS_FROM_LINEAR = (
    (0.4122214708, 0.5363325363, 0.0514459929),
    (0.2119034982, 0.6806995451, 0.1073969566),
    (0.0883024619, 0.2817188376, 0.6299787005),
)
_OKLAB_FROM_LMS = (
    (0.2104542553, 0.7936177850, -0.0040720468),
    (1.9779984951, -2.4285922050, 0.4505937099),
    (0.0259040371, 0.7827717662, -0.8086757660),
)


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return low if value < low else (high if value > high else value)


def _encode_srgb(channel: float) -> float:
    """Linear light to sRGB electro-optical encoding."""
    if channel <= 0.0031308:
        return 12.92 * channel
    return 1.055 * (channel ** (1.0 / 2.4)) - 0.055


def _decode_srgb(channel: float) -> float:
    if channel <= 0.04045:
        return channel / 12.92
    return ((channel + 0.055) / 1.055) ** 2.4


def _oklab_to_linear(lightness: float, a: float, b: float) -> Tuple[float, float, float]:
    lms = [row[0] * lightness + row[1] * a + row[2] * b for row in _LMS_FROM_OKLAB]
    lms = [value ** 3 for value in lms]
    return tuple(  # type: ignore[return-value]
        sum(row[i] * lms[i] for i in range(3)) for row in _LINEAR_FROM_LMS
    )


class Oklch:
    """A colour as lightness (0..1), chroma (0..~0.4) and hue (degrees)."""

    __slots__ = ("lightness", "chroma", "hue", "alpha")

    def __init__(self, lightness: float, chroma: float, hue: float, alpha: float = 1.0) -> None:
        self.lightness = clamp(lightness)
        self.chroma = max(0.0, chroma)
        self.hue = hue % 360.0
        self.alpha = clamp(alpha)

    def __repr__(self) -> str:
        return "Oklch(%.4f, %.4f, %.1f, %.3f)" % (
            self.lightness, self.chroma, self.hue, self.alpha,
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Oklch):
            return NotImplemented
        return self.hex() == other.hex() and abs(self.alpha - other.alpha) < 1e-9

    def __hash__(self) -> int:
        return hash((self.hex(), round(self.alpha, 6)))

    # -- derivation ------------------------------------------------------

    def with_(
        self,
        lightness: float = None,
        chroma: float = None,
        hue: float = None,
        alpha: float = None,
    ) -> "Oklch":
        return Oklch(
            self.lightness if lightness is None else lightness,
            self.chroma if chroma is None else chroma,
            self.hue if hue is None else hue,
            self.alpha if alpha is None else alpha,
        )

    def lighten(self, amount: float) -> "Oklch":
        return self.with_(lightness=clamp(self.lightness + amount))

    def darken(self, amount: float) -> "Oklch":
        return self.lighten(-amount)

    def saturate(self, factor: float) -> "Oklch":
        return self.with_(chroma=max(0.0, self.chroma * factor))

    def rotate(self, degrees: float) -> "Oklch":
        return self.with_(hue=self.hue + degrees)

    def fade(self, alpha: float) -> "Oklch":
        return self.with_(alpha=clamp(alpha))

    # -- output ----------------------------------------------------------

    def _linear_rgb(self, chroma: float) -> Tuple[float, float, float]:
        radians = math.radians(self.hue)
        return _oklab_to_linear(
            self.lightness, chroma * math.cos(radians), chroma * math.sin(radians)
        )

    def _in_gamut(self, rgb: Tuple[float, float, float], tolerance: float = 1e-6) -> bool:
        return all(-tolerance <= channel <= 1.0 + tolerance for channel in rgb)

    def rgb(self) -> Tuple[int, int, int]:
        """Return 8-bit sRGB, reducing chroma until the colour fits the gamut.

        Lightness and hue are preserved; only chroma gives way. This is the
        behaviour CSS Color 4 specifies for gamut mapping, and it keeps a
        rotated palette visually even instead of letting saturated hues clip.
        """
        linear = self._linear_rgb(self.chroma)
        if not self._in_gamut(linear):
            low, high = 0.0, self.chroma
            for _ in range(24):  # ~1e-7 of chroma, far below 8-bit resolution
                mid = (low + high) / 2.0
                if self._in_gamut(self._linear_rgb(mid)):
                    low = mid
                else:
                    high = mid
            linear = self._linear_rgb(low)
        return tuple(  # type: ignore[return-value]
            int(round(clamp(_encode_srgb(clamp(channel))) * 255.0)) for channel in linear
        )

    def hex(self) -> str:
        return "#%02x%02x%02x" % self.rgb()

    def css(self) -> str:
        """Hex, or ``rgba()`` when the colour is translucent."""
        if self.alpha >= 1.0:
            return self.hex()
        red, green, blue = self.rgb()
        return "rgba(%d,%d,%d,%s)" % (red, green, blue, _format_alpha(self.alpha))


def _format_alpha(alpha: float) -> str:
    text = "%.4f" % (alpha,)
    text = text.rstrip("0").rstrip(".")
    return text or "0"


def srgb_to_oklch(value: str) -> Oklch:
    """Parse ``#rgb``/``#rrggbb``/``#rrggbbaa`` into OKLCH."""
    text = value.strip().lstrip("#")
    if len(text) == 3:
        text = "".join(channel * 2 for channel in text)
    if len(text) not in (6, 8) or any(c not in "0123456789abcdefABCDEF" for c in text):
        raise ValueError("not a hex colour: %r" % (value,))
    channels = [int(text[i : i + 2], 16) / 255.0 for i in range(0, len(text), 2)]
    alpha = channels[3] if len(channels) == 4 else 1.0
    linear = [_decode_srgb(channel) for channel in channels[:3]]
    lms = [
        sum(row[i] * linear[i] for i in range(3)) ** (1.0 / 3.0)
        if sum(row[i] * linear[i] for i in range(3)) >= 0
        else -((-sum(row[i] * linear[i] for i in range(3))) ** (1.0 / 3.0))
        for row in _LMS_FROM_LINEAR
    ]
    lightness, a, b = (
        sum(row[i] * lms[i] for i in range(3)) for row in _OKLAB_FROM_LMS
    )
    chroma = math.hypot(a, b)
    hue = math.degrees(math.atan2(b, a)) % 360.0
    return Oklch(lightness, chroma, hue, alpha)


def mix(first: Oklch, second: Oklch, amount: float) -> Oklch:
    """Interpolate in OKLCH, taking the shorter way around the hue circle."""
    amount = clamp(amount)
    delta = (second.hue - first.hue + 540.0) % 360.0 - 180.0
    return Oklch(
        first.lightness + (second.lightness - first.lightness) * amount,
        first.chroma + (second.chroma - first.chroma) * amount,
        first.hue + delta * amount,
        first.alpha + (second.alpha - first.alpha) * amount,
    )


def relative_luminance(color: Oklch) -> float:
    """WCAG relative luminance of the gamut-mapped sRGB colour."""
    red, green, blue = (channel / 255.0 for channel in color.rgb())
    return (
        0.2126 * _decode_srgb(red)
        + 0.7152 * _decode_srgb(green)
        + 0.0722 * _decode_srgb(blue)
    )


def contrast_ratio(foreground: Oklch, background: Oklch) -> float:
    """WCAG 2.1 contrast ratio, from 1.0 to 21.0."""
    first = relative_luminance(foreground)
    second = relative_luminance(background)
    lighter, darker = max(first, second), min(first, second)
    return (lighter + 0.05) / (darker + 0.05)
