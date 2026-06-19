const $ = (selector) => document.querySelector(selector);
const dropZone = $('#dropZone');
const fileInput = $('#fileInput');
const editor = $('#editor');
const result = $('#result');
const preview = $('#previewCanvas');
const pctx = preview.getContext('2d', { willReadFrequently: true });

let sourceFile = null;
let sourceBitmap = null;
let outputUrl = null;
let outputBlob = null;
let analysis = null;

const presets = {
  model: { target: .64, minQuality: .16, maxWidth: 750, maxHeight: 1000, sharpen: .72, flatStep: 24, grayscale: true },
  crisp: { target: .91, minQuality: .62, maxDimension: 3600, sharpen: .56, flatStep: 4 },
  balanced: { target: .84, minQuality: .48, maxDimension: 2800, sharpen: .44, flatStep: 7 },
  compact: { target: .76, minQuality: .34, maxDimension: 2100, sharpen: .32, flatStep: 10 }
};

dropZone.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') fileInput.click(); });
fileInput.addEventListener('change', () => fileInput.files[0] && loadFile(fileInput.files[0]));
['dragenter', 'dragover'].forEach(type => dropZone.addEventListener(type, e => { e.preventDefault(); dropZone.classList.add('dragging'); }));
['dragleave', 'drop'].forEach(type => dropZone.addEventListener(type, e => { e.preventDefault(); dropZone.classList.remove('dragging'); }));
dropZone.addEventListener('drop', e => e.dataTransfer.files[0] && loadFile(e.dataTransfer.files[0]));
$('#removeButton').addEventListener('click', reset);
$('#againButton').addEventListener('click', reset);
$('#compressButton').addEventListener('click', compress);
$('#downloadButton').addEventListener('click', saveOutput);
document.querySelectorAll('input[name="preset"]').forEach(input => input.addEventListener('change', () => {
  document.querySelectorAll('.preset').forEach(el => el.classList.toggle('selected', el.contains(input)));
}));

async function loadFile(file) {
  if (!/^image\/jpeg$/.test(file.type) && !/\.jpe?g$/i.test(file.name)) return setDropError('Please choose a JPEG file.');
  if (file.size > 30 * 1024 * 1024) return setDropError('That file is over the 30 MB limit.');
  try {
    sourceBitmap?.close();
    sourceFile = file;
    sourceBitmap = await createImageBitmap(file);
    dropZone.hidden = true;
    result.hidden = true;
    editor.hidden = false;
    $('#fileName').textContent = file.name;
    $('#fileMeta').textContent = `${formatBytes(file.size)} · ${sourceBitmap.width} × ${sourceBitmap.height}`;
    drawPreview(sourceBitmap);
    $('#analysisTitle').textContent = 'Analyzing text…';
    $('#analysisText').textContent = 'Mapping high-contrast edges';
    await nextFrame();
    analysis = analyzeCanvas(pctx.getImageData(0, 0, preview.width, preview.height));
    $('#analysisTitle').textContent = analysis.edgeDensity > .18 ? 'Dense text detected' : analysis.edgeDensity > .07 ? 'Text regions detected' : 'Light text detected';
    $('#analysisText').textContent = `${Math.round(analysis.edgeDensity * 100)}% edge detail · protection map ready`;
  } catch { reset(); setDropError('This JPEG could not be decoded.'); }
}

function drawPreview(bitmap) {
  const scale = Math.min(1, 900 / bitmap.width, 650 / bitmap.height);
  preview.width = Math.max(1, Math.round(bitmap.width * scale));
  preview.height = Math.max(1, Math.round(bitmap.height * scale));
  pctx.drawImage(bitmap, 0, 0, preview.width, preview.height);
}

function analyzeCanvas(image) {
  const { data, width, height } = image;
  const gray = new Uint8Array(width * height);
  for (let i = 0, p = 0; i < data.length; i += 4, p++) gray[p] = (data[i] * 77 + data[i + 1] * 150 + data[i + 2] * 29) >> 8;
  let edges = 0, samples = 0, contrast = 0;
  for (let y = 1; y < height - 1; y += 2) for (let x = 1; x < width - 1; x += 2) {
    const p = y * width + x;
    const gx = -gray[p-width-1] + gray[p-width+1] - 2*gray[p-1] + 2*gray[p+1] - gray[p+width-1] + gray[p+width+1];
    const gy = -gray[p-width-1] - 2*gray[p-width] - gray[p-width+1] + gray[p+width-1] + 2*gray[p+width] + gray[p+width+1];
    const magnitude = Math.min(255, (Math.abs(gx) + Math.abs(gy)) / 4);
    if (magnitude > 28) { edges++; contrast += magnitude; }
    samples++;
  }
  return { edgeDensity: edges / Math.max(1, samples), edgeContrast: contrast / Math.max(1, edges) };
}

