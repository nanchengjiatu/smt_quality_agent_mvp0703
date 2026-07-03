import unittest

from smt_quality_agent.drilldown import (
    FULL_SPI_CONTEXT_WINDOW,
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
    px: float = 1.0,
    py: float = 2.0,
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
        "comp_px": px,
        "comp_py": py,
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
            px=10.0,
            py=10.0,
        ))
        rows.append(make_row(
            barcode,
            index,
            compname="C1_2",
            errname=spec.get("sibling_err", "PASS"),
            printspeed_plan=spec.get("plan", 40),
            px=12.0,
            py=10.0,
        ))
        for pad_no, px, py in [
            (1, 90.0, 10.0),
            (2, 90.0, 90.0),
            (3, 10.0, 90.0),
            (4, 50.0, 50.0),
        ]:
            rows.append(make_row(
                barcode,
                index,
                compname=f"R9_{pad_no}",
                printspeed_plan=spec.get("plan", 40),
                px=px,
                py=py,
            ))
    return rows


def trigger_specs(pre: int, ng: int, post: int, **kwargs) -> list[dict]:
    specs = [{"avdp": 12.0} for _ in range(pre)]
    specs += [{"err": "Over Volume", "avdp": 80.0} for _ in range(ng)]
    specs += [{"avdp": 12.0} for _ in range(post)]
    for key, value in kwargs.items():
        specs = value(specs) if callable(value) else specs
    return specs


def first_contract(rows: list[dict]) -> dict:
    return build_drilldown_report(rows)["triggers"][0]["analysis_contract"]


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

    def test_full_spi_window_uses_full_rows_not_same_pad_only(self) -> None:
        report = build_drilldown_report(make_boards(trigger_specs(10, 3, 5)))
        trigger = report["triggers"][0]
        full_window = trigger["full_spi_window"]
        self.assertEqual(full_window["requested_before"], FULL_SPI_CONTEXT_WINDOW)
        self.assertEqual(full_window["requested_after"], FULL_SPI_CONTEXT_WINDOW)
        self.assertEqual(full_window["actual_before"], 60)
        self.assertEqual(full_window["actual_after"], 35)
        self.assertEqual(len(full_window["rows"]), 108)
        self.assertGreater(
            len({row["pad_name"] for row in full_window["rows"]}),
            1,
        )


class ChangeTypeTest(unittest.TestCase):
    def test_stable_baseline_then_jump_reads_as_step(self) -> None:
        contract = first_contract(make_boards(trigger_specs(30, 3, 0)))
        self.assertEqual(contract["trend"]["kind"], "step")
        self.assertIn("突变型", contract["trend"]["verdict"])
        self.assertIn("倍", contract["trend"]["detail"])

    def test_climbing_baseline_reads_as_gradual(self) -> None:
        specs = [{"avdp": 12.0} for _ in range(20)]
        specs += [{"avdp": 14.0 + 2.0 * step} for step in range(8)]
        specs += [{"err": "Over Volume", "avdp": 80.0} for _ in range(3)]
        contract = first_contract(make_boards(specs))
        self.assertEqual(contract["trend"]["kind"], "gradual")
        self.assertIn("爬升", contract["trend"]["detail"])

    def test_no_pre_data_is_reported_honestly(self) -> None:
        contract = first_contract(make_boards(trigger_specs(0, 3, 0)))
        self.assertEqual(contract["trend"]["kind"], "unknown")
        self.assertIn("事件前", contract["trend"]["detail"])

    def test_trend_finding_carries_chart_highlight(self) -> None:
        report = build_drilldown_report(make_boards(trigger_specs(30, 3, 0)))
        trigger = report["triggers"][0]
        trend_finding = trigger["findings"][1]
        self.assertEqual(trend_finding["highlight"], [0, 2])


class RecoveryTest(unittest.TestCase):
    def test_recovery_links_to_setpoint_change(self) -> None:
        specs = trigger_specs(10, 3, 0)
        specs += [{"avdp": 12.0, "plan": 35}, {"avdp": 12.0, "plan": 35}]
        contract = first_contract(make_boards(specs))
        self.assertEqual(contract["recheck"]["recovery_kind"], "recovered")
        self.assertIn("printspeed", contract["recheck"]["recovery_detail"])

    def test_recovery_without_setpoint_change(self) -> None:
        contract = first_contract(make_boards(trigger_specs(10, 3, 2)))
        self.assertEqual(contract["recheck"]["recovery_kind"], "recovered")
        self.assertIn("未记录", contract["recheck"]["recovery_detail"])

    def test_no_post_data(self) -> None:
        contract = first_contract(make_boards(trigger_specs(10, 3, 0)))
        self.assertEqual(contract["recheck"]["recovery_kind"], "no_data")

    def test_passing_but_still_above_baseline_band_is_not_recovered(self) -> None:
        # A PASS board whose value stays far above the baseline band must not
        # count as recovery — the process is still off even if SPI lets it through.
        specs = trigger_specs(10, 3, 0)
        specs += [{"avdp": 50.0}]
        contract = first_contract(make_boards(specs))
        self.assertEqual(contract["recheck"]["recovery_kind"], "not_recovered")


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


