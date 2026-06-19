const PLUGIN_NAME = "astrbot_plugin_memoryos";
const bridge = window.AstrBotPluginPage || null;
const urlToken = new URLSearchParams(window.location.search).get("token") || "";

const state = {
  meta: {},
  diagnostics: {},
  stats: {},
  contexts: [],
  memories: [],
  jobs: [],
  logs: [],
  raw: [],
  selectedJobId: "",
  pollTimer: null,
};

const pageCopy = {
  dashboard: ["系统监控", "查看插件健康状态、最近任务和下一步建议。"],
  bootstrap: ["历史初始化", "选择会话、预检历史、查看原始消息、dry-run 候选并确认写入。"],
  memories: ["记忆管理", "搜索、创建、编辑、过期或删除长期记忆。"],
  raw: ["原始历史", "查看初始化读取到的历史样本和噪声标记。"],
  jobs: ["任务中心", "查看后台任务进度、取消任务和排查结果。"],
  logs: ["操作日志", "查看 WebUI 和 API 的成功、失败与修复建议。"],
  io: ["导入导出", "备份、迁移或恢复结构化记忆。"],
  diagnostics: ["诊断详情", "查看原始 JSON 返回和错误详情。"],
};

const els = {
  boot: byId("boot"),
  bootText: byId("boot-text"),
  runtimeMode: byId("runtime-mode"),
  pageTitle: byId("page-title"),
  pageSubtitle: byId("page-subtitle"),
  connectionStatus: byId("connection-status"),
  toast: byId("toast"),
  healthChecks: byId("health-checks"),
  latestDiagnosis: byId("latest-diagnosis"),
  metricActive: byId("metric-active"),
  metricRaw: byId("metric-raw"),
  metricContexts: byId("metric-contexts"),
  metricLogs: byId("metric-logs"),
  contextSelect: byId("context-select"),
  bootstrapOrigin: byId("bootstrap-origin"),
  bootstrapPlatform: byId("bootstrap-platform"),
  bootstrapSession: byId("bootstrap-session"),
  bootstrapUser: byId("bootstrap-user"),
  bootstrapGroup: byId("bootstrap-group"),
  bootstrapBot: byId("bootstrap-bot"),
  bootstrapLimit: byId("bootstrap-limit"),
  bootstrapDiagnosis: byId("bootstrap-diagnosis"),
  bootstrapSummary: byId("bootstrap-summary"),
  bootstrapPreview: byId("bootstrap-preview"),
  memoriesList: byId("memories-list"),
  query: byId("query"),
  status: byId("status"),
  type: byId("type"),
  newContent: byId("new-content"),
  newScope: byId("new-scope"),
  newOwner: byId("new-owner"),
  rawSummary: byId("raw-summary"),
  rawList: byId("raw-list"),
  jobsList: byId("jobs-list"),
  logsList: byId("logs-list"),
  importJson: byId("import-json"),
  importPreview: byId("import-preview"),
  details: byId("details"),
};

const api = makeApiClient();

bindEvents();
await boot();

async function boot() {
  els.runtimeMode.textContent = bridge ? "AstrBot Page" : "Standalone";
  try {
    if (bridge?.ready) await bridge.ready();
    await refreshAll();
    setConnection("已连接", "ok");
    hideBoot("已连接");
  } catch (error) {
    setConnection("连接异常", "error");
    showError(error, "boot");
    hideBoot("连接异常，查看诊断详情");
  }
}

function bindEvents() {
  bindButton("refresh-all", refreshAll);
  bindButton("refresh-contexts", loadContexts);
  bindButton("save-context", saveContext);
  bindButton("bootstrap-probe", probeBootstrap);
  bindButton("load-raw", loadRawMessages);
  bindButton("bootstrap-dry-run", () => startBootstrap(true));
  bindButton("bootstrap-start", () => startBootstrap(false));
  bindButton("refresh-memories", loadMemories);
  bindButton("search-memories", loadMemories);
  bindButton("create", createMemory);
  bindButton("refresh-raw", loadRawMessages);
  bindButton("refresh-jobs", loadJobs);
  bindButton("refresh-logs", loadOperationLogs);
  bindButton("export", exportMemories);
  bindButton("preview-import", previewImport);
  bindButton("import", importMemories);
  byId("clear-details").addEventListener("click", () => showDetails({}));
  els.contextSelect.addEventListener("change", applySelectedContext);
  els.newScope.addEventListener("change", updateOwnerHint);
  for (const item of document.querySelectorAll("[data-page-target]")) {
    item.addEventListener("click", () => showPage(item.dataset.pageTarget));
  }
}

