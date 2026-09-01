"""
report.py — the single-screen report canvas
===========================================
Builds one self-contained HTML document: stylesheet, layout skeleton, the
whole record set as JSON, and a JavaScript engine that owns every filter.

Why the report is a canvas instead of Streamlit widgets
------------------------------------------------------
The brief is "no page scrolling under any circumstance". A Streamlit page
is a vertical document: widget heights vary with browser zoom and font
availability, so a stack of panels can only ever *usually* fit. Here the
layout is a CSS grid whose rows are declared as fractions of 100% with
`overflow:hidden`, so it cannot exceed the viewport - the arithmetic, not
good luck, is what guarantees the fit.

The second reason is feel. Streamlit reruns the whole script on every
click, which costs a few hundred milliseconds and a visible flash. Filter
state lives in `S` inside the browser instead, so selecting a slicer,
drilling into a component or paging the table repaints immediately and
nothing round-trips to Python.

Two rules keep the code honest
------------------------------
1. Every renderer is a pure function returning an HTML string, exactly as
   the old `ui.py` was. Nothing reaches into the DOM except `apply()`,
   which assigns those strings. That means the whole engine can be run
   under bare node and asserted on without a browser.
2. There is one filter pipeline (`rows()`), so no two panels can disagree
   about a number. Where a control must not filter itself away - the
   health chips, the component cards - it asks for the scope *excluding*
   its own dimension via `rows({skip:'band'})`, which is the only
   sanctioned exception.

Health thresholds match src/expiry_checker.py, so what the report calls
urgent is exactly what the reminder emails act on.
"""

from __future__ import annotations

import json
from datetime import date

# The palette, the health thresholds and the plain-language glosses all come
# from ui.py, which is the one place they are defined. Restating them here
# would let the canvas drift away from the Manage tab and from the thresholds
# the reminder emails use.
from ui import (  # noqa: F401  (BAND_COLOR is re-exported for callers)
    BAND_COLOR,
    BAND_META,
    BANDS,
    COMPONENT_BLURB,
    COMPONENT_CODE,
    CRITICAL_DAYS,
    ENV_BLURB,
    STATES,
    TOKENS,
    WARNING_DAYS,
)

# --------------------------------------------------------------------------
# Canvas configuration: the slicer and bookmark sets, which exist only in
# this report and have no meaning outside it.
# --------------------------------------------------------------------------
WINDOWS = [
    {"id": "all", "label": "All dates"},
    {"id": "90", "label": "Next 90 days"},
    {"id": "365", "label": "Next 12 months"},
    {"id": "past", "label": "Overdue"},
]

# ui.TOKENS with camelCase keys, for the SVG builders. CSS can read
# `--rule-soft` from a custom property, but an SVG `stroke` attribute needs the
# literal value, so the charts read it from the payload rather than repeating
# the hex - which is how the old dark palette survived in the charts long after
# the rest of the page went light.
JS_TOKENS = {
    parts[0] + "".join(word.capitalize() for word in parts[1:]): value
    for parts, value in ((name.split("_"), value) for name, value in TOKENS.items())
}

# Saved views. Each one sets slicers and the focus panel in a single click,
# which is what a Power BI bookmark does.
BOOKMARKS = [
    {"id": "all", "label": "Everything",
     "tip": "Clear every filter and show the full picture",
     "set": {"band": None, "window": "all", "component": None, "environment": None,
             "q": "", "focus": "horizon", "sort": "soon"}},
    {"id": "attention", "label": "Needs attention",
     "tip": "Only items that are expired or inside the 30-day warning window",
     "set": {"band": "__urgent__", "window": "all", "focus": "horizon", "sort": "soon"}},
    {"id": "q90", "label": "Next 90 days",
     "tip": "Renewal work due in the next quarter, soonest first",
     "set": {"band": None, "window": "90", "focus": "horizon", "sort": "soon"}},
    {"id": "overdue", "label": "Overdue",
     "tip": "Items whose expiry date has already passed",
     "set": {"band": "Expired", "window": "all", "focus": "horizon", "sort": "soon"}},
    {"id": "coverage", "label": "Coverage map",
     "tip": "Clear filters and show which pairings are tracked at all",
     "set": {"band": None, "window": "all", "component": None, "environment": None,
             "q": "", "focus": "coverage"}},
]

