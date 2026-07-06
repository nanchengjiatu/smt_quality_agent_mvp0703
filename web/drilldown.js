// 三板连发下钻工作台：全屏 overlay,主趋势图 + 对比视图 + 自动分析结论 + 对话占位。
// 依赖 app.js 提供的 escapeHtml / formatNumber(脚本加载顺序在 index.html 中保证)。

const drilldownState = {
  trigger: null,
  metricField: null,
  showParamEvents: true,
  overlayParam: "",
  highlight: null,
  compareTab: "siblings",
  chatMessages: [],
  chatLoading: false,
};

const DD_CHAT_API = "/api/drilldown/chat";

const DD_METRICS = [
  ["comp_avdp", "体积偏差"],
  ["comp_aadp", "面积偏差"],
  ["comp_ahdp", "高度偏差"],
];

function renderCountList(items) {
  if (!items || !items.length) {
    return `<span class="details">无</span>`;
  }
  return items.map((item) => `
    <span class="dd-mini-pill">${escapeHtml(item.name)} <strong>${escapeHtml(item.count)}</strong></span>
  `).join("");
}

function renderAgentOverview(trigger) {
  const contract = trigger.analysis_contract || {};
  const contractScope = contract.scope || {};
  const contractDisposition = contract.disposition || {};
  const contractTrigger = contract.trigger || {};
  const summary = (contract.evidence || {}).context || {};
  const fullWindow = trigger.full_spi_window || {};
  return `
    <section class="panel dd-agent-overview">
      <div class="dd-agent-head">
        <div>
          <h3>Agent 判定</h3>
          <div class="dd-agent-title">
            ${escapeHtml(contractScope.category || "待判定")}
            <span class="badge risk-${escapeHtml(contractScope.confidence || "中")}">置信度 ${escapeHtml(contractScope.confidence || "中")}</span>
          </div>
          ${contractScope.spatial_label ? `
          <div class="details">
            空间：${escapeHtml(contractScope.spatial_label)} ·
            时间：${escapeHtml(contractScope.temporal_label || "-")} ·
            数据：${escapeHtml(contractScope.validity_label || "-")}
          </div>` : ""}
        </div>
        <div class="dd-agent-source">
          全量 SPI 窗口 前 ${escapeHtml(fullWindow.actual_before ?? 0)}/${escapeHtml(fullWindow.requested_before ?? 500)}
          · 后 ${escapeHtml(fullWindow.actual_after ?? 0)}/${escapeHtml(fullWindow.requested_after ?? 500)}
        </div>
      </div>
      <div class="dd-agent-grid">
        <div>
          <span class="details">窗口总行数</span>
          <strong>${escapeHtml(summary.total_rows ?? 0)}</strong>
        </div>
        <div>
          <span class="details">窗口 NG</span>
          <strong>${escapeHtml(summary.ng_rows ?? 0)} / ${formatNumber(((summary.ng_rate || 0) * 100))}%</strong>
        </div>
        <div>
          <span class="details">同元件 NG</span>
          <strong>${escapeHtml(summary.same_component_ng_rows ?? 0)}</strong>
        </div>
        <div>
          <span class="details">同 Pad NG</span>
          <strong>${escapeHtml(summary.same_pad_ng_rows ?? 0)}</strong>
        </div>
      </div>
      <p class="dd-agent-detail">${escapeHtml(contractTrigger.conclusion || "")}</p>
      <div class="dd-disposition">
        <div>
          <span class="details">处置等级</span>
          <strong>${escapeHtml(contractDisposition.priority || "P3")}</strong>
        </div>
        <div>
          <span class="details">处置建议</span>
          <strong>${escapeHtml(contractDisposition.suggestion || "按单点异常做快速确认")}</strong>
        </div>
        <div>
          <span class="details">首要依据</span>
          <strong>${escapeHtml(contractDisposition.primary_evidence || "待现场确认")}</strong>
        </div>
        <div>
          <span class="details">第一步动作</span>
          <strong>${escapeHtml(contractDisposition.primary_action || "复核触发 Pad、原始 SPI 图像和事件时段设备记录。")}</strong>
        </div>
      </div>
      <div class="dd-evidence-tags">
        ${(((contract.evidence || {}).tags) || []).map((item) => `<span>${escapeHtml(item)}</span>`).join("")}
      </div>
    </section>
  `;
}

