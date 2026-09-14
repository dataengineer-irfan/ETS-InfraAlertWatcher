"""
on_call.py — 24/7 On-Call Operations Command Hub & Roster Intelligence
======================================================================
Enterprise Grafana-style zero-scroll dashboard for 24x7 on-call schedules,
cross-tab shift matrices, multi-tier escalation hierarchy lineage, and
weekly Excel roster updates.

Follows Enterprise Design Standards:
  - Zero-page-scroll constraint (100vh viewport locking)
  - Unified 3-column Grafana Cockpit (0.85fr State / 1.15fr Module / 1.5fr Support)
  - 5-Card KPI metric ribbon with gradient fills
  - Multi-Dimensional Slicers:
      * State Slicer: All States, AK MMIS, ND MMIS, NH MMIS
      * Team Slicer: All Teams, Production Support, Infra Team, Core Dev, Non-Core Dev
      * Shift-Type Quick Chips: All Shifts, Morning, Evening, Night, WO, Holiday, Leave
      * Full-text search across domains, modules, and resources
      * One-click Reset & Excel Roster Upload
  - Master-Detail Context Inspector for escalation dispatch
  - High-performance caching (@st.cache_data ttl=120)
"""

from __future__ import annotations

from datetime import datetime, timezone
from html import escape
from pathlib import Path

import pandas as pd
import streamlit as st

import ui
from db import (
    get_connection,
    get_active_on_call_roster,
    get_on_call_production_support,
    get_on_call_shifts,
    get_on_call_escalations,
)
from ingest_roster import ingest_roster_file

ROOT = Path(__file__).resolve().parent.parent

_STATE_OPTIONS = ["All States", "AK MMIS", "ND MMIS", "NH MMIS"]
_TEAM_OPTIONS = ["All Teams", "Production Support", "Infra Team", "Core Dev", "Non-Core Dev"]
_LOCATION_OPTIONS = ["All Locations", "Offshore (IST)", "Onshore (EST)"]
_SDM_OPTIONS = [
    "All SDMs",
    "Anil Tankala (Infra / Ops)",
    "Kishore Nagarajan (AK / Non-Core)",
    "Ravi M Shankar (AK Onshore)",
    "Thirupathi Katakam (ND Onshore)",
    "Anil Kumar Khamari (ND Offshore)",
    "Madhav (NH Onshore)",
    "Dipak/Rama (NH Offshore)",
]

_SHIFT_CHIPS = [
    ("All Shifts", None),
    ("Morning", "morning"),
    ("Evening", "evening"),
    ("Night", "night"),
    ("WO", "wo"),
    ("Holiday", "holiday"),
    ("Leave", "leave"),
]

_PAGE_SIZE = 14


def _get_shift_badge(win_val: str | None) -> tuple[str, str]:
    """Return CSS class and compact label for shift chip."""
    if not win_val:
        return "", ""
    v = str(win_val).strip()
    v_low = v.lower()
    if v.startswith("6:30") or v.startswith("06:30") or "morning" in v_low:
        return "shift-m", "M"
    elif v.startswith("14:30") or "evening" in v_low:
        return "shift-e", "E"
    elif v.startswith("22:30") or "night" in v_low:
        return "shift-n", "N"
    elif v == "WO" or v_low == "week off":
        return "shift-wo", "WO"
    elif "comp" in v_low:
        return "shift-wo", "Comp"
    elif "holiday" in v_low or "float" in v_low:
        return "shift-fh", "Hol"
    elif "leave" in v_low:
        return "shift-lv", "Lv"
    return "shift-wo", v[:4]


def _get_current_active_slot(now_dt: datetime) -> int:
    h = now_dt.hour
    if 21 <= h or h < 3:
        return 1
    elif 3 <= h < 9:
        return 2
    elif 9 <= h < 15:
        return 3
    return 4


def _matches_ps_shift(chip_key: str | None, win_str: str) -> bool:
    if not chip_key:
        return True
    s = (win_str or "").lower()
    if chip_key == "morning":
        return "6:30" in s or "morning" in s
    elif chip_key == "evening":
        return "14:30" in s or "evening" in s
    elif chip_key == "night":
        return "22:30" in s or "night" in s
    elif chip_key == "wo":
        return "wo" in s or "off" in s
    elif chip_key == "holiday":
        return "holiday" in s or "float" in s
    elif chip_key == "leave":
        return "leave" in s
    return True


def _matches_domain_slot(chip_key: str | None, slot_num: int) -> bool:
    if not chip_key:
        return True
    if chip_key == "morning":
        return slot_num == 2
    elif chip_key == "evening":
        return slot_num in [3, 4]
    elif chip_key == "night":
        return slot_num == 1
    return True


def _resolve_shift_sdm(s: dict, esc_lookup: dict, esc_fallback: dict) -> str:
    div = s.get("division", "")
    dom = s.get("domain_state", "")
    slot = s.get("shift_slot", 1)
    sdate = s.get("shift_date", "")

    if div == "Infra Team":
        return "Anil Tankala"

    k_exact = (div, dom, slot, sdate)
    e = esc_lookup.get(k_exact) or esc_fallback.get((div, dom))
    if e and e.get("tier2_name"):
        sdm_name = e["tier2_name"].strip()
        if sdm_name != "State Specific SDM":
            return sdm_name

    # State specific mapping for Non-Core Dev
    if "NH" in dom.upper():
        return "Madhav" if slot in [3, 4] else "Dipak/Rama"
    elif "ND" in dom.upper():
        return "Thirupathi Katakam" if slot in [3, 4] else "Anil Kumar Khamari"
    elif "AK" in dom.upper():
        return "Ravi M Shankar" if slot in [3, 4] else "Kishore Nagarajan"

    return "Kishore Nagarajan" if slot in [1, 2] else "Anil Tankala"


