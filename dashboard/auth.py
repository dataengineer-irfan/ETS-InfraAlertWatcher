"""
auth.py — ETS Watchtower Frontend Authorization Helpers
=========================================================
Centralized role-based access control helpers for the Streamlit dashboard.
All authorization decisions flow through this module so they stay consistent
and maintainable.

Roles (from db.VALID_ROLES):
    Admin    — Full access: user provisioning, global policy, all writes
    Operator — State-scoped writes: expiry edits, batch renewals, alert dispatch
    Auditor  — Read-only: audit trail, compliance views (no writes)
    Viewer   — Read-only: dashboards, telemetry, scheduling (no writes)
"""

from __future__ import annotations

from html import escape

import streamlit as st

# ---------------------------------------------------------------------------
# Role constants (mirrors db.VALID_ROLES — keep in sync)
# ---------------------------------------------------------------------------
ROLE_ADMIN = "Admin"
ROLE_OPERATOR = "Operator"
ROLE_AUDITOR = "Auditor"
ROLE_VIEWER = "Viewer"

WRITE_ROLES = (ROLE_ADMIN, ROLE_OPERATOR)
ALL_ROLES = (ROLE_ADMIN, ROLE_OPERATOR, ROLE_AUDITOR, ROLE_VIEWER)


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------

def get_current_user() -> dict:
    """Returns a dict with the authenticated user's session values."""
    return {
        "username": st.session_state.get("active_user", ""),
        "role": st.session_state.get("user_role", ROLE_VIEWER),
        "full_name": st.session_state.get("user_full_name", ""),
        "assigned_state": st.session_state.get("assigned_state"),
        "authenticated": st.session_state.get("authenticated", False),
    }


def is_authenticated() -> bool:
    """Returns True if the current session is authenticated."""
    return bool(st.session_state.get("authenticated", False))


def require_role(allowed_roles: tuple | list) -> bool:
    """
    Returns True if the current user's role is in allowed_roles.
    Does NOT stop the page — caller decides what to render.
    """
    role = st.session_state.get("user_role", ROLE_VIEWER)
    return role in allowed_roles


def is_admin() -> bool:
    return require_role((ROLE_ADMIN,))


def can_write() -> bool:
    """Returns True for Admin and Operator roles (write-enabled roles)."""
    return require_role(WRITE_ROLES)


def check_state_scope(target_state: str | None) -> bool:
    """
    Returns True if the current user is allowed to write to target_state.
    - Admins can write to any state.
    - Operators can only write to their assigned_state.
    - Auditors/Viewers cannot write (should check can_write() first).
    """
    role = st.session_state.get("user_role", ROLE_VIEWER)
    if role == ROLE_ADMIN:
        return True
    if role == ROLE_OPERATOR:
        assigned = st.session_state.get("assigned_state")
        if assigned is None:
            return True  # Operator with no state restriction — can write to all
        return assigned == target_state
    return False


# ---------------------------------------------------------------------------
# UI helper — Access Denied panel
# ---------------------------------------------------------------------------

def render_access_denied(
    reason: str = "You do not have permission to access this section.",
    required_role: str = "Admin",
) -> str:
    """
    Returns an HTML string for a compact, styled 'Access Denied' panel.
    Inject with st.markdown(..., unsafe_allow_html=True).
    """
    user = get_current_user()
    return f"""
<div style="border:1px solid rgba(239,68,68,0.4);border-radius:6px;background:rgba(239,68,68,0.06);
            padding:18px 22px;margin:12px 0;display:flex;align-items:flex-start;gap:14px;">
  <span style="font-size:24px;flex-shrink:0;">🔒</span>
  <div>
    <div style="font-size:13px;font-weight:700;color:#f87171;margin-bottom:4px;">
      Access Restricted
    </div>
    <div style="font-size:12px;color:#94a3b8;line-height:1.6;">
      {escape(reason)}
    </div>
    <div style="margin-top:8px;font-size:11px;color:#64748b;">
      Your role: <span style="color:#f8fafc;font-weight:700;">{escape(user['role'])}</span>
      &nbsp;·&nbsp;
      Required: <span style="color:#f8fafc;font-weight:700;">{escape(required_role)}</span>
    </div>
  </div>
</div>"""


def render_role_badge(role: str) -> str:
    """Returns a small colored HTML badge for a role."""
    colors = {
        ROLE_ADMIN: ("#f2495c", "rgba(242,73,92,0.15)"),
        ROLE_OPERATOR: ("#ff9830", "rgba(255,152,48,0.15)"),
        ROLE_AUDITOR: ("#73bf69", "rgba(115,191,105,0.15)"),
        ROLE_VIEWER: ("#5794f2", "rgba(87,148,242,0.15)"),
    }
    color, bg = colors.get(role, ("#94a3b8", "rgba(148,163,184,0.15)"))
    return (
        f'<span style="display:inline-block;padding:2px 8px;border-radius:3px;'
        f'font-size:10px;font-weight:700;color:{color};background:{bg};'
        f'border:1px solid {color}40;">{escape(role)}</span>'
    )
