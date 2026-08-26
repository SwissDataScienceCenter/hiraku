"use strict";

let snapshot = null;

const byId = (id) => document.getElementById(id);
const text = (value) => value === null || value === undefined || value === "" ? "—" : String(value);
const short = (value, length) => value ? String(value).slice(0, length) : "—";
const compactId = (value) => value && String(value).length > 14 ? `${String(value).slice(0, 8)}…${String(value).slice(-5)}` : text(value);
const compactTime = (value) => {
  if (!value) return "—";
  const parsed = new Date(value);
  return Number.isNaN(parsed.valueOf()) ? text(value) : parsed.toLocaleString();
};

function node(tag, value, className) {
  const element = document.createElement(tag);
  if (value !== undefined) element.textContent = text(value);
  if (className) element.className = className;
  return element;
}

function selectedValues(select) {
  return [...select.selectedOptions].map((option) => option.value);
}

function restoreSelection(select, values) {
  const selected = new Set(values);
  for (const option of select.options) option.selected = selected.has(option.value);
}

function showError(message) {
  const target = byId("error");
  target.textContent = message;
  target.classList.remove("hidden");
}

function clearError() { byId("error").classList.add("hidden"); }

async function getJson(path) {
  const response = await fetch(path, {cache: "no-store"});
  const value = await response.json();
  if (!response.ok) throw new Error(value.error || `Request failed (${response.status})`);
  return value;
}

function setOptions(select, values, labeler = (value) => value) {
  select.replaceChildren();
  for (const value of values) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = labeler(value);
    select.append(option);
  }
}

function setStatusOptions(select, values) {
  select.replaceChildren();
  for (const value of values) {
    const option = document.createElement("option");
    option.value = value || "__unspecified__";
    option.textContent = value || "Unspecified";
    select.append(option);
  }
}

function renderSnapshot(value) {
  snapshot = value;
  const task = value.task;
  document.title = `${task.id} · Hiraku`;
  byId("task-title").textContent = task.summary || task.id;
  byId("task-goal").textContent = task.goal || "No task goal recorded.";
  byId("backend-badge").textContent = `${value.tracking_backend} tracking`;
  const meta = byId("task-meta");
  meta.replaceChildren();
  const metrics = (task.metrics || []).map((metric) => `${metric.name} (${metric.direction})`).join(", ");
  const pairs = [["Task", task.id], ["Branch", task.branch], ["Metrics", metrics || "Not declared"]];
  if (task.review?.pr_id) pairs.push(["Pull request", task.review.pr_id]);
  if (task.review?.mr_id) pairs.push(["Merge request", task.review.mr_id]);
  for (const [key, val] of pairs) { meta.append(node("dt", key), node("dd", val)); }
  byId("task-document").innerHTML = task.markdown_html;

  setOptions(byId("metric"), value.metrics);
  byId("metric").value = value.default_metric;
  setOptions(byId("experiment-filter"), value.experiments.map((item) => item.id));
  const statuses = [...new Set(value.experiments.map((item) => item.status))].sort();
  setStatusOptions(byId("status-filter"), statuses);

  const warningPanel = byId("warnings-panel");
  const warningList = byId("warnings");
  warningList.replaceChildren(...value.warnings.map((warning) => node("li", warning)));
  warningPanel.classList.toggle("hidden", value.warnings.length === 0);
  renderLineage(value.lineage);
}

function paramsForView() {
  const params = new URLSearchParams();
  const metric = byId("metric").value;
  if (metric) params.set("metric", metric);
  for (const value of selectedValues(byId("experiment-filter"))) params.append("experiment", value);
  for (const value of selectedValues(byId("status-filter"))) params.append("status", value);
  return params;
}

async function updateView() {
  clearError();
  try {
    const value = await getJson(`/api/view?${paramsForView()}`);
    renderCounts(value.counts);
    renderExperiments(value.experiments, value.metric);
    renderRuns(value.runs, value.metric);
    byId("direction").textContent = `${value.direction === "maximize" ? "↑" : "↓"} ${value.direction}`;
    byId("chart-note").textContent = `Frontier recomputed from ${value.counts.numeric_runs} visible numeric run(s).`;
    const empty = value.counts.numeric_runs === 0;
    byId("chart-empty").classList.toggle("hidden", !empty);
    byId("chart").classList.toggle("hidden", empty);
    if (!empty) await vegaEmbed("#chart", value.chart_spec, {actions: false, renderer: "svg"});
  } catch (error) { showError(error.message); }
}

function renderCounts(counts) {
  const labels = [["Experiments", counts.experiments], ["Visible runs", counts.runs], ["Numeric runs", counts.numeric_runs], ["Warnings", counts.warnings]];
  byId("counts").replaceChildren(...labels.map(([label, value]) => {
    const card = node("div", undefined, "count"); card.append(node("strong", value), node("span", label)); return card;
  }));
}

function pill(value) { return node("span", value || "unspecified", `pill ${value || ""}`); }

function detail(values) {
  const details = document.createElement("details");
  details.append(node("summary", "Details"));
  const list = node("dl", undefined, "detail-grid");
  for (const [key, value] of values) {
    list.append(node("dt", key), node("dd", Array.isArray(value) ? value.join(", ") || "—" : value));
  }
  details.append(list); return details;
}

