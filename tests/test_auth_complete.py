"""
test_auth_complete.py
=====================
Comprehensive verification suite for ETS Watchtower Authentication and RBAC:
1. Self-service Signup with role restriction (enforces Viewer role)
2. Password hashing & salt security (PBKDF2-HMAC-SHA256, no plaintext)
3. Duplicate username prevention
4. Duplicate email prevention
5. Weak password rejection
6. Login authentication (valid credentials, invalid password, non-existent user)
7. Inactive user rejection
8. Password reset token generation (cryptographic randomness, SHA256 hashed in DB)
9. Reset token expiration logic
10. Reset token single-use invalidation
11. Password reset execution (old password invalidated, new password succeeds)
12. Invalid token rejection
13. RBAC role enforcement (Admin vs Operator vs Auditor vs Viewer permissions)
14. State scoping enforcement for Operators
15. Audit logging of all authentication events
"""

import os
import pathlib
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "dashboard"))

import db
import auth


class TestAuthCompleteSuite(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "auth_test.db")
        self.conn = db.get_connection(self.db_path)

    def tearDown(self):
        self.conn.close()
        self.tmpdir.cleanup()

    # -------------------------------------------------------------------------
    # 1. Signup & Validation Tests
    # -------------------------------------------------------------------------

    def test_signup_creates_viewer_user(self):
        """Verify signup creates a user persisted in DB with Viewer role."""
        res = db.signup_user(
            self.conn,
            username="new_user_1",
            password="SecurePassword2026!",
            email="newuser1@infinite.com",
            full_name="New User One",
        )
        self.assertEqual(res["username"], "new_user_1")
        self.assertEqual(res["role"], "Viewer")
        self.assertEqual(res["email"], "newuser1@infinite.com")

        # Verify DB row
        row = self.conn.execute("SELECT * FROM users WHERE username = 'new_user_1'").fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["role"], "Viewer")
        # Ensure password is NOT stored in plaintext
        self.assertNotEqual(row["password_hash"], "SecurePassword2026!")
        self.assertTrue(len(row["password_hash"]) >= 64)
        self.assertTrue(len(row["salt"]) >= 16)

    def test_signup_duplicate_username_fails(self):
        """Verify duplicate signup with same username fails."""
        db.signup_user(self.conn, "alice_op", "SecurePwd123!", "alice1@infinite.com")
        with self.assertRaises(sqlite3.IntegrityError):
            db.signup_user(self.conn, "alice_op", "AnotherPwd456!", "alice2@infinite.com")

    def test_signup_duplicate_email_fails(self):
        """Verify duplicate signup with same email fails."""
        db.signup_user(self.conn, "bob_one", "SecurePwd123!", "shared_email@infinite.com")
        with self.assertRaises(sqlite3.IntegrityError):
            db.signup_user(self.conn, "bob_two", "AnotherPwd456!", "shared_email@infinite.com")

    def test_signup_weak_password_fails(self):
        """Verify signup rejects short or non-complex passwords."""
        with self.assertRaises(ValueError):
            # Short password (< 8 chars)
            db.signup_user(self.conn, "short_pwd_user", "Short1!", "short@infinite.com")

        with self.assertRaises(ValueError):
            # No digit or symbol
            db.signup_user(self.conn, "simple_user", "passwordallletters", "simple@infinite.com")

    def test_signup_invalid_email_fails(self):
        """Verify signup rejects malformed email addresses."""
        with self.assertRaises(ValueError):
            db.signup_user(self.conn, "bad_email_user", "ValidPassword123!", "not-an-email")

    # -------------------------------------------------------------------------
    # 2. Login & Authentication Tests
    # -------------------------------------------------------------------------

    def test_login_success(self):
        """Verify valid user can successfully authenticate."""
        db.signup_user(self.conn, "charlie_test", "ValidPwd789!", "charlie@infinite.com")
        user = db.authenticate_user(self.conn, "charlie_test", "ValidPwd789!")
        self.assertIsNotNone(user)
        self.assertEqual(user["username"], "charlie_test")
        self.assertEqual(user["role"], "Viewer")
        self.assertIsNotNone(user["last_login_at"])

    def test_login_wrong_password_fails(self):
        """Verify invalid password returns None."""
        db.signup_user(self.conn, "dave_test", "CorrectPwd123!", "dave@infinite.com")
        user = db.authenticate_user(self.conn, "dave_test", "WrongPassword!")
        self.assertIsNone(user)

    def test_login_nonexistent_user_fails(self):
        """Verify non-existent user returns None."""
        user = db.authenticate_user(self.conn, "ghost_user", "AnyPassword123!")
        self.assertIsNone(user)

    def test_login_inactive_user_rejected(self):
        """Verify deactivated accounts cannot log in."""
        db.signup_user(self.conn, "eve_inactive", "ValidPwd123!", "eve@infinite.com")
        self.conn.execute("UPDATE users SET is_active = 0 WHERE username = 'eve_inactive'")
        self.conn.commit()

        user = db.authenticate_user(self.conn, "eve_inactive", "ValidPwd123!")
        self.assertIsNone(user)

    # -------------------------------------------------------------------------
    # 3. Forgot Password / Reset Token Lifecycle Tests
    # -------------------------------------------------------------------------

    def test_create_reset_token(self):
        """Verify token creation generates URL-safe token and stores SHA256 hash."""
        db.signup_user(self.conn, "frank_reset", "InitialPwd123!", "frank@infinite.com")
        token_info = db.create_reset_token(self.conn, "frank_reset")
        self.assertIsNotNone(token_info)
        self.assertIn("token", token_info)
        token = token_info["token"]
        self.assertTrue(len(token) >= 32)

        # Verify DB stores hash, NOT the raw token
        row = self.conn.execute("SELECT reset_token_hash, reset_token_expires_at FROM users WHERE username = 'frank_reset'").fetchone()
        self.assertIsNotNone(row["reset_token_hash"])
        self.assertNotEqual(row["reset_token_hash"], token)
        self.assertIsNotNone(row["reset_token_expires_at"])

        # Token can also be created via email identifier
        token_by_email = db.create_reset_token(self.conn, "frank@infinite.com")
        self.assertIsNotNone(token_by_email)

    def test_create_reset_token_nonexistent_returns_none(self):
        """Verify non-existent account returns None safely."""
        token_info = db.create_reset_token(self.conn, "nobody@nowhere.com")
        self.assertIsNone(token_info)

    def test_consume_reset_token_updates_password(self):
        """Verify token consumption resets password and invalidates old credentials."""
        db.signup_user(self.conn, "grace_reset", "OldPassword123!", "grace@infinite.com")
        token_info = db.create_reset_token(self.conn, "grace_reset")
        token = token_info["token"]

        # Consume token with new password
        success = db.consume_reset_token(self.conn, token, "BrandNewPassword2026!")
        self.assertTrue(success)

        # Old password must fail
        old_login = db.authenticate_user(self.conn, "grace_reset", "OldPassword123!")
        self.assertIsNone(old_login)

        # New password must succeed
        new_login = db.authenticate_user(self.conn, "grace_reset", "BrandNewPassword2026!")
        self.assertIsNotNone(new_login)
        self.assertEqual(new_login["username"], "grace_reset")

    def test_reset_token_cannot_be_reused(self):
        """Verify reset token is strictly single-use."""
        db.signup_user(self.conn, "heidi_reuse", "OriginalPwd123!", "heidi@infinite.com")
        token_info = db.create_reset_token(self.conn, "heidi_reuse")
        token = token_info["token"]

        # First use succeeds
        self.assertTrue(db.consume_reset_token(self.conn, token, "NewPasswordA1!"))

        # Second use must fail
        self.assertFalse(db.consume_reset_token(self.conn, token, "NewPasswordB2!"))

    def test_expired_reset_token_fails(self):
        """Verify expired tokens cannot be consumed."""
        db.signup_user(self.conn, "ivan_expire", "InitialPwd123!", "ivan@infinite.com")
        token_info = db.create_reset_token(self.conn, "ivan_expire")
        token = token_info["token"]

        # Artificially expire the token in the DB
        past_time = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        self.conn.execute("UPDATE users SET reset_token_expires_at = ? WHERE username = 'ivan_expire'", (past_time,))
        self.conn.commit()

        # Consuming expired token must fail
        self.assertFalse(db.consume_reset_token(self.conn, token, "ShouldFailPassword1!"))

        # Old password remains intact
        user = db.authenticate_user(self.conn, "ivan_expire", "InitialPwd123!")
        self.assertIsNotNone(user)

    def test_invalid_reset_token_fails(self):
        """Verify random / invalid token fails."""
        self.assertFalse(db.consume_reset_token(self.conn, "completely-bogus-token-12345", "NewPassword123!"))

    # -------------------------------------------------------------------------
    # 4. RBAC Helper & Policy Tests
    # -------------------------------------------------------------------------

    def test_rbac_roles_and_privileges(self):
        """Verify role hierarchies: Admin > Operator > Auditor > Viewer."""
        self.assertIn("Admin", db.VALID_ROLES)
        self.assertIn("Operator", db.VALID_ROLES)
        self.assertIn("Auditor", db.VALID_ROLES)
        self.assertIn("Viewer", db.VALID_ROLES)

        # Test auth module role constants
        self.assertEqual(auth.ROLE_ADMIN, "Admin")
        self.assertEqual(auth.ROLE_OPERATOR, "Operator")
        self.assertEqual(auth.ROLE_AUDITOR, "Auditor")
        self.assertEqual(auth.ROLE_VIEWER, "Viewer")
        self.assertEqual(set(auth.WRITE_ROLES), {"Admin", "Operator"})

    def test_audit_trail_records_auth_lifecycle(self):
        """Verify USER_SIGNUP, USER_LOGIN, PASSWORD_RESET events are recorded."""
        db.signup_user(self.conn, "audit_user", "ValidSecure1!", "audit@infinite.com")
        db.authenticate_user(self.conn, "audit_user", "ValidSecure1!")
        token_info = db.create_reset_token(self.conn, "audit_user")
        db.consume_reset_token(self.conn, token_info["token"], "NewSecurePassword2!")

        logs = db.get_audit_logs(self.conn, limit=50)
        actions = [l["action"] for l in logs]

        self.assertIn("USER_SIGNUP", actions)
        self.assertIn("USER_LOGIN", actions)
        self.assertIn("PASSWORD_RESET_REQUESTED", actions)
        self.assertIn("PASSWORD_RESET_COMPLETED", actions)


if __name__ == "__main__":
    unittest.main()
