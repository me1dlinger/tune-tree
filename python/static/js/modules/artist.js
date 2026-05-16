/**
 * artist.js — 艺术家视图模块
 * 包含：侧边栏艺术家树、专辑网格、曲目列表、工具栏选中状态。
 * 依赖：api.js、state.js、utils.js、ui.js
 */

/* ═══════════════════════════════════════════════════════════
   ARTIST TREE（侧边栏）
═══════════════════════════════════════════════════════════ */

/**
 * 从服务端加载艺术家列表并渲染树
 * @param {string} [q] — 可选搜索关键字
 */
async function loadArtistTree(q) {
  const url = '/artists' + (q ? `?q=${encodeURIComponent(q)}` : '');
  try {
    allArtists = await GET(url);
    renderArtistTree(getSortedArtists(allArtists));
  } catch (e) {
    document.getElementById('artist-tree').innerHTML =
      `<div class="loading-row" style="color:var(--red)">加载失败: ${e.message}</div>`;
  }
}

/**
 * 根据搜索框输入过滤艺术家（纯前端过滤）
 * @param {string} q
 */
function filterArtists(q) {
  let toRender = allArtists;
  if (q) {
    toRender = allArtists.filter(a =>
      a.artist.toLowerCase().includes(q.toLowerCase())
    );
  }
  renderArtistTree(getSortedArtists(toRender));
}

/* ═══════════════════════════════════════════════════════════
   ARTIST SORTING
═══════════════════════════════════════════════════════════ */

let currentArtistSort = 'name';

function setArtistSort(sort) {
  currentArtistSort = sort;
  document.getElementById('sort-artist-name').classList.toggle('active', sort === 'name');
  document.getElementById('sort-artist-count').classList.toggle('active', sort === 'count');
  const searchInput = document.getElementById('artist-search');
  filterArtists(searchInput.value);
}

function getSortedArtists(artists) {
  const sorted = [...artists];
  if (currentArtistSort === 'count') {
    sorted.sort((a, b) => b.track_count - a.track_count);
  } else {
    sorted.sort((a, b) => (a.artist || '').localeCompare(b.artist || '', undefined, { sensitivity: 'base', numeric: true }));
  }
  return sorted;
}

/**
 * 将艺术家数组渲染为侧边栏树
 * @param {Array} artists
 */
function renderArtistTree(artists) {
  const tree = document.getElementById('artist-tree');
  if (!artists.length) {
    tree.innerHTML = '<div class="loading-row">暂无数据</div>';
    return;
  }
  tree.innerHTML = artists.map(a => `
    <div class="tree-artist" id="ta-${eid(a.artist)}">
      <div class="tree-artist-header" onclick="toggleArtist('${esc(a.artist)}')" id="tah-${eid(a.artist)}">
        <div class="tree-chevron" id="tch-${eid(a.artist)}">▶</div>
        <div class="tree-artist-name">${esc(a.artist)}</div>
        ${a.all_organized ? '<div class="tree-organized" title="已整理"></div>' : ''}
        <div class="tree-badge">${a.track_count}</div>
      </div>
      <div class="tree-albums" id="talb-${eid(a.artist)}">
        <div class="loading-row" style="font-size:10px;">点击展开...</div>
      </div>
    </div>
  `).join('');
}

/**
 * 展开/收起艺术家，并加载其专辑
 * @param {string} artist
 */
async function toggleArtist(artist) {
  const albums = document.getElementById('talb-' + eid(artist));
  const chevron = document.getElementById('tch-' + eid(artist));
  const header = document.getElementById('tah-' + eid(artist));
  const isOpen = albums.classList.contains('open');

  document.querySelectorAll('.tree-artist-header').forEach(h => h.classList.remove('active'));
  document.querySelectorAll('.tree-albums').forEach(a => a.classList.remove('open'));
  document.querySelectorAll('.tree-chevron').forEach(c => c.classList.remove('open'));

  if (!isOpen) {
    albums.classList.add('open');
    chevron.classList.add('open');
    header.classList.add('active');
    await selectArtist(artist, albums);
  }
}

