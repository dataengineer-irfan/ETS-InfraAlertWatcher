"""
operations_hub.py — Portfolio Matrix & Operations Hub (Power BI Executive Workspace)
===================================================================================
Enterprise-grade zero-scroll operations hub for component expiry management,
cross-tab multi-team risk matrices, organizational hierarchy trees, and batch renewals.

Adheres strictly to the Enterprise Dashboard Architect standard:
  - Universal 1-line command bar (Brand, Search, State, Team, Component, Health, Telemetry, CSV, Reset)
  - Dynamic 4-KPI ribbon with click-to-filter capability and sparkline/donut trends
  - 5-Tab Enterprise Architecture:
      1. 📋 Fleet Inventory & Master-Detail (Strictly synchronized 58% / 42% split pane)
      2. 🗺️ Severity Matrix & Cross-Tab Heatmap (Dedicated full-width matrix & SLA telemetry)
      3. ⚡ Batch Operations Console (Multi-entity bulk date updates & interactive editor)
      4. 🌳 Organizational Hierarchy Tree (State -> Team -> Component -> Asset explorer)
      5. ↩️ Rollback & Audit History Ledger (Revision tracking & 1-click revert console)
  - Zero outer page scroll (Viewport locked to 100vh with internal scroll containers)
"""

from __future__ import annotations

import json
from datetime import datetime, timezone, date
from html import escape
from pathlib import Path

import pandas as pd
import streamlit as st

import ui
from db import (
    get_connection,
    get_metric_snapshots,
    revert_component_exp_date,
    update_component_exp_date,
    log_audit_event,
)
from auth import can_write, check_state_scope, ROLE_OPERATOR

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = str(ROOT / "data" / "expiry.db")

STATES = ui.STATES
COMPONENT_ORDER = ui.COMPONENT_ORDER


def _on_reset_operations_hub() -> None:
    """Pre-execution callback to reliably clear all filter states without StreamlitAPIException."""
    st.session_state["op_reset_idx"] = st.session_state.get("op_reset_idx", 0) + 1
    st.session_state["op_kpi_filter"] = "All"
    st.session_state["op_cell_filter"] = None
    st.session_state["op_cell_team"] = None
    st.session_state["op_cell_env"] = None
    st.session_state["op_heatmap_dim"] = "auto"
    st.session_state["op_tree_open"] = set()
    st.session_state["op_selected_entity_ids"] = set()
    st.session_state["op_batch_page_no"] = 0
    st.session_state["_override_canvas_state"] = None
    st.session_state["confirm_action"] = None
    for k in list(st.session_state.keys()):
        if any(k.startswith(p) for p in ["op_state_", "op_team_", "op_comp_", "op_health_", "op_search_"]):
            del st.session_state[k]


def _apply_edits(changes: list, state_scope: str | None = None) -> None:
    """Persist date modifications to SQLite and log audit event."""
    if not can_write():
        st.error("Access Denied: Modifying expiry dates requires Operator or Admin role.")
        return
    if state_scope and not check_state_scope(state_scope):
        st.error(f"Access Denied: Your assigned state scope does not permit modifying {state_scope} records.")
        return
    conn = get_connection(DB_PATH)
    active_user = st.session_state.get("active_user", "Operator")
    active_role = st.session_state.get("user_role", ROLE_OPERATOR)
    for record_id, new_date in changes:
        dt_str = new_date.isoformat() if hasattr(new_date, "isoformat") else str(new_date)[:10]
        update_component_exp_date(conn, int(record_id), dt_str)
        log_audit_event(
            conn,
            actor=active_user,
            role=active_role,
            action="EXPIRY_EDITED",
            target_entity=f"Record #{record_id}",
            details=f"Expiry date updated to {dt_str}",
        )
    conn.close()
    st.session_state["_bust"] = st.session_state.get("_bust", 0) + 1
    st.cache_data.clear()


def _search_records(df: pd.DataFrame, query: str) -> pd.DataFrame:
    """Case-insensitive multi-column substring filter."""
    if not query or not query.strip():
        return df
    needle = query.strip().lower()
    haystack = (
        df["schema_name"].str.lower() + " " +
        df["env_label"].str.lower() + " " +
        df["component"].str.lower() + " " +
        df["team"].str.lower() + " " +
        df["state"].str.lower() + " " +
        df["env_no"].astype(str) + " " +
        df["exp_date"].astype(str)
    )
    return df[haystack.str.contains(needle, regex=False, na=False)]


def _render_html(html_str: str) -> None:
    """Render HTML safely without markdown code-block escaping."""
    cleaned = "\n".join(line.strip() for line in html_str.splitlines() if line.strip())
    st.markdown(cleaned, unsafe_allow_html=True)


