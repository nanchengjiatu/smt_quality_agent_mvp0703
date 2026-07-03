"""Code-native ontology for the SMT SPI quality agent.

This module is the single vocabulary source for the whole project:

- Every user-facing label (defect direction, abnormal scope, root cause,
  evidence type, action, disposition) is registered here exactly once as a
  concept with a stable ID.
- The label->ID mappings used to annotate rule output are generated from the
  concepts (labels + aliases), never hand-maintained.
- ``docs/smt_quality_ontology.ttl`` is generated from this module
  (``python3 -m smt_quality_agent.ontology``), so the TTL can never drift.

Executable diagnostic rules do NOT live here — they live in
``knowledge_base.py`` and reference this vocabulary. Realtime patterns and
drilldown categories with different判定口径 are registered as distinct
concepts instead of being forced under one label.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

ONTOLOGY_VERSION = "spi-printing-v2"
ONTOLOGY_FOCUS = "锡膏印刷 + SPI 多锡/少锡异常管理"


@dataclass(frozen=True)
class OntologyConcept:
    id: str
    type: str
    label: str
    description: str
    aliases: tuple[str, ...] = ()
    properties: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "id": self.id,
            "type": self.type,
            "label": self.label,
            "description": self.description,
            "aliases": list(self.aliases),
        }
        if self.properties:
            payload["properties"] = self.properties
        return payload


@dataclass(frozen=True)
class OntologyRelation:
    subject: str
    predicate: str
    object: str
    description: str = ""

    def to_dict(self) -> dict[str, str]:
        payload = {
            "subject": self.subject,
            "predicate": self.predicate,
            "object": self.object,
        }
        if self.description:
            payload["description"] = self.description
        return payload


CONCEPTS = [
    OntologyConcept(
        "process.solder_paste_printing",
        "ProcessStep",
        "锡膏印刷",
        "当前 MVP 聚焦的制程步骤，负责将锡膏转移到 PCB Pad。",
        ("印刷", "printing"),
    ),
    OntologyConcept(
        "inspection.spi",
        "InspectionMethod",
        "SPI 检测",
        "锡膏印刷后的 3D/2D 检测数据来源。",
        ("SPI", "solder paste inspection"),
    ),
    OntologyConcept(
        "defect.over_volume",
        "DefectDirection",
        "多锡",
        "锡膏体积、面积或高度高于工艺窗口。",
        ("Over Volume", "过量"),
        {"direction": "多锡"},
    ),
    OntologyConcept(
        "defect.insufficient_volume",
        "DefectDirection",
        "少锡",
        "锡膏体积、面积或高度低于工艺窗口。",
        ("Insufficient Volume", "少量"),
        {"direction": "少锡"},
    ),
    # -- Abnormal scopes -----------------------------------------------------
    # 实时(rules_engine)与下钻(drilldown)的判定口径不同：实时按单板/跨板计数
    # 归类，下钻基于前后全量 SPI 窗口归类。口径不同的判断注册为不同概念，
    # 不共用标签。
    OntologyConcept(
        "scope.board_same_direction",
        "AbnormalScope",
        "整板同向",
        "下钻口径：触发板或全量窗口内多个位置呈现同方向异常，优先判断整板制程条件。",
        ("整板同向趋势",),
        {"judged_by": "drilldown", "priority": 1},
    ),
    OntologyConcept(
        "scope.board_trend",
        "AbnormalScope",
        "整板趋势异常",
        "实时口径：单板异常点占比达到阈值（≥5% 中风险、≥10% 高风险），不区分方向。",
        (),
        {"judged_by": "realtime", "priority": 1},
    ),
    OntologyConcept(
        "scope.component_multi_pad",
        "AbnormalScope",
        "同元件多Pad异常",
        "同一元件多个 Pad 同步异常，优先判断局部贴合、堵孔、支撑或污染。",
        ("同一元件多Pad异常",),
        {"judged_by": "realtime+drilldown", "priority": 2},
    ),
    OntologyConcept(
        "scope.consecutive_same_pad",
        "AbnormalScope",
        "连续3板同点异常",
        "下钻触发口径：同一产品、元件、Pad 连续（中间无 PASS 生产板）≥3 块生产板同方向异常，复测不计入。",
        ("连续同点异常",),
        {"judged_by": "drilldown", "priority": 3},
    ),
    OntologyConcept(
        "scope.repeated_same_pad",
        "AbnormalScope",
        "同点多板异常",
        "实时口径：同一产品、元件、Pad 在 ≥3 块不同生产板重复异常，不要求连续。",
        (),
        {"judged_by": "realtime", "priority": 3},
    ),
    OntologyConcept(
        "scope.single_pad_isolated",
        "AbnormalScope",
        "单Pad孤立异常",
        "下钻口径：连续触发局限于单 Pad，同元件其他 Pad 与全量窗口均未见扩散。",
        (),
        {"judged_by": "drilldown", "priority": 4},
    ),
    OntologyConcept(
        "scope.single_point_random",
        "AbnormalScope",
        "单点偶发异常",
        "实时口径：单个 Pad 偶发异常，优先做快速复核和短程复判。",
        ("单点偶发",),
        {"judged_by": "realtime", "priority": 4},
    ),
    OntologyConcept(
        "scope.local_area",
        "AbnormalScope",
        "局部区域",
        "同一局部区域多个 Pad 或元件异常，优先判断局部钢网、PCB 支撑和贴合状态。",
        ("区域异常",),
        {"judged_by": "drilldown"},
    ),
    OntologyConcept(
        "scope.suspected_spi_false_alarm",
        "AbnormalScope",
        "疑似SPI假异常",
        "排除项驱动的归类：NG 标签与主指标偏差不一致，先复核 SPI 程序/识别框/阈值，再谈物理根因。",
        (),
        {"judged_by": "drilldown"},
    ),
    # -- Root cause candidates ------------------------------------------------
    OntologyConcept(
        "root_cause.stencil_single_aperture_residue",
        "RootCauseCandidate",
        "钢网单孔底部残锡或开口异常",
        "单 Pad 多锡的高频候选根因。",
    ),
    OntologyConcept(
        "root_cause.stencil_single_aperture_blockage",
        "RootCauseCandidate",
        "钢网单孔堵塞或脱模不良",
        "单 Pad 少锡的高频候选根因。",
    ),
    OntologyConcept(
        "root_cause.component_area_stencil_contamination",
        "RootCauseCandidate",
        "元件区域钢网底部污染或贴合不良",
        "同元件多 Pad 多锡的候选根因。",
    ),
    OntologyConcept(
        "root_cause.component_area_blockage_or_support",
        "RootCauseCandidate",
        "元件区域堵孔或局部支撑不足",
        "同元件多 Pad 少锡的候选根因。",
    ),
    OntologyConcept(
        "root_cause.local_stencil_contamination",
        "RootCauseCandidate",
        "局部钢网污染、变形或支撑异常",
        "局部区域多锡的候选根因。",
    ),
    OntologyConcept(
        "root_cause.local_stencil_blockage_or_rolling",
        "RootCauseCandidate",
        "局部钢网堵塞、锡膏滚动或支撑异常",
        "局部区域少锡的候选根因。",
    ),
    OntologyConcept(
        "root_cause.board_printing_condition_drift",
        "RootCauseCandidate",
        "整板印刷条件或锡膏状态漂移",
        "整板同向多锡的候选根因。",
    ),
    OntologyConcept(
        "root_cause.board_paste_supply_or_print_action",
        "RootCauseCandidate",
        "锡膏供给中断或整板印刷动作异常",
        "整板同向少锡的候选根因。",
    ),
    OntologyConcept(
        "root_cause.spi_program_false_alarm",
        "RootCauseCandidate",
        "SPI程序阈值或识别框异常",
        "主指标偏差不支撑 NG 标签时的优先排除项。",
    ),
    OntologyConcept(
        "root_cause.parameter_setpoint_drift",
        "RootCauseCandidate",
        "印刷参数实际值偏离设定",
        "触发板印刷参数实际-计划偏差超出基线时的候选根因。",
    ),
    OntologyConcept(
        "root_cause.printing_program_mismatch",
        "RootCauseCandidate",
        "印刷程序设定不适配",
        "参数调整后异常随即恢复时的候选根因。",
    ),
    OntologyConcept(
        "root_cause.maintenance_cycle_mismatch",
        "RootCauseCandidate",
        "钢网清洗或锡膏维护周期不匹配",
        "NG 连段按固定节拍复发时的候选根因。",
    ),
    OntologyConcept(
        "root_cause.cumulative_state_degradation",
        "RootCauseCandidate",
        "随生产累积的钢网或锡膏状态劣化",
        "触发前指标持续爬升（渐变失效）时的候选根因。",
    ),
    OntologyConcept(
        "root_cause.discrete_process_change",
        "RootCauseCandidate",
        "触发时点的离散制程变化",
        "无事前爬升、指标突跳（突变失效）时的候选根因。",
    ),
    OntologyConcept(
        "root_cause.local_printing_state",
        "RootCauseCandidate",
        "局部印刷状态异常",
        "证据不足以锁定单一物理原因时的兜底判断。",
    ),
    # -- Evidence types -------------------------------------------------------
    OntologyConcept(
        "evidence.full_spi_window",
        "EvidenceType",
        "前后500条全量SPI窗口",
        "围绕触发事件抽取的上下文窗口，用于范围、趋势、复判和排除检查。",
    ),
    OntologyConcept(
        "evidence.same_pad_consecutive_ng",
        "EvidenceType",
        "同Pad连续NG证据",
        "同产品、同元件、同 Pad 连续生产板同方向 NG。",
    ),
    OntologyConcept(
        "evidence.component_multi_pad_ng",
        "EvidenceType",
        "同元件多Pad证据",
        "同一元件多个 Pad 在同一窗口内同步异常。",
    ),
    OntologyConcept(
        "evidence.board_same_direction_trend",
        "EvidenceType",
        "整板同向趋势证据",
        "整板多个位置呈现同方向异常或异常比例升高。",
    ),
    OntologyConcept(
        "evidence.parameter_drift",
        "EvidenceType",
        "参数偏离证据",
        "印刷参数实际值相对计划值或历史基线出现偏离。",
    ),
    OntologyConcept(
        "evidence.recovery",
        "EvidenceType",
        "恢复性证据",
        "触发后是否恢复，以及恢复是否与处置或参数变化对齐。",
    ),
    OntologyConcept(
        "evidence.data_continuity",
        "EvidenceType",
        "数据连续性证据",
        "检查生产板序、时间窗口和复测记录是否支撑当前判断。",
    ),
    OntologyConcept(
        "evidence.raw_spi_image",
        "EvidenceType",
        "原始SPI图像",
        "用于确认测量框、Gerber 对位、阈值和实物异常真实性。",
    ),
    # -- Actions --------------------------------------------------------------
    OntologyConcept(
        "action.inspect_single_stencil_aperture",
        "ActionType",
        "检查单个钢网孔",
        "检查触发 Pad 对应钢网孔底部残锡、孔壁、开口尺寸和清洁状态。",
    ),
    OntologyConcept(
        "action.clean_blocked_aperture",
        "ActionType",
        "清洁堵塞钢网孔",
        "显微检查并清洁对应钢网孔，确认通透性和脱模条件。",
    ),
    OntologyConcept(
        "action.inspect_component_area",
        "ActionType",
        "检查元件区域印刷条件",
        "检查元件区域钢网底部污染、局部贴合、PCB 支撑和平整度。",
    ),
    OntologyConcept(
        "action.review_board_printing_conditions",
        "ActionType",
        "复核整板印刷条件",
        "复核锡膏状态、刮刀参数、钢网底面和整板印刷动作。",
    ),
    OntologyConcept(
        "action.review_raw_spi_image",
        "ActionType",
        "复核原始SPI图像",
        "复核测量框、Gerber 对位、上下限和主指标偏差。",
    ),
    # -- Dispositions ----------------------------------------------------------
    OntologyConcept(
        "disposition.immediate_field_check",
        "Disposition",
        "立即现场排查并跟踪复判",
        "异常范围扩大或风险较高时的处置口径。",
        (),
        {"priority": "P1"},
    ),
    OntologyConcept(
        "disposition.fast_single_point_check",
        "Disposition",
        "按单点异常做快速确认",
        "低扩散风险时的处置口径。",
        (),
        {"priority": "P3"},
    ),
]

RELATIONS = [
    OntologyRelation("process.solder_paste_printing", "verified_by", "inspection.spi"),
    OntologyRelation("inspection.spi", "observes", "defect.over_volume"),
    OntologyRelation("inspection.spi", "observes", "defect.insufficient_volume"),
    # scope -> candidate causes（多锡/少锡两个方向的候选都挂在范围上，
    # 方向到具体规则的绑定在 knowledge_base 的规则条件里）
    OntologyRelation("scope.single_pad_isolated", "has_candidate_cause", "root_cause.stencil_single_aperture_residue"),
    OntologyRelation("scope.single_pad_isolated", "has_candidate_cause", "root_cause.stencil_single_aperture_blockage"),
    OntologyRelation("scope.component_multi_pad", "has_candidate_cause", "root_cause.component_area_stencil_contamination"),
    OntologyRelation("scope.component_multi_pad", "has_candidate_cause", "root_cause.component_area_blockage_or_support"),
    OntologyRelation("scope.local_area", "has_candidate_cause", "root_cause.local_stencil_contamination"),
    OntologyRelation("scope.local_area", "has_candidate_cause", "root_cause.local_stencil_blockage_or_rolling"),
    OntologyRelation("scope.board_same_direction", "has_candidate_cause", "root_cause.board_printing_condition_drift"),
    OntologyRelation("scope.board_same_direction", "has_candidate_cause", "root_cause.board_paste_supply_or_print_action"),
    OntologyRelation("scope.suspected_spi_false_alarm", "has_candidate_cause", "root_cause.spi_program_false_alarm"),
    # scope -> required evidence
    OntologyRelation("scope.consecutive_same_pad", "requires_evidence", "evidence.full_spi_window"),
    OntologyRelation("scope.consecutive_same_pad", "requires_evidence", "evidence.same_pad_consecutive_ng"),
    OntologyRelation("scope.component_multi_pad", "requires_evidence", "evidence.component_multi_pad_ng"),
    OntologyRelation("scope.board_same_direction", "requires_evidence", "evidence.board_same_direction_trend"),
    OntologyRelation("scope.suspected_spi_false_alarm", "requires_evidence", "evidence.raw_spi_image"),
    # scope -> disposition
    OntologyRelation("scope.board_same_direction", "uses_disposition", "disposition.immediate_field_check"),
    OntologyRelation("scope.local_area", "uses_disposition", "disposition.immediate_field_check"),
    OntologyRelation("scope.single_point_random", "uses_disposition", "disposition.fast_single_point_check"),
    # root cause -> evidence / action
    OntologyRelation("root_cause.spi_program_false_alarm", "requires_evidence", "evidence.raw_spi_image"),
    OntologyRelation("root_cause.parameter_setpoint_drift", "requires_evidence", "evidence.parameter_drift"),
    OntologyRelation("root_cause.printing_program_mismatch", "requires_evidence", "evidence.recovery"),
    OntologyRelation("root_cause.stencil_single_aperture_residue", "recommends_action", "action.inspect_single_stencil_aperture"),
    OntologyRelation("root_cause.stencil_single_aperture_blockage", "recommends_action", "action.clean_blocked_aperture"),
    OntologyRelation("root_cause.component_area_stencil_contamination", "recommends_action", "action.inspect_component_area"),
    OntologyRelation("root_cause.component_area_blockage_or_support", "recommends_action", "action.inspect_component_area"),
    OntologyRelation("root_cause.board_printing_condition_drift", "recommends_action", "action.review_board_printing_conditions"),
    OntologyRelation("root_cause.board_paste_supply_or_print_action", "recommends_action", "action.review_board_printing_conditions"),
    OntologyRelation("root_cause.spi_program_false_alarm", "recommends_action", "action.review_raw_spi_image"),
]


def _label_to_id(concept_type: str) -> dict[str, str]:
    """Label/alias -> concept ID for one concept type, generated from CONCEPTS."""
    mapping: dict[str, str] = {}
    for concept in CONCEPTS:
        if concept.type != concept_type:
            continue
        mapping[concept.label] = concept.id
        for alias in concept.aliases:
            mapping.setdefault(alias, concept.id)
    return mapping


DIRECTION_TO_CONCEPT_ID = _label_to_id("DefectDirection")
SCOPE_TO_CONCEPT_ID = _label_to_id("AbnormalScope")
CAUSE_TO_CONCEPT_ID = _label_to_id("RootCauseCandidate")

SCOPE_LABELS = tuple(
    concept.label for concept in CONCEPTS if concept.type == "AbnormalScope"
)


def concept_by_id(concept_id: str) -> dict[str, Any] | None:
    for concept in CONCEPTS:
        if concept.id == concept_id:
            return concept.to_dict()
    return None


def ontology_snapshot() -> dict[str, Any]:
    return {
        "version": ONTOLOGY_VERSION,
        "focus": ONTOLOGY_FOCUS,
        "concepts": [concept.to_dict() for concept in CONCEPTS],
        "relations": [relation.to_dict() for relation in RELATIONS],
        "mappings": {
            "direction": DIRECTION_TO_CONCEPT_ID,
            "scope": SCOPE_TO_CONCEPT_ID,
            "cause": CAUSE_TO_CONCEPT_ID,
        },
    }


def ontology_ids_for(
    direction: str | None = None,
    scope: str | None = None,
    cause: str | None = None,
) -> dict[str, str]:
    ids = {}
    if direction and direction in DIRECTION_TO_CONCEPT_ID:
        ids["direction"] = DIRECTION_TO_CONCEPT_ID[direction]
    if scope and scope in SCOPE_TO_CONCEPT_ID:
        ids["scope"] = SCOPE_TO_CONCEPT_ID[scope]
    if cause and cause in CAUSE_TO_CONCEPT_ID:
        ids["cause"] = CAUSE_TO_CONCEPT_ID[cause]
    return ids


# --- Turtle export -----------------------------------------------------------

_TTL_PREDICATES = {
    "verified_by": "verifiedBy",
    "observes": "observes",
    "has_candidate_cause": "hasCandidateCause",
    "requires_evidence": "requiresEvidence",
    "recommends_action": "recommendsAction",
    "uses_disposition": "usesDisposition",
}

_TTL_CLASS_LABELS = {
    "ProcessStep": "工序",
    "InspectionMethod": "检测方法",
    "DefectDirection": "缺陷方向",
    "AbnormalScope": "异常范围",
    "RootCauseCandidate": "根因候选",
    "EvidenceType": "证据类型",
    "ActionType": "排查动作",
    "Disposition": "处置方式",
}

_TTL_PROPERTY_LABELS = {
    "verifiedBy": "由...验证",
    "observes": "观察到",
    "hasCandidateCause": "候选根因为",
    "requiresEvidence": "需要证据",
    "recommendsAction": "建议动作",
    "usesDisposition": "使用处置方式",
    "priority": "优先级",
    "direction": "缺陷方向值",
    "judgedBy": "判定口径",
    "version": "版本",
    "focus": "聚焦范围",
}

_TTL_PROPERTY_KEYS = {"priority": "priority", "direction": "direction", "judged_by": "judgedBy"}


def _ttl_literal(value: Any) -> str:
    if isinstance(value, int):
        return str(value)
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"@zh'


def to_turtle() -> str:
    lines = [
        "@prefix smt: <https://example.com/smt-quality-agent/ontology#> .",
        "@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .",
        "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .",
        "@prefix skos: <http://www.w3.org/2004/02/skos/core#> .",
        "",
        "# Generated from smt_quality_agent/ontology.py — do not edit by hand.",
        "# Regenerate with: python3 -m smt_quality_agent.ontology",
        "",
        "smt:SMTQualityOntology",
        "    rdf:type smt:Ontology ;",
        '    rdfs:label "SMT Quality Agent Ontology"@en ;',
        f'    rdfs:comment {_ttl_literal(ONTOLOGY_FOCUS + "本体。")} ;',
        f'    smt:version "{ONTOLOGY_VERSION}" ;',
        f"    smt:focus {_ttl_literal(ONTOLOGY_FOCUS)} .",
        "",
    ]
    for class_name, label in _TTL_CLASS_LABELS.items():
        lines.append(f"smt:{class_name} rdf:type rdfs:Class ; rdfs:label {_ttl_literal(label)} .")
    lines.append("")
    for prop, label in _TTL_PROPERTY_LABELS.items():
        lines.append(f"smt:{prop} rdf:type rdf:Property ; rdfs:label {_ttl_literal(label)} .")
    lines.append("")

    relations_by_subject: dict[str, list[OntologyRelation]] = {}
    for relation in RELATIONS:
        relations_by_subject.setdefault(relation.subject, []).append(relation)

    for concept in CONCEPTS:
        entry = [
            f"smt:{concept.id}",
            f"    rdf:type smt:{concept.type} ;",
            f"    rdfs:label {_ttl_literal(concept.label)} ;",
            f"    rdfs:comment {_ttl_literal(concept.description)} ;",
        ]
        if concept.aliases:
            alt = ", ".join(_ttl_literal(alias) for alias in concept.aliases)
            entry.append(f"    skos:altLabel {alt} ;")
        for key, value in (concept.properties or {}).items():
            predicate = _TTL_PROPERTY_KEYS.get(key, key)
            entry.append(f"    smt:{predicate} {_ttl_literal(value)} ;")
        for relation in relations_by_subject.get(concept.id, []):
            predicate = _TTL_PREDICATES[relation.predicate]
            entry.append(f"    smt:{predicate} smt:{relation.object} ;")
        entry[-1] = entry[-1].rstrip(" ;") + " ."
        lines.extend(entry)
        lines.append("")
    return "\n".join(lines)


def write_turtle(path: str | Path = "docs/smt_quality_ontology.ttl") -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(to_turtle(), encoding="utf-8")
    return target


if __name__ == "__main__":
    print(f"written: {write_turtle()}")
