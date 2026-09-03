"""SVG serialisation: escaping, structure and the constraints we rely on."""

from __future__ import annotations

import re
import unittest
import xml.etree.ElementTree as ElementTree

from gspg import svg
from gspg.typography import Face


class Escaping(unittest.TestCase):
    def test_markup_characters_are_escaped(self):
        self.assertEqual(svg.escape('<a & "b">'), "&lt;a &amp; &quot;b&quot;&gt;")

    def test_ampersands_are_escaped_before_anything_else(self):
        self.assertEqual(svg.escape("&lt;"), "&amp;lt;")

    def test_attributes_carrying_markup_stay_inert(self):
        element = svg.rect(0, 0, 1, 1, ("data-label", '"><script>x</script>'))
        rendered = "\n".join(element.render())
        self.assertNotIn("<script>", rendered)
        ElementTree.fromstring(rendered)

    def test_booleans_are_refused_as_attribute_values(self):
        with self.assertRaises(TypeError):
            "\n".join(svg.rect(0, 0, 1, 1, ("visible", True)).render())


class Structure(unittest.TestCase):
    def test_a_document_parses_as_xml(self):
        document = svg.Document(1280, 640)
        document.body.add(svg.rect(0, 0, 10, 10, ("fill", "#fff")))
        root = ElementTree.fromstring(document.to_string())
        self.assertTrue(root.tag.endswith("svg"))

    def test_the_viewbox_matches_the_declared_size(self):
        output = svg.Document(1280, 640).to_string()
        self.assertIn('viewBox="0 0 1280 640"', output)

    def test_none_valued_attributes_are_dropped(self):
        rendered = "\n".join(svg.rect(0, 0, 1, 1, ("stroke", None)).render())
        self.assertNotIn("stroke", rendered)

    def test_ids_are_unique_and_stable(self):
        document = svg.Document(10, 10)
        self.assertEqual(
            [document.unique_id("x") for _ in range(3)], ["x-1", "x-2", "x-3"]
        )

    def test_defs_entries_need_an_id(self):
        document = svg.Document(10, 10)
        with self.assertRaises(ValueError):
            document.define(svg.Element("linearGradient"))

    def test_rgba_stops_are_split_into_colour_and_opacity(self):
        gradient = svg.linear_gradient("g", [(0.0, "rgba(1,2,3,0.5)")])
        rendered = "\n".join(gradient.render())
        self.assertIn('stop-color="rgb(1,2,3)"', rendered)
        self.assertIn('stop-opacity="0.5"', rendered)

    def test_a_polyline_needs_points(self):
        with self.assertRaises(ValueError):
            svg.polyline([])

    def test_generator_comments_cannot_break_out(self):
        # XML comments may not contain "--" at all, so a generator string that
        # tries to close one early must be neutralised, not passed through.
        output = svg.Document(1, 1, generator='a --> <svg onload="x"> b').to_string()
        self.assertEqual(output.count("-->"), 1)
        self.assertNotIn("onload", output.split("-->", 1)[1])
        ElementTree.fromstring(output.split("?>", 1)[1])


class PortabilityConstraints(unittest.TestCase):
    """The output must avoid anything rasterisers disagree about."""

    def _sample_document(self) -> str:
        from gspg import palette, templates
        from gspg.model import Preview

        preview = Preview.from_dict(
            {"repo": "owner/name", "description": "A description"}
        )
        composition = templates.render_into(
            preview, palette.build("owner/name", mode="light"), "s", "strata"
        )
        return composition.document.to_string()

    def test_no_filters_are_emitted(self):
        output = self._sample_document()
        for forbidden in ("<filter", "feTurbulence", "feGaussianBlur", "filter="):
            self.assertNotIn(forbidden, output, forbidden)

    def test_no_text_elements_are_emitted(self):
        output = self._sample_document()
        self.assertNotIn("<text", output)
        self.assertNotIn("font-family", output)

    def test_nothing_is_fetched_from_outside(self):
        output = self._sample_document()
        self.assertNotIn("http://", output.replace("http://www.w3.org/2000/svg", ""))
        self.assertNotIn("https://", output)
        self.assertNotIn("<image", output)

    def test_no_scripting(self):
        self.assertNotIn("<script", self._sample_document())

    def test_coordinates_never_use_exponent_notation(self):
        output = self._sample_document()
        self.assertIsNone(re.search(r'="[-\d.]+e[-+]\d', output))


class TextElements(unittest.TestCase):
    def test_a_run_becomes_a_single_path(self):
        run = Face.load("Inter-Regular").run("Ok", 20.0)
        element = svg.text(run, ("fill", "#fff"))
        self.assertIsNotNone(element)
        self.assertEqual(element.tag, "path")

    def test_an_empty_run_produces_nothing(self):
        run = Face.load("Inter-Regular").run(" ", 20.0)
        self.assertIsNone(svg.text(run))
