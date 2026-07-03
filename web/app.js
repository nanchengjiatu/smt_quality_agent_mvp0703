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
    const [abnormals, cases, summary, top, analysis, drilldown, rules] = await Promise.all([
      fetchJson(DATA_PATHS.abnormals),
      fetchJson(DATA_PATHS.cases),
      fetchJson(DATA_PATHS.summary),
      fetchJson(DATA_PATHS.top),
      fetchJson(DATA_PATHS.analysis).catch(() => null),
      fetchJson(DATA_PATHS.drilldown).catch(() => null),
      fetchJson(API_RULES).catch(() => null),
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
  const types = Array.from(new Set(rules.map((item) => item.rule_type))).sort();
  const filtered = rules.filter((rule) => {
    if (state.ruleType && rule.rule_type !== state.ruleType) {
      return false;
    }
    if (!query) {
      return true;
    }
    return JSON.stringify(rule).toLowerCase().includes(query);
  });

  viewRoot.innerHTML = `
    <section class="rules-view">
      <div class="rules-head">
        <div>
          <h2>规则/知识库</h2>
          <p class="details">${escapeHtml(catalog.version || "")} · ${escapeHtml(catalog.focus || "")}</p>
        </div>
        <div class="rules-count">${filtered.length} / ${rules.length}</div>
      </div>
      <div class="rule-type-filter">
        <button class="rule-type-chip ${state.ruleType ? "" : "active"}" data-rule-type="">全部</button>
        ${types.map((type) => `
          <button class="rule-type-chip ${state.ruleType === type ? "active" : ""}" data-rule-type="${escapeHtml(type)}">
            ${escapeHtml(ruleTypeLabel(type))}
          </button>
        `).join("")}
      </div>
      <div class="rules-table-wrap">
        <table class="rules-table">
          <thead>
            <tr>
              <th>规则</th>
              <th>类型</th>
              <th>条件</th>
              <th>判断与动作</th>
              <th>证据与复判</th>
              <th>来源</th>
            </tr>
          </thead>
          <tbody>
            ${filtered.map(renderRuleRow).join("") || `<tr><td colspan="5" class="empty">没有匹配规则</td></tr>`}
          </tbody>
        </table>
      </div>
    </section>
  `;
}

function renderRuleRow(rule) {
  const output = rule.output || {};
  const condition = Object.entries(rule.condition || {})
    .map(([key, value]) => `${key}: ${value}`)
    .join("；");
  const outputLines = [
    output.cause ? `根因：${output.cause}` : "",
    output.disposition ? `处置：${output.disposition}` : "",
    output.evidence ? `证据：${output.evidence}` : "",
    output.action ? `动作：${output.action}` : "",
    output.reason ? `原因：${output.reason}` : "",
    output.applies_when ? `适用：${output.applies_when}` : "",
    output.not_sufficient_when ? `不足：${output.not_sufficient_when}` : "",
    output.first_check ? `首查：${output.first_check}` : "",
  ].filter(Boolean);
  const evidenceLines = [
    output.evidence_required ? `所需证据：${[].concat(output.evidence_required).join("、")}` : "",
    output.recheck_method ? `复判：${output.recheck_method}` : "",
    output.confidence_base != null ? `基础置信度：${Math.round(Number(output.confidence_base) * 100)}%` : "",
  ].filter(Boolean);
  return `
    <tr>
      <td>
        <strong>${escapeHtml(rule.rule_id)}</strong>
        ${rule.priority ? `<div><span class="badge">${escapeHtml(rule.priority)}</span></div>` : ""}
      </td>
      <td>${escapeHtml(ruleTypeLabel(rule.rule_type))}</td>
      <td class="details">${escapeHtml(condition || "-")}</td>
      <td class="details">${outputLines.map(escapeHtml).join("<br>") || "-"}</td>
      <td class="details">${evidenceLines.map(escapeHtml).join("<br>") || "-"}</td>
      <td class="details">${escapeHtml(rule.source || "-")}</td>
    </tr>
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
