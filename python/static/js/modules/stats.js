/**
 * stats.js — 统计概览模块 + 重复文件弹窗
 * 依赖：api.js、utils.js、ui.js（openModal / hideDetailPanel / showToast / switchPage）
 * 依赖：detail.js（showDuplicateTrackDetail）
 */

/* ═══════════════════════════════════════════════════════════
   STATS
═══════════════════════════════════════════════════════════ */

function formatElapsedTime(seconds) {
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const secs = seconds % 60;
  let str = '';
  if (hours > 0) str += `${hours}小时`;
  if (minutes > 0 || hours > 0) str += `${minutes}分钟`;
  str += `${secs}秒`;
  return str;
}

/** 加载并渲染统计概览页 */
async function loadStats() {
  try {
    const s = await GET('/stats');
    const artistOrg = s.total_artists > 0 ? Math.round(s.org_artists / s.total_artists * 100) : 0;
    const albumOrg = s.total_albums > 0 ? Math.round(s.org_albums / s.total_albums * 100) : 0;

    const scanInfo = s.scan_info || {};
    let scanStatusText = '';
    let scanStatusClass = '';

    if (scanInfo.scanning) {
      const elapsed = formatElapsedTime(scanInfo.scan_elapsed_seconds || 0);
      if (scanInfo.scan_timed_out) {
        scanStatusText = `扫描超时（已运行 ${elapsed}），请刷新页面后重新扫描`;
        scanStatusClass = 'scan-timed-out';
      } else {
        scanStatusText = `正在扫描中（已运行 ${elapsed}）`;
        scanStatusClass = 'scan-running';
      }
    }

    document.getElementById('stats-view').innerHTML = `
      <div style="margin-bottom:20px;padding-bottom:16px;border-bottom:1px solid var(--border);">
        <div style="font-family:var(--font-display);font-size:20px;font-weight:700;margin-bottom:4px;">统计概览</div>
        <div style="font-family:var(--font-mono);font-size:11px;color:var(--text3);">上次扫描：${esc(s.last_scan)}</div>
        ${scanStatusText ? `<div style="font-size:12px;margin-top:4px;color:${scanInfo.scan_timed_out ? 'var(--red)' : 'var(--amber)'};" class="${scanStatusClass}">${scanStatusText}</div>` : ''}
      </div>
      <div class="stats-grid">
        <div class="stat-card" onclick="switchPage('artist');document.getElementById('nav-artist').classList.add('active')">
          <div class="stat-label">曲目总数</div>
          <div class="stat-value accent">${s.total_tracks}</div>
          <div class="stat-sub">FLAC ${s.flac_count} · MP3 ${s.mp3_count}</div>
          <div class="format-bar">
            <div class="format-fill flac-fill" style="width:${s.total_tracks > 0 ? Math.round(s.flac_count / s.total_tracks * 100) : 0}%"></div>
            <div class="format-fill mp3-fill" style="width:${s.total_tracks > 0 ? Math.round(s.mp3_count / s.total_tracks * 100) : 0}%"></div>
          </div>
        </div>
        <div class="stat-card">
          <div class="stat-label">艺术家</div>
          <div class="stat-value">${s.total_artists}</div>
          <div class="stat-sub">已整理 ${s.org_artists} · 未整理 ${s.total_artists - s.org_artists}</div>
          <div class="progress-bar"><div class="progress-fill" style="width:${artistOrg}%"></div></div>
        </div>
        <div class="stat-card">
          <div class="stat-label">专辑</div>
          <div class="stat-value">${s.total_albums}</div>
          <div class="stat-sub">已整理 ${s.org_albums} · 未整理 ${s.total_albums - s.org_albums}</div>
          <div class="progress-bar"><div class="progress-fill" style="width:${albumOrg}%"></div></div>
        </div>
        <div class="stat-card" onclick="switchPage('pending');document.getElementById('nav-pending').classList.add('active')">
          <div class="stat-label">待定文件</div>
          <div class="stat-value amber">${s.pending_count}</div>
          <div class="stat-sub">元数据不完整 · 点击查看</div>
        </div>
        <div class="stat-card" onclick="openDuplicateModal()">
          <div class="stat-label">重复文件</div>
          <div class="stat-value red">${s.duplicates}</div>
          <div class="stat-sub">同名同艺术家 · 跨路径检测 · 点击查看</div>
        </div>
        <div class="stat-card" onclick="switchPage('files');document.getElementById('nav-files').classList.add('active')">
          <div class="stat-label">目录浏览</div>
          <div class="stat-value" style="font-size:28px;">Files</div>
          <div class="stat-sub">点击进入文件管理器</div>
        </div>
      </div>
    `;

    if (s.pending_count > 0) {
      document.getElementById('pending-dot').style.display = 'inline-block';
    }
  } catch (e) {
    document.getElementById('stats-view').innerHTML =
      `<div class="loading-row" style="color:var(--red)">加载失败: ${esc(e.message)}</div>`;
  }
}

