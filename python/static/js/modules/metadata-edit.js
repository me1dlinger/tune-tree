
/**
 * metadata-edit.js — 元数据在线编辑模块
 * 包含：编辑弹窗渲染、临时状态管理、封面管理、歌词编辑、保存提交、元数据刮削
 * 依赖：api.js（PUT）、state.js（TOKEN）、ui.js（openModal/closeModal/showToast）、detail.js（showTrackDetail）
 */

/* ═══════════════════════════════════════════════════════════
   STATE
   ═══════════════════════════════════════════════════════════ */

let editState = null;
let originalData = null;
let scrapedData = null;
let newCoverFile = null;
let newCoverPreviewUrl = null;

/* ═══════════════════════════════════════════════════════════
   OPEN / CLOSE
   ═══════════════════════════════════════════════════════════ */

function openMetadataEdit(track) {
  originalData = {
    title: track.title || '',
    artist: track.artist || '',
    album: track.album || '',
    album_artist: track.album_artist || '',
    track_num: track.track_num ?? '',
    year: track.year || '',
    lyrics: track.lyrics || '',
  };
  editState = { ...originalData };
  scrapedData = null;
  newCoverFile = null;
  newCoverPreviewUrl = null;

  const modal = document.getElementById('metadata-edit-modal');
  modal.dataset.trackId = track.id;
  modal.querySelector('.modal-body').innerHTML = renderEditModal(track);
  openModal('metadata-edit-modal');
}

function closeMetadataEdit() {
  editState = null;
  originalData = null;
  scrapedData = null;
  newCoverFile = null;
  if (newCoverPreviewUrl) {
    URL.revokeObjectURL(newCoverPreviewUrl);
    newCoverPreviewUrl = null;
  }
  closeModal('metadata-edit-modal');
}

/* ═══════════════════════════════════════════════════════════
   RENDER
   ═══════════════════════════════════════════════════════════ */

function renderEditModal(track) {
  const coverUrl = track.has_cover ? `/api/cover/${track.id}?token=${TOKEN}` : '';
  const coverFilename = `${(track.artist || 'unknown').replace(/[\\/:*?"<>|]/g, '_')}-${(track.album || 'unknown').replace(/[\\/:*?"<>|]/g, '_')}`;
  let coverPreview = '';

  if (newCoverPreviewUrl) {
    coverPreview = newCoverPreviewUrl;
  } else if (scrapedData && scrapedData._cover_data) {
    coverPreview = `data:image/jpeg;base64,${scrapedData._cover_data}`;
  } else if (coverUrl) {
    coverPreview = coverUrl;
  }

  const coverImg = coverPreview
    ? `<img src="${coverPreview}" style="width:100%;height:100%;object-fit:cover;cursor:zoom-in;" onclick="openCoverImageViewer('${coverPreview}', '${coverFilename}')">`
    : `<svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1">
             <path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/>
           </svg>`;

  return `
    <div class="edit-cover-area">
      <div class="edit-cover-preview">${coverImg}</div>
      <div class="edit-cover-actions">
        ${coverUrl ? `<button class="toolbar-btn" onclick="downloadCoverImage('${coverUrl}', '${coverFilename}')" title="下载封面">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
            <polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>
          </svg>
          下载封面
        </button>` : ''}
        <label class="toolbar-btn" title="上传替换封面">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
            <polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/>
          </svg>
          上传替换
          <input type="file" accept="image/jpeg,image/png" style="display:none;" onchange="handleCoverUpload(this)">
        </label>
      </div>
    </div>
    <div class="edit-form">
      <div class="scrape-section">
        <button class="toolbar-btn scrape-btn" onclick="scrapeMetadata()" id="scrape-btn">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/>
          </svg>
          音乐标签
        </button>
        <button class="toolbar-btn" onclick="editLyrics()">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
            <polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/>
          </svg>
          编辑歌词
        </button>
        ${scrapedData ? `<div class="scrape-success">已从 ${scrapedData._source} 获取元数据</div>` : ''}
      </div>
      <div class="edit-field">
        <label>歌名</label>
        ${renderFieldWithComparison('title')}
      </div>
      <div class="edit-field">
        <label>艺术家</label>
        ${renderFieldWithComparison('artist')}
      </div>
      <div class="edit-field">
        <label>专辑名</label>
        ${renderFieldWithComparison('album')}
      </div>
      <div class="edit-field">
        <label>专辑艺术家</label>
        ${renderFieldWithComparison('album_artist')}
      </div>
      <div class="edit-field-row">
        <div class="edit-field">
          <label>音轨号</label>
          ${renderFieldWithComparison('track_num')}
        </div>
        <div class="edit-field">
          <label>年份</label>
          ${renderFieldWithComparison('year')}
        </div>
      </div>
    </div>
  `;
}

