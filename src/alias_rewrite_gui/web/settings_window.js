function closeWindow() {
  if (window.pywebview?.api?.cancel_settings) {
    window.pywebview.api.cancel_settings();
    return;
  }
  window.close();
}

function csvText(value) {
  if (Array.isArray(value)) return value.join(", ");
  return value || "";
}

function setChecked(selector, value) {
  const element = document.querySelector(selector);
  if (element) element.checked = Boolean(value);
}

function setValue(selector, value) {
  const element = document.querySelector(selector);
  if (element) element.value = value ?? "";
}

function normalizeEditMode(value, fallback = "allow") {
  if (value === "representative" || value === "empty_alias_only") return "allow";
  return value || fallback;
}

let colorSchemes = [];

function applyColorScheme(name) {
  const normalized = name === "dark" ? "defoko_dark" : name === "light" ? "defoko_light" : name;
  const scheme = colorSchemes.find((item) => item.name === normalized);
  const base = colorSchemes.find((item) => item.name === "defoko_dark")?.colors || {};
  const colors = { ...base, ...(scheme?.colors || {}) };
  Object.entries(colors).forEach(([key, value]) => {
    document.documentElement.style.setProperty(`--${key}`, String(value));
  });
}

function collectSettings() {
  return {
    backup: document.querySelector("#backup-enabled")?.checked ?? true,
    backup_mode: document.querySelector('input[name="backup"]:checked')?.value || "voice_dir",
    backup_root: "backup",
    backup_max_count_enabled: document.querySelector("#backup-limit-enabled")?.checked ?? false,
    backup_max_count: clampBackupCount(document.querySelector("#backup-max-count")?.value || "30"),
    write_csv: document.querySelector("#write-csv")?.checked ?? true,
    merge_csv: document.querySelector("#csv-merge")?.checked ?? true,
    csv_path: document.querySelector("#csv-output-file")?.value || "",
    show_full_ust_path: document.querySelector("#ust-full-path")?.checked ?? false,
    strict_voice_match: document.querySelector("#strict-voice-match")?.checked ?? false,
    utau_exe_path: document.querySelector("#utau-exe-path")?.value || "",
    rename_files: true,
    wav_edit_mode: document.querySelector("#wav-edit-mode")?.value || "allow",
    alias_edit_mode: document.querySelector("#alias-edit-mode")?.value || "allow",
    block_on_danger: !(document.querySelector("#ignore-danger-warnings")?.checked ?? false),
    relax_cannotcall_for_unused_ust_entries: document.querySelector("#relax-unused-cannotcall")?.checked ?? false,
    write_debug_log: document.querySelector("#write-debug-log")?.checked ?? true,
    theme: document.querySelector("#theme-mode")?.value || "defoko_dark",
    ui_scale: Number.parseFloat(document.querySelector("#ui-scale")?.value || "1") || 1,
    excluded_call_key_moras: csvText(document.querySelector("#excluded-call-key-moras")?.value || "_")
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean),
    numbering_order_mode: document.querySelector("#numbering-order-mode")?.value || "separate",
    renumber_after_order_change: document.querySelector("#renumber-after-order-change")?.checked ?? true,
    related_file_patterns: csvText(document.querySelector("#related-file-patterns")?.value || "{stem}_wav.frq, {stem}.wav.llsm, {stem}_wav.pmk, {stem}*.hifi.npz")
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean),
  };
}

