/**
 * artist.js — 艺术家视图模块
 * 包含：侧边栏艺术家树、专辑网格、曲目列表、工具栏选中状态。
 * 依赖：api.js、state.js、utils.js、ui.js
 */

/* ═══════════════════════════════════════════════════════════
   DOWNLOAD HELPERS
═══════════════════════════════════════════════════════════ */

function downloadFile(url, filename) {
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
}

async function downloadTrack(trackId, filename, ext) {
  try {
    streamDownloadGet(`/tracks/${trackId}/download`);
  } catch (e) {
    showToast(`下载失败: ${e.message}`, 'error');
  }
}

async function downloadAlbum(albumId) {
  try {
    streamDownloadGet(`/albums/${albumId}/download`);
  } catch (e) {
    showToast(`下载失败: ${e.message}`, 'error');
  }
}

async function downloadArtist(artistId) {
  try {
    streamDownloadGet(`/artists/${artistId}/download`);
  } catch (e) {
    showToast(`下载失败: ${e.message}`, 'error');
  }
}

async function downloadSelectedTracks() {
  if (selectedTracks.size === 0) {
    showToast('请先选择要下载的歌曲', 'warn');
    return;
  }
  try {
    await streamDownloadPost('/tracks/download-batch', { track_ids: [...selectedTracks] });
  } catch (e) {
    showToast(`下载失败: ${e.message}`, 'error');
  }
}

function downloadCurrentArtist() {
  if (!currentArtist) {
    showToast('请先选择一个艺术家', 'warn');
    return;
  }
  downloadArtist(currentArtist.id);
}

/* ═══════════════════════════════════════════════════════════
   ARTIST CACHE（浏览器缓存）
═══════════════════════════════════════════════════════════ */

/** 艺术家缓存的 localStorage 键名 */
const ARTIST_CACHE_KEY = 'tunetree_artist_cache';

/** 最大缓存艺术家数量 */
const MAX_CACHE_SIZE = 20;

/** 缓存过期时间（小时） */
const CACHE_EXPIRE_HOURS = 6;

/**
 * 获取艺术家缓存
 * @returns {Object} 缓存对象，key为艺术家名，value为完整信息
 */
function getArtistCache() {
  try {
    const data = localStorage.getItem(ARTIST_CACHE_KEY);
    return data ? JSON.parse(data) : { items: [], cache: {} };
  } catch (e) {
    console.error('Failed to read artist cache:', e);
    return { items: [], cache: {} };
  }
}

/**
 * 保存艺术家缓存
 * @param {Object} cacheData - 缓存数据对象
 */
function saveArtistCache(cacheData) {
  try {
    localStorage.setItem(ARTIST_CACHE_KEY, JSON.stringify(cacheData));
  } catch (e) {
    console.error('Failed to save artist cache:', e);
  }
}

/**
 * 检查缓存是否过期
 * @param {number} cachedAt - 缓存时间戳
 * @returns {boolean} true表示已过期
 */
function isCacheExpired(cachedAt) {
  if (!cachedAt) return true;
  const now = Date.now();
  const expireMs = CACHE_EXPIRE_HOURS * 60 * 60 * 1000;
  return (now - cachedAt) > expireMs;
}

/**
 * 从缓存中获取艺术家数据（检查过期时间）
 * @param {number} artistId - 艺术家 ID
 * @returns {Object|null} 艺术家完整信息，如果不存在或已过期返回null
 */
function getArtistFromCache(artistId) {
  const cache = getArtistCache();
  const cachedData = cache.cache[artistId];
  if (!cachedData) return null;

  if (isCacheExpired(cachedData._cachedAt)) {
    console.debug(`Artist ${artistId} cache expired (${CACHE_EXPIRE_HOURS} hours)`);
    return null;
  }

  const { _cachedAt, ...data } = cachedData;
  return data;
}

/**
 * 将艺术家数据存入缓存（FIFO策略），并记录缓存时间
 * @param {number} artistId - 艺术家 ID
 * @param {Object} data - 艺术家完整信息
 */
function setArtistToCache(artistId, data) {
  const cache = getArtistCache();

  if (cache.cache[artistId]) {
    const index = cache.items.indexOf(artistId);
    if (index > -1) {
      cache.items.splice(index, 1);
    }
  }

  cache.items.push(artistId);
  cache.cache[artistId] = {
    ...data,
    _cachedAt: Date.now()
  };

  while (cache.items.length > MAX_CACHE_SIZE) {
    const oldestArtist = cache.items.shift();
    delete cache.cache[oldestArtist];
  }

  saveArtistCache(cache);
}

/**
 * 清除艺术家缓存
 */
function clearArtistCache() {
  try {
    localStorage.removeItem(ARTIST_CACHE_KEY);
  } catch (e) {
    console.error('Failed to clear artist cache:', e);
  }
}

/**
 * 清除指定艺术家列表的缓存
 * @param {number[]} artistIds - 要清除缓存的艺术家 ID 数组
 */
function clearArtistsFromCache(artistIds) {
  if (!artistIds || artistIds.length === 0) return;

  const cache = getArtistCache();
  let cleared = 0;

  artistIds.forEach(artistId => {
    if (cache.cache[artistId]) {
      delete cache.cache[artistId];
      const index = cache.items.indexOf(artistId);
      if (index > -1) {
        cache.items.splice(index, 1);
      }
      cleared++;
    }
  });

  if (cleared > 0) {
    saveArtistCache(cache);
    console.debug(`Cleared cache for ${cleared} artists:`, artistIds);
  }
}

/**
 * 获取缓存统计信息
 * @returns {Object} { count: 当前缓存数量, max: 最大缓存数量 }
 */
function getArtistCacheStats() {
  const cache = getArtistCache();
  return { count: cache.items.length, max: MAX_CACHE_SIZE };
}

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
 * 刷新艺术家列表，保持当前排序方式
 */
