from __future__ import annotations

from datetime import datetime
from typing import Any

from smt_quality_agent.affected_model import split_component_pad
from smt_quality_agent.param_correlation import (
    BOARD_WIDE_NG_SHARE,
    DEFECT_MAIN_METRIC,
    DEFECT_NAME_CN,
    EVENT_RULES,
    METRIC_FIELDS,
    METRIC_LABELS,
    PRECURSOR_RISE_THRESHOLD,
    PRECURSOR_SLOPE_THRESHOLD,
    as_float,
    check_parameters,
    is_ng,
    linear_slope,
    parse_fdate,
    tail_consecutive_rise,
)


# A pad failing on at least this many consecutive production boards triggers
# a drill-down package.
TRIGGER_RUN_BOARDS = 3

# How many of the pad's records before and after the trigger run to include.
WINDOW_RECORDS = 300

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
    max_share = max(
        (point["board_ng_count"] / point["board_row_count"])
        for point in trigger_points
        if point["board_row_count"]
    )
    ng_siblings = [item for item in sibling_summaries if item["trigger_ng_count"] > 0]

    if max_share >= BOARD_WIDE_NG_SHARE:
        kind = "board"
        rule_scope = "整板大面积"
        detail = (
            f"触发板上最多 {max_share * 100:.0f}% 的检测点同时异常，"
            "为整板性失效，问题不局限于该焊盘。"
        )
    elif ng_siblings:
        kind = "component"
        rule_scope = "局部焊盘"
        names = "、".join(item["pad_name"] for item in ng_siblings)
        detail = (
            f"同元件焊盘 {names} 在触发板上同步判 NG——异常波及同一元件的多个焊盘，"
            "指向该元件区域的钢网开口或局部印刷条件。"
        )
    else:
        kind = "single"
        rule_scope = "局部焊盘"
        detail = (
            "同元件其余焊盘在触发板上全部正常——异常孤立于该焊盘，"
            "优先排查该 Pad 对应的钢网单孔状态。"
        )

    return {
        "kind": kind,
        "rule_scope": rule_scope,
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
    component, pad = split_component_pad(pad_name)
    main_defect = main_defect_of_run(points, start_idx, end_idx)
    metric_field = DEFECT_MAIN_METRIC.get(main_defect.lower(), "comp_avdp")
    metric_label = METRIC_LABELS[metric_field]
    defect_cn = DEFECT_NAME_CN.get(main_defect.lower(), main_defect)
    direction = "多锡" if main_defect.lower().startswith("over") else "少锡"

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
    causes = EVENT_RULES.get((direction, scope["rule_scope"]), [])

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
        "trigger_id": f"TRG{trigger_no:03d}",
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
        "baseline": baseline,
        "param_events": param_events,
        "param_series": param_series,
        "change_type": change_type,
        "scope": scope,
        "recovery": recovery,
        "periodicity": periodicity,
        "parameter_check": parameter_check,
        "findings": findings,
        "suggested_causes": [item[0] for item in causes],
        "suggested_actions": [item[1] for item in causes],
        "siblings": siblings,
        "heatmap": build_heatmap(model_pads, trigger_board_keys),
    }


def build_drilldown_report(rows: list[dict[str, Any]]) -> dict[str, Any]:
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
        "source_table": "l780db.public.full_excel0608",
        "trigger_rule": f"同一焊盘连续 {TRIGGER_RUN_BOARDS} 块及以上生产板判 NG（复测不计入）",
        "window_records": WINDOW_RECORDS,
        "triggers": triggers,
        "caveats": [
            "下钻窗口按该焊盘的检测记录截取，数据不足时如实标注实际条数。",
            "突变/渐变判别复用前兆分析阈值（斜率 ≥0.5%/板 且末段连续 ≥3 板上升判渐变）。",
        ],
    }
