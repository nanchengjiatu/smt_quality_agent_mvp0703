"""SMT quality knowledge base: one rule registry shared by all agents.

Layering (v4 收敛):

- ``ontology.py`` owns the vocabulary (concept IDs, labels, aliases) AND the
  mechanism layer — 机理 label 是根因显示文本的唯一权威,机理的 action 是
  规范首选动作。
- This module owns the executable rules. Every hand-written rule lives in the
  single ``RULES`` registry with one schema. 事件/实时候选不再是手工规则表,
  而是由机理目录按 (缺陷方向 × 空间范围) **投影生成**(mechanism_projection)
  —— 修机理声明即修所有投影面,同一知识只写一处。
- Chat phrasing lives in ``drilldown_chat.py``.

Rule schema (uniform across all rule types):

    id                  stable rule ID ("rule.*")
    rule_type           scope_root_cause | trend_root_cause | evidence_root_cause
                        | exclusion_check | process_review | abnormal_cause
    condition           dict matching what the analysis provides
    mechanism           ontology FailureMechanism ID (stamped from
                        RULE_MECHANISMS; trend rules are exempt — 趋势形态
                        只是证据,不足以锁定机理)
    cause               derived from the mechanism label(单源);只有
                        CAUSE_OVERRIDES 里的兜底规则和无机理的趋势规则
                        允许手写 cause
    action              the judgment; *_template variants take .format() args
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
推导(≥0.75 高 / ≥0.5 中 / <0.5 低),不再单独维护。候选按**机理**去重
(同一机理不同来源只保留最高置信),前 3 名不再被同一机理的不同措辞占位。

CONFIDENCE_LADDER — confidence_base is the mechanism prior used for ranking
(higher = shown first before evidence multipliers):

    0.85  direct time/image evidence (SPI false alarm, recovery after参数调整)
    0.80  fixed-cadence recurrence (maintenance cycle)
    0.75  measured parameter drift
    0.70  scope rules with tight局部证据 (单Pad / 同元件)
    0.65  scope rules over wider areas (局部区域 / 整板同向)
    0.60  trend-shape rules (渐变/突变归因)
    0.55  process review checklist items
    0.45  mechanism projection candidates without direct佐证
    0.35  fallback
"""

from __future__ import annotations

from typing import Any

