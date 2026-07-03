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

ONTOLOGY_VERSION = "spi-printing-v3"
ONTOLOGY_FOCUS = "锡膏印刷 + SPI 多锡/少锡异常管理"

# v3 层次(docs/knowledge_model_v3_design.md):
#   实体层  ProcessStage / EquipmentElement / Material
#   观测层  SpatialExtent / TemporalPattern / DataValidity 三个正交判定轴
#          + EvidenceType(verification: auto|manual, availability)
#   机理层  FailureMechanism(部位 × 签名 × 起病 × 可预警性 × 证据)
#   决策层  在 knowledge_base.py(诊断规则引用这里的机理与证据)
# v2 的 AbnormalScope 概念保留一个版本(properties.deprecated=true),
# 显示标签仍在用,三轴是权威表达。


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
        {"judged_by": "drilldown", "priority": 1, "deprecated": True},
    ),
    OntologyConcept(
        "scope.board_trend",
        "AbnormalScope",
        "整板趋势异常",
        "实时口径：单板异常点占比达到阈值（≥5% 中风险、≥10% 高风险），不区分方向。",
        (),
        {"judged_by": "realtime", "priority": 1, "deprecated": True},
    ),
    OntologyConcept(
        "scope.component_multi_pad",
        "AbnormalScope",
        "同元件多Pad异常",
        "同一元件多个 Pad 同步异常，优先判断局部贴合、堵孔、支撑或污染。",
        ("同一元件多Pad异常",),
        {"judged_by": "realtime+drilldown", "priority": 2, "deprecated": True},
    ),
    OntologyConcept(
        "scope.consecutive_same_pad",
        "AbnormalScope",
        "连续3板同点异常",
        "下钻触发口径：同一产品、元件、Pad 连续（中间无 PASS 生产板）≥3 块生产板同方向异常，复测不计入。",
        ("连续同点异常",),
        {"judged_by": "drilldown", "priority": 3, "deprecated": True},
    ),
    OntologyConcept(
        "scope.repeated_same_pad",
        "AbnormalScope",
        "同点多板异常",
        "实时口径：同一产品、元件、Pad 在 ≥3 块不同生产板重复异常，不要求连续。",
        (),
        {"judged_by": "realtime", "priority": 3, "deprecated": True},
    ),
    OntologyConcept(
        "scope.single_pad_isolated",
        "AbnormalScope",
        "单Pad孤立异常",
        "下钻口径：连续触发局限于单 Pad，同元件其他 Pad 与全量窗口均未见扩散。",
        (),
        {"judged_by": "drilldown", "priority": 4, "deprecated": True},
    ),
    OntologyConcept(
        "scope.single_point_random",
        "AbnormalScope",
        "单点偶发异常",
        "实时口径：单个 Pad 偶发异常，优先做快速复核和短程复判。",
        ("单点偶发",),
        {"judged_by": "realtime", "priority": 4, "deprecated": True},
    ),
    OntologyConcept(
        "scope.local_area",
        "AbnormalScope",
        "局部区域",
        "同一局部区域多个 Pad 或元件异常，优先判断局部钢网、PCB 支撑和贴合状态。",
        ("区域异常",),
        {"judged_by": "drilldown", "deprecated": True},
    ),
    OntologyConcept(
        "scope.suspected_spi_false_alarm",
        "AbnormalScope",
        "疑似SPI假异常",
        "排除项驱动的归类：NG 标签与主指标偏差不一致，先复核 SPI 程序/识别框/阈值，再谈物理根因。",
        (),
        {"judged_by": "drilldown", "deprecated": True},
    ),
    # -- Entity layer (骨架；台账化等接入 MES/设备数据后再展开) -----------------
    OntologyConcept("stage.alignment", "ProcessStage", "对位",
                    "印刷循环第一段：视觉识别 Fiducial，钢网与 PCB 对位。"),
    OntologyConcept("stage.print_stroke", "ProcessStage", "印刷行程",
                    "刮刀带动锡膏滚动填充钢网开口的行程段。"),
    OntologyConcept("stage.release", "ProcessStage", "脱模",
                    "PCB 与钢网分离、锡膏从开口转移到 Pad 的阶段。"),
    OntologyConcept("stage.cleaning_cycle", "ProcessStage", "擦网周期",
                    "按设定频率进行的钢网底面自动清洁循环。"),
    OntologyConcept("element.stencil_aperture", "EquipmentElement", "钢网开口",
                    "单个钢网孔：开口尺寸、孔壁状态、通透性。"),
    OntologyConcept("element.stencil_underside", "EquipmentElement", "钢网底面",
                    "钢网与 PCB 接触面：残锡、污染、清洁状态。"),
    OntologyConcept("element.squeegee", "EquipmentElement", "刮刀",
                    "前/后刮刀：压力、角度、磨损状态。"),
    OntologyConcept("element.board_support", "EquipmentElement", "支撑与夹持",
                    "PCB 支撑、夹持、板翘,决定钢网-PCB 密合。"),
    OntologyConcept("element.cleaning_system", "EquipmentElement", "擦网系统",
                    "自动擦网机构：擦网纸、溶剂、真空。"),
    OntologyConcept("element.vision_alignment", "EquipmentElement", "视觉对位系统",
                    "Fiducial 识别与对位机构。"),
    OntologyConcept("element.printing_program", "EquipmentElement", "印刷程序",
                    "印刷机参数设定：速度、压力、脱模、擦网频率等。"),
    OntologyConcept("element.spi_program", "EquipmentElement", "SPI程序",
                    "SPI 测量框、阈值、Gerber 对位设置。"),
    OntologyConcept("material.solder_paste", "Material", "锡膏",
                    "印刷物料：回温、搅拌、使用时长、流变状态。"),
    # -- Observation axes (三个正交判定轴,v3 的权威范围表达) --------------------
    OntologyConcept("spatial.single_pad", "SpatialExtent", "单Pad",
                    "异常局限于单个 Pad,同元件其他 Pad 与窗口未扩散。"),
    OntologyConcept("spatial.component_multi_pad", "SpatialExtent", "同元件多Pad",
                    "同一元件 ≥2 个 Pad 同板同向异常。"),
    OntologyConcept("spatial.local_area", "SpatialExtent", "局部区域",
                    "≥3 个不同 NG Pad 聚集在 ≤35% 坐标跨度内。"),
    OntologyConcept("spatial.board_wide", "SpatialExtent", "整板",
                    "板 NG 占比 ≥50% 且板记录行数 ≥10。"),
    OntologyConcept("temporal.sporadic", "TemporalPattern", "偶发",
                    "单板出现,无重复。"),
    OntologyConcept("temporal.consecutive", "TemporalPattern", "连续N板",
                    "中间无 PASS 生产板的连续 ≥3 板(下钻触发口径,复测不计)。"),
    OntologyConcept("temporal.repeated", "TemporalPattern", "跨板重复",
                    "≥3 块不同生产板重复,不要求连续(实时口径)。"),
    OntologyConcept("temporal.periodic", "TemporalPattern", "周期复发",
                    "NG 连段以近似固定间隔重复出现。"),
    OntologyConcept("validity.valid", "DataValidity", "数据可信",
                    "数据连续性与 SPI 标签一致性检查均通过。"),
    OntologyConcept("validity.spi_suspect", "DataValidity", "疑似SPI误判",
                    "NG 标签但主指标偏差不显著,需先复核 SPI 程序。"),
    OntologyConcept("validity.data_suspect", "DataValidity", "数据连续性存疑",
                    "触发段含复测/跨机种/板数不足等数据疑点。"),
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
    # verification: auto=数据表内一条查询能回答 / manual=需现场人工确认。
    # availability(仅 auto): available=已实现 / planned=数据有、待实现 /
    # not_collected=字段存在但当前导出未采集(2026-07-03 逐列核实)。
    OntologyConcept("evidence.full_spi_window", "EvidenceType", "前后500条全量SPI窗口",
                    "围绕触发事件抽取的上下文窗口，用于范围、趋势、复判和排除检查。",
                    (), {"verification": "auto", "availability": "available"}),
    OntologyConcept("evidence.same_pad_consecutive_ng", "EvidenceType", "同Pad连续NG证据",
                    "同产品、同元件、同 Pad 连续生产板同方向 NG。",
                    (), {"verification": "auto", "availability": "available"}),
    OntologyConcept("evidence.component_multi_pad_ng", "EvidenceType", "同元件多Pad证据",
                    "同一元件多个 Pad 在同一窗口内同步异常。",
                    (), {"verification": "auto", "availability": "available"}),
    OntologyConcept("evidence.board_same_direction_trend", "EvidenceType", "整板同向趋势证据",
                    "整板多个位置呈现同方向异常或异常比例升高。",
                    (), {"verification": "auto", "availability": "available"}),
    OntologyConcept("evidence.parameter_drift", "EvidenceType", "参数偏离证据",
                    "印刷参数实际值相对计划值或历史基线出现偏离。",
                    (), {"verification": "auto", "availability": "available"}),
    OntologyConcept("evidence.recovery", "EvidenceType", "恢复性证据",
                    "触发后是否恢复，以及恢复是否与处置或参数变化对齐。",
                    (), {"verification": "auto", "availability": "available"}),
    OntologyConcept("evidence.data_continuity", "EvidenceType", "数据连续性证据",
                    "检查生产板序、时间窗口和复测记录是否支撑当前判断。",
                    (), {"verification": "auto", "availability": "available"}),
    OntologyConcept("evidence.trend_slope", "EvidenceType", "触发前趋势斜率",
                    "触发前该 Pad 指标的爬升斜率与末段连升板数(渐变/突变判别)。",
                    (), {"verification": "auto", "availability": "available"}),
    OntologyConcept("evidence.periodic_recurrence", "EvidenceType", "NG连段周期性复发",
                    "历史 NG 连段间隔的变异系数低于阈值,呈固定节拍。",
                    (), {"verification": "auto", "availability": "available"}),
    OntologyConcept("evidence.spi_metric_mismatch", "EvidenceType", "NG标签与主指标不符",
                    "记录判 NG 但主指标偏差 <20%,提示 SPI 程序误判。",
                    (), {"verification": "auto", "availability": "available"}),
    OntologyConcept("evidence.metric_signature", "EvidenceType", "三指标签名",
                    "体积/面积/高度偏差组合特征与机理签名的匹配度,判界 max(3σ, 10pp)。",
                    (), {"verification": "auto", "availability": "available"}),
    OntologyConcept("evidence.cleaning_frequency_reference", "EvidenceType", "擦网频率参照",
                    "NG 连段间隔与 CleaningFrequency 设定值的整数倍关系(容差 20%)。",
                    (), {"verification": "auto", "availability": "available"}),
    OntologyConcept("evidence.mark_deviation_trend", "EvidenceType", "MarkDeviation趋势",
                    "机器级对位偏差随板序的趋势(第二阶段)。",
                    (), {"verification": "auto", "availability": "planned"}),
    OntologyConcept("evidence.cleaning_marker", "EvidenceType", "擦网事件对齐",
                    "NG 连段与真实擦网事件的对齐(CleaningAfterLastBoard 当前未采集)。",
                    (), {"verification": "auto", "availability": "not_collected"}),
    OntologyConcept("evidence.print_direction_split", "EvidenceType", "印刷方向分组差异",
                    "NG 按印刷方向分组的偏斜(PrintDirection 当前未采集)。",
                    (), {"verification": "auto", "availability": "not_collected"}),
    OntologyConcept("evidence.raw_spi_image", "EvidenceType", "原始SPI图像",
                    "用于确认测量框、Gerber 对位、阈值和实物异常真实性。",
                    (), {"verification": "manual"}),
    OntologyConcept("evidence.microscope_aperture", "EvidenceType", "显微检查钢网开口",
                    "显微确认开口堵塞、孔壁与开口尺寸。",
                    (), {"verification": "manual"}),
    OntologyConcept("evidence.stencil_underside_check", "EvidenceType", "钢网底面目视检查",
                    "目视/擦拭确认钢网底面残锡与污染。",
                    (), {"verification": "manual"}),
    OntologyConcept("evidence.paste_condition_log", "EvidenceType", "锡膏状态记录",
                    "回温、搅拌、开封使用时长、余量记录(当前无系统数据)。",
                    (), {"verification": "manual"}),
    OntologyConcept("evidence.stencil_version_ecn", "EvidenceType", "钢网版本/ECN记录",
                    "钢网开口设计版本与变更历史。",
                    (), {"verification": "manual"}),
    OntologyConcept("evidence.board_support_check", "EvidenceType", "支撑与板翘检查",
                    "PCB 支撑、夹持、板翘和钢网贴合的现场确认。",
                    (), {"verification": "manual"}),
    OntologyConcept("evidence.change_approval", "EvidenceType", "变更审批记录",
                    "参数/程序变更的审批与执行记录。",
                    (), {"verification": "manual"}),
    OntologyConcept("evidence.squeegee_inspection", "EvidenceType", "刮刀状态检查",
                    "刮刀刃口磨损、压力平衡、安装状态的现场确认。",
                    (), {"verification": "manual"}),
    OntologyConcept("evidence.cleaning_supplies_check", "EvidenceType", "擦网耗材检查",
                    "擦网纸、溶剂、真空状态的现场确认。",
                    (), {"verification": "manual"}),
    OntologyConcept("evidence.alignment_check", "EvidenceType", "Fiducial/对位复核",
                    "视觉识别、Gerber/钢网/PCB 对位的现场复核。",
                    (), {"verification": "manual"}),
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
    # -- Failure mechanisms (机理层：可推理、可闭环、可挂预警的失效机理目录) ------
    # signature 机器编码：<指标>:<up|down|flat|any>,多值用 | 分隔。
    # 重要口径(2026-07-03 经 full_excel0623 全表核实)：Comp_avdp/aadp/ahdp 是
    # **无符号偏差幅度**(全表最小值 0,Under Volume 行均值 40.8 与 Over 同向),
    # 缺陷方向只在 errName 标签里。因此 up=该指标劣化(偏差幅度扩大)、
    # down=回落、flat=不变;签名甄别的是"哪些指标劣化",不是物理升降方向。
    # onset: gradual(渐变,可预警) / step(突变) / periodic(周期) / any。
    OntologyConcept(
        "mech.aperture_clogging", "FailureMechanism", "钢网开口堵塞",
        "锡膏残留逐渐堵塞开口,供锡量随生产递减,单孔或细间距区域少锡。",
        (),
        {"element": "element.stencil_aperture", "stage": "stage.release",
         "direction": "少锡", "onset": "gradual",
         "signature": "avdp:up,aadp:up|flat,ahdp:up|flat",
         "signature_text": "体积偏差↑ 面积偏差↑/平 高度偏差↑/平",
         "typical_spatial": ["spatial.single_pad", "spatial.local_area"],
         "typical_temporal": ["temporal.consecutive", "temporal.periodic"],
         "early_warning": "可预警：体积偏差 EWMA 渐变爬升",
         "auto_checks": ["evidence.trend_slope", "evidence.cleaning_marker"],
         "manual_checks": ["evidence.microscope_aperture", "evidence.raw_spi_image"]},
    ),
    OntologyConcept(
        "mech.poor_release", "FailureMechanism", "脱模不良",
        "脱模速度/距离不适配或孔壁粗糙,锡膏未完整转移,少锡或形状不稳。",
        (),
        {"element": "element.stencil_aperture", "stage": "stage.release",
         "direction": "少锡", "onset": "any",
         "signature": "avdp:up,aadp:flat,ahdp:up",
         "signature_text": "体积偏差↑ 面积偏差平 高度偏差↑",
         "typical_spatial": ["spatial.single_pad", "spatial.local_area"],
         "typical_temporal": ["temporal.consecutive", "temporal.repeated"],
         "early_warning": "",
         "auto_checks": ["evidence.parameter_drift"],
         "manual_checks": ["evidence.microscope_aperture", "evidence.board_support_check"]},
    ),
    OntologyConcept(
        "mech.understencil_residue", "FailureMechanism", "钢网底部残锡转印",
        "钢网底面残锡在后续板转印,多锡且面积偏大,随擦网间隔累积。",
        (),
        {"element": "element.stencil_underside", "stage": "stage.cleaning_cycle",
         "direction": "多锡", "onset": "gradual",
         "signature": "avdp:up,aadp:up,ahdp:flat|up",
         "signature_text": "体积偏差↑ 面积偏差↑ 高度偏差平/↑",
         "typical_spatial": ["spatial.single_pad", "spatial.component_multi_pad"],
         "typical_temporal": ["temporal.consecutive", "temporal.periodic"],
         "early_warning": "可预警：擦网间隔内面积偏差递增",
         "auto_checks": ["evidence.trend_slope", "evidence.cleaning_marker"],
         "manual_checks": ["evidence.stencil_underside_check"]},
    ),
    OntologyConcept(
        "mech.poor_gasketing", "FailureMechanism", "密合不良渗锡",
        "钢网与 Pad 密合不良(支撑差/板翘/局部变形),锡膏从缝隙渗出,面积大高度低。",
        (),
        {"element": "element.board_support", "stage": "stage.print_stroke",
         "direction": "多锡", "onset": "step",
         "signature": "avdp:flat|up,aadp:up,ahdp:up",
         "signature_text": "面积偏差↑ 高度偏差↑（体积平/↑）",
         "typical_spatial": ["spatial.local_area", "spatial.component_multi_pad"],
         "typical_temporal": ["temporal.consecutive", "temporal.repeated"],
         "early_warning": "",
         "auto_checks": [],
         "manual_checks": ["evidence.board_support_check", "evidence.raw_spi_image"]},
    ),
    OntologyConcept(
        "mech.slump", "FailureMechanism", "塌陷",
        "锡膏流变性劣化导致印刷后塌边,面积增大高度下降而体积基本不变。",
        (),
        {"element": "material.solder_paste", "stage": "stage.release",
         "direction": "多锡", "onset": "gradual",
         "signature": "avdp:flat,aadp:up,ahdp:up",
         "signature_text": "体积偏差平 面积偏差↑ 高度偏差↑",
         "typical_spatial": ["spatial.board_wide", "spatial.local_area"],
         "typical_temporal": ["temporal.consecutive"],
         "early_warning": "可预警：高度/面积偏差反向漂移",
         "auto_checks": ["evidence.metric_signature"],
         "manual_checks": ["evidence.paste_condition_log"]},
    ),
    OntologyConcept(
        "mech.paste_rheology_drift", "FailureMechanism", "锡膏流变劣化",
        "锡膏变干/黏度漂移(回温不足、使用超时、环境),整板指标同向渐变。",
        (),
        {"element": "material.solder_paste", "stage": "stage.print_stroke",
         "direction": "双向", "onset": "gradual",
         "signature": "avdp:any,aadp:any,ahdp:any",
         "signature_text": "三指标偏差同向渐变",
         "typical_spatial": ["spatial.board_wide"],
         "typical_temporal": ["temporal.consecutive"],
         "early_warning": "可预警：整板均值 EWMA 漂移",
         "auto_checks": ["evidence.trend_slope"],
         "manual_checks": ["evidence.paste_condition_log"]},
    ),
    OntologyConcept(
        "mech.supply_interruption", "FailureMechanism", "供锡中断/漏印",
        "锡膏余量不足、漏印或单次行程异常,整板突发性大面积少锡。",
        (),
        {"element": "material.solder_paste", "stage": "stage.print_stroke",
         "direction": "少锡", "onset": "step",
         "signature": "avdp:up,aadp:up,ahdp:up",
         "signature_text": "整板三指标偏差同时↑",
         "typical_spatial": ["spatial.board_wide"],
         "typical_temporal": ["temporal.sporadic"],
         "early_warning": "",
         "auto_checks": [],
         "manual_checks": ["evidence.paste_condition_log"]},
    ),
    OntologyConcept(
        "mech.parameter_mismatch", "FailureMechanism", "参数漂移/设定不适配",
        "印刷参数实际值偏离设定,或程序设定本身不适配当前机种/钢网。",
        (),
        {"element": "element.printing_program", "stage": "stage.print_stroke",
         "direction": "双向", "onset": "step",
         "signature": "",
         "signature_text": "视具体参数而定",
         "typical_spatial": ["spatial.board_wide", "spatial.local_area"],
         "typical_temporal": ["temporal.consecutive"],
         "early_warning": "",
         "auto_checks": ["evidence.parameter_drift", "evidence.recovery"],
         "manual_checks": ["evidence.change_approval"]},
    ),
    OntologyConcept(
        "mech.squeegee_one_side", "FailureMechanism", "刮刀单边异常",
        "前/后刮刀压力不平衡或单边磨损,仅某一印刷方向的板异常。",
        (),
        {"element": "element.squeegee", "stage": "stage.print_stroke",
         "direction": "双向", "onset": "any",
         "signature": "",
         "signature_text": "按印刷方向分组呈差异",
         "typical_spatial": ["spatial.board_wide", "spatial.local_area"],
         "typical_temporal": ["temporal.repeated"],
         "early_warning": "",
         "auto_checks": ["evidence.print_direction_split"],
         "manual_checks": ["evidence.squeegee_inspection"]},
    ),
    OntologyConcept(
        "mech.cleaning_cycle_mismatch", "FailureMechanism", "擦网周期不匹配",
        "擦网频率或清洁效果与产出不匹配,NG 以固定节拍周期复发。",
        (),
        {"element": "element.cleaning_system", "stage": "stage.cleaning_cycle",
         "direction": "多锡", "onset": "periodic",
         "signature": "",
         "signature_text": "周期性复发,擦网后复位",
         "typical_spatial": ["spatial.single_pad", "spatial.local_area"],
         "typical_temporal": ["temporal.periodic"],
         "early_warning": "可预警：周期检测 + 擦网频率参照",
         "auto_checks": ["evidence.periodic_recurrence",
                          "evidence.cleaning_frequency_reference",
                          "evidence.cleaning_marker"],
         "manual_checks": ["evidence.cleaning_supplies_check"]},
    ),
    OntologyConcept(
        "mech.alignment_offset", "FailureMechanism", "对位偏移",
        "Fiducial 识别或钢网-PCB 对位偏移;Pad 级 offset 当前不可观测,仅机器级 MarkDeviation。",
        (),
        {"element": "element.vision_alignment", "stage": "stage.alignment",
         "direction": "双向", "onset": "step",
         "signature": "",
         "signature_text": "面积异常伴方向性(观测受限)",
         "typical_spatial": ["spatial.board_wide", "spatial.local_area"],
         "typical_temporal": ["temporal.consecutive"],
         "early_warning": "",
         "auto_checks": ["evidence.mark_deviation_trend"],
         "manual_checks": ["evidence.alignment_check"]},
    ),
    OntologyConcept(
        "mech.spi_false_call", "FailureMechanism", "SPI程序误判",
        "SPI 测量框/阈值/对位设置问题导致的假 NG,非实物异常。",
        (),
        {"element": "element.spi_program", "stage": "stage.alignment",
         "direction": "双向", "onset": "any",
         "signature": "",
         "signature_text": "NG 标签与主指标偏差不符",
         "typical_spatial": ["spatial.single_pad"],
         "typical_temporal": ["temporal.repeated"],
         "early_warning": "",
         "auto_checks": ["evidence.spi_metric_mismatch"],
         "manual_checks": ["evidence.raw_spi_image"]},
    ),
    OntologyConcept(
        "mech.undetermined", "FailureMechanism", "未定机理",
        "证据不足以锁定单一物理机理时的兜底,按局部印刷状态异常处置。",
        (),
        {"element": "element.stencil_aperture", "stage": "stage.print_stroke",
         "direction": "双向", "onset": "any",
         "signature": "",
         "signature_text": "",
         "typical_spatial": [], "typical_temporal": [],
         "early_warning": "",
         "auto_checks": [],
         "manual_checks": ["evidence.raw_spi_image", "evidence.microscope_aperture"]},
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

MECHANISMS = {
    concept.id: concept.to_dict()
    for concept in CONCEPTS if concept.type == "FailureMechanism"
}

_EVIDENCE = {
    concept.id: concept.to_dict()
    for concept in CONCEPTS if concept.type == "EvidenceType"
}

_LABELS_BY_ID = {concept.id: concept.label for concept in CONCEPTS}


def mechanism_by_id(mechanism_id: str) -> dict[str, Any] | None:
    return MECHANISMS.get(mechanism_id)


def concept_label(concept_id: str) -> str:
    """Display label for any concept ID; falls back to the ID itself."""
    return _LABELS_BY_ID.get(concept_id, concept_id)


def evidence_availability(evidence_id: str) -> str:
    """auto 证据返回 available/planned/not_collected;manual 证据返回 manual。"""
    concept = _EVIDENCE.get(evidence_id)
    if not concept:
        return "unknown"
    properties = concept.get("properties") or {}
    if properties.get("verification") == "manual":
        return "manual"
    return properties.get("availability", "unknown")


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
    "AbnormalScope": "异常范围(v2,已废弃)",
    "ProcessStage": "工序阶段",
    "EquipmentElement": "设备要素",
    "Material": "物料",
    "SpatialExtent": "空间范围",
    "TemporalPattern": "时间模式",
    "DataValidity": "数据有效性",
    "RootCauseCandidate": "根因候选",
    "FailureMechanism": "失效机理",
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
    "deprecated": "已废弃",
    "verification": "核验方式",
    "availability": "可得性",
    "affectsElement": "作用部位",
    "occursAtStage": "所属工序阶段",
    "onset": "起病形态",
    "signature": "指标签名",
    "signatureText": "签名描述",
    "typicalSpatial": "典型空间范围",
    "typicalTemporal": "典型时间模式",
    "earlyWarning": "可预警性",
    "autoCheck": "自动核验证据",
    "manualCheck": "人工确认证据",
    "version": "版本",
    "focus": "聚焦范围",
}

