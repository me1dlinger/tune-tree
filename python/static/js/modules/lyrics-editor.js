
/**
 * lyrics-editor.js - Lyrics search and edit module
 * Dependencies: api.js, ui.js
 */

let lyricsState = {
    track: null,
    currentLyrics: '',
    searchResults: [],
    searchCache: {}
};

function openLyricsEditorModal(trackOrLyrics, lyrics = null) {
    if (typeof trackOrLyrics === 'object' && trackOrLyrics !== null) {
        lyricsState.track = trackOrLyrics;
        lyricsState.currentLyrics = trackOrLyrics.lyrics || '';
    } else {
        lyricsState.currentLyrics = trackOrLyrics || '';
    }

    const modal = document.getElementById('lyrics-editor-modal');
    const textarea = document.getElementById('lyrics-editor-textarea');
    const infoEl = document.getElementById('lyrics-editor-info');
    const searchPanel = document.getElementById('lyrics-editor-search-panel');

    textarea.value = lyricsState.currentLyrics;
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

    openModal('lyrics-editor-modal');
}

function closeLyricsEditorModal() {
    closeModal('lyrics-editor-modal');
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
            <div class="scrape-empty-state">
                <div>无法获取歌曲信息</div>
            </div>
        `;
        return;
    }

    const resultsEl = document.getElementById('lyrics-editor-search-results');
    resultsEl.innerHTML = `
        <div class="scrape-loading">
            <div class="loading-spinner"></div>
            <div>正在搜索...</div>
        </div>
    `;

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
        resultsEl.innerHTML = `
            <div class="scrape-empty-state">
                <div>搜索失败: ${e.message}</div>
            </div>
        `;
    }
}

function renderLyricsSearchResultsInEditor(results) {
    const container = document.getElementById('lyrics-editor-search-results');

    if (!results || results.length === 0) {
        container.innerHTML = `
            <div class="scrape-empty-state">
                <div>未找到匹配的歌词</div>
            </div>
        `;
        return;
    }

    const sourceNames = {
        'netease': '网易云'
    };

    let html = '';
    for (const result of results) {
        html += `
            <div class="lyrics-result-item" ondblclick="applyLyricsFromSearch('${result.id}')" title="双击应用歌词">
                <div class="lyrics-result-item-header">
                    <span class="lyrics-result-source">${sourceNames[result.source] || result.source}</span>
                </div>
                <div class="lyrics-result-title">${esc(result.title || '未知歌曲')}</div>
                <div class="lyrics-result-artist">${esc(result.artist || '未知艺术家')}</div>
                <div class="lyrics-result-album">${esc(result.album || '')}</div>
            </div>
        `;
    }

    container.innerHTML = html;
}

async function applyLyricsFromSearch(songId) {
    const resultsEl = document.getElementById('lyrics-editor-search-results');
    resultsEl.innerHTML = `
        <div class="scrape-loading">
            <div class="loading-spinner"></div>
            <div>正在获取歌词...</div>
        </div>
    `;

    try {
        const data = await GET(`/lyrics/${songId}`);
        if (data.ok) {
            const textarea = document.getElementById('lyrics-editor-textarea');
            textarea.value = data.lyrics;
            lyricsState.currentLyrics = data.lyrics;

            const searchPanel = document.getElementById('lyrics-editor-search-panel');
            searchPanel.style.display = 'none';

            showToast('歌词已应用', 'success');
        } else {
            throw new Error(data.error || '获取歌词失败');
        }
    } catch (e) {
        resultsEl.innerHTML = `
            <div class="scrape-empty-state">
                <div>获取歌词失败: ${e.message}</div>
            </div>
        `;
    }
}

function confirmLyricsEdit() {
    const textarea = document.getElementById('lyrics-editor-textarea');
    const newLyrics = textarea.value;

    if (typeof window.onLyricsConfirmed === 'function') {
        window.onLyricsConfirmed(newLyrics);
    }

    closeLyricsEditorModal();
}