from smt_quality_agent.ontology import (
    MECHANISMS,
    concept_by_id,
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

_ABNORMAL_EVIDENCE_REQUIRED = ["异常模式", "风险等级", "连续板序列", "现场确认结果"]

# 实时模式 → 三轴中的空间×时间(ID 来自 ontology 的 SpatialExtent /
# TemporalPattern;数据有效性判断是下钻层的能力,实时口径不给出)。
# rules_engine 标注结果、机理投影选候选,共用这一张映射。
PATTERN_AXES = {
    "同点多板异常": ("spatial.single_pad", "temporal.repeated"),
    "整板趋势异常": ("spatial.board_wide", "temporal.sporadic"),
    "同元件多Pad异常": ("spatial.component_multi_pad", "temporal.sporadic"),
    "单点偶发异常": ("spatial.single_pad", "temporal.sporadic"),
}

# 下钻范围类别:空间轴 → 显示标签。范围的权威表达是三轴概念 ID 组合,
# 类别标签只是给 UI 和规则条件用的投影词表——此处单源注册,杜绝并行词表。
DRILLDOWN_SPATIAL_CATEGORY = {
    "spatial.board_wide": "整板同向",
    "spatial.component_multi_pad": "同元件多Pad异常",
    "spatial.local_area": "局部区域",
    "spatial.single_pad": "单Pad孤立异常",
}

# 数据有效性轴驱动的类别:NG 标签与主指标不符时覆盖空间类别。
SPI_FALSE_ALARM_CATEGORY = "疑似SPI假异常"

# 规则条件 condition.scope / condition.abnormal_pattern 允许使用的全部标签。
SCOPE_CATEGORY_LABELS = frozenset({
    *DRILLDOWN_SPATIAL_CATEGORY.values(),
    SPI_FALSE_ALARM_CATEGORY,
    *PATTERN_AXES.keys(),
})

# 事件分组轴 → 空间范围集合(事件分析只分整板/局部,局部覆盖三种局部范围)。
_EVENT_SCOPE_SPATIAL = {
    EVENT_SCOPE_BOARD: ("spatial.board_wide",),
    EVENT_SCOPE_LOCAL: ("spatial.single_pad", "spatial.component_multi_pad",
                        "spatial.local_area"),
}


def event_scope_for_category(category: str) -> str:
    """Map a canonical scope category onto the coarse event grouping axis."""
    return EVENT_SCOPE_BOARD if category in {"整板同向", "整板趋势异常"} else EVENT_SCOPE_LOCAL


# --- Singleton rules referenced directly by the drilldown agent --------------

SPI_FALSE_ALARM_ROOT_CAUSE = {
    "id": "rule.spi_false_alarm_review",
    "rule_type": "exclusion_check",
    "condition": {"trigger": "主指标偏差不支撑 NG 标签"},
    "source": "knowledge_base",
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
    "evidence_template": "触发板 {parameters} 的实际-计划偏差超出基线。",
    "action_template": "调取印刷机事件时段记录，校验 {parameters} 的设定值、实际值和机构状态；恢复基准后做首件确认。",
    "evidence_required": ["印刷机设定值", "印刷机实际值", "事件时段设备日志", "首块 NG 时间"],
    "confidence_base": 0.75,
}

# 参数按机理分组:组内参数漂移产生绑定对应机理的专属候选,替代笼统的
# "印刷参数偏离设定"。分组令牌按参数名(小写)子串匹配。
RELEASE_PARAMETER_DRIFT_RULE = {
    "id": "rule.release_parameter_drift",
    "rule_type": "evidence_root_cause",
    "condition": {"trigger": "脱模参数(SnapOff*)实际-计划偏差超出基线"},
    "source": "knowledge_base",
    "evidence_template": "触发板脱模参数 {parameters} 的实际-计划偏差超出基线。",
    "action_template": "核对 {parameters} 的设定值与实际值，确认脱模速度/距离/延时与钢网-PCB 分离动作；恢复基准后做首件确认。",
    "evidence_required": ["SnapOff 系设定值与实际值", "事件时段设备日志", "首块 NG 时间"],
    "confidence_base": 0.75,
}

SQUEEGEE_PARAMETER_DRIFT_RULE = {
    "id": "rule.squeegee_parameter_drift",
    "rule_type": "evidence_root_cause",
    "condition": {"trigger": "刮刀参数(SQG*)实际-计划偏差超出基线"},
    "source": "knowledge_base",
    "evidence_template": "触发板刮刀参数 {parameters} 的实际-计划偏差超出基线。",
    "action_template": "核对 {parameters} 的设定值与实际值，现场确认刮刀压力/升降速度与刃口状态后再调整。",
    "evidence_required": ["刮刀参数设定值与实际值", "刮刀状态检查", "首块 NG 时间"],
    "confidence_base": 0.75,
}

CLEANING_PARAMETER_DRIFT_RULE = {
    "id": "rule.cleaning_parameter_drift",
    "rule_type": "evidence_root_cause",
    "condition": {"trigger": "擦网参数(Cleaning*)实际-计划偏差超出基线"},
    "source": "knowledge_base",
    "evidence_template": "触发板擦网参数 {parameters} 的实际-计划偏差超出基线。",
    "action_template": "核对 {parameters} 的设定值与实际值，确认擦网频率/速度与清洁效果；必要时立即手动清洁并复判。",
    "evidence_required": ["擦网参数设定值与实际值", "擦网耗材检查", "复判结果"],
    "confidence_base": 0.75,
}

_PARAMETER_GROUPS = [
    ("snapoff", RELEASE_PARAMETER_DRIFT_RULE),
    ("sqg", SQUEEGEE_PARAMETER_DRIFT_RULE),
    ("cleaning", CLEANING_PARAMETER_DRIFT_RULE),
]

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
        "evidence": "SPI 位置偏移、同向面积/体积异常或多个 Pad 同步偏移时需要排除。",
        "action": "复核印刷机视觉识别、Fiducial、Gerber/钢网/PCB 对位和 SPI Offset 趋势。",
        "evidence_required": ["X/Y Offset 趋势", "Fiducial 识别记录", "Gerber/钢网/PCB 对位", "首件图像"],
        "confidence_base": 0.55,
    },
    {
        "id": "rule.review_gasketing_board_support",
        "condition": {"review_item": "gasketing_board_support"},
        "evidence": "局部区域、多 Pad 或板翘相关异常需要排除贴合与支撑问题。",
        "action": "检查 PCB 支撑、夹持、板翘、钢网贴合和局部真空/顶针设置。",
        "evidence_required": ["板翘/支撑检查", "夹持状态", "钢网贴合检查", "局部 Pad 分布热力图"],
        "confidence_base": 0.55,
    },
    {
        "id": "rule.review_stencil_cleaning_process",
        "condition": {"review_item": "stencil_cleaning_process"},
        "evidence": "异常随生产累积、清洁后恢复或固定周期复发时需要优先复核。",
        "action": "核对擦网周期、擦网纸/溶剂/真空清洁状态，并比较清洁前后 SPI 分布。",
        "evidence_required": ["擦网周期", "擦网纸/溶剂状态", "清洁前后 SPI 趋势", "固定周期复发记录"],
        "confidence_base": 0.55,
    },
    {
        "id": "rule.review_stencil_aperture_design",
        "condition": {"review_item": "stencil_aperture_design"},
        "evidence": "长期稳定复发、清洁无效或细间距 Pad 反复少锡/多锡时需要评估开口设计。",
        "action": "复核开口尺寸、钢网厚度、面积比、开口壁状态和历史 ECN/钢网版本。",
        "evidence_required": ["开口尺寸", "钢网厚度", "面积比", "钢网版本/ECN", "清洁后复发情况"],
        "confidence_base": 0.55,
    },
]

