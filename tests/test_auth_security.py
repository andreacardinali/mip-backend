import asyncio
import unittest
from unittest.mock import MagicMock, patch

import jwt
from fastapi import HTTPException

from auth.security import get_current_user, require_admin


class GetCurrentUserTests(unittest.TestCase):
    @staticmethod
    def _row(*, is_active: bool = True, roles=None):
        return {
            "user_id": "user-123",
            "username": "andrea",
            "email": "andrea@example.test",
            "display_name": "Andrea",
            "is_active": is_active,
            "reporting_currency": "EUR",
            "roles": roles if roles is not None else ["USER"],
        }

    @staticmethod
    def _session(row):
        db = MagicMock()
        db.execute.return_value.mappings.return_value.one_or_none.return_value = row
        return db

    @staticmethod
    def _call(token: str = "token"):
        return asyncio.run(get_current_user(token))

    @patch("auth.security.SessionLocal")
    @patch("auth.security.jwt.decode")
    def test_active_user_is_returned(self, decode, session_local):
        decode.return_value = {"sub": "user-123"}
        db = self._session(self._row(is_active=True, roles=["ADMIN", "USER"]))
        session_local.return_value = db

        result = self._call()

        self.assertEqual(
            result,
            {
                "user_id": "user-123",
                "username": "andrea",
                "email": "andrea@example.test",
                "display_name": "Andrea",
                "is_active": True,
                "reporting_currency": "EUR",
                "roles": ["ADMIN", "USER"],
            },
        )
        decode.assert_called_once()
        db.execute.assert_called_once()
        db.close.assert_called_once_with()

    @patch("auth.security.SessionLocal")
    @patch("auth.security.jwt.decode")
    def test_roles_default_to_empty_list(self, decode, session_local):
        decode.return_value = {"sub": "user-123"}
        row = self._row()
        row["roles"] = None
        db = self._session(row)
        session_local.return_value = db

        result = self._call()

        self.assertEqual(result["roles"], [])
        db.close.assert_called_once_with()

    @patch("auth.security.SessionLocal")
    @patch("auth.security.jwt.decode")
    def test_unknown_user_returns_401_and_closes_session(
        self,
        decode,
        session_local,
    ):
        decode.return_value = {"sub": "missing-user"}
        db = self._session(None)
        session_local.return_value = db

        with self.assertRaises(HTTPException) as caught:
            self._call()

        self.assertEqual(caught.exception.status_code, 401)
        self.assertEqual(caught.exception.detail, "User not found")
        db.close.assert_called_once_with()

    @patch("auth.security.SessionLocal")
    @patch("auth.security.jwt.decode")
    def test_inactive_user_returns_403_and_closes_session(
        self,
        decode,
        session_local,
    ):
        decode.return_value = {"sub": "user-123"}
        db = self._session(self._row(is_active=False))
        session_local.return_value = db

        with self.assertRaises(HTTPException) as caught:
            self._call()

        self.assertEqual(caught.exception.status_code, 403)
        self.assertEqual(caught.exception.detail, "User inactive")
        db.close.assert_called_once_with()

    @patch("auth.security.SessionLocal")
    @patch("auth.security.jwt.decode")
    def test_token_without_subject_returns_401_without_opening_session(
        self,
        decode,
        session_local,
    ):
        decode.return_value = {}

        with self.assertRaises(HTTPException) as caught:
            self._call()

        self.assertEqual(caught.exception.status_code, 401)
        self.assertEqual(caught.exception.detail, "Invalid token")
        session_local.assert_not_called()

    @patch("auth.security.SessionLocal")
    @patch("auth.security.jwt.decode")
    def test_invalid_token_returns_401_without_opening_session(
        self,
        decode,
        session_local,
    ):
        decode.side_effect = jwt.InvalidTokenError("invalid")

        with self.assertRaises(HTTPException) as caught:
            self._call("invalid-token")

        self.assertEqual(caught.exception.status_code, 401)
        self.assertEqual(caught.exception.detail, "Invalid token")
        session_local.assert_not_called()

    @patch("auth.security.SessionLocal")
    @patch("auth.security.jwt.decode")
    def test_database_error_still_closes_session(
        self,
        decode,
        session_local,
    ):
        decode.return_value = {"sub": "user-123"}
        db = MagicMock()
        db.execute.side_effect = RuntimeError("database unavailable")
        session_local.return_value = db

        with self.assertRaisesRegex(RuntimeError, "database unavailable"):
            self._call()

        db.close.assert_called_once_with()


class RequireAdminTests(unittest.TestCase):
    @staticmethod
    def _user(roles):
        return {
            "user_id": "user-123",
            "username": "andrea",
            "email": "andrea@example.test",
            "display_name": "Andrea",
            "is_active": True,
            "reporting_currency": "EUR",
            "roles": roles,
        }

    def test_admin_is_returned(self):
        user = self._user(["ADMIN", "USER"])

        result = asyncio.run(require_admin(user))

        self.assertIs(result, user)

    def test_non_admin_returns_403(self):
        with self.assertRaises(HTTPException) as caught:
            asyncio.run(require_admin(self._user(["USER"])))

        self.assertEqual(caught.exception.status_code, 403)
        self.assertEqual(caught.exception.detail, "Administrator role required")


if __name__ == "__main__":
    unittest.main()
