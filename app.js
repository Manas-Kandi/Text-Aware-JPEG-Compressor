const API_BASE = location.protocol === 'file:' ? 'http://127.0.0.1:8000' : location.origin;
const $ = selector => document.querySelector(selector);
const $$ = selector => [...document.querySelectorAll(selector)];
const THEME_KEY = 'piper-theme';

let state = { messages: [], tasks: [], notes: [], memories: [], edges: [], latest_benchmark: null, config: {} };
let artifacts = [];
let busy = false;
let currentDocument = 'overview.md';

document.addEventListener('DOMContentLoaded', init);

async function init() {
  bindEvents();
  setTheme(localStorage.getItem(THEME_KEY) || 'dark');
  openVirtualDocument('overview');
  await Promise.all([loadState(), loadArtifacts()]);
}

function bindEvents() {
  $$('.activity').forEach(button => button.addEventListener('click', () => switchPanel(button.dataset.panel)));
  $('#themeToggle').addEventListener('click', () => setTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark'));
  $('#settingsButton').addEventListener('click', () => $('#connectionDialog').showModal());
  $('#focusChat').addEventListener('click', () => $('#messageInput').focus());
  $('#newThread').addEventListener('click', () => { $('#messageInput').value = ''; $('#messageInput').focus(); });
  $('#refreshFiles').addEventListener('click', loadArtifacts);
  $('#addTask').addEventListener('click', openTaskDialog);
  $('#taskForm').addEventListener('submit', createTask);
  $('#decayButton').addEventListener('click', runDecay);
  $('#runBenchmark').addEventListener('click', runBenchmark);
  $('#clearOutput').addEventListener('click', () => { $('#output').innerHTML = ''; $('#problemCount').textContent = '0'; });
  $('#composer').addEventListener('submit', sendMessage);
  $('#messageInput').addEventListener('input', updateSendButton);
  $('#messageInput').addEventListener('keydown', event => {
    if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); $('#composer').requestSubmit(); }
  });
  $$('.chat-welcome button').forEach(button => button.addEventListener('click', () => {
    $('#messageInput').value = button.dataset.prompt;
    updateSendButton();
    $('#messageInput').focus();
  }));
  $$('dialog').forEach(dialog => dialog.addEventListener('click', event => { if (event.target === dialog) dialog.close(); }));
  document.addEventListener('keydown', event => {
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') { event.preventDefault(); $('#messageInput').focus(); }
  });
}

async function api(path, options = {}) {
  const response = await fetch(API_BASE + path, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) }
  });
  if (!response.ok) {
    const raw = await response.text();
    let detail = raw;
    try { detail = JSON.parse(raw).detail || raw; } catch {}
    throw new Error(detail || `Request failed (${response.status})`);
  }
  return response.json();
}

async function loadState() {
  try {
    state = await api('/api/state');
    setConnection(true);
    renderAll();
    logOutput('Workspace state synchronized.', 'success');
  } catch (error) {
    setConnection(false, error);
    renderAll();
    logOutput(`Backend unavailable: ${error.message}`, 'error');
  }
}

async function loadArtifacts() {
  try {
    artifacts = (await api('/api/artifacts')).artifacts || [];
    renderFileTree();
    renderStatus();
  } catch (error) {
    artifacts = [];
    renderFileTree();
    logOutput(`Artifact index unavailable: ${error.message}`, 'error');
  }
}

function setConnection(online, error) {
  $('#modePill').classList.toggle('online', online);
  $('#modeText').textContent = online ? (state.config.demo_mode ? 'demo mode' : `${state.config.provider || 'model'} connected`) : 'backend offline';
  $('#connectionTitle').textContent = online ? 'Backend connected' : 'Backend not connected';
  $('#connectionText').textContent = online
    ? `${state.config.main_model || 'Local agent'} · visual memory graph enabled.`
    : `Start the API with .venv/bin/python server.py. ${error?.message || ''}`;
}

