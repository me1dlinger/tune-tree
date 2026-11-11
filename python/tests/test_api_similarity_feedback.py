"""A2 接口层回归：相似艺术家误报「不是同一人 / 是同一人」。

用户原话：相似艺术家检测很多确实不是相同艺术家，但一直识别为相同，
可以标识，让之后的检测不再把那些标识为相似的。

覆盖：反馈接口（POST/DELETE/GET）、检测结果过滤与 confirmed 回显、
阈值读写与生效、非法输入。
"""

import pytest


@pytest.fixture(autouse=True)
def _restore_thresholds():
    """阈值是进程内模块级状态：每个用例后恢复默认，避免互相污染。"""
    yield
    from services import similarity_service as svc

    svc.update_thresholds(dict(svc.DEFAULT_SIMILARITY_THRESHOLDS))


@pytest.fixture()
def similar_artists(app_ctx, library):
    """造 3 个高度相似的艺术家名（简繁/大小写差异，现有算法必然判为相似）。"""
    from repository.artist_repository import ensure_artist

    a = ensure_artist("周杰伦", library_id=library["id"])
    b = ensure_artist("周杰倫", library_id=library["id"])
    c = ensure_artist("周杰倫2", library_id=library["id"])
    return {"a": a, "b": b, "c": c}


def _pairs(client):
    groups = client.get("/api/stats/similar-artists").get_json()
    return {(g["artist_a"]["id"], g["artist_b"]["id"]) for g in groups}


def test_dismissed_pair_disappears_from_detection(client, similar_artists):
    assert _pairs(client), "夹具应当能产生至少一组相似对"
    a, b = similar_artists["a"], similar_artists["b"]
    assert (a, b) in _pairs(client)

    resp = client.post(
        "/api/stats/similar-artists/feedback",
        json={"artist_a_id": a, "artist_b_id": b, "verdict": "dismissed"},
    )
    assert resp.status_code == 200, resp.get_json()
    assert (a, b) not in _pairs(client), "已忽略的配对不应再出现"

    # 记录可查
    listed = client.get(
        "/api/stats/similar-artists/feedback?verdict=dismissed"
    ).get_json()
    assert [(r["artist_a_id"], r["artist_b_id"]) for r in listed] == [(a, b)]
    assert listed[0]["name_a"] and listed[0]["name_b"]


def test_feedback_is_order_insensitive(client, similar_artists):
    """先 b 后 a 与先 a 后 b 必须落在同一行（规范有序对）。"""
    a, b = similar_artists["a"], similar_artists["b"]
    client.post(
        "/api/stats/similar-artists/feedback",
        json={"artist_a_id": b, "artist_b_id": a, "verdict": "dismissed"},
    )
    client.post(
        "/api/stats/similar-artists/feedback",
        json={"artist_a_id": a, "artist_b_id": b, "verdict": "dismissed"},
    )
    listed = client.get("/api/stats/similar-artists/feedback").get_json()
    assert len(listed) == 1, listed


def test_confirmed_pair_is_kept_and_flagged(client, similar_artists):
    a, b = similar_artists["a"], similar_artists["b"]
    client.post(
        "/api/stats/similar-artists/feedback",
        json={"artist_a_id": a, "artist_b_id": b, "verdict": "confirmed"},
    )

    groups = client.get("/api/stats/similar-artists").get_json()
    hit = [
        g for g in groups
        if (g["artist_a"]["id"], g["artist_b"]["id"]) == (a, b)
        or (g["artist_b"]["id"], g["artist_a"]["id"]) == (a, b)
    ]
    assert hit, "已确认同一人的配对必须仍然可见（否则用户会以为标记没生效）"
    assert hit[0]["feedback"] == "confirmed"
    assert hit[0]["similarity"] == 1.0


def test_confirmed_pair_visible_even_if_greedy_pairing_skips_it(client, similar_artists):
    """贪心配对可能把 confirmed 的对吃掉：接口必须补充回显。"""
    a, b, c = similar_artists["a"], similar_artists["b"], similar_artists["c"]
    # 先确认两组（a-b、a-c），贪心配对只会产出其中一组
    for x, y in ((a, b), (a, c)):
        assert (
            client.post(
                "/api/stats/similar-artists/feedback",
                json={"artist_a_id": x, "artist_b_id": y, "verdict": "confirmed"},
            ).status_code
            == 200
        )

    groups = client.get("/api/stats/similar-artists").get_json()
    confirmed = {
        tuple(sorted((g["artist_a"]["id"], g["artist_b"]["id"])))
        for g in groups
        if g.get("feedback") == "confirmed"
    }
    assert (a, b) in confirmed or (b, a) in confirmed
    assert (a, c) in confirmed or (c, a) in confirmed, (
        "被 seen 抢占的 confirmed 配对也必须回显"
    )


