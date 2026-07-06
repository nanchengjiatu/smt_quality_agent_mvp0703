"""Rule-based chat answers for one drilldown trigger.

All judgment content is read from the trigger's ``analysis_contract`` — the
single authoritative conclusion payload — plus the raw parameter evidence
(``parameter_check`` / ``param_events``). Answers are three-part
(结论 / 证据 / 下一步), and the next step is conditioned on the judged scope
category instead of a fixed canned sentence.
"""

from __future__ import annotations

import json
from typing import Any

from smt_quality_agent.llm import chat_completion, load_llm_config
from smt_quality_agent.ontology import MECHANISMS


QUICK_QUESTIONS = [
    "为什么判定为这个范围？",
    "现场先查什么？",
    "哪些证据支持首要根因？",
    "这是不是 SPI 假异常？",
    "解释参数对比结果",
]

_INTENTS = [
    ("spi_false_alarm", ("假异常", "SPI假", "误报")),
    ("scope", ("范围", "为什么判定", "为何判定")),
    ("parameters", ("参数",)),
    ("evidence", ("证据", "根因", "依据")),
]

# Scope-specific field guidance: what to physically check first for each
# drilldown category.
_SCOPE_NEXT_STEP = {
    "单Pad孤立异常": "锁定该 Pad：先看原始 SPI 图像，再检查对应钢网单孔的底部残锡/堵塞和开口状态。",
    "同元件多Pad异常": "按元件区域排查：检查该元件区域钢网底部污染、贴合状态和 PCB 局部支撑。",
    "局部区域": "按热力图圈定区域排查：检查区域内钢网底面、局部张力/变形和 PCB 支撑。",
    "整板同向": "升级到整板制程条件：复核锡膏状态、刮刀参数和钢网底面，不要只处理单个 Pad。",
    "疑似SPI假异常": "先复核 SPI 程序：调取原始图像核对测量框、Gerber 对位和上下限，确认实物前不动印刷参数。",
}
_DEFAULT_NEXT_STEP = "先复核触发 Pad 原始 SPI 图像和现场可见状态，再按证据扩大排查面。"

_SPI_FALSE_ALARM_NEXT_STEP = (
    "调取触发 Pad 原始 SPI 图像，检查测量框、Gerber 对位、上下限和主指标偏差；"
    "确认实物异常前不要只改印刷参数。"
)
_PARAMETER_NEXT_STEP = (
    "把参数变更时间点与首块 NG、恢复板和设备日志对齐；若没有参数证据，不要把参数调整当作首要根因。"
)


def classify_chat_intent(question: str) -> str:
    normalized = question or ""
    for intent, keywords in _INTENTS:
        if any(keyword in normalized for keyword in keywords):
            return intent
    return "actions"


def _join(items: list[str], fallback: str = "当前事件上下文没有足够证据。") -> str:
    clean = []
    seen = set()
    for item in items:
        if item and item not in seen:
            clean.append(item)
            seen.add(item)
    return "；".join(clean) if clean else fallback


def _contract(trigger: dict[str, Any]) -> dict[str, Any]:
    return trigger.get("analysis_contract") or {}


def _scope_next_step(trigger: dict[str, Any]) -> str:
    category = (_contract(trigger).get("scope") or {}).get("category") or ""
    return _SCOPE_NEXT_STEP.get(category, _DEFAULT_NEXT_STEP)


def _answer_scope(trigger: dict[str, Any]) -> dict[str, str]:
    contract = _contract(trigger)
    scope = contract.get("scope") or {}
    context = (contract.get("evidence") or {}).get("context") or {}
    category = scope.get("category") or "待判定"
    return {
        "conclusion": f"本事件当前判定范围是：{category}（置信度{scope.get('confidence', '中')}）。",
        "evidence": _join([
            scope.get("detail", ""),
            f"全量 SPI 窗口共 {context.get('total_rows', 0)} 行，NG {context.get('ng_rows', 0)} 行",
            f"同元件 NG {context.get('same_component_ng_rows', 0)} 行，同 Pad NG {context.get('same_pad_ng_rows', 0)} 行",
        ]),
        "next_step": _scope_next_step(trigger),
    }


