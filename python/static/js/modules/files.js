/**
 * files.js — 目录浏览模块
 * 包含：目录加载、渲染、上级目录导航、排序切换、分页、多选、批量刮削。
 * 依赖：api.js、state.js（fileSort / filePath / TOKEN）、utils.js、detail.js（showFileMeta）
 */

const PAGE_SIZE = 200;

function normalizePath(path) {
  return path.replace(/\\/g, '/');
}
let currentOffset = 0;
let totalItems = 0;
let currentSearch = '';

let fileSelectMode = false;
let fileSelectedPaths = new Set();
const FILE_SELECT_LIMIT = 20;

/* ═══════════════════════════════════════════════════════════
   LOAD & RENDER
   ═══════════════════════════════════════════════════════════ */

async function loadFiles(path, forceRefresh = false) {
  filePath = normalizePath(path || '');
  currentOffset = 0;
  currentSearch = document.getElementById('file-search')?.value?.trim() || '';
  document.getElementById('file-path-text').textContent = '/' + filePath;

  await fetchFiles();
}

async function fetchFiles() {
  document.getElementById('file-list').innerHTML = '<div class="loading-row">加载中...</div>';
  document.getElementById('pagination-info').textContent = '加载中...';

  try {
    const params = new URLSearchParams({
      path: filePath,
      limit: PAGE_SIZE,
      offset: currentOffset,
      sort: fileSort || 'name',
      folders_first: foldersFirst ? 'true' : 'false'
    });
    if (currentSearch) {
      params.set('search', currentSearch);
    }

    const data = await GET(`/files?${params.toString()}`);
    currentFiles = data.items || [];
    totalItems = data.total || 0;

    renderFiles(currentFiles);
    updatePagination();
  } catch (e) {
    document.getElementById('file-list').innerHTML =
      `<div class="loading-row" style="color:var(--red)">加载失败：${esc(e.message)}</div>`;
  }
}

function renderFiles(items) {
  if (items.length === 0) {
    document.getElementById('file-list').innerHTML = '<div class="loading-row">此目录为空</div>';
    return;
  }
  const html = items.map(f => {
    const isSelected = fileSelectedPaths.has(f.path);
    const canSelect = f.is_dir || f.is_audio;
    const selectDisabled = canSelect && !isSelected && fileSelectedPaths.size >= FILE_SELECT_LIMIT;

    return `
    <div class="file-row ${isSelected ? 'selected' : ''} ${selectDisabled && fileSelectMode ? 'select-disabled' : ''}"
         data-path="${escJs(f.path)}"
         data-is-dir="${f.is_dir}"
         data-is-audio="${f.is_audio}"
         onclick="${fileSelectMode
        ? (canSelect ? `toggleFileSelect('${escJs(f.path)}', ${f.is_dir}, ${f.is_audio})` : '')
        : (f.is_dir ? `loadFiles('${escJs(f.path)}')` : (f.is_audio ? `showFileMeta('${escJs(f.path)}')` : ''))
      }"
         style="${(f.is_dir || f.is_audio) && !fileSelectMode ? 'cursor:pointer' : ''}">
      <div class="fr fr-check">
        ${fileSelectMode && canSelect
        ? `<i class="bi ${isSelected ? 'bi-check-circle-fill' : 'bi-circle'} ${isSelected ? 'selected' : ''}" 
               style="font-size:16px;${selectDisabled ? 'opacity:0.3;' : ''}"></i>`
        : (f.is_dir
          ? '<i class="bi bi-folder"></i>'
          : f.is_audio
            ? '<i class="bi bi-music-note"></i>'
            : '<i class="bi bi-file-earmark"></i>')
      }
      </div>
      <div class="fr fr-name">${esc(f.name)}</div>
      <div class="fr fr-dir">${esc('/' + normalizePath(f.path))}</div>
      <div class="fr fr-type ${f.ext === 'flac' ? 'fmt-flac' : f.ext === 'mp3' ? 'fmt-mp3' : f.ext === 'm4a' ? 'fmt-m4a' : ''}">
        ${f.is_dir ? 'DIR' : f.ext.toUpperCase()}
      </div>
      <div class="fr fr-size">${f.is_dir ? '—' : fmtSize(f.size)}</div>
      <div class="fr fr-date">${f.mtime}</div>
      <div class="fr fr-download">
        ${f.is_dir ? '' : `
        <i class="bi bi-download"
           onclick="event.stopPropagation();downloadFileOrDir('${escJs(f.path)}', '${escJs(f.name)}', false)">
        </i>
        `}
      </div>
    </div>
  `;
  }).join('');

  document.getElementById('file-list').innerHTML = html;
  updateFileSelectUI();
}

