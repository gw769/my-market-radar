import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.testclient import TestClient

from app import main


class ResponsePerformanceTests(unittest.TestCase):
    def test_application_installs_gzip_with_bounded_threshold(self):
        gzip_entries = [
            middleware
            for middleware in main.app.user_middleware
            if middleware.cls is GZipMiddleware
        ]
        self.assertEqual(len(gzip_entries), 1)
        self.assertEqual(gzip_entries[0].kwargs["minimum_size"], 1024)
        self.assertEqual(gzip_entries[0].kwargs["compresslevel"], 6)

    def test_hashed_static_assets_are_immutable_and_gzipped(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            assets = Path(temp_dir)
            hashed = assets / "index-AbCdEf123456.js"
            plain = assets / "runtime.js"
            hashed.write_text("const payload = '" + ("abcdef" * 2000) + "';")
            plain.write_text("const runtime = true;")

            app = FastAPI()
            app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=6)

            @app.get("/large.json")
            def large_json():
                return {"payload": "abcdef" * 2000}

            app.mount("/assets", main.CachedStaticFiles(directory=temp_dir), name="assets")

            with TestClient(app) as client:
                json_response = client.get(
                    "/large.json",
                    headers={"Accept-Encoding": "gzip"},
                )
                self.assertEqual(json_response.status_code, 200)
                self.assertEqual(json_response.headers.get("content-encoding"), "gzip")

                response = client.get(
                    f"/assets/{hashed.name}",
                    headers={"Accept-Encoding": "gzip"},
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers.get("content-encoding"), "gzip")
                self.assertEqual(
                    response.headers.get("cache-control"),
                    "public, max-age=31536000, immutable",
                )

                plain_response = client.get(f"/assets/{plain.name}")
                self.assertEqual(plain_response.status_code, 200)
                self.assertNotIn("immutable", plain_response.headers.get("cache-control", ""))

    def test_index_response_is_never_immutable(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            frontend_dir = Path(temp_dir)
            (frontend_dir / "index.html").write_text("<!doctype html><title>test</title>")
            with patch.object(main, "_frontend_out", frontend_dir):
                response = main._index_response()
            self.assertEqual(response.headers.get("cache-control"), "no-cache")


if __name__ == "__main__":
    unittest.main()