/**
 * 选中艺术家，加载专辑列表，渲染主视图
 * @param {string} artist
 * @param {HTMLElement|null} albumsEl — 侧边栏专辑容器（null 表示仅刷新主视图）
 */
async function selectArtist(artist, albumsEl) {
  currentArtist = allArtists.find(a => a.artist === artist);
  currentAlbum = null;
  selectedAlbums.clear();
  selectedTracks.clear();

  try {
    artistAlbums = await GET(`/artists/${encodeURIComponent(artist)}/albums`);

    if (albumsEl) {
      albumsEl.innerHTML = artistAlbums.map(al => `
        <div class="tree-album" id="talbcard-${eid(artist + '|' + al.album)}"
             onclick="selectAlbumFromTree('${esc(artist)}','${esc(al.album)}')">
          <div class="tree-album-name">${esc(al.album)}</div>
          ${al.all_organized ? '<div style="font-size:8px;color:var(--accent)">●</div>' : ''}
          <div class="tree-album-count">${al.track_count}</div>
        </div>
      `).join('');
    }

    renderArtistView();
    switchPage('artist');
  } catch (e) {
    showToast('加载失败: ' + e.message, 'error');
  }
}

/**
 * 从侧边栏专辑行选中单张专辑
 * @param {string} artist
 * @param {string} album
 */
function selectAlbumFromTree(artist, album) {
  document.querySelectorAll('.tree-album').forEach(el => el.classList.remove('active'));
  const card = document.getElementById('talbcard-' + eid(artist + '|' + album));
  if (card) card.classList.add('active');
  currentAlbum = album;
  selectedAlbums.clear();
  selectedTracks.clear();
  renderArtistView();
  switchPage('artist');
}

/* ═══════════════════════════════════════════════════════════
   ARTIST VIEW（主区域）
═══════════════════════════════════════════════════════════ */

/** 渲染艺术家主视图（专辑网格 + 曲目列表） */
async function renderArtistView() {
  if (!currentArtist) return;
  const view = document.getElementById('artist-view');
  const a = currentArtist;

  // 面包屑
  let bc = `<span>${esc(a.artist)}</span>`;
  if (currentAlbum) bc += `<span class="sep">/</span><span>${esc(currentAlbum)}</span>`;
  document.getElementById('breadcrumb').innerHTML = bc;

  const albumsToShow = currentAlbum
    ? artistAlbums.filter(al => al.album === currentAlbum)
    : artistAlbums;

  const totalTracks = artistAlbums.reduce((s, al) => s + al.track_count, 0);
  const orgAlbums = artistAlbums.filter(al => al.all_organized).length;

  view.innerHTML = `
    <div class="artist-header">
      <div class="artist-cover" id="artist-cover-img">
        <svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1">
          <path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/>
        </svg>
      </div>
      <div class="artist-info-block">
        <div class="artist-title">${esc(a.artist)}</div>
        <div class="artist-meta">
          <span>${artistAlbums.length} 张专辑</span>
          <span>${totalTracks} 首曲目</span>
          <span>${orgAlbums} 已整理</span>
          ${a.all_organized ? '<span class="organized-badge">● 已整理</span>' : ''}
        </div>
      </div>
    </div>

    <div class="albums-grid">
      ${albumsToShow.map(al => `
        <div class="album-card ${selectedAlbums.has(al.album) ? 'selected' : ''}" id="alcard-${eid(al.album)}">
          <div class="album-cover-wrap">
            ${al.has_cover_some && al.sample_id
      ? `<img class="album-cover-img" src="/api/cover/${al.sample_id}?token=${TOKEN}" onerror="this.style.display='none'" loading="lazy">`
      : ''
    }
            <div class="album-cover-placeholder" ${al.has_cover_some && al.sample_id ? 'style="display:none"' : ''}>
              <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="0.8">
                <rect x="3" y="3" width="18" height="18" rx="1"/><circle cx="12" cy="12" r="4"/>
                <circle cx="12" cy="12" r="1.5" fill="currentColor"/>
              </svg>
            </div>
            <div class="album-select-overlay">
              <div class="album-checkbox" onclick="toggleAlbum(event,'${esc(al.album)}')">
                ${selectedAlbums.has(al.album) ? '✓' : ''}
              </div>
            </div>
          </div>
          <div class="album-info" onclick="expandAlbum('${esc(al.album)}')">
            <div class="album-name">${esc(al.album)}${al.all_organized ? '<span class="album-organized">●</span>' : ''}</div>
            <div class="album-year">${al.year || '—'} · ${al.track_count} 曲</div>
          </div>
        </div>
      `).join('')}
    </div>

    <div id="track-sections"></div>
  `;

  for (const al of albumsToShow) {
    await loadTrackSection(a.artist, al.album);
  }
  updateToolbar();
}

