const entryPanel = document.querySelector("#entry-panel");
const runPanel = document.querySelector("#run-panel");
const resultPanel = document.querySelector("#result-panel");
const form = document.querySelector("#subject-form");
const configList = document.querySelector("#config-list");
const phaseLabel = document.querySelector("#phase-label");
const runTitle = document.querySelector("#run-title");
const instruction = document.querySelector("#instruction");
const timer = document.querySelector("#timer");
const progressFill = document.querySelector("#progress-fill");
const sampleCount = document.querySelector("#sample-count");
const samplingRate = document.querySelector("#sampling-rate");
const targetChannels = document.querySelector("#target-channels");
const message = document.querySelector("#message");
const resultJson = document.querySelector("#result-json");

let appConfig = null;
let lastResult = null;
let source = null;

async function loadConfig() {
  const response = await fetch("/api/config");
  appConfig = await response.json();
  configList.innerHTML = [
    ["采集时长", `${appConfig.recording_seconds}s`],
    ["裁剪", `首尾各 ${appConfig.trim_start_seconds}s`],
    ["目标通道", appConfig.target_channels.join(", ")],
  ]
    .map(([label, value]) => `<div><dt>${label}</dt><dd>${value}</dd></div>`)
    .join("");
  targetChannels.textContent = appConfig.target_channels.join(", ");
  timer.textContent = Math.round(appConfig.recording_seconds);
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const payload = {
    subject_id: document.querySelector("#subject-id").value.trim(),
    age: Number(document.querySelector("#age").value),
  };
  if (!payload.subject_id || !Number.isFinite(payload.age)) {
    return;
  }
  showPanel(runPanel);
  setRunState("connecting", "正在连接 Curry9 LSL EEG 数据流。");
  const response = await fetch("/api/session/start", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    setRunState("error", await response.text());
    return;
  }
  const session = await response.json();
  connectEvents(session.id);
});

document.querySelector("#download-json").addEventListener("click", () => {
  if (lastResult) {
    downloadBlob(JSON.stringify(lastResult, null, 2), "iaf-result.json", "application/json");
  }
});

document.querySelector("#download-csv").addEventListener("click", () => {
  if (lastResult) {
    downloadBlob(resultToCsv(lastResult), "iaf-result.csv", "text/csv;charset=utf-8");
  }
});

document.querySelector("#new-session").addEventListener("click", () => {
  window.location.reload();
});

function connectEvents(sessionId) {
  if (source) {
    source.close();
  }
  source = new EventSource(`/api/session/${sessionId}/events`);
  source.onmessage = (event) => {
    const data = JSON.parse(event.data);
    renderEvent(data);
    if (data.status === "finished" || data.status === "error") {
      source.close();
    }
  };
  source.onerror = () => {
    setRunState("error", "SSE 连接中断，请检查后端服务。");
    source.close();
  };
}

function renderEvent(data) {
  setRunState(data.status, data.message || "");
  const progress = data.progress || {};
  if (progress.sample_count !== undefined) {
    sampleCount.textContent = progress.sample_count;
  }
  if (data.stream?.sampling_rate_hz) {
    samplingRate.textContent = `${Number(data.stream.sampling_rate_hz).toFixed(1)} Hz`;
  }
  if (data.stream?.channels?.length) {
    targetChannels.textContent = appConfig.target_channels
      .map((channel) => (data.stream.channels.includes(channel) ? channel : `${channel} 缺失`))
      .join(", ");
  }
  if (progress.remaining_seconds !== undefined) {
    timer.textContent = Math.ceil(progress.remaining_seconds);
  }
  if (progress.elapsed_seconds !== undefined && appConfig) {
    const pct = Math.min(100, (progress.elapsed_seconds / appConfig.recording_seconds) * 100);
    progressFill.style.width = `${pct}%`;
  }
  if (data.status === "finished" && data.result) {
    showResult(data.result);
  }
  if (data.status === "error") {
    instruction.textContent = "实验中止";
    message.textContent = data.error || data.message;
  }
}

function setRunState(status, text) {
  phaseLabel.textContent = stateLabel(status);
  runTitle.textContent = status === "recording" ? "闭眼静息采集中" : stateTitle(status);
  instruction.textContent = status === "recording" ? "请闭眼保持静息" : "请保持准备，等待系统连接 EEG 数据流。";
  message.textContent = text;
}

function showResult(result) {
  lastResult = result;
  showPanel(resultPanel);
  document.querySelector("#paf").textContent = formatHz(result.paf_hz);
  document.querySelector("#cog").textContent = formatHz(result.cog_hz);
  document.querySelector("#alpha-window").textContent = result.alpha_window_hz
    ? `${result.alpha_window_hz.map((value) => value.toFixed(2)).join(" - ")} Hz`
    : "--";
  document.querySelector("#valid-channels").textContent = `${result.valid_peak_channels} PAF / ${result.valid_band_channels} CoG`;
  resultJson.textContent = JSON.stringify(result, null, 2);
}

function showPanel(panel) {
  [entryPanel, runPanel, resultPanel].forEach((item) => item.classList.add("hidden"));
  panel.classList.remove("hidden");
}

function stateLabel(status) {
  return {
    created: "已创建",
    connecting: "连接中",
    recording: "采集中",
    processing: "处理中",
    finished: "完成",
    error: "错误",
  }[status] || status;
}

function stateTitle(status) {
  return {
    created: "准备开始",
    connecting: "正在搜索 LSL 数据流",
    processing: "正在计算 IAF",
    finished: "IAF 计算完成",
    error: "无法完成实验",
  }[status] || "运行中";
}

function formatHz(value) {
  return Number.isFinite(value) ? `${value.toFixed(2)} Hz` : "--";
}

function downloadBlob(content, filename, type) {
  const blob = new Blob([content], { type });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

function resultToCsv(result) {
  const rows = [
    ["subject_id", "age", "paf_hz", "cog_hz", "alpha_low_hz", "alpha_high_hz", "valid_peak_channels", "valid_band_channels"],
    [
      result.subject_id,
      result.age,
      result.paf_hz ?? "",
      result.cog_hz ?? "",
      result.alpha_window_hz?.[0] ?? "",
      result.alpha_window_hz?.[1] ?? "",
      result.valid_peak_channels,
      result.valid_band_channels,
    ],
    [],
    ["channel", "paf_hz", "cog_hz", "alpha_low_hz", "alpha_high_hz", "peak_quality"],
    ...result.channel_estimates.map((item) => [
      item.channel,
      item.paf_hz ?? "",
      item.cog_hz ?? "",
      item.alpha_low_hz ?? "",
      item.alpha_high_hz ?? "",
      item.peak_quality ?? "",
    ]),
  ];
  return rows.map((row) => row.map(csvCell).join(",")).join("\n");
}

function csvCell(value) {
  const text = String(value);
  return /[",\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}

loadConfig().catch((error) => {
  message.textContent = error.message;
});