/* ═══════════════════════════════════════════════════════════
   DUPLICATES MODAL
═══════════════════════════════════════════════════════════ */

let currentDuplicates = [];
let deletePreviewTracks = [];

function addDeleteDuplicatesButton(body) {
  const footer = document.getElementById('duplicate-modal-footer');
  if (footer.querySelector('.toolbar-btn.danger')) return;
  const btn = document.createElement('button');
  btn.className = 'toolbar-btn danger';
  btn.textContent = '一键删除重复文件';
  btn.style.marginLeft = '8px';
  btn.onclick = showDeletePreview;
  footer.appendChild(btn);
}

function calculateDeleteList(groups) {
  const toDelete = [];
  for (const [key, tracks] of Object.entries(groups)) {
    if (tracks.length < 2) continue;

    const sorted = [...tracks].sort((a, b) => {
      const aOrg = a.organized || 0;
      const bOrg = b.organized || 0;

      if (aOrg !== bOrg) {
        return aOrg - bOrg;
      }

      const aSize = a.size || 0;
      const bSize = b.size || 0;
      return bSize - aSize;
    });

    const maxSize = sorted[0].size || 0;
    const sizeRejects = sorted.filter(t => (t.size || 0) < maxSize);

    if (sizeRejects.length > 0) {
      for (const t of sizeRejects) {
        toDelete.push(t);
      }
    } else {
      const sortedByMtime = [...sorted].sort((a, b) => (a.mtime || 0) - (b.mtime || 0));
      for (let i = 0; i < sortedByMtime.length - 1; i++) {
        toDelete.push(sortedByMtime[i]);
      }
    }
  }
  return toDelete;
}

function showDeletePreview() {
  const groups = {};
  for (const track of currentDuplicates) {
    const key = (track.artist || 'Unknown') + '|' +
      (track.album || 'Unknown') + '|' +
      (track.title || 'Unknown');
    if (!groups[key]) groups[key] = [];
    groups[key].push(track);
  }

  deletePreviewTracks = calculateDeleteList(groups);
  renderDeletePreview();
  openModal('delete-preview-modal');
}

