
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
let editModeActive = false;
let initialEditState = null;
let initialScrapedData = null;
let initialNewCoverFile = null;
let initialNewCoverPreviewUrl = null;

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
  editModeActive = false;
  initialEditState = { ...editState };
  initialScrapedData = null;
  initialNewCoverFile = null;
  initialNewCoverPreviewUrl = null;

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
  editModeActive = false;
  initialEditState = null;
  initialScrapedData = null;
  initialNewCoverFile = null;
  if (initialNewCoverPreviewUrl) {
    URL.revokeObjectURL(initialNewCoverPreviewUrl);
    initialNewCoverPreviewUrl = null;
  }
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
    ? `<img src="${coverPreview}" style="width:100%;height:100%;object-fit:cover;cursor:zoom-in;" onclick="openCoverImageViewer('${coverPreview}', '${coverFilename}')" onerror="this.outerHTML='<i class=&quot;bi bi-disc&quot; style=&quot;font-size:48px;&quot;></i>'">`
    : `<i class="bi bi-disc" style="font-size: 48px;"></i>`;

  const relativePath = track.relative_path || track.path || '';

  const sourceLabel = scrapedData
    ? { cloud: '网易云', qq: 'QQ音乐', kugou: '酷狗' }[scrapedData._api || scrapedData._source] || scrapedData._source || ''
    : '';

  return `
    <div class="edit-layout">
      <div class="edit-left">
        <div class="edit-cover-preview">${coverImg}</div>
        <div class="edit-cover-actions">
          ${coverUrl ? `<button class="toolbar-btn" onclick="downloadCoverImage('${coverUrl}', '${coverFilename}')" title="下载封面">
            <i class="bi bi-download"></i> 下载封面
          </button>` : ''}
          <label class="toolbar-btn" title="上传替换封面">
            <i class="bi bi-upload"></i> 上传替换
            <input type="file" accept="image/jpeg,image/png" style="display:none;" onchange="handleCoverUpload(this)">
          </label>
        </div>
      </div>
      <div class="edit-right">
        ${relativePath ? `
        <div class="edit-path-info">
          <i class="bi bi-folder-open"></i>
          <span class="edit-path-text">${esc(relativePath)}</span>
        </div>
        ` : ''}
        <div class="edit-right-header">
          ${sourceLabel ? `<span class="batch-card-source ${scrapedData._api || ''}"><i class="bi bi-tag"></i> ${sourceLabel}</span>` : ''}
          <span style="flex:1;"></span>
          ${!editModeActive ? `
          <button class="toolbar-btn scrape-btn" onclick="scrapeMetadata()" id="scrape-btn">
            <i class="bi bi-search"></i> 标签搜索
          </button>
          <button class="toolbar-btn" onclick="editLyrics()">
            <i class="bi bi-file-text"></i> 歌词编辑
          </button>
          <button class="toolbar-btn edit-btn-reset" onclick="resetMetadataEdit()">
            <i class="bi bi-arrow-counterclockwise"></i> 重置
          </button>` : ''}
          ${editModeActive
      ? `<button class="toolbar-btn batch-btn-save-edit" onclick="saveMetadataEditMode()"><i class="bi bi-check-lg"></i> 保存</button>
               <button class="toolbar-btn batch-btn-cancel-edit" onclick="cancelMetadataEditMode()"><i class="bi bi-x"></i> 取消</button>`
      : `<button class="toolbar-btn batch-btn-edit" onclick="startMetadataEditMode()"><i class="bi bi-pencil"></i> 标签编辑</button>`
    }
        </div>
        <div class="edit-fields-list">
          ${renderTagField('歌名', 'title')}
          ${renderTagField('艺术家', 'artist')}
          ${renderTagField('专辑', 'album')}
          ${renderTagField('专辑艺术家', 'album_artist')}
          ${renderTagField('音轨号', 'track_num')}
          ${renderTagField('年份', 'year')}
        </div>
      </div>
    </div>
  `;
}

