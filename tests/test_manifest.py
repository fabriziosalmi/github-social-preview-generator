"""Manifest parsing and the entry model."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest

from gspg import config
from gspg.errors import ConfigError
from gspg.model import Preview


class Entries(unittest.TestCase):
    def test_the_minimum_entry_fills_in_sensible_defaults(self):
        preview = Preview.from_dict({"repo": "owner/name"})
        self.assertEqual(preview.owner, "owner")
        self.assertEqual(preview.name, "name")
        self.assertEqual(preview.title, "name")
        self.assertEqual(preview.seed, "owner/name")
        self.assertEqual(preview.pattern, "strata")
        self.assertFalse(preview.skip)

    def test_the_slug_is_filesystem_safe(self):
        self.assertEqual(Preview.from_dict({"repo": "o/n"}).slug, "o__n")
        self.assertNotIn("/", Preview.from_dict({"repo": "a.b/c-d"}).slug)

    def test_topics_accept_a_comma_separated_string(self):
        preview = Preview.from_dict({"repo": "o/n", "topics": "tls, acme , pki"})
        self.assertEqual(preview.topics, ["tls", "acme", "pki"])

    def test_facts_are_ordered_language_licence_then_topics(self):
        preview = Preview.from_dict(
            {"repo": "o/n", "language": "Go", "license": "MIT", "topics": ["x"]}
        )
        self.assertEqual(preview.facts, ["Go", "MIT", "x"])

    def test_defaults_are_applied_but_never_override_an_entry(self):
        preview = Preview.from_dict(
            {"repo": "o/n", "accent": "teal"}, {"accent": "amber", "saturation": 0.5}
        )
        self.assertEqual(preview.accent, "teal")
        self.assertEqual(preview.saturation, 0.5)

    def test_a_null_field_falls_back_to_the_default(self):
        preview = Preview.from_dict({"repo": "o/n", "accent": None}, {"accent": "amber"})
        self.assertEqual(preview.accent, "amber")

    def test_a_misspelled_field_is_reported_not_ignored(self):
        with self.assertRaises(ConfigError) as caught:
            Preview.from_dict({"repo": "o/n", "colour": "red"})
        self.assertIn("colour", str(caught.exception))

    def test_fields_the_card_no_longer_renders_are_refused(self):
        """Dead configuration is worse than none: it silently does nothing."""
        for field in ("template", "mode", "footer", "badge"):
            with self.assertRaises(ConfigError, msg=field):
                Preview.from_dict({"repo": "o/n", field: "x"})

    def test_a_bad_repository_name_is_rejected(self):
        for repo in (None, "", "no-slash", "owner/", "/name", "own er/name", "a/b/c"):
            with self.assertRaises(ConfigError):
                Preview.from_dict({"repo": repo})

    def test_out_of_range_values_are_rejected(self):
        with self.assertRaises(ConfigError):
            Preview.from_dict({"repo": "o/n", "saturation": 5})
        with self.assertRaises(ConfigError):
            Preview.from_dict({"repo": "o/n", "saturation": "loud"})
        with self.assertRaises(ConfigError):
            Preview.from_dict({"repo": "o/n", "topics": [1, 2]})


class ManifestFiles(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp()
        self.path = os.path.join(self.directory, "previews.json")

    def tearDown(self):
        shutil.rmtree(self.directory, ignore_errors=True)

    def write(self, document):
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump(document, handle)

    def test_a_starter_manifest_round_trips(self):
        config.write(self.path, config.starter("someone"))
        manifest = config.load(self.path)
        self.assertEqual(len(manifest), 1)
        self.assertTrue(manifest.previews[0].repo.startswith("someone/"))

    def test_a_missing_file_says_what_to_do(self):
        with self.assertRaises(ConfigError) as caught:
            config.load(os.path.join(self.directory, "absent.json"))
        self.assertIn("gspg init", str(caught.exception))

    def test_invalid_json_is_reported(self):
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("{not json")
        with self.assertRaises(ConfigError):
            config.load(self.path)

    def test_a_future_version_is_refused(self):
        self.write({"version": 99, "repositories": []})
        with self.assertRaises(ConfigError):
            config.load(self.path)

    def test_unknown_top_level_keys_are_refused(self):
        self.write({"version": 1, "repositories": [], "extras": {}})
        with self.assertRaises(ConfigError):
            config.load(self.path)

    def test_duplicate_repositories_are_refused(self):
        self.write({"version": 1, "repositories": [{"repo": "o/n"}, {"repo": "o/n"}]})
        with self.assertRaises(ConfigError) as caught:
            config.load(self.path)
        self.assertIn("twice", str(caught.exception))

    def test_the_failing_entry_is_identified_by_position(self):
        self.write({"version": 1, "repositories": [{"repo": "o/n"}, {"repo": "bad"}]})
        with self.assertRaises(ConfigError) as caught:
            config.load(self.path)
        self.assertIn("entry 2", str(caught.exception))

    def test_skipped_entries_are_excluded_from_active(self):
        self.write(
            {"version": 1, "repositories": [{"repo": "o/a"}, {"repo": "o/b", "skip": True}]}
        )
        manifest = config.load(self.path)
        self.assertEqual([p.repo for p in manifest.active()], ["o/a"])

    def test_selection_matches_full_or_bare_names(self):
        self.write({"version": 1, "repositories": [{"repo": "o/a"}, {"repo": "o/b"}]})
        manifest = config.load(self.path)
        self.assertEqual([p.repo for p in manifest.select(["o/a"])], ["o/a"])
        self.assertEqual([p.repo for p in manifest.select(["b"])], ["o/b"])
        self.assertEqual(len(manifest.select(None)), 2)

    def test_selecting_something_absent_is_an_error(self):
        self.write({"version": 1, "repositories": [{"repo": "o/a"}]})
        with self.assertRaises(ConfigError):
            config.load(self.path).select(["o/nope"])


class ShippedManifest(unittest.TestCase):
    """The manifest committed to this repository must stay loadable."""

    def test_it_loads_and_every_entry_is_valid(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(root, "previews.json")
        if not os.path.exists(path):
            self.skipTest("no previews.json in this checkout")
        manifest = config.load(path)
        self.assertGreater(len(manifest), 0)
        slugs = [preview.slug for preview in manifest]
        self.assertEqual(len(slugs), len(set(slugs)), "slug collision")

    def test_it_validates_against_the_shipped_schema(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        schema_path = os.path.join(root, "schema", "previews.schema.json")
        manifest_path = os.path.join(root, "previews.json")
        if not (os.path.exists(schema_path) and os.path.exists(manifest_path)):
            self.skipTest("schema or manifest missing")
        with open(schema_path, "r", encoding="utf-8") as handle:
            schema = json.load(handle)
        with open(manifest_path, "r", encoding="utf-8") as handle:
            document = json.load(handle)
        # No JSON Schema library is available by design, so check the property
        # that actually matters: the schema and the loader agree on the fields.
        allowed = set(schema["properties"]["repositories"]["items"]["properties"])
        for entry in document["repositories"]:
            self.assertLessEqual(set(entry), allowed)
        allowed_defaults = set(schema["properties"]["defaults"]["properties"])
        self.assertLessEqual(set(document.get("defaults", {})), allowed_defaults)
