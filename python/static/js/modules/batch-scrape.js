/**
 * batch-scrape.js — 批量搜索模块
 * 包含：批量搜索流程、层叠卡片组件、滑动交互、应用/换一个/移除操作
 * 依赖：api.js、state.js（TOKEN）、ui.js（openModal/closeModal/showToast）、utils.js
 */

const BATCH_SELECT_LIMIT = 20;

let batchCards = [];
let batchCurrentIndex = 0;
let batchSwipeState = null;
let batchApplying = false;
let batchEditingIndex = -1;

/* ═══════════════════════════════════════════════════════════
   OPEN BATCH SCRAPE MODAL
   ═══════════════════════════════════════════════════════════ */

async function openBatchScrapeModal(selectedPaths) {
  const body = document.getElementById('batch-scrape-body');

  body.innerHTML = `
    <div class="batch-loading">
      <div class="loading-spinner"></div>
      <div>正在解析选中内容并获取元数据...</div>
      <div class="batch-loading-sub" id="batch-loading-progress"></div>
    </div>
  `;
  openModal('batch-scrape-modal');

  try {
    const trackIds = await resolveTrackIds(selectedPaths);
    await runBatchScrape(trackIds);
  } catch (e) {
    body.innerHTML = `
      <div class="scrape-empty-state">
        <i class="bi bi-exclamation-circle" style="font-size: 24px;"></i>
        <div>请求出错: ${esc(e.message)}</div>
      </div>
    `;
  }
}

/**
 * 直接以 track id 列表发起批量搜索。
 * 待定文件多选等场景已持有 track id，无需再按路径解析。
 */
async function runBatchScrape(trackIds) {
  const body = document.getElementById('batch-scrape-body');

  if (!trackIds || trackIds.length === 0) {
    body.innerHTML = `
      <div class="scrape-empty-state">
        <i class="bi bi-emoji-frown" style="font-size: 24px;"></i>
        <div>所选内容中没有找到音频文件</div>
      </div>
    `;
    openModal('batch-scrape-modal');
    return;
  }

  body.innerHTML = `
    <div class="batch-loading">
      <div class="loading-spinner"></div>
      <div>正在获取元数据...</div>
      <div class="batch-loading-sub" id="batch-loading-progress"></div>
    </div>
  `;
  openModal('batch-scrape-modal');

  try {
    updateBatchProgress(0, trackIds.length);
    batchCards = [];

    const chunkSize = 5;
    for (let i = 0; i < trackIds.length; i += chunkSize) {
      const chunk = trackIds.slice(i, i + chunkSize);
      const result = await POST('/tracks/batch-scrape', { track_ids: chunk });

      if (result.ok) {
        for (const r of result.results) {
          if (r.ok && r.best) {
            batchCards.push({
              trackId: r.track_id,
              original: r.original,
              firstBest: { ...r.best },
              best: r.best,
              allResults: r.all_results,
              hasCover: r.has_cover,
              trackTitle: r.track_title,
              trackArtist: r.track_artist,
              trackAlbum: r.track_album,
              filename: r.filename,
              relativePath: r.relative_path || '',
              userInput: {},
              excludeIds: [r.best._id],
              applied: false,
              removed: false,
            });
          } else if (r.ok && !r.best) {
            batchCards.push({
              trackId: r.track_id,
              original: r.original,
              best: null,
              allResults: r.all_results || {},
              hasCover: r.has_cover,
              trackTitle: r.track_title,
              trackArtist: r.track_artist,
              trackAlbum: r.track_album,
              filename: r.filename,
              relativePath: r.relative_path || '',
              userInput: {},
              excludeIds: [],
              applied: false,
              removed: false,
              noResult: true,
            });
          }
        }
      }
      updateBatchProgress(Math.min(i + chunkSize, trackIds.length), trackIds.length);
    }

    if (batchCards.length === 0) {
      body.innerHTML = `
        <div class="scrape-empty-state">
          <i class="bi bi-emoji-frown" style="font-size: 24px;"></i>
          <div>未找到任何匹配的元数据</div>
        </div>
      `;
      return;
    }

    batchCurrentIndex = 0;
    renderBatchCards();

  } catch (e) {
    body.innerHTML = `
      <div class="scrape-empty-state">
        <i class="bi bi-exclamation-circle" style="font-size: 24px;"></i>
        <div>请求出错: ${esc(e.message)}</div>
      </div>
    `;
  }
}

function updateBatchProgress(current, total) {
  const el = document.getElementById('batch-loading-progress');
  if (el) {
    el.textContent = `${current} / ${total}`;
  }
}

