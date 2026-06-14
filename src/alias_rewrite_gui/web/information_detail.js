import { escapeHtml } from "./dom_utils.js";

let currentDetail = null;
let heatMode = "alias";
let selectedMora = "";
let colorSchemes = [];
let audioPlaying = false;
let audioTimer = null;

function waitForApi() {
  return new Promise((resolve) => {
    if (window.pywebview?.api) {
      resolve(true);
      return;
    }
    window.addEventListener("pywebviewready", () => resolve(true), { once: true });
    window.setTimeout(() => resolve(Boolean(window.pywebview?.api)), 5000);
  });
}

async function api(name, payload = {}) {
  if (!(await waitForApi())) throw new Error("pywebview API is not available");
  return await window.pywebview.api[name](payload);
}

function setText(selector, value) {
  const element = document.querySelector(selector);
  if (element) element.textContent = value ?? "";
}

function applyColorScheme(name, schemes) {
  const normalized = name === "dark" ? "defoko_dark" : name === "light" ? "defoko_light" : name || "defoko_dark";
  const base = schemes.find((item) => item.name === "defoko_dark")?.colors || {};
  const scheme = schemes.find((item) => item.name === normalized)?.colors || {};
  Object.entries({ ...base, ...scheme }).forEach(([key, value]) => {
    document.documentElement.style.setProperty(`--${key}`, String(value));
  });
}

function applyUiScale(value) {
  const scale = Number.parseFloat(value);
  document.documentElement.style.setProperty("--ui-scale", String(Number.isFinite(scale) && scale > 0 ? scale : 1));
}

function hz(value, digits = 2) {
  const number = Number(value);
  return Number.isFinite(number) && number > 0 ? `${number.toFixed(digits)}Hz` : "-";
}

function ms(value) {
  const number = Number(value);
  return Number.isFinite(number) ? `${Math.round(number)}ms` : "-";
}

function rowHtml(cells) {
  return `<tr>${cells.map((cell) => `<td>${escapeHtml(cell ?? "")}</td>`).join("")}</tr>`;
}

function infoRow(label, value) {
  return `<tr><th>${escapeHtml(label)}</th><td>${escapeHtml(value ?? "")}</td></tr>`;
}

function renderSummary(detail) {
  const summary = detail.summary || {};
  setText("#voice-name", summary.voice_name || "-");
  setText("#voice-path", summary.voice_folder_path || detail.voice_dir || "-");
  const noteStats = [summary.note_average, summary.note_median, summary.note_mode].map((value) => value || "-").join(" / ");
  document.querySelector("#summary-table").innerHTML = [
    infoRow("音源名", summary.folder_name || summary.voice_name || "-"),
    infoRow("音源フォルダパス", summary.voice_folder_path || detail.voice_dir || "-"),
    infoRow("原音ファイル数", summary.wav_file_count ?? 0),
    infoRow("検出した原音関連ファイル数", summary.related_file_count ?? 0),
    infoRow("oto.ini エントリ数", summary.oto_entry_count ?? summary.preview_rows ?? 0),
    infoRow("音階 平均 / 中央値 / 最頻値", noteStats),
    infoRow("平均周波数", hz(summary.frequency_average)),
    infoRow("検出したUST数", summary.ust_count ?? 0),
  ].join("");
  renderPitchDistribution(detail.pitch_distribution || []);
}

function renderPitchDistribution(distribution) {
  const maxCount = Math.max(1, ...distribution.map((item) => Number(item.count) || 0));
  const ticks = [maxCount, Math.ceil(maxCount * 0.75), Math.ceil(maxCount * 0.5), Math.ceil(maxCount * 0.25), 0]
    .filter((value, index, values) => index === 0 || value !== values[index - 1])
    .map((value) => ({
      value,
      bottom: (value / maxCount) * 100,
    }));
  document.querySelector("#detail-pitch-y-axis").innerHTML = ticks
    .map((tick) => `<span style="bottom:${tick.bottom}%">${tick.value}</span>`)
    .join("");
  document.querySelector("#detail-pitch-grid").innerHTML = ticks
    .map((tick) => `<span style="bottom:${tick.bottom}%"></span>`)
    .join("");
  document.querySelector("#detail-pitch-chart").innerHTML = distribution
    .map((item) => {
      const count = Number(item.count) || 0;
      const height = Math.max(1, Math.round((count / maxCount) * 100));
      return `<div class="pitch-bar" title="${escapeHtml(item.label)}: ${count}" style="height:${height}%"></div>`;
    })
    .join("");
  document.querySelector("#detail-pitch-axis").innerHTML = distribution
    .filter((_item, index) => index % 6 === 0)
    .map((item) => `<span>${escapeHtml(item.label)}</span>`)
    .join("");
}