function renderAll() {
  renderFileTree();
  renderTasks();
  renderMemories();
  renderMessages();
  renderBenchmark();
  renderStatus();
  if (currentDocument === 'agent.config.json') openVirtualDocument('config');
  if (currentDocument === 'memory.graph.json') openVirtualDocument('graph');
}

function renderStatus() {
  const open = state.tasks.filter(task => task.status === 'open').length;
  $('#statusTasks').textContent = `${open} open task${open === 1 ? '' : 's'}`;
  $('#statusMemories').textContent = `${state.memories.length} memor${state.memories.length === 1 ? 'y' : 'ies'}`;
  $('#retrievalCount').textContent = `${state.memories.length} memor${state.memories.length === 1 ? 'y' : 'ies'}`;
  $('#retrievalStrip').hidden = state.memories.length === 0;
  setBadge('#tasksBadge', open);
  setBadge('#memoryBadge', state.memories.length);
  setBadge('#benchmarkBadge', state.latest_benchmark?.run?.status === 'complete' ? 1 : 0);
}

function setBadge(selector, count) {
  const badge = $(selector);
  badge.hidden = !count;
  badge.textContent = count;
}

function switchPanel(name) {
  $$('.activity').forEach(button => button.classList.toggle('active', button.dataset.panel === name));
  $$('.side-panel').forEach(panel => panel.classList.remove('active'));
  $(`#panel${name[0].toUpperCase()}${name.slice(1)}`).classList.add('active');
  if (name === 'memory') requestAnimationFrame(renderGraph);
  if (name === 'benchmark' && state.latest_benchmark?.run?.status === 'complete') openBenchmark(state.latest_benchmark);
}

function renderFileTree() {
  const generated = artifacts.map(file => `
    <div class="tree-row" data-artifact="${escapeHtml(file.url)}" data-name="${escapeHtml(file.name)}"><i>◇</i><span>${escapeHtml(file.name)}</span><small>${formatSize(file.size)}</small></div>`).join('');
  $('#fileTree').innerHTML = `
    <div class="tree-row tree-folder"><i>⌄</i><span>src</span></div>
    <div class="tree-row" data-virtual="overview"><i>◇</i><span>overview.md</span></div>
    <div class="tree-row" data-virtual="config"><i>{}</i><span>agent.config.json</span></div>
    <div class="tree-row" data-virtual="graph"><i>◎</i><span>memory.graph.json</span></div>
    <div class="tree-row tree-folder"><i>⌄</i><span>generated</span><small>${artifacts.length}</small></div>
    ${generated || '<div class="empty-list">No generated artifacts</div>'}`;
  $$('[data-virtual]').forEach(row => row.addEventListener('click', () => openVirtualDocument(row.dataset.virtual)));
  $$('[data-artifact]').forEach(row => row.addEventListener('click', () => openArtifact(row.dataset.artifact, row.dataset.name)));
  markActiveTreeRow();
}

function openVirtualDocument(kind) {
  const documents = {
    overview: ['overview.md', `# Piper Workbench\n\nAgentic execution with a durable visual-memory graph.\n\n## Workflow\n\n1. Delegate work from the Piper panel.\n2. Track explicit work in the Tasks view.\n3. Inspect generated artifacts in the Explorer.\n4. Recall or decay image-backed memories in Memory Graph.\n5. Compare visual and text memory with Dual Stream.\n\n## Memory policy\n\nNew task outcomes become compact, tagged image nodes. Frequently recalled nodes stay legible. Unused nodes progressively lose JPEG quality and retrieval weight, while their structured tags remain searchable.`],
    config: ['agent.config.json', JSON.stringify(state.config || {}, null, 2)],
    graph: ['memory.graph.json', JSON.stringify({ nodes: state.memories, edges: state.edges }, null, 2)]
  };
  const [name, text] = documents[kind];
  showTextDocument(name, text, kind === 'overview' ? 'Workspace architecture and operating model.' : 'Generated from current backend state.');
}

