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
const LIVE_POLL_MS = 4000;

const state = {
  activeView: "abnormal",
  abnormals: [],
  cases: [],
  summary: {},
  top: {},
  analysis: null,
  drilldown: null,
  // Per-stage status from the last /api/refresh, keyed by stage name.
  // Lets each view show an honest "load failed" message instead of a blank.
  stageStatus: {},
  // Last data version seen from /api/live; a higher one means the server
  // re-ran the pipeline (new over_volume data) so we reload automatically.
  liveVersion: 0,
};

let liveTimer = null;

const viewRoot = document.getElementById("viewRoot");
const dataStatus = document.getElementById("dataStatus");
const defectFilter = document.getElementById("defectFilter");
const riskFilter = document.getElementById("riskFilter");
const searchInput = document.getElementById("searchInput");

document.getElementById("refreshButton").addEventListener("click", refreshData);

document.querySelectorAll(".tab").forEach((button) => {
  button.addEventListener("click", () => {
    state.activeView = button.dataset.view;
    document.querySelectorAll(".tab").forEach((item) => item.classList.remove("active"));
    button.classList.add("active");
    render();
  });
});

[defectFilter, riskFilter, searchInput].forEach((control) => {
  control.addEventListener("input", render);
});

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
    const [abnormals, cases, summary, top, analysis, drilldown] = await Promise.all([
      fetchJson(DATA_PATHS.abnormals),
      fetchJson(DATA_PATHS.cases),
      fetchJson(DATA_PATHS.summary),
      fetchJson(DATA_PATHS.top),
      fetchJson(DATA_PATHS.analysis).catch(() => null),
      fetchJson(DATA_PATHS.drilldown).catch(() => null),
    ]);

    state.abnormals = abnormals;
    state.cases = cases;
    state.summary = summary;
    state.top = top;
    state.analysis = analysis;
    state.drilldown = drilldown;
    dataStatus.textContent = composeStatus(meta, abnormals, cases);
    render();
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
  } else {
    renderDashboardView();
  }
}

function renderMetrics() {
  const summary = state.summary || {};
  const metrics = [
    ["异常总数", summary.abnormal_count ?? 0],
    ["少锡", summary.less_solder_count ?? 0],
    ["多锡", summary.more_solder_count ?? 0],
    ["未关闭案例", summary.open_case_count ?? 0],
    ["高风险", summary.high_risk_count ?? 0],
    ["中风险", summary.medium_risk_count ?? 0],
    ["低风险", summary.low_risk_count ?? 0],
    ["复测有效率", formatRate(summary.recheck_effective_rate)],
  ];

  document.getElementById("metrics").innerHTML = metrics.map(([label, value]) => `
    <article class="metric">
      <span>${label}</span>
      <strong>${value}</strong>
    </article>
  `).join("");
}

function renderAbnormalView() {
  const rows = filterItems(state.abnormals);
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
          ${rows.map(renderAbnormalRow).join("")}
        </tbody>
      </table>
    </div>
  `;
}

function renderAbnormalRow(item) {
  const trigger = findDrilldownTrigger((state.drilldown || {}).triggers, item.component, item.pad);
  const badge = trigger
    ? `<button class="dd-entry-badge" data-dd="${escapeHtml(trigger.trigger_id)}" title="进入下钻分析">🔴 三板连发</button>`
    : "";
  return `
    <tr>
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
    ["焊点不良率", overview.defect_rate_percent != null ? `${overview.defect_rate_percent}%` : "-"],
    ["板级直通率", overview.board_pass_rate_percent != null ? `${overview.board_pass_rate_percent}%` : "-"],
    ["复测有效率", formatRate(overview.recheck_effective_rate)],
    ["数据时间", (overview.time_range || []).filter(Boolean).join(" ~ ") || "-"],
  ];

  const events = analysis.events || [];
  viewRoot.innerHTML = `
    <div class="event-view">
      <section class="panel">
        <h2>全量数据概览 <span class="details">${escapeHtml(analysis.source_table || "")}</span></h2>
        <div class="overview-grid">
          ${overviewItems.map(([label, value]) => `
            <div class="overview-item"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>
          `).join("")}
        </div>
      </section>
      ${renderDrilldownPanel()}
      ${events.length
        ? events.map(renderEventCard).join("")
        : `<div class="empty">数据时间范围内未检出聚集事件</div>`}
      ${(analysis.caveats || []).length
        ? `<p class="details caveats">说明：${analysis.caveats.map(escapeHtml).join(" ")}</p>`
        : ""}
    </div>
  `;
}

