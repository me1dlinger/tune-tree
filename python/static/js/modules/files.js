/**
 * files.js — 目录浏览模块
 * 包含：目录加载、渲染、上级目录导航、排序切换、分页。
 * 依赖：api.js、state.js（fileSort / filePath / TOKEN）、utils.js、detail.js（showFileMeta）
 */

const PAGE_SIZE = 200;
let currentOffset = 0;
let totalItems = 0;
let currentSearch = '';

/* ═══════════════════════════════════════════════════════════
   LOAD & RENDER
═══════════════════════════════════════════════════════════ */

async function loadFiles(path, forceRefresh = false) {
  filePath = path || '';
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

  const html = items.map(f => `
    <div class="file-row"
         onclick="${f.is_dir ? `loadFiles('${escJs(f.path)}')` : (f.is_audio ? `showFileMeta('${escJs(f.path)}')` : '')}"
         style="${f.is_dir || f.is_audio ? 'cursor:pointer' : ''}">
      <div class="fr fr-icon">
        ${f.is_dir
      ? '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>'
      : f.is_audio
      ? '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/></svg>'
      : '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>'
    }
      </div>
      <div class="fr fr-name">${esc(f.name)}</div>
      <div class="fr fr-dir">${esc('/' + f.path)}</div>
      <div class="fr fr-type ${f.ext === 'flac' ? 'fmt-flac' : f.ext === 'mp3' ? 'fmt-mp3' : ''}">
        ${f.is_dir ? 'DIR' : f.ext.toUpperCase()}
      </div>
      <div class="fr fr-size">${f.is_dir ? '—' : fmtSize(f.size)}</div>
      <div class="fr fr-date">${f.mtime}</div>
      <div class="fr fr-download">
        ${f.is_dir ? '' : `
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"
             onclick="event.stopPropagation();downloadFileOrDir('${escJs(f.path)}', '${escJs(f.name)}', false)">
          <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
          <polyline points="7 10 12 15 17 10"/>
          <line x1="12" y1="15" x2="12" y2="3"/>
        </svg>
        `}
      </div>
    </div>
  `).join('');

  document.getElementById('file-list').innerHTML = html;
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
  const params = new URLSearchParams({ path: filePath });
  const url = `/api/files/download?${params.toString()}&token=${TOKEN}`;
  try {
    const res = await fetch(url);
    if (!res.ok) throw new Error(res.statusText);
    const blob = await res.blob();
    const blobUrl = URL.createObjectURL(blob);
    downloadFile(blobUrl, isDir ? fileName + '.zip' : fileName);
    URL.revokeObjectURL(blobUrl);
  } catch (e) {
    showToast(`下载失败: ${e.message}`, 'error');
  }
}