async function openArtifact(url, name) {
  try {
    const response = await fetch(assetUrl(url));
    if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
    showTextDocument(name, await response.text(), 'Generated artifact');
    logOutput(`Opened generated/${name}.`, 'success');
  } catch (error) {
    toast(`Could not open ${name}`);
    logOutput(`Failed to open ${name}: ${error.message}`, 'error');
  }
}

function showTextDocument(name, text, outline) {
  currentDocument = name;
  $('#editorText').hidden = false;
  $('#memoryPreview').hidden = true;
  $('#benchmarkDashboard').hidden = true;
  $('#tabName').textContent = name;
  $('#tabIcon').textContent = name.endsWith('.json') ? '{}' : '◇';
  $('#breadcrumbs').innerHTML = `<span>piedpiper</span><i>›</i><b>${escapeHtml(name)}</b>`;
  const lines = String(text).replace(/\r\n/g, '\n').split('\n');
  $('#lineGutter').innerHTML = lines.map((_, index) => `<span>${index + 1}</span>`).join('');
  $('#codeContent').textContent = text;
  $('#outlineText').textContent = outline;
  markActiveTreeRow();
}

function markActiveTreeRow() {
  $$('.tree-row').forEach(row => row.classList.toggle('active', row.dataset.name === currentDocument ||
    (row.dataset.virtual === 'overview' && currentDocument === 'overview.md') ||
    (row.dataset.virtual === 'config' && currentDocument === 'agent.config.json') ||
    (row.dataset.virtual === 'graph' && currentDocument === 'memory.graph.json')));
}

function renderTasks() {
  const open = state.tasks.filter(task => task.status === 'open').length;
  $('#openStat').textContent = open;
  $('#doneStat').textContent = state.tasks.length - open;
  $('#taskList').innerHTML = state.tasks.length ? state.tasks.map(task => `
    <div class="task-card ${task.status}">
      <button class="task-check" data-task-id="${task.id}" data-next="${task.status === 'done' ? 'open' : 'done'}" aria-label="${task.status === 'done' ? 'Reopen' : 'Complete'} task"></button>
      <div class="task-copy"><b>${escapeHtml(task.title)}</b><small>${escapeHtml(task.details || 'No details')}</small></div>
    </div>`).join('') : '<div class="empty-list">No tasks. Create one with + or delegate from Piper.</div>';
  $$('.task-check').forEach(button => button.addEventListener('click', () => updateTask(button.dataset.taskId, button.dataset.next)));
}

function openTaskDialog() {
  $('#taskForm').reset();
  $('#taskDialog').showModal();
  $('#taskTitle').focus();
}

async function createTask(event) {
  event.preventDefault();
  const title = $('#taskTitle').value.trim();
  if (!title) return;
  const button = $('#saveTask');
  button.disabled = true;
  try {
    const data = await api('/api/tasks', { method: 'POST', body: JSON.stringify({ title, details: $('#taskDetails').value.trim() }) });
    state = data.state;
    $('#taskDialog').close();
    renderAll();
    reportWarning(data.warning, 'Task created; memory indexing was skipped.');
    if (!data.warning) toast('Task created and indexed');
    logOutput(`Task created: ${title}`, 'success');
  } catch (error) {
    toast(`Task creation failed: ${error.message}`);
    logOutput(`Task creation failed: ${error.message}`, 'error');
  } finally { button.disabled = false; }
}

async function updateTask(id, status) {
  try {
    const data = await api(`/api/tasks/${id}`, { method: 'PATCH', body: JSON.stringify({ status }) });
    state = data.state;
    renderAll();
    reportWarning(data.warning, `Task ${status}; memory indexing was skipped.`);
    if (!data.warning) toast(status === 'done' ? 'Task completed' : 'Task reopened');
    logOutput(`Task marked ${status}.`, 'success');
  } catch (error) { toast(error.message); logOutput(error.message, 'error'); }
}