# ==========================================================================
# Stylesheet
# ==========================================================================
# Sizes that must survive a 430px-tall canvas *and* fill a 940px one are
# expressed in vh. Inside this iframe 1vh is 1% of the canvas, so the type
# and chart scale with the space actually available instead of guessing.
_CSS = r"""
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=IBM+Plex+Mono:wght@400;500;600&display=swap');

:root{
  color-scheme: dark;
  /*__TOKENS__*/
  --ui:'Inter',"Segoe UI Variable Text","Segoe UI",system-ui,-apple-system,sans-serif;
  --mono:'IBM Plex Mono',"Cascadia Mono",ui-monospace,Consolas,monospace;
  --gap:6px;
  --shadow:0 2px 8px rgba(0,0,0,0.3);
}

*{ box-sizing:border-box; }
html,body{ height:100%; margin:0; overflow:hidden; }
body{
  background:var(--paper); color:var(--ink); font-family:var(--ui);
  font-size:12px; line-height:1.35;
  -webkit-font-smoothing:antialiased; text-rendering:optimizeLegibility;
}
.mono,.num{ font-family:var(--mono); font-variant-numeric:tabular-nums; }

/* Universal flex/grid containment — prevent text blowout */
.head, .head-row, .brand, .narrative-strip, .crumbs, .views, .asof,
.kpis, .kpi, .kpi-row1, .kpi-row2, .comps, .cc, .panel, .phead, .phint, .pbody,
.slicers, .search, .filter-drawer, .tbl, .tbl td {
  min-width:0;
}

/* Panels */
.panel{
  background:var(--card); border-radius:7px;
  box-shadow:var(--shadow); min-height:0; min-width:0;
  display:flex; flex-direction:column; overflow:hidden;
}
.phead{
  display:flex; align-items:baseline; gap:9px; flex:none;
  padding:7px 10px 5px; border-bottom:1px solid var(--rule-soft);
}
.ptitle{ font-size:11px; font-weight:600; letter-spacing:.005em; color:var(--ink); }
.phint{ font-size:10.5px; color:var(--slate); flex:1; min-width:0;
        overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.pbody{ flex:1; min-height:0; padding:8px 10px 9px; display:flex;
        flex-direction:column; overflow:hidden; }

/* ---- shell: header, slicers, then three content rows ------------------ */
.shell{
  height:100%; padding:var(--gap); display:grid; gap:var(--gap);
  grid-template-rows:auto minmax(52px,.62fr) minmax(136px,1.55fr) minmax(154px,1.95fr);
}
.rowB{ display:grid; gap:var(--gap); grid-template-columns:minmax(0,1.05fr) minmax(0,1fr);
       min-height:0; }
.rowC{ display:grid; gap:var(--gap); grid-template-columns:minmax(0,1.55fr) minmax(0,1fr);
       min-height:0; }

/* ---- header / unified command bar ----------------------------------- */
.head{
  display:flex; flex-direction:column; gap:2px; flex:none;
  background:var(--card); border-radius:7px; box-shadow:var(--shadow);
  padding:4px 8px; min-width:0; overflow:hidden;
}
.head-row{
  display:flex; align-items:center; gap:6px; width:100%; min-width:0; overflow:hidden;
}
.brand{ display:flex; align-items:baseline; gap:5px; flex:none; }
.brand h1{ margin:0; font-size:12.5px; font-weight:700; letter-spacing:-.01em; white-space:nowrap; }
.brand .where{
  font-family:var(--mono); font-size:9px; font-weight:600; letter-spacing:.04em;
  text-transform:uppercase; color:var(--accent); background:var(--accent-tint);
  border:1px solid var(--accent-line); border-radius:4px; padding:1px 4px; white-space:nowrap;
}

/* Smart Narrative Banner */
.narrative-strip{
  display:flex; align-items:center; gap:5px; flex:1 1 auto; min-width:0; max-width:100%;
  background:var(--sunk); border-radius:5px; padding:2px 7px;
  overflow:hidden; text-overflow:ellipsis; white-space:nowrap;
}
.narrative-tag{
  font-family:var(--mono); font-size:8.5px; font-weight:700; letter-spacing:.05em;
  text-transform:uppercase; padding:1px 4px; border-radius:3px; flex:none; white-space:nowrap;
}
.tag-alert{ background:rgba(239,68,68,0.22); color:#EF4444; border:1px solid rgba(239,68,68,0.4); }
.tag-crit{ background:rgba(249,115,22,0.22); color:#F97316; border:1px solid rgba(249,115,22,0.4); }
.tag-warn{ background:rgba(245,158,11,0.22); color:#F59E0B; border:1px solid rgba(245,158,11,0.4); }
.tag-ok{ background:rgba(16,185,129,0.2); color:#10B981; border:1px solid rgba(16,185,129,0.35); }
.narrative-text{
  font-size:10px; color:var(--ink); flex:1 1 auto; min-width:0; max-width:100%;
  overflow:hidden; text-overflow:ellipsis; white-space:nowrap;
}
.narrative-text b{ font-weight:600; color:var(--ink); }

.story-btn{
  font:inherit; font-size:9.5px; font-weight:600; color:var(--accent);
  background:var(--accent-tint); border:1px solid var(--accent-line); border-radius:5px;
  padding:2px 7px; cursor:pointer; white-space:nowrap; flex:none;
  transition:all .12s ease; display:inline-flex; align-items:center; gap:3px;
}
.story-btn:hover{ background:var(--accent); color:#000; }

.asof{ font-family:var(--mono); font-size:8.5px; color:var(--slate); text-align:right;
       line-height:1.2; flex:none; white-space:nowrap; }
.asof b{ color:var(--ink); font-weight:600; }

.crumbs{
  display:flex; align-items:center; gap:5px; width:100%; min-width:0; overflow:hidden;
  border-top:1px solid var(--rule-soft); padding-top:2px; margin-top:1px;
}
.crumbs:has(.none){ display:none; }
.crumbs .lead{ font-size:8.5px; font-weight:600; letter-spacing:.1em; text-transform:uppercase;
               color:var(--mute); flex:none; }
.crumbs .none{ font-size:9.5px; color:var(--slate); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.cx{
  display:inline-flex; align-items:center; gap:4px; flex:none;
  background:var(--accent-tint); border:1px solid var(--accent-line); color:var(--accent);
  border-radius:4px; padding:1px 4px; font-size:9.5px; white-space:nowrap;
  font:inherit; cursor:pointer;
}
.cx u{ text-decoration:none; color:var(--mute); font-size:8px; letter-spacing:.08em; text-transform:uppercase; }
.cx b{ font-family:var(--mono); font-weight:500; }
.cx s{ text-decoration:none; font-size:9.5px; line-height:1; color:var(--accent); opacity:.6; padding-left:1px; }
.cx:hover s{ opacity:1; }

.views{ display:flex; align-items:center; gap:3px; flex:none; }
.views .lead{ font-size:8.5px; font-weight:600; letter-spacing:.1em; text-transform:uppercase;
              color:var(--mute); margin-right:1px; }

/* ---- chips ----------------------------------------------------------- */
.chip{
  font:inherit; font-size:10px; font-weight:500; color:var(--slate);
  background:var(--sunk); border:1px solid transparent; border-radius:5px;
  padding:1px 6px; cursor:pointer; white-space:nowrap; display:inline-flex;
  align-items:center; gap:3px; line-height:1.25;
  transition:background .12s ease, border-color .12s ease, color .12s ease;
}
.chip:hover{ border-color:var(--accent-line); color:var(--ink); background:var(--card); }
.chip[aria-pressed="true"], .chip.on{
  background:var(--accent-tint); border-color:var(--accent); color:var(--accent); font-weight:600;
}
.chip .n{ font-family:var(--mono); font-size:9px; color:var(--mute); }
.chip[aria-pressed="true"] .n{ color:var(--accent); }
.chip i{ width:5px; height:5px; border-radius:2px; flex:none; }
.chip:focus-visible, .cx:focus-visible, .seg button:focus-visible,
.tbl th button:focus-visible, .pg button:focus-visible{
  outline:2px solid var(--accent); outline-offset:1px;
}

/* ---- slicer bar ------------------------------------------------------ */
.slicers{
  display:inline-flex; align-items:center; gap:4px; flex:none;
  background:transparent; box-shadow:none; padding:0; min-width:0;
}
.sgroup{ display:flex; align-items:center; gap:3px; }
.sgroup > label{ font-size:8.5px; font-weight:600; letter-spacing:.09em; text-transform:uppercase;
                 color:var(--mute); margin-right:1px; }
.vr{ width:1px; align-self:stretch; background:var(--rule-soft); margin:0 2px; }

.filter-drawer{
  display:flex; flex-wrap:wrap; gap:4px; width:100%;
  border-top:1px solid var(--rule-soft); margin-top:2px; padding-top:3px;
}
.filter-toggle{
  font:inherit; font-size:10.5px; font-weight:600; color:var(--slate);
  background:var(--sunk); border:1px solid transparent; border-radius:5px;
  padding:2px 7px; cursor:pointer; white-space:nowrap; display:inline-flex;
  align-items:center; gap:4px; transition:background .12s ease, border-color .12s ease, color .12s ease;
}
.filter-toggle:hover{ border-color:var(--accent-line); color:var(--ink); background:var(--card); }
.filter-toggle.active{ border-color:var(--accent); color:var(--accent); background:var(--accent-tint); }
.filter-badge{
  font-family:var(--mono); font-size:8.5px; font-weight:700;
  background:var(--accent); color:#000; border-radius:3px; padding:0 3px;
}
.search{ position:relative; display:flex; align-items:center; flex:1; min-width:120px; max-width:220px; }
.search input{
  font:inherit; font-size:10.5px; font-family:var(--mono); width:100%; color:var(--ink);
  background:var(--sunk); border:1px solid var(--rule); border-radius:5px;
  padding:2px 20px 2px 7px;
}
.search input::placeholder{ color:var(--mute); font-family:var(--ui); }
.search input:focus{ outline:none; border-color:var(--accent); background:var(--card); }
.search .clr{ position:absolute; right:3px; border:0; background:none; cursor:pointer;
              color:var(--mute); font-size:12px; line-height:1; padding:0 2px; }

/* ---- KPI strip ------------------------------------------------------- */
.kpis{ display:grid; gap:var(--gap); grid-template-columns:repeat(6,minmax(0,1fr)); min-height:0; min-width:0; }
.kpi{
  background:var(--card); border:none; border-left:3px solid var(--edge,transparent);
  border-radius:7px; box-shadow:var(--shadow); padding:5px 8px 5px; cursor:pointer;
  display:flex; flex-direction:column; justify-content:space-between;
  text-align:left; font:inherit; min-width:0; max-width:100%; overflow:hidden;
  transition:background .12s ease, box-shadow .12s ease; position:relative;
}
.kpi:hover{ background:var(--sunk); }
.kpi[aria-pressed="true"]{ background:var(--accent-tint);
                           box-shadow:inset 0 0 0 1.5px var(--accent); }
.kpi.flat{ cursor:default; }
.kpi.flat:hover{ background:var(--card); }
.kpi-row1{ display:flex; align-items:baseline; justify-content:space-between; gap:4px; width:100%; min-width:0; overflow:hidden; }
.kpi .v{
  font-family:var(--mono); font-variant-numeric:tabular-nums; font-weight:700;
  font-size:clamp(16px,3.2vh,24px); line-height:1.05; color:var(--val,var(--ink));
  white-space:nowrap; overflow:hidden; text-overflow:ellipsis; min-width:0;
}
.kpi-spark{ width:42px; height:12px; flex:none; }
.kpi .k{ font-size:9.5px; font-weight:600; color:var(--ink); white-space:nowrap;
         overflow:hidden; text-overflow:ellipsis; min-width:0; max-width:100%; }
.kpi-row2{ display:flex; align-items:center; justify-content:space-between; gap:4px; width:100%; min-width:0; overflow:hidden; }
.kpi .s{ font-size:8.5px; color:var(--slate); font-family:var(--mono); white-space:nowrap;
         overflow:hidden; text-overflow:ellipsis; min-width:0; max-width:100%; }
.trend{
  font-family:var(--mono); font-size:8px; font-weight:700; padding:1px 3px;
  border-radius:3px; flex:none; white-space:nowrap; line-height:1.1;
}
.trend.good{ background:rgba(16,185,129,0.2); color:#10B981; }
.trend.bad{ background:rgba(239,68,68,0.2); color:#EF4444; }
.trend.flat{ background:var(--sunk); color:var(--mute); }
.trend.none{ color:var(--mute); background:transparent; }

.so-what{
  font-size:8px; color:var(--slate); max-width:100%; min-width:0;
  overflow:hidden; text-overflow:ellipsis; white-space:nowrap;
  line-height:1.15; margin-top:2px;
}

.kpi[data-dom]{ padding:6px 10px 6px; }
.kpi[data-dom] .v{ font-size:clamp(20px,4vh,30px); font-weight:800; }
.kpi[data-dom] .k{ font-size:10.5px; }

/* ---- component cards ------------------------------------------------- */
.comps{ display:grid; gap:var(--gap); grid-template-columns:1fr 1fr; grid-template-rows:1fr 1fr;
        min-height:0; min-width:0; }
.cc{
  background:var(--card); border:none; border-radius:7px;
  box-shadow:var(--shadow); padding:7px 9px; cursor:pointer; font:inherit; text-align:left;
  display:flex; flex-direction:column; justify-content:space-between; min-height:0; min-width:0; overflow:hidden;
  transition:background .12s ease, box-shadow .12s ease;
}
.cc:hover{ background:var(--sunk); box-shadow:var(--shadow), 0 0 0 1px var(--accent-line); }
.cc[aria-pressed="true"]{ background:var(--accent-tint);
                          box-shadow:inset 0 0 0 1.5px var(--accent); }
.cc .head-row{ display:flex; align-items:center; gap:6px; min-width:0; overflow:hidden; }
.cc .code{ font-family:var(--mono); font-size:9px; font-weight:700; letter-spacing:.06em;
           color:var(--accent); background:var(--accent-tint); border:1px solid var(--accent-line);
           border-radius:3px; padding:1px 4px; flex:none; }
.cc[aria-pressed="true"] .code{ background:var(--accent); color:#fff; }
.cc .nm{ font-size:11px; font-weight:700; color:var(--ink);
         overflow:hidden; text-overflow:ellipsis; white-space:nowrap; flex:1; min-width:0; }
.cc .foot{ margin-top:auto; display:flex; align-items:flex-end; justify-content:space-between;
           gap:6px; padding-top:2px; min-width:0; }
.cc .cnt{ font-family:var(--mono); font-variant-numeric:tabular-nums; font-weight:700;
          font-size:clamp(15px,2.5vh,20px); line-height:1; min-width:0; }
.cc .cnt em{ font-style:normal; font-size:9px; font-weight:500; color:var(--mute);
             margin-left:2px; font-family:var(--ui); }
.cc .nx{ font-family:var(--mono); font-size:9px; color:var(--slate); text-align:right;
         white-space:nowrap; min-width:0; overflow:hidden; text-overflow:ellipsis; }
.cc .nx b{ color:var(--val,var(--ink)); font-weight:700; }
.meter{ display:flex; height:4px; border-radius:2px; overflow:hidden; background:var(--rule-soft);
        margin:4px 0 2px; flex:none; }
.meter-label{ font-size:8.5px; color:var(--mute); margin-bottom:3px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.meter i{ display:block; height:100%; }

/* ---- segmented control ----------------------------------------------- */
.seg{ display:inline-flex; background:var(--sunk); border:1px solid var(--rule);
      border-radius:5px; padding:1px; flex:none; }
.seg button{
  font:inherit; font-size:9.5px; font-weight:600; color:var(--slate); background:none;
  border:0; border-radius:4px; padding:2px 6px; cursor:pointer; white-space:nowrap;
}
.seg button:hover{ color:var(--ink); }
.seg button[aria-pressed="true"]{ background:var(--card); color:var(--accent);
                                  box-shadow:0 0 0 1px var(--rule), var(--shadow); }

/* ---- charts ---------------------------------------------------------- */
.chart{ flex:1; min-height:0; display:block; width:100%; }
.chart text{ font-family:var(--mono); }
.legend{ display:flex; align-items:center; gap:9px; flex-wrap:wrap; flex:none;
         font-size:9px; color:var(--slate); padding-top:3px; }
.legend span{ display:inline-flex; align-items:center; gap:4px; white-space:nowrap; }
.legend i{ width:6px; height:6px; border-radius:2px; }

/* ---- environment cards (focus: Environments) ------------------------- */
.envs{ flex:1; min-height:0; display:grid; gap:5px; align-content:start;
       grid-template-columns:repeat(auto-fill,minmax(96px,1fr)); overflow:hidden; }
.ec{
  background:var(--sunk); border:none; border-left:3px solid var(--val,var(--rule-soft));
  border-radius:6px; padding:4px 7px 5px; cursor:pointer; font:inherit; text-align:left;
  min-width:0; overflow:hidden; transition:background .12s ease, box-shadow .12s ease;
}
.ec:hover{ background:var(--card); box-shadow:0 0 0 1px var(--accent-line); }
.ec[aria-pressed="true"]{ background:var(--accent-tint);
                          box-shadow:inset 0 0 0 1.5px var(--accent); }
.ec .en{ font-family:var(--mono); font-size:11px; font-weight:600; letter-spacing:.03em; }
.ec .eb{ font-size:9px; color:var(--slate); white-space:nowrap; overflow:hidden;
         text-overflow:ellipsis; }
.ec .er{ display:flex; justify-content:space-between; gap:5px; font-family:var(--mono);
         font-size:9.5px; color:var(--slate); margin-top:2px; }
.ec .er b{ color:var(--val,var(--ink)); font-weight:600; }

/* ---- coverage matrix ------------------------------------------------- */
.mx{ border-collapse:separate; border-spacing:3px; width:100%; flex:none; }
.mx th{ font-family:var(--mono); font-size:8.5px; font-weight:600; letter-spacing:.07em;
        color:var(--mute); padding:0 2px 2px; text-align:center; }
.mx th.rh{ text-align:left; }
.mx td{ padding:0; }
.mx .rl{ font-family:var(--mono); font-size:10px; color:var(--ink); white-space:nowrap; padding-right:6px; }
.mx .rl u{ text-decoration:none; color:var(--mute); font-size:8.5px; }
.mx .c{ border-radius:3px; display:flex; align-items:center; justify-content:center;
        font-family:var(--mono); font-size:9.5px; font-weight:600; height:20px;
        border:1px solid transparent; }
.mx .c.void{ background:repeating-linear-gradient(45deg,var(--sunk),var(--sunk) 3px,
             var(--rule-soft) 3px,var(--rule-soft) 6px); color:var(--mute); font-weight:400; }

/* ---- detail table ---------------------------------------------------- */
.twrap{ flex:1; min-height:0; overflow:hidden; }
.tbl{ width:100%; border-collapse:collapse; table-layout:fixed; }
.tbl thead th{ padding:0; border-bottom:1px solid var(--rule); }
.tbl thead th button{
  font:inherit; font-size:9px; font-weight:600; letter-spacing:.09em; text-transform:uppercase;
  color:var(--mute); background:none; border:0; cursor:pointer; width:100%; text-align:left;
  padding:0 7px 5px; display:flex; align-items:center; gap:3px;
}
.tbl thead th.r button{ justify-content:flex-end; }
.tbl thead th button:hover{ color:var(--ink); }
.tbl thead th[aria-sort] button{ color:var(--accent); }
.tbl thead th button s{ text-decoration:none; font-size:8px; }
.tbl tbody td{ padding:0 7px; height:26px; border-bottom:1px solid var(--rule-soft);
               white-space:nowrap; overflow:hidden; text-overflow:ellipsis; font-size:11px; }
.tbl tbody tr:hover td{ background:var(--sunk); }
.tbl tbody tr.hot td{ background:var(--accent-tint); }
.tbl td.r{ text-align:right; }
.tbl .schema{ font-family:var(--mono); font-size:10.5px; }
.tbl .sub{ font-size:9px; color:var(--slate); }
.env-pill{ display:inline-block; font-family:var(--mono); font-size:9px; font-weight:600;
           letter-spacing:.04em; padding:1px 5px; border-radius:3px; background:var(--sunk);
           border:1px solid var(--rule); color:var(--ink); }
.dot{ display:inline-flex; align-items:center; gap:5px; white-space:nowrap; font-weight:500; }
.dot i{ width:7px; height:7px; border-radius:50%; flex:none; }
.tag{ font-family:var(--mono); font-size:8px; letter-spacing:.06em; color:var(--accent);
      border:1px solid var(--accent-line); background:var(--accent-tint); border-radius:3px;
      padding:0 3px; margin-left:5px; }
.pg{ display:flex; align-items:center; gap:6px; flex:none; padding-top:5px; font-size:10px;
     color:var(--slate); }
.pg button{ font:inherit; font-family:var(--mono); font-size:11px; line-height:1;
            background:var(--card); border:none; border-radius:4px;
            width:20px; height:19px; cursor:pointer; color:var(--ink);
            box-shadow:0 1px 3px rgba(0,0,0,0.4); }
.pg button:disabled{ color:var(--rule-soft); cursor:default; box-shadow:none; }
.pg button:not(:disabled):hover{ box-shadow:0 0 0 1.5px var(--accent); color:var(--accent); }
.pg .of{ font-family:var(--mono); }
.pg .sp{ flex:1; }

/* Table inline data bars */
.time-cell{ position:relative; overflow:hidden; }
.data-bar{
  position:absolute; left:0; top:2px; bottom:2px;
  border-radius:2px; pointer-events:none; z-index:0;
  transition:width .2s ease;
}
.time-val{ position:relative; z-index:1; }

/* Cross-visual Highlighting */
.shell[data-hl-active="true"] [data-hl-comp],
.shell[data-hl-active="true"] [data-hl-env],
.shell[data-hl-active="true"] [data-hl-quarter]{
  opacity:0.25; transition:opacity .15s ease;
}
.shell[data-hl-active="true"] [data-hl-match="true"]{
  opacity:1 !important; filter:brightness(1.2);
}

/* Story Mode Overlay */
.story-overlay{
  position:fixed; inset:0; z-index:9999; display:none; pointer-events:auto;
}
.story-overlay.on{ display:flex; align-items:flex-end; justify-content:center; padding-bottom:20px; }
.story-backdrop{
  position:absolute; inset:0; background:rgba(0,0,0,0.65); backdrop-filter:blur(2px);
}
.story-box{
  position:relative; z-index:10; background:var(--card); border:1px solid var(--accent);
  border-radius:8px; box-shadow:0 12px 36px rgba(0,0,0,0.7); width:92%; max-width:580px;
  padding:10px 14px; display:flex; flex-direction:column; gap:6px;
  animation:slideUp .2s cubic-bezier(0.16, 1, 0.3, 1);
}
@keyframes slideUp{ from{ opacity:0; transform:translateY(10px); } to{ opacity:1; transform:translateY(0); } }
.story-head{ display:flex; align-items:center; justify-content:space-between; gap:6px; }
.story-badge{ font-size:10.5px; font-weight:700; color:var(--accent); text-transform:uppercase; letter-spacing:.05em; }
.story-ctrls{ display:flex; align-items:center; gap:5px; }
.story-ctrls button{
  font:inherit; font-size:10px; font-weight:600; color:var(--slate); background:var(--sunk);
  border:1px solid var(--rule); border-radius:4px; padding:2px 7px; cursor:pointer;
}
.story-ctrls button:hover{ color:var(--ink); border-color:var(--accent); }
.story-desc{
  font-size:11px; color:var(--ink); line-height:1.35; max-width:100%;
  overflow:hidden; text-overflow:ellipsis; white-space:nowrap;
}
.story-progress{ height:3px; background:var(--sunk); border-radius:2px; overflow:hidden; width:100%; }
.story-bar{ height:100%; background:var(--accent); transition:width .3s ease; }
.story-spotlight{
  position:relative; z-index:10000 !important;
  box-shadow:0 0 0 2px var(--accent), 0 0 20px rgba(56, 189, 248, 0.4) !important;
  border-radius:7px;
}

/* Empty state */
.void{ flex:1; min-height:0; display:flex; flex-direction:column; align-items:center;
       justify-content:center; text-align:center; gap:3px; padding:8px; }
.void .h{ font-size:11.5px; font-weight:600; }
.void .p{ font-size:10.5px; color:var(--slate); max-width:34ch; }
.void button{ margin-top:4px; }

/* Tooltip */
#tip{
  position:fixed; z-index:99; pointer-events:none; opacity:0; transform:translateY(2px);
  transition:opacity .1s ease; max-width:260px;
  background:#1E293B; color:#F8FAFC; font-size:10.5px; line-height:1.35;
  border:1px solid #334155; border-radius:7px; padding:6px 10px;
  box-shadow:0 8px 24px rgba(0,0,0,0.6);
}
#tip.on{ opacity:1; transform:none; }
#tip b{ font-family:var(--mono); font-weight:700; color:var(--slate); }

.rowC .phead{ padding:5px 8px 4px; }
.rowC .pbody{ padding:6px 8px 7px; }
.rowC .ptitle{ font-size:10px; }

@media (prefers-reduced-motion:reduce){ *{ transition:none !important; } }
@media (max-width:1150px){
  .kpis{ grid-template-columns:repeat(3,minmax(0,1fr)); }
  .shell{ grid-template-rows:auto auto minmax(96px,.9fr) minmax(132px,1.5fr) minmax(142px,1.8fr); }
}
@media (max-width:820px){
  .rowB,.rowC{ grid-template-columns:minmax(0,1fr); }
}
"""