function renderFieldWithComparison(field) {
  const currentValue = esc(String(editState[field] || ''));
  const originalValue = esc(String(originalData[field] || ''));
  const scrapedValue = scrapedData ? esc(String(scrapedData[field] || '')) : null;
  const hasChange = scrapedData && scrapedValue !== originalValue && scrapedValue !== '';

  if (field === 'track_num') {
    return `
      <input type="number" min="0" value="${currentValue}" data-field="${field}" oninput="handleFieldInput(this)">
      ${hasChange ? `<div class="scraped-suggestion" onclick="useScrapedValue('${field}')">推荐: ${scrapedValue}</div>` : ''}
      ${originalValue !== '' ? `<div class="original-value">原值: ${originalValue}</div>` : ''}
    `;
  }

  return `
    <input type="text" value="${currentValue}" data-field="${field}" oninput="handleFieldInput(this)">
    ${hasChange ? `<div class="scraped-suggestion" onclick="useScrapedValue('${field}')">推荐: ${scrapedValue}</div>` : ''}
    ${originalValue !== '' ? `<div class="original-value">原值: ${originalValue}</div>` : ''}
  `;
}

/* ═══════════════════════════════════════════════════════════
   FIELD INPUT HANDLERS
   ═══════════════════════════════════════════════════════════ */

function handleFieldInput(el) {
  const field = el.dataset.field;
  let val = el.value;
  if (field === 'track_num') {
    val = val === '' ? '' : parseInt(val, 10);
    if (isNaN(val)) val = '';
  }
  editState[field] = val;
}

function useScrapedValue(field) {
  if (!scrapedData) return;

  if (field === 'lyrics') {
    editState.lyrics = scrapedData.lyrics || '';
  } else if (scrapedData[field] !== null && scrapedData[field] !== undefined) {
    editState[field] = scrapedData[field];
  }

  const trackId = document.getElementById('metadata-edit-modal').dataset.trackId;
  GET(`/api/tracks/${trackId}`).then(track => {
    document.getElementById('metadata-edit-modal').querySelector('.modal-body').innerHTML = renderEditModal(track);
  });
}

/* ═══════════════════════════════════════════════════════════
   COVER MANAGEMENT
   ═══════════════════════════════════════════════════════════ */

function handleCoverUpload(input) {
  const file = input.files[0];
  if (!file) return;

  if (!['image/jpeg', 'image/png'].includes(file.type)) {
    showToast('仅支持 JPEG/PNG 格式', 'error');
    input.value = '';
    return;
  }
  if (file.size > 5 * 1024 * 1024) {
    showToast('封面图片不能超过 5MB', 'error');
    input.value = '';
    return;
  }

  newCoverFile = file;
  if (newCoverPreviewUrl) URL.revokeObjectURL(newCoverPreviewUrl);
  newCoverPreviewUrl = URL.createObjectURL(file);

  const preview = document.querySelector('#metadata-edit-modal .edit-cover-preview');
  if (preview) {
    preview.innerHTML = `<img src="${newCoverPreviewUrl}" style="width:100%;height:100%;object-fit:cover;">`;
  }
}

function downloadCoverImage(coverUrl, filename) {
  fetch(coverUrl)
    .then(response => response.blob())
    .then(blob => {
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename + '.jpg';
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
      showToast('封面已下载', 'success');
    })
    .catch(() => {
      showToast('下载失败', 'error');
    });
}

/* ═══════════════════════════════════════════════════════════
   LYRICS MANAGEMENT
   ═══════════════════════════════════════════════════════════ */

function editLyrics() {
  const trackId = document.getElementById('metadata-edit-modal').dataset.trackId;
  GET(`/tracks/${trackId}`).then(track => {
    window.onLyricsConfirmed = (newLyrics) => {
      editState.lyrics = newLyrics;
    };
    openLyricsEditorModal(track, editState.lyrics);
  });
}

/* ═══════════════════════════════════════════════════════════
   METADATA SCRAPING
   ═══════════════════════════════════════════════════════════ */

