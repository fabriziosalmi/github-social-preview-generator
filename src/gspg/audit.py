"""Which repositories actually have a custom social preview, and which do not.

GitHub has no API for reading or writing a repository's social preview, so
this asks the public repository page the same question a link unfurler would
and reads the answer out of the Open Graph tag:

* ``opengraph.githubassets.com/...`` is the image GitHub renders on the fly —
  the repository has **no** custom preview;
* ``repository-images.githubusercontent.com/...`` is an uploaded one.

Everything here is an unauthenticated GET of a public page. No token, no
``GITHUB_TOKEN``, nothing to put in Actions secrets, and nothing that could
leak if the workflow log is public. That is a deliberate design constraint,
not an omission: a coverage report should never be a reason to hand a CI job
credentials over a repository.
"""

from __future__ import annotations

import http.client
import json
import os
import re
import time
import urllib.error
import urllib.request
from typing import Dict, Iterable, List, Optional, Sequence

from .errors import AuditError

USER_AGENT = "github-social-preview-generator (+https://github.com/topics/social-preview)"

#: Status values, ordered from "needs work" to "done".
STATUS_ERROR = "error"
STATUS_MISSING = "missing"
STATUS_DEFAULT = "default"
STATUS_CUSTOM = "custom"

_OG_IMAGE = re.compile(
    r'<meta\s+(?:property|name)="og:image"\s+content="([^"]+)"', re.IGNORECASE
)
_CUSTOM_HOST = "repository-images.githubusercontent.com"
_DEFAULT_HOST = "opengraph.githubassets.com"


class Coverage:
    """One repository's preview status."""

    __slots__ = ("repo", "status", "image", "detail", "generated")

    def __init__(
        self,
        repo: str,
        status: str,
        image: Optional[str] = None,
        detail: str = "",
        generated: bool = False,
    ) -> None:
        self.repo = repo
        self.status = status
        self.image = image
        self.detail = detail
        self.generated = generated

    @property
    def uploaded(self) -> bool:
        return self.status == STATUS_CUSTOM

    @property
    def action(self) -> str:
        """What a human should do next about this repository."""
        if self.status == STATUS_CUSTOM:
            return "nothing"
        if self.status == STATUS_DEFAULT:
            return "upload" if self.generated else "generate, then upload"
        if self.status == STATUS_MISSING:
            return "check the name, or the repository is private"
        return "retry"

    def to_dict(self) -> Dict[str, object]:
        return {
            "repo": self.repo,
            "status": self.status,
            "generated": self.generated,
            "image": self.image,
            "detail": self.detail,
            "action": self.action,
        }


#: Only the document head is needed, and a repository page can be megabytes.
#: Reading a bounded prefix keeps the check fast and sidesteps the truncated
#: chunked responses GitHub sends when a client stops early.
HEAD_BYTES = 262144


def _get(url: str, timeout: float, retries: int = 3, limit: int = HEAD_BYTES) -> bytes:
    """Fetch up to ``limit`` bytes of ``url``, backing off on throttling."""
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity"}
    )
    last_error = ""
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                try:
                    return response.read(limit)
                except http.client.IncompleteRead as error:
                    # A truncated body still carries the head we came for.
                    return error.partial
        except urllib.error.HTTPError as error:
            if error.code == 404:
                raise FileNotFoundError(url)
            if error.code in (403, 429) or 500 <= error.code < 600:
                last_error = "HTTP %d" % (error.code,)
                time.sleep(1.5 * (2 ** attempt))
                continue
            raise AuditError("%s returned HTTP %d" % (url, error.code))
        except urllib.error.URLError as error:
            last_error = str(error.reason)
            time.sleep(1.0 * (2 ** attempt))
        except (http.client.HTTPException, OSError) as error:
            last_error = str(error) or error.__class__.__name__
            time.sleep(1.0 * (2 ** attempt))
    raise AuditError("%s failed after %d attempts: %s" % (url, retries, last_error))


def classify(html: bytes) -> Coverage:
    """Read the Open Graph image out of a repository page. Repo name filled in later."""
    match = _OG_IMAGE.search(html.decode("utf-8", "replace"))
    if not match:
        return Coverage("", STATUS_ERROR, detail="no og:image tag in the page")
    image = match.group(1)
    if _CUSTOM_HOST in image:
        return Coverage("", STATUS_CUSTOM, image=image)
    if _DEFAULT_HOST in image:
        return Coverage("", STATUS_DEFAULT, image=image, detail="GitHub's generated card")
    return Coverage("", STATUS_DEFAULT, image=image, detail="unrecognised image host")


