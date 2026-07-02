const API_BASE = location.protocol === 'file:' ? 'http://127.0.0.1:8000' : location.origin;
const $ = selector => document.querySelector(selector);
const $$ = selector => [...document.querySelectorAll(selector)];
const THEME_KEY = 'piper-theme';

const PRESETS = {
  quick: { lengths: [8, 16], seeds: [1103, 2207] },
  full: { lengths: [16, 32, 64, 128], seeds: [1103, 2207, 3301, 4409, 5519] },
};
const PHASE_LABELS = {
  queued: 'waiting in queue', preparing: 'building trajectories', jpeg: 'jpeg arm',
  text: 'text arm', analysis: 'scoring and charts', complete: 'done',
  failed: 'failed', cancelled: 'stopped',
};
const CHART_EXPLANATIONS = {
  'accuracy-by-length.png': 'Shows whether each arm keeps answers correct as the synthetic project log gets longer.',
  'accuracy-by-probe.png': 'Breaks accuracy down by question type, so weak spots are visible instead of averaged away.',
  'survival-by-depth.png': 'Tracks how many trajectories still have no prior mistake as checkpoints get deeper.',
  'efficiency.png': 'Compares latency, reported input tokens, payload bytes, and reported cost for JPEG versus text requests.',
  'capacity-and-cost.png': 'Combines page count, depth, cost, latency, and accuracy to show the practical tradeoff.',
};

let state = { messages: [], tasks: [], notes: [], memories: [], edges: [], latest_benchmark: null, config: {} };
let artifacts = [];
let models = [];
let busy = false;
let selectedRunId = null;
let dialogMemoryId = null;
let benchmarkPoll = null;
let toastTimer = null;

document.addEventListener('DOMContentLoaded', init);

async function init() {
  bindEvents();
  updateRunHint();
  await Promise.all([loadState(), loadArtifacts(), loadModels()]);
}

function bindEvents() {
  $$('.tab').forEach(tab => tab.addEventListener('click', () => switchView(tab.dataset.view)));
  $('#themeToggle').addEventListener('click', () => setTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark'));
  $('#runTest').addEventListener('click', runTest);
  $('#cancelRun').addEventListener('click', cancelRun);
  $('#presetGroup').addEventListener('change', updateRunHint);
  $('#closedLoop').addEventListener('change', updateRunHint);
  $('#modelSelect').addEventListener('change', () => { if ($('#modelSelect').value) $('#modelCustom').value = ''; });
  $('#modelCustom').addEventListener('input', () => { if ($('#modelCustom').value.trim()) $('#modelSelect').value = ''; });
  $('#addTask').addEventListener('click', () => { $('#taskForm').reset(); $('#taskDialog').showModal(); $('#taskTitle').focus(); });
  $('#taskForm').addEventListener('submit', createTask);
  $('#refreshFiles').addEventListener('click', loadArtifacts);
  $('#decayButton').addEventListener('click', runDecay);
  $('#recallMemory').addEventListener('click', recallDialogMemory);
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
}

function switchView(name) {
  $$('.tab').forEach(tab => tab.classList.toggle('active', tab.dataset.view === name));
  $$('.view').forEach(view => view.classList.remove('active'));
  $(`#view${name[0].toUpperCase()}${name.slice(1)}`).classList.add('active');
  if (name === 'runs') loadRuns();
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
    setConnection('online');
    renderAll();
    const run = state.latest_benchmark;
    if (run) {
      selectedRunId = selectedRunId || run.id;
      if (['queued', 'running'].includes(run.status)) pollRun(run.id);
    }
  } catch (error) {
    setConnection('offline', error);
    renderAll();
  }
}

async function loadModels() {
  try {
    const data = await api('/api/benchmark-models');
    models = data.models || [];
    renderModelSelect(data.error);
  } catch (error) {
    models = [];
    renderModelSelect(error.message);
  }
}

async function loadArtifacts() {
  try {
    artifacts = (await api('/api/artifacts')).artifacts || [];
  } catch { artifacts = []; }
  renderArtifacts();
}

function setConnection(status, error) {
  const pill = $('#statusPill');
  pill.classList.toggle('online', status === 'online');
  pill.classList.toggle('offline', status === 'offline');
  const demo = state.config.demo_mode;
  $('#statusText').textContent = status === 'online' ? (demo ? 'demo mode — no api key' : 'connected') : 'backend off';
  $('#demoBanner').hidden = !(status === 'online' && demo);
  $('#offlineBanner').hidden = status !== 'offline';
  if (status === 'offline' && error) console.warn('backend unreachable:', error.message);
}

