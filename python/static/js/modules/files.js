/**
 * files.js — 目录浏览模块
 * 包含：目录加载、渲染、上级目录导航、排序切换。
 * 依赖：api.js、state.js（fileSort / filePath / TOKEN）、utils.js、detail.js（showFileMeta）
 */

/* ═══════════════════════════════════════════════════════════
   LOAD & RENDER
═══════════════════════════════════════════════════════════ */

/**
 * 加载并渲染指定路径下的文件列表
 * @param {string} path
 * @param {boolean} forceRefresh - 是否强制刷新，忽略缓存
 */
async function loadFiles(path, forceRefresh = false) {
  filePath = path || '';
  document.getElementById('file-path-text').textContent = '/' + filePath;

  const cacheKey = filePath;
  const now = Date.now();
  const cached = filesCache[cacheKey];

  if (!forceRefresh && cached && (now - cached.timestamp) < FILES_CACHE_TTL) {
    currentFiles = cached.items;
    filterFiles(document.getElementById('file-search').value);
    return;
  }

  document.getElementById('file-list').innerHTML = '<div class="loading-row">加载中...</div>';

  try {
    const data = await GET(`/files?path=${encodeURIComponent(filePath)}`);
    currentFiles = data.items;
    filesCache[cacheKey] = { items: currentFiles, timestamp: now };
    filterFiles(document.getElementById('file-search').value);
  } catch (e) {
    document.getElementById('file-list').innerHTML =
      `<div class="loading-row" style="color:var(--red)">加载失败：${esc(e.message)}</div>`;
  }
}

/**
 * 将文件数组渲染为文件列表 HTML
 * @param {Array} items
 * @param {string} basePath — 当前目录路径（暂未使用，保留备用）
 */
function renderFiles(items, basePath) {
  document.getElementById('file-list').innerHTML = items.map(f => `
    <div class="file-row"
         onclick="${f.is_dir ? `loadFiles('${escJs(f.path)}')` : `showFileMeta('${escJs(f.path)}')`}">
      <div class="fr fr-icon">
        ${f.is_dir
      ? '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>'
      : '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/></svg>'
    }
      </div>
      <div class="fr fr-name">${esc(f.name)}</div>
      <div class="fr fr-dir">${esc('/' + f.path)}</div>
      <div class="fr fr-type ${f.ext === 'flac' ? 'fmt-flac' : f.ext === 'mp3' ? 'fmt-mp3' : ''}">
        ${f.is_dir ? 'DIR' : f.ext.toUpperCase()}
      </div>
      <div class="fr fr-size">${f.is_dir ? '—' : fmtSize(f.size)}</div>
      <div class="fr fr-date">${f.mtime}</div>
    </div>
  `).join('') || '<div class="loading-row">此目录为空</div>';
}

/**
 * 根据搜索关键词过滤文件列表
 * @param {string} query
 */
function filterFiles(query) {
  let items = currentFiles;

  if (query && query.trim()) {
    const lowerQuery = query.toLowerCase();
    items = currentFiles.filter(f =>
      f.name.toLowerCase().includes(lowerQuery)
    );
  }

  if (fileSort === 'date') {
    items = [...items].sort((a, b) => b.mtime.localeCompare(a.mtime));
  }

  renderFiles(items, filePath);
}

/* ═══════════════════════════════════════════════════════════
   NAVIGATION
═══════════════════════════════════════════════════════════ */

/** 返回上级目录 */
function filesGoUp() {
  const parts = filePath.split('/').filter(Boolean);
  parts.pop();
  loadFiles(parts.join('/'));
}

/* ═══════════════════════════════════════════════════════════
   SORT
═══════════════════════════════════════════════════════════ */

/**
 * 切换文件列表排序方式
 * @param {'name'|'date'} mode
 */
function setFileSort(mode) {
  fileSort = mode;
  document.querySelectorAll('.sort-chip').forEach(c => c.classList.remove('active'));
  document.getElementById('sort-' + mode).classList.add('active');
  filterFiles(document.getElementById('file-search').value);
}
