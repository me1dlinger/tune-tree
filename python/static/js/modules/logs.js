/**
 * logs.js — 操作日志模块
 * 依赖：api.js、utils.js、ui.js（showConfirm / showToast）
 */

/** 加载并渲染操作日志 */
async function loadLogs() {
  try {
    const logs = await GET('/logs');
    const lv = document.getElementById('log-view');
    lv.innerHTML = logs.map(l => `
      <div class="log-entry">
        <span class="log-time">${esc(l.ts)}</span>
        <span class="log-type ${esc(l.op_type)}">${l.op_type.toUpperCase()}</span>
        <span class="log-msg">${esc(l.message)}</span>
      </div>
    `).join('') || '<div style="color:var(--text3);font-size:12px;padding:20px;">暂无日志</div>';
  } catch (e) {
    document.getElementById('log-view').innerHTML =
      `<div class="loading-row" style="color:var(--red)">加载失败</div>`;
  }
}

/** 清空所有操作日志（带二次确认） */
function clearLog() {
  showConfirm('清空日志', '确认清空所有操作日志？此操作不可撤销。', async () => {
    try {
      await DELETE('/logs');
      loadLogs();
      showToast('日志已清空');
    } catch (e) {
      showToast('操作失败: ' + e.message, 'error');
    }
  });
}
