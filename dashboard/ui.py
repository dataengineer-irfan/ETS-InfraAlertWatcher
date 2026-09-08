"""
ui.py — design tokens, domain vocabulary, and Streamlit chrome
=============================================================
Two jobs, both about presentation, neither of them about data:

1. It owns the palette and the domain vocabulary - health bands, component
   codes, the plain-language gloss for each term, and the date and duration
   formats. `report.py` imports these rather than restating them, so a colour
   or a threshold is defined once and the canvas, the Manage tab and the
   health logic can never drift apart.

2. It supplies the CSS that makes Streamlit's own furniture - tabs, buttons,
   the data editor - match that palette, and gets Streamlit's default padding
   out of the way so the report canvas has the screen almost to itself.

The palette is built for daylight and for reading at a glance. Every band
colour clears WCAG AA as text on white (4.9:1 or better) *and* accepts white
text at the same ratio, which is why one hex value can serve as both a label
colour and a filled chip. The dark theme this replaced used saturated neons
that did neither once the background went pale.

This module imports neither Streamlit nor the database, so everything in it
can be rendered and asserted on without either.
"""

from __future__ import annotations

from datetime import date, datetime
from html import escape  # re-exported: callers use ui.escape

# --------------------------------------------------------------------------
# Health bands
#
# These thresholds match src/expiry_checker.py, which decides who gets a
# reminder email. The dashboard must never call something urgent that the
# alerts ignore, or the other way round, so the numbers live in both places
# deliberately rather than being loosened for visual effect.
# --------------------------------------------------------------------------
CRITICAL_DAYS = 15
WARNING_DAYS = 30

# Ordered most urgent first: worst_band() and every sort depend on this.
BANDS = ["Expired", "Critical", "Warning", "Healthy"]

# Each band carries a symbol as well as a colour. Colour alone fails about one
# man in twelve, and it also fails anyone printing the report or glancing at it
# on a projector, so the shape is what actually distinguishes the four states
# and the colour reinforces it.
BAND_META = {
    "Expired": {
        "color": "#f2495c", "tint": "rgba(242,73,92,0.18)", "symbol": "●",
        "label": "Expired", "plain": "already past its date",
    },
    "Critical": {
        "color": "#ff9830", "tint": "rgba(255,152,48,0.16)", "symbol": "▲",
        "label": f"Critical ({CRITICAL_DAYS} days or less)",
        "plain": f"expires within {CRITICAL_DAYS} days",
    },
    "Warning": {
        "color": "#ff9830", "tint": "rgba(255,152,48,0.16)", "symbol": "◆",
        "label": f"Warning ({WARNING_DAYS} days or less)",
        "plain": f"expires within {WARNING_DAYS} days",
    },
    "Healthy": {
        "color": "#73bf69", "tint": "rgba(115,191,105,0.16)", "symbol": "✓",
        "label": "Healthy", "plain": "more than 30 days of life left",
    },
}

BAND_COLOR = {band: meta["color"] for band, meta in BAND_META.items()}
BAND_SYMBOL = {band: meta["symbol"] for band, meta in BAND_META.items()}

# --------------------------------------------------------------------------
# Component vocabulary
# --------------------------------------------------------------------------
COMPONENT_CODE = {
    "Crypto Keys & CA Certificates": "CRYPTO",
    "Database Password Expiry": "DBPWD",
    "Software Versions & N-1 Tracking": "SWVER",
    "Upgrade & Patch Tasks": "PATCH",
}

COMPONENT_ICONS = {
    "Crypto Keys & CA Certificates": "🔑",
    "Database Password Expiry": "🛡️",
    "Software Versions & N-1 Tracking": "🏷️",
    "Upgrade & Patch Tasks": "🔧",
    "CRYPTO": "🔑",
    "DBPWD": "🛡️",
    "SWVER": "🏷️",
    "PATCH": "🔧",
}

COMPONENT_BLURB = {
    "Crypto Keys & CA Certificates": "Signing keys and certificate authority certificates",
    "Database Password Expiry": "Service account passwords on each database",
    "Software Versions & N-1 Tracking": "Supported release levels and the version behind",
    "Upgrade & Patch Tasks": "Scheduled upgrade and patching work",
}

ENV_BLURB = {
    "DEV": "Development",
    "SIT": "System integration testing",
    "UAT": "User acceptance testing",
    "MO": "Model office",
    "DR": "Disaster recovery",
    "PROD": "Live production",
    "UNMAPPED": "No environment label in the workbook",
}

# Deployment order, so environment rows read the way an operator thinks about
# them rather than alphabetically.
ENV_ORDER = ["DEV", "SIT", "UAT", "MO", "DR", "PROD", "UNMAPPED"]

# --------------------------------------------------------------------------
# SLA & Business Impact Tier Weights
# --------------------------------------------------------------------------
ENV_TIER_WEIGHT = {
    "PROD": {"tier": "Tier 1", "desc": "Mission Critical Live SLA", "weight": 5.0, "color": "#EF4444", "badge": "T1-PROD"},
    "DR":   {"tier": "Tier 2", "desc": "Disaster Recovery Standby", "weight": 3.0, "color": "#F97316", "badge": "T2-DR"},
    "MO":   {"tier": "Tier 3", "desc": "Model Office Pre-Prod", "weight": 2.0, "color": "#F59E0B", "badge": "T3-MO"},
    "UAT":  {"tier": "Tier 3", "desc": "User Acceptance Test", "weight": 2.0, "color": "#F59E0B", "badge": "T3-UAT"},
    "SIT":  {"tier": "Tier 3", "desc": "System Integration Test", "weight": 2.0, "color": "#F59E0B", "badge": "T3-SIT"},
    "DEV":  {"tier": "Tier 4", "desc": "Development Sandbox", "weight": 1.0, "color": "#94A3B8", "badge": "T4-DEV"},
    "UNMAPPED": {"tier": "Tier 4", "desc": "Unmapped Target", "weight": 1.0, "color": "#64748B", "badge": "T4-OTHER"},
}


def env_tier(env_label: str) -> dict:
    """Return tier metadata dict for environment label."""
    norm = str(env_label or "UNMAPPED").upper().strip()
    return ENV_TIER_WEIGHT.get(norm, ENV_TIER_WEIGHT["UNMAPPED"])


def sla_badge(env_label: str) -> str:
    """Return compact HTML pill for SLA tier."""
    t = env_tier(env_label)
    return (
        f'<span style="display:inline-block;padding:1px 5px;border-radius:3px;'
        f'font-size:8.5px;font-weight:700;font-family:var(--mono);'
        f'background:{t["color"]}22;color:{t["color"]};border:1px solid {t["color"]}55;">'
        f'{t["badge"]}</span>'
    )

STATES = ["AK", "NH", "ND"]

COMPONENT_ORDER = list(COMPONENT_CODE)

# --------------------------------------------------------------------------
# Multi-Team vocabulary
# --------------------------------------------------------------------------
TEAMS = ["Cognos", "Informatica", "Letters", "App Server", "Core"]

TEAM_META = {
    "Cognos": {"color": "#818CF8", "icon": "📊", "lead": "BI & Analytics"},
    "Informatica": {"color": "#FB923C", "icon": "🔄", "lead": "ETL & Integration"},
    "Letters": {"color": "#34D399", "icon": "✉️", "lead": "Correspondence & Print"},
    "App Server": {"color": "#FBBF24", "icon": "⚙️", "lead": "Java Containers & JVM"},
    "Core": {"color": "#38BDF8", "icon": "🛡️", "lead": "Database & Infrastructure"},
}


def team_of(schema_name: str, component: str = "", env_no: str | int = "", team: str | None = None) -> str:
    """Classify schema/environment into team owner with direct team resolution."""
    if team and str(team).strip() in TEAMS:
        return str(team).strip()
    sn = (schema_name or "").upper()
    if any(k in sn for k in ["CGNS", "COGNOS", "ORR", "MMIS", "COTS_REP", "COTS_CGNS"]):
        return "Cognos"
    if any(k in sn for k in ["INFA", "ISIM", "EMAR", "ETL"]):
        return "Informatica"
    if any(k in sn for k in ["LTRS", "FADS", "OMNI", "LETTER", "PRINT"]):
        return "Letters"
    if any(k in sn for k in ["APPSRV", "WAS", "TC", "JBOSS", "JVM", "APPSERVER"]):
        return "App Server"
    if any(k in sn for k in ["CORE", "DBA", "INFRA", "ORACLE"]):
        return "Core"
    return "Core"

