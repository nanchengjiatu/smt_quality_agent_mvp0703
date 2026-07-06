"""P1: process-dimension health checks (docs/p1_process_dimensions_design.md).

Three root-cause dimensions — stencil-cleaning cycle effect, first-board-
after-stoppage effect, print-direction odd/even difference — computed every
pipeline run and reported honestly as one of four states: effect present,
no visible effect, insufficient samples, or data not collected. On the
current dataset all three are grey/green; the value is that they light up
by themselves once production data starts carrying the signal.
"""

from __future__ import annotations

from typing import Any

from smt_quality_agent.param_correlation import (
    as_float,
    first_inspection_rows,
    is_ng,
    parse_fdate,
)


# A pause of at least this long between consecutive boards counts as a
# stoppage; the next board is a "first board".
GAP_THRESHOLD_MINUTES = 30

# Below this many first-board samples the verdict is "insufficient" instead
# of pretending a mean of a handful of boards is a finding.
MIN_FIRST_BOARD_SAMPLES = 30

# An effect must clear both an absolute floor and a fraction of the board-
# mean noise to be called real.
EFFECT_MIN_PP = 1.0
EFFECT_NOISE_RATIO = 0.5

VERDICT_LABELS = {
    "effect": "存在效应",
    "no_effect": "无明显效应",
    "insufficient": "样本不足",
    "not_collected": "数据未采集",
}


