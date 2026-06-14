function syncRoundingInput(mode, input) {
  const locked = mode.value === "semitone" || mode.value === "whole_tone";
  input.disabled = locked;
  if (locked) input.value = "";
  input.placeholder = mode.value === "key" ? "C#,G" : mode.value === "explicit_notes" ? "C#3,G4" : "";
}

function bindRoundingControls(modeSelector, inputSelector) {
  const mode = document.querySelector(modeSelector);
  const input = document.querySelector(inputSelector);
  if (!mode || !input) return () => {};
  function sync() {
    syncRoundingInput(mode, input);
  }
  mode.addEventListener("change", sync);
  sync();
  return sync;
}

function bindMirroredValue(selectorA, selectorB, afterSync = () => {}) {
  const first = document.querySelector(selectorA);
  const second = document.querySelector(selectorB);
  if (!first || !second) return;
  let syncing = false;
  function copy(source, target) {
    if (syncing) return;
    syncing = true;
    if (target.value !== source.value) target.value = source.value;
    syncing = false;
    afterSync();
  }
  ["input", "change"].forEach((eventName) => {
    first.addEventListener(eventName, () => copy(first, second));
    second.addEventListener(eventName, () => copy(second, first));
  });
  copy(first, second);
}

export function enableRoundingControls() {
  const syncPitch = bindRoundingControls("#rounding-mode", "#rounding-candidates");
  const syncRule = bindRoundingControls("#rule-rounding-mode", "#rule-rounding-candidates");
  const syncAll = () => {
    syncPitch();
    syncRule();
  };
  bindMirroredValue("#rounding-mode", "#rule-rounding-mode", syncAll);
  bindMirroredValue("#rounding-candidates", "#rule-rounding-candidates");
  syncAll();
}

export function enableExcludePlaceholder() {
  const mode = document.querySelector("#exclude-mode");
  const input = document.querySelector("#exclude-pattern");
  function sync() {
    if (mode.value === "regex") {
      input.placeholder = "^(R|br)|息$";
    } else if (mode.value === "mora") {
      input.placeholder = "_, R";
    } else if (mode.value === "string_list") {
      input.placeholder = "息, R, br";
    } else {
      input.placeholder = "";
    }
    input.disabled = mode.value === "none";
    if (mode.value === "none") input.value = "";
    input.classList.toggle("placeholder-select", mode.value === "none");
    mode.classList.toggle("placeholder-select", mode.value === "none");
  }
  mode.addEventListener("change", sync);
  sync();
}

export function enableSortChips() {
  const selected = [];
  const direction = { mora: "asc", prefix: "asc", suffix: "asc", filename: "asc", alias: "asc", pitch: "asc", old_order: "asc", usage: "desc" };
  const chips = Array.from(document.querySelectorAll(".sort-chip"));
  function sync() {
    chips.forEach((chip) => {
      const key = chip.dataset.key;
      const index = selected.indexOf(key);
      chip.classList.toggle("active", index !== -1);
      chip.dataset.direction = direction[key];
      chip.querySelector(".rank").textContent = index === -1 ? "" : String(index + 1);
      chip.querySelector(".arrow").textContent = direction[key] === "asc" ? "\u2193" : "\u2191";
    });
  }
  chips.forEach((chip) => {
    chip.addEventListener("click", (event) => {
      const key = chip.dataset.key;
      if (event.target.closest(".arrow")) {
        direction[key] = direction[key] === "asc" ? "desc" : "asc";
        sync();
        return;
      }
      const index = selected.indexOf(key);
      if (index === -1) selected.push(key);
      else selected.splice(index, 1);
      sync();
    });
  });
  sync();
}

function datasetKeyForRadio(name) {
  if (name === "edit-scope") return "editScope";
  if (name === "rule-scope") return "ruleScope";
  return "";
}

function radioGroupValue(name, fallback = "") {
  const radios = Array.from(document.querySelectorAll(`input[name="${name}"]`));
  if (!radios.length) return fallback;
  const datasetKey = datasetKeyForRadio(name);
  const stored = datasetKey ? document.documentElement.dataset[datasetKey] : "";
  const checked = radios.find((radio) => radio.checked)?.value;
  const value = stored || checked || fallback || radios[0].value;
  return radios.some((radio) => radio.value === value) ? value : fallback || radios[0].value;
}

function setRadioGroupValue(name, value, fallback = "") {
  const radios = Array.from(document.querySelectorAll(`input[name="${name}"]`));
  if (!radios.length) return fallback;
  const selected =
    radios.find((radio) => radio.value === value) ||
    radios.find((radio) => radio.value === fallback) ||
    radios[0];
  radios.forEach((radio) => {
    radio.checked = radio === selected;
  });
  const datasetKey = datasetKeyForRadio(name);
  if (datasetKey) document.documentElement.dataset[datasetKey] = selected.value;
  return selected.value;
}

function bindPersistentRadioGroup(name, fallback = "") {
  setRadioGroupValue(name, radioGroupValue(name, fallback), fallback);
  document.querySelectorAll(`input[name="${name}"]`).forEach((radio) => {
    radio.addEventListener("click", () => setRadioGroupValue(name, radio.value, fallback));
    radio.addEventListener("change", () => setRadioGroupValue(name, radio.value, fallback));
  });
}

