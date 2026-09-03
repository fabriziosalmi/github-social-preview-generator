"""The description of one preview, independent of how it gets drawn."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from .errors import ConfigError

_SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

#: The house background. Every card uses the same field, seeded from a
#: constant, so the form is identical and only the hue changes. A repository
#: may override it, but then it stops matching its neighbours.
DEFAULT_PATTERN = "strata"

#: Fields a manifest entry may set. Anything else is a typo, and is reported
#: as one rather than silently ignored.
FIELDS = frozenset(
    {
        "repo", "title", "description", "language", "license", "topics",
        "pattern", "accent", "saturation", "seed", "skip",
    }
)


class Preview:
    """One repository's preview settings, fully resolved."""

    __slots__ = tuple(FIELDS | {"owner", "name"})

    def __init__(self, **values: Any) -> None:
        for field in self.__slots__:
            setattr(self, field, values.get(field))

    # -- construction ----------------------------------------------------

    @classmethod
    def from_dict(
        cls, raw: Dict[str, Any], defaults: Optional[Dict[str, Any]] = None
    ) -> "Preview":
        if not isinstance(raw, dict):
            raise ConfigError(
                "each repository entry must be an object, got %r"
                % (type(raw).__name__,)
            )
        unknown = sorted(set(raw) - FIELDS)
        if unknown:
            raise ConfigError(
                "unknown field(s) %s in entry %r; valid fields are: %s"
                % (", ".join(repr(key) for key in unknown),
                   raw.get("repo", "?"), ", ".join(sorted(FIELDS)))
            )

        merged: Dict[str, Any] = dict(defaults or {})
        merged.update({key: value for key, value in raw.items() if value is not None})

        repo = merged.get("repo")
        if not isinstance(repo, str) or "/" not in repo:
            raise ConfigError(
                "entry needs a 'repo' of the form 'owner/name', got %r" % (repo,)
            )
        owner, _, name = repo.partition("/")
        for part, label in ((owner, "owner"), (name, "name")):
            if not _SLUG.match(part):
                raise ConfigError("%r is not a valid repository %s" % (part, label))

        topics = merged.get("topics") or []
        if isinstance(topics, str):
            topics = [item.strip() for item in topics.split(",") if item.strip()]
        if not isinstance(topics, list) or any(not isinstance(t, str) for t in topics):
            raise ConfigError("'topics' for %s must be a list of strings" % (repo,))

        saturation = merged.get("saturation", 1.0)
        try:
            saturation = float(saturation)
        except (TypeError, ValueError):
            raise ConfigError("'saturation' for %s must be a number" % (repo,))
        if not 0.0 <= saturation <= 2.0:
            raise ConfigError("'saturation' for %s must be between 0 and 2" % (repo,))

        return cls(
            repo=repo,
            owner=owner,
            name=name,
            title=merged.get("title") or name,
            description=merged.get("description") or "",
            language=merged.get("language") or "",
            license=merged.get("license") or "",
            topics=topics,
            pattern=merged.get("pattern") or DEFAULT_PATTERN,
            accent=merged.get("accent"),
            saturation=saturation,
            seed=merged.get("seed") or repo,
            skip=bool(merged.get("skip", False)),
        )

    # -- derived ---------------------------------------------------------

    @property
    def slug(self) -> str:
        """Filename stem: ``owner__name``, safe on every filesystem."""
        return "%s__%s" % (self.owner, self.name)

    @property
    def facts(self) -> List[str]:
        """Repository facts, in a fixed order. Shown in the gallery, not on the
        card: at feed size they would be too small to read."""
        items: List[str] = []
        if self.language:
            items.append(self.language)
        if self.license:
            items.append(self.license)
        items.extend(self.topics)
        return items

    def to_dict(self) -> Dict[str, Any]:
        return {field: getattr(self, field) for field in sorted(FIELDS)}

    def __repr__(self) -> str:
        return "Preview(%r)" % (self.repo,)
