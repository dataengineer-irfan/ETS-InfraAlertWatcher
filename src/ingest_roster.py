"""
ingest_roster.py
================
Parses weekly on-call operational rosters from Excel files (.xlsx)
covering 4 functional divisions:
  1. Production Support (24x7 Resource-level shift and off-duty roster)
  2. Infra Team (Component-level on-call across 4 shifts/day)
  3. Core Dev (State MMIS Core development on-call: AK, ND, NH)
  4. Non-Core Dev (Letters, Cognos, Informatica, TMSIS, EDMS)

Supports both CLI file ingestion and in-memory BytesIO ingestion from the Streamlit UI.
"""

from __future__ import annotations

import io
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO, Union

import openpyxl

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from db import get_connection, save_on_call_roster


def _clean_str(val: object) -> str:
    """Safely cast cell value to trimmed string."""
    if val is None:
        return ""
    s = str(val).strip()
    return "" if s.lower() in ("none", "nan", "null") else s


def _format_date(val: object) -> str:
    """Format date cell to YYYY-MM-DD."""
    if isinstance(val, datetime):
        return val.strftime("%Y-%m-%d")
    s = _clean_str(val)
    if " " in s:
        s = s.split(" ")[0]
    return s


def parse_on_call_workbook(file_source: Union[str, Path, BinaryIO]) -> dict:
    """
    Parse all 4 sheets from an On-Call Roster workbook.
    Returns:
        {
            "meta": {...},
            "production_support": [...],
            "shifts": [...],
            "escalations": [...]
        }
    """
    if isinstance(file_source, (str, Path)):
        wb = openpyxl.load_workbook(str(file_source), data_only=True)
        source_name = Path(file_source).name
    else:
        wb = openpyxl.load_workbook(io.BytesIO(file_source.read()), data_only=True)
        source_name = "Uploaded_Roster.xlsx"

    sheetnames = wb.sheetnames

    # Assert required sheets exist (case-insensitive fuzzy match)
    s_map = {s.lower().strip(): s for s in sheetnames}
    ps_sheet_name = s_map.get("production support")
    infra_sheet_name = s_map.get("infra team")
    core_sheet_name = s_map.get("core dev")
    non_core_sheet_name = s_map.get("non-core dev") or s_map.get("non core dev")

    if not (ps_sheet_name and infra_sheet_name and core_sheet_name and non_core_sheet_name):
        missing = []
        if not ps_sheet_name: missing.append("Production support")
        if not infra_sheet_name: missing.append("Infra Team")
        if not core_sheet_name: missing.append("Core Dev")
        if not non_core_sheet_name: missing.append("Non-Core Dev")
        raise ValueError(f"Invalid roster workbook. Missing sheet(s): {', '.join(missing)}")

    # --------------------------------------------------------------------------
    # 1. PARSE SHEET: Production support
    # --------------------------------------------------------------------------
    ws_ps = wb[ps_sheet_name]
    ps_dates = []
    # Dates from row 2, day names from row 1
    for c in range(2, min(ws_ps.max_column + 1, 9)):
        d_val = ws_ps.cell(2, c).value
        day_name = _clean_str(ws_ps.cell(1, c).value)
        if d_val:
            ps_dates.append((c, day_name, _format_date(d_val)))

    valid_from = ps_dates[0][2] if ps_dates else datetime.now().strftime("%Y-%m-%d")
    valid_to = ps_dates[-1][2] if ps_dates else datetime.now().strftime("%Y-%m-%d")

    ps_records = []
    for r in range(3, ws_ps.max_row + 1):
        res_name = _clean_str(ws_ps.cell(r, 1).value)
        if not res_name or res_name.lower().startswith("resource") or res_name.lower() == "total":
            continue

        for col_idx, day_name, d_str in ps_dates:
            win_val = _clean_str(ws_ps.cell(r, col_idx).value)
            if not win_val:
                win_val = "WO"

            # Determine shift type and working status
            win_low = win_val.lower()
            if "6:30" in win_low or "06:30" in win_low:
                s_type = "Morning"
                is_working = 1
            elif "14:30" in win_low:
                s_type = "Evening"
                is_working = 1
            elif "22:30" in win_low:
                s_type = "Night"
                is_working = 1
            elif "comp" in win_low:
                s_type = "Comp Off"
                is_working = 0
            elif "float" in win_low or "holiday" in win_low:
                s_type = "Holiday"
                is_working = 0
            elif "leave" in win_low:
                s_type = "Leave"
                is_working = 0
            elif "wo" in win_low or "off" in win_low:
                s_type = "Week Off"
                is_working = 0
            else:
                s_type = "Custom"
                is_working = 1

            ps_records.append({
                "resource_name": res_name,
                "shift_date": d_str,
                "day_name": day_name,
                "shift_type": s_type,
                "shift_window": win_val,
                "is_working": is_working,
            })

    # --------------------------------------------------------------------------
    # Helper: Extract 28 Shift Slots (7 days x 4 shifts) from date/day rows
    # --------------------------------------------------------------------------
    def extract_time_slots(ws, date_row: int, day_row: int) -> list[dict]:
        slots = []
        cur_date = None
        cur_day = None
        day_slot_idx = 0
        for c in range(3, 31):
            d_val = ws.cell(date_row, c).value
            dy_val = ws.cell(day_row, c).value
            if d_val is not None:
                new_date = _format_date(d_val)
                if new_date != cur_date:
                    cur_date = new_date
                    day_slot_idx = 0
            if dy_val is not None:
                cur_day = _clean_str(dy_val)

            day_slot_idx += 1
            slot_num = ((day_slot_idx - 1) % 4) + 1
            slots.append({
                "col": c,
                "date": cur_date or valid_from,
                "day": cur_day or "Sat",
                "slot": slot_num,
            })
        return slots

    shift_records = []
    escalation_records = []

    # --------------------------------------------------------------------------
    # 2. PARSE SHEET: Infra Team
    # --------------------------------------------------------------------------
    ws_infra = wb[infra_sheet_name]
    infra_slots = extract_time_slots(ws_infra, date_row=1, day_row=2)

    # Component blocks: (row_start, domain_name)
    infra_domains = [
        (3, "Cognos"),
        (8, "Informatica"),
        (13, "UC4"),
        (18, "App Server"),
        (23, "Database"),
        (28, "IAM"),
    ]

    for r_start, d_name in infra_domains:
        est_row = r_start
        ist_row = r_start + 1
        prim_row = r_start + 2
        sec_row = r_start + 3

        for sl in infra_slots:
            c = sl["col"]
            t_est = _clean_str(ws_infra.cell(est_row, c).value)
            t_ist = _clean_str(ws_infra.cell(ist_row, c).value)
            p_on = _clean_str(ws_infra.cell(prim_row, c).value)
            s_on = _clean_str(ws_infra.cell(sec_row, c).value)

            shift_records.append({
                "division": "Infra Team",
                "domain_state": d_name,
                "shift_date": sl["date"],
                "day_name": sl["day"],
                "shift_slot": sl["slot"],
                "time_est": t_est or "09:00 PM to 3:29 AM",
                "time_ist": t_ist or "7:30 AM to 2:59 PM",
                "primary_on_call": p_on,
                "secondary_on_call": s_on,
                "module_lead": "",
                "module_backup": "",
            })

    # Escalations in Infra Team (Rows 34, 35, 36)
    for sl in infra_slots:
        c = sl["col"]
        t1 = _clean_str(ws_infra.cell(34, c).value)
        t2 = _clean_str(ws_infra.cell(35, c).value)
        t3 = _clean_str(ws_infra.cell(36, c).value)

        escalation_records.append({
            "division": "Infra Team",
            "domain_state": "All Infrastructure",
            "shift_date": sl["date"],
            "shift_slot": sl["slot"],
            "tier1_name": t1 or "Abhijit Vajja",
            "tier1_title": "Offshore Primary Lead",
            "tier2_name": t2 or "Anil Tankala",
            "tier2_title": "Service Delivery Manager (SDM)",
            "tier3_name": t3 or "Nagarajan Kochunni",
            "tier3_title": "Project Director (PD)",
        })

    # --------------------------------------------------------------------------
    # 3. PARSE SHEET: Core Dev
    # --------------------------------------------------------------------------
    ws_core = wb[core_sheet_name]
    core_sections = [
        ("AK DEV", 1, 3, 4, 5, 6, 8, 9, 10),
        ("ND DEV", 13, 15, None, 16, 17, 19, 20, 21),
        ("NH DEV", 24, 26, None, 27, 28, 30, 31, 32),
    ]

    for state_name, d_row, est_r, ist_r, prim_r, sec_r, e1_r, e2_r, e3_r in core_sections:
        c_slots = extract_time_slots(ws_core, date_row=d_row, day_row=d_row + 1)

        for sl in c_slots:
            c = sl["col"]
            t_est = _clean_str(ws_core.cell(est_r, c).value)
            t_ist = _clean_str(ws_core.cell(ist_r, c).value) if ist_r else ""
            if not t_ist:
                # Standard slot conversion to IST
                ist_map = {
                    1: "7:30 AM to 1:29 PM",
                    2: "1:30 PM to 7 :29 PM",
                    3: "7:30 PM to 1:29 AM",
                    4: "1:30 AM  to 7:29AM",
                }
                t_ist = ist_map.get(sl["slot"], "7:30 AM to 1:29 PM")

            p_on = _clean_str(ws_core.cell(prim_r, c).value)
            s_on = _clean_str(ws_core.cell(sec_r, c).value)

            mod_lead = ""
            mod_back = ""
            if state_name == "NH DEV":
                mod_lead = "Sunil / Madhu / Nagesh"
                mod_back = "Nagesh / Sunil"

            shift_records.append({
                "division": "Core Dev",
                "domain_state": state_name,
                "shift_date": sl["date"],
                "day_name": sl["day"],
                "shift_slot": sl["slot"],
                "time_est": t_est or "9:00 PM to 2:59 AM",
                "time_ist": t_ist,
                "primary_on_call": p_on,
                "secondary_on_call": s_on,
                "module_lead": mod_lead,
                "module_backup": mod_back,
            })

            t1 = _clean_str(ws_core.cell(e1_r, c).value)
            t2 = _clean_str(ws_core.cell(e2_r, c).value)
            t3 = _clean_str(ws_core.cell(e3_r, c).value)

            escalation_records.append({
                "division": "Core Dev",
                "domain_state": state_name,
                "shift_date": sl["date"],
                "shift_slot": sl["slot"],
                "tier1_name": t1,
                "tier1_title": "TL / TM - Offshore",
                "tier2_name": t2,
                "tier2_title": "SDM - Offshore",
                "tier3_name": t3,
                "tier3_title": "PD - Offshore",
            })

    # --------------------------------------------------------------------------
    # 4. PARSE SHEET: Non-Core Dev
    # --------------------------------------------------------------------------
    ws_nc = wb[non_core_sheet_name]
    nc_sections = [
        ("Cognos", 1, 3, 4, 5, 6, 7, 8, 9),
        ("Letters", 11, 13, 14, 15, 16, 17, 18, 19),
        ("Informatica", 23, 25, 26, 27, 28, 29, 30, 31),
        ("NH- TMSIS Informatica", 34, 36, 37, 38, 39, 40, 41, 42),
        ("EDMS (Docfinity & xPression)", 45, 47, 48, 49, 50, 51, 52, 53),
    ]

    for d_name, d_row, est_r, ist_r, prim_r, sec_r, e1_r, e2_r, e3_r in nc_sections:
        nc_slots = extract_time_slots(ws_nc, date_row=d_row, day_row=d_row + 1)

        for sl in nc_slots:
            c = sl["col"]
            t_est = _clean_str(ws_nc.cell(est_r, c).value)
            t_ist = _clean_str(ws_nc.cell(ist_r, c).value)
            p_on = _clean_str(ws_nc.cell(prim_r, c).value)
            s_on = _clean_str(ws_nc.cell(sec_r, c).value)

            shift_records.append({
                "division": "Non-Core Dev",
                "domain_state": d_name,
                "shift_date": sl["date"],
                "day_name": sl["day"],
                "shift_slot": sl["slot"],
                "time_est": t_est or "9:00 PM to 2:59 AM",
                "time_ist": t_ist or "7:30 AM to 1:29 PM",
                "primary_on_call": p_on,
                "secondary_on_call": s_on,
                "module_lead": "",
                "module_backup": "",
            })

            t1 = _clean_str(ws_nc.cell(e1_r, c).value)
            t2 = _clean_str(ws_nc.cell(e2_r, c).value)
            t3 = _clean_str(ws_nc.cell(e3_r, c).value)

            escalation_records.append({
                "division": "Non-Core Dev",
                "domain_state": d_name,
                "shift_date": sl["date"],
                "shift_slot": sl["slot"],
                "tier1_name": t1,
                "tier1_title": "TL / TM",
                "tier2_name": t2,
                "tier2_title": "SDM",
                "tier3_name": t3,
                "tier3_title": "PD",
            })

    meta = {
        "roster_name": f"On Call Roster ({valid_from} to {valid_to})",
        "valid_from": valid_from,
        "valid_to": valid_to,
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
        "uploaded_by": "admin",
        "source_file": source_name,
        "is_active": 1,
    }

    return {
        "meta": meta,
        "production_support": ps_records,
        "shifts": shift_records,
        "escalations": escalation_records,
    }


