"""WSGI entry point — gunicorn tune_tree.wsgi:application"""
import logging
import os
import shlex
import sys

from app import app, init_db
from repository.track_repository import reset_stale_scan_status

logger = logging.getLogger("tunetree")

# 关闭定时任务调度器的环境变量：多 worker 部署时通常只让一个进程跑调度
DISABLE_SCHEDULER_ENV = "TUNE_TREE_DISABLE_SCHEDULER"

# 视为「真」的取值（大小写不敏感）
_TRUTHY_VALUES = {"1", "true", "yes", "on"}

# 多 worker 告警只打一次，避免刷屏
_multi_worker_warned = False

# 请求期兜底是否已检查过：置位后每个请求只做一次布尔判断就短路
_request_guard_done = False


def _is_scheduler_disabled() -> bool:
    """是否通过环境变量显式关闭了定时任务调度器"""
    value = (os.environ.get(DISABLE_SCHEDULER_ENV) or "").strip().lower()
    return value in _TRUTHY_VALUES


def _parse_worker_count(tokens) -> int | None:
    """从参数序列中解析 gunicorn 的 worker 数。

    支持 ``-w 4`` / ``-w4`` / ``--workers 4`` / ``--workers=4``；
    没写或写得不合法时返回 None。
    """
    for index, token in enumerate(tokens):
        if token in ("-w", "--workers"):
            if index + 1 < len(tokens):
                try:
                    return int(tokens[index + 1])
                except ValueError:
                    return None
        elif token.startswith("--workers="):
            try:
                return int(token.split("=", 1)[1])
            except ValueError:
                return None
        elif token.startswith("-w") and token[2:].isdigit():
            return int(token[2:])
    return None


def _detect_multi_worker():
    """推断当前是否处于多 worker 部署环境。

    返回 ``(is_multi, detail)``：无法判断时返回 ``(False, None)``。
    只做保守判断，绝不因为判断结果拒绝启动。

    判据按「启动时刻真的拿得到」的顺序：
    1. ``WEB_CONCURRENCY``（部署平台设置的 worker 数，gunicorn 也读它）；
    2. ``GUNICORN_CMD_ARGS``（gunicorn 的额外命令行参数，例如 ``--workers 4``）；
    3. ``sys.argv``（gunicorn worker 由 master fork 而来，继承 master 的 argv，
       例如 ``gunicorn wsgi:application -w 4``）；
    4. ``SERVER_SOFTWARE``——只在**请求期**的 WSGI environ 里才有，
       启动时刻通常为空，这里保留是为了兼容手工设置该变量的部署，
       真正的兜底见 ``_multi_worker_request_guard``。
    """
    raw = (os.environ.get("WEB_CONCURRENCY") or "").strip()
    if raw:
        try:
            workers = int(raw)
        except ValueError:
            workers = None
        if workers is not None and workers > 1:
            return True, f"WEB_CONCURRENCY={workers}"

    cmd_args = (os.environ.get("GUNICORN_CMD_ARGS") or "").strip()
    if cmd_args:
        try:
            tokens = shlex.split(cmd_args)
        except ValueError:
            tokens = cmd_args.split()
        workers = _parse_worker_count(tokens)
        if workers is not None and workers > 1:
            return True, f"GUNICORN_CMD_ARGS={cmd_args!r}（workers={workers}）"

    argv = list(sys.argv or [])
    workers = _parse_worker_count(argv)
    if workers is not None and workers > 1:
        return True, f"sys.argv={argv!r}（workers={workers}）"

    server_software = (os.environ.get("SERVER_SOFTWARE") or "").strip()
    if "gunicorn" in server_software.lower():
        return True, f"SERVER_SOFTWARE={server_software}"

    return False, None


def _warn_multi_worker_once(detail: str) -> None:
    """多 worker 预警（整个进程只打一次）。

    APScheduler 的调度器是进程内的，每个 worker 都会各自跑一份定时任务；
    scan_service 里的单例锁同样只在进程内生效，多进程下无法互斥，
    结果是同一首歌被反复扫描/整理。这里只告警，不阻止启动。
    """
    global _multi_worker_warned
    if _multi_worker_warned:
        return
    _multi_worker_warned = True

    logger.warning(
        "检测到多 worker 部署（%s）：多 worker 下 APScheduler 会在每个进程各跑一份定时任务，"
        "且扫描单例锁只在进程内生效，可能出现重复扫描/重复整理；"
        "建议 gunicorn 使用 -w 1，或设置 %s=1 只让一个进程负责调度",
        detail,
        DISABLE_SCHEDULER_ENV,
    )