def _answer_actions(trigger: dict[str, Any]) -> dict[str, str]:
    contract = _contract(trigger)
    disposition = contract.get("disposition") or {}
    recheck = contract.get("recheck") or {}
    actions = [disposition.get("primary_action", "")]
    actions.extend(
        item.get("action", "")
        for item in contract.get("root_cause_candidates") or []
    )
    actions.extend(recheck.get("criteria") or [])
    return {
        "conclusion": disposition.get("suggestion") or "先按首要根因执行现场确认。",
        "evidence": disposition.get("reason") or "该建议来自当前 Agent 的范围、趋势、恢复和排除检查结果。",
        "next_step": _join(actions[:5], _DEFAULT_NEXT_STEP),
    }


def _answer_evidence(trigger: dict[str, Any]) -> dict[str, str]:
    contract = _contract(trigger)
    candidates = contract.get("root_cause_candidates") or []
    primary = candidates[0] if candidates else {}
    summary_items = [
        f"{item.get('name')}：{item.get('value')}，{item.get('detail')}"
        for item in (contract.get("evidence") or {}).get("summary") or []
    ]
    check_items = [
        f"{check.get('name')}[{check.get('status')}]"
        for check in primary.get("auto_checks") or []
    ]
    evidence_parts = [primary.get("evidence", ""), *summary_items[:3]]
    if check_items:
        evidence_parts.append("已自动核验：" + "、".join(check_items))
    if primary.get("manual_checks"):
        evidence_parts.append("待现场确认：" + "、".join(primary["manual_checks"][:4]))
    trigger_info = contract.get("trigger") or {}
    conclusion = primary.get("cause") or trigger_info.get("conclusion", "当前事件已触发 Agent 根因分析。")
    if primary.get("mechanism"):
        conclusion = f"{conclusion}（机理：{primary['mechanism']}，部位：{primary.get('location') or '待定'}）"
    return {
        "conclusion": conclusion,
        "evidence": _join(evidence_parts),
        "next_step": primary.get("action") or _scope_next_step(trigger),
    }


def _answer_spi_false_alarm(trigger: dict[str, Any]) -> dict[str, str]:
    checks = (_contract(trigger).get("evidence") or {}).get("exclusion_checks") or []
    spi_check = next(
        (item for item in checks if item.get("name") == "SPI 假异常"), {},
    )
    status = spi_check.get("status") or "review"
    if status == "suspect":
        conclusion = "存在 SPI 假异常嫌疑，需要先复核程序、识别框或阈值。"
    elif status == "pass":
        conclusion = "当前没有明显 SPI 假异常信号。"
    else:
        conclusion = "当前需要人工复核 SPI 假异常风险。"
    return {
        "conclusion": conclusion,
        "evidence": spi_check.get("detail") or "排除检查没有返回详细说明。",
        "next_step": _SPI_FALSE_ALARM_NEXT_STEP,
    }


def _answer_parameters(trigger: dict[str, Any]) -> dict[str, str]:
    parameter_check = trigger.get("parameter_check") or {}
    param_events = trigger.get("param_events") or []
    drifted = parameter_check.get("drifted") or []
    event_text = [
        f"{item.get('parameter')} 从 {item.get('from')} 到 {item.get('to')}"
        for item in param_events[:4]
    ]
    drift_text = [
        f"{item.get('parameter')} 偏离基线"
        for item in drifted[:4]
    ]
    return {
        "conclusion": parameter_check.get("verdict") or "当前窗口内未形成明确参数异常结论。",
        "evidence": _join(
            [*drift_text, *event_text],
            "当前窗口内没有检测到明确程序设定变更或参数偏离。",
        ),
        "next_step": _PARAMETER_NEXT_STEP,
    }