def check(repo: str, timeout: float = 15.0, delay: float = 0.0) -> Coverage:
    """Determine the preview status of ``owner/name``."""
    if delay:
        time.sleep(delay)
    url = "https://github.com/%s" % (repo,)
    try:
        html = _get(url, timeout)
    except FileNotFoundError:
        return Coverage(repo, STATUS_MISSING, detail="404 — private, renamed or gone")
    except AuditError as error:
        return Coverage(repo, STATUS_ERROR, detail=str(error))
    result = classify(html)
    result.repo = repo
    return result


def audit(
    repos: Sequence[str],
    timeout: float = 15.0,
    delay: float = 0.35,
    generated: Optional[Iterable[str]] = None,
    progress=None,
) -> List[Coverage]:
    """Check every repository in ``repos``, politely and in order.

    ``delay`` spaces the requests out. These are ordinary public page views and
    there is no token to burn quota against, but hammering a public endpoint
    from a loop is rude whether or not anyone stops you.
    """
    have_png = set(generated or ())
    results: List[Coverage] = []
    for index, repo in enumerate(repos):
        result = check(repo, timeout, delay if index else 0.0)
        result.generated = repo in have_png
        results.append(result)
        if progress is not None:
            progress(result, index + 1, len(repos))
    return results


def discover(owner: str, timeout: float = 15.0, include_forks: bool = False) -> List[str]:
    """List an owner's public repositories, unauthenticated.

    The public API allows 60 requests an hour per address, which covers a few
    hundred repositories. It is not used for the preview check itself because
    the API does not expose social previews at all.
    """
    repos: List[str] = []
    for page in range(1, 11):
        url = (
            "https://api.github.com/users/%s/repos"
            "?per_page=100&type=owner&sort=full_name&page=%d" % (owner, page)
        )
        try:
            body = _get(url, timeout, limit=4 * 1024 * 1024)
            payload = json.loads(body.decode("utf-8"))
        except FileNotFoundError:
            raise AuditError("no such GitHub user or organisation: %s" % (owner,))
        except ValueError as error:
            raise AuditError("unexpected response from the GitHub API: %s" % (error,))
        if not isinstance(payload, list):
            raise AuditError(
                "unexpected response from the GitHub API: %s"
                % (payload.get("message", "not a list")
                   if isinstance(payload, dict) else "not a list",)
            )
        if not payload:
            break
        for entry in payload:
            if entry.get("archived"):
                continue
            if entry.get("fork") and not include_forks:
                continue
            repos.append(entry["full_name"])
        if len(payload) < 100:
            break
    return repos


def generated_repos(output_dir: str, repos: Iterable[str]) -> List[str]:
    """Which of ``repos`` already have a rendered PNG on disk."""
    png_dir = os.path.join(output_dir, "png")
    present: List[str] = []
    for repo in repos:
        owner, _, name = repo.partition("/")
        if os.path.exists(os.path.join(png_dir, "%s__%s.png" % (owner, name))):
            present.append(repo)
    return present


# -- reporting -----------------------------------------------------------


def summarise(results: Sequence[Coverage]) -> Dict[str, int]:
    counts = {STATUS_CUSTOM: 0, STATUS_DEFAULT: 0, STATUS_MISSING: 0, STATUS_ERROR: 0}
    for result in results:
        counts[result.status] = counts.get(result.status, 0) + 1
    return counts


def to_markdown(results: Sequence[Coverage], title: str = "Social preview coverage") -> str:
    """A report that reads well in a repository, an issue or a Pages site."""
    counts = summarise(results)
    total = len(results)
    covered = counts[STATUS_CUSTOM]
    percent = (100.0 * covered / total) if total else 0.0

    lines = [
        "# %s" % (title,),
        "",
        "%d of %d repositories have a custom social preview (%.0f%%)."
        % (covered, total, percent),
        "",
        "| Repository | Preview | Rendered locally | Next step |",
        "| --- | --- | --- | --- |",
    ]
    symbols = {
        STATUS_CUSTOM: "custom",
        STATUS_DEFAULT: "GitHub default",
        STATUS_MISSING: "not found",
        STATUS_ERROR: "check failed",
    }
    for result in sorted(results, key=lambda item: (item.status != STATUS_DEFAULT, item.repo)):
        lines.append(
            "| [%s](https://github.com/%s) | %s | %s | %s |"
            % (
                result.repo,
                result.repo,
                symbols.get(result.status, result.status),
                "yes" if result.generated else "no",
                result.action,
            )
        )
    lines.extend([
        "",
        "Checked by reading each repository's public `og:image` tag. "
        "No authentication, no API token, no secrets.",
        "",
    ])
    return "\n".join(lines)


def to_json(results: Sequence[Coverage]) -> str:
    payload = {
        "summary": summarise(results),
        "total": len(results),
        "repositories": [result.to_dict() for result in results],
    }
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"
