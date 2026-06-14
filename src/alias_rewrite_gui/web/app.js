import { callApi, chooseDirectory, chooseFile, syncMainWindowGeometry } from "./api.js";
import { escapeHtml } from "./dom_utils.js";
import { applySettingsToDom, collectApplyRequest, collectPreviewRequest, collectSettings } from "./state.js";
import {
  enableDragSort,
  clearSelection,
  previewRowsSnapshot,
  renderPreviewRows,
  rowsForApply,
  setManualSortMode,
  setRowPlayCallback,
  setRowSelectedCallback,
  setRowsChangedCallback,
} from "./preview_table.js";
import { renderLog, renderSummary, updateApplyState, warningMessages } from "./warnings.js";
import {
  enableExcludePlaceholder,
  enableModeOptions,
  enableRoundingControls,
  enableRuleScopeControls,
  enableSortChips,
  enableTooltips,
} from "./settings.js";

let lastInformation = null;
let selectedPreviewRow = null;
let sortCancelSnapshot = null;
let sortCancelCanApply = false;
let manualSortActive = false;
let currentAudioButton = null;
let currentAudioTimer = null;
let colorSchemes = [];
let ustUsageStale = false;
let hasUnappliedChanges = false;
let settingsWindowOpen = false;
let compactMinimumViewport = null;
let previewPaneVisible = false;
let lastPreviewRewrite = null;
let lastUstItems = [];
let previewStale = false;
let ustSearchTimer = null;

const previewAffectingSettingKeys = [
  "wav_edit_mode",
  "alias_edit_mode",
  "excluded_call_key_moras",
  "auto_wav_excluded_moras",
  "numbering_order_mode",
  "renumber_after_order_change",
  "strict_voice_match",
  "utau_exe_path",
  "relax_cannotcall_for_unused_ust_entries",
];

const expandedWindowBaseWidth = 956;
const previewHintWidthRatio = 0.2;
const previewAutoResizeWidthRatio = 0.7;
const leftPaneMinimumSizeRatio = 1;
const leftPaneFitSafetyPadding = 4;

window.setSettingsWindowOpen = (open) => {
  settingsWindowOpen = Boolean(open);
  document.body.classList.toggle("settings-window-open", settingsWindowOpen);
};

function setUnappliedChanges(value, { syncSize = true } = {}) {
  hasUnappliedChanges = Boolean(value);
  window.aliascaleHasUnappliedChanges = hasUnappliedChanges;
  window.pywebview?.api?.set_unapplied_changes?.({ value: hasUnappliedChanges }).catch(() => {});
  if (syncSize) syncWindowForPreviewState({ resizeToTarget: false }).catch(() => {});
}

function setPreviewStale(value) {
  previewStale = Boolean(value) && rowsForApply().length > 0;
  document.querySelector("#preview-button")?.classList.toggle("preview-stale", previewStale);
}

function markPreviewStale() {
  setPreviewStale(true);
}

function comparableSettingValue(value) {
  if (Array.isArray(value)) return value.map((item) => String(item ?? "")).join("\u0000");
  if (typeof value === "boolean") return value ? "1" : "0";
  return String(value ?? "");
}

function settingsChanged(previousSettings, nextSettings, keys) {
  return keys.some((key) => (
    comparableSettingValue(previousSettings?.[key]) !== comparableSettingValue(nextSettings?.[key])
  ));
}

window.requestMainClose = async () => {
  const closeMain = async () => {
    try {
      await callApi("force_close_main", {});
    } catch (_) {
      // Closing should proceed even if the native API is already shutting down.
    }
  };
  if (!hasUnappliedChanges) {
    await closeMain();
    return;
  }
  const ok = await askConfirm(
    "\u672a\u9069\u7528\u306e\u5909\u66f4\u304c\u3042\u308a\u307e\u3059\u3002",
    ["\u7de8\u96c6\u5185\u5bb9\u3092\u7834\u68c4\u3057\u3066\u7d42\u4e86\u3057\u307e\u3059\u304b\uff1f"],
    "\u306f\u3044",
    "\u30ad\u30e3\u30f3\u30bb\u30eb",
  );
  if (ok) await closeMain();
};

