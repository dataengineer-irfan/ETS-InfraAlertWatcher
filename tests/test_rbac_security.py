"""
test_rbac_security.py
=====================
Automated verification suite for:
  1. SSRF Protection & Scheme Validation (src/fetch_excel.py)
  2. Jinja2 HTML Autoescape & XSS Neutralization (src/notifier.py)
  3. SQLite WAL Mode & Concurrency Configuration (src/db.py)
  4. PBKDF2-HMAC-SHA256 User Password Hashing & Verification (src/db.py)
  5. Role-Based Access Control (RBAC) Provisioning & Revocation (src/db.py)
  6. Immutable Audit Trail Logging & Filtering (src/db.py)
  7. Keep-Alive Ping Verification (scripts/keepalive.py)
"""

import os
import pathlib
import sqlite3
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "dashboard"))

import db
import fetch_excel
import notifier
import keepalive


class TestSecurityHardening(unittest.TestCase):

    def test_ssrf_and_scheme_rejection(self):
        """Verify fetch_excel rejects non-HTTPS schemes and internal/metadata IPs."""
        # 1. Non-HTTPS scheme
        with self.assertRaises(ValueError) as ctx:
            fetch_excel.validate_url("http://example.com/data.xlsx")
        self.assertIn("Only HTTPS is permitted", str(ctx.exception))

        with self.assertRaises(ValueError) as ctx:
            fetch_excel.validate_url("file:///etc/passwd")
        self.assertIn("Only HTTPS is permitted", str(ctx.exception))

        # 2. Loopback
        with self.assertRaises(ValueError) as ctx:
            fetch_excel.validate_url("https://127.0.0.1/data.xlsx")
        self.assertIn("SSRF guard", str(ctx.exception))

        # 3. Localhost hostname
        with self.assertRaises(ValueError) as ctx:
            fetch_excel.validate_url("https://localhost/data.xlsx")
        self.assertIn("SSRF guard", str(ctx.exception))

        # 4. Cloud metadata IP
        with self.assertRaises(ValueError) as ctx:
            fetch_excel.validate_url("https://169.254.169.254/latest/meta-data")
        self.assertIn("SSRF guard", str(ctx.exception))

    def test_jinja2_html_autoescape(self):
        """Verify HTML injection is escaped in email templates."""
        malicious_record = {
            "username": "<script>alert('xss')</script>",
            "schema_name": "CORE_SCHEMA",
            "state": "AK",
            "env": "PROD",
            "exp_date": "2026-09-15",
            "days_left": 5,
            "owner_name": "Admin <img src=x onerror=alert(1)>",
            "is_first_reminder": True,
        }
        rendered = notifier.render_email(malicious_record)
        self.assertNotIn("<script>", rendered)
        self.assertIn("&lt;script&gt;", rendered)
        self.assertNotIn("<img src=x", rendered)
        self.assertIn("&lt;img src=x", rendered)

    def test_wal_mode_and_schema(self):
        """Verify SQLite connections enable WAL mode and create required tables."""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_db = os.path.join(tmpdir, "test_security.db")
            conn = db.get_connection(test_db)

            # Check journal mode
            mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
            self.assertEqual(mode.lower(), "wal")

            # Check tables
            tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
            self.assertIn("users", tables)
            self.assertIn("audit_log", tables)

            # Default admin should be provisioned
            users = db.get_users(conn)
            self.assertTrue(any(u["username"] == "admin" and u["role"] == "Admin" for u in users))

            # Initial audit entry
            logs = db.get_audit_logs(conn)
            self.assertTrue(len(logs) >= 1)
            self.assertEqual(logs[0]["action"], "SYSTEM_INITIALIZATION")
            conn.close()