# --------------------------------------------------------------------------
# Colour tokens shared by the Streamlit chrome and the canvas (Deep Slate)
# --------------------------------------------------------------------------
TOKENS = {
    "paper": "#111217",     # Grafana page background
    "card": "#181b1f",      # Panel surface - one shade lighter than page
    "sunk": "#141619",      # Dark inset / sub-bars / wells
    "rule": "#2c3235",      # Panel 1px borders
    "rule_soft": "#212429", # Button & input fill / secondary lines
    "ink": "#d8d9da",       # Primary text
    "slate": "#9fa7b3",     # Secondary labels
    "mute": "#6e7681",      # Faint / disabled text
    "accent": "#ff780a",    # Grafana Orange — marks interactive elements only
    "accent_tint": "rgba(255,120,10,0.14)",
    "accent_line": "rgba(255,120,10,0.45)",
}


# --------------------------------------------------------------------------
# Domain helpers
# --------------------------------------------------------------------------
def health_of(days_left) -> str:
    """Days remaining -> band name. The same arithmetic the emails use."""
    try:
        d = int(days_left)
    except (TypeError, ValueError):
        return "Healthy"
    if d < 0:
        return "Expired"
    if d <= CRITICAL_DAYS:
        return "Critical"
    if d <= WARNING_DAYS:
        return "Warning"
    return "Healthy"


def worst_band(bands) -> str:
    """The most urgent band present. A group is only as healthy as its worst member."""
    present = set(bands)
    for band in BANDS:
        if band in present:
            return band
    return "Healthy"


def fmt_date(value) -> str:
    """Any date-ish value -> '16 Oct 2026'. Unambiguous in any locale."""
    if value is None or value == "":
        return "--"
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value[:10]).date()
        except ValueError:
            return value
    if isinstance(value, datetime):
        value = value.date()
    if not isinstance(value, date):
        try:
            value = value.date()          # pandas Timestamp
        except AttributeError:
            return str(value)
    return f"{value.day:02d} {value.strftime('%b')} {value.year}"


def _span(days: int) -> str:
    """A positive day count as a compact duration.

    Precision drops as the horizon gets further away, because nobody plans
    around '1,093 days'. Inside three months the exact day count matters, so
    that is where it is kept.
    """
    if days < 90:
        return f"{days}d"
    if days < 730:
        return f"{days // 30}mo"
    return f"{days / 365:.1f}yr"


def fmt_days(days) -> str:
    """
    Canonical enterprise duration format across the entire application:
    - Exact days under 60 days: '14d left', '0d left', '1d overdue'
    - 'Xm Yd' format at 60+ days: '6m 5d left', '10m 2d overdue', '69m 8d overdue'
    """
    if days is None:
        return "--"
    try:
        d = int(days)
    except (TypeError, ValueError):
        return "--"
    if d < 0:
        n = -d
        if n < 60:
            return f"{n}d overdue"
        m, rd = divmod(n, 30)
        return f"{m}m {rd}d overdue" if rd else f"{m}m 0d overdue"
    if d < 60:
        return f"{d}d left"
    m, rd = divmod(d, 30)
    return f"{m}m {rd}d left" if rd else f"{m}m 0d left"


def fmt_heatmap_time(days) -> str:
    """Alias for backwards compatibility; delegates to canonical fmt_days."""
    return fmt_days(days)


def _span_long(days: int) -> str:
    """The same duration as a phrase."""
    if days == 1:
        return "1 day"
    if days < 60:
        return f"{days} days"
    months = round(days / 30.44)
    return f"about {months} months" if months < 24 else f"about {days / 365:.1f} years"


def fmt_days_long(days) -> str:
    """For tooltips and screen readers, where a phrase reads better than 23d."""
    if days is None:
        return "no date"
    try:
        d = int(days)
    except (TypeError, ValueError):
        return "no date"
    if d < 0:
        return f"{_span_long(-d)} overdue"
    if d == 0:
        return "expires today"
    return f"{_span_long(d)} left"


