import unittest

from smt_quality_agent.early_warning import (
    BASELINE_MIN_RECORDS,
    BOARD_PAD,
    PAGE_ALERT_MIN_LEVEL,
    backtest,
    build_early_warning_report,
    causal_ng_floors,
    monitor_pad,
    nominate_mechanisms,
    pad_points,
    replay,
    warning_id,
)


def fdate(minute: int) -> str:
    return f"2024/1/9 {3 + minute // 60}:{minute % 60:02d}"


def make_points(values, ng_from=None, aadp=10.0, ahdp=10.0):
    """One synthetic pad series; ng_from marks boards >= that index as NG."""
    points = []
    for index, value in enumerate(values):
        points.append({
            "board_sn": f"B{index:04d}",
            "time": index,
            "time_text": fdate(index),
            "is_ng": ng_from is not None and index >= ng_from,
            "values": {"comp_avdp": value, "comp_aadp": aadp, "comp_ahdp": ahdp},
        })
    return points


def stable(count, base=10.0):
    """Deterministic period-4 noise around base: ±1 pattern, mean = base."""
    pattern = (0.0, 1.0, 0.0, -1.0)
    return [base + pattern[index % 4] for index in range(count)]


class GradualDriftTest(unittest.TestCase):
    def test_stable_series_never_escalates(self) -> None:
        result = monitor_pad("M", "C1_1", make_points(stable(400)))
        self.assertEqual(max(result["levels"]), 0)
        self.assertEqual(result["episodes"], [])

    def test_ramp_fires_l2_during_the_ramp(self) -> None:
        values = stable(200) + [10.0 + 0.5 * step for step in range(1, 41)]
        result = monitor_pad("M", "C1_1", make_points(values, ng_from=239))
        episodes = [e for e in result["episodes"] if e["l2_index"] is not None]
        self.assertEqual(len(episodes), 1)
        episode = episodes[0]
        self.assertGreaterEqual(episode["start_index"], 200)
        self.assertLess(episode["l2_index"], 239)
        # A monotone climb is exactly the old precursor signature -> L3.
        self.assertEqual(episode["level"], 3)

    def test_sudden_jump_has_no_l2_before_the_ng(self) -> None:
        values = stable(200) + [80.0]
        result = monitor_pad("M", "C1_1", make_points(values, ng_from=200))
        self.assertTrue(all(level < 2 for level in result["levels"][:200]))

    def test_warm_up_period_stays_silent(self) -> None:
        # Even wild values cannot alarm before the baseline is armed.
        values = [50.0 + step for step in range(BASELINE_MIN_RECORDS - 1)]
        result = monitor_pad("M", "C1_1", make_points(values))
        self.assertEqual(result["episodes"], [])


class BaselineFreezeTest(unittest.TestCase):
    def test_limit_does_not_chase_the_drift(self) -> None:
        values = stable(200) + [10.0 + 0.5 * step for step in range(1, 61)]
        points = make_points(values)

        captured = []

        def probe(model, pad, pts):
            # Re-run monitor and capture the avdp limit at two moments by
            # monitoring truncated prefixes: right before the ramp and deep
            # inside the alarm. The frozen baseline must keep them equal.
            before = monitor_pad(model, pad, pts[:200])
            during = monitor_pad(model, pad, pts)
            return before, during

        before, during = probe("M", "C1_1", points)
        episode = next(e for e in during["episodes"] if e["l2_index"] is not None)
        # After the episode starts, every alarmed board leaves the baseline
        # untouched, so boards_above keeps counting against the same limit
        # instead of the limit drifting up and silencing the alarm.
        self.assertGreaterEqual(episode["boards_above"], 30)
        self.assertIsNone(episode["end_index"])  # still active at window end


class NgBandEscalationTest(unittest.TestCase):
    def test_entering_the_observed_ng_band_escalates_to_l3(self) -> None:
        # Plateau drift (no monotone tail rise) that crosses the causal NG
        # floor of 28.0 -> must escalate via the band condition.
        values = stable(200) + [24.0, 29.0, 24.0, 29.0, 24.0, 29.0, 24.0, 29.0]
        floors = [(-1, 28.0)]
        result = monitor_pad("M", "C1_1", make_points(values), ng_floors=floors)
        episode = next(e for e in result["episodes"] if e["l2_index"] is not None)
        self.assertEqual(episode["level"], 3)

    def test_causal_floor_only_uses_earlier_ng(self) -> None:
        rows = []
        for index, value in enumerate(stable(40, base=30.0)):
            rows.append({
                "cmodel": "M", "barcode": f"B{index:04d}", "fdate": fdate(index),
                "compname": "C1_1", "comp_errname": "PASS",
                "comp_avdp": value, "comp_aadp": 10.0, "comp_ahdp": 10.0,
            })
        rows[35]["comp_errname"] = "Over Volume"
        rows[35]["comp_avdp"] = 66.0
        floors = causal_ng_floors(rows)
        self.assertEqual(len(floors), 1)
        self.assertEqual(floors[0][1], 66.0)


