# SMT 质量知识模型 v3 设计

状态:已评审通过;**第一、二阶段均已实施**(2026-07-03)。
第一阶段:ontology spi-printing-v3 / rule-catalog-v5 / analysis-contract-v3。
第二阶段:三指标签名甄别(判界 max(3σ,10pp),据真实分布)、参数按机理分组
(SnapOff*/SQG*/Cleaning*)、擦网频率参照(偏离最近整倍数 ≤0.2 周期加权)。
数据侧需求(CleaningAfterLastBoard/PrintDirection/温湿度/Pad级offset)待推进。
前置:2026-07-03 知识治理重构(词表/规则单源化、analysis_contract v2,提交 c6cce58)

---

## 1. 为什么要有 v3

v2(当前版本)解决的是**工程组织**问题:一词一义、规则单源、输出单契约。
但领域模型本身仍有四个结构性缺陷,导致"规则和知识库梳理不明确"的观感:

| # | 缺陷 | 表现 |
|---|------|------|
| 1 | 判定维度未正交化 | "范围"里混了空间范围、时间模式、数据有效性三种性质不同的判断 |
| 2 | 根因是文本标签,不是机理模型 | 根因无法推理、无法闭环、无法挂预警 |
| 3 | 知识未落到数据可观测的证据上 | 数据里有的判据(擦网标记、印刷方向)知识库不知道;知识库要的证据(锡膏日志)一半数据里没有,且不区分能否自动核验 |
| 4 | 组合诊断逻辑在代码里 | `build_conclusion` 的候选收集顺序就是诊断决策树,却以 if/else 形态存在,`/api/rules` 看不到 |

v3 的目标:**把本体从"给输出贴标签的装饰品"变成"驱动分析的骨架"**。

## 2. 目标结构:四层模型

```
┌──────────────────────────────────────────────────────────┐
│ 决策层 Decision   诊断规则(证据组合→机理候选)+ 处置策略      │
├──────────────────────────────────────────────────────────┤
│ 机理层 Mechanism  失效机理 × 作用部位 × 指标签名 × 证据 × 可预警性 │
├──────────────────────────────────────────────────────────┤
│ 观测层 Observation SPI指标/参数列 + 三个正交判定轴 + 趋势形态   │
├──────────────────────────────────────────────────────────┤
│ 实体层 Entity     工序阶段/设备要素/物料/产品结构(本期只建骨架) │
└──────────────────────────────────────────────────────────┘
```

依赖方向自上而下:决策规则引用机理,机理引用观测与实体,反向引用禁止。

### 2.1 实体层(本期只建骨架,不展开)

只注册机理需要挂靠的最小集合,每个是一个本体概念:

- **工序阶段** `stage.*`:对位 → 印刷行程 → 脱模 → 擦网周期(印刷循环的四段)
- **设备要素** `element.*`:钢网开口、钢网底面、刮刀(前/后)、支撑与夹持、擦网系统、视觉对位系统
- **物料** `material.*`:锡膏(状态:回温/搅拌/使用时长)
- **产品结构** `structure.*`:板 → 元件 → Pad(已隐含在数据模型中,显式化)

不建的部分:钢网版本台账、锡膏批次、设备台账——等接入 MES/设备数据再展开,现在建是空壳。

### 2.2 观测层:三个正交判定轴

现有 9 个 `AbnormalScope` 概念拆解为三个独立维度,组合表达诊断上下文:

**轴 1:空间范围 `spatial.*`**

| ID | 标签 | 判据(现有代码已实现) |
|---|---|---|
| `spatial.single_pad` | 单Pad | 同元件其他 Pad 与窗口均未扩散 |
| `spatial.component_multi_pad` | 同元件多Pad | 同元件 ≥2 Pad 同板同向 |
| `spatial.local_area` | 局部区域 | ≥3 个不同 NG Pad 聚集在 ≤35% 坐标跨度内 |
| `spatial.board_wide` | 整板 | 板 NG 占比 ≥50% 且板记录行 ≥10 |

**轴 2:时间模式 `temporal.*`**