def production_boards(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Time-ordered per-board aggregates from first-inspection rows."""
    boards: dict[tuple[str, str], dict[str, Any]] = {}
    for row in first_inspection_rows(rows):
        time = parse_fdate(str(row.get("fdate") or ""))
        if time is None:
            continue
        key = (str(row.get("barcode") or ""), str(row.get("fdate") or ""))
        board = boards.setdefault(key, {
            "board_sn": key[0],
            "time": time,
            "time_text": key[1],
            "values": [],
            "ng_count": 0,
            "cleaningfrequency": as_float(row.get("cleaningfrequency")),
            "frontsqgpress": as_float(row.get("frontsqgpress")),
            "rearsqgpress": as_float(row.get("rearsqgpress")),
            "printdirection": str(row.get("printdirection") or "").strip(),
        })
        value = as_float(row.get("comp_avdp"))
        if value is not None:
            board["values"].append(value)
        if is_ng(row):
            board["ng_count"] += 1

    result = []
    for board in boards.values():
        board["mean_avdp"] = (
            sum(board["values"]) / len(board["values"]) if board["values"] else None
        )
        del board["values"]
        result.append(board)
    result.sort(key=lambda board: board["time"])
    return result


def _noise_sd(boards: list[dict[str, Any]]) -> float | None:
    means = [b["mean_avdp"] for b in boards if b["mean_avdp"] is not None]
    if len(means) < 2:
        return None
    mu = sum(means) / len(means)
    return (sum((m - mu) ** 2 for m in means) / len(means)) ** 0.5


def _effect_threshold(noise_sd: float | None) -> float:
    if noise_sd is None:
        return EFFECT_MIN_PP
    return max(EFFECT_MIN_PP, EFFECT_NOISE_RATIO * noise_sd)


def _result(verdict: str, detail: str, **extra: Any) -> dict[str, Any]:
    return {"verdict": verdict, "verdict_label": VERDICT_LABELS[verdict],
            "detail": detail, **extra}


def analyze_cleaning_cycle(
    boards: list[dict[str, Any]],
    noise_sd: float | None,
) -> dict[str, Any]:
    """Fold board-mean deviation by position inside the cleaning cycle.

    Honest premise (stated on the card): cleaning timestamps are not logged
    (CleaningAfterLastBoard is not collected), so the fold assumes a stable
    cadence from the start of the series and the phase is unknown.
    """
    freqs = [b["cleaningfrequency"] for b in boards if b["cleaningfrequency"]]
    if not freqs:
        return _result(
            "not_collected",
            "CleaningFrequency 列无有效值，无法评估清洗周期效应。",
        )
    freq = int(max(set(freqs), key=freqs.count))
    if freq < 2:
        return _result(
            "no_effect",
            f"擦网周期为每 {freq} 板一次，周期内不存在可累积的残留窗口。",
            frequency=freq,
        )

    positions: dict[int, list[float]] = {}
    for index, board in enumerate(boards):
        if board["mean_avdp"] is not None:
            positions.setdefault(index % freq, []).append(board["mean_avdp"])
    profile = [
        {
            "position": position,
            "mean": round(sum(values) / len(values), 2),
            "count": len(values),
        }
        for position, values in sorted(positions.items())
    ]
    if len(profile) < 2:
        return _result("insufficient", "周期内位置样本不足。", frequency=freq)

    amplitude = max(p["mean"] for p in profile) - min(p["mean"] for p in profile)
    threshold = _effect_threshold(noise_sd)
    extra = {
        "frequency": freq,
        "profile": profile,
        "amplitude_pp": round(amplitude, 2),
        "threshold_pp": round(threshold, 2),
        "noise_sd_pp": round(noise_sd, 2) if noise_sd is not None else None,
        "caveat": "清洗时刻未记录（CleaningAfterLastBoard 未采集），按序列起点折叠、相位未知。",
    }
    if amplitude >= threshold:
        return _result(
            "effect",
            f"周期 {freq} 板内偏差振幅 {amplitude:.2f}pp（≥门槛 {threshold:.2f}pp）——"
            "周期尾部偏差抬升，提示网底残留随擦网间隔累积。",
            **extra,
        )
    return _result(
        "no_effect",
        f"周期 {freq} 板内偏差振幅仅 {amplitude:.2f}pp（门槛 {threshold:.2f}pp，"
        f"板均噪声 σ={noise_sd:.2f}pp）——未见周期效应。" if noise_sd is not None
        else f"周期 {freq} 板内偏差振幅仅 {amplitude:.2f}pp——未见周期效应。",
        **extra,
    )


def analyze_first_board(
    boards: list[dict[str, Any]],
    noise_sd: float | None,
    gap_minutes: int = GAP_THRESHOLD_MINUTES,
) -> dict[str, Any]:
    """Compare first boards after a stoppage with all other boards."""
    first_means: list[float] = []
    other_means: list[float] = []
    first_ng = 0
    other_ng = 0
    for index, board in enumerate(boards):
        if board["mean_avdp"] is None:
            continue
        is_first = (
            index > 0
            and (board["time"] - boards[index - 1]["time"]).total_seconds()
            >= gap_minutes * 60
        )
        if is_first:
            first_means.append(board["mean_avdp"])
            first_ng += 1 if board["ng_count"] else 0
        else:
            other_means.append(board["mean_avdp"])
            other_ng += 1 if board["ng_count"] else 0

    extra = {
        "gap_threshold_minutes": gap_minutes,
        "first_board_count": len(first_means),
        "other_board_count": len(other_means),
        "first_ng_boards": first_ng,
        "other_ng_boards": other_ng,
    }
    if not first_means or not other_means:
        return _result(
            "insufficient",
            f"观察期内无 ≥{gap_minutes} 分钟的停机间隔，无法评估首板效应。",
            **extra,
        )

    first_mean = sum(first_means) / len(first_means)
    other_mean = sum(other_means) / len(other_means)
    delta = first_mean - other_mean
    threshold = _effect_threshold(noise_sd)
    extra.update({
        "first_mean_pp": round(first_mean, 2),
        "other_mean_pp": round(other_mean, 2),
        "delta_pp": round(delta, 2),
        "threshold_pp": round(threshold, 2),
    })
    if len(first_means) < MIN_FIRST_BOARD_SAMPLES:
        return _result(
            "insufficient",
            f"停机后首板仅 {len(first_means)} 块（判定需 ≥{MIN_FIRST_BOARD_SAMPLES}）。"
            f"现有观察：首板均值 {first_mean:.2f}pp vs 其余 {other_mean:.2f}pp、"
            f"首板 NG {first_ng} 板——暂无不利迹象，样本继续累积。",
            **extra,
        )
    if delta >= threshold:
        return _result(
            "effect",
            f"停机后首板偏差均值高出 {delta:.2f}pp（≥门槛 {threshold:.2f}pp，"
            f"n={len(first_means)}）——存在首板效应，建议停机重开后首件确认。",
            **extra,
        )
    return _result(
        "no_effect",
        f"停机后首板与其余板偏差均值差 {delta:+.2f}pp（门槛 {threshold:.2f}pp，"
        f"n={len(first_means)}）——未见不利首板效应。",
        **extra,
    )


def analyze_direction(
    boards: list[dict[str, Any]],
    noise_sd: float | None,
) -> dict[str, Any]:
    """Front/rear print-direction difference; honest about missing data."""
    directions = {b["printdirection"] for b in boards if b["printdirection"]}
    threshold = _effect_threshold(noise_sd)

    if len(directions) >= 2:
        groups: dict[str, list[float]] = {}
        for board in boards:
            if board["printdirection"] and board["mean_avdp"] is not None:
                groups.setdefault(board["printdirection"], []).append(board["mean_avdp"])
        stats = {
            key: {"count": len(values), "mean_pp": round(sum(values) / len(values), 2)}
            for key, values in groups.items()
        }
        means = [item["mean_pp"] for item in stats.values()]
        delta = max(means) - min(means)
        extra = {"groups": stats, "delta_pp": round(delta, 2), "threshold_pp": round(threshold, 2)}
        if delta >= threshold:
            return _result(
                "effect",
                f"不同印刷方向的板均偏差相差 {delta:.2f}pp（≥门槛 {threshold:.2f}pp）——"
                "存在方向差异，建议检查前后刮刀压力/角度对称性。",
                **extra,
            )
        return _result(
            "no_effect",
            f"不同印刷方向的板均偏差相差仅 {delta:.2f}pp（门槛 {threshold:.2f}pp）。",
            **extra,
        )

    # No direction column: the odd/even fold is only a weak proxy, so it is
    # reported as reference data, never as an effect verdict.
    odd = [b["mean_avdp"] for i, b in enumerate(boards) if b["mean_avdp"] is not None and i % 2]
    even = [b["mean_avdp"] for i, b in enumerate(boards) if b["mean_avdp"] is not None and not i % 2]
    proxy = None
    if odd and even:
        proxy = round(sum(odd) / len(odd) - sum(even) / len(even), 2)
    pressures = {
        (b["frontsqgpress"], b["rearsqgpress"]) for b in boards
        if b["frontsqgpress"] is not None or b["rearsqgpress"] is not None
    }
    return _result(
        "not_collected",
        "PrintDirection 列未采集，前后刮刀压力也无逐板变化，数据不含方向信号；"
        f"奇偶板折叠差 {proxy if proxy is not None else '-'}pp 仅供参考（奇偶与实际方向的对应关系未知）。"
        "已列入数据侧需求：PrintDirection 逐板采集。",
        odd_even_proxy_pp=proxy,
        pressure_varies=len(pressures) > 1,
    )


def build_process_dimensions(rows: list[dict[str, Any]]) -> dict[str, Any]:
    boards = production_boards(rows)
    noise_sd = _noise_sd(boards)
    return {
        "board_count": len(boards),
        "noise_sd_pp": round(noise_sd, 2) if noise_sd is not None else None,
        "cleaning_cycle": analyze_cleaning_cycle(boards, noise_sd),
        "first_board": analyze_first_board(boards, noise_sd),
        "direction": analyze_direction(boards, noise_sd),
    }
