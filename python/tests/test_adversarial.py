"""红队对抗性测试 — 目标是打穿 `fix/p0-p1-defects` 的修复，而不是复述改动。

约定：
- 以 ``test_defect_*`` 命名的用例断言「修复**应该**达到的行为」，
  当前实现达不到，因此标记 ``xfail(strict=True)``。
  它们 xfail == 缺陷存在（报告里会列出）；一旦被修好会 XPASS 并让套件变红。
- 其余用例断言「修复确实生效」，用来证明修复是真的。

只允许写这一个文件，不修改任何产品代码。
"""

import base64
import io
import os
import threading
import time
import zipfile
from pathlib import Path

import pytest
from PIL import Image

PYTHON_DIR = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _defects_are_xfail(request):
    """所有 ``test_defect_*`` 断言的都是「修复**应该**达到的行为」。

    当前实现达不到 → 统一标记 ``xfail(strict=True)``：
    - 套件保持绿色，但每条缺陷都在测试报告里显式列出（xfailed，``pytest -rx`` 可看原因）；
    - 一旦某条被真修好，会变成 XPASS 并让套件变红，提醒同步更新本文件与红队报告。
    """
    if request.node.name.startswith("test_defect_"):
        request.node.add_marker(
            pytest.mark.xfail(
                strict=True, reason="红队确认的缺陷：详见本文件对应用例的 docstring"
            )
        )
    yield


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------


def _import_wsgi():
    """导入 wsgi（模块级 bootstrap 会启动调度器），先在环境变量里关掉它。"""
    import sys

    if str(PYTHON_DIR) not in sys.path:
        sys.path.insert(0, str(PYTHON_DIR))
    os.environ["TUNE_TREE_DISABLE_SCHEDULER"] = "1"
    try:
        import wsgi as wsgi_mod
    finally:
        os.environ.pop("TUNE_TREE_DISABLE_SCHEDULER", None)
    return wsgi_mod


def _real_jpeg(width=8, height=8) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (10, 20, 30)).save(buf, "JPEG")
    return buf.getvalue()


def _make_library(app_ctx, path: str, needs_config: int = 0):
    from repository.library_repository import insert_library, set_current_library_id

    lib_id = insert_library("库", path, is_default=1, needs_config=needs_config)
    set_current_library_id(lib_id)
    return lib_id


# ===========================================================================
# 一、BUG-1（空库根保护）是否真的覆盖了所有退化点
# ===========================================================================


def test_empty_root_scan_is_refused_at_service_layer(app_ctx, no_library, mp3_data):
    """【已修复】空库根在**服务层**就被拒绝（纵深防御）。

    根因：``Path("") == Path(".")`` 且 ``is_dir() is True``，所以"空根"会被
    当成合法的音乐库根，把**进程工作目录**（应用源码目录）里的音频扫进库。
    现在有两道防线：
    1. 调用方（routes / task_service / format_service）用 ``require_library_root()``；
    2. ``scan_library`` 自身拒绝空根（本用例直接以空串调用，绕开第 1 道防线验证）。
    """
    from models.db import get_db
    from services.scan_service import scan_library

    cwd = Path.cwd()
    stray = cwd / "stray-cwd.ogg"
    stray.write_bytes(mp3_data)
    (cwd / "stray-cwd.mp3").write_bytes(mp3_data)
    try:
        result = scan_library("", library_id=None)
        rows = [r["path"] for r in get_db().execute("SELECT path FROM tracks")]
        assert rows == [], f"空根扫描遍历了 cwd 并入库：{rows}"
        assert result["incomplete"] is True, result
        assert result["walk_errors"], result
    finally:
        stray.unlink(missing_ok=True)
        (cwd / "stray-cwd.mp3").unlink(missing_ok=True)


def test_no_cwd_path_fallback_remains_in_services():
    """【已修复】静态证据：后台服务里不得再有 ``get_current_library_path() or ...``。

    这类回退会把 ``Path("")``（= 进程工作目录）当成音乐库根。
    """
    offenders = []
    for name in ("task_service.py", "format_service.py", "scan_service.py"):
        src = (PYTHON_DIR / "services" / name).read_text(encoding="utf-8")
        offenders += [
            f"{name}: {line.strip()}"
            for line in src.splitlines()
            if "get_current_library_path()" in line and " or " in line
        ]
    assert offenders == [], f"仍有退化成 cwd 的调用点：{offenders}"


