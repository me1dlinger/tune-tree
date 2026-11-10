"""BUG-5 回归：扫描超时 / 卡死时状态必须与事实一致。

修复前 `_check_scan_timeout()` 到点直接 `set_scan_finished()`（置 idle），
但后台扫描线程仍在跑：
- 界面显示“未在扫描”，实际仍在写库；
- 用户可以再次点扫描，与新线程并发写库并互相删 stale；
- 此时 `/api/scan/cancel` 因 `scanning=False` 直接返回 400，用户无法终止。
"""

import threading
import time

import pytest

import api.routes as routes


@pytest.fixture()
def scan_state(app_ctx):
    """保存/恢复扫描相关的模块级状态，避免用例互相污染。"""
    saved_event = routes._scan_cancel_event
    saved_at = routes._scan_stall_cancel_at
    yield
    routes._scan_cancel_event = saved_event
    routes._scan_stall_cancel_at = saved_at
    if routes._scan_lock.locked():
        try:
            routes._scan_lock.release()
        except RuntimeError:
            pass
    from repository.track_repository import set_scan_finished

    set_scan_finished()


def _start_scan_ago(seconds: float, heartbeat_age: float | None = 0.0):
    from repository.track_repository import set_scan_heartbeat, set_scan_running

    now = time.time()
    set_scan_running(now - seconds)
    if heartbeat_age is None:
        set_scan_heartbeat(0)
    else:
        set_scan_heartbeat(now - heartbeat_age)


def test_healthy_scan_is_not_flagged(client, scan_state):
    _start_scan_ago(30, heartbeat_age=2)
    routes._scan_cancel_event = threading.Event()

    body = client.get("/api/scan/status").get_json()

    assert body["scanning"] is True
    assert body["timed_out"] is False
    assert body["stalled"] is False
    assert not routes._scan_cancel_event.is_set()


def test_scan_without_any_heartbeat_yet_is_not_cancelled(client, scan_state):
    """刚启动、还没写过心跳（heartbeat=None）：不能当失联处理。

    这是「大目录遍历」不再被误杀的正确保障方式——由 scan_service 在遍历阶段
    持续刷新心跳（见 test_scan_prune.py），而不是靠 elapsed 门槛豁免。
    """
    _start_scan_ago(30, heartbeat_age=None)
    routes._scan_cancel_event = threading.Event()

    body = client.get("/api/scan/status").get_json()

    assert body["timed_out"] is False
    assert body["stalled"] is False
    assert not routes._scan_cancel_event.is_set()


def test_stale_heartbeat_triggers_cooperative_cancel_at_any_elapsed(client, scan_state):
    """心跳过期即为失联（不再有 elapsed 门槛）：这是卡死恢复的唯一入口。"""
    _start_scan_ago(120, heartbeat_age=routes.SCAN_HEARTBEAT_STALE_SECONDS + 60)
    ev = threading.Event()
    routes._scan_cancel_event = ev

    body = client.get("/api/scan/status").get_json()

    assert ev.is_set()
    assert body["scanning"] is True, "先协作式取消，不能直接置 idle"


def test_healthy_scan_keeps_refreshing_heartbeat_so_it_is_never_cancelled(client, scan_state):
    """心跳新鲜（scan_service 每 ~2s 刷新）→ 永远不判失联。"""
    _start_scan_ago(routes.SCAN_STALL_GRACE_SECONDS * 20, heartbeat_age=2)
    routes._scan_cancel_event = threading.Event()

    body = client.get("/api/scan/status").get_json()

    assert body["stalled"] is False
    assert body["timed_out"] is False
    assert not routes._scan_cancel_event.is_set()


def test_heartbeat_dead_on_long_scan_requests_cooperative_cancel(client, scan_state):
    _start_scan_ago(700, heartbeat_age=routes.SCAN_HEARTBEAT_STALE_SECONDS + 600)
    ev = threading.Event()
    routes._scan_cancel_event = ev

    body = client.get("/api/scan/status").get_json()

    # 关键：先协作式取消，而不是直接把状态改成 idle
    assert ev.is_set()
    assert body["scanning"] is True
    assert body["timed_out"] is True
    assert body["stalled"] is True


