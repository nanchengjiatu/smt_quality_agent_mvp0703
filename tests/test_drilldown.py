import unittest

from smt_quality_agent.drilldown import (
    WINDOW_RECORDS,
    analyze_periodicity,
    build_drilldown_report,
    build_pad_points,
    detect_trigger_runs,
)


def fdate(minute: int) -> str:
    return f"2024/1/9 {3 + minute // 60}:{minute % 60:02d}"


def make_row(
    barcode: str,
    minute: int,
    compname: str = "C1_1",
    errname: str = "PASS",
    avdp: float = 12.0,
    printspeed_plan: int = 40,
    markdeviation_plan: float = 0.0,
    abs_y1backward: float = 0.0,
) -> dict:
    return {
        "markdeviation_plan": markdeviation_plan,
        "printspeed": float(printspeed_plan),
        "diff_printspeed": 0.0,
        "abs_printspeed": 0.0,
        "fdate": fdate(minute),
        "machinename": "GKG-PC",
        "cmodel": "MODEL-A",
        "barcode": barcode,
        "compname": compname,
        "comp_errname": errname,
        "comp_avdp": avdp,
        "comp_aadp": 11.0,
        "comp_ahdp": 10.0,
        "comp_px": 1.0,
        "comp_py": 2.0,
        "printspeed_plan": printspeed_plan,
        "abs_y1backward": abs_y1backward,
        "temperature": 0,
        "humidity": 0,
    }


def make_boards(specs: list[dict]) -> list[dict]:
    """Each spec describes one production board: keys err/avdp/sibling_err/plan."""
    rows = []
    for index, spec in enumerate(specs):
        barcode = spec.get("barcode", f"B{index:03d}")
        rows.append(make_row(
            barcode,
            index,
            errname=spec.get("err", "PASS"),
            avdp=spec.get("avdp", 12.0),
            printspeed_plan=spec.get("plan", 40),
            markdeviation_plan=spec.get("noise_plan", 0.0),
        ))
        rows.append(make_row(
            barcode,
            index,
            compname="C1_2",
            errname=spec.get("sibling_err", "PASS"),
            printspeed_plan=spec.get("plan", 40),
        ))
        for pad_no in range(1, 5):
            rows.append(make_row(
                barcode,
                index,
                compname=f"R9_{pad_no}",
                printspeed_plan=spec.get("plan", 40),
            ))
    return rows


def trigger_specs(pre: int, ng: int, post: int, **kwargs) -> list[dict]:
    specs = [{"avdp": 12.0} for _ in range(pre)]
    specs += [{"err": "Over Volume", "avdp": 80.0} for _ in range(ng)]
    specs += [{"avdp": 12.0} for _ in range(post)]
    for key, value in kwargs.items():
        specs = value(specs) if callable(value) else specs
    return specs


class DetectTriggerRunsTest(unittest.TestCase):
    def pad_points(self, rows: list[dict]) -> list[dict]:
        return build_pad_points(rows)["MODEL-A"]["C1_1"]

    def test_three_consecutive_ng_boards_trigger(self) -> None:
        rows = make_boards(trigger_specs(5, 3, 2))
        runs = detect_trigger_runs(self.pad_points(rows))
        self.assertEqual(runs, [(5, 7)])

    def test_two_consecutive_ng_boards_do_not_trigger(self) -> None:
        rows = make_boards(trigger_specs(5, 2, 2))
        self.assertEqual(detect_trigger_runs(self.pad_points(rows)), [])

    def test_recheck_neither_breaks_nor_extends_run(self) -> None:
        specs = trigger_specs(2, 3, 1)
        rows = make_boards(specs)
        # Re-inspect the first NG board between the 2nd and 3rd NG boards:
        # the run must still register as 3 consecutive production boards.
        rows.append(make_row("B002", 10, errname="PASS", avdp=12.0))
        runs = detect_trigger_runs(build_pad_points(rows)["MODEL-A"]["C1_1"])
        self.assertEqual(len(runs), 1)
        start, end = runs[0]
        points = build_pad_points(rows)["MODEL-A"]["C1_1"]
        production_ng = [
            point for point in points[start:end + 1] if not point["is_recheck"]
        ]
        self.assertEqual(len(production_ng), 3)


