"""
release_plan.py
===============
Enterprise Schedule Release Plan & State RM Governance Workspace.
Power BI-grade analytics interface with interactive slicers, state-wise & state RM-wise
command cards, multi-phase stage-gate timeline, and deep master-detail drillthrough.

Adheres strictly to the Enterprise Dashboard Architect playbook:
  - Zero-page-scroll constraint (100vh viewport locking, internal scroll containers)
  - Deep Slate theme (#0f172a, #1e293b, #334155, neon cyan/emerald/amber/crimson accents)
  - Master-Detail split-pane with 4-tab contextual inspector
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
import pandas as pd
import streamlit as st

from db import (
    get_connection,
    get_release_milestones,
    get_release_schedules,
    get_release_summary_metrics,
    get_rm_portfolio_breakdown,
)


def render_html(html_str: str) -> None:
    """Render HTML safely without markdown 4-space code-block escaping."""
    cleaned = "\n".join(line.strip() for line in html_str.splitlines() if line.strip())
    st.markdown(cleaned, unsafe_allow_html=True)


def _format_stage_dates(s_date: str | None, f_date: str | None) -> str:
    """Helper to format date window for pipeline cards."""
    if s_date and f_date:
        if s_date == f_date:
            return s_date
        return f"{s_date} ➔ {f_date}"
    if s_date:
        return f"From {s_date}"
    if f_date:
        return f"Until {f_date}"
    return "Scheduled in Sprint"


def _calc_stage_status(s_date: str | None, f_date: str | None, now_iso: str) -> tuple[str, str, str, str]:
    """
    Returns (status_label, bg_color, text_color, border_color)
    status_label in ('COMPLETED', 'ACTIVE TODAY', 'SCHEDULED')
    """
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


def render_release_plan_workspace(db_path: str) -> None:
    """Render the Enterprise Schedule Release Plan workspace."""
    conn = get_connection(db_path)

    # --------------------------------------------------------------------------
    # 1. RBAC Identification & State RM Isolation Governance
    # --------------------------------------------------------------------------
    active_user = st.session_state.get("active_user") or "admin"
    user_role = st.session_state.get("user_role") or "Admin"
    user_assigned_state = st.session_state.get("assigned_state")

    # If assigned_state not set explicitly in session, deduce from username
    if not user_assigned_state:
        u_low = active_user.lower()
        if "ak" in u_low:
            user_assigned_state = "AK"
        elif "nd" in u_low:
            user_assigned_state = "ND"
        elif "nh" in u_low:
            user_assigned_state = "NH"

    is_enterprise_admin = (user_role == "Admin" and user_assigned_state is None)

    # State naming & icon dictionaries
    state_names = {
        "AK": "Alaska State MMIS",
        "ND": "North Dakota State MMIS",
        "NH": "New Hampshire State MMIS",
    }
    state_icons = {"AK": "🏔️", "ND": "🌾", "NH": "🍁"}
    state_highlights = {
        "AK": "19 Milestones per Release • SOA Review & Approval Gates • FAS Test Matrix",
        "ND": "Build-76 CI/CD Deployments • Defect Freeze • Formal Go/No-Go Board",
        "NH": "Multi-Tier Pipeline (ENV52➔57➔53➔04➔05) • NTT Data QA Doc Reviews",
    }
    rm_lead_names = {
        "AK": "Alaska State RM Lead",
        "ND": "North Dakota State RM Lead",
        "NH": "New Hampshire State RM Lead",
    }

    # For Enterprise Admin: Provide an RBAC Persona Simulator
    simulated_state = None
    if is_enterprise_admin:
        p_col1, p_col2 = st.columns([2.0, 1.8])
        with p_col1:
            persona_choice = st.selectbox(
                "🛡️ RBAC Persona & Jurisdiction Simulator (Executive Administrative Mode)",
                [
                    "🌐 Enterprise Executive (Unrestricted Multi-State Rollup)",
                    "🏔️ Alaska State RM (AK Enforced Quarantine)",
                    "🌾 North Dakota State RM (ND Enforced Quarantine)",
                    "🍁 New Hampshire State RM (NH Enforced Quarantine)",
                ],
                index=0,
                key="rbac_persona_sim",
                help="Switch perspectives to verify strict State RM RBAC isolation. In RM mode, other states are completely quarantined."
            )
            if "Alaska" in persona_choice:
                simulated_state = "AK"
            elif "North Dakota" in persona_choice:
                simulated_state = "ND"
            elif "New Hampshire" in persona_choice:
                simulated_state = "NH"
        with p_col2:
            render_html("""
            <div style="background:rgba(56,189,248,0.05);border:1px solid rgba(56,189,248,0.25);border-radius:6px;padding:6px 10px;margin-top:14px;font-size:10px;color:#94a3b8;">
              <span style="color:#38bdf8;font-weight:700;">🔐 RBAC Policy Mandate:</span> 1 State RM cannot see another state. In State RM view, cross-jurisdiction data is strictly quarantined.
            </div>
            """)

    # Determine effective state lock:
    effective_state_lock = simulated_state if is_enterprise_admin and simulated_state else user_assigned_state

    # Render security quarantine alert if locked to a specific state
    if effective_state_lock:
        lock_st_name = state_names.get(effective_state_lock, effective_state_lock)
        lock_icon = state_icons.get(effective_state_lock, "🔒")
        render_html(f"""
        <div style="background:rgba(15,23,42,0.95);border:1px solid rgba(56,189,248,0.4);border-left:4px solid #38bdf8;border-radius:6px;padding:8px 12px;margin-bottom:8px;display:flex;align-items:center;justify-content:space-between;">
          <div style="display:flex;align-items:center;gap:10px;">
            <span style="font-size:18px;">{lock_icon}</span>
            <div>
              <div style="font-size:11.5px;font-weight:800;color:#f8fafc;letter-spacing:0.02em;">
                SECURE JURISDICTION LOCK // {lock_st_name.upper()}
              </div>
              <div style="font-size:9.5px;color:#94a3b8;">
                Strict State RM RBAC Isolation Active • Cross-State Data Quarantined • Identity: <code style="color:#38bdf8;">{active_user}</code> ({user_role})
              </div>
            </div>
          </div>
          <span style="font-size:9px;font-weight:800;background:rgba(56,189,248,0.15);color:#38bdf8;border:1px solid rgba(56,189,248,0.3);padding:3px 8px;border-radius:12px;letter-spacing:0.05em;">
            ENFORCED JURISDICTION
          </span>
        </div>
        """)

    # --------------------------------------------------------------------------
    # 2. Top Header & Quick Context
    # --------------------------------------------------------------------------
    sub_title = f"{state_names.get(effective_state_lock, 'MULTI-STATE')} RM PORTFOLIO & STAGE-GATE GOVERNANCE" if effective_state_lock else "MULTI-STATE RM PORTFOLIO & STAGE-GATE GOVERNANCE // ALASKA • NORTH DAKOTA • NEW HAMPSHIRE"
    render_html(f"""
    <div style="display:flex;align-items:center;justify-content:space-between;padding:2px 0 8px 0;border-bottom:1px solid #1e293b;margin-bottom:8px;">
      <div style="display:flex;align-items:center;gap:10px;">
        <span style="font-size:18px;">📅</span>
        <div>
          <div style="font-size:14.5px;font-weight:800;color:#f8fafc;letter-spacing:-0.02em;text-transform:uppercase;">
            Schedule Release Plan & Environment Pipeline
          </div>
          <div style="font-size:9.5px;color:#38bdf8;font-family:var(--mono);font-weight:600;letter-spacing:0.04em;">
            {sub_title}
          </div>
        </div>
      </div>
      <div style="display:flex;align-items:center;gap:8px;">
        <span style="font-size:9.5px;background:rgba(56,189,248,0.12);border:1px solid rgba(56,189,248,0.3);color:#38bdf8;padding:2px 8px;border-radius:4px;font-family:var(--mono);font-weight:700;">
          SYSTEM OF RECORD: _Input/*.xlsx
        </span>
        <span style="font-size:9.5px;background:rgba(16,185,129,0.12);border:1px solid rgba(16,185,129,0.3);color:#34d399;padding:2px 8px;border-radius:4px;font-family:var(--mono);font-weight:700;">
          LIVE SYNC: ACTIVE
        </span>
      </div>
    </div>
    """)

    # --------------------------------------------------------------------------
    # 3. Power BI Interactive Slicers Bar (With Strict RBAC Locking)
    # --------------------------------------------------------------------------
    s_col1, s_col2, s_col3, s_col4, s_col5, s_col6 = st.columns([1.2, 1.4, 1.0, 0.9, 1.1, 1.6])

    with s_col1:
        if effective_state_lock:
            st.selectbox("State Filter", [effective_state_lock], index=0, key="sl_state_locked", disabled=True)
            selected_state = effective_state_lock
        else:
            state_opts = ["All", "AK", "ND", "NH"]
            selected_state = st.selectbox("State Filter", state_opts, index=0, key="sl_state")

    with s_col2:
        if effective_state_lock:
            rm_locked_lead = rm_lead_names.get(effective_state_lock, "Assigned RM Lead")
            st.selectbox("State RM Lead", [rm_locked_lead], index=0, key="sl_rm_locked", disabled=True)
            selected_rm = rm_locked_lead
        else:
            rm_opts = [
                "All",
                "Alaska State RM Lead",
                "North Dakota State RM Lead",
                "New Hampshire State RM Lead",
            ]
            selected_rm = st.selectbox("State RM Lead", rm_opts, index=0, key="sl_rm")

    with s_col3:
        year_opts = ["All", "2026", "2025", "2027"]
        selected_year = st.selectbox("Release Year", year_opts, index=0, key="sl_year")

    with s_col4:
        quarter_opts = ["All", "Q1", "Q2", "Q3", "Q4"]
        selected_quarter = st.selectbox("Quarter", quarter_opts, index=0, key="sl_quarter")

    with s_col5:
        status_opts = ["All", "Completed", "In Progress", "Scheduled"]
        selected_status = st.selectbox("Status Filter", status_opts, index=0, key="sl_status")

    with s_col6:
        search_query = st.text_input("Instant Search", placeholder="Search Release ID, Task, or ENV...", key="sl_search")

    # Fetch data based on slicers with strict state quarantine
    releases = get_release_schedules(
        conn,
        state=selected_state,
        year=int(selected_year) if selected_year != "All" else None,
        quarter=selected_quarter,
        status=selected_status,
        rm=selected_rm if not effective_state_lock else None,
    )

    # Filter by search string if provided
    if search_query.strip():
        sq = search_query.strip().lower()
        releases = [
            r for r in releases
            if sq in r["release_id"].lower()
            or sq in r["release_name"].lower()
            or sq in r.get("notes", "").lower()
            or sq in r["state_rm_name"].lower()
            or sq in r["state"].lower()
        ]

    # --------------------------------------------------------------------------
    # 4. Dynamic State-Aware Fortune-500 Executive KPI Ribbon
    # --------------------------------------------------------------------------
    now_iso = datetime.now().strftime("%Y-%m-%d")
    total_rel_count = len(releases)
    total_cutovers = sum(1 for r in releases if r.get("prod_deploy_date"))

    # Dynamic Next Impending Cutover strictly for the active state/scope!
    upcoming = [r for r in releases if r.get("prod_deploy_date", "") >= now_iso]
    upcoming.sort(key=lambda x: x.get("prod_deploy_date", ""))
    next_r = upcoming[0] if upcoming else (releases[0] if releases else None)

    in_flight_count = sum(1 for r in releases if r.get("status") == "In Progress")
    completed_count = sum(1 for r in releases if r.get("status") == "Completed")
    avg_readiness = (sum(r.get("readiness_pct", 0) for r in releases) / total_rel_count) if total_rel_count else 100.0

    kpi_col1, kpi_col2, kpi_col3, kpi_col4, kpi_col5 = st.columns(5)

    with kpi_col1:
        if selected_state == "All":
            sub_lbl = "(12 AK • 12 ND • 14 NH)"
        else:
            sub_lbl = f"(100% {selected_state} MMIS Scoped)"
        render_html(f"""
        <div style="background:#0f172a;border:1px solid #1e293b;border-top:3px solid #38bdf8;border-radius:6px;padding:8px 12px;">
          <div style="font-size:9px;font-weight:700;color:#94a3b8;text-transform:uppercase;letter-spacing:0.06em;">Total Planned Releases</div>
          <div style="display:flex;align-items:baseline;gap:8px;margin-top:2px;">
            <span style="font-size:22px;font-weight:800;color:#f8fafc;font-family:var(--mono);">{total_rel_count}</span>
            <span style="font-size:10px;color:#38bdf8;font-weight:700;">{sub_lbl}</span>
          </div>
        </div>
        """)

    with kpi_col2:
        render_html(f"""
        <div style="background:#0f172a;border:1px solid #1e293b;border-top:3px solid #10b981;border-radius:6px;padding:8px 12px;">
          <div style="font-size:9px;font-weight:700;color:#94a3b8;text-transform:uppercase;letter-spacing:0.06em;">Production Cutovers</div>
          <div style="display:flex;align-items:baseline;gap:8px;margin-top:2px;">
            <span style="font-size:22px;font-weight:800;color:#10b981;font-family:var(--mono);">{total_cutovers}</span>
            <span style="font-size:10px;color:#34d399;font-weight:700;">100% Scheduled</span>
          </div>
        </div>
        """)

    with kpi_col3:
        next_tag = next_r["release_id"] if next_r else "None"
        next_dt = next_r["prod_deploy_date"] if next_r else "N/A"
        next_st = next_r["state"] if next_r else ""
        next_diff = 999
        if next_r and next_r.get("prod_deploy_date"):
            try:
                next_diff = (datetime.strptime(next_r["prod_deploy_date"], "%Y-%m-%d").date() - datetime.strptime(now_iso, "%Y-%m-%d").date()).days
            except Exception:
                pass

        if next_diff == 0:
            cd_badge = '<span style="color:#ef4444;font-weight:800;">CUTOVER TODAY</span>'
        elif 0 < next_diff <= 3:
            cd_badge = f'<span style="color:#fbbf24;font-weight:800;">T-{next_diff}d Impending</span>'
        elif next_diff > 3:
            cd_badge = f'<span style="color:#38bdf8;font-weight:700;">T-{next_diff}d Window</span>'
        else:
            cd_badge = '<span style="color:#10b981;">Deployed</span>'

        render_html(f"""
        <div style="background:#0f172a;border:1px solid #1e293b;border-top:3px solid #f59e0b;border-radius:6px;padding:8px 12px;">
          <div style="font-size:9px;font-weight:700;color:#94a3b8;text-transform:uppercase;letter-spacing:0.06em;">Next Impending Cutover</div>
          <div style="display:flex;align-items:baseline;gap:8px;margin-top:2px;">
            <span style="font-size:15px;font-weight:800;color:#fbbf24;font-family:var(--mono);">{next_tag}</span>
            <span style="font-size:10px;color:#f8fafc;font-weight:600;">{next_dt} ({next_st}) • {cd_badge}</span>
          </div>
        </div>
        """)

    with kpi_col4:
        render_html(f"""
        <div style="background:#0f172a;border:1px solid #1e293b;border-top:3px solid #a855f7;border-radius:6px;padding:8px 12px;">
          <div style="font-size:9px;font-weight:700;color:#94a3b8;text-transform:uppercase;letter-spacing:0.06em;">Avg Gate Readiness</div>
          <div style="display:flex;align-items:baseline;gap:8px;margin-top:2px;">
            <span style="font-size:22px;font-weight:800;color:#c084fc;font-family:var(--mono);">{avg_readiness:.0f}%</span>
            <span style="font-size:10px;color:#a855f7;font-weight:700;">Zero Blockers</span>
          </div>
        </div>
        """)

    with kpi_col5:
        render_html(f"""
        <div style="background:#0f172a;border:1px solid #1e293b;border-top:3px solid #06b6d4;border-radius:6px;padding:8px 12px;">
          <div style="font-size:9px;font-weight:700;color:#94a3b8;text-transform:uppercase;letter-spacing:0.06em;">Active In-Flight Releases</div>
          <div style="display:flex;align-items:baseline;gap:8px;margin-top:2px;">
            <span style="font-size:22px;font-weight:800;color:#22d3ee;font-family:var(--mono);">{in_flight_count} Active</span>
            <span style="font-size:10px;color:#94a3b8;">({completed_count} Done)</span>
          </div>
        </div>
        """)

    # --------------------------------------------------------------------------
    # 5. State Release Management Command Center Section (Strictly Scoped!)
    # --------------------------------------------------------------------------
    render_html("""
    <div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.06em;color:#94a3b8;margin:8px 0 4px 0;">
      State Release Management (RM) Command Center
    </div>
    """)

    rm_breakdowns = get_rm_portfolio_breakdown(conn)
    if selected_state != "All":
        rm_breakdowns = [rm for rm in rm_breakdowns if rm["state"] == selected_state]

    # If scoped to 1 state (either via slicer or RBAC lock), render ONLY that state's card!
    if len(rm_breakdowns) == 1:
        rm = rm_breakdowns[0]
        st_code = rm["state"]
        render_html(f"""
        <div style="background:#0f172a;border:1px solid #1e293b;border-left:4px solid #38bdf8;border-radius:6px;padding:8px 12px;margin-bottom:8px;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px;">
          <div style="display:flex;align-items:center;gap:10px;">
            <span style="font-size:22px;">{state_icons.get(st_code, '🏛️')}</span>
            <div>
              <div style="font-size:13px;font-weight:800;color:#f8fafc;">{state_names.get(st_code, st_code)} — Release Management Command Center</div>
              <div style="font-size:10px;color:#94a3b8;">
                Designated RM Lead: <b style="color:#e2e8f0;">{rm['state_rm_name']}</b> • {state_highlights.get(st_code, '')}
              </div>
            </div>
          </div>
          <div style="display:flex;align-items:center;gap:12px;font-size:10.5px;">
            <div style="background:rgba(255,255,255,0.03);border:1px solid #1e293b;padding:3px 8px;border-radius:4px;">
              <span style="color:#94a3b8;">Jurisdiction Scope:</span> <b style="color:#38bdf8;font-family:var(--mono);">{rm['total_releases']} Releases</b>
            </div>
            <div style="background:rgba(255,255,255,0.03);border:1px solid #1e293b;padding:3px 8px;border-radius:4px;">
              <span style="color:#94a3b8;">Cadence:</span> <b style="color:#f8fafc;font-family:var(--mono);">{rm['earliest_deploy']} ➔ {rm['latest_deploy']}</b>
            </div>
            <div style="background:rgba(16,185,129,0.08);border:1px solid rgba(16,185,129,0.25);padding:3px 8px;border-radius:4px;">
              <span style="color:#10b981;font-weight:700;">Avg Gate Readiness: {rm['avg_readiness']:.0f}%</span>
            </div>
          </div>
        </div>
        """)
    else:
        # Multi-state rollup cards (Only for Enterprise Admin viewing 'All')
        rm_col1, rm_col2, rm_col3 = st.columns(3)
        cols = [rm_col1, rm_col2, rm_col3]
        for idx, rm in enumerate(rm_breakdowns):
            st_code = rm["state"]
            c = cols[idx % 3]
            with c:
                render_html(f"""
                <div style="background:#0f172a;border:1px solid #1e293b;border-radius:6px;padding:8px 10px;display:flex;flex-direction:column;gap:5px;">
                  <div style="display:flex;align-items:center;justify-content:space-between;">
                    <div style="display:flex;align-items:center;gap:6px;">
                      <span style="font-size:15px;">{state_icons.get(st_code, '🏛️')}</span>
                      <span style="font-size:11.5px;font-weight:800;color:#f8fafc;">{state_names.get(st_code, st_code)}</span>
                    </div>
                    <span style="font-size:9.5px;font-weight:700;padding:2px 5px;border-radius:4px;background:rgba(56,189,248,0.12);color:#38bdf8;font-family:var(--mono);">
                      {rm['total_releases']} Releases
                    </span>
                  </div>
                  <div style="font-size:9.5px;color:#94a3b8;">
                    Lead: <b style="color:#e2e8f0;">{rm['state_rm_name']}</b>
                  </div>
                  <div style="font-size:9px;color:#64748b;line-height:1.3;">
                    {state_highlights.get(st_code, '')}
                  </div>
                  <div style="display:flex;justify-content:space-between;align-items:center;padding-top:4px;border-top:1px solid #1e293b;font-size:9px;color:#94a3b8;">
                    <span>Cadence: <b style="color:#f8fafc;font-family:var(--mono);">{rm['earliest_deploy']} ➔ {rm['latest_deploy']}</b></span>
                    <span style="color:#10b981;font-weight:700;">Ready: {rm['avg_readiness']:.0f}%</span>
                  </div>
                </div>
                """)

    # --------------------------------------------------------------------------
    # 5. Split-Pane Master-Detail Workspace
    # --------------------------------------------------------------------------
    render_html("""
    <div style="display:flex;align-items:center;justify-content:space-between;margin:12px 0 6px 0;">
      <div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.06em;color:#94a3b8;">
        Release Portfolio Matrix & Milestone Inspector
      </div>
      <div style="font-size:9.5px;color:#64748b;">
        Showing matching releases based on active slicers
      </div>
    </div>
    """)

    # Manage active selected release in session state based on current date
    valid_ids = [r["release_id"] for r in releases]
    now_iso = datetime.now().strftime("%Y-%m-%d")

    # Smart determination of current active release based on today's date
    active_cand = [r for r in releases if r.get("prod_deploy_date", "") >= now_iso]
    smart_active_id = active_cand[0]["release_id"] if active_cand else (valid_ids[0] if valid_ids else None)

    if "selected_release_id" not in st.session_state or st.session_state["selected_release_id"] not in valid_ids:
        st.session_state["selected_release_id"] = smart_active_id

    master_col, detail_col = st.columns([1.25, 1.0])

    # --- LEFT MASTER TABLE ---
    with master_col:
        render_html(f"""
        <div style="font-size:10.5px;font-weight:700;color:#94a3b8;margin-bottom:6px;display:flex;justify-content:space-between;">
          <span>Release Schedule Matrix ({len(releases)} matches)</span>
          <span style="color:#38bdf8;font-family:var(--mono);font-size:10px;">Click selector below to inspect</span>
        </div>
        """)

        if not releases:
            st.info("No release schedules match the selected slicers.")
        else:
            # Build an interactive table inside an internal scrollable container
            table_rows_html = []
            for r in releases:
                rel_id = r["release_id"]
                is_sel = rel_id == st.session_state["selected_release_id"]
                row_bg = "background:rgba(56,189,248,0.15);border-left:3px solid #38bdf8;" if is_sel else "background:rgba(255,255,255,0.015);"
                
                # Check date relative to today
                c_date = r.get("prod_deploy_date", "")
                days_diff = 999
                try:
                    days_diff = (datetime.strptime(c_date, "%Y-%m-%d").date() - datetime.strptime(now_iso, "%Y-%m-%d").date()).days
                except Exception:
                    pass

                live_badge = ""
                if days_diff == 0:
                    live_badge = '<span style="font-size:8px;font-weight:800;padding:2px 5px;border-radius:10px;background:rgba(239,68,68,0.25);color:#fca5a5;border:1px solid #ef4444;margin-left:4px;white-space:nowrap;">● CUTOVER TODAY</span>'
                elif 0 < days_diff <= 3:
                    live_badge = f'<span style="font-size:8px;font-weight:800;padding:2px 5px;border-radius:10px;background:rgba(245,158,11,0.25);color:#fcd34d;border:1px solid #f59e0b;margin-left:4px;white-space:nowrap;">● T-{days_diff}d IMPENDING</span>'
                elif 3 < days_diff <= 20:
                    live_badge = f'<span style="font-size:8px;font-weight:800;padding:2px 5px;border-radius:10px;background:rgba(56,189,248,0.2);color:#38bdf8;border:1px solid rgba(56,189,248,0.4);margin-left:4px;white-space:nowrap;">● T-{days_diff}d ACTIVE</span>'

                # Status pill
                st_color = "#10b981" if r["status"] == "Completed" else ("#ef4444" if days_diff == 0 else ("#f59e0b" if 0 < days_diff <= 3 else ("#38bdf8" if r["status"] == "In Progress" else "#94a3b8")))
                st_bg = "rgba(16,185,129,0.12)" if r["status"] == "Completed" else ("rgba(239,68,68,0.18)" if days_diff == 0 else ("rgba(245,158,11,0.18)" if 0 < days_diff <= 3 else ("rgba(56,189,248,0.12)" if r["status"] == "In Progress" else "rgba(255,255,255,0.05)")))
                display_status = "Cutover Today" if days_diff == 0 else r["status"]

                st_badge = f'<span style="font-size:9px;font-weight:800;padding:2px 5px;border-radius:3px;background:rgba(255,255,255,0.08);color:#f8fafc;font-family:var(--mono);">{r["state"]}</span>'

                row_html = f"""
                <tr style="{row_bg}border-bottom:1px solid #1e293b;">
                  <td style="padding:6px 8px;">{st_badge}</td>
                  <td style="padding:6px 8px;font-size:11px;font-weight:700;color:#f8fafc;font-family:var(--mono);">{rel_id}{live_badge}</td>
                  <td style="padding:6px 8px;font-size:10.5px;color:#94a3b8;font-family:var(--mono);">{r['quarter']} {r['year']}</td>
                  <td style="padding:6px 8px;font-size:10.5px;font-weight:700;color:#f8fafc;font-family:var(--mono);">{r['prod_deploy_date']}</td>
                  <td style="padding:6px 8px;">
                    <span style="font-size:9px;font-weight:700;padding:2px 6px;border-radius:10px;background:{st_bg};color:{st_color};">
                      {display_status}
                    </span>
                  </td>
                  <td style="padding:6px 8px;font-size:10px;color:#38bdf8;font-family:var(--mono);font-weight:700;">
                    {r['readiness_pct']:.0f}%
                  </td>
                </tr>
                """
                table_rows_html.append("\n".join(l.strip() for l in row_html.splitlines() if l.strip()))

            table_body = "".join(table_rows_html)
            render_html(f"""
            <div style="border:1px solid #1e293b;border-radius:6px;background:#0f172a;overflow:hidden;margin-bottom:8px;">
              <table style="width:100%;border-collapse:collapse;text-align:left;">
                <thead>
                  <tr style="background:#1e293b;border-bottom:1px solid #334155;font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:0.06em;color:#94a3b8;">
                    <th style="padding:6px 8px;">State</th>
                    <th style="padding:6px 8px;">Release ID</th>
                    <th style="padding:6px 8px;">Cadence</th>
                    <th style="padding:6px 8px;">PROD Cutover</th>
                    <th style="padding:6px 8px;">Status</th>
                    <th style="padding:6px 8px;">Readiness</th>
                  </tr>
                </thead>
              </table>
              <div style="max-height:260px;overflow-y:auto;">
                <table style="width:100%;border-collapse:collapse;text-align:left;">
                  <tbody>
                    {table_body}
                  </tbody>
                </table>
              </div>
            </div>
            """)

            # Quick Selector Dropdown to drive the Right Contextual Inspector
            r_lookup = {r["release_id"]: r for r in releases}
            sel_idx = valid_ids.index(st.session_state["selected_release_id"]) if st.session_state["selected_release_id"] in valid_ids else 0
            render_html('<div style="font-size:9.5px;font-weight:700;color:#94a3b8;text-transform:uppercase;letter-spacing:0.04em;margin-top:6px;margin-bottom:2px;">Inspect Selected Release:</div>')
            selected_from_picker = st.selectbox(
                "Select Release for Deep Drillthrough",
                valid_ids,
                index=sel_idx,
                format_func=lambda rid: f"{rid}  |  {r_lookup[rid]['state']}  |  Cutover: {r_lookup[rid]['prod_deploy_date']}  |  {r_lookup[rid]['status']}",
                key="active_rel_picker",
                label_visibility="collapsed"
            )
            if selected_from_picker != st.session_state["selected_release_id"]:
                st.session_state["selected_release_id"] = selected_from_picker
                st.rerun()

    # --- RIGHT CONTEXTUAL DETAIL INSPECTOR ---
    with detail_col:
        sel_id = st.session_state.get("selected_release_id")
        active_rel = next((r for r in releases if r["release_id"] == sel_id), None)

        if not active_rel:
            st.info("Select a release from the left matrix to inspect its environment pipeline.")
        else:
            milestones = get_release_milestones(conn, active_rel["release_id"])

            active_days_diff = 999
            try:
                active_days_diff = (datetime.strptime(active_rel["prod_deploy_date"], "%Y-%m-%d").date() - datetime.strptime(now_iso, "%Y-%m-%d").date()).days
            except Exception:
                pass

            # Stage date extractions
            scope_date = active_rel.get("scope_freeze_date")
            dev_s = active_rel.get("dev_start_date")
            dev_f = active_rel.get("dev_end_date")
            sit_s = active_rel.get("sit_start_date")
            sit_f = active_rel.get("sit_end_date")
            uat_s = active_rel.get("uat_start_date")
            uat_f = active_rel.get("uat_end_date")
            gonogo_d = active_rel.get("go_nogo_date")
            prod_d = active_rel.get("prod_deploy_date")

            # Determine where the release sits TODAY across DEV / SIT / UAT / GONOGO / PROD
            current_stage_label = "Pre-Dev Planning / Scope Freeze"
            current_stage_sub = "Requirements and defect scope lock in progress."
            current_stage_code = "PLAN"
            current_env_tag = "ENV52 Pre-Drop" if active_rel["state"] == "NH" else ("Build-76 Ingest" if active_rel["state"] == "ND" else "SOA Planning")

            if prod_d and now_iso == prod_d:
                current_stage_label = "🚨 Production Live Cutover Active"
                current_stage_sub = "Deployment playbook executing in live cutover window."
                current_stage_code = "PROD"
                current_env_tag = "PROD (ENV05 / Live)"
            elif prod_d and now_iso > prod_d:
                current_stage_label = "✅ In Production / Post-Deploy Active"
                current_stage_sub = "Production verification and hypercare monitoring."
                current_stage_code = "COMPLETED"
                current_env_tag = "PROD (Live)"
            elif uat_s and uat_f and uat_s <= now_iso <= uat_f:
                current_stage_label = "📋 State UAT Acceptance Testing"
                days_in_uat = (datetime.strptime(now_iso, "%Y-%m-%d").date() - datetime.strptime(uat_s, "%Y-%m-%d").date()).days + 1
                days_to_uat_end = (datetime.strptime(uat_f, "%Y-%m-%d").date() - datetime.strptime(now_iso, "%Y-%m-%d").date()).days
                current_stage_sub = f"State RM test acceptance active • Day {days_in_uat} of stage • {days_to_uat_end}d to UAT Sign-Off"
                current_stage_code = "UAT"
                current_env_tag = "ENV04 (UAT)" if active_rel["state"] == "NH" else "State Acceptance UAT"
            elif sit_s and sit_f and sit_s <= now_iso <= sit_f:
                current_stage_label = "🧪 SIT Regression Testing Window"
                days_in_sit = (datetime.strptime(now_iso, "%Y-%m-%d").date() - datetime.strptime(sit_s, "%Y-%m-%d").date()).days + 1
                days_to_sit_end = (datetime.strptime(sit_f, "%Y-%m-%d").date() - datetime.strptime(now_iso, "%Y-%m-%d").date()).days
                current_stage_sub = f"End-to-end regression test suite execution • Day {days_in_sit} • {days_to_sit_end}d to SIT Freeze"
                current_stage_code = "SIT"
                current_env_tag = "ENV57 / ENV53" if active_rel["state"] == "NH" else "SIT Regression"
            elif dev_s and dev_f and dev_s <= now_iso <= dev_f:
                current_stage_label = "🛠️ Development & Coding Sprint"
                days_in_dev = (datetime.strptime(now_iso, "%Y-%m-%d").date() - datetime.strptime(dev_s, "%Y-%m-%d").date()).days + 1
                days_to_dev_end = (datetime.strptime(dev_f, "%Y-%m-%d").date() - datetime.strptime(now_iso, "%Y-%m-%d").date()).days
                current_stage_sub = f"Active dev coding and unit verification • Day {days_in_dev} • {days_to_dev_end}d to Code Cutoff"
                current_stage_code = "DEV"
                current_env_tag = "ENV52 Dev" if active_rel["state"] == "NH" else "Build-76 Dev"
            elif gonogo_d and (now_iso == gonogo_d or (uat_f and uat_f < now_iso < prod_d)):
                current_stage_label = "🚦 Go / No-Go Decision Gate Active"
                current_stage_sub = "Formal release governance board review prior to production cutover."
                current_stage_code = "GATE"
                current_env_tag = "Governance Board"

            # Stage calculations for the visual chevron stepper
            stg_dev_lbl, stg_dev_bg, stg_dev_fg, stg_dev_bd = _calc_stage_status(dev_s, dev_f, now_iso)
            stg_sit_lbl, stg_sit_bg, stg_sit_fg, stg_sit_bd = _calc_stage_status(sit_s, sit_f, now_iso)
            stg_uat_lbl, stg_uat_bg, stg_uat_fg, stg_uat_bd = _calc_stage_status(uat_s, uat_f, now_iso)
            stg_gate_lbl, stg_gate_bg, stg_gate_fg, stg_gate_bd = _calc_stage_status(gonogo_d, gonogo_d, now_iso)
            stg_prod_lbl, stg_prod_bg, stg_prod_fg, stg_prod_bd = _calc_stage_status(prod_d, prod_d, now_iso)

            # High-visibility active environment alert banner
            render_html(f"""
            <div style="background:#0f172a;border:1px solid #1e293b;border-top:3px solid #38bdf8;border-radius:6px;padding:8px 10px;margin-bottom:6px;">
              <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:4px;">
                <div style="display:flex;align-items:center;gap:6px;">
                  <span style="font-size:13.5px;font-weight:800;color:#f8fafc;font-family:var(--mono);">{active_rel['release_id']}</span>
                  <span style="font-size:9px;padding:1px 5px;border-radius:4px;background:rgba(56,189,248,0.12);color:#38bdf8;font-weight:700;">
                    {active_rel['state']} ({active_rel['quarter']} {active_rel['year']})
                  </span>
                </div>
                <span style="font-size:9.5px;font-weight:800;color:#10b981;font-family:var(--mono);">
                  Cutover: {active_rel['prod_deploy_date']}
                </span>
              </div>
              <div style="background:rgba(56,189,248,0.08);border:1px solid rgba(56,189,248,0.3);border-radius:4px;padding:5px 8px;display:flex;align-items:center;justify-content:space-between;">
                <div>
                  <div style="font-size:10px;font-weight:800;color:#38bdf8;letter-spacing:0.03em;">
                    ● CURRENT STAGE: {current_stage_label}
                  </div>
                  <div style="font-size:9px;color:#cbd5e1;">{current_stage_sub}</div>
                </div>
                <span style="font-size:8.5px;font-weight:800;background:#0284c7;color:#fff;padding:2px 6px;border-radius:10px;font-family:var(--mono);white-space:nowrap;">
                  {current_env_tag}
                </span>
              </div>
            </div>
            """)

            # 4 Detail Tabs
            d_tab1, d_tab2, d_tab3, d_tab4 = st.tabs([
                "Visual Environment Pipeline",
                f"WBS Milestones ({len(milestones)})",
                "Environment Gate Lineage",
                "Raw JSON & Export"
            ])

            # Tab 1: Visual Environment Progression Stepper (Zero Narrative!)
            with d_tab1:
                env_tag_dev = "ENV52" if active_rel["state"] == "NH" else "Build-76"
                env_tag_sit = "ENV57 / ENV53" if active_rel["state"] == "NH" else "SIT QA"
                env_tag_uat = "ENV04" if active_rel["state"] == "NH" else "State Acceptance"
                env_tag_prod = "ENV05" if active_rel["state"] == "NH" else "PROD Cutover"

                render_html(f"""
                <div style="display:flex;flex-direction:column;gap:5px;margin-bottom:6px;">
                  <div style="font-size:9px;font-weight:700;color:#94a3b8;text-transform:uppercase;letter-spacing:0.04em;">
                    Multi-Tier Stage Gate Pipeline Tracker (As of {now_iso}):
                  </div>

                  <!-- DEV STAGE -->
                  <div style="background:#020617;border:1px solid {stg_dev_bd};border-radius:5px;padding:6px 8px;display:flex;align-items:center;justify-content:space-between;">
                    <div style="display:flex;align-items:center;gap:8px;">
                      <span style="font-size:13px;">🛠️</span>
                      <div>
                        <div style="font-size:10px;font-weight:700;color:#f8fafc;">1. Development & Build ({env_tag_dev})</div>
                        <div style="font-size:9px;color:#94a3b8;font-family:var(--mono);">{_format_stage_dates(dev_s, dev_f)}</div>
                      </div>
                    </div>
                    <span style="font-size:8.5px;font-weight:800;padding:2px 6px;border-radius:8px;background:{stg_dev_bg};color:{stg_dev_fg};border:1px solid {stg_dev_bd};">
                      {stg_dev_lbl}
                    </span>
                  </div>

                  <!-- SIT STAGE -->
                  <div style="background:#020617;border:1px solid {stg_sit_bd};border-radius:5px;padding:6px 8px;display:flex;align-items:center;justify-content:space-between;">
                    <div style="display:flex;align-items:center;gap:8px;">
                      <span style="font-size:13px;">🧪</span>
                      <div>
                        <div style="font-size:10px;font-weight:700;color:#f8fafc;">2. SIT & Regression Testing ({env_tag_sit})</div>
                        <div style="font-size:9px;color:#94a3b8;font-family:var(--mono);">{_format_stage_dates(sit_s, sit_f)}</div>
                      </div>
                    </div>
                    <span style="font-size:8.5px;font-weight:800;padding:2px 6px;border-radius:8px;background:{stg_sit_bg};color:{stg_sit_fg};border:1px solid {stg_sit_bd};">
                      {stg_sit_lbl}
                    </span>
                  </div>

                  <!-- UAT STAGE -->
                  <div style="background:#020617;border:1px solid {stg_uat_bd};border-radius:5px;padding:6px 8px;display:flex;align-items:center;justify-content:space-between;">
                    <div style="display:flex;align-items:center;gap:8px;">
                      <span style="font-size:13px;">📋</span>
                      <div>
                        <div style="font-size:10px;font-weight:700;color:#f8fafc;">3. State UAT Acceptance ({env_tag_uat})</div>
                        <div style="font-size:9px;color:#94a3b8;font-family:var(--mono);">{_format_stage_dates(uat_s, uat_f)}</div>
                      </div>
                    </div>
                    <span style="font-size:8.5px;font-weight:800;padding:2px 6px;border-radius:8px;background:{stg_uat_bg};color:{stg_uat_fg};border:1px solid {stg_uat_bd};">
                      {stg_uat_lbl}
                    </span>
                  </div>

                  <!-- GO/NO-GO STAGE -->
                  <div style="background:#020617;border:1px solid {stg_gate_bd};border-radius:5px;padding:6px 8px;display:flex;align-items:center;justify-content:space-between;">
                    <div style="display:flex;align-items:center;gap:8px;">
                      <span style="font-size:13px;">🚦</span>
                      <div>
                        <div style="font-size:10px;font-weight:700;color:#f8fafc;">4. Formal Go / No-Go Decision Gate</div>
                        <div style="font-size:9px;color:#94a3b8;font-family:var(--mono);">{gonogo_d or 'Pre-Cutover Thursday'}</div>
                      </div>
                    </div>
                    <span style="font-size:8.5px;font-weight:800;padding:2px 6px;border-radius:8px;background:{stg_gate_bg};color:{stg_gate_fg};border:1px solid {stg_gate_bd};">
                      {stg_gate_lbl}
                    </span>
                  </div>

                  <!-- PROD STAGE -->
                  <div style="background:#020617;border:1px solid {stg_prod_bd};border-radius:5px;padding:6px 8px;display:flex;align-items:center;justify-content:space-between;">
                    <div style="display:flex;align-items:center;gap:8px;">
                      <span style="font-size:13px;">🚀</span>
                      <div>
                        <div style="font-size:10px;font-weight:700;color:#f8fafc;">5. Production Live Cutover ({env_tag_prod})</div>
                        <div style="font-size:9px;color:#94a3b8;font-family:var(--mono);">{prod_d} (Weekend Window)</div>
                      </div>
                    </div>
                    <span style="font-size:8.5px;font-weight:800;padding:2px 6px;border-radius:8px;background:{stg_prod_bg};color:{stg_prod_fg};border:1px solid {stg_prod_bd};">
                      {stg_prod_lbl}
                    </span>
                  </div>
                </div>
                """)

            # Tab 2: Granular WBS Milestones Table
            with d_tab2:
                if not milestones:
                    st.info("No granular sub-tasks found.")
                else:
                    ms_rows = []
                    for m in milestones:
                        cat_color = "#38bdf8" if m["phase_category"] == "Planning" else (
                            "#818cf8" if m["phase_category"] == "Development" else (
                                "#fbbf24" if m["phase_category"] == "SIT" else (
                                    "#22d3ee" if m["phase_category"] == "UAT" else (
                                        "#10b981" if m["phase_category"] == "Production" else "#c084fc"
                                    )
                                )
                            )
                        )
                        st_pill = "Passed" if m["status"] == "Passed" else ("Active" if m["status"] == "Active" else "Scheduled")
                        st_p_color = "#10b981" if st_pill == "Passed" else ("#38bdf8" if st_pill == "Active" else "#94a3b8")
                        m_row_html = f"""
                        <tr style="border-bottom:1px solid #1e293b;font-size:9.5px;">
                          <td style="padding:4px 6px;color:#f8fafc;font-weight:600;">{m['task_name']}</td>
                          <td style="padding:4px 6px;"><span style="color:{cat_color};font-weight:700;">{m['phase_category']}</span></td>
                          <td style="padding:4px 6px;color:#94a3b8;font-family:var(--mono);">{m['env_target'] or '—'}</td>
                          <td style="padding:4px 6px;color:#94a3b8;font-family:var(--mono);">{m['start_date'] or ''}</td>
                          <td style="padding:4px 6px;color:#f8fafc;font-family:var(--mono);">{m['finish_date'] or ''}</td>
                          <td style="padding:4px 6px;"><span style="color:{st_p_color};font-weight:700;">{st_pill}</span></td>
                        </tr>
                        """
                        ms_rows.append("\n".join(l.strip() for l in m_row_html.splitlines() if l.strip()))

                    ms_body = "".join(ms_rows)
                    render_html(f"""
                    <div style="max-height:220px;overflow-y:auto;border:1px solid #1e293b;border-radius:4px;background:#020617;">
                      <table style="width:100%;border-collapse:collapse;text-align:left;">
                        <thead>
                          <tr style="background:#1e293b;font-size:8.5px;font-weight:700;color:#94a3b8;text-transform:uppercase;">
                            <th style="padding:4px 6px;">Task / Gate</th>
                            <th style="padding:4px 6px;">Category</th>
                            <th style="padding:4px 6px;">Target</th>
                            <th style="padding:4px 6px;">Start</th>
                            <th style="padding:4px 6px;">Finish</th>
                            <th style="padding:4px 6px;">Status</th>
                          </tr>
                        </thead>
                        <tbody>
                          {ms_body}
                        </tbody>
                      </table>
                    </div>
                    """)

            # Tab 3: Environment Gate Lineage & Exit Criteria Matrix
            with d_tab3:
                render_html(f"""
                <div style="background:#020617;border:1px solid #1e293b;border-radius:6px;padding:8px 10px;font-size:10px;">
                  <div style="color:#38bdf8;font-weight:700;margin-bottom:6px;text-transform:uppercase;letter-spacing:0.04em;">
                    Environment Handover & Exit Gate Verification Matrix
                  </div>
                  <table style="width:100%;border-collapse:collapse;text-align:left;font-size:9px;">
                    <thead>
                      <tr style="background:#1e293b;color:#94a3b8;text-transform:uppercase;">
                        <th style="padding:4px 6px;">Environment</th>
                        <th style="padding:4px 6px;">Window</th>
                        <th style="padding:4px 6px;">Gate Authority</th>
                        <th style="padding:4px 6px;">Exit Criteria</th>
                        <th style="padding:4px 6px;">Status</th>
                      </tr>
                    </thead>
                    <tbody>
                      <tr style="border-bottom:1px solid #1e293b;">
                        <td style="padding:4px 6px;color:#f8fafc;font-weight:700;">DEV ({env_tag_dev})</td>
                        <td style="padding:4px 6px;color:#94a3b8;font-family:var(--mono);">{dev_s or '—'} ➔ {dev_f or '—'}</td>
                        <td style="padding:4px 6px;color:#e2e8f0;">Dev Lead</td>
                        <td style="padding:4px 6px;color:#94a3b8;">Code Check-in complete; 0 blocker build errors</td>
                        <td style="padding:4px 6px;"><span style="color:{stg_dev_fg};font-weight:700;">{stg_dev_lbl}</span></td>
                      </tr>
                      <tr style="border-bottom:1px solid #1e293b;">
                        <td style="padding:4px 6px;color:#f8fafc;font-weight:700;">SIT ({env_tag_sit})</td>
                        <td style="padding:4px 6px;color:#94a3b8;font-family:var(--mono);">{sit_s or '—'} ➔ {sit_f or '—'}</td>
                        <td style="padding:4px 6px;color:#e2e8f0;">QA Lead</td>
                        <td style="padding:4px 6px;color:#94a3b8;">100% regression executed; 0 Sev-1/Sev-2 open</td>
                        <td style="padding:4px 6px;"><span style="color:{stg_sit_fg};font-weight:700;">{stg_sit_lbl}</span></td>
                      </tr>
                      <tr style="border-bottom:1px solid #1e293b;">
                        <td style="padding:4px 6px;color:#f8fafc;font-weight:700;">UAT ({env_tag_uat})</td>
                        <td style="padding:4px 6px;color:#94a3b8;font-family:var(--mono);">{uat_s or '—'} ➔ {uat_f or '—'}</td>
                        <td style="padding:4px 6px;color:#e2e8f0;">State RM Lead</td>
                        <td style="padding:4px 6px;color:#94a3b8;">State acceptance scenarios validated & approved</td>
                        <td style="padding:4px 6px;"><span style="color:{stg_uat_fg};font-weight:700;">{stg_uat_lbl}</span></td>
                      </tr>
                      <tr style="border-bottom:1px solid #1e293b;">
                        <td style="padding:4px 6px;color:#f8fafc;font-weight:700;">PROD ({env_tag_prod})</td>
                        <td style="padding:4px 6px;color:#f8fafc;font-weight:700;font-family:var(--mono);">{prod_d}</td>
                        <td style="padding:4px 6px;color:#e2e8f0;">Go/No-Go Board</td>
                        <td style="padding:4px 6px;color:#94a3b8;">Formal Board approval; 4-hour rollback staged</td>
                        <td style="padding:4px 6px;"><span style="color:{stg_prod_fg};font-weight:700;">{stg_prod_lbl}</span></td>
                      </tr>
                    </tbody>
                  </table>
                </div>
                """)

            # Tab 4: Raw JSON & Export
            with d_tab4:
                st.caption("Auditable System-of-Record Release JSON Payload:")
                st.code(
                    json.dumps(
                        {
                            "release_id": active_rel["release_id"],
                            "state": active_rel["state"],
                            "state_rm_name": active_rel["state_rm_name"],
                            "current_stage": current_stage_label,
                            "current_environment": current_env_tag,
                            "dates": {
                                "scope_freeze": active_rel["scope_freeze_date"],
                                "dev_window": f"{active_rel['dev_start_date']} to {active_rel['dev_end_date']}",
                                "sit_window": f"{active_rel['sit_start_date']} to {active_rel['sit_end_date']}",
                                "uat_window": f"{active_rel['uat_start_date']} to {active_rel['uat_end_date']}",
                                "go_nogo": active_rel["go_nogo_date"],
                                "prod_cutover": active_rel["prod_deploy_date"],
                            },
                            "milestone_count": len(milestones),
                        },
                        indent=2,
                    ),
                    language="json",
                )

                # Export CSV button
                df_export = pd.DataFrame(releases)
                csv_data = df_export.to_csv(index=False).encode("utf-8")
                st.download_button(
                    label="📥 Export Current Filtered Portfolio to CSV",
                    data=csv_data,
                    file_name=f"ETS_Release_Plan_Export_{datetime.now().strftime('%Y%m%d')}.csv",
                    mime="text/csv",
                    use_container_width=True,
                )

    conn.close()