function numericCss(value) {
  const parsed = Number.parseFloat(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

function stripDuplicateIds(root) {
  root.querySelectorAll("[id]").forEach((element) => {
    element.removeAttribute("id");
  });
  root.querySelectorAll('input[type="radio"][name]').forEach((element, index) => {
    element.name = `__measure_${element.name}_${index}`;
  });
}

function measureCompactLeftShell() {
  const left = document.querySelector(".left");
  const leftRect = left.getBoundingClientRect();
  const clone = left.cloneNode(true);
  stripDuplicateIds(clone);
  clone.setAttribute("aria-hidden", "true");
  clone.querySelector(".ust-list")?.classList.add("is-empty");

  const scroll = clone.querySelector(".left-scroll");
  if (scroll) {
    scroll.style.overflow = "visible";
    scroll.style.height = "auto";
    scroll.style.maxHeight = "none";
  }

  const ust = clone.querySelector(".ust");
  if (ust) {
    ust.style.flex = "0 0 auto";
    ust.style.height = "auto";
    ust.style.maxHeight = "none";
    ust.style.minHeight = "0";
    ust.style.overflow = "visible";
  }

  Object.assign(clone.style, {
    position: "fixed",
    left: "-10000px",
    top: "0",
    width: `${Math.ceil(leftRect.width)}px`,
    height: "auto",
    maxHeight: "none",
    visibility: "hidden",
    pointerEvents: "none",
    overflow: "visible",
    zIndex: "-1",
  });

  document.body.appendChild(clone);
  const result = {
    width: Math.ceil(Math.max(clone.scrollWidth, left.scrollWidth, leftRect.width)),
    height: Math.ceil(clone.scrollHeight),
  };
  clone.remove();
  return result;
}

function enableAdvancedSettings() {
  const button = document.querySelector("#advanced-toggle");
  const content = document.querySelector("#advanced-content");
  const arrow = button?.querySelector(".advanced-arrow");
  if (!button || !content) return;

  const setOpen = (open) => {
    content.hidden = !open;
    button.setAttribute("aria-expanded", open ? "true" : "false");
    if (arrow) arrow.textContent = "▾";
  };

  setOpen(false);
  button.addEventListener("click", () => {
    setOpen(content.hidden);
  });
}

function measureCompactViewport() {
  const app = document.querySelector(".app");
  const topbar = document.querySelector(".topbar");
  const style = window.getComputedStyle(app);
  const paddingX = numericCss(style.paddingLeft) + numericCss(style.paddingRight);
  const paddingY = numericCss(style.paddingTop) + numericCss(style.paddingBottom);
  const safetyPadding = Math.ceil(leftPaneFitSafetyPadding * currentUiScale());
  const leftShell = measureCompactLeftShell();
  return {
    width: Math.ceil(leftShell.width + paddingX + safetyPadding),
    height: Math.ceil(topbar.offsetHeight + leftShell.height + paddingY + safetyPadding),
  };
}

function currentUiScale() {
  const value = Number.parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--ui-scale"));
  return Number.isFinite(value) && value > 0 ? value : 1;
}

function currentViewportSize() {
  const visualViewport = window.visualViewport;
  const visualWidth = Number(visualViewport?.width) || 0;
  const visualHeight = Number(visualViewport?.height) || 0;
  return {
    width: Math.max(1, Math.round(visualWidth || window.innerWidth || document.documentElement.clientWidth || 0)),
    height: Math.max(1, Math.round(visualHeight || window.innerHeight || document.documentElement.clientHeight || 0)),
  };
}

function nextFrame() {
  return new Promise((resolve) => requestAnimationFrame(() => resolve()));
}

function jsonClone(value) {
  return value == null ? value : JSON.parse(JSON.stringify(value));
}

function hasPreviewRowsInTable() {
  return rowsForApply().length > 0;
}

function setPreviewPaneVisible(visible) {
  previewPaneVisible = Boolean(visible);
  document.querySelector(".app")?.classList.toggle("compact-start", !previewPaneVisible);
}

async function syncPreviewVisibilityTransition(previousHasRows, nextHasRows) {
  const nextVisible = Boolean(nextHasRows);
  const changed = Boolean(previousHasRows) !== nextVisible;
  setPreviewPaneVisible(nextVisible);
  if (!changed) return;
  await nextFrame();
  await syncWindowForPreviewState({
    resizeBelowMinimumOnly: true,
    hasPreviewRows: nextVisible,
    showPreviewPane: nextVisible,
    preserveCurrentHeight: true,
  });
}

async function showPreviewPaneForLoading() {
  if (!previewPaneVisible) {
    setPreviewPaneVisible(true);
    await nextFrame();
  }
  await syncWindowForPreviewState({
    resizeBelowMinimumOnly: true,
    hasPreviewRows: true,
    showPreviewPane: true,
    preserveCurrentHeight: true,
  });
}

function rightPaneFullWidth(leftWidth, scale = currentUiScale()) {
  const expandedWidth = expandedWindowBaseWidth * scale;
  return Math.max(0, expandedWidth - leftWidth);
}

function geometryForPreviewState(leftSize, { hasPreviewRows = hasPreviewRowsInTable(), showPreviewPane = previewPaneVisible, scale = currentUiScale() } = {}) {
  const leftWidth = Math.ceil(leftSize.width);
  const leftHeight = leftSize.height;
  const rightWidth = rightPaneFullWidth(leftWidth, scale);
  const startupMinWidth = Math.ceil(leftWidth * leftPaneMinimumSizeRatio);
  const startupMinHeight = Math.ceil(leftHeight * leftPaneMinimumSizeRatio);
  const usesPreviewWidth = Boolean(hasPreviewRows || showPreviewPane);
  return {
    minWidth: usesPreviewWidth ? Math.ceil(leftWidth + rightWidth * previewHintWidthRatio) : startupMinWidth,
    minHeight: startupMinHeight,
    resizeMinWidth: usesPreviewWidth ? Math.ceil(leftWidth + rightWidth * previewAutoResizeWidthRatio) : startupMinWidth,
    targetWidth: showPreviewPane ? Math.ceil(leftWidth + rightWidth) : Math.ceil(leftWidth),
    targetHeight: Math.ceil(leftHeight),
  };
}

async function syncWindowForPreviewState({
  resizeToTarget = false,
  resizeToTargetWidth = resizeToTarget,
  resizeToTargetHeight = resizeToTarget,
  hasPreviewRows = hasPreviewRowsInTable(),
  showPreviewPane = previewPaneVisible,
  refreshCompactHeight = false,
  preserveCurrentHeight = false,
  resizeBelowMinimumOnly = false,
} = {}) {
  const measuredLeftSize = measureCompactViewport();
  if (!compactMinimumViewport || refreshCompactHeight || !showPreviewPane) {
    compactMinimumViewport = {
      width: Math.ceil(measuredLeftSize.width),
      height: Math.ceil(measuredLeftSize.height),
    };
  }
  const leftSize = {
    width: Math.ceil(measuredLeftSize.width),
    height: compactMinimumViewport.height,
  };
  const geometry = geometryForPreviewState(leftSize, { hasPreviewRows, showPreviewPane });
  compactMinimumViewport = {
    width: Math.ceil(leftSize.width),
    height: Math.ceil(leftSize.height),
  };
  const viewport = currentViewportSize();
  let minHeight = preserveCurrentHeight ? Math.min(geometry.minHeight, viewport.height) : geometry.minHeight;
  let targetWidth = geometry.targetWidth;
  let targetHeight = preserveCurrentHeight ? viewport.height : geometry.targetHeight;
  let resizeToMinimumWidth = false;
  let resizeToMinimumHeight = false;
  let resizeToTargetWidthIfBelow = false;

  if (resizeBelowMinimumOnly) {
    const resizeMinWidth = geometry.resizeMinWidth || geometry.minWidth;
    resizeToTargetWidth = false;
    resizeToTargetHeight = false;
    targetWidth = resizeMinWidth;
    targetHeight = geometry.minHeight;
    resizeToTargetWidthIfBelow = true;
    resizeToMinimumHeight = viewport.height < geometry.minHeight;
    if (resizeToMinimumHeight) {
      minHeight = geometry.minHeight;
    }
  }

  await syncMainWindowGeometry({
    min_width: geometry.minWidth,
    min_height: minHeight,
    target_width: targetWidth,
    target_height: targetHeight,
    resize_to_min_width: resizeToMinimumWidth,
    resize_to_min_height: resizeToMinimumHeight,
    resize_to_target_width_if_below: resizeToTargetWidthIfBelow,
    resize_to_target_width: resizeToTargetWidth,
    resize_to_target_height: resizeToTargetHeight,
  });
}

function measureMinimumViewportForCurrentState(settings = null) {
  if (settings) {
    applySettingsToDom(settings);
  }
  const parsedScale = Number.parseFloat(settings?.ui_scale ?? currentUiScale());
  const targetScale = Number.isFinite(parsedScale) && parsedScale > 0 ? parsedScale : 1;
  document.documentElement.style.setProperty("--ui-scale", String(targetScale));
  syncUstListForPreview();
  document.querySelector(".app")?.getBoundingClientRect();
  const leftSize = measureCompactViewport();
  const geometry = geometryForPreviewState(leftSize, {
    hasPreviewRows: previewPaneVisible || rowsForApply().length > 0,
    showPreviewPane: previewPaneVisible,
    scale: targetScale,
  });
  const viewport = currentViewportSize();
  return {
    min_width: Math.ceil(geometry.minWidth),
    min_height: Math.ceil(geometry.minHeight),
    target_width: Math.ceil(geometry.targetWidth),
    target_height: Math.ceil(geometry.targetHeight),
    current_width: viewport.width,
    current_height: viewport.height,
  };
}

function syncUstListForPreview() {
  const list = document.querySelector(".ust-list");
  const hasRoot = Boolean(document.querySelector("#ust-folder")?.value || "");
  const hasItems = Boolean(list?.children.length);
  list.classList.toggle("is-empty", !hasRoot || !hasItems);
}

function mergedUstItemsWithDiff(nextItems, previousItems) {
  const previousByPath = new Map((previousItems || []).map((item) => [item.path, item]));
  const nextByPath = new Map((nextItems || []).map((item) => [item.path, item]));
  const merged = [];
  for (const item of nextItems || []) {
    merged.push({
      ...item,
      diffState: previousByPath.has(item.path) || !previousItems?.length ? "" : "added",
    });
  }
  for (const item of previousItems || []) {
    if (!nextByPath.has(item.path)) {
      merged.push({
        ...item,
        checked: false,
        disabled: true,
        diffState: "removed",
      });
    }
  }
  return merged;
}

function renderUstList(items, { diffAgainst = null } = {}) {
  const list = document.querySelector(".ust-list");
  list.textContent = "";
  const settings = collectSettings();
  const displayItems = diffAgainst ? mergedUstItemsWithDiff(items || [], diffAgainst) : (items || []);
  lastUstItems = displayItems.map((item) => ({ ...item }));
  displayItems.forEach((item) => {
    const label = document.createElement("label");
    if (item.diffState === "added") label.classList.add("ust-added");
    if (item.diffState === "removed") label.classList.add("ust-removed");
    const input = document.createElement("input");
    input.type = "checkbox";
    input.checked = item.diffState === "removed" ? false : item.checked !== false;
    input.disabled = Boolean(item.disabled || item.diffState === "removed");
    input.dataset.path = item.path;
    input.addEventListener("change", () => {
      const target = lastUstItems.find((ustItem) => ustItem.path === item.path);
      if (target) target.checked = input.checked;
      markPreviewStale();
      syncInformationDetailWindow();
      if (rowsForApply().length) validateEditedRows(rowsForApply());
    });
    label.appendChild(input);
    const pathLabel = settings.show_full_ust_path ? item.path : String(item.path || item.label || "").split(/[\\/]/).pop();
    label.append(` ${pathLabel || item.label || ""}`);
    list.appendChild(label);
  });
  syncUstListForPreview();
}

function scheduleUstSearch({ markStale = true } = {}) {
  if (ustSearchTimer) window.clearTimeout(ustSearchTimer);
  if (markStale) markPreviewStale();
  ustSearchTimer = window.setTimeout(() => {
    ustSearchTimer = null;
    runUstSearch({ diff: true }).catch((error) => renderLog([error.message || String(error)]));
  }, 350);
}

function resetPreviewAfterApply() {
  const settings = collectSettings();
  manualSortActive = false;
  setPreviewPaneVisible(false);
  document.querySelector("#sort-order-button").hidden = false;
  document.querySelector("#sort-order-actions").hidden = true;
  setManualSortMode(false);
  renderPreviewRows([], {
    wavEditMode: settings.wav_edit_mode,
    aliasEditMode: settings.alias_edit_mode,
  });
  renderSummary(null);
  renderUstList([]);
  setPreviewStale(false);
  setUnappliedChanges(false);
  clearSelection();
  updateApplyState(false);
  syncInformationDetailWindow();
}

function renderPitchChart(distribution) {
  const chart = document.querySelector("#pitch-chart");
  if (!chart) return;
  const bins = distribution || [];
  const maxCount = Math.max(1, ...bins.map((bin) => Number(bin.count) || 0));
  chart.innerHTML = bins
    .map((bin) => {
      const height = Math.max(1, Math.round(((Number(bin.count) || 0) / maxCount) * 100));
      return `<div class="pitch-bar" title="${escapeHtml(bin.label)}: ${Number(bin.count) || 0}" style="height:${height}%"></div>`;
    })
    .join("");
}

function renderVoiceInformation(information) {
  lastInformation = information || {};
  selectedPreviewRow = null;
  const info = document.querySelector(".info > div");
  if (!info) return;
  const min = information?.frequency_min ? `${Number(information.frequency_min).toFixed(1)}Hz` : "-";
  const max = information?.frequency_max ? `${Number(information.frequency_max).toFixed(1)}Hz` : "-";
  const avg = information?.frequency_average ? `${Number(information.frequency_average).toFixed(1)}Hz` : "-";
  info.innerHTML = `
    <div class="info-title"><h2>INFORMATION</h2><button id="information-detail-button" type="button">詳細</button></div>
    <p>voice: ${escapeHtml(information?.voice_name || "-")} / wav: ${information?.wav_file_count ?? 0} / aliases: ${information?.alias_count ?? 0} / empty: ${information?.empty_alias_count ?? 0}</p>
    <p>frequency: ${escapeHtml(information?.frequency_table_type || "-")} / range: ${min} - ${max} / avg: ${avg}</p>
    <p>ust usage: ${information?.ust_usage_ust_count ?? 0} UST / ${information?.ust_usage_resolved_lyrics ?? 0}/${information?.ust_usage_total_lyrics ?? 0} resolved / unresolved: ${information?.ust_usage_unresolved_lyrics ?? 0}</p>
    <div class="pitch-chart" id="pitch-chart" aria-label="C1-B6 pitch distribution"></div>
  `;
  renderPitchChart(information?.pitch_distribution || []);
  updateInformationPlayButton();
  syncInformationDetailWindow();
}

function renderRowInformation(row) {
  if (!row) {
    renderVoiceInformation(lastInformation || {});
    return;
  }
  selectedPreviewRow = row;
  const info = document.querySelector(".info > div");
  if (!info) return;
  const note = row.note || inferNote(row.new_alias) || inferNote(row.new_wav) || "-";
  info.innerHTML = `
    <div class="info-title"><h2>INFORMATION</h2><button id="information-detail-button" type="button">詳細</button></div>
    <p>line: ${row.line_number ?? "-"} / order: ${row.old_order_id ?? "-"} -> ${row.new_order_id ?? "-"}</p>
    <p>wav: ${escapeHtml(row.old_wav)} -> ${escapeHtml(row.new_wav)}</p>
    <p>alias: ${escapeHtml(row.old_alias || "(empty)")} -> ${escapeHtml(row.new_alias || "(empty)")}</p>
    <p>note: ${escapeHtml(note)} / frequency: ${row.frequency == null ? "-" : Number(row.frequency).toFixed(3)}</p>
    <p>status: ${escapeHtml(row.status || row.severity || row.reason || "-")} / UST usage: ${row.usage_count ?? 0}</p>
  `;
  updateInformationPlayButton();
  syncInformationDetailWindow();
}

function collectInformationPayload() {
  const payload = collectApplyRequest(rowsForApply());
  payload.selected_line_number = selectedPreviewRow?.line_number ?? null;
  payload.usage_stale = false;
  return payload;
}

async function syncInformationDetailWindow() {
  try {
    await callApi("update_information_window", collectInformationPayload());
  } catch (_error) {
    // The detail window may not be open yet.
  }
}

async function openInformationDetailWindow() {
  try {
    await callApi("open_information_window", collectInformationPayload());
  } catch (error) {
    renderLog([error.message || String(error)]);
  }
}

function inferNote(value) {
  const match = String(value || "").match(/[A-G]#?-?\d/);
  return match ? match[0] : "";
}

function showMessageDialog(title, lines) {
  const modal = document.querySelector("#confirm-modal");
  const message = document.querySelector("#confirm-message");
  const cancel = document.querySelector("#confirm-cancel");
  const ok = document.querySelector("#confirm-ok");
  if (!modal || !message || !ok) return;
  message.innerHTML = `<strong>${escapeHtml(title)}</strong><br>${(lines || []).map(escapeHtml).join("<br>")}`;
  cancel.hidden = true;
  modal.classList.add("open");
  modal.setAttribute("aria-hidden", "false");
  const close = () => {
    modal.classList.remove("open");
    modal.setAttribute("aria-hidden", "true");
    ok.removeEventListener("click", close);
    cancel.hidden = false;
  };
  ok.addEventListener("click", close);
}

// HTML形式の通知ダイアログを表示する
function showHtmlMessageDialog(html) {
  const modal = document.querySelector("#confirm-modal");
  const message = document.querySelector("#confirm-message");
  const cancel = document.querySelector("#confirm-cancel");
  const ok = document.querySelector("#confirm-ok");
  if (!modal || !message || !ok) return;
  message.innerHTML = html;
  cancel.hidden = true;
  modal.classList.add("open");
  modal.setAttribute("aria-hidden", "false");
  const close = () => {
    modal.classList.remove("open");
    modal.setAttribute("aria-hidden", "true");
    ok.removeEventListener("click", close);
    cancel.hidden = false;
  };
  ok.addEventListener("click", close);
}

function askConfirm(title, lines, yesText = "Yes", noText = "No") {
  return new Promise((resolve) => {
    const modal = document.querySelector("#confirm-modal");
    const message = document.querySelector("#confirm-message");
    const cancel = document.querySelector("#confirm-cancel");
    const ok = document.querySelector("#confirm-ok");
    if (!modal || !message || !ok || !cancel) {
      resolve(false);
      return;
    }
    message.innerHTML = `<strong>${escapeHtml(title)}</strong><br>${(lines || []).map(escapeHtml).join("<br>")}`;
    ok.textContent = yesText;
    cancel.textContent = noText;
    cancel.hidden = false;
    modal.classList.add("open");
    modal.setAttribute("aria-hidden", "false");
    const cleanup = (value) => {
      modal.classList.remove("open");
      modal.setAttribute("aria-hidden", "true");
      ok.textContent = "OK";
      cancel.textContent = "キャンセル";
      ok.removeEventListener("click", onOk);
      cancel.removeEventListener("click", onCancel);
      resolve(value);
    };
    const onOk = () => cleanup(true);
    const onCancel = () => cleanup(false);
    ok.addEventListener("click", onOk);
    cancel.addEventListener("click", onCancel);
  });
}

// Apply結果をユーザー向けの概要表示に整える
function applyResultHtml(response, summary, settings) {
  const errors = response.errors || [];
  const warnings = response.warnings || [];
  const skipped = response.skipped || [];
  const hasErrors = errors.length > 0;
  const hasIssues = hasErrors || warnings.length > 0 || skipped.length > 0 || (response.moved_to_conflict_folder || []).length > 0;
  const title = hasErrors ? "一部の処理に失敗しました" : hasIssues ? "適用が完了しました（一部注意あり）" : "適用が完了しました";
  const lead = hasErrors
    ? "変更できなかった項目があります。必要に応じてログとバックアップを確認してください。"
    : hasIssues
      ? "変更は完了しましたが、確認が必要な項目があります。"
      : "変更内容を反映しました。";
  const csvState = response.csv_path
    ? "出力済み"
    : settings.write_csv
      ? "出力なし"
      : "OFF";
  const backupState = settings.backup
    ? (response.backups || []).length
      ? "作成済み"
      : "作成なし"
    : "OFF";
  const cards = [
    ["oto.ini", `${summary?.oto_changed_rows ?? 0} 行`],
    ["原音ファイル", `${summary?.wav_file_count ?? 0} 件`],
    ["原音関連ファイル", `${summary?.related_file_count ?? 0} 件`],
    ["UST", `${summary?.ust_write_count ?? 0} 件`],
    ["CSV", csvState],
    ["バックアップ", backupState],
  ];
  const issueItems = [
    ["エラー", errors.length, "danger"],
    ["警告", warnings.length, "warning"],
    ["スキップ", skipped.length, skipped.length ? "warning" : "ok"],
    ["退避", (response.moved_to_conflict_folder || []).length, "warning"],
  ].filter((item) => item[1] > 0);
  const issueHtml = issueItems.length
    ? `<section class="apply-section"><h3>確認が必要な項目</h3><div class="apply-issue-list">${issueItems
        .map(([label, count, level]) => `<div class="apply-issue ${level}"><span>${escapeHtml(label)}</span><strong>${count}件</strong></div>`)
        .join("")}</div></section>`
    : `<section class="apply-section"><h3>確認が必要な項目</h3><p class="apply-muted">ありません</p></section>`;
  const logHtml = response.log_path
    ? `<p class="apply-log">詳細ログ: <code>${escapeHtml(response.log_path)}</code></p>`
    : `<p class="apply-muted">詳細ログは出力されていません</p>`;
  return `
    <article class="apply-result ${hasErrors ? "has-errors" : hasIssues ? "has-issues" : "is-success"}">
      <h2>${escapeHtml(title)}</h2>
      <p class="apply-lead">${escapeHtml(lead)}</p>
      <section class="apply-section">
        <h3>変更内容</h3>
        <div class="apply-card-grid">
          ${cards.map(([label, value]) => `<div class="apply-card"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`).join("")}
        </div>
      </section>
      ${issueHtml}
      <section class="apply-section">
        <h3>詳細</h3>
        ${logHtml}
      </section>
    </article>
  `;
}

// Apply結果を小さめのテキスト一覧と折り畳み詳細に整える
function applyResultTextHtml(response, summary, settings) {
  const errors = response.errors || [];
  const warnings = response.warnings || [];
  const skipped = response.skipped || [];
  const conflicts = response.moved_to_conflict_folder || [];
  const writtenFiles = response.written_files || [];
  const backups = response.backups || [];
  const hasErrors = errors.length > 0;
  const hasIssues = hasErrors || warnings.length > 0 || skipped.length > 0 || conflicts.length > 0;
  const title = hasErrors ? "一部の処理に失敗しました" : hasIssues ? "適用が完了しました（warning）" : "適用が完了しました";
  const lead = hasErrors
    ? "変更できなかった項目があります。詳細ログとバックアップを確認してください。"
    : hasIssues
      ? "変更内容を反映しましたが、問題が発生した可能性があります。"
      : "変更内容を反映しました。";
  const csvState = response.csv_path ? "出力済み" : settings.write_csv ? "出力なし" : "OFF";
  const backupState = settings.backup ? (backups.length ? "作成済み" : "作成なし") : "OFF";
  const mrqWrittenCount = writtenFiles.filter((path) => String(path).toLowerCase().endsWith(".mrq")).length;
  const summaryRows = [
    ["oto.ini", `${summary?.oto_changed_rows ?? 0} 行を書き換え`],
    ["原音ファイル", `${summary?.wav_file_count ?? 0} 件をrename`],
    ["原音関連ファイル", `${summary?.related_file_count ?? 0} 件をrename`],
    ["MRQ", `${mrqWrittenCount || summary?.mrq_file_count || 0} 件を書き換え`],
    ["UST", `${summary?.ust_write_count ?? 0} 件を書き換え`],
    ["CSV", csvState],
    ["バックアップ", backupState],
  ];
  const issueRows = [
    ["エラー", errors.length, "danger"],
    ["警告", warnings.length, "warning"],
    ["スキップ", skipped.length, "warning"],
    ["退避", conflicts.length, "warning"],
  ].filter(([, count]) => count > 0);
  const detailGroups = [
    ["エラー", errors],
    ["警告", warnings],
    ["スキップ", skipped],
    ["更新されたファイル", writtenFiles],
    ["退避されたファイル", conflicts],
    ["バックアップ", backups],
    ["CSV", response.csv_path ? [response.csv_path] : []],
    ["詳細ログ", response.log_path ? [response.log_path] : []],
  ].filter(([, items]) => items.length > 0);
  const summaryHtml = summaryRows
    .map(([label, value]) => `<li><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></li>`)
    .join("");
  const issueHtml = issueRows.length
    ? issueRows
        .map(([label, count, level]) => `<li class="${level}"><span>${escapeHtml(label)}</span><strong>${count} 件</strong></li>`)
        .join("")
    : `<li><span>確認が必要な項目</span><strong>ありません</strong></li>`;
  const detailsHtml = detailGroups.length
    ? `<details class="apply-details"><summary>詳細を表示</summary>${detailGroups
        .map(
          ([label, items]) => `
            <section>
              <h4>${escapeHtml(label)}</h4>
              <ul>${items.map((item) => `<li>${escapeHtml(String(item))}</li>`).join("")}</ul>
            </section>
          `
        )
        .join("")}</details>`
    : `<p class="apply-muted">個別に確認する項目はありません</p>`;
  return `
    <article class="apply-result ${hasErrors ? "has-errors" : hasIssues ? "has-issues" : "is-success"}">
      <h2>${escapeHtml(title)}</h2>
      <p class="apply-lead">${escapeHtml(lead)}</p>
      <section class="apply-section">
        <h3>変更内容</h3>
        <ul class="apply-text-list">${summaryHtml}</ul>
      </section>
      <section class="apply-section">
        <h3>確認が必要な項目</h3>
        <ul class="apply-text-list">${issueHtml}</ul>
      </section>
      <section class="apply-section">
        <h3>詳細</h3>
        ${detailsHtml}
      </section>
    </article>
  `;
}

/* function formatApplySummary(summary) {
  if (summary?.errors?.length) {
    return [
      "確認用の件数取得に失敗しました。",
      ...summary.errors.map((message) => String(message)),
      "このまま適用しますか？",
    ];
  }
  return [
    `音源パス: ${summary?.voice_dir || "-"}`,
    `oto.iniの書き換え行数: ${summary?.oto_changed_rows ?? 0}`,
    `USTの書き換え数: ${summary?.ust_write_count ?? 0}`,
    `原音ファイルの書き換え数: ${summary?.wav_file_count ?? 0}`,
    `原音関連ファイルの書き換え数: ${summary?.related_file_count ?? 0}`,
  ];
}

*/
function formatApplySummary(summary) {
  if (summary?.errors?.length) {
    return [
      "\u78ba\u8a8d\u7528\u306e\u4ef6\u6570\u53d6\u5f97\u306b\u5931\u6557\u3057\u307e\u3057\u305f\u3002",
      ...summary.errors.map((message) => String(message)),
      "\u3053\u306e\u307e\u307e\u9069\u7528\u3057\u307e\u3059\u304b\uff1f",
    ];
  }
  return [
    `\u97f3\u6e90\u30d1\u30b9: ${summary?.voice_dir || "-"}`,
    `oto.ini\u306e\u66f8\u304d\u63db\u3048\u884c\u6570: ${summary?.oto_changed_rows ?? 0}`,
    `UST\u306e\u66f8\u304d\u63db\u3048\u6570: ${summary?.ust_write_count ?? 0}`,
    `\u539f\u97f3\u30d5\u30a1\u30a4\u30eb\u306e\u66f8\u304d\u63db\u3048\u6570: ${summary?.wav_file_count ?? 0}`,
    `\u539f\u97f3\u95a2\u9023\u30d5\u30a1\u30a4\u30eb\u306e\u66f8\u304d\u63db\u3048\u6570: ${summary?.related_file_count ?? 0}`,
    `MRQ\u306e\u66f8\u304d\u63db\u3048\u6570: ${summary?.mrq_file_count ?? 0}`,
  ];
}

async function runPreview() {
  const hadPreviewRows = hasPreviewRowsInTable();
  const wasPreviewPaneVisible = previewPaneVisible;
  syncUstListForPreview();
  setPreviewLoading(true);
  await showPreviewPaneForLoading();
  const transitionBaselineVisible = hadPreviewRows || wasPreviewPaneVisible || previewPaneVisible;
  try {
    const request = collectPreviewRequest();
    lastPreviewRewrite = jsonClone(request.rewrite);
    const response = await callApi("preview", request);
    if (response.errors?.length) {
      const settings = collectSettings();
      renderPreviewRows([], {
        wavEditMode: settings.wav_edit_mode,
        aliasEditMode: settings.alias_edit_mode,
      });
      renderSummary(null);
      renderLog(response.errors);
      updateApplyState(false);
      setUnappliedChanges(false, { syncSize: false });
      setPreviewPaneVisible(true);
      return;
    }
    const settings = collectSettings();
    const previewRows = response.rows || [];
    renderPreviewRows(response.rows || [], {
      wavEditMode: settings.wav_edit_mode,
      aliasEditMode: settings.alias_edit_mode,
    });
    renderSummary(response.summary);
    renderUstList(response.ust_list || []);
    ustUsageStale = false;
    setPreviewStale(false);
    renderVoiceInformation(response.information || {});
    renderLog(warningMessages(response.warnings || []));
    updateApplyState(!manualSortActive && (Boolean(response.summary?.can_apply) || !collectSettings().block_on_danger));
    setUnappliedChanges(Boolean(previewRows.length), { syncSize: false });
    await syncPreviewVisibilityTransition(transitionBaselineVisible, true);
  } catch (error) {
    const settings = collectSettings();
    renderPreviewRows([], {
      wavEditMode: settings.wav_edit_mode,
      aliasEditMode: settings.alias_edit_mode,
    });
    renderSummary(null);
    renderLog([error.message || String(error)]);
    updateApplyState(false);
    setUnappliedChanges(false, { syncSize: false });
    setPreviewPaneVisible(true);
  } finally {
    setPreviewLoading(false);
  }
}

async function runUstSearch({ diff = false } = {}) {
  syncUstListForPreview();
  setPreviewLoading(true);
  try {
    const previousItems = lastUstItems.map((item) => ({ ...item }));
    const response = await callApi("search_ust", collectPreviewRequest());
    if (response.errors?.length) {
      renderLog(response.errors);
      return;
    }
    renderUstList(response.ust_list || [], { diffAgainst: diff ? previousItems : null });
    ustUsageStale = false;
    renderVoiceInformation(response.information || {});
    if (rowsForApply().length) {
      await validateEditedRows(rowsForApply());
    }
  } catch (error) {
    renderLog([error.message || String(error)]);
  } finally {
    setPreviewLoading(false);
  }
}

function setPreviewLoading(loading) {
  const overlay = document.querySelector("#preview-loading");
  if (!overlay) return;
  overlay.classList.toggle("open", Boolean(loading));
  overlay.setAttribute("aria-hidden", loading ? "false" : "true");
}

function enablePreviewColumnResize() {
  const table = document.querySelector("#preview");
  if (!table) return;
  const cols = Array.from(table.querySelectorAll("colgroup col"));
  const headers = Array.from(table.querySelectorAll("thead th"));
  const freezeColumnWidths = () => {
    const widths = headers.map((header) => Math.round(header.getBoundingClientRect().width));
    widths.forEach((width, colIndex) => {
      if (cols[colIndex]) cols[colIndex].style.width = `${width}px`;
    });
    table.style.width = `${widths.reduce((sum, width) => sum + width, 0)}px`;
  };
  headers.forEach((header, index) => {
    if (header.classList.contains("op") || header.querySelector(".col-resizer")) return;
    const handle = document.createElement("span");
    handle.className = "col-resizer";
    handle.setAttribute("aria-hidden", "true");
    header.appendChild(handle);
    handle.addEventListener("pointerdown", (event) => {
      const col = cols[index];
      if (!col) return;
      event.preventDefault();
      const startX = event.clientX;
      const startWidth = header.getBoundingClientRect().width;
      const scale = numericCss(getComputedStyle(document.documentElement).getPropertyValue("--ui-scale")) || 1;
      const minWidth = Math.round(72 * scale);
      freezeColumnWidths();
      handle.classList.add("dragging");
      document.body.classList.add("column-resizing");
      handle.setPointerCapture?.(event.pointerId);

      const onPointerMove = (moveEvent) => {
        const width = Math.max(minWidth, Math.round(startWidth + moveEvent.clientX - startX));
        col.style.width = `${width}px`;
        const tableWidth = cols.reduce((sum, currentCol) => {
          const parsed = Number.parseFloat(currentCol.style.width);
          return sum + (Number.isFinite(parsed) ? parsed : 0);
        }, 0);
        table.style.width = `${Math.max(tableWidth, table.parentElement?.clientWidth || 0)}px`;
      };
      const onPointerUp = () => {
        handle.classList.remove("dragging");
        document.body.classList.remove("column-resizing");
        window.removeEventListener("pointermove", onPointerMove);
        window.removeEventListener("pointerup", onPointerUp);
        window.removeEventListener("pointercancel", onPointerUp);
      };
      window.addEventListener("pointermove", onPointerMove);
      window.addEventListener("pointerup", onPointerUp);
      window.addEventListener("pointercancel", onPointerUp);
    });
  });
}

function applyRequestWithPreviewRewrite(rows) {
  const payload = collectApplyRequest(rows);
  if (lastPreviewRewrite) payload.rewrite = jsonClone(lastPreviewRewrite);
  return payload;
}

async function validateEditedRows(rows) {
  try {
    const response = await callApi("validate_rows", applyRequestWithPreviewRewrite(rows));
    if (response.errors?.length) {
      renderLog(response.errors);
      updateApplyState(false);
      return;
    }
    const settings = collectSettings();
    renderPreviewRows(response.rows || [], {
      wavEditMode: settings.wav_edit_mode,
      aliasEditMode: settings.alias_edit_mode,
    });
    renderSummary(response.summary);
    ustUsageStale = false;
    renderVoiceInformation(response.information || lastInformation || {});
    renderLog(warningMessages(response.warnings || []));
    updateApplyState(!manualSortActive && (Boolean(response.summary?.can_apply) || !collectSettings().block_on_danger));
    if (rows.length) setUnappliedChanges(true);
  } catch (error) {
    renderLog([error.message || String(error)]);
    updateApplyState(false);
  }
}

async function runApply() {
  try {
    const rows = rowsForApply();
    const settings = collectSettings();
    const dangerMessages = rows
      .filter((row) => row.severity === "danger")
      .flatMap((row) => row.warnings || [])
      .slice(0, 20);
    if (dangerMessages.length && !settings.block_on_danger) {
      const ok = await askConfirm("danger\u8b66\u544a\u3092\u7121\u8996\u3057\u3066\u9069\u7528\u3057\u307e\u3059\u304b\uff1f", dangerMessages, "\u306f\u3044", "\u3044\u3044\u3048");
      if (!ok) return;
    }
    const payload = collectApplyRequest(rows);
    const summary = await callApi("apply_summary", payload);
    /*
    const confirmed = await askConfirm("以下の内容を適用しますか？", formatApplySummary(summary), "適用", "キャンセル");
    */
    const confirmed = await askConfirm(
      "\u4ee5\u4e0b\u306e\u5185\u5bb9\u3092\u9069\u7528\u3057\u307e\u3059\u304b\uff1f",
      formatApplySummary(summary),
      "\u9069\u7528",
      "\u30ad\u30e3\u30f3\u30bb\u30eb",
    );
    if (!confirmed) return;
    const response = await callApi("apply", payload);
    const messages = [
      ...(response.errors || []).map((message) => `[error] ${message}`),
      ...(response.warnings || []).map((message) => `[warning] ${message}`),
      ...(response.written_files || []).map((path) => `[written] ${path}`),
      ...(response.moved_to_conflict_folder || []).map((path) => `[conflict] ${path}`),
    ];
    renderLog(messages.length ? messages : ["Apply completed."]);
    showHtmlMessageDialog(applyResultTextHtml(response, summary, settings));
    resetPreviewAfterApply();
  } catch (error) {
    renderLog([error.message || String(error)]);
  }
}

function enableBrowseButtons() {
  document.querySelector("#browse-voice")?.addEventListener("click", async () => {
    const selected = await chooseDirectory("voice").catch((error) => {
      renderLog([error.message || String(error)]);
      return "";
    });
    if (selected) document.querySelector("#voice-folder").value = selected;
    if (selected) markPreviewStale();
    if (!selected) renderLog(["\u9078\u629e\u304c\u30ad\u30e3\u30f3\u30bb\u30eb\u3055\u308c\u305f\u304b\u3001\u30d5\u30a1\u30a4\u30eb\u30c0\u30a4\u30a2\u30ed\u30b0\u3092\u958b\u3051\u307e\u305b\u3093\u3067\u3057\u305f\u3002"]);
    const hasBlockingDanger = rowsForApply().some((row) => row.severity === "danger") && collectSettings().block_on_danger;
    updateApplyState(hasPreviewRowsInTable() && !hasBlockingDanger);
  });
  document.querySelector("#browse-ust")?.addEventListener("click", async () => {
    const selected = await chooseDirectory("ust").catch((error) => {
      renderLog([error.message || String(error)]);
      return "";
    });
    if (selected) {
      document.querySelector("#ust-folder").value = selected;
      scheduleUstSearch();
      syncInformationDetailWindow();
    }
    if (!selected) renderLog(["\u9078\u629e\u304c\u30ad\u30e3\u30f3\u30bb\u30eb\u3055\u308c\u305f\u304b\u3001\u30d5\u30a1\u30a4\u30eb\u30c0\u30a4\u30a2\u30ed\u30b0\u3092\u958b\u3051\u307e\u305b\u3093\u3067\u3057\u305f\u3002"]);
  });
  document.querySelector("#browse-mrq")?.addEventListener("click", async () => {
    const selected = await chooseFile("mrq").catch((error) => {
      renderLog([error.message || String(error)]);
      return "";
    });
    if (selected) document.querySelector("#mrq-path").value = selected;
    if (selected) markPreviewStale();
    if (!selected) renderLog(["\u9078\u629e\u304c\u30ad\u30e3\u30f3\u30bb\u30eb\u3055\u308c\u305f\u304b\u3001\u30d5\u30a1\u30a4\u30eb\u30c0\u30a4\u30a2\u30ed\u30b0\u3092\u958b\u3051\u307e\u305b\u3093\u3067\u3057\u305f\u3002"]);
  });
  document.querySelector("#browse-csv")?.addEventListener("click", async () => {
    const selected = await chooseFile("csv").catch((error) => {
      renderLog([error.message || String(error)]);
      return "";
    });
    if (selected) document.querySelector("#csv-path").value = selected;
    if (selected) markPreviewStale();
    if (!selected) renderLog(["\u9078\u629e\u304c\u30ad\u30e3\u30f3\u30bb\u30eb\u3055\u308c\u305f\u304b\u3001\u30d5\u30a1\u30a4\u30eb\u30c0\u30a4\u30a2\u30ed\u30b0\u3092\u958b\u3051\u307e\u305b\u3093\u3067\u3057\u305f\u3002"]);
  });
}

function enableUstVisibility() {
  document.querySelector("#ust-rewrite")?.addEventListener("change", () => {
    markPreviewStale();
    syncUstListForPreview();
    syncInformationDetailWindow();
  });
  document.querySelector("#ust-folder")?.addEventListener("input", () => {
    scheduleUstSearch();
    syncUstListForPreview();
    syncInformationDetailWindow();
  });
}

function enablePreviewStaleTracking() {
  const left = document.querySelector(".left");
  if (!left) return;
  left.addEventListener("input", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLElement)) return;
    if (target.closest(".ust-list")) return;
    if (target.id === "ust-folder") return;
    markPreviewStale();
  });
  left.addEventListener("change", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLElement)) return;
    if (target.closest(".ust-list")) return;
    if (target.id === "ust-folder" || target.id === "ust-rewrite") return;
    markPreviewStale();
  });
  left.addEventListener("click", (event) => {
    const target = event.target;
    if (target instanceof HTMLElement && target.closest(".sort-chip")) markPreviewStale();
  });
}

