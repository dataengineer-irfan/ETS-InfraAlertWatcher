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

from html import escape
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "dashboard"))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

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
    create_user,
    get_users,
    delete_user,
    update_user_role,
    log_audit_event,
    get_audit_logs,
    authenticate_user,
    get_current_active_releases,
    get_release_schedules,
    signup_user,
    create_reset_token,
    verify_reset_token,
    consume_reset_token,
    update_user_password,
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
    render_maintenance_cadence_email,
    dispatch_cadence_alert_real,
    send_password_reset_email,
)
from auth import (  # noqa: E402
    require_role,
    is_admin,
    can_write,
    check_state_scope,
    render_access_denied,
    render_role_badge,
    ROLE_ADMIN,
    ROLE_OPERATOR,
    ROLE_AUDITOR,
    ROLE_VIEWER,
)
from ingest_releases import run_release_ingest  # noqa: E402
from release_plan import render_release_plan_workspace
from on_call import render_on_call_workspace

DB_PATH = os.environ.get("EXPIRY_DB_PATH", str(ROOT / "data" / "expiry.db"))
WORKBOOK_DIR = os.environ.get("EXPIRY_WORKBOOK_DIR", str(ROOT))

STATES = ui.STATES
COMPONENT_ORDER = ui.COMPONENT_ORDER
ENV_ORDER = ui.ENV_ORDER

CANVAS_OVERVIEW = 840
CANVAS_STATE = 840
EDITOR_HEIGHT = 580

st.set_page_config(
    page_title="Expiry Watchtower - Enterprise Governance",
    layout="wide",
    initial_sidebar_state="collapsed",
)
st.markdown(ui.css(), unsafe_allow_html=True)

# Ensure Streamlit's static index.html is patched with resilience and hotkey handlers
try:
    from patch_streamlit import patch_index_html
    patch_index_html()
except Exception:
    pass




# ==========================================================================
# Data Loading & Ingestion
# ==========================================================================
@st.cache_data(ttl=600, show_spinner=False)
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


@st.cache_data(ttl=600, show_spinner=False)
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
    """Auto-ingest workbooks, release schedules, and on-call rosters on fresh startup if database table is empty."""
    conn = get_connection(DB_PATH)
    count = conn.execute("SELECT count(*) FROM component_records").fetchone()[0]
    rel_count = 0
    try:
        rel_count = conn.execute("SELECT count(*) FROM release_schedules").fetchone()[0]
    except Exception:
        pass
    roster_count = 0
    try:
        roster_count = conn.execute("SELECT count(*) FROM on_call_rosters").fetchone()[0]
    except Exception:
        pass
    conn.close()

    if not count:
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

    input_dir = ROOT / "_Input"
    if not rel_count and input_dir.exists():
        run_release_ingest(input_dir, DB_PATH)

    if not roster_count and input_dir.exists():
        for fpath in input_dir.glob("*.xlsx"):
            if "on call" in fpath.name.lower() or "roster" in fpath.name.lower():
                try:
                    from src.ingest_roster import ingest_roster_file
                    ingest_roster_file(str(fpath), DB_PATH)
                    break
                except Exception as e:
                    pass


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


# ==============================================================================
# Enterprise Access Control & Login Portal Gate
# ==============================================================================
def render_login_gate(db_path: str) -> None:
    """Renders the enterprise authentication portal (Login, Self-Service Signup, Password Reset)."""
    mode = st.session_state.setdefault("auth_mode", "login")

    col_l, col_center, col_r = st.columns([1, 1.4, 1])
    with col_center:
        # Display flash message if one was set (e.g. after successful signup or reset)
        flash_msg = st.session_state.pop("auth_flash_msg", None)
        if flash_msg:
            st.success(flash_msg)

        if mode == "signup":
            # -------------------------------------------------------------
            # Self-Service Signup Screen
            # -------------------------------------------------------------
            st.markdown("""
            <div style="text-align:center;margin-top:35px;margin-bottom:20px;">
              <div style="display:inline-flex;align-items:center;justify-content:center;width:56px;height:56px;border-radius:14px;background:rgba(56,189,248,0.12);border:1px solid rgba(56,189,248,0.35);margin-bottom:12px;">
                <span style="font-size:26px;">👤</span>
              </div>
              <div style="font-size:20px;font-weight:800;letter-spacing:-0.02em;color:#f8fafc;">CREATE ENTERPRISE ACCOUNT</div>
              <div style="font-size:10px;font-family:var(--mono);color:#38bdf8;font-weight:700;letter-spacing:0.12em;margin-top:3px;">SELF-SERVICE ONBOARDING</div>
              <div style="font-size:11.5px;color:#94a3b8;margin-top:6px;">Standard accounts are provisioned with <b>Viewer</b> role</div>
            </div>
            """, unsafe_allow_html=True)

            with st.form("portal_signup_form", clear_on_submit=False):
                st.markdown('<div style="font-size:11px;font-weight:700;color:#cbd5e1;text-transform:uppercase;letter-spacing:0.06em;margin-bottom:4px;">Full Name</div>', unsafe_allow_html=True)
                su_fullname = st.text_input("Full Name", key="signup_fullname", placeholder="e.g. Jane Doe", label_visibility="collapsed")

                st.markdown('<div style="font-size:11px;font-weight:700;color:#cbd5e1;text-transform:uppercase;letter-spacing:0.06em;margin-top:10px;margin-bottom:4px;">Enterprise Email *</div>', unsafe_allow_html=True)
                su_email = st.text_input("Enterprise Email", key="signup_email", placeholder="e.g. jdoe@infinite.com", label_visibility="collapsed")

                st.markdown('<div style="font-size:11px;font-weight:700;color:#cbd5e1;text-transform:uppercase;letter-spacing:0.06em;margin-top:10px;margin-bottom:4px;">Username (min 3 chars) *</div>', unsafe_allow_html=True)
                su_uname = st.text_input("Username", key="signup_uname", placeholder="e.g. jdoe", label_visibility="collapsed")

                st.markdown('<div style="font-size:11px;font-weight:700;color:#cbd5e1;text-transform:uppercase;letter-spacing:0.06em;margin-top:10px;margin-bottom:4px;">Password (min 8 chars, 1 number/symbol) *</div>', unsafe_allow_html=True)
                su_pwd = st.text_input("Password", type="password", key="signup_pwd", placeholder="••••••••••••", label_visibility="collapsed")

                st.markdown('<div style="font-size:11px;font-weight:700;color:#cbd5e1;text-transform:uppercase;letter-spacing:0.06em;margin-top:10px;margin-bottom:4px;">Confirm Password *</div>', unsafe_allow_html=True)
                su_pwd2 = st.text_input("Confirm Password", type="password", key="signup_pwd2", placeholder="••••••••••••", label_visibility="collapsed")

                st.markdown('<div style="margin-top:16px;"></div>', unsafe_allow_html=True)
                submit_signup = st.form_submit_button("Create Account", use_container_width=True, type="primary")

                if submit_signup:
                    if not su_uname.strip() or not su_email.strip() or not su_pwd:
                        st.error("Please fill in all required fields marked with *.")
                    elif su_pwd != su_pwd2:
                        st.error("Passwords do not match.")
                    else:
                        try:
                            conn = get_connection(db_path)
                            signup_user(conn, username=su_uname.strip(), password=su_pwd, email=su_email.strip(), full_name=su_fullname.strip())
                            conn.close()
                            st.session_state["auth_flash_msg"] = f"✓ Account '{su_uname.strip()}' successfully created! Please sign in with your credentials."
                            st.session_state["auth_mode"] = "login"
                            st.rerun()
                        except ValueError as ve:
                            st.error(str(ve))
                        except Exception as ex:
                            err_str = str(ex).lower()
                            if "users.email" in err_str:
                                st.error("An account with this email address already exists.")
                            elif "users.username" in err_str:
                                st.error("This username is already taken. Please choose another.")
                            else:
                                st.error(f"Registration failed: {ex}")

            if st.button("← Back to Sign In", key="btn_signup_back_login", use_container_width=True):
                st.session_state["auth_mode"] = "login"
                st.rerun()

        elif mode == "forgot_password":
            # -------------------------------------------------------------
            # Forgot / Reset Password Screen
            # -------------------------------------------------------------
            st.markdown("""
            <div style="text-align:center;margin-top:35px;margin-bottom:20px;">
              <div style="display:inline-flex;align-items:center;justify-content:center;width:56px;height:56px;border-radius:14px;background:rgba(245,158,11,0.12);border:1px solid rgba(245,158,11,0.35);margin-bottom:12px;">
                <span style="font-size:26px;">🔑</span>
              </div>
              <div style="font-size:20px;font-weight:800;letter-spacing:-0.02em;color:#f8fafc;">PASSWORD RECOVERY</div>
              <div style="font-size:10px;font-family:var(--mono);color:#f59e0b;font-weight:700;letter-spacing:0.12em;margin-top:3px;">ZERO-TRUST VERIFICATION</div>
              <div style="font-size:11.5px;color:#94a3b8;margin-top:6px;">Single-use cryptographic token validation (60-min expiry)</div>
            </div>
            """, unsafe_allow_html=True)

            fp_tab1, fp_tab2 = st.tabs(["1. Request Reset Code", "2. Enter Code & Set Password"])

            with fp_tab1:
                with st.form("request_code_form"):
                    st.markdown('<div style="font-size:11px;font-weight:700;color:#cbd5e1;text-transform:uppercase;letter-spacing:0.06em;margin-bottom:4px;">Account Username or Email</div>', unsafe_allow_html=True)
                    fp_ident = st.text_input("Identifier", key="forgot_ident_val", placeholder="Enter your username or email", label_visibility="collapsed")
                    req_btn = st.form_submit_button("Generate & Send Verification Code", use_container_width=True, type="primary")

                    if req_btn:
                        if not fp_ident.strip():
                            st.error("Please enter your account username or email.")
                        else:
                            conn = get_connection(db_path)
                            token_info = create_reset_token(conn, fp_ident.strip())
                            conn.close()

                            if token_info:
                                send_password_reset_email(
                                    to_email=token_info["email"],
                                    username=token_info["username"],
                                    reset_token=token_info["token"],
                                )
                                st.session_state["dev_reset_code"] = token_info["token"]
                                st.session_state["dev_reset_target"] = token_info["username"]
                            else:
                                st.session_state.pop("dev_reset_code", None)

                            st.success("✓ If an active account matches that identifier, a verification code has been dispatched. Enter it in tab 2 with your new password.")

                if "dev_reset_code" in st.session_state:
                    st.markdown(f"""
                    <div style="background:rgba(56,189,248,0.08);border:1px solid rgba(56,189,248,0.3);border-radius:4px;padding:10px 14px;margin-top:8px;font-size:11px;">
                      <div style="font-weight:700;color:#38bdf8;margin-bottom:3px;">⚡ Verification Code (Dev / Local Display):</div>
                      <div style="font-family:monospace;font-size:13px;color:#f8fafc;word-break:break-all;user-select:all;">{st.session_state['dev_reset_code']}</div>
                      <div style="color:#94a3b8;font-size:10px;margin-top:4px;">Account: <b>{st.session_state.get('dev_reset_target')}</b> · Valid for 60 minutes</div>
                    </div>
                    """, unsafe_allow_html=True)

            with fp_tab2:
                with st.form("consume_token_form"):
                    st.markdown('<div style="font-size:11px;font-weight:700;color:#cbd5e1;text-transform:uppercase;letter-spacing:0.06em;margin-bottom:4px;">Reset Verification Code</div>', unsafe_allow_html=True)
                    token_in = st.text_input("Verification Code", key="reset_token_input_val", value=st.session_state.get("dev_reset_code", ""), placeholder="Paste single-use token", label_visibility="collapsed")

                    st.markdown('<div style="font-size:11px;font-weight:700;color:#cbd5e1;text-transform:uppercase;letter-spacing:0.06em;margin-top:10px;margin-bottom:4px;">New Password (min 8 chars, 1 number/symbol)</div>', unsafe_allow_html=True)
                    new_p1 = st.text_input("New Password", type="password", key="reset_new_pwd1", placeholder="••••••••••••", label_visibility="collapsed")

                    st.markdown('<div style="font-size:11px;font-weight:700;color:#cbd5e1;text-transform:uppercase;letter-spacing:0.06em;margin-top:10px;margin-bottom:4px;">Confirm New Password</div>', unsafe_allow_html=True)
                    new_p2 = st.text_input("Confirm New Password", type="password", key="reset_new_pwd2", placeholder="••••••••••••", label_visibility="collapsed")

                    st.markdown('<div style="margin-top:14px;"></div>', unsafe_allow_html=True)
                    update_pwd_btn = st.form_submit_button("Update Password", use_container_width=True, type="primary")

                    if update_pwd_btn:
                        if not token_in.strip():
                            st.error("Verification code is required.")
                        elif not new_p1:
                            st.error("New password is required.")
                        elif new_p1 != new_p2:
                            st.error("Passwords do not match.")
                        else:
                            try:
                                conn = get_connection(db_path)
                                ok = consume_reset_token(conn, token_in.strip(), new_p1)
                                conn.close()
                                if ok:
                                    st.session_state.pop("dev_reset_code", None)
                                    st.session_state.pop("dev_reset_target", None)
                                    st.session_state["auth_flash_msg"] = "✓ Password successfully updated! Please sign in with your new credentials."
                                    st.session_state["auth_mode"] = "login"
                                    st.rerun()
                                else:
                                    st.error("Invalid or expired verification code. Please request a new one.")
                            except ValueError as ve:
                                st.error(str(ve))
                            except Exception as ex:
                                st.error(f"Password reset failed: {ex}")

            st.markdown('<div style="margin-top:12px;"></div>', unsafe_allow_html=True)
            if st.button("← Back to Sign In", key="btn_forgot_back_login", use_container_width=True):
                st.session_state["auth_mode"] = "login"
                st.rerun()

        else:
            # -------------------------------------------------------------
            # Default: Login Screen
            # -------------------------------------------------------------
            st.markdown("""
            <div style="text-align:center;margin-top:50px;margin-bottom:24px;">
              <div style="display:inline-flex;align-items:center;justify-content:center;width:60px;height:60px;border-radius:14px;background:rgba(56,189,248,0.12);border:1px solid rgba(56,189,248,0.35);margin-bottom:14px;">
                <span style="font-size:28px;">🛡️</span>
              </div>
              <div style="font-size:22px;font-weight:800;letter-spacing:-0.02em;color:#f8fafc;">ETS WATCHTOWER</div>
              <div style="font-size:10px;font-family:var(--mono);color:#38bdf8;font-weight:700;letter-spacing:0.12em;margin-top:3px;">ENTERPRISE ACCESS PORTAL</div>
              <div style="font-size:12px;color:#94a3b8;margin-top:8px;">Zero-Trust PBKDF2-HMAC-SHA256 Encrypted Session</div>
            </div>
            """, unsafe_allow_html=True)

            with st.form("portal_login_form", clear_on_submit=False):
                st.markdown('<div style="font-size:11px;font-weight:700;color:#cbd5e1;text-transform:uppercase;letter-spacing:0.06em;margin-bottom:6px;">Username</div>', unsafe_allow_html=True)
                u_input = st.text_input("Username", key="auth_login_username", placeholder="Enter username (e.g. admin)", label_visibility="collapsed")

                st.markdown('<div style="font-size:11px;font-weight:700;color:#cbd5e1;text-transform:uppercase;letter-spacing:0.06em;margin-top:14px;margin-bottom:6px;">Password</div>', unsafe_allow_html=True)
                p_input = st.text_input("Password", type="password", key="auth_login_password", placeholder="••••••••••••", label_visibility="collapsed")

                st.markdown('<div style="margin-top:16px;"></div>', unsafe_allow_html=True)
                submit_login = st.form_submit_button("Sign In to Watchtower", use_container_width=True, type="primary")

                if submit_login:
                    if not u_input.strip() or not p_input:
                        st.error("Please enter both username and password.")
                    else:
                        conn = get_connection(db_path)
                        user = authenticate_user(conn, u_input, p_input)
                        conn.close()
                        if user:
                            st.session_state["authenticated"] = True
                            st.session_state["active_user"] = user["username"]
                            st.session_state["user_role"] = user["role"]
                            st.session_state["user_full_name"] = user.get("full_name") or user["username"]
                            st.session_state["assigned_state"] = user.get("assigned_state")
                            st.query_params["_auth_user"] = user["username"]
                            st.rerun()
                        else:
                            st.error("Authentication failed: Invalid username or password.")

            # Action navigation links below the form
            opt_c1, opt_c2 = st.columns(2)
            with opt_c1:
                if st.button("🔑 Forgot Password?", key="btn_nav_forgot", use_container_width=True):
                    st.session_state["auth_mode"] = "forgot_password"
                    st.rerun()
            with opt_c2:
                if st.button("✨ Create Account", key="btn_nav_signup", use_container_width=True):
                    st.session_state["auth_mode"] = "signup"
                    st.rerun()


if not st.session_state.get("_ingested_verified", False):
    ensure_ingested()
    st.session_state["_ingested_verified"] = True

# Session State Initialization & Authentication Gate
_qp_user = st.query_params.get("_auth_user")
if _qp_user and not st.session_state.get("authenticated", False):
    try:
        conn_auth = get_connection(DB_PATH)
        u_data = conn_auth.execute("SELECT username, role, full_name, assigned_state FROM users WHERE username = ?", (_qp_user,)).fetchone()
        conn_auth.close()
        if u_data:
            st.session_state["authenticated"] = True
            st.session_state["active_user"] = u_data[0]
            st.session_state["user_role"] = u_data[1]
            st.session_state["user_full_name"] = u_data[2] or u_data[0]
            st.session_state["assigned_state"] = u_data[3]
    except Exception:
        pass

if "authenticated" not in st.session_state:
    st.session_state["authenticated"] = False
    st.session_state["active_user"] = None
    st.session_state["user_role"] = None
    st.session_state["user_full_name"] = None
    st.session_state["assigned_state"] = None

if not st.session_state.get("authenticated", False):
    render_login_gate(DB_PATH)
    st.stop()

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


