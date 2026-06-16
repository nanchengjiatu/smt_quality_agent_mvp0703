import json

from smt_quality_agent.dashboard import build_dashboard_summary, build_dashboard_top
from smt_quality_agent.over_volume import normalize_over_volume_rows
from smt_quality_agent.pipeline import (
    OUTPUT_DIR,
    load_over_volume_rows,
    write_json,
)
from smt_quality_agent.rules_engine import build_quality_cases, run_agent


def main() -> None:
    rows = normalize_over_volume_rows(load_over_volume_rows())
    results = run_agent(rows)
    quality_cases = build_quality_cases(results)
    dashboard_summary = build_dashboard_summary(results, quality_cases)
    dashboard_top = build_dashboard_top(results, quality_cases)

    print("=== L780DB over_volume Summary ===")
    print(json.dumps(dashboard_summary, ensure_ascii=False, indent=2))
    print("\n=== Dashboard Top ===")
    print(json.dumps(dashboard_top, ensure_ascii=False, indent=2))

    write_json(OUTPUT_DIR / "abnormal_results.json", results)
    write_json(OUTPUT_DIR / "quality_cases.json", quality_cases)
    write_json(OUTPUT_DIR / "dashboard_summary.json", dashboard_summary)
    write_json(OUTPUT_DIR / "dashboard_top.json", dashboard_top)


if __name__ == "__main__":
    main()
