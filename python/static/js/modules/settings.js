/**
 * 定时任务设置模块
 */

let taskConfig = null;
let taskStatus = null;
let runningTask = null;
let statusRefreshInterval = null;

async function loadTaskConfig() {
    try {
        taskConfig = await GET('/task/config');

        document.getElementById('settings-scrape-enabled').checked = taskConfig.scrape_enabled;
        document.getElementById('settings-organize-enabled').checked = taskConfig.organize_enabled;
        document.getElementById('settings-interval').value = taskConfig.interval_minutes;
    } catch (error) {
        console.error('加载任务配置失败:', error);
    }
}

async function loadTaskStatus() {
    try {
        taskStatus = await GET('/task/status');

        updateStatusDisplay();
    } catch (error) {
        console.error('加载任务状态失败:', error);
    }
}

async function checkRunningTask() {
    try {
        const result = await GET('/task/running');

        runningTask = result.scheduled_running || result.scrape_running || result.organize_running;

        // 更新按钮状态
        const execButtons = document.querySelectorAll('.manual-exec-options button');
        execButtons.forEach(btn => {
            btn.disabled = runningTask;
        });

        if (runningTask) {
            document.getElementById('manual-exec-hint').textContent = '⚠️ 有任务正在运行，请等待完成';
        } else {
            document.getElementById('manual-exec-hint').textContent = '选择要执行的任务，点击后异步执行';
        }
    } catch (error) {
        console.error('检查运行中任务失败:', error);
    }
}

function updateStatusDisplay() {
    if (!taskStatus) return;

    // 定时任务状态
    const scheduled = taskStatus.scheduled;
    document.getElementById('status-scheduled').textContent = getStatusText(scheduled.status);
    document.getElementById('status-scheduled').className = `task-status-badge status-${scheduled.status}`;
    document.getElementById('status-scheduled-last').textContent = formatTimestamp(scheduled.last_run_at);
    document.getElementById('status-scheduled-next').textContent = formatTimestamp(scheduled.next_run_at);
    document.getElementById('status-scheduled-count').textContent = `${scheduled.run_count}次`;

    if (scheduled.is_manual) {
        document.getElementById('status-scheduled').textContent += ' (手动)';
    }

    // 刮削任务状态
    const scrape = taskStatus.scrape;
    document.getElementById('status-scrape').textContent = getStatusText(scrape.status);
    document.getElementById('status-scrape').className = `task-status-badge status-${scrape.status}`;
    document.getElementById('status-scrape-success').textContent = formatTimestamp(scrape.last_success_at);
    document.getElementById('status-scrape-failure').textContent = formatTimestamp(scrape.last_failure_at);
    document.getElementById('status-scrape-counts').textContent = `${scrape.success_count}/${scrape.failure_count}`;

    // 整理任务状态
    const organize = taskStatus.organize;
    document.getElementById('status-organize').textContent = getStatusText(organize.status);
    document.getElementById('status-organize').className = `task-status-badge status-${organize.status}`;
    document.getElementById('status-organize-success').textContent = formatTimestamp(organize.last_success_at);
    document.getElementById('status-organize-failure').textContent = formatTimestamp(organize.last_failure_at);
    document.getElementById('status-organize-counts').textContent = `${organize.success_count}/${organize.failure_count}`;
}

function getStatusText(status) {
    const statusMap = {
        'idle': '空闲',
        'running': '执行中',
        'success': '成功',
        'failed': '失败'
    };
    return statusMap[status] || status;
}

function formatTimestamp(timestamp) {
    if (!timestamp) return '从未';
    const date = new Date(timestamp * 1000);
    return date.toLocaleString('zh-CN');
}

