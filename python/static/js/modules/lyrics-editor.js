
/**
 * lyrics-editor.js - 歌词可视化编辑器模块
 * 功能：LRC解析展示、行编辑、音乐播放、打轴、缓存持久化
 * 依赖：api.js, ui.js, lrc-parser.js
 */

let lyricsState = {
    track: null,
    currentLyrics: '',
    initialLyrics: '',
    parsedData: null,
    activeGroupIndex: -1,
    searchResults: [],
    searchCache: {},
    audioElement: null,
    isPlaying: false,
    volume: 0.8,
    cacheKey: null,
    _audioTrackId: null,
    _audioCurrentTime: 0
};

const CACHE_PREFIX = 'tt-lyrics-edit-';

function _getCacheKey() {
    if (!lyricsState.track) return null;
    return `${CACHE_PREFIX}${lyricsState.track.id}`;
}

function _saveToCache() {
    const key = _getCacheKey();
    if (!key || !lyricsState.parsedData) return;
    try {
        localStorage.setItem(key, JSON.stringify({
            groups: lyricsState.parsedData.groups,
            metadata: lyricsState.parsedData.metadata,
            hasTimestamp: lyricsState.parsedData.hasTimestamp,
            audioFileName: null,
            timestamp: Date.now()
        }));
    } catch (e) { }
}

function _loadFromCache() {
    const key = _getCacheKey();
    if (!key) return null;
    try {
        const raw = localStorage.getItem(key);
        if (!raw) return null;
        const data = JSON.parse(raw);
        return data;
    } catch (e) {
        return null;
    }
}

function _clearCache() {
    const key = _getCacheKey();
    if (key) {
        try { localStorage.removeItem(key); } catch (e) { }
    }
}

function openLyricsEditorModal(trackOrLyrics, lyrics = null) {
    if (typeof trackOrLyrics === 'object' && trackOrLyrics !== null) {
        lyricsState.track = trackOrLyrics;
        lyricsState.currentLyrics = lyrics !== null ? lyrics : (trackOrLyrics.lyrics || '');
    } else {
        lyricsState.track = null;
        lyricsState.currentLyrics = trackOrLyrics || '';
    }

    lyricsState.initialLyrics = lyricsState.currentLyrics;

    const modal = document.getElementById('lyrics-editor-modal');
    const infoEl = document.getElementById('lyrics-editor-info');
    const searchPanel = document.getElementById('lyrics-editor-search-panel');
    searchPanel.style.display = 'none';

    if (lyricsState.track) {
        const t = lyricsState.track;
        infoEl.innerHTML = `<div class="lyrics-editor-track-info">
            <span class="lyrics-editor-track-title">${esc(t.title || '未知歌曲')}</span>
            <span class="lyrics-editor-track-artist">${esc(t.artist || '未知艺术家')}</span>
            <span class="lyrics-editor-track-album">${esc(t.album || '')}</span>
        </div>`;
    } else {
        infoEl.innerHTML = '';
    }

    const cached = _loadFromCache();
    if (cached && cached.groups) {
        lyricsState.parsedData = cached;
    } else {
        lyricsState.parsedData = LrcParser.parse(lyricsState.currentLyrics);
    }

    lyricsState.activeGroupIndex = -1;

    _renderEditor();
    _initAudioPlayer();
    _bindKeyboardShortcuts();

    openModal('lyrics-editor-modal');

    if (lyricsState.track) {
        const trackId = lyricsState.track.id;
        const audio = lyricsState.audioElement;
        if (audio && lyricsState._audioTrackId === trackId && audio.src) {
            _restoreAudioState();
        } else {
            _loadAudioFromServer(trackId);
        }
    }
}

function closeLyricsEditorModal() {
    _saveAudioState();
    _unbindKeyboardShortcuts();
    if (lyricsState.audioElement) {
        lyricsState.audioElement.pause();
        lyricsState.isPlaying = false;
        _updatePlayButton();
    }
    closeModal('lyrics-editor-modal');
}

