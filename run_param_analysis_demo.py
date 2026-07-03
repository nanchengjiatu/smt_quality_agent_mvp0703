import json

from smt_quality_agent.param_correlation import build_param_analysis
from smt_quality_agent.pipeline import OUTPUT_DIR, load_full_excel_rows, write_json


def main() -> None:
    analysis = build_param_analysis(load_full_excel_rows())

    overview = analysis["data_overview"]
    print("=== full_excel0623 数据概览 ===")
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

    output_path = OUTPUT_DIR / "param_analysis.json"
    write_json(output_path, analysis)
    print(f"\n已写入 {output_path}")


if __name__ == "__main__":
    main()
