"""The render pipeline, the lock file, and the offline guarantee."""

from __future__ import annotations

import json
import os
import shutil
import socket
import tempfile
import unittest

from gspg import raster, render
from gspg.errors import RenderError
from gspg.model import Preview

ENTRY = {
    "repo": "owner/name",
    "description": "A deterministic description.",
    "language": "Python",
    "license": "MIT",
    "topics": ["one"],
}


def has_rasteriser() -> bool:
    try:
        raster.select()
        return True
    except RenderError:
        return False


class NoNetworkDuringRender(unittest.TestCase):
    """Rendering must not touch the network. This asserts it, rather than claiming it."""

    def setUp(self):
        self.directory = tempfile.mkdtemp()
        self._socket = socket.socket
        self._create_connection = socket.create_connection
        self._getaddrinfo = socket.getaddrinfo

        def refuse(*args, **kwargs):
            raise AssertionError("the renderer attempted a network connection")

        socket.socket = refuse
        socket.create_connection = refuse
        socket.getaddrinfo = refuse

    def tearDown(self):
        socket.socket = self._socket
        socket.create_connection = self._create_connection
        socket.getaddrinfo = self._getaddrinfo
        shutil.rmtree(self.directory, ignore_errors=True)

    def test_composing_a_preview_opens_no_socket(self):
        document, _composition, pattern = render.compose(Preview.from_dict(ENTRY))
        self.assertTrue(document.startswith("<?xml"))
        self.assertTrue(pattern)

    def test_writing_the_svg_opens_no_socket(self):
        result = render.render(Preview.from_dict(ENTRY), self.directory, png=False)
        self.assertTrue(os.path.exists(result.svg_path))


class Reproducibility(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.directory, ignore_errors=True)

    def test_the_same_entry_produces_the_same_svg(self):
        first = render.render(Preview.from_dict(ENTRY), self.directory, png=False, force=True)
        second = render.render(Preview.from_dict(ENTRY), self.directory, png=False, force=True)
        self.assertEqual(first.svg_digest, second.svg_digest)

    def test_a_changed_field_changes_the_digest(self):
        base = render.render(Preview.from_dict(ENTRY), self.directory, png=False, force=True)
        changed = dict(ENTRY, description="Something else entirely.")
        other = render.render(
            Preview.from_dict(changed), self.directory, png=False, force=True
        )
        self.assertNotEqual(base.input_digest, other.input_digest)
        self.assertNotEqual(base.svg_digest, other.svg_digest)

    def test_the_render_epoch_participates_in_the_digest(self):
        preview = Preview.from_dict(ENTRY)
        before = render.input_digest(preview, 1280, 640)
        original = render.RENDER_EPOCH
        try:
            render.RENDER_EPOCH = original + 1
            self.assertNotEqual(render.input_digest(preview, 1280, 640), before)
        finally:
            render.RENDER_EPOCH = original

    @unittest.skipUnless(has_rasteriser(), "no SVG rasteriser installed")
    def test_the_png_is_byte_identical_across_runs(self):
        first = render.render(Preview.from_dict(ENTRY), self.directory, force=True)
        second = render.render(Preview.from_dict(ENTRY), self.directory, force=True)
        self.assertEqual(first.png_digest, second.png_digest)

    @unittest.skipUnless(has_rasteriser(), "no SVG rasteriser installed")
    def test_the_png_fits_githubs_upload_limit(self):
        result = render.render(Preview.from_dict(ENTRY), self.directory, force=True)
        self.assertLessEqual(
            os.path.getsize(result.png_path), raster.MAX_UPLOAD_BYTES,
            "a social preview over 1 MiB cannot be uploaded to GitHub",
        )


class Caching(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.directory, ignore_errors=True)

    def test_an_unchanged_entry_is_skipped_on_the_next_run(self):
        preview = Preview.from_dict(ENTRY)
        first = render.render(preview, self.directory, png=False)
        self.assertFalse(first.skipped)
        lock = {preview.repo: first.lock_entry()}
        second = render.render(preview, self.directory, png=False, lock=lock)
        self.assertTrue(second.skipped)

    def test_force_defeats_the_cache(self):
        preview = Preview.from_dict(ENTRY)
        first = render.render(preview, self.directory, png=False)
        lock = {preview.repo: first.lock_entry()}
        again = render.render(preview, self.directory, png=False, lock=lock, force=True)
        self.assertFalse(again.skipped)

    def test_a_deleted_output_defeats_the_cache(self):
        preview = Preview.from_dict(ENTRY)
        first = render.render(preview, self.directory, png=False)
        lock = {preview.repo: first.lock_entry()}
        os.remove(first.svg_path)
        again = render.render(preview, self.directory, png=False, lock=lock)
        self.assertFalse(again.skipped)


class LockFile(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp()
        self.path = os.path.join(self.directory, "previews.lock.json")

    def tearDown(self):
        shutil.rmtree(self.directory, ignore_errors=True)

    def test_a_missing_lock_reads_as_empty(self):
        self.assertEqual(render.load_lock(self.path), {})

    def test_a_corrupt_lock_is_reported(self):
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("{oops")
        with self.assertRaises(RenderError):
            render.load_lock(self.path)

    def test_the_lock_round_trips_and_is_sorted(self):
        entries = {"o/z": {"svg": "sha256:z"}, "o/a": {"svg": "sha256:a"}}
        render.save_lock(self.path, entries)
        with open(self.path, "r", encoding="utf-8") as handle:
            document = json.load(handle)
        self.assertEqual(list(document["previews"]), ["o/a", "o/z"])
        self.assertEqual(render.load_lock(self.path), entries)

    def test_verification_reports_drift_and_absence(self):
        preview = Preview.from_dict(ENTRY)
        result = render.render(preview, self.directory, png=False)
        problems, notes = render.verify([result], {preview.repo: result.lock_entry()})
        self.assertEqual((problems, notes), ([], []))

        problems, _ = render.verify([result], {})
        self.assertIn("not in the lock file", problems[0])

        stale = dict(result.lock_entry(), svg="sha256:0000")
        problems, _ = render.verify([result], {preview.repo: stale})
        self.assertIn("SVG digest changed", problems[0])

    @unittest.skipUnless(has_rasteriser(), "no SVG rasteriser installed")
    def test_png_bytes_are_not_compared_across_rasteriser_builds(self):
        preview = Preview.from_dict(ENTRY)
        result = render.render(preview, self.directory, force=True)
        elsewhere = dict(
            result.lock_entry(), backend="rsvg-convert 0.1", png="sha256:0000"
        )
        problems, notes = render.verify([result], {preview.repo: elsewhere})
        self.assertEqual(problems, [])
        self.assertIn("PNG not compared", notes[0])

        same = dict(result.lock_entry(), png="sha256:0000")
        problems, _ = render.verify([result], {preview.repo: same})
        self.assertIn("PNG digest changed", problems[0])


class Backends(unittest.TestCase):
    def test_an_unknown_backend_is_reported(self):
        with self.assertRaises(RenderError) as caught:
            raster.select("imagination")
        self.assertIn("supported", str(caught.exception))

    def test_available_backends_report_a_version(self):
        for name, version in raster.available().items():
            self.assertTrue(version.startswith(name.split("-")[0]) or version, name)
