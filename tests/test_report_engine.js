/*
 * test_report_engine.js — checks the report's numbers against pandas
 * ==================================================================
 * The engine that draws the dashboard is a block of pure functions inside
 * dashboard/report.py, marked off by ENGINE-START / ENGINE-END. This script
 * slices that exact block out of a *built page* and runs it under node, so
 * what gets tested is the shipped code rather than a copy of it.
 *
 * The numbers it asserts against come from pandas reading the same SQLite
 * table (see build_fixture.py), which means a filter bug shows up as a
 * disagreement between two independent implementations instead of silently
 * rendering a plausible wrong total.
 *
 *     python3 tests/build_fixture.py     # writes the page + ground truth
 *     node tests/test_report_engine.js
 *
 * There is no jsdom and no browser here on purpose: every renderer returns
 * an HTML string, so the whole engine is testable with nothing but node.
 */

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const FIX = path.join(__dirname, "fixture");
let pass = 0;
const failures = [];

function ok(name, cond, detail) {
  if (cond) { pass++; return; }
  failures.push(name + (detail ? "  ->  " + detail : ""));
}
function eq(name, got, want) {
  const a = JSON.stringify(got), b = JSON.stringify(want);
  ok(name, a === b, "got " + a + ", want " + b);
}

// ---- load the engine out of a built page --------------------------------
function loadEngine(file) {
  const html = fs.readFileSync(file, "utf8");
  const start = html.indexOf("const DATA =");
  const end = html.indexOf("/*==ENGINE-END==*/");
  if (start < 0 || end < 0) throw new Error("engine markers missing in " + file);

  const ctx = { module: { exports: {} }, console };
  vm.createContext(ctx);
  vm.runInContext(
    html.slice(start, end) +
    ";Object.assign(module.exports,{DATA,rows,counts,soonest,sorted,healthOf,worstBand," +
    "fmtDate,fmtDays,fmtDaysLong,renderTable,renderSummary,summaryRows,visibleCount," +
    "renderKpis,renderComps,renderHorizon,renderEnvs,renderCoverage,renderCadence,renderWhen," +
    "renderCascades,renderAlertBanner,isMaintToday,renderSlicers,renderCrumbs,renderPager,renderNarrative,renderSinceVisit,sparklineSvg," +
    "trendDelta,storySteps,clampStr,getScopeSnapshots,METRIC_DIRECTIONS,tableHint," +
    "whereLabel,focusHint,whenHint,quarters,qOrd,COLS,SUM_COLS,SORTS});",
    ctx, { filename: path.basename(file) }
  );
  return ctx.module.exports;
}

const E = loadEngine(path.join(FIX, "page_all.html"));
const ES = loadEngine(path.join(FIX, "page_state.html"));
const T = JSON.parse(fs.readFileSync(path.join(FIX, "truth.json"), "utf8"));

// A fresh default state, matching what the page starts with.
const base = () => ({
  state: null, team: null, component: null, environment: null, band: null, window: "all", q: "",
  focus: "horizon", sort: "soon", page: 0, rows: 9, tview: "detail", qty: "count", view: "all",
  showFilters: false
});
const withS = o => Object.assign(base(), o);
const COMPS = Object.keys(E.DATA.componentCode);
const ALL_RENDERERS = ["renderKpis", "renderComps", "renderHorizon", "renderEnvs",
  "renderCoverage", "renderCadence", "renderWhen", "renderCascades", "renderAlertBanner",
  "renderTable", "renderSummary", "renderSlicers", "renderCrumbs", "renderPager"];

// =========================================================================
// 1. the filter pipeline agrees with pandas
// =========================================================================
eq("total records", E.rows(base()).length, T.total);

Object.keys(T.byState).forEach(st =>
  eq("state " + st, E.rows(withS({ state: st })).length, T.byState[st]));

Object.keys(T.byStateComp).forEach(st =>
  Object.keys(T.byStateComp[st]).forEach(comp =>
    eq("state " + st + " / " + comp,
       E.rows(withS({ state: st, component: comp })).length, T.byStateComp[st][comp])));

