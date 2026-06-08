from __future__ import annotations

from typing import Any


def normalize_affected_model_row(row: dict[str, Any]) -> dict[str, Any]:
    component, pad = split_component_pad(row.get("compname"))

    return {
        "work_order": str(row.get("cmodel") or ""),
        "product_name": str(row.get("cmodel") or ""),
        "board_sn": str(row.get("barcode") or ""),
        "inspect_time": str(row.get("fdate") or ""),
        "machine": str(row.get("machinename") or ""),
        "side": "",
        "component": component,
        "pad": pad,
        "x": row.get("comp_px"),
        "y": row.get("comp_py"),
        "raw_ng_type": row.get("comp_errname"),
        "volume_deviation_percent": row.get("comp_avdp"),
        "area_deviation_percent": row.get("comp_aadp"),
        "height_deviation_percent": row.get("comp_ahdp"),
        "printspeed": row.get("printspeed"),
        "printspeed_plan": row.get("printspeed_plan"),
        "diff_printspeed": row.get("diff_printspeed"),
        "frontsqgpress": row.get("frontsqgpress"),
        "frontsqgpress_plan": row.get("frontsqgpress_plan"),
        "diff_frontsqgpress": row.get("diff_frontsqgpress"),
        "rearsqgpress": row.get("rearsqgpress"),
        "rearsqgpress_plan": row.get("rearsqgpress_plan"),
        "diff_rearsqgpress": row.get("diff_rearsqgpress"),
        "snapoffspeed": row.get("snapoffspeed"),
        "snapoffspeed_plan": row.get("snapoffspeed_plan"),
        "diff_snapoffspeed": row.get("diff_snapoffspeed"),
        "cleaningfrequency": row.get("cleaningfrequency"),
        "cleaningfrequency_plan": row.get("cleaningfrequency_plan"),
        "diff_cleaningfrequency": row.get("diff_cleaningfrequency"),
    }


def normalize_affected_model_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [normalize_affected_model_row(row) for row in rows]


def split_component_pad(compname: Any) -> tuple[str, str]:
    value = str(compname or "").strip()
    if not value:
        return "", "1"

    head, separator, tail = value.rpartition("_")
    if separator and tail.isdigit() and head:
        return head, tail

    return value, "1"