function _saveAudioState() {
    const audio = lyricsState.audioElement;
    if (!audio) return;
    lyricsState._audioCurrentTime = audio.currentTime || 0;
}

function _restoreAudioState() {
    const audio = lyricsState.audioElement;
    if (!audio) return;

    if (lyricsState._audioCurrentTime > 0 && audio.duration) {
        audio.currentTime = Math.min(lyricsState._audioCurrentTime, audio.duration);
    }

    _updateTimeDisplay();
    _updateProgress();
    _updatePlayButton();

    const label = document.getElementById('lyrics-audio-filename');
    if (label && lyricsState.track) {
        label.textContent = lyricsState.track.filename || '已加载';
    }
}

function _renderEditor() {
    const container = document.getElementById('lyrics-editor-lines');
    const metaContainer = document.getElementById('lyrics-editor-metadata');
    if (!container || !lyricsState.parsedData) return;

    _renderMetadata(metaContainer);
    _renderLines(container);
}

function _renderMetadata(container) {
    const data = lyricsState.parsedData;

    const metaTags = (data.metadata || []).map(m => `[${m.key}:${m.value}]`).join('\n');

    const hasTimestamped = data.groups.some(g => g.timestamp !== null);
    const untimestampedLines = hasTimestamped ? data.groups
        .filter(g => g.timestamp === null)
        .map(g => g.primary.text)
        .join('\n') : '';

    const rawMeta = data.metadataRaw || (metaTags ? metaTags + (untimestampedLines ? '\n' + untimestampedLines : '') : untimestampedLines);

    container.style.display = 'block';
    let html = '<div class="lyrics-metadata-header"><span class="lyrics-metadata-label">附加信息</span></div>';
    html += '<div class="lyrics-metadata-rows">';
    html += `<textarea class="lyrics-untimestamped-input" id="lyrics-metadata-raw" placeholder="附加信息，将放置在歌词头">${esc(rawMeta)}</textarea>`;
    html += '</div>';
    container.innerHTML = html;
}

function _renderLines(container) {
    const data = lyricsState.parsedData;
    let html = '';

    const hasTimestamped = data.groups.some(g => g.timestamp !== null);

    data.groups.forEach((group, idx) => {
        if (group.timestamp === null && hasTimestamped) return;
        const isActive = idx === lyricsState.activeGroupIndex;
        const hasSecondary = group.secondary !== null;

        html += `<div class="lyrics-group ${isActive ? 'active' : ''}" data-group-idx="${idx}" onclick="setActiveGroup(${idx})">`;

        html += `<div class="lyrics-group-timestamp">`;
        html += `<input type="text" class="lyrics-timestamp-input" value="${esc(group.timestampStr)}" 
            onchange="updateGroupTimestamp(${idx}, this.value)" 
            onclick="event.stopPropagation()">`;
        html += `</div>`;

        html += `<div class="lyrics-group-content">`;

        html += `<div class="lyrics-line-row lyrics-primary-row">`;
        html += `<span class="lyrics-role-badge primary"></span>`;
        html += `<input type="text" class="lyrics-text-input primary-text" value="${esc(group.primary.text)}" 
            onchange="updateGroupPrimaryText(${idx}, this.value)"
            onclick="event.stopPropagation()">`;
        if (hasSecondary) {
            html += `<button class="lyrics-line-action-btn lyrics-delete-inline-btn" onclick="removeLineFromGroup(${idx}, 'primary')" title="删除主行">
                <i class="bi bi-x"></i>
            </button>`;
        }
        html += `</div>`;

        if (hasSecondary) {
            html += `<div class="lyrics-line-row lyrics-secondary-row">`;
            html += `<span class="lyrics-role-badge secondary"></span>`;
            html += `<input type="text" class="lyrics-text-input secondary-text" value="${esc(group.secondary.text)}" 
                onchange="updateGroupSecondaryText(${idx}, this.value)"
                onclick="event.stopPropagation()">`;
            html += `<button class="lyrics-line-action-btn lyrics-delete-inline-btn" onclick="removeLineFromGroup(${idx}, 'secondary')" title="删除附行">
                <i class="bi bi-x"></i>
            </button>`;
            html += `</div>`;
        }

        html += `</div>`;

        html += `<div class="lyrics-group-actions">`;
        if (!hasSecondary) {
            html += `<button class="lyrics-line-action-btn lyrics-add-sub-btn" onclick="addSubLineToGroup(${idx})" title="添加附行（同时间戳）">
                <i class="bi bi-plus"></i>
            </button>`;
        }
        html += `<button class="lyrics-line-action-btn lyrics-add-after-btn" onclick="addGroupAfter(${idx})" title="在下方添加行">
            <i class="bi bi-arrow-down"></i>
        </button>`;
        html += `<button class="lyrics-line-action-btn lyrics-delete-group-btn" onclick="removeGroup(${idx})" title="删除整行">
            <i class="bi bi-x-lg"></i>
        </button>`;
        html += `</div>`;

        html += `</div>`;
    });

    if (data.groups.length === 0) {
        html = '<div class="lyrics-empty-hint">暂无歌词行，点击下方按钮添加</div>';
    }

    container.innerHTML = html;
}