Object.keys(T.byEnv).forEach(env =>
  eq("env " + env, E.rows(withS({ environment: env })).length, T.byEnv[env]));

const bands = E.counts(E.rows(base()));
Object.keys(T.byBand).forEach(b => eq("band " + b, bands[b], T.byBand[b]));
eq("bands sum to total",
   Object.values(bands).reduce((a, b) => a + b, 0), T.total);
E.DATA.bands.forEach(b =>
  eq("band filter " + b, E.rows(withS({ band: b })).length, T.byBand[b] || 0));

eq("needs-attention bookmark",
   E.rows(withS({ band: "__urgent__" })).length,
   (T.byBand.Expired || 0) + (T.byBand.Critical || 0) + (T.byBand.Warning || 0));

eq("window: next 90 days", E.rows(withS({ window: "90" })).length, T.next90);
eq("window: overdue", E.rows(withS({ window: "past" })).length, T.overdue);
eq("window: all is a no-op", E.rows(withS({ window: "all" })).length, T.total);
ok("window 365 contains window 90",
   E.rows(withS({ window: "365" })).length >= E.rows(withS({ window: "90" })).length);

// Filters must compose, not override each other.
const combo = withS({ state: "AK", component: COMPS[0], window: "365" });
const byHand = E.DATA.records.filter(r =>
  r.state === "AK" && r.component === COMPS[0] && r.days >= 0 && r.days <= 365);
eq("filters compose", E.rows(combo).length, byHand.length);

// `skip` must lift exactly one dimension and leave the rest applied.
eq("skip state lifts only state",
   E.rows(withS({ state: "AK", component: COMPS[0] }), "state").length,
   E.rows(withS({ component: COMPS[0] })).length);
eq("skip band lifts only band",
   E.rows(withS({ state: "NH", band: "Healthy" }), "band").length,
   E.rows(withS({ state: "NH" })).length);

// =========================================================================
// 2. search
// =========================================================================
eq("search is case-insensitive",
   E.rows(withS({ q: "crypto" })).length, E.rows(withS({ q: "CRYPTO" })).length);
ok("search finds a component code", E.rows(withS({ q: "CRYPTO" })).length > 0);
ok("search finds an environment", E.rows(withS({ q: "prod" })).length > 0);
eq("search matches the printed date form",
   E.rows(withS({ q: T.sampleHumanDate })).length, T.sampleHumanDateCount);
eq("nonsense search returns nothing", E.rows(withS({ q: "zzzzqqq" })).length, 0);
eq("blank search is a no-op", E.rows(withS({ q: "   " })).length, T.total);

// =========================================================================
// 3. sorting
// =========================================================================
Object.keys(E.SORTS).forEach(key => {
  const out = E.sorted(E.rows(base()), key);
  eq("sort " + key + " keeps every row", out.length, T.total);
  eq("sort " + key + " is a permutation",
     out.map(r => r.schema + r.state).sort().join(),
     E.rows(base()).map(r => r.schema + r.state).sort().join());
});
const soon = E.sorted(E.rows(base()), "soon");
ok("sort soon is ascending by days",
   soon.every((r, i) => i === 0 || soon[i - 1].days <= r.days));
eq("sort soon starts at the minimum", soon[0].days, T.minDays);
const late = E.sorted(E.rows(base()), "late");
eq("sort late starts at the maximum", late[0].days, T.maxDays);
ok("sort health puts expired first",
   T.byBand.Expired ? E.sorted(E.rows(base()), "health")[0].band === "Expired" : true);
eq("unknown sort key falls back to soonest",
   E.sorted(E.rows(base()), "nope")[0].days, T.minDays);

