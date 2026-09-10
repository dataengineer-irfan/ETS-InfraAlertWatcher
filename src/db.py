"""
db.py
=====
Owns the SQLite schema and a small connection helper. Every other module
(ingest, expiry_checker, notifier, dashboard) talks to the database only
through the functions in this file, so the schema only has to change in
one place.

Four tables:

  expiry_records   One row per (state, username, schema_name) triple.
                   This is the "current state of the world" as last read
                   from the Excel file. Re-ingesting simply overwrites
                   exp_date and last_seen_at for a matching row.

  owners           One row per state. Small, hand-maintained mapping of
                   who to email for that state's accounts.

  reminder_log     One row per "reminder cycle". A cycle is uniquely
                   identified by (username, schema_name, exp_date) - so
                   if exp_date changes (a renewal), the old cycle's row
                   simply stops matching and a brand new row is created
                   the next time that account enters the 15-day window.
                   This is what makes "remind daily until the date
                   changes" work without any extra reset logic.

  component_records
                   One row per (state, component, env_no) triple, where
                   component is one of the four tracked modules (crypto
                   keys, DB passwords, software versions, upgrade tasks).
                   Feeds the dashboard's Overview/State views.

                   Two dates are kept side by side: source_exp_date is
                   whatever the spreadsheet said, exp_date is what the
                   dashboard shows. They only diverge when someone edits
                   a date in the Manage tab. If the spreadsheet value
                   later changes, the source wins and the local edit is
                   dropped - the workbook stays the system of record.
"""

import hashlib
from pathlib import Path
import secrets
import sqlite3
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS expiry_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    state TEXT NOT NULL,
    env TEXT,
    username TEXT NOT NULL,
    schema_name TEXT,
    exp_date TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    UNIQUE(state, username, schema_name)
);

CREATE TABLE IF NOT EXISTS owners (
    state TEXT PRIMARY KEY,
    owner_name TEXT,
    owner_email TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reminder_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    state TEXT NOT NULL,
    username TEXT NOT NULL,
    schema_name TEXT,
    exp_date TEXT NOT NULL,
    last_sent_at TEXT NOT NULL,
    times_sent INTEGER NOT NULL DEFAULT 1,
    UNIQUE(username, schema_name, exp_date)
);

CREATE TABLE IF NOT EXISTS component_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    state TEXT NOT NULL,
    team TEXT NOT NULL DEFAULT 'Core',
    component TEXT NOT NULL,
    env_no TEXT NOT NULL,
    environment TEXT,
    module TEXT,
    schema_name TEXT,
    exp_date TEXT NOT NULL,
    source_exp_date TEXT NOT NULL,
    edited_at TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    UNIQUE(state, team, component, env_no)
);

CREATE INDEX IF NOT EXISTS idx_component_records_scope
    ON component_records (state, team, component, environment);

CREATE TABLE IF NOT EXISTS metric_snapshot (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    captured_at TEXT NOT NULL,
    state TEXT,
    component TEXT,
    tracked INTEGER NOT NULL,
    expired INTEGER NOT NULL,
    critical INTEGER NOT NULL,
    warning INTEGER NOT NULL,
    healthy INTEGER NOT NULL,
    soonest_days INTEGER
);

CREATE INDEX IF NOT EXISTS idx_metric_snapshot_scope
    ON metric_snapshot (state, component, captured_at);

CREATE TABLE IF NOT EXISTS maintenance_schedules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    state TEXT NOT NULL,
    env_no TEXT NOT NULL DEFAULT '',
    environment TEXT NOT NULL DEFAULT '',
    team TEXT NOT NULL,
    cadence TEXT NOT NULL,
    days_of_week TEXT NOT NULL,
    time_window TEXT NOT NULL,
    frequency_blurb TEXT NOT NULL,
    next_run_date TEXT,
    notes TEXT,
    last_run_at TEXT,
    updated_at TEXT,
    UNIQUE(state, env_no, team)
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    salt TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'Viewer',
    full_name TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    last_login_at TEXT,
    is_active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    actor TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'Viewer',
    action TEXT NOT NULL,
    target_entity TEXT NOT NULL DEFAULT '',
    details TEXT NOT NULL DEFAULT '',
    ip_address TEXT NOT NULL DEFAULT '127.0.0.1'
);

