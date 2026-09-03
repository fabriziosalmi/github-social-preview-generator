"""Build a manifest from a GitHub account's public repositories.

This is a convenience, not part of the render path: it makes one read-only,
unauthenticated call to the public API so a manifest does not have to be typed
out by hand. Rendering itself never touches the network.

Existing entries win. Re-importing refreshes what GitHub knows (description,
language, licence, topics) and leaves every design choice — accent, template,
pattern, custom title — exactly as it was.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Sequence

from .audit import _get
from .errors import AuditError
from .model import FIELDS

#: Fields refreshed from GitHub on re-import. Everything else is yours.
#: Only ``description`` reaches the image; the rest appear in the gallery.
UPSTREAM_FIELDS = ("description", "language", "license", "topics")

#: Design choices, preserved across re-imports.
PRESERVED_FIELDS = tuple(sorted(FIELDS - set(UPSTREAM_FIELDS) - {"repo"}))


def fetch(
    owner: str, timeout: float = 15.0, include_forks: bool = False
) -> List[Dict[str, Any]]:
    """Return one raw entry per public, non-archived repository."""
    entries: List[Dict[str, Any]] = []
    for page in range(1, 11):
        url = (
            "https://api.github.com/users/%s/repos"
            "?per_page=100&type=owner&sort=full_name&page=%d" % (owner, page)
        )
        try:
            payload = json.loads(
                _get(url, timeout, limit=4 * 1024 * 1024).decode("utf-8")
            )
        except FileNotFoundError:
            raise AuditError("no such GitHub user or organisation: %s" % (owner,))
        except ValueError as error:
            raise AuditError("unexpected response from the GitHub API: %s" % (error,))
        if not isinstance(payload, list):
            message = payload.get("message") if isinstance(payload, dict) else "not a list"
            raise AuditError("unexpected response from the GitHub API: %s" % (message,))
        if not payload:
            break
        for repository in payload:
            if repository.get("archived"):
                continue
            if repository.get("fork") and not include_forks:
                continue
            entries.append(_entry(repository))
        if len(payload) < 100:
            break
    return entries


def _entry(repository: Dict[str, Any]) -> Dict[str, Any]:
    licence = repository.get("license") or {}
    entry: Dict[str, Any] = {"repo": repository["full_name"]}
    description = (repository.get("description") or "").strip()
    if description:
        entry["description"] = description
    if repository.get("language"):
        entry["language"] = repository["language"]
    if licence.get("spdx_id") and licence["spdx_id"] not in ("NOASSERTION", "NONE"):
        entry["license"] = licence["spdx_id"]
    topics = [topic for topic in (repository.get("topics") or []) if topic][:3]
    if topics:
        entry["topics"] = topics
    return entry


def merge(
    existing: Sequence[Dict[str, Any]], fetched: Sequence[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Fold ``fetched`` into ``existing`` without losing local edits.

    Upstream metadata is refreshed; design choices and any hand-written title
    or description are kept. Repositories that have disappeared upstream stay
    in the manifest — deleting someone's entry because an API call came back
    short would be the wrong default.
    """
    by_repo: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for entry in existing:
        repo = entry.get("repo")
        if not repo:
            continue
        by_repo[repo] = dict(entry)
        order.append(repo)

    for entry in fetched:
        repo = entry["repo"]
        if repo not in by_repo:
            by_repo[repo] = entry
            order.append(repo)
            continue
        current = by_repo[repo]
        for field in UPSTREAM_FIELDS:
            # A description written by hand is not overwritten by the upstream
            # one; clearing it in the manifest is how you ask for a refresh.
            if field == "description" and current.get("description"):
                continue
            if field in entry:
                current[field] = entry[field]
    return [by_repo[repo] for repo in order]


def summarise(
    existing: Sequence[Dict[str, Any]], merged: Sequence[Dict[str, Any]]
) -> Dict[str, int]:
    before = {entry.get("repo") for entry in existing}
    after = {entry.get("repo") for entry in merged}
    return {"added": len(after - before), "kept": len(before & after), "total": len(after)}