def test_over_time_scan_keeps_scanning_state_until_thread_exits(client, scan_state):
    _start_scan_ago(routes.SCAN_TIMEOUT_HOURS * 3600 + 60, heartbeat_age=1)
    routes._scan_cancel_event = threading.Event()

    body = client.get("/api/scan/status").get_json()

    assert body["scanning"] is True, "旧实现会在这里错误地置为 idle"
    assert routes._scan_cancel_event.is_set()


def test_grace_period_exhausted_forces_reset(client, scan_state):
    """宽限期内线程未退出 → 状态复位，但**故意保留扫描锁**。"""
    _start_scan_ago(routes.SCAN_TIMEOUT_HOURS * 3600 + 60, heartbeat_age=1)
    routes._scan_cancel_event = threading.Event()
    routes._scan_stall_cancel_at = time.time() - (
        routes.SCAN_STALL_GRACE_SECONDS + 10
    )
    routes._scan_lock.acquire()

    body = client.get("/api/scan/status").get_json()

    assert body["scanning"] is False
    assert routes._scan_cancel_event is None
    # 锁必须仍然持有：放行新扫描会与僵尸线程并发写库
    assert routes._scan_lock.locked()


def test_stuck_scan_blocks_new_scan_with_clear_message(client, scan_state, library):
    """卡死复位后再次触发扫描：必须给出「需重启服务」的明确提示，而不是含糊的 409。"""
    from repository.track_repository import set_scan_finished

    set_scan_finished()
    routes._scan_cancel_event = None
    routes._scan_lock.acquire()

    resp = client.post("/api/scan")

    assert resp.status_code == 409
    assert resp.get_json()["error"] == "scan_thread_stuck"


def test_residual_state_without_thread_is_reset(client, scan_state):
    """本进程没有扫描线程（例如进程重启后残留 running）→ 直接复位。"""
    _start_scan_ago(routes.SCAN_TIMEOUT_HOURS * 3600 + 60, heartbeat_age=1)
    routes._scan_cancel_event = None

    body = client.get("/api/scan/status").get_json()

    assert body["scanning"] is False
    assert body["timed_out"] is True


def test_cancel_endpoint_records_request_time(client, scan_state):
    _start_scan_ago(10, heartbeat_age=1)
    ev = threading.Event()
    routes._scan_cancel_event = ev

    resp = client.post("/api/scan/cancel")
    assert resp.status_code == 200
    assert ev.is_set()
    assert routes._scan_stall_cancel_at is not None


def test_cancel_endpoint_without_thread_returns_409(client, scan_state):
    _start_scan_ago(10, heartbeat_age=1)
    routes._scan_cancel_event = None

    resp = client.post("/api/scan/cancel")
    assert resp.status_code == 409


def test_status_reports_heartbeat_threshold(client, scan_state):
    _start_scan_ago(5, heartbeat_age=1)

    body = client.get("/api/scan/status").get_json()

    assert body["heartbeat_stale_seconds"] == routes.SCAN_HEARTBEAT_STALE_SECONDS


def test_scan_result_payload_includes_incomplete_flag(client, scan_state, app_ctx):
    """遍历不完整时前端必须能拿到标记，否则只会看到「移除 0」。"""
    import json

    from repository.track_repository import set_scan_finished

    set_scan_finished()
    from repository.track_repository import set_scan_meta

    set_scan_meta(
        "last_scan_result",
        json.dumps({"added": 1, "removed": 0, "incomplete": True, "walk_errors": ["x"]}),
    )
    routes._scan_cancel_event = None

    body = client.get("/api/scan/status").get_json()

    assert body["last_result"]["incomplete"] is True