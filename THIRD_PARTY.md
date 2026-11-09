# Third-party dependency

- Name: Fab Downloader (`fab-downloader`)
- Author: Fobal
- Pinned version: 1.3.0
- Source: https://github.com/Fobal/fab-downloader
- Registry: https://pypi.org/project/fab-downloader/1.3.0/
- License: GPL-3.0-only; upstream copyright notices are retained in its package/source.
- Role: Epic OAuth and Windows DPAPI session storage, Fab manifest access/parsing, chunk download and file verification.

The release contains the original PyPI wheel and corresponding source distribution. Their SHA-256 hashes were checked against PyPI's release metadata when preparing this package; `SHA256SUMS.txt` lists the release bundle's files. The backend is not affiliated with or endorsed by Epic Games. This project has no official Fab API entitlement.

For downloads, Fab Library calls the pinned Python library through a narrow adapter and stores its own isolated session and catalog.

## Optional store backend (0.2.0)

- Name: FabCLI, author `zirklerite`, GPL-3.0-only.
- Source: https://github.com/zirklerite/FabCLI/tree/v0.1.0
- EGS API submodule inspected: https://github.com/zirklerite/egs-api-rs/tree/1bb7d37256ddaa4d160b4e9ab2ba1c14cbdc5113
- Version: 0.1.0, Windows x64 release.
- Release: https://github.com/zirklerite/FabCLI/releases/tag/v0.1.0
- Windows archive SHA-256: `67e0eb68d62589a3282a4af12085a40246625fa7c57ffdd5189d5814b5c0a383` (verified against GitHub release asset metadata).

The optional installer fetches the executable directly from the upstream release, verifies the pinned archive hash and copies upstream notices. The executable is not redistributed inside Fab Library's release archive. `FABCLI_TOKEN_PATH` isolates its token, web session and WebView storage under Fab Library's `store-session` directory.

The adapter permits search, listing/offer/ownership reads, explicit login and zero-price claims. Paid shopping is a local shortlist with official links; there are no cart/order/payment endpoints. Claim logic was checked against `src/cli/fab.rs` and serialization against `fab_search.rs`; the wrapper adds all-offers-zero checks, account/offer preconditions and post-claim ownership verification. The backend selects the first zero-price offer and does not expose reliable license tier labels.
