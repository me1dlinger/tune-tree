/**
 * artist-stats.js — 艺术家统计面板
 * 依赖：api.js、utils.js、ui.js、detail.js、metadata-edit.js
 */

let _artistStatsData = null;
let _similarArtistsData = null;
let _similarDetailTracks = [];
let _asSearchTimer = null;

const SIMILAR_CACHE_KEY = 'tunetree_similar_artists';

function _getCachedSimilar() {
  try {
    const raw = localStorage.getItem(SIMILAR_CACHE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (parsed && parsed.data && parsed.ts) return parsed;
  } catch {}
  return null;
}

function _setCachedSimilar(data) {
  try {
    localStorage.setItem(SIMILAR_CACHE_KEY, JSON.stringify({ data, ts: Date.now() }));
  } catch {}
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
    <circle cx="${size/2}" cy="${size/2}" r="${r}" fill="none" stroke="var(--bg4)" stroke-width="5"/>
    <circle cx="${size/2}" cy="${size/2}" r="${r}" fill="none" stroke="${color}" stroke-width="5"
      stroke-dasharray="${c}" stroke-dashoffset="${offset}" stroke-linecap="round"
      transform="rotate(-90 ${size/2} ${size/2})" style="transition:stroke-dashoffset .6s"/>
    <text x="${size/2}" y="${size/2}" text-anchor="middle" dominant-baseline="central"
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
      <span class="as-topn-name" title="${esc(item.name)}">${esc(item.name)}</span>
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
    section.innerHTML = `
      <div class="as-section-title">相似艺术家检测 ${refreshBtn}</div>
      <div class="as-similar-hint">自适应相似度检测：简繁体、全半角、子串包含等</div>
      <div class="as-similar-loading"><i class="bi bi-hourglass-split"></i> 检测中请稍后...</div>
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
            <div class="as-similar-hover-info" id="similar-hover-${idx}">
              <span>${g.artist_a.track_count} 首 / ${g.artist_a.album_count} 张</span>
              <span>${g.artist_b.track_count} 首 / ${g.artist_b.album_count} 张</span>
            </div>
          </div>
        `).join('')}
      </div>
    ` : '<div class="as-empty">未检测到相似艺术家</div>'}
  `;
}

async function loadArtistStats() {
  const container = document.getElementById('artist-stats-view');
  if (!container) return;
  container.innerHTML = '<div class="loading-row">加载中...</div>';

  try {
    const stats = await GET('/stats/artists');
    _artistStatsData = stats;

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
  _renderSimilarSection(null, true);
  try {
    const similar = await GET('/stats/similar-artists');
    _similarArtistsData = similar;
    _setCachedSimilar(similar);
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

async function refreshSimilarArtists() {
  _renderSimilarSection(null, true);
  try {
    const similar = await GET('/stats/similar-artists');
    _similarArtistsData = similar;
    _setCachedSimilar(similar);
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
        <div class="as-section-title">无封面艺术家 TOP 10</div>
        <div class="as-nocover-row">
          ${stats.no_cover_top && stats.no_cover_top.length > 0 ? stats.no_cover_top.map(a => `
            <div class="as-nocover-item">
              <div class="as-nocover-icon"><i class="bi bi-person-circle"></i></div>
              <div class="as-nocover-info">
                <div class="as-nocover-name" title="${esc(a.name)}">${esc(a.name)}</div>
                <div class="as-nocover-count">${a.track_count} 首曲目</div>
              </div>
            </div>
          `).join('') : '<div class="as-empty">所有艺术家都有封面</div>'}
        </div>
        ${stats.no_cover_top && stats.no_cover_top.length > 0 ? `
          <button class="as-batch-btn" onclick="batchScrapeNoCover()">
            <i class="bi bi-cloud-download"></i> 一键获取封面
          </button>
        ` : ''}
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
              ${a.cover_path ? `<img src="/api/artists/${a.id}/cover?token=${TOKEN}" alt="">` : '<i class="bi bi-person-circle"></i>'}
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
  if (!_artistStatsData || !_artistStatsData.no_cover_top) return;
  const ids = _artistStatsData.no_cover_top.map(a => a.id);
  if (ids.length === 0) return;

  const btn = document.querySelector('.as-batch-btn');
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = '<i class="bi bi-hourglass-split"></i> 获取中...';
  }

  try {
    const result = await POST('/artists/batch-scrape-covers', { artist_ids: ids });
    showToast(`封面获取完成：${result.success}/${result.total} 成功`);
    loadArtistStats();
  } catch (e) {
    showToast('批量获取封面失败: ' + e.message, 'error');
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = '<i class="bi bi-cloud-download"></i> 一键获取封面';
    }
  }
}

async function openSimilarDetail(aId, bId) {
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

  container.innerHTML = `
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
