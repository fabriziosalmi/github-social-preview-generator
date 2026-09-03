"""Colour conversion, gamut mapping and contrast."""

from __future__ import annotations

import unittest

from gspg.color import Oklch, contrast_ratio, mix, srgb_to_oklch


class HexRoundTrip(unittest.TestCase):
    def test_known_colours_survive_a_round_trip(self):
        for value in ("#000000", "#ffffff", "#58a6ff", "#0d1117", "#f85149", "#3fb950"):
            self.assertEqual(srgb_to_oklch(value).hex(), value)

    def test_short_form_expands(self):
        self.assertEqual(srgb_to_oklch("#abc").hex(), "#aabbcc")

    def test_alpha_is_parsed(self):
        self.assertAlmostEqual(srgb_to_oklch("#00000080").alpha, 128 / 255.0, places=4)

    def test_bad_input_is_rejected(self):
        for value in ("", "#12", "#gggggg", "not a colour", "#1234567"):
            with self.assertRaises(ValueError):
                srgb_to_oklch(value)


class GamutMapping(unittest.TestCase):
    def test_out_of_gamut_chroma_is_reduced_not_clipped(self):
        # A chroma this high is outside sRGB at every hue.
        for hue in range(0, 360, 15):
            colour = Oklch(0.72, 0.45, hue)
            red, green, blue = colour.rgb()
            for channel in (red, green, blue):
                self.assertGreaterEqual(channel, 0)
                self.assertLessEqual(channel, 255)

    def test_hue_is_preserved_while_mapping(self):
        original = Oklch(0.72, 0.45, 150.0)
        mapped = srgb_to_oklch(original.hex())
        difference = abs((mapped.hue - 150.0 + 180.0) % 360.0 - 180.0)
        self.assertLess(difference, 6.0)

    def test_extremes_are_black_and_white(self):
        self.assertEqual(Oklch(0.0, 0.0, 0.0).hex(), "#000000")
        self.assertEqual(Oklch(1.0, 0.0, 0.0).hex(), "#ffffff")


class Serialisation(unittest.TestCase):
    def test_opaque_colours_serialise_as_hex(self):
        self.assertEqual(Oklch(0.5, 0.1, 250.0).css(), Oklch(0.5, 0.1, 250.0).hex())

    def test_translucent_colours_serialise_as_rgba(self):
        self.assertTrue(Oklch(0.5, 0.1, 250.0, 0.25).css().startswith("rgba("))

    def test_alpha_is_clamped(self):
        self.assertEqual(Oklch(0.5, 0.1, 250.0, 4.0).alpha, 1.0)
        self.assertEqual(Oklch(0.5, 0.1, 250.0, -1.0).alpha, 0.0)


class Contrast(unittest.TestCase):
    def test_black_on_white_is_the_maximum(self):
        ratio = contrast_ratio(Oklch(0.0, 0.0, 0.0), Oklch(1.0, 0.0, 0.0))
        self.assertAlmostEqual(ratio, 21.0, places=1)

    def test_identical_colours_have_no_contrast(self):
        colour = Oklch(0.5, 0.1, 200.0)
        self.assertAlmostEqual(contrast_ratio(colour, colour), 1.0, places=6)

    def test_order_does_not_matter(self):
        first, second = Oklch(0.2, 0.05, 30.0), Oklch(0.9, 0.02, 30.0)
        self.assertAlmostEqual(
            contrast_ratio(first, second), contrast_ratio(second, first), places=9
        )


class Interpolation(unittest.TestCase):
    def test_the_ends_are_the_inputs(self):
        first, second = Oklch(0.2, 0.1, 10.0), Oklch(0.8, 0.2, 200.0)
        self.assertEqual(mix(first, second, 0.0).hex(), first.hex())
        self.assertEqual(mix(first, second, 1.0).hex(), second.hex())

    def test_hue_takes_the_short_way_round(self):
        midpoint = mix(Oklch(0.5, 0.1, 350.0), Oklch(0.5, 0.1, 10.0), 0.5)
        self.assertAlmostEqual(midpoint.hue, 0.0, places=6)
