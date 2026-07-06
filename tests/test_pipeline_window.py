import unittest

from smt_quality_agent.datasource import normalize_datasource
from smt_quality_agent.param_correlation import parse_fdate
from smt_quality_agent.pipeline import (
    cumulative_stats_query,
    fingerprint_query,
    full_excel_query,
)


class FdateOrderingTest(unittest.TestCase):
    """fdate is unpadded text; text ordering breaks across months."""

    def test_text_order_is_wrong_across_months(self) -> None:
        earlier, later = "2024/9/30 10:00", "2024/11/1 10:00"
        self.assertGreater(earlier, later)  # the trap: text says 9月 > 11月
        self.assertLess(parse_fdate(earlier), parse_fdate(later))

    def test_fingerprint_parses_fdate_instead_of_text_max(self) -> None:
        query = fingerprint_query(normalize_datasource({}))
        self.assertIn("to_timestamp", query)
        self.assertNotIn('max("fdate")', query)


class WindowQueryTest(unittest.TestCase):
    def test_zero_window_loads_full_table(self) -> None:
        query = full_excel_query(normalize_datasource({}), window_boards=0)
        self.assertNotIn("limit", query)
        self.assertIn("full_excel", query)

    def test_windowed_query_limits_recent_boards(self) -> None:
        config = normalize_datasource({})
        query = full_excel_query(config, window_boards=1234)
        self.assertIn("limit 1234", query)
        self.assertIn('"barcode"', query)
        self.assertIn("to_timestamp", query)
        self.assertIn("desc nulls last", query)


class CumulativeStatsQueryTest(unittest.TestCase):
    """真累计走全表聚合（只算计数不拉明细），NG 口径与 is_ng 一致。"""

    def test_query_aggregates_whole_table(self) -> None:
        query = cumulative_stats_query(normalize_datasource({}))
        self.assertIn("count(*)", query)
        self.assertIn('count(distinct "barcode")', query)
        self.assertNotIn("limit", query)
        self.assertNotIn("row_to_json", query)

    def test_ng_matches_is_ng_semantics(self) -> None:
        # 非空且 upper != 'PASS'，与 param_correlation.is_ng 相同。
        query = cumulative_stats_query(normalize_datasource({}))
        self.assertIn("nullif(trim(\"comp_errname\"), '') is not null", query)
        self.assertIn("upper(trim(\"comp_errname\")) <> 'PASS'", query)

    def test_time_range_parses_fdate(self) -> None:
        query = cumulative_stats_query(normalize_datasource({}))
        self.assertIn("to_timestamp", query)
        self.assertNotIn('max("fdate")', query)


class WindowConfigTest(unittest.TestCase):
    def test_default_window(self) -> None:
        self.assertEqual(normalize_datasource({})["realtime_window_boards"], 3000)

    def test_explicit_zero_means_unlimited(self) -> None:
        config = normalize_datasource({"realtime_window_boards": 0})
        self.assertEqual(config["realtime_window_boards"], 0)

    def test_garbage_falls_back_to_default(self) -> None:
        config = normalize_datasource({"realtime_window_boards": "abc"})
        self.assertEqual(config["realtime_window_boards"], 3000)

    def test_negative_clamps_to_zero(self) -> None:
        config = normalize_datasource({"realtime_window_boards": -5})
        self.assertEqual(config["realtime_window_boards"], 0)

    def test_window_must_cover_all_analysis_lookbacks(self) -> None:
        from smt_quality_agent.drilldown import FULL_SPI_CONTEXT_WINDOW, PAD_SERIES_WINDOW
        from smt_quality_agent.param_correlation import PRECURSOR_LOOKBACK_BOARDS

        window = normalize_datasource({})["realtime_window_boards"]
        self.assertGreater(window, PRECURSOR_LOOKBACK_BOARDS * 10)
        self.assertGreater(window, PAD_SERIES_WINDOW)
        self.assertGreater(window, FULL_SPI_CONTEXT_WINDOW)


if __name__ == "__main__":
    unittest.main()