| ID | 标签 | 判据 |
|---|---|---|
| `temporal.sporadic` | 偶发 | 单板出现,无重复 |
| `temporal.consecutive` | 连续N板 | 中间无 PASS 生产板的连续 ≥3 板(下钻触发口径,复测不计) |
| `temporal.repeated` | 跨板重复 | ≥3 块不同板重复,不要求连续(实时口径) |
| `temporal.periodic` | 周期复发 | NG 连段间隔变异系数 <0.25(v3 第二阶段改用擦网标记直接核验) |

**轴 3:数据有效性 `validity.*`**

| ID | 标签 | 判据 |
|---|---|---|
| `validity.valid` | 数据可信 | 排除检查全过 |
| `validity.spi_suspect` | 疑似SPI误判 | NG 标签但主指标偏差 <20% |
| `validity.data_suspect` | 数据连续性存疑 | 触发段含复测/跨机种/板数不足 |

**趋势形态**(渐变/突变/未知)作为观测属性输入决策层,不是分类轴。

v2 概念的映射关系(保证迁移可回溯):

| v2 概念 | v3 表达 |
|---|---|
| 单Pad孤立异常 | spatial.single_pad + temporal.consecutive |
| 连续3板同点异常 | spatial.single_pad + temporal.consecutive(同上,是触发条件而非独立范围) |
| 同点多板异常 | spatial.single_pad + temporal.repeated |
| 同元件多Pad异常 | spatial.component_multi_pad(+当时的时间轴取值) |
| 局部区域 | spatial.local_area |
| 整板同向 | spatial.board_wide(下钻口径) |
| 整板趋势异常 | spatial.board_wide + 实时口径判据 |
| 单点偶发异常 | spatial.single_pad + temporal.sporadic |
| 疑似SPI假异常 | validity.spi_suspect(不再占用范围轴) |

UI 展示继续用组合后的中文标签(如"单Pad连续异常"),但契约里三轴分开给。

### 2.3 机理层:失效机理目录(v3 的核心增量)

> **签名口径修正(2026-07-03 第二阶段实施时核实)**:`Comp_avdp/aadp/ahdp`
> 是**无符号偏差幅度**——全表 46587 行三指标最小值均为 0,Under Volume 行
> avdp 均值 40.8 与 Over 同量级。缺陷的物理方向只在 errName 标签里。因此
> 签名统一按"哪些指标劣化(偏差幅度扩大↑)/回落↓/不变(平)"表达,本节
> 表格中的 ↑↓ 均指偏差幅度而非物理锡量方向;权威签名以 ontology.py 为准。

每个机理是一个结构化对象,替代 v2 的根因文本标签:

```
mechanism:
  id:                mech.aperture_clogging
  label:             钢网开口堵塞
  stage:             stage.印刷行程/脱模         # 发生在哪个工序阶段
  element:           element.钢网开口            # 作用部位
  direction:         少锡                        # 造成的缺陷方向
  signature:         体积↓ 面积↓或平 高度↓        # 三指标特征签名
  typical_spatial:   single_pad / local_area     # 典型空间分布
  typical_temporal:  consecutive / periodic      # 典型时间模式
  onset:             gradual                     # 渐变/突变 → 决定可预警性
  early_warning:     可预警(体积偏差 EWMA)        # P0 挂点
  auto_checks:       [擦网后是否复位, 触发前趋势斜率]  # 数据内可自动核验
  manual_checks:     [显微检查开口, 清洁后印3块验证]   # 需现场人工
  action:            清洁对应钢网孔,确认通透性和脱模条件
```

**初始机理目录(12 个)**,按数据可判别性排序:

