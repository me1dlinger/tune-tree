/**
 * pending.js — 待定文件模块
 * 依赖：api.js、utils.js、metadata-edit.js、batch-scrape.js
 */

/** 是否处于多选模式 */
let pendingSelectMode = false;
/** 已选中的待定文件 track id 集合 */
let pendingSelectedIds = new Set();
/** 当前待定文件列表数据 */
let pendingFiles = [];
/** 批量搜索上限（与 batch-scrape.js 的 BATCH_SELECT_LIMIT 保持一致） */
const PENDING_SELECT_LIMIT = 20;

/** 同步“待定文件”导航角标（桌面侧栏 + 移动端视图下拉） */
function updatePendingDot(hasPending) {
  const dot = document.getElementById('pending-dot');
  if (dot) dot.style.display = hasPending ? 'inline-block' : 'none';
  const vsDot = document.getElementById('view-switch-pending-dot');
  if (vsDot) vsDot.style.display = hasPending ? 'inline-block' : 'none';
}

/** 加载并渲染待定文件页 */
async function loadPending() {
  try {
    const files = await GET('/pending');
    pendingFiles = files;

    // 根据实际待定数量同步角标，避免处理后角标残留
    updatePendingDot(files.length > 0);

    // 清理已不在列表中的选中项
    const idSet = new Set(files.map(f => f.id));
    for (const id of Array.from(pendingSelectedIds)) {
      if (!idSet.has(id)) pendingSelectedIds.delete(id);
    }
    if (files.length === 0) {
      pendingSelectMode = false;
      pendingSelectedIds.clear();
    }

    renderPending();
  } catch (e) {
    document.getElementById('pending-view').innerHTML =
      `<div class="loading-row" style="color:var(--red)">加载失败</div>`;
  }
}

/** 手动刷新待定文件列表 */
async function refreshPending() {
  const btn = document.getElementById('pending-refresh-btn');
  if (btn) {
    btn.disabled = true;
    const icon = btn.querySelector('i');
    if (icon) icon.classList.add('spin');
  }
  await loadPending();
}

/** 渲染待定文件列表（使用已缓存的 pendingFiles，不发起请求） */
function renderPending() {
  const pv = document.getElementById('pending-view');
  if (!pv) return;

  const files = pendingFiles || [];

  // 分离刮削失败和普通待定文件
  const scrapeFailedFiles = files.filter(f => f.scrape_failed);
  const normalPendingFiles = files.filter(f => !f.scrape_failed);

  const headerActions = `
    <div class="pending-header-actions">
      ${files.length > 0 ? `
        <span class="select-count" id="pending-select-count" style="display:none;"></span>
        <button class="toolbar-btn" id="pending-batch-scrape-btn" onclick="startPendingBatchScrape()" style="display:none;">
          <i class="bi bi-search"></i>
          批量搜索标签
        </button>
        <button class="toolbar-btn${pendingSelectMode ? ' active' : ''}" id="pending-select-toggle" onclick="togglePendingSelectMode()">
          <i class="bi bi-check2-square"></i>
          <span id="pending-select-text">${pendingSelectMode ? '退出多选' : '多选'}</span>
        </button>
      ` : ''}
      <button class="toolbar-btn" id="pending-refresh-btn" onclick="refreshPending()" title="刷新待定文件列表">
        <i class="bi bi-arrow-clockwise"></i>
        刷新
      </button>
    </div>
  `;

  const rowCheckbox = (f) => {
    if (!pendingSelectMode) return '<div class="pending-check"></div>';
    const selected = pendingSelectedIds.has(f.id);
    return `<div class="pending-check"><i class="bi ${selected ? 'bi-check-circle-fill selected' : 'bi-circle'}"></i></div>`;
  };

  const failedRows = scrapeFailedFiles.map(f => `
    <div class="pending-row pending-row-failed${pendingSelectedIds.has(f.id) ? ' selected' : ''}" data-track-id="${f.id}"
         style="display:grid;grid-template-columns:28px 1fr 160px 80px 100px;align-items:center;height:40px;border-bottom:1px solid var(--border);cursor:pointer;"
         onclick="${pendingSelectMode ? `togglePendingSelect(${f.id})` : `editPendingTrack(${f.id})`}">
      ${rowCheckbox(f)}
      <div style="padding:0 10px;font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">
        ${esc(f.filename)}
      </div>
      <div style="padding:0 10px;font-family:var(--font-mono);font-size:10px;color:var(--text3);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">
        ${esc(f.path)}
      </div>
      <div style="padding:0 10px;font-family:var(--font-mono);font-size:10px;color:var(--text3);">
        ${fmtSize(f.size)}
      </div>
      <div style="padding:0 10px;">
        <span class="status-badge status-failed">刮削失败</span>
      </div>
    </div>
  `).join('');

  const normalRows = normalPendingFiles.map(f => `
    <div class="pending-row${pendingSelectedIds.has(f.id) ? ' selected' : ''}" data-track-id="${f.id}"
         style="display:grid;grid-template-columns:28px 1fr 1fr 80px 170px;align-items:center;height:40px;border-bottom:1px solid var(--border);cursor:pointer;"
         onclick="${pendingSelectMode ? `togglePendingSelect(${f.id})` : `editPendingTrack(${f.id})`}">
      ${rowCheckbox(f)}
      <div style="padding:0 10px;font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">
        ${esc(f.filename)}
      </div>
      <div style="padding:0 10px;font-family:var(--font-mono);font-size:10px;color:var(--text3);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">
        ${esc(f.relative_path)}
      </div>
      <div style="padding:0 10px;font-family:var(--font-mono);font-size:10px;color:var(--text3);">
        ${fmtSize(f.size)}
      </div>
      <div style="padding:0 10px;">
        <div class="missing-tags">
          ${(f.missing_tags || '').split(',').filter(Boolean).map(m =>
            `<span class="missing-tag">${esc(m)}</span>`
          ).join('')}
        </div>
      </div>
    </div>
  `).join('');

  pv.innerHTML = `
    <div class="pending-header">
      <div class="pending-title">待定文件</div>
      <div class="pending-badge">${files.length} 个文件</div>
      ${headerActions}
    </div>

    ${scrapeFailedFiles.length > 0 ? `
      <div style="margin-bottom:12px;">
        <div style="font-size:12px;color:var(--red);font-weight:500;margin-bottom:4px;">
          ⚠️ 刮削失败 (${scrapeFailedFiles.length})
        </div>
        <div style="font-size:11px;color:var(--text3);">
          这些文件刮削元数据失败，已加入冷却列表，3天内不会再次尝试刮削。点击可编辑元数据。
        </div>
      </div>
      <div style="display:grid;grid-template-columns:28px 1fr 160px 80px 100px;align-items:center;height:28px;border-bottom:1px solid var(--border);background:var(--bg);">
        <div class="th"></div>
        <div class="th">文件名</div>
        <div class="th">路径</div>
        <div class="th">大小</div>
        <div class="th">状态</div>
      </div>
      ${failedRows}
      <div style="height:16px;"></div>
    ` : ''}

    <div style="margin-bottom:12px;font-size:12px;color:var(--text2);">
      ${normalPendingFiles.length > 0 ? `以下文件因元数据不完整无法自动分类，请补充元数据后重新扫描。点击可编辑元数据。` : ''}
    </div>
    ${normalPendingFiles.length === 0
      ? (scrapeFailedFiles.length === 0 ? '<div class="loading-row">暂无待定文件</div>' : '')
      : `
        <div style="display:grid;grid-template-columns:28px 1fr 1fr 80px 170px;align-items:center;height:28px;border-bottom:1px solid var(--border);background:var(--bg);">
          <div class="th"></div>
          <div class="th">文件名</div>
          <div class="th">路径</div>
          <div class="th">大小</div>
          <div class="th">缺失字段</div>
        </div>
        ${normalRows}
      `
    }
  `;

  updatePendingSelectUI();
}