class WindowTest(unittest.TestCase):
    def test_window_counts_reported_honestly_when_short(self) -> None:
        report = build_drilldown_report(make_boards(trigger_specs(10, 3, 4)))
        trigger = report["triggers"][0]
        self.assertEqual(trigger["window"]["requested"], WINDOW_RECORDS)
        self.assertEqual(trigger["window"]["before_count"], 10)
        self.assertEqual(trigger["window"]["after_count"], 4)
        self.assertTrue(any("窗口说明" in item["text"] for item in trigger["findings"]))

    def test_window_clamps_to_300_records(self) -> None:
        report = build_drilldown_report(make_boards(trigger_specs(320, 3, 310)))
        trigger = report["triggers"][0]
        self.assertEqual(trigger["window"]["before_count"], WINDOW_RECORDS)
        self.assertEqual(trigger["window"]["after_count"], WINDOW_RECORDS)
        self.assertEqual(
            len(trigger["series"]), WINDOW_RECORDS + 3 + WINDOW_RECORDS,
        )


class ChangeTypeTest(unittest.TestCase):
    def test_stable_baseline_then_jump_reads_as_step(self) -> None:
        report = build_drilldown_report(make_boards(trigger_specs(30, 3, 0)))
        change = report["triggers"][0]["change_type"]
        self.assertEqual(change["kind"], "step")
        self.assertGreaterEqual(change["jump_ratio"], 2.0)
        self.assertEqual(change["highlight"], [0, 2])

    def test_climbing_baseline_reads_as_gradual(self) -> None:
        specs = [{"avdp": 12.0} for _ in range(20)]
        specs += [{"avdp": 14.0 + 2.0 * step} for step in range(8)]
        specs += [{"err": "Over Volume", "avdp": 80.0} for _ in range(3)]
        report = build_drilldown_report(make_boards(specs))
        change = report["triggers"][0]["change_type"]
        self.assertEqual(change["kind"], "gradual")
        self.assertGreaterEqual(change["tail_consecutive_rise"], 3)

    def test_no_pre_data_is_reported_honestly(self) -> None:
        report = build_drilldown_report(make_boards(trigger_specs(0, 3, 0)))
        change = report["triggers"][0]["change_type"]
        self.assertEqual(change["kind"], "unknown")
        self.assertIn("事件前", change["detail"])


class RecoveryTest(unittest.TestCase):
    def test_recovery_links_to_setpoint_change(self) -> None:
        specs = trigger_specs(10, 3, 0)
        specs += [{"avdp": 12.0, "plan": 35}, {"avdp": 12.0, "plan": 35}]
        report = build_drilldown_report(make_boards(specs))
        recovery = report["triggers"][0]["recovery"]
        self.assertEqual(recovery["kind"], "recovered")
        self.assertEqual(
            [event["parameter"] for event in recovery["related_param_events"]],
            ["printspeed"],
        )

    def test_recovery_without_setpoint_change(self) -> None:
        report = build_drilldown_report(make_boards(trigger_specs(10, 3, 2)))
        recovery = report["triggers"][0]["recovery"]
        self.assertEqual(recovery["kind"], "recovered")
        self.assertEqual(recovery["related_param_events"], [])

    def test_no_post_data(self) -> None:
        report = build_drilldown_report(make_boards(trigger_specs(10, 3, 0)))
        self.assertEqual(report["triggers"][0]["recovery"]["kind"], "no_data")

    def test_passing_but_still_above_baseline_band_is_not_recovered(self) -> None:
        # A PASS board whose value stays far above the baseline band must not
        # count as recovery — the process is still off even if SPI lets it through.
        specs = trigger_specs(10, 3, 0)
        specs += [{"avdp": 50.0}]
        report = build_drilldown_report(make_boards(specs))
        self.assertEqual(report["triggers"][0]["recovery"]["kind"], "not_recovered")