def make_uniform_boards(pad_count: int, pre: int, ng: int) -> list[dict]:
    """Boards where every pad is PASS in the pre phase and NG in the ng phase.
    Pads belong to different components at spread-out coordinates, so neither
    the component nor the local-area classification can fire."""
    pads = ["C1_1"] + [f"F{index}_1" for index in range(2, pad_count + 1)]
    rows = []
    for index in range(pre + ng):
        barcode = f"B{index:03d}"
        is_ng = index >= pre
        for pad_no, pad in enumerate(pads):
            rows.append(make_row(
                barcode,
                index,
                compname=pad,
                errname="Over Volume" if is_ng else "PASS",
                avdp=80.0 if is_ng else 12.0,
                px=float(5 + pad_no * 7),
                py=float(3 + (pad_no % 4) * 25),
            ))
    return rows


def contract_for_pad(rows: list[dict], pad_name: str) -> dict:
    report = build_drilldown_report(rows)
    trigger = next(
        item for item in report["triggers"] if item["pad_name"] == pad_name
    )
    return trigger["analysis_contract"]


class BoardWideThresholdTest(unittest.TestCase):
    def test_tiny_board_full_ng_is_not_board_wide(self) -> None:
        # 一块只有 4 个检测点的板全部 NG，占比 100% 但样本太少，不允许
        # 判为整板同向。
        rows = make_uniform_boards(pad_count=4, pre=10, ng=3)
        contract = contract_for_pad(rows, "C1_1")
        self.assertNotEqual(contract["scope"]["category"], "整板同向")

    def test_board_with_enough_rows_is_board_wide(self) -> None:
        rows = make_uniform_boards(pad_count=12, pre=10, ng=3)
        contract = contract_for_pad(rows, "C1_1")
        self.assertEqual(contract["scope"]["category"], "整板同向")


