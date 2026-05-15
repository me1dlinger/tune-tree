/**
 * ui.js — 通用 UI 工具
 * 包含：Toast 提示、Modal 控制、主题切换、页面导航。
 * 依赖：state.js（theme / currentPage）
 */

/* ═══════════════════════════════════════════════════════════
   TOAST
═══════════════════════════════════════════════════════════ */

/**
 * 弹出一条 Toast 通知
 * @param {string} msg
 * @param {'success'|'error'|'warn'|'info'|''} type
 */
function showToast(msg, type = '') {
  const container = document.getElementById('toast-container');
  const toast = document.createElement('div');
  toast.className = 'toast' + (type ? ' ' + type : '');
  toast.textContent = msg;
  container.appendChild(toast);
  setTimeout(() => {
    toast.style.opacity = '0';
    toast.style.transform = 'translateX(10px)';
    toast.style.transition = '0.3s';
    setTimeout(() => toast.remove(), 300);
  }, 3000);
}

/* ═══════════════════════════════════════════════════════════
   MODAL
═══════════════════════════════════════════════════════════ */

/**
 * 打开指定 id 的 modal-overlay
 * @param {string} id
 */
function openModal(id) {
  document.getElementById(id).classList.add('open');
}

/**
 * 关闭指定 id 的 modal-overlay
 * @param {string} id
 */
function closeModal(id) {
  document.getElementById(id).classList.remove('open');
}

/**
 * 弹出通用确认框
 * @param {string} title
 * @param {string} msg
 * @param {Function} onOk  — 用户点击确认后的回调
 */
function showConfirm(title, msg, onOk) {
  document.getElementById('confirm-title').textContent = title;
  document.getElementById('confirm-msg').textContent = msg;
  document.getElementById('confirm-ok-btn').onclick = () => {
    closeModal('confirm-modal');
    onOk();
  };
  openModal('confirm-modal');
}

// 点击遮罩层关闭 modal
document.querySelectorAll('.modal-overlay').forEach(el => {
  el.addEventListener('click', e => {
    if (e.target === el) el.classList.remove('open');
  });
});

/* ═══════════════════════════════════════════════════════════
   THEME
═══════════════════════════════════════════════════════════ */

/**
 * 应用并持久化主题
 * @param {'light'|'dark'} t
 */
function applyTheme(t) {
  theme = t;
  document.documentElement.setAttribute('data-theme', t);
  document.getElementById('theme-label').textContent = t.toUpperCase();
  localStorage.setItem('tt-theme', t);
}

/** 切换明暗主题 */
function toggleTheme() {
  applyTheme(theme === 'dark' ? 'light' : 'dark');
}

/* ═══════════════════════════════════════════════════════════
   PAGE NAV
═══════════════════════════════════════════════════════════ */

/**
 * 切换主内容区页面
 * @param {'artist'|'files'|'stats'|'pending'|'log'} page
 */
function switchPage(page) {
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));
  document.getElementById('page-' + page).classList.add('active');
  document.getElementById('nav-' + page).classList.add('active');
  currentPage = page;
  if (page !== 'artist') hideDetailPanel();
}