/**
 * 就地同步多选状态到已渲染的行（class / 图标 / 禁用态），
 * 避免每次勾选都重建整个列表 DOM（200 行时尤其明显）。
 */
function syncFileSelectionUI() {
  const list = document.getElementById('file-list');
  if (!list) return;
  const rows = list.querySelectorAll('.file-row');
  rows.forEach((row, i) => {
    const f = currentFiles[i];
    if (!f) return;
    const isSelected = fileSelectedPaths.has(f.path);
    const canSelect = f.is_dir || f.is_audio;
    const selectDisabled = canSelect && !isSelected && fileSelectedPaths.size >= FILE_SELECT_LIMIT;

    row.classList.toggle('selected', isSelected);
    row.classList.toggle('select-disabled', !!(selectDisabled && fileSelectMode));

    if (fileSelectMode && canSelect) {
      const icon = row.querySelector('.fr-check i');
      if (icon) {
        icon.className = `bi ${isSelected ? 'bi-check-circle-fill' : 'bi-circle'}${isSelected ? ' selected' : ''}`;
        icon.style.opacity = selectDisabled ? '0.3' : '';
      }
    }
  });
}

/* ═══════════════════════════════════════════════════════════
   MULTI-SELECT
   ═══════════════════════════════════════════════════════════ */

function toggleFileSelectMode() {
  fileSelectMode = !fileSelectMode;
  if (!fileSelectMode) {
    fileSelectedPaths.clear();
  }
  renderFiles(currentFiles);
  updateFileSelectUI();
}

let _folderAudioCounts = {};

async function toggleFileSelect(path, isDir, isAudio) {
  if (fileSelectedPaths.has(path)) {
    fileSelectedPaths.delete(path);
  } else {
    if (isDir) {
      if (!_folderAudioCounts[path]) {
        try {
          const data = await GET(`/files/audio-count?paths=${encodeURIComponent(path)}`);
          _folderAudioCounts[path] = data.counts[path] || 0;
        } catch (_) {
          _folderAudioCounts[path] = 1;
        }
      }
      const count = _folderAudioCounts[path];
      const currentAudioCount = _countSelectedAudio();
      if (currentAudioCount + count > FILE_SELECT_LIMIT) {
        showToast(`文件夹内 ${count} 个音频文件，加上已选将超出 ${FILE_SELECT_LIMIT} 条限制`, 'warn');
        return;
      }
      showToast(`文件夹内含 ${count} 个音频文件`, 'info');
    }
    if (fileSelectedPaths.size >= FILE_SELECT_LIMIT) {
      showToast(`最多选择 ${FILE_SELECT_LIMIT} 条`, 'warn');
      return;
    }
    fileSelectedPaths.add(path);
  }
  syncFileSelectionUI();
  updateFileSelectUI();
}

function _countSelectedAudio() {
  let count = 0;
  for (const path of fileSelectedPaths) {
    const item = currentFiles.find(f => f.path === path);
    if (item && item.is_audio) count++;
    if (item && item.is_dir) count += _folderAudioCounts[path] || 1;
  }
  return count;
}

function clearFileSelection() {
  fileSelectedPaths.clear();
  _folderAudioCounts = {};
  syncFileSelectionUI();
  updateFileSelectUI();
}

function updateFileSelectUI() {
  const toggleBtn = document.getElementById('file-select-toggle');
  const selectText = document.getElementById('file-select-text');
  const batchBtn = document.getElementById('batch-scrape-btn');
  const selectCount = document.getElementById('file-select-count');

  if (toggleBtn) {
    toggleBtn.classList.toggle('active', fileSelectMode);
  }
  if (selectText) {
    selectText.textContent = fileSelectMode ? '退出多选' : '多选';
  }
  if (batchBtn) {
    batchBtn.style.display = fileSelectMode && fileSelectedPaths.size > 0 ? '' : 'none';
  }
  if (selectCount) {
    if (fileSelectMode && fileSelectedPaths.size > 0) {
      selectCount.style.display = '';
      const audioCount = _countSelectedAudio();
      selectCount.textContent = audioCount !== fileSelectedPaths.size
        ? `已选 ${fileSelectedPaths.size} 项 / ${audioCount} 首音频`
        : `已选 ${fileSelectedPaths.size}/${FILE_SELECT_LIMIT}`;
    } else {
      selectCount.style.display = 'none';
    }
  }
}