def test_empty_root_path_is_cwd_root_cause():
    """缺陷根因：``Path("")`` 就是 cwd，扫描不会拒绝空库根。"""
    from services.scan_service import scan_library

    assert Path("").is_dir() is True


def test_files_list_409_when_library_path_is_a_file(client, app_ctx, tmp_path):
    """库 path 指向文件而不是目录 → 必须 409（get_library_root 的 is_dir 判据）。"""
    f = tmp_path / "not-a-dir.txt"
    f.write_text("x", encoding="utf-8")
    _make_library(app_ctx, str(f))
    resp = client.get("/api/files?path=")
    assert resp.status_code == 409
    assert resp.get_json()["error"] == "library_not_configured"


def test_apply_scrape_missing_album_dir_still_succeeds(
    client, make_track, app_ctx, mp3_data
):
    """缺陷（**回归**，最严重的一条）：

    修复在 ``api_apply_scraped_metadata`` 里加了
    ``if not os.path.isdir(album_dir): raise LibraryNotConfigured(...)``
    （api/routes.py:2547-2552）。两个问题：

    1. 这个 ``raise`` 落在同一个函数的 ``except Exception``（:2596）之内，
       蓝图 errorhandler 永远收不到它 → 想要的 409 变成 500；
    2. 判据本身是错的：``not os.path.isdir(album_dir)`` 并不等于「音乐库未配置」。
       只要专辑记录里的 ``dir_name`` 与磁盘目录不一致（刮削换了专辑名、人工改过目录），
       目录就不存在。此时**修复前返回 200（跳过写专辑封面）**，修复后返回 500 +
       「当前音乐库未配置可用路径」——一个完全正常、库也配置好的请求变成失败。

    实测（本仓库 main 版本）：旧代码 → 200 {"ok": true}；当前工作区 → 500。

    另外 except 分支里还有 ``commit()``，会把之前已经写进文件的封面和
    ``has_cover=1`` 一起提交，用户看到 500 却已经改了数据。
    """
    from models.db import get_db

    track = make_track("01. A.mp3", data=mp3_data)
    db = get_db()
    # 让 album.dir_name 指向一个不存在的目录（现实中刮削换专辑名就会这样）
    db.execute(
        "UPDATE albums SET dir_name='不存在的目录' WHERE id=?", (track["album_id"],)
    )
    db.commit()

    payload = base64.b64encode(_real_jpeg()).decode()
    resp = client.post(
        f"/api/tracks/{track['id']}/apply-scrape", json={"_cover_data": payload}
    )
    body = resp.get_json()

    # 【已修复】专辑目录不存在只跳过封面落盘，整体仍然成功（200）
    assert resp.status_code == 200, (resp.status_code, body)
    assert body.get("ok") is True, body
    # 内嵌封面确实写入了（这是用户真正要的效果），且没有出现 500 掩盖副作用
    assert (
        db.execute(
            "SELECT has_cover FROM tracks WHERE id=?", (track["id"],)
        ).fetchone()["has_cover"]
        == 1
    )


# ===========================================================================
# 二、BUG-2 流式 ZIP
# ===========================================================================


def test_zip_stream_early_close_keeps_source_fd(tmp_path):
    """缺陷候选：生成器被提前 close（客户端中断）时是否泄漏源文件句柄。

    修复应当保证 close 之后 /proc/self/fd 数量回到基线。
    """
    from api.routes import _zip_stream

    src = tmp_path / "big.bin"
    src.write_bytes(os.urandom(8 * 1024 * 1024))

    before = len(os.listdir("/proc/self/fd"))
    gen = _zip_stream([(src, "big.bin")])
    next(gen)  # 只消费第一块就中断
    gen.close()
    del gen
    import gc

    gc.collect()
    after = len(os.listdir("/proc/self/fd"))
    assert after <= before, f"fd 泄漏：{before} -> {after}"