# ==========================================================================
# Layout skeleton — every dynamic region is an empty mount point
# ==========================================================================
_BODY = r"""
<div class="shell" id="mShell">
  <div class="head">
    <div class="head-row">
      <div class="brand"><h1>Expiry Watchtower</h1><span class="where" id="mWhere"></span></div>
      <div class="narrative-strip" id="mNarrative" data-tip="Executive narrative summary reacting to active filters"></div>
      <div class="slicers" id="mSlicers"></div>
      <div class="views" id="mViews"></div>
      <button class="story-btn" type="button" data-act="startStory" data-tip="Guided executive narrative walkthrough">▶ Walk me through it</button>
      <div class="asof" id="mAsOf"></div>
    </div>
    <div class="crumbs" id="mCrumbs"></div>
  </div>

  <div class="kpis" id="mKpis"></div>

  <div class="rowB">
    <div class="comps" id="mComps"></div>
    <div class="panel">
      <div class="phead">
        <span class="ptitle">Explore</span>
        <span class="phint" id="mFocusHint"></span>
        <div class="seg" id="mFocusSeg"></div>
      </div>
      <div class="pbody" id="mFocus"></div>
    </div>
  </div>

  <div class="rowC">
    <div class="panel" id="mTablePanel">
      <div class="phead">
        <span class="ptitle">Environment &amp; schema detail</span>
        <span class="phint" id="mTableHint"></span>
        <div class="seg" id="mTableSeg"></div>
      </div>
      <div class="pbody" id="mTable"></div>
      <div class="pg" id="mPager"></div>
    </div>

    <div class="panel">
      <div class="phead">
        <span class="ptitle">Workload by quarter</span>
        <span class="phint" id="mWhenHint"></span>
        <div class="seg" id="mWhenSeg"></div>
      </div>
      <div class="pbody" id="mWhen"></div>
    </div>
  </div>
</div>
<div class="story-overlay" id="mStoryModal"></div>
<div id="tip"></div>
"""