function renderAll() {
  renderRunControls();
  renderResult();
  renderTasks();
  renderMessages();
  renderMemories();
  renderArtifacts();
}

/* ---------- Experiment ---------- */

function presetConfig() {
  const preset = PRESETS[document.querySelector('input[name=preset]:checked').value];
  return { lengths: preset.lengths, seeds: preset.seeds, closed_loop: $('#closedLoop').checked };
}

function chosenModel() {
  return $('#modelCustom').value.trim() || $('#modelSelect').value;
}

// Mirrors benchmark/scenarios.py: checkpoints are unique values of max(4, round(length * f)).
function estimateCalls(config) {
  const probes = length => new Set([.25, .5, .75, 1].map(f => Math.max(4, Math.round(length * f)))).size;
  const primary = config.lengths.reduce((sum, length) => sum + probes(length), 0) * config.seeds.length * 2;
  const closed = config.closed_loop ? Math.min(config.seeds.length, 4) * 4 * 2 : 0;
  return primary + closed;
}

function updateRunHint() {
  const config = presetConfig();
  $('#runHint').textContent = `About ${estimateCalls(config)} model calls. One pinned model runs both arms. Temperature zero.`;
}

function renderModelSelect(error) {
  const select = $('#modelSelect');
  if (!models.length) {
    select.innerHTML = `<option value="">${error ? 'could not load models — type one below' : 'no models found — type one below'}</option>`;
    return;
  }
  const free = models.filter(model => model.free);
  const paid = models.filter(model => !model.free);
  const option = model => `<option value="${escapeHtml(model.id)}">${escapeHtml(model.id)}${model.context_length ? ` · ${Math.round(model.context_length / 1000)}k ctx` : ''}</option>`;
  select.innerHTML = '<option value="">pick a model…</option>'
    + (free.length ? `<optgroup label="Free (${free.length})">${free.map(option).join('')}</optgroup>` : '')
    + (paid.length ? `<optgroup label="Paid (${paid.length})">${paid.map(option).join('')}</optgroup>` : '');
  // Same default the server picks: pinned model if set, else the stable free model with the biggest context.
  const pinned = state.config.benchmark_model;
  const stableThenBig = model => [!/preview|-exp|beta/.test(model.id), model.context_length];
  const better = (a, b) => { const [sa, ca] = stableThenBig(a), [sb, cb] = stableThenBig(b); return sb !== sa ? (sb ? b : a) : (cb > ca ? b : a); };
  if (pinned && models.some(model => model.id === pinned)) select.value = pinned;
  else if (free.length) select.value = free.reduce((best, model) => better(best, model)).id;
}

async function runTest() {
  const model = chosenModel();
  if (!model) { showToast('Pick a model first.'); return; }
  const button = $('#runTest');
  button.disabled = true;
  try {
    const run = await api('/api/benchmarks', { method: 'POST', body: JSON.stringify({ model, ...presetConfig() }) });
    state.latest_benchmark = run;
    selectedRunId = run.id;
    renderRunControls();
    renderResult();
    pollRun(run.id);
    showToast('Test started.');
  } catch (error) {
    showToast(error.message);
  } finally { button.disabled = false; }
}

async function cancelRun() {
  const run = state.latest_benchmark;
  if (!run) return;
  try {
    state.latest_benchmark = await api(`/api/benchmarks/${run.id}/cancel`, { method: 'POST' });
    renderRunControls();
    showToast('Stop requested. The current call finishes first.');
  } catch (error) { showToast(error.message); }
}

function pollRun(id) {
  clearTimeout(benchmarkPoll);
  benchmarkPoll = setTimeout(async () => {
    try {
      const run = await api(`/api/benchmarks/${id}`);
      state.latest_benchmark = run;
      renderRunControls();
      if (selectedRunId === id) renderResult();
      if (['queued', 'running'].includes(run.status)) pollRun(id);
      else showToast(run.status === 'complete' ? 'Test done. Read the result.' : `Test ${run.status}.`);
    } catch { pollRun(id); }
  }, 2000);
}

function renderRunControls() {
  const run = state.latest_benchmark;
  const active = ['queued', 'running'].includes(run?.status);
  $('#runTest').disabled = active;
  $('#runTest').textContent = active ? 'A test is running…' : 'Run test';
  $('#progressBlock').hidden = !active;
  if (active) {
    $('#progressPhase').textContent = PHASE_LABELS[run.phase] || run.phase;
    $('#progressCount').textContent = `${run.progress.completed} / ${run.progress.total || '?'}`;
    $('#progressBar').style.width = `${run.progress.percent}%`;
  }
}