function bindButton(id, fn) {
  byId(id).addEventListener("click", () => withButton(id, fn));
}

async function refreshAll() {
  const results = await Promise.allSettled([
    loadRuntimeMeta(),
    loadDiagnostics(),
    loadContexts(),
    loadMemories(),
    loadJobs(),
    loadOperationLogs(),
  ]);
  const failed = results.find((item) => item.status === "rejected");
  if (failed) throw failed.reason;
  renderDashboard();
  ensureContextSelected(false);
  await loadRawMessagesSafe();
}

async function loadRuntimeMeta() {
  try {
    state.meta = await api.get("runtime-meta", {});
  } catch (error) {
    state.meta = {
      api_version: "unknown",
      compatibility_warning: "后端未提供 runtime-meta，可能尚未重载到最新版本。",
    };
    await recordUiLog("runtime.meta", "warning", state.meta.compatibility_warning, routeSuggestion(error));
  }
}

async function loadDiagnostics() {
  try {
    state.diagnostics = await api.get("diagnostics", {});
    state.stats = state.diagnostics.stats || {};
  } catch (error) {
    state.diagnostics = { checks: [], error: error.message };
    await loadStatsFallback();
  }
}

async function loadStatsFallback() {
  state.stats = await api.get("stats", {});
}

async function loadContexts() {
  const result = await api.get("contexts", { limit: 100 });
  state.contexts = mergeContexts(result.contexts || [], loadLocalContexts());
  renderContexts();
  ensureContextSelected(false);
  renderDashboard();
}

async function saveContext() {
  const payload = bootstrapPayload();
  validateContextPayload(payload);
  try {
    const result = await api.post("contexts", payload);
    showDetails(result);
    await loadContexts();
    els.contextSelect.value = result.context?.unified_origin || payload.unified_origin;
    applySelectedContext(false);
    showToast("会话已保存", "ok");
  } catch (error) {
    if (!isRouteMissing(error)) throw error;
    const context = decorateLocalContext(payload);
    saveLocalContext(context);
    state.contexts = mergeContexts(state.contexts, [context]);
    renderContexts();
    els.contextSelect.value = context.unified_origin;
    applySelectedContext(false);
    showDetails({ compatibility: "POST /contexts missing, saved in localStorage", context });
    showToast("后端未提供会话保存接口，已临时保存在浏览器", "warn");
    await recordUiLog("context.save.local", "warning", "后端未提供会话保存接口，已使用 localStorage 兼容。", routeSuggestion(error));
  }
}

async function probeBootstrap() {
  ensureContextSelected();
  const payload = bootstrapPayload();
  validateBootstrapPayload(payload);
  try {
    const result = await api.post("bootstrap/probe", payload);
    showDetails(result);
    renderProbe(result);
    await recordUiLog("bootstrap.probe", "info", "历史预检完成", result.diagnosis?.suggestion || "", "", "", payload, result);
  } catch (error) {
    if (!isRouteMissing(error)) throw error;
    els.bootstrapDiagnosis.innerHTML = noticeHtml("warning", "后端未注册预检接口，自动改用 dry-run 兼容探测。", routeSuggestion(error));
    await startBootstrap(true, { compatibilityProbe: true });
  }
}

