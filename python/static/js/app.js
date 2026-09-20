/**
 * app.js — 应用入口
 * 负责：应用初始化、全局扫描操作。
 * 依赖：所有其他模块（需最后加载）
 */

/* ═══════════════════════════════════════════════════════════
   INIT
═══════════════════════════════════════════════════════════ */

async function loadCurrentLibrary() {
  try {
    currentLibrary = await GET('/libraries/current');
  } catch (e) {
    currentLibrary = null;
  }
}

async function checkScanStatus() {
  try {
    const r = await GET('/scan/status');
    if (r.scanning) {
      setScanningUI(true, r.elapsed_seconds);
      // 页面加载时若扫描仍在进行，跟随轮询直到结束（不阻塞首屏初始化）
      followScanUntilDone();
    } else if (r.timed_out) {
      showToast('扫描已超时，可重新扫描', 'warning');
    }
  } catch (e) {
  }
}

/**
 * 登录成功后初始化各页面数据
 * 由 auth.js 的 showApp() 调用
 */
async function initApp() {
  await loadCurrentLibrary();
  await checkScanStatus();
  await loadArtistTree();

  // 自动选择第一位艺术家并显示专辑信息
  if (allArtists && allArtists.length > 0) {
    const sortedArtists = getSortedArtists(allArtists);
    const firstArtistId = sortedArtists[0].id;
    // 等待艺术家视图加载完成后再加载其他页面数据
    await selectArtistFromId(firstArtistId);
  }

  // 各页面数据互相独立，并行加载以缩短首屏等待
  await Promise.all([
    loadFiles(''),
    loadStats(),
    loadPending(),
    loadLogs(),
  ]);
}

/* ═══════════════════════════════════════════════════════════
   SCAN
═══════════════════════════════════════════════════════════ */

/**
 * 扫描中 UI 的完整刷新。
 * @param {boolean} scanning
 * @param {number} [elapsedSeconds]
 * @param {number|null} [heartbeatAgeSeconds] 心跳失联秒数（来自 /scan/status）
 * @param {boolean} [cancelling] 正在请求取消中
 */
function setScanningUI(scanning, elapsedSeconds = 0, heartbeatAgeSeconds = null, cancelling = false) {
  isScanning = scanning;
  const btn = document.getElementById('scan-btn');
  const statusEl = document.getElementById('scan-status');
  const statusText = document.getElementById('scan-status-text');
  const cancelBtn = document.getElementById('scan-cancel-btn');

  if (btn) btn.disabled = scanning || cancelling;
  if (statusEl) statusEl.style.display = scanning ? 'flex' : 'none';
  if (cancelBtn) {
    cancelBtn.style.display = scanning && cancelling ? 'none' : (scanning ? '' : 'none');
    cancelBtn.disabled = cancelling;
  }

  if (statusText && scanning) {
    const hours = Math.floor(elapsedSeconds / 3600);
    const minutes = Math.floor((elapsedSeconds % 3600) / 60);
    const seconds = elapsedSeconds % 60;
    let timeStr = '';
    if (hours > 0) timeStr += `${hours}小时`;
    if (minutes > 0 || hours > 0) timeStr += `${minutes}分钟`;
    timeStr += `${seconds}秒`;

    let extra = '';
    if (cancelling) {
      extra = ' · 正在停止…';
    } else if (heartbeatAgeSeconds != null && heartbeatAgeSeconds > 90) {
      extra = ` · 停留 ${formatHeartbeat(heartbeatAgeSeconds)}，可能已卡住，可点击停止`;
    }
    statusText.textContent = `扫描中... ${timeStr}${extra}`;

    // 心跳失联时给状态条加警示样式
    const stale = heartbeatAgeSeconds != null && heartbeatAgeSeconds > 90;
    if (statusEl) statusEl.classList.toggle('scan-stale', stale);
  }
}

function formatHeartbeat(sec) {
  if (sec >= 60) return `${Math.floor(sec / 60)}分${sec % 60}秒`;
  return `${sec}秒`;
}

/**
 * 轮询 /scan/status，直到扫描结束。
 * @param {(status: object) => void} [onTick] 仍在扫描时每次回调
 * @returns {Promise<object|null>} 结束时的状态对象（含 last_result）
 */
function pollScanUntilDone(onTick) {
  return new Promise((resolve) => {
    const tick = async () => {
      let r;
      try {
        r = await GET('/scan/status');
      } catch (e) {
        // 网络抖动：稍后重试，避免误判为已结束
        setTimeout(tick, 3000);
        return;
      }
      if (r.scanning) {
        if (typeof onTick === 'function') onTick(r);
        setTimeout(tick, 1000);
      } else {
        resolve(r);
      }
    };
    tick();
  });
}