/* ═══════════════════════════════════════════════════════════
   TRACK SECTION
═══════════════════════════════════════════════════════════ */

/**
 * 加载并渲染单张专辑的曲目列表
 * @param {string} artist
 * @param {string} album
 */
async function loadTrackSection(artist, album) {
  const container = document.getElementById('track-sections');
  const sectionId = 'ts-' + eid(album);

  if (!document.getElementById(sectionId)) {
    const div = document.createElement('div');
    div.className = 'track-section';
    div.id = sectionId;
    div.innerHTML = `
      <div class="track-section-header">
        <div class="check-all" onclick="toggleAlbumTracks('${esc(album)}')"></div>
        <span>${esc(album)}</span>
      </div>
      <div class="track-header-row">
        <div class="th"></div><div class="th"></div><div class="th">#</div>
        <div class="th">曲目</div><div class="th">专辑</div>
        <div class="th" style="text-align:right;">时长</div>
        <div class="th" style="text-align:center;">格式</div>
        <div class="th" style="text-align:right;">质量</div>
      </div>
      <div id="tl-${eid(album)}"><div class="loading-row">加载中...</div></div>
    `;
    container.appendChild(div);
  }

  try {
    const tracks = await GET(
      `/artists/${encodeURIComponent(artist)}/albums/${encodeURIComponent(album)}/tracks`
    );
    const tlEl = document.getElementById('tl-' + eid(album));
    if (!tlEl) return;

    tlEl.innerHTML = tracks.map(t => {
      const dur = t.duration ? fmtDur(t.duration) : '—';
      const sr = t.sample_rate
        ? (t.sample_rate >= 1000 ? Math.round(t.sample_rate / 1000) + 'kHz' : t.sample_rate + 'Hz')
        : '—';
      const fmt = (t.ext || '').replace('.', '').toUpperCase();
      return `
        <div class="track-row ${selectedTracks.has(t.id) ? 'selected' : ''}" id="tr-${t.id}"
             onclick="showTrackDetail(${t.id})">
          <div class="tc tc-check">
            <div class="track-checkbox" onclick="toggleTrack(event,${t.id})">
              ${selectedTracks.has(t.id) ? '✓' : ''}
            </div>
          </div>
          <div class="tc tc-play">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <polygon points="5 3 19 12 5 21 5 3"/>
            </svg>
          </div>
          <div class="tc tc-num">${t.track_num || '—'}</div>
          <div class="tc tc-title">${esc(t.title || t.filename)}</div>
          <div class="tc tc-album">${esc(t.album || '')}</div>
          <div class="tc tc-time">${dur}</div>
          <div class="tc tc-format ${fmt === 'FLAC' ? 'fmt-flac' : 'fmt-mp3'}">${fmt}</div>
          <div class="tc tc-quality">${sr}</div>
        </div>
      `;
    }).join('') || '<div class="loading-row">暂无曲目</div>';
  } catch (e) {
    const tlEl = document.getElementById('tl-' + eid(album));
    if (tlEl) tlEl.innerHTML = `<div class="loading-row" style="color:var(--red)">加载失败</div>`;
  }
}

