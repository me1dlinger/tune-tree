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
      <div class="fr fr-type ${f.ext === 'flac' ? 'fmt-flac' : f.ext === 'mp3' ? 'fmt-mp3' : ''}">
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
  renderFiles(currentFiles);
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
  renderFiles(currentFiles);
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