export function enableModeOptions() {
  const mode = document.querySelector("#process-mode");
  const stripPrefix = document.querySelector("#strip-prefix");
  const stripSuffix = document.querySelector("#strip-suffix");
  const addAlias = document.querySelector("#add-alias-unused");
  const prefixUnderscore = document.querySelector("#prefix-underscore");
  const numberFirst = document.querySelector("#number-first-alias");
  const excludeNoF0 = document.querySelector("#exclude-no-f0");
  const excludeNoFreqSrc = document.querySelector("#exclude-no-freq-src");
  const excludeEmptyParams = document.querySelector("#exclude-empty-params");
  const editScopeControls = Array.from(document.querySelectorAll('input[name="edit-scope"]'));
  const commonRewriteControls = [
    ...editScopeControls,
    stripPrefix,
    stripSuffix,
    addAlias,
    prefixUnderscore,
  ].filter(Boolean);
  const groups = Array.from(document.querySelectorAll("[data-mode-group]"));
  bindPersistentRadioGroup("edit-scope", "call_key");

  const defaults = {
    pitch_append: { stripPrefix: false, stripSuffix: true, addAlias: false, prefixUnderscore: false, numberFirst: false, excludeNoF0: true, excludeNoFreqSrc: true, excludeEmptyParams: true },
    numbering: { stripPrefix: false, stripSuffix: true, addAlias: false, prefixUnderscore: false, numberFirst: false, excludeNoF0: false, excludeNoFreqSrc: false, excludeEmptyParams: true },
    none: { stripPrefix: false, stripSuffix: false, addAlias: false, prefixUnderscore: false, numberFirst: false, excludeNoF0: false, excludeNoFreqSrc: false, excludeEmptyParams: true },
    replace: { stripPrefix: false, stripSuffix: false, addAlias: false, prefixUnderscore: false, numberFirst: false, excludeNoF0: false, excludeNoFreqSrc: false, excludeEmptyParams: true },
    csv: { stripPrefix: false, stripSuffix: false, addAlias: false, prefixUnderscore: false, numberFirst: false, excludeNoF0: false, excludeNoFreqSrc: false, excludeEmptyParams: true },
    rule_based: { stripPrefix: false, stripSuffix: false, addAlias: false, prefixUnderscore: false, numberFirst: false, excludeNoF0: true, excludeNoFreqSrc: true, excludeEmptyParams: true },
  };

  function sync({ applyDefaults = false } = {}) {
    const current = mode.value;
    setRadioGroupValue("edit-scope", radioGroupValue("edit-scope", "call_key"), "call_key");
    if (applyDefaults) {
      const values = defaults[current] || defaults.pitch_append;
      stripPrefix.checked = values.stripPrefix;
      stripSuffix.checked = values.stripSuffix;
      addAlias.checked = values.addAlias;
      prefixUnderscore.checked = values.prefixUnderscore;
      numberFirst.checked = values.numberFirst;
      if (excludeNoF0) excludeNoF0.checked = values.excludeNoF0;
      if (excludeNoFreqSrc) excludeNoFreqSrc.checked = values.excludeNoFreqSrc;
      if (excludeEmptyParams) excludeEmptyParams.checked = values.excludeEmptyParams;
    }
    const rewriteDisabled = current === "csv" || current === "rule_based" || current === "replace";
    commonRewriteControls.forEach((control) => {
      control.disabled = rewriteDisabled;
      control.closest("label")?.classList.toggle("disabled-option", rewriteDisabled);
    });
    document.querySelectorAll("[data-rewrite-common]").forEach((element) => {
      element.hidden = rewriteDisabled;
    });
    const pitchExcludeDisabled = current !== "pitch_append" && current !== "rule_based";
    [excludeNoF0, excludeNoFreqSrc].filter(Boolean).forEach((control) => {
      control.disabled = pitchExcludeDisabled;
      control.closest("label")?.classList.toggle("disabled-option", pitchExcludeDisabled);
    });
    if (excludeEmptyParams) {
      excludeEmptyParams.disabled = false;
      excludeEmptyParams.closest("label")?.classList.toggle("disabled-option", false);
    }
    groups.forEach((group) => {
      const modes = String(group.dataset.modeGroup || "").split(/\s+/);
      group.hidden = !modes.includes(current);
    });
  }

  mode.addEventListener("change", () => sync({ applyDefaults: true }));
  sync();
}

export function enableRuleScopeControls() {
  const radios = Array.from(document.querySelectorAll('input[name="rule-scope"]'));
  const aliasLabel = document.querySelector("#rule-alias-template")?.closest("label");
  const wavLabel = document.querySelector("#rule-wav-template")?.closest("label");
  if (aliasLabel) aliasLabel.dataset.ruleScopeGroup = "alias_wav";
  if (wavLabel) wavLabel.dataset.ruleScopeGroup = "alias_wav";
  const groups = Array.from(document.querySelectorAll("[data-rule-scope-group]"));
  bindPersistentRadioGroup("rule-scope", "call_key");

  function sync() {
    const scope = setRadioGroupValue("rule-scope", radioGroupValue("rule-scope", "call_key"), "call_key");
    groups.forEach((group) => {
      group.hidden = group.dataset.ruleScopeGroup !== scope;
    });
  }

  radios.forEach((radio) => radio.addEventListener("change", sync));
  sync();
}

export function enableTooltips() {
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
