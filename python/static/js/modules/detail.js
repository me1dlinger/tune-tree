/**
 * detail.js — 右侧详情面板模块
 * 包含：曲目详情展示、文件详情展示、歌词复制、面板显隐控制。
 * 依赖：api.js、state.js（TOKEN）、utils.js、ui.js（showToast）
 */

/* ═══════════════════════════════════════════════════════════
   PANEL VISIBILITY
═══════════════════════════════════════════════════════════ */

/** 隐藏详情面板 */
function hideDetailPanel() {
  document.getElementById('detail-panel').classList.add('hidden');
}

/** 切换（收起）详情面板 */
function toggleDetailPanel() {
  document.getElementById('detail-panel').classList.add('hidden');
}

/* ═══════════════════════════════════════════════════════════
   DETAIL ROW HELPER
═══════════════════════════════════════════════════════════ */

/**
 * 渲染一行 key-value 详情（值为空时不输出）
 * @param {string} label
 * @param {*} val
 * @returns {string}
 */
function dr(label, val) {
  if (!val && val !== 0) return '';
  return `<div class="detail-row">
    <span class="detail-key">${label}</span>
    <span class="detail-val">${esc(String(val))}</span>
  </div>`;
}

/* ═══════════════════════════════════════════════════════════
   COVER RENDER HELPER
═══════════════════════════════════════════════════════════ */

/**
 * 渲染封面（有图则用 img，否则用占位符 SVG）
 * @param {{ has_cover: boolean, id: number }} track
 * @param {HTMLElement} coverEl
 */
let _coverBustIds = new Set();

function bustCoverCache(trackId) {
  _coverBustIds.add(trackId);
}

function coverUrl(trackId) {
  const bust = _coverBustIds.has(trackId) ? `&_t=${Date.now()}` : '';
  return `/api/cover/${trackId}?token=${TOKEN}${bust}`;
}

function renderCover(track, coverEl) {
  const placeholder = `<i class="bi bi-disc" style="font-size: 48px;"></i>`;

  if (track.has_cover) {
    const url = coverUrl(track.id);
    const coverFilename = `${(track.artist || 'unknown').replace(/[\\/:*?"<>|]/g, '_')}-${(track.album || 'unknown').replace(/[\\/:*?"<>|]/g, '_')}`;
    const img = document.createElement('img');
    img.src = url;
    img.style.cssText = 'width:100%;height:100%;object-fit:cover;cursor:zoom-in;';
    img.onload = function () {
      _coverBustIds.delete(track.id);
    };
    img.onerror = function () {
      coverEl.innerHTML = placeholder;
    };
    img.onclick = function () {
      openCoverImageViewer(url, coverFilename);
    };
    coverEl.innerHTML = '';
    coverEl.appendChild(img);
  } else {
    coverEl.innerHTML = placeholder;
  }
}

/**
 * 打开封面图片查看器
 * @param {string} coverUrl - 封面图片 URL
 * @param {string} filename - 下载时的文件名
 */
function openCoverImageViewer(coverUrl, filename = 'album-cover') {
  if (typeof showImageViewer === 'function') {
    showImageViewer(coverUrl, { filename: filename });
  } else {
    window.open(coverUrl, '_blank');
  }
}

/* ═══════════════════════════════════════════════════════════
   DETAIL BODY BUILDER
═══════════════════════════════════════════════════════════ */

/**
 * 将 track 对象渲染为详情面板 body HTML
 * @param {object} t — 曲目数据
 * @param {string} lyricsPrefix — 歌词元素 id 前缀（区分 artist/files 页）
 * @returns {string}
 */