# --------------------------------------------------------------------------
# Streamlit chrome
# --------------------------------------------------------------------------
def css() -> str:
    """
    Restyle Streamlit's own furniture to match the report, and reclaim the
    vertical space it spends by default.

    The height budget is the reason most of this exists. Streamlit ships
    roughly 6rem of padding above the first element and caps the content
    width; the canvas needs neither. What is left is a 34px tab bar and a few
    pixels of gap, which is the entire cost of navigation.
    """
    t = TOKENS
    return f"""
<style>
:root {{
  color-scheme: dark;
  --bg: {t['paper']};
  --panel: {t['card']};
  --paper: {t['paper']};
  --card: {t['card']};
  --sunk: {t['sunk']};
  --rule: {t['rule']};
  --rule-soft: {t['rule_soft']};
  --border: {t['rule']};
  --ink: {t['ink']};
  --slate: {t['slate']};
  --mute: {t['mute']};
  --text: {t['ink']};
  --text-sec: {t['slate']};
  --text-faint: {t['mute']};
  --orange: {t['accent']};
  --accent: {t['accent']};
  --accent-tint: {t['accent_tint']};
  --accent-line: {t['accent_line']};
  --expired: {BAND_COLOR['Expired']};
  --critical: {BAND_COLOR['Critical']};
  --warning: {BAND_COLOR['Warning']};
  --healthy: {BAND_COLOR['Healthy']};
  --green: {BAND_COLOR['Healthy']};
  --green-dim: rgba(115,191,105,0.16);
  --yellow: {BAND_COLOR['Critical']};
  --yellow-dim: rgba(255,152,48,0.16);
  --red: {BAND_COLOR['Expired']};
  --red-dim: rgba(242,73,92,0.18);
  --ui: 'Helvetica Neue', Helvetica, Arial, system-ui, sans-serif;
  --mono: 'Helvetica Neue', Helvetica, Arial, system-ui, sans-serif;
}}

* {{
  box-sizing: border-box;
  border-radius: 2px;
}}

.stApp, body {{
  background: var(--bg);
  color: var(--text);
  font-family: var(--ui);
  font-size: 13px;
  line-height: 1.35;
}}

/* Zero-scroll viewport layout */
.block-container {{ max-width:none !important; padding:.3rem .6rem 0 !important; }}
#MainMenu, footer, header[data-testid="stHeader"] {{ visibility:hidden; height:0; }}
[data-testid="stToolbar"], div[data-testid="stDialog"], div[role="dialog"], [data-testid="stToast"], [data-testid="stNotification"], [data-testid="stDecoration"] {{ display:none !important; }}
[data-testid="stVerticalBlock"] {{ gap:.5rem; }}
[data-testid="stVerticalBlockBorderWrapper"] {{ background:transparent; }}
iframe {{ display:block; border:0; }}

h1, h2, h3, h4, h5, h6 {{
  font-family: var(--ui);
  color: var(--text) !important;
  letter-spacing: -.01em;
  font-weight: 600;
}}
code, kbd, .mono, .num {{
  font-family: var(--ui) !important;
  font-variant-numeric: tabular-nums !important;
}}

/* Custom scrollbars */
::-webkit-scrollbar {{ width:6px; height:6px; }}
::-webkit-scrollbar-track {{ background:var(--bg); }}
::-webkit-scrollbar-thumb {{ background:var(--rule); border-radius:2px; }}
::-webkit-scrollbar-thumb:hover {{ background:var(--mute); }}

/* ---- primary navigation tabs: hidden since primary navigation is in Left Panel Drawer ---------- */
[data-testid="stMainBlockContainer"] > [data-testid="stVerticalBlock"] > [data-testid="stTabs"] > div > [role="tablist"],
[data-baseweb="tab-list"] {{
  height: 0 !important;
  min-height: 0 !important;
  max-height: 0 !important;
  opacity: 0 !important;
  overflow: hidden !important;
  margin: 0 !important;
  padding: 0 !important;
  border: none !important;
  pointer-events: none !important;
  visibility: hidden !important;
}}
[data-testid="stMainBlockContainer"] > [data-testid="stVerticalBlock"] > [data-testid="stTabs"] > div > [role="tablist"] * {{
  height: 0 !important;
  min-height: 0 !important;
  max-height: 0 !important;
  padding: 0 !important;
  margin: 0 !important;
  border: none !important;
  overflow: hidden !important;
}}
[data-baseweb="tab-highlight"], [data-baseweb="tab-border"] {{ display:none !important; }}
[data-baseweb="tab-panel"],
[data-testid="stMainBlockContainer"] > [data-testid="stVerticalBlock"] > [data-testid="stTabs"] > div > [role="tabpanel"],
[data-testid="stMainBlockContainer"] > [data-testid="stVerticalBlock"] > [data-testid="stTabs"] > div > [data-testid="stTabPanel"] {{
  padding: 0 !important;
  margin: 0 !important;
}}

/* ---- VS Code Style Permanent Slim Navigation Rail (48px -> 260px) ---- */
[data-testid="stSidebar"] {{
  position: fixed !important;
  top: 0 !important;
  left: 0 !important;
  height: 100vh !important;
  z-index: 1000 !important;
  background: #111217 !important;
  border-right: 1px solid #2c3235 !important;
  overflow-x: hidden !important;
  transform: none !important;
  transition: width 0.22s cubic-bezier(0.16, 1, 0.3, 1) !important;
  box-shadow: none !important;
}}

[data-testid="stSidebar"] [data-testid="stSidebarHeader"],
[data-testid="stSidebarHeader"],
.st-emotion-cache-10p9htt {{
  display: none !important;
  height: 0 !important;
  min-height: 0 !important;
  padding: 0 !important;
  margin: 0 !important;
}}

[data-testid="stSidebar"] [data-testid="stSidebarContent"],
[data-testid="stSidebar"] [data-testid="stSidebarUserContent"] {{
  padding: 6px 4px !important;
}}

/* Collapsed state: 48px slim icon rail */
[data-testid="stSidebar"][data-rail-state="collapsed"],
[data-testid="stSidebar"]:not([data-rail-state="expanded"]) {{
  width: 48px !important;
  min-width: 48px !important;
  max-width: 48px !important;
}}

[data-testid="stSidebar"][data-rail-state="collapsed"] [data-testid="stSidebarContent"],
[data-testid="stSidebar"]:not([data-rail-state="expanded"]) [data-testid="stSidebarContent"] {{
  overflow: hidden !important;
  scrollbar-width: none !important;
}}
[data-testid="stSidebar"][data-rail-state="collapsed"] [data-testid="stSidebarContent"]::-webkit-scrollbar,
[data-testid="stSidebar"]:not([data-rail-state="expanded"]) [data-testid="stSidebarContent"]::-webkit-scrollbar {{
  display: none !important;
  width: 0 !important;
}}

/* Expanded state: 260px full navigation rail */
[data-testid="stSidebar"][data-rail-state="expanded"] {{
  width: 260px !important;
  min-width: 260px !important;
  max-width: 260px !important;
  box-shadow: none !important;
}}

/* In collapsed (48px) state: hide labels, brand text, close button and telemetry */
[data-testid="stSidebar"][data-rail-state="collapsed"] .rail-brand-text,
[data-testid="stSidebar"]:not([data-rail-state="expanded"]) .rail-brand-text,
[data-testid="stSidebar"][data-rail-state="collapsed"] .rail-close-btn,
[data-testid="stSidebar"]:not([data-rail-state="expanded"]) .rail-close-btn,
[data-testid="stSidebar"][data-rail-state="collapsed"] .rail-section-label,
[data-testid="stSidebar"]:not([data-rail-state="expanded"]) .rail-section-label,
[data-testid="stSidebar"][data-rail-state="collapsed"] .nav-label,
[data-testid="stSidebar"]:not([data-rail-state="expanded"]) .nav-label,
[data-testid="stSidebar"][data-rail-state="collapsed"] .nav-telemetry,
[data-testid="stSidebar"]:not([data-rail-state="expanded"]) .nav-telemetry {{
  display: none !important;
}}

/* In collapsed (48px) state: center toggle button and nav icons */
[data-testid="stSidebar"][data-rail-state="collapsed"] .rail-header,
[data-testid="stSidebar"]:not([data-rail-state="expanded"]) .rail-header {{
  justify-content: center !important;
  padding: 4px 0 8px !important;
}}

[data-testid="stSidebar"][data-rail-state="collapsed"] .rail-brand-group,
[data-testid="stSidebar"]:not([data-rail-state="expanded"]) .rail-brand-group {{
  width: 100% !important;
  justify-content: center !important;
}}

[data-testid="stSidebar"][data-rail-state="collapsed"] .ets-nav-item,
[data-testid="stSidebar"]:not([data-rail-state="expanded"]) .ets-nav-item {{
  width: 36px !important;
  height: 36px !important;
  padding: 0 !important;
  margin: 0 auto !important;
  justify-content: center !important;
}}

/* In expanded (260px) state: full width nav buttons */
[data-testid="stSidebar"][data-rail-state="expanded"] .ets-nav-item {{
  width: 100% !important;
  padding: 8px 10px !important;
  justify-content: flex-start !important;
}}

/* Shared styling for .ets-nav-item */
.ets-nav-item {{
  display: flex !important;
  align-items: center !important;
  gap: 10px !important;
  background: #181b1f !important;
  border: 1px solid #2c3235 !important;
  border-radius: 2px !important;
  color: #9fa7b3 !important;
  cursor: pointer !important;
  transition: all 0.1s ease !important;
  box-shadow: none !important;
}}
.ets-nav-item:hover {{
  background: #212429 !important;
  border-color: #ff780a !important;
  color: #ff780a !important;
}}
.ets-nav-item.active {{
  background: rgba(255,120,10,0.14) !important;
  border-color: #ff780a !important;
  color: #d8d9da !important;
  box-shadow: none !important;
}}

/* Dynamic sibling push for main dashboard canvas */
[data-testid="stSidebar"][data-rail-state="collapsed"] + div,
[data-testid="stSidebar"]:not([data-rail-state="expanded"]) + div,
[data-testid="stSidebar"][data-rail-state="collapsed"] ~ div,
[data-testid="stSidebar"]:not([data-rail-state="expanded"]) ~ div {{
  margin-left: 48px !important;
  width: calc(100vw - 48px) !important;
  max-width: calc(100vw - 48px) !important;
  transition: margin-left 0.22s cubic-bezier(0.16, 1, 0.3, 1), width 0.22s cubic-bezier(0.16, 1, 0.3, 1) !important;
}}

[data-testid="stSidebar"][data-rail-state="expanded"] + div,
[data-testid="stSidebar"][data-rail-state="expanded"] ~ div {{
  margin-left: 260px !important;
  width: calc(100vw - 260px) !important;
  max-width: calc(100vw - 260px) !important;
  transition: margin-left 0.22s cubic-bezier(0.16, 1, 0.3, 1), width 0.22s cubic-bezier(0.16, 1, 0.3, 1) !important;
}}

/* Hide Streamlit default chevron collapse and expand controls */
[data-testid="stSidebarCollapseButton"],
[data-testid="stExpandSidebarButton"] {{
  display: none !important;
}}
[data-testid="stSidebar"] [data-testid="stRadio"] > div {{
  gap: 5px !important;
}}
[data-testid="stSidebar"] [data-testid="stRadio"] label {{
  background: #181b1f !important;
  border: 1px solid #2c3235 !important;
  border-radius: 2px !important;
  padding: 6px 10px !important;
  transition: all 0.1s ease !important;
  cursor: pointer !important;
  width: 100% !important;
  box-shadow: none !important;
}}
[data-testid="stSidebar"] [data-testid="stRadio"] label:hover {{
  background: #212429 !important;
  border-color: #ff780a !important;
}}
[data-testid="stSidebar"] [data-testid="stRadio"] label:has(input:checked) {{
  background: rgba(255,120,10,0.14) !important;
  border-color: #ff780a !important;
  box-shadow: none !important;
}}

/* ---- Flat buttons: 2px corners, #212429 fill, 1px #2c3235 border ---- */
.stButton > button, [data-testid="stDownloadButton"] > button {{
  width: 100%;
  background: #212429 !important;
  color: #d8d9da !important;
  border: 1px solid #2c3235 !important;
  border-radius: 2px !important;
  padding: .35rem .6rem;
  min-height: 28px;
  font-family: var(--ui) !important;
  font-size: 11.5px;
  font-weight: 500;
  white-space: nowrap !important;
  box-shadow: none !important;
  transition: all .1s ease;
}}
.stButton > button:hover, [data-testid="stDownloadButton"] > button:hover {{
  border-color: var(--accent) !important;
  background: rgba(255,120,10,0.12) !important;
  color: var(--accent) !important;
  box-shadow: none !important;
}}
.stButton > button:focus-visible, [data-testid="stDownloadButton"] > button:focus-visible {{
  outline: 1px solid var(--accent);
  outline-offset: 1px;
}}
.stButton > button[kind="primary"]:not(:disabled) {{
  background: var(--accent) !important;
  border: 1px solid var(--accent) !important;
  color: #111217 !important;
  font-weight: 700 !important;
  box-shadow: none !important;
}}
.stButton > button[kind="primary"]:not(:disabled):hover {{
  background: #ff8e26 !important;
  border-color: #ff8e26 !important;
  color: #111217 !important;
  box-shadow: none !important;
}}
.stButton > button:disabled,
.stButton > button[kind="primary"]:disabled,
.stButton > button[disabled] {{
  background: #181b1f !important;
  border: 1px solid #2c3235 !important;
  color: #6e7681 !important;
  box-shadow: none !important;
  cursor: not-allowed !important;
  opacity: 0.45 !important;
}}

/* ---- state chooser ----------------------------------------------------- */
.pickline {{
  display:flex; align-items:center; gap:8px; padding:2px 0 3px; margin-bottom:2px;
}}
.pickline .h {{ font-size:12px; font-weight:600; color:#d8d9da !important; }}
.pickline .p {{ font-size:11px; color:#9fa7b3 !important; }}
.statebar {{ display:flex; align-items:center; gap:8px; }}

/* ---- Inputs, Selectors, Popovers & Expanders (2px radius, #212429) ---- */
[data-testid="stExpander"] {{
  background: var(--card) !important;
  border: 1px solid var(--rule) !important;
  border-radius: 2px !important;
  box-shadow: none !important;
  margin-bottom: 6px !important;
}}
[data-testid="stExpander"] details {{
  background: var(--card) !important;
  color: var(--ink) !important;
}}
[data-testid="stExpander"] summary {{
  background: var(--card) !important;
  color: var(--ink) !important;
  font-family: var(--ui);
  font-size: 12px;
  font-weight: 500;
  border-radius: 2px;
  padding: 6px 10px !important;
}}
[data-testid="stExpander"] summary:hover {{
  background: var(--sunk) !important;
  color: var(--accent) !important;
}}
[data-testid="stExpander"] details[open] summary {{
  border-bottom: 1px solid var(--rule-soft) !important;
}}
[data-testid="stExpander"] [data-testid="stExpanderDetails"] {{
  background: var(--card) !important;
  padding: 8px 10px !important;
}}

[data-testid="stDataFrame"], [data-testid="stDataEditor"] {{
  border: 1px solid var(--rule) !important;
  border-radius: 2px !important;
  overflow: hidden !important;
  background: var(--card) !important;
  box-shadow: none !important;
}}
[data-testid="stDataEditor"] canvas {{
  border-radius: 2px !important;
}}
.stTextInput input, .stDateInput input, .stNumberInput input,
div[data-baseweb="select"] > div {{
  background: #212429 !important;
  border: 1px solid var(--rule) !important;
  color: var(--ink) !important;
  font-family: var(--ui) !important;
  font-size: 12px;
  border-radius: 2px !important;
  box-shadow: none !important;
}}
div[data-baseweb="popover"], ul[role="listbox"] {{
  background: var(--card) !important;
  border: 1px solid var(--rule) !important;
  color: var(--ink) !important;
  border-radius: 2px !important;
  box-shadow: none !important;
}}
div[data-testid="stPopover"] > button, button[data-testid="stPopoverButton"], [data-testid="stPopoverButton"] {{
  background: #212429 !important;
  color: #d8d9da !important;
  border: 1px solid #2c3235 !important;
  border-radius: 2px !important;
  padding: .35rem .6rem !important;
  min-height: 28px !important;
  font-family: var(--ui) !important;
  font-size: 11.5px !important;
  font-weight: 500 !important;
  box-shadow: none !important;
  width: 100% !important;
}}
div[data-testid="stPopover"] > button:hover, button[data-testid="stPopoverButton"]:hover, [data-testid="stPopoverButton"]:hover {{
  border-color: var(--accent) !important;
  background: rgba(255,120,10,0.12) !important;
  color: var(--accent) !important;
}}
li[role="option"] {{
  background: var(--card) !important;
  color: var(--ink) !important;
  font-family: var(--ui);
  font-size: 12px;
}}
li[role="option"]:hover, li[aria-selected="true"] {{
  background: rgba(255,120,10,0.15) !important;
  color: var(--accent) !important;
}}
.stTextInput input:focus, div[data-baseweb="select"] > div:focus-within {{
  border-color: var(--accent) !important;
  box-shadow: none !important;
}}

[data-testid="stTextInput"], [data-testid="stSelectbox"], [data-testid="stMultiSelect"] {{
  margin: 0 !important;
}}
[data-testid="stTextInput"] input,
[data-testid="stSelectbox"] div[data-baseweb="select"] > div,
[data-testid="stMultiSelect"] div[data-baseweb="select"] > div {{
  min-height: 28px !important;
}}
[data-testid="stMultiSelect"] div[data-baseweb="select"] > div {{
  background: #212429;
  border-color: var(--rule);
}}
[data-testid="stMultiSelect"] span[data-baseweb="tag"] {{
  background: rgba(255,120,10,0.15);
  border: 1px solid rgba(255,120,10,0.4);
  color: var(--accent);
  border-radius: 2px;
  font-family: var(--ui);
  font-size: 10.5px;
}}
[data-testid="stMetricValue"] {{
  font-family: var(--ui);
  font-variant-numeric: tabular-nums;
  color: var(--ink);
}}
.stAlert {{
  border-radius: 2px;
  background: var(--card);
  border: 1px solid var(--rule);
  color: var(--ink);
  box-shadow: none !important;
}}

/* ---- Enterprise components -------------------------------------------- */
.note {{ font-size:12px; color:var(--slate); line-height:1.45; margin:4px 0 8px; }}
.note b {{ color:var(--ink); font-weight:600; }}
.eyebrow {{
  font-size:10px; font-weight:700; letter-spacing:.08em; text-transform:uppercase;
  color:var(--slate); padding:4px 0 4px; display:block; margin-top:8px; margin-bottom:4px;
}}
.pill {{
  display:inline-flex; align-items:center; gap:5px; font-size:10.5px; font-weight:700;
  padding:2px 8px; border-radius:2px; white-space:nowrap; border:1px solid transparent;
}}
.pill .sym {{ font-size:10px; line-height:1; font-weight:700; }}
.card {{
  background:var(--card); border:1px solid var(--rule); border-radius:2px;
  padding:10px 12px; box-shadow:none !important;
}}
.void {{
  background:var(--card); border:1px dashed var(--rule); border-radius:2px;
  padding:14px 16px; text-align:center;
}}
.void .h {{ font-size:13px; font-weight:600; color:var(--ink); }}
.void .p {{ font-size:11.5px; color:var(--slate); margin-top:3px; }}

.tblx {{ width:100%; border-collapse:collapse; font-size:12px; }}
.tblx th {{
  font-size:10px; font-weight:600; letter-spacing:.06em;
  text-transform:uppercase; color:var(--slate); padding:6px 8px;
  border-bottom:1px solid var(--rule); background:var(--card);
}}
.tblx td {{
  padding:6px 8px; border-bottom:1px solid var(--rule-soft); white-space:nowrap; color:var(--ink);
  font-variant-numeric:tabular-nums;
}}
.tblx tr:hover td {{ background:rgba(255,120,10,0.05); }}
.tblx td.m {{ font-variant-numeric:tabular-nums; }}
.tblx td.c {{ text-align:center; }}
.tblx td.r {{ text-align:right; }}
.tblx td.sym {{ width:1%; padding-right:4px; font-weight:700; text-align:center; }}
.tblx td.was {{ color:var(--mute); text-decoration:line-through; }}
.tblx td.now {{ color:var(--ink); font-weight:600; }}

/* Master-detail & Navigation Panels */
.panel {{
  background: var(--card);
  border: 1px solid var(--rule);
  border-radius: 2px;
  position: relative;
  overflow: hidden;
  box-shadow: none !important;
}}
.panel-head {{
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6px 10px;
  border-bottom: 1px solid var(--rule);
}}
.panel-title {{
  font-size: 12px;
  color: var(--slate);
  font-weight: 500;
}}
.panel-menu {{
  opacity: 0;
  color: var(--mute);
  font-size: 13px;
  cursor: pointer;
  transition: opacity .1s;
}}
.panel:hover .panel-menu {{
  opacity: 1;
}}

/* Stat tiles with full background tints */
.stat-row {{
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 8px;
  margin-bottom: 10px;
}}
.stat-panel {{
  padding: 12px 14px;
  min-height: 104px;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  border-radius: 2px;
  border: 1px solid var(--rule);
  box-shadow: none !important;
}}
.stat-fill-red {{
  background: linear-gradient(180deg, rgba(242,73,92,0.20), rgba(242,73,92,0.05)) !important;
}}
.stat-fill-yellow {{
  background: linear-gradient(180deg, rgba(255,152,48,0.18), rgba(255,152,48,0.04)) !important;
}}
.stat-fill-green {{
  background: linear-gradient(180deg, rgba(115,191,105,0.14), rgba(115,191,105,0.03)) !important;
}}
.stat-fill-neutral {{
  background: var(--card) !important;
}}
.stat-label {{
  font-size: 11px;
  color: var(--slate);
  text-transform: uppercase;
  letter-spacing: .02em;
  font-weight: 500;
}}
.stat-val {{
  font-size: 30px;
  font-weight: 600;
  line-height: 1;
  margin-top: 6px;
  font-variant-numeric: tabular-nums;
}}
.stat-val.red {{ color: var(--expired); }}
.stat-val.yellow {{ color: var(--critical); }}
.stat-val.green {{ color: var(--healthy); }}
.stat-val.white {{ color: var(--ink); }}
.stat-sub {{
  font-size: 10.5px;
  color: var(--mute);
  margin-top: 6px;
}}
.stat-action {{
  margin-top: 8px;
  background: #212429;
  border: 1px solid var(--rule);
  border-radius: 2px;
  padding: 6px 0;
  text-align: center;
  font-size: 11.5px;
  color: var(--slate);
  cursor: pointer;
  transition: all .1s;
}}
.stat-action.active {{
  background: var(--accent);
  color: #111217;
  font-weight: 700;
  border-color: transparent;
}}

/* Proportional Component Severity Distribution */
.dist-body {{
  padding: 8px 12px 12px;
}}
.dist-row {{
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 9px;
}}
.dist-row:last-child {{
  margin-bottom: 0;
}}
.dist-name {{
  width: 58px;
  font-size: 11.5px;
  color: var(--slate);
  flex-shrink: 0;
  font-weight: 600;
}}
.dist-track {{
  flex: 1;
  height: 11px;
  background: #212429;
  border-radius: 2px;
  overflow: hidden;
  display: flex;
}}
.dist-num {{
  font-size: 11px;
  color: var(--mute);
  width: 90px;
  text-align: right;
  flex-shrink: 0;
  font-variant-numeric: tabular-nums;
}}

/* Entity Header & Fact Grid */
.entity-head {{
  padding: 12px 14px;
  border-left: 3px solid var(--expired);
  background: linear-gradient(120deg, rgba(242,73,92,0.08), transparent 60%);
  border-radius: 2px;
}}
.entity-crumb {{
  font-size: 10.5px;
  color: var(--mute);
  margin-bottom: 5px;
  display: flex;
  justify-content: space-between;
  align-items: center;
}}
.status-pill {{
  background: var(--red-dim);
  color: #ff8a95;
  font-weight: 700;
  padding: 2px 8px;
  border-radius: 2px;
  font-size: 10px;
}}
.entity-name {{
  font-size: 16px;
  font-weight: 700;
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--ink);
}}
.env-tag {{
  font-size: 10px;
  background: #212429;
  color: var(--slate);
  border: 1px solid var(--rule);
  padding: 2px 7px;
  border-radius: 2px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}}
.quickrange {{
  display: flex;
  gap: 8px;
  padding: 10px 14px;
  border-top: 1px solid var(--rule);
}}
.qr-btn {{
  flex: 1;
  background: #212429;
  border: 1px solid var(--rule);
  border-radius: 2px;
  padding: 7px;
  font-size: 11.5px;
  color: var(--slate);
  text-align: center;
  cursor: pointer;
}}
.qr-btn:hover {{
  border-color: var(--accent);
  color: var(--accent);
}}
.qr-btn.custom {{
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  color: var(--ink);
}}
.entity-tabs {{
  display: flex;
  gap: 20px;
  padding: 0 14px;
  border-top: 1px solid var(--rule);
}}
.etab {{
  font-size: 12px;
  color: var(--mute);
  padding: 8px 0;
  cursor: pointer;
  border-bottom: 2px solid transparent;
}}
.etab.active {{
  color: var(--ink);
  border-color: var(--accent);
  font-weight: 600;
}}
.fact-grid {{
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
  padding: 12px 14px;
}}
.fact-panel {{
  background: #141619;
  border: 1px solid var(--rule);
  border-radius: 2px;
  padding: 11px 13px;
}}
.fact-row {{
  display: flex;
  justify-content: space-between;
  padding: 5px 0;
  font-size: 12px;
  border-bottom: 1px solid #1d2024;
}}
.fact-row:last-child {{
  border-bottom: none;
}}
.fact-key {{
  color: var(--mute);
}}
.fact-val {{
  text-align: right;
  font-variant-numeric: tabular-nums;
}}
.fact-val.crit {{
  color: var(--expired);
  font-weight: 700;
}}
.fact-hint {{
  font-size: 10.5px;
  color: var(--mute);
  margin-top: 4px;
  line-height: 1.5;
}}
.raw-toggle {{
  margin: 0 14px 14px;
  padding: 8px 12px;
  background: #141619;
  border: 1px solid var(--rule);
  border-radius: 2px;
  font-size: 11.5px;
  color: var(--slate);
  cursor: pointer;
}}

/* Scope line */
.scope-line {{
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 12px;
  border: 1px solid var(--rule);
  border-radius: 2px;
  background: var(--card);
  margin-bottom: 10px;
  font-size: 12px;
}}
.scope-label {{
  color: var(--mute);
  text-transform: uppercase;
  font-size: 10.5px;
  letter-spacing: .03em;
  font-weight: 600;
}}
.scope-val {{
  font-weight: 600;
  color: var(--ink);
}}
.scope-muted {{
  color: var(--mute);
}}
.live-dot {{
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--healthy);
  display: inline-block;
}}
.reset-btn {{
  background: #212429;
  border: 1px solid var(--rule);
  color: var(--slate);
  border-radius: 2px;
  padding: 5px 10px;
  font-size: 11px;
  cursor: pointer;
}}
.reset-btn:disabled {{
  opacity: 0.4;
  cursor: not-allowed;
}}

/* Tree rows */
.tree-row {{
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 7px 10px;
  border-top: 1px solid var(--rule);
  cursor: pointer;
  border-radius: 2px;
}}
.tree-row:hover {{
  background: #212429;
}}
.tree-row.active {{
  background: rgba(255,120,10,0.14);
  border-color: var(--accent);
}}
.cb {{
  width: 13px;
  height: 13px;
  border: 1.5px solid var(--mute);
  border-radius: 2px;
  flex-shrink: 0;
}}
.pin {{
  color: var(--expired);
  font-size: 11px;
}}
.tree-name {{
  font-weight: 600;
  font-size: 12px;
  color: var(--ink);
}}
.tree-count {{
  color: var(--mute);
  font-size: 11px;
  font-variant-numeric: tabular-nums;
}}
.tree-spacer {{
  flex: 1;
}}
.badge {{
  font-size: 10px;
  font-weight: 700;
  padding: 2px 8px;
  border-radius: 2px;
}}
.badge-crit {{
  background: var(--red-dim);
  color: #ff8a95;
}}
.badge-warn {{
  background: var(--yellow-dim);
  color: #ffb673;
}}
.badge-good {{
  background: var(--green-dim);
  color: #a8df9e;
}}
.play {{
  color: var(--mute);
  font-size: 10px;
}}

/* Heatmap visual tile */
.hm-tile-card {{
  border-radius: 2px;
  padding: 4px 6px 3px;
  position: relative;
  box-shadow: none !important;
}}
.hm-tile-card .hm-count {{
  font-size: 13px;
  font-weight: 700;
  line-height: 1;
  font-variant-numeric: tabular-nums;
}}
.hm-tile-card .hm-sub {{
  font-size: 8px;
  font-weight: 600;
  opacity: .9;
}}
.hm-tile-card .hm-sbar {{
  display: flex;
  height: 3px;
  border-radius: 1px;
  overflow: hidden;
  margin-top: 3px;
  gap: 1px;
}}
.hm-tile-card .hm-time {{
  font-size: 8px;
  margin-top: 2px;
  color: var(--mute);
  text-align: center;
  font-variant-numeric: tabular-nums;
}}
.hm-action-btn button {{
  height: 20px !important;
  min-height: 20px !important;
  font-size: 9.5px !important;
  padding: 0 4px !important;
  margin-top: -1px !important;
  border-radius: 0 0 2px 2px !important;
  border-top: 1px solid var(--rule) !important;
}}
.hm-state-btn button {{
  height: 56px !important;
  min-height: 56px !important;
  font-size: 11px !important;
  font-weight: 700 !important;
  border-radius: 2px !important;
}}

/* KPI button flush attachment under stat card */
[data-testid="column"] > div > div > div > .stButton > button {{
  min-height: 22px !important;
  height: 22px !important;
  font-size: 10px !important;
  padding: 1px 6px !important;
  margin-top: -2px !important;
  border-top: none !important;
  border-radius: 0 0 2px 2px !important;
}}

/* Life gauge */
.life-gauge {{
  height: 6px;
  border-radius: 2px;
  background: rgba(255,255,255,0.08);
  overflow: hidden;
  margin-top: 3px;
}}
.life-gauge-fill {{
  height: 100%;
  border-radius: 2px;
}}

/* Leaf sparkbar */
.leaf-sparkbar {{
  height: 4px;
  border-radius: 1px;
  overflow: hidden;
  margin-top: 1px;
  background: rgba(255,255,255,0.06);
  min-width: 40px;
}}
.leaf-sparkbar-fill {{
  height: 100%;
  border-radius: 1px;
}}

.state-ribbon {{
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 8px;
  margin-bottom: 8px;
}}
.state-kpi-card {{
  background: var(--card);
  border: 1px solid var(--rule);
  border-left: 3px solid var(--accent);
  border-radius: 2px;
  padding: 6px 10px;
  box-shadow: none !important;
}}
.state-kpi-card.urgent {{ border-left-color: var(--critical); }}
.state-kpi-card.warn {{ border-left-color: var(--warning); }}
.state-kpi-card.good {{ border-left-color: var(--healthy); }}
.state-kpi-label {{
  font-size: 9px;
  font-weight: 600;
  letter-spacing: .08em;
  text-transform: uppercase;
  color: var(--mute);
}}
.state-kpi-val {{
  font-size: 16px;
  font-weight: 700;
  color: var(--ink);
  margin-top: 1px;
  font-variant-numeric: tabular-nums;
}}
.state-kpi-hint {{ font-size: 10px; color: var(--slate); }}

.top-glow-kpi {{
  background: var(--card);
  border: 1px solid var(--rule);
  border-top: 2px solid var(--glow, var(--accent));
  border-radius: 2px;
  padding: 8px 10px;
  box-shadow: none !important;
}}
.top-glow-kpi.interactive:hover {{
  border-color: var(--accent);
}}

@media (prefers-reduced-motion:reduce) {{ * {{ transition:none !important; }} }}
</style>
"""


