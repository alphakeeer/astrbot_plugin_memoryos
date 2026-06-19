const bridge = window.AstrBotPluginPage || null;
const urlToken = new URLSearchParams(window.location.search).get("token") || "";

const state = {
  memories: [],
  jobs: [],
  stats: {},
};

const els = {
  runtimeMode: document.getElementById("runtime-mode"),
  connectionStatus: document.getElementById("connection-status"),
  toast: document.getElementById("toast"),
  details: document.getElementById("details"),
  memories: document.getElementById("memories"),
  memoriesEmpty: document.getElementById("memories-empty"),
  jobs: document.getElementById("jobs"),
  jobsEmpty: document.getElementById("jobs-empty"),
  query: document.getElementById("query"),
  status: document.getElementById("status"),
  type: document.getElementById("type"),
  newContent: document.getElementById("new-content"),
  newScope: document.getElementById("new-scope"),
  newOwner: document.getElementById("new-owner"),
  importJson: document.getElementById("import-json"),
  bootstrapOrigin: document.getElementById("bootstrap-origin"),
  bootstrapSession: document.getElementById("bootstrap-session"),
  bootstrapUser: document.getElementById("bootstrap-user"),
  bootstrapGroup: document.getElementById("bootstrap-group"),
  bootstrapLimit: document.getElementById("bootstrap-limit"),
  bootstrapCancelId: document.getElementById("bootstrap-cancel-id"),
  metricActive: document.getElementById("metric-active"),
  metricVectors: document.getElementById("metric-vectors"),
  metricRaw: document.getElementById("metric-raw"),
  metricWeb: document.getElementById("metric-web"),
};

const api = makeApiClient();

bindEvents();
await boot();

async function boot() {
  els.runtimeMode.textContent = bridge ? "AstrBot Page" : "Standalone";
  try {
    if (bridge) await bridge.ready();
    await refreshAll();
    setConnection("已连接", "ok");
  } catch (error) {
    showError(error);
    setConnection("连接异常", "error");
  }
}

function bindEvents() {
  document.getElementById("refresh").addEventListener("click", () =>
    withButton("refresh", refreshAll),
  );
  document.getElementById("refresh-jobs").addEventListener("click", () =>
    withButton("refresh-jobs", loadJobs),
  );
  document.getElementById("rebuild").addEventListener("click", () =>
    withButton("rebuild", rebuildIndex),
  );
  document.getElementById("export").addEventListener("click", () =>
    withButton("export", exportMemories),
  );
  document.getElementById("create").addEventListener("click", () =>
    withButton("create", createMemory),
  );
  document.getElementById("import").addEventListener("click", () =>
    withButton("import", importMemories),
  );
  document.getElementById("bootstrap-start").addEventListener("click", () =>
    withButton("bootstrap-start", () => startBootstrap(false)),
  );
  document.getElementById("bootstrap-dry-run").addEventListener("click", () =>
    withButton("bootstrap-dry-run", () => startBootstrap(true)),
  );
  document.getElementById("bootstrap-cancel").addEventListener("click", () =>
    withButton("bootstrap-cancel", cancelBootstrap),
  );
  document.getElementById("clear-details").addEventListener("click", () => {
    els.details.textContent = "{}";
  });
  for (const input of [els.query, els.status, els.type]) {
    input.addEventListener("change", loadMemoriesSafe);
    input.addEventListener("keyup", debounce(loadMemoriesSafe, 250));
  }
  for (const button of document.querySelectorAll("[data-section-target]")) {
    button.addEventListener("click", () => showSection(button.dataset.sectionTarget));
  }
}

async function refreshAll() {
  const results = await Promise.allSettled([loadStats(), loadMemories(), loadJobs()]);
  const rejected = results.find((item) => item.status === "rejected");
  if (rejected) throw rejected.reason;
}

async function loadStats() {
  const stats = await api.get("stats", {});
  state.stats = stats;
  els.metricActive.textContent = numberText(stats.active_memories);
  els.metricVectors.textContent = numberText(stats.vectors);
  els.metricRaw.textContent = numberText(stats.raw_messages);
  const web = stats.standalone_web || {};
  els.metricWeb.textContent = web.running ? "运行中" : "未运行";
}

async function loadMemoriesSafe() {
  try {
    await loadMemories();
  } catch (error) {
    showError(error);
  }
}

