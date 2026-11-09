"""Finite, reviewed promotion plans. No scheduler, background scan or recurring claims."""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import uuid

from .catalog import Failure, now
from .store import listing_id


def create_plan(store, out, limit=20, listings=None):
    if not 1 <= limit <= 50:
        raise Failure(
            "invalid_limit", "A single promotion plan supports 1–50 candidates."
        )
    account = store.account()
    if listings:
        ids = list(dict.fromkeys(listing_id(x) for x in listings))
        if len(ids) > limit:
            raise Failure(
                "invalid_limit", "Explicit listing count exceeds this plan's limit."
            )
        next_cursor = None
        source = "explicit_listings"
    else:
        search = store.search(limited_time=True, limit=limit)
        ids = list(dict.fromkeys(x["listing_id"] for x in search["items"]))
        next_cursor = search["next_cursor"]
        source = "UE listings with 100% discount; verify against Fab's official current promotion"
    items, skipped = [], []
    for uid in ids:
        quote = store.quote(uid)
        if quote["account"]["id"] != account["id"]:
            raise Failure(
                "account_changed",
                "Store account changed during planning; no plan saved.",
                4,
            )
        if quote["claim_eligible"]:
            items.append(
                {
                    "listing_id": uid,
                    "title": quote["title"],
                    "expected_offer": quote["selected_offer_id"],
                    "url": quote["url"],
                }
            )
        else:
            skipped.append(
                {
                    "listing_id": uid,
                    "title": quote["title"],
                    "reason": quote["claim_block_reason"],
                    "url": quote["url"],
                }
            )
    created = datetime.now(timezone.utc)
    plan = {
        "schema_version": 1,
        "kind": "fab-library-freebies-plan",
        "account": account,
        "created_at": created.isoformat(),
        "expires_at": (created + timedelta(minutes=30)).isoformat(),
        "source": source,
        "items": items,
        "skipped": skipped,
        "next_cursor": next_cursor,
        "scope": "Only the exact items in this file; no future scans or claims.",
        "license_selection": "Backend-selected first zero-price offer; use the official page when a specific license tier is required.",
    }
    path = Path(out).expanduser().resolve()
    with path.open("x", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)
    return {"plan_file": str(path), "claim_submitted": False, **plan}


def read_plan(path):
    path = Path(path).expanduser().resolve()
    if path.stat().st_size > 1024 * 1024:
        raise Failure("invalid_plan", "Plan file is too large.")
    plan = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(plan, dict)
        or plan.get("schema_version") != 1
        or plan.get("kind") != "fab-library-freebies-plan"
    ):
        raise Failure("invalid_plan", "Not a supported freebie plan.")
    account = plan.get("account")
    if (
        not isinstance(account, dict)
        or not isinstance(account.get("id"), str)
        or not account["id"]
    ):
        raise Failure("invalid_plan", "Plan lacks an account identity.")
    items = plan.get("items")
    if not isinstance(items, list) or len(items) > 50:
        raise Failure("invalid_plan", "Plan must contain at most 50 items.")
    clean = []
    for row in items:
        if (
            not isinstance(row, dict)
            or not isinstance(row.get("expected_offer"), str)
            or not row["expected_offer"]
        ):
            raise Failure("invalid_plan", "Plan contains an invalid item.")
        clean.append(
            {
                "listing_id": listing_id(row.get("listing_id")),
                "expected_offer": row["expected_offer"],
            }
        )
    if len({x["listing_id"] for x in clean}) != len(clean):
        raise Failure("invalid_plan", "Plan contains duplicate listing IDs.")
    try:
        created = datetime.fromisoformat(plan["created_at"])
        expires = datetime.fromisoformat(plan["expires_at"])
        current = datetime.now(timezone.utc)
        if (
            created.tzinfo is None
            or expires.tzinfo is None
            or expires - created != timedelta(minutes=30)
        ):
            raise ValueError()
        if not created - timedelta(minutes=1) <= current < expires:
            raise Failure(
                "plan_expired",
                "Plan expired or is dated in the future. Create a fresh preview.",
                6,
            )
    except (ValueError, TypeError, KeyError):
        raise Failure("invalid_plan", "Invalid plan timestamps.") from None
    return {
        "account": {"id": account["id"]},
        "items": clean,
        "plan_file": str(path),
        "expires_at": plan["expires_at"],
    }


def apply_plan(store, home, path, dry_run=False):
    plan = read_plan(path)
    if dry_run:
        return {
            "dry_run": True,
            "prices_rechecked": False,
            "claim_submitted": False,
            **plan,
        }
    if store.account()["id"] != plan["account"]["id"]:
        raise Failure(
            "account_changed",
            "Current store account differs from this plan. No claims submitted.",
            4,
        )
    root = Path(home) / "freebie-runs"
    root.mkdir(parents=True, exist_ok=True)
    receipt = root / (uuid.uuid4().hex + ".json")
    result = {
        "account": plan["account"],
        "plan_file": plan["plan_file"],
        "started_at": now(),
        "completed": [],
        "status": "running",
    }

    def save():
        temporary = receipt.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(receipt)

    save()
    for row in plan["items"]:
        try:
            outcome = store.claim(
                row["listing_id"],
                execute=True,
                expected_account=plan["account"]["id"],
                expected_offer=row["expected_offer"],
            )
            result["completed"].append(outcome)
            save()
        except BaseException as exc:
            result["status"] = "stopped"
            result["stopped_listing_id"] = row["listing_id"]
            result["error_kind"] = (
                exc.kind if isinstance(exc, Failure) else "interrupted_or_unknown"
            )
            save()
            if isinstance(exc, Failure):
                exc.details = {
                    "receipt_file": str(receipt),
                    "completed_count": len(result["completed"]),
                    "stopped_listing_id": row["listing_id"],
                }
            raise
    result["status"] = "completed"
    result["finished_at"] = now()
    save()
    return {"receipt_file": str(receipt), **result}
