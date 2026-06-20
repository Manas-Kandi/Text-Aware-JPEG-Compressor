const API_BASE = location.protocol === 'file:' ? 'http://127.0.0.1:8000' : location.origin;
const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];
const THEME_KEY = 'piper-theme';

let state = { messages: [], tasks: [], notes: [], memories: [], edges: [], latest_benchmark: null, config: {} };
let artifacts = [];
let busy = false;
let activeWsTab = 'files';

document.addEventListener('DOMContentLoaded', init);

async function init() {
  loadTheme();
  bindEvents();
  updateSendButton();
  await loadState();
  await loadArtifacts();
}

/* ===== Theme ===== */
function loadTheme() {
  const stored = localStorage.getItem(THEME_KEY);
  const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
  document.documentElement.setAttribute('data-theme', stored ? stored : (prefersDark ? 'dark' : 'light'));
}
function toggleTheme() {
  const current = document.documentElement.getAttribute('data-theme') || 'dark';
  const next = current === 'dark' ? 'light' : 'dark';
  document.documentElement.setAttribute('data-theme', next);
  localStorage.setItem(THEME_KEY, next);
}

/* ===== Events ===== */
function bindEvents() {
  $('#themeToggle').addEventListener('click', toggleTheme);
  $('#newThread').addEventListener('click', () => { $('#messageInput').focus(); });
  $('#composer').addEventListener('submit', sendMessage);
  $('#messageInput').addEventListener('input', () => { autoGrow($('#messageInput')); updateSendButton(); });
  $('#messageInput').addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); $('#composer').requestSubmit(); }
  });
  $('#addTask').addEventListener('click', () => { $('#taskForm').reset(); $('#taskDialog').showModal(); $('#taskTitle').focus(); });
  $('#taskForm').addEventListener('submit', createTask);
  $('#decayButton').addEventListener('click', runDecay);
  $('#runBenchmark').addEventListener('click', runBenchmark);
  $('#settingsButton').addEventListener('click', () => $('#connectionDialog').showModal());

  // Dismiss dialogs by clicking the backdrop (native <dialog> only closes on Escape)
  $$('dialog').forEach(dlg => dlg.addEventListener('click', e => { if (e.target === dlg) dlg.close(); }));

  // Workspace tabs
  $$('.ws-tab').forEach(tab => tab.addEventListener('click', () => switchWsTab(tab.dataset.wsTab)));

  // Panel toggles
  $('#toggleWorkspace').addEventListener('click', () => $('#appShell').classList.toggle('workspace-collapsed'));
  $('#closeWorkspace').addEventListener('click', () => $('#appShell').classList.add('workspace-collapsed'));
  $('#collapseSidebar').addEventListener('click', () => $('#appShell').classList.toggle('sidebar-collapsed'));
  $('#mobileMenuBtn').addEventListener('click', () => $('#sidebar').classList.toggle('open'));
  $('#closeViewer').addEventListener('click', closeViewer);

  // Suggestions
  $$('.suggestion-card').forEach(btn => btn.addEventListener('click', () => {
    $('#messageInput').value = btn.dataset.prompt;
    autoGrow($('#messageInput'));
    updateSendButton();
    $('#messageInput').focus();
  }));

  // Keyboard shortcut
  document.addEventListener('keydown', e => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); $('#messageInput').focus(); }
  });
}

function updateSendButton() {
  const hasText = $('#messageInput').value.trim().length > 0;
  $('#sendButton').disabled = !hasText || busy;
}

/* ===== API ===== */
async function api(path, options = {}) {
  const res = await fetch(API_BASE + path, { ...options, headers: { 'Content-Type': 'application/json', ...(options.headers || {}) } });
  if (!res.ok) { const detail = await res.text(); throw new Error(detail || `Request failed (${res.status})`); }
  return res.json();
}

async function loadState() {
  try {
    state = await api('/api/state');
    setConnection(true);
    renderAll();
  } catch (error) {
    setConnection(false, error);
    renderAll();
  }
}

