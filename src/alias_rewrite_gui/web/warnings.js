import { escapeHtml } from "./dom_utils.js";

export function renderSummary(summary) {
  const target = document.querySelector("#summary");
  if (!target) return;
  if (!summary) {
    target.textContent = "0 rows / 0 edits / 0 warnings";
    return;
  }
  target.textContent = `${summary.rows} rows / ${summary.edits} edits / ${summary.warnings} warnings`;
}

export function renderLog(messages) {
  const log = document.querySelector(".log");
  if (!log) return;
  const heading = log.querySelector("h2")?.outerHTML || "<h2>LOG</h2>";
  const body = messages.length
    ? messages.map((message) => `<p>${escapeHtml(message)}</p>`).join("")
    : "<p>Ready.</p>";
  log.innerHTML = `${heading}${body}`;
}

export function warningMessages(warnings) {
  return (warnings || []).map((warning) => {
    const line = warning.line_number ? `line ${warning.line_number}: ` : "";
    return `[${warning.severity}] ${line}${warning.message}`;
  });
}

export function updateApplyState(canApply) {
  const button = document.querySelector("#apply-button");
  if (!button) return;
  button.disabled = !canApply;
  button.title = canApply ? "" : "Previewが未作成、またはdanger警告が残っているためApplyできません。";
}
