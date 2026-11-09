from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import sys
from urllib.parse import quote

from . import __version__
from .catalog import Catalog, Failure, engine_version, state_home
from .provider import Provider
from .service import download, plan_download
from .store import Store, Shopping
from .freebies import create_plan, apply_plan


class Parser(argparse.ArgumentParser):
    def error(self, message):
        # Do not echo arbitrary argument values (they could accidentally be credentials).
        raise Failure(
            "invalid_arguments",
            "Invalid or missing command arguments. Run the command with --help.",
        )


def bounded(value):
    number = int(value)
    if not 1 <= number <= 1000:
        raise argparse.ArgumentTypeError("Use a number from 1 to 1000.")
    return number


def offset_value(value):
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("Offset must be non-negative.")
    return number


def parser():
    p = Parser(
        prog="fab-library",
        description="Manage your owned Fab UE library: discover, resolve, tag and download. Windows-first.",
    )
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument(
        "--json",
        action="store_true",
        help="Emit stable JSON envelopes (also accepted after subcommands)",
    )
    p.add_argument(
        "--home",
        help="State directory; default FAB_LIBRARY_HOME or per-user application data",
    )
    s = p.add_subparsers(dest="command", required=True)
    d = s.add_parser(
        "doctor", help="Inspect runtime, catalog and auth without network by default"
    )
    d.add_argument(
        "--online",
        action="store_true",
        help="Check the authenticated Fab library endpoint",
    )
    a = s.add_parser(
        "auth", help="Sign in locally to Epic; credentials never appear in JSON output"
    ).add_subparsers(dest="action", required=True)
    login = a.add_parser(
        "login", help="Open Epic login and read a hidden authorization code"
    )
    login.add_argument(
        "--code-stdin",
        action="store_true",
        help="Read authorization code/JSON from stdin instead of prompting",
    )
    login.add_argument(
        "--print-url",
        action="store_true",
        help="Print only the official Epic login URL",
    )
    a.add_parser("status", help="Inspect local session presence, without refreshing it")
    a.add_parser(
        "logout",
        help="Remove this tool's local session; keep catalog and downloaded files",
    )
    a = s.add_parser(
        "library", help="Sync or search your owned UE resource catalog"
    ).add_subparsers(dest="action", required=True)
    sync = a.add_parser(
        "sync", help="Atomically refresh the full owned library; tags are preserved"
    )
    sync.add_argument("--max-pages", type=bounded, default=1000)
    sync.add_argument(
        "--fixture",
        type=Path,
        help="Import a synthetic/offline snapshot; downloads stay disabled",
    )
    listing = a.add_parser("list", help="Search the local catalog without network")
    listing.add_argument("--query", default="")
    listing.add_argument("--tag")
    listing.add_argument("--engine")
    listing.add_argument("--limit", type=bounded, default=25)
    listing.add_argument("--offset", type=offset_value, default=0)
    export = a.add_parser(
        "export", help="Export a sanitized catalog snapshot to a NEW file"
    )
    export.add_argument("--out", required=True, type=Path)
    a = s.add_parser(
        "assets", help="Resolve names to IDs or inspect one exact asset"
    ).add_subparsers(dest="action", required=True)
    resolve = a.add_parser(
        "resolve", help="Resolve a title/keyword or ID; ambiguity returns candidates"
    )
    resolve.add_argument("query")
    for action in ("show", "raw"):
        show = a.add_parser(
            action,
            help="Inspect exact ID"
            if action == "show"
            else "Read-only escape hatch: normalized provider metadata from the local snapshot",
        )
        show.add_argument("id")
    a = s.add_parser(
        "tags", help="Add or remove local Chinese/English tags"
    ).add_subparsers(dest="action", required=True)
    for action in ("add", "remove"):
        t = a.add_parser(action)
        t.add_argument("id")
        t.add_argument("tag")
    d = s.add_parser(
        "download",
        help="Download one exact owned ID/version to a new folder, or preview offline",
    )
    download_args(d)
    a = s.add_parser(
        "queue", help="Manage a persistent, serial download queue"
    ).add_subparsers(dest="action", required=True)
    add = a.add_parser("add", help="Queue one exact download; no network is used")
    download_args(add)
    qlist = a.add_parser("list", help="Inspect bounded queue entries")
    qlist.add_argument("--limit", type=bounded, default=25)
    qlist.add_argument("--offset", type=offset_value, default=0)
    run = a.add_parser(
        "run", help="Run pending jobs serially; stop on the first failure"
    )
    run.add_argument("--limit", type=bounded, default=1)
    run.add_argument("--dry-run", action="store_true")
    retry = a.add_parser(
        "retry", help="Requeue a failed/interrupted job for a resumable retry"
    )
    retry.add_argument("id")
    d = s.add_parser(
        "downloads", help="List previously completed downloads (local metadata)"
    )
    d.add_argument("--limit", type=bounded, default=25)
    d.add_argument("--offset", type=offset_value, default=0)
    a = s.add_parser(
        "store",
        help="Search Fab, inspect live offers and claim explicitly free listings",
    ).add_subparsers(dest="action", required=True)
    a.add_parser("doctor", help="Check the optional FabCLI store backend and session")
    a.add_parser("login", help="Open FabCLI's Epic/Fab sign-in window on this computer")
    a.add_parser("account", help="Show the store account without email or credentials")
    for command in ("search", "free"):
        search = a.add_parser(
            command, help="Read one bounded page of UE marketplace listings"
        )
        search.add_argument("--query", default="")
        search.add_argument("--limit", type=bounded, default=20)
        search.add_argument("--cursor")
        filters = search.add_mutually_exclusive_group()
        filters.add_argument("--free", action="store_true")
        filters.add_argument("--limited-time", action="store_true")
    q = a.add_parser(
        "quote", help="Read current prices, ownership and official purchase URL"
    )
    q.add_argument("listing")
    c = a.add_parser(
        "claim",
        help="Preview a zero-price claim; --execute submits only reviewed free listings",
    )
    c.add_argument("listing")
    mode = c.add_mutually_exclusive_group()
    mode.add_argument("--execute", action="store_true")
    mode.add_argument(
        "--dry-run", action="store_true", help="Explicit preview (the default)"
    )
    c.add_argument(
        "--expected-account", help="Store account ID from the reviewed preview"
    )
    c.add_argument(
        "--expected-offer",
        help="Selected zero-price offer ID from the reviewed preview",
    )
    a = s.add_parser(
        "shopping",
        help="Local priced-resource shortlist and official links; no cart or payment writes",
    ).add_subparsers(dest="action", required=True)
    add = a.add_parser("add", help="Save one exact offer quote in the local shortlist")
    add.add_argument("listing")
    add.add_argument("--offer", required=True)
    add.add_argument("--dry-run", action="store_true")
    for command in ("list", "links"):
        ls = a.add_parser(
            command, help="Read timestamped local quotes and official listing links"
        )
        ls.add_argument("--limit", type=bounded, default=25)
        ls.add_argument("--offset", type=offset_value, default=0)
    remove = a.add_parser("remove", help="Remove a local shortlist item")
    remove.add_argument("listing")
    a = s.add_parser(
        "freebies",
        help="Preview and apply a finite list of current free promotions; no scheduling",
    ).add_subparsers(dest="action", required=True)
    plan = a.add_parser(
        "plan", help="Save a reviewable, 30-minute claim plan without claiming anything"
    )
    plan.add_argument("--out", required=True, type=Path)
    plan.add_argument("--limit", type=bounded, default=20)
    plan.add_argument(
        "--listing",
        action="append",
        help="Exact Fab listing UUID/URL; repeat for an explicit list instead of discovery",
    )
    apply = a.add_parser(
        "apply",
        help="Claim only this plan's exact items, with fresh price/account checks",
    )
    apply.add_argument("plan_file", type=Path)
    apply.add_argument("--dry-run", action="store_true")
    return p


