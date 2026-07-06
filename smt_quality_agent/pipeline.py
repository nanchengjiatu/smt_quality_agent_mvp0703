"""Pipeline orchestration for the SMT quality agent MVP.

Runs the three analysis stages (anomaly_cases / param_analysis / drilldown) that
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
from smt_quality_agent.datasource import (
    load_datasource,
    qualified_table,
    quote_identifier,
    run_psql,
    source_table_label,
)
from smt_quality_agent.drilldown import build_drilldown_report
from smt_quality_agent.early_warning import build_early_warning_report
from smt_quality_agent.over_volume import normalize_spi_rows
from smt_quality_agent.param_correlation import build_param_analysis, first_inspection_rows
from smt_quality_agent.rules_engine import build_quality_cases, infer_total_pad_counts, run_agent


ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "output"

DEFAULT_DATABASE = "l780db"

# Files each stage owns, used by the server's /api/status to report freshness.
STAGE_FILES: dict[str, tuple[str, ...]] = {
    "anomaly_cases": (
        "abnormal_results.json",
        "quality_cases.json",
        "dashboard_summary.json",
        "dashboard_top.json",
    ),
    "param_analysis": ("param_analysis.json",),
    "drilldown": ("drilldown.json",),
    "early_warning": ("early_warning.json",),
}


def datasource_for(database: str | None = None) -> dict[str, Any]:
    config = load_datasource()
    if database:
        config = {**config, "database": database}
    return config


# fdate is stored as unpadded text ("2024/1/4 9:58"), so text ordering breaks
# across months ("2024/9/…" > "2024/11/…"). SQL-side ordering must parse it;
# the regex guard turns malformed values into NULL instead of failing the query.
FDATE_PATTERN = r"^\d{4}[/-]\d{1,2}[/-]\d{1,2} \d{1,2}:\d{2}"


def fdate_ts_expr(config: dict[str, Any]) -> str:
    field = quote_identifier(config["fields"]["time"])
    return (
        f"case when {field} ~ '{FDATE_PATTERN}' "
        f"then to_timestamp(substring({field} from '{FDATE_PATTERN}'), 'YYYY/MM/DD HH24:MI') end"
    )


def full_excel_query(config: dict[str, Any], window_boards: int = 0) -> str:
    table = qualified_table(config)
    if not window_boards:
        return f"""
select coalesce(json_agg(row_to_json(t)), '[]'::json)
from (
    select *
    from {table}
) t;
"""
    board_field = quote_identifier(config["fields"]["board"])
    return f"""
with board_times as (
    select {board_field} as board, max({fdate_ts_expr(config)}) as ts
    from {table}
    group by {board_field}
),
recent_boards as (
    select board from board_times order by ts desc nulls last limit {int(window_boards)}
)
select coalesce(json_agg(row_to_json(t)), '[]'::json)
from (
    select *
    from {table}
    where {board_field} in (select board from recent_boards)
) t;
"""


def fingerprint_query(config: dict[str, Any]) -> str:
    table = qualified_table(config)
    return f"select count(*) || '|' || coalesce(max({fdate_ts_expr(config)})::text, '') from {table};"


def cumulative_stats_query(config: dict[str, Any]) -> str:
    """Whole-table aggregates backing the dashboard's true-cumulative numbers.

    NG mirrors ``param_correlation.is_ng``: a non-empty defect name other than
    the literal PASS. Aggregate-only (no detail rows), so it stays cheap even
    when the table has grown far past the realtime window.
    """
    table = qualified_table(config)
    board = quote_identifier(config["fields"]["board"])
    defect = quote_identifier(config["fields"]["defect"])
    is_ng_sql = (
        f"nullif(trim({defect}), '') is not null "
        f"and upper(trim({defect})) <> 'PASS'"
    )
    return f"""
