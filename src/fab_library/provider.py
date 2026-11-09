"""Narrow adapter to pinned fab-downloader 1.3.0. No purchases or account-library writes."""

from __future__ import annotations

import getpass
import json
from pathlib import Path
import sys
from urllib.parse import quote, urlencode
import webbrowser

from .catalog import Failure, normalize_item


class Provider:
    def __init__(self, home, http=None):
        import importlib.metadata

        try:
            version = importlib.metadata.version("fab-downloader")
        except importlib.metadata.PackageNotFoundError:
            raise Failure(
                "dependency_missing",
                "Install the bundled fab-downloader 1.3.0 wheel.",
                2,
            ) from None
        if version != "1.3.0":
            raise Failure(
                "backend_version_mismatch",
                "This adapter requires fab-downloader 1.3.0. Re-run the installer.",
                2,
            )
        from fabdl.auth import SessionStore
        from fabdl.net import HttpClient

        self.http = http or HttpClient(timeout=45)
        self.store = SessionStore(Path(home) / "session.bin")

    def status(self):
        saved = self.store.load()
        return {
            "token_available": bool(saved and saved.get("access_token")),
            "auth_source": "local_session" if saved else "missing",
            "account": {
                "id": saved.get("account_id"),
                "display_name": saved.get("displayName", ""),
            }
            if saved
            else None,
            "expires_at": saved.get("expires_at") if saved else None,
            "storage": "Windows DPAPI"
            if sys.platform == "win32"
            else "user-only file (unencrypted)",
            "verified_online": False,
        }

    def session(self):
        from fabdl.auth import load_session

        session = load_session(self.http, self.store)
        if session is None:
            raise Failure(
                "auth_required",
                "Sign in on the target computer with: fab-library auth login",
                4,
            )
        return session

    def login(self, code_stdin=False, print_url=False):
        from fabdl.auth import LOGIN_URL, sign_in

        if print_url:
            return {
                "login_url": LOGIN_URL,
                "next": "Complete Epic login, then run auth login --code-stdin locally.",
            }
        if code_stdin:
            code = sys.stdin.read(65536)
        else:
            if not sys.stdin.isatty():
                raise Failure(
                    "interactive_login_required",
                    "Run auth login in an interactive terminal, or pass the code through --code-stdin.",
                    4,
                )
            print(
                "Sign in on Epic's page, then paste authorizationCode here (input is hidden).",
                file=sys.stderr,
            )
            print(LOGIN_URL, file=sys.stderr)
            webbrowser.open(LOGIN_URL)
            code = getpass.getpass("Authorization code: ", stream=sys.stderr)
        session = sign_in(self.http, self.store, code)
        return {
            "authenticated": True,
            "account": {"id": session.account_id, "display_name": session.display_name},
        }

    def logout(self):
        had_session = self.store.path.exists()
        self.store.clear()
        return {"local_session_removed": had_session, "remote_session_revoked": False}

    def library(self, max_pages=1000, target_id=None):
        session = self.session()
        return self.fetch_library(self.http, session, max_pages, target_id), session

    @staticmethod
    def fetch_library(http, session, max_pages=1000, target_id=None):
        cursor, seen, items, seen_ids = None, set(), [], set()
        for _ in range(max_pages):
            query = {"count": 100}
            if cursor is not None:
                query["cursor"] = cursor
            url = (
                "https://www.fab.com/e/accounts/"
                + quote(session.account_id, safe="")
                + "/ue/library?"
                + urlencode(query)
            )
            page = http.json(
                "GET", url, headers={"Authorization": "Bearer " + session.access_token}
            )
            if not isinstance(page, dict) or not isinstance(page.get("results"), list):
                raise Failure(
                    "provider_schema_changed",
                    "Fab returned an unexpected library format; previous catalog kept.",
                    5,
                )
            for raw in page["results"]:
                clean = normalize_item(raw)
                if clean["assetId"] in seen_ids:
                    raise Failure(
                        "unstable_pagination",
                        "Library changed while paging; retry sync. Previous catalog kept.",
                        5,
                    )
                seen_ids.add(clean["assetId"])
                items.append(clean)
                if target_id and clean["assetId"] == target_id:
                    return [clean]
            cursors = page.get("cursors") or {}
            if not isinstance(cursors, dict):
                raise Failure(
                    "provider_schema_changed", "Unexpected pagination format.", 5
                )
            cursor = cursors.get("next")
            if not cursor:
                return items if target_id is None else []
            if not isinstance(cursor, str) or cursor in seen:
                raise Failure(
                    "unstable_pagination",
                    "Fab repeated a pagination cursor; previous catalog kept.",
                    5,
                )
            seen.add(cursor)
        raise Failure(
            "page_limit",
            "Sync exceeded --max-pages; previous catalog kept. Increase the bound explicitly.",
            5,
        )

    def download(self, raw, artifact_id, platform, out, account_id):
        from fabdl.fab import parse_library_item, request_build
        from fabdl.workflow import fetch_manifest
        from fabdl.download import download_build

        session = self.session()
        if session.account_id != account_id:
            raise Failure(
                "account_changed",
                "Signed-in account differs from the catalog. Run library sync.",
                4,
            )
        current = self.fetch_library(self.http, session, target_id=raw["assetId"])
        if not current:
            raise Failure(
                "not_owned", "Asset is not in the current account's UE library.", 4
            )
        item = parse_library_item(current[0])
        versions = [v for v in item.project_versions if v.artifact_id == artifact_id]
        if len(versions) != 1:
            raise Failure(
                "catalog_stale",
                "Asset build changed; run library sync and make a new download plan.",
                5,
            )
        build = request_build(self.http, session, item, versions[0], platform)
        manifest, sources = fetch_manifest(self.http, build.sources)
        # Validate all remote file paths before the backend creates its staging directory.
        self.validate_manifest(manifest, out)

        def progress(done, total):
            if done == total or done % 100 == 0:
                print(
                    json.dumps(
                        {
                            "event": "download_progress",
                            "chunks_done": done,
                            "chunks_total": total,
                        }
                    ),
                    file=sys.stderr,
                )

        paths = download_build(
            manifest,
            [s.base_url for s in sources],
            out,
            lambda url: self.http.request("GET", url),
            report=progress,
        )
        return {
            "build_version": build.build_version,
            "files": [str(p.relative_to(out)) for p in paths],
            "file_count": len(paths),
            "bytes": sum(f.size for f in manifest.files),
        }

    @staticmethod
    def validate_manifest(manifest, out):
        import re
        from pathlib import PureWindowsPath
        from fabdl.download import safe_relative_path

        names = set()
        for entry in manifest.files:
            relative = safe_relative_path(entry.filename)
            parts = relative.parts
            if any(
                p.endswith((".", " "))
                or re.search(r'[<>"|?*\x00-\x1f]', p)
                or re.match(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", p, re.I)
                for p in parts
            ):
                raise Failure(
                    "unsafe_manifest",
                    "Manifest contains an unsafe Windows filename.",
                    5,
                )
            if (
                PureWindowsPath(str(relative)).is_absolute()
                or relative.as_posix().casefold() in names
            ):
                raise Failure(
                    "unsafe_manifest",
                    "Manifest contains a duplicate or absolute Windows path.",
                    5,
                )
            names.add(relative.as_posix().casefold())
        if ".fab-library-receipt.json" in names:
            raise Failure(
                "unsafe_manifest", "Manifest conflicts with the download receipt.", 5
            )