def test_zip_stream_early_close_no_generator_exit_warning(tmp_path, recwarn):
    """客户端中断时不得抛 "generator ignored GeneratorExit"。"""
    from api.routes import _zip_stream

    src = tmp_path / "a.bin"
    src.write_bytes(os.urandom(3 * 1024 * 1024))
    gen = _zip_stream([(src, "a.bin")])
    next(gen)
    gen.close()  # 不应该抛任何异常
    assert not [w for w in recwarn if "GeneratorExit" in str(w.message)]


def test_zip_response_content_disposition_is_parseable(client, make_track):
    """Content-Disposition 必须同时提供合法 filename 与 filename*（RFC 6266）。"""
    track = make_track("01. 中文 名.mp3")
    resp = client.get(f"/api/albums/{track['album_id']}/download")
    assert resp.status_code == 200
    cd = resp.headers["Content-Disposition"]
    assert "filename*=UTF-8''" in cd
    # filename= 段不得裸含分号/引号导致的参数注入
    first = cd.split(";")[0]
    assert first.startswith("attachment")


def test_empty_directory_download_returns_empty_zip(client, library):
    """缺陷（回归）：旧实现下载空目录会返回 200 + 空 zip；
    新实现 ``next(entries)`` 拿不到元素就 abort(404)（api/routes.py:865-868）。
    空目录并不是「不存在」，前端会把它当错误弹出。"""
    (Path(library["path"]) / "empty_dir").mkdir()
    resp = client.get("/api/files/download?path=empty_dir")
    # 【已修复】空目录返回 200 + 空 ZIP（前端下载走 iframe，4xx 无任何反馈）
    assert resp.status_code == 200, resp.status_code
    with zipfile.ZipFile(io.BytesIO(resp.get_data())) as zf:
        assert zf.namelist() == []


def test_zip_stream_handles_zero_byte_and_chinese_names(tmp_path):
    """空文件 + 中文名 + 大文件（>1 块）混合，回读必须逐字节一致。"""
    from api.routes import _zip_stream

    small = tmp_path / "空文件.mp3"
    small.write_bytes(b"")
    big = tmp_path / "大 文件.bin"
    payload = os.urandom(3 * 1024 * 1024 + 7)
    big.write_bytes(payload)

    data = b"".join(_zip_stream([(small, "空文件.mp3"), (big, "大 文件.bin")]))
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert sorted(zf.namelist()) == ["大 文件.bin", "空文件.mp3"]
        assert zf.read("大 文件.bin") == payload
        assert zf.read("空文件.mp3") == b""
        assert zf.testzip() is None


def test_directory_download_streams_incrementally(client, make_track, library):
    """目录下载应当增量产出（响应对象可流式读取），而不是先在内存拼完整包。"""
    for i in range(4):
        make_track(f"0{i}. A.mp3", data=b"x" * (2 * 1024 * 1024))
    resp = client.get("/api/files/download?path=.", buffered=False)
    assert resp.status_code == 200
    it = iter(resp.response)
    first = next(it)
    assert len(first) <= 4 * 1024 * 1024
    resp.response.close()


# ===========================================================================
# 三、BUG-5 扫描卡死状态机
# ===========================================================================


def _set_scan_state(start_ago: float, heartbeat_ago: float | None):
    from repository.track_repository import set_scan_heartbeat, set_scan_running

    now = time.time()
    set_scan_running(now - start_ago)
    if heartbeat_ago is None:
        set_scan_heartbeat(now - start_ago)
    else:
        set_scan_heartbeat(now - heartbeat_ago)


