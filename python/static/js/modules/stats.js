/**
 * stats.js — 统计概览模块 + 重复文件弹窗
 * 依赖：api.js、utils.js、ui.js（openModal / hideDetailPanel / showToast / switchPage）
 * 依赖：detail.js（showDuplicateTrackDetail）
 */

/* ═══════════════════════════════════════════════════════════
   STATS
═══════════════════════════════════════════════════════════ */

/** 加载并渲染统计概览页 */
async function loadStats() {
  try {
    const s = await GET('/stats');
    const artistOrg = s.total_artists > 0 ? Math.round(s.org_artists / s.total_artists * 100) : 0;
    const albumOrg = s.total_albums > 0 ? Math.round(s.org_albums / s.total_albums * 100) : 0;

    document.getElementById('stats-view').innerHTML = `
      <div style="margin-bottom:20px;padding-bottom:16px;border-bottom:1px solid var(--border);">
        <div style="font-family:var(--font-display);font-size:20px;font-weight:700;margin-bottom:4px;">统计概览</div>
        <div style="font-family:var(--font-mono);font-size:11px;color:var(--text3);">上次扫描：${esc(s.last_scan)}</div>
      </div>
      <div class="stats-grid">
        <div class="stat-card" onclick="switchPage('artist');document.getElementById('nav-artist').classList.add('active')">
          <div class="stat-label">曲目总数</div>
          <div class="stat-value accent">${s.total_tracks}</div>
          <div class="stat-sub">FLAC ${s.flac_count} · MP3 ${s.mp3_count}</div>
          <div class="progress-bar">
            <div class="progress-fill" style="width:${s.total_tracks > 0 ? Math.round(s.flac_count / s.total_tracks * 100) : 0}%"></div>
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
      <div class="refresh-time">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
          <circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>
        </svg>
        上次刷新：${esc(s.last_scan)}
        <button class="toolbar-btn" style="margin-left:8px;" onclick="doScan()">立即刷新</button>
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

/** 打开重复文件弹窗并加载数据 */
async function openDuplicateModal() {
  openModal('duplicate-modal');
  hideDetailPanel();
  const body = document.getElementById('duplicate-modal-body');
  body.innerHTML = '<div class="loading-row">加载中...</div>';

  try {
    const duplicates = await GET('/duplicates');

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
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.2">
                <path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/>
              </svg>
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
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                      <path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/>
                    </svg>
                  </div>
                  <div style="flex:1;min-width:0;">
                    <div style="font-size:13px;color:var(--text);font-weight:500;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${esc(t.filename)}</div>
                    <div style="font-size:11px;color:var(--text3);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${esc(t.path)}</div>
                  </div>
                  <div style="display:flex;align-items:center;gap:8px;">
                    <span style="padding:3px 8px;border-radius:var(--radius);font-size:11px;font-weight:500;${
                      fmt === 'FLAC' ? 'background:var(--blue-dim);color:var(--blue);' :
                      fmt === 'MP3'  ? 'background:var(--amber-dim);color:var(--amber);' :
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
  } catch (e) {
    body.innerHTML = `<div class="loading-row" style="color:var(--red)">加载失败: ${esc(e.message)}</div>`;
  }
}