# --- Mechanism projection (event / realtime candidates) ------------------------
# v3 时代这里是两张手工规则表(12 条事件候选 + 25 条实时候选),同一机理在
# 各表里维护着不同的 cause/action 措辞,格子还出现过不对称遗漏。v4 起候选由
# 机理目录按 (缺陷方向 × 空间范围) 投影生成:机理声明 direction 与
# typical_spatial 即自动出现在对应投影面,cause=机理 label,action=机理规范
# 动作。修机理声明即修所有投影面。
#
# 排序:方向精确匹配(非"双向")在前 → 声明了观测时间模式的在前 → 机理目录
# 声明顺序;每个投影面取前 3。时间模式只参与排序不做硬过滤——实时/事件口径
# 能看到的历史有限,typical_temporal 是按下钻口径写的。

PROJECTION_CANDIDATE_LIMIT = 3
PROJECTION_CONFIDENCE = 0.45


def project_mechanisms(
    direction: str,
    spatial_ids: tuple[str, ...],
    temporal_id: str | None = None,
    limit: int = PROJECTION_CANDIDATE_LIMIT,
) -> list[dict[str, Any]]:
    """Select mechanisms whose declared direction/typical_spatial cover the
    requested projection cell, ranked, capped. Returns synthetic rules with
    the same schema as registry rules."""
    ranked = []
    for index, (mechanism_id, mechanism) in enumerate(MECHANISMS.items()):
        props = mechanism.get("properties") or {}
        mech_direction = props.get("direction", "")
        if mech_direction not in (direction, "双向"):
            continue
        typical_spatial = props.get("typical_spatial") or []
        if not any(spatial in typical_spatial for spatial in spatial_ids):
            continue
        rank = (
            0 if mech_direction == direction else 1,
            0 if temporal_id and temporal_id in (props.get("typical_temporal") or []) else 1,
            index,
        )
        ranked.append((rank, mechanism_id, mechanism, props))
    ranked.sort(key=lambda item: item[0])

    rules = []
    for _, mechanism_id, mechanism, props in ranked[:limit]:
        rules.append({
            "id": f"rule.projected.{mechanism_id.split('.', 1)[1]}",
            "rule_type": "mechanism_projection",
            "condition": {"direction": direction, "spatial": list(spatial_ids)},
            "source": "knowledge_base.mechanism_projection",
            "cause": mechanism["label"],
            "evidence": "按缺陷方向与空间范围从机理目录投影的候选，当前无直接佐证。",
            "action": props.get("action", ""),
            "confidence_base": PROJECTION_CONFIDENCE,
            "mechanism": mechanism_id,
        })
    return rules


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
    RELEASE_PARAMETER_DRIFT_RULE,
    SQUEEGEE_PARAMETER_DRIFT_RULE,
    CLEANING_PARAMETER_DRIFT_RULE,
    FALLBACK_ROOT_CAUSE,
    *_finalize(_SCOPE_ROOT_CAUSE_RULES, "scope_root_cause", "knowledge_base",
               { "recheck_method": DEFAULT_RECHECK_METHOD}),
    *_finalize(_TREND_ROOT_CAUSE_RULES, "trend_root_cause", "knowledge_base", {}),
    *_finalize(_PROCESS_REVIEW_RULES, "process_review", "knowledge_base.process_review", {}),
    _ABNORMAL_FALLBACK_RULE,
]

# --- Mechanism binding (机理层绑定,集中映射便于评审) ---------------------------
# 每条规则指向 ontology 里最接近的主机理;趋势形态规则不绑定(趋势本身不足以
# 锁定机理,只作为证据输入决策层)。规则的 cause 显示文本 = 机理 label(单源),
# 例外只允许出现在 CAUSE_OVERRIDES:两条兜底规则挂 mech.undetermined,但对
# 用户要传达的是"证据不足/先复测",不是机理名。
RULE_MECHANISMS = {
    "rule.spi_false_alarm_review": "mech.spi_false_call",
    "rule.parameter_recovery": "mech.parameter_mismatch",
    "rule.periodic_maintenance_cycle": "mech.cleaning_cycle_mismatch",
    "rule.parameter_drift": "mech.parameter_mismatch",
    "rule.release_parameter_drift": "mech.poor_release",
    "rule.squeegee_parameter_drift": "mech.parameter_mismatch",
    "rule.cleaning_parameter_drift": "mech.cleaning_cycle_mismatch",
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
    # 实时兜底
    "rule.abnormal_observe_next_board": "mech.undetermined",
}

# 明确不绑定机理的规则(趋势形态只是证据,不足以锁定机理)。
RULES_WITHOUT_MECHANISM = {"rule.trend_gradual_degradation", "rule.trend_step_change"}

# 兜底规则的 cause 手写例外(见 RULE_MECHANISMS 注释)。
CAUSE_OVERRIDES = {"rule.fallback_local_printing_state", "rule.abnormal_observe_next_board"}