async function resolveTrackIds(selectedPaths) {
  const trackIds = [];
  let totalAudioCount = 0;

  const dirPaths = [];
  const audioPaths = [];

  for (const path of selectedPaths) {
    const item = currentFiles.find(f => f.path === path);
    if (item && item.is_dir) {
      dirPaths.push(path);
    } else if (item && item.is_audio) {
      audioPaths.push(path);
    }
  }

  for (const path of audioPaths) {
    if (totalAudioCount >= BATCH_SELECT_LIMIT) break;
    try {
      const track = await GET(`/tracks/by-path?path=${encodeURIComponent(path)}`);
      if (track && track.id) {
        trackIds.push(track.id);
        totalAudioCount++;
      }
    } catch (_) { }
  }

  for (const dirPath of dirPaths) {
    if (totalAudioCount >= BATCH_SELECT_LIMIT) break;
    const remaining = BATCH_SELECT_LIMIT - totalAudioCount;
    try {
      const data = await GET(`/files?path=${dirPath}&limit=${remaining}&sort=name&recursive=true`);
      const audioItems = data.items || [];
      for (const item of audioItems) {
        if (totalAudioCount >= BATCH_SELECT_LIMIT) break;
        try {
          const track = await GET(`/tracks/by-path?path=${encodeURIComponent(item.path)}`);
          if (track && track.id) {
            trackIds.push(track.id);
            totalAudioCount++;
          }
        } catch (_) { }
      }
    } catch (_) { }
  }

  return trackIds;
}

/* ═══════════════════════════════════════════════════════════
   RENDER STACKED CARDS
   ═══════════════════════════════════════════════════════════ */

function renderBatchCards() {
  const body = document.getElementById('batch-scrape-body');
  const activeCards = batchCards.filter(c => !c.removed);

  if (activeCards.length === 0) {
    const appliedCount = batchCards.filter(c => c.applied).length;
    const skippedCount = batchCards.filter(c => c.removed && !c.applied).length;
    const parts = [];
    if (appliedCount > 0) parts.push(`已应用 ${appliedCount} 首`);
    if (skippedCount > 0) parts.push(`跳过 ${skippedCount} 首`);
    if (parts.length > 0) {
      showToast(parts.join('，'), 'success');
    }
    closeBatchScrapeModal();
    return;
  }

  if (batchCurrentIndex >= activeCards.length) {
    batchCurrentIndex = activeCards.length - 1;
  }

  const currentCard = activeCards[batchCurrentIndex];
  const cardIndex = batchCards.indexOf(currentCard);

  const visibleStack = [];
  for (let i = 0; i < Math.min(3, activeCards.length); i++) {
    const index = (batchCurrentIndex + i) % activeCards.length;
    visibleStack.push(activeCards[index]);
  }

  let cardsHtml = '';
  for (let i = visibleStack.length - 1; i >= 0; i--) {
    const card = visibleStack[i];
    const ci = batchCards.indexOf(card);
    const isTop = i === 0;
    const stackOffset = i * 6;
    const stackScale = 1 - i * 0.03;
    const stackOpacity = 1 - i * 0.15;

    cardsHtml += renderSingleCard(card, ci, isTop, stackOffset, stackScale, stackOpacity);
  }

  body.innerHTML = `
    <div class="batch-cards-container">
      <div class="batch-cards-stack" id="batch-cards-stack">
        ${cardsHtml}
      </div>
      <div class="batch-cards-counter" id="batch-cards-counter">
        ${batchCurrentIndex + 1} / ${activeCards.length}
      </div>
    </div>
  `;

  if (currentCard && !currentCard.noResult && batchEditingIndex === -1) {
    initBatchSwipe();
  }

  updateBatchFooter();
}

function batchApiLabel(api) {
  return { cloud: '网易云', qq: 'QQ音乐', kugou: '酷狗' }[api] || api || '';
}

