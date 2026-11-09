import copy
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fab_library.catalog import Failure
from fab_library.cli import parser, dispatch
from fab_library.store import FabCLIClient, Store, Shopping, listing_id

UID = "10000000-0000-4000-8000-000000000001"
PAID = "20000000-0000-4000-8000-000000000002"


class FakeStoreClient:
    def __init__(self):
        self.calls = []
        self.owned = False
        self.account_id = "test-account"
        self.offers = [
            {
                "offerId": "free-offer",
                "currencyCode": "USD",
                "price": 10,
                "discountedPrice": 0,
            }
        ]
        self.kind = "3d-model"
        self.hint = True
        self.result = {"ok": True, "uid": UID, "claimed": True}
        self.verify_failure = False

    def check(self):
        pass

    def call(self, *args):
        self.calls.append(args)
        if args == ("auth", "whoami"):
            return {
                "account_id": self.account_id,
                "display_name": "Tester",
                "email": "PRIVATE_EMAIL",
            }
        if args[0] == "listing":
            return {
                "uid": args[1],
                "title": "Test asset",
                "isFree": self.hint,
                "listingType": self.kind,
            }
        if args[0] == "prices":
            return {"offers": copy.deepcopy(self.offers)}
        if args[0] == "ownership":
            if self.owned and self.verify_failure:
                raise Failure("store_timeout", "simulated", 5)
            return {"listingUid": args[1], "owned": self.owned}
        if args[0] == "claim":
            self.owned = True
            return self.result
        if args[0] == "search":
            return {
                "results": [{"uid": UID, "title": "Demo", "isFree": True}],
                "cursors": {"next": "real-cursor"},
            }
        if args == ("auth", "status"):
            return {
                "authenticated": True,
                "fab": {"session_present": True, "needs_refresh": False},
            }
        raise AssertionError(args)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.client = FakeStoreClient()
        self.store = Store(self.home, self.client)

    def tearDown(self):
        self.temp.cleanup()

    def execute(self, **kwargs):
        return self.store.claim(
            UID,
            True,
            kwargs.get("account", "test-account"),
            kwargs.get("offer", "free-offer"),
        )

    def mutations(self):
        return [x for x in self.client.calls if x[0] == "claim"]

    def test_default_claim_is_preview_only(self):
        result = self.store.claim(UID)
        self.assertTrue(result["dry_run"])
        self.assertTrue(result["claim_eligible"])
        self.assertFalse(self.mutations())
        self.assertNotIn("PRIVATE_EMAIL", json.dumps(result))

    def test_claim_verified_and_already_owned_skipped(self):
        self.assertTrue(self.execute()["verified"])
        self.assertEqual(len(self.mutations()), 1)
        self.assertTrue(self.execute()["already_owned"])
        self.assertEqual(len(self.mutations()), 1)

    def test_confirmations_required_before_any_reads(self):
        with self.assertRaises(Failure):
            self.store.claim(UID, True)
        self.assertFalse(self.client.calls)

    def test_account_and_offer_changes_block_write(self):
        for kwargs in ({"account": "other"}, {"offer": "different"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(Failure):
                self.execute(**kwargs)
        self.assertFalse(self.mutations())

    def test_price_rechecked_and_paid_unknown_mixed_rejected(self):
        self.assertTrue(self.store.claim(UID)["claim_eligible"])
        for prices in (
            [{"offerId": "free-offer", "price": 10}],
            [{"offerId": "free-offer"}],
            [{"offerId": "free-offer", "price": 0, "discountedPrice": "bad"}],
            [{"offerId": "free-offer", "price": 0}, {"offerId": "paid", "price": 1}],
            [{"offerId": "free-offer", "price": False}],
            [{"offerId": "free-offer", "price": "NaN"}],
        ):
            with self.subTest(prices=prices):
                self.client.offers = prices
                with self.assertRaises(Failure):
                    self.execute()
                self.assertFalse(self.mutations())

    def test_bundle_and_expired_discount_are_blocked(self):
        self.client.kind = "bundle"
        self.assertEqual(
            self.store.claim(UID)["claim_block_reason"],
            "bundle_requires_official_checkout",
        )
        self.client.kind = "3d-model"
        self.client.offers[0]["discountEndDate"] = "2000-01-01T00:00:00Z"
        with self.assertRaises(Failure):
            self.execute()
        self.assertFalse(self.mutations())

    def test_unknown_post_claim_outcome_is_not_retried(self):
        self.client.verify_failure = True
        with self.assertRaises(Failure) as ctx:
            self.execute()
        self.assertEqual(ctx.exception.kind, "claim_outcome_unknown")
        self.assertEqual(len(self.mutations()), 1)

    def test_paid_backend_response_is_not_success(self):
        self.client.result = {"ok": False, "reason": "not_free"}
        with self.assertRaises(Failure) as ctx:
            self.execute()
        self.assertEqual(ctx.exception.kind, "free_claim_rejected")

    def test_official_url_validation(self):
        self.assertEqual(
            listing_id("https://www.fab.com/en-US/listings/" + UID + "?utm_source=x"),
            UID,
        )
        for value in (
            "https://evil.invalid/listings/" + UID,
            "https://fab.com.evil.invalid/listings/" + UID,
            "https://user:pass@fab.com/listings/" + UID,
            ";touch /tmp/test",
            None,
        ):
            with self.subTest(value=value), self.assertRaises(Failure):
                listing_id(value)

    def test_search_pagination_is_real_and_query_is_argument(self):
        result = self.store.search(
            "$(echo injected)", free=True, limit=10, cursor="page=2&x"
        )
        args = self.client.calls[0]
        self.assertIn("$(echo injected)", args)
        self.assertIn("page=2&x", args)
        self.assertEqual(result["next_cursor"], "real-cursor")

    def test_shopping_is_local_and_totals_do_not_mix_currencies(self):
        shopping = Shopping(self.home)
        try:
            self.client.offers = [
                {"offerId": "usd", "price": 19.95, "currencyCode": "USD"}
            ]
            first = self.store.quote(UID)
            shopping.add(first, "usd", True)
            self.assertEqual(shopping.list()["total"], 0)
            shopping.add(first, "usd")
            self.client.offers = [
                {"offerId": "eur", "price": 10, "currencyCode": "EUR"}
            ]
            shopping.add(self.store.quote(PAID), "eur")
            result = shopping.list(limit=1)
            self.assertEqual(
                result["snapshot_totals_by_currency"], {"USD": "19.95", "EUR": "10"}
            )
            self.assertEqual(result["next_offset"], 1)
            self.assertFalse(self.mutations())
            self.assertTrue(shopping.remove(UID)["removed"])
        finally:
            shopping.db.close()

    def test_backend_pins_version_and_redacts_raw_failures(self):
        client = FabCLIClient(self.home, "fabcli.exe")
        with patch(
            "subprocess.run",
            side_effect=[
                SimpleNamespace(returncode=0, stdout="fabcli 0.1.0", stderr=""),
                SimpleNamespace(
                    returncode=5, stdout="", stderr="TOKEN=SECRET cookie=SECRET"
                ),
            ],
        ) as run:
            with self.assertRaises(Failure) as ctx:
                client.call("prices", UID)
            self.assertNotIn("SECRET", str(ctx.exception))
            self.assertEqual(run.call_args.args[0], ["fabcli.exe", "prices", UID])
            self.assertEqual(
                run.call_args.kwargs["env"]["FABCLI_TOKEN_PATH"],
                str(self.home / "store-session/token.json"),
            )
        client = FabCLIClient(self.home, "fabcli.exe")
        with patch(
            "subprocess.run",
            return_value=SimpleNamespace(returncode=0, stdout="fabcli 9.0", stderr=""),
        ):
            with self.assertRaises(Failure):
                client.check()

    def test_timeout_after_claim_is_unknown_not_safe_to_retry(self):
        client = FabCLIClient(self.home, "fabcli.exe")
        client.checked = True
        with patch(
            "subprocess.run", side_effect=subprocess.TimeoutExpired("claim", 120)
        ):
            with self.assertRaises(Failure) as ctx:
                client.call("claim", UID)
        self.assertEqual(ctx.exception.kind, "claim_outcome_unknown")

    def test_cli_preview_wiring_does_not_submit(self):
        args = parser().parse_args(["store", "claim", UID])
        result = dispatch(args, self.home, store_factory=lambda _: self.store)
        self.assertTrue(result["dry_run"])
        self.assertFalse(self.mutations())


if __name__ == "__main__":
    unittest.main()