# --------------------------------------------------------------------------
# Small HTML fragments still needed outside the canvas
# --------------------------------------------------------------------------
def eyebrow(label: str) -> str:
    return f'<div class="eyebrow">{escape(label)}</div>'


def note(html: str) -> str:
    """A line of guidance. Takes trusted markup so callers can bold a value."""
    return f'<div class="note">{html}</div>'


def status_pill(band: str) -> str:
    meta = BAND_META.get(band, BAND_META["Healthy"])
    return (f'<span class="pill" style="color:{meta["color"]};background:{meta["tint"]}">'
            f'<b class="sym">{meta["symbol"]}</b>{escape(band)}</span>')


def health_text(band: str) -> str:
    """
    Band name prefixed with its symbol, for places that can only take plain
    text - the Manage editor's Health Status column being the one that matters.

    The editor is a Streamlit grid, so it cannot be handed markup. Putting the
    symbol in the string keeps the editor and the report reading the same way,
    which matters because the two sit one tab apart.
    """
    meta = BAND_META.get(band, BAND_META["Healthy"])
    return f'{meta["symbol"]} {band}'


def pick_line(headline: str, hint: str) -> str:
    return (f'<div class="pickline"><span class="h">{escape(headline)}</span>'
            f'<span class="p">{escape(hint)}</span></div>')


