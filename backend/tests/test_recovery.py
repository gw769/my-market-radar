import unittest
from datetime import datetime, timedelta
from unittest.mock import Mock, patch

from app.services.marketplace.recovery import recover_interrupted_runs, recover_stale_runs


class FakeRun:
    def __init__(self, run_id: int, status: str):
        self.id = run_id
        self.status = status
        self.progress = 64
        self.current_step = "采集中"
        self.started_at = datetime(2026, 8, 23, 4, 0, 0)
        self.heartbeat_at = datetime(2026, 8, 23, 4, 1, 0)
        self.worker_id = "worker-old"
        self.completed_at = datetime(2026, 8, 23, 4, 2, 0)
        self.error_message = "old error"
        self.verification_platform = "shopee"


class FakeQuery:
    def __init__(self, runs):
        self.runs = runs

    def filter(self, *_args, **_kwargs):
        return self

    def order_by(self, *_args, **_kwargs):
        return self

    def all(self):
        return self.runs


class FakeSession:
    def __init__(self, runs):
        self.runs = runs
        self.committed = False
        self.closed = False

    def query(self, _model):
        return FakeQuery(self.runs)

    def commit(self):
        self.committed = True

    def close(self):
        self.closed = True


class RecoveryTests(unittest.TestCase):
    def test_startup_recovery_uses_normal_serial_queue(self):
        running = FakeRun(10, "running")
        pending = FakeRun(11, "pending")
        session = FakeSession([running, pending])
        normal_submit = Mock(side_effect=[True, True])
        recovery_submit = Mock()

        with patch("app.services.marketplace.recovery.SessionLocal", return_value=session), patch(
            "app.services.marketplace.recovery.submit_run", normal_submit
        ), patch("app.services.marketplace.recovery.submit_recovery_run", recovery_submit):
            queued = recover_interrupted_runs()

        self.assertEqual(queued, 2)
        self.assertTrue(session.committed)
        self.assertTrue(session.closed)
        for run in (running, pending):
            self.assertEqual(run.status, "pending")
            self.assertEqual(run.progress, 0)
            self.assertEqual(run.current_step, "服务重启后等待恢复采集")
            self.assertIsNone(run.started_at)
            self.assertIsNone(run.completed_at)
            self.assertIsNone(run.error_message)
            self.assertIsNone(run.verification_platform)
            self.assertIsNone(run.worker_id)
            self.assertIsNone(run.heartbeat_at)
        self.assertEqual([call.args[0] for call in normal_submit.call_args_list], [10, 11])
        recovery_submit.assert_not_called()

    def test_stale_running_worker_uses_independent_recovery_executor(self):
        stale = FakeRun(21, "running")
        recent = FakeRun(22, "running")
        now = datetime(2026, 8, 23, 5, 0, 0)
        stale.heartbeat_at = now - timedelta(minutes=10)
        recent.heartbeat_at = now - timedelta(seconds=30)
        session = FakeSession([stale, recent])
        normal_submit = Mock()
        recovery_submit = Mock(return_value=True)

        with patch("app.services.marketplace.recovery.SessionLocal", return_value=session), patch(
            "app.services.marketplace.recovery.submit_run", normal_submit
        ), patch(
            "app.services.marketplace.recovery.submit_recovery_run", recovery_submit
        ), patch("app.services.marketplace.recovery.settings.RUN_STALE_AFTER_SECONDS", 240):
            queued = recover_stale_runs(now=now)

        self.assertEqual(queued, 1)
        self.assertTrue(session.committed)
        self.assertEqual(stale.status, "pending")
        self.assertEqual(stale.current_step, "采集 worker 心跳超时，启动独立恢复 worker")
        self.assertIsNone(stale.worker_id)
        self.assertIsNone(stale.heartbeat_at)
        self.assertEqual(recent.status, "running")
        self.assertEqual(recent.worker_id, "worker-old")
        recovery_submit.assert_called_once_with(21)
        normal_submit.assert_not_called()

    def test_empty_recovery_does_not_commit_or_submit(self):
        session = FakeSession([])
        normal_submit = Mock()
        recovery_submit = Mock()

        with patch("app.services.marketplace.recovery.SessionLocal", return_value=session), patch(
            "app.services.marketplace.recovery.submit_run", normal_submit
        ), patch("app.services.marketplace.recovery.submit_recovery_run", recovery_submit):
            queued = recover_interrupted_runs()

        self.assertEqual(queued, 0)
        self.assertFalse(session.committed)
        self.assertTrue(session.closed)
        normal_submit.assert_not_called()
        recovery_submit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
