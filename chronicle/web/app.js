"use strict";

const $ = (selector) => document.querySelector(selector);
const state = { status: null, conflicts: null, question: "", request: 0 };
const viewNames = { ask: "Questions", conflicts: "Conflicts", changes: "Changes" };

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function append(parent, ...nodes) {
  nodes.forEach((node) => parent.append(node));
  return parent;
}

function prettyDate(iso) {
  return new Intl.DateTimeFormat("en", {
    year: "numeric", month: "short", day: "numeric", timeZone: "UTC",
  }).format(new Date(iso + "T00:00:00Z"));
}

async function api(path, params = {}) {
  const url = new URL(path, location.origin);
  for (const [key, value] of Object.entries(params)) {
    if (value) url.searchParams.set(key, value);
  }
  const response = await fetch(url, { cache: "no-store" });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "The request could not be completed.");
  return data;
}

function showError(error) {
  $("#global-error").textContent = error.message;
  $("#global-error").classList.remove("hidden");
}

function clearError() {
  $("#global-error").classList.add("hidden");
}

function tableView(headers, className, caption) {
  const container = el("div", "table-scroll");
  container.tabIndex = 0;
  container.setAttribute("role", "region");
  container.setAttribute("aria-label", caption);
  const table = el("table", className);
  table.append(el("caption", "sr-only", caption));
  const head = el("thead");
  const row = el("tr");
  headers.forEach((name) => {
    const cell = el("th", "", name);
    cell.scope = "col";
    row.append(cell);
  });
  head.append(row);
  const body = el("tbody");
  append(table, head, body);
  container.append(table);
  return { container, body };
}

function sourceExcerpt(quote) {
  const lines = (quote || "").trim().split("\n");
  if (lines.length > 1 && lines.every((line) => line.trim().startsWith("|"))) {
    const rows = lines.map((line) => line.trim().replace(/^\||\|$/g, "").split("|").map((cell) => cell.trim()));
    if (rows.every((row) => row.length === rows[0].length)) {
      const { container, body } = tableView(rows[0], "evidence-table", "Source excerpt");
      rows.slice(1).forEach((cells) => {
        const row = el("tr");
        cells.forEach((value) => {
          const cell = el("td");
          const footnote = value.match(/^(.+%)(\d+)$/);
          if (footnote) append(cell, document.createTextNode(footnote[1]), el("sup", "", footnote[2]));
          else cell.textContent = value;
          row.append(cell);
        });
        body.append(row);
      });
      return container;
    }
  }
  return el("blockquote", "", quote || "No excerpt available.");
}

function evidenceCard(source, index, detail = {}) {
  const card = el("div", "evidence-card");
  const title = el("div", "evidence-title");
  append(title, el("span", "source-num", `[${index + 1}]`), el("span", "", detail.title || "Source"));
  const link = el("a", "source-link", source.url);
  try {
    const url = new URL(source.url);
    if (["http:", "https:"].includes(url.protocol)) {
      link.href = url.href;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.textContent = url.hostname + url.pathname + url.hash;
      link.title = source.url;
    }
  } catch { /* An invalid source URL remains plain text. */ }
  append(card, title, link, sourceExcerpt(source.quote));
  const meta = el("div", "evidence-detail");
  const snapshot = state.status.snapshots.find((item) => item.id === source.snapshot_id);
  meta.append(el("span", "", snapshot ? prettyDate(snapshot.taken_on) : source.snapshot_id || "Date unavailable"));
  if (detail.line) meta.append(el("span", "", "Line " + detail.line));
  if (detail.published_on) meta.append(el("span", "", "Published " + detail.published_on));
  card.append(meta);
  return card;
}

function queryDetails(decision) {
  const details = el("details", "details");
  details.append(el("summary", "", "Query details"));
  const fields = el("dl", "query-fields");
  const intent = decision.intent;
  const values = {
    Product: intent.family,
    Metric: intent.metric?.toUpperCase(),
    "Requested date": decision.requested_date || "Latest",
    ...Object.fromEntries(Object.entries(intent.constraints || {}).map(([key, value]) => [key.replaceAll("_", " "), value])),
  };
  for (const [name, value] of Object.entries(values)) {
    if (value) append(fields, el("dt", "", name), el("dd", "", value));
  }
  details.append(fields);
  decision.reasons.forEach((reason) => details.append(el("p", "reason", reason)));
  return details;
}

