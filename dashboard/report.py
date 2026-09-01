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

/* Panels are deep slate cards on dark canvas — border removed; depth = shade + shadow */
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
  grid-template-rows:auto auto minmax(54px,.62fr) minmax(132px,1.5fr) minmax(142px,1.8fr);
}
.rowB{ display:grid; gap:var(--gap); grid-template-columns:minmax(0,1.05fr) minmax(0,1fr);
       min-height:0; }
.rowC{ display:grid; gap:var(--gap); grid-template-columns:minmax(0,1.55fr) minmax(0,1fr);
       min-height:0; }

/* ---- header ---------------------------------------------------------- */
.head{
  display:flex; align-items:center; gap:12px; flex:none;
  background:var(--card); border-radius:7px;
  box-shadow:var(--shadow); padding:6px 10px;
}
.brand{ display:flex; align-items:baseline; gap:8px; flex:none; }
.brand h1{ margin:0; font-size:14px; font-weight:700; letter-spacing:-.01em; white-space:nowrap; }
.brand .where{
  font-family:var(--mono); font-size:10px; font-weight:500; letter-spacing:.04em;
  text-transform:uppercase; color:var(--accent);
  background:var(--accent-tint); border:1px solid var(--accent-line);
  border-radius:4px; padding:1px 6px; white-space:nowrap;
}
.crumbs{ display:flex; align-items:center; gap:5px; flex:1; min-width:0; overflow:hidden; }
.crumbs .lead{ font-size:9.5px; font-weight:600; letter-spacing:.11em; text-transform:uppercase;
               color:var(--mute); flex:none; }
.crumbs .none{ font-size:10.5px; color:var(--slate); white-space:nowrap; }
.cx{
  display:inline-flex; align-items:center; gap:5px; flex:none;
  background:var(--accent-tint); border:1px solid var(--accent-line); color:var(--accent);
  border-radius:4px; padding:1px 4px 1px 6px; font-size:10.5px; white-space:nowrap;
  font:inherit; font-size:10.5px; cursor:pointer;
}
.cx u{ text-decoration:none; color:var(--mute); font-size:8.5px; letter-spacing:.09em;
       text-transform:uppercase; }
.cx b{ font-family:var(--mono); font-weight:500; }
.cx s{ text-decoration:none; font-size:11px; line-height:1; color:var(--accent);
       opacity:.6; padding-left:1px; }
.cx:hover s{ opacity:1; }
.asof{ font-family:var(--mono); font-size:9.5px; color:var(--slate); text-align:right;
       line-height:1.35; flex:none; white-space:nowrap; }
.asof b{ color:var(--ink); font-weight:600; }

/* ---- saved views ----------------------------------------------------- */
.views{ display:flex; align-items:center; gap:4px; flex:none; }
.views .lead{ font-size:9.5px; font-weight:600; letter-spacing:.11em; text-transform:uppercase;
              color:var(--mute); margin-right:2px; }

/* ---- one chip style for every control -------------------------------- */
/* Default border is transparent — border color is reserved for "selected" state only */
.chip{
  font:inherit; font-size:11px; font-weight:500; color:var(--slate);
  background:var(--sunk); border:1px solid transparent; border-radius:5px;
  padding:3px 8px; cursor:pointer; white-space:nowrap; display:inline-flex;
  align-items:center; gap:5px; line-height:1.25;
  transition:background .12s ease, border-color .12s ease, color .12s ease;
}
.chip:hover{ border-color:var(--accent-line); color:var(--ink); background:var(--card); }
.chip[aria-pressed="true"], .chip.on{
  background:var(--accent-tint); border-color:var(--accent); color:var(--accent); font-weight:600;
}
.chip .n{ font-family:var(--mono); font-size:10px; color:var(--mute); }
.chip[aria-pressed="true"] .n{ color:var(--accent); }
.chip i{ width:7px; height:7px; border-radius:2px; flex:none; }
.chip:focus-visible, .cx:focus-visible, .seg button:focus-visible,
.tbl th button:focus-visible, .pg button:focus-visible{
  outline:2px solid var(--accent); outline-offset:1px;
}

/* ---- slicer bar ------------------------------------------------------ */
.slicers{
  display:flex; align-items:center; gap:5px; flex-wrap:wrap; flex:none;
  background:var(--card); border-radius:7px;
  box-shadow:var(--shadow); padding:5px 8px;
}
.sgroup{ display:flex; align-items:center; gap:4px; }
.sgroup > label{ font-size:9.5px; font-weight:600; letter-spacing:.09em; text-transform:uppercase;
                 color:var(--mute); margin-right:1px; }
.vr{ width:1px; align-self:stretch; background:var(--rule-soft); margin:0 3px; }

