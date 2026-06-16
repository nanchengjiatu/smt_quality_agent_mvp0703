"""Pipeline orchestration for the SMT quality agent MVP.

Runs the three analysis stages (over_volume / param_analysis / drilldown) that
each follow the same shape: load rows from a data source, build an analysis,
write the result JSON into ``output/`` for the static web frontend to fetch.

Each stage is isolated: if one data source is unreachable the others still
produce their files, and the per-stage status is returned so the frontend can
show an honest empty state for whatever failed.
"""

import json
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

from smt_quality_agent.dashboard import build_dashboard_summary, build_dashboard_top
from smt_quality_agent.drilldown import build_drilldown_report
from smt_quality_agent.over_volume import normalize_over_volume_rows
from smt_quality_agent.param_correlation import build_param_analysis
from smt_quality_agent.rules_engine import build_quality_cases, run_agent


ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "output"

DEFAULT_DATABASE = "l780db"

OVER_VOLUME_QUERY = """
select coalesce(json_agg(row_to_json(t)), '[]'::json)
from (
    select *
    from over_volume
    order by fdate, barcode, compname
) t;
"""

FULL_EXCEL_QUERY = """
select coalesce(json_agg(row_to_json(t)), '[]'::json)
from (
    select *
    from full_excel0608
) t;
"""

# Cheap change-detection fingerprint for the over_volume table: row count plus
# the latest fdate. Cheap enough to poll on a short interval; a change in
# either value means new/replaced data the pipeline should pick up.
OVER_VOLUME_FINGERPRINT_QUERY = (
    "select count(*) || '|' || coalesce(max(fdate)::text, '') from over_volume;"
)

# Files each stage owns, used by the server's /api/status to report freshness.
STAGE_FILES: dict[str, tuple[str, ...]] = {
    "over_volume": (
        "abnormal_results.json",
        "quality_cases.json",
        "dashboard_summary.json",
        "dashboard_top.json",
    ),
    "param_analysis": ("param_analysis.json",),
    "drilldown": ("drilldown.json",),
}


def _psql_json(query: str, database: str) -> list[dict[str, Any]]:
    completed = subprocess.run(
        ["psql", "-X", "-d", database, "-t", "-A", "-c", query],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def load_over_volume_rows(database: str = DEFAULT_DATABASE) -> list[dict[str, Any]]:
    return _psql_json(OVER_VOLUME_QUERY, database)


def over_volume_fingerprint(database: str = DEFAULT_DATABASE) -> str:
    """Return a cheap signature of over_volume; changes when data changes."""
    completed = subprocess.run(
        ["psql", "-X", "-d", database, "-t", "-A", "-c", OVER_VOLUME_FINGERPRINT_QUERY],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def load_full_excel_rows(database: str = DEFAULT_DATABASE) -> list[dict[str, Any]]:
    rows = _psql_json(FULL_EXCEL_QUERY, database)
    # Mixed-case column names (BarCode, Comp_errName, ...) are lowered so the
    # analysis modules see the same keys regardless of export casing.
    return [{key.lower(): value for key, value in row.items()} for row in rows]


def write_json(path: Path, payload: object) -> None:
    OUTPUT_DIR.mkdir(exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)
        file.write("\n")


def _stage(name: str, work: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    """Run one stage, capturing timing and any failure into a status dict."""
    start = time.perf_counter()
    try:
        info = work()
        ok = True
        error = None
    except Exception as exc:  # noqa: BLE001 - surface any source/build failure
        info = {}
        ok = False
        error = f"{type(exc).__name__}: {exc}"
        if isinstance(exc, subprocess.CalledProcessError) and exc.stderr:
            error = f"psql: {exc.stderr.strip().splitlines()[-1]}"
    status = {"stage": name, "ok": ok, "ms": round((time.perf_counter() - start) * 1000)}
    status.update(info)
    if error:
        status["error"] = error
    return status


def run_over_volume_stage(database: str = DEFAULT_DATABASE) -> dict[str, Any]:
    def work() -> dict[str, Any]:
        rows = normalize_over_volume_rows(load_over_volume_rows(database))
        results = run_agent(rows)
        quality_cases = build_quality_cases(results)
        write_json(OUTPUT_DIR / "abnormal_results.json", results)
        write_json(OUTPUT_DIR / "quality_cases.json", quality_cases)
        write_json(OUTPUT_DIR / "dashboard_summary.json", build_dashboard_summary(results, quality_cases))
        write_json(OUTPUT_DIR / "dashboard_top.json", build_dashboard_top(results, quality_cases))
        return {"rows": len(rows), "files": list(STAGE_FILES["over_volume"])}

    return _stage("over_volume", work)


def run_full_excel_stages(database: str = DEFAULT_DATABASE) -> list[dict[str, Any]]:
    """Load the big full_excel table once, feed both param + drilldown stages.

    If the shared load fails, both downstream stages are reported as failed with
    the same error rather than querying the table twice.
    """
    try:
        rows = load_full_excel_rows(database)
    except Exception as exc:  # noqa: BLE001
        error = f"{type(exc).__name__}: {exc}"
        if isinstance(exc, subprocess.CalledProcessError) and exc.stderr:
            error = f"psql: {exc.stderr.strip().splitlines()[-1]}"
        return [
            {"stage": "param_analysis", "ok": False, "ms": 0, "error": error},
            {"stage": "drilldown", "ok": False, "ms": 0, "error": error},
        ]

    def param_work() -> dict[str, Any]:
        write_json(OUTPUT_DIR / "param_analysis.json", build_param_analysis(rows))
        return {"rows": len(rows), "files": list(STAGE_FILES["param_analysis"])}

    def drilldown_work() -> dict[str, Any]:
        write_json(OUTPUT_DIR / "drilldown.json", build_drilldown_report(rows))
        return {"rows": len(rows), "files": list(STAGE_FILES["drilldown"])}

    return [_stage("param_analysis", param_work), _stage("drilldown", drilldown_work)]


def run_pipeline(database: str = DEFAULT_DATABASE) -> dict[str, Any]:
    """Run all stages, returning a structured per-stage status report."""
    stages = [run_over_volume_stage(database)]
    stages.extend(run_full_excel_stages(database))
    return {
        "ok": all(stage["ok"] for stage in stages),
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "stages": stages,
    }