function setActiveGroup(idx) {
    lyricsState.activeGroupIndex = idx;
    const container = document.getElementById('lyrics-editor-lines');
    container.querySelectorAll('.lyrics-group').forEach((el, i) => {
        el.classList.toggle('active', i === idx);
    });
}

function updateGroupTimestamp(idx, value) {
    const data = lyricsState.parsedData;
    if (!data || !data.groups[idx]) return;
    const ts = LrcParser.parseTimeStr(value);
    data.groups[idx].timestamp = ts;
    data.groups[idx].timestampStr = value;
    _saveToCache();
}

function updateGroupPrimaryText(idx, value) {
    const data = lyricsState.parsedData;
    if (!data || !data.groups[idx]) return;
    data.groups[idx].primary.text = value;
    _saveToCache();
}

function updateGroupSecondaryText(idx, value) {
    const data = lyricsState.parsedData;
    if (!data || !data.groups[idx]) return;
    if (data.groups[idx].secondary) {
        data.groups[idx].secondary.text = value;
    }
    _saveToCache();
}

function addSubLineToGroup(idx) {
    const data = lyricsState.parsedData;
    if (!data || !data.groups[idx]) return;
    data.groups[idx].secondary = { text: '', role: 'secondary' };
    _saveToCache();
    _renderLines(document.getElementById('lyrics-editor-lines'));
}

function removeLineFromGroup(idx, role) {
    const data = lyricsState.parsedData;
    if (!data || !data.groups[idx]) return;
    const group = data.groups[idx];

    if (role === 'primary' && group.secondary) {
        group.primary = { text: group.secondary.text, role: 'primary' };
        group.secondary = null;
    } else if (role === 'secondary') {
        group.secondary = null;
    } else if (role === 'primary' && !group.secondary) {
        removeGroup(idx);
        return;
    }

    _saveToCache();
    _renderLines(document.getElementById('lyrics-editor-lines'));
}

function addGroupAfter(idx) {
    const data = lyricsState.parsedData;
    if (!data) return;

    const currentGroup = data.groups[idx];
    let newTimestamp = null;
    let newTimestampStr = '';

    if (currentGroup && currentGroup.timestamp !== null) {
        newTimestamp = LrcParser.addOffset(currentGroup.timestamp, 0.1);
        newTimestampStr = LrcParser.formatTime(newTimestamp);
    }

    const newGroup = {
        timestamp: newTimestamp,
        timestampStr: newTimestampStr,
        primary: { text: '', role: 'primary' },
        secondary: null
    };

    data.groups.splice(idx + 1, 0, newGroup);
    _saveToCache();
    _renderLines(document.getElementById('lyrics-editor-lines'));
    setActiveGroup(idx + 1);
}

