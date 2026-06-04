/**
 * pending.js — 待定文件模块
 * 依赖：api.js、utils.js、metadata-edit.js
 */

/** 加载并渲染待定文件页 */
async function loadPending() {
  try {
    const files = await GET('/pending');

    // 分离刮削失败和普通待定文件
    const scrapeFailedFiles = files.filter(f => f.scrape_failed);
    const normalPendingFiles = files.filter(f => !f.scrape_failed);

    const pv = document.getElementById('pending-view');
    pv.innerHTML = `
      <div class="pending-header">
        <div class="pending-title">待定文件</div>
        <div class="pending-badge">${files.length} 个文件</div>
      </div>
      
      ${scrapeFailedFiles.length > 0 ? `
        <div style="margin-bottom:12px;">
          <div style="font-size:12px;color:var(--red);font-weight:500;margin-bottom:4px;">
            ⚠️ 刮削失败 (${scrapeFailedFiles.length})
          </div>
          <div style="font-size:11px;color:var(--text3);">
            这些文件刮削元数据失败，已加入冷却列表，3天内不会再次尝试刮削。点击可编辑元数据。
          </div>
        </div>
        <div style="display:grid;grid-template-columns:1fr 160px 80px 100px;align-items:center;height:28px;border-bottom:1px solid var(--border);background:var(--bg);">
          <div class="th">文件名</div>
          <div class="th">路径</div>
          <div class="th">大小</div>
          <div class="th">状态</div>
        </div>
        ${scrapeFailedFiles.map(f => `
          <div style="display:grid;grid-template-columns:1fr 160px 80px 100px;align-items:center;height:40px;border-bottom:1px solid var(--border);cursor:pointer;transition:background var(--transition);background:var(--red-dim3);"
               onmouseover="this.style.background='var(--red-dim2)'" onmouseout="this.style.background='var(--red-dim3)'"
               onclick="editPendingTrack(${f.id})">
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
              <span class="status-badge status-failed">刮削失败</span>
            </div>
          </div>
        `).join('')}
        <div style="height:16px;"></div>
      ` : ''}
      
      <div style="margin-bottom:12px;font-size:12px;color:var(--text2);">
        ${normalPendingFiles.length > 0 ? `以下文件因元数据不完整无法自动分类，请补充元数据后重新扫描。点击可编辑元数据。` : ''}
      </div>
      ${normalPendingFiles.length === 0
        ? (scrapeFailedFiles.length === 0 ? '<div class="loading-row">暂无待定文件</div>' : '')
        : `
          <div style="display:grid;grid-template-columns:1fr 160px 80px 120px;align-items:center;height:28px;border-bottom:1px solid var(--border);background:var(--bg);">
            <div class="th">文件名</div>
            <div class="th">路径</div>
            <div class="th">大小</div>
            <div class="th">缺失字段</div>
          </div>
          ${normalPendingFiles.map(f => `
            <div style="display:grid;grid-template-columns:1fr 160px 80px 120px;align-items:center;height:40px;border-bottom:1px solid var(--border);cursor:pointer;transition:background var(--transition);"
                 onmouseover="this.style.background='var(--bg3)'" onmouseout="this.style.background=''"
                 onclick="editPendingTrack(${f.id})">
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

/** 编辑待定文件的元数据 */
async function editPendingTrack(trackId) {
  try {
    const track = await GET(`/tracks/${trackId}`);
    if (track) {
      openMetadataEdit(track);
    }
  } catch (e) {
    showToast('加载歌曲信息失败: ' + e.message, 'error');
  }
}