function renderSingleCard(card, cardIndex, isTop, stackOffset, stackScale, stackOpacity) {
  const best = card.best;
  const original = card.original;
  const relativePath = card.relativePath;
  const isEditing = batchEditingIndex === cardIndex;

  if (card.noResult) {
    return `
      <div class="batch-card batch-card-no-result ${isTop ? 'batch-card-top' : 'batch-card-stacked'}"
           data-card-index="${cardIndex}"
           style="transform: translateY(${stackOffset}px) scale(${stackScale}); opacity: ${stackOpacity};">
        <div class="batch-card-cover">
          <i class="bi bi-disc" style="font-size: 48px;"></i>
        </div>
        <div class="batch-card-info">
          <div class="batch-card-filename">${esc(card.filename)}</div>
          <div class="batch-card-no-result-msg">
            <i class="bi bi-emoji-frown"></i>
            未找到匹配的元数据
          </div>
          <div class="batch-source-picker">
            <span class="batch-source-picker-hint">去音乐源搜索：</span>
            <button class="source-capsule-btn source-pick-btn" onclick="openSourceResults(${cardIndex}, 'cloud')">网易云</button>
            <button class="source-capsule-btn source-pick-btn" onclick="openSourceResults(${cardIndex}, 'qq')">QQ音乐</button>
            <button class="source-capsule-btn source-pick-btn" onclick="openSourceResults(${cardIndex}, 'kugou')">酷狗</button>
          </div>
          ${relativePath ? `
          <div class="batch-card-path">
            <i class="bi bi-folder-open"></i>
            <span class="batch-card-path-text">${esc(relativePath)}</span>
          </div>
          ` : ''}
        </div>
        <div class="batch-card-actions">
          <button class="toolbar-btn" onclick="removeBatchCard(${cardIndex})">
            <i class="bi bi-x"></i> 跳过
          </button>
        </div>
      </div>
    `;
  }

  const coverSrc = best._cover_data ? `data:image/jpeg;base64,${best._cover_data}` : '';
  const coverHtml = coverSrc
    ? `<img src="${coverSrc}" style="width:100%;height:100%;object-fit:cover;">`
    : `<i class="bi bi-disc" style="font-size: 48px;"></i>`;

  const apiLabel = batchApiLabel(best._api) || best._source || '';
  const apiClass = best._api || '';

  function fieldRow(label, fieldKey) {
    const origRaw = original[fieldKey];
    const hasOrig = origRaw != null && String(origRaw) !== '';
    const baselineVal = hasOrig ? String(origRaw) : (card.firstBest && card.firstBest[fieldKey] != null ? String(card.firstBest[fieldKey]) : '');
    const scraped = best[fieldKey] != null ? String(best[fieldKey]) : '';
    const userInputVal = card.userInput[fieldKey] || '';
    const effectiveVal = userInputVal || scraped;
    const isSameAsBaseline = baselineVal && effectiveVal && effectiveVal === baselineVal;
    const displayVal = userInputVal || scraped || baselineVal || '';
    const hasChange = effectiveVal && effectiveVal !== baselineVal;
    const isDifferent = hasChange && baselineVal;
    const isSame = isSameAsBaseline;

    if (isEditing) {
      return `
        <div class="batch-field batch-field-editing ${hasChange ? 'has-change' : ''} ${isDifferent ? 'has-original' : ''} ${isSame ? 'has-same' : ''}">
          <div class="batch-field-editing-header">
            <span class="batch-field-label">${label}</span>
            ${isDifferent ? `<span class="batch-field-original">${esc(baselineVal)}</span>` : ''}
          </div>
          <input type="text" 
                 class="batch-field-input" 
                 data-field="${fieldKey}" 
                 value="${esc(displayVal)}"
                 placeholder="${esc(label)}..."
                 oninput="updateBatchCardUserInput(${cardIndex}, '${fieldKey}', this.value)">
        </div>
      `;
    }

    return `
      <div class="batch-field ${hasChange ? 'has-change' : ''} ${isDifferent ? 'has-original' : ''} ${isSame ? 'has-same' : ''} ${userInputVal ? 'has-user-input' : ''}">
        <span class="batch-field-label">${label}</span>
        ${isDifferent ? `<span class="batch-field-original">${esc(baselineVal)}</span>` : ''}
        <span class="batch-field-value ${hasChange ? 'changed' : ''} ${userInputVal ? 'user-input' : ''}">${esc(displayVal || '—')}</span>
        ${isSame ? '<span class="batch-field-same-indicator"></span>' : ''}
        ${userInputVal ? '<span class="batch-field-user-indicator">✎</span>' : ''}
      </div>
    `;
  }

  const resetBtn = `<button class="toolbar-btn batch-btn-reset" onclick="resetBatchCard(${cardIndex})">
         <i class="bi bi-arrow-counterclockwise"></i> 重置
       </button>`;

  const editBtn = isEditing
    ? `<button class="toolbar-btn batch-btn-save-edit" onclick="saveBatchCardEdit(${cardIndex})">
         <i class="bi bi-check-lg"></i> 保存
       </button>`
    : `<button class="toolbar-btn batch-btn-edit" onclick="startBatchCardEdit(${cardIndex})">
         <i class="bi bi-pencil"></i> 编辑
       </button>`;

  const cancelBtn = isEditing
    ? `<button class="toolbar-btn batch-btn-cancel-edit" onclick="cancelBatchCardEdit(${cardIndex})">
         <i class="bi bi-x"></i> 取消
       </button>`
    : '';

  return `
    <div class="batch-card ${isTop ? 'batch-card-top' : 'batch-card-stacked'} ${isEditing ? 'batch-card-editing' : ''}"
         data-card-index="${cardIndex}"
         style="transform: translateY(${stackOffset}px) scale(${stackScale}); opacity: ${stackOpacity};">
       ${!isEditing ? `<div class="batch-card-cover">
         ${coverHtml}
       </div>` : ''}
      <div class="batch-card-info">
        <div class="batch-card-source">
          <button type="button" class="lyrics-source-capsule source-capsule-btn ${apiClass === 'cloud' ? 'netease' : apiClass}"
                  onclick="event.stopPropagation(); openSourceResults(${cardIndex}, '${apiClass}')"
                  title="查看 ${apiLabel} 的全部搜索结果">
            <span class="capsule-primary"></span>
            <span class="capsule-secondary"></span>
            <span class="capsule-text">${apiLabel}</span>
          </button>
        </div>
        ${fieldRow('歌名', 'title')}
        ${fieldRow('艺术家', 'artist')}
        ${fieldRow('专辑', 'album')}
        ${fieldRow('专辑艺术家', 'album_artist')}
        ${fieldRow('音轨号', 'track_num')}
        ${fieldRow('年份', 'year')}
        ${!isEditing && relativePath ? `
        <div class="batch-card-path">
          <i class="bi bi-folder-open"></i>
          <span class="batch-card-path-text" title="${esc(relativePath)}">${esc(relativePath)}</span>
        </div>
        ` : ''}
      </div>
      <div class="batch-card-actions">
        ${editBtn}
        ${cancelBtn}
        ${resetBtn}
        ${!isEditing ? `
        <button class="toolbar-btn batch-btn-apply" onclick="applyBatchCard(${cardIndex})" ${card.applied ? 'disabled' : ''}>
          <i class="bi bi-check-lg"></i> 应用
        </button>
        <button class="toolbar-btn batch-btn-try" onclick="tryAnotherBatchCard(${cardIndex})">
          <i class="bi bi-arrow-clockwise"></i> 换一个
        </button>
        <button class="toolbar-btn batch-btn-retry" onclick="retrySearchBatchCard(${cardIndex})">
          <i class="bi bi-search"></i> 重新搜索
        </button>
        <button class="toolbar-btn" onclick="removeBatchCard(${cardIndex})">
          <i class="bi bi-x"></i>
        </button>
        ` : ''}
      </div>
    </div>
  `;
}