def download_args(p):
    p.add_argument("id", help="Stable asset ID returned by assets resolve")
    p.add_argument("--engine", required=True, help="Explicit UE version, e.g. 5.8")
    p.add_argument("--platform", choices=("Windows", "Mac", "Linux"), default="Windows")
    p.add_argument(
        "--out",
        required=True,
        help="Destination for this single asset; use a new subfolder",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Only return the plan; no network or output-file writes",
    )


def paginated(rows, limit, offset):
    return {
        "items": rows[offset : offset + limit],
        "total": len(rows),
        "limit": limit,
        "offset": offset,
        "next_offset": offset + limit if offset + limit < len(rows) else None,
    }


def dispatch(args, home, provider_factory=Provider, store_factory=Store):
    if args.command == "freebies":
        store = store_factory(home)
        if args.action == "plan":
            return create_plan(store, args.out, args.limit, args.listing)
        return apply_plan(store, home, args.plan_file, args.dry_run)
    if args.command in {"store", "shopping"}:
        store = store_factory(home)
        if args.command == "shopping":
            shopping = Shopping(home)
            try:
                if args.action == "add":
                    return shopping.add(
                        store.quote(args.listing), args.offer, args.dry_run
                    )
                if args.action == "remove":
                    return shopping.remove(args.listing)
                return shopping.list(args.limit, args.offset)
            finally:
                shopping.db.close()
        if args.action == "doctor":
            return store.doctor()
        if args.action == "login":
            store.client.call("auth", "login")
            return {"account": store.account(), "session": store.doctor()}
        if args.action == "account":
            return store.account()
        if args.action in {"search", "free"}:
            if args.limit > 100:
                raise Failure(
                    "invalid_limit",
                    "Store pages support --limit 1 to 100; continue with --cursor.",
                )
            return store.search(
                args.query,
                free=args.free or (args.action == "free" and not args.limited_time),
                limited_time=args.limited_time,
                limit=args.limit,
                cursor=args.cursor,
            )
        if args.action == "quote":
            return store.quote(args.listing)
        return store.claim(
            args.listing, args.execute, args.expected_account, args.expected_offer
        )
    provider = provider_factory(home)
    if args.command == "auth":
        if args.action == "login":
            return provider.login(args.code_stdin, args.print_url)
        return provider.status() if args.action == "status" else provider.logout()
    catalog = Catalog(home)
    try:
        if args.command == "doctor":
            meta, status = catalog.meta(), provider.status()
            fixture = meta.get("source") == "fixture"
            try:
                backend_version = importlib.metadata.version("fab-downloader")
            except importlib.metadata.PackageNotFoundError:
                backend_version = None
            result = {
                "version": __version__,
                "backend": {
                    "name": "fab-downloader",
                    "version": backend_version,
                    "expected": "1.3.0",
                },
                "os": sys.platform,
                "python": sys.version.split()[0],
                "state_directory": str(home),
                "default_download_platform": "Windows",
                "mode": "fixture" if fixture else "live",
                "auth_required": not fixture,
                "auth": status,
                "catalog": {"count": len(catalog.find()), **meta},
                "network_checked": False,
                "next_step": "library sync"
                if status["token_available"]
                else "auth login",
            }
            if fixture:
                result["next_step"] = "library list; download --dry-run"
            if args.online:
                session = provider.session()
                page = provider.http.json(
                    "GET",
                    "https://www.fab.com/e/accounts/"
                    + quote(session.account_id, safe="")
                    + "/ue/library?count=1",
                    headers={"Authorization": "Bearer " + session.access_token},
                )
                if not isinstance(page, dict) or not isinstance(
                    page.get("results"), list
                ):
                    raise Failure(
                        "provider_schema_changed",
                        "Fab returned an unexpected library format.",
                        5,
                    )
                result["network_checked"] = True
                result["auth"]["verified_online"] = True
            return result
        if args.command == "library":
            if args.action == "sync":
                if args.fixture:
                    data = json.loads(args.fixture.read_text(encoding="utf-8"))
                    if (
                        not isinstance(data, dict)
                        or data.get("schema_version") != 1
                        or not isinstance(data.get("items"), list)
                    ):
                        raise Failure(
                            "invalid_fixture",
                            "Expected {schema_version:1, items:[...]}.",
                        )
                    return catalog.sync(
                        data["items"],
                        {"id": "fixture", "display_name": "Offline fixture"},
                        "fixture",
                    )
                items, session = provider.library(args.max_pages)
                return catalog.sync(
                    items,
                    {"id": session.account_id, "display_name": session.display_name},
                    "live",
                )
            if args.action == "list":
                items = catalog.find(
                    args.query,
                    args.tag,
                    engine_version(args.engine) if args.engine else None,
                )
                return paginated(items, args.limit, args.offset)
            items = [catalog.raw(x["id"]) for x in catalog.find()]
            with args.out.expanduser().open("x", encoding="utf-8") as f:
                json.dump(
                    {"schema_version": 1, "items": items},
                    f,
                    ensure_ascii=False,
                    indent=2,
                )
            return {
                "out": str(args.out.resolve()),
                "count": len(items),
                "contains_credentials": False,
            }
        if args.command == "assets":
            if args.action == "resolve":
                return catalog.resolve(args.query)
            raw = catalog.raw(args.id)
            return raw if args.action == "raw" else catalog.describe(raw)
        if args.command == "tags":
            return catalog.tag(args.id, args.tag, args.action == "remove")
        if args.command == "download" or (
            args.command == "queue" and args.action == "add"
        ):
            plan = plan_download(catalog, args.id, args.engine, args.platform, args.out)
            if args.dry_run:
                return {"dry_run": True, **plan}
            return (
                catalog.add_job(plan)
                if args.command == "queue"
                else download(catalog, provider, plan)
            )
        if args.command == "queue":
            if args.action == "list":
                return paginated(
                    [
                        dict(r)
                        for r in catalog.db.execute(
                            "SELECT * FROM jobs ORDER BY created_at,id"
                        )
                    ],
                    args.limit,
                    args.offset,
                )
            if args.action == "retry":
                return catalog.retry(args.id)
            if args.dry_run:
                return {
                    "dry_run": True,
                    "jobs": [
                        dict(r)
                        for r in catalog.db.execute(
                            "SELECT * FROM jobs WHERE status='pending' ORDER BY created_at,id LIMIT ?",
                            (args.limit,),
                        )
                    ],
                }
            completed = []
            for _ in range(args.limit):
                job = catalog.claim_job()
                if job is None:
                    break
                try:
                    if catalog.meta().get("account", {}).get("id") != job["account_id"]:
                        raise Failure(
                            "account_changed",
                            "Queued job belongs to a different catalog account.",
                            4,
                        )
                    plan = plan_download(
                        catalog,
                        job["asset_id"],
                        job["engine"],
                        job["platform"],
                        job["path"],
                    )
                    result = download(catalog, provider, plan)
                    catalog.job_status(job["id"], "completed")
                    completed.append({"job_id": job["id"], **result})
                except KeyboardInterrupt:
                    catalog.job_status(job["id"], "interrupted", "interrupted")
                    raise
                except Exception as exc:
                    error = classify(exc)
                    catalog.job_status(job["id"], "failed", error.kind)
                    error.details = {
                        "failed_job_id": job["id"],
                        "completed_count": len(completed),
                    }
                    raise error from None
            return {"completed": completed, "count": len(completed)}
        if args.command == "downloads":
            return paginated(
                [
                    dict(r)
                    for r in catalog.db.execute(
                        "SELECT * FROM downloads ORDER BY completed_at DESC,path"
                    )
                ],
                args.limit,
                args.offset,
            )
        raise Failure("invalid_command", "Unknown command.")
    finally:
        catalog.db.close()


