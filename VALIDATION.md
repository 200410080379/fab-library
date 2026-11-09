# Validation — 2026-11-09

Environment: macOS, Python 3.13.13. Windows is the intended primary deployment platform.

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

Not yet verified:

- Windows PowerShell installer, Windows PATH/Skill discovery and DPAPI on a real Windows host.
- Live Epic authentication, the user's actual catalog, CDN connectivity and real asset download.
- HTTP compatibility with future changes to Fab's undocumented endpoints.

The included Windows GitHub Actions workflow has not been submitted or run. All demonstration resource records are synthetic.
