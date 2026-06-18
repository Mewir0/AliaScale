import { escapeAttr, escapeHtml } from "./dom_utils.js";

let currentRows = [];
let dragging = null;
let sortMode = false;
let rowsChangedCallback = null;
let rowSelectedCallback = null;
let rowPlayCallback = null;

export function setRowsChangedCallback(callback) {
  rowsChangedCallback = callback;
}

export function setRowSelectedCallback(callback) {
  rowSelectedCallback = callback;
}

export function setRowPlayCallback(callback) {
  rowPlayCallback = callback;
}

export function rowsForApply() {
  return currentRows.map((row, index) => stripClientOnlyFields({ ...row, new_order_id: index + 1 }));
}

export function previewRowsSnapshot() {
  return currentRows.map((row) => ({ ...row }));
}

export function setManualSortMode(enabled) {
  sortMode = Boolean(enabled);
  document.querySelector(".table-wrap")?.classList.toggle("sort-mode", sortMode);
  renderPreviewRows(currentRows, currentEditModes);
}

export function clearSelection() {
  document.querySelectorAll("#preview tbody tr").forEach((rowElement) => rowElement.classList.remove("selected-row"));
  rowSelectedCallback?.(null);
}

let currentEditModes = {};

function singleLine(value) {
  return String(value ?? "").replace(/[\r\n]+/g, " ");
}

function wavEditText(value) {
  return String(value || "").replace(/\.wav$/i, "");
}

function wavInternalText(value) {
  const text = singleLine(value).trim();
  if (!text) return "";
  return /\.wav$/i.test(text) ? text : `${text}.wav`;
}

function stripClientOnlyFields(row) {
  const { _selected, ...rest } = row;
  return { ...rest };
}

function definedText(...values) {
  for (const value of values) {
    if (value !== undefined && value !== null) return String(value);
  }
  return "";
}

function rowDisplayOrder(row, fallbackIndex) {
  const order = Number.parseInt(row?.new_order_id ?? row?.old_order_id ?? row?.line_number ?? fallbackIndex + 1, 10);
  return Number.isFinite(order) ? order : fallbackIndex + 1;
}

function rowsInDisplayOrder(rows) {
  return rows
    .map((row, index) => ({ row, index }))
    .sort((left, right) => {
      const orderDelta = rowDisplayOrder(left.row, left.index) - rowDisplayOrder(right.row, right.index);
      if (orderDelta) return orderDelta;
      return left.index - right.index;
    })
    .map((item) => item.row);
}

function fieldControl(tr, field) {
  const cell = tr.querySelector(`[data-field='${field}']`);
  return cell?.querySelector("input") || cell;
}

function setFieldText(cell, value) {
  const input = cell?.querySelector?.("input");
  if (input) input.value = value;
  else if (cell) cell.textContent = value;
}

function syncRowFromCells(tr, row) {
  const newWavControl = fieldControl(tr, "new_wav");
  const newAliasControl = fieldControl(tr, "new_alias");
  const newWav = wavInternalText(newWavControl?.value ?? newWavControl?.textContent ?? row.new_wav);
  const newAlias = singleLine(newAliasControl?.value ?? newAliasControl?.textContent ?? row.new_alias);
  row.new_wav = newWav;
  row.new_alias = newAlias;
  row.changed = row.old_wav !== newWav || row.old_alias !== newAlias;
}

function commitWavInput(input, tr, row) {
  const newWav = wavInternalText(input.value);
  row.new_wav = newWav;
  const newAliasControl = fieldControl(tr, "new_alias");
  row.new_alias = singleLine(newAliasControl?.value ?? newAliasControl?.textContent ?? row.new_alias);
  row.changed = row.old_wav !== row.new_wav || row.old_alias !== row.new_alias;
  input.value = newWav;
  return newWav;
}

function syncSameOldWav(row, newWav) {
  currentRows.forEach((item) => {
    if (item.old_wav !== row.old_wav) return;
    item.new_wav = newWav;
    item.changed = item.old_wav !== newWav || item.old_alias !== item.new_alias;
  });
  document.querySelectorAll("#preview tbody tr").forEach((tr) => {
    const item = currentRows[Number.parseInt(tr.dataset.index, 10)];
    if (!item || item.old_wav !== row.old_wav) return;
    const cell = tr.querySelector("[data-field='new_wav']");
    setFieldText(cell, newWav);
  });
}

function requestRowsValidation() {
  if (rowsChangedCallback) rowsChangedCallback(currentRows.map((row) => ({ ...row })));
}

