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
        <div style="display:flex;align-items:center;gap:8px;padding:2px 0 6px 0;">
          <span style="font-size:18px;">📅</span>
          <div>
            <div style="font-size:14px;font-weight:800;color:#f8fafc;letter-spacing:-0.02em;text-transform:uppercase;">
              Schedule Release Plan & Environment Pipeline
            </div>
            <div style="font-size:10px;color:#94a3b8;font-family:var(--mono);">
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
    # 4. Top Static KPI Ribbon
    # --------------------------------------------------------------------------
    kpi_col1, kpi_col2, kpi_col3 = st.columns(3)

    cur_label = current_releases[0]["release_id"] if current_releases else "None"
    cur_date = current_releases[0]["prod_deploy_date"] if current_releases else "—"
    cur_days = "TODAY" if cur_date == now_iso else cur_date

    with kpi_col1:
        render_html(f"""
        <div style="background:#0f172a;border:1px solid #1e293b;border-top:3px solid #ef4444;border-radius:6px;padding:8px 12px;">
          <div style="font-size:9.5px;font-weight:700;color:#94a3b8;text-transform:uppercase;letter-spacing:0.05em;">Current Cutover Release</div>
          <div style="font-size:18px;font-weight:800;color:#f8fafc;font-family:var(--mono);margin-top:2px;">{cur_label}</div>
          <div style="font-size:10px;color:#ef4444;font-weight:700;margin-top:2px;">Cutover Date: {cur_days}</div>
        </div>
        """)

    with kpi_col2:
        render_html(f"""
        <div style="background:#0f172a;border:1px solid #1e293b;border-top:3px solid #38bdf8;border-radius:6px;padding:8px 12px;">
          <div style="font-size:9.5px;font-weight:700;color:#94a3b8;text-transform:uppercase;letter-spacing:0.05em;">Active In-Flight Releases</div>
          <div style="font-size:18px;font-weight:800;color:#38bdf8;font-family:var(--mono);margin-top:2px;">{len(active_releases)} Active</div>
          <div style="font-size:10px;color:#94a3b8;margin-top:2px;">Across DEV, SIT, and UAT Pipelines</div>
        </div>
        """)

    with kpi_col3:
        render_html(f"""
        <div style="background:#0f172a;border:1px solid #1e293b;border-top:3px solid #10b981;border-radius:6px;padding:8px 12px;">
          <div style="font-size:9.5px;font-weight:700;color:#94a3b8;text-transform:uppercase;letter-spacing:0.05em;">Upcoming Scheduled Releases</div>
          <div style="font-size:18px;font-weight:800;color:#10b981;font-family:var(--mono);margin-top:2px;">{len(upcoming_releases)} Scheduled</div>
          <div style="font-size:10px;color:#94a3b8;margin-top:2px;">Q4 2026 ➔ 2027 Roadmap Pipeline</div>
        </div>
        """)

    st.markdown('<div style="margin-top:10px;"></div>', unsafe_allow_html=True)

    # --------------------------------------------------------------------------
    # SECTION 1: CURRENT RELEASE (STATIC FOCUS)
    # --------------------------------------------------------------------------
    render_html("""
    <div style="font-size:11px;font-weight:800;color:#f8fafc;text-transform:uppercase;letter-spacing:0.06em;margin-bottom:6px;display:flex;align-items:center;gap:6px;">
      <span style="color:#ef4444;">🚨</span> 1. Current Release (Cutover Focus)
    </div>
    """)

    if not current_releases:
        st.info("No active cutover release found.")
    else:
        for cur_rel in current_releases:
            stg_name, env_tag, badge_lbl = _get_stage_info(cur_rel, now_iso)
            badge_bg = "rgba(239,68,68,0.2)" if "TODAY" in badge_lbl else "rgba(245,158,11,0.2)"
            badge_fg = "#f87171" if "TODAY" in badge_lbl else "#fbbf24"
            badge_bd = "#ef4444" if "TODAY" in badge_lbl else "#f59e0b"

            dev_s, dev_f = cur_rel.get("dev_start_date"), cur_rel.get("dev_end_date")
            sit_s, sit_f = cur_rel.get("sit_start_date"), cur_rel.get("sit_end_date")
            uat_s, uat_f = cur_rel.get("uat_start_date"), cur_rel.get("uat_end_date")
            gn_d = cur_rel.get("go_nogo_date")
            p_d = cur_rel.get("prod_deploy_date")

            stg_dev_l, stg_dev_bg, stg_dev_fg, stg_dev_bd = _calc_stage_pill(dev_s, dev_f, now_iso)
            stg_sit_l, stg_sit_bg, stg_sit_fg, stg_sit_bd = _calc_stage_pill(sit_s, sit_f, now_iso)
            stg_uat_l, stg_uat_bg, stg_uat_fg, stg_uat_bd = _calc_stage_pill(uat_s, uat_f, now_iso)
            stg_gn_l, stg_gn_bg, stg_gn_fg, stg_gn_bd = _calc_stage_pill(gn_d, gn_d, now_iso)
            stg_prod_l, stg_prod_bg, stg_prod_fg, stg_prod_bd = _calc_stage_pill(p_d, p_d, now_iso)

            render_html(f"""
            <div style="background:#0f172a;border:1px solid #1e293b;border-left:4px solid #ef4444;border-radius:6px;padding:10px 14px;margin-bottom:10px;">
              <!-- Header Bar -->
              <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px;border-bottom:1px solid #1e293b;padding-bottom:8px;margin-bottom:8px;">
                <div style="display:flex;align-items:center;gap:10px;">
                  <span style="font-size:16px;font-weight:800;color:#f8fafc;font-family:var(--mono);">{cur_rel['release_id']}</span>
                  <span style="font-size:9.5px;font-weight:800;background:rgba(255,255,255,0.08);color:#cbd5e1;padding:2px 7px;border-radius:4px;font-family:var(--mono);">
                    {cur_rel['state']} MMIS ({cur_rel['quarter']} {cur_rel['year']})
                  </span>
                  <span style="font-size:9px;font-weight:800;padding:2px 7px;border-radius:10px;background:{badge_bg};color:{badge_fg};border:1px solid {badge_bd};letter-spacing:0.04em;">
                    ● {badge_lbl}
                  </span>
                </div>
                <div style="display:flex;align-items:center;gap:14px;font-size:11px;">
                  <div><span style="color:#94a3b8;">Current Stage:</span> <b style="color:#38bdf8;">{stg_name} ({env_tag})</b></div>
                  <div><span style="color:#94a3b8;">Cutover Date:</span> <b style="color:#f8fafc;font-family:var(--mono);">{p_d}</b></div>
                  <div><span style="color:#94a3b8;">Readiness:</span> <b style="color:#10b981;font-family:var(--mono);">{cur_rel['readiness_pct']:.0f}%</b></div>
                </div>
              </div>

              <!-- Static 5-Stage Multi-Environment Pipeline Grid -->
              <div style="display:grid;grid-template-columns:repeat(5, 1fr);gap:6px;">
                <!-- 1. DEV -->
                <div style="background:#020617;border:1px solid {stg_dev_bd};border-radius:4px;padding:6px 8px;">
                  <div style="font-size:8.5px;font-weight:700;color:#94a3b8;text-transform:uppercase;">1. DEV BUILD</div>
                  <div style="font-size:10px;color:#f8fafc;font-family:var(--mono);margin:2px 0;">{dev_s or '—'} ➔ {dev_f or '—'}</div>
                  <span style="font-size:8px;font-weight:800;padding:1px 5px;border-radius:4px;background:{stg_dev_bg};color:{stg_dev_fg};border:1px solid {stg_dev_bd};">{stg_dev_l}</span>
                </div>
                <!-- 2. SIT -->
                <div style="background:#020617;border:1px solid {stg_sit_bd};border-radius:4px;padding:6px 8px;">
                  <div style="font-size:8.5px;font-weight:700;color:#94a3b8;text-transform:uppercase;">2. SIT REGRESSION</div>
                  <div style="font-size:10px;color:#f8fafc;font-family:var(--mono);margin:2px 0;">{sit_s or '—'} ➔ {sit_f or '—'}</div>
                  <span style="font-size:8px;font-weight:800;padding:1px 5px;border-radius:4px;background:{stg_sit_bg};color:{stg_sit_fg};border:1px solid {stg_sit_bd};">{stg_sit_l}</span>
                </div>
                <!-- 3. UAT -->
                <div style="background:#020617;border:1px solid {stg_uat_bd};border-radius:4px;padding:6px 8px;">
                  <div style="font-size:8.5px;font-weight:700;color:#94a3b8;text-transform:uppercase;">3. STATE UAT</div>
                  <div style="font-size:10px;color:#f8fafc;font-family:var(--mono);margin:2px 0;">{uat_s or '—'} ➔ {uat_f or '—'}</div>
                  <span style="font-size:8px;font-weight:800;padding:1px 5px;border-radius:4px;background:{stg_uat_bg};color:{stg_uat_fg};border:1px solid {stg_uat_bd};">{stg_uat_l}</span>
                </div>
                <!-- 4. GO/NO-GO -->
                <div style="background:#020617;border:1px solid {stg_gn_bd};border-radius:4px;padding:6px 8px;">
                  <div style="font-size:8.5px;font-weight:700;color:#94a3b8;text-transform:uppercase;">4. GO / NO-GO</div>
                  <div style="font-size:10px;color:#f8fafc;font-family:var(--mono);margin:2px 0;">{gn_d or 'Pre-Cutover'}</div>
                  <span style="font-size:8px;font-weight:800;padding:1px 5px;border-radius:4px;background:{stg_gn_bg};color:{stg_gn_fg};border:1px solid {stg_gn_bd};">{stg_gn_l}</span>
                </div>
                <!-- 5. PROD CUTOVER -->
                <div style="background:#020617;border:1px solid {stg_prod_bd};border-radius:4px;padding:6px 8px;">
                  <div style="font-size:8.5px;font-weight:700;color:#94a3b8;text-transform:uppercase;">5. PROD CUTOVER</div>
                  <div style="font-size:10px;color:#f8fafc;font-family:var(--mono);margin:2px 0;">{p_d} (Live)</div>
                  <span style="font-size:8px;font-weight:800;padding:1px 5px;border-radius:4px;background:{stg_prod_bg};color:{stg_prod_fg};border:1px solid {stg_prod_bd};">{stg_prod_l}</span>
                </div>
              </div>
            </div>
            """)

    # --------------------------------------------------------------------------
    # SECTION 2: ACTIVE RELEASES (IN-FLIGHT) (STATIC TABLE)
    # --------------------------------------------------------------------------
    render_html(f"""
    <div style="font-size:11px;font-weight:800;color:#f8fafc;text-transform:uppercase;letter-spacing:0.06em;margin:12px 0 6px 0;display:flex;align-items:center;gap:6px;">
      <span style="color:#38bdf8;">⚡</span> 2. Active Releases (In-Flight across DEV / SIT / UAT) — {len(active_releases)} Releases
    </div>
    """)

    if not active_releases:
        st.info("No in-flight releases currently active.")
    else:
        active_rows = []
        for r in active_releases:
            stg_name, env_tag, _ = _get_stage_info(r, now_iso)
            dev_str = f"{r.get('dev_start_date') or '—'} ➔ {r.get('dev_end_date') or '—'}"
            sit_str = f"{r.get('sit_start_date') or '—'} ➔ {r.get('sit_end_date') or '—'}"
            uat_str = f"{r.get('uat_start_date') or '—'} ➔ {r.get('uat_end_date') or '—'}"
            
            row_html = f"""
            <tr style="border-bottom:1px solid #1e293b;font-size:10px;">
              <td style="padding:6px 8px;"><span style="font-size:9px;font-weight:800;padding:2px 5px;border-radius:3px;background:rgba(255,255,255,0.08);color:#f8fafc;font-family:var(--mono);">{r['state']}</span></td>
              <td style="padding:6px 8px;font-weight:700;color:#f8fafc;font-family:var(--mono);">{r['release_id']}</td>
              <td style="padding:6px 8px;"><span style="color:#38bdf8;font-weight:700;">{stg_name}</span> <span style="color:#94a3b8;font-family:var(--mono);font-size:9px;">({env_tag})</span></td>
              <td style="padding:6px 8px;color:#94a3b8;font-family:var(--mono);">{dev_str}</td>
              <td style="padding:6px 8px;color:#94a3b8;font-family:var(--mono);">{sit_str}</td>
              <td style="padding:6px 8px;color:#94a3b8;font-family:var(--mono);">{uat_str}</td>
              <td style="padding:6px 8px;font-weight:700;color:#f8fafc;font-family:var(--mono);">{r['prod_deploy_date']}</td>
              <td style="padding:6px 8px;color:#10b981;font-weight:700;font-family:var(--mono);">{r['readiness_pct']:.0f}%</td>
              <td style="padding:6px 8px;"><span style="font-size:8.5px;font-weight:800;padding:2px 6px;border-radius:10px;background:rgba(56,189,248,0.15);color:#38bdf8;border:1px solid rgba(56,189,248,0.3);">IN PROGRESS</span></td>
            </tr>
            """
            active_rows.append("\n".join(l.strip() for l in row_html.splitlines() if l.strip()))

        render_html(f"""
        <div style="border:1px solid #1e293b;border-radius:6px;background:#0f172a;overflow:hidden;margin-bottom:10px;">
          <table style="width:100%;border-collapse:collapse;text-align:left;">
            <thead>
              <tr style="background:#1e293b;border-bottom:1px solid #334155;font-size:9px;font-weight:700;text-transform:uppercase;color:#94a3b8;">
                <th style="padding:6px 8px;">State</th>
                <th style="padding:6px 8px;">Release ID</th>
                <th style="padding:6px 8px;">Current Stage & Environment</th>
                <th style="padding:6px 8px;">DEV Window</th>
                <th style="padding:6px 8px;">SIT Window</th>
                <th style="padding:6px 8px;">UAT Window</th>
                <th style="padding:6px 8px;">PROD Cutover</th>
                <th style="padding:6px 8px;">Readiness</th>
                <th style="padding:6px 8px;">Status</th>
              </tr>
            </thead>
            <tbody>
              {"".join(active_rows)}
            </tbody>
          </table>
        </div>
        """)

    # --------------------------------------------------------------------------
    # SECTION 3: UPCOMING RELEASES (SCHEDULED ROADMAP) (STATIC TABLE)
    # --------------------------------------------------------------------------
    render_html(f"""
    <div style="font-size:11px;font-weight:800;color:#f8fafc;text-transform:uppercase;letter-spacing:0.06em;margin:12px 0 6px 0;display:flex;align-items:center;gap:6px;">
      <span style="color:#10b981;">🗓️</span> 3. Upcoming Releases (Scheduled Roadmap) — {len(upcoming_releases)} Releases
    </div>
    """)

    if not upcoming_releases:
        st.info("No upcoming releases scheduled.")
    else:
        upcoming_rows = []
        for r in upcoming_releases:
            dev_start = r.get("dev_start_date") or "—"
            sit_start = r.get("sit_start_date") or "—"
            uat_start = r.get("uat_start_date") or "—"

            row_html = f"""
            <tr style="border-bottom:1px solid #1e293b;font-size:10px;">
              <td style="padding:6px 8px;"><span style="font-size:9px;font-weight:800;padding:2px 5px;border-radius:3px;background:rgba(255,255,255,0.08);color:#f8fafc;font-family:var(--mono);">{r['state']}</span></td>
              <td style="padding:6px 8px;font-weight:700;color:#f8fafc;font-family:var(--mono);">{r['release_id']}</td>
              <td style="padding:6px 8px;color:#94a3b8;font-family:var(--mono);">{r['quarter']} {r['year']}</td>
              <td style="padding:6px 8px;color:#94a3b8;font-family:var(--mono);">{dev_start}</td>
              <td style="padding:6px 8px;color:#94a3b8;font-family:var(--mono);">{sit_start}</td>
              <td style="padding:6px 8px;color:#94a3b8;font-family:var(--mono);">{uat_start}</td>
              <td style="padding:6px 8px;font-weight:700;color:#f8fafc;font-family:var(--mono);">{r['prod_deploy_date']}</td>
              <td style="padding:6px 8px;"><span style="font-size:8.5px;font-weight:800;padding:2px 6px;border-radius:10px;background:rgba(255,255,255,0.05);color:#94a3b8;border:1px solid #334155;">SCHEDULED</span></td>
            </tr>
            """
            upcoming_rows.append("\n".join(l.strip() for l in row_html.splitlines() if l.strip()))

        render_html(f"""
        <div style="border:1px solid #1e293b;border-radius:6px;background:#0f172a;overflow:hidden;margin-bottom:10px;">
          <table style="width:100%;border-collapse:collapse;text-align:left;">
            <thead>
              <tr style="background:#1e293b;border-bottom:1px solid #334155;font-size:9px;font-weight:700;text-transform:uppercase;color:#94a3b8;">
                <th style="padding:6px 8px;">State</th>
                <th style="padding:6px 8px;">Release ID</th>
                <th style="padding:6px 8px;">Quarter / Cadence</th>
                <th style="padding:6px 8px;">DEV Start</th>
                <th style="padding:6px 8px;">SIT Start</th>
                <th style="padding:6px 8px;">UAT Start</th>
                <th style="padding:6px 8px;">PROD Cutover</th>
                <th style="padding:6px 8px;">Status</th>
              </tr>
            </thead>
            <tbody>
              {"".join(upcoming_rows)}
            </tbody>
          </table>
        </div>
        """)

    # --------------------------------------------------------------------------
    # SECTION 4: COMPLETED RELEASES (COLLAPSIBLE ARCHIVE)
    # --------------------------------------------------------------------------
    with st.expander(f"📁 Completed Releases (Historical Archive) — {len(completed_releases)} Releases"):
        if not completed_releases:
            st.info("No completed releases.")
        else:
            comp_rows = []
            for r in completed_releases:
                row_html = f"""
                <tr style="border-bottom:1px solid #1e293b;font-size:9.5px;">
                  <td style="padding:4px 6px;"><span style="font-family:var(--mono);font-size:8.5px;">{r['state']}</span></td>
                  <td style="padding:4px 6px;font-weight:700;color:#f8fafc;font-family:var(--mono);">{r['release_id']}</td>
                  <td style="padding:4px 6px;color:#94a3b8;font-family:var(--mono);">{r['quarter']} {r['year']}</td>
                  <td style="padding:4px 6px;color:#f8fafc;font-family:var(--mono);">{r['prod_deploy_date']}</td>
                  <td style="padding:4px 6px;"><span style="font-size:8px;font-weight:700;padding:1px 5px;border-radius:8px;background:rgba(16,185,129,0.12);color:#34d399;border:1px solid rgba(16,185,129,0.3);">COMPLETED (100%)</span></td>
                </tr>
                """
                comp_rows.append("\n".join(l.strip() for l in row_html.splitlines() if l.strip()))

            render_html(f"""
            <div style="border:1px solid #1e293b;border-radius:4px;background:#020617;max-height:200px;overflow-y:auto;">
              <table style="width:100%;border-collapse:collapse;text-align:left;">
                <thead>
                  <tr style="background:#1e293b;font-size:8.5px;font-weight:700;color:#94a3b8;text-transform:uppercase;">
                    <th style="padding:4px 6px;">State</th>
                    <th style="padding:4px 6px;">Release ID</th>
                    <th style="padding:4px 6px;">Quarter</th>
                    <th style="padding:4px 6px;">Cutover Date</th>
                    <th style="padding:4px 6px;">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {"".join(comp_rows)}
                </tbody>
              </table>
            </div>
            """)

    conn.close()
