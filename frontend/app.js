const API = "/api";
const $ = (s) => document.querySelector(s);
const CHAT_KEY = "rag_agent_chat_v1";

const els = {
  messages: $("#messages"),
  empty: $("#empty"),
  chat: $("#chat"),
  clearChatBtn: $("#clear-chat"),
  input: $("#input"),
  send: $("#send"),
  form: $("#composer"),
  model: $("#model"),
  status: $("#status-bar"),
  chunkStat: $("#chunk-stat"),
  ingestBtn: $("#ingest-btn"),
  ingestMsg: $("#ingest-msg"),
  fileInput: $("#file-input"),
  keyWarning: $("#key-warning"),
  chatFile: $("#chat-file"),
  attachBar: $("#attach-bar"),
  attachName: $("#attach-name"),
  attachClear: $("#attach-clear"),
};

/** @type {{role:string, content:string}[]} */
let history = [];
let pendingAttachment = null; // {filename, url, original_name}

marked.setOptions({ gfm: true, breaks: true });
const md = (t) => DOMPurify.sanitize(marked.parse(t || ""));

function scrollDown() {
  els.chat.scrollTop = els.chat.scrollHeight;
}

function buildChatPayload() {
  const rows = [...els.messages.querySelectorAll(".msg")].map((row) => {
    const role = row.classList.contains("user") ? "user" : "assistant";
    const bubble = row.querySelector(".bubble");
    const text = bubble ? bubble.innerText || "" : "";
    const html = bubble ? bubble.innerHTML || "" : "";
    const files = [...row.querySelectorAll("a.file-card-btn")].map((a) => ({
      url: a.getAttribute("href") || "",
      filename:
        a.getAttribute("download") ||
        (a.getAttribute("href") || "").split("/").pop() ||
        "",
      title:
        (a.closest(".file-card") &&
          a.closest(".file-card").querySelector(".file-card-title") &&
          a.closest(".file-card").querySelector(".file-card-title").textContent) ||
        "File ready",
    }));
    return { role, text, html, files };
  });
  return { history, rows, savedAt: Date.now() };
}

function saveChat() {
  try {
    const payload = JSON.stringify(buildChatPayload());
    try { localStorage.setItem(CHAT_KEY, payload); } catch (e) { console.warn("localStorage", e); }
    try { sessionStorage.setItem(CHAT_KEY, payload); } catch (e) { console.warn("sessionStorage", e); }
    // survives same-tab refresh even if storage is blocked
    try { window.name = "RAGCHAT::" + payload; } catch (_) {}
  } catch (e) {
    console.warn("saveChat failed", e);
  }
}

function clearChat() {
  history = [];
  els.messages.innerHTML = "";
  els.empty.hidden = false;
  try { localStorage.removeItem(CHAT_KEY); } catch (_) {}
  try { sessionStorage.removeItem(CHAT_KEY); } catch (_) {}
  try { if (window.name.startsWith("RAGCHAT::")) window.name = ""; } catch (_) {}
}

function addUser(text, skipSave) {
  els.empty.hidden = true;
  const row = document.createElement("div");
  row.className = "msg user";
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = text;
  row.appendChild(bubble);
  els.messages.appendChild(row);
  scrollDown();
  if (!skipSave) saveChat();
}

function addAssistantShell() {
  const row = document.createElement("div");
  row.className = "msg assistant";
  row.innerHTML = `
    <div class="agent-status" hidden>
      <span class="status-spinner"></span>
      <span class="status-text">Thinking…</span>
    </div>
    <div class="steps" hidden></div>
    <div class="bubble"></div>
    <div class="file-cards" hidden></div>
    <div class="sources" hidden></div>
  `;
  els.messages.appendChild(row);
  scrollDown();
  return {
    row,
    status: row.querySelector(".agent-status"),
    statusText: row.querySelector(".status-text"),
    steps: row.querySelector(".steps"),
    bubble: row.querySelector(".bubble"),
    fileCards: row.querySelector(".file-cards"),
    sources: row.querySelector(".sources"),
  };
}

function showThinking(ui, label) {
  ui.status.hidden = false;
  ui.statusText.textContent = label || "Thinking…";
  ui.status.classList.add("active");
}

function hideThinking(ui) {
  ui.status.hidden = true;
  ui.status.classList.remove("active");
}

function addStep(ui, kind, text) {
  ui.steps.hidden = false;
  const chip = document.createElement("div");
  chip.className = `step-chip step-${kind}`;
  const icons = { docs: "🔍", web: "🌐", file: "📄", think: "✨", calc: "🔢" };
  chip.innerHTML = `<span class="step-icon">${icons[kind] || "•"}</span><span></span>`;
  chip.querySelector("span:last-child").textContent = text;
  ui.steps.appendChild(chip);
  scrollDown();
}

