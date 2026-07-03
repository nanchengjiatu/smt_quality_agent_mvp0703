"""SMT quality knowledge base: one rule registry shared by all agents.

Layering:

- ``ontology.py`` owns the vocabulary (concept IDs, labels, aliases).
- This module owns the executable rules. Every rule lives in the single
  ``RULES`` registry with one schema; analysis modules query rules through
  the lookup functions and never keep their own rule tables.
- Chat phrasing lives in ``drilldown_chat.py``.

Rule schema (uniform across all rule types):

    id                  stable rule ID ("rule.*")
    rule_type           scope_root_cause | trend_root_cause | evidence_root_cause
                        | exclusion_check | process_review | event_cause
                        | abnormal_cause
    condition           dict matching what the analysis provides
    mechanism           ontology FailureMechanism ID (stamped from
                        RULE_MECHANISMS; trend rules are exempt — 趋势形态
                        只是证据,不足以锁定机理)
    cause / action      the judgment; *_template variants take .format() args
    evidence            canned evidence text (optional; analyses may override)
    evidence_required   what must be collected before the cause can be固化
    confidence_base     mechanism prior, see CONFIDENCE_LADDER below
    applies_when / not_sufficient_when / first_check
                        hand-written review-card text; only present where an
                        expert actually wrote it — never generated boilerplate

v3 decision layer: ``DECISION_RULES`` (order 10–90) is the explicit诊断决策
ladder — what to suspect first under which observation — and ``diagnose()``
evaluates it over one drilldown observation. 最终置信度 = confidence_base ×
证据乘数(声明在决策规则里),证据强度(evidence_level)由最终置信度分档
推导(≥0.75 高 / ≥0.5 中 / <0.5 低),不再单独维护。

CONFIDENCE_LADDER — confidence_base is the mechanism prior used for ranking
(higher = shown first before evidence multipliers):

    0.85  direct time/image evidence (SPI false alarm, recovery after参数调整)
    0.80  fixed-cadence recurrence (maintenance cycle)
    0.75  measured parameter drift
    0.70  scope rules with tight局部证据 (单Pad / 同元件)
    0.65  scope rules over wider areas (局部区域 / 整板同向)
    0.60  trend-shape rules (渐变/突变归因)
    0.55  process review checklist items
    0.45  event/abnormal candidates without direct佐证
    0.35  fallback
"""

from __future__ import annotations

from typing import Any

from smt_quality_agent.ontology import (
    concept_label,
    evidence_availability,
    mechanism_by_id,
    ontology_ids_for,
)


RECHECK_CRITERIA = [
    "现场处置后至少连续复判 3 块生产板该 Pad 不再 NG。",
    "同时确认同元件其他 Pad 和整板同向 NG 率未继续升高。",
]

DEFAULT_RECHECK_METHOD = "处置后连续复判 3 块生产板，并确认同元件、同区域和整板同向 NG 率未继续升高。"

# Event analysis (param_correlation) only distinguishes "board-wide" from
# "not board-wide" — that is an explicit coarse grouping axis, not a scope
# vocabulary of its own.
EVENT_SCOPE_LOCAL = "局部焊盘"
EVENT_SCOPE_BOARD = "整板大面积"

_EVENT_EVIDENCE_REQUIRED = ["事件起止时间", "缺陷方向", "影响范围", "事件时段设备/人员记录"]
_ABNORMAL_EVIDENCE_REQUIRED = ["异常模式", "风险等级", "连续板序列", "现场确认结果"]


def event_scope_for_category(category: str) -> str:
    """Map a canonical scope category onto the coarse event grouping axis."""
    return EVENT_SCOPE_BOARD if category in {"整板同向", "整板趋势异常"} else EVENT_SCOPE_LOCAL


# --- Singleton rules referenced directly by the drilldown agent --------------

SPI_FALSE_ALARM_ROOT_CAUSE = {
    "id": "rule.spi_false_alarm_review",
    "rule_type": "exclusion_check",
    "condition": {"trigger": "主指标偏差不支撑 NG 标签"},
    "source": "knowledge_base",
    "cause": "SPI程序阈值或识别框异常",
    "action": "先用原始 SPI 图像复核测量框、Gerber 对位和该 Pad 上下限；确认实物异常前不要调整印刷参数。",
    "evidence_required": ["原始 SPI 图像", "测量框/Gerber 对位", "Pad 上下限", "实物复核结果"],
    "confidence_base": 0.85,
    "applies_when": "当 SPI 标签与主指标偏差、原始图像或实物复核不一致时适用。",
    "not_sufficient_when": "若原始图像和实物复核均支持真实异常，不应继续停留在 SPI 假异常判断。",
    "first_check": "先调取触发 Pad 原始 SPI 图像，核对测量框、Gerber 对位、上下限和实物状态。",
}

PARAMETER_RECOVERY_ROOT_CAUSE = {
    "id": "rule.parameter_recovery",
    "rule_type": "evidence_root_cause",
    "condition": {"trigger": "参数调整后异常随即恢复"},
    "source": "knowledge_base",
    "cause": "印刷程序设定不适配",
    "evidence_template": "{parameters} 调整后异常随即恢复，时间关系支持该判断。",
    "action_template": "固化 {parameters} 的有效设定，核对变更审批，并用同机种连续生产板确认参数窗口。",
    "evidence_required": ["参数调整记录", "调整前后 SPI 趋势", "恢复板序列", "变更审批记录"],
    "confidence_base": 0.85,
}

PERIODIC_ROOT_CAUSE = {
    "id": "rule.periodic_maintenance_cycle",
    "rule_type": "evidence_root_cause",
    "condition": {"trigger": "NG 连段按固定节拍复发"},
    "source": "knowledge_base",
    "cause": "钢网清洗或锡膏维护周期不匹配",
    "evidence_template": "历史 NG 连段约每 {gap:g} 块板重复，具有固定节拍。",
    "action": "核对自动擦网、加锡膏和搅拌记录；将维护周期提前一个周期并比较复发间隔。",
    "evidence_required": ["历史 NG 连段间隔", "自动擦网记录", "加锡膏/搅拌记录", "复发间隔对比"],
    "confidence_base": 0.8,
}

PARAMETER_DRIFT_ROOT_CAUSE = {
    "id": "rule.parameter_drift",
    "rule_type": "evidence_root_cause",
    "condition": {"trigger": "触发板参数实际-计划偏差超出基线"},
    "source": "knowledge_base",
    "cause": "印刷参数实际值偏离设定",
    "evidence_template": "触发板 {parameters} 的实际-计划偏差超出基线。",
    "action_template": "调取印刷机事件时段记录，校验 {parameters} 的设定值、实际值和机构状态；恢复基准后做首件确认。",
    "evidence_required": ["印刷机设定值", "印刷机实际值", "事件时段设备日志", "首块 NG 时间"],
    "confidence_base": 0.75,
}

