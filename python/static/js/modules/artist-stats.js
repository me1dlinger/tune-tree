/**
 * artist-stats.js — 艺术家统计面板
 * 依赖：api.js、utils.js、ui.js、detail.js、metadata-edit.js
 */

let _artistStatsData = null;
let _similarArtistsData = null;
let _similarDetailTracks = [];
// A2：当前打开的相似度详情对（详情弹窗里也要能标记）
let _similarDetailPair = null;
// A2：已忽略的配对（「已忽略 N 组 · 管理」面板）
let _dismissedFeedback = [];
// A2：可配置的四档相似度阈值
let _similarThresholds = null;
// A1：已标记「不再获取头像」的艺术家
let _skippedCoverArtists = [];
let _asSearchTimer = null;

const SIMILAR_CACHE_KEY_PREFIX = 'tunetree_similar_artists';

function searchArtistInSidebar(name) {
  const searchInput = document.getElementById('artist-search');
  if (searchInput) {
    searchInput.value = name;
    filterArtists(name);
  }
}

function _similarCacheKey() {
  return currentLibrary && currentLibrary.id ? `${SIMILAR_CACHE_KEY_PREFIX}_${currentLibrary.id}` : SIMILAR_CACHE_KEY_PREFIX;
}

function _getCachedSimilar() {
  try {
    const raw = localStorage.getItem(_similarCacheKey());
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (parsed && parsed.data && parsed.ts) return parsed;
  } catch { }
  return null;
}

function _setCachedSimilar(data) {
  try {
    localStorage.setItem(_similarCacheKey(), JSON.stringify({ data, ts: Date.now() }));
  } catch { }
}

