import json

from smt_quality_agent.dashboard import build_dashboard_summary, build_dashboard_top
from smt_quality_agent.over_volume import normalize_spi_rows
from smt_quality_agent.param_correlation import first_inspection_rows
from smt_quality_agent.pipeline import (
    OUTPUT_DIR,
    load_full_excel_rows,
    write_json,
)
from smt_quality_agent.rules_engine import build_quality_cases, infer_total_pad_counts, run_agent


def main() -> None:
    rows = normalize_spi_rows(first_inspection_rows(load_full_excel_rows()))
    results = run_agent(rows, infer_total_pad_counts(rows))
    quality_cases = build_quality_cases(results)
    dashboard_summary = build_dashboard_summary(results, quality_cases)
    dashboard_top = build_dashboard_top(results, quality_cases)

    print("=== L780DB full SPI Summary ===")
    print(json.dumps(dashboard_summary, ensure_ascii=False, indent=2))
    print("\n=== Dashboard Top ===")
    print(json.dumps(dashboard_top, ensure_ascii=False, indent=2))

    write_json(OUTPUT_DIR / "abnormal_results.json", results)
    write_json(OUTPUT_DIR / "quality_cases.json", quality_cases)
    write_json(OUTPUT_DIR / "dashboard_summary.json", dashboard_summary)
    write_json(OUTPUT_DIR / "dashboard_top.json", dashboard_top)


if __name__ == "__main__":
    main()
