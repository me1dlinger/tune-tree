"""音频 Range 回归：合法的分段请求、后缀请求、越界请求与多段请求。

修复前的问题（在端到端验证时发现）：
- `Range: bytes=<超出文件大小>-` 会算出**负数** Content-Length，返回一个畸形的
  206 响应，客户端直接报协议错误（curl 报 "weird server reply"）；
- `bytes=-500`（最后 500 字节，RFC 7233 后缀形式）不被识别，直接回整文件；
- 无 Range 的响应缺少 `Accept-Ranges`，播放器无法判断服务端支持拖动。
"""

import pytest

from api.routes import _parse_byte_range


# ---------------- 单元：Range 头解析 ----------------


@pytest.mark.parametrize(
    "header,size,expected",
    [
        ("bytes=0-99", 1000, (0, 99)),
        ("bytes=0-", 1000, (0, 999)),
        ("bytes=100-200", 1000, (100, 200)),
        ("bytes=900-2000", 1000, (900, 999)),      # end 超出 → 裁剪到文件末尾
        ("bytes=-100", 1000, (900, 999)),          # 后缀形式
        ("bytes=-5000", 1000, (0, 999)),           # 后缀超过文件长度 → 全文件
    ],
)
def test_valid_ranges(header, size, expected):
    assert _parse_byte_range(header, size) == expected


@pytest.mark.parametrize(
    "header,size",
    [
        ("bytes=1000-", 1000),      # start 等于文件长度
        ("bytes=5000-6000", 1000),  # start 远超文件长度
        ("bytes=200-100", 1000),    # end < start
        ("bytes=-0", 1000),         # 后缀长度为 0
        ("bytes=0-", 0),            # 空文件
    ],
)
def test_invalid_ranges(header, size):
    assert _parse_byte_range(header, size) == "invalid"


@pytest.mark.parametrize("header", ["bytes=0-1,5-6", "octets=0-1", "bytes=abc-def", "bytes="])
def test_unsupported_ranges_fall_back_to_full(header):
    assert _parse_byte_range(header, 1000) is None


# ---------------- 接口 ----------------


@pytest.fixture()
def audio_track(make_track, mp3_data):
    return make_track("01. A.mp3", data=mp3_data)


def test_full_audio_response_advertises_ranges(client, audio_track):
    resp = client.get(f"/api/tracks/{audio_track['id']}/audio")
    assert resp.status_code == 200
    assert resp.headers.get("Accept-Ranges") == "bytes"


def test_partial_audio_response(client, audio_track):
    resp = client.get(
        f"/api/tracks/{audio_track['id']}/audio", headers={"Range": "bytes=0-99"}
    )
    assert resp.status_code == 206
    assert resp.headers["Content-Range"] == f"bytes 0-99/{len(_audio_bytes(audio_track))}"
    assert resp.headers["Content-Length"] == "100"
    assert len(resp.get_data()) == 100


def test_open_ended_range(client, audio_track):
    size = len(_audio_bytes(audio_track))
    resp = client.get(
        f"/api/tracks/{audio_track['id']}/audio", headers={"Range": "bytes=10-"}
    )
    assert resp.status_code == 206
    assert resp.headers["Content-Range"] == f"bytes 10-{size - 1}/{size}"
    assert len(resp.get_data()) == size - 10


def test_suffix_range_returns_tail(client, audio_track):
    data = _audio_bytes(audio_track)
    resp = client.get(
        f"/api/tracks/{audio_track['id']}/audio", headers={"Range": "bytes=-10"}
    )
    assert resp.status_code == 206
    assert resp.get_data() == data[-10:]


def test_range_beyond_eof_returns_416_not_malformed_206(client, audio_track):
    size = len(_audio_bytes(audio_track))
    resp = client.get(
        f"/api/tracks/{audio_track['id']}/audio", headers={"Range": f"bytes={size + 100}-"}
    )
    assert resp.status_code == 416, "旧实现返回 Content-Length 为负数的畸形 206"
    assert resp.headers["Content-Range"] == f"bytes */{size}"


def test_multirange_falls_back_to_full_body(client, audio_track):
    data = _audio_bytes(audio_track)
    resp = client.get(
        f"/api/tracks/{audio_track['id']}/audio", headers={"Range": "bytes=0-1,5-6"}
    )
    assert resp.status_code == 200
    assert resp.get_data() == data


def _audio_bytes(track):
    with open(track["path"], "rb") as fh:
        return fh.read()