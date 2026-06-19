const API_BASE = location.protocol === 'file:' ? 'http://127.0.0.1:8000' : location.origin;
const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];

let state = { messages: [], tasks: [], notes: [], memories: [], edges: [], config: {} };
let activeView = 'chat';
let busy = false;

document.addEventListener('DOMContentLoaded', init);

async function init() {
  bindEvents();
  autoGrow($('#messageInput'));
  await loadState();
}

function bindEvents() {
  $$('.nav-item').forEach(button => button.addEventListener('click', () => switchView(button.dataset.view)));
  $$('[data-view-jump]').forEach(button => button.addEventListener('click', () => switchView(button.dataset.viewJump)));
  $$('.suggestions button').forEach(button => button.addEventListener('click', () => {
    $('#messageInput').value = button.dataset.prompt;
    autoGrow($('#messageInput'));
    $('#messageInput').focus();
  }));
  $('#composer').addEventListener('submit', sendMessage);
  $('#messageInput').addEventListener('input', event => autoGrow(event.target));
  $('#messageInput').addEventListener('keydown', event => {
    if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); $('#composer').requestSubmit(); }
  });
  $('#addTask').addEventListener('click', () => $('#taskDialog').showModal());
  $('#taskForm').addEventListener('submit', createTask);
  $('#decayButton').addEventListener('click', runDecay);
  $('#toggleRail').addEventListener('click', () => $('#memoryRail').classList.toggle('open'));
  $('#closeRail').addEventListener('click', () => $('#memoryRail').classList.remove('open'));
  $('#settingsButton').addEventListener('click', () => $('#connectionDialog').showModal());
  $('#newThread').addEventListener('click', () => { switchView('chat'); $('#messageInput').focus(); });
  document.addEventListener('keydown', event => {
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') { event.preventDefault(); switchView('chat'); $('#messageInput').focus(); }
  });
}

async function api(path, options = {}) {
  const response = await fetch(API_BASE + path, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) }
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || `Request failed (${response.status})`);
  }
  return response.json();
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

function setConnection(online, error) {
  $('.agent-status').classList.toggle('online', online);
  $('.mode-pill').classList.toggle('online', online);
  $('#connectionLabel').textContent = online ? (state.config.demo_mode ? 'Demo mode' : 'Agent online') : 'Backend offline';
  $('#modelLabel').textContent = online ? shortModel(state.config.main_model) : 'Start server.py';
  $('#modePill').innerHTML = `<i></i> ${online ? (state.config.demo_mode ? 'local demo' : 'NVIDIA connected') : 'backend offline'}`;
  $('#connectionTitle').textContent = online ? 'Backend connected' : 'Backend not connected';
  $('#connectionText').textContent = online
    ? `${state.config.main_model}${state.config.demo_mode ? ' · add NVIDIA_API_KEY to .env for live inference.' : ' · visual memory recall enabled.'}`
    : `Run the API from the project directory. ${error ? error.message : ''}`;
}

function shortModel(name = '') { return name.split('/').pop()?.replace(/-/g, ' ').slice(0, 27) || 'Local agent'; }

function renderAll() {
  renderMessages();
  renderTasks();
  renderMemories();
  renderRail();
  const open = state.tasks.filter(task => task.status === 'open').length;
  $('#taskCount').textContent = open;
  $('#memoryCount').textContent = state.memories.length;
  $('#retrievalCount').textContent = `${state.memories.length} ${state.memories.length === 1 ? 'memory' : 'memories'}`;
  $('#retrievalStrip').hidden = state.memories.length === 0;
  $('#threadMeta').textContent = state.messages.length ? `${state.messages.length} messages` : 'No messages yet';
  $('#decayHours').textContent = state.config.decay_hours || 24;
}

function switchView(view) {
  activeView = view;
  $$('.nav-item').forEach(item => item.classList.toggle('active', item.dataset.view === view));
  $$('.view').forEach(section => section.classList.remove('active'));
  $(`#${view}View`).classList.add('active');
  const labels = { chat: ['CONVERSATION', 'Working session'], tasks: ['EXECUTION', 'Tasks'], memory: ['CONTEXT CORTEX', 'Memory graph'] };
  $('#viewEyebrow').textContent = labels[view][0];
  $('#viewTitle').textContent = labels[view][1];
  if (view === 'memory') requestAnimationFrame(renderGraph);
}