async function playAudio(payload, button) {
  const targetButton = button || document.querySelector("#play");
  const playing = targetButton?.dataset.playing === "true";
  if (playing) {
    await callApi("stop_audio", {});
    if (currentAudioTimer) window.clearTimeout(currentAudioTimer);
    targetButton.dataset.playing = "false";
    targetButton.textContent = "\u25b6";
    currentAudioButton = null;
    return;
  }
  if (currentAudioButton && currentAudioButton !== targetButton) {
    currentAudioButton.dataset.playing = "false";
    currentAudioButton.textContent = "\u25b6";
  }
  const result = await callApi("play_audio", payload);
  if (!result.ok) {
    renderLog([result.message || "audio playback failed"]);
    return;
  }
  targetButton.dataset.playing = "true";
  targetButton.textContent = "\u25a0";
  currentAudioButton = targetButton;
  if (currentAudioTimer) window.clearTimeout(currentAudioTimer);
  if (result.duration_ms > 0) {
    currentAudioTimer = window.setTimeout(() => {
      targetButton.dataset.playing = "false";
      targetButton.textContent = "\u25b6";
      if (currentAudioButton === targetButton) currentAudioButton = null;
      currentAudioTimer = null;
    }, result.duration_ms + 120);
  }
}

function enablePlayToggle() {
  const button = document.querySelector("#play");
  button.addEventListener("click", () => {
    const payload = { voice_dir: document.querySelector("#voice-folder")?.value || "" };
    if (selectedPreviewRow?.old_wav_exists !== false && selectedPreviewRow?.old_wav) {
      payload.wav_name = selectedPreviewRow.old_wav;
    }
    playAudio(payload, button);
  });
}

