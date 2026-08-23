import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.services.marketplace.scheduler import _run_due_jobs, initial_next_run_utc, next_run_utc


class FakeQuery:
    def __init__(self, keywords):
        self.keywords = keywords

    def filter(self, *_args, **_kwargs):
        return self

    def all(self):
        return self.keywords


class FakeSession:
    def __init__(self, keywords):
        self.keywords = keywords
        self.commits = 0
        self.closed = False

    def query(self, _model):
        return FakeQuery(self.keywords)

    def commit(self):
        self.commits += 1

    def close(self):
        self.closed = True


class SchedulerTests(unittest.TestCase):
    def test_before_daily_time_runs_same_day(self):
        now = datetime(2026, 8, 22, 11, 0, tzinfo=timezone.utc)  # 19:00 MY
        self.assertEqual(next_run_utc("20:00", "Asia/Kuala_Lumpur", now), datetime(2026, 8, 22, 12, 0))

    def test_after_daily_time_runs_next_day(self):
        now = datetime(2026, 8, 22, 13, 0, tzinfo=timezone.utc)  # 21:00 MY
        self.assertEqual(next_run_utc("20:00", "Asia/Kuala_Lumpur", now), datetime(2026, 8, 23, 12, 0))

    def test_naive_now_is_treated_as_utc(self):
        now = datetime(2026, 8, 22, 11, 0)
        self.assertEqual(next_run_utc("20:00", "Asia/Kuala_Lumpur", now), datetime(2026, 8, 22, 12, 0))

    def test_missing_schedule_catches_up_once_after_daily_time(self):
        now = datetime(2026, 8, 22, 13, 0, tzinfo=timezone.utc)  # 21:00 MY
        self.assertEqual(
            initial_next_run_utc("20:00", "Asia/Kuala_Lumpur", None, now),
            datetime(2026, 8, 22, 13, 0),
        )

    def test_missing_schedule_does_not_catch_up_twice_same_day(self):
        now = datetime(2026, 8, 22, 13, 0, tzinfo=timezone.utc)
        last_run = datetime(2026, 8, 22, 12, 10)
        self.assertEqual(
            initial_next_run_utc("20:00", "Asia/Kuala_Lumpur", last_run, now),
            datetime(2026, 8, 23, 12, 0),
        )

    def test_needs_verification_advances_schedule_without_repeat_submit(self):
        now = datetime(2026, 8, 22, 13, 0, tzinfo=timezone.utc)
        keyword = SimpleNamespace(
            daily_time="20:00",
            timezone="Asia/Kuala_Lumpur",
            last_run_at=datetime(2026, 8, 21, 12, 0),
            next_run_at=datetime(2026, 8, 22, 12, 0),
        )
        session = FakeSession([keyword])
        create_run = Mock(return_value=SimpleNamespace(id=7, status="needs_verification"))
        submit_run = Mock()

        with patch("app.services.marketplace.scheduler.SessionLocal", return_value=session), patch(
            "app.services.marketplace.scheduler.datetime"
        ) as dt, patch("app.services.marketplace.scheduler.create_run", create_run), patch(
            "app.services.marketplace.scheduler.submit_run", submit_run
        ):
            dt.now.return_value = now
            _run_due_jobs()
            _run_due_jobs()

        self.assertEqual(create_run.call_count, 1)
        submit_run.assert_not_called()
        self.assertEqual(keyword.next_run_at, datetime(2026, 8, 23, 12, 0))
        self.assertEqual(session.commits, 1)
        self.assertTrue(session.closed)

    def test_due_pending_run_is_submitted_once_and_schedule_advances(self):
        now = datetime(2026, 8, 22, 13, 0, tzinfo=timezone.utc)
        keyword = SimpleNamespace(
            daily_time="20:00",
            timezone="Asia/Kuala_Lumpur",
            last_run_at=datetime(2026, 8, 21, 12, 0),
            next_run_at=datetime(2026, 8, 22, 12, 0),
        )
        session = FakeSession([keyword])
        create_run = Mock(return_value=SimpleNamespace(id=8, status="pending"))
        submit_run = Mock(return_value=True)

        with patch("app.services.marketplace.scheduler.SessionLocal", return_value=session), patch(
            "app.services.marketplace.scheduler.datetime"
        ) as dt, patch("app.services.marketplace.scheduler.create_run", create_run), patch(
            "app.services.marketplace.scheduler.submit_run", submit_run
        ):
            dt.now.return_value = now
            _run_due_jobs()
            _run_due_jobs()

        create_run.assert_called_once()
        submit_run.assert_called_once_with(8)
        self.assertEqual(keyword.next_run_at, datetime(2026, 8, 23, 12, 0))
        self.assertEqual(session.commits, 1)
        self.assertTrue(session.closed)


if __name__ == "__main__":
    unittest.main()
