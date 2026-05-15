/**
 * auth.js — 认证模块
 * 包含：登录、自动登录、登出逻辑。
 * 依赖：api.js（POST）、state.js（TOKEN）、ui.js（applyTheme、showConfirm）
 */

/* ═══════════════════════════════════════════════════════════
   LOGIN
═══════════════════════════════════════════════════════════ */

/** 用户点击"进入系统"按钮触发的登录流程 */
async function doLogin() {
  const key = document.getElementById('login-key').value.trim();
  try {
    await POST('/auth/verify', { token: key });
    TOKEN = key;
    localStorage.setItem('tt-token', key);
    showApp();
  } catch {
    document.getElementById('login-error').style.display = 'block';
    const inp = document.getElementById('login-key');
    inp.style.borderColor = 'var(--red)';
    setTimeout(() => (inp.style.borderColor = ''), 1500);
  }
}

/** Enter 键快捷登录 */
document.getElementById('login-key').addEventListener('keydown', e => {
  if (e.key === 'Enter') doLogin();
});

/* ═══════════════════════════════════════════════════════════
   APP VISIBILITY
═══════════════════════════════════════════════════════════ */

/** 登录成功后显示主应用界面 */
async function showApp() {
  document.getElementById('login-page').style.display = 'none';
  document.getElementById('app').classList.add('visible');
  applyTheme(theme);
  await initApp();
}

/**
 * 显示登录页
 * @param {boolean} error — 是否同时显示错误提示
 */
function showLogin(error) {
  document.getElementById('login-page').classList.add('visible');
  if (error) {
    document.getElementById('login-error').style.display = 'block';
  }
}

/* ═══════════════════════════════════════════════════════════
   LOGOUT
═══════════════════════════════════════════════════════════ */

/**
 * 登出
 * @param {boolean} silent — true 则直接刷新不弹确认框（用于 401 自动登出）
 */
function doLogout(silent) {
  if (silent) {
    localStorage.removeItem('tt-token');
    location.reload();
    return;
  }
  showConfirm('退出系统', '确认退出当前登录状态？', () => {
    localStorage.removeItem('tt-token');
    location.reload();
  });
}