FALLBACK_ROOT_CAUSE = {
    "id": "rule.fallback_local_printing_state",
    "rule_type": "evidence_root_cause",
    "condition": {"trigger": "证据不足以锁定单一物理原因"},
    "source": "fallback",
    "cause": "局部印刷状态异常",
    "evidence": "现有数据仅能确认连续异常，尚不足以锁定单一物理原因。",
    "action": "复核触发 Pad 的钢网孔、Pad 表面、原始 SPI 图像及事件时段设备记录。",
    "evidence_required": ["触发 Pad 原始 SPI 图像", "钢网孔状态", "Pad 表面状态", "设备事件记录"],
    "confidence_base": 0.35,
}


# --- Scope root-cause rules (drilldown categories) ---------------------------

_SCOPE_ROOT_CAUSE_RULES = [
    {
        "id": "rule.over_volume_single_pad",
        "condition": {"direction": "多锡", "scope": "单Pad孤立异常"},
        "cause": "钢网单孔底部残锡或开口异常",
        "evidence": "异常局限于单 Pad，同元件其他 Pad 与整板未同步扩散。",
        "action": "停机检查该 Pad 对应钢网孔底部残锡、孔壁和开口尺寸；清洁后印刷 3 块验证。",
        "confidence_base": 0.7,
        "evidence_required": ["同 Pad 连续 NG", "同元件其他 Pad 对比", "整板同向 NG 率", "原始 SPI 图像"],
        "applies_when": "连续同点或单 Pad 多锡，且同元件其他 Pad、整板同向趋势未同步异常。",
        "not_sufficient_when": "同元件多个 Pad 或同一区域同时多锡时，不应只按单孔处理。",
        "first_check": "先看原始 SPI 图像和钢网对应单孔底面，再检查开口尺寸/孔壁状态。",
    },
    {
        "id": "rule.insufficient_volume_single_pad",
        "condition": {"direction": "少锡", "scope": "单Pad孤立异常"},
        "cause": "钢网单孔堵塞或脱模不良",
        "evidence": "异常局限于单 Pad，符合单孔供锡不足特征。",
        "action": "显微检查并清洁对应钢网孔，确认开口堵塞、孔壁状态及 PCB 脱模条件。",
        "confidence_base": 0.7,
        "evidence_required": ["同 Pad 连续 NG", "同元件其他 Pad 对比", "整板同向 NG 率", "原始 SPI 图像"],
        "applies_when": "连续同点或单 Pad 少锡，且异常没有扩散到同元件或整板。",
        "not_sufficient_when": "若清洁后仍复发，需转向开口设计、支撑/贴合或脱模参数复核。",
        "first_check": "先显微检查对应钢网孔是否堵塞，再确认脱模速度、脱模距离和 PCB 支撑。",
    },
    {
        "id": "rule.over_volume_component_multi_pad",
        "condition": {"direction": "多锡", "scope": "同元件多Pad异常"},
        "cause": "元件区域钢网底部污染或贴合不良",
        "evidence": "同一元件多个 Pad 在触发板同步多锡。",
        "action": "检查元件区域钢网底部污染、PCB 与钢网贴合及局部变形，清洁后做首件确认。",
        "confidence_base": 0.7,
        "evidence_required": ["同元件多 Pad 同步异常", "元件区域热力图", "钢网贴合/支撑检查", "原始 SPI 图像"],
        "applies_when": "同一元件多个 Pad 同时多锡，且异常集中在该元件区域。",
        "not_sufficient_when": "如果周边元件或整板也同步异常，应升级为局部区域或整板同向排查。",
        "first_check": "先查该元件区域钢网底部污染、贴合状态和 PCB 局部支撑。",
    },
    {
        "id": "rule.insufficient_volume_component_multi_pad",
        "condition": {"direction": "少锡", "scope": "同元件多Pad异常"},
        "cause": "元件区域堵孔或局部支撑不足",
        "evidence": "同一元件多个 Pad 在触发板同步少锡。",
        "action": "检查该元件对应开口通透性、PCB 支撑和平整度，并确认脱模速度。",
        "confidence_base": 0.7,
        "evidence_required": ["同元件多 Pad 同步异常", "元件区域热力图", "钢网贴合/支撑检查", "原始 SPI 图像"],
        "applies_when": "同一元件多个 Pad 同时少锡，且异常分布与元件区域一致。",
        "not_sufficient_when": "如果整板体积整体偏低，应优先排查锡膏供给、印刷动作或大面积堵塞。",
        "first_check": "先查元件区域开口通透性、PCB 支撑/平整度和脱模动作。",
    },
    {
        "id": "rule.over_volume_local_area",
        "condition": {"direction": "多锡", "scope": "局部区域"},
        "cause": "局部钢网污染、变形或支撑异常",
        "action": "按热力图圈定区域，检查钢网底面、局部张力/变形和 PCB 支撑，再做区域首件确认。",
        "confidence_base": 0.65,
        "evidence_required": ["局部区域热力图", "区域内 Pad 分布", "钢网底面检查", "PCB 支撑检查"],
        "applies_when": "多个相邻元件或区域内 Pad 同向多锡，热力图呈局部聚集。",
        "not_sufficient_when": "如果区域边界不稳定或跨越大范围，需要先确认印刷偏移和整板制程漂移。",
        "first_check": "先用热力图圈定区域，再查钢网底面、局部支撑和钢网张力/变形。",
    },
    {
        "id": "rule.insufficient_volume_local_area",
        "condition": {"direction": "少锡", "scope": "局部区域"},
        "cause": "局部钢网堵塞、锡膏滚动或支撑异常",
        "action": "清洁异常区域并检查锡膏滚动、钢网贴合和 PCB 支撑，验证该区域全部 Pad。",
        "confidence_base": 0.65,
        "evidence_required": ["局部区域热力图", "区域内 Pad 分布", "钢网底面检查", "PCB 支撑检查"],
        "applies_when": "多个相邻元件或区域内 Pad 同向少锡，热力图呈局部聚集。",
        "not_sufficient_when": "若全板少锡或跨区域同步少锡，应优先排查供锡、刮刀行程和整板堵塞。",
        "first_check": "先清洁异常区域，再确认锡膏滚动、钢网贴合和 PCB 支撑。",
    },
    {
        "id": "rule.over_volume_board_same_direction",
        "condition": {"direction": "多锡", "scope": "整板同向"},
        "cause": "整板印刷条件或锡膏状态漂移",
        "action": "检查锡膏回温/搅拌/使用时长、刮刀压力速度和钢网底面；禁止只修改单 Pad 阈值。",
        "confidence_base": 0.65,
        "evidence_required": ["整板同向趋势", "刮刀压力/速度记录", "锡膏状态记录", "钢网清洁记录"],
        "applies_when": "整板多个区域同步多锡，且异常不局限于单元件或单区域。",
        "not_sufficient_when": "如果 SPI 原始图像不支持实物多锡，应先走 SPI 假异常排除项。",
        "first_check": "先查锡膏回温/搅拌/使用时长、刮刀压力/速度、钢网底面清洁状态。",
    },
    {
        "id": "rule.insufficient_volume_board_same_direction",
        "condition": {"direction": "少锡", "scope": "整板同向"},
        "cause": "锡膏供给中断或整板印刷动作异常",
        "action": "确认锡膏余量、刮刀行程、钢网大面积堵塞和脱模动作，并调取对应周期设备日志。",
        "confidence_base": 0.65,
        "evidence_required": ["整板同向趋势", "刮刀压力/速度记录", "锡膏状态记录", "钢网清洁记录"],
        "applies_when": "整板多个区域同步少锡，且多个元件/区域同时低于工艺窗口。",
        "not_sufficient_when": "若只有单区域少锡，不应直接归因于整板供锡或整板印刷动作。",
        "first_check": "先确认锡膏余量、刮刀行程完整性、钢网大面积堵塞和脱模动作。",
    },
]

