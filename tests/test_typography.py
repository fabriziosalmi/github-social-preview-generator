"""Glyph packs, measurement, kerning and line breaking."""

from __future__ import annotations

import os
import unittest

from gspg.errors import AssetError
from gspg.typography import FALLBACK_CHAR, Face, format_number

#: The two faces the card draws with, and the only two that ship.
DISPLAY = "InterDisplay-SemiBold"
BODY = "Inter-Regular"
SHIPPED = (DISPLAY, BODY)

FONT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "src", "gspg", "assets", "fonts",
)


class NumberFormatting(unittest.TestCase):
    def test_exact_values_keep_their_precision(self):
        # 64/2048: rounding this to four decimals shears a headline visibly.
        self.assertEqual(format_number(0.03125), "0.03125")
        self.assertEqual(format_number(12 / 2048.0), "0.005859375")

    def test_integers_lose_the_decimal_point(self):
        self.assertEqual(format_number(100.0), "100")
        self.assertEqual(format_number(-7), "-7")

    def test_negative_zero_is_normalised(self):
        self.assertEqual(format_number(-0.0), "0")

    def test_no_exponent_notation_reaches_the_output(self):
        self.assertNotIn("e", format_number(1e-9))

    def test_non_finite_values_are_refused(self):
        for value in (float("inf"), float("-inf"), float("nan")):
            with self.assertRaises(ValueError):
                format_number(value)


class Loading(unittest.TestCase):
    def test_every_shipped_face_loads(self):
        for name in SHIPPED:
            face = Face.load(name)
            self.assertGreater(face.glyph_count, 200)
            self.assertGreater(face.units_per_em, 0)

    def test_only_the_faces_the_card_uses_are_shipped(self):
        """A pack the renderer never opens is dead weight in the repository."""
        packs = sorted(
            name[: -len(".glyphs.json")]
            for name in os.listdir(FONT_DIR)
            if name.endswith(".glyphs.json")
        )
        self.assertEqual(packs, sorted(SHIPPED))

    def test_faces_are_cached(self):
        self.assertIs(Face.load(DISPLAY), Face.load(DISPLAY))

    def test_a_missing_pack_is_reported_clearly(self):
        with self.assertRaises(AssetError) as caught:
            Face.load("NoSuchFace-Regular")
        self.assertIn("glyph pack not found", str(caught.exception))

    def test_kerning_survived_the_glyph_pack_build(self):
        for name in SHIPPED:
            self.assertGreater(Face.load(name).kern_pair_count, 1000, name)


class Metrics(unittest.TestCase):
    def setUp(self):
        self.face = Face.load(DISPLAY)

    def test_measurement_scales_linearly_with_size(self):
        small = self.face.measure("Hamburgefonstiv", 20.0)
        large = self.face.measure("Hamburgefonstiv", 80.0)
        self.assertAlmostEqual(large / small, 4.0, places=6)

    def test_the_empty_string_has_no_width(self):
        self.assertEqual(self.face.measure("", 40.0), 0.0)

    def test_kerning_pulls_a_known_pair_together(self):
        self.assertLess(self.face.kern_units("A", "V"), 0)
        self.assertEqual(self.face.kern_units("H", "H"), 0)

    def test_kerning_makes_a_pair_narrower_than_its_parts(self):
        kerned = self.face.measure("AV", 100.0)
        unkerned = self.face.measure("A", 100.0) + self.face.measure("V", 100.0)
        self.assertLess(kerned, unkerned)

    def test_tracking_widens_only_between_glyphs(self):
        plain = self.face.measure("AAAA", 50.0)
        tracked = self.face.measure("AAAA", 50.0, tracking=0.1)
        # Tracking is rounded to whole font units so paths stay integral; at
        # 2048 units per em that is a rounding error well under a hundredth
        # of a pixel per gap.
        gap_error = 3 * 0.5 * self.face.scale(50.0)
        self.assertAlmostEqual(tracked - plain, 3 * 0.1 * 50.0, delta=gap_error)
        self.assertEqual(
            self.face.measure("A", 50.0), self.face.measure("A", 50.0, tracking=0.5)
        )

    def test_the_faces_are_proportional(self):
        widths = {self.face.advance_units(char) for char in "iIWm."}
        self.assertGreater(len(widths), 3)