function renderAgentEvidence(trigger) {
  const contract = trigger.analysis_contract || {};
  const contractEvidence = contract.evidence || {};
  const summary = contractEvidence.context || {};
  const exclusionItems = contractEvidence.exclusion_checks || [];
  return `
    <section class="dd-agent-evidence">
      <h3>Agent 输出</h3>
      <div class="dd-evidence-block">
        <span class="details">异常结论</span>
        <strong>${escapeHtml((contract.trigger || {}).conclusion || "待判定")}</strong>
      </div>
      <h3>全量 SPI 证据摘要</h3>
      <div class="dd-evidence-block">
        <span class="details">主要异常类型</span>
        <div>${renderCountList(summary.dominant_defects)}</div>
      </div>
      <div class="dd-evidence-block">
        <span class="details">Top NG 元件</span>
        <div>${renderCountList(summary.top_ng_components)}</div>
      </div>
      <div class="dd-evidence-block">
        <span class="details">Top NG Pad</span>
        <div>${renderCountList(summary.top_ng_pads)}</div>
      </div>
      <h3>排除检查</h3>
      ${exclusionItems.map((item) => `
        <div class="dd-check-row ${item.status === "pass" ? "pass" : "review"}">
          <strong>${escapeHtml(item.name)}</strong>
          <span>${escapeHtml(item.detail || "未检查")}</span>
        </div>
      `).join("")}
      <h3>复判标准</h3>
      <ul class="finding-list">
        ${(((contract.recheck || {}).criteria) || []).map((item) => `<li>${escapeHtml(item)}</li>`).join("")}
      </ul>
    </section>
  `;
}

function renderDecisionTrace(trigger) {
  const trace = ((trigger.analysis_contract || {}).decision_trace) || {};
  const steps = trace.steps || [];
  if (!steps.length) {
    return "";
  }
  const eliminated = trace.eliminated || [];
  return `
    <details class="dd-trace">
      <summary>诊断轨迹 <span class="details">决策梯对本次触发的逐条求值记录（可审计）</span></summary>
      <div class="dd-trace-body">
        ${steps.map((step) => `
          <div class="dd-trace-step ${step.fired ? "fired" : "missed"}">
            <span class="dd-trace-mark">${step.fired ? "✓" : "✗"}</span>
            <div class="dd-trace-main">
              <strong>order ${escapeHtml(step.order)} · ${escapeHtml(step.label)}</strong>
              <span class="details">${escapeHtml(step.when)}</span>
              ${step.fired
                ? (step.nominated || []).map((item) => `
                    <div class="dd-trace-nominee">
                      → ${escapeHtml(item.cause)}
                      <em>${escapeHtml(item.formula)}</em>
                      <span class="details">${escapeHtml(item.rule_id)}</span>
                    </div>
                  `).join("") || `<div class="details">命中但未产出候选</div>`
                : ""}
            </div>
          </div>
        `).join("")}
        <div class="dd-trace-tail">
          <strong>排序 · 同根因去重 · 取前 3</strong>
          ${eliminated.length ? `
            <ul>
              ${eliminated.map((item) => `
                <li>落选：${escapeHtml(item.cause)}（置信 ${escapeHtml(String(item.confidence))}） — ${escapeHtml(item.reason)}</li>
              `).join("")}
            </ul>` : `<div class="details">全部提名候选均进入前 3。</div>`}
        </div>
      </div>
    </details>
  `;
}

function renderChatMessages() {
  const messages = drilldownState.chatMessages || [];
  if (!messages.length) {
    return `<div class="dd-chat-empty">选择一个问题，Agent 会基于当前下钻事件回答。</div>`;
  }
  return messages.map((message) => {
    if (message.role === "user") {
      return `<div class="dd-chat-bubble user">${escapeHtml(message.text)}</div>`;
    }
    if (message.error) {
      return `<div class="dd-chat-bubble assistant error">${escapeHtml(message.error)}</div>`;
    }
    const answer = message.answer || {};
    const source = message.mode === "llm"
      ? `${message.provider || "LLM"} · ${message.model || ""}`
      : "离线规则";
    const sourceLine = `<div class="dd-chat-source">${escapeHtml(source)}${message.fallback_reason ? ` · ${escapeHtml(message.fallback_reason)}` : ""}</div>`;
    if (answer.text != null) {
      return `
        <div class="dd-chat-bubble assistant">
          <p class="dd-chat-freeform">${escapeHtml(answer.text)}</p>
          ${sourceLine}
        </div>
      `;
    }
    return `
      <div class="dd-chat-bubble assistant">
        <strong>结论</strong>
        <p>${escapeHtml(answer.conclusion || "暂无结论")}</p>
        <strong>证据</strong>
        <p>${escapeHtml(answer.evidence || "暂无证据")}</p>
        <strong>下一步</strong>
        <p>${escapeHtml(answer.next_step || "暂无建议")}</p>
        ${sourceLine}
      </div>
    `;
  }).join("");
}

