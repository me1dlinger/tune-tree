/**
 * utils.js — 纯工具函数，无副作用，无 DOM 依赖
 */

/**
 * HTML 实体解码（如 &amp; → &）
 * @param {*} s
 * @returns {string}
 */
function decodeEntities(s) {
  if (s == null) return '';
  var el = document.createElement('textarea');
  el.innerHTML = String(s);
  return el.value;
}

/**
 * HTML 转义，防止 XSS
 * @param {*} s
 * @returns {string}
 */
function esc(s) {
  if (s == null) return '';
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

/**
 * JS 字符串转义（用于内联 onclick 属性中的字符串参数）
 * @param {*} s
 * @returns {string}
 */
function escJs(s) {
  if (s == null) return '';
  return String(s)
    .replace(/\\/g, '\\\\')
    .replace(/'/g, "\\'")
    .replace(/"/g, '\\"')
    .replace(/\n/g, '\\n')
    .replace(/\r/g, '\\r');
}

/**
 * 将任意字符串转为稳定的合法 CSS id 片段
 * @param {string} s
 * @returns {string}
 */
function eid(s) {
  return btoa(encodeURIComponent(String(s))).replace(/[^a-zA-Z0-9]/g, '');
}

/**
 * 格式化秒数为 m:ss
 * @param {number} sec
 * @returns {string}
 */
function fmtDur(sec) {
  if (!sec) return '—';
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${String(s).padStart(2, '0')}`;
}

/**
 * 格式化字节数为 KB / MB
 * @param {number} bytes
 * @returns {string}
 */
function fmtSize(bytes) {
  if (!bytes) return '—';
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
}

/**
 * 格式化时间戳为 YYYY-MM-DD HH:mm:ss
 * @param {number} timestamp - Unix 时间戳（秒）
 * @returns {string}
 */
function formatDateTime(timestamp) {
  if (!timestamp) return '—';
  const date = new Date(timestamp * 1000);
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  const hours = String(date.getHours()).padStart(2, '0');
  const minutes = String(date.getMinutes()).padStart(2, '0');
  const seconds = String(date.getSeconds()).padStart(2, '0');
  return `${year}-${month}-${day} ${hours}:${minutes}:${seconds}`;
}