_TTL_PROPERTY_KEYS = {
    "priority": "priority",
    "direction": "direction",
    "judged_by": "judgedBy",
    "deprecated": "deprecated",
    "verification": "verification",
    "availability": "availability",
    "element": "affectsElement",
    "stage": "occursAtStage",
    "onset": "onset",
    "signature": "signature",
    "signature_text": "signatureText",
    "typical_spatial": "typicalSpatial",
    "typical_temporal": "typicalTemporal",
    "early_warning": "earlyWarning",
    "auto_checks": "autoCheck",
    "manual_checks": "manualCheck",
}


def _ttl_literal(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"@zh'


def _ttl_value(value: Any, concept_ids: set[str]) -> str | None:
    """Render one property value: concept IDs become resource refs, lists
    become comma-separated object lists, empty values are skipped."""
    if isinstance(value, (list, tuple)):
        rendered = [_ttl_value(item, concept_ids) for item in value]
        rendered = [item for item in rendered if item]
        return ", ".join(rendered) if rendered else None
    if isinstance(value, str):
        if not value:
            return None
        if value in concept_ids:
            return f"smt:{value}"
        return _ttl_literal(value)
    return _ttl_literal(value)


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

    concept_ids = {concept.id for concept in CONCEPTS}
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
            rendered = _ttl_value(value, concept_ids)
            if rendered is not None:
                entry.append(f"    smt:{predicate} {rendered} ;")
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
