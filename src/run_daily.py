"""
run_daily.py
============
The daily scheduled job. The database is now the source of truth -
Excel is only used once, to seed the initial data (see migrate_from_excel.py).

Ties together:
  1. Load/refresh the owners mapping
  2. Send today's due reminders (real send unless --dry-run is passed)
"""

import argparse
import os

import notifier
from db import get_connection, load_owners_csv

DEFAULT_DB_PATH = os.environ.get("EXPIRY_DB_PATH", "data/expiry.db")
DEFAULT_OWNERS_CSV = os.environ.get("OWNERS_CSV_PATH", "config/owners.csv")
DEFAULT_THRESHOLD_DAYS = int(os.environ.get("THRESHOLD_DAYS", "15"))


def main():
    parser = argparse.ArgumentParser(description="Run the daily expiry-alert pipeline.")
    parser.add_argument("--db", default=DEFAULT_DB_PATH)
    parser.add_argument("--owners-csv", default=DEFAULT_OWNERS_CSV)
    parser.add_argument("--threshold-days", type=int, default=DEFAULT_THRESHOLD_DAYS)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    print("== Step 1: Loading owners ==")
    conn = get_connection(args.db)
    n = load_owners_csv(conn, args.owners_csv)
    conn.close()
    print(f"  loaded {n} owner rows")

    print("== Step 2: Sending reminders ==")
    count = notifier.run(args.db, args.threshold_days, dry_run=args.dry_run)
    print(f"  {count} reminder(s) processed")


if __name__ == "__main__":
    main()