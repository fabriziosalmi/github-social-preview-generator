# Security

## Reporting

Report a vulnerability privately through GitHub's
[security advisory](https://github.com/fabriziosalmi/github-social-preview-generator/security/advisories/new)
form. Please do not open a public issue first.

## What this software does and does not do

It reads a JSON manifest, writes SVG and PNG files, and runs one external
program — the SVG rasteriser you have installed — on files it just wrote.

* **No runtime dependencies.** Standard library only, so there is no dependency
  tree to compromise.
* **No credentials.** Nothing here reads a token, an environment secret or a
  credential file. The CI workflows use a read-only default token they never
  touch, and the Pages job uses its own short-lived OIDC token.
* **No network at render time.** `build`, `preview`, `gallery`, `list`,
  `doctor` and `init` open no socket; the test suite enforces this by making
  `socket` raise. `audit` and `import` read public GitHub pages
  unauthenticated. `tools/vendor_fonts.py` fetches the pinned font archives and
  verifies their SHA-256 before extracting anything.
* **No filters, scripts or external references in the output.** Generated SVG
  contains only shapes, paths and gradients; the published gallery loads
  nothing off-origin and runs no script.

## Trust boundaries

The manifest is treated as trusted input: it is your file. All string values
that reach the output are XML-escaped, and the JSON loader rejects unknown
fields rather than ignoring them, but a manifest you did not write deserves the
same scrutiny as any other configuration file.

Rendering an untrusted manifest cannot execute code, but it can name an
arbitrary output path via the repository slug — which is constrained to
`[A-Za-z0-9._-]` on both halves precisely so it cannot escape the output
directory.