async function loadArtifacts() {
  try {
    const data = await api('/api/artifacts');
    artifacts = data.artifacts || [];
    renderFiles();
  } catch { artifacts = []; renderFiles(); }
}

/* ===== Connection ===== */
function setConnection(online, error) {
  $('#agentStatus').classList.toggle('online', online);
  $('#connectionLabel').textContent = online ? (state.config.demo_mode ? 'Demo mode' : 'Agent online') : 'Backend offline';
  $('#modelLabel').textContent = online ? shortModel(state.config.main_model) : 'Start server.py';
  $('#modeText').textContent = online ? (state.config.demo_mode ? 'local demo' : `${state.config.provider || 'model'} connected`) : 'backend offline';
  $('#modePill').classList.toggle('online', online);
  $('#connectionTitle').textContent = online ? 'Backend connected' : 'Backend not connected';
  $('#connectionText').textContent = online
    ? `${state.config.main_model}${state.config.demo_mode ? ` · add ${(state.config.provider || 'model').toUpperCase()}_API_KEY to .env for live inference.` : ' · visual memory recall enabled.'}`
    : `Run the API from the project directory. ${error ? error.message : ''}`;
}
function shortModel(name = '') { return name.split('/').pop()?.replace(/-/g, ' ').slice(0, 27) || 'Local agent'; }

/* ===== Render ===== */
function renderAll() {
  renderMessages();
  renderTasks();
  renderMemories();
  renderBenchmark();
  renderBadges();
  $('#retrievalCount').textContent = `${state.memories.length} ${state.memories.length === 1 ? 'memory' : 'memories'}`;
  $('#retrievalStrip').hidden = state.memories.length === 0;
  $('#threadMeta').textContent = state.messages.length ? `${state.messages.length} messages` : 'No messages yet';
  $('#decayHours').textContent = state.config.decay_hours || 24;
}

function renderBadges() {
  const openCount = state.tasks.filter(t => t.status === 'open').length;
  const memCount = state.memories.length;
  const fileCount = artifacts.length;
  setBadge($('#tasksBadge'), openCount);
  setBadge($('#memoryBadge'), memCount);
  setBadge($('#filesBadge'), fileCount);
  setBadge($('#benchmarkBadge'), state.latest_benchmark?.run?.status === 'complete' ? 1 : 0);
  $('#filesCount').textContent = `${fileCount} ${fileCount === 1 ? 'file' : 'files'}`;
}
function setBadge(el, count) {
  if (count > 0) { el.textContent = count; el.hidden = false; } else { el.hidden = true; }
}

/* ===== Workspace tabs ===== */
function switchWsTab(tab) {
  activeWsTab = tab;
  $$('.ws-tab').forEach(t => t.classList.toggle('active', t.dataset.wsTab === tab));
  $$('.ws-panel').forEach(p => p.classList.toggle('active', p.id === `ws${tab.charAt(0).toUpperCase() + tab.slice(1)}`));
  if (tab === 'memory') requestAnimationFrame(renderGraph);
}

/* ===== Messages ===== */
function renderMessages() {
  const list = $('#messageList');
  $('#welcome').hidden = state.messages.length > 0;
  list.innerHTML = state.messages.map(msg => {
    const meta = msg.meta || {};
    const chips = [
      ...(meta.retrieved?.length ? [`${meta.retrieved.length} memories recalled`] : []),
      ...(meta.actions?.length ? [`${meta.actions.length} actions executed`] : [])
    ];
    const steps = meta.actions?.length ? renderAgentSteps(meta.actions) : '';
    return `<div class="message ${msg.role}">
      <div class="message-avatar">${msg.role === 'assistant' ? 'P' : 'Y'}</div>
      <div class="message-body">
        <div class="message-head"><b>${msg.role === 'assistant' ? 'Piper' : 'You'}</b><time>${formatTime(msg.created_at)}</time></div>
        <div class="message-content">${escapeHtml(msg.content)}</div>
        ${steps}
        ${chips.length ? `<div class="message-meta">${chips.map(c => `<span>${escapeHtml(c)}</span>`).join('')}</div>` : ''}
      </div>
    </div>`;
  }).join('');
  if (busy) list.insertAdjacentHTML('beforeend',
    `<div class="message assistant" id="typing">
      <div class="message-avatar">P</div>
      <div class="message-body">
        <div class="message-head"><b>Piper</b><time>working</time></div>
        <div class="typing"><i></i><i></i><i></i></div>
      </div>
    </div>`);
  if (state.messages.length || busy) requestAnimationFrame(() => { $('#chatScroll').scrollTop = $('#chatScroll').scrollHeight; });
}