class Outlines(unittest.TestCase):
    def setUp(self):
        self.face = Face.load(DISPLAY)

    def test_a_run_produces_a_path_and_a_transform(self):
        run = self.face.run("Hello", 48.0, 10.0, 20.0)
        self.assertTrue(run.d.startswith("M"))
        self.assertIn("translate(10 20)", run.transform)
        self.assertIn("scale(", run.transform)

    def test_a_space_only_run_draws_nothing_but_has_width(self):
        run = self.face.run("   ", 48.0)
        self.assertEqual(run.d, "")
        self.assertGreater(run.width, 0.0)
        self.assertFalse(run)

    def test_anchors_shift_the_origin_by_the_run_width(self):
        start = self.face.run("Anchor", 40.0, 100.0, 0.0, anchor="start")
        middle = self.face.run("Anchor", 40.0, 100.0, 0.0, anchor="middle")
        end = self.face.run("Anchor", 40.0, 100.0, 0.0, anchor="end")
        self.assertEqual(start.d, middle.d)
        self.assertIn("translate(100 ", start.transform)
        self.assertIn(format_number(100.0 - start.width / 2.0), middle.transform)
        self.assertIn(format_number(100.0 - start.width), end.transform)

    def test_an_invalid_anchor_is_refused(self):
        with self.assertRaises(ValueError):
            self.face.run("x", 10.0, anchor="sideways")

    def test_unknown_characters_fall_back_rather_than_crash(self):
        run = self.face.run("中文", 40.0)
        self.assertTrue(run.d)
        self.assertEqual(
            run.width, self.face.measure(FALLBACK_CHAR * 2, 40.0)
        )

    def test_accented_latin_is_covered(self):
        for char in "àèéìòùÀÈÉabcXYZ0123":
            self.assertTrue(self.face.supports(char), char)


class LineBreaking(unittest.TestCase):
    def setUp(self):
        self.face = Face.load(BODY)

    def test_short_text_stays_on_one_line(self):
        self.assertEqual(self.face.wrap("short", 20.0, 500.0), ["short"])

    def test_empty_text_produces_no_lines(self):
        self.assertEqual(self.face.wrap("   ", 20.0, 500.0), [])

    def test_every_wrapped_line_fits(self):
        text = "Automated certificate management with ACME and DNS challenges"
        for line in self.face.wrap(text, 24.0, 320.0, max_lines=6):
            self.assertLessEqual(self.face.measure(line, 24.0), 320.0)

    def test_wrapping_preserves_the_words(self):
        text = "one two three four five six seven eight nine ten"
        lines = self.face.wrap(text, 20.0, 160.0, max_lines=10)
        self.assertEqual(" ".join(lines).split(), text.split())

    def test_overflow_is_ellipsised(self):
        text = " ".join(["word"] * 60)
        lines = self.face.wrap(text, 20.0, 200.0, max_lines=2)
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[-1].endswith("…"))

    def test_a_hyphenated_name_breaks_at_its_hyphens(self):
        lines = self.face.wrap("cloudflare-backup-actions", 40.0, 220.0, max_lines=3)
        self.assertGreater(len(lines), 1)
        for line in lines:
            self.assertLessEqual(self.face.measure(line, 40.0), 220.0)
        self.assertEqual("".join(lines), "cloudflare-backup-actions")


class Fitting(unittest.TestCase):
    def setUp(self):
        self.face = Face.load("InterDisplay-SemiBold")

    def test_short_titles_get_the_largest_size(self):
        size, lines = self.face.fit_size("api", 600.0, 2, largest=90.0, smallest=40.0)
        self.assertEqual(size, 90.0)
        self.assertEqual(lines, ["api"])

    def test_every_fitted_line_stays_inside_the_column(self):
        titles = [
            "certmate",
            "cloudflare-backup-actions",
            "tanstack-compromise-checker",
            "You-Know-What-AI-Mean",
            "a-truly-preposterous-repository-name-that-goes-on-and-on",
            "supercalifragilisticexpialidocious",
        ]
        for title in titles:
            for column in (300.0, 480.0, 605.0):
                size, lines = self.face.fit_size(
                    title, column, 3, largest=94.0, smallest=40.0, tracking=-0.02
                )
                self.assertTrue(lines, title)
                for line in lines:
                    self.assertLessEqual(
                        self.face.measure(line, size, -0.02), column + 1e-6,
                        "%r overflowed at %.0f" % (title, column),
                    )

    def test_an_inverted_range_is_refused(self):
        with self.assertRaises(ValueError):
            self.face.fit_size("x", 100.0, 1, largest=10.0, smallest=40.0)
