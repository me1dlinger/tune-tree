"""BUG-3 回归：扫描遍历不完整时禁止删除 stale 记录（数据保护）。

背景：``scan_service.scan_library`` 过去直接用 ``os.walk(root_path)`` 遍历且未传
``onerror``，目录列举失败（权限错误、外接盘掉线、IO 错误……）会被静默吞掉；
随后 ``stale_paths = existing_paths - found_paths`` 会无条件删库，
导致“盘没挂上就把整库曲目记录删掉”的 P0 数据丢失。

覆盖点：
1. 正常遍历（含空目录）仍按原语义删除 stale —— 不能把功能改坏。
2. 目录列举失败 → 不删任何记录、返回 incomplete=True、op_log 有 scan_incomplete、
   并连带跳过孤立艺术家/专辑清理。
3. 根目录不存在/不是目录 → 不抛异常、不删任何记录。
4. 文件级失败（stat/元数据读错）**不算**遍历失败，不影响 stale 清理。
5. 心跳逐文件更新但按 2 秒节流。
"""

import os
import time

import pytest

from services import scan_service


# ---------------------------------------------------------------- 工具函数


def _insert_track(path: str, library_id: int, artist: str = "测试歌手", album: str = "测试专辑"):
    """插入一条“历史遗留”的曲目记录（对应文件可以并不存在）。"""
    from models.db import get_db

    db = get_db()
    db.execute(
        """
        INSERT OR REPLACE INTO tracks
        (path, filename, ext, size, mtime, ctime, title, artist, album,
         pending, missing_tags, scanned_at, organized, library_id)
        VALUES (?,?,?,?,?,?,?,?,?,0,'',?,0,?)
        """,
        (
            path,
            os.path.basename(path),
            os.path.splitext(path)[1].lstrip("."),
            1234,
            time.time(),
            time.time(),
            os.path.basename(path),
            artist,
            album,
            time.time(),
            library_id,
        ),
    )
    db.commit()


def _count_tracks() -> int:
    from models.db import get_db

    return get_db().execute("SELECT COUNT(*) FROM tracks").fetchone()[0]


def _track_paths() -> set:
    from models.db import get_db

    rows = get_db().execute("SELECT path FROM tracks").fetchall()
    return {row["path"] for row in rows}


def _op_logs(op_type: str):
    from models.db import get_db

    return get_db().execute(
        "SELECT ts, op_type, message FROM op_log WHERE op_type=?", (op_type,)
    ).fetchall()


# ---------------------------------------------------------------- 用例


def test_empty_dir_scan_still_removes_stale(library, tmp_music_root):
    """正常空目录扫描：旧记录确实找不到文件 → 仍要删除（保持原有语义）。"""
    stale_path = str(tmp_music_root / "old" / "gone.mp3")
    _insert_track(stale_path, library["id"])
    assert _count_tracks() == 1

    result = scan_service.scan_library(str(tmp_music_root), library_id=library["id"])

    assert result["removed"] == 1
    assert result["incomplete"] is False
    assert result["walk_errors"] == []
    assert _count_tracks() == 0
    # 正常完成时不应出现 scan_incomplete 日志
    assert _op_logs("scan_incomplete") == []


def test_walk_error_skips_stale_cleanup(library, tmp_music_root, monkeypatch):
    """目录列举失败 → 遍历不完整 → 一条记录都不许删。"""
    stale_path = str(tmp_music_root / "old" / "gone.mp3")
    _insert_track(stale_path, library["id"])

    real_walk = os.walk

    def fake_walk(top, topdown=True, onerror=None, followlinks=False):
        # 模拟 os.walk 遇到不可读子目录：回调 onerror，但仍继续遍历其余可读目录
        if onerror is not None:
            onerror(PermissionError(13, "Permission denied", str(top) + "/locked"))
        return real_walk(top, topdown=topdown, onerror=onerror, followlinks=followlinks)

    monkeypatch.setattr(scan_service.os, "walk", fake_walk)

    result = scan_service.scan_library(str(tmp_music_root), library_id=library["id"])

    # 关键（数据丢失防线）：记录必须原样保留
    assert _track_paths() == {stale_path}
    assert result["removed"] == 0
    assert result["incomplete"] is True
    assert len(result["walk_errors"]) == 1
    assert "Permission denied" in result["walk_errors"][0]

    logs = _op_logs("scan_incomplete")
    assert len(logs) == 1
    assert "已跳过 stale 清理以保护数据" in logs[0]["message"]
    assert "1 个目录读取失败" in logs[0]["message"]