function buildDetailBody(t, lyricsPrefix = '') {
  const dur = t.duration ? fmtDur(t.duration) : '—';
  const sr = t.sample_rate
    ? (t.sample_rate >= 1000 ? Math.round(t.sample_rate / 1000) + ' kHz' : t.sample_rate + ' Hz')
    : '—';
  const br = t.bitrate ? Math.round(t.bitrate / 1000) + ' kbps' : '—';
  const size = t.size ? fmtSize(t.size) : '—';
  const fmt = (t.ext || '').replace('.', '').toUpperCase();
  const lyricsId = lyricsPrefix ? `lyrics-${lyricsPrefix}-${t.id}` : `lyrics-${t.id}`;

  return `
    <div class="detail-title-row">
      <div class="detail-title">${esc(t.title || t.filename)}</div>
      <button class="detail-edit-btn" onclick="openMetadataEdit(window._currentTrack)" title="编辑元数据">
        <i class="bi bi-pencil"></i>
      </button>
    </div>
    <div class="detail-artist">${esc(t.artist || '未知艺术家')}${t.album ? ' — ' + esc(t.album) : ''}</div>
    <div class="detail-section">
      <div class="detail-section-label">元数据</div>
      ${dr('专辑艺术家', t.album_artist)}
      ${dr('年份', t.year)}
      ${dr('音轨号', t.track_num)}
      ${dr('碟号', t.disc_num)}
    </div>
    <div class="detail-section">
      <div class="detail-section-label">音频信息</div>
      ${dr('格式', fmt)}
      ${dr('时长', dur)}
      ${dr('采样率', sr)}
      ${dr('比特率', br)}
      ${dr('大小', size)}
    </div>
    <div class="detail-section">
      <div class="detail-section-label">文件</div>
      <div style="font-family:var(--font-mono);font-size:10px;color:var(--text3);word-break:break-all;">
        ${esc(t.path)}
      </div>
    </div>
    ${t.lyrics ? `
    <div class="detail-section">
      <div class="detail-section-label">歌词</div>
      <div class="detail-lyrics-wrap">
        <div class="detail-lyrics" id="${lyricsId}">${esc(t.lyrics)}</div>
        <button class="lyrics-copy-btn" onclick="copyLyricsById('${lyricsPrefix}', ${t.id})" title="复制歌词">
          <i class="bi bi-clipboard"></i>
        </button>
      </div>
    </div>` : ''}
  `;
}

/* ═══════════════════════════════════════════════════════════
   PUBLIC API
═══════════════════════════════════════════════════════════ */

/**
 * 通过曲目 id 展示详情面板（艺术家视图使用）
 * @param {number} id
 */
async function showTrackDetail(id) {
  try {
    const t = await GET(`/tracks/${id}`);
    window._currentTrack = t;
    const panel = document.getElementById('detail-panel');
    panel.classList.remove('hidden');
    renderCover(t, document.getElementById('detail-cover'));
    document.getElementById('detail-body').innerHTML = buildDetailBody(t, '');
  } catch (e) {
    showToast('加载详情失败: ' + e.message, 'error');
  }
}

/**
 * 通过文件路径展示详情面板（文件浏览器使用）
 * @param {string} path
 */
async function showFileMeta(path) {
  try {
    const t = await GET(`/tracks/by-path?path=${encodeURIComponent(path)}`);
    window._currentTrack = t;
    const panel = document.getElementById('detail-panel');
    panel.classList.remove('hidden');
    renderCover(t, document.getElementById('detail-cover'));
    document.getElementById('detail-body').innerHTML = buildDetailBody(t, 'files');
  } catch (e) {
    showToast('加载详情失败: ' + e.message, 'error');
  }
}

/**
 * 在重复文件弹窗中展示详情（不关闭弹窗）
 * @param {number} trackId
 */
function showDuplicateTrackDetail(trackId) {
  showTrackDetail(trackId);
}

/* ═══════════════════════════════════════════════════════════
   LYRICS COPY
═══════════════════════════════════════════════════════════ */

/**
 * 复制指定曲目的歌词到剪贴板
 * @param {string} prefix  — 'files' 或 ''
 * @param {number} trackId
 */
async function copyLyricsById(prefix, trackId) {
  // 兼容旧调用签名：copyLyricsById(trackId)
  if (!trackId) {
    trackId = prefix;
    prefix = '';
  }
  const id = prefix ? `lyrics-${prefix}-${trackId}` : `lyrics-${trackId}`;
  const el = document.getElementById(id);
  if (!el) {
    showToast('歌词元素未找到', 'error');
    return;
  }
  const lyrics = el.textContent;
  try {
    await navigator.clipboard.writeText(lyrics);
    showToast('歌词已复制到剪贴板', 'success');
  } catch {
    // 降级方案
    const textarea = document.createElement('textarea');
    textarea.value = lyrics;
    document.body.appendChild(textarea);
    textarea.select();
    document.execCommand('copy');
    document.body.removeChild(textarea);
    showToast('歌词已复制到剪贴板', 'success');
  }
}