# --- Trend-shape rules --------------------------------------------------------

_TREND_ROOT_CAUSE_RULES = [
    {
        "id": "rule.trend_gradual_degradation",
        "condition": {"trend_kind": "gradual"},
        "cause": "随生产累积的钢网或锡膏状态劣化",
        "action": "建立该 Pad 趋势预警，并对照擦网、加锡膏和停线时点确认哪项维护能使趋势复位。",
        "confidence_base": 0.6,
        "evidence_required": ["触发前 SPI 趋势", "擦网记录", "加锡膏记录", "停线/暂停记录"],
        "applies_when": "触发前指标以稳定斜率爬升且末段连续多板单调上升时适用。",
    },
    {
        "id": "rule.trend_step_change",
        "condition": {"trend_kind": "step"},
        "cause": "触发时点的离散制程变化",
        "action": "按触发时间核对换料、擦网、程序切换、设备报警和人工操作记录，逐项确认是否与首块 NG 对齐。",
        "confidence_base": 0.6,
        "evidence_required": ["首块 NG 时间", "换料记录", "程序切换记录", "设备报警/人工操作记录"],
        "applies_when": "触发前基线平稳、触发段指标突跳时适用。",
    },
]

# --- Process review checklist ---------------------------------------------------

_PROCESS_REVIEW_RULES = [
    {
        "id": "rule.review_print_alignment_offset",
        "condition": {"review_item": "print_alignment_offset"},
        "cause": "印刷偏移或 Fiducial 识别异常",
        "evidence": "SPI 位置偏移、同向面积/体积异常或多个 Pad 同步偏移时需要排除。",
        "action": "复核印刷机视觉识别、Fiducial、Gerber/钢网/PCB 对位和 SPI Offset 趋势。",
        "evidence_required": ["X/Y Offset 趋势", "Fiducial 识别记录", "Gerber/钢网/PCB 对位", "首件图像"],
        "confidence_base": 0.55,
    },
    {
        "id": "rule.review_gasketing_board_support",
        "condition": {"review_item": "gasketing_board_support"},
        "cause": "钢网与 PCB 贴合或板支撑不良",
        "evidence": "局部区域、多 Pad 或板翘相关异常需要排除贴合与支撑问题。",
        "action": "检查 PCB 支撑、夹持、板翘、钢网贴合和局部真空/顶针设置。",
        "evidence_required": ["板翘/支撑检查", "夹持状态", "钢网贴合检查", "局部 Pad 分布热力图"],
        "confidence_base": 0.55,
    },
    {
        "id": "rule.review_stencil_cleaning_process",
        "condition": {"review_item": "stencil_cleaning_process"},
        "cause": "钢网清洁周期或清洁效果异常",
        "evidence": "异常随生产累积、清洁后恢复或固定周期复发时需要优先复核。",
        "action": "核对擦网周期、擦网纸/溶剂/真空清洁状态，并比较清洁前后 SPI 分布。",
        "evidence_required": ["擦网周期", "擦网纸/溶剂状态", "清洁前后 SPI 趋势", "固定周期复发记录"],
        "confidence_base": 0.55,
    },
    {
        "id": "rule.review_stencil_aperture_design",
        "condition": {"review_item": "stencil_aperture_design"},
        "cause": "钢网开口设计或面积比不适配",
        "evidence": "长期稳定复发、清洁无效或细间距 Pad 反复少锡/多锡时需要评估开口设计。",
        "action": "复核开口尺寸、钢网厚度、面积比、开口壁状态和历史 ECN/钢网版本。",
        "evidence_required": ["开口尺寸", "钢网厚度", "面积比", "钢网版本/ECN", "清洁后复发情况"],
        "confidence_base": 0.55,
    },
]

# --- Event candidates (param_correlation time-clustered events) ----------------

_EVENT_CAUSE_RULES = [
    {
        "id": "rule.event_over_volume_local_paste_state",
        "condition": {"direction": "多锡", "event_scope": EVENT_SCOPE_LOCAL},
        "cause": "锡膏状态变化",
        "action": "确认事件时段是否刚添加/搅拌锡膏，检查黏度与回温记录",
    },
    {
        "id": "rule.event_over_volume_local_stencil_residue",
        "condition": {"direction": "多锡", "event_scope": EVENT_SCOPE_LOCAL},
        "cause": "钢网底部残锡",
        "action": "清洗钢网底部，复测下一块板确认是否消失",
    },
    {
        "id": "rule.event_over_volume_local_spi_threshold",
        "condition": {"direction": "多锡", "event_scope": EVENT_SCOPE_LOCAL},
        "cause": "SPI程序阈值偏紧",
        "action": "核对涉及焊盘的 SPI 阈值与标准值设置",
    },
    {
        "id": "rule.event_over_volume_board_paste_viscosity",
        "condition": {"direction": "多锡", "event_scope": EVENT_SCOPE_BOARD},
        "cause": "锡膏黏度异常",
        "action": "检查锡膏黏度、回温和使用时间",
    },
    {
        "id": "rule.event_over_volume_board_squeegee_pressure",
        "condition": {"direction": "多锡", "event_scope": EVENT_SCOPE_BOARD},
        "cause": "刮刀压力/角度异常",
        "action": "核对刮刀压力、角度和速度的设定与实际记录，现场确认后再调整压力方向",
    },
    {
        "id": "rule.event_over_volume_board_spi_threshold",
        "condition": {"direction": "多锡", "event_scope": EVENT_SCOPE_BOARD},
        "cause": "SPI程序阈值问题",
        "action": "检查 SPI 程序阈值和标准值设置",
    },
    {
        "id": "rule.event_insufficient_volume_local_stencil_blockage",
        "condition": {"direction": "少锡", "event_scope": EVENT_SCOPE_LOCAL},
        "cause": "钢网局部堵孔",
        "action": "清洗钢网并检查对应 Pad 开口",
    },
    {
        "id": "rule.event_insufficient_volume_local_paste_dry",
        "condition": {"direction": "少锡", "event_scope": EVENT_SCOPE_LOCAL},
        "cause": "锡膏变干",
        "action": "检查锡膏回温、搅拌、使用时间",
    },
    {
        "id": "rule.event_insufficient_volume_local_support",
        "condition": {"direction": "少锡", "event_scope": EVENT_SCOPE_LOCAL},
        "cause": "局部支撑不良",
        "action": "检查该区域 PCB 支撑和平整度",
    },
    {
        "id": "rule.event_insufficient_volume_board_supply_interruption",
        "condition": {"direction": "少锡", "event_scope": EVENT_SCOPE_BOARD},
        "cause": "印刷漏刷或锡膏供给中断",
        "action": "确认该板是否漏印、钢网上锡膏余量是否充足",
    },
    {
        "id": "rule.event_insufficient_volume_board_stencil_blockage",
        "condition": {"direction": "少锡", "event_scope": EVENT_SCOPE_BOARD},
        "cause": "钢网大面积堵塞",
        "action": "立即清洗钢网并检查贴合状态",
    },
    {
        "id": "rule.event_insufficient_volume_board_print_action",
        "condition": {"direction": "少锡", "event_scope": EVENT_SCOPE_BOARD},
        "cause": "单次印刷动作异常",
        "action": "调取印刷机该周期日志，确认刮刀行程是否完整",
    },
]