async function startBatchScrape() {
  if (fileSelectedPaths.size === 0) {
    showToast('请先选择文件或文件夹', 'warn');
    return;
  }

  const selectedPaths = Array.from(fileSelectedPaths);
  let totalCount = 0;

  for (const path of selectedPaths) {
    const item = currentFiles.find(f => f.path === path);
    if (!item) continue;

    if (item.is_dir) {
      try {
        const data = await GET(`/files/audio-count?paths=${encodeURIComponent(path)}`);
        const count = data.counts[path] || 0;
        if (totalCount + count > FILE_SELECT_LIMIT) {
          const remaining = FILE_SELECT_LIMIT - totalCount;
          if (remaining <= 0) break;
          showToast(`文件夹 ${item.name} 内有 ${count} 个音频文件，已截取前 ${remaining} 首`, 'info');
        }
        totalCount += Math.min(count, FILE_SELECT_LIMIT - totalCount);
      } catch (e) {
        showToast(`获取文件夹 ${item.name} 文件数失败`, 'error');
      }
    } else if (item.is_audio) {
      totalCount++;
    }
  }

  if (totalCount === 0) {
    showToast('所选内容中没有音频文件', 'warn');
    return;
  }

  openBatchScrapeModal(selectedPaths);
}

/* ═══════════════════════════════════════════════════════════
   PAGINATION
   ═══════════════════════════════════════════════════════════ */

function updatePagination() {
  const start = totalItems === 0 ? 0 : currentOffset + 1;
  const end = Math.min(currentOffset + PAGE_SIZE, totalItems);
  document.getElementById('pagination-info').textContent =
    totalItems === 0 ? '无文件' : `${start}-${end} / ${totalItems}`;

  document.getElementById('btn-prev').disabled = currentOffset <= 0;
  document.getElementById('btn-next').disabled = currentOffset + PAGE_SIZE >= totalItems;
}

function goToPage(offset) {
  currentOffset = Math.max(0, Math.min(offset, totalItems - 1));
  currentOffset = Math.floor(currentOffset / PAGE_SIZE) * PAGE_SIZE;
  fetchFiles();
}

/* ═══════════════════════════════════════════════════════════
   SEARCH
   ═══════════════════════════════════════════════════════════ */

function filterFiles(query) {
  currentSearch = query?.trim() || '';
  currentOffset = 0;
  fetchFiles();
}

// 搜索框输入去抖：避免每次按键都发起一次后端请求。
let _fileSearchTimer = null;
function onFileSearchInput(query) {
  clearTimeout(_fileSearchTimer);
  _fileSearchTimer = setTimeout(() => filterFiles(query), 250);
}

/* ═══════════════════════════════════════════════════════════
   NAVIGATION
   ═══════════════════════════════════════════════════════════ */

function filesGoUp() {
  const parts = filePath.split('/').filter(Boolean);
  parts.pop();
  loadFiles(parts.join('/'));
}

/* ═══════════════════════════════════════════════════════════
   SORT
   ═══════════════════════════════════════════════════════════ */

function setFileSort(mode) {
  fileSort = mode;
  document.querySelectorAll('.sort-chip').forEach(c => c.classList.remove('active'));
  document.getElementById('sort-' + mode).classList.add('active');
  currentOffset = 0;
  fetchFiles();
}

function toggleFoldersFirst() {
  foldersFirst = !foldersFirst;
  const btn = document.getElementById('folders-first');
  if (foldersFirst) {
    btn.classList.add('active');
    btn.textContent = '文件夹优先';
  } else {
    btn.classList.remove('active');
    btn.textContent = '文件优先';
  }
  currentOffset = 0;
  fetchFiles();
}

/* ═══════════════════════════════════════════════════════════
   DOWNLOAD
   ═══════════════════════════════════════════════════════════ */

function downloadFile(url, filename) {
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
}

async function downloadFileOrDir(filePath, fileName, isDir) {
  try {
    streamDownloadGet(`/files/download?path=${encodeURIComponent(filePath)}`);
  } catch (e) {
    showToast(`下载失败: ${e.message}`, 'error');
  }
}

/* ═══════════════════════════════════════════════════════════
   UPLOAD
   ═══════════════════════════════════════════════════════════ */

