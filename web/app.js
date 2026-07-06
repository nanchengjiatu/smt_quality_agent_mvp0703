const DATA_PATHS = {
  abnormals: "../output/abnormal_results.json",
  cases: "../output/quality_cases.json",
  summary: "../output/dashboard_summary.json",
  top: "../output/dashboard_top.json",
  analysis: "../output/param_analysis.json",
  drilldown: "../output/drilldown.json",
  warning: "../output/early_warning.json",
};

const API_REFRESH = "/api/refresh";
const API_LIVE = "/api/live";
const API_DATASOURCE = "/api/datasource";
const API_RULES = "/api/rules";
const API_ONTOLOGY = "/api/ontology";
const LIVE_POLL_MS = 4000;

const state = {
  activeView: "abnormal",
  abnormals: [],
  cases: [],
  summary: {},
  top: {},
  analysis: null,
  drilldown: null,
  rules: null,
  ruleType: "",
  // Per-stage status from the last /api/refresh, keyed by stage name.
  // Lets each view show an honest "load failed" message instead of a blank.
  stageStatus: {},
  // Last data version seen from /api/live; a higher one means the server
  // re-ran the pipeline (new SPI data) so we reload automatically.
  liveVersion: 0,
  // Summary from the previous load, for showing metric deltas (▲/▼).
  prevSummary: {},
  // Keys of abnormals seen on the previous load, to detect newly-added rows.
  prevAbnormalKeys: new Set(),
  // Deterministic drilldown trigger ids from the previous load; a new id on an
  // auto-update means a fresh three-board run fired and deserves a callout.
  prevTriggerIds: new Set(),
  // Page-alert warning ids from the previous load, for the L3 drift toast.
  prevAlertIds: new Set(),
  warning: null,
  // The knowledge page defaults to the mechanism handbook; the maintenance
  // view (axes/graph/pipeline) opens on demand and survives re-renders.
  maintViewOpen: false,
  // Keys flagged as new on the last auto-update, flashed once then cleared.
  newAbnormalKeys: new Set(),
};

let liveTimer = null;

const viewRoot = document.getElementById("viewRoot");
const dataStatus = document.getElementById("dataStatus");
const defectFilter = document.getElementById("defectFilter");
const riskFilter = document.getElementById("riskFilter");
const searchInput = document.getElementById("searchInput");

document.getElementById("refreshButton").addEventListener("click", refreshData);
document.getElementById("datasourceButton").addEventListener("click", openDatasourceDialog);
document.getElementById("llmButton").addEventListener("click", openLlmDialog);
document.getElementById("rulesButton").addEventListener("click", () => setActiveView("rules"));

document.querySelectorAll(".tab").forEach((button) => {
  button.addEventListener("click", () => setActiveView(button.dataset.view));
});

[defectFilter, riskFilter, searchInput].forEach((control) => {
  control.addEventListener("input", render);
});

function setActiveView(viewName) {
  state.activeView = viewName || "abnormal";
  document.querySelectorAll(".tab").forEach((item) => {
    item.classList.toggle("active", item.dataset.view === state.activeView);
  });
  const url = new URL(window.location.href);
  url.searchParams.set("view", state.activeView);
  window.history.replaceState({}, "", url);
  render();
}