/* ═══════════════════════════════════════════════════════════
   SELECTION LOGIC
═══════════════════════════════════════════════════════════ */

/**
 * 切换单张专辑的勾选状态
 * @param {MouseEvent} e
 * @param {string} album
 */
async function toggleAlbum(e, album) {
  e.stopPropagation();
  const wasSelected = selectedAlbums.has(album);

  if (wasSelected) {
    selectedAlbums.delete(album);
  } else {
    selectedAlbums.add(album);
  }

  const card = document.getElementById('alcard-' + eid(album));
  if (card) {
    card.classList.toggle('selected', selectedAlbums.has(album));
    const checkbox = card.querySelector('.album-checkbox');
    if (checkbox) {
      checkbox.textContent = selectedAlbums.has(album) ? '✓' : '';
    }
  }

  const section = document.getElementById('ts-' + eid(album));
  if (section) {
    const checkAll = section.querySelector('.check-all');
    if (checkAll) {
      checkAll.textContent = '✓';
      checkAll.classList.toggle('checked', selectedAlbums.has(album));
    }
  }

  if (wasSelected !== selectedAlbums.has(album)) {
    try {
      const tracks = await GET(
        `/artists/${encodeURIComponent(currentArtist.artist)}/albums/${encodeURIComponent(album)}/tracks`
      );
      if (selectedAlbums.has(album)) {
        tracks.forEach(t => selectedTracks.add(t.id));
      } else {
        tracks.forEach(t => selectedTracks.delete(t.id));
      }

      const tlEl = document.getElementById('tl-' + eid(album));
      if (tlEl) {
        tracks.forEach(t => {
          const row = document.getElementById('tr-' + t.id);
          if (row) {
            row.classList.toggle('selected', selectedTracks.has(t.id));
            const cb = row.querySelector('.track-checkbox');
            if (cb) cb.textContent = selectedTracks.has(t.id) ? '✓' : '';
          }
        });

        const section = document.getElementById('ts-' + eid(album));
        if (section) {
          const checkAll = section.querySelector('.check-all');
          if (checkAll) {
            checkAll.textContent = selectedAlbums.has(album) ? '✓' : '';
            checkAll.classList.toggle('checked', selectedAlbums.has(album));
          }
        }
      }
    } catch (err) {
      console.error('Failed to sync album tracks:', album, err);
    }
  }

  updateToolbar();
}

/**
 * 切换某张专辑下所有曲目的勾选状态
 * @param {string} album
 */
function toggleAlbumTracks(album) {
  const section = document.getElementById('ts-' + eid(album));
  if (!section) return;

  const rows = section.querySelectorAll('.track-row');
  const ids = [...rows].map(r => {
    const id = parseInt(r.id.replace('tr-', ''));
    return isNaN(id) ? null : id;
  }).filter(id => id !== null);

  if (ids.length === 0) return;

  const allSelected = ids.every(id => selectedTracks.has(id));

  if (allSelected) {
    ids.forEach(id => selectedTracks.delete(id));
    selectedAlbums.delete(album);
  } else {
    ids.forEach(id => selectedTracks.add(id));
    selectedAlbums.add(album);
  }

  const checkAll = section.querySelector('.check-all');
  if (checkAll) {
    checkAll.textContent = selectedAlbums.has(album) ? '✓' : '';
    checkAll.classList.toggle('checked', selectedAlbums.has(album));
  }

  rows.forEach(row => {
    const id = parseInt(row.id.replace('tr-', ''));
    if (!isNaN(id)) {
      row.classList.toggle('selected', selectedTracks.has(id));
      const cb = row.querySelector('.track-checkbox');
      if (cb) cb.textContent = selectedTracks.has(id) ? '✓' : '';
    }
  });

  const card = document.getElementById('alcard-' + eid(album));
  if (card) {
    const checkbox = card.querySelector('.album-checkbox');
    if (checkbox) {
      checkbox.textContent = selectedAlbums.has(album) ? '✓' : '';
    }
  }

  updateToolbar();
}

