const DATA_PATHS = {
  abnormals: "../output/abnormal_results.json",
  cases: "../output/quality_cases.json",
  summary: "../output/dashboard_summary.json",
  top: "../output/dashboard_top.json",
  analysis: "../output/param_analysis.json",
};

const state = {
  activeView: "abnormal",
  abnormals: [],
  cases: [],
  summary: {},
  top: {},
  analysis: null,
};

const viewRoot = document.getElementById("viewRoot");
const dataStatus = document.getElementById("dataStatus");
const defectFilter = document.getElementById("defectFilter");
const riskFilter = document.getElementById("riskFilter");
const searchInput = document.getElementById("searchInput");

document.getElementById("refreshButton").addEventListener("click", loadData);

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

async function loadData() {
  dataStatus.textContent = "加载数据中...";
  try {
    const [abnormals, cases, summary, top, analysis] = await Promise.all([
      fetchJson(DATA_PATHS.abnormals),
      fetchJson(DATA_PATHS.cases),
      fetchJson(DATA_PATHS.summary),
      fetchJson(DATA_PATHS.top),
      fetchJson(DATA_PATHS.analysis).catch(() => null),
    ]);

    state.abnormals = abnormals;
    state.cases = cases;
    state.summary = summary;
    state.top = top;
    state.analysis = analysis;
    dataStatus.textContent = `已加载 ${abnormals.length} 条异常，${cases.length} 个质量案例`;
    render();
  } catch (error) {
    dataStatus.textContent = "数据加载失败，请先运行 python3 run_over_volume_demo.py";
    viewRoot.innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`;
  }
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
  return `
    <tr>
      <td>${escapeHtml(item.inspect_time)}</td>
      <td>${escapeHtml(item.board_sn)}</td>
      <td>${escapeHtml(item.component)} / Pad ${escapeHtml(item.pad)}</td>
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
    viewRoot.innerHTML = `<div class="empty">暂无事件分析数据，请先运行 python3 run_param_analysis_demo.py</div>`;
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

loadData();
