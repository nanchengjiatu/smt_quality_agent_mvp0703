import json
import subprocess
from pathlib import Path
from typing import Any

from smt_quality_agent.param_correlation import build_param_analysis


ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT / "output"


QUERY = """
select coalesce(json_agg(row_to_json(t)), '[]'::json)
from (
    select *
    from full_excel0608
) t;
"""


def load_full_excel_rows(database: str = "l780db") -> list[dict[str, Any]]:
    completed = subprocess.run(
        ["psql", "-X", "-d", database, "-t", "-A", "-c", QUERY],
        check=True,
        capture_output=True,
        text=True,
    )
    rows = json.loads(completed.stdout)
    # Mixed-case column names (BarCode, Comp_errName, ...) are lowered so the
    # analysis module sees the same keys regardless of export casing.
    return [{key.lower(): value for key, value in row.items()} for row in rows]


def main() -> None:
    analysis = build_param_analysis(load_full_excel_rows())

    overview = analysis["data_overview"]
    print("=== full_excel0608 数据概览 ===")
    print(json.dumps(overview, ensure_ascii=False, indent=2))
    print(f"\n=== 检出 {len(analysis['events'])} 个聚集事件 ===")
    for event in analysis["events"]:
        print(
            f"\n[{event['event_id']}] {event['main_defect_cn']} · {event['model']} · "
            f"{event['start_time']} ~ {event['end_time']} · "
            f"{event['board_count']} 块板 / {event['ng_record_count']} 条 NG · {event['scope']}"
        )
        for finding in event["findings"]:
            print(f"  - {finding}")

    OUTPUT_DIR.mkdir(exist_ok=True)
    output_path = OUTPUT_DIR / "param_analysis.json"
    with output_path.open("w", encoding="utf-8") as file:
        json.dump(analysis, file, ensure_ascii=False, indent=2)
        file.write("\n")
    print(f"\n已写入 {output_path}")


if __name__ == "__main__":
    main()
