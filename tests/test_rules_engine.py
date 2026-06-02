import csv
import unittest
from pathlib import Path

from smt_quality_agent.dashboard import build_dashboard_summary, build_dashboard_top
from smt_quality_agent.rules_engine import build_quality_cases, run_agent


ROOT = Path(__file__).resolve().parents[1]


class RulesEngineTest(unittest.TestCase):
    def test_sample_data_classification_and_case_grouping(self) -> None:
        with (ROOT / "data" / "sample_spi_data.csv").open("r", encoding="utf-8", newline="") as file:
            rows = list(csv.DictReader(file))

        total_pad_count_by_board = {
            "PCB001": 80,
            "PCB002": 80,
            "PCB003": 80,
            "PCB004": 80,
            "PCB005": 80,
        }

        abnormals = run_agent(rows, total_pad_count_by_board)
        cases = build_quality_cases(abnormals)

        self.assertEqual(len(abnormals), 11)
        self.assertEqual(len(cases), 3)

        patterns = {(case["defect_type"], case["abnormal_pattern"]) for case in cases}
        self.assertIn(("少锡", "连续3板同点异常"), patterns)
        self.assertIn(("多锡", "同一元件多Pad异常"), patterns)
        self.assertIn(("少锡", "整板趋势异常"), patterns)

        low_risk = [
            item for item in abnormals
            if item["component"] == "U10" and item["pad"] == "5"
        ][0]
        self.assertEqual(low_risk["abnormal_pattern"], "单点偶发异常")
        self.assertFalse(low_risk["create_quality_case"])

        summary = build_dashboard_summary(abnormals, cases)
        self.assertEqual(summary["abnormal_count"], 11)
        self.assertEqual(summary["less_solder_count"], 9)
        self.assertEqual(summary["more_solder_count"], 2)
        self.assertEqual(summary["case_count"], 3)
        self.assertEqual(summary["open_case_count"], 3)

        top = build_dashboard_top(abnormals, cases)
        self.assertEqual(top["top_components"][0]["component"], "R125")
        self.assertEqual(top["top_patterns"][0]["abnormal_pattern"], "整板趋势异常")


if __name__ == "__main__":
    unittest.main()