/* ═══════════════════════════════════════════════════════════
   UPLOAD PROGRESS PANEL
   逐文件上传 → 检查 → 内联处理冲突 → 写入，全部在一个面板内完成。
   通过 XMLHttpRequest.upload.onprogress 获取真实传输进度，无需轮询。
   ═══════════════════════════════════════════════════════════ */

let uploadItems = [];
let uploadRunning = false;
let uploadPhase = 'idle'; // idle | checking | conflict | committing | done
let uploadCurrentXhr = null;
let uploadPanelVisible = false;
let uploadIndicatorTimer = null;

function triggerFileUpload() {
  const input = document.getElementById('file-upload-input');
  input.value = '';
  input.click();
}

async function handleFileUpload(fileList) {
  if (uploadRunning) {
    showToast('已有上传任务进行中', 'info');
    return;
  }
  if (!fileList || fileList.length === 0) return;

  const files = Array.from(fileList);
  const valid = files.filter(f => {
    const ext = f.name.split('.').pop().toLowerCase();
    return ext === 'flac' || ext === 'mp3' || ext === 'm4a';
  });
  const invalid = files.filter(f => {
    const ext = f.name.split('.').pop().toLowerCase();
    return ext !== 'flac' && ext !== 'mp3' && ext !== 'm4a';
  });

  if (invalid.length > 0) {
    showToast(`${invalid.length} 个文件格式不支持，已过滤（仅支持 FLAC/MP3/M4A）`, 'warn');
  }
  if (valid.length === 0) {
    showToast('没有可上传的文件', 'warn');
    return;
  }

  uploadItems = valid.map((f, i) => ({
    idx: i,
    file: f,
    name: f.name,
    size: f.size,
    status: 'pending',
    progress: 0,
    tempId: null,
    title: '',
    artist: '',
    album: '',
    existing: null,
    action: 'skip',
    error: '',
  }));
  uploadRunning = true;
  uploadPhase = 'checking';
  uploadPanelVisible = true;

  renderUploadModal();
  openModal('upload-progress-modal');
  updateUploadIndicator();

  await runUploadCheck();
}

/* ── 面板显隐：隐藏后任务继续在后台运行 ── */

/** 重新打开上传面板（若存在上传任务），并刷新为最新状态 */
function openUploadPanel() {
  if (uploadItems.length === 0) return;
  uploadPanelVisible = true;
  renderUploadModal();
  openModal('upload-progress-modal');
  updateUploadIndicator();
}

/** 隐藏上传面板，但不取消任务 */
function hideUploadPanel() {
  uploadPanelVisible = false;
  closeModal('upload-progress-modal');
  updateUploadIndicator();
}

/** 根据上传任务状态刷新浮动入口（仅在面板隐藏且有任务时显示） */
function updateUploadIndicator() {
  const btn = document.getElementById('upload-float-btn');
  if (!btn) return;

  if (uploadItems.length === 0 || uploadPanelVisible) {
    btn.style.display = 'none';
    clearTimeout(uploadIndicatorTimer);
    return;
  }

  btn.style.display = '';
  const icon = btn.querySelector('i');
  const text = document.getElementById('upload-float-text');
  const total = uploadItems.length;
  // 已处理（完成上传+检查，或已写入）的数量：检查阶段每条落定为
  // new / conflict / error 时即计入，避免一直显示 0/N
  const processed = uploadItems.filter(i =>
    i.status === 'new' || i.status === 'conflict' || i.status === 'committing' ||
    i.status === 'done' || i.status === 'skipped' || i.status === 'error'
  ).length;

  if (uploadPhase === 'done') {
    const fail = uploadItems.filter(i => i.status === 'error').length;
    if (icon) icon.className = fail > 0 ? 'bi bi-exclamation-circle-fill' : 'bi bi-check-circle-fill';
    if (text) text.textContent = fail > 0 ? `上传完成（${fail} 个失败）` : '上传完成';
    // 完成后隐藏面板的入口稍后自动消失
    clearTimeout(uploadIndicatorTimer);
    uploadIndicatorTimer = setTimeout(() => {
      if (uploadPhase === 'done' && !uploadPanelVisible) {
        uploadItems = [];
        uploadPhase = 'idle';
        updateUploadIndicator();
      }
    }, 8000);
  } else if (uploadPhase === 'conflict') {
    if (icon) icon.className = 'bi bi-exclamation-triangle-fill';
    if (text) text.textContent = '上传待处理';
  } else {
    if (icon) icon.className = 'bi bi-arrow-repeat spin';
    if (text) text.textContent = `上传中 ${processed}/${total}`;
  }
}

