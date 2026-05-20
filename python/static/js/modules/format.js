/**
 * format.js — 格式化目录模块
 * 包含：预览弹窗、执行格式化。
 * 依赖：api.js、state.js、utils.js、ui.js（openModal / closeModal / showToast）
 * 依赖：artist.js（loadArtistTree / selectArtist / updateToolbar / clearSelectedArtists）
 * 依赖：stats.js（loadStats）、logs.js（loadLogs）
 */

/** 当前格式化预览数据（跨函数共享），支持多艺术家 */
let formatPreviewData = {};

/** 当前激活的预览 tab 索引 */
let currentPreviewTab = 0;

/* ═══════════════════════════════════════════════════════════
   PREVIEW
═══════════════════════════════════════════════════════════ */

/** 打开格式化预览弹窗并生成预览 */
async function openFormatModal() {
  const hasArtistSelection = selectedArtists.size > 0;
  const hasAlbumTrackSelection = selectedAlbums.size > 0 || selectedTracks.size > 0;

  if (!hasArtistSelection && !hasAlbumTrackSelection) return;

  const btn = document.getElementById('format-exec-btn');
  btn.disabled = true;
  btn.textContent = '预览中...';
  openModal('format-modal');
  document.getElementById('format-modal-body').innerHTML = '<div class="loading-row">生成预览中...</div>';

  try {
    formatPreviewData = {};
    currentPreviewTab = 0;

    if (hasArtistSelection) {
      // 多艺术家格式化
      const artists = Array.from(selectedArtists);

      for (const artist of artists) {
        const formatData = await POST('/format/preview', {
          artist: artist,
        });
        formatPreviewData[artist] = formatData;
      }

      renderMultiArtistPreview();
    } else {
      // 单艺术家/专辑/曲目格式化
      let formatData;
      if (selectedAlbums.size > 0) {
        const albumIds = artistAlbums
          .filter(al => selectedAlbums.has(al.album))
          .map(al => al.sample_id);
        formatData = await POST('/format/preview', {
          artist: currentArtist.artist,
          album_ids: albumIds,
        });
      } else {
        formatData = await POST('/format/preview', {
          track_ids: Array.from(selectedTracks),
        });
      }
      formatPreviewData[currentArtist.artist] = formatData;
      renderSingleArtistPreview(formatData, currentArtist.artist);
    }

    btn.disabled = false;
    btn.textContent = '确认执行';
  } catch (e) {
    document.getElementById('format-modal-body').innerHTML =
      `<div class="loading-row" style="color:var(--red)">预览失败：${esc(e.message)}</div>`;
    btn.textContent = '确认执行';
  }
}

/** 渲染多艺术家预览界面（带 tab） */
function renderMultiArtistPreview() {
  const artists = Object.keys(formatPreviewData);
  if (artists.length === 0) return;

  // 计算总数
  let totalFiles = 0, totalConflicts = 0, totalSkipped = 0;
  for (const data of Object.values(formatPreviewData)) {
    totalFiles += data.items.length;
    totalConflicts += data.conflicts || 0;
    totalSkipped += data.skipped || 0;
  }

  document.getElementById('format-modal-info').textContent =
    `共 ${totalFiles} 个文件` +
    (totalConflicts > 0 ? ` · ${totalConflicts} 个冲突` : '') +
    (totalSkipped > 0 ? ` · ${totalSkipped} 个跳过` : '');

  // 构建 tab HTML，每个 tab 显示文件数
  const tabsHtml = artists.map((artist, index) => {
    const data = formatPreviewData[artist];
    return `
      <div class="preview-tab ${index === currentPreviewTab ? 'active' : ''}" 
           onclick="switchPreviewTab(${index})"
           id="preview-tab-${index}">
        ${esc(artist)}
        <span class="preview-tab-count">(${data.items.length} 文件)</span>
        <button class="preview-tab-close" onclick="removePreviewTab(event, ${index})">×</button>
      </div>
    `;
  }).join('');

  // 构建整体统计区域
  const overallStatsHtml = `
    <div class="preview-overall-stats">
      <div class="overall-stat-item">
        <span class="overall-stat-label">总文件数</span>
        <span class="overall-stat-value">${totalFiles}</span>
      </div>
      <div class="overall-stat-item ${totalConflicts > 0 ? 'warn' : ''}">
        <span class="overall-stat-label">冲突</span>
        <span class="overall-stat-value">${totalConflicts}</span>
      </div>
      <div class="overall-stat-item ${totalSkipped > 0 ? 'info' : ''}">
        <span class="overall-stat-label">跳过</span>
        <span class="overall-stat-value">${totalSkipped}</span>
      </div>
      <div class="overall-stat-item">
        <span class="overall-stat-label">艺术家数</span>
        <span class="overall-stat-value">${artists.length}</span>
      </div>
    </div>
  `;

  // 构建当前 tab 内容（始终显示单个艺术家统计）
  const currentData = formatPreviewData[artists[currentPreviewTab]];
  const contentHtml = buildPreviewContent(currentData, artists[currentPreviewTab], false);

  document.getElementById('format-modal-body').innerHTML = `
    ${overallStatsHtml}
    <div class="preview-tabs">${tabsHtml}</div>
    ${contentHtml}
  `;
}