function renderMemories() {
  const count = state.memories.length;
  $('#cortexCount').textContent = count;
  $('#graphMeta').textContent = state.edges.length;
  const health = count ? Math.round(state.memories.reduce((sum, memory) => sum + (1 - memory.decay_stage / 4), 0) / count * 100) : 0;
  $('#graphHealth').textContent = count ? `${health}%` : '—';
  $('#emptyGraph').hidden = count > 0;
  $('#memoryGraph').hidden = count === 0;
  $('#memoryGrid').innerHTML = state.memories.map(memory => `
    <div class="memory-item" data-memory-id="${memory.id}">
      <img src="${assetUrl(memory.image_url)}?v=${memory.decay_stage}-${memory.access_count}" alt="" loading="lazy">
      <div><b>${escapeHtml(memory.label)}</b><small>${memory.access_count} recalls · decay ${memory.decay_stage}/4</small></div>
    </div>`).join('');
  $$('.memory-item').forEach(item => item.addEventListener('click', () => recallMemory(item.dataset.memoryId)));
  requestAnimationFrame(renderGraph);
}

function renderGraph() {
  const svg = $('#memoryGraph');
  if (!state.memories.length || svg.hidden) { svg.innerHTML = ''; return; }
  const width = svg.clientWidth || 230, height = svg.clientHeight || 140;
  svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
  const positions = new Map(state.memories.map((memory, index) => {
    const angle = Math.PI * 2 * index / Math.max(1, state.memories.length) - Math.PI / 2;
    return [memory.id, { x: width / 2 + Math.cos(angle) * width * .34, y: height / 2 + Math.sin(angle) * height * .3 }];
  }));
  const edges = state.edges.map(edge => {
    const a = positions.get(edge.source_id), b = positions.get(edge.target_id);
    return a && b ? `<line class="graph-edge" x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}"/>` : '';
  }).join('');
  const nodes = state.memories.map(memory => {
    const point = positions.get(memory.id);
    return `<g class="graph-node" data-memory-id="${memory.id}" transform="translate(${point.x} ${point.y})" opacity="${1 - memory.decay_stage * .13}"><circle r="${Math.max(6, 12 - memory.decay_stage)}"/><circle class="node-core" r="3"/><text text-anchor="middle" y="20">${escapeHtml(memory.label.slice(0, 12))}</text></g>`;
  }).join('');
  svg.innerHTML = edges + nodes;
  svg.querySelectorAll('.graph-node').forEach(node => node.addEventListener('click', () => recallMemory(node.dataset.memoryId)));
}

async function recallMemory(id) {
  const memory = state.memories.find(item => item.id === id);
  if (!memory) return;
  showMemory(memory);
  try {
    const data = await api(`/api/memories/${id}/access`, { method: 'POST' });
    state = data.state;
    renderMemories();
    renderStatus();
    logOutput(`Recalled memory: ${memory.label}`, 'success');
  } catch (error) { toast(error.message); logOutput(error.message, 'error'); }
}

function showMemory(memory) {
  currentDocument = `${memory.label}.memory.jpg`;
  $('#editorText').hidden = true;
  $('#benchmarkDashboard').hidden = true;
  $('#memoryPreview').hidden = false;
  $('#tabName').textContent = currentDocument;
  $('#tabIcon').textContent = '◎';
  $('#breadcrumbs').innerHTML = `<span>piedpiper</span><i>›</i><span>memory</span><i>›</i><b>${escapeHtml(memory.label)}</b>`;
  $('#previewStage').textContent = `decay stage ${memory.decay_stage}/4`;
  $('#previewImage').src = `${assetUrl(memory.image_url)}?v=${Date.now()}`;
  const tags = memory.retrieval_meta?.tags || memory.tags || [];
  $('#previewMeta').innerHTML = `<b>${escapeHtml(memory.label)}</b> · ${memory.access_count} recalls · ${escapeHtml(Array.isArray(tags) ? tags.join(', ') : String(tags || 'untagged'))}`;
  $('#outlineText').textContent = 'Image-backed memory node with structured retrieval metadata.';
}