function renderUploadModal() {
  const list = document.getElementById('upload-progress-list');
  if (list) list.innerHTML = uploadItems.map(uploadRowHtml).join('');
  updateUploadSummary();
  updateUploadFooter();
}

function uploadRowHtml(item) {
  return `
    <div class="upload-row" id="upload-row-${item.idx}">
      <div class="upload-row-head">
        <div class="upload-row-name" title="${esc(item.name)}">${esc(item.name)}</div>
        <div class="upload-row-size">${fmtSize(item.size)}</div>
      </div>
      <div class="upload-row-state">${uploadStateHtml(item)}</div>
    </div>`;
}

function uploadStateHtml(item) {
  switch (item.status) {
    case 'pending':
      return '<span class="upload-state upload-state-muted"><i class="bi bi-hourglass"></i> 等待中</span>';
    case 'uploading':
      return `
        <div class="upload-progress-bar"><div class="upload-progress-bar-fill" style="width:${item.progress}%"></div></div>
        <span class="upload-state upload-state-muted">上传中 ${item.progress}%</span>`;
    case 'checking':
      return '<span class="upload-state upload-state-muted"><i class="bi bi-arrow-repeat spin"></i> 检查中…</span>';
    case 'new':
      return `<span class="upload-state upload-state-new"><i class="bi bi-plus-circle"></i> 新文件${uploadMetaText(item) ? ' · ' + esc(uploadMetaText(item)) : ''}</span>`;
    case 'conflict':
      return `
        <span class="upload-state upload-state-warn"><i class="bi bi-exclamation-triangle-fill"></i> 已存在于库中</span>
        <div class="upload-conflict-existing">
          已在 <span class="upload-conflict-dir">${esc((item.existing && item.existing.rel_dir) || '')}</span>
          <span class="upload-conflict-filename">${esc((item.existing && item.existing.filename) || '')}</span>
        </div>
        <div class="upload-row-choice">
          <span class="upload-choice-label">选择处理方式：</span>
          <button class="upload-choice-btn${item.action === 'overwrite' ? ' active' : ''}" onclick="setConflictAction(${item.idx}, 'overwrite')">${item.action === 'overwrite' ? '✓ ' : ''}覆盖</button>
          <button class="upload-choice-btn${item.action === 'skip' ? ' active' : ''}" onclick="setConflictAction(${item.idx}, 'skip')">${item.action === 'skip' ? '✓ ' : ''}跳过</button>
        </div>`;
    case 'committing':
      return '<span class="upload-state upload-state-muted"><i class="bi bi-arrow-repeat spin"></i> 写入中…</span>';
    case 'done':
      return '<span class="upload-state upload-state-done"><i class="bi bi-check-circle-fill"></i> 完成</span>';
    case 'skipped':
      return '<span class="upload-state upload-state-muted"><i class="bi bi-skip-forward"></i> 已跳过</span>';
    case 'error':
      return `<span class="upload-state upload-state-error"><i class="bi bi-x-circle-fill"></i> ${esc(item.error || '失败')}</span>`;
    default:
      return '';
  }
}

function uploadMetaText(item) {
  if (item.title || item.artist) {
    return [item.title, item.artist].filter(Boolean).join(' · ');
  }
  return '';
}

function renderUploadRow(item) {
  const row = document.getElementById('upload-row-' + item.idx);
  if (row) row.outerHTML = uploadRowHtml(item);
  updateUploadIndicator();
}

function updateUploadSummary() {
  const el = document.getElementById('upload-progress-summary');
  if (!el) return;
  const total = uploadItems.length;
  const done = uploadItems.filter(i => i.status === 'done').length;
  const skipped = uploadItems.filter(i => i.status === 'skipped').length;
  const conflictItems = uploadItems.filter(i => i.status === 'conflict');
  const conflict = conflictItems.length;
  const error = uploadItems.filter(i => i.status === 'error').length;
  const parts = [`共 ${total} 个文件`];
  if (done) parts.push(`完成 ${done}`);
  if (skipped) parts.push(`跳过 ${skipped}`);
  if (conflict) {
    const ow = conflictItems.filter(i => i.action === 'overwrite').length;
    parts.push(`待处理冲突 ${conflict}（覆盖 ${ow} · 跳过 ${conflict - ow}）`);
  }
  if (error) parts.push(`失败 ${error}`);
  el.textContent = parts.join(' · ');
  updateUploadIndicator();
}