function renderAgentSteps(actions) {
  const icons = {
    create_task: '<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M9 11l3 3L22 4"/></svg>',
    complete_task: '<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M9 11l3 3L22 4"/></svg>',
    save_note: '<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2L2 7l10 5 10-5z"/></svg>',
    create_artifact: '<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/></svg>',
  };
  const steps = actions.map(action => {
    const text = action.replace(/^✓\s*/, '');
    const icon = icons[Object.keys(icons).find(k => text.toLowerCase().includes(k.replace(/_/g, ' ')))] || '<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/></svg>';
    return `<div class="agent-step"><div class="agent-step-icon">${icon}</div><b>${escapeHtml(text)}</b></div>`;
  }).join('');
  return `<div class="agent-steps">${steps}</div>`;
}

async function sendMessage(e) {
  e.preventDefault();
  const input = $('#messageInput');
  const message = input.value.trim();
  if (!message || busy) return;
  input.value = ''; autoGrow(input); updateSendButton();
  state.messages.push({ id: 'local-' + Date.now(), role: 'user', content: message, created_at: new Date().toISOString(), meta: {} });
  busy = true; updateSendButton(); renderMessages();
  try {
    const data = await api('/api/chat', { method: 'POST', body: JSON.stringify({ message }) });
    state = data.state;
    if (data.memory) toast(`Memory formed: ${data.memory.label}`);
    await loadArtifacts();
  } catch (error) {
    state.messages.push({ id: 'error-' + Date.now(), role: 'assistant', content: `I couldn't reach the agent backend. ${error.message}`, created_at: new Date().toISOString(), meta: {} });
    toast('Agent request failed');
  } finally {
    busy = false; updateSendButton(); renderAll();
  }
}

/* ===== Tasks ===== */
function renderTasks() {
  const open = state.tasks.filter(t => t.status === 'open');
  const done = state.tasks.filter(t => t.status === 'done');
  $('#openStat').textContent = open.length;
  $('#doneStat').textContent = done.length;
  $('#formedStat').textContent = state.memories.length;
  $('#taskList').innerHTML = state.tasks.length ? state.tasks.map(task => `<div class="task-card ${task.status}">
    <button class="task-check" data-task-id="${task.id}" data-next="${task.status === 'done' ? 'open' : 'done'}" aria-label="Toggle"></button>
    <div class="task-copy"><b>${escapeHtml(task.title)}</b><small>${escapeHtml(task.details || 'No details')}</small></div>
    <time>${relativeTime(task.updated_at)}</time>
  </div>`).join('') : '<div class="empty-list">No tasks yet. Ask Piper or add one manually.</div>';
  $$('.task-check').forEach(btn => btn.addEventListener('click', () => updateTask(btn.dataset.taskId, btn.dataset.next)));
}

async function createTask(e) {
  e.preventDefault();
  const title = $('#taskTitle').value.trim();
  if (!title) return;
  try {
    const data = await api('/api/tasks', { method: 'POST', body: JSON.stringify({ title, details: $('#taskDetails').value.trim() }) });
    state = data.state; e.target.reset(); $('#taskDialog').close(); renderAll(); toast('Task created and remembered');
  } catch (error) { toast(error.message); }
}

