import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from fastapi import HTTPException

from app.core.security import create_access_token, get_current_user, get_password_hash, verify_password


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


if __name__ == "__main__":
    unittest.main()