def test_delete_feedback_restores_pair(client, similar_artists):
    a, b = similar_artists["a"], similar_artists["b"]
    client.post(
        "/api/stats/similar-artists/feedback",
        json={"artist_a_id": a, "artist_b_id": b, "verdict": "dismissed"},
    )
    assert (a, b) not in _pairs(client)

    resp = client.delete(f"/api/stats/similar-artists/feedback/{a}/{b}")
    assert resp.status_code == 200
    assert resp.get_json()["removed"] is True
    assert (a, b) in _pairs(client), "撤销后该对应重新参与检测"


def test_delete_missing_feedback_is_idempotent(client, similar_artists):
    resp = client.delete(
        f"/api/stats/similar-artists/feedback/{similar_artists['a']}/{similar_artists['b']}"
    )
    assert resp.status_code == 200
    assert resp.get_json()["removed"] is False


def test_invalid_verdict_returns_400(client, similar_artists):
    resp = client.post(
        "/api/stats/similar-artists/feedback",
        json={
            "artist_a_id": similar_artists["a"],
            "artist_b_id": similar_artists["b"],
            "verdict": "maybe",
        },
    )
    assert resp.status_code == 400
    assert "verdict" in resp.get_json()["error"]


def test_verdict_is_case_insensitive(client, similar_artists):
    resp = client.post(
        "/api/stats/similar-artists/feedback",
        json={
            "artist_a_id": similar_artists["a"],
            "artist_b_id": similar_artists["b"],
            "verdict": "DISMISSED",
        },
    )
    assert resp.status_code == 200, resp.get_json()


def test_missing_ids_returns_400(client):
    resp = client.post(
        "/api/stats/similar-artists/feedback", json={"verdict": "dismissed"}
    )
    assert resp.status_code == 400


def test_same_id_pair_returns_400(client, similar_artists):
    a = similar_artists["a"]
    resp = client.post(
        "/api/stats/similar-artists/feedback",
        json={"artist_a_id": a, "artist_b_id": a, "verdict": "dismissed"},
    )
    assert resp.status_code == 400

    resp = client.delete(f"/api/stats/similar-artists/feedback/{a}/{a}")
    assert resp.status_code == 400, "同 id 的撤销不该 500"


# ---------------- 阈值 ----------------


def test_thresholds_defaults(client):
    body = client.get("/api/stats/similar-artists/thresholds").get_json()
    assert body == {"short": 0.95, "len3": 0.85, "len5": 0.8, "long": 0.75}


def test_thresholds_update_and_persist(client, app_ctx):
    """PUT 后必须**真的落库**：清空进程内状态再 GET 仍应读到配置。"""
    from services import similarity_service as svc

    payload = {"short": 0.9, "len3": 0.7, "len5": 0.6, "long": 0.5}
    resp = client.put("/api/stats/similar-artists/thresholds", json=payload)
    assert resp.status_code == 200, resp.get_json()

    # 模拟「另一个 worker / 重启后的进程」：把模块状态清回默认值
    svc.SIMILARITY_THRESHOLDS.clear()
    svc.SIMILARITY_THRESHOLDS.update(dict(svc.DEFAULT_SIMILARITY_THRESHOLDS))

    assert client.get("/api/stats/similar-artists/thresholds").get_json() == payload


def test_thresholds_reject_invalid(client):
    for bad in (
        {"short": "x"},
        {"short": 1.5},
        {"short": 0.1},
        {"unknown": 0.5},
    ):
        resp = client.put("/api/stats/similar-artists/thresholds", json=bad)
        assert resp.status_code == 400, (bad, resp.get_json())


def test_lowered_threshold_makes_new_pairs_appear(client, app_ctx, library):
    """把 3 字档阈值调低 → 原本判为不相似的名字开始出现（用户可自行调参）。"""
    from repository.artist_repository import ensure_artist

    # 5 字名：默认 len5 阈值 0.80，相似度约 0.60 → 不报；阈值降到 0.50 后应报出
    ensure_artist("ABCDE", library_id=library["id"])
    ensure_artist("ABCDF", library_id=library["id"])

    before = _pairs(client)
    assert len(before) == 0, before

    client.put(
        "/api/stats/similar-artists/thresholds",
        json={"short": 0.5, "len3": 0.5, "len5": 0.5, "long": 0.5},
    )
    assert _pairs(client), "阈值降低后应当出现新的相似对"
    # 复原，避免污染其它用例
    client.put(
        "/api/stats/similar-artists/thresholds",
        json={"short": 0.95, "len3": 0.85, "len5": 0.8, "long": 0.75},
    )