function applySettings(settings = {}) {
  setChecked("#backup-enabled", settings.backup ?? true);
  const backupRadio = document.querySelector(`input[name="backup"][value="${settings.backup_mode || "voice_dir"}"]`);
  if (backupRadio) backupRadio.checked = true;
  setChecked("#backup-limit-enabled", settings.backup_max_count_enabled ?? false);
  setValue("#backup-max-count", settings.backup_max_count ?? 30);
  setChecked("#write-csv", settings.write_csv ?? true);
  setChecked("#csv-merge", settings.merge_csv ?? true);
  setValue("#csv-output-file", settings.csv_path || "");
  setValue("#csv-output-mode", settings.csv_path ? "custom" : "default");
  setChecked("#ust-full-path", settings.show_full_ust_path ?? false);
  setChecked("#strict-voice-match", settings.strict_voice_match ?? false);
  setValue("#utau-exe-path", settings.utau_exe_path || "");
  setValue("#wav-edit-mode", normalizeEditMode(settings.wav_edit_mode));
  setValue("#alias-edit-mode", settings.alias_edit_mode || "allow");
  setChecked("#ignore-danger-warnings", !(settings.block_on_danger ?? true));
  setChecked("#relax-unused-cannotcall", settings.relax_cannotcall_for_unused_ust_entries ?? false);
  setChecked("#write-debug-log", settings.write_debug_log ?? true);
  setValue("#theme-mode", settings.theme || "defoko_dark");
  setValue("#ui-scale", String(settings.ui_scale || 1));
  setValue("#excluded-call-key-moras", csvText(settings.excluded_call_key_moras || "_"));
  setValue("#numbering-order-mode", settings.numbering_order_mode || "separate");
  setChecked("#renumber-after-order-change", settings.renumber_after_order_change ?? true);
  setValue("#related-file-patterns", csvText(settings.related_file_patterns || "{stem}_wav.frq, {stem}.wav.llsm, {stem}_wav.pmk, {stem}*.hifi.npz"));
  document.documentElement.style.setProperty("--ui-scale", String(settings.ui_scale || 1));
  applyColorScheme(settings.theme || "defoko_dark");
  syncCsvFileField();
  syncBackupLimit();
  syncStrictVoiceControls();
}

async function loadSettings() {
  if (!window.pywebview?.api?.get_settings) return;
  try {
    if (!colorSchemes.length) {
      await buildThemeOptions();
    }
    applySettings(await window.pywebview.api.get_settings());
  } catch (_error) {
    // Keep defaults in static preview mode.
  }
}

function clampBackupCount(value) {
  const parsed = Number.parseInt(value, 10);
  if (!Number.isFinite(parsed) || parsed < 1) return 1;
  if (parsed > 999999) return 999999;
  return parsed;
}

function syncBackupLimit() {
  const input = document.querySelector("#backup-max-count");
  if (input) input.value = String(clampBackupCount(input.value));
}

function syncCsvFileField() {
  const mode = document.querySelector("#csv-output-mode")?.value || "";
  const merge = document.querySelector("#csv-merge")?.checked ?? true;
  const input = document.querySelector("#csv-output-file");
  const button = document.querySelector("#csv-output-browse");
  const defaultMode = mode === "default" || mode === "" || mode.includes("既定") || mode.includes("規定");
  if (input) {
    input.disabled = defaultMode;
    input.placeholder = merge
      ? "voice/(音源名)/(音源名).csv に統合"
      : "voice/(音源名)/(音源名)_yymmdd_hhmmss.csv に出力";
  }
  if (button) button.disabled = defaultMode;
}

function syncStrictVoiceControls() {
  const enabled = document.querySelector("#strict-voice-match")?.checked ?? false;
  const input = document.querySelector("#utau-exe-path");
  const button = document.querySelector("#utau-exe-browse");
  if (input) input.disabled = !enabled;
  if (button) button.disabled = !enabled;
}

function enableSettingsActions() {
  const confirm = document.querySelector("#confirm-modal");
  document.querySelector("#settings-ok").addEventListener("click", async () => {
    const settings = collectSettings();
    if (window.pywebview?.api?.apply_settings) {
      await window.pywebview.api.apply_settings(settings);
      return;
    }
    closeWindow();
  });
  document.querySelector("#settings-cancel").addEventListener("click", () => {
    confirm.classList.add("open");
    confirm.setAttribute("aria-hidden", "false");
  });
  document.querySelector("#confirm-ok").addEventListener("click", closeWindow);
  document.querySelector("#confirm-cancel").addEventListener("click", () => {
    confirm.classList.remove("open");
    confirm.setAttribute("aria-hidden", "true");
  });
}

window.requestSettingsCancel = function requestSettingsCancel() {
  const confirm = document.querySelector("#confirm-modal");
  confirm.classList.add("open");
  confirm.setAttribute("aria-hidden", "false");
};

function enableTabs() {
  const tabs = Array.from(document.querySelectorAll(".settings-tab"));
  const panels = Array.from(document.querySelectorAll(".settings-tab-panel"));
  tabs.forEach((tab) => {
    tab.addEventListener("click", () => {
      const name = tab.dataset.tab;
      tabs.forEach((item) => item.classList.toggle("active", item === tab));
      panels.forEach((panel) => panel.classList.toggle("active", panel.dataset.tabPanel === name));
    });
  });
}