function renderChatPanel(trigger) {
  const quickQuestions = [
    "为什么判定为这个范围？",
    "现场先查什么？",
    "哪些证据支持首要根因？",
    "这是不是 SPI 假异常？",
    "解释参数对比结果",
  ];
  return `
    <h3>对话分析</h3>
    <div class="dd-chat-note">
      已启用 LLM 时由所选大模型基于本次触发的分析契约与机理目录回答（来源见每条回答标注），
      未配置或调用失败时自动回退<strong>离线规则问答</strong>。顶栏「LLM」按钮可配置提供商。
    </div>
    <div class="dd-chat-quick">
      ${quickQuestions.map((question) => `
        <button class="dd-chip" data-chat-question="${escapeHtml(question)}" ${drilldownState.chatLoading ? "disabled" : ""}>${escapeHtml(question)}</button>
      `).join("")}
    </div>
    <div id="ddChatMessages" class="dd-chat-messages">
      ${renderChatMessages()}
      ${drilldownState.chatLoading ? `<div class="dd-chat-bubble assistant">正在分析当前事件...</div>` : ""}
    </div>
    <div class="dd-chat-input">
      <input id="ddChatInput" type="text" placeholder="围绕当前事件追问…" ${drilldownState.chatLoading ? "disabled" : ""}>
      <button id="ddChatSend" ${drilldownState.chatLoading ? "disabled" : ""}>发送</button>
    </div>
  `;
}

function findDrilldownTrigger(triggers, component, pad) {
  return (triggers || []).find(
    (item) => item.component === String(component) && item.pad === String(pad),
  ) || null;
}

function openDrilldown(trigger) {
  drilldownState.trigger = trigger;
  drilldownState.metricField = trigger.metric_field;
  drilldownState.showParamEvents = true;
  drilldownState.overlayParam = "";
  drilldownState.highlight = null;
  drilldownState.compareTab = "siblings";
  drilldownState.chatMessages = [];
  drilldownState.chatLoading = false;

  let overlay = document.getElementById("ddOverlay");
  if (!overlay) {
    overlay = document.createElement("div");
    overlay.id = "ddOverlay";
    overlay.className = "dd-overlay";
    document.body.appendChild(overlay);
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        closeDrilldown();
      }
    });
  }
  document.body.classList.add("dd-open");
  renderDrilldown();
}

function closeDrilldown() {
  const overlay = document.getElementById("ddOverlay");
  if (overlay) {
    overlay.remove();
  }
  document.body.classList.remove("dd-open");
  drilldownState.trigger = null;
}

