from __future__ import annotations

from datetime import datetime
from typing import Any

from smt_quality_agent.affected_model import split_component_pad
from smt_quality_agent.knowledge_base import (
    FALLBACK_ROOT_CAUSE,
    PARAMETER_DRIFT_ROOT_CAUSE,
    PARAMETER_RECOVERY_ROOT_CAUSE,
    PERIODIC_ROOT_CAUSE,
    RECHECK_CRITERIA,
    SPI_FALSE_ALARM_ROOT_CAUSE,
    disposition_for,
    event_cause_candidates,
    event_scope_for_category,
    root_cause_candidate_from_rule,
    scope_root_cause_candidate,
    trend_root_cause_candidate,
)
from smt_quality_agent.ontology import ontology_ids_for
from smt_quality_agent.param_correlation import (
    BOARD_WIDE_MIN_BOARD_ROWS,
    BOARD_WIDE_NG_SHARE,
    DEFECT_MAIN_METRIC,
    DEFECT_NAME_CN,
    METRIC_FIELDS,
    METRIC_LABELS,
    PRECURSOR_RISE_THRESHOLD,
    PRECURSOR_SLOPE_THRESHOLD,
    as_float,
    check_parameters,
    is_ng,
    linear_slope,
    normalize_defect_key,
    parse_fdate,
    tail_consecutive_rise,
)


# A pad failing on at least this many consecutive production boards triggers
# a drill-down package.
TRIGGER_RUN_BOARDS = 3

# How many of the pad's records before and after the trigger run to include in
# the trend chart. Kept separate from the full SPI context window below.
PAD_SERIES_WINDOW = 300

# How many full SPI detail rows before and after the trigger run to include as
# context evidence. This is intentionally not limited to the trigger pad.
FULL_SPI_CONTEXT_WINDOW = 500

# Backward-compatible name used by existing tests and frontend wording.
WINDOW_RECORDS = PAD_SERIES_WINDOW

# Pre-trigger PASS records needed before baseline statistics are trusted.
BASELINE_MIN_RECORDS = 5

# Trigger mean at or above this multiple of the baseline mean reads as a jump.
STEP_JUMP_RATIO = 2.0

# Control-band width in standard deviations around the baseline mean.
SIGMA_BAND = 3.0

# Periodicity: at least this many NG runs whose start-gap coefficient of
# variation stays below the threshold.
PERIODIC_MIN_RUNS = 3
PERIODIC_CV_THRESHOLD = 0.25

# Printing-program setpoint fields: a change between consecutive boards is a
# deliberate adjustment worth marking on the chart.
PLAN_FIELD_SUFFIX = "_plan"
PLAN_EXTRA_FIELDS = ("printmode",)

# Some "_Plan" columns (MarkDeviation_Plan, TableUpX_Plan, ...) move on nearly
# every board — they are measurements in disguise, not setpoints. A field is
# treated as a real setpoint only when it changes on at most this share of
# board-to-board transitions.
SETPOINT_MAX_CHANGE_SHARE = 0.34

# Local-area scope: several NG pads clustered inside a small share of the
# board-coordinate range should be treated differently from a single pad or a
# board-wide trend.
LOCAL_AREA_MIN_NG_ROWS = 3
LOCAL_AREA_MAX_SPAN_SHARE = 0.35

# Strong SPI false-alarm signal: the row is labelled NG, but the defect's main
# metric does not show a meaningful deviation.
SPI_FALSE_ALARM_METRIC_THRESHOLD = 20.0


def build_pad_points(rows: list[dict[str, Any]]) -> dict[str, list[list[dict[str, Any]]]]:
    """Per (model, pad), the pad's records over time-ordered inspections.

    Returns model -> pad_name -> ordered point list. A point keeps the raw row
    so printing parameters stay reachable without a second lookup.
    """
    inspections: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for row in rows:
        key = (
            str(row.get("cmodel") or ""),
            str(row.get("barcode") or ""),
            str(row.get("fdate") or ""),
        )
        inspections.setdefault(key, []).append(row)

    ordered = sorted(
        inspections.items(),
        key=lambda item: (parse_fdate(item[0][2]) or datetime.min, item[0][1]),
    )

    seen_barcodes: set[tuple[str, str]] = set()
    by_model: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for (model, barcode, time_text), board_rows in ordered:
        is_recheck = (model, barcode) in seen_barcodes
        seen_barcodes.add((model, barcode))
        board_ng = sum(1 for row in board_rows if is_ng(row))
        for row in board_rows:
            pad_name = str(row.get("compname") or "")
            point = {
                "board_sn": barcode,
                "time": time_text,
                "is_recheck": is_recheck,
                "is_ng": is_ng(row),
                "err": str(row.get("comp_errname") or "").strip(),
                "board_row_count": len(board_rows),
                "board_ng_count": board_ng,
                "values": {field: as_float(row.get(field)) for field in METRIC_FIELDS},
                "row": row,
            }
            by_model.setdefault(model, {}).setdefault(pad_name, []).append(point)

    return by_model


def detect_trigger_runs(points: list[dict[str, Any]]) -> list[tuple[int, int]]:
    """Index ranges [start, end] of runs where the pad fails on at least
    TRIGGER_RUN_BOARDS consecutive production boards. Rechecks neither extend
    nor break a run — they are re-inspections, not new production."""
    runs: list[tuple[int, int]] = []
    run_indexes: list[int] = []
    for index, point in enumerate(points):
        if point["is_recheck"]:
            continue
        if point["is_ng"]:
            run_indexes.append(index)
        else:
            if len(run_indexes) >= TRIGGER_RUN_BOARDS:
                runs.append((run_indexes[0], run_indexes[-1]))
            run_indexes = []
    if len(run_indexes) >= TRIGGER_RUN_BOARDS:
        runs.append((run_indexes[0], run_indexes[-1]))
    return runs


def ng_run_starts(points: list[dict[str, Any]]) -> list[int]:
    """Production-board indexes (recheck-free numbering) where NG runs begin."""
    starts: list[int] = []
    previous_ng = False
    board_no = 0
    for point in points:
        if point["is_recheck"]:
            continue
        if point["is_ng"] and not previous_ng:
            starts.append(board_no)
        previous_ng = point["is_ng"]
        board_no += 1
    return starts