async function runDecay() {
  $('#decayButton').disabled = true;
  try {
    const data = await api('/api/decay', { method: 'POST' });
    state = data.state;
    renderAll();
    toast(data.changed ? `${data.changed} memories decayed` : 'No memories eligible for decay');
    logOutput(`Decay pass complete; ${data.changed} node(s) changed.`, 'success');
  } catch (error) { toast(error.message); logOutput(error.message, 'error'); }
  finally { $('#decayButton').disabled = false; }
}

function renderMessages() {
  $('#welcome').hidden = state.messages.length > 0;
  $('#messageList').innerHTML = state.messages.map(message => {
    const retrieved = message.meta?.retrieved?.length || 0;
    const actions = message.meta?.actions?.length || 0;
    return `<article class="message ${message.role}"><div class="message-head"><b>${message.role === 'assistant' ? 'Piper' : 'You'}</b><time>${formatTime(message.created_at)}</time></div><div class="message-content">${escapeHtml(message.content)}</div>${retrieved || actions ? `<div class="message-meta"><span>${retrieved ? `${retrieved} recalled` : ''}${retrieved && actions ? ' · ' : ''}${actions ? `${actions} actions` : ''}</span></div>` : ''}</article>`;
  }).join('') + (busy ? '<article class="message assistant"><div class="message-head"><b>Piper</b><time>working</time></div><div class="typing"><i></i><i></i><i></i></div></article>' : '');
  requestAnimationFrame(() => { $('#chatScroll').scrollTop = $('#chatScroll').scrollHeight; });
}

async function sendMessage(event) {
  event.preventDefault();
  const input = $('#messageInput');
  const message = input.value.trim();
  if (!message || busy) return;
  input.value = '';
  state.messages.push({ role: 'user', content: message, created_at: new Date().toISOString(), meta: {} });
  busy = true;
  updateSendButton();
  renderMessages();
  logOutput(`Delegated to Piper: ${message.slice(0, 90)}`);
  try {
    const data = await api('/api/chat', { method: 'POST', body: JSON.stringify({ message }) });
    state = data.state;
    reportWarning(data.warning, 'Agent replied; visual memory indexing was skipped.');
    await loadArtifacts();
    logOutput('Agent run completed.', 'success');
  } catch (error) {
    state.messages.push({ role: 'assistant', content: `The agent request failed. ${error.message}`, created_at: new Date().toISOString(), meta: {} });
    logOutput(`Agent run failed: ${error.message}`, 'error');
  } finally {
    busy = false;
    updateSendButton();
    renderAll();
  }
}

function renderBenchmark() {
  const result = state.latest_benchmark;
  const ready = result?.run?.status === 'complete' && result?.summary?.arms;
  $('#benchmarkSideResult').innerHTML = ready
    ? `<b>Latest run</b><p>Image ${result.summary.arms.visual.accuracy}% · Text ${result.summary.arms.text.accuracy}%</p><p>${result.run.scenarios} scenarios × ${result.run.depth} steps</p>`
    : '<p>No completed run.</p>';
}

async function runBenchmark() {
  const button = $('#runBenchmark');
  button.disabled = true;
  button.textContent = '…';
  logOutput('Starting parallel visual/text benchmark.');
  try {
    const result = await api('/api/benchmarks', { method: 'POST', body: JSON.stringify({ scenarios: Number($('#benchmarkScenarios').value), depth: Number($('#benchmarkDepth').value) }) });
    state.latest_benchmark = result;
    renderBenchmark();
    renderStatus();
    openBenchmark(result);
    toast('Benchmark complete');
    logOutput('Dual-stream benchmark completed.', 'success');
  } catch (error) { toast(`Benchmark failed: ${error.message}`); logOutput(error.message, 'error'); }
  finally { button.disabled = false; button.textContent = '▶'; }
}

