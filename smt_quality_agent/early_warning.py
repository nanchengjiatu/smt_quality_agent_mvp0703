"""P0 phase 1: EWMA drift monitoring — pure functions and a replay harness.

Watches the continuous per-pad deviation magnitudes (comp_avdp/aadp/ahdp)
carried by every row — PASS rows included — and grades drift into the
L1/L2/L3 warning ladder from docs/p0_early_warning_design.md §3. Phase 1
deliberately stops at pure functions plus backtesting: no pipeline stage,
no UI. λ and L are calibrated by the parameter-grid backtest, not asserted.

Comp_*dp are unsigned deviation magnitudes (direction lives only in the
defect name), so all monitoring is one-sided: magnitude climbing = worse.
"""

from __future__ import annotations

import hashlib
import math
from collections import deque
from typing import Any

from smt_quality_agent.param_correlation import (
    METRIC_FIELDS,
    as_float,
    first_inspection_rows,
    is_ng,
    parse_fdate,
    tail_consecutive_rise,
)


# Defaults; the backtest grid (backtest_grid) exists to challenge them.
EWMA_LAMBDA = 0.2
EWMA_LIMIT_L = 3.0

# Rolling per-pad baseline: previous PASS observations only, frozen while the
# metric is in alarm so a drift cannot drag its own control limit upward.
BASELINE_BOARDS = 200
BASELINE_MIN_RECORDS = 30

# Ladder thresholds (§3.2). Three consecutive boards aligns with the strict
# three-board trigger rule so the escalation story stays explainable.
L2_CONSECUTIVE_BOARDS = 3
L2_METRIC_CONSONANCE = 2
L3_TAIL_RISE = 3

# An episode ends after the EWMA stays back under the limit this many boards.
RECOVERY_BOARDS = 3

# Backtest bookkeeping: an L2 episode followed by an NG on the same pad within
# this many boards counts as a hit; otherwise it is charged as a false alarm.
HIT_HORIZON_BOARDS = 50

# Synthetic pad name for the board-mean series (whole-stencil drift).
BOARD_PAD = "__board__"


def warning_id(model: str, pad_name: str, board_sn: str, time_text: str) -> str:
    """Deterministic id derived from the first-exceed board's identity, so
    read/ack state survives window slides — same scheme as trigger_id."""
    identity = "|".join((model, pad_name, board_sn, time_text))
    return "WRN-" + hashlib.sha1(identity.encode("utf-8")).hexdigest()[:8]


