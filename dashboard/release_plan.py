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

        # DEV freeze
        dev_f = r.get("dev_end_date")
        d = _days_between(dev_f, now_iso)
        if d is not None:
            if d < 0:
                alerts.append({"release_id": rid, "state": st_code, "phase": "DEV FREEZE",
                                "date": dev_f, "days": d, "chip_cls": "firing", "label": f"DEV FREEZE OVERDUE {abs(d)}d"})
            elif d <= 7:
                alerts.append({"release_id": rid, "state": st_code, "phase": "DEV FREEZE",
                                "date": dev_f, "days": d, "chip_cls": "pending",
                                "label": f"DEV FREEZE IN {d}d" if d > 0 else "DEV FREEZE TODAY"})

        # SIT gate
        sit_f = r.get("sit_end_date")
        d = _days_between(sit_f, now_iso)
        if d is not None:
            if d < 0:
                alerts.append({"release_id": rid, "state": st_code, "phase": "SIT GATE",
                                "date": sit_f, "days": d, "chip_cls": "firing", "label": f"SIT GATE OVERDUE {abs(d)}d"})
            elif d <= 7:
                alerts.append({"release_id": rid, "state": st_code, "phase": "SIT GATE",
                                "date": sit_f, "days": d, "chip_cls": "pending",
                                "label": f"SIT GATE IN {d}d" if d > 0 else "SIT GATE TODAY"})

        # UAT gate
        uat_f = r.get("uat_end_date")
        d = _days_between(uat_f, now_iso)
        if d is not None:
            if d < 0:
                alerts.append({"release_id": rid, "state": st_code, "phase": "UAT GATE",
                                "date": uat_f, "days": d, "chip_cls": "firing", "label": f"UAT GATE OVERDUE {abs(d)}d"})
            elif d <= 7:
                alerts.append({"release_id": rid, "state": st_code, "phase": "UAT GATE",
                                "date": uat_f, "days": d, "chip_cls": "pending",
                                "label": f"UAT GATE IN {d}d" if d > 0 else "UAT GATE TODAY"})

        # Go/NoGo
        gn_d = r.get("go_nogo_date")
        d = _days_between(gn_d, now_iso)
        if d is not None:
            if d < 0:
                alerts.append({"release_id": rid, "state": st_code, "phase": "GO/NOGO",
                                "date": gn_d, "days": d, "chip_cls": "firing", "label": f"GO/NOGO OVERDUE {abs(d)}d"})
            elif d <= 2:
                alerts.append({"release_id": rid, "state": st_code, "phase": "GO/NOGO",
                                "date": gn_d, "days": d, "chip_cls": "firing",
                                "label": f"GO/NOGO IN {d}d" if d > 0 else "GO/NOGO TODAY"})

        # PROD cutover
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
    h_col1, h_col2 = st.columns([3.2, 1.0])
    with h_col1:
        render_html("""
        <div style="display:flex;align-items:center;gap:10px;padding:3px 0 6px 0;border-left:3px solid var(--accent);padding-left:8px;">
          <div>
            <div style="font-size:14px;font-weight:700;color:var(--ink);letter-spacing:0.02em;text-transform:uppercase;">
              Schedule Release Plan &amp; Environment Pipeline Command Center
            </div>
            <div style="font-size:10px;color:var(--slate);font-family:var(--mono);margin-top:1px;">
              MASTER FLIGHT DECK • GRANULAR MILESTONE LEDGER • MULTI-RELEASE ROADMAP
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
    all_releases = get_release_schedules(conn, state=effective_state)

    if not all_releases:
        st.info("No release schedule records found.")
        conn.close()
        return

    # --------------------------------------------------------------------------
    # 3. Categorize into: Current, Active, Upcoming, Completed
    # --------------------------------------------------------------------------
    future_or_today = [r for r in all_releases if r.get("prod_deploy_date", "") >= now_iso]
    future_or_today.sort(key=lambda x: x.get("prod_deploy_date", ""))

    cutovers_today = [r for r in future_or_today if r.get("prod_deploy_date", "") == now_iso]

    if cutovers_today:
        current_releases = cutovers_today
    elif future_or_today:
        st_seen = set()
        current_releases = []
        for r in future_or_today:
            if r["state"] not in st_seen:
                st_seen.add(r["state"])
                current_releases.append(r)
    else:
        current_releases = [all_releases[-1]] if all_releases else []

    current_ids = {r["release_id"] for r in current_releases}

    active_releases = []
    upcoming_releases = []
    completed_releases = []

    for r in all_releases:
        rid = r["release_id"]
        p_d = r.get("prod_deploy_date", "")
        d_s = r.get("dev_start_date")

        if rid in current_ids:
            continue

        if p_d < now_iso:
            completed_releases.append(r)
        elif r["status"] == "In Progress" or (d_s and d_s <= now_iso <= p_d):
            active_releases.append(r)
        else:
            upcoming_releases.append(r)

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

        chips_html = ""
        for a in deduped:
            state_badge = f'<span style="font-size:8px;font-weight:700;color:var(--slate);background:#141619;border:1px solid #2c3235;padding:0px 4px;border-radius:2px;margin-right:2px;">{a["state"]}</span>'
            rel_badge = f'<span style="font-size:8px;font-weight:700;color:var(--mute);margin-right:4px;font-family:var(--mono);">{a["release_id"]}</span>'
            chips_html += f'<span style="display:inline-flex;align-items:center;gap:2px;margin-right:6px;">{state_badge}{rel_badge}<span class="alert-chip {a["chip_cls"]}" style="font-size:9px;">{a["label"]}</span></span>'

        n_firing = sum(1 for a in deduped if a["chip_cls"] == "firing")
        n_pending = sum(1 for a in deduped if a["chip_cls"] == "pending")
        severity_txt = f'<span style="color:#f2495c;font-weight:700;font-size:9px;">{n_firing} FIRING</span>' if n_firing else ''
        pending_txt = f'<span style="color:#ff9830;font-weight:700;font-size:9px;">{n_pending} PENDING</span>' if n_pending else ''
        dot_color = "#f2495c" if n_firing else "#ff9830"

        render_html(f"""
        <div style="background:#181b1f;border:1px solid #2c3235;border-left:3px solid {dot_color};border-radius:2px;padding:6px 12px;margin-bottom:8px;display:flex;align-items:center;gap:10px;flex-wrap:wrap;">
          <div style="display:flex;align-items:center;gap:6px;flex:none;">
            <span style="width:7px;height:7px;border-radius:50%;background:{dot_color};box-shadow:0 0 6px {dot_color};display:inline-block;"></span>
            <span style="font-size:10px;font-weight:700;color:var(--ink);letter-spacing:0.04em;text-transform:uppercase;white-space:nowrap;">Release Gate Alerts</span>
            <span style="display:flex;gap:5px;">{severity_txt}{pending_txt}</span>
          </div>
          <div style="display:flex;flex-wrap:wrap;gap:4px;align-items:center;flex:1;">
            {chips_html}
          </div>
          <div style="flex:none;font-size:9px;color:var(--mute);white-space:nowrap;font-family:var(--mono);">{len(deduped)} gate event{'s' if len(deduped)!=1 else ''}</div>
        </div>
        """)

    # --------------------------------------------------------------------------
    # 6. ENTERPRISE SUB-TABS: MASTER-DETAIL FLIGHT DECK vs ROADMAP vs ARCHIVE
    # --------------------------------------------------------------------------
    subtab_deck, subtab_roadmap, subtab_archive = st.tabs([
        "🎯 Release Flight Deck & Milestone Execution",
        "📋 Multi-Release Roadmap Matrix",
        "📁 Completed Releases Archive",
    ])

    # Determine all available release IDs for the switcher
    release_dict = {r["release_id"]: r for r in all_releases}
    all_rel_ids = list(release_dict.keys())

    # Selected release resolution
    curr_selected = st.session_state.get("rel_selected_id")
    if not curr_selected or curr_selected not in release_dict:
        curr_selected = cur_rel["release_id"] if cur_rel else all_rel_ids[0]
        st.session_state["rel_selected_id"] = curr_selected

    # --------------------------------------------------------------------------
    # SUBTAB 1: MASTER-DETAIL INTERACTIVE FLIGHT DECK & MILESTONE LEDGER
    # --------------------------------------------------------------------------
    with subtab_deck:
        col_deck, col_ledger = st.columns([1.0, 1.15], gap="medium")

        with col_deck:
            # Interactive Release Switcher
            sel_c1, sel_c2 = st.columns([2.0, 1.0])
            with sel_c1:
                cur_idx = all_rel_ids.index(curr_selected) if curr_selected in all_rel_ids else 0
                chosen_rel = st.selectbox(
                    "Switch Target Release",
                    all_rel_ids,
                    index=cur_idx,
                    key="rp_target_rel_picker",
                    label_visibility="collapsed",
                    help="Select any release to inspect its 5-stage pipeline and granular milestone execution ledger",
                )
                if chosen_rel != curr_selected:
                    st.session_state["rel_selected_id"] = chosen_rel
                    st.rerun()

            rel_data = release_dict[curr_selected]
            stg_name, env_tag, badge_lbl = _get_stage_info(rel_data, now_iso)
            chip_cls = "firing" if "TODAY" in badge_lbl else ("pending" if "PROGRESS" in badge_lbl else "ok")

            dev_s, dev_f = rel_data.get("dev_start_date"), rel_data.get("dev_end_date")
            sit_s, sit_f = rel_data.get("sit_start_date"), rel_data.get("sit_end_date")
            uat_s, uat_f = rel_data.get("uat_start_date"), rel_data.get("uat_end_date")
            gn_d = rel_data.get("go_nogo_date")
            p_d = rel_data.get("prod_deploy_date")

            stg_dev_l, stg_dev_bg, stg_dev_fg, stg_dev_bd = _calc_stage_pill(dev_s, dev_f, now_iso)
            stg_sit_l, stg_sit_bg, stg_sit_fg, stg_sit_bd = _calc_stage_pill(sit_s, sit_f, now_iso)
            stg_uat_l, stg_uat_bg, stg_uat_fg, stg_uat_bd = _calc_stage_pill(uat_s, uat_f, now_iso)
            stg_gn_l, stg_gn_bg, stg_gn_fg, stg_gn_bd = _calc_stage_pill(gn_d, gn_d, now_iso)
            stg_prod_l, stg_prod_bg, stg_prod_fg, stg_prod_bd = _calc_stage_pill(p_d, p_d, now_iso)

            env_name_dev = "ENV52 Dev" if rel_data["state"] == "NH" else "Build-76 Dev"
            env_name_sit = "ENV57 / ENV53" if rel_data["state"] == "NH" else "SIT QA"
            env_name_uat = "ENV04 UAT" if rel_data["state"] == "NH" else "State Acceptance"
            env_name_prod = "ENV05 Live" if rel_data["state"] == "NH" else "PROD Cutover"

            top_border_dev = "#73bf69" if "COMPLETED" in stg_dev_l else ("#5794f2" if "ACTIVE" in stg_dev_l else "var(--rule)")
            top_border_sit = "#73bf69" if "COMPLETED" in stg_sit_l else ("#5794f2" if "ACTIVE" in stg_sit_l else "var(--rule)")
            top_border_uat = "#73bf69" if "COMPLETED" in stg_uat_l else ("#5794f2" if "ACTIVE" in stg_uat_l else "var(--rule)")
            top_border_gn = "#73bf69" if "COMPLETED" in stg_gn_l else ("#ff9830" if "ACTIVE" in stg_gn_l else "var(--rule)")
            top_border_prod = "#73bf69" if "COMPLETED" in stg_prod_l else ("#f2495c" if "TODAY" in stg_prod_l else "var(--rule)")

            def _stage_countdown(end_date: str | None, now: str) -> str:
                d = _days_between(end_date, now)
                if end_date is None or d is None:
                    return ""
                if d < 0:
                    return f'<span style="font-size:8px;color:#f2495c;font-weight:700;font-family:var(--mono);">{abs(d)}d OVERDUE</span>'
                elif d == 0:
                    return '<span style="font-size:8px;color:#f2495c;font-weight:700;font-family:var(--mono);">CUTOFF TODAY</span>'
                elif d <= 7:
                    return f'<span style="font-size:8px;color:#ff9830;font-weight:700;font-family:var(--mono);">{d}d to gate</span>'
                return ""

            dev_cd = _stage_countdown(dev_f, now_iso)
            sit_cd = _stage_countdown(sit_f, now_iso)
            uat_cd = _stage_countdown(uat_f, now_iso)
            gn_cd = _stage_countdown(gn_d, now_iso)
            prod_cd = _stage_countdown(p_d, now_iso)

            with sel_c2:
                st.markdown(f"""
                <div style="text-align:right;padding-top:4px;">
                  <span class="alert-chip {chip_cls}">● {badge_lbl}</span>
                </div>
                """, unsafe_allow_html=True)

            # Selected Release Flight Deck Card
            render_html(f"""
            <div style="background:#181b1f;border:1px solid #2c3235;border-left:3px solid var(--accent);border-radius:2px;padding:8px 12px;margin-bottom:6px;">
              <!-- Top Row: Release Meta Strip -->
              <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:6px;border-bottom:1px solid #2c3235;padding-bottom:6px;margin-bottom:6px;">
                <div style="display:flex;align-items:center;gap:8px;">
                  <span style="font-size:15px;font-weight:800;color:var(--ink);font-family:var(--mono);">{rel_data['release_id']}</span>
                  <span style="font-size:9px;font-weight:700;background:#141619;color:var(--slate);border:1px solid #2c3235;padding:1px 6px;border-radius:2px;font-family:var(--mono);">
                    {rel_data['state']} MMIS ({rel_data['quarter']} {rel_data['year']})
                  </span>
                </div>
                <div style="display:flex;align-items:center;gap:12px;font-size:11px;">
                  <div><span style="color:var(--slate);">Gate:</span> <b style="color:#5794f2;">{stg_name} ({env_tag})</b></div>
                  <div><span style="color:var(--slate);">Cutover:</span> <b style="color:var(--ink);font-family:var(--mono);">{p_d}</b></div>
                  <div><span style="color:var(--slate);">Readiness:</span> <b style="color:#73bf69;font-family:var(--mono);">{rel_data['readiness_pct']:.0f}%</b></div>
                </div>
              </div>

              <!-- 5-Stage Stepper Grid (Strict Grafana Tiles) -->
              <div style="display:grid;grid-template-columns:repeat(5, 1fr);gap:5px;">
                <!-- 1. DEV -->
                <div style="background:#141619;border:1px solid #2c3235;border-top:3px solid {top_border_dev};border-radius:2px;padding:4px 6px;display:flex;flex-direction:column;justify-content:space-between;min-height:64px;">
                  <div>
                    <div style="font-size:8px;font-weight:700;color:var(--slate);text-transform:uppercase;letter-spacing:0.04em;">1. DEV BUILD</div>
                    <div style="font-size:10.5px;font-weight:700;color:var(--ink);margin-top:1px;">{env_name_dev}</div>
                    <div style="font-size:9px;color:var(--slate);font-family:var(--mono);margin-top:1px;">{dev_s or '—'} ➔ {dev_f or '—'}</div>
                    <div style="margin-top:1px;">{dev_cd}</div>
                  </div>
                  <div style="margin-top:2px;">
                    <span style="font-size:7.5px;font-weight:700;padding:1px 3px;border-radius:2px;background:{stg_dev_bg};color:{stg_dev_fg};border:1px solid {stg_dev_bd};">{stg_dev_l}</span>
                  </div>
                </div>

                <!-- 2. SIT -->
                <div style="background:#141619;border:1px solid #2c3235;border-top:3px solid {top_border_sit};border-radius:2px;padding:4px 6px;display:flex;flex-direction:column;justify-content:space-between;min-height:64px;">
                  <div>
                    <div style="font-size:8px;font-weight:700;color:var(--slate);text-transform:uppercase;letter-spacing:0.04em;">2. SIT REGRESSION</div>
                    <div style="font-size:10.5px;font-weight:700;color:var(--ink);margin-top:1px;">{env_name_sit}</div>
                    <div style="font-size:9px;color:var(--slate);font-family:var(--mono);margin-top:1px;">{sit_s or '—'} ➔ {sit_f or '—'}</div>
                    <div style="margin-top:1px;">{sit_cd}</div>
                  </div>
                  <div style="margin-top:2px;">
                    <span style="font-size:7.5px;font-weight:700;padding:1px 3px;border-radius:2px;background:{stg_sit_bg};color:{stg_sit_fg};border:1px solid {stg_sit_bd};">{stg_sit_l}</span>
                  </div>
                </div>

                <!-- 3. UAT -->
                <div style="background:#141619;border:1px solid #2c3235;border-top:3px solid {top_border_uat};border-radius:2px;padding:4px 6px;display:flex;flex-direction:column;justify-content:space-between;min-height:64px;">
                  <div>
                    <div style="font-size:8px;font-weight:700;color:var(--slate);text-transform:uppercase;letter-spacing:0.04em;">3. STATE UAT</div>
                    <div style="font-size:10.5px;font-weight:700;color:var(--ink);margin-top:1px;">{env_name_uat}</div>
                    <div style="font-size:9px;color:var(--slate);font-family:var(--mono);margin-top:1px;">{uat_s or '—'} ➔ {uat_f or '—'}</div>
                    <div style="margin-top:1px;">{uat_cd}</div>
                  </div>
                  <div style="margin-top:2px;">
                    <span style="font-size:7.5px;font-weight:700;padding:1px 3px;border-radius:2px;background:{stg_uat_bg};color:{stg_uat_fg};border:1px solid {stg_uat_bd};">{stg_uat_l}</span>
                  </div>
                </div>

                <!-- 4. GO/NO-GO -->
                <div style="background:#141619;border:1px solid #2c3235;border-top:3px solid {top_border_gn};border-radius:2px;padding:4px 6px;display:flex;flex-direction:column;justify-content:space-between;min-height:64px;">
                  <div>
                    <div style="font-size:8px;font-weight:700;color:var(--slate);text-transform:uppercase;letter-spacing:0.04em;">4. GO / NO-GO</div>
                    <div style="font-size:10.5px;font-weight:700;color:var(--ink);margin-top:1px;">Decision Gate</div>
                    <div style="font-size:9px;color:var(--slate);font-family:var(--mono);margin-top:1px;">{gn_d or 'Pre-Cutover'}</div>
                    <div style="margin-top:1px;">{gn_cd}</div>
                  </div>
                  <div style="margin-top:2px;">
                    <span style="font-size:7.5px;font-weight:700;padding:1px 3px;border-radius:2px;background:{stg_gn_bg};color:{stg_gn_fg};border:1px solid {stg_gn_bd};">{stg_gn_l}</span>
                  </div>
                </div>

                <!-- 5. PROD CUTOVER -->
                <div style="background:#141619;border:1px solid #2c3235;border-top:3px solid {top_border_prod};border-radius:2px;padding:4px 6px;display:flex;flex-direction:column;justify-content:space-between;min-height:64px;">
                  <div>
                    <div style="font-size:8px;font-weight:700;color:var(--slate);text-transform:uppercase;letter-spacing:0.04em;">5. PROD LIVE</div>
                    <div style="font-size:10.5px;font-weight:700;color:var(--ink);margin-top:1px;">{env_name_prod}</div>
                    <div style="font-size:9px;color:var(--ink);font-weight:700;font-family:var(--mono);margin-top:1px;">{p_d}</div>
                    <div style="margin-top:1px;">{prod_cd}</div>
                  </div>
                  <div style="margin-top:2px;">
                    <span style="font-size:7.5px;font-weight:700;padding:1px 3px;border-radius:2px;background:{stg_prod_bg};color:{stg_prod_fg};border:1px solid {stg_prod_bd};">{stg_prod_l}</span>
                  </div>
                </div>
              </div>
            </div>
            """)

            # Environment & Release Telemetry Metadata Panel
            rm_contact = rel_data.get("state_rm_name", f"{rel_data['state']} RM")
            rm_email = rel_data.get("state_rm_email", "rm@ets.state.gov")
            readiness = rel_data.get("readiness_pct", 100.0)

            render_html(f"""
            <div style="background:#141619;border:1px solid #2c3235;border-radius:2px;padding:6px 10px;font-size:10.5px;">
              <div style="font-size:9.5px;font-weight:700;color:var(--slate);text-transform:uppercase;letter-spacing:0.04em;margin-bottom:4px;">
                Pipeline Architecture &amp; Governance Contacts
              </div>
              <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;">
                <div>
                  <div style="color:var(--mute);font-size:10px;">Target Infrastructure:</div>
                  <div style="color:var(--text);font-weight:600;margin-top:1px;">{env_name_dev} ➔ {env_name_sit} ➔ {env_name_uat} ➔ {env_name_prod}</div>
                </div>
                <div>
                  <div style="color:var(--mute);font-size:10px;">Assigned Release Manager:</div>
                  <div style="color:var(--text);font-weight:600;margin-top:1px;">{rm_contact} &lt;<code style="color:var(--accent);font-size:10px;">{rm_email}</code>&gt;</div>
                </div>
              </div>
              <div style="margin-top:8px;padding-top:6px;border-top:1px solid #22252b;display:flex;align-items:center;justify-content:space-between;">
                <div><span style="color:var(--mute);">Gate Exit Readiness:</span> <b style="color:#73bf69;font-family:var(--mono);">{readiness:.0f}% Compliance Score</b></div>
                <div style="width:160px;background:rgba(255,255,255,0.06);height:6px;border-radius:2px;overflow:hidden;">
                  <div style="width:{readiness:.0f}%;background:#73bf69;height:100%;"></div>
                </div>
              </div>
            </div>
            """)

        with col_ledger:
            # Fetch real milestone tasks from SQLite
            milestones = get_release_milestones(conn, curr_selected)

            m_tot = len(milestones)
            m_pass = sum(1 for m in milestones if m["status"] == "Passed")
            m_prog = sum(1 for m in milestones if m["status"] == "In Progress")
            m_fail = sum(1 for m in milestones if m["status"] in ("Failed", "Overdue"))

            # Header & Filter Toolbar
            f_col1, f_col2 = st.columns([1.8, 1.2])
            with f_col1:
                st.markdown(ui.panel_header(
                    f"Milestone Tasks Ledger — {curr_selected}",
                    color="#5794f2",
                    live=True,
                    count=f"{m_tot} Tasks • {m_pass} Passed • {m_prog} In Progress",
                ), unsafe_allow_html=True)

            with f_col2:
                task_q = st.text_input(
                    "Search tasks...",
                    key=f"ms_task_search_{curr_selected}",
                    placeholder="Search task or env...",
                    label_visibility="collapsed",
                )

            # Phase filter tabs / options
            all_phases = ["ALL", "Planning", "Development", "SIT", "UAT", "Gate", "Production"]
            active_phase = st.selectbox(
                "Filter Phase",
                all_phases,
                index=0,
                key=f"ms_phase_pick_{curr_selected}",
                label_visibility="collapsed",
            )

            # Filter milestones
            filtered_ms = milestones
            if active_phase != "ALL":
                filtered_ms = [m for m in filtered_ms if m.get("phase_category") == active_phase]
            if task_q and task_q.strip():
                tq = task_q.strip().lower()
                filtered_ms = [
                    m for m in filtered_ms
                    if tq in m.get("task_name", "").lower()
                    or tq in m.get("env_target", "").lower()
                    or tq in m.get("status", "").lower()
                ]

            # Render milestone table inside responsive height viewport
            phase_colors = {
                "Planning": "#94a3b8",
                "Development": "#5794f2",
                "SIT": "#a855f7",
                "UAT": "#ff9830",
                "Gate": "#06b6d4",
                "Production": "#73bf69",
            }

            status_chips = {
                "Passed": '<span class="alert-chip ok">PASSED</span>',
                "In Progress": '<span class="alert-chip pending">IN PROGRESS</span>',
                "Pending": '<span style="font-size:8.5px;font-weight:700;padding:1px 5px;border-radius:2px;background:rgba(255,255,255,0.04);color:var(--mute);border:1px solid var(--rule);">PENDING</span>',
                "Failed": '<span class="alert-chip firing">FAILED</span>',
            }

            if not filtered_ms:
                render_html("""
                <div style="background:#141619;border:1px solid #2c3235;border-radius:2px;padding:24px;text-align:center;color:var(--mute);font-size:12px;">
                  No milestone execution tasks match the selected phase or search criteria.
                </div>
                """)
            else:
                ms_rows = []
                for idx, m in enumerate(filtered_ms):
                    p_cat = m.get("phase_category", "Milestone")
                    p_col = phase_colors.get(p_cat, "#5794f2")
                    st_chip = status_chips.get(m.get("status"), '<span class="alert-chip ok">PASSED</span>')

                    row_bg = "#181b1f" if idx % 2 == 0 else "#141619"
                    d_str = f"{m.get('start_date') or '—'} ➔ {m.get('finish_date') or '—'}"
                    dur = m.get("duration_str") or ""
                    dur_html = f'<span style="font-size:9px;color:var(--mute);font-family:var(--mono);">({dur})</span>' if dur else ""

                    pred = m.get("predecessors") or ""
                    pred_html = f'<span style="font-size:8.5px;color:var(--slate);margin-left:4px;" title="Predecessor Task">⛓️ #{pred}</span>' if pred else ""

                    row_html = f"""
                    <tr style="background:{row_bg};border-bottom:1px solid #22252b;font-size:11px;">
                      <td style="padding:5px 8px;width:80px;">
                        <span style="font-size:8.5px;font-weight:700;padding:1px 5px;border-radius:2px;background:rgba(255,255,255,0.03);color:{p_col};border:1px solid {p_col}40;">
                          {p_cat.upper()}
                        </span>
                      </td>
                      <td style="padding:5px 8px;">
                        <div style="font-weight:600;color:var(--ink);">{m['task_name']}{pred_html}</div>
                        <div style="font-size:9.5px;color:var(--slate);font-family:var(--mono);">Target: <b style="color:var(--text);">{m.get('env_target') or 'All Envs'}</b></div>
                      </td>
                      <td style="padding:5px 8px;font-family:var(--mono);font-size:10px;color:var(--slate);white-space:nowrap;">
                        {d_str} {dur_html}
                      </td>
                      <td style="padding:5px 8px;text-align:right;width:95px;">
                        {st_chip}
                      </td>
                    </tr>
                    """
                    ms_rows.append("\n".join(l.strip() for l in row_html.splitlines() if l.strip()))

                render_html(f"""
                <div style="border:1px solid #2c3235;border-radius:2px;background:#181b1f;max-height:calc(100vh - 270px);min-height:160px;overflow-y:auto;">
                  <table style="width:100%;border-collapse:collapse;text-align:left;">
                    <thead>
                      <tr style="background:#141619;border-bottom:1px solid #2c3235;font-size:9px;font-weight:700;text-transform:uppercase;color:var(--slate);letter-spacing:0.04em;position:sticky;top:0;z-index:2;">
                        <th style="padding:5px 8px;">Phase</th>
                        <th style="padding:5px 8px;">Milestone Task &amp; Target Env</th>
                        <th style="padding:5px 8px;">Date Window</th>
                        <th style="padding:5px 8px;text-align:right;">Status</th>
                      </tr>
                    </thead>
                    <tbody>
                      {"".join(ms_rows)}
                    </tbody>
                  </table>
                </div>
                """)

    # --------------------------------------------------------------------------
    # SUBTAB 2: MULTI-RELEASE ROADMAP MATRIX (ACTIVE + UPCOMING)
    # --------------------------------------------------------------------------
    with subtab_roadmap:
        st.markdown(ui.panel_header(
            "Active Releases In-Flight Pipeline (DEV ➔ SIT ➔ UAT)",
            color="#5794f2",
            live=True,
            count=f"{len(active_releases)} Active Releases",
        ), unsafe_allow_html=True)

        if not active_releases:
            st.info("No in-flight releases currently active.")
        else:
            active_rows = []
            for idx, r in enumerate(active_releases):
                stg_n, e_tag, _ = _get_stage_info(r, now_iso)
                dev_str = f"{r.get('dev_start_date') or '—'} ➔ {r.get('dev_end_date') or '—'}"
                sit_str = f"{r.get('sit_start_date') or '—'} ➔ {r.get('sit_end_date') or '—'}"
                uat_str = f"{r.get('uat_start_date') or '—'} ➔ {r.get('uat_end_date') or '—'}"
                row_bg = "#181b1f" if idx % 2 == 0 else "#141619"
                readiness = r.get('readiness_pct', 0)

                d2c = _days_between(r.get("prod_deploy_date"), now_iso)
                cutover_color = "#f2495c" if (d2c is not None and d2c <= 3) else ("var(--ink)")

                row_html = f"""
                <tr style="background:{row_bg};border-bottom:1px solid #22252b;font-size:11.5px;">
                  <td style="padding:6px 10px;"><span style="font-size:9.5px;font-weight:700;padding:1px 6px;border-radius:2px;background:#141619;border:1px solid #2c3235;color:var(--ink);font-family:var(--mono);">{r['state']}</span></td>
                  <td style="padding:6px 10px;font-weight:700;color:var(--ink);font-family:var(--mono);">{r['release_id']}</td>
                  <td style="padding:6px 10px;">
                    <span style="background:rgba(87,148,242,0.15);color:#5794f2;padding:1px 6px;border-radius:2px;font-weight:700;font-size:10px;border:1px solid rgba(87,148,242,0.3);">{stg_n}</span>
                    <span style="color:var(--slate);font-family:var(--mono);font-size:10px;margin-left:4px;">({e_tag})</span>
                  </td>
                  <td style="padding:6px 10px;color:var(--slate);font-family:var(--mono);font-size:11px;">{dev_str}</td>
                  <td style="padding:6px 10px;color:var(--slate);font-family:var(--mono);font-size:11px;">{sit_str}</td>
                  <td style="padding:6px 10px;color:var(--slate);font-family:var(--mono);font-size:11px;">{uat_str}</td>
                  <td style="padding:6px 10px;font-weight:700;color:{cutover_color};font-family:var(--mono);font-size:11.5px;">{r['prod_deploy_date']}</td>
                  <td style="padding:6px 10px;">
                    <div style="display:flex;align-items:center;gap:6px;min-width:90px;">
                      <div style="flex:1;background:rgba(255,255,255,0.06);border-radius:1px;height:5px;overflow:hidden;">
                        <div style="width:{readiness:.0f}%;background:#73bf69;height:100%;"></div>
                      </div>
                      <span style="font-family:var(--mono);font-size:10.5px;font-weight:700;color:#73bf69;">{readiness:.0f}%</span>
                    </div>
                  </td>
                  <td style="padding:6px 10px;"><span class="alert-chip pending">IN PROGRESS</span></td>
                </tr>
                """
                active_rows.append("\n".join(l.strip() for l in row_html.splitlines() if l.strip()))

            render_html(f"""
            <div style="border:1px solid #2c3235;border-radius:2px;background:#181b1f;overflow:hidden;margin-bottom:14px;max-height:calc(100vh - 460px);min-height:220px;overflow-y:auto;">
              <table style="width:100%;border-collapse:collapse;text-align:left;">
                <thead>
                  <tr style="background:#141619;border-bottom:1px solid #2c3235;font-size:10px;font-weight:700;text-transform:uppercase;color:var(--slate);letter-spacing:0.04em;position:sticky;top:0;z-index:2;">
                    <th style="padding:6px 10px;">State</th>
                    <th style="padding:6px 10px;">Release ID</th>
                    <th style="padding:6px 10px;">Current Gate &amp; Environment</th>
                    <th style="padding:6px 10px;">DEV Window</th>
                    <th style="padding:6px 10px;">SIT Window</th>
                    <th style="padding:6px 10px;">UAT Window</th>
                    <th style="padding:6px 10px;">Cutover Date</th>
                    <th style="padding:6px 10px;">Gate Readiness</th>
                    <th style="padding:6px 10px;">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {"".join(active_rows)}
                </tbody>
              </table>
            </div>
            """)

        # Upcoming releases section
        st.markdown(ui.panel_header(
            "Upcoming Scheduled Releases (Pipeline Roadmap)",
            color="#73bf69",
            live=False,
            count=f"{len(upcoming_releases)} Scheduled",
        ), unsafe_allow_html=True)

        if not upcoming_releases:
            st.info("No upcoming releases scheduled.")
        else:
            upcoming_rows = []
            for idx, r in enumerate(upcoming_releases):
                dev_start = r.get("dev_start_date") or "—"
                sit_start = r.get("sit_start_date") or "—"
                uat_start = r.get("uat_start_date") or "—"
                row_bg = "#181b1f" if idx % 2 == 0 else "#141619"

                row_html = f"""
                <tr style="background:{row_bg};border-bottom:1px solid #22252b;font-size:11.5px;">
                  <td style="padding:6px 10px;"><span style="font-size:9.5px;font-weight:700;padding:1px 6px;border-radius:2px;background:#141619;border:1px solid #2c3235;color:var(--ink);font-family:var(--mono);">{r['state']}</span></td>
                  <td style="padding:6px 10px;font-weight:700;color:var(--ink);font-family:var(--mono);">{r['release_id']}</td>
                  <td style="padding:6px 10px;color:var(--slate);font-family:var(--mono);font-size:11px;">{r['quarter']} {r['year']}</td>
                  <td style="padding:6px 10px;color:var(--mute);font-family:var(--mono);font-size:11px;">{dev_start}</td>
                  <td style="padding:6px 10px;color:var(--mute);font-family:var(--mono);font-size:11px;">{sit_start}</td>
                  <td style="padding:6px 10px;color:var(--mute);font-family:var(--mono);font-size:11px;">{uat_start}</td>
                  <td style="padding:6px 10px;font-weight:700;color:var(--ink);font-family:var(--mono);font-size:11.5px;">{r['prod_deploy_date']}</td>
                  <td style="padding:6px 10px;"><span style="font-size:8.5px;font-weight:700;letter-spacing:.04em;padding:1px 5px;border-radius:2px;background:rgba(255,255,255,0.04);color:var(--mute);border:1px solid var(--rule);">SCHEDULED</span></td>
                </tr>
                """
                upcoming_rows.append("\n".join(l.strip() for l in row_html.splitlines() if l.strip()))

            render_html(f"""
            <div style="border:1px solid #2c3235;border-radius:2px;background:#181b1f;overflow:hidden;margin-bottom:14px;max-height:calc(100vh - 460px);min-height:220px;overflow-y:auto;">
              <table style="width:100%;border-collapse:collapse;text-align:left;">
                <thead>
                  <tr style="background:#141619;border-bottom:1px solid #2c3235;font-size:10px;font-weight:700;text-transform:uppercase;color:var(--slate);letter-spacing:0.04em;position:sticky;top:0;z-index:2;">
                    <th style="padding:6px 10px;">State</th>
                    <th style="padding:6px 10px;">Release ID</th>
                    <th style="padding:6px 10px;">Cadence</th>
                    <th style="padding:6px 10px;">DEV Start</th>
                    <th style="padding:6px 10px;">SIT Start</th>
                    <th style="padding:6px 10px;">UAT Start</th>
                    <th style="padding:6px 10px;">Cutover Date</th>
                    <th style="padding:6px 10px;">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {"".join(upcoming_rows)}
                </tbody>
              </table>
            </div>
            """)

    # --------------------------------------------------------------------------
    # SUBTAB 3: COMPLETED RELEASES (HISTORICAL ARCHIVE)
    # --------------------------------------------------------------------------
    with subtab_archive:
        st.markdown(ui.panel_header(
            f"Completed Releases (Historical Archive) — {len(completed_releases)} Releases",
            color="#73bf69",
            live=False,
            count=f"{len(completed_releases)} Archive Records",
        ), unsafe_allow_html=True)

        if not completed_releases:
            st.info("No completed releases.")
        else:
            comp_rows = []
            for idx, r in enumerate(completed_releases):
                row_bg = "#181b1f" if idx % 2 == 0 else "#141619"
                row_html = f"""
                <tr style="background:{row_bg};border-bottom:1px solid #22252b;font-size:11px;">
                  <td style="padding:5px 8px;"><span style="font-family:var(--mono);font-size:9.5px;font-weight:700;color:var(--slate);">{r['state']}</span></td>
                  <td style="padding:5px 8px;font-weight:700;color:var(--ink);font-family:var(--mono);">{r['release_id']}</td>
                  <td style="padding:5px 8px;color:var(--slate);font-family:var(--mono);">{r['quarter']} {r['year']}</td>
                  <td style="padding:5px 8px;color:var(--ink);font-family:var(--mono);font-weight:700;">{r['prod_deploy_date']}</td>
                  <td style="padding:5px 8px;"><span class="alert-chip ok">DEPLOYED (100%)</span></td>
                </tr>
                """
                comp_rows.append("\n".join(l.strip() for l in row_html.splitlines() if l.strip()))

            render_html(f"""
            <div style="border:1px solid #2c3235;border-radius:2px;background:#141619;max-height:calc(100vh - 360px);min-height:360px;overflow-y:auto;">
              <table style="width:100%;border-collapse:collapse;text-align:left;">
                <thead>
                  <tr style="background:#181b1f;font-size:9.5px;font-weight:700;color:var(--slate);text-transform:uppercase;letter-spacing:0.04em;position:sticky;top:0;z-index:2;">
                    <th style="padding:5px 8px;">State</th>
                    <th style="padding:5px 8px;">Release ID</th>
                    <th style="padding:5px 8px;">Quarter</th>
                    <th style="padding:5px 8px;">Cutover Date</th>
                    <th style="padding:5px 8px;">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {"".join(comp_rows)}
                </tbody>
              </table>
            </div>
            """)

    conn.close()