/** 渲染单艺术家预览界面 */
function renderSingleArtistPreview(formatData, artistName) {
  const { items, conflicts, skipped, tree } = formatData;

  document.getElementById('format-modal-info').textContent =
    `共 ${items.length} 个文件` +
    (conflicts > 0 ? ` · ${conflicts} 个冲突` : '') +
    (skipped > 0 ? ` · ${skipped} 个跳过` : '') +
    ` · 艺术家 <strong>${esc(artistName)}</strong>`;

  document.getElementById('format-modal-body').innerHTML = buildPreviewContent(formatData, artistName);
}

/** 构建预览内容 HTML */
function buildPreviewContent(formatData, artistName, hideSingleArtistStats = false) {
  const { items, conflicts, skipped, tree } = formatData;

  let treeHtml = '';
  if (tree) {
    for (const [artName, albums] of Object.entries(tree)) {
      treeHtml += `
        <div class="preview-tree-artist">
          <div class="preview-tree-artist-name">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/>
            </svg>
            ${esc(artName)}
          </div>
      `;
      for (const [albumName, albumData] of Object.entries(albums)) {
        treeHtml += `
          <div class="preview-tree-album">
            <div class="preview-tree-album-name">
              <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>
              </svg>
              ${esc(albumName)}
            </div>
            <div class="preview-tree-tracks">
        `;
        const sortedTracks = [...albumData.tracks].sort(
          (a, b) => (a.track_num || 0) - (b.track_num || 0)
        );
        for (const track of sortedTracks) {
          treeHtml += `
            <div class="preview-tree-track">
              <span class="preview-tree-num">${track.track_num ? String(track.track_num).padStart(2, '0') : '--'}</span>
              <span class="preview-tree-filename">${esc(track.filename)}</span>
            </div>
          `;
        }
        treeHtml += `</div></div>`;
      }
      treeHtml += `</div>`;
    }
  }

  // 根据参数决定是否显示单个艺术家的统计
  const statHtml = hideSingleArtistStats ? '' : `
    <div class="preview-stat">
      <div class="preview-stat-item">文件总数 <strong>${items.length}</strong></div>
      <div class="preview-stat-item ${conflicts ? 'warn' : ''}">冲突 <strong>${conflicts}</strong></div>
      <div class="preview-stat-item ${skipped ? 'info' : ''}">跳过 <strong>${skipped}</strong></div>
      <div class="preview-stat-item">艺术家 <strong>${esc(artistName)}</strong></div>
    </div>
  `;

  return `
    ${statHtml}
    ${treeHtml ? `<div class="preview-tree">${treeHtml}</div>` : ''}
    <table class="preview-table">
      <thead><tr><th>原文件名</th><th>新文件名</th><th>状态</th></tr></thead>
      <tbody>
        ${items.map(item => `
          <tr>
            <td class="path-old">${esc(item.old_name)}</td>
            <td class="${getStatusClass(item.status)}">${esc(item.new_name)}</td>
            <td>${getStatusTag(item.status)}</td>
          </tr>
        `).join('')}
      </tbody>
    </table>
  `;
}