def apply_edits(changes: list, state_scope: str | None = None) -> None:
    if not can_write():
        st.error("Access Denied: Modifying expiry dates requires Operator or Admin role.")
        return
    if state_scope and not check_state_scope(state_scope):
        st.error(f"Access Denied: Your assigned state scope ({st.session_state.get('assigned_state')}) does not permit modifying {state_scope} records.")
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

            can_modify = can_write() and check_state_scope(state)
            save_col, note_col = st.columns([1, 3])
            if save_col.button("Save changes", type="primary", use_container_width=True,
                                disabled=not changes or not can_modify):
                if can_modify:
                    apply_edits(changes, state_scope=state)
                    st.session_state["mg_saved"] = len(changes)
                    rerun()
                else:
                    st.error(f"Access Denied: Your role ({st.session_state.get('user_role')}) cannot modify {state} records.")
            n_chg = len(changes)
            if not can_modify:
                note_col.markdown(ui.note(
                    f"<b>Read-Only Mode:</b> Modifying {state} records requires Operator ({state}) or Admin role."
                ), unsafe_allow_html=True)
            elif changes:
                note_col.markdown(ui.note(
                    f"<b>{n_chg}</b> unsaved {'change' if n_chg == 1 else 'changes'} — press Save changes to apply."
                ), unsafe_allow_html=True)
            else:
                note_col.markdown(ui.note("Change a date above to enable saving."), unsafe_allow_html=True)
        else:
            can_modify = can_write() and check_state_scope(state)
            labels = {f"{r.schema_name} · {r.env_label} · {r.exp_date}": r.id
                      for r in work.itertuples()}
            pick = st.selectbox("Record", list(labels), key="mg_pick")
            row = work[work["id"] == labels[pick]].iloc[0]
            with st.form("mg_form"):
                new_date = st.date_input("New expiry date", value=row["exp_dt"].date())
                if st.form_submit_button("Save change", type="primary", disabled=not can_modify):
                    if can_modify:
                        apply_edits([(row["id"], new_date)], state_scope=state)
                        st.session_state["mg_saved"] = 1
                        rerun()
                    else:
                        st.error(f"Access Denied: Modifying {state} records requires Operator ({state}) or Admin role.")

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
    cell_team = st.session_state.setdefault("op_cell_team", None)
    cell_env = st.session_state.setdefault("op_cell_env", None)
    tree_open = st.session_state.setdefault("op_tree_open", set())
    selected_entity_ids = st.session_state.setdefault("op_selected_entity_ids", set())

    # Reactive URL Query Parameter Handling for 1-Click Interactive Filtering
    active_user = st.session_state.get("active_user", "admin")
    auth_suffix = f"&_auth_user={active_user}&tab=2"

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
        if qp_subtab == "insp":
            st.session_state["op_target_tab"] = "🔍 Inspector"
        elif qp_subtab == "batch":
            st.session_state["op_target_tab"] = "⚡ Batch"
        elif qp_subtab == "tree":
            st.session_state["op_target_tab"] = "🌳 Hierarchy"
        elif qp_subtab == "rev":
            st.session_state["op_target_tab"] = "↩️ Rollback"
        del st.query_params["op_subtab"]

    active_scope = st.session_state.get("_override_canvas_state")
    op_st_key = f"op_state_{reset_idx}"
    if active_scope and active_scope in STATES and op_st_key not in st.session_state:
        st.session_state[op_st_key] = active_scope

    # --------------------------------------------------------------------------
    # 1. UNIVERSAL 1-LINE COMMAND BAR (Brand & Scope | 5 Slicers | Telemetry & Actions)
    # --------------------------------------------------------------------------
    c_brand, c_f1, c_f2, c_f3, c_f4, c_f5, c_telem, c_csv, c_reset = st.columns(
        [1.8, 1.25, 0.85, 0.85, 0.95, 0.85, 1.7, 0.55, 0.35],
        gap="small"
    )

    with c_f1:
        q = st.text_input("Filter", key=f"op_search_{reset_idx}", placeholder="🔍 Search...", label_visibility="collapsed")
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
        st.markdown(f"""
        <div style="display:flex;align-items:center;gap:6px;height:30px;padding-top:2px;" title="Portfolio Operations Hub · Cross-Tab Multi-Team Expiry & Asset Inventory">
            <div style="width:3px;height:18px;background:#f59e0b;border-radius:1px;flex:none;"></div>
            <span style="font-size:11px;font-weight:800;letter-spacing:0.04em;color:#f8fafc;white-space:nowrap;">OPERATIONS HUB</span>
            <span style="font-size:7.5px;font-weight:800;background:rgba(16,185,129,0.18);color:#10b981;border:1px solid rgba(16,185,129,0.35);padding:1px 5px;border-radius:2px;white-space:nowrap;">LIVE</span>
            <span style="font-size:9px;color:#94a3b8;font-family:var(--mono);white-space:nowrap;">({len(filtered)}/{len(df)})</span>
        </div>
        """, unsafe_allow_html=True)

    with c_telem:
        st.markdown(f"""
        <div style="display:flex;align-items:center;justify-content:flex-end;gap:5px;height:30px;line-height:1;box-sizing:border-box;">
            {sla_callout}
            {debt_callout}
            <span style="font-size:8px;color:#64748b;font-family:var(--mono);white-space:nowrap;">{_utc_now}</span>
        </div>
        """, unsafe_allow_html=True)

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
        if st.button("↺", key="op_sc_reset", use_container_width=True, type="secondary", help="Reset all filters"):
            st.session_state["op_reset_idx"] = reset_idx + 1
            st.session_state["op_kpi_filter"] = "All"
            st.session_state["op_cell_filter"] = None
            st.session_state["op_cell_team"] = None
            st.session_state["op_cell_env"] = None
            st.session_state["op_heatmap_dim"] = "auto"
            st.session_state["op_tree_open"] = set()
            st.session_state["op_selected_entity_ids"] = set()
            st.session_state["op_batch_page_no"] = 0
            rerun()

    # 3. Executive Metric Ribbon — Clickable Grafana stat cards (Single 48px row)
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

        return f'''
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
        '''

    def _render_html(html_str: str) -> None:
        """Render HTML safely without markdown 4-space code-block escaping."""
        cleaned = "\n".join(line.strip() for line in html_str.splitlines() if line.strip())
        st.markdown(cleaned, unsafe_allow_html=True)

    c_k1 = _make_kpi_card_html("Portfolio Scope", f"{scope_cnt} / {tot_cnt}", k1_sub, "ALL FLEET" if k1_active else "SCOPED", "ok", k1_active, "All", spark_vals=_scope_trend)
    c_k2 = _make_kpi_card_html("Expired Items", exp_cnt, k2_sub, "FIRING" if exp_cnt else "CLEAR", "firing" if exp_cnt else "ok", k2_active, "Expired", spark_vals=_exp_trend)
    c_k3 = _make_kpi_card_html("Critical & Warning", crit_cnt + warn_cnt, k3_sub, "PENDING" if (crit_cnt + warn_cnt) else "STABLE", "pending" if (crit_cnt + warn_cnt) else "ok", k3_active, "Urgent", spark_vals=_cw_trend)
    c_k4 = _make_kpi_card_html("Healthy Entities", hlth_cnt, k4_sub, "COMPLIANT", "ok", k4_active, "Healthy", donut_val=pct_local)

    _render_html(f'''
    <div style="display:grid;grid-template-columns:repeat(4, 1fr);gap:6px;margin-bottom:4px;">
      {c_k1}
      {c_k2}
      {c_k3}
      {c_k4}
    </div>
    ''')

    # 4. Cross-Filter Visual Feedback Banner
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

    # --------------------------------------------------------------------------
    # 5. RESOLVE ACTIVE SELECTION
    # --------------------------------------------------------------------------
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

    # --------------------------------------------------------------------------
    # 6. TIER 3: COMMAND CENTER GRID (58% Matrix Heatmap / 42% Risk & SLA Telemetry)
    # --------------------------------------------------------------------------
    mat_col, sla_col = st.columns([5.8, 4.2], gap="small")

    with mat_col:
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
            return f'<a href="?op_dim={d_id}{auth_suffix}" target="_self" style="text-decoration:none;font-size:7.5px;padding:1.5px 5px;border-radius:2px;line-height:1;display:inline-block;{style}">{d_label}</a>'

        dim_pills = f"""<div style="display:inline-flex;align-items:center;gap:3px;"><span style="font-size:7.5px;color:#64748b;text-transform:uppercase;letter-spacing:0.03em;">AXIS:</span>{_dim_pill('auto', 'Auto')}{_dim_pill('state', 'State')}{_dim_pill('team', 'Team')}{_dim_pill('env', 'Env')}</div>"""

        clear_hm_link = f'<a href="?op_cell=clear{auth_suffix}" target="_self" style="font-size:8px;color:#f59e0b;font-weight:700;text-decoration:none;border:1px solid #f59e0b;padding:1px 5px;border-radius:2px;line-height:1;display:inline-block;">✕ Clear</a>' if (cell_filter or cell_team or cell_env) else ''

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
                            f'<td style="padding:1px;">'
                            f'<div style="background:rgba(255,255,255,0.015);border:1px solid #22262a;border-radius:2px;height:32px;display:flex;align-items:center;justify-content:center;color:#475569;font-size:10px;font-family:var(--mono);">'
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

                        c_border = "1.5px solid #38bdf8;box-shadow:0 0 8px rgba(56,189,248,0.3);background:rgba(56,189,248,0.12);" if is_cell_active else f"1px solid #2c3235;border-top:2px solid {c_color};background:{c_fill};"
                        cd_str = ui.fmt_heatmap_time(min_days)

                        td_cells.append(
                            f'<td style="padding:1px;">'
                            f'<a href="?op_cell={st_val}:{c_val}{auth_suffix}" target="_self" style="text-decoration:none;display:block;">'
                            f'<div style="{c_border};border-radius:2px;padding:2px 4px;height:32px;box-sizing:border-box;display:flex;flex-direction:column;justify-content:space-between;cursor:pointer;" title="Filter to {st_val} × {c_code} ({c_cnt} assets · soonest {cd_str})">'
                            f'<div style="display:flex;align-items:center;justify-content:space-between;line-height:1;">'
                            f'<span style="font-family:var(--mono);font-size:11px;font-weight:800;color:#f8fafc;">{c_cnt}</span>'
                            f'<span style="font-size:7.5px;font-weight:700;color:{c_color};">{c_badge}</span>'
                            f'</div>'
                            f'<div style="font-size:7.5px;color:{c_color};font-family:var(--mono);line-height:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">'
                            f'{cd_str}</div>'
                            f'</div></a></td>'
                        )

                _st_worst = ui.worst_band(st_sub["band"].tolist()) if not st_sub.empty else "Healthy"
                _st_color = ui.BAND_META[_st_worst]["color"]
                tot_td = f'<td style="padding:1px;text-align:center;"><div style="font-family:var(--mono);font-size:11.5px;font-weight:800;color:{_st_color};line-height:32px;">{len(st_sub)}</div></td>'

                hm_tr_list.append(
                    f'<tr style="{opacity_style}">'
                    f'<td style="padding:1px;">'
                    f'<a href="?op_cell={st_val}:{auth_suffix}" target="_self" style="text-decoration:none;display:block;">'
                    f'<div style="background:{st_bg};border:{st_border};color:{st_color};border-radius:2px;padding:0 4px;height:32px;display:flex;align-items:center;justify-content:center;font-size:10.5px;font-weight:800;cursor:pointer;" title="Filter to State {st_val}">'
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
                            f'<td style="padding:1px;">'
                            f'<div style="background:rgba(255,255,255,0.015);border:1px solid #22262a;border-radius:2px;height:20px;display:flex;align-items:center;justify-content:center;color:#475569;font-size:9px;font-family:var(--mono);">'
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

                        c_border = "1.5px solid #38bdf8;box-shadow:0 0 8px rgba(56,189,248,0.3);background:rgba(56,189,248,0.12);" if is_cell_active else f"1px solid #2c3235;border-top:2px solid {c_color};background:{c_fill};"
                        cd_str = ui.fmt_heatmap_time(min_days)
                        cell_st_val = state_filter if state_filter != "All States" else ""

                        td_cells.append(
                            f'<td style="padding:1px;">'
                            f'<a href="?op_cell={cell_st_val}:{c_val}:{tm_val}{auth_suffix}" target="_self" style="text-decoration:none;display:block;">'
                            f'<div style="{c_border};border-radius:2px;padding:0 3px;height:20px;box-sizing:border-box;display:flex;align-items:center;justify-content:space-between;cursor:pointer;" title="Filter to {tm_val} × {c_code} ({c_cnt} assets · soonest {cd_str})">'
                            f'<span style="font-family:var(--mono);font-size:9.5px;font-weight:800;color:#f8fafc;">{c_cnt}</span>'
                            f'<span style="font-size:7.5px;font-weight:700;color:{c_color};white-space:nowrap;">{c_badge}</span>'
                            f'</div></a></td>'
                        )

                _tm_worst = ui.worst_band(tm_sub["band"].tolist()) if not tm_sub.empty else "Healthy"
                _tm_color = ui.BAND_META[_tm_worst]["color"]
                tot_td = f'<td style="padding:1px;text-align:center;"><div style="font-family:var(--mono);font-size:10px;font-weight:800;color:{_tm_color};line-height:20px;">{len(tm_sub)}</div></td>'
                cell_st_val = state_filter if state_filter != "All States" else ""

                hm_tr_list.append(
                    f'<tr>'
                    f'<td style="padding:1px;">'
                    f'<a href="?op_cell={cell_st_val}::{tm_val}{auth_suffix}" target="_self" style="text-decoration:none;display:block;">'
                    f'<div style="background:{tm_bg};border:{tm_border};color:{tm_color};border-radius:2px;padding:0 4px;height:20px;display:flex;align-items:center;justify-content:flex-start;font-size:9px;font-weight:800;cursor:pointer;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;" title="Filter to Team {tm_val}">'
                    f'👥 {tm_val}</div></a></td>'
                    f'{"".join(td_cells)}'
                    f'{tot_td}'
                    f'</tr>'
                )

        else: # resolved_dim == "env"
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
                            f'<td style="padding:1px;">'
                            f'<div style="background:rgba(255,255,255,0.015);border:1px solid #22262a;border-radius:2px;height:20px;display:flex;align-items:center;justify-content:center;color:#475569;font-size:9px;font-family:var(--mono);">'
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

                        c_border = "1.5px solid #38bdf8;box-shadow:0 0 8px rgba(56,189,248,0.3);background:rgba(56,189,248,0.12);" if is_cell_active else f"1px solid #2c3235;border-top:2px solid {c_color};background:{c_fill};"
                        cd_str = ui.fmt_heatmap_time(min_days)
                        cell_st_val = state_filter if state_filter != "All States" else ""

                        td_cells.append(
                            f'<td style="padding:1px;">'
                            f'<a href="?op_cell={cell_st_val}:{c_val}::{env_val}{auth_suffix}" target="_self" style="text-decoration:none;display:block;">'
                            f'<div style="{c_border};border-radius:2px;padding:0 3px;height:20px;box-sizing:border-box;display:flex;align-items:center;justify-content:space-between;cursor:pointer;" title="Filter to {env_val} × {c_code} ({c_cnt} assets · soonest {cd_str})">'
                            f'<span style="font-family:var(--mono);font-size:9.5px;font-weight:800;color:#f8fafc;">{c_cnt}</span>'
                            f'<span style="font-size:7.5px;font-weight:700;color:{c_color};white-space:nowrap;">{c_badge}</span>'
                            f'</div></a></td>'
                        )

                _env_worst = ui.worst_band(env_sub["band"].tolist()) if not env_sub.empty else "Healthy"
                _env_color = ui.BAND_META[_env_worst]["color"]
                tot_td = f'<td style="padding:1px;text-align:center;"><div style="font-family:var(--mono);font-size:10px;font-weight:800;color:{_env_color};line-height:20px;">{len(env_sub)}</div></td>'
                cell_st_val = state_filter if state_filter != "All States" else ""

                hm_tr_list.append(
                    f'<tr>'
                    f'<td style="padding:1px;">'
                    f'<a href="?op_cell={cell_st_val}:::{env_val}{auth_suffix}" target="_self" style="text-decoration:none;display:block;">'
                    f'<div style="background:{env_bg};border:{env_border};color:{env_color};border-radius:2px;padding:0 4px;height:20px;display:flex;align-items:center;justify-content:center;font-size:9px;font-weight:800;cursor:pointer;" title="Filter to Env {env_val}">'
                    f'🏷️ {env_val}</div></a></td>'
                    f'{"".join(td_cells)}'
                    f'{tot_td}'
                    f'</tr>'
                )

        _render_html(f'''
        <div style="background:#181b1f;border:1px solid #2c3235;border-radius:3px;padding:4px 8px;box-sizing:border-box;height:160px;min-height:160px;display:flex;flex-direction:column;justify-content:space-between;">
          <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:2px;">
            <div style="font-size:9.5px;font-weight:700;color:#f59e0b;text-transform:uppercase;letter-spacing:0.04em;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:300px;">
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
                <th style="width:14%;text-align:center;padding:1px 0;">{col0_header}</th>
                <th style="width:19%;text-align:center;padding:1px 0;">🔑 CRYPTO</th>
                <th style="width:19%;text-align:center;padding:1px 0;">🔒 DBPWD</th>
                <th style="width:19%;text-align:center;padding:1px 0;">📦 SWVER</th>
                <th style="width:19%;text-align:center;padding:1px 0;">🛠️ PATCH</th>
                <th style="width:10%;text-align:center;padding:1px 0;">TOTAL</th>
              </tr>
            </thead>
            <tbody>
              {''.join(hm_tr_list)}
            </tbody>
          </table>
        </div>
        ''')

    with sla_col:
        dist_source = df[df["id"].isin(selected_entity_ids)] if selected_entity_ids else filtered
        dist_rows = []
        for c_val in COMPONENT_ORDER:
            c_sub = dist_source[dist_source["component"] == c_val]
            c_cnt = len(c_sub)
            c_code = ui.COMPONENT_CODE.get(c_val, c_val)
            if c_cnt == 0:
                dist_rows.append(
                    f'<div class="dist-row" style="margin-bottom:1px;display:flex;align-items:center;gap:6px;">'
                    f'<div class="dist-name" style="font-size:8.5px;width:48px;font-weight:700;">{c_code}</div>'
                    f'<div class="dist-track" style="height:5px;flex:1;background:#212429;border-radius:1px;"></div>'
                    f'<div class="dist-num" style="font-size:8px;color:#94a3b8;width:44px;text-align:right;">0 (0%)</div>'
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
                    f'<div class="dist-row" style="margin-bottom:1px;display:flex;align-items:center;gap:6px;">'
                    f'<div class="dist-name" style="font-size:8.5px;width:48px;font-weight:700;">{c_code}</div>'
                    f'<div class="dist-track" style="height:5px;flex:1;border-radius:1px;display:flex;overflow:hidden;background:#212429;">{track_html}</div>'
                    f'<div class="dist-num" style="font-size:8px;width:58px;text-align:right;">{num_html}</div>'
                    f'</div>'
                )

        _render_html(f"""
        <div style="background:#181b1f;border:1px solid #2c3235;border-radius:3px;padding:4px 8px;box-sizing:border-box;height:160px;min-height:160px;display:flex;flex-direction:column;justify-content:space-between;">
          <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:2px;">
            <div style="font-size:9.5px;font-weight:700;color:#10b981;text-transform:uppercase;letter-spacing:0.04em;">
              Component Severity &amp; Fleet SLA ({mat_scope_lbl})
            </div>
            <span style="font-size:8px;color:#94a3b8;font-weight:600;">100% Target</span>
          </div>

          <div class="dist-body" style="padding:1px 0;display:flex;flex-direction:column;gap:1.5px;">
            {''.join(dist_rows)}
          </div>

          <div style="display:grid;grid-template-columns:1fr 1fr;gap:4px;font-size:8.5px;border-top:1px solid #22252b;padding-top:3px;">
            <div style="background:#141619;border:1px solid #22252b;border-radius:2px;padding:2px 5px;display:flex;justify-content:space-between;align-items:center;">
              <span style="color:#10b981;font-weight:700;">PROD Resiliency</span>
              <span style="color:var(--text);font-family:var(--mono);font-weight:700;">100% (0)</span>
            </div>
            <div style="background:#141619;border:1px solid #22252b;border-radius:2px;padding:2px 5px;display:flex;justify-content:space-between;align-items:center;">
              <span style="color:#38bdf8;font-weight:700;">Fleet Scope</span>
              <span style="color:var(--text);font-family:var(--mono);font-weight:700;">{len(df)} Assets</span>
            </div>
            <div style="background:#141619;border:1px solid #22252b;border-radius:2px;padding:2px 5px;display:flex;justify-content:space-between;align-items:center;">
              <span style="color:#ff9830;font-weight:700;">Governance Leads</span>
              <span style="color:var(--text);font-family:var(--mono);font-weight:700;">5 Teams</span>
            </div>
            <div style="background:#141619;border:1px solid #22252b;border-radius:2px;padding:2px 5px;display:flex;justify-content:space-between;align-items:center;">
              <span style="color:#73bf69;font-weight:700;">Batch Console</span>
              <span style="color:var(--text);font-family:var(--mono);font-weight:700;">Ready</span>
            </div>
          </div>
        </div>
        """)

    # --------------------------------------------------------------------------
    # 7. TIER 4: MASTER OPERATIONS WORKSPACE (Full-Width Tabs & 250px Grid)
    # --------------------------------------------------------------------------
    st.markdown('''
    <style>
    .op-table-container {
        background: #181b1f;
        border: 1px solid #2c3235;
        border-radius: 3px;
        box-sizing: border-box;
        overflow-y: auto;
        overflow-x: auto;
        height: 350px;
        max-height: 350px;
        scrollbar-width: thin;
        scrollbar-color: #38bdf8 #181b1f;
    }
    .op-table-container::-webkit-scrollbar {
        width: 7px;
        height: 7px;
        display: block;
    }
    .op-table-container::-webkit-scrollbar-track {
        background: #181b1f;
        border-left: 1px solid #2c3235;
    }
    .op-table-container::-webkit-scrollbar-thumb {
        background: #38bdf8;
        border-radius: 3px;
        border: 1px solid #0284c7;
    }
    div.st-key-op_workspace_subtabs_box div[data-testid="stTabs"] {
        position: relative !important;
    }
    div.st-key-op_workspace_subtabs_box div[data-baseweb="tab-list"] {
        width: fit-content !important;
        max-width: 480px !important;
        border-bottom: 1px solid #2c3235 !important;
        gap: 2px !important;
        height: 36px !important;
    }
    div.st-key-op_workspace_subtabs_box div[data-baseweb="tab-list"] button[role="tab"] {
        padding: 3px 6px !important;
        font-size: 10px !important;
        font-weight: 600 !important;
        white-space: nowrap !important;
    }
    div[class*="st-key-tab_actions_"] {
        position: absolute !important;
        top: 2px !important;
        right: 0 !important;
        left: auto !important;
        width: auto !important;
        max-width: calc(100% - 500px) !important;
        height: 32px !important;
        z-index: 99 !important;
        background: transparent !important;
        border: none !important;
    }
    div[class*="st-key-tab_actions_inv_"] { min-width: 500px !important; }
    div[class*="st-key-tab_actions_insp_"] { min-width: 500px !important; }
    div[class*="st-key-tab_actions_tree_"] { min-width: 400px !important; }
    div[class*="st-key-tab_actions_batch_"] { min-width: 480px !important; }
    div[class*="st-key-tab_actions_rev_"] { min-width: 260px !important; }
    div[class*="st-key-tab_actions_"] div[data-testid="stHorizontalBlock"] {
        align-items: center !important;
        justify-content: flex-end !important;
        gap: 4px !important;
    }
    div[class*="st-key-tab_actions_"] button,
    div[class*="st-key-tab_actions_"] div[data-testid="stPopover"] > button {
        padding: 0 6px !important;
        font-size: 9.5px !important;
        font-weight: 700 !important;
        height: 28px !important;
        min-height: 28px !important;
        white-space: nowrap !important;
        display: inline-flex !important;
        align-items: center !important;
        justify-content: center !important;
        line-height: 1 !important;
        border-radius: 2px !important;
    }
    div[class*="st-key-tab_actions_"] button p {
        font-size: 9.5px !important;
        font-weight: 700 !important;
        white-space: nowrap !important;
        line-height: 1 !important;
        margin: 0 !important;
        padding: 0 !important;
    }
    div[class*="st-key-tab_actions_"] div[data-testid="stPopover"] {
        width: 100% !important;
        display: inline-flex !important;
        align-items: center !important;
    }
    div[class*="st-key-tab_actions_"] div[data-testid="stPopover"] > button > div {
        white-space: nowrap !important;
        overflow: hidden !important;
        text-overflow: ellipsis !important;
        line-height: 1 !important;
    }
    </style>
    ''', unsafe_allow_html=True)

    target_tab = st.session_state.pop("op_target_tab", None)
    valid_subtabs = [
        "📋 Inventory",
        "🔍 Inspector",
        "🌳 Hierarchy",
        "⚡ Batch",
        "↩️ Rollback"
    ]
    # Backward compatibility for tab targeting
    tab_aliases = {
        "📋 Master Asset Inventory": "📋 Inventory",
        "🔍 Entity Detail Inspector": "🔍 Inspector",
        "🌳 Hierarchy Tree Explorer": "🌳 Hierarchy",
        "⚡ Batch Grid Editor": "⚡ Batch",
        "📋 Master Inventory": "📋 Inventory",
        "🔍 Detail Inspector": "🔍 Inspector",
        "🌳 Hierarchy Tree": "🌳 Hierarchy",
        "⚡ Batch Grid": "⚡ Batch",
        "↩️ Rollback Ledger": "↩️ Rollback",
        "batch": "⚡ Batch",
        "insp": "🔍 Inspector",
        "inv": "📋 Inventory",
        "tree": "🌳 Hierarchy",
        "rev": "↩️ Rollback"
    }
    if target_tab in tab_aliases:
        target_tab = tab_aliases[target_tab]
    default_subtab = target_tab if target_tab in valid_subtabs else valid_subtabs[0]

    with st.container(key="op_workspace_subtabs_box"):
        op_tab_inv, op_tab_insp, op_tab_tree, op_tab_batch, op_tab_rev = st.tabs(
            valid_subtabs,
            default=default_subtab,
            key=f"op_subtabs_bar_{reset_idx}"
        )

    with op_tab_inv:
        with st.container(key=f"tab_actions_inv_{reset_idx}"):
            if selected_id is not None and not cur_scope_df.empty:
                rec = df[df["id"] == selected_id].iloc[0]
                meta = ui.BAND_META.get(rec["band"], ui.BAND_META["Healthy"])
                team_meta = ui.TEAM_META.get(rec["team"], ui.TEAM_META["Core"])
                conf = st.session_state.get("confirm_action")
                cur_dt = rec["exp_dt"].date()

                f_col_info, f_col_acts = st.columns([2.3, 1.2], gap="small")
                with f_col_info:
                    st.markdown(f"""
                    <div style="display:flex;align-items:center;gap:4px;background:#141619;border:1px solid #2c3235;border-left:3px solid {meta['color']};padding:2px 5px;border-radius:2px;height:26px;box-sizing:border-box;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">
                      <span style="font-family:var(--mono);font-size:9px;font-weight:800;color:#f8fafc;">🎯 #{rec['id']} {rec['schema_name']}</span>
                      <span class="env-tag" style="font-size:7.5px;padding:0 3px;">{rec['env_label']}</span>
                      <span style="font-size:8px;color:#94a3b8;">{rec['state']} · {rec['team']}</span>
                      <span style="font-size:7.5px;font-weight:700;padding:0 3px;border-radius:2px;background:{meta['tint']};color:{meta['color']};">{meta['symbol']} {rec['band']}</span>
                      <span style="font-size:8px;color:#8fb8f8;font-family:var(--mono);">{ui.fmt_days(rec['days_left'])}</span>
                    </div>
                    """, unsafe_allow_html=True)
                with f_col_acts:
                    if conf and conf.get("id") == rec["id"]:
                        st.markdown(f"<div style='font-size:8.5px;color:var(--warning);font-weight:700;'>⚠️ {conf['new_dt']} (+{conf['days']}d)?</div>", unsafe_allow_html=True)
                        cf_y, cf_n = st.columns(2)
                        with cf_y:
                            if st.button("✓ Yes", key=f"inv_cf_yes_{rec['id']}", type="primary", use_container_width=True):
                                apply_edits([(conf["id"], conf["new_dt"])])
                                del st.session_state["confirm_action"]
                                st.success(f"Updated {conf['schema']} to {conf['new_dt']}")
                                rerun()
                        with cf_n:
                            if st.button("✕ No", key=f"inv_cf_no_{rec['id']}", use_container_width=True):
                                del st.session_state["confirm_action"]
                                rerun()
                    else:
                        act_cols = st.columns([1.0, 1.0, 1.1, 1.1] if rec["edited"] else [1.0, 1.0, 1.1], gap="small")
                        if act_cols[0].button("+90d", key=f"inv_top_p90_{rec['id']}", use_container_width=True, help="Extend expiry by 90 days"):
                            st.session_state["confirm_action"] = {"id": rec["id"], "days": 90, "new_dt": cur_dt + pd.Timedelta(days=90), "schema": rec["schema_name"]}
                            rerun()
                        if act_cols[1].button("+1yr", key=f"inv_top_p365_{rec['id']}", use_container_width=True, help="Extend expiry by 1 year"):
                            st.session_state["confirm_action"] = {"id": rec["id"], "days": 365, "new_dt": cur_dt + pd.Timedelta(days=365), "schema": rec["schema_name"]}
                            rerun()
                        with act_cols[2]:
                            if hasattr(st, "popover"):
                                with st.popover("📅", help="Pick custom expiry date", use_container_width=True):
                                    c_date = st.date_input("New Expiry Date", value=cur_dt, key=f"inv_pop_dt_{rec['id']}")
                                    if st.button("Commit Date", type="primary", key=f"inv_pop_btn_{rec['id']}", use_container_width=True):
                                        apply_edits([(rec["id"], c_date)])
                                        st.success(f"Updated to {c_date}")
                                        rerun()
                            else:
                                with st.expander("📅 Date"):
                                    c_date = st.date_input("New Expiry Date", value=cur_dt, key=f"inv_pop_dt_{rec['id']}")
                                    if st.button("Commit Date", type="primary", key=f"inv_pop_btn_{rec['id']}", use_container_width=True):
                                        apply_edits([(rec["id"], c_date)])
                                        st.success(f"Updated to {c_date}")
                                        rerun()
                        if rec["edited"] and len(act_cols) > 3:
                            with act_cols[3]:
                                if act_cols[3].button("↩ Rev", key=f"inv_top_rev_{rec['id']}", type="secondary", use_container_width=True, help="Revert to workbook source date"):
                                    conn = get_connection(DB_PATH)
                                    try:
                                        revert_component_exp_date(conn, int(rec["id"]))
                                    finally:
                                        conn.close()
                                    bust_cache()
                                    st.success("Reverted to source workbook.")
                                    rerun()
            else:
                st.markdown("<div style='font-size:9.5px;color:#64748b;padding-top:6px;text-align:right;'>Select an entity row to quick-extend</div>", unsafe_allow_html=True)

        # Build Master Table Rows
        inv_table_rows = []
        for r in cur_scope_df.itertuples():
            is_act = (r.id == selected_id)
            row_bg = "background:rgba(56,189,248,0.14);border-left:3px solid #38bdf8;" if is_act else "border-bottom:1px solid #22252b;"
            target_icon = '<span style="color:#38bdf8;font-size:9.5px;margin-right:3px;">🎯</span>' if is_act else ''
            r_meta = ui.BAND_META.get(r.band, ui.BAND_META["Healthy"])
            sla_badge_str = ui.sla_badge(r.env_label)
            days_str = ui.fmt_days(r.days_left)
            badge_html = f'<span style="font-size:8.5px;font-weight:700;padding:1.5px 5px;border-radius:2px;background:{r_meta["tint"]};color:{r_meta["color"]};">{r_meta["symbol"]} {r.band}</span>'
            action_btn_html = f'<a href="?op_act_id={r.id}&op_subtab=insp{auth_suffix}" target="_self" style="text-decoration:none;display:inline-flex;align-items:center;padding:1.5px 6px;border-radius:2px;font-size:9.5px;font-weight:700;background:rgba(56,189,248,0.15);border:1px solid rgba(56,189,248,0.3);color:#38bdf8;">Inspect ↗</a>'

            inv_table_rows.append(f'''
            <tr style="{row_bg}">
                <td style="padding:3px 6px;font-family:var(--mono);"><a href="?op_act_id={r.id}{auth_suffix}" target="_self" style="text-decoration:none;font-weight:700;color:{'#ffffff' if is_act else '#38bdf8'};">{target_icon}#{r.id}</a></td>
                <td style="padding:3px 6px;font-weight:700;color:#9fa7b3;">{r.state}</td>
                <td style="padding:3px 6px;color:#cbd5e1;">{r.team}</td>
                <td style="padding:3px 6px;color:#d8d9da;">{ui.COMPONENT_CODE.get(r.component, r.component)}</td>
                <td style="padding:3px 6px;font-family:var(--mono);font-weight:600;color:#f8fafc;">{r.schema_name}</td>
                <td style="padding:3px 6px;"><span class="env-tag">{r.env_label}</span></td>
                <td style="padding:3px 6px;">{sla_badge_str}</td>
                <td style="padding:3px 6px;font-family:var(--mono);">{r.exp_date}</td>
                <td style="padding:3px 6px;font-family:var(--mono);color:{r_meta['color']};font-weight:700;">{days_str}</td>
                <td style="padding:3px 6px;">{badge_html}</td>
                <td style="padding:3px 6px;text-align:right;">{action_btn_html}</td>
            </tr>
            ''')

        _render_html(f'''
        <div class="op-table-container">
            <table style="width:100%;min-width:920px;border-collapse:collapse;font-size:10px;color:#d8d9da;">
                <thead style="position:sticky;top:0;background:#141619;border-bottom:1px solid #2c3235;z-index:2;">
                    <tr style="color:#6e7681;text-transform:uppercase;font-size:9px;font-weight:600;letter-spacing:0.03em;">
                        <th style="padding:4px 6px;text-align:left;">ID</th>
                        <th style="padding:4px 6px;text-align:left;">State</th>
                        <th style="padding:4px 6px;text-align:left;">Team</th>
                        <th style="padding:4px 6px;text-align:left;">Component</th>
                        <th style="padding:4px 6px;text-align:left;">Schema / Asset</th>
                        <th style="padding:4px 6px;text-align:left;">Env</th>
                        <th style="padding:4px 6px;text-align:left;">SLA</th>
                        <th style="padding:4px 6px;text-align:left;">Current Expiry</th>
                        <th style="padding:4px 6px;text-align:left;">Time Left</th>
                        <th style="padding:4px 6px;text-align:left;">Status</th>
                        <th style="padding:4px 6px;text-align:right;">Action</th>
                    </tr>
                </thead>
                <tbody>
                    {''.join(inv_table_rows)}
                </tbody>
            </table>
        </div>
        ''')

    with op_tab_insp:
        with st.container(key=f"tab_actions_insp_{reset_idx}"):
            if selected_id is not None and not cur_scope_df.empty:
                rec = df[df["id"] == selected_id].iloc[0]
                meta = ui.BAND_META.get(rec["band"], ui.BAND_META["Healthy"])
                team_meta = ui.TEAM_META.get(rec["team"], ui.TEAM_META["Core"])
                cp_icon = ui.COMPONENT_ICONS.get(rec["component"], "📦")
                conf = st.session_state.get("confirm_action")
                cur_dt = rec["exp_dt"].date()

                head_c1, head_c2 = st.columns([2.3, 1.2], gap="small")
                with head_c1:
                    st.markdown(f"""
                    <div style="display:flex;align-items:center;gap:4px;background:#141619;border:1px solid #2c3235;border-left:3px solid {meta['color']};padding:2px 5px;border-radius:2px;height:26px;box-sizing:border-box;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">
                      <span style="font-family:var(--mono);font-size:9px;font-weight:800;color:#f8fafc;">ENTITY #{rec['id']} {rec['schema_name']}</span>
                      <span class="env-tag" style="font-size:7.5px;padding:0 3px;">{rec['env_label']}</span>
                      <span style="font-size:8px;color:#94a3b8;">{rec['state']} · {rec['team']}</span>
                      <span style="font-size:7.5px;font-weight:700;padding:0 3px;border-radius:2px;background:{meta['tint']};color:{meta['color']};">{meta['symbol']} {rec['band']}</span>
                    </div>
                    """, unsafe_allow_html=True)

                with head_c2:
                    if conf and conf.get("id") == rec["id"]:
                        st.markdown(f"<div style='font-size:8.5px;color:var(--warning);font-weight:700;'>⚠️ {conf['new_dt']} (+{conf['days']}d)?</div>", unsafe_allow_html=True)
                        cf_y, cf_n = st.columns(2)
                        with cf_y:
                            if st.button("✓ Yes", key=f"insp_cf_yes_{rec['id']}", type="primary", use_container_width=True):
                                apply_edits([(conf["id"], conf["new_dt"])])
                                del st.session_state["confirm_action"]
                                st.success(f"Updated {conf['schema']} to {conf['new_dt']}")
                                rerun()
                        with cf_n:
                            if st.button("✕ No", key=f"insp_cf_no_{rec['id']}", use_container_width=True):
                                del st.session_state["confirm_action"]
                                rerun()
                    else:
                        act_cols = st.columns([1.0, 1.0, 1.1, 1.1] if rec["edited"] else [1.0, 1.0, 1.1], gap="small")
                        if act_cols[0].button("+90d", key=f"insp_top_p90_{rec['id']}", use_container_width=True, help="Extend expiry by 90 days"):
                            st.session_state["confirm_action"] = {"id": rec["id"], "days": 90, "new_dt": cur_dt + pd.Timedelta(days=90), "schema": rec["schema_name"]}
                            rerun()
                        if act_cols[1].button("+1yr", key=f"insp_top_p365_{rec['id']}", use_container_width=True, help="Extend expiry by 1 year"):
                            st.session_state["confirm_action"] = {"id": rec["id"], "days": 365, "new_dt": cur_dt + pd.Timedelta(days=365), "schema": rec["schema_name"]}
                            rerun()
                        with act_cols[2]:
                            if hasattr(st, "popover"):
                                with st.popover("📅", help="Pick custom expiry date", use_container_width=True):
                                    c_date = st.date_input("New Expiry Date", value=cur_dt, key=f"insp_pop_dt_{rec['id']}")
                                    if st.button("Commit Date", type="primary", key=f"insp_pop_btn_{rec['id']}", use_container_width=True):
                                        apply_edits([(rec["id"], c_date)])
                                        st.success(f"Updated to {c_date}")
                                        rerun()
                            else:
                                with st.expander("📅 Date"):
                                    c_date = st.date_input("New Expiry Date", value=cur_dt, key=f"insp_pop_dt_{rec['id']}")
                                    if st.button("Commit Date", type="primary", key=f"insp_pop_btn_{rec['id']}", use_container_width=True):
                                        apply_edits([(rec["id"], c_date)])
                                        st.success(f"Updated to {c_date}")
                                        rerun()
                        if rec["edited"] and len(act_cols) > 3:
                            with act_cols[3]:
                                if act_cols[3].button("↩ Rev", key=f"insp_top_rev_{rec['id']}", type="secondary", use_container_width=True, help="Revert to workbook source date"):
                                    conn = get_connection(DB_PATH)
                                    try:
                                        revert_component_exp_date(conn, int(rec["id"]))
                                    finally:
                                        conn.close()
                                    bust_cache()
                                    st.success("Reverted to source workbook.")
                                    rerun()
            else:
                st.markdown("<div style='font-size:9.5px;color:#64748b;padding-top:6px;text-align:right;'>Select an entity row to inspect</div>", unsafe_allow_html=True)

        if selected_id is None or cur_scope_df.empty:
            st.markdown(ui.empty("Select a record", "Choose an item from the master inventory table to inspect."), unsafe_allow_html=True)
        else:
            rec = df[df["id"] == selected_id].iloc[0]
            meta = ui.BAND_META.get(rec["band"], ui.BAND_META["Healthy"])
            team_meta = ui.TEAM_META.get(rec["team"], ui.TEAM_META["Core"])
            cur_dt = rec["exp_dt"].date()

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

    with op_tab_tree:
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

        with st.container(key=f"tab_actions_tree_{reset_idx}"):
            n_sel = len(selected_entity_ids)
            if n_sel > 0:
                tc1, tc2, tc3, tc4 = st.columns([1.1, 1.2, 1.3, 1.3], gap="small")
                with tc1:
                    if st.button("⊞ Expand", key="tree_exp_all", use_container_width=True, help="Expand all tree nodes"):
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
                    if st.button("⊟ Collapse", key="tree_col_all", use_container_width=True, help="Collapse all tree nodes"):
                        tree_open.clear()
                        rerun()
                with tc3:
                    if st.button(f"☐ Clear ({n_sel})", key="tree_clear_sel_btn", use_container_width=True, help="Deselect all items"):
                        selected_entity_ids.clear()
                        rerun()
                with tc4:
                    if st.button(f"⚡ Batch ({n_sel}) ›", key="tree_send_to_batch", type="primary", use_container_width=True, help="Send selected items to Batch Grid Editor"):
                        st.session_state["op_target_tab"] = "⚡ Batch Grid"
                        rerun()
            else:
                tc1, tc2, tc3, tc_stat = st.columns([1.1, 1.2, 1.3, 0.8], gap="small")
                with tc1:
                    if st.button("⊞ Expand", key="tree_exp_all", use_container_width=True, help="Expand all tree nodes"):
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
                    if st.button("⊟ Collapse", key="tree_col_all", use_container_width=True, help="Collapse all tree nodes"):
                        tree_open.clear()
                        rerun()
                with tc3:
                    if st.button("☑ Select All", key="tree_select_all_btn", use_container_width=True, help="Select all items in view"):
                        all_f_ids = set(filtered["id"].tolist())
                        selected_entity_ids.update(all_f_ids)
                        rerun()
                with tc_stat:
                    st.markdown('<div style="height:28px;display:flex;align-items:center;justify-content:center;font-size:9.5px;color:#64748b;background:#141619;border:1px solid #22252b;border-radius:2px;font-family:var(--mono);">0 sel</div>', unsafe_allow_html=True)

        with st.container(height=350, border=True, key="op_tree_box"):
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
                        rerun()
                with s_c1:
                    is_st_foc = (cell_filter == (st_val, None)) or (state_filter == st_val and cell_filter is None and comp_filter == "All Components")
                    st_badge_txt = f"{st_exp_n} Expired" if st_exp_n else st_worst
                    btn_lbl = f"📍 State {st_val} ({len(st_sub)} items) · {st_meta['symbol']} {st_badge_txt}"
                    if st.button(btn_lbl, key=f"foc_st_tree_{st_val}", use_container_width=True, type="primary" if is_st_foc else "secondary", help=f"Focus entire workspace on State {st_val}"):
                        if is_st_foc:
                            st.session_state["op_cell_filter"] = None
                        else:
                            st.session_state["op_cell_filter"] = (st_val, None)
                            tree_open.clear()
                            tree_open.add(st_val)
                        rerun()

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
                        tm_sym, tm_uncheck, tm_type = tri_state_info(tm_child_ids, selected_entity_ids)

                        t_c0, t_c1, t_c2 = st.columns([0.35, 4.15, 0.5])
                        with t_c0:
                            if st.button(tm_sym, key=f"sel_tm_{st_val}_{tm_val}", type=tm_type, use_container_width=True):
                                if tm_uncheck:
                                    selected_entity_ids.difference_update(tm_child_ids)
                                else:
                                    selected_entity_ids.update(tm_child_ids)
                                rerun()
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
                                cp_sym, cp_uncheck, cp_type = tri_state_info(cp_child_ids, selected_entity_ids)

                                cp_c0, cp_c1, cp_c2 = st.columns([0.35, 4.15, 0.5])
                                chip_head = f"{cp_icon} {cp_code}"
                                with cp_c0:
                                    if st.button(cp_sym, key=f"sel_cp_{st_val}_{tm_val}_{cp_code}", type=cp_type, use_container_width=True):
                                        if cp_uncheck:
                                             selected_entity_ids.difference_update(cp_child_ids)
                                        else:
                                             selected_entity_ids.update(cp_child_ids)
                                        rerun()
                                np_act = (cell_filter == (st_val, cp_val))
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
                                        rerun()

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
                                                rerun()
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
                                                rerun()

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
                                                        f"<div style='font-size:9.5px;font-weight:600;color:{r_c};white-space:nowrap;font-variant-numeric:tabular-nums;'>{r_s} {ui.fmt_days(r.days_left)}</div>"
                                                        f"{_sbar}"
                                                        f"</div>",
                                                        unsafe_allow_html=True
                                                    )

    with op_tab_batch:
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

        # ── Unified same-line toolbar: selection status, pagination, and 1-click bulk date actions ──
        with st.container(key=f"tab_actions_batch_{reset_idx}"):
            bg_c1, bg_c2, bg_c3, bg_c4, bg_c5 = st.columns([1.3, 0.6, 1.0, 1.0, 0.8], gap="small")
            with bg_c1:
                if selected_entity_ids:
                    st.markdown(f"<div style='font-size:9.5px;color:#38bdf8;font-weight:700;padding-top:4px;white-space:nowrap;'>⚡ {len(selected_entity_ids)} sel · P{b_page + 1}/{b_pages}</div>", unsafe_allow_html=True)
                else:
                    st.markdown(f"<div style='font-size:9.5px;color:#cbd5e1;font-weight:600;padding-top:4px;white-space:nowrap;'>Scope: {total_batch_n} · P{b_page + 1}/{b_pages}</div>", unsafe_allow_html=True)
            with bg_c2:
                if b_pages > 1:
                    p_c1, p_c2 = st.columns(2)
                    if p_c1.button("‹", key="op_batch_p_prev", disabled=(b_page == 0), use_container_width=True):
                        st.session_state["op_batch_page_no"] = b_page - 1
                        rerun()
                    if p_c2.button("›", key="op_batch_p_next", disabled=(b_page >= b_pages - 1), use_container_width=True):
                        st.session_state["op_batch_page_no"] = b_page + 1
                        rerun()
                elif not is_all_filtered_selected and len(all_filtered_ids) > 0:
                    if st.button("All", key="op_batch_sel_all_flt", type="primary", use_container_width=True, help=f"Select all {len(filtered)} items in current filter"):
                        selected_entity_ids.update(all_filtered_ids)
                        rerun()
            with bg_c3:
                if st.button("+90d All", key="op_batch_bulk_90d", use_container_width=True, help="Extend every item on this page by 90 days"):
                    changes = [(int(row.id), (row.exp_dt + pd.Timedelta(days=90)).date()) for row in page_slice.itertuples()]
                    apply_edits(changes)
                    st.success(f"+90d applied to {len(changes)} items.")
                    rerun()
            with bg_c4:
                if st.button("+1yr All", key="op_batch_bulk_1yr", use_container_width=True, help="Extend every item on this page by 1 year"):
                    changes = [(int(row.id), (row.exp_dt + pd.Timedelta(days=365)).date()) for row in page_slice.itertuples()]
                    apply_edits(changes)
                    st.success(f"+1yr applied to {len(changes)} items.")
                    rerun()
            with bg_c5:
                if hasattr(st, "popover"):
                    with st.popover("📅 Set", help="Set common expiry date for all items on page", use_container_width=True):
                        c_common_dt = st.date_input("Set all to date", key="op_batch_common_dt_pick")
                        if st.button("Apply", type="primary", key="op_batch_common_dt_apply", use_container_width=True):
                            changes = [(int(row.id), c_common_dt) for row in page_slice.itertuples()]
                            apply_edits(changes)
                            st.success(f"Set {len(changes)} items to {c_common_dt}.")
                            rerun()

        if hasattr(st, "data_editor") and hasattr(st, "column_config") and not page_slice.empty:
            b_view = page_slice[["schema_name", "env_label", "exp_dt", "band", "days_left"]].copy()
            b_view["exp_dt"] = b_view["exp_dt"].dt.date
            b_view["days_left"] = b_view["days_left"].apply(ui.fmt_days)
            b_view["band"] = b_view["band"].apply(ui.health_text)

            b_edited = st.data_editor(
                b_view, key=f"op_batch_editor_p{b_page}", hide_index=True, use_container_width=True,
                num_rows="fixed", height=min(320, 36 + len(page_slice) * 35),
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
                apply_edits(b_changes)
                st.success(f"Saved {len(b_changes)} batch updates!")
                rerun()
            n_bchg = len(b_changes)
            b_note_col.markdown(f"<div style='font-size:10.5px;color:#94a3b8;padding-top:4px;'><b>{n_bchg}</b> unsaved {'change' if n_bchg == 1 else 'changes'} on current page</div>", unsafe_allow_html=True)
        elif page_slice.empty:
            st.markdown("<div style='font-size:11px;color:#94a3b8;padding:12px 0;'>No entities selected. Select items from the tree or filters.</div>", unsafe_allow_html=True)

    with op_tab_rev:
        active_edits = df[df["edited"]].copy()
        with st.container(key=f"tab_actions_rev_{reset_idx}"):
            if active_edits.empty:
                st.markdown("<div style='font-size:9.5px;color:#10b981;font-weight:700;padding-top:6px;text-align:right;'>✓ Fleet 100% In Sync with Workbooks</div>", unsafe_allow_html=True)
            else:
                n_ovr = len(active_edits)
                rev_hdr_c1, rev_hdr_c2 = st.columns([2.5, 1.5], gap="small")
                with rev_hdr_c1:
                    st.markdown(f"<div style='font-size:9.5px;color:#cbd5e1;padding-top:5px;'><b>{n_ovr}</b> local {'override' if n_ovr == 1 else 'overrides'}</div>", unsafe_allow_html=True)
                with rev_hdr_c2:
                    if st.button(f"↩ Revert All ({n_ovr})", key="op_rev_all_btn", type="primary", use_container_width=True):
                        conn = get_connection(DB_PATH)
                        try:
                            for er in active_edits.itertuples():
                                revert_component_exp_date(conn, int(er.id))
                        finally:
                            conn.close()
                        bust_cache()
                        st.success(f"Reverted all {n_ovr} overrides to source workbook dates.")
                        rerun()

        if active_edits.empty:
            st.markdown("""
            <div class="card" style="font-size:11px;color:#94a3b8;padding:12px 14px;margin-top:8px;">
              <div style="font-weight:700;color:#10b981;margin-bottom:4px;font-size:12px;">✓ Fleet 100% In Sync with Workbooks</div>
              All 500 records match source Excel files. Local overrides made in the inventory, inspector, or batch editor appear here for 1-click rollback.
            </div>
            """, unsafe_allow_html=True)
        else:
            with st.container(height=320, border=True):
                for er in active_edits.itertuples():
                    src_dt = str(er.source_exp_date) if hasattr(er, "source_exp_date") else "—"
                    cur_dt = str(er.exp_date)
                    delta_days = er.days_left if hasattr(er, "days_left") else 0
                    arrow_color = "#10b981" if delta_days > 0 else "#f2495c"
                    ec1, ec2 = st.columns([3, 1])
                    ec1.markdown(
                        f"<div style='font-size:10.5px;padding:2px 0;'>"
                        f"<b>{er.schema_name}</b> <span style='color:#64748b;font-size:9.5px;'>({er.state} · {er.team})</span><br>"
                        f"<span style='color:#64748b;font-size:9px;'>Source: <code style='color:#94a3b8;'>{src_dt}</code>"
                        f" <span style='color:{arrow_color};font-weight:700;'>➔</span>"
                        f" Override: <code style='color:#38bdf8;font-weight:600;'>{cur_dt}</code></span>"
                        f"</div>",
                        unsafe_allow_html=True
                    )
                    if ec2.button("↩ Revert", key=f"op_rev_ledger_{er.id}", use_container_width=True):
                        conn = get_connection(DB_PATH)
                        revert_component_exp_date(conn, int(er.id))
                        conn.close()
                        bust_cache()
                        st.success(f"Reverted {er.schema_name}")
                        rerun()


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
        "status": "10 Expired",
        "symbol": "●",
        "status_color": "#f2495c",
        "status_bg": "rgba(242,73,92,0.18)"
    },
    "Letters": {
        "team": "Letters",
        "lead": "Correspondence Lead",
        "channel": "letters-ops@example.com",
        "cadence": "Monthly (1st Sun)",
        "assets": 96,
        "status": "5 Critical (15d)",
        "symbol": "▲",
        "status_color": "#ff9830",
        "status_bg": "rgba(255,152,48,0.18)"
    },
    "Cognos": {
        "team": "Cognos",
        "lead": "BI & Analytics Lead",
        "channel": "cognos-dba@example.com",
        "cadence": "3× Weekly",
        "assets": 96,
        "status": "5 Critical (15d)",
        "symbol": "▲",
        "status_color": "#ff9830",
        "status_bg": "rgba(255,152,48,0.18)"
    },
    "Informatica": {
        "team": "Informatica",
        "lead": "ETL Operations Lead",
        "channel": "infa-etl@example.com",
        "cadence": "Weekly (Sun)",
        "assets": 96,
        "status": "100% Healthy",
        "symbol": "✓",
        "status_color": "#73bf69",
        "status_bg": "rgba(115,191,105,0.16)"
    },
    "App Server": {
        "team": "App Server",
        "lead": "JVM Containers Lead",
        "channel": "appserver-admin@example.com",
        "cadence": "Weekly (Sun)",
        "assets": 96,
        "status": "100% Healthy",
        "symbol": "✓",
        "status_color": "#73bf69",
        "status_bg": "rgba(115,191,105,0.16)"
    }
}


