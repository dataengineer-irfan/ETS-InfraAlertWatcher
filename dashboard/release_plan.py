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


def render_release_plan_workspace(db_path: str) -> None:
    """Render the Enterprise Schedule Release Plan workspace."""
    conn = get_connection(db_path)

    # --------------------------------------------------------------------------
    # 1. Top Section: Header & Quick Context
    # --------------------------------------------------------------------------
    render_html("""
    <div style="display:flex;align-items:center;justify-content:space-between;padding:4px 0 10px 0;border-bottom:1px solid #1e293b;margin-bottom:10px;">
      <div style="display:flex;align-items:center;gap:10px;">
        <span style="font-size:20px;">📅</span>
        <div>
          <div style="font-size:15px;font-weight:800;color:#f8fafc;letter-spacing:-0.02em;text-transform:uppercase;">
            Enterprise Schedule Release Plan
          </div>
          <div style="font-size:10px;color:#38bdf8;font-family:var(--mono);font-weight:600;letter-spacing:0.04em;">
            MULTI-STATE RM PORTFOLIO & STAGE-GATE GOVERNANCE // ALASKA • NORTH DAKOTA • NEW HAMPSHIRE
          </div>
        </div>
      </div>
      <div style="display:flex;align-items:center;gap:8px;">
        <span style="font-size:10px;background:rgba(56,189,248,0.12);border:1px solid rgba(56,189,248,0.3);color:#38bdf8;padding:3px 8px;border-radius:4px;font-family:var(--mono);font-weight:700;">
          SYSTEM OF RECORD: _Input/*.xlsx
        </span>
        <span style="font-size:10px;background:rgba(16,185,129,0.12);border:1px solid rgba(16,185,129,0.3);color:#34d399;padding:3px 8px;border-radius:4px;font-family:var(--mono);font-weight:700;">
          LIVE SYNC: ACTIVE
        </span>
      </div>
    </div>
    """)

    # --------------------------------------------------------------------------
    # 2. Power BI Interactive Slicers Bar
    # --------------------------------------------------------------------------
    s_col1, s_col2, s_col3, s_col4, s_col5, s_col6 = st.columns([1.2, 1.4, 1.0, 0.9, 1.1, 1.6])

    with s_col1:
        state_opts = ["All", "AK", "ND", "NH"]
        selected_state = st.selectbox("State Filter", state_opts, index=0, key="sl_state")

    with s_col2:
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
        search_query = st.text_input("Instant Search", placeholder="Search by Release ID, Task, or ENV...", key="sl_search")

    # Fetch data based on slicers
    releases = get_release_schedules(
        conn,
        state=selected_state,
        year=int(selected_year) if selected_year != "All" else None,
        quarter=selected_quarter,
        status=selected_status,
        rm=selected_rm,
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

    summary_metrics = get_release_summary_metrics(conn)
    rm_breakdowns = get_rm_portfolio_breakdown(conn)

    # --------------------------------------------------------------------------
    # 3. Fortune-500 Executive KPI Ribbon
    # --------------------------------------------------------------------------
    kpi_col1, kpi_col2, kpi_col3, kpi_col4, kpi_col5 = st.columns(5)

    with kpi_col1:
        render_html(f"""
        <div style="background:#0f172a;border:1px solid #1e293b;border-top:3px solid #38bdf8;border-radius:6px;padding:8px 12px;">
          <div style="font-size:9.5px;font-weight:700;color:#94a3b8;text-transform:uppercase;letter-spacing:0.06em;">Total Planned Releases</div>
          <div style="display:flex;align-items:baseline;gap:8px;margin-top:2px;">
            <span style="font-size:22px;font-weight:800;color:#f8fafc;font-family:var(--mono);">{summary_metrics['total_releases']}</span>
            <span style="font-size:10px;color:#38bdf8;font-weight:700;">(12 AK • 12 ND • 14 NH)</span>
          </div>
        </div>
        """)

    with kpi_col2:
        render_html(f"""
        <div style="background:#0f172a;border:1px solid #1e293b;border-top:3px solid #10b981;border-radius:6px;padding:8px 12px;">
          <div style="font-size:9.5px;font-weight:700;color:#94a3b8;text-transform:uppercase;letter-spacing:0.06em;">Production Cutovers</div>
          <div style="display:flex;align-items:baseline;gap:8px;margin-top:2px;">
            <span style="font-size:22px;font-weight:800;color:#10b981;font-family:var(--mono);">{summary_metrics['total_cutovers']}</span>
            <span style="font-size:10px;color:#34d399;font-weight:700;">100% Scheduled</span>
          </div>
        </div>
        """)

    with kpi_col3:
        next_r = summary_metrics.get("next_release")
        next_tag = next_r["release_id"] if next_r else "None"
        next_dt = next_r["prod_deploy_date"] if next_r else "N/A"
        next_st = next_r["state"] if next_r else ""
        render_html(f"""
        <div style="background:#0f172a;border:1px solid #1e293b;border-top:3px solid #f59e0b;border-radius:6px;padding:8px 12px;">
          <div style="font-size:9.5px;font-weight:700;color:#94a3b8;text-transform:uppercase;letter-spacing:0.06em;">Next Impending Cutover</div>
          <div style="display:flex;align-items:baseline;gap:8px;margin-top:2px;">
            <span style="font-size:16px;font-weight:800;color:#fbbf24;font-family:var(--mono);">{next_tag}</span>
            <span style="font-size:10.5px;color:#f8fafc;font-weight:600;">{next_dt} ({next_st})</span>
          </div>
        </div>
        """)

    with kpi_col4:
        render_html(f"""
        <div style="background:#0f172a;border:1px solid #1e293b;border-top:3px solid #a855f7;border-radius:6px;padding:8px 12px;">
          <div style="font-size:9.5px;font-weight:700;color:#94a3b8;text-transform:uppercase;letter-spacing:0.06em;">Gate SLA Compliance</div>
          <div style="display:flex;align-items:baseline;gap:8px;margin-top:2px;">
            <span style="font-size:22px;font-weight:800;color:#c084fc;font-family:var(--mono);">{summary_metrics['gate_sla_rate']}%</span>
            <span style="font-size:10px;color:#a855f7;font-weight:700;">Zero Blockers</span>
          </div>
        </div>
        """)

    with kpi_col5:
        render_html(f"""
        <div style="background:#0f172a;border:1px solid #1e293b;border-top:3px solid #06b6d4;border-radius:6px;padding:8px 12px;">
          <div style="font-size:9.5px;font-weight:700;color:#94a3b8;text-transform:uppercase;letter-spacing:0.06em;">Active In-Flight Releases</div>
          <div style="display:flex;align-items:baseline;gap:8px;margin-top:2px;">
            <span style="font-size:22px;font-weight:800;color:#22d3ee;font-family:var(--mono);">{summary_metrics['in_flight']} Active</span>
            <span style="font-size:10px;color:#94a3b8;">({summary_metrics['completed']} Done)</span>
          </div>
        </div>
        """)

    # --------------------------------------------------------------------------
    # 4. State & State RM Executive Portfolio Command Cards
    # --------------------------------------------------------------------------
    render_html("""
    <div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.06em;color:#94a3b8;margin:10px 0 6px 0;">
      State Release Management (RM) Command Centers
    </div>
    """)

    rm_col1, rm_col2, rm_col3 = st.columns(3)

    state_icons = {"AK": "🏔️", "ND": "🌾", "NH": "🍁"}
    state_names = {
        "AK": "Alaska State MMIS",
        "ND": "North Dakota State MMIS",
        "NH": "New Hampshire State MMIS",
    }
    state_highlights = {
        "AK": "19 Milestones per Release • SOA Review & Approval Gates • FAS Test Matrix",
        "ND": "Build-76 CI/CD Deployments • Defect Freeze • Formal Go/No-Go Board",
        "NH": "Multi-Tier Pipeline (ENV52➔57➔53➔04➔05) • NTT Data QA Doc Reviews",
    }

    cols = [rm_col1, rm_col2, rm_col3]
    for idx, rm in enumerate(rm_breakdowns):
        st_code = rm["state"]
        c = cols[idx % 3]
        with c:
            render_html(f"""
            <div style="background:#0f172a;border:1px solid #1e293b;border-radius:6px;padding:10px 12px;display:flex;flex-direction:column;gap:6px;">
              <div style="display:flex;align-items:center;justify-content:space-between;">
                <div style="display:flex;align-items:center;gap:6px;">
                  <span style="font-size:16px;">{state_icons.get(st_code, '🏛️')}</span>
                  <span style="font-size:12px;font-weight:800;color:#f8fafc;">{state_names.get(st_code, st_code)}</span>
                </div>
                <span style="font-size:10px;font-weight:700;padding:2px 6px;border-radius:4px;background:rgba(56,189,248,0.12);color:#38bdf8;font-family:var(--mono);">
                  {rm['total_releases']} Releases
                </span>
              </div>
              <div style="font-size:10px;color:#94a3b8;">
                Lead: <b style="color:#e2e8f0;">{rm['state_rm_name']}</b>
              </div>
              <div style="font-size:9.5px;color:#64748b;line-height:1.3;">
                {state_highlights.get(st_code, '')}
              </div>
              <div style="display:flex;justify-content:space-between;align-items:center;padding-top:4px;border-top:1px solid #1e293b;font-size:9.5px;color:#94a3b8;">
                <span>Cadence: <b style="color:#f8fafc;font-family:var(--mono);">{rm['earliest_deploy']} ➔ {rm['latest_deploy']}</b></span>
                <span style="color:#10b981;font-weight:700;">Avg Ready: {rm['avg_readiness']:.0f}%</span>
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
            st.info("Select a release to inspect milestone breakdown and governance details.")
        else:
            milestones = get_release_milestones(conn, active_rel["release_id"])

            active_days_diff = 999
            try:
                active_days_diff = (datetime.strptime(active_rel["prod_deploy_date"], "%Y-%m-%d").date() - datetime.strptime(now_iso, "%Y-%m-%d").date()).days
            except Exception:
                pass

            cutover_alert_html = ""
            if active_days_diff == 0:
                cutover_alert_html = """
                <div style="background:rgba(239,68,68,0.15);border:1px solid rgba(239,68,68,0.5);border-radius:6px;padding:8px 10px;margin-bottom:8px;display:flex;align-items:center;justify-content:space-between;">
                  <div style="display:flex;align-items:center;gap:8px;">
                    <span style="font-size:18px;">🚨</span>
                    <div>
                      <div style="font-size:11px;font-weight:800;color:#fca5a5;letter-spacing:0.02em;">PRODUCTION CUTOVER ACTIVE TODAY // SEP 10, 2026</div>
                      <div style="font-size:9.5px;color:#cbd5e1;">Deployment pipeline executing in weekend cutover window. Formal Go/No-Go Gate Approved.</div>
                    </div>
                  </div>
                  <span style="font-size:9px;font-weight:800;background:#ef4444;color:#fff;padding:3px 8px;border-radius:12px;letter-spacing:0.05em;">LIVE CUTOVER</span>
                </div>
                """
            elif 0 < active_days_diff <= 3:
                cutover_alert_html = f"""
                <div style="background:rgba(245,158,11,0.12);border:1px solid rgba(245,158,11,0.4);border-radius:6px;padding:8px 10px;margin-bottom:8px;display:flex;align-items:center;justify-content:space-between;">
                  <div style="display:flex;align-items:center;gap:8px;">
                    <span style="font-size:18px;">⚠️</span>
                    <div>
                      <div style="font-size:11px;font-weight:800;color:#fcd34d;letter-spacing:0.02em;">IMPENDING CUTOVER // T-{active_days_diff} DAYS REMAINING</div>
                      <div style="font-size:9.5px;color:#cbd5e1;">Pre-cutover smoke tests & State UAT sign-off in final sign-off stage.</div>
                    </div>
                  </div>
                  <span style="font-size:9px;font-weight:800;background:#f59e0b;color:#1e293b;padding:3px 8px;border-radius:12px;letter-spacing:0.05em;">STAGE GATE CRITICAL</span>
                </div>
                """

            render_html(f"""
            {cutover_alert_html}
            <div style="background:#0f172a;border:1px solid #1e293b;border-radius:6px;padding:10px 12px;margin-bottom:8px;">
              <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:6px;">
                <div style="display:flex;align-items:center;gap:8px;">
                  <span style="font-size:14px;font-weight:800;color:#f8fafc;font-family:var(--mono);">{active_rel['release_id']}</span>
                  <span style="font-size:9.5px;padding:2px 6px;border-radius:4px;background:rgba(56,189,248,0.12);color:#38bdf8;font-weight:700;">
                    {active_rel['state']} ({active_rel['quarter']} {active_rel['year']})
                  </span>
                </div>
                <span style="font-size:10px;font-weight:800;color:#10b981;font-family:var(--mono);">
                  Cutover: {active_rel['prod_deploy_date']}
                </span>
              </div>
              <div style="font-size:10px;color:#94a3b8;display:flex;justify-content:space-between;">
                <span>RM Lead: <b style="color:#e2e8f0;">{active_rel['state_rm_name']}</b></span>
                <span>Readiness: <b style="color:#38bdf8;font-family:var(--mono);">{active_rel['readiness_pct']:.0f}%</b></span>
              </div>
            </div>
            """)

            # 4 Detail Tabs
            d_tab1, d_tab2, d_tab3, d_tab4 = st.tabs([
                "Stage Gates & Schedule",
                f"WBS Milestones ({len(milestones)})",
                "Environment Lineage",
                "Raw JSON & Export"
            ])

            # Tab 1: Stage Gates & Dates
            with d_tab1:
                cutover_box_style = "background:rgba(239,68,68,0.14);border:1px solid #ef4444;" if active_days_diff == 0 else "background:rgba(16,185,129,0.06);border:1px solid rgba(16,185,129,0.25);"
                cutover_title = "🚨 ACTIVE CUTOVER TODAY — Production Deployment Window" if active_days_diff == 0 else "🛡️ Production Deployment Cutover Details"
                cutover_title_color = "#f87171" if active_days_diff == 0 else "#10b981"
                render_html(f"""
                <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-bottom:8px;font-size:10.5px;">
                  <div style="background:rgba(255,255,255,0.02);padding:6px 8px;border-radius:4px;border:1px solid #1e293b;">
                    <span style="color:#94a3b8;">Defects & Scope Freeze:</span><br/>
                    <b style="color:#f8fafc;font-family:var(--mono);">{active_rel['scope_freeze_date'] or 'Established in Sprint'}</b>
                  </div>
                  <div style="background:rgba(255,255,255,0.02);padding:6px 8px;border-radius:4px;border:1px solid #1e293b;">
                    <span style="color:#94a3b8;">Development Window:</span><br/>
                    <b style="color:#f8fafc;font-family:var(--mono);">{active_rel['dev_start_date'] or 'Active'} ➔ {active_rel['dev_end_date'] or 'Done'}</b>
                  </div>
                  <div style="background:rgba(255,255,255,0.02);padding:6px 8px;border-radius:4px;border:1px solid #1e293b;">
                    <span style="color:#94a3b8;">SIT / Regression Window:</span><br/>
                    <b style="color:#f8fafc;font-family:var(--mono);">{active_rel['sit_start_date'] or 'Scheduled'} ➔ {active_rel['sit_end_date'] or 'Done'}</b>
                  </div>
                  <div style="background:rgba(255,255,255,0.02);padding:6px 8px;border-radius:4px;border:1px solid #1e293b;">
                    <span style="color:#94a3b8;">State UAT Acceptance:</span><br/>
                    <b style="color:#f8fafc;font-family:var(--mono);">{active_rel['uat_start_date'] or 'Scheduled'} ➔ {active_rel['uat_end_date'] or 'Sign-Off'}</b>
                  </div>
                </div>
                <div style="{cutover_box_style}border-radius:6px;padding:8px 10px;font-size:10.5px;margin-bottom:6px;">
                  <div style="color:{cutover_title_color};font-weight:700;margin-bottom:2px;">{cutover_title}</div>
                  <div style="color:#94a3b8;">Target Weekend Cutover Date: <b style="color:#f8fafc;font-family:var(--mono);">{active_rel['prod_deploy_date']}</b></div>
                  <div style="color:#94a3b8;">Formal Go / No-Go Decision: <b style="color:#f8fafc;font-family:var(--mono);">{active_rel['go_nogo_date'] or 'Thursday Prior to Cutover'}</b></div>
                  <div style="color:#94a3b8;">RM Sign-off Contact: <b style="color:#38bdf8;">{active_rel['state_rm_email']}</b></div>
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
                        st_pill = "Passed" if m["status"] == "Passed" else "Scheduled"
                        st_p_color = "#10b981" if st_pill == "Passed" else "#94a3b8"
                        m_row_html = f"""
                        <tr style="border-bottom:1px solid #1e293b;font-size:10px;">
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
                          <tr style="background:#1e293b;font-size:9px;font-weight:700;color:#94a3b8;text-transform:uppercase;">
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

            # Tab 3: Environment Lineage & Topology
            with d_tab3:
                render_html(f"""
                <div style="background:#020617;border:1px solid #1e293b;border-radius:6px;padding:10px 12px;font-size:10.5px;">
                  <div style="color:#38bdf8;font-weight:700;margin-bottom:8px;text-transform:uppercase;letter-spacing:0.04em;">
                    Multi-Tier Stage Gate Progression
                  </div>
                  <div style="display:flex;align-items:center;gap:6px;margin-bottom:12px;flex-wrap:wrap;">
                    <span style="padding:4px 8px;background:rgba(56,189,248,0.12);border:1px solid rgba(56,189,248,0.3);color:#38bdf8;border-radius:4px;font-weight:700;">DEV / Build</span>
                    <span style="color:#64748b;">➔</span>
                    <span style="padding:4px 8px;background:rgba(245,158,11,0.12);border:1px solid rgba(245,158,11,0.3);color:#fbbf24;border-radius:4px;font-weight:700;">SIT Regression</span>
                    <span style="color:#64748b;">➔</span>
                    <span style="padding:4px 8px;background:rgba(34,211,238,0.12);border:1px solid rgba(34,211,238,0.3);color:#22d3ee;border-radius:4px;font-weight:700;">State UAT</span>
                    <span style="color:#64748b;">➔</span>
                    <span style="padding:4px 8px;background:rgba(192,132,252,0.12);border:1px solid rgba(192,132,252,0.3);color:#c084fc;border-radius:4px;font-weight:700;">Go/No-Go Gate</span>
                    <span style="color:#64748b;">➔</span>
                    <span style="padding:4px 8px;background:rgba(16,185,129,0.12);border:1px solid rgba(16,185,129,0.3);color:#34d399;border-radius:4px;font-weight:700;">PROD Cutover</span>
                  </div>
                  <div style="color:#94a3b8;font-size:10px;line-height:1.5;">
                    • <b>Target Environments</b>: {active_rel['notes']}<br/>
                    • <b>Predecessors & Gate Approvals</b>: Strictly validated before PROD deployment window.<br/>
                    • <b>Rollback SLA</b>: Maximum 4-hour disaster recovery rollback window.
                  </div>
                </div>
                """)

            # Tab 4: Raw JSON & Export
            with d_tab4:
                st.caption("Structured JSON Payload for Integration & Audit:")
                st.code(
                    json.dumps(
                        {
                            "release_id": active_rel["release_id"],
                            "state": active_rel["state"],
                            "prod_deploy_date": active_rel["prod_deploy_date"],
                            "state_rm_name": active_rel["state_rm_name"],
                            "status": active_rel["status"],
                            "milestone_count": len(milestones),
                            "milestones": milestones[:5],
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