async function loadMemories() {
  const result = await api.get("memories", {
    q: els.query.value,
    status: els.status.value,
    type: els.type.value,
    limit: 100,
  });
  state.memories = result.memories || [];
  renderMemories();
}

async function loadJobs() {
  const result = await api.get("jobs", {});
  state.jobs = result.jobs || [];
  renderJobs();
}

async function rebuildIndex() {
  const result = await api.post("rebuild-index", {});
  showDetails(result);
  await loadJobs();
  showToast("索引重建任务已提交", "ok");
}

async function exportMemories() {
  const result = await api.get("export", {});
  showDetails(result);
  showToast("导出完成", "ok");
}

async function createMemory() {
  const content = els.newContent.value.trim();
  if (!content) throw new Error("记忆内容不能为空");
  const result = await api.post("memories", {
    content,
    canonical_text: content,
    scope: els.newScope.value,
    owner_key: els.newOwner.value || "global",
    memory_type: "fact",
    importance: 0.7,
    confidence: 0.9,
  });
  showDetails(result);
  els.newContent.value = "";
  await refreshAll();
  showToast("记忆已创建", "ok");
}

async function saveMemory(memory, row) {
  const content = row.querySelector('[data-field="content"]').value;
  const result = await api.post(`memories/${memory.memory_id}`, {
    content,
    canonical_text: content,
    tags: memory.tags || [],
    importance: memory.importance,
    confidence: memory.confidence,
  });
  showDetails(result);
  await loadMemories();
  showToast("记忆已保存", "ok");
}

async function showLogs(memory) {
  const result = await api.get(`memories/${memory.memory_id}/logs`, {});
  showDetails(result);
  showSection("diagnostics-panel");
}

async function expireMemory(memory) {
  const result = await api.post(`memories/${memory.memory_id}/expire`, {});
  showDetails(result);
  await loadMemories();
}

async function deleteMemory(memory) {
  const result = await api.post(`memories/${memory.memory_id}/delete`, {});
  showDetails(result);
  await loadMemories();
}

async function importMemories() {
  const payload = parseJson(els.importJson.value || "{}");
  const result = await api.post("import", payload);
  showDetails(result);
  await refreshAll();
  showToast("导入完成", "ok");
}

async function startBootstrap(dryRun) {
  const payload = bootstrapPayload();
  const route = dryRun ? "bootstrap/dry-run" : "bootstrap/start";
  const result = await api.post(route, payload);
  showDetails(result);
  await loadJobs();
  showSection("jobs-panel");
  showToast(dryRun ? "预览任务已提交" : "初始化任务已提交", "ok");
}

async function cancelBootstrap() {
  const jobId = els.bootstrapCancelId.value.trim();
  if (!jobId) throw new Error("缺少 job_id");
  const result = await api.post("bootstrap/cancel", { job_id: jobId });
  showDetails(result);
  await loadJobs();
}

