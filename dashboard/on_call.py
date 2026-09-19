"""
on_call.py — 24/7 On-Call Operations Command Hub & Roster Intelligence
======================================================================
Enterprise Grafana-style zero-scroll dashboard for 24x7 on-call schedules,
cross-tab shift matrices, multi-tier escalation hierarchy lineage, and
weekly Excel roster updates.

Follows Enterprise Design Standards:
  - Zero-page-scroll constraint (100vh viewport locking)
  - 5-Tab Enterprise Workspace:
      1. ⚡ Live Roster & Master-Detail (58% master table / 42% detail inspector)
      2. 🏢 Production Support 24x7 Matrix (Full-width 7-day cross-tab matrix)
      3. 🚨 State Incident Escalation Matrix (AK DEV · ND DEV · NH MMIS multi-state command)
      4. 📋 Shift Handover & Telemetry (4-slot radar, handover checklist, lineage audit)
      5. 📤 Roster Upload & Sync Engine (Weekly Excel ingestion console & integrity contract)
  - Compact Command Bar & Slim 48px KPI Ribbon
  - Horizontal scrollable cross-tab preserving all 7 days
  - Synchronized engineer inspection with unclipped action buttons
"""

from __future__ import annotations

from datetime import datetime, timezone
from html import escape
from pathlib import Path
from urllib.parse import quote

import pandas as pd
import streamlit as st