/* Filter drawer — hidden until toggled */
.filter-drawer{
  display:flex; flex-wrap:wrap; gap:5px; width:100%;
  border-top:1px solid var(--rule-soft); margin-top:4px; padding-top:5px;
}
.filter-toggle{
  font:inherit; font-size:11px; font-weight:600; color:var(--slate);
  background:var(--sunk); border:1px solid transparent; border-radius:5px;
  padding:3px 8px; cursor:pointer; white-space:nowrap; display:inline-flex;
  align-items:center; gap:5px; transition:background .12s ease, border-color .12s ease, color .12s ease;
}
.filter-toggle:hover{ border-color:var(--accent-line); color:var(--ink); background:var(--card); }
.filter-toggle.active{ border-color:var(--accent); color:var(--accent); background:var(--accent-tint); }
.filter-badge{
  font-family:var(--mono); font-size:9px; font-weight:700;
  background:var(--accent); color:#000; border-radius:3px; padding:0 4px;
}
.search{ position:relative; display:flex; align-items:center; flex:1; min-width:120px; max-width:230px; }
.search input{
  font:inherit; font-size:11px; font-family:var(--mono); width:100%; color:var(--ink);
  background:var(--sunk); border:1px solid var(--rule); border-radius:5px;
  padding:3px 22px 3px 8px;
}
.search input::placeholder{ color:var(--mute); font-family:var(--ui); }
.search input:focus{ outline:none; border-color:var(--accent); background:var(--card); }
.search .clr{ position:absolute; right:4px; border:0; background:none; cursor:pointer;
              color:var(--mute); font-size:13px; line-height:1; padding:0 2px; }

/* ---- KPI strip ------------------------------------------------------- */
.kpis{ display:grid; gap:var(--gap); grid-template-columns:repeat(6,minmax(0,1fr)); min-height:0; }
.kpi{
  background:var(--card); border:none; border-left:3px solid var(--edge,transparent);
  border-radius:7px; box-shadow:var(--shadow); padding:5px 9px 6px; cursor:pointer;
  display:flex; flex-direction:column; justify-content:center; gap:1px;
  text-align:left; font:inherit; min-width:0; overflow:hidden;
  transition:background .12s ease, box-shadow .12s ease;
}
.kpi:hover{ background:var(--sunk); }
/* Selected: inset shadow IS the visible border — border now means "selected", nothing else */
.kpi[aria-pressed="true"]{ background:var(--accent-tint);
                           box-shadow:inset 0 0 0 1.5px var(--accent); }
.kpi.flat{ cursor:default; }
.kpi.flat:hover{ background:var(--card); }
.kpi .v{
  font-family:var(--mono); font-variant-numeric:tabular-nums; font-weight:600;
  font-size:clamp(17px,3.5vh,27px); line-height:1.05; color:var(--val,var(--ink));
  white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
}
.kpi .v small{ font-size:.5em; font-weight:500; color:var(--mute); margin-left:3px; }
.kpi .k{ font-size:10px; font-weight:600; color:var(--ink); white-space:nowrap;
         overflow:hidden; text-overflow:ellipsis; }
.kpi .s{ font-size:9.5px; color:var(--slate); font-family:var(--mono); white-space:nowrap;
         overflow:hidden; text-overflow:ellipsis; }
/* Dominant focal tile — larger number, more padding */
.kpi[data-dom]{ padding:8px 12px 10px; }
.kpi[data-dom] .v{ font-size:clamp(22px,4.5vh,36px); font-weight:700; }
.kpi[data-dom] .k{ font-size:11px; }

/* ---- component cards ------------------------------------------------- */
.comps{ display:grid; gap:var(--gap); grid-template-columns:1fr 1fr; grid-template-rows:1fr 1fr;
        min-height:0; }
.cc{
  background:var(--card); border:none; border-radius:7px;
  box-shadow:var(--shadow); padding:8px 10px; cursor:pointer; font:inherit; text-align:left;
  display:flex; flex-direction:column; justify-content:space-between; min-height:0; min-width:0; overflow:hidden;
  transition:background .12s ease, box-shadow .12s ease;
}
.cc:hover{ background:var(--sunk); box-shadow:var(--shadow), 0 0 0 1px var(--accent-line); }
.cc[aria-pressed="true"]{ background:var(--accent-tint);
                          box-shadow:inset 0 0 0 1.5px var(--accent); }
.cc .head-row{ display:flex; align-items:center; gap:8px; min-width:0; overflow:hidden; }
.cc .code{ font-family:var(--mono); font-size:9.5px; font-weight:700; letter-spacing:.08em;
           color:var(--accent); background:var(--accent-tint); border:1px solid var(--accent-line);
           border-radius:3px; padding:1px 5px; flex:none; }