function updateInformationPlayButton() {
  const button = document.querySelector("#play");
  if (!button) return;
  button.hidden = Boolean(selectedPreviewRow && selectedPreviewRow.old_wav_exists === false);
}

function enableSortOrderControls() {
  const button = document.querySelector("#sort-order-button");
  const actions = document.querySelector("#sort-order-actions");
  const ok = document.querySelector("#sort-order-ok");
  const cancel = document.querySelector("#sort-order-cancel");
  button?.addEventListener("click", () => {
    sortCancelSnapshot = previewRowsSnapshot();
    sortCancelCanApply = !document.querySelector("#apply-button")?.disabled;
    manualSortActive = true;
    button.hidden = true;
    actions.hidden = false;
    setManualSortMode(true);
    updateApplyState(false);
  });
  ok?.addEventListener("click", () => {
    manualSortActive = false;
    button.hidden = false;
    actions.hidden = true;
    setManualSortMode(false);
    validateEditedRows(rowsForApply());
  });
  cancel?.addEventListener("click", () => {
    const settings = collectSettings();
    button.hidden = false;
    actions.hidden = true;
    setManualSortMode(false);
    renderPreviewRows(sortCancelSnapshot || [], {
      wavEditMode: settings.wav_edit_mode,
      aliasEditMode: settings.alias_edit_mode,
    });
    sortCancelSnapshot = null;
    updateApplyState(sortCancelCanApply);
    sortCancelCanApply = false;
  });
}