async function refreshArtistList() {
  const searchInput = document.getElementById('artist-search');
  const refreshBtn = document.getElementById('refresh-artist-btn');

  // 添加加载状态
  refreshBtn.classList.add('spin');

  try {
    await loadArtistTree(searchInput.value);
    showToast('艺术家列表已刷新', 'success');
  } catch (e) {
    showToast('刷新失败: ' + e.message, 'error');
  } finally {
    // 移除加载状态
    refreshBtn.classList.remove('spin');
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
      a.name.toLowerCase().includes(q.toLowerCase())
    );
  }
  renderArtistTree(getSortedArtists(toRender));
}

/* ═══════════════════════════════════════════════════════════
   ARTIST SORTING
═══════════════════════════════════════════════════════════ */

let currentArtistSort = { type: 'date', order: 'desc' };

function setArtistSort(sortType) {
  const prevType = currentArtistSort.type;

  if (prevType === sortType) {
    currentArtistSort.order = currentArtistSort.order === 'desc' ? 'asc' : 'desc';
  } else {
    currentArtistSort.type = sortType;
    currentArtistSort.order = sortType === 'count' ? 'desc' : 'asc';
  }

  updateSortButtons();
  const searchInput = document.getElementById('artist-search');
  filterArtists(searchInput.value);
}

function updateSortButtons() {
  const buttons = ['sort-artist-date', 'sort-artist-count', 'sort-artist-name'];
  const types = ['date', 'count', 'name'];

  buttons.forEach((btnId, index) => {
    const btn = document.getElementById(btnId);
    if (btn) {
      const isActive = currentArtistSort.type === types[index];
      btn.classList.toggle('active', isActive);
      btn.textContent = getSortButtonText(types[index], isActive);
    }
  });
}

function getSortButtonText(type, isActive) {
  const orderIcon = isActive ? (currentArtistSort.order === 'desc' ? '↓' : '↑') : '';
  switch (type) {
    case 'count': return `歌曲数${orderIcon}`;
    case 'name': return `名称${orderIcon}`;
    case 'date': return `创建时间${orderIcon}`;
    default: return type;
  }
}

function getSortedArtists(artists) {
  const sorted = [...artists];
  const { type, order } = currentArtistSort;
  const multiplier = order === 'desc' ? 1 : -1;

  switch (type) {
    case 'count':
      sorted.sort((a, b) => (b.track_count - a.track_count) * multiplier);
      break;
    case 'date':
      sorted.sort((a, b) => ((b.last_created_at || 0) - (a.last_created_at || 0)) * multiplier);
      break;
    case 'name':
    default:
      sorted.sort((a, b) => (a.name || '').localeCompare(b.name || '', undefined, { sensitivity: 'base', numeric: true }) * multiplier);
  }
  return sorted;
}

/** 最大可选艺术家数量 */
const MAX_SELECTED_ARTISTS = 50;

/** 艺术家勾选模式开关 */
let artistSelectionEnabled = false;

/** 范围选择的起始艺术家 */
let rangeSelectStartArtist = null;

/** 是否隐藏已整理的艺术家 */
let hideOrganizedArtists = false;

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
  tree.innerHTML = artists.map(a => {
    if (hideOrganizedArtists && a.all_organized) return '';

    const isSelected = selectedArtists.has(a.id);
    const showCheckbox = artistSelectionEnabled && isSelected;

    const taskStatus = (typeof AsyncFormat !== 'undefined') ? AsyncFormat.getArtistTaskStatus(a.id) : '';
    const statusClass = taskStatus ? ` task-status-${taskStatus.replace(' ', '-')}` : '';
    const statusTitle = taskStatus ? ` title="任务状态：${taskStatus}"` : '';

    return `
      <div class="tree-artist ${isSelected ? 'selected' : ''}${statusClass}" id="ta-${a.id}">
        <div class="tree-artist-header" id="tah-${a.id}" data-artist-id="${a.id}"${statusTitle}>
          ${artistSelectionEnabled ? `
          <div class="tree-checkbox" onclick="toggleArtistSelection(event, ${a.id})">
            ${showCheckbox ? '✓' : ''}
          </div>
          ` : ''}
          <div class="tree-chevron" id="tch-${a.id}" onclick="toggleArtistExpand(${a.id})">▶</div>
          <div class="tree-artist-name" onclick="selectArtistFromId(${a.id})">${esc(a.name)}${taskStatus ? `<span class="artist-task-status">${esc(taskStatus)}</span>` : ''}</div>
          ${a.all_organized ? '<div class="tree-organized" title="已整理"></div>' : ''}
          <div class="tree-badge">${a.track_count}</div>
        </div>
        <div class="tree-albums" id="talb-${a.id}">
          <div class="loading-row" style="font-size:10px;">点击箭头展开...</div>
        </div>
      </div>
    `;
  }).filter(html => html !== '').join('');
}

/**
 * 切换艺术家的勾选状态
 * @param {MouseEvent} e
 * @param {string} artist
 */
function toggleArtistSelection(e, artistId) {
  e.stopPropagation();

  if (typeof AsyncFormat !== 'undefined' && !AsyncFormat.canSelectArtist(artistId)) {
    const status = AsyncFormat.getArtistTaskStatus(artistId);
    showToast(`艺术家已有任务正在执行：${status}`, 'warn');
    return;
  }

  const wasSelected = selectedArtists.has(artistId);

  if (wasSelected) {
    selectedArtists.delete(artistId);
  } else {
    if (selectedArtists.size >= MAX_SELECTED_ARTISTS) {
      showToast(`最多只能选择 ${MAX_SELECTED_ARTISTS} 个艺术家`, 'warn');
      return;
    }
    selectedArtists.add(artistId);
  }

  const treeArtist = document.getElementById('ta-' + artistId);
  if (treeArtist) {
    treeArtist.classList.toggle('selected', selectedArtists.has(artistId));
    const checkbox = treeArtist.querySelector('.tree-checkbox');
    if (checkbox) {
      checkbox.textContent = selectedArtists.has(artistId) ? '✓' : '';
    }
  }

  const rangeBtn = document.getElementById('range-select-btn');
  if (rangeBtn && rangeBtn.classList.contains('active')) {
    if (!wasSelected) {
      selectArtistRange(artistId);
    }
  }

  updateToolbar();
}

