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

ONTOLOGY_VERSION = "spi-printing-v5"
ONTOLOGY_FOCUS = "锡膏印刷 + SPI 多锡/少锡异常管理"

# 层次(docs/knowledge_model_v3_design.md;v4 收敛,v5 删除全部废弃词表):
#   实体层  ProcessStage / EquipmentElement / Material —— 问题落在哪个物理对象
#   观测层  SpatialExtent / TemporalPattern / DataValidity 三个正交判定轴
#          + EvidenceType(verification: auto|manual, availability)
#          —— 数据里看到了什么,纯计算事实,不含解释
#   机理层  FailureMechanism(部位 × 签名 × 起病 × 可预警性 × 证据 × 规范动作)
#          —— 为什么会这样;机理是根因词表的唯一权威:规则候选的 cause
#          显示文本直接取机理 label,不再各自维护措辞
#   决策层  在 knowledge_base.py(诊断规则引用这里的机理与证据)
#          —— 该怎么判、怎么办,工厂策略,现场可调
# v2 AbnormalScope / v2/v3 RootCauseCandidate 措辞 / v3 ActionType 已于 v5
# 删除(deprecated 词表按计划保留了一个版本);范围的权威表达是三轴组合。


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
    # -- Trend attribution vocabulary ------------------------------------------
    # 根因显示文本 = 机理 label(单源)。此处仅保留机理锁定不了的三条归因措辞:
    # 两条趋势归因(趋势形态不足以锁定物理机理)+ 一条证据不足时的兜底措辞。
    OntologyConcept(
        "root_cause.cumulative_state_degradation",
        "RootCauseCandidate",
        "随生产累积的钢网或锡膏状态劣化",
        "触发前指标持续爬升（渐变失效）时的归因;趋势形态不锁定机理,在用。",
    ),
    OntologyConcept(
        "root_cause.discrete_process_change",
        "RootCauseCandidate",
        "触发时点的离散制程变化",
        "无事前爬升、指标突跳（突变失效）时的归因;趋势形态不锁定机理,在用。",
    ),
    OntologyConcept(
        "root_cause.local_printing_state",
        "RootCauseCandidate",
        "局部印刷状态异常",
        "证据不足以锁定单一物理原因时的兜底判断措辞,在用（挂 mech.undetermined）。",
        (), {"mechanism": "mech.undetermined"},
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
    # -- Dispositions (与 knowledge_base.DISPOSITION_RULES 一一对应的处置词表) ---
    OntologyConcept(
        "disposition.data_continuity_review",
        "Disposition",
        "先复核数据连续性",
        "触发段存在复测/跨机种/板数不足等数据疑点时的处置口径。",
        (),
        {"priority": "P3"},
    ),
    OntologyConcept(
        "disposition.spi_program_review",
        "Disposition",
        "先复核 SPI 程序/图像",
        "主指标偏差不支撑 NG 标签时的处置口径,先排除假异常。",
        (),
        {"priority": "P2"},
    ),
    OntologyConcept(
        "disposition.immediate_field_check",
        "Disposition",
        "立即现场排查并跟踪复判",
        "异常范围扩大或风险较高时的处置口径。",
        (),
        {"priority": "P1"},
    ),
    OntologyConcept(
        "disposition.halt_and_contain",
        "Disposition",
        "立即处置并暂停放行同类风险板",
        "触发后未恢复、继续生产会扩大不良风险时的处置口径。",
        (),
        {"priority": "P1"},
    ),
    OntologyConcept(
        "disposition.confirm_primary_cause",
        "Disposition",
        "按首要根因执行现场确认",
        "证据链较强时直接按首要根因验证的处置口径。",
        (),
        {"priority": "P2"},
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
    # v4 起机理是根因词表与规范动作的单源：label 即候选根因显示文本,
    # action 是该机理的规范首选动作(场景化动作由规则覆盖)。
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
         "typical_spatial": ["spatial.single_pad", "spatial.component_multi_pad",
                             "spatial.local_area"],
         "typical_temporal": ["temporal.consecutive", "temporal.periodic"],
         "early_warning": "可预警：体积偏差 EWMA 渐变爬升",
         "action": "显微检查并清洁对应钢网开口,确认通透性与孔壁状态;清洁后连续复判确认恢复。",
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
         "action": "核对脱模速度/距离/延时的设定与实际值,检查孔壁与 PCB 支撑,恢复基准后首件确认。",
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
         "action": "清洁钢网底面并复测下一块板;核对擦网周期与擦网耗材状态。",
         "auto_checks": ["evidence.trend_slope", "evidence.cleaning_marker"],
         "manual_checks": ["evidence.stencil_underside_check"]},
    ),
    OntologyConcept(
        "mech.poor_gasketing", "FailureMechanism", "钢网-PCB密合不良",
        "钢网与 Pad 密合不良(支撑差/板翘/局部变形):渗锡时面积大高度低(多锡),"
        "接触不良时锡膏转移不足(少锡)。",
        (),
        {"element": "element.board_support", "stage": "stage.print_stroke",
         "direction": "双向", "onset": "step",
         "signature": "avdp:flat|up,aadp:up,ahdp:up",
         "signature_text": "面积偏差↑ 高度偏差↑（体积平/↑）",
         "typical_spatial": ["spatial.local_area", "spatial.component_multi_pad"],
         "typical_temporal": ["temporal.consecutive", "temporal.repeated"],
         "early_warning": "",
         "action": "检查 PCB 支撑、夹持与板翘,确认钢网-PCB 贴合;必要时调整支撑布局后首件确认。",
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
         "action": "检查锡膏回温/搅拌/使用时长与环境温湿度,确认流变状态;必要时更换锡膏。",
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
         "action": "检查锡膏回温、搅拌、开封使用时长,确认黏度状态;必要时更换锡膏并首件确认。",
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
         "action": "确认钢网上锡膏余量与该板印刷行程是否完整,调取印刷机该周期日志;补膏后复测下一块板。",
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
         "action": "核对印刷参数设定值与实际值及变更审批记录,现场确认后恢复基准并首件确认。",
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
         "action": "按印刷方向分组比较指标,现场检查前/后刮刀压力平衡与刃口磨损,现场确认后再调整。",
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
         "action": "核对擦网频率与擦网纸/溶剂/真空清洁效果,必要时立即手动清洁并将擦网周期提前一档验证。",
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
         "action": "复核 Fiducial 识别、Gerber/钢网/PCB 对位与 MarkDeviation 趋势,再做首件确认。",
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
         "action": "复核原始 SPI 图像、测量框、Gerber 对位与该 Pad 阈值;确认实物异常前不调整印刷参数。",
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
         "action": "复核触发 Pad 原始 SPI 图像与对应钢网开口,复测下一块板确认是否重复。",
         "auto_checks": [],
         "manual_checks": ["evidence.raw_spi_image", "evidence.microscope_aperture"]},
    ),
]