async function updateTask(id, status) {
  try {
    const data = await api(`/api/tasks/${id}`, { method: 'PATCH', body: JSON.stringify({ status }) });
    state = data.state; renderAll(); toast(status === 'done' ? 'Task completed · memory formed' : 'Task reopened');
  } catch (error) { toast(error.message); }
}

/* ===== Files / Artifacts ===== */
function renderFiles() {
  const list = $('#fileList');
  const viewer = $('#fileViewer');
  if (!artifacts.length) {
    list.innerHTML = `<div class="ws-empty">
      <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/></svg>
      <b>No artifacts yet</b><small>Ask Piper to write a document or brief</small>
    </div>`;
    viewer.hidden = true;
    renderBadges();
    return;
  }
  list.innerHTML = artifacts.map(f => `<div class="file-item" data-url="${f.url}" data-name="${escapeHtml(f.name)}">
    <div class="file-icon"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/></svg></div>
    <div class="file-info"><b>${escapeHtml(f.title)}</b><small>${formatSize(f.size)} · ${relativeTime(f.modified)}</small></div>
  </div>`).join('');
  $$('.file-item').forEach(item => item.addEventListener('click', () => openFile(item.dataset.url, item.dataset.name)));
  viewer.hidden = true;
  renderBadges();
}

async function openFile(url, name) {
  $$('.file-item').forEach(item => item.classList.toggle('active', item.dataset.url === url));
  const content = $('#viewerContent');
  $('#fileViewer').hidden = false;
  $('#viewerTitle').textContent = name;
  content.className = 'file-viewer-content';
  content.textContent = 'Loading…';
  try {
    const res = await fetch(assetUrl(url));
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    const text = await res.text();
    if (/\.(md|markdown)$/i.test(name)) {
      content.classList.add('markdown-body');
      content.innerHTML = renderMarkdown(text);
    } else {
      content.textContent = text;
    }
  } catch (error) {
    content.classList.add('viewer-error');
    content.textContent = `Couldn't load this file — ${error.message}`;
  }
}

/* Minimal, dependency-free Markdown → HTML. Escapes first, then applies inline/block rules. */
function renderMarkdown(src) {
  const esc = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  const inline = (s) => esc(s)
    .replace(/`([^`]+)`/g, (_, c) => `<code>${c}</code>`)
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|[^*])\*([^*]+)\*/g, '$1<em>$2</em>')
    .replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  const lines = src.replace(/\r\n/g, '\n').split('\n');
  const out = [];
  let inCode = false, codeBuf = [], listType = null, listBuf = [], para = [];
  const flushPara = () => { if (para.length) { out.push(`<p>${inline(para.join(' '))}</p>`); para = []; } };
  const flushList = () => { if (listBuf.length) { out.push(`<${listType}>${listBuf.map(i => `<li>${inline(i)}</li>`).join('')}</${listType}>`); listBuf = []; listType = null; } };
  for (const line of lines) {
    const fence = line.match(/^```/);
    if (fence) {
      if (inCode) { out.push(`<pre><code>${esc(codeBuf.join('\n'))}</code></pre>`); codeBuf = []; inCode = false; }
      else { flushPara(); flushList(); inCode = true; }
      continue;
    }
    if (inCode) { codeBuf.push(line); continue; }
    const h = line.match(/^(#{1,6})\s+(.*)$/);
    if (h) { flushPara(); flushList(); out.push(`<h${h[1].length}>${inline(h[2])}</h${h[1].length}>`); continue; }
    if (/^\s*([-*+])\s+/.test(line)) { flushPara(); if (listType && listType !== 'ul') flushList(); listType = 'ul'; listBuf.push(line.replace(/^\s*[-*+]\s+/, '')); continue; }
    if (/^\s*\d+\.\s+/.test(line)) { flushPara(); if (listType && listType !== 'ol') flushList(); listType = 'ol'; listBuf.push(line.replace(/^\s*\d+\.\s+/, '')); continue; }
    if (/^\s*>\s?/.test(line)) { flushPara(); flushList(); out.push(`<blockquote>${inline(line.replace(/^\s*>\s?/, ''))}</blockquote>`); continue; }
    if (/^\s*([-*_])\1{2,}\s*$/.test(line)) { flushPara(); flushList(); out.push('<hr>'); continue; }
    if (line.trim() === '') { flushPara(); flushList(); continue; }
    para.push(line.trim());
  }
  if (inCode) out.push(`<pre><code>${esc(codeBuf.join('\n'))}</code></pre>`);
  flushPara(); flushList();
  return out.join('\n');
}