def pad_points(rows: list[dict[str, Any]]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    """Per (model, pad) time-ordered board points with all three metrics."""
    by_pad: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in first_inspection_rows(rows):
        pad_name = str(row.get("compname") or "")
        time = parse_fdate(str(row.get("fdate") or ""))
        if not pad_name or time is None:
            continue
        key = (str(row.get("cmodel") or ""), pad_name)
        by_pad.setdefault(key, []).append({
            "board_sn": str(row.get("barcode") or ""),
            "time": time,
            "time_text": str(row.get("fdate") or ""),
            "is_ng": is_ng(row),
            "values": {field: as_float(row.get(field)) for field in METRIC_FIELDS},
        })
    for points in by_pad.values():
        points.sort(key=lambda point: point["time"])
    return by_pad


def board_points(rows: list[dict[str, Any]]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    """Per model, the board-mean series (all pads averaged) as one series."""
    boards: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in first_inspection_rows(rows):
        time = parse_fdate(str(row.get("fdate") or ""))
        if time is None:
            continue
        key = (str(row.get("cmodel") or ""), str(row.get("barcode") or ""), str(row.get("fdate") or ""))
        board = boards.setdefault(key, {
            "board_sn": key[1],
            "time": time,
            "time_text": key[2],
            "is_ng": False,
            "sums": {field: 0.0 for field in METRIC_FIELDS},
            "counts": {field: 0 for field in METRIC_FIELDS},
        })
        board["is_ng"] = board["is_ng"] or is_ng(row)
        for field in METRIC_FIELDS:
            value = as_float(row.get(field))
            if value is not None:
                board["sums"][field] += value
                board["counts"][field] += 1

    by_model: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for (model, _, _), board in boards.items():
        by_model.setdefault((model, BOARD_PAD), []).append({
            "board_sn": board["board_sn"],
            "time": board["time"],
            "time_text": board["time_text"],
            "is_ng": board["is_ng"],
            "values": {
                field: board["sums"][field] / board["counts"][field]
                if board["counts"][field] else None
                for field in METRIC_FIELDS
            },
        })
    for points in by_model.values():
        points.sort(key=lambda point: point["time"])
    return by_model


def causal_ng_floors(rows: list[dict[str, Any]]) -> list[tuple[Any, float]]:
    """Time-sorted running minimum of NG comp_avdp observations.

    Used causally by the L3 "entered the observed-NG band" check: only NG
    seen before the current board counts. The design pins this check to avdp
    (§3.2) — Under Height rows carry ahdp values down to 0, which would make
    a magnitude floor meaningless. Non-positive values are skipped for the
    same reason.
    """
    observations = []
    for row in first_inspection_rows(rows):
        if not is_ng(row):
            continue
        time = parse_fdate(str(row.get("fdate") or ""))
        value = as_float(row.get("comp_avdp"))
        if time is not None and value is not None and value > 0:
            observations.append((time, value))
    observations.sort(key=lambda item: item[0])

    floors = []
    running = None
    for time, value in observations:
        running = value if running is None else min(running, value)
        floors.append((time, running))
    return floors


def _new_metric_state() -> dict[str, Any]:
    return {
        "baseline": deque(),
        "sum": 0.0,
        "sumsq": 0.0,
        "armed": False,
        "ewma": None,
        "mu": None,
        "sigma": None,
        "limit": None,
        "above": False,
        "consec_above": 0,
        "raw": [],
    }


def _update_baseline(
    state: dict[str, Any],
    value: float,
    lam: float,
    limit_l: float,
    baseline_boards: int,
) -> None:
    state["baseline"].append(value)
    state["sum"] += value
    state["sumsq"] += value * value
    if len(state["baseline"]) > baseline_boards:
        old = state["baseline"].popleft()
        state["sum"] -= old
        state["sumsq"] -= old * old
    count = len(state["baseline"])
    if count < BASELINE_MIN_RECORDS:
        return
    mu = state["sum"] / count
    variance = max(state["sumsq"] / count - mu * mu, 0.0)
    # σ floor keeps a perfectly flat baseline from turning any noise into an
    # instant alarm.
    sigma = max(math.sqrt(variance), 0.01)
    state["mu"] = mu
    state["sigma"] = sigma
    state["limit"] = mu + limit_l * sigma * math.sqrt(lam / (2.0 - lam))
    if not state["armed"]:
        state["armed"] = True
        state["ewma"] = mu


def monitor_pad(
    model: str,
    pad_name: str,
    points: list[dict[str, Any]],
    lam: float = EWMA_LAMBDA,
    limit_l: float = EWMA_LIMIT_L,
    baseline_boards: int = BASELINE_BOARDS,
    ng_floors: list[tuple[Any, float]] | None = None,
) -> dict[str, Any]:
    """Walk one series in time order; return per-board levels and episodes.

    Pure function of its inputs: replaying the same window of boards yields
    the same episodes and warning ids, which is what makes the sliding-window
    recompute model safe for warnings too.
    """
    states = {field: _new_metric_state() for field in METRIC_FIELDS}
    episodes: list[dict[str, Any]] = []
    levels: list[int] = []
    episode: dict[str, Any] | None = None
    below_streak = 0
    floors = ng_floors or []
    floor_index = 0
    current_floor: float | None = None

    for index, point in enumerate(points):
        while floor_index < len(floors) and floors[floor_index][0] < point["time"]:
            current_floor = floors[floor_index][1]
            floor_index += 1

        crossed: list[str] = []
        tail_rise_hit = False
        band_hit = False
        for field in METRIC_FIELDS:
            state = states[field]
            value = point["values"].get(field)
            if value is None:
                continue
            state["raw"].append(value)
            if state["armed"]:
                state["ewma"] = lam * value + (1.0 - lam) * state["ewma"]
                state["above"] = state["ewma"] > state["limit"]
                state["consec_above"] = state["consec_above"] + 1 if state["above"] else 0
                if state["above"]:
                    crossed.append(field)
                    if tail_consecutive_rise(state["raw"]) >= L3_TAIL_RISE:
                        tail_rise_hit = True
                    if field == "comp_avdp" and current_floor is not None and value >= current_floor:
                        band_hit = True
            # Baseline learns only clean history: never NG rows, and never
            # while this metric is in alarm (the freeze from §3.1).
            if not state["above"] and not point["is_ng"]:
                _update_baseline(state, value, lam, limit_l, baseline_boards)

        level = 0
        if crossed:
            level = 1
            if (
                max(states[field]["consec_above"] for field in crossed) >= L2_CONSECUTIVE_BOARDS
                or len(crossed) >= L2_METRIC_CONSONANCE
            ):
                level = 2
            if level == 2 and (tail_rise_hit or band_hit):
                level = 3
        levels.append(level)

        if crossed:
            below_streak = 0
            if episode is None:
                episode = {
                    "warning_id": warning_id(model, pad_name, point["board_sn"], point["time_text"]),
                    "model": model,
                    "pad_name": pad_name,
                    "start_index": index,
                    "start_board_sn": point["board_sn"],
                    "start_time_text": point["time_text"],
                    "level": 0,
                    "l2_index": None,
                    "l2_time_text": None,
                    "l3_index": None,
                    "boards_above": 0,
                    "metrics": set(),
                    "last_above_index": index,
                    "end_index": None,
                }
            episode["boards_above"] += 1
            episode["metrics"].update(crossed)
            episode["level"] = max(episode["level"], level)
            episode["last_above_index"] = index
            if level >= 2 and episode["l2_index"] is None:
                episode["l2_index"] = index
                episode["l2_time_text"] = point["time_text"]
            if level >= 3 and episode["l3_index"] is None:
                episode["l3_index"] = index
        elif episode is not None:
            below_streak += 1
            if below_streak >= RECOVERY_BOARDS:
                episode["end_index"] = episode["last_above_index"]
                episode["metrics"] = sorted(episode["metrics"])
                episodes.append(episode)
                episode = None

    if episode is not None:
        episode["metrics"] = sorted(episode["metrics"])
        episodes.append(episode)  # still active at end of window

    return {"episodes": episodes, "levels": levels}


def replay(
    rows: list[dict[str, Any]],
    lam: float = EWMA_LAMBDA,
    limit_l: float = EWMA_LIMIT_L,
    baseline_boards: int = BASELINE_BOARDS,
) -> dict[str, Any]:
    """Monitor every pad series plus the board-mean series over ``rows``."""
    series = pad_points(rows)
    series.update(board_points(rows))
    floors = causal_ng_floors(rows)

    episodes: list[dict[str, Any]] = []
    by_series: dict[tuple[str, str], dict[str, Any]] = {}
    for key in sorted(series):
        model, pad_name = key
        result = monitor_pad(
            model, pad_name, series[key], lam, limit_l, baseline_boards, floors,
        )
        by_series[key] = {"points": series[key], "result": result}
        episodes.extend(result["episodes"])
    return {"episodes": episodes, "series": by_series}


def backtest(
    rows: list[dict[str, Any]],
    lam: float = EWMA_LAMBDA,
    limit_l: float = EWMA_LIMIT_L,
    baseline_boards: int = BASELINE_BOARDS,
) -> dict[str, Any]:
    """Replay history and score this parameter set (§5 acceptance metrics).

    Hits vs false alarms: an L2+ episode is a hit when the same pad goes NG
    within HIT_HORIZON_BOARDS boards of the episode reaching L2; every other
    L2+ episode is charged to the false-alarm budget. Leads are reported per
    NG pad as boards between the L2 escalation and the first NG.
    """
    played = replay(rows, lam, limit_l, baseline_boards)

    total_boards = max(
        (
            len(item["points"])
            for (_, pad_name), item in played["series"].items()
            if pad_name == BOARD_PAD
        ),
        default=0,
    )

    ng_outcomes = []
    l2_episodes = []
    hit_ids = set()
    for (model, pad_name), item in played["series"].items():
        points = item["points"]
        episodes = item["result"]["episodes"]
        ng_indices = [index for index, point in enumerate(points) if point["is_ng"]]

        for episode in episodes:
            if episode["l2_index"] is None:
                continue
            l2_episodes.append(episode)
            if pad_name == BOARD_PAD:
                continue
            for ng_index in ng_indices:
                if 0 <= ng_index - episode["l2_index"] <= HIT_HORIZON_BOARDS:
                    hit_ids.add(episode["warning_id"])
                    break

        if pad_name != BOARD_PAD and ng_indices:
            first_ng = ng_indices[0]
            preceding = [
                episode for episode in episodes
                if episode["l2_index"] is not None and episode["l2_index"] <= first_ng
            ]
            latest = preceding[-1] if preceding else None
            ng_outcomes.append({
                "model": model,
                "pad_name": pad_name,
                "first_ng_board_sn": points[first_ng]["board_sn"],
                "first_ng_time": points[first_ng]["time_text"],
                "lead_boards": first_ng - latest["l2_index"] if latest else None,
                "warned_within_horizon": bool(
                    latest and first_ng - latest["l2_index"] <= HIT_HORIZON_BOARDS
                ),
            })

    false_episodes = [
        episode for episode in l2_episodes
        if episode["warning_id"] not in hit_ids
    ]
    return {
        "params": {"lambda": lam, "L": limit_l, "baseline_boards": baseline_boards},
        "total_boards": total_boards,
        "l2_episode_count": len(l2_episodes),
        "hit_count": len(hit_ids),
        "false_count": len(false_episodes),
        "false_per_100_boards": round(len(false_episodes) / total_boards * 100, 2)
        if total_boards else None,
        "ng_outcomes": ng_outcomes,
        "l2_episodes": [
            {
                key: episode[key]
                for key in (
                    "warning_id", "model", "pad_name", "level", "start_index",
                    "start_board_sn", "start_time_text", "l2_index",
                    "l2_time_text", "l3_index", "boards_above", "end_index",
                    "metrics",
                )
            }
            for episode in l2_episodes
        ],
    }


def backtest_grid(
    rows: list[dict[str, Any]],
    lambdas: tuple[float, ...] = (0.1, 0.2, 0.3),
    limits: tuple[float, ...] = (2.5, 3.0, 3.5),
    baseline_boards: int = BASELINE_BOARDS,
) -> list[dict[str, Any]]:
    """Score every λ×L combination; the report feeds the design review."""
    return [
        backtest(rows, lam, limit_l, baseline_boards)
        for lam in lambdas
        for limit_l in limits
    ]
