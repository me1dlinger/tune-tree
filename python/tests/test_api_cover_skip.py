"""A1 接口层回归：艺术家头像「不再获取」标记。

用户原话：艺术家统计里很多艺术家就是获取不到头像，这些可以直接标识为以后不去获取，
也不会在「无封面艺术家」出现。

覆盖：标记/解除接口、待办列表过滤、批量标记、批量刮削跳过与计数、
失败计数、上传封面成功后自动解封、以及「一键获取封面」用全量而非 TOP10。
"""

import io
from pathlib import Path

import pytest
from PIL import Image


def _jpeg(width: int = 240, height: int = 240) -> bytes:
    """生成一张 >1KB 的 JPEG。

    头像抓取接口有 `len(image_data) < 1000 → 502` 的下限，太小的图会被当成
    "没抓到"，所以测试图必须足够大。
    """
    import os as _os

    img = Image.frombytes("RGB", (width, height), _os.urandom(width * height * 3))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    return buf.getvalue()


@pytest.fixture()
def artists(app_ctx, library):
    """造 3 个艺术家：2 个无封面、1 个有封面。"""
    from repository.artist_repository import ensure_artist, update_artist

    a = ensure_artist("无封面甲", library_id=library["id"])
    b = ensure_artist("无封面乙", library_id=library["id"])
    c = ensure_artist("有封面丙", library_id=library["id"])

    cover = Path(library["path"]) / "有封面丙"
    cover.mkdir(parents=True, exist_ok=True)
    cover_file = cover / "cover.jpg"
    cover_file.write_bytes(_jpeg())
    # 必须写 DB 的 cover_path：统计口径看的是 cover_path 而不是文件是否存在
    update_artist(c, cover_path=str(cover_file))

    # 给三个艺术家都挂一首曲目，让 track_count 有意义
    from models.db import get_db
    import time

    db = get_db()
    now = time.time()
    for idx, aid in enumerate((a, b, c), start=1):
        db.execute(
            "INSERT INTO tracks (path, filename, ext, size, mtime, ctime, title, artist,"
            " album, artist_id, library_id, track_num, organized, pending, scanned_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                str(Path(library["path"]) / f"t{idx}.mp3"),
                f"t{idx}.mp3",
                "mp3",
                1,
                now,
                now,
                f"t{idx}",
                f"a{idx}",
                None,
                aid,
                library["id"],
                idx,
                0,
                0,
                now,
            ),
        )
    db.commit()
    return {"a": a, "b": b, "c": c}


def test_mark_and_unmark_roundtrip(client, artists, app_ctx):
    aid = artists["a"]

    resp = client.post(f"/api/artists/{aid}/cover-skip", json={"skip": True})
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["cover_skip"] is True

    skipped = client.get("/api/artists/cover-skipped").get_json()
    assert [s["id"] for s in skipped] == [aid]
    assert skipped[0]["cover_skip_reason"] == "user"

    # 可逆：解除标记
    resp = client.post(f"/api/artists/{aid}/cover-skip", json={"skip": False})
    assert resp.status_code == 200
    assert client.get("/api/artists/cover-skipped").get_json() == []


def test_mark_reason_can_be_custom(client, artists):
    client.post(
        f"/api/artists/{artists['a']}/cover-skip",
        json={"skip": True, "reason": "auto_no_result"},
    )
    skipped = client.get("/api/artists/cover-skipped").get_json()
    assert skipped[0]["cover_skip_reason"] == "auto_no_result"


def test_cover_skip_missing_artist_returns_404(client, app_ctx, library):
    resp = client.post("/api/artists/999999/cover-skip", json={"skip": True})
    assert resp.status_code == 404


def test_stats_filters_todo_but_keeps_factual_counts(client, artists):
    stats_before = client.get("/api/stats/artists").get_json()
    assert stats_before["without_cover"] == 2          # 事实计数
    assert stats_before["without_cover_active"] == 2   # 待办计数
    assert stats_before["cover_skipped"] == 0
    assert {r["id"] for r in stats_before["no_cover_top"]} == {artists["a"], artists["b"]}

    client.post(f"/api/artists/{artists['a']}/cover-skip", json={"skip": True})

    stats = client.get("/api/stats/artists").get_json()
    # 事实计数不变（否则「有封面 X / 总数」这个环会和真实文件数对不上）
    assert stats["without_cover"] == 2
    assert stats["without_cover_active"] == 1
    assert stats["cover_skipped"] == 1
    # 待办列表过滤掉已标记的
    assert {r["id"] for r in stats["no_cover_top"]} == {artists["b"]}


def test_without_cover_endpoint_excludes_skipped(client, artists):
    before = client.get("/api/artists/without-cover").get_json()
    assert {r["id"] for r in before} == {artists["a"], artists["b"]}

    client.post(f"/api/artists/{artists['a']}/cover-skip", json={"skip": True})

    after = client.get("/api/artists/without-cover").get_json()
    assert {r["id"] for r in after} == {artists["b"]}


