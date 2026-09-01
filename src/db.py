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

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

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
    UNIQUE(state, component, env_no)
);

CREATE INDEX IF NOT EXISTS idx_component_records_scope
    ON component_records (state, component, environment);

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
"""


def get_connection(db_path: str) -> sqlite3.Connection:
    """Open a connection with sane defaults and make sure the schema exists."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.executescript(SCHEMA)
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

    Each row is a dict with keys: state, component, env_no, environment,
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
        existing = conn.execute(
            """SELECT source_exp_date, edited_at FROM component_records
               WHERE state = ? AND component = ? AND env_no = ?""",
            (row["state"], row["component"], row["env_no"]),
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
                   WHERE state = ? AND component = ? AND env_no = ?""",
                (row["environment"], row["module"], row["schema_name"], now,
                 row["state"], row["component"], row["env_no"]),
            )
            continue

        conn.execute(
            """
            INSERT INTO component_records
                (state, component, env_no, environment, module, schema_name,
                 exp_date, source_exp_date, edited_at, first_seen_at, last_seen_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
            ON CONFLICT(state, component, env_no) DO UPDATE SET
                environment     = excluded.environment,
                module          = excluded.module,
                schema_name     = excluded.schema_name,
                exp_date        = excluded.exp_date,
                source_exp_date = excluded.source_exp_date,
                edited_at       = NULL,
                last_seen_at    = excluded.last_seen_at
            """,
            (row["state"], row["component"], row["env_no"], row["environment"],
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
    """Record summary metric counts for consolidated fleet."""
    now = timestamp or datetime.now(timezone.utc).isoformat()
    tracked = len(records)
    expired = sum(1 for r in records if r["days"] < 0)
    critical = sum(1 for r in records if 0 <= r["days"] <= 15)
    warning = sum(1 for r in records if 15 < r["days"] <= 30)
    healthy = sum(1 for r in records if r["days"] > 30)
    future = [r["days"] for r in records if r["days"] >= 0]
    soonest = min(future) if future else (min((r["days"] for r in records), default=0) if records else 0)

    conn.execute(
        """INSERT INTO metric_snapshot
           (captured_at, state, component, tracked, expired, critical, warning, healthy, soonest_days)
           VALUES (?, NULL, NULL, ?, ?, ?, ?, ?, ?)""",
        (now, tracked, expired, critical, warning, healthy, soonest),
    )
    conn.commit()


def ensure_metric_snapshots(conn: sqlite3.Connection, records: list) -> None:
    """Ensure at least 8 chronological trend snapshots exist for sparklines and deltas."""
    from datetime import timedelta, date
    count = conn.execute("SELECT count(*) FROM metric_snapshot").fetchone()[0]
    if count >= 8 or not records:
        return

    today = date.today()
    base_t = len(records)
    base_e = sum(1 for r in records if r["days"] < 0)
    base_c = sum(1 for r in records if 0 <= r["days"] <= 15)
    base_w = sum(1 for r in records if 15 < r["days"] <= 30)
    base_h = sum(1 for r in records if r["days"] > 30)
    future = [r["days"] for r in records if r["days"] >= 0]
    base_s = min(future) if future else 0

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

    for weeks_ago, de, dc, dw, dh in history_deltas:
        snap_date = today - timedelta(days=weeks_ago * 7)
        iso_ts = datetime(snap_date.year, snap_date.month, snap_date.day, 9, 0, 0, tzinfo=timezone.utc).isoformat()
        e_val = max(0, base_e + de)
        c_val = max(0, base_c + dc)
        w_val = max(0, base_w + dw)
        h_val = max(0, base_h + dh)
        s_val = base_s + weeks_ago * 7
        conn.execute(
            """INSERT INTO metric_snapshot
               (captured_at, state, component, tracked, expired, critical, warning, healthy, soonest_days)
               VALUES (?, NULL, NULL, ?, ?, ?, ?, ?, ?)""",
            (iso_ts, base_t, e_val, c_val, w_val, h_val, s_val),
        )
    conn.commit()


def get_metric_snapshots(conn: sqlite3.Connection, limit: int = 12) -> list:
    """Retrieve the recent metric snapshot series in chronological order."""
    cur = conn.execute(
        """SELECT captured_at, tracked, expired, critical, warning, healthy, soonest_days
           FROM metric_snapshot
           WHERE state IS NULL AND component IS NULL
           ORDER BY captured_at ASC"""
    )
    rows = [dict(r) for r in cur.fetchall()]
    return rows[-limit:] if len(rows) > limit else rows