/**
 * 切换单条曲目的勾选状态（不触发整体重渲染）
 * @param {MouseEvent} e
 * @param {number} id
 */
function toggleTrack(e, id) {
  e.stopPropagation();

  const row = document.getElementById('tr-' + id);
  if (!row) return;

  const tl = row.parentNode;
  const section = tl ? tl.parentNode : null;
  if (!section || !section.classList.contains('track-section')) return;

  const album = section.id.replace('ts-', '');

  if (selectedTracks.has(id)) selectedTracks.delete(id);
  else selectedTracks.add(id);

  row.classList.toggle('selected', selectedTracks.has(id));
  const cb = row.querySelector('.track-checkbox');
  if (cb) cb.textContent = selectedTracks.has(id) ? '✓' : '';

  const allRows = section.querySelectorAll('.track-row');
  const allTrackIds = [...allRows].map(r => {
    const trackId = parseInt(r.id.replace('tr-', ''));
    return isNaN(trackId) ? null : trackId;
  }).filter(tId => tId !== null);

  const allSelected = allTrackIds.length > 0 && allTrackIds.every(tid => selectedTracks.has(tid));

  const checkAll = section.querySelector('.check-all');
  if (checkAll) {
    checkAll.textContent = allSelected ? '✓' : '';
    checkAll.classList.toggle('checked', allSelected);
  }

  if (allSelected !== selectedAlbums.has(album)) {
    if (allSelected) selectedAlbums.add(album);
    else selectedAlbums.delete(album);

    const card = document.getElementById('alcard-' + eid(album));
    if (card) {
      const checkbox = card.querySelector('.album-checkbox');
      if (checkbox) {
        checkbox.textContent = selectedAlbums.has(album) ? '✓' : '';
      }
    }
  }

  updateToolbar();
}

/** 全选 / 取消全选当前艺术家所有专辑 */
async function selectAllAlbums() {
  const allSelected = artistAlbums.every(al => selectedAlbums.has(al.album));
  selectedAlbums.clear();
  selectedTracks.clear();

  if (!allSelected) {
    artistAlbums.forEach(al => selectedAlbums.add(al.album));
    for (const al of artistAlbums) {
      try {
        const tracks = await GET(
          `/artists/${encodeURIComponent(currentArtist.artist)}/albums/${encodeURIComponent(al.album)}/tracks`
        );
        tracks.forEach(t => selectedTracks.add(t.id));
      } catch (e) {
        console.error('Failed to load tracks for album:', al.album, e);
      }
    }
  }

  renderArtistView();
}

/**
 * 展开/折叠单张专辑视图
 * @param {string} album
 */
function expandAlbum(album) {
  currentAlbum = currentAlbum === album ? null : album;
  selectedAlbums.clear();
  renderArtistView();
}

/* ═══════════════════════════════════════════════════════════
   TOOLBAR
═══════════════════════════════════════════════════════════ */

/** 根据当前选中状态更新工具栏（全选按钮文字、计数、格式化按钮显隐） */
function updateToolbar() {
  const count = selectedAlbums.size + selectedTracks.size;
  const cntEl = document.getElementById('select-count');
  const fmtBtn = document.getElementById('format-btn');
  const selectAllBtn = document.getElementById('select-all-btn');

  const allSelected = artistAlbums.length > 0 &&
    artistAlbums.every(al => selectedAlbums.has(al.album));
  if (selectAllBtn) {
    selectAllBtn.textContent = allSelected ? '取消全选' : '全选专辑';
  }

  if (count > 0) {
    cntEl.style.display = 'flex';
    const parts = [];
    if (selectedAlbums.size > 0) parts.push(`${selectedAlbums.size} 张专辑`);
    if (selectedTracks.size > 0) parts.push(`${selectedTracks.size} 首曲目`);
    cntEl.textContent = parts.join(' ') + '已选';
    fmtBtn.style.display = 'flex';
  } else {
    cntEl.style.display = 'none';
    fmtBtn.style.display = 'none';
  }
}