# --- Realtime abnormal candidates (rules_engine patterns) ----------------------

_ABNORMAL_CAUSE_RULES = [
    {
        "id": "rule.abnormal_insufficient_repeat_stencil_blockage",
        "condition": {"defect_type": "少锡", "abnormal_pattern": "同点多板异常", "risk_level": "高"},
        "cause": "钢网堵孔",
        "action": "立即清洗钢网，并检查对应 Pad 开口是否堵塞",
    },
    {
        "id": "rule.abnormal_insufficient_repeat_paste_dry",
        "condition": {"defect_type": "少锡", "abnormal_pattern": "同点多板异常", "risk_level": "高"},
        "cause": "锡膏变干",
        "action": "检查锡膏回温、搅拌、使用时间和黏度状态",
    },
    {
        "id": "rule.abnormal_insufficient_repeat_support",
        "condition": {"defect_type": "少锡", "abnormal_pattern": "同点多板异常", "risk_level": "高"},
        "cause": "局部支撑不良",
        "action": "检查该区域 PCB 支撑和平整度",
    },
    {
        "id": "rule.abnormal_insufficient_component_support",
        "condition": {"defect_type": "少锡", "abnormal_pattern": "同元件多Pad异常", "risk_level": "中"},
        "cause": "PCB支撑不良",
        "action": "检查元件区域支撑和板面平整度",
    },
    {
        "id": "rule.abnormal_insufficient_component_stencil_blockage",
        "condition": {"defect_type": "少锡", "abnormal_pattern": "同元件多Pad异常", "risk_level": "中"},
        "cause": "钢网局部堵塞",
        "action": "检查该元件对应钢网区域是否堵孔或污染",
    },
    {
        "id": "rule.abnormal_insufficient_component_contact",
        "condition": {"defect_type": "少锡", "abnormal_pattern": "同元件多Pad异常", "risk_level": "中"},
        "cause": "印刷接触不良",
        "action": "检查钢网与 PCB 贴合状态",
    },
    {
        "id": "rule.abnormal_insufficient_board_squeegee_pressure",
        "condition": {"defect_type": "少锡", "abnormal_pattern": "整板趋势异常", "risk_level": "中"},
        "cause": "刮刀压力/印刷接触异常",
        "action": "核对刮刀压力、角度、钢网贴合和支撑状态，现场确认后再调整压力方向",
    },
    {
        "id": "rule.abnormal_insufficient_board_print_speed",
        "condition": {"defect_type": "少锡", "abnormal_pattern": "整板趋势异常", "risk_level": "中"},
        "cause": "印刷速度过快",
        "action": "检查并适当降低印刷速度",
    },
    {
        "id": "rule.abnormal_insufficient_board_paste_state",
        "condition": {"defect_type": "少锡", "abnormal_pattern": "整板趋势异常", "risk_level": "中"},
        "cause": "锡膏状态异常",
        "action": "检查锡膏回温、搅拌、使用时间和环境条件",
    },
    {
        "id": "rule.abnormal_insufficient_board_stencil_cleaning",
        "condition": {"defect_type": "少锡", "abnormal_pattern": "整板趋势异常", "risk_level": "中"},
        "cause": "钢网清洗不足",
        "action": "检查钢网清洗频率，必要时立即清洗",
    },
    {
        "id": "rule.abnormal_insufficient_board_squeegee_pressure_high",
        "condition": {"defect_type": "少锡", "abnormal_pattern": "整板趋势异常", "risk_level": "高"},
        "cause": "刮刀压力/印刷接触异常",
        "action": "立即核对刮刀压力、角度、钢网贴合和支撑状态，现场确认后再调整压力方向",
    },
    {
        "id": "rule.abnormal_insufficient_board_paste_state_high",
        "condition": {"defect_type": "少锡", "abnormal_pattern": "整板趋势异常", "risk_level": "高"},
        "cause": "锡膏状态异常",
        "action": "检查锡膏回温、搅拌、使用时间和环境条件",
    },
    {
        "id": "rule.abnormal_over_repeat_stencil_residue",
        "condition": {"defect_type": "多锡", "abnormal_pattern": "同点多板异常", "risk_level": "高"},
        "cause": "钢网底部残锡",
        "action": "清洗钢网底部，并复测下一块板",
    },
    {
        "id": "rule.abnormal_over_repeat_aperture",
        "condition": {"defect_type": "多锡", "abnormal_pattern": "同点多板异常", "risk_level": "高"},
        "cause": "钢网开口异常",
        "action": "检查对应 Pad 钢网开口尺寸和状态",
    },
    {
        "id": "rule.abnormal_over_repeat_release",
        "condition": {"defect_type": "多锡", "abnormal_pattern": "同点多板异常", "risk_level": "高"},
        "cause": "脱模异常",
        "action": "检查脱模速度、脱模距离和 PCB 支撑",
    },
    {
        "id": "rule.abnormal_over_component_stencil_residue",
        "condition": {"defect_type": "多锡", "abnormal_pattern": "同元件多Pad异常", "risk_level": "中"},
        "cause": "钢网底部污染",
        "action": "清洗该元件区域钢网底部",
    },
    {
        "id": "rule.abnormal_over_component_slump",
        "condition": {"defect_type": "多锡", "abnormal_pattern": "同元件多Pad异常", "risk_level": "中"},
        "cause": "局部塌边",
        "action": "检查锡膏状态、脱模条件和支撑状态",
    },
    {
        "id": "rule.abnormal_over_component_release",
        "condition": {"defect_type": "多锡", "abnormal_pattern": "同元件多Pad异常", "risk_level": "中"},
        "cause": "脱模异常",
        "action": "检查脱模参数和 PCB 支撑",
    },
    {
        "id": "rule.abnormal_over_board_squeegee_pressure",
        "condition": {"defect_type": "多锡", "abnormal_pattern": "整板趋势异常", "risk_level": "中"},
        "cause": "刮刀压力/角度异常",
        "action": "核对刮刀压力、角度和速度的设定与实际记录，现场确认后再调整压力方向",
    },
    {
        "id": "rule.abnormal_over_board_print_speed",
        "condition": {"defect_type": "多锡", "abnormal_pattern": "整板趋势异常", "risk_level": "中"},
        "cause": "印刷速度过慢",
        "action": "检查并优化印刷速度",
    },
    {
        "id": "rule.abnormal_over_board_paste_viscosity",
        "condition": {"defect_type": "多锡", "abnormal_pattern": "整板趋势异常", "risk_level": "中"},
        "cause": "锡膏黏度异常",
        "action": "检查锡膏黏度、回温和使用时间",
    },
    {
        "id": "rule.abnormal_over_board_spi_threshold",
        "condition": {"defect_type": "多锡", "abnormal_pattern": "整板趋势异常", "risk_level": "中"},
        "cause": "SPI程序阈值问题",
        "action": "检查 SPI 程序阈值和标准值设置",
    },
    {
        "id": "rule.abnormal_over_board_spi_threshold_high",
        "condition": {"defect_type": "多锡", "abnormal_pattern": "整板趋势异常", "risk_level": "高"},
        "cause": "SPI程序阈值问题",
        "action": "检查 SPI 程序阈值和标准值设置",
    },
    {
        "id": "rule.abnormal_over_board_squeegee_pressure_high",
        "condition": {"defect_type": "多锡", "abnormal_pattern": "整板趋势异常", "risk_level": "高"},
        "cause": "刮刀压力/角度异常",
        "action": "立即核对刮刀压力、角度和速度的设定与实际记录，现场确认后再调整压力方向",
    },
]