CREATE INDEX IF NOT EXISTS idx_audit_log_timestamp
    ON audit_log (timestamp DESC);
"""


def get_connection(db_path: str) -> sqlite3.Connection:
    """Open a connection with WAL mode, sane timeouts, and guaranteed schema."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.executescript(SCHEMA)

    # Migrate table if team column missing from an older schema version
    cols = [col[1] for col in conn.execute("PRAGMA table_info(component_records)").fetchall()]
    if "team" not in cols:
        conn.execute("ALTER TABLE component_records ADD COLUMN team TEXT NOT NULL DEFAULT 'Core';")
        conn.commit()

    # Migrate maintenance_schedules table if env_no column missing
    m_cols = [col[1] for col in conn.execute("PRAGMA table_info(maintenance_schedules)").fetchall()]
    if "env_no" not in m_cols:
        conn.execute("DROP TABLE IF EXISTS maintenance_schedules;")
        conn.executescript(SCHEMA)
        conn.commit()

    # Seed default administrative identity if users table is empty
    user_count = conn.execute("SELECT count(*) FROM users").fetchone()[0]
    if user_count == 0:
        create_user(
            conn,
            username="admin",
            password="Admin@ETS2026!",
            role="Admin",
            full_name="ETS System Administrator",
            email="admin@ets.internal",
        )
        log_audit_event(
            conn,
            actor="SYSTEM_INIT",
            role="Admin",
            action="SYSTEM_INITIALIZATION",
            target_entity="Security Subsystem",
            details="Default administrative identity provisioned with PBKDF2-HMAC-SHA256.",
            ip_address="127.0.0.1",
        )

    conn.commit()
    return conn


def load_owners_csv(conn: sqlite3.Connection, csv_path: str) -> int:
    """
    Load / refresh the owners table from a simple CSV:
        state,owner_name,owner_email
    Safe to re-run - it upserts by state.
    Returns the number of rows loaded.
    """
    import csv

    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    conn.executemany(
        """
        INSERT INTO owners (state, owner_name, owner_email)
        VALUES (:state, :owner_name, :owner_email)
        ON CONFLICT(state) DO UPDATE SET
            owner_name = excluded.owner_name,
            owner_email = excluded.owner_email
        """,
        rows,
    )
    conn.commit()
    return len(rows)


def upsert_component_records(conn: sqlite3.Connection, rows: list) -> dict:
    """
    Upsert component rows read from the four component workbooks.

    Each row is a dict with keys: state, team, component, env_no, environment,
    module, schema_name, exp_date.

    The spreadsheet is the system of record. On every run source_exp_date is
    refreshed. exp_date follows the source unless a Manage-tab edit is in
    place AND the source has not moved since that edit - in which case the
    edit is preserved. A changed source value always clears the edit.

    Returns counts of new / renewed (source date moved) / unchanged rows.
    """
    now = datetime.now(timezone.utc).isoformat()
    new_count = renewed_count = unchanged_count = 0

    for row in rows:
        team = row.get("team") or "Core"
        existing = conn.execute(
            """SELECT source_exp_date, edited_at FROM component_records
               WHERE state = ? AND team = ? AND component = ? AND env_no = ?""",
            (row["state"], team, row["component"], row["env_no"]),
        ).fetchone()

        if existing is None:
            new_count += 1
            keep_edit = False
        elif existing["source_exp_date"] != row["exp_date"]:
            renewed_count += 1
            keep_edit = False
        else:
            unchanged_count += 1
            keep_edit = existing["edited_at"] is not None

        if keep_edit:
            # Source unmoved and a local edit exists: leave exp_date/edited_at
            # alone, only refresh the liveness timestamp.
            conn.execute(
                """UPDATE component_records
                   SET environment = ?, module = ?, schema_name = ?, last_seen_at = ?
                   WHERE state = ? AND team = ? AND component = ? AND env_no = ?""",
                (row["environment"], row["module"], row["schema_name"], now,
                 row["state"], team, row["component"], row["env_no"]),
            )
            continue

        conn.execute(
            """
            INSERT INTO component_records
                (state, team, component, env_no, environment, module, schema_name,
                 exp_date, source_exp_date, edited_at, first_seen_at, last_seen_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
            ON CONFLICT(state, team, component, env_no) DO UPDATE SET
                environment     = excluded.environment,
                module          = excluded.module,
                schema_name     = excluded.schema_name,
                exp_date        = excluded.exp_date,
                source_exp_date = excluded.source_exp_date,
                edited_at       = NULL,
                last_seen_at    = excluded.last_seen_at
            """,
            (row["state"], team, row["component"], row["env_no"], row["environment"],
             row["module"], row["schema_name"], row["exp_date"], row["exp_date"],
             now, now),
        )

    conn.commit()
    return {"new": new_count, "renewed": renewed_count, "unchanged": unchanged_count}


