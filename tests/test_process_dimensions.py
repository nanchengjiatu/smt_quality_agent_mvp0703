import unittest

from smt_quality_agent.process_dimensions import (
    GAP_THRESHOLD_MINUTES,
    MIN_FIRST_BOARD_SAMPLES,
    build_process_dimensions,
)


def make_rows(specs):
    """One row per board (single pad); spec keys: minute, avdp, err,
    cleanfreq, direction."""
    rows = []
    for index, spec in enumerate(specs):
        minute = spec.get("minute", index * 3)
        day = 9 + minute // 1440
        hour = (minute % 1440) // 60
        rows.append({
            "cmodel": "M",
            "barcode": f"B{index:04d}",
            "fdate": f"2024/1/{day} {hour}:{minute % 60:02d}",
            "compname": "C1_1",
            "comp_errname": spec.get("err", "PASS"),
            "comp_avdp": spec.get("avdp", 12.0),
            "comp_aadp": 10.0,
            "comp_ahdp": 10.0,
            "cleaningfrequency": spec.get("cleanfreq"),
            "frontsqgpress": 4.0,
            "rearsqgpress": 4.0,
            "printdirection": spec.get("direction", ""),
        })
    return rows


class CleaningCycleTest(unittest.TestCase):
    def test_strong_sawtooth_is_an_effect(self) -> None:
        # Period 10: deviation climbs 0..9pp within each cycle.
        specs = [
            {"avdp": 10.0 + (index % 10), "cleanfreq": 10.0}
            for index in range(300)
        ]
        result = build_process_dimensions(make_rows(specs))["cleaning_cycle"]
        self.assertEqual(result["verdict"], "effect")
        self.assertEqual(result["frequency"], 10)
        self.assertEqual(len(result["profile"]), 10)
        self.assertGreaterEqual(result["amplitude_pp"], 8.0)
        self.assertIn("相位未知", result["caveat"])

    def test_flat_profile_is_no_effect(self) -> None:
        specs = [
            {"avdp": 12.0 + (0.3 if index % 2 else 0.0), "cleanfreq": 10.0}
            for index in range(300)
        ]
        result = build_process_dimensions(make_rows(specs))["cleaning_cycle"]
        self.assertEqual(result["verdict"], "no_effect")

    def test_missing_frequency_is_not_collected(self) -> None:
        result = build_process_dimensions(make_rows([{} for _ in range(50)]))
        self.assertEqual(result["cleaning_cycle"]["verdict"], "not_collected")


class FirstBoardTest(unittest.TestCase):
    def make_gap_specs(self, gap_count, first_avdp):
        """gap_count stoppages; the board after each gap carries first_avdp."""
        specs = []
        minute = 0
        for index in range(gap_count * 10):
            if index and index % 10 == 0:
                minute += GAP_THRESHOLD_MINUTES + 10
                specs.append({"minute": minute, "avdp": first_avdp})
            else:
                minute += 3
                specs.append({"minute": minute, "avdp": 12.0})
        return specs

    def test_enough_bad_first_boards_is_an_effect(self) -> None:
        specs = self.make_gap_specs(MIN_FIRST_BOARD_SAMPLES + 5, 25.0)
        result = build_process_dimensions(make_rows(specs))["first_board"]
        self.assertEqual(result["verdict"], "effect")
        self.assertGreaterEqual(result["first_board_count"], MIN_FIRST_BOARD_SAMPLES)
        self.assertGreater(result["delta_pp"], 0)

    def test_few_gaps_is_insufficient(self) -> None:
        specs = self.make_gap_specs(5, 25.0)
        result = build_process_dimensions(make_rows(specs))["first_board"]
        self.assertEqual(result["verdict"], "insufficient")
        self.assertLess(result["first_board_count"], MIN_FIRST_BOARD_SAMPLES)

    def test_no_gaps_at_all_is_insufficient(self) -> None:
        specs = [{"avdp": 12.0} for _ in range(100)]
        result = build_process_dimensions(make_rows(specs))["first_board"]
        self.assertEqual(result["verdict"], "insufficient")
        self.assertEqual(result["first_board_count"], 0)


class DirectionTest(unittest.TestCase):
    def test_no_direction_data_is_not_collected(self) -> None:
        result = build_process_dimensions(make_rows([{} for _ in range(60)]))
        direction = result["direction"]
        self.assertEqual(direction["verdict"], "not_collected")
        self.assertIn("PrintDirection", direction["detail"])

    def test_direction_groups_with_gap_is_an_effect(self) -> None:
        specs = [
            {"avdp": 10.0 if index % 2 else 22.0,
             "direction": "F2R" if index % 2 else "R2F"}
            for index in range(200)
        ]
        result = build_process_dimensions(make_rows(specs))["direction"]
        self.assertEqual(result["verdict"], "effect")
        self.assertEqual(set(result["groups"]), {"F2R", "R2F"})

    def test_direction_groups_without_gap_is_no_effect(self) -> None:
        specs = [
            {"avdp": 12.0 + (0.2 if index % 2 else 0.0),
             "direction": "F2R" if index % 2 else "R2F"}
            for index in range(200)
        ]
        result = build_process_dimensions(make_rows(specs))["direction"]
        self.assertEqual(result["verdict"], "no_effect")


class ReportShapeTest(unittest.TestCase):
    def test_top_level_shape(self) -> None:
        report = build_process_dimensions(make_rows([{} for _ in range(40)]))
        self.assertEqual(
            set(report),
            {"board_count", "noise_sd_pp", "cleaning_cycle", "first_board", "direction"},
        )
        for key in ("cleaning_cycle", "first_board", "direction"):
            self.assertIn("verdict", report[key])
            self.assertIn("verdict_label", report[key])
            self.assertIn("detail", report[key])


if __name__ == "__main__":
    unittest.main()