class ScopeTest(unittest.TestCase):
    def test_areaover_alias_uses_area_metric_and_over_direction(self) -> None:
        specs = trigger_specs(10, 3, 2)
        for spec in specs[10:13]:
            spec["err"] = "AREAOVER"
        trigger = build_drilldown_report(make_boards(specs))["triggers"][0]
        self.assertEqual(trigger["direction"], "多锡")
        self.assertEqual(trigger["metric_field"], "comp_aadp")
        self.assertEqual(trigger["main_defect_cn"], "多锡(面积)")

    def test_isolated_pad(self) -> None:
        contract = first_contract(make_boards(trigger_specs(10, 3, 2)))
        self.assertEqual(contract["scope"]["category"], "单Pad孤立异常")
        self.assertEqual(
            contract["scope"]["ontology_ids"]["scope"],
            "scope.single_pad_isolated",
        )

    def test_sibling_pad_failing_widens_scope_to_component(self) -> None:
        specs = trigger_specs(10, 3, 2)
        for spec in specs[10:13]:
            spec["sibling_err"] = "Over Volume"
        contract = first_contract(make_boards(specs))
        self.assertEqual(contract["scope"]["category"], "同元件多Pad异常")
        self.assertIn("C1_2", contract["scope"]["detail"])

    def test_local_area_cluster_gets_own_category(self) -> None:
        specs = trigger_specs(10, 3, 2)
        rows = make_boards(specs)
        for board_index in range(10, 13):
            barcode = f"B{board_index:03d}"
            rows.append(make_row(
                barcode, board_index, compname="C2_1", errname="Over Volume",
                avdp=78.0, px=13.0, py=12.0,
            ))
            rows.append(make_row(
                barcode, board_index, compname="C3_1", errname="Over Volume",
                avdp=76.0, px=14.0, py=13.0,
            ))
        contract = build_drilldown_report(rows)["triggers"][0]["analysis_contract"]
        self.assertEqual(contract["scope"]["category"], "局部区域")
        self.assertTrue(contract["evidence"]["context"]["local_area"]["detected"])
        self.assertEqual(contract["disposition"]["priority"], "P1")
        self.assertIn("立即现场排查", contract["disposition"]["suggestion"])

    def test_strong_spi_false_alarm_signal_overrides_scope_category(self) -> None:
        specs = trigger_specs(10, 3, 2)
        for spec in specs[10:13]:
            spec["avdp"] = 12.0
        contract = first_contract(make_boards(specs))
        self.assertEqual(contract["scope"]["category"], "疑似SPI假异常")
        self.assertEqual(
            contract["scope"]["ontology_ids"]["scope"],
            "scope.suspected_spi_false_alarm",
        )
        spi_check = next(
            item for item in contract["evidence"]["exclusion_checks"]
            if item["name"] == "SPI 假异常"
        )
        self.assertEqual(spi_check["status"], "suspect")
        candidates = contract["root_cause_candidates"]
        self.assertEqual(candidates[0]["cause"], "SPI程序阈值或识别框异常")
        self.assertEqual(candidates[0]["evidence_level"], "高")

    def test_isolated_over_volume_gets_pad_specific_cause_and_action(self) -> None:
        contract = first_contract(make_boards(trigger_specs(10, 3, 2)))
        candidates = contract["root_cause_candidates"]
        self.assertEqual(candidates[0]["cause"], "钢网单孔底部残锡或开口异常")
        self.assertIn("钢网孔", candidates[0]["action"])
        self.assertEqual(
            [item["priority"] for item in candidates],
            list(range(1, len(candidates) + 1)),
        )

    def test_candidates_are_ranked_by_confidence(self) -> None:
        contract = first_contract(make_boards(trigger_specs(10, 3, 2)))
        confidences = [item["confidence_base"] for item in contract["root_cause_candidates"]]
        self.assertEqual(confidences, sorted(confidences, reverse=True))


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

    def test_periodic_evidence_prioritizes_maintenance_cycle(self) -> None:
        specs = []
        for _ in range(3):
            specs += [{"avdp": 12.0} for _ in range(10)]
            specs += [{"err": "Over Volume", "avdp": 80.0} for _ in range(3)]
        contract = first_contract(make_boards(specs))
        candidates = contract["root_cause_candidates"]
        self.assertEqual(candidates[0]["cause"], "钢网清洗或锡膏维护周期不匹配")
        self.assertEqual(candidates[0]["evidence_level"], "高")


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
        first = trigger["findings"][0]
        self.assertEqual(first["highlight"], [0, 2])

    def test_conclusion_lives_only_in_the_analysis_contract(self) -> None:
        # 单一契约：旧的 conclusion / agent_output / case_context 包装不再出现。
        report = build_drilldown_report(make_boards(trigger_specs(10, 3, 5)))
        trigger = report["triggers"][0]
        for legacy_key in (
            "conclusion", "agent_output", "case_context", "scope",
            "scope_classification", "exclusion_checks", "context_summary",
            "change_type", "recovery", "periodicity",
            "suggested_causes", "suggested_actions",
        ):
            self.assertNotIn(legacy_key, trigger, legacy_key)
        self.assertIn("analysis_contract", trigger)

    def test_package_carries_canonical_analysis_contract(self) -> None:
        report = build_drilldown_report(make_boards(trigger_specs(10, 3, 5)))
        contract = report["triggers"][0]["analysis_contract"]

        self.assertEqual(contract["version"], "analysis-contract-v2")
        self.assertEqual(
            set(contract),
            {
                "version",
                "trigger",
                "trend",
                "scope",
                "evidence",
                "root_cause_candidates",
                "disposition",
                "recheck",
            },
        )
        self.assertEqual(contract["trigger"]["trigger_id"], "TRG001")
        self.assertEqual(contract["trigger"]["trigger_board_count"], 3)
        self.assertEqual(contract["trigger"]["defect_cn"], "多锡")
        self.assertIn("连续 3 块生产板", contract["trigger"]["conclusion"])
        self.assertIn(
            contract["scope"]["category"],
            {"单Pad孤立异常", "同元件多Pad异常", "局部区域", "整板同向", "疑似SPI假异常"},
        )
        self.assertIn(contract["scope"]["confidence"], {"高", "中", "低"})
        self.assertGreaterEqual(len(contract["evidence"]["summary"]), 5)
        self.assertEqual(
            [item["name"] for item in contract["evidence"]["exclusion_checks"]],
            ["数据连续性", "SPI 假异常"],
        )
        self.assertIn("context", contract["evidence"])
        self.assertTrue(contract["evidence"]["tags"])
        self.assertTrue(contract["root_cause_candidates"])
        self.assertTrue(all(
            "rule_id" in item and "rule_source" in item and "confidence_base" in item
            for item in contract["root_cause_candidates"]
        ))
        self.assertIn(contract["disposition"]["priority"], {"P1", "P2", "P3"})
        self.assertTrue(contract["disposition"]["primary_rule_id"].startswith("rule."))
        self.assertTrue(contract["disposition"]["primary_action"])
        self.assertTrue(contract["recheck"]["criteria"])


if __name__ == "__main__":
    unittest.main()