async function renderResult() {
  const container = $('#resultDetail');
  let run = state.latest_benchmark;
  if (selectedRunId && run?.id !== selectedRunId) {
    try { run = await api(`/api/benchmarks/${selectedRunId}`); } catch { run = state.latest_benchmark; }
  }
  container.innerHTML = runDetailHtml(run);
  bindResumeButtons(container);
  bindCopyLogButtons(container);
  hydrateTranscripts(container);
}

async function hydrateTranscripts(root) {
  for (const mount of root.querySelectorAll('.transcript[data-run]')) {
    const runId = mount.dataset.run;
    try {
      const observations = (await api(`/api/benchmarks/${runId}/observations`)).observations || [];
      mount.innerHTML = transcriptHtml(observations) || '<p class="muted">No observations yet.</p>';
      mount.querySelectorAll('[data-obs]').forEach(button =>
        button.addEventListener('click', () => openObservation(runId, button.dataset.obs)));
    } catch (error) {
      mount.innerHTML = `<p class="error-note">Could not load the transcript: ${escapeHtml(error.message)}</p>`;
    }
  }
}

function transcriptHtml(observations) {
  if (!observations.length) return '';
  const groups = new Map();
  for (const observation of observations) {
    if (!groups.has(observation.trajectory_id)) groups.set(observation.trajectory_id, []);
    groups.get(observation.trajectory_id).push(observation);
  }
  return [...groups.entries()].map(([trajectoryId, items]) => {
    const checkpoints = new Map();
    for (const item of items) {
      if (!checkpoints.has(item.checkpoint)) checkpoints.set(item.checkpoint, {});
      checkpoints.get(item.checkpoint)[item.arm] = item;
    }
    const first = items[0];
    const score = arm => {
      const scored = items.filter(item => item.arm === arm && item.status === 'complete');
      return `${scored.filter(item => item.correct).length}/${scored.length}`;
    };
    const rowsHtml = [...checkpoints.entries()].sort((a, b) => a[0] - b[0]).map(([checkpoint, arms]) => `
      <tr>
        <td>${checkpoint}</td>
        <td>${escapeHtml((arms.jpeg || arms.text)?.prompt || '')}<br><small class="muted">${escapeHtml((arms.jpeg || arms.text)?.probe_type || '')} · expects “${escapeHtml(String((arms.jpeg || arms.text)?.expected ?? ''))}”</small></td>
        ${answerCell(arms.jpeg)}${answerCell(arms.text)}
      </tr>`).join('');
    return `<details>
      <summary><b>${escapeHtml(first.profile)}</b> · ${first.length ?? '?'} steps · seed ${first.seed ?? '?'} · jpeg ${score('jpeg')} · text ${score('text')}</summary>
      <table class="obs-table"><thead><tr><th>Step</th><th>Question</th><th>JPEG answer</th><th>Text answer</th></tr></thead><tbody>${rowsHtml}</tbody></table>
    </details>`;
  }).join('');
}

function answerCell(observation) {
  if (!observation) return '<td class="muted">—</td>';
  if (observation.status !== 'complete') return '<td class="muted">pending</td>';
  const answer = observation.error_type && !observation.answer
    ? `[${observation.error_type} error]`
    : displayAnswer(observation.answer);
  const mark = observation.correct ? '✓' : '✗';
  return `<td><button class="obs-answer ${observation.correct ? 'ok' : 'bad'}" data-obs="${escapeHtml(observation.id)}">${mark} ${escapeHtml(answer.slice(0, 48))}${answer.length > 48 ? '…' : ''}</button></td>`;
}

function displayAnswer(raw) {
  const text = String(raw || '').replace(/^```(?:json)?\s*|\s*```$/g, '').trim();
  try {
    const parsed = JSON.parse(text);
    if (parsed && typeof parsed === 'object' && 'answer' in parsed) return String(parsed.answer);
  } catch {}
  return text || '[empty]';
}

