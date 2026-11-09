# Validation — v0.2.0 — 2026-11-09

Local validation: macOS, Python 3.13.13. Windows is the primary deployment platform.

Passed locally:

- All 37 unit/integration tests, Python compile checks and Ruff checks/formatting.
- Real download backend exercised against synthetic HTTP responses: manifest parsing, chunk decoding, Unicode files, SHA-1 verification, corrupted-data rejection and successful retry.
- Catalog pagination/atomicity, tag preservation, exact version resolution, download preview, protected existing files, receipts, queue behavior and Windows filename checks.
- Marketplace price changes, unknown/mixed prices, expired discounts, account/offer mismatches, already-owned skips, ownership verification and uncertain outcomes that must not be blindly retried.
- Paid shopping remains local; exact offer selection, separate currency totals and canonical official URLs.
- Finite promotion plans: no claims while planning or dry-running, only snapshot items are applied, expired plans/account changes stop execution, and partial failure preserves a receipt.
- Companion Skill validated. No scheduling/background automation is implemented.

Windows CI passed on Python 3.11 and 3.13: all 37 tests, wheel builds, the PowerShell installer, installation paths containing spaces, Skill installation, the pinned optional FabCLI binary and no-login store diagnostics.

Run: https://github.com/200410080379/fab-library/actions/runs/42589142044

Tested source: `b1fd37e95c4c7bd8e07a72fc4dca7b922603ae72`. Subsequent release updates change documentation only.

Not verified with a real Epic account:

- Interactive Epic/Fab login and encrypted session persistence.
- Real catalog queries, promotional eligibility, license selection, successful claims and CDN asset downloads.
- Future changes to undocumented Fab endpoints.

No live purchases or claims were made during development. All test listings/accounts are synthetic. v0.1.0's prior Windows validation remains available at https://github.com/200410080379/fab-library/actions/runs/42580101755.
