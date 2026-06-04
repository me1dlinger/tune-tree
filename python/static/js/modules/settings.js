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

    // 加载配置和状态（只在打开时请求一次）
    loadTaskConfig();
    loadTaskStatus();
    checkRunningTask();
}

function closeSettingsModal() {
    document.getElementById('settings-modal').classList.remove('open');
}