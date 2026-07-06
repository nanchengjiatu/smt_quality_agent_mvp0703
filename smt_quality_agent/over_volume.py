from __future__ import annotations

from typing import Any

from smt_quality_agent.affected_model import split_component_pad


# Printing parameters stored in the full SPI table (full_excel family) as
# <name>_plan / <name> / diff_<name> triples, kept for cause correlation.
PARAM_TRIPLES = (
    "printspeed",
    "frontsqgpress",
    "rearsqgpress",
    "printgap",
    "snapoffdistance",
    "snapoffspeed",
    "snapoffdelay",
    "sqgupspeed",
    "sqgdownspeed",
    "cleaningfrequency",
    "cleaningspeed",
    "markdeviation",
)

PARAM_SINGLES = (
    "cycletime",
    "printmode_plan",
    "printmode",
    "printdirection",
    "pcbsize",
    "cleaningafterlastboard",
    "temperature",
    "humidity",
)


def normalize_spi_row(row: dict[str, Any]) -> dict[str, Any]:
    """Map one full SPI export row into the rules-engine input contract."""
    component, pad = split_component_pad(row.get("compname"))

    normalized = {
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
    }

    for name in PARAM_TRIPLES:
        normalized[f"{name}_plan"] = row.get(f"{name}_plan")
        normalized[name] = row.get(name)
        normalized[f"diff_{name}"] = row.get(f"diff_{name}")

    for name in PARAM_SINGLES:
        normalized[name] = row.get(name)

    return normalized


def normalize_spi_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [normalize_spi_row(row) for row in rows]


# Backward-compatible names for existing callers and tests. The normalizer was
# originally introduced for an Over Volume-only MVP, but it supports all SPI
# defect names carried by the full export.
normalize_over_volume_row = normalize_spi_row
normalize_over_volume_rows = normalize_spi_rows