/** 扫描结束后的统一处理：结果提示 + 刷新各页面数据 */
async function afterScanFinished(result) {
  if (result && result.cancelled) {
    showToast('扫描已停止', 'info');
  } else if (result) {
    const parts = [
      `新增 ${result.added ?? 0}`,
      `更新 ${result.updated ?? 0}`,
      `移除 ${result.removed ?? 0}`,
    ];
    if (result.skipped != null) parts.push(`跳过 ${result.skipped}`);
    // duration 后端给的是可读字符串（如 "1分钟23秒"）
    const dur = result.duration ? `（耗时 ${result.duration}）` : '';
    showToast(`扫描完成：${parts.join(' ')}${dur}`, 'success');
  } else {
    showToast('扫描完成', 'success');
  }

  // 清空所有艺术家缓存，确保重新扫描后获取最新数据
  clearArtistCache();
  await loadArtistTree();
  loadStats();
  loadPending();
  loadLogs();

  // 如果当前艺术家存在，重新加载他的数据
  if (currentArtist) {
    await selectArtist(currentArtist.id, null);
  }
}

/** 页面加载时发现扫描已在运行：跟随它直到结束并刷新数据 */
async function followScanUntilDone() {
  const final = await pollScanUntilDone(s => setScanningUI(true, s.elapsed_seconds, s.heartbeat_age_seconds));
  setScanningUI(false);
  if (final && final.timed_out) {
    showToast('扫描已超时，可重新扫描', 'warning');
    return;
  }
  await afterScanFinished(final ? final.last_result : null);
}

/**
 * 请求停止当前扫描。
 * 置位后端取消事件后，后端扫描循环会在下一个检查点退出；这里继续轮询直到结束。
 */
async function cancelScan() {
  if (!isScanning) return;
  setScanningUI(true, 0, null, true); // 禁用按钮并显示“正在停止…”
  try {
    await POST('/scan/cancel', {});
  } catch (e) {
    if (e.message === 'not_scanning') {
      setScanningUI(false);
      return;
    }
    showToast('停止失败: ' + e.message, 'error');
    setScanningUI(true);
    return;
  }
  const final = await pollScanUntilDone(s => setScanningUI(true, s.elapsed_seconds, s.heartbeat_age_seconds, true));
  setScanningUI(false);
  await afterScanFinished(final ? final.last_result : null);
}

/** 触发服务端重新扫描音乐目录，完成后刷新各页面 */
async function doScan() {
  if (isScanning) {
    showToast('扫描正在进行中，请稍后', 'info');
    return;
  }

  // 关键：在发起请求前就置为扫描中（禁用按钮 + 置位 isScanning），
  // 杜绝快速连点导致并发触发多个扫描请求。
  setScanningUI(true);

  let started;
  try {
    started = await POST('/scan', {});
  } catch (e) {
    if (e.message === 'scan_in_progress') {
      showToast('扫描正在进行中，请稍后', 'info');
    } else {
      showToast('扫描失败: ' + e.message, 'error');
    }
    setScanningUI(false);
    return;
  }

  // 兼容后端同步返回扫描结果的旧行为
  if (started && (started.added != null || started.updated != null || started.removed != null)) {
    setScanningUI(false);
    await afterScanFinished(started);
    return;
  }

  if (!started || !started.started) {
    showToast('扫描未能启动', 'error');
    setScanningUI(false);
    return;
  }

  // 后端异步执行：轮询状态直到扫描结束，再取本轮结果
  const final = await pollScanUntilDone(s => setScanningUI(true, s.elapsed_seconds, s.heartbeat_age_seconds));
  setScanningUI(false);

  if (final && final.timed_out) {
    showToast('扫描已超时，可重新扫描', 'warning');
    return;
  }
  await afterScanFinished(final ? final.last_result : null);
}

/* ═══════════════════════════════════════════════════════════
   AUTO-LOGIN（页面加载时执行）
═══════════════════════════════════════════════════════════ */
(async () => {
  const saved = localStorage.getItem('tt-token');
  if (saved) {
    try {
      await fetch('/api/auth/verify', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token: saved }),
      }).then(r => {
        if (!r.ok) throw new Error();
      });
      TOKEN = saved;
      showApp();
    } catch {
      localStorage.removeItem('tt-token');
      showLogin(true);
    }
  } else {
    showLogin(false);
  }
})();
