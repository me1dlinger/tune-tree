"""BUG-4 回归测试：WSGI 部署路径必须能按库里的配置启动定时任务调度器。

注意：``wsgi`` 模块在 import 时就执行启动流程（这是 WSGI 入口的职责），
所以这里先在环境变量里关掉调度器再 import，避免测试进程真的拉起后台线程；
需要验证启动行为的用例再显式调用 ``wsgi.start_scheduler()`` / ``wsgi.bootstrap()``。
"""

import logging
import os
import sys
from pathlib import Path
from threading import Event

import pytest
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.schedulers.base import BaseScheduler

PYTHON_DIR = Path(__file__).resolve().parents[1]
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

# 导入 wsgi 前先关闭调度器（模块级 bootstrap 会读这个变量）
os.environ["TUNE_TREE_DISABLE_SCHEDULER"] = "1"
try:
    import wsgi  # noqa: E402
finally:
    os.environ.pop("TUNE_TREE_DISABLE_SCHEDULER", None)


class NoThreadScheduler(BackgroundScheduler):
    """测试替身：使用真实的内存 jobstore / executor，但不创建后台线程。

    这样 ``get_jobs()`` 断言的是真实调度器状态，同时保证测试不会真的等待
    定时触发，也不会给测试进程留下后台线程。
    """

    def start(self, *args, **kwargs):
        if self._event is None or self._event.is_set():
            self._event = Event()
        # 走父类逻辑：创建默认 jobstore/executor、刷新 pending job、置为 running
        BaseScheduler.start(self, *args, **kwargs)
        # 但不创建 BackgroundScheduler 的后台线程
        self._thread = None

    def shutdown(self, *args, **kwargs):
        # BackgroundScheduler.shutdown 会 join 后台线程，这里没有线程可 join
        if self.running:
            BaseScheduler.shutdown(self, *args, **kwargs)


@pytest.fixture(autouse=True)
def _clean_scheduler_env(monkeypatch):
    """每个用例都在干净的环境变量下运行"""
    for name in (
        "TUNE_TREE_DISABLE_SCHEDULER",
        "WEB_CONCURRENCY",
        "SERVER_SOFTWARE",
        "GUNICORN_CMD_ARGS",
    ):
        monkeypatch.delenv(name, raising=False)
    # 兜底：pytest 自己的 argv 里不应该出现 -w，这里显式隔离掉
    monkeypatch.setattr(sys, "argv", ["pytest"])


@pytest.fixture(autouse=True)
def _reset_warn_state(monkeypatch):
    """多 worker 告警是「一进程一次」的，用例之间必须重置，否则互相污染"""
    monkeypatch.setattr(wsgi, "_multi_worker_warned", False)
    monkeypatch.setattr(wsgi, "_request_guard_done", False)


@pytest.fixture()
def task_svc(app_ctx, monkeypatch):
    """隔离 task_service 的全局 scheduler / 任务锁，避免用例之间互相污染。"""
    from services import task_service

    monkeypatch.setattr(task_service, "scheduler", None)
    monkeypatch.setattr(task_service, "BackgroundScheduler", NoThreadScheduler)
    task_service._task_lock.clear()
    yield task_service

    sched = task_service.scheduler
    if sched is not None and sched.running:
        sched.shutdown(wait=False)
    task_service.scheduler = None
    task_service._task_lock.clear()


def test_update_scheduler_twice_registers_single_job(task_svc, library):
    """连续调用两次：不抛异常、不重建调度器、不重复注册 job"""
    from repository.task_repository import set_task_config

    set_task_config(scrape_enabled=1, organize_enabled=0, interval_minutes=30)

    task_svc.update_scheduler()
    first = task_svc.scheduler
    assert first is not None, "启用了定时任务时应当创建调度器"
    assert [j.id for j in first.get_jobs()] == [task_svc.scheduled_job_id]

    # 第二次调用（等价于用户在界面上又保存了一次任务配置）
    task_svc.update_scheduler()
    assert task_svc.scheduler is first, "重复调用不应重建调度器"

    jobs = task_svc.scheduler.get_jobs()
    assert len(jobs) == 1, f"job 不应重复注册：{[j.id for j in jobs]}"
    assert jobs[0].id == task_svc.scheduled_job_id


