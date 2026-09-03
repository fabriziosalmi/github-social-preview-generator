"""Coverage classification and reporting. No network is used by these tests."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest

from gspg import audit, importer

CUSTOM_PAGE = (
    b'<html><head><meta property="og:image" content='
    b'"https://repository-images.githubusercontent.com/123/abc">'
    b"</head></html>"
)
DEFAULT_PAGE = (
    b'<html><head><meta property="og:image" content='
    b'"https://opengraph.githubassets.com/deadbeef/owner/name">'
    b"</head></html>"
)


class Classification(unittest.TestCase):
    def test_an_uploaded_image_is_recognised(self):
        result = audit.classify(CUSTOM_PAGE)
        self.assertEqual(result.status, audit.STATUS_CUSTOM)
        self.assertTrue(result.uploaded)
        self.assertEqual(result.action, "nothing")

    def test_githubs_generated_card_is_recognised(self):
        result = audit.classify(DEFAULT_PAGE)
        self.assertEqual(result.status, audit.STATUS_DEFAULT)
        self.assertFalse(result.uploaded)

    def test_the_name_attribute_spelling_is_accepted(self):
        page = DEFAULT_PAGE.replace(b"property=", b"name=")
        self.assertEqual(audit.classify(page).status, audit.STATUS_DEFAULT)

    def test_a_page_without_the_tag_is_an_error_not_a_pass(self):
        result = audit.classify(b"<html><head></head></html>")
        self.assertEqual(result.status, audit.STATUS_ERROR)
        self.assertIn("no og:image", result.detail)

    def test_an_unrecognised_host_is_not_treated_as_custom(self):
        page = b'<meta property="og:image" content="https://example.com/x.png">'
        result = audit.classify(page)
        self.assertEqual(result.status, audit.STATUS_DEFAULT)
        self.assertIn("unrecognised", result.detail)

    def test_the_next_step_depends_on_whether_a_png_exists(self):
        result = audit.classify(DEFAULT_PAGE)
        self.assertEqual(result.action, "generate, then upload")
        result.generated = True
        self.assertEqual(result.action, "upload")


class Reporting(unittest.TestCase):
    def setUp(self):
        def make(repo, status, generated=False):
            return audit.Coverage(repo, status, generated=generated)

        self.results = [
            make("o/a", audit.STATUS_CUSTOM),
            make("o/b", audit.STATUS_DEFAULT, generated=True),
            make("o/c", audit.STATUS_DEFAULT),
            make("o/d", audit.STATUS_MISSING),
        ]

    def test_the_summary_counts_every_status(self):
        counts = audit.summarise(self.results)
        self.assertEqual(counts[audit.STATUS_CUSTOM], 1)
        self.assertEqual(counts[audit.STATUS_DEFAULT], 2)
        self.assertEqual(counts[audit.STATUS_MISSING], 1)

    def test_the_markdown_report_leads_with_what_needs_doing(self):
        report = audit.to_markdown(self.results)
        self.assertIn("1 of 4 repositories", report)
        # Rows that still need work come first; already-covered ones last.
        self.assertLess(report.index("[o/b]"), report.index("[o/a]"))
        self.assertLess(report.index("[o/c]"), report.index("[o/a]"))
        self.assertIn("no authentication", report.lower())

    def test_the_json_report_is_valid_and_complete(self):
        document = json.loads(audit.to_json(self.results))
        self.assertEqual(document["total"], 4)
        self.assertEqual(len(document["repositories"]), 4)
        self.assertIn("action", document["repositories"][0])


class GeneratedLookup(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.directory, "png"))
        open(os.path.join(self.directory, "png", "o__a.png"), "wb").close()

    def tearDown(self):
        shutil.rmtree(self.directory, ignore_errors=True)

    def test_only_repositories_with_a_png_are_reported(self):
        found = audit.generated_repos(self.directory, ["o/a", "o/b"])
        self.assertEqual(found, ["o/a"])


class Importing(unittest.TestCase):
    def test_new_repositories_are_appended_in_order(self):
        merged = importer.merge(
            [{"repo": "o/a"}], [{"repo": "o/a"}, {"repo": "o/b"}]
        )
        self.assertEqual([entry["repo"] for entry in merged], ["o/a", "o/b"])

    def test_design_choices_survive_a_reimport(self):
        existing = [{"repo": "o/a", "accent": "teal", "template": "console", "seed": "x"}]
        merged = importer.merge(existing, [{"repo": "o/a", "language": "Go"}])
        self.assertEqual(merged[0]["accent"], "teal")
        self.assertEqual(merged[0]["template"], "console")
        self.assertEqual(merged[0]["seed"], "x")
        self.assertEqual(merged[0]["language"], "Go")

    def test_a_hand_written_description_is_not_overwritten(self):
        existing = [{"repo": "o/a", "description": "mine"}]
        merged = importer.merge(existing, [{"repo": "o/a", "description": "theirs"}])
        self.assertEqual(merged[0]["description"], "mine")

    def test_an_empty_description_is_refreshed(self):
        existing = [{"repo": "o/a"}]
        merged = importer.merge(existing, [{"repo": "o/a", "description": "theirs"}])
        self.assertEqual(merged[0]["description"], "theirs")

    def test_repositories_missing_upstream_are_kept(self):
        merged = importer.merge([{"repo": "o/gone"}], [])
        self.assertEqual([entry["repo"] for entry in merged], ["o/gone"])

    def test_the_summary_counts_additions(self):
        counts = importer.summarise([{"repo": "o/a"}], [{"repo": "o/a"}, {"repo": "o/b"}])
        self.assertEqual(counts, {"added": 1, "kept": 1, "total": 2})