async function openObservation(runId, observationId) {
  try {
    const data = await api(`/api/benchmarks/${runId}/observations/${observationId}`);
    const observation = data.observation;
    $('#obsTitle').textContent = `Step ${observation.checkpoint} · ${observation.arm} arm · ${observation.correct ? 'correct' : 'wrong'}`;
    const attempts = (data.attempts || []).map(attempt =>
      `<li>Attempt ${attempt.attempt}: ${escapeHtml(attempt.status)} in ${attempt.latency_ms}ms${attempt.error ? ` — ${escapeHtml(attempt.error.slice(0, 160))}` : ''}</li>`).join('');
    const pages = (data.pages || []).map(url => `<a href="${assetUrl(url)}" target="_blank" rel="noopener"><img src="${assetUrl(url)}" alt="Context page" loading="lazy"></a>`).join('');
    $('#obsBody').innerHTML = `
      <div class="kv">
        <span>Question</span><div>${escapeHtml(observation.prompt)}</div>
        <span>Expected</span><div>${escapeHtml(String(observation.expected))}</div>
        <span>Model said</span><div>${escapeHtml(displayAnswer(observation.answer))}</div>
        <span>Model</span><div>${escapeHtml(observation.resolved_model || '?')} · ${observation.latency_ms}ms · ${observation.input_tokens} in / ${observation.output_tokens} out tokens</div>
        ${observation.error ? `<span>Error</span><div class="error-note">${escapeHtml(observation.error.slice(0, 300))}</div>` : ''}
      </div>
      ${attempts ? `<h3>Attempts</h3><ul class="attempt-list">${attempts}</ul>` : ''}
      <h3>Raw answer</h3><pre>${escapeHtml(observation.answer || '[empty]')}</pre>
      ${pages ? `<h3>The JPEG pages the model saw</h3><div class="obs-pages">${pages}</div>` : ''}
      <h3>The context${observation.arm === 'text' ? ' the model read as text' : ' behind those pages'}</h3>
      <p class="hint">${data.context_verified ? 'Rebuilt from the seed. The hash matches the recorded run.' : 'Rebuilt from the seed, but the hash does not match. Treat it with care.'}</p>
      <pre>${escapeHtml(data.context || '[could not rebuild]')}</pre>`;
    $('#obsDialog').showModal();
  } catch (error) { showToast(error.message); }
}

function runDetailHtml(run) {
  if (!run) return '<div class="card"><h2>Result</h2><p class="muted">No runs yet. Run one.</p></div>';
  const arms = run.summary?.profiles?.primary?.arms;
  const tradeoff = run.summary?.profiles?.primary?.tradeoff;
  const parts = [];
  if (arms?.jpeg && arms?.text) parts.push(verdictHtml(run, arms));
  else if (run.status === 'complete') parts.push('<div class="verdict"><h3>The run finished, but there is no paired summary.</h3><p>Check the artifacts below.</p></div>');
  else if (run.status === 'failed') parts.push(`<div class="verdict"><h3>The run failed.</h3><p class="error-note">${escapeHtml(run.error || 'No error text.')}</p><p>Fix the cause, then resume it. Finished observations are kept.</p></div>`);
  else if (run.status === 'cancelled') parts.push('<div class="verdict"><h3>The run was stopped.</h3><p>Resume it to finish the remaining observations.</p></div>');
  else parts.push(`<div class="verdict"><h3>Running: ${escapeHtml(PHASE_LABELS[run.phase] || run.phase)}</h3><p>${run.progress.completed} of ${run.progress.total || '?'} observations done. Results appear here when the run completes.</p></div>`);
  if (arms?.jpeg && arms?.text) parts.push(kpisHtml(arms));
  if (arms?.jpeg && arms?.text && tradeoff) parts.push(tokenTradeoffHtml(arms, tradeoff));
  const closed = run.summary?.profiles?.closed_loop?.arms;
  if (closed?.jpeg && closed?.text) {
    parts.push(`<div class="card" style="margin-bottom:16px"><h2>Closed-loop stress test</h2><p class="muted">Here the model's own past answers get fed back into the context. Errors can compound. We report it apart from the main result.</p><p>JPEG ${closed.jpeg.field_accuracy}% · Text ${closed.text.field_accuracy}%</p></div>`);
  }
  const charts = (run.artifacts || []).filter(item => item.path.startsWith('charts/'));
  if (charts.length) {
    parts.push(`<div class="charts">${charts.map(item =>
      chartHtml(item, run)).join('')}</div>`);
  }
  if (run.progress?.completed > 0) {
    parts.push(`<div class="card" style="margin-bottom:16px"><h2>Transcript</h2><p class="muted">Every question, both answers, and the exact pages the model saw. Click an answer for the full exchange.</p><div class="transcript" data-run="${escapeHtml(run.id)}"><p class="muted">Loading…</p></div></div>`);
  }
  const files = (run.artifacts || []).filter(item => !item.path.startsWith('charts/') && !item.path.startsWith('pages/'));
  const meta = `<p class="hint">${escapeHtml(run.run_folder || `Run ${run.id.slice(0, 8)}`)} · model ${escapeHtml(run.config.model || '?')} · lengths ${escapeHtml(String(run.config.lengths || ''))} · seeds ${escapeHtml(String((run.config.seeds || []).length))} · ${escapeHtml(formatWhen(run.created_at))}</p>`;
  parts.push(`<div class="card"><h2>Raw data</h2><div class="artifact-links">${
    files.map(item => `<a href="${assetUrl(item.url)}" target="_blank" rel="noopener">${escapeHtml(item.path)}</a>`).join('') || '<span class="muted">No files yet.</span>'
  }<button class="row-action" data-copy-logs="${escapeHtml(run.id)}">Copy logs</button>${['failed', 'cancelled'].includes(run.status) ? `<button class="row-action" data-resume="${escapeHtml(run.id)}">Resume this run</button>` : ''}</div>${meta}</div>`);
  return parts.join('');
}