def test_update_scheduler_no_job_when_all_disabled(task_svc, library):
    """两个任务都未开启时不注册 job"""
    from repository.task_repository import set_task_config

    set_task_config(scrape_enabled=0, organize_enabled=0, interval_minutes=30)

    task_svc.update_scheduler()
    assert task_svc.scheduler is None


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes"])
def test_wsgi_startup_skips_scheduler_when_disabled(
    task_svc, library, monkeypatch, caplog, value
):
    """TUNE_TREE_DISABLE_SCHEDULER 生效时，WSGI 启动路径不启动调度器"""
    from repository.task_repository import set_task_config

    set_task_config(scrape_enabled=1, organize_enabled=1, interval_minutes=30)

    calls = []
    monkeypatch.setattr(task_svc, "update_scheduler", lambda: calls.append(True))
    monkeypatch.setenv(wsgi.DISABLE_SCHEDULER_ENV, value)

    with caplog.at_level(logging.INFO, logger="tunetree"):
        started = wsgi.start_scheduler()

    assert started is False
    assert calls == [], "关闭时不得调用 update_scheduler"
    assert task_svc.scheduler is None
    assert any(
        "跳过定时任务调度器启动" in record.getMessage() for record in caplog.records
    ), "关闭时应有 logger.info 说明"


def test_wsgi_bootstrap_starts_scheduler_in_app_context(task_svc, library, monkeypatch):
    """bootstrap 必须在应用上下文内调用 update_scheduler（update_scheduler 要读库）"""
    from repository.task_repository import set_task_config

    set_task_config(scrape_enabled=1, organize_enabled=0, interval_minutes=30)

    seen = []

    def fake_update_scheduler():
        from flask import current_app

        # 没有应用上下文时访问 current_app 会抛 RuntimeError
        seen.append(current_app.name)

    monkeypatch.setattr(task_svc, "update_scheduler", fake_update_scheduler)

    wsgi.bootstrap()

    assert seen, "bootstrap 必须调用 update_scheduler"


def test_wsgi_bootstrap_registers_job_from_db_config(task_svc, library):
    """WSGI 部署（无 app.py 的 __main__ 分支）也能按库里的配置注册定时任务"""
    from repository.task_repository import set_task_config

    set_task_config(scrape_enabled=0, organize_enabled=1, interval_minutes=15)

    wsgi.bootstrap()

    assert task_svc.scheduler is not None
    jobs = task_svc.scheduler.get_jobs()
    assert [j.id for j in jobs] == [task_svc.scheduled_job_id]


@pytest.mark.parametrize(
    "env_name,env_value",
    [
        ("WEB_CONCURRENCY", "4"),
        ("SERVER_SOFTWARE", "gunicorn/21.2.0"),
    ],
)
def test_multi_worker_emits_warning_and_still_starts(
    task_svc, library, monkeypatch, caplog, env_name, env_value
):
    """检测到多 worker 时告警，但不拒绝启动"""
    from repository.task_repository import set_task_config

    set_task_config(scrape_enabled=1, organize_enabled=0, interval_minutes=30)
    monkeypatch.setenv(env_name, env_value)

    with caplog.at_level(logging.WARNING, logger="tunetree"):
        started = wsgi.start_scheduler()

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any(
        "多 worker" in r.getMessage() and "APScheduler" in r.getMessage()
        for r in warnings
    ), f"应有明确的多 worker 告警，实际：{[r.getMessage() for r in warnings]}"
    assert any(
        "TUNE_TREE_DISABLE_SCHEDULER" in r.getMessage() for r in warnings
    ), "告警里应给出建议（-w 1 或设置关闭变量）"
    assert started is True
    assert task_svc.scheduler is not None, "多 worker 只告警，不应拒绝启动"


@pytest.mark.parametrize(
    "env_name,env_value",
    [
        ("WEB_CONCURRENCY", "1"),
        ("SERVER_SOFTWARE", "waitress"),
    ],
)
def test_no_warning_when_single_worker(
    task_svc, library, monkeypatch, caplog, env_name, env_value
):
    """单 worker 部署不应产生多余告警"""
    from repository.task_repository import set_task_config

    set_task_config(scrape_enabled=1, organize_enabled=0, interval_minutes=30)
    monkeypatch.setenv(env_name, env_value)

    with caplog.at_level(logging.WARNING, logger="tunetree"):
        wsgi.start_scheduler()

    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert not warnings, f"不该有告警：{[r.getMessage() for r in warnings]}"
    assert task_svc.scheduler is not None