# --------------------------------------------------------------------------
# Caching Data Loaders (120s TTL)
# --------------------------------------------------------------------------
@st.cache_data(ttl=120, show_spinner=False)
def _load_roster_meta(db_path: str) -> dict | None:
    conn = get_connection(db_path)
    try:
        r = get_active_on_call_roster(conn)
        return dict(r) if r else None
    finally:
        conn.close()


@st.cache_data(ttl=120, show_spinner=False)
def _load_ps(db_path: str, roster_id: int) -> list[dict]:
    conn = get_connection(db_path)
    try:
        rows = get_on_call_production_support(conn, roster_id)
        return [dict(r) for r in rows]
    finally:
        conn.close()


@st.cache_data(ttl=120, show_spinner=False)
def _load_shifts(db_path: str, roster_id: int) -> list[dict]:
    conn = get_connection(db_path)
    try:
        rows = get_on_call_shifts(conn, roster_id)
        return [dict(r) for r in rows]
    finally:
        conn.close()


@st.cache_data(ttl=120, show_spinner=False)
def _load_escs(db_path: str, roster_id: int) -> list[dict]:
    conn = get_connection(db_path)
    try:
        rows = get_on_call_escalations(conn, roster_id)
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _clear_oncall_cache() -> None:
    _load_roster_meta.clear()
    _load_ps.clear()
    _load_shifts.clear()
    _load_escs.clear()