function chartHtml(item, run) {
  const name = item.path.split('/').pop();
  const explanation = CHART_EXPLANATIONS[name] || 'Shows one benchmark slice for comparing JPEG context against text context.';
  return `<figure class="chart"><img src="${assetUrl(item.url)}?v=${encodeURIComponent(run.updated_at)}" alt="Benchmark chart" loading="lazy"><figcaption><span>${escapeHtml(name)}</span><span class="info-tip" tabindex="0" title="${escapeHtml(explanation)}" data-tip="${escapeHtml(explanation)}">i</span></figcaption></figure>`;
}

function verdictHtml(run, arms) {
  const jpeg = arms.jpeg, text = arms.text;
  const diff = jpeg.field_accuracy - text.field_accuracy;
  let headline;
  if (Math.abs(diff) < 2) headline = 'Both arms scored about the same.';
  else if (diff > 0) headline = `JPEG won this run by ${Math.abs(diff).toFixed(1)} points.`;
  else headline = `Text won this run by ${Math.abs(diff).toFixed(1)} points.`;
  const overlap = jpeg.ci95 && text.ci95 && jpeg.ci95[0] <= text.ci95[1] && text.ci95[0] <= jpeg.ci95[1];
  const caution = overlap
    ? 'The error bars overlap. One run is not proof.'
    : 'The error bars do not overlap. For this model and rendering, the gap looks real.';
  let tokens = '';
  if (text.input_tokens > 0 && jpeg.input_tokens > 0) {
    const saved = Math.round((1 - jpeg.input_tokens / text.input_tokens) * 100);
    tokens = saved > 0
      ? `The JPEG arm used ${saved}% fewer input tokens. That is the compression we are chasing.`
      : `The JPEG arm used ${Math.abs(saved)}% more input tokens. No compression win here.`;
  }
  const pinned = run.summary?.profiles?.primary?.comparable_model === false
    ? '<p class="error-note">Warning: more than one resolved model showed up in this run. The comparison is not clean.</p>' : '';
  return `<div class="verdict"><h3>${escapeHtml(headline)}</h3><p>JPEG ${jpeg.field_accuracy}% vs text ${text.field_accuracy}% field accuracy. ${escapeHtml(caution)}</p>${tokens ? `<p>${escapeHtml(tokens)}</p>` : ''}${pinned}</div>`;
}

function kpisHtml(arms) {
  const kpi = (cls, label, value, note) => `<div class="kpi ${cls}"><span>${label}</span><b>${value}</b><small>${note}</small></div>`;
  return `<div class="kpis">${
    kpi('jpeg', 'JPEG accuracy', `${arms.jpeg.field_accuracy}%`, `95% CI ${arms.jpeg.ci95.join('–')} · ${arms.jpeg.failures} failures`)
  }${kpi('text', 'Text accuracy', `${arms.text.field_accuracy}%`, `95% CI ${arms.text.ci95.join('–')} · ${arms.text.failures} failures`)
  }${kpi('', 'Median latency', `${formatCompact(arms.jpeg.median_latency_ms)} / ${formatCompact(arms.text.median_latency_ms)} ms`, 'jpeg / text')
  }${kpi('', 'Input tokens', `${formatCompact(arms.jpeg.input_tokens)} / ${formatCompact(arms.text.input_tokens)}`, 'jpeg / text · as reported by provider')
  }</div>`;
}