| ID | 机理 | 方向 | 指标签名(avdp/aadp/ahdp) | 部位 | 起病 | 关键自动证据 |
|---|---|---|---|---|---|---|
| `mech.aperture_clogging` | 钢网开口堵塞 | 少锡 | 体↓ 面↓/平 高↓ | 钢网开口 | 渐变 | 擦网后复位、趋势爬升 |
| `mech.poor_release` | 脱模不良 | 少锡 | 体↓ 面平 高不稳 | 钢网开口+脱模参数 | 突变/渐变 | SnapOff* 参数偏差或变更 |
| `mech.understencil_residue` | 钢网底部残锡转印 | 多锡 | 面↑ 高平/↓ | 钢网底面 | 渐变 | 擦网后消失、擦网间隔内递增 |
| `mech.poor_gasketing` | 密合不良渗锡 | 多锡 | 面↑ 高↓ | 支撑与夹持 | 突变 | 局部区域聚集 |
| `mech.slump` | 塌陷 | 多锡(面积) | 面↑ 高↓ 体平 | 锡膏 | 渐变 | 三指标签名、环境(暂无) |
| `mech.paste_rheology_drift` | 锡膏流变劣化 | 双向 | 三指标同向渐变 | 锡膏 | 渐变 | 整板趋势斜率 |
| `mech.supply_interruption` | 供锡中断/漏印 | 少锡 | 整板体↓↓ | 锡膏/行程 | 突变 | 整板单板突发 |
| `mech.parameter_mismatch` | 参数漂移/设定不适配 | 视参数 | 视参数 | 程序 | 突变 | diff_*/abs_* 列、变更→恢复时序 |
| `mech.squeegee_one_side` | 刮刀单边异常 | 双向 | 按印刷方向分组差异 | 刮刀(前/后) | 渐变/突变 | **PrintDirection 分组 NG 率** |
| `mech.cleaning_cycle_mismatch` | 擦网周期不匹配 | 多锡为主 | 周期性复发 | 擦网系统 | 周期 | **CleaningAfterLastBoard 对齐** |
| `mech.alignment_offset` | 对位偏移 | 双向 | 面积异常伴方向性 | 视觉对位 | 突变 | MarkDeviation 趋势(机器级) |
| `mech.spi_false_call` | SPI程序误判 | — | 标签与指标不符 | SPI程序 | — | 主指标偏差 <20% |

说明:
- 指标签名是**甄别证据**不是充分条件:签名匹配 → 置信度加成;签名冲突 → 降权。这是 v2 完全没用的知识(v2 只看被标注缺陷的主指标)。
- 对位偏移在 Pad 级不可直接观测(数据无 per-pad offset),只有机器级 `MarkDeviation`,机理里如实标注观测局限。
- 兜底"局部印刷状态异常"保留为 `mech.undetermined`,置信度 0.35 不变。

### 2.4 证据模型:可自动核验 vs 需人工确认

每条证据是本体概念,分两类,机理通过 `auto_checks`/`manual_checks` 引用:

**可自动核验 `evidence.auto.*`**(数据表内一条查询能回答)。
2026-07-03 已对 full_excel0623(46587 行)逐列核实采集状态:

| 证据 | 数据来源 | 当前状态 |
|---|---|---|
| 三指标签名 | `Comp_avdp/aadp/ahdp` | 有数据,**未使用** |
| 脱模参数偏差/变更 | `SnapOff*` 系列(有真实波动) | 有数据,混在参数一锅烩检查里 |
| 前/后刮刀压力偏差 | `Front/RearSQGPress`(当前恒 4/4) | 有数据,混在一锅烩里 |
| 擦网频率设定 | `CleaningFrequency`(=3) | 有设定值,未用于周期分析参照 |
| 参数变更→恢复时序 | `*_Plan` 变更 + 恢复板序 | ✅ 已实现(param_events + recovery) |
| 触发前趋势斜率 | 窗口序列 | ✅ 已实现(change_type) |
| 复测记录 | 同 barcode 再检 | ✅ 已实现 |
| 窗口范围统计 | full_spi_window | ✅ 已实现 |
| ~~擦网事件对齐~~ | `CleaningAfterLastBoard` | **46587 行全空,未采集**→数据侧需求 |
| ~~印刷方向分组 NG 率~~ | `PrintDirection` | **46587 行全空,未采集**→数据侧需求 |

