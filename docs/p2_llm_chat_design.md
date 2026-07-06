# P2 下钻 Chat 接真 LLM:多提供商 + 本体 grounding

状态:2026-07-06 按用户要求设计并实施。用户明确:配置页可自选提供商,
支持 OpenAI / Anthropic / Gemini / DeepSeek / Qwen(通义) / 智谱 六家。

## 1. 架构

- **零依赖不破**:全部走 stdlib `urllib.request`,无任何 SDK。
- **六家提供商、三个协议适配器**:
  | 提供商 | 协议 | 默认端点 | 默认模型 |
  |---|---|---|---|
  | OpenAI | openai 兼容 | api.openai.com/v1/chat/completions | gpt-5-mini |
  | DeepSeek | openai 兼容 | api.deepseek.com/v1/chat/completions | deepseek-chat |
  | Qwen 通义 | openai 兼容 | dashscope…/compatible-mode/v1/chat/completions | qwen-plus |
  | 智谱 GLM | openai 兼容 | open.bigmodel.cn/api/paas/v4/chat/completions | glm-4.6 |
  | Anthropic | anthropic | api.anthropic.com/v1/messages | claude-haiku-4-5-20251001 |
  | Gemini | gemini | generativelanguage.googleapis.com/v1beta | gemini-2.5-flash |
  模型与端点都可在配置页改(代理网关/新模型无需改码)。openai 兼容协议
  的请求体只带 `model` + `messages`,不带 temperature/max_tokens 等可选
  参数——各家对可选参数的约束不同(如部分新模型拒绝 max_tokens),
  最小请求体兼容面最大。
- **配置**:`config/llm.json`(已加入 .gitignore,密钥不入库),字段
  enabled / provider / api_key / model / base_url / timeout_seconds。
  API 同 datasource 模式:GET 掩码、POST 保存("******" 表示不改密钥)、
  /test 发一条最小消息实测连通。
- **回答链路**:`build_chat_response` = LLM 优先、规则问答兜底。
  未启用、未配密钥、网络/接口失败 → 自动落回既有 build_rule_chat_response,
  响应带 `mode`("llm"/"rule")与 `fallback_reason`,前端如实标注来源。
  规则问答是产品的保底能力,永远不删。

## 2. Grounding(system prompt 组成)

1. 角色与规范:SMT 锡膏印刷质量助手;只依据给定资料回答,资料没有的
   要明说"数据未采集/证据不足";中文,按"结论/证据/下一步"三段作答;
   引用机理 id 与规则 id。
2. 本次触发的 `analysis_contract` 全文(单一权威结论载荷,含 scope、
   三指标签名、根因候选+置信算式、decision_trace、处置、复判计划)
   + 参数核查结论 + 参数事件数。大数组(series/热力图/全量明细)不进
   prompt。
3. 机理目录摘要:13 个 FailureMechanism 的 id/标签/方向/签名/动作
   (来自 ontology 单源,LLM 的根因语言被约束在机理词表内)。

## 3. UI

- 顶栏新增「LLM」按钮 → 配置弹窗:启用开关、提供商下拉(切换时自动
  预填该家默认模型与端点)、API Key(掩码)、模型、Base URL、超时、
  测试连接、保存。
- 下钻对话区:每条回答带来源徽标(如"DeepSeek · deepseek-chat"或
  "离线规则");LLM 失败落回规则时徽标注明原因。LLM 回答按纯文本
  段落渲染,规则回答维持三段式结构。

## 4. 测试口径

请求构造器与响应解析器为纯函数,单测断言三种协议的 URL/头/载荷与
canned 响应解析;网络层可注入,测试零外呼。回退链路(未配置/接口错)
有专测。真实连通性由配置页"测试连接"在现场验证。