def test_walk_error_keeps_orphan_artists_and_albums(library, tmp_music_root, monkeypatch):
    """遍历不完整时连带跳过孤立艺术家/专辑清理，避免误删关联记录。"""
    from models.db import get_db

    db = get_db()
    now = time.time()
    db.execute(
        "INSERT INTO artists (name, name_normalized, dir_name, library_id, created_at, updated_at)"
        " VALUES (?,?,?,?,?,?)",
        ("孤儿歌手", "孤儿歌手", "孤儿歌手", library["id"], now, now),
    )
    artist_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.execute(
        "INSERT INTO albums (title, title_normalized, artist_id, dir_name, library_id, created_at, updated_at)"
        " VALUES (?,?,?,?,?,?,?)",
        ("孤儿专辑", "孤儿专辑", artist_id, "孤儿专辑", library["id"], now, now),
    )
    db.commit()

    real_walk = os.walk

    def fake_walk(top, topdown=True, onerror=None, followlinks=False):
        if onerror is not None:
            onerror(OSError(5, "Input/output error", str(top)))
        return real_walk(top, topdown=topdown, onerror=onerror, followlinks=followlinks)

    monkeypatch.setattr(scan_service.os, "walk", fake_walk)

    scan_service.scan_library(str(tmp_music_root), library_id=library["id"])

    assert (
        db.execute("SELECT COUNT(*) FROM artists WHERE id=?", (artist_id,)).fetchone()[0]
        == 1
    )
    assert (
        db.execute(
            "SELECT COUNT(*) FROM albums WHERE title='孤儿专辑'"
        ).fetchone()[0]
        == 1
    )


def test_walk_errors_capped_at_20(library, tmp_music_root, monkeypatch):
    """错误列表最多保留 20 条，避免返回值/日志被撑爆。"""
    stale_path = str(tmp_music_root / "old" / "gone.mp3")
    _insert_track(stale_path, library["id"])

    real_walk = os.walk

    def fake_walk(top, topdown=True, onerror=None, followlinks=False):
        if onerror is not None:
            for i in range(25):
                onerror(OSError(13, "Permission denied", f"{top}/d{i}"))
        return real_walk(top, topdown=topdown, onerror=onerror, followlinks=followlinks)

    monkeypatch.setattr(scan_service.os, "walk", fake_walk)

    result = scan_service.scan_library(str(tmp_music_root), library_id=library["id"])

    assert len(result["walk_errors"]) == 20
    assert _track_paths() == {stale_path}
    assert result["removed"] == 0
    assert result["incomplete"] is True


def test_missing_root_does_not_delete_anything(library, tmp_music_root):
    """根目录不存在（例如外接盘未挂载）→ 不抛异常、不删任何记录。"""
    stale_path = str(tmp_music_root / "old" / "gone.mp3")
    _insert_track(stale_path, library["id"])

    missing_root = str(tmp_music_root / "not-mounted")
    result = scan_service.scan_library(missing_root, library_id=library["id"])

    assert _track_paths() == {stale_path}
    assert result["removed"] == 0
    assert result["added"] == 0
    assert result["incomplete"] is True
    assert result["walk_errors"] != []
    assert len(_op_logs("scan_incomplete")) == 1