# ---------------------------------------------------------------------------
# D8：真实 gunicorn 启动时的多 worker 检测（GUNICORN_CMD_ARGS / sys.argv）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "cmd_args",
    ["--workers 4 -b 0.0.0.0:5000", "--workers=4", "-w 4", "-w4"],
)
def test_gunicorn_cmd_args_detects_multi_worker(
    task_svc, library, monkeypatch, caplog, cmd_args
):
    """gunicorn 启动时真实拿得到的是 GUNICORN_CMD_ARGS，必须能命中"""
    from repository.task_repository import set_task_config

    set_task_config(scrape_enabled=1, organize_enabled=0, interval_minutes=30)
    monkeypatch.setenv("GUNICORN_CMD_ARGS", cmd_args)

    is_multi, detail = wsgi._detect_multi_worker()
    assert is_multi is True, f"GUNICORN_CMD_ARGS={cmd_args!r} 未被识别为多 worker"
    assert "GUNICORN_CMD_ARGS" in detail

    with caplog.at_level(logging.WARNING, logger="tunetree"):
        started = wsgi.start_scheduler()

    assert any("多 worker" in r.getMessage() for r in caplog.records)
    assert started is True
    assert task_svc.scheduler is not None


@pytest.mark.parametrize(
    "argv",
    [
        ["gunicorn", "wsgi:application", "-w", "4"],
        ["/usr/bin/gunicorn", "-w4", "wsgi:application"],
        ["gunicorn", "wsgi:application", "--workers=3"],
    ],
)
def test_sys_argv_detects_multi_worker(
    task_svc, library, monkeypatch, caplog, argv
):
    """gunicorn worker 由 master fork 而来、继承 master 的 argv"""
    from repository.task_repository import set_task_config

    set_task_config(scrape_enabled=1, organize_enabled=0, interval_minutes=30)
    monkeypatch.setattr(sys, "argv", argv)

    is_multi, detail = wsgi._detect_multi_worker()
    assert is_multi is True, f"argv={argv!r} 未被识别为多 worker"
    assert "sys.argv" in detail

    with caplog.at_level(logging.WARNING, logger="tunetree"):
        wsgi.start_scheduler()
    assert any("多 worker" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize(
    "env_name,env_value,argv",
    [
        ("GUNICORN_CMD_ARGS", "--workers 1", ["gunicorn", "wsgi:application"]),
        ("GUNICORN_CMD_ARGS", "", ["gunicorn", "wsgi:application", "-w", "1"]),
        ("", "", ["waitress-serve", "--call", "app:create_app"]),
        ("", "", ["python", "app.py"]),
    ],
)
def test_single_worker_sources_do_not_warn(
    task_svc, library, monkeypatch, caplog, env_name, env_value, argv
):
    """-w 1 / waitress / 裸 python 都不该误报"""
    from repository.task_repository import set_task_config

    set_task_config(scrape_enabled=1, organize_enabled=0, interval_minutes=30)
    if env_name:
        monkeypatch.setenv(env_name, env_value)
    monkeypatch.setattr(sys, "argv", argv)

    assert wsgi._detect_multi_worker() == (False, None)

    with caplog.at_level(logging.WARNING, logger="tunetree"):
        wsgi.start_scheduler()

    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert not warnings, f"不该有告警：{[r.getMessage() for r in warnings]}"


def test_gunicorn_cmd_args_invalid_value_is_ignored(task_svc, library, monkeypatch):
    """解析失败不能抛异常、也不能误判"""
    monkeypatch.setenv("GUNICORN_CMD_ARGS", "--workers abc --bind 0.0.0.0:5000")
    assert wsgi._detect_multi_worker() == (False, None)

    monkeypatch.setenv("GUNICORN_CMD_ARGS", '--workers "unclosed')
    assert wsgi._detect_multi_worker() == (False, None)


def test_request_guard_warns_once_for_gunicorn(client, monkeypatch, caplog):
    """D8 兜底：SERVER_SOFTWARE 只在请求期 environ 里，第一个请求补一次告警"""
    monkeypatch.setattr(wsgi, "_multi_worker_warned", False)
    monkeypatch.setattr(wsgi, "_request_guard_done", False)
    client.environ_base["SERVER_SOFTWARE"] = "gunicorn/21.2.0"

    with caplog.at_level(logging.WARNING, logger="tunetree"):
        assert client.get("/api/task/status").status_code == 200
        after_first = [r for r in caplog.records if r.levelno == logging.WARNING]

        assert client.get("/api/task/status").status_code == 200
        after_second = [r for r in caplog.records if r.levelno == logging.WARNING]

    assert len(after_first) == 1, "第一个请求应打一次多 worker 告警"
    assert len(after_second) == 1, "命中一次后必须短路，不得每个请求都告警"
    assert "多 worker" in after_first[0].getMessage()
    assert wsgi._request_guard_done is True


def test_request_guard_ignores_non_gunicorn(client, monkeypatch, caplog):
    """非 gunicorn 的请求不应产生告警（且同样只检查一次）"""
    monkeypatch.setattr(wsgi, "_multi_worker_warned", False)
    monkeypatch.setattr(wsgi, "_request_guard_done", False)
    client.environ_base["SERVER_SOFTWARE"] = "waitress"

    with caplog.at_level(logging.WARNING, logger="tunetree"):
        client.get("/api/task/status")

    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert not warnings, f"不该有告警：{[r.getMessage() for r in warnings]}"
    assert wsgi._request_guard_done is True


# ---------------------------------------------------------------------------
# D9：fork 后调度器对象失效（pid 变化重建 + register_at_fork 钩子）
# ---------------------------------------------------------------------------


def test_update_scheduler_rebuilds_when_pid_changed(task_svc, library, monkeypatch):
    """fork 后子进程：scheduler.running 仍是 True 但线程已死 → 必须按 pid 重建"""
    from repository.task_repository import set_task_config

    set_task_config(scrape_enabled=1, organize_enabled=0, interval_minutes=30)

    task_svc.update_scheduler()
    inherited = task_svc.scheduler
    assert inherited is not None
    assert task_svc._scheduler_pid == os.getpid()

    # 模拟 fork：pid 变了，但那个 scheduler 对象的 running 依旧是 True
    real_getpid = os.getpid  # patched 之后不能再直接调 os.getpid（会递归）
    fake_pid = real_getpid() + 1
    monkeypatch.setattr(os, "getpid", lambda: fake_pid)
    assert inherited.running is True, "测试替身下 running 仍为 True，正是缺陷的前提"

    task_svc.update_scheduler()

    assert task_svc.scheduler is not inherited, "pid 变化时必须丢弃旧调度器并重建"
    assert task_svc.scheduler.running is True
    assert [j.id for j in task_svc.scheduler.get_jobs()] == [
        task_svc.scheduled_job_id
    ]
    assert task_svc._scheduler_pid == fake_pid


def test_after_fork_hook_resets_scheduler_state(task_svc, library):
    """os.register_at_fork 的子进程钩子：丢弃 scheduler + 清掉继承来的任务锁"""
    from repository.task_repository import set_task_config

    set_task_config(scrape_enabled=1, organize_enabled=0, interval_minutes=30)
    task_svc.update_scheduler()
    assert task_svc.scheduler is not None
    task_svc._task_lock["scheduled"] = True

    task_svc._reset_scheduler_after_fork()

    assert task_svc.scheduler is None
    assert task_svc._scheduler_pid is None
    assert task_svc._task_lock == {}, "子进程里没有真正在跑的任务，锁必须清空"


@pytest.mark.skipif(not hasattr(os, "fork"), reason="需要 POSIX fork")
def test_real_fork_child_drops_inherited_scheduler(task_svc, library):
    """真 fork 验证钩子确实生效（子进程 os._exit，不阻塞 pytest）"""
    from repository.task_repository import set_task_config

    set_task_config(scrape_enabled=1, organize_enabled=0, interval_minutes=30)
    task_svc.update_scheduler()
    assert task_svc.scheduler is not None

    read_fd, write_fd = os.pipe()
    pid = os.fork()
    if pid == 0:  # 子进程
        try:
            os.close(read_fd)
            payload = b"True" if task_svc.scheduler is None else b"False"
            os.write(write_fd, payload)
        finally:
            os._exit(0)

    os.close(write_fd)
    try:
        payload = os.read(read_fd, 16)
    finally:
        os.close(read_fd)
        os.waitpid(pid, 0)
    assert payload == b"True", "fork 子进程必须丢弃继承来的 scheduler"


# ---------------------------------------------------------------------------
# D2：未配置音乐库时，定时/手动任务不得退化成扫描进程工作目录
# ---------------------------------------------------------------------------


def _op_log_rows():
    from models.db import get_db

    return get_db().execute("SELECT op_type, message FROM op_log ORDER BY id").fetchall()


def test_run_scheduled_task_skips_when_library_not_configured(
    task_svc, no_library, monkeypatch, caplog
):
    """未配置库：不调用 scan_library（Path("") 会扫描 cwd）、写 op_log、不抛异常"""
    from repository.task_repository import get_task_status, set_task_config

    set_task_config(scrape_enabled=1, organize_enabled=1, interval_minutes=30)

    scanned = []
    monkeypatch.setattr(task_svc, "scan_library", lambda *a, **k: scanned.append((a, k)))

    with caplog.at_level(logging.WARNING, logger="tunetree"):
        task_svc.run_scheduled_task()  # 不得抛异常（不能把异常抛给 APScheduler）

    assert scanned == [], "未配置音乐库时绝不能调用 scan_library 遍历 cwd"
    assert any(
        row["op_type"] == "library_not_configured" for row in _op_log_rows()
    ), "跳过必须写 op_log"
    assert any("音乐库未配置" in r.getMessage() for r in caplog.records)

    status = get_task_status("scheduled")
    assert status["status"] == "failed"
    assert status["error_message"], "未配置库的原因必须写进 task_status.error_message"
    assert task_svc._task_lock.get("scheduled") is False, "锁必须释放"


@pytest.mark.parametrize(
    "task_type,status_key", [("both", "scheduled"), ("scrape", "scrape")]
)
def test_run_manual_task_reports_error_when_library_not_configured(
    task_svc, no_library, monkeypatch, caplog, task_type, status_key
):
    """手动任务：如实返回 error，并把原因写进前端会读取的状态键"""
    from repository.task_repository import get_task_status

    scanned = []
    monkeypatch.setattr(task_svc, "scan_library", lambda *a, **k: scanned.append((a, k)))

    with caplog.at_level(logging.WARNING, logger="tunetree"):
        result = task_svc.run_manual_task(task_type)

    assert "error" in result, "接口需要拿到失败原因（/api/task/execute 会转成 5xx）"
    assert scanned == [], "未配置音乐库时绝不能调用 scan_library 遍历 cwd"
    assert any(row["op_type"] == "library_not_configured" for row in _op_log_rows())

    status = get_task_status(status_key)
    assert status["status"] == "failed"
    assert status["error_message"], "前端要能看到失败原因"


def test_api_task_execute_reports_library_not_configured(
    client, task_svc, no_library, monkeypatch
):
    """D2 端到端：POST /api/task/execute 在未配置库时如实报错，且不遍历目录"""
    from repository.task_repository import get_task_status

    scanned = []
    monkeypatch.setattr(task_svc, "scan_library", lambda *a, **k: scanned.append((a, k)))

    resp = client.post("/api/task/execute", json={"task_type": "both"})

    # 409（当前状态不允许执行）而不是 5xx：与其它「未配置音乐库」接口保持一致
    assert resp.status_code == 409
    body = resp.get_json()
    assert body["ok"] is False
    assert "音乐库" in body["error"], f"错误信息要说明原因：{body}"
    assert scanned == [], "未配置音乐库时绝不能遍历目录"

    status = get_task_status("scheduled")
    assert status["status"] == "failed"
    assert status["error_message"], "前端读取的 task_status 必须带上失败原因"


def test_run_scheduled_task_scans_configured_library_root(
    task_svc, library, monkeypatch
):
    """回归：配置了音乐库时仍按库根扫描（不能把功能修坏）"""
    from repository.task_repository import set_task_config

    set_task_config(scrape_enabled=1, organize_enabled=0, interval_minutes=30)

    scanned = []
    monkeypatch.setattr(
        task_svc,
        "scan_library",
        lambda path, library_id=None: scanned.append((path, library_id)),
    )

    task_svc.run_scheduled_task()

    assert scanned == [(str(library["path"]), library["id"])]