"""
expiry_checker.py
==================
Decides which accounts are due a reminder email today.

The rule (as specified):
  - An account becomes eligible once its exp_date is within THRESHOLD_DAYS
    (default 15) of today - including accounts that are already expired
    (a negative days-left is even more urgent, not less).
  - Once eligible, the owner is reminded once per day, every day.
  - This keeps happening for as long as the exp_date in expiry_records
    stays the SAME. The moment the source Excel is re-ingested with a
    new exp_date for that account (a renewal), this becomes a brand new
    reminder cycle - reminders pause until the new date is itself within
    the threshold.

How the "cycle" is implemented:
  reminder_log is keyed on (username, schema_name, exp_date). That key
  IS the cycle. There's no explicit "reset" step needed:
    - If no reminder_log row exists for the account's CURRENT exp_date,
      this is the first reminder of a new cycle (whether that's because
      the account is brand new, or because it was just renewed).
    - If a row exists but last_sent_at != today, send again and bump it.
    - If a row exists and last_sent_at == today, it's already been sent
      today (guards against double-sends if the job runs twice).
"""

import argparse
from datetime import date, datetime, timezone

from db import get_connection

DEFAULT_THRESHOLD_DAYS = 15


def get_due_reminders(conn, threshold_days: int = DEFAULT_THRESHOLD_DAYS, today: date = None):
    """
    Returns a list of dicts, one per account that should be emailed today:
        state, env, username, schema_name, exp_date, days_left,
        owner_name, owner_email, is_first_reminder
    """
    today = today or date.today()

    rows = conn.execute(
        """
        SELECT r.state, r.env, r.username, r.schema_name, r.exp_date,
               o.owner_name, o.owner_email
        FROM expiry_records r
        LEFT JOIN owners o ON o.state = r.state
        """
    ).fetchall()

    due = []
    for r in rows:
        exp_date = datetime.strptime(r["exp_date"], "%Y-%m-%d").date()
        days_left = (exp_date - today).days

        if days_left > threshold_days:
            continue  # not near expiry yet, nothing to do

        if r["owner_email"] is None:
            # No owner configured for this state - skip, but this should
            # be surfaced to whoever runs the job (see run_daily.py logging).
            continue

        log_row = conn.execute(
            """SELECT last_sent_at, times_sent FROM reminder_log
               WHERE username=? AND schema_name=? AND exp_date=?""",
            (r["username"], r["schema_name"], r["exp_date"]),
        ).fetchone()

        already_sent_today = log_row is not None and log_row["last_sent_at"] == today.isoformat()
        if already_sent_today:
            continue

        due.append({
            "state": r["state"],
            "env": r["env"],
            "username": r["username"],
            "schema_name": r["schema_name"],
            "exp_date": r["exp_date"],
            "days_left": days_left,
            "owner_name": r["owner_name"],
            "owner_email": r["owner_email"],
            "is_first_reminder": log_row is None,
        })

    return due


def mark_sent(conn, record: dict, today: date = None):
    """Record that a reminder was just sent for this account's current cycle."""
    today = today or date.today()
    conn.execute(
        """
        INSERT INTO reminder_log (state, username, schema_name, exp_date, last_sent_at, times_sent)
        VALUES (:state, :username, :schema_name, :exp_date, :today, 1)
        ON CONFLICT(username, schema_name, exp_date) DO UPDATE SET
            last_sent_at = :today,
            times_sent = times_sent + 1
        """,
        {**record, "today": today.isoformat()},
    )
    conn.commit()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="List accounts due a reminder today (dry run, no sending).")
    parser.add_argument("--db", default="data/expiry.db")
    parser.add_argument("--threshold-days", type=int, default=DEFAULT_THRESHOLD_DAYS)
    args = parser.parse_args()

    conn = get_connection(args.db)
    due = get_due_reminders(conn, args.threshold_days)
    print(f"{len(due)} account(s) due a reminder (threshold={args.threshold_days} days):\n")
    for d in due:
        tag = "FIRST" if d["is_first_reminder"] else "REPEAT"
        print(f"  [{tag:6}] {d['state']} {d['username']:28} exp {d['exp_date']} "
              f"({d['days_left']:+d} days) -> {d['owner_email']}")
    conn.close()