from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest

from fab_library.catalog import Failure
from fab_library.freebies import create_plan, apply_plan

IDS = [f"{i}0000000-0000-4000-8000-00000000000{i}" for i in (1, 2, 3)]


class BatchStore:
    def __init__(self):
        self.ids = IDS[:2]
        self.account_id = "account"
        self.claimed = []
        self.blocked = set()
        self.fail_on = None

    def account(self):
        return {"id": self.account_id}

    def search(self, **kwargs):
        return {"items": [{"listing_id": x} for x in self.ids], "next_cursor": None}

    def quote(self, uid):
        return {
            "account": self.account(),
            "listing_id": uid,
            "title": "Demo",
            "url": "https://www.fab.com/listings/" + uid,
            "claim_eligible": uid not in self.blocked,
            "claim_block_reason": "already_owned" if uid in self.blocked else None,
            "selected_offer_id": "offer-" + uid,
        }

    def claim(self, uid, execute, expected_account, expected_offer):
        assert (
            execute
            and expected_account == self.account_id
            and expected_offer == "offer-" + uid
        )
        if uid == self.fail_on:
            raise Failure("offer_changed", "test stop", 6)
        self.claimed.append(uid)
        return {"listing_id": uid, "claimed": True, "verified": True}


class FreebiePlanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root / "plan.json"
        self.store = BatchStore()

    def tearDown(self):
        self.temp.cleanup()

    def test_plan_only_reads_and_skips_owned(self):
        self.store.blocked.add(IDS[1])
        result = create_plan(self.store, self.path)
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["skipped"][0]["reason"], "already_owned")
        self.assertFalse(self.store.claimed)

    def test_apply_is_exact_snapshot_not_future_discovery(self):
        create_plan(self.store, self.path)
        self.store.ids = IDS
        result = apply_plan(self.store, self.root, self.path)
        self.assertEqual(self.store.claimed, IDS[:2])
        self.assertEqual(result["status"], "completed")

    def test_dry_run_does_not_claim_or_create_receipt(self):
        create_plan(self.store, self.path)
        result = apply_plan(self.store, self.root, self.path, True)
        self.assertTrue(result["dry_run"])
        self.assertFalse(self.store.claimed)
        self.assertFalse((self.root / "freebie-runs").exists())

    def test_partial_failure_stops_and_keeps_receipt(self):
        self.store.ids = IDS
        create_plan(self.store, self.path)
        self.store.fail_on = IDS[1]
        with self.assertRaises(Failure) as ctx:
            apply_plan(self.store, self.root, self.path)
        result = json.loads(Path(ctx.exception.details["receipt_file"]).read_text())
        self.assertEqual(self.store.claimed, [IDS[0]])
        self.assertEqual(len(result["completed"]), 1)
        self.assertEqual(result["stopped_listing_id"], IDS[1])

    def test_expired_plan_and_account_change_block_all_claims(self):
        create_plan(self.store, self.path)
        self.store.account_id = "other"
        with self.assertRaises(Failure):
            apply_plan(self.store, self.root, self.path)
        self.store.account_id = "account"
        data = json.loads(self.path.read_text())
        start = datetime.now(timezone.utc) - timedelta(hours=1)
        data.update(
            created_at=start.isoformat(),
            expires_at=(start + timedelta(minutes=30)).isoformat(),
        )
        self.path.write_text(json.dumps(data))
        with self.assertRaises(Failure) as ctx:
            apply_plan(self.store, self.root, self.path)
        self.assertEqual(ctx.exception.kind, "plan_expired")
        self.assertFalse(self.store.claimed)

    def test_explicit_links_do_not_discover_other_listings(self):
        result = create_plan(
            self.store, self.path, listings=["https://www.fab.com/listings/" + IDS[2]]
        )
        self.assertEqual([x["listing_id"] for x in result["items"]], [IDS[2]])


if __name__ == "__main__":
    unittest.main()
