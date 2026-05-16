/**
 * app.js — 应用入口
 * 负责：应用初始化、全局扫描操作。
 * 依赖：所有其他模块（需最后加载）
 */

/* ═══════════════════════════════════════════════════════════
   INIT
═══════════════════════════════════════════════════════════ */

async function checkScanStatus() {
  try {
    const r = await GET('/scan/status');
    if (r.scanning) {
      setScanningUI(true, r.elapsed_seconds);
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
  await checkScanStatus();
  await loadArtistTree();

  // 自动展开第一位艺术家
  if (allArtists && allArtists.length > 0) {
    const firstArtist = allArtists[0].artist;
    const header = document.getElementById('tah-' + eid(firstArtist));
    if (header) header.click();
  }

  loadFiles('');
  loadStats();
  loadPending();
  loadLogs();
}

/* ═══════════════════════════════════════════════════════════
   SCAN
═══════════════════════════════════════════════════════════ */

function setScanningUI(scanning, elapsedSeconds = 0) {
  isScanning = scanning;
  const btn = document.getElementById('scan-btn');
  const statusEl = document.getElementById('scan-status');
  const statusText = document.getElementById('scan-status-text');
  
  if (btn) btn.disabled = scanning;
  if (statusEl) statusEl.style.display = scanning ? 'flex' : 'none';
  
  if (statusText && scanning) {
    const hours = Math.floor(elapsedSeconds / 3600);
    const minutes = Math.floor((elapsedSeconds % 3600) / 60);
    const seconds = elapsedSeconds % 60;
    let timeStr = '';
    if (hours > 0) timeStr += `${hours}小时`;
    if (minutes > 0 || hours > 0) timeStr += `${minutes}分钟`;
    timeStr += `${seconds}秒`;
    statusText.textContent = `扫描中... ${timeStr}`;
  }
}

/** 触发服务端重新扫描音乐目录，完成后刷新各页面 */
async function doScan() {
  if (isScanning) {
    showToast('扫描正在进行中，请稍后', 'info');
    return;
  }

  setScanningUI(true);

  try {
    const r = await POST('/scan', {});
    showToast(`扫描完成：新增 ${r.added} 更新 ${r.updated} 移除 ${r.removed}`, 'success');
    await loadArtistTree();
    loadStats();
    loadPending();
    loadLogs();
    if (currentArtist) await selectArtist(currentArtist.artist, null);
  } catch (e) {
    if (e.message === 'scan_in_progress') {
      showToast('扫描正在进行中，请稍后', 'info');
    } else {
      showToast('扫描失败: ' + e.message, 'error');
    }
  } finally {
    setScanningUI(false);
  }
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
