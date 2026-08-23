import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
import app.main as main
from app.core.config import Settings
from app.core.database import Base
from app.models.user import User


class BootstrapAndConfigTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def tearDown(self):
        self.engine.dispose()

    def test_empty_password_does_not_create_known_admin(self):
        with patch("app.core.database.SessionLocal", self.Session), patch.object(
            main.settings, "BOOTSTRAP_ADMIN_PASSWORD", ""
        ):
            main._ensure_default_admin()

        db = self.Session()
        self.assertEqual(db.query(User).count(), 0)
        db.close()

    def test_explicit_bootstrap_password_creates_first_admin_only(self):
        with patch("app.core.database.SessionLocal", self.Session), patch.object(
            main.settings, "BOOTSTRAP_ADMIN_USERNAME", "owner"
        ), patch.object(main.settings, "BOOTSTRAP_ADMIN_EMAIL", "owner@example.com"), patch.object(
            main.settings, "BOOTSTRAP_ADMIN_PASSWORD", "private-pass"
        ):
            main._ensure_default_admin()
            main._ensure_default_admin()

        db = self.Session()
        users = db.query(User).all()
        self.assertEqual(len(users), 1)
        self.assertEqual(users[0].username, "owner")
        self.assertEqual(users[0].email, "owner@example.com")
        self.assertNotEqual(users[0].password_hash, "private-pass")
        db.close()

    def test_existing_user_prevents_surprise_bootstrap_account(self):
        db = self.Session()
        db.add(User(username="existing", email="existing@example.com", password_hash="x"))
        db.commit()
        db.close()

        with patch("app.core.database.SessionLocal", self.Session), patch.object(
            main.settings, "BOOTSTRAP_ADMIN_PASSWORD", "admin123"
        ):
            main._ensure_default_admin()

        db = self.Session()
        self.assertEqual(db.query(User).count(), 1)
        self.assertIsNone(db.query(User).filter_by(email="admin@market.my").first())
        db.close()

    def test_runtime_settings_reject_invalid_ports_timeout_schedule_and_database(self):
        with self.assertRaises(ValueError):
            Settings(_env_file=None, BROWSER_CDP_PORT=70000)
        with self.assertRaises(ValueError):
            Settings(_env_file=None, COLLECTION_TIMEOUT_SECONDS=2)
        with self.assertRaises(ValueError):
            Settings(_env_file=None, DEFAULT_DAILY_TIME="25:99")
        with self.assertRaises(ValueError):
            Settings(_env_file=None, DEFAULT_TIMEZONE="Not/A_Timezone")
        with self.assertRaises(ValueError):
            Settings(_env_file=None, DATABASE_TYPE="postgres")

    def test_database_type_is_normalized(self):
        settings = Settings(_env_file=None, DATABASE_TYPE="MySQL")
        self.assertEqual(settings.DATABASE_TYPE, "mysql")

    def test_default_malaysia_timezone_is_resolvable(self):
        settings = Settings(_env_file=None)
        self.assertEqual(settings.DEFAULT_TIMEZONE, "Asia/Kuala_Lumpur")
        self.assertEqual(settings.DEFAULT_DAILY_TIME, "20:00")


if __name__ == "__main__":
    unittest.main()