function enablePreviewDeselect() {
  document.addEventListener("click", (event) => {
    if (event.target.closest("#preview") || event.target.closest(".preview-head") || event.target.closest(".info")) return;
    clearSelection();
  });
}

function applyColorScheme(name) {
  const normalized = name === "dark" ? "defoko_dark" : name === "light" ? "defoko_light" : name;
  const scheme = colorSchemes.find((item) => item.name === normalized);
  const base = colorSchemes.find((item) => item.name === "defoko_dark")?.colors || {};
  const colors = { ...base, ...(scheme?.colors || {}) };
  Object.entries(colors).forEach(([key, value]) => {
    document.documentElement.style.setProperty(`--${key}`, String(value));
  });
}

async function initializeColorSchemes() {
  try {
    colorSchemes = await callApi("get_color_schemes", {});
  } catch (_error) {
    colorSchemes = [];
  }
  const settings = collectSettings();
  applyColorScheme(settings.theme);
}

function ensureSettingsBlocker() {
  if (document.querySelector("#settings-window-blocker")) return;

  const blocker = document.createElement("div");
  blocker.id = "settings-window-blocker";
  blocker.setAttribute("aria-hidden", "true");

  blocker.addEventListener("click", async (event) => {
    event.preventDefault();
    event.stopPropagation();

    try {
      await callApi("raise_related_windows", {});
    } catch (_) {
      // 設定ウィンドウを前面に戻せなくても、メイン操作は止める
    }
  });

  document.body.appendChild(blocker);
}
function enableSettingsButton() {
  document.querySelector("#settings-button").addEventListener("click", async () => {
    try {
      await callApi("open_settings_window", { current_scale: document.querySelector("#ui-scale")?.value || "1" });
    } catch (error) {
      renderLog([error.message || String(error)]);
    }
  });
}

