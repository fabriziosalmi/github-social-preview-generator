"""Palette derivation.

A repository name is hashed into a hue, and the whole palette is built around
that hue in OKLCH so every theme has the same perceived lightness and
saturation structure. Two repositories therefore look like siblings rather
than like two unrelated colour accidents, which is the practical difference
between a house style and a random generator.

Hues sit on a curated wheel instead of the full circle: the yellow-green band
around 100-130 degrees cannot be made to read as a calm enterprise accent at
the chroma the templates use, so it is simply not offered.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence

from .color import Oklch, contrast_ratio, mix, srgb_to_oklch
from .errors import ConfigError
from .seed import Rng

#: Anchor hues, in OKLCH degrees, that hold up as a primary accent on a near
#: black ground. Names are the vocabulary the manifest may pin a repo to.
ACCENT_HUES: Dict[str, float] = {
    "azure": 250.0,
    "indigo": 275.0,
    "violet": 300.0,
    "magenta": 330.0,
    "crimson": 15.0,
    "amber": 65.0,
    "lime": 130.0,
    "emerald": 155.0,
    "teal": 185.0,
    "cyan": 215.0,
}

#: Minimum contrast the primary text must reach against the background. 7.0 is
#: the WCAG AAA threshold for body copy; a social card is read at a glance and
#: often on a phone in daylight, so the stricter bar is the right one.
MIN_TEXT_CONTRAST = 7.0

#: Minimum contrast for secondary text, where WCAG AA is a sensible floor.
MIN_MUTED_CONTRAST = 4.5


class Palette:
    """The resolved colours a template draws with."""

    __slots__ = (
        "mode", "hue", "accent_name",
        "background", "background_deep", "surface", "hairline",
        "ink", "ink_muted", "ink_faint",
        "accent", "accent_soft", "accent_deep", "secondary",
    )

    def __init__(self, **colors: object) -> None:
        for name in self.__slots__:
            setattr(self, name, colors[name])

    def as_dict(self) -> Dict[str, str]:
        """Flatten to CSS strings, for debugging and for the swatch sheet."""
        result: Dict[str, str] = {
            "mode": self.mode,
            "accent_name": self.accent_name,
            "hue": "%.1f" % (self.hue,),
        }
        for name in self.__slots__:
            value = getattr(self, name)
            if isinstance(value, Oklch):
                result[name] = value.hex()
        return result

    def check_contrast(self) -> List[str]:
        """Return human-readable warnings for any pairing that reads poorly."""
        warnings: List[str] = []
        pairs = (
            ("ink", self.ink, MIN_TEXT_CONTRAST),
            ("ink_muted", self.ink_muted, MIN_MUTED_CONTRAST),
            ("accent", self.accent, MIN_MUTED_CONTRAST),
        )
        for name, color, minimum in pairs:
            ratio = contrast_ratio(color, self.background)
            if ratio < minimum:
                warnings.append(
                    "%s on background is %.2f:1, below the %.1f:1 floor"
                    % (name, ratio, minimum)
                )
        return warnings


def resolve_hue(seed: str, accent: Optional[str] = None) -> float:
    """Pick the accent hue for ``seed``, honouring an explicit ``accent``.

    ``accent`` may be a name from :data:`ACCENT_HUES`, a hue in degrees, or a
    hex colour whose hue is adopted.
    """
    if accent:
        text = accent.strip().lower()
        if text in ACCENT_HUES:
            return ACCENT_HUES[text]
        if text.startswith("#"):
            return srgb_to_oklch(text).hue
        try:
            return float(text) % 360.0
        except ValueError:
            raise ConfigError(
                "unknown accent %r; use a hex colour, a hue in degrees, or one of: %s"
                % (accent, ", ".join(sorted(ACCENT_HUES)))
            )
    names: Sequence[str] = sorted(ACCENT_HUES)
    rng = Rng("gspg.palette.v1", seed)
    # Jitter inside the band so two repositories on the same anchor still differ.
    return (ACCENT_HUES[rng.choice(names)] + rng.uniform(-9.0, 9.0)) % 360.0


def nearest_accent_name(hue: float) -> str:
    """The curated name closest to ``hue``, for reporting."""
    def distance(item):
        return abs((item[1] - hue + 180.0) % 360.0 - 180.0)

    return min(ACCENT_HUES.items(), key=distance)[0]


def build(
    seed: str,
    accent: Optional[str] = None,
    mode: str = "dark",
    saturation: float = 1.0,
) -> Palette:
    """Derive a full palette for ``seed``.

    ``saturation`` scales chroma across the board, so a manifest can dial a
    single repository towards monochrome without redefining every colour.
    """
    if mode not in ("dark", "light"):
        raise ConfigError("theme mode must be 'dark' or 'light', not %r" % (mode,))
    hue = resolve_hue(seed, accent)
    rng = Rng("gspg.palette.shade.v1", seed, mode)
    # The ground carries a trace of the accent so the card reads as one object
    # rather than as artwork pasted onto neutral grey.
    tint = 0.016 * saturation

    if mode == "dark":
        background = Oklch(0.1650 + rng.uniform(-0.006, 0.006), tint, hue)
        background_deep = background.darken(0.055).saturate(1.15)
        surface = background.lighten(0.075).saturate(1.3)
        hairline = Oklch(0.62, 0.010 * saturation, hue)
        ink = Oklch(0.975, 0.004 * saturation, hue)
        ink_muted = Oklch(0.760, 0.014 * saturation, hue)
        ink_faint = Oklch(0.560, 0.014 * saturation, hue)
        accent_color = Oklch(0.760, 0.150 * saturation, hue)
        accent_soft = Oklch(0.840, 0.085 * saturation, hue)
        accent_deep = Oklch(0.500, 0.145 * saturation, hue)
    else:
        background = Oklch(0.975, 0.006 * saturation, hue)
        background_deep = background.darken(0.030)
        surface = Oklch(1.000, 0.0, hue)
        hairline = Oklch(0.480, 0.012 * saturation, hue)
        ink = Oklch(0.230, 0.020 * saturation, hue)
        ink_muted = Oklch(0.440, 0.020 * saturation, hue)
        ink_faint = Oklch(0.600, 0.018 * saturation, hue)
        accent_color = Oklch(0.520, 0.165 * saturation, hue)
        accent_soft = Oklch(0.640, 0.120 * saturation, hue)
        accent_deep = Oklch(0.380, 0.150 * saturation, hue)

    # A secondary hue a third of the wheel away gives gradients somewhere to
    # travel; the direction is seeded so it is stable per repository.
    direction = 1.0 if Rng("gspg.palette.secondary.v1", seed).chance(0.5) else -1.0
    secondary = accent_color.rotate(direction * 44.0).with_(
        chroma=accent_color.chroma * 0.88
    )

    palette = Palette(
        mode=mode,
        hue=hue,
        accent_name=nearest_accent_name(hue),
        background=background,
        background_deep=background_deep,
        surface=surface,
        hairline=hairline,
        ink=ink,
        ink_muted=ink_muted,
        ink_faint=ink_faint,
        accent=accent_color,
        accent_soft=accent_soft,
        accent_deep=accent_deep,
        secondary=secondary,
    )

    # Nudge text lightness until it clears the contrast floor. Chroma and hue
    # are untouched, so the palette keeps its character while becoming legible.
    palette.ink = _enforce_contrast(palette.ink, background, MIN_TEXT_CONTRAST, mode)
    palette.ink_muted = _enforce_contrast(
        palette.ink_muted, background, MIN_MUTED_CONTRAST, mode
    )
    return palette


def _enforce_contrast(
    color: Oklch, background: Oklch, minimum: float, mode: str
) -> Oklch:
    step = 0.01 if mode == "dark" else -0.01
    adjusted = color
    for _ in range(60):
        if contrast_ratio(adjusted, background) >= minimum:
            return adjusted
        next_lightness = adjusted.lightness + step
        if not 0.0 <= next_lightness <= 1.0:
            return adjusted
        adjusted = adjusted.with_(lightness=next_lightness)
    return adjusted


def blend(first: Oklch, second: Oklch, amount: float) -> Oklch:
    """Re-exported for templates that want an OKLCH interpolation."""
    return mix(first, second, amount)
