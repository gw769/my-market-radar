import unittest
from unittest.mock import Mock, patch

from app.services.marketplace.recovery import recover_interrupted_runs


class FakeRun:
    def __init__(self, run_id: int, status: str):
        self.id = run_id
        self.status = status
        self.progress = 64
        self.current_step = "采集中"
        self.started_at = object()
        self.completed_at = object()
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
    def test_all_recoverable_runs_are_reset_and_requeued(self):
        running = FakeRun(10, "running")
        pending = FakeRun(11, "pending")
        session = FakeSession([running, pending])
        submit = Mock(side_effect=[True, True])

        with patch("app.services.marketplace.recovery.SessionLocal", return_value=session), patch(
            "app.services.marketplace.recovery.submit_run", submit
        ):
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
        self.assertEqual([call.args[0] for call in submit.call_args_list], [10, 11])

    def test_empty_recovery_does_not_commit_or_submit(self):
        session = FakeSession([])
        submit = Mock()

        with patch("app.services.marketplace.recovery.SessionLocal", return_value=session), patch(
            "app.services.marketplace.recovery.submit_run", submit
        ):
            queued = recover_interrupted_runs()

        self.assertEqual(queued, 0)
        self.assertFalse(session.committed)
        self.assertTrue(session.closed)
        submit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
