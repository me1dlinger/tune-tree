"""BUG-6（P1）：删除曲目的“文件 / DB 行”一致性。

原实现把删文件和删 DB 行耦合，且 ``except Exception: pass`` 静默吞错：
文件删不掉但 DB 行已删，用户以为删掉了、下次扫描又冒出来。
"""

import os

from repository.track_repository import (
    delete_track_by_id,
    delete_tracks_by_ids,
    get_track_by_id,
    get_track_by_path,
    insert_track,
)


def _make_audio_file(root, name: str):
    """真实创建一个临时音频文件。"""
    path = root / name
    path.write_bytes(b"ID3\x03\x00\x00\x00fake-audio")
    return path


def _insert_track(library_id, file_path) -> int:
    """插入一条 tracks 行，返回 track_id。"""
    path_str = str(file_path)
    insert_track(
        path=path_str,
        filename=os.path.basename(path_str),
        ext=".mp3",
        size=18,
        mtime=1.0,
        ctime=1.0,
        title="测试标题",
        artist="测试艺术家",
        album="测试专辑",
        album_artist=None,
        year=None,
        track_num=1,
        disc_num=1,
        duration=100.0,
        sample_rate=44100,
        bitrate=320,
        has_cover=0,
        has_lyrics=0,
        pending=0,
        missing_tags=None,
        scanned_at=1.0,
        library_id=library_id,
    )
    return get_track_by_path(path_str)["id"]


# === 单条删除 ===


def test_delete_removes_file_and_row(library, tmp_music_root):
    audio = _make_audio_file(tmp_music_root, "a.mp3")
    track_id = _insert_track(library["id"], audio)

    result = delete_track_by_id(track_id)

    assert result["deleted"] is True
    assert result["file_deleted"] is True
    assert result["missing"] is False
    assert result["error"] is None
    assert not audio.exists()
    assert get_track_by_id(track_id) is None


def test_delete_missing_file_still_deletes_row(library, tmp_music_root):
    """文件本来就不在（ENOENT）→ 视为成功，DB 行照删。"""
    audio = tmp_music_root / "gone.mp3"  # 故意不创建
    assert not audio.exists()
    track_id = _insert_track(library["id"], audio)

    result = delete_track_by_id(track_id)

    assert result["deleted"] is True
    assert result["file_deleted"] is True
    assert result["missing"] is True
    assert result["error"] is None
    assert get_track_by_id(track_id) is None


def test_delete_failure_keeps_db_row(library, tmp_music_root, monkeypatch):
    """删文件失败时必须保留 DB 行，并把错误如实返回，供用户重试。"""
    audio = _make_audio_file(tmp_music_root, "locked.mp3")
    track_id = _insert_track(library["id"], audio)

    def _boom(path):
        raise PermissionError("权限不足（测试模拟）")

    monkeypatch.setattr(os, "remove", _boom)

    result = delete_track_by_id(track_id)

    assert result["deleted"] is False
    assert result["file_deleted"] is False
    assert result["error"]
    assert "PermissionError" in result["error"] or "权限" in result["error"]
    # 关键断言：DB 行必须还在
    assert get_track_by_id(track_id) is not None
    assert audio.exists()


def test_delete_file_false_only_removes_row(library, tmp_music_root):
    """delete_file=False 时只删 DB 行，磁盘文件保持不动。"""
    audio = _make_audio_file(tmp_music_root, "keep.mp3")
    track_id = _insert_track(library["id"], audio)

    result = delete_track_by_id(track_id, delete_file=False)

    assert result["deleted"] is True
    assert result["file_deleted"] is False
    assert result["error"] is None
    assert audio.exists()
    assert get_track_by_id(track_id) is None


def test_delete_unknown_id_is_noop(library):
    result = delete_track_by_id(999999)
    assert result["deleted"] is False
    assert result["error"] is None


# === 批量删除 ===


class _CommitCountingConnection:
    """包一层连接，统计 commit 次数（sqlite3.Connection 是不可变的 C 类型，无法直接 monkeypatch）。"""

    def __init__(self, conn):
        self._conn = conn
        self.commit_count = 0

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def execute(self, *args, **kwargs):
        return self._conn.execute(*args, **kwargs)

    def commit(self):
        self.commit_count += 1
        return self._conn.commit()


def _patch_connection(monkeypatch):
    """把 track_repository 里的 get_db 换成计数代理。"""
    import repository.track_repository as track_repo
    from models.db import get_db as real_get_db

    counter = _CommitCountingConnection(real_get_db())
    monkeypatch.setattr(track_repo, "get_db", lambda: counter)
    return counter


def test_batch_delete_commits_once(library, tmp_music_root, monkeypatch):
    """5 条一起删，只提交一次事务（原实现是 N 次 commit）。"""
    track_ids = []
    for i in range(5):
        audio = _make_audio_file(tmp_music_root, f"batch{i}.mp3")
        track_ids.append(_insert_track(library["id"], audio))

    counter = _patch_connection(monkeypatch)

    result = delete_tracks_by_ids(track_ids)

    assert counter.commit_count == 1
    assert result == {"deleted": 5, "file_deleted": 5, "failed": [], "not_found": []}
    for i, track_id in enumerate(track_ids):
        assert get_track_by_id(track_id) is None
        assert not (tmp_music_root / f"batch{i}.mp3").exists()


