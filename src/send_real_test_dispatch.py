"""
send_real_test_dispatch.py
==========================
CLI utility to transmit a genuine, live SMTP alert email for the 10 expired items
to the two designated test addresses:
  - basha.shaikirfan@gmail.com
  - dataengineerib@gmail.com

Usage:
  python src/send_real_test_dispatch.py --host smtp.gmail.com --port 587 --user <email> --password <app_password>
Or via environment variables:
  SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM
"""

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

import pandas as pd

# Add src to sys.path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from db import get_connection
import notifier

TARGET_TEST_RECIPIENTS = [
    "basha.shaikirfan@gmail.com",
    "dataengineerib@gmail.com",
]


def load_expired_records(db_path: str) -> list[dict]:
    conn = get_connection(db_path)
    df = pd.read_sql_query("SELECT * FROM component_records", conn)
    conn.close()

    df["exp_dt"] = pd.to_datetime(df["exp_date"]).dt.date
    today = date.today()
    df["days_left"] = (df["exp_dt"] - today).apply(lambda d: d.days)

    expired_df = df[df["days_left"] < 0].sort_values(by=["state", "team", "environment"])
    records = expired_df.to_dict(orient="records")
    for r in records:
        r["days_left"] = int(r["days_left"])
        r["overdue_days"] = abs(r["days_left"])
        r["overdue_str"] = f"{abs(r['days_left'])} days overdue"
    return records


def main():
    notifier.load_env()
    parser = argparse.ArgumentParser(description="Send real test email for expired items.")
    parser.add_argument("--db", default=str(ROOT / "data" / "expiry.db"), help="Path to SQLite DB")
    parser.add_argument("--host", default=os.environ.get("SMTP_HOST"), help="SMTP Server host")
    raw_port = (os.environ.get("SMTP_PORT") or "").strip()
    default_port = int(raw_port) if raw_port.isdigit() else 587
    parser.add_argument("--port", type=int, default=default_port, help="SMTP Server port")
    parser.add_argument("--user", default=os.environ.get("SMTP_USER"), help="SMTP Username / Auth user")
    parser.add_argument("--password", default=os.environ.get("SMTP_PASSWORD"), help="SMTP Password or App Password")
    parser.add_argument("--from-addr", default=os.environ.get("SMTP_FROM"), help="From address (optional)")
    args = parser.parse_args()

    expired_records = load_expired_records(args.db)
    print(f"Loaded {len(expired_records)} expired records from {args.db}")
    if len(expired_records) == 0:
        print("No expired records found. Nothing to dispatch.")
        return 0

    smtp_config = {
        "host": args.host,
        "port": args.port,
        "user": args.user,
        "password": args.password,
        "from_addr": args.from_addr or args.user,
    }

    if not smtp_config["host"] or not smtp_config["user"] or not smtp_config["password"]:
        print("ERROR: Incomplete SMTP configuration.", file=sys.stderr)
        print("Required: host, user, and password via arguments or environment variables.", file=sys.stderr)
        print("Example: python src/send_real_test_dispatch.py --host smtp.gmail.com --port 587 --user user@gmail.com --password 'xxxx-xxxx-xxxx-xxxx'", file=sys.stderr)
        return 2

    print(f"Connecting to SMTP server {smtp_config['host']}:{smtp_config['port']} as {smtp_config['user']}...")
    print(f"Target test recipients (strict): {TARGET_TEST_RECIPIENTS}")

    try:
        receipt = notifier.dispatch_expired_alert_real(
            recipients=TARGET_TEST_RECIPIENTS,
            expired_records=expired_records,
            smtp_config=smtp_config,
        )
        print("\n=== LIVE DISPATCH SUCCESSFUL ===")
        print(json.dumps(receipt, indent=2))
        return 0
    except Exception as e:
        print(f"\n=== LIVE DISPATCH FAILED ===", file=sys.stderr)
        print(f"Error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())