function renderTagField(label, fieldKey) {
  const origRaw = originalData[fieldKey];
  const hasOrig = origRaw != null && String(origRaw) !== '';
  const origVal = hasOrig ? String(origRaw) : '';
  const currentVal = editState[fieldKey] != null ? String(editState[fieldKey]) : '';
  const hasChange = currentVal !== '' && currentVal !== origVal;
  const isDifferent = hasChange && hasOrig;
  const isSame = hasOrig && currentVal === origVal;

  if (editModeActive) {
    return `
      <div class="batch-field batch-field-editing ${hasChange ? 'has-change' : ''} ${isDifferent ? 'has-original' : ''} ${isSame ? 'has-same' : ''}">
        <div class="batch-field-editing-header">
          <span class="batch-field-label">${label}</span>
          ${isDifferent ? `<span class="batch-field-original">${esc(origVal)}</span>` : ''}
        </div>
        <input type="text"
               class="batch-field-input"
               data-field="${fieldKey}"
               value="${esc(currentVal)}"
               placeholder="${esc(label)}..."
               oninput="handleEditFieldInput(this)">
      </div>
    `;
  }

  const displayVal = currentVal || '';
  return `
    <div class="batch-field ${hasChange ? 'has-change' : ''} ${isDifferent ? 'has-original' : ''} ${isSame ? 'has-same' : ''}">
      <span class="batch-field-label">${label}</span>
      ${isDifferent ? `<span class="batch-field-original">${esc(origVal)}</span>` : ''}
      <span class="batch-field-value ${hasChange ? 'changed' : ''}">${esc(displayVal || '—')}</span>
      ${isSame ? '<span class="batch-field-same-indicator"></span>' : ''}
    </div>
  `;
}

function startMetadataEditMode() {
  editModeActive = true;
  rerenderEditModal();
  setTimeout(() => {
    const firstInput = document.querySelector('.edit-right .batch-field-input');
    if (firstInput) firstInput.focus();
  }, 100);
}

function saveMetadataEditMode() {
  editModeActive = false;
  rerenderEditModal();
  showToast('已保存编辑', 'success');
}

function cancelMetadataEditMode() {
  editState = { ...originalData };
  if (scrapedData) {
    for (const key of ['title', 'artist', 'album', 'album_artist', 'year', 'track_num', 'lyrics']) {
      if (scrapedData[key] !== null && scrapedData[key] !== undefined) {
        editState[key] = scrapedData[key];
      }
    }
  }
  editModeActive = false;
  rerenderEditModal();
}

function resetMetadataEdit() {
  const tagKeys = ['title', 'artist', 'album', 'album_artist', 'track_num', 'year'];
  const hasOriginalTags = tagKeys.some(k => originalData[k] != null && String(originalData[k]) !== '');

  if (hasOriginalTags) {
    editState = { ...originalData };
    scrapedData = null;
  } else if (initialScrapedData) {
    editState = { ...originalData };
    for (const key of tagKeys) {
      if (initialScrapedData[key] !== null && initialScrapedData[key] !== undefined) {
        editState[key] = initialScrapedData[key];
      }
    }
    scrapedData = initialScrapedData;
  } else {
    editState = { ...originalData };
    scrapedData = null;
  }

  newCoverFile = initialNewCoverFile || null;
  if (newCoverPreviewUrl) {
    URL.revokeObjectURL(newCoverPreviewUrl);
  }
  newCoverPreviewUrl = initialNewCoverPreviewUrl || null;

  editModeActive = false;
  rerenderEditModal();
  showToast('已重置', 'info');
}

function handleEditFieldInput(el) {
  const field = el.dataset.field;
  let val = el.value;
  if (field === 'track_num') {
    val = val === '' ? '' : parseInt(val, 10);
    if (isNaN(val)) val = '';
  }
  editState[field] = val;
}