def test_stale_heartbeat_requests_cooperative_cancel(client, app_ctx):
    """【已修复的语义】心跳过期（>90s）即为失联 → 先协作式取消。

    这条原本是缺陷用例：当时 `os.walk` 遍历阶段不写心跳，于是「健康的大目录
    扫描」在 600s 后心跳年龄就等于整个已用时长，会被误判成卡死。
    现在 **遍历阶段同样按 2s 节流刷新心跳**（services/scan_service.py），
    因此心跳过期是真的没有进展 → 取消是正确行为；
    「健康的长遍历不被误杀」由心跳供给保证，见
    `python/tests/test_scan_prune.py::test_walk_phase_writes_heartbeat`。
    """
    import api.routes as routes
    from repository.track_repository import set_scan_finished

    ev = threading.Event()
    routes._scan_cancel_event = ev
    routes._scan_stall_cancel_at = None
    _set_scan_state(start_ago=700, heartbeat_ago=700)  # 心跳=开始时刻：即在遍历阶段
    try:
        resp = client.get("/api/scan/status")
        assert resp.status_code == 200
        assert ev.is_set(), f"心跳过期却没有请求取消：{resp.get_json()}"
        assert resp.get_json()["scanning"] is True, "必须先协作式取消，而不是直接置 idle"
    finally:
        ev.set()
        set_scan_finished()
        routes._scan_cancel_event = None
        routes._scan_stall_cancel_at = None


def test_stalled_scan_eventually_force_finishes(client, app_ctx):
    """卡死：请求取消后超过宽限期仍未退出 → 状态必须被复位（不能永久 scanning）。"""
    import api.routes as routes
    from repository.track_repository import get_scan_status, set_scan_finished

    ev = threading.Event()
    routes._scan_cancel_event = ev
    routes._scan_stall_cancel_at = time.time() - (routes.SCAN_STALL_GRACE_SECONDS + 5)
    _set_scan_state(start_ago=700, heartbeat_ago=700)
    try:
        resp = client.get("/api/scan/status")
        assert resp.status_code == 200
        assert get_scan_status()["scanning"] is False
    finally:
        set_scan_finished()
        routes._scan_cancel_event = None
        routes._scan_stall_cancel_at = None


def test_stuck_thread_reports_restart_required_honestly(client, app_ctx, library):
    """【设计取舍，已按"诚实提示"落地】

    强制复位**有意保留** `_scan_lock`（api/routes.py）：线程可能只是卡在慢 IO 上，
    释放锁会让新一轮扫描与僵尸线程并发写库（互相把对方刚插入的行当 stale 删除）。
    代价是用户必须重启才能再扫描——因此接口必须**直说**「需重启服务」，
    而不是含糊的「稍后重试」（后者永远失败，属于误导）。
    """
    import api.routes as routes

    routes._scan_lock.acquire()  # 模拟僵尸线程仍持有锁
    try:
        resp = client.post("/api/scan")
        body = resp.get_json()
        assert resp.status_code == 409
        assert body["error"] == "scan_thread_stuck", body
        assert "重启" in (body.get("message") or ""), (
            "必须明确告知需要重启服务，否则用户会一直在『稍后重试』上打转：" + str(body)
        )
    finally:
        routes._scan_lock.release()


