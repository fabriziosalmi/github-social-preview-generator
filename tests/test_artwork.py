"""Palettes, marks, patterns and templates: determinism and bounds."""

from __future__ import annotations

import unittest
import xml.etree.ElementTree as ElementTree

from gspg import palette, patterns, svg, templates
from gspg.errors import ConfigError, RenderError
from gspg.model import Preview
from gspg.palette import ACCENT_HUES, MIN_MUTED_CONTRAST, MIN_TEXT_CONTRAST
from gspg.seed import Rng

SEEDS = ["owner/alpha", "owner/beta", "owner/gamma", "owner/delta", "owner/epsilon"]


class Palettes(unittest.TestCase):
    def test_a_palette_is_a_function_of_its_seed(self):
        for seed in SEEDS:
            self.assertEqual(palette.build(seed).as_dict(), palette.build(seed).as_dict())

    def test_different_seeds_give_different_hues(self):
        hues = {round(palette.build(seed).hue) for seed in SEEDS}
        self.assertGreater(len(hues), 1)

    def test_every_generated_palette_meets_its_contrast_floor(self):
        for seed in SEEDS + ["a/b", "x/y", "long/repository-name"]:
            for mode in ("dark", "light"):
                colours = palette.build(seed, mode=mode)
                self.assertEqual(colours.check_contrast(), [], "%s/%s" % (seed, mode))

    def test_every_named_accent_meets_its_contrast_floor(self):
        from gspg.color import contrast_ratio

        for name in ACCENT_HUES:
            for mode in ("dark", "light"):
                colours = palette.build("seed", accent=name, mode=mode)
                self.assertGreaterEqual(
                    contrast_ratio(colours.ink, colours.background), MIN_TEXT_CONTRAST, name
                )
                self.assertGreaterEqual(
                    contrast_ratio(colours.ink_muted, colours.background),
                    MIN_MUTED_CONTRAST,
                    name,
                )

    def test_an_accent_can_be_named_a_hex_colour_or_a_hue(self):
        self.assertAlmostEqual(palette.build("s", accent="teal").hue, ACCENT_HUES["teal"])
        self.assertAlmostEqual(palette.build("s", accent="200").hue, 200.0)
        self.assertGreater(palette.build("s", accent="#58a6ff").hue, 200.0)

    def test_an_unknown_accent_lists_the_valid_ones(self):
        with self.assertRaises(ConfigError) as caught:
            palette.build("s", accent="chartreuse")
        self.assertIn("emerald", str(caught.exception))

    def test_an_unknown_mode_is_refused(self):
        with self.assertRaises(ConfigError):
            palette.build("s", mode="sepia")

    def test_zero_saturation_gives_a_neutral_accent(self):
        colours = palette.build("s", saturation=0.0)
        red, green, blue = colours.accent.rgb()
        self.assertLessEqual(max(red, green, blue) - min(red, green, blue), 2)


class Patterns(unittest.TestCase):
    def _draw(self, name, seed="owner/name"):
        document = svg.Document(1280, 640)
        field = patterns.Field(
            document, palette.build(seed), Rng(seed, name), 0, 0, 1280, 640
        )
        document.body.add(patterns.draw(name, field))
        return document.to_string()

    def test_every_pattern_draws_and_is_valid_xml(self):
        for name in patterns.names():
            output = self._draw(name)
            ElementTree.fromstring(output)
            self.assertGreater(len(output), 500, name)

    def test_every_pattern_is_deterministic(self):
        for name in patterns.names():
            self.assertEqual(self._draw(name), self._draw(name), name)

    def test_every_pattern_stays_within_a_sane_size(self):
        for name in patterns.names():
            self.assertLess(len(self._draw(name)), 900 * 1024, name)

    def test_patterns_are_clipped_to_their_field(self):
        for name in patterns.names():
            self.assertIn("clip-path", self._draw(name), name)

    def test_an_unset_pattern_falls_back_to_the_house_default(self):
        from gspg.model import DEFAULT_PATTERN

        self.assertEqual(patterns.resolve(None), DEFAULT_PATTERN)
        self.assertEqual(patterns.resolve(""), DEFAULT_PATTERN)
        self.assertIn(DEFAULT_PATTERN, patterns.names())

    def test_an_explicit_pattern_is_honoured(self):
        self.assertEqual(patterns.resolve("orbits"), "orbits")

    def test_an_unknown_pattern_lists_the_valid_ones(self):
        with self.assertRaises(RenderError) as caught:
            patterns.resolve("sparkles")
        self.assertIn("topography", str(caught.exception))

    def test_a_field_needs_a_positive_size(self):
        with self.assertRaises(RenderError):
            patterns.Field(svg.Document(10, 10), palette.build("s"), Rng("s"), 0, 0, 0, 10)