// =========================================================================
// 4. formatting
// =========================================================================
eq("fmtDate", E.fmtDate("2026-10-16"), "16 Oct 2026");
eq("fmtDate pads the day", E.fmtDate("2026-01-05"), "05 Jan 2026");
eq("fmtDate on empty", E.fmtDate(""), "--");
eq("fmtDate on null", E.fmtDate(null), "--");
eq("fmtDays today", E.fmtDays(0), "today");
eq("fmtDays days", E.fmtDays(23), "23d");
eq("fmtDays months", E.fmtDays(400), "13mo");
eq("fmtDays years", E.fmtDays(1095), "3.0yr");
eq("fmtDays overdue", E.fmtDays(-5), "5d overdue");
eq("fmtDays long overdue compresses", E.fmtDays(-2068), "5.7yr overdue");
eq("fmtDaysLong reads as a sentence fragment", E.fmtDaysLong(1), "1 day left");
eq("fmtDaysLong overdue", E.fmtDaysLong(-5), "5 days overdue");
eq("fmtDaysLong long overdue", E.fmtDaysLong(-2068), "about 5.7 years overdue");
eq("fmtDaysLong today", E.fmtDaysLong(0), "expires today");

// The Manage tab formats durations in Python (ui.fmt_days) and the report
// formats them in JavaScript. Two implementations of one format drift silently,
// so build_fixture.py writes out what Python produces and we compare.
Object.entries(T.fmtDays).forEach(([days, want]) =>
  eq(`fmtDays(${days}) matches ui.fmt_days`, E.fmtDays(+days), want));
Object.entries(T.fmtDaysLong).forEach(([days, want]) =>
  eq(`fmtDaysLong(${days}) matches ui.fmt_days_long`, E.fmtDaysLong(+days), want));
Object.entries(T.fmtDate).forEach(([iso, want]) =>
  eq(`fmtDate(${iso}) matches ui.fmt_date`, E.fmtDate(iso), want));

// Bands must be derived the same way the emails derive them.
eq("healthOf boundary -1", E.healthOf(-1), "Expired");
eq("healthOf boundary 0", E.healthOf(0), "Critical");
eq("healthOf boundary 15", E.healthOf(15), "Critical");
eq("healthOf boundary 16", E.healthOf(16), "Warning");
eq("healthOf boundary 30", E.healthOf(30), "Warning");
eq("healthOf boundary 31", E.healthOf(31), "Healthy");
ok("every stored band matches healthOf",
   E.DATA.records.every(r => r.band === E.healthOf(r.days)));
eq("worstBand picks the most urgent",
   E.worstBand(new Set(["Healthy", "Warning", "Expired"])), "Expired");
eq("worstBand of healthy only", E.worstBand(new Set(["Healthy"])), "Healthy");
eq("worstBand of nothing", E.worstBand(new Set()), "Healthy");

// =========================================================================
// 5. quarters
// =========================================================================
eq("quarters length", E.quarters(12).length, 12);
eq("quarters start at today's quarter", E.quarters(1)[0],
   "Q" + E.DATA.quarter + " " + E.DATA.year);
ok("quarters roll the year over", E.quarters(12).some(q => /^Q1 \d{4}$/.test(q)));
ok("quarter labels are well formed", E.quarters(12).every(q => /^Q[1-4] \d{4}$/.test(q)));
ok("qOrd is monotonic across the window",
   E.quarters(12).map(E.qOrd).every((v, i, a) => i === 0 || a[i - 1] < v));
eq("qOrd rejects junk", E.qOrd("nope"), null);
eq("qOrd Q4 2020 sorts before Q3 2026", E.qOrd("Q4 2020") < E.qOrd("Q3 2026"), true);
ok("every record has a parseable quarter",
   E.DATA.records.every(r => E.qOrd(r.quarter) !== null));

// =========================================================================
// 6. paging shows every row exactly once
// =========================================================================
[1, 3, 9, 17, 40, 500].forEach(per => {
  const S = withS({ rows: per });
  let totalRowsRendered = 0;
  const pages = Math.max(1, Math.ceil(T.total / per));
  for (let p = 0; p < pages; p++) {
    const html = E.renderTable(withS({ rows: per, page: p }));
    totalRowsRendered += (html.match(/<span class="schema">/g) || []).length;
  }
  eq("paging at " + per + " rows covers every record",
     totalRowsRendered, T.total);
  ok("pager reports the right page count at " + per,
     E.renderPager(S).includes("of " + pages));
});
// A page index past the end must clamp rather than render blank.
ok("page index beyond the end still renders rows",
   E.renderTable(withS({ page: 99 })).includes("<tbody><tr"));