function fileMeta(file) {
  const name = (file.filename || "").toLowerCase();
  const type = (file.type || "").toLowerCase();
  if (type === "pdf" || name.endsWith(".pdf")) return { icon: "📄", title: "PDF ready" };
  if (type === "docx" || name.endsWith(".docx")) return { icon: "📝", title: "Word document ready" };
  if (type === "xlsx" || name.endsWith(".xlsx")) return { icon: "📊", title: "Excel spreadsheet ready" };
  if (type === "csv" || name.endsWith(".csv")) return { icon: "📑", title: "CSV file ready" };
  return { icon: "📎", title: file.title || "File ready" };
}

function addDownloadCard(ui, file) {
  if (!file || !(file.filename || file.url)) return;
  ui.fileCards.hidden = false;
  const meta = fileMeta(file);
  const href = file.url || "/api/files/" + file.filename;
  const name = file.filename || href.split("/").pop();
  const card = document.createElement("div");
  card.className = "file-card";
  card.innerHTML = `
    <div class="file-card-icon">${meta.icon}</div>
    <div class="file-card-body">
      <div class="file-card-title"></div>
      <div class="file-card-name"></div>
    </div>
    <a class="file-card-btn" href="${href}" download="${name}" target="_blank" rel="noopener">Download</a>
  `;
  card.querySelector(".file-card-title").textContent = meta.title;
  card.querySelector(".file-card-name").textContent = name;
  ui.fileCards.appendChild(card);
  scrollDown();
}

function restoreChat() {
  try {
    let raw = null;
    try { raw = localStorage.getItem(CHAT_KEY); } catch (_) {}
    if (!raw) {
      try { raw = sessionStorage.getItem(CHAT_KEY); } catch (_) {}
    }
    if (!raw && typeof window.name === "string" && window.name.startsWith("RAGCHAT::")) {
      raw = window.name.slice("RAGCHAT::".length);
    }
    if (!raw) return false;
    const data = JSON.parse(raw);
    history = Array.isArray(data.history) ? data.history.slice() : [];
    const rows = Array.isArray(data.rows) ? data.rows : [];
    if (!rows.length) return false;

    els.messages.innerHTML = "";
    els.empty.hidden = true;

    rows.forEach((r) => {
      if (r.role === "user") {
        const row = document.createElement("div");
        row.className = "msg user";
        const bubble = document.createElement("div");
        bubble.className = "bubble";
        bubble.textContent = r.text || "";
        row.appendChild(bubble);
        els.messages.appendChild(row);
      } else {
        const ui = addAssistantShell();
        hideThinking(ui);
        if (r.html) ui.bubble.innerHTML = r.html;
        else if (r.text) ui.bubble.innerHTML = md(r.text);
        (r.files || []).forEach((f) => addDownloadCard(ui, f));
      }
    });
    scrollDown();
    return true;
  } catch (e) {
    console.warn("restoreChat failed", e);
    return false;
  }
}

function setStatus(msg) {
  if (!msg) {
    els.status.hidden = true;
    els.status.textContent = "";
    return;
  }
  els.status.hidden = false;
  els.status.innerHTML = `<span class="status-spinner tiny"></span> ${msg}`;
}

function updateSend() {
  els.send.disabled = !els.input.value.trim() || els.send.dataset.busy === "1";
}

async function refreshStats() {
  try {
    const h = await (await fetch(`${API}/health`)).json();
    els.chunkStat.textContent = `Chunks: ${h.chunks ?? 0}`;
    els.keyWarning.hidden = h.has_key;
  } catch {
    els.chunkStat.textContent = "Chunks: ?";
  }
}

