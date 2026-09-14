"""
on_call.py — 24/7 On-Call Operations Command Hub & Roster Intelligence
======================================================================
Enterprise Grafana-style zero-scroll dashboard for 24x7 on-call schedules,
cross-tab shift matrices, multi-tier escalation hierarchy lineage, and
weekly Excel roster updates.

Follows Enterprise Design Standards:
  - Zero-page-scroll constraint (100vh viewport locking)
  - Power BI Master-Detail split pane (58% master table / 42% inspector)
  - Cognos hierarchical crosstab with conditional color variables
  - Dynamic EST <-> IST timezone conversion toggle
  - High-performance caching (@st.cache_data ttl=120)
  - Multi-Dimensional Slicers:
      * State Slicer: All States, NH MMIS, ND MMIS, AK MMIS
      * Location Slicer: All Locations, Offshore (IST), Onshore (EST)
      * SDM Lead Slicer: Anil Tankala, Kishore Nagarajan, Ravi M Shankar,
                         Thirupathi Katakam, Anil Kumar Khamari, Madhav, Dipak/Rama
      * Day Slicer & Shift-Type Quick Chips
      * Quick Status: All Resources, Working Today, Off / Holiday
      * Full-text search across domains and resources
  - Horizontal scrollable cross-tab preserving all 7 days (including FRI)
  - Shift pagination and synchronized engineer inspection
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

_STATE_OPTIONS = ["All States", "NH MMIS", "ND MMIS", "AK MMIS"]
_LOCATION_OPTIONS = ["All Locations", "🌏 Offshore (IST)", "🏛️ Onshore (EST)"]
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
    ("🌅 Morning (06:30-15:30)", "morning"),
    ("☀️ Evening (14:30-23:30)", "evening"),
    ("🌙 Night (22:30-07:30)", "night"),
    ("— Week Off", "wo"),
    ("🎉 Holiday", "holiday"),
    ("✕ Leave", "leave"),
]

_PAGE_SIZE = 14



def _compact_shift_chip(win_str: str) -> str:
    """Render compact chip (M, E, N, WO, Comp, Hol, Lv) with full timing tooltip."""
    if not win_str:
        return "<span style='color:#6e7681;'>—</span>"
    s = str(win_str).strip()
    s_low = s.lower()
    if s.startswith("6:30") or s.startswith("06:30") or "morning" in s_low:
        cls, lbl = "shift-badge-m", "M"
    elif s.startswith("14:30") or "evening" in s_low:
        cls, lbl = "shift-badge-e", "E"
    elif s.startswith("22:30") or "night" in s_low:
        cls, lbl = "shift-badge-n", "N"
    elif s == "WO" or "week off" in s_low:
        cls, lbl = "shift-badge-wo", "WO"
    elif "comp" in s_low:
        cls, lbl = "shift-badge-wo", "Comp"
    elif "holiday" in s_low or "float" in s_low:
        cls, lbl = "shift-badge-fh", "Hol"
    elif "leave" in s_low:
        cls, lbl = "shift-badge-lv", "Lv"
    else:
        cls, lbl = "shift-badge-wo", s[:4]
    return f'<span class="{cls}" title="{escape(s)} IST">{lbl}</span>'


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


def _resolve_shift_governing_lead(s: dict, esc_lookup: dict, esc_fallback: dict) -> tuple[str, str]:
    """
    Returns (lead_name, lead_title).
    For Non-Core Dev: returns Technical Manager (TL / TM).
    For Infra Team / Core Dev: returns Governing SDM.
    """
    div = s.get("division", "")
    dom = s.get("domain_state", "")
    slot = s.get("shift_slot", 1)
    sdate = s.get("shift_date", "")

    if div == "Non-Core Dev":
        k_exact = (div, dom, slot, sdate)
        e = esc_lookup.get(k_exact) or esc_fallback.get((div, dom))
        if e and e.get("tier1_name"):
            t1 = e["tier1_name"].strip()
            if t1:
                return (t1, "Technical Manager (TM)")
        # Fallback based on slot and domain
        if slot in [3, 4]:
            return ("Bichitra Sahoo", "Technical Manager (TM)")
        elif "cognos" in dom.lower():
            return ("Irfan", "Technical Manager (TM)")
        elif "letter" in dom.lower() or "edms" in dom.lower():
            return ("Mahesh Mogali", "Technical Manager (TM)")
        elif "informatica" in dom.lower() or "tmsis" in dom.lower():
            return ("Kishore Nagarajan", "Technical Manager (TM)")
        return ("Technical Manager", "Technical Manager (TM)")

    if div == "Infra Team":
        return ("Anil Tankala", "Governing SDM")

    # Core Dev
    k_exact = (div, dom, slot, sdate)
    e = esc_lookup.get(k_exact) or esc_fallback.get((div, dom))
    if e and e.get("tier2_name"):
        sdm_name = e["tier2_name"].strip()
        if sdm_name != "State Specific SDM":
            return (sdm_name, "Governing SDM")

    if "NH" in dom.upper():
        return ("Madhav" if slot in [3, 4] else "Dipak/Rama", "Governing SDM")
    elif "ND" in dom.upper():
        return ("Thirupathi Katakam" if slot in [3, 4] else "Anil Kumar Khamari", "Governing SDM")
    elif "AK" in dom.upper():
        return ("Ravi M Shankar" if slot in [3, 4] else "Kishore Nagarajan", "Governing SDM")

    return ("Kishore Nagarajan" if slot in [1, 2] else "Anil Tankala", "Governing SDM")


def _consolidate_contiguous_shifts(shifts: list[dict]) -> list[dict]:
    """
    Consolidates contiguous duplicate shift slots (e.g. Slot 1 & 2 for same engineer,
    or Slot 3 & 4 for same engineer on the same day) into a single coherent shift row.
    This eliminates repetitive duplicate rows while preserving all scheduling details.
    """
    if not shifts:
        return []

    sorted_s = sorted(shifts, key=lambda x: (x.get("domain_state", ""), x.get("shift_date", ""), x.get("shift_slot", 0)))
    out = []
    i = 0
    while i < len(sorted_s):
        curr = sorted_s[i].copy()
        if i + 1 < len(sorted_s):
            nxt = sorted_s[i + 1]
            same_dom = (curr.get("domain_state") == nxt.get("domain_state"))
            same_date = (curr.get("shift_date") == nxt.get("shift_date"))
            same_prim = (curr.get("primary_on_call") == nxt.get("primary_on_call"))
            same_sec = (curr.get("secondary_on_call") == nxt.get("secondary_on_call"))
            same_lead = (curr.get("governed_sdm") == nxt.get("governed_sdm"))
            is_pair = (curr.get("shift_slot") in [1, 3] and nxt.get("shift_slot") == curr.get("shift_slot") + 1)

            if same_dom and same_date and same_prim and same_sec and same_lead and is_pair:
                curr_t_ist = curr.get("time_ist", "")
                nxt_t_ist = nxt.get("time_ist", "")
                curr_t_est = curr.get("time_est", "")
                nxt_t_est = nxt.get("time_est", "")

                merged_ist = f"{curr_t_ist.split(' to ')[0]} to {nxt_t_ist.split(' to ')[-1].strip()}" if (" to " in curr_t_ist and " to " in nxt_t_ist) else curr_t_ist
                merged_est = f"{curr_t_est.split(' to ')[0]} to {nxt_t_est.split(' to ')[-1].strip()}" if (" to " in curr_t_est and " to " in nxt_t_est) else curr_t_est

                curr["time_ist"] = merged_ist
                curr["time_est"] = merged_est
                curr["slot_label"] = "Offshore (Day)" if curr.get("shift_slot") == 1 else "Onshore (Night)"
                curr["is_consolidated"] = True
                curr["original_slots"] = [curr.get("shift_slot"), nxt.get("shift_slot")]
                out.append(curr)
                i += 2
                continue

        curr["slot_label"] = f"Slot {curr.get('shift_slot', 1)}"
        curr["is_consolidated"] = False
        curr["original_slots"] = [curr.get("shift_slot")]
        out.append(curr)
        i += 1

    return out


@st.cache_data(ttl=300, show_spinner=False)
def _get_roster_bundle(db_path: str, roster_id: int) -> dict:
    conn = get_connection(db_path)
    try:
        all_ps = [dict(r) for r in get_on_call_production_support(conn, roster_id)]
        all_shifts = [dict(r) for r in get_on_call_shifts(conn, roster_id)]
        all_escs = [dict(r) for r in get_on_call_escalations(conn, roster_id)]
    finally:
        conn.close()

    esc_lookup = {
        (e["division"], e["domain_state"], e["shift_slot"], e["shift_date"]): e
        for e in all_escs
    }
    esc_fallback = {
        (e["division"], e["domain_state"]): e
        for e in all_escs
    }

    shifts_with_sdm = []
    for s in all_shifts:
        s_copy = s.copy()
        lead_name, lead_title = _resolve_shift_governing_lead(s_copy, esc_lookup, esc_fallback)
        s_copy["governed_sdm"] = lead_name
        s_copy["governing_title"] = lead_title
        shifts_with_sdm.append(s_copy)

    date_day_map: dict[str, str] = {}
    for p in all_ps:
        date_day_map[p["shift_date"]] = p["day_name"]
    for s in all_shifts:
        date_day_map[s["shift_date"]] = s["day_name"]

    ps_dates = sorted(list(set(p["shift_date"] for p in all_ps)))
    all_unique_dates = sorted(list(set(date_day_map.keys())))
    day_options = ["All Days"] + [f"{date_day_map.get(d, 'Day')} ({d})" for d in all_unique_dates]

    ref_date = "2026-09-14"
    mon_ps = [p for p in all_ps if p["shift_date"] == ref_date]
    working_count = sum(1 for p in mon_ps if p.get("is_working") == 1)
    total_ps = len(mon_ps) if mon_ps else 31
    wo_count = sum(1 for p in mon_ps if "wo" in (p.get("shift_window") or "").lower() or "comp" in (p.get("shift_window") or "").lower())
    hol_count = sum(1 for p in mon_ps if "holiday" in (p.get("shift_window") or "").lower() or "float" in (p.get("shift_window") or "").lower())
    states_count = len(set(s["domain_state"] for s in shifts_with_sdm if s["division"] == "Core Dev")) or 3

    nc_tms = set(
        s["governed_sdm"] for s in shifts_with_sdm
        if s["division"] == "Non-Core Dev" and s.get("governed_sdm")
    )
    tm_count = len(nc_tms) or 4
    distinct_ps_engineers = len(set(p["resource_name"] for p in all_ps))

    return {
        "all_ps": all_ps,
        "all_shifts": all_shifts,
        "all_escs": all_escs,
        "esc_lookup": esc_lookup,
        "esc_fallback": esc_fallback,
        "shifts_with_sdm": shifts_with_sdm,
        "date_day_map": date_day_map,
        "ps_dates": ps_dates,
        "all_unique_dates": all_unique_dates,
        "day_options": day_options,
        "mon_ps": mon_ps,
        "working_count": working_count,
        "total_ps": total_ps,
        "wo_count": wo_count,
        "hol_count": hol_count,
        "states_count": states_count,
        "nc_tms": nc_tms,
        "tm_count": tm_count,
        "distinct_ps_engineers": distinct_ps_engineers,
    }


@st.cache_data(ttl=300, show_spinner=False)
def _load_roster_meta(db_path: str) -> dict | None:
    conn = get_connection(db_path)
    try:
        r = get_active_on_call_roster(conn)
        return dict(r) if r else None
    finally:
        conn.close()


def _clear_oncall_cache() -> None:
    _load_roster_meta.clear()
    _get_roster_bundle.clear()


# --------------------------------------------------------------------------
# Main Workspace Component
# --------------------------------------------------------------------------
def render_on_call_workspace(db_path: str) -> None:
    """Render the 24/7 On-Call Operations Command Hub."""
    ss = st.session_state

    # 0. Session State Initialization
    ss.setdefault("oncall_div", "Live Ops Radar")
    ss.setdefault("oncall_tz", "IST")
    ss.setdefault("oncall_state_filter", "All States")
    ss.setdefault("oncall_location_filter", "All Locations")
    ss.setdefault("oncall_sdm_filter", "All SDMs")
    ss.setdefault("oncall_day_filter", "All Days")
    ss.setdefault("oncall_shift_chip", None)
    ss.setdefault("oncall_quick_status", "All")
    ss.setdefault("oncall_search", "")
    ss.setdefault("oncall_selected_eng", None)
    ss.setdefault("oncall_show_upload", False)
    ss.setdefault("oncall_shift_page", 0)

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

    # 2. Fetch Cached Datasets from High-Performance Bundle
    bundle = _get_roster_bundle(db_path, roster_id)
    all_ps = bundle["all_ps"]
    all_shifts = bundle["all_shifts"]
    all_escs = bundle["all_escs"]
    esc_lookup = bundle["esc_lookup"]
    esc_fallback = bundle["esc_fallback"]
    shifts_with_sdm = bundle["shifts_with_sdm"]
    date_day_map = bundle["date_day_map"]
    ps_dates = bundle["ps_dates"]
    all_unique_dates = bundle["all_unique_dates"]
    day_options = bundle["day_options"]
    mon_ps = bundle["mon_ps"]
    working_count = bundle["working_count"]
    total_ps = bundle["total_ps"]
    wo_count = bundle["wo_count"]
    hol_count = bundle["hol_count"]
    states_count = bundle["states_count"]
    nc_tms = bundle["nc_tms"]
    tm_count = bundle["tm_count"]
    distinct_ps_engineers = bundle["distinct_ps_engineers"]

    # Resolve Scope Lock state from global app state
    active_scope_state = ss.get("_override_canvas_state") or ss.get("global_state_filter")
    if active_scope_state in ["NH", "ND", "AK"]:
        locked_state = f"{active_scope_state} MMIS"
        ss["oncall_state_filter"] = locked_state
        ss["oncall_state_pick"] = locked_state

    today_str = datetime.now().strftime("%Y-%m-%d")
    now_utc = datetime.now(timezone.utc)
    cur_slot = _get_current_active_slot(now_utc)
    use_ist = (ss["oncall_tz"] == "IST")

    # Injected CSS for Zero-Scroll & HTML-inspired compact cards
    st.markdown("""
    <style>
    .block-container {
        padding-top: 0.6rem !important;
        padding-bottom: 0 !important;
        padding-left: 1.25rem !important;
        padding-right: 1.25rem !important;
        max-width: 100% !important;
    }
    .oc-kpi-row {
        display: grid;
        grid-template-columns: repeat(5, 1fr);
        gap: 6px;
        margin-bottom: 6px;
    }
    .oc-stat-card {
        background: #181b1f;
        border: 1px solid #2c3235;
        border-radius: 3px;
        padding: 5px 8px 6px 8px;
        display: flex;
        flex-direction: column;
        justify-content: flex-start;
        min-height: 56px;
        box-sizing: border-box;
    }
    .oc-stat-fill-green {
        background: linear-gradient(180deg, rgba(115,191,105,0.16), rgba(115,191,105,0.02));
        border-top: 2px solid #73bf69;
    }
    .oc-stat-fill-yellow {
        background: linear-gradient(180deg, rgba(255,152,48,0.16), rgba(255,152,48,0.02));
        border-top: 2px solid #ff9830;
    }
    .oc-stat-fill-blue {
        background: linear-gradient(180deg, rgba(87,148,242,0.14), rgba(87,148,242,0.02));
        border-top: 2px solid #5794f2;
    }
    .oc-stat-fill-red {
        background: linear-gradient(180deg, rgba(242,73,92,0.14), rgba(242,73,92,0.02));
        border-top: 2px solid #f2495c;
    }
    .oc-stat-fill-purple {
        background: linear-gradient(180deg, rgba(184,119,217,0.16), rgba(184,119,217,0.02));
        border-top: 2px solid #b877d7;
    }
    .oc-stat-label {
        font-size: 9px;
        color: #9fa7b3;
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
        font-size: 8.5px;
        color: #8b949e;
        margin-top: 2px;
        line-height: 1.2;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
    }
    .shift-badge-m { background: rgba(115,191,105,0.16); color: #73bf69; border: 1px solid rgba(115,191,105,0.3); border-radius: 2px; padding: 1.5px 5px; font-size: 9px; font-weight: 700; white-space: nowrap; }
    .shift-badge-e { background: rgba(87,148,242,0.16); color: #8fb8f8; border: 1px solid rgba(87,148,242,0.3); border-radius: 2px; padding: 1.5px 5px; font-size: 9px; font-weight: 700; white-space: nowrap; }
    .shift-badge-n { background: rgba(184,119,217,0.16); color: #d6a8ef; border: 1px solid rgba(184,119,217,0.3); border-radius: 2px; padding: 1.5px 5px; font-size: 9px; font-weight: 700; white-space: nowrap; }
    .shift-badge-wo { background: rgba(255,255,255,0.06); color: #6e7681; border-radius: 2px; padding: 1.5px 5px; font-size: 9px; font-weight: 600; white-space: nowrap; }
    .shift-badge-fh { background: rgba(255,152,48,0.16); color: #ff9830; border: 1px solid rgba(255,152,48,0.3); border-radius: 2px; padding: 1.5px 5px; font-size: 9px; font-weight: 700; white-space: nowrap; }
    .shift-badge-lv { background: rgba(242,73,92,0.18); color: #ff8fa3; border: 1px solid rgba(242,73,92,0.3); border-radius: 2px; padding: 1.5px 5px; font-size: 9px; font-weight: 700; white-space: nowrap; }
    .today-col-glow { background: rgba(255, 120, 10, 0.09) !important; box-shadow: inset 2px 0 0 #ff780a; }
    .today-col-hdr { color: #ff780a !important; font-weight: 800 !important; }
    .oc-sdm-missing { color: #ff9830; font-style: italic; font-size: 9px; background: rgba(255,152,48,0.08); padding: 1px 5px; border-radius: 2px; border: 1px dashed rgba(255,152,48,0.3); }
    thead th { position: sticky; top: 0; background: #141619; z-index: 5; }
    </style>
    """, unsafe_allow_html=True)


    # --------------------------------------------------------------------------
    # 3. Top Executive Slicer Command Bar (Power BI / Cognos Multi-Dimensional Strip)
    # --------------------------------------------------------------------------
    c_hdr1, c_hdr2, c_hdr3, c_hdr4, c_hdr5 = st.columns([2.5, 1.0, 1.0, 1.3, 1.0])

    with c_hdr1:
        st_val = ss["oncall_state_filter"]
        state_badge = (
            f'<span class="pill" style="color:#38bdf8;background:rgba(56,189,248,0.12);'
            f'font-size:8.5px;font-weight:700;border:1px solid rgba(56,189,248,0.3);margin-left:4px;">'
            f'{escape(st_val)}</span>'
        ) if st_val != "All States" else ""

        hdr_box = (
            '<div style="display:flex;align-items:center;gap:8px;padding:2px 0 3px 0;border-left:3px solid var(--accent);padding-left:10px;margin-left:2px;">'
            '<div>'
            '<div style="font-size:13.5px;font-weight:800;color:var(--ink);letter-spacing:0.02em;text-transform:uppercase;line-height:1.1;display:flex;align-items:center;gap:6px;">'
            '<span>24/7 On-Call Command Hub</span>'
            '<span class="pill" style="color:#10b981;background:rgba(16,185,129,0.12);font-size:8.5px;font-weight:700;border:1px solid rgba(16,185,129,0.3);">ACTIVE ROSTER</span>'
            f'{state_badge}'
            '</div>'
            f'<div style="font-size:9.5px;color:var(--slate);margin-top:1px;">'
            f'Cycle: <b style="color:#f8fafc;font-family:var(--mono);">{escape(valid_from)} &rarr; {escape(valid_to)}</b> &bull; <code style="color:#38bdf8;font-size:9px;">{escape(active_roster.get("source_file", "Excel Roster"))}</code>'
            '</div></div></div>'
        )
        st.markdown(hdr_box, unsafe_allow_html=True)

    with c_hdr2:
        # State Slicer (NH, ND, AK)
        if active_scope_state in ["NH", "ND", "AK"]:
            locked_st = f"{active_scope_state} MMIS"
            st.selectbox(
                "State Slicer",
                [locked_st],
                index=0,
                key="oncall_state_pick",
                disabled=True,
                label_visibility="collapsed",
                help=f"Scope locked to {locked_st} via Global Filter",
            )
            ss["oncall_state_filter"] = locked_st
        else:
            cur_st = ss.get("oncall_state_filter", "All States")
            if cur_st not in _STATE_OPTIONS:
                cur_st = "All States"
                ss["oncall_state_filter"] = "All States"
            st_idx = _STATE_OPTIONS.index(cur_st)
            picked_state = st.selectbox(
                "State Slicer",
                _STATE_OPTIONS,
                index=st_idx,
                key="oncall_state_pick",
                label_visibility="collapsed",
                help="Filter by MMIS State Scope (NH, ND, AK)",
            )
            if picked_state != ss.get("oncall_state_filter"):
                ss["oncall_state_filter"] = picked_state
                ss["oncall_shift_page"] = 0

    with c_hdr3:
        # Location Slicer (Offshore vs Onshore)
        cur_loc = ss.get("oncall_location_filter", "All Locations")
        if cur_loc not in _LOCATION_OPTIONS:
            cur_loc = "All Locations"
            ss["oncall_location_filter"] = "All Locations"
        loc_idx = _LOCATION_OPTIONS.index(cur_loc)
        picked_loc = st.selectbox(
            "Location Slicer",
            _LOCATION_OPTIONS,
            index=loc_idx,
            key="oncall_location_pick",
            label_visibility="collapsed",
            help="Filter by Location: Offshore (IST) vs Onshore (EST)",
        )
        if picked_loc != ss.get("oncall_location_filter"):
            ss["oncall_location_filter"] = picked_loc
            # Auto-align timezone clock
            if "Offshore" in picked_loc:
                ss["oncall_tz"] = "IST"
            elif "Onshore" in picked_loc:
                ss["oncall_tz"] = "EST"
            ss["oncall_shift_page"] = 0

    with c_hdr4:
        # SDM Slicer (Service Delivery Managers)
        cur_sdm = ss.get("oncall_sdm_filter", "All SDMs")
        if cur_sdm not in _SDM_OPTIONS:
            cur_sdm = "All SDMs"
            ss["oncall_sdm_filter"] = "All SDMs"
        sdm_idx = _SDM_OPTIONS.index(cur_sdm)
        picked_sdm = st.selectbox(
            "SDM Slicer",
            _SDM_OPTIONS,
            index=sdm_idx,
            key="oncall_sdm_pick",
            label_visibility="collapsed",
            help="Filter by Service Delivery Manager (SDM Lead)",
        )
        if picked_sdm != ss.get("oncall_sdm_filter"):
            ss["oncall_sdm_filter"] = picked_sdm
            ss["oncall_shift_page"] = 0

    with c_hdr5:
        # Quick Action Buttons
        act1, act2, act3 = st.columns(3)
        with act1:
            if st.button("📤", key="oncall_toggle_upload_btn", type="secondary", use_container_width=True, help="Upload weekly Excel"):
                ss["oncall_show_upload"] = not ss.get("oncall_show_upload", False)
                st.rerun()
        with act2:
            if st.button("🔄", key="oncall_sync_disk_btn", type="secondary", use_container_width=True, help="Sync latest from _Input"):
                input_dir = ROOT / "_Input"
                target_files = sorted(
                    [f for f in input_dir.glob("*.xlsx") if "on call" in f.name.lower() or "roster" in f.name.lower()],
                    key=lambda f: f.stat().st_mtime,
                    reverse=True,
                )
                if target_files:
                    with st.spinner(f"Syncing {target_files[0].name}..."):
                        ingest_roster_file(db_path, target_files[0])
                        _clear_oncall_cache()
                    st.success(f"Synced {target_files[0].name}!")
                    st.rerun()
                else:
                    st.warning("No files found in _Input.")
        with act3:
            if st.button("✕", key="oncall_clear_filters_btn", type="secondary", use_container_width=True, help="Reset all filters"):
                def_st = f"{active_scope_state} MMIS" if active_scope_state in ["NH", "ND", "AK"] else "All States"
                ss["oncall_state_filter"] = def_st
                ss["oncall_state_pick"] = def_st
                ss["oncall_location_filter"] = "All Locations"
                ss["oncall_location_pick"] = "All Locations"
                ss["oncall_sdm_filter"] = "All SDMs"
                ss["oncall_sdm_pick"] = "All SDMs"
                ss["oncall_day_filter"] = "All Days"
                ss["oncall_day_pick"] = "All Days"
                ss["oncall_shift_chip"] = None
                ss["oncall_quick_status"] = "All"
                ss["oncall_search"] = ""
                ss["oncall_dom_filter"] = "All Domains"
                ss["oncall_shift_page"] = 0
                st.rerun()

    # 3b. Weekly Excel Upload Modal / Expander
    if ss.get("oncall_show_upload", False):
        with st.expander("📤 Production Support Lead — Upload New Weekly On-Call Roster (.xlsx)", expanded=True):
            up_col1, up_col2 = st.columns([2.5, 1.5])
            with up_col1:
                new_roster_file = st.file_uploader(
                    "Select Weekly Excel Roster (Must contain: Production support, Infra Team, Core Dev, Non-Core Dev)",
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
                notice_box = (
                    '<div style="background:#141619;border:1px solid #22252b;border-radius:3px;padding:6px 10px;font-size:10px;color:var(--slate);">'
                    '<b style="color:#f8fafc;">Standard Sheet Contract:</b>'
                    '<ul style="margin:4px 0 0 16px;padding:0;">'
                    '<li><code>Production support</code> (31 engineers &times; 7 days)</li>'
                    '<li><code>Infra Team</code> (Cognos, Informatica, UC4, App Server, DB, IAM)</li>'
                    '<li><code>Core Dev</code> (AK DEV, ND DEV, NH DEV)</li>'
                    '<li><code>Non-Core Dev</code> (Letters, Cognos, Informatica, TMSIS, EDMS)</li>'
                    '</ul></div>'
                )
                st.markdown(notice_box, unsafe_allow_html=True)

    # --------------------------------------------------------------------------
    # 4. Temporal Slicers Ribbon (Timezone Clock, Day Slicer & Shift Type Chips)
    # --------------------------------------------------------------------------
    c_slicers_t1, c_slicers_t2, c_slicers_t3 = st.columns([1.1, 1.4, 4.5])

    with c_slicers_t1:
        tz_val = ss["oncall_tz"]
        t1, t2 = st.columns(2)
        with t1:
            if st.button("EST", key="oncall_tz_est_btn", type="primary" if tz_val == "EST" else "secondary", use_container_width=True, help="Eastern Standard Time (UTC-5)"):
                ss["oncall_tz"] = "EST"
        with t2:
            if st.button("IST", key="oncall_tz_ist_btn", type="primary" if tz_val == "IST" else "secondary", use_container_width=True, help="India Standard Time (UTC+5:30)"):
                ss["oncall_tz"] = "IST"

    use_ist = (ss["oncall_tz"] == "IST")

    with c_slicers_t2:
        day_pick = st.selectbox(
            "Filter Day",
            day_options,
            index=day_options.index(ss["oncall_day_filter"]) if ss["oncall_day_filter"] in day_options else 0,
            key="oncall_day_pick",
            label_visibility="collapsed",
            help="Filter roster to a specific day",
        )
        if day_pick != ss.get("oncall_day_filter"):
            ss["oncall_day_filter"] = day_pick
            ss["oncall_shift_page"] = 0

    with c_slicers_t3:
        cur_shift_chip = ss.get("oncall_shift_chip")
        chip_cols = st.columns(len(_SHIFT_CHIPS))
        for idx, (chip_label, chip_val) in enumerate(_SHIFT_CHIPS):
            is_sel = (cur_shift_chip == chip_val)
            with chip_cols[idx]:
                if st.button(
                    chip_label,
                    key=f"oncall_chip_btn_{idx}",
                    type="primary" if is_sel else "secondary",
                    use_container_width=True,
                ):
                    ss["oncall_shift_chip"] = chip_val
                    ss["oncall_shift_page"] = 0

    cur_shift_chip = ss.get("oncall_shift_chip")

    # --------------------------------------------------------------------------
    # 5. Division Navigation Tabs
    # --------------------------------------------------------------------------
    div_names = [
        "Live Ops Radar",
        "Production Support 24x7",
        "Infrastructure Ops",
        "State Core Dev",
        "Non-Core Dev",
    ]
    div_icons = ["⚡", "🏢", "⚙️", "🏛️", "📦"]
    div_cols = st.columns(len(div_names))
    for idx, d_title in enumerate(div_names):
        is_sel = (ss["oncall_div"] == d_title)
        with div_cols[idx]:
            if st.button(
                f"{div_icons[idx]} {d_title}",
                key=f"oncall_div_btn_{idx}",
                type="primary" if is_sel else "secondary",
                use_container_width=True,
            ):
                ss["oncall_div"] = d_title
                ss["oncall_shift_page"] = 0

    cur_div = ss["oncall_div"]

    # --------------------------------------------------------------------------
    # 6. Slicer Value Extraction & Multi-Dimensional Pre-Filtering
    # --------------------------------------------------------------------------
    state_slicer = ss.get("oncall_state_filter", "All States")
    loc_slicer = ss.get("oncall_location_filter", "All Locations")
    sdm_slicer = ss.get("oncall_sdm_filter", "All SDMs")
    clean_sdm_name = sdm_slicer.split("(")[0].strip() if sdm_slicer != "All SDMs" else None

    # --------------------------------------------------------------------------
    # 7. Grafana Stat Ribbon (5 Operational KPI Cards with Dynamic 5th Card)
    # --------------------------------------------------------------------------
    active_now_shifts = [
        s for s in shifts_with_sdm
        if s["shift_slot"] == cur_slot and s.get("primary_on_call")
    ]
    distinct_active_now = list(set(s["primary_on_call"] for s in active_now_shifts))

    # Dynamic 5th KPI Card tailored to current division
    if cur_div == "Non-Core Dev":
        kpi5_label = "Technical Managers"
        kpi5_val = f'{tm_count} <span style="font-size:10px;color:#6e7681;font-weight:400;">Active</span>'
        kpi5_sub = "TM / TL &bull; Non-Core Dev"
        kpi5_cls = "oc-stat-fill-purple"
    elif cur_div == "Production Support 24x7":
        kpi5_label = "Shift Leadership"
        kpi5_val = '2 <span style="font-size:10px;color:#6e7681;font-weight:400;">Leads</span>'
        kpi5_sub = "Abhijit V. &bull; Sreekanth V. (TL)"
        kpi5_cls = "oc-stat-fill-blue"
    elif cur_div == "Infrastructure Ops":
        kpi5_label = "Governing SDM"
        kpi5_val = '<span style="font-size:13.5px;font-weight:800;">Anil Tankala</span>'
        kpi5_sub = "Infra & Ops Lead SDM"
        kpi5_cls = "oc-stat-fill-blue"
    elif cur_div == "State Core Dev":
        kpi5_label = "Governing SDMs"
        kpi5_val = '3 <span style="font-size:10px;color:#6e7681;font-weight:400;">Leads</span>'
        kpi5_sub = "AK &bull; ND &bull; NH State SDMs"
        kpi5_cls = "oc-stat-fill-green"
    else:  # Live Ops Radar
        active_cnt = len(distinct_active_now)
        kpi5_label = "Active On-Duty"
        kpi5_val = f'{active_cnt} <span style="font-size:10px;color:#6e7681;font-weight:400;">Shifts</span>'
        kpi5_sub = f"Slot {cur_slot} &bull; Live Operations"
        kpi5_cls = "oc-stat-fill-yellow"

    kpi_ribbon_html = (
        '<div class="oc-kpi-row">'
        '<div class="oc-stat-card oc-stat-fill-green">'
        '<span class="oc-stat-label">Working Today</span>'
        f'<div class="oc-stat-val">{working_count} <span style="font-size:10px;color:#6e7681;font-weight:400;">/ {total_ps}</span></div>'
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
        f'<div class="oc-stat-card {kpi5_cls}">'
        f'<span class="oc-stat-label">{kpi5_label}</span>'
        f'<div class="oc-stat-val">{kpi5_val}</div>'
        f'<span class="oc-stat-sub">{kpi5_sub}</span>'
        '</div>'
        '</div>'
    )
    st.markdown(kpi_ribbon_html, unsafe_allow_html=True)

    # --------------------------------------------------------------------------
    # 8. Master-Detail Command Workspace (58% Master / 42% Detail Inspector)
    # --------------------------------------------------------------------------
    master_col, detail_col = st.columns([3.5, 2.5])

    # Pre-select first engineer if none selected
    if not ss["oncall_selected_eng"]:
        if distinct_active_now:
            ss["oncall_selected_eng"] = distinct_active_now[0]
        elif all_ps:
            ss["oncall_selected_eng"] = all_ps[0]["resource_name"]

    target_day = None
    if ss["oncall_day_filter"] != "All Days":
        target_day = ss["oncall_day_filter"].split("(")[-1].replace(")", "").strip()

    quick_status = ss.get("oncall_quick_status", "All")
    cur_selected = ss.get("oncall_selected_eng")

    # ==========================================================================
    # MASTER PANE (Left Column)
    # ==========================================================================
    with master_col:
        # A. MODE 1: PRODUCTION SUPPORT 24x7 (Hierarchical Cross-Tab Matrix)
        if cur_div == "Production Support 24x7":
            # Quick status toggle, Search & Inspector Toolbar (Unified Single Row)
            all_ps_names = sorted(list(set(p["resource_name"] for p in all_ps)))
            safe_idx = all_ps_names.index(cur_selected) if cur_selected in all_ps_names else 0

            tb1, tb2, tb3, tb4, tb5 = st.columns([1.0, 1.1, 1.1, 1.7, 1.8])
            with tb1:
                if st.button("All Resources", key="oncall_qs_all", type="primary" if quick_status == "All" else "secondary", use_container_width=True):
                    ss["oncall_quick_status"] = "All"
            with tb2:
                if st.button("Working Today", key="oncall_qs_working", type="primary" if quick_status == "Working Today" else "secondary", use_container_width=True):
                    ss["oncall_quick_status"] = "Working Today"
            with tb3:
                if st.button("Off / Holiday", key="oncall_qs_off", type="primary" if quick_status == "Off / Holiday" else "secondary", use_container_width=True):
                    ss["oncall_quick_status"] = "Off / Holiday"
            with tb4:
                search_val = st.text_input(
                    "Search Resource",
                    value=ss["oncall_search"],
                    placeholder="Search engineer name...",
                    key="oncall_ps_search_box",
                    label_visibility="collapsed",
                )
                if search_val != ss["oncall_search"]:
                    ss["oncall_search"] = search_val
                    st.rerun()
            with tb5:
                picked_eng = st.selectbox(
                    "Inspect Engineer",
                    all_ps_names,
                    index=safe_idx,
                    key="oncall_ps_eng_picker",
                    label_visibility="collapsed",
                    help="Inspect Engineer contacts & escalation hierarchy",
                )
                if picked_eng != cur_selected:
                    ss["oncall_selected_eng"] = picked_eng
                    cur_selected = picked_eng

            ps_df = pd.DataFrame(all_ps)

            # 1. Text Search Filter
            if ss["oncall_search"].strip():
                q_term = ss["oncall_search"].strip().lower()
                ps_df = ps_df[ps_df["resource_name"].str.lower().str.contains(q_term, na=False)]

            # 2. Shift-Type Filter (applied to PS shift_window column)
            if cur_shift_chip:
                if target_day:
                    matching_names = [
                        r["resource_name"] for _, r in ps_df[ps_df["shift_date"] == target_day].iterrows()
                        if _matches_ps_shift(cur_shift_chip, r.get("shift_window", ""))
                    ]
                else:
                    matching_names = [
                        r["resource_name"] for _, r in ps_df.iterrows()
                        if _matches_ps_shift(cur_shift_chip, r.get("shift_window", ""))
                    ]
                ps_df = ps_df[ps_df["resource_name"].isin(set(matching_names))]

            # 3. Quick Status Filter (Working Today vs Off)
            if quick_status == "Working Today":
                ref_day = target_day if target_day else today_str
                working_names = ps_df[(ps_df["shift_date"] == ref_day) & (ps_df["is_working"] == 1)]["resource_name"].unique()
                ps_df = ps_df[ps_df["resource_name"].isin(working_names)]
            elif quick_status == "Off / Holiday":
                ref_day = target_day if target_day else today_str
                off_names = ps_df[(ps_df["shift_date"] == ref_day) & (ps_df["is_working"] == 0)]["resource_name"].unique()
                ps_df = ps_df[ps_df["resource_name"].isin(off_names)]

            # 4. State Slicer on Production Support
            if state_slicer != "All States":
                st_code = state_slicer.split()[0]
                state_matches = ps_df[ps_df["resource_name"].str.upper().str.contains(st_code, na=False)]
                if not state_matches.empty:
                    ps_df = state_matches

            # 5. SDM Slicer on Production Support
            # Anil Tankala & Kishore Nagarajan govern Production Support
            if clean_sdm_name and clean_sdm_name not in ["Anil Tankala", "Kishore Nagarajan"]:
                # When state-specific SDMs are selected, display state-aligned resources
                if clean_sdm_name in ["Ravi M Shankar", "Kishore Nagarajan"]:
                    st_m = ps_df[ps_df["resource_name"].str.upper().str.contains("AK", na=False)]
                    if not st_m.empty: ps_df = st_m
                elif clean_sdm_name in ["Thirupathi Katakam", "Anil Kumar Khamari"]:
                    st_m = ps_df[ps_df["resource_name"].str.upper().str.contains("ND", na=False)]
                    if not st_m.empty: ps_df = st_m
                elif clean_sdm_name in ["Madhav", "Dipak/Rama"]:
                    st_m = ps_df[ps_df["resource_name"].str.upper().str.contains("NH", na=False)]
                    if not st_m.empty: ps_df = st_m

            # Pivot cross-tab across all 7 days (SAT to FRI guaranteed)
            pivot_dates = ps_dates if ps_dates else all_unique_dates
            if not ps_df.empty:
                pivoted = ps_df.pivot(index="resource_name", columns="shift_date", values="shift_window").reindex(columns=pivot_dates).reset_index()
            else:
                pivoted = pd.DataFrame(columns=["resource_name"] + pivot_dates)

            filtered_eng_count = len(pivoted)
            st.markdown(ui.panel_header(
                "Production Support 24x7 Resource Matrix (7-Day Cross-Tab)",
                color="#f59e0b",
                count=f"{filtered_eng_count} of {distinct_ps_engineers} Engineers",
                info="Live 24x7 rotation across 31 engineers. Use toolbar to filter by shift or inspect an engineer.",
            ), unsafe_allow_html=True)

            # Build Header with TODAY indicator
            today_badge_html = "<br/><span style='font-size:7.5px;color:#ff780a;font-weight:800;'>TODAY</span>"
            th_days = "".join(
                f"<th style='padding:4px 6px;text-align:center;min-width:82px;"
                f"{'background:rgba(245,158,11,0.12);border-top:2px solid #f59e0b;' if d == today_str else ''}'>"
                f"{date_day_map.get(d, d)[:3]}<br/>"
                f"<span style='font-size:8px;font-weight:400;color:var(--slate);'>{d[5:]}</span>"
                f"{today_badge_html if d == today_str else ''}"
                f"</th>"
                for d in pivot_dates
            )
            head_html = (
                "<thead>"
                "<tr style='background:#141619;border-bottom:1px solid #2c3235;font-size:9.5px;text-transform:uppercase;color:var(--slate);'>"
                "<th style='padding:4px 8px;text-align:left;min-width:140px;'>Engineer Name</th>"
                f"{th_days}"
                "<th style='padding:4px 6px;text-align:center;min-width:55px;'>Days On</th>"
                "</tr>"
                "</thead>"
            )

            body_rows = []
            for _, row_data in pivoted.iterrows():
                r_name = row_data["resource_name"]
                is_active_eng = (cur_selected == r_name)

                td_cells = []
                w_count = 0
                for d in pivot_dates:
                    win_val = row_data.get(d)
                    if pd.isna(win_val) or not win_val:
                        win_val = "Week Off"
                    chip = _compact_shift_chip(str(win_val))
                    cell_cls = ' class="today-col-glow"' if (d == today_str) else ''
                    td_cells.append(f"<td style='padding:3px 4px;text-align:center;'{cell_cls}>{chip}</td>")
                    if win_val and not any(k in str(win_val).lower() for k in ["wo", "off", "holiday", "leave"]):
                        w_count += 1

                row_bg = (
                    "rgba(56, 189, 248, 0.09)" if is_active_eng
                    else ("#181b1f" if len(body_rows) % 2 == 0 else "#141619")
                )
                border_style = (
                    "border-left:3px solid var(--accent);" if is_active_eng
                    else "border-left:3px solid transparent;"
                )
                avatar = ui.on_call_avatar(r_name)

                body_rows.append(
                    f"<tr style='background:{row_bg};border-bottom:1px solid #22252b;{border_style}'>"
                    f"<td style='padding:4px 8px;color:var(--ink);font-weight:700;font-size:10.5px;white-space:nowrap;display:flex;align-items:center;gap:6px;'>"
                    f"{avatar}<span>{escape(r_name)}</span></td>"
                    f"{''.join(td_cells)}"
                    f"<td style='padding:4px 6px;text-align:center;font-family:var(--mono);font-weight:700;color:#10b981;'>{w_count}d</td>"
                    f"</tr>"
                )

            # Daily Headcount Summary Footer
            foot_cells = []
            for d in pivot_dates:
                d_rows = [r.get(d) for _, r in pivoted.iterrows()]
                m_c = sum(1 for v in d_rows if v and ("6:30" in str(v) or "morning" in str(v).lower()))
                e_c = sum(1 for v in d_rows if v and ("14:30" in str(v) or "evening" in str(v).lower()))
                n_c = sum(1 for v in d_rows if v and ("22:30" in str(v) or "night" in str(v).lower()))
                h_c = sum(1 for v in d_rows if v and any(k in str(v).lower() for k in ["holiday", "float"]))
                cell_cls = ' class="today-col-glow"' if (d == today_str) else ''
                foot_cells.append(
                    f"<td style='padding:3px 2px;text-align:center;font-size:8px;line-height:1.2;'{cell_cls}>"
                    f"<b style='color:#73bf69;'>{m_c}M</b> <b style='color:#8fb8f8;'>{e_c}E</b><br/>"
                    f"<b style='color:#d6a8ef;'>{n_c}N</b> <span style='color:#ff9830;'>{h_c}H</span>"
                    f"</td>"
                )
            foot_html = (
                "<tfoot>"
                "<tr style='background:#141619;border-top:2px solid #2c3235;font-size:8.5px;color:var(--slate);'>"
                "<td style='padding:4px 8px;font-weight:800;color:var(--ink);text-transform:uppercase;'>Daily Coverage</td>"
                f"{''.join(foot_cells)}"
                f"<td style='padding:4px 6px;text-align:center;font-family:var(--mono);font-weight:800;color:#10b981;'>{filtered_eng_count}</td>"
                "</tr>"
                "</tfoot>"
            )

            empty_notice = (
                "<tr><td colspan='100' style='padding:20px;text-align:center;color:var(--mute);font-size:11px;'>"
                "No engineers match the selected filters. Click ✕ in the top right to reset filters.</td></tr>"
            )
            table_crosstab_html = (
                "<div style='border:1px solid #2c3235;border-radius:2px;background:#181b1f;max-height:calc(100vh - 325px);overflow-y:auto;overflow-x:hidden;'>"
                "<table style='width:100%;border-collapse:collapse;font-size:10px;min-width:780px;'>"
                f"{head_html}"
                f"<tbody>{''.join(body_rows) if body_rows else empty_notice}</tbody>"
                f"{foot_html if body_rows else ''}"
                "</table>"
                "</div>"
            )
            st.markdown(table_crosstab_html, unsafe_allow_html=True)

        # B. MODE 2: LIVE OPS RADAR & DOMAIN SHIFTS (Infra / Core Dev / Non-Core Dev)
        else:
            filtered_shifts = shifts_with_sdm.copy()

            # 1. Division filter
            if cur_div == "Live Ops Radar":
                filtered_shifts = [s for s in filtered_shifts if s["shift_slot"] == cur_slot]
            elif cur_div == "Infrastructure Ops":
                filtered_shifts = [s for s in filtered_shifts if s["division"] == "Infra Team"]
            elif cur_div == "State Core Dev":
                filtered_shifts = [s for s in filtered_shifts if s["division"] == "Core Dev"]
            elif cur_div == "Non-Core Dev":
                filtered_shifts = [s for s in filtered_shifts if s["division"] == "Non-Core Dev"]

            # 2. State Slicer
            if state_slicer != "All States":
                st_code = state_slicer.split()[0].upper()
                filtered_shifts = [
                    s for s in filtered_shifts
                    if st_code in s["domain_state"].upper() or s["division"] == "Infra Team"
                ]

            # 3. Location Slicer (Offshore = Slots 1 & 2 / Onshore = Slots 3 & 4)
            if loc_slicer == "🌏 Offshore (IST)":
                filtered_shifts = [s for s in filtered_shifts if s["shift_slot"] in [1, 2]]
            elif loc_slicer == "🏛️ Onshore (EST)":
                filtered_shifts = [s for s in filtered_shifts if s["shift_slot"] in [3, 4]]

            # 4. SDM Slicer
            if clean_sdm_name:
                filtered_shifts = [
                    s for s in filtered_shifts
                    if clean_sdm_name.lower() in s.get("governed_sdm", "").lower()
                ]

            # 5. Day filter
            if target_day:
                filtered_shifts = [s for s in filtered_shifts if s["shift_date"] == target_day]

            # 6. Shift-Type Filter (applied to shift slot)
            if cur_shift_chip:
                filtered_shifts = [
                    s for s in filtered_shifts
                    if _matches_domain_slot(cur_shift_chip, s.get("shift_slot", 0))
                ]

            # Multi-control horizontal strip: Domain Filter + Search box + Inspect Engineer + Clock in the SAME line
            all_shift_primaries = sorted(list(set(s["primary_on_call"] for s in filtered_shifts if s.get("primary_on_call"))))
            safe_shift_idx = all_shift_primaries.index(cur_selected) if cur_selected in all_shift_primaries else 0

            if cur_div == "Non-Core Dev":
                nc_domains = ["All Domains"] + sorted(list(set(s["domain_state"] for s in shifts_with_sdm if s["division"] == "Non-Core Dev")))
                fc1, fc2, fc3, fc4 = st.columns([1.3, 1.8, 1.8, 0.9])
                with fc1:
                    picked_nc_dom = st.selectbox(
                        "Domain Slicer",
                        nc_domains,
                        index=nc_domains.index(ss.get("oncall_dom_filter", "All Domains")) if ss.get("oncall_dom_filter") in nc_domains else 0,
                        key="oncall_nc_dom_filter",
                        label_visibility="collapsed",
                        help="Filter by Non-Core Domain (Cognos, Letters, Informatica, EDMS, TMSIS)",
                    )
                    if picked_nc_dom != ss.get("oncall_dom_filter", "All Domains"):
                        ss["oncall_dom_filter"] = picked_nc_dom
                        ss["oncall_shift_page"] = 0
                        st.rerun()
                with fc2:
                    search_shift = st.text_input(
                        "Search Shifts",
                        value=ss["oncall_search"],
                        placeholder="Search engineer, module, TM...",
                        key="oncall_shift_search_box",
                        label_visibility="collapsed",
                    )
                    if search_shift != ss["oncall_search"]:
                        ss["oncall_search"] = search_shift
                        ss["oncall_shift_page"] = 0
                        st.rerun()
                with fc3:
                    p_eng = st.selectbox(
                        "Inspect Shift Engineer",
                        all_shift_primaries if all_shift_primaries else [cur_selected or "No Engineers"],
                        index=safe_shift_idx if all_shift_primaries else 0,
                        key="oncall_mode2_eng_picker",
                        label_visibility="collapsed",
                        help="Inspect engineer escalation path",
                    )
                    if p_eng != cur_selected and all_shift_primaries:
                        ss["oncall_selected_eng"] = p_eng
                        cur_selected = p_eng
                with fc4:
                    tz_label = "IST (UTC+5:30)" if use_ist else "EST (UTC-5)"
                    st.markdown(
                        f'<div style="font-size:10px;font-weight:600;color:var(--slate);line-height:28px;text-align:right;">'
                        f'Clock: <b style="color:#38bdf8;">{tz_label}</b></div>',
                        unsafe_allow_html=True,
                    )
                if ss.get("oncall_dom_filter", "All Domains") != "All Domains":
                    filtered_shifts = [s for s in filtered_shifts if s["domain_state"] == ss["oncall_dom_filter"]]

            elif cur_div == "Infrastructure Ops":
                infra_domains = ["All Domains"] + sorted(list(set(s["domain_state"] for s in shifts_with_sdm if s["division"] == "Infra Team")))
                fc1, fc2, fc3, fc4 = st.columns([1.3, 1.8, 1.8, 0.9])
                with fc1:
                    picked_infra_dom = st.selectbox(
                        "Infra Domain",
                        infra_domains,
                        index=infra_domains.index(ss.get("oncall_dom_filter", "All Domains")) if ss.get("oncall_dom_filter") in infra_domains else 0,
                        key="oncall_infra_dom_filter",
                        label_visibility="collapsed",
                        help="Filter by Infrastructure Domain",
                    )
                    if picked_infra_dom != ss.get("oncall_dom_filter", "All Domains"):
                        ss["oncall_dom_filter"] = picked_infra_dom
                        ss["oncall_shift_page"] = 0
                        st.rerun()
                with fc2:
                    search_shift = st.text_input(
                        "Search Shifts",
                        value=ss["oncall_search"],
                        placeholder="Search engineer, domain, lead...",
                        key="oncall_shift_search_box",
                        label_visibility="collapsed",
                    )
                    if search_shift != ss["oncall_search"]:
                        ss["oncall_search"] = search_shift
                        ss["oncall_shift_page"] = 0
                        st.rerun()
                with fc3:
                    p_eng = st.selectbox(
                        "Inspect Shift Engineer",
                        all_shift_primaries if all_shift_primaries else [cur_selected or "No Engineers"],
                        index=safe_shift_idx if all_shift_primaries else 0,
                        key="oncall_infra_eng_picker",
                        label_visibility="collapsed",
                        help="Inspect engineer escalation path",
                    )
                    if p_eng != cur_selected and all_shift_primaries:
                        ss["oncall_selected_eng"] = p_eng
                        cur_selected = p_eng
                with fc4:
                    tz_label = "IST (UTC+5:30)" if use_ist else "EST (UTC-5)"
                    st.markdown(
                        f'<div style="font-size:10px;font-weight:600;color:var(--slate);line-height:28px;text-align:right;">'
                        f'Clock: <b style="color:#38bdf8;">{tz_label}</b></div>',
                        unsafe_allow_html=True,
                    )
                if ss.get("oncall_dom_filter", "All Domains") != "All Domains":
                    filtered_shifts = [s for s in filtered_shifts if s["domain_state"] == ss["oncall_dom_filter"]]

            else:
                srch1, srch2, srch3 = st.columns([2.5, 2.0, 0.9])
                with srch1:
                    search_shift = st.text_input(
                        "Search Shifts",
                        value=ss["oncall_search"],
                        placeholder="Search domain, lead, or on-call engineer...",
                        key="oncall_shift_search_box",
                        label_visibility="collapsed",
                    )
                    if search_shift != ss["oncall_search"]:
                        ss["oncall_search"] = search_shift
                        ss["oncall_shift_page"] = 0
                        st.rerun()
                with srch2:
                    p_eng = st.selectbox(
                        "Inspect Shift Engineer",
                        all_shift_primaries if all_shift_primaries else [cur_selected or "No Engineers"],
                        index=safe_shift_idx if all_shift_primaries else 0,
                        key="oncall_core_eng_picker",
                        label_visibility="collapsed",
                        help="Inspect engineer escalation path",
                    )
                    if p_eng != cur_selected and all_shift_primaries:
                        ss["oncall_selected_eng"] = p_eng
                        cur_selected = p_eng
                with srch3:
                    tz_label = "IST (UTC+5:30)" if use_ist else "EST (UTC-5)"
                    st.markdown(
                        f'<div style="font-size:10px;font-weight:600;color:var(--slate);line-height:28px;text-align:right;">'
                        f'Clock: <b style="color:#38bdf8;">{tz_label}</b></div>',
                        unsafe_allow_html=True,
                    )

            if ss["oncall_search"].strip():
                q_term = ss["oncall_search"].strip().lower()
                filtered_shifts = [
                    s for s in filtered_shifts
                    if q_term in s["domain_state"].lower()
                    or q_term in (s.get("primary_on_call") or "").lower()
                    or q_term in (s.get("secondary_on_call") or "").lower()
                    or q_term in s.get("governed_sdm", "").lower()
                ]

            # Consolidate duplicate contiguous slots (Slots 1 & 2 for same engineer, Slots 3 & 4 for same engineer)
            if cur_div in ["Non-Core Dev", "Infrastructure Ops"]:
                filtered_shifts = _consolidate_contiguous_shifts(filtered_shifts)

            total_shift_rows = len(filtered_shifts)
            page = ss.get("oncall_shift_page", 0)
            max_page = max(0, (total_shift_rows - 1) // _PAGE_SIZE)
            page = min(page, max_page)
            ss["oncall_shift_page"] = page
            page_slice = filtered_shifts[page * _PAGE_SIZE: (page + 1) * _PAGE_SIZE]

            div_title = "⚡ Live Ops Radar (All Active Shifts)" if cur_div == "Live Ops Radar" else f"{cur_div} On-Call Shifts"
            st.markdown(ui.panel_header(
                div_title,
                color="#38bdf8",
                count=f"Showing {len(page_slice)} of {total_shift_rows} Slots",
                info="Live schedule across functional domains. Click an engineer to inspect contacts & escalation paths.",
            ), unsafe_allow_html=True)

            tz_hdr = "Time in IST" if use_ist else "Time in EST"
            governing_col_hdr = "Technical Manager (TM)" if cur_div == "Non-Core Dev" else "Governing SDM"
            shift_rows_html = []

            for idx, s in enumerate(page_slice):
                p_name = s.get("primary_on_call") or "Unassigned"
                s_name = s.get("secondary_on_call") or "—"
                sdm_lead = s.get("governed_sdm") or ("Technical Manager" if cur_div == "Non-Core Dev" else "Lead SDM")
                time_disp = s["time_ist"] if use_ist else s["time_est"]
                is_active_now = (cur_slot in s.get("original_slots", [s.get("shift_slot")]) and s.get("shift_date") == today_str)

                is_sel = (cur_selected == p_name)
                scope_hl = (state_slicer != "All States") and (state_slicer.split()[0].upper() in s["domain_state"].upper())

                row_bg = (
                    "rgba(56, 189, 248, 0.09)" if is_sel
                    else ("rgba(245, 158, 11, 0.05)" if scope_hl
                          else ("#181b1f" if idx % 2 == 0 else "#141619"))
                )
                border_style = (
                    "border-left:3px solid #38bdf8;" if is_active_now
                    else ("border-left:3px solid var(--accent);" if is_sel
                          else ("border-left:3px solid #f59e0b;" if scope_hl
                                else "border-left:3px solid transparent;"))
                )

                p_avatar = ui.on_call_avatar(p_name)
                slot_tag = s.get("slot_label") or f'Slot {s["shift_slot"]}'
                active_badge = (
                    '<span style="color:#10b981;font-weight:700;font-size:8.5px;background:rgba(16,185,129,0.15);'
                    'padding:1px 4px;border-radius:2px;border:1px solid rgba(16,185,129,0.3);">'
                    '<span class="pulse-dot"></span>ACTIVE NOW</span>'
                    if is_active_now
                    else f'<span style="color:var(--slate);font-size:8.5px;font-family:var(--mono);">{escape(slot_tag)}</span>'
                )

                shift_rows_html.append(
                    f"<tr style='background:{row_bg};border-bottom:1px solid #22252b;{border_style}'>"
                    f"<td style='padding:5px 8px;font-family:var(--mono);font-weight:700;color:#f8fafc;'><span class='st-tag'>{escape(s['domain_state'])}</span></td>"
                    f"<td style='padding:5px 8px;color:var(--slate);font-size:10px;'>{escape(s['day_name'][:3])} <span style='color:var(--mute);'>{escape(s['shift_date'][5:])}</span></td>"
                    f"<td style='padding:5px 8px;font-family:var(--mono);font-size:9.5px;color:var(--ink);'>{escape(time_disp)}</td>"
                    f"<td style='padding:5px 8px;font-weight:700;color:var(--ink);display:flex;align-items:center;gap:6px;'>{p_avatar}<span>{escape(p_name)}</span></td>"
                    f"<td style='padding:5px 8px;color:var(--slate);font-size:10px;'><b style='color:#f59e0b;'>{escape(sdm_lead)}</b></td>"
                    f"<td style='padding:5px 8px;'>{active_badge}</td>"
                    f"</tr>"
                )

            empty_shifts_notice = (
                "<tr><td colspan='6' style='padding:20px;text-align:center;color:var(--mute);font-size:11px;'>"
                "No shift assignments match the selected filters. Click ✕ to reset filters.</td></tr>"
            )
            table_shifts_html = (
                "<div style='border:1px solid #2c3235;border-radius:2px;overflow:hidden;background:#181b1f;max-height:calc(100vh - 350px);overflow-y:auto;'>"
                "<table style='width:100%;border-collapse:collapse;font-size:10.5px;'>"
                "<thead>"
                "<tr style='background:#141619;border-bottom:1px solid #2c3235;font-size:9.5px;text-transform:uppercase;color:var(--slate);'>"
                "<th style='padding:5px 8px;text-align:left;'>Domain / Scope</th>"
                "<th style='padding:5px 8px;text-align:left;'>Day</th>"
                f"<th style='padding:5px 8px;text-align:left;'>{tz_hdr}</th>"
                "<th style='padding:5px 8px;text-align:left;'>Primary On-Call</th>"
                f"<th style='padding:5px 8px;text-align:left;'>{governing_col_hdr}</th>"
                "<th style='padding:5px 8px;text-align:left;'>Status</th>"
                "</tr>"
                "</thead>"
                f"<tbody>{''.join(shift_rows_html) if shift_rows_html else empty_shifts_notice}</tbody>"
                "</table>"
                "</div>"
            )
            st.markdown(table_shifts_html, unsafe_allow_html=True)

            # Pagination Controls
            if total_shift_rows > _PAGE_SIZE:
                p_c1, p_c2, p_c3 = st.columns([1, 2.5, 1])
                with p_c1:
                    if page > 0 and st.button("◀ Previous", key="oncall_prev_page", use_container_width=True):
                        ss["oncall_shift_page"] = page - 1
                        st.rerun()
                with p_c2:
                    st.markdown(
                        f'<div style="text-align:center;font-size:10px;color:var(--slate);padding-top:6px;">'
                        f'Page {page + 1} of {max_page + 1} &bull; {total_shift_rows} Total Shift Slots'
                        f'</div>',
                        unsafe_allow_html=True,
                    )
                with p_c3:
                    if page < max_page and st.button("Next ▶", key="oncall_next_page", use_container_width=True):
                        ss["oncall_shift_page"] = page + 1
                        st.rerun()


    # ==========================================================================
    # DETAIL INSPECTOR PANE (Right Column)
    # ==========================================================================
    with detail_col:
        sel_name = ss.get("oncall_selected_eng") or (distinct_active_now[0] if distinct_active_now else "Operations Lead")

        matching_shifts = [s for s in shifts_with_sdm if s.get("primary_on_call") == sel_name or s.get("secondary_on_call") == sel_name]
        matching_ps = [p for p in all_ps if p.get("resource_name") == sel_name]

        eng_div = matching_shifts[0]["division"] if matching_shifts else ("Production Support" if matching_ps else "Enterprise Operations")
        eng_domain = matching_shifts[0]["domain_state"] if matching_shifts else "Tier-1 Production Support"

        matching_esc = None
        if matching_shifts:
            ref_s = matching_shifts[0]
            k_exact = (ref_s["division"], ref_s["domain_state"], ref_s["shift_slot"], ref_s["shift_date"])
            matching_esc = esc_lookup.get(k_exact) or esc_fallback.get((ref_s["division"], ref_s["domain_state"]))

        if not matching_esc:
            matching_esc = {
                "tier1_name": "Abhijit Vajja / Sreekanth Veluguleti",
                "tier1_title": "Offshore Team Lead (TL/TM)",
                "tier2_name": "Anil Tankala / Kishore Nagarajan",
                "tier2_title": "Service Delivery Manager (SDM)",
                "tier3_name": "Nagarajan Kochunni / Radhakanta Samantara",
                "tier3_title": "Project Director (PD)",
            }

        st.markdown('<div style="max-height:calc(100vh - 340px);overflow-y:auto;padding-right:4px;">', unsafe_allow_html=True)
        st.markdown(ui.panel_header(
            "On-Call Personnel & Escalation Inspector",
            color="#38bdf8",
            info="Contextual identity, active shift windows, and 3-tier escalation authority for on-call personnel.",
        ), unsafe_allow_html=True)

        # 1. Profile Header Card & Contextual Duty Badge
        avatar_lg = ui.on_call_avatar(sel_name).replace("width:20px;height:20px;font-size:8.5px;", "width:36px;height:36px;font-size:13px;")
        today_match_ps = [p for p in matching_ps if p.get("shift_date") == today_str]
        win_today = today_match_ps[0].get("shift_window", "") if today_match_ps else ""
        win_low = win_today.lower()

        if "holiday" in win_low or "float" in win_low:
            duty_badge = '<span class="pill" style="color:#ff9830;background:rgba(255,152,48,0.16);font-size:8.5px;font-weight:700;border:1px solid rgba(255,152,48,0.4);">🎉 FLOATING HOLIDAY</span>'
        elif "wo" in win_low or "week off" in win_low or "comp" in win_low:
            duty_badge = '<span class="pill" style="color:#94a3b8;background:rgba(148,163,184,0.12);font-size:8.5px;font-weight:700;border:1px solid rgba(148,163,184,0.3);">SCHEDULED WEEK OFF</span>'
        elif "leave" in win_low:
            duty_badge = '<span class="pill" style="color:#ef4444;background:rgba(239,68,68,0.16);font-size:8.5px;font-weight:700;border:1px solid rgba(239,68,68,0.4);">ON APPROVED LEAVE</span>'
        elif any(k in win_low for k in ["6:30", "14:30", "22:30", "morning", "evening", "night"]):
            duty_badge = '<span class="pill" style="color:#10b981;background:rgba(16,185,129,0.16);font-size:8.5px;font-weight:700;border:1px solid rgba(16,185,129,0.4);"><span class="pulse-dot"></span>ON DUTY TODAY</span>'
        elif matching_shifts:
            is_active_now = any(s.get("shift_date") == today_str and cur_slot in s.get("original_slots", [s.get("shift_slot")]) for s in matching_shifts)
            duty_badge = '<span class="pill" style="color:#10b981;background:rgba(16,185,129,0.16);font-size:8.5px;font-weight:700;border:1px solid rgba(16,185,129,0.4);"><span class="pulse-dot"></span>ON DUTY TODAY</span>' if is_active_now else '<span class="pill" style="color:#64748b;background:rgba(100,116,139,0.16);font-size:8.5px;font-weight:700;border:1px solid rgba(100,116,139,0.3);">STANDBY / ROTATION</span>'
        else:
            duty_badge = '<span class="pill" style="color:#64748b;background:rgba(100,116,139,0.16);font-size:8.5px;font-weight:700;border:1px solid rgba(100,116,139,0.3);">OFF DUTY TODAY</span>'

        card_profile = (
            '<div style="background:#141619;border:1px solid #2c3235;border-left:3px solid var(--accent);border-radius:3px;padding:8px 12px;margin-bottom:8px;">'
            '<div style="display:flex;align-items:center;justify-content:space-between;">'
            '<div style="display:flex;align-items:center;gap:10px;">'
            f'{avatar_lg}'
            '<div>'
            f'<div style="font-size:13px;font-weight:800;color:var(--ink);letter-spacing:0.02em;">{escape(sel_name)}</div>'
            f'<div style="font-size:10px;color:var(--slate);">{escape(eng_div)} &bull; <b style="color:#38bdf8;">{escape(eng_domain)}</b></div>'
            '</div></div>'
            f'{duty_badge}'
            '</div></div>'
        )
        st.markdown(card_profile, unsafe_allow_html=True)

        # 2. Timing and Live Status Card (Supports both Domain shifts & Production Support)
        t_est = "See Master Roster"
        t_ist = "Rotation Shift Hours"
        timing_title = "Scheduled Shift Hours"

        if matching_shifts:
            ref_shift = matching_shifts[0]
            t_est = ref_shift.get("time_est", "See Roster")
            t_ist = ref_shift.get("time_ist", "See Roster")
            timing_title = f"Scheduled Shift Hours ({ref_shift.get('day_name', 'Day')} {ref_shift.get('shift_date', '')[5:]})"
        elif matching_ps:
            ref_ps = today_match_ps[0] if today_match_ps else matching_ps[0]
            win = ref_ps.get("shift_window", "")
            win_l = win.lower()
            timing_title = f"Scheduled Shift Hours ({ref_ps.get('day_name', 'Today')} {ref_ps.get('shift_date', '')[5:]})"
            if "6:30" in win_l or "morning" in win_l:
                t_ist = "06:30 AM to 03:30 PM (Morning)"
                t_est = "09:00 PM to 06:00 AM (Prev Night)"
            elif "14:30" in win_l or "evening" in win_l:
                t_ist = "02:30 PM to 11:30 PM (Evening)"
                t_est = "05:00 AM to 02:00 PM (Morning/Day)"
            elif "22:30" in win_l or "night" in win_l:
                t_ist = "10:30 PM to 07:30 AM (Night)"
                t_est = "01:00 PM to 10:00 PM (Afternoon/Eve)"
            elif "holiday" in win_l or "float" in win_l:
                t_ist = "Floating Holiday (On-Call Standby)"
                t_est = "Fleet Standby / Non-Working"
            elif "leave" in win_l:
                t_ist = "Approved Leave (Backfilled)"
                t_est = "Off Fleet Roster"
            else:
                t_ist = "Scheduled Week Off (Rest Day)"
                t_est = "Off Duty / Standby"

        card_timing = (
            '<div style="background:#181b1f;border:1px solid #22252b;border-radius:3px;padding:8px 10px;margin-bottom:8px;">'
            f'<div style="font-size:9.5px;font-weight:700;text-transform:uppercase;color:var(--slate);letter-spacing:0.04em;margin-bottom:6px;">{timing_title}</div>'
            '<div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;">'
            '<div style="background:#141619;border:1px solid #22252b;border-radius:2px;padding:4px 8px;">'
            '<div style="font-size:8.5px;color:var(--mute);font-weight:600;">TIME IN EST (UTC-5)</div>'
            f'<div style="font-size:10.5px;font-family:var(--mono);font-weight:700;color:var(--ink);margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{escape(t_est)}</div>'
            '</div>'
            '<div style="background:#141619;border:1px solid #22252b;border-radius:2px;padding:4px 8px;">'
            '<div style="font-size:8.5px;color:var(--mute);font-weight:600;">TIME IN IST (UTC+5:30)</div>'
            f'<div style="font-size:10.5px;font-family:var(--mono);font-weight:700;color:#38bdf8;margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{escape(t_ist)}</div>'
            '</div></div></div>'
        )
        st.markdown(card_timing, unsafe_allow_html=True)

        # 3. 3-Tier Escalation Hierarchy Lineage
        t1_name = matching_esc.get("tier1_name") or "Abhijit Vajja / Sreekanth Veluguleti"
        t1_title = matching_esc.get("tier1_title") or "Offshore Team Lead (TL/TM)"
        t2_name = matching_esc.get("tier2_name") or "Anil Tankala / Kishore Nagarajan"
        t2_title = matching_esc.get("tier2_title") or "Service Delivery Manager (SDM)"
        t3_name = matching_esc.get("tier3_name") or "Nagarajan Kochunni / Radhakanta Samantara"
        t3_title = matching_esc.get("tier3_title") or "Project Director (PD)"

        # Check if currently filtered SDM matches Tier 2
        is_sdm_focused = clean_sdm_name and clean_sdm_name.lower() in t2_name.lower()
        t2_border = "border-left:3px solid #10b981;background:rgba(16,185,129,0.08);" if is_sdm_focused else "border-left:3px solid #f59e0b;background:#141619;"
        t2_badge = '<span style="font-size:8px;color:#10b981;font-weight:700;">● FILTER FOCUS</span>' if is_sdm_focused else '<span style="font-size:8.5px;color:#f59e0b;border:1px solid rgba(245,158,11,0.3);padding:1px 4px;border-radius:2px;">SDM Lead</span>'

        # Tier 1 badge label (Correct TL for PS, TM for Non-Core)
        if cur_div == "Production Support 24x7" or eng_div == "Production Support":
            t1_badge_label = "Offshore TL"
        elif cur_div == "Non-Core Dev" or eng_div == "Non-Core Dev":
            t1_badge_label = "Tech Mgr (TM)"
        else:
            t1_badge_label = "Lead TL"

        card_esc_t1 = (
            '<div style="display:flex;align-items:center;gap:8px;padding:4px 8px;background:#141619;border-radius:3px;border-left:3px solid #38bdf8;">'
            '<span style="font-size:9px;font-weight:800;font-family:var(--mono);color:#38bdf8;width:42px;">TIER 1</span>'
            '<div style="min-width:0;flex:1;">'
            f'<div style="font-size:10.5px;font-weight:700;color:var(--ink);">{escape(t1_name)}</div>'
            f'<div style="font-size:8.5px;color:var(--slate);">{escape(t1_title)} &bull; SLA: &le;15 mins</div>'
            f'</div><span style="font-size:8.5px;color:#38bdf8;border:1px solid rgba(56,189,248,0.3);padding:1px 4px;border-radius:2px;">{t1_badge_label}</span></div>'
        )
        # Tier 2 (SDM)
        t2_badge_label = "State SDM" if "State Specific" in t2_name else "SDM Lead"
        t2_badge = '<span style="font-size:8px;color:#10b981;font-weight:700;">● FILTER FOCUS</span>' if is_sdm_focused else f'<span style="font-size:8.5px;color:#f59e0b;border:1px solid rgba(245,158,11,0.3);padding:1px 4px;border-radius:2px;">{t2_badge_label}</span>'
        card_esc_t2 = (
            f'<div style="display:flex;align-items:center;gap:8px;padding:4px 8px;border-radius:3px;{t2_border}">'
            '<span style="font-size:9px;font-weight:800;font-family:var(--mono);color:#f59e0b;width:42px;">TIER 2</span>'
            '<div style="min-width:0;flex:1;">'
            f'<div style="font-size:10.5px;font-weight:700;color:var(--ink);">{escape(t2_name)}</div>'
            f'<div style="font-size:8.5px;color:var(--slate);">{escape(t2_title)} &bull; SLA: &le;30 mins</div>'
            f'</div>{t2_badge}</div>'
        )
        # Tier 3
        card_esc_t3 = (
            '<div style="display:flex;align-items:center;gap:8px;padding:4px 8px;background:#141619;border-radius:3px;border-left:3px solid #ef4444;">'
            '<span style="font-size:9px;font-weight:800;font-family:var(--mono);color:#ef4444;width:42px;">TIER 3</span>'
            '<div style="min-width:0;flex:1;">'
            f'<div style="font-size:10.5px;font-weight:700;color:var(--ink);">{escape(t3_name)}</div>'
            f'<div style="font-size:8.5px;color:var(--slate);">{escape(t3_title)} &bull; Executive Escalation</div>'
            '</div><span style="font-size:8.5px;color:#ef4444;border:1px solid rgba(239,68,68,0.3);padding:1px 4px;border-radius:2px;">Director</span></div>'
        )

        card_esc = (
            '<div style="background:#181b1f;border:1px solid #22252b;border-radius:3px;padding:8px 10px;margin-bottom:8px;">'
            '<div style="font-size:9.5px;font-weight:700;text-transform:uppercase;color:var(--slate);letter-spacing:0.04em;margin-bottom:6px;display:flex;align-items:center;justify-content:space-between;">'
            '<span>3-Tier Escalation Hierarchy</span>'
            '<span style="font-size:8px;color:#10b981;font-weight:600;">● Active Lineage</span>'
            '</div>'
            f'<div style="display:flex;flex-direction:column;gap:5px;">{card_esc_t1}{card_esc_t2}{card_esc_t3}</div>'
            '</div>'
        )
        st.markdown(card_esc, unsafe_allow_html=True)

        # 4. Weekly Schedule Strip for this Engineer
        if matching_ps:
            today_bull = " <b style='color:#f59e0b;'>&bull;</b>"
            sched_chips = "".join(
                f"<div style='text-align:center;padding:2px 4px;border-radius:3px;"
                f"{'background:rgba(245,158,11,0.12);border:1px solid rgba(245,158,11,0.3);' if p['shift_date'] == today_str else ''}'>"
                f"<div style='font-size:8.5px;color:var(--mute);'>{p['day_name'][:3]}"
                f"{today_bull if p['shift_date'] == today_str else ''}</div>"
                f"<div style='margin-top:2px;'>{ui.on_call_status_chip(p['shift_window'])}</div></div>"
                for p in matching_ps
            )
            card_weekly = (
                '<div style="background:#141619;border:1px solid #22252b;border-radius:3px;padding:6px 10px;margin-bottom:8px;">'
                '<div style="font-size:9.5px;font-weight:700;text-transform:uppercase;color:var(--slate);letter-spacing:0.04em;margin-bottom:4px;display:flex;justify-content:space-between;">'
                '<span>Weekly Schedule Horizon</span>'
                f'<span style="color:var(--mute);font-size:8.5px;">Today: {today_str}</span>'
                '</div>'
                f'<div style="display:flex;align-items:center;justify-content:space-between;overflow-x:auto;gap:4px;">{sched_chips}</div>'
                '</div>'
            )
            st.markdown(card_weekly, unsafe_allow_html=True)
        elif matching_shifts:
            active_dates = sorted(list(set((s.get('day_name', 'Day')[:3], s.get('shift_date', '')) for s in matching_shifts)), key=lambda x: x[1])
            today_bull = " <b style='color:#f59e0b;'>&bull;</b>"
            dom_chips = "".join(
                f"<div style='text-align:center;padding:3px 6px;border-radius:3px;"
                f"{'background:rgba(245,158,11,0.12);border:1px solid rgba(245,158,11,0.3);' if d[1] == today_str else 'background:#141619;border:1px solid #22252b;'}' title='On-Call on {d[1]}'>"
                f"<div style='font-size:8.5px;color:var(--mute);'>{d[0]}{today_bull if d[1] == today_str else ''}</div>"
                f"<div style='margin-top:2px;'><span class='shift-badge-m'>ON-CALL</span></div></div>"
                for d in active_dates
            )
            card_weekly = (
                '<div style="background:#141619;border:1px solid #22252b;border-radius:3px;padding:6px 10px;margin-bottom:8px;">'
                '<div style="font-size:9.5px;font-weight:700;text-transform:uppercase;color:var(--slate);letter-spacing:0.04em;margin-bottom:4px;display:flex;justify-content:space-between;">'
                '<span>Weekly Rotation Schedule</span>'
                f'<span style="color:var(--mute);font-size:8.5px;">{len(active_dates)} Days Assigned</span>'
                '</div>'
                f'<div style="display:flex;align-items:center;justify-content:flex-start;overflow-x:auto;gap:6px;">{dom_chips}</div>'
                '</div>'
            )
            st.markdown(card_weekly, unsafe_allow_html=True)
        else:
            st.markdown(
                '<div style="background:#141619;border:1px dashed #2c3235;border-radius:3px;padding:10px;text-align:center;color:var(--mute);font-size:10px;margin-bottom:8px;">'
                'Resource assigned to domain shift rotations. See Master pane for shift hours.'
                '</div>',
                unsafe_allow_html=True,
            )

        # 5. Quick Dispatch Actions & 1-Click Clipboard Ready Block
        act_c1, act_c2 = st.columns(2)
        with act_c1:
            if st.button("📧 Dispatch Notice", key=f"oncall_disp_btn_{sel_name}", type="primary", use_container_width=True):
                st.success(f"✓ Dispatch notice queued to {sel_name} and {t1_name} ({t1_title}).")
        with act_c2:
            copy_clicked = st.button("📋 Copy Escalation", key=f"oncall_copy_btn_{sel_name}", type="secondary", use_container_width=True)
            if copy_clicked:
                ss["oncall_copy_target"] = sel_name if ss.get("oncall_copy_target") != sel_name else None

        if ss.get("oncall_copy_target") == sel_name:
            copy_txt = (
                f"[ON-CALL ESCALATION] — {sel_name} ({eng_div} · {eng_domain})\n"
                f"• Scheduled Shift: {t_ist}\n"
                f"• Tier 1 ({t1_badge_label}, ≤15m): {t1_name} ({t1_title})\n"
                f"• Tier 2 (SDM Lead, ≤30m): {t2_name} ({t2_title})\n"
                f"• Tier 3 (Executive Escalation): {t3_name} ({t3_title})"
            )
            st.code(copy_txt, language="text")
        st.markdown("</div>", unsafe_allow_html=True)