/** 清除所有选中的艺术家 */
function clearSelectedArtists() {
  selectedArtists.clear();
  document.querySelectorAll('.tree-artist').forEach(el => {
    el.classList.remove('selected');
    const checkbox = el.querySelector('.tree-checkbox');
    if (checkbox) checkbox.textContent = '';
  });
  rangeSelectStartArtist = null;
  const rangeBtn = document.getElementById('range-select-btn');
  if (rangeBtn) {
    rangeBtn.classList.remove('active');
    rangeBtn.title = '';
  }
  updateToolbar();
}

/** 切换隐藏已整理艺术家 */
function toggleHideOrganized() {
  hideOrganizedArtists = !hideOrganizedArtists;
  const btn = document.getElementById('hide-organized-toggle');
  const textSpan = btn.querySelector('span');

  if (btn && textSpan) {
    btn.classList.toggle('active', hideOrganizedArtists);
    textSpan.textContent = hideOrganizedArtists ? '显示已整理' : '隐藏已整理';
  }

  loadArtistTree(document.getElementById('artist-search').value);
  updateToolbar();
}

/** 切换艺术家勾选模式 */
function toggleArtistSelectionMode() {
  updateToolbar();
}

/** 切换艺术家勾选模式 */
function toggleArtistSelectionMode() {
  artistSelectionEnabled = !artistSelectionEnabled;
  const btn = document.getElementById('artist-select-toggle');
  const textSpan = document.getElementById('artist-select-text');
  const rangeBtn = document.getElementById('range-select-btn');
  if (btn && textSpan) {
    btn.classList.toggle('active', artistSelectionEnabled);
    textSpan.textContent = artistSelectionEnabled ? '取消勾选' : '勾选艺术家';
  }
  if (rangeBtn) {
    rangeBtn.style.display = artistSelectionEnabled ? 'flex' : 'none';
    rangeBtn.classList.remove('active');
    rangeBtn.title = '';
  }
  if (!artistSelectionEnabled) {
    clearSelectedArtists();
    rangeSelectStartArtist = null;
  }
  loadArtistTree(document.getElementById('artist-search').value);
  updateToolbar();
}

/** 切换隐藏已整理艺术家 */
function toggleHideOrganized() {
  hideOrganizedArtists = !hideOrganizedArtists;
  const btn = document.getElementById('hide-organized-toggle');
  const textSpan = btn.querySelector('span');

  if (btn && textSpan) {
    btn.classList.toggle('active', hideOrganizedArtists);
    textSpan.textContent = hideOrganizedArtists ? '显示已整理' : '隐藏已整理';
  }

  loadArtistTree(document.getElementById('artist-search').value);
  updateToolbar();
}

/** 开始/取消范围选择 */
function toggleRangeSelectMode() {
  const rangeBtn = document.getElementById('range-select-btn');

  if (rangeSelectStartArtist) {
    rangeSelectStartArtist = null;
    rangeBtn.classList.remove('active');
    rangeBtn.title = '';
    showToast('已取消范围选择', 'info');
  } else {
    const searchInput = document.getElementById('artist-search');
    const currentArtists = getSortedArtists(allArtists.filter(a =>
      !searchInput.value || a.name.toLowerCase().includes(searchInput.value.toLowerCase())
    ));

    if (currentArtists.length === 0) {
      showToast('当前列表无艺术家', 'warn');
      return;
    }

    rangeBtn.classList.add('active');
    showToast('现在点击第一个艺术家作为范围起点', 'info');
  }
}

/** 选择艺术家范围（从起始艺术家到当前艺术家） */
function selectArtistRange(artistId) {
  if (!artistSelectionEnabled) {
    showToast('请先开启勾选艺术家模式', 'warn');
    return;
  }

  const searchInput = document.getElementById('artist-search');
  const currentArtists = getSortedArtists(allArtists.filter(a =>
    !searchInput.value || a.name.toLowerCase().includes(searchInput.value.toLowerCase())
  ));

  if (currentArtists.length === 0) {
    showToast('当前列表无艺术家', 'warn');
    return;
  }

  if (!rangeSelectStartArtist) {
    rangeSelectStartArtist = artistId;
    const rangeBtn = document.getElementById('range-select-btn');
    if (rangeBtn) {
      rangeBtn.classList.add('active');
      const startArtist = allArtists.find(a => a.id === artistId);
      rangeBtn.title = `起点：${startArtist ? startArtist.name : artistId}`;
    }
    showToast(`已设置范围起点，现在点击另一个艺术家作为终点`, 'info');
    return;
  }

  const startIndex = currentArtists.findIndex(a => a.id === rangeSelectStartArtist);
  const endIndex = currentArtists.findIndex(a => a.id === artistId);

  if (startIndex === -1 || endIndex === -1) {
    showToast('无法确定范围（艺术家可能已被过滤）', 'warn');
    rangeSelectStartArtist = null;
    const rangeBtn = document.getElementById('range-select-btn');
    if (rangeBtn) {
      rangeBtn.classList.remove('active');
      rangeBtn.title = '';
    }
    return;
  }

  const minIndex = Math.min(startIndex, endIndex);
  const maxIndex = Math.max(startIndex, endIndex);

  let count = 0;
  let skippedCount = 0;
  for (let i = minIndex; i <= maxIndex; i++) {
    const artistData = currentArtists[i];
    if (hideOrganizedArtists && artistData.all_organized) {
      skippedCount++;
      continue;
    }
    const aid = artistData.id;
    if (!selectedArtists.has(aid)) {
      if (selectedArtists.size >= MAX_SELECTED_ARTISTS) {
        showToast(`最多只能选择 ${MAX_SELECTED_ARTISTS} 个艺术家，已选择 ${selectedArtists.size} 个`, 'warn');
        break;
      }
      selectedArtists.add(aid);
      count++;
    }
  }

  const startName = currentArtists[minIndex].name;
  const endName = currentArtists[maxIndex].name;
  rangeSelectStartArtist = null;

  const rangeBtn = document.getElementById('range-select-btn');
  if (rangeBtn) {
    rangeBtn.classList.remove('active');
    rangeBtn.title = '';
  }

  loadArtistTree(searchInput.value);
  updateToolbar();

  let msg = `已选择 ${startName} 到 ${endName} 之间的 ${count} 个艺术家`;
  if (skippedCount > 0) {
    msg += `（跳过 ${skippedCount} 个已整理）`;
  }
  showToast(msg, 'success');
}

