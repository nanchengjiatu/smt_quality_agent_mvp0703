from __future__ import annotations

from datetime import datetime
from typing import Any

from smt_quality_agent.knowledge_base import (
    EVENT_SCOPE_BOARD,
    EVENT_SCOPE_LOCAL,
    event_cause_candidates,
)


# Boards whose NG records are closer than this gap belong to the same event.
EVENT_GAP_MINUTES = 30

# How many normal boards before an event are inspected for precursor drift.
PRECURSOR_LOOKBACK_BOARDS = 20

# Precursor verdict thresholds: deviation climbing at least this fast
# (percent per board) and rising over at least this many consecutive boards.
PRECURSOR_SLOPE_THRESHOLD = 0.5
PRECURSOR_RISE_THRESHOLD = 3

# An event board with at least this share of NG pads is a board-wide failure.
BOARD_WIDE_NG_SHARE = 0.5

# A board-wide claim additionally needs enough recorded points on that board:
# a board with only a handful of rows reaches a 50% NG share trivially, which
# would misread a single bad pad as a board-wide failure.
BOARD_WIDE_MIN_BOARD_ROWS = 10

METRIC_FIELDS = ("comp_avdp", "comp_aadp", "comp_ahdp")

METRIC_LABELS = {
    "comp_avdp": "体积偏差",
    "comp_aadp": "面积偏差",
    "comp_ahdp": "高度偏差",
}

DEFECT_MAIN_METRIC = {
    "over volume": "comp_avdp",
    "under volume": "comp_avdp",
    "over area": "comp_aadp",
    "under area": "comp_aadp",
    "over height": "comp_ahdp",
    "under height": "comp_ahdp",
}

DEFECT_NAME_CN = {
    "over volume": "多锡",
    "under volume": "少锡",
    "over area": "多锡(面积)",
    "under area": "少锡(面积)",
    "over height": "多锡(高度)",
    "under height": "少锡(高度不足)",
}

DEFECT_ALIASES = {
    "areaover": "over area",
    "volumeover": "over volume",
    "heightover": "over height",
    "areaunder": "under area",
    "volumeunder": "under volume",
    "heightunder": "under height",
}


def normalize_defect_key(value: str) -> str:
    key = " ".join(str(value or "").strip().lower().replace("_", " ").replace("-", " ").split())
    compact = key.replace(" ", "")
    return DEFECT_ALIASES.get(compact, key)

