"""Marketplace reads and zero-price claims via pinned FabCLI. No paid checkout API."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
from urllib.parse import urlparse
import uuid

from .catalog import Catalog, Failure, now

FABCLI_VERSION = "0.1.0"


def listing_id(value):
    if not isinstance(value, str):
        raise Failure(
            "invalid_listing",
            "Listing ID must be a UUID string or an official Fab URL.",
        )
    if value.startswith(("https://", "http://")):
        url = urlparse(value)
        parts = url.path.strip("/").split("/")
        if (
            url.scheme != "https"
            or url.hostname not in {"fab.com", "www.fab.com"}
            or url.username
            or url.password
            or len(parts) < 2
            or parts[-2] != "listings"
        ):
            raise Failure(
                "invalid_listing",
                "Use a Fab listing UUID or an official https://www.fab.com/listings/... URL.",
            )
        value = parts[-1]
    try:
        return str(uuid.UUID(value))
    except (ValueError, AttributeError):
        raise Failure(
            "invalid_listing",
            "Use the listing UUID from Fab, not a local library asset ID.",
        ) from None


def amount(value):
    if (
        value is None
        or isinstance(value, bool)
        or not isinstance(value, (int, float, str))
    ):
        return None
    try:
        number = Decimal(str(value))
        return number if number.is_finite() and number >= 0 else None
    except InvalidOperation:
        return None


def offers_from(raw):
    if not isinstance(raw, dict) or not isinstance(raw.get("offers"), list):
        raise Failure(
            "store_schema_changed",
            "Unexpected offer format; no claim was submitted.",
            5,
        )
    result = []
    for offer in raw["offers"]:
        if not isinstance(offer, dict):
            raise Failure("store_schema_changed", "Unexpected offer entry.", 5)
        # A present discounted price takes precedence even if malformed. Never fall back to a stale zero.
        effective = (
            offer.get("discountedPrice")
            if offer.get("discountedPrice") is not None
            else offer.get("price")
        )
        price = amount(effective)
        result.append(
            {
                "offer_id": offer.get("offerId"),
                "currency": offer.get("currencyCode"),
                "amount": str(price) if price is not None else None,
                "discount_end": offer.get("discountEndDate"),
                "license_label": None,
            }
        )
    return result


class FabCLIClient:
    """Only a fixed set of public CLI commands can be invoked; never shell interpolation."""

    def __init__(self, home, executable=None):
        self.home = Path(home)
        self.executable = (
            executable or os.getenv("FAB_LIBRARY_FABCLI") or shutil.which("fabcli")
        )
        self.checked = False

    def env(self):
        env = os.environ.copy()
        env["FABCLI_TOKEN_PATH"] = str(self.home / "store-session" / "token.json")
        return env

    def check(self):
        if not self.executable:
            raise Failure(
                "store_backend_missing",
                "Install FabCLI 0.1.0 using scripts/install-store-windows.ps1, then run fab-library store login.",
                2,
            )
        if os.name == "nt" and Path(self.executable).suffix.lower() != ".exe":
            raise Failure(
                "store_backend_unavailable",
                "On Windows, configure a trusted fabcli.exe rather than a shell wrapper.",
                2,
            )
        if not self.checked:
            try:
                result = subprocess.run(
                    [self.executable, "--version"],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    timeout=20,
                    env=self.env(),
                )
            except (OSError, subprocess.TimeoutExpired):
                raise Failure(
                    "store_backend_unavailable",
                    "FabCLI could not start. Check its installation and WebView2 runtime.",
                    2,
                ) from None
            if result.returncode or result.stdout.strip().split()[-1:] != [
                FABCLI_VERSION
            ]:
                raise Failure(
                    "store_backend_version", "This adapter requires FabCLI 0.1.0.", 2
                )
            self.checked = True

    def call(self, *args):
        self.check()
        if not args or args[0] not in {
            "auth",
            "search",
            "listing",
            "prices",
            "ownership",
            "claim",
        }:
            raise Failure(
                "unsupported_store_command", "Store adapter command is not supported."
            )
        if args[0] == "auth" and tuple(args) not in {
            ("auth", "login"),
            ("auth", "status"),
            ("auth", "whoami"),
        }:
            raise Failure(
                "unsupported_store_command", "Authentication command is not supported."
            )
        try:
            result = subprocess.run(
                [self.executable, *args],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=600 if args == ("auth", "login") else 120,
                env=self.env(),
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired:
            kind = "claim_outcome_unknown" if args[0] == "claim" else "store_timeout"
            raise Failure(
                kind,
                "Store operation timed out. Check ownership before retrying a claim.",
                5,
            ) from None
        except OSError:
            raise Failure(
                "store_backend_unavailable", "FabCLI could not start.", 2
            ) from None
        if result.returncode:
            kind = {
                2: "store_auth_required",
                3: "listing_not_found",
                4: "store_rate_limited",
            }.get(result.returncode, "store_backend_error")
            if args[0] == "claim" and result.returncode not in {2, 3, 4, 6}:
                kind = "claim_outcome_unknown"
            raise Failure(
                kind,
                "FabCLI did not complete the request. Log in with store login if needed; verify ownership before retrying claims. Raw provider output was withheld.",
                4 if result.returncode == 2 else 5,
            )
        try:
            data = json.loads(result.stdout)
        except ValueError:
            raise Failure(
                "claim_outcome_unknown"
                if args[0] == "claim"
                else "store_schema_changed",
                "Backend returned invalid JSON; verify ownership before repeating a claim.",
                5,
            ) from None
        if not isinstance(data, dict):
            raise Failure("store_schema_changed", "Expected an object from FabCLI.", 5)
        return data


class Store:
    def __init__(self, home, client=None):
        self.home = Path(home)
        self.client = client or FabCLIClient(home)

    def account(self):
        raw = self.client.call("auth", "whoami")
        account_id = raw.get("account_id")
        if not isinstance(account_id, str) or not account_id:
            raise Failure(
                "store_auth_required",
                "Store account identity unavailable; run store login.",
                4,
            )
        return {"id": account_id, "display_name": raw.get("display_name", "")}

    def doctor(self):
        try:
            self.client.check()
        except Failure as exc:
            return {
                "available": False,
                "expected_version": FABCLI_VERSION,
                "reason": exc.kind,
                "next_step": "Run scripts/install-store-windows.ps1",
            }
        try:
            raw = self.client.call("auth", "status")
            fab = raw.get("fab") or {}
            return {
                "available": True,
                "version": FABCLI_VERSION,
                "authenticated": raw.get("authenticated") is True,
                "fab_session_present": fab.get("session_present") is True,
                "fab_needs_refresh": fab.get("needs_refresh", True),
                "next_step": "store login",
            }
        except Failure as exc:
            return {
                "available": True,
                "version": FABCLI_VERSION,
                "authenticated": False,
                "reason": exc.kind,
                "next_step": "store login",
            }

    def search(self, query="", free=False, limited_time=False, limit=20, cursor=None):
        args = ["search", "--count", str(limit), "--filter", "channels=unreal-engine"]
        if query:
            args += ["--query", query]
        if free:
            args += ["--filter", "is_free=1"]
        if limited_time:
            args += ["--filter", "min_discount_percentage=100"]
        if cursor:
            args += ["--cursor", cursor]
        raw = self.client.call(*args)
        if not isinstance(raw.get("results"), list):
            raise Failure("store_schema_changed", "Unexpected search result format.", 5)
        items = []
        for entry in raw["results"][:limit]:
            uid = listing_id(entry.get("uid", ""))
            hint = entry.get("startingPrice") or {}
            effective = (
                hint.get("discountedPrice")
                if hint.get("discountedPrice") is not None
                else hint.get("price")
            )
            price = amount(effective)
            items.append(
                {
                    "listing_id": uid,
                    "title": entry.get("title"),
                    "listing_type": entry.get("listingType"),
                    "is_free_hint": entry.get("isFree") is True,
                    "starting_amount": str(price) if price is not None else None,
                    "currency": hint.get("currencyCode"),
                    "url": f"https://www.fab.com/listings/{uid}",
                }
            )
        cursors = raw.get("cursors") or {}
        return {
            "items": items,
            "next_cursor": cursors.get("next"),
            "count": len(items),
            "prices_verified_for_claim": False,
            "channel": "unreal-engine",
        }

    def quote(self, value):
        uid = listing_id(value)
        account = self.account()
        listing = self.client.call("listing", uid)
        if listing_id(listing.get("uid", "")) != uid:
            raise Failure(
                "store_schema_changed", "Listing identity did not match request.", 5
            )
        offers = offers_from(self.client.call("prices", uid))
        state = self.client.call("ownership", uid)
        if state.get("listingUid") != uid or not isinstance(state.get("owned"), bool):
            raise Failure(
                "store_schema_changed",
                "Ownership could not be established; no claim was submitted.",
                5,
            )
        start = listing.get("startingPrice") or {}
        hint = (
            listing.get("isFree") is True
            or amount(start.get("price")) == 0
            or amount(start.get("discountedPrice")) == 0
        )
        reason = None
        if state["owned"]:
            reason = "already_owned"
        elif str(listing.get("listingType", "")).casefold() == "bundle":
            reason = "bundle_requires_official_checkout"
        elif (
            not offers
            or any(
                not isinstance(x["offer_id"], str)
                or not x["offer_id"]
                or x["amount"] is None
                for x in offers
            )
            or len({x["offer_id"] for x in offers}) != len(offers)
        ):
            reason = "unknown_offer_price"
        elif any(Decimal(x["amount"]) != 0 for x in offers):
            reason = "priced_or_mixed_offers_use_browser"
        elif not hint:
            reason = "listing_not_marked_free"
        if reason is None:
            for offer in offers:
                if offer["discount_end"]:
                    try:
                        end = datetime.fromisoformat(offer["discount_end"])
                        if end.tzinfo is None:
                            end = end.replace(tzinfo=timezone.utc)
                        if end <= datetime.now(timezone.utc):
                            reason = "discount_expired_recheck_official_page"
                    except (ValueError, TypeError):
                        reason = "unknown_discount_expiry"
        # Upstream selects the first explicit zero-price offer. All offers must be zero here.
        selected = offers[0]["offer_id"] if offers and reason is None else None
        return {
            "listing_id": uid,
            "title": listing.get("title") or uid,
            "account": account,
            "url": f"https://www.fab.com/listings/{uid}",
            "quoted_at": now(),
            "owned": state["owned"],
            "offers": offers,
            "claim_eligible": reason is None,
            "claim_block_reason": reason,
            "selected_offer_id": selected,
            "license_selection": "FabCLI's first explicit zero-price offer; tier labels are not exposed by this backend.",
            "license_url": "https://www.fab.com/eula",
            "payment_submitted": False,
        }

    def claim(self, value, execute=False, expected_account=None, expected_offer=None):
        if execute and (not expected_account or not expected_offer):
            raise Failure(
                "claim_confirmation_required",
                "Preview first; execution needs --expected-account and --expected-offer from the reviewed quote.",
            )
        quote = self.quote(
            value
        )  # Re-read current prices and ownership even when executing.
        if not execute:
            return {"dry_run": True, **quote}
        if quote["account"]["id"] != expected_account:
            raise Failure(
                "account_changed",
                "Store account changed since the reviewed quote; no claim was submitted.",
                4,
            )
        if quote["owned"]:
            return {
                "listing_id": quote["listing_id"],
                "already_owned": True,
                "claimed": False,
                "verified": True,
                "payment_submitted": False,
            }
        if not quote["claim_eligible"]:
            raise Failure(
                "free_claim_blocked",
                "Current offers are not all explicitly zero, or require official checkout. No claim submitted.",
                6,
                {"reason": quote["claim_block_reason"], "url": quote["url"]},
            )
        if quote["selected_offer_id"] != expected_offer:
            raise Failure(
                "offer_changed",
                "The selected free offer changed; review a new quote. No claim submitted.",
                6,
            )
        result = self.client.call("claim", quote["listing_id"])
        if result.get("ok") is not True:
            raise Failure(
                "free_claim_rejected",
                "FabCLI rejected the claim. Recheck the official listing.",
                5,
                {"url": quote["url"]},
            )
        if result.get("uid") != quote["listing_id"] or not (
            result.get("claimed") is True or result.get("already_owned") is True
        ):
            raise Failure(
                "claim_outcome_unknown",
                "Claim response was ambiguous. Check ownership before retrying.",
                5,
            )
        try:
            state = self.client.call("ownership", quote["listing_id"])
        except Failure:
            raise Failure(
                "claim_outcome_unknown",
                "Claim was sent but ownership could not be rechecked. Do not immediately repeat it.",
                5,
            ) from None
        if (
            state.get("listingUid") != quote["listing_id"]
            or state.get("owned") is not True
        ):
            raise Failure(
                "claim_outcome_unknown",
                "Claim was sent but ownership is not confirmed. Check the official library.",
                5,
            )
        return {
            "listing_id": quote["listing_id"],
            "account": quote["account"],
            "claimed": result.get("claimed") is True,
            "already_owned": result.get("already_owned") is True,
            "verified": True,
            "payment_submitted": False,
            "next_step": "Run library sync in the same Epic account to refresh downloadable UE assets.",
        }


class Shopping:
    """Local bookmarks with timestamped offer quotes; no remote cart/order writes."""

    def __init__(self, home):
        self.catalog = Catalog(home)
        self.db = self.catalog.db
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS shopping(listing_id TEXT PRIMARY KEY, payload TEXT NOT NULL)"
        )

    def add(self, quote, offer_id, dry_run=False):
        matches = [x for x in quote["offers"] if x["offer_id"] == offer_id]
        if (
            len(matches) != 1
            or matches[0]["amount"] is None
            or not matches[0]["currency"]
        ):
            raise Failure(
                "invalid_offer",
                "Choose an exact offer ID with a known price and currency from store quote.",
            )
        row = {
            k: quote[k] for k in ("listing_id", "title", "url", "quoted_at", "account")
        }
        row.update(matches[0])
        row["remote_cart_changed"] = False
        if not dry_run:
            with self.db:
                self.db.execute(
                    "INSERT OR REPLACE INTO shopping VALUES(?,?)",
                    (quote["listing_id"], json.dumps(row)),
                )
        return {"dry_run": dry_run, **row}

    def list(self, limit=25, offset=0):
        rows = [
            json.loads(r[0])
            for r in self.db.execute("SELECT payload FROM shopping ORDER BY listing_id")
        ]
        totals = {}
        for row in rows:
            key = row["currency"]
            totals[key] = totals.get(key, Decimal(0)) + Decimal(row["amount"])
        return {
            "items": rows[offset : offset + limit],
            "total": len(rows),
            "next_offset": offset + limit if offset + limit < len(rows) else None,
            "snapshot_totals_by_currency": {k: str(v) for k, v in totals.items()},
            "prices_are_live": False,
            "checkout": "Open each official listing URL, choose the appropriate license and confirm the current final price on Fab.",
        }

    def remove(self, value):
        uid = listing_id(value)
        with self.db:
            cursor = self.db.execute("DELETE FROM shopping WHERE listing_id=?", (uid,))
        return {
            "listing_id": uid,
            "removed": cursor.rowcount > 0,
            "remote_cart_changed": False,
        }