/* ═══════════════════════════════════════════════════════════
   SWIPE INTERACTION
   ═══════════════════════════════════════════════════════════ */

let _batchSwipeCleanup = null;

function initBatchSwipe() {
  if (_batchSwipeCleanup) {
    _batchSwipeCleanup();
    _batchSwipeCleanup = null;
  }

  const topCard = document.querySelector('.batch-card-top');
  if (!topCard) return;

  let startX = 0;
  let startY = 0;
  let currentX = 0;
  let isDragging = false;
  let startTime = 0;

  function onStart(e) {
    const card = e.target.closest('.batch-card-top');
    if (!card) return;
    if (e.target.closest('.batch-card-actions')) return;
    if (e.target.closest('.source-capsule-btn')) return;

    isDragging = true;
    startTime = Date.now();
    const point = e.touches ? e.touches[0] : e;
    startX = point.clientX;
    startY = point.clientY;
    currentX = 0;
    topCard.style.transition = 'none';
  }

  function onMove(e) {
    if (!isDragging) return;
    const point = e.touches ? e.touches[0] : e;
    currentX = point.clientX - startX;
    const currentY = point.clientY - startY;

    if (Math.abs(currentX) < Math.abs(currentY) && Math.abs(currentX) < 10) {
      return;
    }

    e.preventDefault();

    const rotation = currentX * 0.08;
    const opacity = Math.max(0.5, 1 - Math.abs(currentX) / 400);
    topCard.style.transform = `translateX(${currentX}px) rotate(${rotation}deg)`;
    topCard.style.opacity = opacity;
  }

  function onEnd(e) {
    if (!isDragging) return;
    isDragging = false;

    const elapsed = Date.now() - startTime;
    const velocity = Math.abs(currentX) / elapsed;

    if (Math.abs(currentX) > 120 || velocity > 0.5) {
      const direction = currentX > 0 ? 1 : -1;
      topCard.style.transition = 'transform 0.3s ease, opacity 0.3s ease';
      topCard.style.transform = `translateX(${direction * 400}px) rotate(${direction * 15}deg)`;
      topCard.style.opacity = '0.5';

      setTimeout(() => {
        navigateBatchCard(direction);
      }, 300);
    } else {
      topCard.style.transition = 'transform 0.3s cubic-bezier(0.175, 0.885, 0.32, 1.275), opacity 0.3s ease';
      topCard.style.transform = 'translateX(0) rotate(0deg)';
      topCard.style.opacity = '1';
    }
  }

  topCard.addEventListener('mousedown', onStart);
  topCard.addEventListener('touchstart', onStart, { passive: true });
  document.addEventListener('mousemove', onMove);
  document.addEventListener('touchmove', onMove, { passive: false });
  document.addEventListener('mouseup', onEnd);
  document.addEventListener('touchend', onEnd);

  _batchSwipeCleanup = () => {
    topCard.removeEventListener('mousedown', onStart);
    topCard.removeEventListener('touchstart', onStart);
    document.removeEventListener('mousemove', onMove);
    document.removeEventListener('mouseup', onEnd);
    document.removeEventListener('touchmove', onMove);
    document.removeEventListener('touchend', onEnd);
  };
}