async function compress() {
  if (!sourceBitmap) return;
  const button = $('#compressButton');
  button.disabled = true;
  const preset = presets[document.querySelector('input[name="preset"]:checked').value];
  try {
    setStatus('Preparing text protection map…');
    await nextFrame();
    const scale = preset.maxWidth
      ? Math.min(1, preset.maxWidth / sourceBitmap.width, preset.maxHeight / sourceBitmap.height)
      : Math.min(1, preset.maxDimension / Math.max(sourceBitmap.width, sourceBitmap.height));
    const canvas = document.createElement('canvas');
    canvas.width = Math.max(1, Math.round(sourceBitmap.width * scale));
    canvas.height = Math.max(1, Math.round(sourceBitmap.height * scale));
    const ctx = canvas.getContext('2d', { willReadFrequently: true });
    ctx.imageSmoothingQuality = 'high';
    ctx.drawImage(sourceBitmap, 0, 0, canvas.width, canvas.height);
    const original = ctx.getImageData(0, 0, canvas.width, canvas.height);
    setStatus('Protecting ink and quieting paper…');
    await nextFrame();
    const guarded = preprocess(original, preset, $('#inkGuard').checked);
    ctx.putImageData(guarded, 0, 0);
    const protectedReference = createReadabilitySample(guarded);

    let low = preset.minQuality, high = .94, best = null, bestQuality = high, bestScore = 1;
    for (let pass = 0; pass < 5; pass++) {
      const quality = pass === 0 ? high : (low + high) / 2;
      setStatus(`Readability check ${pass + 1} of 5…`);
      const blob = await canvasToBlob(canvas, quality);
      const score = await readabilityScore(blob, protectedReference, canvas.width, canvas.height);
      if (score >= preset.target) { best = blob; bestQuality = quality; bestScore = score; high = quality; }
      else low = quality;
    }
    if (!best) { best = await canvasToBlob(canvas, Math.min(.96, low + .08)); bestQuality = Math.min(.96, low + .08); bestScore = preset.target; }
    showResult(best, bestQuality, bestScore, canvas.width, canvas.height);
  } catch (error) {
    console.error(error);
    setStatus('Compression failed. Try a smaller image.');
  } finally { button.disabled = false; }
}

function preprocess(image, preset, inkGuard) {
  const { data, width, height } = image;
  const out = new ImageData(new Uint8ClampedArray(data), width, height);
  const src = data, dst = out.data;
  const lumaAt = (x, y) => { const i = (y * width + x) * 4; return (src[i]*77 + src[i+1]*150 + src[i+2]*29) >> 8; };
  for (let y = 1; y < height - 1; y++) for (let x = 1; x < width - 1; x++) {
    const i = (y * width + x) * 4;
    const center = lumaAt(x, y);
    const blur = (lumaAt(x-1,y-1)+lumaAt(x,y-1)+lumaAt(x+1,y-1)+lumaAt(x-1,y)+center+lumaAt(x+1,y)+lumaAt(x-1,y+1)+lumaAt(x,y+1)+lumaAt(x+1,y+1))/9;
    const edge = Math.abs(center - blur);
    if (edge > 8) {
      const amount = inkGuard ? preset.sharpen : preset.sharpen * .35;
      if (preset.grayscale) {
        const value = center + (center - blur) * amount;
        dst[i] = value; dst[i+1] = value; dst[i+2] = value;
      } else {
        for (let c = 0; c < 3; c++) dst[i+c] = src[i+c] + (center - blur) * amount;
      }
    } else {
      if (preset.grayscale) {
        const value = Math.round(center / preset.flatStep) * preset.flatStep;
        dst[i] = value; dst[i+1] = value; dst[i+2] = value;
      } else {
        for (let c = 0; c < 3; c++) dst[i+c] = Math.round(src[i+c] / preset.flatStep) * preset.flatStep;
      }
    }
  }
  return out;
}

function createReadabilitySample(image) {
  const stride = Math.max(1, Math.floor(Math.max(image.width, image.height) / 700));
  const points = [];
  const d = image.data, w = image.width;
  const lum = i => (d[i]*77 + d[i+1]*150 + d[i+2]*29) >> 8;
  for (let y = 1; y < image.height-1; y += stride) for (let x = 1; x < w-1; x += stride) {
    const i = (y*w+x)*4, c = lum(i), delta = Math.max(Math.abs(c-lum(i-4)), Math.abs(c-lum(i+4)), Math.abs(c-lum(i-w*4)), Math.abs(c-lum(i+w*4)));
    if (delta > 25) points.push([x,y,delta]);
  }
  if (points.length > 18000) return points.filter((_, i) => i % Math.ceil(points.length / 18000) === 0);
  return points;
}