# ==========================================================================
# Engine
# ==========================================================================
# Everything between the ENGINE markers is pure: string in, string out, no
# DOM. tests/test_report_engine.mjs slices it out of the built page and runs
# it under node against pandas ground truth, so the numbers on screen are
# checked by the same source that draws them.
_JS = r"""
const DATA = /*__DATA__*/;

/*==ENGINE-START==*/
const BANDS = DATA.bands, META = DATA.bandMeta, CODE = DATA.componentCode;
const CRIT = DATA.criticalDays, WARN = DATA.warningDays;
const T = DATA.tokens;
const NAME_OF = {};
Object.keys(CODE).forEach(name => { NAME_OF[CODE[name]] = name; });

const esc = v => String(v === null || v === undefined ? "" : v).replace(
  /[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// ---- length clamp helper ---------------------------------------------
function clampStr(s, maxLen){
  if (!s) return "";
  s = String(s).trim();
  if (s.length <= maxLen) return s;
  return s.slice(0, maxLen - 1).trim() + "…";
}

const healthOf = d => d < 0 ? "Expired" : d <= CRIT ? "Critical" : d <= WARN ? "Warning" : "Healthy";
const worstBand = set => BANDS.find(b => set.has(b)) || "Healthy";

const MONTHS = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
function fmtDate(iso){
  if (!iso) return "--";
  const p = String(iso).slice(0, 10).split("-");
  if (p.length !== 3) return String(iso);
  return String(p[2]).padStart(2, "0") + " " + MONTHS[+p[1] - 1] + " " + p[0];
}

function fmtDays(d){
  if (d === null || d === undefined) return "--";
  d = Math.trunc(d);
  if (d < 0) return span(-d) + " overdue";
  if (d === 0) return "today";
  return span(d);
}
function span(d){
  if (d < 90) return d + "d";
  if (d < 730) return Math.floor(d / 30) + "mo";
  return (d / 365).toFixed(1) + "yr";
}

function fmtDaysLong(d){
  if (d === null || d === undefined) return "no date";
  d = Math.trunc(d);
  if (d < 0) return spanLong(-d) + " overdue";
  if (d === 0) return "expires today";
  return spanLong(d) + " left";
}
function spanLong(d){
  if (d === 1) return "1 day";
  if (d < 60) return d + " days";
  const mo = Math.round(d / 30.44);
  return mo < 24 ? "about " + mo + " months" : "about " + (d / 365).toFixed(1) + " years";
}

const URGENT = ["Expired", "Critical", "Warning"];

function rows(S, skip){
  skip = skip || "";
  let out = DATA.records;
  if (S.state && skip !== "state") out = out.filter(r => r.state === S.state);
  if (S.component && skip !== "component") out = out.filter(r => r.component === S.component);
  if (S.environment && skip !== "environment") out = out.filter(r => r.environment === S.environment);
  if (S.band && skip !== "band"){
    out = S.band === "__urgent__" ? out.filter(r => URGENT.includes(r.band))
                                  : out.filter(r => r.band === S.band);
  }
  if (S.window && S.window !== "all" && skip !== "window"){
    if (S.window === "past") out = out.filter(r => r.days < 0);
    else { const n = +S.window; out = out.filter(r => r.days >= 0 && r.days <= n); }
  }
  const q = (S.q || "").trim().toLowerCase();
  if (q && skip !== "q") out = out.filter(r => r.hay.includes(q));
  return out;
}

const counts = rs => {
  const c = {}; BANDS.forEach(b => c[b] = 0);
  rs.forEach(r => c[r.band]++);
  return c;
};
const soonest = rs => rs.length ? rs.reduce((a, b) => b.days < a.days ? b : a) : null;

const SORTS = {
  soon:   { label: "Soonest expiry", cmp: (a, b) => a.days - b.days || a.schema.localeCompare(b.schema) },
  late:   { label: "Latest expiry",  cmp: (a, b) => b.days - a.days || a.schema.localeCompare(b.schema) },
  env:    { label: "Environment",    cmp: (a, b) => a.envRank - b.envRank || (+a.envNo) - (+b.envNo) || a.compRank - b.compRank },
  schema: { label: "Schema name",    cmp: (a, b) => a.schema.localeCompare(b.schema) },
  health: { label: "Health status",  cmp: (a, b) => BANDS.indexOf(a.band) - BANDS.indexOf(b.band) || a.days - b.days }
};

function sorted(rs, key){ return rs.slice().sort((SORTS[key] || SORTS.soon).cmp); }

function chip({ act, val, label, n, on, swatch, tip }){
  const sw = swatch ? '<i style="background:' + swatch + '"></i>' : "";
  const num = n !== undefined ? '<span class="n">' + n + "</span>" : "";
  const tp = tip ? ' data-tip="' + esc(tip) + '"' : "";
  const cl = "chip" + (on ? " on" : "");
  return '<button class="' + cl + '" type="button" data-act="' + esc(act) + '" data-val="'
    + esc(val) + '"' + (on ? ' aria-pressed="true"' : "") + tp + ">"
    + sw + esc(label) + num + "</button>";
}

function seg(name, opts, active){
  return '<div class="seg">' + opts.map(o =>
    '<button type="button" data-act="' + esc(name) + '" data-val="' + esc(o.id) + '"'
    + (o.id === active ? ' aria-pressed="true"' : "")
    + (o.tip ? ' data-tip="' + esc(o.tip) + '"' : "") + ">" + esc(o.label) + "</button>"
  ).join("") + "</div>";
}

function dot(band){
  return '<span class="dot" style="color:' + META[band].color + '"><i style="background:'
    + META[band].color + '"></i>' + esc(META[band].label) + "</span>";
}

function meter(c){
  const tot = (c.Expired || 0) + (c.Critical || 0) + (c.Warning || 0) + (c.Healthy || 0);
  if (!tot) return '<div class="meter"></div>';
  return '<div class="meter">' + BANDS.map(b => {
    const n = c[b] || 0;
    return n ? '<i style="width:' + (n / tot * 100) + '%;background:' + META[b].color + '"></i>' : "";
  }).join("") + "</div>";
}

function voidState(head, hint, action){
  return '<div class="void"><div class="h">' + esc(head) + '</div><div class="p">' + esc(hint)
    + "</div>" + (action || "") + "</div>";
}
function legend(){
  return '<div class="legend">' + BANDS.map(b =>
    '<span data-tip="' + esc(META[b].label + " - " + META[b].plain) + '"><i style="background:'
    + META[b].color + '"></i>' + esc(META[b].label) + "</span>").join("")
    + '<span style="color:var(--mute)">Hover mark for detail</span></div>';
}

// ---- header -----------------------------------------------------------
function renderCrumbs(S){
  const bits = [];
  if (DATA.mode === "all" && S.state) bits.push(["State", S.state, "state"]);
  if (S.component) bits.push(["Component", CODE[S.component], "component"]);
  if (S.environment) bits.push(["Environment", S.environment, "environment"]);
  if (S.band) bits.push(["Health", S.band === "__urgent__" ? "Needs attention" : S.band, "band"]);
  if (S.window && S.window !== "all")
    bits.push(["Dates", (DATA.windows.find(w => w.id === S.window) || {}).label, "window"]);
  if ((S.q || "").trim()) bits.push(["Search", clampStr(S.q.trim(), 14), "q"]);

  if (!bits.length)
    return '<span class="lead">Filters</span><span class="none">Showing every tracked item</span>';

  return '<span class="lead">Filters</span>' + bits.map(b =>
    '<button class="cx" type="button" data-act="drop" data-val="' + esc(b[2])
    + '" data-tip="Remove this filter"><u>' + esc(b[0]) + '</u><b>' + esc(b[1])
    + "</b><s>&times;</s></button>").join("")
    + '<button class="chip" type="button" data-act="reset" data-tip="Clear all filters">Clear all</button>';
}

function renderViews(S){
  return '<span class="lead">Saved views</span>' + DATA.bookmarks.map(b =>
    chip({ act: "view", val: b.id, label: b.label, on: S.view === b.id, tip: b.tip })).join("");
}

// ---- sparkline SVG ----------------------------------------------------
function sparklineSvg(values, color, width, height){
  width = width || 42; height = height || 12;
  if (!values || values.length < 2) return "";
  const min = Math.min(...values), max = Math.max(...values);
  const range = max === min ? 1 : (max - min);
  const pad = 1.2;
  const pts = values.map((v, i) => {
    const x = (i / (values.length - 1)) * (width - pad * 2) + pad;
    const y = height - pad - ((v - min) / range) * (height - pad * 2);
    return [x, y];
  });
  const dLine = "M" + pts.map(p => p[0].toFixed(1) + "," + p[1].toFixed(1)).join(" L");
  const dArea = dLine + " L" + (width - pad).toFixed(1) + "," + height + " L" + pad + "," + height + " Z";
  const last = pts[pts.length - 1];
  return '<svg class="kpi-spark" viewBox="0 0 ' + width + ' ' + height + '" preserveAspectRatio="none">'
    + '<path d="' + dArea + '" fill="' + color + '" fill-opacity="0.18"/>'
    + '<path d="' + dLine + '" fill="none" stroke="' + color + '" stroke-width="1.1" stroke-linecap="round"/>'
    + '<circle cx="' + last[0].toFixed(1) + '" cy="' + last[1].toFixed(1) + '" r="1.5" fill="' + color + '"/>'
    + '</svg>';
}

// ---- centralized metric direction config -----------------------------
const METRIC_DIRECTIONS = {
  tracked: "higher",
  expired: "lower",
  critical: "lower",
  warning: "lower",
  healthy: "higher",
  overdue: "lower",
  soonest: "higher",
  coverage: "higher"
};

// ---- scope-matched snapshot resolver ---------------------------------
function getScopeSnapshots(S){
  const snaps = DATA.snapshots;
  if (!snaps || !snaps.length) return [];

  // Check if any non-supported ad-hoc filters are active:
  // (environment, band, date window, search query)
  if (S.environment) return [];
  if (S.band && S.band !== "__urgent__") return [];
  if (S.window && S.window !== "all") return [];
  if (S.q && S.q.trim()) return [];

  const targetState = (DATA.mode === "state" ? DATA.state : S.state) || null;
  const targetComp = S.component || null;

  return snaps.filter(s => (s.state || null) === targetState && (s.component || null) === targetComp);
}

// ---- metric-aware trend delta badge (max 30 chars) -------------------
function trendDelta(curr, prev, metricKey){
  if (prev === undefined || prev === null) return '<span class="trend none">&mdash;</span>';
  const diff = curr - prev;
  if (diff === 0) return '<span class="trend flat">● Stable</span>';

  const isHigherBetter = METRIC_DIRECTIONS[metricKey] === "higher";
  const isGood = isHigherBetter ? (diff > 0) : (diff < 0);
  const cls = isGood ? "good" : "bad";

  if (diff > 0){
    return '<span class="trend ' + cls + '">&#9650; +' + diff + ' vs prev</span>';
  } else {
    return '<span class="trend ' + cls + '">&#9660; ' + diff + ' vs prev</span>';
  }
}

// ---- smart narrative headline generator (hard cap <= 90 chars) ---------
function renderNarrative(S){
  const rs = rows(S);
  const st = (DATA.mode === "all" ? S.state : DATA.state) || "All";
  const compName = S.component ? CODE[S.component] : "all";

  if (!rs.length){
    return '<span class="narrative-tag tag-ok">&#9675; Empty</span>'
      + '<span class="narrative-text" title="No items match active filters. Clear filters to reset.">'
      + 'No items match current filters. Clear filters to reset.</span>';
  }

  const expired = rs.filter(r => r.days < 0);
  const crit = rs.filter(r => r.days >= 0 && r.days <= CRIT);
  const warn = rs.filter(r => r.days > CRIT && r.days <= WARN);
  const nx = soonest(rs.filter(r => r.days >= 0)) || soonest(rs);

  let tag = "", text = "";
  if (expired.length > 0){
    const worst = expired.reduce((a, b) => b.days < a.days ? b : a);
    const sName = clampStr(worst.schema, 16);
    tag = '<span class="narrative-tag tag-alert">&#9679; Action</span>';
    text = '<b>' + esc(sName) + '</b> in ' + esc(worst.environment) + ' is <b>'
      + esc(fmtDays(worst.days)) + '</b> overdue. Rotate credentials.';
  } else if (crit.length > 0){
    tag = '<span class="narrative-tag tag-crit">&#9650; Critical</span>';
    const sName = nx ? clampStr(nx.schema, 14) : "";
    text = '<b>' + crit.length + ' item' + (crit.length === 1 ? '' : 's') + '</b> due in 15d'
      + (nx ? ' (next: ' + esc(sName) + ' in ' + esc(fmtDays(nx.days)) + ')' : '') + '. Renew now.';
  } else if (warn.length > 0){
    tag = '<span class="narrative-tag tag-warn">&#9670; Notice</span>';
    text = '<b>' + warn.length + ' item' + (warn.length === 1 ? '' : 's') + '</b> due in 30d'
      + (nx ? ' (next: ' + esc(fmtDate(nx.exp)) + ', ' + esc(fmtDays(nx.days)) + ')' : '') + '.';
  } else {
    tag = '<span class="narrative-tag tag-ok">&#10003; Compliant</span>';
    text = 'All <b>' + rs.length + '</b> items healthy. Next: <b>'
      + (nx ? (esc(fmtDays(nx.days)) + ' (' + esc(fmtDate(nx.exp)) + ')') : 'None') + '</b>.';
  }

  const plain = text.replace(/<[^>]+>/g, "");
  return tag + '<span class="narrative-text" title="' + esc(plain) + '">' + text + '</span>';
}

// ---- activity comparison strip ---------------------------------------
function renderSinceVisit(snaps){
  if (!snaps || snaps.length < 2){
    return "<b>" + DATA.records.length + "</b> tracked as of <b>" + esc(DATA.asOf) + "</b>";
  }
  const curr = snaps[snaps.length - 1], prev = snaps[snaps.length - 2];
  const dExp = curr.expired - prev.expired;
  const deltaText = dExp > 0 ? ('<b style="color:var(--expired)">+' + dExp + " overdue</b>") : '<span style="color:var(--healthy)">0 new overdue</span>';
  return "<b>" + DATA.records.length + "</b> items as of <b>" + esc(DATA.asOf) + "</b><br/>"
    + '<span style="color:var(--slate)">Last check: ' + deltaText + "</span>";
}

// ---- guided story mode step generator (hard cap <= 80 chars per step) --
function storySteps(S){
  const rs = DATA.records;
  const expired = rs.filter(r => r.days < 0);
  const nx = soonest(rs.filter(r => r.days >= 0)) || soonest(rs);

  return [
    {
      target: "mKpis",
      title: "1. Fleet Overview",
      desc: "Fleet Overview: " + rs.length + " infrastructure items across " + DATA.states.length + " states with live monitoring.",
      hint: "KPI headline metrics reflect live compliance status."
    },
    {
      target: "mKpis",
      title: "2. Immediate Risk",
      desc: expired.length
        ? "Risk Profile: " + expired.length + " overdue item(s) detected; credential rotation required."
        : "Risk Profile: Zero expired items detected across all environments.",
      hint: "Filter by 'Needs Attention' anytime to isolate these items."
    },
    {
      target: "mFocus",
      title: "3. Renewal Horizon",
      desc: nx
        ? "Timeline: Next renewal in " + fmtDays(nx.days) + " (" + fmtDate(nx.exp) + " for " + clampStr(nx.schema, 12) + ")."
        : "Timeline: No upcoming renewals in scope.",
      hint: "Hover any event point on the timeline for instant entity inspection."
    },
    {
      target: "mWhen",
      title: "4. Workload",
      desc: "Workload: Renewal tasks are grouped by quarter across 2026–2029.",
      hint: "Click any quarter bar to cross-filter detail tables across the screen."
    }
  ];
}

// ---- slicer bar -------------------------------------------------------
function renderSlicers(S){
  const activeCount = (DATA.mode === "all" && S.state ? 1 : 0)
    + (S.component ? 1 : 0)
    + (S.band && S.band !== "__urgent__" ? 1 : S.band === "__urgent__" ? 1 : 0)
    + ((S.window && S.window !== "all") ? 1 : 0);

  const q = S.q || "";

  const topRow = [];
  topRow.push('<div class="search"><input id="q" type="search" value="' + esc(q)
    + '" placeholder="Find a schema, environment or date" aria-label="Search tracked items" />'
    + (q ? '<button class="clr" type="button" data-act="drop" data-val="q" '
         + 'aria-label="Clear search">&times;</button>' : "") + "</div>");
  topRow.push('<button class="filter-toggle' + (S.showFilters ? " active" : "") + '" type="button"'
    + ' data-act="toggleFilters" data-tip="'
    + (S.showFilters ? "Collapse filter options" : "Expand filters to narrow by state, component, health or date range") + '">'
    + (S.showFilters ? "&#9650; Filters" : "&#9660; Filters")
    + (activeCount ? '<span class="filter-badge">' + activeCount + "</span>" : "")
    + "</button>");

  if (!S.showFilters) return topRow.join("");

  const drawer = ['<div class="filter-drawer">'];
  if (DATA.mode === "all"){
    const by = {}; rows(S, "state").forEach(r => by[r.state] = (by[r.state] || 0) + 1);
    drawer.push('<div class="sgroup"><label>State</label>' + DATA.states.map(s =>
      chip({ act: "state", val: s, label: s, n: by[s] || 0, on: S.state === s,
             tip: "Show only " + s })).join("") + "</div>", '<div class="vr"></div>');
  }
  const byComp = {}; rows(S, "component").forEach(r => byComp[r.component] = (byComp[r.component] || 0) + 1);
  drawer.push('<div class="sgroup"><label>Component</label>' + DATA.components.map(c =>
    chip({ act: "component", val: c, label: CODE[c], n: byComp[c] || 0, on: S.component === c,
           tip: c + " - " + DATA.componentBlurb[c] })).join("") + "</div>", '<div class="vr"></div>');
  const cb = counts(rows(S, "band"));
  drawer.push('<div class="sgroup"><label>Health</label>' + BANDS.map(b =>
    chip({ act: "band", val: b, label: b, n: cb[b], on: S.band === b, swatch: META[b].color,
           tip: META[b].label + " - " + META[b].plain })).join("") + "</div>", '<div class="vr"></div>');
  drawer.push('<div class="sgroup"><label>Dates</label>' + DATA.windows.map(w => {
    const probe = Object.assign({}, S, { window: w.id });
    return chip({ act: "window", val: w.id, label: w.label, n: rows(probe).length,
                  on: (S.window || "all") === w.id,
                  tip: w.id === "all" ? "No date limit" : "Only items in this date range" });
  }).join("") + "</div>");
  drawer.push("</div>");

  return topRow.join("") + drawer.join("");
}

// ---- KPI strip --------------------------------------------------------
function renderKpis(S){
  const base = rows(S, "band");
  const c = counts(base), total = base.length;
  const inScope = rows(S);
  const nx = soonest(inScope.filter(r => r.days >= 0)) || soonest(inScope);

  const scope = [
    DATA.mode === "all" ? (S.state || DATA.states.length + " states") : DATA.state,
    S.component ? CODE[S.component] : DATA.components.length + " components",
  ];
  if (S.environment) scope.push(S.environment);

  const hasExpired = c["Expired"] > 0;
  const snaps = getScopeSnapshots(S);
  const prevSnap = snaps.length >= 2 ? snaps[snaps.length - 2] : null;

  const trackedSpark = sparklineSvg(snaps.map(s => s.tracked), T.accent);
  const trackedTrend = trendDelta(total, prevSnap ? prevSnap.tracked : null, "tracked");
  const tiles = [
    '<button class="kpi" type="button" data-act="band" data-val="" aria-pressed="'
    + (S.band ? "false" : "true") + '"'
    + (!hasExpired ? ' data-dom="1"' : "")
    + ' data-tip="Show every health status">'
    + '<div class="kpi-row1"><div class="v">' + total + '</div>' + trackedSpark + '</div>'
    + '<div class="k">Tracked items</div>'
    + '<div class="kpi-row2"><div class="s">' + scope.map(esc).join(" &middot; ") + '</div>' + trackedTrend + '</div>'
    + '</button>'
  ];

  BANDS.forEach(b => {
    const pct = total ? Math.round(c[b] / total * 100) + "% of " + total : "--";
    const isDom = b === "Expired" && hasExpired;
    const key = b.toLowerCase();
    const bSpark = sparklineSvg(snaps.map(s => s[key] || 0), META[b].color);
    const bTrend = trendDelta(c[b], prevSnap ? (prevSnap[key] !== undefined ? prevSnap[key] : null) : null, key);

    let soWhatHtml = "";
    if (b === "Expired" && c["Expired"] > 0){
      soWhatHtml = '<div class="so-what" title="So what: Stale credentials violate policy. Now what: Rotate today.">So what: Stale credentials. Now what: Rotate today.</div>';
    } else if (b === "Critical" && c["Critical"] > 0){
      soWhatHtml = '<div class="so-what" title="So what: Expiry within 15 days. Now what: Stage renewal workflow.">So what: Expiry in 15d. Now what: Stage renewal workflow.</div>';
    }

    tiles.push('<button class="kpi" type="button" data-act="band" data-val="' + esc(b)
      + '" aria-pressed="' + (S.band === b ? "true" : "false")
      + '" style="--val:' + META[b].color + ";--edge:" + META[b].color + '"'
      + (isDom ? ' data-dom="1"' : "")
      + ' data-tip="' + esc(META[b].label + " - " + META[b].plain + ". Click to show only these.")
      + '">'
      + '<div class="kpi-row1"><div class="v">' + c[b] + '</div>' + bSpark + '</div>'
      + '<div class="k">' + esc(META[b].label) + '</div>'
      + '<div class="kpi-row2"><div class="s">' + esc(pct) + '</div>' + bTrend + '</div>'
      + soWhatHtml
      + '</button>');
  });

  const band = nx ? nx.band : "Healthy";
  const nxSpark = sparklineSvg(snaps.map(s => Math.max(0, s.soonest_days || 0)), META[band].color);
  tiles.push('<div class="kpi flat" style="--val:' + META[band].color + ";--edge:" + META[band].color
    + '"' + (nx ? ' data-tip="' + esc(nx.schema + " in " + nx.environment + " - " + nx.component) + '"' : "")
    + '><div class="kpi-row1"><div class="v">' + esc(nx ? fmtDays(nx.days) : "--") + '</div>' + nxSpark + '</div>'
    + '<div class="k">Next expiry</div><div class="s">'
    + esc(nx ? fmtDate(nx.exp) : "nothing in scope") + "</div></div>");

  return tiles.join("");
}

// ---- component cards --------------------------------------------------
function renderComps(S){
  const base = rows(S, "component");
  return DATA.components.map(comp => {
    const sub = base.filter(r => r.component === comp);
    const c = counts(sub), nx = soonest(sub);
    const band = worstBand(new Set(sub.map(r => r.band)));
    const on = S.component === comp;
    return '<button class="cc" type="button" data-act="component" data-val="' + esc(comp)
      + '" data-hl-comp="' + esc(comp)
      + '" aria-pressed="' + (on ? "true" : "false") + '" style="--val:' + META[band].color
      + '" data-tip="' + esc((on ? "Click again to clear. " : "Click to focus ")
      + comp + " (" + CODE[comp] + ") — " + DATA.componentBlurb[comp])
      + '"><div class="head-row"><span class="code">' + esc(CODE[comp]) + '</span><span class="nm">'
      + esc(comp) + '</span></div>' + meter(c)
      + '<div class="meter-label">Health distribution</div>'
      + '<div class="foot"><span class="cnt">' + sub.length + "<em>item"
      + (sub.length === 1 ? "" : "s") + '</em></span><span class="nx">'
      + (nx ? "next <b>" + esc(fmtDays(nx.days)) + "</b><br />" + esc(fmtDate(nx.exp))
            : "nothing in scope") + "</span></div></button>";
  }).join("");
}

// ---- focus panel ------------------------------------------------------
const FOCUS_VIEWS = [
  { id: "horizon", label: "Timeline", tip: "Every item placed on a time axis" },
  { id: "envs", label: "Environments", tip: "Environments in current selection" },
  { id: "coverage", label: "Coverage", tip: "State/Env vs Component coverage" }
];

function focusHint(S){
  if (S.focus === "envs")
    return S.component ? "Environments in " + CODE[S.component] + " - click one to focus it"
                       : "Every environment in scope - click one to focus it";
  if (S.focus === "coverage")
    return (DATA.mode === "all" ? "State" : "Environment") + " against component, "
      + "labelled with time to the soonest expiry";
  const rs = rows(S), nx = soonest(rs.filter(r => r.days >= 0)) || soonest(rs);
  if (!nx) return "Nothing in scope";
  return (nx.days < 0 ? "Oldest overdue item lapsed " + fmtDays(nx.days)
                      : "Next expiry " + fmtDaysLong(nx.days)) + " - " + fmtDate(nx.exp);
}

function renderFocus(S){
  if (S.focus === "envs") return renderEnvs(S);
  if (S.focus === "coverage") return renderCoverage(S);
  return renderHorizon(S);
}

// The signature visual: Area gradient timeline with Today anchor & reference guides
const GRID = [[30, "30d"], [90, "90d"], [180, "6mo"], [365, "1yr"],
              [730, "2yr"], [1095, "3yr"], [1460, "4yr"], [1825, "5yr"]];

function renderHorizon(S){
  const rs = rows(S);
  if (!rs.length)
    return voidState("Nothing on the timeline",
      "No tracked item matches the current filters.",
      '<button class="chip" type="button" data-act="reset">Clear all filters</button>');

  const W = 1000, H = 210, base = 158, top = 20, x0 = 78, x1 = 988, gut = 32;
  const future = rs.filter(r => r.days >= 0), past = rs.filter(r => r.days < 0);
  const horizon = Math.max(90, ...future.map(r => r.days), 90);
  const root = Math.sqrt(horizon);
  const px = d => x0 + (Math.sqrt(Math.max(d, 0)) / root) * (x1 - x0);

  const o = ['<svg class="chart" viewBox="0 0 ' + W + " " + H
    + '" preserveAspectRatio="none" role="img" aria-label="Expiry timeline">'];

  o.push("<defs>"
    + '<linearGradient id="areaGrad" x1="0%" y1="0%" x2="0%" y2="100%">'
    + '<stop offset="0%" stop-color="' + T.accent + '" stop-opacity="0.32"/>'
    + '<stop offset="100%" stop-color="' + T.accent + '" stop-opacity="0.02"/>'
    + "</linearGradient>"
    + '<linearGradient id="critGrad" x1="0%" y1="0%" x2="0%" y2="100%">'
    + '<stop offset="0%" stop-color="' + META.Critical.color + '" stop-opacity="0.28"/>'
    + '<stop offset="100%" stop-color="' + META.Critical.color + '" stop-opacity="0.03"/>'
    + "</linearGradient>"
    + "</defs>");

  o.push('<rect x="' + x0.toFixed(1) + '" y="' + top + '" width="' + (px(CRIT) - x0).toFixed(1)
    + '" height="' + (base - top).toFixed(1) + '" fill="url(#critGrad)"/>');
  o.push('<rect x="' + px(CRIT).toFixed(1) + '" y="' + top + '" width="'
    + (px(WARN) - px(CRIT)).toFixed(1) + '" height="' + (base - top).toFixed(1)
    + '" fill="' + META.Warning.tint + '"/>');

  if (past.length){
    o.push('<rect x="' + (gut - 17) + '" y="' + top + '" width="34" height="'
      + (base - top).toFixed(1) + '" fill="' + META.Expired.tint + '" stroke="'
      + META.Expired.color + '" stroke-opacity=".45" stroke-width="1" rx="3"/>');
  }

  o.push('<line x1="' + (gut - 21) + '" y1="' + base + '" x2="' + x1 + '" y2="' + base
    + '" stroke="' + T.rule + '" stroke-width="1"/>');

  const guideLines = [
    [30, "30d Warning", META.Warning.color],
    [90, "90d Quarter", T.accent],
    [365, "1yr Horizon", T.slate]
  ];
  guideLines.forEach(([d, lbl, clr]) => {
    if (d <= horizon * 0.95){
      const gx = px(d);
      o.push('<line x1="' + gx.toFixed(1) + '" y1="' + (top + 2) + '" x2="' + gx.toFixed(1)
        + '" y2="' + base + '" stroke="' + clr + '" stroke-opacity="0.38" stroke-width="1" stroke-dasharray="2 3"/>');
      o.push('<text x="' + gx.toFixed(1) + '" y="' + (top + 10) + '" fill="' + clr + '" font-size="9"'
        + ' font-weight="600" text-anchor="middle" opacity="0.8">' + esc(lbl) + "</text>");
    }
  });

  GRID.filter(g => g[0] <= horizon * 0.985).concat(horizon > 90 ? [[horizon, fmtDays(horizon)]] : [])
    .forEach(g => {
      const x = px(g[0]);
      o.push('<line x1="' + x.toFixed(1) + '" y1="' + (top + 14) + '" x2="' + x.toFixed(1)
        + '" y2="' + base + '" stroke="' + T.ruleSoft + '" stroke-width="1" stroke-dasharray="2 4"/>');
      o.push('<text x="' + x.toFixed(1) + '" y="' + (base + 16) + '" fill="' + T.mute + '" font-size="11"'
        + ' text-anchor="middle">' + esc(g[1]) + "</text>");
    });

  o.push('<line x1="' + x0 + '" y1="' + (top - 8) + '" x2="' + x0 + '" y2="' + (base + 5)
    + '" stroke="' + T.accent + '" stroke-width="1.8"/>');
  o.push('<circle cx="' + x0 + '" cy="' + (top - 8) + '" r="3" fill="' + T.accent + '"/>');
  o.push('<circle cx="' + x0 + '" cy="' + (base + 5) + '" r="2.5" fill="' + T.accent + '"/>');
  o.push('<text x="' + (x0 + 6) + '" y="' + (top - 1) + '" fill="' + T.accent + '" font-size="11"'
    + ' font-weight="700" letter-spacing="1">TODAY</text>');

  const cols = new Map();
  future.forEach(r => {
    const k = Math.round(px(r.days) / 7);
    if (!cols.has(k)) cols.set(k, []);
    cols.get(k).push(r);
  });
  if (past.length) cols.set(-999, past.slice());

  const STEP = 6.4, MARK = 5.2, ceiling = top + 8;
  cols.forEach((bucket, k) => {
    const cx = k === -999 ? gut : k * 7;
    const room = Math.floor((base - 5 - ceiling) / STEP);
    bucket.sort((a, b) => a.days - b.days).forEach((r, i) => {
      if (i >= room) return;
      const y = base - 5 - i * STEP;
      o.push('<rect x="' + (cx - MARK / 2).toFixed(1) + '" y="' + y.toFixed(1) + '" width="' + MARK
        + '" height="' + (STEP - 1.4).toFixed(1) + '" rx="1.2" fill="' + META[r.band].color
        + '" data-hl-comp="' + esc(r.component) + '" data-hl-env="' + esc(r.environment) + '"'
        + ' data-tip="' + esc(r.schema + " &middot; " + r.environment + " &middot; " + r.component
        + " &middot; " + fmtDate(r.exp) + " (" + fmtDaysLong(r.days) + ")") + '"/>');
    });
    if (bucket.length > room)
      o.push('<text x="' + cx.toFixed(1) + '" y="' + (ceiling - 2) + '" fill="' + T.slate + '"'
        + ' font-size="10" text-anchor="middle" data-tip="' + (bucket.length - room)
        + ' more in this window">+' + (bucket.length - room) + "</text>");
  });

  if (past.length)
    o.push('<text x="' + gut + '" y="' + (base + 16) + '" fill="' + META.Expired.color
      + '" font-size="11" font-weight="600" text-anchor="middle">past</text>');

  o.push("</svg>");
  return o.join("") + '<div class="legend"><span style="color:var(--slate)">Time runs on a '
    + "square-root scale. Column height is density of items expiring in each window.</span></div>";
}

function renderEnvs(S){
  const base = rows(S, "environment");
  const groups = new Map();
  base.forEach(r => {
    if (!groups.has(r.environment))
      groups.set(r.environment, { n: 0, nos: new Set(), bands: new Set(), min: Infinity, rank: r.envRank });
    const g = groups.get(r.environment);
    g.n++; g.nos.add(r.envNo); g.bands.add(r.band); g.min = Math.min(g.min, r.days);
  });
  if (!groups.size)
    return voidState("No envs", "Clear a filter to see environments.",
      '<button class="chip" type="button" data-act="reset">Clear all filters</button>');

  const items = [...groups.entries()].sort((a, b) => a[1].rank - b[1].rank);
  return '<div class="envs">' + items.map(([env, g]) => {
    const band = worstBand(g.bands), on = S.environment === env;
    return '<button class="ec" type="button" data-act="environment" data-val="' + esc(env)
      + '" data-hl-env="' + esc(env) + '"'
      + '" aria-pressed="' + (on ? "true" : "false") + '" style="--val:' + META[band].color
      + '" data-tip="' + esc(env + " - " + (DATA.envBlurb[env] || env) + ". " + g.n
      + " item(s), soonest " + fmtDaysLong(g.min) + ". "
      + (on ? "Click again to clear." : "Click to focus environments."))
      + '"><div class="en">' + esc(env) + '</div><div class="eb">'
      + esc(DATA.envBlurb[env] || "") + '</div><div class="er"><span>' + g.n + " item"
      + (g.n === 1 ? "" : "s") + "</span><b>" + esc(fmtDays(g.min)) + "</b></div></button>";
  }).join("") + "</div>" + legend();
}

function renderCoverage(S){
  const rs = rows(S);
  if (!rs.length)
    return voidState("Nothing to map", "Adjust filters to show coverage.",
      '<button class="chip" type="button" data-act="reset">Clear all filters</button>');

  const rowKey = DATA.mode === "all" ? "state" : "environment";
  const cells = new Map(), order = new Map();
  rs.forEach(r => {
    const k = r[rowKey] + "|" + r.component;
    if (!cells.has(k)) cells.set(k, { bands: new Set(), min: Infinity, n: 0 });
    const c = cells.get(k);
    c.bands.add(r.band); c.min = Math.min(c.min, r.days); c.n++;
    if (!order.has(r[rowKey]))
      order.set(r[rowKey], { rank: rowKey === "state" ? DATA.states.indexOf(r.state) : r.envRank, nos: new Set() });
    order.get(r[rowKey]).nos.add(r.envNo);
  });

  const head = '<tr><th class="rh">' + (rowKey === "state" ? "state" : "env") + "</th>"
    + DATA.components.map(c => '<th data-tip="' + esc(c) + '">' + esc(CODE[c]) + "</th>").join("") + "</tr>";

  const body = [...order.entries()].sort((a, b) => a[1].rank - b[1].rank).map(([key, meta]) => {
    const tds = DATA.components.map(comp => {
      const c = cells.get(key + "|" + comp);
      if (!c) return '<td><div class="c void" data-tip="' + esc(key + " / " + CODE[comp]
        + ": not tracked.") + '">&middot;</div></td>';
      const band = worstBand(c.bands), solid = band !== "Healthy";
      return '<td><button class="c" type="button" data-act="cell" data-val="'
        + esc(key + "|" + CODE[comp]) + '" style="background:' + (solid ? META[band].color : META[band].tint)
        + ";color:" + (solid ? "#fff" : META[band].color) + ";border-color:" + META[band].color
        + (solid ? "" : ";border-color:rgba(0,0,0,0)") + '" data-tip="'
        + esc(key + " / " + comp + ": " + c.n + " item(s), soonest " + fmtDaysLong(c.min)
        + ". Click to filter.") + '">' + esc(fmtDays(c.min)) + "</button></td>";
    }).join("");
    const sub = rowKey === "state" ? "" : ' <u>' + esc([...meta.nos].sort((a, b) => a - b).join("/")) + "</u>";
    return "<tr><td class=\"rl\">" + esc(key) + sub + "</td>" + tds + "</tr>";
  }).join("");

  return '<table class="mx">' + head + body + "</table>" + legend();
}

// ---- detail table ----------------------------------------------------
const COLS = [
  { id: "env", sort: "env", label: "Environment", w: "15%" },
  { id: "schema", sort: "schema", label: "Schema Name", w: "31%" },
  { id: "exp", sort: "soon", label: "Expiry Date", w: "18%" },
  { id: "health", sort: "health", label: "Health Status", w: "20%" },
  { id: "left", sort: "soon", label: "Time Left", w: "16%", right: true }
];

const SUM_COLS = [
  { label: "Environment", w: "17%" },
  { label: "Component", w: "26%" },
  { label: "Items", w: "12%", right: true },
  { label: "Soonest Expiry", w: "23%" },
  { label: "Health Status", w: "22%" }
];

function thead(cols, S){
  return cols.map((c, i) => {
    if (!c.sort) return "<th" + (c.right ? ' class="r"' : "") + ' style="width:' + c.w
      + '"><button type="button" tabindex="-1" style="cursor:default">' + esc(c.label)
      + "</button></th>";
    const active = S.sort === c.sort || (c.sort === "soon" && S.sort === "late");
    const owner = cols.findIndex(x => x.sort === c.sort) === i;
    const arrow = !(active && owner) ? "" : (S.sort === "late" ? "&#9660;" : "&#9650;");
    return "<th" + (c.right ? ' class="r"' : "") + ' style="width:' + c.w + '"'
      + (active && owner ? ' aria-sort="' + (S.sort === "late" ? "descending" : "ascending") + '"' : "")
      + '><button type="button" data-act="sort" data-val="' + c.sort
      + '" data-tip="Sort by ' + esc(c.label.toLowerCase())
      + (c.sort === "soon" ? ". Click again to reverse" : "") + '">' + esc(c.label)
      + "<s>" + arrow + "</s></button></th>";
  }).join("");
}

function renderTable(S){
  if (S.tview === "summary") return renderSummary(S);

  const all = sorted(rows(S), S.sort);
  if (!all.length)
    return voidState("No items match these filters",
      "Remove a filter chip in the header, or clear the search box, to bring rows back.",
      '<button class="chip" type="button" data-act="reset">Clear all filters</button>');

  const per = Math.max(1, S.rows);
  const pages = Math.max(1, Math.ceil(all.length / per));
  const page = Math.min(S.page, pages - 1);
  const slice = all.slice(page * per, page * per + per);

  const body = slice.map(r => {
    let barPct = 10;
    if (r.days < 0) barPct = 100;
    else if (r.days <= CRIT) barPct = Math.round(80 + (1 - r.days / CRIT) * 20);
    else if (r.days <= WARN) barPct = Math.round(45 + (1 - (r.days - CRIT) / (WARN - CRIT)) * 35);
    else barPct = Math.max(10, Math.round((1 - Math.min(r.days, 730) / 730) * 40));

    return '<tr class="' + (r.band === "Healthy" ? "" : "hot") + '"'
      + ' data-hl-comp="' + esc(r.component) + '"'
      + ' data-hl-env="' + esc(r.environment) + '"'
      + ' data-hl-quarter="' + esc(r.quarter) + '">'
      + '<td><span class="env-pill" data-tip="' + esc(DATA.envBlurb[r.environment] || r.environment)
      + '">' + esc(r.environment) + "</span></td>"
      + '<td><span class="schema">' + esc(r.schema) + "</span>"
      + (r.edited ? '<span class="tag" data-tip="Changed in Manage">EDITED</span>' : "")
      + ' <span class="sub">'
      + (DATA.mode === "all" ? esc(r.state) + " &middot; " : "")
      + esc(r.component) + "</span></td>"
      + '<td class="mono">' + esc(fmtDate(r.exp)) + "</td>"
      + "<td>" + dot(r.band) + "</td>"
      + '<td class="r mono time-cell" data-tip="' + esc(fmtDaysLong(r.days)) + '">'
      + '<div class="data-bar" style="width:' + barPct + '%;background:' + META[r.band].tint + ';border-right:2px solid ' + META[r.band].color + '"></div>'
      + '<span class="time-val" style="color:' + META[r.band].color + ';font-weight:600">' + esc(fmtDays(r.days)) + "</span></td></tr>";
  }).join("");

  return '<table class="tbl"><thead><tr>' + thead(COLS, S)
    + "</tr></thead><tbody>" + body + "</tbody></table>";
}

function summaryRows(S){
  const groups = new Map();
  rows(S).forEach(r => {
    const k = r.environment + "|" + CODE[r.component];
    if (!groups.has(k))
      groups.set(k, { environment: r.environment, component: r.component, n: 0,
                      bands: new Set(), days: Infinity, exp: r.exp,
                      envRank: r.envRank, compRank: r.compRank, envNo: r.envNo });
    const g = groups.get(k);
    g.n++; g.bands.add(r.band);
    if (r.days < g.days){ g.days = r.days; g.exp = r.exp; }
  });
  const out = [...groups.values()];
  out.forEach(g => { g.band = worstBand(g.bands); g.schema = g.environment + " " + CODE[g.component]; });
  return sorted(out, S.sort);
}

function renderSummary(S){
  const all = summaryRows(S);
  if (!all.length)
    return voidState("Nothing to summarise",
      "No tracked item matches the current filters.",
      '<button class="chip" type="button" data-act="reset">Clear all filters</button>');

  const per = Math.max(1, S.rows);
  const pages = Math.max(1, Math.ceil(all.length / per));
  const page = Math.min(S.page, pages - 1);
  const slice = all.slice(page * per, page * per + per);

  const body = slice.map(g =>
    '<tr class="' + (g.band === "Healthy" ? "" : "hot") + '"'
      + ' data-hl-comp="' + esc(g.component) + '"'
      + ' data-hl-env="' + esc(g.environment) + '">'
      + '<td><button class="env-pill" type="button" data-act="pair" data-val="'
      + esc(g.environment + "|" + CODE[g.component]) + '" data-tip="'
      + esc("Filter to " + g.environment + " / " + g.component)
      + '">' + esc(g.environment) + "</button></td>"
      + '<td><span class="schema">' + esc(CODE[g.component]) + '</span> <span class="sub">'
      + esc(g.component) + "</span></td>"
      + '<td class="r mono">' + g.n + "</td>"
      + '<td class="mono">' + esc(fmtDate(g.exp)) + ' <span class="sub">' + esc(fmtDays(g.days))
      + "</span></td>"
      + "<td>" + dot(g.band) + "</td></tr>").join("");

  return '<table class="tbl"><thead><tr>' + thead(SUM_COLS, S)
    + "</tr></thead><tbody>" + body + "</tbody></table>";
}

function visibleCount(S){ return S.tview === "summary" ? summaryRows(S).length : rows(S).length; }

function renderPager(S){
  const shown = visibleCount(S);
  const total = DATA.records.length;
  if (!shown) return "";
  const per = Math.max(1, S.rows);
  const pages = Math.max(1, Math.ceil(shown / per));
  const page = Math.min(S.page, pages - 1);
  const from = page * per + 1, to = Math.min(shown, (page + 1) * per);
  const noun = S.tview === "summary" ? "pairing" : "item";

  return '<button type="button" data-act="page" data-val="prev" ' + (page ? "" : "disabled")
    + ' aria-label="Previous page" data-tip="Previous page (left arrow key)">&#8249;</button>'
    + '<button type="button" data-act="page" data-val="next" ' + (page < pages - 1 ? "" : "disabled")
    + ' aria-label="Next page" data-tip="Next page (right arrow key)">&#8250;</button>'
    + '<span class="of">' + from + "&ndash;" + to + " of " + shown + "</span>"
    + '<span style="color:var(--mute)">' + esc(noun) + (shown === 1 ? "" : "s")
    + (S.tview === "summary" ? " covering " + rows(S).length + " items"
       : (shown === total ? ", all tracked" : ", filtered from " + total))
    + '</span><span class="sp"></span>'
    + '<span style="color:var(--mute)">Page ' + (page + 1) + " of " + pages + "</span>";
}

function quarters(n){
  const out = [];
  let y = DATA.year, q = DATA.quarter;
  for (let i = 0; i < n; i++){ out.push("Q" + q + " " + y); if (++q > 4){ q = 1; y++; } }
  return out;
}
function qOrd(label){
  const m = /^Q([1-4]) (\d{4})$/.exec(label || "");
  if (!m) return null;
  return +m[2] * 4 + (+m[1] - 1);
}

function renderWhen(S){
  const rs = rows(S);
  if (!rs.length)
    return voidState("No renewal work in scope",
      "Nothing matches the current filters, so there is no workload to plot.",
      '<button class="chip" type="button" data-act="reset">Clear all filters</button>');

  const labels = quarters(12);
  const first = qOrd(labels[0]), last = qOrd(labels[labels.length - 1]);
  const byQ = {}, bandOf = {};
  let before = 0, after = 0;
  rs.forEach(r => {
    const o = qOrd(r.quarter);
    if (o === null) return;
    if (o < first) { before++; return; }
    if (o > last)  { after++;  return; }
    byQ[r.quarter] = (byQ[r.quarter] || 0) + 1;
    const cur = bandOf[r.quarter];
    bandOf[r.quarter] = cur ? worstBand(new Set([cur, r.band])) : r.band;
  });
  const buckets = labels.map(l => [l, byQ[l] || 0, bandOf[l] || "Healthy"]);

  const W = 1000, H = 210, top = 18, base = 162;
  const peak = Math.max(1, ...buckets.map(b => b[1]));
  const slot = W / buckets.length, bw = Math.min(slot * 0.58, 44);
  const share = S.qty === "share";

  const o = ['<svg class="chart" viewBox="0 0 ' + W + " " + H
    + '" preserveAspectRatio="none" role="img" aria-label="Workload by quarter">'];

  o.push('<line x1="0" y1="' + base + '" x2="' + W + '" y2="' + base
    + '" stroke="' + T.rule + '" stroke-width="1"/>');

  buckets.forEach((b, i) => {
    const cx = slot * (i + 0.5), n = b[1];
    if (n){
      const h = (n / peak) * (base - top);
      const label = share ? Math.round(n / rs.length * 100) + "%" : n;
      o.push('<rect x="' + (cx - bw / 2).toFixed(1) + '" y="' + (base - h).toFixed(1) + '" width="'
        + bw.toFixed(1) + '" height="' + h.toFixed(1) + '" rx="2" fill="' + META[b[2]].color
        + '" fill-opacity=".88" data-hl-quarter="' + esc(b[0]) + '"'
        + ' data-tip="' + esc(b[0] + ": " + n + " item" + (n === 1 ? "" : "s") + " ("
        + Math.round(n / rs.length * 100) + "%), worst status " + b[2]) + '"/>');
      o.push('<text x="' + cx.toFixed(1) + '" y="' + (base - h - 5).toFixed(1)
        + '" fill="' + T.ink + '" font-size="12" font-weight="600" text-anchor="middle">'
        + esc(label) + "</text>");
    }
    o.push('<text x="' + cx.toFixed(1) + '" y="' + (base + 16) + '" fill="' + T.mute + '" font-size="11"'
      + ' text-anchor="middle">' + esc(b[0]) + "</text>");
  });
  o.push("</svg>");

  const notes = [];
  if (before) notes.push(before + " already lapsed before " + labels[0]);
  if (after) notes.push(after + " land after " + labels[labels.length - 1]);
  notes.push("Bar colour is the most urgent item in that quarter");
  return o.join("") + '<div class="legend"><span style="color:var(--slate)">'
    + notes.map(esc).join(" &middot; ") + ".</span></div>";
}

function whenHint(S){
  const n = rows(S).length;
  return n + " item" + (n === 1 ? "" : "s") + " grouped by the quarter they expire in";
}

function tableHint(S){
  const n = rows(S).length;
  const bits = [];
  if (DATA.mode === "all" && S.state) bits.push(S.state);
  else if (DATA.mode === "state") bits.push(DATA.state);
  bits.push(S.component ? CODE[S.component] : "all components");
  bits.push(S.environment ? S.environment : "all environments");
  const lead = S.tview === "summary"
    ? summaryRows(S).length + " pairings across " + n + " items"
    : n + " of " + DATA.records.length + " items";
  return lead + " &mdash; " + bits.join(" &middot; ");
}

function whereLabel(S){
  if (DATA.mode === "state") return DATA.state;
  return S.state ? S.state : "All states";
}
/*==ENGINE-END==*/

/* ======================================================================
   DOM layer.
   ====================================================================== */
const S = {
  state: DATA.mode === "state" ? DATA.state : null,
  component: null, environment: null, band: null, window: "all", q: "",
  focus: "horizon", sort: "soon", page: 0, rows: 9, tview: "detail", qty: "count", view: "all",
  showFilters: false,
  storyStep: null,
  storyTimer: null,
  storyPaused: false
};

const $ = id => document.getElementById(id);
const MOUNTS = {};
["mWhere", "mNarrative", "mCrumbs", "mViews", "mAsOf", "mSlicers", "mKpis", "mComps", "mFocusSeg",
 "mFocusHint", "mFocus", "mTableHint", "mTableSeg", "mTable", "mPager", "mWhenHint",
 "mWhenSeg", "mWhen", "mStoryModal", "mShell"].forEach(k => MOUNTS[k] = $(k));

const last = {};
function put(key, html){
  if (!MOUNTS[key]) return;
  if (last[key] === html) return;
  last[key] = html;
  MOUNTS[key].innerHTML = html;
}

function animateNumbers(){
  // Numbers render instantaneously and cleanly without jitter or stripping duration suffixes
}

function apply(){
  put("mWhere", esc(whereLabel(S)));
  put("mNarrative", renderNarrative(S));
  put("mCrumbs", renderCrumbs(S));
  put("mViews", renderViews(S));
  put("mAsOf", renderSinceVisit(DATA.snapshots));
  const focused = document.activeElement === $("q");
  if (!focused) put("mSlicers", renderSlicers(S));
  else { last.mSlicers = null; }
  put("mKpis", renderKpis(S));
  put("mComps", renderComps(S));
  put("mFocusSeg", seg("focus", FOCUS_VIEWS, S.focus));
  put("mFocusHint", focusHint(S));
  put("mFocus", renderFocus(S));
  put("mTableHint", tableHint(S));
  put("mTableSeg", seg("tview", [
    { id: "detail", label: "Detail", tip: "One row per item" },
    { id: "summary", label: "Summary", tip: "Environment/Comp groupings" }
  ], S.tview));
  put("mTable", renderTable(S));
  put("mPager", renderPager(S));
  put("mWhenHint", whenHint(S));
  put("mWhenSeg", seg("qty", [
    { id: "count", label: "Count", tip: "Number of items" },
    { id: "share", label: "Share", tip: "Percentage of total" }
  ], S.qty));
  put("mWhen", renderWhen(S));

  if (S.storyStep !== null) renderStoryModal();
  animateNumbers();
}

function toggle(key, value){
  S[key] = S[key] === value ? null : value;
  S.view = null;
  S.page = 0;
}

// ---- Story Mode Controller --------------------------------------------
function startStory(){
  S.storyStep = 0;
  S.storyPaused = false;
  renderStoryModal();
  advanceStoryTimer();
}

function stopStory(){
  if (S.storyTimer) clearTimeout(S.storyTimer);
  S.storyStep = null;
  S.storyTimer = null;
  document.querySelectorAll(".story-spotlight").forEach(el => el.classList.remove("story-spotlight"));
  const modal = $("mStoryModal");
  if (modal){ modal.classList.remove("on"); modal.innerHTML = ""; }
}

function nextStory(){
  const steps = storySteps(S);
  if (S.storyStep < steps.length - 1){
    S.storyStep++;
    renderStoryModal();
    advanceStoryTimer();
  } else {
    stopStory();
  }
}

function prevStory(){
  if (S.storyStep > 0){
    S.storyStep--;
    renderStoryModal();
    advanceStoryTimer();
  }
}

function toggleStoryPause(){
  S.storyPaused = !S.storyPaused;
  if (S.storyPaused){
    if (S.storyTimer) clearTimeout(S.storyTimer);
  } else {
    advanceStoryTimer();
  }
  renderStoryModal();
}

function advanceStoryTimer(){
  if (S.storyTimer) clearTimeout(S.storyTimer);
  if (S.storyPaused) return;
  S.storyTimer = setTimeout(() => {
    nextStory();
  }, 4000);
}

function renderStoryModal(){
  const modal = $("mStoryModal");
  if (!modal || S.storyStep === null) return;
  const steps = storySteps(S);
  const step = steps[S.storyStep];
  if (!step) return;

  document.querySelectorAll(".story-spotlight").forEach(el => el.classList.remove("story-spotlight"));
  const targetEl = $(step.target);
  if (targetEl) targetEl.classList.add("story-spotlight");

  modal.classList.add("on");
  modal.innerHTML = '<div class="story-backdrop" data-act="stopStory"></div>'
    + '<div class="story-box">'
    + '<div class="story-head">'
    + '<span class="story-badge">' + esc(step.title) + '</span>'
    + '<div class="story-ctrls">'
    + '<button type="button" data-act="prevStory"' + (S.storyStep === 0 ? " disabled" : "") + '>&#8249; Back</button>'
    + '<button type="button" data-act="pauseStory">' + (S.storyPaused ? '&#9654; Resume' : '&#10074;&#10074; Pause') + '</button>'
    + '<button type="button" data-act="nextStory">' + (S.storyStep === steps.length - 1 ? 'Finish &#10003;' : 'Next &#8250;') + '</button>'
    + '<button type="button" data-act="stopStory" style="margin-left:4px" aria-label="Close story">&times;</button>'
    + '</div></div>'
    + '<div class="story-desc">' + esc(step.desc) + '</div>'
    + '<div class="story-progress"><div class="story-bar" style="width:' + ((S.storyStep + 1) / steps.length * 100) + '%"></div></div>'
    + '</div>';
}

function act(name, value){
  switch (name){
    case "startStory": startStory(); return;
    case "stopStory": stopStory(); return;
    case "nextStory": nextStory(); return;
    case "prevStory": prevStory(); return;
    case "pauseStory": toggleStoryPause(); return;
    case "state":
      toggle("state", value);
      S.component = null; S.environment = null;
      break;
    case "component":
      toggle("component", value);
      S.environment = null;
      S.focus = S.component ? "envs" : "horizon";
      break;
    case "environment":
      toggle("environment", value);
      if (S.environment) S.focus = "horizon";
      break;
    case "band":
      S.band = value === "" ? null : (S.band === value ? null : value);
      S.view = null; S.page = 0;
      break;
    case "window":
      S.window = (S.window === value && value !== "all") ? "all" : value;
      S.view = null; S.page = 0;
      break;
    case "cell": {
      const [key, code] = value.split("|");
      const comp = NAME_OF[code];
      if (DATA.mode === "all") S.state = key; else S.environment = key;
      S.component = comp; S.focus = "horizon"; S.view = null; S.page = 0;
      break;
    }
    case "pair": {
      const [env, code] = value.split("|");
      S.environment = env; S.component = NAME_OF[code];
      S.focus = "horizon"; S.view = null; S.page = 0;
      break;
    }
    case "drop":
      if (value === "q"){ S.q = ""; const box = $("q"); if (box) box.value = ""; }
      else if (value === "window") S.window = "all";
      else S[value] = null;
      S.view = null; S.page = 0;
      break;
    case "reset":
      Object.assign(S, {
        state: DATA.mode === "state" ? DATA.state : null, component: null, environment: null,
        band: null, window: "all", q: "", focus: "horizon", sort: "soon", page: 0, view: "all"
      });
      { const box = $("q"); if (box) box.value = ""; }
      break;
    case "view": {
      const bm = DATA.bookmarks.find(b => b.id === value);
      if (!bm) break;
      Object.assign(S, bm.set);
      if (DATA.mode === "state") S.state = DATA.state;
      if ("q" in bm.set){ const box = $("q"); if (box) box.value = bm.set.q; }
      S.view = value; S.page = 0;
      break;
    }
    case "focus": S.focus = value; break;
    case "tview": S.tview = value; S.page = 0; break;
    case "qty": S.qty = value; break;
    case "toggleFilters": S.showFilters = !S.showFilters; break;
    case "sort":
      S.sort = (S.sort === "soon" && value === "soon") ? "late" : value;
      S.page = 0;
      break;
    case "page": {
      const per = Math.max(1, S.rows);
      const pages = Math.max(1, Math.ceil(visibleCount(S) / per));
      S.page = Math.min(pages - 1, Math.max(0, S.page + (value === "next" ? 1 : -1)));
      break;
    }
  }
  apply();
}

document.addEventListener("click", e => {
  const hit = e.target.closest("[data-act]");
  if (!hit || hit.disabled) return;
  e.preventDefault();
  act(hit.dataset.act, hit.dataset.val || "");
});

document.addEventListener("input", e => {
  if (e.target.id !== "q") return;
  S.q = e.target.value; S.view = null; S.page = 0;
  apply();
});
document.addEventListener("focusout", e => {
  if (e.target.id === "q") apply();
});

document.addEventListener("keydown", e => {
  if (e.target.tagName === "INPUT") return;
  if (e.key === "Escape"){
    if (S.storyStep !== null){ stopStory(); return; }
    act("reset", "");
  }
  if (e.key === "ArrowLeft") act("page", "prev");
  if (e.key === "ArrowRight") act("page", "next");
});

// ---- Cross-Visual Highlighting Listener ------------------------------
const shellEl = $("mShell");
if (shellEl){
  shellEl.addEventListener("mouseover", e => {
    const el = e.target.closest("[data-hl-comp], [data-hl-env], [data-hl-quarter]");
    if (!el){
      if (shellEl.dataset.hlActive){
        delete shellEl.dataset.hlActive;
        shellEl.querySelectorAll("[data-hl-match]").forEach(m => delete m.dataset.hlMatch);
      }
      return;
    }
    const comp = el.dataset.hlComp, env = el.dataset.hlEnv, qtr = el.dataset.hlQuarter;
    shellEl.dataset.hlActive = "true";
    shellEl.querySelectorAll("[data-hl-comp], [data-hl-env], [data-hl-quarter]").forEach(node => {
      const mComp = comp && node.dataset.hlComp === comp;
      const mEnv = env && node.dataset.hlEnv === env;
      const mQtr = qtr && node.dataset.hlQuarter === qtr;
      if (mComp || mEnv || mQtr){
        node.dataset.hlMatch = "true";
      } else {
        delete node.dataset.hlMatch;
      }
    });
  });
  shellEl.addEventListener("mouseleave", () => {
    delete shellEl.dataset.hlActive;
    shellEl.querySelectorAll("[data-hl-match]").forEach(m => delete m.dataset.hlMatch);
  });
}

// ---- Rich Tooltip Listener -------------------------------------------
const tip = $("tip");
let tipFor = null;
document.addEventListener("mousemove", e => {
  const hit = e.target.closest("[data-tip]");
  if (!hit){
    if (tipFor){ tip.classList.remove("on"); tipFor = null; }
    return;
  }
  const text = hit.dataset.tip;
  if (!text){
    if (tipFor){ tip.classList.remove("on"); tipFor = null; }
    return;
  }
  if (hit !== tipFor){
    tipFor = hit;
    tip.innerHTML = text.replace(/ &middot; /g, "<br/>");
    tip.classList.add("on");
  }
  const pad = 12;
  let x = e.clientX + pad, y = e.clientY + pad;
  const w = tip.offsetWidth, h = tip.offsetHeight;
  if (x + w > innerWidth - 4) x = e.clientX - w - pad;
  if (y + h > innerHeight - 4) y = e.clientY - h - pad;
  tip.style.left = Math.max(4, x) + "px";
  tip.style.top = Math.max(4, y) + "px";
});
document.addEventListener("mouseleave", () => { tip.classList.remove("on"); tipFor = null; });

function claimHeight(){
  try {
    const frame = window.frameElement;
    if (!frame) return;
    const box = frame.getBoundingClientRect();
    const avail = window.parent.innerHeight - box.top - 10;
    if (avail > 380 && Math.abs(avail - frame.clientHeight) > 6){
      frame.style.height = Math.floor(avail) + "px";
      frame.setAttribute("height", Math.floor(avail));
    }
  } catch (_e) {}
}

const THEAD_H = 24, ROW_H = 26;
function fitRows(){
  const box = $("mTablePanel");
  if (!box) return false;
  const room = box.clientHeight;
  if (room < 20) return false;
  const next = Math.max(2, Math.floor((room - THEAD_H) / ROW_H));
  if (next === S.rows) return false;
  S.rows = next;
  return true;
}

function relayout(){
  claimHeight();
  requestAnimationFrame(() => { if (fitRows()) apply(); });
}

apply();
relayout();
addEventListener("resize", relayout);
if (window.ResizeObserver) new ResizeObserver(() => { if (fitRows()) apply(); })
  .observe(MOUNTS.mTable);
setTimeout(relayout, 250);

if (typeof module !== "undefined" && module.exports){
  module.exports = { rows, counts, soonest, sorted, healthOf, worstBand, fmtDate, fmtDays,
                     fmtDaysLong, renderTable, renderSummary, summaryRows, visibleCount,
                     renderKpis, renderComps, renderHorizon, renderEnvs, renderCoverage,
                     renderWhen, renderSlicers, renderCrumbs, renderPager, renderNarrative,
                     renderSinceVisit, sparklineSvg, trendDelta, storySteps, clampStr,
                     getScopeSnapshots, METRIC_DIRECTIONS, tableHint, whereLabel, focusHint,
                     whenHint, quarters, qOrd, COLS, SUM_COLS, SORTS };
}
"""


