"""夹具自检：确认 pytest 测试环境与 Flask 客户端可用。"""


def test_health_check_requires_auth(client):
    resp = client.get("/api/stats")
    assert resp.status_code == 200


def test_auth_rejects_bad_token(client):
    resp = client.get("/api/stats", headers={"X-Token": "wrong"})
    assert resp.status_code == 401


def test_fresh_db_is_empty(app_ctx):
    from repository.track_repository import count_total_tracks

    assert count_total_tracks() == 0