function removeGroup(idx) {
    const data = lyricsState.parsedData;
    if (!data || !data.groups[idx]) return;
    data.groups.splice(idx, 1);

    if (lyricsState.activeGroupIndex >= data.groups.length) {
        lyricsState.activeGroupIndex = data.groups.length - 1;
    }

    _saveToCache();
    _renderLines(document.getElementById('lyrics-editor-lines'));
}

function addNewGroupAtEnd() {
    const data = lyricsState.parsedData;
    if (!data) return;

    const lastGroup = data.groups[data.groups.length - 1];
    let newTimestamp = null;
    let newTimestampStr = '';

    if (lastGroup && lastGroup.timestamp !== null) {
        newTimestamp = LrcParser.addOffset(lastGroup.timestamp, 0.1);
        newTimestampStr = LrcParser.formatTime(newTimestamp);
    }

    data.groups.push({
        timestamp: newTimestamp,
        timestampStr: newTimestampStr,
        primary: { text: '', role: 'primary' },
        secondary: null
    });

    _saveToCache();
    _renderLines(document.getElementById('lyrics-editor-lines'));
    setActiveGroup(data.groups.length - 1);

    const container = document.getElementById('lyrics-editor-lines');
    container.scrollTop = container.scrollHeight;
}

function addMetadata() {
    const data = lyricsState.parsedData;
    if (!data) return;
    data.metadata.push({ key: '', value: '' });
    _saveToCache();
    _renderMetadata(document.getElementById('lyrics-editor-metadata'));
}

function removeMetadata(idx) {
    const data = lyricsState.parsedData;
    if (!data || !data.metadata[idx]) return;
    data.metadata.splice(idx, 1);
    _saveToCache();
    _renderMetadata(document.getElementById('lyrics-editor-metadata'));
}

function updateMetadataKey(idx, value) {
    const data = lyricsState.parsedData;
    if (!data || !data.metadata[idx]) return;
    data.metadata[idx].key = value;
    _saveToCache();
}

function updateMetadataValue(idx, value) {
    const data = lyricsState.parsedData;
    if (!data || !data.metadata[idx]) return;
    data.metadata[idx].value = value;
    _saveToCache();
}

function confirmLyricsEdit() {
    const data = lyricsState.parsedData;
    if (!data) {
        if (typeof window.onLyricsConfirmed === 'function') {
            window.onLyricsConfirmed('');
        }
        _unbindKeyboardShortcuts();
        _destroyAudioPlayer();
        _clearCache();
        closeModal('lyrics-editor-modal');
        return;
    }

    const metaRawEl = document.getElementById('lyrics-metadata-raw');
    const rawMeta = metaRawEl ? metaRawEl.value.trim() : '';

    if (rawMeta) {
        data.metadataRaw = rawMeta;
    }

    data.metadata = [];
    let newLyrics = LrcParser.serialize(data);

    if (rawMeta) {
        newLyrics = rawMeta + '\n' + newLyrics;
    }

    if (typeof window.onLyricsConfirmed === 'function') {
        window.onLyricsConfirmed(newLyrics);
    }

    _unbindKeyboardShortcuts();
    _destroyAudioPlayer();
    _clearCache();
    closeModal('lyrics-editor-modal');
}

function resetLyricsEdit() {
    lyricsState.parsedData = LrcParser.parse(lyricsState.initialLyrics);
    lyricsState.activeGroupIndex = -1;
    _saveToCache();
    _renderEditor();
    showToast('歌词已还原', 'info');
}

function _getCurrentLyricsText() {
    const data = lyricsState.parsedData;
    if (!data) return '';

    const metaRawEl = document.getElementById('lyrics-metadata-raw');
    const rawMeta = metaRawEl ? metaRawEl.value.trim() : '';
    if (rawMeta) {
        data.metadataRaw = rawMeta;
    }
    data.metadata = [];

    let text = LrcParser.serialize(data);
    if (rawMeta) {
        text = rawMeta + '\n' + text;
    }
    return text;
}

