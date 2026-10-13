"""Where assets may be downloaded from: control/asset_source.py.

Run with plain Python, no Blender:

    python3 -m unittest discover -s tests -p 'test_*.py'
"""

import os
import unittest

from _load import load_module

assets = load_module("control/asset_source.py")


class AssetUrls(unittest.TestCase):
    def test_framo_storage(self):
        for url in ("https://lovely-goose-27.convex.cloud/api/storage/abc",
                    "https://lovely-goose-27.convex.site/assets/abc.glb",
                    "https://X.CONVEX.CLOUD/a", "https://a.convex.cloud:443/a"):
            with self.subTest(url):
                self.assertTrue(assets.is_allowed_asset_url(url))

    def test_everything_else(self):
        for url in ("http://lovely-goose-27.convex.cloud/a", "https://convex.cloud/a",
                    "https://.convex.cloud/a", "https://evil.com/a.convex.cloud",
                    "https://convex.cloud.evil.com/a", "https://evilconvex.cloud/a",
                    "https://a.convex.cloud:8443/a", "https://user:pw@a.convex.cloud/a",
                    "file:///etc/passwd", "ftp://a.convex.cloud/a", "", None, 5,
                    "https://a.convex.cloud@evil.com/a", "https://localhost/a",
                    "https://169.254.169.254/latest"):
            with self.subTest(url):
                self.assertFalse(assets.is_allowed_asset_url(url))

    def test_refusal_downloads_nothing(self):
        class Boom:
            def open(self, *a, **k):
                raise AssertionError("must not open a refused URL")
        with self.assertRaises(assets.AssetDownloadError) as ctx:
            assets.fetch_asset("never-cached-xyz", "https://evil.example/a.glb", opener=Boom())
        self.assertIn("refused", str(ctx.exception))


class CachePaths(unittest.TestCase):
    def test_plain_ids_name_the_file(self):
        self.assertEqual(os.path.basename(assets.cache_path("kd7abc123")), "kd7abc123.glb")

    def test_unsafe_ids_are_hashed_into_the_cache_dir(self):
        for asset_id in ("../../etc/passwd", "/abs", "..", ".", "a/b", "a\\b", "x" * 200, "ü"):
            with self.subTest(asset_id):
                path = assets.cache_path(asset_id)
                self.assertEqual(os.path.dirname(path), assets.cache_dir())
                self.assertTrue(os.path.basename(path).startswith("id-"))


if __name__ == "__main__":
    unittest.main()
