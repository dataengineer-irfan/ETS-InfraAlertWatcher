import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from db import (
    get_connection,
    get_active_on_call_roster,
    get_on_call_production_support,
    get_on_call_shifts,
    get_on_call_escalations,
)


def test_roster_ingestion_and_queries():
    conn = get_connection(str(ROOT / "data" / "expiry.db"))
    roster = get_active_on_call_roster(conn)
    assert roster is not None, "Roster should not be None"
    assert roster["valid_from"] == "2026-09-12"
    assert roster["valid_to"] == "2026-09-18"

    ps = get_on_call_production_support(conn, roster["id"])
    assert len(ps) == 217, f"Expected 217 PS records, got {len(ps)}"
    distinct_ps = set(r["resource_name"] for r in ps)
    assert len(distinct_ps) == 31, f"Expected 31 distinct PS resources, got {len(distinct_ps)}"

    infra_shifts = get_on_call_shifts(conn, roster["id"], division="Infra Team")
    assert len(infra_shifts) == 168, f"Expected 168 infra shifts, got {len(infra_shifts)}" # 6 domains * 28 slots

    core_shifts = get_on_call_shifts(conn, roster["id"], division="Core Dev")
    assert len(core_shifts) == 84, f"Expected 84 core shifts, got {len(core_shifts)}" # 3 states * 28 slots

    nc_shifts = get_on_call_shifts(conn, roster["id"], division="Non-Core Dev")
    assert len(nc_shifts) == 140, f"Expected 140 non-core shifts, got {len(nc_shifts)}" # 5 domains * 28 slots

    escs = get_on_call_escalations(conn, roster["id"])
    assert len(escs) > 0, "Expected escalations"

    conn.close()
    print("[PASS] test_roster_ingestion_and_queries passed completely!")


if __name__ == "__main__":
    test_roster_ingestion_and_queries()
