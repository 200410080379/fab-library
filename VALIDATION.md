# Validation — v0.3.0 — 2026-11-09

Local macOS validation:

- 46 unit/integration tests passed, including Windows-backend compatibility, native Mac request mapping, exact zero-price offer/Personal/Professional binding, unknown-tier rejection, OAuth exchange before Fab SSO, pagination and receipts.
- Swift/WebKit helper compiled for Apple silicon with macOS 14 deployment target. Version and request-allowlist self-tests passed.
- CLI installed on PATH using uv. The native app helper and companion Skill are installed in user-owned locations.
- Real Epic OAuth and Fab web-session login verified locally. Short-lived OAuth codes are exchanged before the second login leg.
- Real official animation/free category search, complete pagination, pricing and ownership reads verified. No session cookies or tokens were copied into the repository.
- Live zero-price Personal-tier claims and post-claim ownership checks verified on macOS. Actual asset file downloads remain unverified. No paid checkout API exists.

The native store helper permits only specific Fab read endpoints and the exact-offer add-to-library endpoint. It does not accept arbitrary JavaScript/hosts, export browser cookies, solve challenges or weaken TLS verification. WebKit uses an isolated persistent profile. OAuth session files are user-readable only and are not encrypted on macOS.

New Windows and macOS CI runs validate the 0.3.0 code. Prior 0.2.0 Windows validation: https://github.com/200410080379/fab-library/actions/runs/42589142044.
