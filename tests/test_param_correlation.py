import unittest

from smt_quality_agent.param_correlation import (
    aggregate_boards,
    analyze_precursor,
    build_param_analysis,
    check_parameters,
    detect_events,
    linear_slope,
    parse_fdate,
    tail_consecutive_rise,
)


def make_row(
    barcode: str,
    fdate: str,
    errname: str = "PASS",
    avdp: float = 12.0,
    ahdp: float = 10.0,
    cmodel: str = "MODEL-A",
    compname: str = "C1_1",
    abs_printspeed: float = 0.0,
) -> dict:
    return {
        "fdate": fdate,
        "machinename": "GKG-PC",
        "cmodel": cmodel,
        "barcode": barcode,
        "compname": compname,
        "comp_errname": errname,
        "comp_avdp": avdp,
        "comp_aadp": 11.0,
        "comp_ahdp": ahdp,
        "abs_printspeed": abs_printspeed,
        "temperature": 0,
        "humidity": 0,
    }


class ParseFdateTest(unittest.TestCase):
    def test_parses_non_padded_slash_format(self) -> None:
        parsed = parse_fdate("2024/1/9 3:12")
        self.assertIsNotNone(parsed)
        self.assertEqual((parsed.month, parsed.day, parsed.hour), (1, 9, 3))

    def test_orders_correctly_despite_text_storage(self) -> None:
        self.assertLess(parse_fdate("2024/1/9 3:12"), parse_fdate("2024/1/12 10:50"))

    def test_returns_none_for_empty(self) -> None:
        self.assertIsNone(parse_fdate(""))
        self.assertIsNone(parse_fdate(None))


class AggregateBoardsTest(unittest.TestCase):
    def test_marks_later_inspection_of_same_board_as_recheck(self) -> None:
        rows = [
            make_row("B1", "2024/1/12 10:50", "Under Height"),
            make_row("B1", "2024/1/12 10:56", "PASS"),
        ]
        boards = aggregate_boards(rows)

        self.assertEqual(len(boards), 2)
        self.assertFalse(boards[0]["is_recheck"])
        self.assertTrue(boards[1]["is_recheck"])
        self.assertEqual(boards[0]["ng_share"], 1.0)
        self.assertEqual(boards[1]["ng_count"], 0)


class DetectEventsTest(unittest.TestCase):
    def test_clusters_ng_boards_within_gap(self) -> None:
        rows = [
            make_row("B1", "2024/1/9 3:12", "Over Volume"),
            make_row("B2", "2024/1/9 3:18", "Over Volume"),
            make_row("B3", "2024/1/9 9:00", "Over Volume"),
            make_row("B4", "2024/1/9 9:05", "PASS"),
        ]
        clusters = detect_events(aggregate_boards(rows))

        self.assertEqual(len(clusters), 2)
        self.assertEqual([board["board_sn"] for board in clusters[0]], ["B1", "B2"])
        self.assertEqual([board["board_sn"] for board in clusters[1]], ["B3"])

    def test_does_not_merge_events_across_models(self) -> None:
        rows = [
            make_row("B1", "2024/1/9 3:12", "Over Volume", cmodel="MODEL-A"),
            make_row("B2", "2024/1/9 3:14", "Over Volume", cmodel="MODEL-B"),
        ]
        clusters = detect_events(aggregate_boards(rows))
        self.assertEqual(len(clusters), 2)