def empty(headline: str, hint: str) -> str:
    """An empty result should say what happened and what to do about it."""
    return (f'<div class="void"><div class="h">{escape(headline)}</div>'
            f'<div class="p">{escape(hint)}</div></div>')


def attention_table(rows: list) -> str:
    """
    The renewal queue: what needs doing, soonest first, with the health symbol
    carrying the urgency.

    This fills the column beside the editor, which previously held a line of
    text saying there was nothing to report. The point of that space is to tell
    someone what to type into the editor next, so it lists the work instead.
    """
    if not rows:
        return ""
    head = ("<tr><th></th><th>Environment</th><th>Schema Name</th><th>Expiry Date</th>"
            "<th class=\"r\">Time Left</th></tr>")
    body = "".join(
        "<tr>"
        f'<td class="sym" style="color:{BAND_META[r["band"]]["color"]}" '
        f'title="{escape(BAND_META[r["band"]]["label"])}">{BAND_META[r["band"]]["symbol"]}</td>'
        f'<td class="m">{escape(str(r["environment"]))}</td>'
        f'<td class="m">{escape(str(r["schema_name"]))}</td>'
        f'<td class="m">{escape(fmt_date(r["exp_date"]))}</td>'
        f'<td class="m r" style="color:{BAND_META[r["band"]]["color"]};font-weight:600">'
        f'{escape(fmt_days(r["days_left"]))}</td>'
        "</tr>"
        for r in rows
    )
    return f'<table class="tblx">{head}{body}</table>'