for _rule in RULES:
    _rule["mechanism"] = RULE_MECHANISMS.get(_rule["id"])
    if _rule["mechanism"] and _rule["id"] not in CAUSE_OVERRIDES:
        _rule["cause"] = concept_label(_rule["mechanism"])

_RULES_BY_ID = {rule["id"]: rule for rule in RULES}
assert len(_RULES_BY_ID) == len(RULES), "duplicate rule id in RULES registry"
assert all(
    rule["mechanism"] or rule["id"] in RULES_WITHOUT_MECHANISM for rule in RULES
), "every rule needs a mechanism or an explicit exemption"
assert all(rule.get("cause") for rule in RULES), "every rule needs a derived or exempted cause"


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


# 处置词表单源:disposition 文本与优先级取自 ontology 的 Disposition 概念,
# 这里只声明触发条件对应的 reason。
def _disposition_rule(rule_id: str, concept_id: str, reason: str) -> dict[str, str]:
    concept = concept_by_id(concept_id) or {}
    return {
        "id": rule_id,
        "concept": concept_id,
        "priority": (concept.get("properties") or {}).get("priority", ""),
        "disposition": concept.get("label", concept_id),
        "reason": reason,
    }


DISPOSITION_RULES = [
    _disposition_rule(
        "data_continuity_review", "disposition.data_continuity_review",
        "触发段存在数据连续性疑点，根因判断只能作为预判。",
    ),
    _disposition_rule(
        "spi_false_alarm_review", "disposition.spi_program_review",
        "主指标偏差不支撑当前 NG 标签，需先排除 SPI 假异常。",
    ),
    _disposition_rule(
        "widened_scope", "disposition.immediate_field_check",
        "异常已从单 Pad 扩散到更大范围，存在批量风险。",
    ),
    _disposition_rule(
        "not_recovered", "disposition.halt_and_contain",
        "触发后未恢复，连续生产会扩大同点不良风险。",
    ),
    _disposition_rule(
        "high_confidence", "disposition.confirm_primary_cause",
        "规则证据链较强，可直接按首要根因验证。",
    ),
    _disposition_rule(
        "single_point_fast_check", "disposition.fast_single_point_check",
        "当前证据更像局部单点问题，先做低成本复核和短程复判。",
    ),
]

_DISPOSITION_BY_ID = {rule["id"]: rule for rule in DISPOSITION_RULES}


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


# --- Metric-signature discrimination (三指标签名甄别) ---------------------------
# 观测签名由 drilldown 按 max(3σ, 10pp) 判界得出;这里只做与机理声明签名的
# 匹配。方向硬冲突(观测↑而机理只允许↓,或反之)才降权;"平 vs 要求↑"这类
# 弱分歧记 partial 不调整,避免判界灵敏度误伤。
SIGNATURE_MATCH_MULTIPLIER = 1.2
SIGNATURE_CONFLICT_MULTIPLIER = 0.7
CONFIDENCE_CAP = 0.95

# 观测空间范围与机理声明的典型范围(typical_spatial)的一致性调整。比签名
# 弱得多的证据(典型范围是先验知识,不是本次观测的直接测量),幅度取小。
SPATIAL_TYPICAL_MULTIPLIER = 1.1
SPATIAL_ATYPICAL_MULTIPLIER = 0.9

# NG 周期与擦网频率设定成整数倍关系时,周期性候选的加权。
CLEANING_ALIGNMENT_MULTIPLIER = 1.15
CLEANING_ALIGNMENT_TOLERANCE = 0.2


def _parse_signature(signature: str) -> dict[str, set[str]]:
    """'avdp:down,aadp:down|flat' -> {'avdp': {'down'}, 'aadp': {'down','flat'}};
    'any' constraints are dropped (no discriminating power)."""
    constraints: dict[str, set[str]] = {}
    for part in (signature or "").split(","):
        if ":" not in part:
            continue
        metric, allowed_text = part.split(":", 1)
        allowed = {value.strip() for value in allowed_text.split("|")}
        if "any" not in allowed:
            constraints[metric.strip()] = allowed
    return constraints


def match_metric_signature(
    signature: str,
    observed: dict[str, str],
) -> tuple[str, str]:
    """Match one mechanism's declared signature against the observed one.

    Returns (status, detail): matched(全部受约束指标一致,≥2 项可判) /
    conflict(存在方向硬冲突) / partial(部分一致或仅 1 项可判) /
    unknown(无声明或无观测)。"""
    constraints = _parse_signature(signature)
    if not constraints or not observed:
        return "unknown", "机理未声明签名或观测数据不足。"

    evaluated = 0
    agreed = 0
    conflicts = []
    for metric, allowed in constraints.items():
        verdict = observed.get(metric)
        if verdict is None:
            continue
        evaluated += 1
        if verdict in allowed:
            agreed += 1
        elif (verdict == "up" and allowed == {"down"}) or (verdict == "down" and allowed == {"up"}):
            conflicts.append(metric)
    if not evaluated:
        return "unknown", "受约束指标均无足够基线数据。"
    if conflicts:
        return "conflict", f"指标 {'、'.join(conflicts)} 与机理签名方向相反。"
    if agreed == evaluated and evaluated >= 2:
        return "matched", f"{evaluated} 项受约束指标全部符合机理签名。"
    return "partial", f"{agreed}/{evaluated} 项受约束指标符合机理签名。"