class ParamEventTest(unittest.TestCase):
    def test_plan_field_changing_every_board_is_not_a_setpoint(self) -> None:
        # MarkDeviation_Plan-style columns move on every board — they are
        # measurements, not program setpoints, and must not produce events.
        specs = trigger_specs(10, 3, 2)
        for index, spec in enumerate(specs):
            spec["noise_plan"] = 0.01 * index
        specs[12]["plan"] = 35
        report = build_drilldown_report(make_boards(specs))
        parameters = {event["parameter"] for event in report["triggers"][0]["param_events"]}
        self.assertEqual(parameters, {"printspeed"})


class ParamSeriesTest(unittest.TestCase):
    def test_param_series_aligned_with_window(self) -> None:
        specs = trigger_specs(10, 3, 2)
        specs[12]["plan"] = 35
        report = build_drilldown_report(make_boards(specs))
        trigger = report["triggers"][0]
        param_series = trigger["param_series"]
        self.assertIn("printspeed", param_series["fields"])
        values = param_series["series"]["printspeed"]
        self.assertEqual(len(values), len(trigger["series"]))
        self.assertEqual(values[0]["v"], 40.0)
        self.assertEqual(values[0]["plan"], 40.0)
        self.assertEqual(values[12]["v"], 35.0)


class ScopeTest(unittest.TestCase):
    def test_isolated_pad(self) -> None:
        report = build_drilldown_report(make_boards(trigger_specs(10, 3, 2)))
        scope = report["triggers"][0]["scope"]
        self.assertEqual(scope["kind"], "single")
        self.assertEqual(scope["rule_scope"], "局部焊盘")

    def test_sibling_pad_failing_widens_scope_to_component(self) -> None:
        specs = trigger_specs(10, 3, 2)
        for spec in specs[10:13]:
            spec["sibling_err"] = "Over Volume"
        report = build_drilldown_report(make_boards(specs))
        scope = report["triggers"][0]["scope"]
        self.assertEqual(scope["kind"], "component")
        self.assertIn("C1_2", scope["ng_sibling_pads"])


class PeriodicityTest(unittest.TestCase):
    def test_regular_runs_flag_periodicity(self) -> None:
        specs = []
        for cycle in range(3):
            specs += [{"avdp": 12.0} for _ in range(10)]
            specs += [{"err": "Over Volume", "avdp": 80.0} for _ in range(3)]
        rows = make_boards(specs)
        points = build_pad_points(rows)["MODEL-A"]["C1_1"]
        result = analyze_periodicity(points)
        self.assertTrue(result["periodic"])
        self.assertEqual(result["run_count"], 3)

    def test_single_run_is_not_periodic(self) -> None:
        rows = make_boards(trigger_specs(10, 3, 2))
        points = build_pad_points(rows)["MODEL-A"]["C1_1"]
        self.assertFalse(analyze_periodicity(points)["periodic"])


class ReportShapeTest(unittest.TestCase):
    def test_package_carries_chart_ready_fields(self) -> None:
        report = build_drilldown_report(make_boards(trigger_specs(10, 3, 5)))
        trigger = report["triggers"][0]
        self.assertEqual(trigger["pad_name"], "C1_1")
        self.assertEqual(trigger["component"], "C1")
        self.assertEqual(trigger["main_defect_cn"], "多锡")
        self.assertTrue(trigger["baseline"]["available"])
        self.assertEqual(len(trigger["siblings"]), 1)
        self.assertEqual(len(trigger["heatmap"]), 6)
        self.assertTrue(all("row" not in point for point in trigger["series"]))
        self.assertTrue(trigger["suggested_causes"])
        first = trigger["findings"][0]
        self.assertEqual(first["highlight"], [0, 2])


if __name__ == "__main__":
    unittest.main()
