# Validation — 2026-11-09

Environments: macOS with Python 3.13.13, and GitHub-hosted Windows with Python 3.11 and 3.13. Windows is the primary deployment platform.

Passed:

- 17 unit/integration tests (`python -m unittest discover -s tests -v`).
- Real backend used against synthetic offline HTTP responses: manifest parsing, chunk decoding, Unicode file assembly, SHA-1 verification, rejection of corrupted data, successful retry.
- Pagination encoding, duplicate cursors, page bounds, atomic sync, preserved tags and credentials removed from catalog metadata.
- Ambiguous titles, unsupported engine versions, fixture/live separation, offline dry runs, protected output folders, download receipts, queue claims/retries, Windows filename checks.
- Ruff static checks and formatting; Python compileall.
- Skill frontmatter/content validation.
- Wheel built and installed into a clean virtual environment. Command invoked by name on PATH from `/private/tmp`, outside the source folder.
- Installed `--help`, `--json doctor`, fixture sync, Chinese tags, pagination and download dry run.
- Installed `library sync` without login returns structured `auth_required` and exit code 4, without opening a browser or prompting.
- Original upstream wheel and source archive checked against PyPI SHA-256 release metadata.
- Windows CI: all 17 tests on both Python versions, package build, offline-wheel installer, installation path containing spaces, user PATH setup, Skill installation and installed-command execution outside the source directory.

Not yet verified:

- Live Epic authentication and encrypted Windows session round trips with the user's account.
- The user's actual catalog, CDN connectivity and real asset download.
- HTTP compatibility with future changes to Fab's undocumented endpoints.

Windows CI passed: https://github.com/200410080379/fab-library/actions/runs/42580101755

The tested source commit is `a5303f1997eea745034bdb29a2930de8bac61f3d`; the subsequent publication update changes documentation only. All demonstration resource records are synthetic.