_ABNORMAL_FALLBACK_RULE = {
    "id": "rule.abnormal_observe_next_board",
    "rule_type": "abnormal_cause",
    "condition": {"defect_type": "*", "abnormal_pattern": "*", "risk_level": "*"},
    "source": "fallback",
    "cause": "继续观察",
    "action": "复测下一块板，确认是否重复发生",
    "evidence_required": _ABNORMAL_EVIDENCE_REQUIRED,
    "confidence_base": 0.35,
}


def _finalize(rules: list[dict[str, Any]], rule_type: str, source: str,
              defaults: dict[str, Any]) -> list[dict[str, Any]]:
    """Stamp shared rule_type/source/defaults onto one rule group."""
    finalized = []
    for rule in rules:
        finalized.append({
            "rule_type": rule_type,
            "source": source,
            **defaults,
            **rule,
        })
    return finalized


RULES: list[dict[str, Any]] = [
    SPI_FALSE_ALARM_ROOT_CAUSE,
    PARAMETER_RECOVERY_ROOT_CAUSE,
    PERIODIC_ROOT_CAUSE,
    PARAMETER_DRIFT_ROOT_CAUSE,
    FALLBACK_ROOT_CAUSE,
    *_finalize(_SCOPE_ROOT_CAUSE_RULES, "scope_root_cause", "knowledge_base",
               { "recheck_method": DEFAULT_RECHECK_METHOD}),
    *_finalize(_TREND_ROOT_CAUSE_RULES, "trend_root_cause", "knowledge_base", {}),
    *_finalize(_PROCESS_REVIEW_RULES, "process_review", "knowledge_base.process_review", {}),
    *_finalize(_EVENT_CAUSE_RULES, "event_cause", "knowledge_base.event_rules",
               {"confidence_base": 0.45,
                "evidence_required": _EVENT_EVIDENCE_REQUIRED}),
    *_finalize(_ABNORMAL_CAUSE_RULES, "abnormal_cause", "knowledge_base.abnormal_rules",
               {"confidence_base": 0.45,
                "evidence_required": _ABNORMAL_EVIDENCE_REQUIRED,
                "recheck_method": DEFAULT_RECHECK_METHOD}),
    _ABNORMAL_FALLBACK_RULE,
]

# --- Mechanism binding (机理层绑定,集中映射便于评审) ---------------------------
# 每条规则指向 ontology 里最接近的主机理;趋势形态规则不绑定(趋势本身不足以
# 锁定机理,只作为证据输入决策层)。
RULE_MECHANISMS = {
    "rule.spi_false_alarm_review": "mech.spi_false_call",
    "rule.parameter_recovery": "mech.parameter_mismatch",
    "rule.periodic_maintenance_cycle": "mech.cleaning_cycle_mismatch",
    "rule.parameter_drift": "mech.parameter_mismatch",
    "rule.fallback_local_printing_state": "mech.undetermined",
    # scope 规则
    "rule.over_volume_single_pad": "mech.understencil_residue",
    "rule.insufficient_volume_single_pad": "mech.aperture_clogging",
    "rule.over_volume_component_multi_pad": "mech.understencil_residue",
    "rule.insufficient_volume_component_multi_pad": "mech.aperture_clogging",
    "rule.over_volume_local_area": "mech.poor_gasketing",
    "rule.insufficient_volume_local_area": "mech.aperture_clogging",
    "rule.over_volume_board_same_direction": "mech.paste_rheology_drift",
    "rule.insufficient_volume_board_same_direction": "mech.supply_interruption",
    # 工艺复核项
    "rule.review_print_alignment_offset": "mech.alignment_offset",
    "rule.review_gasketing_board_support": "mech.poor_gasketing",
    "rule.review_stencil_cleaning_process": "mech.cleaning_cycle_mismatch",
    "rule.review_stencil_aperture_design": "mech.poor_release",
    # 事件候选
    "rule.event_over_volume_local_paste_state": "mech.paste_rheology_drift",
    "rule.event_over_volume_local_stencil_residue": "mech.understencil_residue",
    "rule.event_over_volume_local_spi_threshold": "mech.spi_false_call",
    "rule.event_over_volume_board_paste_viscosity": "mech.paste_rheology_drift",
    "rule.event_over_volume_board_squeegee_pressure": "mech.parameter_mismatch",
    "rule.event_over_volume_board_spi_threshold": "mech.spi_false_call",
    "rule.event_insufficient_volume_local_stencil_blockage": "mech.aperture_clogging",
    "rule.event_insufficient_volume_local_paste_dry": "mech.paste_rheology_drift",
    "rule.event_insufficient_volume_local_support": "mech.poor_gasketing",
    "rule.event_insufficient_volume_board_supply_interruption": "mech.supply_interruption",
    "rule.event_insufficient_volume_board_stencil_blockage": "mech.aperture_clogging",
    "rule.event_insufficient_volume_board_print_action": "mech.supply_interruption",
    # 实时候选
    "rule.abnormal_insufficient_repeat_stencil_blockage": "mech.aperture_clogging",
    "rule.abnormal_insufficient_repeat_paste_dry": "mech.paste_rheology_drift",
    "rule.abnormal_insufficient_repeat_support": "mech.poor_gasketing",
    "rule.abnormal_insufficient_component_support": "mech.poor_gasketing",
    "rule.abnormal_insufficient_component_stencil_blockage": "mech.aperture_clogging",
    "rule.abnormal_insufficient_component_contact": "mech.poor_gasketing",
    "rule.abnormal_insufficient_board_squeegee_pressure": "mech.parameter_mismatch",
    "rule.abnormal_insufficient_board_print_speed": "mech.parameter_mismatch",
    "rule.abnormal_insufficient_board_paste_state": "mech.paste_rheology_drift",
    "rule.abnormal_insufficient_board_stencil_cleaning": "mech.cleaning_cycle_mismatch",
    "rule.abnormal_insufficient_board_squeegee_pressure_high": "mech.parameter_mismatch",
    "rule.abnormal_insufficient_board_paste_state_high": "mech.paste_rheology_drift",
    "rule.abnormal_over_repeat_stencil_residue": "mech.understencil_residue",
    "rule.abnormal_over_repeat_aperture": "mech.poor_release",
    "rule.abnormal_over_repeat_release": "mech.poor_release",
    "rule.abnormal_over_component_stencil_residue": "mech.understencil_residue",
    "rule.abnormal_over_component_slump": "mech.slump",
    "rule.abnormal_over_component_release": "mech.poor_release",
    "rule.abnormal_over_board_squeegee_pressure": "mech.parameter_mismatch",
    "rule.abnormal_over_board_print_speed": "mech.parameter_mismatch",
    "rule.abnormal_over_board_paste_viscosity": "mech.paste_rheology_drift",
    "rule.abnormal_over_board_spi_threshold": "mech.spi_false_call",
    "rule.abnormal_over_board_spi_threshold_high": "mech.spi_false_call",
    "rule.abnormal_over_board_squeegee_pressure_high": "mech.parameter_mismatch",
    "rule.abnormal_observe_next_board": "mech.undetermined",
}

