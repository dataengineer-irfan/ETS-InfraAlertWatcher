"""
rbac.py — Role-Based Access Control (RBAC) & Enterprise Security Audit Console
==============================================================================
Modernized Grafana-grade enterprise console for user provisioning, entitlement
governance, zero-trust session security, and immutable audit ledger telemetry.

Follows Enterprise Design Standards:
  - Universal 1-line executive command bar (Brand, Search, Role, State, Action, Telemetry, CSV, Reset)
  - Dynamic 4-KPI metric ribbon calculated from active filter scope
  - 5-Tab Enterprise Architecture:
      1. 👥 Live Directory & Master-Detail (Strictly synchronized 58% / 42% split)
      2. ➕ Provision Enterprise Account (Streamlined form + Entitlements Policy)
      3. 🛡️ Compliance & Immutable Audit Ledger (Forensic log table with sticky headers)
      4. 🔐 Zero-Trust Policy Matrix & Session Security (Cross-tab capability matrix)
      5. 📊 Access Analytics & Security Telemetry (Event velocity & distribution)
  - Ultra-compact inspector (<240px) with guaranteed unclipped action buttons
  - Strict 100vh viewport locking (zero outer page scrollbars)
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone, date
from html import escape
from pathlib import Path

import pandas as pd
import streamlit as st

import ui
from db import (
    get_connection,
    get_users,
    delete_user,
    update_user_role,
    update_user_state,
    create_user,
    log_audit_event,
    get_audit_logs,
)

_ROLE_OPTIONS = ["All Roles", "Admin", "Operator", "Auditor", "Viewer"]
_STATE_OPTIONS = ["All States", "Global", "AK", "ND", "NH"]
_ACTION_OPTIONS = [
    "ALL",
    "USER_CREATED",
    "USER_DELETED",
    "ROLE_MODIFIED",
    "EXPIRY_EDITED",
    "EMAIL_DISPATCHED",
    "USER_LOGIN",
    "LOGIN_FAILED",
    "USER_LOGOUT",
    "SYSTEM_INITIALIZATION",
]

_ROLE_BADGES = {
    "Admin": '<span class="alert-chip firing" style="font-size:8.5px;font-weight:700;letter-spacing:.04em;">ADMIN</span>',
    "Operator": '<span class="alert-chip pending" style="font-size:8.5px;font-weight:700;letter-spacing:.04em;">OPERATOR</span>',
    "Auditor": '<span class="alert-chip ok" style="font-size:8.5px;font-weight:700;color:#10b981;border-color:rgba(16,185,129,0.35);letter-spacing:.04em;">AUDITOR</span>',
    "Viewer": '<span class="alert-chip ok" style="font-size:8.5px;font-weight:700;color:#38bdf8;border-color:rgba(56,189,248,0.35);letter-spacing:.04em;">VIEWER</span>',
}

_ROLE_COLORS = {
    "Admin": "#ef4444",
    "Operator": "#f59e0b",
    "Auditor": "#10b981",
    "Viewer": "#38bdf8",
}

_ACTION_CHIPS = {
    "SYSTEM_INITIALIZATION": '<span class="alert-chip ok" style="font-size:8.5px;">SYSTEM_INIT</span>',
    "USER_CREATED": '<span class="alert-chip ok" style="font-size:8.5px;">USER_CREATED</span>',
    "USER_DELETED": '<span class="alert-chip firing" style="font-size:8.5px;">USER_DELETED</span>',
    "ROLE_MODIFIED": '<span class="alert-chip pending" style="font-size:8.5px;">ROLE_MODIFIED</span>',
    "EXPIRY_EDITED": '<span class="alert-chip pending" style="font-size:8.5px;">EXPIRY_EDITED</span>',
    "EMAIL_DISPATCHED": '<span class="alert-chip ok" style="font-size:8.5px;">EMAIL_SENT</span>',
    "USER_LOGIN": '<span class="alert-chip ok" style="font-size:8.5px;">LOGIN_OK</span>',
    "LOGIN_FAILED": '<span class="alert-chip firing" style="font-size:8.5px;">AUTH_FAIL</span>',
    "USER_LOGOUT": '<span class="alert-chip ok" style="font-size:8.5px;color:#94a3b8;">LOGOUT</span>',
}


def _user_monogram(name: str, role: str) -> str:
    color = _ROLE_COLORS.get(role, "#38bdf8")
    parts = [p for p in name.replace("_", " ").split() if p]
    initials = "".join([p[0].upper() for p in parts[:2]]) if parts else name[:2].upper()
    return (
        f'<div style="width:28px;height:28px;border-radius:3px;background:rgba(255,255,255,0.06);'
        f'border:1.5px solid {color};color:{color};display:flex;align-items:center;justify-content:center;'
        f'font-size:10px;font-weight:800;font-family:var(--mono);flex-shrink:0;">{initials}</div>'
    )


def _render_user_detail_inspector(
    target_user: dict,
    all_users: list[dict],
    user_audit_logs: list[dict],
    is_active_admin: bool,
    active_user: str,
    db_path: str,
    auth_suffix: str,
) -> None:
    """Render compact, zero-scroll right-hand detail inspector for selected user."""
    u_name = target_user["username"]
    u_role = target_user.get("role", "Viewer")
    u_full = target_user.get("full_name") or "—"
    u_email = target_user.get("email") or "—"
    u_state = target_user.get("assigned_state") or "Global"
    u_created = str(target_user.get("created_at") or "")[:19].replace("T", " ")
    u_last_login = str(target_user.get("last_login_at") or "")[:19].replace("T", " ") if target_user.get("last_login_at") else "Never"

    avatar_html = _user_monogram(u_name, u_role)
    role_chip = _ROLE_BADGES.get(u_role, _ROLE_BADGES["Viewer"])
    role_color = _ROLE_COLORS.get(u_role, "#38bdf8")

    # Entitlements definition per role
    entitlements = [
        ("Fleet Monitoring (Read)", True),
        ("Expiry Dates Modification", u_role in ["Admin", "Operator"]),
        ("Batch Renewals & Rollbacks", u_role in ["Admin", "Operator"]),
        ("SMTP Alert Dispatches", u_role in ["Admin", "Operator"]),
        ("User Provisioning & Roles", u_role == "Admin"),
        ("Immutable Audit Ledger", u_role in ["Admin", "Auditor"]),
    ]

    ent_pills = []
    for label, has_ent in entitlements:
        if has_ent:
            ent_pills.append(
                f'<div style="display:flex;align-items:center;gap:4px;background:rgba(16,185,129,0.08);'
                f'border:1px solid rgba(16,185,129,0.25);border-radius:2px;padding:2px 6px;font-size:9px;'
                f'color:#34d399;font-weight:600;"><span style="color:#10b981;font-weight:800;">✓</span> {label}</div>'
            )
        else:
            ent_pills.append(
                f'<div style="display:flex;align-items:center;gap:4px;background:rgba(255,255,255,0.02);'
                f'border:1px solid rgba(255,255,255,0.06);border-radius:2px;padding:2px 6px;font-size:9px;'
                f'color:#64748b;"><span style="color:#64748b;">✕</span> {label}</div>'
            )

    # Inspector card container
    st.markdown(f"""
    <div style="background:#181b1f;border:1px solid #2c3235;border-top:2px solid {role_color};border-radius:2px;padding:8px 10px;margin-bottom:6px;">
      <!-- Profile Header -->
      <div style="display:flex;align-items:center;justify-content:space-between;gap:8px;border-bottom:1px solid #22252b;padding-bottom:8px;margin-bottom:8px;">
        <div style="display:flex;align-items:center;gap:8px;min-width:0;">
          {avatar_html}
          <div style="min-width:0;">
            <div style="font-size:12px;font-weight:800;color:#f8fafc;font-family:var(--mono);display:flex;align-items:center;gap:6px;">
              <span>{escape(u_name)}</span>
              {role_chip}
            </div>
            <div style="font-size:9.5px;color:#94a3b8;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">
              {escape(u_full)} &bull; <span style="font-family:var(--mono);">{escape(u_email)}</span>
            </div>
          </div>
        </div>
        <div style="text-align:right;flex-shrink:0;">
          <div style="font-size:8px;font-weight:700;text-transform:uppercase;color:#64748b;">State Scope</div>
          <div style="font-size:10px;font-weight:800;color:#38bdf8;font-family:var(--mono);">{escape(u_state)}</div>
        </div>
      </div>

      <!-- Metadata Strip -->
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;background:#141619;border:1px solid #22252b;border-radius:2px;padding:5px 8px;margin-bottom:8px;font-size:9px;font-family:var(--mono);">
        <div><span style="color:#64748b;">Provisioned:</span> <b style="color:#cbd5e1;">{u_created}</b></div>
        <div><span style="color:#64748b;">Last Login:</span> <b style="color:#cbd5e1;">{u_last_login}</b></div>
      </div>

      <!-- Entitlements Grid -->
      <div style="font-size:8.5px;font-weight:700;text-transform:uppercase;letter-spacing:.04em;color:#94a3b8;margin-bottom:4px;">Effective Security Entitlements</div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:4px;margin-bottom:8px;">
        {''.join(ent_pills)}
      </div>
    </div>
    """, unsafe_allow_html=True)

    # 4. Privileged Admin Action Console
    if is_active_admin:
        st.markdown("""
        <div style="font-size:8.5px;font-weight:700;text-transform:uppercase;letter-spacing:.04em;color:#f59e0b;margin-bottom:4px;">
          ⚙️ Account Administration &amp; Governance
        </div>
        """, unsafe_allow_html=True)

        adm_c1, adm_c2, adm_c3 = st.columns([1.1, 1.0, 1.1], gap="small")

        with adm_c1:
            curr_role_idx = ["Operator", "Viewer", "Auditor", "Admin"].index(u_role) if u_role in ["Operator", "Viewer", "Auditor", "Admin"] else 1
            new_role_val = st.selectbox(
                "Modify Role",
                ["Operator", "Viewer", "Auditor", "Admin"],
                index=curr_role_idx,
                key=f"rbac_sel_role_{u_name}",
                label_visibility="collapsed",
            )
            if new_role_val != u_role:
                if st.button(f"Update to {new_role_val}", key=f"rbac_apply_role_{u_name}", type="primary", use_container_width=True):
                    conn_m = get_connection(db_path)
                    update_user_role(conn_m, u_name, new_role_val)
                    log_audit_event(
                        conn_m,
                        actor=active_user,
                        role="Admin",
                        action="ROLE_MODIFIED",
                        target_entity=f"User: {u_name}",
                        details=f"Privilege change: {u_role} -> {new_role_val}",
                    )
                    conn_m.close()
                    st.success(f"✓ Role updated to {new_role_val}")
                    st.rerun()

        with adm_c2:
            st_opts = ["Global", "AK", "ND", "NH"]
            curr_st_idx = st_opts.index(u_state) if u_state in st_opts else 0
            new_st_val = st.selectbox(
                "Scope",
                st_opts,
                index=curr_st_idx,
                key=f"rbac_sel_st_{u_name}",
                label_visibility="collapsed",
            )
            if new_st_val != u_state:
                if st.button(f"Set {new_st_val}", key=f"rbac_apply_st_{u_name}", use_container_width=True):
                    conn_m = get_connection(db_path)
                    update_user_state(conn_m, u_name, new_st_val)
                    log_audit_event(
                        conn_m,
                        actor=active_user,
                        role="Admin",
                        action="ROLE_MODIFIED",
                        target_entity=f"User: {u_name}",
                        details=f"State scope updated: {u_state} -> {new_st_val}",
                    )
                    conn_m.close()
                    st.success(f"✓ State scope set to {new_st_val}")
                    st.rerun()

        with adm_c3:
            if u_name == "admin":
                st.button("🔒 Root Protected", disabled=True, use_container_width=True, help="Default root administrator cannot be revoked")
            else:
                if st.button("🗑️ Revoke Account", key=f"rbac_del_btn_{u_name}", type="secondary", use_container_width=True):
                    conn_m = get_connection(db_path)
                    delete_user(conn_m, u_name)
                    log_audit_event(
                        conn_m,
                        actor=active_user,
                        role="Admin",
                        action="USER_DELETED",
                        target_entity=f"User: {u_name}",
                        details=f"Revoked and deleted enterprise account {u_name}",
                    )
                    conn_m.close()
                    st.warning(f"Revoked {u_name}")
                    st.rerun()

    # 5. User Activity Mini-Stream
    user_events = [
        l for l in user_audit_logs
        if l.get("actor") == u_name or u_name in str(l.get("target_entity", "")) or u_name in str(l.get("details", ""))
    ][:5]

    st.markdown("""
    <div style="font-size:8.5px;font-weight:700;text-transform:uppercase;letter-spacing:.04em;color:#94a3b8;margin:6px 0 3px;">
      Recent Activity Trail for this User
    </div>
    """, unsafe_allow_html=True)

    if user_events:
        ev_rows = []
        for e in user_events:
            ts = str(e.get("timestamp", ""))[:19].replace("T", " ")
            chip = _ACTION_CHIPS.get(e.get("action", ""), f'<span class="alert-chip ok" style="font-size:8.5px;">{e.get("action")}</span>')
            ev_rows.append(
                f"<tr style='border-bottom:1px solid #22252b;font-size:9.5px;'>"
                f"<td style='padding:3px 5px;font-family:var(--mono);color:#64748b;white-space:nowrap;'>{ts}</td>"
                f"<td style='padding:3px 5px;'>{chip}</td>"
                f"<td style='padding:3px 5px;color:#cbd5e1;max-width:180px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;'>{escape(str(e.get('details') or '—'))}</td>"
                f"</tr>"
            )
        st.markdown(f"""
        <div style="border:1px solid #2c3235;border-radius:2px;overflow:hidden;background:#141619;max-height:110px;overflow-y:auto;">
          <table style="width:100%;border-collapse:collapse;text-align:left;">
            <tbody>{''.join(ev_rows)}</tbody>
          </table>
        </div>
        """, unsafe_allow_html=True)
    else:
        st.markdown("""
        <div style="background:#141619;border:1px solid #2c3235;border-radius:2px;padding:6px 8px;font-size:9.5px;color:#64748b;text-align:center;">
          No audit entries recorded for this user yet.
        </div>
        """, unsafe_allow_html=True)


def render_rbac_workspace(db_path: str) -> None:
    """
    Dedicated Role-Based Access Control (RBAC) & Enterprise Security Audit Console.
    Fully integrated with 1-line command bar, reactive 4-KPI ribbon, synchronized
    master-detail inspector, and strict zero-scroll bounds.
    """
    ss = st.session_state

    # Initialize session state keys
    ss.setdefault("rbac_search", "")
    ss.setdefault("rbac_role_filter", "All Roles")
    ss.setdefault("rbac_state_filter", "All States")
    ss.setdefault("rbac_action_filter", "ALL")
    ss.setdefault("rbac_selected_user", "admin")

    # Fetch live users and audit logs
    conn = get_connection(db_path)
    all_users_raw = get_users(conn)
    all_users = [u for u in all_users_raw if not u["username"].startswith("testuser_")]
    audit_logs = get_audit_logs(conn, limit=300)
    conn.close()

    active_user = ss.get("active_user", "admin")
    user_role = ss.get("user_role", "Admin")
    is_admin = (user_role == "Admin")
    auth_suffix = f"&_auth_user={active_user}&tab=4"

    # Query param handling for 1-click selection
    qp_user = st.query_params.get("rbac_user")
    if qp_user:
        all_unames = [u["username"] for u in all_users]
        if qp_user in all_unames:
            ss["rbac_selected_user"] = qp_user
        if "rbac_user" in st.query_params:
            del st.query_params["rbac_user"]

    # ==========================================================================
    # 1. UNIVERSAL 1-LINE COMMAND BAR
    # ==========================================================================
    c_brand, c_srch, c_role, c_st, c_act, c_telem, c_csv, c_rst = st.columns(
        [1.65, 1.45, 1.10, 1.00, 1.25, 1.20, 0.50, 0.35],
        gap="small"
    )

    with c_brand:
        st.markdown(f"""
        <div style="display:flex;align-items:center;gap:5px;height:28px;padding-top:2px;" title="Access Control & Security Audit (Zero-Trust RBAC)">
            <div style="width:3px;height:16px;background:#ec4899;border-radius:1px;flex:none;"></div>
            <span style="font-size:11px;font-weight:800;letter-spacing:0.03em;color:#f8fafc;white-space:nowrap;">RBAC HUB</span>
            <span style="font-size:7.5px;font-weight:800;background:rgba(236,72,153,0.18);color:#f472b6;border:1px solid rgba(236,72,153,0.35);padding:1px 4px;border-radius:2px;white-space:nowrap;">ZERO-TRUST</span>
            <span style="font-size:8px;color:#94a3b8;font-family:var(--mono);white-space:nowrap;">({len(all_users)} Users)</span>
        </div>
        """, unsafe_allow_html=True)

    with c_srch:
        srch_val = st.text_input(
            "Search",
            value=ss["rbac_search"],
            placeholder="🔍 Search users, audit, IP...",
            key="rbac_top_search_input",
            label_visibility="collapsed",
            autocomplete="off",
        )
        if srch_val != ss["rbac_search"]:
            ss["rbac_search"] = srch_val

    with c_role:
        picked_role = st.selectbox(
            "Role",
            _ROLE_OPTIONS,
            index=_ROLE_OPTIONS.index(ss["rbac_role_filter"]) if ss["rbac_role_filter"] in _ROLE_OPTIONS else 0,
            key="rbac_top_role_select",
            label_visibility="collapsed",
            help="Filter by Enterprise Role",
        )
        if picked_role != ss["rbac_role_filter"]:
            ss["rbac_role_filter"] = picked_role

    with c_st:
        picked_st = st.selectbox(
            "State Scope",
            _STATE_OPTIONS,
            index=_STATE_OPTIONS.index(ss["rbac_state_filter"]) if ss["rbac_state_filter"] in _STATE_OPTIONS else 0,
            key="rbac_top_state_select",
            label_visibility="collapsed",
            help="Filter by Assigned State Scope",
        )
        if picked_st != ss["rbac_state_filter"]:
            ss["rbac_state_filter"] = picked_st

    with c_act:
        picked_act = st.selectbox(
            "Action Category",
            _ACTION_OPTIONS,
            index=_ACTION_OPTIONS.index(ss["rbac_action_filter"]) if ss["rbac_action_filter"] in _ACTION_OPTIONS else 0,
            key="rbac_top_action_select",
            label_visibility="collapsed",
            help="Filter Audit Log by Action Category",
        )
        if picked_act != ss["rbac_action_filter"]:
            ss["rbac_action_filter"] = picked_act

    with c_telem:
        now_str = datetime.now(timezone.utc).strftime("%H:%M UTC")
        st.markdown(f"""
        <div style="display:flex;align-items:center;justify-content:flex-end;gap:5px;height:28px;padding-top:2px;">
          <span style="font-size:7.5px;font-weight:700;color:#10b981;background:rgba(16,185,129,0.1);border:1px solid rgba(16,185,129,0.25);border-radius:2px;padding:1.5px 4px;font-family:var(--mono);">PBKDF2-SHA256</span>
          <span style="font-size:8px;color:#94a3b8;font-family:var(--mono);">{now_str}</span>
        </div>
        """, unsafe_allow_html=True)

    with c_csv:
        conn_csv = get_connection(db_path)
        df_audit_full = pd.read_sql_query(
            "SELECT timestamp, actor, role, action, target_entity, details, ip_address FROM audit_log ORDER BY id DESC LIMIT 500",
            conn_csv
        )
        conn_csv.close()
        st.markdown(
            ui.csv_download_button(
                df=df_audit_full,
                filename=f"ets_security_audit_{date.today().isoformat()}.csv",
                label="📥 CSV",
                key="rbac_audit_top_csv_btn",
            ),
            unsafe_allow_html=True,
        )

    with c_rst:
        if st.button("↺", key="rbac_top_reset_btn", help="Reset all filters to Fleet view", use_container_width=True):
            ss["rbac_search"] = ""
            ss["rbac_role_filter"] = "All Roles"
            ss["rbac_state_filter"] = "All States"
            ss["rbac_action_filter"] = "ALL"
            st.rerun()

    # ==========================================================================
    # 2. BACKEND FILTER ENGINE (Strict Master-Detail Cohesion)
    # ==========================================================================
    filtered_users = all_users
    if ss["rbac_role_filter"] != "All Roles":
        filtered_users = [u for u in filtered_users if u.get("role") == ss["rbac_role_filter"]]

    if ss["rbac_state_filter"] != "All States":
        filtered_users = [
            u for u in filtered_users
            if (u.get("assigned_state") or "Global") == ss["rbac_state_filter"]
        ]

    if ss["rbac_search"].strip():
        sq = ss["rbac_search"].strip().lower()
        filtered_users = [
            u for u in filtered_users
            if sq in u["username"].lower()
            or sq in (u.get("full_name") or "").lower()
            or sq in (u.get("email") or "").lower()
            or sq in (u.get("role") or "").lower()
        ]

    # Filtered audit logs
    filtered_audits = audit_logs
    if ss["rbac_action_filter"] != "ALL":
        filtered_audits = [a for a in filtered_audits if a.get("action") == ss["rbac_action_filter"]]

    if ss["rbac_search"].strip():
        sq = ss["rbac_search"].strip().lower()
        filtered_audits = [
            a for a in filtered_audits
            if sq in str(a.get("actor", "")).lower()
            or sq in str(a.get("target_entity", "")).lower()
            or sq in str(a.get("details", "")).lower()
            or sq in str(a.get("action", "")).lower()
            or sq in str(a.get("ip_address", "")).lower()
        ]

    # Synchronize inspector selection
    in_scope_unames = [u["username"] for u in filtered_users]
    cur_selected = ss["rbac_selected_user"]
    if cur_selected not in in_scope_unames and in_scope_unames:
        cur_selected = in_scope_unames[0]
        ss["rbac_selected_user"] = cur_selected

    # KPI counts
    total_users_scope = len(filtered_users)
    admin_count = sum(1 for u in filtered_users if u.get("role") == "Admin")
    op_count = sum(1 for u in filtered_users if u.get("role") == "Operator")
    audit_count = sum(1 for u in filtered_users if u.get("role") == "Auditor")
    viewer_count = sum(1 for u in filtered_users if u.get("role") == "Viewer")
    audit_events_count = len(filtered_audits)

    # ==========================================================================
    # 3. DYNAMIC 4-KPI METRIC RIBBON
    # ==========================================================================
    st.markdown(f"""
    <div style="display:grid;grid-template-columns:repeat(4, 1fr);gap:5px;margin-bottom:4px;margin-top:2px;">
      <div style="background:#181b1f;border:1px solid #2c3235;border-radius:2px;padding:3px 8px;display:flex;flex-direction:column;justify-content:space-between;height:38px;box-sizing:border-box;background:linear-gradient(180deg, rgba(56,189,248,0.12), rgba(56,189,248,0.01));border-top:2px solid #38bdf8;">
        <span style="font-size:8px;color:#94a3b8;text-transform:uppercase;font-weight:700;letter-spacing:.04em;line-height:1;">Total Accounts</span>
        <div style="font-size:13px;font-weight:800;line-height:1.1;color:#fff;font-family:var(--mono);">{total_users_scope} <span style="font-size:9px;color:#64748b;font-weight:400;">Users</span></div>
        <span style="font-size:7.5px;color:#38bdf8;font-weight:600;line-height:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">{admin_count} Admin &bull; {op_count} Op &bull; {audit_count} Aud &bull; {viewer_count} Vw</span>
      </div>
      <div style="background:#181b1f;border:1px solid #2c3235;border-radius:2px;padding:3px 8px;display:flex;flex-direction:column;justify-content:space-between;height:38px;box-sizing:border-box;background:linear-gradient(180deg, rgba(239,68,68,0.14), rgba(239,68,68,0.01));border-top:2px solid #ef4444;">
        <span style="font-size:8px;color:#94a3b8;text-transform:uppercase;font-weight:700;letter-spacing:.04em;line-height:1;">Privileged Admins</span>
        <div style="font-size:13px;font-weight:800;line-height:1.1;color:#f87171;font-family:var(--mono);">{admin_count} <span style="font-size:9px;color:#64748b;font-weight:400;">Root Tier</span></div>
        <span style="font-size:7.5px;color:#fca5a5;font-weight:600;line-height:1;">Zero-Trust Root Access</span>
      </div>
      <div style="background:#181b1f;border:1px solid #2c3235;border-radius:2px;padding:3px 8px;display:flex;flex-direction:column;justify-content:space-between;height:38px;box-sizing:border-box;background:linear-gradient(180deg, rgba(16,185,129,0.14), rgba(16,185,129,0.01));border-top:2px solid #10b981;">
        <span style="font-size:8px;color:#94a3b8;text-transform:uppercase;font-weight:700;letter-spacing:.04em;line-height:1;">Operational Personnel</span>
        <div style="font-size:13px;font-weight:800;line-height:1.1;color:#34d399;font-family:var(--mono);">{op_count} <span style="font-size:9px;color:#64748b;font-weight:400;">Operators</span></div>
        <span style="font-size:7.5px;color:#34d399;font-weight:600;line-height:1;">State-Scoped Write Entitlements</span>
      </div>
      <div style="background:#181b1f;border:1px solid #2c3235;border-radius:2px;padding:3px 8px;display:flex;flex-direction:column;justify-content:space-between;height:38px;box-sizing:border-box;background:linear-gradient(180deg, rgba(245,158,11,0.14), rgba(245,158,11,0.01));border-top:2px solid #f59e0b;">
        <span style="font-size:8px;color:#94a3b8;text-transform:uppercase;font-weight:700;letter-spacing:.04em;line-height:1;">Security Audit Trail</span>
        <div style="font-size:13px;font-weight:800;line-height:1.1;color:#fbbf24;font-family:var(--mono);">{audit_events_count} <span style="font-size:9px;color:#64748b;font-weight:400;">Events</span></div>
        <span style="font-size:7.5px;color:#fcd34d;font-weight:600;line-height:1;">Immutable SHA-256 HMAC Ledger</span>
      </div>
    </div>
    """, unsafe_allow_html=True)

    # ==========================================================================
    # 4. MASTER 5-TAB WORKSPACE
    # ==========================================================================
    tab_dir, tab_prov, tab_audit, tab_policy, tab_telemetry = st.tabs([
        "👥 Live User Directory & Master-Detail",
        "➕ Provision Enterprise Account",
        "🛡️ Compliance & Immutable Audit Ledger",
        "🔐 Zero-Trust Policy Matrix & Session Security",
        "📊 Access Analytics & Security Telemetry",
    ])

    # ==========================================================================
    # TAB 1: USER DIRECTORY & MASTER-DETAIL (58% / 42% Split)
    # ==========================================================================
    with tab_dir:
        col_master, col_detail = st.columns([5.8, 4.2], gap="small")

        with col_master:
            user_rows_html = []
            for idx, u in enumerate(filtered_users):
                un = u["username"]
                fn = u.get("full_name") or "—"
                em = u.get("email") or "—"
                ro = u.get("role", "Viewer")
                st_sc = u.get("assigned_state") or "Global"
                cr = str(u.get("created_at") or "")[:10]
                is_sel = (un == cur_selected)

                badge = _ROLE_BADGES.get(ro, _ROLE_BADGES["Viewer"])
                row_bg = "background:rgba(56,189,248,0.12);border-left:3px solid #38bdf8;" if is_sel else ("background:#181b1f;" if idx % 2 == 0 else "background:#141619;")
                btn_style = "background:#38bdf8;color:#040e1a;font-weight:800;" if is_sel else "background:rgba(56,189,248,0.08);color:#38bdf8;border:1px solid rgba(56,189,248,0.3);"

                user_rows_html.append(
                    f"<tr style='{row_bg}border-bottom:1px solid #22252b;font-size:10.5px;'>"
                    f"<td style='padding:4px 6px;font-family:var(--mono);font-weight:700;color:#f8fafc;'>{escape(un)}</td>"
                    f"<td style='padding:4px 6px;color:#cbd5e1;white-space:nowrap;'>{escape(fn)}</td>"
                    f"<td style='padding:4px 6px;color:#94a3b8;font-size:9.5px;font-family:var(--mono);'>{escape(em)}</td>"
                    f"<td style='padding:4px 6px;'>{badge}</td>"
                    f"<td style='padding:4px 6px;font-family:var(--mono);font-size:9.5px;color:#38bdf8;'>{escape(st_sc)}</td>"
                    f"<td style='padding:4px 6px;font-family:var(--mono);font-size:9.5px;color:#64748b;'>{cr}</td>"
                    f"<td style='padding:4px 6px;text-align:right;'>"
                    f"<a href='?rbac_user={escape(un)}{auth_suffix}' target='_self' style='text-decoration:none;display:inline-block;padding:2px 7px;border-radius:2px;font-size:8.5px;font-weight:700;{btn_style}'>"
                    f"{'● ACTIVE' if is_sel else 'INSPECT ↗'}</a></td>"
                    f"</tr>"
                )

            tbody_content = "".join(user_rows_html) if user_rows_html else '<tr><td colspan="7" style="text-align:center;padding:16px;color:#64748b;">No enterprise accounts match active filters.</td></tr>'

            st.markdown(f"""
            <div style="border:1px solid #2c3235;border-radius:2px;overflow:hidden;background:#181b1f;max-height:calc(100vh - 275px);min-height:360px;overflow-y:auto;">
              <table style="width:100%;border-collapse:collapse;text-align:left;">
                <thead>
                  <tr style="background:#141619;border-bottom:1px solid #2c3235;font-size:9px;font-weight:700;text-transform:uppercase;color:#94a3b8;letter-spacing:0.04em;position:sticky;top:0;z-index:2;">
                    <th style="padding:4px 6px;">Username</th>
                    <th style="padding:4px 6px;">Full Name</th>
                    <th style="padding:4px 6px;">Email</th>
                    <th style="padding:4px 6px;">Role</th>
                    <th style="padding:4px 6px;">State Scope</th>
                    <th style="padding:4px 6px;">Created</th>
                    <th style="padding:4px 6px;text-align:right;">Action</th>
                  </tr>
                </thead>
                <tbody>{tbody_content}</tbody>
              </table>
            </div>
            """, unsafe_allow_html=True)

        with col_detail:
            # Person Switcher Dropdown (Restricted to in-scope users)
            if in_scope_unames:
                picked_u = st.selectbox(
                    "Inspect Account",
                    in_scope_unames,
                    index=in_scope_unames.index(cur_selected) if cur_selected in in_scope_unames else 0,
                    key="rbac_detail_user_sync_select",
                    label_visibility="collapsed",
                    help="Switch inspected enterprise account",
                )
                if picked_u != cur_selected:
                    ss["rbac_selected_user"] = picked_u
                    cur_selected = picked_u

                target_dict = next((u for u in filtered_users if u["username"] == cur_selected), filtered_users[0])
                _render_user_detail_inspector(
                    target_user=target_dict,
                    all_users=all_users,
                    user_audit_logs=audit_logs,
                    is_active_admin=is_admin,
                    active_user=active_user,
                    db_path=db_path,
                    auth_suffix=auth_suffix,
                )
            else:
                st.info("No matching accounts to inspect.")

    # ==========================================================================
    # TAB 2: PROVISION ENTERPRISE ACCOUNT
    # ==========================================================================
    with tab_prov:
        prov_c1, prov_c2 = st.columns([1.1, 1.9], gap="medium")

        with prov_c1:
            st.markdown(ui.panel_header("Provision Enterprise Account", color="#ec4899", count="Admin Only"), unsafe_allow_html=True)
            if not is_admin:
                st.markdown(f"""
                <div style="background:rgba(239,68,68,0.08);border:1px solid rgba(239,68,68,0.25);border-radius:2px;padding:12px;color:#fca5a5;font-size:11px;">
                  <b>Access Denied:</b> Provisioning enterprise accounts and assigning security roles requires <b>Admin</b> privileges. Current active session (<code>{escape(active_user)}</code>) is read-only.
                </div>
                """, unsafe_allow_html=True)
            else:
                with st.form("rbac_prov_new_user_form", clear_on_submit=True):
                    f_c1, f_c2 = st.columns(2)
                    with f_c1:
                        p_username = st.text_input("Username *", key="prov_f_uname", placeholder="e.g. jdoe_sec", autocomplete="username")
                        p_password = st.text_input("Temporary Password (min 6) *", type="password", key="prov_f_pwd", autocomplete="new-password")
                    with f_c2:
                        p_fullname = st.text_input("Full Name", key="prov_f_fname", placeholder="e.g. John Doe", autocomplete="name")
                        p_email = st.text_input("Enterprise Email", key="prov_f_email", placeholder="e.g. jdoe@ets.gov", autocomplete="email")

                    f_r1, f_r2 = st.columns(2)
                    with f_r1:
                        p_role = st.selectbox("Enterprise Role *", ["Operator", "Viewer", "Auditor", "Admin"], index=0, key="prov_f_role")
                    with f_r2:
                        p_state = st.selectbox("State Scope Assignment", ["Global", "AK", "ND", "NH"], index=0, key="prov_f_state")

                    submitted = st.form_submit_button("⚡ Provision Enterprise User", type="primary", use_container_width=True)
                    if submitted:
                        if not p_username or not p_username.strip():
                            st.error("Username cannot be blank.")
                        elif len(p_password) < 6:
                            st.error("Password must be at least 6 characters.")
                        else:
                            try:
                                conn_w = get_connection(db_path)
                                create_user(
                                    conn_w,
                                    username=p_username.strip(),
                                    password=p_password,
                                    role=p_role,
                                    assigned_state=p_state if p_state != "Global" else None,
                                    full_name=p_fullname.strip(),
                                    email=p_email.strip(),
                                )
                                log_audit_event(
                                    conn_w,
                                    actor=active_user,
                                    role="Admin",
                                    action="USER_CREATED",
                                    target_entity=f"User: {p_username.strip()}",
                                    details=f"Provisioned role '{p_role}' with scope '{p_state}' ({p_fullname.strip() or 'No Name'}).",
                                )
                                conn_w.close()
                                st.success(f"✓ Successfully provisioned enterprise account '{p_username.strip()}' as {p_role} ({p_state})!")
                                st.rerun()
                            except Exception as ex:
                                st.error(f"Failed to create user: {ex}")

        with prov_c2:
            st.markdown(ui.panel_header("Zero-Trust Role Entitlements & Policy Guardrails", color="#5794f2", count="Least-Privilege Tiers"), unsafe_allow_html=True)
            st.markdown("""
            <div style="background:#181b1f;border:1px solid #2c3235;border-radius:2px;overflow:hidden;">
              <table style="width:100%;border-collapse:collapse;font-size:10.5px;">
                <thead>
                  <tr style="background:#141619;border-bottom:1px solid #2c3235;font-size:9.5px;font-weight:700;text-transform:uppercase;color:#94a3b8;">
                    <th style="padding:6px 10px;width:80px;">Role Tier</th>
                    <th style="padding:6px 10px;width:110px;">Security Clearance</th>
                    <th style="padding:6px 10px;">Entitled Operations &amp; Boundary Policy</th>
                  </tr>
                </thead>
                <tbody>
                  <tr style="border-bottom:1px solid #22252b;">
                    <td style="padding:6px 10px;"><span class="alert-chip firing">Admin</span></td>
                    <td style="padding:6px 10px;color:#f87171;font-weight:700;font-size:10px;">Root Controller</td>
                    <td style="padding:6px 10px;color:#cbd5e1;">Full privilege across all states, user account provisioning, role updates, revocation, and immutable ledger oversight.</td>
                  </tr>
                  <tr style="border-bottom:1px solid #22252b;">
                    <td style="padding:6px 10px;"><span class="alert-chip pending">Operator</span></td>
                    <td style="padding:6px 10px;color:#fbbf24;font-weight:700;font-size:10px;">State Operations</td>
                    <td style="padding:6px 10px;color:#cbd5e1;">State-scoped expiry extensions, batch renewals, rollback execution, and SMTP escalation alert dispatching.</td>
                  </tr>
                  <tr style="border-bottom:1px solid #22252b;">
                    <td style="padding:6px 10px;"><span class="alert-chip ok" style="color:#10b981;border-color:rgba(16,185,129,0.35);">Auditor</span></td>
                    <td style="padding:6px 10px;color:#34d399;font-weight:700;font-size:10px;">Compliance Lead</td>
                    <td style="padding:6px 10px;color:#cbd5e1;">Read-only inspection of forensic audit records, integrity verification, export of compliance evidence, and posture reporting.</td>
                  </tr>
                  <tr>
                    <td style="padding:6px 10px;"><span class="alert-chip ok" style="color:#38bdf8;border-color:rgba(56,189,248,0.35);">Viewer</span></td>
                    <td style="padding:6px 10px;color:#38bdf8;font-weight:700;font-size:10px;">Observer</td>
                    <td style="padding:6px 10px;color:#cbd5e1;">Read-only access to operations dashboards, release plans, and telemetry. Zero write or export privileges.</td>
                  </tr>
                </tbody>
              </table>
            </div>
            """, unsafe_allow_html=True)

    # ==========================================================================
    # TAB 3: COMPLIANCE & IMMUTABLE AUDIT LEDGER
    # ==========================================================================
    with tab_audit:
        audit_rows_html = []
        for idx, a in enumerate(filtered_audits[:150]):
            chip = _ACTION_CHIPS.get(a.get("action", ""), f'<span class="alert-chip ok" style="font-size:8.5px;">{a.get("action")}</span>')
            ts_str = str(a.get("timestamp", ""))[:19].replace("T", " ")
            row_bg = "background:#181b1f;" if idx % 2 == 0 else "background:#141619;"

            audit_rows_html.append(
                f"<tr style='{row_bg}border-bottom:1px solid #22252b;font-size:10.5px;'>"
                f"<td style='padding:4px 6px;font-family:var(--mono);font-size:9.5px;color:#64748b;white-space:nowrap;'>{ts_str}</td>"
                f"<td style='padding:4px 6px;font-weight:700;color:#f8fafc;font-family:var(--mono);'>{escape(str(a.get('actor', 'system')))}</td>"
                f"<td style='padding:4px 6px;font-size:9.5px;color:#94a3b8;'>{escape(str(a.get('role', 'Viewer')))}</td>"
                f"<td style='padding:4px 6px;'>{chip}</td>"
                f"<td style='padding:4px 6px;font-weight:600;color:#cbd5e1;font-size:10px;white-space:nowrap;'>{escape(str(a.get('target_entity', '—')))}</td>"
                f"<td style='padding:4px 6px;color:#94a3b8;font-size:10px;max-width:340px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;' title='{escape(str(a.get('details', '')))}'>{escape(str(a.get('details', '')))}</td>"
                f"<td style='padding:4px 6px;font-family:var(--mono);font-size:9.5px;color:#64748b;'>{escape(str(a.get('ip_address', '127.0.0.1')))}</td>"
                f"</tr>"
            )

        audit_body = "".join(audit_rows_html) if audit_rows_html else '<tr><td colspan="7" style="text-align:center;padding:16px;color:#64748b;">No security audit records match active filter criteria.</td></tr>'

        st.markdown(f"""
        <div style="border:1px solid #2c3235;border-radius:2px;overflow:hidden;background:#181b1f;max-height:calc(100vh - 275px);min-height:360px;overflow-y:auto;">
          <table style="width:100%;border-collapse:collapse;text-align:left;">
            <thead>
              <tr style="background:#141619;border-bottom:1px solid #2c3235;font-size:9px;font-weight:700;text-transform:uppercase;color:#94a3b8;letter-spacing:0.04em;position:sticky;top:0;z-index:2;">
                <th style="padding:4px 6px;">Timestamp (UTC)</th>
                <th style="padding:4px 6px;">Actor</th>
                <th style="padding:4px 6px;">Role</th>
                <th style="padding:4px 6px;">Action Category</th>
                <th style="padding:4px 6px;">Target Entity</th>
                <th style="padding:4px 6px;">Audit Details</th>
                <th style="padding:4px 6px;">Source IP</th>
              </tr>
            </thead>
            <tbody>{audit_body}</tbody>
          </table>
        </div>
        """, unsafe_allow_html=True)

    # ==========================================================================
    # TAB 4: ZERO-TRUST POLICY MATRIX & SESSION SECURITY
    # ==========================================================================
    with tab_policy:
        pol_c1, pol_c2 = st.columns([1.35, 1.0], gap="medium")

        with pol_c1:
            st.markdown(ui.panel_header("Zero-Trust Privilege Cross-Tab Matrix", color="#38bdf8", count="Role vs Capability"), unsafe_allow_html=True)

            capabilities = [
                ("View Dashboards & Roster Flights", "✓ ALLOWED", "✓ ALLOWED", "✓ ALLOWED", "✓ ALLOWED"),
                ("Inspect Production Support Shifts", "✓ ALLOWED", "✓ ALLOWED", "✓ ALLOWED", "✓ ALLOWED"),
                ("Export Incident & Audit CSV", "✕ DENIED", "✓ ALLOWED", "✓ ALLOWED", "✓ ALLOWED"),
                ("Modify Expiry Dates & Overrides", "✕ DENIED", "✕ DENIED", "✓ SCOPED", "✓ FULL"),
                ("Trigger Batch Workflows & Rollbacks", "✕ DENIED", "✕ DENIED", "✓ SCOPED", "✓ FULL"),
                ("Dispatch SMTP Escalation Notices", "✕ DENIED", "✕ DENIED", "✓ SCOPED", "✓ FULL"),
                ("View Immutable Security Audit Log", "✕ DENIED", "✓ READ-ONLY", "✕ DENIED", "✓ FULL"),
                ("Provision Enterprise User Accounts", "✕ DENIED", "✕ DENIED", "✕ DENIED", "✓ FULL"),
                ("Modify Roles & Revoke Accounts", "✕ DENIED", "✕ DENIED", "✕ DENIED", "✓ FULL"),
            ]

            cap_rows = []
            for cap, v, a, o, ad in capabilities:
                def _badge(val: str) -> str:
                    if "ALLOWED" in val or "FULL" in val:
                        return '<span style="color:#10b981;font-weight:800;font-size:9.5px;">✓ ALLOWED</span>'
                    elif "SCOPED" in val:
                        return '<span style="color:#f59e0b;font-weight:800;font-size:9.5px;">⚡ SCOPED</span>'
                    elif "READ-ONLY" in val:
                        return '<span style="color:#38bdf8;font-weight:800;font-size:9.5px;">👁 READ</span>'
                    return '<span style="color:#64748b;font-size:9.5px;">✕ DENIED</span>'

                cap_rows.append(
                    f"<tr style='border-bottom:1px solid #22252b;font-size:10px;'>"
                    f"<td style='padding:5px 8px;font-weight:600;color:#f8fafc;'>{cap}</td>"
                    f"<td style='padding:5px 8px;text-align:center;'>{_badge(v)}</td>"
                    f"<td style='padding:5px 8px;text-align:center;'>{_badge(a)}</td>"
                    f"<td style='padding:5px 8px;text-align:center;'>{_badge(o)}</td>"
                    f"<td style='padding:5px 8px;text-align:center;'>{_badge(ad)}</td>"
                    f"</tr>"
                )

            st.markdown(f"""
            <div style="border:1px solid #2c3235;border-radius:2px;overflow:hidden;background:#181b1f;">
              <table style="width:100%;border-collapse:collapse;text-align:left;">
                <thead>
                  <tr style="background:#141619;border-bottom:1px solid #2c3235;font-size:9px;font-weight:700;text-transform:uppercase;color:#94a3b8;">
                    <th style="padding:5px 8px;">Capability / Entitlement</th>
                    <th style="padding:5px 8px;text-align:center;">Viewer</th>
                    <th style="padding:5px 8px;text-align:center;">Auditor</th>
                    <th style="padding:5px 8px;text-align:center;">Operator</th>
                    <th style="padding:5px 8px;text-align:center;">Admin</th>
                  </tr>
                </thead>
                <tbody>{''.join(cap_rows)}</tbody>
              </table>
            </div>
            """, unsafe_allow_html=True)

        with pol_c2:
            st.markdown(ui.panel_header("Cryptographic Architecture & Telemetry", color="#10b981", count="Zero-Trust Controls"), unsafe_allow_html=True)
            st.markdown("""
            <div style="background:#181b1f;border:1px solid #2c3235;border-radius:2px;padding:8px 10px;display:flex;flex-direction:column;gap:6px;font-size:10px;">
              <div style="display:flex;justify-content:space-between;padding:4px 6px;background:#141619;border-radius:2px;border:1px solid #22252b;">
                <span style="color:#94a3b8;">Hashing Standard:</span>
                <b style="color:#10b981;font-family:var(--mono);">PBKDF2-HMAC-SHA256</b>
              </div>
              <div style="display:flex;justify-content:space-between;padding:4px 6px;background:#141619;border-radius:2px;border:1px solid #22252b;">
                <span style="color:#94a3b8;">Derivation Iterations:</span>
                <b style="color:#f8fafc;font-family:var(--mono);">100,000 Rounds</b>
              </div>
              <div style="display:flex;justify-content:space-between;padding:4px 6px;background:#141619;border-radius:2px;border:1px solid #22252b;">
                <span style="color:#94a3b8;">Entropy Salt Length:</span>
                <b style="color:#38bdf8;font-family:var(--mono);">16 Bytes CSPRNG</b>
              </div>
              <div style="display:flex;justify-content:space-between;padding:4px 6px;background:#141619;border-radius:2px;border:1px solid #22252b;">
                <span style="color:#94a3b8;">Comparison Guard:</span>
                <b style="color:#38bdf8;font-family:var(--mono);">Constant-Time hmac.compare_digest</b>
              </div>
              <div style="display:flex;justify-content:space-between;padding:4px 6px;background:#141619;border-radius:2px;border:1px solid #22252b;">
                <span style="color:#94a3b8;">Audit Ledger Immutability:</span>
                <b style="color:#10b981;font-family:var(--mono);">Append-Only SQLite Ledger</b>
              </div>
              <div style="display:flex;justify-content:space-between;padding:4px 6px;background:#141619;border-radius:2px;border:1px solid #22252b;">
                <span style="color:#94a3b8;">Session State Isolation:</span>
                <b style="color:#38bdf8;font-family:var(--mono);">Server-Side Session Store</b>
              </div>
            </div>
            """, unsafe_allow_html=True)

    # ==========================================================================
    # TAB 5: ACCESS ANALYTICS & SECURITY TELEMETRY
    # ==========================================================================
    with tab_telemetry:
        tel_c1, tel_c2 = st.columns(2, gap="medium")

        with tel_c1:
            st.markdown(ui.panel_header("Enterprise Role Distribution Breakdown", color="#5794f2", count=f"{len(all_users)} Total Accounts"), unsafe_allow_html=True)

            role_stats = [
                ("Admin", admin_count, "#ef4444", "Privileged Root Controllers"),
                ("Operator", op_count, "#f59e0b", "State-Scoped Write Engineers"),
                ("Auditor", audit_count, "#10b981", "Compliance & Forensics Inspectors"),
                ("Viewer", viewer_count, "#38bdf8", "Read-Only Operations Observers"),
            ]

            tot_u = max(1, len(all_users))
            bar_items = []
            for r_name, r_cnt, r_col, r_desc in role_stats:
                pct = (r_cnt / tot_u) * 100.0
                bar_items.append(
                    f"<div style='margin-bottom:8px;'>"
                    f"<div style='display:flex;justify-content:space-between;font-size:10px;margin-bottom:2px;'>"
                    f"<span style='font-weight:700;color:#f8fafc;'>{r_name} <span style='color:#64748b;font-weight:400;'>({r_desc})</span></span>"
                    f"<b style='font-family:var(--mono);color:{r_col};'>{r_cnt} ({pct:.1f}%)</b>"
                    f"</div>"
                    f"<div style='height:4px;border-radius:2px;background:#22252b;overflow:hidden;'>"
                    f"<div style='width:{pct}%;height:100%;background:{r_col};border-radius:2px;'></div>"
                    f"</div></div>"
                )

            st.markdown(f"""
            <div style="background:#181b1f;border:1px solid #2c3235;border-radius:2px;padding:10px 12px;">
              {''.join(bar_items)}
            </div>
            """, unsafe_allow_html=True)

        with tel_c2:
            st.markdown(ui.panel_header("Audit Activity Velocity by Category", color="#f59e0b", count=f"{len(audit_logs)} Total Recorded"), unsafe_allow_html=True)

            act_counts: dict[str, int] = {}
            for a in audit_logs:
                act = a.get("action", "OTHER")
                act_counts[act] = act_counts.get(act, 0) + 1

            sorted_acts = sorted(act_counts.items(), key=lambda x: x[1], reverse=True)
            tot_acts = max(1, len(audit_logs))

            act_bar_items = []
            for a_name, a_cnt in sorted_acts[:6]:
                pct = (a_cnt / tot_acts) * 100.0
                chip = _ACTION_CHIPS.get(a_name, f'<span class="alert-chip ok">{a_name}</span>')
                act_bar_items.append(
                    f"<div style='margin-bottom:6px;'>"
                    f"<div style='display:flex;align-items:center;justify-content:space-between;font-size:10px;margin-bottom:2px;'>"
                    f"<div>{chip}</div>"
                    f"<b style='font-family:var(--mono);color:#fbbf24;'>{a_cnt} events ({pct:.1f}%)</b>"
                    f"</div>"
                    f"<div style='height:3px;border-radius:1.5px;background:#22252b;overflow:hidden;'>"
                    f"<div style='width:{pct}%;height:100%;background:#f59e0b;'></div>"
                    f"</div></div>"
                )

            st.markdown(f"""
            <div style="background:#181b1f;border:1px solid #2c3235;border-radius:2px;padding:10px 12px;">
              {''.join(act_bar_items)}
            </div>
            """, unsafe_allow_html=True)
