"""BUG-8 回归：封面写入必须同时校验「体积」与「像素」。

修复前的两个绕过点：
- `PUT /api/tracks/<id>/cover` 只校验体积（5MB），没有像素上限；
- `POST /api/tracks/<id>/apply-scrape` 用 `_cover_data` 写内嵌封面时**两个都没校验**。
两者都能被「小体积、巨尺寸」的图片触发 PIL 解码时的内存/CPU 爆炸，
而这份图片随后还会被 `extract_cover_to_file` 在扫描/整理流程里再次解码。
"""

import base64
import io
import struct
import zlib

import pytest
from PIL import Image

from api.routes import _validate_cover_payload


def _png_declaring_size(width: int, height: int) -> bytes:
    """构造一个只在 IHDR 里声明巨大尺寸的合法 PNG（不真正编码巨图）。"""
    def chunk(typ: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + typ
            + data
            + struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    row = b"\x00" + b"\x00\x00\x00" * min(width, 4)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(row))
        + chunk(b"IEND", b"")
    )


def _real_png(width=8, height=8) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (10, 20, 30)).save(buf, "PNG")
    return buf.getvalue()


def _real_jpeg(width=8, height=8) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (10, 20, 30)).save(buf, "JPEG")
    return buf.getvalue()


# ---------------- 单元：校验函数 ----------------


def test_valid_png_passes():
    assert _validate_cover_payload(_real_png()) is None


def test_valid_jpeg_passes():
    assert _validate_cover_payload(_real_jpeg()) is None


def test_oversized_pixels_rejected():
    """1x20000 的 PNG 只有几十字节，但高度超过 8192 → 必须拒绝。"""
    payload = _png_declaring_size(1, 20000)
    assert len(payload) < 10_000, "本用例依赖「小体积」这个前提"
    assert _validate_cover_payload(payload) == "cover image too large"


def test_oversized_bytes_rejected():
    assert (
        _validate_cover_payload(b"x" * (5 * 1024 * 1024 + 1))
        == "cover file too large (max 5MB)"
    )


def test_garbage_rejected():
    assert _validate_cover_payload(b"definitely not an image") == "invalid image data"


def test_empty_rejected():
    assert _validate_cover_payload(b"") == "cover file is empty"


# ---------------- 接口：单曲封面 PUT ----------------


def test_track_cover_put_rejects_giant_dimensions(client, make_track):
    track = make_track("01. A.mp3", data=b"ID3\x03\x00\x00\x00")

    resp = client.put(
        f"/api/tracks/{track['id']}/cover",
        data={"cover": (io.BytesIO(_png_declaring_size(1, 20000)), "bomb.png")},
        content_type="multipart/form-data",
    )

    assert resp.status_code == 400
    assert "too large" in resp.get_json()["error"]
    # 文件不得被写入
    assert open(track["path"], "rb").read() == b"ID3\x03\x00\x00\x00"


def test_track_cover_put_rejects_oversize_bytes(client, make_track):
    track = make_track("01. A.mp3", data=b"ID3\x03\x00\x00\x00")

    resp = client.put(
        f"/api/tracks/{track['id']}/cover",
        data={"cover": (io.BytesIO(b"x" * (5 * 1024 * 1024 + 1)), "big.jpg")},
        content_type="multipart/form-data",
    )

    assert resp.status_code == 400
    assert "too large" in resp.get_json()["error"]


def test_track_cover_put_accepts_valid_image(client, make_track, mp3_data, app_ctx):
    """合法封面应当真的写进标签（用最小可写 MP3 验证端到端写入）。"""
    track = make_track("01. A.mp3", data=mp3_data)

    resp = client.put(
        f"/api/tracks/{track['id']}/cover",
        data={"cover": (io.BytesIO(_real_jpeg()), "cover.jpg")},
        content_type="multipart/form-data",
    )

    assert resp.status_code == 200
    from utils.metadata import get_cover_b64

    assert get_cover_b64(track["path"])


# ---------------- 接口：apply-scrape ----------------


def test_apply_scrape_rejects_cover_bomb(client, make_track):
    track = make_track("01. A.mp3", data=b"ID3\x03\x00\x00\x00")
    payload = base64.b64encode(_png_declaring_size(1, 20000)).decode()

    resp = client.post(
        f"/api/tracks/{track['id']}/apply-scrape", json={"_cover_data": payload}
    )

    assert resp.status_code == 400
    assert "too large" in resp.get_json()["error"]
    assert open(track["path"], "rb").read() == b"ID3\x03\x00\x00\x00"


def test_apply_scrape_rejects_invalid_base64(client, make_track):
    track = make_track("01. A.mp3", data=b"ID3\x03\x00\x00\x00")

    resp = client.post(
        f"/api/tracks/{track['id']}/apply-scrape", json={"_cover_data": "!!!not-base64!!!"}
    )

    assert resp.status_code == 400


def test_apply_scrape_without_cover_still_works(client, make_track, mp3_data, app_ctx):
    """不带封面的刮削结果不受影响（避免修复引入回归）。"""
    track = make_track("01. A.mp3", data=mp3_data)

    resp = client.post(
        f"/api/tracks/{track['id']}/apply-scrape",
        json={"title": "新标题", "artist": "新艺术家"},
    )

    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True