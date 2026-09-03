"""The manifest: which repositories get a preview, and how.

JSON, not YAML or TOML. The standard library can read and write it on every
Python this project supports, which keeps the promise of zero dependencies
literal rather than aspirational, and a JSON Schema sits next to it so an
editor can validate while you type.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from .errors import ConfigError
from .model import Preview

DEFAULT_MANIFEST = "previews.json"

#: Bumped only when an existing manifest would need editing to keep working.
SCHEMA_VERSION = 1


class Manifest:
    """A parsed manifest: defaults plus the repositories they apply to."""

    def __init__(self, path: str, defaults: Dict[str, Any], previews: List[Preview]) -> None:
        self.path = path
        self.defaults = defaults
        self.previews = previews

    def __len__(self) -> int:
        return len(self.previews)

    def __iter__(self):
        return iter(self.previews)

    def active(self) -> List[Preview]:
        """Entries not marked ``skip``."""
        return [preview for preview in self.previews if not preview.skip]

    def select(self, patterns: Optional[List[str]]) -> List[Preview]:
        """Filter by repository name; ``owner/name`` or bare ``name`` both match."""
        entries = self.active()
        if not patterns:
            return entries
        wanted = {value.lower() for value in patterns}
        chosen = [
            preview
            for preview in entries
            if preview.repo.lower() in wanted or preview.name.lower() in wanted
        ]
        missing = wanted - {p.repo.lower() for p in chosen} - {p.name.lower() for p in chosen}
        if missing:
            raise ConfigError(
                "not in %s: %s" % (self.path, ", ".join(sorted(missing)))
            )
        return chosen


def load(path: str = DEFAULT_MANIFEST) -> Manifest:
    if not os.path.exists(path):
        raise ConfigError(
            "manifest not found: %s\nRun `gspg init` to create a starter manifest."
            % (path,)
        )
    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except ValueError as error:
        raise ConfigError("%s is not valid JSON: %s" % (path, error))

    if not isinstance(raw, dict):
        raise ConfigError("%s must contain a JSON object at the top level" % (path,))

    version = raw.get("version", SCHEMA_VERSION)
    if version != SCHEMA_VERSION:
        raise ConfigError(
            "%s declares version %r; this build understands version %d"
            % (path, version, SCHEMA_VERSION)
        )

    unknown = sorted(set(raw) - {"version", "defaults", "repositories", "$schema"})
    if unknown:
        raise ConfigError(
            "unknown top-level key(s) in %s: %s" % (path, ", ".join(unknown))
        )

    defaults = raw.get("defaults") or {}
    if not isinstance(defaults, dict):
        raise ConfigError("'defaults' in %s must be an object" % (path,))
    stray = sorted(set(defaults) - (set(Preview.__slots__) - {"owner", "name", "repo"}))
    if stray:
        raise ConfigError(
            "'defaults' in %s may not set: %s" % (path, ", ".join(stray))
        )

    entries = raw.get("repositories")
    if not isinstance(entries, list):
        raise ConfigError("'repositories' in %s must be a list" % (path,))

    previews: List[Preview] = []
    seen: Dict[str, int] = {}
    for index, entry in enumerate(entries):
        try:
            preview = Preview.from_dict(entry, defaults)
        except ConfigError as error:
            raise ConfigError("%s, entry %d: %s" % (path, index + 1, error))
        if preview.repo in seen:
            raise ConfigError(
                "%s lists %s twice (entries %d and %d)"
                % (path, preview.repo, seen[preview.repo] + 1, index + 1)
            )
        seen[preview.repo] = index
        previews.append(preview)
    return Manifest(path, defaults, previews)


def starter(owner: str) -> Dict[str, Any]:
    """The manifest ``gspg init`` writes."""
    return {
        "$schema": "./schema/previews.schema.json",
        "version": SCHEMA_VERSION,
        "defaults": {},
        "repositories": [
            {
                "repo": "%s/example-repo" % (owner,),
                "description": "Replace this entry with your own repositories.",
                "language": "Python",
                "license": "MIT",
            }
        ],
    }


def write(path: str, data: Dict[str, Any]) -> None:
    """Write a manifest with stable formatting, so diffs stay small."""
    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