def build_rule_chat_response(trigger: dict[str, Any], question: str) -> dict[str, Any]:
    normalized = (question or "").strip()
    if not normalized:
        normalized = "现场先查什么？"

    intent = classify_chat_intent(normalized)
    if intent == "scope":
        answer = _answer_scope(trigger)
    elif intent == "spi_false_alarm":
        answer = _answer_spi_false_alarm(trigger)
    elif intent == "evidence":
        answer = _answer_evidence(trigger)
    elif intent == "parameters":
        answer = _answer_parameters(trigger)
    else:
        answer = _answer_actions(trigger)

    return {
        "mode": "rule",
        "intent": intent,
        "trigger_id": trigger.get("trigger_id"),
        "question": normalized,
        "answer": answer,
    }


def build_llm_grounding(trigger: dict[str, Any]) -> str:
    """System prompt: role + answering rules + this trigger's authoritative
    contract + the mechanism catalog (single-source root-cause vocabulary).

    Large arrays (pad series, heatmap, full SPI window) stay out of the
    prompt; the analysis_contract already carries every judged conclusion.
    """
    mechanism_lines = []
    for mechanism_id, mechanism in MECHANISMS.items():
        props = mechanism.get("properties") or {}
        mechanism_lines.append(
            f"- {mechanism_id} {mechanism['label']}｜方向:{props.get('direction', '')}"
            f"｜签名:{props.get('signature_text', '')}｜动作:{props.get('action', '')}"
        )

    contract = _contract(trigger)
    parameter_check = trigger.get("parameter_check") or {}
    facts = {
        "analysis_contract": contract,
        "parameter_check_verdict": parameter_check.get("verdict", ""),
        "param_event_count": len(trigger.get("param_events") or []),
        "trigger_meta": {
            "trigger_id": trigger.get("trigger_id"),
            "model": trigger.get("model"),
            "pad_name": trigger.get("pad_name"),
            "main_defect_cn": trigger.get("main_defect_cn"),
            "start_time": trigger.get("start_time"),
            "end_time": trigger.get("end_time"),
            "trigger_board_count": trigger.get("trigger_board_count"),
        },
    }

    return (
        "你是 SMT 锡膏印刷 + SPI 质量分析助手，嵌在一个三板连发下钻工作台里。\n"
        "回答规范：\n"
        "1. 只依据下方资料回答；资料没有的信息要明说“数据未采集”或“证据不足”，不得编造。\n"
        "2. 用中文，按“结论/证据/下一步”三段作答，总长不超过 300 字。\n"
        "3. 提及根因时使用机理目录里的机理（引用 mech.* id），提及规则时引用 rule id。\n"
        "4. Comp_avdp/aadp/ahdp 是无符号偏差幅度，缺陷方向只在缺陷名里，不要把幅度当方向。\n"
        "\n=== 本次触发的分析契约（唯一权威结论）===\n"
        + json.dumps(facts, ensure_ascii=False)
        + "\n\n=== 失效机理目录（根因词表单源）===\n"
        + "\n".join(mechanism_lines)
    )


def build_chat_response(
    trigger: dict[str, Any],
    question: str,
    config: dict[str, Any] | None = None,
    post: Any = None,
) -> dict[str, Any]:
    """LLM first, rule-based fallback — the offline responder never goes away."""
    normalized = (question or "").strip() or "现场先查什么？"
    config = config if config is not None else load_llm_config()

    if config.get("enabled") and config.get("api_key"):
        try:
            kwargs = {"post": post} if post is not None else {}
            text = chat_completion(
                config, build_llm_grounding(trigger), normalized, **kwargs,
            )
            return {
                "mode": "llm",
                "provider": config["provider"],
                "model": config["model"],
                "trigger_id": trigger.get("trigger_id"),
                "question": normalized,
                "answer": {"text": text},
            }
        except Exception as exc:  # noqa: BLE001 - any failure falls back
            fallback = build_rule_chat_response(trigger, normalized)
            fallback["fallback_reason"] = f"LLM 调用失败，已回退离线规则问答（{exc}）"
            return fallback

    return build_rule_chat_response(trigger, normalized)