function renderDrilldown() {
  const trigger = drilldownState.trigger;
  const overlay = document.getElementById("ddOverlay");
  if (!trigger || !overlay) {
    return;
  }

  const window_ = trigger.window || {};
  const maxValue = Math.max(...trigger.series
    .map((point) => point.values[trigger.metric_field])
    .filter((value) => value != null));
  const rootCauseCandidates = ((trigger.analysis_contract || {}).root_cause_candidates) || [];

  overlay.innerHTML = `
    <header class="dd-header">
      <button class="dd-back" id="ddBack">← 返回</button>
      <div>
        <div class="dd-title">
          焊盘 ${escapeHtml(trigger.pad_name)} · 机种 ${escapeHtml(trigger.model)}
          <span class="${trigger.direction === "多锡" ? "defect-多锡" : "defect-少锡"}">${escapeHtml(trigger.main_defect_cn)}</span>
          <span class="badge risk-高">${escapeHtml(((trigger.analysis_contract || {}).scope || {}).category || "待判定")}</span>
          <span class="badge dd-badge">三板连发</span>
        </div>
        <div class="dd-sub">
          触发：连续 ${trigger.trigger_board_count} 块生产板 ·
          ${escapeHtml(trigger.start_time)} ~ ${escapeHtml(trigger.end_time)} ·
          ${escapeHtml(trigger.metric_label)}最高 ${formatNumber(maxValue)}% ·
          窗口 前 ${window_.before_count}/请求 ${window_.requested} 条、后 ${window_.after_count}/${window_.requested} 条
        </div>
      </div>
    </header>
    <div class="dd-body">
      <div class="dd-main">
        ${renderAgentOverview(trigger)}
        <section class="panel">
          <div class="dd-chart-toolbar">
            <div class="dd-metric-switch">
              ${DD_METRICS.map(([field, label]) => `
                <button class="dd-chip ${field === drilldownState.metricField ? "active" : ""}" data-metric="${field}">${label}</button>
              `).join("")}
            </div>
            <label class="dd-toggle ${(trigger.param_events || []).length ? "" : "dd-toggle-disabled"}">
              <input type="checkbox" id="ddParamToggle"
                ${drilldownState.showParamEvents ? "checked" : ""}
                ${(trigger.param_events || []).length ? "" : "disabled"}>
              ${(trigger.param_events || []).length
                ? `参数变更事件线（${trigger.param_events.length}）`
                : "参数变更事件线（窗口内无程序设定变更）"}
            </label>
            <label class="dd-toggle dd-overlay-pick">
              叠加参数曲线
              <select id="ddOverlaySelect">
                <option value="">无</option>
                ${(((trigger.param_series || {}).fields) || []).map((field) => `
                  <option value="${escapeHtml(field)}" ${drilldownState.overlayParam === field ? "selected" : ""}>${escapeHtml(field)}</option>
                `).join("")}
              </select>
            </label>
          </div>
          <div id="ddChart" class="dd-chart"></div>
          <div class="dd-legend">
            <span><i class="dd-dot dd-dot-ng"></i>NG</span>
            <span><i class="dd-dot dd-dot-ok"></i>正常</span>
            <span><i class="dd-dot dd-dot-recheck"></i>复测</span>
            <span><i class="dd-swatch dd-swatch-trigger"></i>触发区</span>
            <span><i class="dd-swatch dd-swatch-band"></i>基线±3σ</span>
            <span><i class="dd-swatch dd-swatch-param"></i>参数变更</span>
          </div>
        </section>
        <section class="panel">
          <div class="dd-tabs">
            <button class="dd-chip ${drilldownState.compareTab === "siblings" ? "active" : ""}" data-tab="siblings">同件焊盘</button>
            <button class="dd-chip ${drilldownState.compareTab === "heatmap" ? "active" : ""}" data-tab="heatmap">焊盘热力图</button>
            <button class="dd-chip ${drilldownState.compareTab === "params" ? "active" : ""}" data-tab="params">参数对比</button>
          </div>
          <div id="ddCompareBody"></div>
        </section>
        <section class="panel">
          <h3>自动分析结论 <span class="details">点击带 ◎ 的结论可在图上高亮对应区段</span></h3>
          <ul class="finding-list dd-finding-list">
            ${trigger.findings.map((finding, index) => `
              <li class="${finding.highlight ? "dd-finding-clickable" : ""} ${drilldownState.highlight === index ? "active" : ""}"
                  data-finding="${index}">
                ${finding.highlight ? "◎ " : ""}${escapeHtml(finding.text)}
              </li>
            `).join("")}
          </ul>
          <h3>根因优先级与现场建议</h3>
          <ul class="finding-list">
            ${rootCauseCandidates.length
              ? rootCauseCandidates.map((item) => `
                <li>
                  <strong>${item.priority}. ${escapeHtml(item.cause)}</strong>
                  <span class="badge risk-${escapeHtml(item.evidence_level || "中")}" title="${escapeHtml(item.confidence_formula || "")}">置信 ${escapeHtml(item.confidence != null ? Math.round(item.confidence * 100) + "%" : item.evidence_level || "中")}</span>
                  ${item.mechanism ? `<span class="details">机理：${escapeHtml(item.mechanism)}${item.location ? `（${escapeHtml(item.location)}）` : ""}</span>` : ""}
                  <span class="details">规则：${escapeHtml(item.rule_id || "rule.unspecified")}</span>
                  <span class="details"> — 依据：${escapeHtml(item.evidence || "待现场确认")}</span><br>
                  ${(item.auto_checks || []).length ? `
                  <span class="details">已核验：${(item.auto_checks || []).map((check) => `
                    <span class="dd-mini-pill" title="${escapeHtml(check.detail || "")}">${escapeHtml(check.name)} ${escapeHtml(check.status)}</span>
                  `).join("")}</span><br>` : ""}
                  ${(item.manual_checks || []).length ? `<span class="details">待现场确认：${escapeHtml((item.manual_checks || []).join("、"))}</span><br>` : ""}
                  ${item.early_warning ? `<span class="details">预警：${escapeHtml(item.early_warning)}</span><br>` : ""}
                  <span class="details">处置：${escapeHtml(item.action || "")}</span>
                </li>
              `).join("")
              : `<li class="details">当前事件没有可用的根因候选。</li>`}
          </ul>
          ${renderDecisionTrace(trigger)}
        </section>
      </div>
      <aside class="dd-chat panel">
        ${renderAgentEvidence(trigger)}
        ${renderChatPanel(trigger)}
      </aside>
    </div>
  `;

  overlay.querySelector("#ddBack").addEventListener("click", closeDrilldown);
  overlay.querySelectorAll("[data-metric]").forEach((button) => {
    button.addEventListener("click", () => {
      drilldownState.metricField = button.dataset.metric;
      renderDrilldown();
    });
  });
  overlay.querySelector("#ddParamToggle").addEventListener("change", (event) => {
    drilldownState.showParamEvents = event.target.checked;
    renderRunChart();
  });
  overlay.querySelector("#ddOverlaySelect").addEventListener("change", (event) => {
    drilldownState.overlayParam = event.target.value;
    renderRunChart();
  });
  overlay.querySelectorAll("[data-tab]").forEach((button) => {
    button.addEventListener("click", () => {
      drilldownState.compareTab = button.dataset.tab;
      renderDrilldown();
    });
  });
  overlay.querySelectorAll("[data-finding]").forEach((item) => {
    const finding = trigger.findings[Number(item.dataset.finding)];
    if (!finding.highlight) {
      return;
    }
    item.addEventListener("click", () => {
      const index = Number(item.dataset.finding);
      drilldownState.highlight = drilldownState.highlight === index ? null : index;
      renderDrilldown();
    });
  });
  overlay.querySelectorAll("[data-chat-question]").forEach((button) => {
    button.addEventListener("click", () => {
      askDrilldownQuestion(button.dataset.chatQuestion || button.textContent || "");
    });
  });
  const chatInput = overlay.querySelector("#ddChatInput");
  const chatSend = overlay.querySelector("#ddChatSend");
  if (chatInput && chatSend) {
    chatSend.addEventListener("click", () => askDrilldownQuestion(chatInput.value));
    chatInput.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        askDrilldownQuestion(chatInput.value);
      }
    });
  }

  renderRunChart();
  renderCompareBody();
}

