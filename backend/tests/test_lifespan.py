import asyncio
import unittest
from unittest.mock import patch

from app import main


class LifespanTests(unittest.TestCase):
    def test_scheduler_stops_when_lifespan_body_raises(self):
        async def exercise():
            with self.assertRaises(RuntimeError):
                async with main.lifespan(None):
                    raise RuntimeError("boom")

        with patch.object(main, "_validate_runtime_security") as validate, patch.object(
            main, "init_db"
        ) as init_db, patch.object(main, "_ensure_default_admin") as bootstrap, patch.object(
            main, "recover_interrupted_runs"
        ) as recover, patch.object(main, "start_scheduler") as start, patch.object(
            main, "stop_scheduler"
        ) as stop:
            asyncio.run(exercise())

        validate.assert_called_once_with()
        init_db.assert_called_once_with()
        bootstrap.assert_called_once_with()
        recover.assert_called_once_with()
        start.assert_called_once_with()
        stop.assert_called_once_with()

    def test_scheduler_stops_on_normal_lifespan_exit(self):
        async def exercise():
            async with main.lifespan(None):
                return

        with patch.object(main, "_validate_runtime_security"), patch.object(
            main, "init_db"
        ), patch.object(main, "_ensure_default_admin"), patch.object(
            main, "recover_interrupted_runs"
        ), patch.object(main, "start_scheduler") as start, patch.object(
            main, "stop_scheduler"
        ) as stop:
            asyncio.run(exercise())

        start.assert_called_once_with()
        stop.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