def _warn_if_multi_worker() -> None:
    """启动时刻的多 worker 预警"""
    is_multi, detail = _detect_multi_worker()
    if not is_multi:
        return
    _warn_multi_worker_once(detail)


def _multi_worker_request_guard(environ) -> None:
    """请求期兜底：``SERVER_SOFTWARE`` 只在每个请求的 WSGI environ 里。

    gunicorn 启动时既不在进程环境变量里放 ``SERVER_SOFTWARE``，也不会设
    ``WEB_CONCURRENCY``（那是读的），所以 import 时刻判定可能是漏网的
    （例如 ``gunicorn --preload``）。这里在第一个请求上补一次检测。

    开销：只在第一次请求做一次检测，之后 ``_request_guard_done`` 置位，
    后续请求只做一次布尔判断就返回。
    """
    global _request_guard_done
    if _request_guard_done:
        return
    _request_guard_done = True

    server_software = str((environ or {}).get("SERVER_SOFTWARE") or "")
    if "gunicorn" in server_software.lower():
        _warn_multi_worker_once(f"SERVER_SOFTWARE={server_software}")


def _wrap_wsgi_app(wsgi_app):
    """把兜底检测包在 WSGI 应用外层。

    用 wsgi_app 包装而不是 ``app.before_request``：后者在应用处理过第一个请求
    之后就会抛 ``AssertionError``（Flask 要求所有 setup 在首次请求前完成），
    这会让「先跑过请求、再 import wsgi」的场景（测试、嵌入使用）直接炸掉。
    """

    def guarded(environ, start_response):
        try:
            _multi_worker_request_guard(environ)
        except Exception:  # noqa: BLE001
            logger.debug("多 worker 请求期检测失败（忽略）", exc_info=True)
        return wsgi_app(environ, start_response)

    return guarded


def start_scheduler() -> bool:
    """按数据库里已保存的配置启动/刷新定时任务调度器。

    必须在 Flask 应用上下文内调用（``update_scheduler`` 会读数据库）。
    返回 ``True`` 表示已尝试启动，``False`` 表示被环境变量显式关闭。
    """
    if _is_scheduler_disabled():
        logger.info(
            "%s 已设置，跳过定时任务调度器启动（本进程不执行定时扫描/刮削/整理）",
            DISABLE_SCHEDULER_ENV,
        )
        return False

    _warn_if_multi_worker()

    # 延迟导入：便于测试 monkeypatch services.task_service.update_scheduler
    from services.task_service import update_scheduler

    update_scheduler()
    return True


def bootstrap(app_instance=None):
    """WSGI 启动流程（抽成函数以便测试直接调用，避免 import 本模块的副作用难以控制）：

    1. 初始化数据库；
    2. 复位可能残留的「扫描中」状态；
    3. 按数据库里已保存的配置启动定时任务调度器（可用环境变量关闭）。

    该函数可安全重复调用：``update_scheduler`` 内部是幂等的。
    """
    instance = app_instance or app

    init_db()

    with instance.app_context():
        # 若上次进程在扫描途中退出，数据库会留下 running 状态，不清理会导致
        # 重启后界面永远显示“扫描中”。全新进程必然没有真正在跑的扫描线程，
        # 因此这里复位是安全的，也是让「重启服务即可恢复」生效的关键。
        reset_stale_scan_status()

        # WSGI 部署（gunicorn/waitress）下没有 app.py 的 __main__ 分支，
        # 必须在这里按库里已保存的配置启动调度器，否则定时任务永远不跑。
        start_scheduler()

    return instance


application = bootstrap(app)

# D8 兜底：gunicorn 只在请求的 WSGI environ 里放 SERVER_SOFTWARE，启动时刻拿不到，
# 因此在外层 WSGI 应用上补一次多 worker 检测（命中一次后置位短路）。
application.wsgi_app = _wrap_wsgi_app(application.wsgi_app)

if __name__ == "__main__":
    # 模块级已执行 bootstrap（含调度器启动），这里只需拉起开发服务器
    app.run(debug=False, host="0.0.0.0", port=5000)