function cleanupSwipe() {
  if (_batchSwipeCleanup) {
    _batchSwipeCleanup();
    _batchSwipeCleanup = null;
  }
}

function navigateBatchCard(direction) {
  const activeCards = batchCards.filter(c => !c.removed);
  if (activeCards.length === 0) return;

  if (direction === 1) {
    batchCurrentIndex = (batchCurrentIndex - 1 + activeCards.length) % activeCards.length;
  } else {
    batchCurrentIndex = (batchCurrentIndex + 1) % activeCards.length;
  }

  renderBatchCards();
}

/* ═══════════════════════════════════════════════════════════
   EDIT MODE FUNCTIONS
   ═══════════════════════════════════════════════════════════ */

function startBatchCardEdit(cardIndex) {
  cleanupSwipe();
  batchEditingIndex = cardIndex;
  const card = batchCards[cardIndex];
  if (card) {
    card._savedUserInput = { ...card.userInput };
  }
  renderBatchCards();

  setTimeout(() => {
    const firstInput = document.querySelector('.batch-field-input');
    if (firstInput) {
      firstInput.focus();
    }
  }, 100);
}

function cancelBatchCardEdit(cardIndex) {
  const card = batchCards[cardIndex];
  if (card && card._savedUserInput) {
    card.userInput = { ...card._savedUserInput };
    delete card._savedUserInput;
  }
  batchEditingIndex = -1;
  renderBatchCards();
}

function updateBatchCardUserInput(cardIndex, fieldKey, value) {
  const card = batchCards[cardIndex];
  if (card) {
    const trimmed = value.trim();
    const origRaw = card.original[fieldKey];
    const hasOrig = origRaw != null && String(origRaw) !== '';
    const baselineVal = hasOrig ? String(origRaw) : (card.firstBest && card.firstBest[fieldKey] != null ? String(card.firstBest[fieldKey]) : '');
    if (trimmed && trimmed !== baselineVal) {
      card.userInput[fieldKey] = trimmed;
    } else {
      delete card.userInput[fieldKey];
    }
  }
}

async function saveBatchCardEdit(cardIndex) {
  if (batchApplying) return;
  const card = batchCards[cardIndex];
  if (!card) return;

  if (card._savedUserInput) {
    delete card._savedUserInput;
  }
  batchEditingIndex = -1;
  renderBatchCards();
  showToast('已保存', 'success');
}

function resetBatchCard(cardIndex) {
  const card = batchCards[cardIndex];
  if (!card) return;

  const tagKeys = ['title', 'artist', 'album', 'album_artist', 'track_num', 'year'];
  const hasOriginalTags = tagKeys.some(k => card.original[k] != null && String(card.original[k]) !== '');

  card.userInput = {};
  if (hasOriginalTags) {
    card.best = card.firstBest ? { ...card.firstBest } : null;
  } else if (card.firstBest) {
    card.best = { ...card.firstBest };
  }

  if (card._savedUserInput) {
    delete card._savedUserInput;
  }
  batchEditingIndex = -1;
  renderBatchCards();
  showToast('已重置', 'info');
}

/* ═══════════════════════════════════════════════════════════
   CARD ACTIONS
   ═══════════════════════════════════════════════════════════ */

function isCardEffectivelyUnchanged(card) {
  const fields = ['title', 'artist', 'album', 'album_artist', 'track_num', 'year'];
  for (const key of fields) {
    const effective = getBatchCardEffectiveVal(card, key);
    const origRaw = card.original[key];
    const origVal = (origRaw != null && String(origRaw) !== '') ? String(origRaw) : '';
    if (effective !== origVal) return false;
  }
  return true;
}

