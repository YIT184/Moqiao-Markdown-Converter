const app = document.querySelector(".app-shell");
const dropZone = document.querySelector("#dropZone");
const fileList = document.querySelector("#fileList");
const emptyQueue = document.querySelector("#emptyQueue");
const outputPath = document.querySelector("#outputPath");
const convertButton = document.querySelector("#convertButton");
const recordsBody = document.querySelector("#recordsBody");
const summary = document.querySelector("#summary");

let files = [];
let logs = [];
let pollTimer = null;
let wasRunning = false;
let apiInitialized = false;
let suppressClickUntil = 0;

const apiReady = () => window.pywebview?.api;
const escapeHtml = value => String(value).replace(/[&<>"']/g, char => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;"
})[char]);

async function initializeApi() {
  if (apiInitialized) return;
  if (!apiReady()) {
    setTimeout(initializeApi, 60);
    return;
  }
  apiInitialized = true;
  try {
    outputPath.value = await window.pywebview.api.default_output();
  } catch {
    apiInitialized = false;
    setTimeout(initializeApi, 200);
  }
}

function appendLocalLog(level, message) {
  logs.push({
    time: new Date().toLocaleTimeString("zh-CN", { hour12: false }),
    level,
    message
  });
  renderLogs();
}

function mergeFiles(incoming) {
  const known = new Set(files.map(file => file.path));
  let added = 0;
  incoming.forEach(file => {
    if (!known.has(file.path)) {
      files.push(file);
      known.add(file.path);
      added += 1;
    }
  });
  renderFiles();
  return added;
}

window.moqiaoAcceptDroppedFiles = incoming => {
  const added = mergeFiles(incoming || []);
  if (added) appendLocalLog("done", `已拖入 ${added} 个文件`);
};

window.moqiaoShowDropMessage = message => {
  appendLocalLog("error", message || "未发现受支持的文件");
};

function renderFiles(state = null) {
  emptyQueue.hidden = files.length > 0;
  fileList.innerHTML = files.map((file, index) => {
    const item = state?.items?.[file.path] || { status: "waiting", progress: 0 };
    const labels = { waiting: "等待中", active: "转换中", done: "转换完成", error: "失败" };
    return `<div class="file-row" style="animation-delay:${Math.min(index * 35, 180)}ms">
      <div class="file-main">
        <span class="type-icon ${file.type.toLowerCase()}">${escapeHtml(file.type.slice(0, 4))}</span>
        <strong title="${escapeHtml(file.path)}">${escapeHtml(file.name)}</strong>
      </div>
      <span class="file-meta">${escapeHtml(file.type)}</span>
      <span class="file-meta">${escapeHtml(file.size)}</span>
      <div class="status-cell ${item.status}">
        <span class="status-text ${item.status}">${labels[item.status] || "等待中"}</span>
        <div class="progress-track"><div class="progress-fill" style="width:${item.progress || 0}%"></div></div>
      </div>
      <button class="remove-file" data-index="${index}" aria-label="移除 ${escapeHtml(file.name)}" ${state?.running ? "disabled" : ""}>
        <svg viewBox="0 0 24 24"><path d="M4 7h16M9 7V4h6v3M7 7l1 13h8l1-13M10 11v5M14 11v5"/></svg>
      </button>
    </div>`;
  }).join("");
}

async function chooseFiles() {
  if (!apiReady()) return;
  try {
    mergeFiles(await window.pywebview.api.select_files());
  } catch {
    appendLocalLog("error", "无法打开文件选择器，请重试");
  }
}

document.querySelector("#chooseFiles").addEventListener("click", event => {
  event.stopPropagation();
  chooseFiles();
});

dropZone.addEventListener("click", () => {
  if (Date.now() >= suppressClickUntil) chooseFiles();
});

dropZone.addEventListener("keydown", event => {
  if (event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    chooseFiles();
  }
});

["dragenter", "dragover"].forEach(name => dropZone.addEventListener(name, event => {
  event.preventDefault();
  dropZone.classList.add("dragging");
}));

dropZone.addEventListener("dragleave", event => {
  event.preventDefault();
  dropZone.classList.remove("dragging");
});

// Full local paths are delivered by pywebview's native DOM drop event.
// Never fall back to the file picker here: that was the source of the
// surprising "last folder opened" behavior.
dropZone.addEventListener("drop", event => {
  event.preventDefault();
  suppressClickUntil = Date.now() + 600;
  dropZone.classList.remove("dragging");
});

fileList.addEventListener("click", event => {
  const button = event.target.closest(".remove-file");
  if (!button || wasRunning) return;
  files.splice(Number(button.dataset.index), 1);
  renderFiles();
});

document.querySelector("#chooseOutput").addEventListener("click", async () => {
  if (!apiReady()) return;
  try {
    const result = await window.pywebview.api.select_output();
    if (result) outputPath.value = result;
  } catch {
    appendLocalLog("error", "无法选择输出目录，请重试");
  }
});

document.querySelector("#openOutputTop").addEventListener("click", async () => {
  if (apiReady()) await window.pywebview.api.open_output(outputPath.value);
});

convertButton.addEventListener("click", async () => {
  if (!apiReady() || wasRunning) return;
  try {
    const result = await window.pywebview.api.start_conversion(
      files.map(file => file.path),
      outputPath.value,
      {
        tables: document.querySelector("#tables").checked,
        images: document.querySelector("#images").checked,
        vectors: document.querySelector("#vectors").checked,
        password: document.querySelector("#password").value
      }
    );
    if (!result.ok) {
      appendLocalLog("error", result.message);
      return;
    }
    startPolling();
  } catch {
    appendLocalLog("error", "转换进程启动失败，请重试");
  }
});

function renderLogs() {
  if (!logs.length) {
    recordsBody.innerHTML = '<div class="empty-record">转换进度和结果会显示在这里</div>';
    return;
  }
  recordsBody.innerHTML = logs.map(log => `<div class="record-row ${log.level}">
    <time>${escapeHtml(log.time)}</time>
    <i class="record-dot"></i>
    <span>${escapeHtml(log.message)}</span>
  </div>`).join("");
  recordsBody.scrollTop = recordsBody.scrollHeight;
}

document.querySelector("#clearLogs").addEventListener("click", () => {
  logs = [];
  renderLogs();
});

async function pollState() {
  if (!apiReady()) return;
  try {
    const state = await window.pywebview.api.get_state();
    wasRunning = state.running;
    app.classList.toggle("converting", state.running);
    convertButton.disabled = state.running;
    convertButton.querySelector("span").textContent = state.running ? "正在转换" : "开始转换";
    logs = state.logs || [];
    renderFiles(state);
    renderLogs();
    summary.textContent = state.total ? `${state.completed} / ${state.total} 已完成` : "等待任务";
    if (!state.running) {
      clearInterval(pollTimer);
      pollTimer = null;
      convertButton.classList.remove("completed");
      void convertButton.offsetWidth;
      convertButton.classList.add("completed");
    }
  } catch {
    // A transient bridge delay must not freeze the visible UI.
  }
}

function startPolling() {
  if (pollTimer) clearInterval(pollTimer);
  wasRunning = true;
  pollState();
  pollTimer = setInterval(pollState, 350);
}

window.addEventListener("pywebviewready", initializeApi);
initializeApi();
