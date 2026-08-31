---
name: enterprise-dashboard-architect
description: Enterprise Automation & Power BI Dashboard UI Pattern Playbook. Use when designing, building, or refactoring high-density enterprise dashboard screens, executive command centers, master-detail inspectors, matrix cross-tabs, drilldowns, and data verification tables.
---

# Enterprise Dashboard Architect & UI Pattern Playbook

This playbook provides enterprise design standards and reusable task-specific prompts for building Power BI / Databricks / Snowflake grade analytics interfaces.

---

## 🏛️ Master System Prompt (Global Standards)

```markdown
You are an Enterprise Dashboard Architect specializing in Power BI / Databricks / Snowflake grade analytics interfaces.
Whenever building or refactoring UI dashboard screens, you must strictly follow these Enterprise Design Standards:

1. ZERO-PAGE-SCROLL CONSTRAINT (Viewport Locking):
   - The entire application view MUST fit within `100vh` without outer page vertical scrollbars.
   - Root containers must use `display: flex; flex-direction: column; height: 100%; overflow: hidden;`.
   - Only individual inner widgets, table bodies (`tbody`), code areas, or inspector panels may have scrollbars (`overflow-y: auto; max-height: calc(100vh - ...)`).

2. THEME & VISUAL TOKENS:
   - Palette: Deep Slate `#0f172a` (card background), `#020617` (inset/editor), `#1e293b` (panel header/border), `#334155` (sub-borders).
   - Accents: Neon Cyan `#38bdf8` (primary/active), Emerald `#10b981` (success/clean), Amber `#f59e0b` (warning/pending), Crimson `#ef4444` (danger/PII).
   - Typography: Clean sans for headers (`Inter`, `Segoe UI`), monospaced for IDs, queries, hashes, and numbers (`ui-monospace`, `Fira Code`).

3. INTERACTION PATTERNS:
   - Master-Detail: Left/Top master summaries driving instant right/bottom contextual detail views.
   - Drilldown & Drillthrough: Breadcrumb-navigated multi-level drilldowns.
   - Zero-Mock Enforcement: Always bind directly to live backend REST/WebSocket endpoints with explicit data-loading skeletons and genuine empty states (never mock arrays).
```

---

## 📦 Reusable Task-Specific Prompts by Dashboard Pattern

### 1. Zero-Scroll Power BI Executive Layout Prompt
*Use this prompt when building top-level analytics hubs or KPI command centers.*

```markdown
Create an Enterprise Power BI style Executive Analytics Command Center for [INSERT DOMAIN, e.g., Healthcare Provider Claims / Financial Transactions].
Strict Requirements:
1. Layout Structure:
   - Zero vertical page scrolling (`height: 100vh`, `box-sizing: border-box`, `overflow: hidden`).
   - Top Bar: PageHeader with breadcrumb, environment pill, and primary action buttons (Refresh, Export, Filter Drawer).
   - Metric Ribbon: 4 compact KPI cards across the top with glowing colored top borders (Total Volume, Success Rate %, Latency ms, Active Anomalies).
   - Main Grid: 2-column split (60% Main Analytics Chart/Table, 40% Live Activity Feed & Status Matrix).
2. Interactivity:
   - Clicking any metric card filters the main grid to that specific state.
   - Quick date range filter toggle (Today, 7D, 30D, YTD) in the top right.
3. Design System: Dark theme (`#0f172a`), compact typography (9.5px uppercase labels, 18px bold metric values), subtle borders (`#1e293b`).
```

---

### 2. Master-Detail Split-Pane & Inspector Hub Prompt
*Use this prompt when building management tools, log viewers, template engines, or execution trackers.*

```markdown
Build a Master-Detail Workspace Dashboard for [INSERT ENTITY, e.g., ETL Pipeline Runs / Letter Templates / Transaction Audit Logs].
Strict Requirements:
1. Split-Pane Architecture:
   - Left Master Pane (45% width): Searchable and filterable table listing all records with status badges, timestamps, and record IDs.
   - Right Detail Inspector (55% width): Sticky contextual inspector updating immediately upon selecting a record from the master list.
2. Detail Inspector Elements:
   - Header with active Record ID, copy button, and status indicator.
   - Tabbed or sectioned views: 
     - Tab 1: Overview & Bound Metadata (Table attributes, timestamps, actors).
     - Tab 2: Raw JSON/SQL Payload Inspector with monospace syntax formatting.
     - Tab 3: Action Console (Re-run, Edit, Download Artifact, Abort).
3. Zero-Page-Scroll:
   - Table body on the left has internal `max-height: 480px; overflow-y: auto;`.
   - Inspector pane on the right scrolls independently if payload is long.
   - The outer page remains 100% locked to viewport height.
```

---

### 3. Hierarchical Matrix Cross-Tab with Multi-Column Sort
*Use this prompt for complex data grids, test case studio matrices, reconciliation grids, or pivot-like views.*

```markdown
Build an Advanced Matrix Cross-Tab Data Table for [INSERT DATASET, e.g., SIT Schema Reconciliation / Multi-Environment Test Execution].
Strict Requirements:
1. Matrix Grid Capabilities:
   - Multi-Column Sort: Clicking column headers sorts asc/desc with active arrow indicators (`▲`/`▼`).
   - Multi-Dimensional Grouping: Group rows hierarchically by [Category -> Subcategory -> Record].
   - Expandable/Collapsible Row Groups: Clicking group row toggles children with indentations.
2. Cell Formatting:
   - Status indicators rendered with semi-transparent background pills (e.g., `rgba(16,185,129,0.15)` with `#10b981` text).
   - Numerical values right-aligned with monospace font (`1,392 rows`).
   - PII/Sensitive column data flagged with Red `PII` chips and hover tooltip.
