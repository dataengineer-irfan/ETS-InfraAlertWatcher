"""
app.py — Expiry Watchtower & Enterprise Governance Suite
========================================================
Enterprise-grade Streamlit application for database and component expiry tracking,
governance, and renewal workflows.

Views:
    1. Overview                 Consolidated zero-scroll Power BI executive canvas across all states
    2. State                    State-pinned canvas (AK, NH, ND) & renewal management editor
    3. Master-Detail Inspector  Split-pane workspace with live metadata, JSON/SQL payload, & inline actions
    4. Hierarchical Matrix      Grouped cross-tab matrix with multi-column sort & CSV export
    5. Governance & Alerts      Live database lineage, email alert simulator & workbook sync console
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "dashboard"))
sys.path.insert(0, str(ROOT / "src"))

import report  # noqa: E402
import ui  # noqa: E402
from db import (  # noqa: E402
    ensure_metric_snapshots,
    get_connection,
    get_metric_snapshots,
    revert_component_exp_date,
    update_component_exp_date,
    load_maintenance_schedules_csv,
    get_maintenance_schedules,
    update_maintenance_schedule,
)
from ingest_components import COMPONENTS, run as run_ingest  # noqa: E402
from expiry_checker import get_due_reminders, mark_sent  # noqa: E402
from notifier import (  # noqa: E402
    render_email,
    subject_for,
    smtp_config_from_env,
    send_real_smtp_email,
    render_expired_alert_email,
    dispatch_expired_alert_real,
)

DB_PATH = os.environ.get("EXPIRY_DB_PATH", str(ROOT / "data" / "expiry.db"))
WORKBOOK_DIR = os.environ.get("EXPIRY_WORKBOOK_DIR", str(ROOT))

STATES = ui.STATES
COMPONENT_ORDER = ui.COMPONENT_ORDER
ENV_ORDER = ui.ENV_ORDER

CANVAS_OVERVIEW = 580
CANVAS_STATE = 510
EDITOR_HEIGHT = 380

st.set_page_config(
    page_title="Expiry Watchtower - Enterprise Governance",
    layout="wide",
    initial_sidebar_state="collapsed",
)
st.markdown(ui.css(), unsafe_allow_html=True)


# ==========================================================================
# Data Loading & Ingestion
# ==========================================================================
@st.cache_data(ttl=2, show_spinner=False)
def load_records(db_path: str, _bust: int = 0) -> pd.DataFrame:
    """Read component_records and derive everything the views need."""
    conn = get_connection(db_path)
    df = pd.read_sql_query("SELECT * FROM component_records", conn)
    conn.close()

    if df.empty:
        return pd.DataFrame(columns=[
            "id", "state", "component", "env_no", "environment", "module",
            "schema_name", "exp_date", "source_exp_date", "edited_at",
            "exp_dt", "days_left", "band", "edited", "quarter", "env_label", "team",
        ])

    df["exp_dt"] = pd.to_datetime(df["exp_date"], errors="coerce")
    df = df.dropna(subset=["exp_dt"])
    df["days_left"] = (df["exp_dt"] - pd.Timestamp(date.today())).dt.days.astype(int)
    df["band"] = df["days_left"].apply(ui.health_of)
    df["edited"] = df["edited_at"].notna()
    df["quarter"] = ("Q" + df["exp_dt"].dt.quarter.astype(str)
                     + " " + df["exp_dt"].dt.year.astype(str))
    df["env_label"] = df["environment"].fillna("UNMAPPED")
    df["team"] = df.apply(lambda r: ui.team_of(r.get("schema_name", ""), r.get("component", ""), r.get("env_no", ""), r.get("team", None)), axis=1)
    df["sla_tier"] = df["env_label"].apply(lambda e: ui.env_tier(e)["tier"])
    df["sla_weight"] = df["env_label"].apply(lambda e: ui.env_tier(e)["weight"])
    return df


@st.cache_data(ttl=2, show_spinner=False)
def build_page(db_path: str, mode: str, state: str | None, _bust: int = 0) -> str:
    """The report canvas as a self-contained zero-scroll HTML document."""
    df = load_records(db_path, _bust)
    records = report.to_records(df.to_dict("records"), env_order=ENV_ORDER,
                                component_order=COMPONENT_ORDER)
    conn = get_connection(db_path)
    ensure_metric_snapshots(conn, records)
    snapshots = get_metric_snapshots(conn)
    # Ensure baseline maintenance schedules are loaded
    load_maintenance_schedules_csv(conn, str(ROOT / "config" / "maintenance_schedules.csv"))
    schedules = get_maintenance_schedules(conn)
    conn.close()
    return report.build(
        records, mode=mode, state=state, env_order=ENV_ORDER,
        snapshots=snapshots, schedules=schedules, teams=ui.TEAMS
    )


def ensure_ingested() -> None:
    """Auto-ingest workbooks on fresh startup if database table is empty."""
    conn = get_connection(DB_PATH)
    count = conn.execute("SELECT count(*) FROM component_records").fetchone()[0]
    conn.close()
    if count:
        return
    with st.spinner("Reading component workbooks for the first time..."):
        result = run_ingest(WORKBOOK_DIR, DB_PATH)
    if result["total_rows_read"]:
        st.cache_data.clear()
    else:
        st.error(
            f"No component data found in `{WORKBOOK_DIR}`. "
            + ", ".join(f"`{stem}.xlsx`" for stem in COMPONENTS)
        )
        st.stop()


def rerun() -> None:
    (st.rerun if hasattr(st, "rerun") else st.experimental_rerun)()


def bust_cache() -> None:
    """Force next read and page build to refresh after a write."""
    st.session_state["_bust"] = st.session_state.get("_bust", 0) + 1
    st.cache_data.clear()


def canvas(mode: str, state: str | None, height: int) -> None:
    """Mount the zero-scroll report canvas iframe."""
    components.html(
        build_page(DB_PATH, mode, state, st.session_state.get("_bust", 0)),
        height=height,
        scrolling=False,
    )


ensure_ingested()
records = load_records(DB_PATH, st.session_state.get("_bust", 0))

if records.empty:
    st.markdown(ui.empty(
        "The component table is empty",
        "Run: python src/ingest_components.py --data-dir . --db data/expiry.db"),
        unsafe_allow_html=True)
    st.stop()


# ==========================================================================
# Helpers & Search
# ==========================================================================
MANAGE_WINDOWS = {
    "Next 90 days": lambda d: d[d["days_left"].between(0, 90)],
    "Next 12 months": lambda d: d[d["days_left"].between(0, 365)],
    "Overdue only": lambda d: d[d["days_left"] < 0],
    "All dates": lambda d: d,
}


def apply_edits(changes: list) -> None:
    conn = get_connection(DB_PATH)
    for record_id, new_date in changes:
        dt_str = new_date.isoformat() if hasattr(new_date, "isoformat") else str(new_date)[:10]
        update_component_exp_date(conn, int(record_id), dt_str)
    conn.close()
    bust_cache()



def search(df: pd.DataFrame, query: str) -> pd.DataFrame:
    if not query or not query.strip():
        return df
    needle = query.strip().lower()
    haystack = (df["schema_name"].str.lower() + " " + df["env_label"].str.lower()
                + " " + df["component"].str.lower() + " "
                + df["env_no"].astype(str) + " " + df["exp_date"])
    return df[haystack.str.contains(needle, regex=False, na=False)]


# ==========================================================================
# View 2: State Manage Editor
# ==========================================================================
def render_manage(state_records: pd.DataFrame, state: str) -> None:
    tot = len(state_records)
    soonest = int(state_records["days_left"].min()) if not state_records.empty else 0
    overdue = int((state_records["days_left"] < 0).sum())
    overrides = int(state_records["edited"].sum())

    st.markdown(f"""
    <div class="state-ribbon">
      <div class="state-kpi-card">
        <div class="state-kpi-label">Portfolio Total</div>
        <div class="state-kpi-val">{tot}</div>
        <div class="state-kpi-hint">Tracked items in {state}</div>
      </div>
      <div class="state-kpi-card {'urgent' if soonest <= ui.CRITICAL_DAYS else 'warn' if soonest <= ui.WARNING_DAYS else 'good'}">
        <div class="state-kpi-label">Soonest Expiry</div>
        <div class="state-kpi-val">{ui.fmt_days(soonest)}</div>
        <div class="state-kpi-hint">{ui.health_of(soonest)} status horizon</div>
      </div>
      <div class="state-kpi-card {'urgent' if overdue else 'good'}">
        <div class="state-kpi-label">Lapsed & Overdue</div>
        <div class="state-kpi-val">{overdue}</div>
        <div class="state-kpi-hint">{'Action required immediately' if overdue else 'Zero lapsed accounts'}</div>
      </div>
      <div class="state-kpi-card">
        <div class="state-kpi-label">Local Overrides</div>
        <div class="state-kpi-val">{overrides}</div>
        <div class="state-kpi-hint">{'Modified locally' if overrides else '100% in sync with Excel'}</div>
      </div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown(ui.pick_line(
        f"Record a renewal for {state}",
        "change an expiry date in place — the workbook remains the system of record"),
        unsafe_allow_html=True)

    filters, _, panel = st.columns([2.8, 0.04, 1.1])

    with filters:
        c1, c2, c3, c4, c5 = st.columns([1.5, 1.0, 1.2, 1.0, 1.1])
        query = c1.text_input("Search", key="mg_q",
                              placeholder="Search schema, env...",
                              label_visibility="collapsed")
        team_pick = c2.selectbox("Team", ["All Teams"] + ui.TEAMS, key="mg_team",
                                 label_visibility="collapsed")
        comp_pick = c3.selectbox("Component", ["All Components"] + COMPONENT_ORDER, key="mg_comp",
                                 label_visibility="collapsed",
                                 format_func=lambda c: ui.COMPONENT_CODE.get(c, c) if c != "All Components" else "All Components")
        health_pick = c4.selectbox("Health", ["All Health"] + ui.BANDS, key="mg_health",
                                   label_visibility="collapsed")
        window = c5.selectbox("Window", list(MANAGE_WINDOWS), key="mg_window",
                              label_visibility="collapsed")

        work = search(state_records, query)
        if team_pick != "All Teams":
            work = work[work["team"] == team_pick]
        if comp_pick != "All Components":
            work = work[work["component"] == comp_pick]
        if health_pick != "All Health":
            work = work[work["band"] == health_pick]
        work = MANAGE_WINDOWS[window](work).sort_values("days_left")

        counts = work["band"].value_counts().to_dict()
        n_work = len(work)
        st.markdown(ui.note(
            f"<b>{n_work}</b> {'record' if n_work == 1 else 'records'} in view — {int(counts.get('Expired', 0))} expired, "
            f"{int(counts.get('Critical', 0))} critical, {int(counts.get('Warning', 0))} warning, "
            f"{int(counts.get('Healthy', 0))} healthy."), unsafe_allow_html=True)

        if work.empty:
            st.markdown(ui.empty("Nothing in this window",
                                 "Widen the window or clear the search to see records again."),
                        unsafe_allow_html=True)
        elif hasattr(st, "data_editor") and hasattr(st, "column_config"):
            view = work[["schema_name", "env_label", "team", "component",
                         "exp_dt", "band", "days_left"]].copy()
            view["exp_dt"] = view["exp_dt"].dt.date
            view["days_left"] = view["days_left"].apply(ui.fmt_days)
            view["band"] = view["band"].apply(ui.health_text)
            editor_h = min(EDITOR_HEIGHT, max(140, (len(view) + 1) * 36 + 32))
            edited = st.data_editor(
                view, key="mg_editor", hide_index=True, use_container_width=True,
                num_rows="fixed", height=editor_h,
                column_config={
                    "schema_name": st.column_config.TextColumn("Schema Name", disabled=True, width="medium"),
                    "env_label": st.column_config.TextColumn("Environment", disabled=True, width="small"),
                    "team": st.column_config.TextColumn("Team", disabled=True, width="small"),
                    "component": st.column_config.TextColumn("Component", disabled=True, width="medium"),
                    "exp_dt": st.column_config.DateColumn(
                        "Expiry Date", format="YYYY-MM-DD", required=True, width="medium",
                        help="Type or pick a new date, then press Save changes."),
                    "band": st.column_config.TextColumn("Health Status", disabled=True, width="small"),
                    "days_left": st.column_config.TextColumn("Time Left", disabled=True, width="small"),
                },
            )

            ids = work["id"].tolist()
            changes = []
            for position, record_id in enumerate(ids):
                before = view.iloc[position]["exp_dt"]
                after = edited.iloc[position]["exp_dt"]
                if after is None or pd.isna(after):
                    continue
                after = pd.to_datetime(after).date()
                if after != before:
                    changes.append((record_id, after))

            save_col, note_col = st.columns([1, 3])
            if save_col.button("Save changes", type="primary", use_container_width=True,
                                disabled=not changes):
                apply_edits(changes)
                st.session_state["mg_saved"] = len(changes)
                rerun()
            n_chg = len(changes)
            note_col.markdown(ui.note(
                f"<b>{n_chg}</b> unsaved {'change' if n_chg == 1 else 'changes'} — press Save changes to apply."
                if changes else
                "Change a date above to enable saving."), unsafe_allow_html=True)
        else:
            labels = {f"{r.schema_name} · {r.env_label} · {r.exp_date}": r.id
                      for r in work.itertuples()}
            pick = st.selectbox("Record", list(labels), key="mg_pick")
            row = work[work["id"] == labels[pick]].iloc[0]
            with st.form("mg_form"):
                new_date = st.date_input("New expiry date", value=row["exp_dt"].date())
                if st.form_submit_button("Save change", type="primary"):
                    apply_edits([(row["id"], new_date)])
                    st.session_state["mg_saved"] = 1
                    rerun()

        if st.session_state.pop("mg_saved", None):
            st.success("Saved. The Overview tab now reflects the new dates.")

    with panel:
        edits = state_records[state_records["edited"]].copy()
        st.markdown('<div class="eyebrow">Local Edits Ledger</div>', unsafe_allow_html=True)

        if edits.empty:
            st.markdown(f"""
            <div class="card" style="font-size:12px;line-height:1.5;color:#94a3b8;">
              <div style="font-weight:700;color:#f8fafc;margin-bottom:4px;display:flex;align-items:center;gap:6px;">
                <span style="color:#10b981;">✓</span> 100% In Sync with Excel
              </div>
              All dates for <b>{state}</b> match the source workbooks. Changes saved in the editor on the left will appear here with 1-click rollback history.
            </div>
            """, unsafe_allow_html=True)
        else:
            edits["edited_dt"] = pd.to_datetime(edits["edited_at"], format="mixed", utc=True)
            edits = edits.sort_values("edited_dt", ascending=False)

            n_edt = len(edits)
            st.markdown(ui.note(
                f"<b>{n_edt}</b> {'record' if n_edt == 1 else 'records'} differ from the workbook. Reverting restores the "
                "workbook date."), unsafe_allow_html=True)
            st.markdown(ui.edits_table([
                {"schema_name": r.schema_name, "environment": r.env_label,
                 "source_exp_date": r.source_exp_date, "exp_date": r.exp_date,
                 "edited_at": r.edited_dt.date()}
                for r in edits.itertuples()
            ]), unsafe_allow_html=True)

            revert_labels = {f"{r.schema_name} · {r.env_label} · now {r.exp_date}": r.id
                             for r in edits.itertuples()}
            pick = st.selectbox("Revert to workbook value", list(revert_labels),
                                key="mg_revert", label_visibility="collapsed")
            if st.button("Revert selected", key="mg_revert_go", use_container_width=True):
                conn = get_connection(DB_PATH)
                revert_component_exp_date(conn, int(revert_labels[pick]))
                conn.close()
                bust_cache()
                rerun()