.cc[aria-pressed="true"] .code{ background:var(--accent); color:#fff; }
.cc .nm{ font-size:11.5px; font-weight:700; color:var(--ink);
         overflow:hidden; text-overflow:ellipsis; white-space:nowrap; flex:1; min-width:0; }
.cc .foot{ margin-top:auto; display:flex; align-items:flex-end; justify-content:space-between;
           gap:7px; padding-top:2px; }
.cc .cnt{ font-family:var(--mono); font-variant-numeric:tabular-nums; font-weight:700;
          font-size:clamp(16px,2.8vh,22px); line-height:1; }
.cc .cnt em{ font-style:normal; font-size:9.5px; font-weight:500; color:var(--mute);
             margin-left:3px; font-family:var(--ui); }
.cc .nx{ font-family:var(--mono); font-size:9.5px; color:var(--slate); text-align:right;
         white-space:nowrap; }
.cc .nx b{ color:var(--val,var(--ink)); font-weight:700; }
.meter{ display:flex; height:4px; border-radius:2px; overflow:hidden; background:var(--rule-soft);
        margin:5px 0 2px; flex:none; }
.meter-label{ font-size:9px; color:var(--mute); margin-bottom:4px; }
.meter i{ display:block; height:100%; }

/* ---- segmented control (focus panel + chart toggles) ----------------- */
.seg{ display:inline-flex; background:var(--sunk); border:1px solid var(--rule);
      border-radius:5px; padding:1px; flex:none; }
.seg button{
  font:inherit; font-size:10px; font-weight:600; color:var(--slate); background:none;
  border:0; border-radius:4px; padding:2px 7px; cursor:pointer; white-space:nowrap;
}
.seg button:hover{ color:var(--ink); }
.seg button[aria-pressed="true"]{ background:var(--card); color:var(--accent);
                                  box-shadow:0 0 0 1px var(--rule), var(--shadow); }

/* ---- charts ---------------------------------------------------------- */
.chart{ flex:1; min-height:0; display:block; width:100%; }
.chart text{ font-family:var(--mono); }
.legend{ display:flex; align-items:center; gap:11px; flex-wrap:wrap; flex:none;
         font-size:9.5px; color:var(--slate); padding-top:4px; }
.legend span{ display:inline-flex; align-items:center; gap:4px; white-space:nowrap; }
.legend i{ width:7px; height:7px; border-radius:2px; }

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
        color:var(--mute); padding:0 2px 2px; text-align:center; font-weight:600; }
.mx th.rh{ text-align:left; }
.mx td{ padding:0; }
.mx .rl{ font-family:var(--mono); font-size:10px; color:var(--ink); white-space:nowrap;
         padding-right:6px; }
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
.tbl tbody td{ padding:0 7px; height:25px; border-bottom:1px solid var(--rule-soft);
               white-space:nowrap; overflow:hidden; text-overflow:ellipsis; font-size:11px; }
.tbl tbody tr:hover td{ background:var(--sunk); }
.tbl tbody tr.hot td{ background:var(--accent-tint); }
.tbl td.r{ text-align:right; }
.tbl .schema{ font-family:var(--mono); font-size:10.5px; }
.tbl .sub{ font-size:9px; color:var(--slate); }
.env-pill{ display:inline-block; font-family:var(--mono); font-size:9.5px; font-weight:600;
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

/* ---- empty state ----------------------------------------------------- */
.void{ flex:1; min-height:0; display:flex; flex-direction:column; align-items:center;
       justify-content:center; text-align:center; gap:3px; padding:8px; }
.void .h{ font-size:11.5px; font-weight:600; }
.void .p{ font-size:10.5px; color:var(--slate); max-width:34ch; }
.void button{ margin-top:4px; }

/* ---- tooltip — neutral dark, no cyan outline -------------------------- */
#tip{
  position:fixed; z-index:99; pointer-events:none; opacity:0; transform:translateY(2px);
  transition:opacity .1s ease; max-width:260px;
  background:#1E293B; color:#F8FAFC; font-size:11px; line-height:1.4;
  border:1px solid #334155; border-radius:8px; padding:7px 11px;
  box-shadow:0 8px 24px rgba(0,0,0,0.6);
}
#tip.on{ opacity:1; transform:none; }
#tip b{ font-family:var(--mono); font-weight:700; color:var(--slate); }

/* ---- bottom panels (table + chart) are supporting detail, not co-equal headlines -- */
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
<div class="shell">
  <div class="head">
    <div class="brand"><h1>Expiry Watchtower</h1><span class="where" id="mWhere"></span></div>
    <div class="crumbs" id="mCrumbs"></div>
    <div class="views" id="mViews"></div>
    <div class="asof" id="mAsOf"></div>
  </div>

  <div class="slicers" id="mSlicers"></div>

  <div class="kpis" id="mKpis"></div>

  <div class="rowB">
    <div class="comps" id="mComps"></div>
    <section class="panel" aria-labelledby="focusTitle">
      <div class="phead">
        <span class="ptitle" id="focusTitle">Explore</span>
        <span class="phint" id="mFocusHint"></span>
        <span id="mFocusSeg"></span>
      </div>
      <div class="pbody" id="mFocus"></div>
    </section>
  </div>

  <div class="rowC">
    <section class="panel" aria-labelledby="tableTitle">
      <div class="phead">
        <span class="ptitle" id="tableTitle">Environment &amp; schema detail</span>
        <span class="phint" id="mTableHint"></span>
        <span id="mTableSeg"></span>
      </div>
      <div class="pbody" id="mTablePanel" style="padding:6px 6px 7px">
        <div class="twrap" id="mTable"></div>
        <div class="pg" id="mPager"></div>
      </div>
    </section>
    <section class="panel" aria-labelledby="whenTitle">
      <div class="phead">
        <span class="ptitle" id="whenTitle">When renewals land</span>
        <span class="phint" id="mWhenHint"></span>
        <span id="mWhenSeg"></span>
      </div>
      <div class="pbody" id="mWhen"></div>
    </section>
  </div>
</div>
<div id="tip" role="status" aria-live="polite"></div>
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
// The stylesheet reads the palette as CSS custom properties; SVG attributes
// need the values themselves, so the charts read them from the same source.
const T = DATA.tokens;
// Coverage cells travel through the DOM as "row|CODE", so the short code has
// to resolve back to the full component name on the way in.
const NAME_OF = {};
Object.keys(CODE).forEach(name => { NAME_OF[CODE[name]] = name; });

