/**
 * api.js — HTTP 客户端
 * 封装 fetch，统一处理鉴权 header、401 自动登出、错误响应解析。
 * 依赖：state.js（读取 TOKEN）、auth.js（doLogout）
 */

/**
 * 底层请求方法
 * @param {'GET'|'POST'|'DELETE'} method
 * @param {string} path  — /api 之后的路径，如 '/artists'
 * @param {object|null} body
 * @returns {Promise<any>}
 */
async function api(method, path, body) {
  const opts = {
    method,
    headers: {
      'Content-Type': 'application/json',
      'X-Token': TOKEN,
    },
  };
  if (body) opts.body = JSON.stringify(body);

  const res = await fetch('/api' + path, opts);

  if (res.status === 401) {
    doLogout(true);
    throw new Error('unauthorized');
  }
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: res.statusText }));
    throw new Error(err.error || res.statusText);
  }
  return res.json();
}

/** GET 快捷方法 */
const GET = (path) => api('GET', path);

/** POST 快捷方法 */
const POST = (path, body) => api('POST', path, body);

/** DELETE 快捷方法 */
const DELETE = (path) => api('DELETE', path);