function renderDeletePreview() {
  const body = document.getElementById('delete-preview-body');
  const info = document.getElementById('delete-preview-info');

  if (deletePreviewTracks.length === 0) {
    body.innerHTML = '<div class="loading-row">没有需要删除的文件</div>';
    info.textContent = '';
    return;
  }

  let totalSize = 0;
  for (const t of deletePreviewTracks) {
    totalSize += t.size || 0;
  }

  info.textContent = `将删除 ${deletePreviewTracks.length} 个文件，共 ${fmtSize(totalSize)}`;

  body.innerHTML = `
    <div style="margin-bottom:16px;padding-bottom:12px;border-bottom:1px solid var(--border);">
        <div style="font-size:13px;color:var(--text2);">
          删除策略：<span style="color:var(--accent)">优先删除未格式化文件</span>，再<span style="color:var(--accent)">按文件大小保留最大</span>，相同大小则<span style="color:var(--accent)">按时间保留最新</span>
        </div>
        <div style="font-size:11px;color:var(--text3);margin-top:4px;">点击移除按钮可将该文件从删除列表中移除</div>
      </div>
    <div style="max-height:400px;overflow-y:auto;">
      ${deletePreviewTracks.map(t => {
    const fmt = (t.ext || '').replace('.', '').toUpperCase();
    const size = t.size ? fmtSize(t.size) : '—';
    const mtime = t.mtime ? new Date(t.mtime * 1000).toLocaleString() : '—';
    return `
          <div style="display:flex;align-items:center;gap:12px;padding:10px 12px;border-radius:var(--radius);border-bottom:1px solid var(--border);transition:background var(--transition);"
               onmouseover="this.style.background='var(--bg3)'" onmouseout="this.style.background=''">
            <button class="toolbar-btn" style="padding:4px 8px;font-size:11px;background:var(--red-dim);color:var(--red);border-color:var(--red);"
                    onclick="removeFromDeletePreview(${t.id})">移除</button>
            <div style="flex:1;min-width:0;">
              <div style="font-size:13px;color:var(--text);font-weight:500;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${esc(t.filename)}</div>
              <div style="font-size:11px;color:var(--text3);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${esc(t.path)}</div>
            </div>
            <div style="display:flex;align-items:center;gap:6px;">
              <span style="padding:3px 8px;border-radius:var(--radius);font-size:11px;font-weight:500;${fmt === 'FLAC' ? 'background:var(--blue-dim);color:var(--blue);' :
        fmt === 'MP3' ? 'background:var(--amber-dim);color:var(--amber);' :
          'background:var(--bg4);color:var(--text2);'
      }">${fmt}</span>
            </div>
            <div style="font-size:12px;color:var(--text2);font-family:var(--font-mono);min-width:70px;text-align:right;">${size}</div>
            <div style="font-size:11px;color:var(--text3);min-width:140px;">${mtime}</div>
          </div>
        `;
  }).join('')}
    </div>
  `;
}

function removeFromDeletePreview(trackId) {
  deletePreviewTracks = deletePreviewTracks.filter(t => t.id !== trackId);
  renderDeletePreview();
}

async function executeDeleteDuplicates() {
  if (deletePreviewTracks.length === 0) {
    showToast('没有文件需要删除', 'error');
    return;
  }

  const btn = document.getElementById('delete-exec-btn');
  btn.disabled = true;
  btn.textContent = '删除中...';

  try {
    const trackIds = deletePreviewTracks.map(t => t.id);
    await POST('/tracks/batch-delete', { track_ids: trackIds });
    showToast(`成功删除 ${trackIds.length} 个文件`);
    closeModal('delete-preview-modal');
    closeModal('duplicate-modal');
    deletePreviewTracks = [];
    currentDuplicates = [];
    loadStats();
  } catch (e) {
    showToast('删除失败: ' + e.message, 'error');
    btn.disabled = false;
    btn.textContent = '确认删除';
  }
}

