"""SVG to PNG, through whichever local rasteriser is available.

No network, no service, no headless browser required — though one can be used
if it is the only thing installed. Backends are tried in order of how closely
they honour the SVG spec for the narrow subset this project emits.

The chosen backend and its version are reported so they can be recorded in the
lock file: identical SVG plus identical backend is what makes a checksum
comparison meaningful. Different backends produce visually identical but
byte-different PNGs, and pretending otherwise would be dishonest.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from typing import Dict, List, Optional, Sequence, Tuple

from .errors import RenderError

#: GitHub rejects a social preview larger than this.
MAX_UPLOAD_BYTES = 1024 * 1024


class Backend:
    """One way of turning an SVG file into a PNG file."""

    name = "abstract"
    executables: Tuple[str, ...] = ()

    def __init__(self, executable: str) -> None:
        self.executable = executable

    @classmethod
    def find(cls) -> Optional["Backend"]:
        for candidate in cls.executables:
            path = shutil.which(candidate)
            if path:
                return cls(path)
        return None

    def version(self) -> str:
        raise NotImplementedError

    def command(self, source: str, destination: str, width: int, height: int) -> List[str]:
        raise NotImplementedError

    def render(self, source: str, destination: str, width: int, height: int) -> None:
        command = self.command(source, destination, width, height)
        try:
            result = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                # A rasteriser that has not finished in two minutes is stuck.
                timeout=120,
            )
        except OSError as error:
            raise RenderError("could not run %s: %s" % (self.name, error))
        except subprocess.TimeoutExpired:
            raise RenderError("%s timed out rendering %s" % (self.name, source))
        if result.returncode != 0:
            detail = result.stderr.decode("utf-8", "replace").strip()
            raise RenderError(
                "%s failed on %s (exit %d)%s"
                % (self.name, source, result.returncode, ": " + detail if detail else "")
            )
        if not os.path.exists(destination) or os.path.getsize(destination) == 0:
            raise RenderError("%s produced no output for %s" % (self.name, source))

    def _probe(self, args: Sequence[str]) -> str:
        try:
            result = subprocess.run(
                [self.executable] + list(args),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=20,
            )
        except (OSError, subprocess.TimeoutExpired):
            return "unknown"
        text = result.stdout.decode("utf-8", "replace")
        match = re.search(r"(\d+\.\d+(?:\.\d+)?)", text)
        return match.group(1) if match else "unknown"


class ResvgBackend(Backend):
    """resvg: the strictest of the three, and the reference for this project."""

    name = "resvg"
    executables = ("resvg",)

    def version(self) -> str:
        return "resvg " + self._probe(["--version"])

    def command(self, source, destination, width, height):
        return [
            self.executable,
            "--width", str(width),
            "--height", str(height),
            "--background", "#00000000",
            source, destination,
        ]


class RsvgBackend(Backend):
    """librsvg, shipped with GNOME and available through Homebrew."""

    name = "rsvg-convert"
    executables = ("rsvg-convert",)

    def version(self) -> str:
        return "rsvg-convert " + self._probe(["--version"])

    def command(self, source, destination, width, height):
        return [
            self.executable,
            "--width", str(width),
            "--height", str(height),
            "--keep-aspect-ratio",
            "--format", "png",
            "--output", destination,
            source,
        ]


class InkscapeBackend(Backend):
    name = "inkscape"
    executables = ("inkscape",)

    def version(self) -> str:
        return "inkscape " + self._probe(["--version"])

    def command(self, source, destination, width, height):
        return [
            self.executable,
            "--export-type=png",
            "--export-width=%d" % (width,),
            "--export-height=%d" % (height,),
            "--export-filename=%s" % (destination,),
            source,
        ]


class ChromiumBackend(Backend):
    """A last resort: correct, but it drags in a whole browser."""

    name = "chromium"
    executables = (
        "chromium",
        "chromium-browser",
        "google-chrome",
        "google-chrome-stable",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
    )

    @classmethod
    def find(cls) -> Optional["Backend"]:
        for candidate in cls.executables:
            path = shutil.which(candidate)
            if path:
                return cls(path)
            if os.path.isabs(candidate) and os.path.exists(candidate):
                return cls(candidate)
        return None

    def version(self) -> str:
        return "chromium " + self._probe(["--version"])

    def command(self, source, destination, width, height):
        return [
            self.executable,
            "--headless",
            "--disable-gpu",
            "--hide-scrollbars",
            "--force-device-scale-factor=1",
            "--default-background-color=00000000",
            "--screenshot=%s" % (destination,),
            "--window-size=%d,%d" % (width, height),
            "file://" + os.path.abspath(source),
        ]


#: Preference order. resvg first because it is the most predictable.
BACKENDS: Tuple[type, ...] = (ResvgBackend, RsvgBackend, InkscapeBackend, ChromiumBackend)


def available() -> Dict[str, str]:
    """Return ``{backend name: version}`` for every rasteriser on this machine."""
    found: Dict[str, str] = {}
    for backend_class in BACKENDS:
        backend = backend_class.find()
        if backend is not None:
            found[backend.name] = backend.version()
    return found


def select(preferred: Optional[str] = None) -> Backend:
    """Pick a backend, honouring an explicit ``preferred`` name."""
    if preferred:
        for backend_class in BACKENDS:
            if backend_class.name == preferred:
                backend = backend_class.find()
                if backend is None:
                    raise RenderError(
                        "rasteriser %r is not installed; found: %s"
                        % (preferred, ", ".join(sorted(available())) or "none")
                    )
                return backend
        raise RenderError(
            "unknown rasteriser %r; supported: %s"
            % (preferred, ", ".join(cls.name for cls in BACKENDS))
        )
    for backend_class in BACKENDS:
        backend = backend_class.find()
        if backend is not None:
            return backend
    raise RenderError(
        "no SVG rasteriser found. Install one of: %s\n"
        "  macOS:  brew install librsvg      (rsvg-convert)\n"
        "  Debian: apt install librsvg2-bin\n"
        "  Rust:   cargo install resvg"
        % (", ".join(cls.name for cls in BACKENDS),)
    )


def rasterize(
    source: str,
    destination: str,
    width: int,
    height: int,
    backend: Optional[Backend] = None,
) -> Backend:
    """Render ``source`` to ``destination`` and return the backend used."""
    chosen = backend or select()
    directory = os.path.dirname(os.path.abspath(destination))
    if directory:
        os.makedirs(directory, exist_ok=True)
    chosen.render(source, destination, width, height)
    return chosen


def check_upload_size(path: str) -> Optional[str]:
    """Warn when a PNG is too large for GitHub's social preview upload."""
    size = os.path.getsize(path)
    if size <= MAX_UPLOAD_BYTES:
        return None
    return (
        "%s is %.2f MiB, over GitHub's 1 MiB limit for social previews. "
        "Lower the grain density or pick a flatter pattern."
        % (os.path.basename(path), size / (1024.0 * 1024.0))
    )