function rowClass(row) {
  const classes = [];
  if (row.changed) classes.push("changed-row");
  if (isManualEdited(row)) classes.push("manual-edited-row");
  if (row.reason === "excluded" || row.reason === "excluded_unvoiced" || row.status === "exclude" || row.origin_status === "exclude") classes.push("excluded-row");
  if (row.severity === "warning") classes.push("warning-row");
  if (row.severity === "danger") classes.push("danger-row");
  return classes.join(" ");
}

function applyWarningCellClasses(tr, row) {
  const level = row.severity === "danger" ? "cell-danger" : row.severity === "warning" ? "cell-warning" : "";
  if (!level) return;
  (row.warning_cells || []).forEach((field) => {
    const cell = tr.querySelector(`[data-field='${field}']`);
    if (cell) cell.classList.add(level);
  });
}

function displayStatus(row) {
  if (row.severity && row.severity !== "ok") return statusLabel(row.status || row.severity);
  if (row.reason === "excluded" || row.status === "exclude") return statusLabel("exclude");
  if (row.status === "manual" || row.origin_status === "manual") return statusLabel("manual");
  if (isManualEdited(row)) return statusLabel("manual");
  if (row.origin_status && row.origin_status !== "manual") {
    const origin = row.origin_status === "window" || row.origin_status === "system_fallback" ? "system" : row.origin_status;
    if (origin === "system" && !row.changed && !(row.diagnostics || []).length) return "";
    return statusLabel(origin);
  }
  const status = row.status || "";
  const labels = {
    window: "system",
    system: "system",
    system_fallback: "system",
    fallback_full_wav: "no_f0",
    no_valid_f0: "no_f0",
    no_freq: "no_f0",
    no_f0: "no_f0",
    no_freq_src: "no_freq_src",
    missing_mrq_record: "no_freq_src",
    missing_frequency: "no_freq_src",
    invalid_freq: "invalid_freq",
  };
  const normalized = labels[status] || status;
  if (normalized === "system" && !row.changed && !(row.diagnostics || []).length) return "";
  return statusLabel(normalized);
}

function statusLabel(status) {
  const labels = {
    system: "自動",
    manual: "手動",
    exclude: "除外",
    empty: "数値設定なし",
    no_freq: "無声音",
    no_freq_src: "周波数表なし",
    invalid_freq: "周波数表不正",
    no_f0: "無声音",
    duplication: "重複",
    changed_mora: "発音変化",
    external_file_conflict: "退避",
    cannotcall: "呼び出し不可",
    unused_cannotcall: "UST未使用",
    old_wav_split: "wav分裂",
    wav_conflict: "wav衝突",
    invalid_wav_name: "wav名不正",
    warning: "警告",
    danger: "危険",
    ok: "",
    matched:"適用"
  };
  return labels[status] ?? status;
}

function editableCell(field, value, editable, className) {
  if (!editable) {
    return `<td data-field="${field}" class="${className}">${escapeHtml(value || "")}</td>`;
  }
  return `<td data-field="${field}" class="${className}"><input class="cell-input" value="${escapeAttr(value || "")}" /></td>`;
}

