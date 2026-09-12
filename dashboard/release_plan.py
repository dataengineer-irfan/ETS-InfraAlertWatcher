"""
release_plan.py
===============
Enterprise Schedule Release Plan & Environment Pipeline Workspace.
Strictly follows Grafana Flat Design System tokens:
  - 2px sharp corners across all cards, panels, and chips
  - 1px #2c3235 borders, #181b1f panel backgrounds, #141619 sunken tiles
  - Grafana stat cards (ui.grafana_stat_card) with sparklines and donut visual
  - Grafana alert chips (.alert-chip.firing, .alert-chip.pending, .alert-chip.ok)
  - Monospace tabular dates and telemetry
  - Zero narrative banners; silent RBAC isolation under the hood
  - Interactive Master-Detail Release Command Center utilizing SQLite release_milestones
"""

from __future__ import annotations

import json
from datetime import datetime, date
import pandas as pd
import streamlit as st
@st.cache_data(ttl=600, show_spinner=False)
def get_cached_release_schedules(db_path: str, state: str | None = None) -> list[dict]:
    conn = get_connection(db_path)
    res = get_release_schedules(conn, state=state)
    conn.close()
    return res


import ui
from db import (
    get_connection,
    get_release_schedules,
    get_release_milestones,
)


def render_html(html_str: str) -> None:
    """Render HTML safely without markdown 4-space code-block escaping."""
    cleaned = "\n".join(line.strip() for line in html_str.splitlines() if line.strip())
    st.markdown(cleaned, unsafe_allow_html=True)


def _days_between(d1: str | None, d2: str) -> int | None:
    """Return integer days between date strings. Positive = d1 is in future."""
    if not d1:
        return None
    try:
        return (datetime.strptime(d1, "%Y-%m-%d").date() - datetime.strptime(d2, "%Y-%m-%d").date()).days
    except Exception:
        return None


def _calc_stage_pill(s_date: str | None, f_date: str | None, now_iso: str) -> tuple[str, str, str, str]:
    """Returns (label, bg, color, border)."""
    if not s_date and not f_date:
        return "SCHEDULED", "rgba(255,255,255,0.04)", "var(--mute)", "var(--rule)"
    eff_start = s_date or f_date
    eff_finish = f_date or s_date
    if eff_finish < now_iso:
        return "COMPLETED", "rgba(115,191,105,0.18)", "#73bf69", "rgba(115,191,105,0.4)"
    elif eff_start <= now_iso <= eff_finish:
        return "ACTIVE TODAY", "rgba(242,73,92,0.2)", "#f2495c", "#f2495c"
    else:
        return "SCHEDULED", "rgba(255,255,255,0.04)", "var(--mute)", "var(--rule)"


def _get_stage_info(r: dict, now_iso: str) -> tuple[str, str, str]:
    """Returns (stage_name, env_tag, badge_status)."""
    p_d = r.get("prod_deploy_date")
    uat_s, uat_f = r.get("uat_start_date"), r.get("uat_end_date")
    sit_s, sit_f = r.get("sit_start_date"), r.get("sit_end_date")
    dev_s, dev_f = r.get("dev_start_date"), r.get("dev_end_date")
    gn_d = r.get("go_nogo_date")
    st_code = r.get("state", "")

    if p_d and now_iso == p_d:
        env = "ENV05 (Live)" if st_code == "NH" else "PROD (Live)"
        return "Production Cutover", env, "CUTOVER TODAY"
    elif p_d and now_iso > p_d:
        return "Production Deployed", "PROD", "COMPLETED"
    elif gn_d and now_iso == gn_d:
        return "Go / No-Go Decision Gate", "Board Gate", "ACTIVE TODAY"
    elif uat_s and uat_f and uat_s <= now_iso <= uat_f:
        env = "ENV04" if st_code == "NH" else "State Acceptance"
        return "State UAT Acceptance", env, "IN PROGRESS"
    elif sit_s and sit_f and sit_s <= now_iso <= sit_f:
        env = "ENV57 / ENV53" if st_code == "NH" else "SIT QA"
        return "SIT & Regression", env, "IN PROGRESS"
    elif dev_s and dev_f and dev_s <= now_iso <= dev_f:
        env = "ENV52" if st_code == "NH" else "Build-76"
        return "Development & Build", env, "IN PROGRESS"
    elif p_d and now_iso < p_d:
        if uat_f and now_iso > uat_f:
            return "Go / No-Go Decision Gate", "Board Gate", "PENDING"
        elif sit_f and now_iso > sit_f:
            env = "ENV04" if st_code == "NH" else "State Acceptance"
            return "State UAT Acceptance", env, "SCHEDULED"
        elif dev_f and now_iso > dev_f:
            env = "ENV57 / ENV53" if st_code == "NH" else "SIT QA"
            return "SIT & Regression", env, "SCHEDULED"
        else:
            env = "ENV52" if st_code == "NH" else "Build-76"
            return "Development & Build", env, "SCHEDULED"
    return "Scheduled Roadmap", "Pipeline", "SCHEDULED"


