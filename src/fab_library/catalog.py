"""Local catalog. A completed sync is committed atomically; tags survive resyncs."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import uuid
from datetime import datetime, timezone


class Failure(Exception):
    def __init__(self, kind, message, code=2, details=None):
        super().__init__(message)
        self.kind, self.code, self.details = kind, code, details


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def state_home(value=None):
    if value or os.getenv("FAB_LIBRARY_HOME"):
        return Path(value or os.environ["FAB_LIBRARY_HOME"]).expanduser().resolve()
    if sys.platform == "win32":
        base = Path(os.getenv("LOCALAPPDATA", Path.home() / "AppData/Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library/Application Support"
    else:
        base = Path(os.getenv("XDG_DATA_HOME", Path.home() / ".local/share"))
    return base / "fab-library"


def engine_version(value):
    match = re.fullmatch(r"(?:UE_?)?(\d+)\.(\d+)", value.strip(), re.I)
    if not match:
        raise Failure("invalid_engine", "Use an engine version such as 5.8 or UE_5.8.")
    return f"UE_{int(match[1])}.{int(match[2])}"


def normalize_item(raw):
    """Whitelist catalog fields: never persist auth, cookies or signed download URLs."""
    if not isinstance(raw, dict):
        raise Failure("invalid_catalog", "Asset entries must be objects.")
    asset_id, namespace = raw.get("assetId"), raw.get("assetNamespace")
    if (
        not isinstance(asset_id, str)
        or not asset_id
        or not isinstance(namespace, str)
        or not namespace
    ):
        raise Failure("invalid_catalog", "Asset entry lacks assetId or assetNamespace.")
    versions = []
    for version in raw.get("projectVersions") or []:
        if (
            not isinstance(version, dict)
            or not isinstance(version.get("artifactId"), str)
            or not version["artifactId"]
        ):
            raise Failure("invalid_catalog", "Invalid artifact entry.")
        engines = version.get("engineVersions") or []
        if not isinstance(engines, list) or not all(
            isinstance(x, str) for x in engines
        ):
            raise Failure("invalid_catalog", "engineVersions must be a string array.")
        versions.append(
            {
                "artifactId": version["artifactId"],
                "engineVersions": [engine_version(x) for x in engines],
            }
        )
    title = raw.get("title") or asset_id
    method = raw.get("distributionMethod") or ""
    if not isinstance(title, str) or not isinstance(method, str):
        raise Failure(
            "invalid_catalog", "Asset title and distributionMethod must be strings."
        )
    return {
        "assetId": asset_id,
        "assetNamespace": namespace,
        "title": title,
        "distributionMethod": method,
        "projectVersions": versions,
    }


class Catalog:
    def __init__(self, home):
        self.home = Path(home)
        self.home.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = sqlite3.connect(self.home / "catalog.sqlite3", timeout=15)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS assets(id TEXT PRIMARY KEY,raw TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS tags(asset_id TEXT,tag TEXT,PRIMARY KEY(asset_id,tag));
            CREATE TABLE IF NOT EXISTS downloads(
                path TEXT PRIMARY KEY,asset_id TEXT,engine TEXT,platform TEXT,account_id TEXT,
                build_version TEXT,file_count INTEGER,bytes INTEGER,completed_at TEXT);
            CREATE TABLE IF NOT EXISTS jobs(
                id TEXT PRIMARY KEY,asset_id TEXT,engine TEXT,platform TEXT,path TEXT,account_id TEXT,
                status TEXT,created_at TEXT,updated_at TEXT,error_kind TEXT);
        """)

    def meta(self):
        return {
            row["key"]: json.loads(row["value"])
            for row in self.db.execute("SELECT * FROM meta")
        }

    def sync(self, items, account, source):
        clean = [normalize_item(x) for x in items]
        ids = [x["assetId"] for x in clean]
        if len(ids) != len(set(ids)):
            raise Failure(
                "invalid_catalog",
                "Duplicate asset IDs in snapshot; existing catalog kept.",
            )
        with self.db:
            self.db.execute("DELETE FROM assets")
            self.db.executemany(
                "INSERT INTO assets VALUES(?,?)",
                [(x["assetId"], json.dumps(x)) for x in clean],
            )
            for key, value in {
                "account": account,
                "source": source,
                "synced_at": now(),
            }.items():
                self.db.execute(
                    "INSERT OR REPLACE INTO meta VALUES(?,?)", (key, json.dumps(value))
                )
        return {
            "count": len(clean),
            "source": source,
            "account": account,
            "synced_at": self.meta()["synced_at"],
        }

    def raw(self, asset_id):
        row = self.db.execute(
            "SELECT raw FROM assets WHERE id=?", (asset_id,)
        ).fetchone()
        if not row:
            raise Failure(
                "asset_not_found",
                "Asset not in the local catalog. Run library sync or assets resolve.",
                3,
            )
        return json.loads(row[0])

    def describe(self, raw):
        asset_id = raw["assetId"]
        return {
            "id": asset_id,
            "title": raw["title"],
            "distribution_method": raw["distributionMethod"],
            "engine_versions": sorted(
                {v for p in raw["projectVersions"] for v in p["engineVersions"]}
            ),
            "tags": [
                r[0]
                for r in self.db.execute(
                    "SELECT tag FROM tags WHERE asset_id=? ORDER BY tag", (asset_id,)
                )
            ],
            "artifacts": raw["projectVersions"],
        }

    def find(self, query="", tag=None, engine=None):
        items = [
            self.describe(json.loads(r[0]))
            for r in self.db.execute("SELECT raw FROM assets")
        ]
        if query:
            q = query.casefold()
            items = [
                x
                for x in items
                if q in x["title"].casefold()
                or q in x["id"].casefold()
                or any(q in t.casefold() for t in x["tags"])
            ]
        if tag:
            items = [x for x in items if tag in x["tags"]]
        if engine:
            items = [x for x in items if engine in x["engine_versions"]]
        return sorted(items, key=lambda x: (x["title"].casefold(), x["id"]))

    def resolve(self, query):
        rows = self.find()
        exact = [x for x in rows if x["id"] == query]
        matches = (
            exact
            or [x for x in rows if x["title"].casefold() == query.casefold()]
            or self.find(query)
        )
        if not matches:
            raise Failure(
                "asset_not_found", "No owned catalog asset matches the query.", 3
            )
        if len(matches) != 1:
            raise Failure(
                "ambiguous_asset",
                "Several assets match; use an exact ID.",
                2,
                {
                    "candidates": [
                        {"id": x["id"], "title": x["title"]} for x in matches[:20]
                    ],
                    "total": len(matches),
                },
            )
        return matches[0]

    def tag(self, asset_id, value, remove=False):
        self.raw(asset_id)
        if not value.strip() or len(value) > 120:
            raise Failure("invalid_tag", "Tag must be 1–120 characters.")
        with self.db:
            if remove:
                self.db.execute(
                    "DELETE FROM tags WHERE asset_id=? AND tag=?", (asset_id, value)
                )
            else:
                self.db.execute(
                    "INSERT OR IGNORE INTO tags VALUES(?,?)", (asset_id, value)
                )
        return self.describe(self.raw(asset_id))

    def add_job(self, plan):
        account = self.meta().get("account", {}).get("id", "")
        with self.db:
            existing = self.db.execute(
                "SELECT id FROM jobs WHERE path=? AND status IN ('pending','running','failed','interrupted')",
                (plan["out"],),
            ).fetchone()
            if existing:
                raise Failure(
                    "duplicate_job",
                    "A queued job already owns this destination.",
                    details={"job_id": existing[0]},
                )
            job_id = uuid.uuid4().hex
            self.db.execute(
                "INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    job_id,
                    plan["asset_id"],
                    plan["engine"],
                    plan["platform"],
                    plan["out"],
                    account,
                    "pending",
                    now(),
                    now(),
                    None,
                ),
            )
        return {"id": job_id, "status": "pending", **plan}

    def claim_job(self):
        try:
            self.db.execute("BEGIN IMMEDIATE")
            row = self.db.execute(
                "SELECT * FROM jobs WHERE status='pending' ORDER BY created_at,id LIMIT 1"
            ).fetchone()
            if row:
                self.db.execute(
                    "UPDATE jobs SET status='running',updated_at=? WHERE id=?",
                    (now(), row["id"]),
                )
            self.db.commit()
            return dict(row) if row else None
        except BaseException:
            self.db.rollback()
            raise

    def job_status(self, job_id, status, error=None):
        with self.db:
            self.db.execute(
                "UPDATE jobs SET status=?,error_kind=?,updated_at=? WHERE id=?",
                (status, error, now(), job_id),
            )

    def retry(self, job_id):
        with self.db:
            row = self.db.execute(
                "SELECT status FROM jobs WHERE id=?", (job_id,)
            ).fetchone()
            if not row or row[0] not in {"failed", "interrupted"}:
                raise Failure(
                    "invalid_job", "Retry requires a failed or interrupted job."
                )
            self.db.execute(
                "UPDATE jobs SET status='pending',error_kind=NULL,updated_at=? WHERE id=?",
                (now(), job_id),
            )
        return {"id": job_id, "status": "pending"}

    def record_download(self, result, account_id):
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO downloads VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    result["out"],
                    result["asset_id"],
                    result["engine"],
                    result["platform"],
                    account_id,
                    result["build_version"],
                    result["file_count"],
                    result["bytes"],
                    now(),
                ),
            )