function updateUploadFooter() {
  const info = document.getElementById('upload-progress-info');
  const cancelBtn = document.getElementById('upload-cancel-btn');
  const overwriteAll = document.getElementById('upload-overwrite-all-btn');
  const skipAll = document.getElementById('upload-skip-all-btn');
  const confirmBtn = document.getElementById('upload-confirm-btn');
  const doneBtn = document.getElementById('upload-done-btn');
  const hideBtn = document.getElementById('upload-hide-btn');
  const show = (el, visible) => { if (el) el.style.display = visible ? '' : 'none'; };

  // 有任务进行时始终允许隐藏面板（任务在后台继续）
  show(hideBtn, uploadItems.length > 0);

  if (uploadPhase === 'checking') {
    if (info) info.textContent = '正在上传并检查…';
    show(cancelBtn, true); show(overwriteAll, false); show(skipAll, false);
    show(confirmBtn, false); show(doneBtn, false);
  } else if (uploadPhase === 'conflict') {
    const conflicts = uploadItems.filter(i => i.status === 'conflict');
    const n = conflicts.length;
    const ow = conflicts.filter(i => i.action === 'overwrite').length;
    const sk = n - ow;
    if (info) info.textContent = `冲突处理：覆盖 ${ow} 个 · 跳过 ${sk} 个`;
    if (overwriteAll) overwriteAll.classList.toggle('active', n > 0 && ow === n);
    if (skipAll) skipAll.classList.toggle('active', n > 0 && sk === n);
    show(cancelBtn, true); show(overwriteAll, true); show(skipAll, true);
    show(confirmBtn, true); show(doneBtn, false);
  } else if (uploadPhase === 'committing') {
    if (info) info.textContent = '正在写入文件…';
    show(cancelBtn, false); show(overwriteAll, false); show(skipAll, false);
    show(confirmBtn, false); show(doneBtn, false);
  } else if (uploadPhase === 'done') {
    const ok = uploadItems.filter(i => i.status === 'done').length;
    const fail = uploadItems.filter(i => i.status === 'error').length;
    if (info) info.textContent = fail > 0 ? `完成 ${ok} 个，失败 ${fail} 个` : `全部完成（${ok} 个）`;
    show(cancelBtn, false); show(overwriteAll, false); show(skipAll, false);
    show(confirmBtn, false); show(doneBtn, true);
  } else {
    show(cancelBtn, true); show(overwriteAll, false); show(skipAll, false);
    show(confirmBtn, false); show(doneBtn, false);
  }

  updateUploadIndicator();
}

async function runUploadCheck() {
  for (const item of uploadItems) {
    if (!uploadRunning) { updateUploadFooter(); return; }
    item.status = 'uploading';
    item.progress = 0;
    renderUploadRow(item);

    const res = await uploadOneFile(item);

    if (res.data) {
      classifyUploadResult(item, res.data);
    } else {
      item.status = 'error';
      item.error = res.error || '上传失败';
    }
    renderUploadRow(item);
    if (!uploadRunning) { updateUploadSummary(); updateUploadFooter(); return; }
  }

  updateUploadSummary();

  if (uploadItems.some(i => i.status === 'conflict')) {
    uploadPhase = 'conflict';
    updateUploadFooter();
  } else {
    await runUploadCommit();
  }
}

function classifyUploadResult(item, data) {
  if (data.errors && data.errors.length > 0) {
    item.status = 'error';
    item.error = data.errors[0].error || '处理失败';
    return;
  }
  if (data.conflicts && data.conflicts.length > 0) {
    const c = data.conflicts[0];
    item.status = 'conflict';
    item.tempId = c.temp_id;
    item.title = c.title || '';
    item.artist = c.artist || '';
    item.album = c.album || '';
    item.existing = c.existing || null;
    item.action = 'skip';
    return;
  }
  if (data.new_files && data.new_files.length > 0) {
    const n = data.new_files[0];
    item.status = 'new';
    item.tempId = n.temp_id;
    item.title = n.title || '';
    item.artist = n.artist || '';
    item.album = n.album || '';
    item.action = 'new';
    return;
  }
  item.status = 'error';
  item.error = '未知结果';
}