def test_batch_mark_and_release(client, artists):
    resp = client.post(
        "/api/artists/cover-skip/batch",
        json={"artist_ids": [artists["a"], artists["b"], 999999], "skip": True},
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["updated"] == 2
    assert body["missing"] == [999999]

    resp = client.post(
        "/api/artists/cover-skip/batch",
        json={"artist_ids": [artists["a"], artists["b"]], "skip": False},
    )
    assert resp.get_json()["updated"] == 2
    assert client.get("/api/artists/cover-skipped").get_json() == []


def test_batch_scrape_skips_marked_artist(client, artists, app_ctx, monkeypatch):
    """标记后批量刮削必须跳过它，且不能真的去调用刮削器。"""
    import api.routes as routes

    client.post(f"/api/artists/{artists['a']}/cover-skip", json={"skip": True})

    calls = []

    def fake_scrape(name):
        calls.append(name)
        return None, name

    monkeypatch.setattr(routes.scraper, "scrape_artist_avatar", fake_scrape)

    resp = client.post(
        "/api/artists/batch-scrape-covers",
        json={"artist_ids": [artists["a"], artists["b"]]},
    )
    body = resp.get_json()
    assert resp.status_code == 200
    assert body["skipped"] == 1
    skipped_entry = [r for r in body["results"] if r.get("skipped")]
    assert skipped_entry and skipped_entry[0]["id"] == artists["a"]
    assert "无封面甲" not in calls, "已标记的艺术家不应被抓取"

    # 被跳过的不算 error，避免 UI 一片红：应恰好 1 条 skipped、0 条 error
    assert len([r for r in body["results"] if r.get("skipped")]) == 1
    assert len([r for r in body["results"] if r["ok"]]) == 0
    assert body["success"] == 0


def test_batch_scrape_failure_records_attempts(client, artists, app_ctx, monkeypatch):
    """抓不到头像要记录失败次数，供 UI 展示与后续自动标记。"""
    import api.routes as routes
    from repository.artist_repository import get_artist_by_id

    monkeypatch.setattr(
        routes.scraper, "scrape_artist_avatar", lambda name: (None, "netease")
    )

    client.post(
        "/api/artists/batch-scrape-covers",
        json={"artist_ids": [artists["b"]]},
    )

    row = get_artist_by_id(artists["b"])
    assert row["cover_fetch_attempts"] == 1
    assert row["cover_fetch_failed_at"] is not None
    assert row["cover_last_source"] == "netease"


def test_single_scrape_on_marked_artist_returns_409_unless_force(
    client, artists, app_ctx, monkeypatch
):
    import api.routes as routes

    client.post(f"/api/artists/{artists['a']}/cover-skip", json={"skip": True})
    called = []
    monkeypatch.setattr(
        routes.scraper,
        "scrape_artist_avatar",
        lambda name: (called.append(name), (None, name))[1],
    )

    resp = client.post(f"/api/artists/{artists['a']}/scrape-cover", json={})
    assert resp.status_code == 409
    assert resp.get_json()["error"] == "cover_skip"
    assert called == [], "被标记的艺术家默认不该发起抓取"

    # force=true 是逃生通道（前端会弹确认框）
    resp = client.post(f"/api/artists/{artists['a']}/scrape-cover", json={"force": True})
    assert resp.status_code == 502  # 抓取器返回空图
    assert called == ["无封面甲"]


def test_manual_upload_unmarks_artist(client, artists, app_ctx):
    """手动上传封面成功 → 自动解除「不再获取」标记（否则会永久留在跳过集合里）。"""
    client.post(f"/api/artists/{artists['a']}/cover-skip", json={"skip": True})

    resp = client.post(
        f"/api/artists/{artists['a']}/cover",
        data={"cover": (io.BytesIO(_jpeg()), "cover.jpg")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200, resp.get_json()
    assert client.get("/api/artists/cover-skipped").get_json() == []


def test_scrape_success_unmarks_artist(client, artists, app_ctx, monkeypatch):
    """强制重刮成功 → 同样自动解封（reason=user 也不会把它永久粘住）。"""
    import api.routes as routes

    client.post(f"/api/artists/{artists['a']}/cover-skip", json={"skip": True})
    monkeypatch.setattr(
        routes.scraper, "scrape_artist_avatar", lambda name: (_jpeg(), "netease")
    )

    resp = client.post(
        f"/api/artists/{artists['a']}/scrape-cover", json={"force": True}
    )
    assert resp.status_code == 200, resp.get_json()
    assert client.get("/api/artists/cover-skipped").get_json() == []


def test_artists_list_exposes_cover_skip(client, artists):
    rows = client.get("/api/artists").get_json()
    by_id = {r["id"]: r for r in rows}
    assert "cover_skip" in by_id[artists["a"]]

    client.post(f"/api/artists/{artists['a']}/cover-skip", json={"skip": True})
    rows = client.get("/api/artists").get_json()
    by_id = {r["id"]: r for r in rows}
    assert by_id[artists["a"]]["cover_skip"] == 1
    assert by_id[artists["b"]]["cover_skip"] == 0

def test_scan_clears_skip_when_cover_appears(client, artists, library, app_ctx):
    """磁盘上已有 cover.jpg 时，扫描应顺手解除「不再获取」标记。

    否则会出现「已跳过」面板里挂着一位其实已经有封面的艺术家，让用户困惑。
    """
    from pathlib import Path

    from repository.artist_repository import get_artist_by_id
    from services.scan_service import _ensure_covers

    client.post(f"/api/artists/{artists['a']}/cover-skip", json={"skip": True})
    assert get_artist_by_id(artists["a"])["cover_skip"] == 1

    # 人为把封面放到该艺术家的目录里（模拟用户自己塞图/别的工具整理）
    artist_dir = Path(library["path"]) / "无封面甲"
    artist_dir.mkdir(parents=True, exist_ok=True)
    (artist_dir / "cover.jpg").write_bytes(_jpeg())

    _ensure_covers(library["path"], library_id=library["id"])

    row = get_artist_by_id(artists["a"])
    assert row["cover_path"], "扫描应当把磁盘上的封面写进 DB"
    assert row["cover_skip"] == 0, "已有封面后不应再保留「不再获取」标记"
    assert row["cover_skip_reason"] is None