3. Action Toolbar:
   - Search input for real-time string filtering.
   - Column visibility multi-select dropdown.
   - Export to CSV / Excel button.
```

---

### 4. Multi-Level Drilldown & Breadcrumb Drillthrough
*Use this prompt when users need to dive from high-level aggregations down to granular atomic records.*

```markdown
Implement a 3-Level Drillthrough & Drilldown Navigation Pattern for [INSERT DOMAIN, e.g., Error Breakdown by Pipeline -> Step -> Stack Trace].
Strict Requirements:
1. Drilldown Levels:
   - Level 1 (Aggregated Summary): Bar chart or summary table showing total counts grouped by [Category].
   - Level 2 (Sub-Breakdown): Clicking a category transitions the view to sub-entities matching that parent.
   - Level 3 (Atomic Event Lineage): Clicking a sub-entity opens the exact raw log, SQL query, and execution trace.
2. Navigation & Breadcrumb Trail:
   - Top dynamic breadcrumb banner: `All Categories / Selected Category / Selected Event SK`.
   - Back button (`← Back`) and clickable breadcrumb nodes that return to prior levels without losing filter state.
3. Smooth State Transition:
   - Keep URL hash or React state synchronized (`level: 1 | 2 | 3`, `selectedCategory`, `selectedEventId`).
```

---

### 5. Live Database Verification Lineage & Zero-Mock Pipeline
*Use this prompt when building backend-connected data reconciliation or verification pages.*

```markdown
Build a Live Database Verification & Lineage Engine for [INSERT SYSTEM, e.g., Oracle-to-PostgreSQL Transpilation / ETL Ingestion Lineage].
Strict Requirements:
1. Zero-Mock Policy:
   - Zero hardcoded arrays for live data. Connect to live REST backend (`GET /api/...`).
   - If 0 records exist, render a genuine empty state banner with a call-to-action button, never fake sample rows.
2. Single-Batch Metadata Aggregation:
   - Backend queries MUST use single-query `UNION ALL` or `information_schema` aggregations (under 400ms), avoiding sequential loops.
3. Field Mapping & Transpilation Verification:
   - Display a 2-column side-by-side verification: Source System Query vs Transpiled Target Query.
   - Working "Edit Mapping" modal that performs live SQL syntax check on save (`PUT /api/...`).
   - Dialect conversion highlight chips (e.g., `NVL() -> COALESCE()`, `DECODE() -> CASE WHEN`, `SYSDATE -> CURRENT_DATE`).
```

---

### 6. Visual QA & Pixel-Diff Baseline Overlay Comparison
*Use this prompt for visual verification tools, document rendering QA, or screenshot regression testing.*

```markdown
Build an Automated Visual QA & Pixel-Diff Comparison Dashboard for [INSERT ARTIFACT, e.g., Rendered PDF Letters vs Legacy Baselines / UI Screenshots].
Strict Requirements:
1. Comparison Grid:
   - 3-Column Split View:
     1. Left: Legacy Sample Baseline Image (150 DPI render).
     2. Middle: Visual Diff Overlay with red variance highlighting (`ImageChops.difference`).
     3. Right: Automated Structure Assertion Checklist (Emblem, Dates, Section Bars, Font, Barcodes).
2. Live Metrics:
   - Match Score percentage banner (e.g., `100.0% Match`).
   - Pixel Variance percentage badge (`0.00% Variance`).
3. Execution Trigger:
   - "Run Real Pixel Diff" button calling backend PyMuPDF/Pillow comparison service.
   - Loading spinner during image generation and instant dismissal of diff modal on close.
```

---

### 7. Live Template Editor with AST / Syntax Guardrail
*Use this prompt when building in-browser code/template editors that write directly to server files.*

```markdown
Build a Real-Time Template & Code Editor Screen for [INSERT LANGUAGE/ENGINE, e.g., Jinja2 HTML / SQL Queries / YAML Pipelines].
Strict Requirements:
1. Filesystem Synchronization:
   - Load file content directly from disk on mount (`GET /api/templates/{id}/source`).
   - Display live disk metadata: actual file size in KB, line count, and last-modified timestamp.
2. Interactive Editor Features:
   - Textarea with monospace font, dark slate background (`#020617`), line numbers, and tab formatting.
   - "Reload from Disk" and "Copy Source" clipboard button with 2-second "Copied!" feedback.
3. Server-Side AST Syntax Guardrail:
   - Clicking "Save Template to Disk" calls `PUT /api/...`.
   - Backend parses AST (e.g., `jinja2.Environment().parse()`).
   - If syntax is broken (e.g., unclosed tag), backend returns `400 Bad Request` with exact line number.
   - Frontend renders a prominent red error banner with the exact syntax message and refuses to corrupt disk storage.
   - If syntax is valid, show a green success notification and update file size/timestamp.
```

---

## 💡 Quick Cheat Sheet: Feature to Prompt Mapping

| Dashboard Feature You Want to Build | Recommended Prompt to Use |
| :--- | :--- |
| **No-scroll Power BI screen with 4 KPI cards** | **Prompt #1** (Zero-Scroll Executive Layout) |
| **Left Table + Right Dynamic Inspector Panel** | **Prompt #2** (Master-Detail Split-Pane) |
| **Multi-column sorting, grouping & status chips** | **Prompt #3** (Hierarchical Matrix Table) |
| **Click row to dive 3 levels deeper with breadcrumbs** | **Prompt #4** (Multi-Level Drilldown) |
| **Live Database Table Verification without Mocks** | **Prompt #5** (Live DB Verification Lineage) |
| **Side-by-side Image / PDF pixel comparison** | **Prompt #6** (Visual QA & Pixel-Diff) |
| **In-browser code editor with save & syntax check** | **Prompt #7** (AST Template Editor) |
