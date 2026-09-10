"""
release_plan.py
===============
Enterprise Schedule Release Plan & Environment Pipeline Workspace.
Clean, static, high-density release tracking organized strictly into:
  1. Current Release (Immediate Cutover Focus)
  2. Active Releases (In-Flight across DEV / SIT / UAT)
  3. Upcoming Releases (Scheduled Roadmap)
  4. Completed Releases (Historical Archive)

Strict RBAC isolation enforced silently under the hood without narrative banners.
"""

from __future__ import annotations

import json
from datetime import datetime
import pandas as pd
import streamlit as st

from db import (
    get_connection,
    get_release_schedules,
)


def render_html(html_str: str) -> None:
    """Render HTML safely without markdown 4-space code-block escaping."""
    cleaned = "\n".join(line.strip() for line in html_str.splitlines() if line.strip())
    st.markdown(cleaned, unsafe_allow_html=True)


def _calc_stage_pill(s_date: str | None, f_date: str | None, now_iso: str) -> tuple[str, str, str, str]:
    """Returns (label, bg, color, border)."""
    if not s_date and not f_date:
        return "SCHEDULED", "rgba(255,255,255,0.04)", "#64748b", "#1e293b"
    eff_start = s_date or f_date
    eff_finish = f_date or s_date
    if eff_finish < now_iso:
        return "COMPLETED", "rgba(16,185,129,0.12)", "#34d399", "rgba(16,185,129,0.3)"
    elif eff_start <= now_iso <= eff_finish:
        return "ACTIVE TODAY", "rgba(56,189,248,0.2)", "#38bdf8", "#38bdf8"
    else:
        return "SCHEDULED", "rgba(255,255,255,0.04)", "#94a3b8", "#1e293b"


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