def main_defect_of_run(points: list[dict[str, Any]], start: int, end: int) -> str:
    counts: dict[str, int] = {}
    for point in points[start:end + 1]:
        if point["is_ng"]:
            counts[point["err"]] = counts.get(point["err"], 0) + 1
    return max(counts, key=lambda err: counts[err]) if counts else ""


def baseline_stats(values: list[float]) -> dict[str, Any]:
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    sigma = variance ** 0.5
    return {
        "available": True,
        "count": len(values),
        "mean": round(mean, 2),
        "sigma": round(sigma, 2),
        "upper_band": round(mean + SIGMA_BAND * sigma, 2),
        "lower_band": round(max(mean - SIGMA_BAND * sigma, 0.0), 2),
    }


def analyze_change_type(
    pre_values: list[float],
    trigger_values: list[float],
    baseline: dict[str, Any],
    metric_label: str,
    run_length: int,
) -> dict[str, Any]:
    if not baseline["available"]:
        return {
            "kind": "unknown",
            "verdict": "无法判别（事件前数据不足）",
            "detail": (
                f"事件前该焊盘仅有 {len(pre_values)} 条正常记录"
                f"（判别至少需要 {BASELINE_MIN_RECORDS} 条），无法区分突变还是渐变。"
            ),
            "highlight": None,
        }

    tail = pre_values[-20:]
    slope = linear_slope(tail)
    rise = tail_consecutive_rise(tail)
    if slope >= PRECURSOR_SLOPE_THRESHOLD and rise >= PRECURSOR_RISE_THRESHOLD:
        return {
            "kind": "gradual",
            "verdict": "渐变型（事前有爬升）",
            "detail": (
                f"触发前 {len(tail)} 条记录的{metric_label}以约 {slope:.2f}%/板 的速度爬升，"
                f"末段连续 {rise} 块板单调上升——属可预警的渐变失效，建议为该焊盘配置趋势报警。"
            ),
            "trend_slope_per_board": round(slope, 3),
            "tail_consecutive_rise": rise,
            "highlight": [-len(tail), -1],
        }

    trigger_mean = sum(trigger_values) / len(trigger_values) if trigger_values else 0.0
    ratio = trigger_mean / baseline["mean"] if baseline["mean"] else None
    ratio_text = f"（约为正常水平的 {ratio:.1f} 倍）" if ratio else ""
    return {
        "kind": "step",
        "verdict": "突变型（无事前爬升）",
        "detail": (
            f"触发前{metric_label}稳定在 {baseline['mean']:.1f}% 左右（趋势斜率 {slope:.2f}%/板），"
            f"触发段均值跳至 {trigger_mean:.1f}%{ratio_text}——属突发失效，"
            "应排查触发时刻附近的离散变化（换料、清洗、参数调整、设备动作异常）。"
        ),
        "trigger_mean": round(trigger_mean, 2),
        "jump_ratio": round(ratio, 2) if ratio else None,
        "highlight": [0, run_length - 1],
    }


def analyze_scope(
    trigger_points: list[dict[str, Any]],
    sibling_summaries: list[dict[str, Any]],
) -> dict[str, Any]:
    board_shares = [
        (point["board_ng_count"] / point["board_row_count"], point["board_row_count"])
        for point in trigger_points
        if point["board_row_count"]
    ]
    max_share = max(share for share, _ in board_shares)
    # Board-wide needs both a high NG share and enough recorded points on the
    # board — a board with a handful of rows reaches 50% trivially.
    qualified_shares = [
        share for share, rows in board_shares
        if share >= BOARD_WIDE_NG_SHARE and rows >= BOARD_WIDE_MIN_BOARD_ROWS
    ]
    ng_siblings = [item for item in sibling_summaries if item["trigger_ng_count"] > 0]

    if qualified_shares:
        kind = "board"
        detail = (
            f"触发板上最多 {max(qualified_shares) * 100:.0f}% 的检测点同时异常，"
            "为整板性失效，问题不局限于该焊盘。"
        )
    elif ng_siblings:
        kind = "component"
        names = "、".join(item["pad_name"] for item in ng_siblings)
        detail = (
            f"同元件焊盘 {names} 在触发板上同步判 NG——异常波及同一元件的多个焊盘，"
            "指向该元件区域的钢网开口或局部印刷条件。"
        )
    else:
        kind = "single"
        detail = (
            "同元件其余焊盘在触发板上全部正常——异常孤立于该焊盘，"
            "优先排查该 Pad 对应的钢网单孔状态。"
        )

    return {
        "kind": kind,
        "max_board_ng_share": round(max_share, 4),
        "ng_sibling_pads": [item["pad_name"] for item in ng_siblings],
        "detail": detail,
    }


def analyze_recovery(
    post_points: list[dict[str, Any]],
    baseline: dict[str, Any],
    metric_field: str,
    trigger_end_seq: int,
    param_events: list[dict[str, Any]],
) -> dict[str, Any]:
    production_after = [point for point in post_points if not point["is_recheck"]]
    if not production_after:
        return {
            "kind": "no_data",
            "verdict": "无法确认恢复",
            "detail": "事件后该机种再无生产数据，无法确认异常是否已消除——后续首板务必复检该焊盘。",
            "highlight": None,
        }

    recovered_seq = None
    recovered_board_no = None
    for offset, point in enumerate(production_after):
        value = point["values"].get(metric_field)
        within_band = (
            value is not None and baseline["available"]
            and value <= baseline["upper_band"]
        )
        if not point["is_ng"] and (within_band or not baseline["available"]):
            recovered_seq = point["seq"]
            recovered_board_no = offset + 1
            break

    if recovered_seq is None:
        return {
            "kind": "not_recovered",
            "verdict": "仍未恢复",
            "detail": (
                f"事件后 {len(production_after)} 块生产板该焊盘仍未回到正常水平"
                "（判 NG 或数值高于基线上限），截至数据末尾问题未消除，需立即处置而非观察。"
            ),
            "highlight": [production_after[0]["seq"], production_after[-1]["seq"]],
        }

    related = [
        event for event in param_events
        if trigger_end_seq < event["seq"] <= recovered_seq
    ]
    if related:
        names = "、".join(sorted({event["parameter"] for event in related}))
        detail = (
            f"异常自第 {recovered_board_no} 块后续板起消失，且恢复前发生了 {names} "
            "的程序设定变更——恢复大概率与该次调整相关，可作为根因佐证。"
        )
    else:
        detail = (
            f"异常自第 {recovered_board_no} 块后续板起消失，"
            "期间未记录到印刷程序设定变更——更可能由清洗钢网/搅拌锡膏等未记录的人工处置消除。"
        )

    return {
        "kind": "recovered",
        "verdict": "已恢复",
        "recovered_seq": recovered_seq,
        "related_param_events": related,
        "detail": detail,
        "highlight": [recovered_seq, recovered_seq],
    }