def edits_table(rows: list) -> str:
    """
    The local-edit audit trail on the Manage tab: what the workbook said, what
    it says now, and when it was changed.

    "Now showing" is the column someone comes here to read - it is the date they
    just typed - so it is the one set in solid ink, with the workbook value
    struck through beside it.
    """
    if not rows:
        return ""
    head = ("<tr><th>Schema Name</th><th>Was</th><th>Now showing</th><th>Changed</th></tr>")
    body = "".join(
        "<tr>"
        f'<td class="m">{escape(str(r["schema_name"]))}'
        f' <span style="color:var(--mute)">{escape(str(r["environment"]))}</span></td>'
        f'<td class="m was">{escape(fmt_date(r["source_exp_date"]))}</td>'
        f'<td class="m now">{escape(fmt_date(r["exp_date"]))}</td>'
        f'<td class="m" style="color:var(--slate)">{escape(fmt_date(r["edited_at"]))}</td>'
        "</tr>"
        for r in rows
    )
    return f'<table class="tblx">{head}{body}</table>'


def sanitize_kpi_subtext(value: str | int | float, subtext: str | None) -> str:
    """
    Guarantees subtext never duplicates the headline number verbatim.
    Shows genuinely complementary context only, or returns an empty string.
    """
    if not subtext:
        return ""
    sub = str(subtext).strip()
    val_str = str(value).strip()

    # If the subtext is purely the value
    if sub == val_str:
        return ""

    # Strip verbatim repetition like "{value} of ...", "{value} / ..."
    if sub.startswith(f"{val_str} of "):
        sub = sub[len(f"{val_str} of "):].strip()
    elif sub.startswith(f"{val_str} / "):
        sub = sub[len(f"{val_str} / "):].strip()

    # Strip verbatim repetition like "{value} fleet total", "of {value} total fleet", etc.
    patterns_to_clean = [
        f"of {val_str} total fleet",
        f"of {val_str} total",
        f"{val_str} fleet total",
        f"{val_str} total fleet",
        f"{val_str} fleet",
        f"{val_str} total",
    ]
    for p in patterns_to_clean:
        if p in sub:
            sub = sub.replace(p, "").strip(" ·,-")

    # If after cleaning the subtext is empty or just punctuation
    if not sub or sub in ["·", "-", ",", ""]:
        return ""
    return sub


