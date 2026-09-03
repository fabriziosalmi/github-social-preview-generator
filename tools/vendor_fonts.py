#!/usr/bin/env python3
"""Fetch and verify the upstream font archives listed in ``fonts.lock.json``.

This is the only step in the whole project that touches the network, and it is
needed once. It downloads the pinned archives, checks every SHA-256 in the lock
file before extracting anything, and drops the faces and their licences into
``src/gspg/assets/fonts/``. From there ``build_glyphpack.py`` produces the packs that
are committed, and rendering never needs a font file — or a network — again.

A clone that already has the glyph packs does not need to run this at all. It
exists so the derivation from upstream is auditable rather than asserted.

Usage::

    tools/vendor_fonts.py                 # download, verify, extract
    tools/vendor_fonts.py --verify-only   # check what is already on disk
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import sys
import urllib.error
import urllib.request
import zipfile
from typing import Dict, List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
#: Committed: glyph packs, licences and the lock file travel with the package.
FONT_DIR = os.path.join(ROOT, "src", "gspg", "assets", "fonts")
#: Not committed: the upstream .ttf files are build inputs only.
SOURCE_DIR = os.path.join(ROOT, "vendor", "fonts")
LOCK_PATH = os.path.join(FONT_DIR, "fonts.lock.json")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_lock() -> Dict[str, dict]:
    with open(LOCK_PATH, "r", encoding="utf-8") as handle:
        return json.load(handle)["sources"]


def download(url: str, timeout: float = 120.0) -> bytes:
    print("  fetching %s" % (url,))
    request = urllib.request.Request(
        url, headers={"User-Agent": "github-social-preview-generator/vendor_fonts"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except (urllib.error.URLError, OSError) as error:
        raise SystemExit("error: could not download %s: %s" % (url, error))


def verify_only() -> int:
    """Check the extracted faces against the lock without downloading."""
    problems: List[str] = []
    for family, spec in sorted(load_lock().items()):
        for member, face in sorted(spec["faces"].items()):
            path = os.path.join(SOURCE_DIR, face["as"])
            if not os.path.exists(path):
                problems.append("%s: missing (%s)" % (face["as"], family))
                continue
            with open(path, "rb") as handle:
                actual = digest(handle.read())
            if actual != face["sha256"]:
                problems.append(
                    "%s: SHA-256 mismatch\n    expected %s\n    actual   %s"
                    % (face["as"], face["sha256"], actual)
                )
            else:
                print("  ok  %s" % (face["as"],))
    for problem in problems:
        print("  !!  %s" % (problem,), file=sys.stderr)
    return 1 if problems else 0


def vendor(force: bool = False) -> int:
    os.makedirs(SOURCE_DIR, exist_ok=True)
    for family, spec in sorted(load_lock().items()):
        print("%s %s" % (family, spec["version"]))

        wanted = [
            (member, face)
            for member, face in sorted(spec["faces"].items())
            if force or not os.path.exists(os.path.join(SOURCE_DIR, face["as"]))
        ]
        licence = spec["license"]
        licence_path = os.path.join(FONT_DIR, licence["as"])
        if not wanted and os.path.exists(licence_path):
            print("  already present")
            continue

        payload = download(spec["url"])
        actual = digest(payload)
        if actual != spec["archive_sha256"]:
            raise SystemExit(
                "error: %s archive SHA-256 mismatch\n  expected %s\n  actual   %s\n"
                "Refusing to extract an archive that is not the pinned one."
                % (family, spec["archive_sha256"], actual)
            )
        print("  archive verified")

        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            for member, face in sorted(spec["faces"].items()):
                data = archive.read(member)
                if digest(data) != face["sha256"]:
                    raise SystemExit(
                        "error: %s SHA-256 mismatch inside the archive" % (member,)
                    )
                destination = os.path.join(SOURCE_DIR, face["as"])
                with open(destination, "wb") as handle:
                    handle.write(data)
                print("  wrote vendor/fonts/%s" % (face["as"],))
            with open(licence_path, "wb") as handle:
                handle.write(archive.read(licence["member"]))
            print("  wrote %s  (%s)" % (os.path.relpath(licence_path, ROOT), licence["name"]))
    print()
    print("Next: make glyphs")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--verify-only", action="store_true", help="check local files, download nothing"
    )
    parser.add_argument("--force", action="store_true", help="re-download even if present")
    args = parser.parse_args(argv)
    return verify_only() if args.verify_only else vendor(args.force)


if __name__ == "__main__":
    raise SystemExit(main())