**数据侧需求清单**(向产线/导出方提出,与温湿度同类):
`CleaningAfterLastBoard`(擦网事件对齐——把周期性从统计猜测变成事实核验)、
`PrintDirection`(刮刀单边判别)、`Temperature/Humidity`(锡膏流变机理的环境证据)、
Pad 级 offset(对位偏移机理目前只有机器级 `MarkDeviation`)。

**需人工确认 `evidence.manual.*`**:原始 SPI 图像、显微检查开口、钢网底面目视、锡膏回温/搅拌/使用时长记录、钢网版本/ECN、支撑与板翘检查。

**产品语义**:chat 和契约里,自动证据显示"已核验:✓/✗/无数据",人工证据显示"待现场确认"。现场的人一眼知道 Agent 替他查了什么、还剩什么要查。这是 P3(知识闭环)的落点:闭环回填的是"某机理的某条人工证据的确认结果"。

### 2.5 决策层:把 build_conclusion 显式化

v2 的 `build_conclusion` 隐式顺序改写为**声明式决策规则**,存于知识库、`/api/rules` 可见:

```
decision_rule:
  id:        decide.spi_gate
  order:     10                    # 求值顺序(门槛类在前)
  when:      validity == spi_suspect
  then:      提名 mech.spi_false_call, 置信 0.85, 其余机理降权
```

规则组(与现有逻辑一一对应,行为不变,只是搬家):

| order | 规则组 | 对应 v2 代码 |
|---|---|---|
| 10 | 有效性门槛(SPI误判/数据存疑) | exclusions 分支 |
| 20 | 直接时序证据(参数变更→恢复、参数漂移) | drifted/related 分支 |
| 30 | 周期性(v3 改为擦网对齐核验) | periodicity 分支 |
| 40 | 签名甄别(三指标签名匹配/冲突加减权) | **新增** |
| 50 | 空间×方向先验(v2 的 scope 规则) | scope_root_cause 分支 |
| 60 | 趋势形态归因 | trend 分支 |
| 70 | 事件候选兜底 | event_cause 分支 |
| 90 | 未定机理兜底 | fallback |

置信度模型统一为:`最终置信 = 机理先验(confidence_base) × 证据乘数`,证据乘数由决策规则声明(签名匹配 ×1.2、自动证据核验通过 ×1.3、冲突 ×0.6 等,系数入库可调)。v2 的 `evidence_level` 高/中/低与 `confidence_base` 两套并行强度表述合并:`evidence_level` 改为按最终置信度分档显示(≥0.75 高 / ≥0.5 中 / <0.5 低),不再单独维护。

处置策略(P1/P2/P3)保持 v2 的决策阶梯不变,输入改为三轴 + 置信度。

## 3. 对 analysis_contract 的影响(v2 → v3)

保持外形稳定,增量演进:

```jsonc
"scope": {
  "category": "单Pad连续异常",          // 组合显示标签,UI 兼容
  "spatial": "spatial.single_pad",      // 新增:三轴分开
  "temporal": "temporal.consecutive",
  "validity": "validity.valid",
  "detail": "...", "confidence": "高"
},
"root_cause_candidates": [{
  "mechanism_id": "mech.aperture_clogging",   // 新增:机理 ID
  "cause": "钢网开口堵塞",                     // 保留:显示文本
  "location": "element.钢网开口",              // 新增
  "signature_match": "matched",                // 新增:签名甄别结果
  "auto_checks": [{"name": "擦网后复位", "result": "✓", "detail": "..."}],  // 新增
  "manual_checks": ["显微检查开口"],            // 替代 evidence_required
  "confidence": 0.78,                          // 最终置信(先验×乘数)
  "early_warning": "可预警(体积偏差 EWMA)",     // 新增:P0 挂点
  "action": "...", "evidence": "..."           // 保留
}]
```

前端改动小:决策卡/下钻页新增"已核验证据"区块,其余字段名不变或只增不删。

## 4. 分期实施

### 第一阶段:模型正交化(纯代码内重构,行为等价可测试)

