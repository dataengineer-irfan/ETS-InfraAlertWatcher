"""
ingest_components.py
====================
Reads the four component workbooks into the component_records table.

Each workbook covers one tracked component and has one sheet per state
(AK, NH, ND). Unlike the account workbook handled by ingest.py, these are
clean four-column tables:

    Environemnts | ENV | Module                        | Expiry Dates
    PROD         | 30  | Crypto Keys & CA Certificates | 2029-08-20
    DEV          | 31  | Crypto Keys & CA Certificates | 2027-11-04

Three things need normalising along the way:

1. **Two date formats.** Most sheets store real Excel dates, but
   "Database Password Expiry" stores strings like ``18-SEP-26``. Both are
   parsed to ISO ``YYYY-MM-DD``.

2. **Missing environment labels.** A few rows leave the Environemnts cell
   blank (e.g. AK ENV 40 in the crypto and patch workbooks). The same ENV
   number is labelled in the other workbooks, so we build a
   (state, env_no) -> environment lookup from every row that *is* labelled
   and use it to fill the gaps. Anything still unknown becomes "UNMAPPED"
   rather than being silently dropped.

3. **No schema name.** The source has no schema column, so one is derived
   as ``ENV<n>_<CODE>`` (e.g. ``ENV30_CRYPTO``), matching the
   ``ENV30_COTS_CGNS`` convention already used elsewhere in this project.

Re-running is always safe - rows upsert on (state, component, env_no).
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

from db import get_connection, upsert_component_records

# filename stem -> (display name, short code used in the derived schema name)
COMPONENTS = {
    "Crypto Keys & CA Certificates": ("Crypto Keys & CA Certificates", "CRYPTO"),
    "Database Password Expiry": ("Database Password Expiry", "DBPWD"),
    "Software Versions & N-1 Tracking": ("Software Versions & N-1 Tracking", "SWVER"),
    "Upgrade & Patch Tasks": ("Upgrade & Patch Tasks", "PATCH"),
}

# Source header spellings, including the typo in the first column.
COL_ENVIRONMENT = "Environemnts"
COL_ENV_NO = "ENV"
COL_MODULE = "Module"
COL_EXPIRY = "Expiry Dates"

UNMAPPED = "UNMAPPED"

_MONTHS = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}


def parse_expiry(value) -> str | None:
    """
    Normalise one Expiry Dates cell to an ISO date string, or None if the
    cell cannot be read as a date.

    Handles real datetimes (most workbooks) and the DD-MMM-YY strings used
    by the Database Password Expiry workbook. Two-digit years are read as
    20xx, which is correct for this dataset - every date sits between 2020
    and 2029.
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None

    if isinstance(value, (datetime, pd.Timestamp)):
        return value.strftime("%Y-%m-%d")

    text = str(value).strip()
    if not text:
        return None

    parts = text.upper().split("-")
    if len(parts) == 3 and parts[1] in _MONTHS:
        day, month, year = parts
        try:
            year_n = int(year)
        except ValueError:
            return None
        if year_n < 100:
            year_n += 2000
        try:
            return datetime(year_n, _MONTHS[month], int(day)).strftime("%Y-%m-%d")
        except ValueError:
            return None

    parsed = pd.to_datetime(text, errors="coerce", dayfirst=True)
    return None if pd.isna(parsed) else parsed.strftime("%Y-%m-%d")


def read_component_workbook(path: Path, component: str) -> list:
    """Read every state sheet in one workbook into a list of raw row dicts."""
    sheets = pd.read_excel(path, sheet_name=None)
    rows = []

    for state, df in sheets.items():
        if df.empty:
            continue

        df = df.dropna(how="all")
        # A row is only a real record if it has both an ENV number and a date.
        for record in df.to_dict("records"):
            env_no = record.get(COL_ENV_NO)
            exp_date = parse_expiry(record.get(COL_EXPIRY))
            if env_no is None or pd.isna(env_no) or exp_date is None:
                continue

            environment = record.get(COL_ENVIRONMENT)
            if environment is None or pd.isna(environment) or not str(environment).strip():
                environment = None
            else:
                environment = str(environment).strip().upper()

            module = record.get(COL_MODULE)
            module = None if module is None or pd.isna(module) else str(module).strip()

            rows.append({
                "state": str(state).strip().upper(),
                "component": component,
                "env_no": str(int(env_no)) if isinstance(env_no, float) else str(env_no).strip(),
                "environment": environment,
                "module": module,
                "exp_date": exp_date,
            })

    return rows


def build_environment_lookup(rows: list) -> dict:
    """
    Map (state, env_no) -> environment using every row that carries a label.

    Blanks in one workbook are covered by the same ENV number in another.
    If two workbooks disagree, the most frequently seen label wins so a
    single typo cannot flip an environment's identity.
    """
    tally: dict = {}
    for row in rows:
        if row["environment"]:
            key = (row["state"], row["env_no"])
            tally.setdefault(key, {})
            tally[key][row["environment"]] = tally[key].get(row["environment"], 0) + 1

    return {key: max(counts.items(), key=lambda kv: kv[1])[0] for key, counts in tally.items()}


def read_all(data_dir: Path) -> tuple:
    """
    Read all four component workbooks found in data_dir.

    Returns (rows, warnings). Rows are fully normalised and ready to upsert.
    """
    raw: list = []
    warnings: list = []

    for stem, (component, _code) in COMPONENTS.items():
        path = data_dir / f"{stem}.xlsx"
        if not path.exists():
            warnings.append(f"Workbook not found, skipped: {path.name}")
            continue
        found = read_component_workbook(path, component)
        if not found:
            warnings.append(f"No readable rows in {path.name}")
        raw.extend(found)

    lookup = build_environment_lookup(raw)
    codes = {component: code for component, code in COMPONENTS.values()}

    for row in raw:
        if not row["environment"]:
            filled = lookup.get((row["state"], row["env_no"]))
            if filled:
                row["environment"] = filled
            else:
                row["environment"] = UNMAPPED
                warnings.append(
                    f"{row['state']} ENV{row['env_no']} ({row['component']}): "
                    "no environment label in any workbook, marked UNMAPPED"
                )
        row["schema_name"] = f"ENV{row['env_no']}_{codes[row['component']]}"

    return raw, warnings


def run(data_dir: str, db_path: str) -> dict:
    rows, warnings = read_all(Path(data_dir))
    conn = get_connection(db_path)
    summary = upsert_component_records(conn, rows)
    conn.close()
    summary["total_rows_read"] = len(rows)
    summary["warnings"] = warnings
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Ingest the four component workbooks into SQLite."
    )
    parser.add_argument(
        "--data-dir", default=".",
        help="Directory holding the four component .xlsx files (default: current directory)",
    )
    parser.add_argument("--db", default="data/expiry.db", help="Path to the SQLite database file")
    args = parser.parse_args()

    result = run(args.data_dir, args.db)
    print(f"Rows read from workbooks : {result['total_rows_read']}")
    print(f"New records              : {result['new']}")
    print(f"Renewed (date moved)     : {result['renewed']}")
    print(f"Unchanged                : {result['unchanged']}")
    for warning in result["warnings"]:
        print(f"  warning: {warning}")
    sys.exit(0)
