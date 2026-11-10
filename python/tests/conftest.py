"""pytest 公共夹具。

运行方式（仓库根目录）：

    /path/to/venv/bin/python -m pytest python/tests -q

设计要点：
- 在导入应用代码**之前**设置 DB_ROOT / ACCESS_KEY / SECRET_KEY 环境变量，
  避免测试污染开发/生产数据库（config.py 在导入时读取环境变量）。
- ``client`` 夹具每个用例前重建一个全新的 SQLite 数据库，保证用例之间互不影响。
"""

import os
import sys
import tempfile
import shutil
import importlib
from pathlib import Path

import pytest

# --- 必须在导入 app / config 之前完成 ---
PYTHON_DIR = Path(__file__).resolve().parents[1]
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

_TMP_DB_ROOT = tempfile.mkdtemp(prefix="tune-tree-test-db-")
_TMP_CWD = tempfile.mkdtemp(prefix="tune-tree-test-cwd-")
os.environ["DB_ROOT"] = _TMP_DB_ROOT
os.environ["ACCESS_KEY"] = "test-access-key"
os.environ["SECRET_KEY"] = "test-secret-key"
os.environ.pop("MUSIC_ROOT", None)

# 切到临时工作目录再导入应用：app.py 会创建相对路径的 ./instance（日志目录），
# 不切换的话会把测试产物写进仓库。
os.chdir(_TMP_CWD)

import config  # noqa: E402
import models.db as db_module  # noqa: E402

TEST_TOKEN = os.environ["ACCESS_KEY"]


def _reset_database():
    """删除并重建数据库文件，返回全新的连接。"""
    db_module.close_db_background()
    for suffix in ("", "-wal", "-shm"):
        p = Path(config.DB_PATH + suffix)
        if p.exists():
            try:
                p.unlink()
            except OSError:
                pass
    db_module.init_db()


@pytest.fixture(scope="session", autouse=True)
def _session_db_root():
    yield
    shutil.rmtree(_TMP_DB_ROOT, ignore_errors=True)


@pytest.fixture()
def db_path():
    return config.DB_PATH


@pytest.fixture()
def flask_app():
    """每个用例一个干净数据库的全新 Flask 应用。"""
    _reset_database()
    import app as app_module

    importlib.reload(app_module) if False else None  # 保留占位：app 单例复用即可
    app_module.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
    return app_module.app


@pytest.fixture()
def client(flask_app):
    """带鉴权头的测试客户端。"""
    with flask_app.test_client() as c:
        c.environ_base["HTTP_X_TOKEN"] = TEST_TOKEN
        yield c


@pytest.fixture()
def app_ctx(flask_app):
    with flask_app.app_context():
        yield


@pytest.fixture()
def tmp_music_root(tmp_path):
    """一个空的临时“音乐库根目录”。"""
    root = tmp_path / "music"
    root.mkdir(parents=True, exist_ok=True)
    return root


@pytest.fixture()
def library(tmp_music_root, app_ctx):
    """把当前音乐库指向临时目录。"""
    from repository.library_repository import (
        get_current_library_id,
        set_current_library_id,
        insert_library,
        update_library,
    )

    lib_id = insert_library("测试库", str(tmp_music_root), is_default=1)
    set_current_library_id(lib_id)
    return {"id": lib_id, "path": str(tmp_music_root)}


@pytest.fixture()
def no_library(app_ctx):
    """模拟“尚未配置任何音乐库”的初始状态（回归 O-1 的触发态）。"""
    from models.db import get_db

    db = get_db()
    db.execute("DELETE FROM music_libraries")
    db.execute("DELETE FROM scan_meta WHERE key='current_library_id'")
    db.commit()
    return None


@pytest.fixture(scope="session")
def mp3_data():
    """一个“合法到可以被 mutagen 写入”的最小 MP3 字节串。

    空 ID3v2 头 + 若干个 MPEG-1 Layer III 帧（128kbps / 44.1kHz = 417 字节/帧）。
    纯空文件、或只有 1 帧的文件都会让 mutagen 在保存标签时报
    "can't sync to MPEG frame"（至少要 3 帧才能被识别为有效流）。
    """
    import tempfile
    from mutagen.id3 import ID3

    fd, path = tempfile.mkstemp(suffix=".mp3")
    os.close(fd)
    try:
        ID3().save(path)
        with open(path, "rb") as fh:
            header = fh.read()
    finally:
        os.remove(path)
    frame = b"\xff\xfb\x90\x00" + b"\x00" * 413
    return header + frame * 20


@pytest.fixture()
def make_track(library, app_ctx):
    """在音乐库里造一个真实文件，并写入 artists/albums/tracks 记录。"""
    import time
    from models.db import get_db
    from repository.artist_repository import ensure_artist
    from repository.album_repository import ensure_album

    db = get_db()

    def _make(
        name: str = "01. Test.mp3",
        album: str = "测试专辑",
        artist: str = "测试艺术家",
        data: bytes = b"ID3\x03\x00\x00\x00",
    ) -> dict:
        root = Path(library["path"])
        target = root / artist / album / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

        artist_id = ensure_artist(artist, library_id=library["id"])
        album_id = ensure_album(album, artist_id, library_id=library["id"])
        now = time.time()
        cur = db.execute(
            "INSERT INTO tracks "
            "(path, filename, ext, size, mtime, ctime, title, artist, album, "
            " album_artist, artist_id, album_id, library_id, track_num, has_cover, "
            " has_lyrics, organized, pending, scanned_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                str(target),
                target.name,
                target.suffix.lstrip("."),
                len(data),
                now,
                now,
                target.stem,
                artist,
                album,
                artist,
                artist_id,
                album_id,
                library["id"],
                1,
                0,
                0,
                0,
                0,
                now,
            ),
        )
        db.commit()
        return {
            "id": cur.lastrowid,
            "path": str(target),
            "artist": artist,
            "album": album,
            "artist_id": artist_id,
            "album_id": album_id,
        }

    return _make