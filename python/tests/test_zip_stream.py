"""BUG-2 回归：ZIP 下载必须流式生成，不能把整包放进内存。

修复前：四条下载路径都用 ``io.BytesIO()`` 累积整个 ZIP 再 ``send_file``，
下载一个大目录（例如 8GB）会把进程内存推到同量级并 OOM。
"""

import inspect
import io
import os
import zipfile
import tracemalloc

import pytest

import api.routes as routes
from api.routes import _zip_stream


def _assert_streaming_impl(view_func):
    """实现级回归守卫：下载视图必须走流式打包，不能回到 io.BytesIO 全内存。"""
    src = inspect.getsource(view_func)
    assert "_zip_response" in src, f"{view_func.__name__} 必须使用 _zip_response 流式打包"
    assert "io.BytesIO" not in src, f"{view_func.__name__} 不得在内存中拼整个 ZIP"


def _big_file(path, size, compressible=False):
    if compressible:
        path.write_bytes(b"\x00" * size)
    else:
        path.write_bytes(os.urandom(size))
    return path


# ---------------- 单元：流式打包正确性 ----------------


def test_zip_stream_produces_valid_archive(tmp_path):
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_text("hello", encoding="utf-8")
    b.write_text("world", encoding="utf-8")

    data = b"".join(_zip_stream([(a, "a.txt"), (b, "b.txt")]))

    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert sorted(zf.namelist()) == ["a.txt", "b.txt"]
        assert zf.read("a.txt") == b"hello"
        assert zf.read("b.txt") == b"world"


def test_zip_stream_keeps_chinese_filenames(tmp_path):
    f = tmp_path / "以父之名.flac"
    f.write_bytes(b"data")

    data = b"".join(_zip_stream([(f, "以父之名.flac")]))

    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert zf.namelist() == ["以父之名.flac"]


def test_zip_stream_skips_missing_files(tmp_path):
    missing = tmp_path / "gone.mp3"
    ok = tmp_path / "ok.mp3"
    ok.write_bytes(b"x")

    data = b"".join(_zip_stream([(missing, "gone.mp3"), (ok, "ok.mp3")]))

    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert zf.namelist() == ["ok.mp3"]


def test_zip_stream_is_incremental_and_bounded(tmp_path):
    """6MB 不可压缩内容必须分多块产出，且单块远小于整包。"""
    big = _big_file(tmp_path / "big.bin", 6 * 1024 * 1024)

    chunks = list(_zip_stream([(big, "big.bin")]))

    assert len(chunks) > 3, "应当分块产出，而不是一次性构造整包"
    assert max(len(c) for c in chunks) < 2 * 1024 * 1024
    assert sum(len(c) for c in chunks) >= 6 * 1024 * 1024


def test_zip_stream_peak_memory_far_below_payload(tmp_path):
    """20MB 内容打包过程中，峰值内存必须远小于整包大小（旧实现≈整包）。"""
    big = _big_file(tmp_path / "huge.bin", 20 * 1024 * 1024)

    tracemalloc.start()
    try:
        total = 0
        for chunk in _zip_stream([(big, "huge.bin")]):
            total += len(chunk)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert total >= 20 * 1024 * 1024
    assert peak < 12 * 1024 * 1024, f"峰值内存过高：{peak / 1024 / 1024:.1f}MB"


def test_zip_stream_empty_entries_is_valid_empty_zip():
    data = b"".join(_zip_stream([]))
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert zf.namelist() == []


# ---------------- 接口：四条下载路径 ----------------


def test_album_download_is_streamed_and_valid(client, make_track):
    first = make_track("01. A.mp3", data=b"A" * 100)
    make_track("02. B.mp3", data=b"B" * 100)

    resp = client.get(f"/api/albums/{first['album_id']}/download")
    assert resp.status_code == 200
    assert resp.mimetype == "application/zip"
    assert "attachment" in resp.headers["Content-Disposition"]
    _assert_streaming_impl(routes.api_album_download)

    with zipfile.ZipFile(io.BytesIO(resp.get_data())) as zf:
        assert sorted(zf.namelist()) == ["01. A.mp3", "02. B.mp3"]
        assert zf.read("01. A.mp3") == b"A" * 100


def test_artist_download_uses_album_folder(client, make_track):
    t = make_track("01. A.mp3")

    resp = client.get(f"/api/artists/{t['artist_id']}/download")
    assert resp.status_code == 200
    _assert_streaming_impl(routes.api_artist_download)
    with zipfile.ZipFile(io.BytesIO(resp.get_data())) as zf:
        assert zf.namelist() == [f"{t['album']}/01. A.mp3"]


def test_batch_download_streams_multiple_tracks(client, make_track):
    a = make_track("01. A.mp3", data=b"A")
    b = make_track("02. B.mp3", data=b"B")

    resp = client.post(
        "/api/tracks/download-batch", json={"track_ids": [a["id"], b["id"]]}
    )
    assert resp.status_code == 200
    _assert_streaming_impl(routes.api_tracks_batch_download)
    with zipfile.ZipFile(io.BytesIO(resp.get_data())) as zf:
        assert len(zf.namelist()) == 2


def test_files_directory_download_is_streamed(client, make_track, library):
    t = make_track("01. A.mp3", data=b"A" * 32)

    resp = client.get("/api/files/download?path=.")
    assert resp.status_code == 200
    _assert_streaming_impl(routes.api_files_download)
    with zipfile.ZipFile(io.BytesIO(resp.get_data())) as zf:
        assert f"{t['artist']}/{t['album']}/01. A.mp3" in zf.namelist()


def test_files_download_single_file_not_zipped(client, make_track):
    t = make_track("01. A.mp3", data=b"UNIQUE")

    resp = client.get("/api/files/download?path=测试艺术家/测试专辑/01. A.mp3")
    assert resp.status_code == 200
    assert resp.get_data() == b"UNIQUE"


def test_album_download_empty_album_returns_404(client, make_track, app_ctx):
    from repository.album_repository import ensure_album
    from repository.artist_repository import ensure_artist

    artist_id = ensure_artist("空专辑艺术家")
    album_id = ensure_album("空专辑", artist_id)

    resp = client.get(f"/api/albums/{album_id}/download")
    assert resp.status_code == 404


def test_directory_download_skips_upload_temp(client, make_track, library):
    make_track("01. A.mp3")
    temp_dir = os.path.join(library["path"], ".upload_temp")
    os.makedirs(temp_dir, exist_ok=True)
    with open(os.path.join(temp_dir, "pending.tmp"), "wb") as fh:
        fh.write(b"junk")

    resp = client.get("/api/files/download?path=.")
    with zipfile.ZipFile(io.BytesIO(resp.get_data())) as zf:
        assert not any(".upload_temp" in n for n in zf.namelist())