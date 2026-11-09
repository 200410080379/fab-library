import contextlib
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from fab_library.catalog import Catalog, Failure, normalize_item
from fab_library.cli import main, parser, dispatch
from fab_library.provider import Provider
from fab_library.service import download, plan_download, RECEIPT


def asset(key="asset-1", title="背包 Inventory"):
    return {
        "assetId": key,
        "assetNamespace": "namespace",
        "title": title,
        "distributionMethod": "ASSET_PACK",
        "projectVersions": [
            {"artifactId": key + "-build", "engineVersions": ["UE_5.8"]}
        ],
    }


class FakeHttp:
    def __init__(self, pages):
        self.pages, self.calls = iter(pages), []

    def json(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return next(self.pages)


class FakeDownloader:
    def __init__(self):
        self.calls = 0

    def download(self, raw, artifact_id, platform, out, account_id):
        self.calls += 1
        out.mkdir()
        (out / "Content").mkdir()
        (out / "Content/Asset.uasset").write_bytes(b"test asset")
        return {
            "build_version": "build-1",
            "files": ["Content/Asset.uasset"],
            "file_count": 1,
            "bytes": 10,
        }


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.catalog = Catalog(self.root / "state")
        self.catalog.sync([asset()], {"id": "account-1"}, "live")

    def tearDown(self):
        self.catalog.db.close()
        self.temp.cleanup()

    def plan(self):
        return plan_download(
            self.catalog, "asset-1", "5.8", "Windows", self.root / "download"
        )

    def test_pagination_and_encoding(self):
        h = FakeHttp(
            [
                {"results": [asset()], "cursors": {"next": "a/b+?"}},
                {"results": [asset("asset-2")], "cursors": {}},
            ]
        )
        items = Provider.fetch_library(
            h, SimpleNamespace(account_id="acct/id", access_token="secret")
        )
        self.assertEqual(len(items), 2)
        self.assertIn("acct%2Fid", h.calls[0][0][1])
        self.assertIn("cursor=a%2Fb%2B%3F", h.calls[1][0][1])

    def test_repeated_cursor_and_page_bound(self):
        h = FakeHttp(
            [
                {"results": [asset()], "cursors": {"next": "same"}},
                {"results": [asset("asset-2")], "cursors": {"next": "same"}},
            ]
        )
        with self.assertRaisesRegex(Failure, "repeated"):
            Provider.fetch_library(
                h, SimpleNamespace(account_id="id", access_token="s")
            )
        h = FakeHttp([{"results": [asset()], "cursors": {"next": "next"}}])
        with self.assertRaisesRegex(Failure, "max-pages"):
            Provider.fetch_library(
                h, SimpleNamespace(account_id="id", access_token="s"), max_pages=1
            )
        self.assertEqual(len(self.catalog.find()), 1)

    def test_invalid_sync_is_atomic_and_tags_survive(self):
        self.catalog.tag("asset-1", "背包")
        with self.assertRaises(Failure):
            self.catalog.sync([asset(), {"bad": True}], {"id": "other"}, "live")
        self.assertEqual(self.catalog.meta()["account"]["id"], "account-1")
        self.catalog.sync([asset(title="Renamed")], {"id": "account-1"}, "live")
        self.assertEqual(self.catalog.find(tag="背包")[0]["id"], "asset-1")

    def test_whitelist_drops_credentials(self):
        raw = {
            **asset(),
            "access_token": "SECRET",
            "cookies": "SECRET",
            "signed_url": "SECRET",
        }
        self.assertNotIn("SECRET", json.dumps(normalize_item(raw)))

    def test_resolve_ambiguity_requires_exact_id(self):
        self.catalog.sync(
            [asset(), asset("asset-2", "Inventory other")], {"id": "account-1"}, "live"
        )
        with self.assertRaises(Failure) as ctx:
            self.catalog.resolve("Inventory")
        self.assertEqual(ctx.exception.kind, "ambiguous_asset")
        self.assertEqual(self.catalog.resolve("asset-1")["id"], "asset-1")

    def test_dry_run_has_no_output_or_network(self):
        args = parser().parse_args(
            [
                "download",
                "asset-1",
                "--engine",
                "5.8",
                "--out",
                str(self.root / "download"),
                "--dry-run",
            ]
        )
        result = dispatch(args, self.catalog.home, provider_factory=lambda _: object())
        self.assertTrue(result["dry_run"])
        self.assertFalse((self.root / "download").exists())

    def test_unsupported_version(self):
        with self.assertRaises(Failure):
            plan_download(
                self.catalog, "asset-1", "5.7", "Windows", self.root / "download"
            )

    def test_existing_files_are_preserved(self):
        dest = self.root / "download"
        dest.mkdir()
        (dest / "mine.txt").write_text("keep")
        fake = FakeDownloader()
        with self.assertRaises(Failure):
            download(self.catalog, fake, self.plan())
        self.assertEqual((dest / "mine.txt").read_text(), "keep")
        self.assertEqual(fake.calls, 0)

    def test_download_receipt_skip_and_missing_file(self):
        fake = FakeDownloader()
        result = download(self.catalog, fake, self.plan())
        self.assertFalse(result["skipped"])
        self.assertTrue(download(self.catalog, fake, self.plan())["skipped"])
        self.assertEqual(fake.calls, 1)
        (self.root / "download/Content/Asset.uasset").unlink()
        with self.assertRaises(Failure):
            download(self.catalog, fake, self.plan())

    def test_fixture_execution_is_blocked_but_plan_works(self):
        self.catalog.sync([asset()], {"id": "fixture"}, "fixture")
        plan = self.plan()
        with self.assertRaises(Failure) as ctx:
            download(self.catalog, FakeDownloader(), plan)
        self.assertEqual(ctx.exception.kind, "offline_catalog")

    def test_foreign_staging_preserved(self):
        staging = self.root / "download.fabdl-partial"
        staging.mkdir()
        marker = staging / "important.txt"
        marker.write_text("keep")
        with self.assertRaises(Failure):
            download(self.catalog, FakeDownloader(), self.plan())
        self.assertEqual(marker.read_text(), "keep")

    def test_queue_claim_is_exclusive_and_retry_bounded(self):
        job = self.catalog.add_job(self.plan())
        self.assertEqual(self.catalog.claim_job()["id"], job["id"])
        self.assertIsNone(self.catalog.claim_job())
        with self.assertRaises(Failure):
            self.catalog.retry(job["id"])
        self.catalog.job_status(job["id"], "failed", "network")
        self.catalog.retry(job["id"])
        self.assertEqual(self.catalog.claim_job()["id"], job["id"])

    def test_windows_manifest_paths(self):
        for names in (
            ["CON.txt"],
            ["dir/../escape"],
            ["C:/escape"],
            ["a", "A"],
            ["bad?.uasset"],
            [RECEIPT],
        ):
            with self.subTest(names=names), self.assertRaises(Exception):
                Provider.validate_manifest(
                    SimpleNamespace(files=[SimpleNamespace(filename=n) for n in names]),
                    self.root,
                )
        Provider.validate_manifest(
            SimpleNamespace(files=[SimpleNamespace(filename="Content/Good.uasset")]),
            self.root,
        )

    def test_json_missing_auth_and_invalid_args_do_not_leak(self):
        for argv in (
            ["--home", str(self.root / "clean"), "doctor", "--json"],
            ["--json", "--SECRET_TOKEN=SECRET_VALUE"],
        ):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = main(argv)
            data = json.loads(out.getvalue())
            self.assertNotIn("SECRET_VALUE", out.getvalue())
            if code == 0:
                self.assertFalse(data["data"]["auth"]["token_available"])
            else:
                self.assertEqual(data["error"]["kind"], "invalid_arguments")


if __name__ == "__main__":
    unittest.main()