async function askDrilldownQuestion(question) {
  const text = String(question || "").trim();
  const trigger = drilldownState.trigger;
  if (!text || !trigger || drilldownState.chatLoading) {
    return;
  }
  drilldownState.chatMessages.push({ role: "user", text });
  drilldownState.chatLoading = true;
  renderDrilldown();
  try {
    const response = await fetch(DD_CHAT_API, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ trigger_id: trigger.trigger_id, question: text }),
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok || body.ok === false) {
      throw new Error(body.error || `HTTP ${response.status}`);
    }
    drilldownState.chatMessages.push({
      role: "assistant",
      answer: body.answer,
      intent: body.intent,
      mode: body.mode,
      provider: body.provider,
      model: body.model,
      fallback_reason: body.fallback_reason,
    });
  } catch (error) {
    drilldownState.chatMessages.push({
      role: "assistant",
      error: `对话分析失败：${error.message}。请确认当前页面由 python3 serve.py 提供，而不是普通静态服务。`,
    });
  } finally {
    drilldownState.chatLoading = false;
    renderDrilldown();
  }
}

function renderRunChart() {
  const trigger = drilldownState.trigger;
  const container = document.getElementById("ddChart");
  if (!trigger || !container) {
    return;
  }

  const metricField = drilldownState.metricField;
  const metricLabel = (DD_METRICS.find(([field]) => field === metricField) || [])[1] || metricField;
  const series = trigger.series;
  const overlayName = drilldownState.overlayParam;
  const overlayValues = overlayName
    ? ((trigger.param_series || {}).series || {})[overlayName] || null
    : null;
  const W = Math.max(container.clientWidth || 920, 640);
  const H = 300;
  const padL = 52;
  const padR = overlayValues ? 58 : 16;
  const padT = 14;
  const padB = 34;

  const values = series.map((point) => point.values[metricField]).filter((value) => value != null);
  if (!values.length) {
    container.innerHTML = `<div class="empty">该指标在窗口内无有效数据</div>`;
    return;
  }
  const baseline = trigger.baseline || {};
  let yMax = Math.max(...values, baseline.available ? baseline.upper_band : 0);
  let yMin = Math.min(...values, baseline.available ? baseline.lower_band : 0, 0);
  yMax += (yMax - yMin) * 0.08 || 1;

  const x = (index) => padL + (series.length === 1
    ? (W - padL - padR) / 2
    : index * (W - padL - padR) / (series.length - 1));
  const y = (value) => padT + (1 - (value - yMin) / (yMax - yMin)) * (H - padT - padB);

  const parts = [];

  // baseline ±3σ band and mean line
  if (baseline.available) {
    parts.push(`<rect x="${padL}" y="${y(baseline.upper_band)}" width="${W - padL - padR}"
      height="${Math.max(y(baseline.lower_band) - y(baseline.upper_band), 1)}" class="dd-band"/>`);
    parts.push(`<line x1="${padL}" y1="${y(baseline.mean)}" x2="${W - padR}" y2="${y(baseline.mean)}" class="dd-mean"/>`);
    parts.push(`<text x="${W - padR - 4}" y="${y(baseline.mean) - 4}" class="dd-axis-text" text-anchor="end">基线均值 ${baseline.mean}%</text>`);
  }

  // trigger region
  const triggerIndexes = series.map((point, index) => point.is_trigger ? index : -1).filter((index) => index >= 0);
  if (triggerIndexes.length) {
    const x0 = x(triggerIndexes[0]) - 6;
    const x1 = x(triggerIndexes[triggerIndexes.length - 1]) + 6;
    parts.push(`<rect x="${x0}" y="${padT}" width="${x1 - x0}" height="${H - padT - padB}" class="dd-trigger-region"/>`);
  }

  // finding highlight region
  const activeFinding = drilldownState.highlight != null ? trigger.findings[drilldownState.highlight] : null;
  if (activeFinding && activeFinding.highlight) {
    const [seqA, seqB] = activeFinding.highlight;
    const indexes = series.map((point, index) => (point.seq >= seqA && point.seq <= seqB) ? index : -1)
      .filter((index) => index >= 0);
    if (indexes.length) {
      const x0 = x(indexes[0]) - 6;
      const x1 = x(indexes[indexes.length - 1]) + 6;
      parts.push(`<rect x="${x0}" y="${padT}" width="${x1 - x0}" height="${H - padT - padB}" class="dd-highlight-region"/>`);
    }
  }

  // y grid + labels
  for (let tick = 0; tick <= 4; tick += 1) {
    const value = yMin + (yMax - yMin) * tick / 4;
    parts.push(`<line x1="${padL}" y1="${y(value)}" x2="${W - padR}" y2="${y(value)}" class="dd-grid"/>`);
    parts.push(`<text x="${padL - 6}" y="${y(value) + 4}" class="dd-axis-text" text-anchor="end">${value.toFixed(0)}%</text>`);
  }

  // x labels: relative seq, 0 = trigger start
  const step = Math.max(1, Math.ceil(series.length / 10));
  series.forEach((point, index) => {
    if (index % step === 0 || point.is_trigger && point.seq === 0) {
      parts.push(`<text x="${x(index)}" y="${H - padB + 16}" class="dd-axis-text" text-anchor="middle">${point.seq}</text>`);
    }
  });
  parts.push(`<text x="${padL}" y="${H - 4}" class="dd-axis-text">横轴：相对触发起点的记录序号 · 纵轴：${escapeHtml(metricLabel)}（%）</text>`);

  // param event vertical lines — events on the same board share one line,
  // and labels are skipped when they would collide with the previous one.
  if (drilldownState.showParamEvents) {
    const groups = new Map();
    (trigger.param_events || []).forEach((event) => {
      if (!groups.has(event.seq)) {
        groups.set(event.seq, []);
      }
      groups.get(event.seq).push(event);
    });

    let lastLabelX = -Infinity;
    [...groups.entries()].sort((a, b) => a[0] - b[0]).forEach(([seq, events]) => {
      const index = series.findIndex((point) => point.seq === seq);
      if (index < 0) {
        return;
      }
      const detail = events.map((event) =>
        `${event.parameter}: ${event.from} → ${event.to}`,
      ).join("\n");
      const title = `<title>${escapeHtml(events[0].time)}\n${escapeHtml(detail)}</title>`;
      parts.push(`<g class="dd-param-group" data-param-seq="${seq}">
        <line x1="${x(index)}" y1="${padT}" x2="${x(index)}" y2="${H - padB}" class="dd-param-line"/>
        <rect x="${x(index) - 6}" y="${padT}" width="12" height="${H - padT - padB}" fill="transparent"/>
        ${title}
      </g>`);
      if (x(index) - lastLabelX >= 56) {
        const label = events.length === 1
          ? events[0].parameter
          : `${events[0].parameter} 等${events.length}项`;
        parts.push(`<text x="${x(index) + 3}" y="${padT + 10}" class="dd-param-text">${escapeHtml(label)}${title}</text>`);
        lastLabelX = x(index);
      }
    });
  }

  // connecting line (skip nulls)
  let path = "";
  let pen = false;
  series.forEach((point, index) => {
    const value = point.values[metricField];
    if (value == null) {
      pen = false;
      return;
    }
    path += `${pen ? "L" : "M"}${x(index).toFixed(1)},${y(value).toFixed(1)}`;
    pen = true;
  });
  parts.push(`<path d="${path}" class="dd-line"/>`);

  // points
  series.forEach((point, index) => {
    const value = point.values[metricField];
    if (value == null) {
      return;
    }
    const classes = ["dd-point"];
    if (point.is_ng) {
      classes.push("ng");
    }
    if (point.is_recheck) {
      classes.push("recheck");
    }
    if (point.is_trigger) {
      classes.push("trigger");
    }
    parts.push(`<circle cx="${x(index)}" cy="${y(value)}" r="${point.is_trigger ? 5 : 3.5}" class="${classes.join(" ")}" data-point-index="${index}">
      <title>${escapeHtml(point.board_sn)} · ${escapeHtml(point.time)}
${escapeHtml(metricLabel)} ${formatNumber(value)}% · ${point.is_ng ? escapeHtml(point.err) : "PASS"}${point.is_recheck ? " · 复测" : ""}
板 NG ${point.board_ng_count}/${point.board_row_count}</title>
    </circle>`);
  });

  // measurement-parameter overlay curve on a second (right) axis
  if (overlayValues) {
    const present = overlayValues.filter((item) => item != null).map((item) => item.v);
    if (present.length) {
      let oMax = Math.max(...present);
      let oMin = Math.min(...present);
      if (oMax === oMin) {
        oMax += Math.abs(oMax) * 0.1 || 1;
        oMin -= Math.abs(oMin) * 0.1 || 1;
      } else {
        const margin = (oMax - oMin) * 0.12;
        oMax += margin;
        oMin -= margin;
      }
      const oy = (value) => padT + (1 - (value - oMin) / (oMax - oMin)) * (H - padT - padB);

      for (let tick = 0; tick <= 2; tick += 1) {
        const value = oMin + (oMax - oMin) * tick / 2;
        parts.push(`<text x="${W - padR + 6}" y="${oy(value) + 4}" class="dd-overlay-axis">${value.toFixed(Math.abs(oMax - oMin) < 1 ? 3 : 1)}</text>`);
      }
      parts.push(`<text x="${W - padR + 6}" y="${padT - 2}" class="dd-overlay-axis">${escapeHtml(overlayName)}</text>`);

      let oPath = "";
      let oPen = false;
      series.forEach((point, index) => {
        const item = overlayValues[index];
        if (item == null) {
          oPen = false;
          return;
        }
        oPath += `${oPen ? "L" : "M"}${x(index).toFixed(1)},${oy(item.v).toFixed(1)}`;
        oPen = true;
      });
      parts.push(`<path d="${oPath}" class="dd-overlay-line"/>`);

      series.forEach((point, index) => {
        const item = overlayValues[index];
        if (item == null) {
          return;
        }
        parts.push(`<rect x="${x(index) - 3}" y="${oy(item.v) - 3}" width="6" height="6"
          class="dd-overlay-point" data-overlay-index="${index}">
          <title>${escapeHtml(overlayName)} ${item.v}${item.plan != null ? ` · 计划 ${item.plan}` : ""}</title>
        </rect>`);
      });
    }
  }

  container.innerHTML = `<svg viewBox="0 0 ${W} ${H}" width="100%" height="${H}">${parts.join("")}</svg>`;
  bindChartClicks(container, trigger, metricField, metricLabel);
}