async function applyBatchCard(cardIndex) {
  if (batchApplying) return;
  const card = batchCards[cardIndex];
  if (!card || card.applied || card.noResult) return;

  if (isCardEffectivelyUnchanged(card)) {
    card.applied = true;
    showToast(`无变更，已跳过: ${card.trackTitle || card.filename}`, 'info');
    animateCardOut(cardIndex);
    return;
  }

  batchApplying = true;
  try {
    const applyData = { ...card.best };

    if (card.userInput.title) {
      applyData.title = card.userInput.title;
    }
    if (card.userInput.artist) {
      applyData.artist = card.userInput.artist;
    }
    if (card.userInput.album) {
      applyData.album = card.userInput.album;
    }
    if (card.userInput.album_artist) {
      applyData.album_artist = card.userInput.album_artist;
    }
    if (card.userInput.track_num) {
      applyData.track_num = card.userInput.track_num;
    }
    if (card.userInput.year) {
      applyData.year = card.userInput.year;
    }

    await POST(`/tracks/${card.trackId}/apply-scrape`, applyData);
    card.applied = true;
    showToast(`已应用: ${card.trackTitle || card.filename}`, 'success');

    animateCardOut(cardIndex);
  } catch (e) {
    showToast(`应用失败: ${e.message}`, 'error');
  } finally {
    batchApplying = false;
  }
}

function getBatchCardEffectiveVal(card, fieldKey) {
  if (card.userInput[fieldKey] != null && String(card.userInput[fieldKey]) !== '') {
    return String(card.userInput[fieldKey]);
  }
  if (card.best && card.best[fieldKey] != null && String(card.best[fieldKey]) !== '') {
    return String(card.best[fieldKey]);
  }
  if (card.original && card.original[fieldKey] != null && String(card.original[fieldKey]) !== '') {
    return String(card.original[fieldKey]);
  }
  return '';
}

async function tryAnotherBatchCard(cardIndex) {
  if (batchApplying) return;
  const card = batchCards[cardIndex];
  if (!card || card.noResult) return;

  batchApplying = true;
  const btn = document.querySelector(`.batch-card[data-card-index="${cardIndex}"] .batch-btn-try`);
  if (btn) {
    btn.disabled = true;
    btn.classList.add('loading');
  }

  try {
    const excludeIds = card.excludeIds.slice(-10);
    const requestData = { exclude_ids: excludeIds };
    if (card.userInput.title) requestData.title = card.userInput.title;
    if (card.userInput.artist) requestData.artist = card.userInput.artist;
    if (card.userInput.album) requestData.album = card.userInput.album;
    if (card.userInput.track_num) requestData.track_num = card.userInput.track_num;
    if (card.userInput.year) requestData.year = card.userInput.year;
    const result = await POST(`/tracks/${card.trackId}/scrape-all`, requestData);

    if (result.ok) {
      const allItems = [];
      for (const [api, items] of Object.entries(result.results)) {
        for (const item of items) {
          item._api = api;
          allItems.push(item);
        }
      }
      allItems.sort((a, b) => (b._match_score || 0) - (a._match_score || 0));

      if (allItems.length > 0) {
        const newBest = allItems[0];
        for (const key of Object.keys(card.userInput)) {
          if (newBest[key] != null && String(newBest[key]) !== '') {
            delete card.userInput[key];
          }
        }
        card.best = newBest;
        card.excludeIds.push(newBest._id);
        if (card.excludeIds.length > 10) {
          card.excludeIds = card.excludeIds.slice(-10);
        }
        renderBatchCards();
        showToast('已获取新的结果', 'success');
      } else {
        showToast('没有更多结果了', 'info');
      }
    }
  } catch (e) {
    showToast(`刷新失败: ${e.message}`, 'error');
  } finally {
    batchApplying = false;
    if (btn) {
      btn.disabled = false;
      btn.classList.remove('loading');
    }
  }
}