function initialView() {
  const params = new URLSearchParams(window.location.search);
  const view = params.get("view") || window.location.hash.replace(/^#/, "");
  return ["abnormal", "warning", "cases", "dashboard", "events", "rules"].includes(view) ? view : "abnormal";
}

// Re-run the analysis pipeline on the server, then reload the generated JSON.
async function refreshData() {
  const button = document.getElementById("refreshButton");
  button.classList.add("spinning");
  button.disabled = true;
  dataStatus.textContent = "正在重新分析数据...";
  try {
    const response = await fetch(API_REFRESH, { method: "POST" });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    const report = await response.json();
    state.stageStatus = {};
    (report.stages || []).forEach((stage) => {
      state.stageStatus[stage.stage] = stage;
    });
    await loadData(report);
    if (report.version) {
      // Adopt the version we just produced so live polling doesn't re-trigger.
      state.liveVersion = report.version;
    }
  } catch (error) {
    dataStatus.textContent = `刷新失败：${error.message}（确认已用 python3 serve.py 启动服务）`;
  } finally {
    button.classList.remove("spinning");
    button.disabled = false;
  }
}

async function loadData(meta) {
  if (!meta) {
    dataStatus.textContent = "加载数据中...";
  }
  try {
    const [abnormals, cases, summary, top, analysis, drilldown, rules, ontology, warning] = await Promise.all([
      fetchJson(DATA_PATHS.abnormals),
      fetchJson(DATA_PATHS.cases),
      fetchJson(DATA_PATHS.summary),
      fetchJson(DATA_PATHS.top),
      fetchJson(DATA_PATHS.analysis).catch(() => null),
      fetchJson(DATA_PATHS.drilldown).catch(() => null),
      fetchJson(API_RULES).catch(() => null),
      fetchJson(API_ONTOLOGY).catch(() => null),
      fetchJson(DATA_PATHS.warning).catch(() => null),
    ]);

    // Work out deltas and newly-added abnormals before overwriting state.
    const prevKeys = state.prevAbnormalKeys || new Set();
    const newlyAdded = (meta && meta.auto)
      ? abnormals.filter((item) => !prevKeys.has(abnormalKey(item)))
      : [];
    const triggerIds = ((drilldown || {}).triggers || []).map((item) => item.trigger_id);
    const newTriggers = (meta && meta.auto)
      ? triggerIds.filter((id) => !state.prevTriggerIds.has(id))
      : [];
    const alertIds = ((warning || {}).warnings || [])
      .filter((item) => item.page_alert)
      .map((item) => item.warning_id);
    const newAlerts = (meta && meta.auto)
      ? alertIds.filter((id) => !state.prevAlertIds.has(id))
      : [];
    state.prevSummary = state.summary || {};
    state.newAbnormalKeys = new Set(newlyAdded.map(abnormalKey));
    state.prevAbnormalKeys = new Set(abnormals.map(abnormalKey));
    state.prevTriggerIds = new Set(triggerIds);
    state.prevAlertIds = new Set(alertIds);

    state.abnormals = abnormals;
    state.cases = cases;
    state.summary = summary;
    state.top = top;
    state.analysis = analysis;
    state.drilldown = drilldown;
    state.rules = rules;
    state.ontology = ontology;
    state.warning = warning;
    dataStatus.textContent = composeStatus(meta, abnormals, cases);
    render();

    if (meta && meta.auto) {
      if (newTriggers.length) {
        showToast(`🔴 检测到 ${newTriggers.length} 个新三板连发触发，请进入下钻分析`);
      } else if (newAlerts.length) {
        showToast(`⚠️ ${newAlerts.length} 条新漂移预警（L3），请查看事前预警页`);
      } else {
        showToast(newlyAdded.length ? `检测到 ${newlyAdded.length} 条新异常` : "数据已更新");
      }
    }
  } catch (error) {
    dataStatus.textContent = "数据加载失败，请先运行 python3 serve.py";
    viewRoot.innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`;
  }
}

function composeStatus(meta, abnormals, cases) {
  let text = `已加载 ${abnormals.length} 条异常，${cases.length} 个质量案例`;
  if (meta && meta.generated_at) {
    text += meta.auto ? ` · 自动更新于 ${meta.generated_at}` : ` · 更新于 ${meta.generated_at}`;
    const failed = (meta.stages || []).filter((stage) => !stage.ok).map((stage) => stage.stage);
    if (failed.length) {
      text += ` · 部分失败：${failed.join("、")}`;
    }
  }
  return text;
}

// Poll the server's data version; reload automatically when the SPI table
// has changed (the watcher re-ran the pipeline and bumped the version).
async function pollLive() {
  try {
    const response = await fetch(`${API_LIVE}?t=${Date.now()}`);
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    const live = await response.json();
    updateLiveBadge(live);
    if (state.liveVersion === 0) {
      state.liveVersion = live.version;
      return;
    }
    if (live.version > state.liveVersion) {
      state.liveVersion = live.version;
      // The auto-run may have changed any stage; per-stage detail isn't in
      // /api/live, so drop stale stage status and rely on null-data fallbacks.
      state.stageStatus = {};
      await loadData({ generated_at: live.updated_at, auto: true });
    }
  } catch (error) {
    updateLiveBadge(null);
  }
}

function updateLiveBadge(live) {
  const badge = document.getElementById("liveBadge");
  if (!badge) {
    return;
  }
  if (!live) {
    badge.textContent = "● 离线";
    badge.className = "live-badge offline";
    badge.title = "无法连接服务";
    return;
  }
  if (!live.watching) {
    badge.textContent = "● 手动";
    badge.className = "live-badge manual";
    badge.title = "实时监听未开启（--no-watch）";
    return;
  }
  if (live.last_error) {
    badge.textContent = "● 实时(异常)";
    badge.className = "live-badge warn";
    badge.title = `监听出错：${live.last_error}`;
    return;
  }
  badge.textContent = "● 实时";
  badge.className = "live-badge on";
  badge.title = live.last_check ? `最近检查：${live.last_check}` : "实时监听中";
}

function startLivePolling() {
  if (liveTimer) {
    clearInterval(liveTimer);
  }
  pollLive();
  liveTimer = setInterval(pollLive, LIVE_POLL_MS);
}

// If a stage failed during the last refresh, return its error message so a
// view can show an honest failure state instead of a generic empty one.
function stageError(stageName) {
  const stage = state.stageStatus[stageName];
  return stage && stage.ok === false ? stage.error || "数据加载失败" : null;
}

async function fetchJson(path) {
  const response = await fetch(`${path}?t=${Date.now()}`);
  if (!response.ok) {
    throw new Error(`${path} HTTP ${response.status}`);
  }
  return response.json();
}

function render() {
  renderMetrics();
  if (state.activeView === "abnormal") {
    renderAbnormalView();
  } else if (state.activeView === "warning") {
    renderWarningView();
  } else if (state.activeView === "cases") {
    renderCaseView();
  } else if (state.activeView === "events") {
    renderEventsView();
  } else if (state.activeView === "rules") {
    renderRulesView();
  } else {
    renderDashboardView();
  }
}

function renderMetrics() {
  const summary = state.summary || {};
  const prev = state.prevSummary || {};
  let metrics;

  if (state.activeView === "rules") {
    const catalog = state.rules || {};
    const rules = catalog.rules || [];
    const typeCount = new Set(rules.map((item) => item.rule_type)).size;
    metrics = [
      { label: "规则总数", value: catalog.rule_count || rules.length },
      { label: "规则类型", value: typeCount },
      { label: "根因规则", value: rules.filter((item) => String(item.rule_type || "").includes("cause")).length },
      { label: "处置规则", value: rules.filter((item) => item.rule_type === "disposition").length },
      { label: "目录版本", value: catalog.version || "-" },
      { label: "来源", value: "knowledge_base" },
    ];
  } else if (state.activeView === "warning") {
    const report = state.warning || {};
    const ewSummary = report.summary || {};
    const params = report.params || {};
    metrics = [
      { label: "监控 Pad 数", value: ewSummary.pads_monitored },
      { label: "页面告警", value: ewSummary.page_alerts ?? 0, tone: "danger" },
      { label: "待确认新常态", value: ewSummary.pending_new_baseline ?? 0, tone: "warn" },
      { label: "活动 episode", value: ewSummary.active_episodes ?? 0 },
      { label: "NG 观测下界", value: report.ng_floor_avdp != null ? `${report.ng_floor_avdp}%` : "-" },
      { label: "EWMA 参数", value: params.lambda != null ? `λ${params.lambda} / L${params.L}` : "-" },
    ];
  } else if (state.activeView === "events") {
    const overview = (state.analysis || {}).data_overview || {};
    metrics = [
      { label: "检测记录", value: overview.record_count },
      { label: "生产板数", value: overview.board_count },
      { label: "首次NG点", value: overview.ng_count, tone: "warn" },
      { label: "NG板数", value: overview.ng_board_count, tone: "danger" },
      { label: "聚集事件", value: ((state.analysis || {}).events || []).length },
      { label: "焊点不良率", value: overview.defect_rate_percent, percent: true },
      { label: "板级直通率", value: overview.board_pass_rate_percent, percent: true, tone: "ok" },
      { label: "复测有效率", value: overview.recheck_effective_rate, rate: true, tone: "ok" },
    ];
  } else if (state.activeView === "cases") {
    const cases = state.cases || [];
    metrics = [
      { label: "案例总数", value: cases.length, tone: "warn" },
      { label: "未关闭案例", value: cases.filter((item) => !["已关闭", "关闭"].includes(item.status)).length },
      { label: "高风险案例", value: cases.filter((item) => item.risk_level === "高").length, tone: "danger" },
      { label: "中风险案例", value: cases.filter((item) => item.risk_level === "中").length },
      { label: "多锡案例", value: cases.filter((item) => item.defect_type === "多锡").length },
      { label: "少锡案例", value: cases.filter((item) => item.defect_type === "少锡").length },
      { label: "关联异常", value: cases.reduce((sum, item) => sum + (item.abnormal_count || 0), 0) },
      { label: "复测有效率", value: summary.recheck_effective_rate, rate: true, tone: "ok" },
    ];
  } else {
    metrics = [
      { label: "异常总数", key: "abnormal_count", tone: "warn" },
      { label: "少锡", key: "less_solder_count" },
      { label: "多锡", key: "more_solder_count" },
      { label: "未关闭案例", key: "open_case_count" },
      { label: "高风险", key: "high_risk_count", tone: "danger" },
      { label: "中风险", key: "medium_risk_count" },
      { label: "低风险", key: "low_risk_count" },
      { label: "复测有效率", key: "recheck_effective_rate", rate: true, tone: "ok" },
    ];
  }

  document.getElementById("metrics").innerHTML = metrics.map((metric) => {
    const raw = Object.hasOwn(metric, "value") ? metric.value : summary[metric.key];
    const value = metric.rate
      ? formatRate(raw)
      : metric.percent && raw != null ? `${raw}%` : (raw ?? 0);
    const delta = metric.key && !metric.rate ? deltaBadge(raw, prev[metric.key]) : "";
    return `
      <article class="metric${metric.tone ? " tone-" + metric.tone : ""}">
        <span>${metric.label}</span>
        <strong>${value}${delta}</strong>
      </article>
    `;
  }).join("");
  renderMetricsFootnote();
}

// Secondary reading line under the metric tiles: the primary numbers are
// window-scoped, so the sliding-window scope (only once data has actually
// outgrown the window) and the whole-table cumulative ride along here.
function renderMetricsFootnote() {
  const footnote = document.getElementById("metricsFootnote");
  if (!footnote) {
    return;
  }
  const summary = state.summary || {};
  if (!["abnormal", "cases", "dashboard"].includes(state.activeView)) {
    footnote.innerHTML = "";
    return;
  }
  const parts = [];
  const scope = summary.scope || {};
  if (scope.window_boards > 0 && scope.loaded_boards >= scope.window_boards) {
    parts.push(`统计口径：最近 ${scope.loaded_boards} 块板（滑动窗口，更早的板已滚出实时视图）`);
  }
  const cumulative = summary.cumulative;
  if (cumulative && cumulative.row_count != null) {
    let text = `数据源累计：${cumulative.board_count} 块板 · ${cumulative.row_count} 条记录 · `
      + `NG ${cumulative.ng_row_count} 条 / ${cumulative.ng_board_count} 板`;
    if (cumulative.first_time && cumulative.latest_time) {
      text += `（${cumulative.first_time} ~ ${cumulative.latest_time}）`;
    }
    parts.push(text);
  }
  footnote.innerHTML = parts.map((text) => `<span>${escapeHtml(text)}</span>`).join("");
}

// Small ▲/▼ change indicator vs the previous load; neutral colour because the
// "good" direction differs per metric (more abnormals bad, higher rate good).
function deltaBadge(current, previous) {
  if (current == null || previous == null || current === previous) {
    return "";
  }
  const diff = current - previous;
  return `<em class="delta">${diff > 0 ? "▲" : "▼"}${Math.abs(diff)}</em>`;
}

function abnormalKey(item) {
  return [item.board_sn, item.component, item.pad, item.inspect_time, item.defect_type].join("|");
}

function showToast(text) {
  let toast = document.getElementById("toast");
  if (!toast) {
    toast = document.createElement("div");
    toast.id = "toast";
    toast.className = "toast";
    document.body.appendChild(toast);
  }
  toast.textContent = text;
  toast.classList.add("show");
  clearTimeout(showToast._timer);
  showToast._timer = setTimeout(() => toast.classList.remove("show"), 4000);
}

async function openDatasourceDialog() {
  let config = null;
  try {
    config = await fetchJson(API_DATASOURCE);
  } catch (error) {
    showToast(`读取数据源配置失败：${error.message}`);
    config = {
      type: "postgresql",
      host: "",
      port: 5432,
      database: "l780db",
      user: "",
      password: "",
      tables: { full_spi: "full_excel0623" },
      fields: { time: "fdate" },
      refresh_interval_seconds: 30,
    };
  }

  const existing = document.getElementById("datasourceOverlay");
  if (existing) {
    existing.remove();
  }

  const overlay = document.createElement("div");
  overlay.id = "datasourceOverlay";
  overlay.className = "modal-overlay";
  overlay.innerHTML = `
    <div class="modal-panel datasource-modal" role="dialog" aria-modal="true" aria-labelledby="datasourceTitle">
      <div class="modal-head">
        <div>
          <h2 id="datasourceTitle">数据库连接</h2>
          <p>低频配置入口。保存配置后不会自动刷新数据，需要手动点击刷新。</p>
        </div>
        <button class="modal-close" data-ds-close aria-label="关闭">×</button>
      </div>
      <form id="datasourceForm" class="datasource-form" data-password-set="${config.password_set ? "1" : "0"}">
        <label>
          数据库类型
          <input name="type" value="PostgreSQL" disabled>
        </label>
        <label>
          Host
          <input name="host" value="${escapeHtml(config.host || "")}" placeholder="留空使用本机默认 socket">
        </label>
        <label>
          Port
          <input name="port" type="number" min="1" max="65535" value="${escapeHtml(config.port || 5432)}">
        </label>
        <label>
          Database
          <input name="database" required value="${escapeHtml(config.database || "l780db")}">
        </label>
        <label>
          User
          <input name="user" value="${escapeHtml(config.user || "")}" autocomplete="username">
        </label>
        <label>
          Password
          <input name="password" type="password" value="" placeholder="${config.password_set ? "留空表示不修改已保存密码" : "可选"}" autocomplete="current-password">
        </label>
        <label>
          SPI 明细表（生产实时写入表）
          <input name="full_spi" required value="${escapeHtml(((config.tables || {}).full_spi) || "full_excel0623")}">
        </label>
        <label>
          时间字段
          <input name="time" required value="${escapeHtml(((config.fields || {}).time) || "fdate")}">
        </label>
        <label>
          刷新间隔(秒)
          <input name="refresh_interval_seconds" type="number" min="5" value="${escapeHtml(config.refresh_interval_seconds || 30)}">
        </label>
      </form>
      <div id="datasourceResult" class="datasource-result"></div>
      <div class="modal-actions">
        <button type="button" class="secondary-button" id="datasourceTest">测试连接</button>
        <button type="button" class="secondary-button" data-ds-close>取消</button>
        <button type="button" class="primary-button" id="datasourceSave">保存配置</button>
      </div>
    </div>
  `;
  document.body.appendChild(overlay);
  overlay.querySelectorAll("[data-ds-close]").forEach((button) => {
    button.addEventListener("click", closeDatasourceDialog);
  });
  overlay.addEventListener("click", (event) => {
    if (event.target === overlay) {
      closeDatasourceDialog();
    }
  });
  overlay.querySelector("#datasourceTest").addEventListener("click", testDatasourceFromForm);
  overlay.querySelector("#datasourceSave").addEventListener("click", saveDatasourceFromForm);
}

// ------- P2 LLM 问答配置：六家提供商，三协议适配，密钥只存本机 -------

async function openLlmDialog() {
  let config = null;
  try {
    config = await fetchJson("/api/llm");
  } catch (error) {
    showToast(`读取 LLM 配置失败：${error.message}`);
    return;
  }
  const providers = config.providers || {};

  const existing = document.getElementById("llmOverlay");
  if (existing) {
    existing.remove();
  }
  const overlay = document.createElement("div");
  overlay.id = "llmOverlay";
  overlay.className = "modal-overlay";
  overlay.innerHTML = `
    <div class="modal-panel datasource-modal" role="dialog" aria-modal="true" aria-labelledby="llmTitle">
      <div class="modal-head">
        <div>
          <h2 id="llmTitle">LLM 问答配置</h2>
          <p>下钻对话优先由所选大模型回答（基于本次触发的分析契约与机理目录 grounding）；未启用或调用失败时自动回退离线规则问答。密钥仅保存在本机 config/llm.json。</p>
        </div>
        <button class="modal-close" data-llm-close aria-label="关闭">×</button>
      </div>
      <form id="llmForm" class="datasource-form" data-key-set="${config.key_set ? "1" : "0"}">
        <div class="llm-mode">
          <span class="llm-mode-title">回答模式</span>
          <div class="llm-mode-options" id="llmModeOptions">
            <label class="llm-mode-option${config.enabled ? "" : " selected"}">
              <input type="radio" name="enabled" value="0" ${config.enabled ? "" : "checked"}>
              <strong>离线规则问答</strong>
              <small>不调用任何外部接口，基于本次触发的分析契约作答</small>
            </label>
            <label class="llm-mode-option${config.enabled ? " selected" : ""}">
              <input type="radio" name="enabled" value="1" ${config.enabled ? "checked" : ""}>
              <strong>LLM 大模型回答</strong>
              <small>由下方所选提供商回答，调用失败自动回退离线规则</small>
            </label>
          </div>
        </div>
        <label>
          提供商
          <select name="provider" id="llmProvider">
            ${Object.entries(providers).map(([name, info]) => `
              <option value="${escapeHtml(name)}" ${name === config.provider ? "selected" : ""}>${escapeHtml(info.label)}</option>
            `).join("")}
          </select>
        </label>
        <label>
          API Key
          <input name="api_key" type="password" value="" placeholder="${config.key_set ? "留空表示不修改已保存密钥" : "必填"}" autocomplete="off">
        </label>
        <label>
          模型
          <input name="model" id="llmModel" value="${escapeHtml(config.model || "")}">
        </label>
        <label class="llm-wide">
          接口地址（Base URL，代理/网关可改）
          <input name="base_url" id="llmBaseUrl" value="${escapeHtml(config.base_url || "")}">
        </label>
        <label>
          超时(秒)
          <input name="timeout_seconds" type="number" min="5" value="${escapeHtml(config.timeout_seconds || 30)}">
        </label>
      </form>
      <div id="llmResult" class="datasource-result"></div>
      <div class="modal-actions">
        <button type="button" class="secondary-button" id="llmTest">测试连接</button>
        <button type="button" class="secondary-button" data-llm-close>取消</button>
        <button type="button" class="primary-button" id="llmSave">保存配置</button>
      </div>
    </div>
  `;
  document.body.appendChild(overlay);
  overlay.querySelectorAll("[data-llm-close]").forEach((button) => {
    button.addEventListener("click", () => overlay.remove());
  });
  overlay.addEventListener("click", (event) => {
    if (event.target === overlay) {
      overlay.remove();
    }
  });
  overlay.querySelectorAll('#llmModeOptions input[type="radio"]').forEach((radio) => {
    radio.addEventListener("change", () => {
      overlay.querySelectorAll(".llm-mode-option").forEach((option) => {
        option.classList.toggle("selected", option.querySelector("input").checked);
      });
    });
  });
  overlay.querySelector("#llmProvider").addEventListener("change", (event) => {
    const info = providers[event.target.value] || {};
    overlay.querySelector("#llmModel").value = info.default_model || "";
    overlay.querySelector("#llmBaseUrl").value = info.base_url || "";
  });
  overlay.querySelector("#llmTest").addEventListener("click", () => llmRequest("/api/llm/test", "测试中...", (body) =>
    `连接成功：${body.provider} · ${body.model} · ${body.latency_ms}ms · 回复「${body.reply}」`));
  overlay.querySelector("#llmSave").addEventListener("click", () => llmRequest("/api/llm", "保存中...", () => "配置已保存。"));
}

function llmPayloadFromForm() {
  const form = document.getElementById("llmForm");
  const data = new FormData(form);
  const key = String(data.get("api_key") || "");
  return {
    enabled: String(data.get("enabled")) === "1",
    provider: String(data.get("provider") || ""),
    api_key: key || (form.dataset.keySet === "1" ? "******" : ""),
    model: String(data.get("model") || "").trim(),
    base_url: String(data.get("base_url") || "").trim(),
    timeout_seconds: Number(data.get("timeout_seconds") || 30),
  };
}

async function llmRequest(endpoint, busyText, successText) {
  const result = document.getElementById("llmResult");
  result.className = "datasource-result";
  result.textContent = busyText;
  try {
    const response = await fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(llmPayloadFromForm()),
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok || body.ok === false) {
      throw new Error(body.error || `HTTP ${response.status}`);
    }
    result.className = "datasource-result ok";
    result.textContent = successText(body);
    const form = document.getElementById("llmForm");
    if (endpoint === "/api/llm" && body.key_set) {
      form.dataset.keySet = "1";
    }
  } catch (error) {
    result.className = "datasource-result error";
    result.textContent = `失败：${error.message}`;
  }
}

function closeDatasourceDialog() {
  const overlay = document.getElementById("datasourceOverlay");
  if (overlay) {
    overlay.remove();
  }
}

function datasourcePayloadFromForm() {
  const form = document.getElementById("datasourceForm");
  const data = new FormData(form);
  const password = String(data.get("password") || "");
  return {
    type: "postgresql",
    host: String(data.get("host") || "").trim(),
    port: Number(data.get("port") || 5432),
    database: String(data.get("database") || "").trim(),
    user: String(data.get("user") || "").trim(),
    password: password || (form.dataset.passwordSet === "1" ? "******" : ""),
    tables: {
      full_spi: String(data.get("full_spi") || "").trim(),
    },
    fields: {
      time: String(data.get("time") || "").trim(),
    },
    refresh_interval_seconds: Number(data.get("refresh_interval_seconds") || 30),
  };
}

function setDatasourceResult(message, tone = "") {
  const result = document.getElementById("datasourceResult");
  if (!result) {
    return;
  }
  result.className = `datasource-result ${tone}`;
  result.textContent = message;
}

async function postDatasource(path, payload) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok || body.ok === false) {
    throw new Error(body.error || `HTTP ${response.status}`);
  }
  return body;
}

async function testDatasourceFromForm() {
  setDatasourceResult("正在测试连接...");
  try {
    const result = await postDatasource(`${API_DATASOURCE}/test`, datasourcePayloadFromForm());
    setDatasourceResult(
      `连接成功：${result.database} / ${result.table}，${result.row_count} 行，最新时间 ${result.latest_time || "-"}`,
      "ok",
    );
  } catch (error) {
    setDatasourceResult(`连接失败：${error.message}`, "error");
  }
}

async function saveDatasourceFromForm() {
  setDatasourceResult("正在保存配置...");
  try {
    await postDatasource(API_DATASOURCE, datasourcePayloadFromForm());
    setDatasourceResult("配置已保存。需要应用新数据源时，请点击右上角刷新。", "ok");
    showToast("数据库连接配置已保存");
  } catch (error) {
    setDatasourceResult(`保存失败：${error.message}`, "error");
  }
}

function renderAbnormalView() {
  const rows = filterItems(state.abnormals);
  // Snapshot the flash keys then clear, so newly-added rows highlight once.
  const flashKeys = state.newAbnormalKeys || new Set();
  state.newAbnormalKeys = new Set();
  if (!rows.length) {
    viewRoot.innerHTML = `<div class="empty">没有匹配的异常记录</div>`;
    return;
  }

  viewRoot.innerHTML = `
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>时间</th>
            <th>PCB</th>
            <th>位置</th>
            <th>异常</th>
            <th>模式</th>
            <th>风险</th>
            <th>主指标</th>
            <th>偏离</th>
            <th>建议动作</th>
          </tr>
        </thead>
        <tbody>
          ${rows.map((item) => renderAbnormalRow(item, flashKeys)).join("")}
        </tbody>
      </table>
    </div>
  `;
}

function renderAbnormalRow(item, flashKeys = new Set()) {
  const trigger = findDrilldownTrigger((state.drilldown || {}).triggers, item.component, item.pad);
  const badge = trigger
    ? `<button class="dd-entry-badge" data-dd="${escapeHtml(trigger.trigger_id)}" title="进入下钻分析">🔴 三板连发</button>`
    : "";
  // Only true three-consecutive-board (三板连发) rows get a persistent red
  // highlight; all other rows stay plain.
  const classes = [];
  if (trigger) {
    classes.push("row-trigger");
  }
  if (flashKeys.has(abnormalKey(item))) {
    classes.push("is-new");
  }
  const rowClass = classes.join(" ");
  return `
    <tr class="${escapeHtml(rowClass)}">
      <td>${escapeHtml(item.inspect_time)}</td>
      <td>${escapeHtml(item.board_sn)}</td>
      <td>${escapeHtml(item.component)} / Pad ${escapeHtml(item.pad)} ${badge}</td>
      <td class="${defectClass(item.defect_type)}">${escapeHtml(item.defect_type)}</td>
      <td>${escapeHtml(item.abnormal_pattern)}</td>
      <td><span class="badge risk-${escapeHtml(item.risk_level)}">${escapeHtml(item.risk_level)}</span></td>
      <td>${escapeHtml(item.main_metric)} ${formatNumber(item.actual_value)}</td>
      <td>${formatNumber(item.deviation_percent)}%</td>
      <td class="details">${escapeHtml((item.suggested_action || []).slice(0, 2).join("；"))}</td>
    </tr>
  `;
}

function renderCaseView() {
  const rows = filterItems(state.cases);
  if (!rows.length) {
    viewRoot.innerHTML = `<div class="empty">没有匹配的质量案例</div>`;
    return;
  }

  viewRoot.innerHTML = `
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>案例</th>
            <th>创建时间</th>
            <th>位置</th>
            <th>异常</th>
            <th>风险</th>
            <th>证据</th>
            <th>推荐原因</th>
            <th>状态</th>
          </tr>
        </thead>
        <tbody>
          ${rows.map(renderCaseRow).join("")}
        </tbody>
      </table>
    </div>
  `;
}

function renderCaseRow(item) {
  return `
    <tr>
      <td>${escapeHtml(item.case_id)}</td>
      <td>${escapeHtml(item.created_at)}</td>
      <td>${escapeHtml(item.component)} / ${escapeHtml(item.pad)}</td>
      <td>
        <span class="${defectClass(item.defect_type)}">${escapeHtml(item.defect_type)}</span>
        <div class="details">${escapeHtml(item.abnormal_pattern)}</div>
      </td>
      <td><span class="badge risk-${escapeHtml(item.risk_level)}">${escapeHtml(item.risk_level)}</span></td>
      <td class="details">${escapeHtml(item.evidence_summary)}</td>
      <td class="details">${escapeHtml((item.root_cause_guess || []).join("、"))}</td>
      <td>${escapeHtml(item.status)}</td>
    </tr>
  `;
}

// ------- 事前预警（P0）：漂移告警卡 + 待确认新常态 + Pad 健康矩阵 -------

function renderWarningView() {
  const report = state.warning;
  if (!report) {
    const error = stageError("early_warning");
    viewRoot.innerHTML = `<div class="empty">${error ? escapeHtml(error) : "事前预警数据尚未生成，请刷新数据。"}</div>`;
    return;
  }
  const warnings = report.warnings || [];
  const alerts = warnings.filter((item) => item.page_alert);
  const pending = warnings.filter((item) => item.pending_new_baseline);
  const recoveredCount = warnings.filter((item) => item.status === "recovered").length;

  const alertBlock = alerts.length
    ? alerts.map((item) => renderWarningCard(item, false)).join("")
    : `<div class="empty">当前无漂移预警——${(report.summary || {}).pads_monitored ?? 0} 个 Pad 的 EWMA 均在控制限内。</div>`;

  const pendingBlock = pending.length
    ? pending.map((item) => renderWarningCard(item, true)).join("")
    : `<div class="empty">没有待确认的台阶式新常态。</div>`;

  viewRoot.innerHTML = `
    <section class="ew-section">
      <h3>漂移预警（活动 L3，越限即将成不良）</h3>
      ${alertBlock}
    </section>
    <section class="ew-section">
      <h3>待确认新常态（越限持续超 100 板的台阶迁移）</h3>
      <p class="details">这些 Pad 的偏差水位整体抬升后不再回落。确认现场无工艺问题后可"接受为新基线"，监控将以抬升后的水平重新建基线。</p>
      ${pendingBlock}
    </section>
    <section class="ew-section">
      <h3>Pad 健康矩阵（按漂移裕度着色，margin = 距 NG 观测水位的 3σ 倍数）</h3>
      ${renderHealthMatrix(report)}
    </section>
    <p class="details">已恢复 episode ${recoveredCount} 条 · ${(report.caveats || []).map((text) => escapeHtml(text)).join(" ")}</p>
  `;

  viewRoot.querySelectorAll("[data-accept-baseline]").forEach((button) => {
    button.addEventListener("click", () => acceptBaseline(button.dataset.acceptBaseline, button));
  });
}

function renderWarningCard(item, pendingMode) {
  const title = item.is_board_series ? "整板均值" : item.pad_name;
  const mechanisms = (item.mechanism_candidates || []).map((candidate) => `
    <li>
      <strong>${escapeHtml(candidate.cause)}</strong>
      <span class="details">${escapeHtml(candidate.early_warning)} · ${escapeHtml(candidate.action)}</span>
    </li>
  `).join("");
  const acceptButton = pendingMode
    ? `<button class="secondary-button" data-accept-baseline="${escapeHtml(item.warning_id)}">接受为新基线</button>`
    : "";
  return `
    <article class="ew-card ${pendingMode ? "ew-pending" : "ew-alert"}">
      <header class="ew-card-head">
        <div>
          <strong title="${escapeHtml(item.warning_id)}">${escapeHtml(title)}</strong>
          <span class="badge risk-${item.level >= 3 ? "高" : "中"}">L${item.level}</span>
          <span class="details">${escapeHtml(item.model)} · 越限指标 ${(item.metrics || []).map((m) => escapeHtml(m.replace("comp_", ""))).join("/")}</span>
        </div>
        <div class="details">
          自 ${escapeHtml(item.start_time)} 起 · 已越限 ${item.boards_above} 板
          ${item.margin != null ? ` · margin ${formatNumber(item.margin)}` : ""}
          ${acceptButton}
        </div>
      </header>
      ${renderWarningSparkline(item.series || [])}
      <ul class="ew-mechanisms">${mechanisms}</ul>
    </article>
  `;
}

function renderWarningSparkline(series) {
  if (!series.length) {
    return "";
  }
  const width = 640;
  const height = 110;
  const pad = 6;
  const values = [];
  series.forEach((point) => {
    [point.value, point.ewma, point.limit].forEach((value) => {
      if (value != null) {
        values.push(value);
      }
    });
  });
  if (!values.length) {
    return "";
  }
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const x = (index) => pad + (index / Math.max(series.length - 1, 1)) * (width - 2 * pad);
  const y = (value) => height - pad - ((value - min) / span) * (height - 2 * pad);
  const line = (field) => series
    .map((point, index) => point[field] == null ? null : `${x(index).toFixed(1)},${y(point[field]).toFixed(1)}`)
    .filter(Boolean)
    .join(" ");
  const ngDots = series
    .map((point, index) => point.is_ng
      ? `<circle cx="${x(index).toFixed(1)}" cy="${y(point.value ?? min).toFixed(1)}" r="3.5" class="ew-ng-dot"/>`
      : "")
    .join("");
  return `
    <svg class="ew-spark" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" role="img" aria-label="EWMA 漂移曲线">
      <polyline class="ew-raw" points="${line("value")}"/>
      <polyline class="ew-limit" points="${line("limit")}"/>
      <polyline class="ew-ewma" points="${line("ewma")}"/>
      ${ngDots}
    </svg>
    <p class="details ew-legend">灰=逐板实测 · 蓝=EWMA · 红虚线=控制限 · 红点=NG</p>
  `;
}

function renderHealthMatrix(report) {
  const pads = (report.pad_health || []).filter((item) => !item.is_board_series);
  const board = (report.pad_health || []).find((item) => item.is_board_series);
  const sorted = [...pads].sort((a, b) => (a.margin ?? Infinity) - (b.margin ?? Infinity));
  const tile = (item, label) => {
    let tone = "ok";
    if (item.episode_active || (item.margin != null && item.margin < 0)) {
      tone = "danger";
    } else if (item.margin != null && item.margin < 0.5) {
      tone = "warn";
    } else if (item.margin == null) {
      tone = "muted";
    }
    return `
      <div class="ew-tile ew-${tone}${item.episode_active ? " ew-active" : ""}"
           title="EWMA ${item.avdp?.ewma ?? "-"} / 基线 ${item.avdp?.mu ?? "-"} / 限 ${item.avdp?.limit ?? "-"}${item.baseline_accepted ? " · 已接受新基线" : ""}">
        <span>${escapeHtml(label || item.pad_name)}</span>
        <strong>${item.margin != null ? formatNumber(item.margin) : "-"}</strong>
        ${item.level ? `<em>L${item.level}</em>` : ""}
      </div>
    `;
  };
  return `
    ${board ? `<div class="ew-matrix ew-matrix-board">${tile(board, "整板均值")}</div>` : ""}
    <div class="ew-matrix">${sorted.map((item) => tile(item)).join("")}</div>
  `;
}

async function acceptBaseline(warningId, button) {
  if (!window.confirm("确认将该 Pad 当前抬升后的水平接受为新基线？监控将重新建基线，此操作会立即触发一轮重算。")) {
    return;
  }
  button.disabled = true;
  button.textContent = "重算中...";
  try {
    const response = await fetch("/api/warning/accept-baseline", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ warning_id: warningId }),
    });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    const report = await response.json();
    state.stageStatus = {};
    (report.stages || []).forEach((stage) => {
      state.stageStatus[stage.stage] = stage;
    });
    await loadData(report);
    if (report.version) {
      state.liveVersion = report.version;
    }
    showToast("已接受为新基线，监控已按新水平重建");
  } catch (error) {
    showToast(`接受新基线失败：${error.message}`);
    button.disabled = false;
    button.textContent = "接受为新基线";
  }
}

function renderDashboardView() {
  const top = state.top || {};
  viewRoot.innerHTML = `
    <div class="cards-grid">
      ${renderRankPanel("TOP 元件", top.top_components || [], (item) => item.component, (item) => `${item.defect_count} 次 / ${item.main_defect_type}`)}
      ${renderRankPanel("TOP Pad", top.top_pads || [], (item) => `${item.component} Pad${item.pad}`, (item) => `${item.defect_count} 次 / ${item.main_defect_type}`)}
      ${renderRankPanel("异常模式", top.top_patterns || [], (item) => item.abnormal_pattern, (item) => `${item.defect_count} 次`)}
      ${renderRankPanel("质量案例位置", top.top_case_locations || [], (item) => `${item.component} / ${item.pad}`, (item) => `${item.case_count} 个案例`)}
    </div>
  `;
}

function renderEventsView() {
  const analysis = state.analysis;
  if (!analysis) {
    const error = stageError("param_analysis");
    viewRoot.innerHTML = error
      ? `<div class="empty error">事件分析数据加载失败：${escapeHtml(error)}</div>`
      : `<div class="empty">暂无事件分析数据，点击右上角 ↻ 刷新生成</div>`;
    return;
  }

  const overview = analysis.data_overview || {};
  const overviewItems = [
    ["检测记录", overview.record_count ?? "-"],
    ["生产板数", overview.board_count ?? "-"],
    ["首次检测NG", overview.ng_count ?? "-"],
    ["复测NG", overview.recheck_ng_count ?? "-"],
    ["焊点不良率", overview.defect_rate_percent != null ? `${overview.defect_rate_percent}%` : "-"],
    ["板级直通率", overview.board_pass_rate_percent != null ? `${overview.board_pass_rate_percent}%` : "-"],
    ["复测有效率", formatRate(overview.recheck_effective_rate)],
    ["数据时间", (overview.time_range || []).filter(Boolean).join(" ~ ") || "-"],
  ];

  const events = analysis.events || [];
  viewRoot.innerHTML = `
    <div class="event-view">
      <section class="panel">
        <h2>事件总览 <span class="details">${escapeHtml(analysis.source_table || "")}</span></h2>
        <div class="overview-grid">
          ${overviewItems.map(([label, value]) => `
            <div class="overview-item"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>
          `).join("")}
        </div>
      </section>
      ${renderDrilldownPanel()}
      ${renderProcessDimensionsPanel(analysis.process_dimensions)}
      <section class="panel">
        <div class="section-head">
          <h2>参数聚集事件</h2>
          <span class="details">${events.length ? `${events.length} 个事件` : "暂无事件"}</span>
        </div>
        ${events.length
          ? `<div class="event-summary-list">${events.map(renderEventCard).join("")}</div>`
          : `<div class="empty">数据时间范围内未检出聚集事件</div>`}
      </section>
      ${(analysis.caveats || []).length
        ? `<p class="details caveats">说明：${analysis.caveats.map(escapeHtml).join(" ")}</p>`
        : ""}
    </div>
  `;
}

// ------- P1 工艺维度体检：清洗周期 / 停机首板 / 印刷方向 -------

function renderProcessDimensionsPanel(dimensions) {
  if (!dimensions) {
    return "";
  }
  const cards = [
    ["钢网清洗周期效应", dimensions.cleaning_cycle, renderCycleProfile(dimensions.cleaning_cycle)],
    ["停机后首板效应", dimensions.first_board, ""],
    ["印刷方向差异", dimensions.direction, ""],
  ];
  return `
    <section class="panel">
      <div class="section-head">
        <h2>工艺维度体检</h2>
        <span class="details">每轮随数据重算 · ${dimensions.board_count ?? "-"} 块板 · 板均噪声 σ=${dimensions.noise_sd_pp ?? "-"}pp</span>
      </div>
      <div class="pd-cards">
        ${cards.map(([title, item, extraHtml]) => item ? `
          <article class="pd-card pd-${escapeHtml(item.verdict)}">
            <header>
              <strong>${escapeHtml(title)}</strong>
              <span class="pd-verdict">${escapeHtml(item.verdict_label)}</span>
            </header>
            <p>${escapeHtml(item.detail)}</p>
            ${extraHtml}
            ${item.caveat ? `<p class="details">${escapeHtml(item.caveat)}</p>` : ""}
          </article>
        ` : "").join("")}
      </div>
    </section>
  `;
}

function renderCycleProfile(cycle) {
  const profile = (cycle || {}).profile || [];
  if (profile.length < 2) {
    return "";
  }
  const means = profile.map((item) => item.mean);
  const min = Math.min(...means);
  const span = (Math.max(...means) - min) || 1;
  return `
    <div class="pd-profile" title="周期内逐位置板均偏差（第 0 位 = 假定的擦网后首板）">
      ${profile.map((item) => `
        <div class="pd-bar-wrap" title="位置 ${item.position}: ${item.mean}pp (n=${item.count})">
          <div class="pd-bar" style="height:${Math.round(18 + ((item.mean - min) / span) * 42)}px"></div>
          <span>${item.position}</span>
        </div>
      `).join("")}
    </div>
  `;
}

function renderEventCard(event) {
  const firstFinding = (event.findings || [])[0] || "已检出聚集异常，建议进入下钻或调取明细复核。";
  const primaryCandidate = (event.cause_candidates || [])[0] || {};
  const primaryCause = primaryCandidate.cause || (event.suggested_causes || [])[0] || "待现场确认";
  const primaryAction = primaryCandidate.action || (event.suggested_actions || [])[0] || "复核异常时间段、设备参数和首检/复测记录。";
  const primaryRule = primaryCandidate.rule_id || "";

  return `
    <article class="event-row">
      <div class="event-row-main">
        <div class="event-row-title">
          <strong>${escapeHtml(event.event_id)}</strong>
          <span class="${defectClass(event.main_defect_cn)}">${escapeHtml(event.main_defect_cn)}</span>
          <span class="badge risk-高">${escapeHtml(event.scope)}</span>
        </div>
        <p>${escapeHtml(firstFinding)}</p>
        <div class="event-row-meta">
          <span>${escapeHtml(event.model)}</span>
          <span>${escapeHtml(event.machine)}</span>
          <span>${escapeHtml(event.start_time)} ~ ${escapeHtml(event.end_time)}</span>
          <span>${escapeHtml(event.board_count)} 块板 / ${escapeHtml(event.ng_record_count)} 条 NG</span>
          <span>前兆：${escapeHtml((event.precursor || {}).verdict || "-")}</span>
        </div>
      </div>
      <div class="event-row-action">
        <span class="details">首要判断</span>
        <strong>${escapeHtml(primaryCause)}</strong>
        ${primaryRule ? `<small>${escapeHtml(primaryRule)}</small>` : ""}
        <small>${escapeHtml(primaryAction)}</small>
      </div>
    </article>
  `;
}

function renderDrilldownPanel() {
  const error = stageError("drilldown");
  if (error) {
    return `<section class="panel"><h2>三板连发下钻</h2><div class="empty error">下钻数据加载失败：${escapeHtml(error)}</div></section>`;
  }
  const triggers = (state.drilldown || {}).triggers || [];
  if (!triggers.length) {
    return "";
  }
  return `
    <section class="panel">
      <div class="section-head">
        <h2>待处理三板连发</h2>
        <span class="details">${escapeHtml((state.drilldown || {}).trigger_rule || "")}</span>
      </div>
      <div class="decision-list">
        ${triggers.map(renderDrilldownDecisionCard).join("")}
      </div>
    </section>
  `;
}

function renderDrilldownDecisionCard(trigger) {
  const contract = trigger.analysis_contract || {};
  const contractScope = contract.scope || {};
  const contractDisposition = contract.disposition || {};
  const contractTrigger = contract.trigger || {};
  const priority = contractDisposition.priority || "P3";
  return `
    <article class="decision-card priority-${escapeHtml(priority)}">
      <div class="decision-priority">
        <strong>${escapeHtml(priority)}</strong>
        <span>${escapeHtml(contractScope.confidence || "中")}置信</span>
      </div>
      <div class="decision-main">
        <div class="decision-title">
          <strong title="${escapeHtml(trigger.trigger_id)}">触发 #${escapeHtml(trigger.trigger_no || "")} · ${escapeHtml(trigger.pad_name)}</strong>
          <span class="${defectClass(trigger.main_defect_cn)}">${escapeHtml(trigger.main_defect_cn)}</span>
          <span class="badge">${escapeHtml(contractScope.category || "待判定")}</span>
          <span class="badge dd-badge">连续 ${escapeHtml(trigger.trigger_board_count)} 板</span>
        </div>
        <p>${escapeHtml(contractDisposition.suggestion || "按单点异常做快速确认")}</p>
        <div class="decision-meta">
          <span>${escapeHtml(trigger.model)}</span>
          <span>${escapeHtml(trigger.start_time)} ~ ${escapeHtml(trigger.end_time)}</span>
          <span>${escapeHtml(((contract.trend || {}).verdict) || "")}</span>
          <span>${escapeHtml(((contract.recheck || {}).recovery_verdict) || "")}</span>
        </div>
      </div>
      <div class="decision-action">
        <span class="details">第一步动作</span>
        <strong>${escapeHtml(contractDisposition.primary_action || "复核触发 Pad、原始 SPI 图像和事件时段设备记录。")}</strong>
        <button class="dd-enter" data-dd="${escapeHtml(trigger.trigger_id)}">查看下钻</button>
      </div>
    </article>
  `;
}

viewRoot.addEventListener("click", (event) => {
  const maintToggle = event.target.closest("[data-maint-toggle]");
  if (maintToggle) {
    state.maintViewOpen = !state.maintViewOpen;
    render();
    return;
  }
  const ontoNode = event.target.closest("[data-node]");
  if (ontoNode) {
    const id = ontoNode.dataset.node;
    state.ontologyNode = state.ontologyNode === id ? null : id;
    render();
    return;
  }
  if (event.target.closest(".onto-svg")) {
    // 点击图内空白处取消选中
    if (state.ontologyNode) {
      state.ontologyNode = null;
      render();
    }
    return;
  }

  const ruleType = event.target.closest("[data-rule-type]");
  if (ruleType) {
    state.ruleType = ruleType.dataset.ruleType || "";
    render();
    return;
  }

  const entry = event.target.closest("[data-dd]");
  if (!entry) {
    return;
  }
  const triggers = (state.drilldown || {}).triggers || [];
  const trigger = triggers.find((item) => item.trigger_id === entry.dataset.dd);
  if (trigger) {
    openDrilldown(trigger);
  }
});

const ONSET_LABELS = {
  gradual: "渐变(可预警)",
  step: "突变",
  periodic: "周期",
  any: "不定",
};

const CONCEPT_TYPE_LABELS = {
  ProcessStage: "工序阶段",
  EquipmentElement: "设备要素",
  Material: "物料",
  FailureMechanism: "失效机理",
  EvidenceType: "证据",
  SpatialExtent: "空间范围",
  TemporalPattern: "时间模式",
  DataValidity: "数据有效性",
  RootCauseCandidate: "趋势归因词表",
  Disposition: "处置方式",
};

// 本体分层关系图:实体(阶段/部位) ← 机理 → 证据。手写 SVG,零依赖。
function buildOntologyGraph(ontology) {
  const concepts = (ontology || {}).concepts || [];
  const byId = new Map(concepts.map((item) => [item.id, item]));
  const mechanisms = concepts.filter((item) => item.type === "FailureMechanism");
  const stages = concepts.filter((item) => item.type === "ProcessStage");
  const elements = concepts.filter(
    (item) => item.type === "EquipmentElement" || item.type === "Material",
  );
  const evidenceIds = [];
  mechanisms.forEach((mech) => {
    const props = mech.properties || {};
    [...(props.auto_checks || []), ...(props.manual_checks || [])].forEach((id) => {
      if (!evidenceIds.includes(id)) {
        evidenceIds.push(id);
      }
    });
  });
  const evidence = evidenceIds.map((id) => byId.get(id)).filter(Boolean);

  const edges = [];
  mechanisms.forEach((mech) => {
    const props = mech.properties || {};
    if (props.stage) {
      edges.push({ from: props.stage, to: mech.id, kind: "stage" });
    }
    if (props.element) {
      edges.push({ from: props.element, to: mech.id, kind: "element" });
    }
    (props.auto_checks || []).forEach((id) => edges.push({ from: mech.id, to: id, kind: "auto" }));
    (props.manual_checks || []).forEach((id) => edges.push({ from: mech.id, to: id, kind: "manual" }));
  });

  return { byId, edges, columns: [
    { title: "工序阶段", x: 14, w: 108, nodes: stages },
    { title: "部位/物料", x: 168, w: 138, nodes: elements },
    { title: "失效机理", x: 372, w: 200, nodes: mechanisms },
    { title: "证据(自动/人工)", x: 658, w: 282, nodes: evidence },
  ] };
}

function ontologyNeighborhood(graph, selectedId) {
  if (!selectedId) {
    return null;
  }
  const nodes = new Set([selectedId]);
  const edges = new Set();
  graph.edges.forEach((edge, index) => {
    if (edge.from === selectedId || edge.to === selectedId) {
      edges.add(index);
      nodes.add(edge.from);
      nodes.add(edge.to);
    }
  });
  return { nodes, edges };
}

function evidenceNodeClass(concept) {
  const props = concept.properties || {};
  if (props.verification === "manual") {
    return "ev-manual";
  }
  return `ev-${props.availability || "available"}`;
}

function renderOntologyGraph(graph, selectedId) {
  const W = 954;
  const rowH = 31;
  const maxRows = Math.max(...graph.columns.map((column) => column.nodes.length));
  const H = maxRows * rowH + 58;
  const positions = new Map();

  graph.columns.forEach((column) => {
    const step = (H - 46) / column.nodes.length;
    column.nodes.forEach((node, index) => {
      positions.set(node.id, {
        x: column.x,
        y: 38 + step * index + step / 2,
        w: column.w,
      });
    });
  });

  const highlight = ontologyNeighborhood(graph, selectedId);
  const parts = [];

  graph.columns.forEach((column) => {
    parts.push(`<text x="${column.x + column.w / 2}" y="20" class="onto-col-title" text-anchor="middle">${escapeHtml(column.title)}</text>`);
  });

  graph.edges.forEach((edge, index) => {
    const from = positions.get(edge.from);
    const to = positions.get(edge.to);
    if (!from || !to) {
      return;
    }
    const x1 = from.x + from.w;
    const x2 = to.x;
    const mid = (x1 + x2) / 2;
    const dim = highlight && !highlight.edges.has(index) ? " dim" : "";
    parts.push(`<path d="M${x1},${from.y} C${mid},${from.y} ${mid},${to.y} ${x2},${to.y}"
      class="onto-edge onto-edge-${edge.kind}${dim}"/>`);
  });

  graph.columns.forEach((column) => {
    column.nodes.forEach((node) => {
      const pos = positions.get(node.id);
      const classes = ["onto-node"];
      if (node.type === "FailureMechanism") {
        classes.push("onto-mech");
      } else if (node.type === "EvidenceType") {
        classes.push(evidenceNodeClass(node));
      } else {
        classes.push("onto-entity");
      }
      if (selectedId === node.id) {
        classes.push("selected");
      } else if (highlight && !highlight.nodes.has(node.id)) {
        classes.push("dim");
      }
      parts.push(`<g class="${classes.join(" ")}" data-node="${escapeHtml(node.id)}">
        <rect x="${pos.x}" y="${pos.y - 12}" width="${pos.w}" height="24" rx="6"/>
        <text x="${pos.x + pos.w / 2}" y="${pos.y + 4}" text-anchor="middle">${escapeHtml(node.label)}</text>
        <title>${escapeHtml(node.label)}（${escapeHtml(CONCEPT_TYPE_LABELS[node.type] || node.type)}）
${escapeHtml(node.description || "")}</title>
      </g>`);
    });
  });

  return `<svg viewBox="0 0 ${W} ${H}" width="100%" class="onto-svg">${parts.join("")}</svg>`;
}

function renderOntologyDetail(graph, selectedId) {
  if (!selectedId || !graph.byId.has(selectedId)) {
    return `
      <div class="details">点击图中任意节点查看定义、关联与绑定规则；再次点击取消。</div>
      <div class="onto-legend">
        <span><i class="onto-swatch sw-mech"></i>机理</span>
        <span><i class="onto-swatch sw-entity"></i>阶段/部位</span>
        <span><i class="onto-swatch sw-available"></i>证据·已实现</span>
        <span><i class="onto-swatch sw-planned"></i>证据·待实现</span>
        <span><i class="onto-swatch sw-missing"></i>证据·数据未采集</span>
        <span><i class="onto-swatch sw-manual"></i>证据·需人工</span>
        <span><i class="onto-line line-auto"></i>自动核验</span>
        <span><i class="onto-line line-manual"></i>人工确认</span>
      </div>
    `;
  }
  const concept = graph.byId.get(selectedId);
  const props = concept.properties || {};
  const lines = [];
  if (concept.type === "FailureMechanism") {
    if (props.direction) lines.push(`方向：${props.direction}`);
    if (props.onset) lines.push(`起病：${ONSET_LABELS[props.onset] || props.onset}`);
    if (props.signature_text) lines.push(`签名：${props.signature_text}`);
    if (props.early_warning) lines.push(`预警：${props.early_warning}`);
    if (props.action) lines.push(`规范动作：${props.action}`);
  }
  if (concept.type === "EvidenceType") {
    lines.push(props.verification === "manual"
      ? "核验方式：现场人工确认"
      : `核验方式：自动 · ${AVAILABILITY_LABELS[props.availability] || props.availability || ""}`);
  }
  const boundRules = concept.type === "FailureMechanism"
    ? ((state.rules || {}).rules || []).filter(
        (rule) => (rule.output || {}).mechanism_id === selectedId,
      )
    : [];
  return `
    <div class="onto-detail-head">
      <strong>${escapeHtml(concept.label)}</strong>
      <span class="badge">${escapeHtml(CONCEPT_TYPE_LABELS[concept.type] || concept.type)}</span>
      <span class="details">${escapeHtml(concept.id)}</span>
    </div>
    <p class="details">${escapeHtml(concept.description || "")}</p>
    ${lines.length ? `<p class="details">${lines.map(escapeHtml).join("<br>")}</p>` : ""}
    ${concept.type === "FailureMechanism" ? `
      <div class="details"><strong>绑定规则（${boundRules.length}）</strong></div>
      <ul class="onto-rule-links">
        ${boundRules.map((rule) => `
          <li><a href="#mech-${escapeHtml(selectedId)}">${escapeHtml(rule.rule_id)}</a>
            <span class="details">${escapeHtml((rule.output || {}).cause || "")}
            （先验 ${Math.round(((rule.output || {}).confidence_base || 0) * 100)}%）</span></li>
        `).join("") || `<li class="details">暂无绑定规则（第二阶段接入）</li>`}
      </ul>` : ""}
  `;
}

const AVAILABILITY_LABELS = {
  available: "已实现",
  planned: "待实现",
  not_collected: "数据未采集",
};

function renderRulesView() {
  const catalog = state.rules || {};
  const rules = catalog.rules || [];
  if (!rules.length) {
    viewRoot.innerHTML = `
      <section class="panel">
        <h2>规则/知识库</h2>
        <div class="empty">规则目录加载失败，请确认服务已暴露 /api/rules。</div>
      </section>
    `;
    return;
  }

  const query = (searchInput.value || "").trim().toLowerCase();
  const matches = (item) => !query || JSON.stringify(item).toLowerCase().includes(query);

  const decisionRules = rules.filter((rule) => rule.rule_type === "decision").filter(matches);
  const dispositionRules = rules.filter((rule) => rule.rule_type === "disposition").filter(matches);
  const executable = rules.filter(
    (rule) => rule.rule_type !== "decision" && rule.rule_type !== "disposition",
  );

  const rulesByMechanism = new Map();
  executable.forEach((rule) => {
    const key = (rule.output || {}).mechanism_id || "__unbound__";
    if (!rulesByMechanism.has(key)) {
      rulesByMechanism.set(key, []);
    }
    rulesByMechanism.get(key).push(rule);
  });

  const mechanismCards = (catalog.mechanisms || [])
    .map((mech) => ({
      mech,
      rules: (rulesByMechanism.get(mech.mechanism_id) || []).filter(matches),
    }))
    // 搜索时只留有命中规则或机理自身命中的卡片;不搜索时全部展示
    .filter(({ mech, rules: list }) => !query || list.length || matches(mech));
  const unboundRules = (rulesByMechanism.get("__unbound__") || []).filter(matches);
  const shownCount = decisionRules.length + dispositionRules.length + unboundRules.length
    + mechanismCards.reduce((sum, card) => sum + card.rules.length, 0);

  const graph = buildOntologyGraph(state.ontology);
  const concepts = (state.ontology || {}).concepts || [];
  const axes = concepts.filter((item) =>
    ["SpatialExtent", "TemporalPattern", "DataValidity"].includes(item.type));
  const countOf = (type) => concepts.filter((item) => item.type === type).length;
  const entityCount = countOf("ProcessStage") + countOf("EquipmentElement") + countOf("Material");

  // 手册分组：按方向组织，兜底机理单列——读者按"我看到多锡/少锡"入手。
  const groupDefs = [
    ["多锡", "多锡方向", "锡量偏多：残锡转印、塌陷、擦网周期类"],
    ["少锡", "少锡方向", "锡量偏少：堵孔、脱模、供锡类"],
    ["双向", "双向（视具体情形偏多或偏少）", "密合、流变、参数、对位、误判类"],
    ["兜底", "兜底", "证据不足时的保守归因"],
  ];
  const grouped = new Map(groupDefs.map(([key]) => [key, []]));
  mechanismCards.forEach((card) => {
    const key = card.mech.mechanism_id === "mech.undetermined"
      ? "兜底"
      : (grouped.has(card.mech.direction) ? card.mech.direction : "双向");
    grouped.get(key).push(card);
  });

  viewRoot.innerHTML = `
    <section class="rules-view">
      <div class="rules-head">
        <div>
          <h2>失效机理手册</h2>
          <p class="details">根因词表的唯一权威——诊断、预警、对话给出的根因都出自这 ${(catalog.mechanisms || []).length} 个机理。
            每张卡回答：什么现象 → 怎么确认 → 确认后怎么办。
            ${escapeHtml(((state.ontology || {}).version) || "")} · ${escapeHtml(catalog.version || "")}</p>
        </div>
        <div class="rules-count">${shownCount} / ${rules.length}</div>
      </div>

      ${groupDefs.map(([key, title, note]) => {
        const cards = grouped.get(key) || [];
        if (!cards.length) {
          return "";
        }
        return `
          <section class="panel mech-group">
            <h2>${escapeHtml(title)} <span class="details">${cards.length} 个 · ${escapeHtml(note)}</span></h2>
            <div class="mech-list">
              ${cards.map(renderMechanismCard).join("")}
            </div>
          </section>
        `;
      }).join("")}

      ${unboundRules.length ? `
        <section class="panel">
          <h2>形态归因（不绑定机理）<span class="details">趋势形态只是证据，不足以锁定物理机理</span></h2>
          ${unboundRules.map(renderRuleEntry).join("")}
        </section>` : ""}

      <section class="panel maint-panel">
        <button class="maint-toggle" data-maint-toggle>
          ${state.maintViewOpen ? "▾" : "▸"} 维护视图
          <span class="details">改知识库的人看：观测三轴词表 · 机理关系全景 · 诊断决策管道 · 处置阶梯 · 分层原则</span>
        </button>
        ${state.maintViewOpen ? `
          <div class="layer-guide">
            <span class="layer-card">
              <span class="layer-name">观测层</span>
              <strong>数据里发生了什么？</strong>
              <span class="details">系统对数据的客观描述：异常在哪（空间）、怎么出现（时间）、是不是真的（有效性），三轴共 ${axes.length} 个取值，外加 ${countOf("EvidenceType")} 种有名字的证据。这里的描述与实际不符＝计算逻辑的 bug，改代码，不动知识。</span>
            </span>
            <span class="layer-card">
              <span class="layer-name">机理层</span>
              <strong>为什么会这样？</strong>
              <span class="details">锡膏印刷的工艺知识：${countOf("FailureMechanism")} 个失效机理解释观测到的现象，是全系统根因说法的唯一来源。说法不符合产线实际＝知识错了，改上方机理卡的内容。</span>
            </span>
            <span class="layer-card">
              <span class="layer-name">决策层</span>
              <strong>该怎么判、怎么办？</strong>
              <span class="details">工厂自己的策略：多少置信度算高、先查什么、什么情况升级处置。决策管道 ${decisionRules.length} 条＋处置阶梯 ${dispositionRules.length} 级。觉得太松或太紧＝策略问题，调阈值和顺序，知识与代码都不用动。</span>
            </span>
            <span class="layer-card layer-card-pending">
              <span class="layer-name">实体层</span>
              <strong>落在哪个物理对象上？</strong>
              <span class="details">${entityCount} 个工序阶段/设备部位/物料的骨架，目前只作机理卡上的部位标注（全景图左两列）；设备台账化要等 MES 数据接入。</span>
            </span>
          </div>

          <section class="panel" id="layer-observation">
            <h2>观测层 · 三个正交判定轴
              <span class="details">任何一次触发的"范围"都表达为三轴各取一值的组合;证据按机理挂载,见上方机理卡</span>
            </h2>
            <div class="onto-axes">
              ${["SpatialExtent", "TemporalPattern", "DataValidity"].map((type) => `
                <span class="onto-axis-group">
                  <em>${escapeHtml(CONCEPT_TYPE_LABELS[type])}</em>
                  ${axes.filter((item) => item.type === type).map((item) => `
                    <span class="check-chip" title="${escapeHtml(item.description || "")}">${escapeHtml(item.label)}</span>
                  `).join("")}
                </span>
              `).join("")}
            </div>
          </section>

          <section class="panel" id="layer-mechanism">
            <h2>机理层 · 关系全景
              <span class="details">工序阶段/部位 ← 失效机理 → 证据 · 点击节点联动高亮</span>
            </h2>
            <div class="onto-layout">
              <div class="onto-graph-wrap">
                ${state.ontology ? renderOntologyGraph(graph, state.ontologyNode) : `<div class="empty">本体数据加载失败，请确认 /api/ontology 可访问。</div>`}
              </div>
              <aside class="onto-detail" id="ontoDetail">
                ${renderOntologyDetail(graph, state.ontologyNode)}
              </aside>
            </div>
          </section>

          <section class="panel" id="layer-decision">
            <h2>决策层 · 从观测到处置的四步
              <span class="details">每步内 order 越小越先求值；下钻页的「诊断轨迹」记录每次触发在这条流程上的逐条求值结果</span>
            </h2>
            ${renderDecisionFlow(decisionRules, dispositionRules, catalog)}
          </section>
        ` : ""}
      </section>
    </section>
  `;
}

function renderDecisionFlow(decisionRules, dispositionRules, catalog) {
  if (!decisionRules.length) {
    return `<div class="empty">没有匹配的决策规则</div>`;
  }
  const byRole = (role) => decisionRules.filter((rule) => rule.role === role);
  const ruleRow = (rule) => `
    <div class="flow-rule">
      <strong>${escapeHtml(rule.label || rule.rule_id)}</strong>
      <span class="details">${escapeHtml((rule.condition || {}).when || "")}</span>
      ${(rule.output || {}).action ? `<span class="flow-outcome">→ ${escapeHtml(rule.output.action)}</span>` : ""}
    </div>
  `;

  const model = catalog.confidence_model || {};
  // 真实触发的置信算式做活例子——知识库常量与实际输出对得上才算讲清楚。
  let example = "";
  for (const trigger of ((state.drilldown || {}).triggers) || []) {
    const candidate = ((trigger.analysis_contract || {}).root_cause_candidates || [])[0];
    if (candidate && candidate.confidence_formula) {
      example = `实例（${trigger.trigger_id} 首要候选「${candidate.cause}」）：${candidate.confidence_formula} → ${candidate.evidence_level}`;
      break;
    }
  }
  const confCard = `
    <div class="conf-card">
      <strong>把握度怎么算（置信度算式，数值取自知识库常量，改常量页面自动跟随）</strong>
      <p>最终把握 = 初始把握 × 三指标症状对不对得上（吻合 ×${model.signature_match ?? "-"} / 方向相反 ×${model.signature_conflict ?? "-"} / 说不清不动）
        × 出现位置典不典型（典型 ×${model.spatial_typical ?? "-"} / 不典型 ×${model.spatial_atypical ?? "-"}）
        × NG 节拍与擦网周期合不合拍（合拍 ×${model.cleaning_alignment ?? "-"}）
        × 参数基线是否借自其他机种（是 ×${model.cross_model ?? "-"}），封顶 ${model.cap ?? "-"}</p>
      <p>把握分档：≥${model.level_high ?? "-"} 高 / ≥${model.level_medium ?? "-"} 中 / 其余 低</p>
      ${example ? `<p class="details">${escapeHtml(example)}</p>` : ""}
    </div>
  `;

  const priorityRank = { P1: 1, P2: 2, P3: 3 };
  const sortedDispositions = [...dispositionRules].sort((a, b) =>
    (priorityRank[a.priority] || 9) - (priorityRank[b.priority] || 9));

  const steps = [
    {
      title: "这个 NG 是真的吗？",
      note: "先排除 SPI 测量误报——测量框/阈值/对位问题造成的假 NG 不折腾印刷参数（技术名：门槛 gate）",
      body: byRole("gate").map(ruleRow).join(""),
    },
    {
      title: "可能是哪里出的问题？",
      note: "按证据列出嫌疑原因，每条带一个初始把握度；最后一条投影是没有直接证据时按缺陷方向圈的常见嫌疑（技术名：候选提名 nominate）",
      body: byRole("nominate").map(ruleRow).join(""),
    },
    {
      title: "把握有多大？",
      note: "拿观测到的证据逐条核对：对得上加分、对不上减分，只改把握度不加新嫌疑（技术名：置信度调整 adjust）",
      body: byRole("adjust").map(ruleRow).join("") + confCard,
    },
    {
      title: "现场先干什么？",
      note: "把握度排序、同一原因只留最高的、取前 3 给现场；处置按风险等级自上而下首个命中生效",
      body: sortedDispositions.map((rule) => `
        <div class="flow-rule">
          <span class="ladder-order priority-${escapeHtml(rule.priority || "")}">${escapeHtml(rule.priority || "")}</span>
          <strong>${escapeHtml((rule.output || {}).disposition || "")}</strong>
          <span class="details">${escapeHtml((rule.output || {}).reason || "")}</span>
        </div>
      `).join(""),
    },
  ];

  return `
    <div class="flow-steps">
      ${steps.map((step, index) => `
        <div class="flow-step">
          <div class="flow-marker">
            <span class="flow-no">${index + 1}</span>
            ${index < steps.length - 1 ? `<span class="flow-line"></span>` : ""}
          </div>
          <div class="flow-content">
            <div class="flow-head">
              <strong>${escapeHtml(step.title)}</strong>
              <span class="details">${escapeHtml(step.note)}</span>
            </div>
            ${step.body || `<div class="details">（无规则）</div>`}
          </div>
        </div>
      `).join("")}
    </div>
  `;
}

function renderCheckChips(mech) {
  const auto = (mech.auto_checks || []).map((check) => `
    <span class="check-chip check-${escapeHtml(check.availability)}"
          title="${escapeHtml(AVAILABILITY_LABELS[check.availability] || check.availability)}">
      ${escapeHtml(check.label)}${check.availability !== "available" ? ` · ${escapeHtml(AVAILABILITY_LABELS[check.availability] || "")}` : ""}
    </span>
  `).join("");
  const manual = (mech.manual_checks || []).map((check) => `
    <span class="check-chip check-manual">${escapeHtml(check.label)}</span>
  `).join("");
  return `
    <div class="mech-checks">
      ${auto ? `<span class="details">自动核验：</span>${auto}` : ""}
      ${manual ? `<span class="details">现场确认：</span>${manual}` : ""}
    </div>
  `;
}

function renderMechanismCard({ mech, rules: list }) {
  const facts = [
    ["部位", `${mech.element || "-"} · ${mech.stage || "-"}`],
    ["起病", ONSET_LABELS[mech.onset] || mech.onset || "-"],
    ["签名", mech.signature_text || "无固定签名"],
    ["典型范围", (mech.typical_spatial_labels || []).join(" / ") || "-"],
    ["时间形态", (mech.typical_temporal_labels || []).join(" / ") || "-"],
  ];
  return `
    <article class="mech-card" id="mech-${escapeHtml(mech.mechanism_id)}">
      <div class="mech-head">
        <div>
          <strong>${escapeHtml(mech.label)}</strong>
          ${mech.direction ? `<span class="${defectClass(mech.direction)}">${escapeHtml(mech.direction)}</span>` : ""}
        </div>
        ${mech.early_warning ? `<span class="mech-warning">${escapeHtml(mech.early_warning)}</span>` : ""}
      </div>
      <p class="mech-desc">${escapeHtml(mech.description)}</p>
      <div class="mech-facts">
        ${facts.map(([label, value]) => `
          <span class="mech-fact"><em>${escapeHtml(label)}</em>${escapeHtml(value)}</span>
        `).join("")}
      </div>
      <div class="mech-block">
        <span class="mech-block-title">怎么确认</span>
        ${renderCheckChips(mech)}
      </div>
      ${mech.action ? `
        <div class="mech-block">
          <span class="mech-block-title">确认后怎么办</span>
          <p class="mech-action">${escapeHtml(mech.action)}</p>
        </div>` : ""}
      <details class="mech-rules">
        <summary>${list.length ? `绑定规则 ${list.length} 条` : "暂无绑定规则"} · ${escapeHtml(mech.mechanism_id)}</summary>
        ${list.length
          ? list.map(renderRuleEntry).join("")
          : `<div class="details mech-empty">该机理暂无自动判别规则；事件/实时候选由方向×范围投影生成。</div>`}
      </details>
    </article>
  `;
}

function renderRuleEntry(rule) {
  const output = rule.output || {};
  const condition = Object.entries(rule.condition || {})
    .map(([key, value]) => `${key}: ${value}`)
    .join("；");
  const bodyLines = [
    output.cause ? `根因：${output.cause}` : "",
    output.evidence ? `证据：${output.evidence}` : "",
    output.action ? `动作：${output.action}` : "",
    output.applies_when ? `适用：${output.applies_when}` : "",
    output.not_sufficient_when ? `不足：${output.not_sufficient_when}` : "",
    output.first_check ? `首查：${output.first_check}` : "",
    output.evidence_required && output.evidence_required.length
      ? `所需证据：${[].concat(output.evidence_required).join("、")}` : "",
    output.recheck_method ? `复判：${output.recheck_method}` : "",
  ].filter(Boolean);
  return `
    <div class="rule-entry">
      <div class="rule-entry-head">
        <strong>${escapeHtml(rule.rule_id)}</strong>
        <span class="badge">${escapeHtml(ruleTypeLabel(rule.rule_type))}</span>
        <span class="details">${escapeHtml(condition || "-")}</span>
        ${output.confidence_base != null
          ? `<span class="rule-conf">先验 ${Math.round(Number(output.confidence_base) * 100)}%</span>` : ""}
      </div>
      <div class="details">${bodyLines.map(escapeHtml).join("<br>")}</div>
    </div>
  `;
}

function ruleTypeLabel(type) {
  const labels = {
    scope_root_cause: "范围根因",
    trend_root_cause: "趋势根因",
    evidence_root_cause: "证据根因",
    exclusion_check: "排除项",
    process_review: "工艺复核",
    event_cause: "事件根因",
    abnormal_cause: "实时异常根因",
    decision: "诊断决策",
    disposition: "处置策略",
  };
  return labels[type] || type || "-";
}

function renderRankPanel(title, rows, labelFn, valueFn) {
  return `
    <section class="panel">
      <h2>${escapeHtml(title)}</h2>
      <ol class="rank-list">
        ${rows.map((item) => `
          <li>
            <span>${escapeHtml(labelFn(item))}</span>
            <strong>${escapeHtml(valueFn(item))}</strong>
          </li>
        `).join("")}
      </ol>
    </section>
  `;
}

function filterItems(items) {
  const defect = defectFilter.value;
  const risk = riskFilter.value;
  const keyword = searchInput.value.trim().toLowerCase();

  return items.filter((item) => {
    const defectMatch = !defect || String(item.defect_type || "").endsWith(defect);
    const riskMatch = !risk || item.risk_level === risk;
    const keywordSource = [
      item.work_order,
      item.product_name,
      item.board_sn,
      item.machine,
      item.component,
      item.pad,
      item.case_id,
      item.abnormal_pattern,
    ].join(" ").toLowerCase();
    return defectMatch && riskMatch && (!keyword || keywordSource.includes(keyword));
  });
}

function defectClass(defectType) {
  if (String(defectType).includes("少锡")) {
    return "defect-少锡";
  }
  if (String(defectType).includes("多锡")) {
    return "defect-多锡";
  }
  return "";
}

function formatNumber(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) {
    return "-";
  }
  return Number(value).toFixed(2).replace(/\.00$/, "");
}

function formatRate(value) {
  if (value === null || value === undefined) {
    return "-";
  }
  return `${(Number(value) * 100).toFixed(1)}%`;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

state.activeView = initialView();
document.querySelectorAll(".tab").forEach((item) => {
  item.classList.toggle("active", item.dataset.view === state.activeView);
});

loadData().then(startLivePolling);