async function updateTaskConfig() {
    const scrapeEnabled = document.getElementById('settings-scrape-enabled').checked;
    const organizeEnabled = document.getElementById('settings-organize-enabled').checked;
    let intervalMinutes = parseInt(document.getElementById('settings-interval').value);

    // 最小间隔为5分钟
    if (intervalMinutes < 5) {
        intervalMinutes = 5;
        document.getElementById('settings-interval').value = 5;
    }

    try {
        const result = await POST('/task/config', {
            scrape_enabled: scrapeEnabled,
            organize_enabled: organizeEnabled,
            interval_minutes: intervalMinutes
        });
        if (result.ok) {
            taskConfig = {
                scrape_enabled: result.scrape_enabled,
                organize_enabled: result.organize_enabled,
                interval_minutes: result.interval_minutes
            };
            showToast('配置已保存', 'success');
        } else {
            showToast('保存失败: ' + result.error, 'error');
        }
    } catch (error) {
        console.error('更新任务配置失败:', error);
        showToast('保存失败', 'error');
    }
}

async function executeManualTask(taskType) {
    if (runningTask) {
        showToast('有任务正在运行，请等待完成', 'warning');
        return;
    }

    const buttonId = `exec-${taskType === 'both' ? 'both' : taskType}-btn`;
    const button = document.getElementById(buttonId);
    const originalText = button.innerHTML;

    button.innerHTML = '<i class="bi bi-spinner spin"></i> 执行中...';
    button.disabled = true;

    try {
        const result = await POST('/task/execute', { task_type: taskType });

        if (result.ok) {
            let message = '';
            if (taskType === 'scrape') {
                const r = result.result;
                message = `刮削完成：成功 ${r.success}，失败 ${r.failed}，跳过 ${r.skipped}`;
            } else if (taskType === 'organize') {
                const r = result.result;
                message = `整理完成：移动 ${r.moved}，跳过 ${r.skipped}，失败 ${r.failed}`;
            } else if (taskType === 'both') {
                const s = result.result.scrape;
                const o = result.result.organize;
                message = `刮削：成功 ${s.success}，失败 ${s.failed}；整理：移动 ${o.moved}，失败 ${o.failed}`;
            }
            showToast(message, 'success');
        } else {
            showToast('执行失败: ' + result.error, 'error');
        }

        // 刷新状态
        await loadTaskStatus();
        await checkRunningTask();
    } catch (error) {
        console.error('执行任务失败:', error);
        showToast('执行失败: ' + error.message, 'error');
    } finally {
        button.innerHTML = originalText;
        button.disabled = runningTask;
    }
}

function openSettingsModal() {
    document.getElementById('settings-modal').classList.add('open');

    loadTaskConfig();
    loadTaskStatus();
    checkRunningTask();
    loadLibrarySettings();
}

function closeSettingsModal() {
    document.getElementById('settings-modal').classList.remove('open');
}

async function loadLibrarySettings() {
    try {
        allLibraries = await GET('/libraries');
        currentLibrary = await GET('/libraries/current');
        renderLibraryList();
    } catch (error) {
        console.error('加载音乐库设置失败:', error);
    }
}

