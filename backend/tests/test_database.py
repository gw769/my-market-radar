import sqlite3
import unittest

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
            # In-memory SQLite cannot switch to WAL; file databases can. The important
            # regression is that configuring the connection succeeds in both environments.
            self.assertIn(journal_mode.lower(), {"memory", "wal"})
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