# --- Relations ---------------------------------------------------------------
# v4 起关系不再手写业务边(v3 的 scope→cause/evidence/disposition 手写边引用
# 废弃概念且与规则内容重复,已删)。骨架边保持静态;机理→缺陷方向边由机理的
# direction 属性生成,保证图与机理目录永不漂移。机理→部位/阶段/证据的边
# 直接由机理 properties 表达(TTL 渲染为资源引用),不在此重复。

_STATIC_RELATIONS = [
    OntologyRelation("process.solder_paste_printing", "verified_by", "inspection.spi"),
    OntologyRelation("inspection.spi", "observes", "defect.over_volume"),
    OntologyRelation("inspection.spi", "observes", "defect.insufficient_volume"),
]

_DIRECTION_DEFECT_IDS = {
    "多锡": ("defect.over_volume",),
    "少锡": ("defect.insufficient_volume",),
    "双向": ("defect.over_volume", "defect.insufficient_volume"),
}


def _generated_relations() -> list[OntologyRelation]:
    relations = []
    for concept in CONCEPTS:
        if concept.type != "FailureMechanism":
            continue
        direction = (concept.properties or {}).get("direction", "")
        for defect_id in _DIRECTION_DEFECT_IDS.get(direction, ()):
            relations.append(OntologyRelation(concept.id, "causes_defect", defect_id))
    return relations


RELATIONS = [*_STATIC_RELATIONS, *_generated_relations()]


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
# 根因词表：机理 label 是权威;RootCauseCandidate 只剩趋势归因/兜底三条在用措辞。
CAUSE_TO_CONCEPT_ID = {**_label_to_id("RootCauseCandidate"), **_label_to_id("FailureMechanism")}

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
            "cause": CAUSE_TO_CONCEPT_ID,
        },
    }


def ontology_ids_for(
    direction: str | None = None,
    cause: str | None = None,
) -> dict[str, str]:
    """范围没有单独 ID——权威表达是契约 scope 里的三轴概念 ID 组合。"""
    ids = {}
    if direction and direction in DIRECTION_TO_CONCEPT_ID:
        ids["direction"] = DIRECTION_TO_CONCEPT_ID[direction]
    if cause and cause in CAUSE_TO_CONCEPT_ID:
        ids["cause"] = CAUSE_TO_CONCEPT_ID[cause]
    return ids


# --- Turtle export -----------------------------------------------------------

_TTL_PREDICATES = {
    "verified_by": "verifiedBy",
    "observes": "observes",
    "causes_defect": "causesDefect",
}

_TTL_CLASS_LABELS = {
    "ProcessStep": "工序",
    "InspectionMethod": "检测方法",
    "DefectDirection": "缺陷方向",
    "ProcessStage": "工序阶段",
    "EquipmentElement": "设备要素",
    "Material": "物料",
    "SpatialExtent": "空间范围",
    "TemporalPattern": "时间模式",
    "DataValidity": "数据有效性",
    "RootCauseCandidate": "趋势归因/兜底措辞(机理锁定不了时使用)",
    "FailureMechanism": "失效机理",
    "EvidenceType": "证据类型",
    "Disposition": "处置方式",
}

_TTL_PROPERTY_LABELS = {
    "verifiedBy": "由...验证",
    "observes": "观察到",
    "causesDefect": "致缺陷方向",
    "priority": "优先级",
    "direction": "缺陷方向值",
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
    "canonicalAction": "规范动作",
    "autoCheck": "自动核验证据",
    "manualCheck": "人工确认证据",
    "expressesMechanism": "对应机理",
    "version": "版本",
    "focus": "聚焦范围",
}

_TTL_PROPERTY_KEYS = {
    "priority": "priority",
    "direction": "direction",
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
    "action": "canonicalAction",
    "auto_checks": "autoCheck",
    "manual_checks": "manualCheck",
    "mechanism": "expressesMechanism",
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