def cleaning_cycle_aligned(gap: float | None, frequency: float | None) -> bool:
    """NG 连段间隔是否与擦网频率设定成整数倍关系。

    容差按"偏离最近整倍数不超过 0.2 个擦网周期"计——若按倍数的百分比计,
    倍数一大容差窗口会互相重叠,任何间隔都能"对齐"。"""
    if not gap or not frequency or frequency <= 0:
        return False
    nearest = max(round(gap / frequency), 1)
    return abs(gap - nearest * frequency) <= CLEANING_ALIGNMENT_TOLERANCE * frequency


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
    "evidence.cleaning_frequency_reference": lambda obs: (
        cleaning_cycle_aligned(obs.get("periodic_gap"), obs.get("cleaning_frequency")),
        (
            f"NG 连段间隔约 {obs.get('periodic_gap')} 块板,与擦网频率设定 "
            f"{obs.get('cleaning_frequency')} 成整数倍关系。"
            if cleaning_cycle_aligned(obs.get("periodic_gap"), obs.get("cleaning_frequency"))
            else "NG 连段间隔与擦网频率设定无整数倍关系,或缺少周期/设定数据。"
        ),
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
    multiplier_label: str = "证据乘数",
) -> dict[str, Any]:
    """Uniform root-cause candidate payload consumed by conclusions and UI.

    最终置信度 = 机理先验(confidence_base) × 证据乘数 × 签名乘数 × 空间
    典型性乘数,上限 CONFIDENCE_CAP;evidence_level 由最终置信度分档推导;
    算式因子记录在 confidence_factors/confidence_formula 供诊断轨迹与 UI
    展示。带 observation 时评估机理的自动核验证据、做签名甄别和空间典型性
    比对。"""
    mechanism = mechanism_by_id(rule.get("mechanism") or "") or {}
    props = mechanism.get("properties") or {}

    signature_status = "unknown"
    signature_detail = ""
    if observation is not None and props.get("signature"):
        signature_status, signature_detail = match_metric_signature(
            props["signature"], observation.get("metric_signature") or {},
        )
    signature_multiplier = {
        "matched": SIGNATURE_MATCH_MULTIPLIER,
        "conflict": SIGNATURE_CONFLICT_MULTIPLIER,
    }.get(signature_status, 1.0)

    typicality_multiplier = 1.0
    observed_spatial = (observation or {}).get("spatial")
    typical_spatial = props.get("typical_spatial") or []
    if observed_spatial and typical_spatial:
        typicality_multiplier = (
            SPATIAL_TYPICAL_MULTIPLIER if observed_spatial in typical_spatial
            else SPATIAL_ATYPICAL_MULTIPLIER
        )

    base = rule.get("confidence_base", 0.5)
    factors = [{"label": "先验", "value": base}]
    if multiplier != 1.0:
        factors.append({"label": multiplier_label, "value": multiplier})
    if signature_multiplier != 1.0:
        factors.append({
            "label": "签名匹配" if signature_status == "matched" else "签名冲突",
            "value": signature_multiplier,
        })
    if typicality_multiplier != 1.0:
        factors.append({
            "label": "范围典型" if typicality_multiplier > 1.0 else "范围非典型",
            "value": typicality_multiplier,
        })
    raw = round(base * multiplier * signature_multiplier * typicality_multiplier, 3)
    confidence = min(raw, CONFIDENCE_CAP)
    formula = " × ".join(
        f"{item['value']:g}({item['label']})" if index else f"{item['value']:g}"
        for index, item in enumerate(factors)
    )
    if len(factors) > 1:
        formula += f" = {confidence:g}"
        if raw > CONFIDENCE_CAP:
            formula += f"（上限 {CONFIDENCE_CAP:g}）"
    manual_checks = [concept_label(item) for item in props.get("manual_checks", [])]
    candidate = {
        "rule_id": rule["id"],
        "rule_source": rule["source"],
        "cause": rule["cause"],
        "evidence": evidence or rule.get("evidence", ""),
        "action": rule.get("action") or rule.get("action_template", ""),
        "confidence_base": base,
        "confidence": confidence,
        "confidence_factors": factors,
        "confidence_formula": formula,
        "evidence_level": confidence_level(confidence),
        "mechanism_id": rule.get("mechanism"),
        "mechanism": mechanism.get("label"),
        "location": concept_label(props["element"]) if props.get("element") else None,
        "onset": props.get("onset"),
        "early_warning": props.get("early_warning") or None,
        "signature_match": signature_status,
        "manual_checks": manual_checks or rule.get("evidence_required", []),
    }
    if observation is not None:
        checks = evaluate_auto_checks(props.get("auto_checks", []), observation)
        if signature_status != "unknown" and not any(
            check["evidence_id"] == "evidence.metric_signature" for check in checks
        ):
            checks.append({
                "evidence_id": "evidence.metric_signature",
                "name": concept_label("evidence.metric_signature"),
                "status": {
                    "matched": "核验通过",
                    "conflict": "不匹配",
                    "partial": "未见",
                }[signature_status],
                "detail": f"{observation.get('metric_signature_text', '')}。{signature_detail}",
            })
        candidate["auto_checks"] = checks
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
    """事件候选 = 机理目录按 (方向 × 事件分组轴对应的空间范围集) 投影。"""
    rules = project_mechanisms(direction, _EVENT_SCOPE_SPATIAL.get(event_scope, ()))
    return [
        root_cause_candidate_from_rule(rule, observation=observation)
        for rule in rules
    ]


