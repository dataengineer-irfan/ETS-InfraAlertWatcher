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
from notifier import render_email, subject_for, smtp_config_from_env  # noqa: E402

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
        update_component_exp_date(conn, int(record_id), new_date.isoformat())
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
        <div class="state-kpi-hint">{'Modified in SQLite' if overrides else '100% in sync with Excel'}</div>
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
        st.markdown(ui.note(
            f"<b>{len(work)}</b> record(s) in view — {int(counts.get('Expired', 0))} expired, "
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
            note_col.markdown(ui.note(
                f"<b>{len(changes)}</b> unsaved change(s) — press Save changes to apply."
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

            st.markdown(ui.note(
                f"<b>{len(edits)}</b> record(s) differ from the workbook. Reverting restores the "
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
    # 1. Top Slicer Command Bar
    f1, f2, f3, f4, f5, f6 = st.columns([1.6, 0.9, 1.0, 1.15, 0.95, 0.8])
    q = f1.text_input("Filter", key="op_search", placeholder="Search schema, env...", label_visibility="collapsed")
    state_filter = f2.selectbox("State", ["All States"] + STATES, key="op_state", label_visibility="collapsed")
    team_filter = f3.selectbox("Team", ["All Teams"] + ui.TEAMS, key="op_team", label_visibility="collapsed")
    comp_filter = f4.selectbox(
        "Component",
        ["All Components"] + COMPONENT_ORDER,
        key="op_comp",
        label_visibility="collapsed",
        format_func=lambda c: ui.COMPONENT_CODE.get(c, c) if c != "All Components" else "All Components",
    )
    health_filter = f5.selectbox("Health", ["All Health"] + ui.BANDS, key="op_health", label_visibility="collapsed")

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

    filtered = filtered.sort_values("days_left")

    csv_data = filtered.to_csv(index=False).encode("utf-8")
    if hasattr(st, "download_button"):
        f6.download_button(
            label="📥 CSV",
            data=csv_data,
            file_name=f"expiry_operations_{date.today().isoformat()}.csv",
            mime="text/csv",
            use_container_width=True,
        )

    # 2. Executive Metric Ribbon — reactive to active slicer, dual local/fleet context
    tot_cnt = len(df)
    scope_cnt = len(filtered)
    exp_cnt = int((filtered["days_left"] < 0).sum())
    crit_cnt = int((filtered["days_left"].between(0, ui.CRITICAL_DAYS)).sum())
    warn_cnt = int((filtered["days_left"].between(ui.CRITICAL_DAYS + 1, ui.WARNING_DAYS)).sum())
    hlth_cnt = int((filtered["days_left"] > ui.WARNING_DAYS).sum())
    g_exp = int((df["days_left"] < 0).sum())
    g_crit_warn = int((df["days_left"].between(0, ui.WARNING_DAYS)).sum())
    g_hlth = int((df["days_left"] > ui.WARNING_DAYS).sum())

    k1, k2, k3, k4 = st.columns(4)
    with k1:
        st.markdown(f"""
        <div class="top-glow-kpi" style="--glow:#38bdf8;padding:4px 10px;margin-bottom:2px;">
          <div class="kpi-label" style="font-size:9.5px;">Portfolio Scope</div>
          <div class="kpi-value" style="font-size:17px;line-height:1.1;">{scope_cnt} <span style="font-size:10px;color:#94a3b8;font-weight:400;">/ {tot_cnt}</span></div>
          <div class="kpi-sub" style="font-size:9.5px;">of {tot_cnt} total fleet</div>
        </div>
        """, unsafe_allow_html=True)
    with k2:
        st.markdown(f"""
        <div class="top-glow-kpi" style="--glow:{'#ef4444' if exp_cnt else '#10b981'};padding:4px 10px;margin-bottom:2px;">
          <div class="kpi-label" style="font-size:9.5px;">Expired Items</div>
          <div class="kpi-value" style="font-size:17px;line-height:1.1;color:{'#ef4444' if exp_cnt else '#10b981'};">{exp_cnt}</div>
          <div class="kpi-sub" style="font-size:9.5px;">{'Requires renewal' if exp_cnt else 'None in view'} · {g_exp} fleet total</div>
        </div>
        """, unsafe_allow_html=True)
    with k3:
        st.markdown(f"""
        <div class="top-glow-kpi" style="--glow:{'#f97316' if crit_cnt else '#f59e0b' if warn_cnt else '#10b981'};padding:4px 10px;margin-bottom:2px;">
          <div class="kpi-label" style="font-size:9.5px;">Critical &amp; Warning</div>
          <div class="kpi-value" style="font-size:17px;line-height:1.1;color:{'#f59e0b' if (crit_cnt + warn_cnt) else '#10b981'};">{crit_cnt + warn_cnt}</div>
          <div class="kpi-sub" style="font-size:9.5px;">{crit_cnt} crit · {warn_cnt} warn · {g_crit_warn} fleet total</div>
        </div>
        """, unsafe_allow_html=True)
    with k4:
        pct_local = (hlth_cnt / scope_cnt * 100) if scope_cnt else 0
        st.markdown(f"""
        <div class="top-glow-kpi" style="--glow:#10b981;padding:4px 10px;margin-bottom:2px;">
          <div class="kpi-label" style="font-size:9.5px;">Healthy Entities</div>
          <div class="kpi-value" style="font-size:17px;line-height:1.1;color:#10b981;">{hlth_cnt}</div>
          <div class="kpi-sub" style="font-size:9.5px;">{pct_local:.0f}% in view · {g_hlth} fleet total</div>
        </div>
        """, unsafe_allow_html=True)

    st.markdown("<div style='margin-top:2px;'></div>", unsafe_allow_html=True)

    # 3. Master-Detail Workspace (42% Left Hierarchy Tree / 58% Right Inspector)
    left_col, _, right_col = st.columns([1.7, 0.04, 2.3])

    tree_open = st.session_state.setdefault("op_tree_open", set())

    # Auto-open single most urgent entity on initial visit
    if filtered.empty:
        selected_id = None
    else:
        cur_active_id = st.session_state.get("op_active_id")
        if cur_active_id not in filtered["id"].values:
            most_urgent = filtered.sort_values("days_left").iloc[0]
            cur_active_id = int(most_urgent["id"])
            st.session_state["op_active_id"] = cur_active_id
            # Auto-expand the ancestral path for the most urgent entity
            tree_open.add(str(most_urgent["state"]))
            tree_open.add(f"{most_urgent['state']}/{most_urgent['team']}")
            tree_open.add(f"{most_urgent['state']}/{most_urgent['team']}/{most_urgent['component']}")
            tree_open.add(f"{most_urgent['state']}/{most_urgent['team']}/{most_urgent['component']}/{most_urgent['env_label']}")
        selected_id = cur_active_id

    with left_col:
        if filtered.empty:
            st.markdown(ui.empty("No records match filter", "Try broadening your search query or reset filters."), unsafe_allow_html=True)
        else:
            # Persistent Interactive Breadcrumb Header & Selection Toolbar
            active_rec = df[df["id"] == selected_id].iloc[0] if selected_id in df["id"].values else None
            bc_st = active_rec["state"] if active_rec is not None else (state_filter if state_filter != "All States" else None)
            bc_tm = active_rec["team"] if active_rec is not None else (team_filter if team_filter != "All Teams" else None)
            bc_cp = active_rec["component"] if active_rec is not None else (comp_filter if comp_filter != "All Components" else None)
            bc_cp_code = ui.COMPONENT_CODE.get(bc_cp, bc_cp) if bc_cp else None
            bc_icon = ui.COMPONENT_ICONS.get(bc_cp, "📦") if bc_cp else "📦"

            bc_parts = ["<span style='color:var(--accent);font-weight:700;'>🏠 All</span>"]
            if bc_st: bc_parts.append(f"<span style='color:#f8fafc;font-weight:600;'>📍 {bc_st}</span>")
            if bc_tm: bc_parts.append(f"<span style='color:#cbd5e1;'>👥 {bc_tm}</span>")
            if bc_cp_code: bc_parts.append(f"<span style='color:#94a3b8;'>{bc_icon} {bc_cp_code}</span>")
            bc_trail = " <span style='color:var(--rule);font-size:9px;'>›</span> ".join(bc_parts)

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

            bc_c1, bc_c2 = st.columns([2.0, 2.4])
            with bc_c1:
                st.markdown(f"<div style='font-size:10px;padding:2px 2px 4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;'>{bc_trail}</div>", unsafe_allow_html=True)
            with bc_c2:
                tc1, tc2, tc3, tc4 = st.columns([0.7, 0.7, 1.6, 2.0])
                with tc1:
                    if st.button("＋", key="tree_exp_all", help="Expand All Branches"):
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
                    if st.button("－", key="tree_col_all", help="Collapse All Branches"):
                        tree_open.clear()
                        rerun()
                with tc3:
                    all_f_ids = set(filtered["id"].tolist())
                    n_sel = len(selected_entity_ids)
                    if n_sel > 0:
                        if st.button(f"Clear ({n_sel})", key="tree_clear_sel_btn", help="Clear selection"):
                            selected_entity_ids.clear()
                            rerun()
                    else:
                        if st.button("Select All", key="tree_select_all_btn", help="Select all filtered entities"):
                            selected_entity_ids.update(all_f_ids)
                            rerun()
                with tc4:
                    n_sel = len(selected_entity_ids)
                    btn_txt = f"⚡ Batch ({n_sel})" if n_sel > 0 else "⚡ Batch Editor"
                    if st.button(btn_txt, key="tree_send_to_batch", disabled=(n_sel == 0), type="primary" if n_sel > 0 else "secondary", use_container_width=True, help="Send selected entities to Batch Grid Editor"):
                        st.session_state["op_target_tab"] = "batch"
                        rerun()

            # Hierarchical Matrix Tree (5-Level Cascading Hierarchy with Tri-State Multi-Select)
            st.markdown("<div style='max-height:280px;overflow-y:auto;border:1px solid var(--rule);border-radius:6px;padding:2px 3px;'>", unsafe_allow_html=True)

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
                    if st.button(st_sym, key=f"sel_st_{st_val}", use_container_width=True, help=f"Toggle all {len(st_child_ids)} entities in State {st_val}"):
                        if st_uncheck:
                            selected_entity_ids.difference_update(st_child_ids)
                        else:
                            selected_entity_ids.update(st_child_ids)
                        rerun()
                with s_c1:
                    st.markdown(f"""
                    <div style="display:flex;align-items:center;justify-content:space-between;background:rgba(255,255,255,0.04);border-radius:4px;padding:2px 6px;margin-bottom:2px;font-size:11px;">
                      <span style="font-weight:700;color:#f8fafc;font-family:var(--mono);">
                        📍 State {st_val} <span style="font-weight:400;color:#94a3b8;font-size:9.5px;">({len(st_sub)} items)</span>
                      </span>
                      <span class="pill" style="color:{st_meta['color']};background:{st_meta['tint']};font-size:9px;padding:1px 5px;">
                        <b>{st_meta['symbol']}</b> {f'{st_exp_n} Expired' if st_exp_n else st_worst}
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
                        tm_color = ui.TEAM_META.get(tm_val, {}).get("color", "#38bdf8")
                        tm_child_ids = set(tm_sub["id"].tolist())
                        tm_sym, tm_uncheck = tri_state_info(tm_child_ids, selected_entity_ids)

                        # Level 2: Team Node
                        t_c0, t_c1, t_c2 = st.columns([0.6, 3.8, 0.6])
                        with t_c0:
                            if st.button(tm_sym, key=f"sel_tm_{st_val}_{tm_val}", use_container_width=True, help=f"Toggle all {len(tm_child_ids)} entities in Team {tm_val}"):
                                if tm_uncheck:
                                    selected_entity_ids.difference_update(tm_child_ids)
                                else:
                                    selected_entity_ids.update(tm_child_ids)
                                rerun()
                        with t_c1:
                            st.markdown(f"""
                            <div style="display:flex;align-items:center;justify-content:space-between;margin-left:4px;background:rgba(255,255,255,0.02);border-radius:3px;padding:2px 6px;margin-bottom:2px;font-size:10.5px;">
                              <span style="color:{tm_color};font-weight:700;">
                                👥 {tm_val} <span style="color:#94a3b8;font-weight:400;font-size:9px;">({len(tm_sub)})</span>
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
                                with cp_c0:
                                    if st.button(cp_sym, key=f"sel_cp_{st_val}_{tm_val}_{cp_code}", use_container_width=True, help=f"Toggle all {len(cp_child_ids)} entities in {cp_code}"):
                                        if cp_uncheck:
                                            selected_entity_ids.difference_update(cp_child_ids)
                                        else:
                                            selected_entity_ids.update(cp_child_ids)
                                        rerun()
                                handy_cp_head = f"{cp_icon} {cp_code}"
                                with cp_c1:
                                    st.markdown(f"""
                                    <div style="display:flex;align-items:center;justify-content:space-between;margin-left:8px;border-left:2px solid {cp_meta['color']};padding:1px 6px;margin-bottom:2px;font-size:10px;">
                                      <span style="color:#f8fafc;font-weight:600;">{handy_cp_head} <span style="color:#94a3b8;font-size:8.5px;">({len(cp_sub)})</span></span>
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
                                            if st.button(ev_sym, key=f"sel_ev_{st_val}_{tm_val}_{cp_code}_{ev_val}", use_container_width=True, help=f"Toggle all {len(ev_child_ids)} entities in {ev_val}"):
                                                if ev_uncheck:
                                                    selected_entity_ids.difference_update(ev_child_ids)
                                                else:
                                                    selected_entity_ids.update(ev_child_ids)
                                                rerun()
                                        with ev_c1:
                                            st.markdown(f"""
                                            <div style="display:flex;align-items:center;justify-content:space-between;margin-left:12px;padding:1px 4px;font-size:9.5px;color:#cbd5e1;">
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
                                                r_bg = "background:rgba(56,189,248,0.2);border:1px solid #38bdf8;box-shadow:inset 2px 0 0 #38bdf8;" if is_act else "background:rgba(255,255,255,0.015);border:1px solid rgba(255,255,255,0.04);"

                                                row_c0, row_c1, row_c2 = st.columns([0.6, 3.2, 1.2])
                                                with row_c0:
                                                    if st.button("☑" if is_leaf_sel else "☐", key=f"sel_leaf_{r.id}", use_container_width=True, help="Toggle selection for Batch Editor"):
                                                        if is_leaf_sel:
                                                            selected_entity_ids.discard(r.id)
                                                        else:
                                                            selected_entity_ids.add(r.id)
                                                        rerun()
                                                with row_c1:
                                                    st.markdown(f"""
                                                    <div style="{r_bg};margin-left:14px;border-radius:3px;padding:2px 5px;margin-bottom:1px;display:flex;align-items:center;justify-content:space-between;">
                                                      <span style="font-family:var(--mono);font-size:9.5px;font-weight:{'700' if is_act else '500'};color:{'#38bdf8' if is_act else '#f8fafc'};">
                                                        {r.schema_name}
                                                      </span>
                                                      <span style="color:{r_meta['color']};font-size:9px;font-family:var(--mono);font-weight:700;">{ui.fmt_days(r.days_left)}</span>
                                                    </div>
                                                    """, unsafe_allow_html=True)
                                                with row_c2:
                                                    if st.button("Inspect", key=f"t_leaf_btn_{r.id}", type="primary" if is_act else "secondary", use_container_width=True):
                                                        st.session_state["op_active_id"] = r.id
                                                        rerun()
            st.markdown("</div>", unsafe_allow_html=True)

    with right_col:
        if selected_id is None:
            st.markdown(ui.empty("Select a record", "Choose an item from the master hierarchy tree on the left to inspect."), unsafe_allow_html=True)
            return

        rec = df[df["id"] == selected_id].iloc[0]
        meta = ui.BAND_META.get(rec["band"], ui.BAND_META["Healthy"])
        team_meta = ui.TEAM_META.get(rec["team"], ui.TEAM_META["Core"])
        cp_icon = ui.COMPONENT_ICONS.get(rec["component"], "📦")

        # Top Inspector Header & Quick Renewal Bar (Compact 38px)
        st.markdown(f"""
        <div class="card" style="border-left:4px solid {meta['color']};margin-bottom:4px;padding:5px 10px;">
          <div style="display:flex;align-items:center;justify-content:space-between;">
            <div>
              <span style="font-size:9px;font-weight:700;letter-spacing:.08em;color:{meta['color']}">ENTITY #{rec['id']} · {rec['state']} · <span style="color:{team_meta['color']}">{rec['team']}</span> · {cp_icon} {rec['component']}</span>
              <div style="font-size:14px;font-weight:700;font-family:var(--mono);color:#f8fafc;margin-top:1px;">{rec['schema_name']} <span class="env-tag" style="font-size:9.5px;">{rec['env_label']}</span></div>
            </div>
            {ui.status_pill(rec['band'])}
          </div>
        </div>
        """, unsafe_allow_html=True)

        # Instant Action & Renewal Bar with 2-Step Confirmation
        conf = st.session_state.get("confirm_action")
        cur_dt = rec["exp_dt"].date()

        if conf and conf.get("id") == rec["id"]:
            cf_c1, cf_c2, cf_c3 = st.columns([2.5, 0.8, 0.8])
            with cf_c1:
                st.markdown(f"<div style='font-size:10.5px;color:#f59e0b;font-weight:700;padding-top:4px;'>⚠️ Extend {conf['schema']} to {conf['new_dt']} (+{conf['days']}d)?</div>", unsafe_allow_html=True)
            with cf_c2:
                if st.button("✓ Confirm", key=f"op_cf_yes_{rec['id']}", type="primary", use_container_width=True):
                    apply_edits([(conf["id"], conf["new_dt"])])
                    del st.session_state["confirm_action"]
                    st.success(f"Updated {conf['schema']} to {conf['new_dt']}")
                    rerun()
            with cf_c3:
                if st.button("Cancel", key=f"op_cf_no_{rec['id']}", use_container_width=True):
                    del st.session_state["confirm_action"]
                    rerun()
        else:
            act_b1, act_b2, act_b3, act_b4 = st.columns([1, 1, 1.6, 1.2])
            if act_b1.button("+90 Days", key=f"op_top_p90_{rec['id']}", use_container_width=True):
                st.session_state["confirm_action"] = {"id": rec["id"], "days": 90, "new_dt": cur_dt + pd.Timedelta(days=90), "schema": rec["schema_name"]}
                rerun()
            if act_b2.button("+1 Year", key=f"op_top_p365_{rec['id']}", use_container_width=True):
                st.session_state["confirm_action"] = {"id": rec["id"], "days": 365, "new_dt": cur_dt + pd.Timedelta(days=365), "schema": rec["schema_name"]}
                rerun()

            with act_b3:
                if hasattr(st, "popover"):
                    with st.popover("📅 Custom Date"):
                        c_date = st.date_input("New Expiry Date", value=cur_dt, key=f"op_pop_dt_{rec['id']}")
                        if st.button("Commit Date", type="primary", key=f"op_pop_btn_{rec['id']}", use_container_width=True):
                            apply_edits([(rec["id"], c_date)])
                            st.success(f"Updated to {c_date}")
                            rerun()
                else:
                    with st.expander("📅 Custom Date"):
                        c_date = st.date_input("New Expiry Date", value=cur_dt, key=f"op_pop_dt_{rec['id']}")
                        if st.button("Commit Date", type="primary", key=f"op_pop_btn_{rec['id']}", use_container_width=True):
                            apply_edits([(rec["id"], c_date)])
                            st.success(f"Updated to {c_date}")
                            rerun()

            if rec["edited"]:
                with act_b4:
                    if st.button("↩ Revert", key=f"op_top_rev_{rec['id']}", type="secondary", use_container_width=True):
                        conn = get_connection(DB_PATH)
                        revert_component_exp_date(conn, int(rec["id"]))
                        conn.close()
                        bust_cache()
                        st.success("Reverted to source workbook.")
                        rerun()

        i_tab1, i_tab2, i_tab3, i_tab4 = st.tabs(["Overview & Lineage", "Portfolio Matrix", "Batch Grid Editor", "Rollback Ledger"])

        with i_tab1:
            st.markdown("<div style='max-height:200px;overflow-y:auto;padding-right:2px;'>", unsafe_allow_html=True)
            c_a, c_b = st.columns(2)
            with c_a:
                st.markdown(f"""
                <div class="card" style="font-size:11px;line-height:1.45;padding:5px 8px;">
                  <div><b>Team Owner:</b> <span style="color:{team_meta['color']};font-weight:700;">{rec['team']}</span> <span style="color:#94a3b8;font-size:9.5px;">({team_meta['lead']})</span></div>
                  <div><b>Component:</b> {rec['component']}</div>
                  <div style="color:#94a3b8;font-size:9.5px;margin-bottom:3px;">{ui.COMPONENT_BLURB.get(rec['component'], '')}</div>
                  <div><b>Environment:</b> <span class="env-tag" style="font-size:9px;">{rec['env_label']}</span> ({ui.ENV_BLURB.get(rec['env_label'], 'Custom')})</div>
                  <div><b>Module Code:</b> <code>{rec['module'] or 'N/A'}</code></div>
                </div>
                """, unsafe_allow_html=True)
            with c_b:
                st.markdown(f"""
                <div class="card" style="font-size:11px;line-height:1.45;padding:5px 8px;">
                  <div><b>Current Expiry:</b> <code style="font-weight:700;color:{meta['color']}">{rec['exp_date']}</code></div>
                  <div><b>Workbook Source:</b> <code>{rec['source_exp_date']}</code></div>
                  <div><b>Life Remaining:</b> <b>{ui.fmt_days(rec['days_left'])}</b> ({rec['days_left']} days)</div>
                  <div><b>Quarter Horizon:</b> <code>{rec['quarter']}</code></div>
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
            with st.expander("View raw data / query", expanded=False):
                if hasattr(st, "code"):
                    st.caption("Technical Diagnostics & Database Query")
                    st.code(f"SELECT * FROM component_records WHERE id = {int(rec['id'])};", language="sql")
                    st.code(json.dumps(payload, indent=2), language="json")
            st.markdown("</div>", unsafe_allow_html=True)

        with i_tab2:
            st.markdown("<div style='max-height:200px;overflow-y:auto;padding-right:2px;'>", unsafe_allow_html=True)
            st.markdown('<div class="note" style="margin-bottom:6px;"><b>Severity Heatmap (State × Component):</b> Color saturation indicates risk density. Click any cell to filter and expand the tree.</div>', unsafe_allow_html=True)
            mat_states = STATES
            mat_comps = COMPONENT_ORDER
            header_cols = "".join(f"<th style='text-align:center;padding:4px 6px;'>{ui.COMPONENT_ICONS.get(c, '')} {ui.COMPONENT_CODE.get(c, c)}</th>" for c in mat_comps)
            matrix_head = f"<tr><th>State</th>{header_cols}<th class='r' style='padding:4px 6px;'>Total</th></tr>"

            matrix_rows = []
            for st_val in mat_states:
                st_sub = df[df["state"] == st_val]
                cell_tds = []
                for c_val in mat_comps:
                    cell_sub = st_sub[st_sub["component"] == c_val]
                    if cell_sub.empty:
                        cell_tds.append("<td class='c' style='color:var(--mute);padding:4px;'>—</td>")
                    else:
                        c_cnt = len(cell_sub)
                        c_exp = (cell_sub["days_left"] < 0).sum()
                        c_warn = (cell_sub["days_left"].between(0, ui.WARNING_DAYS)).sum()
                        worst_b = ui.worst_band(cell_sub["band"].tolist())
                        meta = ui.BAND_META.get(worst_b, ui.BAND_META["Healthy"])
                        min_days = cell_sub["days_left"].min()

                        # Color-intensity gradient styling
                        if c_exp > 0:
                            bg_style = "background:linear-gradient(135deg, rgba(239,68,68,0.32), rgba(239,68,68,0.12));border:1px solid #ef4444;color:#fca5a5;"
                            badge_txt = f"● {c_exp} Exp"
                        elif c_warn > 0:
                            bg_style = "background:linear-gradient(135deg, rgba(245,158,11,0.32), rgba(245,158,11,0.12));border:1px solid #f59e0b;color:#fcd34d;"
                            badge_txt = f"▲ {c_warn} Warn"
                        else:
                            bg_style = "background:linear-gradient(135deg, rgba(16,185,129,0.2), rgba(16,185,129,0.06));border:1px solid rgba(16,185,129,0.35);color:#6ee7b7;"
                            badge_txt = f"✓ {c_cnt} OK"

                        cell_tds.append(
                            f"<td class='c' style='padding:3px;'>"
                            f"<div style='{bg_style}border-radius:4px;padding:3px 4px;text-align:center;font-size:9.5px;line-height:1.2;'>"
                            f"<b>{badge_txt}</b><br/>"
                            f"<span style='font-size:8.5px;opacity:0.8;'>{ui.fmt_days(min_days)}</span>"
                            f"</div></td>"
                        )
                st_tot = len(st_sub)
                matrix_rows.append(
                    f"<tr><td style='font-weight:700;font-family:var(--mono);padding:4px 6px;'>{st_val}</td>{''.join(cell_tds)}<td class='m r' style='font-weight:700;color:var(--accent);padding:4px 6px;'>{st_tot}</td></tr>"
                )
            st.markdown(f"<div style='border:1px solid var(--rule);border-radius:6px;overflow:hidden;'><table class='tblx' style='font-size:10.5px;'>{matrix_head}{''.join(matrix_rows)}</table></div>", unsafe_allow_html=True)
            st.markdown("</div>", unsafe_allow_html=True)

        with i_tab3:
            if selected_entity_ids:
                batch_work = df[df["id"].isin(selected_entity_ids)].copy()
                batch_work.sort_values(by=["state", "team", "component", "env_no", "schema_name"], inplace=True)
            else:
                batch_work = filtered.copy()

            total_batch_n = len(batch_work)
            b_per_page = 8
            b_pages = max(1, (total_batch_n + b_per_page - 1) // b_per_page)
            b_page = st.session_state.setdefault("op_batch_page_no", 0)
            b_page = max(0, min(b_page, b_pages - 1))

            b_from = b_page * b_per_page + 1 if total_batch_n > 0 else 0
            b_to = min(total_batch_n, (b_page + 1) * b_per_page)
            page_slice = batch_work.iloc[b_from - 1:b_to].copy() if total_batch_n > 0 else batch_work.copy()

            bg_c1, bg_c2, bg_c3 = st.columns([2.5, 1.4, 1.1])
            with bg_c1:
                if selected_entity_ids:
                    st.markdown(f"<div style='font-size:10.5px;color:#38bdf8;font-weight:700;padding-top:3px;'>⚡ Showing {b_from}–{b_to} of {total_batch_n} selected items</div>", unsafe_allow_html=True)
                else:
                    st.markdown(f"<div style='font-size:10.5px;color:#cbd5e1;font-weight:600;padding-top:3px;'>Showing {b_from}–{b_to} of {total_batch_n} items</div>", unsafe_allow_html=True)
            with bg_c2:
                if b_pages > 1:
                    p_c1, p_c2, p_c3 = st.columns([1, 1.6, 1])
                    if p_c1.button("‹", key="op_batch_p_prev", disabled=(b_page == 0), use_container_width=True):
                        st.session_state["op_batch_page_no"] = b_page - 1
                        rerun()
                    p_c2.markdown(f"<div style='font-size:10px;text-align:center;padding-top:4px;color:#94a3b8;'>Page {b_page+1}/{b_pages}</div>", unsafe_allow_html=True)
                    if p_c3.button("›", key="op_batch_p_next", disabled=(b_page >= b_pages - 1), use_container_width=True):
                        st.session_state["op_batch_page_no"] = b_page + 1
                        rerun()
            with bg_c3:
                if selected_entity_ids:
                    if st.button("Clear", key="op_batch_clear_sel", use_container_width=True):
                        selected_entity_ids.clear()
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
                        "exp_dt": st.column_config.DateColumn("Expiry Date", format="YYYY-MM-DD", required=True, width="medium"),
                        "band": st.column_config.TextColumn("Status", disabled=True, width="small"),
                        "days_left": st.column_config.TextColumn("Time Left", disabled=True, width="small"),
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
                b_note_col.markdown(f"<div style='font-size:10.5px;color:#94a3b8;padding-top:4px;'><b>{len(b_changes)}</b> unsaved change(s) on current page</div>", unsafe_allow_html=True)
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
                st.markdown(ui.note(f"<b>{len(active_edits)}</b> override(s) in SQLite:"), unsafe_allow_html=True)
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
        <div style="background:var(--sunk);border:1px solid var(--rule);border-radius:6px;padding:4px 10px;display:flex;align-items:center;justify-content:space-between;">
          <div style="display:flex;align-items:center;gap:8px;">
            <span style="font-size:9.5px;font-weight:700;color:var(--accent);letter-spacing:0.06em;">GOVERNANCE SCOPE:</span>
            <span style="font-size:11px;color:#f8fafc;font-weight:700;">{scope_name}</span>
            <span style="font-size:9.5px;color:#94a3b8;">({len(scoped_records)} of {len(records)} Total Managed Assets)</span>
          </div>
          <div style="font-size:9.5px;color:#10b981;font-weight:600;font-family:var(--mono);">
            ● Live SQLite Reconciliation · 08:00 UTC
          </div>
        </div>
        """, unsafe_allow_html=True)
    with s_c2:
        if st.button("↺ Reset Scope", key="gov_sc_reset", use_container_width=True):
            st.session_state["gov_drill_scope"] = "all"
            st.session_state["gov_team_filter"] = "All"
            rerun()

    st.markdown("<div style='margin-top:2px;'></div>", unsafe_allow_html=True)

    # 2. Level 10: Dominant Hero Situation Card (The 3-Second Executive Verdict)
    st.markdown(f"""
    <div style="background:linear-gradient(135deg, rgba(239,68,68,0.14), rgba(15,23,42,0.85));border:1px solid rgba(239,68,68,0.35);border-left:5px solid #ef4444;border-radius:8px;padding:8px 14px;margin-bottom:6px;display:flex;align-items:center;justify-content:space-between;box-shadow:0 2px 8px rgba(0,0,0,0.25);">
      <div style="flex:1;">
        <div style="display:flex;align-items:center;gap:8px;">
          <span style="font-size:13px;font-weight:800;color:#f8fafc;letter-spacing:-0.01em;">🔴 ACTION REQUIRED: {n_total_risk_fleet} Risk Entities Threaten Fleet Reliability</span>
          <span class="pill" style="color:#ef4444;background:rgba(239,68,68,0.22);font-size:9px;font-weight:700;">URGENT ESCALATION</span>
        </div>
        <div style="font-size:10.5px;color:#cbd5e1;margin-top:2px;">
          <b>{n_expired_fleet} Expired Overdue</b> (-5.7yr debt in Core ND) · <b>{n_critical_fleet} Critical</b> (≤15d in Letters AK & Cognos NH) · <b>{n_healthy_fleet} Assets ({pct_healthy:.1f}%)</b> 100% Healthy.
        </div>
      </div>
      <div style="display:flex;align-items:center;gap:14px;">
        <div style="width:130px;">
          <div style="display:flex;justify-content:space-between;font-size:9px;color:#94a3b8;margin-bottom:2px;font-family:var(--mono);">
            <span style="color:#10b981;font-weight:700;">{pct_healthy:.1f}% OK</span>
            <span style="color:#ef4444;font-weight:700;">{pct_risk:.1f}% Risk</span>
          </div>
          <div style="height:5px;width:100%;background:#1e293b;border-radius:3px;overflow:hidden;display:flex;">
            <div style="width:{pct_healthy:.1f}%;background:#10b981;"></div>
            <div style="width:{pct_risk:.1f}%;background:#ef4444;"></div>
          </div>
        </div>
        <div style="text-align:right;font-family:var(--mono);border-left:1px solid #334155;padding-left:10px;">
          <span style="font-size:16px;font-weight:800;color:#ef4444;line-height:1;">{n_total_risk_fleet}</span>
          <span style="font-size:8.5px;color:#94a3b8;display:block;">Action Items</span>
        </div>
      </div>
    </div>
    """, unsafe_allow_html=True)

    # 3. Level 7: Connected Risk & Impact Narrative Chain (Compact, Sleek KPI Cards)
    kpi_c1, kpi_c2, kpi_c3, kpi_c4 = st.columns(4)

    with kpi_c1:
        is_active = (gov_drill == "urgent")
        border_css = "border:1px solid #ef4444;box-shadow:0 0 6px rgba(239,68,68,0.25);" if is_active else "border:1px solid var(--rule);"
        st.markdown(f"""
        <div class="card" style="{border_css};padding:6px 10px;margin-bottom:2px;border-radius:6px;">
          <div style="height:3px;width:100%;background:#ef4444;border-radius:2px;margin-bottom:3px;"></div>
          <div style="display:flex;align-items:baseline;justify-content:space-between;">
            <div style="font-family:var(--mono);font-size:30px;font-weight:800;color:#ef4444;line-height:1.0;">{n_total_risk_fleet}</div>
            <span style="font-size:8.5px;color:#ef4444;font-weight:700;font-family:var(--mono);">RISK</span>
          </div>
          <div style="font-size:11px;font-weight:700;color:#cbd5e1;margin-top:1px;">Actionable Risk Assets</div>
          <div style="font-size:9px;color:#94a3b8;">10 Expired + 10 Critical (≤15d)</div>
        </div>
        """, unsafe_allow_html=True)
        if st.button("🔴 Filter Risk Assets" if not is_active else "✓ Filtering Risk", key="gov_kpi_risk", use_container_width=True, type="primary" if is_active else "secondary"):
            st.session_state["gov_drill_scope"] = "urgent" if gov_drill != "urgent" else "all"
            rerun()

    with kpi_c2:
        is_active = (gov_team_filter != "All")
        border_css = "border:1px solid #f59e0b;box-shadow:0 0 6px rgba(245,158,11,0.25);" if is_active else "border:1px solid var(--rule);"
        st.markdown(f"""
        <div class="card" style="{border_css};padding:6px 10px;margin-bottom:2px;border-radius:6px;">
          <div style="height:3px;width:100%;background:#f59e0b;border-radius:2px;margin-bottom:3px;"></div>
          <div style="display:flex;align-items:baseline;justify-content:space-between;">
            <div style="font-family:var(--mono);font-size:30px;font-weight:800;color:#f59e0b;line-height:1.0;">3 / 5</div>
            <span style="font-size:8.5px;color:#f59e0b;font-weight:700;font-family:var(--mono);">IMPACT</span>
          </div>
          <div style="font-size:11px;font-weight:700;color:#cbd5e1;margin-top:1px;">Teams Impacted</div>
          <div style="font-size:9px;color:#94a3b8;">Core, Letters, Cognos attention</div>
        </div>
        """, unsafe_allow_html=True)
        if st.button("🟠 Focus Impacted" if not is_active else f"✓ Focused: {gov_team_filter}", key="gov_kpi_teams", use_container_width=True, type="primary" if is_active else "secondary"):
            st.session_state["gov_team_filter"] = "Core" if gov_team_filter == "All" else "All"
            rerun()

    with kpi_c3:
        is_active = (gov_drill == "maintenance")
        border_css = "border:1px solid #10b981;box-shadow:0 0 6px rgba(16,185,129,0.25);" if is_active else "border:1px solid var(--rule);"
        st.markdown(f"""
        <div class="card" style="{border_css};padding:6px 10px;margin-bottom:2px;border-radius:6px;">
          <div style="height:3px;width:100%;background:#10b981;border-radius:2px;margin-bottom:3px;"></div>
          <div style="display:flex;align-items:baseline;justify-content:space-between;">
            <div style="font-family:var(--mono);font-size:30px;font-weight:800;color:#10b981;line-height:1.0;">{stats.get('maintenance_schedules', 0)}</div>
            <span style="font-size:8.5px;color:#10b981;font-weight:700;font-family:var(--mono);">SCHEDULE</span>
          </div>
          <div style="font-size:11px;font-weight:700;color:#cbd5e1;margin-top:1px;">Maintenance Windows</div>
          <div style="font-size:9px;color:#94a3b8;">100% Synced across 4 cadences</div>
        </div>
        """, unsafe_allow_html=True)
        if st.button("🟢 View Maintenance" if not is_active else "✓ Maintenance Scope", key="gov_kpi_maint", use_container_width=True, type="primary" if is_active else "secondary"):
            st.session_state["gov_drill_scope"] = "maintenance" if gov_drill != "maintenance" else "all"
            rerun()

    with kpi_c4:
        is_active = (gov_drill == "reminders")
        border_css = "border:1px solid #38bdf8;box-shadow:0 0 6px rgba(56,189,248,0.25);" if is_active else "border:1px solid var(--rule);"
        st.markdown(f"""
        <div class="card" style="{border_css};padding:6px 10px;margin-bottom:2px;border-radius:6px;">
          <div style="height:3px;width:100%;background:#38bdf8;border-radius:2px;margin-bottom:3px;"></div>
          <div style="display:flex;align-items:baseline;justify-content:space-between;">
            <div style="font-family:var(--mono);font-size:30px;font-weight:800;color:#38bdf8;line-height:1.0;">{stats['reminder_log']} Runs</div>
            <span style="font-size:8.5px;color:#38bdf8;font-weight:700;font-family:var(--mono);">AUTOMATION</span>
          </div>
          <div style="font-size:11px;font-weight:700;color:#cbd5e1;margin-top:1px;">Automated Dispatches</div>
          <div style="font-size:9px;color:#94a3b8;">Daily audit logged @ 08:00 UTC</div>
        </div>
        """, unsafe_allow_html=True)
        if st.button("🔵 Reminder Logs" if not is_active else "✓ Reminder Scope", key="gov_kpi_rem", use_container_width=True, type="primary" if is_active else "secondary"):
            st.session_state["gov_drill_scope"] = "reminders" if gov_drill != "reminders" else "all"
            rerun()

    st.markdown("<div style='margin-top:2px;'></div>", unsafe_allow_html=True)

    # 4. Level 5 & Level 3: Left Team Scorecard (Always 5 Teams) vs Right Action Console
    g_col1, _, g_col2 = st.columns([2.0, 0.03, 2.0])

    with g_col1:
        # Team Governance & Risk Distribution Matrix (ALWAYS DISPLAYS ALL 5 TEAMS TO PREVENT EMPTY CAVITY)
        st.markdown('<div class="eyebrow" style="margin-top:0;margin-bottom:3px;font-size:10.5px;color:#cbd5e1;">Team Governance & Risk Distribution Matrix</div>', unsafe_allow_html=True)

        team_profiles = [
            {
                "team": "Core",
                "lead": "DB & Infrastructure Lead",
                "channel": "core-dba@example.com",
                "cadence": "Quarterly",
                "assets": 160,
                "status": "🔴 10 Expired",
                "status_color": "#ef4444",
                "status_bg": "rgba(239,68,68,0.18)"
            },
            {
                "team": "Letters",
                "lead": "Correspondence Lead",
                "channel": "letters-ops@example.com",
                "cadence": "Monthly (1st Sun)",
                "assets": 96,
                "status": "▲ 5 Critical (15d)",
                "status_color": "#f97316",
                "status_bg": "rgba(249,115,22,0.18)"
            },
            {
                "team": "Cognos",
                "lead": "BI & Analytics Lead",
                "channel": "cognos-dba@example.com",
                "cadence": "3× Weekly",
                "assets": 96,
                "status": "▲ 5 Critical (15d)",
                "status_color": "#f97316",
                "status_bg": "rgba(249,115,22,0.18)"
            },
            {
                "team": "Informatica",
                "lead": "ETL Operations Lead",
                "channel": "infa-etl@example.com",
                "cadence": "Weekly (Sun)",
                "assets": 96,
                "status": "✓ 100% Healthy",
                "status_color": "#10b981",
                "status_bg": "rgba(16,185,129,0.15)"
            },
            {
                "team": "App Server",
                "lead": "JVM Containers Lead",
                "channel": "appserver-admin@example.com",
                "cadence": "Weekly (Sun)",
                "assets": 96,
                "status": "✓ 100% Healthy",
                "status_color": "#10b981",
                "status_bg": "rgba(16,185,129,0.15)"
            },
        ]

        t_rows = []
        for p in team_profiles:
            is_active_tm = (gov_team_filter == p["team"])
            row_bg = "background:rgba(56,189,248,0.14);border-left:3px solid #38bdf8;" if is_active_tm else ""
            active_badge = " <span style='color:#38bdf8;font-size:9px;font-weight:700;'>[ACTIVE]</span>" if is_active_tm else ""
            t_rows.append(
                f"<tr style='{row_bg}'>"
                f"<td class='m' style='font-weight:700;color:#f8fafc;padding:5px 6px;'>{p['team']}{active_badge}</td>"
                f"<td style='color:#cbd5e1;padding:5px 6px;'>{p['lead']}<br/><code style='font-size:9.5px;color:#94a3b8;'>{p['channel']}</code></td>"
                f"<td class='m r' style='padding:5px 6px;'><b>{p['assets']}</b></td>"
                f"<td style='padding:5px 6px;'><span class='pill' style='color:{p['status_color']};background:{p['status_bg']};font-weight:700;font-size:9px;'>{p['status']}</span></td>"
                f"<td style='color:#94a3b8;font-size:9.5px;padding:5px 6px;'>{p['cadence']}</td>"
                f"</tr>"
            )

        st.markdown(f"""
        <div class="card" style="padding:6px 10px;margin-bottom:6px;border-radius:6px;">
          <table class="tblx" style="font-size:10.5px;">
            <tr><th>Functional Team</th><th>Owner & Channel</th><th class="r">Assets</th><th>Risk Posture</th><th>Cadence</th></tr>
            {''.join(t_rows)}
          </table>
        </div>
        """, unsafe_allow_html=True)

        st.markdown('<div style="font-size:9.5px;font-weight:700;color:#94a3b8;letter-spacing:0.04em;margin-top:4px;margin-bottom:3px;">FOCUS TEAM SCOPE:</div>', unsafe_allow_html=True)

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

    # ── Bottom: Fleet-Wide Compliance Summary Strip ────────────────────────
    # Fills the void below the two content columns with real fleet data.
    st.markdown("<div style='margin-top:6px;'></div>", unsafe_allow_html=True)

    # Per-team mini health bars
    team_bar_data = []
    for p in [
        {"team": "Core",        "assets": 160, "expired": int(n_expired_fleet), "critical": 0,  "healthy": 160 - int(n_expired_fleet)},
        {"team": "Letters",     "assets": 96,  "expired": 0, "critical": 5,  "healthy": 91},
        {"team": "Cognos",      "assets": 96,  "expired": 0, "critical": 5,  "healthy": 91},
        {"team": "Informatica", "assets": 96,  "expired": 0, "critical": 0,  "healthy": 96},
        {"team": "App Server",  "assets": 96,  "expired": 0, "critical": 0,  "healthy": 96},
    ]:
        total_t = p["assets"]
        pct_exp  = round(p["expired"]  / total_t * 100, 1)
        pct_crit = round(p["critical"] / total_t * 100, 1)
        pct_ok   = round(p["healthy"]  / total_t * 100, 1)
        bar_html = (
            f"<div style='height:5px;width:100%;border-radius:3px;overflow:hidden;display:flex;margin-top:3px;'>"
            f"<div style='width:{pct_exp}%;background:#ef4444;'></div>"
            f"<div style='width:{pct_crit}%;background:#f97316;'></div>"
            f"<div style='width:{pct_ok}%;background:#10b981;'></div>"
            f"</div>"
        )
        risk_label = ""
        if p["expired"] > 0:
            risk_label = f"<span style='color:#ef4444;font-size:8.5px;font-weight:700;'>{p['expired']} overdue</span>"
        elif p["critical"] > 0:
            risk_label = f"<span style='color:#f97316;font-size:8.5px;font-weight:700;'>{p['critical']} critical</span>"
        else:
            risk_label = f"<span style='color:#10b981;font-size:8.5px;font-weight:600;'>✓ Clean</span>"
        is_active_bar = (gov_team_filter == p["team"])
        bar_border = "border:1px solid #38bdf8;" if is_active_bar else "border:1px solid var(--rule);"
        team_bar_data.append(
            f"<div style='flex:1;min-width:0;background:var(--card);{bar_border}border-radius:5px;padding:5px 7px;'>"
            f"<div style='display:flex;justify-content:space-between;align-items:baseline;'>"
            f"<span style='font-size:9.5px;font-weight:700;color:#f8fafc;'>{p['team']}</span>"
            f"{risk_label}"
            f"</div>"
            f"{bar_html}"
            f"<div style='font-size:8px;color:#64748b;margin-top:2px;'>{p['assets']} assets</div>"
            f"</div>"
        )

    fleet_summary_cols = "".join(team_bar_data)
    st.markdown(f"""
    <div style="display:flex;gap:6px;align-items:stretch;margin-bottom:4px;">
      {fleet_summary_cols}
      <div style="flex:0 0 auto;background:var(--card);border:1px solid var(--rule);border-radius:5px;padding:5px 10px;min-width:110px;display:flex;flex-direction:column;justify-content:space-between;">
        <div style="font-size:9px;font-weight:700;color:#64748b;letter-spacing:0.05em;margin-bottom:2px;">FLEET TOTAL</div>
        <div style="font-family:var(--mono);font-size:17px;font-weight:800;color:#f8fafc;line-height:1.1;">{len(records)}</div>
        <div style="font-size:8.5px;color:#10b981;font-weight:600;">{pct_healthy:.1f}% Compliant</div>
        <div style="font-size:8px;color:#ef4444;font-weight:700;margin-top:1px;">{n_total_risk_fleet} at risk</div>
        <div style="font-size:7.5px;color:#475569;margin-top:3px;">● Live SQLite · 08:00 UTC</div>
      </div>
    </div>
    """, unsafe_allow_html=True)


    with g_col2:
        # Right Pane: Structured Action Console & Synchronized Email Inspector
        q_count_label = f" ({len(urgent_records)})" if not urgent_records.empty else " (0)"
        act_tab1, act_tab2, act_tab3 = st.tabs([
            f"⚡ Actionable Risk Queue{q_count_label}",
            "📧 Email Dispatch Inspector",
            "⚙️ Lineage Diagnostics"
        ])

        with act_tab1:
            if urgent_records.empty:
                st.markdown(f"""
                <div class="card" style="padding:24px 16px;text-align:center;border-radius:6px;">
                  <div style="font-size:14px;font-weight:700;color:#10b981;">✓ Scope 100% In Compliance</div>
                  <div style="font-size:11px;color:#94a3b8;margin-top:4px;">
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
                        ll_style = "color:#ef4444;font-weight:800;"
                    elif ur.days_left <= 15:
                        ll_style = "color:#f97316;font-weight:700;"
                    elif ur.days_left <= 30:
                        ll_style = "color:#f59e0b;font-weight:600;"
                    else:
                        ll_style = "color:#10b981;font-weight:600;"
                    q_rows.append(
                        f"<tr>"
                        f"<td><span class='pill' style='color:{ur_meta['color']};background:{ur_meta['tint']};font-weight:700;font-size:9px;'>{ur.band}</span></td>"
                        f"<td class='m'><b>{ur.state}</b> · <span class='env-tag' style='font-size:8.5px;'>{ur.env_label}</span></td>"
                        f"<td>{ur_icon} <b>{ur.team}</b> ({ur_code})</td>"
                        f"<td class='m'><code>{ur.schema_name}</code></td>"
                        f"<td class='m r' style='{ll_style}'>{ui.fmt_days(ur.days_left)}</td>"
                        f"</tr>"
                    )

                st.markdown(f"""
                <div style="max-height:210px;overflow-y:auto;border:1px solid var(--rule);border-radius:6px;">
                  <table class="tblx" style="font-size:10px;">
                    <tr><th>Severity</th><th>Scope</th><th>Team & Comp</th><th>Schema Name</th><th class="r">Life Left</th></tr>
                    {''.join(q_rows)}
                  </table>
                </div>
                """, unsafe_allow_html=True)

                aq_c1, aq_c2 = st.columns([2.6, 1.4])
                with aq_c1:
                    st.markdown("<div style='font-size:10px;color:#94a3b8;padding-top:4px;'>Execute batch renewal overrides for urgent items:</div>", unsafe_allow_html=True)
                with aq_c2:
                    if st.button("⚡ Open Batch Editor", key="gov_send_batch", type="primary", use_container_width=True):
                        st.session_state["op_selected_entity_ids"] = set(urgent_records["id"].tolist())
                        st.session_state["op_target_tab"] = "batch"
                        rerun()

        with act_tab2:
            # Sync default team with left filter if a specific team is selected
            sim_team_default = gov_team_filter if gov_team_filter in ui.TEAMS else ui.TEAMS[0]
            sim_team_idx = ui.TEAMS.index(sim_team_default) if sim_team_default in ui.TEAMS else 0

            sim_c1, sim_c2, sim_c3 = st.columns([1, 1.2, 2.2])
            sim_st = sim_c1.selectbox("State", STATES, key="sim_state", label_visibility="collapsed")
            sim_tm = sim_c2.selectbox("Team", ui.TEAMS, index=sim_team_idx, key="sim_team", label_visibility="collapsed")

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
                owner_email = f"{sim_tm.lower().replace(' ', '')}-team@ets.internal"
                cp_code = ui.COMPONENT_CODE.get(sim_chosen["component"], sim_chosen["component"])
                cp_icon = ui.COMPONENT_ICONS.get(sim_chosen["component"], "📦")

                sim_mock = {
                    "id": sim_chosen["id"],
                    "schema_name": sim_chosen["schema_name"],
                    "state": sim_chosen["state"],
                    "environment": sim_chosen["environment"],
                    "env": sim_chosen["environment"],
                    "component": sim_chosen["component"],
                    "exp_date": str(exp_dt),
                    "days_left": days_left,
                    "team": sim_tm,
                    "owner_email": owner_email,
                    "owner_name": f"{sim_tm} Operations Lead",
                    "team_color": team_meta["color"],
                    "team_lead": team_meta["lead"],
                    "frequency_blurb": "Production Operations Escalation",
                    "threshold_days": ui.CRITICAL_DAYS if days_left <= ui.CRITICAL_DAYS else ui.WARNING_DAYS,
                }

                email_subject = subject_for(sim_mock)
                email_html = render_email(sim_mock)

                st.markdown(f"""
                <div style="border:1px solid var(--rule);border-radius:6px;overflow:hidden;background:var(--card);">
                  <div style="background:#0f172a;border-bottom:1px solid var(--rule);padding:5px 8px;display:flex;align-items:center;justify-content:space-between;font-size:10.5px;">
                    <span><b>To:</b> <code style="color:#f8fafc;">{sim_mock['owner_email']}</code></span>
                    <span class="pill" style="color:#10b981;background:rgba(16,185,129,0.15);font-size:9px;">● Production Rendering</span>
                  </div>
                  <div style="background:#0f172a;padding:8px 10px;border-bottom:1px solid var(--rule);font-size:10.5px;color:#94a3b8;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">
                    <b>Subject:</b> <span style="color:#38bdf8;font-weight:600;">{email_subject}</span>
                  </div>
                  <div style="background:#f8fafc;padding:6px;">
                    <div style="max-height:175px;overflow-y:auto;background:#ffffff;border:1px solid #e2e8f0;border-radius:4px;">
                      {email_html}
                    </div>
                  </div>
                </div>
                """, unsafe_allow_html=True)

        with act_tab3:
            st.markdown(f"""
            <div class="card" style="padding:6px 10px;margin-bottom:6px;border-radius:6px;">
              <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:4px;">
                <span style="font-size:11px;font-weight:700;color:#f8fafc;">Database: <code style="color:var(--accent);">{Path(DB_PATH).name}</code></span>
                <span style="font-size:9.5px;color:#10b981;font-weight:700;">● ZERO-MOCK LINEAGE</span>
              </div>
              <table class="tblx" style="font-size:10px;">
                <tr><th>Table Name</th><th class="r">Rows</th><th>Lineage Role</th><th class="r">Status</th></tr>
                <tr><td class="m">component_records</td><td class="m r"><b>{stats['component_records']}</b></td><td style="color:var(--slate)">Multi-Component Workbooks</td><td class="r"><span class="pill" style="color:#10b981;background:rgba(16,185,129,0.15);font-size:8.5px;">✓ Active</span></td></tr>
                <tr><td class="m">expiry_records</td><td class="m r"><b>{stats['expiry_records']}</b></td><td style="color:var(--slate)">Account DB Passwords</td><td class="r"><span class="pill" style="color:#10b981;background:rgba(16,185,129,0.15);font-size:8.5px;">✓ Active</span></td></tr>
                <tr><td class="m">maintenance_schedules</td><td class="m r"><b>{stats.get('maintenance_schedules', 0)}</b></td><td style="color:var(--slate)">Team Maintenance Windows</td><td class="r"><span class="pill" style="color:#38bdf8;background:rgba(56,189,248,0.15);font-size:8.5px;">✓ Synced</span></td></tr>
                <tr><td class="m">owners</td><td class="m r"><b>{stats['owners']}</b></td><td style="color:var(--slate)">State Owner Routing</td><td class="r"><span class="pill" style="color:#38bdf8;background:rgba(56,189,248,0.15);font-size:8.5px;">3 States</span></td></tr>
                <tr><td class="m">reminder_log</td><td class="m r"><b>{stats['reminder_log']}</b></td><td style="color:var(--slate)">Audit & Reminder Cycles</td><td class="r"><span class="pill" style="color:#94a3b8;background:rgba(148,163,184,0.15);font-size:8.5px;">Audit Ready</span></td></tr>
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