def _build_alert_chips(releases: list[dict], now_iso: str) -> list[dict]:
    """
    Scan all releases and return list of cutoff alert dicts:
    { release_id, state, phase, date, days, chip_cls, label }
    """
    alerts = []
    for r in releases:
        rid = r.get("release_id", "")
        st_code = r.get("state", "")
        p_d = r.get("prod_deploy_date")

        # Skip already-deployed
        if p_d and p_d < now_iso:
            continue

        dev_s = r.get("dev_start_date")
        dev_f = r.get("dev_end_date")
        sit_s = r.get("sit_start_date")
        sit_f = r.get("sit_end_date")
        uat_s = r.get("uat_start_date")
        uat_f = r.get("uat_end_date")
        gn_d = r.get("go_nogo_date")

        # DEV freeze
        if dev_f and dev_f >= now_iso:
            d = _days_between(dev_f, now_iso)
            if d is not None:
                if d < 0:
                    alerts.append({"release_id": rid, "state": st_code, "phase": "DEV FREEZE",
                                    "date": dev_f, "days": d, "chip_cls": "firing", "label": f"DEV FREEZE OVERDUE {abs(d)}d"})
                elif d <= 7:
                    alerts.append({"release_id": rid, "state": st_code, "phase": "DEV FREEZE",
                                    "date": dev_f, "days": d, "chip_cls": "pending",
                                    "label": f"DEV FREEZE IN {d}d" if d > 0 else "DEV FREEZE TODAY"})
        elif dev_f and dev_f < now_iso and (not sit_s or sit_s > now_iso):
            # Overdue only if next phase hasn't started yet
            pass # Simplified: if it's past dev_f but they didn't update dates, it could be overdue, but let's avoid false positives

        # SIT gate
        if sit_f and sit_f >= now_iso:
            d = _days_between(sit_f, now_iso)
            if d is not None:
                if d <= 7:
                    alerts.append({"release_id": rid, "state": st_code, "phase": "SIT GATE",
                                    "date": sit_f, "days": d, "chip_cls": "pending",
                                    "label": f"SIT GATE IN {d}d" if d > 0 else "SIT GATE TODAY"})

        # UAT gate
        if uat_f and uat_f >= now_iso:
            d = _days_between(uat_f, now_iso)
            if d is not None:
                if d <= 7:
                    alerts.append({"release_id": rid, "state": st_code, "phase": "UAT GATE",
                                    "date": uat_f, "days": d, "chip_cls": "pending",
                                    "label": f"UAT GATE IN {d}d" if d > 0 else "UAT GATE TODAY"})

        # Go/NoGo
        if gn_d and gn_d >= now_iso:
            d = _days_between(gn_d, now_iso)
            if d is not None:
                if d <= 2:
                    alerts.append({"release_id": rid, "state": st_code, "phase": "GO/NOGO",
                                    "date": gn_d, "days": d, "chip_cls": "firing",
                                    "label": f"GO/NOGO IN {d}d" if d > 0 else "GO/NOGO TODAY"})

        # PROD cutover
        if p_d and p_d >= now_iso:
            d = _days_between(p_d, now_iso)
            if d is not None:
                if d == 0:
                    alerts.append({"release_id": rid, "state": st_code, "phase": "CUTOVER",
                                    "date": p_d, "days": 0, "chip_cls": "firing", "label": "CUTOVER TODAY"})
                elif d <= 3:
                    alerts.append({"release_id": rid, "state": st_code, "phase": "CUTOVER",
                                    "date": p_d, "days": d, "chip_cls": "firing", "label": f"CUTOVER IN {d}d"})

    return alerts


