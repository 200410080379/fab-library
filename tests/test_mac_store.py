import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from fab_library.catalog import Failure
from fab_library.mac_store import BridgeTransport, MacStoreClient
from fab_library.store import Store

UID = "10000000-0000-4000-8000-000000000001"


class Transport:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def check(self):
        pass

    def request(self, payload, **kwargs):
        self.calls.append(payload)
        status, body = next(self.responses)
        return {
            "ok": True,
            "response": {
                "status": status,
                "body": json.dumps(body) if body is not None else "",
            },
        }


class MacStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def client(self, responses):
        c = MacStoreClient(self.home, Transport(responses))
        c.session = lambda: SimpleNamespace(account_id="account", display_name="Tester")
        return c

    def test_search_uses_animation_filter_without_ue_restriction(self):
        c = self.client([(200, {"results": [], "cursors": {}})])
        Store(self.home, c).search(free=True, category="animation", all_formats=True)
        path = c.transport.calls[0]["path"]
        self.assertIn("listing_types=animation", path)
        self.assertIn("is_free=1", path)
        self.assertNotIn("channels=", path)

    def test_native_claim_posts_exact_reviewed_offer_and_verifies(self):
        c = self.client(
            [
                (200, {"acquired": False}),
                (
                    200,
                    {
                        "offers": [
                            {"offerId": "one", "price": 0},
                            {"offerId": "two", "price": 0},
                        ]
                    },
                ),
                (204, None),
                (200, {"acquired": True}),
            ]
        )
        result = c.claim_exact(UID, "two")
        self.assertTrue(result["claimed"])
        writes = [x for x in c.transport.calls if x["method"] == "POST"]
        self.assertEqual(writes[0]["body"], {"offerId": "two"})
        self.assertEqual(writes[0]["path"], f"/i/listings/{UID}/add-to-library")

    def test_native_claim_blocks_price_change_before_post(self):
        c = self.client(
            [
                (200, {"acquired": False}),
                (200, {"offers": [{"offerId": "one", "price": 4}]}),
            ]
        )
        with self.assertRaises(Failure):
            c.claim_exact(UID, "one")
        self.assertFalse(any(x["method"] == "POST" for x in c.transport.calls))

    def test_ownership_failure_after_post_is_unknown_outcome(self):
        c = self.client(
            [
                (200, {"acquired": False}),
                (200, {"offers": [{"offerId": "one", "price": 0}]}),
                (204, None),
                (403, {"challenge": "blocked"}),
            ]
        )
        with self.assertRaises(Failure) as ctx:
            c.claim_exact(UID, "one")
        self.assertEqual(ctx.exception.kind, "claim_outcome_unknown")
        self.assertEqual(sum(x["method"] == "POST" for x in c.transport.calls), 1)

    def test_native_claim_skips_owned_and_stops_on_site_verification(self):
        c = self.client([(200, {"acquired": True})])
        self.assertTrue(c.claim_exact(UID, "one")["already_owned"])
        self.assertEqual(len(c.transport.calls), 1)
        c = self.client([(403, {"challenge": "not for logs"})])
        with self.assertRaises(Failure) as ctx:
            c.call("ownership", UID)
        self.assertEqual(ctx.exception.kind, "store_access_required")

    def test_oauth_is_exchanged_before_continuing_fab_login(self):
        t = BridgeTransport(self.home)
        t.start = lambda: None
        stream = io.StringIO()
        t.process = SimpleNamespace(stdin=stream)
        t.messages.put({"event": "oauth_code", "authorization_code": "FAKE_CODE"})
        t.messages.put({"ok": True, "fab_session_verified": True})

        def authorize(code):
            self.assertEqual(code, "FAKE_CODE")
            self.assertNotIn("continue_login", stream.getvalue())

        response = t.request({"command": "login"}, authorize=authorize)
        self.assertTrue(response["fab_session_verified"])
        self.assertIn("continue_login", stream.getvalue())
        self.assertNotIn("FAKE_CODE", stream.getvalue())
        t.process = None

    def test_personal_free_professional_paid_uses_pinned_personal_offer(self):
        offers = {
            "offers": [
                {"offerId": "pro", "price": 10},
                {"offerId": "personal", "price": 0},
            ]
        }
        listing = {
            "uid": UID,
            "startingPrice": {"price": 0},
            "licenses": [
                {"offerId": "pro", "slug": "professional", "name": "Professional"},
                {"offerId": "personal", "slug": "personal", "name": "Personal"},
            ],
        }
        c = self.client([(200, listing), (200, offers), (200, {"acquired": False})])
        quote = Store(self.home, c).quote(UID, license_tier="personal")
        self.assertTrue(quote["claim_eligible"])
        self.assertEqual(quote["selected_offer_id"], "personal")
        c = self.client(
            [
                (200, {"acquired": False}),
                (200, offers),
                (200, listing),
                (204, None),
                (200, {"acquired": True}),
            ]
        )
        self.assertTrue(
            c.claim_exact(UID, "personal", license_tier="personal")["claimed"]
        )
        self.assertEqual(
            [x["body"] for x in c.transport.calls if x["method"] == "POST"],
            [{"offerId": "personal"}],
        )

    def test_license_tier_change_blocks_post(self):
        c = self.client(
            [
                (200, {"acquired": False}),
                (200, {"offers": [{"offerId": "one", "price": 0}]}),
                (
                    200,
                    {
                        "licenses": [
                            {
                                "offerId": "one",
                                "slug": "professional",
                                "name": "Professional",
                            }
                        ]
                    },
                ),
            ]
        )
        with self.assertRaises(Failure):
            c.claim_exact(UID, "one", license_tier="personal")
        self.assertFalse(any(x["method"] == "POST" for x in c.transport.calls))

    def test_missing_license_metadata_is_not_guessed(self):
        c = self.client(
            [
                (200, {"uid": UID, "startingPrice": {"price": 0}}),
                (200, {"offers": [{"offerId": "one", "price": 0}]}),
                (200, {"acquired": False}),
            ]
        )
        quote = Store(self.home, c).quote(UID, license_tier="personal")
        self.assertFalse(quote["claim_eligible"])
        self.assertEqual(
            quote["claim_block_reason"],
            "requested_license_tier_unavailable_or_ambiguous",
        )


if __name__ == "__main__":
    unittest.main()
