import csv
import unittest
from pathlib import Path

from smt_quality_agent.dashboard import build_dashboard_summary, build_dashboard_top
from smt_quality_agent.affected_model import normalize_affected_model_row
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
        self.assertIn(("少锡", "同点多板异常"), patterns)
        self.assertIn(("多锡", "同元件多Pad异常"), patterns)
        self.assertIn(("少锡", "整板趋势异常"), patterns)

        low_risk = [
            item for item in abnormals
            if item["component"] == "U10" and item["pad"] == "5"
        ][0]
        self.assertEqual(low_risk["abnormal_pattern"], "单点偶发异常")
        self.assertFalse(low_risk["create_quality_case"])
        self.assertTrue(low_risk["cause_candidates"])
        self.assertTrue(all("rule_id" in item and "rule_source" in item for item in low_risk["cause_candidates"]))

        high_risk = [
            item for item in abnormals
            if item["abnormal_pattern"] == "同点多板异常"
        ][0]
        self.assertEqual(high_risk["root_cause_guess"], [item["cause"] for item in high_risk["cause_candidates"]])

        summary = build_dashboard_summary(abnormals, cases)
        self.assertEqual(summary["abnormal_count"], 11)
        self.assertEqual(summary["less_solder_count"], 9)
        self.assertEqual(summary["more_solder_count"], 2)
        self.assertEqual(summary["case_count"], 3)
        self.assertEqual(summary["open_case_count"], 3)

        top = build_dashboard_top(abnormals, cases)
        self.assertEqual(top["top_components"][0]["component"], "R125")
        self.assertEqual(top["top_patterns"][0]["abnormal_pattern"], "整板趋势异常")
        self.assertTrue(cases[0]["cause_candidates"])

    def test_affected_model_row_mapping(self) -> None:
        row = normalize_affected_model_row({
            "fdate": "2023-11-29 04:28:00",
            "machinename": "GKG-PC",
            "cmodel": "C1027835AB13",
            "barcode": "FOC274807UP",
            "compname": "ISO_3V3_R9_1",
            "comp_errname": "Under Height",
            "comp_avdp": 12.85,
            "comp_aadp": 6.84,
            "comp_ahdp": 10.77,
            "comp_px": -1,
            "comp_py": 35.3,
        })

        abnormals = run_agent([row])

        self.assertEqual(row["component"], "ISO_3V3_R9")
        self.assertEqual(row["pad"], "1")
        self.assertEqual(abnormals[0]["defect_type"], "少锡")
        self.assertEqual(abnormals[0]["main_metric"], "height")
        self.assertEqual(abnormals[0]["deviation_percent"], 10.77)


if __name__ == "__main__":
    unittest.main()