function enableDisplayScale() {
  const select = document.querySelector("#ui-scale");
  select.addEventListener("change", () => {
    document.documentElement.style.setProperty("--ui-scale", select.value);
  });
}

async function buildThemeOptions() {
  const select = document.querySelector("#theme-mode");
  if (!select) return;
  try {
    colorSchemes = await window.pywebview?.api?.get_color_schemes?.() || [];
  } catch (_error) {
    colorSchemes = [];
  }
  if (!colorSchemes.length) return;
  const current = select.value || "defoko_dark";
  select.textContent = "";
  colorSchemes.forEach((scheme) => {
    const option = document.createElement("option");
    option.value = scheme.name;
    option.textContent = scheme.display_name || scheme.name;
    select.appendChild(option);
  });
  setValue("#theme-mode", current === "dark" ? "defoko_dark" : current === "light" ? "defoko_light" : current);
  applyColorScheme(select.value);
  select.addEventListener("change", () => applyColorScheme(select.value));
}

function buildScaleOptions() {
  const select = document.querySelector("#ui-scale");
  if (!select || select.children.length) return;
  for (let value = 0.5; value <= 1.5001; value += 0.1) {
    const rounded = Math.round(value * 10) / 10;
    const option = document.createElement("option");
    option.value = String(rounded);
    option.textContent = `${Math.round(rounded * 100)}%`;
    if (rounded === 1) option.selected = true;
    select.appendChild(option);
  }
}

function enableCsvControls() {
  document.querySelector("#csv-merge")?.addEventListener("change", syncCsvFileField);
  document.querySelector("#csv-output-mode")?.addEventListener("change", syncCsvFileField);
  document.querySelector("#csv-output-browse")?.addEventListener("click", async () => {
    if (!window.pywebview?.api?.choose_file) return;
    const selected = await window.pywebview.api.choose_file("csv");
    if (selected) document.querySelector("#csv-output-file").value = selected;
  });
  syncCsvFileField();
}

function enableStrictVoiceControls() {
  document.querySelector("#strict-voice-match")?.addEventListener("change", syncStrictVoiceControls);
  document.querySelector("#utau-exe-browse")?.addEventListener("click", async () => {
    if (!window.pywebview?.api?.choose_file) return;
    const selected = await window.pywebview.api.choose_file("utau_exe");
    if (selected) document.querySelector("#utau-exe-path").value = selected;
  });
  syncStrictVoiceControls();
}

function enableBackupStepper() {
  const input = document.querySelector("#backup-max-count");
  document.querySelector("#backup-count-down")?.addEventListener("click", () => {
    input.value = String(Math.max(1, clampBackupCount(input.value) - 1));
  });
  document.querySelector("#backup-count-up")?.addEventListener("click", () => {
    const current = clampBackupCount(input.value);
    input.value = String(current >= 999999 ? 1 : current + 1);
  });
  input?.addEventListener("change", syncBackupLimit);
}

function enableTooltips() {
  const tip = document.querySelector("#tip-card");
  const scaled = (value) => value * (Number.parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--ui-scale")) || 1);
  document.querySelectorAll("[data-tip]").forEach((element) => {
    element.addEventListener("mouseenter", () => {
      const rect = element.getBoundingClientRect();
      tip.textContent = element.dataset.tip;
      tip.classList.add("open");
      tip.setAttribute("aria-hidden", "false");
      const gap = scaled(6);
      const edge = scaled(8);
      const belowTop = rect.bottom + gap;
      const aboveTop = rect.top - tip.offsetHeight - gap;
      const top = belowTop + tip.offsetHeight > window.innerHeight && aboveTop >= edge
        ? aboveTop
        : Math.min(belowTop, window.innerHeight - tip.offsetHeight - edge);
      const left = Math.min(rect.left, window.innerWidth - tip.offsetWidth - edge);
      tip.style.top = `${Math.max(edge, top)}px`;
      tip.style.left = `${Math.max(edge, left)}px`;
    });
    element.addEventListener("mouseleave", () => {
      tip.classList.remove("open");
      tip.setAttribute("aria-hidden", "true");
    });
  });
}

buildScaleOptions();
buildThemeOptions();
enableTabs();
enableSettingsActions();
enableDisplayScale();
enableCsvControls();
enableStrictVoiceControls();
enableBackupStepper();
enableTooltips();
window.addEventListener("pywebviewready", loadSettings, { once: true });
loadSettings();