def render_release_plan_workspace(db_path: str) -> None:
    """Render static, high-density Schedule Release Plan workspace."""
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
    # 2. Header & State Selection (Clean, No RBAC Banners)
    # --------------------------------------------------------------------------
    h_col1, h_col2 = st.columns([3.0, 1.2])
    with h_col1:
        render_html("""
        <div style="display:flex;align-items:center;gap:12px;padding:4px 0 8px 0;">
          <span style="font-size:24px;">📅</span>
          <div>
            <div style="font-size:18px;font-weight:900;color:#f8fafc;letter-spacing:-0.02em;text-transform:uppercase;">
              Schedule Release Plan & Environment Pipeline
            </div>
            <div style="font-size:11px;color:#94a3b8;font-family:var(--mono);margin-top:2px;">
              CURRENT RELEASES • ACTIVE IN-FLIGHT PIPELINE • UPCOMING ROADMAP
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
    # 4. BIG Executive KPI Metric Cards (Enhanced, Bold & Spacious)
    # --------------------------------------------------------------------------
    kpi_col1, kpi_col2, kpi_col3 = st.columns(3)

    cur_rel = current_releases[0] if current_releases else None
    cur_label = cur_rel["release_id"] if cur_rel else "None"
    cur_date = cur_rel["prod_deploy_date"] if cur_rel else "—"
    cur_state = cur_rel["state"] if cur_rel else ""
    
    days_to_cutover = 0
    try:
        days_to_cutover = (datetime.strptime(cur_date, "%Y-%m-%d").date() - datetime.strptime(now_iso, "%Y-%m-%d").date()).days
    except Exception:
        pass

    if days_to_cutover == 0:
        cur_badge_html = '<span style="font-size:10px;font-weight:800;padding:3px 9px;border-radius:12px;background:rgba(239,68,68,0.25);color:#fca5a5;border:1px solid #ef4444;letter-spacing:0.04em;">● CUTOVER TODAY</span>'
    elif days_to_cutover <= 3:
        cur_badge_html = f'<span style="font-size:10px;font-weight:800;padding:3px 9px;border-radius:12px;background:rgba(245,158,11,0.25);color:#fcd34d;border:1px solid #f59e0b;letter-spacing:0.04em;">● {days_to_cutover} DAYS TO CUTOVER</span>'
    else:
        cur_badge_html = f'<span style="font-size:10px;font-weight:800;padding:3px 9px;border-radius:12px;background:rgba(56,189,248,0.2);color:#38bdf8;border:1px solid rgba(56,189,248,0.4);letter-spacing:0.04em;">● {days_to_cutover} DAYS TO CUTOVER</span>'

    uat_count = sum(1 for r in active_releases if _get_stage_info(r, now_iso)[0].startswith("State UAT"))
    sit_count = sum(1 for r in active_releases if _get_stage_info(r, now_iso)[0].startswith("SIT"))
    dev_count = sum(1 for r in active_releases if _get_stage_info(r, now_iso)[0].startswith("Development"))

    with kpi_col1:
        render_html(f"""
        <div style="background:linear-gradient(145deg, #0f172a, #1e293b);border:1px solid rgba(239,68,68,0.4);border-top:4px solid #ef4444;border-radius:8px;padding:16px 20px;box-shadow:0 6px 18px rgba(0,0,0,0.3);">
          <div style="display:flex;justify-content:space-between;align-items:center;">
            <span style="font-size:10.5px;font-weight:800;color:#94a3b8;text-transform:uppercase;letter-spacing:0.08em;">Immediate Cutover Target</span>
            {cur_badge_html}
          </div>
          <div style="display:flex;align-items:baseline;gap:10px;margin-top:6px;">
            <span style="font-size:28px;font-weight:900;color:#f8fafc;font-family:var(--mono);letter-spacing:-0.02em;">{cur_label}</span>
            <span style="font-size:11px;font-weight:800;color:#cbd5e1;background:rgba(255,255,255,0.08);padding:2px 8px;border-radius:4px;font-family:var(--mono);">{cur_state} MMIS</span>
          </div>
          <div style="display:flex;justify-content:space-between;align-items:center;margin-top:8px;padding-top:8px;border-top:1px solid rgba(255,255,255,0.08);font-size:11.5px;">
            <span style="color:#94a3b8;">Target Cutover: <b style="color:#f8fafc;font-family:var(--mono);font-size:12px;">{cur_date}</b></span>
            <span style="color:#10b981;font-weight:800;">Gate Readiness: {cur_rel['readiness_pct']:.0f}%</span>
          </div>
        </div>
        """)

    with kpi_col2:
        render_html(f"""
        <div style="background:linear-gradient(145deg, #0f172a, #1e293b);border:1px solid rgba(56,189,248,0.4);border-top:4px solid #38bdf8;border-radius:8px;padding:16px 20px;box-shadow:0 6px 18px rgba(0,0,0,0.3);">
          <div style="display:flex;justify-content:space-between;align-items:center;">
            <span style="font-size:10.5px;font-weight:800;color:#94a3b8;text-transform:uppercase;letter-spacing:0.08em;">Active In-Flight Pipeline</span>
            <span style="font-size:10px;font-weight:800;color:#38bdf8;background:rgba(56,189,248,0.15);padding:3px 9px;border-radius:10px;border:1px solid rgba(56,189,248,0.35);">LIVE TESTING</span>
          </div>
          <div style="display:flex;align-items:baseline;gap:8px;margin-top:6px;">
            <span style="font-size:28px;font-weight:900;color:#38bdf8;font-family:var(--mono);letter-spacing:-0.02em;">{len(active_releases)} Active Releases</span>
          </div>
          <div style="display:flex;justify-content:space-between;align-items:center;margin-top:8px;padding-top:8px;border-top:1px solid rgba(255,255,255,0.08);font-size:11.5px;color:#94a3b8;">
            <span><b style="color:#38bdf8;">{uat_count}</b> in UAT</span>
            <span>•</span>
            <span><b style="color:#fcd34d;">{sit_count}</b> in SIT</span>
            <span>•</span>
            <span><b style="color:#a78bfa;">{dev_count}</b> in DEV</span>
          </div>
        </div>
        """)

    with kpi_col3:
        next_up_date = upcoming_releases[0]["prod_deploy_date"] if upcoming_releases else "—"
        next_up_rel = upcoming_releases[0]["release_id"] if upcoming_releases else "None"
        render_html(f"""
        <div style="background:linear-gradient(145deg, #0f172a, #1e293b);border:1px solid rgba(16,185,129,0.4);border-top:4px solid #10b981;border-radius:8px;padding:16px 20px;box-shadow:0 6px 18px rgba(0,0,0,0.3);">
          <div style="display:flex;justify-content:space-between;align-items:center;">
            <span style="font-size:10.5px;font-weight:800;color:#94a3b8;text-transform:uppercase;letter-spacing:0.08em;">Scheduled Roadmap Horizon</span>
            <span style="font-size:10px;font-weight:800;color:#10b981;background:rgba(16,185,129,0.15);padding:3px 9px;border-radius:10px;border:1px solid rgba(16,185,129,0.35);">ROADMAP</span>
          </div>
          <div style="display:flex;align-items:baseline;gap:8px;margin-top:6px;">
            <span style="font-size:28px;font-weight:900;color:#10b981;font-family:var(--mono);letter-spacing:-0.02em;">{len(upcoming_releases)} Scheduled</span>
          </div>
          <div style="display:flex;justify-content:space-between;align-items:center;margin-top:8px;padding-top:8px;border-top:1px solid rgba(255,255,255,0.08);font-size:11.5px;color:#94a3b8;">
            <span>Next Target: <b style="color:#f8fafc;font-family:var(--mono);font-size:12px;">{next_up_rel}</b></span>
            <span style="color:#34d399;font-family:var(--mono);font-size:12px;font-weight:700;">{next_up_date}</span>
          </div>
        </div>
        """)

    st.markdown('<div style="margin-top:14px;"></div>', unsafe_allow_html=True)

    # --------------------------------------------------------------------------
    # SECTION 1: HERO CURRENT RELEASE FLIGHT DECK (ENLARGED & PROMINENT)
    # --------------------------------------------------------------------------
    render_html("""
    <div style="font-size:13px;font-weight:800;color:#f8fafc;text-transform:uppercase;letter-spacing:0.08em;margin-bottom:8px;display:flex;align-items:center;gap:8px;">
      <span style="color:#ef4444;font-size:15px;">🚨</span>
      <span>1. Current Release — Live Environment Flight Deck</span>
    </div>
    """)

    if not current_releases:
        st.info("No active cutover release found.")
    else:
        for cur_rel_item in current_releases:
            stg_name, env_tag, badge_lbl = _get_stage_info(cur_rel_item, now_iso)
            badge_bg = "rgba(239,68,68,0.25)" if "TODAY" in badge_lbl else "rgba(245,158,11,0.22)"
            badge_fg = "#fca5a5" if "TODAY" in badge_lbl else "#fcd34d"
            badge_bd = "#ef4444" if "TODAY" in badge_lbl else "#f59e0b"

            dev_s, dev_f = cur_rel_item.get("dev_start_date"), cur_rel_item.get("dev_end_date")
            sit_s, sit_f = cur_rel_item.get("sit_start_date"), cur_rel_item.get("sit_end_date")
            uat_s, uat_f = cur_rel_item.get("uat_start_date"), cur_rel_item.get("uat_end_date")
            gn_d = cur_rel_item.get("go_nogo_date")
            p_d = cur_rel_item.get("prod_deploy_date")

            stg_dev_l, stg_dev_bg, stg_dev_fg, stg_dev_bd = _calc_stage_pill(dev_s, dev_f, now_iso)
            stg_sit_l, stg_sit_bg, stg_sit_fg, stg_sit_bd = _calc_stage_pill(sit_s, sit_f, now_iso)
            stg_uat_l, stg_uat_bg, stg_uat_fg, stg_uat_bd = _calc_stage_pill(uat_s, uat_f, now_iso)
            stg_gn_l, stg_gn_bg, stg_gn_fg, stg_gn_bd = _calc_stage_pill(gn_d, gn_d, now_iso)
            stg_prod_l, stg_prod_bg, stg_prod_fg, stg_prod_bd = _calc_stage_pill(p_d, p_d, now_iso)

            env_name_dev = "ENV52 Dev" if cur_rel_item["state"] == "NH" else "Build-76 Dev"
            env_name_sit = "ENV57 / ENV53" if cur_rel_item["state"] == "NH" else "SIT QA"
            env_name_uat = "ENV04 UAT" if cur_rel_item["state"] == "NH" else "State Acceptance"
            env_name_prod = "ENV05 Live" if cur_rel_item["state"] == "NH" else "PROD Cutover"

            top_border_dev = "#10b981" if "COMPLETED" in stg_dev_l else ("#38bdf8" if "IN PROGRESS" in stg_dev_l else "#334155")
            top_border_sit = "#10b981" if "COMPLETED" in stg_sit_l else ("#38bdf8" if "IN PROGRESS" in stg_sit_l else "#334155")
            top_border_uat = "#10b981" if "COMPLETED" in stg_uat_l else ("#38bdf8" if "IN PROGRESS" in stg_uat_l else "#334155")
            top_border_gn = "#10b981" if "COMPLETED" in stg_gn_l else ("#f59e0b" if "IN PROGRESS" in stg_gn_l else "#334155")
            top_border_prod = "#10b981" if "COMPLETED" in stg_prod_l else ("#ef4444" if "TODAY" in stg_prod_l else "#334155")

            render_html(f"""
            <div style="background:#0f172a;border:1px solid #1e293b;border-left:6px solid #ef4444;border-radius:10px;padding:16px 20px;margin-bottom:16px;box-shadow:0 6px 20px rgba(0,0,0,0.3);">
              <!-- Top Row: Release Meta Strip -->
              <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px;border-bottom:1px solid #1e293b;padding-bottom:12px;margin-bottom:14px;">
                <div style="display:flex;align-items:center;gap:12px;">
                  <span style="font-size:24px;font-weight:900;color:#f8fafc;font-family:var(--mono);letter-spacing:-0.02em;">{cur_rel_item['release_id']}</span>
                  <span style="font-size:11px;font-weight:800;background:rgba(255,255,255,0.08);color:#f8fafc;padding:3px 8px;border-radius:5px;font-family:var(--mono);">
                    {cur_rel_item['state']} MMIS ({cur_rel_item['quarter']} {cur_rel_item['year']})
                  </span>
                  <span style="font-size:10.5px;font-weight:800;padding:3px 10px;border-radius:12px;background:{badge_bg};color:{badge_fg};border:1px solid {badge_bd};letter-spacing:0.04em;">
                    ● {badge_lbl}
                  </span>
                </div>
                <div style="display:flex;align-items:center;gap:20px;font-size:12.5px;">
                  <div><span style="color:#94a3b8;">Current Stage:</span> <b style="color:#38bdf8;">{stg_name} ({env_tag})</b></div>
                  <div><span style="color:#94a3b8;">Target Cutover:</span> <b style="color:#f8fafc;font-family:var(--mono);font-size:13px;">{p_d}</b></div>
                  <div><span style="color:#94a3b8;">Gate Readiness:</span> <b style="color:#10b981;font-family:var(--mono);font-size:13px;">{cur_rel_item['readiness_pct']:.0f}%</b></div>
                </div>
              </div>

              <!-- Expanded 5-Stage Stepper Grid -->
              <div style="display:grid;grid-template-columns:repeat(5, 1fr);gap:12px;">
                <!-- 1. DEV -->
                <div style="background:#020617;border:1px solid {stg_dev_bd};border-top:4px solid {top_border_dev};border-radius:8px;padding:12px 14px;display:flex;flex-direction:column;justify-content:space-between;min-height:105px;">
                  <div>
                    <div style="display:flex;justify-content:space-between;align-items:center;">
                      <span style="font-size:10.5px;font-weight:800;color:#94a3b8;text-transform:uppercase;letter-spacing:0.05em;">1. DEV BUILD</span>
                      <span style="font-size:14px;">🛠️</span>
                    </div>
                    <div style="font-size:13px;font-weight:800;color:#f8fafc;margin-top:4px;">{env_name_dev}</div>
                    <div style="font-size:11px;color:#cbd5e1;font-family:var(--mono);margin-top:4px;font-weight:600;">{dev_s or '—'} ➔ {dev_f or '—'}</div>
                  </div>
                  <div style="margin-top:8px;">
                    <span style="font-size:9.5px;font-weight:800;padding:3px 8px;border-radius:4px;background:{stg_dev_bg};color:{stg_dev_fg};border:1px solid {stg_dev_bd};">{stg_dev_l}</span>
                  </div>
                </div>

                <!-- 2. SIT -->
                <div style="background:#020617;border:1px solid {stg_sit_bd};border-top:4px solid {top_border_sit};border-radius:8px;padding:12px 14px;display:flex;flex-direction:column;justify-content:space-between;min-height:105px;">
                  <div>
                    <div style="display:flex;justify-content:space-between;align-items:center;">
                      <span style="font-size:10.5px;font-weight:800;color:#94a3b8;text-transform:uppercase;letter-spacing:0.05em;">2. SIT REGRESSION</span>
                      <span style="font-size:14px;">🧪</span>
                    </div>
                    <div style="font-size:13px;font-weight:800;color:#f8fafc;margin-top:4px;">{env_name_sit}</div>
                    <div style="font-size:11px;color:#cbd5e1;font-family:var(--mono);margin-top:4px;font-weight:600;">{sit_s or '—'} ➔ {sit_f or '—'}</div>
                  </div>
                  <div style="margin-top:8px;">
                    <span style="font-size:9.5px;font-weight:800;padding:3px 8px;border-radius:4px;background:{stg_sit_bg};color:{stg_sit_fg};border:1px solid {stg_sit_bd};">{stg_sit_l}</span>
                  </div>
                </div>

                <!-- 3. UAT -->
                <div style="background:#020617;border:1px solid {stg_uat_bd};border-top:4px solid {top_border_uat};border-radius:8px;padding:12px 14px;display:flex;flex-direction:column;justify-content:space-between;min-height:105px;">
                  <div>
                    <div style="display:flex;justify-content:space-between;align-items:center;">
                      <span style="font-size:10.5px;font-weight:800;color:#94a3b8;text-transform:uppercase;letter-spacing:0.05em;">3. STATE UAT</span>
                      <span style="font-size:14px;">📋</span>
                    </div>
                    <div style="font-size:13px;font-weight:800;color:#f8fafc;margin-top:4px;">{env_name_uat}</div>
                    <div style="font-size:11px;color:#cbd5e1;font-family:var(--mono);margin-top:4px;font-weight:600;">{uat_s or '—'} ➔ {uat_f or '—'}</div>
                  </div>
                  <div style="margin-top:8px;">
                    <span style="font-size:9.5px;font-weight:800;padding:3px 8px;border-radius:4px;background:{stg_uat_bg};color:{stg_uat_fg};border:1px solid {stg_uat_bd};">{stg_uat_l}</span>
                  </div>
                </div>

                <!-- 4. GO/NO-GO -->
                <div style="background:#020617;border:1px solid {stg_gn_bd};border-top:4px solid {top_border_gn};border-radius:8px;padding:12px 14px;display:flex;flex-direction:column;justify-content:space-between;min-height:105px;">
                  <div>
                    <div style="display:flex;justify-content:space-between;align-items:center;">
                      <span style="font-size:10.5px;font-weight:800;color:#94a3b8;text-transform:uppercase;letter-spacing:0.05em;">4. GO / NO-GO</span>
                      <span style="font-size:14px;">🚦</span>
                    </div>
                    <div style="font-size:13px;font-weight:800;color:#f8fafc;margin-top:4px;">Decision Board</div>
                    <div style="font-size:11px;color:#cbd5e1;font-family:var(--mono);margin-top:4px;font-weight:600;">{gn_d or 'Pre-Cutover'}</div>
                  </div>
                  <div style="margin-top:8px;">
                    <span style="font-size:9.5px;font-weight:800;padding:3px 8px;border-radius:4px;background:{stg_gn_bg};color:{stg_gn_fg};border:1px solid {stg_gn_bd};">{stg_gn_l}</span>
                  </div>
                </div>

                <!-- 5. PROD CUTOVER -->
                <div style="background:#020617;border:1px solid {stg_prod_bd};border-top:4px solid {top_border_prod};border-radius:8px;padding:12px 14px;display:flex;flex-direction:column;justify-content:space-between;min-height:105px;">
                  <div>
                    <div style="display:flex;justify-content:space-between;align-items:center;">
                      <span style="font-size:10.5px;font-weight:800;color:#94a3b8;text-transform:uppercase;letter-spacing:0.05em;">5. PROD LIVE</span>
                      <span style="font-size:14px;">🚀</span>
                    </div>
                    <div style="font-size:13px;font-weight:800;color:#f8fafc;margin-top:4px;">{env_name_prod}</div>
                    <div style="font-size:11px;color:#f8fafc;font-weight:800;font-family:var(--mono);margin-top:4px;">{p_d}</div>
                  </div>
                  <div style="margin-top:8px;">
                    <span style="font-size:9.5px;font-weight:800;padding:3px 8px;border-radius:4px;background:{stg_prod_bg};color:{stg_prod_fg};border:1px solid {stg_prod_bd};">{stg_prod_l}</span>
                  </div>
                </div>
              </div>
            </div>
            """)

    # --------------------------------------------------------------------------
    # SECTION 2: ACTIVE RELEASES (IN-FLIGHT) (HIGH-DENSITY MATRIX TABLE)
    # --------------------------------------------------------------------------
    render_html(f"""
    <div style="font-size:13px;font-weight:800;color:#f8fafc;text-transform:uppercase;letter-spacing:0.08em;margin:18px 0 8px 0;display:flex;align-items:center;gap:8px;">
      <span style="color:#38bdf8;font-size:15px;">⚡</span>
      <span>2. Active Releases (In-Flight across DEV / SIT / UAT) — {len(active_releases)} Releases</span>
    </div>
    """)

    if not active_releases:
        st.info("No in-flight releases currently active.")
    else:
        active_rows = []
        for idx, r in enumerate(active_releases):
            stg_name, env_tag, _ = _get_stage_info(r, now_iso)
            dev_str = f"{r.get('dev_start_date') or '—'} ➔ {r.get('dev_end_date') or '—'}"
            sit_str = f"{r.get('sit_start_date') or '—'} ➔ {r.get('sit_end_date') or '—'}"
            uat_str = f"{r.get('uat_start_date') or '—'} ➔ {r.get('uat_end_date') or '—'}"
            row_bg = "#0f172a" if idx % 2 == 0 else "#090e1a"
            readiness = r.get('readiness_pct', 0)
            
            row_html = f"""
            <tr style="background:{row_bg};border-bottom:1px solid #1e293b;font-size:12px;">
              <td style="padding:10px 14px;"><span style="font-size:11px;font-weight:800;padding:3px 8px;border-radius:4px;background:rgba(255,255,255,0.08);color:#f8fafc;font-family:var(--mono);">{r['state']}</span></td>
              <td style="padding:10px 14px;font-weight:800;color:#f8fafc;font-family:var(--mono);font-size:13px;">{r['release_id']}</td>
              <td style="padding:10px 14px;">
                <span style="background:rgba(56,189,248,0.14);color:#38bdf8;padding:3px 9px;border-radius:4px;font-weight:800;font-size:11.5px;border:1px solid rgba(56,189,248,0.3);">{stg_name}</span>
                <span style="color:#94a3b8;font-family:var(--mono);font-size:11px;margin-left:5px;">({env_tag})</span>
              </td>
              <td style="padding:10px 14px;color:#cbd5e1;font-family:var(--mono);">{dev_str}</td>
              <td style="padding:10px 14px;color:#cbd5e1;font-family:var(--mono);">{sit_str}</td>
              <td style="padding:10px 14px;color:#cbd5e1;font-family:var(--mono);">{uat_str}</td>
              <td style="padding:10px 14px;font-weight:800;color:#f8fafc;font-family:var(--mono);font-size:12.5px;">{r['prod_deploy_date']}</td>
              <td style="padding:10px 14px;">
                <div style="display:flex;align-items:center;gap:8px;min-width:110px;">
                  <div style="flex:1;background:rgba(255,255,255,0.08);border-radius:4px;height:8px;overflow:hidden;">
                    <div style="width:{readiness:.0f}%;background:linear-gradient(90deg,#10b981,#34d399);height:100%;border-radius:4px;"></div>
                  </div>
                  <span style="font-family:var(--mono);font-size:11.5px;font-weight:800;color:#34d399;">{readiness:.0f}%</span>
                </div>
              </td>
              <td style="padding:10px 14px;"><span style="font-size:10px;font-weight:800;padding:3px 9px;border-radius:12px;background:rgba(56,189,248,0.18);color:#38bdf8;border:1px solid rgba(56,189,248,0.4);letter-spacing:0.04em;">IN PROGRESS</span></td>
            </tr>
            """
            active_rows.append("\n".join(l.strip() for l in row_html.splitlines() if l.strip()))

        render_html(f"""
        <div style="border:1px solid #1e293b;border-radius:8px;background:#0f172a;overflow:hidden;margin-bottom:16px;box-shadow:0 4px 16px rgba(0,0,0,0.2);">
          <table style="width:100%;border-collapse:collapse;text-align:left;">
            <thead>
              <tr style="background:#1e293b;border-bottom:2px solid #334155;font-size:11px;font-weight:800;text-transform:uppercase;color:#cbd5e1;letter-spacing:0.06em;">
                <th style="padding:10px 14px;">State</th>
                <th style="padding:10px 14px;">Release ID</th>
                <th style="padding:10px 14px;">Current Stage & Environment</th>
                <th style="padding:10px 14px;">DEV Window</th>
                <th style="padding:10px 14px;">SIT Window</th>
                <th style="padding:10px 14px;">UAT Window</th>
                <th style="padding:10px 14px;">PROD Cutover</th>
                <th style="padding:10px 14px;">Gate Readiness</th>
                <th style="padding:10px 14px;">Status</th>
              </tr>
            </thead>
            <tbody>
              {"".join(active_rows)}
            </tbody>
          </table>
        </div>
        """)

    # --------------------------------------------------------------------------
    # SECTION 3: UPCOMING RELEASES (SCHEDULED ROADMAP)
    # --------------------------------------------------------------------------
    render_html(f"""
    <div style="font-size:13px;font-weight:800;color:#f8fafc;text-transform:uppercase;letter-spacing:0.08em;margin:18px 0 8px 0;display:flex;align-items:center;gap:8px;">
      <span style="color:#10b981;font-size:15px;">🗓️</span>
      <span>3. Upcoming Releases (Scheduled Roadmap Pipeline) — {len(upcoming_releases)} Releases</span>
    </div>
    """)

    if not upcoming_releases:
        st.info("No upcoming releases scheduled.")
    else:
        upcoming_rows = []
        for idx, r in enumerate(upcoming_releases):
            dev_start = r.get("dev_start_date") or "—"
            sit_start = r.get("sit_start_date") or "—"
            uat_start = r.get("uat_start_date") or "—"
            row_bg = "#0f172a" if idx % 2 == 0 else "#090e1a"

            row_html = f"""
            <tr style="background:{row_bg};border-bottom:1px solid #1e293b;font-size:12px;">
              <td style="padding:10px 14px;"><span style="font-size:11px;font-weight:800;padding:3px 8px;border-radius:4px;background:rgba(255,255,255,0.08);color:#f8fafc;font-family:var(--mono);">{r['state']}</span></td>
              <td style="padding:10px 14px;font-weight:800;color:#f8fafc;font-family:var(--mono);font-size:13px;">{r['release_id']}</td>
              <td style="padding:10px 14px;color:#cbd5e1;font-family:var(--mono);">{r['quarter']} {r['year']}</td>
              <td style="padding:10px 14px;color:#94a3b8;font-family:var(--mono);">{dev_start}</td>
              <td style="padding:10px 14px;color:#94a3b8;font-family:var(--mono);">{sit_start}</td>
              <td style="padding:10px 14px;color:#94a3b8;font-family:var(--mono);">{uat_start}</td>
              <td style="padding:10px 14px;font-weight:800;color:#f8fafc;font-family:var(--mono);font-size:12.5px;">{r['prod_deploy_date']}</td>
              <td style="padding:10px 14px;"><span style="font-size:10px;font-weight:800;padding:3px 9px;border-radius:12px;background:rgba(255,255,255,0.06);color:#94a3b8;border:1px solid #334155;letter-spacing:0.04em;">SCHEDULED</span></td>
            </tr>
            """
            upcoming_rows.append("\n".join(l.strip() for l in row_html.splitlines() if l.strip()))

        render_html(f"""
        <div style="border:1px solid #1e293b;border-radius:8px;background:#0f172a;overflow:hidden;margin-bottom:16px;box-shadow:0 4px 16px rgba(0,0,0,0.2);">
          <table style="width:100%;border-collapse:collapse;text-align:left;">
            <thead>
              <tr style="background:#1e293b;border-bottom:2px solid #334155;font-size:11px;font-weight:800;text-transform:uppercase;color:#cbd5e1;letter-spacing:0.06em;">
                <th style="padding:10px 14px;">State</th>
                <th style="padding:10px 14px;">Release ID</th>
                <th style="padding:10px 14px;">Quarter / Cadence</th>
                <th style="padding:10px 14px;">DEV Start</th>
                <th style="padding:10px 14px;">SIT Start</th>
                <th style="padding:10px 14px;">UAT Start</th>
                <th style="padding:10px 14px;">PROD Cutover</th>
                <th style="padding:10px 14px;">Status</th>
              </tr>
            </thead>
            <tbody>
              {"".join(upcoming_rows)}
            </tbody>
          </table>
        </div>
        """)

    # --------------------------------------------------------------------------
    # SECTION 4: COMPLETED RELEASES (COLLAPSIBLE HISTORICAL ARCHIVE)
    # --------------------------------------------------------------------------
    with st.expander(f"📁 Completed Releases (Historical Archive) — {len(completed_releases)} Releases"):
        if not completed_releases:
            st.info("No completed releases.")
        else:
            comp_rows = []
            for idx, r in enumerate(completed_releases):
                row_bg = "#020617" if idx % 2 == 0 else "#0b1220"
                row_html = f"""
                <tr style="background:{row_bg};border-bottom:1px solid #1e293b;font-size:11.5px;">
                  <td style="padding:8px 12px;"><span style="font-family:var(--mono);font-size:11px;font-weight:700;color:#94a3b8;">{r['state']}</span></td>
                  <td style="padding:8px 12px;font-weight:800;color:#f8fafc;font-family:var(--mono);">{r['release_id']}</td>
                  <td style="padding:8px 12px;color:#cbd5e1;font-family:var(--mono);">{r['quarter']} {r['year']}</td>
                  <td style="padding:8px 12px;color:#f8fafc;font-family:var(--mono);font-weight:700;">{r['prod_deploy_date']}</td>
                  <td style="padding:8px 12px;"><span style="font-size:9.5px;font-weight:800;padding:2px 7px;border-radius:8px;background:rgba(16,185,129,0.15);color:#34d399;border:1px solid rgba(16,185,129,0.35);">✓ DEPLOYED (100%)</span></td>
                </tr>
                """
                comp_rows.append("\n".join(l.strip() for l in row_html.splitlines() if l.strip()))

            render_html(f"""
            <div style="border:1px solid #1e293b;border-radius:6px;background:#020617;max-height:240px;overflow-y:auto;">
              <table style="width:100%;border-collapse:collapse;text-align:left;">
                <thead>
                  <tr style="background:#1e293b;font-size:10px;font-weight:800;color:#cbd5e1;text-transform:uppercase;letter-spacing:0.05em;">
                    <th style="padding:8px 12px;">State</th>
                    <th style="padding:8px 12px;">Release ID</th>
                    <th style="padding:8px 12px;">Quarter</th>
                    <th style="padding:8px 12px;">Cutover Date</th>
                    <th style="padding:8px 12px;">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {"".join(comp_rows)}
                </tbody>
              </table>
            </div>
            """)

    conn.close()