def abnormal_cause_candidates(defect_type: str, pattern: str, risk_level: str) -> list[dict[str, Any]]:
    """实时候选 = 机理目录按 (方向 × 实时模式的空间轴) 投影。

    单点偶发不投影——单板孤立一次不足以提名机理,先复测(兜底规则)。
    risk_level 不再筛选候选(v3 手工表按风险重复维护近似规则),它由处置
    分级消费。"""
    spatial, temporal = PATTERN_AXES.get(pattern, (None, None))
    rules: list[dict[str, Any]] = []
    if spatial and pattern != "单点偶发异常":
        rules = project_mechanisms(defect_type, (spatial,), temporal)
    if not rules:
        rules = [_ABNORMAL_FALLBACK_RULE]
    return [root_cause_candidate_from_rule(rule) for rule in rules]


# --- Decision layer ------------------------------------------------------------
# v2 时代 build_conclusion 里的隐式候选收集顺序,显式化为可评审的决策规则组。
# order 越小越先求值;门槛类(有效性)在前,直接时序证据次之,先验类靠后。
# 乘数(multiplier)是置信度模型的证据修正项,声明在规则里而不是散在代码中。

def _decide_spi_gate(obs: dict[str, Any]) -> list[dict[str, Any]]:
    return [root_cause_candidate_from_rule(
        SPI_FALSE_ALARM_ROOT_CAUSE, obs.get("spi_detail", ""), observation=obs,
    )]


def _decide_parameter_drift(obs: dict[str, Any]) -> list[dict[str, Any]]:
    """Drifted parameters are grouped by the mechanism they physically belong
    to (脱模/刮刀/擦网); only ungrouped parameters fall back to the generic
    parameter-drift rule."""
    multiplier = 0.8 if obs.get("cross_model_baseline") else 1.0
    remaining = list(obs["drifted_parameters"])
    candidates = []

    def build(rule: dict[str, Any], parameters: list[str]) -> dict[str, Any]:
        names = "、".join(parameters[:3])
        candidate = root_cause_candidate_from_rule(
            rule,
            rule["evidence_template"].format(parameters=names),
            multiplier=multiplier,
            observation=obs,
            multiplier_label="跨机种基线",
        )
        candidate["action"] = rule["action_template"].format(parameters=names)
        return candidate

    for token, rule in _PARAMETER_GROUPS:
        group = [name for name in remaining if token in name.lower()]
        if not group:
            continue
        remaining = [name for name in remaining if name not in group]
        candidates.append(build(rule, group))
    if remaining:
        candidates.append(build(PARAMETER_DRIFT_ROOT_CAUSE, remaining))
    return candidates


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
    aligned = cleaning_cycle_aligned(gap, obs.get("cleaning_frequency"))
    if aligned:
        evidence += (
            f"节拍与擦网频率设定({obs['cleaning_frequency']:g})成整数倍关系，"
            "直接指向擦网周期。"
        )
    return [root_cause_candidate_from_rule(
        PERIODIC_ROOT_CAUSE, evidence,
        multiplier=CLEANING_ALIGNMENT_MULTIPLIER if aligned else 1.0,
        observation=obs,
        multiplier_label="擦网频率对齐",
    )]


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


def _decide_mechanism_projection(obs: dict[str, Any]) -> list[dict[str, Any]]:
    """下钻观测带精确的空间轴,直接按 (方向 × 观测空间) 投影机理目录。"""
    spatial = obs.get("spatial")
    spatial_ids = (
        (spatial,) if spatial
        else _EVENT_SCOPE_SPATIAL[event_scope_for_category(obs["category"])]
    )
    rules = project_mechanisms(obs["direction"], spatial_ids, obs.get("temporal"))
    return [root_cause_candidate_from_rule(rule, observation=obs) for rule in rules]


