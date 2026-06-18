const bridge = window.AstrBotPluginPage;
const state = {
  memories: [],
};

const els = {
  stats: document.getElementById("stats"),
  memories: document.getElementById("memories"),
  jobs: document.getElementById("jobs"),
  details: document.getElementById("details"),
  query: document.getElementById("query"),
  status: document.getElementById("status"),
  type: document.getElementById("type"),
  newContent: document.getElementById("new-content"),
  newScope: document.getElementById("new-scope"),
  newOwner: document.getElementById("new-owner"),
  importJson: document.getElementById("import-json"),
};

await bridge.ready();
await refreshAll();

document.getElementById("refresh").addEventListener("click", refreshAll);
document.getElementById("rebuild").addEventListener("click", async () => {
  const result = await bridge.apiPost("rebuild-index", {});
  els.details.textContent = JSON.stringify(result, null, 2);
  await loadJobs();
});
document.getElementById("export").addEventListener("click", async () => {
  const result = await bridge.apiGet("export", {});
  els.details.textContent = JSON.stringify(result, null, 2);
});
document.getElementById("create").addEventListener("click", createMemory);
document.getElementById("import").addEventListener("click", importMemories);
for (const input of [els.query, els.status, els.type]) {
  input.addEventListener("change", loadMemories);
  input.addEventListener("keyup", debounce(loadMemories, 250));
}

async function refreshAll() {
  await Promise.all([loadStats(), loadMemories(), loadJobs()]);
}

async function loadStats() {
  const stats = await bridge.apiGet("stats", {});
  els.stats.textContent = `active ${stats.active_memories} · vectors ${stats.vectors} · fts ${stats.fts_enabled ? "on" : "off"} · embedding ${stats.embedding_available ? "on" : "keyword fallback"}`;
}

async function loadMemories() {
  const result = await bridge.apiGet("memories", {
    q: els.query.value,
    status: els.status.value,
    type: els.type.value,
    limit: 100,
  });
  state.memories = result.memories || [];
  renderMemories();
}

async function loadJobs() {
  const result = await bridge.apiGet("jobs", {});
  els.jobs.textContent = JSON.stringify(result.jobs || [], null, 2);
}

function renderMemories() {
  els.memories.innerHTML = "";
  for (const memory of state.memories) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${escapeHtml(memory.memory_id)}</td>
      <td>${escapeHtml(memory.scope)}<br>${escapeHtml(memory.owner_key)}</td>
      <td>${escapeHtml(memory.memory_type)}</td>
      <td>${Number(memory.importance).toFixed(2)} / ${Number(memory.confidence).toFixed(2)}</td>
      <td><textarea data-field="content">${escapeHtml(memory.content)}</textarea></td>
      <td class="row-actions">
        <button data-action="save">Save</button>
        <button class="secondary" data-action="logs">Logs</button>
        <button class="secondary" data-action="expire">Expire</button>
        <button class="secondary" data-action="delete">Delete</button>
      </td>
    `;
    tr.querySelector('[data-action="save"]').addEventListener("click", () =>
      saveMemory(memory, tr),
    );
    tr.querySelector('[data-action="logs"]').addEventListener("click", () =>
      showLogs(memory),
    );
    tr.querySelector('[data-action="expire"]').addEventListener("click", () =>
      expireMemory(memory),
    );
    tr.querySelector('[data-action="delete"]').addEventListener("click", () =>
      deleteMemory(memory),
    );
    els.memories.appendChild(tr);
  }
}

async function createMemory() {
  const content = els.newContent.value.trim();
  if (!content) return;
  const result = await bridge.apiPost("memories", {
    content,
    canonical_text: content,
    scope: els.newScope.value,
    owner_key: els.newOwner.value || "global",
    memory_type: "fact",
    importance: 0.7,
    confidence: 0.9,
  });
  els.details.textContent = JSON.stringify(result, null, 2);
  els.newContent.value = "";
  await refreshAll();
}

async function saveMemory(memory, row) {
  const content = row.querySelector('[data-field="content"]').value;
  const result = await bridge.apiPost(`memories/${memory.memory_id}`, {
    content,
    canonical_text: content,
    tags: memory.tags || [],
    importance: memory.importance,
    confidence: memory.confidence,
  });
  els.details.textContent = JSON.stringify(result, null, 2);
  await loadMemories();
}

async function showLogs(memory) {
  const result = await bridge.apiGet(`memories/${memory.memory_id}/logs`, {});
  els.details.textContent = JSON.stringify(result, null, 2);
}

async function expireMemory(memory) {
  const result = await bridge.apiPost(`memories/${memory.memory_id}/expire`, {});
  els.details.textContent = JSON.stringify(result, null, 2);
  await loadMemories();
}

async function deleteMemory(memory) {
  const result = await bridge.apiPost(`memories/${memory.memory_id}/delete`, {});
  els.details.textContent = JSON.stringify(result, null, 2);
  await loadMemories();
}

async function importMemories() {
  const payload = JSON.parse(els.importJson.value || "{}");
  const result = await bridge.apiPost("import", payload);
  els.details.textContent = JSON.stringify(result, null, 2);
  await refreshAll();
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