function tokenTradeoffHtml(arms, tradeoff) {
  const saved = Number(tradeoff.input_tokens_saved || 0);
  const savedPct = Number(tradeoff.input_token_savings_percent || 0);
  const accuracyDelta = Number(tradeoff.accuracy_delta_points || 0);
  const latencyDelta = Number(tradeoff.latency_delta_ms || 0);
  const payloadDelta = Number(tradeoff.payload_bytes_delta_percent || 0);
  const savedText = saved >= 0
    ? `${formatCompact(saved)} fewer reported input tokens`
    : `${formatCompact(Math.abs(saved))} more reported input tokens`;
  const accuracyText = `${accuracyDelta >= 0 ? '+' : ''}${accuracyDelta.toFixed(2)} accuracy points`;
  const latencyText = `${latencyDelta >= 0 ? '+' : ''}${formatCompact(latencyDelta)} ms median latency`;
  const payloadText = `${payloadDelta >= 0 ? '+' : ''}${payloadDelta.toFixed(1)}% request bytes`;
  return `<div class="card tradeoff-card">
    <h2>Token context tradeoff</h2>
    <div class="tradeoff-grid">
      <div><span>Saved context</span><b>${escapeHtml(savedText)}</b><small>${savedPct.toFixed(1)}% vs text · provider reported</small></div>
      <div><span>Accuracy tradeoff</span><b>${escapeHtml(accuracyText)}</b><small>JPEG minus text field accuracy</small></div>
      <div><span>Runtime tradeoff</span><b>${escapeHtml(latencyText)}</b><small>${escapeHtml(payloadText)} · JPEG minus text</small></div>
    </div>
    <p class="hint">JPEG totals: ${formatCompact(arms.jpeg.input_tokens)} input tokens. Text totals: ${formatCompact(arms.text.input_tokens)} input tokens.</p>
  </div>`;
}

/* ---------- Runs list ---------- */

async function loadRuns() {
  const container = $('#runsTable');
  try {
    const runs = (await api('/api/benchmarks')).runs || [];
    if (!runs.length) { container.innerHTML = '<p class="muted">No runs yet. Go run one.</p>'; $('#runDetail').innerHTML = ''; return; }
    container.innerHTML = `<table class="runs"><thead><tr><th>Run</th><th>When</th><th>Model</th><th>Status</th><th>Progress</th><th>JPEG</th><th>Text</th><th></th></tr></thead><tbody>${
      runs.map(run => {
        const arms = run.summary?.profiles?.primary?.arms;
        return `<tr>
          <td class="mono">${escapeHtml(run.run_folder || run.id.slice(0, 8))}</td>
          <td>${escapeHtml(formatWhen(run.created_at))}</td>
          <td class="mono">${escapeHtml(run.config.model || '?')}</td>
          <td><span class="status-chip ${escapeHtml(run.status)}">${escapeHtml(run.status)}</span></td>
          <td>${run.progress.completed}/${run.progress.total || '?'}</td>
          <td>${arms?.jpeg ? `${arms.jpeg.field_accuracy}%` : '—'}</td>
          <td>${arms?.text ? `${arms.text.field_accuracy}%` : '—'}</td>
          <td><button class="row-action" data-open="${escapeHtml(run.id)}">View</button>${
            ['failed', 'cancelled'].includes(run.status) ? ` <button class="row-action" data-resume="${escapeHtml(run.id)}">Resume</button>` : ''}</td>
        </tr>`;
      }).join('')}</tbody></table>`;
    container.querySelectorAll('[data-open]').forEach(button => button.addEventListener('click', async () => {
      const run = await api(`/api/benchmarks/${button.dataset.open}`);
      selectedRunId = run.id;
      $('#runDetail').innerHTML = runDetailHtml(run);
      bindResumeButtons($('#runDetail'));
      bindCopyLogButtons($('#runDetail'));
      hydrateTranscripts($('#runDetail'));
      $('#runDetail').scrollIntoView({ behavior: 'smooth' });
    }));
    bindResumeButtons(container);
  } catch (error) {
    container.innerHTML = `<p class="error-note">Could not load runs: ${escapeHtml(error.message)}</p>`;
  }
}