function alternativeDetails(alternatives) {
  const details = el("details", "details");
  details.append(el("summary", "", `Other evidence (${alternatives.length})`));
  let rendered = false;
  details.addEventListener("toggle", () => {
    if (!details.open || rendered) return;
    rendered = true;
    alternatives.forEach((item, index) => {
      const row = el("div", "alternate");
      if (item.reason) row.append(el("p", "", item.reason));
      if (item.evidence) row.append(evidenceCard(item.evidence, index, item.evidence));
      details.append(row);
    });
  });
  return details;
}

function renderAnswer(decision, elapsed) {
  const area = $("#answer-area");
  area.replaceChildren();
  const grid = el("div", "answer-grid");
  const answer = el("section", "answer-panel");
  const heading = el("div", "panel-heading");
  append(heading, el("h2", "", "Answer"), el("span", "status " + (decision.answer.confident ? "supported" : "unconfirmed"), decision.answer.confident ? "Supported" : "Unable to confirm"));
  answer.append(heading);
  answer.append(el("div", "answer-text", decision.answer.text));
  const meta = el("div", "answer-meta");
  const snapshot = state.status.snapshots.find((item) => item.id === decision.snapshot_id);
  append(meta, el("span", "", snapshot ? "Snapshot: " + prettyDate(snapshot.taken_on) : "No eligible snapshot"), el("span", "", elapsed + " ms"));
  answer.append(meta);
  if (decision.answer.conflict_note) answer.append(el("p", "answer-note", decision.answer.conflict_note));
  const actions = el("div", "answer-actions");
  const timeline = el("button", "", "View history");
  timeline.addEventListener("click", loadTimeline);
  const download = el("button", "", "Export JSON");
  download.addEventListener("click", () => {
    const url = URL.createObjectURL(new Blob([JSON.stringify(decision, null, 2)], { type: "application/json" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = "chronicle-evidence-trace.json";
    link.click();
    URL.revokeObjectURL(url);
  });
  append(actions, timeline, download);
  append(answer, actions, queryDetails(decision));
  if (decision.alternatives.length) answer.append(alternativeDetails(decision.alternatives));
  const evidence = el("section", "source-panel");
  const sourceHeading = el("div", "panel-heading");
  append(sourceHeading, el("h2", "", "Sources"), el("span", "muted", String(decision.answer.sources.length)));
  evidence.append(sourceHeading);
  if (!decision.answer.sources.length) evidence.append(el("div", "empty", "No supporting source found."));
  decision.answer.sources.forEach((source, index) => {
    const match = decision.selected.find((item) => item.evidence && item.evidence.url === source.url && item.evidence.quote === source.quote);
    evidence.append(evidenceCard(source, index, match?.evidence));
  });
  append(grid, answer, evidence);
  area.append(grid);
  document.querySelectorAll(".snapshot-item").forEach((button) => {
    button.classList.toggle("selected", button.dataset.snapshot === decision.snapshot_id);
    button.setAttribute("aria-pressed", String(button.dataset.snapshot === decision.snapshot_id));
  });
}

async function ask() {
  clearError();
  const request = ++state.request;
  state.question = $("#question").value.trim();
  if (!state.question) return;
  $("#ask-button").disabled = true;
  $("#answer-area").replaceChildren(el("div", "loading", "Loading answer…"));
  $("#timeline-area").classList.add("hidden");
  try {
    const data = await api("/api/answer", { q: state.question, as_of: $("#as-of").value });
    if (request === state.request) renderAnswer(data.result, data.elapsed_ms);
  } catch (error) {
    if (request === state.request) { showError(error); $("#answer-area").replaceChildren(); }
  } finally {
    if (request === state.request) $("#ask-button").disabled = false;
  }
}

async function loadTimeline() {
  clearError();
  const request = state.request;
  const area = $("#timeline-area");
  area.classList.remove("hidden");
  area.replaceChildren(el("div", "loading", "Loading history…"));
  try {
    const { result } = await api("/api/timeline", { q: state.question });
    if (request !== state.request) return;
    area.replaceChildren();
    const heading = el("div", "section-heading");
    const close = el("button", "text-button", "Hide");
    close.addEventListener("click", () => area.classList.add("hidden"));
    append(heading, el("h2", "", "Answer history"), close);
    const { container, body } = tableView(["Snapshot", "Answer", ""], "history-table", "Answer history by snapshot");
    result.forEach((item) => {
      const row = el("tr", "history-row");
      const action = el("td");
      const button = el("button", "record-toggle", "Use date");
      button.setAttribute("aria-label", "Use " + prettyDate(item.snapshot.taken_on));
      button.addEventListener("click", () => { $("#as-of").value = item.snapshot.taken_on; ask(); });
      action.append(button);
      append(row, el("td", "", prettyDate(item.snapshot.taken_on)), el("td", "history-answer", item.decision.answer.text), action);
      body.append(row);
    });
    append(area, heading, container);
  } catch (error) { area.replaceChildren(); showError(error); }
}

function renderConflicts() {
  if (!state.conflicts) return;
  const query = $("#conflict-search").value.toLowerCase();
  const type = $("#conflict-type").value;
  const items = state.conflicts.filter((record) => {
    const historical = record.reason.startsWith("Historical");
    return (record.topic + " " + record.values.join(" ")).toLowerCase().includes(query) &&
      (type === "all" || type === "historical" && historical || type === "active" && !historical || type === "unresolved" && !record.resolution);
  });
  $("#conflict-summary").textContent = items.length + " of " + state.conflicts.length + " records";
  const list = $("#conflict-list");
  list.replaceChildren();
  if (!items.length) { list.append(el("div", "empty", "No matching records.")); return; }
  const { container, body } = tableView(["Topic", "Observed values", "Resolution", "Type", ""], "records-table", "Conflicting values and resolutions");
  items.forEach((record, index) => {
    const row = el("tr", "conflict-row");
    const historical = record.reason.startsWith("Historical");
    const action = el("td");
    const button = el("button", "record-toggle", "Details");
    button.setAttribute("aria-expanded", "false");
    button.setAttribute("aria-controls", "record-detail-" + index);
    action.append(button);
    append(row, el("td", "topic", record.topic), el("td", "values", record.values.join(", ")),
      el("td", "resolution", record.resolution || "Unresolved"),
      el("td", "record-type", historical ? "Historical" : "Active"), action);
    const detailRow = el("tr", "record-detail hidden");
    detailRow.id = "record-detail-" + index;
    const detailCell = el("td");
    detailCell.colSpan = 5;
    detailRow.append(detailCell);
    let rendered = false;
    button.addEventListener("click", () => {
      const open = button.getAttribute("aria-expanded") !== "true";
      button.setAttribute("aria-expanded", String(open));
      button.textContent = open ? "Close" : "Details";
      detailRow.classList.toggle("hidden", !open);
      if (open && !rendered) {
        rendered = true;
        detailCell.append(el("p", "reason", record.reason));
        const sources = el("div", "record-sources");
        record.sources.forEach((source, sourceIndex) => sources.append(evidenceCard(source, sourceIndex)));
        detailCell.append(sources);
      }
    });
    append(body, row, detailRow);
  });
  list.append(container);
}

async function loadChanges() {
  const area = $("#changes-area");
  area.replaceChildren(el("div", "loading", "Loading changes…"));
  try {
    const changes = await api("/api/changes", { snapshot: $("#change-snapshot").value });
    area.replaceChildren();
    const previous = state.status.snapshots.find((item) => item.id === changes.previous_snapshot_id);
    area.append(el("p", "muted", previous ? "Compared with " + prettyDate(previous.taken_on) : "Baseline snapshot"));
    const summary = el("div", "change-summary");
    for (const [name, value] of [["pages added", changes.pages_added.length], ["pages removed", changes.pages_removed.length], ["pages changed", changes.pages_changed.length], ["offers changed", changes.changed_offers.length]]) {
      const item = el("span");
      append(item, el("strong", "", value), document.createTextNode(name));
      summary.append(item);
    }
    area.append(summary);
    const grid = el("div", "changed-grid");
    for (const [heading, items] of [["Offer changes", changes.changed_offers], ["Document changes", [...changes.pages_changed, ...changes.pages_added.map((url) => "Added: " + url), ...changes.pages_removed.map((url) => "Removed: " + url)]]]) {
      const panel = el("section");
      panel.append(el("h2", "", heading));
      const list = el("div", "changed-list");
      if (!items.length) list.append(el("p", "empty", "No changes."));
      items.forEach((item) => {
        const row = el("div", "changed-item", typeof item === "string" ? item : item.topic);
        if (typeof item !== "string") {
          const values = item.values.map((value) => Object.entries(value)
            .filter(([key]) => ["rate", "apr", "apy"].includes(key))
            .map(([key, number]) => key.toUpperCase() + " " + number + "%").join(" · ")).join(" / ");
          row.append(el("small", "", values));
        }
        list.append(row);
      });
      panel.append(list);
      grid.append(panel);
    }
    area.append(grid);
  } catch (error) { area.replaceChildren(); showError(error); }
}

async function switchView(name) {
  clearError();
  document.querySelectorAll(".view").forEach((view) => view.classList.toggle("hidden", view.id !== "view-" + name));
  document.querySelectorAll(".nav-button").forEach((button) => {
    button.classList.toggle("active", button.dataset.view === name);
    if (button.dataset.view === name) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });
  $("#current-view").textContent = viewNames[name];
  document.title = viewNames[name] + " · chronicle";
  try {
    if (name === "conflicts") {
      if (!state.conflicts) state.conflicts = await api("/api/conflicts");
      renderConflicts();
    } else if (name === "changes") await loadChanges();
  } catch (error) { showError(error); }
}

async function init() {
  try {
    state.status = await api("/api/status");
    const snapshots = state.status.snapshots;
    $("#corpus-name").textContent = state.status.demo ? "Riverstone Credit Union" : "Credit union website";
    $("#corpus-detail").textContent = snapshots.length + " snapshots · " + state.status.offers + " offers";
    $("#observation-summary").textContent = "Latest snapshot: " + prettyDate(snapshots.at(-1).taken_on);
    snapshots.forEach((snapshot) => {
      const button = el("button", "snapshot-item");
      button.dataset.snapshot = snapshot.id;
      button.setAttribute("aria-pressed", "false");
      append(button, el("span", "", prettyDate(snapshot.taken_on)), el("small", "", snapshot.page_count + " pages"));
      button.addEventListener("click", async () => {
        $("#as-of").value = snapshot.taken_on;
        await switchView("ask");
        await ask();
      });
      $("#snapshot-list").append(button);
      const option = el("option", "", prettyDate(snapshot.taken_on));
      option.value = snapshot.id;
      $("#change-snapshot").append(option);
    });
    $("#change-snapshot").value = snapshots.at(-1).id;
    $("#question").value = "What is the APR for a 60-month auto loan on a 2025 vehicle?";
    await ask();
  } catch (error) { showError(error); }
}

$("#question-form").addEventListener("submit", (event) => { event.preventDefault(); ask(); });
$("#latest-button").addEventListener("click", () => { $("#as-of").value = ""; ask(); });
$("#question").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) { event.preventDefault(); ask(); }
});
$("#conflict-search").addEventListener("input", renderConflicts);
$("#conflict-type").addEventListener("change", renderConflicts);
$("#change-snapshot").addEventListener("change", loadChanges);
document.querySelectorAll("[data-view]").forEach((button) => button.addEventListener("click", () => switchView(button.dataset.view)));
$("#example-question").addEventListener("change", async (event) => {
  const example = event.target.value;
  if (!example || !state.status) return;
  const questions = {
    auto: "What is the APR for a 60-month auto loan on a 2025 vehicle?",
    certificate: "What is the APY for a " + (state.status.demo ? "12" : "17") + "-month special certificate?",
    missing: "What is the rate on a 7/1 adjustable-rate mortgage?",
  };
  $("#question").value = questions[example];
  $("#as-of").value = "";
  await ask();
});
init();