function renderEntry(detail) {
  const row = detail.selected_entry;
  const waveform = detail.waveform;
  if (!row) {
    setText("#entry-title", "Preview表で行を選択してください");
    setText("#entry-subtitle", "行未選択時の再生ボタンは原音フォルダ内のランダムな音声を再生します");
    renderEntryValues(null);
    renderWaveform(null, null);
    const summary = detail.summary || {};
    document.querySelector("#entry-information").innerHTML = [
      infoRow("状態", "行が選択されていません"),
      infoRow("音源名", summary.voice_name || "-"),
      infoRow("音源フォルダパス", summary.voice_folder_path || detail.voice_dir || "-"),
      infoRow("原音ファイル数", summary.wav_file_count ?? 0),
      infoRow("oto.ini エントリ数", summary.oto_entry_count ?? summary.preview_rows ?? 0),
      infoRow("検出したUST数", summary.ust_count ?? 0),
    ].join("");
  } else {
    const alias = row.new_alias || row.old_alias || "(空)";
    setText("#entry-title", `${row.new_wav || row.old_wav} / ${alias}`);
    setText("#entry-subtitle", `line ${row.line_number ?? "-"} / order ${row.old_order_id ?? "-"} → ${row.new_order_id ?? "-"}`);
    renderEntryValues(row);
    renderWaveform(waveform, row);
    document.querySelector("#entry-information").innerHTML = [
      infoRow("行番号", row.line_number ?? "-"),
      infoRow("旧順番 → 新順番", `${row.old_order_id ?? "-"} → ${row.new_order_id ?? "-"}`),
      infoRow("旧ファイル名", row.old_wav || ""),
      infoRow("新ファイル名", row.new_wav || ""),
      infoRow("元エイリアス", row.old_alias || "(空)"),
      infoRow("新エイリアス", row.new_alias || "(空)"),
      infoRow("推定音階", row.note || "-"),
      infoRow("推定周波数", hz(row.frequency, 3)),
      infoRow("ステータス", row.status || row.origin_status || "-"),
      infoRow("警告レベル", row.severity || "-"),
      infoRow("UST使用数", row.usage_count ?? 0),
      infoRow("診断", (row.diagnostics || []).join(", ") || "-"),
    ].join("");
  }

  const related = detail.related_files || [];
  document.querySelector("#related-files").innerHTML = related.length
    ? related.map((item) => rowHtml([item.old_name, item.new_name || ""])).join("")
    : `<tr><td colspan="2" class="empty">実在する関連ファイルはありません</td></tr>`;

  const usingUst = detail.using_ust || [];
  document.querySelector("#using-ust").innerHTML = usingUst.length
    ? usingUst.map((item) => rowHtml([item.label || item.path, item.count])).join("")
    : `<tr><td colspan="2" class="empty">選択USTでの使用はありません</td></tr>`;
}

function renderEntryValues(row) {
  setText("#entry-offset", row ? ms(row.offset_ms) : "-");
  setText("#entry-consonant", row ? ms(row.consonant_ms) : "-");
  setText("#entry-cutoff", row ? ms(row.cutoff_ms) : "-");
  setText("#entry-preutterance", row ? ms(row.preutterance_ms) : "-");
  setText("#entry-overlap", row ? ms(row.overlap_ms) : "-");
}

