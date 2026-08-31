"""
test_app_host.py — the Streamlit wiring and the zero-scroll budget
=================================================================
Runs dashboard/app.py against a recording stand-in for Streamlit
(tests/mockst) and asserts on the structure it produced. Needs no browser and
no Streamlit install.

    python3 tests/build_fixture.py     # ground truth + built pages
    node tests/test_report_engine.js   # the report engine
    python3 tests/test_app_host.py     # this file: the host around it

Three things are checked that the engine suite cannot see:

1. Navigation. The two main tabs, the state prompt, the two sub-tabs, and the
   promise that AK, NH and ND all behave identically.
2. Widget keys. Streamlit raises on a duplicate key, and a duplicate is easy to
   introduce when the same panel is rendered per state. The mock records every
   key so a collision fails here instead of at runtime.
3. The height budget. The brief forbids page scrolling under any circumstance,
   which is an arithmetic claim: the canvas grid's minimum height has to fit
   inside the height app.py mounts it at, which in turn has to fit the smallest
   screen the report is meant for. Both sides are read out of the source rather
   than restated, so changing either one without the other fails.
"""

from __future__ import annotations

import collections
import os
import pathlib
import re
import runpy
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests" / "mockst"))
sys.path.insert(0, str(ROOT / "dashboard"))
os.environ.setdefault("EXPIRY_DB_PATH", str(ROOT / "data" / "expiry.db"))

import streamlit as st  # noqa: E402  (the mock, via the path above)

# The smallest screen the report is designed for: a 1366x768 laptop, whose
# viewport is roughly 640px once browser chrome is accounted for.
SMALL_VIEWPORT = 640

FAILS = []


def check(name: str, ok, detail="") -> None:
    if ok:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name}   {detail}")
        FAILS.append(name)


def run(state: str | None = None) -> dict:
    """Execute app.py once with a given state pre-selected."""
    for store in (st.LOG, st.KEYS, st.HTML, st.MOUNTS, st.STOPPED):
        store.clear()
    st.session_state.clear()
    if state:
        st.session_state["st_state"] = state
    try:
        runpy.run_path(str(ROOT / "dashboard" / "app.py"), run_name="__main__")
    except st.Stop:
        pass
    return {"log": list(st.LOG), "keys": list(st.KEYS),
            "html": list(st.HTML), "mounts": list(st.MOUNTS)}


def grid_minimum() -> int:
    """
    The shortest the canvas can be drawn, read out of report.py's stylesheet.

    `.shell` declares five rows as `auto`, `auto` and three `minmax(Npx, Mfr)`.
    The auto rows are the header and the slicer strip, whose real heights come
    from their own padding and type; 44 and 36 are what they measure. The rest
    is the three minmax floors plus four gaps.
    """
    import report

    row_line = re.search(r"grid-template-rows:([^;]+);", report._CSS).group(1)
    floors = [int(n) for n in re.findall(r"minmax\((\d+)px", row_line)]
    gap = int(re.search(r"--gap:(\d+)px", report._CSS).group(1))
    auto_rows = 44 + 36
    padding = gap * 2
    return auto_rows + sum(floors) + gap * 4 + padding


def main() -> int:
    print("[1] no state chosen — the prompt path")
    r = run()
    dups = [k for k, n in collections.Counter(r["keys"]).items() if n > 1]
    check("no duplicate widget keys", not dups, dups)
    check("one canvas mounted", len(r["mounts"]) == 1, f"{len(r['mounts'])} mounts")
    check("main tabs include Overview and State",
          any(e[0] == "tabs" and "Overview" in e[1] and "State" in e[1] for e in r["log"]))
    check("no sub-tabs before a state is picked",
          not any(e[0] == "tabs" and e[1] == ("Overview", "Manage") for e in r["log"]))
    check("the user is prompted to pick a state",
          any("Pick a state above" in h for h in r["html"]))
    check("all three states are offered",
          all(("button", s) in r["log"] for s in ("AK", "NH", "ND")))

    print("\n[2] AK chosen — the full path")
    r = run("AK")
    dups = [k for k, n in collections.Counter(r["keys"]).items() if n > 1]
    check("no duplicate widget keys", not dups, dups)
    check("two canvases mounted", len(r["mounts"]) == 2, f"{len(r['mounts'])} mounts")
    check("sub-tabs are Overview then Manage",
          ("tabs", ("Overview", "Manage")) in r["log"])
    check("consolidated canvas covers every state",
          '"mode":"all"' in r["mounts"][0]["body"]
          and '"state":null' in r["mounts"][0]["body"])
    check("state canvas is pinned to AK",
          '"mode":"state"' in r["mounts"][1]["body"]
          and '"state":"AK"' in r["mounts"][1]["body"])
    check("every canvas has scrolling switched off",
          all(m["scrolling"] is False for m in r["mounts"]))

    editors = [e for e in r["log"] if e[0] == "data_editor"]
    check("the Manage editor has a fixed height so the page cannot grow",
          editors and isinstance(editors[0][2], int), editors)
    check("the editor exposes the four columns the brief names",
          editors and {"schema_name", "env_label", "exp_dt", "band"} <= set(editors[0][1]),
          editors)

    print("\n[3] the zero-scroll budget")
    minimum = grid_minimum()
    heights = [m["height"] for m in r["mounts"]]
    print(f"       canvas grid minimum {minimum}px · mounted at {heights} · "
          f"small viewport {SMALL_VIEWPORT}px")
    check("the grid fits inside every mounted height",
          all(h >= minimum for h in heights), f"{heights} vs {minimum}")
    check("no mounted height overflows the smallest screen",
          all(h <= SMALL_VIEWPORT - 40 for h in heights), heights)
    check("the state canvas leaves room for the chooser and sub-tabs",
          heights[1] <= heights[0] - 60, heights)

    print("\n[4] every state behaves identically")
    shapes = {}
    for state in ("AK", "NH", "ND"):
        r_state = run(state)
        shapes[state] = (len(r_state["mounts"]),
                         tuple(sorted(r_state["keys"])),
                         tuple(e[0] for e in r_state["log"]))
    check("AK, NH and ND produce the same widget structure",
          shapes["AK"] == shapes["NH"] == shapes["ND"],
          {k: v[0] for k, v in shapes.items()})

    print("\n[5] markup the host renders itself")
    r = run("AK")
    blob = "\n".join(r["html"])
    leaks = re.findall(r"\b(?:nan|NaT|None|undefined)\b|\[object Object\]", blob)
    check("no placeholder values leaked into the page", not leaks, set(leaks))
    check("no class names left over from the old dark theme",
          not re.search(r'class="(?:tbl|schema|comp|env-pill)"', blob))
    check("no reference to the deleted --faint token", "--faint" not in blob)

    print("\n[6] the four column names the brief specifies reach the canvas")
    body = r["mounts"][1]["body"]
    for column in ("Environment", "Schema Name", "Expiry Date", "Health Status"):
        check(f"canvas declares {column!r}", f'"{column}"' in body)

    print()
    if FAILS:
        print(f"{len(FAILS)} check(s) failed: " + ", ".join(FAILS))
        return 1
    print("app host: all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
