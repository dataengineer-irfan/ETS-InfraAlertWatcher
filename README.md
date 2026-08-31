\# Expiry Alert System



Tracks database account expiry dates (Cognos DB users across AK, NH, ND) from an Excel

source file, and emails state owners a daily reminder for any account expiring within

15 days — repeating daily until the expiry date is renewed. Includes a Streamlit

dashboard for visualizing expiry status across states, environments, and schema types.



\## How it works



1\. \*\*Ingest\*\* (`src/ingest.py`) reads the Excel workbook (one sheet per state, with a

&#x20;  repeating block structure) and loads it into a local SQLite database.

2\. \*\*Expiry checker\*\* (`src/expiry\_checker.py`) finds accounts within 15 days of expiry

&#x20;  (or already expired) and determines who's due a reminder today.

3\. \*\*Notifier\*\* (`src/notifier.py`) sends one email per due account, using a Jinja2

&#x20;  template. Reminders repeat daily until the source expiry date changes.

4\. \*\*Dashboard\*\* (`dashboard/app.py`) is a Streamlit app visualizing the same database.

5\. \*\*`src/run\_daily.py`\*\* ties ingest → checker → notifier into a single command, meant

&#x20;  to be run on a schedule (cron, Windows Task Scheduler, or GitHub Actions).



\## Setup



```bash

python -m venv venv

source venv/bin/activate      # Windows: venv\\Scripts\\Activate.ps1

pip install -r requirements.txt

```



Place your source Excel file in `data/`, and configure state owners in `config/owners.csv`:

## Component tracking dashboard

Alongside the account expiry pipeline above, four component workbooks are tracked:
`Crypto Keys & CA Certificates.xlsx`, `Database Password Expiry.xlsx`,
`Software Versions & N-1 Tracking.xlsx` and `Upgrade & Patch Tasks.xlsx`. Each has one
sheet per state (AK, NH, ND) with the columns `Environemnts`, `ENV`, `Module` and
`Expiry Dates`.

The four workbooks are committed to this repository, so a fresh clone has everything it needs.
Replace them in place to track a different set of environments, keeping the filenames above.

Read them into SQLite:

```bash
python src/ingest_components.py --data-dir . --db data/expiry.db
```

Then launch the dashboard:

```bash
streamlit run dashboard/app.py
```

The app auto-ingests on first run if the table is empty, and the sidebar has a
**Re-read workbooks** button, so the command above is only needed for scripted refreshes.
Override paths with the `EXPIRY_DB_PATH` and `EXPIRY_WORKBOOK_DIR` environment variables.

### What the dashboard shows

The report is a single fixed screen. Nothing scrolls: the layout is a CSS grid measured
against the space it has been given, and it resizes itself to whatever the browser window
allows. Filters live in the browser, so clicking one repaints immediately rather than
re-running the Python.

**Overview** is the consolidated position across all three states. A slicer strip across the
top filters by component, health status and date window; below it five KPI tiles, then the
four component cards, then the detail table beside a chart of expiries by quarter.

**State** asks which state to look at, then opens two sub-tabs. *Overview* is the same report
pinned to that state: click a component card to list the environments inside it, click an
environment to redraw every tile and chart for that environment alone. The table underneath
always carries Environment, Schema Name, Expiry Date and Health Status, with a Summary toggle
that rolls the same rows up per environment and component. *Manage* is a soonest-first
worklist with editable expiry dates, beside a record of every local edit.

Every card, tile, chip, column header and table cell is a filter. Clicking one narrows the
view, clicking it again releases it, and the breadcrumb row in the header shows what is
applied — `Esc` clears everything. Five saved views in the header set several filters at once:
Everything, Needs attention, Next 90 days, Overdue and Coverage map.

The panel on the right of the component cards switches between three views: **Timeline**, a
rail of every expiry on a square-root time scale so the next three months stay readable next
to 2029; **Environments**, the environments inside the selected component; and **Coverage**,
which pairings are tracked at all. Drilling into a component or an environment switches it for
you.

Because most dates in this data sit years out, 87 of 91 records read Healthy. Near-term
pressure is therefore carried by the timeline and the quarter chart, not by the health bands —
read those first.

### Two dates per record

`component_records` keeps `source_exp_date` (whatever the workbook said) next to `exp_date`
(what the dashboard shows). They only diverge when someone edits a date in Manage, which is
tagged `EDITED` and can be reverted. If the workbook value later changes, the source wins and
the local edit is dropped — the spreadsheet stays the system of record.

The source has no schema column, so one is derived as `ENV<n>_<CODE>` (e.g. `ENV30_CRYPTO`),
matching the `ENV30_COTS_CGNS` convention used elsewhere in this project. Health bands use the
same 15 / 30 day thresholds as the email reminders, so the dashboard and the alerts never
disagree about what counts as critical.

### How the dashboard is put together

`dashboard/ui.py` owns the palette, the health thresholds and the plain-language wording, and
imports neither Streamlit nor the database. `dashboard/report.py` builds the report as one
self-contained HTML document with its own filter engine. `dashboard/app.py` is the host: it
reads the database, provides the tabs and the state chooser, mounts the report, and runs the
Manage editor — the only place that writes.

The split exists so the report can promise a single screen. Streamlit is a vertical document
whose widget heights shift with browser zoom and font substitution, so a stack of panels can
only ever usually fit; inside the report's own frame the fit is arithmetic.

### Tests

No browser and no Streamlit install needed:

```bash
python tests/build_fixture.py      # ground truth from pandas, plus both built pages
node tests/test_report_engine.js   # the report's filter engine and renderers
python tests/test_app_host.py      # the Streamlit wiring and the zero-scroll budget
```

`build_fixture.py` recomputes every total in pandas, independently of the JavaScript that
draws them, so the two implementations check each other rather than the engine checking
itself. `test_report_engine.js` runs the engine sliced out of a real built page, so what is
tested is what ships. `test_app_host.py` executes `app.py` against a recording stand-in for
Streamlit (`tests/mockst/`) to catch duplicate widget keys, missing tabs, and a canvas mounted
taller than the screen it has to fit.


