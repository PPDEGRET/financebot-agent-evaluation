"use strict";

const NS = "http://www.w3.org/2000/svg";
const pct = new Intl.NumberFormat("en-US", { style: "percent", minimumFractionDigits: 2, maximumFractionDigits: 2, signDisplay: "always" });
const ratioPct = new Intl.NumberFormat("en-US", { style: "percent", minimumFractionDigits: 2, maximumFractionDigits: 2 });
const num = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 });
const money = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: 2 });

const statusCopy = {
  below_qqq_with_larger_drawdown: "Below QQQ; larger drawdown.",
  highest_return_in_this_simulation: "Highest return in this simulator; all study caveats apply.",
  negative_simulated_return: "Negative simulated return.",
  more_calls_lower_return_than_open_close: "3.5× open + close calls; materially lower simulated return."
};

function svgNode(name, attrs = {}, text = "") {
  const node = document.createElementNS(NS, name);
  Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, String(value)));
  if (text) node.textContent = text;
  return node;
}

function renderChart(rows, benchmark) {
  const svg = document.querySelector("#calls-chart");
  const title = svg.querySelector("title");
  const desc = svg.querySelector("desc");
  svg.replaceChildren(title, desc);

  const width = 900;
  const height = 470;
  const margin = { top: 36, right: 60, bottom: 44, left: 74 };
  const plotW = width - margin.left - margin.right;
  const plotH = height - margin.top - margin.bottom;
  const xMax = 700;
  const yMin = -0.1;
  const yMax = 0.3;
  const x = value => margin.left + (value / xMax) * plotW;
  const y = value => margin.top + ((yMax - value) / (yMax - yMin)) * plotH;

  [-0.1, 0, 0.1, 0.2, 0.3].forEach(value => {
    svg.append(svgNode("line", { x1: margin.left, y1: y(value), x2: width - margin.right, y2: y(value), class: "grid" }));
    svg.append(svgNode("text", { x: margin.left - 14, y: y(value) + 4, "text-anchor": "end", class: "axis-label" }, pct.format(value)));
  });
  [0, 100, 200, 300, 400, 500, 600, 700].forEach(value => {
    svg.append(svgNode("line", { x1: x(value), y1: margin.top, x2: x(value), y2: height - margin.bottom, class: "grid" }));
    svg.append(svgNode("text", { x: x(value), y: height - margin.bottom + 24, "text-anchor": "middle", class: "axis-label" }, String(value)));
  });

  svg.append(svgNode("line", { x1: margin.left, y1: y(benchmark), x2: width - margin.right, y2: y(benchmark), class: "benchmark" }));
  svg.append(svgNode("text", { x: width - margin.right, y: y(benchmark) - 9, "text-anchor": "end", class: "benchmark-label" }, `QQQ ${pct.format(benchmark)}`));

  const labelOffsets = {
    daily_open: { dx: -10, dy: 26, anchor: "end" },
    open_close: { dx: 12, dy: -16, anchor: "start" },
    three_times_day: { dx: 12, dy: -16, anchor: "start" },
    every_hour: { dx: -12, dy: -16, anchor: "end" }
  };

  rows.forEach(row => {
    const pointX = x(row.model_calls);
    const pointY = y(row.simulated_return);
    const offset = labelOffsets[row.id];
    const group = svgNode("g", {
      class: "point-group",
      tabindex: "0",
      role: "group",
      "aria-label": `${row.label}: ${row.model_calls} calls, ${pct.format(row.simulated_return)} simulated return`
    });
    const circleClass = ["point", row.id === "open_close" ? "highlight" : "", row.simulated_return < 0 ? "negative-point" : ""].filter(Boolean).join(" ");
    group.append(svgNode("circle", { cx: pointX, cy: pointY, r: row.id === "open_close" ? 12 : 9, class: circleClass }));
    group.append(svgNode("text", { x: pointX + offset.dx, y: pointY + offset.dy, "text-anchor": offset.anchor, class: "point-label" }, row.label));
    group.append(svgNode("text", { x: pointX + offset.dx, y: pointY + offset.dy + 15, "text-anchor": offset.anchor, class: "point-detail" }, `${row.model_calls} calls · ${pct.format(row.simulated_return)}`));
    svg.append(group);
  });
}