function bindResumeButtons(root) {
  root.querySelectorAll('[data-resume]').forEach(button => button.addEventListener('click', async () => {
    button.disabled = true;
    try {
      const run = await api(`/api/benchmarks/${button.dataset.resume}/resume`, { method: 'POST' });
      state.latest_benchmark = run;
      selectedRunId = run.id;
      renderRunControls();
      renderResult();
      pollRun(run.id);
      switchView('experiment');
      showToast('Run resumed. Finished observations are kept.');
    } catch (error) { showToast(error.message); button.disabled = false; }
  }));
}

function bindCopyLogButtons(root) {
  root.querySelectorAll('[data-copy-logs]').forEach(button => button.addEventListener('click', async () => {
    button.disabled = true;
    try {
      const data = await api(`/api/benchmarks/${button.dataset.copyLogs}/diagnostic-log`);
      await copyText(data.log || '');
      showToast('Diagnostic logs copied.');
    } catch (error) {
      showToast(`Could not copy logs: ${error.message}`);
    } finally {
      button.disabled = false;
    }
  }));
}

async function copyText(text) {
  if (navigator.clipboard?.writeText && window.isSecureContext) {
    await navigator.clipboard.writeText(text);
    return;
  }
  const textarea = document.createElement('textarea');
  textarea.value = text;
  textarea.setAttribute('readonly', '');
  textarea.style.position = 'fixed';
  textarea.style.left = '-9999px';
  document.body.appendChild(textarea);
  textarea.select();
  document.execCommand('copy');
  textarea.remove();
}

/* ---------- Playground ---------- */

function renderTasks() {
  const container = $('#taskList');
  container.innerHTML = state.tasks.length ? state.tasks.map(task => `
    <div class="task-row ${task.status}">
      <button class="task-check" data-task-id="${task.id}" data-next="${task.status === 'done' ? 'open' : 'done'}" aria-label="${task.status === 'done' ? 'Reopen' : 'Complete'} task">${task.status === 'done' ? '✓' : ''}</button>
      <div><b>${escapeHtml(task.title)}</b><small>${escapeHtml(task.details || 'No details')}</small></div>
    </div>`).join('') : '<p class="empty">No tasks. Make one with ＋ or ask Piper.</p>';
  container.querySelectorAll('.task-check').forEach(button => button.addEventListener('click', () => updateTask(button.dataset.taskId, button.dataset.next)));
}

async function createTask(event) {
  if (event.submitter?.value === 'cancel') return;
  event.preventDefault();
  const title = $('#taskTitle').value.trim();
  if (!title) return;
  $('#saveTask').disabled = true;
  try {
    const data = await api('/api/tasks', { method: 'POST', body: JSON.stringify({ title, details: $('#taskDetails').value.trim() }) });
    state = data.state;
    $('#taskDialog').close();
    renderAll();
    showToast(data.warning ? 'Task saved. Memory image failed.' : 'Task saved. Memory made.');
  } catch (error) { showToast(error.message); }
  finally { $('#saveTask').disabled = false; }
}

async function updateTask(id, status) {
  try {
    const data = await api(`/api/tasks/${id}`, { method: 'PATCH', body: JSON.stringify({ status }) });
    state = data.state;
    renderAll();
    if (data.warning) showToast('Task updated. Memory image failed.');
  } catch (error) { showToast(error.message); }
}

function renderArtifacts() {
  $('#artifactList').innerHTML = artifacts.length ? artifacts.map(file =>
    `<a class="artifact-row" href="${assetUrl(file.url)}" target="_blank" rel="noopener">${escapeHtml(file.name)}<small>${formatSize(file.size)}</small></a>`
  ).join('') : '<p class="empty">No files yet.</p>';
}

function renderMemories() {
  const count = state.memories.length;
  $('#memoryCount').textContent = count;
  $('#edgeCount').textContent = state.edges.length;
  const health = count ? Math.round(state.memories.reduce((sum, memory) => sum + (1 - memory.decay_stage / 4), 0) / count * 100) : 0;
  $('#memoryHealth').textContent = count ? `${health}%` : '—';
  $('#memoryGrid').innerHTML = count ? state.memories.map(memory => `
    <button class="memory-tile" data-memory-id="${memory.id}">
      <img src="${assetUrl(memory.image_url)}?v=${memory.decay_stage}-${memory.access_count}" alt="" loading="lazy">
      <b>${escapeHtml(memory.label)}</b><small>stage ${memory.decay_stage}/4 · ${memory.access_count} recalls</small>
    </button>`).join('') : '<p class="empty">No memory yet. Finish some work first.</p>';
  $('#memoryGrid').querySelectorAll('.memory-tile').forEach(tile => tile.addEventListener('click', () => openMemory(tile.dataset.memoryId)));
}

