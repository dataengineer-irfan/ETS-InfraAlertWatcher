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

    # 2. Executive Metric Ribbon (Ultra-compact 42px)
    tot_cnt = len(filtered)
    exp_cnt = int((filtered["days_left"] < 0).sum())
    crit_cnt = int((filtered["days_left"].between(0, ui.CRITICAL_DAYS)).sum())
    warn_cnt = int((filtered["days_left"].between(ui.CRITICAL_DAYS + 1, ui.WARNING_DAYS)).sum())
    hlth_cnt = int((filtered["days_left"] > ui.WARNING_DAYS).sum())

    k1, k2, k3, k4 = st.columns(4)
    with k1:
        st.markdown(f"""
        <div class="top-glow-kpi" style="--glow:#38bdf8;padding:4px 10px;margin-bottom:2px;">
          <div class="kpi-label" style="font-size:9.5px;">Entities in View</div>
          <div class="kpi-value" style="font-size:17px;line-height:1.1;">{tot_cnt}</div>
          <div class="kpi-sub" style="font-size:9.5px;">Across active filters</div>
        </div>
        """, unsafe_allow_html=True)
    with k2:
        st.markdown(f"""
        <div class="top-glow-kpi" style="--glow:{'#ef4444' if exp_cnt else '#10b981'};padding:4px 10px;margin-bottom:2px;">
          <div class="kpi-label" style="font-size:9.5px;">Expired Items</div>
          <div class="kpi-value" style="font-size:17px;line-height:1.1;">{exp_cnt}</div>
          <div class="kpi-sub" style="font-size:9.5px;">{'Requires renewal' if exp_cnt else 'Zero expired'}</div>
        </div>
        """, unsafe_allow_html=True)
    with k3:
        st.markdown(f"""
        <div class="top-glow-kpi" style="--glow:{'#f97316' if crit_cnt else '#f59e0b' if warn_cnt else '#10b981'};padding:4px 10px;margin-bottom:2px;">
          <div class="kpi-label" style="font-size:9.5px;">Critical & Warning</div>
          <div class="kpi-value" style="font-size:17px;line-height:1.1;">{crit_cnt + warn_cnt}</div>
          <div class="kpi-sub" style="font-size:9.5px;">{crit_cnt} crit · {warn_cnt} warn</div>
        </div>
        """, unsafe_allow_html=True)
    with k4:
        st.markdown(f"""
        <div class="top-glow-kpi" style="--glow:#10b981;padding:4px 10px;margin-bottom:2px;">
          <div class="kpi-label" style="font-size:9.5px;">Healthy Entities</div>
          <div class="kpi-value" style="font-size:17px;line-height:1.1;">{hlth_cnt}</div>
          <div class="kpi-sub" style="font-size:9.5px;">{(hlth_cnt/tot_cnt*100) if tot_cnt else 0:.0f}% healthy fleet</div>
        </div>
        """, unsafe_allow_html=True)

    st.markdown("<div style='margin-top:2px;'></div>", unsafe_allow_html=True)

    # 3. Master-Detail Workspace (40% Left / 60% Right)
    left_col, _, right_col = st.columns([1.7, 0.04, 2.3])

    with left_col:
        if filtered.empty:
            st.markdown(ui.empty("No records match filter", "Try broadening your search query or reset filters."), unsafe_allow_html=True)
            selected_id = None
        else:
            cur_active_id = st.session_state.get("op_active_id")
            if cur_active_id not in filtered["id"].values:
                cur_active_id = int(filtered.iloc[0]["id"])
                st.session_state["op_active_id"] = cur_active_id
            selected_id = cur_active_id

            st.markdown(f"""
            <div style="display:flex;align-items:center;justify-content:space-between;padding:2px 2px 3px;">
              <span style="font-size:11.5px;font-weight:600;color:#f8fafc;">
                Showing <b style="color:#38bdf8;font-family:var(--mono);font-size:12.5px;">{len(filtered)}</b> entities
              </span>
              <span style="font-size:9.5px;font-weight:600;color:#94a3b8;font-family:var(--mono);background:var(--sunk);border:1px solid var(--rule);border-radius:4px;padding:1px 5px;">
                Soonest Expiry First
              </span>
            </div>
            """, unsafe_allow_html=True)

            st.markdown("<div style='max-height:290px;overflow-y:auto;border:1px solid var(--rule);border-radius:6px;padding:2px;'>", unsafe_allow_html=True)
            for r in filtered.head(30).itertuples():
                meta = ui.BAND_META.get(r.band, ui.BAND_META["Healthy"])
                t_color = ui.TEAM_META.get(r.team, {}).get("color", "#38BDF8")
                is_active = (r.id == selected_id)
                bg_active = "background:rgba(56,189,248,0.18);border:1px solid #38bdf8;box-shadow:inset 2px 0 0 #38bdf8;" if is_active else "background:rgba(255,255,255,0.02);border:1px solid rgba(255,255,255,0.05);"

                c_row1, c_row2 = st.columns([4.2, 1.1])
                with c_row1:
                    st.markdown(f"""
                    <div style="{bg_active};border-radius:4px;padding:3px 6px;margin-bottom:2px;display:flex;align-items:center;justify-content:space-between;">
                      <div style="display:flex;align-items:center;gap:4px;overflow:hidden;">
                        <span style="color:{meta['color']};font-weight:700;font-size:11.5px;">{meta['symbol']}</span>
                        <span style="font-weight:700;color:#f8fafc;font-size:10.5px;">{r.state}</span>
                        <span class="env-tag" style="background:rgba(255,255,255,0.06);color:{t_color};border:1px solid {t_color}55;font-size:8.5px;padding:0 3px;">{r.team}</span>
                        <span style="font-family:var(--mono);font-size:10px;font-weight:{'700' if is_active else '500'};color:{'#38bdf8' if is_active else '#f8fafc'};">{r.schema_name}</span>
                        <span class="env-tag" style="font-size:8px;padding:0 2px;">{r.env_label}</span>
                      </div>
                      <span style="color:{meta['color']};font-weight:700;font-size:10px;font-family:var(--mono);flex:none;margin-left:3px;">{ui.fmt_days(r.days_left)}</span>
                    </div>
                    """, unsafe_allow_html=True)
                with c_row2:
                    if st.button("Inspect", key=f"op_row_btn_{r.id}", type="primary" if is_active else "secondary", use_container_width=True):
                        st.session_state["op_active_id"] = r.id
                        rerun()
            st.markdown("</div>", unsafe_allow_html=True)

    with right_col:
        if selected_id is None:
            st.markdown(ui.empty("Select a record", "Choose an item from the master list on the left to inspect."), unsafe_allow_html=True)
            return

        rec = df[df["id"] == selected_id].iloc[0]
        meta = ui.BAND_META.get(rec["band"], ui.BAND_META["Healthy"])
        team_meta = ui.TEAM_META.get(rec["team"], ui.TEAM_META["Core"])

        # Top Inspector Header & Quick Renewal Bar (Compact 38px)
        st.markdown(f"""
        <div class="card" style="border-left:4px solid {meta['color']};margin-bottom:4px;padding:5px 10px;">
          <div style="display:flex;align-items:center;justify-content:space-between;">
            <div>
              <span style="font-size:9px;font-weight:700;letter-spacing:.08em;color:{meta['color']}">ENTITY #{rec['id']} · {rec['state']} · <span style="color:{team_meta['color']}">{rec['team']}</span> · {rec['component']}</span>
              <div style="font-size:14px;font-weight:700;font-family:var(--mono);color:#f8fafc;margin-top:1px;">{rec['schema_name']} <span class="env-tag" style="font-size:9.5px;">{rec['env_label']}</span></div>
            </div>
            {ui.status_pill(rec['band'])}
          </div>
        </div>
        """, unsafe_allow_html=True)

        # Instant Action & Renewal Bar
        act_b1, act_b2, act_b3, act_b4 = st.columns([1, 1, 1.6, 1.2])
        cur_dt = rec["exp_dt"].date()
        if act_b1.button("+90 Days", key=f"op_top_p90_{rec['id']}", use_container_width=True):
            target_dt = cur_dt + pd.Timedelta(days=90)
            apply_edits([(rec["id"], target_dt)])
            st.success(f"Extended +90 days to {target_dt}")
            rerun()
        if act_b2.button("+1 Year", key=f"op_top_p365_{rec['id']}", use_container_width=True):
            target_dt = cur_dt + pd.Timedelta(days=365)
            apply_edits([(rec["id"], target_dt)])
            st.success(f"Extended +1 year to {target_dt}")
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
            st.markdown('<div class="note" style="margin-bottom:4px;">Portfolio 2D Matrix Cross-Tab Summary:</div>', unsafe_allow_html=True)
            mat_states = STATES
            mat_comps = COMPONENT_ORDER
            header_cols = "".join(f"<th style='text-align:center;'>{ui.COMPONENT_CODE.get(c, c)}</th>" for c in mat_comps)
            matrix_head = f"<tr><th>State</th>{header_cols}<th class='r'>Total</th></tr>"

            matrix_rows = []
            for st_val in mat_states:
                st_sub = df[df["state"] == st_val]
                cell_tds = []
                for c_val in mat_comps:
                    cell_sub = st_sub[st_sub["component"] == c_val]
                    if cell_sub.empty:
                        cell_tds.append("<td class='c' style='color:var(--mute);'>—</td>")
                    else:
                        c_cnt = len(cell_sub)
                        worst_b = ui.worst_band(cell_sub["band"].tolist())
                        meta = ui.BAND_META.get(worst_b, ui.BAND_META["Healthy"])
                        cell_tds.append(
                            f"<td class='c'>"
                            f"<span class='matrix-cell-badge' style='background:{meta['tint']};color:{meta['color']};border:1px solid {meta['color']}33;font-size:9.5px;padding:1px 4px;'>"
                            f"<b>{meta['symbol']}</b> {c_cnt}"
                            f"</span></td>"
                        )
                st_tot = len(st_sub)
                matrix_rows.append(
                    f"<tr><td style='font-weight:700;font-family:var(--mono);'>{st_val}</td>{''.join(cell_tds)}<td class='m r' style='font-weight:700;color:var(--accent);'>{st_tot}</td></tr>"
                )
            st.markdown(f"<div style='border:1px solid var(--rule);border-radius:6px;overflow:hidden;'><table class='tblx' style='font-size:10.5px;'>{matrix_head}{''.join(matrix_rows)}</table></div>", unsafe_allow_html=True)
            st.markdown("</div>", unsafe_allow_html=True)

        with i_tab3:
            st.markdown("<div style='max-height:200px;overflow-y:auto;padding-right:2px;'>", unsafe_allow_html=True)
            batch_work = filtered.head(50).copy()
            if hasattr(st, "data_editor") and hasattr(st, "column_config"):
                b_view = batch_work[["schema_name", "state", "team", "env_label", "component", "exp_dt", "band", "days_left"]].copy()
                b_view["exp_dt"] = b_view["exp_dt"].dt.date
                b_view["days_left"] = b_view["days_left"].apply(ui.fmt_days)
                b_view["band"] = b_view["band"].apply(ui.health_text)

                b_edited = st.data_editor(
                    b_view, key="op_batch_editor", hide_index=True, use_container_width=True,
                    num_rows="fixed", height=155,
                    column_config={
                        "schema_name": st.column_config.TextColumn("Schema Name", disabled=True, width="medium"),
                        "state": st.column_config.TextColumn("State", disabled=True, width="small"),
                        "team": st.column_config.TextColumn("Team", disabled=True, width="small"),
                        "env_label": st.column_config.TextColumn("Env", disabled=True, width="small"),
                        "component": st.column_config.TextColumn("Component", disabled=True, width="medium"),
                        "exp_dt": st.column_config.DateColumn("Expiry Date", format="YYYY-MM-DD", required=True, width="medium"),
                        "band": st.column_config.TextColumn("Status", disabled=True, width="small"),
                        "days_left": st.column_config.TextColumn("Time Left", disabled=True, width="small"),
                    },
                )

                b_ids = batch_work["id"].tolist()
                b_changes = []
                for b_pos, b_rec_id in enumerate(b_ids):
                    b_before = b_view.iloc[b_pos]["exp_dt"]
                    b_after = b_edited.iloc[b_pos]["exp_dt"]
                    if b_after is not None and not pd.isna(b_after):
                        b_after = pd.to_datetime(b_after).date()
                        if b_after != b_before:
                            b_changes.append((b_rec_id, b_after))

                b_btn_col, b_note_col = st.columns([1.2, 2])
                if b_btn_col.button("Save Batch Changes", type="primary", key="op_save_batch_btn", disabled=not b_changes, use_container_width=True):
                    apply_edits(b_changes)
                    st.success(f"Saved {len(b_changes)} batch updates!")
                    rerun()
                b_note_col.markdown(f"<div style='font-size:10.5px;color:#94a3b8;padding-top:4px;'><b>{len(b_changes)}</b> unsaved change(s)</div>", unsafe_allow_html=True)
            st.markdown("</div>", unsafe_allow_html=True)

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
# View 3: Pipeline Governance & Alert Center (Zero-Scroll Control Center)
# ==========================================================================
def render_governance_center() -> None:
    # Top KPI summary strip (Ultra-compact zero-scroll)
    conn = get_connection(DB_PATH)
    tables = ["component_records", "expiry_records", "maintenance_schedules", "owners", "reminder_log"]
    stats = {}
    for t in tables:
        try:
            stats[t] = conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
        except Exception:
            stats[t] = 0
    conn.close()

    kpi_c1, kpi_c2, kpi_c3, kpi_c4 = st.columns(4)
    with kpi_c1:
        st.markdown(f"""
        <div class="top-glow-kpi" style="--glow:#38bdf8;padding:6px 12px;">
          <div class="kpi-label">Component Entities</div>
          <div class="kpi-value" style="font-size:20px;">{stats['component_records']}</div>
          <div class="kpi-sub">Across AK, NH, ND portfolios</div>
        </div>
        """, unsafe_allow_html=True)
    with kpi_c2:
        st.markdown(f"""
        <div class="top-glow-kpi" style="--glow:#10b981;padding:6px 12px;">
          <div class="kpi-label">Database Passwords</div>
          <div class="kpi-value" style="font-size:20px;">{stats['expiry_records']}</div>
          <div class="kpi-sub">Tracked in SQLite table</div>
        </div>
        """, unsafe_allow_html=True)
    with kpi_c3:
        st.markdown(f"""
        <div class="top-glow-kpi" style="--glow:#f59e0b;padding:6px 12px;">
          <div class="kpi-label">Maintenance Windows</div>
          <div class="kpi-value" style="font-size:20px;">{stats.get('maintenance_schedules', 0)} Schedules</div>
          <div class="kpi-sub">Cognos, Infa, Letters, App Server</div>
        </div>
        """, unsafe_allow_html=True)
    with kpi_c4:
        st.markdown(f"""
        <div class="top-glow-kpi" style="--glow:#6366f1;padding:6px 12px;">
          <div class="kpi-label">Reminder Cycles</div>
          <div class="kpi-value" style="font-size:20px;">{stats['reminder_log']} Dispatched</div>
          <div class="kpi-sub">Daily Schedule @ 08:00 UTC</div>
        </div>
        """, unsafe_allow_html=True)

    st.markdown("<div style='margin-top:6px;'></div>", unsafe_allow_html=True)
    
    # Primary Business View: Multi-Team Alert Routing & Email Dispatch Simulator
    g_col1, _, g_col2 = st.columns([1.8, 0.04, 2.2])

    with g_col1:
        st.markdown('<div class="eyebrow" style="margin-top:0;margin-bottom:4px;">Multi-Team Alert Routing Directory</div>', unsafe_allow_html=True)
        team_routes = [
            ("Cognos", "BI & Analytics", "cognos-dba@example.com", "#818CF8", "Thrice-weekly (Sun/Tue/Fri)"),
            ("Informatica", "ETL Team", "infa-etl@example.com", "#FB923C", "Weekly on Sundays"),
            ("Letters", "Correspondence", "letters-ops@example.com", "#34D399", "Monthly (1st Sun)"),
            ("App Server", "JVM Containers", "appserver-admin@example.com", "#FBBF24", "Weekly on Sundays"),
            ("Core", "DB & Infra", "core-dba@example.com", "#38BDF8", "Quarterly Maintenance"),
        ]
        route_rows = "".join(
            f"<tr><td class='m' style='color:{color};font-weight:700;'>{team}</td>"
            f"<td style='color:#f8fafc;'>{lead}</td>"
            f"<td class='m'><code>{email}</code></td>"
            f"<td style='color:var(--slate);'>{cadence}</td></tr>"
            for team, lead, email, color, cadence in team_routes
        )
        st.markdown(f"""
        <div class="card" style="padding:6px 10px;margin-bottom:8px;">
          <table class="tblx" style="font-size:11px;">
            <tr><th>Team</th><th>Lead</th><th>Recipient</th><th>Cadence</th></tr>
            {route_rows}
          </table>
        </div>
        """, unsafe_allow_html=True)

    with g_col2:
        st.markdown('<div class="eyebrow" style="margin-top:0;margin-bottom:4px;">Dynamic Email Alert Dispatch Simulator</div>', unsafe_allow_html=True)

        sim_c1, sim_c2, sim_c3 = st.columns([1, 1.2, 2.2])
        sim_st = sim_c1.selectbox("State", STATES, key="sim_state", label_visibility="collapsed")
        sim_tm = sim_c2.selectbox("Team", ui.TEAMS, key="sim_team", label_visibility="collapsed")

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

            team_email = {
                "Cognos": "cognos-dba@example.com",
                "Informatica": "infa-etl@example.com",
                "Letters": "letters-ops@example.com",
                "App Server": "appserver-admin@example.com",
                "Core": "core-dba@example.com"
            }.get(sim_tm, "ops-team@example.com")

            sim_mock = {
                "state": sim_chosen["state"],
                "env": sim_chosen["environment"],
                "team": sim_chosen["team"],
                "component": sim_chosen["component"],
                "username": f"{sim_chosen['state']}{sim_chosen['team'][:3].upper()}USR",
                "schema_name": sim_chosen["schema_name"],
                "exp_date": sim_chosen["exp_date"],
                "days_left": days_left if days_left > 0 else 16,
                "owner_name": f"{sim_chosen['team']} Operations",
                "owner_email": team_email,
                "is_first_reminder": True
            }

            st.markdown(f"""
            <div class="card" style="margin-bottom:6px;padding:6px 10px;font-size:11.5px;">
              <div style="display:flex;align-items:center;justify-content:space-between;">
                <span><b>Recipient:</b> <code style="color:#f8fafc;">{sim_mock['owner_email']}</code></span>
                <span style="color:#38bdf8;font-weight:700;">{sim_chosen['state']} · {sim_chosen['team']}</span>
              </div>
              <div style="margin-top:2px;color:#cbd5e1;"><b>Subject:</b> <code style="color:#38bdf8;font-size:10.5px;">{subject_for(sim_mock)}</code></div>
            </div>
            """, unsafe_allow_html=True)

            email_html = render_email(sim_mock)
            st.markdown(f"""
            <div style="border:1px solid var(--rule);border-radius:7px;overflow-y:auto;max-height:260px;background:#ffffff;padding:0;">
              {email_html}
            </div>
            """, unsafe_allow_html=True)

    # Technical Diagnostics Disclosure (Demoted / Collapsed by default)
    with st.expander("System Diagnostics", expanded=False):
        d_c1, d_c2 = st.columns([2.5, 1.5])
        with d_c1:
            st.markdown(f"""
            <div class="card" style="padding:6px 10px;margin-bottom:6px;">
              <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:6px;">
                <span style="font-size:11.5px;font-weight:700;color:#f8fafc;">Database: <code style="color:var(--accent);">{Path(DB_PATH).name}</code></span>
                <span style="font-size:10px;color:#10b981;font-weight:700;">● ZERO-MOCK ACTIVE</span>
              </div>
              <table class="tblx" style="font-size:11px;">
                <tr><th>Table Name</th><th class="r">Rows</th><th>Lineage Role</th><th class="r">Status</th></tr>
                <tr><td class="m">component_records</td><td class="m r"><b>{stats['component_records']}</b></td><td style="color:var(--slate)">Multi-Component Workbooks</td><td class="r"><span class="pill" style="color:#10b981;background:rgba(16,185,129,0.15)">✓ Active</span></td></tr>
                <tr><td class="m">expiry_records</td><td class="m r"><b>{stats['expiry_records']}</b></td><td style="color:var(--slate)">Account DB Passwords</td><td class="r"><span class="pill" style="color:#10b981;background:rgba(16,185,129,0.15)">✓ Active</span></td></tr>
                <tr><td class="m">maintenance_schedules</td><td class="m r"><b>{stats.get('maintenance_schedules', 0)}</b></td><td style="color:var(--slate)">Team Maintenance Windows</td><td class="r"><span class="pill" style="color:#38bdf8;background:rgba(56,189,248,0.15)">✓ Synced</span></td></tr>
                <tr><td class="m">owners</td><td class="m r"><b>{stats['owners']}</b></td><td style="color:var(--slate)">State Owner Routing</td><td class="r"><span class="pill" style="color:#38bdf8;background:rgba(56,189,248,0.15)">3 States</span></td></tr>
                <tr><td class="m">reminder_log</td><td class="m r"><b>{stats['reminder_log']}</b></td><td style="color:var(--slate)">Audit & Reminder Cycles</td><td class="r"><span class="pill" style="color:#94a3b8;background:rgba(148,163,184,0.15)">Audit Ready</span></td></tr>
              </table>
            </div>
            """, unsafe_allow_html=True)
        with d_c2:
            st.markdown("<div style='font-size:11.5px;color:#94a3b8;margin-bottom:6px;'>AST Parser & Excel Sync:</div>", unsafe_allow_html=True)
            if st.button("Trigger Immediate Re-ingest (AST Parser)", key="gov_reingest", type="primary", use_container_width=True):
                t_start = datetime.now()
                with st.spinner("Executing workbook parser..."):
                    res = run_ingest(WORKBOOK_DIR, DB_PATH)
                duration_ms = (datetime.now() - t_start).total_seconds() * 1000
                bust_cache()
                st.success(f"Ingested {res['total_rows_read']} records in {duration_ms:.1f}ms ({res['new']} new, {res['renewed']} renewed).")
                rerun()


# ==========================================================================
# Primary Navigation (3 Unified Enterprise Workspaces)
# ==========================================================================
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