class TestRBACAndAuditSubsystem(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "rbac_test.db")
        self.conn = db.get_connection(self.db_path)

    def tearDown(self):
        self.conn.close()
        self.tmpdir.cleanup()

    def test_password_hashing(self):
        """Verify PBKDF2 hashing produces distinct salts and validates correctly."""
        h1, s1 = db.hash_password("Secr3tP@ssword!")
        h2, s2 = db.hash_password("Secr3tP@ssword!")
        # Different salts must yield different hashes
        self.assertNotEqual(s1, s2)
        self.assertNotEqual(h1, h2)

        # Verification must succeed for correct password
        self.assertTrue(db.verify_password("Secr3tP@ssword!", h1, s1))
        self.assertTrue(db.verify_password("Secr3tP@ssword!", h2, s2))

        # Verification must fail for wrong password
        self.assertFalse(db.verify_password("WrongPassword", h1, s1))

    def test_user_lifecycle(self):
        """Verify user creation, role modification, and deletion."""
        # 1. Create user
        user = db.create_user(
            self.conn,
            username="analyst1",
            password="StrongPassword123!",
            role="Auditor",
            full_name="Alex Auditor",
            email="alex@ets.internal",
        )
        self.assertEqual(user["username"], "analyst1")
        self.assertEqual(user["role"], "Auditor")

        # 2. Check get_users
        users = db.get_users(self.conn)
        usernames = [u["username"] for u in users]
        self.assertIn("analyst1", usernames)

        # 3. Update role
        db.update_user_role(self.conn, "analyst1", "Operator")
        updated_users = db.get_users(self.conn)
        analyst = next(u for u in updated_users if u["username"] == "analyst1")
        self.assertEqual(analyst["role"], "Operator")

        # 4. Delete user
        self.assertTrue(db.delete_user(self.conn, "analyst1"))
        final_users = db.get_users(self.conn)
        self.assertNotIn("analyst1", [u["username"] for u in final_users])

    def test_authenticate_user(self):
        """Verify user authentication, password check, and audit trail recording."""
        # 1. Successful authentication with seeded admin
        auth_admin = db.authenticate_user(self.conn, "admin", "Admin@ETS2026!", ip_address="192.168.1.10")
        self.assertIsNotNone(auth_admin)
        self.assertEqual(auth_admin["username"], "admin")
        self.assertEqual(auth_admin["role"], "Admin")

        # 2. Case-insensitive username support
        auth_admin_upper = db.authenticate_user(self.conn, "ADMIN", "Admin@ETS2026!")
        self.assertIsNotNone(auth_admin_upper)

        # 3. Invalid password
        bad_pwd = db.authenticate_user(self.conn, "admin", "WrongPassword123!")
        self.assertIsNone(bad_pwd)

        # 4. Non-existent username
        bad_user = db.authenticate_user(self.conn, "non_existent_user", "AnyPassword123!")
        self.assertIsNone(bad_user)

        # 5. Deactivated user
        db.create_user(self.conn, "inactive_user", "ValidPassword123!", role="Viewer")
        self.conn.execute("UPDATE users SET is_active = 0 WHERE username = 'inactive_user'")
        self.conn.commit()
        deactivated = db.authenticate_user(self.conn, "inactive_user", "ValidPassword123!")
        self.assertIsNone(deactivated)

        # 6. Audit log validation
        logs = db.get_audit_logs(self.conn, limit=20)
        login_actions = [l["action"] for l in logs]
        self.assertIn("USER_LOGIN", login_actions)
        self.assertIn("LOGIN_FAILED", login_actions)

    def test_audit_event_logging_and_filtering(self):
        """Verify audit logging records events and respects action filters."""
        db.log_audit_event(
            self.conn,
            actor="test_admin",
            role="Admin",
            action="EXPIRY_EDITED",
            target_entity="Component #42",
            details="Changed expiry date to 2026-10-31",
            ip_address="10.0.0.5",
        )
        db.log_audit_event(
            self.conn,
            actor="test_admin",
            role="Admin",
            action="USER_CREATED",
            target_entity="User: jdoe",
            details="Provisioned Operator account",
            ip_address="10.0.0.5",
        )

        all_logs = db.get_audit_logs(self.conn, limit=50)
        self.assertTrue(len(all_logs) >= 3)  # init + 2 events

        expiry_logs = db.get_audit_logs(self.conn, action_filter="EXPIRY_EDITED")
        self.assertEqual(len(expiry_logs), 1)
        self.assertEqual(expiry_logs[0]["target_entity"], "Component #42")

        user_logs = db.get_audit_logs(self.conn, action_filter="USER_CREATED")
        self.assertEqual(len(user_logs), 1)
        self.assertEqual(user_logs[0]["target_entity"], "User: jdoe")


class TestKeepaliveUtility(unittest.TestCase):

    def test_keepalive_ping_against_local(self):
        """Verify keepalive script ping returns status and latency."""
        success, code, latency = keepalive.ping_health("http://localhost:8501", timeout=5)
        # Server is running on 8501
        self.assertTrue(success)
        self.assertEqual(code, 200)
        self.assertGreater(latency, 0)


if __name__ == "__main__":
    unittest.main()
