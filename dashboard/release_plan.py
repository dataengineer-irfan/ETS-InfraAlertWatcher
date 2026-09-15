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

    # --------------------------------------------------------------------------
    # 2. Header & State Selection (Compact Single-Row Header)
    # --------------------------------------------------------------------------
    h_col1, h_col2 = st.columns([7.0, 3.0])
    with h_col1:
        st.markdown(ui.render_universal_header(
            title="Schedule Release Plan",
            subtitle="Enterprise Milestones & Pipeline Health",
            badge_text="ENTERPRISE PIPELINE",
            badge_color="#38bdf8",
            state_scope=active_scope if active_scope != "All" else None,
        ), unsafe_allow_html=True)

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

    # Sort chronologically by dev_start_date (fallback prod_deploy_date)
    all_releases.sort(key=lambda x: (x.get("dev_start_date") or "9999-12-31", x.get("prod_deploy_date") or "9999-12-31"))

    # --------------------------------------------------------------------------
    # 3. Categorize into: Previous, Current, Upcoming based on DEV Date Window
    # --------------------------------------------------------------------------
    curr_r = None
    for r in all_releases:
        d_s = r.get("dev_start_date") or ""
        d_e = r.get("dev_end_date") or ""
        if d_s and d_e and d_s <= now_iso <= d_e:
            curr_r = r
            break
    if not curr_r:
        for r in all_releases:
            if (r.get("dev_start_date") or "") >= now_iso:
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

    upcoming_count = sum(1 for r in all_releases if (r.get("dev_start_date") or r.get("prod_deploy_date") or "") > (curr_r.get("dev_end_date", "") if curr_r else now_iso))

    monitored_pool = [r for r in (prev_r, curr_r, next_r) if r is not None]
    avg_readiness = (sum(r.get("readiness_pct", 0) for r in monitored_pool) / len(monitored_pool)) if monitored_pool else 100.0

    # --------------------------------------------------------------------------
    # 4. Strict Grafana Metric Ribbon (ui.grafana_stat_card)
    # --------------------------------------------------------------------------
    kpi_col1, kpi_col2, kpi_col3, kpi_col4 = st.columns(4)

    cur_label = curr_r["release_id"] if curr_r else "None"
    cur_dev_s = curr_r.get("dev_start_date", "—") if curr_r else "—"
    cur_dev_e = curr_r.get("dev_end_date", "—") if curr_r else "—"
    cur_date = curr_r.get("prod_deploy_date", "—") if curr_r else "—"
    cur_state = curr_r.get("state", "") if curr_r else ""

    days_to_dev_freeze = 0
    try:
        days_to_dev_freeze = (datetime.strptime(cur_dev_e, "%Y-%m-%d").date() - datetime.strptime(now_iso, "%Y-%m-%d").date()).days
    except Exception:
        pass

    with kpi_col1:
        st.markdown(ui.grafana_stat_card(
            label="Active Dev Target",
            value=cur_label,
            color="#38bdf8",
            subtext=f"DEV: {cur_dev_s} → {cur_dev_e} · {cur_state} MMIS",
            badge=f"D-{days_to_dev_freeze} DEV FREEZE" if days_to_dev_freeze > 0 else ("DEV FREEZE TODAY" if days_to_dev_freeze == 0 else "DEV PASSED"),
            sparkline_vals=[60, 75, 85, 90, 95],
            delta=f"Cutover: {cur_date}",
            state="ok",
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
        next_dev = next_r.get("dev_start_date", "—") if next_r else "—"
        next_date = next_r.get("prod_deploy_date", "—") if next_r else "—"
        next_label = next_r.get("release_id", "None") if next_r else "None"
        st.markdown(ui.grafana_stat_card(
            label="Scheduled Roadmap",
            value=f"{upcoming_count} Planned",
            color="#73bf69",
            subtext=f"Next: {next_label} (DEV: {next_dev})",
            badge="ROADMAP",
            sparkline_vals=[upcoming_count, max(0, upcoming_count - 1), upcoming_count],
            delta=f"Cutover: {next_date}",
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
            st_c = a.get('state', '')
            rid = a.get('release_id', '')
            c_rid = rid[len(st_c)+1:] if rid.startswith(f"{st_c}.") else rid
            chips_html.append(
                f"<span style='background:{bg_c};border:1px solid {border_c};border-radius:2px;padding:2px 8px;font-size:10px;display:inline-flex;align-items:center;gap:5px;white-space:nowrap;'>"
                f"<b style='color:var(--ink);'>{st_c}.{c_rid}</b>"
                f"<span style='color:{border_c};font-weight:700;'>{a['label']}</span>"
                f"</span>"
            )

        n_firing = sum(1 for a in deduped if a["chip_cls"] == "firing")
        dot_color = "#f2495c" if n_firing else "#ff9830"

        render_html(f"""
        <div style="display:flex;align-items:center;justify-content:space-between;padding:3px 10px;background:#141619;border:1px solid #22252b;border-radius:2px;margin-top:2px;margin-bottom:4px;box-sizing:border-box;">
          <div style="display:flex;align-items:center;gap:8px;overflow-x:auto;">
            <div style="display:flex;align-items:center;gap:5px;flex-shrink:0;">
              <span style="width:7px;height:7px;border-radius:50%;background:{dot_color};box-shadow:0 0 6px {dot_color};display:inline-block;"></span>
              <span style="font-size:9.5px;font-weight:700;color:var(--slate);text-transform:uppercase;letter-spacing:0.04em;">Active Gate Alerts:</span>
            </div>
            <div style="display:flex;align-items:center;gap:6px;flex-wrap:nowrap;">
              {''.join(chips_html)}
            </div>
          </div>
          <div style="font-size:9px;color:var(--mute);flex-shrink:0;letter-spacing:0.02em;margin-left:12px;">
            Gate Horizon: &le; 7d to Cutoff
          </div>
        </div>
        """)

    # --------------------------------------------------------------------------
    # 5b. Milestone Extraction & Formatting Helpers
    # --------------------------------------------------------------------------
    def _extract_milestones(r: dict) -> dict:
        rj = r.get("raw_json")
        dev_f = r.get("dev_end_date") or "TBD"
        dev_s = r.get("dev_start_date") or "TBD"
        sit_f = r.get("sit_end_date") or "TBD"
        uat_f = r.get("uat_end_date") or "None"
        prod_f = r.get("prod_deploy_date") or "TBD"
        reg_f = "TBD"

        if rj:
            try:
                m_data = json.loads(rj) if isinstance(rj, str) else rj
                milestones = m_data.get("milestones", [])
                for m in milestones:
                    tname = m.get("task_name", "").lower()
                    if "regression" in tname:
                        reg_f = m.get("finish_date") or m.get("start_date") or reg_f
                        if "execution" in tname or "complete" in tname or "reversal" in tname:
                            break
                for m in milestones:
                    tname = m.get("task_name", "").lower()
                    if "sit" in tname and "regression" not in tname:
                        f_date = m.get("finish_date")
                        if f_date and f_date != reg_f:
                            sit_f = f_date
                            break
            except Exception:
                pass

        if reg_f == "TBD":
            reg_f = sit_f if sit_f != "TBD" else dev_f

        return {
            "dev_start": dev_s,
            "dev_end": dev_f,
            "sit_end": sit_f,
            "regression_end": reg_f,
            "uat_end": uat_f,
            "prod_date": prod_f
        }

    def _fmt_md(dt_str: str | None) -> str:
        if not dt_str or dt_str in ("TBD", "None", ""):
            return "None" if dt_str == "None" else "TBD"
        parts = dt_str.split("-")
        return f"{parts[1]}-{parts[2]}" if len(parts) == 3 else dt_str

    def _fmt_date_clean(d_str: str | None) -> str:
        if not d_str or d_str in ("TBD", "None", "", None):
            return '<span style="color:#6e7681;">—</span>' if d_str in ("None", None) else '<span style="color:#f59e0b;">TBD</span>'
        try:
            parts = str(d_str).split("-")
            if len(parts) == 3:
                months = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
                m_idx = int(parts[1])
                return f"{months[m_idx]} {parts[2]}, {parts[0]}"
        except Exception:
            pass
        return str(d_str)

    def _fmt_range_clean(d_s: str | None, d_e: str | None) -> str:
        if not d_s or not d_e or d_s == "TBD" or d_e == "TBD":
            return f"{d_s or 'TBD'} &rarr; {d_e or 'TBD'}"
        try:
            p1 = str(d_s).split("-")
            p2 = str(d_e).split("-")
            if len(p1) == 3 and len(p2) == 3:
                months = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
                m1 = months[int(p1[1])]
                m2 = months[int(p2[1])]
                if p1[0] == p2[0]:
                    return f"{m1} {p1[2]} &rarr; {m2} {p2[2]}, {p1[0]}"
                return f"{m1} {p1[2]}, {p1[0]} &rarr; {m2} {p2[2]}, {p2[0]}"
        except Exception:
            pass
        return f"{d_s} &rarr; {d_e}"

    # --------------------------------------------------------------------------
    # 6. RESOLVE ACTIVE SELECTION TARGET
    # --------------------------------------------------------------------------
    release_dict = {r["release_id"]: r for r in all_releases}
    all_rel_ids = list(release_dict.keys())

    qp_target = st.query_params.get("target_rel")
    if qp_target and qp_target in all_rel_ids:
        st.session_state["rp_target_rel_picker"] = qp_target
        st.session_state["global_release_selection"] = qp_target
        st_code = qp_target.split(".")[0] if "." in qp_target else None
        if st_code in ["NH", "ND", "AK"]:
            st.session_state["_override_canvas_state"] = st_code
            st.session_state["gov_state_filter"] = st_code
            reset_idx = st.session_state.get("op_reset_idx", 0)
            st.session_state[f"op_state_{reset_idx}"] = st_code

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

    # --------------------------------------------------------------------------
    # 7. 3 EXECUTIVE STORYBOARD DECKS (5-Stage Pipeline: DEV, SIT, REGRESS, UAT, PROD)
    # --------------------------------------------------------------------------
    story_c1, story_c2, story_c3 = st.columns(3, gap="small")

    def _render_story_card(title: str, rel_data: dict | None, accent_color: str, icon: str):
        if not rel_data:
            return f'''
            <div style="background:#181b1f;border:1px solid #2c3235;border-top:3px solid #2c3235;border-radius:2px;padding:8px 12px;min-height:86px;display:flex;align-items:center;justify-content:center;box-sizing:border-box;">
              <div style="text-align:center;color:#6e7681;font-size:11px;">
                <div style="font-size:16px;">{icon}</div>
                <div style="margin-top:3px;">No {title} Found</div>
              </div>
            </div>
            '''

        st_c = rel_data.get("state", "Unknown")
        raw_rid = rel_data.get("release_id", "Unknown")
        c_rid = raw_rid[len(st_c)+1:] if raw_rid.startswith(f"{st_c}.") else raw_rid
        state_mmis = rel_data.get("state", "Unknown")
        readiness = rel_data.get("readiness_pct", 0)
        m = _extract_milestones(rel_data)

        dev_s = m["dev_start"]
        dev_f = m["dev_end"]
        sit_f = m["sit_end"]
        reg_f = m["regression_end"]
        uat_f = m["uat_end"]
        pd_date = m["prod_date"]

        is_deployed = str(pd_date) < now_iso
        is_active_dev = str(dev_s) <= now_iso <= str(dev_f)
        is_chosen = (raw_rid == chosen_rel)

        if is_deployed:
            status_label = "Dev Complete" if "Previous" in title else "Deployed"
            badge_bg = "rgba(115, 191, 105, 0.16)"
            badge_col = "#73bf69"
        elif is_active_dev:
            status_label = "Active Dev"
            badge_bg = "rgba(87, 148, 242, 0.2)"
            badge_col = "#8fb8f8"
        elif "Previous" in title:
            status_label = "Dev Complete"
            badge_bg = "rgba(115, 191, 105, 0.16)"
            badge_col = "#73bf69"
        else:
            status_label = "Upcoming"
            badge_bg = "rgba(255, 152, 48, 0.16)"
            badge_col = "#ff9830"

        if is_chosen:
            active_border = "border:1.5px solid #38bdf8;border-top:3px solid #38bdf8;box-shadow:0 0 14px rgba(56,189,248,0.28);"
            card_opacity = "opacity:1;"
            focus_pill = '<span style="font-size:8px;font-weight:800;color:#38bdf8;background:rgba(56,189,248,0.15);border:1px solid rgba(56,189,248,0.4);padding:1px 5px;border-radius:2px;margin-right:4px;">ACTIVE FOCUS</span>'
        elif is_active_dev:
            active_border = "border:1px solid rgba(87,148,242,0.4);border-top:3px solid #5794f2;"
            card_opacity = "opacity:1;"
            focus_pill = ""
        else:
            active_border = f"border:1px solid #2c3235;border-top:3px solid {accent_color};"
            card_opacity = "opacity:0.88;"
            focus_pill = ""

        # Format 5 stages: Dev, Sit, Regress, Uat, Prod
        s_dev = _fmt_md(dev_f)
        s_sit = _fmt_md(sit_f)
        s_reg = _fmt_md(reg_f)
        s_uat = _fmt_md(uat_f)
        s_prd = _fmt_md(pd_date)

        card_html = f'''
        <div class="story-deck" style="background:#181b1f;{active_border}{card_opacity}border-radius:2px;padding:4px 8px;min-height:72px;display:flex;flex-direction:column;justify-content:space-between;box-sizing:border-box;">
          <div style="display:flex;align-items:center;justify-content:space-between;">
            <div>
              <span style="font-size:13px;font-weight:700;color:#d8d9da;font-family:var(--mono);">{st_c}.{c_rid}</span>
              <span style="font-size:9px;color:#9fa7b3;margin-left:4px;">{state_mmis} Scope</span>
            </div>
            <div style="display:flex;align-items:center;">
              {focus_pill}
              <span style="font-size:8px;padding:1px 6px;border-radius:2px;font-weight:700;text-transform:uppercase;background:{badge_bg};color:{badge_col};">{status_label}</span>
            </div>
          </div>

          <div style="display:flex;justify-content:space-between;align-items:baseline;margin-top:1px;">
            <div style="font-size:10px;color:#9fa7b3;">Gate: <b style="color:#8fb8f8;font-size:11px;">{readiness:.0f}%</b></div>
            <div style="font-size:9px;color:#6e7681;">DEV: <span style="font-family:var(--mono);color:#d8d9da;font-weight:600;">{dev_s} &rarr; {dev_f}</span></div>
          </div>

          <div style="display:grid;grid-template-columns:repeat(5,1fr);gap:4px;margin-top:3px;">
            <div style="background:#212429;border-radius:2px;padding:2px 2px;text-align:center;" title="DEV Freeze: {dev_f}">
              <div style="font-size:8px;color:#6e7681;text-transform:uppercase;letter-spacing:0.02em;">Dev</div>
              <div style="font-size:10px;font-weight:600;margin-top:1px;font-family:var(--mono);color:#d8d9da;">{s_dev}</div>
            </div>
            <div style="background:#212429;border-radius:2px;padding:2px 2px;text-align:center;" title="SIT Gate: {sit_f}">
              <div style="font-size:8px;color:#6e7681;text-transform:uppercase;letter-spacing:0.02em;">Sit</div>
              <div style="font-size:10px;font-weight:600;margin-top:1px;font-family:var(--mono);color:#d8d9da;">{s_sit}</div>
            </div>
            <div style="background:#212429;border-radius:2px;padding:2px 2px;text-align:center;border:1px solid rgba(87,148,242,0.25);" title="Regression Gate: {reg_f}">
              <div style="font-size:8px;color:#8fb8f8;text-transform:uppercase;letter-spacing:0.02em;font-weight:700;">Regress</div>
              <div style="font-size:10px;font-weight:600;margin-top:1px;font-family:var(--mono);color:#d8d9da;">{s_reg}</div>
            </div>
            <div style="background:#212429;border-radius:2px;padding:2px 2px;text-align:center;" title="UAT Gate: {uat_f}">
              <div style="font-size:8px;color:#6e7681;text-transform:uppercase;letter-spacing:0.02em;">Uat</div>
              <div style="font-size:10px;font-weight:600;margin-top:1px;font-family:var(--mono);color:#d8d9da;">{s_uat}</div>
            </div>
            <div style="background:#212429;border-radius:2px;padding:2px 2px;text-align:center;" title="PROD Cutover: {pd_date}">
              <div style="font-size:8px;color:#6e7681;text-transform:uppercase;letter-spacing:0.02em;">Prod</div>
              <div style="font-size:10px;font-weight:700;margin-top:1px;font-family:var(--mono);color:#8fb8f8;">{s_prd}</div>
            </div>
          </div>
        </div>
        '''
        return card_html

    with story_c1:
        render_html(_render_story_card("Previous Release", prev_r, "#73bf69", "📁"))
        if prev_r:
            prev_rid = prev_r.get("release_id", "")
            st_c = prev_r.get("state", "")
            c_rid = prev_rid[len(st_c)+1:] if prev_rid.startswith(f"{st_c}.") else prev_rid
            is_chosen = (prev_rid == chosen_rel)
            btn_txt = f"✓ Focus: {c_rid}" if is_chosen else f"📁 Focus {c_rid}"
            if st.button(btn_txt, key=f"btn_card_{prev_rid}", type="primary" if is_chosen else "secondary", use_container_width=True):
                st.session_state["rp_target_rel_picker"] = prev_rid
                _on_release_filter_change()
                st.rerun()

    with story_c2:
        render_html(_render_story_card("Current Release", curr_r, "#5794f2", "🎯"))
        if curr_r:
            curr_rid = curr_r.get("release_id", "")
            st_c = curr_r.get("state", "")
            c_rid = curr_rid[len(st_c)+1:] if curr_rid.startswith(f"{st_c}.") else curr_rid
            is_chosen = (curr_rid == chosen_rel)
            btn_txt = f"✓ Focus: {c_rid}" if is_chosen else f"🎯 Focus {c_rid}"
            if st.button(btn_txt, key=f"btn_card_{curr_rid}", type="primary" if is_chosen else "secondary", use_container_width=True):
                st.session_state["rp_target_rel_picker"] = curr_rid
                _on_release_filter_change()
                st.rerun()

    with story_c3:
        render_html(_render_story_card("Upcoming Release", next_r, "#ff9830", "🚀"))
        if next_r:
            next_rid = next_r.get("release_id", "")
            st_c = next_r.get("state", "")
            c_rid = next_rid[len(st_c)+1:] if next_rid.startswith(f"{st_c}.") else next_rid
            is_chosen = (next_rid == chosen_rel)
            btn_txt = f"✓ Focus: {c_rid}" if is_chosen else f"🚀 Focus {c_rid}"
            if st.button(btn_txt, key=f"btn_card_{next_rid}", type="primary" if is_chosen else "secondary", use_container_width=True):
                st.session_state["rp_target_rel_picker"] = next_rid
                _on_release_filter_change()
                st.rerun()

    # --------------------------------------------------------------------------
    # 8. TARGET RELEASE FLIGHT DECK (Upper Workspace ~124px)
    # --------------------------------------------------------------------------
    st_c_tag = rel_data.get('state', '')
    clean_tag = chosen_rel[len(st_c_tag)+1:] if st_c_tag and chosen_rel.startswith(f"{st_c_tag}.") else chosen_rel
    st.markdown(
        f"<div style='display:flex;align-items:center;justify-content:space-between;margin-bottom:3px;padding:0 1px;'>"
        f"<div style='font-size:10.5px;font-weight:700;color:#d8d9da;text-transform:uppercase;letter-spacing:0.04em;'>🎯 Target Release Deep-Dive &amp; Milestone Ledger</div>"
        f"<div style='font-size:9.5px;color:#9fa7b3;'>Target: <span style='color:#38bdf8;font-family:var(--mono);font-weight:700;background:rgba(56,189,248,0.12);padding:1px 6px;border-radius:2px;border:1px solid rgba(56,189,248,0.3);'>🎯 {clean_tag}</span></div>"
        f"</div>",
        unsafe_allow_html=True
    )

    d_c1, d_c2 = st.columns([1.0, 2.0], gap="small")
    with d_c1:
        if rel_data:
            st_code = rel_data.get('state', 'NH')
            raw_rid = rel_data.get('release_id', 'Unknown')
            clean_rid = raw_rid[len(st_code)+1:] if raw_rid.startswith(f"{st_code}.") else raw_rid
            disp_title = f"{st_code} MMIS &bull; Release {clean_rid}" if clean_rid else raw_rid

            prod_env = "PROD / ENV05" if st_code == "NH" else ("PROD / PRM" if st_code == "ND" else "PROD / ENV30")
            is_deployed = str(rel_data.get('prod_deploy_date', 'TBD')) < now_iso
            d_s = rel_data.get('dev_start_date', 'TBD')
            d_e = rel_data.get('dev_end_date', 'TBD')
            is_in_dev = str(d_s) <= now_iso <= str(d_e)
            status_color = "#73bf69" if is_deployed else ("#5794f2" if is_in_dev else "#ff9830")
            status_text = "DEPLOYED" if is_deployed else ("ACTIVE DEV" if is_in_dev else "SCHEDULED")

            st.markdown(f'''
            <div style="background:#141619;border:1px solid #2c3235;border-left:3px solid {status_color};border-radius:2px;padding:6px 10px;height:124px;display:flex;flex-direction:column;justify-content:space-between;box-sizing:border-box;">
                <div style="display:flex;justify-content:space-between;align-items:center;">
                    <div style="font-size:9px;color:#9fa7b3;text-transform:uppercase;font-weight:700;">Selected Target Release</div>
                    <span style="font-size:8px;font-weight:700;color:{status_color};background:rgba(255,255,255,0.05);border:1px solid {status_color}40;padding:1px 5px;border-radius:2px;">{status_text}</span>
                </div>
                <div>
                    <div style="font-size:13px;font-weight:800;color:#d8d9da;font-family:var(--mono);">{disp_title}</div>
                    <div style="font-size:9.5px;color:#9fa7b3;margin-top:1px;line-height:1.3;">
                        <b>DEV Window:</b> <span style="font-family:var(--mono);color:#d8d9da;">{_fmt_range_clean(d_s, d_e)}</span><br/>
                        <b>Target PROD:</b> <span style="color:#8fb8f8;font-weight:700;">{prod_env}</span> ({_fmt_date_clean(rel_data.get('prod_deploy_date', 'TBD'))})<br/>
                        <b>RM:</b> {rel_data.get('state_rm_name', 'Unassigned')} &bull; <b>Lead:</b> {rel_data.get('tech_lead_name', 'Unassigned')}
                    </div>
                </div>
                <div style="font-size:9.5px;font-family:var(--mono);color:#9fa7b3;display:flex;justify-content:space-between;align-items:center;border-top:1px solid #22252b;padding-top:2px;">
                    <span>Readiness: <b style="color:{status_color};background:{status_color}18;padding:1px 5px;border-radius:2px;border:1px solid {status_color}33;">{rel_data.get('readiness_pct', 0):.0f}%</b></span>
                    <span style="color:#8fb8f8;font-weight:700;">PROD: {_fmt_date_clean(rel_data.get('prod_deploy_date', 'TBD'))}</span>
                </div>
            </div>
            ''', unsafe_allow_html=True)

    with d_c2:
        if rel_data:
            st_code = rel_data.get('state', 'NH')
            raw_rid = rel_data.get('release_id', 'Unknown')
            clean_rid = raw_rid[len(st_code)+1:] if raw_rid.startswith(f"{st_code}.") else raw_rid
            prod_env_label = "PROD / ENV05" if st_code == "NH" else ("PROD / PRM" if st_code == "ND" else "PROD / ENV30")
            m_curr = _extract_milestones(rel_data)
            
            st.markdown('''
            <div style="background:#181b1f;border:1px solid #2c3235;border-radius:2px;padding:4px 8px;height:124px;overflow-y:auto;box-sizing:border-box;">
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:2px;">
                    <div style="font-size:9.5px;font-weight:700;color:#d8d9da;text-transform:uppercase;letter-spacing:0.03em;">Milestone Execution Ledger (5-Phase Pipeline)</div>
                    <div style="font-size:9px;color:#6e7681;font-family:var(--mono);">Standard Gate Policy</div>
                </div>
                <table class="tblx" style="width:100%;font-size:9.5px;line-height:1.22;">
                    <thead>
                        <tr style="border-bottom:1px solid #2c3235;color:#6e7681;">
                            <th style="text-align:left;padding:1.5px 3px;">Phase</th>
                            <th style="text-align:left;padding:1.5px 3px;">Target Environment</th>
                            <th style="text-align:left;padding:1.5px 3px;">Target Date</th>
                            <th style="text-align:right;padding:1.5px 3px;">Status</th>
                        </tr>
                    </thead>
                    <tbody>
                        <tr>
                            <td style="padding:1.5px 3px;font-weight:600;">DEV Cycle Window</td>
                            <td style="padding:1.5px 3px;color:#9fa7b3;">Build-76 / ENV52</td>
                            <td style="padding:1.5px 3px;font-family:var(--mono);">{dev_window}</td>
                            <td style="padding:1.5px 3px;text-align:right;">{d_stat}</td>
                        </tr>
                        <tr>
                            <td style="padding:1.5px 3px;font-weight:600;">SIT Gate Exit</td>
                            <td style="padding:1.5px 3px;color:#9fa7b3;">SIT QA / ENV57</td>
                            <td style="padding:1.5px 3px;font-family:var(--mono);">{sit}</td>
                            <td style="padding:1.5px 3px;text-align:right;">{s_stat}</td>
                        </tr>
                        <tr style="background:rgba(87,148,242,0.04);">
                            <td style="padding:1.5px 3px;font-weight:700;color:#8fb8f8;">Regression Testing Gate</td>
                            <td style="padding:1.5px 3px;color:#9fa7b3;">Regression / ENV53</td>
                            <td style="padding:1.5px 3px;font-family:var(--mono);font-weight:600;">{reg}</td>
                            <td style="padding:1.5px 3px;text-align:right;">{r_stat}</td>
                        </tr>
                        <tr>
                            <td style="padding:1.5px 3px;font-weight:600;">State UAT Acceptance</td>
                            <td style="padding:1.5px 3px;color:#9fa7b3;">Acceptance / ENV04</td>
                            <td style="padding:1.5px 3px;font-family:var(--mono);">{uat}</td>
                            <td style="padding:1.5px 3px;text-align:right;">{u_stat}</td>
                        </tr>
                        <tr>
                            <td style="padding:1.5px 3px;font-weight:700;color:#d8d9da;">PROD Cutover</td>
                            <td style="padding:1.5px 3px;font-weight:700;color:#8fb8f8;">{prod_env}</td>
                            <td style="padding:1.5px 3px;font-family:var(--mono);font-weight:700;color:#d8d9da;">{prod}</td>
                            <td style="padding:1.5px 3px;text-align:right;">{p_stat}</td>
                        </tr>
                    </tbody>
                </table>
            </div>
            '''.format(
                rid=f"{st_code}.{clean_rid}",
                dev_window=_fmt_range_clean(m_curr['dev_start'], m_curr['dev_end']),
                d_stat='<span style="color:#73bf69;font-weight:700;">PASSED</span>' if str(m_curr['dev_end']) < now_iso else ('<span style="color:#8fb8f8;font-weight:700;">ACTIVE DEV</span>' if str(m_curr['dev_start']) <= now_iso <= str(m_curr['dev_end']) else '<span style="color:#9fa7b3;font-weight:700;">PENDING</span>'),
                sit=_fmt_date_clean(m_curr['sit_end']),
                s_stat='<span style="color:#73bf69;font-weight:700;">PASSED</span>' if str(m_curr['sit_end']) < now_iso else '<span style="color:#9fa7b3;font-weight:700;">PENDING</span>',
                reg=_fmt_date_clean(m_curr['regression_end']),
                r_stat='<span style="color:#73bf69;font-weight:700;">PASSED</span>' if str(m_curr['regression_end']) < now_iso else '<span style="color:#8fb8f8;font-weight:700;">SCHEDULED</span>',
                uat=_fmt_date_clean(m_curr['uat_end']),
                u_stat='<span style="color:#73bf69;font-weight:700;">PASSED</span>' if str(m_curr['uat_end']) < now_iso else '<span style="color:#9fa7b3;font-weight:700;">PENDING</span>',
                prod_env=prod_env_label,
                prod=_fmt_date_clean(m_curr['prod_date']),
                p_stat='<span style="color:#73bf69;font-weight:700;">PASSED</span>' if str(m_curr['prod_date']) < now_iso else '<span style="color:#ff9830;font-weight:700;">PENDING</span>'
            ), unsafe_allow_html=True)

    # --------------------------------------------------------------------------
    # 9. MULTI-RELEASE ROADMAP MATRIX (Lower Workspace ~370px, Fills Canvas)
    # --------------------------------------------------------------------------
    rf_col1, rf_col2, rf_col3 = st.columns([3.8, 3.8, 2.4])
    with rf_col1:
        st.markdown("<div style='font-size:10.5px;font-weight:700;color:#d8d9da;text-transform:uppercase;letter-spacing:0.04em;padding-top:2px;'>📋 Multi-Release Pipeline Roadmap &amp; Gate Matrix</div>", unsafe_allow_html=True)
    with rf_col2:
        roadmap_filter = st.radio(
            "Roadmap Filter",
            ["All Releases", "Active & In-Flight", "Upcoming", "Deployed"],
            key="rp_roadmap_filter_tab",
            horizontal=True,
            label_visibility="collapsed"
        )
    with rf_col3:
        clean_opts = {r["release_id"]: (r["release_id"].split(".", 1)[1] if "." in r["release_id"] else r["release_id"]) for r in all_releases}
        if "rp_matrix_quick_jump" not in st.session_state or st.session_state.get("rp_matrix_quick_jump") not in all_rel_ids or st.session_state.get("rp_matrix_quick_jump") != chosen_rel:
            st.session_state["rp_matrix_quick_jump"] = chosen_rel

        def _on_quick_pick_change():
            q_val = st.session_state.get("rp_matrix_quick_jump")
            if q_val:
                st.session_state["rp_target_rel_picker"] = q_val
                _on_release_filter_change()

        st.selectbox(
            "Quick Focus Release",
            all_rel_ids,
            format_func=lambda rid: f"🎯 Focus: {clean_opts.get(rid, rid)}",
            key="rp_matrix_quick_jump",
            on_change=_on_quick_pick_change,
            label_visibility="collapsed"
        )

    roadmap_rows = []
    for r in all_releases:
        st_code = r.get("state", "NH")
        raw_r_id = r.get("release_id", "")
        clean_r_id = raw_r_id[len(st_code)+1:] if raw_r_id.startswith(f"{st_code}.") else raw_r_id

        prod_env = "PROD (ENV05)" if st_code == "NH" else ("PROD (PRM)" if st_code == "ND" else "PROD (ENV30)")
        m_r = _extract_milestones(r)
        d_s = str(m_r['dev_start'])
        d_e = str(m_r['dev_end'])
        p_d = str(m_r['prod_date'])
        reg_d = m_r['regression_end']
        is_deployed = p_d < now_iso
        is_in_dev = d_s <= now_iso <= d_e
        is_today = p_d == now_iso
        is_upcoming = d_s > now_iso

        # Filter check
        if roadmap_filter == "Active & In-Flight" and not (is_in_dev or (not is_deployed and d_s <= now_iso)):
            continue
        elif roadmap_filter == "Upcoming" and not is_upcoming:
            continue
        elif roadmap_filter == "Deployed" and not is_deployed:
            continue

        if is_today:
            status = '<span style="font-size:8.5px;font-weight:700;padding:1.5px 5px;border-radius:2px;background:rgba(255,152,48,0.16);color:#ff9830;">CUTOVER TODAY</span>'
        elif is_deployed:
            status = '<span style="font-size:8.5px;font-weight:700;padding:1.5px 5px;border-radius:2px;background:rgba(115,191,105,0.16);color:#73bf69;">DEPLOYED</span>'
        elif is_in_dev:
            status = '<span style="font-size:8.5px;font-weight:700;padding:1.5px 5px;border-radius:2px;background:rgba(87,148,242,0.2);color:#8fb8f8;">ACTIVE DEV</span>'
        else:
            status = '<span style="font-size:8.5px;font-weight:700;padding:1.5px 5px;border-radius:2px;background:rgba(255,152,48,0.16);color:#ff9830;">SCHEDULED</span>'

        is_selected_row = (raw_r_id == chosen_rel)
        if is_selected_row:
            row_style = "background:rgba(56,189,248,0.12);border-left:3px solid #38bdf8;border-bottom:1px solid #2c3235;"
            target_icon = '<span style="color:#38bdf8;font-size:9.5px;margin-right:3px;">🎯</span>'
        else:
            row_style = "border-bottom:1px solid #22252b;"
            target_icon = ''

        roadmap_rows.append(
            f"<tr style='{row_style}'>"
            f"<td style='padding:3px 5px;'><span style='font-weight:700;color:#9fa7b3;'>{st_code}</span></td>"
            f"<td style='padding:3px 5px;'>{target_icon}<b style='color:#38bdf8;font-family:var(--mono);'>{clean_r_id}</b></td>"
            f"<td style='padding:3px 5px;font-family:var(--mono);color:#d8d9da;'>{_fmt_range_clean(d_s, d_e)}</td>"
            f"<td style='padding:3px 5px;font-family:var(--mono);'>{_fmt_date_clean(m_r['sit_end'])}</td>"
            f"<td style='padding:3px 5px;font-family:var(--mono);color:#8fb8f8;font-weight:600;'>{_fmt_date_clean(reg_d)}</td>"
            f"<td style='padding:3px 5px;font-family:var(--mono);'>{_fmt_date_clean(m_r['uat_end'])}</td>"
            f"<td style='padding:3px 5px;font-family:var(--mono);font-weight:700;color:#d8d9da;'>{_fmt_date_clean(p_d)}</td>"
            f"<td style='padding:3px 5px;font-size:9px;color:#8fb8f8;'>{prod_env}</td>"
            f"<td style='padding:3px 5px;text-align:right;'>{status}</td>"
            f"</tr>"
        )

    empty_roadmap_notice = "<tr><td colspan='9' style='text-align:center;padding:20px;color:#6e7681;'>No releases match the selected roadmap filter.</td></tr>"
    st.markdown(f'''
    <style>
    .story-card-link:hover div {{
        border-color: #38bdf8 !important;
        box-shadow: 0 0 12px rgba(56, 189, 248, 0.28) !important;
    }}
    .rel-link:hover {{
        color: #7dd3fc !important;
        text-decoration: underline !important;
    }}
    div[data-testid="stColumn"] button[kind="primary"],
    div[data-testid="stColumn"] button[kind="secondary"] {{
        height: 22px !important;
        min-height: 22px !important;
        padding: 0 6px !important;
        font-size: 10px !important;
        font-weight: 700 !important;
        font-family: var(--mono) !important;
        margin-top: 2px !important;
        border-radius: 2px !important;
    }}
    table.tblx tbody tr:hover {{
        background: rgba(56, 189, 248, 0.06) !important;
    }}
    </style>
    <div style="background:#181b1f;border:1px solid #2c3235;border-radius:2px;max-height:calc(100vh - 460px);min-height:180px;overflow-y:auto;box-sizing:border-box;">
        <table class="tblx" style="width:100%;border-collapse:collapse;font-size:10px;">
            <thead style="position:sticky;top:0;background:#141619;border-bottom:1px solid #2c3235;z-index:2;">
                <tr>
                    <th style="padding:4px 5px;text-align:left;font-size:9px;color:#6e7681;font-weight:600;text-transform:uppercase;">State</th>
                    <th style="padding:4px 5px;text-align:left;font-size:9px;color:#6e7681;font-weight:600;text-transform:uppercase;">Release</th>
                    <th style="padding:4px 5px;text-align:left;font-size:9px;color:#6e7681;font-weight:600;text-transform:uppercase;">DEV Window</th>
                    <th style="padding:4px 5px;text-align:left;font-size:9px;color:#6e7681;font-weight:600;text-transform:uppercase;">SIT Gate</th>
                    <th style="padding:4px 5px;text-align:left;font-size:9px;color:#8fb8f8;font-weight:700;text-transform:uppercase;">Regression</th>
                    <th style="padding:4px 5px;text-align:left;font-size:9px;color:#6e7681;font-weight:600;text-transform:uppercase;">UAT Gate</th>
                    <th style="padding:4px 5px;text-align:left;font-size:9px;color:#6e7681;font-weight:600;text-transform:uppercase;">PROD Cutover</th>
                    <th style="padding:4px 5px;text-align:left;font-size:9px;color:#6e7681;font-weight:600;text-transform:uppercase;">Production Env</th>
                    <th style="padding:4px 5px;text-align:right;font-size:9px;color:#6e7681;font-weight:600;text-transform:uppercase;">Status</th>
                </tr>
            </thead>
            <tbody>
                {''.join(roadmap_rows) if roadmap_rows else empty_roadmap_notice}
            </tbody>
        </table>
    </div>
    ''', unsafe_allow_html=True)


