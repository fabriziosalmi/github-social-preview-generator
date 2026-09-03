"""The published gallery: self-contained, same-origin, no third parties."""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import unittest

from gspg import config, gallery
from gspg.errors import RenderError

MANIFEST = {
    "version": 1,
    "repositories": [
        {"repo": "owner/alpha", "description": "First & <best>", "language": "Go"},
        {"repo": "owner/beta"},
    ],
}
LOCK = {"owner/alpha": {"pattern": "strata", "accent": "#abcdef"}}


class SiteAssembly(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.output = os.path.join(self.root, "previews")
        self.site = os.path.join(self.root, "site")
        os.makedirs(os.path.join(self.output, "png"))
        manifest_path = os.path.join(self.root, "previews.json")
        config.write(manifest_path, MANIFEST)
        self.manifest = config.load(manifest_path)
        for preview in self.manifest:
            with open(os.path.join(self.output, "png", preview.slug + ".png"), "wb") as fh:
                fh.write(b"\x89PNG\r\n\x1a\n")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def build(self):
        return gallery.build(self.manifest, LOCK, self.output, self.site)

    def test_the_site_contains_everything_it_needs(self):
        self.build()
        for name in ("index.html", "styles.css", "index.json", ".nojekyll"):
            self.assertTrue(os.path.exists(os.path.join(self.site, name)), name)
        images = os.listdir(os.path.join(self.site, "previews"))
        self.assertEqual(sorted(images), ["owner__alpha.png", "owner__beta.png"])

    def test_the_page_loads_nothing_from_off_origin(self):
        """Anchors may point anywhere; subresources may not."""
        self.build()
        with open(os.path.join(self.site, "index.html"), encoding="utf-8") as handle:
            page = handle.read()
        subresources = re.findall(r'src="([^"]+)"', page)
        subresources += re.findall(r'<link[^>]+href="([^"]+)"', page)
        self.assertTrue(subresources)
        for source in subresources:
            self.assertFalse(
                source.startswith(("http://", "https://", "//", "data:")),
                "off-origin subresource: %s" % (source,),
            )

    def test_the_page_carries_a_restrictive_policy(self):
        self.build()
        with open(os.path.join(self.site, "index.html"), encoding="utf-8") as handle:
            page = handle.read()
        self.assertIn("Content-Security-Policy", page)
        self.assertIn("default-src 'none'", page)
        self.assertIn("img-src 'self'", page)
        self.assertNotIn("unsafe-inline", page)
        self.assertIn('name="referrer" content="no-referrer"', page)

    def test_no_scripts_and_no_inline_styles(self):
        self.build()
        with open(os.path.join(self.site, "index.html"), encoding="utf-8") as handle:
            page = handle.read()
        self.assertNotIn("<script", page)
        self.assertNotIn("<style", page)
        self.assertNotIn("onload=", page)

    def test_content_is_escaped(self):
        self.build()
        with open(os.path.join(self.site, "index.html"), encoding="utf-8") as handle:
            page = handle.read()
        self.assertIn("First &amp; &lt;best&gt;", page)

    def test_the_machine_readable_index_lists_every_preview(self):
        self.build()
        with open(os.path.join(self.site, "index.json"), encoding="utf-8") as handle:
            document = json.load(handle)
        self.assertEqual(document["count"], 2)
        self.assertEqual(document["previews"][0]["pattern"], "strata")

    def test_a_raw_base_is_offered_when_given(self):
        written, _ = gallery.build(
            self.manifest, LOCK, self.output, self.site, raw_base="https://example.test/p/"
        )
        self.assertTrue(written)
        with open(os.path.join(self.site, "index.json"), encoding="utf-8") as handle:
            document = json.load(handle)
        self.assertEqual(
            document["previews"][0]["raw_url"], "https://example.test/p/owner__alpha.png"
        )

    def test_a_missing_png_is_a_warning_not_a_crash(self):
        os.remove(os.path.join(self.output, "png", "owner__beta.png"))
        _written, warnings = self.build()
        self.assertEqual(len(warnings), 1)
        self.assertIn("owner/beta", warnings[0])

    def test_stale_images_are_removed_on_rebuild(self):
        self.build()
        stale = os.path.join(self.site, "previews", "owner__gone.png")
        open(stale, "wb").close()
        self.build()
        self.assertFalse(os.path.exists(stale))

    def test_nothing_rendered_is_an_error_worth_reporting(self):
        for preview in self.manifest:
            os.remove(os.path.join(self.output, "png", preview.slug + ".png"))
        with self.assertRaises(RenderError) as caught:
            self.build()
        self.assertIn("gspg build", str(caught.exception))