function enableInformationDetailButton() {
  document.addEventListener("click", (event) => {
    if (!event.target.closest("#information-detail-button")) return;
    event.preventDefault();
    openInformationDetailWindow();
  });
}

async function initializePersistedSettings() {
  try {
    const settings = await callApi("get_settings", {});
    applySettingsToDom(settings || {});
  } catch (_error) {
    // Static fallback keeps the in-DOM defaults.
  }
}

async function initializePluginContext() {
  try {
    const context = await callApi("get_plugin_context", {});
    if (!context?.active || !context.voice_dir) return;
    document.querySelector("#voice-folder").value = context.voice_dir;
    updateApplyState(false);
    renderLog([
      `UTAU plugin temp: ${context.temp_path}`,
      `VoiceDir: ${context.voice_dir}`,
      `selected notes: ${context.note_count || 0}`,
    ]);
  } catch (_error) {
    // Normal desktop launch has no plugin context.
  }
}

window.applyAliaScaleSettings = async (settings = {}) => {
  const previousSettings = collectSettings();
  const previousScale = currentUiScale();
  applySettingsToDom(settings);
  await nextFrame();
  const nextSettings = collectSettings();
  const hasPreviewAffectingSettingChange = settingsChanged(
    previousSettings,
    nextSettings,
    previewAffectingSettingKeys,
  );
  syncUstListForPreview();
  if (previousSettings.show_full_ust_path !== nextSettings.show_full_ust_path && lastUstItems.length) {
    renderUstList(lastUstItems);
  }
  if (
    previousSettings.strict_voice_match !== nextSettings.strict_voice_match
    || previousSettings.utau_exe_path !== nextSettings.utau_exe_path
  ) {
    scheduleUstSearch();
  }
  if (hasPreviewAffectingSettingChange) {
    markPreviewStale();
  }
  if (rowsForApply().length && !manualSortActive) {
    await validateEditedRows(rowsForApply());
  } else {
    const hasBlockingDanger = rowsForApply().some((row) => row.severity === "danger") && nextSettings.block_on_danger;
    updateApplyState(rowsForApply().length > 0 && !manualSortActive && !hasBlockingDanger);
    syncInformationDetailWindow();
  }
  const nextScale = currentUiScale();
  const scaleChanged = Math.abs(previousScale - nextScale) > 0.001;
  if (!scaleChanged) return;
  setPreviewPaneVisible(previewPaneVisible);
  await syncWindowForPreviewState({
    resizeToTarget: !previewPaneVisible,
    resizeBelowMinimumOnly: previewPaneVisible,
    hasPreviewRows: previewPaneVisible || rowsForApply().length > 0,
    showPreviewPane: previewPaneVisible,
    refreshCompactHeight: true,
  });
};
window.measureAliaScaleMinimumViewport = measureMinimumViewportForCurrentState;
window.applyAliaScaleTheme = applyColorScheme;