// =========================================================================
// 7. the table carries the column names the specification asks for
// =========================================================================
const SPEC = ["Environment", "Schema Name", "Expiry Date", "Health Status"];
const detail = E.renderTable(base());
SPEC.forEach(col => ok("detail table has the " + col + " column", detail.includes(">" + col + "<")));
eq("spec columns appear in order",
   SPEC.map(c => detail.indexOf(c)).every((v, i, a) => i === 0 || a[i - 1] < v), true);
eq("COLS starts with the four spec columns",
   E.COLS.slice(0, 4).map(c => c.label), SPEC);

// =========================================================================
// 8. summary view is a faithful rollup
// =========================================================================
const sum = E.summaryRows(base());
eq("summary item counts add back up",
   sum.reduce((a, g) => a + g.n, 0), T.total);
eq("summary pairing count", sum.length, T.pairings);
ok("summary bands are the worst in each group", sum.every(g => {
  const members = E.DATA.records.filter(r =>
    r.environment === g.environment && r.component === g.component);
  return g.band === E.worstBand(new Set(members.map(m => m.band)))
      && g.days === Math.min(...members.map(m => m.days));
}));
eq("visibleCount follows the table view",
   [E.visibleCount(withS({ tview: "detail" })), E.visibleCount(withS({ tview: "summary" }))],
   [T.total, T.pairings]);
SPEC.filter(c => c !== "Schema Name" && c !== "Expiry Date")
  .forEach(c => ok("summary keeps the " + c + " column",
                   E.renderSummary(base()).includes(">" + c + "<")));

// =========================================================================
// 9. drill-through: coverage and summary round-trip through the DOM value
// =========================================================================
const NAME_OF = {};
Object.keys(E.DATA.componentCode).forEach(n => NAME_OF[E.DATA.componentCode[n]] = n);
const cellVals = [...E.renderCoverage(base()).matchAll(/data-act="cell" data-val="([^"]+)"/g)]
  .map(m => m[1]);
ok("coverage emits clickable cells", cellVals.length > 0);
ok("every coverage value decodes to a real component",
   cellVals.every(v => NAME_OF[v.split("|")[1]] !== undefined));
ok("every coverage value decodes to a real row key",
   cellVals.every(v => E.DATA.states.includes(v.split("|")[0])));
const pairVals = [...E.renderSummary(base()).matchAll(/data-act="pair" data-val="([^"]+)"/g)]
  .map(m => m[1]);
ok("summary emits clickable pairings", pairVals.length > 0);
ok("every summary value decodes cleanly",
   pairVals.every(v => NAME_OF[v.split("|")[1]] !== undefined
                    && E.DATA.envBlurb[v.split("|")[0]] !== undefined));
// Component names contain spaces and an ampersand; the separator must survive.
ok("no data value collides with the separator",
   E.DATA.records.every(r => !r.environment.includes("|") && !r.state.includes("|")));

// =========================================================================
// 10. every renderer survives every scope, and never leaks a placeholder
// =========================================================================
const LEAKS = /\b(undefined|NaN|Infinity|null|nan|NaT|None|\[object Object\])\b/;
const scopes = [base()];
E.DATA.states.forEach(s => scopes.push(withS({ state: s })));
COMPS.forEach(c => scopes.push(withS({ component: c }), withS({ state: "AK", component: c })));
Object.keys(T.byEnv).forEach(e => scopes.push(withS({ environment: e })));
E.DATA.bands.forEach(b => scopes.push(withS({ band: b }), withS({ band: b, window: "90" })));
E.DATA.windows.forEach(w => scopes.push(withS({ window: w.id })));
["horizon", "envs", "coverage"].forEach(f => scopes.push(withS({ focus: f })));
["detail", "summary"].forEach(t => scopes.push(withS({ tview: t })));
["count", "share"].forEach(q => scopes.push(withS({ qty: q })));
Object.keys(E.SORTS).forEach(k => scopes.push(withS({ sort: k })));
// The empty intersection: a real filter combination that matches nothing.
scopes.push(withS({ q: "zzzzqqq" }), withS({ band: "Critical", window: "past" }));
scopes.push(withS({ state: "AK", environment: "PROD", component: COMPS[1], band: "Expired" }));