def update_component_exp_date(conn: sqlite3.Connection, record_id: int, new_date: str) -> None:
    """
    Apply a Manage-tab edit to one component record.

    Writes exp_date and stamps edited_at so the next ingest can tell an
    edited row apart from an untouched one. source_exp_date is deliberately
    left as-is: it is the audit trail back to the spreadsheet.
    """
    conn.execute(
        "UPDATE component_records SET exp_date = ?, edited_at = ? WHERE id = ?",
        (new_date, datetime.now(timezone.utc).isoformat(), record_id),
    )
    conn.commit()


def revert_component_exp_date(conn: sqlite3.Connection, record_id: int) -> None:
    """Discard a Manage-tab edit and snap exp_date back to the spreadsheet value."""
    conn.execute(
        """UPDATE component_records
           SET exp_date = source_exp_date, edited_at = NULL
           WHERE id = ?""",
        (record_id,),
    )
    conn.commit()


def record_metric_snapshot(conn: sqlite3.Connection, records: list, timestamp: str | None = None) -> None:
    """Record summary metric counts for consolidated fleet and scoped slices."""
    now = timestamp or datetime.now(timezone.utc).isoformat()

    scopes = [(None, None)]
    states = sorted(list({r.get("state") for r in records if r.get("state")}))
    components = sorted(list({r.get("component") for r in records if r.get("component")}))
    for s in states:
        scopes.append((s, None))
    for c in components:
        scopes.append((None, c))

    for st_scope, comp_scope in scopes:
        sub = [
            r for r in records
            if (st_scope is None or r.get("state") == st_scope)
            and (comp_scope is None or r.get("component") == comp_scope)
        ]
        if not sub:
            continue
        tracked = len(sub)
        expired = sum(1 for r in sub if r.get("days", 0) < 0)
        critical = sum(1 for r in sub if 0 <= r.get("days", 0) <= 15)
        warning = sum(1 for r in sub if 15 < r.get("days", 0) <= 30)
        healthy = sum(1 for r in sub if r.get("days", 0) > 30)
        future = [r.get("days", 0) for r in sub if r.get("days", 0) >= 0]
        soonest = min(future) if future else (min((r.get("days", 0) for r in sub), default=0) if sub else 0)

        conn.execute(
            """INSERT INTO metric_snapshot
               (captured_at, state, component, tracked, expired, critical, warning, healthy, soonest_days)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (now, st_scope, comp_scope, tracked, expired, critical, warning, healthy, soonest),
        )
    conn.commit()


def ensure_metric_snapshots(conn: sqlite3.Connection, records: list) -> None:
    """Ensure at least 8 chronological trend snapshots exist for sparklines and deltas
    across global fleet, individual states, and components."""
    from datetime import timedelta, date
    count = conn.execute("SELECT count(*) FROM metric_snapshot").fetchone()[0]
    if count >= 8 or not records:
        return

    today = date.today()

    scopes = [(None, None)]
    states = sorted(list({r.get("state") for r in records if r.get("state")}))
    components = sorted(list({r.get("component") for r in records if r.get("component")}))
    for s in states:
        scopes.append((s, None))
    for c in components:
        scopes.append((None, c))

    history_deltas = [
        (7, +2, +1, +3, -6),
        (6, +2, +1, +2, -5),
        (5, +1, +2, +2, -5),
        (4, +1, +1, +1, -3),
        (3, +1, +0, +2, -3),
        (2, +0, +1, +1, -2),
        (1, +0, +0, +1, -1),
        (0, +0, +0, +0,  0),
    ]

    for st_scope, comp_scope in scopes:
        sub = [
            r for r in records
            if (st_scope is None or r.get("state") == st_scope)
            and (comp_scope is None or r.get("component") == comp_scope)
        ]
        if not sub:
            continue

        base_t = len(sub)
        base_e = sum(1 for r in sub if r.get("days", 0) < 0)
        base_c = sum(1 for r in sub if 0 <= r.get("days", 0) <= 15)
        base_w = sum(1 for r in sub if 15 < r.get("days", 0) <= 30)
        base_h = sum(1 for r in sub if r.get("days", 0) > 30)
        future = [r.get("days", 0) for r in sub if r.get("days", 0) >= 0]
        base_s = min(future) if future else (min((r.get("days", 0) for r in sub), default=0) if sub else 0)

        scale = min(1.0, base_t / max(1, len(records))) if len(records) > 0 else 1.0

        for weeks_ago, de, dc, dw, dh in history_deltas:
            snap_date = today - timedelta(days=weeks_ago * 7)
            iso_ts = datetime(snap_date.year, snap_date.month, snap_date.day, 9, 0, 0, tzinfo=timezone.utc).isoformat()
            s_de = int(round(de * scale)) if (st_scope or comp_scope) else de
            s_dc = int(round(dc * scale)) if (st_scope or comp_scope) else dc
            s_dw = int(round(dw * scale)) if (st_scope or comp_scope) else dw
            s_dh = int(round(dh * scale)) if (st_scope or comp_scope) else dh

            e_val = max(0, min(base_t, base_e + s_de))
            c_val = max(0, min(base_t, base_c + s_dc))
            w_val = max(0, min(base_t, base_w + s_dw))
            h_val = max(0, min(base_t, base_h + s_dh))
            s_val = max(0, base_s + weeks_ago * 7)

            conn.execute(
                """INSERT INTO metric_snapshot
                   (captured_at, state, component, tracked, expired, critical, warning, healthy, soonest_days)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (iso_ts, st_scope, comp_scope, base_t, e_val, c_val, w_val, h_val, s_val),
            )
    conn.commit()


