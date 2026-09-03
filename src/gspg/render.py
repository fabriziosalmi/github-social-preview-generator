"""The render pipeline: a manifest entry in, an SVG and a PNG out.

Every step is a pure function of the entry plus the pinned assets, so a build
is reproducible. The lock file records what each input hashed to and what came
out, which is what lets ``--check`` prove that nothing drifted and lets a
rebuild skip work that would produce the same bytes.
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Dict, Iterable, List, Optional, Tuple

from . import __version__, palette as palette_module, patterns, raster, templates
from .errors import RenderError
from .model import Preview
from .typography import Face

LOCK_FILENAME = "previews.lock.json"

#: Bumped whenever a change to this package would alter existing artwork.
#: It goes into the input digest, so a bump invalidates every cached render.
RENDER_EPOCH = 6


class Result:
    """What one render produced."""

    __slots__ = (
        "preview", "pattern", "accent", "svg_path", "png_path",
        "svg_digest", "png_digest", "input_digest", "backend", "warnings", "skipped",
    )

    def __init__(self, **values) -> None:
        for field in self.__slots__:
            setattr(self, field, values.get(field))

    def lock_entry(self) -> Dict[str, object]:
        return {
            "input": self.input_digest,
            "svg": self.svg_digest,
            "png": self.png_digest,
            "pattern": self.pattern,
            "accent": self.accent,
            "backend": self.backend,
            "generator": "gspg %s" % (__version__,),
        }


def _digest_file(path: str) -> str:
    hasher = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            hasher.update(chunk)
    return "sha256:" + hasher.hexdigest()


def _digest_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def asset_fingerprint() -> str:
    """A digest of every glyph pack, so a font change invalidates the cache."""
    hasher = hashlib.sha256()
    for name in (templates.DISPLAY_FACE, templates.BODY_FACE):
        face = Face.load(name)
        hasher.update(name.encode("utf-8"))
        hasher.update(b"%d/%d/%d" % (face.units_per_em, face.cap_height, face.ascender))
    return hasher.hexdigest()[:16]


def input_digest(preview: Preview, width: int, height: int) -> str:
    """Everything that can change the output, hashed into one value."""
    payload = {
        "entry": preview.to_dict(),
        "size": [width, height],
        "epoch": RENDER_EPOCH,
        "assets": asset_fingerprint(),
    }
    return _digest_text(json.dumps(payload, sort_keys=True, ensure_ascii=False))


def compose(preview: Preview) -> Tuple[str, templates.Composition, str]:
    """Build the SVG for ``preview``; returns (svg, composition, pattern)."""
    pattern_name = patterns.resolve(preview.pattern)
    colors = palette_module.build(
        preview.seed, preview.accent, "light", preview.saturation
    )
    composition = templates.render_into(preview, colors, preview.seed, pattern_name)
    return composition.document.to_string(), composition, pattern_name


def render(
    preview: Preview,
    output_dir: str,
    width: int = int(templates.WIDTH),
    height: int = int(templates.HEIGHT),
    backend: Optional[raster.Backend] = None,
    png: bool = True,
    lock: Optional[Dict[str, Dict[str, object]]] = None,
    force: bool = False,
) -> Result:
    """Render one preview into ``output_dir``.

    When the entry's input digest matches the lock file and both output files
    are present, the render is skipped: identical inputs cannot produce
    different bytes, so redoing the work would only cost time.
    """
    svg_dir = os.path.join(output_dir, "svg")
    png_dir = os.path.join(output_dir, "png")
    svg_path = os.path.join(svg_dir, preview.slug + ".svg")
    png_path = os.path.join(png_dir, preview.slug + ".png")

    digest = input_digest(preview, width, height)
    previous = (lock or {}).get(preview.repo)
    if (
        not force
        and previous
        and previous.get("input") == digest
        and os.path.exists(svg_path)
        and (not png or os.path.exists(png_path))
    ):
        return Result(
            preview=preview,
            pattern=previous.get("pattern"),
            accent=previous.get("accent"),
            svg_path=svg_path,
            png_path=png_path if png else None,
            svg_digest=previous.get("svg"),
            png_digest=previous.get("png"),
            input_digest=digest,
            backend=previous.get("backend"),
            warnings=[],
            skipped=True,
        )

    document, composition, pattern_name = compose(preview)
    warnings: List[str] = list(composition.palette.check_contrast())

    os.makedirs(svg_dir, exist_ok=True)
    with open(svg_path, "w", encoding="utf-8") as handle:
        handle.write(document)

    png_digest = None
    backend_name = None
    if png:
        chosen = raster.rasterize(svg_path, png_path, width, height, backend)
        backend_name = chosen.version()
        oversize = raster.check_upload_size(png_path)
        if oversize:
            warnings.append(oversize)
        png_digest = _digest_file(png_path)

    return Result(
        preview=preview,
        pattern=pattern_name,
        accent=composition.palette.accent.hex(),
        svg_path=svg_path,
        png_path=png_path if png else None,
        svg_digest=_digest_text(document),
        png_digest=png_digest,
        input_digest=digest,
        backend=backend_name,
        warnings=warnings,
        skipped=False,
    )


# -- lock file -----------------------------------------------------------


def load_lock(path: str) -> Dict[str, Dict[str, object]]:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except ValueError as error:
        raise RenderError("%s is not valid JSON: %s" % (path, error))
    entries = data.get("previews") if isinstance(data, dict) else None
    return entries if isinstance(entries, dict) else {}


def save_lock(path: str, entries: Dict[str, Dict[str, object]]) -> None:
    """Write the lock file with sorted keys, so it diffs cleanly."""
    payload = {
        "version": 1,
        "generator": "gspg %s" % (__version__,),
        "epoch": RENDER_EPOCH,
        "previews": {repo: entries[repo] for repo in sorted(entries)},
    }
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def verify(
    results: Iterable[Result], lock: Dict[str, Dict[str, object]]
) -> Tuple[List[str], List[str]]:
    """Compare fresh renders against the lock file.

    Returns ``(problems, notes)``. The SVG digest is compared unconditionally:
    identical input must give identical bytes on any machine, and a mismatch is
    a failure. The PNG digest is only compared when the same rasteriser build
    produced both, because two builds of librsvg can encode the same image
    differently. Pretending otherwise would turn an honest guarantee into a
    flaky one.
    """
    problems: List[str] = []
    notes: List[str] = []
    for result in results:
        recorded = lock.get(result.preview.repo)
        if recorded is None:
            problems.append("%s: not in the lock file" % (result.preview.repo,))
            continue

        expected_svg = recorded.get("svg")
        if expected_svg and result.svg_digest and expected_svg != result.svg_digest:
            problems.append(
                "%s: SVG digest changed\n    expected %s\n    actual   %s"
                % (result.preview.repo, expected_svg, result.svg_digest)
            )

        if result.png_digest is None:
            continue
        expected_png = recorded.get("png")
        if not expected_png:
            continue
        if recorded.get("backend") != result.backend:
            notes.append(
                "%s: PNG not compared (rendered with %s, lock records %s)"
                % (result.preview.repo, result.backend, recorded.get("backend"))
            )
        elif expected_png != result.png_digest:
            problems.append(
                "%s: PNG digest changed\n    expected %s\n    actual   %s"
                % (result.preview.repo, expected_png, result.png_digest)
            )
    return problems, notes