def test_file_level_error_still_prunes(library, tmp_music_root, monkeypatch):
    """文件级失败（stat/元数据读错）不算遍历失败 → stale 清理照常执行。"""
    stale_path = str(tmp_music_root / "old" / "gone.mp3")
    _insert_track(stale_path, library["id"])

    (tmp_music_root / "bad.mp3").write_bytes(b"not really audio")
    (tmp_music_root / "good.mp3").write_bytes(b"not really audio either")

    real_process = scan_service._process_file

    def flaky_process(filepath, existing_tracks, scanned_at, music_root):
        if filepath.name == "bad.mp3":
            # 模拟单个文件读取失败（原代码在 future.result() 处被 except 捕获）
            raise OSError(5, "Input/output error", str(filepath))
        return real_process(filepath, existing_tracks, scanned_at, music_root)

    monkeypatch.setattr(scan_service, "_process_file", flaky_process)

    result = scan_service.scan_library(str(tmp_music_root), library_id=library["id"])

    # 单个文件失败不影响遍历完整性判定
    assert result["incomplete"] is False
    assert result["walk_errors"] == []
    # stale 记录仍被删除（语义未变）
    assert result["removed"] == 1
    assert stale_path not in _track_paths()
    assert str(tmp_music_root / "good.mp3") in _track_paths()


class _FakeClock:
    """把 scan_service 里的 time.monotonic 换成每次调用 +1 秒的假时钟。

    其余属性（time.time 等）透传给真实 time 模块，避免影响扫描耗时统计。
    """

    def __init__(self, start: float = 100.0):
        self._t = start

    def monotonic(self) -> float:
        self._t += 1.0
        return self._t

    def __getattr__(self, name):
        return getattr(time, name)


def test_heartbeat_is_per_file_with_throttle(library, tmp_music_root, monkeypatch):
    """心跳逐文件更新，但距上次写库 ≥2 秒才真正写一次（遍历阶段共用同一节流器）。"""
    for name in ("a.mp3", "b.mp3", "c.mp3"):
        (tmp_music_root / name).write_bytes(b"not really audio")

    beats = []
    monkeypatch.setattr(scan_service, "set_scan_heartbeat", lambda: beats.append(1))
    monkeypatch.setattr(scan_service, "time", _FakeClock())

    result = scan_service.scan_library(str(tmp_music_root), library_id=library["id"])

    # 假时钟每次 monotonic +1s：遍历阶段 1 次（100→101）；
    # 元数据阶段 3 个文件：第 1 个被节流（101→102）、第 2 个写一次（102→103）、
    # 第 3 个被节流（103→104）。共 2 次写库，远少于 4 次调用 → 说明节流生效。
    assert len(beats) == 2
    assert result["incomplete"] is False