function copyLyricsToClipboard() {
    const text = _getCurrentLyricsText();
    if (!text) {
        showToast('歌词内容为空', 'warn');
        return;
    }
    navigator.clipboard.writeText(text).then(() => {
        showToast('歌词已复制到剪贴板', 'success');
    }).catch(() => {
        const ta = document.createElement('textarea');
        ta.value = text;
        ta.style.cssText = 'position:fixed;left:-9999px';
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        document.body.removeChild(ta);
        showToast('歌词已复制到剪贴板', 'success');
    });
}

function exportLrcFile() {
    const text = _getCurrentLyricsText();
    if (!text) {
        showToast('歌词内容为空', 'warn');
        return;
    }
    const track = lyricsState.track;
    const baseName = track ? (track.filename || track.title || 'lyrics').replace(/\.[^.]+$/, '') : 'lyrics';
    const lrcName = baseName + '.lrc';

    const overlay = document.createElement('div');
    overlay.className = 'modal-overlay open';
    overlay.style.zIndex = '10001';
    overlay.innerHTML = `
        <div class="modal" style="width:340px;padding:20px;">
            <div class="modal-title" style="margin-bottom:16px;">导出LRC文件</div>
            <div style="font-size:12px;color:var(--text2);margin-bottom:16px;">文件名：${esc(lrcName)}</div>
            <div style="display:flex;gap:8px;justify-content:flex-end;">
                <button class="toolbar-btn" id="lrc-export-browser">浏览器下载</button>
                <button class="toolbar-btn" id="lrc-export-server">保存到歌曲目录</button>
                <button class="toolbar-btn" id="lrc-export-cancel">取消</button>
            </div>
        </div>
    `;
    document.body.appendChild(overlay);

    overlay.querySelector('#lrc-export-browser').onclick = () => {
        const blob = new Blob([text], { type: 'text/plain;charset=utf-8' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = lrcName;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
        document.body.removeChild(overlay);
        showToast('LRC文件已下载', 'success');
    };

    overlay.querySelector('#lrc-export-server').onclick = async () => {
        if (!track || !track.id) {
            showToast('无法获取歌曲信息', 'error');
            return;
        }
        try {
            const result = await POST(`/tracks/${track.id}/export-lrc`, { lyrics: text });
            if (result.ok) {
                showToast(`已保存到歌曲目录：${result.path}`, 'success');
            } else {
                showToast('保存失败：' + (result.error || '未知错误'), 'error');
            }
        } catch (e) {
            showToast('保存失败：' + e.message, 'error');
        }
        document.body.removeChild(overlay);
    };

    overlay.querySelector('#lrc-export-cancel').onclick = () => {
        document.body.removeChild(overlay);
    };

    overlay.onclick = (e) => {
        if (e.target === overlay) document.body.removeChild(overlay);
    };
}

function showLyricsSearchInEditor() {
    const searchPanel = document.getElementById('lyrics-editor-search-panel');
    if (searchPanel.style.display === 'none') {
        searchPanel.style.display = 'block';
        searchLyricsInEditor();
    } else {
        searchPanel.style.display = 'none';
    }
}

async function searchLyricsInEditor() {
    if (!lyricsState.track) return;

    const keyword = `${lyricsState.track.title || ''} ${lyricsState.track.artist || ''}`.trim();
    if (!keyword) {
        document.getElementById('lyrics-editor-search-results').innerHTML = `
            <div class="scrape-empty-state"><div>无法获取歌曲信息</div></div>`;
        return;
    }

    const resultsEl = document.getElementById('lyrics-editor-search-results');
    resultsEl.innerHTML = `<div class="scrape-loading"><div class="loading-spinner"></div><div>正在搜索...</div></div>`;

    try {
        const cacheKey = keyword.toLowerCase();
        let results = lyricsState.searchCache[cacheKey];

        if (!results) {
            const data = await POST('/lyrics/search', { keyword });
            if (data.ok) {
                results = data.results;
                lyricsState.searchCache[cacheKey] = results;
            } else {
                throw new Error(data.error || '搜索失败');
            }
        }

        lyricsState.searchResults = results;
        renderLyricsSearchResultsInEditor(results);
    } catch (e) {
        resultsEl.innerHTML = `<div class="scrape-empty-state"><div>搜索失败: ${e.message}</div></div>`;
    }
}

function renderLyricsSearchResultsInEditor(results) {
    const container = document.getElementById('lyrics-editor-search-results');
    if (!results || results.length === 0) {
        container.innerHTML = `<div class="scrape-empty-state"><div>未找到匹配的歌词</div></div>`;
        return;
    }

    const sourceNames = { 'netease': '网易云' };
    let html = '';
    for (const result of results) {
        html += `<div class="lyrics-result-item" ondblclick="applyLyricsFromSearch('${result.id}')" title="双击应用歌词">
            <div class="lyrics-result-item-header">
                <span class="lyrics-result-source">${sourceNames[result.source] || result.source}</span>
            </div>
            <div class="lyrics-result-title">${esc(result.title || '未知歌曲')}</div>
            <div class="lyrics-result-artist">${esc(result.artist || '未知艺术家')}</div>
            <div class="lyrics-result-album">${esc(result.album || '')}</div>
        </div>`;
    }
    container.innerHTML = html;
}

async function applyLyricsFromSearch(songId) {
    const resultsEl = document.getElementById('lyrics-editor-search-results');
    resultsEl.innerHTML = `<div class="scrape-loading"><div class="loading-spinner"></div><div>正在获取歌词...</div></div>`;

    try {
        const data = await GET(`/lyrics/${songId}`);
        if (data.ok) {
            lyricsState.currentLyrics = data.lyrics;
            lyricsState.parsedData = LrcParser.parse(data.lyrics);
            lyricsState.activeGroupIndex = -1;

            document.getElementById('lyrics-editor-search-panel').style.display = 'none';
            _renderEditor();
            _saveToCache();
            showToast('歌词已应用', 'success');
        } else {
            throw new Error(data.error || '获取歌词失败');
        }
    } catch (e) {
        resultsEl.innerHTML = `<div class="scrape-empty-state"><div>获取歌词失败: ${e.message}</div></div>`;
    }
}

/* ═══════════════════════════════════════════════════════════
   AUDIO PLAYER
   ═══════════════════════════════════════════════════════════ */

function _initAudioPlayer() {
    const audio = document.getElementById('lyrics-audio');
    if (!audio) return;

    lyricsState.audioElement = audio;
    audio.volume = lyricsState.volume;

    audio.addEventListener('timeupdate', _onAudioTimeUpdate);
    audio.addEventListener('ended', () => {
        lyricsState.isPlaying = false;
        _updatePlayButton();
    });
    audio.addEventListener('loadedmetadata', () => {
        _updateTimeDisplay();
    });

    const volumeSlider = document.getElementById('lyrics-volume-slider');
    if (volumeSlider) {
        volumeSlider.value = lyricsState.volume;
    }
}

function _destroyAudioPlayer() {
    if (lyricsState.audioElement) {
        lyricsState.audioElement.pause();
        lyricsState.audioElement.removeAttribute('src');
        lyricsState.audioElement.load();
        lyricsState.audioElement.removeEventListener('timeupdate', _onAudioTimeUpdate);
    }
    lyricsState.isPlaying = false;
    lyricsState._audioTrackId = null;
    lyricsState._audioCurrentTime = 0;
}

function _onAudioTimeUpdate() {
    _updateTimeDisplay();
    _updateProgress();
}

function _updateTimeDisplay() {
    const audio = lyricsState.audioElement;
    if (!audio || !audio.duration) return;
    const current = document.getElementById('lyrics-time-current');
    const total = document.getElementById('lyrics-time-total');
    if (current) current.textContent = LrcParser.formatTime(audio.currentTime);
    if (total) total.textContent = LrcParser.formatTime(audio.duration);
}

function _updateProgress() {
    const audio = lyricsState.audioElement;
    if (!audio || !audio.duration) return;
    const bar = document.getElementById('lyrics-progress-fill');
    if (bar) {
        const pct = (audio.currentTime / audio.duration) * 100;
        bar.style.width = pct + '%';
    }
}

function _updatePlayButton() {
    const btn = document.getElementById('lyrics-play-btn');
    if (!btn) return;
    if (lyricsState.isPlaying) {
        btn.innerHTML = `<i class="bi bi-pause-fill"></i>`;
    } else {
        btn.innerHTML = `<i class="bi bi-play-fill"></i>`;
    }
}

function _loadAudioFromServer(trackId) {
    const audio = lyricsState.audioElement;
    if (!audio) return;

    lyricsState._audioTrackId = trackId;
    lyricsState._audioCurrentTime = 0;

    const label = document.getElementById('lyrics-audio-filename');
    if (label) label.textContent = '加载中...';

    audio.src = `/api/tracks/${trackId}/audio?token=${TOKEN}`;
    audio.load();

    audio.addEventListener('canplay', function onCanPlay() {
        audio.removeEventListener('canplay', onCanPlay);
        if (label) label.textContent = lyricsState.track ? (lyricsState.track.filename || '已加载') : '已加载';
        _updateTimeDisplay();
        _updateProgress();
    });

    audio.addEventListener('error', function onError() {
        audio.removeEventListener('error', onError);
        if (label) label.textContent = '加载失败';
    });
}

function togglePlay() {
    const audio = lyricsState.audioElement;
    if (!audio || !audio.src) {
        showToast('请先加载音频文件', 'info');
        return;
    }

    if (lyricsState.isPlaying) {
        audio.pause();
        lyricsState.isPlaying = false;
    } else {
        audio.play().catch(() => {
            showToast('播放失败', 'error');
        });
        lyricsState.isPlaying = true;
    }
    _updatePlayButton();
}

function seekForward() {
    const audio = lyricsState.audioElement;
    if (!audio || !audio.src) return;
    audio.currentTime = Math.min(audio.duration || 0, audio.currentTime + 5);
    _moveCursorToNearestTimestamp();
}

function seekBackward() {
    const audio = lyricsState.audioElement;
    if (!audio || !audio.src) return;
    audio.currentTime = Math.max(0, audio.currentTime - 5);
    _moveCursorToNearestTimestamp();
}

function setVolume(val) {
    lyricsState.volume = parseFloat(val);
    if (lyricsState.audioElement) {
        lyricsState.audioElement.volume = lyricsState.volume;
    }
}

function seekToPosition(e) {
    const audio = lyricsState.audioElement;
    if (!audio || !audio.duration) return;
    const bar = document.getElementById('lyrics-progress-bar');
    if (!bar) return;
    const rect = bar.getBoundingClientRect();
    const pct = (e.clientX - rect.left) / rect.width;
    audio.currentTime = pct * audio.duration;
    _moveCursorToNearestTimestamp();
}

/* ═══════════════════════════════════════════════════════════
   TIMING (打轴)
   ═══════════════════════════════════════════════════════════ */

function stampTimestamp() {
    const audio = lyricsState.audioElement;
    if (!audio || !audio.src) {
        showToast('请先加载音频文件', 'info');
        return;
    }

    const data = lyricsState.parsedData;
    if (!data || lyricsState.activeGroupIndex < 0 || lyricsState.activeGroupIndex >= data.groups.length) {
        showToast('请先选中一行歌词', 'info');
        return;
    }

    const currentTime = audio.currentTime;
    const group = data.groups[lyricsState.activeGroupIndex];
    group.timestamp = currentTime;
    group.timestampStr = LrcParser.formatTime(currentTime);

    _saveToCache();
    _renderLines(document.getElementById('lyrics-editor-lines'));

    if (lyricsState.activeGroupIndex < data.groups.length - 1) {
        setActiveGroup(lyricsState.activeGroupIndex + 1);
        _scrollToActiveGroup();
    }
}

function _moveCursorToNearestTimestamp() {
    const audio = lyricsState.audioElement;
    const data = lyricsState.parsedData;
    if (!audio || !data) return;

    const currentTime = audio.currentTime;
    let nearestIdx = -1;
    let nearestDiff = Infinity;

    data.groups.forEach((group, idx) => {
        if (group.timestamp !== null) {
            const diff = Math.abs(group.timestamp - currentTime);
            if (diff < nearestDiff) {
                nearestDiff = diff;
                nearestIdx = idx;
            }
        }
    });

    if (nearestIdx >= 0) {
        setActiveGroup(nearestIdx);
        _scrollToActiveGroup();
    }
}

function _scrollToActiveGroup() {
    const container = document.getElementById('lyrics-editor-lines');
    if (!container) return;
    const activeEl = container.querySelector('.lyrics-group.active');
    if (activeEl) {
        const containerRect = container.getBoundingClientRect();
        const elRect = activeEl.getBoundingClientRect();
        const elCenter = elRect.top + elRect.height / 2;
        const containerCenter = containerRect.top + containerRect.height / 2;
        const offset = elCenter - containerCenter;
        container.scrollTop += offset;
    }
}

/* ═══════════════════════════════════════════════════════════
   KEYBOARD SHORTCUTS
   ═══════════════════════════════════════════════════════════ */

let _keyHandler = null;

function _bindKeyboardShortcuts() {
    _keyHandler = _handleLyricsKeydown;
    document.addEventListener('keydown', _keyHandler, true);
}

function _unbindKeyboardShortcuts() {
    if (_keyHandler) {
        document.removeEventListener('keydown', _keyHandler, true);
        _keyHandler = null;
    }
}

function _handleLyricsKeydown(e) {
    const modal = document.getElementById('lyrics-editor-modal');
    if (!modal || !modal.classList.contains('open')) return;

    const tag = e.target.tagName.toLowerCase();
    const inInput = (tag === 'input' || tag === 'textarea');

    if (e.ctrlKey && e.key === 'Enter') {
        e.preventDefault();
        stampTimestamp();
        return;
    }

    if (e.ctrlKey && e.key === 'ArrowRight') {
        e.preventDefault();
        seekForward();
        return;
    }

    if (e.ctrlKey && e.key === 'ArrowLeft') {
        e.preventDefault();
        seekBackward();
        return;
    }

    if (e.ctrlKey && e.code === 'Space') {
        e.preventDefault();
        e.stopPropagation();
        togglePlay();
        return;
    }

    if (!e.ctrlKey && !e.altKey && e.key === 'ArrowUp') {
        if (inInput) {
            const input = e.target;
            if (input.selectionStart !== 0 || input.selectionEnd !== 0) return;
        }
        e.preventDefault();
        const data = lyricsState.parsedData;
        if (data && lyricsState.activeGroupIndex > 0) {
            setActiveGroup(lyricsState.activeGroupIndex - 1);
            _scrollToActiveGroup();
            _focusActiveGroupInput();
        }
        return;
    }

    if (!e.ctrlKey && !e.altKey && e.key === 'ArrowDown') {
        if (inInput) {
            const input = e.target;
            const len = input.value.length;
            if (input.selectionStart !== len || input.selectionEnd !== len) return;
        }
        e.preventDefault();
        const data = lyricsState.parsedData;
        if (data && lyricsState.activeGroupIndex < data.groups.length - 1) {
            setActiveGroup(lyricsState.activeGroupIndex + 1);
            _scrollToActiveGroup();
            _focusActiveGroupInput();
        }
        return;
    }
}

function _focusActiveGroupInput() {
    const container = document.getElementById('lyrics-editor-lines');
    if (!container) return;
    const activeEl = container.querySelector('.lyrics-group.active');
    if (!activeEl) return;
    const textInput = activeEl.querySelector('.lyrics-text-input');
    if (textInput) textInput.focus();
}