function renderMessages() {
  const list = $('#messageList');
  $('#welcome').hidden = state.messages.length > 0;
  list.innerHTML = state.messages.map(message => {
    const meta = message.meta || {};
    const chips = [
      ...(meta.retrieved?.length ? [`${meta.retrieved.length} memories recalled`] : []),
      ...(meta.actions?.length ? [`${meta.actions.length} actions executed`] : [])
    ];
    return `<article class="message ${message.role}">
      <div class="message-avatar">${message.role === 'assistant' ? 'P' : 'Y'}</div>
      <div><div class="message-head"><b>${message.role === 'assistant' ? 'Piper' : 'You'}</b><time>${formatTime(message.created_at)}</time></div>
      <div class="message-content">${escapeHtml(message.content)}</div>
      ${chips.length ? `<div class="message-meta">${chips.map(chip => `<span>${escapeHtml(chip)}</span>`).join('')}</div>` : ''}</div>
    </article>`;
  }).join('');
  if (busy) list.insertAdjacentHTML('beforeend', `<article class="message assistant" id="typing"><div class="message-avatar">P</div><div><div class="message-head"><b>Piper</b><time>working</time></div><div class="typing"><i></i><i></i><i></i></div></div></article>`);
  if (state.messages.length || busy) requestAnimationFrame(() => { $('#chatScroll').scrollTop = $('#chatScroll').scrollHeight; });
}

async function sendMessage(event) {
  event.preventDefault();
  const input = $('#messageInput');
  const message = input.value.trim();
  if (!message || busy) return;
  input.value = ''; autoGrow(input);
  state.messages.push({ id: 'local-' + Date.now(), role: 'user', content: message, created_at: new Date().toISOString(), meta: {} });
  busy = true; $('#sendButton').disabled = true; renderMessages();
  try {
    const data = await api('/api/chat', { method: 'POST', body: JSON.stringify({ message }) });
    state = data.state;
    if (data.memory) toast(`Memory formed: ${data.memory.label}`);
  } catch (error) {
    state.messages.push({ id: 'error-' + Date.now(), role: 'assistant', content: `I couldn't reach the agent backend. ${error.message}`, created_at: new Date().toISOString(), meta: {} });
    toast('Agent request failed');
  } finally {
    busy = false; $('#sendButton').disabled = false; renderAll();
  }
}

function renderTasks() {
  const open = state.tasks.filter(task => task.status === 'open');
  const done = state.tasks.filter(task => task.status === 'done');
  $('#openStat').textContent = open.length;
  $('#doneStat').textContent = done.length;
  $('#formedStat').textContent = state.memories.length;
  $('#taskList').innerHTML = state.tasks.length ? state.tasks.map(task => `<article class="task-card ${task.status}">
    <button class="task-check" data-task-id="${task.id}" data-next="${task.status === 'done' ? 'open' : 'done'}" aria-label="${task.status === 'done' ? 'Reopen' : 'Complete'} ${escapeHtml(task.title)}"></button>
    <div class="task-copy"><b>${escapeHtml(task.title)}</b><small>${escapeHtml(task.details || 'No additional details')}</small></div>
    <time>${relativeTime(task.updated_at)}</time>
  </article>`).join('') : '<div class="empty-list">No tasks yet. Ask Piper to create one, or add one manually.</div>';
  $$('.task-check').forEach(button => button.addEventListener('click', () => updateTask(button.dataset.taskId, button.dataset.next)));
}

async function createTask(event) {
  event.preventDefault();
  const title = $('#taskTitle').value.trim();
  if (!title) return;
  try {
    const data = await api('/api/tasks', { method: 'POST', body: JSON.stringify({ title, details: $('#taskDetails').value.trim() }) });
    state = data.state; $('#taskDialog').close(); event.target.reset(); renderAll(); toast('Task created and remembered');
  } catch (error) { toast(error.message); }
}

async function updateTask(id, status) {
  try {
    const data = await api(`/api/tasks/${id}`, { method: 'PATCH', body: JSON.stringify({ status }) });
    state = data.state; renderAll(); toast(status === 'done' ? 'Task completed · memory formed' : 'Task reopened');
  } catch (error) { toast(error.message); }
}