function renderMemories() {
  els.memories.innerHTML = "";
  els.memoriesEmpty.hidden = state.memories.length > 0;
  for (const memory of state.memories) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td class="mono">${escapeHtml(memory.memory_id)}</td>
      <td>${escapeHtml(memory.scope)}<br><span class="muted">${escapeHtml(memory.owner_key)}</span></td>
      <td>${escapeHtml(memory.memory_type)}</td>
      <td>${Number(memory.importance).toFixed(2)} / ${Number(memory.confidence).toFixed(2)}</td>
      <td><textarea data-field="content">${escapeHtml(memory.content)}</textarea></td>
      <td class="row-actions">
        <button data-action="save">保存</button>
        <button class="secondary" data-action="logs">日志</button>
        <button class="secondary" data-action="expire">过期</button>
        <button class="danger" data-action="delete">删除</button>
      </td>
    `;
    tr.querySelector('[data-action="save"]').addEventListener("click", () =>
      safeInline(() => saveMemory(memory, tr)),
    );
    tr.querySelector('[data-action="logs"]').addEventListener("click", () =>
      safeInline(() => showLogs(memory)),
    );
    tr.querySelector('[data-action="expire"]').addEventListener("click", () =>
      safeInline(() => expireMemory(memory)),
    );
    tr.querySelector('[data-action="delete"]').addEventListener("click", () =>
      safeInline(() => deleteMemory(memory)),
    );
    els.memories.appendChild(tr);
  }
}

function renderJobs() {
  els.jobs.innerHTML = "";
  els.jobsEmpty.hidden = state.jobs.length > 0;
  for (const job of state.jobs) {
    const result = job.result || {};
    const div = document.createElement("article");
    div.className = "job-card";
    div.innerHTML = `
      <div>
        <strong>${escapeHtml(job.job_type)}</strong>
        <span class="muted mono">${escapeHtml(job.job_id)}</span>
      </div>
      <span class="job-status">${escapeHtml(job.status)}</span>
      <dl>
        <div><dt>读取</dt><dd>${numberText(result.read_messages)}</dd></div>
        <div><dt>候选</dt><dd>${numberText(result.candidate_count)}</dd></div>
        <div><dt>写入</dt><dd>${numberText(result.stored_count)}</dd></div>
        <div><dt>失败</dt><dd>${numberText(result.failed_chunks)}</dd></div>
      </dl>
    `;
    div.addEventListener("click", () => {
      showDetails(job);
      showSection("diagnostics-panel");
    });
    els.jobs.appendChild(div);
  }
}

function showSection(id) {
  for (const panel of document.querySelectorAll("[data-section]")) {
    panel.classList.toggle("active", panel.id === id);
  }
  for (const item of document.querySelectorAll("[data-section-target]")) {
    item.classList.toggle("active", item.dataset.sectionTarget === id);
  }
}

async function withButton(id, fn) {
  const button = document.getElementById(id);
  button.disabled = true;
  try {
    await fn();
    setConnection("已连接", "ok");
  } catch (error) {
    showError(error);
    setConnection("请求失败", "error");
  } finally {
    button.disabled = false;
  }
}

async function safeInline(fn) {
  try {
    await fn();
  } catch (error) {
    showError(error);
  }
}

function makeApiClient() {
  if (bridge) {
    return {
      async get(route, params) {
        return normalizeResponse(await bridge.apiGet(route, params || {}));
      },
      async post(route, body) {
        return normalizeResponse(await bridge.apiPost(route, body || {}));
      },
    };
  }
  return {
    async get(route, params) {
      return fetchJson(route, "GET", params || {}, null);
    },
    async post(route, body) {
      return fetchJson(route, "POST", {}, body || {});
    },
  };
}

async function fetchJson(route, method, params, body) {
  const url = new URL(`/api/${route}`, window.location.origin);
  for (const [key, value] of Object.entries(params || {})) {
    if (value !== undefined && value !== "") url.searchParams.set(key, value);
  }
  if (urlToken) url.searchParams.set("token", urlToken);
  const response = await fetch(url.toString(), {
    method,
    headers: {
      "Content-Type": "application/json",
      ...(urlToken ? { Authorization: `Bearer ${urlToken}` } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  const payload = await response.json().catch(() => ({}));
  return normalizeResponse(payload, response.status);
}

function normalizeResponse(payload, status = 200) {
  if (payload && payload.ok === true) return payload.data || {};
  if (payload && payload.ok === false) {
    throw new Error(payload.error?.message || `请求失败：${status}`);
  }
  if (payload && payload.error) {
    throw new Error(
      typeof payload.error === "string"
        ? payload.error
        : payload.error.message || `请求失败：${status}`,
    );
  }
  if (status >= 400) throw new Error(`请求失败：${status}`);
  return payload || {};
}

function bootstrapPayload() {
  return {
    unified_origin: els.bootstrapOrigin.value.trim(),
    session_id: els.bootstrapSession.value.trim(),
    user_id: els.bootstrapUser.value.trim(),
    group_id: els.bootstrapGroup.value.trim(),
    limit: Number(els.bootstrapLimit.value || 300),
  };
}

function parseJson(text) {
  try {
    return JSON.parse(text);
  } catch (error) {
    throw new Error("JSON 格式无效");
  }
}

function showDetails(value) {
  els.details.textContent = JSON.stringify(value, null, 2);
}

function showError(error) {
  const message = error?.message || String(error);
  showToast(message, "error");
  showDetails({ error: message });
}

function showToast(message, kind) {
  els.toast.hidden = false;
  els.toast.textContent = message;
  els.toast.dataset.kind = kind || "info";
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => {
    els.toast.hidden = true;
  }, 4800);
}

function setConnection(text, kind) {
  els.connectionStatus.textContent = text;
  els.connectionStatus.dataset.kind = kind;
}

function numberText(value) {
  if (value === undefined || value === null || value === "") return "0";
  return String(value);
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function debounce(fn, ms) {
  let timer;
  return () => {
    clearTimeout(timer);
    timer = setTimeout(fn, ms);
  };
}
