"""BUG-10 回归测试：整理时「文件移动成功但写库失败」必须回滚文件。

场景：``execute_format`` 先 ``shutil.move`` 再 ``update_track_path_and_name``，
两者之间没有事务保护。若不回滚，文件已在新位置、DB 仍指向旧路径，
UI 会显示「文件不存在」，下次扫描还会把新位置当新文件插入 → 重复记录。
"""

import logging

import pytest

from models.db import get_db
from repository.album_repository import ensure_album
from repository.artist_repository import ensure_artist

ARTIST = "张三"
ALBUM = "第一张专辑"
SRC_DIR = "乱放的目录"
SRC_NAME = "01 Song.mp3"
TARGET_NAME = "01. Song.mp3"  # build_target_filename 的产物


def _make_track(library, tmp_music_root):
    """造一条「有标签、待整理」的曲目，返回 (track_id, src_path)。"""
    src_dir = tmp_music_root / SRC_DIR
    src_dir.mkdir(parents=True, exist_ok=True)
    src = src_dir / SRC_NAME
    src.write_bytes(b"fake-audio")

    artist_id = ensure_artist(ARTIST, library_id=library["id"])
    album_id = ensure_album(ALBUM, artist_id, library_id=library["id"])

    db = get_db()
    cursor = db.execute(
        """
        INSERT INTO tracks (
            path, filename, ext, title, artist, album, artist_id, album_id,
            library_id, track_num, pending, organized
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0)
        """,
        (
            str(src),
            SRC_NAME,
            ".mp3",
            "Song",
            ARTIST,
            ALBUM,
            artist_id,
            album_id,
            library["id"],
            1,
        ),
    )
    db.commit()
    return cursor.lastrowid, src


def _target_path(tmp_music_root):
    return tmp_music_root / ARTIST / ALBUM / TARGET_NAME


def _track_row(track_id):
    return get_db().execute(
        "SELECT path, filename, organized FROM tracks WHERE id=?", (track_id,)
    ).fetchone()


def test_execute_format_moves_file_and_updates_db(library, tmp_music_root):
    """正常路径：文件被移动，DB 同步更新（防回归）"""
    from services import format_service

    track_id, src = _make_track(library, tmp_music_root)
    dst = _target_path(tmp_music_root)
    assert not dst.exists()

    result = format_service.execute_format(
        ARTIST, track_ids=[track_id], library_id=library["id"]
    )

    assert result["moved"] == 1
    assert result["errors"] == 0
    assert dst.exists(), "文件应被移动到目标路径"
    assert not src.exists(), "原路径不应再有文件"

    row = _track_row(track_id)
    assert row["path"] == str(dst)
    assert row["filename"] == dst.name
    assert row["organized"] == 1


def test_execute_format_rolls_back_file_when_db_update_fails(
    library, tmp_music_root, monkeypatch, caplog
):
    """写库失败：文件回到原路径、DB 保持旧值、errors == 1、op_log 有记录"""
    from services import format_service

    track_id, src = _make_track(library, tmp_music_root)
    dst = _target_path(tmp_music_root)
    before = _track_row(track_id)

    def boom(*args, **kwargs):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(format_service, "update_track_path_and_name", boom)

    with caplog.at_level(logging.ERROR, logger="tunetree"):
        result = format_service.execute_format(
            ARTIST, track_ids=[track_id], library_id=library["id"]
        )

    assert result["errors"] == 1
    assert result["moved"] == 0, "写库失败的曲目不能计入 moved"
    assert src.exists(), "写库失败后文件必须被搬回原路径"
    assert not dst.exists(), "目标路径不应残留文件"

    after = _track_row(track_id)
    assert after["path"] == before["path"] == str(src), "DB 应保持旧路径"
    assert after["organized"] == 0

    logs = get_db().execute(
        "SELECT op_type, message FROM op_log ORDER BY id"
    ).fetchall()
    error_logs = [row for row in logs if row["op_type"] == "error"]
    assert error_logs, "回滚必须写 op_log"
    assert any("回滚" in (row["message"] or "") for row in error_logs)
    assert any(
        "已回滚文件" in record.getMessage() for record in caplog.records
    ), "回滚成功应有日志"


def test_preview_format_raises_when_library_not_configured(no_library):
    """D2：未配置音乐库时 preview_format 必须抛 LibraryNotConfigured。

    绝不能再退化成 ``Path("")``（== 进程工作目录），否则预览/整理会把
    应用源码目录当成音乐库根。api 层已有 errorhandler 负责转成 409。
    """
    from repository.library_repository import LibraryNotConfigured
    from services import format_service

    with pytest.raises(LibraryNotConfigured):
        format_service.preview_format(ARTIST)


def test_execute_format_raises_when_library_not_configured(no_library):
    """D2：未配置音乐库时 execute_format 同样不得退化成 cwd"""
    from repository.library_repository import LibraryNotConfigured
    from services import format_service

    with pytest.raises(LibraryNotConfigured):
        format_service.execute_format(ARTIST, track_ids=[1])


def test_rollback_failure_is_logged_as_critical(
    library, tmp_music_root, monkeypatch, caplog
):
    """回滚本身失败：logger.critical + op_log 说明「DB/磁盘不一致，需人工处理」"""
    import shutil as real_shutil

    from services import format_service

    track_id, src = _make_track(library, tmp_music_root)
    dst = _target_path(tmp_music_root)

    real_move = real_shutil.move
    calls = {"n": 0}

    def flaky_move(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return real_move(*args, **kwargs)
        raise OSError("disk full during rollback")

    def boom(*args, **kwargs):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(format_service, "update_track_path_and_name", boom)
    monkeypatch.setattr(format_service.shutil, "move", flaky_move)

    with caplog.at_level(logging.ERROR, logger="tunetree"):
        result = format_service.execute_format(
            ARTIST, track_ids=[track_id], library_id=library["id"]
        )

    assert result["errors"] == 1
    assert result["moved"] == 0
    assert dst.exists(), "回滚失败时文件留在新位置"
    assert not src.exists()
    assert calls["n"] == 2, "应该尝试过 rollback"

    criticals = [r for r in caplog.records if r.levelno == logging.CRITICAL]
    assert criticals, "回滚失败必须 logger.critical"
    assert any("需人工处理" in r.getMessage() for r in criticals)

    logs = get_db().execute(
        "SELECT op_type, message FROM op_log ORDER BY id"
    ).fetchall()
    assert any(
        row["op_type"] == "error" and "需人工处理" in (row["message"] or "")
        for row in logs
    ), "回滚失败必须写 op_log 说明需人工处理"