function openBenchmark(result) {
  const visual = result.summary.arms.visual, text = result.summary.arms.text;
  currentDocument = 'dual-stream.benchmark';
  $('#editorText').hidden = true;
  $('#memoryPreview').hidden = true;
  $('#benchmarkDashboard').hidden = false;
  $('#tabName').textContent = currentDocument;
  $('#tabIcon').textContent = '≋';
  $('#breadcrumbs').innerHTML = '<span>piedpiper</span><i>›</i><span>benchmarks</span><i>›</i><b>latest</b>';
  $('#benchmarkDashboard').innerHTML = `<div class="dash-head"><div><h2>Dual-stream memory benchmark</h2><p>${result.run.scenarios} scenarios × ${result.run.depth} sequential steps, executed in parallel</p></div></div><div class="dash-grid">${kpi('IMAGE ACCURACY', `${visual.accuracy}%`, `${visual.failures} failures`)}${kpi('TEXT ACCURACY', `${text.accuracy}%`, `${text.failures} failures`)}${kpi('ACCURACY DELTA', `${signed(result.summary.accuracy_delta)} pts`, 'image minus text')}${kpi('TOKEN DELTA', `${signed(result.summary.visual_token_delta)}%`, 'image versus text')}</div><div class="charts">${barChart('Retrieval accuracy', visual.accuracy, text.accuracy, '%')}${barChart('Average latency', visual.avg_latency_ms, text.avg_latency_ms, 'ms')}${barChart('Input tokens', visual.total_input_tokens, text.total_input_tokens, '')}${barChart('Memory bytes', visual.avg_memory_bytes, text.avg_memory_bytes, '')}</div>`;
  $('#outlineText').textContent = 'Parallel comparison of image-backed and text-backed sequential memory.';
}

function kpi(label, value, note) { return `<div class="dash-kpi"><span>${label}</span><b>${value}</b><small>${note}</small></div>`; }
function barChart(title, visual, text, suffix) {
  const max = Math.max(1, visual, text);
  const bar = (kind, label, value) => `<div class="bar-wrap ${kind}"><b>${formatCompact(value)}${suffix}</b><i style="height:${Math.max(3, value / max * 78)}px"></i><small>${label}</small></div>`;
  return `<div class="chart-card"><h3>${title}</h3><div class="bar-pair">${bar('visual', 'image', visual)}${bar('text', 'text', text)}</div></div>`;
}

function setTheme(theme) { document.documentElement.dataset.theme = theme; localStorage.setItem(THEME_KEY, theme); }
function updateSendButton() { $('#sendButton').disabled = busy || !$('#messageInput').value.trim(); }
function reportWarning(warning, message) { if (!warning) return; toast(message); logOutput(`${message} ${warning}`, 'error'); }
function logOutput(message, type = '') {
  const row = document.createElement('p');
  row.innerHTML = `<time>${new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</time><span class="${type}">${escapeHtml(message)}</span>`;
  $('#output').append(row);
  $('#output').scrollTop = $('#output').scrollHeight;
  if (type === 'error') $('#problemCount').textContent = String(Number($('#problemCount').textContent || 0) + 1);
}
function assetUrl(path = '') { return path.startsWith('http') ? path : API_BASE + path; }
function escapeHtml(value = '') { const div = document.createElement('div'); div.textContent = String(value); return div.innerHTML; }
function formatTime(value) { return new Intl.DateTimeFormat([], { hour: 'numeric', minute: '2-digit' }).format(new Date(value)); }
function formatSize(bytes = 0) { return bytes < 1024 ? `${bytes}B` : bytes < 1048576 ? `${(bytes / 1024).toFixed(1)}KB` : `${(bytes / 1048576).toFixed(1)}MB`; }
function formatCompact(value = 0) { return new Intl.NumberFormat([], { notation: 'compact', maximumFractionDigits: 1 }).format(value); }
function signed(value = 0) { return `${value > 0 ? '+' : ''}${Number(value).toFixed(1).replace('.0', '')}`; }