class Card(unittest.TestCase):
    """One layout, used for every repository."""

    def _compose(self, entry=None, pattern="strata"):
        preview = Preview.from_dict(entry or {
            "repo": "owner/name",
            "description": "A short description of what this repository does.",
        })
        colours = palette.build(preview.seed, preview.accent, "light")
        composition = templates.render_into(
            preview, colours, preview.seed, pattern
        )
        return composition.document.to_string()

    def test_every_pattern_can_carry_the_card(self):
        for pattern in patterns.names():
            ElementTree.fromstring(self._compose(pattern=pattern))

    def test_the_card_is_deterministic(self):
        self.assertEqual(self._compose(), self._compose())

    def test_an_unknown_pattern_is_refused(self):
        with self.assertRaises(RenderError):
            self._compose(pattern="sparkles")

    def test_the_canvas_is_the_size_github_renders(self):
        self.assertEqual((templates.WIDTH, templates.HEIGHT), (1280.0, 640.0))
        self.assertIn('width="1280" height="640"', self._compose())

    def test_the_card_survives_awkward_content(self):
        awkward = [
            {"repo": "o/n"},
            {"repo": "o/a-really-long-repository-name-that-refuses-to-wrap-nicely"},
            {"repo": "o/n", "description": "x " * 300},
            {"repo": "o/n", "title": "\u00dcn\u00efc\u00f6d\u00e9 \u2014 \u201cquoted\u201d",
             "description": "\u00e0 \u00e8 \u00ec \u00f2 \u00f9 100%"},
            {"repo": "o/n", "title": "\u4e2d\u6587\u6807\u9898",
             "description": "unsupported script"},
        ]
        for entry in awkward:
            ElementTree.fromstring(self._compose(entry))

    def test_nothing_on_the_card_is_too_small_to_read_in_a_feed(self):
        """A feed scales the card to roughly a third; 30px is the floor."""
        self.assertGreaterEqual(templates.DESC_SMALLEST, 30.0)
        self.assertGreaterEqual(templates.TITLE_SMALLEST, templates.DESC_LARGEST)

    def test_the_title_is_never_truncated(self):
        """A truncated headline still measures narrower than the column, so the
        solver has to reject the size rather than trust the measurement."""
        for name in ("cloudflare-backup-actions", "a-really-long-repository-name",
                     "supercalifragilisticexpialidocious-and-then-some-more",
                     "tanstack-compromise-checker"):
            output = self._compose({"repo": "owner/" + name,
                                    "description": "A description."})
            self.assertNotIn("\u2026", output, name)

    def test_the_description_is_never_truncated(self):
        for length in (20, 80, 140, 200, 320, 600):
            output = self._compose({
                "repo": "owner/name",
                "description": " ".join(["word"] * (length // 5)),
            })
            self.assertNotIn("\u2026", output, "%d characters were cut" % (length,))

    def test_the_solver_keeps_the_block_inside_the_safe_area(self):
        available = templates.HEIGHT - templates.TOP - templates.BOTTOM
        for description in ("", "short", "medium length description here",
                            " ".join(["word"] * 40)):
            preview = Preview.from_dict(
                {"repo": "owner/repository-name", "description": description}
            )
            composition = templates.Composition(
                preview, palette.build(preview.seed, mode="light"), preview.seed
            )
            fit = templates.solve(
                composition, templates.WIDTH - templates.MARGIN * 2, available
            )
            self.assertLessEqual(fit.height, available + 1e-6, repr(description))

    def test_the_title_claims_space_before_the_description(self):
        """A long description must not crush the headline to its own size."""
        preview = Preview.from_dict({
            "repo": "owner/name",
            "description": " ".join(["word"] * 30),
        })
        composition = templates.Composition(
            preview, palette.build(preview.seed, mode="light"), preview.seed
        )
        fit = templates.solve(
            composition,
            templates.WIDTH - templates.MARGIN * 2,
            templates.HEIGHT - templates.TOP - templates.BOTTOM,
        )
        self.assertGreaterEqual(fit.title_size, fit.desc_size * 2.0)

    def test_the_spine_is_drawn_so_the_card_has_an_edge(self):
        """Without it, a near-white card dissolves into a near-white feed."""
        from gspg.typography import format_number

        output = self._compose()
        self.assertIn(
            'width="%s" height="%s"'
            % (format_number(templates.SPINE), format_number(templates.HEIGHT)),
            output,
        )