def render_governance_center(records_df: pd.DataFrame | None = None) -> None:
    st.markdown('''
    <style>
    .gov-table-container {
        background: #181b1f;
        border: 1px solid #2c3235;
        border-radius: 3px;
        box-sizing: border-box;
        overflow-y: auto;
        overflow-x: auto;
        height: 432px;
        max-height: 432px;
        scrollbar-width: thin;
        scrollbar-color: #38bdf8 #181b1f;
    }
    .gov-table-container::-webkit-scrollbar {
        width: 7px;
        height: 7px;
        display: block;
    }
    .gov-table-container::-webkit-scrollbar-track {
        background: #181b1f;
        border-left: 1px solid #2c3235;
    }
    .gov-table-container::-webkit-scrollbar-thumb {
        background: #38bdf8;
        border-radius: 3px;
        border: 1px solid #0284c7;
    }
    div.st-key-gov_workspace_subtabs_box div[data-testid="stTabs"] {
        position: relative !important;
    }
    div.st-key-gov_workspace_subtabs_box div[data-baseweb="tab-list"] {
        width: fit-content !important;
        max-width: 540px !important;
        border-bottom: 1px solid #2c3235 !important;
        gap: 2px !important;
        height: 36px !important;
    }
    div.st-key-gov_workspace_subtabs_box div[data-baseweb="tab-list"] button[role="tab"] {
        padding: 3px 6px !important;
        font-size: 10px !important;
        font-weight: 600 !important;
        white-space: nowrap !important;
    }
    div[class*="st-key-gov_tab_actions_"] {
        position: absolute !important;
        top: 2px !important;
        right: 0 !important;
        left: auto !important;
        width: auto !important;
        max-width: calc(100% - 550px) !important;
        height: 32px !important;
        z-index: 99 !important;
        background: transparent !important;
        border: none !important;
    }
    div[class*="st-key-gov_tab_actions_risk_"] { min-width: 460px !important; }
    div[class*="st-key-gov_tab_actions_disp_"] { min-width: 320px !important; }
    div[class*="st-key-gov_tab_actions_cad_"] { min-width: 360px !important; }
    div[class*="st-key-gov_tab_actions_cut_"] { min-width: 420px !important; }
    div[class*="st-key-gov_tab_actions_aud_"] { min-width: 320px !important; }
    div[class*="st-key-gov_tab_actions_"] div[data-testid="stHorizontalBlock"] {
        align-items: center !important;
        justify-content: flex-end !important;
        gap: 4px !important;
    }
    div[class*="st-key-gov_tab_actions_"] button,
    div[class*="st-key-gov_tab_actions_"] div[data-testid="stPopover"] > button {
        padding: 0 6px !important;
        font-size: 9.5px !important;
        font-weight: 700 !important;
        height: 28px !important;
        min-height: 28px !important;
        white-space: nowrap !important;
        display: inline-flex !important;
        align-items: center !important;
        justify-content: center !important;
        line-height: 1 !important;
        border-radius: 2px !important;
    }
    div[class*="st-key-gov_tab_actions_"] button p {
        font-size: 9.5px !important;
        font-weight: 700 !important;
        white-space: nowrap !important;
        line-height: 1 !important;
        margin: 0 !important;
        padding: 0 !important;
    }
    div[class*="st-key-gov_tab_actions_"] div[data-testid="stPopover"] {
        width: 100% !important;
        display: inline-flex !important;
        align-items: center !important;
    }
    div[class*="st-key-gov_tab_actions_"] div[data-testid="stPopover"] > button > div {
        white-space: nowrap !important;
        overflow: hidden !important;
        text-overflow: ellipsis !important;
        line-height: 1 !important;
    }
    </style>
    ''', unsafe_allow_html=True)

    conn = get_connection(DB_PATH)
    tables = ["component_records", "expiry_records", "maintenance_schedules", "owners", "reminder_log"]
    stats = {}
    for t in tables:
        try:
            stats[t] = conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
        except Exception:
            stats[t] = 0
    conn.close()

    reset_idx = st.session_state.setdefault("gov_reset_idx", 0)

    # Reactive URL Query Parameter Handling
    qp_st = st.query_params.get("gov_st")
    if qp_st and qp_st in STATES:
        st.session_state[f"gov_state_{reset_idx}"] = qp_st
        del st.query_params["gov_st"]

    qp_tm = st.query_params.get("gov_tm")
    if qp_tm:
        if qp_tm in ui.TEAMS:
            st.session_state[f"gov_team_{reset_idx}"] = qp_tm
        elif qp_tm in ["All Teams", "all", "clear"]:
            st.session_state[f"gov_team_{reset_idx}"] = "All Teams"
        del st.query_params["gov_tm"]

    qp_subtab = st.query_params.get("gov_subtab")
    if qp_subtab:
        if qp_subtab in ["risk", "queue", "master", "detail"]:
            st.session_state["gov_target_tab"] = "⚡ Risk Master-Detail"
        elif qp_subtab in ["disp", "mail", "dispatch"]:
            st.session_state["gov_target_tab"] = "📧 Alert Dispatch"
        elif qp_subtab in ["cad", "cadence"]:
            st.session_state["gov_target_tab"] = "🛠️ Cadence Console"
        elif qp_subtab in ["cut", "cutoff", "gates"]:
            st.session_state["gov_target_tab"] = "🚀 Cutoff Engine"
        elif qp_subtab in ["aud", "audit"]:
            st.session_state["gov_target_tab"] = "📋 Audit Ledger"
        del st.query_params["gov_subtab"]

    active_user = st.session_state.get("active_user", "admin")
    auth_suffix = f"&_auth_user={active_user}&tab=3"

    # --------------------------------------------------------------------------
    # 1. UNIVERSAL 1-LINE COMMAND BAR (Brand & Scope | Slicers | Telemetry & Actions)
    # --------------------------------------------------------------------------
    gov_st_key = f"gov_state_{reset_idx}"
    gov_tm_key = f"gov_team_{reset_idx}"
    gov_risk_key = f"gov_risk_{reset_idx}"
    gov_srch_key = f"gov_srch_{reset_idx}"

    active_scope = st.session_state.get("_override_canvas_state")
    if active_scope and active_scope in STATES and gov_st_key not in st.session_state:
        st.session_state[gov_st_key] = active_scope

    c_brand, c_srch, c_st, c_tm, c_risk, c_telem, c_csv, c_reset = st.columns(
        [1.9, 1.2, 0.85, 0.85, 0.95, 1.7, 0.55, 0.35],
        gap="small"
    )

    with c_srch:
        q = st.text_input("Filter", key=gov_srch_key, placeholder="🔍 Search...", label_visibility="collapsed")
    with c_st:
        state_opts = ["All States"] + STATES
        state_filter = st.selectbox("State", state_opts, key=gov_st_key, label_visibility="collapsed")
    with c_tm:
        team_filter = st.selectbox("Team", ["All Teams"] + ui.TEAMS, key=gov_tm_key, label_visibility="collapsed")
    with c_risk:
        risk_filter = st.selectbox("Risk", ["All Risks", "Expired", "Critical (≤15d)", "Warning (≤30d)", "Healthy"], key=gov_risk_key, label_visibility="collapsed")

    base_df = records if records_df is None else records_df
    scoped_records = base_df.copy()
    if q:
        scoped_records = search(scoped_records, q)
    if state_filter != "All States":
        scoped_records = scoped_records[scoped_records["state"] == state_filter]
    if team_filter != "All Teams":
        scoped_records = scoped_records[scoped_records["team"] == team_filter]
    if risk_filter == "Expired":
        scoped_records = scoped_records[scoped_records["band"] == "Expired"]
    elif risk_filter == "Critical (≤15d)":
        scoped_records = scoped_records[scoped_records["band"] == "Critical"]
    elif risk_filter == "Warning (≤30d)":
        scoped_records = scoped_records[scoped_records["band"] == "Warning"]
    elif risk_filter == "Healthy":
        scoped_records = scoped_records[scoped_records["band"] == "Healthy"]

    scoped_records = scoped_records.sort_values("days_left")

    urgent_records = scoped_records[scoped_records["band"].isin(["Expired", "Critical", "Warning"])].copy()
    urgent_records.sort_values(by="days_left", ascending=True, inplace=True)

    n_expired_fleet = int((scoped_records["band"] == "Expired").sum())
    n_critical_fleet = int((scoped_records["band"] == "Critical").sum())
    n_warning_fleet = int((scoped_records["band"] == "Warning").sum())
    n_total_risk_fleet = n_expired_fleet + n_critical_fleet + n_warning_fleet
    n_healthy_fleet = int((scoped_records["band"] == "Healthy").sum())
    total_len = len(scoped_records) if len(scoped_records) > 0 else 1
    pct_healthy = (n_healthy_fleet / total_len) * 100.0
    pct_risk = (n_total_risk_fleet / total_len) * 100.0

    _utc_now = datetime.now(timezone.utc).strftime("%H:%M UTC")

    with c_brand:
        st.markdown(f"""
        <div style="display:flex;align-items:center;gap:6px;height:30px;padding-top:2px;" title="Governance & Alerts Center · Automated Policy Enforcement & Multi-Team Alerts">
            <div style="width:3px;height:18px;background:#8b5cf6;border-radius:1px;flex:none;"></div>
            <span style="font-size:11px;font-weight:800;letter-spacing:0.04em;color:#f8fafc;white-space:nowrap;">GOVERNANCE &amp; ALERTS</span>
            <span style="font-size:7.5px;font-weight:800;background:rgba(139,92,246,0.18);color:#a78bfa;border:1px solid rgba(139,92,246,0.35);padding:1px 5px;border-radius:2px;white-space:nowrap;">POLICY</span>
            <span style="font-size:9px;color:#94a3b8;font-family:var(--mono);white-space:nowrap;">({len(scoped_records)}/{len(base_df)})</span>
        </div>
        """, unsafe_allow_html=True)

    with c_telem:
        st.markdown(f"""
        <div style="display:flex;align-items:center;justify-content:flex-end;gap:5px;height:30px;line-height:1;box-sizing:border-box;">
            <span style="font-size:8px;font-weight:700;background:rgba(115,191,105,0.16);color:#73bf69;border:1px solid rgba(115,191,105,0.3);padding:1.5px 5px;border-radius:2px;white-space:nowrap;">🛡️ PROD 100%</span>
            <span style="font-size:8px;font-weight:700;background:rgba(242,73,92,0.18);color:#f2495c;border:1px solid rgba(242,73,92,0.4);padding:1.5px 5px;border-radius:2px;white-space:nowrap;">⚠️ {n_total_risk_fleet} Debt</span>
            <span style="font-size:8px;color:#64748b;font-family:var(--mono);white-space:nowrap;">{_utc_now}</span>
        </div>
        """, unsafe_allow_html=True)

    with c_csv:
        st.markdown(
            ui.csv_download_button(
                df=scoped_records,
                filename=f"governance_audit_{date.today().isoformat()}.csv",
                label="📥 CSV",
                key=f"gov_export_csv_{reset_idx}",
            ),
            unsafe_allow_html=True
        )

    with c_reset:
        if st.button("↺", key=f"gov_reset_btn_{reset_idx}", help="Reset all filters and scope", use_container_width=True):
            st.session_state["gov_reset_idx"] = reset_idx + 1
            st.session_state.pop("gov_target_tab", None)
            rerun()

    # --------------------------------------------------------------------------
    # 2. ACTION DIRECTIVE TICKER (Compact 24px Bar)
    # --------------------------------------------------------------------------
    st.markdown(f"""
    <div style="display:flex;align-items:center;justify-content:space-between;background:linear-gradient(90deg, rgba(242,73,92,0.12) 0%, rgba(24,27,31,0.95) 100%);border:1px solid #2c3235;border-left:3px solid #f2495c;border-radius:2px;padding:2px 8px;margin-bottom:6px;height:24px;box-sizing:border-box;">
      <div style="display:flex;align-items:center;gap:6px;min-width:0;overflow:hidden;">
        <span style="width:6px;height:6px;border-radius:50%;background:#f2495c;box-shadow:0 0 5px #f2495c;flex:none;"></span>
        <span style="font-size:9.5px;font-weight:800;color:#f8fafc;letter-spacing:0.03em;white-space:nowrap;">ACTION DIRECTIVE:</span>
        <span style="color:#f2495c;background:rgba(242,73,92,0.18);font-size:8px;font-weight:700;border-radius:2px;padding:0 4px;flex:none;">POLICY ESCALATION</span>
        <span style="font-size:9px;color:#cbd5e1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">
          <b>Core (ND)</b> overdue &middot; <b>Letters (AK)</b> &amp; <b>Cognos (NH)</b> &le;15d renewals pending
        </span>
      </div>
      <div style="display:flex;align-items:center;gap:10px;flex:none;">
        <span style="font-size:8.5px;color:#10b981;font-weight:700;">{pct_healthy:.1f}% OK</span>
        <span style="font-size:8.5px;color:#f2495c;font-weight:700;">{pct_risk:.1f}% Risk ({n_total_risk_fleet} Items)</span>
      </div>
    </div>
    """, unsafe_allow_html=True)

    # --------------------------------------------------------------------------
    # 3. COMPACT METRIC RIBBON (4 KPI Cards - Zero Secondary Buttons)
    # --------------------------------------------------------------------------
    kpi_c1, kpi_c2, kpi_c3, kpi_c4 = st.columns(4, gap="small")

    with kpi_c1:
        st.markdown(ui.grafana_stat_card(
            label="Governance Debt Assets",
            value=n_total_risk_fleet,
            color="#f2495c" if n_total_risk_fleet else "#10b981",
            subtext=f"{n_expired_fleet} Exp · {n_critical_fleet} Crit · {n_warning_fleet} Warn",
            badge="FIRING" if n_total_risk_fleet else "COMPLIANT",
            sparkline_vals=[28, 26, 25, 25],
            state="firing" if n_total_risk_fleet else "ok",
        ), unsafe_allow_html=True)

    with kpi_c2:
        st.markdown(ui.grafana_stat_card(
            label="Teams Impacted",
            value="3 / 5",
            color="#ff9830",
            subtext="Core, Letters, Cognos attention",
            badge="ATTENTION",
            sparkline_vals=[4, 3, 3, 3],
            state="pending",
        ), unsafe_allow_html=True)

    with kpi_c3:
        maint_cnt = stats.get('maintenance_schedules', 0)
        st.markdown(ui.grafana_stat_card(
            label="Maintenance Windows",
            value=maint_cnt,
            color="#5794f2",
            subtext="100% Synced across 4 cadences",
            badge="SCHEDULE",
            sparkline_vals=[120, 122, 123, 123],
            state="ok",
        ), unsafe_allow_html=True)

    with kpi_c4:
        smtp_live = bool(os.environ.get("SMTP_HOST"))
        audit_cnt = stats.get('reminder_log', 0)
        st.markdown(ui.grafana_stat_card(
            label="Alert Dispatch Audit",
            value=f"{audit_cnt} Logged",
            color="#73bf69" if smtp_live else "#5794f2",
            subtext="Live SMTP Configured" if smtp_live else "Daily dry-run audit @ 08:00 UTC",
            badge="LIVE SMTP" if smtp_live else "SIMULATED",
            sparkline_vals=[1, 2, 2, 2],
            state="ok",
        ), unsafe_allow_html=True)

    st.markdown("<div style='margin-top:2px;'></div>", unsafe_allow_html=True)

    # --------------------------------------------------------------------------
    # 4. MASTER ACTION & DISPATCH WORKSPACE (Full-Width, 432px Viewport Locked)
    # --------------------------------------------------------------------------
    valid_gov_subtabs = [
        "⚡ Risk Master-Detail",
        "📧 Alert Dispatch",
        "🛠️ Cadence Console",
        "🚀 Cutoff Engine",
        "📋 Audit Ledger"
    ]
    gov_tab_aliases = {
        "⚡ Risk Master-Detail": "⚡ Risk Master-Detail",
        "⚡ Risk Queue": "⚡ Risk Master-Detail",
        "risk": "⚡ Risk Master-Detail",
        "queue": "⚡ Risk Master-Detail",
        "master": "⚡ Risk Master-Detail",
        "detail": "⚡ Risk Master-Detail",
        "📧 Alert Dispatch": "📧 Alert Dispatch",
        "disp": "📧 Alert Dispatch",
        "dispatch": "📧 Alert Dispatch",
        "🛠️ Cadence Console": "🛠️ Cadence Console",
        "cad": "🛠️ Cadence Console",
        "cadence": "🛠️ Cadence Console",
        "🚀 Cutoff Engine": "🚀 Cutoff Engine",
        "cut": "🚀 Cutoff Engine",
        "cutoffs": "🚀 Cutoff Engine",
        "gates": "🚀 Cutoff Engine",
        "📋 Audit Ledger": "📋 Audit Ledger",
        "aud": "📋 Audit Ledger",
        "audit": "📋 Audit Ledger",
    }
    gov_target = st.session_state.pop("gov_target_tab", None)
    if gov_target in gov_tab_aliases:
        gov_target = gov_tab_aliases[gov_target]
    default_gov_subtab = gov_target if gov_target in valid_gov_subtabs else valid_gov_subtabs[0]

    with st.container(key="gov_workspace_subtabs_box"):
        gov_tab_risk, gov_tab_disp, gov_tab_cad, gov_tab_cut, gov_tab_aud = st.tabs(
            valid_gov_subtabs,
            default=default_gov_subtab,
            key=f"gov_subtabs_bar_{reset_idx}"
        )

    # --- SUBTAB 1: INTEGRATED MASTER-DETAIL RISK COMMAND ---
    with gov_tab_risk:
        scope_prefix = f"{team_filter} · " if team_filter != "All Teams" else ""
        with st.container(key=f"gov_tab_actions_risk_{reset_idx}"):
            act_c1, act_c2, act_c3, act_c4 = st.columns([1.6, 0.85, 0.85, 0.75], gap="small")
            with act_c1:
                st.markdown(f"<div style='font-size:9.5px;color:#38bdf8;font-weight:700;padding-top:4px;white-space:nowrap;'>⚡ {scope_prefix}{len(urgent_records)} urgent items queued</div>", unsafe_allow_html=True)
            with act_c2:
                if st.button("+90d All", key=f"gov_risk_bulk_90d_{reset_idx}", use_container_width=True, help=f"Extend all {len(urgent_records)} items by 90 days"):
                    p90_dt = date.today() + timedelta(days=90)
                    edits = [(int(uid), p90_dt) for uid in urgent_records["id"]]
                    apply_edits(edits)
                    st.success(f"+90d applied to {len(edits)} items.")
                    rerun()
            with act_c3:
                if st.button("+1yr All", key=f"gov_risk_bulk_365d_{reset_idx}", use_container_width=True, help=f"Extend all {len(urgent_records)} items by 1 year"):
                    p365_dt = date.today() + timedelta(days=365)
                    edits = [(int(uid), p365_dt) for uid in urgent_records["id"]]
                    apply_edits(edits)
                    st.success(f"+1yr applied to {len(edits)} items.")
                    rerun()
            with act_c4:
                if hasattr(st, "popover"):
                    with st.popover("📅 Set", help="Set custom expiry date for all queued items", use_container_width=True):
                        c_dt = st.date_input("Target Date", value=date.today() + timedelta(days=90), key=f"gov_risk_pop_dt_{reset_idx}")
                        if st.button("Apply Date", type="primary", key=f"gov_risk_pop_apply_{reset_idx}", use_container_width=True):
                            edits = [(int(uid), c_dt) for uid in urgent_records["id"]]
                            apply_edits(edits)
                            st.success(f"Updated {len(edits)} items to {c_dt}!")
                            rerun()

        # MASTER-DETAIL 2-COLUMN SPLIT-PANE
        col_master, col_detail = st.columns([3.8, 6.2], gap="small")

        with col_master:
            # 1. Team Governance & Risk Distribution Matrix (Master)
            team_profiles = list(TEAM_GOVERNANCE_PROFILES.values())
            t_rows = []
            for p in team_profiles:
                tm_name = p['team']
                is_tm_active = (team_filter == tm_name)
                tm_border = "border-left:3px solid #38bdf8;background:rgba(56,189,248,0.14);" if is_tm_active else "border-bottom:1px solid #22252b;"
                tm_link_color = "#ffffff" if is_tm_active else "#38bdf8"
                t_sub = scoped_records[scoped_records['team'] == tm_name] if team_filter == "All Teams" else base_df[(base_df['team'] == tm_name) & ((base_df['state'] == state_filter) if state_filter != "All States" else True)]
                t_cnt = len(t_sub)
                t_exp = int((t_sub['days_left'] < 0).sum())
                t_crit = int((t_sub['days_left'].between(0, ui.CRITICAL_DAYS)).sum())
                t_warn = int((t_sub['days_left'].between(ui.CRITICAL_DAYS + 1, ui.WARNING_DAYS)).sum())

                if t_exp > 0:
                    status_badge = f"<span style='color:#f2495c;background:rgba(242,73,92,0.18);font-weight:700;font-size:8px;padding:1px 4px;border-radius:2px;'>● {t_exp} Exp</span>"
                elif t_crit > 0:
                    status_badge = f"<span style='color:#f2495c;background:rgba(242,73,92,0.18);font-weight:700;font-size:8px;padding:1px 4px;border-radius:2px;'>▲ {t_crit} Crit</span>"
                elif t_warn > 0:
                    status_badge = f"<span style='color:#ff9830;background:rgba(255,152,48,0.18);font-weight:700;font-size:8px;padding:1px 4px;border-radius:2px;'>{t_warn} Warn</span>"
                else:
                    status_badge = f"<span style='color:#10b981;background:rgba(16,185,129,0.16);font-weight:700;font-size:8px;padding:1px 4px;border-radius:2px;'>✓ OK</span>"

                t_rows.append(
                    f"<tr style='{tm_border}'>"
                    f"<td style='padding:2.5px 5px;font-weight:700;'><a href='?gov_tm={tm_name}{auth_suffix}' target='_self' style='color:{tm_link_color};text-decoration:none;'>{tm_name}</a></td>"
                    f"<td style='padding:2.5px 5px;color:#cbd5e1;'><span style='color:#e2e8f0;font-weight:600;'>{p['lead']}</span> <code style='font-size:8px;color:#64748b;'>{p['channel']}</code></td>"
                    f"<td style='padding:2.5px 5px;text-align:right;font-family:var(--mono);font-weight:700;'>{t_cnt}</td>"
                    f"<td style='padding:2.5px 5px;'>{status_badge}</td>"
                    f"<td style='padding:2.5px 5px;color:#94a3b8;font-size:8.5px;'>{p['cadence']}</td>"
                    f"</tr>"
                )

            clear_tm_link = f'<a href="?gov_tm=All Teams{auth_suffix}" target="_self" style="font-size:8px;color:#f59e0b;font-weight:700;text-decoration:none;border:1px solid #f59e0b;padding:1px 4px;border-radius:2px;line-height:1;">✕ Clear</a>' if team_filter != "All Teams" else ''

            st.markdown(f"""<div style="background:#181b1f;border:1px solid #2c3235;border-radius:3px;padding:4px 8px;box-sizing:border-box;height:212px;min-height:212px;margin-bottom:8px;display:flex;flex-direction:column;justify-content:space-between;">
<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:2px;">
<div style="font-size:9.5px;font-weight:700;color:#ff9830;text-transform:uppercase;letter-spacing:0.04em;">Team Governance &amp; Risk Distribution Matrix</div>
<div style="display:flex;align-items:center;gap:4px;"><span style="font-size:8px;color:#94a3b8;font-weight:600;">5 Teams Live</span>{clear_tm_link}</div>
</div>
<div style="overflow-y:auto;overflow-x:hidden;flex:1;scrollbar-width:none;">
<table style="font-size:9.5px;width:100%;border-collapse:collapse;color:#d8d9da;">
<thead style="position:sticky;top:0;background:#141619;border-bottom:1px solid #2c3235;color:#6e7681;text-transform:uppercase;font-size:8px;letter-spacing:0.03em;">
<tr><th style="padding:2px 5px;text-align:left;">Team</th><th style="padding:2px 5px;text-align:left;">Owner &amp; Channel</th><th style="padding:2px 5px;text-align:right;">Assets</th><th style="padding:2px 5px;text-align:left;">Risk Posture</th><th style="padding:2px 5px;text-align:left;">Cadence</th></tr>
</thead>
<tbody>
{''.join(t_rows)}
</tbody>
</table>
</div>
</div>""", unsafe_allow_html=True)

            # 2. Release Gate Cutoff Radar & Telemetry (Master)
            conn_rel = get_connection(DB_PATH)
            rel_all = get_release_schedules(conn_rel, state=None)
            conn_rel.close()

            today_date = date.today()
            milestone_items = []
            for r in rel_all:
                st_code = r.get("state", "")
                rid = r.get("release_id", "")
                rm_name = r.get("state_rm_name", f"{st_code} RM")
                rm_email = r.get("state_rm_email", f"{st_code.lower()}_rm@ets.state.gov")

                gates = [
                    ("DEV Freeze", r.get("dev_end_date"), "ENV52" if st_code == "NH" else "Build-76"),
                    ("SIT QA Gate", r.get("sit_end_date"), "ENV57" if st_code == "NH" else "SIT QA"),
                    ("State UAT Gate", r.get("uat_end_date"), "ENV04" if st_code == "NH" else "UAT"),
                    ("PROD Cutover", r.get("prod_deploy_date"), "ENV05" if st_code == "NH" else "PROD"),
                ]
                for g_name, g_date, g_env in gates:
                    if g_date:
                        try:
                            diff = (datetime.strptime(g_date, "%Y-%m-%d").date() - today_date).days
                            if -7 <= diff <= 30:
                                milestone_items.append({
                                    "state": st_code,
                                    "release_id": rid,
                                    "phase": g_name,
                                    "env": g_env,
                                    "cutoff_date": g_date,
                                    "days_left": diff,
                                    "rm_name": rm_name,
                                    "rm_email": rm_email,
                                    })
                        except Exception:
                            pass

            milestone_items.sort(key=lambda m: (m["days_left"] if m["days_left"] >= 0 else 999, abs(m["days_left"])))

            gate_rows = []
            for m in milestone_items[:3]:
                d = m["days_left"]
                if d < 0:
                    chip_html = f'<span style="font-size:8px;font-weight:700;color:#f2495c;background:rgba(242,73,92,0.18);padding:1px 4px;border-radius:2px;">{abs(d)}d OVERDUE</span>'
                elif d == 0:
                    chip_html = '<span style="font-size:8px;font-weight:700;color:#f2495c;background:rgba(242,73,92,0.18);padding:1px 4px;border-radius:2px;">TODAY</span>'
                elif d <= 3:
                    chip_html = f'<span style="font-size:8px;font-weight:700;color:#f2495c;background:rgba(242,73,92,0.18);padding:1px 4px;border-radius:2px;">{d}d LEFT</span>'
                elif d <= 7:
                    chip_html = f'<span style="font-size:8px;font-weight:700;color:#ff9830;background:rgba(255,152,48,0.18);padding:1px 4px;border-radius:2px;">{d}d LEFT</span>'
                else:
                    chip_html = f'<span style="font-size:8px;font-weight:700;color:#10b981;background:rgba(16,185,129,0.16);padding:1px 4px;border-radius:2px;">{d}d LEFT</span>'

                gate_rows.append(
                    f"<div style='display:flex;align-items:center;justify-content:space-between;background:#141619;border:1px solid #22252b;border-radius:2px;padding:2px 6px;font-size:9.5px;margin-bottom:2px;'>"
                    f"<div style='display:flex;align-items:center;gap:5px;'>"
                    f"<span style='font-family:var(--mono);font-weight:700;color:#38bdf8;font-size:8.5px;'>{m['state']}</span>"
                    f"<span style='font-family:var(--mono);font-weight:600;color:#f8fafc;font-size:9px;'>{m['release_id']}</span>"
                    f"<span style='color:#94a3b8;font-size:8.5px;'>{m['phase']}</span>"
                    f"</div>"
                    f"<div style='display:flex;align-items:center;gap:6px;'>"
                    f"<span style='font-family:var(--mono);color:#cbd5e1;font-size:8.5px;'>{m['cutoff_date']}</span>"
                    f"{chip_html}"
                    f"</div>"
                    f"</div>"
                )

            st.markdown(f"""<div style="background:#181b1f;border:1px solid #2c3235;border-radius:3px;padding:4px 8px;box-sizing:border-box;height:212px;min-height:212px;display:flex;flex-direction:column;justify-content:space-between;">
<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:2px;">
<div style="font-size:9.5px;font-weight:700;color:#38bdf8;text-transform:uppercase;letter-spacing:0.04em;">Release Gate Cutoff Radar &amp; Telemetry</div>
<span style="font-size:8px;color:#94a3b8;font-weight:600;">Upcoming Gates (≤30d)</span>
</div>
<div style="display:flex;flex-direction:column;gap:1.5px;">
{''.join(gate_rows) if gate_rows else '<div style="font-size:9px;color:#64748b;padding:6px 0;">No imminent gate cutoffs in next 30d</div>'}
</div>
<div style="display:grid;grid-template-columns:1fr 1fr;gap:4px;font-size:8.5px;border-top:1px solid #22252b;padding-top:3px;">
<div style="background:#141619;border:1px solid #22252b;border-radius:2px;padding:2px 5px;display:flex;justify-content:space-between;align-items:center;">
<span style="color:#10b981;font-weight:700;">PROD Resiliency</span>
<span style="color:var(--text);font-family:var(--mono);font-weight:700;">100% (0)</span>
</div>
<div style="background:#141619;border:1px solid #22252b;border-radius:2px;padding:2px 5px;display:flex;justify-content:space-between;align-items:center;">
<span style="color:#38bdf8;font-weight:700;">Fleet Scope</span>
<span style="color:var(--text);font-family:var(--mono);font-weight:700;">{len(base_df)} Assets</span>
</div>
<div style="background:#141619;border:1px solid #22252b;border-radius:2px;padding:2px 5px;display:flex;justify-content:space-between;align-items:center;">
<span style="color:#ff9830;font-weight:700;">Schedules</span>
<span style="color:var(--text);font-family:var(--mono);font-weight:700;">{stats.get('maintenance_schedules', 0)} Windows</span>
</div>
<div style="background:#141619;border:1px solid #22252b;border-radius:2px;padding:2px 5px;display:flex;justify-content:space-between;align-items:center;">
<span style="color:#73bf69;font-weight:700;">Audit Logs</span>
<span style="color:var(--text);font-family:var(--mono);font-weight:700;">{stats.get('reminder_log', 0)} Logged</span>
</div>
</div>
</div>""", unsafe_allow_html=True)

        with col_detail:
            if urgent_records.empty:
                st.markdown(f"""<div style="padding:48px 16px;text-align:center;background:#181b1f;border:1px solid #2c3235;border-radius:3px;height:432px;display:flex;flex-direction:column;align-items:center;justify-content:center;box-sizing:border-box;">
<div style="font-size:14px;font-weight:700;color:#10b981;">✓ {scope_prefix or 'Scope '}100% In Compliance</div>
<div style="font-size:11px;color:#94a3b8;margin-top:4px;">No expired or critical debt entities found matching current filters.</div>
</div>""", unsafe_allow_html=True)
            else:
                q_rows = []
                for ur in urgent_records.itertuples():
                    ur_meta = ui.BAND_META.get(ur.band, ui.BAND_META["Healthy"])
                    ur_code = ui.COMPONENT_CODE.get(ur.component, ur.component)
                    ur_icon = ui.COMPONENT_ICONS.get(ur.component, "📦")
                    ur_meta_color = ur_meta["color"]
                    ur_meta_tint = ur_meta["tint"]
                    days_color = "#f2495c" if ur.days_left < 0 else ("#ff9830" if ur.days_left <= 15 else "#73bf69")
                    action_btn_html = f'<a href="?op_act_id={ur.id}&op_subtab=insp{auth_suffix}" target="_self" style="text-decoration:none;display:inline-flex;align-items:center;padding:1.5px 6px;border-radius:2px;font-size:9px;font-weight:700;background:rgba(56,189,248,0.15);border:1px solid rgba(56,189,248,0.3);color:#38bdf8;">Inspect ↗</a>'

                    q_rows.append(
                        f"<tr style='border-bottom:1px solid #22252b;'>"
                        f"<td style='padding:3px 5px;font-family:var(--mono);'><span style='color:#f8fafc;font-weight:700;'>#{ur.id}</span></td>"
                        f"<td style='padding:3px 5px;'><span style='color:{ur_meta_color};background:{ur_meta_tint};font-weight:700;font-size:8.5px;padding:1px 4px;border-radius:2px;'>{ur.band}</span></td>"
                        f"<td style='padding:3px 5px;font-weight:700;color:#9fa7b3;'>{ur.state}</td>"
                        f"<td style='padding:3px 5px;'><span class='env-tag'>{ur.env_label}</span></td>"
                        f"<td style='padding:3px 5px;color:#cbd5e1;'>{ur.team}</td>"
                        f"<td style='padding:3px 5px;color:#d8d9da;'>{ur_icon} {ur_code}</td>"
                        f"<td style='padding:3px 5px;font-family:var(--mono);font-weight:600;color:#f8fafc;'>{ur.schema_name}</td>"
                        f"<td style='padding:3px 5px;font-family:var(--mono);color:#cbd5e1;'>{ur.exp_date}</td>"
                        f"<td style='padding:3px 5px;font-family:var(--mono);color:{days_color};font-weight:700;'>{ui.fmt_days(ur.days_left)}</td>"
                        f"<td style='padding:3px 5px;text-align:right;'>{action_btn_html}</td>"
                        f"</tr>"
                    )

                st.markdown(f"""<div class="gov-table-container">
<table style="width:100%;border-collapse:collapse;font-size:9.5px;color:#d8d9da;">
<thead style="position:sticky;top:0;background:#141619;border-bottom:1px solid #2c3235;z-index:2;">
<tr style="color:#6e7681;text-transform:uppercase;font-size:8.5px;font-weight:600;letter-spacing:0.03em;">
<th style="padding:3px 5px;text-align:left;">ID</th>
<th style="padding:3px 5px;text-align:left;">Severity</th>
<th style="padding:3px 5px;text-align:left;">State</th>
<th style="padding:3px 5px;text-align:left;">Env</th>
<th style="padding:3px 5px;text-align:left;">Team</th>
<th style="padding:3px 5px;text-align:left;">Component</th>
<th style="padding:3px 5px;text-align:left;">Schema / Asset</th>
<th style="padding:3px 5px;text-align:left;">Current Expiry</th>
<th style="padding:3px 5px;text-align:left;">Time Left</th>
<th style="padding:3px 5px;text-align:right;">Action</th>
</tr>
</thead>
<tbody>
{''.join(q_rows)}
</tbody>
</table>
</div>""", unsafe_allow_html=True)

    # --- SUBTAB 2: ALERT DISPATCH (SIMULATION & LIVE SMTP) ---
    with gov_tab_disp:
        sim_team_default = team_filter if team_filter in ui.TEAMS else ui.TEAMS[0]
        sim_team_idx = ui.TEAMS.index(sim_team_default) if sim_team_default in ui.TEAMS else 0

        with st.container(key=f"gov_tab_actions_disp_{reset_idx}"):
            disp_act_c1, disp_act_c2 = st.columns([1.5, 1.5], gap="small")
            with disp_act_c1:
                sim_trig = st.button("▶ Trigger Dry-Run", key=f"gov_trig_dry_{reset_idx}", type="secondary", use_container_width=True, help="Simulate email dispatch and log audit event")
            with disp_act_c2:
                real_trig = st.button("🚀 Send Real SMTP Test", key=f"gov_trig_real_{reset_idx}", type="primary", use_container_width=True, help="Transmit live email packets to designated inboxes")

        disp_col_left, disp_col_right = st.columns([1.1, 1.9], gap="small")

        with disp_col_left:
            ds_c1, ds_c2 = st.columns(2)
            sim_st = ds_c1.selectbox("State", STATES, key=f"sim_st_{reset_idx}")
            sim_tm = ds_c2.selectbox("Team", ui.TEAMS, index=sim_team_idx, key=f"sim_tm_{reset_idx}")

            team_gov = TEAM_GOVERNANCE_PROFILES.get(sim_tm, TEAM_GOVERNANCE_PROFILES["Core"])
            owner_role = team_gov["lead"]
            owner_channel = team_gov["channel"]

            conn = get_connection(DB_PATH)
            cur_sim = conn.execute(
                "SELECT * FROM component_records WHERE state = ? AND team = ? ORDER BY CAST(env_no AS INTEGER)",
                (sim_st, sim_tm)
            ).fetchall()
            conn.close()
            sim_recs = [dict(r) for r in cur_sim]

            sim_opts = {f"{r['schema_name']} ({r['environment']}) · {ui.COMPONENT_CODE.get(r['component'], r['component'])}": r for r in sim_recs} if sim_recs else {}
            sim_pick_lbl = st.selectbox("Target Entity", list(sim_opts) if sim_opts else ["No entities"], key=f"sim_pick_{reset_idx}")
            sim_chosen = sim_opts.get(sim_pick_lbl, sim_recs[0] if sim_recs else None)

            env_cfg = smtp_config_from_env()
            sess_host = st.session_state.get("gov_smtp_host", env_cfg.get("host") or "")
            sess_port = st.session_state.get("gov_smtp_port", env_cfg.get("port") or 587)
            sess_user = st.session_state.get("gov_smtp_user", env_cfg.get("user") or "")
            sess_pass = st.session_state.get("gov_smtp_pass", env_cfg.get("password") or "")
            has_live_creds = bool(sess_host and sess_user and sess_pass)

            with st.expander("⚙️ Live SMTP Credentials", expanded=not has_live_creds):
                c_h1, c_h2 = st.columns([1.6, 0.8])
                live_host = c_h1.text_input("SMTP Host", value=sess_host, placeholder="smtp.gmail.com", key=f"g_host_{reset_idx}")
                live_user = c_h1.text_input("Username", value=sess_user, placeholder="user@gmail.com", key=f"g_user_{reset_idx}")
                live_port = c_h2.text_input("Port", value=str(sess_port), placeholder="587", key=f"g_port_{reset_idx}")
                live_pass = c_h2.text_input("Password", value=sess_pass, type="password", placeholder="App Password", key=f"g_pass_{reset_idx}")

                port_val = int(live_port.strip()) if live_port.strip().isdigit() else 587
                st.session_state["gov_smtp_host"] = live_host.strip()
                st.session_state["gov_smtp_port"] = port_val
                st.session_state["gov_smtp_user"] = live_user.strip()
                st.session_state["gov_smtp_pass"] = live_pass.strip()
                st.session_state["gov_smtp_from"] = live_user.strip()

        with disp_col_right:
            if sim_chosen:
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

                if sim_trig:
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

                if real_trig:
                    if not can_write():
                        st.error("🔒 Live SMTP dispatch requires Operator or Admin role.")
                    else:
                        active_host = st.session_state.get("gov_smtp_host")
                        active_port = st.session_state.get("gov_smtp_port", 587)
                        active_user = st.session_state.get("gov_smtp_user")
                        active_pass = st.session_state.get("gov_smtp_pass")
                        active_from = st.session_state.get("gov_smtp_from") or active_user

                        if not (active_host and active_user and active_pass):
                            st.error("❌ Live SMTP Dispatch Blocked: Missing credentials in '⚙️ Live SMTP Credentials'.")
                        else:
                            real_recipients = ["basha.shaikirfan@gmail.com", "dataengineerib@gmail.com"]
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
                                    try:
                                        conn_aud = get_connection(DB_PATH)
                                        log_audit_event(
                                            conn_aud,
                                            actor=st.session_state.get("active_user", "admin"),
                                            role=st.session_state.get("user_role", ROLE_OPERATOR),
                                            action="EMAIL_DISPATCHED",
                                            target_entity="Live SMTP Overdue Alert",
                                            details=f"Delivered overdue alert with {len(exp_list)} records to {len(real_recipients)} recipient(s).",
                                        )
                                        conn_aud.close()
                                    except Exception:
                                        pass
                                except Exception as ex:
                                    st.error(f"❌ Real Delivery Failed: {ex}")

                st.markdown(f"""
                <div style="background:#181b1f;border:1px solid #2c3235;border-radius:2px;padding:6px 10px;font-size:10px;margin-bottom:4px;">
                  <div style="display:flex;justify-content:space-between;margin-bottom:2px;">
                    <span style="color:#94a3b8;"><b>To:</b> {sim_mock['owner_name']} &lt;{sim_mock['owner_email']}&gt;</span>
                    <span style="color:#f59e0b;font-weight:700;">● Simulation &amp; Live Channel</span>
                  </div>
                  <div style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:#f8fafc;font-weight:600;"><b>Subject:</b> {email_subject}</div>
                </div>
                <div style="background:#ffffff;border:1px solid #2c3235;border-radius:2px;height:400px;overflow-y:auto;padding:6px;">
                  {email_html}
                </div>
                """, unsafe_allow_html=True)

                if "last_real_receipt" in st.session_state:
                    rcpt = st.session_state["last_real_receipt"]
                    st.markdown(f"""
                    <div style="background:rgba(16,185,129,0.12);border:1px solid rgba(16,185,129,0.3);border-radius:2px;padding:4px 8px;margin-top:4px;font-size:9.5px;color:#10b981;">
                      <b>Receipt:</b> {rcpt.get('smtp_response', '250 OK')} &middot; {rcpt.get('timestamp')} &middot; {rcpt.get('item_count')} Expired Items
                    </div>
                    """, unsafe_allow_html=True)
            else:
                st.info("No entity records available for simulated preview.")

    # --- SUBTAB 3: CADENCE CONSOLE ---
    with gov_tab_cad:
        with st.container(key=f"gov_tab_actions_cad_{reset_idx}"):
            cad_act_c1, cad_act_c2 = st.columns([1.5, 1.5], gap="small")
            with cad_act_c1:
                cad_sim_trig = st.button("▶ Simulate Cadence", key=f"gov_cad_sim_{reset_idx}", type="secondary", use_container_width=True)
            with cad_act_c2:
                cad_real_trig = st.button("🚀 Send Cadence Email", key=f"gov_cad_real_{reset_idx}", type="primary", use_container_width=True)

        cad_col_left, cad_col_right = st.columns([1.1, 1.9], gap="small")

        with cad_col_left:
            cad_st_pick = st.selectbox(
                "Select State Scope",
                ["Fleet-Wide (All States)", "Alaska (AK)", "North Dakota (ND)", "New Hampshire (NH)"],
                key=f"gov_cad_st_{reset_idx}"
            )
            cad_st_map = {
                "Fleet-Wide (All States)": None,
                "Alaska (AK)": "AK",
                "North Dakota (ND)": "ND",
                "New Hampshire (NH)": "NH"
            }
            cad_state_code = cad_st_map[cad_st_pick]

            default_recips = {
                "AK": "ak-operations@ets.internal, basha.shaikirfan@gmail.com, dataengineerib@gmail.com",
                "ND": "nd-operations@ets.internal, basha.shaikirfan@gmail.com, dataengineerib@gmail.com",
                "NH": "nh-operations@ets.internal, basha.shaikirfan@gmail.com, dataengineerib@gmail.com",
                None: "fleet-operations@ets.internal, basha.shaikirfan@gmail.com, dataengineerib@gmail.com",
            }
            cad_recips_input = st.text_input(
                "Target Distribution List",
                value=default_recips[cad_state_code],
                key=f"gov_cad_recips_{cad_state_code}_{reset_idx}"
            )

            conn_cad = get_connection(DB_PATH)
            all_schedules = get_maintenance_schedules(conn_cad)
            conn_cad.close()
            st_schedules = [s for s in all_schedules if not cad_state_code or s.get("state") == cad_state_code]

            st.markdown(f"""
            <div style="background:#141619;border:1px solid #2c3235;border-radius:2px;padding:6px 10px;font-size:10px;margin-top:4px;">
              <span style="color:#38bdf8;font-weight:700;">{len(st_schedules)} Planned Windows</span>
              <div style="color:#94a3b8;font-size:9px;margin-top:2px;">Coordinates operational maintenance hours and weekly cadence across all 5 functional teams.</div>
            </div>
            """, unsafe_allow_html=True)

        with cad_col_right:
            cad_email_html = render_maintenance_cadence_email(
                all_schedules,
                state=cad_state_code,
                extra_context={"recipients_str": cad_recips_input}
            )

            if cad_sim_trig:
                st.toast(f"Simulated Cadence alert logged for {cad_st_pick} ({len(st_schedules)} windows)", icon="📅")

            if cad_real_trig:
                if not can_write():
                    st.error("🔒 Real SMTP dispatch requires Operator or Admin role.")
                else:
                    active_host = st.session_state.get("gov_smtp_host")
                    active_port = st.session_state.get("gov_smtp_port", 587)
                    active_user = st.session_state.get("gov_smtp_user")
                    active_pass = st.session_state.get("gov_smtp_pass")
                    active_from = st.session_state.get("gov_smtp_from") or active_user

                    if not (active_host and active_user and active_pass):
                        st.error("❌ Live SMTP Dispatch Blocked: Configure SMTP Server Credentials in Alert Dispatch first.")
                    else:
                        active_smtp_config = {
                            "host": active_host,
                            "port": int(active_port),
                            "user": active_user,
                            "password": active_pass,
                            "from_addr": active_from,
                        }
                        recips_list = [e.strip() for e in cad_recips_input.split(",") if e.strip() and "@" in e and not e.strip().endswith(".internal")]
                        if not recips_list:
                            st.warning("⚠️ No valid live test email address found in recipients.")
                        else:
                            with st.spinner(f"Transmitting weekly cadence alert for {cad_st_pick}..."):
                                try:
                                    rcpt = dispatch_cadence_alert_real(
                                        recipients=recips_list,
                                        schedules=all_schedules,
                                        state=cad_state_code,
                                        smtp_config=active_smtp_config,
                                    )
                                    st.session_state["last_cadence_receipt"] = rcpt
                                    st.success(f"✓ Weekly Cadence Alert delivered to {', '.join(recips_list)}!")
                                except Exception as ex:
                                    st.error(f"❌ Real Delivery Failed: {ex}")

            st.markdown(f"""
            <div style="background:#181b1f;border:1px solid #2c3235;border-radius:2px;padding:6px 10px;font-size:10px;margin-bottom:4px;">
              <div style="display:flex;justify-content:space-between;margin-bottom:2px;">
                <span style="color:#94a3b8;"><b>Scope:</b> {cad_st_pick.upper()} &middot; {len(st_schedules)} Windows</span>
                <span style="color:#38bdf8;font-weight:700;">● Weekly Cadence Notice</span>
              </div>
              <div style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:#f8fafc;font-weight:600;"><b>Subject:</b> [CADENCE NOTICE] ETS Weekly Maintenance Windows: {cad_st_pick}</div>
            </div>
            <div style="background:#ffffff;border:1px solid #2c3235;border-radius:2px;height:400px;overflow-y:auto;padding:6px;">
              {cad_email_html}
            </div>
            """, unsafe_allow_html=True)

    # --- SUBTAB 4: RELEASE GATE CUTOFF ENGINE ---
    with gov_tab_cut:
        conn_rel = get_connection(DB_PATH)
        rel_all = get_release_schedules(conn_rel, state=None)
        conn_rel.close()

        today_date = date.today()
        milestone_rows = []
        for r in rel_all:
            st_code = r.get("state", "")
            rid = r.get("release_id", "")
            rm_name = r.get("state_rm_name", f"{st_code} Release Manager")
            rm_email = r.get("state_rm_email", f"{st_code.lower()}_rm@ets.state.gov")

            gates = [
                ("DEV Freeze", r.get("dev_end_date"), "ENV52 Dev" if st_code == "NH" else "Build-76"),
                ("SIT QA Gate", r.get("sit_end_date"), "ENV57 / ENV53" if st_code == "NH" else "SIT QA"),
                ("State UAT Gate", r.get("uat_end_date"), "ENV04 UAT" if st_code == "NH" else "State Acceptance"),
                ("Go / No-Go Board", r.get("go_nogo_date"), "Decision Board"),
                ("Production Cutover", r.get("prod_deploy_date"), "ENV05 Live" if st_code == "NH" else "PROD Cutover"),
            ]
            for g_name, g_date, g_env in gates:
                if g_date:
                    try:
                        diff = (datetime.strptime(g_date, "%Y-%m-%d").date() - today_date).days
                        if -14 <= diff <= 45:
                            s_lbl = f"{abs(diff)}d OVERDUE" if diff < 0 else ("CUTOFF TODAY" if diff == 0 else f"{diff}d REMAINING")
                            s_chip = "firing" if diff <= 3 else ("pending" if diff <= 7 else "ok")
                            milestone_rows.append({
                                "state": st_code,
                                "release_id": rid,
                                "phase": g_name,
                                "env": g_env,
                                "cutoff_date": g_date,
                                "days_left": diff,
                                "status_label": s_lbl,
                                "chip": s_chip,
                                "rm_name": rm_name,
                                "rm_email": rm_email,
                            })
                    except Exception:
                        pass

        milestone_rows.sort(key=lambda m: (0 if m["chip"] == "firing" else (1 if m["chip"] == "pending" else 2), m["days_left"]))

        m_opts = {f"{m['state']} · {m['release_id']} ({m['phase']}) — {m['cutoff_date']}": m for m in milestone_rows} if milestone_rows else {}

        with st.container(key=f"gov_tab_actions_cut_{reset_idx}"):
            rc_c1, rc_c2 = st.columns([2.2, 1.8], gap="small")
            with rc_c1:
                chosen_m_lbl = st.selectbox("Target Cutoff Milestone", list(m_opts.keys()) if m_opts else ["No cutoffs"], key=f"gov_cut_pick_{reset_idx}", label_visibility="collapsed")
            with rc_c2:
                dispatch_gate_btn = st.button("🚀 Dispatch Cutoff Alert", key=f"gov_cut_send_{reset_idx}", type="primary", use_container_width=True)

        cut_col_left, cut_col_right = st.columns([1.1, 1.9], gap="small")

        with cut_col_left:
            if not milestone_rows:
                st.info("No release cutoffs within the next 45 days.")
            else:
                m_table_rows = []
                for m in milestone_rows[:12]:
                    m_table_rows.append(
                        f"<tr style='border-bottom:1px solid #22252b;'>"
                        f"<td style='padding:3px 6px;font-family:var(--mono);font-weight:700;color:#38bdf8;'>{m['state']}</td>"
                        f"<td style='padding:3px 6px;font-family:var(--mono);color:#f8fafc;'>{m['release_id']}</td>"
                        f"<td style='padding:3px 6px;color:#cbd5e1;'>{m['phase']}</td>"
                        f"<td style='padding:3px 6px;font-family:var(--mono);color:#94a3b8;'>{m['cutoff_date']}</td>"
                        f"<td style='padding:3px 6px;'><span class='alert-chip {m['chip']}'>{m['status_label']}</span></td>"
                        f"</tr>"
                    )

                st.markdown(f"""
                <div class="gov-table-container" style="height:420px;max-height:420px;">
                  <table style="width:100%;border-collapse:collapse;font-size:9.5px;color:#d8d9da;">
                    <thead style="position:sticky;top:0;background:#141619;border-bottom:1px solid #2c3235;z-index:2;color:#6e7681;text-transform:uppercase;font-size:8px;">
                      <tr><th style="padding:3px 6px;text-align:left;">State</th><th style="padding:3px 6px;text-align:left;">Release</th><th style="padding:3px 6px;text-align:left;">Phase</th><th style="padding:3px 6px;text-align:left;">Date</th><th style="padding:3px 6px;text-align:left;">Status</th></tr>
                    </thead>
                    <tbody>
                      {''.join(m_table_rows)}
                    </tbody>
                  </table>
                </div>
                """, unsafe_allow_html=True)

        with cut_col_right:
            chosen_m = m_opts.get(chosen_m_lbl)
            if chosen_m:
                if dispatch_gate_btn:
                    if not can_write():
                        st.error("🔒 Dispatching cutoff alerts requires Operator or Admin role.")
                    else:
                        try:
                            conn_aud = get_connection(DB_PATH)
                            log_audit_event(
                                conn_aud,
                                actor=st.session_state.get("active_user", "admin"),
                                role=st.session_state.get("user_role", ROLE_OPERATOR),
                                action="EMAIL_DISPATCHED",
                                target_entity=f"{chosen_m['state']} {chosen_m['release_id']} {chosen_m['phase']}",
                                details=f"Release cutoff alert dispatched to {chosen_m['rm_name']} <{chosen_m['rm_email']}> for cutoff {chosen_m['cutoff_date']} ({chosen_m['status_label']}).",
                            )
                            conn_aud.close()
                            st.success(f"✓ Cutoff Alert dispatched for {chosen_m['release_id']} ({chosen_m['phase']}) to {chosen_m['rm_name']}!")
                        except Exception as ex:
                            st.error(f"Dispatch failed: {ex}")

                st.markdown(f"""
                <div style="background:#141619;border:1px solid #2c3235;border-radius:2px;padding:8px;font-family:var(--mono);font-size:10px;height:420px;box-sizing:border-box;overflow-y:auto;">
                  <div style="color:#94a3b8;font-weight:700;margin-bottom:4px;">📧 Preview Release Cutoff Alert Email Template</div>
                  <div style="color:#cbd5e1;margin-bottom:2px;"><b style="color:#f8fafc;">TO:</b> {chosen_m['rm_name']} &lt;{chosen_m['rm_email']}&gt;</div>
                  <div style="color:#cbd5e1;margin-bottom:6px;"><b style="color:#f8fafc;">SUBJECT:</b> [GATE ALERT] {chosen_m['state']} MMIS — {chosen_m['release_id']} {chosen_m['phase']} Deadline: {chosen_m['cutoff_date']}</div>
                  <div style="border-top:1px solid #2c3235;padding-top:6px;color:#d8d9da;line-height:1.4;">
                    <p style="margin-bottom:4px;">Attention State Release Management,</p>
                    <p style="margin-bottom:6px;">This is an automated ETS notification regarding the upcoming pipeline gate cutoff for <b>{chosen_m['release_id']}</b>.</p>
                    <table style="border:1px solid #2c3235;background:#181b1f;padding:4px;width:100%;font-size:9.5px;">
                      <tr><td style="color:#94a3b8;">State Scope:</td><td><b>{chosen_m['state']} MMIS</b></td></tr>
                      <tr><td style="color:#94a3b8;">Release ID:</td><td><b>{chosen_m['release_id']}</b></td></tr>
                      <tr><td style="color:#94a3b8;">Phase / Gate:</td><td><b style="color:#38bdf8;">{chosen_m['phase']}</b></td></tr>
                      <tr><td style="color:#94a3b8;">Environment:</td><td><b>{chosen_m['env']}</b></td></tr>
                      <tr><td style="color:#94a3b8;">Cutoff Deadline:</td><td><b style="color:#f2495c;">{chosen_m['cutoff_date']}</b></td></tr>
                      <tr><td style="color:#94a3b8;">Remaining Window:</td><td><b style="color:#ff9830;">{chosen_m['status_label']}</b></td></tr>
                    </table>
                  </div>
                </div>
                """, unsafe_allow_html=True)
            else:
                st.info("Select a milestone to preview template.")

    # --- SUBTAB 5: AUDIT LEDGER & AST RE-INGEST ---
    with gov_tab_aud:
        with st.container(key=f"gov_tab_actions_aud_{reset_idx}"):
            aud_act_c1, aud_act_c2 = st.columns([2.0, 1.2], gap="small")
            with aud_act_c1:
                st.markdown(f"<div style='font-size:9px;color:#10b981;font-weight:700;padding-top:4px;'>● ZERO-MOCK LINEAGE: <code>{Path(DB_PATH).name}</code></div>", unsafe_allow_html=True)
            with aud_act_c2:
                reing_btn = st.button("⚡ Trigger Re-ingest (AST)", key=f"gov_reingest_{reset_idx}", type="primary", use_container_width=True)

        aud_col_left, aud_col_right = st.columns([1.0, 1.5], gap="small")

        with aud_col_left:
            st.markdown(f"""
            <div style="background:#181b1f;border:1px solid #2c3235;border-radius:2px;padding:6px 8px;height:420px;box-sizing:border-box;overflow-y:auto;">
              <div style="font-size:9.5px;font-weight:700;color:#f8fafc;margin-bottom:6px;">Database Table Lineage Matrix</div>
              <table style="width:100%;border-collapse:collapse;font-size:9.5px;color:#d8d9da;">
                <tr style="border-bottom:1px solid #2c3235;color:#6e7681;font-size:8.5px;">
                  <th style="text-align:left;padding:3px 4px;">Table</th>
                  <th style="text-align:right;padding:3px 4px;">Rows</th>
                  <th style="text-align:left;padding:3px 4px;">Lineage Role</th>
                  <th style="text-align:right;padding:3px 4px;">Status</th>
                </tr>
                <tr style="border-bottom:1px solid #22252b;"><td style="padding:3px 4px;font-family:var(--mono);">component_records</td><td style="text-align:right;padding:3px 4px;font-family:var(--mono);font-weight:700;">{stats['component_records']}</td><td style="color:#94a3b8;padding:3px 4px;">Multi-Component</td><td style="text-align:right;padding:3px 4px;"><span style="color:#10b981;font-weight:700;font-size:8px;">Active</span></td></tr>
                <tr style="border-bottom:1px solid #22252b;"><td style="padding:3px 4px;font-family:var(--mono);">expiry_records</td><td style="text-align:right;padding:3px 4px;font-family:var(--mono);font-weight:700;">{stats['expiry_records']}</td><td style="color:#94a3b8;padding:3px 4px;">DB Passwords</td><td style="text-align:right;padding:3px 4px;"><span style="color:#10b981;font-weight:700;font-size:8px;">Active</span></td></tr>
                <tr style="border-bottom:1px solid #22252b;"><td style="padding:3px 4px;font-family:var(--mono);">maintenance_schedules</td><td style="text-align:right;padding:3px 4px;font-family:var(--mono);font-weight:700;">{stats.get('maintenance_schedules', 0)}</td><td style="color:#94a3b8;padding:3px 4px;">Maintenance Windows</td><td style="text-align:right;padding:3px 4px;"><span style="color:#38bdf8;font-weight:700;font-size:8px;">Synced</span></td></tr>
                <tr style="border-bottom:1px solid #22252b;"><td style="padding:3px 4px;font-family:var(--mono);">owners</td><td style="text-align:right;padding:3px 4px;font-family:var(--mono);font-weight:700;">{stats['owners']}</td><td style="color:#94a3b8;padding:3px 4px;">Owner Routing</td><td style="text-align:right;padding:3px 4px;"><span style="color:#38bdf8;font-weight:700;font-size:8px;">3 States</span></td></tr>
                <tr style="border-bottom:1px solid #22252b;"><td style="padding:3px 4px;font-family:var(--mono);">reminder_log</td><td style="text-align:right;padding:3px 4px;font-family:var(--mono);font-weight:700;">{stats['reminder_log']}</td><td style="color:#94a3b8;padding:3px 4px;">Audit &amp; Reminders</td><td style="text-align:right;padding:3px 4px;"><span style="color:#94a3b8;font-weight:700;font-size:8px;">Ready</span></td></tr>
              </table>
            </div>
            """, unsafe_allow_html=True)

        with aud_col_right:
            if reing_btn:
                t_start = datetime.now()
                with st.spinner("Executing workbook parser..."):
                    res = run_ingest(WORKBOOK_DIR, DB_PATH)
                duration_ms = (datetime.now() - t_start).total_seconds() * 1000
                bust_cache()
                st.success(f"Ingested {res['total_rows_read']} records in {duration_ms:.1f}ms ({res['new']} new, {res['renewed']} renewed).")
                rerun()

            conn_audit = get_connection(DB_PATH)
            recent_logs = conn_audit.execute(
                "SELECT state, schema_name, last_sent_at, times_sent FROM reminder_log ORDER BY last_sent_at DESC LIMIT 10"
            ).fetchall()
            conn_audit.close()

            if recent_logs:
                rl_rows = "".join(
                    f"<tr style='border-bottom:1px solid #22252b;'>"
                    f"<td style='padding:3px 6px;font-family:var(--mono);font-weight:700;color:#38bdf8;'>{r['state']}</td>"
                    f"<td style='padding:3px 6px;font-family:var(--mono);color:#f8fafc;'><code>{r['schema_name']}</code></td>"
                    f"<td style='padding:3px 6px;font-family:var(--mono);color:#94a3b8;'>{r['last_sent_at']}</td>"
                    f"<td style='padding:3px 6px;font-family:var(--mono);text-align:right;font-weight:700;'>{r['times_sent']}</td>"
                    f"</tr>"
                    for r in recent_logs
                )
                st.markdown(f"""
                <div class="gov-table-container" style="height:420px;max-height:420px;">
                  <table style="width:100%;border-collapse:collapse;font-size:9.5px;color:#d8d9da;">
                    <thead style="position:sticky;top:0;background:#141619;border-bottom:1px solid #2c3235;z-index:2;color:#6e7681;text-transform:uppercase;font-size:8px;">
                      <tr><th style="padding:3px 6px;text-align:left;">State</th><th style="padding:3px 6px;text-align:left;">Entity Audited</th><th style="padding:3px 6px;text-align:left;">Last Audit Date</th><th style="padding:3px 6px;text-align:right;">Dispatches</th></tr>
                    </thead>
                    <tbody>
                      {rl_rows}
                    </tbody>
                  </table>
                </div>
                """, unsafe_allow_html=True)
            else:
                st.markdown("""
                <div style="padding:32px 16px;text-align:center;background:#181b1f;border:1px solid #2c3235;border-radius:2px;color:#94a3b8;font-size:10px;">
                  No reminder dispatches recorded yet.
                </div>
                """, unsafe_allow_html=True)


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