select json_build_object(
    'row_count', count(*),
    'board_count', count(distinct {board}),
    'ng_row_count', count(*) filter (where {is_ng_sql}),
    'ng_board_count', count(distinct {board}) filter (where {is_ng_sql}),
    'first_time', coalesce(min({fdate_ts_expr(config)})::text, ''),
    'latest_time', coalesce(max({fdate_ts_expr(config)})::text, '')
) from {table};
"""


def load_cumulative_stats(database: str | None = None) -> dict[str, Any]:
    config = datasource_for(database)
    completed = run_psql(config, cumulative_stats_query(config), timeout=10)
    return json.loads(completed.stdout)


def _psql_json(query: str, database: str | None = None) -> list[dict[str, Any]]:
    config = datasource_for(database)
    completed = run_psql(config, query)
    return json.loads(completed.stdout)


def source_fingerprint(database: str | None = None) -> str:
    """Return a cheap signature of the active full SPI table."""
    config = datasource_for(database)
    completed = run_psql(config, fingerprint_query(config), timeout=5)
    return completed.stdout.strip()


def load_full_excel_rows(database: str | None = None, window_boards: int = 0) -> list[dict[str, Any]]:
    config = datasource_for(database)
    rows = _psql_json(full_excel_query(config, window_boards), config["database"])
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


def run_pipeline(database: str | None = None, window_boards: int | None = None) -> dict[str, Any]:
    """Run all views from one snapshot of the most recent boards.

    A single read guarantees that realtime abnormalities, quality cases,
    event analysis, and drilldown cannot disagree because data arrived between
    separate stage queries. ``window_boards`` limits the snapshot to the most
    recent N boards (None reads the configured default, 0 loads the full
    table); every analysis lookback is bounded well below the default window,
    so windowed and full runs agree wherever they overlap.
    """
    if window_boards is None:
        window_boards = datasource_for(database)["realtime_window_boards"]
    try:
        full_rows = load_full_excel_rows(database, window_boards)
    except Exception as exc:  # noqa: BLE001
        error = f"{type(exc).__name__}: {exc}"
        if isinstance(exc, subprocess.CalledProcessError) and exc.stderr:
            error = f"psql: {exc.stderr.strip().splitlines()[-1]}"
        stages = [
            {"stage": name, "ok": False, "ms": 0, "error": error}
            for name in ("anomaly_cases", "param_analysis", "drilldown", "early_warning")
        ]
        return {
            "ok": False,
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "window_boards": window_boards,
            "stages": stages,
        }

    production_rows = first_inspection_rows(full_rows)
    normalized_rows = normalize_spi_rows(production_rows)
    source_table = source_table_label(datasource_for(database))
    board_field = load_datasource()["fields"]["board"].lower()
    loaded_boards = len({row.get(board_field) for row in full_rows})

    # Whole-table cumulative numbers ride along as a secondary reading; the
    # primary dashboard numbers stay window-scoped. Best-effort: a failure here
    # must not take down the analysis stages.
    try:
        cumulative = load_cumulative_stats(database)
    except Exception:  # noqa: BLE001
        cumulative = None

    def anomaly_work() -> dict[str, Any]:
        results = run_agent(normalized_rows, infer_total_pad_counts(normalized_rows))
        quality_cases = build_quality_cases(results)
        summary = build_dashboard_summary(results, quality_cases)
        summary["scope"] = {
            "window_boards": window_boards,
            "loaded_boards": loaded_boards,
            "source_table": source_table,
        }
        summary["cumulative"] = cumulative
        write_json(OUTPUT_DIR / "abnormal_results.json", results)
        write_json(OUTPUT_DIR / "quality_cases.json", quality_cases)
        write_json(OUTPUT_DIR / "dashboard_summary.json", summary)
        write_json(OUTPUT_DIR / "dashboard_top.json", build_dashboard_top(results, quality_cases))
        return {
            "rows": len(normalized_rows),
            "source_rows": len(full_rows),
            "files": list(STAGE_FILES["anomaly_cases"]),
        }

    def param_work() -> dict[str, Any]:
        write_json(OUTPUT_DIR / "param_analysis.json", build_param_analysis(full_rows, source_table))
        return {"rows": len(full_rows), "files": list(STAGE_FILES["param_analysis"])}

    def drilldown_work() -> dict[str, Any]:
        write_json(OUTPUT_DIR / "drilldown.json", build_drilldown_report(full_rows, source_table))
        return {"rows": len(full_rows), "files": list(STAGE_FILES["drilldown"])}

    def early_warning_work() -> dict[str, Any]:
        params = datasource_for(database)["early_warning"]
        write_json(OUTPUT_DIR / "early_warning.json", build_early_warning_report(
            full_rows, source_table, lam=params["lambda"], limit_l=params["L"],
        ))
        return {"rows": len(full_rows), "files": list(STAGE_FILES["early_warning"])}

    stages = [
        _stage("anomaly_cases", anomaly_work),
        _stage("param_analysis", param_work),
        _stage("drilldown", drilldown_work),
        _stage("early_warning", early_warning_work),
    ]
    return {
        "ok": all(stage["ok"] for stage in stages),
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "window_boards": window_boards,
        "loaded_boards": loaded_boards,
        "stages": stages,
    }