const esc = v => String(v === null || v === undefined ? "" : v).replace(
  /[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

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
  // Overdue is compressed on the same scale as time remaining. Two records in
  // this data lapsed in 2020 and 2022, and "2068d overdue" is a number nobody
  // reads at a glance - "5.7yr overdue" is the same fact, understood.
  if (d < 0) return span(-d) + " overdue";
  if (d === 0) return "today";
  return span(d);
}
function span(d){
  if (d < 90) return d + "d";
  if (d < 730) return Math.floor(d / 30) + "mo";
  return (d / 365).toFixed(1) + "yr";
}
// Long form for anyone who should not have to decode "13mo".
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

// ---- the one filter pipeline -------------------------------------------
// `skip` lets a control exclude its own dimension so it never filters
// itself out of existence. Nothing else may bypass this function.
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

// ---- small building blocks --------------------------------------------
function meter(c){
  const total = BANDS.reduce((s, b) => s + c[b], 0);
  if (!total) return '<div class="meter"></div>';
  return '<div class="meter">' + BANDS.filter(b => c[b]).map(b =>
    '<i style="width:' + (c[b] / total * 100).toFixed(3) + '%;background:' + META[b].color
    + '" data-tip="' + esc(c[b] + " " + META[b].label) + '"></i>'
  ).join("") + "</div>";
}
function dot(band){
  return '<span class="dot" style="color:' + META[band].color + '"><i style="background:'
    + META[band].color + '"></i>' + esc(band) + "</span>";
}
function chip(o){
  return '<button class="chip" type="button" data-act="' + esc(o.act) + '" data-val="'
    + esc(o.val === null || o.val === undefined ? "" : o.val) + '" aria-pressed="' + (o.on ? "true" : "false")
    + (o.tip ? '" data-tip="' + esc(o.tip) : "")
    + '">' + (o.swatch ? '<i style="background:' + o.swatch + '"></i>' : "")
    + esc(o.label) + (o.n === undefined ? "" : '<span class="n">' + o.n + "</span>") + "</button>";
}
function seg(act, options, current){
  return '<span class="seg" role="group">' + options.map(o =>
    '<button type="button" data-act="' + esc(act) + '" data-val="' + esc(o.id)
    + '" aria-pressed="' + (o.id === current ? "true" : "false")
    + (o.tip ? '" data-tip="' + esc(o.tip) : "")
    + '">' + esc(o.label) + "</button>").join("") + "</span>";
}
function voidState(head, hint, action){
  return '<div class="void"><div class="h">' + esc(head) + '</div><div class="p">' + esc(hint)
    + "</div>" + (action || "") + "</div>";
}
function legend(){
  return '<div class="legend">' + BANDS.map(b =>
    '<span data-tip="' + esc(META[b].label + " - " + META[b].plain) + '"><i style="background:'
    + META[b].color + '"></i>' + esc(META[b].label) + "</span>").join("")
    + '<span style="color:var(--mute)">Hover any mark for detail</span></div>';
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
  if ((S.q || "").trim()) bits.push(["Search", S.q.trim(), "q"]);

  if (!bits.length)
    return '<span class="lead">Filters</span><span class="none">Showing every tracked item '
      + '&mdash; click any card, tile, chip or row to narrow it.</span>';

  return '<span class="lead">Filters</span>' + bits.map(b =>
    '<button class="cx" type="button" data-act="drop" data-val="' + esc(b[2])
    + '" data-tip="Remove this filter"><u>' + esc(b[0]) + '</u><b>' + esc(b[1])
    + "</b><s>&times;</s></button>").join("")
    + '<button class="chip" type="button" data-act="reset" data-tip="Clear all filters '
    + '(or press Escape)">Clear all</button>';
}

function renderViews(S){
  return '<span class="lead">Saved views</span>' + DATA.bookmarks.map(b =>
    chip({ act: "view", val: b.id, label: b.label, on: S.view === b.id, tip: b.tip })).join("");
}

// ---- slicer bar -------------------------------------------------------
function renderSlicers(S){
  // Count active chip-row filters for the badge (excludes search — that shows inline)
  const activeCount = (DATA.mode === "all" && S.state ? 1 : 0)
    + (S.component ? 1 : 0)
    + (S.band && S.band !== "__urgent__" ? 1 : S.band === "__urgent__" ? 1 : 0)
    + ((S.window && S.window !== "all") ? 1 : 0);

  const q = S.q || "";

  // Always-visible row: search input + Filters toggle
  const topRow = [];
  topRow.push('<div class="search"><input id="q" type="search" value="' + esc(q)
    + '" placeholder="Find a schema, environment or date" aria-label="Search tracked items" />'
    + (q ? '<button class="clr" type="button" data-act="drop" data-val="q" '
         + 'aria-label="Clear search">&times;</button>' : "") + "</div>");
  topRow.push('<button class="filter-toggle' + (S.showFilters ? " active" : "") + '" type="button"'
    + ' data-act="toggleFilters" data-tip="'
    + (S.showFilters ? "Collapse filter options" : "Expand filters to narrow by state, component, health or date range") + '">'
    + (S.showFilters ? "&#9650; Filters" : "&#9660; Filters")
    + (activeCount ? '<span class="filter-badge">' + activeCount + '</span>' : '')
    + '</button>');

  if (!S.showFilters) return topRow.join("");

  // Expanded drawer: the full chip rows
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
  drawer.push('</div>');

  return topRow.join("") + drawer.join("");
}

// ---- KPI strip --------------------------------------------------------
function renderKpis(S){
  // Computed before the health filter, so the tile you are filtering by
  // still shows its own total instead of collapsing to itself.
  const base = rows(S, "band");
  const c = counts(base), total = base.length;
  const inScope = rows(S);
  const nx = soonest(inScope.filter(r => r.days >= 0)) || soonest(inScope);

  // What this count actually covers. Naming the live scope here means the
  // headline number can never be read against the wrong denominator.
  const scope = [
    DATA.mode === "all" ? (S.state || DATA.states.length + " states") : DATA.state,
    S.component ? CODE[S.component] : DATA.components.length + " components",
  ];
  if (S.environment) scope.push(S.environment);

  // The dominant tile is Expired when any items are overdue; otherwise Tracked items.
  // data-dom triggers the larger focal styling in CSS.
  const hasExpired = c["Expired"] > 0;

  const tiles = [
    '<button class="kpi" type="button" data-act="band" data-val="" aria-pressed="'
    + (S.band ? "false" : "true") + '"'
    + (!hasExpired ? ' data-dom="1"' : "")
    + ' data-tip="Show every health status">'
    + '<div class="v">' + total + '</div><div class="k">Tracked items</div>'
    + '<div class="s">' + scope.map(esc).join(" &middot; ") + "</div></button>"
  ];

  BANDS.forEach(b => {
    // "3% of 30" rather than "3% of these": a percentage is only useful if the
    // reader can see what it is a percentage of.
    const pct = total ? Math.round(c[b] / total * 100) + "% of " + total : "--";
    const isDom = b === "Expired" && hasExpired;
    tiles.push('<button class="kpi" type="button" data-act="band" data-val="' + esc(b)
      + '" aria-pressed="' + (S.band === b ? "true" : "false")
      + '" style="--val:' + META[b].color + ";--edge:" + META[b].color + '"'
      + (isDom ? ' data-dom="1"' : "")
      + ' data-tip="' + esc(META[b].label + " - " + META[b].plain + ". Click to show only these.")
      + '"><div class="v">' + c[b] + '</div><div class="k">' + esc(META[b].label)
      + '</div><div class="s">' + esc(pct) + "</div></button>");
  });

  const band = nx ? nx.band : "Healthy";
  tiles.push('<div class="kpi flat" style="--val:' + META[band].color + ";--edge:" + META[band].color
    + '"' + (nx ? ' data-tip="' + esc(nx.schema + " in " + nx.environment + " - " + nx.component) + '"' : "")
    + '><div class="v">' + esc(nx ? fmtDays(nx.days) : "--")
    + '</div><div class="k">Next expiry</div><div class="s">'
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
      + '" aria-pressed="' + (on ? "true" : "false") + '" style="--val:' + META[band].color
      + '" data-tip="' + esc((on ? "Click again to clear. " : "Click to focus ")
      + comp + " (" + CODE[comp] + ") — " + DATA.componentBlurb[comp])
      + '"><div class="head-row"><span class="code">' + esc(CODE[comp]) + '</span><span class="nm">'
      + esc(comp) + '</span></div>' + meter(c)
      + '<div class="meter-label">Health distribution across tracked items</div>'
      + '<div class="foot"><span class="cnt">' + sub.length + "<em>item"
      + (sub.length === 1 ? "" : "s") + '</em></span><span class="nx">'
      + (nx ? "next <b>" + esc(fmtDays(nx.days)) + "</b><br />" + esc(fmtDate(nx.exp))
            : "nothing in scope") + "</span></div></button>";
  }).join("");
}