def analyze_periodicity(points: list[dict[str, Any]]) -> dict[str, Any]:
    starts = ng_run_starts(points)
    if len(starts) < PERIODIC_MIN_RUNS:
        return {
            "periodic": False,
            "run_count": len(starts),
            "detail": f"该焊盘历史上共出现 {len(starts)} 次 NG 连段，样本不足以判断周期性复发。",
        }

    gaps = [second - first for first, second in zip(starts, starts[1:])]
    mean_gap = sum(gaps) / len(gaps)
    if mean_gap <= 0:
        return {"periodic": False, "run_count": len(starts), "detail": "NG 连段间隔异常，无法判断周期性。"}
    variance = sum((gap - mean_gap) ** 2 for gap in gaps) / len(gaps)
    cv = (variance ** 0.5) / mean_gap

    if cv < PERIODIC_CV_THRESHOLD:
        return {
            "periodic": True,
            "run_count": len(starts),
            "mean_gap_boards": round(mean_gap, 1),
            "detail": (
                f"该焊盘的 NG 连段以约每 {mean_gap:.0f} 块板的节奏重复出现（共 {len(starts)} 次）——"
                "高度怀疑与钢网清洗周期等固定节拍相关，建议核对清洗设定。"
            ),
        }
    return {
        "periodic": False,
        "run_count": len(starts),
        "detail": f"该焊盘历史上有 {len(starts)} 次 NG 连段，但间隔无规律，未见周期性。",
    }