function renderLibraryList() {
    const container = document.getElementById('library-list');
    if (!container) return;
    container.innerHTML = '';

    if (!allLibraries || allLibraries.length === 0) {
        container.innerHTML = '<div class="settings-hint">暂无音乐库，请添加一个音乐库路径</div>';
        return;
    }

    allLibraries.forEach(lib => {
        const row = document.createElement('div');
        row.className = 'library-item' + (lib.is_current ? ' library-current' : '');

        const needsConfigBadge = lib.needs_config
            ? '<span class="library-badge library-badge-warning" title="需要配置路径">待配置</span>'
            : '';
        const defaultBadge = lib.is_default
            ? '<span class="library-badge library-badge-default">默认</span>'
            : '';
        const currentBadge = lib.is_current
            ? '<span class="library-badge library-badge-current">当前</span>'
            : '';

        const pathExists = lib.path && !lib.needs_config;
        const pathWarning = !pathExists
            ? '<div class="library-path-warning">路径未配置，请编辑设置路径</div>'
            : '';

        row.innerHTML = `
            <div class="library-info">
                <div class="library-name">${escapeHtml(lib.name)} ${defaultBadge} ${currentBadge} ${needsConfigBadge}</div>
                <div class="library-path">${escapeHtml(lib.path || '未配置')}</div>
                ${pathWarning}
            </div>
            <div class="library-actions">
                ${!lib.is_current ? `<button class="toolbar-btn library-btn" onclick="switchLibrary(${lib.id})" title="切换为当前音乐库"><i class="bi bi-arrow-right-circle"></i></button>` : ''}
                <button class="toolbar-btn library-btn" onclick="editLibrary(${lib.id})" title="编辑"><i class="bi bi-pencil"></i></button>
                ${!lib.is_default ? `<button class="toolbar-btn library-btn library-btn-danger" onclick="deleteLibraryConfirm(${lib.id}, '${escapeHtml(lib.name)}')" title="删除"><i class="bi bi-trash"></i></button>` : ''}
            </div>
        `;
        container.appendChild(row);
    });
}

function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

async function switchLibrary(libraryId) {
    try {
        await POST('/libraries/' + libraryId + '/switch', {});
        showToast('已切换音乐库', 'success');
        await loadLibrarySettings();
        clearArtistCache();
        await loadArtistTree();
        loadStats();
        loadPending();
    } catch (error) {
        showToast('切换失败: ' + error.message, 'error');
    }
}

function editLibrary(libraryId) {
    const lib = allLibraries.find(l => l.id === libraryId);
    if (!lib) return;

    document.getElementById('library-edit-id').value = lib.id;
    document.getElementById('library-edit-name').value = lib.name;
    document.getElementById('library-edit-path').value = lib.path || '';
    document.getElementById('library-edit-form').style.display = 'flex';
}

function cancelEditLibrary() {
    document.getElementById('library-edit-form').style.display = 'none';
}

async function saveEditLibrary() {
    const id = parseInt(document.getElementById('library-edit-id').value);
    const name = document.getElementById('library-edit-name').value.trim();
    const path = document.getElementById('library-edit-path').value.trim();

    if (!name) {
        showToast('名称不能为空', 'error');
        return;
    }

    try {
        await PUT('/libraries/' + id, { name, path });
        showToast('已保存', 'success');
        cancelEditLibrary();
        await loadLibrarySettings();
    } catch (error) {
        showToast('保存失败: ' + error.message, 'error');
    }
}

function showAddLibraryForm() {
    document.getElementById('library-add-form').style.display = 'flex';
    document.getElementById('library-add-name').value = '';
    document.getElementById('library-add-path').value = '';
}

function cancelAddLibrary() {
    document.getElementById('library-add-form').style.display = 'none';
}

async function saveAddLibrary() {
    const name = document.getElementById('library-add-name').value.trim();
    const path = document.getElementById('library-add-path').value.trim();

    if (!name || !path) {
        showToast('名称和路径不能为空', 'error');
        return;
    }

    try {
        await POST('/libraries', { name, path });
        showToast('音乐库已添加', 'success');
        cancelAddLibrary();
        await loadLibrarySettings();
    } catch (error) {
        showToast('添加失败: ' + error.message, 'error');
    }
}

function deleteLibraryConfirm(libraryId, libraryName) {
    if (confirm(`确定要删除音乐库"${libraryName}"吗？\n将同时删除该音乐库下的所有艺术家、专辑和曲目数据。`)) {
        doDeleteLibrary(libraryId);
    }
}

async function doDeleteLibrary(libraryId) {
    try {
        await DELETE('/libraries/' + libraryId);
        showToast('音乐库已删除', 'success');
        await loadLibrarySettings();
        clearArtistCache();
        await loadArtistTree();
        loadStats();
        loadPending();
    } catch (error) {
        showToast('删除失败: ' + error.message, 'error');
    }
}