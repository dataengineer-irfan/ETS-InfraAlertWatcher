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
        "color": "#EF4444", "tint": "rgba(239,68,68,0.18)", "symbol": "✕",
        "label": "Expired", "plain": "already past its date",
    },
    "Critical": {
        "color": "#F97316", "tint": "rgba(249,115,22,0.18)", "symbol": "▲",
        "label": f"Critical ({CRITICAL_DAYS} days or less)",
        "plain": f"expires within {CRITICAL_DAYS} days",
    },
    "Warning": {
        "color": "#F59E0B", "tint": "rgba(245,158,11,0.18)", "symbol": "◆",
        "label": f"Warning ({WARNING_DAYS} days or less)",
        "plain": f"expires within {WARNING_DAYS} days",
    },
    "Healthy": {
        "color": "#10B981", "tint": "rgba(16,185,129,0.18)", "symbol": "✓",
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


def team_of(schema_name: str, component: str = "") -> str:
    """Classify schema or component into team owner."""
    sn = (schema_name or "").upper()
    cmp = (component or "").upper()
    if any(k in sn for k in ["ORR", "MMIS", "COGNOS", "COTS_REP"]):
        return "Cognos"
    if any(k in sn for k in ["ISIM", "EMAR", "INFA", "ETL"]):
        return "Informatica"
    if any(k in sn for k in ["FADS", "OMNI", "LETTER", "PRINT"]):
        return "Letters"
    if any(k in sn for k in ["WAS", "TC", "JBOSS", "JVM"]) or any(k in cmp for k in ["SOFTWARE", "PATCH", "SWVER"]):
        return "App Server"
    return "Core"

# --------------------------------------------------------------------------
# Colour tokens shared by the Streamlit chrome and the canvas (Deep Slate)
# --------------------------------------------------------------------------
TOKENS = {
    "paper": "#020617",     # Deepest canvas background
    "card": "#0F172A",      # Deep slate panel & card surface
    "sunk": "#070D1E",      # Dark inset / inputs / table rows
    "rule": "#1E293B",      # Panel borders & hairlines
    "rule_soft": "#283548", # Secondary inner dividers
    "ink": "#F8FAFC",       # 18.5:1 high-contrast crisp text
    "slate": "#94A3B8",     # Clean secondary labels
    "mute": "#64748B",      # Decorative / subtle tags
    "accent": "#38BDF8",    # Neon Cyan primary active accent
    "accent_tint": "rgba(56,189,248,0.14)",
    "accent_line": "rgba(56,189,248,0.45)",
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
    Days remaining -> a compact duration, or how long ago it lapsed.

    Overdue uses the same scale as time remaining. Two records in this data
    lapsed in 2020 and 2022, and '2068d overdue' is a number nobody reads at a
    glance - '5.7yr overdue' is the same fact, understood.
    """
    if days is None:
        return "--"
    try:
        d = int(days)
    except (TypeError, ValueError):
        return "--"
    if d < 0:
        return f"{_span(-d)} overdue"
    if d == 0:
        return "today"
    return _span(d)


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
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=IBM+Plex+Mono:wght@400;500;600&display=swap');

:root {{
  color-scheme: dark;
  --paper:{t['paper']}; --card:{t['card']}; --sunk:{t['sunk']};
  --rule:{t['rule']}; --rule-soft:{t['rule_soft']};
  --ink:{t['ink']}; --slate:{t['slate']}; --mute:{t['mute']};
  --accent:{t['accent']}; --accent-tint:{t['accent_tint']}; --accent-line:{t['accent_line']};
  --expired:{BAND_COLOR['Expired']}; --critical:{BAND_COLOR['Critical']};
  --warning:{BAND_COLOR['Warning']}; --healthy:{BAND_COLOR['Healthy']};
  --ui:'Inter',"Segoe UI Variable Text","Segoe UI",system-ui,-apple-system,sans-serif;
  --mono:'IBM Plex Mono',"Cascadia Mono",ui-monospace,Consolas,monospace;
}}

.stApp, body {{ background:var(--paper); color:var(--ink); font-family:var(--ui); }}

/* Zero-scroll viewport layout */
.block-container {{ max-width:none !important; padding:.3rem .6rem 0 !important; }}
#MainMenu, footer, header[data-testid="stHeader"] {{ visibility:hidden; height:0; }}
[data-testid="stToolbar"], div[data-testid="stDialog"], div[role="dialog"], [data-testid="stToast"], [data-testid="stNotification"], [data-testid="stDecoration"] {{ display:none !important; }}
[data-testid="stVerticalBlock"] {{ gap:.5rem; }}
[data-testid="stVerticalBlockBorderWrapper"] {{ background:transparent; }}
iframe {{ display:block; border:0; }}

h1, h2, h3, h4, h5, h6 {{ font-family:var(--ui); color:#f8fafc !important; letter-spacing:-.015em; font-weight:700; }}
code, kbd, .mono {{ font-family:var(--mono); }}

/* Custom scrollbars */
::-webkit-scrollbar {{ width:6px; height:6px; }}
::-webkit-scrollbar-track {{ background:var(--paper); }}
::-webkit-scrollbar-thumb {{ background:var(--rule-soft); border-radius:3px; }}
::-webkit-scrollbar-thumb:hover {{ background:var(--mute); }}

/* ---- tabs: enterprise dark tab bar -------------------------- */
[data-baseweb="tab-list"] {{
  gap:3px; background:var(--card); border:1px solid var(--rule); border-radius:7px;
  padding:2px; height:32px; align-items:center; box-shadow:0 2px 6px rgba(0,0,0,0.3);
}}
button[data-baseweb="tab"] {{
  height:28px !important; min-height:28px !important; padding:0 12px !important;
  border-radius:5px; background:transparent !important;
  transition:all .12s ease;
}}
button[data-baseweb="tab"] div,
button[data-baseweb="tab"] p,
button[data-baseweb="tab"] span,
button[data-baseweb="tab"] {{
  color:#94a3b8 !important; font-family:var(--ui); font-size:12px !important;
  font-weight:600 !important; letter-spacing:.01em;
}}
button[data-baseweb="tab"]:hover {{
  background:rgba(255,255,255,0.06) !important;
}}
button[data-baseweb="tab"]:hover div,
button[data-baseweb="tab"]:hover p,
button[data-baseweb="tab"]:hover span {{
  color:#f8fafc !important;
}}
button[data-baseweb="tab"][aria-selected="true"] {{
  background:var(--accent-tint) !important;
  box-shadow:inset 0 0 0 1px var(--accent-line) !important;
}}
button[data-baseweb="tab"][aria-selected="true"] div,
button[data-baseweb="tab"][aria-selected="true"] p,
button[data-baseweb="tab"][aria-selected="true"] span {{
  color:#38bdf8 !important; font-weight:700 !important;
}}
[data-baseweb="tab-highlight"], [data-baseweb="tab-border"] {{ display:none; }}
[data-baseweb="tab-panel"] {{ padding:4px 0 0 !important; margin:0 !important; }}

/* ---- buttons ----------------------------------------------------------- */
.stButton > button, [data-testid="stDownloadButton"] > button {{
  width:100%; background:var(--card) !important; color:#f8fafc !important;
  border:none !important; border-radius:6px; padding:.3rem .6rem; min-height:30px;
  font-family:var(--ui); font-size:11.5px; font-weight:600;
  box-shadow:0 1px 3px rgba(0,0,0,0.35);
  transition:all .15s ease;
}}
.stButton > button:hover, [data-testid="stDownloadButton"] > button:hover {{
  border:1px solid var(--accent) !important; background:var(--accent-tint) !important; color:var(--accent) !important;
}}
.stButton > button:focus-visible, [data-testid="stDownloadButton"] > button:focus-visible {{
  outline:2px solid var(--accent); outline-offset:1px;
}}
.stButton > button[kind="primary"] {{
  background:linear-gradient(135deg, #0284c7 0%, #0369a1 100%) !important;
  border:1px solid #38bdf8 !important; color:#fff !important; font-weight:700;
  box-shadow:0 0 10px rgba(56,189,248,0.25);
}}
.stButton > button[kind="primary"]:hover {{
  background:linear-gradient(135deg, #38bdf8 0%, #0284c7 100%) !important;
  color:#fff !important; box-shadow:0 0 14px rgba(56,189,248,0.4);
}}

/* ---- state chooser ----------------------------------------------------- */
.pickline {{
  display:flex; align-items:center; gap:8px; padding:2px 0 3px; margin-bottom:2px;
}}
.pickline .h {{ font-size:12.5px; font-weight:700; color:#f8fafc !important; }}
.pickline .p {{ font-size:11px; color:#94a3b8 !important; }}
.statebar {{ display:flex; align-items:center; gap:8px; }}

/* ---- Inputs, Selectors, Popovers & Expanders --------------------------- */
[data-testid="stExpander"] {{
  background:var(--card) !important; border:1px solid var(--rule) !important;
  border-radius:8px !important; box-shadow:0 2px 6px rgba(0,0,0,0.25);
  margin-bottom:6px !important;
}}
[data-testid="stExpander"] details {{
  background:var(--card) !important; color:var(--ink) !important;
}}
[data-testid="stExpander"] summary {{
  background:var(--card) !important; color:var(--ink) !important;
  font-family:var(--ui); font-size:12.5px; font-weight:600;
  border-radius:8px; padding:6px 12px !important;
}}
[data-testid="stExpander"] summary:hover {{
  background:var(--sunk) !important; color:var(--accent) !important;
}}
[data-testid="stExpander"] details[open] summary {{
  border-bottom:1px solid var(--rule-soft) !important;
}}
[data-testid="stExpander"] [data-testid="stExpanderDetails"] {{
  background:var(--card) !important; padding:8px 12px !important;
}}

[data-testid="stDataFrame"], [data-testid="stDataEditor"] {{
  border:1px solid var(--rule) !important; border-radius:8px !important; overflow:hidden !important;
  background:var(--card) !important; box-shadow:0 2px 8px rgba(0,0,0,0.3) !important;
}}
[data-testid="stDataEditor"] canvas {{
  border-radius:6px !important;
}}
.stTextInput input, .stDateInput input, .stNumberInput input,
div[data-baseweb="select"] > div {{
  background:var(--sunk) !important; border:1px solid var(--rule) !important; color:#f8fafc !important;
  font-family:var(--mono); font-size:12px; border-radius:6px;
}}
div[data-baseweb="popover"], ul[role="listbox"] {{
  background:var(--card) !important; border:1px solid var(--rule) !important; color:#f8fafc !important;
}}
li[role="option"] {{
  background:var(--card) !important; color:#f8fafc !important; font-family:var(--ui); font-size:12px;
}}
li[role="option"]:hover, li[aria-selected="true"] {{
  background:var(--accent-tint) !important; color:var(--accent) !important;
}}
.stTextInput input:focus, div[data-baseweb="select"] > div:focus-within {{
  border-color:var(--accent) !important; box-shadow:0 0 0 1px var(--accent);
}}

[data-testid="stTextInput"], [data-testid="stSelectbox"], [data-testid="stMultiSelect"] {{
  margin:0 !important;
}}
[data-testid="stTextInput"] input,
[data-testid="stSelectbox"] div[data-baseweb="select"] > div,
[data-testid="stMultiSelect"] div[data-baseweb="select"] > div {{
  min-height:34px !important;
}}
[data-testid="stMultiSelect"] div[data-baseweb="select"] > div {{
  background:var(--sunk); border-color:var(--rule);
}}
[data-testid="stMultiSelect"] span[data-baseweb="tag"] {{
  background:rgba(56,189,248,0.18); border:1px solid rgba(56,189,248,0.4);
  color:var(--accent); border-radius:4px; font-family:var(--mono); font-size:10.5px;
}}
[data-testid="stMetricValue"] {{ font-family:var(--mono); color:var(--ink); }}
.stAlert {{ border-radius:7px; background:var(--card); border:1px solid var(--rule); color:var(--ink); }}

/* ---- Enterprise components -------------------------------------------- */
.note {{ font-size:12px; color:#94a3b8; line-height:1.45; margin:4px 0 8px; }}
.note b {{ color:#f8fafc; font-weight:700; }}
.eyebrow {{
  font-size:10px; font-weight:700; letter-spacing:.12em; text-transform:uppercase;
  color:#38bdf8; padding:4px 0 4px; display:block; margin-top:8px; margin-bottom:4px;
}}
.pill {{
  display:inline-flex; align-items:center; gap:5px; font-size:11px; font-weight:600;
  padding:2px 8px; border-radius:5px; white-space:nowrap; border:1px solid transparent;
}}
.pill .sym {{ font-size:10.5px; line-height:1; font-weight:700; }}
.card {{
  background:var(--card); border-radius:8px;
  padding:10px 12px; box-shadow:0 2px 6px rgba(0,0,0,0.25);
}}
.void {{
  background:var(--card); border:1px dashed var(--rule); border-radius:8px;
  padding:14px 16px; text-align:center;
}}
.void .h {{ font-size:13px; font-weight:600; color:var(--ink); }}
.void .p {{ font-size:11.5px; color:var(--slate); margin-top:3px; }}

.tblx {{ width:100%; border-collapse:collapse; font-size:12px; }}
.tblx th {{
  font-size:10px; font-weight:700; letter-spacing:.08em;
  text-transform:uppercase; color:#94a3b8; padding:8px 10px;
  border-bottom:1px solid var(--rule); background:var(--card);
}}
.tblx td {{
  padding:7px 10px; border-bottom:1px solid var(--rule-soft); white-space:nowrap; color:#f8fafc;
}}
.tblx tr:hover td {{ background:rgba(56,189,248,0.04); }}
.tblx td.m {{ font-family:var(--mono); }}
.tblx td.c {{ text-align:center; }}
.tblx td.r {{ text-align:right; }}
.tblx td.sym {{ width:1%; padding-right:4px; font-weight:700; text-align:center; }}
.tblx td.was {{ color:var(--mute); text-decoration:line-through; }}
.tblx td.now {{ color:#f8fafc; font-weight:600; }}

/* Master-detail & Navigation Pills */
.nav-ribbon {{
  display:flex; align-items:center; gap:8px; background:var(--card);
  border:1px solid var(--rule); border-radius:8px; padding:6px 10px;
  margin-bottom:6px; box-shadow:0 2px 6px rgba(0,0,0,0.25);
}}
.nav-title {{ font-size:14px; font-weight:700; color:var(--ink); letter-spacing:-.01em; }}
.nav-badge {{
  font-family:var(--mono); font-size:10px; font-weight:600;
  color:var(--accent); background:var(--accent-tint);
  border:1px solid var(--accent-line); border-radius:4px; padding:1px 6px;
}}
.top-glow-kpi {{
  background:var(--card); border-top:3px solid var(--glow,#38bdf8);
  border-radius:8px; padding:10px 12px; box-shadow:0 2px 8px rgba(0,0,0,0.25);
}}
.top-glow-kpi .kpi-label {{
  font-size:9.5px; font-weight:700; letter-spacing:.1em; text-transform:uppercase; color:var(--mute);
}}
.top-glow-kpi .kpi-value {{
  font-family:var(--mono); font-size:22px; font-weight:700; color:var(--ink); margin-top:2px;
}}
.top-glow-kpi .kpi-sub {{
  font-size:10.5px; color:var(--slate); margin-top:1px;
}}

.state-ribbon {{
  display:grid; grid-template-columns:repeat(4, 1fr); gap:8px; margin-bottom:8px;
}}
.state-kpi-card {{
  background:var(--card); border:1px solid var(--rule); border-left:3px solid var(--accent);
  border-radius:6px; padding:6px 10px; box-shadow:0 1px 4px rgba(0,0,0,0.25);
}}
.state-kpi-card.urgent {{ border-left-color:var(--critical); }}
.state-kpi-card.warn {{ border-left-color:var(--warning); }}
.state-kpi-card.good {{ border-left-color:var(--healthy); }}
.state-kpi-label {{
  font-size:9px; font-weight:700; letter-spacing:.08em; text-transform:uppercase; color:var(--mute);
}}
.state-kpi-val {{
  font-family:var(--mono); font-size:16px; font-weight:700; color:var(--ink); margin-top:1px;
}}
.state-kpi-hint {{ font-size:10px; color:var(--slate); }}

.env-tag {{
  display:inline-block; font-family:var(--mono); font-size:10.5px; font-weight:600;
  padding:1px 6px; border-radius:4px; background:rgba(56,189,248,0.12);
  border:1px solid rgba(56,189,248,0.3); color:var(--accent);
}}

.matrix-table {{
  width:100%; border-collapse:collapse; font-size:11.5px;
}}
.matrix-table th {{
  font-size:9.5px; font-weight:700; letter-spacing:.08em; text-transform:uppercase;
  color:#94a3b8; padding:7px 8px; border-bottom:1px solid var(--rule); background:var(--card);
}}
.matrix-table td {{
  padding:6px 8px; border-bottom:1px solid var(--rule-soft); color:#f8fafc;
}}
.matrix-cell-badge {{
  display:inline-flex; align-items:center; gap:4px; font-family:var(--mono); font-size:10.5px;
  font-weight:600; padding:2px 6px; border-radius:4px;
}}

.code-box {{
  background:var(--sunk); border:1px solid var(--rule); border-radius:6px;
  padding:10px; font-family:var(--mono); font-size:11px; line-height:1.5;
  color:#f8fafc; overflow-x:auto; max-height:220px;
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