// ---- focus panel: horizon / environments / coverage -------------------
const FOCUS_VIEWS = [
  { id: "horizon", label: "Timeline", tip: "Every item placed on a time axis" },
  { id: "envs", label: "Environments", tip: "The environments carrying the current selection" },
  { id: "coverage", label: "Coverage", tip: "Which pairings are tracked, and how urgent each is" }
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

// The signature visual. Time on a square-root axis, because this data runs
// to 2029: linear spacing would bury the next 90 days in the first 6% of
// the width. Records stack into columns, so a renewal wave reads as a
// shape rather than a number you have to look up.
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
    + '" preserveAspectRatio="none" role="img" aria-label="Expiry timeline: '
    + rs.length + ' tracked items by time until expiry">'];

  o.push('<rect x="' + x0.toFixed(1) + '" y="' + top + '" width="' + (px(CRIT) - x0).toFixed(1)
    + '" height="' + (base - top).toFixed(1) + '" fill="' + META.Critical.tint + '"/>');
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

  GRID.filter(g => g[0] <= horizon * 0.985).concat(horizon > 90 ? [[horizon, fmtDays(horizon)]] : [])
    .forEach(g => {
      const x = px(g[0]);
      o.push('<line x1="' + x.toFixed(1) + '" y1="' + (top + 2) + '" x2="' + x.toFixed(1)
        + '" y2="' + base + '" stroke="' + T.ruleSoft + '" stroke-width="1" stroke-dasharray="2 4"/>');
      o.push('<text x="' + x.toFixed(1) + '" y="' + (base + 16) + '" fill="' + T.mute + '" font-size="11"'
        + ' text-anchor="middle">' + esc(g[1]) + "</text>");
    });

  o.push('<line x1="' + x0 + '" y1="' + (top - 8) + '" x2="' + x0 + '" y2="' + (base + 5)
    + '" stroke="' + T.accent + '" stroke-width="1.5"/>');
  o.push('<text x="' + (x0 + 6) + '" y="' + (top - 1) + '" fill="' + T.accent + '" font-size="11"'
    + ' font-weight="600" letter-spacing="1">TODAY</text>');

  // Stack marks into 7px columns so clusters build visible height.
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
        + '" data-tip="' + esc(r.schema + " &middot; " + r.environment + " &middot; " + r.component
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
    + "square-root scale, so the next three months stay readable next to " + esc(DATA.lastYear)
    + ". Column height is how many items land in that window.</span></div>";
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
    return voidState("No environments in scope",
      "Clear a filter to bring environments back.",
      '<button class="chip" type="button" data-act="reset">Clear all filters</button>');

  const items = [...groups.entries()].sort((a, b) => a[1].rank - b[1].rank);
  return '<div class="envs">' + items.map(([env, g]) => {
    const band = worstBand(g.bands), on = S.environment === env;
    return '<button class="ec" type="button" data-act="environment" data-val="' + esc(env)
      + '" aria-pressed="' + (on ? "true" : "false") + '" style="--val:' + META[band].color
      + '" data-tip="' + esc(env + " - " + (DATA.envBlurb[env] || env) + ". " + g.n
      + " item(s), soonest " + fmtDaysLong(g.min) + ". "
      + (on ? "Click again to clear." : "Click to focus every panel on this environment."))
      + '"><div class="en">' + esc(env) + '</div><div class="eb">'
      + esc(DATA.envBlurb[env] || "") + '</div><div class="er"><span>' + g.n + " item"
      + (g.n === 1 ? "" : "s") + "</span><b>" + esc(fmtDays(g.min)) + "</b></div></button>";
  }).join("") + "</div>" + legend();
}

