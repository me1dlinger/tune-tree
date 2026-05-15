/**
 * pending.js — 待定文件模块
 * 依赖：api.js、utils.js
 */

/** 加载并渲染待定文件页 */
async function loadPending() {
  try {
    const files = await GET('/pending');
    const pv = document.getElementById('pending-view');
    pv.innerHTML = `
      <div class="pending-header">
        <div class="pending-title">待定文件</div>
        <div class="pending-badge">${files.length} 个文件</div>
      </div>
      <div style="margin-bottom:12px;font-size:12px;color:var(--text2);">
        以下文件因元数据不完整无法自动分类，请补充元数据后重新扫描。
      </div>
      ${files.length === 0
        ? '<div class="loading-row">暂无待定文件</div>'
        : `
          <div style="display:grid;grid-template-columns:1fr 160px 80px 120px;align-items:center;height:28px;border-bottom:1px solid var(--border);background:var(--bg);">
            <div class="th">文件名</div>
            <div class="th">路径</div>
            <div class="th">大小</div>
            <div class="th">缺失字段</div>
          </div>
          ${files.map(f => `
            <div style="display:grid;grid-template-columns:1fr 160px 80px 120px;align-items:center;height:40px;border-bottom:1px solid var(--border);cursor:pointer;transition:background var(--transition);"
                 onmouseover="this.style.background='var(--bg3)'" onmouseout="this.style.background=''">
              <div style="padding:0 10px;font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">
                ${esc(f.filename)}
              </div>
              <div style="padding:0 10px;font-family:var(--font-mono);font-size:10px;color:var(--text3);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">
                ${esc(f.path)}
              </div>
              <div style="padding:0 10px;font-family:var(--font-mono);font-size:10px;color:var(--text3);">
                ${fmtSize(f.size)}
              </div>
              <div style="padding:0 10px;">
                <div class="missing-tags">
                  ${(f.missing_tags || '').split(',').filter(Boolean).map(m =>
                    `<span class="missing-tag">${esc(m)}</span>`
                  ).join('')}
                </div>
              </div>
            </div>
          `).join('')}
        `
      }
      <div style="margin-top:20px;padding:12px 16px;background:var(--amber-dim);border:1px solid var(--amber);border-radius:var(--radius);">
        <div style="font-family:var(--font-mono);font-size:11px;color:var(--amber);">
          提示：格式化操作只会处理有完整元数据的文件，待定文件将被自动跳过。
        </div>
      </div>
    `;
  } catch (e) {
    document.getElementById('pending-view').innerHTML =
      `<div class="loading-row" style="color:var(--red)">加载失败</div>`;
  }
}