function closeViewer() {
  $('#fileViewer').hidden = true;
  $$('.file-item').forEach(item => item.classList.remove('active'));
}

/* ===== Memory ===== */
function renderMemories() {
  $('#graphMeta').textContent = state.edges.length;
  $('#emptyGraph').hidden = state.memories.length > 0;
  $('#memoryGraph').hidden = state.memories.length === 0;
  $('#cortexCount').textContent = state.memories.length;
  const health = state.memories.length ? Math.round(state.memories.reduce((s, m) => s + (1 - m.decay_stage / 4), 0) / state.memories.length * 100) : 0;
  $('#graphHealth').textContent = state.memories.length ? `${health}%` : '—';
  $('#memoryGrid').innerHTML = state.memories.map(m => `<div class="memory-card" data-memory-id="${m.id}">
    <div class="memory-image-wrap"><img src="${assetUrl(m.image_url)}?v=${m.decay_stage}-${m.access_count}" alt="${escapeHtml(m.label)}" loading="lazy" style="opacity:${1 - m.decay_stage * .1}" onerror="this.classList.add('img-failed');this.removeAttribute('src')"></div>
    <div class="memory-card-copy"><b>${escapeHtml(m.label)}</b><div class="memory-card-meta"><span>${m.access_count} recalls</span><span>stage ${m.decay_stage}/4</span></div></div>
  </div>`).join('');
  $$('.memory-card').forEach(card => card.addEventListener('click', () => accessMemory(card.dataset.memoryId)));
  if (activeWsTab === 'memory') renderGraph();
}

function renderGraph() {
  const svg = $('#memoryGraph');
  if (!state.memories.length || svg.hidden) { svg.innerHTML = ''; return; }
  const w = svg.clientWidth || 360, h = svg.clientHeight || 200;
  svg.setAttribute('viewBox', `0 0 ${w} ${h}`);
  const count = state.memories.length;
  const cx = w / 2, cy = h / 2;
  const rx = Math.min(w * .38, 140), ry = Math.min(h * .35, 75);
  const positions = new Map(state.memories.map((m, i) => {
    if (count === 1) return [m.id, { x: cx, y: cy }];
    const angle = (Math.PI * 2 * i / count) - Math.PI / 2;
    const ring = 1 - (i % 3) * .08;
    return [m.id, { x: cx + Math.cos(angle) * rx * ring, y: cy + Math.sin(angle) * ry * ring }];
  }));
  const edges = state.edges.map(e => {
    const a = positions.get(e.source_id), b = positions.get(e.target_id);
    return a && b ? `<line class="graph-edge" x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}" style="opacity:${.2 + e.weight}"/>` : '';
  }).join('');
  const nodes = state.memories.map(m => {
    const p = positions.get(m.id), faded = m.decay_stage / 4;
    return `<g class="graph-node" data-memory-id="${m.id}" transform="translate(${p.x} ${p.y})" style="opacity:${1 - faded * .4}">
      <circle r="${14 - m.decay_stage * 2}"/><circle class="node-core" r="4"/><text text-anchor="middle" y="24">${escapeHtml(m.label.slice(0, 16))}</text></g>`;
  }).join('');
  svg.innerHTML = edges + nodes;
  svg.querySelectorAll('.graph-node').forEach(n => n.addEventListener('click', () => accessMemory(n.dataset.memoryId)));
}