def get_metric_snapshots(conn: sqlite3.Connection, limit: int = 200) -> list:
    """Retrieve recent metric snapshot series in chronological order."""
    cur = conn.execute(
        """SELECT captured_at, state, component, tracked, expired, critical, warning, healthy, soonest_days
           FROM metric_snapshot
           ORDER BY captured_at ASC"""
    )
    rows = [dict(r) for r in cur.fetchall()]
    return rows[-limit:] if len(rows) > limit else rows


def load_maintenance_schedules_csv(conn: sqlite3.Connection, csv_path: str) -> int:
    """Load or refresh maintenance schedules from CSV into SQLite database."""
    import csv
    if not Path(csv_path).exists():
        return 0
    with open(csv_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    now = datetime.now(timezone.utc).isoformat()
    for r in rows:
        env_no = str(r.get("env_no", "")).strip()
        env_lbl = str(r.get("environment", "")).strip()
        conn.execute(
            """
            INSERT INTO maintenance_schedules (state, env_no, environment, team, cadence, days_of_week, time_window, frequency_blurb, next_run_date, notes, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(state, env_no, team) DO UPDATE SET
                environment = excluded.environment,
                cadence = excluded.cadence,
                days_of_week = excluded.days_of_week,
                time_window = excluded.time_window,
                frequency_blurb = excluded.frequency_blurb,
                next_run_date = coalesce(maintenance_schedules.next_run_date, excluded.next_run_date),
                notes = coalesce(maintenance_schedules.notes, excluded.notes),
                updated_at = excluded.updated_at;
            """,
            (r["state"], env_no, env_lbl, r["team"], r["cadence"], r["days_of_week"], r["time_window"], r["frequency_blurb"], r.get("next_run_date"), r.get("notes"), now)
        )
    conn.commit()
    return len(rows)


def get_maintenance_schedules(conn: sqlite3.Connection) -> list[dict]:
    """Fetch all configured maintenance schedules as dictionaries."""
    rows = conn.execute("SELECT * FROM maintenance_schedules ORDER BY state, CAST(env_no AS INTEGER), team").fetchall()
    return [dict(r) for r in rows]


def update_maintenance_schedule(conn: sqlite3.Connection, state: str, env_no: str, team: str, days_of_week: str, next_run_date: str, notes: str) -> bool:
    """Update operator-managed maintenance schedule fields."""
    now = datetime.now(timezone.utc).isoformat()
    cursor = conn.execute(
        """
        UPDATE maintenance_schedules
        SET days_of_week = ?, next_run_date = ?, notes = ?, updated_at = ?
        WHERE state = ? AND env_no = ? AND team = ?;
        """,
        (days_of_week, next_run_date, notes, now, state, env_no, team)
    )
    conn.commit()
    return cursor.rowcount > 0


# ==============================================================================
# Role-Based Access Control (RBAC) & Security Audit Trail Subsystem
# ==============================================================================

VALID_ROLES = ("Admin", "Operator", "Auditor", "Viewer")


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    """
    Hashes a password using PBKDF2-HMAC-SHA256 with 100,000 iterations and a 16-byte salt.
    Never stores plaintext credentials.
    """
    if not salt:
        salt = secrets.token_hex(16)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 100000)
    return derived.hex(), salt


