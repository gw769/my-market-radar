import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi import HTTPException

from app.api import auth as auth_api
from app.core.security import create_access_token, get_current_user, get_password_hash, verify_password
from app.schemas.user import UserCreate


class SecurityTests(unittest.TestCase):
    def test_password_hash_and_verify_use_same_bcrypt_byte_limit(self):
        password = "密" * 40
        hashed = get_password_hash(password)
        self.assertTrue(verify_password(password, hashed))
        self.assertFalse(verify_password("different-password", hashed))

    def test_invalid_password_hash_returns_false_instead_of_500(self):
        self.assertFalse(verify_password("secret", "not-a-valid-bcrypt-hash"))

    def test_non_integer_subject_is_401_not_internal_error(self):
        token = create_access_token({"sub": "not-an-integer"})
        credentials = SimpleNamespace(credentials=token)
        db = Mock()

        with self.assertRaises(HTTPException) as ctx:
            get_current_user(credentials=credentials, db=db)

        self.assertEqual(ctx.exception.status_code, 401)
        db.query.assert_not_called()

    def test_self_registration_is_disabled_by_default(self):
        payload = UserCreate(username="someone", email="someone@example.com", password="secret123")
        db = Mock()
        with patch.object(auth_api.settings, "ALLOW_REGISTRATION", False):
            with self.assertRaises(HTTPException) as ctx:
                auth_api.register(payload, db=db)
        self.assertEqual(ctx.exception.status_code, 403)
        db.query.assert_not_called()


if __name__ == "__main__":
    unittest.main()