1. `ontology.py`:三轴概念 + 实体层骨架 + 机理概念(12 个);v2 scope 概念保留为 deprecated 别名一个版本,映射表照常生成
2. `knowledge_base.py`:根因规则升级为机理对象;决策规则组(order 10–90)入库;证据分 auto/manual 两类
3. `drilldown.py`:`classify_scope` 拆三轴;`build_conclusion` 改为决策规则求值器(对现有测试场景输出不变)
4. `rules_engine.py`:实时模式映射到三轴(同点多板 = single_pad×repeated 等)
5. 契约升 v3,前端跟进;TTL 重新生成
6. 守护测试:三轴正交性(任意组合可表达)、决策规则求值顺序、v2→v3 场景快照对比

### 第二阶段:吃透现有数据的自动证据(每条独立交付)

按性价比排序(已按 2026-07-03 数据核实结果调整——擦网标记与印刷方向未采集,降级为数据侧需求):

1. **三指标签名甄别**(决策规则 order 40):对每个候选机理计算签名匹配度,加减权;签名判界用 full_excel0623 的实际分布定
2. **脱模/刮刀参数分组检查**:参数一锅烩偏差检查按机理分组(SnapOff* → `mech.poor_release`,SQGPress → 刮刀类),参数证据直接挂到对应机理而非笼统的"参数漂移"
3. **擦网频率参照**:周期性分析把 `CleaningFrequency` 设定值(当前=3)作为参照周期——NG 连段间隔若与它成整数倍关系,`mech.cleaning_cycle_mismatch` 加权;拿到 `CleaningAfterLastBoard` 真实采集后升级为事实对齐
4. **数据侧需求推进**(非代码):向导出方申请 `CleaningAfterLastBoard`、`PrintDirection`、温湿度、Pad 级 offset 的采集;每到位一项,对应机理的自动证据即可上线

### 不做(本期非目标)

- 实体层台账化(钢网版本/锡膏批次/设备)——等 MES/设备数据
- RDF/OWL 运行时推理——继续代码原生,TTL 仅作交换格式
- LLM 接入(P2)——但 v3 的机理网络就是为它准备的上下文
- 温湿度相关机理——数据全 0 未采集,机理目录中标注"证据不可得"

## 5. 与改进路线图 P0–P4 的咬合

| 路线项 | v3 提供的挂点 |
|---|---|
| P0 事前预警(EWMA/Cpk) | 机理的 `onset=gradual` + `early_warning` 字段就是监控点清单:体积偏差 EWMA(堵孔/残锡)、整板均值漂移(锡膏劣化) |
| P1 根因维度补齐 | 第二阶段 1–4 就是 P1 的具体内容 |
| P2 chat 接 LLM | 机理网络 + 已核验证据表 = 喂给 LLM 的结构化上下文;LLM 负责表达,机理层负责事实 |
| P3 知识闭环 | 闭环单位 = (机理, 人工证据, 确认结果);回填后可修正机理先验 |
| P4 行业指标 | 转印效率(实际体积/理论体积)可从三指标推算,挂观测层 |

## 6. 风险与开放问题(请评审时拍板)

1. **机理目录的置信先验**:表中数值是我基于 SMT 通用知识的初值,建议第一阶段落库后用真实数据回看两个触发点校准。
2. **签名阈值**:三指标"↑/↓/平"的判界(如 |偏差|<10% 算平)需要用 full_excel0623 的分布定,不拍脑袋。
3. **v2 scope 概念的废弃节奏**:建议保留一个版本(标 deprecated),外部若无人消费 `/api/ontology` 的旧 ID 则下一版删除。
4. ~~CleaningAfterLastBoard 的语义确认~~ 已核实:该列(连同 `PrintDirection`)在当前导出中 46587 行全空,未采集。转为数据侧需求;拿到数据后再确认语义(印刷前擦网还是印刷后)。
5. **机理目录中依赖未采集证据的两项**(`mech.squeegee_one_side` 单边判别、`mech.cleaning_cycle_mismatch` 事实对齐)在第一阶段照常入库,但其 auto_checks 标注"数据未采集",避免给现场造成"已核验"的错觉。