def render_operations_hub(df: pd.DataFrame) -> None:
    """Renders the modernized Operations Hub workspace with zero outer-scroll."""
    # 0. Session state defaults and dynamic key versioning for error-free resets
    reset_idx = st.session_state.setdefault("op_reset_idx", 0)
    cur_kpi = st.session_state.setdefault("op_kpi_filter", "All")
    cell_filter = st.session_state.setdefault("op_cell_filter", None)
    cell_team = st.session_state.setdefault("op_cell_team", None)
    cell_env = st.session_state.setdefault("op_cell_env", None)
    tree_open = st.session_state.setdefault("op_tree_open", set())
    selected_entity_ids = st.session_state.setdefault("op_selected_entity_ids", set())

    active_user = st.session_state.get("active_user", "admin")
    auth_suffix = f"&_auth_user={active_user}&tab=2"

    # Process query parameters
    qp_dim = st.query_params.get("op_dim")
    if qp_dim:
        if qp_dim in ["state", "team", "env", "auto"]:
            st.session_state["op_heatmap_dim"] = qp_dim
        del st.query_params["op_dim"]

    qp_kpi = st.query_params.get("op_kpi")
    if qp_kpi:
        if qp_kpi in ["All", "Expired", "Urgent", "Healthy"]:
            st.session_state["op_kpi_filter"] = qp_kpi
            st.session_state["op_cell_filter"] = None
            st.session_state["op_cell_team"] = None
            st.session_state["op_cell_env"] = None
            if qp_kpi == "Expired":
                tree_open.clear()
                tree_open.add("ND")
                tree_open.add("ND/Core")
                tree_open.add("ND/Core/Database Password Expiry")
            elif qp_kpi == "Urgent":
                tree_open.clear()
                tree_open.add("AK")
                tree_open.add("NH")
        del st.query_params["op_kpi"]

    qp_cell = st.query_params.get("op_cell")
    if qp_cell:
        if qp_cell == "clear":
            st.session_state["op_cell_filter"] = None
            st.session_state["op_cell_team"] = None
            st.session_state["op_cell_env"] = None
        elif ":" in qp_cell:
            parts = qp_cell.split(":")
            p_st = parts[0] if len(parts) > 0 and parts[0] and parts[0] != "ALL" else None
            p_cp = parts[1] if len(parts) > 1 and parts[1] else None
            p_tm = parts[2] if len(parts) > 2 and parts[2] else None
            p_env = parts[3] if len(parts) > 3 and parts[3] else None

            cur_filter = st.session_state.get("op_cell_filter")
            cur_tm = st.session_state.get("op_cell_team")
            cur_env = st.session_state.get("op_cell_env")

            if cur_filter == (p_st, p_cp) and cur_tm == p_tm and cur_env == p_env:
                st.session_state["op_cell_filter"] = None
                st.session_state["op_cell_team"] = None
                st.session_state["op_cell_env"] = None
            else:
                st.session_state["op_cell_filter"] = (p_st, p_cp)
                st.session_state["op_cell_team"] = p_tm
                st.session_state["op_cell_env"] = p_env
                if p_st:
                    tree_open.clear()
                    tree_open.add(p_st)
                    if p_cp:
                        sub_matches = df[(df["state"] == p_st) & (df["component"] == p_cp)]
                        for t in sub_matches["team"].unique():
                            tree_open.add(f"{p_st}/{t}")
                            tree_open.add(f"{p_st}/{t}/{p_cp}")
        del st.query_params["op_cell"]

    cell_filter = st.session_state.get("op_cell_filter")
    cell_team = st.session_state.get("op_cell_team")
    cell_env = st.session_state.get("op_cell_env")

    qp_act = st.query_params.get("op_act_id")
    if qp_act:
        try:
            st.session_state["op_active_id"] = int(qp_act)
        except Exception:
            pass
        del st.query_params["op_act_id"]

    qp_subtab = st.query_params.get("op_subtab")
    if qp_subtab:
        if qp_subtab in ["inv", "master"]:
            st.session_state["op_target_tab"] = "📋 Fleet Inventory & Master-Detail"
        elif qp_subtab in ["matrix", "heat", "hm"]:
            st.session_state["op_target_tab"] = "🗺️ Severity Matrix & Cross-Tab Heatmap"
        elif qp_subtab == "batch":
            st.session_state["op_target_tab"] = "⚡ Batch Operations Console"
        elif qp_subtab == "tree":
            st.session_state["op_target_tab"] = "🌳 Organizational Hierarchy Tree"
        elif qp_subtab == "rev":
            st.session_state["op_target_tab"] = "↩️ Rollback & Audit History Ledger"
        del st.query_params["op_subtab"]

    # Optional initial state scope if navigated from Release Plan
    active_scope = st.session_state.get("_override_canvas_state")
    op_st_key = f"op_state_{reset_idx}"
    if active_scope and active_scope in STATES and op_st_key not in st.session_state:
        st.session_state[op_st_key] = active_scope

    # ==========================================================================
    # 1. UNIVERSAL 1-LINE COMMAND BAR (Brand & Scope | 5 Slicers | Telemetry & Actions)
    # ==========================================================================
    c_brand, c_f1, c_f2, c_f3, c_f4, c_f5, c_telem, c_csv, c_reset = st.columns(
        [1.35, 1.4, 1.05, 1.15, 1.15, 1.05, 1.25, 0.5, 0.35],
        gap="small"
    )

    with c_f1:
        q = st.text_input("Filter", key=f"op_search_{reset_idx}", placeholder="🔍 Search...", label_visibility="collapsed", autocomplete="off")
    with c_f2:
        state_opts = ["All States"] + STATES
        state_filter = st.selectbox("State", state_opts, key=op_st_key, label_visibility="collapsed")
    with c_f3:
        team_filter = st.selectbox("Team", ["All Teams"] + ui.TEAMS, key=f"op_team_{reset_idx}", label_visibility="collapsed")
    with c_f4:
        comp_filter = st.selectbox(
            "Component",
            ["All Components"] + COMPONENT_ORDER,
            key=f"op_comp_{reset_idx}",
            label_visibility="collapsed",
            format_func=lambda c: ui.COMPONENT_CODE.get(c, c) if c != "All Components" else "All Components",
        )
    with c_f5:
        health_filter = st.selectbox("Health", ["All Health"] + ui.BANDS, key=f"op_health_{reset_idx}", label_visibility="collapsed")

    # Apply slicer filters across the complete dataset
    filtered = df.copy()
    if q:
        filtered = _search_records(filtered, q)
    if state_filter != "All States":
        filtered = filtered[filtered["state"] == state_filter]
    if team_filter != "All Teams":
        filtered = filtered[filtered["team"] == team_filter]
    if comp_filter != "All Components":
        filtered = filtered[filtered["component"] == comp_filter]
    if health_filter != "All Health":
        filtered = filtered[filtered["band"] == health_filter]

    if cell_filter:
        c_st, c_cp = cell_filter
        if c_st:
            filtered = filtered[filtered["state"] == c_st]
        if c_cp:
            filtered = filtered[filtered["component"] == c_cp]
    if cell_team:
        filtered = filtered[filtered["team"] == cell_team]
    if cell_env:
        filtered = filtered[filtered["env_label"] == cell_env]

    if cur_kpi == "Expired":
        filtered = filtered[filtered["band"] == "Expired"]
    elif cur_kpi == "Urgent":
        filtered = filtered[filtered["band"].isin(["Critical", "Warning"])]
    elif cur_kpi == "Healthy":
        filtered = filtered[filtered["band"] == "Healthy"]

    filtered = filtered.sort_values("days_left")

    # Scope & Telemetry Determination
    is_scoped = (
        bool(q) or
        state_filter != "All States" or
        team_filter != "All Teams" or
        comp_filter != "All Components" or
        health_filter != "All Health" or
        cur_kpi != "All" or
        cell_filter is not None or
        cell_team is not None or
        cell_env is not None or
        len(tree_open) > 0 or
        len(selected_entity_ids) > 0
    )

    _utc_now = datetime.now(timezone.utc).strftime("%H:%M UTC")

    # Production SLA Exposure & Operational Debt Breakdown
    sc_prod_exp = int(((filtered["days_left"] < 0) & (filtered["env_label"] == "PROD")).sum())
    sc_dr_exp = int(((filtered["days_left"] < 0) & (filtered["env_label"] == "DR")).sum())
    sc_mo_exp = int(((filtered["days_left"] < 0) & (filtered["env_label"] == "MO")).sum())

    if sc_prod_exp == 0:
        sla_callout = '<span style="font-size:8px;font-weight:700;background:rgba(115,191,105,0.16);color:#73bf69;border:1px solid rgba(115,191,105,0.3);padding:1.5px 5px;border-radius:2px;white-space:nowrap;">🛡️ PROD 100%</span>'
    else:
        sla_callout = f'<span style="font-size:8px;font-weight:700;background:rgba(242,73,92,0.18);color:#f2495c;border:1px solid rgba(242,73,92,0.4);padding:1.5px 5px;border-radius:2px;white-space:nowrap;">⚠️ PROD: {sc_prod_exp} Overdue</span>'

    debt_cnt = sc_dr_exp + sc_mo_exp
    debt_callout = f'<span style="font-size:8px;font-weight:700;background:rgba(255,152,48,0.15);color:#ff9830;border:1px solid rgba(255,152,48,0.3);padding:1.5px 5px;border-radius:2px;white-space:nowrap;">⚠️ Debt: {debt_cnt}</span>' if debt_cnt > 0 else ''

    with c_brand:
        _brand_html = f'<div style="display:flex;align-items:center;gap:6px;height:30px;padding-top:2px;" title="Portfolio Operations Hub · Cross-Tab Multi-Team Expiry & Asset Inventory"><div style="width:3px;height:18px;background:#f59e0b;border-radius:1px;flex:none;"></div><span style="font-size:11px;font-weight:800;letter-spacing:0.04em;color:#f8fafc;white-space:nowrap;">OPERATIONS HUB</span><span style="font-size:7.5px;font-weight:800;background:rgba(16,185,129,0.18);color:#10b981;border:1px solid rgba(16,185,129,0.35);padding:1px 5px;border-radius:2px;white-space:nowrap;">LIVE</span><span style="font-size:9px;color:#94a3b8;font-family:var(--mono);white-space:nowrap;">({len(filtered)}/{len(df)})</span></div>'
        st.markdown(_brand_html, unsafe_allow_html=True)

    with c_telem:
        _telem_parts = [p for p in [sla_callout, debt_callout, f'<span style="font-size:8px;color:#64748b;font-family:var(--mono);white-space:nowrap;">{_utc_now}</span>'] if p]
        _telem_html = f'<div style="display:flex;align-items:center;justify-content:flex-end;gap:5px;height:30px;line-height:1;box-sizing:border-box;">{" ".join(_telem_parts)}</div>'
        st.markdown(_telem_html, unsafe_allow_html=True)

    with c_csv:
        st.markdown(
            ui.csv_download_button(
                df=filtered,
                filename=f"expiry_operations_{date.today().isoformat()}.csv",
                label="📥 CSV",
                key=f"op_export_csv_{reset_idx}",
            ),
            unsafe_allow_html=True,
        )

    with c_reset:
        st.button(
            "↺",
            key=f"op_sc_reset_btn_{reset_idx}",
            on_click=_on_reset_operations_hub,
            use_container_width=True,
            type="secondary",
            help="Reset all filters and restore full fleet coverage",
        )

    # ==========================================================================
    # 2. DYNAMIC 4-KPI RIBBON (Single 48px row with click-to-filter)
    # ==========================================================================
    tot_cnt = len(df)
    scope_cnt = len(filtered)
    exp_cnt = int((filtered["days_left"] < 0).sum())
    crit_cnt = int((filtered["days_left"].between(0, ui.CRITICAL_DAYS)).sum())
    warn_cnt = int((filtered["days_left"].between(ui.CRITICAL_DAYS + 1, ui.WARNING_DAYS)).sum())
    hlth_cnt = int((filtered["days_left"] > ui.WARNING_DAYS).sum())
    g_exp = int((df["days_left"] < 0).sum())

    k1_sub = f"Filtered scope ({scope_cnt} of {tot_cnt})" if scope_cnt < tot_cnt else "Consolidated fleet coverage"
    k2_sub = f"Requires renewal ({g_exp} fleet)" if (scope_cnt < tot_cnt and exp_cnt != g_exp) else ("Requires immediate renewal" if exp_cnt else "Zero overdue accounts")
    k3_sub = f"{crit_cnt} critical · {warn_cnt} warning"
    pct_local = (hlth_cnt / scope_cnt * 100) if scope_cnt else 0
    k4_sub = f"{pct_local:.1f}% compliance rate"

    k1_active = (cur_kpi == "All" and not is_scoped)
    k2_active = (cur_kpi == "Expired" or health_filter == "Expired")
    k3_active = (cur_kpi == "Urgent" or health_filter in ["Critical", "Warning"])
    k4_active = (cur_kpi == "Healthy" or health_filter == "Healthy")

    try:
        conn_snap = get_connection(DB_PATH)
        _snaps = get_metric_snapshots(conn_snap, limit=7)
        conn_snap.close()
        _exp_trend   = [int(s.get("expired", exp_cnt)) for s in _snaps] if _snaps else [exp_cnt]
        _cw_trend    = [int(s.get("critical", 0)) + int(s.get("warning", 0)) for s in _snaps] if _snaps else [crit_cnt + warn_cnt]
        _hlth_trend  = [int(s.get("healthy", hlth_cnt)) for s in _snaps] if _snaps else [hlth_cnt]
        _scope_trend = [int(s.get("tracked", scope_cnt)) for s in _snaps] if _snaps else [scope_cnt]
    except Exception:
        _exp_trend = _cw_trend = _hlth_trend = _scope_trend = []

    def _make_kpi_card_html(label: str, val: str | int, subtext: str, badge: str, state_kpi: str, is_active: bool, kpi_param: str, spark_vals=None, donut_val=None) -> str:
        if state_kpi == "firing":
            fill_cls = "stat-fill-red"
            val_col = "#f2495c"
            bg_col = "rgba(242,73,92,0.18)"
            fg_col = "#f2495c"
        elif state_kpi == "pending":
            fill_cls = "stat-fill-yellow"
            val_col = "#ff9830"
            bg_col = "rgba(255,152,48,0.18)"
            fg_col = "#ff9830"
        elif state_kpi == "ok" and "health" in label.lower():
            fill_cls = "stat-fill-green"
            val_col = "#73bf69"
            bg_col = "rgba(115,191,105,0.18)"
            fg_col = "#73bf69"
        else:
            fill_cls = "stat-fill-neutral"
            val_col = "#5794f2"
            bg_col = "rgba(255,255,255,0.08)"
            fg_col = "#f8fafc"

        active_style = "border:1.5px solid #38bdf8;box-shadow:0 0 10px rgba(56,189,248,0.28);background:rgba(56,189,248,0.08);" if is_active else "border:1px solid #2c3235;"
        vis_html = ""
        if donut_val is not None:
            vis_html = ui.compliance_donut(donut_val, val_col, size=24)
        elif spark_vals:
            vis_html = ui.grafana_sparkline(spark_vals, val_col, height=16, width=44)

        b_html = f'<span style="font-size:8px;font-weight:700;padding:1px 5px;border-radius:2px;background:{bg_col};color:{fg_col};">{"ACTIVE: " if is_active else ""}{badge}</span>'

        return f"""
        <a href="?op_kpi={kpi_param}{auth_suffix}" target="_self" style="text-decoration:none;display:block;">
          <div class="panel stat-panel grafana-card {fill_cls}" style="{active_style}border-radius:2px;padding:4px 8px;min-height:48px;box-sizing:border-box;display:flex;align-items:center;justify-content:space-between;cursor:pointer;">
            <div style="flex:1;min-width:0;">
              <div style="display:flex;align-items:center;justify-content:space-between;line-height:1;margin-bottom:2px;">
                <span style="font-size:9px;font-weight:700;color:var(--slate);text-transform:uppercase;letter-spacing:.02em;">{label}</span>
                {b_html}
              </div>
              <div style="display:flex;align-items:baseline;gap:6px;">
                <span style="font-size:17px;font-weight:800;font-family:var(--mono);color:{val_col};line-height:1;">{val}</span>
                <span style="font-size:8.5px;color:var(--mute);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{subtext}</span>
              </div>
            </div>
            <div style="flex:none;margin-left:4px;">
              {vis_html}
            </div>
          </div>
        </a>
        """

    c_k1 = _make_kpi_card_html("Portfolio Scope", f"{scope_cnt} / {tot_cnt}", k1_sub, "ALL FLEET" if k1_active else "SCOPED", "ok", k1_active, "All", spark_vals=_scope_trend)
    c_k2 = _make_kpi_card_html("Expired Items", exp_cnt, k2_sub, "FIRING" if exp_cnt else "CLEAR", "firing" if exp_cnt else "ok", k2_active, "Expired", spark_vals=_exp_trend)
    c_k3 = _make_kpi_card_html("Critical & Warning", crit_cnt + warn_cnt, k3_sub, "PENDING" if (crit_cnt + warn_cnt) else "STABLE", "pending" if (crit_cnt + warn_cnt) else "ok", k3_active, "Urgent", spark_vals=_cw_trend)
    c_k4 = _make_kpi_card_html("Healthy Entities", hlth_cnt, k4_sub, "COMPLIANT", "ok", k4_active, "Healthy", donut_val=pct_local)

    _render_html(f"""
    <div style="display:grid;grid-template-columns:repeat(4, 1fr);gap:6px;margin-bottom:4px;">
      {c_k1}
      {c_k2}
      {c_k3}
      {c_k4}
    </div>
    """)

    # Cross-Filter Visual Feedback Banner
    if k2_active:
        _render_html(f"""
        <div class="cross-filter-pulse" style="background:rgba(242,73,92,0.12);border:1px solid #f2495c;border-radius:2px;padding:2px 8px;margin-bottom:4px;display:flex;align-items:center;justify-content:space-between;">
          <div style="display:flex;align-items:center;gap:6px;">
            <span style="font-size:10px;font-weight:700;color:#f2495c;">⚡ ACTIVE FILTER:</span>
            <span style="font-size:10.5px;font-weight:700;color:var(--text);">Expired Items</span>
            <span style="font-size:9px;color:var(--slate);">({scope_cnt} overdue assets in focus)</span>
          </div>
          <a href="?op_kpi=All{auth_suffix}" target="_self" style="font-size:9px;color:#f2495c;text-decoration:none;font-weight:700;">✕ Clear Filter</a>
        </div>
        """)
    elif k3_active:
        _render_html(f"""
        <div class="cross-filter-pulse" style="background:rgba(255,152,48,0.12);border:1px solid #ff9830;border-radius:2px;padding:2px 8px;margin-bottom:4px;display:flex;align-items:center;justify-content:space-between;">
          <div style="display:flex;align-items:center;gap:6px;">
            <span style="font-size:10px;font-weight:700;color:#ff9830;">⚡ ACTIVE FILTER:</span>
            <span style="font-size:10.5px;font-weight:700;color:var(--text);">Critical & Warning</span>
            <span style="font-size:9px;color:var(--slate);">({scope_cnt} urgent assets in focus)</span>
          </div>
          <a href="?op_kpi=All{auth_suffix}" target="_self" style="font-size:9px;color:#ff9830;text-decoration:none;font-weight:700;">✕ Clear Filter</a>
        </div>
        """)
    elif cell_filter or cell_team or cell_env:
        c_st, c_cp = cell_filter if cell_filter else (None, None)
        active_tags = []
        if c_st: active_tags.append(f"State {c_st}")
        if cell_team: active_tags.append(f"Team {cell_team}")
        if cell_env: active_tags.append(f"Env {cell_env}")
        if c_cp: active_tags.append(f"{c_cp}")
        tag_str = " · ".join(active_tags) if active_tags else "Active Filter"
        _render_html(f"""
        <div class="cross-filter-pulse" style="background:rgba(255,120,10,0.12);border:1px solid #ff780a;border-radius:2px;padding:2px 8px;margin-bottom:4px;display:flex;align-items:center;justify-content:space-between;">
          <div style="display:flex;align-items:center;gap:6px;">
            <span style="font-size:10px;font-weight:700;color:#ff780a;">⚡ HEATMAP FILTER:</span>
            <span style="font-size:10.5px;font-weight:700;color:var(--text);">{tag_str}</span>
            <span style="font-size:9px;color:var(--slate);">({scope_cnt} entities synced)</span>
          </div>
          <a href="?op_cell=clear{auth_suffix}" target="_self" style="font-size:9px;color:#ff780a;text-decoration:none;font-weight:700;">✕ Clear Filter</a>
        </div>
        """)

    # Resolve active entity selection
    cur_scope_df = df[df["id"].isin(selected_entity_ids)] if selected_entity_ids else filtered
    if cur_scope_df.empty:
        selected_id = None
    else:
        cur_active_id = st.session_state.get("op_active_id")
        if cur_active_id not in cur_scope_df["id"].values:
            most_urgent = cur_scope_df.sort_values("days_left").iloc[0]
            cur_active_id = int(most_urgent["id"])
            st.session_state["op_active_id"] = cur_active_id
        selected_id = cur_active_id

    # ==========================================================================
    # 3. ENTERPRISE SUBTAB NAVIGATION (5 Enterprise Subtabs)
    # ==========================================================================
    st.markdown("""
    <style>
    .op-master-table-box {
        background: #181b1f;
        border: 1px solid #2c3235;
        border-radius: 3px;
        box-sizing: border-box;
        overflow-y: auto;
        overflow-x: auto;
        height: calc(100vh - 275px);
        max-height: calc(100vh - 275px);
        min-height: 440px;
        scrollbar-width: thin;
        scrollbar-color: #38bdf8 #181b1f;
    }
    .op-master-table-box::-webkit-scrollbar {
        width: 7px;
        height: 7px;
        display: block;
    }
    .op-master-table-box::-webkit-scrollbar-track {
        background: #181b1f;
        border-left: 1px solid #2c3235;
    }
    .op-master-table-box::-webkit-scrollbar-thumb {
        background: #38bdf8;
        border-radius: 3px;
        border: 1px solid #0284c7;
    }
    div.st-key-op_detail_box {
        background: #181b1f !important;
        border: 1px solid #2c3235 !important;
        border-radius: 3px !important;
        padding: 8px 10px !important;
        box-sizing: border-box !important;
        height: calc(100vh - 275px) !important;
        max-height: calc(100vh - 275px) !important;
        overflow-y: auto !important;
        overflow-x: hidden !important;
        scrollbar-width: thin !important;
        scrollbar-color: #38bdf8 #181b1f !important;
    }
    div.st-key-op_detail_box::-webkit-scrollbar {
        width: 6px;
        display: block;
    }
    div.st-key-op_detail_box::-webkit-scrollbar-track {
        background: #181b1f;
    }
    div.st-key-op_detail_box::-webkit-scrollbar-thumb {
        background: #334155;
        border-radius: 3px;
    }
    div.st-key-op_subtabs_container div[data-testid="stTabs"] {
        position: relative !important;
    }
    div.st-key-op_subtabs_container div[data-baseweb="tab-list"] {
        width: 100% !important;
        border-bottom: 1px solid #2c3235 !important;
        gap: 4px !important;
        height: 32px !important;
    }
    div.st-key-op_subtabs_container div[data-baseweb="tab-list"] button[role="tab"] {
        padding: 4px 10px !important;
        font-size: 10.5px !important;
        font-weight: 600 !important;
        white-space: nowrap !important;
    }
    </style>
    """, unsafe_allow_html=True)

    valid_subtabs = [
        "📋 Fleet Inventory & Master-Detail",
        "🗺️ Severity Matrix & Cross-Tab Heatmap",
        "⚡ Batch Operations Console",
        "🌳 Organizational Hierarchy Tree",
        "↩️ Rollback & Audit History Ledger",
    ]

    target_tab = st.session_state.pop("op_target_tab", None)
    tab_aliases = {
        "📋 Fleet Inventory & Master-Detail": "📋 Fleet Inventory & Master-Detail",
        "📋 Inventory": "📋 Fleet Inventory & Master-Detail",
        "inv": "📋 Fleet Inventory & Master-Detail",
        "master": "📋 Fleet Inventory & Master-Detail",
        "🗺️ Severity Matrix & Cross-Tab Heatmap": "🗺️ Severity Matrix & Cross-Tab Heatmap",
        "matrix": "🗺️ Severity Matrix & Cross-Tab Heatmap",
        "heat": "🗺️ Severity Matrix & Cross-Tab Heatmap",
        "⚡ Batch Operations Console": "⚡ Batch Operations Console",
        "⚡ Batch": "⚡ Batch Operations Console",
        "batch": "⚡ Batch Operations Console",
        "🌳 Organizational Hierarchy Tree": "🌳 Organizational Hierarchy Tree",
        "🌳 Hierarchy": "🌳 Organizational Hierarchy Tree",
        "tree": "🌳 Organizational Hierarchy Tree",
        "↩️ Rollback & Audit History Ledger": "↩️ Rollback & Audit History Ledger",
        "↩️ Rollback": "↩️ Rollback & Audit History Ledger",
        "rev": "↩️ Rollback & Audit History Ledger",
    }
    if target_tab in tab_aliases:
        target_tab = tab_aliases[target_tab]
    default_subtab = target_tab if target_tab in valid_subtabs else valid_subtabs[0]

    with st.container(key="op_subtabs_container"):
        subtab_inv, subtab_matrix, subtab_batch, subtab_tree, subtab_rev = st.tabs(
            valid_subtabs,
            default=default_subtab,
            key=f"op_subtab_bar_{reset_idx}"
        )

    # ==========================================================================
    # SUBTAB 1: 📋 FLEET INVENTORY & MASTER-DETAIL (58% / 42% SYNCHRONIZED SPLIT)
    # ==========================================================================
    with subtab_inv:
        c_master, c_detail = st.columns([5.8, 4.2], gap="small")

        with c_master:
            inv_table_rows = []
            for r in cur_scope_df.itertuples():
                is_act = (r.id == selected_id)
                row_bg = "background:rgba(56,189,248,0.14);border-left:3px solid #38bdf8;" if is_act else "border-bottom:1px solid #22252b;"
                target_icon = '<span style="color:#38bdf8;font-size:9.5px;margin-right:2px;">🎯</span>' if is_act else ''
                r_meta = ui.BAND_META.get(r.band, ui.BAND_META["Healthy"])
                sla_badge_str = ui.sla_badge(r.env_label)
                days_str = ui.fmt_days(r.days_left)
                badge_html = f'<span style="font-size:8.5px;font-weight:700;padding:1.5px 5px;border-radius:2px;background:{r_meta["tint"]};color:{r_meta["color"]};">{r_meta["symbol"]} {r.band}</span>'

                if is_act:
                    action_btn_html = '<span style="font-size:8.5px;font-weight:700;color:#38bdf8;background:rgba(56,189,248,0.2);padding:1.5px 5px;border-radius:2px;border:1px solid #38bdf8;white-space:nowrap;">● ACTIVE</span>'
                else:
                    action_btn_html = f'<a href="?op_act_id={r.id}{auth_suffix}" target="_self" style="text-decoration:none;display:inline-flex;align-items:center;padding:1.5px 6px;border-radius:2px;font-size:8.5px;font-weight:700;background:rgba(56,189,248,0.12);border:1px solid rgba(56,189,248,0.3);color:#38bdf8;white-space:nowrap;">INSPECT ↗</a>'

                inv_table_rows.append(f"""
                <tr style="{row_bg}">
                    <td style="padding:3px 6px;font-family:var(--mono);"><a href="?op_act_id={r.id}{auth_suffix}" target="_self" style="text-decoration:none;font-weight:700;color:{'#ffffff' if is_act else '#38bdf8'};">{target_icon}#{r.id}</a></td>
                    <td style="padding:3px 6px;font-weight:700;color:#9fa7b3;">{r.state}</td>
                    <td style="padding:3px 6px;color:#cbd5e1;">{r.team}</td>
                    <td style="padding:3px 6px;color:#d8d9da;">{ui.COMPONENT_CODE.get(r.component, r.component)}</td>
                    <td style="padding:3px 6px;font-family:var(--mono);font-weight:600;color:#f8fafc;max-width:140px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="{r.schema_name}">{r.schema_name}</td>
                    <td style="padding:3px 6px;"><span class="env-tag">{r.env_label}</span></td>
                    <td style="padding:3px 6px;">{sla_badge_str}</td>
                    <td style="padding:3px 6px;font-family:var(--mono);">{r.exp_date}</td>
                    <td style="padding:3px 6px;font-family:var(--mono);color:{r_meta['color']};font-weight:700;">{days_str}</td>
                    <td style="padding:3px 6px;">{badge_html}</td>
                    <td style="padding:3px 6px;text-align:right;">{action_btn_html}</td>
                </tr>
                """)

            _render_html(f"""
            <div class="op-master-table-box">
                <table style="width:100%;min-width:760px;border-collapse:collapse;font-size:10px;color:#d8d9da;">
                    <thead style="position:sticky;top:0;background:#141619;border-bottom:1px solid #2c3235;z-index:2;">
                        <tr style="color:#6e7681;text-transform:uppercase;font-size:8.5px;font-weight:700;letter-spacing:0.03em;">
                            <th style="padding:4px 6px;text-align:left;">ID</th>
                            <th style="padding:4px 6px;text-align:left;">State</th>
                            <th style="padding:4px 6px;text-align:left;">Team</th>
                            <th style="padding:4px 6px;text-align:left;">Comp</th>
                            <th style="padding:4px 6px;text-align:left;">Schema / Asset</th>
                            <th style="padding:4px 6px;text-align:left;">Env</th>
                            <th style="padding:4px 6px;text-align:left;">SLA</th>
                            <th style="padding:4px 6px;text-align:left;">Expiry</th>
                            <th style="padding:4px 6px;text-align:left;">Time Left</th>
                            <th style="padding:4px 6px;text-align:left;">Status</th>
                            <th style="padding:4px 6px;text-align:right;">Action</th>
                        </tr>
                    </thead>
                    <tbody>
                        {' '.join(inv_table_rows) if inv_table_rows else '<tr><td colspan="11" style="text-align:center;padding:30px;color:#64748b;">No assets match current filters.</td></tr>'}
                    </tbody>
                </table>
            </div>
            """)

        with c_detail:
            with st.container(key="op_detail_box"):
                if selected_id is None or cur_scope_df.empty:
                    st.markdown(ui.empty("Select a record", "Choose an asset from the inventory list to inspect."), unsafe_allow_html=True)
                else:
                    rec = df[df["id"] == selected_id].iloc[0]
                    meta = ui.BAND_META.get(rec["band"], ui.BAND_META["Healthy"])
                    team_meta = ui.TEAM_META.get(rec["team"], ui.TEAM_META["Core"])
                    cp_icon = ui.COMPONENT_ICONS.get(rec["component"], "📦")
                    cur_dt = rec["exp_dt"].date()
                    conf = st.session_state.get("confirm_action")

                    # Contextual Inspector Header
                    st.markdown(f"""
                    <div style="display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid #22252b;padding-bottom:5px;margin-bottom:6px;">
                      <div style="display:flex;align-items:center;gap:6px;min-width:0;">
                        <span style="font-size:16px;">{cp_icon}</span>
                        <div style="min-width:0;">
                          <div style="font-size:12px;font-weight:800;color:#f8fafc;font-family:var(--mono);line-height:1.2;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">
                            #{rec['id']} {rec['schema_name']}
                          </div>
                          <div style="font-size:9px;color:#94a3b8;margin-top:1px;">
                            {rec['state']} · {rec['team']} · <span class="env-tag" style="font-size:7.5px;">{rec['env_label']}</span> {ui.sla_badge(rec['env_label'])}
                          </div>
                        </div>
                      </div>
                      <span style="font-size:8.5px;font-weight:700;padding:2px 6px;border-radius:2px;background:{meta['tint']};color:{meta['color']};white-space:nowrap;">
                        {meta['symbol']} {rec['band']}
                      </span>
                    </div>
                    """, unsafe_allow_html=True)

                # Action Controls Row
                if conf and conf.get("id") == rec["id"]:
                    st.markdown(f"<div style='font-size:9.5px;color:var(--warning);font-weight:700;margin-bottom:4px;'>⚠️ Confirm {conf['new_dt']} (+{conf['days']}d)?</div>", unsafe_allow_html=True)
                    cf_y, cf_n = st.columns(2)
                    with cf_y:
                        if st.button("✓ Confirm", key=f"insp_cf_yes_{rec['id']}", type="primary", use_container_width=True):
                            _apply_edits([(conf["id"], conf["new_dt"])])
                            del st.session_state["confirm_action"]
                            st.success(f"Updated {conf['schema']} to {conf['new_dt']}")
                            st.rerun()
                    with cf_n:
                        if st.button("✕ Cancel", key=f"insp_cf_no_{rec['id']}", use_container_width=True):
                            del st.session_state["confirm_action"]
                            st.rerun()
                else:
                    act_cols = st.columns([1.0, 1.0, 1.1, 1.1] if rec["edited"] else [1.0, 1.0, 1.2], gap="small")
                    if act_cols[0].button("+90d", key=f"insp_top_p90_{rec['id']}", use_container_width=True, help="Extend expiry by 90 days"):
                        st.session_state["confirm_action"] = {"id": rec["id"], "days": 90, "new_dt": cur_dt + pd.Timedelta(days=90), "schema": rec["schema_name"]}
                        st.rerun()
                    if act_cols[1].button("+1yr", key=f"insp_top_p365_{rec['id']}", use_container_width=True, help="Extend expiry by 1 year"):
                        st.session_state["confirm_action"] = {"id": rec["id"], "days": 365, "new_dt": cur_dt + pd.Timedelta(days=365), "schema": rec["schema_name"]}
                        st.rerun()
                    with act_cols[2]:
                        if hasattr(st, "popover"):
                            with st.popover("📅 Date", help="Pick custom expiry date", use_container_width=True):
                                c_date = st.date_input("New Expiry Date", value=cur_dt, key=f"insp_pop_dt_{rec['id']}")
                                if st.button("Commit Expiry", type="primary", key=f"insp_pop_btn_{rec['id']}", use_container_width=True):
                                    _apply_edits([(rec["id"], c_date)])
                                    st.success(f"Updated to {c_date}")
                                    st.rerun()
                    if rec["edited"] and len(act_cols) > 3:
                        with act_cols[3]:
                            if act_cols[3].button("↩ Rev", key=f"insp_top_rev_{rec['id']}", type="secondary", use_container_width=True, help="Revert to workbook source date"):
                                conn = get_connection(DB_PATH)
                                try:
                                    revert_component_exp_date(conn, int(rec["id"]))
                                finally:
                                    conn.close()
                                st.session_state["_bust"] = st.session_state.get("_bust", 0) + 1
                                st.cache_data.clear()
                                st.success("Reverted to workbook date.")
                                st.rerun()

                exp_detail = f"(Expired {rec['exp_dt'].strftime('%b %Y')})" if rec['days_left'] < 0 else f"(Expires {rec['exp_date']})"
                _life_gauge = ui.life_gauge(int(rec["days_left"]))
                _team_chip = ui.alert_chip(rec["band"])

                st.markdown(f"""
                <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;margin:8px 0;">
                  <div style="background:#141619;border:1px solid #22252b;border-radius:2px;padding:5px 7px;">
                    <div style="font-size:8.5px;color:#64748b;text-transform:uppercase;font-weight:700;">Team Owner</div>
                    <div style="font-size:10.5px;color:{team_meta['color']};font-weight:700;margin-top:2px;">{rec['team']}</div>
                    <div style="font-size:8.5px;color:#94a3b8;">Lead: {team_meta['lead']}</div>
                  </div>
                  <div style="background:#141619;border:1px solid #22252b;border-radius:2px;padding:5px 7px;">
                    <div style="font-size:8.5px;color:#64748b;text-transform:uppercase;font-weight:700;">Component</div>
                    <div style="font-size:10.5px;color:#f8fafc;font-weight:700;margin-top:2px;">{rec['component']}</div>
                    <div style="font-size:8.5px;color:#94a3b8;">Code: {ui.COMPONENT_CODE.get(rec['component'], rec['component'])}</div>
                  </div>
                  <div style="background:#141619;border:1px solid #22252b;border-radius:2px;padding:5px 7px;">
                    <div style="font-size:8.5px;color:#64748b;text-transform:uppercase;font-weight:700;">Current Expiry</div>
                    <div style="font-size:11px;color:{meta['color']};font-weight:700;font-family:var(--mono);margin-top:2px;">{rec['exp_date']}</div>
                    <div style="font-size:8.5px;color:#94a3b8;">Source: {rec['source_exp_date']}</div>
                  </div>
                  <div style="background:#141619;border:1px solid #22252b;border-radius:2px;padding:5px 7px;">
                    <div style="font-size:8.5px;color:#64748b;text-transform:uppercase;font-weight:700;">Life Remaining</div>
                    <div style="font-size:11px;color:{meta['color']};font-weight:800;font-family:var(--mono);margin-top:2px;">{ui.fmt_days(rec['days_left'])}</div>
                    <div style="font-size:8.5px;color:#94a3b8;">{rec['quarter']}</div>
                  </div>
                </div>
                <div style="margin:4px 0 8px;">{_life_gauge}</div>
                """, unsafe_allow_html=True)

                # Technical Diagnostics
                payload = {
                    "id": int(rec["id"]),
                    "state": rec["state"],
                    "team": rec["team"],
                    "component": rec["component"],
                    "environment": rec["env_label"],
                    "schema_name": rec["schema_name"],
                    "exp_date": rec["exp_date"],
                    "source_exp_date": rec["source_exp_date"],
                    "days_left": int(rec["days_left"]),
                    "band": rec["band"],
                    "edited_at": str(rec["edited_at"]),
                }
                with st.expander("Technical Diagnostics & Database Query", expanded=False):
                    st.code(f"SELECT * FROM component_records WHERE id = {int(rec['id'])};", language="sql")
                    st.code(json.dumps(payload, indent=2), language="json")

    # ==========================================================================
    # SUBTAB 2: 🗺️ SEVERITY MATRIX & CROSS-TAB HEATMAP (DEDICATED FULL-WIDTH MATRIX)
    # ==========================================================================
    with subtab_matrix:
        user_dim = st.session_state.get("op_heatmap_dim", "auto")
        if user_dim == "auto":
            if state_filter != "All States" and team_filter == "All Teams":
                resolved_dim = "team"
            elif state_filter != "All States" and team_filter != "All Teams":
                resolved_dim = "env"
            else:
                resolved_dim = "state"
        else:
            resolved_dim = user_dim

        if selected_entity_ids:
            mat_scope_lbl = f"{len(selected_entity_ids)} Selected Entities"
            base_scope_df = df[df["id"].isin(selected_entity_ids)].copy()
        else:
            mat_scope_lbl = "Filtered Scope" if is_scoped else "Consolidated Fleet"
            base_scope_df = filtered.copy()

        def _dim_pill(d_id, d_label):
            is_cur = (user_dim == d_id) or (user_dim == "auto" and d_id == "auto")
            style = "background:rgba(56,189,248,0.25);border:1px solid #38bdf8;color:#38bdf8;font-weight:700;" if is_cur else "background:#141619;border:1px solid #2c3235;color:#94a3b8;font-weight:600;"
            return f'<a href="?op_dim={d_id}&op_subtab=matrix{auth_suffix}" target="_self" style="text-decoration:none;font-size:8px;padding:2px 6px;border-radius:2px;line-height:1;display:inline-block;{style}">{d_label}</a>'

        dim_pills = f'''<div style="display:inline-flex;align-items:center;gap:4px;"><span style="font-size:8px;color:#64748b;text-transform:uppercase;letter-spacing:0.03em;">AXIS:</span>{_dim_pill("auto", "Auto")}{_dim_pill("state", "State")}{_dim_pill("team", "Team")}{_dim_pill("env", "Env")}</div>'''
        clear_hm_link = f'<a href="?op_cell=clear&op_subtab=matrix{auth_suffix}" target="_self" style="font-size:8.5px;color:#f59e0b;font-weight:700;text-decoration:none;border:1px solid #f59e0b;padding:1.5px 6px;border-radius:2px;line-height:1;display:inline-block;">✕ Clear Cell Filter</a>' if (cell_filter or cell_team or cell_env) else ''

        mat_comps = COMPONENT_ORDER
        hm_tr_list = []

        if resolved_dim == "state":
            heatmap_title = f"Severity Heatmap — State × Component ({mat_scope_lbl})"
            col0_header = "STATE"

            for st_val in STATES:
                is_st_active = (cell_filter == (st_val, None)) or (state_filter == st_val and cell_filter is None and comp_filter == "All Components")
                st_border = "1.5px solid #38bdf8" if is_st_active else "1px solid #2c3235"
                st_bg = "rgba(56,189,248,0.2)" if is_st_active else "#212429"
                st_color = "#38bdf8" if is_st_active else "#f8fafc"

                if state_filter == st_val or state_filter == "All States":
                    st_sub = base_scope_df[base_scope_df["state"] == st_val]
                    opacity_style = "opacity:1;"
                else:
                    bg_filter = df[df["state"] == st_val]
                    if team_filter != "All Teams": bg_filter = bg_filter[bg_filter["team"] == team_filter]
                    if comp_filter != "All Components": bg_filter = bg_filter[bg_filter["component"] == comp_filter]
                    if health_filter != "All Health": bg_filter = bg_filter[bg_filter["band"] == health_filter]
                    st_sub = bg_filter
                    opacity_style = "opacity:0.65;"

                td_cells = []
                for c_val in mat_comps:
                    c_code = ui.COMPONENT_CODE.get(c_val, c_val)
                    sub = st_sub[st_sub["component"] == c_val]
                    c_cnt = len(sub)
                    is_cell_active = (cell_filter == (st_val, c_val))

                    if c_cnt == 0:
                        td_cells.append(
                            f'<td style="padding:2px;">'
                            f'<div style="background:#141619;border:1px dashed #22262a;border-radius:2px;height:38px;display:flex;align-items:center;justify-content:center;color:#475569;font-size:10px;font-family:var(--mono);">'
                            f'<span style="opacity:0.35;">—</span></div></td>'
                        )
                    else:
                        c_exp = int((sub["days_left"] < 0).sum())
                        c_crit = int((sub["days_left"].between(0, ui.CRITICAL_DAYS)).sum())
                        c_warn = int((sub["days_left"].between(ui.CRITICAL_DAYS + 1, ui.WARNING_DAYS)).sum())
                        c_hlth = int((sub["days_left"] > ui.WARNING_DAYS).sum())
                        min_days = int(sub["days_left"].min())

                        worst_b = ui.worst_band(sub["band"].tolist())
                        c_color = ui.BAND_META[worst_b]["color"]

                        if c_exp > 0:
                            c_badge = f"▲ {c_exp} Exp"
                            c_fill = "rgba(242,73,92,0.22)"
                        elif c_crit > 0:
                            c_badge = f"▲ {c_crit} Crit"
                            c_fill = "rgba(242,73,92,0.18)"
                        elif c_warn > 0:
                            c_badge = f"{c_warn} Warn"
                            c_fill = "rgba(255,152,48,0.18)"
                        else:
                            c_badge = f"✓ {c_hlth} OK"
                            c_fill = "rgba(115,191,105,0.18)"

                        p_hlth = (c_hlth / c_cnt) * 100.0
                        p_risk = 100.0 - p_hlth
                        risk_bar = f'<div style="width:{p_risk:.0f}%;background:{c_color};"></div>' if p_risk > 0 else ''
                        hlth_bar = f'<div style="width:{p_hlth:.0f}%;background:#10b981;"></div>' if p_hlth > 0 else ''

                        c_border = "1.5px solid #38bdf8;box-shadow:0 0 8px rgba(56,189,248,0.3);background:rgba(56,189,248,0.12);" if is_cell_active else f"1px solid #2c3235;border-top:2px solid {c_color};background:{c_fill};"
                        cd_str = ui.fmt_heatmap_time(min_days)

                        td_cells.append(
                            f'<td style="padding:2px;">'
                            f'<a href="?op_cell={st_val}:{c_val}&op_subtab=matrix{auth_suffix}" target="_self" style="text-decoration:none;display:block;">'
                            f'<div style="{c_border};border-radius:2px;padding:3px 5px;height:38px;box-sizing:border-box;display:flex;flex-direction:column;justify-content:space-between;cursor:pointer;" title="Filter to {st_val} × {c_code} ({c_cnt} assets · soonest {cd_str})">'
                            f'<div style="display:flex;align-items:center;justify-content:space-between;line-height:1;">'
                            f'<span style="font-family:var(--mono);font-size:11px;font-weight:800;color:#f8fafc;">{c_cnt}</span>'
                            f'<span style="font-size:7.5px;font-weight:700;color:{c_color};">{c_badge}</span>'
                            f'</div>'
                            f'<div style="display:flex;height:2px;border-radius:1px;overflow:hidden;background:rgba(255,255,255,0.06);margin:1px 0;">{risk_bar}{hlth_bar}</div>'
                            f'<div style="font-size:7.5px;color:{c_color};font-family:var(--mono);line-height:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">'
                            f'{cd_str}</div>'
                            f'</div></a></td>'
                        )

                _st_worst = ui.worst_band(st_sub["band"].tolist()) if not st_sub.empty else "Healthy"
                _st_color = ui.BAND_META[_st_worst]["color"]
                tot_td = f'<td style="padding:2px;text-align:center;"><div style="font-family:var(--mono);font-size:12px;font-weight:800;color:{_st_color};line-height:38px;">{len(st_sub)}</div></td>'

                hm_tr_list.append(
                    f'<tr style="{opacity_style}">'
                    f'<td style="padding:2px;">'
                    f'<a href="?op_cell={st_val}:&op_subtab=matrix{auth_suffix}" target="_self" style="text-decoration:none;display:block;">'
                    f'<div style="background:{st_bg};border:{st_border};color:{st_color};border-radius:2px;padding:0 4px;height:38px;display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:800;cursor:pointer;" title="Filter to State {st_val}">'
                    f'📍 {st_val}</div></a></td>'
                    f'{"".join(td_cells)}'
                    f'{tot_td}'
                    f'</tr>'
                )

        elif resolved_dim == "team":
            st_scope_name = f"State: {state_filter}" if state_filter != "All States" else "Fleet"
            heatmap_title = f"Severity Heatmap — Team × Component ({st_scope_name})"
            col0_header = "TEAM"

            for tm_val in ui.TEAMS:
                tm_sub = base_scope_df[base_scope_df["team"] == tm_val]
                is_tm_active = (team_filter == tm_val) or (cell_team == tm_val)
                tm_border = "1.5px solid #38bdf8" if is_tm_active else "1px solid #2c3235"
                tm_bg = "rgba(56,189,248,0.2)" if is_tm_active else "#212429"
                tm_color = "#38bdf8" if is_tm_active else "#f8fafc"

                td_cells = []
                for c_val in mat_comps:
                    c_code = ui.COMPONENT_CODE.get(c_val, c_val)
                    sub = tm_sub[tm_sub["component"] == c_val]
                    c_cnt = len(sub)
                    is_cell_active = (cell_filter == (state_filter if state_filter != "All States" else None, c_val)) and (cell_team == tm_val)

                    if c_cnt == 0:
                        td_cells.append(
                            f'<td style="padding:2px;">'
                            f'<div style="background:rgba(255,255,255,0.015);border:1px solid #22262a;border-radius:2px;height:32px;display:flex;align-items:center;justify-content:center;color:#475569;font-size:9.5px;font-family:var(--mono);">'
                            f'—</div></td>'
                        )
                    else:
                        c_exp = int((sub["days_left"] < 0).sum())
                        c_crit = int((sub["days_left"].between(0, ui.CRITICAL_DAYS)).sum())
                        c_warn = int((sub["days_left"].between(ui.CRITICAL_DAYS + 1, ui.WARNING_DAYS)).sum())
                        c_hlth = int((sub["days_left"] > ui.WARNING_DAYS).sum())
                        min_days = int(sub["days_left"].min())

                        worst_b = ui.worst_band(sub["band"].tolist())
                        c_color = ui.BAND_META[worst_b]["color"]

                        if c_exp > 0:
                            c_badge = f"▲ {c_exp} Exp"
                            c_fill = "rgba(242,73,92,0.22)"
                        elif c_crit > 0:
                            c_badge = f"▲ {c_crit} Crit"
                            c_fill = "rgba(242,73,92,0.18)"
                        elif c_warn > 0:
                            c_badge = f"{c_warn} Warn"
                            c_fill = "rgba(255,152,48,0.18)"
                        else:
                            c_badge = f"✓ {c_hlth} OK"
                            c_fill = "rgba(115,191,105,0.18)"

                        p_hlth = (c_hlth / c_cnt) * 100.0
                        p_risk = 100.0 - p_hlth
                        risk_bar = f'<div style="width:{p_risk:.0f}%;background:{c_color};"></div>' if p_risk > 0 else ''
                        hlth_bar = f'<div style="width:{p_hlth:.0f}%;background:#10b981;"></div>' if p_hlth > 0 else ''

                        c_border = "1.5px solid #38bdf8;box-shadow:0 0 8px rgba(56,189,248,0.3);background:rgba(56,189,248,0.12);" if is_cell_active else f"1px solid #2c3235;border-top:2px solid {c_color};background:{c_fill};"
                        cd_str = ui.fmt_heatmap_time(min_days)
                        cell_st_val = state_filter if state_filter != "All States" else ""

                        td_cells.append(
                            f'<td style="padding:2px;">'
                            f'<a href="?op_cell={cell_st_val}:{c_val}:{tm_val}&op_subtab=matrix{auth_suffix}" target="_self" style="text-decoration:none;display:block;">'
                            f'<div style="{c_border};border-radius:2px;padding:2px 4px;height:32px;box-sizing:border-box;display:flex;align-items:center;justify-content:space-between;cursor:pointer;position:relative;overflow:hidden;" title="Filter to {tm_val} × {c_code} ({c_cnt} assets · soonest {cd_str})">'
                            f'<span style="font-family:var(--mono);font-size:10px;font-weight:800;color:#f8fafc;">{c_cnt}</span>'
                            f'<span style="font-size:7.5px;font-weight:700;color:{c_color};white-space:nowrap;">{c_badge}</span>'
                            f'<div style="position:absolute;bottom:0;left:0;right:0;height:2px;display:flex;background:rgba(255,255,255,0.06);">{risk_bar}{hlth_bar}</div>'
                            f'</div></a></td>'
                        )

                _tm_worst = ui.worst_band(tm_sub["band"].tolist()) if not tm_sub.empty else "Healthy"
                _tm_color = ui.BAND_META[_tm_worst]["color"]
                tot_td = f'<td style="padding:2px;text-align:center;"><div style="font-family:var(--mono);font-size:10.5px;font-weight:800;color:{_tm_color};line-height:32px;">{len(tm_sub)}</div></td>'
                cell_st_val = state_filter if state_filter != "All States" else ""

                hm_tr_list.append(
                    f'<tr>'
                    f'<td style="padding:2px;">'
                    f'<a href="?op_cell={cell_st_val}::{tm_val}&op_subtab=matrix{auth_suffix}" target="_self" style="text-decoration:none;display:block;">'
                    f'<div style="background:{tm_bg};border:{tm_border};color:{tm_color};border-radius:2px;padding:0 5px;height:32px;display:flex;align-items:center;justify-content:flex-start;font-size:9.5px;font-weight:800;cursor:pointer;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;" title="Filter to Team {tm_val}">'
                    f'👥 {tm_val}</div></a></td>'
                    f'{"".join(td_cells)}'
                    f'{tot_td}'
                    f'</tr>'
                )

        else:  # resolved_dim == "env"
            scope_name = f"{state_filter} · {team_filter}" if state_filter != "All States" or team_filter != "All Teams" else "Fleet"
            heatmap_title = f"Severity Heatmap — Env × Component ({scope_name})"
            col0_header = "ENV"

            for env_val in ["DEV", "SIT", "UAT", "PROD", "DR"]:
                env_sub = base_scope_df[base_scope_df["env_label"] == env_val]
                is_env_active = (cell_env == env_val)
                env_border = "1.5px solid #38bdf8" if is_env_active else "1px solid #2c3235"
                env_bg = "rgba(56,189,248,0.2)" if is_env_active else "#212429"
                env_color = "#38bdf8" if is_env_active else "#f8fafc"

                td_cells = []
                for c_val in mat_comps:
                    c_code = ui.COMPONENT_CODE.get(c_val, c_val)
                    sub = env_sub[env_sub["component"] == c_val]
                    c_cnt = len(sub)
                    is_cell_active = (cell_filter == (state_filter if state_filter != "All States" else None, c_val)) and (cell_env == env_val)

                    if c_cnt == 0:
                        td_cells.append(
                            f'<td style="padding:2px;">'
                            f'<div style="background:rgba(255,255,255,0.015);border:1px solid #22262a;border-radius:2px;height:32px;display:flex;align-items:center;justify-content:center;color:#475569;font-size:9.5px;font-family:var(--mono);">'
                            f'—</div></td>'
                        )
                    else:
                        c_exp = int((sub["days_left"] < 0).sum())
                        c_crit = int((sub["days_left"].between(0, ui.CRITICAL_DAYS)).sum())
                        c_warn = int((sub["days_left"].between(ui.CRITICAL_DAYS + 1, ui.WARNING_DAYS)).sum())
                        c_hlth = int((sub["days_left"] > ui.WARNING_DAYS).sum())
                        min_days = int(sub["days_left"].min())

                        worst_b = ui.worst_band(sub["band"].tolist())
                        c_color = ui.BAND_META[worst_b]["color"]

                        if c_exp > 0:
                            c_badge = f"▲ {c_exp} Exp"
                            c_fill = "rgba(242,73,92,0.22)"
                        elif c_crit > 0:
                            c_badge = f"▲ {c_crit} Crit"
                            c_fill = "rgba(242,73,92,0.18)"
                        elif c_warn > 0:
                            c_badge = f"{c_warn} Warn"
                            c_fill = "rgba(255,152,48,0.18)"
                        else:
                            c_badge = f"✓ {c_hlth} OK"
                            c_fill = "rgba(115,191,105,0.18)"

                        p_hlth = (c_hlth / c_cnt) * 100.0
                        p_risk = 100.0 - p_hlth
                        risk_bar = f'<div style="width:{p_risk:.0f}%;background:{c_color};"></div>' if p_risk > 0 else ''
                        hlth_bar = f'<div style="width:{p_hlth:.0f}%;background:#10b981;"></div>' if p_hlth > 0 else ''

                        c_border = "1.5px solid #38bdf8;box-shadow:0 0 8px rgba(56,189,248,0.3);background:rgba(56,189,248,0.12);" if is_cell_active else f"1px solid #2c3235;border-top:2px solid {c_color};background:{c_fill};"
                        cd_str = ui.fmt_heatmap_time(min_days)
                        cell_st_val = state_filter if state_filter != "All States" else ""

                        td_cells.append(
                            f'<td style="padding:2px;">'
                            f'<a href="?op_cell={cell_st_val}:{c_val}::{env_val}&op_subtab=matrix{auth_suffix}" target="_self" style="text-decoration:none;display:block;">'
                            f'<div style="{c_border};border-radius:2px;padding:2px 4px;height:32px;box-sizing:border-box;display:flex;align-items:center;justify-content:space-between;cursor:pointer;position:relative;overflow:hidden;" title="Filter to {env_val} × {c_code} ({c_cnt} assets · soonest {cd_str})">'
                            f'<span style="font-family:var(--mono);font-size:10px;font-weight:800;color:#f8fafc;">{c_cnt}</span>'
                            f'<span style="font-size:7.5px;font-weight:700;color:{c_color};white-space:nowrap;">{c_badge}</span>'
                            f'<div style="position:absolute;bottom:0;left:0;right:0;height:2px;display:flex;background:rgba(255,255,255,0.06);">{risk_bar}{hlth_bar}</div>'
                            f'</div></a></td>'
                        )

                _env_worst = ui.worst_band(env_sub["band"].tolist()) if not env_sub.empty else "Healthy"
                _env_color = ui.BAND_META[_env_worst]["color"]
                tot_td = f'<td style="padding:2px;text-align:center;"><div style="font-family:var(--mono);font-size:10.5px;font-weight:800;color:{_env_color};line-height:32px;">{len(env_sub)}</div></td>'
                cell_st_val = state_filter if state_filter != "All States" else ""

                hm_tr_list.append(
                    f'<tr>'
                    f'<td style="padding:2px;">'
                    f'<a href="?op_cell={cell_st_val}:::{env_val}&op_subtab=matrix{auth_suffix}" target="_self" style="text-decoration:none;display:block;">'
                    f'<div style="background:{env_bg};border:{env_border};color:{env_color};border-radius:2px;padding:0 5px;height:32px;display:flex;align-items:center;justify-content:center;font-size:9.5px;font-weight:800;cursor:pointer;" title="Filter to Env {env_val}">'
                    f'🏷️ {env_val}</div></a></td>'
                    f'{"".join(td_cells)}'
                    f'{tot_td}'
                    f'</tr>'
                )

        mat_col, sla_col = st.columns([5.8, 4.2], gap="small")

        with mat_col:
            _render_html(f"""
            <div style="background:#181b1f;border:1px solid #2c3235;border-radius:3px;padding:8px 10px;box-sizing:border-box;height:calc(100vh - 275px);max-height:calc(100vh - 275px);min-height:440px;overflow-y:auto;display:flex;flex-direction:column;justify-content:space-between;">
              <div>
                <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px;">
                  <div style="font-size:11px;font-weight:800;color:#f59e0b;text-transform:uppercase;letter-spacing:0.04em;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">
                    {heatmap_title}
                  </div>
                  <div style="display:flex;align-items:center;gap:6px;">
                    {dim_pills}
                    {clear_hm_link}
                  </div>
                </div>
                <table style="width:100%;border-collapse:separate;border-spacing:3px;margin:0;padding:0;">
                  <thead>
                    <tr style="font-size:8.5px;color:#94a3b8;text-transform:uppercase;font-weight:700;line-height:1;">
                      <th style="width:16%;text-align:center;padding:2px 0;">{col0_header}</th>
                      <th style="width:18%;text-align:center;padding:2px 0;">🔑 CRYPTO</th>
                      <th style="width:18%;text-align:center;padding:2px 0;">🔒 DBPWD</th>
                      <th style="width:18%;text-align:center;padding:2px 0;">📦 SWVER</th>
                      <th style="width:18%;text-align:center;padding:2px 0;">🛠️ PATCH</th>
                      <th style="width:12%;text-align:center;padding:2px 0;">TOTAL</th>
                    </tr>
                  </thead>
                  <tbody>
                    {' '.join(hm_tr_list)}
                  </tbody>
                </table>
              </div>
              <div style="font-size:8.5px;color:#64748b;margin-top:8px;padding-top:6px;border-top:1px solid #22252b;display:flex;align-items:center;justify-content:space-between;">
                <span>💡 Click any cell to cross-filter inventory in Tab 1</span>
                <span>Active Scope: {len(base_scope_df)} Assets</span>
              </div>
            </div>
            """)

        with sla_col:
            dist_source = df[df["id"].isin(selected_entity_ids)] if selected_entity_ids else filtered
            dist_rows = []
            for c_val in COMPONENT_ORDER:
                c_sub = dist_source[dist_source["component"] == c_val]
                c_cnt = len(c_sub)
                c_code = ui.COMPONENT_CODE.get(c_val, c_val)
                if c_cnt == 0:
                    dist_rows.append(
                        f'<div class="dist-row" style="margin-bottom:6px;display:flex;align-items:center;gap:6px;">'
                        f'<div class="dist-name" style="font-size:9.5px;width:52px;font-weight:700;">{c_code}</div>'
                        f'<div class="dist-track" style="height:7px;flex:1;background:#212429;border-radius:2px;"></div>'
                        f'<div class="dist-num" style="font-size:8.5px;color:#94a3b8;width:55px;text-align:right;">0 (0%)</div>'
                        f'</div>'
                    )
                else:
                    c_exp = int((c_sub["days_left"] < 0).sum())
                    c_crit = int((c_sub["days_left"].between(0, ui.CRITICAL_DAYS)).sum())
                    c_warn = int((c_sub["days_left"].between(ui.CRITICAL_DAYS + 1, ui.WARNING_DAYS)).sum())
                    c_hlth = int((c_sub["days_left"] > ui.WARNING_DAYS).sum())

                    p_exp = (c_exp / c_cnt) * 100
                    p_crit = (c_crit / c_cnt) * 100
                    p_warn = (c_warn / c_cnt) * 100
                    p_hlth = (c_hlth / c_cnt) * 100

                    track_parts = []
                    if p_exp > 0: track_parts.append(f'<div style="width:{p_exp:.1f}%;background:var(--expired);" title="{c_exp} expired"></div>')
                    if p_crit > 0: track_parts.append(f'<div style="width:{p_crit:.1f}%;background:var(--critical);" title="{c_crit} critical"></div>')
                    if p_warn > 0: track_parts.append(f'<div style="width:{p_warn:.1f}%;background:var(--warning);" title="{c_warn} warning"></div>')
                    if p_hlth > 0: track_parts.append(f'<div style="width:{p_hlth:.1f}%;background:var(--healthy);" title="{c_hlth} healthy"></div>')
                    track_html = "".join(track_parts) if track_parts else '<div style="width:100%;background:#212429;"></div>'

                    if c_exp > 0:
                        num_html = f'<span style="color:var(--expired);font-weight:700;">{c_exp} exp</span> <span style="color:var(--mute);font-weight:400;">/ {c_cnt}</span>'
                    elif c_crit > 0:
                        num_html = f'<span style="color:var(--critical);font-weight:700;">{c_crit} crit</span> <span style="color:var(--mute);font-weight:400;">/ {c_cnt}</span>'
                    elif c_warn > 0:
                        num_html = f'<span style="color:var(--warning);font-weight:700;">{c_warn} warn</span> <span style="color:var(--mute);font-weight:400;">/ {c_cnt}</span>'
                    else:
                        num_html = f'<span style="color:var(--healthy);font-weight:700;">{c_cnt}</span> <span style="color:var(--mute);font-weight:400;">(100%)</span>'

                    dist_rows.append(
                        f'<div class="dist-row" style="margin-bottom:6px;display:flex;align-items:center;gap:6px;">'
                        f'<div class="dist-name" style="font-size:9.5px;width:52px;font-weight:700;">{c_code}</div>'
                        f'<div class="dist-track" style="height:7px;flex:1;border-radius:2px;display:flex;overflow:hidden;background:#212429;">{track_html}</div>'
                        f'<div class="dist-num" style="font-size:8.5px;width:68px;text-align:right;">{num_html}</div>'
                        f'</div>'
                    )

            _render_html(f"""
            <div style="background:#181b1f;border:1px solid #2c3235;border-radius:3px;padding:8px 10px;box-sizing:border-box;height:calc(100vh - 275px);max-height:calc(100vh - 275px);min-height:440px;display:flex;flex-direction:column;justify-content:space-between;overflow-y:auto;">
              <div>
                <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px;">
                  <div style="font-size:11px;font-weight:800;color:#10b981;text-transform:uppercase;letter-spacing:0.04em;">
                    Component Severity &amp; Fleet SLA ({mat_scope_lbl})
                  </div>
                  <span style="font-size:8.5px;color:#94a3b8;font-weight:600;">100% Target</span>
                </div>

                <div class="dist-body" style="padding:4px 0;display:flex;flex-direction:column;gap:3px;">
                  {' '.join(dist_rows)}
                </div>
              </div>

              <div>
                <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;font-size:9.5px;border-top:1px solid #22252b;padding-top:8px;">
                  <div style="background:#141619;border:1px solid #22252b;border-radius:2px;padding:4px 6px;display:flex;justify-content:space-between;align-items:center;">
                    <span style="color:#10b981;font-weight:700;">PROD Resiliency</span>
                    <span style="color:var(--text);font-family:var(--mono);font-weight:700;">100% (0)</span>
                  </div>
                  <div style="background:#141619;border:1px solid #22252b;border-radius:2px;padding:4px 6px;display:flex;justify-content:space-between;align-items:center;">
                    <span style="color:#38bdf8;font-weight:700;">Fleet Scope</span>
                    <span style="color:var(--text);font-family:var(--mono);font-weight:700;">{len(df)} Assets</span>
                  </div>
                  <div style="background:#141619;border:1px solid #22252b;border-radius:2px;padding:4px 6px;display:flex;justify-content:space-between;align-items:center;">
                    <span style="color:#ff9830;font-weight:700;">Governance Leads</span>
                    <span style="color:var(--text);font-family:var(--mono);font-weight:700;">5 Teams</span>
                  </div>
                  <div style="background:#141619;border:1px solid #22252b;border-radius:2px;padding:4px 6px;display:flex;justify-content:space-between;align-items:center;">
                    <span style="color:#73bf69;font-weight:700;">Batch Console</span>
                    <span style="color:var(--text);font-family:var(--mono);font-weight:700;">Ready</span>
                  </div>
                </div>
              </div>
            </div>
            """)

    # ==========================================================================
    # SUBTAB 3: ⚡ BATCH OPERATIONS CONSOLE
    # ==========================================================================
    with subtab_batch:
        if selected_entity_ids:
            batch_work = df[df["id"].isin(selected_entity_ids)].copy()
            batch_work.sort_values(by=["state", "team", "component", "env_no", "schema_name"], inplace=True)
        else:
            batch_work = filtered.copy()

        total_batch_n = len(batch_work)
        b_per_page = 15
        b_pages = max(1, (total_batch_n + b_per_page - 1) // b_per_page)
        b_page = st.session_state.setdefault("op_batch_page_no", 0)
        b_page = max(0, min(b_page, b_pages - 1))

        b_from = b_page * b_per_page + 1 if total_batch_n > 0 else 0
        b_to = min(total_batch_n, (b_page + 1) * b_per_page)
        page_slice = batch_work.iloc[b_from - 1:b_to].copy() if total_batch_n > 0 else batch_work.copy()

        all_filtered_ids = set(filtered["id"].tolist())
        is_all_filtered_selected = (len(all_filtered_ids) > 0 and all_filtered_ids.issubset(selected_entity_ids))

        bg_c1, bg_c2, bg_c3, bg_c4, bg_c5 = st.columns([1.3, 0.6, 1.0, 1.0, 0.8], gap="small")
        with bg_c1:
            if selected_entity_ids:
                st.markdown(f"<div style='font-size:10.5px;color:#38bdf8;font-weight:700;padding-top:4px;white-space:nowrap;'>⚡ {len(selected_entity_ids)} sel · Page {b_page + 1}/{b_pages}</div>", unsafe_allow_html=True)
            else:
                st.markdown(f"<div style='font-size:10.5px;color:#cbd5e1;font-weight:600;padding-top:4px;white-space:nowrap;'>Scope: {total_batch_n} · Page {b_page + 1}/{b_pages}</div>", unsafe_allow_html=True)
        with bg_c2:
            if b_pages > 1:
                p_c1, p_c2 = st.columns(2)
                if p_c1.button("‹", key="op_batch_p_prev", disabled=(b_page == 0), use_container_width=True):
                    st.session_state["op_batch_page_no"] = b_page - 1
                    st.rerun()
                if p_c2.button("›", key="op_batch_p_next", disabled=(b_page >= b_pages - 1), use_container_width=True):
                    st.session_state["op_batch_page_no"] = b_page + 1
                    st.rerun()
            elif not is_all_filtered_selected and len(all_filtered_ids) > 0:
                if st.button("All", key="op_batch_sel_all_flt", type="primary", use_container_width=True, help=f"Select all {len(filtered)} items in current filter"):
                    selected_entity_ids.update(all_filtered_ids)
                    st.rerun()
        with bg_c3:
            if st.button("+90d All", key="op_batch_bulk_90d", use_container_width=True, help="Extend every item on this page by 90 days"):
                changes = [(int(row.id), (row.exp_dt + pd.Timedelta(days=90)).date()) for row in page_slice.itertuples()]
                _apply_edits(changes)
                st.success(f"+90d applied to {len(changes)} items.")
                st.rerun()
        with bg_c4:
            if st.button("+1yr All", key="op_batch_bulk_1yr", use_container_width=True, help="Extend every item on this page by 1 year"):
                changes = [(int(row.id), (row.exp_dt + pd.Timedelta(days=365)).date()) for row in page_slice.itertuples()]
                _apply_edits(changes)
                st.success(f"+1yr applied to {len(changes)} items.")
                st.rerun()
        with bg_c5:
            if hasattr(st, "popover"):
                with st.popover("📅 Set", help="Set common expiry date for all items on page", use_container_width=True):
                    c_common_dt = st.date_input("Set all to date", key="op_batch_common_dt_pick")
                    if st.button("Apply", type="primary", key="op_batch_common_dt_apply", use_container_width=True):
                        changes = [(int(row.id), c_common_dt) for row in page_slice.itertuples()]
                        _apply_edits(changes)
                        st.success(f"Set {len(changes)} items to {c_common_dt}.")
                        st.rerun()

        if hasattr(st, "data_editor") and hasattr(st, "column_config") and not page_slice.empty:
            b_view = page_slice[["schema_name", "env_label", "exp_dt", "band", "days_left"]].copy()
            b_view["exp_dt"] = b_view["exp_dt"].dt.date
            b_view["days_left"] = b_view["days_left"].apply(ui.fmt_days)
            b_view["band"] = b_view["band"].apply(ui.health_text)

            b_edited = st.data_editor(
                b_view, key=f"op_batch_editor_p{b_page}", hide_index=True, use_container_width=True,
                num_rows="fixed", height=min(360, 40 + len(page_slice) * 35),
                column_config={
                    "schema_name": st.column_config.TextColumn("Schema Name", disabled=True, width=175),
                    "env_label": st.column_config.TextColumn("Env", disabled=True, width=55),
                    "exp_dt": st.column_config.DateColumn("Expiry Date", format="YYYY-MM-DD", required=True, width=140),
                    "band": st.column_config.TextColumn("Status", disabled=True, width=85),
                    "days_left": st.column_config.TextColumn("Time Left", disabled=True, width=110),
                },
            )

            b_ids = page_slice["id"].tolist()
            b_changes = []
            for b_pos, b_rec_id in enumerate(b_ids):
                b_before = b_view.iloc[b_pos]["exp_dt"]
                b_after = b_edited.iloc[b_pos]["exp_dt"]
                if b_after is not None and not pd.isna(b_after):
                    b_after = pd.to_datetime(b_after).date()
                    if b_after != b_before:
                        b_changes.append((b_rec_id, b_after))

            b_btn_col, b_note_col = st.columns([1.4, 2.6])
            if b_btn_col.button("Save Changes", type="primary", key="op_save_batch_btn", disabled=not b_changes, use_container_width=True):
                _apply_edits(b_changes)
                st.success(f"Saved {len(b_changes)} batch updates!")
                st.rerun()
            n_bchg = len(b_changes)
            b_note_col.markdown(f"<div style='font-size:10.5px;color:#94a3b8;padding-top:4px;'><b>{n_bchg}</b> unsaved {'change' if n_bchg == 1 else 'changes'} on current page</div>", unsafe_allow_html=True)
        elif page_slice.empty:
            st.markdown("<div style='font-size:11px;color:#94a3b8;padding:12px 0;'>No entities selected. Select items from the hierarchy tree or filters.</div>", unsafe_allow_html=True)

    # ==========================================================================
    # SUBTAB 4: 🌳 ORGANIZATIONAL HIERARCHY TREE
    # ==========================================================================
    with subtab_tree:
        bc_parts = ["<span style='color:var(--accent);font-weight:700;font-size:10px;'>All</span>"]
        d_st = state_filter if state_filter != "All States" else None
        if not d_st and len(tree_open) > 0:
            open_st = [s for s in STATES if s in tree_open]
            if len(open_st) == 1:
                d_st = open_st[0]

        d_tm = team_filter if team_filter != "All Teams" else None
        if not d_tm and len(tree_open) > 0 and d_st:
            open_tm = [t for t in ui.TEAMS if f"{d_st}/{t}" in tree_open]
            if len(open_tm) == 1:
                d_tm = open_tm[0]

        d_cp = comp_filter if comp_filter != "All Components" else None
        if not d_cp and len(tree_open) > 0 and d_st and d_tm:
            open_cp = [c for c in COMPONENT_ORDER if f"{d_st}/{d_tm}/{c}" in tree_open]
            if len(open_cp) == 1:
                d_cp = open_cp[0]

        if d_st:
            bc_parts.append(f"<span style='color:#f8fafc;font-weight:600;font-size:10px;'>State: {d_st}</span>")
        if d_tm:
            bc_parts.append(f"<span style='color:#cbd5e1;font-size:10px;'>Team: {d_tm}</span>")
        if d_cp:
            cp_c = ui.COMPONENT_CODE.get(d_cp, d_cp)
            bc_parts.append(f"<span style='color:#94a3b8;font-size:10px;'>Comp: {cp_c}</span>")
        if cur_kpi != "All":
            bc_parts.append(f"<span style='color:var(--accent);font-weight:600;font-size:10px;'>Status: {cur_kpi}</span>")

        bc_trail = " <span style='color:var(--rule);font-size:10px;margin:0 2px;'>›</span> ".join(bc_parts)

        def tri_state_info(child_ids: set, selected_ids: set) -> tuple[str, bool, str]:
            if not child_ids:
                return " ", False, "secondary"
            intersect_n = len(child_ids.intersection(selected_ids))
            if intersect_n == len(child_ids):
                return "✓", True, "primary"
            elif intersect_n > 0:
                return "−", True, "primary"
            else:
                return " ", False, "secondary"

        def toggle_tree_node(path: str, parent_prefix: str | None = None) -> None:
            if path in tree_open:
                to_remove = {p for p in tree_open if p == path or p.startswith(path + "/")}
                tree_open.difference_update(to_remove)
            else:
                if parent_prefix:
                    prefix_slash = parent_prefix + "/"
                    to_remove = {p for p in tree_open if p.startswith(prefix_slash)}
                    tree_open.difference_update(to_remove)
                else:
                    tree_open.clear()
                tree_open.add(path)

        st.markdown(f"""
        <div style="display:flex;align-items:center;justify-content:space-between;background:#181b1f;border:1px solid var(--rule);border-radius:2px;padding:4px 8px;margin-bottom:4px;">
          <div style="display:flex;align-items:center;gap:6px;min-width:0;overflow:hidden;">
            <span style="font-size:11px;font-weight:600;color:var(--ink);white-space:nowrap;">Hierarchy — State</span>
            <span style="color:var(--rule-soft);font-size:10px;">|</span>
            <div style="font-size:10px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;line-height:1.2;">{bc_trail}</div>
          </div>
          <span style="color:var(--mute);font-size:11px;flex:none;cursor:pointer;">⋮</span>
        </div>
        """, unsafe_allow_html=True)

        n_sel = len(selected_entity_ids)
        if n_sel > 0:
            tc1, tc2, tc3, tc4 = st.columns([1.1, 1.2, 1.3, 1.3], gap="small")
            with tc1:
                if st.button("⊞ Expand All", key="tree_exp_all", use_container_width=True):
                    for s_val in filtered["state"].unique():
                        tree_open.add(str(s_val))
                        st_sub = filtered[filtered["state"] == s_val]
                        for t_val in st_sub["team"].unique():
                            tree_open.add(f"{s_val}/{t_val}")
                            tm_sub = st_sub[st_sub["team"] == t_val]
                            for c_val in tm_sub["component"].unique():
                                tree_open.add(f"{s_val}/{t_val}/{c_val}")
                                cp_sub = tm_sub[tm_sub["component"] == c_val]
                                for e_val in cp_sub["env_label"].unique():
                                    tree_open.add(f"{s_val}/{t_val}/{c_val}/{e_val}")
                    st.rerun()
            with tc2:
                if st.button("⊟ Collapse All", key="tree_col_all", use_container_width=True):
                    tree_open.clear()
                    st.rerun()
            with tc3:
                if st.button(f"☐ Clear ({n_sel})", key="tree_clear_sel_btn", use_container_width=True):
                    selected_entity_ids.clear()
                    st.rerun()
            with tc4:
                if st.button(f"⚡ Batch ({n_sel}) ›", key="tree_send_to_batch", type="primary", use_container_width=True):
                    st.session_state["op_target_tab"] = "⚡ Batch Operations Console"
                    st.rerun()
        else:
            tc1, tc2, tc3, tc_stat = st.columns([1.1, 1.2, 1.3, 0.8], gap="small")
            with tc1:
                if st.button("⊞ Expand All", key="tree_exp_all", use_container_width=True):
                    for s_val in filtered["state"].unique():
                        tree_open.add(str(s_val))
                        st_sub = filtered[filtered["state"] == s_val]
                        for t_val in st_sub["team"].unique():
                            tree_open.add(f"{s_val}/{t_val}")
                            tm_sub = st_sub[st_sub["team"] == t_val]
                            for c_val in tm_sub["component"].unique():
                                tree_open.add(f"{s_val}/{t_val}/{c_val}")
                                cp_sub = tm_sub[tm_sub["component"] == c_val]
                                for e_val in cp_sub["env_label"].unique():
                                    tree_open.add(f"{s_val}/{t_val}/{c_val}/{e_val}")
                    st.rerun()
            with tc2:
                if st.button("⊟ Collapse All", key="tree_col_all", use_container_width=True):
                    tree_open.clear()
                    st.rerun()
            with tc3:
                if st.button("☑ Select All", key="tree_select_all_btn", use_container_width=True):
                    all_f_ids = set(filtered["id"].tolist())
                    selected_entity_ids.update(all_f_ids)
                    st.rerun()
            with tc_stat:
                st.markdown('<div style="height:28px;display:flex;align-items:center;justify-content:center;font-size:9.5px;color:#64748b;background:#141619;border:1px solid #22252b;border-radius:2px;font-family:var(--mono);">0 sel</div>', unsafe_allow_html=True)

        with st.container(height=380, border=True):
            for st_val in filtered["state"].unique():
                st_sub = filtered[filtered["state"] == st_val]
                st_path = str(st_val)
                st_is_open = st_path in tree_open
                st_worst = ui.worst_band(st_sub["band"].tolist())
                st_meta = ui.BAND_META.get(st_worst, ui.BAND_META["Healthy"])
                st_exp_n = (st_sub["days_left"] < 0).sum()
                st_child_ids = set(st_sub["id"].tolist())
                st_sym, st_uncheck, st_type = tri_state_info(st_child_ids, selected_entity_ids)

                s_c0, s_c1, s_c2 = st.columns([0.35, 4.15, 0.5])
                with s_c0:
                    if st.button(st_sym, key=f"sel_st_{st_val}", type=st_type, use_container_width=True):
                        if st_uncheck:
                            selected_entity_ids.difference_update(st_child_ids)
                        else:
                            selected_entity_ids.update(st_child_ids)
                        st.rerun()
                with s_c1:
                    is_st_foc = (cell_filter == (st_val, None)) or (state_filter == st_val and cell_filter is None and comp_filter == "All Components")
                    st_badge_txt = f"{st_exp_n} Expired" if st_exp_n else st_worst
                    btn_lbl = f"📍 State {st_val} ({len(st_sub)} items) · {st_meta['symbol']} {st_badge_txt}"
                    if st.button(btn_lbl, key=f"foc_st_tree_{st_val}", use_container_width=True, type="primary" if is_st_foc else "secondary"):
                        if is_st_foc:
                            st.session_state["op_cell_filter"] = None
                        else:
                            st.session_state["op_cell_filter"] = (st_val, None)
                            tree_open.clear(
                            )
                            tree_open.add(st_val)
                        st.rerun()

                with s_c2:
                    if st.button("▼" if st_is_open else "▶", key=f"t_st_{st_val}", use_container_width=True):
                        toggle_tree_node(st_path, None)
                        st.rerun()

                if st_is_open:
                    for tm_val in st_sub["team"].unique():
                        tm_sub = st_sub[st_sub["team"] == tm_val]
                        tm_path = f"{st_val}/{tm_val}"
                        tm_is_open = tm_path in tree_open
                        tm_worst = ui.worst_band(tm_sub["band"].tolist())
                        tm_meta = ui.BAND_META.get(tm_worst, ui.BAND_META["Healthy"])
                        tm_color = ui.TEAM_META.get(tm_val, {}).get("color", "#5794f2")
                        tm_child_ids = set(tm_sub["id"].tolist())
                        tm_sym, tm_uncheck, tm_type = tri_state_info(tm_child_ids, selected_entity_ids)

                        t_c0, t_c1, t_c2 = st.columns([0.35, 4.15, 0.5])
                        with t_c0:
                            if st.button(tm_sym, key=f"sel_tm_{st_val}_{tm_val}", type=tm_type, use_container_width=True):
                                if tm_uncheck:
                                    selected_entity_ids.difference_update(tm_child_ids)
                                else:
                                    selected_entity_ids.update(tm_child_ids)
                                st.rerun()
                        with t_c1:
                            st.markdown(f"""
                            <div class="tree-node-row{' active' if tm_is_open else ''}" style="display:flex;align-items:center;justify-content:space-between;margin-left:4px;background:#141619;border-radius:2px;padding:2px 6px;margin-bottom:2px;font-size:10px;">
                              <span style="color:{tm_color};font-weight:600;">
                                👥 {tm_val} <span style="color:var(--mute);font-weight:400;font-size:8.5px;">({len(tm_sub)})</span>
                              </span>
                              <span style="color:{tm_meta['color']};font-weight:600;font-size:8.5px;">{tm_meta['symbol']} {tm_worst}</span>
                            </div>
                            """, unsafe_allow_html=True)
                        with t_c2:
                            if st.button("▼" if tm_is_open else "▶", key=f"t_tm_{st_val}_{tm_val}", use_container_width=True):
                                toggle_tree_node(tm_path, st_path)
                                st.rerun()

                        if tm_is_open:
                            for cp_val in tm_sub["component"].unique():
                                cp_sub = tm_sub[tm_sub["component"] == cp_val]
                                cp_path = f"{st_val}/{tm_val}/{cp_val}"
                                cp_is_open = cp_path in tree_open
                                cp_worst = ui.worst_band(cp_sub["band"].tolist())
                                cp_meta = ui.BAND_META.get(cp_worst, ui.BAND_META["Healthy"])
                                cp_code = ui.COMPONENT_CODE.get(cp_val, cp_val)
                                cp_icon = ui.COMPONENT_ICONS.get(cp_val, ui.COMPONENT_ICONS.get(cp_code, "📦"))
                                cp_child_ids = set(cp_sub["id"].tolist())
                                cp_sym, cp_uncheck, cp_type = tri_state_info(cp_child_ids, selected_entity_ids)

                                cp_c0, cp_c1, cp_c2 = st.columns([0.35, 4.15, 0.5])
                                chip_head = f"{cp_icon} {cp_code}"
                                with cp_c0:
                                    if st.button(cp_sym, key=f"sel_cp_{st_val}_{tm_val}_{cp_code}", type=cp_type, use_container_width=True):
                                        if cp_uncheck:
                                            selected_entity_ids.difference_update(cp_child_ids)
                                        else:
                                            selected_entity_ids.update(cp_child_ids)
                                        st.rerun()
                                with cp_c1:
                                    st.markdown(f"""
                                    <div class="tree-node-row{' active' if cp_is_open else ''}" style="display:flex;align-items:center;justify-content:space-between;margin-left:8px;border-left:2px solid {cp_meta['color']};border-radius:2px;padding:2px 6px;margin-bottom:2px;font-size:9.5px;">
                                      <span style="color:var(--text);font-weight:500;">{chip_head} <span style="color:var(--mute);font-size:8.5px;">({len(cp_sub)})</span></span>
                                      <span style="color:{cp_meta['color']};font-size:8.5px;">{cp_meta['symbol']} {cp_worst}</span>
                                    </div>
                                    """, unsafe_allow_html=True)
                                with cp_c2:
                                    if st.button("▼" if cp_is_open else "▶", key=f"t_cp_{st_val}_{tm_val}_{cp_code}", use_container_width=True):
                                        toggle_tree_node(cp_path, tm_path)
                                        st.rerun()

                                if cp_is_open:
                                    for ev_val in cp_sub["env_label"].unique():
                                        ev_sub = cp_sub[cp_sub["env_label"] == ev_val]
                                        ev_path = f"{st_val}/{tm_val}/{cp_val}/{ev_val}"
                                        ev_is_open = ev_path in tree_open
                                        ev_worst = ui.worst_band(ev_sub["band"].tolist())
                                        ev_meta = ui.BAND_META.get(ev_worst, ui.BAND_META["Healthy"])
                                        ev_child_ids = set(ev_sub["id"].tolist())
                                        ev_sym, ev_uncheck, ev_type = tri_state_info(ev_child_ids, selected_entity_ids)

                                        ev_c0, ev_c1, ev_c2 = st.columns([0.35, 4.15, 0.5])
                                        with ev_c0:
                                            if st.button(ev_sym, key=f"sel_ev_{st_val}_{tm_val}_{cp_code}_{ev_val}", type=ev_type, use_container_width=True):
                                                if ev_uncheck:
                                                    selected_entity_ids.difference_update(ev_child_ids)
                                                else:
                                                    selected_entity_ids.update(ev_child_ids)
                                                st.rerun()
                                        with ev_c1:
                                            st.markdown(f"""
                                            <div class="tree-node-row{' active' if ev_is_open else ''}" style="display:flex;align-items:center;justify-content:space-between;margin-left:12px;border-radius:2px;padding:2px 4px;font-size:9px;color:var(--slate);">
                                              <span>🖥️ <span class="env-tag" style="font-size:8px;">{ev_val}</span> ({len(ev_sub)})</span>
                                              <span style="color:{ev_meta['color']};font-size:8px;">{ev_meta['symbol']} {ui.fmt_days(ev_sub['days_left'].min())}</span>
                                            </div>
                                            """, unsafe_allow_html=True)
                                        with ev_c2:
                                            if st.button("▼" if ev_is_open else "▶", key=f"t_ev_{st_val}_{tm_val}_{cp_code}_{ev_val}", use_container_width=True):
                                                toggle_tree_node(ev_path, cp_path)
                                                st.rerun()

                                        if ev_is_open:
                                            for r in ev_sub.itertuples():
                                                r_meta = ui.BAND_META.get(r.band, ui.BAND_META["Healthy"])
                                                is_act = (r.id == selected_id)
                                                is_leaf_sel = r.id in selected_entity_ids

                                                row_c0, row_c1, row_c2 = st.columns([0.35, 3.25, 1.4])
                                                with row_c0:
                                                    ck_txt = "✓" if is_leaf_sel else " "
                                                    if st.button(ck_txt, key=f"sel_leaf_{r.id}", type="primary" if is_leaf_sel else "secondary", use_container_width=True):
                                                        if is_leaf_sel:
                                                            selected_entity_ids.discard(r.id)
                                                        else:
                                                            selected_entity_ids.add(r.id)
                                                        st.rerun()
                                                with row_c1:
                                                    if st.button(r.schema_name, key=f"leaf_btn_{r.id}", type="primary" if is_act else "secondary", use_container_width=True):
                                                        st.session_state["op_active_id"] = r.id
                                                        st.rerun()
                                                with row_c2:
                                                    r_c = r_meta["color"]
                                                    r_s = r_meta["symbol"]
                                                    _sbar = ui.leaf_sparkbar(int(r.days_left))
                                                    st.markdown(
                                                        f"<div style='text-align:right;padding-top:4px;padding-right:4px;'>"
                                                        f"<div style='font-size:9.5px;font-weight:600;color:{r_c};white-space:nowrap;font-variant-numeric:tabular-nums;'>{r_s} {ui.fmt_days(r.days_left)}</div>"
                                                        f"{_sbar}"
                                                        f"</div>",
                                                        unsafe_allow_html=True
                                                    )

    # ==========================================================================
    # SUBTAB 5: ↩️ ROLLBACK & AUDIT HISTORY LEDGER
    # ==========================================================================
    with subtab_rev:
        active_edits = df[df["edited"]].copy()
        if active_edits.empty:
            st.markdown("""
            <div class="card" style="font-size:11px;color:#94a3b8;padding:16px;margin-top:8px;background:#181b1f;border:1px solid #2c3235;border-radius:4px;">
              <div style="font-weight:700;color:#10b981;margin-bottom:6px;font-size:13px;">✓ Fleet 100% In Sync with Source Workbooks</div>
              All 500 records match source Excel files. Local overrides made in the inventory, inspector, or batch editor appear here for 1-click rollback.
            </div>
            """, unsafe_allow_html=True)
        else:
            n_ovr = len(active_edits)
            rev_hdr_c1, rev_hdr_c2 = st.columns([2.5, 1.5], gap="small")
            with rev_hdr_c1:
                st.markdown(f"<div style='font-size:11px;color:#cbd5e1;padding-top:6px;'><b>{n_ovr}</b> local {'override' if n_ovr == 1 else 'overrides'} pending</div>", unsafe_allow_html=True)
            with rev_hdr_c2:
                if st.button(f"↩ Revert All ({n_ovr})", key="op_rev_all_btn", type="primary", use_container_width=True):
                    conn = get_connection(DB_PATH)
                    try:
                        for er in active_edits.itertuples():
                            revert_component_exp_date(conn, int(er.id))
                    finally:
                        conn.close()
                    st.session_state["_bust"] = st.session_state.get("_bust", 0) + 1
                    st.cache_data.clear()
                    st.success(f"Reverted all {n_ovr} overrides to source workbook dates.")
                    st.rerun()

            with st.container(height=360, border=True):
                for er in active_edits.itertuples():
                    src_dt = str(er.source_exp_date) if hasattr(er, "source_exp_date") else "—"
                    cur_dt = str(er.exp_date)
                    delta_days = er.days_left if hasattr(er, "days_left") else 0
                    arrow_color = "#10b981" if delta_days > 0 else "#f2495c"
                    ec1, ec2 = st.columns([3, 1])
                    ec1.markdown(
                        f"<div style='font-size:10.5px;padding:4px 0;'>"
                        f"<b>{er.schema_name}</b> <span style='color:#64748b;font-size:9.5px;'>({er.state} · {er.team})</span><br>"
                        f"<span style='color:#64748b;font-size:9px;'>Source: <code style='color:#94a3b8;'>{src_dt}</code>"
                        f" <span style='color:{arrow_color};font-weight:700;'>➔</span>"
                        f" Override: <code style='color:#38bdf8;font-weight:600;'>{cur_dt}</code></span>"
                        f"</div>",
                        unsafe_allow_html=True
                    )
                    if ec2.button("↩ Revert", key=f"op_rev_ledger_{er.id}", use_container_width=True):
                        conn = get_connection(DB_PATH)
                        try:
                            revert_component_exp_date(conn, int(er.id))
                        finally:
                            conn.close()
                        st.session_state["_bust"] = st.session_state.get("_bust", 0) + 1
                        st.cache_data.clear()
                        st.success(f"Reverted {er.schema_name}")
                        st.rerun()
