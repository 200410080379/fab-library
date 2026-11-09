# Third-party dependency

- Name: Fab Downloader (`fab-downloader`)
- Author: Fobal
- Pinned version: 1.3.0
- Source: https://github.com/Fobal/fab-downloader
- Registry: https://pypi.org/project/fab-downloader/1.3.0/
- License: GPL-3.0-only; upstream copyright notices are retained in its package/source.
- Role: Epic OAuth and Windows DPAPI session storage, Fab manifest access/parsing, chunk download and file verification.

The release contains the original PyPI wheel and corresponding source distribution. Their SHA-256 hashes were checked against PyPI's release metadata when preparing this package; `SHA256SUMS.txt` lists the release bundle's files. The backend is not affiliated with or endorsed by Epic Games. This project has no official Fab API entitlement.

Fab Library does not execute the backend's GUI or CLI. It calls the pinned Python library through a narrow adapter, stores its own isolated session and catalog, and exposes no purchasing or free-asset claiming commands.