def kpi_card(
    label: str,
    value: str | int | float,
    subtext: str | None = None,
    glow: str = "#38bdf8",
    val_color: str | None = None,
    total_suffix: str | None = None,
    badge: str | None = None,
    badge_color: str | None = None,
    is_active: bool = False,
    interactive: bool = False,
    onclick: str | None = None,
) -> str:
    """
    Canonical shared KPI card component used across the application.
    Enforces that subtext never restates the headline number verbatim.
    Strictly follows Grafana flat design system: 2px corners, thin 1px border, zero shadow.
    """
    clean_sub = sanitize_kpi_subtext(value, subtext)
    sub_html = f'<div class="kpi-sub" style="font-size:9.5px;color:var(--mute);margin-top:2px;">{clean_sub}</div>' if clean_sub else '<div class="kpi-sub" style="font-size:9.5px;min-height:13px;"></div>'
    v_col = f"color:{val_color};" if val_color else "color:var(--ink);"
    val_suffix_html = f' <span style="font-size:10px;color:var(--mute);font-weight:400;">/ {total_suffix}</span>' if total_suffix else ""
    badge_html = f'<span style="font-size:8.5px;color:{badge_color or glow};font-weight:700;font-variant-numeric:tabular-nums;">{badge}</span>' if badge else ""
    active_style = "border:1px solid var(--accent);" if is_active else "border:1px solid var(--rule);"

    lbl_lower = label.lower()
    if "risk" in lbl_lower or "expir" in lbl_lower or (val_color and val_color.lower() in ("#f2495c", "#ef4444")):
        fill_cls = "stat-fill-red"
    elif "impact" in lbl_lower or "crit" in lbl_lower or "warn" in lbl_lower or "urgent" in lbl_lower or (val_color and val_color.lower() in ("#ff9830", "#f97316", "#f59e0b")):
        fill_cls = "stat-fill-yellow"
    elif "health" in lbl_lower or (val_color and val_color.lower() in ("#73bf69", "#10b981")):
        fill_cls = "stat-fill-green"
    else:
        fill_cls = "stat-fill-neutral"

    click_attr = f' onclick="{onclick}"' if onclick else ""

    return f"""
    <div class="panel stat-panel {fill_cls}" style="padding:6px 10px;margin-bottom:2px;{active_style}box-shadow:none !important;border-radius:2px;"{click_attr}>
      <div style="display:flex;align-items:center;justify-content:space-between;">
        <div class="kpi-label" style="font-size:10.5px;font-weight:600;color:var(--slate);text-transform:uppercase;letter-spacing:.02em;">{escape(label)}</div>
        {badge_html}
      </div>
      <div class="kpi-value" style="font-size:20px;font-weight:600;line-height:1.15;margin-top:2px;font-variant-numeric:tabular-nums;{v_col}">{escape(str(value))}{val_suffix_html}</div>
      {sub_html}
    </div>
    """


def grafana_sparkline(values: list[int], color: str, height: int = 24, width: int = 68) -> str:
    """
    Sleek Grafana-grade SVG line sparkline with subtle gradient area fill.
    values: list of ints (up to 7), most recent last.
    """
    if not values:
        return ""
    vals = values[-7:]
    peak = max(vals) or 1
    low = min(vals)
    n = len(vals)
    if n == 1:
        vals = vals * 2
        n = 2

    # If constant across snapshots (e.g. 500 items tracked), render clean baseline + dot
    if peak == low:
        mid_y = height / 2
        return (
            f'<svg viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
            f'style="display:block;overflow:visible" role="img" aria-label="stable trend">'
            f'<line x1="2" y1="{mid_y:.1f}" x2="{width - 4:.1f}" y2="{mid_y:.1f}" stroke="{color}" stroke-width="1.8"/>'
            f'<circle cx="{width - 4:.1f}" cy="{mid_y:.1f}" r="2" fill="{color}"/>'
            f'</svg>'
        )

    # Trend curve with subtle area fill
    dx = (width - 8) / max(n - 1, 1)
    val_range = max(1, peak - low)
    pts = []
    for i, v in enumerate(vals):
        x = 3 + i * dx
        y = (height - 4) - ((v - low) / val_range) * (height - 8)
        pts.append((x, y))

    poly_pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    area_pts = f"3,{height - 2} " + poly_pts + f" {pts[-1][0]:.1f},{height - 2}"

    return (
        f'<svg viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
        f'style="display:block;overflow:visible" role="img" aria-label="trend">'
        f'<polygon points="{area_pts}" fill="{color}" fill-opacity="0.15"/>'
        f'<polyline points="{poly_pts}" fill="none" stroke="{color}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>'
        f'<circle cx="{pts[-1][0]:.1f}" cy="{pts[-1][1]:.1f}" r="2" fill="{color}"/>'
        f'</svg>'
    )


def compliance_donut(pct: float, color: str, size: int = 28) -> str:
    """
    SVG donut arc showing compliance %. Pure SVG stroke-dashoffset technique.
    pct: 0-100
    """
    sw = 3.5 if size <= 32 else 5
    r = (size - sw - 2) / 2
    circ = 2 * 3.14159 * r
    filled = circ * (pct / 100)
    cx = size / 2
    fsize = 7.5 if size <= 32 else 9
    return (
        f'<svg width="{size}" height="{size}" viewBox="0 0 {size} {size}" role="img" aria-label="{pct:.0f}% compliant">'
        f'<circle cx="{cx}" cy="{cx}" r="{r}" fill="none" stroke="rgba(255,255,255,0.08)" stroke-width="{sw}"/>'
        f'<circle cx="{cx}" cy="{cx}" r="{r}" fill="none" stroke="{color}" stroke-width="{sw}" '
        f'stroke-linecap="round" stroke-dasharray="{filled:.1f} {circ:.1f}" '
        f'transform="rotate(-90 {cx} {cx})"/>'
        f'<text x="{cx}" y="{cx + 2.8}" text-anchor="middle" font-family="var(--mono)" '
        f'font-size="{fsize}" font-weight="700" fill="{color}">{pct:.0f}%</text>'
        f'</svg>'
    )


