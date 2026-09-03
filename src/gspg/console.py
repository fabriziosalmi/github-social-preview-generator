"""Terminal output helpers.

Colour is applied only when the stream is a terminal and the environment has
not asked for plain text, so redirecting to a file or piping into CI produces
clean, greppable output.
"""

from __future__ import annotations

import os
import sys
from typing import IO, Optional

_CODES = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "dim": "\033[2m",
    "red": "\033[31m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "blue": "\033[34m",
    "cyan": "\033[36m",
    "grey": "\033[90m",
}


def supports_color(stream: IO[str]) -> bool:
    if os.environ.get("NO_COLOR") is not None:
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    if os.environ.get("TERM") == "dumb":
        return False
    return hasattr(stream, "isatty") and stream.isatty()


class Printer:
    """Writes to a stream, colouring only when that stream can take it."""

    def __init__(self, stream: Optional[IO[str]] = None, quiet: bool = False) -> None:
        self.stream = stream or sys.stdout
        self.quiet = quiet
        self.color = supports_color(self.stream)

    def paint(self, text: str, *styles: str) -> str:
        if not self.color or not styles:
            return text
        prefix = "".join(_CODES.get(style, "") for style in styles)
        return prefix + text + _CODES["reset"]

    def line(self, text: str = "") -> None:
        if not self.quiet:
            self.stream.write(text + "\n")

    def step(self, symbol: str, text: str, *styles: str) -> None:
        self.line("%s %s" % (self.paint(symbol, *styles), text))

    def ok(self, text: str) -> None:
        self.step("+", text, "green")

    def skip(self, text: str) -> None:
        self.step("=", text, "grey")

    def warn(self, text: str) -> None:
        self.step("!", text, "yellow")

    def fail(self, text: str) -> None:
        self.step("x", text, "red")

    def heading(self, text: str) -> None:
        self.line()
        self.line(self.paint(text, "bold"))


def error(message: str) -> None:
    """Report a fatal problem on stderr, coloured if stderr is a terminal."""
    prefix = "error:"
    if supports_color(sys.stderr):
        prefix = _CODES["red"] + _CODES["bold"] + prefix + _CODES["reset"]
    sys.stderr.write("%s %s\n" % (prefix, message))
