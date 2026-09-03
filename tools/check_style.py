#!/usr/bin/env python3
"""A small house-style check, so the project needs no linter to stay tidy.

It enforces the handful of rules that actually matter here and that a reader
would notice: line length, no tabs, no trailing whitespace, a final newline,
every module documented, and no stray debugging left behind.
"""

from __future__ import annotations

import ast
import os
import sys
from typing import Iterator, List, Tuple

MAX_LINE = 96
ROOTS = ("src", "tools", "tests")
#: Assembled at runtime so this file does not trip its own check.
_FORBIDDEN = ("break" + "point()", "pdb." + "set_trace")


def python_files() -> Iterator[str]:
    for root in ROOTS:
        for directory, _subdirs, names in os.walk(root):
            if "__pycache__" in directory:
                continue
            for name in sorted(names):
                if name.endswith(".py"):
                    yield os.path.join(directory, name)


def check(path: str) -> List[Tuple[int, str]]:
    problems: List[Tuple[int, str]] = []
    with open(path, "r", encoding="utf-8") as handle:
        source = handle.read()

    if source and not source.endswith("\n"):
        problems.append((len(source.splitlines()), "no newline at end of file"))

    for number, line in enumerate(source.splitlines(), start=1):
        if "\t" in line:
            problems.append((number, "tab character"))
        if line != line.rstrip():
            problems.append((number, "trailing whitespace"))
        if len(line) > MAX_LINE:
            problems.append((number, "line is %d characters (max %d)" % (len(line), MAX_LINE)))
        for needle in _FORBIDDEN:
            if needle in line:
                problems.append((number, "leftover %s" % (needle,)))

    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as error:
        problems.append((error.lineno or 1, "syntax error: %s" % (error.msg,)))
        return problems
    if ast.get_docstring(tree) is None:
        problems.append((1, "module has no docstring"))
    return problems


def main() -> int:
    failures = 0
    for path in python_files():
        for number, message in check(path):
            print("%s:%d: %s" % (path, number, message))
            failures += 1
    if failures:
        print("\n%d style problem(s)" % (failures,), file=sys.stderr)
        return 1
    print("style ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