def parse_fdate(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def as_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def is_ng(row: dict[str, Any]) -> bool:
    err = str(row.get("comp_errname") or "").strip()
    return bool(err) and err.upper() != "PASS"


def defect_direction(value: str) -> str:
    """Collapse machine defect names into root-cause-compatible directions."""
    key = normalize_defect_key(value)
    if key.startswith("over"):
        return "多锡"
    if key.startswith("under"):
        return "少锡"
    return f"其他:{key or '未知'}"


def aggregate_boards(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group rows into inspections keyed by (board, inspect time).

    The same barcode can appear again minutes later — that is a re-inspection
    after rework, not new production, and is marked `is_recheck`.
    """
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        key = (str(row.get("barcode") or ""), str(row.get("fdate") or ""))
        grouped.setdefault(key, []).append(row)

    boards = []
    for (board_sn, time_text), board_rows in grouped.items():
        ng_rows = [row for row in board_rows if is_ng(row)]
        ng_types: dict[str, int] = {}
        ng_types_by_direction: dict[str, dict[str, int]] = {}
        ng_components_by_direction: dict[str, set[str]] = {}
        for row in ng_rows:
            err = str(row.get("comp_errname") or "").strip()
            ng_types[err] = ng_types.get(err, 0) + 1
            direction = defect_direction(err)
            direction_types = ng_types_by_direction.setdefault(direction, {})
            direction_types[err] = direction_types.get(err, 0) + 1
            ng_components_by_direction.setdefault(direction, set()).add(
                str(row.get("compname") or "")
            )

        boards.append({
            "board_sn": board_sn,
            "model": str(board_rows[0].get("cmodel") or ""),
            "machine": str(board_rows[0].get("machinename") or ""),
            "time": parse_fdate(time_text),
            "time_text": time_text,
            "is_recheck": False,
            "row_count": len(board_rows),
            "ng_count": len(ng_rows),
            "ng_share": len(ng_rows) / len(board_rows) if board_rows else 0.0,
            "ng_types": ng_types,
            "ng_components": sorted({str(row.get("compname") or "") for row in ng_rows}),
            "ng_types_by_direction": ng_types_by_direction,
            "ng_count_by_direction": {
                direction: sum(types.values())
                for direction, types in ng_types_by_direction.items()
            },
            "ng_components_by_direction": {
                direction: sorted(components)
                for direction, components in ng_components_by_direction.items()
            },
            "metric_avgs": {field: avg_metric(board_rows, field) for field in METRIC_FIELDS},
            "pass_metric_avgs": {
                field: avg_metric([row for row in board_rows if not is_ng(row)], field)
                for field in METRIC_FIELDS
            },
        })

    boards.sort(key=lambda board: (board["time"] or datetime.min, board["board_sn"]))

    seen_board_sns: set[str] = set()
    for board in boards:
        board["is_recheck"] = board["board_sn"] in seen_board_sns
        seen_board_sns.add(board["board_sn"])

    return boards


def first_inspection_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return rows from each board's first inspection, excluding rechecks."""
    first_keys = {
        (board["board_sn"], board["time_text"])
        for board in aggregate_boards(rows)
        if not board["is_recheck"]
    }
    return [
        row for row in rows
        if (str(row.get("barcode") or ""), str(row.get("fdate") or "")) in first_keys
    ]


def avg_metric(rows: list[dict[str, Any]], field: str) -> float | None:
    values = [value for value in (as_float(row.get(field)) for row in rows) if value is not None]
    if not values:
        return None
    return sum(values) / len(values)


def detect_events(
    boards: list[dict[str, Any]],
    gap_minutes: int = EVENT_GAP_MINUTES,
) -> list[list[dict[str, Any]]]:
    """Cluster first-inspection NGs by model, machine, and defect direction."""
    ng_boards = [
        board for board in boards
        if board["ng_count"] > 0 and board["time"] and not board["is_recheck"]
    ]
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for board in ng_boards:
        for direction, types in board["ng_types_by_direction"].items():
            direction_count = sum(types.values())
            projected = {
                **board,
                "event_direction": direction,
                "ng_count": direction_count,
                "ng_share": direction_count / board["row_count"] if board["row_count"] else 0.0,
                "ng_types": types,
                "ng_components": board["ng_components_by_direction"].get(direction, []),
            }
            key = (board["model"], board["machine"], direction)
            grouped.setdefault(key, []).append(projected)

    clusters: list[list[dict[str, Any]]] = []
    for group_boards in grouped.values():
        current: list[dict[str, Any]] = []
        for board in group_boards:
            if current and (board["time"] - current[-1]["time"]).total_seconds() > gap_minutes * 60:
                clusters.append(current)
                current = []
            current.append(board)
        if current:
            clusters.append(current)

    clusters.sort(key=lambda cluster: cluster[0]["time"])
    return clusters


def main_defect_type(cluster: list[dict[str, Any]]) -> str:
    counts: dict[str, int] = {}
    for board in cluster:
        for err, count in board["ng_types"].items():
            counts[err] = counts.get(err, 0) + count
    return max(counts, key=lambda err: counts[err]) if counts else ""


def analyze_precursor(
    cluster: list[dict[str, Any]],
    boards: list[dict[str, Any]],
    metric_field: str,
) -> dict[str, Any]:
    model = cluster[0]["model"]
    event_start = cluster[0]["time"]
    label = METRIC_LABELS.get(metric_field, metric_field)

    baseline = [
        board for board in boards
        if board["model"] == model
        and board["ng_count"] == 0
        and not board["is_recheck"]
        and board["time"] is not None
        and board["time"] < event_start
        and board["metric_avgs"].get(metric_field) is not None
    ]
    baseline = baseline[-PRECURSOR_LOOKBACK_BOARDS:]

    if len(baseline) < PRECURSOR_RISE_THRESHOLD:
        return {
            "available": False,
            "metric_label": label,
            "baseline_board_count": len(baseline),
            "verdict": "无事件前数据",
            "detail": f"事件前无足够的同机种正常生产数据可回溯（仅 {len(baseline)} 块板），无法判断是否存在前兆。",
            "series": [],
        }

    values = [board["metric_avgs"][metric_field] for board in baseline]
    slope = linear_slope(values)
    rise = tail_consecutive_rise(values)
    baseline_avg = sum(values) / len(values)
    has_precursor = slope >= PRECURSOR_SLOPE_THRESHOLD and rise >= PRECURSOR_RISE_THRESHOLD

    if has_precursor:
        verdict = "有前兆（渐变型）"
        detail = (
            f"事件前 {len(values)} 块正常板的平均{label}以约 {slope:.2f}%/板 的速度爬升，"
            f"且末段连续 {rise} 块板单调上升——存在可预警的漂移前兆。"
        )
    else:
        verdict = "无明显前兆（突发型）"
        detail = (
            f"事件前 {len(values)} 块正常板的平均{label}稳定在 {baseline_avg:.1f}% 左右"
            f"（趋势斜率 {slope:.2f}%/板），未见爬升——该事件属突发型，事前控制图难以拦截。"
        )

    return {
        "available": True,
        "metric_label": label,
        "baseline_board_count": len(values),
        "baseline_avg": round(baseline_avg, 2),
        "trend_slope_per_board": round(slope, 3),
        "tail_consecutive_rise": rise,
        "has_precursor": has_precursor,
        "verdict": verdict,
        "detail": detail,
        "series": [
            {
                "board_sn": board["board_sn"],
                "time": board["time_text"],
                "value": round(board["metric_avgs"][metric_field], 2),
            }
            for board in baseline
        ],
    }


def linear_slope(values: list[float]) -> float:
    n = len(values)
    if n < 2:
        return 0.0
    mean_x = (n - 1) / 2
    mean_y = sum(values) / n
    denominator = sum((i - mean_x) ** 2 for i in range(n))
    if denominator == 0:
        return 0.0
    numerator = sum((i - mean_x) * (value - mean_y) for i, value in enumerate(values))
    return numerator / denominator


def tail_consecutive_rise(values: list[float]) -> int:
    rise = 0
    for previous, current in zip(reversed(values[:-1]), reversed(values[1:])):
        if current > previous:
            rise += 1
        else:
            break
    return rise


def check_parameters(
    rows: list[dict[str, Any]],
    event_board_sns: set[str],
    model: str,
) -> dict[str, Any]:
    """Compare printing-parameter deviations inside the event with the rest
    of production. A parameter is flagged only when the event saw deviations
    beyond anything normal production produced."""
    param_fields = sorted({
        key for row in rows[:1] for key in row if key.startswith("abs_")
    })
    event_rows = [row for row in rows if str(row.get("barcode") or "") in event_board_sns]
    baseline_rows = [
        row for row in rows
        if str(row.get("barcode") or "") not in event_board_sns
        and str(row.get("cmodel") or "") == model
    ]
    cross_model_baseline = not baseline_rows
    if cross_model_baseline:
        baseline_rows = [
            row for row in rows if str(row.get("barcode") or "") not in event_board_sns
        ]

    drifted = []
    for field in param_fields:
        event_max = max_metric(event_rows, field)
        baseline_max = max_metric(baseline_rows, field)
        if event_max is None:
            continue
        if baseline_max is None or event_max > baseline_max:
            drifted.append({
                "parameter": field.removeprefix("abs_"),
                "event_max_abs_diff": round(event_max, 4),
                "baseline_max_abs_diff": round(baseline_max, 4) if baseline_max is not None else None,
            })

    environment_recorded = any(
        (as_float(row.get("temperature")) or 0) > 0 or (as_float(row.get("humidity")) or 0) > 0
        for row in rows
    )

    if drifted:
        names = "、".join(item["parameter"] for item in drifted)
        verdict = f"事件期间 {names} 的偏差超出正常生产水平，需优先排查这些参数。"
    else:
        verdict = (
            f"事件期间 {len(param_fields)} 项印刷参数的实际-计划偏差均未超出正常生产水平，"
            "可排除设备参数漂移因素。"
        )
    if cross_model_baseline:
        verdict += "（注意：该机种无事件外正常生产数据，此处为跨机种对比，定位/对位类参数差异可能源自机种本身，仅供参考。）"

    return {
        "checked_count": len(param_fields),
        "drifted": drifted,
        "cross_model_baseline": cross_model_baseline,
        "environment_recorded": environment_recorded,
        "verdict": verdict,
    }


def max_metric(rows: list[dict[str, Any]], field: str) -> float | None:
    values = [value for value in (as_float(row.get(field)) for row in rows) if value is not None]
    return max(values) if values else None


def build_event(
    event_no: int,
    cluster: list[dict[str, Any]],
    boards: list[dict[str, Any]],
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    main_defect = main_defect_type(cluster)
    main_defect_key = normalize_defect_key(main_defect)
    metric_field = DEFECT_MAIN_METRIC.get(main_defect_key, "comp_avdp")
    metric_label = METRIC_LABELS[metric_field]
    defect_cn = DEFECT_NAME_CN.get(main_defect_key, main_defect)
    direction = cluster[0]["event_direction"]

    max_ng_share = max(board["ng_share"] for board in cluster)
    board_wide = any(
        board["ng_share"] >= BOARD_WIDE_NG_SHARE
        and board["row_count"] >= BOARD_WIDE_MIN_BOARD_ROWS
        for board in cluster
    )
    scope = EVENT_SCOPE_BOARD if board_wide else EVENT_SCOPE_LOCAL

    event_board_sns = {board["board_sn"] for board in cluster}
    precursor = analyze_precursor(cluster, boards, metric_field)
    parameter_check = check_parameters(rows, event_board_sns, cluster[0]["model"])
    recheck = analyze_recheck(cluster, boards)

    defect_types: dict[str, int] = {}
    components: set[str] = set()
    for board in cluster:
        components.update(board["ng_components"])
        for err, count in board["ng_types"].items():
            defect_types[err] = defect_types.get(err, 0) + count

    duration_minutes = round(
        (cluster[-1]["time"] - cluster[0]["time"]).total_seconds() / 60
    )
    findings = build_findings(
        cluster, boards, metric_field, metric_label, defect_cn,
        scope, duration_minutes, precursor, parameter_check, recheck,
    )
    cause_candidates = event_cause_candidates(direction, scope)

    return {
        "event_id": f"EVT{event_no:03d}",
        "model": cluster[0]["model"],
        "machine": cluster[0]["machine"],
        "defect_direction": direction,
        "start_time": cluster[0]["time_text"],
        "end_time": cluster[-1]["time_text"],
        "duration_minutes": duration_minutes,
        "board_count": len(cluster),
        "ng_record_count": sum(board["ng_count"] for board in cluster),
        "defect_types": defect_types,
        "main_defect_type": main_defect,
        "main_defect_cn": defect_cn,
        "scope": scope,
        "max_ng_share": round(max_ng_share, 4),
        "affected_components": sorted(components),
        "metric_field": metric_field,
        "metric_label": metric_label,
        "boards": [
            {
                "board_sn": board["board_sn"],
                "time": board["time_text"],
                "ng_count": board["ng_count"],
                "ng_share": round(board["ng_share"], 4),
                "metric_avg": round_or_none(board["metric_avgs"].get(metric_field)),
            }
            for board in cluster
        ],
        "precursor": precursor,
        "parameter_check": parameter_check,
        "recheck": recheck,
        "findings": findings,
        "cause_candidates": cause_candidates,
        "suggested_causes": [item["cause"] for item in cause_candidates],
        "suggested_actions": [item["action"] for item in cause_candidates],
    }


def analyze_recheck(
    cluster: list[dict[str, Any]],
    boards: list[dict[str, Any]],
) -> dict[str, Any]:
    """Look for later inspections of event boards and report the outcome."""
    outcomes = []
    for event_board in cluster:
        rechecks = [
            board for board in boards
            if board["board_sn"] == event_board["board_sn"]
            and board["is_recheck"]
            and board["time"] is not None
            and event_board["time"] is not None
            and board["time"] > event_board["time"]
        ]
        if not rechecks:
            continue
        latest = rechecks[-1]
        direction = event_board.get("event_direction")
        recheck_ng_count = latest.get("ng_count_by_direction", {}).get(
            direction, latest["ng_count"]
        )
        outcomes.append({
            "board_sn": event_board["board_sn"],
            "recheck_time": latest["time_text"],
            "recheck_ng_count": recheck_ng_count,
            "passed": recheck_ng_count == 0,
        })

    return {
        "rechecked_board_count": len(outcomes),
        "passed_board_count": sum(1 for item in outcomes if item["passed"]),
        "outcomes": outcomes,
    }


def build_findings(
    cluster: list[dict[str, Any]],
    boards: list[dict[str, Any]],
    metric_field: str,
    metric_label: str,
    defect_cn: str,
    scope: str,
    duration_minutes: int,
    precursor: dict[str, Any],
    parameter_check: dict[str, Any],
    recheck: dict[str, Any],
) -> list[str]:
    model = cluster[0]["model"]
    findings = [
        f"{defect_cn}集中发生在 {cluster[0]['time_text']} 至 {cluster[-1]['time_text']} "
        f"的 {duration_minutes} 分钟内，连续 {len(cluster)} 块 {model} 板，属时间聚集型事件，"
        "而非随机散发。"
    ]

    if scope == EVENT_SCOPE_BOARD:
        qualified = [
            board for board in cluster
            if board["row_count"] >= BOARD_WIDE_MIN_BOARD_ROWS
        ]
        worst = max(qualified or cluster, key=lambda board: board["ng_share"])
        findings.append(
            f"板 {worst['board_sn']} 上 {worst['ng_count']}/{worst['row_count']} 个检测点同时异常"
            f"（占比 {worst['ng_share'] * 100:.0f}%），为整板性失效而非个别焊盘问题。"
        )

    pass_avg = avg_of([board["pass_metric_avgs"].get(metric_field) for board in cluster])
    reference_avg = avg_of([
        board["pass_metric_avgs"].get(metric_field)
        for board in boards
        if board["board_sn"] not in {item["board_sn"] for item in cluster} and board["ng_count"] == 0
    ])
    if pass_avg is not None and reference_avg is not None and reference_avg > 0 and pass_avg >= 2 * reference_avg:
        findings.append(
            f"事件板上未判 NG 的焊盘平均{metric_label}也达 {pass_avg:.1f}%"
            f"（正常生产水平约 {reference_avg:.1f}%），整板锡量整体偏移，问题不局限于报警焊盘。"
        )

    findings.append(precursor["detail"])
    findings.append(parameter_check["verdict"])

    for outcome in recheck["outcomes"]:
        if outcome["passed"]:
            findings.append(
                f"板 {outcome['board_sn']} 于 {outcome['recheck_time']} 复测，异常点全部通过——"
                "返工/重印处置有效，事件未扩散。"
            )
        else:
            findings.append(
                f"板 {outcome['board_sn']} 于 {outcome['recheck_time']} 复测仍有 "
                f"{outcome['recheck_ng_count']} 个异常点，处置未见效，需升级排查。"
            )
    if not parameter_check["environment_recorded"]:
        findings.append("温湿度字段全程无有效记录，环境因素暂无法用数据排除，建议接入车间温湿度采集。")

    return findings


def avg_of(values: list[float | None]) -> float | None:
    present = [value for value in values if value is not None]
    if not present:
        return None
    return sum(present) / len(present)


def round_or_none(value: float | None) -> float | None:
    return round(value, 2) if value is not None else None


def build_data_overview(rows: list[dict[str, Any]], boards: list[dict[str, Any]]) -> dict[str, Any]:
    first_inspections = [board for board in boards if not board["is_recheck"]]
    rechecks = [board for board in boards if board["is_recheck"]]
    production_record_count = sum(board["row_count"] for board in first_inspections)
    ng_count = sum(board["ng_count"] for board in first_inspections)
    inspection_ng_count = sum(board["ng_count"] for board in boards)
    recheck_ng_count = sum(board["ng_count"] for board in rechecks)
    ng_board_count = sum(1 for board in first_inspections if board["ng_count"] > 0)
    recheck_pass_count = sum(1 for board in rechecks if board["ng_count"] == 0)
    timed_boards = [board for board in boards if board["time"]]

    return {
        "record_count": len(rows),
        "production_record_count": production_record_count,
        "board_count": len(first_inspections),
        "inspection_count": len(boards),
        "model_count": len({board["model"] for board in boards}),
        "machine_count": len({board["machine"] for board in boards}),
        "pass_count": production_record_count - ng_count,
        "ng_count": ng_count,
        "inspection_ng_count": inspection_ng_count,
        "recheck_ng_count": recheck_ng_count,
        "defect_rate_percent": round(ng_count / production_record_count * 100, 2)
        if production_record_count else None,
        "ng_board_count": ng_board_count,
        "board_pass_rate_percent": round(
            (1 - ng_board_count / len(first_inspections)) * 100, 2
        ) if first_inspections else None,
        "recheck_count": len(rechecks),
        "recheck_pass_count": recheck_pass_count,
        "recheck_effective_rate": round(recheck_pass_count / len(rechecks), 4) if rechecks else None,
        "time_range": [
            timed_boards[0]["time_text"] if timed_boards else None,
            timed_boards[-1]["time_text"] if timed_boards else None,
        ],
    }


def build_param_analysis(
    rows: list[dict[str, Any]],
    source_table: str = "l780db.public.full_excel0623",
) -> dict[str, Any]:
    boards = aggregate_boards(rows)
    clusters = detect_events(boards)
    events = [
        build_event(event_no, cluster, boards, rows)
        for event_no, cluster in enumerate(clusters, start=1)
    ]

    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source_table": source_table,
        "data_overview": build_data_overview(rows, boards),
        "events": events,
        "caveats": [
            "当前样本量有限（单机台、少量机种），结论应视为排查线索而非统计结论。",
            "事件聚类窗口为同机种 NG 板间隔不超过 30 分钟，可按产线节拍调整。",
        ],
    }