def ingest_roster_file(db_path: str, file_source: Union[str, Path, BinaryIO]) -> dict:
    """Parse on-call roster workbook and persist into SQLite database."""
    parsed = parse_on_call_workbook(file_source)
    conn = get_connection(db_path)
    try:
        roster_id = save_on_call_roster(
            conn,
            roster_meta=parsed["meta"],
            ps_records=parsed["production_support"],
            shift_records=parsed["shifts"],
            escalation_records=parsed["escalations"],
        )
        return {
            "status": "success",
            "roster_id": roster_id,
            "roster_name": parsed["meta"]["roster_name"],
            "valid_from": parsed["meta"]["valid_from"],
            "valid_to": parsed["meta"]["valid_to"],
            "ps_count": len(parsed["production_support"]),
            "shift_count": len(parsed["shifts"]),
            "escalation_count": len(parsed["escalations"]),
        }
    finally:
        conn.close()


def main():
    db_path = os.environ.get("EXPIRY_DB_PATH", str(ROOT / "data" / "expiry.db"))
    input_dir = ROOT / "_Input"
    target_files = list(input_dir.glob("On Call Roster*.xlsx"))
    if not target_files:
        print(f"[!] No roster file found in {input_dir}")
        sys.exit(1)

    file_to_ingest = sorted(target_files, key=lambda f: f.stat().st_mtime, reverse=True)[0]
    print(f"[*] Ingesting latest roster: {file_to_ingest.name} into {db_path}...")
    res = ingest_roster_file(db_path, file_to_ingest)
    print(f"[+] Successfully ingested roster id={res['roster_id']}:")
    print(f"    Name: {res['roster_name']}")
    print(f"    Dates: {res['valid_from']} to {res['valid_to']}")
    print(f"    Production Support: {res['ps_count']} resource-shift records")
    print(f"    On-Call Shifts: {res['shift_count']} shift slots")
    print(f"    Escalations: {res['escalation_count']} hierarchy records")


if __name__ == "__main__":
    main()