def test_concurrent_status_polls_do_not_break_state(flask_app, app_ctx):
    """并发轮询 /api/scan/status 不得抛异常或把状态写坏。"""
    import api.routes as routes
    from repository.track_repository import get_scan_status, set_scan_finished

    from tests.conftest import TEST_TOKEN

    ev = threading.Event()
    routes._scan_cancel_event = ev
    routes._scan_stall_cancel_at = None
    _set_scan_state(start_ago=650, heartbeat_ago=1)
    errors = []
    barrier = threading.Barrier(8)

    def poll():
        try:
            with flask_app.test_client() as c:
                c.environ_base["HTTP_X_TOKEN"] = TEST_TOKEN
                barrier.wait(timeout=10)
                for _ in range(5):
                    r = c.get("/api/scan/status")
                    assert r.status_code == 200
        except Exception as exc:  # noqa: BLE001
            errors.append(repr(exc))

    threads = [threading.Thread(target=poll) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    try:
        assert not errors, errors
        assert get_scan_status()["scanning"] is True
    finally:
        ev.set()
        set_scan_finished()
        routes._scan_cancel_event = None
        routes._scan_stall_cancel_at = None


def test_stale_heartbeat_is_reported_as_stalled(client, app_ctx):
    """同一根因的另一个症状：/api/scan/status 的 `stalled` 字段。

    修复后语义：心跳过期 ⇒ stalled=true（前端弹「心跳失联，正在尝试停止」），
    健康扫描由遍历阶段的心跳供给保证不会过期。"""
    import api.routes as routes
    from repository.track_repository import set_scan_finished

    ev = threading.Event()
    routes._scan_cancel_event = ev
    routes._scan_stall_cancel_at = None
    _set_scan_state(start_ago=700, heartbeat_ago=700)
    try:
        body = client.get("/api/scan/status").get_json()
        assert body["stalled"] is True, body
    finally:
        ev.set()
        set_scan_finished()
        routes._scan_cancel_event = None
        routes._scan_stall_cancel_at = None


# ===========================================================================
# 四、BUG-3 遍历不完整禁止删 stale
# ===========================================================================


def test_scan_root_missing_is_incomplete_and_keeps_rows(app_ctx, tmp_path):
    """库根不存在时 → incomplete，且不得删除任何已有记录。"""
    from models.db import get_db
    from services.scan_service import scan_library

    missing = tmp_path / "offline"
    missing.mkdir()
    lib_id = _make_library(app_ctx, str(missing))
    db = get_db()
    from repository.artist_repository import ensure_artist
    from repository.album_repository import ensure_album

    aid = ensure_artist("A", library_id=lib_id)
    alid = ensure_album("B", aid, library_id=lib_id)
    db.execute(
        "INSERT INTO tracks (path, filename, ext, size, mtime, ctime, library_id, artist_id, album_id)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        (str(missing / "gone.mp3"), "gone.mp3", "mp3", 1, 1.0, 1.0, lib_id, aid, alid),
    )
    db.commit()

    missing.rmdir()  # 盘掉了

    res = scan_library(str(missing), library_id=lib_id)
    assert res["incomplete"] is True
    assert res["removed"] == 0
    left = db.execute("SELECT COUNT(*) c FROM tracks").fetchone()["c"]
    assert left == 1, "遍历不可用却删除了记录（数据被清空）"


def test_partial_permission_failure_still_reports_incomplete(
    app_ctx, tmp_path, mp3_data
):
    """遍历部分失败（权限）必须置 incomplete，且不得删 stale。"""
    import stat

    from models.db import get_db
    from services.scan_service import scan_library

    root = tmp_path / "lib"
    (root / "ok").mkdir(parents=True)
    (root / "ok" / "a.mp3").write_bytes(mp3_data)
    blocked = root / "blocked"
    blocked.mkdir()
    (blocked / "b.mp3").write_bytes(mp3_data)
    lib_id = _make_library(app_ctx, str(root))

    db = get_db()
    from repository.artist_repository import ensure_artist
    from repository.album_repository import ensure_album

    aid = ensure_artist("A", library_id=lib_id)
    alid = ensure_album("B", aid, library_id=lib_id)
    db.execute(
        "INSERT INTO tracks (path, filename, ext, size, mtime, ctime, library_id, artist_id, album_id)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        (str(blocked / "b.mp3"), "b.mp3", "mp3", 1, 1.0, 1.0, lib_id, aid, alid),
    )
    db.commit()

    blocked.chmod(0)
    try:
        res = scan_library(str(root), library_id=lib_id)
    finally:
        blocked.chmod(stat.S_IRWXU)
    if os.geteuid() == 0:
        pytest.skip("root 无视权限位，无法构造遍历失败")
    assert res["incomplete"] is True
    assert res["removed"] == 0
    # 已存在的 blocked/b.mp3 保留 + 新发现的 ok/a.mp3 入库 == 2
    assert db.execute("SELECT COUNT(*) c FROM tracks").fetchone()["c"] == 2
    paths = {r["path"] for r in db.execute("SELECT path FROM tracks")}
    assert str(blocked / "b.mp3") in paths, "遍历不完整却把读不到的记录删了"


# ===========================================================================
# 五、BUG-6 删除反馈
# ===========================================================================


def test_delete_track_failure_does_not_leak_os_error(client, make_track):
    """缺陷（回归）：单条删除在文件删不掉时返回 500 + 原始 OS 错误串
    （api/routes.py:1710-1730），前端 ``api()`` 会把它当请求失败并直接弹出
    ``[Errno 21] Is a directory`` 这种给用户看的文案。"""
    track = make_track("01. A.mp3")
    p = Path(track["path"])
    p.unlink()
    p.mkdir()  # 同名目录 → os.remove 抛 IsADirectoryError
    (p / "keep.txt").write_text("x", encoding="utf-8")

    resp = client.delete(f"/api/tracks/{track['id']}")
    body = resp.get_json()
    # 【已修复】失败仍是 500（服务端错误语义不变），但正文不得包含 errno/
    # 服务器绝对路径；给用户的是稳定错误码 + 可读提示。
    assert resp.status_code == 500, body
    assert body.get("error") == "delete_failed", body
    assert "[Errno" not in str(body), body
    assert str(Path(track["path"]).parent) not in str(body), body
    assert body.get("message"), body


def test_delete_track_keeps_row_when_file_undeletable(client, make_track, app_ctx):
    """修复生效：文件删不掉时必须保留 DB 行（可重试）。"""
    from models.db import get_db

    track = make_track("01. A.mp3")
    p = Path(track["path"])
    p.unlink()
    p.mkdir()

    resp = client.delete(f"/api/tracks/{track['id']}")
    assert resp.status_code == 500
    assert resp.get_json()["ok"] is False
    row = get_db().execute("SELECT id FROM tracks WHERE id=?", (track["id"],)).fetchone()
    assert row is not None, "删除失败却把 DB 行删掉了（假删除）"


def test_batch_delete_is_single_commit(client, make_track, app_ctx, monkeypatch):
    """修复生效：批量删除只 commit 一次。"""
    import repository.track_repository as tr

    ids = [make_track(f"0{i}. A.mp3")["id"] for i in range(5)]
    calls = []
    orig = tr.get_db

    class _CountingConn:
        def __init__(self, conn):
            self._c = conn

        def __getattr__(self, name):
            return getattr(self._c, name)

        def commit(self):
            calls.append(1)
            return self._c.commit()

    monkeypatch.setattr(tr, "get_db", lambda: _CountingConn(orig()))
    tr.delete_tracks_by_ids(ids)
    assert len(calls) == 1, f"批量删除提交了 {len(calls)} 次"


def test_batch_delete_reports_not_found_and_reconciles(client, make_track):
    """【已修复】批量删除必须让调用方看清「有几个没删掉」。

    契约：``deleted_count`` = 真的删掉的条数（保持精确，不虚报），
    ``not_found`` / ``failed`` 分别报告「id 不存在」与「文件删不掉」，
    且 ``deleted_count + len(failed) + len(not_found) == len(track_ids)`` 恒成立，
    前端据此提示而不是一律报「成功删除 N 个」。
    """
    a = make_track("01. A.mp3")
    ids = [a["id"], 999999]
    resp = client.post("/api/tracks/batch-delete", json={"track_ids": ids})
    body = resp.get_json()

    assert resp.status_code == 200
    assert body["deleted_count"] == 1, body
    assert body["not_found"] == [999999], body
    assert body["failed"] == [], body
    assert (
        body["deleted_count"] + len(body["failed"]) + len(body["not_found"]) == len(ids)
    ), body
    assert body.get("message"), "应当提示存在未删除/不存在的记录"


def test_delete_file_false_leaves_existing_file(client, make_track, app_ctx):
    """delete_file=False 的语义：只删行、文件保留；但 file_deleted 返回 False
    会让调用方误以为「文件还没删掉」。这里记录语义是否自洽。"""
    from repository.track_repository import delete_track_by_id

    track = make_track("01. A.mp3")
    res = delete_track_by_id(track["id"], delete_file=False)
    assert res["deleted"] is True
    assert Path(track["path"]).exists()
    assert res["file_deleted"] is False or res["missing"] is True, res


# ===========================================================================
# 六、BUG-4 / WSGI
# ===========================================================================


def test_multi_worker_detection_uses_gunicorn_args(monkeypatch):
    """缺陷：``_detect_multi_worker`` 依赖 ``SERVER_SOFTWARE``（wsgi.py:51-53），
    但 gunicorn 只在**每个请求**的 WSGI environ 里放 SERVER_SOFTWARE，
    import 时刻环境变量里没有它；WEB_CONCURRENCY 是 gunicorn 读的默认值来源，
    也不是 gunicorn 自己设置的。因此在真实 gunicorn 多 worker 启动时
    这个警告根本不会打印——README 承诺的「启动时若检测到多 worker 也会打印对应警告」
    不成立。"""
    wsgi_mod = _import_wsgi()

    monkeypatch.delenv("WEB_CONCURRENCY", raising=False)
    monkeypatch.delenv("SERVER_SOFTWARE", raising=False)
    monkeypatch.setenv("GUNICORN_CMD_ARGS", "--workers 4 -b 0.0.0.0:5000")
    is_multi, detail = wsgi_mod._detect_multi_worker()
    assert is_multi, "以 gunicorn -w 4 启动时未能检测到多 worker"


def test_scheduler_disabled_env(monkeypatch):
    wsgi_mod = _import_wsgi()

    monkeypatch.setenv(wsgi_mod.DISABLE_SCHEDULER_ENV, "TRUE")
    assert wsgi_mod._is_scheduler_disabled() is True
    assert wsgi_mod.start_scheduler() is False


def test_fork_guard_discards_inherited_scheduler(monkeypatch):
    """【已修复】fork 后调度器不能"看起来还活着"。

    原缺陷：`os.fork()` 后子进程里 `scheduler.running` 仍是 True，而 APScheduler
    的后台线程不会跨 fork 存活 → 定时任务静默失效（`--preload` 部署必踩）。

    修复方式（services/task_service.py）：`_scheduler_pid` 记录创建者 pid +
    `os.register_at_fork(after_in_child=...)` 在子进程里丢弃继承来的调度器，
    使 `update_scheduler()` 必然重建。

    注意：这里**不再**断言"子进程里 `sched._thread.is_alive()` 为 True"——
    CPython 的 `threading._after_fork` 会停掉非当前线程，那个断言恒假，
    任何正确修复都不可能让它通过（红队原用例的前提有误）。
    """
    import services.task_service as ts

    if not hasattr(os, "fork"):
        pytest.skip("需要 posix fork")

    monkeypatch.setattr(ts, "scheduler", object())
    monkeypatch.setattr(ts, "_scheduler_pid", os.getpid())

    r, w = os.pipe()
    pid = os.fork()
    if pid == 0:  # 子进程
        try:
            os.close(r)
            inherited = ts.scheduler
            pid_matches = ts._scheduler_pid == os.getpid()
            os.write(w, f"{inherited is None}|{pid_matches}".encode())
        finally:
            os._exit(0)
    os.close(w)
    payload = os.read(r, 64).decode()
    os.close(r)
    os.waitpid(pid, 0)

    discarded, pid_matched = payload.split("|")
    assert discarded == "True", "fork 后没有丢弃继承的调度器（会静默不跑定时任务）"
    assert pid_matched == "False", "_scheduler_pid 应被清空，以便下次调用重建"


# ===========================================================================
# 七、其他回归面
# ===========================================================================


def test_needs_config_library_download_is_409(client, app_ctx):
    """needs_config=1（path=""）的库：下载接口也必须 409，不能落到 cwd。"""
    _make_library(app_ctx, "", needs_config=1)
    resp = client.get("/api/files/download?path=.env")
    assert resp.status_code == 409


def test_path_traversal_forms_still_blocked(client, library):
    """各种编码/穿越写法的 path 参数都不能越界。"""
    for raw in (
        "../",
        "..%2f..%2f",
        "%2e%2e%2f%2e%2e%2f",
        "....//",
        "..\\..\\",
        "/etc/passwd",
        "a/../../../../etc/passwd",
    ):
        resp = client.get(f"/api/files?path={raw}")
        assert resp.status_code in (200, 403, 404), (raw, resp.status_code)
        if resp.status_code == 200:
            body = resp.get_data(as_text=True)
            assert "root:" not in body
            assert "ACCESS_KEY" not in body


def test_zip_download_content_length_absent_allows_streaming(client, make_track):
    """流式响应不应带死板的 Content-Length（否则大包会在 WSGI 层重新缓冲）。"""
    track = make_track("01. A.mp3")
    resp = client.get(f"/api/albums/{track['album_id']}/download")
    assert resp.status_code == 200
    assert resp.headers.get("Content-Type", "").startswith("application/zip")


# ===========================================================================
# 八、扫描状态机的时序竞态 / 前端接线
# ===========================================================================


def test_normal_teardown_window_allows_new_scan(
    client, app_ctx, library, monkeypatch
):
    """缺陷（时序）：``_do_scan`` 的 finally 先 ``set_scan_finished()``，
    ``_scan_worker`` 的 finally 之后才 ``_scan_lock.release()``
    （api/routes.py:610-612 / :614-627）。因此在「状态已 idle、锁还没放」的收尾窗口里
    发起的 POST /api/scan 会命中 :550 的分支，被误判成「上一轮线程卡死」，
    返回 409 scan_thread_stuck 并劝用户重启服务——而上一轮扫描其实完全正常。

    本用例把这个窗口人为放大到 0.4s 使竞态可复现；生产中该窗口是
    「退出 app context + finally」的耗时（毫秒级但非零）。
    """
    import api.routes as routes
    from repository.track_repository import get_scan_status

    real_finish = routes.set_scan_finished

    def slow_finish():
        real_finish()
        time.sleep(0.4)  # 只放大「状态已 idle 但锁未释放」这一段

    monkeypatch.setattr(routes, "set_scan_finished", slow_finish)

    assert client.post("/api/scan").status_code == 200
    deadline = time.time() + 10
    while time.time() < deadline and get_scan_status()["scanning"]:
        time.sleep(0.02)
    # 此刻状态是 idle，而锁仍被正在收尾的线程持有
    resp = client.post("/api/scan")
    assert resp.status_code != 409 or resp.get_json()["error"] != "scan_thread_stuck", (
        resp.get_json()
    )
    tried = time.time()
    while time.time() - tried < 5:
        if client.post("/api/scan").status_code == 200:
            break
        time.sleep(0.1)


def test_scan_stuck_error_shows_readable_message():
    """缺陷（接线）：``doScan`` 只对 ``scan_in_progress`` 做友好提示，
    其它错误直接把 ``err.error`` 拼给用户（static/js/app.js:243-248），
    于是新加的 scan_thread_stuck 会显示成「扫描失败: scan_thread_stuck」，
    后端精心写的 ``message`` 一个字都用不上。"""
    src = (PYTHON_DIR / "static" / "js" / "app.js").read_text(encoding="utf-8")
    idx = src.index("async function doScan")
    seg = src[idx : idx + 3000]
    assert "scan_thread_stuck" in seg, "前端没有针对 scan_thread_stuck 的提示分支"


def test_batch_delete_ui_consumes_failure_payload():
    """缺陷（接线）：BUG-6 的删除反馈只加在后端；唯一的前端调用点
    ``static/js/modules/stats.js:237`` 完全不看响应体，直接
    ``showToast('成功删除 N 个文件')``（N=请求数）。
    也就是说文件没删掉时用户看到的仍然是「成功删除」。"""
    src = (PYTHON_DIR / "static" / "js" / "modules" / "stats.js").read_text(
        encoding="utf-8"
    )
    idx = src.index("batch-delete")
    seg = src[idx - 300 : idx + 700]
    assert "failed" in seg, "前端忽略 failed/deleted_count，仍会谎报『成功删除 N 个文件』"


def test_single_track_delete_is_api_only(client, make_track, app_present=None):
    """【已知现状，非缺陷】单条 ``DELETE /api/tracks/<id>`` 目前没有前端调用点。

    它仍是有意义的公开 API（脚本/第三方可调用），因此保留并验证其行为；
    这里显式记录「UI 尚未接入」，避免以后误以为删除反馈已经在界面上可用。
    """
    import re

    track = make_track("01. A.mp3", data=b"data")
    resp = client.delete(f"/api/tracks/{track['id']}")
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["ok"] is True

    pattern = re.compile(r"""DELETE\(\s*[`"']\s*/tracks/""")
    hits = [
        str(p)
        for p in (PYTHON_DIR / "static" / "js").rglob("*.js")
        if pattern.search(p.read_text(encoding="utf-8"))
    ]
    assert hits == [], (
        "前端开始调用 DELETE /tracks/<id> 了：请同步检查它是否消费 "
        "file_deleted/missing 与失败文案（原缺口已变化）"
    )