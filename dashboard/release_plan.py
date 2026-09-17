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
from pathlib import Path
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
    has_active_filter = bool(active_scope or st.session_state.get("global_release_selection"))
    if has_active_filter and is_enterprise_admin:
        h_col1, h_col_reset, h_col2 = st.columns([6.8, 1.2, 2.0])
    else:
        h_col1, h_col2 = st.columns([7.8, 2.2])
        h_col_reset = None

    with h_col1:
        st.markdown(ui.render_universal_header(
            title="Schedule Release Plan",
            subtitle="Enterprise Milestones & Pipeline Health",
            badge_text="ENTERPRISE PIPELINE",
            badge_color="#38bdf8",
            state_scope=active_scope if active_scope != "All" else None,
        ), unsafe_allow_html=True)

    if h_col_reset is not None:
        with h_col_reset:
            if st.button("↺ Reset Scope", key="btn_rp_reset_scope", use_container_width=True, help="Reset to All States"):
                st.session_state["_override_canvas_state"] = None
                st.session_state["global_release_selection"] = None
                st.session_state["gov_state_filter"] = "All"
                reset_idx = st.session_state.get("op_reset_idx", 0)
                st.session_state[f"op_state_{reset_idx}"] = "All States"
                st.session_state["sl_state_clean"] = "All States"
                if "rp_target_rel_picker" in st.session_state:
                    del st.session_state["rp_target_rel_picker"]
                st.rerun()

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
    # 4. Strict Grafana Metric Ribbon — Removed per UX direction to enlarge 3 Releases
    # --------------------------------------------------------------------------


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
        try:
            parts = dt_str.split("-")
            if len(parts) == 3:
                months = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
                m_idx = int(parts[1])
                return f"{months[m_idx]} {parts[2]}"
        except Exception:
            pass
        return dt_str

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
            badge_html = f'<span style="font-size:8px;padding:1px 6px;border-radius:2px;font-weight:800;text-transform:uppercase;background:rgba(56,189,248,0.18);color:#38bdf8;border:1px solid rgba(56,189,248,0.4);">ACTIVE FOCUS · {status_label}</span>'
        elif is_active_dev:
            active_border = "border:1px solid rgba(87,148,242,0.4);border-top:3px solid #5794f2;"
            card_opacity = "opacity:1;"
            badge_html = f'<span style="font-size:8px;padding:1px 6px;border-radius:2px;font-weight:700;text-transform:uppercase;background:{badge_bg};color:{badge_col};">{status_label}</span>'
        else:
            active_border = f"border:1px solid #2c3235;border-top:3px solid {accent_color};"
            card_opacity = "opacity:0.88;"
            badge_html = f'<span style="font-size:8px;padding:1px 6px;border-radius:2px;font-weight:700;text-transform:uppercase;background:{badge_bg};color:{badge_col};">{status_label}</span>'

        # Format 5 stages: Dev, Sit, Regress, Uat, Prod
        s_dev = _fmt_md(dev_f)
        s_sit = _fmt_md(sit_f)
        s_reg = _fmt_md(reg_f)
        s_uat = _fmt_md(uat_f)
        s_prd = _fmt_md(pd_date)

        card_html = f'''
        <div class="story-deck" style="background:#181b1f;{active_border}{card_opacity}border-radius:3px;padding:6px 10px;min-height:98px;display:flex;flex-direction:column;justify-content:space-between;box-sizing:border-box;">
          <div style="display:flex;align-items:center;justify-content:space-between;">
            <div>
              <span style="font-size:15px;font-weight:800;color:#f8fafc;font-family:var(--mono);">{st_c}.{c_rid}</span>
              <span style="font-size:10.5px;color:#94a3b8;margin-left:6px;font-weight:500;">{state_mmis} Scope</span>
            </div>
            <div style="display:flex;align-items:center;">
              {badge_html}
            </div>
          </div>

          <div style="display:flex;justify-content:space-between;align-items:baseline;margin:3px 0 2px 0;">
            <div style="font-size:10.5px;color:#94a3b8;">Gate Readiness: <b style="color:#38bdf8;font-size:12px;font-family:var(--mono);">{readiness:.0f}%</b></div>
            <div style="font-size:9.5px;color:#94a3b8;">DEV: <span style="font-family:var(--mono);color:#f1f5f9;font-weight:600;">{dev_s} &rarr; {dev_f}</span></div>
          </div>

          <div style="display:grid;grid-template-columns:repeat(5,1fr);gap:4px;margin-top:3px;">
            <div style="background:#212429;border:1px solid #2c3235;border-radius:2px;padding:3px 2px;text-align:center;" title="DEV Freeze: {dev_f}">
              <div style="font-size:8px;color:#94a3b8;text-transform:uppercase;letter-spacing:0.02em;font-weight:700;">Dev Exit</div>
              <div style="font-size:10.5px;font-weight:700;margin-top:1px;font-family:var(--mono);color:#f8fafc;">{s_dev}</div>
            </div>
            <div style="background:#212429;border:1px solid #2c3235;border-radius:2px;padding:3px 2px;text-align:center;" title="SIT Gate: {sit_f}">
              <div style="font-size:8px;color:#94a3b8;text-transform:uppercase;letter-spacing:0.02em;font-weight:700;">SIT Exit</div>
              <div style="font-size:10.5px;font-weight:700;margin-top:1px;font-family:var(--mono);color:#f8fafc;">{s_sit}</div>
            </div>
            <div style="background:#212429;border-radius:2px;padding:3px 2px;text-align:center;border:1px solid rgba(56,189,248,0.35);background:rgba(56,189,248,0.06);" title="Regression Gate: {reg_f}">
              <div style="font-size:8px;color:#38bdf8;text-transform:uppercase;letter-spacing:0.02em;font-weight:700;">Regression</div>
              <div style="font-size:10.5px;font-weight:700;margin-top:1px;font-family:var(--mono);color:#f8fafc;">{s_reg}</div>
            </div>
            <div style="background:#212429;border:1px solid #2c3235;border-radius:2px;padding:3px 2px;text-align:center;" title="UAT Gate: {uat_f}">
              <div style="font-size:8px;color:#94a3b8;text-transform:uppercase;letter-spacing:0.02em;font-weight:700;">State UAT</div>
              <div style="font-size:10.5px;font-weight:700;margin-top:1px;font-family:var(--mono);color:#f8fafc;">{s_uat}</div>
            </div>
            <div style="background:#212429;border:1px solid #2c3235;border-radius:2px;padding:3px 2px;text-align:center;" title="PROD Cutover: {pd_date}">
              <div style="font-size:8px;color:#38bdf8;text-transform:uppercase;letter-spacing:0.02em;font-weight:700;">PROD Live</div>
              <div style="font-size:10.5px;font-weight:800;margin-top:1px;font-family:var(--mono);color:#38bdf8;">{s_prd}</div>
            </div>
          </div>
        </div>
        '''
        return card_html

    st.markdown('''
    <style>
    div[class*="st-key-btn_card_"] button {
        height: 24px !important;
        min-height: 24px !important;
        padding: 0 8px !important;
        font-size: 10.5px !important;
        font-weight: 700 !important;
        font-family: var(--mono) !important;
        margin-top: 2px !important;
        margin-bottom: 2px !important;
        border-radius: 3px !important;
        line-height: 22px !important;
    }
    div[class*="st-key-btn_card_"] button[data-testid*="stBaseButton-primary"],
    div[class*="st-key-btn_card_"] button[kind="primary"] {
        background: #0284c7 !important;
        border-color: #38bdf8 !important;
        color: #ffffff !important;
        box-shadow: 0 0 8px rgba(56, 189, 248, 0.3) !important;
    }
    /* Master Pipeline Roadmap Table Container with High-Contrast Cyan Scrollbar */
    .rp-table-container {
        background: #181b1f;
        border: 1px solid #2c3235;
        border-radius: 4px;
        box-sizing: border-box;
        overflow-y: auto;
        overflow-x: auto;
        height: 245px;
        max-height: 255px;
        scrollbar-width: thin;
        scrollbar-color: #38bdf8 #181b1f;
    }
    .rp-table-container::-webkit-scrollbar {
        width: 7px;
        height: 7px;
        display: block;
    }
    .rp-table-container::-webkit-scrollbar-track {
        background: #181b1f;
        border-left: 1px solid #2c3235;
    }
    .rp-table-container::-webkit-scrollbar-thumb {
        background: #38bdf8;
        border-radius: 3px;
        border: 1px solid #0284c7;
    }
    </style>
    ''', unsafe_allow_html=True)

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
    # 8. TARGET RELEASE FLIGHT DECK (Unified Single Grid Container ~130px)
    # --------------------------------------------------------------------------
    st_c_tag = rel_data.get('state', '')
    clean_tag = chosen_rel[len(st_c_tag)+1:] if st_c_tag and chosen_rel.startswith(f"{st_c_tag}.") else chosen_rel

    st_code = rel_data.get('state', 'NH') if rel_data else 'NH'
    raw_rid = rel_data.get('release_id', 'Unknown') if rel_data else 'Unknown'
    clean_rid = raw_rid[len(st_code)+1:] if raw_rid.startswith(f"{st_code}.") else raw_rid
    disp_title = f"{st_code} MMIS &bull; Release {clean_rid}" if clean_rid else raw_rid

    prod_env = "PROD / ENV05" if st_code == "NH" else ("PROD / PRM" if st_code == "ND" else "PROD / ENV30")
    is_deployed = str(rel_data.get('prod_deploy_date', 'TBD')) < now_iso if rel_data else False
    d_s = rel_data.get('dev_start_date', 'TBD') if rel_data else 'TBD'
    d_e = rel_data.get('dev_end_date', 'TBD') if rel_data else 'TBD'
    is_in_dev = str(d_s) <= now_iso <= str(d_e)
    status_color = "#73bf69" if is_deployed else ("#5794f2" if is_in_dev else "#ff9830")
    status_text = "DEPLOYED" if is_deployed else ("ACTIVE DEV" if is_in_dev else "SCHEDULED")

    prod_env_label = "PROD / ENV05" if st_code == "NH" else ("PROD / PRM" if st_code == "ND" else "PROD / ENV30")
    m_curr = _extract_milestones(rel_data) if rel_data else {
        "dev_start": "TBD", "dev_end": "TBD", "sit_end": "TBD", "regression_end": "TBD", "uat_end": "TBD", "prod_date": "TBD"
    }

    render_html(f'''
    <div style="margin: 5px 0 3px 0; padding: 0; box-sizing: border-box;">
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:3px;padding:0 2px;">
            <div style="font-size:10px;font-weight:700;color:#d8d9da;text-transform:uppercase;letter-spacing:0.04em;">
                🎯 Target Release Deep-Dive &amp; Milestone Ledger
            </div>
            <div style="font-size:9px;color:#9fa7b3;">
                Target: <span style="color:#38bdf8;font-family:var(--mono);font-weight:700;background:rgba(56,189,248,0.12);padding:1px 6px;border-radius:2px;border:1px solid rgba(56,189,248,0.3);">🎯 {clean_tag}</span>
            </div>
        </div>
        <div style="display:grid;grid-template-columns:1fr 2fr;gap:8px;align-items:stretch;box-sizing:border-box;">
            <!-- Left Card: Selected Target Release -->
            <div style="background:#141619;border:1px solid #2c3235;border-left:3px solid {status_color};border-radius:4px;padding:6px 10px;min-height:108px;display:flex;flex-direction:column;justify-content:space-between;box-sizing:border-box;">
                <div style="display:flex;justify-content:space-between;align-items:center;">
                    <div style="font-size:8.5px;color:#9fa7b3;text-transform:uppercase;font-weight:700;">Selected Target Release</div>
                    <span style="font-size:8px;font-weight:700;color:{status_color};background:rgba(255,255,255,0.05);border:1px solid {status_color}40;padding:1px 5px;border-radius:2px;">{status_text}</span>
                </div>
                <div>
                    <div style="font-size:12.5px;font-weight:800;color:#d8d9da;font-family:var(--mono);">{disp_title}</div>
                    <div style="font-size:9px;color:#9fa7b3;margin-top:2px;line-height:1.3;">
                        <b>DEV Window:</b> <span style="font-family:var(--mono);color:#d8d9da;">{_fmt_range_clean(d_s, d_e)}</span><br/>
                        <b>Target PROD:</b> <span style="color:#8fb8f8;font-weight:700;">{prod_env}</span> ({_fmt_date_clean(rel_data.get('prod_deploy_date', 'TBD'))})<br/>
                        <b>RM:</b> {rel_data.get('state_rm_name', 'Unassigned')} &bull; <b>Lead:</b> {rel_data.get('tech_lead_name', 'Unassigned')}
                    </div>
                </div>
                <div style="font-size:9px;font-family:var(--mono);color:#9fa7b3;display:flex;justify-content:space-between;align-items:center;border-top:1px solid #22252b;padding-top:2px;">
                    <span>Readiness: <b style="color:{status_color};background:{status_color}18;padding:1px 5px;border-radius:2px;border:1px solid {status_color}33;">{rel_data.get('readiness_pct', 0):.0f}%</b></span>
                    <span style="color:#8fb8f8;font-weight:700;">PROD: {_fmt_date_clean(rel_data.get('prod_deploy_date', 'TBD'))}</span>
                </div>
            </div>
            <!-- Right Card: Milestone Execution Ledger -->
            <div style="background:#181b1f;border:1px solid #2c3235;border-radius:4px;padding:5px 8px;min-height:108px;overflow-y:auto;box-sizing:border-box;">
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:2px;">
                    <div style="font-size:9px;font-weight:700;color:#d8d9da;text-transform:uppercase;letter-spacing:0.03em;">Milestone Execution Ledger (5-Phase Pipeline)</div>
                    <div style="font-size:8.5px;color:#6e7681;font-family:var(--mono);">Standard Gate Policy</div>
                </div>
                <table class="tblx" style="width:100%;font-size:9px;line-height:1.2;">
                    <thead>
                        <tr style="border-bottom:1px solid #2c3235;color:#6e7681;">
                            <th style="text-align:left;padding:2px 4px;">Phase</th>
                            <th style="text-align:left;padding:2px 4px;">Target Environment</th>
                            <th style="text-align:left;padding:2px 4px;">Target Date</th>
                            <th style="text-align:right;padding:2px 4px;">Status</th>
                        </tr>
                    </thead>
                    <tbody>
                        <tr>
                            <td style="padding:2px 4px;font-weight:600;">DEV Cycle Window</td>
                            <td style="padding:2px 4px;color:#9fa7b3;">Build-76 / ENV52</td>
                            <td style="padding:2px 4px;font-family:var(--mono);">{_fmt_range_clean(m_curr['dev_start'], m_curr['dev_end'])}</td>
                            <td style="padding:2px 4px;text-align:right;">{'<span style="color:#73bf69;font-weight:700;">PASSED</span>' if str(m_curr['dev_end']) < now_iso else ('<span style="color:#8fb8f8;font-weight:700;">ACTIVE DEV</span>' if str(m_curr['dev_start']) <= now_iso <= str(m_curr['dev_end']) else '<span style="color:#9fa7b3;font-weight:700;">PENDING</span>')}</td>
                        </tr>
                        <tr>
                            <td style="padding:2px 4px;font-weight:600;">SIT Gate Exit</td>
                            <td style="padding:2px 4px;color:#9fa7b3;">SIT QA / ENV57</td>
                            <td style="padding:2px 4px;font-family:var(--mono);">{_fmt_date_clean(m_curr['sit_end'])}</td>
                            <td style="padding:2px 4px;text-align:right;">{'<span style="color:#73bf69;font-weight:700;">PASSED</span>' if str(m_curr['sit_end']) < now_iso else '<span style="color:#9fa7b3;font-weight:700;">PENDING</span>'}</td>
                        </tr>
                        <tr style="background:rgba(87,148,242,0.04);">
                            <td style="padding:2px 4px;font-weight:700;color:#8fb8f8;">Regression Testing Gate</td>
                            <td style="padding:2px 4px;color:#9fa7b3;">Regression / ENV53</td>
                            <td style="padding:2px 4px;font-family:var(--mono);font-weight:600;">{_fmt_date_clean(m_curr['regression_end'])}</td>
                            <td style="padding:2px 4px;text-align:right;">{'<span style="color:#73bf69;font-weight:700;">PASSED</span>' if str(m_curr['regression_end']) < now_iso else '<span style="color:#8fb8f8;font-weight:700;">SCHEDULED</span>'}</td>
                        </tr>
                        <tr>
                            <td style="padding:2px 4px;font-weight:600;">State UAT Acceptance</td>
                            <td style="padding:2px 4px;color:#9fa7b3;">Acceptance / ENV04</td>
                            <td style="padding:2px 4px;font-family:var(--mono);">{_fmt_date_clean(m_curr['uat_end'])}</td>
                            <td style="padding:2px 4px;text-align:right;">{'<span style="color:#73bf69;font-weight:700;">PASSED</span>' if str(m_curr['uat_end']) < now_iso else '<span style="color:#9fa7b3;font-weight:700;">PENDING</span>'}</td>
                        </tr>
                        <tr>
                            <td style="padding:2px 4px;font-weight:700;color:#d8d9da;">PROD Cutover</td>
                            <td style="padding:2px 4px;font-weight:700;color:#8fb8f8;">{prod_env_label}</td>
                            <td style="padding:2px 4px;font-family:var(--mono);font-weight:700;color:#d8d9da;">{_fmt_date_clean(m_curr['prod_date'])}</td>
                            <td style="padding:2px 4px;text-align:right;">{'<span style="color:#73bf69;font-weight:700;">PASSED</span>' if str(m_curr['prod_date']) < now_iso else '<span style="color:#ff9830;font-weight:700;">PENDING</span>'}</td>
                        </tr>
                    </tbody>
                </table>
            </div>
        </div>
    </div>
    ''')

    # --------------------------------------------------------------------------
    # 9. MULTI-RELEASE ROADMAP MATRIX (Lower Workspace ~230px, Fills Canvas)
    # --------------------------------------------------------------------------
    total_fleet_count = len(all_releases)
    active_count = sum(1 for r in all_releases if str(r.get('dev_start_date', '')) <= now_iso <= str(r.get('dev_end_date', '')))
    deployed_count = sum(1 for r in all_releases if str(r.get('prod_deploy_date', '9999')) < now_iso)
    scheduled_count = total_fleet_count - active_count - deployed_count

    render_html(f'''
    <div style='display:flex;align-items:center;justify-content:space-between;margin:6px 0 3px 0;padding:0 2px;'>
        <div style='font-size:10px;font-weight:700;color:#d8d9da;text-transform:uppercase;letter-spacing:0.04em;'>
            📋 Multi-Release Pipeline Roadmap &amp; Gate Matrix
            <span style='font-size:8.5px;color:#6e7681;font-weight:400;text-transform:none;margin-left:6px;'>· Click any Release to filter flight deck</span>
        </div>
        <div style='font-size:9px;color:#9fa7b3;font-family:var(--mono);'>
            Fleet Total: <b style='color:#38bdf8;'>{total_fleet_count} Releases</b> 
            <span style='color:#6e7681;'>({active_count} Active &bull; {scheduled_count} Scheduled &bull; {deployed_count} Deployed)</span>
        </div>
    </div>
    ''')

    roadmap_data = []
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

        if is_today:
            status = '<span style="font-size:8.5px;font-weight:700;padding:1.5px 5px;border-radius:2px;background:rgba(255,152,48,0.16);color:#ff9830;">CUTOVER TODAY</span>'
        elif is_deployed:
            status = '<span style="font-size:8.5px;font-weight:700;padding:1.5px 5px;border-radius:2px;background:rgba(115,191,105,0.16);color:#73bf69;">DEPLOYED</span>'
        elif is_in_dev:
            status = '<span style="font-size:8.5px;font-weight:700;padding:1.5px 5px;border-radius:2px;background:rgba(87,148,242,0.2);color:#8fb8f8;">ACTIVE DEV</span>'
        else:
            status = '<span style="font-size:8.5px;font-weight:700;padding:1.5px 5px;border-radius:2px;background:rgba(255,152,48,0.16);color:#ff9830;">SCHEDULED</span>'

        roadmap_data.append({
            "id": raw_r_id,
            "state": st_code,
            "clean_id": clean_r_id,
            "dev_window": _fmt_range_clean(d_s, d_e),
            "sit": _fmt_date_clean(m_r['sit_end']),
            "reg": _fmt_date_clean(reg_d),
            "uat": _fmt_date_clean(m_r['uat_end']),
            "prod": _fmt_date_clean(p_d),
            "prod_env": prod_env,
            "status_badge": status,
        })

    table_rows = []
    for r in roadmap_data:
        is_sel = (r["id"] == chosen_rel)
        row_bg = "background:rgba(56,189,248,0.14);border-left:3px solid #38bdf8;" if is_sel else "border-bottom:1px solid #22252b;"
        target_icon = '<span style="color:#38bdf8;font-size:9.5px;margin-right:3px;">🎯</span>' if is_sel else ''
        badge_bg = "rgba(56,189,248,0.25)" if is_sel else "rgba(56,189,248,0.08)"
        badge_border = "#38bdf8" if is_sel else "rgba(56,189,248,0.25)"
        badge_color = "#ffffff" if is_sel else "#38bdf8"
        badge_shadow = "box-shadow:0 0 8px rgba(56,189,248,0.35);" if is_sel else ""

        table_rows.append(f'''
        <tr style="{row_bg}">
            <td style="padding:3px 6px;"><span style="font-weight:700;color:#9fa7b3;">{r["state"]}</span></td>
            <td style="padding:3px 6px;">
                <a href="?target_rel={r['id']}" target="_self" style="text-decoration:none;display:inline-flex;align-items:center;padding:1.5px 5px;border-radius:2px;font-family:var(--mono);font-size:10px;font-weight:700;background:{badge_bg};border:1px solid {badge_border};color:{badge_color};{badge_shadow}">
                    {target_icon}{r["clean_id"]}
                </a>
            </td>
            <td style="padding:3px 6px;font-family:var(--mono);color:#d8d9da;">{r["dev_window"]}</td>
            <td style="padding:3px 6px;font-family:var(--mono);">{r["sit"]}</td>
            <td style="padding:3px 6px;font-family:var(--mono);color:#8fb8f8;font-weight:600;">{r["reg"]}</td>
            <td style="padding:3px 6px;font-family:var(--mono);">{r["uat"]}</td>
            <td style="padding:3px 6px;font-family:var(--mono);font-weight:700;color:#d8d9da;">{r["prod"]}</td>
            <td style="padding:3px 6px;font-size:9px;color:#8fb8f8;">{r["prod_env"]}</td>
            <td style="padding:3px 6px;text-align:right;">{r["status_badge"]}</td>
        </tr>
        ''')

    render_html(f'''
    <div class="rp-table-container">
        <table style="width:100%;min-width:850px;border-collapse:collapse;font-size:10px;color:#d8d9da;">
            <thead style="position:sticky;top:0;background:#141619;border-bottom:1px solid #2c3235;z-index:2;">
                <tr style="color:#6e7681;text-transform:uppercase;font-size:9px;font-weight:600;letter-spacing:0.03em;">
                    <th style="padding:4px 6px;text-align:left;">State</th>
                    <th style="padding:4px 6px;text-align:left;">Release (Click to Filter)</th>
                    <th style="padding:4px 6px;text-align:left;">DEV Window</th>
                    <th style="padding:4px 6px;text-align:left;">SIT Gate</th>
                    <th style="padding:4px 6px;text-align:left;color:#8fb8f8;font-weight:700;">Regression</th>
                    <th style="padding:4px 6px;text-align:left;">UAT Gate</th>
                    <th style="padding:4px 6px;text-align:left;">PROD Cutover</th>
                    <th style="padding:4px 6px;text-align:left;">Production Env</th>
                    <th style="padding:4px 6px;text-align:right;">Status</th>
                </tr>
            </thead>
            <tbody>
                {''.join(table_rows)}
            </tbody>
        </table>
    </div>
    ''')