function renderWaveform(waveform, row) {
  const bars = document.querySelector("#wave-bars");
  const curve = document.querySelector("#pitch-polyline");
  bars.textContent = "";
  curve.setAttribute("points", "");
  if (!waveform || !Array.isArray(waveform.peaks) || !waveform.peaks.length) {
    return;
  }
  const peaks = waveform.peaks;
  const start = Number(waveform.display_start_ms) || 0;
  const end = Number(waveform.display_end_ms) || start + 1;
  const duration = Math.max(1, end - start);
  const binMs = duration / peaks.length;
  const voiceStart = (Number(waveform.offset_ms) || 0) + Math.max(0, Number(waveform.consonant_ms) || 0);
  const offsetAt = Number(waveform.offset_ms) || 0;
  const overlapAt = (Number(waveform.offset_ms) || 0) + (Number(waveform.overlap_ms) || 0);
  const preutteranceAt = (Number(waveform.offset_ms) || 0) + (Number(waveform.preutterance_ms) || 0);
  bars.style.setProperty("--wave-bar-count", String(peaks.length));
  peaks.forEach((peak, index) => {
    const time = start + index * binMs;
    const bar = document.createElement("div");
    bar.className = "wave-bar";
    if (time < offsetAt) bar.classList.add("pre-offset");
    if (time >= voiceStart) bar.classList.add("voice");
    if (Math.abs(time - overlapAt) <= binMs * 0.7) bar.className = "wave-bar overlap";
    if (Math.abs(time - preutteranceAt) <= binMs * 0.7) bar.className = "wave-bar preutterance";
    bar.style.height = `${Math.max(6, Math.round(Number(peak) * 138))}px`;
    bars.appendChild(bar);
  });
  curve.setAttribute("points", pitchPolylinePoints(waveform, row));
}

function midiFromFrequency(frequency) {
  return 69 + 12 * Math.log2(frequency / 440);
}