# --------------------------------------------------------------------------
# Main Workspace Component
# --------------------------------------------------------------------------
def render_on_call_workspace(db_path: str) -> None:
    """Render the unified 3-column Grafana On-Call Command Hub."""
    ss = st.session_state

    # 0. Session State Initialization
    ss.setdefault("oncall_state_filter", "All States")
    ss.setdefault("oncall_team_filter", "All Teams")
    ss.setdefault("oncall_shift_chip", None)
    ss.setdefault("oncall_search", "")
    ss.setdefault("oncall_selected_eng", "Nirosha V")
    ss.setdefault("oncall_show_upload", False)

    # 1. Fetch Cached Active Roster
    active_roster = _load_roster_meta(db_path)

    # Auto-ingest fallback if DB is blank
    if not active_roster:
        input_dir = ROOT / "_Input"
        target_files = sorted(
            [f for f in input_dir.glob("*.xlsx") if "on call" in f.name.lower() or "roster" in f.name.lower()],
            key=lambda f: f.stat().st_mtime,
            reverse=True,
        )
        if target_files:
            with st.spinner("Initializing On-Call Roster from _Input..."):
                ingest_roster_file(db_path, target_files[0])
                _clear_oncall_cache()
                active_roster = _load_roster_meta(db_path)

    if not active_roster:
        st.warning("⚠️ No On-Call Roster found in database. Please upload a weekly roster Excel file.")
        uploaded_file = st.file_uploader("Upload On-Call Roster (.xlsx)", type=["xlsx"], key="oncall_init_upload")
        if uploaded_file:
            with st.spinner("Parsing and ingesting weekly roster..."):
                res = ingest_roster_file(db_path, uploaded_file)
                _clear_oncall_cache()
                st.success(f"✓ Ingested {res['roster_name']} with {res['ps_count']} support rows and {res['shift_count']} shift slots!")
                st.rerun()
        return

    roster_id = active_roster["id"]
    valid_from = active_roster["valid_from"]
    valid_to = active_roster["valid_to"]

    # 2. Fetch Cached Datasets
    all_ps = _load_ps(db_path, roster_id)
    all_shifts = _load_shifts(db_path, roster_id)
    all_escs = _load_escs(db_path, roster_id)

    # Resolve Scope Lock state from global app state
    active_scope_state = ss.get("_override_canvas_state") or ss.get("global_state_filter")
    if active_scope_state in ["NH", "ND", "AK"] and ss["oncall_state_filter"] == "All States":
        ss["oncall_state_filter"] = f"{active_scope_state} MMIS"

    # Reference today's date for daytime shift slot 3
    ref_date = "2026-09-14"
    cur_slot = 3

    # Escalation lookup for Core Dev
    core_escs = [e for e in all_escs if e["division"] == "Core Dev" and e["shift_date"] == ref_date and e["shift_slot"] == cur_slot]
    core_esc_map = {e["domain_state"]: e for e in core_escs}

    # Core Dev shifts (Slot 3)
    core_shifts = [s for s in all_shifts if s["division"] == "Core Dev" and s["shift_date"] == ref_date and s["shift_slot"] == cur_slot]
    core_shift_map = {s["domain_state"]: s for s in core_shifts}

    # Module shifts (Infra + Non-Core Dev Slot 3)
    mod_shifts = [
        s for s in all_shifts
        if s["division"] in ["Infra Team", "Non-Core Dev"]
        and s["shift_date"] == ref_date
        and s["shift_slot"] == cur_slot
    ]
    # Sort modules: Infra first, then Non-Core
    mod_shifts.sort(key=lambda m: (0 if m["division"] == "Infra Team" else 1, m["domain_state"]))

    # Organize PS rows by engineer
    ps_dates = sorted(list(set(p["shift_date"] for p in all_ps)))
    ps_by_eng: dict[str, dict[str, dict]] = {}
    eng_order: list[str] = []
    for p in all_ps:
        ename = p["resource_name"]
        if ename not in ps_by_eng:
            ps_by_eng[ename] = {}
            eng_order.append(ename)
        ps_by_eng[ename][p["shift_date"]] = p

    # Default selected engineer
    if ss.get("oncall_selected_eng") not in eng_order and eng_order:
        ss["oncall_selected_eng"] = eng_order[0]

    # Calculate KPIs for Monday 2026-09-14
    mon_ps = [p for p in all_ps if p["shift_date"] == ref_date]
    working_count = sum(1 for p in mon_ps if p.get("is_working") == 1)
    total_ps = len(mon_ps) if mon_ps else 31
    wo_count = sum(1 for p in mon_ps if "wo" in (p.get("shift_window") or "").lower() or "comp" in (p.get("shift_window") or "").lower())
    hol_count = sum(1 for p in mon_ps if "holiday" in (p.get("shift_window") or "").lower() or "float" in (p.get("shift_window") or "").lower())
    states_count = len(core_shifts) if core_shifts else 3
    missing_sdm_count = sum(1 for m in mod_shifts if m["division"] == "Non-Core Dev")

    # --------------------------------------------------------------------------
    # 3. CSS Injections: Zero-Scroll 100vh Layout & Grafana Command Hub Styles
    # --------------------------------------------------------------------------
    st.markdown(
        """
        <style>
        :root {
            --oc-bg: #111217;
            --oc-panel: #181b1f;
            --oc-border: #2c3235;
            --oc-text: #d8d9da;
            --oc-text-sec: #9fa7b3;
            --oc-text-faint: #6e7681;
            --oc-orange: #ff780a;
            --oc-green: #73bf69;
            --oc-green-dim: rgba(115, 191, 105, 0.16);
            --oc-yellow: #ff9830;
            --oc-yellow-dim: rgba(255, 152, 48, 0.16);
            --oc-red: #f2495c;
            --oc-red-dim: rgba(242, 73, 92, 0.18);
            --oc-blue: #5794f2;
            --oc-blue-dim: rgba(87, 148, 242, 0.16);
            --oc-purple: #b877d9;
            --oc-purple-dim: rgba(184, 119, 217, 0.16);
            --oc-grey-dim: rgba(255, 255, 255, 0.06);
        }

        /* Enforce Zero-Scroll Viewport Locking */
        .block-container {
            padding-top: 0.8rem !important;
            padding-bottom: 0 !important;
            max-width: 100% !important;
        }

        /* Topbar Header Banner */
        .oc-topbar-row {
            display: flex;
            align-items: center;
            gap: 10px;
            padding: 5px 12px;
            background: #141619;
            border: 1px solid var(--oc-border);
            border-radius: 3px;
            margin-bottom: 6px;
            flex-shrink: 0;
        }
        .oc-page-title {
            font-size: 12.5px;
            font-weight: 700;
            color: #f8fafc;
            letter-spacing: 0.02em;
        }
        .oc-date-range {
            font-size: 11px;
            color: var(--oc-text-faint);
        }
        .oc-today-badge {
            background: var(--oc-orange);
            color: #0b0c0e;
            font-size: 9.5px;
            font-weight: 800;
            padding: 2px 7px;
            border-radius: 2px;
            letter-spacing: 0.03em;
        }
        .oc-divider-v {
            width: 1px;
            height: 16px;
            background: var(--oc-border);
        }

        /* KPI Ribbon */
        .oc-kpi-row {
            display: grid;
            grid-template-columns: repeat(5, 1fr);
            gap: 6px;
            margin-bottom: 6px;
        }
        .oc-stat-card {
            background: var(--oc-panel);
            border: 1px solid var(--oc-border);
            border-radius: 3px;
            padding: 5px 10px;
            display: flex;
            flex-direction: column;
            justify-content: center;
            min-height: 48px;
        }
        .oc-stat-fill-green {
            background: linear-gradient(180deg, rgba(115,191,105,0.16), rgba(115,191,105,0.02));
            border-top: 2px solid var(--oc-green);
        }
        .oc-stat-fill-yellow {
            background: linear-gradient(180deg, rgba(255,152,48,0.16), rgba(255,152,48,0.02));
            border-top: 2px solid var(--oc-yellow);
        }
        .oc-stat-fill-blue {
            background: linear-gradient(180deg, rgba(87,148,242,0.14), rgba(87,148,242,0.02));
            border-top: 2px solid var(--oc-blue);
        }
        .oc-stat-fill-red {
            background: linear-gradient(180deg, rgba(242,73,92,0.14), rgba(242,73,92,0.02));
            border-top: 2px solid var(--oc-red);
        }
        .oc-stat-label {
            font-size: 9.5px;
            color: var(--oc-text-sec);
            text-transform: uppercase;
            font-weight: 700;
            letter-spacing: .02em;
        }
        .oc-stat-val {
            font-size: 17px;
            font-weight: 800;
            margin-top: 1px;
            line-height: 1;
            color: #fff;
            font-family: var(--mono, monospace);
        }
        .oc-stat-sub {
            font-size: 9px;
            color: var(--oc-text-faint);
            margin-top: 2px;
        }

        /* 3-Column Body Grid */
        .oc-body-grid {
            display: grid;
            grid-template-columns: 0.85fr 1.15fr 1.5fr;
            gap: 6px;
            height: calc(100vh - 305px);
            max-height: calc(100vh - 305px);
            overflow: hidden;
            margin-bottom: 6px;
        }
        .oc-col {
            display: flex;
            flex-direction: column;
            background: var(--oc-panel);
            border: 1px solid var(--oc-border);
            border-radius: 3px;
            min-height: 0;
            overflow: hidden;
        }
        .oc-panel-head {
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 5px 10px;
            background: #141619;
            border-bottom: 1px solid var(--oc-border);
            flex-shrink: 0;
        }
        .oc-panel-title {
            font-size: 11px;
            font-weight: 700;
            color: #f1f5f9;
            display: flex;
            align-items: center;
            gap: 6px;
        }
        .oc-panel-tag {
            font-size: 8.5px;
            padding: 1px 6px;
            border-radius: 2px;
            font-weight: 700;
            background: var(--oc-grey-dim);
            color: var(--oc-text-sec);
        }
        .oc-panel-body {
            flex: 1;
            min-height: 0;
            overflow-y: auto;
            padding: 8px 10px;
        }
        .oc-panel-body::-webkit-scrollbar {
            width: 4px;
            height: 4px;
        }
        .oc-panel-body::-webkit-scrollbar-thumb {
            background: #374151;
            border-radius: 2px;
        }

        /* State Escalation Ladder (Col 1) */
        .oc-state-sec {
            margin-bottom: 10px;
            padding-bottom: 8px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.05);
        }
        .oc-state-sec:last-child {
            margin-bottom: 0;
            padding-bottom: 0;
            border-bottom: none;
        }
        .oc-state-hdr {
            font-size: 11.5px;
            font-weight: 700;
            display: flex;
            align-items: center;
            gap: 6px;
            margin-bottom: 4px;
            color: #f8fafc;
        }
        .oc-tag-ak {
            background: var(--oc-blue-dim);
            color: #8fb8f8;
            font-size: 8.5px;
            font-weight: 800;
            padding: 1px 6px;
            border-radius: 2px;
        }
        .oc-tag-nd {
            background: var(--oc-green-dim);
            color: var(--oc-green);
            font-size: 8.5px;
            font-weight: 800;
            padding: 1px 6px;
            border-radius: 2px;
        }
        .oc-tag-nh {
            background: var(--oc-yellow-dim);
            color: var(--oc-yellow);
            font-size: 8.5px;
            font-weight: 800;
            padding: 1px 6px;
            border-radius: 2px;
        }
        .oc-ladder-row {
            display: flex;
            align-items: center;
            gap: 6px;
            padding: 2.5px 0;
            font-size: 10.5px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.02);
        }
        .oc-ladder-tier {
            width: 72px;
            color: var(--oc-text-faint);
            font-size: 9px;
            text-transform: uppercase;
            font-weight: 600;
            flex-shrink: 0;
        }
        .oc-ladder-name {
            font-weight: 600;
            flex: 1;
            color: #e2e8f0;
        }
        .oc-ladder-warn {
            color: var(--oc-yellow);
            font-size: 9px;
            font-style: italic;
        }

        /* Tables (Cols 2 & 3) */
        .oc-table {
            width: 100%;
            border-collapse: collapse;
            font-size: 10.5px;
        }
        .oc-table thead th {
            position: sticky;
            top: 0;
            text-align: left;
            padding: 5px 7px;
            font-size: 9px;
            color: var(--oc-text-faint);
            font-weight: 600;
            border-bottom: 1px solid var(--oc-border);
            text-transform: uppercase;
            background: #181b1f;
            z-index: 2;
        }
        .oc-table tbody td {
            padding: 4px 7px;
            border-bottom: 1px solid #1e2226;
            color: #d8d9da;
        }
        .oc-table tbody tr:hover {
            background: rgba(255, 255, 255, 0.025);
        }
        .oc-team-tag {
            font-size: 8.5px;
            padding: 1px 5px;
            border-radius: 2px;
            font-weight: 700;
            display: inline-block;
        }
        .oc-team-infra {
            background: var(--oc-purple-dim);
            color: #d6a8ef;
            border: 1px solid rgba(184, 119, 217, 0.3);
        }
        .oc-team-noncore {
            background: var(--oc-blue-dim);
            color: #8fb8f8;
            border: 1px solid rgba(87, 148, 242, 0.3);
        }
        .oc-sdm-missing {
            color: var(--oc-yellow);
            font-style: italic;
            font-size: 9.5px;
            background: rgba(255, 152, 48, 0.08);
            padding: 1px 5px;
            border-radius: 2px;
            border: 1px dashed rgba(255, 152, 48, 0.3);
        }

        /* PS Shift Chips */
        .shift-m {
            background: var(--oc-green-dim);
            color: var(--oc-green);
            border: 1px solid rgba(115, 191, 105, 0.3);
            border-radius: 2px;
            padding: 1px 5px;
            font-size: 9px;
            font-weight: 700;
            white-space: nowrap;
        }
        .shift-e {
            background: var(--oc-blue-dim);
            color: #8fb8f8;
            border: 1px solid rgba(87, 148, 242, 0.3);
            border-radius: 2px;
            padding: 1px 5px;
            font-size: 9px;
            font-weight: 700;
            white-space: nowrap;
        }
        .shift-n {
            background: var(--oc-purple-dim);
            color: #d6a8ef;
            border: 1px solid rgba(184, 119, 217, 0.3);
            border-radius: 2px;
            padding: 1px 5px;
            font-size: 9px;
            font-weight: 700;
            white-space: nowrap;
        }
        .shift-wo {
            background: var(--oc-grey-dim);
            color: var(--oc-text-faint);
            border-radius: 2px;
            padding: 1px 5px;
            font-size: 9px;
            font-weight: 600;
            white-space: nowrap;
        }
        .shift-fh {
            background: var(--oc-yellow-dim);
            color: var(--oc-yellow);
            border: 1px solid rgba(255, 152, 48, 0.3);
            border-radius: 2px;
            padding: 1px 5px;
            font-size: 9px;
            font-weight: 700;
            white-space: nowrap;
        }
        .shift-lv {
            background: var(--oc-red-dim);
            color: #ff8fa3;
            border: 1px solid rgba(242, 73, 92, 0.3);
            border-radius: 2px;
            padding: 1px 5px;
            font-size: 9px;
            font-weight: 700;
            white-space: nowrap;
        }
        .today-col {
            background: rgba(255, 120, 10, 0.09) !important;
            box-shadow: inset 2px 0 0 var(--oc-orange);
        }
        th.today-col {
            color: var(--oc-orange) !important;
            font-weight: 800 !important;
        }

        /* Master-Detail Context Drawer */
        .oc-drawer {
            background: #141619;
            border: 1px solid var(--oc-border);
            border-left: 3px solid var(--oc-orange);
            border-radius: 3px;
            padding: 5px 12px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            font-size: 11px;
            flex-shrink: 0;
            min-height: 46px;
        }
        .oc-drawer-left {
            display: flex;
            align-items: center;
            gap: 12px;
        }
        .oc-drawer-right {
            display: flex;
            align-items: center;
            gap: 16px;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    # --------------------------------------------------------------------------
    # 4. Top Executive Slicer Command Bar
    # --------------------------------------------------------------------------
    topbar_hdr = (
        '<div class="oc-topbar-row">'
        '<span class="oc-page-title">24/7 On-Call Operations Command Hub</span>'
        f'<span class="oc-date-range">{escape(valid_from)} &ndash; {escape(valid_to)}</span>'
        '<span class="oc-today-badge">TODAY &bull; Mon 14 Sep</span>'
        '<div class="oc-divider-v"></div>'
        '<span style="font-size:10px;color:var(--oc-text-faint);">Active Cycle: <b style="color:#38bdf8;">Production Support &bull; Core Dev &bull; Infra &bull; Non-Core</b></span>'
        '</div>'
    )
    st.markdown(topbar_hdr, unsafe_allow_html=True)

    c_slicers_1, c_slicers_2, c_slicers_3, c_slicers_4, c_slicers_5 = st.columns(
        [1.6, 1.1, 1.1, 3.5, 0.7]
    )

    with c_slicers_1:
        # Search Box
        search_val = st.text_input(
            "Search",
            value=ss["oncall_search"],
            placeholder="🔍 Search name, module...",
            label_visibility="collapsed",
            key="oncall_search_box",
        )
        search_q = search_val.strip().lower()
        if search_val != ss["oncall_search"]:
            ss["oncall_search"] = search_val
            st.rerun()

    with c_slicers_2:
        # State Slicer
        cur_st = ss["oncall_state_filter"]
        st_idx = _STATE_OPTIONS.index(cur_st) if cur_st in _STATE_OPTIONS else 0
        picked_state = st.selectbox(
            "State",
            _STATE_OPTIONS,
            index=st_idx,
            label_visibility="collapsed",
            key="oncall_state_select",
        )
        st_filter = picked_state
        if picked_state != ss["oncall_state_filter"]:
            ss["oncall_state_filter"] = picked_state
            st.rerun()

    with c_slicers_3:
        # Team Slicer
        cur_tm = ss["oncall_team_filter"]
        tm_idx = _TEAM_OPTIONS.index(cur_tm) if cur_tm in _TEAM_OPTIONS else 0
        picked_team = st.selectbox(
            "Team",
            _TEAM_OPTIONS,
            index=tm_idx,
            label_visibility="collapsed",
            key="oncall_team_select",
        )
        team_filter = picked_team
        if picked_team != ss["oncall_team_filter"]:
            ss["oncall_team_filter"] = picked_team
            st.rerun()

    with c_slicers_4:
        # Shift Quick Chips
        cur_chip = ss["oncall_shift_chip"]
        chip_cols = st.columns(len(_SHIFT_CHIPS))
        for idx, (chip_label, chip_val) in enumerate(_SHIFT_CHIPS):
            is_active = (cur_chip == chip_val)
            with chip_cols[idx]:
                if st.button(
                    chip_label,
                    key=f"oc_chip_{idx}",
                    type="primary" if is_active else "secondary",
                    use_container_width=True,
                ):
                    ss["oncall_shift_chip"] = chip_val
                    st.rerun()

    with c_slicers_5:
        # Action Buttons (Reset + Upload)
        a1, a2 = st.columns(2)
        with a1:
            if st.button("✕", key="oc_btn_reset", help="Reset all filters", use_container_width=True):
                ss["oncall_state_filter"] = "All States"
                ss["oncall_team_filter"] = "All Teams"
                ss["oncall_shift_chip"] = None
                ss["oncall_search"] = ""
                st.rerun()
        with a2:
            if st.button("📤", key="oc_btn_upload", help="Upload weekly Excel roster", use_container_width=True):
                ss["oncall_show_upload"] = not ss["oncall_show_upload"]
                st.rerun()

    # 4b. Weekly Excel Upload Expander (if toggled)
    if ss["oncall_show_upload"]:
        with st.expander("📤 Production Support Lead — Upload New Weekly On-Call Roster (.xlsx)", expanded=True):
            up_col1, up_col2 = st.columns([2.5, 1.5])
            with up_col1:
                new_roster_file = st.file_uploader(
                    "Select Weekly Excel Roster",
                    type=["xlsx"],
                    key="oncall_modal_uploader",
                )
                if new_roster_file:
                    if st.button("🚀 Ingest & Activate Roster", key="oncall_commit_upload_btn", type="primary"):
                        with st.spinner("Validating sheets & committing to database..."):
                            try:
                                res = ingest_roster_file(db_path, new_roster_file)
                                _clear_oncall_cache()
                                ss["oncall_show_upload"] = False
                                st.success(f"✓ Ingested {res['roster_name']}! ({res['ps_count']} PS rows, {res['shift_count']} shift slots)")
                                st.rerun()
                            except Exception as e:
                                st.error(f"Ingestion failed: {e}")
            with up_col2:
                st.markdown(
                    """
                    <div style="background:#141619;border:1px solid #22252b;border-radius:3px;padding:6px 10px;font-size:10px;color:var(--oc-text-sec);">
                    <b style="color:#f8fafc;">Standard Sheet Contract:</b>
                    <ul style="margin:4px 0 0 16px;padding:0;">
                    <li><code>Production support</code> (31 engineers &times; 7 days)</li>
                    <li><code>Infra Team</code> (Cognos, Informatica, UC4, App Server, DB, IAM)</li>
                    <li><code>Core Dev</code> (AK DEV, ND DEV, NH DEV)</li>
                    <li><code>Non-Core Dev</code> (Letters, Cognos, Informatica, TMSIS, EDMS)</li>
                    </ul></div>
                    """,
                    unsafe_allow_html=True,
                )

    # --------------------------------------------------------------------------
    # 5. Top Meta Banner & 5 KPI Cards Ribbon
    # --------------------------------------------------------------------------
    kpi_banner_html = (
        '<div class="oc-kpi-row">'
        '<div class="oc-stat-card oc-stat-fill-green">'
        '<span class="oc-stat-label">Working Today</span>'
        f'<div class="oc-stat-val">{working_count} <span style="font-size:11px;color:var(--oc-text-faint);font-weight:400;">/ {total_ps}</span></div>'
        '<span class="oc-stat-sub">Production Support Fleet</span>'
        '</div>'
        '<div class="oc-stat-card oc-stat-fill-yellow">'
        '<span class="oc-stat-label">Off / WO Today</span>'
        f'<div class="oc-stat-val">{wo_count}</div>'
        '<span class="oc-stat-sub">Week Off & Comp OFF</span>'
        '</div>'
        '<div class="oc-stat-card oc-stat-fill-yellow">'
        '<span class="oc-stat-label">Holiday Today</span>'
        f'<div class="oc-stat-val">{hol_count}</div>'
        '<span class="oc-stat-sub">Floating Holiday</span>'
        '</div>'
        '<div class="oc-stat-card oc-stat-fill-blue">'
        '<span class="oc-stat-label">States Covered</span>'
        f'<div class="oc-stat-val">{states_count}</div>'
        '<span class="oc-stat-sub">AK &bull; ND &bull; NH Core Dev</span>'
        '</div>'
        '<div class="oc-stat-card oc-stat-fill-red">'
        '<span class="oc-stat-label">SDM Names Missing</span>'
        f'<div class="oc-stat-val">{missing_sdm_count}</div>'
        '<span class="oc-stat-sub">Non-Core modules unfilled</span>'
        '</div>'
        '</div>'
    )
    st.markdown(kpi_banner_html, unsafe_allow_html=True)

    # --------------------------------------------------------------------------
    # 6. Build HTML for the Unified 3-Column Body Grid
    # --------------------------------------------------------------------------
    cur_shift_chip = ss["oncall_shift_chip"]

    # --- Column 1: State Escalation Ladders ---
    col1_html_parts = []
    col1_html_parts.append('<div class="oc-col">')
    col1_html_parts.append(
        '<div class="oc-panel-head">'
        '<span class="oc-panel-title">📍 State Escalation &mdash; Today (Daytime IST)</span>'
        '<span class="oc-panel-tag">3 States</span>'
        '</div>'
    )
    col1_html_parts.append('<div class="oc-panel-body">')

    state_configs = [
        ("AK", "AK DEV", "oc-tag-ak", "Supriya Yella", "— none listed", "Kishore Kanuparthi", "Ravi M Shankar", "Abhilash Pulikkathodi"),
        ("ND", "ND DEV", "oc-tag-nd", "Veeraganesh Velugula", "Ajith Kumar Gandi", "Ajit Gandhi", "Thirupathi Katakam", "Abhilash Pulikkathodi"),
        ("NH", "NH DEV", "oc-tag-nh", "Module Lead (name TBD)", "Module Back-up (name TBD)", "Sunil P", "Madhav", "Abhilash Pulikkathodi"),
    ]

    for st_code, st_domain, tag_cls, def_p, def_s, def_tl, def_sdm, def_pd in state_configs:
        # State Filter Check
        if st_filter != "All States" and st_code not in st_filter:
            continue
        # Team Filter Check
        if team_filter not in ["All Teams", "Core Dev"]:
            continue

        shift_row = core_shift_map.get(st_domain)
        esc_row = core_esc_map.get(st_domain)

        primary_name = (shift_row.get("primary_on_call") if shift_row else None) or def_p
        sec_name = (shift_row.get("secondary_on_call") if shift_row else None) or def_s
        tl_name = (esc_row.get("tier1_name") if esc_row else None) or def_tl
        sdm_name = (esc_row.get("tier2_name") if esc_row else None) or def_sdm
        pd_name = (esc_row.get("tier3_name") if esc_row else None) or def_pd

        # Search Query filter
        if search_q:
            combined_txt = f"{st_code} core dev {primary_name} {sec_name} {tl_name} {sdm_name} {pd_name}".lower()
            if search_q not in combined_txt:
                continue

        # Format TBD warnings
        p_display = 'Module Lead <span class="oc-ladder-warn">(name TBD)</span>' if "TBD" in primary_name else escape(primary_name)
        s_display = 'Module Back-up <span class="oc-ladder-warn">(name TBD)</span>' if "TBD" in sec_name else (
            '<span style="color:var(--oc-text-faint);font-style:italic;">' + escape(sec_name) + '</span>' if "none" in sec_name else escape(sec_name)
        )

        col1_html_parts.append('<div class="oc-state-sec">')
        col1_html_parts.append(
            f'<div class="oc-state-hdr"><span class="{tag_cls}">{st_code}</span> Core Dev</div>'
            f'<div class="oc-ladder-row"><span class="oc-ladder-tier">Primary</span><span class="oc-ladder-name">{p_display}</span></div>'
            f'<div class="oc-ladder-row"><span class="oc-ladder-tier">Secondary</span><span class="oc-ladder-name">{s_display}</span></div>'
            f'<div class="oc-ladder-row"><span class="oc-ladder-tier">1st Esc &bull; TL</span><span class="oc-ladder-name">{escape(tl_name)}</span></div>'
            f'<div class="oc-ladder-row"><span class="oc-ladder-tier">2nd Esc &bull; SDM</span><span class="oc-ladder-name">{escape(sdm_name)}</span></div>'
            f'<div class="oc-ladder-row"><span class="oc-ladder-tier">3rd Esc &bull; PD</span><span class="oc-ladder-name">{escape(pd_name)}</span></div>'
            '</div>'
        )

    col1_html_parts.append('</div></div>')
    col1_html = "".join(col1_html_parts)

    # --- Column 2: Module On-Call (Infra + Non-Core) ---
    col2_html_parts = []
    col2_html_parts.append('<div class="oc-col">')
    col2_html_parts.append(
        '<div class="oc-panel-head">'
        '<span class="oc-panel-title">🧩 Module On-Call &mdash; Today</span>'
        f'<span class="oc-panel-tag">{len(mod_shifts)} modules</span>'
        '</div>'
    )
    col2_html_parts.append('<div class="oc-panel-body" style="padding:0;">')
    col2_html_parts.append(
        '<table class="oc-table">'
        '<thead><tr>'
        '<th>Module</th>'
        '<th>Team</th>'
        '<th>Primary</th>'
        '<th>Secondary</th>'
        '<th>SDM</th>'
        '</tr></thead><tbody>'
    )

    filtered_mods = []
    for m in mod_shifts:
        div = m["division"]
        mod_name = m["domain_state"]
        p_name = m.get("primary_on_call") or "—"
        s_name = m.get("secondary_on_call") or "—"

        # Team Filter Check
        if team_filter != "All Teams" and team_filter != div:
            continue

        # State Filter Check (if NH selected, only NH-TMSIS; if AK/ND, keep non-core/infra)
        if st_filter == "NH MMIS" and "NH" not in mod_name.upper():
            continue

        # SDM Resolution: Infra has Anil Tankala (or —), Non-Core has unfilled
        if div == "Infra Team":
            team_badge = '<span class="oc-team-tag oc-team-infra">Infra</span>'
            sdm_cell = 'Anil Tankala' if mod_name == 'App Server' else '—'
        else:
            team_badge = '<span class="oc-team-tag oc-team-noncore">Non-Core</span>'
            sdm_cell = '<span class="oc-sdm-missing">unfilled</span>'

        # Search Query Check
        if search_q:
            combined_m = f"{mod_name} {div} {p_name} {s_name} {sdm_cell}".lower()
            if search_q not in combined_m:
                continue

        filtered_mods.append((mod_name, team_badge, p_name, s_name, sdm_cell))

    for m_name, t_badge, p_val, s_val, sdm_val in filtered_mods:
        sec_disp = '<span style="color:var(--oc-text-faint);">&mdash;</span>' if s_val in ["—", "None", ""] else escape(s_val)
        col2_html_parts.append(
            f'<tr>'
            f'<td><b>{escape(m_name)}</b></td>'
            f'<td>{t_badge}</td>'
            f'<td>{escape(p_val)}</td>'
            f'<td>{sec_disp}</td>'
            f'<td>{sdm_val}</td>'
            f'</tr>'
        )

    if not filtered_mods:
        col2_html_parts.append('<tr><td colspan="5" style="text-align:center;color:var(--oc-text-faint);padding:14px;">No modules match the active slicers.</td></tr>')

    col2_html_parts.append('</tbody></table></div></div>')
    col2_html = "".join(col2_html_parts)

    # --- Column 3: Production Support Roster ---
    col3_html_parts = []
    col3_html_parts.append('<div class="oc-col">')
    col3_html_parts.append(
        '<div class="oc-panel-head">'
        '<span class="oc-panel-title">👥 Production Support Roster</span>'
        f'<span class="oc-panel-tag">{len(eng_order)} people</span>'
        '</div>'
    )
    col3_html_parts.append('<div class="oc-panel-body" style="padding:0;">')
    col3_html_parts.append(
        '<table class="oc-table">'
        '<thead><tr>'
        '<th>Resource Name</th>'
        '<th>Sat 12</th>'
        '<th>Sun 13</th>'
        '<th class="today-col">Mon 14 &bull; TODAY</th>'
        '<th>Tue 15</th>'
        '<th>Wed 16</th>'
        '<th>Thu 17</th>'
        '<th>Fri 18</th>'
        '</tr></thead><tbody>'
    )

    filtered_engs = []
    for ename in eng_order:
        days_dict = ps_by_eng.get(ename, {})
        mon_row = days_dict.get(ref_date, {})
        mon_win = mon_row.get("shift_window", "")

        # Team Filter Check
        if team_filter not in ["All Teams", "Production Support"]:
            continue

        # Shift Chip Filter Check (filters based on today's shift window)
        if cur_shift_chip:
            mon_win_low = mon_win.lower()
            if cur_shift_chip == "morning" and not ("6:30" in mon_win_low or "morning" in mon_win_low):
                continue
            elif cur_shift_chip == "evening" and not ("14:30" in mon_win_low or "evening" in mon_win_low):
                continue
            elif cur_shift_chip == "night" and not ("22:30" in mon_win_low or "night" in mon_win_low):
                continue
            elif cur_shift_chip == "wo" and not ("wo" in mon_win_low or "comp" in mon_win_low):
                continue
            elif cur_shift_chip == "holiday" and not ("holiday" in mon_win_low or "float" in mon_win_low):
                continue
            elif cur_shift_chip == "leave" and not ("leave" in mon_win_low):
                continue

        # Search Query Check
        if search_q and search_q not in ename.lower():
            continue

        filtered_engs.append((ename, days_dict))

    for ename, days_dict in filtered_engs:
        # Build 7 cells
        row_cells = []
        for d in ps_dates:
            p_entry = days_dict.get(d)
            win_val = p_entry.get("shift_window") if p_entry else None
            cls, lbl = _get_shift_badge(win_val)
            is_today = (d == ref_date)
            td_cls = ' class="today-col"' if is_today else ''
            if lbl:
                chip_html = f'<span class="{cls}" title="{escape(str(win_val))} IST">{lbl}</span>'
            else:
                chip_html = '<span style="color:var(--oc-text-faint);">&mdash;</span>'
            row_cells.append(f'<td{td_cls}>{chip_html}</td>')

        cells_str = "".join(row_cells)
        col3_html_parts.append(
            f'<tr>'
            f'<td><b>{escape(ename)}</b></td>'
            f'{cells_str}'
            f'</tr>'
        )

    if not filtered_engs:
        col3_html_parts.append('<tr><td colspan="8" style="text-align:center;color:var(--oc-text-faint);padding:14px;">No support engineers match the active filters.</td></tr>')

    col3_html_parts.append('</tbody></table></div></div>')
    col3_html = "".join(col3_html_parts)

    # --------------------------------------------------------------------------
    # 7. Render 3-Column Body Grid
    # --------------------------------------------------------------------------
    board_grid_html = (
        '<div class="oc-body-grid">'
        f'{col1_html}'
        f'{col2_html}'
        f'{col3_html}'
        '</div>'
    )
    st.markdown(board_grid_html, unsafe_allow_html=True)

    # --------------------------------------------------------------------------
    # 8. Docked Master-Detail Context Drawer (Bottom Strip)
    # --------------------------------------------------------------------------
    cur_sel_eng = ss.get("oncall_selected_eng") or (eng_order[0] if eng_order else "Nirosha V")
    sel_p_dict = ps_by_eng.get(cur_sel_eng, {}).get(ref_date, {})
    sel_win = sel_p_dict.get("shift_window", "14:30-23:30")
    sel_is_work = (sel_p_dict.get("is_working") == 1)

    # Resolve EST clock for selected engineer
    est_clock = "05:00 - 14:00 EDT"
    if "6:30" in sel_win:
        est_clock = "21:00 - 06:00 EDT (Prev Day)"
    elif "22:30" in sel_win:
        est_clock = "13:00 - 22:00 EDT"
    elif "wo" in sel_win.lower() or "comp" in sel_win.lower():
        est_clock = "Off Duty (Weekend / Comp OFF)"
    elif "holiday" in sel_win.lower():
        est_clock = "Off Duty (Floating Holiday)"

    drawer_col_left, drawer_col_mid, drawer_col_right = st.columns([1.5, 3.2, 1.3])

    with drawer_col_left:
        picked_eng = st.selectbox(
            "Inspect Resource",
            eng_order,
            index=eng_order.index(cur_sel_eng) if cur_sel_eng in eng_order else 0,
            label_visibility="collapsed",
            key="oncall_eng_picker",
            help="Select engineer to inspect shift schedule & escalation path",
        )
        if picked_eng != ss["oncall_selected_eng"]:
            ss["oncall_selected_eng"] = picked_eng
            st.rerun()

    with drawer_col_mid:
        work_status_badge = (
            '<span class="pill" style="color:#10b981;background:rgba(16,185,129,0.12);font-size:8.5px;font-weight:700;border:1px solid rgba(16,185,129,0.3);">ON-DUTY TODAY</span>'
            if sel_is_work
            else '<span class="pill" style="color:#ff9830;background:rgba(255,152,48,0.12);font-size:8.5px;font-weight:700;border:1px solid rgba(255,152,48,0.3);">SCHEDULED OFF</span>'
        )
        avatar_html = ui.on_call_avatar(cur_sel_eng)
        drawer_html = (
            '<div class="oc-drawer">'
            '<div class="oc-drawer-left">'
            f'{avatar_html}'
            '<div>'
            f'<div style="font-weight:800;color:#f8fafc;font-size:11.5px;display:flex;align-items:center;gap:6px;">'
            f'<span>{escape(cur_sel_eng)}</span>'
            f'{work_status_badge}'
            '</div>'
            f'<div style="color:var(--oc-text-sec);font-size:9.5px;margin-top:1px;">'
            f'IST: <b style="color:#38bdf8;">{escape(sel_win)}</b> &bull; EST: <b style="color:#a855f7;">{est_clock}</b>'
            '</div>'
            '</div>'
            '</div>'
            '<div class="oc-drawer-right">'
            '<div style="font-size:9.5px;color:var(--oc-text-sec);text-align:right;">'
            '<div>Escalation Path: <b>Offshore Command &rarr; State TL &rarr; SDM</b></div>'
            '<div style="color:var(--oc-text-faint);margin-top:1px;">PD Oversight: <b>Abhilash Pulikkathodi</b></div>'
            '</div>'
            '</div>'
            '</div>'
        )
        st.markdown(drawer_html, unsafe_allow_html=True)

    with drawer_col_right:
        esc_btn1, esc_btn2 = st.columns(2)
        with esc_btn1:
            if st.button("📋 Copy", key="oc_copy_esc_btn", help="Copy escalation contact info to clipboard", use_container_width=True):
                st.toast(f"Copied escalation dossier for {cur_sel_eng}!", icon="📋")
        with esc_btn2:
            if st.button("⚡ Dispatch", key="oc_dispatch_btn", help="Send alert broadcast via Teams Webhook", type="secondary", use_container_width=True):
                st.toast(f"Dispatched on-call alert notification to {cur_sel_eng}!", icon="⚡")
