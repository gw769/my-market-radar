import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class WindowsLauncherSafetyTests(unittest.TestCase):
    def test_stop_script_does_not_use_wildcard_launcher_title(self):
        text = (ROOT / "stop.bat").read_text(encoding="utf-8")
        self.assertIn('WINDOWTITLE eq MY Market Radar"', text)
        self.assertNotIn("WINDOWTITLE eq MY Market Radar*", text)

    def test_stop_script_leaves_unrelated_ports_alone(self):
        text = (ROOT / "stop.bat").read_text(encoding="utf-8")
        self.assertIn("not identifiable as MY Market Radar. Leaving it untouched", text)
        self.assertIn("Port 3000 is in use by PID", text)
        self.assertIn("leaving it untouched", text)
        self.assertIn("stop_docker.bat", text)

    def test_local_launcher_supports_both_virtualenv_names(self):
        text = (ROOT / "start_local.bat").read_text(encoding="utf-8")
        self.assertIn(r"backend\.venv\Scripts\python.exe", text)
        self.assertIn(r"backend\venv\Scripts\python.exe", text)


if __name__ == "__main__":
    unittest.main()