function makeTable(headers, rows) {
  if (!rows.length) return node("div", "Nothing to show.", "empty");
  const table = document.createElement("table");
  const head = document.createElement("thead"); const tr = document.createElement("tr");
  for (const header of headers) tr.append(node("th", header)); head.append(tr); table.append(head);
  const body = document.createElement("tbody");
  for (const cells of rows) { const row = document.createElement("tr"); for (const cell of cells) { const td = document.createElement("td"); td.append(cell instanceof Node ? cell : document.createTextNode(text(cell))); row.append(td); } body.append(row); }
  table.append(body); return table;
}

function renderExperiments(experiments, metric) {
  const rows = experiments.map((item) => {
    const branch = node("span", item.branch || "linear");
    if (item.contained_in_task_branch) branch.prepend(node("i", "", "contained-dot"));
    return [node("strong", item.id), item.summary, pill(item.status), pill(item.decision), branch,
      node("code", item.short_commit), item.run_count, item.best_metric === null ? "—" : text(item.best_metric),
      compactTime(item.updated_at), detail([["Hypothesis", item.hypothesis], ["Conclusion", item.conclusion], ["Exact commit", item.commit], ["Entrypoints", item.entrypoints], ["Parents", item.parent_experiments], ["Created", item.created_at], ["Commit mismatches", item.commit_mismatches]])];
  });
  byId("experiments-table").replaceChildren(makeTable(["Experiment", "Summary", "Status", "Decision", "Branch", "Commit", "Runs", `Best ${metric || "metric"}`, "Updated", "More"], rows));
}

function renderRuns(runs, metric) {
  const rows = runs.map((run) => {
    const mismatch = run.commit_mismatch ? node("span", "Mismatch", "mismatch") : node("span", "OK");
    return [node("code", compactId(run.run_id)), run.experiment_id || "Unassociated", compactTime(run.created_at), pill(run.status), node("code", short(run.commit, 10)), text(run.metrics[metric]), mismatch,
      detail([["Run ID", run.run_id], ["Association", run.association], ["Exact commit", run.commit], ["All metrics", JSON.stringify(run.metrics)], ["Parameters", JSON.stringify(run.params)], ["Tags", JSON.stringify(run.tags)], ["Warnings", run.warnings]])];
  });
  byId("runs-table").replaceChildren(makeTable(["Run", "Experiment", "Created", "Status", "Commit", metric || "Metric", "Provenance", "More"], rows));
}

function renderLineage(lineage) {
  const target = byId("lineage");
  if (!lineage.applicable || !lineage.nodes.length) { target.replaceChildren(node("div", "No branched experiment lineage is available for this task.", "empty")); return; }
  const parents = new Map(lineage.nodes.map((item) => [item.id, []]));
  for (const edge of lineage.edges) if (parents.has(edge.target)) parents.get(edge.target).push(edge.source);
  const depths = new Map();
  function depth(id, visiting = new Set()) {
    if (depths.has(id)) return depths.get(id); if (visiting.has(id)) return 0; visiting.add(id);
    const value = parents.get(id) || []; const result = value.length ? 1 + Math.max(...value.map((parent) => depth(parent, visiting))) : 0;
    depths.set(id, result); visiting.delete(id); return result;
  }
  for (const item of lineage.nodes) depth(item.id);
  const columns = [];
  for (const item of lineage.nodes) { const index = depths.get(item.id); columns[index] ||= []; columns[index].push(item); }
  const grid = node("div", undefined, "lineage-grid");
  for (const [index, items] of columns.entries()) {
    const column = node("div", undefined, "lineage-column");
    for (const item of items || []) {
      const card = node("div", undefined, `lineage-node ${item.contained_in_task_branch ? "contained" : ""} ${index > 0 ? "has-parent" : ""}`);
      card.append(node("strong", item.id), node("small", `${item.status || "unspecified"} · ${item.decision || "undecided"}`), node("small", `Parents: ${(parents.get(item.id) || []).join(", ") || "root"}`), node("small", item.branch || "linear")); column.append(card);
    }
    grid.append(column);
  }
  target.replaceChildren(grid);
}

async function refresh() {
  const button = byId("refresh"); button.disabled = true; button.textContent = "Refreshing…";
  const metric = byId("metric").value;
  const experiments = selectedValues(byId("experiment-filter"));
  const statuses = selectedValues(byId("status-filter"));
  try {
    const value = await getJson("/api/refresh"); renderSnapshot(value);
    if (value.metrics.includes(metric)) byId("metric").value = metric;
    restoreSelection(byId("experiment-filter"), experiments);
    restoreSelection(byId("status-filter"), statuses);
    await updateView();
  }
  catch (error) { showError(`Refresh failed: ${error.message}. The previous snapshot remains visible.`); }
  finally { button.disabled = false; button.textContent = "Refresh data"; }
}

async function start() {
  try {
    const value = await getJson("/api/snapshot"); renderSnapshot(value);
    for (const id of ["metric", "experiment-filter", "status-filter"]) byId(id).addEventListener("change", updateView);
    byId("refresh").addEventListener("click", refresh); await updateView();
  } catch (error) { showError(error.message); }
  finally { byId("main").setAttribute("aria-busy", "false"); }
}

start();