async function accessMemory(id) {
  try {
    const data = await api(`/api/memories/${id}/access`, { method: 'POST' });
    state = data.state; renderAll(); toast('Memory recalled and restored');
  } catch (error) { toast(error.message); }
}

async function runDecay() {
  $('#decayButton').disabled = true;
  try {
    const data = await api('/api/decay', { method: 'POST' });
    state = data.state; renderAll(); toast(data.changed ? `${data.changed} memories decayed` : 'No memories to decay');
  } catch (error) { toast(error.message); }
  finally { $('#decayButton').disabled = false; }
}

/* ===== Dual-stream benchmark ===== */
function renderBenchmark() {
  $('#benchmarkModel').textContent = state.config.benchmark_model || state.config.main_model || 'openrouter/free';
  const result = state.latest_benchmark;
  const ready = result?.run?.status === 'complete' && result?.summary?.arms;
  $('#benchmarkEmpty').hidden = Boolean(ready);
  $('#benchmarkResults').hidden = !ready;
  if (!ready) return;

  const visual = result.summary.arms.visual;
  const text = result.summary.arms.text;
  $('#benchmarkKpis').innerHTML = [
    ['VISUAL ACCURACY', `${visual.accuracy}%`, `${visual.failures} request failures`],
    ['TEXT ACCURACY', `${text.accuracy}%`, `${text.failures} request failures`],
    ['ACCURACY DELTA', `${signed(result.summary.accuracy_delta)} pts`, 'visual minus text'],
    ['TOKEN DELTA', `${signed(result.summary.visual_token_delta)}%`, 'visual versus text']
  ].map(([label, value, note]) => `<div class="benchmark-kpi"><span>${label}</span><strong>${value}</strong><small>${note}</small></div>`).join('');

  renderBars('#accuracyChart', visual.accuracy, text.accuracy, value => `${value.toFixed(1)}%`);
  renderBars('#latencyChart', visual.avg_latency_ms, text.avg_latency_ms, value => `${formatCompact(value)}ms`);
  renderBars('#tokenChart', visual.total_input_tokens, text.total_input_tokens, formatCompact);
  renderBars('#storageChart', visual.avg_memory_bytes, text.avg_memory_bytes, formatSize);
  renderStepChart(result.steps || []);

  const models = new Set([...(visual.models || []), ...(text.models || [])]);
  $('#benchmarkRouteNote').textContent = result.summary.mixed_models
    ? `${models.size} underlying models were selected; modality and model effects are partially confounded.`
    : 'Both streams used the same routed model in this run.';
  $('#benchmarkRunMeta').textContent = `${result.run.scenarios} scenarios × ${result.run.depth} steps`;
  $('#benchmarkTable').innerHTML = (result.steps || []).map(step => `<div class="benchmark-step-row" title="${escapeHtml(step.error || step.answer)}">
    <span class="stream-chip ${step.arm}">${step.arm === 'visual' ? 'image' : 'text'}</span>
    <span>S${step.scenario + 1} · ${step.step + 1}</span>
    <span class="answer">${escapeHtml(step.error || `${step.expected} → ${step.answer || 'no answer'}`)}</span>
    <b class="${step.correct ? 'pass' : 'fail'}">${step.correct ? '✓' : '×'}</b>
  </div>`).join('');
}

function renderBars(selector, visual, text, formatter) {
  const max = Math.max(1, visual, text);
  $(selector).innerHTML = [
    ['visual', visual, 'image'],
    ['text', text, 'text']
  ].map(([type, value, label]) => `<div class="bar-group"><div class="bar ${type}" style="height:${Math.max(3, value / max * 68)}px"><em>${formatter(value)}</em></div><label>${label}</label></div>`).join('');
}

