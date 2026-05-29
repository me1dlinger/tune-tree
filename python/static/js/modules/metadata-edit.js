/**
 * metadata-edit.js — 元数据在线编辑模块
 * 包含：编辑弹窗渲染、临时状态管理、封面管理、歌词编辑、保存提交。
 * 依赖：api.js（PUT）、state.js（TOKEN）、ui.js（openModal/closeModal/showToast）、detail.js（showTrackDetail）
 */

/* ═══════════════════════════════════════════════════════════
   STATE
   ═══════════════════════════════════════════════════════════ */

let editState = null;
let originalData = null;
let newCoverFile = null;
let newCoverPreviewUrl = null;
let lyricsExpanded = false;

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
  newCoverFile = null;
  newCoverPreviewUrl = null;
  lyricsExpanded = false;

  const modal = document.getElementById('metadata-edit-modal');
  modal.dataset.trackId = track.id;
  modal.querySelector('.modal-body').innerHTML = renderEditModal(track);
  openModal('metadata-edit-modal');
}

function closeMetadataEdit() {
  editState = null;
  originalData = null;
  newCoverFile = null;
  if (newCoverPreviewUrl) {
    URL.revokeObjectURL(newCoverPreviewUrl);
    newCoverPreviewUrl = null;
  }
  lyricsExpanded = false;
  closeModal('metadata-edit-modal');
}

/* ═══════════════════════════════════════════════════════════
   RENDER
   ═══════════════════════════════════════════════════════════ */

function renderEditModal(track) {
  const coverUrl = track.has_cover ? `/api/cover/${track.id}?token=${TOKEN}` : '';
  const coverPreview = newCoverPreviewUrl
    ? `<img src="${newCoverPreviewUrl}" style="width:100%;height:100%;object-fit:cover;">`
    : coverUrl
      ? `<img src="${coverUrl}" style="width:100%;height:100%;object-fit:cover;">`
      : `<svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1">
           <path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/>
         </svg>`;

  return `
    <div class="edit-cover-area">
      <div class="edit-cover-preview">${coverPreview}</div>
      <div class="edit-cover-actions">
        ${coverUrl ? `<a class="toolbar-btn" href="${coverUrl}" download="cover.jpg" title="下载封面">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
            <polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>
          </svg>
          下载封面
        </a>` : ''}
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
      <div class="edit-field">
        <label>歌名</label>
        <input type="text" value="${esc(editState.title)}" data-field="title" oninput="handleFieldInput(this)">
      </div>
      <div class="edit-field">
        <label>艺术家</label>
        <input type="text" value="${esc(editState.artist)}" data-field="artist" oninput="handleFieldInput(this)">
      </div>
      <div class="edit-field">
        <label>专辑名</label>
        <input type="text" value="${esc(editState.album)}" data-field="album" oninput="handleFieldInput(this)">
      </div>
      <div class="edit-field">
        <label>专辑艺术家</label>
        <input type="text" value="${esc(editState.album_artist)}" data-field="album_artist" oninput="handleFieldInput(this)">
      </div>
      <div class="edit-field-row">
        <div class="edit-field">
          <label>音轨号</label>
          <input type="number" min="0" value="${esc(String(editState.track_num))}" data-field="track_num" oninput="handleFieldInput(this)">
        </div>
        <div class="edit-field">
          <label>年份</label>
          <input type="text" value="${esc(editState.year)}" data-field="year" oninput="handleFieldInput(this)">
        </div>
      </div>
    </div>
    <div class="edit-lyrics-toggle">
      <button class="toolbar-btn" onclick="toggleLyricsEditor()">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
          <polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/>
        </svg>
        ${lyricsExpanded ? '收起歌词编辑' : '编辑歌词'}
      </button>
    </div>
    <div class="edit-lyrics-panel" style="display:${lyricsExpanded ? 'block' : 'none'};">
      <textarea class="edit-lyrics-textarea" oninput="handleLyricsInput(this)">${esc(editState.lyrics)}</textarea>
    </div>
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

function handleLyricsInput(el) {
  editState.lyrics = el.value;
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

/* ═══════════════════════════════════════════════════════════
   LYRICS TOGGLE
   ═══════════════════════════════════════════════════════════ */

function toggleLyricsEditor() {
  lyricsExpanded = !lyricsExpanded;
  const panel = document.querySelector('#metadata-edit-modal .edit-lyrics-panel');
  const btn = document.querySelector('#metadata-edit-modal .edit-lyrics-toggle button');
  if (panel) panel.style.display = lyricsExpanded ? 'block' : 'none';
  if (btn) {
    btn.innerHTML = `
      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
        <polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/>
      </svg>
      ${lyricsExpanded ? '收起歌词编辑' : '编辑歌词'}`;
  }
}

/* ═══════════════════════════════════════════════════════════
   SAVE
   ═══════════════════════════════════════════════════════════ */

async function saveMetadataEdit() {
  if (!editState || !originalData) return;

  const trackId = document.querySelector('#metadata-edit-modal').dataset.trackId;
  if (!trackId) return;

  const saveBtn = document.getElementById('metadata-edit-save-btn');
  saveBtn.disabled = true;
  saveBtn.classList.add('loading');

  try {
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

    if (newCoverFile) {
      const formData = new FormData();
      formData.append('cover', newCoverFile);
      const res = await fetch(`/api/tracks/${trackId}/cover`, {
        method: 'PUT',
        headers: { 'X-Token': TOKEN },
        body: formData,
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ error: res.statusText }));
        throw new Error(err.error || '封面上传失败');
      }
      bustCoverCache(parseInt(trackId, 10));
    }

    if (String(editState.lyrics) !== String(originalData.lyrics)) {
      await PUT(`/tracks/${trackId}/lyrics`, { lyrics: editState.lyrics });
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