function rerenderEditModal() {
  const trackId = document.getElementById('metadata-edit-modal').dataset.trackId;
  GET(`/tracks/${trackId}`).then(track => {
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
    preview.innerHTML = `<img src="${newCoverPreviewUrl}" style="width:100%;height:100%;object-fit:cover;cursor:zoom-in;" onclick="openCoverImageViewer('${newCoverPreviewUrl}', '')">`;
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
    const userInput = {
      title: editState.title || '',
      artist: editState.artist || '',
      album: editState.album || '',
      track_num: editState.track_num ?? '',
      year: editState.year || ''
    };
    const result = await POST(`/tracks/${trackId}/scrape-all`, userInput);

    if (result.ok) {
      renderScrapeResults(result.results);
    } else {
      document.getElementById('scrape-results-body').innerHTML = `
            <div class="scrape-empty-state">
              <i class="bi bi-exclamation-circle" style="font-size: 24px;"></i>
              <div>搜索失败: ${result.error || '未知错误'}</div>
            </div>
          `;
    }
  } catch (e) {
    document.getElementById('scrape-results-body').innerHTML = `
        <div class="scrape-empty-state">
          <i class="bi bi-exclamation-circle" style="font-size: 24px;"></i>
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
  const hasAnyResults = results.cloud.length > 0 || results.kugou.length > 0 || results.qq.length > 0;

  if (!hasAnyResults) {
    body.innerHTML = `
        <div class="scrape-empty-state">
          <i class="bi bi-emoji-frown" style="font-size: 24px;"></i>
          <div>未找到任何匹配的元数据</div>
        </div>
      `;
    return;
  }

  let html = '<div class="scrape-results-container">';

  const apiNames = {
    cloud: '网易云音乐',
    kugou: '酷狗音乐',
    qq: 'QQ音乐'
  };

  for (const [api, items] of Object.entries(results)) {
    if (items.length === 0) continue;

    html += `
        <div class="scrape-api-section" data-api="${api}">
          <div class="scrape-api-title ${api}">
            <span class="lyrics-source-capsule ${api === 'cloud' ? 'netease' : api}">
              <span class="capsule-primary"></span>
              <span class="capsule-secondary"></span>
              <span class="capsule-text">${apiNames[api]}</span>
            </span>
            <span class="api-count ${api}">${items.length}</span>
            <button class="refresh-api-btn" onclick="refreshApiResults('${api}')" title="换一批">
              <i class="bi bi-arrow-clockwise"></i>
            </button>
          </div>
          <div class="scrape-results-grid" id="scrape-grid-${api}">
      `;

    for (let i = 0; i < items.length; i++) {
      const item = items[i];
      const coverSrc = item._cover_data ? `data:image/jpeg;base64,${item._cover_data}` : '';
      const coverHtml = coverSrc
        ? `<img class="scrape-cover" src="${coverSrc}" alt="cover">`
        : `<div class="scrape-cover" style="display:flex;align-items:center;justify-content:center;color:var(--text3);">
                <i class="bi bi-disc" style="font-size: 32px;"></i>
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
  // 存储每个 API 的当前结果 ID，用于"换一批"时排除（使用 idOrMd5）
  window._scrapeResultsByApi = {};
  for (const [api, items] of Object.entries(results)) {
    window._scrapeResultsByApi[api] = items.map(item => item._id);
  }
}

/**
 * 刷新单个 API 的搜索结果（换一批）
 * @param {string} api - API 名称 (cloud, kugou 或 qq)
 */
async function refreshApiResults(api) {
  const trackId = document.getElementById('metadata-edit-modal').dataset.trackId;
  const grid = document.getElementById(`scrape-grid-${api}`);
  const refreshBtn = document.querySelector(`.scrape-api-section[data-api="${api}"] .refresh-api-btn`);

  if (!grid) return;

  // 添加加载状态
  refreshBtn.classList.add('spin');

  // 获取当前显示的所有 ID（用于排除，使用 idOrMd5）
  const currentIds = window._scrapeResultsByApi[api] || [];
  const userInput = {
    exclude_ids: currentIds,
    title: editState.title || '',
    artist: editState.artist || '',
    album: editState.album || '',
    track_num: editState.track_num ?? '',
    year: editState.year || ''
  };
  try {
    const result = await POST(`/tracks/${trackId}/scrape-all`, userInput);

    if (result.ok) {
      const newItems = result.results[api] || [];

      if (newItems.length === 0) {
        showToast(`没有更多 ${api === 'cloud' ? '网易云音乐' : '酷狗音乐'} 的结果了`, 'info');
        refreshBtn.classList.remove('spin');
        return;
      }

      // 更新缓存中的 ID 列表（使用 idOrMd5）
      window._scrapeResultsByApi[api] = newItems.map(item => item._id);

      // 重新渲染该 API 的结果
      grid.innerHTML = newItems.map((item, i) => {
        const coverSrc = item._cover_data ? `data:image/jpeg;base64,${item._cover_data}` : '';
        const coverHtml = coverSrc
          ? `<img class="scrape-cover" src="${coverSrc}" alt="cover">`
          : `<div class="scrape-cover" style="display:flex;align-items:center;justify-content:center;color:var(--text3);">
              <i class="bi bi-disc" style="font-size: 32px;"></i>
             </div>`;

        return `
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
      }).join('');

      // 更新标题中的数量
      const countEl = document.querySelector(`.scrape-api-section[data-api="${api}"] .api-count`);
      if (countEl) {
        countEl.textContent = newItems.length;
      }

      // 清除当前选择
      document.getElementById('scrape-confirm-btn').disabled = true;
      selectedScrapeResult = null;

      showToast(`已加载新的 ${{ cloud: '网易云音乐', kugou: '酷狗音乐', qq: 'QQ音乐' }[api]} 结果`, 'success');
    }
  } catch (e) {
    showToast(`刷新失败: ${e.message}`, 'error');
  } finally {
    refreshBtn.classList.remove('spin');
  }
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

  if (!initialScrapedData) {
    initialScrapedData = scrapedData;
    initialNewCoverFile = newCoverFile;
    if (newCoverPreviewUrl && !initialNewCoverPreviewUrl) {
      initialNewCoverPreviewUrl = newCoverPreviewUrl;
    }
  }

  closeScrapeResultsModal();
  rerenderEditModal();
  showToast(`已选择 ${scrapedData._source} 的元数据`, 'success');
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
    const artistIdsToInvalidate = artistsToInvalidate
      .map(name => { const a = allArtists.find(x => x.name === name); return a ? a.id : null; })
      .filter(id => id !== null);
    clearArtistsFromCache(artistIdsToInvalidate);

    const artistChanged = editState.artist && editState.artist !== originalData.artist;
    const newArtistName = editState.artist;

    showToast('保存成功', 'success');
    closeMetadataEdit();

    if (typeof currentArtist !== 'undefined' && currentArtist) {
      if (artistChanged) {
        await loadArtistTree(document.getElementById('artist-search')?.value || '');
      }
      const targetArtistName = artistChanged ? newArtistName : currentArtist.name;
      let targetArtistObj = allArtists.find(a => a.name === targetArtistName);
      if (!targetArtistObj && artistChanged) {
        await loadArtistTree(document.getElementById('artist-search')?.value || '');
        targetArtistObj = allArtists.find(a => a.name === targetArtistName);
      }
      const targetArtistId = targetArtistObj ? targetArtistObj.id : null;
      if (targetArtistId) {
        const fullInfo = await GET(`/artists/${targetArtistId}/full`);
        setArtistToCache(targetArtistId, fullInfo);
        artistAlbums = fullInfo.albums;
        artistTracksCache = {};
        fullInfo.albums.forEach(al => { artistTracksCache[al.id] = al.tracks; });
        if (artistChanged) {
          currentArtist = targetArtistObj;
        }
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