async function startBootstrap(dryRun, options = {}) {
  ensureContextSelected();
  const payload = bootstrapPayload();
  validateBootstrapPayload(payload);
  const route = dryRun ? "bootstrap/dry-run" : "bootstrap/start";
  const result = await api.post(route, payload);
  state.selectedJobId = result.job_id || "";
  showDetails(result);
  showToast(dryRun ? "Dry-run 已提交，正在等待结果" : "写入任务已提交", "ok");
  await recordUiLog(
    dryRun ? "bootstrap.dry_run.submit" : "bootstrap.start.submit",
    "info",
    dryRun ? "已提交历史初始化预览任务" : "已提交历史初始化写入任务",
    "",
    "",
    state.selectedJobId,
    payload,
    result,
  );
  await loadJobs();
  pollSelectedJob();
  if (options.compatibilityProbe) showPage("bootstrap");
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

async function createMemory() {
  ensureContextSelected(false);
  const content = els.newContent.value.trim();
  if (!content) throw new Error("记忆内容不能为空");
  const scope = els.newScope.value;
  const ownerKey = els.newOwner.value.trim() || ownerKeyForScope(scope, selectedContextFromForm());
  const result = await api.post("memories", {
    content,
    canonical_text: content,
    scope,
    owner_key: ownerKey,
    memory_type: guessMemoryType(content),
    importance: 0.72,
    confidence: 0.9,
  });
  els.newContent.value = "";
  els.newOwner.value = "";
  showDetails(result);
  await loadMemories();
  await loadOperationLogs();
  showToast("记忆已创建", "ok");
}

async function saveMemory(memory, card) {
  const content = card.querySelector("[data-edit-content]").value.trim();
  const importance = Number(card.querySelector("[data-edit-importance]").value || memory.importance);
  const confidence = Number(card.querySelector("[data-edit-confidence]").value || memory.confidence);
  const result = await api.post(`memories/${memory.memory_id}`, {
    content,
    canonical_text: content,
    tags: memory.tags || [],
    importance,
    confidence,
  });
  showDetails(result);
  await loadMemories();
  await loadOperationLogs();
  showToast("记忆已保存", "ok");
}

async function expireMemory(memory) {
  if (!confirm(`确认将记忆 ${memory.memory_id} 标记为过期？`)) return;
  const result = await api.post(`memories/${memory.memory_id}/expire`, {});
  showDetails(result);
  await loadMemories();
  await loadOperationLogs();
}

async function deleteMemory(memory) {
  if (!confirm(`确认删除记忆 ${memory.memory_id}？该操作会软删除并移除向量。`)) return;
  const result = await api.post(`memories/${memory.memory_id}/delete`, {});
  showDetails(result);
  await loadMemories();
  await loadOperationLogs();
}

async function showMemoryLogs(memory) {
  const result = await api.get(`memories/${memory.memory_id}/logs`, {});
  showDetails(result);
  showPage("diagnostics");
}

async function loadRawMessagesSafe() {
  try {
    await loadRawMessages();
  } catch (error) {
    els.rawList.innerHTML = emptyHtml("原始历史暂不可用：" + error.message);
  }
}

async function loadRawMessages() {
  ensureContextSelected(false);
  const context = selectedContextFromForm();
  if (!context.session_id && !context.group_id && !context.user_id) {
    els.rawList.innerHTML = emptyHtml("请选择会话后再查看原始历史。");
    return;
  }
  const result = await api.get("raw-messages", {
    session_id: context.session_id,
    group_id: context.group_id,
    user_id: context.user_id,
    platform_id: context.platform_id,
    limit: 80,
  });
  state.raw = result.messages || [];
  renderRawMessages(result);
}

async function loadJobs() {
  const result = await api.get("jobs", { limit: 30 });
  state.jobs = result.jobs || [];
  renderJobs();
  renderLatestBootstrapJob();
}

async function loadOperationLogs() {
  try {
    const result = await api.get("operation-logs", { limit: 100 });
    state.logs = result.logs || [];
  } catch (error) {
    state.logs = loadLocalLogs();
  }
  renderLogs();
}

async function exportMemories() {
  const result = await api.get("export", {});
  showDetails(result);
  showPage("diagnostics");
  showToast("导出完成，结果已显示在诊断详情", "ok");
}

function previewImport() {
  const payload = parseJson(els.importJson.value || "{}");
  const summary = {
    memories: (payload.memories || []).length,
    raw_messages: (payload.raw_messages || []).length,
    edges: (payload.edges || []).length,
  };
  els.importPreview.innerHTML = noticeHtml("ok", "JSON 可解析", `将导入 ${summary.memories} 条记忆、${summary.raw_messages} 条原始消息、${summary.edges} 条关系。`);
  showDetails({ import_preview: summary });
}

async function importMemories() {
  const payload = parseJson(els.importJson.value || "{}");
  previewImport();
  if (!confirm("确认导入这些 MemoryOS 数据？")) return;
  const result = await api.post("import", payload);
  showDetails(result);
  await refreshAll();
}

function pollSelectedJob() {
  clearInterval(state.pollTimer);
  if (!state.selectedJobId) return;
  state.pollTimer = setInterval(async () => {
    try {
      await loadJobs();
      const job = state.jobs.find((item) => item.job_id === state.selectedJobId);
      if (job && ["done", "failed", "cancelled"].includes(job.status)) {
        clearInterval(state.pollTimer);
        await loadOperationLogs();
      }
    } catch (error) {
      clearInterval(state.pollTimer);
      showError(error, "job.poll");
    }
  }, 1600);
}

function renderDashboard() {
  const stats = state.stats || {};
  els.metricActive.textContent = numberText(stats.active_memories);
  els.metricRaw.textContent = numberText(stats.raw_messages);
  els.metricContexts.textContent = numberText(stats.known_contexts ?? state.contexts.length);
  els.metricLogs.textContent = numberText(stats.operation_logs ?? state.logs.length);
  const checks = state.diagnostics.checks || fallbackChecks(stats);
  els.healthChecks.innerHTML = checks.map(checkCardHtml).join("");
  const diagnosis = state.diagnostics.latest_bootstrap_diagnosis;
  els.latestDiagnosis.innerHTML = diagnosis
    ? noticeHtml(diagnosis.level, diagnosis.message, diagnosis.suggestion)
    : noticeHtml("info", "暂无初始化任务", "选择会话并执行 dry-run 后，这里会显示诊断结果。");
}

function renderContexts() {
  const current = els.contextSelect.value;
  els.contextSelect.innerHTML = '<option value="">选择已知会话</option>';
  for (const context of state.contexts) {
    const option = document.createElement("option");
    option.value = context.unified_origin || "";
    option.textContent = context.display_name || contextLabel(context);
    els.contextSelect.appendChild(option);
  }
  if (current) els.contextSelect.value = current;
}

function renderProbe(result) {
  const diagnosis = result.diagnosis || {};
  els.bootstrapDiagnosis.innerHTML = noticeHtml(
    diagnosis.level || (result.can_bootstrap ? "ok" : "warning"),
    diagnosis.message || (result.can_bootstrap ? "历史可读" : "预检未通过"),
    diagnosis.suggestion || (result.errors || []).join("；"),
  );
  els.bootstrapSummary.innerHTML = summaryHtml([
    ["读取", result.read_messages],
    ["跳过", result.parse_skipped],
    ["conversation", result.conversation_id || "-"],
    ["错误", (result.errors || []).length],
  ]);
  if (result.messages?.length) renderRawMessages(result);
}

function renderMemories() {
  if (!state.memories.length) {
    els.memoriesList.innerHTML = emptyHtml("没有匹配的记忆。可以调整筛选或新建一条明确记忆。");
    return;
  }
  els.memoriesList.innerHTML = "";
  for (const memory of state.memories) {
    const card = document.createElement("article");
    card.className = "item-card memory-card";
    card.innerHTML = `
      <div class="item-head">
        <div>
          <strong>${escapeHtml(memory.content || "")}</strong>
          <p>${escapeHtml(memory.scope_label || `${memory.scope} / ${memory.owner_key}`)}</p>
        </div>
        <span class="badge">${escapeHtml(memory.status || "active")}</span>
      </div>
      <div class="meta-row">
        <span>${escapeHtml(memory.memory_id)}</span>
        <span>${escapeHtml(memory.memory_type)}</span>
        <span>${escapeHtml(memory.score_label || "")}</span>
      </div>
      <details>
        <summary>编辑与详情</summary>
        <label><span>内容</span><textarea data-edit-content>${escapeHtml(memory.content || "")}</textarea></label>
        <div class="form-grid compact-grid">
          <label><span>重要性</span><input data-edit-importance type="number" step="0.01" min="0" max="1" value="${escapeHtml(memory.importance ?? 0.7)}" /></label>
          <label><span>置信度</span><input data-edit-confidence type="number" step="0.01" min="0" max="1" value="${escapeHtml(memory.confidence ?? 0.9)}" /></label>
        </div>
        <pre>${escapeHtml(JSON.stringify(memory, null, 2))}</pre>
      </details>
      <div class="toolbar compact">
        <button data-action="save">保存</button>
        <button class="secondary" data-action="logs">召回日志</button>
        <button class="secondary" data-action="expire">过期</button>
        <button class="danger" data-action="delete">删除</button>
      </div>
    `;
    card.querySelector('[data-action="save"]').addEventListener("click", () => safeInline(() => saveMemory(memory, card)));
    card.querySelector('[data-action="logs"]').addEventListener("click", () => safeInline(() => showMemoryLogs(memory)));
    card.querySelector('[data-action="expire"]').addEventListener("click", () => safeInline(() => expireMemory(memory)));
    card.querySelector('[data-action="delete"]').addEventListener("click", () => safeInline(() => deleteMemory(memory)));
    els.memoriesList.appendChild(card);
  }
}

function renderRawMessages(result) {
  const summary = result.summary || {};
  els.rawSummary.innerHTML = summaryHtml([
    ["总数", summary.total ?? state.raw.length],
    ["可用", summary.usable ?? 0],
    ["噪声", summary.noisy ?? 0],
    ["噪声比例", summary.noise_ratio ?? 0],
  ]);
  const messages = result.messages || state.raw;
  if (!messages.length) {
    els.rawList.innerHTML = emptyHtml("没有原始历史。请先执行预检或确认 AstrBot 已保存该会话历史。");
    return;
  }
  els.rawList.innerHTML = messages
    .map((message) => `
      <article class="item-card ${message.noise_reason ? "is-noise" : ""}">
        <div class="item-head">
          <div><strong>${escapeHtml(message.role)} · ${escapeHtml(message.user_id || "-")}</strong><p>${escapeHtml(message.message_id || "")}</p></div>
          <span class="badge">${message.noise_reason ? "噪声" : "可用"}</span>
        </div>
        <p>${escapeHtml(message.content_preview || message.content || "")}</p>
        ${message.noise_reason ? `<div class="notice warning">${escapeHtml(message.noise_reason)}</div>` : ""}
      </article>
    `)
    .join("");
}

function renderJobs() {
  if (!state.jobs.length) {
    els.jobsList.innerHTML = emptyHtml("暂无后台任务。");
    return;
  }
  els.jobsList.innerHTML = "";
  for (const job of state.jobs) {
    const result = job.result || {};
    const diagnosis = result.diagnosis || {};
    const card = document.createElement("article");
    card.className = "item-card";
    card.innerHTML = `
      <div class="item-head">
        <div><strong>${escapeHtml(job.job_type)}</strong><p>${escapeHtml(job.job_id)}</p></div>
        <span class="badge ${job.status}">${escapeHtml(job.status)}</span>
      </div>
      <div class="summary-grid">${summaryHtml([
        ["读取", result.read_messages ?? "-"],
        ["候选", result.candidate_count ?? "-"],
        ["写入", result.stored_count ?? "-"],
        ["失败块", result.failed_chunks ?? "-"],
      ])}</div>
      ${diagnosis.message ? noticeHtml(diagnosis.level, diagnosis.message, diagnosis.suggestion) : ""}
      <div class="toolbar compact">
        <button class="secondary" data-action="details">详情</button>
        <button class="secondary" data-action="select">设为当前任务</button>
        <button class="danger" data-action="cancel">取消</button>
      </div>
    `;
    card.querySelector('[data-action="details"]').addEventListener("click", () => {
      showDetails(job);
      if (job.job_type === "bootstrap_history") renderBootstrapJob(job);
      showPage("diagnostics");
    });
    card.querySelector('[data-action="select"]').addEventListener("click", () => {
      state.selectedJobId = job.job_id || "";
      renderBootstrapJob(job);
      showPage("bootstrap");
    });
    card.querySelector('[data-action="cancel"]').addEventListener("click", () => safeInline(() => cancelJob(job.job_id)));
    els.jobsList.appendChild(card);
  }
}

async function cancelJob(jobId) {
  if (!jobId) return;
  const result = await api.post("bootstrap/cancel", { job_id: jobId });
  showDetails(result);
  await loadJobs();
  await loadOperationLogs();
}

function renderLatestBootstrapJob() {
  const job =
    state.jobs.find((item) => item.job_id === state.selectedJobId) ||
    state.jobs.find((item) => item.job_type === "bootstrap_history");
  if (job) renderBootstrapJob(job);
}

function renderBootstrapJob(job) {
  const result = job.result || {};
  const diagnosis = result.diagnosis || {};
  els.bootstrapDiagnosis.innerHTML = diagnosis.message
    ? noticeHtml(diagnosis.level, diagnosis.message, diagnosis.suggestion)
    : noticeHtml("info", `任务 ${job.status}`, "查看任务详情了解完整结果。");
  els.bootstrapSummary.innerHTML = summaryHtml([
    ["状态", job.status],
    ["读取", result.read_messages ?? 0],
    ["候选", result.candidate_count ?? 0],
    ["写入", result.stored_count ?? 0],
    ["空块", result.empty_candidate_chunks ?? 0],
    ["低置信", result.skipped_low_confidence ?? 0],
    ["低重要", result.skipped_low_importance ?? 0],
    ["非法作用域", result.skipped_invalid_scope ?? 0],
  ]);
  const preview = result.preview || [];
  if (!preview.length) {
    els.bootstrapPreview.innerHTML = emptyHtml("没有候选记忆。请查看上方诊断和“原始历史”的噪声标记。");
    return;
  }
  els.bootstrapPreview.innerHTML = preview
    .map((item, index) => `
      <article class="item-card">
        <div class="item-head"><strong>#${index + 1} ${escapeHtml(item.memory_type || "fact")}</strong><span class="badge">${escapeHtml(item.scope || "")}</span></div>
        <p>${escapeHtml(item.content || item.canonical_text || "")}</p>
        <div class="meta-row"><span>importance ${score(item.importance)}</span><span>confidence ${score(item.confidence)}</span><span>${escapeHtml(item.reason || "")}</span></div>
      </article>
    `)
    .join("");
}

function renderLogs() {
  if (!state.logs.length) {
    els.logsList.innerHTML = emptyHtml("暂无操作日志。新的错误和操作会自动记录。");
    return;
  }
  els.logsList.innerHTML = state.logs
    .map((log) => `
      <article class="item-card">
        <div class="item-head"><strong>${escapeHtml(log.message || log.action)}</strong><span class="badge ${escapeHtml(log.level)}">${escapeHtml(log.level)}</span></div>
        <div class="meta-row"><span>${escapeHtml(log.action)}</span><span>${escapeHtml(log.code || "")}</span><span>${formatTime(log.created_at)}</span></div>
        ${log.suggestion ? `<div class="notice info">${escapeHtml(log.suggestion)}</div>` : ""}
        <details><summary>原始详情</summary><pre>${escapeHtml(JSON.stringify(log, null, 2))}</pre></details>
      </article>
    `)
    .join("");
}

function showPage(id) {
  for (const page of document.querySelectorAll(".page")) page.classList.toggle("active", page.id === id);
  for (const nav of document.querySelectorAll("[data-page-target]")) nav.classList.toggle("active", nav.dataset.pageTarget === id);
  const copy = pageCopy[id] || pageCopy.dashboard;
  els.pageTitle.textContent = copy[0];
  els.pageSubtitle.textContent = copy[1];
}

async function withButton(id, fn) {
  const button = byId(id);
  button.disabled = true;
  try {
    await fn();
    setConnection("已连接", "ok");
  } catch (error) {
    showError(error, id);
    setConnection("请求失败", "error");
  } finally {
    button.disabled = false;
  }
}

async function safeInline(fn) {
  try {
    await fn();
  } catch (error) {
    showError(error, "inline");
  }
}

function makeApiClient() {
  if (bridge) {
    return {
      async get(route, params) {
        return bridgeCall("GET", route, params || {});
      },
      async post(route, body) {
        return bridgeCall("POST", route, body || {});
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

async function bridgeCall(method, route, payload) {
  const attempts = [route, `/${PLUGIN_NAME}/${route}`, `${PLUGIN_NAME}/${route}`];
  let lastError;
  for (const candidate of attempts) {
    try {
      const raw = method === "GET"
        ? await bridge.apiGet(candidate, payload)
        : await bridge.apiPost(candidate, payload);
      return normalizeResponse(raw);
    } catch (error) {
      lastError = error;
      if (!isRouteMissing(error)) throw error;
    }
  }
  throw lastError || new Error(`未找到该路由：${route}`);
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
  if (payload && payload.ok === false) throw new Error(payload.error?.message || `请求失败：${status}`);
  if (payload && payload.error) {
    throw new Error(typeof payload.error === "string" ? payload.error : payload.error.message || `请求失败：${status}`);
  }
  if (status >= 400) throw new Error(`请求失败：${status}`);
  return payload || {};
}

async function recordUiLog(action, level, message, suggestion = "", code = "", jobId = "", request = {}, response = {}) {
  const entry = { action, level, message, suggestion, code, job_id: jobId, request, response };
  saveLocalLog(entry);
  try {
    await api.post("operation-logs", entry);
  } catch (error) {
    // Logging must not break the user's original operation.
  }
}

function showError(error, action = "ui.error") {
  const message = error?.message || String(error);
  const suggestion = routeSuggestion(error) || genericSuggestion(message);
  showToast(message, "error");
  showDetails({ error: message, suggestion, action });
  recordUiLog(action, "error", message, suggestion);
}

function routeSuggestion(error) {
  if (!isRouteMissing(error)) return "";
  return "后端路由缺失通常表示插件 Python 进程还未重载。重启/热重载 MemoryOS 插件后再刷新页面。";
}

function genericSuggestion(message) {
  if (message.includes("unified_origin")) return "先选择已知会话，或填写并保存会话字段。";
  if (message.includes("JSON")) return "检查导入内容是否是完整 JSON。";
  if (message.includes("历史")) return "检查会话字段、AstrBot 历史保存和 LLM provider。";
  return "查看操作日志和诊断详情中的原始响应。";
}

function isRouteMissing(error) {
  const message = error?.message || String(error || "");
  return message.includes("未找到该路由") || message.includes("没有找到接口") || message.includes("not_found") || message.includes("404");
}

function bootstrapPayload() {
  return {
    unified_origin: els.bootstrapOrigin.value.trim(),
    platform_id: els.bootstrapPlatform.value.trim(),
    session_id: els.bootstrapSession.value.trim(),
    user_id: els.bootstrapUser.value.trim(),
    group_id: els.bootstrapGroup.value.trim(),
    bot_id: els.bootstrapBot.value.trim() || "bot",
    limit: Number(els.bootstrapLimit.value || 300),
  };
}

function validateContextPayload(payload) {
  for (const [key, message] of [
    ["unified_origin", "请先选择会话或填写 unified_origin"],
    ["platform_id", "请填写 platform_id，例如 aiocqhttp"],
    ["session_id", "请填写 session_id"],
    ["user_id", "请填写 user_id"],
  ]) {
    if (!payload[key]) throw new Error(message);
  }
}

function validateBootstrapPayload(payload) {
  validateContextPayload(payload);
  if (!Number.isFinite(payload.limit) || payload.limit < 1) throw new Error("读取数量必须大于 0");
}

function selectedContextFromForm() {
  return bootstrapPayload();
}

function applySelectedContext() {
  const selected = state.contexts.find((item) => item.unified_origin === els.contextSelect.value);
  if (!selected) return;
  els.bootstrapOrigin.value = selected.unified_origin || "";
  els.bootstrapPlatform.value = selected.platform_id || "";
  els.bootstrapSession.value = selected.session_id || "";
  els.bootstrapUser.value = selected.user_id || "";
  els.bootstrapGroup.value = selected.group_id || "";
  els.bootstrapBot.value = selected.bot_id || "bot";
  updateOwnerHint();
}

function ensureContextSelected(showMessage = true) {
  if (els.bootstrapOrigin.value.trim()) return true;
  const selected = state.contexts.find((item) => item.unified_origin === els.contextSelect.value) || state.contexts[0];
  if (!selected) return false;
  els.contextSelect.value = selected.unified_origin || "";
  applySelectedContext();
  if (showMessage) showToast("已自动选择最近会话", "ok");
  return true;
}

function updateOwnerHint() {
  const scope = els.newScope.value;
  const owner = ownerKeyForScope(scope, selectedContextFromForm());
  els.newOwner.placeholder = owner || "owner_key";
}

function ownerKeyForScope(scope, context) {
  const platform = context.platform_id || "unknown";
  if (scope === "global") return "global";
  if (scope === "user_private") return `${platform}:private:${context.user_id || "unknown_user"}`;
  if (scope === "group_shared") return `${platform}:group:${context.group_id || "unknown_group"}`;
  if (scope === "user_in_group") return `${platform}:group:${context.group_id || "unknown_group"}:user:${context.user_id || "unknown_user"}`;
  if (scope === "session") return `${platform}:session:${context.session_id || "unknown_session"}`;
  if (scope === "persona") return "persona:default";
  return "global";
}

function mergeContexts(primary, fallback) {
  const map = new Map();
  for (const context of [...fallback, ...primary]) {
    if (!context?.unified_origin) continue;
    map.set(context.unified_origin, { ...map.get(context.unified_origin), ...context });
  }
  return [...map.values()].sort((a, b) => Number(b.updated_at || 0) - Number(a.updated_at || 0));
}

function decorateLocalContext(payload) {
  const context = { ...payload, is_group: Boolean(payload.group_id), updated_at: Date.now(), source: "browser_local" };
  context.display_name = contextLabel(context);
  return context;
}

function contextLabel(context) {
  const target = context.group_id ? `群 ${context.group_id}` : `用户 ${context.user_id || "-"}`;
  return `${context.platform_id || "unknown"} · ${target} · ${context.unified_origin || ""}`;
}

function loadLocalContexts() {
  return safeLocalJson("memoryos.contexts", []);
}

function saveLocalContext(context) {
  localStorage.setItem("memoryos.contexts", JSON.stringify(mergeContexts(loadLocalContexts(), [context]).slice(0, 30)));
}

function saveLocalLog(entry) {
  const logs = [{ ...entry, created_at: Date.now(), source: "browser_local" }, ...loadLocalLogs()].slice(0, 100);
  localStorage.setItem("memoryos.logs", JSON.stringify(logs));
}

function loadLocalLogs() {
  return safeLocalJson("memoryos.logs", []);
}

function safeLocalJson(key, fallback) {
  try {
    const value = JSON.parse(localStorage.getItem(key) || JSON.stringify(fallback));
    return Array.isArray(fallback) ? (Array.isArray(value) ? value : fallback) : value;
  } catch (error) {
    return fallback;
  }
}

function fallbackChecks(stats) {
  return [
    { name: "API", status: state.meta.compatibility_warning ? "warning" : "ok", message: state.meta.api_version || "unknown", suggestion: state.meta.compatibility_warning || "" },
    { name: "LLM", status: stats.last_llm_error ? "warning" : "ok", message: stats.llm_provider_id || "跟随当前会话", suggestion: stats.last_llm_error || "" },
    { name: "Embedding", status: stats.embedding_provider_id ? "ok" : "warning", message: stats.embedding_provider_id || "未配置，关键词检索", suggestion: stats.embedding_provider_id ? "" : "需要语义检索时配置 embedding_provider_id。" },
  ];
}

function checkCardHtml(check) {
  return `<article class="status-card ${escapeHtml(check.status)}"><span>${escapeHtml(check.name)}</span><strong>${escapeHtml(check.message)}</strong>${check.suggestion ? `<p>${escapeHtml(check.suggestion)}</p>` : ""}</article>`;
}

function noticeHtml(level, message, suggestion = "") {
  return `<div class="notice ${escapeHtml(level || "info")}"><strong>${escapeHtml(message || "")}</strong>${suggestion ? `<p>${escapeHtml(suggestion)}</p>` : ""}</div>`;
}

function summaryHtml(items) {
  return items.map(([label, value]) => `<div class="summary-item"><span>${escapeHtml(label)}</span><strong>${escapeHtml(numberText(value))}</strong></div>`).join("");
}

function emptyHtml(text) {
  return `<div class="empty-state">${escapeHtml(text)}</div>`;
}

function showDetails(value) {
  els.details.textContent = JSON.stringify(value || {}, null, 2);
}

function showToast(message, kind = "info") {
  els.toast.hidden = false;
  els.toast.dataset.kind = kind;
  els.toast.textContent = message;
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => {
    els.toast.hidden = true;
  }, 5200);
}

function setConnection(text, kind) {
  els.connectionStatus.textContent = text;
  els.connectionStatus.dataset.kind = kind;
}

function hideBoot(text) {
  els.bootText.textContent = text;
  setTimeout(() => els.boot.classList.add("is-hidden"), 260);
}

function parseJson(text) {
  try {
    return JSON.parse(text);
  } catch (error) {
    throw new Error("JSON 格式无效：" + error.message);
  }
}

function guessMemoryType(text) {
  if (/喜欢|偏好|prefer/i.test(text)) return "preference";
  if (/项目|插件|实现|架构|project|plugin/i.test(text)) return "project_state";
  if (/叫我|call me/i.test(text)) return "nickname";
  if (/不要|不是|纠正|correction/i.test(text)) return "correction";
  return "fact";
}

function numberText(value) {
  if (value === undefined || value === null || value === "") return "0";
  return String(value);
}

function score(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n.toFixed(2) : "-";
}

function formatTime(ms) {
  const n = Number(ms);
  if (!Number.isFinite(n) || n <= 0) return "-";
  return new Date(n).toLocaleString();
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function byId(id) {
  return document.getElementById(id);
}