/** 打开重复文件弹窗并加载数据 */
async function openDuplicateModal() {
  openModal('duplicate-modal');
  hideDetailPanel();
  const body = document.getElementById('duplicate-modal-body');
  body.innerHTML = '<div class="loading-row">加载中...</div>';

  try {
    const duplicates = await GET('/duplicates');
    currentDuplicates = duplicates;

    if (duplicates.length === 0) {
      body.innerHTML = '<div class="loading-row">暂无重复文件</div>';
      return;
    }

    // 按（艺术家、专辑、标题）分组
    const groups = {};
    for (const track of duplicates) {
      const key = (track.artist || 'Unknown') + '|' +
        (track.album || 'Unknown') + '|' +
        (track.title || 'Unknown');
      if (!groups[key]) groups[key] = [];
      groups[key].push(track);
    }

    let html = `
      <div style="margin-bottom:16px;padding-bottom:12px;border-bottom:1px solid var(--border);">
        <div style="font-size:14px;font-weight:500;color:var(--text1);">
          共 <span style="color:var(--red)">${duplicates.length}</span> 个重复文件，
          <span style="color:var(--accent)">${Object.keys(groups).length}</span> 组重复
        </div>
        <div style="font-size:11px;color:var(--text3);margin-top:4px;">重复判定：艺术家、专辑、标题三者都相同</div>
      </div>
    `;

    for (const [key, tracks] of Object.entries(groups)) {
      const [artist, album, title] = key.split('|');
      const dur = tracks[0].duration ? fmtDur(tracks[0].duration) : '—';

      html += `
        <div style="background:var(--bg3);border-radius:var(--radius2);overflow:hidden;margin-bottom:16px;">
          <div style="padding:14px 16px;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:12px;">
            <div style="width:40px;height:40px;border-radius:var(--radius);background:var(--bg4);display:flex;align-items:center;justify-content:center;color:var(--text3);flex-shrink:0;">
              <i class="bi bi-music-note" style="font-size: 20px;"></i>
            </div>
            <div style="flex:1;min-width:0;">
              <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px;">
                <span style="font-size:14px;font-weight:600;color:var(--text);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${esc(title)}</span>
                <span style="padding:2px 8px;border-radius:12px;background:var(--accent-dim);color:var(--accent);font-size:11px;font-weight:500;">${tracks.length} 副本</span>
              </div>
              <div style="font-size:12px;color:var(--text2);margin-bottom:2px;">${esc(artist) || 'Unknown Artist'}</div>
              <div style="font-size:11px;color:var(--text3);">${esc(album) || 'Unknown Album'} · ${dur}</div>
            </div>
          </div>
          <div style="padding:4px;">
            ${tracks.map(t => {
        const fmt = (t.ext || '').replace('.', '').toUpperCase();
        const size = t.size ? fmtSize(t.size) : '—';
        const sr = t.sample_rate
          ? (t.sample_rate >= 1000 ? Math.round(t.sample_rate / 1000) + 'kHz' : t.sample_rate + 'Hz')
          : '—';
        return `
                <div style="display:flex;align-items:center;gap:12px;padding:10px 12px;border-radius:var(--radius);cursor:pointer;transition:background var(--transition);"
                     onmouseover="this.style.background='var(--bg4)'" onmouseout="this.style.background=''"
                     onclick="showDuplicateTrackDetail(${t.id})">
                  <div style="width:32px;height:32px;border-radius:var(--radius);background:var(--bg2);display:flex;align-items:center;justify-content:center;color:var(--text3);">
                    <i class="bi bi-music-note" style="font-size: 16px;"></i>
                  </div>
                  <div style="flex:1;min-width:0;">
                    <div style="font-size:13px;color:var(--text);font-weight:500;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${esc(t.filename)}</div>
                    <div style="font-size:11px;color:var(--text3);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${esc(t.path)}</div>
                  </div>
                  <div style="display:flex;align-items:center;gap:8px;">
                    <span style="padding:3px 8px;border-radius:var(--radius);font-size:11px;font-weight:500;${fmt === 'FLAC' ? 'background:var(--blue-dim);color:var(--blue);' :
            fmt === 'MP3' ? 'background:var(--amber-dim);color:var(--amber);' :
              'background:var(--bg4);color:var(--text2);'
          }">${fmt}</span>
                    <span style="font-size:11px;color:var(--text3);">${sr}</span>
                  </div>
                  <div style="font-size:12px;color:var(--text2);font-family:var(--font-mono);min-width:60px;text-align:right;">${size}</div>
                </div>
              `;
      }).join('')}
          </div>
        </div>
      `;
    }

    body.innerHTML = html;
    addDeleteDuplicatesButton();
  } catch (e) {
    body.innerHTML = `<div class="loading-row" style="color:var(--red)">加载失败: ${esc(e.message)}</div>`;
  }
}