# ==========================================================================
# Assembly
# ==========================================================================
def to_records(rows: list, *, env_order: list, component_order: list) -> list:
    """Flatten the loaded DataFrame into the shape the engine expects."""
    out = []
    for r in rows:
        env = r.get("env_label") or r.get("environment") or "UNMAPPED"
        comp = r["component"]
        exp = str(r["exp_date"])[:10]
        days = int(r["days_left"])
        band = r.get("band") or (
            "Expired" if days < 0 else "Critical" if days <= CRITICAL_DAYS
            else "Warning" if days <= WARNING_DAYS else "Healthy")
        schema = r.get("schema_name") or ""
        try:
            env_no = int(r.get("env_no") or 0)
        except (TypeError, ValueError):
            env_no = 0

        hay = " ".join(str(v) for v in (
            r.get("state", ""), comp, COMPONENT_CODE.get(comp, ""), env, schema,
            r.get("module") or "", exp, _human_date(exp), band, r.get("quarter") or "",
        )).lower()

        out.append({
            "id": r.get("id"),
            "state": r.get("state", ""),
            "component": comp,
            "environment": env,
            "envNo": env_no,
            "envRank": env_order.index(env) if env in env_order else len(env_order),
            "compRank": component_order.index(comp) if comp in component_order
                        else len(component_order),
            "schema": schema,
            "exp": exp,
            "days": days,
            "band": band,
            "quarter": r.get("quarter") or "",
            "edited": bool(r.get("edited")),
            "hay": hay,
        })
    return out


