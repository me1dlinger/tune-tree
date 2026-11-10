"""BUG-1 / BUG-7 回归：未配置音乐库不得退化为进程工作目录；非 ASCII token 不得 500。

背景（修复前的真实缺陷）：
- `Path(get_current_library_path() or "")` 在未配置库时等于 `Path("")`，即进程工作目录；
  `_is_within` 因此判定“合法”，`/api/files` 会列出应用源码目录，
  `/api/files/download?path=.env` 能下载到 `.env`（内含 ACCESS_KEY）。
- `/api/tracks/by-path` 的穿越校验被 `if _lib_path:` 整段跳过，等于放行任意路径。
- `hmac.compare_digest(str, str)` 对非 ASCII 字符串抛 TypeError → 500。
"""

import os
from pathlib import Path


def test_files_listing_requires_configured_library(client, no_library):
    """未配置库时 /api/files 必须 409，而不是列出工作目录。"""
    resp = client.get("/api/files?path=")
    assert resp.status_code == 409, resp.get_data(as_text=True)
    assert resp.get_json()["error"] == "library_not_configured"


def test_files_download_cannot_read_dotenv(client, no_library, tmp_path):
    """未配置库时不得下载应用目录里的 .env（修复前可直接读取凭据）。"""
    resp = client.get("/api/files/download?path=.env")
    assert resp.status_code == 409, resp.get_data(as_text=True)
    body = resp.get_data(as_text=True)
    assert "ACCESS_KEY" not in body


def test_files_download_cannot_read_arbitrary_file(client, no_library):
    """未配置库时不得下载任意本地文件。"""
    resp = client.get("/api/files/download?path=../../etc/passwd")
    assert resp.status_code in (403, 409)


def test_tracks_by_path_requires_library(client, no_library):
    """未配置库时 by-path 的穿越校验不能被跳过。"""
    resp = client.get("/api/tracks/by-path?path=../../etc/passwd")
    assert resp.status_code == 409, resp.get_data(as_text=True)
    assert resp.get_json()["error"] == "library_not_configured"


def test_audio_count_requires_library(client, no_library):
    resp = client.get("/api/files/audio-count?paths=.")
    assert resp.status_code == 409


def test_upload_check_requires_library(client, no_library):
    resp = client.post(
        "/api/files/upload-check",
        data={"files": (__import__("io").BytesIO(b"x"), "a.mp3")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 409


def test_library_with_empty_path_is_treated_as_unconfigured(client, app_ctx):
    """needs_config=1 的库 path=""，同样必须 409（这是最容易漏的触发态）。"""
    from repository.library_repository import insert_library, set_current_library_id

    lib_id = insert_library("待配置音乐库", "", is_default=1, needs_config=1)
    set_current_library_id(lib_id)

    resp = client.get("/api/files?path=")
    assert resp.status_code == 409
    assert resp.get_json()["error"] == "library_not_configured"


def test_path_traversal_still_blocked_when_configured(client, library):
    """配置了库时，越界路径仍然必须 403。"""
    resp = client.get("/api/files?path=../../")
    assert resp.status_code == 403


def test_non_ascii_token_returns_401_not_500(client):
    """非 ASCII token 不能触发 500（hmac.compare_digest 对 str 会抛 TypeError）。"""
    resp = client.get("/api/stats", headers={"X-Token": "音乐库密钥✓"})
    assert resp.status_code == 401


def test_non_ascii_token_on_auth_verify(client):
    resp = client.post("/api/auth/verify", json={"token": "音乐库密钥✓"})
    assert resp.status_code == 401
    assert resp.get_json()["ok"] is False


def test_correct_token_still_works(client):
    from tests.conftest import TEST_TOKEN

    assert client.post("/api/auth/verify", json={"token": TEST_TOKEN}).status_code == 200
    assert client.get("/api/stats").status_code == 200


def test_healthcheck_does_not_expose_workdir(client, no_library, monkeypatch):
    """/api/files 绝不能把工作目录内容当成音乐库返回（修复前的核心症状）。"""
    marker = "tune-tree-secret-marker.txt"
    Path(marker).write_text("secret", encoding="utf-8")
    try:
        resp = client.get("/api/files?path=")
        assert resp.status_code == 409
        assert marker not in resp.get_data(as_text=True)
    finally:
        try:
            os.remove(marker)
        except OSError:
            pass