def test_walk_phase_writes_heartbeat(library, tmp_music_root, monkeypatch):
    """D5 回归：**遍历阶段（os.walk）也必须写心跳**。

    缺陷背景：心跳原本只在元数据并发循环里写，``os.walk`` 阶段一次都不写。
    而 ``api/routes.py::_check_scan_timeout`` 用 ``heartbeat_age > 90s`` 判失联并
    协作式取消扫描 —— 遍历期间 ``heartbeat_age ≈ 已用时长``，于是一次**健康**的
    大目录/慢挂载盘扫描（遍历 > 600s）会被当成卡死取消，永远扫不完。
    """
    (tmp_music_root / "a.mp3").write_bytes(b"not really audio")

    # 阶段标记：由主线程的遍历进度决定（与工作线程无关，避免竞态）
    phase = {"value": "walk"}
    beats = []
    monkeypatch.setattr(scan_service, "set_scan_heartbeat",
                        lambda: beats.append(phase["value"]))

    real_walk = os.walk

    def slow_walk(top, topdown=True, onerror=None, followlinks=False):
        # 模拟慢盘/大库：先慢慢吐出 6 个目录（其间不应有任何文件进入处理循环）
        for i in range(6):
            yield (str(tmp_music_root / f"slow{i}"), [], [])
        yield from real_walk(top, topdown=topdown, onerror=onerror, followlinks=followlinks)
        # 遍历生成器耗尽 → 主线程随后才进入元数据阶段，此处切换标记
        phase["value"] = "metadata"

    monkeypatch.setattr(scan_service.os, "walk", slow_walk)
    monkeypatch.setattr(scan_service, "time", _FakeClock())

    result = scan_service.scan_library(str(tmp_music_root), library_id=library["id"])

    walk_beats = [b for b in beats if b == "walk"]
    assert walk_beats, (
        "遍历阶段一次心跳都没写：健康的大目录扫描会被判为心跳失联并取消；"
        f"实际心跳序列={beats}"
    )
    assert beats[0] == "walk", f"首个心跳不是发生在遍历阶段：{beats}"
    # 遍历阶段也要遵守 2 秒节流（7 个目录条目 → 不应写 7 次）
    assert len(walk_beats) < 7, f"遍历阶段心跳未被节流：{beats}"
    # 扫描本身仍然正常完成
    assert result["added"] == 1
    assert result["incomplete"] is False


def test_walk_phase_refreshes_db_heartbeat_before_walk_ends(
    library, tmp_music_root, monkeypatch
):
    """集成口径：**遍历还在进行时**，库里的心跳就已经是“新鲜”的。

    这正是 ``api/routes.py::_check_scan_timeout`` 读取的信号：
    ``heartbeat_age > SCAN_HEARTBEAT_STALE_SECONDS(90s)`` 才判失联并取消。
    只要遍历期间心跳持续刷新，健康的大目录扫描就不会被误取消。
    """
    from repository.track_repository import get_scan_heartbeat

    (tmp_music_root / "a.mp3").write_bytes(b"not really audio")

    observed = []
    real_walk = os.walk

    def slow_walk(top, topdown=True, onerror=None, followlinks=False):
        # 先模拟一段“慢遍历”，此时仍处在 os.walk 阶段
        for i in range(4):
            yield (str(tmp_music_root / f"slow{i}"), [], [])
        # 遍历尚未结束：此刻读取心跳，应当是刚写过的
        observed.append(get_scan_heartbeat())
        yield from real_walk(top, topdown=topdown, onerror=onerror, followlinks=followlinks)

    monkeypatch.setattr(scan_service.os, "walk", slow_walk)

    result = scan_service.scan_library(str(tmp_music_root), library_id=library["id"])

    assert observed, "遍历阶段未执行到探针"
    assert observed[0] is not None, (
        "遍历阶段没有写任何心跳：/api/scan/status 会把健康扫描判为心跳失联并取消"
    )
    # 远小于 routes 的 90s 失联阈值
    assert time.time() - observed[0] < 30
    assert result["added"] == 1


def test_heartbeat_errors_are_swallowed(library, tmp_music_root, monkeypatch):
    """心跳写库异常必须被吞掉，不能中断扫描。"""
    (tmp_music_root / "a.mp3").write_bytes(b"not really audio")

    def boom():
        raise RuntimeError("db busy")

    monkeypatch.setattr(scan_service, "set_scan_heartbeat", boom)

    result = scan_service.scan_library(str(tmp_music_root), library_id=library["id"])

    assert result["added"] == 1
    assert result["incomplete"] is False


@pytest.mark.parametrize("filename", ["x.mp3"])
def test_scan_finds_audio_file(library, tmp_music_root, filename):
    """冒烟：正常扫描能把音频文件入库（保证夹具与调用姿势正确）。"""
    (tmp_music_root / filename).write_bytes(b"not really audio")

    result = scan_service.scan_library(str(tmp_music_root), library_id=library["id"])

    assert result["added"] == 1
    assert str(tmp_music_root / filename) in _track_paths()