/**
 * 点击箭头：仅展开/收起专辑列表，不触发右侧视图
 * @param {string} artist
 */
async function toggleArtistExpand(artistId) {
  const albums = document.getElementById('talb-' + artistId);
  const chevron = document.getElementById('tch-' + artistId);
  const isOpen = albums.classList.contains('open');

  document.querySelectorAll('.tree-artist-header').forEach(h => h.classList.remove('active'));
  document.querySelectorAll('.tree-albums').forEach(a => a.classList.remove('open'));

  if (!isOpen) {
    albums.classList.add('open');
    chevron.textContent = '▼';

    if (!albums.querySelector('.tree-album')) {
      await loadArtistAlbumsOnly(artistId, albums);
    }
  } else {
    albums.classList.remove('open');
    chevron.textContent = '▶';
  }
}

/**
 * 点击艺术家名字：触发右侧视图展示
 * @param {string} artist
 */
async function selectArtistFromId(artistId) {
  const albums = document.getElementById('talb-' + artistId);
  await selectArtist(artistId, albums);
}

/**
 * 仅加载艺术家的专辑数据到侧边栏，不触发右侧视图
 * @param {string} artist
 * @param {HTMLElement} albumsEl — 侧边栏专辑容器
 */
async function loadArtistAlbumsOnly(artistId, albumsEl) {
  try {
    const artistAlbumsTemp = await GET(`/artists/${artistId}/albums`);

    albumsEl.innerHTML = artistAlbumsTemp.map(al => `
      <div class="tree-album" id="talbcard-${al.id}"
           onclick="selectAlbumFromTree(${artistId}, ${al.id})">
        <div class="tree-album-name">${esc(al.title)}</div>
        ${al.all_organized ? '<div style="font-size:8px;color:var(--accent)">●</div>' : ''}
        <div class="tree-album-count">${al.track_count}</div>
      </div>
    `).join('');
  } catch (e) {
    albumsEl.innerHTML = '<div class="loading-row" style="color:var(--red)">加载失败</div>';
  }
}

/** 缓存艺术家的所有歌曲数据 */
let artistTracksCache = {};

/**
 * 选中艺术家，加载专辑列表和所有歌曲，渲染主视图
 * 优先从localStorage缓存获取，缓存不存在时才请求API
 * @param {string} artist
 * @param {HTMLElement|null} albumsEl — 侧边栏专辑容器（null 表示仅刷新主视图）
 */
async function selectArtist(artistId, albumsEl) {
  currentArtist = allArtists.find(a => a.id === artistId);
  currentAlbum = null;
  selectedAlbums.clear();
  selectedTracks.clear();

  try {
    let fullInfo = getArtistFromCache(artistId);
    let fromCache = !!fullInfo;

    if (!fullInfo) {
      fullInfo = await GET(`/artists/${artistId}/full`);
      setArtistToCache(artistId, fullInfo);
    }

    artistAlbums = fullInfo.albums;

    artistTracksCache = {};
    fullInfo.albums.forEach(al => {
      artistTracksCache[al.id] = al.tracks;
    });

    if (albumsEl) {
      albumsEl.innerHTML = artistAlbums.map(al => `
        <div class="tree-album" id="talbcard-${al.id}"
             onclick="selectAlbumFromTree(${artistId}, ${al.id})">
          <div class="tree-album-name">${esc(al.title)}</div>
          ${al.all_organized ? '<div style="font-size:8px;color:var(--accent)">●</div>' : ''}
          <div class="tree-album-count">${al.track_count}</div>
        </div>
      `).join('');
    }

    renderArtistView();
    switchPage('artist');

    if (fromCache) {
      console.debug(`Artist ${artistId} loaded from cache`);
    } else {
      console.debug(`Artist ${artistId} loaded from API`);
    }
  } catch (e) {
    showToast('加载失败: ' + e.message, 'error');
  }
}

/**
 * 从侧边栏专辑行选中单张专辑
 * 如果当前艺术家不是目标艺术家，先加载艺术家数据
 * @param {string} artist
 * @param {string} album
 */
async function selectAlbumFromTree(artistId, albumId) {
  if (!currentArtist || currentArtist.id !== artistId) {
    await selectArtist(artistId, null);
  }

  document.querySelectorAll('.tree-album').forEach(el => el.classList.remove('active'));
  const card = document.getElementById('talbcard-' + albumId);
  if (card) card.classList.add('active');
  currentAlbum = albumId;
  selectedAlbums.clear();
  selectedTracks.clear();
  renderArtistView();
  switchPage('artist');
}

/* ═══════════════════════════════════════════════════════════
   ARTIST VIEW（主区域）
═══════════════════════════════════════════════════════════ */

/** 艺术家封面缓存 */
let artistCoverCache = {};

/** 专辑封面缓存 */
let albumCoverCache = {};

function artistCoverUrl(artistId) {
  const bust = artistCoverCache[artistId] ? `&_t=${Date.now()}` : '';
  return `/api/artists/${artistId}/cover?token=${TOKEN}${bust}`;
}

