import json
import subprocess
from pathlib import Path
from typing import Any

from smt_quality_agent.affected_model import normalize_affected_model_rows
from smt_quality_agent.dashboard import build_dashboard_summary, build_dashboard_top
from smt_quality_agent.rules_engine import build_quality_cases, run_agent


ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT / "output"


QUERY = """
select coalesce(json_agg(row_to_json(t)), '[]'::json)
from (
    select
        fdate,
        machinename,
        cmodel,
        barcode,
        compname,
        comp_errname,
        comp_avdp,
        comp_aadp,
        comp_ahdp,
        comp_px,
        comp_py,
        printspeed_plan,
        printspeed,
        diff_printspeed,
        frontsqgpress_plan,
        frontsqgpress,
        diff_frontsqgpress,
        rearsqgpress_plan,
        rearsqgpress,
        diff_rearsqgpress,
        snapoffspeed_plan,
        snapoffspeed,
        diff_snapoffspeed,
        cleaningfrequency_plan,
        cleaningfrequency,
        diff_cleaningfrequency
    from affected_model_0601
    where comp_errname in (
        'Under Volume',
        'Under Area',
        'Under Height',
        'Over Volume',
        'Over Height',
        'AREAOVER'
    )
    order by fdate, barcode, compname
) t;
"""


def load_affected_model_rows(database: str = "l780db") -> list[dict[str, Any]]:
    completed = subprocess.run(
        ["psql", "-X", "-d", database, "-t", "-A", "-c", QUERY],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def main() -> None:
    rows = normalize_affected_model_rows(load_affected_model_rows())
    results = run_agent(rows)
    quality_cases = build_quality_cases(results)
    dashboard_summary = build_dashboard_summary(results, quality_cases)
    dashboard_top = build_dashboard_top(results, quality_cases)

    print("=== L780DB affected_model_0601 Summary ===")
    print(json.dumps(dashboard_summary, ensure_ascii=False, indent=2))
    print("\n=== Dashboard Top ===")
    print(json.dumps(dashboard_top, ensure_ascii=False, indent=2))

    OUTPUT_DIR.mkdir(exist_ok=True)
    write_json(OUTPUT_DIR / "l780db_abnormal_results.json", results)
    write_json(OUTPUT_DIR / "l780db_quality_cases.json", quality_cases)
    write_json(OUTPUT_DIR / "l780db_dashboard_summary.json", dashboard_summary)
    write_json(OUTPUT_DIR / "l780db_dashboard_top.json", dashboard_top)


def write_json(path: Path, payload: object) -> None:
    with path.open("w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)
        file.write("\n")


if __name__ == "__main__":
    main()