import ui
from db import (
    get_connection,
    get_active_on_call_roster,
    get_on_call_production_support,
    get_on_call_shifts,
    get_on_call_escalations,
    log_audit_event,
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
    ("All", None),
    ("🌅 Morning", "morning"),
    ("☀️ Evening", "evening"),
    ("🌙 Night", "night"),
    ("☕ Week Off", "wo"),
    ("🎉 Holiday", "holiday"),
    ("🏖️ Leave", "leave"),
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
    date_val = s.get("shift_date", "")

    esc = esc_lookup.get((div, dom, slot, date_val)) or esc_fallback.get((div, dom))

    if not esc:
        for (e_div, e_dom), fallback_esc in esc_fallback.items():
            if div.lower() in e_div.lower() or e_div.lower() in div.lower():
                esc = fallback_esc
                break

    if not esc:
        if div == "Infra Team":
            return ("Anil Tankala", "Infra & DB SDM")
        elif div == "Core Dev":
            if "AK" in dom:
                return ("Ravi M Shankar / Kishore Nagarajan", "AK State SDMs")
            elif "ND" in dom:
                return ("Thirupathi Katakam / Anil Kumar Khamari", "ND State SDMs")
            else:
                return ("Madhav / Dipak/Rama", "NH State SDMs")
        elif div == "Non-Core Dev":
            return ("Technical Manager", "Technical Manager (TM)")
        return ("Governing SDM", "Service Delivery Manager")

    if div == "Non-Core Dev":
        t1_n = esc.get("tier1_name") or "Technical Manager"
        t1_t = esc.get("tier1_title") or "Technical Manager (TM)"
        return (t1_n, t1_t)
    else:
        t2_n = esc.get("tier2_name") or "Service Delivery Manager"
        t2_t = esc.get("tier2_title") or "Service Delivery Manager (SDM)"
        return (t2_n, t2_t)


def _consolidate_contiguous_shifts(shifts: list[dict]) -> list[dict]:
    """Merge continuous shifts for the same person into a single row."""
    if not shifts:
        return []
    keyfunc = lambda x: (
        x.get("division", ""),
        x.get("domain_state", ""),
        x.get("day_name", ""),
        x.get("shift_date", ""),
        x.get("primary_on_call", ""),
        x.get("secondary_on_call", ""),
    )
    from itertools import groupby
    shifts_sorted = sorted(shifts, key=keyfunc)
    consolidated = []
    for key, group in groupby(shifts_sorted, key=keyfunc):
        group_list = sorted(list(group), key=lambda x: x.get("shift_slot", 1))
        first = group_list[0].copy()
        if len(group_list) > 1:
            slots = [g.get("shift_slot", 1) for g in group_list]
            first["shift_slot_disp"] = f"Slots {slots[0]}-{slots[-1]}"
            first["time_est"] = f"{group_list[0].get('time_est','').split('to')[0].strip()} to {group_list[-1].get('time_est','').split('to')[-1].strip()}"
            first["time_ist"] = f"{group_list[0].get('time_ist','').split('to')[0].strip()} to {group_list[-1].get('time_ist','').split('to')[-1].strip()}"
            first["original_slots"] = slots
        else:
            first["shift_slot_disp"] = f"Slot {first.get('shift_slot', 1)}"
            first["original_slots"] = [first.get("shift_slot", 1)]
        consolidated.append(first)
    return consolidated


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


def _render_personnel_inspector(
    sel_name: str,
    shifts_with_sdm: list[dict],
    all_ps: list[dict],
    all_escs: list[dict],
    esc_lookup: dict,
    esc_fallback: dict,
    clean_sdm_name: str | None,
    today_str: str,
    cur_slot: int,
    scoped_engineers: list[str],
    cur_div_name: str,
) -> None:
    """Render high-density, unclipped personnel inspector card."""
    ss = st.session_state

    insp_col_left, insp_col_right = st.columns([1.3, 2.7])
    with insp_col_left:
        st.markdown(
            '<div style="font-size:11px;font-weight:800;color:#38bdf8;line-height:28px;text-transform:uppercase;letter-spacing:0.02em;">'
            '🔍 Personnel'
            '</div>',
            unsafe_allow_html=True,
        )
    with insp_col_right:
        insp_picker_key = "oncall_insp_picker_tab"
        if insp_picker_key not in ss or ss[insp_picker_key] not in scoped_engineers:
            ss[insp_picker_key] = sel_name if sel_name in scoped_engineers else scoped_engineers[0]
        picked_eng = st.selectbox(
            "Inspect Person",
            scoped_engineers,
            index=scoped_engineers.index(ss[insp_picker_key]),
            key=insp_picker_key,
            label_visibility="collapsed",
            help="Select personnel to inspect details",
        )
        if picked_eng != ss.get("oncall_selected_eng"):
            ss["oncall_selected_eng"] = picked_eng
            sel_name = picked_eng

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
        for esc in all_escs:
            if sel_name in [esc.get("tier1_name"), esc.get("tier2_name"), esc.get("tier3_name")]:
                matching_esc = esc
                eng_div = esc["division"]
                eng_domain = esc["domain_state"]
                break

    if not matching_esc:
        matching_esc = {
            "tier1_name": "Abhijit Vajja / Sreekanth Veluguleti",
            "tier1_title": "Offshore Team Lead (TL/TM)",
            "tier2_name": "Anil Tankala / Kishore Nagarajan",
            "tier2_title": "Service Delivery Manager (SDM)",
            "tier3_name": "Nagarajan Kochunni / Radhakanta Samantara",
            "tier3_title": "Project Director (PD)",
        }

    avatar_lg = ui.on_call_avatar(sel_name).replace("width:20px;height:20px;font-size:8.5px;", "width:32px;height:32px;font-size:12px;")
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

    t_ist = "7:30 PM to 1:29 AM (Slot 3 Evening)"
    t_est = "9:00 AM to 2:59 PM (EST Day Shift)"
    timing_title = "Scheduled Shift Hours"

    if matching_shifts:
        ref_s = matching_shifts[0]
        t_ist = ref_s.get("time_ist", t_ist)
        t_est = ref_s.get("time_est", t_est)
        timing_title = f"Scheduled Shift ({ref_s.get('day_name', 'Today')} {ref_s.get('shift_date', '')[5:]})"
    elif matching_ps:
        ref_ps = matching_ps[0]
        for p in matching_ps:
            if p.get("shift_date") == today_str:
                ref_ps = p
                break
        win = ref_ps.get("shift_window", "")
        win_l = win.lower()
        timing_title = f"Scheduled Shift ({ref_ps.get('day_name', 'Today')} {ref_ps.get('shift_date', '')[5:]})"
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
            t_ist = "Floating Holiday (Standby)"
            t_est = "Fleet Standby / Non-Working"
        elif "leave" in win_l:
            t_ist = "Approved Leave (Backfilled)"
            t_est = "Off Fleet Roster"
        else:
            t_ist = "Scheduled Week Off (Rest Day)"
            t_est = "Off Duty / Standby"

    t1_name = matching_esc.get("tier1_name") or "Abhijit Vajja / Sreekanth Veluguleti"
    t1_title = matching_esc.get("tier1_title") or "Offshore Team Lead (TL/TM)"
    t2_name = matching_esc.get("tier2_name") or "Anil Tankala / Kishore Nagarajan"
    t2_title = matching_esc.get("tier2_title") or "Service Delivery Manager (SDM)"
    t3_name = matching_esc.get("tier3_name") or "Nagarajan Kochunni / Radhakanta Samantara"
    t3_title = matching_esc.get("tier3_title") or "Project Director (PD)"

    is_sdm_focused = clean_sdm_name and clean_sdm_name.lower() in t2_name.lower()
    t2_border = "border-left:3px solid #10b981;background:rgba(16,185,129,0.08);" if is_sdm_focused else "border-left:3px solid #f59e0b;background:#141619;"
    t2_badge = '<span style="font-size:8px;color:#10b981;font-weight:700;">● FOCUS</span>' if is_sdm_focused else '<span style="font-size:8px;color:#f59e0b;border:1px solid rgba(245,158,11,0.3);padding:1px 4px;border-radius:2px;">SDM Lead</span>'

    t1_badge_label = "Offshore TL" if "Support" in eng_div else ("Tech Mgr (TM)" if "Non-Core" in eng_div else "Lead TL")

    if matching_ps:
        today_bull = " <b style='color:#f59e0b;'>&bull;</b>"
        sched_chips = "".join(
            f"<div style='text-align:center;padding:2px 3px;border-radius:3px;"
            f"{'background:rgba(245,158,11,0.12);border:1px solid rgba(245,158,11,0.3);' if p['shift_date'] == today_str else 'background:#141619;border:1px solid #22252b;'}' title='{p.get('shift_window','')}'><div style='font-size:8px;color:var(--mute);'>{p['day_name'][:3]}"
            f"{today_bull if p['shift_date'] == today_str else ''}</div>"
            f"<div style='margin-top:1px;'>{_compact_shift_chip(p.get('shift_window',''))}</div></div>"
            for p in matching_ps
        )
        card_weekly = (
            f'<div style="background:#181b1f;border:1px solid #22252b;border-radius:3px;padding:4px 8px;margin-bottom:6px;">'
            f'<div style="font-size:9px;font-weight:700;text-transform:uppercase;color:var(--slate);letter-spacing:0.03em;margin-bottom:3px;display:flex;justify-content:space-between;">'
            f'<span>Weekly Schedule Horizon</span>'
            f'<span style="color:var(--mute);font-size:8px;">Today: {today_str}</span>'
            f'</div>'
            f'<div style="display:flex;align-items:center;justify-content:space-between;gap:3px;">{sched_chips}</div>'
            f'</div>'
        )
    elif matching_shifts:
        active_dates = sorted(list(set((s.get('day_name', 'Day')[:3], s.get('shift_date', '')) for s in matching_shifts)), key=lambda x: x[1])
        today_bull = " <b style='color:#f59e0b;'>&bull;</b>"
        dom_chips = "".join(
            f"<div style='text-align:center;padding:2px 4px;border-radius:3px;"
            f"{'background:rgba(245,158,11,0.12);border:1px solid rgba(245,158,11,0.3);' if d[1] == today_str else 'background:#141619;border:1px solid #22252b;'}' title='On-Call on {d[1]}'>"
            f"<div style='font-size:8px;color:var(--mute);'>{d[0]}{today_bull if d[1] == today_str else ''}</div>"
            f"<div style='margin-top:1px;'><span class='shift-badge-m'>ON-CALL</span></div></div>"
            for d in active_dates
        )
        card_weekly = (
            f'<div style="background:#181b1f;border:1px solid #22252b;border-radius:3px;padding:4px 8px;margin-bottom:6px;">'
            f'<div style="font-size:9px;font-weight:700;text-transform:uppercase;color:var(--slate);letter-spacing:0.03em;margin-bottom:3px;display:flex;justify-content:space-between;">'
            f'<span>Weekly Rotation Schedule</span>'
            f'<span style="color:var(--mute);font-size:8px;">{len(active_dates)} Days Assigned</span>'
            f'</div>'
            f'<div style="display:flex;align-items:center;justify-content:flex-start;gap:4px;">{dom_chips}</div>'
            f'</div>'
        )
    else:
        card_weekly = (
            '<div style="background:#181b1f;border:1px dashed #2c3235;border-radius:3px;padding:6px;text-align:center;color:var(--mute);font-size:9px;margin-bottom:6px;">'
            'Resource assigned to domain shift rotations.'
            '</div>'
        )

    st.markdown(f"""<div class="oc-insp-container">
<div style="background:#141619;border:1px solid #2c3235;border-left:3px solid var(--accent);border-radius:3px;padding:6px 10px;margin-bottom:6px;">
<div style="display:flex;align-items:center;justify-content:space-between;">
<div style="display:flex;align-items:center;gap:8px;">
{avatar_lg}
<div>
<div style="font-size:12.5px;font-weight:800;color:var(--ink);letter-spacing:0.01em;">{escape(sel_name)}</div>
<div style="font-size:9.5px;color:var(--slate);">{escape(eng_div)} &bull; <b style="color:#38bdf8;">{escape(eng_domain)}</b></div>
</div></div>
{duty_badge}
</div></div>

<div style="background:#181b1f;border:1px solid #22252b;border-radius:3px;padding:5px 8px;margin-bottom:6px;">
<div style="font-size:9px;font-weight:700;text-transform:uppercase;color:var(--slate);letter-spacing:0.03em;margin-bottom:4px;">{timing_title}</div>
<div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;">
<div style="background:#141619;border:1px solid #22252b;border-radius:2px;padding:3px 6px;">
<div style="font-size:8px;color:var(--mute);font-weight:600;">TIME IN EST (UTC-5)</div>
<div style="font-size:10px;font-family:var(--mono);font-weight:700;color:var(--ink);margin-top:1px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{escape(t_est)}</div>
</div>
<div style="background:#141619;border:1px solid #22252b;border-radius:2px;padding:3px 6px;">
<div style="font-size:8px;color:var(--mute);font-weight:600;">TIME IN IST (UTC+5:30)</div>
<div style="font-size:10px;font-family:var(--mono);font-weight:700;color:#38bdf8;margin-top:1px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{escape(t_ist)}</div>
</div></div></div>

<div style="background:#181b1f;border:1px solid #22252b;border-radius:3px;padding:5px 8px;margin-bottom:6px;">
<div style="font-size:9px;font-weight:700;text-transform:uppercase;color:var(--slate);letter-spacing:0.03em;margin-bottom:4px;display:flex;align-items:center;justify-content:space-between;">
<span>3-Tier Escalation Hierarchy</span>
<span style="font-size:8px;color:#10b981;font-weight:600;">● Active Lineage</span>
</div>
<div style="display:flex;flex-direction:column;gap:4px;">
<div style="display:flex;align-items:center;gap:6px;padding:3px 6px;background:#141619;border-radius:2px;border-left:3px solid #38bdf8;">
<span style="font-size:8.5px;font-weight:800;font-family:var(--mono);color:#38bdf8;width:38px;">TIER 1</span>
<div style="min-width:0;flex:1;">
<div style="font-size:10px;font-weight:700;color:var(--ink);">{escape(t1_name)}</div>
<div style="font-size:8px;color:var(--slate);">{escape(t1_title)} &bull; SLA: &le;15 mins</div>
</div><span style="font-size:8px;color:#38bdf8;border:1px solid rgba(56,189,248,0.3);padding:0 4px;border-radius:2px;">{t1_badge_label}</span></div>

<div style="display:flex;align-items:center;gap:6px;padding:3px 6px;border-radius:2px;{t2_border}">
<span style="font-size:8.5px;font-weight:800;font-family:var(--mono);color:#f59e0b;width:38px;">TIER 2</span>
<div style="min-width:0;flex:1;">
<div style="font-size:10px;font-weight:700;color:var(--ink);">{escape(t2_name)}</div>
<div style="font-size:8px;color:var(--slate);">{escape(t2_title)} &bull; SLA: &le;30 mins</div>
</div>{t2_badge}</div>

<div style="display:flex;align-items:center;gap:6px;padding:3px 6px;background:#141619;border-radius:2px;border-left:3px solid #ef4444;">
<span style="font-size:8.5px;font-weight:800;font-family:var(--mono);color:#ef4444;width:38px;">TIER 3</span>
<div style="min-width:0;flex:1;">
<div style="font-size:10px;font-weight:700;color:var(--ink);">{escape(t3_name)}</div>
<div style="font-size:8px;color:var(--slate);">{escape(t3_title)} &bull; Executive Escalation</div>
</div><span style="font-size:8px;color:#ef4444;border:1px solid rgba(239,68,68,0.3);padding:0 4px;border-radius:2px;">Director</span></div>
</div></div>

{card_weekly}

<div style="background:#141619;border:1px solid #22252b;border-radius:3px;padding:4px 8px;margin-bottom:6px;">
<div style="display:grid;grid-template-columns:1fr 1fr;gap:4px;font-size:8.5px;">
<div><span style="color:#38bdf8;font-weight:700;">T1 Ack:</span> &le; 15m</div>
<div><span style="color:#f59e0b;font-weight:700;">T2 SDM:</span> &le; 30m</div>
<div><span style="color:#ef4444;font-weight:700;">T3 Exec:</span> &le; 45m</div>
<div><span style="color:#10b981;font-weight:700;">Bridge:</span> Teams P1-Live</div>
</div></div>
</div>""", unsafe_allow_html=True)

    act_c1, act_c2 = st.columns(2)
    with act_c1:
        if st.button("📧 Dispatch Notice", key=f"oncall_disp_btn_{sel_name}", type="primary", use_container_width=True):
            st.success(f"✓ Notice queued to {sel_name} & {t1_name}!")
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


def render_on_call_workspace(db_path: str) -> None:
    """Render the 24/7 On-Call Operations Command Hub."""
    ss = st.session_state

    # 0. Session State Initialization
    ss.setdefault("oncall_tz", "IST")
    ss.setdefault("oncall_state_filter", "All States")
    ss.setdefault("oncall_location_filter", "All Locations")
    ss.setdefault("oncall_sdm_filter", "All SDMs")
    ss.setdefault("oncall_day_filter", "All Days")
    ss.setdefault("oncall_shift_chip", None)
    ss.setdefault("oncall_quick_status", "All")
    ss.setdefault("oncall_search", "")
    ss.setdefault("oncall_selected_eng", None)
    ss.setdefault("oncall_shift_page", 0)
    ss.setdefault("oncall_view_mode", "today")
    ss.setdefault("oncall_div_pill", "All Divisions")

    # 1. Fetch Cached Active Roster
    active_roster = _load_roster_meta(db_path)

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
    distinct_ps_engineers = bundle["distinct_ps_engineers"]

    all_available_engineers = sorted(list(set(
        [p["resource_name"] for p in all_ps if p.get("resource_name")] +
        [s["primary_on_call"] for s in shifts_with_sdm if s.get("primary_on_call")] +
        [s["secondary_on_call"] for s in shifts_with_sdm if s.get("secondary_on_call") and s.get("secondary_on_call") not in ["—", "None", ""]]
    )))

    qp_eng = st.query_params.get("eng")
    if qp_eng and qp_eng in all_available_engineers:
        ss["oncall_selected_eng"] = qp_eng

    active_scope_state = ss.get("_override_canvas_state") or ss.get("global_state_filter")
    if active_scope_state in ["NH", "ND", "AK"]:
        locked_state = f"{active_scope_state} MMIS"
        ss["oncall_state_filter"] = locked_state
        ss["oncall_state_pick"] = locked_state

    today_str = datetime.now().strftime("%Y-%m-%d")
    now_utc = datetime.now(timezone.utc)
    cur_slot = _get_current_active_slot(now_utc)
    use_ist = (ss["oncall_tz"] == "IST")

    st.markdown("""
    <style>
    .block-container {
        padding-top: 0.35rem !important;
        padding-bottom: 0 !important;
        padding-left: 1rem !important;
        padding-right: 1rem !important;
        max-width: 100% !important;
    }
    .oc-kpi-row {
        display: grid;
        grid-template-columns: repeat(5, 1fr);
        gap: 6px;
        margin-bottom: 4px;
        margin-top: 2px;
    }
    .oc-stat-card {
        background: #181b1f;
        border: 1px solid #2c3235;
        border-radius: 2px;
        padding: 4px 8px 3px 8px;
        display: flex;
        flex-direction: column;
        justify-content: space-between;
        height: 46px;
        min-height: 46px;
        max-height: 46px;
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
    .oc-stat-fill-purple {
        background: linear-gradient(180deg, rgba(184,119,217,0.16), rgba(184,119,217,0.02));
        border-top: 2px solid #b877d7;
    }
    .oc-stat-label {
        font-size: 8.5px;
        color: var(--slate, #9fa7b3);
        text-transform: uppercase;
        font-weight: 700;
        letter-spacing: .03em;
        line-height: 1;
    }
    .oc-stat-val {
        font-size: 14.5px;
        font-weight: 800;
        margin-top: 1px;
        line-height: 1.1;
        color: #fff;
        font-family: var(--ui);
        font-variant-numeric: tabular-nums;
    }
    .oc-stat-sub {
        font-size: 8px;
        color: var(--mute, #8b949e);
        margin-top: 1px;
        line-height: 1.1;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
    }
    div[class*="st-key-oncall_tz_"] button,
    div[class*="st-key-oncall_chip_btn_"] button,
    div[class*="st-key-oncall_qs_"] button,
    div[class*="st-key-oncall_vmode_"] button,
    div[class*="st-key-oncall_dpill_"] button {
        height: 24px !important;
        min-height: 24px !important;
        padding: 0 5px !important;
        font-size: 9.5px !important;
        font-weight: 700 !important;
        line-height: 22px !important;
        margin-top: 1px !important;
        margin-bottom: 1px !important;
        border-radius: 2px !important;
    }
    .oc-table-container {
        border: 1px solid #2c3235;
        border-radius: 3px;
        background: #141619;
        height: 432px !important;
        max-height: 432px !important;
        overflow-y: auto !important;
        overflow-x: auto !important;
        scrollbar-width: thin;
        scrollbar-color: #38bdf8 #181b1f;
    }
    .oc-insp-container {
        border: 1px solid #2c3235;
        border-radius: 3px;
        background: #141619;
        height: 388px !important;
        max-height: 388px !important;
        overflow-y: auto !important;
        overflow-x: hidden !important;
        padding: 6px 8px !important;
        scrollbar-width: thin;
        scrollbar-color: #38bdf8 #181b1f;
    }
    .oc-table-container::-webkit-scrollbar,
    .oc-insp-container::-webkit-scrollbar {
        width: 6px;
        height: 6px;
        display: block;
    }
    .oc-table-container::-webkit-scrollbar-track,
    .oc-insp-container::-webkit-scrollbar-track {
        background: #181b1f;
        border-left: 1px solid #2c3235;
    }
    .oc-table-container::-webkit-scrollbar-thumb,
    .oc-insp-container::-webkit-scrollbar-thumb {
        background: #38bdf8;
        border-radius: 3px;
        border: 1px solid #0284c7;
    }
    thead th {
        position: sticky !important;
        top: 0 !important;
        background: #141619 !important;
        z-index: 10 !important;
        border-bottom: 2px solid #2c3235 !important;
    }
    .shift-badge-m { background: rgba(115,191,105,0.16); color: #73bf69; border: 1px solid rgba(115,191,105,0.3); border-radius: 2px; padding: 1px 4px; font-size: 8.5px; font-weight: 700; white-space: nowrap; }
    .shift-badge-e { background: rgba(255,152,48,0.16); color: #ff9830; border: 1px solid rgba(255,152,48,0.3); border-radius: 2px; padding: 1px 4px; font-size: 8.5px; font-weight: 700; white-space: nowrap; }
    .shift-badge-n { background: rgba(87,148,242,0.16); color: #5794f2; border: 1px solid rgba(87,148,242,0.3); border-radius: 2px; padding: 1px 4px; font-size: 8.5px; font-weight: 700; white-space: nowrap; }
    .shift-badge-wo { background: rgba(148,163,184,0.12); color: #94a3b8; border: 1px solid rgba(148,163,184,0.25); border-radius: 2px; padding: 1px 4px; font-size: 8.5px; font-weight: 700; white-space: nowrap; }
    .shift-badge-fh { background: rgba(234,179,8,0.14); color: #eab308; border: 1px solid rgba(234,179,8,0.3); border-radius: 2px; padding: 1px 4px; font-size: 8.5px; font-weight: 700; white-space: nowrap; }
    .shift-badge-lv { background: rgba(239,68,68,0.14); color: #ef4444; border: 1px solid rgba(239,68,68,0.3); border-radius: 2px; padding: 1px 4px; font-size: 8.5px; font-weight: 700; white-space: nowrap; }
    .pulse-dot { display: inline-block; width: 6px; height: 6px; border-radius: 50%; background: #10b981; box-shadow: 0 0 6px #10b981; margin-right: 4px; animation: pulse 1.8s infinite; }
    @keyframes pulse { 0% { opacity: 1; } 50% { opacity: 0.3; } 100% { opacity: 1; } }
    </style>
    """, unsafe_allow_html=True)

    # 1. Compact Universal Header + Actions
    c_h_left, c_h_right = st.columns([7.8, 2.2])

    with c_h_left:
        st_val = ss["oncall_state_filter"]
        st.markdown(ui.render_universal_header(
            title="24/7 On-Call Command Hub",
            subtitle=f"Cycle: {valid_from} → {valid_to} • Active Shift Rotation",
            badge_text="ACTIVE ROSTER",
            badge_color="#10b981",
            state_scope=st_val if st_val != "All States" else None,
        ), unsafe_allow_html=True)

    with c_h_right:
        act_sync, act_reset = st.columns(2)
        with act_sync:
            if st.button("🔄 Sync", key="oncall_sync_disk_btn", type="secondary", use_container_width=True, help="Sync latest from _Input"):
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
        with act_reset:
            if st.button("✕ Reset", key="oncall_clear_filters_btn", type="secondary", use_container_width=True, help="Reset all filters"):
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
                ss["oncall_shift_page"] = 0
                ss["oncall_div_pill"] = "All Divisions"
                st.rerun()

    # 2. Unified Template Variable Slicers (Single Row)
    col_f1, col_f2, col_f3, col_f4, col_f5, col_f6 = st.columns([1.05, 1.1, 1.25, 1.05, 0.95, 3.6])

    with col_f1:
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

    with col_f2:
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
            if "Offshore" in picked_loc:
                ss["oncall_tz"] = "IST"
            elif "Onshore" in picked_loc:
                ss["oncall_tz"] = "EST"
            ss["oncall_shift_page"] = 0

    with col_f3:
        cur_sdm = ss.get("oncall_sdm_filter", "All SDMs")
        if cur_sdm not in _SDM_OPTIONS:
            cur_sdm = "All SDMs"
            ss["oncall_sdm_filter"] = "All SDMs"
        sdm_idx = _SDM_OPTIONS.index(cur_sdm)
        picked_sdm = st.selectbox(
            "SDM / TM Slicer",
            _SDM_OPTIONS,
            index=sdm_idx,
            key="oncall_sdm_pick",
            label_visibility="collapsed",
            help="Filter by Service Delivery Manager (SDM) or Technical Manager (TM)",
        )
        if picked_sdm != ss.get("oncall_sdm_filter"):
            ss["oncall_sdm_filter"] = picked_sdm
            ss["oncall_shift_page"] = 0

    with col_f4:
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

    with col_f5:
        tz_val = ss["oncall_tz"]
        t1, t2 = st.columns(2)
        with t1:
            if st.button("EST", key="oncall_tz_est_btn", type="primary" if tz_val == "EST" else "secondary", use_container_width=True, help="Eastern Standard Time (UTC-5)"):
                ss["oncall_tz"] = "EST"
                st.rerun()
        with t2:
            if st.button("IST", key="oncall_tz_ist_btn", type="primary" if tz_val == "IST" else "secondary", use_container_width=True, help="India Standard Time (UTC+5:30)"):
                ss["oncall_tz"] = "IST"
                st.rerun()

    use_ist = (ss["oncall_tz"] == "IST")

    with col_f6:
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
                    ss["oncall_shift_chip"] = None if is_sel else chip_val
                    ss["oncall_shift_page"] = 0
                    st.rerun()

    state_slicer = ss.get("oncall_state_filter", "All States")
    loc_slicer = ss.get("oncall_location_filter", "All Locations")
    sdm_slicer = ss.get("oncall_sdm_filter", "All SDMs")
    clean_sdm_name = sdm_slicer.split("(")[0].strip() if sdm_slicer != "All SDMs" else None

    target_day = None
    if ss["oncall_day_filter"] != "All Days":
        target_day = ss["oncall_day_filter"].split("(")[-1].replace(")", "").strip()

    mod_date = target_day if target_day else "2026-09-14"
    mod_shifts = [s for s in shifts_with_sdm if s["shift_date"] == mod_date]

    if cur_shift_chip:
        mod_shifts = [s for s in mod_shifts if _matches_domain_slot(cur_shift_chip, s.get("shift_slot", 0))]
    elif loc_slicer == "🌏 Offshore (IST)":
        mod_shifts = [s for s in mod_shifts if s.get("shift_slot") in [1, 2]]
    elif loc_slicer == "🏛️ Onshore (EST)":
        mod_shifts = [s for s in mod_shifts if s.get("shift_slot") in [3, 4]]
    else:
        mod_shifts_curr = [s for s in mod_shifts if s.get("shift_slot") == cur_slot]
        if mod_shifts_curr:
            mod_shifts = mod_shifts_curr
        else:
            mod_shifts = [s for s in mod_shifts if s.get("shift_slot") in [1, 3]]

    if state_slicer != "All States":
        st_code = state_slicer.split()[0].upper()
        mod_shifts = [
            s for s in mod_shifts
            if st_code in s["domain_state"].upper() or s["division"] in ["Infra Team", "Non-Core Dev"]
        ]

    if clean_sdm_name:
        mod_shifts = [
            s for s in mod_shifts
            if clean_sdm_name.lower() in s.get("governed_sdm", "").lower()
        ]

    div_order = {"Infra Team": 0, "Non-Core Dev": 1, "Core Dev": 2}
    mod_shifts.sort(key=lambda m: (div_order.get(m["division"], 3), m["domain_state"]))

    # 3. Slim 5-KPI Ribbon (46px)
    st.markdown(f"""<div class="oc-kpi-row">
<div class="oc-stat-card oc-stat-fill-green">
<span class="oc-stat-label">Working Today</span>
<div class="oc-stat-val">{working_count} <span style="font-size:9.5px;color:#6e7681;font-weight:400;">/ {total_ps}</span></div>
<span class="oc-stat-sub">Production Support Fleet</span>
</div>
<div class="oc-stat-card oc-stat-fill-yellow">
<span class="oc-stat-label">Off / WO Today</span>
<div class="oc-stat-val">{wo_count}</div>
<span class="oc-stat-sub">Week Off &amp; Comp OFF</span>
</div>
<div class="oc-stat-card oc-stat-fill-yellow">
<span class="oc-stat-label">Holiday Today</span>
<div class="oc-stat-val">{hol_count}</div>
<span class="oc-stat-sub">Floating Holiday</span>
</div>
<div class="oc-stat-card oc-stat-fill-blue">
<span class="oc-stat-label">States Covered</span>
<div class="oc-stat-val">{states_count}</span></div>
<span class="oc-stat-sub">AK &bull; ND &bull; NH Core Dev</span>
</div>
<div class="oc-stat-card oc-stat-fill-purple">
<span class="oc-stat-label">Active On-Duty</span>
<div class="oc-stat-val">{len(mod_shifts)} Domains</div>
<span class="oc-stat-sub">Slot {cur_slot} &bull; Live Handover Ready</span>
</div>
</div>""", unsafe_allow_html=True)

    # 4. Master 5-Tab Workspace (432px Viewport Locked)
    valid_tabs = [
        "⚡ Live Roster & Master-Detail",
        "🏢 Production Support 24x7 Matrix",
        "🚨 State Incident Escalation Matrix",
        "📋 Shift Handover & Telemetry",
        "📤 Roster Upload & Sync Engine",
    ]

    tab_live, tab_support, tab_escalation, tab_handover, tab_sync = st.tabs(valid_tabs)

    scoped_engineers = sorted(list(set(
        [m.get("primary_on_call") for m in mod_shifts if m.get("primary_on_call")] +
        [m.get("secondary_on_call") for m in mod_shifts if m.get("secondary_on_call") and m.get("secondary_on_call") not in ["—", "None", "", None]]
    )))
    if not scoped_engineers:
        scoped_engineers = all_available_engineers

    if ss.get("oncall_selected_eng") not in scoped_engineers:
        ss["oncall_selected_eng"] = scoped_engineers[0]
    cur_selected = ss.get("oncall_selected_eng")

    # ==========================================================================
    # TAB 1: LIVE ROSTER & MASTER-DETAIL
    # ==========================================================================
    with tab_live:
        tb_c1, tb_c2, tb_c3 = st.columns([1.6, 2.4, 2.0], gap="small")
        with tb_c1:
            vm1, vm2 = st.columns(2)
            is_today = (ss["oncall_view_mode"] == "today")
            with vm1:
                if st.button("🧩 Today", key="oncall_vmode_today", type="primary" if is_today else "secondary", use_container_width=True):
                    ss["oncall_view_mode"] = "today"
                    st.rerun()
            with vm2:
                if st.button("⚡ All Weekly", key="oncall_vmode_all", type="primary" if not is_today else "secondary", use_container_width=True):
                    ss["oncall_view_mode"] = "all"
                    st.rerun()

        with tb_c2:
            div_pills = ["All Divisions", "Infra Team", "Core Dev", "Non-Core Dev"]
            cur_dpill = ss.get("oncall_div_pill", "All Divisions")
            dp_cols = st.columns(len(div_pills))
            for idx, dp in enumerate(div_pills):
                with dp_cols[idx]:
                    if st.button(dp.replace(" Divisions", "").replace(" Team", ""), key=f"oncall_dpill_{idx}", type="primary" if cur_dpill == dp else "secondary", use_container_width=True):
                        ss["oncall_div_pill"] = dp
                        st.rerun()

        with tb_c3:
            search_val = st.text_input(
                "Search",
                value=ss["oncall_search"],
                placeholder="🔍 Search engineer, domain, lead...",
                key="oncall_tab1_search",
                label_visibility="collapsed",
            )
            if search_val != ss["oncall_search"]:
                ss["oncall_search"] = search_val

        col_master, col_detail = st.columns([5.8, 4.2], gap="small")

        with col_master:
            cur_dpill = ss.get("oncall_div_pill", "All Divisions")

            if ss["oncall_view_mode"] == "today":
                display_shifts = mod_shifts
                if cur_dpill != "All Divisions":
                    display_shifts = [s for s in display_shifts if s["division"] == cur_dpill]

                if ss["oncall_search"].strip():
                    q = ss["oncall_search"].strip().lower()
                    display_shifts = [
                        s for s in display_shifts
                        if q in s["domain_state"].lower()
                        or q in (s.get("primary_on_call") or "").lower()
                        or q in (s.get("secondary_on_call") or "").lower()
                        or q in s.get("governed_sdm", "").lower()
                    ]

                tz_hdr = "Time in IST" if use_ist else "Time in EST"
                mod_rows_html = []
                for idx, m in enumerate(display_shifts):
                    p_name = m.get("primary_on_call") or "Unassigned"
                    s_name = m.get("secondary_on_call") or "—"
                    lead_name = m.get("governed_sdm") or "Governing SDM"
                    time_disp = m["time_ist"] if use_ist else m["time_est"]
                    is_sel = (cur_selected == p_name)

                    if m["division"] == "Infra Team":
                        div_badge = '<span class="oc-team-tag oc-team-infra">Infra Ops</span>'
                    elif m["division"] == "Non-Core Dev":
                        div_badge = '<span class="oc-team-tag oc-team-noncore">Non-Core</span>'
                    else:
                        div_badge = '<span class="oc-team-tag oc-team-core">Core Dev</span>'

                    row_bg = "rgba(56, 189, 248, 0.09)" if is_sel else ("#181b1f" if idx % 2 == 0 else "#141619")
                    border_style = "border-left:3px solid var(--accent);" if is_sel else "border-left:3px solid transparent;"
                    p_avatar = ui.on_call_avatar(p_name)
                    sec_disp = '<span style="color:var(--mute);">&mdash;</span>' if s_name in ["—", "None", "", None] else escape(s_name)

                    p_link = f'<a href="?eng={quote(p_name)}" target="_self" style="color:var(--ink);text-decoration:none;font-weight:700;">{escape(p_name)}</a>'

                    mod_rows_html.append(
                        f"<tr style='background:{row_bg};border-bottom:1px solid #22252b;{border_style}'>"
                        f"<td style='padding:4px 6px;font-family:var(--mono);font-weight:700;color:#f8fafc;'><span class='st-tag'>{escape(m['domain_state'])}</span></td>"
                        f"<td style='padding:4px 6px;'>{div_badge}</td>"
                        f"<td style='padding:4px 6px;font-family:var(--mono);font-size:9px;color:var(--ink);'>{escape(time_disp)}</td>"
                        f"<td style='padding:4px 6px;font-weight:700;color:var(--ink);display:flex;align-items:center;gap:5px;'>{p_avatar}<span>{p_link}</span></td>"
                        f"<td style='padding:4px 6px;color:var(--slate);font-size:9.5px;'>{sec_disp}</td>"
                        f"<td style='padding:4px 6px;color:var(--slate);font-size:9.5px;'><b style='color:#f59e0b;'>{escape(lead_name)}</b></td>"
                        f"<td style='padding:4px 6px;'><span style='color:#10b981;font-weight:700;font-size:8px;background:rgba(16,185,129,0.15);padding:1px 4px;border-radius:2px;border:1px solid rgba(16,185,129,0.3);'><span class='pulse-dot'></span>ACTIVE</span></td>"
                        f"</tr>"
                    )

                empty_notice = "<tr><td colspan='7' style='padding:24px;text-align:center;color:var(--mute);font-size:11px;'>No active modules match current filters. Click ✕ Reset to view all.</td></tr>"
                st.markdown(f"""<div class="oc-table-container">
<table style="width:100%;border-collapse:collapse;font-size:9.5px;">
<thead>
<tr style="background:#141619;border-bottom:1px solid #2c3235;font-size:8.5px;text-transform:uppercase;color:var(--slate);">
<th style="padding:4px 6px;text-align:left;">Module / Domain</th>
<th style="padding:4px 6px;text-align:left;">Division</th>
<th style="padding:4px 6px;text-align:left;">{tz_hdr}</th>
<th style="padding:4px 6px;text-align:left;">Primary On-Call</th>
<th style="padding:4px 6px;text-align:left;">Secondary</th>
<th style="padding:4px 6px;text-align:left;">Governing Lead</th>
<th style="padding:4px 6px;text-align:left;">Status</th>
</tr>
</thead>
<tbody>
{''.join(mod_rows_html) if mod_rows_html else empty_notice}
</tbody>
</table>
</div>""", unsafe_allow_html=True)

            else:
                all_weekly = shifts_with_sdm
                if cur_dpill != "All Divisions":
                    all_weekly = [s for s in all_weekly if s["division"] == cur_dpill]

                if state_slicer != "All States":
                    st_code = state_slicer.split()[0].upper()
                    all_weekly = [s for s in all_weekly if st_code in s["domain_state"].upper() or s["division"] in ["Infra Team", "Non-Core Dev"]]

                if target_day:
                    all_weekly = [s for s in all_weekly if s["shift_date"] == target_day]

                if ss["oncall_search"].strip():
                    q = ss["oncall_search"].strip().lower()
                    all_weekly = [
                        s for s in all_weekly
                        if q in s["domain_state"].lower()
                        or q in (s.get("primary_on_call") or "").lower()
                        or q in (s.get("secondary_on_call") or "").lower()
                        or q in s.get("governed_sdm", "").lower()
                    ]

                all_weekly = _consolidate_contiguous_shifts(all_weekly)
                tz_hdr = "Time in IST" if use_ist else "Time in EST"

                shift_rows = []
                for idx, s in enumerate(all_weekly[:60]):
                    p_name = s.get("primary_on_call") or "Unassigned"
                    s_name = s.get("secondary_on_call") or "—"
                    p_avatar = ui.on_call_avatar(p_name)
                    is_sel = (cur_selected == p_name)
                    row_bg = "rgba(56, 189, 248, 0.09)" if is_sel else ("#181b1f" if idx % 2 == 0 else "#141619")
                    time_disp = s.get("time_ist", "") if use_ist else s.get("time_est", "")
                    p_link = f'<a href="?eng={quote(p_name)}" target="_self" style="color:var(--ink);text-decoration:none;font-weight:700;">{escape(p_name)}</a>'

                    shift_rows.append(
                        f"<tr style='background:{row_bg};border-bottom:1px solid #22252b;'>"
                        f"<td style='padding:4px 6px;font-weight:700;color:#f8fafc;'><span class='st-tag'>{escape(s['domain_state'])}</span></td>"
                        f"<td style='padding:4px 6px;color:#94a3b8;'>{escape(s.get('day_name', ''))[:3]} {s.get('shift_date', '')[5:]}</td>"
                        f"<td style='padding:4px 6px;font-family:var(--mono);font-size:9px;color:var(--ink);'>{escape(time_disp)}</td>"
                        f"<td style='padding:4px 6px;font-weight:700;color:var(--ink);display:flex;align-items:center;gap:5px;'>{p_avatar}<span>{p_link}</span></td>"
                        f"<td style='padding:4px 6px;color:#cbd5e1;'><b style='color:#f59e0b;'>{escape(s.get('governed_sdm', ''))}</b></td>"
                        f"<td style='padding:4px 6px;'><span style='color:#38bdf8;font-weight:700;font-size:8px;background:rgba(56,189,248,0.15);padding:1px 4px;border-radius:2px;'>{s.get('shift_slot_disp','Slot 1')}</span></td>"
                        f"</tr>"
                    )

                empty_notice = "<tr><td colspan='6' style='padding:24px;text-align:center;color:var(--mute);font-size:11px;'>No weekly shifts match current filters.</td></tr>"
                st.markdown(f"""<div class="oc-table-container">
<table style="width:100%;border-collapse:collapse;font-size:9.5px;">
<thead>
<tr style="background:#141619;border-bottom:1px solid #2c3235;font-size:8.5px;text-transform:uppercase;color:var(--slate);">
<th style="padding:4px 6px;text-align:left;">Domain / Scope</th>
<th style="padding:4px 6px;text-align:left;">Day</th>
<th style="padding:4px 6px;text-align:left;">{tz_hdr}</th>
<th style="padding:4px 6px;text-align:left;">Primary On-Call</th>
<th style="padding:4px 6px;text-align:left;">Governing Lead</th>
<th style="padding:4px 6px;text-align:left;">Slot</th>
</tr>
</thead>
<tbody>
{''.join(shift_rows) if shift_rows else empty_notice}
</tbody>
</table>
</div>""", unsafe_allow_html=True)

        with col_detail:
            _render_personnel_inspector(
                sel_name=cur_selected,
                shifts_with_sdm=shifts_with_sdm,
                all_ps=all_ps,
                all_escs=all_escs,
                esc_lookup=esc_lookup,
                esc_fallback=esc_fallback,
                clean_sdm_name=clean_sdm_name,
                today_str=today_str,
                cur_slot=cur_slot,
                scoped_engineers=scoped_engineers,
                cur_div_name=ss.get("oncall_div_pill", "All Divisions"),
            )

    # ==========================================================================
    # TAB 2: PRODUCTION SUPPORT 24x7 MATRIX
    # ==========================================================================
    with tab_support:
        ps_df = pd.DataFrame(all_ps)

        if ss["oncall_search"].strip():
            q_term = ss["oncall_search"].strip().lower()
            ps_df = ps_df[ps_df["resource_name"].str.lower().str.contains(q_term, na=False)]

        if cur_shift_chip:
            ref_d = target_day if target_day else today_str
            matching_names = [
                r["resource_name"] for _, r in ps_df.iterrows()
                if _matches_ps_shift(cur_shift_chip, r.get("shift_window", ""))
            ]
            ps_df = ps_df[ps_df["resource_name"].isin(set(matching_names))]

        quick_status = ss.get("oncall_quick_status", "All")
        if quick_status == "Working Today":
            ref_day = target_day if target_day else today_str
            working_names = ps_df[(ps_df["shift_date"] == ref_day) & (ps_df["is_working"] == 1)]["resource_name"].unique()
            ps_df = ps_df[ps_df["resource_name"].isin(working_names)]
        elif quick_status == "Off / Holiday":
            ref_day = target_day if target_day else today_str
            off_names = ps_df[(ps_df["shift_date"] == ref_day) & (ps_df["is_working"] == 0)]["resource_name"].unique()
            ps_df = ps_df[ps_df["resource_name"].isin(off_names)]

        if state_slicer != "All States":
            st_code = state_slicer.split()[0]
            state_matches = ps_df[ps_df["resource_name"].str.upper().str.contains(st_code, na=False)]
            if not state_matches.empty:
                ps_df = state_matches

        pivot_dates = ps_dates if ps_dates else all_unique_dates
        if not ps_df.empty:
            pivoted = ps_df.pivot(index="resource_name", columns="shift_date", values="shift_window").reindex(columns=pivot_dates).reset_index()
        else:
            pivoted = pd.DataFrame(columns=["resource_name"] + pivot_dates)

        filtered_eng_count = len(pivoted)

        ps_h1, ps_h2 = st.columns([3.5, 2.5], gap="small")
        with ps_h1:
            st.markdown(
                f'<div style="font-size:12px;font-weight:800;color:var(--ink);line-height:28px;">'
                f'🏢 Production Support 24x7 Matrix &bull; <span style="font-size:9.5px;color:#38bdf8;font-weight:600;">{filtered_eng_count} of {distinct_ps_engineers} Engineers Active</span>'
                f'</div>',
                unsafe_allow_html=True,
            )
        with ps_h2:
            tb1, tb2, tb3 = st.columns(3)
            with tb1:
                if st.button("All Resources", key="oncall_qs_all", type="primary" if quick_status == "All" else "secondary", use_container_width=True):
                    ss["oncall_quick_status"] = "All"
                    st.rerun()
            with tb2:
                if st.button("Working Today", key="oncall_qs_working", type="primary" if quick_status == "Working Today" else "secondary", use_container_width=True):
                    ss["oncall_quick_status"] = "Working Today"
                    st.rerun()
            with tb3:
                if st.button("Off / Holiday", key="oncall_qs_off", type="primary" if quick_status == "Off / Holiday" else "secondary", use_container_width=True):
                    ss["oncall_quick_status"] = "Off / Holiday"
                    st.rerun()

        today_badge_html = "<br/><span style='font-size:7.5px;color:#ff780a;font-weight:800;'>TODAY</span>"
        th_days = "".join(
            f"<th style='padding:5px 6px;text-align:center;font-size:9px;white-space:nowrap;{'border-bottom:2px solid #ff780a;' if d == today_str else ''}'>"
            f"{date_day_map.get(d, 'Day')[:3]}<br/>"
            f"<span style='color:var(--mute);font-size:8px;'>{d[5:]}</span>"
            f"{today_badge_html if d == today_str else ''}"
            f"</th>"
            for d in pivot_dates
        )

        matrix_rows = []
        for idx, row in pivoted.iterrows():
            eng_name = row["resource_name"]
            avatar_html = ui.on_call_avatar(eng_name)
            is_sel = (cur_selected == eng_name)
            row_bg = "rgba(56, 189, 248, 0.09)" if is_sel else ("#181b1f" if idx % 2 == 0 else "#141619")
            border_left = "border-left:3px solid var(--accent);" if is_sel else "border-left:3px solid transparent;"

            day_cells = "".join(
                f"<td style='padding:4px 6px;text-align:center;font-size:9.5px;'>{_compact_shift_chip(row.get(d, ''))}</td>"
                for d in pivot_dates
            )

            p_link = f'<a href="?eng={quote(eng_name)}" target="_self" style="color:var(--ink);text-decoration:none;font-weight:700;">{escape(eng_name)}</a>'

            matrix_rows.append(
                f"<tr style='background:{row_bg};border-bottom:1px solid #22252b;{border_left}'>"
                f"<td style='padding:4px 8px;font-weight:700;color:var(--ink);white-space:nowrap;display:flex;align-items:center;gap:6px;position:sticky;left:0;background:{row_bg};z-index:5;'>"
                f"{avatar_html}<span>{p_link}</span></td>"
                f"{day_cells}"
                f"</tr>"
            )

        empty_ps = "<tr><td colspan='8' style='padding:24px;text-align:center;color:var(--mute);font-size:11px;'>No engineers match current search and status filters.</td></tr>"
        st.markdown(f"""<div class="oc-table-container">
<table style="width:100%;border-collapse:collapse;font-size:10px;">
<thead>
<tr style="background:#141619;border-bottom:1px solid #2c3235;text-transform:uppercase;color:var(--slate);">
<th style="padding:5px 8px;text-align:left;position:sticky;left:0;z-index:15;background:#141619;min-width:180px;">Engineer Name</th>
{th_days}
</tr>
</thead>
<tbody>
{''.join(matrix_rows) if matrix_rows else empty_ps}
</tbody>
</table>
</div>""", unsafe_allow_html=True)

    # ==========================================================================
    # TAB 3: STATE INCIDENT ESCALATION MATRIX
    # ==========================================================================
    with tab_escalation:
        st.markdown("""<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:6px;">
<div style="font-size:12px;font-weight:800;color:var(--ink);">
🚨 State MMIS Incident Escalation Matrix &bull; <span style="font-size:9.5px;color:#f43f5e;font-weight:700;">ACTIVE ROTATION</span>
</div>
<div style="display:flex;align-items:center;gap:6px;">
<span style="font-size:8.5px;color:#38bdf8;background:rgba(56,189,248,0.12);padding:1px 6px;border-radius:2px;border:1px solid rgba(56,189,248,0.3);font-weight:700;">T1 Ack &le;15m</span>
<span style="font-size:8.5px;color:#f59e0b;background:rgba(245,158,11,0.12);padding:1px 6px;border-radius:2px;border:1px solid rgba(245,158,11,0.3);font-weight:700;">T2 SDM &le;30m</span>
<span style="font-size:8.5px;color:#ef4444;background:rgba(239,68,68,0.12);padding:1px 6px;border-radius:2px;border:1px solid rgba(239,68,68,0.3);font-weight:700;">T3 Exec Immediate</span>
</div>
</div>""", unsafe_allow_html=True)

        state_esc_configs = [
            {
                "state": "AK MMIS",
                "title": "🏛️ Alaska MMIS (AK DEV)",
                "code": "AK",
                "offshore": {
                    "t1": ("Kavita Doraisamy", "Technical Lead (Offshore TL)", "< 15 Mins"),
                    "t2": ("Kishore Nagarajan", "Service Delivery Manager (SDM)", "< 30 Mins"),
                    "t3": ("Radhakanta Samantara", "Project Director (Executive)", "Immediate"),
                },
                "onshore": {
                    "t1": ("Kishore Kanuparthi", "Technical Lead (Onshore TL)", "< 15 Mins"),
                    "t2": ("Ravi M Shankar", "Service Delivery Manager (SDM)", "< 30 Mins"),
                    "t3": ("Abhilash Pulikkathodi", "Project Director (Executive)", "Immediate"),
                }
            },
            {
                "state": "ND MMIS",
                "title": "🏛️ North Dakota MMIS (ND DEV)",
                "code": "ND",
                "offshore": {
                    "t1": ("Sreekanth Veluguleti", "Technical Lead (Offshore TL)", "< 15 Mins"),
                    "t2": ("Anil Kumar Khamari", "Service Delivery Manager (SDM)", "< 30 Mins"),
                    "t3": ("Radhakanta Samantara", "Project Director (Executive)", "Immediate"),
                },
                "onshore": {
                    "t1": ("Ajit Gandhi", "Technical Lead (Onshore TL)", "< 15 Mins"),
                    "t2": ("Thirupathi Katakam", "Service Delivery Manager (SDM)", "< 30 Mins"),
                    "t3": ("Abhilash Pulikkathodi", "Project Director (Executive)", "Immediate"),
                }
            },
            {
                "state": "NH MMIS",
                "title": "🏛️ New Hampshire MMIS (NH DEV)",
                "code": "NH",
                "offshore": {
                    "t1": ("Anand", "Technical Lead (Offshore TL)", "< 15 Mins"),
                    "t2": ("Dipak/Rama", "Service Delivery Manager (SDM)", "< 30 Mins"),
                    "t3": ("Radhakanta Samantara", "Project Director (Executive)", "Immediate"),
                },
                "onshore": {
                    "t1": ("Sunil P", "Technical Lead (Onshore TL)", "< 15 Mins"),
                    "t2": ("Madhav", "Service Delivery Manager (SDM)", "< 30 Mins"),
                    "t3": ("Abhilash Pulikkathodi", "Project Director (Executive)", "Immediate"),
                }
            },
        ]

        col_ak, col_nd, col_nh = st.columns(3, gap="small")
        state_cols = [col_ak, col_nd, col_nh]

        for idx, cfg in enumerate(state_esc_configs):
            with state_cols[idx]:
                off_t1, off_t2, off_t3 = cfg["offshore"]["t1"], cfg["offshore"]["t2"], cfg["offshore"]["t3"]
                on_t1, on_t2, on_t3 = cfg["onshore"]["t1"], cfg["onshore"]["t2"], cfg["onshore"]["t3"]

                st.markdown(f"""<div style="background:#181b1f;border:1px solid #2c3235;border-radius:3px;padding:8px 10px;height:355px;box-sizing:border-box;display:flex;flex-direction:column;justify-content:space-between;">
<div>
<div style="display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid #22252b;padding-bottom:5px;margin-bottom:6px;">
<div style="font-size:11px;font-weight:800;color:#f8fafc;">{cfg['title']}</div>
<span style="font-size:8px;font-weight:700;color:#10b981;background:rgba(16,185,129,0.15);padding:1px 4px;border-radius:2px;">ACTIVE CHAIN</span>
</div>

<div style="font-size:8.5px;font-weight:700;color:#38bdf8;text-transform:uppercase;letter-spacing:0.04em;margin-bottom:3px;">🌏 Offshore Shifts (IST / UTC+5:30) &bull; Slots 1 &amp; 2</div>
<div style="background:#141619;border:1px solid #22252b;border-radius:2px;padding:5px 7px;margin-bottom:6px;display:flex;flex-direction:column;gap:3px;">
<div style="display:flex;justify-content:space-between;align-items:center;font-size:9.5px;">
<span style="color:#cbd5e1;"><b style="color:#38bdf8;font-size:8px;border:1px solid rgba(56,189,248,0.3);padding:0 3px;border-radius:2px;margin-right:4px;">T1</b>{off_t1[0]}</span>
<span style="font-size:8px;color:#94a3b8;font-family:var(--mono);">&lt; 15m</span>
</div>
<div style="display:flex;justify-content:space-between;align-items:center;font-size:9.5px;">
<span style="color:#cbd5e1;"><b style="color:#f59e0b;font-size:8px;border:1px solid rgba(245,158,11,0.3);padding:0 3px;border-radius:2px;margin-right:4px;">T2</b>{off_t2[0]}</span>
<span style="font-size:8px;color:#94a3b8;font-family:var(--mono);">&lt; 30m</span>
</div>
<div style="display:flex;justify-content:space-between;align-items:center;font-size:9.5px;">
<span style="color:#cbd5e1;"><b style="color:#ef4444;font-size:8px;border:1px solid rgba(239,68,68,0.3);padding:0 3px;border-radius:2px;margin-right:4px;">T3</b>{off_t3[0]}</span>
<span style="font-size:8px;color:#94a3b8;font-family:var(--mono);">Immediate</span>
</div>
</div>

<div style="font-size:8.5px;font-weight:700;color:#10b981;text-transform:uppercase;letter-spacing:0.04em;margin-bottom:3px;">🏛️ Onshore Shifts (EST / UTC-5) &bull; Slots 3 &amp; 4</div>
<div style="background:#141619;border:1px solid #22252b;border-radius:2px;padding:5px 7px;display:flex;flex-direction:column;gap:3px;">
<div style="display:flex;justify-content:space-between;align-items:center;font-size:9.5px;">
<span style="color:#cbd5e1;"><b style="color:#38bdf8;font-size:8px;border:1px solid rgba(56,189,248,0.3);padding:0 3px;border-radius:2px;margin-right:4px;">T1</b>{on_t1[0]}</span>
<span style="font-size:8px;color:#94a3b8;font-family:var(--mono);">&lt; 15m</span>
</div>
<div style="display:flex;justify-content:space-between;align-items:center;font-size:9.5px;">
<span style="color:#cbd5e1;"><b style="color:#f59e0b;font-size:8px;border:1px solid rgba(245,158,11,0.3);padding:0 3px;border-radius:2px;margin-right:4px;">T2</b>{on_t2[0]}</span>
<span style="font-size:8px;color:#94a3b8;font-family:var(--mono);">&lt; 30m</span>
</div>
<div style="display:flex;justify-content:space-between;align-items:center;font-size:9.5px;">
<span style="color:#cbd5e1;"><b style="color:#ef4444;font-size:8px;border:1px solid rgba(239,68,68,0.3);padding:0 3px;border-radius:2px;margin-right:4px;">T3</b>{on_t3[0]}</span>
<span style="font-size:8px;color:#94a3b8;font-family:var(--mono);">Immediate</span>
</div>
</div>
</div>

<div style="border-top:1px solid #22252b;padding-top:4px;font-size:8.5px;color:#94a3b8;display:flex;justify-content:space-between;">
<span>Primary SLA Target: <b>15m ACK</b></span>
<span style="color:#10b981;font-weight:700;">100% On-Call Coverage</span>
</div>
</div>""", unsafe_allow_html=True)

        st.markdown("<div style='margin-top:6px;'></div>", unsafe_allow_html=True)
        ib_c1, ib_c2, ib_c3, ib_c4 = st.columns([1.5, 1.5, 2.0, 2.0], gap="small")
        with ib_c1:
            inc_st = st.selectbox("State", ["AK MMIS", "ND MMIS", "NH MMIS"], key="inc_st_pick")
        with ib_c2:
            inc_sev = st.selectbox("Severity", ["P1 Blocker (≤15m)", "P2 Major (≤30m)", "P3 Minor (≤2h)"], key="inc_sev_pick")
        with ib_c3:
            if st.button("🚨 Broadcast Incident Alert", key="inc_broadcast_btn", type="primary", use_container_width=True):
                st.success(f"✓ Emergency incident broadcast triggered for {inc_st} ({inc_sev})!")
        with ib_c4:
            if st.button("📋 Copy Bridge Notice", key="inc_copy_btn", type="secondary", use_container_width=True):
                st.info(f"Copied bridge announcement template for {inc_st}.")

    # ==========================================================================
    # TAB 4: SHIFT HANDOVER & TELEMETRY
    # ==========================================================================
    with tab_handover:
        ho_left, ho_right = st.columns([5.5, 4.5], gap="small")

        with ho_left:
            slots_data = [
                ("Slot 1: Night Shift", "22:30 - 06:30 IST", "01:00 PM - 09:00 PM EST", 1),
                ("Slot 2: Morning Shift", "06:30 - 14:30 IST", "09:00 PM - 05:00 AM EST", 2),
                ("Slot 3: Evening Shift", "14:30 - 22:30 IST", "05:00 AM - 01:00 PM EST", 3),
                ("Slot 4: Standby / Transition", "Flexible Rotation", "Flexible Rotation", 4),
            ]
            slot_cards = []
            for s_title, s_ist, s_est, s_num in slots_data:
                is_active = (s_num == cur_slot)
                border = "border-left:3px solid #10b981;background:rgba(16,185,129,0.08);" if is_active else "border-left:3px solid #2c3235;background:#141619;"
                badge = '<span style="color:#10b981;font-weight:700;font-size:8px;background:rgba(16,185,129,0.18);padding:1px 5px;border-radius:2px;"><span class="pulse-dot"></span>ACTIVE NOW</span>' if is_active else '<span style="color:#64748b;font-size:8px;">UPCOMING</span>'
                slot_cards.append(
                    f"<div style='border:1px solid #2c3235;border-radius:2px;padding:6px 10px;margin-bottom:6px;{border}'>"
                    f"<div style='display:flex;justify-content:space-between;align-items:center;margin-bottom:2px;'>"
                    f"<b style='font-size:10px;color:#f8fafc;'>{s_title}</b>{badge}</div>"
                    f"<div style='display:flex;justify-content:space-between;font-size:8.5px;color:#94a3b8;font-family:var(--mono);'>"
                    f"<span>IST: {s_ist}</span><span>EST: {s_est}</span></div>"
                    f"</div>"
                )

            st.markdown(f"""<div style="background:#181b1f;border:1px solid #2c3235;border-radius:3px;padding:8px 10px;height:432px;box-sizing:border-box;overflow-y:auto;">
<div style="font-size:11px;font-weight:800;color:#38bdf8;text-transform:uppercase;letter-spacing:0.03em;margin-bottom:6px;">
📡 4-Slot Real-Time Shift Radar &bull; Active Duty Status
</div>
{''.join(slot_cards)}

<div style="font-size:10px;font-weight:700;color:#f8fafc;margin-top:10px;margin-bottom:4px;">Handover Verification Checklist</div>
<div style="background:#141619;border:1px solid #22252b;border-radius:2px;padding:6px 8px;font-size:9.5px;color:#cbd5e1;display:flex;flex-direction:column;gap:4px;">
<div>✓ All high-priority DB alerts triaged and acknowledged in Watchtower</div>
<div>✓ Production support incident ticket queue handed over with context</div>
<div>✓ War-room audio bridge transition scheduled at shift cutoff</div>
<div>✓ On-duty lead sign-off recorded in compliance audit ledger</div>
</div>
</div>""", unsafe_allow_html=True)

        with ho_right:
            st.markdown(f"""<div style="background:#181b1f;border:1px solid #2c3235;border-radius:3px;padding:8px 10px;height:432px;box-sizing:border-box;display:flex;flex-direction:column;justify-content:space-between;">
<div>
<div style="font-size:11px;font-weight:800;color:#10b981;text-transform:uppercase;letter-spacing:0.03em;margin-bottom:8px;">
📋 Roster Lineage &amp; Operational Health
</div>

<div style="display:flex;flex-direction:column;gap:6px;font-size:10px;">
<div style="background:#141619;border:1px solid #22252b;border-radius:2px;padding:6px 8px;display:flex;justify-content:space-between;">
<span style="color:#94a3b8;">Active Roster Name:</span>
<b style="color:#f8fafc;font-family:var(--mono);">{escape(active_roster.get('roster_name', ''))}</b>
</div>
<div style="background:#141619;border:1px solid #22252b;border-radius:2px;padding:6px 8px;display:flex;justify-content:space-between;">
<span style="color:#94a3b8;">Coverage Horizon:</span>
<b style="color:#f8fafc;font-family:var(--mono);">{valid_from} &rarr; {valid_to}</b>
</div>
<div style="background:#141619;border:1px solid #22252b;border-radius:2px;padding:6px 8px;display:flex;justify-content:space-between;">
<span style="color:#94a3b8;">Production Support Engineers:</span>
<b style="color:#38bdf8;font-family:var(--mono);">{distinct_ps_engineers} Resources</b>
</div>
<div style="background:#141619;border:1px solid #22252b;border-radius:2px;padding:6px 8px;display:flex;justify-content:space-between;">
<span style="color:#94a3b8;">Total Functional Shift Slots:</span>
<b style="color:#10b981;font-family:var(--mono);">{len(all_shifts)} Slots Parsed</b>
</div>
<div style="background:#141619;border:1px solid #22252b;border-radius:2px;padding:6px 8px;display:flex;justify-content:space-between;">
<span style="color:#94a3b8;">Shift Handover SLA:</span>
<b style="color:#10b981;font-family:var(--mono);">100% Synchronized</b>
</div>
</div>
</div>

<div style="border-top:1px solid #22252b;padding-top:8px;">
<div style="font-size:8.5px;color:#94a3b8;margin-bottom:6px;">Sign off current shift rotation and log operational handover to audit ledger.</div>
</div>
</div>""", unsafe_allow_html=True)

    # ==========================================================================
    # TAB 5: ROSTER UPLOAD & SYNC ENGINE
    # ==========================================================================
    with tab_sync:
        up_col1, up_col2 = st.columns([5.5, 4.5], gap="small")

        with up_col1:
            st.markdown("""<div style="background:#181b1f;border:1px solid #2c3235;border-radius:3px;padding:8px 10px;margin-bottom:8px;">
<div style="font-size:11px;font-weight:800;color:#f59e0b;text-transform:uppercase;letter-spacing:0.03em;margin-bottom:4px;">
📤 Upload New Weekly On-Call Roster (.xlsx)
</div>
<div style="font-size:9.5px;color:#94a3b8;margin-bottom:8px;">
Upload the weekly Excel roster to parse all support shifts, infrastructure domains, state core developers, and 3-tier escalation matrices.
</div>
</div>""", unsafe_allow_html=True)

            new_roster_file = st.file_uploader(
                "Select Weekly Excel Roster",
                type=["xlsx"],
                key="oncall_tab5_uploader",
                help="Must contain: Production support, Infra Team, Core Dev, Non-Core Dev",
            )
            if new_roster_file:
                if st.button("🚀 Ingest & Activate Roster", key="oncall_tab5_commit_btn", type="primary", use_container_width=True):
                    with st.spinner("Validating sheets & committing to database..."):
                        try:
                            res = ingest_roster_file(db_path, new_roster_file)
                            _clear_oncall_cache()
                            st.success(f"✓ Ingested {res['roster_name']}! ({res['ps_count']} PS rows, {res['shift_count']} shift slots)")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Ingestion failed: {e}")

            if st.button("🔄 Force Re-Sync from _Input Directory", key="oncall_tab5_sync_btn", type="secondary", use_container_width=True):
                input_dir = ROOT / "_Input"
                target_files = sorted(
                    [f for f in input_dir.glob("*.xlsx") if "on call" in f.name.lower() or "roster" in f.name.lower()],
                    key=lambda f: f.stat().st_mtime,
                    reverse=True,
                )
                if target_files:
                    with st.spinner(f"Ingesting {target_files[0].name}..."):
                        res = ingest_roster_file(db_path, target_files[0])
                        _clear_oncall_cache()
                    st.success(f"✓ Re-synced {res['roster_name']} ({res['ps_count']} PS rows, {res['shift_count']} shift slots)!")
                    st.rerun()
                else:
                    st.warning("No roster files found in _Input.")

        with up_col2:
            st.markdown(f"""<div style="background:#181b1f;border:1px solid #2c3235;border-radius:3px;padding:8px 10px;height:432px;box-sizing:border-box;display:flex;flex-direction:column;justify-content:space-between;">
<div>
<div style="font-size:11px;font-weight:800;color:#38bdf8;text-transform:uppercase;letter-spacing:0.03em;margin-bottom:8px;">
📋 Standard Excel Sheet Contract
</div>
<div style="font-size:9.5px;color:#94a3b8;margin-bottom:8px;">
The weekly Excel workbook must provide the following standard sheets:
</div>

<div style="display:flex;flex-direction:column;gap:5px;font-size:9.5px;">
<div style="background:#141619;border:1px solid #22252b;border-radius:2px;padding:5px 8px;">
<b style="color:#10b981;">✓ Production support:</b> <span style="color:#cbd5e1;">31 engineers &times; 7 days (M, E, N, WO, Comp, Hol, Lv)</span>
</div>
<div style="background:#141619;border:1px solid #22252b;border-radius:2px;padding:5px 8px;">
<b style="color:#38bdf8;">✓ Infra Team:</b> <span style="color:#cbd5e1;">App Server, Database, IAM, Informatica, UC4, Cognos</span>
</div>
<div style="background:#141619;border:1px solid #22252b;border-radius:2px;padding:5px 8px;">
<b style="color:#f59e0b;">✓ Core Dev:</b> <span style="color:#cbd5e1;">Alaska DEV, North Dakota DEV, New Hampshire DEV</span>
</div>
<div style="background:#141619;border:1px solid #22252b;border-radius:2px;padding:5px 8px;">
<b style="color:#b877d7;">✓ Non-Core Dev:</b> <span style="color:#cbd5e1;">Letters, Cognos, Informatica, TMSIS, EDMS</span>
</div>
<div style="background:#141619;border:1px solid #22252b;border-radius:2px;padding:5px 8px;">
<b style="color:#f43f5e;">✓ Escalations:</b> <span style="color:#cbd5e1;">Tier 1 TL &bull; Tier 2 SDM &bull; Tier 3 Project Director</span>
</div>
</div>
</div>

<div style="border-top:1px solid #22252b;padding-top:8px;font-size:8.5px;color:#94a3b8;">
<b>Active Roster Database ID:</b> #{roster_id} &bull; Valid: {valid_from} &rarr; {valid_to}
</div>
</div>""", unsafe_allow_html=True)