// 点击参数事件线 / 数据点弹出明细卡片;点空白处关闭。
function bindChartClicks(container, trigger, metricField, metricLabel) {
  container.onclick = (event) => {
    const group = event.target.closest("[data-param-seq]");
    const circle = event.target.closest("[data-point-index]");
    const overlayRect = event.target.closest("[data-overlay-index]");

    if (overlayRect) {
      const index = Number(overlayRect.dataset.overlayIndex);
      const point = trigger.series[index];
      const item = (((trigger.param_series || {}).series || {})[drilldownState.overlayParam] || [])[index];
      if (item) {
        showChartTip(container, event, `
          <strong>${escapeHtml(drilldownState.overlayParam)} · ${escapeHtml(point.board_sn)}</strong>
          <div>${escapeHtml(point.time)}</div>
          <div>实际值：<strong>${escapeHtml(item.v)}</strong>${item.plan != null ? ` · 计划值：${escapeHtml(item.plan)}` : ""}${item.diff != null ? ` · 偏差：${escapeHtml(item.diff)}` : ""}</div>
          <div class="dd-tip-muted">相对触发起点 ${point.seq >= 0 ? "+" : ""}${point.seq}${point.is_trigger ? " · 触发板" : ""}</div>
        `);
      }
      return;
    }

    if (group) {
      const seq = Number(group.dataset.paramSeq);
      const events = (trigger.param_events || []).filter((item) => item.seq === seq);
      showChartTip(container, event, `
        <strong>程序设定变更 · ${escapeHtml(events[0] ? events[0].time : "")}</strong>
        ${events.map((item) => `
          <div>${escapeHtml(item.parameter)}：${escapeHtml(item.from)} → <strong>${escapeHtml(item.to)}</strong></div>
        `).join("")}
        <div class="dd-tip-muted">板 ${escapeHtml(events[0] ? events[0].board_sn : "")} 起生效</div>
      `);
      return;
    }

    if (circle) {
      const point = trigger.series[Number(circle.dataset.pointIndex)];
      const value = point.values[metricField];
      showChartTip(container, event, `
        <strong>${escapeHtml(point.board_sn)}</strong>
        <div>${escapeHtml(point.time)}${point.is_recheck ? " · 复测" : ""}</div>
        <div>${escapeHtml(metricLabel)}：<strong>${formatNumber(value)}%</strong> · ${point.is_ng ? `<span class="dd-tip-ng">${escapeHtml(point.err)}</span>` : "PASS"}</div>
        <div class="dd-tip-muted">该板整体：NG ${point.board_ng_count}/${point.board_row_count} 点 · 相对触发起点 ${point.seq >= 0 ? "+" : ""}${point.seq}</div>
      `);
      return;
    }

    hideChartTip(container);
  };
}

