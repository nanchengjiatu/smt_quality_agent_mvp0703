const DATA_PATHS = {
  abnormals: "../output/abnormal_results.json",
  cases: "../output/quality_cases.json",
  summary: "../output/dashboard_summary.json",
  top: "../output/dashboard_top.json",
  analysis: "../output/param_analysis.json",
  drilldown: "../output/drilldown.json",
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
  // re-ran the pipeline (new over_volume data) so we reload automatically.
  liveVersion: 0,
  // Summary from the previous load, for showing metric deltas (▲/▼).
  prevSummary: {},
  // Keys of abnormals seen on the previous load, to detect newly-added rows.
  prevAbnormalKeys: new Set(),
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
  return ["abnormal", "cases", "dashboard", "events", "rules"].includes(view) ? view : "abnormal";
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
    const [abnormals, cases, summary, top, analysis, drilldown, rules, ontology] = await Promise.all([
      fetchJson(DATA_PATHS.abnormals),
      fetchJson(DATA_PATHS.cases),
      fetchJson(DATA_PATHS.summary),
      fetchJson(DATA_PATHS.top),
      fetchJson(DATA_PATHS.analysis).catch(() => null),
      fetchJson(DATA_PATHS.drilldown).catch(() => null),
      fetchJson(API_RULES).catch(() => null),
      fetchJson(API_ONTOLOGY).catch(() => null),
    ]);

    // Work out deltas and newly-added abnormals before overwriting state.
    const prevKeys = state.prevAbnormalKeys || new Set();
    const newlyAdded = (meta && meta.auto)
      ? abnormals.filter((item) => !prevKeys.has(abnormalKey(item)))
      : [];
    state.prevSummary = state.summary || {};
    state.newAbnormalKeys = new Set(newlyAdded.map(abnormalKey));
    state.prevAbnormalKeys = new Set(abnormals.map(abnormalKey));

    state.abnormals = abnormals;
    state.cases = cases;
    state.summary = summary;
    state.top = top;
    state.analysis = analysis;
    state.drilldown = drilldown;
    state.rules = rules;
    state.ontology = ontology;
    dataStatus.textContent = composeStatus(meta, abnormals, cases);
    render();

    if (meta && meta.auto) {
      showToast(newlyAdded.length ? `检测到 ${newlyAdded.length} 条新异常` : "数据已更新");
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

// Poll the server's data version; reload automatically when over_volume data
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
      tables: { full_spi: "full_excel0623", ng_events: "over_volume" },
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
          SPI 明细表
          <input name="full_spi" required value="${escapeHtml(((config.tables || {}).full_spi) || "full_excel0623")}">
        </label>
        <label>
          NG/异常表
          <input name="ng_events" value="${escapeHtml(((config.tables || {}).ng_events) || "over_volume")}">
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
      ng_events: String(data.get("ng_events") || "").trim(),
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
          <strong>${escapeHtml(trigger.trigger_id)} · ${escapeHtml(trigger.pad_name)}</strong>
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
  RootCauseCandidate: "根因候选",
  Disposition: "处置方式",
  AbnormalScope: "范围(v2,已废弃)",
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
  const axes = ((state.ontology || {}).concepts || []).filter((item) =>
    ["SpatialExtent", "TemporalPattern", "DataValidity"].includes(item.type));

  viewRoot.innerHTML = `
    <section class="rules-view">
      <div class="rules-head">
        <div>
          <h2>本体/知识库</h2>
          <p class="details">
            ${escapeHtml(((state.ontology || {}).version) || "")} · ${escapeHtml(catalog.version || "")} ·
            浏览动线：<strong>本体分层图</strong>(实体←机理→证据) →
            <strong>诊断决策梯</strong> → <strong>机理与规则明细</strong> → <strong>处置策略</strong>
          </p>
        </div>
        <div class="rules-count">${shownCount} / ${rules.length}</div>
      </div>

      <section class="panel">
        <h2>① 本体分层图
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
        <div class="onto-axes">
          <span class="details">三个正交判定轴：</span>
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

      <section class="panel">
        <h2>② 诊断决策管道 <span class="details">观测输入自左向右流经各段;每段内 order 越小越先求值</span></h2>
        ${renderDecisionPipeline(decisionRules)}
      </section>

      <section class="panel">
        <h2>③ 机理目录与规则 <span class="details">${(catalog.mechanisms || []).length} 个失效机理 · 规则按其绑定的机理分组</span></h2>
        <div class="mech-list">
          ${mechanismCards.map(renderMechanismCard).join("")}
          ${unboundRules.length ? `
            <article class="mech-card">
              <div class="mech-head">
                <div>
                  <strong>形态归因(不绑定机理)</strong>
                  <span class="details">趋势形态只是证据,不足以锁定物理机理</span>
                </div>
              </div>
              ${unboundRules.map(renderRuleEntry).join("")}
            </article>` : ""}
        </div>
      </section>

      <section class="panel">
        <h2>④ 处置策略 <span class="details">优先级阶梯,自上而下首个命中生效</span></h2>
        <div class="ladder">
          ${dispositionRules.map((rule) => `
            <div class="ladder-step">
              <span class="ladder-order priority-${escapeHtml(rule.priority || "")}">${escapeHtml(rule.priority || "")}</span>
              <div>
                <strong>${escapeHtml((rule.output || {}).disposition || "")}</strong>
                <div class="details">${escapeHtml((rule.output || {}).reason || "")}</div>
              </div>
            </div>
          `).join("") || `<div class="empty">没有匹配的处置策略</div>`}
        </div>
      </section>
    </section>
  `;
}

const PIPELINE_ROLE_STAGES = [
  { role: "gate", title: "门槛", note: "改变整体走向" },
  { role: "nominate", title: "证据/先验提名", note: "产出根因候选" },
  { role: "adjust", title: "调整", note: "只修正置信度" },
];

function renderDecisionPipeline(decisionRules) {
  if (!decisionRules.length) {
    return `<div class="empty">没有匹配的决策规则</div>`;
  }
  const stages = PIPELINE_ROLE_STAGES.map((stage) => ({
    ...stage,
    rules: decisionRules.filter((rule) => rule.role === stage.role),
  }));
  const tailStages = [
    { title: "排序 · 去重 · 取前3", note: "按最终置信度排序,同根因保留最高者", rules: null },
    { title: "处置分级", note: "见 ④ 处置策略阶梯", rules: null },
  ];
  const renderStage = (stage) => `
    <div class="pipe-stage ${stage.rules ? "" : "pipe-stage-fixed"}">
      <div class="pipe-stage-head">
        <strong>${escapeHtml(stage.title)}</strong>
        <span class="details">${escapeHtml(stage.note)}</span>
      </div>
      ${stage.rules ? stage.rules.map((rule) => `
        <div class="pipe-rule" title="${escapeHtml((rule.output || {}).action || "")}">
          <span class="ladder-order">${escapeHtml(String(rule.priority || ""))}</span>
          <div>
            <strong>${escapeHtml(rule.label || rule.rule_id)}</strong>
            <div class="details">${escapeHtml((rule.condition || {}).when || "")}</div>
          </div>
        </div>
      `).join("") || `<div class="details">（无匹配规则）</div>` : ""}
    </div>
  `;
  return `
    <div class="pipeline">
      <div class="pipe-input">观测<br>输入</div>
      <span class="pipe-arrow">→</span>
      ${[...stages.map(renderStage), ...tailStages.map(renderStage)]
        .join(`<span class="pipe-arrow">→</span>`)}
    </div>
    <p class="details">下钻页每个触发的「诊断轨迹」折叠区记录本管道对该次触发的逐条求值结果(命中/未命中、置信算式、落选原因)。</p>
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
  return `
    <article class="mech-card" id="mech-${escapeHtml(mech.mechanism_id)}">
      <div class="mech-head">
        <div>
          <strong>${escapeHtml(mech.label)}</strong>
          ${mech.direction ? `<span class="${defectClass(mech.direction)}">${escapeHtml(mech.direction)}</span>` : ""}
          <span class="details">
            ${escapeHtml(mech.element)} · ${escapeHtml(mech.stage)}
            · 起病：${escapeHtml(ONSET_LABELS[mech.onset] || mech.onset || "-")}
            ${mech.signature_text ? ` · 签名：${escapeHtml(mech.signature_text)}` : ""}
          </span>
        </div>
        ${mech.early_warning ? `<span class="mech-warning">${escapeHtml(mech.early_warning)}</span>` : ""}
      </div>
      <p class="details">${escapeHtml(mech.description)}</p>
      ${mech.action ? `<p class="details">规范动作：${escapeHtml(mech.action)}</p>` : ""}
      ${renderCheckChips(mech)}
      ${list.length
        ? list.map(renderRuleEntry).join("")
        : `<div class="details mech-empty">暂无绑定规则（该机理的自动判别在第二阶段接入）</div>`}
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