/** 切换预览 tab */
function switchPreviewTab(index) {
  const artists = Object.keys(formatPreviewData);
  if (index < 0 || index >= artists.length) return;

  currentPreviewTab = index;

  // 更新 tab 样式
  document.querySelectorAll('.preview-tab').forEach((tab, i) => {
    tab.classList.toggle('active', i === index);
  });

  // 更新内容
  const currentData = formatPreviewData[artists[index]];
  const contentHtml = buildPreviewContent(currentData, artists[index], true);

  // 替换内容区域（保留整体统计和 tabs）
  const body = document.getElementById('format-modal-body');
  const overallStats = body.querySelector('.preview-overall-stats')?.outerHTML || '';

  body.innerHTML = `
    ${overallStats}
    <div class="preview-tabs">${body.querySelector('.preview-tabs').innerHTML}</div>
    ${buildPreviewContent(currentData, artists[index], false)}
  `;
}

/** 移除预览 tab */
function removePreviewTab(event, index) {
  event.stopPropagation();

  const artists = Object.keys(formatPreviewData);
  const artistToRemove = artists[index];

  delete formatPreviewData[artistToRemove];

  // 从选中集合中移除
  selectedArtists.delete(artistToRemove);

  const newArtists = Object.keys(formatPreviewData);
  if (newArtists.length === 0) {
    closeModal('format-modal');
    showToast('已移除所有艺术家', 'info');
    return;
  }

  // 调整当前 tab 索引
  if (currentPreviewTab >= newArtists.length) {
    currentPreviewTab = newArtists.length - 1;
  }

  renderMultiArtistPreview();
}

/** 获取状态样式类名 */
function getStatusClass(status) {
  switch (status) {
    case 'skip': return 'path-skipped';
    case 'conflict': return 'path-conflict';
    default: return 'path-new';
  }
}

/** 获取状态标签 HTML */
function getStatusTag(status) {
  const tags = {
    'skip': '<span class="tag" style="color:var(--gray)">跳过</span>',
    'conflict': '<span class="tag" style="color:var(--red)">冲突</span>',
    'normal': '<span class="tag" style="color:var(--accent)">正常</span>'
  };
  return tags[status] || tags['normal'];
}

/* ═══════════════════════════════════════════════════════════
   EXECUTE
═══════════════════════════════════════════════════════════ */

/** 执行格式化操作 */
async function executeFormat() {
  const hasArtistSelection = selectedArtists.size > 0;
  const hasAlbumTrackSelection = selectedAlbums.size > 0 || selectedTracks.size > 0;

  if (!hasArtistSelection && !hasAlbumTrackSelection) return;

  const btn = document.getElementById('format-exec-btn');
  btn.disabled = true;
  btn.textContent = '执行中...';

  try {
    let totalMoved = 0, totalSkipped = 0, totalErrors = 0;

    if (hasArtistSelection) {
      // 多艺术家格式化
      const artists = Object.keys(formatPreviewData);

      for (const artist of artists) {
        const result = await POST('/format/execute', {
          artist: artist,
        });
        totalMoved += result.moved || 0;
        totalSkipped += result.skipped || 0;
        totalErrors += result.errors || 0;
      }
    } else {
      // 单艺术家/专辑/曲目格式化
      let result;
      if (selectedAlbums.size > 0) {
        const albumIds = artistAlbums
          .filter(al => selectedAlbums.has(al.album))
          .map(al => al.sample_id);
        result = await POST('/format/execute', {
          artist: currentArtist.artist,
          album_ids: albumIds,
        });
      } else {
        result = await POST('/format/execute', {
          track_ids: Array.from(selectedTracks),
        });
      }
      totalMoved = result.moved || 0;
      totalSkipped = result.skipped || 0;
      totalErrors = result.errors || 0;
    }

    closeModal('format-modal');
    showToast(
      `格式化完成：移动 ${totalMoved} 个文件${totalSkipped ? '，跳过 ' + totalSkipped + ' 个' : ''}${totalErrors ? '，' + totalErrors + ' 个失败' : ''}`,
      totalErrors ? 'warn' : 'success'
    );

    selectedArtists.clear();
    selectedAlbums.clear();
    selectedTracks.clear();
    clearSelectedArtists();

    await loadArtistTree();
    if (currentArtist) {
      await selectArtist(currentArtist.artist, null);
    }
    loadStats();
    loadLogs();
  } catch (e) {
    showToast('格式化失败: ' + e.message, 'error');
  } finally {
    btn.disabled = false;
    btn.textContent = '确认执行';
  }
}