function pitchPolylinePoints(waveform, row) {
  const start = Number(waveform?.display_start_ms) || 0;
  const end = Number(waveform?.display_end_ms) || start + 1;
  let points = Array.isArray(waveform?.f0_points) ? waveform.f0_points.filter((item) => Number(item.frequency) > 0) : [];
  if (points.length < 2 && row?.frequency) {
    points = [
      { time_ms: start, frequency: row.frequency },
      { time_ms: end, frequency: row.frequency },
    ];
  }
  if (points.length < 2) return "";
  const midiValues = points.map((item) => midiFromFrequency(Number(item.frequency))).filter(Number.isFinite);
  if (!midiValues.length) return "";
  const minMidi = Math.min(...midiValues);
  const maxMidi = Math.max(...midiValues);
  const span = Math.max(1, maxMidi - minMidi);
  return points
    .map((item) => {
      const time = Number(item.time_ms);
      const midi = midiFromFrequency(Number(item.frequency));
      if (!Number.isFinite(time) || !Number.isFinite(midi)) return "";
      const x = Math.max(0, Math.min(1000, ((time - start) / Math.max(1, end - start)) * 1000));
      const y = 145 - ((midi - minMidi) / span) * 110;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .filter(Boolean)
    .join(" ");
}

function tokenizeOtolistLine(line) {
  const tokens = [];
  const smallKana = new Set(Array.from("ゃゅょぁぃぅぇぉゎャュョァィゥェォヮ"));
  const chars = Array.from(line);
  for (let index = 0; index < chars.length; index += 1) {
    const char = chars[index];
    if (char === " " || char === "\u3000" || char === "\t") tokens.push({ type: "gap" });
    else if (index + 1 < chars.length && smallKana.has(chars[index + 1])) {
      tokens.push({ type: "mora", value: char + chars[index + 1] });
      index += 1;
    } else tokens.push({ type: "mora", value: char });
  }
  return tokens;
}

function parseOtolist(text) {
  const bands = [];
  let band = [];
  String(text || "").split(/\r?\n/).forEach((line) => {
    if (!line.trim()) {
      if (band.length) {
        bands.push(band);
        band = [];
      }
      return;
    }
    band.push(tokenizeOtolistLine(line));
  });
  if (band.length) bands.push(band);
  return bands;
}

function renderMoraTable(detail) {
  const pronunciation = detail.pronunciation || {};
  const stats = pronunciation.stats || {};
  const values = Object.values(stats).map((item) => Number(item[heatMode]) || 0);
  const maxValue = Math.max(1, ...values);
  const bands = parseOtolist(detail.otolist_text || "");
  const table = document.querySelector("#mora-table");
  table.textContent = "";
  bands.forEach((band) => {
    const bandElement = document.createElement("div");
    bandElement.className = "mora-band";
    band.forEach((column) => {
      const columnElement = document.createElement("div");
      columnElement.className = "mora-column";
      column.forEach((token) => {
        if (token.type === "gap") {
          const gap = document.createElement("div");
          gap.className = "mora-gap";
          columnElement.appendChild(gap);
          return;
        }
        const mora = token.value;
        const value = Number(stats[mora]?.[heatMode]) || 0;
        const ratio = maxValue > 0 ? Math.max(0, Math.min(1, value / maxValue)) : 0;
        const mix = Math.round(8 + ratio * 58);
        const button = document.createElement("button");
        button.type = "button";
        button.className = "mora-cell";
        button.classList.toggle("active", selectedMora === mora);
        button.style.borderColor = value > 0 ? `color-mix(in srgb, var(--main) ${Math.min(65, 18 + (value / maxValue) * 47)}%, var(--soft-line))` : "color-mix(in oklab, var(--soft-line) 30%, transparent)";
        button.style.background = value > 0 ? `color-mix(in oklab, var(--main) ${mix}%, var(--right))` : "var(--right)";
        button.style.color = value > 0 ? "var(--text)" : "var(--muted)";
        button.innerHTML = `${escapeHtml(mora)}<br><b>${value}</b>`;
        button.title = `${mora}: ${heatMode === "alias" ? "エイリアス数" : "UST使用数"} ${value}`;
        button.addEventListener("click", () => {
          selectedMora = mora;
          renderMoraTable(currentDetail);
          renderMoraDetail(currentDetail);
        });
        columnElement.appendChild(button);
      });
      bandElement.appendChild(columnElement);
    });
    table.appendChild(bandElement);
  });
}

function renderMoraDetail(detail) {
  const pronunciation = detail.pronunciation || {};
  const stats = pronunciation.stats?.[selectedMora] || { alias: 0, usage: 0 };
  setText("#mora-summary", selectedMora ? `${selectedMora}: エイリアス数 ${stats.alias || 0} / UST使用数 ${stats.usage || 0}` : "発音を選択してください");
  const entries = (pronunciation.entries || []).filter((item) => item.mora === selectedMora);
  document.querySelector("#mora-entries").innerHTML = entries.length
    ? entries.map((item) => rowHtml([item.new_alias || "", item.new_wav || "", item.note || "", item.usage ?? 0])).join("")
    : `<tr><td colspan="4" class="empty">該当する行はありません</td></tr>`;
}

function renderUnresolved(detail) {
  const rows = detail.unresolved_lyrics || [];
  const summary = detail.summary || {};
  setText("#unresolved-count", `${summary.unresolved_lyrics ?? rows.length}/${summary.total_lyrics ?? 0}`);
  document.querySelector("#unresolved-lyrics").innerHTML = rows.length
    ? rows.map((item) => rowHtml([item.lyric, item.ust_label || item.ust_path, item.note_id])).join("")
    : `<tr><td colspan="3" class="empty">未解決Lyricはありません</td></tr>`;
}

function renderEmpty(message) {
  setText("#window-state", message);
  setText("#voice-name", "-");
  setText("#voice-path", message);
  document.querySelector("#summary-table").innerHTML = infoRow("状態", message);
  document.querySelector("#detail-pitch-chart").innerHTML = "";
  document.querySelector("#detail-pitch-y-axis").innerHTML = "";
  document.querySelector("#detail-pitch-grid").innerHTML = "";
  document.querySelector("#detail-pitch-axis").innerHTML = "";
  document.querySelector("#entry-information").innerHTML = infoRow("状態", message);
  document.querySelector("#related-files").innerHTML = "";
  document.querySelector("#using-ust").innerHTML = "";
  document.querySelector("#mora-table").innerHTML = "";
  document.querySelector("#mora-entries").innerHTML = "";
  document.querySelector("#unresolved-lyrics").innerHTML = "";
  renderWaveform(null, null);
}

export function renderInformationDetail(detail) {
  currentDetail = detail || {};
  applyUiScale(detail?.ui_scale || 1);
  applyColorScheme(detail?.theme || "defoko_dark", colorSchemes);
  if (!detail || detail.state === "empty") {
    renderEmpty("Preview未実行、またはApply後にPreviewがリセットされた状態です");
    return;
  }
  if (detail.state === "error") {
    renderEmpty((detail.errors || []).join(" / ") || "INFORMATIONを更新できませんでした");
    return;
  }
  setText("#window-state", "Preview結果に追従しています");
  renderSummary(detail);
  renderEntry(detail);
  if (!selectedMora) {
    selectedMora = Object.keys(detail.pronunciation?.stats || {})[0] || "";
  }
  renderMoraTable(detail);
  renderMoraDetail(detail);
  renderUnresolved(detail);
}

window.renderInformationDetailFromHost = renderInformationDetail;

document.querySelectorAll("[data-tab]").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll("[data-tab]").forEach((item) => item.classList.toggle("active", item === button));
    document.querySelectorAll("[data-panel]").forEach((panel) => panel.classList.toggle("active", panel.dataset.panel === button.dataset.tab));
  });
});