# 明确不绑定机理的规则(趋势形态只是证据,不足以锁定机理)。
RULES_WITHOUT_MECHANISM = {"rule.trend_gradual_degradation", "rule.trend_step_change"}

for _rule in RULES:
    _rule["mechanism"] = RULE_MECHANISMS.get(_rule["id"])

_RULES_BY_ID = {rule["id"]: rule for rule in RULES}
assert len(_RULES_BY_ID) == len(RULES), "duplicate rule id in RULES registry"
assert all(
    rule["mechanism"] or rule["id"] in RULES_WITHOUT_MECHANISM for rule in RULES
), "every rule needs a mechanism or an explicit exemption"


def _index_by(rule_type: str, *keys: str) -> dict[tuple, list[dict[str, Any]]]:
    index: dict[tuple, list[dict[str, Any]]] = {}
    for rule in RULES:
        if rule["rule_type"] != rule_type:
            continue
        key = tuple(rule["condition"].get(field) for field in keys)
        index.setdefault(key, []).append(rule)
    return index

_SCOPE_INDEX = _index_by("scope_root_cause", "direction", "scope")
_TREND_INDEX = _index_by("trend_root_cause", "trend_kind")
_EVENT_INDEX = _index_by("event_cause", "direction", "event_scope")
_ABNORMAL_INDEX = _index_by("abnormal_cause", "defect_type", "abnormal_pattern", "risk_level")


DISPOSITION_RULES = [
    {
        "id": "data_continuity_review",
        "priority": "P3",
        "disposition": "先复核数据连续性",
        "reason": "触发段存在数据连续性疑点，根因判断只能作为预判。",
    },
    {
        "id": "spi_false_alarm_review",
        "priority": "P2",
        "disposition": "先复核 SPI 程序/图像",
        "reason": "主指标偏差不支撑当前 NG 标签，需先排除 SPI 假异常。",
    },
    {
        "id": "widened_scope",
        "priority": "P1",
        "disposition": "立即现场排查并跟踪复判",
        "reason": "异常已从单 Pad 扩散到更大范围，存在批量风险。",
    },
    {
        "id": "not_recovered",
        "priority": "P1",
        "disposition": "立即处置并暂停放行同类风险板",
        "reason": "触发后未恢复，连续生产会扩大同点不良风险。",
    },
    {
        "id": "high_confidence",
        "priority": "P2",
        "disposition": "按首要根因执行现场确认",
        "reason": "规则证据链较强，可直接按首要根因验证。",
    },
    {
        "id": "single_point_fast_check",
        "priority": "P3",
        "disposition": "按单点异常做快速确认",
        "reason": "当前证据更像局部单点问题，先做低成本复核和短程复判。",
    },
]


# --- Lookup API ----------------------------------------------------------------

def rule_by_id(rule_id: str) -> dict[str, Any] | None:
    return _RULES_BY_ID.get(rule_id)


def confidence_level(confidence: float) -> str:
    """高/中/低 by final confidence — the single strength scale (v2 had a
    parallel hand-set evidence_level; it is now derived, never stored)."""
    if confidence >= 0.75:
        return "高"
    if confidence >= 0.5:
        return "中"
    return "低"


# Auto-check evaluators: how each auto evidence type is verified against one
# drilldown observation. Keys are ontology evidence IDs; each returns
# (passed, detail). Evidence whose availability is planned/not_collected never
# reaches these — it is reported as such instead of silently skipped.
_AUTO_CHECK_EVALUATORS = {
    "evidence.trend_slope": lambda obs: (
        obs.get("trend_kind") == "gradual", obs.get("trend_detail", ""),
    ),
    "evidence.parameter_drift": lambda obs: (
        bool(obs.get("drifted_parameters")),
        "偏离参数：" + "、".join(obs.get("drifted_parameters", [])) if obs.get("drifted_parameters") else "窗口内未检出参数偏离基线。",
    ),
    "evidence.recovery": lambda obs: (
        obs.get("recovery_kind") == "recovered" and bool(obs.get("recovery_parameters")),
        obs.get("recovery_detail", ""),
    ),
    "evidence.periodic_recurrence": lambda obs: (
        bool(obs.get("periodic")), obs.get("periodicity_detail", ""),
    ),
    "evidence.spi_metric_mismatch": lambda obs: (
        obs.get("validity") == "validity.spi_suspect", obs.get("spi_detail", ""),
    ),
}

_AVAILABILITY_STATUS = {
    "not_collected": ("数据未采集", "该字段在当前数据导出中未采集，已列入数据侧需求。"),
    "planned": ("待实现", "对应自动核验在第二阶段实现。"),
}


