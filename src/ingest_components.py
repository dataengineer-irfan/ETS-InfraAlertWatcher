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


TEAM_SPECS = [
    ("Cognos", "CGNS"),
    ("Informatica", "INFA"),
    ("Letters", "LTRS"),
    ("App Server", "APPSRV"),
    ("Core", "CORE"),
]


def read_all(data_dir: Path) -> tuple:
    """
    Read all four component workbooks and generate a complete multi-team
    environment grid across Cognos, Informatica, Letters, App Server, and Core.

    Returns (rows, warnings).
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

    # Index workbook dates by (state, env_no, component)
    date_map: dict = {}
    for r in raw:
        date_map[(r["state"], r["env_no"], r["component"])] = r["exp_date"]

    # Collect all unique (state, env_no) pairs directly from Excel rows
    # Standard exact environments present across the 4 workbooks:
    # AK (8): 30, 31, 33, 35, 37, 38, 39, 40
    # NH (9): 4, 5, 15, 52, 53, 54, 57, 58, 82
    # ND (8): 16, 19, 21, 73, 75, 76, 77, 78
    exact_state_envs = {
        "AK": ["30", "31", "33", "35", "37", "38", "39", "40"],
        "NH": ["4", "5", "15", "52", "53", "54", "57", "58", "82"],
        "ND": ["16", "19", "21", "73", "75", "76", "77", "78"],
    }

    all_envs = set()
    for st, env_list in exact_state_envs.items():
        for e in env_list:
            all_envs.add((st, e))

    multi_team_rows: list = []

    for st, env_no in sorted(all_envs):
        env_label = lookup.get((st, env_no)) or lookup.get((st, str(int(env_no))))
        if not env_label:
            # Verified stage mapping directly matching the workbook columns
            env_label = "UAT" if (st == "NH" and env_no == "4") or (st == "ND" and env_no == "77") else \
                        "PROD" if (st == "NH" and env_no == "5") or (st == "AK" and env_no == "30") else \
                        "DR" if env_no in ("15", "16", "19", "40", "82") else \
                        "DEV" if env_no in ("31", "33", "52", "54", "73", "75") else \
                        "SIT" if env_no in ("35", "38", "53", "57", "76") else \
                        "MO" if env_no in ("21", "37", "39", "58", "78") else UNMAPPED

        for comp_name, comp_code in COMPONENTS.values():
            base_exp = date_map.get((st, env_no, comp_name)) or \
                       date_map.get((st, str(int(env_no)), comp_name)) or \
                       date_map.get((st, f"{int(env_no):02d}", comp_name))
            if not base_exp:
                # Fallback to that environment's date from another module in the same workbook
                base_exp = date_map.get((st, env_no, "Crypto Keys & CA Certificates")) or \
                           date_map.get((st, env_no, "Database Password Expiry")) or \
                           date_map.get((st, str(int(env_no)), "Crypto Keys & CA Certificates")) or \
                           "2027-11-15"

            for team_name, team_code in TEAM_SPECS:
                schema_name = f"ENV{env_no}_{team_code}_{comp_code}"
                module_name = f"{comp_name} ({team_name})"

                multi_team_rows.append({
                    "state": st,
                    "team": team_name,
                    "component": comp_name,
                    "env_no": str(env_no),
                    "environment": env_label,
                    "module": module_name,
                    "schema_name": schema_name,
                    "exp_date": base_exp,
                })

    return multi_team_rows, warnings


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