function uploadOneFile(item) {
  return new Promise(resolve => {
    const xhr = new XMLHttpRequest();
    uploadCurrentXhr = xhr;
    xhr.open('POST', '/api/files/upload-check');
    xhr.setRequestHeader('X-Token', TOKEN);

    xhr.upload.onprogress = e => {
      if (e.lengthComputable && item.status === 'uploading') {
        const pct = Math.round((e.loaded / e.total) * 100);
        if (pct !== item.progress) {
          item.progress = pct;
          renderUploadRow(item);
        }
      }
    };
    xhr.upload.onload = () => {
      if (item.status === 'uploading') {
        item.progress = 100;
        item.status = 'checking';
        renderUploadRow(item);
      }
    };
    xhr.onload = () => {
      uploadCurrentXhr = null;
      let data = null;
      try { data = JSON.parse(xhr.responseText); } catch (_) { }
      if (xhr.status === 200 && data) resolve({ data });
      else resolve({ error: (data && data.error) || ('HTTP ' + xhr.status) });
    };
    xhr.onerror = () => { uploadCurrentXhr = null; resolve({ error: '网络错误' }); };
    xhr.onabort = () => { uploadCurrentXhr = null; resolve({ aborted: true, error: '已取消' }); };

    const fd = new FormData();
    fd.append('files', item.file);
    xhr.send(fd);
  });
}

function setConflictAction(idx, action) {
  const item = uploadItems[idx];
  if (!item || item.status !== 'conflict') return;
  item.action = action;
  renderUploadRow(item);
  updateUploadSummary();
  updateUploadFooter();
}

function setAllConflictAction(action) {
  for (const item of uploadItems) {
    if (item.status === 'conflict') {
      item.action = action;
      renderUploadRow(item);
    }
  }
  updateUploadSummary();
  updateUploadFooter();
}

async function confirmUpload() {
  if (uploadPhase !== 'conflict') return;
  await runUploadCommit();
}

async function runUploadCommit() {
  uploadPhase = 'committing';
  updateUploadFooter();

  for (const item of uploadItems) {
    if (!uploadRunning) return;
    if (item.status !== 'new' && item.status !== 'conflict') continue;
    if (!item.tempId) {
      item.status = 'error';
      item.error = '临时文件缺失';
      renderUploadRow(item);
      continue;
    }
    item.status = 'committing';
    renderUploadRow(item);

    try {
      const body = {
        path: filePath,
        resolve: { [item.tempId]: item.action },
        overwrite_ids: {},
      };
      if (item.action === 'overwrite' && item.existing) {
        body.overwrite_ids[item.tempId] = item.existing.id;
      }
      const result = await POST('/files/upload-commit', body);
      if (result.errors && result.errors.length > 0) {
        item.status = 'error';
        item.error = result.errors[0].error || '写入失败';
      } else if (item.action === 'skip') {
        item.status = 'skipped';
      } else {
        item.status = 'done';
      }
    } catch (e) {
      item.status = 'error';
      item.error = e.message;
    }
    renderUploadRow(item);
    updateUploadSummary();
  }

  uploadRunning = false;
  uploadPhase = 'done';
  updateUploadSummary();
  updateUploadFooter();

  const doneCount = uploadItems.filter(i => i.status === 'done').length;
  const failCount = uploadItems.filter(i => i.status === 'error').length;
  if (doneCount > 0) fetchFiles();
  if (failCount === 0) {
    showToast(doneCount > 0 ? `已上传 ${doneCount} 个文件` : '上传结束', 'success');
  } else {
    showToast(`上传结束：成功 ${doneCount}，失败 ${failCount}`, 'warn');
  }
}

async function cancelUpload() {
  uploadRunning = false;
  if (uploadCurrentXhr) {
    try { uploadCurrentXhr.abort(); } catch (_) { }
    uploadCurrentXhr = null;
  }
  const tempIds = uploadItems.map(i => i.tempId).filter(Boolean);
  if (tempIds.length > 0) {
    try { await POST('/files/upload-cancel', { temp_ids: tempIds }); } catch (_) { }
  }
  closeUploadModal();
  showToast('已取消上传', 'info');
}

function closeUploadModal() {
  uploadRunning = false;
  uploadItems = [];
  uploadPhase = 'idle';
  uploadPanelVisible = false;
  clearTimeout(uploadIndicatorTimer);
  closeModal('upload-progress-modal');
  updateUploadIndicator();
}
