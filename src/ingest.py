"""
ingest.py
=========
Reads the source Excel workbook and loads it into SQLite.

The workbook has one sheet per state (AK, NH, ND, ...). Each sheet is NOT
a clean table - it's a repeating block structure:

    ENV   | User Name              | Schema Name       | Exp Date
    ENV30 | AKCGAU30E2             | ENV30_COTS_CGNS    | 2026-06-07
          | AKORR30E2_CGNS_USER    | ENV30_ORR_CGNS     | 2026-06-07
          | AKMMIS30E2_CGNS_USER   | ENV30_MMIS_CGNS    | 2026-06-07
    (blank row)
    (blank row)
    ENV   | User Name              | Schema Name       | Exp Date      <- header repeats
    ENV31 | AKCGAU31E2             | ENV31_COTS_CGNS    | 2026-09-18
          ...

So for each sheet we have to:
  1. Drop fully blank rows (the block separators).
  2. Drop repeated header rows (they show up as literal data rows).
  3. Forward-fill the ENV column, since only the first row of each block
     carries it.
  4. Tag every row with the sheet name as its "state".

Re-running this script is always safe: matching rows (same state,
username, schema_name) get their exp_date and last_seen_at refreshed
in place, they are never duplicated.
"""

import argparse
import sys
from datetime import datetime, timezone

import pandas as pd

from db import get_connection

REQUIRED_COLUMNS = ["ENV", "User Name", "Schema Name", "Exp Date"]


def read_state_sheet(xl: pd.ExcelFile, sheet_name: str) -> pd.DataFrame:
    """Flatten one state's sheet into clean rows: env, username, schema_name, exp_date."""
    df = pd.read_excel(xl, sheet_name=sheet_name, header=None,
                        names=["ENV", "User", "Schema", "ExpDate"])

    # Drop fully blank separator rows between blocks.
    df = df.dropna(how="all")

    # Drop repeated literal header rows (the ones where ENV == "ENV").
    df = df[df["ENV"] != "ENV"]

    # Only the first row of each block carries the ENV code - forward-fill it.
    df["ENV"] = df["ENV"].ffill()

    # A row with no username or no expiry date isn't a real account record.
    df = df.dropna(subset=["User", "ExpDate"])

    df["ExpDate"] = pd.to_datetime(df["ExpDate"], errors="coerce")
    df = df.dropna(subset=["ExpDate"])

    df["State"] = sheet_name
    return df[["State", "ENV", "User", "Schema", "ExpDate"]]


def read_workbook(excel_path: str) -> pd.DataFrame:
    """Read every sheet in the workbook and concatenate into one flat DataFrame."""
    xl = pd.ExcelFile(excel_path)
    frames = [read_state_sheet(xl, sheet) for sheet in xl.sheet_names]
    full = pd.concat(frames, ignore_index=True)
    full["ExpDate"] = full["ExpDate"].dt.strftime("%Y-%m-%d")
    return full


def upsert_records(conn, df: pd.DataFrame) -> dict:
    """
    Upsert every row into expiry_records. Returns a small summary dict:
    how many rows were brand new vs. how many had their exp_date change
    (i.e. a renewal was detected) vs. unchanged.
    """
    now = datetime.now(timezone.utc).isoformat()
    new_count = changed_count = unchanged_count = 0

    for row in df.itertuples(index=False):
        existing = conn.execute(
            """SELECT exp_date FROM expiry_records
               WHERE state=? AND username=? AND schema_name=?""",
            (row.State, row.User, row.Schema),
        ).fetchone()

        if existing is None:
            new_count += 1
        elif existing["exp_date"] != row.ExpDate:
            changed_count += 1
        else:
            unchanged_count += 1

        conn.execute(
            """
            INSERT INTO expiry_records
                (state, env, username, schema_name, exp_date, first_seen_at, last_seen_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(state, username, schema_name) DO UPDATE SET
                env = excluded.env,
                exp_date = excluded.exp_date,
                last_seen_at = excluded.last_seen_at
            """,
            (row.State, row.ENV, row.User, row.Schema, row.ExpDate, now, now),
        )

    conn.commit()
    return {"new": new_count, "changed": changed_count, "unchanged": unchanged_count}


def run(excel_path: str, db_path: str) -> dict:
    df = read_workbook(excel_path)
    conn = get_connection(db_path)
    summary = upsert_records(conn, df)
    summary["total_rows_read"] = len(df)
    conn.close()
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingest the expiry Excel workbook into SQLite.")
    parser.add_argument("--excel", required=True, help="Path to the source .xlsx file")
    parser.add_argument("--db", default="data/expiry.db", help="Path to the SQLite database file")
    args = parser.parse_args()

    result = run(args.excel, args.db)
    print(f"Rows read from Excel : {result['total_rows_read']}")
    print(f"New records          : {result['new']}")
    print(f"Renewed (date changed): {result['changed']}")
    print(f"Unchanged            : {result['unchanged']}")
    sys.exit(0)