let selectedScrapeResult = null;

async function scrapeMetadata() {
  const trackId = document.getElementById('metadata-edit-modal').dataset.trackId;
  const btn = document.getElementById('scrape-btn');

  btn.disabled = true;
  btn.classList.add('loading');

  document.getElementById('scrape-results-body').innerHTML = `
    <div class="scrape-loading">
      <div class="loading-spinner"></div>
      <div>正在获取元数据...</div>
    </div>
  `;
  openModal('scrape-results-modal');
  selectedScrapeResult = null;

  try {
    const result = await POST(`/tracks/${trackId}/scrape-all`, {});

    if (result.ok) {
      renderScrapeResults(result.results);
    } else {
      document.getElementById('scrape-results-body').innerHTML = `
            <div class="scrape-empty-state">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                <circle cx="12" cy="12" r="10"/>
                <line x1="15" y1="9" x2="9" y2="15"/>
                <line x1="9" y1="9" x2="15" y2="15"/>
              </svg>
              <div>搜索失败: ${result.error || '未知错误'}</div>
            </div>
          `;
    }
  } catch (e) {
    document.getElementById('scrape-results-body').innerHTML = `
        <div class="scrape-empty-state">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
            <circle cx="12" cy="12" r="10"/>
            <line x1="15" y1="9" x2="9" y2="15"/>
            <line x1="9" y1="9" x2="15" y2="15"/>
          </svg>
          <div>请求出错: ${e.message}</div>
        </div>
      `;
  } finally {
    btn.disabled = false;
    btn.classList.remove('loading');
  }
}

function renderScrapeResults(results) {
  const body = document.getElementById('scrape-results-body');
  const hasAnyResults = results.cloud.length > 0 || results.kugou.length > 0;

  if (!hasAnyResults) {
    body.innerHTML = `
        <div class="scrape-empty-state">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
            <path d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/>
          </svg>
          <div>未找到任何匹配的元数据</div>
        </div>
      `;
    return;
  }

  let html = '<div class="scrape-results-container">';

  const apiNames = {
    cloud: '网易云音乐',
    kugou: '酷狗音乐'
  };

  for (const [api, items] of Object.entries(results)) {
    if (items.length === 0) continue;

    html += `
        <div class="scrape-api-section">
          <div class="scrape-api-title ${api}">${apiNames[api]} (${items.length})</div>
          <div class="scrape-results-grid">
      `;

    for (let i = 0; i < items.length; i++) {
      const item = items[i];
      const coverSrc = item._cover_data ? `data:image/jpeg;base64,${item._cover_data}` : '';
      const coverHtml = coverSrc
        ? `<img class="scrape-cover" src="${coverSrc}" alt="cover">`
        : `<div class="scrape-cover" style="display:flex;align-items:center;justify-content:center;color:var(--text3);">
                <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1">
                  <path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/>
                </svg>
               </div>`;

      html += `
            <div class="scrape-result-card" data-api="${api}" data-index="${i}" onclick="selectScrapeResult('${api}', ${i})" ondblclick="confirmScrapeResult('${api}', ${i})">
              ${coverHtml}
              <div class="scrape-title" title="${esc(item.title || '')}">${item.title || '未知歌名'}</div>
              <div class="scrape-artist" title="${esc(item.artist || '')}">${item.artist || '未知艺术家'}</div>
              <div class="scrape-album" title="${esc(item.album || '')}">${item.album || '未知专辑'}</div>
              <div class="scrape-meta">
                ${item.track_num ? `<span>音轨: ${item.track_num}</span>` : ''}
                ${item.year ? `<span>年份: ${item.year}</span>` : ''}
              </div>
            </div>
          `;
    }

    html += '</div></div>';
  }

  html += `
      <div class="scrape-confirm-btn" style="text-align:center;">
        <button class="toolbar-btn primary" id="scrape-confirm-btn" onclick="confirmScrapeSelection()" disabled>
          确认选择
        </button>
      </div>
    `;

  html += '</div>';
  body.innerHTML = html;

  window._scrapeResultsCache = results;
}

function selectScrapeResult(api, index) {
  const cards = document.querySelectorAll('.scrape-result-card');
  cards.forEach(card => card.classList.remove('selected'));

  const selectedCard = document.querySelector(`.scrape-result-card[data-api="${api}"][data-index="${index}"]`);
  if (selectedCard) {
    selectedCard.classList.add('selected');
  }

  const results = window._scrapeResultsCache;
  selectedScrapeResult = results[api][index];

  document.getElementById('scrape-confirm-btn').disabled = false;
}

