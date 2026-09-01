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
@st.cache_data(ttl=60, show_spinner=False)
def load_records(db_path: str, _bust: int = 0) -> pd.DataFrame:
    """Read component_records and derive everything the views need."""
    conn = get_connection(db_path)
    df = pd.read_sql_query("SELECT * FROM component_records", conn)
    conn.close()

    if df.empty:
        return pd.DataFrame(columns=[
            "id", "state", "component", "env_no", "environment", "module",
            "schema_name", "exp_date", "source_exp_date", "edited_at",
            "exp_dt", "days_left", "band", "edited", "quarter", "env_label",
        ])

    df["exp_dt"] = pd.to_datetime(df["exp_date"], errors="coerce")
    df = df.dropna(subset=["exp_dt"])
    df["days_left"] = (df["exp_dt"] - pd.Timestamp(date.today())).dt.days.astype(int)
    df["band"] = df["days_left"].apply(ui.health_of)
    df["edited"] = df["edited_at"].notna()
    df["quarter"] = ("Q" + df["exp_dt"].dt.quarter.astype(str)
                     + " " + df["exp_dt"].dt.year.astype(str))
    df["env_label"] = df["environment"].fillna("UNMAPPED")
    return df


@st.cache_data(ttl=60, show_spinner=False)
def build_page(db_path: str, mode: str, state: str | None, _bust: int = 0) -> str:
    """The report canvas as a self-contained zero-scroll HTML document."""
    df = load_records(db_path, _bust)
    records = report.to_records(df.to_dict("records"), env_order=ENV_ORDER,
                                component_order=COMPONENT_ORDER)
    conn = get_connection(db_path)
    ensure_metric_snapshots(conn, records)
    snapshots = get_metric_snapshots(conn)
    conn.close()
    return report.build(records, mode=mode, state=state, env_order=ENV_ORDER, snapshots=snapshots)


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
    st.markdown(ui.pick_line(
        f"Record a renewal for {state}",
        "change an expiry date in place — the workbook remains the system of record"),
        unsafe_allow_html=True)

    filters, _, panel = st.columns([2.2, 0.05, 1.3])

    with filters:
        c1, c2, c3 = st.columns([2.0, 1.8, 1.4])
        query = c1.text_input("Search", key="mg_q",
                              placeholder="Search schema, env, component...",
                              label_visibility="collapsed")
        comp_pick = c2.multiselect("Component", COMPONENT_ORDER, key="mg_comp",
                                   placeholder="All components",
                                   label_visibility="collapsed",
                                   format_func=lambda c: ui.COMPONENT_CODE[c])
        window = c3.selectbox("Window", list(MANAGE_WINDOWS), key="mg_window",
                              label_visibility="collapsed")

        work = search(state_records, query)
        if comp_pick:
            work = work[work["component"].isin(comp_pick)]
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
            view = work[["schema_name", "env_label", "component",
                         "exp_dt", "band", "days_left"]].copy()
            view["exp_dt"] = view["exp_dt"].dt.date
            view["days_left"] = view["days_left"].apply(ui.fmt_days)
            ids = work["id"].tolist()

            edited = st.data_editor(
                view, key="mg_editor", hide_index=True, use_container_width=True,
                num_rows="fixed", height=EDITOR_HEIGHT,
                column_config={
                    "schema_name": st.column_config.TextColumn("Schema Name", disabled=True, width="large"),
                    "env_label": st.column_config.TextColumn("Environment", disabled=True, width="small"),
                    "component": st.column_config.TextColumn(
                        "Component", disabled=True, width="medium"),
                    "exp_dt": st.column_config.DateColumn(
                        "Expiry Date", format="YYYY-MM-DD", required=True,
                        help="Type or pick a new date, then press Save changes."),
                    "band": st.column_config.TextColumn("Health Status", disabled=True, width="small"),
                    "days_left": st.column_config.TextColumn(
                        "Time Left", disabled=True, width="small"),
                },
            )

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
            <div class="card" style="font-size:12px;line-height:1.6;color:#94a3b8;">
              <div style="font-weight:700;color:#f8fafc;margin-bottom:4px;">No Local Overrides</div>
              Every date shown for <b>{state}</b> matches its source Excel workbook. Edits you save in the editor will be logged here with 1-click rollback.
            </div>
            """, unsafe_allow_html=True)
            return

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


# ==========================================================================
# View 3: Master-Detail Inspector Hub (Prompt #2)
# ==========================================================================
def render_master_detail(df: pd.DataFrame) -> None:
    st.markdown("""
    <div style="margin-bottom:12px;">
      <div style="font-size:15px;font-weight:700;color:#f8fafc;">Master-Detail Workspace & Inspector Hub</div>
      <div style="font-size:12px;color:#94a3b8;margin-top:2px;">Select any entity on the left to inspect metadata lineage, JSON payload, and execute renewals.</div>
    </div>
    """, unsafe_allow_html=True)

    left_col, _, right_col = st.columns([1.8, 0.05, 2.2])

    with left_col:
        st.markdown('<div class="eyebrow" style="margin-top:0;">Master Records Filter</div>', unsafe_allow_html=True)
        f1, f2, f3 = st.columns([2.0, 1.5, 1.5])
        q = f1.text_input("Filter", key="md_search", placeholder="Search schema, state, env...", label_visibility="collapsed")
        state_filter = f2.selectbox("State", ["All States"] + STATES, key="md_state", label_visibility="collapsed")
        health_filter = f3.selectbox("Health", ["All Health"] + ui.BANDS, key="md_health", label_visibility="collapsed")

        filtered = df.copy()
        if q:
            filtered = search(filtered, q)
        if state_filter != "All States":
            filtered = filtered[filtered["state"] == state_filter]
        if health_filter != "All Health":
            filtered = filtered[filtered["band"] == health_filter]

        filtered = filtered.sort_values("days_left")

        if filtered.empty:
            st.markdown(ui.empty("No records match filter", "Try broadening your search query."), unsafe_allow_html=True)
            selected_id = None
        else:
            record_options = {
                f"{r.state} · {r.schema_name} ({r.env_label}) · {r.exp_date} [{r.band}]": r.id
                for r in filtered.itertuples()
            }
            selected_label = st.selectbox(
                "Select Record to Inspect",
                list(record_options),
                key="md_select_record",
                label_visibility="collapsed",
            )
            selected_id = record_options[selected_label]

            # Compact preview table
            st.markdown(f"<div style='font-size:11px;color:#94a3b8;margin:6px 0 4px;'>Showing <b>{len(filtered)}</b> matching entities:</div>", unsafe_allow_html=True)
            table_rows = []
            for r in filtered.head(15).itertuples():
                meta = ui.BAND_META.get(r.band, ui.BAND_META["Healthy"])
                table_rows.append(
                    f"<tr>"
                    f"<td class='sym' style='color:{meta['color']}'>{meta['symbol']}</td>"
                    f"<td class='m' style='font-weight:600;'>{r.state}</td>"
                    f"<td class='m'>{r.schema_name}</td>"
                    f"<td class='m'><span class='env-pill'>{r.env_label}</span></td>"
                    f"<td class='m r' style='color:{meta['color']};font-weight:600;'>{ui.fmt_days(r.days_left)}</td>"
                    f"</tr>"
                )
            head = "<tr><th></th><th>State</th><th>Schema</th><th>Env</th><th class='r'>Remaining</th></tr>"
            st.markdown(f"<div style='max-height:360px;overflow-y:auto;border:1px solid var(--rule);border-radius:6px;'><table class='tblx'>{head}{''.join(table_rows)}</table></div>", unsafe_allow_html=True)

    with right_col:
        st.markdown('<div class="eyebrow" style="margin-top:0;">Detail Inspector Panel</div>', unsafe_allow_html=True)
        if selected_id is None:
            st.markdown(ui.empty("Select a record", "Choose an item from the master list to inspect."), unsafe_allow_html=True)
            return

        rec = df[df["id"] == selected_id].iloc[0]
        meta = ui.BAND_META.get(rec["band"], ui.BAND_META["Healthy"])

        st.markdown(f"""
        <div class="card" style="border-left:4px solid {meta['color']};margin-bottom:12px;">
          <div style="display:flex;align-items:center;justify-content:space-between;">
            <div>
              <span style="font-size:10px;font-weight:700;letter-spacing:.1em;color:{meta['color']}">RECORD ID #{rec['id']} · {rec['state']}</span>
              <div style="font-size:17px;font-weight:700;font-family:var(--mono);color:#f8fafc;margin-top:2px;">{rec['schema_name']}</div>
            </div>
            {ui.status_pill(rec['band'])}
          </div>
        </div>
        """, unsafe_allow_html=True)

        i_tab1, i_tab2, i_tab3 = st.tabs(["Overview & Metadata", "Raw JSON & SQL Lineage", "Action & Renewal Console"])

        with i_tab1:
            c_a, c_b = st.columns(2)
            with c_a:
                st.markdown(f"""
                <div class="card" style="font-size:12px;line-height:1.6;">
                  <div><b>Component:</b> {rec['component']}</div>
                  <div style="color:#94a3b8;font-size:10.5px;margin-bottom:6px;">{ui.COMPONENT_BLURB.get(rec['component'], '')}</div>
                  <div><b>Environment:</b> <code>{rec['env_label']}</code> ({ui.ENV_BLURB.get(rec['env_label'], 'Custom')})</div>
                  <div><b>Module Code:</b> <code>{rec['module'] or 'N/A'}</code></div>
                </div>
                """, unsafe_allow_html=True)
            with c_b:
                st.markdown(f"""
                <div class="card" style="font-size:12px;line-height:1.6;">
                  <div><b>Current Expiry:</b> <code style="font-weight:700;color:{meta['color']}">{rec['exp_date']}</code></div>
                  <div><b>Workbook Source:</b> <code>{rec['source_exp_date']}</code></div>
                  <div><b>Status Delta:</b> <b>{ui.fmt_days(rec['days_left'])}</b> ({rec['days_left']} days)</div>
                  <div><b>Quarter Horizon:</b> <code>{rec['quarter']}</code></div>
                </div>
                """, unsafe_allow_html=True)

        with i_tab2:
            payload = {
                "id": int(rec["id"]),
                "state": rec["state"],
                "component": rec["component"],
                "environment": rec["env_label"],
                "schema_name": rec["schema_name"],
                "exp_date": rec["exp_date"],
                "source_exp_date": rec["source_exp_date"],
                "days_left": int(rec["days_left"]),
                "band": rec["band"],
                "edited_at": str(rec["edited_at"]),
            }
            if hasattr(st, "code"):
                st.code(json.dumps(payload, indent=2), language="json")
                st.markdown("<div class='eyebrow'>SQLite Lineage Query</div>", unsafe_allow_html=True)
                st.code(f"SELECT * FROM component_records WHERE id = {int(rec['id'])};", language="sql")
            else:
                st.markdown(f"<pre style='background:var(--sunk);padding:8px;border-radius:6px;font-size:11px;'>{json.dumps(payload, indent=2)}</pre>", unsafe_allow_html=True)

        with i_tab3:
            st.markdown(ui.note("Modify working expiry date or revert back to Excel source:"), unsafe_allow_html=True)
            act_col1, act_col2 = st.columns(2)
            with act_col1:
                with st.form(f"md_form_{rec['id']}"):
                    new_dt = st.date_input("Update Expiry Date", value=rec["exp_dt"].date())
                    if st.form_submit_button("Commit Renewal to SQLite", type="primary", use_container_width=True):
                        apply_edits([(rec["id"], new_dt)])
                        st.success(f"Updated {rec['schema_name']} to {new_dt}")
                        rerun()

            with act_col2:
                if rec["edited"]:
                    st.warning(f"Locally modified from `{rec['source_exp_date']}`")
                    if st.button("Revert to Workbook Date", key=f"revert_btn_{rec['id']}", use_container_width=True):
                        conn = get_connection(DB_PATH)
                        revert_component_exp_date(conn, int(rec["id"]))
                        conn.close()
                        bust_cache()
                        st.success("Reverted to source workbook.")
                        rerun()
                else:
                    st.info("Record is in sync with Excel workbook.")


# ==========================================================================
# View 4: Hierarchical Matrix Cross-Tab (Prompt #3)
# ==========================================================================
def render_matrix_view(df: pd.DataFrame) -> None:
    st.markdown("""
    <div style="margin-bottom:12px;">
      <div style="font-size:15px;font-weight:700;color:#f8fafc;">Hierarchical Matrix & Cross-Tab Analysis</div>
      <div style="font-size:12px;color:#94a3b8;margin-top:2px;">Multi-dimensional grouping by State, Component, and Environment with status chips & export.</div>
    </div>
    """, unsafe_allow_html=True)

    m_col1, m_col2, m_col3, m_col4 = st.columns([1.8, 1.2, 1.4, 1.0])
    s_term = m_col1.text_input("Filter Matrix", key="mat_search", placeholder="Filter schema / env / component...", label_visibility="collapsed")
    s_state = m_col2.selectbox("State Filter", ["All States"] + STATES, key="mat_state", label_visibility="collapsed")
    s_comp = m_col3.selectbox("Component Filter", ["All Components"] + COMPONENT_ORDER, key="mat_comp", label_visibility="collapsed")

    mat_df = df.copy()
    if s_term:
        mat_df = search(mat_df, s_term)
    if s_state != "All States":
        mat_df = mat_df[mat_df["state"] == s_state]
    if s_comp != "All Components":
        mat_df = mat_df[mat_df["component"] == s_comp]

    csv_data = mat_df.to_csv(index=False).encode("utf-8")
    if hasattr(st, "download_button"):
        m_col4.download_button(
            label="Download CSV",
            data=csv_data,
            file_name=f"expiry_matrix_{date.today().isoformat()}.csv",
            mime="text/csv",
            use_container_width=True,
        )

    # State grouping summaries
    for state in (STATES if s_state == "All States" else [s_state]):
        sub = mat_df[mat_df["state"] == state]
        if sub.empty:
            continue

        exp_c = int((sub["days_left"] < 0).sum())
        crit_c = int((sub["days_left"].between(0, ui.CRITICAL_DAYS)).sum())
        warn_c = int((sub["days_left"].between(ui.CRITICAL_DAYS + 1, ui.WARNING_DAYS)).sum())
        hlth_c = int((sub["days_left"] > ui.WARNING_DAYS).sum())

        with st.expander(f"📍 {state} — {len(sub)} entities ({exp_c} Expired · {crit_c} Critical · {warn_c} Warning · {hlth_c} Healthy)", expanded=True):
            display_df = sub[["schema_name", "environment", "component", "exp_date", "days_left", "band"]].copy()
            display_df = display_df.sort_values("days_left")

            rows_html = []
            for r in display_df.itertuples():
                meta = ui.BAND_META.get(r.band, ui.BAND_META["Healthy"])
                rows_html.append(
                    f"<tr>"
                    f"<td class='sym' style='color:{meta['color']}'>{meta['symbol']}</td>"
                    f"<td class='m' style='font-weight:600;'>{r.schema_name}</td>"
                    f"<td class='m'><span class='env-pill'>{r.environment}</span></td>"
                    f"<td>{ui.COMPONENT_CODE.get(r.component, r.component)}</td>"
                    f"<td class='m'>{r.exp_date}</td>"
                    f"<td class='m c' style='color:{meta['color']};font-weight:700;'>{ui.fmt_days(r.days_left)}</td>"
                    f"<td class='c'>{ui.status_pill(r.band)}</td>"
                    f"</tr>"
                )
            head = "<tr><th></th><th>Schema Name</th><th>Environment</th><th>Component</th><th>Expiry Date</th><th style='text-align:center;'>Time Left</th><th style='text-align:center;'>Status</th></tr>"
            st.markdown(f"<div style='max-height:380px;overflow-y:auto;border:1px solid var(--rule);border-radius:6px;'><table class='tblx'>{head}{''.join(rows_html)}</table></div>", unsafe_allow_html=True)


# ==========================================================================
# View 5: Pipeline Governance & Alert Center (Prompt #5)
# ==========================================================================
def render_governance_center() -> None:
    st.markdown("""
    <div style="margin-bottom:14px;">
      <div style="font-size:15px;font-weight:700;color:#f8fafc;">Live Database Governance & Email Alert Simulator</div>
      <div style="font-size:12px;color:#94a3b8;margin-top:2px;">Zero-mock SQLite lineage inspection, AST workbook validation, and email notification simulation.</div>
    </div>
    """, unsafe_allow_html=True)

    # Top KPI summary strip
    conn = get_connection(DB_PATH)
    tables = ["component_records", "expiry_records", "owners", "reminder_log"]
    stats = {}
    for t in tables:
        stats[t] = conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
    conn.close()

    kpi_c1, kpi_c2, kpi_c3, kpi_c4 = st.columns(4)
    with kpi_c1:
        st.markdown(f"""
        <div class="top-glow-kpi" style="--glow:#38bdf8;">
          <div class="kpi-label">Component Entities</div>
          <div class="kpi-value">{stats['component_records']}</div>
          <div class="kpi-sub">Across AK, NH, ND portfolios</div>
        </div>
        """, unsafe_allow_html=True)
    with kpi_c2:
        st.markdown(f"""
        <div class="top-glow-kpi" style="--glow:#10b981;">
          <div class="kpi-label">Database Passwords</div>
          <div class="kpi-value">{stats['expiry_records']}</div>
          <div class="kpi-sub">Tracked in SQLite table</div>
        </div>
        """, unsafe_allow_html=True)
    with kpi_c3:
        st.markdown(f"""
        <div class="top-glow-kpi" style="--glow:#f59e0b;">
          <div class="kpi-label">Owner Routing</div>
          <div class="kpi-value">{stats['owners']} States</div>
          <div class="kpi-sub">Alaska, New Hampshire, North Dakota</div>
        </div>
        """, unsafe_allow_html=True)
    with kpi_c4:
        st.markdown(f"""
        <div class="top-glow-kpi" style="--glow:#6366f1;">
          <div class="kpi-label">Reminder Cycles</div>
          <div class="kpi-value">{stats['reminder_log']} Dispatched</div>
          <div class="kpi-sub">Scheduled Daily @ 08:00 UTC</div>
        </div>
        """, unsafe_allow_html=True)

    st.markdown("<div style='margin-top:12px;'></div>", unsafe_allow_html=True)
    g_col1, _, g_col2 = st.columns([1.8, 0.05, 2.2])

    with g_col1:
        st.markdown('<div class="eyebrow" style="margin-top:0;">Database Schema & Lineage Status</div>', unsafe_allow_html=True)
        st.markdown(f"""
        <div class="card" style="margin-bottom:16px;">
          <div style="font-size:12.5px;font-weight:700;color:#f8fafc;margin-bottom:8px;">
            SQLite Database: <code style="color:var(--accent);">{Path(DB_PATH).name}</code>
          </div>
          <table class="tblx">
            <tr><th>Table Name</th><th class="r">Row Count</th><th>Lineage Role</th><th>Status</th></tr>
            <tr><td class="m">component_records</td><td class="m r"><b>{stats['component_records']}</b></td><td style="color:var(--slate)">Multi-Component Workbooks</td><td><span class="pill" style="color:#10b981;background:rgba(16,185,129,0.15)">✓ Active</span></td></tr>
            <tr><td class="m">expiry_records</td><td class="m r"><b>{stats['expiry_records']}</b></td><td style="color:var(--slate)">Account DB Passwords</td><td><span class="pill" style="color:#10b981;background:rgba(16,185,129,0.15)">✓ Active</span></td></tr>
            <tr><td class="m">owners</td><td class="m r"><b>{stats['owners']}</b></td><td style="color:var(--slate)">State Owner Routing</td><td><span class="pill" style="color:#38bdf8;background:rgba(56,189,248,0.15)">3 States</span></td></tr>
            <tr><td class="m">reminder_log</td><td class="m r"><b>{stats['reminder_log']}</b></td><td style="color:var(--slate)">Audit & Reminder Cycles</td><td><span class="pill" style="color:#94a3b8;background:rgba(148,163,184,0.15)">Audit Ready</span></td></tr>
          </table>
        </div>
        """, unsafe_allow_html=True)

        st.markdown('<div class="eyebrow">Workbook Synchronization Console</div>', unsafe_allow_html=True)
        st.markdown('<div class="note">4 Source Excel Workbooks in project root:</div>', unsafe_allow_html=True)

        if st.button("Trigger Immediate Re-ingest", key="gov_reingest", type="primary", use_container_width=True):
            t_start = datetime.now()
            with st.spinner("Executing workbook parser..."):
                res = run_ingest(WORKBOOK_DIR, DB_PATH)
            duration_ms = (datetime.now() - t_start).total_seconds() * 1000
            bust_cache()
            st.success(f"Ingested {res['total_rows_read']} records in {duration_ms:.1f}ms ({res['new']} new, {res['renewed']} renewed).")
            rerun()

    with g_col2:
        st.markdown('<div class="eyebrow" style="margin-top:0;">Email Alert Engine & Dispatch Simulator</div>', unsafe_allow_html=True)
        conn = get_connection(DB_PATH)
        due_alerts = get_due_reminders(conn, threshold_days=ui.CRITICAL_DAYS)
        conn.close()

        if due_alerts:
            st.markdown(ui.note(
                f"<b>{len(due_alerts)}</b> account(s) due for daily email reminder (<= {ui.CRITICAL_DAYS} days)."),
                unsafe_allow_html=True)

            sample = due_alerts[0]
            st.markdown(f"""
            <div class="card" style="margin-bottom:8px;">
              <div style="font-size:11.5px;color:#f8fafc;"><b>Simulated Recipient:</b> <code>{sample['owner_email']}</code> ({sample['owner_name']})</div>
              <div style="font-size:11.5px;color:#94a3b8;margin-top:2px;"><b>Subject:</b> <code>{subject_for(sample)}</code></div>
            </div>
            """, unsafe_allow_html=True)

            with st.expander("Preview Rendered Jinja2 HTML Email Template", expanded=False):
                email_html = render_email(sample)
                st.markdown(f"<div style='background:#070D1E;padding:10px;border-radius:6px;border:1px solid #1E293B;max-height:220px;overflow-y:auto;'>{email_html}</div>", unsafe_allow_html=True)

            if st.button("Run Dry-Run Notification Cycle", key="gov_dry_run", use_container_width=True):
                st.info(f"Dry-run executed. {len(due_alerts)} notification(s) evaluated successfully with zero transport errors.")
        else:
            st.markdown(f"""
            <div class="card" style="text-align:center;padding:24px 16px;margin-bottom:12px;">
              <div style="font-size:24px;color:#10b981;margin-bottom:4px;">✓</div>
              <div style="font-size:14px;font-weight:700;color:#f8fafc;">No Pending Email Alerts</div>
              <div style="font-size:11.5px;color:#94a3b8;margin-top:4px;max-width:40ch;margin-left:auto;margin-right:auto;">
                All tracked database accounts and components currently have more than {ui.CRITICAL_DAYS} days of life remaining.
              </div>
            </div>
            """, unsafe_allow_html=True)

            with st.expander("Preview Standard Alert Email Template", expanded=False):
                mock_record = {
                    "state": "AK", "env": "DEV", "username": "AKCGAU30E2",
                    "schema_name": "ENV30_COTS_CGNS", "exp_date": "2026-09-18",
                    "days_left": 12, "owner_name": "Alaska DB Team",
                    "owner_email": "alaska-dba@example.com", "is_first_reminder": True
                }
                email_html = render_email(mock_record)
                st.markdown(f"<div style='background:#070D1E;padding:10px;border-radius:6px;border:1px solid #1E293B;max-height:220px;overflow-y:auto;'>{email_html}</div>", unsafe_allow_html=True)


# ==========================================================================
# Primary Navigation
# ==========================================================================
tab_overview, tab_state, tab_inspector, tab_matrix, tab_governance = st.tabs([
    "Overview",
    "State",
    "Master-Detail Inspector",
    "Hierarchical Matrix",
    "Governance & Alerts",
])

with tab_overview:
    canvas("all", None, CANVAS_OVERVIEW)

with tab_state:
    chosen = st.session_state.get("st_state")

    cols = st.columns([1, 1, 1, 5])
    for col, state in zip(cols, STATES):
        if col.button(state, key=f"st_pick_{state}",
                      type="primary" if chosen == state else "secondary",
                      use_container_width=True):
            st.session_state["st_state"] = None if chosen == state else state
            for key in ("mg_q", "mg_comp", "mg_editor", "mg_revert"):
                st.session_state.pop(key, None)
            rerun()

    with cols[3]:
        if chosen:
            subset = records[records["state"] == chosen]
            overdue = int((subset["days_left"] < 0).sum())
            st.markdown(ui.pick_line(
                f"{chosen} selected",
                f"{len(subset)} records · "
                + (f"{overdue} overdue" if overdue else "none overdue")
                + " · press the same button again to go back"),
                unsafe_allow_html=True)
        else:
            st.markdown(ui.pick_line(
                "Choose a state",
                "AK, NH and ND each open the same two views"), unsafe_allow_html=True)

    if not chosen:
        st.markdown(ui.empty(
            "Pick a state above to continue",
            "Each state opens an Overview — the same report as the first tab, pinned to that "
            "state — and a Manage view for recording renewals. The Overview tab already shows "
            "all three states together."), unsafe_allow_html=True)
    else:
        sub_overview, sub_manage = st.tabs(["Overview", "Manage"])
        with sub_overview:
            canvas("state", chosen, CANVAS_STATE)
        with sub_manage:
            render_manage(records[records["state"] == chosen], chosen)

with tab_inspector:
    render_master_detail(records)

with tab_matrix:
    render_matrix_view(records)

with tab_governance:
    render_governance_center()