class PrecursorTest(unittest.TestCase):
    def build_boards(self, values: list[float]) -> list[dict]:
        rows = [
            make_row(f"B{index}", f"2024/1/9 3:{index:02d}", "PASS", avdp=value)
            for index, value in enumerate(values)
        ]
        rows.append(make_row("BNG", "2024/1/9 3:59", "Over Volume", avdp=80.0))
        return aggregate_boards(rows)

    def test_detects_rising_precursor(self) -> None:
        boards = self.build_boards([10, 12, 14, 16, 18, 20, 22, 24])
        cluster = [board for board in boards if board["ng_count"] > 0]
        precursor = analyze_precursor(cluster, boards, "comp_avdp")

        self.assertTrue(precursor["has_precursor"])
        self.assertEqual(precursor["verdict"], "有前兆（渐变型）")

    def test_flat_baseline_means_sudden_event(self) -> None:
        boards = self.build_boards([12, 12.2, 11.8, 12.1, 11.9, 12.0, 12.1, 11.8])
        cluster = [board for board in boards if board["ng_count"] > 0]
        precursor = analyze_precursor(cluster, boards, "comp_avdp")

        self.assertFalse(precursor["has_precursor"])
        self.assertEqual(precursor["verdict"], "无明显前兆（突发型）")

    def test_no_baseline_data(self) -> None:
        boards = aggregate_boards([make_row("BNG", "2024/1/9 3:00", "Over Volume")])
        precursor = analyze_precursor(boards, boards, "comp_avdp")

        self.assertFalse(precursor["available"])
        self.assertEqual(precursor["verdict"], "无事件前数据")


class TrendHelpersTest(unittest.TestCase):
    def test_linear_slope(self) -> None:
        self.assertAlmostEqual(linear_slope([0, 1, 2, 3]), 1.0)
        self.assertAlmostEqual(linear_slope([5, 5, 5]), 0.0)

    def test_tail_consecutive_rise(self) -> None:
        self.assertEqual(tail_consecutive_rise([5, 4, 5, 6, 7]), 3)
        self.assertEqual(tail_consecutive_rise([5, 4]), 0)


class CheckParametersTest(unittest.TestCase):
    def test_flags_parameter_beyond_baseline(self) -> None:
        rows = [
            make_row("B1", "2024/1/9 3:00", "PASS", abs_printspeed=0.02),
            make_row("B2", "2024/1/9 3:05", "Over Volume", abs_printspeed=0.50),
        ]
        result = check_parameters(rows, {"B2"}, "MODEL-A")

        self.assertEqual(len(result["drifted"]), 1)
        self.assertEqual(result["drifted"][0]["parameter"], "printspeed")
        self.assertFalse(result["cross_model_baseline"])

    def test_stable_parameters_excluded_with_verdict(self) -> None:
        rows = [
            make_row("B1", "2024/1/9 3:00", "PASS", abs_printspeed=0.02),
            make_row("B2", "2024/1/9 3:05", "Over Volume", abs_printspeed=0.01),
        ]
        result = check_parameters(rows, {"B2"}, "MODEL-A")

        self.assertEqual(result["drifted"], [])
        self.assertIn("排除设备参数漂移", result["verdict"])

    def test_cross_model_baseline_is_annotated(self) -> None:
        rows = [
            make_row("B1", "2024/1/9 3:00", "PASS", cmodel="MODEL-B"),
            make_row("B2", "2024/1/9 3:05", "Over Volume", cmodel="MODEL-A"),
        ]
        result = check_parameters(rows, {"B2"}, "MODEL-A")

        self.assertTrue(result["cross_model_baseline"])
        self.assertIn("跨机种对比", result["verdict"])


class BuildParamAnalysisTest(unittest.TestCase):
    def test_end_to_end_with_recheck(self) -> None:
        rows = []
        for index in range(5):
            rows.append(make_row(f"B{index}", f"2024/1/9 3:{index:02d}", "PASS"))
        rows.append(make_row("BNG", "2024/1/9 3:10", "Under Height", ahdp=95.0))
        rows.append(make_row("BNG", "2024/1/9 3:16", "PASS", ahdp=9.0))

        analysis = build_param_analysis(rows)
        overview = analysis["data_overview"]

        self.assertEqual(overview["board_count"], 6)
        self.assertEqual(overview["inspection_count"], 7)
        self.assertEqual(overview["recheck_count"], 1)
        self.assertEqual(overview["recheck_effective_rate"], 1.0)
        self.assertEqual(len(analysis["events"]), 1)

        event = analysis["events"][0]
        self.assertEqual(event["main_defect_cn"], "少锡(高度不足)")
        self.assertEqual(event["scope"], "整板大面积")
        self.assertEqual(event["recheck"]["passed_board_count"], 1)
        self.assertTrue(any("复测" in finding for finding in event["findings"]))
        self.assertTrue(event["suggested_causes"])


if __name__ == "__main__":
    unittest.main()
