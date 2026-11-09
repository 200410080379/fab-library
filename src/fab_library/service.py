"""Download plans and receipts. Dry runs are offline and never create output folders."""

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path

from .catalog import Failure, engine_version, now

RECEIPT = ".fab-library-receipt.json"


def plan_download(catalog, asset_id, engine, platform, out):
    raw = catalog.raw(asset_id)
    engine = engine_version(engine)
    matches = {
        p["artifactId"] for p in raw["projectVersions"] if engine in p["engineVersions"]
    }
    if len(matches) != 1:
        raise Failure(
            "unsupported_or_ambiguous_version",
            "Select one supported engine version with an unambiguous artifact.",
            details={"artifacts": raw["projectVersions"]},
        )
    target = Path(out).expanduser().resolve()
    if target == target.parent:
        raise Failure(
            "invalid_destination", "Choose a new asset folder, not a drive root."
        )
    return {
        "asset_id": asset_id,
        "title": raw["title"],
        "engine": engine,
        "platform": platform,
        "artifact_id": next(iter(matches)),
        "out": str(target),
        "destination_exists": target.exists(),
        "source": catalog.meta().get("source"),
        "network_required": True,
        "action": "download_to_new_folder",
        "installs_into_project": False,
    }


@contextmanager
def destination_lock(home, out):
    root = Path(home) / "locks"
    root.mkdir(parents=True, exist_ok=True)
    # Windows paths are case-insensitive; casefold also avoids duplicate work on default macOS.
    path = root / (hashlib.sha256(str(out).casefold().encode()).hexdigest() + ".lock")
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise Failure(
            "download_locked",
            "Another download owns this destination. If a process crashed, confirm it stopped before removing this lock.",
            6,
            {"lock_file": str(path)},
        ) from None
    try:
        with os.fdopen(fd, "w") as f:
            json.dump({"pid": os.getpid(), "out": str(out), "started": now()}, f)
        yield
    finally:
        path.unlink(missing_ok=True)


def receipt_matches(path, plan, account_id):
    try:
        record = json.loads((path / RECEIPT).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if (
        not all(
            record.get(k) == plan[k]
            for k in ("asset_id", "engine", "platform", "artifact_id")
        )
        or record.get("account_id") != account_id
    ):
        return False
    files = record.get("file_sizes")
    if not isinstance(files, dict) or not files:
        return False
    for name, size in files.items():
        target = path / name
        try:
            if (
                not target.resolve().is_relative_to(path)
                or not target.is_file()
                or target.stat().st_size != size
            ):
                return False
        except (OSError, ValueError):
            return False
    return record


def download(catalog, provider, plan):
    meta = catalog.meta()
    if meta.get("source") != "live":
        raise Failure(
            "offline_catalog",
            "Fixture catalogs support search and dry runs only. Sign in and run library sync before downloading.",
            4,
        )
    account_id = meta.get("account", {}).get("id")
    if not account_id:
        raise Failure(
            "auth_required", "Catalog has no account identity; run library sync.", 4
        )
    out = Path(plan["out"])
    with destination_lock(catalog.home, out):
        if out.exists():
            receipt = receipt_matches(out, plan, account_id)
            if not receipt:
                raise Failure(
                    "destination_exists",
                    "Output exists and is not a complete matching download. Choose a new folder; existing files were kept.",
                    6,
                )
            result = {
                **plan,
                **{k: receipt[k] for k in ("build_version", "file_count", "bytes")},
                "skipped": True,
            }
            catalog.record_download(result, account_id)
            return result
        # A foreign staging tree can make the backend delete unrelated data. Only reuse ours.
        staging = out.with_name(out.name + ".fabdl-partial")
        owner = out.with_name(out.name + ".fab-library-owner.json")
        identity = {
            k: plan[k] for k in ("asset_id", "engine", "platform", "artifact_id")
        }
        identity["account_id"] = account_id
        if staging.exists() or owner.exists():
            try:
                known = json.loads(owner.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                known = None
            if known != identity or staging.is_symlink():
                raise Failure(
                    "staging_conflict",
                    "Existing partial download belongs to another plan; choose a new output folder.",
                    6,
                )
        out.parent.mkdir(parents=True, exist_ok=True)
        owner.write_text(json.dumps(identity), encoding="utf-8")
        result = provider.download(
            catalog.raw(plan["asset_id"]),
            plan["artifact_id"],
            plan["platform"],
            out,
            account_id,
        )
        file_sizes = {name: (out / name).stat().st_size for name in result.pop("files")}
        record = {**identity, **result, "file_sizes": file_sizes, "completed_at": now()}
        temporary = out / (RECEIPT + ".tmp")
        temporary.write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(out / RECEIPT)
        owner.unlink(missing_ok=True)
        final = {**plan, **result, "skipped": False}
        catalog.record_download(final, account_id)
        return final