function renderCoverage(S){
  const rs = rows(S);
  if (!rs.length)
    return voidState("Nothing to map", "No tracked item matches the current filters.",
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
        + ": not tracked in the workbooks. That is different from healthy.") + '">&middot;</div></td>';
      const band = worstBand(c.bands), solid = band !== "Healthy";
      return '<td><button class="c" type="button" data-act="cell" data-val="'
        + esc(key + "|" + CODE[comp]) + '" style="background:' + (solid ? META[band].color : META[band].tint)
        + ";color:" + (solid ? "#fff" : META[band].color) + ";border-color:" + META[band].color
        + (solid ? "" : ";border-color:rgba(0,0,0,0)") + '" data-tip="'
        + esc(key + " / " + comp + ": " + c.n + " item(s), soonest " + fmtDaysLong(c.min)
        + ". Click to filter to this pairing.") + '">' + esc(fmtDays(c.min)) + "</button></td>";
    }).join("");
    const sub = rowKey === "state" ? "" : ' <u>' + esc([...meta.nos].sort((a, b) => a - b).join("/")) + "</u>";
    return "<tr><td class=\"rl\">" + esc(key) + sub + "</td>" + tds + "</tr>";
  }).join("");

  return '<table class="mx">' + head + body + "</table>" + legend();
}

// ---- detail table ----------------------------------------------------
// The first four headers are the column names the specification asks for,
// spelled exactly as written there. "Time Left" is the one addition: the
// date alone does not tell you whether to act this week.
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
    // Expiry Date and Time Left both sort by date, so without this the arrow
    // would appear twice and imply two active sorts. It belongs on the first
    // column carrying the sort, which is the one the reader is looking at.
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

  const body = slice.map(r =>
    '<tr class="' + (r.band === "Healthy" ? "" : "hot") + '">'
      + '<td><span class="env-pill" data-tip="' + esc(DATA.envBlurb[r.environment] || r.environment)
      + '">' + esc(r.environment) + "</span></td>"
      + '<td><span class="schema">' + esc(r.schema) + "</span>"
      + (r.edited ? '<span class="tag" data-tip="Changed in Manage; differs from the workbook">EDITED</span>' : "")
      // The schema name already ends in the component code, so repeating the
      // code here would be decoration. The full component name is what someone
      // meeting "ENV31_DBPWD" for the first time actually needs - and in the
      // consolidated view the state is the one thing no column carries.
      + ' <span class="sub">'
      + (DATA.mode === "all" ? esc(r.state) + " &middot; " : "")
      + esc(r.component) + "</span></td>"
      + '<td class="mono">' + esc(fmtDate(r.exp)) + "</td>"
      + "<td>" + dot(r.band) + "</td>"
      + '<td class="r mono" style="color:' + META[r.band].color + ';font-weight:600" data-tip="'
      + esc(fmtDaysLong(r.days)) + '">' + esc(fmtDays(r.days)) + "</td></tr>").join("");

  return '<table class="tbl"><thead><tr>' + thead(COLS, S)
    + "</tr></thead><tbody>" + body + "</tbody></table>";
}

