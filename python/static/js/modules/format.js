/**
 * format.js — 格式化目录模块
 * 包含：预览弹窗、执行格式化。
 * 依赖：api.js、state.js、utils.js、ui.js（openModal / closeModal / showToast）
 * 依赖：artist.js（loadArtistTree / selectArtist / updateToolbar）
 * 依赖：stats.js（loadStats）、logs.js（loadLogs）
 */

/** 当前格式化预览数据（跨函数共享） */
let formatPreviewData = null;

/* ═══════════════════════════════════════════════════════════
   PREVIEW
═══════════════════════════════════════════════════════════ */

/** 打开格式化预览弹窗并生成预览 */
async function openFormatModal() {
  if (!currentArtist || (selectedAlbums.size === 0 && selectedTracks.size === 0)) return;

  const btn = document.getElementById('format-exec-btn');
  btn.disabled = true;
  btn.textContent = '预览中...';
  openModal('format-modal');
  document.getElementById('format-modal-body').innerHTML = '<div class="loading-row">生成预览中...</div>';

  try {
    let formatData;
    if (selectedAlbums.size > 0) {
      // 按专辑格式化
      const albumIds = artistAlbums
        .filter(al => selectedAlbums.has(al.album))
        .map(al => al.sample_id);
      formatData = await POST('/format/preview', {
        artist: currentArtist.artist,
        album_ids: albumIds,
      });
    } else {
      // 按曲目格式化
      formatData = await POST('/format/preview', {
        track_ids: Array.from(selectedTracks),
      });
    }

    formatPreviewData = formatData;
    const { items, conflicts, skipped, tree } = formatPreviewData;

    document.getElementById('format-modal-info').textContent =
      `共 ${items.length} 个文件` +
      (conflicts > 0 ? ` · ${conflicts} 个冲突` : '') +
      (skipped > 0 ? ` · ${skipped} 个跳过` : '');

    // 构建目录树 HTML
    let treeHtml = '';
    if (tree) {
      for (const [artistName, albums] of Object.entries(tree)) {
        treeHtml += `
          <div class="preview-tree-artist">
            <div class="preview-tree-artist-name">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/>
              </svg>
              ${esc(artistName)}
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

    document.getElementById('format-modal-body').innerHTML = `
      <div class="preview-stat">
        <div class="preview-stat-item">文件总数 <strong>${items.length}</strong></div>
        <div class="preview-stat-item ${conflicts ? 'warn' : ''}">冲突 <strong>${conflicts}</strong></div>
        <div class="preview-stat-item ${skipped ? 'info' : ''}">跳过 <strong>${skipped}</strong></div>
        <div class="preview-stat-item">艺术家 <strong>${esc(currentArtist.artist)}</strong></div>
      </div>
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

    btn.disabled = false;
    btn.textContent = '确认执行';
  } catch (e) {
    document.getElementById('format-modal-body').innerHTML =
      `<div class="loading-row" style="color:var(--red)">预览失败：${esc(e.message)}</div>`;
    btn.textContent = '确认执行';
  }
}

/** 获取状态样式类名 */
function getStatusClass(status) {
  switch(status) {
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
  if (!currentArtist || (selectedAlbums.size === 0 && selectedTracks.size === 0)) return;

  const btn = document.getElementById('format-exec-btn');
  btn.disabled = true;
  btn.textContent = '执行中...';

  try {
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

    closeModal('format-modal');
    showToast(
      `格式化完成：移动 ${result.moved} 个文件${result.skipped ? '，跳过 ' + result.skipped + ' 个' : ''}${result.errors ? '，' + result.errors + ' 个失败' : ''}`,
      result.errors ? 'warn' : 'success'
    );

    selectedAlbums.clear();
    selectedTracks.clear();

    await loadArtistTree();
    await selectArtist(currentArtist.artist, null);
    loadStats();
    loadLogs();
  } catch (e) {
    showToast('格式化失败: ' + e.message, 'error');
  } finally {
    btn.disabled = false;
    btn.textContent = '确认执行';
  }
}