function renderEventCard(event) {
  const boards = (event.boards || []).map((board) => `
    <tr>
      <td>${escapeHtml(board.time)}</td>
      <td>${escapeHtml(board.board_sn)}</td>
      <td>${escapeHtml(board.ng_count)}</td>
      <td>${board.ng_share != null ? `${(board.ng_share * 100).toFixed(0)}%` : "-"}</td>
      <td>${formatNumber(board.metric_avg)}%</td>
    </tr>
  `).join("");

  const causes = (event.suggested_causes || []).map((cause, index) => `
    <li><strong>${escapeHtml(cause)}</strong><span class="details"> — ${escapeHtml((event.suggested_actions || [])[index] || "")}</span></li>
  `).join("");

  return `
    <section class="panel event-card">
      <h2>
        ${escapeHtml(event.event_id)}
        <span class="${defectClass(event.main_defect_cn)}">${escapeHtml(event.main_defect_cn)}</span>
        <span class="badge risk-高">${escapeHtml(event.scope)}</span>
      </h2>
      <p class="details">
        ${escapeHtml(event.model)} · ${escapeHtml(event.machine)} ·
        ${escapeHtml(event.start_time)} ~ ${escapeHtml(event.end_time)}（${escapeHtml(event.duration_minutes)} 分钟）·
        ${escapeHtml(event.board_count)} 块板 / ${escapeHtml(event.ng_record_count)} 条 NG ·
        前兆判定：${escapeHtml((event.precursor || {}).verdict || "-")}
      </p>
      <div class="event-body">
        <div>
          <h3>分析结论</h3>
          <ul class="finding-list">
            ${(event.findings || []).map((finding) => `<li>${escapeHtml(finding)}</li>`).join("")}
          </ul>
          <h3>疑似原因与建议</h3>
          <ul class="finding-list">${causes}</ul>
        </div>
        <div class="table-wrap">
          <table class="event-board-table">
            <thead>
              <tr><th>时间</th><th>PCB</th><th>NG 点数</th><th>NG 占比</th><th>平均${escapeHtml(event.metric_label)}</th></tr>
            </thead>
            <tbody>${boards}</tbody>
          </table>
        </div>
      </div>
    </section>
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
      <h2>三板连发下钻 <span class="details">${escapeHtml((state.drilldown || {}).trigger_rule || "")}</span></h2>
      <div class="dd-trigger-cards">
        ${triggers.map((trigger) => `
          <article class="dd-trigger-card">
            <div>
              <strong>${escapeHtml(trigger.trigger_id)} · 焊盘 ${escapeHtml(trigger.pad_name)}</strong>
              <span class="${defectClass(trigger.main_defect_cn)}">${escapeHtml(trigger.main_defect_cn)}</span>
            </div>
            <p class="details">
              ${escapeHtml(trigger.model)} · ${escapeHtml(trigger.start_time)} ~ ${escapeHtml(trigger.end_time)} ·
              连续 ${escapeHtml(trigger.trigger_board_count)} 块板 ·
              ${escapeHtml(trigger.change_type.verdict)} · ${escapeHtml(trigger.recovery.verdict)}
            </p>
            <button class="dd-enter" data-dd="${escapeHtml(trigger.trigger_id)}">进入下钻分析 →</button>
          </article>
        `).join("")}
      </div>
    </section>
  `;
}

viewRoot.addEventListener("click", (event) => {
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

loadData().then(startLivePolling);
