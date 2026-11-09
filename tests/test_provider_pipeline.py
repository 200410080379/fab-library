"""Offline integration: real manifest parser, chunk decoder, assembly and SHA-1 check."""

import hashlib
import json
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace
import unittest

from fab_library.catalog import Catalog, Failure
from fab_library.cli import classify
from fab_library.provider import Provider
from fab_library.service import download, plan_download
from fabdl.download import DownloadError
from fabdl.net import HttpError


ITEM = {
    "assetId": "owned-id",
    "assetNamespace": "ns",
    "title": "Offline test",
    "distributionMethod": "ASSET_PACK",
    "projectVersions": [{"artifactId": "artifact", "engineVersions": ["UE_5.8"]}],
}
PAYLOAD = b"fixture asset bytes, not a real UE asset"
GUID = (1, 2, 3, 4)
GUID_HEX = "".join(f"{x:08X}" for x in GUID)


def blob(data):
    return "".join(f"{n:03d}" for n in data)


class FixtureTransport:
    def __init__(self, corrupt=False):
        self.corrupt = corrupt
        self.posts = []
        self.chunk_requests = 0

    def json(self, method, url, *, headers=None, body=None):
        if method == "GET" and "/ue/library?" in url:
            return {"results": [ITEM], "cursors": {}}
        if method == "POST" and url.endswith("/manifest"):
            self.posts.append(json.loads(body))
            return {
                "downloadInfo": [
                    {
                        "artifactId": "artifact",
                        "buildVersion": "test-build",
                        "distributionPointBaseUrls": [
                            "https://cdn.example.invalid/build"
                        ],
                        "distributionPoints": [
                            {
                                "manifestUrl": "https://cdn.example.invalid/build/manifest?signature=FAKE_SECRET"
                            }
                        ],
                    }
                ]
            }
        raise AssertionError("Unexpected fixture API request")

    def request(self, method, url):
        if "/manifest?" in url:
            return json.dumps(
                {
                    "ManifestFileVersion": "000000000000",
                    "BuildVersionString": "test-build",
                    "ChunkFilesizeList": {GUID_HEX: "000"},
                    "ChunkHashList": {GUID_HEX: "000"},
                    "DataGroupList": {GUID_HEX: "000"},
                    "FileManifestList": [
                        {
                            "Filename": "Content/测试.uasset",
                            "FileHash": blob(hashlib.sha1(PAYLOAD).digest()),
                            "FileChunkParts": [
                                {
                                    "Guid": GUID_HEX,
                                    "Offset": "000000000000",
                                    "Size": blob(len(PAYLOAD).to_bytes(4, "little")),
                                }
                            ],
                        }
                    ],
                }
            ).encode()
        if url.endswith(".chunk"):
            self.chunk_requests += 1
            data = b"X" * len(PAYLOAD) if self.corrupt else PAYLOAD
            return (
                struct.pack("<IIIIIIIIQB", 0xB1FE3AA2, 1, 41, len(data), *GUID, 0, 0)
                + data
            )
        raise AssertionError("Unexpected fixture CDN request")


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.catalog = Catalog(self.root / "state")
        self.catalog.sync([ITEM], {"id": "account"}, "live")
        self.transport = FixtureTransport()
        self.provider = Provider(self.catalog.home, http=self.transport)
        self.provider.session = lambda: SimpleNamespace(
            account_id="account", access_token="FAKE_TOKEN"
        )
        self.plan = plan_download(
            self.catalog, "owned-id", "5.8", "Windows", self.root / "asset"
        )

    def tearDown(self):
        self.catalog.db.close()
        self.temp.cleanup()

    def test_complete_download_using_real_backend(self):
        result = download(self.catalog, self.provider, self.plan)
        self.assertEqual(
            (self.root / "asset/Content/测试.uasset").read_bytes(), PAYLOAD
        )
        self.assertEqual(result["bytes"], len(PAYLOAD))
        self.assertEqual(
            self.transport.posts,
            [{"item_id": "owned-id", "namespace": "ns", "platform": "Windows"}],
        )
        self.assertNotIn("FAKE_SECRET", json.dumps(result))
        self.assertNotIn("FAKE_TOKEN", json.dumps(result))

    def test_corrupt_data_fails_and_same_plan_can_resume(self):
        self.transport.corrupt = True
        with self.assertRaises(DownloadError):
            download(self.catalog, self.provider, self.plan)
        self.assertFalse((self.root / "asset").exists())
        self.transport.corrupt = False
        result = download(self.catalog, self.provider, self.plan)
        self.assertEqual(result["file_count"], 1)
        self.assertEqual(
            (self.root / "asset/Content/测试.uasset").read_bytes(), PAYLOAD
        )

    def test_wrong_account_and_exception_redaction(self):
        self.provider.session = lambda: SimpleNamespace(
            account_id="another", access_token="FAKE_TOKEN"
        )
        with self.assertRaises(Failure) as ctx:
            download(self.catalog, self.provider, self.plan)
        self.assertEqual(ctx.exception.kind, "account_changed")
        error = classify(
            HttpError(
                "GET",
                "https://example.invalid/FAKE_TOKEN?secret=FAKE_SECRET",
                403,
                b"FAKE_TOKEN",
            )
        )
        self.assertNotIn("FAKE", str(error))


if __name__ == "__main__":
    unittest.main()
