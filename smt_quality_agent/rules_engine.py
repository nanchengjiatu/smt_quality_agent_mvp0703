from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from smt_quality_agent.knowledge_base import abnormal_cause_candidates


@dataclass(frozen=True)
class Defect:
    defect_type: str
    main_metric: str
    actual_value: float | None
    upper_limit: float | None
    lower_limit: float | None
    limit_value: float | None
    deviation_percent: float | None = None


@dataclass
class Abnormal:
    abnormal_id: str
    work_order: str
    product_name: str
    board_sn: str
    inspect_time: str
    machine: str
    side: str
    component: str
    pad: str
    defect_type: str
    main_metric: str
    actual_value: float | None
    upper_limit: float | None
    lower_limit: float | None
    deviation_percent: float | None
    abnormal_pattern: str = ""
    risk_level: str = ""
    repeat_count: int = 1
    affected_pad_count: int = 1
    affected_component_count: int = 1
    board_abnormal_ratio: float = 0.0
    root_cause_guess: list[str] = field(default_factory=list)
    suggested_action: list[str] = field(default_factory=list)
    cause_candidates: list[dict[str, Any]] = field(default_factory=list)
    status: str = "待处理"
    create_quality_case: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "abnormal_id": self.abnormal_id,
            "work_order": self.work_order,
            "product_name": self.product_name,
            "board_sn": self.board_sn,
            "inspect_time": self.inspect_time,
            "machine": self.machine,
            "side": self.side,
            "component": self.component,
            "pad": self.pad,
            "defect_type": self.defect_type,
            "main_metric": self.main_metric,
            "actual_value": self.actual_value,
            "upper_limit": self.upper_limit,
            "lower_limit": self.lower_limit,
            "deviation_percent": self.deviation_percent,
            "abnormal_pattern": self.abnormal_pattern,
            "risk_level": self.risk_level,
            "repeat_count": self.repeat_count,
            "affected_pad_count": self.affected_pad_count,
            "affected_component_count": self.affected_component_count,
            "board_abnormal_ratio": round(self.board_abnormal_ratio, 4),
            "root_cause_guess": self.root_cause_guess,
            "suggested_action": self.suggested_action,
            "cause_candidates": self.cause_candidates,
            "status": self.status,
            "create_quality_case": self.create_quality_case,
        }


# 实时模式 → 三轴中的空间×时间(ID 来自 ontology 的 SpatialExtent /
# TemporalPattern;数据有效性判断是下钻层的能力,实时口径不给出)。
PATTERN_AXES = {
    "同点多板异常": ("spatial.single_pad", "temporal.repeated"),
    "整板趋势异常": ("spatial.board_wide", "temporal.sporadic"),
    "同元件多Pad异常": ("spatial.component_multi_pad", "temporal.sporadic"),
    "单点偶发异常": ("spatial.single_pad", "temporal.sporadic"),
}