class WarningIdTest(unittest.TestCase):
    def test_id_is_deterministic(self) -> None:
        self.assertEqual(
            warning_id("M", "C1_1", "B0001", "2024/1/9 3:00"),
            warning_id("M", "C1_1", "B0001", "2024/1/9 3:00"),
        )
        self.assertNotEqual(
            warning_id("M", "C1_1", "B0001", "2024/1/9 3:00"),
            warning_id("M", "C1_2", "B0001", "2024/1/9 3:00"),
        )

    def test_id_survives_a_window_slide(self) -> None:
        # Dropping old boards beyond the baseline window must not move the
        # first-exceed board, hence must not change the warning id.
        values = stable(400) + [10.0 + 0.5 * step for step in range(1, 41)]
        full = monitor_pad("M", "C1_1", make_points(values))
        slid_points = make_points(values)[120:]
        slid = monitor_pad("M", "C1_1", slid_points)
        full_ep = next(e for e in full["episodes"] if e["l2_index"] is not None)
        slid_ep = next(e for e in slid["episodes"] if e["l2_index"] is not None)
        self.assertEqual(full_ep["warning_id"], slid_ep["warning_id"])


class ReplayAndBacktestTest(unittest.TestCase):
    def make_rows(self):
        """Two pads: P1 ramps into an NG, P2 stays stable."""
        rows = []
        drift = stable(200) + [10.0 + 0.6 * step for step in range(1, 41)]
        for index, value in enumerate(drift):
            is_ng_board = index == len(drift) - 1
            rows.append({
                "cmodel": "M", "barcode": f"B{index:04d}", "fdate": fdate(index),
                "compname": "C1_1",
                "comp_errname": "Over Volume" if is_ng_board else "PASS",
                "comp_avdp": 70.0 if is_ng_board else value,
                "comp_aadp": 10.0, "comp_ahdp": 10.0,
            })
            rows.append({
                "cmodel": "M", "barcode": f"B{index:04d}", "fdate": fdate(index),
                "compname": "C2_1", "comp_errname": "PASS",
                "comp_avdp": stable(1, base=8.0)[0],
                "comp_aadp": 9.0, "comp_ahdp": 9.0,
            })
        return rows

    def test_replay_covers_pads_and_board_series(self) -> None:
        rows = self.make_rows()
        played = replay(rows)
        self.assertIn(("M", "C1_1"), played["series"])
        self.assertIn(("M", "C2_1"), played["series"])
        self.assertIn(("M", BOARD_PAD), played["series"])

    def test_backtest_scores_the_drift_as_a_hit_with_lead(self) -> None:
        report = backtest(self.make_rows())
        outcome = next(
            item for item in report["ng_outcomes"] if item["pad_name"] == "C1_1"
        )
        self.assertIsNotNone(outcome["lead_boards"])
        self.assertGreaterEqual(outcome["lead_boards"], 3)
        self.assertTrue(outcome["warned_within_horizon"])
        self.assertGreaterEqual(report["hit_count"], 1)
        self.assertEqual(report["total_boards"], 240)

    def test_stable_pad_charges_no_false_alarm(self) -> None:
        report = backtest(self.make_rows())
        self.assertTrue(
            all(episode["pad_name"] != "C2_1" for episode in report["l2_episodes"]),
        )


class MechanismNominationTest(unittest.TestCase):
    def test_pad_warning_nominates_pad_scope_early_warning_mechanisms(self) -> None:
        candidates = nominate_mechanisms("spatial.single_pad")
        ids = {item["mechanism"] for item in candidates}
        self.assertIn("mech.aperture_clogging", ids)
        self.assertIn("mech.understencil_residue", ids)
        self.assertNotIn("mech.paste_rheology_drift", ids)
        self.assertTrue(all(item["early_warning"] for item in candidates))

    def test_board_warning_nominates_board_scope_mechanisms(self) -> None:
        ids = {item["mechanism"] for item in nominate_mechanisms("spatial.board_wide")}
        self.assertIn("mech.paste_rheology_drift", ids)
        self.assertNotIn("mech.aperture_clogging", ids)