document.querySelectorAll("#heat-mode [data-mode]").forEach((button) => {
  button.addEventListener("click", () => {
    heatMode = button.dataset.mode;
    document.querySelectorAll("#heat-mode [data-mode]").forEach((item) => item.classList.toggle("active", item === button));
    renderMoraTable(currentDetail || {});
    renderMoraDetail(currentDetail || {});
  });
});

document.querySelector("#close-window")?.addEventListener("click", () => {
  api("close_information_window", {}).catch(() => window.close());
});

document.querySelector("#open-voice-folder")?.addEventListener("click", () => {
  api("open_voice_folder", { voice_dir: currentDetail?.voice_dir || currentDetail?.summary?.voice_folder_path || "" }).catch(() => false);
});

document.querySelector("#entry-play")?.addEventListener("click", async () => {
  const button = document.querySelector("#entry-play");
  if (audioPlaying) {
    await api("stop_audio", {}).catch(() => null);
    if (audioTimer) window.clearTimeout(audioTimer);
    audioPlaying = false;
    button.textContent = "▶";
    button.classList.remove("playing");
    audioTimer = null;
    return;
  }
  const row = currentDetail?.selected_entry;
  const waveform = currentDetail?.waveform;
  const rowEnd = Number(row?.play_end_ms);
  const payload = row
    ? {
        voice_dir: currentDetail?.voice_dir || "",
        wav_name: row.old_wav || row.new_wav || "",
        start_ms: row.play_start_ms ?? waveform?.display_start_ms ?? 0,
        end_ms: Number.isFinite(rowEnd) && rowEnd > 0 ? rowEnd : waveform?.display_end_ms ?? 0,
      }
    : { voice_dir: currentDetail?.voice_dir || "" };
  const result = await api("play_audio", payload).catch((error) => ({ ok: false, message: error.message }));
  if (result?.ok) {
    audioPlaying = true;
    button.textContent = "■";
    button.classList.add("playing");
    if (audioTimer) window.clearTimeout(audioTimer);
    if (result.duration_ms > 0) {
      audioTimer = window.setTimeout(() => {
        audioPlaying = false;
        button.textContent = "▶";
        button.classList.remove("playing");
        audioTimer = null;
      }, result.duration_ms + 120);
    }
  }
});

try {
  colorSchemes = await api("get_color_schemes", {}) || [];
  applyColorScheme("defoko_dark", colorSchemes);
} catch (_error) {
  // 配色取得に失敗しても既定色で表示する
}

try {
  renderInformationDetail(await api("get_detail", {}));
} catch (error) {
  renderEmpty(error.message || String(error));
}