// One row per environment-and-component pairing. Same filters, same numbers,
// fewer rows - for the reader who wants the shape of the workload rather
// than the individual schema names.
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
    '<tr class="' + (g.band === "Healthy" ? "" : "hot") + '">'
      + '<td><button class="env-pill" type="button" data-act="pair" data-val="'
      + esc(g.environment + "|" + CODE[g.component]) + '" data-tip="'
      + esc("Filter everything to " + g.environment + " / " + g.component)
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

// Paging counts whatever the table is currently listing, which is records in
// Detail and pairings in Summary.
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

// ---- quarter bars ----------------------------------------------------
function quarters(n){
  const out = [];
  let y = DATA.year, q = DATA.quarter;
  for (let i = 0; i < n; i++){ out.push("Q" + q + " " + y); if (++q > 4){ q = 1; y++; } }
  return out;
}
// "Q2 2027" -> 8107, so quarters can be compared without parsing dates.
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
    // Long-overdue items sit in quarters that closed years ago. Counting them
    // as "later than the window" would be the wrong way round.
    if (o === null || o < first){ before++; return; }
    if (o > last){ after++; return; }
    byQ[r.quarter] = (byQ[r.quarter] || 0) + 1;
    const cur = bandOf[r.quarter];
    bandOf[r.quarter] = cur ? worstBand(new Set([cur, r.band])) : r.band;
  });
  const buckets = labels.map(l => [l, byQ[l] || 0, bandOf[l] || "Healthy"]);

  const W = 1000, H = 200, top = 22, base = 162;
  const peak = Math.max(1, ...buckets.map(b => b[1]));
  const slot = W / buckets.length, bw = Math.min(slot * 0.6, 44);
  const share = S.qty === "share";

  const o = ['<svg class="chart" viewBox="0 0 ' + W + " " + H
    + '" preserveAspectRatio="none" role="img" aria-label="Items expiring per quarter">'];
  o.push('<line x1="0" y1="' + base + '" x2="' + W + '" y2="' + base
    + '" stroke="' + T.rule + '" stroke-width="1"/>');

  buckets.forEach((b, i) => {
    const cx = slot * (i + 0.5), n = b[1];
    if (!n){
      o.push('<line x1="' + (cx - bw / 2).toFixed(1) + '" y1="' + base + '" x2="'
        + (cx + bw / 2).toFixed(1) + '" y2="' + base + '" stroke="' + T.ruleSoft + '" stroke-width="2"/>');
    } else {
      const h = (n / peak) * (base - top);
      const label = share ? Math.round(n / rs.length * 100) + "%" : n;
      o.push('<rect x="' + (cx - bw / 2).toFixed(1) + '" y="' + (base - h).toFixed(1) + '" width="'
        + bw.toFixed(1) + '" height="' + h.toFixed(1) + '" rx="2" fill="' + META[b[2]].color
        + '" fill-opacity=".85" data-tip="' + esc(b[0] + ": " + n + " item(s) expiring, "
        + Math.round(n / rs.length * 100) + "% of the " + rs.length + " in scope. Most urgent status "
        + b[2] + ".") + '"/>');
      o.push('<text x="' + cx.toFixed(1) + '" y="' + (base - h - 6).toFixed(1)
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
   DOM layer. The only place that touches the document: it assigns the
   strings the pure functions above return, and translates clicks back
   into state changes.
   ====================================================================== */
const S = {
  state: DATA.mode === "state" ? DATA.state : null,
  component: null, environment: null, band: null, window: "all", q: "",
  focus: "horizon", sort: "soon", page: 0, rows: 9, tview: "detail", qty: "count", view: "all",
  showFilters: false
};

const $ = id => document.getElementById(id);
const MOUNTS = {};
["mWhere", "mCrumbs", "mViews", "mAsOf", "mSlicers", "mKpis", "mComps", "mFocusSeg",
 "mFocusHint", "mFocus", "mTableHint", "mTableSeg", "mTable", "mPager", "mWhenHint",
 "mWhenSeg", "mWhen"].forEach(k => MOUNTS[k] = $(k));

// Assign only when the string actually changed: fewer repaints, and the
// browser keeps focus and hover where the user left them.
const last = {};
function put(key, html){
  if (last[key] === html) return;
  last[key] = html;
  MOUNTS[key].innerHTML = html;
}

function apply(){
  put("mWhere", esc(whereLabel(S)));
  put("mCrumbs", renderCrumbs(S));
  put("mViews", renderViews(S));
  put("mAsOf", "<b>" + DATA.records.length + "</b> items tracked<br />as of <b>"
    + esc(DATA.asOf) + "</b>");
  const focused = document.activeElement === $("q");
  if (!focused) put("mSlicers", renderSlicers(S));
  else { last.mSlicers = null; }          // refresh after the box loses focus
  put("mKpis", renderKpis(S));
  put("mComps", renderComps(S));
  put("mFocusSeg", seg("focus", FOCUS_VIEWS, S.focus));
  put("mFocusHint", focusHint(S));
  put("mFocus", renderFocus(S));
  put("mTableHint", tableHint(S));
  put("mTableSeg", seg("tview", [
    { id: "detail", label: "Detail", tip: "One row per tracked item" },
    { id: "summary", label: "Summary", tip: "One row per environment and component, with counts" }
  ], S.tview));
  put("mTable", renderTable(S));
  put("mPager", renderPager(S));
  put("mWhenHint", whenHint(S));
  put("mWhenSeg", seg("qty", [
    { id: "count", label: "Count", tip: "Number of items per quarter" },
    { id: "share", label: "Share", tip: "Each quarter as a percentage of the current selection" }
  ], S.qty));
  put("mWhen", renderWhen(S));
}

// Selecting the value already active clears it, so every control is its
// own off switch.
function toggle(key, value){
  S[key] = S[key] === value ? null : value;
  S.view = null;
  S.page = 0;
}

function act(name, value){
  switch (name){
    case "state":
      toggle("state", value);
      S.component = null; S.environment = null;      // narrower choices no longer apply
      break;
    case "component":
      toggle("component", value);
      S.environment = null;
      S.focus = S.component ? "envs" : "horizon";    // drill-through
      break;
    case "environment":
      toggle("environment", value);
      if (S.environment) S.focus = "horizon";        // redraw everything for that environment
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
  if (e.target.id === "q") apply();     // repaint the slicer counts once typing stops
});

document.addEventListener("keydown", e => {
  if (e.key === "Escape"){ act("reset", ""); return; }
  const typing = e.target && (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA");
  if (typing) return;
  if (e.key === "ArrowRight") act("page", "next");
  else if (e.key === "ArrowLeft") act("page", "prev");
});

// ---- tooltip ---------------------------------------------------------
const tip = $("tip");
let tipFor = null;
document.addEventListener("mousemove", e => {
  const hit = e.target.closest ? e.target.closest("[data-tip]") : null;
  if (!hit){
    if (tipFor){ tip.classList.remove("on"); tipFor = null; }
    return;
  }
  if (hit !== tipFor){ tipFor = hit; tip.innerHTML = hit.dataset.tip; tip.classList.add("on"); }
  const pad = 12, w = tip.offsetWidth, h = tip.offsetHeight;
  let x = e.clientX + pad, y = e.clientY + pad;
  if (x + w > innerWidth - 4) x = e.clientX - w - pad;
  if (y + h > innerHeight - 4) y = e.clientY - h - pad;
  tip.style.left = Math.max(4, x) + "px";
  tip.style.top = Math.max(4, y) + "px";
});
document.addEventListener("mouseleave", () => { tip.classList.remove("on"); tipFor = null; });

/* ----------------------------------------------------------------------
   Fit to the screen, exactly.

   Two things happen here. First the canvas tries to size its own iframe to
   the space Streamlit actually has, so the report fills the window instead
   of stopping at whatever pixel height Python guessed. If the browser
   refuses that (a sandboxed frame would), the fixed height still holds and
   the layout is merely a little shorter - never scrolling, which is the
   requirement that matters.

   Then the table asks how many rows fit in the room it was given and shows
   exactly that many. This is what keeps "no scrolling" and "no wasted
   space" from being in conflict: the page never grows past the viewport,
   and the last row always lands just above the edge.
   ---------------------------------------------------------------------- */
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
  } catch (err) {
    /* Cross-origin frame: keep the height Python asked for. */
  }
}

const ROW_H = 25, THEAD_H = 25;
function fitRows(){
  const box = MOUNTS.mTable;
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
// Streamlit sizes its iframe a beat after first paint; re-check once.
setTimeout(relayout, 250);

if (typeof module !== "undefined" && module.exports){
  module.exports = { rows, counts, soonest, sorted, healthOf, worstBand, fmtDate, fmtDays,
                     fmtDaysLong, renderTable, renderSummary, summaryRows, visibleCount,
                     renderKpis, renderComps, renderHorizon, renderEnvs, renderCoverage,
                     renderWhen, renderSlicers, renderCrumbs, renderPager, tableHint,
                     whereLabel, focusHint, whenHint, quarters, qOrd, COLS, SUM_COLS, SORTS };
}
"""


# ==========================================================================
# Assembly
# ==========================================================================
def to_records(rows: list, *, env_order: list, component_order: list) -> list:
    """
    Flatten the loaded DataFrame into the shape the engine expects.

    Lives here rather than in app.py so the browser payload has exactly one
    definition, which is what lets tests/test_report_engine.mjs assert on the
    same records the page draws. Takes `df.to_dict("records")` - plain dicts
    only, so this module never has to import pandas.

    `hay` is a pre-lowercased search string. Building it once at load time
    keeps the search box instant no matter how the record count grows, and
    means searching "oct 2026" finds the same rows as searching "ENV30".
    """
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


def _payload(records: list, *, mode: str, state, as_of: date, env_order: list) -> dict:
    """The JSON handed to the browser. One flat record list, plus the labels
    and orderings the engine needs to sort and explain itself."""
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
    }


def build(records: list, *, mode: str = "all", state: str | None = None,
          as_of: date | None = None, env_order: list | None = None) -> str:
    """
    One self-contained HTML document for the whole report.

    `records` is a list of dicts with keys: state, component, environment,
    envNo, envRank, compRank, schema, exp, days, band, quarter, edited, hay.
    `mode` is "all" for the consolidated view or "state" for a single state.
    """
    data = _payload(records, mode=mode, state=state,
                    as_of=as_of or date.today(), env_order=env_order or [])
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