def collect_param_events(window_points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Setpoint changes between consecutive production boards in the window."""
    production = [point for point in window_points if not point["is_recheck"]]
    if not production:
        return []

    candidates = sorted(
        key for key in production[0]["row"]
        if key.endswith(PLAN_FIELD_SUFFIX) or key in PLAN_EXTRA_FIELDS
    )
    transitions = list(zip(production, production[1:]))
    fields = []
    for field in candidates:
        changes = sum(
            1 for previous, current in transitions
            if previous["row"].get(field) is not None
            and current["row"].get(field) is not None
            and str(previous["row"].get(field)) != str(current["row"].get(field))
        )
        if changes and transitions and changes / len(transitions) <= SETPOINT_MAX_CHANGE_SHARE:
            fields.append(field)

    events: list[dict[str, Any]] = []
    for previous, current in transitions:
        for field in fields:
            before = previous["row"].get(field)
            after = current["row"].get(field)
            if before is None or after is None or str(before) == str(after):
                continue
            events.append({
                "seq": current["seq"],
                "time": current["time"],
                "board_sn": current["board_sn"],
                "parameter": field.removesuffix(PLAN_FIELD_SUFFIX),
                "from": before,
                "to": after,
            })
    return events


def build_sibling_series(
    rows_by_inspection_pad: dict[tuple[str, str, str], dict[str, Any]],
    component: str,
    pad_name: str,
    model_pads: dict[str, list[dict[str, Any]]],
    window_points: list[dict[str, Any]],
    trigger_board_sns: set[str],
    metric_field: str,
) -> list[dict[str, Any]]:
    siblings = []
    for other_name in sorted(model_pads):
        if other_name == pad_name:
            continue
        other_component, _ = split_component_pad(other_name)
        if other_component != component:
            continue

        points = []
        trigger_ng = 0
        for window_point in window_points:
            key = (window_point["board_sn"], window_point["time"], other_name)
            row = rows_by_inspection_pad.get(key)
            if row is None:
                continue
            ng = is_ng(row)
            if ng and window_point["board_sn"] in trigger_board_sns and window_point["is_trigger"]:
                trigger_ng += 1
            points.append({
                "seq": window_point["seq"],
                "value": as_float(row.get(metric_field)),
                "is_ng": ng,
            })
        siblings.append({
            "pad_name": other_name,
            "trigger_ng_count": trigger_ng,
            "points": points,
        })
    return siblings


def build_heatmap(
    model_pads: dict[str, list[dict[str, Any]]],
    trigger_board_keys: set[tuple[str, str]],
) -> list[dict[str, Any]]:
    cells = []
    for pad_name, points in sorted(model_pads.items()):
        xs = [as_float(point["row"].get("comp_px")) for point in points]
        ys = [as_float(point["row"].get("comp_py")) for point in points]
        xs = [value for value in xs if value is not None]
        ys = [value for value in ys if value is not None]
        trigger_points = [
            point for point in points
            if (point["board_sn"], point["time"]) in trigger_board_keys
        ]
        cells.append({
            "pad_name": pad_name,
            "px": round(sum(xs) / len(xs), 3) if xs else None,
            "py": round(sum(ys) / len(ys), 3) if ys else None,
            "trigger_ng_count": sum(1 for point in trigger_points if point["is_ng"]),
            "trigger_board_count": len(trigger_points),
            "history_ng_count": sum(1 for point in points if point["is_ng"]),
        })
    return cells


def build_param_compare(parameter_check: dict[str, Any]) -> list[dict[str, Any]]:
    return parameter_check.get("drifted", [])


def build_param_series(window_points: list[dict[str, Any]]) -> dict[str, Any]:
    """Per-board actual/plan/diff of every printing parameter, aligned with the
    window series — rendered as overlay curves on the run chart. Parameter
    families are derived from `abs_<name>` columns; fields whose values move
    the most are listed first so drifting parameters surface on top."""
    if not window_points:
        return {"fields": [], "series": {}}

    names = sorted(
        key.removeprefix("abs_")
        for key in window_points[0]["row"]
        if key.startswith("abs_")
    )
    series: dict[str, list[dict[str, Any] | None]] = {}
    for name in names:
        values: list[dict[str, Any] | None] = []
        for point in window_points:
            row = point["row"]
            actual = as_float(row.get(name))
            if actual is None:
                values.append(None)
                continue
            plan = as_float(row.get(f"{name}{PLAN_FIELD_SUFFIX}"))
            diff = as_float(row.get(f"diff_{name}"))
            values.append({
                "v": round(actual, 4),
                "plan": round(plan, 4) if plan is not None else None,
                "diff": round(diff, 4) if diff is not None else None,
            })
        if any(values):
            series[name] = values

    def distinct_count(name: str) -> int:
        return len({item["v"] for item in series[name] if item})

    fields = sorted(series, key=lambda name: (-distinct_count(name), name))
    return {"fields": fields, "series": {name: series[name] for name in fields}}


def full_spi_sort_key(item: tuple[int, dict[str, Any]]) -> tuple[Any, str, str, int]:
    index, row = item
    return (
        parse_fdate(str(row.get("fdate") or "")) or datetime.min,
        str(row.get("barcode") or ""),
        str(row.get("compname") or ""),
        index,
    )


def compact_spi_row(
    row: dict[str, Any],
    index: int,
    trigger_row_ids: set[int],
    component: str,
    pad_name: str,
) -> dict[str, Any]:
    row_component, row_pad = split_component_pad(str(row.get("compname") or ""))
    return {
        "global_seq": index,
        "time": str(row.get("fdate") or ""),
        "board_sn": str(row.get("barcode") or ""),
        "model": str(row.get("cmodel") or ""),
        "machine": str(row.get("machinename") or ""),
        "component": row_component,
        "pad": row_pad,
        "pad_name": str(row.get("compname") or ""),
        "px": as_float(row.get("comp_px")),
        "py": as_float(row.get("comp_py")),
        "defect_type": str(row.get("comp_errname") or "").strip(),
        "is_ng": is_ng(row),
        "is_trigger_row": id(row) in trigger_row_ids,
        "is_same_component": row_component == component,
        "is_same_pad": str(row.get("compname") or "") == pad_name,
        "values": {
            field: round(value, 2) if value is not None else None
            for field in METRIC_FIELDS
            for value in [as_float(row.get(field))]
        },
    }


def build_full_spi_window(
    rows: list[dict[str, Any]],
    trigger_points: list[dict[str, Any]],
    component: str,
    pad_name: str,
) -> dict[str, Any]:
    ordered = sorted(enumerate(rows), key=full_spi_sort_key)
    positions = {id(row): position for position, (_, row) in enumerate(ordered)}
    trigger_row_ids = {id(point["row"]) for point in trigger_points}
    trigger_positions = [
        positions[row_id] for row_id in trigger_row_ids if row_id in positions
    ]

    if not trigger_positions:
        return {
            "scope": "full_spi",
            "order_by": "fdate, barcode, compname, source_index",
            "requested_before": FULL_SPI_CONTEXT_WINDOW,
            "requested_after": FULL_SPI_CONTEXT_WINDOW,
            "actual_before": 0,
            "actual_after": 0,
            "rows": [],
        }

    start_position = min(trigger_positions)
    end_position = max(trigger_positions)
    window_start = max(0, start_position - FULL_SPI_CONTEXT_WINDOW)
    window_end = min(len(ordered) - 1, end_position + FULL_SPI_CONTEXT_WINDOW)
    compact_rows = [
        compact_spi_row(row, source_index, trigger_row_ids, component, pad_name)
        for source_index, row in ordered[window_start:window_end + 1]
    ]
    return {
        "scope": "full_spi",
        "order_by": "fdate, barcode, compname, source_index",
        "requested_before": FULL_SPI_CONTEXT_WINDOW,
        "requested_after": FULL_SPI_CONTEXT_WINDOW,
        "actual_before": start_position - window_start,
        "actual_after": window_end - end_position,
        "trigger_start_seq": start_position - window_start,
        "trigger_end_seq": end_position - window_start,
        "rows": compact_rows,
    }


def direction_matches(defect_type: str, direction: str) -> bool:
    normalized = defect_type.lower()
    if direction == "多锡":
        return "over" in normalized
    if direction == "少锡":
        return "insufficient" in normalized or "less" in normalized or "under" in normalized
    return False


def coordinate_span(rows: list[dict[str, Any]], field: str) -> float | None:
    values = [row.get(field) for row in rows if row.get(field) is not None]
    if len(values) < 2:
        return None
    return max(values) - min(values)


def analyze_local_area(trigger_board_rows: list[dict[str, Any]]) -> dict[str, Any]:
    ng_rows = [
        row for row in trigger_board_rows
        if row["is_ng"] and row.get("px") is not None and row.get("py") is not None
    ]
    coordinate_rows = [
        row for row in trigger_board_rows
        if row.get("px") is not None and row.get("py") is not None
    ]
    distinct_ng_pads = {row["pad_name"] for row in ng_rows if row["pad_name"]}
    if len(distinct_ng_pads) < LOCAL_AREA_MIN_NG_ROWS or len(coordinate_rows) < LOCAL_AREA_MIN_NG_ROWS:
        return {
            "detected": False,
            "ng_rows": len(ng_rows),
            "distinct_ng_pads": len(distinct_ng_pads),
            "detail": (
                f"触发板仅涉及 {len(distinct_ng_pads)} 个不同 NG Pad，"
                "不足以判定为局部区域异常。"
            ),
        }

    board_x_span = coordinate_span(coordinate_rows, "px")
    board_y_span = coordinate_span(coordinate_rows, "py")
    ng_x_span = coordinate_span(ng_rows, "px")
    ng_y_span = coordinate_span(ng_rows, "py")
    if not board_x_span or not board_y_span or ng_x_span is None or ng_y_span is None:
        return {
            "detected": False,
            "ng_rows": len(ng_rows),
            "detail": "坐标范围不足，未判定为局部区域异常。",
        }

    x_share = ng_x_span / board_x_span
    y_share = ng_y_span / board_y_span
    detected = x_share <= LOCAL_AREA_MAX_SPAN_SHARE and y_share <= LOCAL_AREA_MAX_SPAN_SHARE
    return {
        "detected": detected,
        "ng_rows": len(ng_rows),
        "distinct_ng_pads": len(distinct_ng_pads),
        "x_span_share": round(x_share, 4),
        "y_span_share": round(y_share, 4),
        "detail": (
            f"触发板 NG 点集中在约 {x_share * 100:.0f}% x {y_share * 100:.0f}% 的坐标范围内，"
            "符合局部区域异常特征。"
            if detected else
            f"触发板 NG 点坐标跨度约 {x_share * 100:.0f}% x {y_share * 100:.0f}%，未形成明显局部聚集。"
        ),
    }


def build_context_summary(
    full_spi_window: dict[str, Any],
    component: str,
    pad_name: str,
    direction: str,
    trigger_board_sns: set[str],
) -> dict[str, Any]:
    rows = full_spi_window.get("rows", [])
    ng_rows = [row for row in rows if row["is_ng"]]
    same_component_ng = [
        row for row in ng_rows if row["component"] == component
    ]
    same_pad_ng = [row for row in ng_rows if row["pad_name"] == pad_name]
    same_direction_ng = [
        row for row in ng_rows if direction_matches(row["defect_type"], direction)
    ]
    trigger_board_rows = [
        row for row in rows if row["board_sn"] in trigger_board_sns
    ]
    trigger_board_ng = [row for row in trigger_board_rows if row["is_ng"]]
    local_area = analyze_local_area(trigger_board_rows)

    defect_counts: dict[str, int] = {}
    component_counts: dict[str, int] = {}
    pad_counts: dict[str, int] = {}
    for row in ng_rows:
        defect = row["defect_type"] or "NG"
        defect_counts[defect] = defect_counts.get(defect, 0) + 1
        component_counts[row["component"]] = component_counts.get(row["component"], 0) + 1
        pad_counts[row["pad_name"]] = pad_counts.get(row["pad_name"], 0) + 1

    def top_items(counts: dict[str, int], limit: int = 5) -> list[dict[str, Any]]:
        return [
            {"name": name, "count": count}
            for name, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:limit]
        ]

    total = len(rows)
    return {
        "total_rows": total,
        "ng_rows": len(ng_rows),
        "ng_rate": round(len(ng_rows) / total, 4) if total else 0.0,
        "same_direction_ng_rows": len(same_direction_ng),
        "same_component_ng_rows": len(same_component_ng),
        "same_pad_ng_rows": len(same_pad_ng),
        "trigger_board_rows": len(trigger_board_rows),
        "trigger_board_ng_rows": len(trigger_board_ng),
        "local_area": local_area,
        "dominant_defects": top_items(defect_counts),
        "top_ng_components": top_items(component_counts),
        "top_ng_pads": top_items(pad_counts),
    }


def classify_scope(
    scope: dict[str, Any],
    context_summary: dict[str, Any],
    exclusions: dict[str, Any],
) -> dict[str, Any]:
    local_area = context_summary.get("local_area") or {}
    if (exclusions.get("spi_false_alarm") or {}).get("status") == "suspect":
        category = "疑似SPI假异常"
        detail = "触发记录的缺陷标签与关键度量值不一致，需优先复核 SPI 程序、识别框和阈值。"
    elif scope["kind"] == "board":
        category = "整板同向"
        detail = scope["detail"]
    elif scope["kind"] == "component":
        category = "同元件多Pad异常"
        detail = scope["detail"]
    elif local_area.get("detected"):
        category = "局部区域"
        detail = local_area["detail"]
    elif context_summary["ng_rate"] >= BOARD_WIDE_NG_SHARE:
        category = "整板同向"
        detail = "前后全量 SPI 窗口 NG 占比较高，需要检查同线体/同设备的整体制程波动。"
    else:
        category = "单Pad孤立异常"
        detail = "同元件和全量窗口没有显示明显扩散，优先锁定触发 Pad 的局部原因。"
    return {
        "category": category,
        "detail": detail,
    }


def build_exclusion_checks(
    trigger_points: list[dict[str, Any]],
    change_type: dict[str, Any],
    recovery: dict[str, Any],
    full_spi_window: dict[str, Any],
    metric_field: str,
) -> dict[str, Any]:
    trigger_has_recheck = any(point["is_recheck"] for point in trigger_points)
    window_rows = full_spi_window.get("rows", [])
    trigger_rows = [row for row in window_rows if row["is_trigger_row"]]
    mixed_models = len({row["model"] for row in trigger_rows if row["model"]}) > 1
    mixed_boards = len({row["board_sn"] for row in trigger_rows if row["board_sn"]}) < TRIGGER_RUN_BOARDS

    data_flags = []
    if trigger_has_recheck:
        data_flags.append("触发段含复测记录")
    if mixed_models:
        data_flags.append("触发段跨机种")
    if mixed_boards:
        data_flags.append("触发段生产板数量不足")

    spi_flags = []
    trigger_metric_values = [
        point["values"].get(metric_field) for point in trigger_points
        if point["is_ng"] and point["values"].get(metric_field) is not None
    ]
    if change_type["kind"] == "unknown":
        spi_flags.append("事件前正常样本不足")
    if trigger_metric_values and max(trigger_metric_values) < SPI_FALSE_ALARM_METRIC_THRESHOLD:
        spi_flags.append("触发 NG 记录的主指标偏差不明显")
    if recovery["kind"] == "recovered" and not recovery.get("related_param_events"):
        spi_flags.append("后续自行恢复或存在未记录人工处置")
    strong_spi_flags = {"触发 NG 记录的主指标偏差不明显"}
    spi_status = (
        "suspect" if any(flag in strong_spi_flags for flag in spi_flags)
        else "review" if spi_flags else "pass"
    )

    return {
        "data_continuity": {
            "status": "pass" if not data_flags else "review",
            "flags": data_flags,
            "detail": "连续性检查未发现明显数据问题。" if not data_flags else "；".join(data_flags),
        },
        "spi_false_alarm": {
            "status": spi_status,
            "flags": spi_flags,
            "detail": "未发现明显 SPI 假异常信号。" if not spi_flags else "；".join(spi_flags),
        },
    }


def build_conclusion(
    direction: str,
    scope_classification: dict[str, Any],
    change_type: dict[str, Any],
    recovery: dict[str, Any],
    periodicity: dict[str, Any],
    parameter_check: dict[str, Any],
    cause_candidates: list[dict[str, Any]],
    exclusions: dict[str, Any],
) -> dict[str, Any]:
    """Collect root-cause candidates from the rule registry and rank them by
    confidence_base — the knowledge base's explicit confidence ladder — instead
    of an implicit insertion order."""
    candidates: list[dict[str, Any]] = []

    if exclusions["spi_false_alarm"]["status"] == "suspect":
        candidates.append(root_cause_candidate_from_rule(
            SPI_FALSE_ALARM_ROOT_CAUSE,
            exclusions["spi_false_alarm"]["detail"],
        ))

    drifted = parameter_check.get("drifted") or []
    if drifted:
        names = "、".join(item["parameter"] for item in drifted[:3])
        drift = root_cause_candidate_from_rule(
            PARAMETER_DRIFT_ROOT_CAUSE,
            PARAMETER_DRIFT_ROOT_CAUSE["evidence_template"].format(parameters=names),
        )
        drift["action"] = PARAMETER_DRIFT_ROOT_CAUSE["action_template"].format(parameters=names)
        drift["evidence_level"] = "高" if not parameter_check.get("cross_model_baseline") else "中"
        candidates.append(drift)

    related = recovery.get("related_param_events") or []
    if recovery["kind"] == "recovered" and related:
        names = "、".join(sorted({item["parameter"] for item in related}))
        recovered = root_cause_candidate_from_rule(
            PARAMETER_RECOVERY_ROOT_CAUSE,
            PARAMETER_RECOVERY_ROOT_CAUSE["evidence_template"].format(parameters=names),
        )
        recovered["action"] = PARAMETER_RECOVERY_ROOT_CAUSE["action_template"].format(parameters=names)
        candidates.append(recovered)

    if periodicity.get("periodic"):
        gap = periodicity.get("mean_gap_boards")
        evidence = (
            PERIODIC_ROOT_CAUSE["evidence_template"].format(gap=gap)
            if gap else periodicity["detail"]
        )
        candidates.append(root_cause_candidate_from_rule(PERIODIC_ROOT_CAUSE, evidence))

    category = scope_classification["category"]
    if category != "疑似SPI假异常":
        category_rule = scope_root_cause_candidate(
            direction, category, scope_classification["detail"],
        )
        if category_rule:
            candidates.append(category_rule)

    trend_rule = trend_root_cause_candidate(change_type["kind"], change_type["detail"])
    if trend_rule and (change_type["kind"] != "step" or not drifted):
        candidates.append(trend_rule)

    candidates.extend(cause_candidates)
    if not candidates:
        candidates.append(root_cause_candidate_from_rule(FALLBACK_ROOT_CAUSE))

    # Stable sort keeps collection order between equal weights; duplicate
    # causes keep their strongest entry.
    candidates.sort(key=lambda item: -item["confidence_base"])
    assessments: list[dict[str, Any]] = []
    for candidate in candidates:
        if candidate["cause"] in {item["cause"] for item in assessments}:
            continue
        assessments.append(candidate)
        if len(assessments) >= 3:
            break

    for priority, item in enumerate(assessments, 1):
        item["priority"] = priority
        item["ontology_ids"] = ontology_ids_for(
            direction=direction, scope=category, cause=item["cause"],
        )

    confidence = "中"
    if exclusions["data_continuity"]["status"] != "pass":
        confidence = "低"
    elif assessments[0]["evidence_level"] == "高":
        confidence = "高"

    return {
        "category": category,
        "direction": direction,
        "confidence": confidence,
        "root_cause_assessment": assessments,
        "recheck_plan": RECHECK_CRITERIA,
    }


def build_analysis_contract(
    trigger_id: str,
    model: str,
    machine: str,
    component: str,
    pad: str,
    pad_name: str,
    main_defect: str,
    defect_cn: str,
    direction: str,
    start_time: str,
    end_time: str,
    run_length: int,
    metric_label: str,
    trigger_values: list[float],
    change_type: dict[str, Any],
    scope_classification: dict[str, Any],
    context_summary: dict[str, Any],
    exclusions: dict[str, Any],
    recovery: dict[str, Any],
    conclusion: dict[str, Any],
) -> dict[str, Any]:
    """The single authoritative conclusion payload for UI, chat, and closure
    records. Everything user-facing about the judgment lives here; the rest of
    the trigger package is chart-ready raw data."""
    assessments = conclusion["root_cause_assessment"]
    primary = assessments[0] if assessments else {}
    category = scope_classification["category"]
    confidence = conclusion["confidence"]
    data_check = exclusions["data_continuity"]
    spi_check = exclusions["spi_false_alarm"]

    peak_text = (
        f"，{metric_label}最高 {max(trigger_values):.1f}%" if trigger_values else ""
    )
    conclusion_text = (
        f"焊盘 {pad_name} 连续 {run_length} 块生产板判 {main_defect}"
        f"（{direction}）{peak_text}，Agent 判定为{category}。"
    )

    disposition = disposition_for(
        data_status=data_check["status"],
        spi_status=spi_check["status"],
        category=category,
        recovery_kind=recovery["kind"],
        confidence=confidence,
    )

    evidence_summary = [
        {
            "name": "连续触发",
            "value": f"{run_length} 块生产板",
            "detail": "复测记录不参与连续生产板计数。",
        },
        {
            "name": "趋势形态",
            "value": change_type["verdict"],
            "detail": change_type["detail"],
        },
        {
            "name": "全量 SPI 窗口",
            "value": (
                f"{context_summary['total_rows']} 行 / "
                f"NG {context_summary['ng_rows']} 行"
            ),
            "detail": (
                f"同元件 NG {context_summary['same_component_ng_rows']} 行，"
                f"同 Pad NG {context_summary['same_pad_ng_rows']} 行。"
            ),
        },
        {
            "name": "范围判断",
            "value": category,
            "detail": scope_classification["detail"],
        },
        {
            "name": "恢复状态",
            "value": recovery["verdict"],
            "detail": recovery["detail"],
        },
    ]

    evidence_tags = [
        f"范围：{category}",
        f"置信度：{confidence}",
        f"窗口NG率：{context_summary['ng_rate'] * 100:.1f}%",
        f"恢复：{recovery['verdict']}",
    ]
    if spi_check["status"] != "pass":
        evidence_tags.append("SPI需复核")
    if data_check["status"] != "pass":
        evidence_tags.append("数据需复核")

    return {
        "version": "analysis-contract-v2",
        "trigger": {
            "trigger_id": trigger_id,
            "agent_type": "consecutive_pad_root_cause",
            "model": model,
            "machine": machine,
            "component": component,
            "pad": pad,
            "pad_name": pad_name,
            "defect_type": main_defect,
            "defect_cn": defect_cn,
            "direction": direction,
            "start_time": start_time,
            "end_time": end_time,
            "trigger_board_count": run_length,
            "conclusion": conclusion_text,
        },
        "trend": {
            "kind": change_type["kind"],
            "verdict": change_type["verdict"],
            "detail": change_type["detail"],
        },
        "scope": {
            "category": category,
            "detail": scope_classification["detail"],
            "confidence": confidence,
            "ontology_ids": ontology_ids_for(direction=direction, scope=category),
        },
        "evidence": {
            "summary": evidence_summary,
            "context": context_summary,
            "exclusion_checks": [
                {
                    "name": "数据连续性",
                    "status": data_check["status"],
                    "detail": data_check["detail"],
                },
                {
                    "name": "SPI 假异常",
                    "status": spi_check["status"],
                    "detail": spi_check["detail"],
                },
            ],
            "tags": evidence_tags,
        },
        "root_cause_candidates": assessments,
        "disposition": {
            "priority": disposition["priority"],
            "suggestion": disposition["disposition"],
            "reason": disposition["reason"],
            "primary_rule_id": primary.get("rule_id", "rule.unspecified"),
            "primary_rule_source": primary.get("rule_source", "knowledge_base"),
            "primary_cause": primary.get("cause", "待现场确认"),
            "primary_evidence": primary.get("evidence", "现有数据不足以形成单一证据。"),
            "primary_action": primary.get(
                "action", "复核触发 Pad、同元件 Pad、原始 SPI 图像和事件时段设备记录。",
            ),
        },
        "recheck": {
            "recovery_kind": recovery["kind"],
            "recovery_verdict": recovery["verdict"],
            "recovery_detail": recovery["detail"],
            "criteria": conclusion["recheck_plan"],
        },
    }


def build_trigger_package(
    trigger_no: int,
    model: str,
    pad_name: str,
    points: list[dict[str, Any]],
    run: tuple[int, int],
    model_pads: dict[str, list[dict[str, Any]]],
    rows: list[dict[str, Any]],
    rows_by_inspection_pad: dict[tuple[str, str, str], dict[str, Any]],
) -> dict[str, Any]:
    start_idx, end_idx = run
    trigger_id = f"TRG{trigger_no:03d}"
    component, pad = split_component_pad(pad_name)
    main_defect = main_defect_of_run(points, start_idx, end_idx)
    main_defect_key = normalize_defect_key(main_defect)
    metric_field = DEFECT_MAIN_METRIC.get(main_defect_key, "comp_avdp")
    metric_label = METRIC_LABELS[metric_field]
    defect_cn = DEFECT_NAME_CN.get(main_defect_key, main_defect)
    direction = "多锡" if main_defect_key.startswith("over") else "少锡"

    window_start = max(0, start_idx - WINDOW_RECORDS)
    window_end = min(len(points) - 1, end_idx + WINDOW_RECORDS)
    window_points = []
    for index in range(window_start, window_end + 1):
        point = points[index]
        window_points.append({
            "seq": index - start_idx,
            "board_sn": point["board_sn"],
            "time": point["time"],
            "is_recheck": point["is_recheck"],
            "is_ng": point["is_ng"],
            "is_trigger": start_idx <= index <= end_idx and not point["is_recheck"],
            "err": point["err"],
            "board_row_count": point["board_row_count"],
            "board_ng_count": point["board_ng_count"],
            "values": {
                field: round(value, 2) if value is not None else None
                for field, value in point["values"].items()
            },
            "row": point["row"],
        })

    trigger_points_raw = points[start_idx:end_idx + 1]
    trigger_window_points = [point for point in window_points if point["is_trigger"]]
    trigger_board_sns = {point["board_sn"] for point in trigger_window_points}
    trigger_board_keys = {
        (point["board_sn"], point["time"]) for point in trigger_window_points
    }

    pre_points = [
        point for point in window_points
        if point["seq"] < 0 and not point["is_recheck"] and not point["is_ng"]
    ]
    pre_values = [
        point["values"].get(metric_field) for point in pre_points
        if point["values"].get(metric_field) is not None
    ]
    baseline = (
        baseline_stats(pre_values)
        if len(pre_values) >= BASELINE_MIN_RECORDS
        else {"available": False, "count": len(pre_values)}
    )

    trigger_values = [
        point["values"].get(metric_field) for point in trigger_window_points
        if point["values"].get(metric_field) is not None
    ]
    run_length = len(trigger_window_points)

    param_events = collect_param_events(window_points)
    change_type = analyze_change_type(
        pre_values, trigger_values, baseline, metric_label, run_length,
    )
    siblings = build_sibling_series(
        rows_by_inspection_pad, component, pad_name, model_pads,
        window_points, trigger_board_sns, metric_field,
    )
    sibling_summaries = [
        {"pad_name": item["pad_name"], "trigger_ng_count": item["trigger_ng_count"]}
        for item in siblings
    ]
    scope = analyze_scope(trigger_window_points, sibling_summaries)
    trigger_end_seq = end_idx - start_idx
    post_points = [point for point in window_points if point["seq"] > trigger_end_seq]
    recovery = analyze_recovery(post_points, baseline, metric_field, trigger_end_seq, param_events)
    periodicity = analyze_periodicity(points)
    parameter_check = check_parameters(rows, trigger_board_sns, model)
    full_spi_window = build_full_spi_window(
        rows, trigger_points_raw, component, pad_name,
    )
    context_summary = build_context_summary(
        full_spi_window, component, pad_name, direction, trigger_board_sns,
    )
    exclusion_checks = build_exclusion_checks(
        trigger_points_raw, change_type, recovery, full_spi_window, metric_field,
    )
    scope_classification = classify_scope(scope, context_summary, exclusion_checks)
    cause_candidates = event_cause_candidates(
        direction, event_scope_for_category(scope_classification["category"]),
    )
    conclusion = build_conclusion(
        direction, scope_classification, change_type, recovery, periodicity,
        parameter_check, cause_candidates, exclusion_checks,
    )
    analysis_contract = build_analysis_contract(
        trigger_id=trigger_id,
        model=model,
        machine=str(rows[0].get("machinename") or "") if rows else "",
        component=component,
        pad=pad,
        pad_name=pad_name,
        main_defect=main_defect,
        defect_cn=defect_cn,
        direction=direction,
        start_time=trigger_window_points[0]["time"],
        end_time=trigger_window_points[-1]["time"],
        run_length=run_length,
        metric_label=metric_label,
        trigger_values=trigger_values,
        change_type=change_type,
        scope_classification=scope_classification,
        context_summary=context_summary,
        exclusions=exclusion_checks,
        recovery=recovery,
        conclusion=conclusion,
    )

    before_count = start_idx - window_start
    after_count = window_end - end_idx
    findings = [{
        "text": (
            f"焊盘 {pad_name} 于 {trigger_window_points[0]['time']} ~ "
            f"{trigger_window_points[-1]['time']} 连续 {run_length} 块生产板判 "
            f"{main_defect}（{defect_cn}），{metric_label}最高 "
            f"{max(trigger_values):.1f}%。" if trigger_values else
            f"焊盘 {pad_name} 连续 {run_length} 块生产板判 {main_defect}（{defect_cn}）。"
        ),
        "highlight": [0, run_length - 1],
    }]
    findings.append({"text": change_type["detail"], "highlight": change_type.get("highlight")})
    findings.append({"text": scope["detail"], "highlight": None})
    findings.append({"text": recovery["detail"], "highlight": recovery.get("highlight")})
    findings.append({"text": parameter_check["verdict"], "highlight": None})
    if periodicity["run_count"] >= PERIODIC_MIN_RUNS or periodicity["periodic"]:
        findings.append({"text": periodicity["detail"], "highlight": None})
    if before_count < WINDOW_RECORDS or after_count < WINDOW_RECORDS:
        findings.append({
            "text": (
                f"窗口说明：请求触发点前后各 {WINDOW_RECORDS} 条记录，"
                f"实际可取 前 {before_count} 条 / 后 {after_count} 条（该焊盘全部历史已展示）。"
            ),
            "highlight": None,
        })

    param_series = build_param_series(window_points)

    for point in window_points:
        point.pop("row", None)

    return {
        "trigger_id": trigger_id,
        "agent_type": "consecutive_pad_root_cause",
        "model": model,
        "machine": str(rows[0].get("machinename") or "") if rows else "",
        "component": component,
        "pad": pad,
        "pad_name": pad_name,
        "main_defect_type": main_defect,
        "main_defect_cn": defect_cn,
        "direction": direction,
        "metric_field": metric_field,
        "metric_label": metric_label,
        "start_time": trigger_window_points[0]["time"],
        "end_time": trigger_window_points[-1]["time"],
        "trigger_board_count": run_length,
        "window": {
            "requested": WINDOW_RECORDS,
            "before_count": before_count,
            "after_count": after_count,
        },
        "series": window_points,
        "full_spi_window": full_spi_window,
        "analysis_contract": analysis_contract,
        "baseline": baseline,
        "param_events": param_events,
        "param_series": param_series,
        "parameter_check": parameter_check,
        "findings": findings,
        "siblings": siblings,
        "heatmap": build_heatmap(model_pads, trigger_board_keys),
    }


def build_drilldown_report(
    rows: list[dict[str, Any]],
    source_table: str = "l780db.public.full_excel0623",
) -> dict[str, Any]:
    by_model = build_pad_points(rows)
    rows_by_inspection_pad = {
        (
            str(row.get("barcode") or ""),
            str(row.get("fdate") or ""),
            str(row.get("compname") or ""),
        ): row
        for row in rows
    }

    triggers = []
    trigger_no = 0
    for model in sorted(by_model):
        model_pads = by_model[model]
        for pad_name in sorted(model_pads):
            points = model_pads[pad_name]
            for run in detect_trigger_runs(points):
                trigger_no += 1
                triggers.append(build_trigger_package(
                    trigger_no, model, pad_name, points, run,
                    model_pads, rows, rows_by_inspection_pad,
                ))

    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source_table": source_table,
        "trigger_rule": f"同一焊盘连续 {TRIGGER_RUN_BOARDS} 块及以上生产板判 NG（复测不计入）",
        "window_records": WINDOW_RECORDS,
        "pad_series_window_records": PAD_SERIES_WINDOW,
        "full_spi_context_window_records": FULL_SPI_CONTEXT_WINDOW,
        "triggers": triggers,
        "caveats": [
            "趋势图窗口按该焊盘的检测记录截取，数据不足时如实标注实际条数。",
            "full_spi_window 为触发点前后全量 SPI 明细上下文，不限于触发焊盘，用于范围判断和证据摘要。",
            "突变/渐变判别复用前兆分析阈值（斜率 ≥0.5%/板 且末段连续 ≥3 板上升判渐变）。",
        ],
    }