document.querySelector("#preview-button").addEventListener("click", runPreview);
document.querySelector("#apply-button").addEventListener("click", runApply);
enableBrowseButtons();
enableUstVisibility();
enablePreviewStaleTracking();
enableDragSort();
enablePreviewColumnResize();
setRowsChangedCallback(validateEditedRows);
setRowSelectedCallback((row) => {
  renderRowInformation(row);
  syncInformationDetailWindow();
});
setRowPlayCallback((row, button) => {
  playAudio({
    voice_dir: document.querySelector("#voice-folder")?.value || "",
    wav_name: row.old_wav,
    start_ms: row.play_start_ms || 0,
    end_ms: row.play_end_ms || 0,
  }, button);
});
enablePlayToggle();
ensureSettingsBlocker();
enableSettingsButton();
enableInformationDetailButton();
enableSortOrderControls();
enablePreviewDeselect();
enableRoundingControls();
enableExcludePlaceholder();
enableModeOptions();
enableRuleScopeControls();
enableSortChips();
enableAdvancedSettings();
enableTooltips();
await initializePersistedSettings();
await initializeColorSchemes();
setPreviewPaneVisible(false);
await syncWindowForPreviewState({
  resizeToTarget: true,
  hasPreviewRows: false,
  showPreviewPane: false,
  refreshCompactHeight: true,
});
renderLog(["Ready."]);
await initializePluginContext();
renderVoiceInformation(lastInformation || {});
syncUstListForPreview();
    manualSortActive = false;