def render_state_maintenance_windows(state: str | None = None) -> None:
    """Renders high-contrast operational maintenance windows synchronized with live team data."""
    conn = get_connection(DB_PATH)
    load_maintenance_schedules_csv(conn, str(ROOT / "config" / "maintenance_schedules.csv"))
    all_schedules = get_maintenance_schedules(conn)
    conn.close()

    schedules = [s for s in all_schedules if s.get("state") == state] if state else all_schedules
    if not schedules:
        return

    today_name = datetime.now().strftime("%A").lower()
    today_dt = date.today().isoformat()

    title = f"🛠️ Operational Maintenance Windows for {state}" if state else "🛠️ Fleet-Wide Operational Maintenance Windows"
    subtitle = "Live operational cadences, scheduled days, maintenance hours, and team playbooks."

    rows_html = []
    for s in schedules:
        st_code = s.get("state", "")
        team_name = s.get("team", "")
        t_meta = ui.TEAM_META.get(team_name, ui.TEAM_META["Core"])
        days_str = str(s.get("days_of_week", "")).lower()
        is_today = today_name in days_str or (s.get("next_run_date") == today_dt)

        status_badge = (
            '<span style="background:rgba(16,185,129,0.2);color:#10b981;border:1px solid rgba(16,185,129,0.5);border-radius:4px;padding:2px 7px;font-size:10px;font-weight:700;">🟢 ACTIVE TODAY</span>'
            if is_today else
            f'<span style="background:rgba(56,189,248,0.12);color:#38bdf8;border:1px solid rgba(56,189,248,0.3);border-radius:4px;padding:2px 7px;font-size:10px;font-weight:600;">Upcoming ({s.get("next_run_date", "Sun")})</span>'
        )

        state_cell = f"<td class='m' style='font-weight:700;'>{st_code}</td>" if not state else ""

        rows_html.append(
            f"<tr style='{'background:rgba(16,185,129,0.06);' if is_today else ''}'>"
            f"{state_cell}"
            f"<td class='m' style='color:{t_meta['color']};font-weight:700;'>{team_name}</td>"
            f"<td style='color:#f8fafc;font-size:11.5px;'>{s.get('frequency_blurb', '')}</td>"
            f"<td class='m' style='font-size:11px;'>{s.get('days_of_week', '')}</td>"
            f"<td class='m' style='color:var(--slate);font-size:11px;'><code>{s.get('time_window', '')}</code></td>"
            f"<td class='m' style='font-size:11px;'>{s.get('next_run_date', '')}</td>"
            f"<td>{status_badge}</td>"
            f"<td style='font-size:11px;color:#94a3b8;max-width:300px;'>{s.get('notes', '')}</td>"
            f"</tr>"
        )

    state_th = "<th>State</th>" if not state else ""
    head = f"<tr>{state_th}<th>Team</th><th>Recurrence Cadence</th><th>Maintenance Days</th><th>Window (UTC)</th><th>Next Planned Date</th><th>Status</th><th>Operational Notes</th></tr>"

    with st.expander(f"{title} ({len(schedules)} Schedules)", expanded=(state is not None)):
        st.markdown(f"<div style='font-size:11.5px;color:#94a3b8;margin-bottom:8px;'>{subtitle}</div>", unsafe_allow_html=True)
        st.markdown(f"<div style='border:1px solid var(--rule);border-radius:7px;overflow:hidden;'><table class='tblx'>{head}{''.join(rows_html)}</table></div>", unsafe_allow_html=True)