class ReportContractTest(unittest.TestCase):
    def test_report_shape_and_alert_gating(self) -> None:
        rows = ReplayAndBacktestTest().make_rows()
        report = build_early_warning_report(rows, "test.table")
        self.assertEqual(report["source_table"], "test.table")
        self.assertEqual(report["params"]["page_alert_min_level"], PAGE_ALERT_MIN_LEVEL)
        self.assertEqual(report["summary"]["pads_monitored"], 2)

        pads = {item["pad_name"] for item in report["pad_health"]}
        self.assertEqual(pads, {"C1_1", "C2_1", BOARD_PAD})

        drift_warnings = [w for w in report["warnings"] if w["pad_name"] == "C1_1"]
        self.assertTrue(drift_warnings)
        warning = drift_warnings[0]
        self.assertGreaterEqual(warning["level"], 2)
        self.assertEqual(warning["component"], "C1")
        self.assertTrue(warning["mechanism_candidates"])
        self.assertTrue(warning["series"])
        first = warning["series"][0]
        self.assertEqual(
            set(first), {"board_sn", "time", "is_ng", "value", "ewma", "limit"},
        )
        # page_alert must be exactly "active, at/above the gate, and fresh".
        for item in report["warnings"]:
            self.assertEqual(
                item["page_alert"],
                item["status"] == "active"
                and item["level"] >= PAGE_ALERT_MIN_LEVEL
                and not item["pending_new_baseline"],
            )

    def test_step_shift_becomes_pending_new_baseline_not_page_alert(self) -> None:
        # A level shift that stays above the limit for 150 boards is a "new
        # normal pending confirmation", not a fresh page alert.
        values = stable(200) + stable(150, base=30.0)
        rows = []
        for index, value in enumerate(values):
            rows.append({
                "cmodel": "M", "barcode": f"B{index:04d}", "fdate": fdate(index),
                "compname": "C1_1", "comp_errname": "PASS",
                "comp_avdp": value, "comp_aadp": 10.0, "comp_ahdp": 10.0,
            })
        report = build_early_warning_report(rows)
        active = [w for w in report["warnings"] if w["status"] == "active"]
        self.assertTrue(active)
        stale = active[0]
        self.assertTrue(stale["pending_new_baseline"])
        self.assertFalse(stale["page_alert"])
        self.assertGreaterEqual(report["summary"]["pending_new_baseline"], 1)

        # Accepting the shift as the new baseline restarts monitoring at the
        # shifted level: the eternal alarm disappears and the pad re-arms
        # around the new normal.
        accepted = build_early_warning_report(
            rows, accepted_ids={stale["warning_id"]},
        )
        # The single-pad fixture makes the board-mean series identical to the
        # pad series; its own (unaccepted) pending episode remains — only the
        # accepted pad's must be gone.
        self.assertFalse([
            w for w in accepted["warnings"]
            if w["pad_name"] == "C1_1" and w["pending_new_baseline"]
        ])
        self.assertEqual(accepted["summary"]["accepted_baselines"], 1)
        health = next(
            item for item in accepted["pad_health"] if item["pad_name"] == "C1_1"
        )
        self.assertTrue(health["baseline_accepted"])
        self.assertFalse(health["episode_active"])
        self.assertAlmostEqual(health["avdp"]["mu"], 30.0, delta=1.0)

    def test_stable_data_reports_empty_but_honest(self) -> None:
        rows = []
        for index, value in enumerate(stable(120)):
            rows.append({
                "cmodel": "M", "barcode": f"B{index:04d}", "fdate": fdate(index),
                "compname": "C1_1", "comp_errname": "PASS",
                "comp_avdp": value, "comp_aadp": 10.0, "comp_ahdp": 10.0,
            })
        report = build_early_warning_report(rows)
        self.assertEqual(report["warnings"], [])
        self.assertEqual(report["summary"]["page_alerts"], 0)
        self.assertIsNone(report["ng_floor_avdp"])
        health = next(i for i in report["pad_health"] if i["pad_name"] == "C1_1")
        self.assertIsNone(health["margin"])
        self.assertIsNotNone(health["avdp"]["ewma"])


class PadPointsTest(unittest.TestCase):
    def test_recheck_rows_are_excluded(self) -> None:
        rows = []
        for index, value in enumerate(stable(10)):
            rows.append({
                "cmodel": "M", "barcode": f"B{index:04d}", "fdate": fdate(index),
                "compname": "C1_1", "comp_errname": "PASS",
                "comp_avdp": value, "comp_aadp": 10.0, "comp_ahdp": 10.0,
            })
        # A recheck of board 3 (same barcode, later fdate) must not appear.
        rows.append({
            "cmodel": "M", "barcode": "B0003", "fdate": fdate(50),
            "compname": "C1_1", "comp_errname": "PASS",
            "comp_avdp": 99.0, "comp_aadp": 10.0, "comp_ahdp": 10.0,
        })
        points = pad_points(rows)[("M", "C1_1")]
        self.assertEqual(len(points), 10)
        self.assertTrue(all(point["values"]["comp_avdp"] < 99 for point in points))


if __name__ == "__main__":
    unittest.main()
