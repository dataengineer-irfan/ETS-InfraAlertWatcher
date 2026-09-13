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
    active_scope = st.session_state.get("_override_canvas_state")
    effective_state = active_scope if is_enterprise_admin else user_assigned_state

    # Callbacks for seamless, non-looping state & release filter synchronization
    def _on_state_filter_change():
        chosen = st.session_state.get("sl_state_clean", "All States")
        if "Alaska" in chosen:
            st_val = "AK"
        elif "North Dakota" in chosen:
            st_val = "ND"
        elif "New Hampshire" in chosen:
            st_val = "NH"
        else:
            st_val = None
        st.session_state["_override_canvas_state"] = st_val
        st.session_state["global_release_selection"] = None
        st.session_state["gov_state_filter"] = st_val or "All"
        reset_idx = st.session_state.get("op_reset_idx", 0)
        st.session_state[f"op_state_{reset_idx}"] = st_val or "All States"

    def _on_release_filter_change():
        chosen_rel = st.session_state.get("rp_target_rel_picker")
        if chosen_rel:
            st.session_state["global_release_selection"] = chosen_rel
            st_code = chosen_rel.split(".")[0] if "." in chosen_rel else None
            if st_code in ["NH", "ND", "AK"]:
                st.session_state["_override_canvas_state"] = st_code
                st.session_state["gov_state_filter"] = st_code
                reset_idx = st.session_state.get("op_reset_idx", 0)
                st.session_state[f"op_state_{reset_idx}"] = st_code
                st_map = {"AK": "Alaska (AK)", "ND": "North Dakota (ND)", "NH": "New Hampshire (NH)"}
                st.session_state["sl_state_clean"] = st_map.get(st_code, "All States")

    # --------------------------------------------------------------------------
    # 2. Header & State Selection (Compact Single-Row Header)
    # --------------------------------------------------------------------------
    h_col1, h_col2 = st.columns([3.2, 1.8])
    with h_col1:
        render_html("""
        <div style="display:flex;align-items:center;gap:8px;padding:2px 0 4px 0;border-left:3px solid var(--accent);padding-left:8px;">
          <div>
            <div style="font-size:15px;font-weight:800;color:var(--ink);letter-spacing:0.02em;text-transform:uppercase;line-height:1.1;">
              Release Schedule
            </div>
            <div style="font-size:10px;color:var(--slate);margin-top:1px;">
              Enterprise Milestones &amp; Pipeline Health
            </div>
          </div>
        </div>
        """)

    with h_col2:
        if is_enterprise_admin:
            state_options = ["All States", "Alaska (AK)", "North Dakota (ND)", "New Hampshire (NH)"]
            st_map = {"AK": "Alaska (AK)", "ND": "North Dakota (ND)", "NH": "New Hampshire (NH)"}
            target_label = st_map.get(active_scope, "All States")
            if "sl_state_clean" not in st.session_state or st.session_state.get("sl_state_clean") != target_label:
                st.session_state["sl_state_clean"] = target_label

            st.selectbox(
                "Filter State",
                state_options,
                key="sl_state_clean",
                on_change=_on_state_filter_change,
                label_visibility="collapsed"
            )

    # Query releases for effective scope
    all_releases = get_cached_release_schedules(db_path, state=effective_state)

    if not all_releases:
        st.info("No release schedule records found.")
        return

    # Sort all by prod_deploy_date
    all_releases.sort(key=lambda x: x.get("prod_deploy_date", "9999-12-31"))

    # --------------------------------------------------------------------------
    # 3. Categorize into: Previous, Current, Upcoming
    # --------------------------------------------------------------------------
    curr_r = None
    for r in all_releases:
        d_s = r.get("dev_start_date") or r.get("prod_deploy_date", "")
        p_d = r.get("prod_deploy_date", "")
        if d_s <= now_iso <= p_d:
            curr_r = r
            break
    if not curr_r:
        for r in all_releases:
            if r.get("prod_deploy_date", "") >= now_iso:
                curr_r = r
                break
    if not curr_r and all_releases:
        curr_r = all_releases[-1]

    curr_idx = all_releases.index(curr_r) if curr_r in all_releases else -1
    prev_r = all_releases[curr_idx - 1] if curr_idx > 0 else None
    next_r = all_releases[curr_idx + 1] if 0 <= curr_idx < len(all_releases) - 1 else None

    # Count for pipeline stages
    uat_count = sum(1 for r in all_releases if _get_stage_info(r, now_iso)[0].startswith("State UAT"))
    sit_count = sum(1 for r in all_releases if _get_stage_info(r, now_iso)[0].startswith("SIT"))
    dev_count = sum(1 for r in all_releases if _get_stage_info(r, now_iso)[0].startswith("Development"))
    active_count = sum(1 for r in all_releases if (r.get("dev_start_date") or "") <= now_iso <= (r.get("prod_deploy_date") or "9999"))
    if active_count == 0 and curr_r:
        active_count = 1

    upcoming_count = sum(1 for r in all_releases if (r.get("prod_deploy_date") or "") > (curr_r.get("prod_deploy_date", "") if curr_r else now_iso))

    monitored_pool = [r for r in (prev_r, curr_r, next_r) if r is not None]
    avg_readiness = (sum(r.get("readiness_pct", 0) for r in monitored_pool) / len(monitored_pool)) if monitored_pool else 100.0

    # --------------------------------------------------------------------------
    # 4. Strict Grafana Metric Ribbon (ui.grafana_stat_card)
    # --------------------------------------------------------------------------
    kpi_col1, kpi_col2, kpi_col3, kpi_col4 = st.columns(4)

    cur_label = curr_r["release_id"] if curr_r else "None"
    cur_date = curr_r["prod_deploy_date"] if curr_r else "—"
    cur_state = curr_r["state"] if curr_r else ""

    days_to_cutover = 0
    try:
        days_to_cutover = (datetime.strptime(cur_date, "%Y-%m-%d").date() - datetime.strptime(now_iso, "%Y-%m-%d").date()).days
    except Exception:
        pass

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
            delta=f"Gate: {curr_r['readiness_pct']:.0f}%" if curr_r else "100%",
            state=k1_state,
        ), unsafe_allow_html=True)

    with kpi_col2:
        st.markdown(ui.grafana_stat_card(
            label="Active In-Flight Pipeline",
            value=f"{active_count} Active",
            color="#5794f2",
            subtext=f"{uat_count} in UAT · {sit_count} in SIT · {dev_count} in DEV",
            badge="LIVE TESTING",
            sparkline_vals=[dev_count, sit_count, uat_count, active_count],
            delta="DEV➔SIT➔UAT Flow",
            state="ok",
        ), unsafe_allow_html=True)

    with kpi_col3:
        next_date = next_r["prod_deploy_date"] if next_r else "—"
        next_label = next_r["release_id"] if next_r else "None"
        st.markdown(ui.grafana_stat_card(
            label="Scheduled Roadmap",
            value=f"{upcoming_count} Planned",
            color="#73bf69",
            subtext=f"Next: {next_label} ({next_date})",
            badge="ROADMAP",
            sparkline_vals=[upcoming_count, max(0, upcoming_count - 1), upcoming_count],
            delta=f"Target: {next_date}",
            state="ok",
        ), unsafe_allow_html=True)

    with kpi_col4:
        st.markdown(ui.grafana_stat_card(
            label="Pipeline Gate Readiness",
            value=f"{avg_readiness:.0f}%",
            color="#73bf69" if avg_readiness >= 80 else "#ff9830",
            subtext=f"{len(monitored_pool)} release gates monitored",
            badge="ON SCHEDULE" if avg_readiness >= 80 else "ATTENTION",
            donut_pct=avg_readiness,
            delta="✓ Gate Exit Score",
            state="ok" if avg_readiness >= 80 else "pending",
        ), unsafe_allow_html=True)

    # --------------------------------------------------------------------------
    # 5. RELEASE CUTOFF ALERT STRIP (Compact Ticker)
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

        chips_html = []
        for a in deduped[:4]:
            border_c = "#f2495c" if a["chip_cls"] == "firing" else "#ff9830"
            bg_c = "rgba(242, 73, 92, 0.12)" if a["chip_cls"] == "firing" else "rgba(255, 152, 48, 0.12)"
            chips_html.append(
                f"<span style='background:{bg_c};border:1px solid {border_c};border-radius:2px;padding:2px 8px;font-size:10px;display:inline-flex;align-items:center;gap:5px;white-space:nowrap;'>"
                f"<b style='color:var(--ink);'>{a['state']}.{a['release_id']}</b>"
                f"<span style='color:{border_c};font-weight:700;'>{a['label']}</span>"
                f"</span>"
            )

        n_firing = sum(1 for a in deduped if a["chip_cls"] == "firing")
        dot_color = "#f2495c" if n_firing else "#ff9830"

        render_html(f"""
        <div style="display:flex;align-items:center;gap:8px;padding:2px 8px;background:rgba(255,255,255,0.02);border:1px solid #22252b;border-radius:2px;margin-top:2px;margin-bottom:4px;overflow-x:auto;">
          <div style="display:flex;align-items:center;gap:5px;flex-shrink:0;">
            <span style="width:7px;height:7px;border-radius:50%;background:{dot_color};box-shadow:0 0 5px {dot_color};"></span>
            <span style="font-size:10px;font-weight:700;color:var(--mute);text-transform:uppercase;letter-spacing:0.04em;">Gate Alerts:</span>
          </div>
          <div style="display:flex;align-items:center;gap:6px;flex-wrap:nowrap;">
            {''.join(chips_html)}
          </div>
        </div>
        """)

    # --------------------------------------------------------------------------
    # 6. 3 EXECUTIVE STORYBOARD DECKS (Previous, Current, Upcoming — Zero-Scroll)
    # --------------------------------------------------------------------------
    story_c1, story_c2, story_c3 = st.columns(3, gap="small")

    def _render_story_card(title: str, rel_data: dict | None, accent_color: str, icon: str):
        if not rel_data:
            return f'''
            <div style="background:#181b1f;border:1px solid #2c3235;border-top:3px solid #2c3235;border-radius:2px;padding:8px 10px;height:105px;display:flex;align-items:center;justify-content:center;">
              <div style="text-align:center;color:var(--mute);font-size:11px;">
                <div>{icon}</div>
                <div style="margin-top:2px;">No {title} Found</div>
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

        prod_env = "ENV05" if state_mmis == "NH" else ("PRM" if state_mmis == "ND" else "ENV30")
        is_deployed = str(pd_date) < now_iso
        status_label = "DEPLOYED" if is_deployed else ("ACTIVE DEV" if "Current" in title else "PLANNED")

        return f'''
        <div style="background:#181b1f;border:1px solid #2c3235;border-top:3px solid {accent_color};border-radius:2px;padding:6px 10px;height:105px;display:flex;flex-direction:column;justify-content:space-between;">
          <div style="display:flex;justify-content:space-between;align-items:center;">
            <div style="display:flex;align-items:center;gap:4px;font-size:9.5px;font-weight:700;color:var(--slate);text-transform:uppercase;letter-spacing:0.04em;">
              <span>{icon}</span> {title}
            </div>
            <span style="font-size:8.5px;font-weight:700;color:{accent_color};background:rgba(255,255,255,0.04);padding:1px 5px;border-radius:2px;">{status_label}</span>
          </div>

          <div style="display:flex;justify-content:space-between;align-items:baseline;margin:1px 0;">
            <div>
              <span style="font-size:14px;font-weight:800;color:var(--ink);font-family:var(--mono);line-height:1;">{rid}</span>
              <span style="font-size:9.5px;font-weight:600;color:{accent_color};margin-left:6px;">{state_mmis} Scope</span>
            </div>
            <div style="font-size:9.5px;font-weight:700;color:var(--slate);font-family:var(--mono);">
              Gate: <span style="color:{accent_color};">{readiness:.0f}%</span>
            </div>
          </div>

          <div style="background:#141619;border:1px solid #22252b;border-radius:2px;padding:3px 6px;display:grid;grid-template-columns:1fr 1fr 1fr 1.3fr;gap:4px;font-size:8.5px;font-family:var(--mono);">
            <div><span style="color:var(--mute);">DEV:</span> <span style="color:var(--slate);">{dev_f}</span></div>
            <div><span style="color:var(--mute);">SIT:</span> <span style="color:var(--slate);">{sit_f}</span></div>
            <div><span style="color:var(--mute);">UAT:</span> <span style="color:var(--slate);">{uat_f}</span></div>
            <div><span style="color:var(--mute);">PROD:</span> <span style="color:#38bdf8;font-weight:700;">{pd_date} ({prod_env})</span></div>
          </div>
        </div>
        '''

    with story_c1:
        render_html(_render_story_card("Previous Release", prev_r, "#73bf69", "📁"))

    with story_c2:
        render_html(_render_story_card("Current Release", curr_r, "#38bdf8", "🎯"))

    with story_c3:
        render_html(_render_story_card("Upcoming Release", next_r, "#f59e0b", "🚀"))

    # --------------------------------------------------------------------------
    # 7. MASTER-DETAIL ROADMAP INSPECTOR (Clean Subtabs / Switcher, Zero-Scroll)
    # --------------------------------------------------------------------------
    release_dict = {r["release_id"]: r for r in all_releases}
    all_rel_ids = list(release_dict.keys())

    # Ensure valid target release selected in state
    cur_sel = st.session_state.get("global_release_selection")
    if "rp_target_rel_picker" not in st.session_state or st.session_state["rp_target_rel_picker"] not in all_rel_ids:
        if cur_sel and cur_sel in all_rel_ids:
            st.session_state["rp_target_rel_picker"] = cur_sel
        elif curr_r and curr_r["release_id"] in all_rel_ids:
            st.session_state["rp_target_rel_picker"] = curr_r["release_id"]
        elif all_rel_ids:
            st.session_state["rp_target_rel_picker"] = all_rel_ids[0]

    chosen_rel = st.session_state.get("rp_target_rel_picker")
    rel_data = release_dict.get(chosen_rel) or (all_releases[0] if all_releases else {})

    sub_c1, sub_c2 = st.columns([1.5, 3.5])
    with sub_c1:
        st.markdown("<div style='font-size:10.5px;font-weight:700;color:var(--mute);text-transform:uppercase;letter-spacing:0.04em;padding-top:4px;'>Roadmap &amp; Flight Deck</div>", unsafe_allow_html=True)
    with sub_c2:
        mode = st.radio(
            "View Mode",
            ["🎯 Release Flight Deck", "📋 Multi-Release Roadmap Matrix"],
            horizontal=True,
            key="rp_view_submode",
            label_visibility="collapsed"
        )

    if mode == "🎯 Release Flight Deck":
        d_c1, d_c2 = st.columns([1.0, 1.8], gap="small")
        with d_c1:
            st.selectbox(
                "Select Target Release",
                all_rel_ids,
                key="rp_target_rel_picker",
                on_change=_on_release_filter_change,
                label_visibility="collapsed"
            )

            if rel_data:
                st_code = rel_data.get('state', 'NH')
                prod_env = "PROD / ENV05" if st_code == "NH" else ("PROD / PRM" if st_code == "ND" else "PROD / ENV30")
                is_deployed = str(rel_data.get('prod_deploy_date', 'TBD')) < now_iso
                status_color = "#73bf69" if is_deployed else "#38bdf8"
                status_text = "DEPLOYED" if is_deployed else "ACTIVE FLIGHT"

                st.markdown(f'''
                <div style="background:#141619;border:1px solid #2c3235;border-left:3px solid {status_color};border-radius:2px;padding:8px 10px;height:120px;display:flex;flex-direction:column;justify-content:space-between;">
                    <div style="display:flex;justify-content:space-between;align-items:center;">
                        <div style="font-size:9px;color:var(--mute);text-transform:uppercase;">Selected Target</div>
                        <span style="font-size:8px;font-weight:700;color:{status_color};background:rgba(255,255,255,0.05);padding:1px 5px;border-radius:2px;">{status_text}</span>
                    </div>
                    <div>
                        <div style="font-size:14px;font-weight:800;color:var(--text);">{st_code}.{rel_data.get('release_id')}</div>
                        <div style="font-size:10px;color:var(--slate);margin-top:2px;">
                            <b>Target PROD:</b> <span style="color:#38bdf8;font-weight:700;">{prod_env}</span><br/>
                            <b>RM:</b> {rel_data.get('state_rm_name', 'Unassigned')} &bull; <b>Lead:</b> {rel_data.get('tech_lead_name', 'Unassigned')}
                        </div>
                    </div>
                    <div style="font-size:9.5px;font-family:var(--mono);color:var(--slate);">
                        Cutover: <b style="color:var(--ink);">{rel_data.get('prod_deploy_date', 'TBD')}</b> | Readiness: <b style="color:{status_color};">{rel_data.get('readiness_pct', 0):.0f}%</b>
                    </div>
                </div>
                ''', unsafe_allow_html=True)

        with d_c2:
            if rel_data:
                st_code = rel_data.get('state', 'NH')
                prod_env_label = "PROD / ENV05" if st_code == "NH" else ("PROD / PRM" if st_code == "ND" else "PROD / ENV30")
                st.markdown('''
                <div style="background:#181b1f;border:1px solid #2c3235;border-radius:2px;padding:6px 10px;height:150px;overflow-y:auto;">
                    <div style="font-size:10.5px;font-weight:700;color:var(--text);margin-bottom:4px;">Milestone Execution Ledger</div>
                    <table class="tblx" style="width:100%;font-size:10px;">
                        <tr><th style="text-align:left;">Phase</th><th style="text-align:left;">Target Environment</th><th style="text-align:left;">Target Date</th><th style="text-align:right;">Status</th></tr>
                        <tr><td>DEV Freeze</td><td>Build-76 / ENV52</td><td style="font-family:var(--mono);">{dev}</td><td style="text-align:right;">{d_stat}</td></tr>
                        <tr><td>SIT Gate</td><td>SIT QA / ENV57</td><td style="font-family:var(--mono);">{sit}</td><td style="text-align:right;">{s_stat}</td></tr>
                        <tr><td>UAT Gate</td><td>Acceptance / ENV04</td><td style="font-family:var(--mono);">{uat}</td><td style="text-align:right;">{u_stat}</td></tr>
                        <tr><td>PROD Cutover</td><td style="font-weight:700;color:#38bdf8;">{prod_env}</td><td style="font-family:var(--mono);font-weight:700;color:var(--text);">{prod}</td><td style="text-align:right;">{p_stat}</td></tr>
                    </table>
                </div>
                '''.format(
                    dev=rel_data.get('dev_end_date', 'TBD'), d_stat='<span style="color:#73bf69;font-weight:700;">PASSED</span>' if str(rel_data.get('dev_end_date', '')) < now_iso else '<span style="color:#5794f2;font-weight:700;">PENDING</span>',
                    sit=rel_data.get('sit_end_date', 'TBD'), s_stat='<span style="color:#73bf69;font-weight:700;">PASSED</span>' if str(rel_data.get('sit_end_date', '')) < now_iso else '<span style="color:#5794f2;font-weight:700;">PENDING</span>',
                    uat=rel_data.get('uat_end_date', 'TBD'), u_stat='<span style="color:#73bf69;font-weight:700;">PASSED</span>' if str(rel_data.get('uat_end_date', '')) < now_iso else '<span style="color:#5794f2;font-weight:700;">PENDING</span>',
                    prod_env=prod_env_label,
                    prod=rel_data.get('prod_deploy_date', 'TBD'), p_stat='<span style="color:#73bf69;font-weight:700;">PASSED</span>' if str(rel_data.get('prod_deploy_date', '')) < now_iso else '<span style="color:#5794f2;font-weight:700;">PENDING</span>'
                ), unsafe_allow_html=True)
    else:
        roadmap_rows = []
        for r in all_releases:
            st_code = r.get("state", "NH")
            prod_env = "PROD (ENV05)" if st_code == "NH" else ("PROD (PRM)" if st_code == "ND" else "PROD (ENV30)")
            d_end = r.get('prod_deploy_date', 'TBD')
            is_deployed = str(d_end) < now_iso
            status = '<span style="color:#73bf69;font-weight:700;">DEPLOYED</span>' if is_deployed else '<span style="color:#ff9830;font-weight:700;">SCHEDULED</span>'
            roadmap_rows.append(
                f"<tr>"
                f"<td><span style='font-weight:700;color:var(--slate);'>{st_code}</span></td>"
                f"<td><b>{r.get('release_id')}</b></td>"
                f"<td style='font-family:var(--mono);'>{r.get('dev_end_date', 'TBD')}</td>"
                f"<td style='font-family:var(--mono);'>{r.get('sit_end_date', 'TBD')}</td>"
                f"<td style='font-family:var(--mono);'>{r.get('uat_end_date', 'TBD')}</td>"
                f"<td style='font-family:var(--mono);font-weight:700;color:var(--text);'>{d_end}</td>"
                f"<td style='font-size:10px;color:#38bdf8;'>{prod_env}</td>"
                f"<td style='text-align:right;'>{status}</td>"
                f"</tr>"
            )

        st.markdown(f'''
        <div style="background:#181b1f;border:1px solid #2c3235;border-radius:2px;max-height:150px;overflow-y:auto;">
            <table class="tblx" style="width:100%;font-size:10px;">
                <tr style="position:sticky;top:0;background:#141619;border-bottom:1px solid #2c3235;z-index:2;">
                    <th style="text-align:left;">State</th>
                    <th style="text-align:left;">Release</th>
                    <th style="text-align:left;">DEV Freeze</th>
                    <th style="text-align:left;">SIT Gate</th>
                    <th style="text-align:left;">UAT Gate</th>
                    <th style="text-align:left;">PROD Cutover</th>
                    <th style="text-align:left;">Production Env</th>
                    <th style="text-align:right;">Status</th>
                </tr>
                {''.join(roadmap_rows)}
            </table>
        </div>
        ''', unsafe_allow_html=True)