function openMemory(id) {
  const memory = state.memories.find(item => item.id === id);
  if (!memory) return;
  dialogMemoryId = id;
  $('#memoryDialogTitle').textContent = memory.label;
  $('#memoryImage').src = `${assetUrl(memory.image_url)}?v=${memory.decay_stage}-${memory.access_count}`;
  const tags = Array.isArray(memory.tags) ? memory.tags.join(', ') : '';
  $('#memoryMeta').textContent = `Decay stage ${memory.decay_stage} of 4. Recalled ${memory.access_count} times. ${tags ? 'Tags: ' + tags + '.' : ''}`;
  $('#memoryDialog').showModal();
}

async function recallDialogMemory() {
  if (!dialogMemoryId) return;
  try {
    const data = await api(`/api/memories/${dialogMemoryId}/access`, { method: 'POST' });
    state = data.state;
    renderMemories();
    const memory = state.memories.find(item => item.id === dialogMemoryId);
    if (memory) {
      $('#memoryImage').src = `${assetUrl(memory.image_url)}?v=${Date.now()}`;
      $('#memoryMeta').textContent = `Fresh again. Decay stage 0 of 4. Recalled ${memory.access_count} times.`;
    }
    showToast('Memory recalled. It is sharp again.');
  } catch (error) { showToast(error.message); }
}

async function runDecay() {
  $('#decayButton').disabled = true;
  try {
    const data = await api('/api/decay', { method: 'POST' });
    state = data.state;
    renderMemories();
    showToast(data.changed ? `${data.changed} memories faded one stage.` : 'Nothing to fade.');
  } catch (error) { showToast(error.message); }
  finally { $('#decayButton').disabled = false; }
}

function renderMessages() {
  $('#welcome').hidden = state.messages.length > 0;
  $('#chatHint').textContent = state.config.demo_mode ? 'demo mode' : '';
  $('#messageList').innerHTML = state.messages.map(message => {
    const retrieved = message.meta?.retrieved?.length || 0;
    const actions = message.meta?.actions?.length || 0;
    return `<article class="message ${message.role}">
      <div class="message-head"><b>${message.role === 'assistant' ? 'Piper' : 'You'}</b><time>${formatTime(message.created_at)}</time></div>
      <div class="message-content">${escapeHtml(message.content)}</div>
      ${retrieved || actions ? `<div class="message-meta">${retrieved ? `${retrieved} memories recalled` : ''}${retrieved && actions ? ' · ' : ''}${actions ? `${actions} actions` : ''}</div>` : ''}
    </article>`;
  }).join('') + (busy ? '<article class="message assistant"><div class="message-head"><b>Piper</b><time>thinking</time></div><div class="typing"><i></i><i></i><i></i></div></article>' : '');
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
  try {
    const data = await api('/api/chat', { method: 'POST', body: JSON.stringify({ message }) });
    state = data.state;
    if (data.warning) showToast('Piper replied. Memory image failed.');
    await loadArtifacts();
  } catch (error) {
    state.messages.push({ role: 'assistant', content: `The request failed. ${error.message}`, created_at: new Date().toISOString(), meta: {} });
  } finally {
    busy = false;
    updateSendButton();
    renderAll();
  }
}

/* ---------- Helpers ---------- */

function setTheme(theme) { document.documentElement.dataset.theme = theme; localStorage.setItem(THEME_KEY, theme); }
function updateSendButton() { $('#sendButton').disabled = busy || !$('#messageInput').value.trim(); }
function showToast(message) {
  const element = $('#toast');
  element.textContent = message;
  element.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => element.classList.remove('show'), 3000);
}
function assetUrl(path = '') { return path.startsWith('http') ? path : API_BASE + path; }
function escapeHtml(value = '') { const div = document.createElement('div'); div.textContent = String(value); return div.innerHTML; }
function formatTime(value) { return new Intl.DateTimeFormat([], { hour: 'numeric', minute: '2-digit' }).format(new Date(value)); }
function formatWhen(value) { return new Intl.DateTimeFormat([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }).format(new Date(value)); }
function formatSize(bytes = 0) { return bytes < 1024 ? `${bytes}B` : bytes < 1048576 ? `${(bytes / 1024).toFixed(1)}KB` : `${(bytes / 1048576).toFixed(1)}MB`; }
function formatCompact(value = 0) { return new Intl.NumberFormat([], { notation: 'compact', maximumFractionDigits: 1 }).format(value); }