def render_release_plan_workspace(db_path: str) -> None:
    """Render Grafana-grade Schedule Release Plan workspace."""
    conn = get_connection(db_path)
    now_iso = datetime.now().strftime("%Y-%m-%d")

    # --------------------------------------------------------------------------
    # 1. Silent RBAC Identification (Under-the-Hood)
    # --------------------------------------------------------------------------
    active_user = st.session_state.get("active_user") or "admin"
    user_role = st.session_state.get("user_role") or "Admin"
    user_assigned_state = st.session_state.get("assigned_state")

    if not user_assigned_state:
        u_low = active_user.lower()
        if "ak" in u_low:
            user_assigned_state = "AK"
        elif "nd" in u_low:
            user_assigned_state = "ND"
        elif "nh" in u_low:
            user_assigned_state = "NH"

    is_enterprise_admin = (user_role == "Admin" and user_assigned_state is None)

    # --------------------------------------------------------------------------
    # 2. Header & State Selection (Grafana Style: 2px sharp, thin border, live telemetry)
    # --------------------------------------------------------------------------
    h_col1, h_col2 = st.columns([3.0, 2.0])
    with h_col1:
        render_html("""
        <div style="display:flex;align-items:center;gap:10px;padding:3px 0 6px 0;border-left:3px solid var(--accent);padding-left:8px;">
          <div>
            <div style="font-size:16px;font-weight:800;color:var(--ink);letter-spacing:0.02em;text-transform:uppercase;">
              Release Schedule
            </div>
            <div style="font-size:10.5px;color:var(--slate);margin-top:2px;">
              Enterprise Milestones &amp; Pipeline Health
            </div>
          </div>
        </div>
        """)

    with h_col2:
        if is_enterprise_admin:
            state_options = ["All States", "Alaska (AK)", "North Dakota (ND)", "New Hampshire (NH)"]
            chosen_state_label = st.selectbox("Filter State", state_options, index=0, key="sl_state_clean", label_visibility="collapsed")
            if "Alaska" in chosen_state_label:
                effective_state = "AK"
            elif "North Dakota" in chosen_state_label:
                effective_state = "ND"
            elif "New Hampshire" in chosen_state_label:
                effective_state = "NH"
            else:
                effective_state = None
        else:
            effective_state = user_assigned_state

    # Query all releases for effective scope
    all_releases = get_cached_release_schedules(db_path, state=effective_state)

    if not all_releases:
        st.info("No release schedule records found.")
        conn.close()
        return

    # --------------------------------------------------------------------------
    # 3. Categorize into: Current, Active, Upcoming, Completed
    # --------------------------------------------------------------------------
    current_releases = []
    upcoming_releases = []
    completed_releases = []
    
    # Sort all by prod_deploy_date
    all_releases.sort(key=lambda x: x.get("prod_deploy_date", "9999-12-31"))
    
    # We want one "Current" release per state.
    # Current = The release currently in dev phase (dev_start_date <= now <= prod_deploy_date).
    # If multiple are in dev, pick the one with the earliest prod_deploy_date.
    # If none are in dev, pick the very next future release.
    st_current_chosen = set()
    
    # Pass 1: Find active dev cycles
    for r in all_releases:
        d_s = r.get("dev_start_date") or r.get("prod_deploy_date", "")
        p_d = r.get("prod_deploy_date", "")
        if d_s <= now_iso <= p_d:
            if r["state"] not in st_current_chosen:
                current_releases.append(r)
                st_current_chosen.add(r["state"])
                
    # Pass 2: Process the rest
    for r in all_releases:
        if r["state"] in st_current_chosen and r in current_releases:
            continue
            
        p_d = r.get("prod_deploy_date", "")
        
        if p_d < now_iso:
            completed_releases.append(r)
        else:
            if r["state"] not in st_current_chosen:
                current_releases.append(r)
                st_current_chosen.add(r["state"])
            else:
                upcoming_releases.append(r)

    current_ids = {r["release_id"] for r in current_releases}
    active_releases = [] # Kept for compatibility with other arrays if needed

    # --------------------------------------------------------------------------
    # 4. Strict Grafana Metric Ribbon (ui.grafana_stat_card)
    # --------------------------------------------------------------------------
    kpi_col1, kpi_col2, kpi_col3, kpi_col4 = st.columns(4)

    cur_rel = current_releases[0] if current_releases else None
    cur_label = cur_rel["release_id"] if cur_rel else "None"
    cur_date = cur_rel["prod_deploy_date"] if cur_rel else "—"
    cur_state = cur_rel["state"] if cur_rel else ""

    days_to_cutover = 0
    try:
        days_to_cutover = (datetime.strptime(cur_date, "%Y-%m-%d").date() - datetime.strptime(now_iso, "%Y-%m-%d").date()).days
    except Exception:
        pass

    uat_count = sum(1 for r in active_releases if _get_stage_info(r, now_iso)[0].startswith("State UAT"))
    sit_count = sum(1 for r in active_releases if _get_stage_info(r, now_iso)[0].startswith("SIT"))
    dev_count = sum(1 for r in active_releases if _get_stage_info(r, now_iso)[0].startswith("Development"))

    # Calculate average gate readiness across active + current releases
    monitored_pool = (current_releases + active_releases)
    avg_readiness = (sum(r.get("readiness_pct", 0) for r in monitored_pool) / len(monitored_pool)) if monitored_pool else 100.0

    # Card 1: Immediate Cutover Focus
    with kpi_col1:
        k1_state = "firing" if days_to_cutover == 0 else "pending"
        k1_badge = "CUTOVER TODAY" if days_to_cutover == 0 else f"D-{days_to_cutover} CUTOVER"
        st.markdown(ui.grafana_stat_card(
            label="Immediate Cutover Target",
            value=cur_label,
            color="#f2495c" if days_to_cutover == 0 else "#ff9830",
            subtext=f"Cutover: {cur_date} · {cur_state} MMIS",
            badge=k1_badge,
            sparkline_vals=[100, 85, 90, 95, 100] if days_to_cutover == 0 else [60, 70, 75, 80, 88],
            delta=f"Gate: {cur_rel['readiness_pct']:.0f}%" if cur_rel else "100%",
            state=k1_state,
        ), unsafe_allow_html=True)

    # Card 2: Active In-Flight Pipeline
    with kpi_col2:
        st.markdown(ui.grafana_stat_card(
            label="Active In-Flight Pipeline",
            value=f"{len(active_releases)} Active",
            color="#5794f2",
            subtext=f"{uat_count} in UAT · {sit_count} in SIT · {dev_count} in DEV",
            badge="LIVE TESTING",
            sparkline_vals=[dev_count, sit_count, uat_count, len(active_releases)] if active_releases else [0],
            delta="DEV➔SIT➔UAT Flow",
            state="ok",
        ), unsafe_allow_html=True)

    # Card 3: Scheduled Roadmap
    with kpi_col3:
        next_up_date = upcoming_releases[0]["prod_deploy_date"] if upcoming_releases else "—"
        next_up_rel = upcoming_releases[0]["release_id"] if upcoming_releases else "None"
        st.markdown(ui.grafana_stat_card(
            label="Scheduled Roadmap",
            value=f"{len(upcoming_releases)} Planned",
            color="#73bf69",
            subtext=f"Next: {next_up_rel} ({next_up_date})",
            badge="ROADMAP",
            sparkline_vals=[len(upcoming_releases), max(0, len(upcoming_releases) - 2), len(upcoming_releases)],
            delta=f"Target: {next_up_date}",
            state="ok",
        ), unsafe_allow_html=True)

    # Card 4: Pipeline Gate Readiness (Compliance Donut)
    with kpi_col4:
        st.markdown(ui.grafana_stat_card(
            label="Pipeline Gate Readiness",
            value=f"{avg_readiness:.0f}%",
            color="#73bf69" if avg_readiness >= 80 else "#ff9830",
            subtext=f"{len(monitored_pool)} releases in active gates",
            badge="ON SCHEDULE" if avg_readiness >= 80 else "ATTENTION",
            donut_pct=avg_readiness,
            delta="✓ Gate Exit Score",
            state="ok" if avg_readiness >= 80 else "pending",
        ), unsafe_allow_html=True)

    st.markdown('<div style="margin-top:4px;"></div>', unsafe_allow_html=True)

    # --------------------------------------------------------------------------
    # 5. RELEASE CUTOFF ALERT STRIP (DEV / SIT / UAT / GO-NOGO / PROD)
    # --------------------------------------------------------------------------
    alert_chips = _build_alert_chips(all_releases, now_iso)

    if alert_chips:
        seen = {}
        for a in alert_chips:
            key = f"{a['release_id']}_{a['phase']}"
            if key not in seen or a["chip_cls"] == "firing":
                seen[key] = a
        deduped = list(seen.values())
        deduped.sort(key=lambda x: (0 if x["chip_cls"] == "firing" else 1, x["days"]))

        alert_cards = ""
        for a in deduped:
            border_c = "#f2495c" if a["chip_cls"] == "firing" else "#ff9830"
            bg_c = "rgba(242, 73, 92, 0.05)" if a["chip_cls"] == "firing" else "rgba(255, 152, 48, 0.05)"
            alert_cards += f'''
            <div style="background:{bg_c};border:1px solid {border_c};border-radius:2px;padding:8px 12px;display:flex;flex-direction:column;gap:4px;min-width:180px;flex:1;">
              <div style="display:flex;align-items:center;justify-content:space-between;">
                <span style="font-size:10px;font-weight:800;color:var(--ink);">{a["state"]}</span>
                <span style="font-size:9.5px;font-family:var(--mono);color:var(--slate);">{a["release_id"]}</span>
              </div>
              <div style="font-size:11px;font-weight:700;color:{border_c};margin-top:2px;">
                {a["label"]}
              </div>
            </div>
            '''

        n_firing = sum(1 for a in deduped if a["chip_cls"] == "firing")
        n_pending = sum(1 for a in deduped if a["chip_cls"] == "pending")
        severity_txt = f'<span style="color:#f2495c;font-weight:700;font-size:11px;margin-right:8px;">{n_firing} FIRING</span>' if n_firing else ''
        pending_txt = f'<span style="color:#ff9830;font-weight:700;font-size:11px;">{n_pending} PENDING</span>' if n_pending else ''
        dot_color = "#f2495c" if n_firing else "#ff9830"

        render_html(f"""
        <div style="margin-bottom:16px;">
          <div style="display:flex;align-items:center;gap:6px;margin-bottom:8px;">
            <span style="width:8px;height:8px;border-radius:50%;background:{dot_color};box-shadow:0 0 6px {dot_color};display:inline-block;"></span>
            <span style="font-size:12px;font-weight:700;color:var(--ink);letter-spacing:0.04em;text-transform:uppercase;">Release Gate Alerts</span>
            <span style="display:flex;margin-left:8px;">{severity_txt}{pending_txt}</span>
          </div>
          <div style="display:flex;flex-wrap:wrap;gap:8px;">
            {alert_cards}
          </div>
        </div>
        """)

    # --------------------------------------------------------------------------
    # --------------------------------------------------------------------------
    # 6. EXECUTIVE RELEASE STORYBOARD (Previous, Current, Upcoming)
    # --------------------------------------------------------------------------
    st.markdown("<div style='margin-top:12px;margin-bottom:8px;font-size:12px;font-weight:700;color:var(--mute);text-transform:uppercase;letter-spacing:0.04em;'>Enterprise Release Storyboard</div>", unsafe_allow_html=True)

    story_c1, story_c2, story_c3 = st.columns(3, gap="medium")

    def _render_story_card(title: str, rel_data: dict | None, accent_color: str, icon: str):
        if not rel_data:
            return f'''
            <div style="background:#181b1f;border:1px solid #2c3235;border-top:3px solid #2c3235;border-radius:2px;padding:12px;min-height:300px;display:flex;align-items:center;justify-content:center;">
              <div style="text-align:center;color:var(--mute);font-size:11px;">
                <div>{icon}</div>
                <div style="margin-top:6px;">No {title} Found</div>
              </div>
            </div>
            '''
            
        rid = rel_data.get("release_id", "Unknown")
        state_mmis = rel_data.get("state", "Unknown")
        pd_date = rel_data.get("prod_deploy_date", "TBD")
        readiness = rel_data.get("readiness_pct", 0)
        
        dev_f = rel_data.get("dev_end_date", "TBD")
        sit_f = rel_data.get("sit_end_date", "TBD")
        uat_f = rel_data.get("uat_end_date", "TBD")
        gn_d = rel_data.get("go_nogo_date", "TBD")
        
        return f'''
        <div style="background:#181b1f;border:1px solid #2c3235;border-top:3px solid {accent_color};border-radius:2px;padding:12px;min-height:280px;display:flex;flex-direction:column;justify-content:space-between;">
          <div>
            <div style="display:flex;align-items:center;gap:6px;font-size:10px;font-weight:700;color:var(--slate);text-transform:uppercase;letter-spacing:0.04em;margin-bottom:8px;">
              <span>{icon}</span> {title}
            </div>
            <div style="font-size:18px;font-weight:800;color:var(--ink);font-family:var(--mono);line-height:1.2;">
              {rid}
            </div>
            <div style="font-size:11px;font-weight:600;color:{accent_color};margin-top:2px;">
              {state_mmis} MMIS Scope
            </div>
          </div>
          
          <div style="margin:16px 0;background:#141619;border:1px solid #2c3235;border-radius:2px;padding:8px;">
            <div style="font-size:9px;color:var(--mute);text-transform:uppercase;margin-bottom:6px;">Milestone Ledger</div>
            
            <div style="display:flex;justify-content:space-between;font-size:10.5px;margin-bottom:4px;border-bottom:1px dashed #2c3235;padding-bottom:2px;">
              <span style="color:var(--slate);">DEV Freeze</span>
              <span style="color:var(--text);font-family:var(--mono);">{dev_f}</span>
            </div>
            <div style="display:flex;justify-content:space-between;font-size:10.5px;margin-bottom:4px;border-bottom:1px dashed #2c3235;padding-bottom:2px;">
              <span style="color:var(--slate);">SIT Exit</span>
              <span style="color:var(--text);font-family:var(--mono);">{sit_f}</span>
            </div>
            <div style="display:flex;justify-content:space-between;font-size:10.5px;margin-bottom:4px;border-bottom:1px dashed #2c3235;padding-bottom:2px;">
              <span style="color:var(--slate);">UAT Sign-off</span>
              <span style="color:var(--text);font-family:var(--mono);">{uat_f}</span>
            </div>
            <div style="display:flex;justify-content:space-between;font-size:10.5px;margin-bottom:4px;border-bottom:1px dashed #2c3235;padding-bottom:2px;">
              <span style="color:var(--slate);">Go / No-Go</span>
              <span style="color:var(--text);font-family:var(--mono);">{gn_d}</span>
            </div>
          </div>
          
          <div style="display:flex;align-items:center;justify-content:space-between;background:rgba(255,255,255,0.02);border:1px solid #2c3235;padding:6px 10px;border-radius:2px;">
            <div>
              <div style="font-size:9px;color:var(--slate);text-transform:uppercase;">Cutover Target</div>
              <div style="font-size:12px;font-weight:700;color:var(--ink);font-family:var(--mono);">{pd_date}</div>
            </div>
            <div style="text-align:right;">
              <div style="font-size:9px;color:var(--slate);text-transform:uppercase;">Readiness</div>
              <div style="font-size:12px;font-weight:700;color:{accent_color};font-family:var(--mono);">{readiness:.0f}%</div>
            </div>
          </div>
        </div>
        '''

    with story_c1:
        prev_r = completed_releases[-1] if completed_releases else None
        render_html(_render_story_card("Previous Release", prev_r, "#73bf69", "📁"))
        
    with story_c2:
        curr_r = current_releases[0] if current_releases else None
        render_html(_render_story_card("Current Release", curr_r, "#38bdf8", "🎯"))
        
    with story_c3:
        next_r = upcoming_releases[0] if upcoming_releases else None
        render_html(_render_story_card("Upcoming Release", next_r, "#f59e0b", "🚀"))

    conn.close()

    # --------------------------------------------------------------------------
    # 7. GRANULAR MASTER-DETAIL INSPECTOR
    # --------------------------------------------------------------------------
    st.markdown("<div style='margin-top:24px;margin-bottom:8px;font-size:12px;font-weight:700;color:var(--mute);text-transform:uppercase;letter-spacing:0.04em;'>Granular Milestone Ledger & Roadmap</div>", unsafe_allow_html=True)
    
    tab_deck, tab_matrix = st.tabs([
        "🎯 Release Flight Deck", 
        "📋 Multi-Release Roadmap Matrix"
    ])
    
    release_dict = {r["release_id"]: r for r in all_releases}
    all_rel_ids = list(release_dict.keys())
    
    with tab_deck:
        d_c1, d_c2 = st.columns([1.0, 2.0], gap="medium")
        with d_c1:
            chosen_rel = st.selectbox(
                "Select Release to Inspect",
                all_rel_ids,
                key="rp_target_rel_picker",
                label_visibility="collapsed"
            )
            rel_data = release_dict.get(chosen_rel)
            if rel_data:
                st.markdown(f'''
                <div style="background:#141619;border:1px solid #2c3235;border-radius:2px;padding:12px;">
                    <div style="font-size:10px;color:var(--mute);text-transform:uppercase;">Selected Target</div>
                    <div style="font-size:16px;font-weight:700;color:var(--text);">{rel_data['state']}.{rel_data['release_id']}</div>
                    <div style="margin-top:8px;font-size:12px;color:var(--slate);">
                        <b>DEV Freeze:</b> {rel_data.get('dev_end_date', 'TBD')}<br/>
                        <b>SIT Exit:</b> {rel_data.get('sit_end_date', 'TBD')}<br/>
                        <b>UAT Sign-off:</b> {rel_data.get('uat_end_date', 'TBD')}<br/>
                        <b>PROD Cutover:</b> {rel_data.get('prod_deploy_date', 'TBD')}
                    </div>
                </div>
                ''', unsafe_allow_html=True)
        with d_c2:
            if rel_data:
                st.markdown('''
                <div style="background:#181b1f;border:1px solid #2c3235;border-radius:2px;padding:12px;">
                    <div style="font-size:12px;font-weight:600;color:var(--text);margin-bottom:8px;">Milestone Execution Ledger</div>
                    <table class="tblx" style="width:100%;font-size:11px;">
                        <tr><th style="text-align:left;">Phase</th><th style="text-align:left;">Environment</th><th style="text-align:left;">Target Date</th><th style="text-align:right;">Status</th></tr>
                        <tr><td>DEV Freeze</td><td>Build-76 / ENV52</td><td>{dev}</td><td style="text-align:right;">{d_stat}</td></tr>
                        <tr><td>SIT Gate</td><td>SIT QA / ENV57</td><td>{sit}</td><td style="text-align:right;">{s_stat}</td></tr>
                        <tr><td>UAT Gate</td><td>Acceptance / ENV04</td><td>{uat}</td><td style="text-align:right;">{u_stat}</td></tr>
                        <tr><td>PROD Cutover</td><td>PROD / ENV05</td><td>{prod}</td><td style="text-align:right;">{p_stat}</td></tr>
                    </table>
                </div>
                '''.format(
                    dev=rel_data.get('dev_end_date', 'TBD'), d_stat='<span style="color:#73bf69;font-weight:700;">PASSED</span>' if rel_data.get('dev_end_date', '') < now_iso else '<span style="color:#5794f2;font-weight:700;">PENDING</span>',
                    sit=rel_data.get('sit_end_date', 'TBD'), s_stat='<span style="color:#73bf69;font-weight:700;">PASSED</span>' if rel_data.get('sit_end_date', '') < now_iso else '<span style="color:#5794f2;font-weight:700;">PENDING</span>',
                    uat=rel_data.get('uat_end_date', 'TBD'), u_stat='<span style="color:#73bf69;font-weight:700;">PASSED</span>' if rel_data.get('uat_end_date', '') < now_iso else '<span style="color:#5794f2;font-weight:700;">PENDING</span>',
                    prod=rel_data.get('prod_deploy_date', 'TBD'), p_stat='<span style="color:#73bf69;font-weight:700;">PASSED</span>' if rel_data.get('prod_deploy_date', '') < now_iso else '<span style="color:#5794f2;font-weight:700;">PENDING</span>'
                ), unsafe_allow_html=True)
                
    with tab_matrix:
        if not all_releases:
            st.info("No releases available for roadmap.")
        else:
            roadmap_rows = []
            for r in all_releases:
                d_end = r.get('prod_deploy_date', 'TBD')
                status = '<span style="color:#73bf69;font-weight:700;">DEPLOYED</span>' if d_end < now_iso else '<span style="color:#ff9830;font-weight:700;">SCHEDULED</span>'
                roadmap_rows.append(f"<tr><td>{r.get('state')}</td><td><b>{r.get('release_id')}</b></td><td>{r.get('dev_end_date', 'TBD')}</td><td>{r.get('sit_end_date', 'TBD')}</td><td>{r.get('uat_end_date', 'TBD')}</td><td>{d_end}</td><td style='text-align:right;'>{status}</td></tr>")
            
            st.markdown(f'''
            <div style="background:#181b1f;border:1px solid #2c3235;border-radius:2px;max-height:300px;overflow-y:auto;">
                <table class="tblx" style="width:100%;font-size:11px;">
                    <tr style="position:sticky;top:0;background:#141619;border-bottom:1px solid #2c3235;">
                        <th style="text-align:left;">State</th><th style="text-align:left;">Release</th><th style="text-align:left;">DEV</th><th style="text-align:left;">SIT</th><th style="text-align:left;">UAT</th><th style="text-align:left;">PROD</th><th style="text-align:right;">Status</th>
                    </tr>
                    {''.join(roadmap_rows)}
                </table>
            </div>
            ''', unsafe_allow_html=True)