function renderResults(windowData) {
  const tbody = document.querySelector("#results-body");
  tbody.replaceChildren();
  windowData.variants.forEach(row => {
    const tr = document.createElement("tr");
    if (row.id === "open_close") tr.className = "highlight-row";
    const cells = [
      `<span class="variant-cell"><i class="variant-dot" aria-hidden="true"></i>${row.label}</span>`,
      row.review_schedule,
      pct.format(row.simulated_return),
      pct.format(row.benchmark),
      pct.format(row.max_drawdown),
      num.format(row.reported_sharpe),
      String(row.fills),
      String(row.model_calls),
      statusCopy[row.status] || row.status
    ];
    cells.forEach((value, index) => {
      const td = document.createElement("td");
      if (index === 0) td.innerHTML = value;
      else td.textContent = value;
      if ([2, 3, 4, 5, 6, 7].includes(index)) td.className = "numeric";
      if (index === 8) td.className = "status-copy";
      tr.append(td);
    });
    tbody.append(tr);
  });
  renderChart(windowData.variants, windowData.benchmarks.QQQ);
}

function renderDemo(report) {
  const tbody = document.querySelector("#demo-body");
  tbody.replaceChildren();
  report.variants.forEach(row => {
    const tr = document.createElement("tr");
    [row.label, row.policy_calls, row.fills, pct.format(row.simulated_return), pct.format(row.max_drawdown)].forEach((value, index) => {
      const td = document.createElement("td");
      td.textContent = String(value);
      if (index > 0) td.className = "numeric";
      tr.append(td);
    });
    tbody.append(tr);
  });
  document.querySelector("#demo-window").textContent = `${report.window.start} → ${report.window.end}`;
}

function formatTraceTime(value) {
  return `${value.slice(0, 10)} · ${value.slice(11, 16)} ET`;
}

function renderTrace(trace) {
  document.querySelector("#trace-hash").textContent = `trace sha256 · ${trace.trace_sha256}`;
  document.querySelector("#trace-cutoff").textContent = formatTraceTime(trace.data_cutoff);
  document.querySelector("#trace-decision-time").textContent = formatTraceTime(trace.decision_time);
  document.querySelector("#trace-execution-time").textContent = formatTraceTime(trace.execution_time);
  document.querySelector("#trace-caveat").textContent = trace.caveat;

  const stages = document.querySelector("#trace-stages");
  stages.replaceChildren();
  trace.stages.forEach(stage => {
    const item = document.createElement("li");
    const index = document.createElement("span");
    const name = document.createElement("strong");
    const status = document.createElement("small");
    index.textContent = String(stage.index).padStart(2, "0");
    name.textContent = stage.name;
    status.textContent = stage.status;
    item.append(index, name, status);
    stages.append(item);
  });

  const candidateList = document.querySelector("#trace-candidates");
  candidateList.replaceChildren();
  trace.candidates.forEach(candidate => {
    const item = document.createElement("div");
    item.className = "trace-item";
    const head = document.createElement("div");
    head.className = "trace-item-head";
    const ticker = document.createElement("strong");
    ticker.textContent = candidate.ticker;
    const chip = document.createElement("span");
    chip.className = "trace-chip";
    chip.textContent = `${candidate.side} candidate`;
    head.append(ticker, chip);
    const details = document.createElement("dl");
    [["Target weight", ratioPct.format(candidate.target_weight)], ["Confidence", ratioPct.format(candidate.confidence)], ["Synthetic edge", `${num.format(candidate.expected_net_edge_bps)} bps`]].forEach(([label, value]) => {
      const dt = document.createElement("dt");
      const dd = document.createElement("dd");
      dt.textContent = label;
      dd.textContent = value;
      details.append(dt, dd);
    });
    const thesis = document.createElement("p");
    thesis.textContent = candidate.thesis;
    item.append(head, details, thesis);
    candidateList.append(item);
  });

  const orderList = document.querySelector("#trace-orders");
  orderList.replaceChildren();
  trace.validations.forEach(validation => {
    const fill = trace.fills.find(item => item.ticker === validation.ticker);
    const item = document.createElement("div");
    item.className = "trace-item";
    const head = document.createElement("div");
    head.className = "trace-item-head";
    const ticker = document.createElement("strong");
    ticker.textContent = validation.ticker;
    const chip = document.createElement("span");
    chip.className = "trace-chip";
    chip.textContent = validation.issues[0].code;
    head.append(ticker, chip);
    const details = document.createElement("dl");
    [["Visible price", money.format(validation.visible_price)], ["Approved quantity", String(validation.quantity)], ["Later fill", money.format(fill.execution_price)], ["Commission", money.format(fill.commission)]].forEach(([label, value]) => {
      const dt = document.createElement("dt");
      const dd = document.createElement("dd");
      dt.textContent = label;
      dd.textContent = value;
      details.append(dt, dd);
    });
    const message = document.createElement("p");
    message.textContent = validation.issues.map(issue => `${issue.code}: ${issue.message}`).join(" ");
    item.append(head, details, message);
    orderList.append(item);
  });

  document.querySelector("#trace-equity-before").textContent = money.format(trace.ledger_before.equity);
  document.querySelector("#trace-equity-after").textContent = money.format(trace.ledger_after.equity);
  document.querySelector("#trace-positions-before").textContent = `${trace.ledger_before.positions.length} positions · ${trace.ledger_before.fill_count} fills`;
  document.querySelector("#trace-positions-after").textContent = `${trace.ledger_after.positions.length} positions · ${trace.ledger_after.fill_count} fills`;
  document.querySelector("#trace-ledger-delta").textContent = `${trace.ledger_delta.fills} fills booked · cash ${money.format(trace.ledger_delta.cash)} · immediate marked equity ${money.format(trace.ledger_delta.equity)}. Costs and next-bar movement explain the delta.`;
}