async function retrySearchBatchCard(cardIndex) {
  if (batchApplying) return;
  const card = batchCards[cardIndex];
  if (!card || card.noResult) return;

  batchApplying = true;
  const btn = document.querySelector(`.batch-card[data-card-index="${cardIndex}"] .batch-btn-retry`);
  if (btn) {
    btn.disabled = true;
    btn.classList.add('loading');
  }

  try {
    const requestData = { exclude_ids: [] };
    if (card.userInput.title) requestData.title = card.userInput.title;
    if (card.userInput.artist) requestData.artist = card.userInput.artist;
    if (card.userInput.album) requestData.album = card.userInput.album;
    if (card.userInput.track_num) requestData.track_num = card.userInput.track_num;
    if (card.userInput.year) requestData.year = card.userInput.year;
    const result = await POST(`/tracks/${card.trackId}/scrape-all`, requestData);

    if (result.ok) {
      const allItems = [];
      for (const [api, items] of Object.entries(result.results)) {
        for (const item of items) {
          item._api = api;
          allItems.push(item);
        }
      }
      allItems.sort((a, b) => (b._match_score || 0) - (a._match_score || 0));

      if (allItems.length > 0) {
        const newBest = allItems[0];
        for (const key of Object.keys(card.userInput)) {
          if (newBest[key] != null && String(newBest[key]) !== '') {
            delete card.userInput[key];
          }
        }
        card.best = newBest;
        card.excludeIds = [newBest._id];
        renderBatchCards();
        showToast('已重新搜索', 'success');
      } else {
        showToast('未找到匹配结果', 'info');
      }
    }
  } catch (e) {
    showToast(`重新搜索失败: ${e.message}`, 'error');
  } finally {
    batchApplying = false;
    if (btn) {
      btn.disabled = false;
      btn.classList.remove('loading');
    }
  }
}

/* ═══════════════════════════════════════════════════════════
   SOURCE PICKER — 点击胶囊查看该音乐源的全部候选并挑选
   ═══════════════════════════════════════════════════════════ */

let sourceResultsState = null; // { cardIndex, api, results }

async function openSourceResults(cardIndex, api) {
  const card = batchCards[cardIndex];
  if (!card || !api) return;

  sourceResultsState = { cardIndex, api, results: [] };

  const titleEl = document.getElementById('source-results-title');
  if (titleEl) titleEl.textContent = `${batchApiLabel(api)} 搜索结果`;

  const body = document.getElementById('source-results-body');
  body.innerHTML = `
    <div class="batch-loading">
      <div class="loading-spinner"></div>
      <div>正在从 ${batchApiLabel(api)} 搜索…</div>
    </div>`;
  openModal('source-results-modal');

  try {
    const payload = { source: api, exclude_ids: [] };
    for (const key of ['title', 'artist', 'album', 'track_num', 'year']) {
      if (card.userInput[key]) payload[key] = card.userInput[key];
    }
    const result = await POST(`/tracks/${card.trackId}/scrape-source`, payload);
    if (!result.ok) {
      body.innerHTML = `
        <div class="scrape-empty-state">
          <i class="bi bi-exclamation-circle" style="font-size: 24px;"></i>
          <div>搜索失败: ${esc(result.error || '未知错误')}</div>
        </div>`;
      return;
    }
    sourceResultsState.results = result.results || [];
    renderSourceResults();
  } catch (e) {
    body.innerHTML = `
      <div class="scrape-empty-state">
        <i class="bi bi-exclamation-circle" style="font-size: 24px;"></i>
        <div>请求出错: ${esc(e.message)}</div>
      </div>`;
  }
}

function renderSourceResults() {
  const st = sourceResultsState;
  if (!st) return;
  const body = document.getElementById('source-results-body');

  if (!st.results || st.results.length === 0) {
    body.innerHTML = `
      <div class="scrape-empty-state">
        <i class="bi bi-search" style="font-size: 24px;"></i>
        <div>该音乐源没有找到更多结果</div>
      </div>`;
    return;
  }

  const card = batchCards[st.cardIndex];
  const currentId = card && card.best ? card.best._id : null;

  body.innerHTML = st.results.map((r, i) => {
    const cover = r._cover_data
      ? `<img src="data:image/jpeg;base64,${r._cover_data}" alt="">`
      : '<i class="bi bi-disc"></i>';
    const isCurrent = currentId && r._id === currentId;
    const meta = [
      r.album ? esc(r.album) : '',
      r.year ? esc(String(r.year)) : '',
      r.track_num ? `第 ${esc(String(r.track_num))} 首` : '',
    ].filter(Boolean).join(' · ');
    return `
      <div class="source-result-item${isCurrent ? ' active' : ''}" onclick="applySourceResult(${i})">
        <div class="source-result-cover">${cover}</div>
        <div class="source-result-info">
          <div class="source-result-title">${esc(r.title || '—')}</div>
          <div class="source-result-line">${esc(r.artist || '—')}</div>
          <div class="source-result-line source-result-sub">${meta || '—'}</div>
        </div>
        <div class="source-result-action">
          ${isCurrent
            ? '<span class="source-result-current">当前</span>'
            : '<span class="source-result-pick">选择</span>'}
        </div>
      </div>`;
  }).join('');
}