# ==============================================================================
# View 4: Access Control & Security Audit Trail (RBAC)
# ==============================================================================
def render_rbac_workspace() -> None:
    """
    Dedicated Role-Based Access Control (RBAC) & Enterprise Security Audit Console.
    Strictly follows Grafana Flat Design System tokens:
      - 2px sharp corners across cards, panels, and chips
      - #181b1f panel backgrounds, #141619 sunken headers/tiles, #2c3235 borders
      - Grafana stat cards (ui.grafana_stat_card)
      - Grafana alert chips (.alert-chip.firing, .alert-chip.pending, .alert-chip.ok)
      - Monospace tabular data
    """
    conn = get_connection(DB_PATH)
    all_users = get_users(conn)
    users = [u for u in all_users if not u["username"].startswith("testuser_")]
    audit_logs = get_audit_logs(conn, limit=200)
    conn.close()

    total_users = len(users)
    admin_count = sum(1 for u in users if u["role"] == "Admin")
    op_count = sum(1 for u in users if u["role"] == "Operator")
    audit_count = sum(1 for u in users if u["role"] == "Auditor")
    viewer_count = sum(1 for u in users if u["role"] == "Viewer")
    total_audit_events = len(audit_logs)
    active_u = st.session_state.get("active_user", "admin")
    u_role = st.session_state.get("user_role", "Viewer")
    h_col1, h_col2 = st.columns([7.2, 2.8])
    with h_col1:
        st.markdown(ui.render_universal_header(
            title="Access Control & Security Audit",
            subtitle="Role-Based Access Control (RBAC), Entitlements & Immutable Ledger",
            badge_text="ZERO-TRUST RBAC",
            badge_color="#ec4899",
            state_scope=st.session_state.get("assigned_state"),
        ), unsafe_allow_html=True)
    with h_col2:
        st.markdown(
            f'<div style="display:flex;align-items:center;justify-content:flex-end;gap:8px;height:38px;">'
            f'<span style="font-size:9.5px;color:var(--mute);">Active Session:</span>'
            f'<span style="font-size:10px;font-weight:700;color:#f8fafc;background:rgba(255,255,255,0.06);padding:2px 8px;border-radius:2px;border:1px solid var(--rule);">{escape(active_u)} ({escape(u_role)})</span>'
            f'</div>',
            unsafe_allow_html=True
        )

    # 1. Grafana Metric Ribbon (4 Stat Panels)
    rc1, rc2, rc3, rc4 = st.columns(4)
    with rc1:
        st.markdown(ui.grafana_stat_card(
            label="Total Enterprise Users",
            value=f"{total_users} Accounts",
            color="#5794f2",
            subtext=f"{admin_count} Admin · {op_count} Operator · {audit_count} Auditor",
            badge="DIRECTORY",
            sparkline_vals=[1, 2, 3, total_users],
            delta="RBAC Active",
            state="ok",
        ), unsafe_allow_html=True)
    with rc2:
        st.markdown(ui.grafana_stat_card(
            label="Security Administrators",
            value=f"{admin_count} Admin",
            color="#f2495c",
            subtext="Privileged enterprise access",
            badge="PRIVILEGED",
            sparkline_vals=[1, 1, 1, 1],
            delta="Zero-Trust Root",
            state="firing",
        ), unsafe_allow_html=True)
    with rc3:
        st.markdown(ui.grafana_stat_card(
            label="Operational Personnel",
            value=f"{op_count} Operators",
            color="#73bf69",
            subtext="AK, ND, NH State RM Entitlements",
            badge="ISOLATED",
            sparkline_vals=[1, 2, 3, op_count],
            delta="Silent RBAC Scope",
            state="ok",
        ), unsafe_allow_html=True)
    with rc4:
        st.markdown(ui.grafana_stat_card(
            label="Security Audit Density",
            value=f"{total_audit_events} Events",
            color="#ff9830",
            subtext="Immutable event ledger active",
            badge="IMMUTABLE",
            sparkline_vals=[90, 100, 110, total_audit_events],
            delta="PBKDF2-SHA256",
            state="pending",
        ), unsafe_allow_html=True)


    # 3. Sub-tabs for RBAC workspace
    subtab_users, subtab_audit = st.tabs([
        "👥 Enterprise User Directory & Provisioning",
        "🛡️ Compliance & Security Audit Trail",
    ])

    with subtab_users:
        uc1, uc2 = st.columns([1.1, 1.9], gap="medium")

        with uc1:
            st.markdown(ui.panel_header("Provision Enterprise User", color="#5794f2", count="Admin Only"), unsafe_allow_html=True)
            if not is_admin():
                st.markdown(render_access_denied(
                    reason="Provisioning new enterprise user accounts and assigning security roles requires Administrator (Admin) privileges. Current session is read-only.",
                    required_role="Admin"
                ), unsafe_allow_html=True)
            else:
                with st.form("rbac_create_user_form", clear_on_submit=True):
                    pf_c1, pf_c2 = st.columns(2)
                    with pf_c1:
                        new_username = st.text_input("Username *", key="rbac_user_uname", placeholder="e.g. jdoe_ops")
                        new_password = st.text_input("Password (min 6) *", type="password", key="rbac_user_pwd")
                    with pf_c2:
                        new_fullname = st.text_input("Full Name", key="rbac_user_fname", placeholder="e.g. Jane Doe")
                        new_email = st.text_input("Enterprise Email", key="rbac_user_email", placeholder="e.g. jdoe@ets.internal")

                    new_role = st.selectbox("Assign Enterprise Role *", ["Operator", "Viewer", "Auditor", "Admin"], index=0, key="rbac_user_role")

                    submitted = st.form_submit_button("Provision User", type="primary", use_container_width=True)
                    if submitted:
                        if not new_username or not new_username.strip():
                            st.error("Username cannot be blank.")
                        elif len(new_password) < 6:
                            st.error("Password must be at least 6 characters.")
                        else:
                            try:
                                conn_w = get_connection(DB_PATH)
                                create_user(
                                    conn_w,
                                    username=new_username.strip(),
                                    password=new_password,
                                    role=new_role,
                                    full_name=new_fullname.strip(),
                                    email=new_email.strip(),
                                )
                                log_audit_event(
                                    conn_w,
                                    actor=st.session_state.get("active_user", "admin"),
                                    role="Admin",
                                    action="USER_CREATED",
                                    target_entity=f"User: {new_username.strip()}",
                                    details=f"Assigned role {new_role} ({new_fullname.strip() or 'No Name'}).",
                                )
                                conn_w.close()
                                st.success(f"✓ Provisioned user '{new_username.strip()}' as {new_role}!")
                                rerun()
                            except Exception as ex:
                                st.error(f"Failed to create user: {ex}")

            st.markdown("""
            <div style="background:#141619;border:1px solid #2c3235;border-radius:2px;padding:8px 10px;margin-top:10px;">
              <div style="font-size:10px;font-weight:700;color:var(--slate);text-transform:uppercase;letter-spacing:0.04em;margin-bottom:6px;">🛡️ Zero-Trust Role Entitlements &amp; Policy Matrix</div>
              <table style="width:100%;border-collapse:collapse;font-size:10px;line-height:1.4;">
                <tr style="border-bottom:1px solid #22252b;"><td style="padding:3px 0;font-weight:700;color:#f2495c;width:65px;">Admin</td><td style="padding:3px 0;color:var(--slate);">Full root access, user provisioning, global policy enforcement</td></tr>
                <tr style="border-bottom:1px solid #22252b;"><td style="padding:3px 0;font-weight:700;color:#ff9830;">Operator</td><td style="padding:3px 0;color:var(--slate);">State-scoped updates, batch renewals, alert dispatching</td></tr>
                <tr style="border-bottom:1px solid #22252b;"><td style="padding:3px 0;font-weight:700;color:#73bf69;">Auditor</td><td style="padding:3px 0;color:var(--slate);">Read-only audit inspection, compliance verification</td></tr>
                <tr><td style="padding:3px 0;font-weight:700;color:#5794f2;">Viewer</td><td style="padding:3px 0;color:var(--slate);">Read-only dashboards, reporting &amp; telemetry review</td></tr>
              </table>
            </div>
            """, unsafe_allow_html=True)

        with uc2:
            st.markdown(ui.panel_header(f"Active User Directory ({total_users} Accounts)", color="#73bf69", count=f"{admin_count} Admin · {op_count} Op · {audit_count} Aud"), unsafe_allow_html=True)

            role_chips = {
                "Admin": '<span class="alert-chip firing">Admin</span>',
                "Operator": '<span class="alert-chip pending">Operator</span>',
                "Auditor": '<span class="alert-chip ok">Auditor</span>',
                "Viewer": '<span class="alert-chip ok">Viewer</span>',
            }

            user_rows_html = []
            for idx, u in enumerate(users):
                rc = role_chips.get(u["role"], '<span class="alert-chip ok">Viewer</span>')
                created_str = u.get("created_at", "")[:19].replace("T", " ")
                row_bg = "#181b1f" if idx % 2 == 0 else "#141619"
                user_rows_html.append(
                    f"<tr style='background:{row_bg};border-bottom:1px solid #22252b;font-size:11.5px;'>"
                    f"<td style='padding:6px 10px;font-family:var(--mono);font-weight:700;color:var(--ink);'>{u['username']}</td>"
                    f"<td style='padding:6px 10px;color:var(--slate);'>{u.get('full_name') or '—'}</td>"
                    f"<td style='padding:6px 10px;color:var(--slate);font-size:11px;font-family:var(--mono);'>{u.get('email') or '—'}</td>"
                    f"<td style='padding:6px 10px;'>{rc}</td>"
                    f"<td style='padding:6px 10px;font-family:var(--mono);font-size:10.5px;color:var(--mute);'>{created_str}</td>"
                    f"<td style='padding:6px 10px;'><span class='alert-chip ok'>ACTIVE</span></td>"
                    f"</tr>"
                )

            table_html = f"""
            <div style="border:1px solid #2c3235;border-radius:2px;overflow:hidden;background:#181b1f;margin-bottom:10px;max-height:calc(100vh - 280px);min-height:150px;overflow-y:auto;">
              <table style="width:100%;border-collapse:collapse;text-align:left;">
                <thead>
                  <tr style="background:#141619;border-bottom:1px solid #2c3235;font-size:10px;font-weight:700;text-transform:uppercase;color:var(--slate);letter-spacing:0.04em;">
                    <th style="padding:6px 10px;">Username</th>
                    <th style="padding:6px 10px;">Full Name</th>
                    <th style="padding:6px 10px;">Email</th>
                    <th style="padding:6px 10px;">Assigned Role</th>
                    <th style="padding:6px 10px;">Provisioned (UTC)</th>
                    <th style="padding:6px 10px;">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {''.join(user_rows_html)}
                </tbody>
              </table>
            </div>
            """
            st.markdown(table_html, unsafe_allow_html=True)

            # User Role Modification / Account Revocation Controls
            with st.expander("⚙️ Manage Existing Accounts & Revocations", expanded=True):
                if not is_admin():
                    st.markdown(render_access_denied(
                        reason="Updating user roles and revoking enterprise accounts requires Administrator (Admin) privileges. Current session is read-only.",
                        required_role="Admin"
                    ), unsafe_allow_html=True)
                else:
                    del_c1, del_c2 = st.columns([1.5, 1.5])
                    with del_c1:
                        user_list = [u["username"] for u in users if u["username"] != "admin"]
                        if user_list:
                            target_user = st.selectbox("Select Account to Manage", user_list, key="rbac_target_user")
                            new_r = st.selectbox("Change Role", ["Operator", "Viewer", "Auditor", "Admin"], key="rbac_change_role_val")
                            if st.button("Update Role", key="rbac_update_role_btn", use_container_width=True):
                                conn_u = get_connection(DB_PATH)
                                update_user_role(conn_u, target_user, new_r)
                                log_audit_event(
                                    conn_u,
                                    actor=st.session_state.get("active_user", "admin"),
                                    role="Admin",
                                    action="ROLE_MODIFIED",
                                    target_entity=f"User: {target_user}",
                                    details=f"Changed role to {new_r}.",
                                )
                                conn_u.close()
                                st.success(f"✓ Updated {target_user} to {new_r}")
                                rerun()
                        else:
                            st.info("No secondary user accounts provisioned yet.")
                    with del_c2:
                        if user_list:
                            st.markdown("<div style='height:24px;'></div>", unsafe_allow_html=True)
                            if st.button("🗑️ Revoke & Delete Account", key="rbac_delete_user_btn", type="secondary", use_container_width=True):
                                conn_d = get_connection(DB_PATH)
                                delete_user(conn_d, target_user)
                                log_audit_event(
                                    conn_d,
                                    actor=st.session_state.get("active_user", "admin"),
                                    role="Admin",
                                    action="USER_DELETED",
                                    target_entity=f"User: {target_user}",
                                    details=f"Permanently revoked account {target_user}.",
                                )
                                conn_d.close()
                                st.warning(f"Revoked user '{target_user}'.")
                                rerun()

            # Live Security Audit Stream preview
            st.markdown(ui.panel_header("Recent Security Audit Stream", color="#5794f2", count=f"{min(5, len(audit_logs))} Latest Events"), unsafe_allow_html=True)
            stream_rows = []
            for a in audit_logs[:5]:
                chip = role_chips.get(a.get("role", "Admin"), '<span class="alert-chip ok">Viewer</span>')
                ts = str(a.get("timestamp", ""))[:19].replace("T", " ")
                stream_rows.append(
                    f"<tr style='border-bottom:1px solid #22252b;font-size:10.5px;'>"
                    f"<td style='padding:4px 8px;font-family:var(--mono);color:var(--mute);'>{ts}</td>"
                    f"<td style='padding:4px 8px;font-weight:700;color:var(--ink);'>{escape(str(a.get('actor', 'admin')))}</td>"
                    f"<td style='padding:4px 8px;'><span class='alert-chip pending' style='font-size:8.5px;'>{escape(str(a.get('action', '')))}</span></td>"
                    f"<td style='padding:4px 8px;color:var(--slate);font-size:10px;'>{escape(str(a.get('target_entity', '')))}</td>"
                    f"</tr>"
                )
            stream_html = f"""
            <div style="border:1px solid #2c3235;border-radius:2px;overflow:hidden;background:#181b1f;margin-top:4px;">
              <table style="width:100%;border-collapse:collapse;text-align:left;">
                <thead>
                  <tr style="background:#141619;border-bottom:1px solid #2c3235;font-size:9.5px;font-weight:700;text-transform:uppercase;color:var(--slate);">
                    <th style="padding:4px 8px;">Timestamp (UTC)</th>
                    <th style="padding:4px 8px;">Actor</th>
                    <th style="padding:4px 8px;">Action</th>
                    <th style="padding:4px 8px;">Target Entity</th>
                  </tr>
                </thead>
                <tbody>
                  {''.join(stream_rows)}
                </tbody>
              </table>
            </div>
            """
            st.markdown(stream_html, unsafe_allow_html=True)

    with subtab_audit:
        st.markdown(ui.panel_header("Immutable Security Audit Trail", color="#5794f2", count=f"{total_audit_events} Events Recorded"), unsafe_allow_html=True)

        aud_f1, aud_f2, aud_f3 = st.columns([1.5, 1.5, 1.0])
        with aud_f1:
            action_filter = st.selectbox(
                "Filter by Action Category",
                ["ALL", "USER_CREATED", "USER_DELETED", "ROLE_MODIFIED", "EXPIRY_EDITED", "EMAIL_DISPATCHED", "SYSTEM_INITIALIZATION"],
                key="rbac_audit_filter",
            )
        with aud_f2:
            search_query = st.text_input("Search Actor / Target / Details", key="rbac_audit_search", placeholder="Filter events...")
        with aud_f3:
            st.markdown("<div style='height:28px;'></div>", unsafe_allow_html=True)
            conn_csv = get_connection(DB_PATH)
            df_audit_full = pd.read_sql_query("SELECT timestamp, actor, role, action, target_entity, details, ip_address FROM audit_log ORDER BY id DESC", conn_csv)
            conn_csv.close()
            st.markdown(
                ui.csv_download_button(
                    df=df_audit_full,
                    filename=f"ets_security_audit_{date.today().isoformat()}.csv",
                    label="📥 Export Audit CSV",
                    key="rbac_audit_csv_btn",
                ),
                unsafe_allow_html=True,
            )

        filtered_logs = audit_logs
        if action_filter != "ALL":
            filtered_logs = [l for l in filtered_logs if l["action"] == action_filter]
        if search_query and search_query.strip():
            sq = search_query.strip().lower()
            filtered_logs = [
                l for l in filtered_logs
                if sq in str(l.get("actor", "")).lower()
                or sq in str(l.get("target_entity", "")).lower()
                or sq in str(l.get("details", "")).lower()
                or sq in str(l.get("action", "")).lower()
            ]

        action_chips = {
            "SYSTEM_INITIALIZATION": '<span class="alert-chip ok">SYSTEM_INIT</span>',
            "USER_CREATED": '<span class="alert-chip ok">USER_CREATED</span>',
            "USER_DELETED": '<span class="alert-chip firing">USER_DELETED</span>',
            "ROLE_MODIFIED": '<span class="alert-chip pending">ROLE_MODIFIED</span>',
            "EXPIRY_EDITED": '<span class="alert-chip pending">EXPIRY_EDITED</span>',
            "EMAIL_DISPATCHED": '<span class="alert-chip ok">EMAIL_SENT</span>',
        }

        audit_rows_html = []
        for idx, a in enumerate(filtered_logs[:100]):
            chip = action_chips.get(a["action"], f'<span class="alert-chip ok">{a["action"]}</span>')
            ts_str = a.get("timestamp", "")[:19].replace("T", " ")
            row_bg = "#181b1f" if idx % 2 == 0 else "#141619"
            audit_rows_html.append(
                f"<tr style='background:{row_bg};border-bottom:1px solid #22252b;font-size:11px;'>"
                f"<td style='padding:5px 8px;font-family:var(--mono);font-size:10px;color:var(--mute);white-space:nowrap;'>{ts_str}</td>"
                f"<td style='padding:5px 8px;font-weight:700;color:var(--ink);font-family:var(--mono);'>{a.get('actor', 'system')}</td>"
                f"<td style='padding:5px 8px;font-size:10px;color:var(--slate);'>{a.get('role', 'Viewer')}</td>"
                f"<td style='padding:5px 8px;'>{chip}</td>"
                f"<td style='padding:5px 8px;font-weight:600;color:var(--text);font-size:10.5px;'>{a.get('target_entity', '—')}</td>"
                f"<td style='padding:5px 8px;color:var(--slate);font-size:10.5px;max-width:340px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;'>{a.get('details', '')}</td>"
                f"<td style='padding:5px 8px;font-family:var(--mono);font-size:10px;color:var(--mute);'>{a.get('ip_address', '127.0.0.1')}</td>"
                f"</tr>"
            )

        audit_body_content = "".join(audit_rows_html) if audit_rows_html else '<tr><td colspan="7" style="text-align:center;padding:16px;color:var(--mute);">No audit records match the current filter.</td></tr>'
        audit_table_html = f"""
        <div style="border:1px solid #2c3235;border-radius:2px;overflow:hidden;background:#181b1f;max-height:calc(100vh - 280px);min-height:500px;overflow-y:auto;">
          <table style="width:100%;border-collapse:collapse;text-align:left;">
            <thead>
              <tr style="background:#141619;border-bottom:1px solid #2c3235;font-size:9.5px;font-weight:700;text-transform:uppercase;color:var(--slate);letter-spacing:0.04em;position:sticky;top:0;z-index:2;">
                <th style="padding:5px 8px;">Timestamp (UTC)</th>
                <th style="padding:5px 8px;">Actor</th>
                <th style="padding:5px 8px;">Role</th>
                <th style="padding:5px 8px;">Action Category</th>
                <th style="padding:5px 8px;">Target Entity</th>
                <th style="padding:5px 8px;">Audit Details</th>
                <th style="padding:5px 8px;">Source IP</th>
              </tr>
            </thead>
            <tbody>
              {audit_body_content}
            </tbody>
          </table>
        </div>
        """
        st.markdown(audit_table_html, unsafe_allow_html=True)


