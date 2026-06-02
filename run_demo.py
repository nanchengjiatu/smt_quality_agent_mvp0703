import csv
import json
from pathlib import Path

from smt_quality_agent.dashboard import build_dashboard_summary, build_dashboard_top
from smt_quality_agent.rules_engine import build_quality_cases, run_agent


ROOT = Path(__file__).resolve().parent
SAMPLE_FILE = ROOT / "data" / "sample_spi_data.csv"
OUTPUT_DIR = ROOT / "output"


def load_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


def main() -> None:
    rows = load_csv(SAMPLE_FILE)

    # In real SPI data this should come from the full board inspection count.
    total_pad_count_by_board = {
        "PCB001": 80,
        "PCB002": 80,
        "PCB003": 80,
        "PCB004": 80,
        "PCB005": 80,
    }

    results = run_agent(rows, total_pad_count_by_board)
    quality_cases = build_quality_cases(results)
    dashboard_summary = build_dashboard_summary(results, quality_cases)
    dashboard_top = build_dashboard_top(results, quality_cases)

    print("=== Abnormal Results ===")
    print(json.dumps(results, ensure_ascii=False, indent=2))
    print("\n=== Quality Cases ===")
    print(json.dumps(quality_cases, ensure_ascii=False, indent=2))
    print("\n=== Dashboard Summary ===")
    print(json.dumps(dashboard_summary, ensure_ascii=False, indent=2))
    print("\n=== Dashboard Top ===")
    print(json.dumps(dashboard_top, ensure_ascii=False, indent=2))

    OUTPUT_DIR.mkdir(exist_ok=True)
    write_json(OUTPUT_DIR / "abnormal_results.json", results)
    write_json(OUTPUT_DIR / "quality_cases.json", quality_cases)
    write_json(OUTPUT_DIR / "dashboard_summary.json", dashboard_summary)
    write_json(OUTPUT_DIR / "dashboard_top.json", dashboard_top)


def write_json(path: Path, payload: object) -> None:
    with path.open("w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)
        file.write("\n")


if __name__ == "__main__":
    main()
