from __future__ import annotations

from collections import Counter
from typing import Any


def build_dashboard_summary(
    abnormal_results: list[dict[str, Any]],
    quality_cases: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "abnormal_count": len(abnormal_results),
        "more_solder_count": count_by_defect(abnormal_results, "多锡"),
        "less_solder_count": count_by_defect(abnormal_results, "少锡"),
        "high_risk_count": count_by_field(abnormal_results, "risk_level", "高"),
        "medium_risk_count": count_by_field(abnormal_results, "risk_level", "中"),
        "low_risk_count": count_by_field(abnormal_results, "risk_level", "低"),
        "open_case_count": count_open_cases(quality_cases),
        "case_count": len(quality_cases),
        "recheck_effective_rate": calc_recheck_effective_rate(quality_cases),
        "recurrence_count": count_by_field(quality_cases, "status", "复发"),
    }


def build_dashboard_top(
    abnormal_results: list[dict[str, Any]],
    quality_cases: list[dict[str, Any]],
    limit: int = 10,
) -> dict[str, Any]:
    return {
        "top_components": top_components(abnormal_results, limit),
        "top_pads": top_pads(abnormal_results, limit),
        "top_products": top_products(abnormal_results, limit),
        "top_patterns": top_patterns(abnormal_results, limit),
        "top_case_locations": top_case_locations(quality_cases, limit),
    }


def count_by_defect(items: list[dict[str, Any]], defect_suffix: str) -> int:
    return sum(1 for item in items if item.get("defect_type", "").endswith(defect_suffix))


def count_by_field(items: list[dict[str, Any]], field: str, value: str) -> int:
    return sum(1 for item in items if item.get(field) == value)


def count_open_cases(quality_cases: list[dict[str, Any]]) -> int:
    closed_statuses = {"已关闭", "忽略"}
    return sum(1 for case in quality_cases if case.get("status") not in closed_statuses)


def calc_recheck_effective_rate(quality_cases: list[dict[str, Any]]) -> float | None:
    rechecked_cases = [
        case for case in quality_cases
        if case.get("recheck_result") in {"OK", "NG"}
    ]
    if not rechecked_cases:
        return None

    effective_cases = [
        case for case in rechecked_cases
        if case.get("recheck_result") == "OK" or case.get("effective") is True
    ]
    return round(len(effective_cases) / len(rechecked_cases), 4)


def top_components(abnormal_results: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    counter = Counter(item["component"] for item in abnormal_results)
    return [
        {
            "component": component,
            "defect_count": count,
            "main_defect_type": main_defect_type(
                item for item in abnormal_results
                if item["component"] == component
            ),
        }
        for component, count in counter.most_common(limit)
    ]


def top_pads(abnormal_results: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    counter = Counter((item["component"], item["pad"]) for item in abnormal_results)
    return [
        {
            "component": component,
            "pad": pad,
            "defect_count": count,
            "main_defect_type": main_defect_type(
                item for item in abnormal_results
                if item["component"] == component and item["pad"] == pad
            ),
        }
        for (component, pad), count in counter.most_common(limit)
    ]


def top_products(abnormal_results: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    counter = Counter(item["product_name"] for item in abnormal_results)
    return [
        {
            "product_name": product_name,
            "defect_count": count,
            "main_defect_type": main_defect_type(
                item for item in abnormal_results
                if item["product_name"] == product_name
            ),
        }
        for product_name, count in counter.most_common(limit)
    ]


def top_patterns(abnormal_results: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    counter = Counter(item["abnormal_pattern"] for item in abnormal_results)
    return [
        {
            "abnormal_pattern": pattern,
            "defect_count": count,
        }
        for pattern, count in counter.most_common(limit)
    ]


def top_case_locations(quality_cases: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    counter = Counter((case["component"], case["pad"]) for case in quality_cases)
    return [
        {
            "component": component,
            "pad": pad,
            "case_count": count,
        }
        for (component, pad), count in counter.most_common(limit)
    ]


def main_defect_type(items: Any) -> str | None:
    counter = Counter(item["defect_type"] for item in items)
    if not counter:
        return None
    return counter.most_common(1)[0][0]
