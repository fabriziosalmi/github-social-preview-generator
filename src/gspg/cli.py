"""Command line interface.

    gspg build      render previews from the manifest
    gspg preview    render one repository without touching the manifest
    gspg audit      report which repositories have a custom preview
    gspg gallery    build the public gallery page
    gspg doctor     check the local toolchain
    gspg list       show the available templates and patterns
    gspg init       write a starter manifest
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import List, Optional, Sequence

from . import __version__, audit as audit_module, config, gallery as gallery_module
from . import importer as importer_module
from . import patterns, raster, render as render_module, templates
from .console import Printer, error
from .errors import GspgError
from . import model as model_module
from .model import Preview

DEFAULT_OUTPUT = "previews"


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "-m", "--manifest", default=config.DEFAULT_MANIFEST,
        help="manifest to read (default: %(default)s)",
    )
    parser.add_argument(
        "-o", "--output", default=DEFAULT_OUTPUT,
        help="directory for generated files (default: %(default)s)",
    )
    parser.add_argument("-q", "--quiet", action="store_true", help="only report problems")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gspg",
        description="Deterministic, offline social preview images for GitHub repositories.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("\n", 2)[2],
    )
    parser.add_argument("--version", action="version", version="gspg %s" % (__version__,))
    subcommands = parser.add_subparsers(dest="command", metavar="<command>")

    build = subcommands.add_parser("build", help="render previews from the manifest")
    _add_common(build)
    build.add_argument("repos", nargs="*", help="limit to these repositories")
    build.add_argument("--force", action="store_true", help="re-render even if unchanged")
    build.add_argument("--svg-only", action="store_true", help="skip rasterisation")
    build.add_argument("--backend", help="rasteriser to use (see `gspg doctor`)")
    build.add_argument(
        "--check", action="store_true",
        help="verify output matches the lock file and change nothing "
             "(exit 1 on drift)",
    )
    build.add_argument("--width", type=int, default=int(templates.WIDTH))
    build.add_argument("--height", type=int, default=int(templates.HEIGHT))
    build.set_defaults(handler=command_build)

    preview = subcommands.add_parser(
        "preview", help="render one repository without touching the manifest"
    )
    preview.add_argument("repo", help="owner/name")
    preview.add_argument("-o", "--output", default=os.path.join(DEFAULT_OUTPUT, "scratch"))
    preview.add_argument("-q", "--quiet", action="store_true")
    preview.add_argument("--title")
    preview.add_argument("--description", default="")
    preview.add_argument("--language", default="")
    preview.add_argument("--license", default="")
    preview.add_argument("--topics", default="", help="comma separated")
    preview.add_argument("--pattern", default=None, choices=patterns.names())
    preview.add_argument("--accent", help="hue name, degrees or hex colour")
    preview.add_argument("--backend")
    preview.set_defaults(handler=command_preview)

    audit = subcommands.add_parser(
        "audit", help="report which repositories have a custom social preview"
    )
    _add_common(audit)
    audit.add_argument(
        "--discover", metavar="OWNER",
        help="check every public repository of OWNER instead of the manifest",
    )
    audit.add_argument("--include-forks", action="store_true", help="with --discover")
    audit.add_argument("--json", metavar="PATH", help="also write a JSON report")
    audit.add_argument("--markdown", metavar="PATH", help="also write a Markdown report")
    audit.add_argument(
        "--strict", action="store_true",
        help="exit 1 when any repository still lacks a custom preview",
    )
    audit.add_argument("--delay", type=float, default=0.35, help="seconds between requests")
    audit.add_argument("--timeout", type=float, default=15.0)
    audit.set_defaults(handler=command_audit)

    gallery = subcommands.add_parser(
        "gallery", help="assemble the self-contained public gallery site"
    )
    _add_common(gallery)
    gallery.add_argument(
        "--site", default="site", help="site directory to assemble (default: %(default)s)"
    )
    gallery.add_argument(
        "--raw-base", default="",
        help="raw URL prefix the PNGs are also reachable at, e.g. "
             "https://raw.githubusercontent.com/OWNER/REPO/main/previews/png/",
    )
    gallery.set_defaults(handler=command_gallery)

    importing = subcommands.add_parser(
        "import", help="build or refresh a manifest from a GitHub account"
    )
    importing.add_argument("owner", help="GitHub username or organisation")
    importing.add_argument("-m", "--manifest", default=config.DEFAULT_MANIFEST)
    importing.add_argument("-q", "--quiet", action="store_true")
    importing.add_argument("--include-forks", action="store_true")
    importing.add_argument("--timeout", type=float, default=15.0)
    importing.add_argument(
        "--dry-run", action="store_true", help="report what would change and write nothing"
    )
    importing.set_defaults(handler=command_import)

    doctor = subcommands.add_parser("doctor", help="check the local toolchain")
    doctor.add_argument("-q", "--quiet", action="store_true")
    doctor.set_defaults(handler=command_doctor)

    listing = subcommands.add_parser("list", help="show the background fields and accents")
    listing.add_argument("-q", "--quiet", action="store_true")
    listing.set_defaults(handler=command_list)

    initialise = subcommands.add_parser("init", help="write a starter manifest")
    initialise.add_argument("owner", help="your GitHub username or organisation")
    initialise.add_argument("-m", "--manifest", default=config.DEFAULT_MANIFEST)
    initialise.add_argument("-q", "--quiet", action="store_true")
    initialise.add_argument(
        "--force", action="store_true", help="overwrite an existing manifest"
    )
    initialise.set_defaults(handler=command_init)

    return parser


# -- commands ------------------------------------------------------------


def command_build(args: argparse.Namespace) -> int:
    printer = Printer(quiet=args.quiet)
    manifest = config.load(args.manifest)
    selected = manifest.select(args.repos)
    if not selected:
        printer.warn("nothing to do: no active entries in %s" % (args.manifest,))
        return 0

    lock_path = os.path.join(os.path.dirname(os.path.abspath(args.manifest)),
                             render_module.LOCK_FILENAME)
    lock = render_module.load_lock(lock_path)
    backend = None if args.svg_only else raster.select(args.backend)

    results = []
    warnings: List[str] = []
    for preview in selected:
        result = render_module.render(
            preview,
            args.output,
            width=args.width,
            height=args.height,
            backend=backend,
            png=not args.svg_only,
            lock=lock,
            force=args.force or args.check,
        )
        results.append(result)
        for warning in result.warnings:
            warnings.append("%s: %s" % (preview.repo, warning))
        if result.skipped:
            printer.skip("%s  unchanged" % (preview.repo,))
        else:
            printer.ok(
                "%s  %s"
                % (preview.repo, printer.paint(result.accent or "", "grey"))
            )

    for warning in warnings:
        printer.warn(warning)

    if args.check:
        problems, notes = render_module.verify(results, lock)
        printer.line()
        if notes:
            printer.warn(
                "%d preview(s) rendered with a different rasteriser build; "
                "their PNG bytes were not compared." % (len(notes),)
            )
        if problems:
            for problem in problems:
                printer.fail(problem)
            error(
                "output does not match %s. Run `gspg build` and commit the result."
                % (os.path.basename(lock_path),)
            )
            return 1
        printer.ok("%d preview(s) match the lock file" % (len(results),))
        return 0

    # Entries for repositories outside this run are carried over untouched, so
    # building a single repository never drops the rest of the lock file.
    merged = dict(lock)
    merged.update({result.preview.repo: result.lock_entry() for result in results})
    render_module.save_lock(lock_path, merged)
    printer.line()
    printer.ok(
        "%d preview(s) in %s" % (len(results), os.path.join(args.output, "png"))
    )
    return 1 if warnings else 0


def command_preview(args: argparse.Namespace) -> int:
    printer = Printer(quiet=args.quiet)
    preview = Preview.from_dict(
        {
            "repo": args.repo,
            "title": args.title,
            "description": args.description,
            "language": args.language,
            "license": args.license,
            "topics": args.topics,
            "pattern": args.pattern,
            "accent": args.accent,
        }
    )
    result = render_module.render(
        preview, args.output, backend=raster.select(args.backend), force=True
    )
    for warning in result.warnings:
        printer.warn(warning)
    printer.ok("%s  %s  %s" % (preview.repo, result.pattern, result.accent))
    printer.line(result.png_path or result.svg_path)
    return 0


def command_audit(args: argparse.Namespace) -> int:
    printer = Printer(quiet=args.quiet)
    if args.discover:
        printer.line("Discovering public repositories for %s ..." % (args.discover,))
        repos = audit_module.discover(args.discover, args.timeout, args.include_forks)
    else:
        repos = [preview.repo for preview in config.load(args.manifest).active()]
    if not repos:
        printer.warn("no repositories to check")
        return 0

    rendered = set(audit_module.generated_repos(args.output, repos))
    printer.line("Checking %d repositories (public pages, no authentication)" % (len(repos),))
    printer.line()

    labels = {
        audit_module.STATUS_CUSTOM: ("custom preview", ("green",), "+"),
        audit_module.STATUS_DEFAULT: ("GitHub default", ("yellow",), "!"),
        audit_module.STATUS_MISSING: ("not found", ("grey",), "?"),
        audit_module.STATUS_ERROR: ("check failed", ("red",), "x"),
    }

    def report(result, index, total):
        label, styles, symbol = labels[result.status]
        suffix = "" if result.uploaded else ("  [rendered]" if result.generated else "")
        printer.line(
            "%s %-44s %s%s"
            % (printer.paint(symbol, *styles), result.repo,
               printer.paint(label, *styles), printer.paint(suffix, "grey"))
        )

    results = audit_module.audit(
        repos, args.timeout, args.delay, generated=rendered, progress=report
    )

    counts = audit_module.summarise(results)
    total = len(results)
    printer.heading("Coverage")
    printer.line(
        "  %d/%d custom  %d default  %d not found  %d errors"
        % (counts[audit_module.STATUS_CUSTOM], total,
           counts[audit_module.STATUS_DEFAULT],
           counts[audit_module.STATUS_MISSING],
           counts[audit_module.STATUS_ERROR])
    )

    todo = [r for r in results if r.status == audit_module.STATUS_DEFAULT]
    if todo:
        ready = [r for r in todo if r.generated]
        printer.line()
        printer.line("  %d still to upload; %d already rendered locally."
                     % (len(todo), len(ready)))
        printer.line("  Upload at: https://github.com/<repo>/settings  ->  Social preview")

    if args.json:
        _write(args.json, audit_module.to_json(results))
        printer.ok("wrote %s" % (args.json,))
    if args.markdown:
        _write(args.markdown, audit_module.to_markdown(results))
        printer.ok("wrote %s" % (args.markdown,))

    if counts[audit_module.STATUS_ERROR]:
        return 2
    if args.strict and counts[audit_module.STATUS_DEFAULT]:
        return 1
    return 0


def command_gallery(args: argparse.Namespace) -> int:
    printer = Printer(quiet=args.quiet)
    manifest = config.load(args.manifest)
    lock_path = os.path.join(os.path.dirname(os.path.abspath(args.manifest)),
                             render_module.LOCK_FILENAME)
    written, warnings = gallery_module.build(
        manifest, render_module.load_lock(lock_path), args.output, args.site, args.raw_base
    )
    for warning in warnings:
        printer.warn(warning)
    for path in written:
        printer.ok("wrote %s" % (path,))
    printer.line()
    printer.line("The site is self-contained: open %s in a browser, or publish the"
                 % (os.path.join(args.site, "index.html"),))
    printer.line("whole directory. It makes no third-party requests.")
    return 0


def command_import(args: argparse.Namespace) -> int:
    printer = Printer(quiet=args.quiet)
    printer.line("Reading public repositories for %s ..." % (args.owner,))
    fetched = importer_module.fetch(args.owner, args.timeout, args.include_forks)

    if os.path.exists(args.manifest):
        import json as _json
        with open(args.manifest, "r", encoding="utf-8") as handle:
            document = _json.load(handle)
        existing = document.get("repositories") or []
    else:
        document = config.starter(args.owner)
        existing = []
        document["repositories"] = []

    merged = importer_module.merge(existing, fetched)
    counts = importer_module.summarise(existing, merged)
    document["repositories"] = merged

    printer.line()
    printer.ok(
        "%d repositories: %d new, %d already present"
        % (counts["total"], counts["added"], counts["kept"])
    )
    if args.dry_run:
        printer.line("Dry run - %s was not written." % (args.manifest,))
        return 0
    config.write(args.manifest, document)
    printer.ok("wrote %s" % (args.manifest,))
    return 0


def command_doctor(args: argparse.Namespace) -> int:
    printer = Printer(quiet=args.quiet)
    printer.heading("Rasterisers")
    found = raster.available()
    if found:
        for name in sorted(found):
            printer.ok("%-14s %s" % (name, found[name]))
    else:
        printer.fail("none found - install librsvg (rsvg-convert) or resvg")

    printer.heading("Glyph packs")
    from .typography import Face
    for name in (templates.DISPLAY_FACE, templates.BODY_FACE):
        try:
            face = Face.load(name)
        except GspgError as failure:
            printer.fail("%-24s %s" % (name, failure))
            continue
        printer.ok(
            "%-24s %d glyphs, %d kern pairs, %d upem"
            % (name, face.glyph_count, face.kern_pair_count, face.units_per_em)
        )

    printer.heading("Environment")
    printer.line("  python      %s" % (sys.version.split()[0],))
    printer.line("  gspg        %s" % (__version__,))
    printer.line("  render epoch %d" % (render_module.RENDER_EPOCH,))
    printer.line("  assets      %s" % (render_module.asset_fingerprint(),))
    printer.line()
    printer.line("Rendering needs no network. `gspg audit` does, and uses no credentials.")
    return 0 if found else 1


def command_list(args: argparse.Namespace) -> int:
    printer = Printer(quiet=args.quiet)
    printer.heading("Background fields")
    printer.line("  Every card uses %s unless a repository overrides it."
                 % (model_module.DEFAULT_PATTERN,))
    printer.line()
    for name in patterns.names():
        doc = (patterns.PATTERNS[name].__doc__ or "").strip().split("\n")[0]
        printer.line("  %-16s %s" % (name, doc))
    printer.heading("Accents")
    from .palette import ACCENT_HUES
    printer.line("  " + ", ".join(sorted(ACCENT_HUES)))
    return 0


def command_init(args: argparse.Namespace) -> int:
    printer = Printer(quiet=args.quiet)
    if os.path.exists(args.manifest) and not args.force:
        error("%s already exists; pass --force to overwrite" % (args.manifest,))
        return 1
    config.write(args.manifest, config.starter(args.owner))
    printer.ok("wrote %s" % (args.manifest,))
    printer.line("Next: edit it, then run `gspg build`.")
    return 0


def _write(path: str, content: str) -> None:
    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    if not getattr(args, "handler", None):
        parser.print_help()
        return 2
    try:
        return args.handler(args)
    except GspgError as failure:
        error(str(failure))
        return 1
    except KeyboardInterrupt:
        error("interrupted")
        return 130