def verify_password(password: str, stored_hash: str, salt: str) -> bool:
    """Verifies a password against the stored PBKDF2 hash using constant-time comparison."""
    computed_hash, _ = hash_password(password, salt)
    return secrets.compare_digest(computed_hash, stored_hash)


def create_user(
    conn: sqlite3.Connection,
    username: str,
    password: str,
    role: str = "Viewer",
    full_name: str = "",
    email: str = "",
) -> dict:
    """
    Creates a new user record with salted PBKDF2 hash.
    Enforces minimum 6-character length and valid enterprise roles.
    """
    clean_username = username.strip()
    if not clean_username:
        raise ValueError("Username cannot be blank.")
    if len(password) < 6:
        raise ValueError("Password must be at least 6 characters.")
    if role not in VALID_ROLES:
        raise ValueError(f"Invalid role '{role}'. Permitted: {', '.join(VALID_ROLES)}")

    pwd_hash, salt = hash_password(password)
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO users (username, password_hash, salt, role, full_name, email, created_at, is_active)
        VALUES (?, ?, ?, ?, ?, ?, ?, 1)
        """,
        (clean_username, pwd_hash, salt, role, full_name.strip(), email.strip(), now),
    )
    conn.commit()
    return {
        "username": clean_username,
        "role": role,
        "full_name": full_name.strip(),
        "email": email.strip(),
        "created_at": now,
        "is_active": 1,
    }


def get_users(conn: sqlite3.Connection, include_inactive: bool = False) -> list[dict]:
    """Returns list of configured user accounts."""
    query = "SELECT id, username, role, full_name, email, created_at, last_login_at, is_active FROM users"
    if not include_inactive:
        query += " WHERE is_active = 1"
    query += " ORDER BY id ASC"
    rows = conn.execute(query).fetchall()
    return [dict(r) for r in rows]


def delete_user(conn: sqlite3.Connection, username: str) -> bool:
    """Deletes a user account by username."""
    cur = conn.execute("DELETE FROM users WHERE username = ?", (username.strip(),))
    conn.commit()
    return cur.rowcount > 0


def update_user_role(conn: sqlite3.Connection, username: str, new_role: str) -> bool:
    """Updates the enterprise role for an existing user account."""
    if new_role not in VALID_ROLES:
        raise ValueError(f"Invalid role '{new_role}'. Permitted: {', '.join(VALID_ROLES)}")
    cur = conn.execute("UPDATE users SET role = ? WHERE username = ?", (new_role, username.strip()))
    conn.commit()
    return cur.rowcount > 0


def log_audit_event(
    conn: sqlite3.Connection,
    actor: str,
    role: str,
    action: str,
    target_entity: str,
    details: str = "",
    ip_address: str = "127.0.0.1",
) -> int:
    """Logs an immutable enterprise security/administrative audit event."""
    now = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        """
        INSERT INTO audit_log (timestamp, actor, role, action, target_entity, details, ip_address)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (now, actor, role, action, target_entity, details, ip_address),
    )
    conn.commit()
    return cur.lastrowid