function renderStepChart(steps) {
  const svg = $('#stepChart');
  const maxStep = Math.max(1, ...steps.map(step => step.step + 1));
  const width = 330, height = 135, left = 24, right = 9, top = 10, bottom = 20;
  const chartW = width - left - right, chartH = height - top - bottom;
  svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
  const grid = [0, 50, 100].map(value => {
    const y = top + chartH * (1 - value / 100);
    return `<line class="bench-gridline" x1="${left}" y1="${y}" x2="${width-right}" y2="${y}"/><text class="bench-label" x="2" y="${y+3}">${value}</text>`;
  }).join('');
  const series = arm => Array.from({ length: maxStep }, (_, index) => {
    const matching = steps.filter(step => step.arm === arm && step.step === index);
    const accuracy = matching.length ? matching.filter(step => step.correct).length / matching.length * 100 : 0;
    const x = left + (maxStep === 1 ? chartW / 2 : index / (maxStep - 1) * chartW);
    const y = top + chartH * (1 - accuracy / 100);
    return { x, y, accuracy };
  });
  const visual = series('visual'), text = series('text');
  const line = (points, cls) => `<polyline class="${cls}" points="${points.map(point => `${point.x},${point.y}`).join(' ')}"/>`;
  const dots = (points, cls) => points.map((point, index) => `<circle class="${cls}" cx="${point.x}" cy="${point.y}" r="3"><title>Step ${index+1}: ${point.accuracy.toFixed(0)}%</title></circle>`).join('');
  const labels = visual.map((point, index) => `<text class="bench-label" text-anchor="middle" x="${point.x}" y="${height-4}">${index+1}</text>`).join('');
  svg.innerHTML = grid + line(visual, 'bench-visual-line') + line(text, 'bench-text-line') + dots(visual, 'bench-visual-dot') + dots(text, 'bench-text-dot') + labels;
}

async function runBenchmark() {
  if (busy) return;
  const panel = $('#wsBenchmark');
  const button = $('#runBenchmark');
  panel.classList.add('benchmark-running');
  button.disabled = true;
  try {
    const result = await api('/api/benchmarks', {
      method: 'POST',
      body: JSON.stringify({ scenarios: Number($('#benchmarkScenarios').value), depth: Number($('#benchmarkDepth').value) })
    });
    state.latest_benchmark = result;
    renderBenchmark();
    renderBadges();
    toast('Dual-stream benchmark complete');
  } catch (error) {
    toast(`Benchmark failed: ${friendlyError(error)}`);
  } finally {
    panel.classList.remove('benchmark-running');
    button.disabled = false;
  }
}

/* ===== Utils ===== */
function assetUrl(path) { return path?.startsWith('http') ? path : API_BASE + path; }
function autoGrow(el) { el.style.height = 'auto'; el.style.height = Math.min(el.scrollHeight, 160) + 'px'; }
function escapeHtml(v = '') { const d = document.createElement('div'); d.textContent = v; return d.innerHTML; }
function formatTime(v) { return new Intl.DateTimeFormat([], { hour: 'numeric', minute: '2-digit' }).format(new Date(v)); }
function relativeTime(v) { const s = Math.max(0, (Date.now() - new Date(v).getTime()) / 1000); if (s < 60) return 'just now'; if (s < 3600) return `${Math.floor(s / 60)}m ago`; if (s < 86400) return `${Math.floor(s / 3600)}h ago`; return `${Math.floor(s / 86400)}d ago`; }
function formatSize(bytes) { if (bytes < 1024) return `${bytes}B`; if (bytes < 1048576) return `${(bytes / 1024).toFixed(1)}KB`; return `${(bytes / 1048576).toFixed(1)}MB`; }
function formatCompact(value) { return new Intl.NumberFormat([], { notation: 'compact', maximumFractionDigits: 1 }).format(value); }
function signed(value) { return `${value > 0 ? '+' : ''}${value}`; }
function friendlyError(error) { try { const parsed = JSON.parse(error.message); return parsed.detail || error.message; } catch { return error.message; } }
function toast(msg) { const el = $('#toast'); el.textContent = msg; el.classList.add('show'); clearTimeout(toast.timer); toast.timer = setTimeout(() => el.classList.remove('show'), 2200); }