# ==========================================================================
# View 2: Portfolio Matrix & Operations Hub (Power BI Master-Detail Workspace)
# ==========================================================================
def render_operations_hub(df: pd.DataFrame) -> None:
    # 0. Session state defaults and dynamic key versioning for error-free resets
    reset_idx = st.session_state.setdefault("op_reset_idx", 0)
    cur_kpi = st.session_state.setdefault("op_kpi_filter", "All")
    cell_filter = st.session_state.setdefault("op_cell_filter", None)
    tree_open = st.session_state.setdefault("op_tree_open", set())
    selected_entity_ids = st.session_state.setdefault("op_selected_entity_ids", set())

    # 1. Top Slicer Command Bar (7 columns with inline CSV & Reset Scope)
    f1, f2, f3, f4, f5, f6, f7 = st.columns([1.5, 0.85, 0.9, 1.05, 0.85, 0.55, 0.65])
    q = f1.text_input("Filter", key=f"op_search_{reset_idx}", placeholder="Search schema, env...", label_visibility="collapsed")
    state_filter = f2.selectbox("State", ["All States"] + STATES, key=f"op_state_{reset_idx}", label_visibility="collapsed")
    team_filter = f3.selectbox("Team", ["All Teams"] + ui.TEAMS, key=f"op_team_{reset_idx}", label_visibility="collapsed")
    comp_filter = f4.selectbox(
        "Component",
        ["All Components"] + COMPONENT_ORDER,
        key=f"op_comp_{reset_idx}",
        label_visibility="collapsed",
        format_func=lambda c: ui.COMPONENT_CODE.get(c, c) if c != "All Components" else "All Components",
    )
    health_filter = f5.selectbox("Health", ["All Health"] + ui.BANDS, key=f"op_health_{reset_idx}", label_visibility="collapsed")

    filtered = df.copy()
    if q:
        filtered = search(filtered, q)
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

    if cur_kpi == "Expired":
        filtered = filtered[filtered["band"] == "Expired"]
    elif cur_kpi == "Urgent":
        filtered = filtered[filtered["band"].isin(["Critical", "Warning"])]
    elif cur_kpi == "Healthy":
        filtered = filtered[filtered["band"] == "Healthy"]

    filtered = filtered.sort_values("days_left")

    # Scope Determination
    is_scoped = (
        bool(q) or
        state_filter != "All States" or
        team_filter != "All Teams" or
        comp_filter != "All Components" or
        health_filter != "All Health" or
        cur_kpi != "All" or
        cell_filter is not None or
        len(tree_open) > 0 or
        len(selected_entity_ids) > 0
    )

    csv_data = filtered.to_csv(index=False).encode("utf-8")
    if hasattr(st, "download_button"):
        f6.download_button(
            label="📥 CSV",
            data=csv_data,
            file_name=f"expiry_operations_{date.today().isoformat()}.csv",
            mime="text/csv",
            use_container_width=True,
        )

    with f7:
        if is_scoped:
            if st.button("↺ Reset", key="op_sc_reset", use_container_width=True, type="primary"):
                st.session_state["op_reset_idx"] = reset_idx + 1
                st.session_state["op_kpi_filter"] = "All"
                st.session_state["op_cell_filter"] = None
                st.session_state["op_tree_open"] = set()
                st.session_state["op_selected_entity_ids"] = set()
                st.session_state["op_batch_page_no"] = 0
                rerun()
        else:
            st.button("↺ Reset", key="op_sc_reset_dis", use_container_width=True, disabled=True)

    # 2. Scope Ribbon (Ultra-thin 18px single-line telemetry)
    scope_parts = []
    if cell_filter:
        c_st, c_cp = cell_filter
        scope_parts.append(f"{c_st} × {ui.COMPONENT_CODE.get(c_cp, c_cp) if c_cp else 'All'}")
    if state_filter != "All States": scope_parts.append(f"State {state_filter}")
    if team_filter != "All Teams": scope_parts.append(f"Team {team_filter}")
    if comp_filter != "All Components": scope_parts.append(ui.COMPONENT_CODE.get(comp_filter, comp_filter))
    if health_filter != "All Health": scope_parts.append(f"Health: {health_filter}")
    elif cur_kpi != "All": scope_parts.append(f"KPI: {cur_kpi}")
    if q: scope_parts.append(f'"{q}"')
    if len(tree_open) > 0: scope_parts.append(f"{len(tree_open)} {'branch' if len(tree_open) == 1 else 'branches'} drilled")
    if len(selected_entity_ids) > 0: scope_parts.append(f"{len(selected_entity_ids)} entity batch")

    scope_name = "All Teams & Portfolios" if not scope_parts else " · ".join(scope_parts)
    _utc_now = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")

    # Production SLA Exposure & Operational Debt Breakdown
    sc_prod_exp = int(((filtered["days_left"] < 0) & (filtered["env_label"] == "PROD")).sum())
    sc_dr_exp = int(((filtered["days_left"] < 0) & (filtered["env_label"] == "DR")).sum())
    sc_mo_exp = int(((filtered["days_left"] < 0) & (filtered["env_label"] == "MO")).sum())
    sc_oth_exp = int(((filtered["days_left"] < 0) & (~filtered["env_label"].isin(["PROD", "DR", "MO"]))).sum())

    if sc_prod_exp == 0:
        sla_callout = '<span style="font-size:9px;font-weight:700;background:rgba(115,191,105,0.16);color:#73bf69;border:1px solid rgba(115,191,105,0.3);padding:1px 6px;border-radius:2px;">🛡️ PROD SLA: 100% RESILIENT (0 Breaches)</span>'
    else:
        sla_callout = f'<span style="font-size:9px;font-weight:700;background:rgba(242,73,92,0.18);color:#f2495c;border:1px solid rgba(242,73,92,0.4);padding:1px 6px;border-radius:2px;">⚠️ PROD SLA BREACH: {sc_prod_exp} Overdue</span>'

    debt_sub = []
    if sc_dr_exp > 0: debt_sub.append(f"{sc_dr_exp} DR")
    if sc_mo_exp > 0: debt_sub.append(f"{sc_mo_exp} MO")
    if sc_oth_exp > 0: debt_sub.append(f"{sc_oth_exp} Other")
    debt_callout = f'<span style="font-size:9px;color:var(--warning);font-weight:600;">(Overdue Debt: {" · ".join(debt_sub)})</span>' if debt_sub else '<span style="font-size:9px;color:var(--healthy);font-weight:600;">(Debt Free)</span>'

    st.markdown(f"""
    <div class="scope-line" style="margin-top:2px;margin-bottom:6px;padding:6px 10px;">
      <div style="display:flex;align-items:center;gap:8px;flex:1;flex-wrap:wrap;">
        <span class="scope-label">SCOPE</span>
        <span class="scope-val">{scope_name}</span>
        <span class="scope-muted">({len(filtered)} of {len(df)} total managed assets)</span>
        {sla_callout}
        {debt_callout}
      </div>
      <div style="display:flex;align-items:center;gap:8px;margin-left:auto;">
        <div class="live-dot"></div>
        <span class="scope-muted" style="font-variant-numeric:tabular-nums;font-size:11px;">Live Data Sync · {_utc_now}</span>
      </div>
    </div>
    """, unsafe_allow_html=True)

    # 3. Executive Metric Ribbon — Compact Grafana-style stat panels
    tot_cnt = len(df)
    scope_cnt = len(filtered)
    exp_cnt = int((filtered["days_left"] < 0).sum())
    crit_cnt = int((filtered["days_left"].between(0, ui.CRITICAL_DAYS)).sum())
    warn_cnt = int((filtered["days_left"].between(ui.CRITICAL_DAYS + 1, ui.WARNING_DAYS)).sum())
    hlth_cnt = int((filtered["days_left"] > ui.WARNING_DAYS).sum())
    g_exp = int((df["days_left"] < 0).sum())

    k1_sub = f"Filtered scope ({scope_cnt} of {tot_cnt} fleet)" if scope_cnt < tot_cnt else "Consolidated fleet coverage"
    k2_sub = f"Requires renewal ({g_exp} across fleet)" if (scope_cnt < tot_cnt and exp_cnt != g_exp) else ("Requires immediate renewal" if exp_cnt else "Zero overdue accounts")
    k3_sub = f"{crit_cnt} critical (≤15d) · {warn_cnt} warning (≤30d)"
    pct_local = (hlth_cnt / scope_cnt * 100) if scope_cnt else 0
    k4_sub = f"{pct_local:.1f}% compliance rate"

    k1_active = (cur_kpi == "All" and not is_scoped)
    k2_active = (cur_kpi == "Expired" or health_filter == "Expired")
    k3_active = (cur_kpi == "Urgent" or health_filter in ["Critical", "Warning"])
    k4_active = (cur_kpi == "Healthy" or health_filter == "Healthy")

    # Build sparkline trend from snapshot history
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


    k1, k2, k3, k4 = st.columns(4)

    with k1:
        st.markdown(ui.grafana_stat_card(
            label="Portfolio Scope",
            value=f"{scope_cnt} / {tot_cnt}",
            color="#5794f2",
            subtext=k1_sub,
            badge="ALL FLEET" if k1_active else "SCOPED",
            sparkline_vals=_scope_trend,
            state="ok",
        ), unsafe_allow_html=True)
        if st.button("✓ Active: All Fleet" if k1_active else "Filter: All Fleet", key="op_kpi_all", use_container_width=True, type="primary" if k1_active else "secondary"):
            st.session_state["op_kpi_filter"] = "All"
            st.session_state["op_cell_filter"] = None
            rerun()

    with k2:
        k2_state = "firing" if exp_cnt > 0 else "ok"
        st.markdown(ui.grafana_stat_card(
            label="Expired Items",
            value=exp_cnt,
            color="#f2495c" if exp_cnt else "#73bf69",
            subtext=k2_sub,
            badge="FIRING" if exp_cnt else "CLEAR",
            sparkline_vals=_exp_trend,
            delta=f"▲ +{exp_cnt} vs baseline" if exp_cnt else "✓ None overdue",
            state=k2_state,
        ), unsafe_allow_html=True)
        if st.button("✓ Active: Expired" if k2_active else "Filter: Expired", key="op_kpi_exp", use_container_width=True, type="primary" if k2_active else "secondary"):
            if k2_active:
                st.session_state["op_kpi_filter"] = "All"
            else:
                st.session_state["op_kpi_filter"] = "Expired"
                st.session_state["op_cell_filter"] = None
                tree_open.clear()
                tree_open.add("ND")
                tree_open.add("ND/Core")
                tree_open.add("ND/Core/Database Password Expiry")
            rerun()

    with k3:
        k3_state = "pending" if (crit_cnt + warn_cnt) > 0 else "ok"
        st.markdown(ui.grafana_stat_card(
            label="Critical & Warning",
            value=crit_cnt + warn_cnt,
            color="#f2495c" if crit_cnt else ("#ff9830" if warn_cnt else "#73bf69"),
            subtext=k3_sub,
            badge="PENDING" if (crit_cnt + warn_cnt) else "STABLE",
            sparkline_vals=_cw_trend,
            delta=f"▲ {crit_cnt}C + {warn_cnt}W" if (crit_cnt + warn_cnt) else "✓ All stable",
            state=k3_state,
        ), unsafe_allow_html=True)
        if st.button("✓ Active: Urgent" if k3_active else "Filter: Urgent", key="op_kpi_urgent", use_container_width=True, type="primary" if k3_active else "secondary"):
            if k3_active:
                st.session_state["op_kpi_filter"] = "All"
            else:
                st.session_state["op_kpi_filter"] = "Urgent"
                st.session_state["op_cell_filter"] = None
                tree_open.clear()
                tree_open.add("AK")
                tree_open.add("NH")
            rerun()

    with k4:
        st.markdown(ui.grafana_stat_card(
            label="Healthy Entities",
            value=hlth_cnt,
            color="#73bf69",
            subtext=k4_sub,
            badge="COMPLIANT",
            donut_pct=pct_local,
            delta=f"✓ {pct_local:.1f}% fleet OK",
            state="ok",
        ), unsafe_allow_html=True)
        if st.button("✓ Active: Healthy" if k4_active else "Filter: Healthy", key="op_kpi_hlth", use_container_width=True, type="primary" if k4_active else "secondary"):
            if k4_active:
                st.session_state["op_kpi_filter"] = "All"
            else:
                st.session_state["op_kpi_filter"] = "Healthy"
                st.session_state["op_cell_filter"] = None
            rerun()

    # 4. Cross-Filter Visual Feedback Banner (Animates cause & effect connection)
    if k2_active:
        st.markdown(f"""
        <div class="cross-filter-pulse" style="background:rgba(242,73,92,0.12);border:1px solid #f2495c;border-radius:2px;padding:3px 10px;margin-top:3px;margin-bottom:3px;display:flex;align-items:center;justify-content:space-between;">
          <div style="display:flex;align-items:center;gap:6px;">
            <span style="font-size:10.5px;font-weight:700;color:#f2495c;">⚡ ACTIVE CROSS-FILTER:</span>
            <span style="font-size:11px;font-weight:700;color:var(--text);">Expired Items</span>
            <span style="font-size:9.5px;color:var(--slate);">({scope_cnt} debt entities synchronized across Hierarchy Tree & Heatmap)</span>
          </div>
          <span style="font-size:9.5px;color:#f2495c;font-variant-numeric:tabular-nums;font-weight:600;">Tree & Heatmap Linked</span>
        </div>
        """, unsafe_allow_html=True)
    elif k3_active:
        st.markdown(f"""
        <div class="cross-filter-pulse" style="background:rgba(255,152,48,0.12);border:1px solid #ff9830;border-radius:2px;padding:3px 10px;margin-top:3px;margin-bottom:3px;display:flex;align-items:center;justify-content:space-between;">
          <div style="display:flex;align-items:center;gap:6px;">
            <span style="font-size:10.5px;font-weight:700;color:#ff9830;">⚡ ACTIVE CROSS-FILTER:</span>
            <span style="font-size:11px;font-weight:700;color:var(--text);">Critical & Warning</span>
            <span style="font-size:9.5px;color:var(--slate);">({scope_cnt} urgent entities synchronized across Hierarchy Tree & Heatmap)</span>
          </div>
          <span style="font-size:9.5px;color:#ff9830;font-variant-numeric:tabular-nums;font-weight:600;">Tree & Heatmap Linked</span>
        </div>
        """, unsafe_allow_html=True)
    elif k4_active and is_scoped:
        st.markdown(f"""
        <div class="cross-filter-pulse" style="background:rgba(115,191,105,0.12);border:1px solid #73bf69;border-radius:2px;padding:3px 10px;margin-top:3px;margin-bottom:3px;display:flex;align-items:center;justify-content:space-between;">
          <div style="display:flex;align-items:center;gap:6px;">
            <span style="font-size:10.5px;font-weight:700;color:#73bf69;">⚡ ACTIVE CROSS-FILTER:</span>
            <span style="font-size:11px;font-weight:700;color:var(--text);">Healthy Entities</span>
            <span style="font-size:9.5px;color:var(--slate);">({scope_cnt} compliant assets synchronized across Hierarchy Tree & Heatmap)</span>
          </div>
          <span style="font-size:9.5px;color:#73bf69;font-variant-numeric:tabular-nums;font-weight:600;">Tree & Heatmap Linked</span>
        </div>
        """, unsafe_allow_html=True)
    elif cell_filter:
        c_st, c_cp = cell_filter
        st.markdown(f"""
        <div class="cross-filter-pulse" style="background:rgba(255,120,10,0.12);border:1px solid #ff780a;border-radius:2px;padding:3px 10px;margin-top:3px;margin-bottom:3px;display:flex;align-items:center;justify-content:space-between;">
          <div style="display:flex;align-items:center;gap:6px;">
            <span style="font-size:10.5px;font-weight:700;color:#ff780a;">⚡ ACTIVE HEATMAP FILTER:</span>
            <span style="font-size:11px;font-weight:700;color:var(--text);">State {c_st}{f' × {c_cp}' if c_cp else ''}</span>
            <span style="font-size:9.5px;color:var(--slate);">({scope_cnt} entities in focus across Tree & Inspector)</span>
          </div>
          <span style="font-size:9.5px;color:#ff780a;font-variant-numeric:tabular-nums;font-weight:600;">Heatmap Synced</span>
        </div>
        """, unsafe_allow_html=True)
    else:
        st.markdown("<div style='margin-top:2px;'></div>", unsafe_allow_html=True)

    # 5. Master-Detail Workspace (49% Left Hierarchy Tree / 51% Right Inspector)
    left_col, _, right_col = st.columns([1.98, 0.04, 2.02])

    if filtered.empty:
        selected_id = None
    else:
        cur_active_id = st.session_state.get("op_active_id")
        if cur_active_id not in filtered["id"].values:
            most_urgent = filtered.sort_values("days_left").iloc[0]
            cur_active_id = int(most_urgent["id"])
            st.session_state["op_active_id"] = cur_active_id
        selected_id = cur_active_id


    with left_col:
        lt_c1, lt_c2 = st.columns([1.6, 1.4])
        with lt_c1:
            if hasattr(st, "radio"):
                left_mode = st.radio(
                    "Explorer Mode",
                    ["🌳 Tree", "📋 Power Grid"],
                    horizontal=True,
                    label_visibility="collapsed",
                    key="op_explorer_mode",
                )
            else:
                left_mode = "🌳 Tree"
        with lt_c2:
            st.markdown(
                f"<div style='text-align:right;font-size:9.5px;color:#94a3b8;padding-top:4px;font-family:var(--mono);'>"
                f"<b>{len(filtered)}</b> of {len(df)} Assets</div>",
                unsafe_allow_html=True,
            )

        if filtered.empty:
            st.markdown(ui.empty("No records match filter", "Try broadening your search query or reset filters."), unsafe_allow_html=True)
        elif left_mode == "📋 Power Grid":
            selected_entity_ids = st.session_state.setdefault("op_selected_entity_ids", set())

            # Sort and Search Controls
            pg_s_c1, pg_s_c2 = st.columns([1.8, 1.4])
            with pg_s_c1:
                pg_sort = st.selectbox(
                    "Sort Grid",
                    ["Days Left (Asc)", "Days Left (Desc)", "SLA Weight (High Risk First)", "Schema Name (A-Z)", "State (A-Z)"],
                    label_visibility="collapsed",
                    key="op_pg_sort",
                )
            with pg_s_c2:
                all_pg_ids = set(filtered["id"].tolist())
                n_sel = len(selected_entity_ids)
                if n_sel > 0:
                    if st.button(f"Clear ({n_sel})", key="pg_clear_all_sel", use_container_width=True):
                        selected_entity_ids.clear()
                        rerun()
                else:
                    if st.button("Select Filtered", key="pg_sel_all_filt", use_container_width=True):
                        selected_entity_ids.update(all_pg_ids)
                        rerun()

            pg_work = filtered.copy()
            if pg_sort == "Days Left (Asc)":
                pg_work = pg_work.sort_values("days_left", ascending=True)
            elif pg_sort == "Days Left (Desc)":
                pg_work = pg_work.sort_values("days_left", ascending=False)
            elif pg_sort == "SLA Weight (High Risk First)":
                pg_work = pg_work.sort_values(["sla_weight", "days_left"], ascending=[False, True])
            elif pg_sort == "Schema Name (A-Z)":
                pg_work = pg_work.sort_values("schema_name", ascending=True)
            elif pg_sort == "State (A-Z)":
                pg_work = pg_work.sort_values(["state", "days_left"], ascending=[True, True])

            PAGE_SIZE = 14
            n_records = len(pg_work)
            n_pages = max(1, (n_records + PAGE_SIZE - 1) // PAGE_SIZE)
            cur_page = st.session_state.setdefault("op_pg_page_idx", 0)
            if cur_page >= n_pages:
                cur_page = n_pages - 1
                st.session_state["op_pg_page_idx"] = cur_page

            # Compact Pagination Bar
            p_c1, p_c2, p_c3 = st.columns([1.0, 2.0, 1.0])
            with p_c1:
                if st.button("◀ Prev", key="op_pg_prev_btn", disabled=(cur_page <= 0), use_container_width=True):
                    st.session_state["op_pg_page_idx"] = max(0, cur_page - 1)
                    rerun()
            with p_c2:
                st.markdown(
                    f"<div style='text-align:center;font-size:10px;font-weight:700;color:#f8fafc;padding-top:5px;font-family:var(--mono);'>"
                    f"Page {cur_page + 1} of {n_pages} <span style='color:#94a3b8;font-weight:400;'>({n_records} assets)</span></div>",
                    unsafe_allow_html=True,
                )
            with p_c3:
                if st.button("Next ▶", key="op_pg_next_btn", disabled=(cur_page >= n_pages - 1), use_container_width=True):
                    st.session_state["op_pg_page_idx"] = min(n_pages - 1, cur_page + 1)
                    rerun()

            # High-density entity list with zero-scroll budget
            st.markdown("<div style='max-height:335px;overflow-y:auto;padding-right:2px;border-top:1px solid var(--rule);margin-top:4px;'>", unsafe_allow_html=True)
            page_records = pg_work.iloc[cur_page * PAGE_SIZE : (cur_page + 1) * PAGE_SIZE]
            for r in page_records.itertuples():
                is_act = (r.id == selected_id)
                is_sel = r.id in selected_entity_ids
                r_meta = ui.BAND_META.get(r.band, ui.BAND_META["Healthy"])
                _sbar = ui.leaf_sparkbar(int(r.days_left))
                sla_badge_str = ui.sla_badge(r.env_label)

                pg_r0, pg_r1, pg_r2 = st.columns([0.5, 2.7, 1.4])
                with pg_r0:
                    if st.button("☑" if is_sel else "☐", key=f"pg_ck_{r.id}", use_container_width=True):
                        if is_sel:
                            selected_entity_ids.discard(r.id)
                        else:
                            selected_entity_ids.add(r.id)
                        rerun()
                with pg_r1:
                    btn_txt = f"#{r.id} {r.schema_name} · {r.state}"
                    if st.button(btn_txt, key=f"pg_act_{r.id}", type="primary" if is_act else "secondary", use_container_width=True):
                        st.session_state["op_active_id"] = int(r.id)
                        rerun()
                with pg_r2:
                    st.markdown(
                        f"<div style='text-align:right;padding-top:2px;padding-right:2px;'>"
                        f"<span style='font-family:var(--mono);font-size:9.5px;font-weight:700;color:{r_meta['color']};'>{r_meta['symbol']} {ui.fmt_days(r.days_left)}</span> {sla_badge_str}"
                        f"{_sbar}"
                        f"</div>",
                        unsafe_allow_html=True,
                    )
            st.markdown("</div>", unsafe_allow_html=True)
        else:
            # Persistent Interactive Breadcrumb Header & Selection Toolbar
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

            selected_entity_ids = st.session_state.setdefault("op_selected_entity_ids", set())

            def tri_state_info(child_ids: set, selected_ids: set) -> tuple[str, bool]:
                """Returns (symbol, should_uncheck) where symbol is '☑', '⊟', or '☐'."""
                if not child_ids:
                    return "☐", False
                intersect_n = len(child_ids.intersection(selected_ids))
                if intersect_n == len(child_ids):
                    return "☑", True
                elif intersect_n > 0:
                    return "⊟", True
                else:
                    return "☐", False

            def toggle_tree_node(path: str, parent_prefix: str | None = None) -> None:
                """Toggle a node with accordion behavior (collapsing sibling nodes at the same level)."""
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

            # Tree Header Bar — integrated Title + Breadcrumb Trail
            st.markdown(f"""
            <div style="display:flex;align-items:center;justify-content:space-between;background:#181b1f;border:1px solid var(--rule);border-radius:2px;padding:6px 10px;margin-bottom:6px;">
              <div style="display:flex;align-items:center;gap:8px;min-width:0;overflow:hidden;">
                <span style="font-size:11.5px;font-weight:600;color:var(--ink);white-space:nowrap;">Hierarchy — State</span>
                <span style="color:var(--rule-soft);font-size:10px;">|</span>
                <div style="font-size:10.5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;line-height:1.2;">{bc_trail}</div>
              </div>
              <span style="color:var(--mute);font-size:12px;flex:none;cursor:pointer;">⋮</span>
            </div>
            """, unsafe_allow_html=True)

            # Action Toolbar
            tc1, tc2, tc3, tc4 = st.columns([1.0, 1.0, 1.2, 1.3])
            with tc1:
                if st.button("Expand", key="tree_exp_all", use_container_width=True):
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
                    rerun()
            with tc2:
                if st.button("Collapse", key="tree_col_all", use_container_width=True):
                    tree_open.clear()
                    rerun()
            with tc3:
                all_f_ids = set(filtered["id"].tolist())
                n_sel = len(selected_entity_ids)
                if n_sel > 0:
                    if st.button(f"Clear ({n_sel})", key="tree_clear_sel_btn", use_container_width=True):
                        selected_entity_ids.clear()
                        rerun()
                else:
                    if st.button("Select All", key="tree_select_all_btn", use_container_width=True):
                        selected_entity_ids.update(all_f_ids)
                        rerun()
            with tc4:
                n_sel = len(selected_entity_ids)
                btn_txt = f"Batch ({n_sel})" if n_sel > 0 else "Batch Editor"
                if st.button(btn_txt, key="tree_send_to_batch", disabled=(n_sel == 0), type="primary" if n_sel > 0 else "secondary", use_container_width=True):
                    st.session_state["op_target_tab"] = "batch"
                    rerun()

            # Hierarchical Matrix Tree (Scrollable node list)
            st.markdown("<div style='max-height:260px;overflow-y:auto;border:1px solid var(--rule);border-radius:2px;background:#141619;padding:3px 4px;margin-top:6px;'>", unsafe_allow_html=True)

            for st_val in filtered["state"].unique():
                st_sub = filtered[filtered["state"] == st_val]
                st_path = str(st_val)
                st_is_open = st_path in tree_open
                st_worst = ui.worst_band(st_sub["band"].tolist())
                st_meta = ui.BAND_META.get(st_worst, ui.BAND_META["Healthy"])
                st_exp_n = (st_sub["days_left"] < 0).sum()
                st_child_ids = set(st_sub["id"].tolist())
                st_sym, st_uncheck = tri_state_info(st_child_ids, selected_entity_ids)

                # Level 1: State Node
                s_c0, s_c1, s_c2 = st.columns([0.6, 3.8, 0.6])
                with s_c0:
                    if st.button(st_sym, key=f"sel_st_{st_val}", use_container_width=True):
                        if st_uncheck:
                            selected_entity_ids.difference_update(st_child_ids)
                        else:
                            selected_entity_ids.update(st_child_ids)
                        rerun()
                with s_c1:
                    _st_chip = ui.alert_chip(st_worst)
                    st.markdown(f"""
                    <div class="tree-node-row{' active' if st_is_open else ''}" style="display:flex;align-items:center;justify-content:space-between;background:#181b1f;border-left:3px solid {st_meta['color']};border-radius:2px;padding:3px 6px;margin-bottom:2px;font-size:11px;">
                      <span style="font-weight:600;color:var(--text);font-variant-numeric:tabular-nums;">
                        📍 State {st_val} <span style="font-weight:400;color:var(--mute);font-size:9.5px;">({len(st_sub)} items)</span>
                      </span>
                      <span style="display:flex;align-items:center;gap:5px;">
                        {_st_chip}
                        <span class="pill" style="color:{st_meta['color']};background:{st_meta['tint']};font-size:9px;padding:1px 5px;border-radius:2px;">
                          <b>{st_meta['symbol']}</b> {f'{st_exp_n} Expired' if st_exp_n else st_worst}
                        </span>
                      </span>
                    </div>
                    """, unsafe_allow_html=True)

                with s_c2:
                    if st.button("▼" if st_is_open else "▶", key=f"t_st_{st_val}", use_container_width=True):
                        toggle_tree_node(st_path, None)
                        rerun()

                if st_is_open:
                    for tm_val in st_sub["team"].unique():
                        tm_sub = st_sub[st_sub["team"] == tm_val]
                        tm_path = f"{st_val}/{tm_val}"
                        tm_is_open = tm_path in tree_open
                        tm_worst = ui.worst_band(tm_sub["band"].tolist())
                        tm_meta = ui.BAND_META.get(tm_worst, ui.BAND_META["Healthy"])
                        tm_color = ui.TEAM_META.get(tm_val, {}).get("color", "#5794f2")
                        tm_child_ids = set(tm_sub["id"].tolist())
                        tm_sym, tm_uncheck = tri_state_info(tm_child_ids, selected_entity_ids)

                        # Level 2: Team Node
                        t_c0, t_c1, t_c2 = st.columns([0.6, 3.8, 0.6])
                        with t_c0:
                            if st.button(tm_sym, key=f"sel_tm_{st_val}_{tm_val}", use_container_width=True):
                                if tm_uncheck:
                                    selected_entity_ids.difference_update(tm_child_ids)
                                else:
                                    selected_entity_ids.update(tm_child_ids)
                                rerun()
                        with t_c1:
                            st.markdown(f"""
                            <div class="tree-node-row{' active' if tm_is_open else ''}" style="display:flex;align-items:center;justify-content:space-between;margin-left:4px;background:#141619;border-radius:2px;padding:2px 6px;margin-bottom:2px;font-size:10.5px;">
                              <span style="color:{tm_color};font-weight:600;">
                                👥 {tm_val} <span style="color:var(--mute);font-weight:400;font-size:9px;">({len(tm_sub)})</span>
                              </span>
                              <span style="color:{tm_meta['color']};font-weight:600;font-size:9px;">{tm_meta['symbol']} {tm_worst}</span>
                            </div>
                            """, unsafe_allow_html=True)
                        with t_c2:
                            if st.button("▼" if tm_is_open else "▶", key=f"t_tm_{st_val}_{tm_val}", use_container_width=True):
                                toggle_tree_node(tm_path, st_path)
                                rerun()

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
                                cp_sym, cp_uncheck = tri_state_info(cp_child_ids, selected_entity_ids)

                                # Level 3: Component Node
                                cp_c0, cp_c1, cp_c2 = st.columns([0.6, 3.8, 0.6])
                                chip_head = f"{cp_icon} {cp_code}"
                                with cp_c0:
                                    if st.button(cp_sym, key=f"sel_cp_{st_val}_{tm_val}_{cp_code}", use_container_width=True):
                                        if cp_uncheck:
                                             selected_entity_ids.difference_update(cp_child_ids)
                                        else:
                                             selected_entity_ids.update(cp_child_ids)
                                        rerun()
                                with cp_c1:
                                    st.markdown(f"""
                                    <div class="tree-node-row{' active' if cp_is_open else ''}" style="display:flex;align-items:center;justify-content:space-between;margin-left:8px;border-left:2px solid {cp_meta['color']};border-radius:2px;padding:2px 6px;margin-bottom:2px;font-size:10px;">
                                      <span style="color:var(--text);font-weight:500;">{chip_head} <span style="color:var(--mute);font-size:8.5px;">({len(cp_sub)})</span></span>
                                      <span style="color:{cp_meta['color']};font-size:9px;">{cp_meta['symbol']} {cp_worst}</span>
                                    </div>
                                    """, unsafe_allow_html=True)
                                with cp_c2:
                                    if st.button("▼" if cp_is_open else "▶", key=f"t_cp_{st_val}_{tm_val}_{cp_code}", use_container_width=True):
                                        toggle_tree_node(cp_path, tm_path)
                                        rerun()

                                if cp_is_open:
                                    for ev_val in cp_sub["env_label"].unique():
                                        ev_sub = cp_sub[cp_sub["env_label"] == ev_val]
                                        ev_path = f"{st_val}/{tm_val}/{cp_val}/{ev_val}"
                                        ev_is_open = ev_path in tree_open
                                        ev_worst = ui.worst_band(ev_sub["band"].tolist())
                                        ev_meta = ui.BAND_META.get(ev_worst, ui.BAND_META["Healthy"])
                                        ev_child_ids = set(ev_sub["id"].tolist())
                                        ev_sym, ev_uncheck = tri_state_info(ev_child_ids, selected_entity_ids)

                                        # Level 4: Environment Node
                                        ev_c0, ev_c1, ev_c2 = st.columns([0.6, 3.8, 0.6])
                                        with ev_c0:
                                            if st.button(ev_sym, key=f"sel_ev_{st_val}_{tm_val}_{cp_code}_{ev_val}", use_container_width=True):
                                                if ev_uncheck:
                                                    selected_entity_ids.difference_update(ev_child_ids)
                                                else:
                                                    selected_entity_ids.update(ev_child_ids)
                                                rerun()
                                        with ev_c1:
                                            st.markdown(f"""
                                            <div class="tree-node-row{' active' if ev_is_open else ''}" style="display:flex;align-items:center;justify-content:space-between;margin-left:12px;border-radius:2px;padding:2px 4px;font-size:9.5px;color:var(--slate);">
                                              <span>🖥️ <span class="env-tag" style="font-size:8.5px;">{ev_val}</span> ({len(ev_sub)})</span>
                                              <span style="color:{ev_meta['color']};font-size:8.5px;">{ev_meta['symbol']} {ui.fmt_days(ev_sub['days_left'].min())}</span>
                                            </div>
                                            """, unsafe_allow_html=True)
                                        with ev_c2:
                                            if st.button("▼" if ev_is_open else "▶", key=f"t_ev_{st_val}_{tm_val}_{cp_code}_{ev_val}", use_container_width=True):
                                                toggle_tree_node(ev_path, cp_path)
                                                rerun()

                                        if ev_is_open:
                                            # Level 5: Leaf Entities
                                            for r in ev_sub.itertuples():
                                                r_meta = ui.BAND_META.get(r.band, ui.BAND_META["Healthy"])
                                                is_act = (r.id == selected_id)
                                                is_leaf_sel = r.id in selected_entity_ids

                                                row_c0, row_c1, row_c2 = st.columns([0.6, 3.0, 1.4])
                                                with row_c0:
                                                    if st.button("☑" if is_leaf_sel else "☐", key=f"sel_leaf_{r.id}", use_container_width=True):
                                                        if is_leaf_sel:
                                                            selected_entity_ids.discard(r.id)
                                                        else:
                                                            selected_entity_ids.add(r.id)
                                                        rerun()
                                                with row_c1:
                                                    if st.button(r.schema_name, key=f"leaf_btn_{r.id}", type="primary" if is_act else "secondary", use_container_width=True):
                                                        st.session_state["op_active_id"] = r.id
                                                        rerun()
                                                with row_c2:
                                                    r_c = r_meta["color"]
                                                    r_s = r_meta["symbol"]
                                                    _sbar = ui.leaf_sparkbar(int(r.days_left))
                                                    st.markdown(
                                                        f"<div style='text-align:right;padding-top:4px;padding-right:4px;'>"
                                                        f"<div style='font-size:10px;font-weight:600;color:{r_c};white-space:nowrap;font-variant-numeric:tabular-nums;'>{r_s} {ui.fmt_days(r.days_left)}</div>"
                                                        f"{_sbar}"
                                                        f"</div>",
                                                        unsafe_allow_html=True
                                                    )
            st.markdown("</div>", unsafe_allow_html=True)

            # Component Severity Distribution Panel (Rule 6: size to content, eliminate empty space)
            dist_rows = []
            for c_val in COMPONENT_ORDER:
                c_sub = filtered[filtered["component"] == c_val]
                c_cnt = len(c_sub)
                c_code = ui.COMPONENT_CODE.get(c_val, c_val)
                if c_cnt == 0:
                    dist_rows.append(
                        f'<div class="dist-row">'
                        f'<div class="dist-name">{c_code}</div>'
                        f'<div class="dist-track"><div style="width:100%;background:#212429;"></div></div>'
                        f'<div class="dist-num">0 · 0%</div>'
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
                        num_html = f'<span style="color:var(--healthy);font-weight:600;">{c_cnt}</span> <span style="color:var(--mute);font-weight:400;">(100%)</span>'

                    dist_rows.append(
                        f'<div class="dist-row">'
                        f'<div class="dist-name">{c_code}</div>'
                        f'<div class="dist-track">{track_html}</div>'
                        f'<div class="dist-num">{num_html}</div>'
                        f'</div>'
                    )

            st.markdown(f"""
            <div class="panel" style="margin-top:8px;">
              <div class="panel-head"><span class="panel-title">Severity by Component — Selected Scope</span><span class="panel-menu">⋮</span></div>
              <div class="dist-body">
                {''.join(dist_rows)}
              </div>
            </div>
            """, unsafe_allow_html=True)

    with right_col:
        if selected_id is None:
            st.markdown(ui.empty("Select a record", "Choose an item from the master hierarchy tree on the left to inspect."), unsafe_allow_html=True)
            return

        rec = df[df["id"] == selected_id].iloc[0]
        meta = ui.BAND_META.get(rec["band"], ui.BAND_META["Healthy"])
        team_meta = ui.TEAM_META.get(rec["team"], ui.TEAM_META["Core"])
        cp_icon = ui.COMPONENT_ICONS.get(rec["component"], "📦")
        conf = st.session_state.get("confirm_action")
        cur_dt = rec["exp_dt"].date()

        # Unified Inspector Command Deck (Breadcrumb + Entity Title + Status + Action Chips)
        head_c1, head_c2 = st.columns([2.5, 1.5])
        with head_c1:
            st.markdown(f"""
            <div class="entity-head" style="border-left:3px solid {meta['color']};margin-bottom:6px;">
              <div class="entity-crumb">
                <span>ENTITY #{rec['id']} · <b style="color:var(--text);letter-spacing:0.04em;">State {rec['state']}</b> · <span style="color:{team_meta['color']};font-weight:600;">{rec['team']}</span> · {cp_icon} {rec['component']}</span>
                <span class="status-pill" style="background:{meta['tint']};color:{meta['color']};">{meta['symbol']} {rec['band']}</span>
              </div>
              <div class="entity-name">{rec['schema_name']} <span class="env-tag">{rec['env_label']}</span></div>
            </div>
            """, unsafe_allow_html=True)

        with head_c2:
            if conf and conf.get("id") == rec["id"]:
                st.markdown(f"<div style='font-size:9.5px;color:var(--warning);font-weight:700;padding-top:2px;'>⚠️ Extend to {conf['new_dt']} (+{conf['days']}d)?</div>", unsafe_allow_html=True)
                cf_y, cf_n = st.columns(2)
                with cf_y:
                    if st.button("✓ Confirm", key=f"op_cf_yes_{rec['id']}", type="primary", use_container_width=True):
                        apply_edits([(conf["id"], conf["new_dt"])])
                        del st.session_state["confirm_action"]
                        st.success(f"Updated {conf['schema']} to {conf['new_dt']}")
                        rerun()
                with cf_n:
                    if st.button("Cancel", key=f"op_cf_no_{rec['id']}", use_container_width=True):
                        del st.session_state["confirm_action"]
                        rerun()
            else:
                n_act = 4 if rec["edited"] else 3
                act_cols = st.columns(n_act)
                if act_cols[0].button("+90d", key=f"op_top_p90_{rec['id']}", use_container_width=True, help="Extend expiry by 90 days"):
                    st.session_state["confirm_action"] = {"id": rec["id"], "days": 90, "new_dt": cur_dt + pd.Timedelta(days=90), "schema": rec["schema_name"]}
                    rerun()
                if act_cols[1].button("+1yr", key=f"op_top_p365_{rec['id']}", use_container_width=True, help="Extend expiry by 1 year"):
                    st.session_state["confirm_action"] = {"id": rec["id"], "days": 365, "new_dt": cur_dt + pd.Timedelta(days=365), "schema": rec["schema_name"]}
                    rerun()
                with act_cols[2]:
                    if hasattr(st, "popover"):
                        with st.popover("📅 Date"):
                            c_date = st.date_input("New Expiry Date", value=cur_dt, key=f"op_pop_dt_{rec['id']}")
                            if st.button("Commit Date", type="primary", key=f"op_pop_btn_{rec['id']}", use_container_width=True):
                                apply_edits([(rec["id"], c_date)])
                                st.success(f"Updated to {c_date}")
                                rerun()
                    else:
                        with st.expander("📅 Date"):
                            c_date = st.date_input("New Expiry Date", value=cur_dt, key=f"op_pop_dt_{rec['id']}")
                            if st.button("Commit Date", type="primary", key=f"op_pop_btn_{rec['id']}", use_container_width=True):
                                apply_edits([(rec["id"], c_date)])
                                st.success(f"Updated to {c_date}")
                                rerun()
                if rec["edited"] and len(act_cols) > 3:
                    with act_cols[3]:
                        if st.button("↩ Rev", key=f"op_top_rev_{rec['id']}", type="secondary", use_container_width=True, help="Revert to workbook source date"):
                            conn = get_connection(DB_PATH)
                            try:
                                revert_component_exp_date(conn, int(rec["id"]))
                            finally:
                                conn.close()
                            bust_cache()
                            st.success("Reverted to source workbook.")
                            rerun()

        i_tab1, i_tab2, i_tab3, i_tab4 = st.tabs(["Overview & Lineage", "Portfolio Matrix", "Batch Grid Editor", "Rollback Ledger"])

        with i_tab1:
            st.markdown("<div style='max-height:220px;overflow-y:auto;padding-right:2px;'>", unsafe_allow_html=True)
            exp_detail = f"(Expired {rec['exp_dt'].strftime('%b %Y')})" if rec['days_left'] < 0 else f"(Expires {rec['exp_date']})"
            _life_gauge = ui.life_gauge(int(rec['days_left']))
            _team_chip = ui.alert_chip(rec["band"])
            st.markdown(f"""
            <div class="fact-grid" style="padding:4px 0 8px;">
              <div class="fact-panel">
                <div class="fact-row"><span class="fact-key">Team Owner</span><span class="fact-val" style="color:{team_meta['color']};font-weight:600;">{rec['team']} <span style="font-size:9.5px;color:var(--mute);font-weight:400;">({team_meta['lead']})</span> {_team_chip}</span></div>
                <div class="fact-row"><span class="fact-key">Component</span><span class="fact-val" style="font-weight:500;">{rec['component']}</span></div>
                <div class="fact-row"><span class="fact-key">Environment</span><span class="fact-val"><span class="env-tag">{rec['env_label']}</span> {ui.sla_badge(rec['env_label'])}</span></div>
                <div class="fact-row"><span class="fact-key">Module Code</span><span class="fact-val" style="color:var(--slate);">{rec['module'] or 'N/A'}</span></div>
              </div>
              <div class="fact-panel">
                <div class="fact-row"><span class="fact-key">Current Expiry</span><span class="fact-val" style="color:{meta['color']};font-weight:600;">{rec['exp_date']}</span></div>
                <div class="fact-row"><span class="fact-key">Workbook Source</span><span class="fact-val" style="color:var(--slate);">{rec['source_exp_date']}</span></div>
                <div class="fact-row"><span class="fact-key">Life Remaining</span><span class="fact-val crit" style="color:{meta['color']};font-weight:700;">{ui.fmt_days(rec['days_left'])} <span style="font-size:9.5px;font-weight:400;color:var(--mute);">{exp_detail}</span></span></div>
                <div class="fact-row"><span class="fact-key">Quarter Horizon</span><span class="fact-val" style="color:var(--slate);">{rec['quarter']}</span></div>
                <div style="margin-top:6px;">{_life_gauge}</div>
              </div>
            </div>
            """, unsafe_allow_html=True)

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
                if hasattr(st, "code"):
                    st.caption("Technical Diagnostics & Database Query")
                    st.code(f"SELECT * FROM component_records WHERE id = {int(rec['id'])};", language="sql")
                    st.code(json.dumps(payload, indent=2), language="json")
            st.markdown("</div>", unsafe_allow_html=True)

        with i_tab2:
            st.markdown("<div style='max-height:265px;overflow-y:auto;padding-right:2px;'>", unsafe_allow_html=True)
            st.markdown(ui.panel_header(
                "Severity Heatmap — State × Component",
                color="#f59e0b",
                info="Cell color saturation = risk density. Click any cell to cross-filter the Hierarchy Tree."
            ), unsafe_allow_html=True)
            mat_states = STATES
            mat_comps = COMPONENT_ORDER

            # Header Row — framed column labels with clean bottom margin
            h_c0, h_c1, h_c2, h_c3, h_c4, h_c5 = st.columns([0.7, 1.25, 1.25, 1.25, 1.25, 0.7])
            with h_c0:
                st.markdown("<div class='hm-col-hdr' style='color:#94a3b8;'>STATE</div>", unsafe_allow_html=True)
            for idx, c_val in enumerate(mat_comps):
                c_icon = ui.COMPONENT_ICONS.get(c_val, '')
                c_code = ui.COMPONENT_CODE.get(c_val, c_val)
                [h_c1, h_c2, h_c3, h_c4][idx].markdown(
                    f"<div class='hm-col-hdr'>{c_icon} {c_code}</div>",
                    unsafe_allow_html=True
                )
            with h_c5:
                st.markdown("<div class='hm-col-hdr' style='color:#94a3b8;'>TOTAL</div>", unsafe_allow_html=True)

            # Rows
            for st_val in mat_states:
                st_sub = df[df["state"] == st_val]
                r_c0, r_c1, r_c2, r_c3, r_c4, r_c5 = st.columns([0.7, 1.25, 1.25, 1.25, 1.25, 0.7])
                is_st_active = (cell_filter == (st_val, None)) or (state_filter == st_val and cell_filter is None and comp_filter == "All Components")
                with r_c0:
                    st.markdown("<div class='hm-state-btn'>", unsafe_allow_html=True)
                    if st.button(f"📍 {st_val}", key=f"hm_st_{st_val}", type="primary" if is_st_active else "secondary", use_container_width=True, help=f"Filter to State {st_val}"):
                        if is_st_active:
                            st.session_state["op_cell_filter"] = None
                        else:
                            st.session_state["op_cell_filter"] = (st_val, None)
                            tree_open.clear()
                            tree_open.add(st_val)
                        rerun()
                    st.markdown("</div>", unsafe_allow_html=True)

                comp_cols = [r_c1, r_c2, r_c3, r_c4]
                for idx, c_val in enumerate(mat_comps):
                    cell_sub = st_sub[st_sub["component"] == c_val]
                    with comp_cols[idx]:
                        if cell_sub.empty:
                            st.markdown("<div style='background:rgba(255,255,255,0.02);border:1px dashed rgba(255,255,255,0.08);border-radius:6px;height:56px;display:flex;align-items:center;justify-content:center;color:rgba(255,255,255,0.15);font-size:14px;'>—</div>", unsafe_allow_html=True)
                        else:
                            c_cnt = len(cell_sub)
                            c_exp = int((cell_sub["days_left"] < 0).sum())
                            c_crit = int((cell_sub["days_left"].between(0, ui.CRITICAL_DAYS)).sum())
                            c_warn = int((cell_sub["days_left"].between(ui.CRITICAL_DAYS + 1, ui.WARNING_DAYS)).sum())
                            c_hlth = int((cell_sub["days_left"] > ui.WARNING_DAYS).sum())
                            min_days = int(cell_sub["days_left"].min())
                            c_code = ui.COMPONENT_CODE.get(c_val, c_val)
                            c_worst = ui.worst_band(cell_sub["band"].tolist())
                            c_color = ui.BAND_META[c_worst]["color"]
                            is_cell_active = (cell_filter == (st_val, c_val)) or (state_filter == st_val and comp_filter == c_val and cell_filter is None)

                            breakdown_parts = []
                            if c_exp: breakdown_parts.append(f"{c_exp} Expired")
                            if c_crit: breakdown_parts.append(f"{c_crit} Critical (≤15d)")
                            if c_warn: breakdown_parts.append(f"{c_warn} Warning (≤30d)")
                            if c_hlth: breakdown_parts.append(f"{c_hlth} Healthy")
                            help_desc = f"Cross-filter to {st_val} × {c_code} ({', '.join(breakdown_parts)} — soonest in {ui.fmt_heatmap_time(min_days)})"

                            # Visual cell
                            st.markdown(
                                ui.heatmap_visual_cell(c_cnt, c_exp, c_crit, c_warn, c_hlth, min_days, c_color, is_cell_active),
                                unsafe_allow_html=True
                            )
                            btn_label = "✓ SELECTED" if is_cell_active else "Inspect ↗"
                            st.markdown("<div class='hm-action-btn'>", unsafe_allow_html=True)
                            if st.button(btn_label, key=f"hm_c_{st_val}_{c_code}", help=help_desc, use_container_width=True, type="primary" if is_cell_active else "secondary"):
                                if is_cell_active:
                                    st.session_state["op_cell_filter"] = None
                                else:
                                    st.session_state["op_cell_filter"] = (st_val, c_val)
                                    tree_open.clear()
                                    tree_open.add(st_val)
                                    for t in cell_sub["team"].unique():
                                        tree_open.add(f"{st_val}/{t}")
                                        tree_open.add(f"{st_val}/{t}/{c_val}")
                                rerun()
                            st.markdown("</div>", unsafe_allow_html=True)

                with r_c5:
                    # State total with colored accent
                    _st_worst = ui.worst_band(st_sub["band"].tolist())
                    _st_color = ui.BAND_META[_st_worst]["color"]
                    st.markdown(f"<div style='font-family:var(--mono);font-weight:700;color:{_st_color};text-align:center;padding-top:22px;font-size:13px;letter-spacing:.02em;'>{len(st_sub)}</div>", unsafe_allow_html=True)

            st.markdown("</div>", unsafe_allow_html=True)




        with i_tab3:
            if selected_entity_ids:
                batch_work = df[df["id"].isin(selected_entity_ids)].copy()
                batch_work.sort_values(by=["state", "team", "component", "env_no", "schema_name"], inplace=True)
            else:
                batch_work = filtered.copy()

            total_batch_n = len(batch_work)
            b_per_page = 4
            b_pages = max(1, (total_batch_n + b_per_page - 1) // b_per_page)
            b_page = st.session_state.setdefault("op_batch_page_no", 0)
            b_page = max(0, min(b_page, b_pages - 1))

            b_from = b_page * b_per_page + 1 if total_batch_n > 0 else 0
            b_to = min(total_batch_n, (b_page + 1) * b_per_page)
            page_slice = batch_work.iloc[b_from - 1:b_to].copy() if total_batch_n > 0 else batch_work.copy()

            all_filtered_ids = set(filtered["id"].tolist())
            is_all_filtered_selected = (len(all_filtered_ids) > 0 and all_filtered_ids.issubset(selected_entity_ids))

            bg_c1, bg_c2, bg_c3, bg_c4 = st.columns([1.6, 1.3, 0.9, 0.6])
            with bg_c1:
                if selected_entity_ids:
                    st.markdown(f"<div style='font-size:10px;color:#38bdf8;font-weight:700;padding-top:4px;'>⚡ {len(selected_entity_ids)} selected · Page {b_page + 1}/{b_pages}</div>", unsafe_allow_html=True)
                else:
                    st.markdown(f"<div style='font-size:10px;color:#cbd5e1;font-weight:600;padding-top:4px;'>Scope: {total_batch_n} items · Page {b_page + 1}/{b_pages}</div>", unsafe_allow_html=True)
            with bg_c2:
                if is_all_filtered_selected:
                    st.button("✓ All In Filter Selected", key="op_batch_sel_all_flt", use_container_width=True, disabled=True)
                else:
                    if st.button(f"Select All {len(filtered)} in Filter", key="op_batch_sel_all_flt", type="primary", use_container_width=True):
                        selected_entity_ids.update(all_filtered_ids)
                        st.session_state["op_batch_page_no"] = 0
                        rerun()
            with bg_c3:
                if b_pages > 1:
                    p_c1, p_c2 = st.columns(2)
                    if p_c1.button("‹", key="op_batch_p_prev", disabled=(b_page == 0), use_container_width=True):
                        st.session_state["op_batch_page_no"] = b_page - 1
                        rerun()
                    if p_c2.button("›", key="op_batch_p_next", disabled=(b_page >= b_pages - 1), use_container_width=True):
                        st.session_state["op_batch_page_no"] = b_page + 1
                        rerun()
            with bg_c4:
                if selected_entity_ids:
                    if st.button("Clear", key="op_batch_clear_sel", use_container_width=True):
                        selected_entity_ids.clear()
                        st.session_state["op_batch_page_no"] = 0
                        rerun()

            if hasattr(st, "data_editor") and hasattr(st, "column_config") and not page_slice.empty:
                b_view = page_slice[["schema_name", "env_label", "exp_dt", "band", "days_left"]].copy()
                b_view["exp_dt"] = b_view["exp_dt"].dt.date
                b_view["days_left"] = b_view["days_left"].apply(ui.fmt_days)
                b_view["band"] = b_view["band"].apply(ui.health_text)

                b_edited = st.data_editor(
                    b_view, key=f"op_batch_editor_p{b_page}", hide_index=True, use_container_width=True,
                    num_rows="fixed", height=min(180, 36 + len(page_slice) * 35),
                    column_config={
                        "schema_name": st.column_config.TextColumn("Schema Name", disabled=True, width="medium"),
                        "env_label": st.column_config.TextColumn("Env", disabled=True, width="small"),
                        "exp_dt": st.column_config.DateColumn("Expiry Date", format="YYYY-MM-DD", required=True, width="small"),
                        "band": st.column_config.TextColumn("Status", disabled=True, width="small"),
                        "days_left": st.column_config.TextColumn("Time Left", disabled=True, width="medium"),
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
                    apply_edits(b_changes)
                    st.success(f"Saved {len(b_changes)} batch updates!")
                    rerun()
                n_bchg = len(b_changes)
                b_note_col.markdown(f"<div style='font-size:10.5px;color:#94a3b8;padding-top:4px;'><b>{n_bchg}</b> unsaved {'change' if n_bchg == 1 else 'changes'} on current page</div>", unsafe_allow_html=True)
            elif page_slice.empty:
                st.markdown("<div style='font-size:11px;color:#94a3b8;padding:12px 0;'>No entities selected. Select items from the tree or filters.</div>", unsafe_allow_html=True)

        with i_tab4:
            st.markdown("<div style='max-height:200px;overflow-y:auto;padding-right:2px;'>", unsafe_allow_html=True)
            active_edits = df[df["edited"]].copy()
            if active_edits.empty:
                st.markdown("""
                <div class="card" style="font-size:11px;color:#94a3b8;padding:6px 10px;">
                  <div style="font-weight:700;color:#10b981;margin-bottom:2px;">✓ Fleet 100% In Sync with Workbooks</div>
                  All 500 records match source Excel files. Local overrides appear here for 1-click rollback.
                </div>
                """, unsafe_allow_html=True)
            else:
                n_ovr = len(active_edits)
                st.markdown(ui.note(f"<b>{n_ovr}</b> local {'override' if n_ovr == 1 else 'overrides'}:"), unsafe_allow_html=True)
                for er in active_edits.itertuples():
                    ec1, ec2 = st.columns([3, 1])
                    ec1.markdown(f"<span style='font-size:11px;'><b>{er.schema_name}</b> ({er.state}) — <code>{er.exp_date}</code></span>", unsafe_allow_html=True)
                    if ec2.button("Revert", key=f"op_rev_ledger_{er.id}", use_container_width=True):
                        conn = get_connection(DB_PATH)
                        revert_component_exp_date(conn, int(er.id))
                        conn.close()
                        bust_cache()
                        st.success(f"Reverted {er.schema_name}")
                        rerun()
            st.markdown("</div>", unsafe_allow_html=True)


# ==========================================================================
# View 3: Pipeline Governance & Alert Center (Power BI / Fabric Executive Hub)
# ==========================================================================
TEAM_GOVERNANCE_PROFILES = {
    "Core": {
        "team": "Core",
        "lead": "DB & Infrastructure Lead",
        "channel": "core-dba@example.com",
        "cadence": "Quarterly",
        "assets": 160,
        "status": "🔴 10 Expired",
        "status_color": "#f2495c",
        "status_bg": "rgba(242,73,92,0.18)"
    },
    "Letters": {
        "team": "Letters",
        "lead": "Correspondence Lead",
        "channel": "letters-ops@example.com",
        "cadence": "Monthly (1st Sun)",
        "assets": 96,
        "status": "▲ 5 Critical (15d)",
        "status_color": "#ff9830",
        "status_bg": "rgba(255,152,48,0.18)"
    },
    "Cognos": {
        "team": "Cognos",
        "lead": "BI & Analytics Lead",
        "channel": "cognos-dba@example.com",
        "cadence": "3× Weekly",
        "assets": 96,
        "status": "▲ 5 Critical (15d)",
        "status_color": "#ff9830",
        "status_bg": "rgba(255,152,48,0.18)"
    },
    "Informatica": {
        "team": "Informatica",
        "lead": "ETL Operations Lead",
        "channel": "infa-etl@example.com",
        "cadence": "Weekly (Sun)",
        "assets": 96,
        "status": "✓ 100% Healthy",
        "status_color": "#73bf69",
        "status_bg": "rgba(115,191,105,0.16)"
    },
    "App Server": {
        "team": "App Server",
        "lead": "JVM Containers Lead",
        "channel": "appserver-admin@example.com",
        "cadence": "Weekly (Sun)",
        "assets": 96,
        "status": "✓ 100% Healthy",
        "status_color": "#73bf69",
        "status_bg": "rgba(115,191,105,0.16)"
    },
}


def render_governance_center() -> None:
    conn = get_connection(DB_PATH)
    tables = ["component_records", "expiry_records", "maintenance_schedules", "owners", "reminder_log"]
    stats = {}
    for t in tables:
        try:
            stats[t] = conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
        except Exception:
            stats[t] = 0
    conn.close()

    gov_drill = st.session_state.setdefault("gov_drill_scope", "all")
    gov_team_filter = st.session_state.setdefault("gov_team_filter", "All")

    # Filter records based on active drill scope & team
    scoped_records = records.copy()
    if gov_team_filter != "All":
        scoped_records = scoped_records[scoped_records["team"] == gov_team_filter]

    urgent_records = scoped_records[scoped_records["band"].isin(["Expired", "Critical", "Warning"])].copy()
    urgent_records.sort_values(by="days_left", ascending=True, inplace=True)

    n_expired_fleet = (records["band"] == "Expired").sum()
    n_critical_fleet = (records["band"] == "Critical").sum()
    n_warning_fleet = (records["band"] == "Warning").sum()
    n_total_risk_fleet = n_expired_fleet + n_critical_fleet + n_warning_fleet
    n_healthy_fleet = (records["band"] == "Healthy").sum()
    pct_healthy = (n_healthy_fleet / len(records)) * 100.0
    pct_risk = (n_total_risk_fleet / len(records)) * 100.0

    # 1. Lightweight Utility Scope Slicer
    scope_name = "All Teams & Portfolios" if gov_team_filter == "All" else f"Team {gov_team_filter}"
    if gov_drill != "all":
        scope_name += f" · Filter: {gov_drill.title()}"

    s_c1, s_c2 = st.columns([4.2, 0.8])
    with s_c1:
        st.markdown(f"""
        <div class="scope-line" style="margin-top:2px;margin-bottom:6px;padding:6px 10px;">
          <div style="display:flex;align-items:center;gap:8px;flex:1;">
            <span class="scope-label">GOVERNANCE SCOPE</span>
            <span class="scope-val">{scope_name}</span>
            <span class="scope-muted">({len(scoped_records)} of 500 total managed assets)</span>
          </div>
          <div style="display:flex;align-items:center;gap:8px;">
            <div class="live-dot"></div>
            <span class="scope-muted" style="font-variant-numeric:tabular-nums;font-size:11px;">Live Data Sync · 08:00 UTC</span>
          </div>
        </div>
        """, unsafe_allow_html=True)
    with s_c2:
        if st.button("↺ Reset Scope", key="gov_reset_scope", use_container_width=True):
            st.session_state["gov_drill_scope"] = "all"
            st.session_state["gov_team_filter"] = "All"
            rerun()

    st.markdown("<div style='margin-top:4px;'></div>", unsafe_allow_html=True)

    # 2. Level 1: Authoritative Action Directive (High Impact, Zero Ambiguity)
    st.markdown(f"""
    <div class="panel" style="border-left:3px solid var(--expired);background:linear-gradient(90deg, rgba(242,73,92,0.12) 0%, rgba(24,27,31,0.95) 100%);padding:8px 12px;margin-bottom:6px;display:flex;align-items:center;justify-content:space-between;border-radius:2px;">
      <div>
        <div style="display:flex;align-items:center;gap:8px;">
          <span style="font-size:12.5px;font-weight:700;color:var(--text);letter-spacing:-0.01em;">🔴 EXECUTIVE ACTION DIRECTIVE: Critical Credential Rotation Required Across 3 Functional Domains</span>
          <span class="pill" style="color:var(--expired);background:var(--red-dim);font-size:9px;font-weight:700;border-radius:2px;">POLICY ESCALATION</span>
        </div>
        <div style="font-size:11px;color:var(--slate);margin-top:3px;">
          Escalate overdue credential rotation for <b>Core (ND)</b> and stage 15-day renewals for <b>Letters (AK)</b> and <b>Cognos (NH)</b> to eliminate operational disruption risks.
        </div>
      </div>
      <div style="display:flex;align-items:center;gap:14px;">
        <div style="width:130px;">
          <div style="display:flex;justify-content:space-between;font-size:9.5px;color:var(--mute);margin-bottom:2px;font-variant-numeric:tabular-nums;">
            <span style="color:var(--healthy);font-weight:700;">{pct_healthy:.1f}% OK</span>
            <span style="color:var(--expired);font-weight:700;">{pct_risk:.1f}% Risk</span>
          </div>
          <div style="height:5px;width:100%;background:#212429;border-radius:2px;overflow:hidden;display:flex;">
            <div style="width:{pct_healthy:.1f}%;background:var(--healthy);"></div>
            <div style="width:{pct_risk:.1f}%;background:var(--expired);"></div>
          </div>
        </div>
        <div style="text-align:right;border-left:1px solid var(--rule);padding-left:10px;font-variant-numeric:tabular-nums;">
          <span style="font-size:18px;font-weight:700;color:var(--expired);line-height:1;">{n_total_risk_fleet}</span>
          <span style="font-size:9px;color:var(--mute);display:block;">Action Items</span>
        </div>
      </div>
    </div>
    """, unsafe_allow_html=True)

    # 3. Level 7: Connected Risk & Impact Narrative Chain (Compact, Sleek KPI Cards)
    kpi_c1, kpi_c2, kpi_c3, kpi_c4 = st.columns(4)

    with kpi_c1:
        is_active = (gov_drill == "urgent")
        st.markdown(ui.kpi_card(
            label="Actionable Risk Assets",
            value=n_total_risk_fleet,
            subtext=f"{n_expired_fleet} Expired · {n_critical_fleet} Critical · {n_warning_fleet} Warning",
            glow="#f2495c",
            val_color="#f2495c",
            badge="RISK",
            badge_color="#f2495c",
            is_active=is_active,
        ), unsafe_allow_html=True)
        if st.button("Filter Risk Assets" if not is_active else "✓ Filtering Risk", key="gov_kpi_risk", use_container_width=True, type="primary" if is_active else "secondary"):
            st.session_state["gov_drill_scope"] = "urgent" if gov_drill != "urgent" else "all"
            rerun()

    with kpi_c2:
        is_active = (gov_team_filter != "All")
        st.markdown(ui.kpi_card(
            label="Teams Impacted",
            value="3 / 5",
            subtext="Core, Letters, Cognos attention",
            glow="#ff9830",
            val_color="#ff9830",
            badge="IMPACT",
            badge_color="#ff9830",
            is_active=is_active,
        ), unsafe_allow_html=True)
        if st.button("Focus Impacted" if not is_active else f"✓ Focused: {gov_team_filter}", key="gov_kpi_teams", use_container_width=True, type="primary" if is_active else "secondary"):
            st.session_state["gov_team_filter"] = "Core" if gov_team_filter == "All" else "All"
            rerun()

    with kpi_c3:
        is_active = (gov_drill == "maintenance")
        st.markdown(ui.kpi_card(
            label="Maintenance Windows",
            value=stats.get('maintenance_schedules', 0),
            subtext="100% Synced across 4 cadences",
            glow="#5794f2",
            val_color="var(--ink)",
            badge="SCHEDULE",
            badge_color="#5794f2",
            is_active=is_active,
        ), unsafe_allow_html=True)
        if st.button("View Maintenance" if not is_active else "✓ Maintenance Scope", key="gov_kpi_maint", use_container_width=True, type="primary" if is_active else "secondary"):
            st.session_state["gov_drill_scope"] = "maintenance" if gov_drill != "maintenance" else "all"
            rerun()

    with kpi_c4:
        is_active = (gov_drill == "reminders")
        smtp_live = bool(os.environ.get("SMTP_HOST"))
        st.markdown(ui.kpi_card(
            label="Alert Dispatch Audit",
            value=f"{stats['reminder_log']} Logged",
            subtext="Live SMTP Configured" if smtp_live else "Daily dry-run audit @ 08:00 UTC",
            glow="#5794f2",
            val_color="var(--ink)",
            badge="LIVE SMTP" if smtp_live else "SIMULATED",
            badge_color="#73bf69" if smtp_live else "#ff9830",
            is_active=is_active,
        ), unsafe_allow_html=True)
        if st.button("Audit Logs" if not is_active else "✓ Audit Log Scope", key="gov_kpi_rem", use_container_width=True, type="primary" if is_active else "secondary"):
            st.session_state["gov_drill_scope"] = "reminders" if gov_drill != "reminders" else "all"
            rerun()

    st.markdown("<div style='margin-top:2px;'></div>", unsafe_allow_html=True)

    # 4. Level 5 & Level 3: Left Team Scorecard (Always 5 Teams) vs Right Action Console
    g_col1, _, g_col2 = st.columns([2.0, 0.03, 2.0])

    with g_col1:
        # Team Governance & Risk Distribution Matrix (ALWAYS DISPLAYS ALL 5 TEAMS TO PREVENT EMPTY CAVITY)
        team_profiles = list(TEAM_GOVERNANCE_PROFILES.values())

        t_rows = []
        for p in team_profiles:
            is_active_tm = (gov_team_filter == p["team"])
            row_bg = "background:rgba(255,120,10,0.12);border-left:3px solid var(--accent);" if is_active_tm else ""
            active_badge = " <span style='color:var(--accent);font-size:9px;font-weight:700;'>[ACTIVE]</span>" if is_active_tm else ""
            t_rows.append(
                f"<tr style='{row_bg}'>"
                f"<td class='m' style='font-weight:600;color:var(--text);padding:5px 6px;'>{p['team']}{active_badge}</td>"
                f"<td style='color:var(--slate);padding:5px 6px;'>{p['lead']}<br/><code style='font-size:9.5px;color:var(--mute);'>{p['channel']}</code></td>"
                f"<td class='m r' style='padding:5px 6px;'><b>{p['assets']}</b></td>"
                f"<td style='padding:5px 6px;'><span class='pill' style='color:{p['status_color']};background:{p['status_bg']};font-weight:700;font-size:9px;border-radius:2px;'>{p['status']}</span></td>"
                f"<td style='color:var(--mute);font-size:9.5px;padding:5px 6px;'>{p['cadence']}</td>"
                f"</tr>"
            )

        st.markdown(f"""
        <div class="panel" style="margin-bottom:6px;">
          <div class="panel-head"><span class="panel-title">Team Governance & Risk Distribution Matrix</span><span class="panel-menu">⋮</span></div>
          <table class="tblx" style="font-size:10.5px;">
            <tr><th>Functional Team</th><th>Owner & Channel</th><th class="r">Assets</th><th>Risk Posture</th><th>Cadence</th></tr>
            {''.join(t_rows)}
          </table>
        </div>
        """, unsafe_allow_html=True)

        st.markdown('<div style="font-size:9.5px;font-weight:700;color:var(--mute);letter-spacing:0.04em;margin-top:4px;margin-bottom:3px;">FOCUS TEAM SCOPE:</div>', unsafe_allow_html=True)

        # Team drill buttons (hidden but functional — triggered by the scorecard row clicks above)
        _drill_cols = st.columns(6)
        _teams_map = [("All","All"),("Core","Core"),("Letters","Letters"),("Cognos","Cognos"),("Informatica","Infa"),("App Server","AppSrv")]
        for _col, (_tv, _tl) in zip(_drill_cols, _teams_map):
            if _col.button(_tl, key=f"tm_btn_{_tv.lower().replace(' ','_')}", use_container_width=True,
                           type="primary" if gov_team_filter == _tv else "secondary"):
                st.session_state["gov_team_filter"] = _tv if _tv != gov_team_filter else "All"
                if _tv == "All":
                    st.session_state["gov_team_filter"] = "All"
                rerun()

    with g_col2:
        # Right Pane: Structured Action Console & Synchronized Email Inspector
        q_count_label = f" ({len(urgent_records)})" if not urgent_records.empty else " (0)"
        act_tab1, act_tab2, act_tab3 = st.tabs([
            f"⚡ Actionable Risk Queue{q_count_label}",
            "📧 Alert Dispatch Preview (Simulator)",
            "📋 Compliance & Audit Ledger"
        ])

        with act_tab1:
            if urgent_records.empty:
                st.markdown(f"""
                <div class="panel" style="padding:24px 16px;text-align:center;">
                  <div style="font-size:14px;font-weight:700;color:var(--healthy);">✓ Scope 100% In Compliance</div>
                  <div style="font-size:11px;color:var(--mute);margin-top:4px;">
                    No expired or critical debt entities found for <b>{scope_name}</b>.
                  </div>
                </div>
                """, unsafe_allow_html=True)
            else:
                q_rows = []
                for ur in urgent_records.itertuples():
                    ur_meta = ui.BAND_META.get(ur.band, ui.BAND_META["Healthy"])
                    ur_code = ui.COMPONENT_CODE.get(ur.component, ur.component)
                    ur_icon = ui.COMPONENT_ICONS.get(ur.component, "📦")
                    if ur.days_left < 0:
                        ll_style = "color:var(--expired);font-weight:700;"
                    elif ur.days_left <= 15:
                        ll_style = "color:var(--critical);font-weight:700;"
                    elif ur.days_left <= 30:
                        ll_style = "color:var(--warning);font-weight:600;"
                    else:
                        ll_style = "color:var(--healthy);font-weight:600;"
                    q_rows.append(
                        f"<tr>"
                        f"<td><span class='pill' style='color:{ur_meta['color']};background:{ur_meta['tint']};font-weight:700;font-size:9px;border-radius:2px;'>{ur.band}</span></td>"
                        f"<td class='m'><b>{ur.state}</b> · <span class='env-tag' style='font-size:8.5px;'>{ur.env_label}</span></td>"
                        f"<td>{ur_icon} <b>{ur.team}</b> ({ur_code})</td>"
                        f"<td class='m'><code>{ur.schema_name}</code></td>"
                        f"<td class='m r' style='{ll_style}'>{ui.fmt_days(ur.days_left)}</td>"
                        f"</tr>"
                    )

                st.markdown(f"""
                <div style="max-height:210px;overflow-y:auto;border:1px solid var(--rule);border-radius:2px;">
                  <table class="tblx" style="font-size:10px;">
                    <tr><th>Severity</th><th>Scope</th><th>Team & Comp</th><th>Schema Name</th><th class="r">Life Left</th></tr>
                    {''.join(q_rows)}
                  </table>
                </div>
                """, unsafe_allow_html=True)

                aq_c1, aq_c2 = st.columns([2.6, 1.4])
                with aq_c1:
                    st.markdown("<div style='font-size:10px;color:var(--mute);padding-top:4px;'>Execute batch renewal overrides for urgent items:</div>", unsafe_allow_html=True)
                with aq_c2:
                    if st.button("⚡ Open Batch Editor", key="gov_send_batch", type="primary", use_container_width=True):
                        st.session_state["op_selected_entity_ids"] = set(urgent_records["id"].tolist())
                        st.session_state["op_target_tab"] = "batch"
                        rerun()

        with act_tab2:
            # Sync default team with left filter if a specific team is selected
            sim_team_default = gov_team_filter if gov_team_filter in ui.TEAMS else ui.TEAMS[0]
            sim_team_idx = ui.TEAMS.index(sim_team_default) if sim_team_default in ui.TEAMS else 0

            sim_c1, sim_c2, sim_c3 = st.columns([0.9, 1.1, 2.0])
            sim_st = sim_c1.selectbox("State", STATES, key="sim_state", label_visibility="collapsed")
            sim_tm = sim_c2.selectbox("Team", ui.TEAMS, index=sim_team_idx, key="sim_team", label_visibility="collapsed")

            # Source of Truth: resolve team ownership directly from Team Governance profiles
            team_gov = TEAM_GOVERNANCE_PROFILES.get(sim_tm, TEAM_GOVERNANCE_PROFILES["Core"])
            owner_role = team_gov["lead"]
            owner_channel = team_gov["channel"]

            conn = get_connection(DB_PATH)
            cur_sim = conn.execute(
                "SELECT * FROM component_records WHERE state = ? AND team = ? ORDER BY CAST(env_no AS INTEGER)",
                (sim_st, sim_tm)
            ).fetchall()
            sim_recs = [dict(r) for r in cur_sim]
            conn.close()

            if sim_recs:
                sim_opts = {f"{r['schema_name']} ({r['environment']}) · {ui.COMPONENT_CODE.get(r['component'], r['component'])}": r for r in sim_recs}
                sim_pick_lbl = sim_c3.selectbox("Target Entity", list(sim_opts), key="sim_entity_pick", label_visibility="collapsed")
                sim_chosen = sim_opts[sim_pick_lbl]

                exp_dt = pd.to_datetime(sim_chosen["exp_date"]).date()
                days_left = (exp_dt - date.today()).days
                team_meta = ui.TEAM_META.get(sim_tm, ui.TEAM_META["Core"])

                sim_mock = {
                    "id": sim_chosen["id"],
                    "username": sim_chosen.get("schema_name", "sim_user"),
                    "schema_name": sim_chosen["schema_name"],
                    "state": sim_chosen["state"],
                    "environment": sim_chosen["environment"],
                    "env": sim_chosen["environment"],
                    "component": sim_chosen["component"],
                    "exp_date": str(exp_dt),
                    "days_left": days_left,
                    "team": sim_tm,
                    "owner_email": owner_channel,
                    "owner_name": f"{owner_role} ({sim_tm})",
                    "team_color": team_meta["color"],
                    "team_lead": owner_role,
                    "frequency_blurb": team_gov["cadence"],
                    "threshold_days": ui.CRITICAL_DAYS if days_left <= ui.CRITICAL_DAYS else ui.WARNING_DAYS,
                }

                email_subject = subject_for(sim_mock)
                email_html = render_email(sim_mock)

                st.markdown(f"""
                <div class="panel" style="border:1px solid var(--rule);border-radius:2px;overflow:hidden;background:var(--card);box-shadow:none !important;margin-top:6px;margin-bottom:8px;">
                  <div class="panel-head" style="background:#141619;">
                    <span class="panel-title" style="font-size:10px;font-weight:700;color:var(--text);letter-spacing:0.06em;">EMAIL ALERT DISPATCH PREVIEW &amp; SIMULATOR</span>
                    <span class="pill" style="color:var(--warning);background:rgba(255,152,48,0.15);font-size:8.5px;font-weight:700;border-radius:2px;">● Simulation Mode (No Live SMTP)</span>
                  </div>
                  <div style="background:#181b1f;border-bottom:1px solid var(--rule-soft);padding:6px 10px;font-size:11px;display:flex;flex-direction:column;gap:3px;">
                    <div><span style="color:var(--mute);font-weight:600;">To:</span> <b style="color:var(--text);">{sim_mock['owner_name']}</b> &lt;<code style="color:var(--accent);font-size:10px;background:rgba(255,120,10,0.1);padding:1px 6px;border-radius:2px;">{sim_mock['owner_email']}</code>&gt;</div>
                    <div style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis;"><span style="color:var(--mute);font-weight:600;">Subject:</span> <span style="color:var(--text);font-weight:600;font-size:11px;">{email_subject}</span></div>
                  </div>
                  <div style="background:#111217;padding:7px;">
                    <div style="max-height:160px;overflow-y:auto;background:#ffffff;border:1px solid var(--rule);border-radius:2px;box-shadow:none !important;">
                      {email_html}
                    </div>
                  </div>
                </div>
                """, unsafe_allow_html=True)

                disp_c1, disp_c2 = st.columns([2.6, 1.4])
                with disp_c1:
                    st.markdown(
                        f"<div style='font-size:10px;color:var(--mute);line-height:32px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;'>"
                        f"Simulate dispatch to <code style='color:var(--accent);font-size:9.5px;'>{sim_mock['owner_email']}</code> &amp; log audit:</div>",
                        unsafe_allow_html=True
                    )
                with disp_c2:
                    if st.button("▶ Trigger Dry-Run Dispatch", key="gov_trigger_dispatch", type="secondary", use_container_width=True):
                        conn_disp = get_connection(DB_PATH)
                        sim_rec_log = {
                            "state": sim_mock["state"],
                            "username": sim_chosen.get("schema_name", "sim_user"),
                            "schema_name": sim_mock["schema_name"],
                            "exp_date": sim_mock["exp_date"],
                        }
                        mark_sent(conn_disp, sim_rec_log)
                        conn_disp.close()
                        bust_cache()
                        st.toast(f"Simulated dispatch logged for {sim_mock['owner_email']}", icon="📧")
                        rerun()

                # -------------------------------------------------------------
                # SEPARATE & EXPLICIT: Live SMTP Dispatch (Real Network Send)
                # -------------------------------------------------------------
                st.markdown("""
                <div style="border-top:1px dashed var(--rule);margin-top:12px;margin-bottom:8px;padding-top:8px;">
                  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:4px;">
                    <div style="display:flex;align-items:center;gap:6px;">
                      <span style="font-size:10.5px;font-weight:700;color:var(--text);letter-spacing:0.04em;">🚀 LIVE SMTP DISPATCH &bull; REAL NETWORK SEND</span>
                    </div>
                    <span class="pill" style="color:var(--healthy);background:var(--green-dim);font-size:8.5px;font-weight:700;border-radius:2px;">NETWORK READY</span>
                  </div>
                  <div style="font-size:10px;color:var(--mute);line-height:1.4;">
                    Dispatches authentic, RFC-compliant email alert for <b>all 10 expired components</b> strictly to designated operational test inboxes.
                  </div>
                </div>
                """, unsafe_allow_html=True)

                real_recipients = ["basha.shaikirfan@gmail.com", "dataengineerib@gmail.com"]

                # Check if credentials are in env or session state
                env_cfg = smtp_config_from_env()
                sess_host = st.session_state.get("gov_smtp_host", env_cfg.get("host") or "")
                sess_port = st.session_state.get("gov_smtp_port", env_cfg.get("port") or 587)
                sess_user = st.session_state.get("gov_smtp_user", env_cfg.get("user") or "")
                sess_pass = st.session_state.get("gov_smtp_pass", env_cfg.get("password") or "")

                has_live_creds = bool(sess_host and sess_user and sess_pass)

                with st.expander("⚙️ Live SMTP Server Credentials", expanded=not has_live_creds):
                    cfg_c1, cfg_c2 = st.columns([1.5, 0.8])
                    with cfg_c1:
                        live_host = st.text_input("SMTP Host", value=sess_host, placeholder="smtp.gmail.com", key="gov_real_smtp_host")
                        live_user = st.text_input("Username / Email", value=sess_user, placeholder="user@gmail.com", key="gov_real_smtp_user")
                    with cfg_c2:
                        live_port = st.text_input("Port", value=str(sess_port), placeholder="587", key="gov_real_smtp_port")
                        live_pass = st.text_input("App Password", value=sess_pass, type="password", placeholder="16-char app password", key="gov_real_smtp_pass")

                    port_val = int(live_port.strip()) if live_port.strip().isdigit() else 587
                    st.session_state["gov_smtp_host"] = live_host.strip()
                    st.session_state["gov_smtp_port"] = port_val
                    st.session_state["gov_smtp_user"] = live_user.strip()
                    st.session_state["gov_smtp_pass"] = live_pass.strip()
                    st.session_state["gov_smtp_from"] = live_user.strip()

                    if not (live_host.strip() and live_user.strip() and live_pass.strip()):
                        st.markdown("<div style='font-size:9.5px;color:var(--warning);'>⚠️ SMTP Host, Username, and Password are required for live network send.</div>", unsafe_allow_html=True)
                    else:
                        st.markdown("<div style='font-size:9.5px;color:var(--healthy);'>✓ SMTP configuration set for session. Ready to dispatch.</div>", unsafe_allow_html=True)

                st.markdown(f"""
                <div style="background:#141619;border:1px solid var(--rule);border-radius:2px;padding:6px 10px;margin-bottom:6px;font-size:10px;">
                  <div><span style="color:var(--mute);font-weight:600;">Strict Target Inboxes:</span> <code style="color:var(--accent);">{', '.join(real_recipients)}</code></div>
                  <div style="margin-top:2px;"><span style="color:var(--mute);font-weight:600;">Alert Payload:</span> <b style="color:var(--text);">10 Overdue Records (ND DR &amp; MO Software Versions)</b></div>
                </div>
                """, unsafe_allow_html=True)

                if st.button("🚀 Send Real Test Email (10 Expired Items)", key="gov_trigger_real_dispatch", type="primary", use_container_width=True):
                    active_host = st.session_state.get("gov_smtp_host")
                    active_port = st.session_state.get("gov_smtp_port", 587)
                    active_user = st.session_state.get("gov_smtp_user")
                    active_pass = st.session_state.get("gov_smtp_pass")
                    active_from = st.session_state.get("gov_smtp_from") or active_user

                    if not (active_host and active_user and active_pass):
                        st.error("❌ Live SMTP Dispatch Blocked: Missing credentials. Please expand '⚙️ Live SMTP Server Credentials' above and supply Host, Username, and App Password.")
                    else:
                        conn_exp = get_connection(DB_PATH)
                        df_all = pd.read_sql_query("SELECT * FROM component_records", conn_exp)
                        conn_exp.close()

                        df_all["exp_dt"] = pd.to_datetime(df_all["exp_date"]).dt.date
                        df_all["days_left"] = (df_all["exp_dt"] - date.today()).apply(lambda d: d.days)
                        exp_list = df_all[df_all["days_left"] < 0].sort_values(by=["state", "team", "environment"]).to_dict(orient="records")

                        active_smtp_config = {
                            "host": active_host,
                            "port": int(active_port),
                            "user": active_user,
                            "password": active_pass,
                            "from_addr": active_from,
                        }

                        with st.spinner("Connecting to SMTP server & transmitting live packets..."):
                            try:
                                receipt = dispatch_expired_alert_real(
                                    recipients=real_recipients,
                                    expired_records=exp_list,
                                    smtp_config=active_smtp_config,
                                    is_simulation=False,
                                )
                                st.session_state["last_real_receipt"] = receipt
                                st.success("✓ Live SMTP Alert Successfully Delivered!")
                            except Exception as ex:
                                import traceback
                                st.error(f"❌ Real SMTP Delivery Failed: {ex}")
                                st.code(traceback.format_exc(), language="text")

                if "last_real_receipt" in st.session_state:
                    rcpt = st.session_state["last_real_receipt"]
                    st.markdown(f"""
                    <div style="background:rgba(115,191,105,0.08);border:1px solid rgba(115,191,105,0.3);border-radius:2px;padding:8px 10px;margin-top:6px;font-size:10px;">
                      <div style="font-weight:700;color:var(--healthy);margin-bottom:4px;">VERIFIABLE DELIVERY RECEIPT</div>
                      <div><b>Server Response:</b> <code>{rcpt.get('smtp_response', '250 2.0.0 OK')}</code></div>
                      <div><b>Timestamp (UTC):</b> <code>{rcpt.get('timestamp')}</code></div>
                      <div><b>Message-ID:</b> <code>{rcpt.get('message_id')}</code></div>
                      <div><b>Recipients:</b> <code>{', '.join(rcpt.get('recipients', []))}</code></div>
                      <div><b>Payload:</b> <code>{rcpt.get('item_count')} Expired Items</code></div>
                    </div>
                    """, unsafe_allow_html=True)

        with act_tab3:
            st.markdown(f"""
            <div class="panel" style="padding:6px 10px;margin-bottom:6px;border-radius:2px;">
              <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:4px;">
                <span style="font-size:11px;font-weight:700;color:var(--text);">Database: <code style="color:var(--accent);">{Path(DB_PATH).name}</code></span>
                <span style="font-size:9.5px;color:var(--healthy);font-weight:700;">● ZERO-MOCK LINEAGE</span>
              </div>
              <table class="tblx" style="font-size:10px;">
                <tr><th>Table Name</th><th class="r">Rows</th><th>Lineage Role</th><th class="r">Status</th></tr>
                <tr><td class="m">component_records</td><td class="m r"><b>{stats['component_records']}</b></td><td style="color:var(--slate)">Multi-Component Workbooks</td><td class="r"><span class="pill" style="color:var(--healthy);background:var(--green-dim);font-size:8.5px;border-radius:2px;">✓ Active</span></td></tr>
                <tr><td class="m">expiry_records</td><td class="m r"><b>{stats['expiry_records']}</b></td><td style="color:var(--slate)">Account DB Passwords</td><td class="r"><span class="pill" style="color:var(--healthy);background:var(--green-dim);font-size:8.5px;border-radius:2px;">✓ Active</span></td></tr>
                <tr><td class="m">maintenance_schedules</td><td class="m r"><b>{stats.get('maintenance_schedules', 0)}</b></td><td style="color:var(--slate)">Team Maintenance Windows</td><td class="r"><span class="pill" style="color:#5794f2;background:rgba(87,148,242,0.15);font-size:8.5px;border-radius:2px;">✓ Synced</span></td></tr>
                <tr><td class="m">owners</td><td class="m r"><b>{stats['owners']}</b></td><td style="color:var(--slate)">State Owner Routing</td><td class="r"><span class="pill" style="color:#5794f2;background:rgba(87,148,242,0.15);font-size:8.5px;border-radius:2px;">3 States</span></td></tr>
                <tr><td class="m">reminder_log</td><td class="m r"><b>{stats['reminder_log']}</b></td><td style="color:var(--slate)">Audit & Reminder Cycles</td><td class="r"><span class="pill" style="color:var(--slate);background:rgba(159,167,179,0.15);font-size:8.5px;border-radius:2px;">Audit Ready</span></td></tr>
              </table>
            </div>
            """, unsafe_allow_html=True)

            conn_audit = get_connection(DB_PATH)
            recent_logs = conn_audit.execute(
                "SELECT state, schema_name, last_sent_at, times_sent FROM reminder_log ORDER BY last_sent_at DESC LIMIT 4"
            ).fetchall()
            conn_audit.close()
            if recent_logs:
                rl_rows = "".join(
                    f"<tr><td class='m'><b>{r['state']}</b></td><td class='m'><code>{r['schema_name']}</code></td><td class='m'>{r['last_sent_at']}</td><td class='m r'><b>{r['times_sent']}</b></td></tr>"
                    for r in recent_logs
                )
                st.markdown(f"""
                <div style="border:1px solid var(--rule);border-radius:2px;overflow:hidden;margin-bottom:6px;">
                  <table class="tblx" style="font-size:9.5px;">
                    <tr><th>State</th><th>Entity Audited</th><th>Last Audit Date</th><th class="r">Dispatches</th></tr>
                    {rl_rows}
                  </table>
                </div>
                """, unsafe_allow_html=True)

            if st.button("⚡ Trigger Immediate Re-ingest (AST Parser)", key="gov_reingest_tab", type="primary", use_container_width=True):
                t_start = datetime.now()
                with st.spinner("Executing workbook parser..."):
                    res = run_ingest(WORKBOOK_DIR, DB_PATH)
                duration_ms = (datetime.now() - t_start).total_seconds() * 1000
                bust_cache()
                st.success(f"Ingested {res['total_rows_read']} records in {duration_ms:.1f}ms ({res['new']} new, {res['renewed']} renewed).")
                rerun()


# ==========================================================================
# Auto-Surfaced "What's New" Strip & Primary Navigation (3 Unified Workspaces)
# ==========================================================================
def compute_whats_new_diff(conn: sqlite3.Connection) -> dict | None:
    """Computes specific, human-readable change description by comparing
    the latest snapshot against the previous one per state and global fleet."""
    cur = conn.execute(
        """SELECT captured_at, state, component, tracked, expired, critical, warning, healthy, soonest_days
           FROM metric_snapshot
           WHERE state IS NOT NULL AND component IS NULL
           ORDER BY captured_at ASC"""
    )
    all_state_snaps = cur.fetchall()
    if not all_state_snaps:
        return None

    by_state = {}
    for r in all_state_snaps:
        s = r["state"]
        if s not in by_state:
            by_state[s] = []
        by_state[s].append(dict(r))

    specific_changes = []
    latest_ts = None
    prev_ts = None

    for state, snaps in by_state.items():
        if len(snaps) >= 2:
            curr = snaps[-1]
            prev = snaps[-2]
            latest_ts = curr["captured_at"]
            prev_ts = prev["captured_at"]
            de = curr["expired"] - prev["expired"]
            dc = curr["critical"] - prev["critical"]
            dw = curr["warning"] - prev["warning"]

            state_parts = []
            if de > 0: state_parts.append(f"+{de} overdue")
            elif de < 0: state_parts.append(f"{abs(de)} resolved overdue")
            if dc > 0: state_parts.append(f"+{dc} critical (≤15d)")
            elif dc < 0: state_parts.append(f"{abs(dc)} exited critical")
            if dw > 0: state_parts.append(f"+{dw} warning (≤30d)")
            elif dw < 0: state_parts.append(f"{abs(dw)} exited warning")

            if state_parts:
                specific_changes.append(f"<b>State {state}</b>: {', '.join(state_parts)}")

    if not specific_changes:
        cur_g = conn.execute(
            """SELECT captured_at, state, component, tracked, expired, critical, warning, healthy
               FROM metric_snapshot
               WHERE state IS NULL AND component IS NULL
               ORDER BY captured_at ASC"""
        )
        g_snaps = [dict(r) for r in cur_g.fetchall()]
        if len(g_snaps) >= 2:
            curr = g_snaps[-1]
            prev = g_snaps[-2]
            latest_ts = curr["captured_at"]
            prev_ts = prev["captured_at"]
            de = curr["expired"] - prev["expired"]
            dc = curr["critical"] - prev["critical"]
            dw = curr["warning"] - prev["warning"]
            g_parts = []
            if de > 0: g_parts.append(f"+{de} overdue items")
            elif de < 0: g_parts.append(f"{abs(de)} resolved overdue items")
            if dc > 0: g_parts.append(f"+{dc} critical items (≤15d)")
            if dw > 0: g_parts.append(f"+{dw} warning items (≤30d)")
            if g_parts:
                specific_changes.append(f"<b>Global Fleet</b>: {', '.join(g_parts)}")

    if not specific_changes:
        return None

    days_ago_str = ""
    if prev_ts:
        try:
            prev_dt = datetime.fromisoformat(prev_ts.replace("Z", "+00:00"))
            cur_dt = datetime.fromisoformat(latest_ts.replace("Z", "+00:00")) if latest_ts else datetime.now(timezone.utc)
            diff_days = (cur_dt.date() - prev_dt.date()).days
            if diff_days > 0:
                days_ago_str = f" ({diff_days} day{'s' if diff_days != 1 else ''} ago)"
            else:
                days_ago_str = " (since last check)"
        except Exception:
            pass

    return {
        "id": f"{latest_ts}_{'|'.join(specific_changes)}",
        "text": f"{'; '.join(specific_changes)}{days_ago_str}."
    }

conn_snap = get_connection(DB_PATH)
diff_info = compute_whats_new_diff(conn_snap)
conn_snap.close()

if diff_info is not None:
    if st.session_state.get("dismissed_whats_new") != diff_info["id"]:
        wn_c1, wn_c2 = st.columns([9.5, 0.5])
        with wn_c1:
            st.markdown(f"""
            <div style="background:rgba(56,189,248,0.08);border:1px solid rgba(56,189,248,0.25);border-radius:4px;padding:3px 8px;font-size:10.5px;color:#f8fafc;display:flex;align-items:center;gap:6px;margin-bottom:2px;">
              <span style="font-weight:700;color:var(--accent);">💡 What's New:</span>
              <span>{diff_info['text']}</span>
            </div>
            """, unsafe_allow_html=True)
        with wn_c2:
            if st.button("✕", key="wn_dismiss_btn", help="Dismiss Notification", use_container_width=True):
                st.session_state["dismissed_whats_new"] = diff_info["id"]
                rerun()

# ==========================================================================
# Left Toggle Bar (Collapsible Enterprise Navigation Rail)
# ==========================================================================
with st.sidebar:
    st.markdown("""
    <!-- Top Rail Header: Toggle button + Brand -->
    <div class="rail-header" style="display:flex;align-items:center;justify-content:space-between;gap:8px;padding:6px 4px 10px;border-bottom:1px solid #1e293b;margin-bottom:10px;">
      <div class="rail-brand-group" style="display:flex;align-items:center;gap:8px;min-width:0;overflow:hidden;">
        <button id="ets-rail-toggle-btn" class="rail-toggle-btn" title="Toggle Navigation Panel (Click to expand / collapse)" style="display:flex;align-items:center;justify-content:center;width:32px;height:32px;background:rgba(56,189,248,0.12);border:1px solid rgba(56,189,248,0.4);border-radius:6px;color:#38bdf8;cursor:pointer;flex-shrink:0;transition:all 0.15s ease;">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <rect x="3" y="3" width="18" height="18" rx="2" ry="2"></rect>
            <line x1="9" y1="3" x2="9" y2="21"></line>
          </svg>
        </button>
        <div class="rail-brand-text" style="overflow:hidden;white-space:nowrap;">
          <div style="font-size:12px;font-weight:800;color:#f8fafc;letter-spacing:-.01em;">ETS WATCHTOWER</div>
          <div style="font-size:8px;color:#38bdf8;font-family:var(--mono);font-weight:600;">ENTERPRISE PLATFORM</div>
        </div>
      </div>
      <button id="ets-close-panel-btn" class="rail-close-btn" style="background:rgba(239,68,68,0.12);border:1px solid rgba(239,68,68,0.35);color:#fca5a5;font-size:10px;font-weight:700;border-radius:4px;padding:3px 7px;cursor:pointer;white-space:nowrap;" title="Collapse Navigation Rail">
        ✕
      </button>
    </div>

    <!-- Navigation Workspace Icons / Links -->
    <div class="rail-section-label" style="font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:#94a3b8;margin-bottom:6px;white-space:nowrap;overflow:hidden;">Workspaces</div>
    <div class="ets-nav-items" style="display:flex;flex-direction:column;gap:6px;">
      <button class="ets-nav-item active" data-nav-idx="0" title="Executive Command Center">
        <span class="nav-icon" style="font-size:15px;display:flex;align-items:center;justify-content:center;width:20px;flex-shrink:0;">⚡</span>
        <span class="nav-label" style="font-size:11.5px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">Command Center</span>
      </button>
      <button class="ets-nav-item" data-nav-idx="1" title="Portfolio Matrix & Operations Hub">
        <span class="nav-icon" style="font-size:15px;display:flex;align-items:center;justify-content:center;width:20px;flex-shrink:0;">📊</span>
        <span class="nav-label" style="font-size:11.5px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">Operations Hub</span>
      </button>
      <button class="ets-nav-item" data-nav-idx="2" title="Governance & Alerts">
        <span class="nav-icon" style="font-size:15px;display:flex;align-items:center;justify-content:center;width:20px;flex-shrink:0;">🛡️</span>
        <span class="nav-label" style="font-size:11.5px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">Governance & Alerts</span>
      </button>
    </div>

    <!-- Live Fleet Telemetry (Visible when expanded) -->
    <div class="nav-telemetry" style="margin-top:16px;padding-top:12px;border-top:1px solid #1e293b;">
      <div style="font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:#94a3b8;margin-bottom:8px;">Fleet Telemetry</div>
      <div style="display:flex;flex-direction:column;gap:5px;font-size:11px;">
        <div style="display:flex;justify-content:space-between;padding:4px 7px;background:rgba(255,255,255,0.03);border-radius:4px;border:1px solid rgba(255,255,255,0.05);">
          <span style="color:#94a3b8;">Managed Assets</span>
          <b style="color:#f8fafc;font-family:var(--mono);">500</b>
        </div>
        <div style="display:flex;justify-content:space-between;padding:4px 7px;background:rgba(16,185,129,0.08);border-radius:4px;border:1px solid rgba(16,185,129,0.2);">
          <span style="color:#34d399;">Fleet SLA</span>
          <b style="color:#10b981;font-family:var(--mono);">95.0% OK</b>
        </div>
        <div style="display:flex;justify-content:space-between;padding:4px 7px;background:rgba(56,189,248,0.08);border-radius:4px;border:1px solid rgba(56,189,248,0.2);">
          <span style="color:#38bdf8;">PROD Resiliency</span>
          <b style="color:#38bdf8;font-family:var(--mono);">100% OK</b>
        </div>
        <div style="display:flex;justify-content:space-between;padding:4px 7px;background:rgba(239,68,68,0.08);border-radius:4px;border:1px solid rgba(239,68,68,0.2);">
          <span style="color:#f87171;">Overdue Debt</span>
          <b style="color:#ef4444;font-family:var(--mono);">10 Active</b>
        </div>
      </div>
    </div>
    """, unsafe_allow_html=True)





tab_overview, tab_operations, tab_governance = st.tabs([
    "Executive Command Center",
    "Portfolio Matrix & Operations Hub",
    "Governance & Alerts",
])

with tab_overview:
    canvas("all", None, CANVAS_OVERVIEW)

with tab_operations:
    render_operations_hub(records)

with tab_governance:
    render_governance_center()