function fmtDurationLong(sec) {
  if (!sec) return '0:00';
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = Math.floor(sec % 60);
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m ${s}s`;
}

function renderDonutSVG(pct, color, size = 80) {
  const r = (size - 8) / 2;
  const c = 2 * Math.PI * r;
  const offset = c * (1 - pct / 100);
  return `<svg width="${size}" height="${size}" viewBox="0 0 ${size} ${size}">
    <circle cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none" stroke="var(--bg4)" stroke-width="5"/>
    <circle cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none" stroke="${color}" stroke-width="5"
      stroke-dasharray="${c}" stroke-dashoffset="${offset}" stroke-linecap="round"
      transform="rotate(-90 ${size / 2} ${size / 2})" style="transition:stroke-dashoffset .6s"/>
    <text x="${size / 2}" y="${size / 2}" text-anchor="middle" dominant-baseline="central"
      fill="var(--text)" font-family="var(--font-display)" font-size="14" font-weight="700">${pct}%</text>
  </svg>`;
}

function renderTopNBar(items, valueKey, formatFn, maxItems = 10) {
  if (!items || items.length === 0) return '<div class="as-empty">暂无数据</div>';
  return `<div class="as-topn-grid">${items.map((item, idx) => {
    const val = item[valueKey] || 0;
    const display = formatFn ? formatFn(val) : val;
    const rankClass = idx < 3 ? `as-topn-rank as-topn-rank-${idx + 1}` : 'as-topn-rank';
    return `<div class="as-topn-item">
      <span class="${rankClass}">${idx + 1}</span>
      <span class="as-topn-name" title="${esc(item.name)}" onclick="searchArtistInSidebar('${esc(escJs(item.name))}')">${esc(item.name)}</span>
      <span class="as-topn-val">${display}</span>
    </div>`;
  }).join('')}</div>`;
}

function _renderSimilarSection(similar, loading = false) {
  const section = document.querySelector('[data-section="similar"]');
  if (!section) return;
  const refreshBtn = `<button class="as-similar-refresh" onclick="refreshSimilarArtists()"${loading ? ' disabled' : ''}>
    <i class="bi bi-arrow-clockwise"></i>${loading ? ' 检测中...' : ' 重新检测'}
  </button>`;
  if (loading) {
    // 检测期间也要保留「已忽略 / 阈值」面板，否则用户正在改的阈值输入框会被销毁
    section.innerHTML = `
      <div class="as-section-title">相似艺术家检测 ${refreshBtn}</div>
      <div class="as-similar-hint">自适应相似度检测：简繁体、全半角、子串包含等</div>
      <div class="as-similar-loading"><i class="bi bi-hourglass-split"></i> 检测中请稍后...</div>
      ${_renderDismissedPanel()}
      ${_renderThresholdEditor()}
    `;
    return;
  }
  section.innerHTML = `
    <div class="as-section-title">相似艺术家检测 ${refreshBtn}</div>
    <div class="as-similar-hint">自适应相似度检测：简繁体、全半角、子串包含等</div>
    ${similar && similar.length > 0 ? `
      <div class="as-similar-grid">
        ${similar.map((g, idx) => `
          <div class="as-similar-card" onclick="openSimilarDetail(${g.artist_a.id}, ${g.artist_b.id})"
               onmouseenter="showSimilarHover(this, ${idx})" onmouseleave="hideSimilarHover(this)">
            <div class="as-similar-pair">
              <span class="as-similar-name">${esc(g.artist_a.name)}</span>
              <span class="as-similar-vs">/</span>
              <span class="as-similar-name">${esc(g.artist_b.name)}</span>
            </div>
            <div class="as-similar-score">${Math.round(g.similarity * 100)}%</div>
            ${g.feedback === 'confirmed' ? '<div class="as-similar-badge">已确认同一人</div>' : ''}
            <div class="as-similar-actions">
              <button class="as-mini-btn" title="不是同一人：以后不再提示这一对"
                      onclick="setSimilarFeedback(${g.artist_a.id}, ${g.artist_b.id}, 'dismissed', event)">不是同一人</button>
              <button class="as-mini-btn" title="是同一人（同一人的不同写法）"
                      onclick="setSimilarFeedback(${g.artist_a.id}, ${g.artist_b.id}, 'confirmed', event)">是同一人</button>
            </div>
            <div class="as-similar-hover-info" id="similar-hover-${idx}">
              <span>${g.artist_a.track_count} 首 / ${g.artist_a.album_count} 张</span>
              <span>${g.artist_b.track_count} 首 / ${g.artist_b.album_count} 张</span>
            </div>
          </div>
        `).join('')}
      </div>
      <div class="as-similar-tip">忽略一对会释放这两位艺术家参与其它配对，因此可能出现新的相似提示（这是预期行为）。</div>
    ` : '<div class="as-empty">未检测到相似艺术家</div>'}
    ${_renderDismissedPanel()}
    ${_renderThresholdEditor()}
  `;
}

/* ═══════════════════════════════════════════════════════════
   A2 相似艺术家反馈（不是同一人 / 是同一人）
═══════════════════════════════════════════════════════════ */

/**
 * 标记一对相似艺术家的人工判断。
 * @param {number} aId @param {number} bId
 * @param {'dismissed'|'confirmed'} verdict dismissed=不是同一人（以后不再报这对）
 * @param {Event} [ev] 传了就会 stopPropagation，避免触发卡片的 openSimilarDetail
 */
async function setSimilarFeedback(aId, bId, verdict, ev) {
  if (ev && ev.stopPropagation) ev.stopPropagation();
  try {
    await POST('/stats/similar-artists/feedback', {
      artist_a_id: aId,
      artist_b_id: bId,
      verdict,
    });
    if (verdict === 'dismissed') {
      // 本地先移除，避免重新拉取前的闪烁；真正的过滤在后端
      _similarArtistsData = (_similarArtistsData || []).filter(
        g => !(g.artist_a.id === aId && g.artist_b.id === bId)
      );
      showToast(
        '已标记「不是同一人」，之后不再提示这一对；可能有新的相似组合补位（可在「已忽略」里恢复）',
        'info'
      );
    } else {
      (_similarArtistsData || []).forEach(g => {
        if (g.artist_a.id === aId && g.artist_b.id === bId) g.feedback = 'confirmed';
      });
      showToast('已标记「是同一人」', 'success');
    }
    _setCachedSimilar(_similarArtistsData);
    await _loadDismissedFeedback();
    _renderSimilarSection(_similarArtistsData);
    if (_similarDetailPair && _similarDetailPair.a === aId && _similarDetailPair.b === bId) {
      // 详情弹窗保持同步（标记状态要立刻可见）
      openSimilarDetail(aId, bId);
    }
  } catch (e) {
    showToast('标记失败: ' + (e.serverMessage || e.message), 'error');
  }
}

/** 撤销标记：该对会重新参与检测 */
async function undoSimilarFeedback(aId, bId, ev) {
  if (ev && ev.stopPropagation) ev.stopPropagation();
  try {
    await DELETE(`/stats/similar-artists/feedback/${aId}/${bId}`);
    showToast('已恢复，该对会重新参与检测', 'info');
    await _loadDismissedFeedback();
    await refreshSimilarArtists();
  } catch (e) {
    showToast('恢复失败: ' + (e.serverMessage || e.message), 'error');
  }
}

async function _loadDismissedFeedback() {
  try {
    _dismissedFeedback = await GET('/stats/similar-artists/feedback?verdict=dismissed');
  } catch (e) {
    _dismissedFeedback = [];
  }
  return _dismissedFeedback;
}

async function _loadSimilarityThresholds() {
  try {
    _similarThresholds = await GET('/stats/similar-artists/thresholds');
  } catch (e) {
    _similarThresholds = null;
  }
  return _similarThresholds;
}

/** 保存四档阈值（不自动调参：由用户显式决定） */
async function saveSimilarityThresholds() {
  const keys = ['short', 'len3', 'len5', 'long'];
  const payload = {};
  try {
    for (const k of keys) {
      const el = document.getElementById(`sim-th-${k}`);
      const v = parseFloat(el ? el.value : '');
      if (!(v >= 0.5 && v <= 1)) {
        throw new Error('阈值必须在 0.5 ~ 1 之间');
      }
      payload[k] = v;
    }
    await PUT('/stats/similar-artists/thresholds', payload);
    showToast('阈值已保存，重新检测后生效', 'success');
    await refreshSimilarArtists();
  } catch (e) {
    showToast('保存失败: ' + (e.serverMessage || e.message), 'error');
  }
}

function _renderThresholdEditor() {
  const t = _similarThresholds || { short: 0.95, len3: 0.85, len5: 0.8, long: 0.75 };
  const row = (key, label) => `
    <label class="as-th-item">
      <span>${label}</span>
      <input id="sim-th-${key}" type="number" step="0.01" min="0.5" max="1" value="${t[key]}" />
    </label>`;
  return `
    <details class="as-similar-details">
      <summary>相似度阈值设置</summary>
      <div class="as-th-body">
        <div class="as-th-hint">名称越短要求越严。调低会多报（含误报），调高会漏报；系统不会自动调整。</div>
        <div class="as-th-grid">
          ${row('short', '1-2 字')}
          ${row('len3', '3 字')}
          ${row('len5', '4-5 字')}
          ${row('long', '6 字以上')}
        </div>
        <button class="as-batch-btn as-th-save" onclick="saveSimilarityThresholds()">保存阈值</button>
      </div>
    </details>`;
}

function _renderDismissedPanel() {
  if (!_dismissedFeedback.length) return '';
  return `
    <details class="as-similar-details">
      <summary>已忽略 ${_dismissedFeedback.length} 组 · 管理</summary>
      <div class="as-dismissed-list">
        ${_dismissedFeedback.map(f => `
          <div class="as-dismissed-item">
            <span class="as-dismissed-names">${esc(f.name_a)} / ${esc(f.name_b)}</span>
            <button class="as-mini-btn" onclick="undoSimilarFeedback(${f.artist_a_id}, ${f.artist_b_id}, event)">恢复</button>
          </div>
        `).join('')}
      </div>
    </details>`;
}

/* ═══════════════════════════════════════════════════════════
   A1 艺术家头像「不再获取」标记
═══════════════════════════════════════════════════════════ */

async function _loadSkippedCoverArtists() {
  try {
    _skippedCoverArtists = await GET('/artists/cover-skipped');
  } catch (e) {
    _skippedCoverArtists = [];
  }
  return _skippedCoverArtists;
}

/**
 * 标记 / 解除「不再自动获取该艺术家头像」。
 * @param {Event} [ev] 传了会 stopPropagation（卡片上还有跳转搜索的点击）
 */
async function setArtistCoverSkip(artistId, skip, ev) {
  if (ev && ev.stopPropagation) ev.stopPropagation();
  try {
    await POST(`/artists/${artistId}/cover-skip`, { skip });
    showToast(skip ? '已标记：以后不再自动获取该艺术家头像' : '已恢复自动获取', 'success');
    await _loadSkippedCoverArtists();
    await loadArtistStats();
  } catch (e) {
    showToast('操作失败: ' + (e.serverMessage || e.message), 'error');
  }
}

function _renderSkippedCoverPanel() {
  if (!_skippedCoverArtists.length) return '';
  return `
    <details class="as-similar-details">
      <summary>已跳过获取头像 ${_skippedCoverArtists.length} 位 · 管理</summary>
      <div class="as-dismissed-list">
        ${_skippedCoverArtists.map(a => `
          <div class="as-dismissed-item">
            <span class="as-dismissed-names">${esc(a.name)}${
              a.cover_fetch_attempts ? ` <span class="as-mini-hint">已尝试 ${a.cover_fetch_attempts} 次</span>` : ''
            }</span>
            <button class="as-mini-btn" onclick="setArtistCoverSkip(${a.id}, false, event)">恢复</button>
          </div>
        `).join('')}
      </div>
    </details>`;
}

async function loadArtistStats() {
  const container = document.getElementById('artist-stats-view');
  if (!container) return;
  container.innerHTML = '<div class="loading-row">加载中...</div>';

  try {
    const stats = await GET('/stats/artists');
    _artistStatsData = stats;
    // A1/A2 的辅助数据：已跳过头像的艺术家、已忽略的相似对、阈值
    await Promise.all([
      _loadSkippedCoverArtists(),
      _loadDismissedFeedback(),
      _loadSimilarityThresholds(),
    ]);

    const cached = _getCachedSimilar();
    if (cached) {
      _similarArtistsData = cached.data;
      renderArtistStats(stats, cached.data);
    } else {
      renderArtistStats(stats, null);
      _loadSimilarAsync();
    }
  } catch (e) {
    container.innerHTML = `<div class="loading-row" style="color:var(--red)">加载失败: ${esc(e.message)}</div>`;
  }
}

async function _loadSimilarAsync() {
  _snapshotThresholdInputs();
  _renderSimilarSection(null, true);
  try {
    const similar = await GET('/stats/similar-artists');
    _similarArtistsData = similar;
    _setCachedSimilar(similar);
    await _loadDismissedFeedback();
    _renderSimilarSection(similar, false);
  } catch (e) {
    const section = document.querySelector('[data-section="similar"]');
    if (section) {
      section.innerHTML = `
        <div class="as-section-title">相似艺术家检测 <button class="as-similar-refresh" onclick="refreshSimilarArtists()"><i class="bi bi-arrow-clockwise"></i> 重新检测</button></div>
        <div class="as-similar-hint">自适应相似度检测：简繁体、全半角、子串包含等</div>
        <div class="as-empty" style="color:var(--red)">检测失败: ${esc(e.message)}</div>
      `;
    }
  }
}

/** 重新渲染前把用户正在编辑的阈值输入备份下来，避免检测期间丢失未保存的修改 */
function _snapshotThresholdInputs() {
  const keys = ['short', 'len3', 'len5', 'long'];
  const snapshot = _similarThresholds ? { ..._similarThresholds } : {};
  let found = false;
  keys.forEach(k => {
    const el = document.getElementById(`sim-th-${k}`);
    if (el) {
      const v = parseFloat(el.value);
      if (!Number.isNaN(v)) {
        snapshot[k] = v;
        found = true;
      }
    }
  });
  if (found) _similarThresholds = snapshot;
}

async function refreshSimilarArtists() {
  _snapshotThresholdInputs();
  _renderSimilarSection(null, true);
  try {
    const similar = await GET('/stats/similar-artists');
    _similarArtistsData = similar;
    _setCachedSimilar(similar);
    await _loadDismissedFeedback();
    _renderSimilarSection(similar, false);
  } catch (e) {
    _renderSimilarSection(_similarArtistsData, false);
    showToast('相似艺术家检测失败: ' + e.message, 'error');
  }
}

function renderArtistStats(stats, similar) {
  const container = document.getElementById('artist-stats-view');
  const total = stats.total || 0;
  const orgPct = total > 0 ? Math.round(stats.organized / total * 100) : 0;
  const coverPct = total > 0 ? Math.round(stats.with_cover / total * 100) : 0;
  const lyricsPct = total > 0 ? Math.round(stats.with_lyrics / total * 100) : 0;
  const tagPct = total > 0 ? Math.round(stats.with_track_tags / total * 100) : 0;

  container.innerHTML = `
    <div class="as-header">
      <div class="as-title">艺术家统计</div>
      <div class="as-header-right">
        <div class="as-search-box">
          <i class="bi bi-search"></i>
          <input type="text" id="as-search-input" placeholder="搜索艺术家..." oninput="onArtistStatsSearch(this.value)">
        </div>
        <div class="as-back" onclick="closeArtistStats()">
          <i class="bi bi-arrow-left"></i> 返回概览
        </div>
      </div>
    </div>

    <div class="as-content" id="as-content">
      <div class="as-section" data-section="overview">
        <div class="as-section-title">总览</div>
        <div class="as-overview-row">
          <div class="as-overview-card">
            <div class="as-overview-value">${total}</div>
            <div class="as-overview-label">艺术家总数</div>
          </div>
          <div class="as-ring-group">
            <div class="as-ring-item">
              ${renderDonutSVG(orgPct, 'var(--accent)')}
              <div class="as-ring-label">已整理 ${stats.organized} / ${total}</div>
            </div>
            <div class="as-ring-item">
              ${renderDonutSVG(coverPct, 'var(--blue)')}
              <div class="as-ring-label">有封面 ${stats.with_cover} / ${total}</div>
            </div>
            <div class="as-ring-item">
              ${renderDonutSVG(lyricsPct, 'var(--amber)')}
              <div class="as-ring-label">有歌词 ${stats.with_lyrics} / ${total}</div>
            </div>
            <div class="as-ring-item">
              ${renderDonutSVG(tagPct, 'var(--red)')}
              <div class="as-ring-label">标签完整 ${stats.with_track_tags} / ${total}</div>
            </div>
          </div>
        </div>
      </div>

      <div class="as-section" data-section="nocover">
        <div class="as-section-title">无封面艺术家 TOP 10${
          stats.cover_skipped ? `（另有 ${stats.cover_skipped} 位已标记不再获取）` : ''
        }</div>
        <div class="as-nocover-row">
          ${stats.no_cover_top && stats.no_cover_top.length > 0 ? stats.no_cover_top.map(a => `
            <div class="as-nocover-item">
              <div class="as-nocover-icon"><i class="bi bi-person-circle"></i></div>
              <div class="as-nocover-info">
                <div class="as-nocover-name" title="${esc(a.name)}" onclick="searchArtistInSidebar('${esc(escJs(a.name))}')">${esc(a.name)}</div>
                <div class="as-nocover-count">${a.track_count} 首曲目</div>
              </div>
              <button class="as-mini-btn" title="标记为「以后不再自动获取头像」，并从待办列表移除"
                      onclick="setArtistCoverSkip(${a.id}, true, event)">不再获取</button>
            </div>
          `).join('') : '<div class="as-empty">所有艺术家都有封面</div>'}
        </div>
        ${stats.no_cover_top && stats.no_cover_top.length > 0 ? `
          <button class="as-batch-btn" onclick="batchScrapeNoCover()">
            <i class="bi bi-cloud-download"></i> 一键获取封面（全部未跳过）
          </button>
        ` : ''}
        ${_renderSkippedCoverPanel()}
      </div>

      <div class="as-section" data-section="top-albums">
        <div class="as-section-title">专辑数 TOP 10</div>
        ${renderTopNBar(stats.top_by_albums, 'album_count', v => v + ' 张')}
      </div>

      <div class="as-section" data-section="top-tracks">
        <div class="as-section-title">曲目数 TOP 10</div>
        ${renderTopNBar(stats.top_by_tracks, 'track_count', v => v + ' 首')}
      </div>

      <div class="as-section" data-section="top-duration">
        <div class="as-section-title">总时长 TOP 10</div>
        ${renderTopNBar(stats.top_by_duration, 'total_duration', v => fmtDurationLong(v))}
      </div>

      <div class="as-section" data-section="similar">
        <div class="as-section-title">相似艺术家检测</div>
        <div class="as-similar-hint">自适应相似度检测：简繁体、全半角、子串包含等</div>
        <div class="as-empty">加载中...</div>
      </div>
    </div>

    <div class="as-search-results" id="as-search-results" style="display:none"></div>
  `;

  if (similar) {
    _renderSimilarSection(similar, false);
  }
}

function onArtistStatsSearch(query) {
  clearTimeout(_asSearchTimer);
  const q = query.trim();
  if (!q) {
    document.getElementById('as-content').style.display = '';
    document.getElementById('as-search-results').style.display = 'none';
    return;
  }
  _asSearchTimer = setTimeout(() => searchArtistStats(q), 200);
}

async function searchArtistStats(query) {
  const resultsEl = document.getElementById('as-search-results');
  const contentEl = document.getElementById('as-content');
  if (!resultsEl || !contentEl) return;

  contentEl.style.display = 'none';
  resultsEl.style.display = '';

  try {
    const artists = await GET('/artists?q=' + encodeURIComponent(query));
    if (!artists || artists.length === 0) {
      resultsEl.innerHTML = `<div class="as-empty">未找到匹配的艺术家</div>`;
      return;
    }
    resultsEl.innerHTML = `
      <div class="as-section-title">搜索结果 (${artists.length})</div>
      <div class="as-search-list">
        ${artists.map(a => `
          <div class="as-search-item" onclick="showArtistSearchDetail(${a.id})">
            <div class="as-search-icon">
              ${a.cover_path ? `<img src="/api/artists/${a.id}/cover?token=${TOKEN}" alt="" loading="lazy" decoding="async">` : '<i class="bi bi-person-circle"></i>'}
            </div>
            <div class="as-search-info">
              <div class="as-search-name">${esc(a.name)}</div>
              <div class="as-search-meta">${a.album_count || 0} 张专辑 / ${a.track_count || 0} 首曲目${a.all_organized ? '' : ' · <span style="color:var(--amber)">未整理</span>'}</div>
            </div>
          </div>
        `).join('')}
      </div>
    `;
  } catch (e) {
    resultsEl.innerHTML = `<div class="as-empty" style="color:var(--red)">搜索失败: ${esc(e.message)}</div>`;
  }
}

function showArtistSearchDetail(artistId) {
  switchPage('artist');
  const navBtn = document.getElementById('nav-artist');
  if (navBtn) navBtn.classList.add('active');
  setTimeout(() => {
    if (typeof selectArtist === 'function') selectArtist(artistId);
  }, 100);
}

function showSimilarHover(el, idx) {
  const info = document.getElementById('similar-hover-' + idx);
  if (info) info.style.display = 'flex';
}

function hideSimilarHover(el) {
  const info = el.querySelector('.as-similar-hover-info');
  if (info) info.style.display = 'none';
}

function openArtistStats() {
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));
  document.getElementById('page-artist-stats').classList.add('active');
  currentPage = 'artist-stats';
  loadArtistStats();
}

function closeArtistStats() {
  switchPage('stats');
}

async function batchScrapeNoCover() {
  const btn = document.querySelector('.as-batch-btn');
  const reset = () => {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = '<i class="bi bi-cloud-download"></i> 一键获取封面（全部未跳过）';
    }
  };
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = '<i class="bi bi-hourglass-split"></i> 获取中...';
  }

  try {
    // 旧实现只处理统计面板里的 TOP10；这里取「全部未跳过且无封面」的艺术家
    const targets = await GET('/artists/without-cover');
    const ids = (targets || []).map(a => a.id);
    if (ids.length === 0) {
      showToast('没有需要获取封面的艺术家', 'info');
      reset();
      return;
    }
    const result = await POST('/artists/batch-scrape-covers', { artist_ids: ids });
    const skipped = result.skipped || 0;
    showToast(
      `封面获取完成：成功 ${result.success}/${result.total}${skipped ? `，跳过 ${skipped} 位已标记的艺术家` : ''}`,
      result.success > 0 ? 'info' : 'warn'
    );
    await loadArtistStats();
  } catch (e) {
    showToast('批量获取封面失败: ' + (e.serverMessage || e.message), 'error');
  } finally {
    reset();
  }
}

async function openSimilarDetail(aId, bId) {
  _similarDetailPair = { a: aId, b: bId };
  openModal('similar-detail-modal');
  const body = document.getElementById('similar-detail-body');
  body.innerHTML = '<div class="loading-row">加载中...</div>';

  try {
    const data = await GET(`/stats/similar-artists/${aId}/${bId}`);
    renderSimilarDetail(body, data);
  } catch (e) {
    body.innerHTML = `<div class="loading-row" style="color:var(--red)">加载失败: ${esc(e.message)}</div>`;
  }
}

function renderSimilarDetail(container, data) {
  const a = data.artist_a;
  const b = data.artist_b;
  _similarDetailTracks = [];

  function trackRow(t, side) {
    const idx = _similarDetailTracks.length;
    _similarDetailTracks.push(t);
    return `<div class="asd-track-item" onclick="openMetadataEdit(_similarDetailTracks[${idx}])">
      <span class="asd-track-num">${t.track_num || ''}</span>
      <span class="asd-track-name" title="${esc(t.title || t.filename)}">${esc(t.title || t.filename)}</span>
      <span class="asd-track-dur">${t.duration ? fmtDur(t.duration) : ''}</span>
    </div>`;
  }

  const pair = _similarDetailPair || { a: a.id, b: b.id };
  const group = (_similarArtistsData || []).find(
    g => g.artist_a.id === pair.a && g.artist_b.id === pair.b
  );
  const actionBar = `
    <div class="asd-actions">
      ${group && group.feedback === 'confirmed' ? '<span class="as-similar-badge">已确认同一人</span>' : ''}
      <button class="as-mini-btn" onclick="setSimilarFeedback(${pair.a}, ${pair.b}, 'dismissed', event)">不是同一人</button>
      <button class="as-mini-btn" onclick="setSimilarFeedback(${pair.a}, ${pair.b}, 'confirmed', event)">是同一人</button>
    </div>`;

  container.innerHTML = `
    ${actionBar}
    <div class="asd-compare">
      <div class="asd-artist-col">
        <div class="asd-artist-name">${esc(a.name)}</div>
        <div class="asd-artist-meta">${a.tracks.length} 首 / ${a.albums.length} 张专辑</div>
        <div class="asd-albums">
          ${a.albums.map(al => `
            <div class="asd-album-item">
              <i class="bi bi-disc"></i>
              <span>${esc(al.title)}</span>
              <span class="asd-album-year">${al.year || ''}</span>
            </div>
          `).join('')}
        </div>
        <div class="asd-tracks">
          <div class="asd-tracks-title">曲目列表</div>
          ${a.tracks.slice(0, 50).map(t => trackRow(t, 'a')).join('')}
          ${a.tracks.length > 50 ? `<div class="asd-more">...还有 ${a.tracks.length - 50} 首</div>` : ''}
        </div>
      </div>
      <div class="asd-divider"></div>
      <div class="asd-artist-col">
        <div class="asd-artist-name">${esc(b.name)}</div>
        <div class="asd-artist-meta">${b.tracks.length} 首 / ${b.albums.length} 张专辑</div>
        <div class="asd-albums">
          ${b.albums.map(al => `
            <div class="asd-album-item">
              <i class="bi bi-disc"></i>
              <span>${esc(al.title)}</span>
              <span class="asd-album-year">${al.year || ''}</span>
            </div>
          `).join('')}
        </div>
        <div class="asd-tracks">
          <div class="asd-tracks-title">曲目列表</div>
          ${b.tracks.slice(0, 50).map(t => trackRow(t, 'b')).join('')}
          ${b.tracks.length > 50 ? `<div class="asd-more">...还有 ${b.tracks.length - 50} 首</div>` : ''}
        </div>
      </div>
    </div>
  `;
}
