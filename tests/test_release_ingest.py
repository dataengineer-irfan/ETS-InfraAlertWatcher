"""
test_release_ingest.py
======================
Unit tests for multi-state release plan ingestion from _Input/ workbooks.
"""

import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "dashboard"))

from db import (
    get_connection,
    get_release_milestones,
    get_release_schedules,
    get_release_summary_metrics,
    get_rm_portfolio_breakdown,
)
from ingest_releases import run_release_ingest

ROOT = pathlib.Path(__file__).resolve().parent.parent
INPUT_DIR = ROOT / "_Input"


class TestReleaseIngest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(pathlib.Path(self.temp_dir.name) / "test_expiry.db")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_ingest_all_states(self):
        result = run_release_ingest(INPUT_DIR, self.db_path)
        self.assertEqual(result["total_releases_ingested"], 38)
        self.assertGreater(result["total_milestones_ingested"], 400)
        self.assertEqual(sorted(result["states_ingested"]), ["AK", "ND", "NH"])

        conn = get_connection(self.db_path)
        try:
            # Check state counts
            ak_rels = get_release_schedules(conn, state="AK")
            self.assertEqual(len(ak_rels), 12)

            nd_rels = get_release_schedules(conn, state="ND")
            self.assertEqual(len(nd_rels), 12)

            nh_rels = get_release_schedules(conn, state="NH")
            self.assertEqual(len(nh_rels), 14)

            # Check filtering by quarter
            q1_rels = get_release_schedules(conn, quarter="Q1")
            self.assertGreater(len(q1_rels), 0)

            # Check filtering by State RM
            ak_rm_rels = get_release_schedules(conn, rm="Alaska State RM Lead")
            self.assertEqual(len(ak_rm_rels), 12)

            # Check milestone detail lookup
            ak1 = ak_rels[0]["release_id"]
            ms = get_release_milestones(conn, ak1)
            self.assertGreater(len(ms), 10)

            # Check summary metrics
            metrics = get_release_summary_metrics(conn)
            self.assertEqual(metrics["total_releases"], 38)
            self.assertEqual(metrics["total_cutovers"], 38)
            self.assertIsNotNone(metrics["next_release"])

            # Check RM breakdown
            rm_rows = get_rm_portfolio_breakdown(conn)
            self.assertEqual(len(rm_rows), 3)
            rm_states = [r["state"] for r in rm_rows]
            self.assertIn("AK", rm_states)
            self.assertIn("ND", rm_states)
            self.assertIn("NH", rm_states)

        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
