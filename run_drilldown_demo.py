from smt_quality_agent.drilldown import build_drilldown_report
from smt_quality_agent.pipeline import OUTPUT_DIR, load_full_excel_rows, write_json


def main() -> None:
    report = build_drilldown_report(load_full_excel_rows())

    print(f"=== 触发规则：{report['trigger_rule']} ===")
    print(f"检出 {len(report['triggers'])} 个下钻触发点")
    for trigger in report["triggers"]:
        window = trigger["window"]
        print(
            f"\n[{trigger['trigger_id']}] 焊盘 {trigger['pad_name']} · {trigger['model']} · "
            f"{trigger['main_defect_cn']} · {trigger['start_time']} ~ {trigger['end_time']} · "
            f"连续 {trigger['trigger_board_count']} 块板 · "
            f"窗口 前{window['before_count']}/后{window['after_count']} 条"
        )
        for finding in trigger["findings"]:
            print(f"  - {finding['text']}")

    output_path = OUTPUT_DIR / "drilldown.json"
    write_json(output_path, report)
    print(f"\n已写入 {output_path}")


if __name__ == "__main__":
    main()
