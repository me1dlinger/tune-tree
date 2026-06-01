/**
 * api.js — HTTP 客户端
 * 封装 fetch，统一处理鉴权 header、401 自动登出、错误响应解析。
 * 依赖：state.js（读取 TOKEN）、auth.js（doLogout）
 */

/**
 * 底层请求方法
 * @param {'GET'|'POST'|'DELETE'|'PUT'} method
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

/** PUT 快捷方法 */
const PUT = (path, body) => api('PUT', path, body);

/**
 * 下载文件，返回 {blob: Blob, response: Response}
 */
async function apiFetchBlob(method, path, opts = {}) {
  const options = {
    method,
    headers: {
      'X-Token': TOKEN,
    },
    ...opts,
  };

  const res = await fetch('/api' + path, options);

  if (res.status === 401) {
    doLogout(true);
    throw new Error('unauthorized');
  }
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: res.statusText }));
    throw new Error(err.error || res.statusText);
  }
  const blob = await res.blob();
  return { blob, response: res };
}

/** GET blob */
const GET_BLOB = (path) => apiFetchBlob('GET', path);

/**
 * 流式下载 — GET 请求直接触发浏览器原生下载（边下边存）
 * 原理：用带 token 参数的 URL 创建隐藏 iframe，浏览器自动处理下载弹窗
 * @param {string} path — /api 之后的路径
 * @param {string} [filename] — 建议文件名（部分浏览器可能忽略）
 */
function streamDownloadGet(path, filename) {
  const sep = path.includes('?') ? '&' : '?';
  const url = '/api' + path + sep + 'token=' + encodeURIComponent(TOKEN);
  let iframe = document.getElementById('_stream_dl_iframe');
  if (!iframe) {
    iframe = document.createElement('iframe');
    iframe.id = '_stream_dl_iframe';
    iframe.style.display = 'none';
    document.body.appendChild(iframe);
  }
  iframe.src = url;
}

/**
 * 流式下载 — POST 请求
 * 优先使用 File System Access API（showSaveFilePicker + 流式写入），
 * 不支持时降级为 blob 下载。
 * @param {string} path — /api 之后的路径
 * @param {object} body — POST 请求体
 * @param {string} [suggestedName] — 建议文件名
 */
async function streamDownloadPost(path, body, suggestedName = 'download.zip') {
  if (window.showSaveFilePicker) {
    try {
      const handle = await window.showSaveFilePicker({
        suggestedName,
        types: suggestedName.endsWith('.zip')
          ? [{ description: 'ZIP', accept: { 'application/zip': ['.zip'] } }]
          : [],
      });
      const res = await fetch('/api' + path, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Token': TOKEN },
        body: JSON.stringify(body),
      });
      if (res.status === 401) { doLogout(true); throw new Error('unauthorized'); }
      if (!res.ok) {
        const err = await res.json().catch(() => ({ error: res.statusText }));
        throw new Error(err.error || res.statusText);
      }
      const writable = await handle.createWritable();
      await res.body.pipeTo(writable);
      return;
    } catch (e) {
      if (e.name === 'AbortError') return;
    }
  }
  const { blob, response } = await apiFetchBlob('POST', path, {
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const cd = response.headers.get('Content-Disposition');
  let filename = suggestedName;
  if (cd) {
    const u = cd.match(/filename\*=UTF-8''(.+)/i);
    if (u) filename = decodeURIComponent(u[1]);
    else { const m = cd.match(/filename[^;=\n]*=((['"]).*?\2|[^;\n]*)/); if (m) filename = m[1].replace(/['"]/g, ''); }
  }
  const blobUrl = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = blobUrl; a.download = filename;
  document.body.appendChild(a); a.click(); document.body.removeChild(a);
  URL.revokeObjectURL(blobUrl);
}

/**
 * 上传 FormData
 */
async function apiUpload(path, formData, method = 'POST') {
  const res = await fetch('/api' + path, {
    method,
    headers: {
      'X-Token': TOKEN,
    },
    body: formData,
  });

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