def evaluate_auto_checks(
    evidence_ids: list[str],
    observation: dict[str, Any],
) -> list[dict[str, str]]:
    """Evaluate one mechanism's auto checks against a drilldown observation.
    Statuses: 核验通过 / 未见 / 数据未采集 / 待实现."""
    results = []
    for evidence_id in evidence_ids:
        availability = evidence_availability(evidence_id)
        if availability in _AVAILABILITY_STATUS:
            status, detail = _AVAILABILITY_STATUS[availability]
        else:
            evaluator = _AUTO_CHECK_EVALUATORS.get(evidence_id)
            if evaluator is None:
                continue
            passed, detail = evaluator(observation)
            status = "核验通过" if passed else "未见"
        results.append({
            "evidence_id": evidence_id,
            "name": concept_label(evidence_id),
            "status": status,
            "detail": detail,
        })
    return results


def root_cause_candidate_from_rule(
    rule: dict[str, Any],
    evidence: str | None = None,
    multiplier: float = 1.0,
    observation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Uniform root-cause candidate payload consumed by conclusions and UI.

    最终置信度 = 机理先验(confidence_base) × 证据乘数;evidence_level 由
    最终置信度分档推导。带 observation 时评估机理的自动核验证据。"""
    confidence = round(rule.get("confidence_base", 0.5) * multiplier, 3)
    mechanism = mechanism_by_id(rule.get("mechanism") or "") or {}
    props = mechanism.get("properties") or {}
    manual_checks = [concept_label(item) for item in props.get("manual_checks", [])]
    candidate = {
        "rule_id": rule["id"],
        "rule_source": rule["source"],
        "cause": rule["cause"],
        "evidence": evidence or rule.get("evidence", ""),
        "action": rule.get("action") or rule.get("action_template", ""),
        "confidence_base": rule.get("confidence_base", 0.5),
        "confidence": confidence,
        "evidence_level": confidence_level(confidence),
        "mechanism_id": rule.get("mechanism"),
        "mechanism": mechanism.get("label"),
        "location": concept_label(props["element"]) if props.get("element") else None,
        "onset": props.get("onset"),
        "early_warning": props.get("early_warning") or None,
        "manual_checks": manual_checks or rule.get("evidence_required", []),
    }
    if observation is not None:
        candidate["auto_checks"] = evaluate_auto_checks(
            props.get("auto_checks", []), observation,
        )
    return candidate


def scope_root_cause_candidate(
    direction: str,
    category: str,
    detail: str,
    observation: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    rules = _SCOPE_INDEX.get((direction, category))
    if not rules:
        return None
    rule = rules[0]
    return root_cause_candidate_from_rule(
        rule, rule.get("evidence") or detail, observation=observation,
    )


def trend_root_cause_candidate(
    kind: str,
    detail: str,
    observation: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    rules = _TREND_INDEX.get((kind,))
    if not rules:
        return None
    return root_cause_candidate_from_rule(rules[0], detail, observation=observation)


def event_cause_candidates(
    direction: str,
    event_scope: str,
    observation: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    return [
        root_cause_candidate_from_rule(
            rule, "基于事件缺陷方向与影响范围的规则候选，当前无直接佐证。",
            observation=observation,
        )
        for rule in _EVENT_INDEX.get((direction, event_scope), [])
    ]


def abnormal_cause_candidates(defect_type: str, pattern: str, risk_level: str) -> list[dict[str, Any]]:
    rules = (
        _ABNORMAL_INDEX.get((defect_type, pattern, risk_level))
        or _ABNORMAL_INDEX.get((defect_type, pattern, "中"))
        or [_ABNORMAL_FALLBACK_RULE]
    )
    return [
        root_cause_candidate_from_rule(rule, "基于实时异常类型、模式和风险等级的规则候选。")
        for rule in rules
    ]


# --- Decision layer ------------------------------------------------------------
# v2 时代 build_conclusion 里的隐式候选收集顺序,显式化为可评审的决策规则组。
# order 越小越先求值;门槛类(有效性)在前,直接时序证据次之,先验类靠后。
# 乘数(multiplier)是置信度模型的证据修正项,声明在规则里而不是散在代码中。

def _decide_spi_gate(obs: dict[str, Any]) -> list[dict[str, Any]]:
    return [root_cause_candidate_from_rule(
        SPI_FALSE_ALARM_ROOT_CAUSE, obs.get("spi_detail", ""), observation=obs,
    )]


def _decide_parameter_drift(obs: dict[str, Any]) -> list[dict[str, Any]]:
    names = "、".join(obs["drifted_parameters"][:3])
    candidate = root_cause_candidate_from_rule(
        PARAMETER_DRIFT_ROOT_CAUSE,
        PARAMETER_DRIFT_ROOT_CAUSE["evidence_template"].format(parameters=names),
        multiplier=0.8 if obs.get("cross_model_baseline") else 1.0,
        observation=obs,
    )
    candidate["action"] = PARAMETER_DRIFT_ROOT_CAUSE["action_template"].format(parameters=names)
    return [candidate]


def _decide_parameter_recovery(obs: dict[str, Any]) -> list[dict[str, Any]]:
    names = "、".join(sorted(set(obs["recovery_parameters"])))
    candidate = root_cause_candidate_from_rule(
        PARAMETER_RECOVERY_ROOT_CAUSE,
        PARAMETER_RECOVERY_ROOT_CAUSE["evidence_template"].format(parameters=names),
        observation=obs,
    )
    candidate["action"] = PARAMETER_RECOVERY_ROOT_CAUSE["action_template"].format(parameters=names)
    return [candidate]


def _decide_periodic(obs: dict[str, Any]) -> list[dict[str, Any]]:
    gap = obs.get("periodic_gap")
    evidence = (
        PERIODIC_ROOT_CAUSE["evidence_template"].format(gap=gap)
        if gap else obs.get("periodicity_detail", "")
    )
    return [root_cause_candidate_from_rule(PERIODIC_ROOT_CAUSE, evidence, observation=obs)]


def _decide_scope_prior(obs: dict[str, Any]) -> list[dict[str, Any]]:
    candidate = scope_root_cause_candidate(
        obs["direction"], obs["category"], obs.get("scope_detail", ""), observation=obs,
    )
    return [candidate] if candidate else []


def _decide_trend_shape(obs: dict[str, Any]) -> list[dict[str, Any]]:
    candidate = trend_root_cause_candidate(
        obs.get("trend_kind", ""), obs.get("trend_detail", ""), observation=obs,
    )
    return [candidate] if candidate else []


def _decide_event_candidates(obs: dict[str, Any]) -> list[dict[str, Any]]:
    return event_cause_candidates(
        obs["direction"], event_scope_for_category(obs["category"]), observation=obs,
    )


DECISION_RULES = [
    {
        "id": "decide.spi_false_alarm_gate",
        "order": 10,
        "when": "数据有效性 = 疑似SPI误判",
        "nominates": "rule.spi_false_alarm_review（先排除假异常再谈物理机理）",
        "applies": lambda obs: obs.get("validity") == "validity.spi_suspect",
        "build": _decide_spi_gate,
    },
    {
        "id": "decide.parameter_recovery",
        "order": 20,
        "when": "触发后恢复且恢复前有程序设定变更",
        "nominates": "rule.parameter_recovery（时序证据直接支持）",
        "applies": lambda obs: (
            obs.get("recovery_kind") == "recovered" and bool(obs.get("recovery_parameters"))
        ),
        "build": _decide_parameter_recovery,
    },
    {
        "id": "decide.parameter_drift",
        "order": 21,
        "when": "触发板参数实际-计划偏差超出基线",
        "nominates": "rule.parameter_drift（跨机种基线时置信 ×0.8）",
        "applies": lambda obs: bool(obs.get("drifted_parameters")),
        "build": _decide_parameter_drift,
    },
    {
        "id": "decide.periodic_recurrence",
        "order": 30,
        "when": "NG 连段呈固定节拍复发",
        "nominates": "rule.periodic_maintenance_cycle",
        "applies": lambda obs: bool(obs.get("periodic")),
        "build": _decide_periodic,
    },
    {
        "id": "decide.scope_prior",
        "order": 50,
        "when": "空间范围与缺陷方向组合(疑似SPI误判时跳过)",
        "nominates": "scope_root_cause 规则组",
        "applies": lambda obs: obs.get("validity") != "validity.spi_suspect",
        "build": _decide_scope_prior,
    },
    {
        "id": "decide.trend_shape",
        "order": 60,
        "when": "趋势形态可判(突变且已有参数漂移证据时跳过,避免重复归因)",
        "nominates": "trend_root_cause 规则组(不绑定机理,仅形态归因)",
        "applies": lambda obs: (
            obs.get("trend_kind") in {"gradual", "step"}
            and not (obs.get("trend_kind") == "step" and obs.get("drifted_parameters"))
        ),
        "build": _decide_trend_shape,
    },
    {
        "id": "decide.event_candidates",
        "order": 70,
        "when": "按方向与事件分组轴补充候选(无直接佐证)",
        "nominates": "event_cause 规则组",
        "applies": lambda obs: True,
        "build": _decide_event_candidates,
    },
]


def diagnose(observation: dict[str, Any]) -> dict[str, Any]:
    """Run the decision-rule ladder over one drilldown observation.

    Returns the ranked root-cause assessment: candidates sorted by final
    confidence (stable within ties), deduplicated by cause, capped at 3,
    with ontology IDs attached and an overall confidence grade."""
    candidates: list[dict[str, Any]] = []
    for decision_rule in sorted(DECISION_RULES, key=lambda item: item["order"]):
        if decision_rule["applies"](observation):
            for candidate in decision_rule["build"](observation):
                candidate["decided_by"] = decision_rule["id"]
                candidates.append(candidate)

    if not candidates:
        fallback = root_cause_candidate_from_rule(FALLBACK_ROOT_CAUSE, observation=observation)
        fallback["decided_by"] = "decide.fallback"
        candidates.append(fallback)

    candidates.sort(key=lambda item: -item["confidence"])
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
            direction=observation.get("direction"),
            scope=observation.get("category"),
            cause=item["cause"],
        )

    confidence = "中"
    if observation.get("data_status") != "pass":
        confidence = "低"
    elif assessments[0]["evidence_level"] == "高":
        confidence = "高"

    return {
        "category": observation.get("category"),
        "direction": observation.get("direction"),
        "confidence": confidence,
        "root_cause_assessment": assessments,
        "recheck_plan": RECHECK_CRITERIA,
    }


def disposition_for(
    data_status: str | None,
    spi_status: str | None,
    category: str,
    recovery_kind: str,
    confidence: str,
) -> dict[str, str]:
    if data_status != "pass":
        return DISPOSITION_RULES[0]
    if spi_status == "suspect":
        return DISPOSITION_RULES[1]
    if category in {"整板同向", "同元件多Pad异常", "局部区域"}:
        return DISPOSITION_RULES[2]
    if recovery_kind == "not_recovered":
        return DISPOSITION_RULES[3]
    if confidence == "高":
        return DISPOSITION_RULES[4]
    return DISPOSITION_RULES[5]


def rule_catalog() -> dict[str, Any]:
    """Reviewable catalog of every executable rule, the mechanism it points
    to, and the decision ladder that combines them. Optional review-card
    fields (applies_when/...) appear only where an expert wrote them."""
    entries = []
    for rule in RULES:
        mechanism = mechanism_by_id(rule.get("mechanism") or "") or {}
        output = {
            "cause": rule.get("cause"),
            "mechanism_id": rule.get("mechanism"),
            "mechanism": mechanism.get("label"),
            "evidence": rule.get("evidence") or rule.get("evidence_template"),
            "action": rule.get("action") or rule.get("action_template"),
            "evidence_level": confidence_level(rule.get("confidence_base", 0.5)),
            "evidence_required": rule.get("evidence_required", []),
            "confidence_base": rule.get("confidence_base", 0.5),
        }
        for optional in ("applies_when", "not_sufficient_when", "first_check", "recheck_method"):
            if rule.get(optional):
                output[optional] = rule[optional]
        entries.append({
            "rule_id": rule["id"],
            "rule_type": rule["rule_type"],
            "source": rule["source"],
            "condition": rule["condition"],
            "output": output,
        })
    for decision_rule in sorted(DECISION_RULES, key=lambda item: item["order"]):
        entries.append({
            "rule_id": decision_rule["id"],
            "rule_type": "decision",
            "source": "knowledge_base.decision_rules",
            "priority": f"order {decision_rule['order']}",
            "condition": {"when": decision_rule["when"]},
            "output": {
                "action": decision_rule["nominates"],
            },
        })
    for rule in DISPOSITION_RULES:
        entries.append({
            "rule_id": f"disposition.{rule['id']}",
            "rule_type": "disposition",
            "source": "knowledge_base.disposition_rules",
            "priority": rule["priority"],
            "condition": {"id": rule["id"]},
            "output": {
                "disposition": rule["disposition"],
                "reason": rule["reason"],
                "evidence_required": ["数据连续性", "SPI 复核状态", "恢复状态", "根因候选置信度"],
            },
        })
    return {
        "version": "rule-catalog-v5",
        "focus": "锡膏印刷 + SPI 多锡/少锡异常管理",
        "rule_count": len(entries),
        "rules": entries,
    }


def enrich_with_ontology_ids(item: dict[str, Any]) -> dict[str, Any]:
    """Attach stable ontology IDs to user-facing rule output."""
    ids = ontology_ids_for(
        direction=item.get("direction"),
        scope=item.get("category") or item.get("scope"),
        cause=item.get("cause") or item.get("primary_cause"),
    )
    return {**item, "ontology_ids": ids} if ids else item