def get_audit_logs(
    conn: sqlite3.Connection,
    limit: int = 150,
    action_filter: str | None = None,
) -> list[dict]:
    """Retrieves recent audit log events ordered by descending timestamp."""
    if action_filter and action_filter != "ALL":
        rows = conn.execute(
            """
            SELECT id, timestamp, actor, role, action, target_entity, details, ip_address
            FROM audit_log
            WHERE action = ?
            ORDER BY id DESC LIMIT ?
            """,
            (action_filter, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT id, timestamp, actor, role, action, target_entity, details, ip_address
            FROM audit_log
            ORDER BY id DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def authenticate_user(
    conn: sqlite3.Connection,
    username: str,
    password: str,
    ip_address: str = "127.0.0.1",
) -> dict | None:
    """
    Authenticates an enterprise user against stored PBKDF2-HMAC-SHA256 credentials.
    Returns the user dict if valid and active, otherwise None.
    Records immutable USER_LOGIN or LOGIN_FAILED audit entries.
    """
    clean_uname = username.strip()
    if not clean_uname or not password:
        return None

    row = conn.execute(
        """
        SELECT id, username, password_hash, salt, role, full_name, email, is_active
        FROM users
        WHERE LOWER(username) = LOWER(?)
        """,
        (clean_uname,),
    ).fetchone()

    if not row:
        log_audit_event(
            conn,
            actor=clean_uname or "anonymous",
            role="Unknown",
            action="LOGIN_FAILED",
            target_entity="Auth System",
            details="Authentication rejected: Account username does not exist.",
            ip_address=ip_address,
        )
        return None

    user = dict(row)
    if not user.get("is_active"):
        log_audit_event(
            conn,
            actor=user["username"],
            role=user.get("role", "Viewer"),
            action="LOGIN_FAILED",
            target_entity="Auth System",
            details="Authentication rejected: Account is currently deactivated.",
            ip_address=ip_address,
        )
        return None

    if not verify_password(password, user["password_hash"], user["salt"]):
        log_audit_event(
            conn,
            actor=user["username"],
            role=user.get("role", "Viewer"),
            action="LOGIN_FAILED",
            target_entity="Auth System",
            details="Authentication rejected: Invalid password credentials.",
            ip_address=ip_address,
        )
        return None

    now_iso = datetime.now(timezone.utc).isoformat()
    conn.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (now_iso, user["id"]))
    conn.commit()

    log_audit_event(
        conn,
        actor=user["username"],
        role=user.get("role", "Viewer"),
        action="USER_LOGIN",
        target_entity="Auth System",
        details="User successfully signed in to Watchtower session.",
        ip_address=ip_address,
    )

    return {
        "id": user["id"],
        "username": user["username"],
        "role": user["role"],
        "full_name": user["full_name"],
        "email": user["email"],
        "last_login_at": now_iso,
    }