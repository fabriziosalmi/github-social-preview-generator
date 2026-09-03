"""Exception types shared across the package.

Every failure a user can plausibly cause is one of these, so the command line
can print a short actionable message instead of a traceback.
"""

from __future__ import annotations


class GspgError(Exception):
    """Base class for every error this tool raises deliberately."""


class ConfigError(GspgError):
    """The manifest is missing, malformed or internally inconsistent."""


class AssetError(GspgError):
    """A required asset — a glyph pack, a template — is missing or unreadable."""


class RenderError(GspgError):
    """A preview could not be composed or rasterised."""


class AuditError(GspgError):
    """Coverage could not be determined for a repository."""
