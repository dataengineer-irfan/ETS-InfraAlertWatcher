"""
build_fixture.py — the ground truth the JavaScript engine is checked against
===========================================================================
Reads component_records with pandas, builds both report pages, and writes the
counts pandas computed to tests/fixture/truth.json.

The point is to have two independent implementations of the same arithmetic.
The dashboard filters and totals records in JavaScript; this script does it in
pandas. If they ever disagree, one of them is wrong and the test says so -
which is a much stronger guarantee than asserting the engine matches numbers
the engine itself produced.

    python3 tests/build_fixture.py
    node tests/test_report_engine.js
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "dashboard"))

import report  # noqa: E402
import ui  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixture"
DB = os.environ.get("EXPIRY_DB_PATH", str(ROOT / "data" / "expiry.db"))

ENV_ORDER = ["DEV", "SIT", "UAT", "MO", "DR", "PROD", "UNMAPPED"]
COMPONENT_ORDER = list(report.COMPONENT_CODE)


def load() -> pd.DataFrame:
    conn = sqlite3.connect(DB)
    df = pd.read_sql_query("SELECT * FROM component_records", conn)
    conn.close()

    df["exp_dt"] = pd.to_datetime(df["exp_date"], errors="coerce")
    df = df.dropna(subset=["exp_dt"]).copy()
    df["days_left"] = (df["exp_dt"] - pd.Timestamp(date.today())).dt.days.astype(int)
    df["band"] = df["days_left"].apply(
        lambda d: "Expired" if d < 0 else "Critical" if d <= report.CRITICAL_DAYS
        else "Warning" if d <= report.WARNING_DAYS else "Healthy")
    df["edited"] = df["edited_at"].notna()
    df["quarter"] = ("Q" + df["exp_dt"].dt.quarter.astype(str)
                     + " " + df["exp_dt"].dt.year.astype(str))
    df["env_label"] = df["environment"].fillna("UNMAPPED")
    return df


def quarter_window(n: int = 12) -> list:
    """The same forward window renderWhen() plots, computed independently."""
    today = date.today()
    y, q = today.year, (today.month - 1) // 3 + 1
    out = []
    for _ in range(n):
        out.append(f"Q{q} {y}")
        q += 1
        if q > 4:
            q, y = 1, y + 1
    return out


def q_ord(label: str):
    try:
        # "Q3 2026" -> ordinal
        qq, yy = label.split(" ")
        return int(yy) * 4 + int(qq[1:]) - 1
    except (ValueError, AttributeError, IndexError):
        return None


def main() -> int:
    df = load()
    if df.empty:
        print("component_records is empty. Run: "
              "python src/ingest_components.py --data-dir . --db data/expiry.db")
        return 1

    records = report.to_records(df.to_dict("records"),
                               env_order=ENV_ORDER, component_order=COMPONENT_ORDER)

    window = quarter_window(12)
    first, last = q_ord(window[0]), q_ord(window[-1])
    ords = df["quarter"].map(q_ord)

    # A date that is actually on screen, to prove the search box matches the
    # printed form ("16 Oct 2026") and not just the ISO string.
    sample_iso = df.sort_values("days_left")["exp_date"].iloc[-1]
    sample_human = report._human_date(str(sample_iso)[:10])
    needle = sample_human.lower()

    # What ui.py's formatters produce. The report re-implements these in
    # JavaScript because they run in the browser, and the Manage tab uses the
    # Python originals - so the two must agree or the same record reads
    # differently on two tabs. Every boundary in the format, plus every day
    # count actually present in the data.
    probes = sorted({-2068, -1538, -365, -90, -60, -31, -30, -16, -15, -2, -1,
                     0, 1, 2, 15, 16, 30, 31, 59, 60, 89, 90, 91, 729, 730, 731,
                     1095, 3650, *df["days_left"].tolist()})
    fmt_days = {str(d): ui.fmt_days(d) for d in probes}
    fmt_days_long = {str(d): ui.fmt_days_long(d) for d in probes}
    fmt_date = {iso: ui.fmt_date(iso)
                for iso in sorted({str(v)[:10] for v in df["exp_date"]})}

    truth = {
        "total": len(records),
        "byState": df.groupby("state").size().to_dict(),
        "byStateComp": {s: g.groupby("component").size().to_dict()
                        for s, g in df.groupby("state")},
        "byBand": df["band"].value_counts().to_dict(),
        "byEnv": df["env_label"].value_counts().to_dict(),
        "pairings": int(df.groupby(["env_label", "component"]).ngroups),
        "minDays": int(df["days_left"].min()),
        "maxDays": int(df["days_left"].max()),
        "edited": int(df["edited"].sum()),
        "next90": int(((df["days_left"] >= 0) & (df["days_left"] <= 90)).sum()),
        "next365": int(((df["days_left"] >= 0) & (df["days_left"] <= 365)).sum()),
        "overdue": int((df["days_left"] < 0).sum()),
        "inQuarterWindow": int(((ords >= first) & (ords <= last)).sum()),
        "beforeWindow": int((ords < first).sum()),
        "afterWindow": int((ords > last).sum()),
        "sampleHumanDate": sample_human,
        "sampleHumanDateCount": sum(1 for r in records if needle in r["hay"]),
        "fmtDays": fmt_days,
        "fmtDaysLong": fmt_days_long,
        "fmtDate": fmt_date,
    }

    FIXTURE.mkdir(parents=True, exist_ok=True)
    (FIXTURE / "truth.json").write_text(json.dumps(truth, indent=1, default=str),
                                        encoding="utf-8")

    for mode, state in (("all", None), ("state", "AK")):
        html = report.build(records, mode=mode, state=state, env_order=ENV_ORDER)
        (FIXTURE / f"page_{mode}.html").write_text(html, encoding="utf-8")
        print(f"page_{mode}.html  {len(html):>7,} bytes")

    print(f"truth.json       {len(records)} records, {truth['pairings']} pairings, "
          f"{truth['overdue']} overdue")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