export function renderPreviewRows(rows, editModes = {}) {
  currentEditModes = editModes;
  const previousByLine = new Map(currentRows.map((row) => [row.line_number, row]));
  currentRows = rowsInDisplayOrder(rows).map((row) => {
    const previous = previousByLine.get(row.line_number);
    return {
      ...row,
      auto_new_wav: definedText(row.auto_new_wav, previous?.auto_new_wav, row.new_wav),
      auto_new_alias: definedText(row.auto_new_alias, previous?.auto_new_alias, row.new_alias),
      new_wav: singleLine(row.new_wav || ""),
      new_alias: singleLine(row.new_alias || ""),
    };
  });
  const tbody = document.querySelector("#preview tbody");
  tbody.textContent = "";
  const wavEditable = editModes.wavEditMode !== "disabled";
  const aliasEditable = editModes.aliasEditMode !== "disabled";
  currentRows.forEach((row, index) => {
    const tr = document.createElement("tr");
    tr.dataset.index = String(index);
    tr.draggable = sortMode;
    tr.className = rowClass(row);
    const opHtml = sortMode
      ? `<button class="row-handle" type="button" aria-label="行を並べ替え">&#9776;</button>`
      : row.old_wav_exists === false
        ? ""
        : `<button class="row-play" type="button" aria-label="行を再生">\u25b6</button>`;
    const newWavClass = [
      wavEditable ? "" : "readonly-cell",
      row.new_wav !== row.auto_new_wav ? "manual-edit-cell" : "",
      isAutoEditedField(row, "new_wav") ? "auto-edit-cell" : "",
    ].filter(Boolean).join(" ");
    const newAliasClass = [
      aliasEditable ? "" : "readonly-cell",
      row.new_alias !== row.auto_new_alias ? "manual-edit-cell" : "",
      isAutoEditedField(row, "new_alias") ? "auto-edit-cell" : "",
    ].filter(Boolean).join(" ");
    tr.innerHTML = `
      <td class="op">${opHtml}</td>
      <td>${escapeHtml(row.old_wav || "")}</td>
      ${editableCell("new_wav", row.new_wav, wavEditable, newWavClass)}
      <td>${escapeHtml(row.old_alias || "")}</td>
      ${editableCell("new_alias", row.new_alias, aliasEditable, newAliasClass)}
      <td>${escapeHtml(displayStatus(row))}</td>
    `;
    applyWarningCellClasses(tr, row);
    tr.addEventListener("click", () => {
      tbody.querySelectorAll("tr").forEach((rowElement) => rowElement.classList.remove("selected-row"));
      tr.classList.add("selected-row");
      const item = currentRows[Number.parseInt(tr.dataset.index, 10)] || row;
      rowSelectedCallback?.({ ...item });
    });
    tr.querySelector(".row-play")?.addEventListener("click", (event) => {
      event.stopPropagation();
      rowPlayCallback?.({ ...row }, event.currentTarget);
    });
    tr.addEventListener("input", () => syncRowFromCells(tr, row));
    tr.querySelectorAll(".cell-input").forEach((input) => {
      input.addEventListener("focus", () => {
        if (input.closest("[data-field='new_wav']")) {
          input.value = wavEditText(input.value);
        }
      });
      input.addEventListener("keydown", (event) => {
        if (event.key === "Enter") {
          event.preventDefault();
          input.blur();
        }
      });
      input.addEventListener("paste", (event) => {
        event.preventDefault();
        const text = singleLine(event.clipboardData?.getData("text/plain") || "");
        const start = input.selectionStart ?? input.value.length;
        const end = input.selectionEnd ?? start;
        input.value = input.value.slice(0, start) + text + input.value.slice(end);
        input.setSelectionRange(start + text.length, start + text.length);
        input.dispatchEvent(new Event("input", { bubbles: true }));
      });
    });
    tr.querySelector("[data-field='new_wav'] input")?.addEventListener("blur", () => {
      const input = tr.querySelector("[data-field='new_wav'] input");
      const newWav = input ? commitWavInput(input, tr, row) : row.new_wav;
      syncSameOldWav(row, newWav);
      requestRowsValidation();
    });
    tr.querySelector("[data-field='new_alias'] input")?.addEventListener("blur", () => {
      syncRowFromCells(tr, row);
      requestRowsValidation();
    });
    tbody.appendChild(tr);
  });
}

export function enableDragSort() {
  const tbody = document.querySelector("#preview tbody");
  tbody.addEventListener("dragstart", (event) => {
    if (!sortMode) {
      event.preventDefault();
      return;
    }
    dragging = event.target.closest("tr");
    dragging?.classList.add("dragging");
  });
  tbody.addEventListener("dragend", () => {
    if (!sortMode) return;
    dragging?.classList.remove("dragging");
    dragging = null;
    currentRows = Array.from(tbody.querySelectorAll("tr")).map((tr, index) => {
      const row = currentRows[Number.parseInt(tr.dataset.index, 10)];
      row.new_order_id = index + 1;
      tr.dataset.index = String(index);
      return row;
    });
    requestRowsValidation();
  });
  tbody.addEventListener("dragover", (event) => {
    if (!sortMode) return;
    event.preventDefault();
    const target = event.target.closest("tr");
    if (!dragging || !target || dragging === target) return;
    const rect = target.getBoundingClientRect();
    const after = event.clientY > rect.top + rect.height / 2;
    tbody.insertBefore(dragging, after ? target.nextSibling : target);
  });
}

function isManualEdited(row) {
  return row.new_wav !== definedText(row.auto_new_wav, row.new_wav) || row.new_alias !== definedText(row.auto_new_alias, row.new_alias);
}

function isAutoEditedField(row, field) {
  if (field === "new_wav") {
    return row.new_wav !== row.old_wav && row.new_wav === definedText(row.auto_new_wav, row.new_wav);
  }
  if (field === "new_alias") {
    return row.new_alias !== row.old_alias && row.new_alias === definedText(row.auto_new_alias, row.new_alias);
  }
  return false;
}
