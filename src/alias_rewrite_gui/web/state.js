const qs = (selector) => document.querySelector(selector);
let storedSettings = {};

function value(selector, fallback = "") {
  const element = qs(selector);
  return element ? element.value : fallback;
}

function checked(selector, fallback = false) {
  const element = qs(selector);
  return element ? element.checked : fallback;
}

function csvList(text) {
  return String(text || "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

function checkedValues(selector) {
  return Array.from(document.querySelectorAll(selector))
    .filter((element) => element.checked)
    .map((element) => element.value);
}

function radioValue(name, fallback = "") {
  const datasetKey = name === "edit-scope" ? "editScope" : name === "rule-scope" ? "ruleScope" : "";
  const radios = Array.from(document.querySelectorAll(`input[name="${name}"]`));
  const stored = datasetKey ? document.documentElement.dataset[datasetKey] : "";
  const storedRadio = stored ? radios.find((radio) => radio.value === stored) : null;
  if (storedRadio) {
    radios.forEach((radio) => {
      radio.checked = radio === storedRadio;
    });
    return storedRadio.value;
  }
  const checkedRadio = radios.find((radio) => radio.checked);
  if (checkedRadio) {
    if (datasetKey) document.documentElement.dataset[datasetKey] = checkedRadio.value;
    return checkedRadio.value;
  }
  const fallbackRadio = radios.find((radio) => radio.value === fallback) || radios[0];
  if (fallbackRadio) {
    radios.forEach((radio) => {
      radio.checked = radio === fallbackRadio;
    });
    if (datasetKey) document.documentElement.dataset[datasetKey] = fallbackRadio.value;
    return fallbackRadio.value;
  }
  return fallback;
}

function selectedUstPaths() {
  return Array.from(document.querySelectorAll(".ust-list input[type='checkbox']:checked"))
    .map((input) => input.dataset.path)
    .filter(Boolean);
}

function otoPathFromVoice(voiceDir) {
  if (!voiceDir) return "";
  const sep = voiceDir.includes("\\") ? "\\" : "/";
  return `${voiceDir.replace(/[\\/]$/, "")}${sep}oto.ini`;
}

function pathFromVoice(voiceDir, filename) {
  if (!voiceDir) return "";
  const sep = voiceDir.includes("\\") ? "\\" : "/";
  return `${voiceDir.replace(/[\\/]$/, "")}${sep}${filename}`;
}

export function collectSort() {
  return Array.from(document.querySelectorAll(".sort-chip.active"))
    .map((chip) => ({
      key: chip.dataset.key,
      direction: chip.dataset.direction === "desc" ? "desc" : "asc",
      rank: Number.parseInt(chip.querySelector(".rank")?.textContent || "999", 10),
    }))
    .sort((a, b) => a.rank - b.rank)
    .map(({ key, direction }) => ({ key, direction }));
}

function collectReplacementRules() {
  return Array.from(document.querySelectorAll(".replacement-rule"))
    .map((row) => ({
      old: row.querySelector("[data-replace='old']")?.value || "",
      new: row.querySelector("[data-replace='new']")?.value || "",
      target: row.querySelector("[data-replace='target']")?.value || "alias",
      use_regex: row.querySelector("[data-replace='regex']")?.checked || false,
    }))
    .filter((rule) => rule.old || rule.new);
}

export function collectSettings() {
  return {
    backup: checked("#backup-enabled", storedSettings.backup ?? true),
    backup_mode: document.querySelector('input[name="backup"]:checked')?.value || storedSettings.backup_mode || "voice_dir",
    backup_root: "backup",
    backup_max_count_enabled: checked("#backup-limit-enabled", storedSettings.backup_max_count_enabled ?? false),
    backup_max_count: Number.parseInt(value("#backup-max-count", String(storedSettings.backup_max_count || 30)), 10) || 30,
    write_csv: checked("#write-csv", storedSettings.write_csv ?? true),
    merge_csv: checked("#csv-merge", storedSettings.merge_csv ?? true),
    csv_path: value("#csv-output-file", storedSettings.csv_path || ""),
    update_ust: checked("#ust-rewrite", storedSettings.update_ust ?? false),
    show_full_ust_path: checked("#ust-full-path", storedSettings.show_full_ust_path ?? false),
    strict_voice_match: checked("#strict-voice-match", storedSettings.strict_voice_match ?? false),
    utau_exe_path: value("#utau-exe-path", storedSettings.utau_exe_path || ""),
    rename_files: true,
    wav_edit_mode: value("#wav-edit-mode", storedSettings.wav_edit_mode || "allow"),
    alias_edit_mode: value("#alias-edit-mode", storedSettings.alias_edit_mode || "allow"),
    block_on_danger: !checked("#ignore-danger-warnings", !(storedSettings.block_on_danger ?? true)),
    relax_cannotcall_for_unused_ust_entries: checked("#relax-unused-cannotcall", storedSettings.relax_cannotcall_for_unused_ust_entries ?? false),
    theme: value("#theme-mode", storedSettings.theme || "defoko_dark"),
    ui_scale: Number.parseFloat(value("#ui-scale", String(storedSettings.ui_scale || 1))) || 1,
    excluded_call_key_moras: csvList(value("#excluded-call-key-moras", csvText(storedSettings.excluded_call_key_moras || "_"))),
    auto_wav_excluded_moras: csvList(value("#excluded-call-key-moras", csvText(storedSettings.excluded_call_key_moras || "_"))),
    numbering_order_mode: value("#numbering-order-mode", storedSettings.numbering_order_mode || (storedSettings.number_alias_before_wav ? "alias_wav" : "separate")),
    number_alias_before_wav: value("#numbering-order-mode", storedSettings.numbering_order_mode || (storedSettings.number_alias_before_wav ? "alias_wav" : "separate")) === "alias_wav",
    renumber_after_order_change: checked("#renumber-after-order-change", storedSettings.renumber_after_order_change ?? true),
    write_debug_log: checked("#write-debug-log", storedSettings.write_debug_log ?? true),
    related_file_patterns: csvList(value("#related-file-patterns", csvText(storedSettings.related_file_patterns || "{stem}_wav.frq, {stem}.wav.llsm, {stem}_wav.pmk, {stem}*.hifi.npz"))),
  };
}

function setChecked(selector, value) {
  const element = qs(selector);
  if (element) element.checked = Boolean(value);
}

function setValue(selector, value) {
  const element = qs(selector);
  if (element) element.value = value ?? "";
}

function csvText(value) {
  if (Array.isArray(value)) return value.join(", ");
  return value ?? "";
}

function normalizeEditMode(value, fallback = "allow") {
  if (value === "representative" || value === "empty_alias_only") return "allow";
  return value || fallback;
}

export function applySettingsToDom(settings = {}) {
  storedSettings = { ...storedSettings, ...settings };
  setChecked("#backup-enabled", settings.backup ?? true);
  const backupMode = settings.backup_mode || "voice_dir";
  const backupRadio = document.querySelector(`input[name="backup"][value="${backupMode}"]`);
  if (backupRadio) backupRadio.checked = true;
  setChecked("#backup-limit-enabled", settings.backup_max_count_enabled ?? false);
  setValue("#backup-max-count", settings.backup_max_count ?? 30);
  setChecked("#write-csv", settings.write_csv ?? true);
  setChecked("#csv-merge", settings.merge_csv ?? true);
  setValue("#csv-output-file", settings.csv_path || "");
  setChecked("#ust-rewrite", settings.update_ust ?? false);
  setChecked("#ust-full-path", settings.show_full_ust_path ?? false);
  setChecked("#strict-voice-match", settings.strict_voice_match ?? false);
  setValue("#utau-exe-path", settings.utau_exe_path || "");
  setChecked("#rename-files", settings.rename_files ?? true);
  setValue("#wav-edit-mode", normalizeEditMode(settings.wav_edit_mode));
  setValue("#alias-edit-mode", settings.alias_edit_mode || "allow");
  setChecked("#ignore-danger-warnings", !(settings.block_on_danger ?? true));
  setChecked("#relax-unused-cannotcall", settings.relax_cannotcall_for_unused_ust_entries ?? false);
  setChecked("#write-debug-log", settings.write_debug_log ?? true);
  setValue("#theme-mode", settings.theme || "defoko_dark");
  setValue("#ui-scale", String(settings.ui_scale || 1));
  setValue("#excluded-call-key-moras", csvText(settings.excluded_call_key_moras || "_"));
  setValue("#numbering-order-mode", settings.numbering_order_mode || (settings.number_alias_before_wav ? "alias_wav" : "separate"));
  setChecked("#renumber-after-order-change", settings.renumber_after_order_change ?? true);
  setValue("#related-file-patterns", csvText(settings.related_file_patterns || "{stem}_wav.frq, {stem}.wav.llsm, {stem}_wav.pmk, {stem}*.hifi.npz"));
  document.documentElement.style.setProperty("--ui-scale", String(settings.ui_scale || 1));
  window.applyAliaScaleTheme?.(settings.theme || "defoko_dark");
  document
    .querySelectorAll("#wav-edit-mode, #alias-edit-mode, #ui-scale")
    .forEach((element) => element.dispatchEvent(new Event("change", { bubbles: true })));
}

export function collectRewriteSettings() {
  const mode = value("#process-mode", "pitch_append");
  const usesCommonRewriteOptions = mode === "pitch_append" || mode === "numbering" || mode === "none";
  const usesPitchOptions = mode === "pitch_append" || mode === "rule_based";
  const usesNumberingOptions = mode === "numbering";
  const usesFirstNumberOption = mode === "numbering" || mode === "rule_based";
  const usesReplacementOptions = mode === "replace";
  const usesCsvOptions = mode === "csv";
  const roundingModeSelector = mode === "rule_based" ? "#rule-rounding-mode" : "#rounding-mode";
  const roundingCandidatesSelector = mode === "rule_based" ? "#rule-rounding-candidates" : "#rounding-candidates";
  const separatorValue = usesNumberingOptions
    ? value("#separator-numbering", "_")
    : mode === "pitch_append"
      ? value("#separator", "_")
      : "_";
  return {
    mode,
    frequency_source: value("#frequency-source", "mrq"),
    separator: separatorValue,
    strip_suffix: usesCommonRewriteOptions ? checked("#strip-suffix", true) : false,
    keep_prefix: usesCommonRewriteOptions ? !checked("#strip-prefix", false) : true,
    missing_pitch: "keep",
    edit_scope: usesCommonRewriteOptions ? radioValue("edit-scope", "call_key") : "alias_wav",
    alias_target: usesCommonRewriteOptions ? radioValue("edit-scope", "call_key") : "call_key",
    add_alias_for_unused_wav: usesCommonRewriteOptions ? checked("#add-alias-unused", false) : false,
    edit_mismatched_wav_mora: false,
    number_first_alias: usesFirstNumberOption ? checked("#number-first-alias", false) : false,
    rounding_mode: usesPitchOptions ? value(roundingModeSelector, "semitone") : "semitone",
    rounding_candidates: usesPitchOptions ? csvList(value(roundingCandidatesSelector)) : [],
    replacement_rules: usesReplacementOptions ? collectReplacementRules() : [],
    csv_invert: usesCsvOptions ? checked("#csv-invert", false) : false,
    csv_read_columns: usesCsvOptions ? checkedValues('input[name="csv-read-column"]') : ["new_wav", "new_alias", "new_order_id"],
    rule_scope: mode === "rule_based" ? radioValue("rule-scope", "call_key") : "alias_wav",
    rule_alias_template: mode === "rule_based" ? value("#rule-alias-template") : "",
    rule_wav_template: mode === "rule_based" ? value("#rule-wav-template") : "",
    rule_call_key_template: mode === "rule_based" ? value("#rule-call-key-template") : "",
    exclude: {
      exclude_unvoiced: false,
      exclude_no_f0: usesPitchOptions ? checked("#exclude-no-f0", false) : false,
      exclude_no_freq_src: usesPitchOptions ? checked("#exclude-no-freq-src", false) : false,
      exclude_empty_params: checked("#exclude-empty-params", true),
      mode: value("#exclude-mode", "none"),
      patterns: csvList(value("#exclude-pattern")),
    },
    sort: collectSort(),
    wav_edit_mode: value("#wav-edit-mode", "allow"),
    alias_edit_mode: value("#alias-edit-mode", "allow"),
    prefix_underscore_for_new_alias: usesCommonRewriteOptions ? checked("#prefix-underscore", false) : false,
  };
}

export function collectPreviewRequest() {
  const voiceDir = value("#voice-folder");
  const frequencySource = value("#frequency-source", "mrq");
  return {
    voice_dir: voiceDir,
    oto_path: otoPathFromVoice(voiceDir),
    mrq_path: frequencySource === "mrq" ? value("#mrq-path") || pathFromVoice(voiceDir, "desc.mrq") : "",
    frequency_source: frequencySource,
    csv_path: value("#csv-path"),
    ust_root: value("#ust-folder"),
    selected_ust_paths: selectedUstPaths(),
    ust_selection_known: Boolean(document.querySelectorAll(".ust-list input[type='checkbox']").length),
    rewrite: collectRewriteSettings(),
    settings: collectSettings(),
  };
}

export function collectApplyRequest(rows) {
  const preview = collectPreviewRequest();
  return {
    voice_dir: preview.voice_dir,
    oto_path: preview.oto_path,
    mrq_path: preview.mrq_path,
    frequency_source: preview.frequency_source,
    rows,
    ust_root: preview.ust_root,
    selected_ust_paths: selectedUstPaths(),
    rewrite: preview.rewrite,
    settings: preview.settings,
  };
}