function showChartTip(container, event, html) {
  let tip = container.querySelector(".dd-chart-tip");
  if (!tip) {
    tip = document.createElement("div");
    tip.className = "dd-chart-tip";
    container.appendChild(tip);
  }
  tip.innerHTML = html;
  const bounds = container.getBoundingClientRect();
  const left = Math.min(event.clientX - bounds.left + 12, bounds.width - 240);
  const top = Math.min(event.clientY - bounds.top + 8, bounds.height - 90);
  tip.style.left = `${Math.max(left, 4)}px`;
  tip.style.top = `${Math.max(top, 4)}px`;
}

function hideChartTip(container) {
  const tip = container.querySelector(".dd-chart-tip");
  if (tip) {
    tip.remove();
  }
}

function renderCompareBody() {
  const trigger = drilldownState.trigger;
  const container = document.getElementById("ddCompareBody");
  if (!trigger || !container) {
    return;
  }

  if (drilldownState.compareTab === "siblings") {
    const siblings = trigger.siblings || [];
    if (!siblings.length) {
      container.innerHTML = `<div class="empty">该元件没有其他焊盘</div>`;
      return;
    }
    container.innerHTML = `
      <p class="details">同元件其余焊盘在同一窗口内的${escapeHtml(trigger.metric_label)}走势（红点为 NG）：</p>
      <div class="dd-sparklines">
        ${siblings.map((sibling) => `
          <div class="dd-sparkline">
            <span>${escapeHtml(sibling.pad_name)}${sibling.trigger_ng_count ? ` <em class="dd-ng-mark">触发板 NG×${sibling.trigger_ng_count}</em>` : ""}</span>
            ${renderSparkline(sibling.points)}
          </div>
        `).join("")}
      </div>
    `;
  } else if (drilldownState.compareTab === "heatmap") {
    container.innerHTML = renderHeatmap(trigger);
  } else {
    const drifted = (trigger.parameter_check || {}).drifted || [];
    container.innerHTML = `
      <p class="details">${escapeHtml((trigger.parameter_check || {}).verdict || "")}</p>
      ${drifted.length ? `
        <table class="dd-param-table">
          <thead><tr><th>参数</th><th>事件期间最大偏差</th><th>正常生产最大偏差</th></tr></thead>
          <tbody>
            ${drifted.map((item) => `
              <tr>
                <td>${escapeHtml(item.parameter)}</td>
                <td class="defect-多锡">${formatNumber(item.event_max_abs_diff)}</td>
                <td>${item.baseline_max_abs_diff != null ? formatNumber(item.baseline_max_abs_diff) : "无数据"}</td>
              </tr>
            `).join("")}
          </tbody>
        </table>` : ""}
    `;
  }
}