function applySourceResult(index) {
  const st = sourceResultsState;
  if (!st || !st.results || !st.results[index]) return;
  const card = batchCards[st.cardIndex];
  if (!card) return;

  const picked = st.results[index];
  // 手动挑选后清掉会被所选结果覆盖的手工输入项
  for (const key of Object.keys(card.userInput)) {
    if (picked[key] != null && String(picked[key]) !== '') {
      delete card.userInput[key];
    }
  }
  card.best = picked;
  card.noResult = false;
  card.applied = false;
  card.removed = false;
  if (picked._id) card.excludeIds = [picked._id];

  closeModal('source-results-modal');
  sourceResultsState = null;
  renderBatchCards();
  showToast('已选择该结果', 'success');
}

function removeBatchCard(cardIndex) {
  cleanupSwipe();
  const card = batchCards[cardIndex];
  if (card) {
    card.removed = true;
  }
  renderBatchCards();
}

function animateCardOut(cardIndex) {
  const cardEl = document.querySelector(`.batch-card[data-card-index="${cardIndex}"]`);
  if (cardEl) {
    cleanupSwipe();
    cardEl.style.transition = 'transform 0.35s ease, opacity 0.35s ease';
    cardEl.style.transform = 'translateY(-30px) scale(0.9)';
    cardEl.style.opacity = '0';
    setTimeout(() => {
      batchCards[cardIndex].removed = true;
      renderBatchCards();
    }, 300);
  } else {
    batchCards[cardIndex].removed = true;
    renderBatchCards();
  }
}

/* ═══════════════════════════════════════════════════════════
   APPLY ALL
   ═══════════════════════════════════════════════════════════ */

async function applyAllBatchCards() {
  if (batchApplying) return;
  const activeCards = batchCards.filter(c => !c.removed && !c.applied && !c.noResult);
  if (activeCards.length === 0) {
    showToast('没有可应用的卡片', 'info');
    return;
  }

  batchApplying = true;
  const applyAllBtn = document.getElementById('batch-apply-all-btn');
  if (applyAllBtn) {
    applyAllBtn.disabled = true;
    applyAllBtn.classList.add('loading');
  }

  let successCount = 0;
  let skipCount = 0;
  let failCount = 0;

  for (const card of activeCards) {
    try {
      if (isCardEffectivelyUnchanged(card)) {
        card.applied = true;
        card.removed = true;
        skipCount++;
        continue;
      }
      const applyData = { ...card.best };
      for (const key of ['title', 'artist', 'album', 'album_artist', 'track_num', 'year']) {
        if (card.userInput[key]) {
          applyData[key] = card.userInput[key];
        }
      }
      await POST(`/tracks/${card.trackId}/apply-scrape`, applyData);
      card.applied = true;
      card.removed = true;
      successCount++;
    } catch (e) {
      failCount++;
    }
  }

  batchApplying = false;
  if (applyAllBtn) {
    applyAllBtn.disabled = false;
    applyAllBtn.classList.remove('loading');
  }

  const parts = [];
  if (successCount > 0) parts.push(`已应用 ${successCount} 首`);
  if (skipCount > 0) parts.push(`跳过 ${skipCount} 首（无变更）`);
  if (failCount > 0) parts.push(`${failCount} 首失败`);
  if (parts.length > 0) {
    showToast(parts.join('，'), successCount > 0 ? 'success' : 'info');
  }

  closeBatchScrapeModal();
}

/* ═══════════════════════════════════════════════════════════
   FOOTER & CLOSE
   ═══════════════════════════════════════════════════════════ */

function updateBatchFooter() {
  const activeCards = batchCards.filter(c => !c.removed && !c.applied && !c.noResult);
  const applyAllBtn = document.getElementById('batch-apply-all-btn');
  if (applyAllBtn) {
    applyAllBtn.disabled = activeCards.length === 0;
    applyAllBtn.textContent = activeCards.length > 0 ? `全部应用 (${activeCards.length})` : '全部应用';
  }
}

function closeBatchScrapeModal() {
  cleanupSwipe();
  batchCards = [];
  batchCurrentIndex = 0;
  batchApplying = false;
  batchEditingIndex = -1;
  closeModal('batch-scrape-modal');

  if (typeof fileSelectMode !== 'undefined' && fileSelectMode) {
    fileSelectedPaths.clear();
    fileSelectMode = false;
    renderFiles(currentFiles);
    updateFileSelectUI();
  }

  // 待定文件批量搜索结束后退出多选并刷新列表（已补全的文件应消失，角标同步更新）
  if (typeof pendingSelectMode !== 'undefined' && pendingSelectMode) {
    pendingSelectMode = false;
    pendingSelectedIds.clear();
    if (typeof loadPending === 'function') loadPending();
  }
}