function confirmScrapeResult(api, index) {
  selectScrapeResult(api, index);
  confirmScrapeSelection();
}

function confirmScrapeSelection() {
  if (!selectedScrapeResult) {
    showToast('请先选择一个结果', 'warning');
    return;
  }

  scrapedData = selectedScrapeResult;

  for (const key of ['title', 'artist', 'album', 'album_artist', 'year', 'track_num', 'lyrics']) {
    if (scrapedData[key] !== null && scrapedData[key] !== undefined) {
      editState[key] = scrapedData[key];
    }
  }

  closeScrapeResultsModal();

  const trackId = document.getElementById('metadata-edit-modal').dataset.trackId;
  GET(`/api/tracks/${trackId}`).then(track => {
    document.getElementById('metadata-edit-modal').querySelector('.modal-body').innerHTML = renderEditModal(track);
    showToast(`已选择 ${selectedScrapeResult._source} 的元数据`, 'success');
  });
}

function closeScrapeResultsModal() {
  selectedScrapeResult = null;
  window._scrapeResultsCache = null;
  closeModal('scrape-results-modal');
}

/* ═══════════════════════════════════════════════════════════
   SAVE
   ═══════════════════════════════════════════════════════════ */

async function saveMetadataEdit() {
  if (!editState || !originalData) return;

  const trackId = document.getElementById('metadata-edit-modal').dataset.trackId;
  if (!trackId) return;

  const saveBtn = document.getElementById('metadata-edit-save-btn');
  saveBtn.disabled = true;
  saveBtn.classList.add('loading');

  try {
    if (scrapedData) {
      const applyData = { ...scrapedData };
      for (const key of ['title', 'artist', 'album', 'album_artist', 'year', 'track_num', 'lyrics']) {
        if (editState[key] !== originalData[key]) {
          applyData[key] = editState[key];
        }
      }

      await POST(`/tracks/${trackId}/apply-scrape`, applyData);
    } else {
      const metaChanges = {};
      const metaFields = ['title', 'artist', 'album', 'album_artist', 'track_num', 'year'];
      for (const f of metaFields) {
        if (String(editState[f]) !== String(originalData[f])) {
          metaChanges[f] = editState[f] === '' ? null : editState[f];
        }
      }

      if (Object.keys(metaChanges).length > 0) {
        await PUT(`/tracks/${trackId}/metadata`, metaChanges);
      }

      if (String(editState.lyrics) !== String(originalData.lyrics)) {
        await PUT(`/tracks/${trackId}/lyrics`, { lyrics: editState.lyrics });
      }
    }

    if (newCoverFile) {
      const formData = new FormData();
      formData.append('cover', newCoverFile);
      await apiUpload(`/tracks/${trackId}/cover`, formData, 'PUT');
      bustCoverCache(parseInt(trackId, 10));
    }

    const artistsToInvalidate = [originalData.artist];
    if (editState.artist && editState.artist !== originalData.artist) {
      artistsToInvalidate.push(editState.artist);
    }
    clearArtistsFromCache(artistsToInvalidate);

    const artistChanged = editState.artist && editState.artist !== originalData.artist;

    showToast('保存成功', 'success');
    closeMetadataEdit();

    if (typeof currentArtist !== 'undefined' && currentArtist) {
      if (artistChanged) {
        await loadArtistTree(document.getElementById('artist-search')?.value || '');
      }
      const targetArtist = artistChanged ? artistsToInvalidate[1] : currentArtist.artist;
      const fullInfo = await GET(`/artists/${encodeURIComponent(targetArtist)}/full`);
      setArtistToCache(targetArtist, fullInfo);
      artistAlbums = fullInfo.albums;
      artistTracksCache = {};
      fullInfo.albums.forEach(al => { artistTracksCache[al.album] = al.tracks; });
      if (artistChanged) {
        currentArtist = allArtists.find(a => a.artist === targetArtist) || { artist: targetArtist };
      }
      currentAlbum = null;
      renderArtistView();
    }

    showTrackDetail(parseInt(trackId, 10));
  } catch (e) {
    showToast('保存失败: ' + e.message, 'error');
  } finally {
    saveBtn.disabled = false;
    saveBtn.classList.remove('loading');
  }
}
