import unittest
from datetime import datetime, timezone

from app.services.marketplace.scheduler import next_run_utc


class SchedulerTests(unittest.TestCase):
    def test_before_daily_time_runs_same_day(self):
        now = datetime(2026, 8, 22, 11, 0, tzinfo=timezone.utc)  # 19:00 MY
        self.assertEqual(next_run_utc("20:00", "Asia/Kuala_Lumpur", now), datetime(2026, 8, 22, 12, 0))

    def test_after_daily_time_runs_next_day(self):
        now = datetime(2026, 8, 22, 13, 0, tzinfo=timezone.utc)  # 21:00 MY
        self.assertEqual(next_run_utc("20:00", "Asia/Kuala_Lumpur", now), datetime(2026, 8, 23, 12, 0))


if __name__ == "__main__":
    unittest.main()