DECISION_RULES = [
    {
        "id": "decide.spi_false_alarm_gate",
        "label": "SPI误判门槛",
        "role": "gate",
        "order": 10,
        "when": "数据有效性 = 疑似SPI误判",
        "nominates": "rule.spi_false_alarm_review（先排除假异常再谈物理机理）",
        "applies": lambda obs: obs.get("validity") == "validity.spi_suspect",
        "build": _decide_spi_gate,
    },
    {
        "id": "decide.parameter_recovery",
        "label": "参数恢复",
        "role": "nominate",
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
        "label": "参数漂移(分组)",
        "role": "nominate",
        "order": 21,
        "when": "触发板参数实际-计划偏差超出基线",
        "nominates": (
            "按机理分组提名：SnapOff*→脱模不良、SQG*→刮刀、Cleaning*→擦网周期，"
            "其余走通用 rule.parameter_drift（跨机种基线时置信 ×0.8）"
        ),
        "applies": lambda obs: bool(obs.get("drifted_parameters")),
        "build": _decide_parameter_drift,
    },
    {
        "id": "decide.periodic_recurrence",
        "label": "周期性",
        "role": "nominate",
        "order": 30,
        "when": "NG 连段呈固定节拍复发",
        "nominates": "rule.periodic_maintenance_cycle",
        "applies": lambda obs: bool(obs.get("periodic")),
        "build": _decide_periodic,
    },
    {
        "id": "decide.scope_prior",
        "label": "范围先验",
        "role": "nominate",
        "order": 50,
        "when": "空间范围与缺陷方向组合(疑似SPI误判时跳过)",
        "nominates": "scope_root_cause 规则组",
        "applies": lambda obs: obs.get("validity") != "validity.spi_suspect",
        "build": _decide_scope_prior,
    },
    {
        "id": "decide.trend_shape",
        "label": "趋势归因",
        "role": "nominate",
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
        "id": "decide.mechanism_projection",
        "label": "机理投影",
        "role": "nominate",
        "order": 70,
        "when": "按缺陷方向与观测空间范围从机理目录投影补充候选(无直接佐证)",
        "nominates": (
            f"direction × typical_spatial 覆盖观测的机理,先验 {PROJECTION_CONFIDENCE:g},"
            f"每面取前 {PROJECTION_CANDIDATE_LIMIT}"
        ),
        "applies": lambda obs: True,
        "build": _decide_mechanism_projection,
    },
]

# 调整型决策规则:不提名新候选,而是修正已提名候选的置信度。求值发生在
# 候选构造内(与提名顺序无关),order 仅表达其在决策梯中的位置。
ADJUSTMENT_RULES = [
    {
        "id": "decide.cleaning_frequency_reference",
        "label": "擦网频率对齐",
        "role": "adjust",
        "order": 31,
        "when": "周期性成立且 NG 节拍偏离 CleaningFrequency 最近整倍数 ≤0.2 个周期",
        "nominates": f"周期性候选置信 ×{CLEANING_ALIGNMENT_MULTIPLIER:g}",
    },
    {
        "id": "decide.signature_discrimination",
        "label": "签名甄别",
        "role": "adjust",
        "order": 40,
        "when": "候选机理声明了三指标签名且观测签名可判(判界 max(3σ, 10pp))",
        "nominates": (
            f"签名全部匹配 ×{SIGNATURE_MATCH_MULTIPLIER:g}；方向硬冲突 "
            f"×{SIGNATURE_CONFLICT_MULTIPLIER:g}；部分/未知不调整；"
            f"最终置信上限 {CONFIDENCE_CAP:g}"
        ),
    },
    {
        "id": "decide.spatial_typicality",
        "label": "空间典型性",
        "role": "adjust",
        "order": 41,
        "when": "候选机理声明了典型空间范围且观测带空间轴",
        "nominates": (
            f"观测空间在机理典型范围内 ×{SPATIAL_TYPICAL_MULTIPLIER:g}；"
            f"不在 ×{SPATIAL_ATYPICAL_MULTIPLIER:g}(典型范围是先验知识,"
            "幅度小于签名甄别)"
        ),
    },
]


