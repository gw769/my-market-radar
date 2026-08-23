import os
import tempfile
import unittest
from pathlib import Path

from app import main


class FrontendPathContainmentTests(unittest.TestCase):
    def test_valid_frontend_file_resolves_inside_dist(self):
        original = main._frontend_out
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "dist"
            base.mkdir()
            target = base / "favicon.ico"
            target.write_text("icon", encoding="utf-8")
            main._frontend_out = base
            try:
                self.assertEqual(main._safe_frontend_path("favicon.ico"), target.resolve())
            finally:
                main._frontend_out = original

    def test_parent_traversal_is_rejected(self):
        original = main._frontend_out
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = root / "dist"
            base.mkdir()
            (root / "secret.txt").write_text("secret", encoding="utf-8")
            main._frontend_out = base
            try:
                self.assertIsNone(main._safe_frontend_path("../secret.txt"))
                self.assertIsNone(main._safe_frontend_path("assets/../../secret.txt"))
            finally:
                main._frontend_out = original

    def test_absolute_path_is_rejected(self):
        original = main._frontend_out
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "dist"
            base.mkdir()
            outside = Path(tmp) / "outside.txt"
            outside.write_text("outside", encoding="utf-8")
            main._frontend_out = base
            try:
                self.assertIsNone(main._safe_frontend_path(str(outside.resolve())))
            finally:
                main._frontend_out = original

    @unittest.skipIf(os.name == "nt", "symlink creation may require Windows developer mode")
    def test_symlink_escape_is_rejected(self):
        original = main._frontend_out
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = root / "dist"
            base.mkdir()
            outside = root / "outside.txt"
            outside.write_text("outside", encoding="utf-8")
            (base / "linked.txt").symlink_to(outside)
            main._frontend_out = base
            try:
                self.assertIsNone(main._safe_frontend_path("linked.txt"))
            finally:
                main._frontend_out = original


if __name__ == "__main__":
    unittest.main()