async function sendMessage(text) {
  text = text.trim();
  if (els.send.dataset.busy === "1") return;
  if (!text && pendingAttachment) text = "Summarize this attached file.";
  if (!text) return;

  addUser(text, true);
  history.push({ role: "user", content: text });
  els.input.value = "";
  updateSend();
  saveChat();

  const ui = addAssistantShell();
  showThinking(ui, "Thinking…");
  els.send.dataset.busy = "1";
  updateSend();

  let reply = "";
  const seenFiles = new Set();

  try {
    const res = await fetch(`${API}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message: text,
        model: els.model.value,
        history: history.slice(0, -1),
        attachment: pendingAttachment ? pendingAttachment.filename : null,
      }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let idx;
      while ((idx = buffer.indexOf("\n\n")) !== -1) {
        const line = buffer.slice(0, idx).trim();
        buffer = buffer.slice(idx + 2);
        if (!line.startsWith("data:")) continue;
        let ev;
        try {
          ev = JSON.parse(line.slice(5));
        } catch {
          continue;
        }

        if (ev.type === "status") {
          showThinking(ui, ev.message || "Working…");
          setStatus(ev.message);
        } else if (ev.type === "tool") {
          const q = ev.query || "";
          if (q.startsWith("[file]")) {
            showThinking(ui, "Working on file…");
            addStep(ui, "file", q.replace("[file]", "").trim() || "File");
          } else if (q.startsWith("[web]")) {
            showThinking(ui, "Searching the web…");
            addStep(ui, "web", q.replace("[web]", "").trim() || "Web");
          } else if (q.startsWith("[docs]")) {
            showThinking(ui, "Searching documents…");
            addStep(ui, "docs", q.replace("[docs]", "").trim() || "Docs");
          } else if (q.startsWith("[calc]")) {
            showThinking(ui, "Calculating…");
            addStep(ui, "calc", q.replace("[calc]", "").trim() || "Calc");
          } else {
            showThinking(ui, "Using tools…");
            addStep(ui, "think", q);
          }
          setStatus(q);
        } else if (ev.type === "file") {
          const f = ev.file || {};
          const key = f.filename || f.url;
          if (key && !seenFiles.has(key)) {
            seenFiles.add(key);
            showThinking(ui, "File ready ✓");
            addDownloadCard(ui, f);
            saveChat();
          }
        } else if (ev.type === "delta") {
          hideThinking(ui);
          setStatus(null);
          reply += ev.content;
          ui.bubble.innerHTML = md(reply);
          scrollDown();
        } else if (ev.type === "done") {
          hideThinking(ui);
          setStatus(null);
          if (ev.sources && ev.sources.length) {
            ui.sources.hidden = false;
            ui.sources.innerHTML =
              "Sources: " +
              ev.sources.map((s) => `<span>${s}</span>`).join("");
          }
          (ev.files || []).forEach((f) => {
            const key = f.filename || f.url;
            if (key && !seenFiles.has(key)) {
              seenFiles.add(key);
              addDownloadCard(ui, f);
            }
          });
        } else if (ev.type === "error") {
          hideThinking(ui);
          ui.bubble.innerHTML = `<div class="error">${ev.message}</div>`;
        }
      }
    }

    if (reply) history.push({ role: "assistant", content: reply });
  } catch (e) {
    hideThinking(ui);
    ui.bubble.innerHTML = `<div class="error">${e.message || "Network error"}</div>`;
  } finally {
    els.send.dataset.busy = "0";
    updateSend();
    setStatus(null);
    // keep attachment for multi-turn edit unless user clears
    saveChat();
  }
}

els.form.addEventListener("submit", (e) => {
  e.preventDefault();
  sendMessage(els.input.value);
});
els.input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendMessage(els.input.value);
  }
});
els.input.addEventListener("input", updateSend);

document.querySelectorAll(".chip").forEach((c) =>
  c.addEventListener("click", () => sendMessage(c.dataset.q))
);


if (els.chatFile) {
  els.chatFile.addEventListener("change", async () => {
    const file = els.chatFile.files[0];
    if (!file) return;
    els.attachName.textContent = "Uploading " + file.name + "…";
    els.attachBar.hidden = false;
    const fd = new FormData();
    fd.append("file", file);
    try {
      const res = await fetch(API + "/chat-upload", { method: "POST", body: fd });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Upload failed");
      pendingAttachment = data;
      els.attachName.textContent = "Attached: " + (data.original_name || data.filename);
      els.input.placeholder = "Ask to summarize, edit, or update this file…";
      updateSend();
    } catch (e) {
      els.attachName.textContent = e.message || "Upload failed";
      pendingAttachment = null;
    }
    els.chatFile.value = "";
  });
}
if (els.attachClear) {
  els.attachClear.addEventListener("click", () => {
    pendingAttachment = null;
    els.attachBar.hidden = true;
    els.attachName.textContent = "";
    els.input.placeholder = "Ask something… or attach a file to edit";
  });
}

if (els.clearChatBtn) {
  els.clearChatBtn.addEventListener("click", () => {
    if (confirm("Clear all chat messages?")) clearChat();
  });
}

els.ingestBtn.addEventListener("click", async () => {
  els.ingestMsg.textContent = "Ingesting…";
  try {
    const res = await fetch(`${API}/ingest`, { method: "POST" });
    const data = await res.json();
    els.ingestMsg.textContent = `Done: ${data.files} files, ${data.chunks} chunks`;
    refreshStats();
  } catch {
    els.ingestMsg.textContent = "Ingest failed — is the server running?";
  }
});

els.fileInput.addEventListener("change", async () => {
  const file = els.fileInput.files[0];
  if (!file) return;
  els.ingestMsg.textContent = `Uploading ${file.name}…`;
  const fd = new FormData();
  fd.append("file", file);
  try {
    const res = await fetch(`${API}/upload`, { method: "POST", body: fd });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Upload failed");
    els.ingestMsg.textContent = `Uploaded ${data.file}: ${data.chunks} chunks`;
    refreshStats();
  } catch (e) {
    els.ingestMsg.textContent = e.message || "Upload failed";
  }
  els.fileInput.value = "";
});

(async function init() {
  try {
    const { models, default: def } = await (await fetch(`${API}/models`)).json();
    models.forEach((m) => els.model.add(new Option(m.label, m.id)));
    els.model.value = def;
  } catch {
    els.model.add(new Option("Server offline", ""));
  }
  await refreshStats();
  restoreChat();
  els.input.focus();
})();