# ==========================================================================
# Left Toggle Bar (Collapsible Enterprise Navigation Rail)
# ==========================================================================
with st.sidebar:
    active_u = st.session_state.get("active_user", "admin")
    st.markdown(f"""
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
      <button class="ets-nav-item active" data-nav-idx="0" title="Schedule Release Plan">
        <span class="nav-icon" style="font-size:15px;display:flex;align-items:center;justify-content:center;width:20px;flex-shrink:0;">📅</span>
        <span class="nav-label" style="font-size:11.5px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">Schedule Release Plan</span>
      </button>
      <button class="ets-nav-item" data-nav-idx="1" title="Executive Command Center">
        <span class="nav-icon" style="font-size:15px;display:flex;align-items:center;justify-content:center;width:20px;flex-shrink:0;">⚡</span>
        <span class="nav-label" style="font-size:11.5px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">Command Center</span>
      </button>
      <button class="ets-nav-item" data-nav-idx="2" title="Portfolio Matrix & Operations Hub">
        <span class="nav-icon" style="font-size:15px;display:flex;align-items:center;justify-content:center;width:20px;flex-shrink:0;">📊</span>
        <span class="nav-label" style="font-size:11.5px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">Operations Hub</span>
      </button>
      <button class="ets-nav-item" data-nav-idx="3" title="Governance & Alerts">
        <span class="nav-icon" style="font-size:15px;display:flex;align-items:center;justify-content:center;width:20px;flex-shrink:0;">🛡️</span>
        <span class="nav-label" style="font-size:11.5px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">Governance & Alerts</span>
      </button>
      <button class="ets-nav-item" data-nav-idx="4" title="Access Control & Security Audit (RBAC)">
        <span class="nav-icon" style="font-size:15px;display:flex;align-items:center;justify-content:center;width:20px;flex-shrink:0;">🔐</span>
        <span class="nav-label" style="font-size:11.5px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">Access Control (RBAC)</span>
      </button>
      <button class="ets-nav-item" data-nav-idx="5" title="24/7 On-Call Operations Command Hub">
        <span class="nav-icon" style="font-size:15px;display:flex;align-items:center;justify-content:center;width:20px;flex-shrink:0;">🚨</span>
        <span class="nav-label" style="font-size:11.5px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">On-Call Command Hub</span>
      </button>
    </div>
    """, unsafe_allow_html=True)

    if st.button("🚪 Sign Out", key="sidebar_logout_btn", help=f"Sign Out ({active_u})", use_container_width=True):
        conn = get_connection(DB_PATH)
        log_audit_event(
            conn,
            actor=st.session_state.get("active_user", "anonymous"),
            role=st.session_state.get("user_role", "Viewer"),
            action="USER_LOGOUT",
            target_entity="Auth System",
            details="User signed out of Watchtower session.",
            ip_address="127.0.0.1",
        )
        conn.close()
        st.session_state["authenticated"] = False
        st.session_state["active_user"] = None
        st.session_state["user_role"] = None
        st.session_state["user_full_name"] = None
        st.session_state["assigned_state"] = None
        st.rerun()

    st.markdown("""
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
          <span style="color:#f87171;">Critical Items</span>
          <b style="color:#ef4444;font-family:var(--mono);">7</b>
        </div>
      </div>
    </div>
    """, unsafe_allow_html=True)




# --- Global Release & State Scope Synchronization ---
active_scope_state = st.session_state.get("_override_canvas_state")
global_sel = st.session_state.get("global_release_selection")

if active_scope_state in ["NH", "ND", "AK"]:
    records = records[records["state"] == active_scope_state]



tab_releases, tab_overview, tab_operations, tab_governance, tab_rbac, tab_oncall = st.tabs([
    "Schedule Release Plan",
    "Executive Command Center",
    "Portfolio Matrix & Operations Hub",
    "Governance & Alerts",
    "Access Control & Audit (RBAC)",
    "24/7 On-Call Operations Hub",
])

with tab_releases:
    render_release_plan_workspace(DB_PATH)

with tab_overview:
    if active_scope_state in ["NH", "ND", "AK"]:
        canvas("state", active_scope_state, CANVAS_OVERVIEW)
    else:
        canvas("all", None, CANVAS_OVERVIEW)

with tab_operations:
    render_operations_hub(records)

with tab_governance:
    render_governance_center(records)

with tab_rbac:
    render_rbac_workspace()

with tab_oncall:
    render_on_call_workspace(DB_PATH)