def run_agent(
    spi_rows: list[dict[str, Any]],
    total_pad_count_by_board: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    enable_board_trend = total_pad_count_by_board is not None
    total_pad_count_by_board = total_pad_count_by_board or infer_total_pad_counts(spi_rows)
    abnormals = build_abnormals(spi_rows)

    for abnormal in abnormals:
        pattern, risk_level = classify_pattern(
            abnormal,
            abnormals,
            total_pad_count_by_board,
            enable_board_trend,
        )
        causes = recommend_causes(abnormal.defect_type, pattern, risk_level)

        abnormal.abnormal_pattern = pattern
        abnormal.risk_level = risk_level
        abnormal.cause_candidates = causes
        abnormal.root_cause_guess = [item["cause"] for item in causes]
        abnormal.suggested_action = [item["action"] for item in causes]
        abnormal.create_quality_case = risk_level in {"中", "高"}

    results = []
    for item in abnormals:
        payload = item.to_dict()
        spatial, temporal = PATTERN_AXES.get(item.abnormal_pattern, (None, None))
        payload["spatial"] = spatial
        payload["temporal"] = temporal
        results.append(payload)
    return results


def build_quality_cases(abnormal_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    case_candidates = [
        item for item in abnormal_results
        if item["create_quality_case"]
    ]
    grouped: dict[tuple[str, ...], list[dict[str, Any]]] = {}

    for item in case_candidates:
        key = quality_case_group_key(item)
        grouped.setdefault(key, []).append(item)

    quality_cases = []
    for case_no, items in enumerate(grouped.values(), start=1):
        items = sorted(items, key=lambda item: item["inspect_time"])
        first = items[0]
        latest = items[-1]
        causes = latest["root_cause_guess"]
        actions = latest["suggested_action"]
        cause_candidates = latest.get("cause_candidates", [])

        quality_cases.append({
            "case_id": f"CASE{case_no:012d}",
            "abnormal_ids": [item["abnormal_id"] for item in items],
            "created_at": latest["inspect_time"],
            "work_order": latest["work_order"],
            "product_name": latest["product_name"],
            "board_sn": latest["board_sn"],
            "machine": latest["machine"],
            "component": latest["component"] if same_value(items, "component") else "多元件",
            "pad": latest["pad"] if same_value(items, "pad") else "多Pad",
            "defect_type": latest["defect_type"],
            "abnormal_pattern": latest["abnormal_pattern"],
            "risk_level": latest["risk_level"],
            "evidence_summary": build_evidence_summary(items),
            "root_cause_guess": causes,
            "suggested_action": actions,
            "cause_candidates": cause_candidates,
            "actual_cause": None,
            "actual_action": None,
            "owner": None,
            "status": "待处理",
            "recheck_result": "未复测",
            "effective": None,
            "closed_at": None,
            "first_inspect_time": first["inspect_time"],
            "latest_inspect_time": latest["inspect_time"],
            "abnormal_count": len(items),
            "source_scope": "首次检测全量SPI异常",
        })

    return quality_cases


def build_abnormals(spi_rows: list[dict[str, Any]]) -> list[Abnormal]:
    abnormals: list[Abnormal] = []

    for row in spi_rows:
        defect = judge_defect(row)
        if defect is None:
            continue

        abnormal_no = len(abnormals) + 1
        abnormals.append(build_abnormal(row, defect, abnormal_no))

    return abnormals


def judge_defect(row: dict[str, Any]) -> Defect | None:
    raw_defect = judge_raw_ng_type(row)
    if raw_defect is not None:
        return raw_defect

    for metric in ("volume", "area", "height"):
        value = as_float(row.get(metric))
        upper = as_float(row.get(f"{metric}_upper"))
        lower = as_float(row.get(f"{metric}_lower"))

        if value is None or upper is None or lower is None:
            continue

        if value > upper:
            return Defect(f"{metric_defect_prefix(metric)}多锡", metric, value, upper, lower, upper)
        if value < lower:
            return Defect(f"{metric_defect_prefix(metric)}少锡", metric, value, upper, lower, lower)

    return None


def judge_raw_ng_type(row: dict[str, Any]) -> Defect | None:
    raw_ng_type = str(row.get("raw_ng_type") or row.get("comp_errname") or "").strip()
    if not raw_ng_type:
        return None

    normalized = raw_ng_type.lower().replace("_", " ")
    defect_map = {
        "under volume": ("少锡", "volume", "volume_deviation_percent"),
        "under area": ("少锡", "area", "area_deviation_percent"),
        "under height": ("少锡", "height", "height_deviation_percent"),
        "over volume": ("多锡", "volume", "volume_deviation_percent"),
        "over area": ("多锡", "area", "area_deviation_percent"),
        "areaover": ("多锡", "area", "area_deviation_percent"),
        "over height": ("多锡", "height", "height_deviation_percent"),
    }
    mapped = defect_map.get(normalized)
    if mapped is None:
        return None

    defect_type, metric, deviation_field = mapped
    return Defect(
        defect_type=defect_type,
        main_metric=metric,
        actual_value=as_float(row.get(metric)),
        upper_limit=as_float(row.get(f"{metric}_upper")),
        lower_limit=as_float(row.get(f"{metric}_lower")),
        limit_value=None,
        deviation_percent=as_float(row.get(deviation_field)),
    )


def metric_defect_prefix(metric: str) -> str:
    return "" if metric == "volume" else "疑似"


def build_abnormal(row: dict[str, Any], defect: Defect, abnormal_no: int) -> Abnormal:
    return Abnormal(
        abnormal_id=f"ABN{abnormal_no:012d}",
        work_order=str(row["work_order"]),
        product_name=str(row["product_name"]),
        board_sn=str(row["board_sn"]),
        inspect_time=str(row["inspect_time"]),
        machine=str(row.get("machine", "")),
        side=str(row.get("side", "")),
        component=str(row["component"]),
        pad=str(row["pad"]),
        defect_type=defect.defect_type,
        main_metric=defect.main_metric,
        actual_value=defect.actual_value,
        upper_limit=defect.upper_limit,
        lower_limit=defect.lower_limit,
        deviation_percent=defect.deviation_percent
        if defect.deviation_percent is not None
        else calc_deviation_percent(defect.actual_value, defect.upper_limit, defect.lower_limit),
    )


def calc_deviation_percent(
    value: float | None,
    upper: float | None,
    lower: float | None,
) -> float | None:
    if value is None or upper is None or lower is None:
        return None

    target = (upper + lower) / 2
    if target == 0:
        return None

    return round((value - target) / target * 100, 2)


def classify_pattern(
    current: Abnormal,
    all_abnormals: list[Abnormal],
    total_pad_count_by_board: dict[str, int],
    enable_board_trend: bool,
) -> tuple[str, str]:
    current.board_abnormal_ratio = get_board_abnormal_ratio(
        current.board_sn,
        all_abnormals,
        total_pad_count_by_board,
    )

    repeat_count = count_three_board_repeat(current, all_abnormals)
    current.repeat_count = repeat_count
    if repeat_count >= 3:
        return "同点多板异常", "高"

    if enable_board_trend:
        board_ratio = current.board_abnormal_ratio
        if board_ratio >= 0.10:
            return "整板趋势异常", "高"
        if board_ratio >= 0.05:
            return "整板趋势异常", "中"

    affected_pad_count = count_same_component_pads(current, all_abnormals)
    current.affected_pad_count = affected_pad_count
    if affected_pad_count >= 2:
        return "同元件多Pad异常", "中"

    return "单点偶发异常", "低"


def count_three_board_repeat(current: Abnormal, all_abnormals: list[Abnormal]) -> int:
    matching = [
        item
        for item in all_abnormals
        if item.work_order == current.work_order
        and item.product_name == current.product_name
        and item.component == current.component
        and item.pad == current.pad
        and item.defect_type == current.defect_type
    ]
    board_sns: list[str] = []

    for item in sorted(matching, key=lambda item: item.inspect_time):
        if item.board_sn not in board_sns:
            board_sns.append(item.board_sn)

    return len(board_sns)


def count_same_component_pads(current: Abnormal, all_abnormals: list[Abnormal]) -> int:
    pads = {
        item.pad
        for item in all_abnormals
        if item.board_sn == current.board_sn
        and item.component == current.component
        and item.defect_type == current.defect_type
    }
    return len(pads)


def get_board_abnormal_ratio(
    board_sn: str,
    all_abnormals: list[Abnormal],
    total_pad_count_by_board: dict[str, int],
) -> float:
    total_pad_count = total_pad_count_by_board.get(board_sn, 0)
    if total_pad_count <= 0:
        return 0.0

    board_abnormal_count = sum(1 for item in all_abnormals if item.board_sn == board_sn)
    return board_abnormal_count / total_pad_count


def recommend_causes(defect_type: str, pattern: str, risk_level: str) -> list[dict[str, Any]]:
    return abnormal_cause_candidates(defect_type, pattern, risk_level)


def quality_case_group_key(item: dict[str, Any]) -> tuple[str, ...]:
    base = (
        item["work_order"],
        item["product_name"],
        item["defect_type"],
        item["abnormal_pattern"],
    )

    if item["abnormal_pattern"] == "同点多板异常":
        return base + (item["component"], item["pad"])
    if item["abnormal_pattern"] == "同元件多Pad异常":
        return base + (item["board_sn"], item["component"])
    if item["abnormal_pattern"] == "整板趋势异常":
        return base + (item["board_sn"],)

    return base + (item["board_sn"], item["component"], item["pad"])


def same_value(items: list[dict[str, Any]], field: str) -> bool:
    values = {item[field] for item in items}
    return len(values) == 1


def build_evidence_summary(items: list[dict[str, Any]]) -> str:
    latest = items[-1]
    pattern = latest["abnormal_pattern"]

    if pattern == "同点多板异常":
        boards = "、".join(item["board_sn"] for item in items)
        return (
            f"{latest['component']} Pad{latest['pad']} 在 {boards} "
            f"共 {len(items)} 块板重复出现{latest['defect_type']}（不要求连续），"
            f"主指标为 {latest['main_metric']}。"
        )

    if pattern == "同元件多Pad异常":
        pads = "、".join(item["pad"] for item in items)
        return (
            f"{latest['board_sn']} 的 {latest['component']} 多个 Pad 同时出现"
            f"{latest['defect_type']}，涉及 Pad：{pads}。"
        )

    if pattern == "整板趋势异常":
        ratio = latest["board_abnormal_ratio"] * 100
        return (
            f"{latest['board_sn']} 出现整板{latest['defect_type']}趋势，"
            f"异常点数 {len(items)}，异常占比约 {ratio:.2f}%。"
        )

    return (
        f"{latest['board_sn']} {latest['component']} Pad{latest['pad']} "
        f"出现{latest['defect_type']}。"
    )


def infer_total_pad_counts(spi_rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, set[tuple[str, str]]] = {}
    for row in spi_rows:
        board_sn = str(row["board_sn"])
        counts.setdefault(board_sn, set()).add((str(row["component"]), str(row["pad"])))

    return {board_sn: len(points) for board_sn, points in counts.items()}


def as_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    return float(value)