/** 同步多选相关的计数与按钮显隐 */
function updatePendingSelectUI() {
  const count = pendingSelectedIds.size;
  const selectCount = document.getElementById('pending-select-count');
  if (selectCount) {
    if (pendingSelectMode && count > 0) {
      selectCount.style.display = '';
      selectCount.textContent = `已选 ${count}/${PENDING_SELECT_LIMIT}`;
    } else {
      selectCount.style.display = 'none';
    }
  }
  const batchBtn = document.getElementById('pending-batch-scrape-btn');
  if (batchBtn) {
    batchBtn.style.display = pendingSelectMode && count > 0 ? '' : 'none';
  }
}

/** 就地同步行选中态，避免每次勾选都重建整个列表 DOM */
function syncPendingSelectionUI() {
  const pv = document.getElementById('pending-view');
  if (!pv) return;
  pv.querySelectorAll('.pending-row[data-track-id]').forEach(row => {
    const id = parseInt(row.dataset.trackId, 10);
    const selected = pendingSelectedIds.has(id);
    row.classList.toggle('selected', selected);
    const icon = row.querySelector('.pending-check i');
    if (icon) {
      icon.className = `bi ${selected ? 'bi-check-circle-fill selected' : 'bi-circle'}`;
    }
  });
  updatePendingSelectUI();
}

/** 切换多选模式 */
function togglePendingSelectMode() {
  pendingSelectMode = !pendingSelectMode;
  if (!pendingSelectMode) pendingSelectedIds.clear();
  renderPending();
}

/** 勾选 / 取消勾选一个待定文件 */
function togglePendingSelect(id) {
  if (pendingSelectedIds.has(id)) {
    pendingSelectedIds.delete(id);
  } else {
    if (pendingSelectedIds.size >= PENDING_SELECT_LIMIT) {
      showToast(`最多选择 ${PENDING_SELECT_LIMIT} 条`, 'warn');
      return;
    }
    pendingSelectedIds.add(id);
  }
  syncPendingSelectionUI();
}

/** 对选中的待定文件批量搜索标签 */
function startPendingBatchScrape() {
  if (!pendingSelectMode || pendingSelectedIds.size === 0) {
    showToast('请先选择文件', 'warn');
    return;
  }
  const ids = Array.from(pendingSelectedIds).slice(0, PENDING_SELECT_LIMIT);
  if (typeof runBatchScrape === 'function') {
    runBatchScrape(ids);
  } else {
    showToast('批量搜索模块未加载', 'error');
  }
}

/** 编辑待定文件的元数据 */
async function editPendingTrack(trackId) {
  try {
    const track = await GET(`/tracks/${trackId}`);
    if (track) {
      openMetadataEdit(track);
    }
  } catch (e) {
    showToast('加载歌曲信息失败: ' + e.message, 'error');
  }
}