def panel_header(title: str, color: str = "#38bdf8", live: bool = True,
                 count: str | None = None, info: str | None = None) -> str:
    """Grafana-style section panel chrome with colored left border and live badge."""
    live_html = '<span class="ph-live">LIVE</span>' if live else ""
    count_html = f'<span class="ph-count">{escape(count)}</span>' if count else ""
    info_html = (f'<span title="{escape(info)}" style="font-size:11px;color:rgba(255,255,255,0.35);'
                 f'cursor:help;">ⓘ</span>') if info else ""
    return (
        f'<div class="panel-header" style="--ph-color:{color}">'
        f'<span class="ph-title">{escape(title)}</span>'
        f'<span class="ph-right">{count_html}{live_html}{info_html}</span>'
        f'</div>'
    )


def alert_chip(band: str) -> str:
    """FIRING / PENDING / OK chip — maps health band to Grafana alert state."""
    if band == "Expired":
        return '<span class="alert-chip firing">FIRING</span>'
    if band in ("Critical", "Warning"):
        return '<span class="alert-chip pending">PENDING</span>'
    return '<span class="alert-chip ok">OK</span>'


def grafana_stat_card(
    label: str,
    value: str | int | float,
    color: str,
    subtext: str = "",
    badge: str = "",
    badge_bg: str = "",
    badge_fg: str = "#fff",
    sparkline_vals: list[int] | None = None,
    donut_pct: float | None = None,
    delta: str = "",
    state: str = "ok",   # "ok" | "pending" | "firing"
) -> str:
    """
    Full Grafana-style stat panel — full background tint wash matching severity,
    2px sharp corners, 1px border #2c3235, tabular numbers, sparkline/donut.
    """
    lbl_lower = label.lower()
    if "expir" in lbl_lower or color.lower() in ("#f2495c", "#ef4444") or state == "firing":
        fill_cls = "stat-fill-red"
        val_cls = "red"
        val_color = BAND_META["Expired"]["color"]
    elif "crit" in lbl_lower or "warn" in lbl_lower or "urgent" in lbl_lower or color.lower() in ("#ff9830", "#f97316", "#f59e0b") or state == "pending":
        fill_cls = "stat-fill-yellow"
        val_cls = "yellow"
        val_color = BAND_META["Critical"]["color"]
    elif "health" in lbl_lower or color.lower() in ("#73bf69", "#10b981"):
        fill_cls = "stat-fill-green"
        val_cls = "green"
        val_color = BAND_META["Healthy"]["color"]
    else:
        fill_cls = "stat-fill-neutral"
        val_cls = "white"
        val_color = "var(--ink)"

    sub_html = f'<div class="stat-sub">{escape(subtext)}</div>' if subtext else ""

    # Right-side visual: donut for healthy, sparkline for others
    visual_html = ""
    if donut_pct is not None:
        visual_html = compliance_donut(donut_pct, val_color, size=28)
    elif sparkline_vals:
        visual_html = grafana_sparkline(sparkline_vals, val_color, height=18, width=54)

    right_visual = f'<div style="flex:none;margin-left:auto;">{visual_html}</div>' if visual_html else ""

    return f"""
    <div class="panel stat-panel {fill_cls}" style="border:1px solid var(--rule);border-radius:2px;">
      <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:8px;">
        <div style="flex:1;min-width:0;">
          <div class="stat-label">{escape(label)}</div>
          <div class="stat-val {val_cls}" style="font-size:26px;font-weight:600;line-height:1.1;margin-top:4px;">{escape(str(value))}</div>
        </div>
        {right_visual}
      </div>
      <div>
        {sub_html}
      </div>
    </div>
    """


def life_gauge(days_left: int, max_days: int = 730) -> str:
    """Inline linear gauge bar showing how much life is remaining, 0–max_days scale."""
    if days_left < 0:
        color = BAND_META["Expired"]["color"]
        pct = 0
    else:
        d = min(days_left, max_days)
        pct = round(d / max_days * 100)
        if days_left <= CRITICAL_DAYS:
            color = BAND_META["Critical"]["color"]
        elif days_left <= WARNING_DAYS:
            color = BAND_META["Warning"]["color"]
        else:
            color = BAND_META["Healthy"]["color"]
    return (
        f'<div class="life-gauge" title="{days_left}d remaining">'
        f'<div class="life-gauge-fill" style="width:{pct}%;background:{color}"></div>'
        f'</div>'
    )


def leaf_sparkbar(days_left: int, max_days: int = 365) -> str:
    """Tiny inline days-remaining bar for entity leaf rows."""
    if days_left < 0:
        color = BAND_META["Expired"]["color"]
        pct = 0
    else:
        pct = min(100, round(days_left / max_days * 100))
        if days_left <= CRITICAL_DAYS:
            color = BAND_META["Critical"]["color"]
        elif days_left <= WARNING_DAYS:
            color = BAND_META["Warning"]["color"]
        else:
            color = BAND_META["Healthy"]["color"]
    return (
        f'<div class="leaf-sparkbar">'
        f'<div class="leaf-sparkbar-fill" style="width:{pct}%;background:{color}"></div>'
        f'</div>'
    )


def heatmap_visual_cell(n: int, exp: int, crit: int, warn: int, hlth: int,
                        min_days: int, color: str, is_active: bool = False) -> str:
    """
    Grafana/Datadog-grade heatmap cell — colored background with stacked mini-bar,
    item count, and soonest expiry sub-label.
    """
    risk_ratio = (exp * 3 + crit * 2 + warn) / max(n, 1)
    opacity = min(0.75, 0.12 + risk_ratio * 0.35)
    bg = color + f"{int(opacity * 255):02x}"
    border = f"1px solid {color}" if not is_active else "1px solid var(--accent)"

    # Stacked mini-bar segments
    segs = ""
    if n > 0:
        if exp:
            segs += f'<div style="flex:{exp};background:{BAND_META["Expired"]["color"]}"></div>'
        if crit:
            segs += f'<div style="flex:{crit};background:{BAND_META["Critical"]["color"]}"></div>'
        if warn:
            segs += f'<div style="flex:{warn};background:{BAND_META["Warning"]["color"]}"></div>'
        if hlth:
            segs += f'<div style="flex:{hlth};background:{BAND_META["Healthy"]["color"]};opacity:.6"></div>'

    # Concise status description
    if exp > 0 and crit > 0:
        count_str = f"{exp}E · {crit}C"
    elif exp > 0:
        count_str = f"{exp} Exp"
    elif crit > 0 and warn > 0:
        count_str = f"{crit}C · {warn}W"
    elif crit > 0:
        count_str = f"{crit} Crit"
    elif warn > 0:
        count_str = f"{warn} Warn"
    else:
        count_str = f"✓ {hlth} OK"

    # Compact duration formatting to prevent clipping
    if min_days < 0:
        d_neg = -min_days
        if d_neg >= 365:
            y = d_neg // 365
            rm = (d_neg % 365) // 30
            time_str = f"{y}y {rm}m overdue" if rm else f"{y}y overdue"
        elif d_neg >= 60:
            time_str = f"{d_neg // 30}m overdue"
        else:
            time_str = f"{d_neg}d overdue"
    else:
        if min_days >= 365:
            y = min_days // 365
            rm = (min_days % 365) // 30
            time_str = f"{y}y {rm}m left" if rm else f"{y}y left"
        elif min_days >= 60:
            time_str = f"{min_days // 30}m left"
        else:
            time_str = f"{min_days}d left"

    return (
        f'<div class="hm-tile-card" style="background:{bg};border:{border};border-bottom:none;border-radius:2px;">'
        f'<div style="display:flex;align-items:center;justify-content:space-between;">'
        f'<span class="hm-count" style="color:{color}">{escape(str(n))}</span>'
        f'<span class="hm-sub" style="color:{color}">{escape(count_str)}</span>'
        f'</div>'
        f'<div class="hm-sbar">{segs}</div>'
        f'<div class="hm-time">{escape(time_str)}</div>'
        f'</div>'
    )


sla_pill = sla_badge