def test_batch_delete_reports_failed_items(library, tmp_music_root, monkeypatch):
    """删文件失败的单条被跳过并单独列出，其余条目正常删除。"""
    good_ids = []
    for i in range(2):
        audio = _make_audio_file(tmp_music_root, f"good{i}.mp3")
        good_ids.append(_insert_track(library["id"], audio))
    bad_audio = _make_audio_file(tmp_music_root, "bad.mp3")
    bad_id = _insert_track(library["id"], bad_audio)

    real_remove = os.remove

    def _boom(path):
        if str(path).endswith("bad.mp3"):
            raise PermissionError("权限不足（测试模拟）")
        return real_remove(path)

    monkeypatch.setattr(os, "remove", _boom)
    counter = _patch_connection(monkeypatch)

    result = delete_tracks_by_ids(good_ids + [bad_id])

    assert counter.commit_count == 1
    assert result["deleted"] == 2
    assert result["file_deleted"] == 2
    assert len(result["failed"]) == 1
    assert result["failed"][0]["id"] == bad_id
    assert result["failed"][0]["error"]
    assert result["not_found"] == []
    # 失败条目：文件与 DB 行都保留
    assert bad_audio.exists()
    assert get_track_by_id(bad_id) is not None
    # 成功条目：文件与 DB 行都消失
    for i, track_id in enumerate(good_ids):
        assert get_track_by_id(track_id) is None
        assert not (tmp_music_root / f"good{i}.mp3").exists()


def test_batch_delete_empty_list(library):
    assert delete_tracks_by_ids([]) == {
        "deleted": 0,
        "file_deleted": 0,
        "failed": [],
        "not_found": [],
    }


def test_batch_delete_missing_file_counts_as_success(library, tmp_music_root):
    audio = tmp_music_root / "vanished.mp3"  # 不创建
    track_id = _insert_track(library["id"], audio)

    result = delete_tracks_by_ids([track_id])

    assert result["deleted"] == 1
    assert result["file_deleted"] == 1
    assert result["failed"] == []
    assert result["not_found"] == []
    assert get_track_by_id(track_id) is None


# === D11：不存在的 id 必须被如实报告 ===


def test_batch_delete_reports_not_found_ids(library, tmp_music_root, monkeypatch):
    """D11：请求里混入不存在的 id 时，必须出现在 not_found 中。

    旧实现把「id 不存在」静默丢弃：请求 [有效id, 999999] 会返回
    deleted=1、failed=[]、ok=true，调用方完全不知道有一个 id 根本没找到。
    """
    audio = _make_audio_file(tmp_music_root, "found.mp3")
    track_id = _insert_track(library["id"], audio)
    missing_id = 999999
    assert get_track_by_id(missing_id) is None

    counter = _patch_connection(monkeypatch)

    result = delete_tracks_by_ids([track_id, missing_id])

    # 不存在的 id 单独列出，不再静默
    assert result["not_found"] == [missing_id]
    # 其余条目照常删除
    assert result["deleted"] == 1
    assert result["file_deleted"] == 1
    assert result["failed"] == []
    assert get_track_by_id(track_id) is None
    assert not audio.exists()
    # 仍然只提交一次事务
    assert counter.commit_count == 1


def test_batch_delete_not_found_does_not_abort_others(
    library, tmp_music_root, monkeypatch
):
    """多个不存在的 id：按请求顺序收集，且不影响前后条目的删除。"""
    ids = []
    for i in range(3):
        audio = _make_audio_file(tmp_music_root, f"mix{i}.mp3")
        ids.append(_insert_track(library["id"], audio))

    counter = _patch_connection(monkeypatch)

    result = delete_tracks_by_ids([ids[0], 888888, ids[1], 777777, ids[2]])

    assert result["not_found"] == [888888, 777777]
    assert result["deleted"] == 3
    assert result["file_deleted"] == 3
    assert result["failed"] == []
    assert counter.commit_count == 1
    for i, track_id in enumerate(ids):
        assert get_track_by_id(track_id) is None
        assert not (tmp_music_root / f"mix{i}.mp3").exists()


def test_batch_delete_all_not_found(library, monkeypatch):
    """全部 id 都不存在：不报错、不删任何东西、deleted=0、not_found 完整。"""
    counter = _patch_connection(monkeypatch)

    result = delete_tracks_by_ids([123456, 654321])

    assert result["deleted"] == 0
    assert result["file_deleted"] == 0
    assert result["failed"] == []
    assert result["not_found"] == [123456, 654321]
    assert counter.commit_count == 1


def test_delete_track_by_id_keeps_single_return_shape(library):
    """单条删除的返回结构不变（调用方依赖这四个键，不含 not_found）。"""
    result = delete_track_by_id(999999)

    assert set(result) == {"deleted", "file_deleted", "missing", "error"}
    assert result["deleted"] is False
    assert result["error"] is None