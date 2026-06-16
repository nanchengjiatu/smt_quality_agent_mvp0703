import unittest

from smt_quality_agent.over_volume import normalize_over_volume_row
from smt_quality_agent.rules_engine import build_quality_cases, run_agent


def make_row(barcode: str, compname: str, fdate: str, avdp: float) -> dict:
    return {
        "fdate": fdate,
        "machinename": "GKG-PC",
        "cmodel": "C1024412AB13",
        "barcode": barcode,
        "compname": compname,
        "comp_errname": "Over Volume",
        "comp_avdp": avdp,
        "comp_aadp": 30.0,
        "comp_ahdp": 35.0,
        "comp_px": 1.0,
        "comp_py": 2.0,
        "printspeed_plan": 50.0,
        "printspeed": 50.01,
        "diff_printspeed": 0.01,
        "frontsqgpress_plan": 4.0,
        "frontsqgpress": 4.0,
        "diff_frontsqgpress": 0.0,
        "temperature": 25.3,
        "humidity": 48.6,
    }


class OverVolumeTest(unittest.TestCase):
    def test_row_mapping(self) -> None:
        row = normalize_over_volume_row(
            make_row("FOC28014124", "C9_2", "2024-01-09 03:12:00", 79.59)
        )

        self.assertEqual(row["board_sn"], "FOC28014124")
        self.assertEqual(row["component"], "C9")
        self.assertEqual(row["pad"], "2")
        self.assertEqual(row["raw_ng_type"], "Over Volume")
        self.assertEqual(row["volume_deviation_percent"], 79.59)
        self.assertEqual(row["printspeed_plan"], 50.0)
        self.assertEqual(row["diff_printspeed"], 0.01)
        self.assertEqual(row["temperature"], 25.3)

    def test_repeated_point_becomes_high_risk_case(self) -> None:
        rows = [
            normalize_over_volume_row(make_row(barcode, "C8_2", fdate, 79.0))
            for barcode, fdate in (
                ("FOC28014124", "2024-01-09 03:12:00"),
                ("FOC2801413A", "2024-01-09 03:18:00"),
                ("FOC28014136", "2024-01-09 03:20:00"),
            )
        ]

        abnormals = run_agent(rows)
        cases = build_quality_cases(abnormals)

        self.assertEqual(len(abnormals), 3)
        self.assertEqual(abnormals[0]["defect_type"], "多锡")
        self.assertEqual(abnormals[0]["main_metric"], "volume")
        self.assertEqual(abnormals[0]["abnormal_pattern"], "同点多板异常")
        self.assertEqual(abnormals[0]["risk_level"], "高")
        self.assertEqual(len(cases), 1)
        self.assertEqual(cases[0]["component"], "C8")
        self.assertEqual(cases[0]["pad"], "2")


if __name__ == "__main__":
    unittest.main()