function renderFailureLab(report) {
  document.querySelector("#failure-count").textContent = `${report.blocked_cases} blocked · ${report.approved_controls} control approved`;
  const list = document.querySelector("#failure-list");
  list.replaceChildren();
  report.cases.forEach((entry, index) => {
    const details = document.createElement("details");
    if (index < 2) details.open = true;
    const summary = document.createElement("summary");
    const label = document.createElement("strong");
    label.textContent = entry.label;
    const code = document.createElement("span");
    code.className = "failure-code";
    code.textContent = entry.issue_codes[0];
    if (entry.approved) {
      code.textContent = "CONTROL · APPROVED";
      code.style.color = "var(--mint-dark)";
      code.style.background = "#d9f2e9";
    }
    summary.append(label, code);
    const body = document.createElement("div");
    body.className = "failure-body";
    const message = document.createElement("p");
    message.textContent = entry.issues.map(issue => issue.message).join(" ");
    const input = document.createElement("code");
    input.textContent = `${entry.intent.side.toUpperCase()} ${entry.intent.quantity} ${entry.intent.ticker} · equity ${money.format(entry.portfolio_before.equity)} · cash ${money.format(entry.portfolio_before.cash)}`;
    body.append(message, input);
    details.append(summary, body);
    list.append(details);
  });
}

function renderRecovery(report) {
  document.querySelector("#recovery-status").textContent = report.exact_state_match && report.journal_verified ? "Verified exact match" : "Verification failed";
  document.querySelector("#recovery-events").textContent = String(report.durable_events);
  document.querySelector("#recovery-duplicates").textContent = String(report.duplicate_deliveries_suppressed);
  document.querySelector("#recovery-equity").textContent = money.format(report.recovered_state.equity);
  document.querySelector("#recovery-digest").textContent = report.recovered_state_sha256;
  document.querySelector("#recovery-scope").textContent = report.scope_boundary;
  const timeline = document.querySelector("#recovery-timeline");
  timeline.replaceChildren();
  report.timeline.forEach(step => {
    const item = document.createElement("li");
    item.dataset.step = step.step;
    const label = document.createElement("strong");
    const detail = document.createElement("span");
    label.textContent = step.label;
    detail.textContent = step.detail;
    item.append(label, detail);
    timeline.append(item);
  });
}

function showError(message) {
  ["#results-body", "#demo-body"].forEach(selector => {
    const target = document.querySelector(selector);
    target.replaceChildren();
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.className = "data-error";
    cell.colSpan = 9;
    cell.textContent = message;
    row.append(cell);
    target.append(row);
  });
  ["#trace-stages", "#failure-list", "#recovery-timeline"].forEach(selector => {
    const target = document.querySelector(selector);
    target.replaceChildren();
    const error = document.createElement(selector === "#recovery-timeline" || selector === "#trace-stages" ? "li" : "p");
    error.className = "data-error";
    error.textContent = message;
    target.append(error);
  });
}

async function init() {
  try {
    const [evidenceResponse, demoResponse, failureResponse, recoveryResponse, protocolResponse] = await Promise.all([
      fetch("../evidence/tournament-results.json"),
      fetch("../artifacts/sample-replay.json"),
      fetch("../artifacts/risk-failure-lab.json"),
      fetch("../artifacts/recovery-drill.json"),
      fetch("../protocol/frozen-paper-v1.json")
    ]);
    if (![evidenceResponse, demoResponse, failureResponse, recoveryResponse, protocolResponse].every(response => response.ok)) throw new Error("Local evidence files were not served.");
    const [evidence, demo, failureLab, recovery, protocol] = await Promise.all([
      evidenceResponse.json(),
      demoResponse.json(),
      failureResponse.json(),
      recoveryResponse.json(),
      protocolResponse.json()
    ]);
    renderResults(evidence.historical_evidence.comparison_window);
    renderDemo(demo);
    renderTrace(demo.decision_trace);
    renderFailureLab(failureLab);
    renderRecovery(recovery);
    document.querySelector("#protocol-digest").textContent = protocol.configuration_sha256;
  } catch (error) {
    console.error(error);
    showError("Local evidence could not be loaded. Serve the repository root with python -m http.server 8000.");
  }
}

init();