def diagnose(observation: dict[str, Any]) -> dict[str, Any]:
    """Run the decision-rule ladder over one drilldown observation.

    Returns the ranked root-cause assessment (candidates sorted by final
    confidence, deduplicated by mechanism — 同一机理不同来源只保留最高置信,
    防止同一物理判断的不同措辞挤占前 3 — capped at 3, ontology IDs attached,
    overall confidence grade) plus a decision_trace — the audit trail of
    which decision rules fired, what they nominated with the confidence
    math, and which candidates were eliminated and why."""
    candidates: list[dict[str, Any]] = []
    steps: list[dict[str, Any]] = []
    for decision_rule in sorted(DECISION_RULES, key=lambda item: item["order"]):
        fired = bool(decision_rule["applies"](observation))
        nominated = []
        if fired:
            for candidate in decision_rule["build"](observation):
                candidate["decided_by"] = decision_rule["id"]
                candidates.append(candidate)
                nominated.append({
                    "cause": candidate["cause"],
                    "rule_id": candidate["rule_id"],
                    "confidence": candidate["confidence"],
                    "formula": candidate["confidence_formula"],
                })
        steps.append({
            "id": decision_rule["id"],
            "label": decision_rule["label"],
            "order": decision_rule["order"],
            "when": decision_rule["when"],
            "fired": fired,
            "nominated": nominated,
        })

    if not candidates:
        fallback = root_cause_candidate_from_rule(FALLBACK_ROOT_CAUSE, observation=observation)
        fallback["decided_by"] = "decide.fallback"
        candidates.append(fallback)
        steps.append({
            "id": "decide.fallback",
            "label": "未定机理兜底",
            "order": 90,
            "when": "以上决策规则均未提名任何候选",
            "fired": True,
            "nominated": [{
                "cause": fallback["cause"],
                "rule_id": fallback["rule_id"],
                "confidence": fallback["confidence"],
                "formula": fallback["confidence_formula"],
            }],
        })

    candidates.sort(key=lambda item: -item["confidence"])
    assessments: list[dict[str, Any]] = []
    eliminated: list[dict[str, Any]] = []
    # 去重键:机理 ID(无机理的趋势归因按 cause 文本)。
    dedup_key = lambda item: item.get("mechanism_id") or item["cause"]
    for candidate in candidates:
        if dedup_key(candidate) in {dedup_key(item) for item in assessments}:
            eliminated.append({
                "cause": candidate["cause"],
                "rule_id": candidate["rule_id"],
                "confidence": candidate["confidence"],
                "reason": "同机理去重（保留更高置信候选）",
            })
            continue
        if len(assessments) >= 3:
            eliminated.append({
                "cause": candidate["cause"],
                "rule_id": candidate["rule_id"],
                "confidence": candidate["confidence"],
                "reason": "排序后未进前 3",
            })
            continue
        assessments.append(candidate)

    for priority, item in enumerate(assessments, 1):
        item["priority"] = priority
        item["ontology_ids"] = ontology_ids_for(
            direction=observation.get("direction"),
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
        "decision_trace": {
            "steps": steps,
            "eliminated": eliminated,
        },
    }


def disposition_for(
    data_status: str | None,
    spi_status: str | None,
    category: str,
    recovery_kind: str,
    confidence: str,
) -> dict[str, str]:
    if data_status != "pass":
        return _DISPOSITION_BY_ID["data_continuity_review"]
    if spi_status == "suspect":
        return _DISPOSITION_BY_ID["spi_false_alarm_review"]
    if category in {"整板同向", "同元件多Pad异常", "局部区域"}:
        return _DISPOSITION_BY_ID["widened_scope"]
    if recovery_kind == "not_recovered":
        return _DISPOSITION_BY_ID["not_recovered"]
    if confidence == "高":
        return _DISPOSITION_BY_ID["high_confidence"]
    return _DISPOSITION_BY_ID["single_point_fast_check"]


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
    for decision_rule in sorted(
        [*DECISION_RULES, *ADJUSTMENT_RULES], key=lambda item: item["order"],
    ):
        entries.append({
            "rule_id": decision_rule["id"],
            "rule_type": "decision",
            "source": "knowledge_base.decision_rules",
            "priority": f"order {decision_rule['order']}",
            "label": decision_rule["label"],
            "role": decision_rule["role"],
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
        "version": "rule-catalog-v6",
        "focus": "锡膏印刷 + SPI 多锡/少锡异常管理",
        "rule_count": len(entries),
        "rules": entries,
        "mechanisms": mechanism_catalog(),
    }


def mechanism_catalog() -> list[dict[str, Any]]:
    """Display-ready mechanism summaries for the rules page: each mechanism
    with its location, signature, early-warning hook, and evidence split into
    auto (with availability) and manual."""
    entries = []
    for mechanism_id, mechanism in MECHANISMS.items():
        props = mechanism.get("properties") or {}
        entries.append({
            "mechanism_id": mechanism_id,
            "label": mechanism["label"],
            "description": mechanism["description"],
            "element": concept_label(props.get("element", "")),
            "stage": concept_label(props.get("stage", "")),
            "direction": props.get("direction", ""),
            "onset": props.get("onset", ""),
            "signature_text": props.get("signature_text") or "",
            "typical_spatial_labels": [
                concept_label(spatial_id)
                for spatial_id in props.get("typical_spatial", [])
            ],
            "typical_temporal_labels": [
                concept_label(temporal_id)
                for temporal_id in props.get("typical_temporal", [])
            ],
            "early_warning": props.get("early_warning") or "",
            "action": props.get("action") or "",
            "auto_checks": [
                {
                    "id": evidence_id,
                    "label": concept_label(evidence_id),
                    "availability": evidence_availability(evidence_id),
                }
                for evidence_id in props.get("auto_checks", [])
            ],
            "manual_checks": [
                {"id": evidence_id, "label": concept_label(evidence_id)}
                for evidence_id in props.get("manual_checks", [])
            ],
        })
    return entries


def enrich_with_ontology_ids(item: dict[str, Any]) -> dict[str, Any]:
    """Attach stable ontology IDs to user-facing rule output."""
    ids = ontology_ids_for(
        direction=item.get("direction"),
        cause=item.get("cause") or item.get("primary_cause"),
    )
    return {**item, "ontology_ids": ids} if ids else item