let leaked = null, threw = null;
scopes.forEach((S, i) => {
  ALL_RENDERERS.forEach(fn => {
    let html;
    try { html = E[fn](S); }
    catch (err) { threw = threw || fn + " scope#" + i + ": " + err.message; return; }
    if (typeof html !== "string") { threw = threw || fn + " returned " + typeof html; return; }
    const m = LEAKS.exec(html);
    if (m && !leaked) leaked = fn + " scope#" + i + " leaked '" + m[1] + "'";
  });
});
ok("no renderer throws on any scope", !threw, threw);
ok("no renderer leaks a placeholder value", !leaked, leaked);

// Empty scopes must say something useful rather than render a bare shell.
const nothing = withS({ q: "zzzzqqq" });
ok("empty table explains itself", E.renderTable(nothing).includes("No items match"));
ok("empty table offers a way out", E.renderTable(nothing).includes('data-act="reset"'));
ok("empty horizon explains itself", E.renderHorizon(nothing).includes("Nothing on the timeline"));
ok("empty pager renders nothing at all", E.renderPager(nothing) === "");

// =========================================================================
// 11. markup hygiene
// =========================================================================
scopes.slice(0, 12).forEach((S, i) => {
  ALL_RENDERERS.forEach(fn => {
    const html = E[fn](S);
    const open = (html.match(/<(?!\/)([a-z]+)/g) || []).length;
    const close = (html.match(/<\/[a-z]+/g) || []).length;
    const selfClose = (html.match(/\/>/g) || []).length;
    ok("balanced tags in " + fn + " scope#" + i, open - selfClose === close,
       "open " + open + " self " + selfClose + " close " + close);
    ok("no unescaped ampersand in " + fn + " scope#" + i,
       !/&(?!amp;|lt;|gt;|quot;|#\d+;|middot;|ndash;|mdash;|nbsp;|times;)/.test(html));
    ok("no double-quote breaks an attribute in " + fn + " scope#" + i,
       !/data-tip="[^"]*"[^ =>/]/.test(html));
  });
});
// aria-pressed is what tells a screen reader a filter is on.
ok("component cards expose their pressed state",
   (E.renderComps(withS({ component: COMPS[0] })).match(/aria-pressed="true"/g) || []).length === 1);
ok("KPI band tiles expose their pressed state",
   (E.renderKpis(withS({ band: "Healthy" })).match(/aria-pressed="true"/g) || []).length === 1);
ok("the total tile is pressed when no band is chosen",
   E.renderKpis(base()).indexOf('aria-pressed="true"') > 0);

// =========================================================================
// 12. header, hints and counts stay truthful
// =========================================================================
ok("crumbs say so when nothing is filtered",
   E.renderCrumbs(base()).includes("Showing every tracked item"));
const crumbed = withS({ state: "AK", component: COMPS[0], environment: "DEV",
                        band: "Healthy", window: "90", q: "env" });
["AK", E.DATA.componentCode[COMPS[0]], "DEV", "Healthy", "Next 90 days", "env"]
  .forEach(bit => ok("crumbs show the " + bit + " filter", crumbed && E.renderCrumbs(crumbed).includes(bit)));
eq("crumbs offer one remove button per filter",
   (E.renderCrumbs(crumbed).match(/data-act="drop"/g) || []).length, 6);
eq("crumbs offer a clear-all", (E.renderCrumbs(crumbed).match(/data-act="reset"/g) || []).length, 1);

eq("table hint counts what is shown",
   E.tableHint(withS({ state: "AK" })).startsWith(T.byState.AK + " of " + T.total), true);
ok("summary hint counts pairings",
   E.tableHint(withS({ tview: "summary" })).includes("pairings"));
eq("where label with no state", E.whereLabel(base()), "All states");
eq("where label with a state", E.whereLabel(withS({ state: "ND" })), "ND");

// Slicer counts must respect every filter except their own dimension.
// The filter drawer is closed by default; open it for these checks.
const sl = E.renderSlicers(withS({ state: "AK", showFilters: true }));
COMPS.forEach(c => ok("component slicer count for " + E.DATA.componentCode[c] + " is AK-scoped",
   sl.includes(">" + E.DATA.componentCode[c] + '<span class="n">' + T.byStateComp.AK[c])));

// =========================================================================
// 13. state mode pins the state without hiding the data
// =========================================================================
eq("state mode reports its state", ES.DATA.state, "AK");
eq("state mode ships every record", ES.DATA.records.length, T.total);
eq("state mode scopes by default",
   ES.rows(Object.assign(base(), { state: "AK" })).length, T.byState.AK);
ok("state mode hides the state slicer",
   !ES.renderSlicers(Object.assign(base(), { state: "AK", showFilters: true })).includes(">State<"));
ok("consolidated mode shows the state slicer", E.renderSlicers(Object.assign(base(), { showFilters: true })).includes(">State<"));
eq("state mode header shows the state",
   ES.whereLabel(Object.assign(base(), { state: "AK" })), "AK");
ok("state mode coverage rows are environments, not states",
   ES.renderCoverage(Object.assign(base(), { state: "AK" })).includes(">env<"));
ok("consolidated coverage rows are states",
   E.renderCoverage(base()).includes(">state<"));

// =========================================================================
// 14. bookmarks land somewhere sensible
// =========================================================================
E.DATA.bookmarks.forEach(bm => {
  const S = Object.assign(base(), bm.set);
  const n = E.rows(S).length;
  ok("bookmark '" + bm.label + "' resolves", Number.isInteger(n));
  ALL_RENDERERS.forEach(fn => {
    let html;
    try { html = E[fn](S); } catch (err) { failures.push("bookmark " + bm.id + " / " + fn + ": " + err.message); return; }
    ok("bookmark " + bm.id + " renders " + fn, typeof html === "string" && !LEAKS.test(html));
  });
});
eq("the 'everything' bookmark shows everything",
   E.rows(Object.assign(base(), E.DATA.bookmarks[0].set)).length, T.total);
eq("the 'overdue' bookmark matches pandas",
   E.rows(Object.assign(base(), E.DATA.bookmarks.find(b => b.id === "overdue").set)).length,
   T.overdue);

// =========================================================================
// 15. the horizon rail places marks inside its own viewBox
// =========================================================================
const rail = E.renderHorizon(base());
ok("rail is an svg", rail.startsWith("<svg"));
const xs = [...rail.matchAll(/<rect x="(-?[\d.]+)"[^>]*width="([\d.]+)"/g)]
  .map(m => [parseFloat(m[1]), parseFloat(m[2])]);
ok("every rail mark starts inside the viewBox", xs.every(([x]) => x >= -1 && x <= 1000));
ok("every rail mark ends inside the viewBox", xs.every(([x, w]) => x + w <= 1001));
const ys = [...rail.matchAll(/<rect [^>]*y="(-?[\d.]+)"[^>]*height="([\d.]+)"/g)]
  .map(m => [parseFloat(m[1]), parseFloat(m[2])]);
ok("every rail mark stays above the axis", ys.every(([y, h]) => y >= 0 && y + h <= 210));
ok("rail marks TODAY", rail.includes("TODAY"));
ok("rail shows the overdue gutter when something is overdue",
   T.overdue ? rail.includes(">past<") : true);
ok("rail explains its own scale", rail.includes("square-root"));

// A single record must not divide by zero or collapse the axis.
const one = E.DATA.records.find(r => r.days > 0);
ok("rail survives a one-record scope",
   E.renderHorizon(withS({ q: one.schema.toLowerCase() })).startsWith("<svg"));

// =========================================================================
// 16. quarter bars stay inside their box and label honestly
// =========================================================================
const bars = E.renderWhen(base());
const bh = [...bars.matchAll(/<rect x="([\d.]+)" y="([\d.]+)" width="([\d.]+)" height="([\d.]+)"/g)]
  .map(m => m.slice(1).map(Number));
ok("bars have positive height", bh.every(([, , , h]) => h > 0));
ok("bars stay inside the viewBox",
   bh.every(([x, y, w, h]) => x >= 0 && x + w <= 1000 && y >= 0 && y + h <= 162.5));
eq("bar labels add up to what is in the window",
   [...bars.matchAll(/font-weight="600" text-anchor="middle">(\d+)</g)]
     .reduce((a, m) => a + Number(m[1]), 0),
   T.inQuarterWindow);
ok("bars name what falls outside the window",
   T.beforeWindow ? bars.includes(T.beforeWindow + " already lapsed") : true);
ok("share mode shows percentages", E.renderWhen(withS({ qty: "share" })).includes("%"));

// =========================================================================
// 17. environment cards
// =========================================================================
const envHtml = E.renderEnvs(base());
Object.keys(T.byEnv).forEach(env =>
  ok("environments panel lists " + env, envHtml.includes(">" + env + "<")));
eq("environment counts are complete",
   [...envHtml.matchAll(/<span>(\d+) items?<\/span>/g)].reduce((a, m) => a + Number(m[1]), 0),
   T.total);
ok("environment cards are labelled in plain language", envHtml.includes("Disaster recovery"));

// =========================================================================
// 18. text length discipline & clampStr helper
// =========================================================================
eq("clampStr leaves short string intact", E.clampStr("hello", 10), "hello");
eq("clampStr trims and adds ellipsis", E.clampStr("very long schema name", 12), "very long s…");
eq("clampStr handles null/empty", E.clampStr(null, 10), "");

// =========================================================================
// 19. smart narrative banner (hard cap <= 90 plain text chars)
// =========================================================================
const narrativeAll = E.renderNarrative(base());
const plainAll = narrativeAll.replace(/<[^>]+>/g, "");
ok("narrative headline fits <= 90 chars across all items", plainAll.length <= 90, "got length " + plainAll.length + ": " + plainAll);

const narrativeHealthy = E.renderNarrative(withS({ band: "Healthy" }));
const plainHealthy = narrativeHealthy.replace(/<[^>]+>/g, "");
ok("narrative headline fits <= 90 chars for healthy filter", plainHealthy.length <= 90, "got length " + plainHealthy.length + ": " + plainHealthy);

const narrativeEmpty = E.renderNarrative(withS({ q: "nonexistent_query_xyz" }));
const plainEmpty = narrativeEmpty.replace(/<[^>]+>/g, "");
ok("narrative headline fits <= 90 chars for empty filter", plainEmpty.length <= 90, "got length " + plainEmpty.length + ": " + plainEmpty);

// =========================================================================
// 20. sparkline and metric-aware trend deltas
// =========================================================================
const spark = E.sparklineSvg([4, 3, 2, 2, 2], "#38BDF8");
ok("sparkline generates svg path", spark.includes("<svg") && spark.includes("<path"));

// Centralized METRIC_DIRECTIONS config checks
eq("expired metric is lower-is-better", E.METRIC_DIRECTIONS.expired, "lower");
eq("critical metric is lower-is-better", E.METRIC_DIRECTIONS.critical, "lower");
eq("warning metric is lower-is-better", E.METRIC_DIRECTIONS.warning, "lower");
eq("overdue metric is lower-is-better", E.METRIC_DIRECTIONS.overdue, "lower");
eq("healthy metric is higher-is-better", E.METRIC_DIRECTIONS.healthy, "higher");
eq("tracked metric is higher-is-better", E.METRIC_DIRECTIONS.tracked, "higher");

// Expired / Critical / Warning: decrease is green/positive, increase is red/negative
const expDec = E.trendDelta(0, 2, "expired");
ok("expired decrease renders good/green", expDec.includes("trend good") && expDec.includes("&#9660; -2"));
const expInc = E.trendDelta(3, 1, "expired");
ok("expired increase renders bad/red", expInc.includes("trend bad") && expInc.includes("&#9650; +2"));

const critDec = E.trendDelta(0, 1, "critical");
ok("critical decrease renders good/green", critDec.includes("trend good") && critDec.includes("&#9660; -1"));
const critInc = E.trendDelta(2, 0, "critical");
ok("critical increase renders bad/red", critInc.includes("trend bad") && critInc.includes("&#9650; +2"));

const warnDec = E.trendDelta(1, 3, "warning");
ok("warning decrease renders good/green", warnDec.includes("trend good") && warnDec.includes("&#9660; -2"));
const warnInc = E.trendDelta(4, 2, "warning");
ok("warning increase renders bad/red", warnInc.includes("trend bad") && warnInc.includes("&#9650; +2"));

// Healthy / Tracked: increase is green/positive, decrease is red/negative
const hlthInc = E.trendDelta(88, 85, "healthy");
ok("healthy increase renders good/green", hlthInc.includes("trend good") && hlthInc.includes("&#9650; +3"));
const hlthDec = E.trendDelta(80, 85, "healthy");
ok("healthy decrease renders bad/red", hlthDec.includes("trend bad") && hlthDec.includes("&#9660; -5"));

const trkInc = E.trendDelta(95, 91, "tracked");
ok("tracked increase renders good/green", trkInc.includes("trend good") && trkInc.includes("&#9650; +4"));
const trkDec = E.trendDelta(88, 91, "tracked");
ok("tracked decrease renders bad/red", trkDec.includes("trend bad") && trkDec.includes("&#9660; -3"));

// Flat & missing snapshot delta rendering
const flatDelta = E.trendDelta(3, 3, "tracked");
ok("stable delta renders flat", flatDelta.includes("trend flat") && flatDelta.includes("Stable"));
const noSnapDelta = E.trendDelta(24, null, "tracked");
ok("missing snapshot delta renders neutral dash", noSnapDelta.includes("trend none") && noSnapDelta.includes("&mdash;"));

// =========================================================================
// 21. scope-matched snapshot resolution (Bug 1)
// =========================================================================
const globalSnaps = E.getScopeSnapshots(base());
ok("global scope resolves global fleet snapshots", globalSnaps.length > 0 && globalSnaps.every(s => !s.state && !s.component));

const stateSnaps = ES.getScopeSnapshots(base());
ok("state scope resolves state-pinned snapshots", stateSnaps.length > 0 && stateSnaps.every(s => s.state === "AK"));

const patchSnaps = E.getScopeSnapshots(withS({ component: "PATCH" }));
ok("component scope resolves PATCH snapshots", patchSnaps.length > 0 && patchSnaps.every(s => s.component === "PATCH"));

const adhocEnvSnaps = E.getScopeSnapshots(withS({ environment: "DEV" }));
eq("adhoc environment filter returns empty snapshots to prevent cross-scope mismatch", adhocEnvSnaps.length, 0);

const adhocSearchSnaps = E.getScopeSnapshots(withS({ q: "crypto" }));
eq("adhoc search filter returns empty snapshots to prevent cross-scope mismatch", adhocSearchSnaps.length, 0);

const kpisFiltered = E.renderKpis(withS({ environment: "DEV" }));
ok("filtered KPI tile does NOT leak cross-scope deltas", !kpisFiltered.includes("-67 vs prev") && kpisFiltered.includes("trend none"));

// =========================================================================
// 22. guided story mode steps (hard cap <= 80 chars per step)
// =========================================================================
const steps = E.storySteps(base());
eq("story mode returns 4 sequential steps", steps.length, 4);
steps.forEach((st, idx) => {
  ok("story step " + (idx + 1) + " desc fits <= 80 chars", st.desc.length <= 80, "got length " + st.desc.length + ": " + st.desc);
});

// =========================================================================
// report
// =========================================================================
const total = pass + failures.length;
if (failures.length) {
  console.error("\n" + failures.length + " of " + total + " checks FAILED\n");
  failures.forEach(f => console.error("  x " + f));
  process.exit(1);
}
console.log("all " + total + " checks passed");