function renderMemories() {
  $('#graphMeta').textContent = `${state.memories.length} nodes · ${state.edges.length} edges`;
  $('#emptyGraph').hidden = state.memories.length > 0;
  $('#memoryGraph').hidden = state.memories.length === 0;
  $('#memoryGrid').innerHTML = state.memories.map(memory => `<article class="memory-card" data-memory-id="${memory.id}">
    <div class="memory-image-wrap"><img src="${assetUrl(memory.image_url)}?v=${memory.decay_stage}-${memory.access_count}" alt="Visual memory: ${escapeHtml(memory.label)}" style="opacity:${1 - memory.decay_stage * .1}"></div>
    <div class="memory-card-copy"><b>${escapeHtml(memory.label)}</b><div class="memory-card-meta"><span>${memory.access_count} recalls</span><span class="stage-badge">stage ${memory.decay_stage}/4</span></div></div>
  </article>`).join('');
  $$('.memory-card').forEach(card => card.addEventListener('click', () => accessMemory(card.dataset.memoryId)));
  if (activeView === 'memory') renderGraph();
}

function renderGraph() {
  const svg = $('#memoryGraph');
  if (!state.memories.length || svg.hidden) { svg.innerHTML = ''; return; }
  const width = svg.clientWidth || 800, height = svg.clientHeight || 348;
  svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
  const count = state.memories.length;
  const cx = width / 2, cy = height / 2;
  const radiusX = Math.min(width * .36, 300), radiusY = Math.min(height * .34, 115);
  const positions = new Map(state.memories.map((memory, index) => {
    if (count === 1) return [memory.id, { x: cx, y: cy }];
    const angle = (Math.PI * 2 * index / count) - Math.PI / 2;
    const ring = 1 - (index % 3) * .08;
    return [memory.id, { x: cx + Math.cos(angle) * radiusX * ring, y: cy + Math.sin(angle) * radiusY * ring }];
  }));
  const edges = state.edges.map(edge => {
    const a = positions.get(edge.source_id), b = positions.get(edge.target_id);
    return a && b ? `<line class="graph-edge" x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}" style="opacity:${.25 + edge.weight}" />` : '';
  }).join('');
  const nodes = state.memories.map(memory => {
    const p = positions.get(memory.id), faded = memory.decay_stage / 4;
    return `<g class="graph-node" data-memory-id="${memory.id}" transform="translate(${p.x} ${p.y})" style="opacity:${1 - faded * .45}">
      <circle r="${19 - memory.decay_stage}"/><circle class="node-core" r="5"/><text text-anchor="middle" y="32">${escapeHtml(memory.label.slice(0, 20))}</text></g>`;
  }).join('');
  svg.innerHTML = edges + nodes;
  svg.querySelectorAll('.graph-node').forEach(node => node.addEventListener('click', () => accessMemory(node.dataset.memoryId)));
}

function renderRail() {
  const memories = state.memories.slice(0, 4);
  $('#cortexCount').textContent = state.memories.length;
  const health = state.memories.length ? Math.round(state.memories.reduce((sum, memory) => sum + (1 - memory.decay_stage / 4), 0) / state.memories.length * 100) : 0;
  $('#graphHealth').textContent = state.memories.length ? `${health}%` : '—';
  $('#healthBar').style.width = `${health}%`;
  $('#railMemories').innerHTML = memories.length ? memories.map(memory => `<div class="rail-memory" data-memory-id="${memory.id}"><img src="${assetUrl(memory.image_url)}?v=${memory.decay_stage}-${memory.access_count}" alt=""><div><b>${escapeHtml(memory.label)}</b><small>${relativeTime(memory.last_accessed)} · stage ${memory.decay_stage}</small></div></div>`).join('') : '<p class="rail-empty">Completed work will appear here as compressed visual context.</p>';
  $$('.rail-memory').forEach(item => item.addEventListener('click', () => accessMemory(item.dataset.memoryId)));
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
    state = data.state; renderAll(); toast(data.changed ? `${data.changed} memories decayed` : 'No memories available to decay');
  } catch (error) { toast(error.message); }
  finally { $('#decayButton').disabled = false; }
}

function assetUrl(path) { return path?.startsWith('http') ? path : API_BASE + path; }
function autoGrow(element) { element.style.height = 'auto'; element.style.height = Math.min(element.scrollHeight, 160) + 'px'; }
function escapeHtml(value = '') { const div = document.createElement('div'); div.textContent = value; return div.innerHTML; }
function formatTime(value) { return new Intl.DateTimeFormat([], { hour: 'numeric', minute: '2-digit' }).format(new Date(value)); }
function relativeTime(value) { const seconds = Math.max(0, (Date.now() - new Date(value).getTime()) / 1000); if (seconds < 60) return 'just now'; if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`; if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`; return `${Math.floor(seconds / 86400)}d ago`; }
function toast(message) { const el = $('#toast'); el.textContent = message; el.classList.add('show'); clearTimeout(toast.timer); toast.timer = setTimeout(() => el.classList.remove('show'), 2200); }