async function readabilityScore(blob, reference, width, height) {
  const bitmap = await createImageBitmap(blob);
  const c = document.createElement('canvas'); c.width = width; c.height = height;
  const cx = c.getContext('2d', { willReadFrequently: true }); cx.drawImage(bitmap, 0, 0); bitmap.close();
  const d = cx.getImageData(0,0,width,height).data;
  const lum = i => (d[i]*77+d[i+1]*150+d[i+2]*29)>>8;
  let retained = 0;
  for (const [x,y,originalDelta] of reference) {
    const i=(y*width+x)*4, center=lum(i);
    const delta=Math.max(Math.abs(center-lum(i-4)),Math.abs(center-lum(i+4)),Math.abs(center-lum(i-width*4)),Math.abs(center-lum(i+width*4)));
    retained += Math.min(1, delta / originalDelta);
  }
  return reference.length ? retained / reference.length : 1;
}

function canvasToBlob(canvas, quality) { return new Promise((resolve, reject) => canvas.toBlob(blob => blob ? resolve(blob) : reject(new Error('Encoding failed')), 'image/jpeg', quality)); }

function showResult(blob, quality, score, width, height) {
  if (outputUrl) URL.revokeObjectURL(outputUrl);
  outputBlob = blob;
  outputUrl = URL.createObjectURL(blob);
  $('#beforeSize').textContent = formatBytes(sourceFile.size);
  $('#afterSize').textContent = formatBytes(blob.size);
  const saved = Math.max(0, Math.round((1 - blob.size / sourceFile.size) * 100));
  $('#savedPercent').textContent = `${saved}%`;
  const tokenRange = estimateVisualTokens(width, height);
  $('#resultSummary').textContent = `${width} × ${height}px · ${Math.round(score*100)}% edge contrast retained · JPEG quality ${Math.round(quality*100)}`;
  $('#tokenEstimate').textContent = `${tokenRange.low}–${tokenRange.high} visual tokens`;
  editor.hidden = true; result.hidden = false;
}

function estimateVisualTokens(width, height) {
  const pixels = width * height;
  return { low: Math.max(1, Math.round(pixels / 758)), high: Math.max(1, Math.round(pixels / 596)) };
}

async function saveOutput() {
  if (!outputBlob || !sourceFile) return;
  const button = $('#downloadButton');
  const label = button.querySelector('span');
  const fileName = sourceFile.name.replace(/\.jpe?g$/i, '') + '-glyph.jpg';
  button.disabled = true;

  try {
    if ('showSaveFilePicker' in window) {
      const handle = await window.showSaveFilePicker({
        suggestedName: fileName,
        types: [{ description: 'JPEG image', accept: { 'image/jpeg': ['.jpg', '.jpeg'] } }]
      });
      const writable = await handle.createWritable();
      await writable.write(outputBlob);
      await writable.close();
    } else {
      triggerDownload(fileName);
    }
    label.textContent = 'Saved';
    setTimeout(() => { label.textContent = 'Save JPEG'; }, 1800);
  } catch (error) {
    if (error.name !== 'AbortError') {
      triggerDownload(fileName);
      label.textContent = 'Download started';
      setTimeout(() => { label.textContent = 'Save JPEG'; }, 1800);
    }
  } finally {
    button.disabled = false;
  }
}

function triggerDownload(fileName) {
  const link = document.createElement('a');
  link.href = outputUrl;
  link.download = fileName;
  link.rel = 'noopener';
  document.body.appendChild(link);
  link.click();
  link.remove();
}

function reset() {
  sourceBitmap?.close(); sourceBitmap = null; sourceFile = null; analysis = null;
  if (outputUrl) URL.revokeObjectURL(outputUrl); outputUrl = null; outputBlob = null;
  fileInput.value = ''; editor.hidden = true; result.hidden = true; dropZone.hidden = false;
  setStatus('');
}
function setStatus(text) { $('#status').textContent = text; }
function setDropError(text) { dropZone.querySelector('p').textContent = text; setTimeout(() => dropZone.querySelector('p').textContent = 'or click to choose a file', 2600); }
function formatBytes(bytes) { if (!bytes) return '0 B'; const units=['B','KB','MB']; const i=Math.min(2,Math.floor(Math.log(bytes)/Math.log(1024))); return `${(bytes/1024**i).toFixed(i ? 1 : 0)} ${units[i]}`; }
function nextFrame() { return new Promise(resolve => requestAnimationFrame(() => setTimeout(resolve, 0))); }
