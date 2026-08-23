import sqlite3
import unittest
from unittest.mock import patch

from app.core import database
from app.core.database import SQLITE_BUSY_TIMEOUT_MS, _configure_sqlite_connection


class DatabaseTests(unittest.TestCase):
    def test_sqlite_connections_enable_foreign_keys_and_busy_timeout(self):
        connection = sqlite3.connect(":memory:")
        try:
            _configure_sqlite_connection(connection, None)
            foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()[0]
            busy_timeout = connection.execute("PRAGMA busy_timeout").fetchone()[0]
            journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
            self.assertEqual(foreign_keys, 1)
            self.assertEqual(busy_timeout, SQLITE_BUSY_TIMEOUT_MS)
            self.assertIn(journal_mode.lower(), {"memory", "wal"})
        finally:
            connection.close()

    def test_explicit_mysql_failure_does_not_silently_create_sqlite(self):
        with patch.object(database.settings, "DATABASE_TYPE", "mysql"), patch.object(
            database.settings, "ALLOW_DATABASE_FALLBACK", False
        ), patch.object(database, "create_engine", side_effect=OSError("mysql is down")) as create:
            with self.assertRaises(RuntimeError) as ctx:
                database._create_sync_engine()
        self.assertIn("MySQL", str(ctx.exception))
        self.assertEqual(create.call_count, 1)


if __name__ == "__main__":
    unittest.main()