def classify(error):
    if isinstance(error, Failure):
        return error
    if isinstance(error, ImportError):
        return Failure(
            "dependency_missing",
            "A required package is missing. Re-run the installer.",
            2,
        )
    if isinstance(error, FileExistsError):
        return Failure(
            "file_exists", "Output file already exists; choose a new path.", 6
        )
    from fabdl.auth import AuthError
    from fabdl.net import HttpError
    from fabdl.workflow import EXPECTED_ERRORS

    if isinstance(error, AuthError):
        return Failure(
            "auth_failed",
            "Epic login or refresh failed. Run auth login locally again.",
            4,
        )
    if isinstance(error, HttpError):
        return Failure(
            "http_error",
            f"Provider request failed with HTTP {error.status}. No credentials were logged.",
            5,
        )
    if isinstance(error, EXPECTED_ERRORS):
        return Failure(
            "provider_error",
            "Provider download/network operation failed. Partial downloads can be retried with the same destination.",
            5,
        )
    if isinstance(error, (OSError, ValueError, TypeError, KeyError)):
        return Failure(
            "input_or_storage_error",
            "Input data or a local path could not be read/written. Check the input format and destination.",
            2,
        )
    return Failure(
        "internal_error",
        "Unexpected tool error. No raw exception was printed because it may contain authentication data.",
        1,
    )


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    as_json = "--json" in argv
    # Permit the machine-output flag at any command depth.
    argv = [x for x in argv if x != "--json"]
    os.umask(0o077)
    try:
        args = parser().parse_args(argv)
        result = dispatch(args, state_home(args.home))
        payload, code = {"schema_version": 1, "ok": True, "data": result}, 0
    except KeyboardInterrupt:
        payload, code = (
            {
                "schema_version": 1,
                "ok": False,
                "error": {
                    "kind": "interrupted",
                    "message": "Stopped. Resume downloads with the same destination; check claim receipts and ownership before retrying claims.",
                },
            },
            130,
        )
    except Exception as exc:
        error = classify(exc)
        payload, code = (
            {
                "schema_version": 1,
                "ok": False,
                "error": {"kind": error.kind, "message": str(error)},
            },
            error.code,
        )
        if error.details is not None:
            payload["error"]["details"] = error.details
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        print(json.dumps(payload, ensure_ascii=False, indent=None if as_json else 2))
    except BrokenPipeError:
        return code
    return code