function albumCoverUrl(albumId) {
  const bust = albumCoverCache[albumId] ? `&_t=${Date.now()}` : '';
  return `/api/albums/${albumId}/cover?token=${TOKEN}${bust}`;
}

async function scrapeArtistCover(artistId) {
  try {
    showToast('正在从网易云获取歌手头像...', 'info');
    const result = await POST(`/artists/${artistId}/scrape-cover`, {});

    if (result.error) {
      showToast('获取失败: ' + result.error, 'error');
      return;
    }

    artistCoverCache[artistId] = true;
    await loadArtistCover(artistId);
    showToast('歌手头像获取成功', 'success');
  } catch (err) {
    showToast('获取失败: ' + err.message, 'error');
  }
}

async function deleteArtistCover(artistId) {
  try {
    showToast('正在删除歌手头像...', 'info');
    const result = await DELETE(`/artists/${artistId}/cover`);

    if (result.error) {
      showToast('删除失败: ' + result.error, 'error');
      return;
    }

    delete artistCoverCache[artistId];
    await loadArtistCover(artistId);
    showToast('歌手头像已删除', 'success');
  } catch (err) {
    showToast('删除失败: ' + err.message, 'error');
  }
}

async function loadArtistCover(artistId) {
  const coverEl = document.getElementById('artist-cover-img');
  if (!coverEl) return;

  coverEl.className = 'artist-cover';
  coverEl.onclick = null;
  coverEl.innerHTML = `<i class="bi bi-person" style="font-size: 36px;"></i>`;

  try {
    const exists = await GET(`/artists/${artistId}/cover/exists`);
    if (exists.exists) {
      const img = document.createElement('img');
      img.className = 'artist-cover-image';
      img.src = artistCoverUrl(artistId);
      img.alt = currentArtist ? currentArtist.name : '';
      img.style.cursor = 'zoom-in';
      img.onclick = (e) => {
        e.stopPropagation();
        const safeName = (currentArtist ? currentArtist.name : 'unknown').replace(/[\\/:*?"<>|]/g, '_');
        openCoverImageViewer(img.src, safeName);
      };
      img.onerror = () => {
        coverEl.innerHTML = `<i class="bi bi-person" style="font-size: 36px;"></i>`;
        coverEl.onclick = () => uploadArtistCover(artistId);
      };

      const actionsDiv = document.createElement('div');
      actionsDiv.className = 'artist-cover-actions';
      actionsDiv.innerHTML = `
        <button class="artist-cover-btn" title="从本地上传">
          <i class="bi bi-upload"></i>
        </button>
        <button class="artist-cover-btn" id="scrape-cover-btn" title="从网易云获取">
          <i class="bi bi-cloud-download"></i>
        </button>
        <button class="artist-cover-btn" id="delete-cover-btn" title="删除头像">
          <i class="bi bi-trash"></i>
        </button>
      `;
      actionsDiv.querySelectorAll('button')[0].onclick = (e) => {
        e.stopPropagation();
        uploadArtistCover(artistId);
      };
      actionsDiv.querySelector('#scrape-cover-btn').onclick = (e) => {
        e.stopPropagation();
        scrapeArtistCover(artistId);
      };
      actionsDiv.querySelector('#delete-cover-btn').onclick = (e) => {
        e.stopPropagation();
        deleteArtistCover(artistId);
      };

      coverEl.innerHTML = '';
      coverEl.appendChild(img);
      coverEl.appendChild(actionsDiv);
    } else {
      const actionsDiv = document.createElement('div');
      actionsDiv.className = 'artist-cover-actions';
      actionsDiv.innerHTML = `
        <button class="artist-cover-btn" id="upload-cover-btn" title="从本地上传">
          <i class="bi bi-upload"></i>
        </button>
        <button class="artist-cover-btn" id="scrape-cover-btn" title="从网易云获取">
          <i class="bi bi-cloud-download"></i>
        </button>
      `;
      actionsDiv.querySelector('#upload-cover-btn').onclick = (e) => {
        e.stopPropagation();
        uploadArtistCover(artistId);
      };
      actionsDiv.querySelector('#scrape-cover-btn').onclick = (e) => {
        e.stopPropagation();
        scrapeArtistCover(artistId);
      };
      coverEl.appendChild(actionsDiv);
    }
  } catch (e) {
    const actionsDiv = document.createElement('div');
    actionsDiv.className = 'artist-cover-actions';
    actionsDiv.innerHTML = `
      <button class="artist-cover-btn" id="upload-cover-btn" title="从本地上传">
        <i class="bi bi-upload"></i>
      </button>
    `;
    actionsDiv.querySelector('#upload-cover-btn').onclick = (e) => {
      e.stopPropagation();
      uploadArtistCover(artistId);
    };
    coverEl.appendChild(actionsDiv);
  }
}

async function loadAlbumCovers(albums) {
  for (const al of albums) {
    const wrap = document.getElementById('alcw-' + al.id);
    if (!wrap) continue;

    try {
      const exists = await GET(`/albums/${al.id}/cover/exists`);
      if (exists.exists) {
        const img = document.createElement('img');
        img.className = 'album-cover-img';
        img.src = albumCoverUrl(al.id);
        img.alt = al.title || '';
        img.loading = 'lazy';
        img.style.cursor = 'zoom-in';
        img.onclick = (e) => {
          e.stopPropagation();
          const artistName = currentArtist ? currentArtist.name : 'unknown';
          const albumTitle = al.title || 'unknown';
          const safeName = `${artistName} - ${albumTitle}`.replace(/[\\/:*?"<>|]/g, '_');
          openCoverImageViewer(img.src, safeName);
        };
        img.onerror = () => {
          img.style.display = 'none';
          const ph = wrap.querySelector('.album-cover-placeholder');
          if (ph) ph.style.display = '';
          const actions = wrap.querySelector('.album-cover-actions');
          if (actions) actions.remove();
        };
        img.onload = () => {
          const ph = wrap.querySelector('.album-cover-placeholder');
          if (ph) ph.style.display = 'none';
        };
        wrap.insertBefore(img, wrap.firstChild);

        const actionsDiv = document.createElement('div');
        actionsDiv.className = 'album-cover-actions';
        actionsDiv.innerHTML = `
          <button class="album-cover-btn" title="上传封面">
            <i class="bi bi-upload"></i>
          </button>
        `;
        actionsDiv.querySelector('button').onclick = (e) => {
          e.stopPropagation();
          uploadAlbumCover(al.id);
        };
        wrap.appendChild(actionsDiv);
      } else {
        const actionsDiv = document.createElement('div');
        actionsDiv.className = 'album-cover-actions';
        actionsDiv.innerHTML = `
          <button class="album-cover-btn" title="上传封面">
            <i class="bi bi-upload"></i>
          </button>
        `;
        actionsDiv.querySelector('button').onclick = (e) => {
          e.stopPropagation();
          uploadAlbumCover(al.id);
        };
        wrap.appendChild(actionsDiv);
      }
    } catch (e) {
      // ignore — placeholder stays
    }
  }
}

function uploadAlbumCover(albumId) {
  const input = document.createElement('input');
  input.type = 'file';
  input.accept = 'image/jpeg,image/png';
  input.onchange = async (e) => {
    const file = e.target.files[0];
    if (!file) return;

    if (file.size > 5 * 1024 * 1024) {
      showToast('图片文件过大，最大支持 5MB', 'error');
      return;
    }

    const formData = new FormData();
    formData.append('cover', file);

    try {
      showToast('正在上传专辑封面...', 'info');
      const result = await apiUpload(`/albums/${albumId}/cover`, formData);

      if (result.ok) {
        showToast('专辑封面上传成功', 'success');
        albumCoverCache[albumId] = true;
        const wrap = document.getElementById('alcw-' + albumId);
        if (wrap) {
          const existingImg = wrap.querySelector('.album-cover-img');
          if (existingImg) {
            existingImg.src = albumCoverUrl(albumId);
          } else {
            const img = document.createElement('img');
            img.className = 'album-cover-img';
            img.src = albumCoverUrl(albumId);
            img.alt = '';
            img.loading = 'lazy';
            img.style.cursor = 'zoom-in';
            const al = artistAlbums.find(a => a.id === albumId);
            img.onclick = (ev) => {
              ev.stopPropagation();
              const artistName = currentArtist ? currentArtist.name : 'unknown';
              const albumTitle = al ? al.title : 'unknown';
              const safeName = `${artistName} - ${albumTitle}`.replace(/[\\/:*?"<>|]/g, '_');
              openCoverImageViewer(img.src, safeName);
            };
            img.onload = () => {
              const ph = wrap.querySelector('.album-cover-placeholder');
              if (ph) ph.style.display = 'none';
            };
            wrap.insertBefore(img, wrap.firstChild);
          }
        }
      } else {
        showToast('上传失败: ' + (result.error || '未知错误'), 'error');
      }
    } catch (err) {
      showToast('上传失败: ' + err.message, 'error');
    }
  };
  input.click();
}

/**
 * 上传艺术家封面
 * @param {number} artistId
 */
function uploadArtistCover(artistId) {
  const input = document.createElement('input');
  input.type = 'file';
  input.accept = 'image/jpeg,image/png';
  input.onchange = async (e) => {
    const file = e.target.files[0];
    if (!file) return;

    if (file.size > 5 * 1024 * 1024) {
      showToast('图片文件过大，最大支持 5MB', 'error');
      return;
    }

    const formData = new FormData();
    formData.append('cover', file);

    try {
      showToast('正在上传艺术家封面...', 'info');
      const result = await apiUpload(`/artists/${artistId}/cover`, formData);

      if (result.ok) {
        showToast('艺术家封面上传成功', 'success');
        artistCoverCache[artistId] = true;
        await loadArtistCover(artistId);
      } else {
        showToast('上传失败: ' + (result.error || '未知错误'), 'error');
      }
    } catch (err) {
      showToast('上传失败: ' + err.message, 'error');
    }
  };
  input.click();
}

/** 渲染艺术家主视图（专辑网格 + 曲目列表） */
function renderArtistView() {
  if (!currentArtist) return;
  const view = document.getElementById('artist-view');
  const a = currentArtist;

  const currentAlbumObj = currentAlbum ? artistAlbums.find(al => al.id === currentAlbum) : null;

  let bc = `<span>${esc(a.name)}</span>`;
  if (currentAlbumObj) bc += `<span class="sep">/</span><span>${esc(currentAlbumObj.title)}</span>`;
  document.getElementById('breadcrumb').innerHTML = bc;

  const albumsToShow = currentAlbum
    ? artistAlbums.filter(al => al.id === currentAlbum)
    : artistAlbums;

  const totalTracks = artistAlbums.reduce((s, al) => s + al.track_count, 0);
  const orgAlbums = artistAlbums.filter(al => al.all_organized).length;

  view.innerHTML = `
    <div class="artist-header">
      <div class="artist-cover" id="artist-cover-img">
        <i class="bi bi-person" style="font-size: 36px;"></i>
      </div>
      <div class="artist-info-block">
          <div class="artist-title">${esc(a.name)}</div>
          <div class="artist-meta">
            <span>${artistAlbums.length} 张专辑</span>
            <span>${totalTracks} 首曲目</span>
            <span>${orgAlbums} 已整理</span>
            ${a.all_organized ? '<span class="organized-badge">● 已整理</span>' : ''}
          </div>
          <div class="artist-last-added">
            最后添加于 ${a.last_created_at ? formatDateTime(a.last_created_at) : '—'}
          </div>
        </div>
    </div>

    <div class="albums-grid">
      ${albumsToShow.map(al => `
        <div class="album-card ${selectedAlbums.has(al.id) ? 'selected' : ''}" id="alcard-${al.id}">
          <div class="album-cover-wrap" id="alcw-${al.id}">
            <div class="album-cover-placeholder">
              <i class="bi bi-disc" style="font-size: 40px;"></i>
            </div>
            <div class="album-select-overlay">
              <div class="album-checkbox" onclick="toggleAlbum(event, ${al.id})">
                ${selectedAlbums.has(al.id) ? '✓' : ''}
              </div>
            </div>

          </div>
          <div class="album-info" onclick="expandAlbum(${al.id})">
            <div class="album-name">${esc(al.title)}${al.all_organized ? '<span class="album-organized">●</span>' : ''}</div>
            <div class="album-year">${al.year || '—'} · ${al.track_count} 曲</div>
          </div>
        </div>
      `).join('')}
    </div>

    <div id="track-sections">
      ${albumsToShow.map(al => renderTrackSection(al)).join('')}
    </div>
  `;

  loadArtistCover(a.id);
  loadAlbumCovers(albumsToShow);
  updateToolbar();
}

/* ═══════════════════════════════════════════════════════════
   TRACK SECTION
═══════════════════════════════════════════════════════════ */

/**
 * 渲染单张专辑的曲目列表（从缓存中获取数据）
 * @param {object} album — 包含 tracks 数组的专辑对象
 */
function renderTrackSection(album) {
  const tracks = album.tracks || [];
  const albumId = album.id;

  return `
    <div class="track-section" id="ts-${albumId}">
      <div class="track-section-header">
        <div class="check-all ${selectedAlbums.has(albumId) ? 'checked' : ''}" onclick="toggleAlbumTracks(${albumId})">
          ${selectedAlbums.has(albumId) ? '✓' : ''}
        </div>
        <span>${esc(album.title)}</span>
        <button class="ts-download-btn" onclick="downloadAlbum(${albumId})" title="下载专辑">
          <i class="bi bi-download"></i>
        </button>
      </div>
      <div class="track-header-row">
        <div class="th"></div><div class="th"></div><div class="th">#</div>
        <div class="th">曲目</div><div class="th">专辑</div>
        <div class="th" style="text-align:right;">时长</div>
        <div class="th" style="text-align:center;">格式</div>
        <div class="th" style="text-align:right;">质量</div>
        <div class="th" style="text-align:right;">添加时间</div>
        <div class="th"></div>
      </div>
      <div id="tl-${albumId}">
        ${tracks.map(t => {
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
                <i class="bi bi-play-fill"></i>
              </div>
              <div class="tc tc-num">${t.track_num || '—'}</div>
              <div class="tc tc-title">${esc(t.title || t.filename)}</div>
              <div class="tc tc-album">${esc(t.album || '')}</div>
              <div class="tc tc-time">${dur}</div>
              <div class="tc tc-format ${fmt === 'FLAC' ? 'fmt-flac' : 'fmt-mp3'}">${fmt}</div>
              <div class="tc tc-quality">${sr}</div>
              <div class="tc tc-ctime">${t.ctime ? formatDateTime(t.ctime) : '—'}</div>
              <div class="tc tc-download" onclick="event.stopPropagation();downloadTrack(${t.id}, '${escJs((t.artist || 'unknown') + ' - ' + (t.title || 'unknown'))}', '${escJs(t.ext || '')}')">
                <i class="bi bi-download"></i>
              </div>
            </div>
          `;
  }).join('') || '<div class="loading-row">暂无曲目</div>'}
      </div>
    </div>
  `;
}

/**
 * 加载并渲染单张专辑的曲目列表（兼容旧代码，从缓存中获取）
 * @param {number} artistId
 * @param {number} albumId
 */
async function loadTrackSection(artistId, albumId) {
  const container = document.getElementById('track-sections');
  const sectionId = 'ts-' + albumId;

  if (!document.getElementById(sectionId)) {
    const div = document.createElement('div');
    div.className = 'track-section';
    div.id = sectionId;

    const tracks = artistTracksCache[albumId] || [];
    const albumObj = artistAlbums.find(al => al.id === albumId);
    const albumTitle = albumObj ? albumObj.title : '';

    div.innerHTML = `
      <div class="track-section-header">
        <div class="check-all ${selectedAlbums.has(albumId) ? 'checked' : ''}" onclick="toggleAlbumTracks(${albumId})">
          ${selectedAlbums.has(albumId) ? '✓' : ''}
        </div>
        <span>${esc(albumTitle)}</span>
        <button class="ts-download-btn" onclick="downloadAlbum(${albumId})" title="下载专辑">
          <i class="bi bi-download"></i>
        </button>
      </div>
      <div class="track-header-row">
        <div class="th"></div><div class="th"></div><div class="th">#</div>
        <div class="th">曲目</div><div class="th">专辑</div>
        <div class="th" style="text-align:right;">时长</div>
        <div class="th" style="text-align:center;">格式</div>
        <div class="th" style="text-align:right;">质量</div>
        <div class="th" style="text-align:right;">添加时间</div>
        <div class="th"></div>
      </div>
      <div id="tl-${albumId}">
        ${tracks.map(t => {
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
                <i class="bi bi-play-fill"></i>
              </div>
              <div class="tc tc-num">${t.track_num || '—'}</div>
              <div class="tc tc-title">${esc(t.title || t.filename)}</div>
              <div class="tc tc-album">${esc(t.album || '')}</div>
              <div class="tc tc-time">${dur}</div>
              <div class="tc tc-format ${fmt === 'FLAC' ? 'fmt-flac' : 'fmt-mp3'}">${fmt}</div>
              <div class="tc tc-quality">${sr}</div>
              <div class="tc tc-ctime">${t.ctime ? formatDateTime(t.ctime) : '—'}</div>
              <div class="tc tc-download" onclick="event.stopPropagation();downloadTrack(${t.id}, '${escJs((t.artist || 'unknown') + ' - ' + (t.title || 'unknown'))}', '${escJs(t.ext || '')}')">
                <i class="bi bi-download"></i>
              </div>
            </div>
          `;
    }).join('') || '<div class="loading-row">暂无曲目</div>'}
      </div>
    `;
    container.appendChild(div);
  }
}

/* ═══════════════════════════════════════════════════════════
   SELECTION LOGIC
═══════════════════════════════════════════════════════════ */

/**
 * 切换单张专辑的勾选状态
 * @param {MouseEvent} e
 * @param {number} albumId
 */
function toggleAlbum(e, albumId) {
  e.stopPropagation();
  const wasSelected = selectedAlbums.has(albumId);

  if (wasSelected) {
    selectedAlbums.delete(albumId);
  } else {
    selectedAlbums.add(albumId);
  }

  const card = document.getElementById('alcard-' + albumId);
  if (card) {
    card.classList.toggle('selected', selectedAlbums.has(albumId));
    const checkbox = card.querySelector('.album-checkbox');
    if (checkbox) {
      checkbox.textContent = selectedAlbums.has(albumId) ? '✓' : '';
    }
  }

  const section = document.getElementById('ts-' + albumId);
  if (section) {
    const checkAll = section.querySelector('.check-all');
    if (checkAll) {
      checkAll.textContent = '✓';
      checkAll.classList.toggle('checked', selectedAlbums.has(albumId));
    }
  }

  if (wasSelected !== selectedAlbums.has(albumId)) {
    const tracks = artistTracksCache[albumId] || [];
    if (selectedAlbums.has(albumId)) {
      tracks.forEach(t => selectedTracks.add(t.id));
    } else {
      tracks.forEach(t => selectedTracks.delete(t.id));
    }

    const tlEl = document.getElementById('tl-' + albumId);
    if (tlEl) {
      tracks.forEach(t => {
        const row = document.getElementById('tr-' + t.id);
        if (row) {
          row.classList.toggle('selected', selectedTracks.has(t.id));
          const cb = row.querySelector('.track-checkbox');
          if (cb) cb.textContent = selectedTracks.has(t.id) ? '✓' : '';
        }
      });

      const section = document.getElementById('ts-' + albumId);
      if (section) {
        const checkAll = section.querySelector('.check-all');
        if (checkAll) {
          checkAll.textContent = selectedAlbums.has(albumId) ? '✓' : '';
          checkAll.classList.toggle('checked', selectedAlbums.has(albumId));
        }
      }
    }
  }

  updateToolbar();
}

/**
 * 切换某张专辑下所有曲目的勾选状态
 * @param {string} album
 */
function toggleAlbumTracks(albumId) {
  const section = document.getElementById('ts-' + albumId);
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
    selectedAlbums.delete(albumId);
  } else {
    ids.forEach(id => selectedTracks.add(id));
    selectedAlbums.add(albumId);
  }

  const checkAll = section.querySelector('.check-all');
  if (checkAll) {
    checkAll.textContent = selectedAlbums.has(albumId) ? '✓' : '';
    checkAll.classList.toggle('checked', selectedAlbums.has(albumId));
  }

  rows.forEach(row => {
    const id = parseInt(row.id.replace('tr-', ''));
    if (!isNaN(id)) {
      row.classList.toggle('selected', selectedTracks.has(id));
      const cb = row.querySelector('.track-checkbox');
      if (cb) cb.textContent = selectedTracks.has(id) ? '✓' : '';
    }
  });

  const card = document.getElementById('alcard-' + albumId);
  if (card) {
    const checkbox = card.querySelector('.album-checkbox');
    if (checkbox) {
      checkbox.textContent = selectedAlbums.has(albumId) ? '✓' : '';
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

  const albumId = parseInt(section.id.replace('ts-', ''));

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

  if (allSelected !== selectedAlbums.has(albumId)) {
    if (allSelected) selectedAlbums.add(albumId);
    else selectedAlbums.delete(albumId);

    const card = document.getElementById('alcard-' + albumId);
    if (card) {
      const checkbox = card.querySelector('.album-checkbox');
      if (checkbox) {
        checkbox.textContent = selectedAlbums.has(albumId) ? '✓' : '';
      }
    }
  }

  updateToolbar();
}

/** 全选 / 取消全选当前艺术家所有专辑 */
function selectAllAlbums() {
  const allSelected = artistAlbums.every(al => selectedAlbums.has(al.id));
  selectedAlbums.clear();
  selectedTracks.clear();

  if (!allSelected) {
    artistAlbums.forEach(al => selectedAlbums.add(al.id));
    for (const al of artistAlbums) {
      const tracks = artistTracksCache[al.id] || [];
      tracks.forEach(t => selectedTracks.add(t.id));
    }
  }

  renderArtistView();
}

/**
 * 展开/折叠单张专辑视图
 * @param {string} album
 */
function expandAlbum(albumId) {
  currentAlbum = currentAlbum === albumId ? null : albumId;
  selectedAlbums.clear();
  renderArtistView();
}

/* ═══════════════════════════════════════════════════════════
   TOOLBAR
═══════════════════════════════════════════════════════════ */

/** 根据当前选中状态更新工具栏（全选按钮文字、计数、格式化按钮显隐） */
function updateToolbar() {
  const cntEl = document.getElementById('select-count');
  const fmtBtn = document.getElementById('format-btn');
  const selectAllBtn = document.getElementById('select-all-btn');

  const hasArtistSelection = selectedArtists.size > 0;
  const hasAlbumTrackSelection = selectedAlbums.size > 0 || selectedTracks.size > 0;

  const allSelected = artistAlbums.length > 0 &&
    artistAlbums.every(al => selectedAlbums.has(al.id));
  if (selectAllBtn) {
    selectAllBtn.textContent = allSelected ? '取消全选' : '全选专辑';
    selectAllBtn.style.display = hasArtistSelection ? 'none' : 'flex';
  }

  if (hasArtistSelection) {
    cntEl.style.display = 'flex';
    cntEl.textContent = `${selectedArtists.size} 位艺术家已选`;
    fmtBtn.style.display = 'flex';
  } else if (hasAlbumTrackSelection) {
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