function renderSparkline(points) {
  const valid = points.filter((point) => point.value != null);
  if (!valid.length) {
    return `<svg width="220" height="44"></svg>`;
  }
  const W = 220;
  const H = 44;
  const max = Math.max(...valid.map((point) => point.value));
  const min = Math.min(...valid.map((point) => point.value), 0);
  const x = (index) => 4 + index * (W - 8) / Math.max(points.length - 1, 1);
  const y = (value) => 4 + (1 - (value - min) / ((max - min) || 1)) * (H - 8);

  let path = "";
  let pen = false;
  const dots = [];
  points.forEach((point, index) => {
    if (point.value == null) {
      pen = false;
      return;
    }
    path += `${pen ? "L" : "M"}${x(index).toFixed(1)},${y(point.value).toFixed(1)}`;
    pen = true;
    if (point.is_ng) {
      dots.push(`<circle cx="${x(index)}" cy="${y(point.value)}" r="3" class="dd-point ng"/>`);
    }
  });
  return `<svg width="${W}" height="${H}"><path d="${path}" class="dd-line"/>${dots.join("")}</svg>`;
}

function renderHeatmap(trigger) {
  const cells = (trigger.heatmap || []).filter((cell) => cell.px != null && cell.py != null);
  if (!cells.length) {
    return `<div class="empty">无焊盘坐标数据（Comp_PX/PY 为空）</div>`;
  }
  const W = 640;
  const H = 300;
  const xs = cells.map((cell) => cell.px);
  const ys = cells.map((cell) => cell.py);
  const xMin = Math.min(...xs);
  const xMax = Math.max(...xs);
  const yMin = Math.min(...ys);
  const yMax = Math.max(...ys);
  const x = (value) => 40 + (xMax === xMin ? 0.5 : (value - xMin) / (xMax - xMin)) * (W - 80);
  const y = (value) => 30 + (yMax === yMin ? 0.5 : 1 - (value - yMin) / (yMax - yMin)) * (H - 60);

  const dots = cells.map((cell) => {
    const share = cell.trigger_board_count ? cell.trigger_ng_count / cell.trigger_board_count : 0;
    const isTriggerPad = cell.pad_name === trigger.pad_name;
    const fill = share > 0
      ? `rgba(180, 35, 24, ${0.25 + share * 0.75})`
      : "#cbd5e1";
    return `
      <circle cx="${x(cell.px)}" cy="${y(cell.py)}" r="11" fill="${fill}"
        class="${isTriggerPad ? "dd-heat-trigger" : ""}">
        <title>${escapeHtml(cell.pad_name)} · 触发板 NG ${cell.trigger_ng_count}/${cell.trigger_board_count} · 历史 NG ${cell.history_ng_count}</title>
      </circle>
      <text x="${x(cell.px)}" y="${y(cell.py) - 14}" class="dd-axis-text" text-anchor="middle">${escapeHtml(cell.pad_name)}</text>
    `;
  });

  return `
    <p class="details">按 SPI 坐标(Comp_PX/PY)排布的焊盘 NG 分布，颜色越深表示触发板上 NG 占比越高，绿色描边为本次触发焊盘：</p>
    <svg viewBox="0 0 ${W} ${H}" width="100%" height="${H}">${dots.join("")}</svg>
  `;
}