def _human_date(iso: str) -> str:
    """'2026-10-16' -> '16 Oct 2026', so the search box matches what is on screen."""
    try:
        y, m, d = (int(p) for p in iso.split("-")[:3])
        return f"{d:02d} {date(y, m, d).strftime('%b')} {y}"
    except (ValueError, TypeError):
        return ""


def _payload(records: list, *, mode: str, state, as_of: date, env_order: list,
             snapshots: list | None = None) -> dict:
    """The JSON handed to the browser."""
    today = as_of
    return {
        "records": records,
        "mode": mode,
        "state": state,
        "states": list(STATES),
        "components": list(COMPONENT_CODE),
        "componentCode": COMPONENT_CODE,
        "componentBlurb": COMPONENT_BLURB,
        "envOrder": env_order,
        "envBlurb": ENV_BLURB,
        "bands": BANDS,
        "bandMeta": BAND_META,
        "tokens": JS_TOKENS,
        "windows": WINDOWS,
        "bookmarks": BOOKMARKS,
        "criticalDays": CRITICAL_DAYS,
        "warningDays": WARNING_DAYS,
        "asOf": f"{today.day:02d} {today.strftime('%b %Y')}",
        "year": today.year,
        "quarter": (today.month - 1) // 3 + 1,
        "lastYear": max((int(r["exp"][:4]) for r in records), default=today.year),
        "snapshots": snapshots or [],
    }


def build(records: list, *, mode: str = "all", state: str | None = None,
          as_of: date | None = None, env_order: list | None = None,
          snapshots: list | None = None) -> str:
    """One self-contained HTML document for the whole report."""
    data = _payload(records, mode=mode, state=state,
                    as_of=as_of or date.today(), env_order=env_order or [],
                    snapshots=snapshots)
    css = _CSS.replace("/*__TOKENS__*/", _css_tokens())
    js = _JS.replace("/*__DATA__*/", json.dumps(data, separators=(",", ":")))
    title = f"Expiry Watchtower - {state}" if state else "Expiry Watchtower"
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        f"<title>{title}</title><style>{css}</style></head><body>"
        f"{_BODY}<script>{js}</script></body></html>"
    )


def _css_tokens() -> str:
    """Turn ui.TOKENS into the custom properties the canvas stylesheet reads."""
    lines = [f"--{name.replace('_', '-')}:{value};" for name, value in TOKENS.items()]
    for band, meta in BAND_META.items():
        key = band.lower()
        lines.append(f"--{key}:{meta['color']}; --{key}-t:{meta['tint']};")
    return "\n  ".join(lines)
